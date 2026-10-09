import time
import asyncio
import logging
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from database import SessionLocal
import models, crud
from services.quote_validator import CanonicalQuote, QuoteValidator
from services.trade_lifecycle_service import TradeLifecycleService
from services.clock import live_clock, IClock

logger = logging.getLogger(__name__)

class ExecutionConsumer:
    """
    Ordered Execution Consumer (V7.1):
    - Receives every validated quote from WebSocket or fresh REST fallback.
    - Guarantees sequential, ordered evaluation BEFORE any UI throttling or coalescing.
    - Prioritizes active exit monitoring:
        * LONG exits on executable Bid
        * SHORT exits on executable Ask
        * Touching TP/SL closes on the first qualifying quote immediately.
    - Enforces armed orders evaluation:
        * Strictly max 1 active open position
        * Re-validates risk config version, margin, and news blackout
    - Queue bounded with lag tracking to prevent memory leaks and detect backpressure.
    """
    def __init__(self, max_queue_size: int = 500):
        self.queue: asyncio.Queue[CanonicalQuote] = asyncio.Queue(maxsize=max_queue_size)
        self._running = False
        self.processed_count = 0
        self.dropped_count = 0
        self.last_processed_quote_ts = 0
        self.last_eval_duration_ms = 0.0
        self.is_lagging = False
        self.last_lag_warn_ts = 0

    @property
    def queue_depth(self) -> int:
        return self.queue.qsize()

    def enqueue_quote(self, quote: CanonicalQuote) -> bool:
        """
        Enqueues a canonical quote into the ordered execution stream.
        If queue is near capacity, drops non-executable quote or enters DEGRADED state.
        """
        if not quote.is_valid:
            return False

        try:
            self.queue.put_nowait(quote)
            return True
        except asyncio.QueueFull:
            self.dropped_count += 1
            self.is_lagging = True
            now = time.time()
            if now - self.last_lag_warn_ts > 5.0:
                self.last_lag_warn_ts = now
                logger.warning(f"Execution consumer queue is full ({self.queue.qsize()}), quote dropped!")
            return False

    def evaluate_quote_sync(
        self,
        db: Session,
        quote: CanonicalQuote,
        clock: Optional[IClock] = None,
        now_ms: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Synchronous evaluation of active position exits and pending armed orders for a single quote.
        Can be called directly from test harnesses or scenario runner.
        """
        c = clock or live_clock
        current_time = now_ms if now_ms is not None else (quote.received_at_ms or c.now_ms())
        result = {"exit_processed": False, "entry_processed": False}

        # 1. Active open position takes precedence
        active_pos = crud.get_active_position(db)
        if active_pos and active_pos.state == "paper_open":
            closed_order = TradeLifecycleService.process_exit_tick(
                db=db,
                current_bid=quote.bid,
                current_ask=quote.ask,
                now_ms=current_time,
                clock=c
            )
            if closed_order:
                result["exit_processed"] = True
                result["closed_order_id"] = closed_order.id
                result["exit_cause"] = closed_order.exit_cause
            return result

        # 2. Evaluate armed orders if no active position exists and not severely lagging
        if not self.is_lagging:
            from services.execution_coordinator import execution_coordinator
            execution_coordinator.evaluate_orders_sync(
                db=db,
                ticker_override=quote.to_dict(),
                clock=c,
                now_ms=current_time
            )
            result["entry_processed"] = True

        return result

    async def run_consumer_loop(self):
        """
        Main execution loop. Processes quotes sequentially with dedicated database sessions.
        """
        self._running = True
        logger.info("ExecutionConsumer loop started.")

        while self._running:
            try:
                quote = await self.queue.get()
                t0 = time.perf_counter()

                # Process quote
                db: Session = SessionLocal()
                try:
                    self.evaluate_quote_sync(db, quote)
                except Exception as e:
                    logger.error(f"Error evaluating quote {quote.symbol}: {e}", exc_info=True)
                finally:
                    db.close()
                    self.queue.task_done()

                t_diff = (time.perf_counter() - t0) * 1000.0
                self.last_eval_duration_ms = round(t_diff, 2)
                self.last_processed_quote_ts = quote.received_at_ms
                self.processed_count += 1

                # Update lag status
                q_size = self.queue.qsize()
                if q_size > 350:
                    self.is_lagging = True
                elif q_size < 50:
                    self.is_lagging = False

            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as e:
                logger.error(f"ExecutionConsumer loop error: {e}", exc_info=True)
                await asyncio.sleep(0.1)

        logger.info("ExecutionConsumer loop terminated.")

execution_consumer = ExecutionConsumer()
