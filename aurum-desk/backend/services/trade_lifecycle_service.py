import time
import json
import logging
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
    - Prevents split-commit crashes (order state + audit + lesson + domain event + outbox commit together).
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
        - Commits order state 'paper_open', DayAudit increment, watch setup state,
          DomainEvent 'trade.opened', and NotificationOutbox 'FILLED' in a single transaction.
        """
        current_time = now_ms if now_ms is not None else (clock.now_ms() if clock else int(time.time() * 1000))

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

        # 4. Record trade fill audit (flush without separate commit)
        crud.record_trade_fill_audit(db, commit=False, date_str=date_str, clock=clock)

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
        session_tag: str = "LIVE_PAPER"
    ) -> Optional[models.PaperOrder]:
        """
        Atomic Unit of Work: Close Open Position.
        - Guarded conditional state check (must be 'paper_open').
        - Computes Net PnL and Realized R after round-trip fees.
        - Updates DayAudit (consecutive losses, cooldown, 1.5% loss cap).
        - Generates structured Lesson.
        - Creates DomainEvent and NotificationOutbox (TP_HIT, SL_HIT, MANUAL_CLOSED, LIQUIDATED).
        - Commits in ONE single atomic transaction.
        """
        current_time = occurred_at or (clock.now_ms() if clock else int(time.time() * 1000))

        # Conditional update check: only close if currently paper_open
        order = db.query(models.PaperOrder).filter(
            models.PaperOrder.id == order_id,
            models.PaperOrder.state == "paper_open"
        ).first()

        if not order:
            # Position not open or already closed by concurrent caller
            return None

        entry_p = order.actual_entry or order.planned_entry
        qty = order.quantity
        direction_mult = 1.0 if order.direction == "LONG" else -1.0
        gross_pnl = (exit_price - entry_p) * qty * direction_mult

        # Fees: round-trip 0.04% maker/taker
        fee_cost = (entry_p + exit_price) * qty * 0.0004
        net_pnl = round(gross_pnl - fee_cost, 2)
        realized_r = round(net_pnl / order.initial_risk_usdt, 2) if (order.initial_risk_usdt and order.initial_risk_usdt > 0) else 0.0

        # Update order fields
        order.state = "closed"
        order.actual_exit = round(exit_price, 2)
        order.realized_pnl_net = net_pnl
        order.realized_r = realized_r
        order.exit_cause = exit_cause
        order.closed_at = current_time

        # Update DayAudit in same transaction
        crud.record_trade_close_audit(db, net_pnl, commit=False, date_str=date_str, clock=clock, now_ms=current_time)

        # Update associated WatchSetup if present
        if order.setup_id:
            watch_setup = db.query(models.WatchSetup).filter(models.WatchSetup.id == order.setup_id).first()
            if watch_setup:
                watch_setup.state = "CLOSED"
                watch_setup.updated_at = current_time

        # Create structured Lesson (with proper session tag)
        TradeLifecycleService._create_lesson(db, order, current_time, session_tag=session_tag)

        # Create Domain Event & Notification Outbox in same transaction
        event_type = "trade.liquidated" if exit_cause == "LIQUIDATED" else "trade.closed"
        event_payload = {
            "trade_id": order.id,
            "order_id": order.id,
            "direction": order.direction,
            "actual_entry": entry_p,
            "actual_exit": round(exit_price, 2),
            "realized_pnl": net_pnl,
            "realized_r": realized_r,
            "exit_cause": exit_cause,
            "opened_at": order.opened_at,
            "closed_at": current_time
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
        Unified exit evaluator for both ExitMonitor and candles/sync:
        - Strict executable side: LONG exits at Bid; SHORT exits at Ask.
        - Prevents using pre-entry candle extremes (no retroactive fills or exits).
        - Correctly prioritizes SL vs Liquidation on continuous price streams.
        - Flags AMBIGUOUS_BAR_SL_FIRST when single bar touches both TP and SL.
        - Calls execute_close atomically when triggered.
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

            # 1. Bar is strictly in the past before position opened -> ignore
            if opened_at >= bar_end:
                return None

            # 2. Bar is the entry bar -> opened_at is within this bar!
            # Full high/low cannot be assumed to have occurred after opened_at
            if bar_start <= opened_at < bar_end:
                # Disallow full bar extremes for entry bar, fallback to live tick
                is_candle_eval = False

        exit_triggered = False
        exit_price = 0.0
        exit_cause = ""
        lp = active_pos.estimated_liquidation

        if not is_candle_eval:
            # ==================== TICK-ONLY EVALUATION ====================
            # LONG exits by selling at current_bid
            if active_pos.direction == "LONG":
                # Liquidation check
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

            # SHORT exits by buying at current_ask
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
            # ==================== ELIGIBLE CLOSED BAR EVALUATION ====================
            high_val = candle_high
            low_val = candle_low

            if active_pos.direction == "LONG":
                hit_sl = low_val <= active_pos.stop_loss
                hit_tp = high_val >= active_pos.take_profit
                hit_liq = (lp is not None and low_val <= lp)

                if hit_sl and hit_tp:
                    # Ambiguous bar: both touched in same bar -> conservative rule SL first
                    exit_triggered = True
                    exit_price = active_pos.stop_loss
                    exit_cause = "AMBIGUOUS_BAR_SL_FIRST"
                elif hit_liq and not hit_tp:
                    # In continuous price, SL is hit before LP unless a major gap occurred
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
        pnl = order.realized_pnl_net or 0.0
        r_mult = order.realized_r or 0.0

        if order.exit_cause == "LIQUIDATED":
            title = f"THANH LÝ VỊ THẾ {order.direction} (-${abs(pnl):.2f}) tại {order.actual_exit:.2f}"
            reflection = f"Vị thế {order.direction} bị thanh lý do giá chạm mức Liquidation Price ({order.actual_exit:.2f})."
            action_rule = "Xem lại mức đòn bẩy và luôn duy trì khoảng đệm an toàn giữa SL và Liquidation Price."
        elif order.exit_cause == "AMBIGUOUS_BAR_SL_FIRST":
            title = f"Dừng lỗ nến mơ hồ {order.direction} (-${abs(pnl):.2f}) tại {order.actual_exit:.2f}"
            reflection = "Nến biến động mạnh chạm cả TP và SL trong cùng một bar. Giả định thận trọng SL khớp trước."
            action_rule = "Tránh giữ lệnh qua các thời điểm công bố tin tức có độ biến động hai đầu lớn."
        elif pnl >= 0:
            title = f"Thắng {order.direction} +{r_mult}R (+${pnl:.2f}) theo cấu trúc SMC"
            reflection = f"Lệnh {order.direction} tuân thủ đúng quy tắc Sweep và FVG. TP tại {order.actual_exit:.2f} hoàn thành kỳ vọng."
            action_rule = "Tiếp tục duy trì tính kỷ luật chỉ mở lệnh khi có Liquidity Sweep rõ ràng."
        else:
            title = f"Dừng lỗ {order.direction} {r_mult}R (-${abs(pnl):.2f}) tại {order.actual_exit:.2f}"
            reflection = f"Lệnh chạm SL do {order.exit_cause}. Thị trường biến động mạnh hơn dự kiến."
            action_rule = "Kiểm tra lại biên độ buffer ATR và tránh vào lệnh gần vùng biến động mở phiên."

        is_approved = (session_tag == "LIVE_PAPER")

        db_lesson = models.Lesson(
            created_at=now_ms,
            title=title,
            category="EXECUTION",
            related_trade_id=order.id,
            setup_type="SMC_ORDER",
            session=session_tag,
            reflection=reflection,
            action_rule=action_rule,
            is_hard_filter=False,
            is_approved=is_approved
        )
        db.add(db_lesson)
