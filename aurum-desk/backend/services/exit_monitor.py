import time
import asyncio
from sqlalchemy.orm import Session
from database import SessionLocal
import models, crud
from services.collector_service import collector_service
from services.trade_lifecycle_service import TradeLifecycleService

class ExitMonitor:
    """
    Background Exit Monitor:
    - Periodically checks the single active open paper position against latest live ticker.
    - Evaluates TP, SL, and Liquidation.
    - Delegates to TradeLifecycleService.process_exit_tick for atomic state transition,
      audit recording, lesson creation, domain event emission, and outbox notification.
    """
    def __init__(self):
        self._running = False

    async def check_active_position(self):
        db: Session = SessionLocal()
        try:
            active_pos = crud.get_active_position(db)
            if not active_pos or active_pos.state != "paper_open":
                return

            ticker = collector_service.latest_ticker
            if not ticker or not ticker.get("bid") or not ticker.get("ask"):
                return

            TradeLifecycleService.process_exit_tick(
                db=db,
                current_bid=ticker["bid"],
                current_ask=ticker["ask"]
            )
        finally:
            db.close()

    async def run_exit_monitor_loop(self):
        self._running = True
        while self._running:
            try:
                await asyncio.sleep(1.5)
                # Single execution owner: if execution_consumer is actively monitoring exits on quotes,
                # yield to consumer so exit_monitor acts as watchdog / fallback only.
                from services.execution_consumer import execution_consumer
                now_ms = int(time.time() * 1000)
                if execution_consumer._running and (now_ms - execution_consumer.last_processed_quote_ts < 5000):
                    continue

                await self.check_active_position()
            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as e:
                await asyncio.sleep(2.0)

exit_monitor = ExitMonitor()
