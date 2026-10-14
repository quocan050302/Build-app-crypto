"""backend/services/forward_repair_worker.py

V14.4.16: Runtime Background Worker for Forward Membership Repair
Guarantees:
- Single in-flight batch execution per worker.
- Non-blocking event loop execution via asyncio.to_thread.
- Dedicated thread-local SessionLocal with guaranteed closure in finally blocks.
- Graceful shutdown awaiting active in-flight task drain up to timeout.
- Pure membership/task mutation: zero financial side effects.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

from database import SessionLocal
from services.forward_paper_service import process_due_forward_repairs

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerShutdownResult:
    status: str  # "COMPLETE" | "TIMED_OUT" | "NO_ACTIVE_WORKER"
    in_flight_done: bool
    reason: Optional[str] = None


class ForwardRepairWorker:
    """Production runtime background worker for processing due forward membership repairs."""

    def __init__(self):
        self._running: bool = False
        self._active_worker_task: Optional[asyncio.Task] = None

    @property
    def is_running(self) -> bool:
        return self._running

    def process_once(
        self,
        db: Optional[Session] = None,
        now_ms: Optional[int] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Executes a single bounded batch of due repairs with own SessionLocal if db is None."""
        owns_session = False
        if db is None:
            db = SessionLocal()
            owns_session = True

        try:
            return process_due_forward_repairs(db=db, now_ms=now_ms, limit=limit)
        finally:
            if owns_session:
                db.close()

    async def run_repair_loop(self, poll_interval_s: float = 3.0):
        """Continuous background repair loop.
        Dispatches synchronous DB batch processing to a worker thread via asyncio.to_thread
        to keep the FastAPI event loop completely non-blocking (PHẦN 026, 027).
        """
        self._running = True
        logger.info("ForwardRepairWorker background loop started.")
        while self._running:
            try:
                def _worker():
                    return self.process_once(limit=50)

                worker_task = asyncio.create_task(asyncio.to_thread(_worker))
                self._active_worker_task = worker_task
                try:
                    await worker_task
                finally:
                    self._active_worker_task = None
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in forward repair worker loop: {e}", exc_info=True)

            try:
                await asyncio.sleep(poll_interval_s)
            except asyncio.CancelledError:
                break

    async def shutdown(self, timeout_s: float = 5.0) -> WorkerShutdownResult:
        """Gracefully stops the background loop and awaits active worker completion (PHẦN 029, 030)."""
        self._running = False
        task = self._active_worker_task
        if task and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
                return WorkerShutdownResult(status="COMPLETE", in_flight_done=True)
            except asyncio.TimeoutError:
                return WorkerShutdownResult(
                    status="TIMED_OUT", in_flight_done=False, reason="Worker in-flight timeout"
                )
            except Exception as e:
                return WorkerShutdownResult(status="COMPLETE", in_flight_done=True, reason=str(e))
        return WorkerShutdownResult(status="NO_ACTIVE_WORKER", in_flight_done=True)

    def stop(self):
        """Signals the worker loop to stop launching new batches."""
        self._running = False


forward_repair_worker = ForwardRepairWorker()
