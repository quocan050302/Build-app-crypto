import time
import json
import asyncio
import math
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from database import SessionLocal
import models, crud
from domain_calculator import calculate_risk_reward
from paper_broker import PaperBroker
from services.event_bus import event_bus
from services.collector_service import collector_service
from services.trade_lifecycle_service import TradeLifecycleService
from services.quote_validator import QuoteValidator
from services.clock import live_clock, IClock
from services.entry_guard_service import serialize_entry_mutation
from services.scalp_m1_types import load_effective_scalp_config
from services.runtime_profile_service import assert_not_observe_only

# Production Safety Invariant: Absolute zero live exchange order submissions allowed
LIVE_EXCHANGE_ORDERS_SENT: int = 0


def _validate_threshold(val: Any) -> Optional[float]:
    """Rule 029: Validate that threshold is a numeric, finite, positive float (> 0)."""
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
        if math.isfinite(f) and f > 0:
            return f
    except (TypeError, ValueError):
        pass
    return None


def resolve_executable_sizing_context(
    order: Any,
    current_equity: float,
    *,
    capital_mode: Optional[str] = None,
    order_cost_snap: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    V16.9 NHÓM 12, PHẦN 111 - 120: Resolve executable sizing context.
    Distinguishes requested sizing target, account safety budget, and executable budget.
    """
    snap = order_cost_snap or {}
    mode = capital_mode or snap.get("capital_mode") or "CURRENT_ACCOUNT_BALANCE"
    risk_pct = float(order.risk_pct or snap.get("risk_pct") or 0.25)

    if mode == "FIXED_INITIAL_CAPITAL":
        target_capital = float(snap.get("target_capital") or snap.get("capital_usdt") or (order.initial_risk_usdt / (risk_pct / 100.0) if order.initial_risk_usdt else current_equity))
        target_budget = round(target_capital * (risk_pct / 100.0), 4)

        # Account safety constraint: cannot risk more than current equity allows
        safety_max_risk = round(current_equity * (risk_pct / 100.0), 4)
        if current_equity < target_capital and target_budget > safety_max_risk:
            # Degraded account equity: cap target risk budget to safety budget
            executable_budget = safety_max_risk
            sizing_capital = current_equity
            adjustment_reason = "ACCOUNT_SAFETY_CAP"
        else:
            executable_budget = target_budget
            sizing_capital = target_capital
            adjustment_reason = "NONE"
    else:
        # CURRENT_ACCOUNT_BALANCE: target basis is current equity
        target_capital = current_equity
        target_budget = round(current_equity * (risk_pct / 100.0), 4)
        executable_budget = target_budget
        sizing_capital = current_equity
        adjustment_reason = "NONE"

    return {
        "capital_mode": mode,
        "target_capital": target_capital,
        "target_budget": target_budget,
        "account_capital_at_fill": current_equity,
        "executable_budget": executable_budget,
        "sizing_capital": sizing_capital,
        "adjustment_reason": adjustment_reason,
    }


class ExecutionCoordinator:
    """
    Singleton Execution Coordinator:
    - Evaluates armed pending orders against live bid/ask quotes and closed bars.
    - Uses QuoteValidator to ensure ticker validity and freshness (<=15s) before any fill.
    - Accurately supports MARKET (ask for LONG, bid for SHORT), LIMIT (touch limit), and STOP (breakout).
    - Authoritative re-validation of risk budget, margins, news blackout, and liquidation safety at execution time.
    - On fill: atomic state transition armed -> paper_open, increments today fills count (0/3), emits trade.opened event.
    - On rejection: marks order rejected without incrementing daily counters, emits order.rejected and REJECTED outbox.
    """
    def __init__(self):
        self._lock: Optional[asyncio.Lock] = None
        self._running = False

    @property
    def lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @serialize_entry_mutation
    def evaluate_orders_sync(
        self,
        db: Session,
        ticker_override: Optional[Dict[str, Any]] = None,
        clock: Optional[IClock] = None,
        now_ms: Optional[int] = None,
        slippage: float = 0.10
    ):
        """
        Synchronous evaluation of armed orders against quotes.
        Callable from background loop, synchronous test fixtures, and replay runner.
        """
        assert_not_observe_only(
            "ExecutionCoordinator.evaluate_orders_sync",
            counter_key="BLOCKED_FILL_ATTEMPTS",
        )
        c = clock or live_clock
        current_time = now_ms if now_ms is not None else c.now_ms()

        # 1. Fetch pending armed orders
        armed_orders = (
            db.query(models.PaperOrder)
            .filter(models.PaperOrder.state == "armed")
            .all()
        )
        if not armed_orders:
            return

        # Housekeeping: Expire pending armed orders that reached or exceeded deadline (Rules 036, 037, 038)
        # Pending orders expire even if quote is invalid or an active position already exists.
        remaining_armed = []
        for order in armed_orders:
            if order.expires_at and current_time >= order.expires_at:
                order.state = "expired"
                order.exit_cause = "EXPIRED"
                order.closed_at = current_time
                event_bus.publish_event(
                    event_type="setup.expired",
                    aggregate_id=order.id,
                    payload={"order_id": order.id, "reason": "Hết hạn lệnh armed", "expires_at": order.expires_at},
                    db=db,
                    occurred_at=current_time
                )
                if order.setup_id:
                    watch_setup = db.query(models.WatchSetup).filter(models.WatchSetup.id == order.setup_id).first()
                    if watch_setup:
                        watch_setup.state = "EXPIRED"
                        watch_setup.invalidation_reason = "Order expired in armed state"
                        watch_setup.updated_at = current_time
                db.commit()
            else:
                remaining_armed.append(order)

        if not remaining_armed:
            return

        # 2. Check and validate ticker via authoritative QuoteValidator
        raw_ticker = ticker_override if ticker_override is not None else collector_service.latest_ticker
        quote = QuoteValidator.validate_ticker(raw_ticker, now_ms=current_time)

        if not quote.is_valid:
            # Quote is invalid, non-finite, or stale: DO NOT fill!
            return

        current_bid = quote.bid
        current_ask = quote.ask

        # 3. Check existing active open position (invariant: max 1 active position)
        active_pos = crud.get_active_position(db)
        if active_pos:
            return

        from services.risk_settings_service import risk_settings_service
        from domain_calculator import calculate_risk_reward, CostAssumptions
        from services.cg01_types import resolve_slippage_usd

        global_settings = risk_settings_service.get_settings(db)

        for order in remaining_armed:
            # V14.4.2: Invalidate pending armed order if config version changed (no silent repricing)
            if getattr(order, 'config_version', 1) != global_settings.config_version:
                curr_cfg = getattr(order, 'config_version', 1)
                TradeLifecycleService.execute_reject(
                    db=db,
                    order=order,
                    reason=f"CONFIG_VERSION_CHANGED: Cấu hình rủi ro đã thay đổi (v{curr_cfg} -> v{global_settings.config_version}). Lệnh chờ đã bị hủy để bảo đảm snapshot.",
                    now_ms=current_time,
                    clock=c
                )
                if order.setup_id:
                    watch_setup = db.query(models.WatchSetup).filter(models.WatchSetup.id == order.setup_id).first()
                    if watch_setup:
                        watch_setup.state = "CANCELLED"
                        watch_setup.invalidation_reason = "CONFIG_VERSION_CHANGED"
                        watch_setup.updated_at = current_time
                db.commit()
                continue

            # V5.1 Authoritative Geometry Guard on armed order
            from domain_calculator import validate_price_geometry
            is_geom_valid, geom_err = validate_price_geometry(order.direction, order.planned_entry, order.stop_loss, order.take_profit)
            if not is_geom_valid:
                TradeLifecycleService.execute_reject(
                    db=db,
                    order=order,
                    reason=f"INVALID_PRICE_GEOMETRY: {geom_err}",
                    now_ms=current_time,
                    clock=c
                )
                continue

            # Resolve order slippage and costs from cost_snapshot (Rules 052, 060, 062)
            order_cost_snap = {}
            if order.cost_snapshot:
                try:
                    order_cost_snap = json.loads(order.cost_snapshot) if isinstance(order.cost_snapshot, str) else dict(order.cost_snapshot)
                except Exception:
                    order_cost_snap = {}

            # Rule 014, 027 - 032, 070: Resolve effective min_net_rr for this fill.
            # Priority: (1) min_net_rr_at_arm persisted in cost_snapshot at ARM time.
            #               If present, must be valid (> 0, finite). If invalid, reject order.
            #           (2) canonical scalp config from DB (honours user's /api/scalp/config setting).
            #               If missing key in snapshot, resolve config. If resolution fails, reject order.
            _rr_from_snap = order_cost_snap.get("min_net_rr_at_arm")
            if _rr_from_snap is not None:
                _valid_rr = _validate_threshold(_rr_from_snap)
                if _valid_rr is None:
                    TradeLifecycleService.execute_reject(
                        db=db,
                        order=order,
                        reason=f"INVALID_ORDER_THRESHOLD: min_net_rr_at_arm in snapshot is invalid ({_rr_from_snap})",
                        now_ms=current_time,
                        clock=c,
                    )
                    continue
                effective_fill_min_rr = _valid_rr
            else:
                try:
                    _scalp_cfg = load_effective_scalp_config(db)
                    _valid_cfg_rr = _validate_threshold(getattr(_scalp_cfg, "min_net_rr", None))
                    if _valid_cfg_rr is None:
                        TradeLifecycleService.execute_reject(
                            db=db,
                            order=order,
                            reason="CONFIG_RESOLUTION_ERROR: effective scalp config returned invalid min_net_rr",
                            now_ms=current_time,
                            clock=c,
                        )
                        continue
                    effective_fill_min_rr = _valid_cfg_rr
                except Exception as e:
                    TradeLifecycleService.execute_reject(
                        db=db,
                        order=order,
                        reason=f"CONFIG_RESOLUTION_ERROR: failed to load scalp config for legacy order fallback ({e})",
                        now_ms=current_time,
                        clock=c,
                    )
                    continue

            from services.plan_financial_context_service import resolve_execution_cost_context
            verified_cost_ctx = resolve_execution_cost_context(snapshot=order_cost_snap) if order_cost_snap else {"available": False}

            if verified_cost_ctx.get("available"):
                order_slip = float(verified_cost_ctx["entry_slippage_usd"])
                order_maker_fee = float(verified_cost_ctx["maker_fee_rate"])
                order_taker_fee = float(verified_cost_ctx["taker_fee_rate"])
            else:
                raw_order_slip = order_cost_snap.get("slippage_usd") if order_cost_snap else None
                if raw_order_slip is not None:
                    order_slip = resolve_slippage_usd(raw_order_slip, default=0.10)
                elif slippage != 0.10:
                    order_slip = resolve_slippage_usd(slippage, default=0.10)
                else:
                    order_slip = resolve_slippage_usd(getattr(global_settings, "slippage_usd", 0.10), default=0.10)

                order_maker_fee = float(order_cost_snap.get("maker_fee_rate", 0.0002)) if order_cost_snap else 0.0002
                order_taker_fee = float(order_cost_snap.get("taker_fee_rate", 0.0004)) if order_cost_snap else 0.0004

            order_costs = CostAssumptions(
                maker_fee_rate=order_maker_fee,
                taker_fee_rate=order_taker_fee,
                slippage_usd=order_slip,
            )

            # Evaluate entry trigger
            triggered = False
            fill_price = 0.0
            order_type = (order.order_type or "MARKET").upper()

            if order_type == "MARKET":
                # Immediate market trigger: Ask for LONG, Bid for SHORT + directional slippage
                triggered = True
                fill_price = round(current_ask + order_slip, 2) if order.direction == "LONG" else round(current_bid - order_slip, 2)

            elif order_type == "LIMIT":
                if order.direction == "LONG":
                    # BUY LIMIT: Ask <= limit
                    if current_ask <= order.planned_entry:
                        triggered = True
                        fill_price = order.planned_entry
                elif order.direction == "SHORT":
                    # SELL LIMIT: Bid >= limit
                    if current_bid >= order.planned_entry:
                        triggered = True
                        fill_price = order.planned_entry

            elif order_type == "STOP":
                if order.direction == "LONG":
                    # BUY STOP: Ask >= stop (breakout)
                    if current_ask >= order.planned_entry:
                        triggered = True
                        fill_price = round(current_ask + order_slip, 2)
                elif order.direction == "SHORT":
                    # SELL STOP: Bid <= stop (breakout)
                    if current_bid <= order.planned_entry:
                        triggered = True
                        fill_price = round(current_bid - order_slip, 2)

            else:
                TradeLifecycleService.execute_reject(
                    db=db,
                    order=order,
                    reason=f"Loại lệnh không được hỗ trợ: {order.order_type}",
                    now_ms=current_time,
                    clock=c
                )
                continue

            if not triggered:
                continue

            # Re-verify trading policy (daily max 3 fills, NY window, slot reservation)
            from services.trading_policy_service import TradingPolicyService
            from datetime import datetime, timezone
            policy_eval = TradingPolicyService.evaluate_entry_policy(
                db=db,
                order_type_or_symbol=order.instrument or "XAUUSDT",
                clock=c,
                now_ms=current_time,
                strategy_family=getattr(order, "strategy_family", "STANDARD_SMC") or "STANDARD_SMC"
            )
            if not policy_eval["allowed"]:
                TradeLifecycleService.execute_reject(
                    db=db,
                    order=order,
                    reason=f"POLICY_GATE: {policy_eval['reason_code']} - {policy_eval['reason_message']}",
                    now_ms=current_time,
                    clock=c
                )
                continue

            # Re-verify execution guards
            audit = crud.get_or_create_today_audit(db, clock=c)
            can_open, block_reason = PaperBroker.can_open_position(db, order.initial_risk_usdt, now_ms=current_time, clock=c)
            if not can_open:
                TradeLifecycleService.execute_reject(
                    db=db,
                    order=order,
                    reason=f"Execution guard check failed: {block_reason}",
                    now_ms=current_time,
                    clock=c
                )
                continue

            # V16.9 Resolve executable sizing context (target budget, safety cap, executable budget)
            sizing_ctx = resolve_executable_sizing_context(
                order=order,
                current_equity=audit.current_equity,
                order_cost_snap=order_cost_snap,
            )

            # V17.8.0.1: Recover frozen stop-placement and protective-distance metadata from ARM snapshot
            sp_snap = order_cost_snap.get("stop_placement") if isinstance(order_cost_snap, dict) else None
            uni_snap = order_cost_snap.get("unicorn_evidence") if isinstance(order_cost_snap, dict) else None
            has_explicit_min_prot = isinstance(order_cost_snap, dict) and "min_protective_distance" in order_cost_snap
            raw_min_prot = (
                order_cost_snap.get("min_protective_distance")
                if has_explicit_min_prot
                else (sp_snap.get("protective_distance") if isinstance(sp_snap, dict) else None)
            )
            fill_ev_meta = {}
            if isinstance(order_cost_snap, dict):
                if "v18_structural_intelligence_enabled" in order_cost_snap:
                    fill_ev_meta["v18_structural_intelligence_enabled"] = order_cost_snap["v18_structural_intelligence_enabled"]
                if "stop_mode_at_arm" in order_cost_snap:
                    fill_ev_meta["stop_mode_at_arm"] = order_cost_snap["stop_mode_at_arm"]
                if "stop_algorithm_version" in order_cost_snap:
                    fill_ev_meta["stop_algorithm_version"] = order_cost_snap["stop_algorithm_version"]

            # Pre-check FILL quote-distance and stop-breach guard before calculator rejection
            # so a breached stop at FILL is deterministically rejected with INVALID_GEOMETRY_STOP_BREACHED
            from services.entry_guard_service import (
                EntryGuardRequest, evaluate_entry_guard, DATA_UNAVAILABLE, _check_geometry,
            )
            from services.entry_decision_service import EntryDecisionService
            from services.setup_quality_service import build_plan_fingerprint

            pre_geom_check = _check_geometry(EntryGuardRequest(
                stage="FILL",
                now_ms=current_time,
                symbol=order.instrument or "XAUUSDT",
                direction=order.direction,
                execution_mode=getattr(order, "execution_mode", None) or "AUTO",
                origin=getattr(order, "origin", None) or "UNKNOWN",
                planned_entry=fill_price,
                stop_loss=order.stop_loss,
                take_profit=order.take_profit,
                validated_quote=quote,
                min_protective_distance=raw_min_prot,
                unicorn_evidence=uni_snap,
                stop_decision=sp_snap,
                evidence=fill_ev_meta,
            ))
            if pre_geom_check.state != "PASS" and pre_geom_check.code in (
                "INVALID_GEOMETRY_STOP_BREACHED",
                "STOP_TOO_CLOSE_TO_EXECUTABLE_QUOTE",
                "UNICORN_INVALIDATED",
                "INVALID_PROTECTIVE_DISTANCE",
                "MISSING_PROTECTIVE_DISTANCE",
            ):
                reject_reason = f"ENTRY_GUARD_BLOCKED: {pre_geom_check.code} - {pre_geom_check.message_vi}"
                gate_trace = {
                    "can_proceed": False,
                    "blocking_reasons": [pre_geom_check.message_vi],
                    "warning_messages": [],
                    "advisory_notes": [],
                    "lessons_retrieved_snapshot": [],
                    "entry_gate": {
                        "allowed": False,
                        "stage": "FILL",
                        "evaluated_at_ms": current_time,
                        "reason_codes": [pre_geom_check.code],
                    },
                }
                EntryDecisionService.record_stage_decision(order, "BEFORE_FILL", gate_trace, now_ms=current_time)
                TradeLifecycleService.execute_reject(
                    db=db,
                    order=order,
                    reason=reject_reason,
                    now_ms=current_time,
                    clock=c,
                )
                continue

            # Authoritative execution re-check with actual fill price
            # Must preserve the armed quantity (no silent size increase) and validate against budget/netRR
            calc_budget = calculate_risk_reward(
                direction=order.direction,
                entry=fill_price,
                sl=order.stop_loss,
                tp=order.take_profit,
                capital=sizing_ctx["sizing_capital"],
                risk_pct=order.risk_pct or 0.25,
                min_net_rr=effective_fill_min_rr,
                entry_has_slippage=True,
                costs=order_costs,
                leverage=order.leverage or 5,
                margin_mode=order.margin_mode or "ISOLATED",
                phase="FILLED_ESTIMATE",
                setup_id=order.setup_id,
                setup_instance_id=getattr(order, "setup_instance_id", None),
                revision=getattr(order, "config_version", 1) or 1,
                now_ms=current_time
            )

            if not calc_budget.can_execute:
                TradeLifecycleService.execute_reject(
                    db=db,
                    order=order,
                    reason=f"Calculation re-check failed: {calc_budget.skip_reason}",
                    now_ms=current_time,
                    clock=c
                )
                continue

            resize_policy = getattr(order, "resize_policy", None) or "PRESERVE_OR_DOWNSIZE"
            if order.quantity is not None and order.quantity > 0:
                if resize_policy == "STRICT_FIXED":
                    qty_to_use = order.quantity
                else:
                    qty_to_use = min(order.quantity, calc_budget.quantity)
            else:
                qty_to_use = calc_budget.quantity

            calc = calculate_risk_reward(
                direction=order.direction,
                entry=fill_price,
                sl=order.stop_loss,
                tp=order.take_profit,
                capital=sizing_ctx["sizing_capital"],
                risk_pct=order.risk_pct or 0.25,
                min_net_rr=effective_fill_min_rr,
                quantity_override=qty_to_use,
                entry_has_slippage=True,
                costs=order_costs,
                leverage=order.leverage or 5,
                margin_mode=order.margin_mode or "ISOLATED",
                phase="FILLED_ESTIMATE",
                setup_id=order.setup_id,
                setup_instance_id=getattr(order, "setup_instance_id", None),
                revision=getattr(order, "config_version", 1) or 1,
                now_ms=current_time
            )

            if not calc.can_execute:
                TradeLifecycleService.execute_reject(
                    db=db,
                    order=order,
                    reason=f"Calculation re-check failed: {calc.skip_reason}",
                    now_ms=current_time,
                    clock=c
                )
                continue

            # V14.2 Common Entry Guard (stage FILL): re-checks news blackout, governed lessons
            # (BEFORE_FILL), session policy, account, quote and feed with the CURRENT state, using the
            # frozen order levels and the actual fill price. The order itself is excluded from
            # armed-order checks (current_order_id) and the WatchSetup is NOT required to be READY.
            try:
                plan_fp = build_plan_fingerprint({
                    "direction": order.direction, "planned_entry": order.planned_entry,
                    "stop_loss": order.stop_loss, "take_profit": order.take_profit,
                })
            except Exception:
                plan_fp = None

            guard = evaluate_entry_guard(db, EntryGuardRequest(
                stage="FILL",
                now_ms=current_time,
                symbol=order.instrument or "XAUUSDT",
                direction=order.direction,
                execution_mode=getattr(order, "execution_mode", None) or "AUTO",
                origin=getattr(order, "origin", None) or "UNKNOWN",
                planned_entry=fill_price,
                stop_loss=order.stop_loss,
                take_profit=order.take_profit,
                calc_result=calc,
                validated_quote=quote,
                strategy_family=getattr(order, "strategy_family", "STANDARD_SMC") or "STANDARD_SMC",
                order_type=order_type,
                timeframe=getattr(order, "timeframe", "15M"),
                plan_kind="ORDER",
                setup_id=order.setup_id,
                setup_instance_id=getattr(order, "setup_instance_id", None),
                plan_fingerprint=plan_fp,
                risk_config_version=getattr(order, "config_version", None),
                order_id=order.id,
                current_order_id=order.id,
                order_state=order.state,
                order_expires_at_ms=order.expires_at,
                trigger_hit=True,
                session_instance_id=order.session_instance_id,
                clock=c,
                min_protective_distance=raw_min_prot,
                unicorn_evidence=uni_snap,
                stop_decision=sp_snap,
                evidence=fill_ev_meta,
            ))

            if not guard.allowed:
                transient_codes = {
                    "QUOTE_STALE", "QUOTE_INVALID", "QUOTE_UNAVAILABLE",
                    "EXECUTION_FEED_DEGRADED", "TRIGGER_NOT_HIT", DATA_UNAVAILABLE,
                }
                permanent = [
                    b for b in guard.blockers if b["code"] not in transient_codes
                ]
                if not permanent:
                    # Quote/feed/data not usable right now: do NOT fill, keep the order pending
                    # (no event, no reject, no write) until the next valid tick or expiry.
                    continue
                first = permanent[0]
                if first["code"].startswith("LESSON"):
                    reject_reason = f"LESSON_RULE_BLOCKED: {first['message_vi']}"
                elif first["code"] == "NEWS_BLACKOUT":
                    reject_reason = f"NEWS_BLACKOUT: {first['message_vi']}"
                else:
                    reject_reason = f"ENTRY_GUARD_BLOCKED: {first['code']} - {first['message_vi']}"
                gate_trace = dict(guard.lesson_result or {
                    "can_proceed": False, "blocking_reasons": [first["message_vi"]],
                    "warning_messages": [], "advisory_notes": [], "lessons_retrieved_snapshot": [],
                })
                gate_trace["entry_gate"] = guard.summary()
                EntryDecisionService.record_stage_decision(order, "BEFORE_FILL", gate_trace, now_ms=current_time)
                TradeLifecycleService.execute_reject(
                    db=db,
                    order=order,
                    reason=reject_reason,
                    now_ms=current_time,
                    clock=c
                )
                continue

            fill_trace = dict(guard.lesson_result or {"can_proceed": True})
            fill_trace["entry_gate"] = guard.summary()
            EntryDecisionService.record_stage_decision(order, "BEFORE_FILL", fill_trace, now_ms=current_time)

            # ATOMIC COMMIT: Open Position via unified lifecycle service (preserving original execution mode)
            effective_source = getattr(order, "execution_mode", "AUTO") or "AUTO"
            TradeLifecycleService.execute_fill(
                db=db,
                order=order,
                fill_price=fill_price,
                calc_result=calc,
                source=effective_source,
                now_ms=current_time,
                clock=c,
                sizing_context=sizing_ctx,
            )
            # Max 1 position enforced, break after filling first triggered order
            break

    async def evaluate_armed_orders(self):
        async with self.lock:
            db: Session = SessionLocal()
            try:
                self.evaluate_orders_sync(db)
            finally:
                db.close()

    async def run_coordinator_loop(self):
        assert_not_observe_only(
            "ExecutionCoordinator.run_coordinator_loop",
            counter_key="BLOCKED_WORKER_START_ATTEMPTS",
        )
        self._running = True
        while self._running:
            try:
                await asyncio.sleep(1.5)
                # Single execution owner: if execution_consumer is actively processing stream quotes,
                # yield to consumer so coordinator acts as watchdog / fallback only.
                from services.execution_consumer import execution_consumer
                now_ms = int(time.time() * 1000)
                if execution_consumer._running and (now_ms - execution_consumer.last_processed_quote_ts < 5000):
                    continue

                await self.evaluate_armed_orders()
            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as e:
                await asyncio.sleep(2.0)

execution_coordinator = ExecutionCoordinator()
