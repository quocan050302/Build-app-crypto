import time
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple
from sqlalchemy.orm import Session
from database import SessionLocal
import models, crud
from domain_calculator import CalculationResult, calculate_risk_reward
from services.event_bus import event_bus
from services.clock import live_clock, IClock

logger = logging.getLogger(__name__)

class TradeLifecycleService:
    """
    Unified Trade Lifecycle Service:
    - Guarantees single-transaction atomic units of work for trade transitions.
    - Prevents split-commit crashes (order state + audit + session quota + lesson + domain event + outbox commit together).
    - Unifies exit processing across ExitMonitor, candles/sync, and manual order close.
    - Employs DB state guards against race conditions and preserves max 1 position invariant.
    - Supports clock injection for reproducible historical replay and deterministic scenarios.
    """

    @staticmethod
    def execute_fill(
        db: Session,
        order: models.PaperOrder,
        fill_price: float,
        calc_result: CalculationResult,
        source: str = "AUTO",
        now_ms: Optional[int] = None,
        clock: Optional[IClock] = None,
        date_str: Optional[str] = None
    ) -> models.PaperOrder:
        """
        Atomic Unit of Work: Fill Armed or Market Order.
        Guards:
        - Order must be in 'armed' or 'candidate' state.
        - Enforces strictly max 1 open position across sessions.
        - Commits order state 'paper_open', DayAudit increment, SessionQuota update, watch setup state,
          DomainEvent 'trade.opened', and NotificationOutbox 'FILLED' in a single transaction.
        """
        current_time = now_ms if now_ms is not None else (clock.now_ms() if clock else int(time.time() * 1000))
        current_dt = datetime.fromtimestamp(current_time / 1000.0, tz=timezone.utc)

        # 1. State transition guards
        if order.state == "paper_open":
            return order  # Already opened, idempotent return

        if order.state not in ("armed", "candidate"):
            logger.warning(f"Cannot fill order {order.id} in state {order.state}")
            return order

        # 2. Invariant: Max 1 active position enforced atomically
        existing_open = db.query(models.PaperOrder).filter(
            models.PaperOrder.state == "paper_open",
            models.PaperOrder.id != order.id
        ).first()

        if existing_open:
            order.state = "rejected"
            order.invalidation_reason = "Đã có vị thế đang mở (giới hạn tối đa 1 vị thế)"
            order.closed_at = current_time
            db.commit()
            return order

        # 3. Transition order to paper_open
        order.state = "paper_open"
        order.actual_entry = fill_price
        order.opened_at = current_time
        order.quantity = calc_result.quantity
        order.initial_risk_usdt = calc_result.net_risk_usdt
        order.gross_rr = calc_result.gross_rr
        order.estimated_net_rr = calc_result.net_rr
        order.estimated_liquidation = calc_result.estimated_liquidation
        order.initial_margin = calc_result.initial_margin_usdt
        order.leverage = calc_result.leverage
        order.margin_mode = calc_result.margin_mode

        # Cost snapshot
        order.cost_snapshot = json.dumps({
            "maker_fee_rate": 0.0004,
            "taker_fee_rate": 0.0004,
            "slippage_usd": 0.10,
            "fees_total_usdt": calc_result.fees_total_usdt,
            "slippage_total_usdt": calc_result.slippage_total_usdt,
            "source": "BITGET_PAPER_MODEL_V7"
        })

        # 4. Record trade fill audit (flush without separate commit)
        crud.record_trade_fill_audit(db, commit=False, date_str=date_str, clock=clock)

        # 4b. Record Trading Policy & NY Session Quota fill atomically
        from services.trading_policy_service import TradingPolicyService
        symbol = order.instrument or "XAUUSDT"
        is_fallback = (getattr(order, "strategy_family", "STANDARD_SMC") == "NY_QUOTA_PAPER")
        quota_update = TradingPolicyService.record_fill(
            db=db,
            symbol=symbol,
            fill_time=current_dt,
            order_id=order.id,
            is_ny_quota_candidate=True
        )

        order.session_instance_id = quota_update.get("session_instance_id")

        # 4c. If standard or manual trade opened during NY, cancel any other pending fallback orders for this session
        if quota_update.get("session_instance_id") and not is_fallback:
            pending_fallbacks = db.query(models.PaperOrder).filter(
                models.PaperOrder.state == "armed",
                models.PaperOrder.strategy_family == "NY_QUOTA_PAPER",
                models.PaperOrder.session_instance_id == quota_update["session_instance_id"]
            ).all()
            for fb in pending_fallbacks:
                fb.state = "cancelled"
                fb.invalidation_reason = "Standard trade filled; NY quota fulfilled"
                fb.closed_at = current_time

        # 5. Update associated watch setup
        if order.setup_id:
            watch_setup = db.query(models.WatchSetup).filter(models.WatchSetup.id == order.setup_id).first()
            if watch_setup:
                watch_setup.state = "PAPER_OPEN"
                watch_setup.confirmed_entry = fill_price
                watch_setup.updated_at = current_time

        # 6. Create Domain Event & Notification Outbox in the same transaction
        event_payload = {
            "trade_id": order.id,
            "order_id": order.id,
            "source": source,
            "direction": order.direction,
            "strategy_family": getattr(order, "strategy_family", "STANDARD_SMC") or "STANDARD_SMC",
            "actual_entry": fill_price,
            "planned_entry": order.planned_entry,
            "quantity": order.quantity,
            "stop_loss": order.stop_loss,
            "take_profit": order.take_profit,
            "initial_risk_usdt": order.initial_risk_usdt,
            "estimated_net_rr": order.estimated_net_rr,
            "leverage": order.leverage,
            "margin_mode": order.margin_mode,
            "initial_margin": order.initial_margin,
            "session_instance_id": order.session_instance_id,
            "opened_at": current_time
        }

        event_bus.publish_event(
            event_type="trade.opened",
            aggregate_id=order.id,
            payload=event_payload,
            db=db,
            occurred_at=current_time
        )

        # 7. Commit atomically at boundary
        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def execute_reject(
        db: Session,
        order: models.PaperOrder,
        reason: str,
        now_ms: Optional[int] = None,
        clock: Optional[IClock] = None
    ) -> models.PaperOrder:
        """
        Atomic Unit of Work: Order Rejected by Execution Guards.
        Marks state 'rejected' without incrementing daily counters.
        Emits 'order.rejected' DomainEvent and 'REJECTED' NotificationOutbox.
        """
        current_time = now_ms if now_ms is not None else (clock.now_ms() if clock else int(time.time() * 1000))
        order.state = "rejected"
        order.invalidation_reason = reason
        order.closed_at = current_time

        event_payload = {
            "order_id": order.id,
            "direction": order.direction,
            "planned_entry": order.planned_entry,
            "reason": reason,
            "rejected_at": current_time
        }

        event_bus.publish_event(
            event_type="order.rejected",
            aggregate_id=order.id,
            payload=event_payload,
            db=db,
            occurred_at=current_time
        )

        if order.setup_id:
            watch_setup = db.query(models.WatchSetup).filter(models.WatchSetup.id == order.setup_id).first()
            if watch_setup:
                watch_setup.state = "REJECTED"
                watch_setup.invalidation_reason = reason
                watch_setup.updated_at = current_time

        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def execute_close(
        db: Session,
        order_id: str,
        exit_price: float,
        exit_cause: str,
        occurred_at: Optional[int] = None,
        clock: Optional[IClock] = None,
        date_str: Optional[str] = None,
        session_tag: str = "LIVE_PAPER",
        recovery_metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[models.PaperOrder]:
        """
        Atomic Unit of Work: Close Open Position.
        - Guarded conditional state check (must be 'paper_open').
        - Computes Net PnL and Realized R after round-trip fees using cost snapshot.
        - Updates DayAudit (consecutive losses, cooldown, 1.5% loss cap).
        - Generates structured, evidence-based Lesson (never fakes Sweep/FVG).
        - Creates DomainEvent and NotificationOutbox (TP_HIT, SL_HIT, MANUAL_CLOSED, LIQUIDATED).
        - Commits in ONE single atomic transaction.
        """
        current_time = occurred_at or (clock.now_ms() if clock else int(time.time() * 1000))

        order = db.query(models.PaperOrder).filter(
            models.PaperOrder.id == order_id,
            models.PaperOrder.state == "paper_open"
        ).first()

        if not order:
            return None

        entry_p = order.actual_entry or order.planned_entry
        qty = order.quantity
        direction_mult = 1.0 if order.direction == "LONG" else -1.0
        gross_pnl = (exit_price - entry_p) * qty * direction_mult

        # Fees: read from cost_snapshot if present, else fallback with legacy note
        fee_rate = 0.0004
        if getattr(order, 'cost_snapshot', None):
            try:
                snap = json.loads(order.cost_snapshot)
                fee_rate = snap.get("taker_fee_rate", 0.0004)
            except Exception:
                fee_rate = 0.0004

        fee_cost = (entry_p + exit_price) * qty * fee_rate
        net_pnl = round(gross_pnl - fee_cost, 2)
        realized_r = round(net_pnl / order.initial_risk_usdt, 2) if (order.initial_risk_usdt and order.initial_risk_usdt > 0) else 0.0

        # Update order fields
        order.state = "closed"
        order.actual_exit = round(exit_price, 2)
        order.realized_pnl_net = net_pnl
        order.realized_r = realized_r
        order.exit_cause = exit_cause
        order.closed_at = current_time

        # Update recovery fields if provided
        if recovery_metadata:
            order.recovery_status = recovery_metadata.get("recovery_status", "RECOVERED")
            order.recovery_confidence = recovery_metadata.get("confidence", "CONFIRMED")
            order.discovered_at = recovery_metadata.get("discovered_at", current_time)
            order.occurred_at = occurred_at
            order.resolved_through = recovery_metadata.get("resolved_through")

        # Update DayAudit in same transaction
        crud.record_trade_close_audit(db, net_pnl, commit=False, date_str=date_str, clock=clock, now_ms=current_time)

        # Update associated WatchSetup if present
        if order.setup_id:
            watch_setup = db.query(models.WatchSetup).filter(models.WatchSetup.id == order.setup_id).first()
            if watch_setup:
                watch_setup.state = "CLOSED"
                watch_setup.updated_at = current_time

        # Create structured Lesson
        TradeLifecycleService._create_lesson(db, order, current_time, session_tag=session_tag)

        # Create Domain Event & Notification Outbox in same transaction
        event_type = "trade.liquidated" if exit_cause == "LIQUIDATED" else "trade.closed"
        event_payload = {
            "trade_id": order.id,
            "order_id": order.id,
            "direction": order.direction,
            "strategy_family": getattr(order, "strategy_family", "STANDARD_SMC") or "STANDARD_SMC",
            "actual_entry": entry_p,
            "actual_exit": round(exit_price, 2),
            "realized_pnl": net_pnl,
            "realized_r": realized_r,
            "exit_cause": exit_cause,
            "opened_at": order.opened_at,
            "closed_at": current_time,
            "recovery_status": getattr(order, "recovery_status", None)
        }

        event_bus.publish_event(
            event_type=event_type,
            aggregate_id=order.id,
            payload=event_payload,
            db=db,
            occurred_at=current_time
        )

        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def process_exit_tick(
        db: Session,
        current_bid: float,
        current_ask: float,
        candle_high: Optional[float] = None,
        candle_low: Optional[float] = None,
        candle_timestamp: Optional[int] = None,
        bar_duration_ms: int = 15 * 60 * 1000,
        now_ms: Optional[int] = None,
        clock: Optional[IClock] = None,
        session_tag: str = "LIVE_PAPER"
    ) -> Optional[models.PaperOrder]:
        """
        Unified exit evaluator for both ExitMonitor and candles/sync.
        """
        active_pos = crud.get_active_position(db)
        if not active_pos or active_pos.state != "paper_open":
            return None

        opened_at = active_pos.opened_at or 0

        # Guard: Check candle timing relative to opened_at
        is_candle_eval = candle_high is not None and candle_low is not None
        if is_candle_eval and candle_timestamp is not None:
            bar_start = candle_timestamp
            bar_end = bar_start + bar_duration_ms

            if opened_at >= bar_end:
                return None

            if bar_start <= opened_at < bar_end:
                is_candle_eval = False

        exit_triggered = False
        exit_price = 0.0
        exit_cause = ""
        lp = active_pos.estimated_liquidation

        if not is_candle_eval:
            # TICK-ONLY EVALUATION
            if active_pos.direction == "LONG":
                if lp is not None and current_bid <= lp:
                    exit_triggered = True
                    exit_price = current_bid
                    exit_cause = "LIQUIDATED"
                elif current_bid <= active_pos.stop_loss:
                    exit_triggered = True
                    exit_price = current_bid
                    exit_cause = "SL_HIT"
                elif current_bid >= active_pos.take_profit:
                    exit_triggered = True
                    exit_price = active_pos.take_profit
                    exit_cause = "TP_HIT"

            elif active_pos.direction == "SHORT":
                if lp is not None and current_ask >= lp:
                    exit_triggered = True
                    exit_price = current_ask
                    exit_cause = "LIQUIDATED"
                elif current_ask >= active_pos.stop_loss:
                    exit_triggered = True
                    exit_price = current_ask
                    exit_cause = "SL_HIT"
                elif current_ask <= active_pos.take_profit:
                    exit_triggered = True
                    exit_price = active_pos.take_profit
                    exit_cause = "TP_HIT"

        else:
            # CANDLE OHLC EVALUATION
            high_val = candle_high
            low_val = candle_low

            if active_pos.direction == "LONG":
                hit_sl = low_val <= active_pos.stop_loss
                hit_tp = high_val >= active_pos.take_profit
                hit_liq = (lp is not None and low_val <= lp)

                if hit_sl and hit_tp:
                    exit_triggered = True
                    exit_price = active_pos.stop_loss
                    exit_cause = "AMBIGUOUS_BAR_SL_FIRST"
                elif hit_liq and not hit_tp:
                    exit_triggered = True
                    exit_price = lp
                    exit_cause = "LIQUIDATED"
                elif hit_sl:
                    exit_triggered = True
                    exit_price = min(active_pos.stop_loss, low_val)
                    exit_cause = "SL_HIT"
                elif hit_tp:
                    exit_triggered = True
                    exit_price = active_pos.take_profit
                    exit_cause = "TP_HIT"

            elif active_pos.direction == "SHORT":
                hit_sl = high_val >= active_pos.stop_loss
                hit_tp = low_val <= active_pos.take_profit
                hit_liq = (lp is not None and high_val >= lp)

                if hit_sl and hit_tp:
                    exit_triggered = True
                    exit_price = active_pos.stop_loss
                    exit_cause = "AMBIGUOUS_BAR_SL_FIRST"
                elif hit_liq and not hit_tp:
                    exit_triggered = True
                    exit_price = lp
                    exit_cause = "LIQUIDATED"
                elif hit_sl:
                    exit_triggered = True
                    exit_price = max(active_pos.stop_loss, high_val)
                    exit_cause = "SL_HIT"
                elif hit_tp:
                    exit_triggered = True
                    exit_price = active_pos.take_profit
                    exit_cause = "TP_HIT"

        if exit_triggered:
            return TradeLifecycleService.execute_close(
                db=db,
                order_id=active_pos.id,
                exit_price=exit_price,
                exit_cause=exit_cause,
                occurred_at=candle_timestamp or now_ms,
                clock=clock,
                session_tag=session_tag
            )

        return None

    @staticmethod
    def _create_lesson(
        db: Session,
        order: models.PaperOrder,
        now_ms: int,
        session_tag: str = "LIVE_PAPER"
    ):
        """
        Create factual, evidence-based Lesson.
        Separates trading outcome from execution quality:
        A win does not prove rules were followed; a loss does not prove rules were wrong.
        Does NOT fake Sweep/FVG when unverified.
        """
        # Check idempotency: do not create duplicate lessons for the same trade
        existing_lesson = db.query(models.Lesson).filter(models.Lesson.related_trade_id == order.id).first()
        if existing_lesson:
            return existing_lesson

        pnl = order.realized_pnl_net or 0.0
        r_mult = order.realized_r or 0.0
        family = getattr(order, "strategy_family", "STANDARD_SMC") or "STANDARD_SMC"
        opened_at = order.opened_at or now_ms
        hold_time_ms = max(0, now_ms - opened_at)

        # Retrieve evidence
        evidence = {}
        if getattr(order, "evidence_snapshot_id", None):
            ev_row = db.query(models.StrategyEvidence).filter(models.StrategyEvidence.id == order.evidence_snapshot_id).first()
            if ev_row and ev_row.evidence_json:
                try:
                    evidence = json.loads(ev_row.evidence_json)
                except Exception:
                    evidence = {}

        has_sweep = bool(evidence.get("sweep_id") or evidence.get("sweep_level"))
        has_fvg = bool(evidence.get("fvg_id") or evidence.get("fvg_zone"))
        has_displacement = bool(evidence.get("is_displacement"))

        if family == "NY_QUOTA_PAPER":
            compliance_status = "PARTIAL_FALLBACK"
            compliance_detail = "Lệnh NY Quota Fallback: Khung 5M retest POI, không yêu cầu Liquidity Sweep đầy đủ."
            invalidation_basis = f"Thủng vùng POI tại SL {order.stop_loss:.2f}"
        elif has_sweep and has_fvg and has_displacement:
            compliance_status = "FULL_COMPLIANCE"
            compliance_detail = "Chuỗi SMC đầy đủ: Sweep + Displacement + MSS + FVG Retest có bằng chứng."
            invalidation_basis = f"Invalidation cấu trúc tại SL {order.stop_loss:.2f}"
        else:
            compliance_status = "INCOMPLETE_EVIDENCE"
            compliance_detail = "Thiếu bằng chứng chuỗi SMC đầy đủ tại thời điểm vào lệnh."
            invalidation_basis = f"Stop Loss tại {order.stop_loss:.2f}"

        cause = str(order.exit_cause or "").upper()
        exit_p = order.actual_exit or 0.0

        if cause == "LIQUIDATED":
            title = f"THANH LÝ VỊ THẾ {family} {order.direction} (-${abs(pnl):.2f}) tại {exit_p:.2f}"
            reflection = f"Vị thế {order.direction} bị thanh lý do giá chạm Liquidation Price ({exit_p:.2f})."
            action_rule = "Xem lại mức đòn bẩy và luôn duy trì khoảng đệm an toàn giữa SL và Liquidation Price."
        elif cause in ("AMBIGUOUS_BAR_SL_FIRST", "AMBIGUOUS_BAR_CONSERVATIVE_SL"):
            title = f"Dừng lỗ nến mơ hồ {family} {order.direction} (-${abs(pnl):.2f}) tại {exit_p:.2f}"
            reflection = "Nến biến động mạnh chạm cả TP và SL trong cùng một bar. Giả định thận trọng SL khớp trước."
            action_rule = "Tránh giữ lệnh qua các thời điểm công bố tin tức có độ biến động hai đầu lớn."
        elif cause in ("MANUAL_CLOSE", "USER_EXIT", "MANUAL_CLOSED"):
            if pnl >= 0:
                title = f"Đóng chủ động (Có lãi) {family} {order.direction} +{r_mult:.2f}R (+${pnl:.2f}) tại {exit_p:.2f}"
                reflection = f"Lệnh {order.direction} ({family}) được đóng chủ động từ Dashboard khi đang có lãi (+${pnl:.2f}). {compliance_detail}"
            else:
                title = f"Đóng chủ động (Cắt lỗ) {family} {order.direction} {r_mult:.2f}R (-${abs(pnl):.2f}) tại {exit_p:.2f}"
                reflection = f"Lệnh {order.direction} ({family}) được đóng chủ động từ Dashboard khi đang bị lỗ (-${abs(pnl):.2f}). {compliance_detail}"
            action_rule = "Ghi nhận lý do đóng lệnh trước kế hoạch vào phần Đánh giá tâm lý để rà soát kỷ luật."
        elif cause == "TP_HIT":
            if pnl >= 0:
                title = f"Thắng {family} {order.direction} +{r_mult:.2f}R (+${pnl:.2f}) tại {exit_p:.2f}"
                reflection = f"Lệnh {order.direction} ({family}) đạt Take Profit. {compliance_detail}"
                action_rule = "Tiếp tục thu thập dữ liệu; không nới lỏng quy tắc dựa trên kết quả đơn lẻ."
            else:
                title = f"Đạt TP nhưng lỗ ròng {family} {order.direction} {r_mult:.2f}R (${pnl:.2f}) tại {exit_p:.2f}"
                reflection = f"Lệnh {order.direction} chạm giá Take Profit nhưng PnL ròng âm sau khi trừ chi phí. {compliance_detail}"
                action_rule = "Kiểm tra lại biên độ lợi nhuận tối thiểu so với chi phí spread và phí sàn."
        elif cause == "SL_HIT":
            title = f"Dừng lỗ {family} {order.direction} {r_mult:.2f}R (-${abs(pnl):.2f}) tại {exit_p:.2f}"
            reflection = f"Lệnh {order.direction} chạm SL ({order.exit_cause}). {compliance_detail}"
            action_rule = "Kiểm tra lại biên độ buffer ATR và cấu trúc bảo vệ; ghi nhận số liệu mẫu để nghiên cứu."
        else:
            sign = "+" if pnl >= 0 else "-"
            title = f"Đóng vị thế ({cause or 'CLOSED'}) {family} {order.direction} {sign}${abs(pnl):.2f} tại {exit_p:.2f}"
            reflection = f"Lệnh {order.direction} ({family}) kết thúc với nguyên nhân {cause or 'CLOSED'}. {compliance_detail}"
            action_rule = "Ghi nhận dữ liệu thực tế vào Journal để phân tích thêm."

        db_lesson = models.Lesson(
            created_at=now_ms,
            title=title,
            category="EXECUTION",
            related_trade_id=order.id,
            setup_type=family,
            session=session_tag,
            reflection=reflection,
            action_rule=action_rule,
            is_hard_filter=False,
            is_approved=False,  # Lessons require human review/validation, never auto-approved
            strategy_family=family,
            facts_snapshot=json.dumps({
                "family": family,
                "direction": order.direction,
                "actual_entry": order.actual_entry,
                "actual_exit": order.actual_exit,
                "pnl": pnl,
                "realized_r": r_mult,
                "exit_cause": order.exit_cause
            }),
            compliance_snapshot=json.dumps({
                "compliance_status": compliance_status,
                "compliance_detail": compliance_detail,
                "invalidation_basis": invalidation_basis
            }),
            mfe_mae_snapshot=json.dumps({
                "hold_time_ms": hold_time_ms
            })
        )
        db.add(db_lesson)
        db.flush()
        return db_lesson
