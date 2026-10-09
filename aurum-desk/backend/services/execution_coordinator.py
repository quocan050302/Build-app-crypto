import time
import asyncio
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

        global_settings = risk_settings_service.get_settings(db)

        for order in armed_orders:
            # Check expiry
            if order.expires_at and current_time > order.expires_at:
                order.state = "expired"
                order.exit_cause = "EXPIRED"
                order.closed_at = current_time
                event_bus.publish_event(
                    event_type="setup.expired",
                    aggregate_id=order.id,
                    payload={"order_id": order.id, "reason": "Hết hạn lệnh armed"},
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
                continue

            # Revalidate if config version changed
            if getattr(order, 'config_version', 1) != global_settings.config_version:
                account_state = crud.get_account_status(db, clock=c)
                equity = account_state["current_equity"] if account_state else 1000.0

                is_quota_order = (getattr(order, 'strategy_family', 'STANDARD_SMC') == "NY_QUOTA_PAPER") or (getattr(order, 'risk_profile', 'STANDARD') == "QUOTA")
                eff_risk_pct = min(global_settings.risk_pct, 0.10) if is_quota_order else global_settings.risk_pct

                calc_res = calculate_risk_reward(
                    direction=order.direction,
                    planned_entry=order.planned_entry,
                    stop_loss=order.stop_loss,
                    take_profit=order.take_profit,
                    capital=equity,
                    risk_pct=eff_risk_pct,
                    leverage=global_settings.requested_leverage,
                    margin_mode=global_settings.margin_mode
                )

                if not calc_res.can_execute:
                    TradeLifecycleService.execute_reject(
                        db=db,
                        order=order,
                        reason=f"RISK_SETTINGS_INVALIDATED: {calc_res.skip_reason or calc_res.invalid_reason}",
                        now_ms=current_time,
                        clock=c
                    )
                    continue

                # Amend order
                order.quantity = calc_res.quantity
                order.leverage = calc_res.leverage
                order.margin_mode = calc_res.margin_mode
                order.risk_pct = calc_res.effective_risk_pct
                order.initial_risk_usdt = calc_res.net_risk_usdt
                order.gross_rr = calc_res.gross_rr
                order.estimated_net_rr = calc_res.net_rr
                order.estimated_liquidation = calc_res.estimated_liquidation
                order.initial_margin = calc_res.initial_margin_usdt
                order.config_version = global_settings.config_version

                if getattr(order, 'setup_instance_id', None):
                    watch = db.query(models.WatchSetup).filter(models.WatchSetup.setup_instance_id == order.setup_instance_id).first()
                    if watch:
                        watch.quantity = calc_res.quantity
                        watch.leverage = calc_res.leverage
                        watch.margin_mode = calc_res.margin_mode
                        watch.risk_pct = calc_res.effective_risk_pct
                        watch.risk_usdt = calc_res.net_risk_usdt
                        watch.gross_rr = calc_res.gross_rr
                        watch.net_rr = calc_res.net_rr
                        watch.estimated_liquidation = calc_res.estimated_liquidation
                        watch.config_version = global_settings.config_version
                        watch.updated_at = current_time

                db.commit()

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

            # Evaluate entry trigger
            triggered = False
            fill_price = 0.0
            order_type = (order.order_type or "MARKET").upper()

            if order_type == "MARKET":
                # Immediate market trigger: Ask for LONG, Bid for SHORT + directional slippage
                triggered = True
                fill_price = round(current_ask + slippage, 2) if order.direction == "LONG" else round(current_bid - slippage, 2)

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
                        fill_price = round(current_ask + slippage, 2)
                elif order.direction == "SHORT":
                    # SELL STOP: Bid <= stop (breakout)
                    if current_bid <= order.planned_entry:
                        triggered = True
                        fill_price = round(current_bid - slippage, 2)

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
            current_dt = datetime.fromtimestamp(current_time / 1000.0, tz=timezone.utc)
            policy_eval = TradingPolicyService.evaluate_entry_policy(db, order.instrument or "XAUUSDT", current_dt)
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

            # Authoritative execution re-check with actual fill price
            calc = calculate_risk_reward(
                direction=order.direction,
                entry=fill_price,
                sl=order.stop_loss,
                tp=order.take_profit,
                capital=audit.current_equity,
                risk_pct=order.risk_pct or 0.25,
                entry_has_slippage=True,
                leverage=order.leverage or 5,
                margin_mode=order.margin_mode or "ISOLATED"
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

            # ATOMIC COMMIT: Open Position via unified lifecycle service
            TradeLifecycleService.execute_fill(
                db=db,
                order=order,
                fill_price=fill_price,
                calc_result=calc,
                source="AUTO",
                now_ms=current_time,
                clock=c
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
