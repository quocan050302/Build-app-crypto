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
        self._lock = asyncio.Lock()
        self._running = False

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
                db.commit()
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

            # Re-verify execution guards
            audit = crud.get_or_create_today_audit(db, clock=c)
            can_open, block_reason = PaperBroker.can_open_position(db, order.initial_risk_usdt, now_ms=current_time)
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
        async with self._lock:
            db: Session = SessionLocal()
            try:
                self.evaluate_orders_sync(db)
            finally:
                db.close()

    async def run_coordinator_loop(self):
        self._running = True
        while True:
            try:
                await asyncio.sleep(1.5)
                await self.evaluate_armed_orders()
            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as e:
                await asyncio.sleep(2.0)

execution_coordinator = ExecutionCoordinator()
