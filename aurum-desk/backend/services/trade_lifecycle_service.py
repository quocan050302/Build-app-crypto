import time
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple
from sqlalchemy import text
from sqlalchemy.orm import Session
from database import SessionLocal
import models, crud
from domain_calculator import CalculationResult, calculate_risk_reward
from services.event_bus import event_bus
from services.clock import live_clock, IClock
from services.shared_exit_evaluator import evaluate_order_exit

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
        date_str: Optional[str] = None,
        sizing_context: Optional[Dict[str, Any]] = None,
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

        # 1.5. V10.1 Shared Final Guard: Ensure BEFORE_FILL rules are verified before position is opened
        from services.entry_decision_service import EntryDecisionService
        if not getattr(order, "fill_decision_snapshot", None):
            fill_context = EntryDecisionService.build_context_from_order(
                order=order,
                stage="BEFORE_FILL",
                fill_price=fill_price,
                calc_result=calc_result,
                now_ms=current_time
            )
            decision = EntryDecisionService.evaluate_entry_rules(db, fill_context)
            EntryDecisionService.record_stage_decision(order, "BEFORE_FILL", decision, now_ms=current_time)
            if not decision.get("can_proceed", True):
                block_reasons = decision.get("blocking_reasons", [])
                reason_str = block_reasons[0] if block_reasons else "Quy tắc bài học chặn khớp lệnh"
                if order.state == "candidate":
                    raise ValueError(f"LESSON_RULE_BLOCKED: {reason_str}")
                order.state = "rejected"
                order.invalidation_reason = f"LESSON_RULE_BLOCKED: {reason_str}"
                order.closed_at = current_time
                db.commit()
                return order

        # 1.6. Rules 055, 056, 059, 066, 079, 080: Verify daily bias validity for PRIMARY pending orders before fill
        if getattr(order, "arm_decision_snapshot", None):
            try:
                raw_snap = order.arm_decision_snapshot
                arm_snap = json.loads(raw_snap) if isinstance(raw_snap, str) else raw_snap
                binding = arm_snap.get("binding") or {}
                if binding.get("direction_policy") == "DAILY_BIAS_PRIMARY":
                    from services.cg01_config_factory import resolve_canonical_cg01_config
                    from services.cg01_daily_bias_service import (
                        resolve_current_cg01_bias_authority,
                        validate_cg01_bias_authority_binding,
                    )
                    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(
                        db=db,
                        symbol=order.instrument or "XAUUSDT",
                    )
                    binding_fp = binding.get("config_fingerprint")

                    # Rules 044, 061, 062, 063: Query resolver strictly using effective execution config.
                    # sizing_context NEVER overrides authoritative resolver.
                    res_auth = resolve_current_cg01_bias_authority(
                        symbol=order.instrument or "XAUUSDT",
                        now_ms=current_time,
                        config=eff_cfg,
                        config_fingerprint=eff_fp,
                        clock=clock,
                    )

                    valid, code, msg_vi, action = validate_cg01_bias_authority_binding(
                        candidate_bias=binding,
                        current_authority=res_auth,
                        order_direction=order.direction,
                        symbol=order.instrument or "XAUUSDT",
                        effective_fingerprint=eff_fp,
                        is_persisted_binding=True,
                    )

                    if not valid:
                        if action == "INVALIDATE":
                            order.state = "invalidated"
                            reason_prefix = "DAILY_BIAS_INVALIDATED" if code in ("INVALIDATED", "DAILY_BIAS_CURRENT_INVALIDATED", "DAILY_BIAS_INVALIDATED") else code
                            order.invalidation_reason = f"{reason_prefix}: {msg_vi}"
                            order.closed_at = current_time
                            db.commit()
                            return order
                        else:  # DEFER or BLOCK
                            logger.info(f"Cannot fill order {order.id}: {code} - {msg_vi}; keeping pending.")
                            return order
            except Exception as e:
                # Rules 069, 070, 082: Fail-closed on exception for PRIMARY - do NOT fill!
                logger.error(f"Error checking pending bias binding at fill time for {order.id}: {e}")
                return order

        # 2. Invariant: Max 1 active position enforced atomically at DB level
        from services.order_plan_snapshot_service import build_fill_plan_snapshot
        cost_snap_dict = build_fill_plan_snapshot(
            calc_result=calc_result,
            order=order,
            fill_price=fill_price,
            now_ms=current_time,
            sizing_context=sizing_context,
        )
        cost_snap = json.dumps(cost_snap_dict, ensure_ascii=False)

        stmt = text("""
            UPDATE paper_orders
            SET state = 'paper_open',
                actual_entry = :fill_price,
                opened_at = :opened_at,
                quantity = :quantity,
                initial_risk_usdt = :initial_risk_usdt,
                gross_rr = :gross_rr,
                estimated_net_rr = :estimated_net_rr,
                estimated_liquidation = :estimated_liquidation,
                initial_margin = :initial_margin,
                leverage = :leverage,
                margin_mode = :margin_mode,
                cost_snapshot = :cost_snapshot,
                fill_decision_snapshot = :fill_decision_snapshot,
                arm_decision_snapshot = :arm_decision_snapshot,
                execution_mode = :execution_mode,
                origin = :origin,
                lessons_retrieved = :lessons_retrieved
            WHERE id = :order_id
              AND state IN ('armed', 'candidate')
              AND NOT EXISTS (
                  SELECT 1 FROM paper_orders WHERE state = 'paper_open' AND id != :order_id
              )
        """)
        res = db.execute(stmt, {
            "fill_price": fill_price,
            "opened_at": current_time,
            "quantity": calc_result.quantity,
            "initial_risk_usdt": calc_result.net_risk_usdt,
            "gross_rr": calc_result.gross_rr,
            "estimated_net_rr": calc_result.net_rr,
            "estimated_liquidation": calc_result.estimated_liquidation,
            "initial_margin": calc_result.initial_margin_usdt,
            "leverage": calc_result.leverage,
            "margin_mode": calc_result.margin_mode,
            "cost_snapshot": cost_snap,
            "fill_decision_snapshot": getattr(order, "fill_decision_snapshot", None),
            "arm_decision_snapshot": getattr(order, "arm_decision_snapshot", None),
            "execution_mode": getattr(order, "execution_mode", "AUTO") or "AUTO",
            "origin": getattr(order, "origin", "UNKNOWN") or "UNKNOWN",
            "lessons_retrieved": getattr(order, "lessons_retrieved", None),
            "order_id": order.id
        })

        if res.rowcount == 0:
            existing_open = db.query(models.PaperOrder).filter(
                models.PaperOrder.state == "paper_open",
                models.PaperOrder.id != order.id
            ).first()
            if existing_open:
                order.state = "rejected"
                order.invalidation_reason = "Đã có vị thế đang mở (giới hạn tối đa 1 vị thế)"
                order.closed_at = current_time
                try:
                    db.commit()
                    return order
                except Exception as e:
                    db.rollback()
                    logger.error(f"Commit failed rejecting fill for {order.id}: {e}")
                    return None
            else:
                db.refresh(order)
                return order

        db.refresh(order)

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

        event_payload = {
            "trade_id": order.id,
            "order_id": order.id,
            "source": getattr(order, "execution_mode", source) or source,
            "execution_mode": getattr(order, "execution_mode", "AUTO"),
            "origin": getattr(order, "origin", "UNKNOWN"),
            "direction": order.direction,
            "strategy_family": getattr(order, "strategy_family", "STANDARD_SMC") or "STANDARD_SMC",
            "actual_entry": fill_price,
            "planned_entry": order.planned_entry,
            "quantity": order.quantity,
            "stop_loss": order.stop_loss,
            "take_profit": order.take_profit,
            "initial_risk_usdt": order.initial_risk_usdt,
            "arm_decision_snapshot": getattr(order, "arm_decision_snapshot", None),
            "fill_decision_snapshot": getattr(order, "fill_decision_snapshot", None),
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

        # V14.4.15: Stage forward session membership status update to FILLED if bound
        from services.forward_paper_service import stage_forward_membership_status, stage_forward_repair_task
        try:
            stage_res = stage_forward_membership_status(db, order.id, "FILLED", now_ms=current_time)
            if stage_res.tracking_repair_needed:
                logger.warning(
                    f"Forward membership tracking repair needed for order {order.id}: {stage_res.reason_codes}"
                )
                stage_forward_repair_task(
                    db=db,
                    order_id=order.id,
                    forward_session_id=stage_res.forward_session_id,
                    target_status="FILLED",
                    reason_codes=stage_res.reason_codes,
                    now_ms=current_time,
                )
        except Exception as e:
            db.rollback()
            logger.error(f"execute_fill aborted due to membership staging failure for order {order.id}: {e}")
            return None

        # V16.16: Record FILLED milestone for linked research runs (Rules 028, 093, 095)
        # V16.17: Wrapped in nested savepoint (db.begin_nested) to prevent research failure from aborting fill
        try:
            with db.begin_nested():
                from services.paper_research_run_service import record_committed_order_milestone
                run_links = db.query(models.PaperResearchOrderLink).filter(
                    models.PaperResearchOrderLink.order_id == order.id
                ).all()
                for rlink in run_links:
                    record_committed_order_milestone(
                        db=db,
                        run_id=rlink.run_id,
                        order_id=order.id,
                        milestone="FILLED",
                        timestamp_ms=current_time,
                        payload={"fill_price": fill_price, "quantity": order.quantity},
                    )
        except Exception as ms_err:
            logger.warning(f"Failed to record FILLED milestone for order {order.id}: {ms_err}")

        # 7. Commit atomically at boundary (owner transaction)
        try:
            db.commit()
            db.refresh(order)
            return order
        except Exception as e:
            db.rollback()
            logger.error(f"execute_fill failed to commit for order {order.id}: {e}", exc_info=True)
            return None

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

        # V14.4.15: Stage forward session membership status to REJECTED if bound
        from services.forward_paper_service import stage_forward_membership_status, stage_forward_repair_task
        try:
            stage_res = stage_forward_membership_status(db, order.id, "REJECTED", now_ms=current_time)
            if stage_res.tracking_repair_needed:
                logger.warning(
                    f"Forward membership tracking repair needed for order {order.id}: {stage_res.reason_codes}"
                )
                stage_forward_repair_task(
                    db=db,
                    order_id=order.id,
                    forward_session_id=stage_res.forward_session_id,
                    target_status="REJECTED",
                    reason_codes=stage_res.reason_codes,
                    now_ms=current_time,
                )
        except Exception as e:
            db.rollback()
            logger.error(f"execute_reject aborted due to membership staging failure for order {order.id}: {e}")
            return None

        try:
            db.commit()
            db.refresh(order)
            return order
        except Exception as e:
            db.rollback()
            logger.error(f"execute_reject failed to commit for order {order.id}: {e}", exc_info=True)
            return None

    @staticmethod
    def execute_close(
        db: Session,
        order_id: str,
        exit_price: float,
        exit_cause: str,
        occurred_at: Optional[int] = None,
        now_ms: Optional[int] = None,
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
        current_time = occurred_at or now_ms or (clock.now_ms() if clock else int(time.time() * 1000))

        order = db.query(models.PaperOrder).filter(
            models.PaperOrder.id == order_id,
            models.PaperOrder.state == "paper_open"
        ).first()

        if not order:
            return None

        entry_p = order.actual_entry or order.planned_entry
        qty = order.quantity
        direction_mult = 1.0 if order.direction == "LONG" else -1.0

        # Multiplier and fee rates from cost_snapshot
        mult = 1.0
        maker_fee_rate = 0.0002
        taker_fee_rate = 0.0004
        tp_is_maker = False
        if getattr(order, 'cost_snapshot', None):
            try:
                snap = json.loads(order.cost_snapshot)
                mult = float(snap.get("multiplier", 1.0))
                maker_fee_rate = float(snap.get("maker_fee_rate", 0.0002))
                taker_fee_rate = float(snap.get("taker_fee_rate", 0.0004))
                tp_is_maker = bool(snap.get("tp_is_maker", False))
            except Exception:
                mult = 1.0
                maker_fee_rate = 0.0002
                taker_fee_rate = 0.0004

        gross_pnl = (exit_price - entry_p) * qty * mult * direction_mult

        entry_fee = entry_p * qty * mult * taker_fee_rate
        exit_fee_rate = maker_fee_rate if (exit_cause == "TP_HIT" and tp_is_maker) else taker_fee_rate
        exit_fee = exit_price * qty * mult * exit_fee_rate
        fee_cost = entry_fee + exit_fee

        net_pnl = round(gross_pnl - fee_cost, 2)
        realized_r = round(net_pnl / order.initial_risk_usdt, 2) if (order.initial_risk_usdt and order.initial_risk_usdt > 0) else 0.0

        # Atomic CAS transition: only close if state is currently 'paper_open'
        stmt = text("""
            UPDATE paper_orders
            SET state = 'closed',
                actual_exit = :actual_exit,
                realized_pnl_net = :net_pnl,
                realized_r = :realized_r,
                exit_cause = :exit_cause,
                closed_at = :closed_at
            WHERE id = :order_id AND state = 'paper_open'
        """)
        res = db.execute(stmt, {
            "actual_exit": round(exit_price, 2),
            "net_pnl": net_pnl,
            "realized_r": realized_r,
            "exit_cause": exit_cause,
            "closed_at": current_time,
            "order_id": order.id
        })
        if res.rowcount == 0:
            # Another session or watchdog already closed this position
            return None
        db.refresh(order)

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

        # V16.39.1 Section 116: Build & persist canonical post-trade review first
        review_obj = None
        try:
            from services.post_trade_review_service import build_post_trade_review, save_post_trade_review
            outcome_dict = {
                "order_id": order.id,
                "exit_price": exit_price,
                "exit_cause": exit_cause,
                "gross_pnl": gross_pnl,
                "net_pnl": net_pnl,
                "realized_r": realized_r,
                "closed_at_ms": current_time,
            }
            raw_arm_snap = getattr(order, "arm_decision_snapshot", None)
            arm_evidence = json.loads(raw_arm_snap) if isinstance(raw_arm_snap, str) else (raw_arm_snap or {})
            review_obj = build_post_trade_review(order, outcome_dict, entry_evidence=arm_evidence, now_ms=current_time)
            save_post_trade_review(db, review_obj)
        except Exception as rev_err:
            logger.warning(f"Post-trade review generation/persistence failed for order {order.id}: {rev_err}")

        # Create structured Lesson using canonical review (Section 116, 122, 123)
        try:
            lesson_res = TradeLifecycleService._create_lesson(db, order, current_time, session_tag=session_tag, review_obj=review_obj)
            if lesson_res:
                TradeLifecycleService._resolve_lesson_repair_task(db, order.id, current_time)
        except Exception as l_err:
            logger.warning(f"Lesson creation failed for order {order.id}: {l_err}")
            TradeLifecycleService._record_lesson_repair_task(db, order.id, str(l_err), current_time)

        # Record persistent paper observation for closed trade outcome
        try:
            from services.smc_observation_service import record_closed_outcome
            record_closed_outcome(db, order, as_of_ms=current_time)
        except Exception as obs_err:
            logger.warning(f"Failed to record closed outcome observation for {order.id}: {obs_err}")

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

        # V14.4.15: Stage forward session membership status to CLOSED if bound
        from services.forward_paper_service import stage_forward_membership_status, stage_forward_repair_task
        try:
            stage_res = stage_forward_membership_status(db, order.id, "CLOSED", now_ms=current_time)
            if stage_res.tracking_repair_needed:
                logger.warning(
                    f"Forward membership tracking repair needed for order {order.id}: {stage_res.reason_codes}"
                )
                stage_forward_repair_task(
                    db=db,
                    order_id=order.id,
                    forward_session_id=stage_res.forward_session_id,
                    target_status="CLOSED",
                    reason_codes=stage_res.reason_codes,
                    now_ms=current_time,
                )
        except Exception as e:
            db.rollback()
            logger.error(f"execute_close aborted due to membership staging failure for order {order.id}: {e}")
            return None

        # V16.16: Record CLOSED milestone for linked research runs (Rules 028, 094, 095)
        # V16.17: Wrapped in nested savepoint (db.begin_nested) to prevent research failure from aborting close
        try:
            with db.begin_nested():
                from services.paper_research_run_service import record_committed_order_milestone
                run_links = db.query(models.PaperResearchOrderLink).filter(
                    models.PaperResearchOrderLink.order_id == order.id
                ).all()
                for rlink in run_links:
                    record_committed_order_milestone(
                        db=db,
                        run_id=rlink.run_id,
                        order_id=order.id,
                        milestone="CLOSED",
                        timestamp_ms=current_time,
                        payload={"exit_price": exit_price, "exit_cause": exit_cause, "realized_pnl_net": net_pnl},
                    )
        except Exception as ms_err:
            logger.warning(f"Failed to record CLOSED milestone for order {order.id}: {ms_err}")

        try:
            db.commit()
            db.refresh(order)
            return order
        except Exception as e:
            db.rollback()
            logger.error(f"execute_close failed to commit for order {order.id}: {e}", exc_info=True)
            return None

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
        session_tag: str = "LIVE_PAPER",
        tick_timestamp: Optional[int] = None,
    ) -> Optional[models.PaperOrder]:
        """
        Unified exit evaluator for both ExitMonitor and candles/sync.
        """
        active_pos = crud.get_active_position(db)
        if not active_pos or active_pos.state != "paper_open":
            return None

        opened_at = active_pos.opened_at or 0

        # Guard: Check tick timing relative to opened_at (late tick cannot retroactively exit)
        if tick_timestamp is not None and opened_at > 0 and tick_timestamp < opened_at:
            return None

        # Guard: Check candle timing relative to opened_at
        is_candle_eval = candle_high is not None and candle_low is not None
        if is_candle_eval and candle_timestamp is not None:
            bar_start = candle_timestamp
            bar_end = bar_start + bar_duration_ms

            if opened_at >= bar_end:
                return None

            if bar_start <= opened_at < bar_end:
                is_candle_eval = False

        executable_quote: Dict[str, Any] = {
            "bid": current_bid,
            "ask": current_ask,
            "evidence_id": f"tick_{tick_timestamp or now_ms}",
        }
        if is_candle_eval:
            executable_quote["high"] = candle_high
            executable_quote["low"] = candle_low

        decision = evaluate_order_exit(active_pos, executable_quote)

        if decision.triggered:
            return TradeLifecycleService.execute_close(
                db=db,
                order_id=active_pos.id,
                exit_price=decision.applied_exit_price,
                exit_cause=decision.cause,
                occurred_at=tick_timestamp or now_ms or candle_timestamp,
                clock=clock,
                session_tag=session_tag,
            )

        return None

    @staticmethod
    def _create_lesson(
        db: Session,
        order: models.PaperOrder,
        now_ms: int,
        session_tag: str = "LIVE_PAPER",
        review_obj: Optional[Any] = None,
    ) -> Optional[models.Lesson]:
        """
        V16.39.2 Sections 011, 016, 021 - 045:
        Create factual, evidence-based Lesson linked directly to closed order.
        Separates trading outcome from execution quality:
        - Planned entry reads order.planned_entry (NEVER order.entry_price).
        - Idempotent: returns existing lesson if one exists for the order.
        - Uses build_entry_review_context and assess_entry_compliance_for_review.
        """
        # Check idempotency: do not create duplicate lessons for the same trade (Section 034, 124)
        existing_lesson = db.query(models.Lesson).filter(models.Lesson.related_trade_id == order.id).first()
        if existing_lesson:
            return existing_lesson

        pnl = order.realized_pnl_net or 0.0
        r_mult = order.realized_r or 0.0
        family = getattr(order, "strategy_family", "STANDARD_SMC") or "STANDARD_SMC"
        opened_at = order.opened_at or now_ms
        hold_time_ms = max(0, now_ms - opened_at)

        # Build entry review context from historical artifacts (Section 021)
        from services.post_trade_review_service import (
            build_entry_review_context,
            assess_entry_compliance_for_review,
            classify_post_trade_lesson,
            get_post_trade_review,
            build_post_trade_review,
            resolve_model_review_requirements,
        )
        ctx = build_entry_review_context(db, order)
        scenario_evidence = ctx.get("scenario_evidence") or {}
        raw_ev = scenario_evidence.get("evidence")
        tc = scenario_evidence.get("trigger_chain")
        guard_audit = ctx.get("guard_audit")
        model_reqs = ctx.get("model_requirements")

        entry_snap_dict = {
            "symbol": order.symbol,
            "direction": order.direction,
            "planned_entry": order.planned_entry,  # Section 011 & 016: Planned entry, NEVER order.entry_price!
            "actual_entry": order.actual_entry,
            "planned_sl": order.stop_loss,
            "planned_tp": order.take_profit,
            "evidence": raw_ev,
            "trigger_chain": tc,
            "model_id": family,
            "strategy_family": family,
            "entry_mode": ctx.get("entry_mode"),
            "order_type": ctx.get("order_type"),
            "plan": scenario_evidence.get("plan") or {
                "entry_mode": ctx.get("entry_mode"),
                "order_type": ctx.get("order_type"),
                "entry": order.planned_entry,
                "stop_loss": order.stop_loss,
                "take_profit": order.take_profit,
            },
            "historical_entry_mode": ctx.get("historical_entry_mode"),
            "provenance_issues": ctx.get("provenance_issues", []),
        }

        # Compliance assessment (Section 097, V16.39.3 / V16.39.4 Sections 041 - 088)
        comp_res = assess_entry_compliance_for_review(
            order=order,
            entry_snapshot=entry_snap_dict,
            fill_snapshot=ctx.get("fill_snapshot"),
            audit=guard_audit,
            model_requirements=model_reqs,
            decision_as_of_ms=ctx.get("decision_as_of_ms"),
            provenance_issues=ctx.get("provenance_issues"),
            review_context=ctx,
        )

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
                reflection = f"Lệnh {order.direction} ({family}) được đóng chủ động từ Dashboard khi đang có lãi (+${pnl:.2f}). {comp_res.reason}"
            else:
                title = f"Đóng chủ động (Cắt lỗ) {family} {order.direction} {r_mult:.2f}R (-${abs(pnl):.2f}) tại {exit_p:.2f}"
                reflection = f"Lệnh {order.direction} ({family}) được đóng chủ động từ Dashboard khi đang bị lỗ (-${abs(pnl):.2f}). {comp_res.reason}"
            action_rule = "Ghi nhận lý do đóng lệnh trước kế hoạch vào phần Đánh giá tâm lý để rà soát kỷ luật."
        elif cause == "TP_HIT":
            if pnl >= 0:
                title = f"Thắng {family} {order.direction} +{r_mult:.2f}R (+${pnl:.2f}) tại {exit_p:.2f}"
                reflection = f"Lệnh {order.direction} ({family}) đạt Take Profit. {comp_res.reason}"
                action_rule = "Tiếp tục thu thập dữ liệu; không nới lỏng quy tắc dựa trên kết quả đơn lẻ."
            else:
                title = f"Đạt TP nhưng lỗ ròng {family} {order.direction} {r_mult:.2f}R (${pnl:.2f}) tại {exit_p:.2f}"
                reflection = f"Lệnh {order.direction} chạm giá Take Profit nhưng PnL ròng âm sau khi trừ chi phí. {comp_res.reason}"
                action_rule = "Kiểm tra lại biên độ lợi nhuận tối thiểu so với chi phí spread và phí sàn."
        elif cause == "SL_HIT":
            title = f"Dừng lỗ {family} {order.direction} {r_mult:.2f}R (-${abs(pnl):.2f}) tại {exit_p:.2f}"
            reflection = f"Lệnh {order.direction} chạm SL ({order.exit_cause}). {comp_res.reason}"
            action_rule = "Kiểm tra lại biên độ buffer ATR và cấu trúc bảo vệ; ghi nhận số liệu mẫu để nghiên cứu."
        else:
            sign = "+" if pnl >= 0 else "-"
            title = f"Đóng vị thế ({cause or 'CLOSED'}) {family} {order.direction} {sign}${abs(pnl):.2f} tại {exit_p:.2f}"
            reflection = f"Lệnh {order.direction} ({family}) kết thúc với nguyên nhân {cause or 'CLOSED'}. {comp_res.reason}"
            action_rule = "Ghi nhận dữ liệu thực tế vào Journal để phân tích thêm."

        # Canonical post-trade review
        rev = review_obj
        if rev is None:
            rev = get_post_trade_review(db, order.id)
            if rev is None:
                outcome_dict = {
                    "order_id": order.id,
                    "exit_price": exit_p,
                    "exit_cause": cause,
                    "gross_pnl": getattr(order, "realized_pnl_gross", None) or 0.0,
                    "net_pnl": pnl,
                    "realized_r": r_mult,
                    "closed_at_ms": now_ms,
                }
                raw_arm_snap = getattr(order, "arm_decision_snapshot", None)
                arm_evidence = json.loads(raw_arm_snap) if isinstance(raw_arm_snap, str) else (raw_arm_snap or {})
                rev = build_post_trade_review(order, outcome_dict, entry_evidence=arm_evidence, now_ms=now_ms)

        classification = classify_post_trade_lesson(
            rev,
            entry_snapshot=entry_snap_dict,
            audit=guard_audit,
            compliance_result=comp_res,
        )

        hold_time_val = None
        close_ts = getattr(order, "closed_at", None) or now_ms
        if opened_at is not None and close_ts >= opened_at:
            hold_time_val = close_ts - opened_at

        # Resolve real entry session (V16.39.4 Sections 027, 033, 034, 061-065, 071, 078)
        entry_session = None
        raw_cost = getattr(order, "cost_snapshot", None)
        if raw_cost:
            try:
                parsed_cost = json.loads(raw_cost) if isinstance(raw_cost, str) else raw_cost
                if isinstance(parsed_cost, dict):
                    entry_session = parsed_cost.get("entry_session") or parsed_cost.get("analysis_session") or parsed_cost.get("session")
            except Exception:
                pass

        if not entry_session and getattr(order, "arm_decision_snapshot", None):
            try:
                raw_arm = order.arm_decision_snapshot
                parsed_arm = json.loads(raw_arm) if isinstance(raw_arm, str) else raw_arm
                if isinstance(parsed_arm, dict):
                    entry_session = parsed_arm.get("entry_session") or parsed_arm.get("analysis_session") or parsed_arm.get("session")
            except Exception:
                pass

        if not entry_session:
            entry_ts = getattr(order, "armed_at", None) or getattr(order, "created_at", None) or now_ms
            try:
                from services.trading_policy_service import TradingPolicyService, resolve_entry_session_context
                pol = TradingPolicyService.get_or_create_policy(db)
                s_ctx = resolve_entry_session_context(pol, family, now_ms=entry_ts)
                entry_session = s_ctx.analysis_session
            except Exception:
                entry_session = "ALL"

        effective_session = entry_session or "ALL"

        # Determine processing origin
        execution_origin = session_tag if session_tag in ("LIVE_PAPER", "REPAIR", "OFFLINE_RECOVERY", "LATE_RECONCILIATION") else ("REPAIR" if session_tag == "REPAIR" else "NORMAL")
        is_repair = (execution_origin == "REPAIR")

        db_lesson = models.Lesson(
            created_at=now_ms,
            title=title,
            category="EXECUTION",
            related_trade_id=order.id,
            setup_type=family,
            session=effective_session,
            reflection=reflection,
            action_rule=action_rule,
            is_hard_filter=False,
            is_approved=False,  # Section 101: Never auto-approved
            enabled=False,      # Section 101: Never auto-enabled
            status="PENDING_REVIEW",  # Section 101
            validation_status="UNVALIDATED",
            severity=classification.canonical_severity,  # INFO, WARNING, CRITICAL
            effect=classification.proposed_effect,        # ANNOTATE, WARN_ENTRY, BLOCK_ENTRY
            hypothesis=classification.reason_vi,         # "Vì sao bot chọn màu này?" (Section 106)
            scope=json.dumps(classification.suggested_scope),
            action_candidate=json.dumps(classification.to_dict()),
            strategy_family=family,
            facts_snapshot=json.dumps({
                "family": family,
                "direction": order.direction,
                "entry_mode": ctx.get("entry_mode"),
                "order_type": ctx.get("order_type"),
                "entry_mode_source": (ctx.get("historical_entry_mode") or {}).get("source"),
                "actual_entry": order.actual_entry,
                "actual_exit": order.actual_exit,
                "pnl": pnl,
                "realized_r": r_mult,
                "exit_cause": order.exit_cause,
                "suggested_color": classification.suggested_color,
                "classification_code": classification.classification_code,
                "origin": execution_origin,
                "execution_origin": execution_origin,
                "processing_origin": execution_origin,
                "entry_session": effective_session,
                "session_source": "FROZEN_ENTRY_CONTEXT" if (raw_cost and ("session" in str(raw_cost))) else "RESOLVER_FALLBACK",
            }),
            compliance_snapshot=json.dumps(comp_res.to_dict()),
            mfe_mae_snapshot=json.dumps({
                "hold_time_ms": hold_time_val
            })
        )
        db.add(db_lesson)
        db.flush()
        return db_lesson

    @staticmethod
    def _record_lesson_repair_task(db: Session, order_id: str, error_msg: str, now_ms: int) -> None:
        """Section 039: Records durable repair task so missing lesson is recovered."""
        try:
            task = db.query(models.LessonRepairTask).filter(models.LessonRepairTask.order_id == order_id).first()
            if not task:
                task = models.LessonRepairTask(
                    id=f"rep_{order_id}",
                    order_id=order_id,
                    artifact_type="LESSON",
                    status="PENDING",
                    retry_count=1,
                    last_error=error_msg[:1000] if error_msg else None,
                    created_at=now_ms,
                    updated_at=now_ms,
                )
                db.add(task)
            else:
                task.retry_count = (task.retry_count or 0) + 1
                task.last_error = error_msg[:1000] if error_msg else None
                task.status = "PENDING"
                task.updated_at = now_ms
            db.flush()
        except Exception as e:
            logger.error(f"Failed to record lesson repair task for {order_id}: {e}")

    @staticmethod
    def _resolve_lesson_repair_task(db: Session, order_id: str, now_ms: int) -> None:
        """Section 041: Marks lesson repair task COMPLETED when lesson creation succeeds."""
        try:
            task = db.query(models.LessonRepairTask).filter(models.LessonRepairTask.order_id == order_id).first()
            if task and task.status != "COMPLETED":
                task.status = "COMPLETED"
                task.updated_at = now_ms
                db.flush()
        except Exception:
            pass

    @classmethod
    def repair_pending_lessons(cls, db: Session, now_ms: Optional[int] = None, limit: int = 20) -> Dict[str, Any]:
        """
        V16.39.2 / V16.39.3 Sections 039 - 042, 101 - 123:
        Durable repair mechanism for pending/failed lessons.
        Loads pending tasks bounded by limit, ordered by created_at.
        Idempotent: verifies if lesson already exists before creation.
        Never calls execute_close() or mutates accounting/DayAudit.
        """
        current_time = now_ms or int(time.time() * 1000)
        pending_tasks = db.query(models.LessonRepairTask).filter(
            models.LessonRepairTask.status == "PENDING"
        ).order_by(models.LessonRepairTask.created_at.asc()).limit(limit).all()

        repaired_count = 0
        failed_count = 0
        results = []

        for task in pending_tasks:
            order = db.query(models.PaperOrder).filter(models.PaperOrder.id == task.order_id).first()
            if not order or order.state != "closed":
                task.last_error = f"Order {task.order_id} not found or not in closed state"
                task.updated_at = current_time
                failed_count += 1
                results.append({"order_id": task.order_id, "status": "FAILED", "reason": task.last_error})
                continue

            # Idempotency check: see if root lesson already exists (Section 041, 112, 123)
            existing_lesson = db.query(models.Lesson).filter(models.Lesson.related_trade_id == task.order_id).first()
            if existing_lesson:
                task.status = "COMPLETED"
                task.updated_at = current_time
                repaired_count += 1
                results.append({"order_id": task.order_id, "status": "ALREADY_EXISTS", "lesson_id": existing_lesson.id})
                continue

            try:
                lesson = cls._create_lesson(db, order, current_time, session_tag="REPAIR")
                if lesson:
                    task.status = "COMPLETED"
                    task.updated_at = current_time
                    repaired_count += 1
                    results.append({"order_id": task.order_id, "status": "COMPLETED", "lesson_id": lesson.id})
                else:
                    task.retry_count = (task.retry_count or 0) + 1
                    task.last_error = "Lesson creation returned None"
                    task.updated_at = current_time
                    if task.retry_count >= 5:
                        task.status = "FAILED"
                    failed_count += 1
                    results.append({"order_id": task.order_id, "status": "FAILED", "reason": task.last_error})
            except Exception as err:
                task.retry_count = (task.retry_count or 0) + 1
                task.last_error = str(err)
                task.updated_at = current_time
                if task.retry_count >= 5:
                    task.status = "FAILED"
                failed_count += 1
                results.append({"order_id": task.order_id, "status": "FAILED", "reason": str(err)})

        db.flush()
        return {
            "repaired_count": repaired_count,
            "failed_count": failed_count,
            "total_processed": len(pending_tasks),
            "tasks": results,
        }
