import time
import asyncio
from sqlalchemy.orm import Session
from database import SessionLocal
import models, crud
from paper_broker import PaperBroker
from services.collector_service import collector_service
from services.event_bus import event_bus

class ExitMonitor:
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

            closed_order = PaperBroker.process_price_tick(
                db=db,
                current_bid=ticker["bid"],
                current_ask=ticker["ask"]
            )

            if closed_order:
                # Update watch setup state to CLOSED
                if closed_order.setup_id:
                    watch_setup = db.query(models.WatchSetup).filter(models.WatchSetup.id == closed_order.setup_id).first()
                    if watch_setup:
                        watch_setup.state = "CLOSED"
                        db.commit()

                event_type = "trade.liquidated" if closed_order.exit_cause == "LIQUIDATED" else "trade.closed"
                event_bus.publish_event(
                    event_type=event_type,
                    aggregate_id=closed_order.id,
                    payload={
                        "trade_id": closed_order.id,
                        "direction": closed_order.direction,
                        "actual_exit": closed_order.actual_exit,
                        "realized_pnl": closed_order.realized_pnl_net,
                        "realized_r": closed_order.realized_r,
                        "exit_cause": closed_order.exit_cause
                    }
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
