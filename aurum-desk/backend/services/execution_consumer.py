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
        self.last_queue_wait_ms = 0.0
        self.is_lagging = False
        self.has_execution_gap = False
        self.gap_start_ms: Optional[int] = None
        self.gap_end_ms: Optional[int] = None
        self.last_lag_warn_ts = 0

    @property
    def queue_depth(self) -> int:
        return self.queue.qsize()

    def reset_execution_gap(self):
        """Clears execution gap flag after audit / historical reconciliation."""
        self.has_execution_gap = False
        self.gap_start_ms = None
        self.gap_end_ms = None
        self.is_lagging = False

    def enqueue_quote(self, quote: CanonicalQuote) -> bool:
        """
        Enqueues a canonical quote into the ordered execution stream.
        If queue is full, enters DEGRADED state and marks execution gap.
        """
        if not quote.is_valid:
            return False

        try:
            self.queue.put_nowait(quote)
            return True
        except asyncio.QueueFull:
            self.dropped_count += 1
            self.is_lagging = True
            self.has_execution_gap = True
            now_ms = quote.received_at_ms or int(time.time() * 1000)
            if self.gap_start_ms is None:
                self.gap_start_ms = now_ms
            self.gap_end_ms = now_ms

            now = time.time()
            if now - self.last_lag_warn_ts > 5.0:
                self.last_lag_warn_ts = now
                logger.warning(
                    f"Execution consumer queue full ({self.queue.qsize()}), quote dropped! Marked EXECUTION_GAP ({self.dropped_count} dropped)."
                )
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
        Re-validates quote freshness according to actual processing time (proc_now_ms),
        preventing stale backlog quotes from filling new entry orders.
        """
        c = clock or live_clock
        proc_now_ms = now_ms if now_ms is not None else c.now_ms()
        result = {"exit_processed": False, "entry_processed": False, "is_stale": False}

        # Track queue wait time and quote age relative to processing clock
        quote_ts = quote.exchange_ts_ms or quote.received_at_ms or proc_now_ms
        quote_age_ms = max(0, proc_now_ms - quote_ts)
        wait_in_queue_ms = max(0, proc_now_ms - quote.received_at_ms) if quote.received_at_ms else 0
        self.last_queue_wait_ms = round(wait_in_queue_ms, 2)

        # A quote in backlog that is older than 15s relative to processing time cannot open new orders
        is_quote_fresh_for_entry = (quote_age_ms <= 15000)

        # 1. Active open position takes absolute precedence (evaluated even during backlog)
        active_pos = crud.get_active_position(db)
        if active_pos and active_pos.state == "paper_open":
            closed_order = TradeLifecycleService.process_exit_tick(
                db=db,
                current_bid=quote.bid,
                current_ask=quote.ask,
                now_ms=proc_now_ms,
                clock=c
            )
            if closed_order:
                result["exit_processed"] = True
                result["closed_order_id"] = closed_order.id
                result["exit_cause"] = closed_order.exit_cause
            return result

        # 2. Evaluate armed orders if no active position exists, not lagging, no gap, and quote is fresh
        if not self.is_lagging and not self.has_execution_gap and is_quote_fresh_for_entry:
            from services.execution_coordinator import execution_coordinator
            execution_coordinator.evaluate_orders_sync(
                db=db,
                ticker_override=quote.to_dict(),
                clock=c,
                now_ms=proc_now_ms
            )
            result["entry_processed"] = True
        else:
            if not is_quote_fresh_for_entry:
                result["is_stale"] = True
                result["rejection_reason"] = f"BACKLOG_QUOTE_STALE ({quote_age_ms}ms > 15000ms)"
            elif self.has_execution_gap or self.is_lagging:
                result["rejection_reason"] = "EXECUTION_GAP_ACTIVE"

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
                self.last_processed_quote_ts = int(time.time() * 1000)
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
