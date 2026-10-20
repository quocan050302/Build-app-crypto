"""backend/services/lesson_repair_worker.py

V16.39.3 Sections 101 - 129:
Runtime background worker for processing due lesson repair tasks.
Guarantees:
- Single in-flight batch execution per worker.
- Non-blocking event loop execution via asyncio.to_thread.
- Dedicated thread-local SessionLocal with guaranteed commit/rollback/closure in finally blocks.
- Graceful shutdown awaiting active in-flight task drain up to timeout.
- Pure lesson artifact & task mutation: zero financial side effects, zero order accounting mutation.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

from database import SessionLocal
from services.trade_lifecycle_service import TradeLifecycleService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LessonWorkerShutdownResult:
    status: str  # "COMPLETE" | "TIMED_OUT" | "NO_ACTIVE_WORKER"
    in_flight_done: bool
    reason: Optional[str] = None


class LessonRepairWorker:
    """Production runtime background worker for processing pending lesson repairs."""

    def __init__(self):
        self._running: bool = False
        self._active_worker_task: Optional[asyncio.Task] = None
        self._last_batch_at_ms: Optional[int] = None
        self._total_processed_count: int = 0
        self._total_repaired_count: int = 0
        self._total_failed_count: int = 0
        self._last_error: Optional[str] = None

    @property
    def is_running(self) -> bool:
        return self._running

    def get_status(self, db: Optional[Session] = None) -> Dict[str, Any]:
        """Returns health and status of the worker (Rule 125, 126)."""
        pending_count = None
        if db is not None:
            try:
                import models
                pending_count = db.query(models.LessonRepairTask).filter(models.LessonRepairTask.status == "PENDING").count()
            except Exception:
                pass
        return {
            "is_running": self._running,
            "last_batch_at_ms": self._last_batch_at_ms,
            "total_processed_count": self._total_processed_count,
            "total_repaired_count": self._total_repaired_count,
            "total_failed_count": self._total_failed_count,
            "pending_count": pending_count,
            "last_error": self._last_error,
        }

    def process_once(
        self,
        db: Optional[Session] = None,
        now_ms: Optional[int] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """
        Executes a single bounded batch of lesson repairs.
        Uses own SessionLocal with explicit commit if db is None (Rule 107, 108).
        """
        owns_session = False
        if db is None:
            db = SessionLocal()
            owns_session = True

        try:
            res = TradeLifecycleService.repair_pending_lessons(db=db, now_ms=now_ms, limit=limit)
            if owns_session:
                db.commit()

            self._last_batch_at_ms = now_ms or int(time.time() * 1000)
            self._total_processed_count += res.get("total_processed", 0)
            self._total_repaired_count += res.get("repaired_count", 0)
            self._total_failed_count += res.get("failed_count", 0)
            return res
        except Exception as e:
            if owns_session:
                db.rollback()
            self._last_error = str(e)
            logger.error(f"Error in lesson repair process_once: {e}", exc_info=True)
            raise
        finally:
            if owns_session:
                db.close()

    def run_once(self, db: Optional[Session] = None, now_ms: Optional[int] = None, limit: int = 20) -> Dict[str, Any]:
        """Alias for process_once for contract compatibility."""
        return self.process_once(db=db, now_ms=now_ms, limit=limit)

    async def run_repair_loop(self, poll_interval_s: float = 3.0):
        """
        Continuous background repair loop (Rule 102, 103, 110).
        Dispatches synchronous DB batch processing to a worker thread via asyncio.to_thread
        to keep the FastAPI event loop completely non-blocking.
        """
        self._running = True
        logger.info("LessonRepairWorker background loop started.")
        while self._running:
            try:
                def _worker():
                    return self.process_once(limit=20)

                worker_task = asyncio.create_task(asyncio.to_thread(_worker))
                self._active_worker_task = worker_task
                try:
                    await worker_task
                finally:
                    self._active_worker_task = None
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._last_error = str(e)
                logger.error(f"Error in lesson repair worker loop: {e}", exc_info=True)

            try:
                await asyncio.sleep(poll_interval_s)
            except asyncio.CancelledError:
                break

    async def shutdown(self, timeout_s: float = 5.0) -> LessonWorkerShutdownResult:
        """Gracefully stops the background loop and awaits active worker completion (Rule 105)."""
        self._running = False
        task = self._active_worker_task
        if task and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
                return LessonWorkerShutdownResult(status="COMPLETE", in_flight_done=True)
            except asyncio.TimeoutError:
                return LessonWorkerShutdownResult(
                    status="TIMED_OUT", in_flight_done=False, reason="Worker in-flight timeout"
                )
            except Exception as e:
                return LessonWorkerShutdownResult(status="COMPLETE", in_flight_done=True, reason=str(e))
        return LessonWorkerShutdownResult(status="NO_ACTIVE_WORKER", in_flight_done=True)

    def stop(self):
        """Signals the worker loop to stop launching new batches."""
        self._running = False


lesson_repair_worker = LessonRepairWorker()
