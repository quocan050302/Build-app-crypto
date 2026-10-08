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
        while True:
            try:
                await asyncio.sleep(1.5)
                await self.check_active_position()
            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as e:
                await asyncio.sleep(2.0)

exit_monitor = ExitMonitor()
