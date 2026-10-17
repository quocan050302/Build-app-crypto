"""
backend/services/cg01_projection_worker.py
V16.17 - Asynchronous Projection Worker for CG-01 Paper Research Milestones.

Guarantees:
- Reads committed domain events (order.armed, trade.opened, trade.closed) from DomainEvent table.
- Projects causal milestones (ARMED, FILLED, CLOSED) into paper_research_milestones.
- Independent database transactions with durable watermark in paper_research_checkpoints.
- Never interferes with or poisons synchronous order execution, fill, or exit transactions.
- Zero mock passes: durable SQLite persistence and idempotent recovery across restarts.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from database import SessionLocal
import models
from services.paper_research_run_service import (
    resolve_active_observation_run,
    associate_order_with_research_run,
    record_committed_order_milestone,
)

logger = logging.getLogger(__name__)

SUPPORTED_EVENT_TYPES = ("order.armed", "trade.opened", "trade.closed")


class CG01ResearchProjectionWorker:
    """
    Production runtime background projection worker.
    Projects committed lifecycle domain events to paper research milestones.
    """

    def __init__(self, scope: str = "CG01", poll_interval_s: float = 1.0):
        self.scope = scope
        self.poll_interval_s = poll_interval_s
        self._running: bool = False
        self._stopped: bool = False
        self._active_worker_task: Optional[asyncio.Task] = None
        self.events_processed_count: int = 0
        self.last_run_timestamp_ms: Optional[int] = None
        self.last_error: Optional[str] = None

    @property
    def is_running(self) -> bool:
        return self._running

    def get_health_status(self, db: Optional[Session] = None) -> Dict[str, Any]:
        """Return truthful health and checkpoint metrics for projection worker."""
        owns_db = False
        if db is None:
            db = SessionLocal()
            owns_db = True
        try:
            cp = self.get_or_create_checkpoint(db)
            return {
                "scope": self.scope,
                "is_running": self._running,
                "poll_interval_s": self.poll_interval_s,
                "last_event_id": cp.last_event_id,
                "last_event_time_ms": cp.last_event_time_ms,
                "last_sequence": cp.last_sequence,
                "updated_at_ms": cp.updated_at_ms,
                "status": "HEALTHY" if self._running else "STOPPED",
            }
        except Exception as e:
            return {
                "scope": self.scope,
                "is_running": self._running,
                "status": "ERROR",
                "error": str(e),
            }
        finally:
            if owns_db:
                db.close()

    def get_or_create_checkpoint(self, db: Session) -> models.PaperResearchCheckpoint:
        """Fetch or initialize the checkpoint row for this projection worker."""
        cp = db.query(models.PaperResearchCheckpoint).filter(
            models.PaperResearchCheckpoint.scope == self.scope
        ).first()
        if not cp:
            cp = models.PaperResearchCheckpoint(
                scope=self.scope,
                last_event_id=None,
                last_event_time_ms=0,
                last_sequence=0,
                updated_at_ms=int(time.time() * 1000),
            )
            db.add(cp)
            db.flush()
        return cp

    def process_pending_events(
        self,
        db: Optional[Session] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        Processes pending unprojected domain events synchronously.
        Can be driven synchronously by tests/reconciliation or asynchronously by run().
        """
        owns_session = False
        target_db = db
        if target_db is None:
            target_db = SessionLocal()
            owns_session = True

        t_now = int(time.time() * 1000)
        self.last_run_timestamp_ms = t_now
        processed_count = 0

        try:
            checkpoint = self.get_or_create_checkpoint(target_db)
            last_seq = int(checkpoint.last_sequence or 0)

            # Query new domain events since checkpoint
            events: List[models.DomainEvent] = (
                target_db.query(models.DomainEvent)
                .filter(
                    models.DomainEvent.sequence > last_seq,
                    models.DomainEvent.event_type.in_(SUPPORTED_EVENT_TYPES),
                )
                .order_by(models.DomainEvent.sequence.asc())
                .limit(limit)
                .all()
            )

            for ev in events:
                try:
                    payload = json.loads(ev.payload) if ev.payload else {}
                except Exception:
                    payload = {}

                order_id = ev.aggregate_id or payload.get("order_id")

                if ev.event_type == "order.armed":
                    self._handle_armed_event(target_db, ev, order_id, payload)
                elif ev.event_type == "trade.opened":
                    self._handle_opened_event(target_db, ev, order_id, payload)
                elif ev.event_type == "trade.closed":
                    self._handle_closed_event(target_db, ev, order_id, payload)

                checkpoint.last_sequence = ev.sequence
                checkpoint.last_event_id = ev.event_id
                checkpoint.last_event_time_ms = ev.occurred_at
                checkpoint.updated_at_ms = int(time.time() * 1000)
                processed_count += 1

            if processed_count > 0:
                target_db.commit()
                self.events_processed_count += processed_count

            return {
                "status": "OK",
                "scope": self.scope,
                "processed": processed_count,
                "last_sequence": checkpoint.last_sequence,
            }

        except Exception as e:
            if target_db is not None:
                try:
                    target_db.rollback()
                except Exception:
                    pass
            self.last_error = str(e)
            logger.error(f"Error in CG01ResearchProjectionWorker.process_pending_events: {e}", exc_info=True)
            return {"status": "ERROR", "error": str(e), "processed": processed_count}
        finally:
            if owns_session and target_db is not None:
                target_db.close()

    def _handle_armed_event(
        self,
        db: Session,
        ev: models.DomainEvent,
        order_id: Optional[str],
        payload: Dict[str, Any],
    ) -> None:
        if not order_id:
            return
        link = db.query(models.PaperResearchOrderLink).filter(
            models.PaperResearchOrderLink.order_id == order_id
        ).first()

        target_run_id = link.run_id if link else payload.get("run_id")
        if not target_run_id:
            order = db.query(models.PaperOrder).filter(models.PaperOrder.id == order_id).first()
            if order:
                sym = order.symbol or "XAUUSDT"
                act = resolve_active_observation_run(db=db, scope=self.scope, symbol=sym)
                if act:
                    target_run_id = act.run_id
                    associate_order_with_research_run(
                        db=db,
                        run_id=target_run_id,
                        order_id=order_id,
                        opportunity_id=payload.get("opportunity_id"),
                        now_ms=ev.occurred_at,
                    )

        if target_run_id:
            record_committed_order_milestone(
                db=db,
                run_id=target_run_id,
                order_id=order_id,
                milestone="ARMED",
                timestamp_ms=ev.occurred_at,
                payload=payload,
            )

    def _handle_opened_event(
        self,
        db: Session,
        ev: models.DomainEvent,
        order_id: Optional[str],
        payload: Dict[str, Any],
    ) -> None:
        if not order_id:
            return
        links = db.query(models.PaperResearchOrderLink).filter(
            models.PaperResearchOrderLink.order_id == order_id
        ).all()
        for link in links:
            record_committed_order_milestone(
                db=db,
                run_id=link.run_id,
                order_id=order_id,
                milestone="FILLED",
                timestamp_ms=ev.occurred_at,
                payload=payload,
            )

    def _handle_closed_event(
        self,
        db: Session,
        ev: models.DomainEvent,
        order_id: Optional[str],
        payload: Dict[str, Any],
    ) -> None:
        if not order_id:
            return
        links = db.query(models.PaperResearchOrderLink).filter(
            models.PaperResearchOrderLink.order_id == order_id
        ).all()
        for link in links:
            record_committed_order_milestone(
                db=db,
                run_id=link.run_id,
                order_id=order_id,
                milestone="CLOSED",
                timestamp_ms=ev.occurred_at,
                payload=payload,
            )

    async def run(self) -> None:
        """Continuous non-blocking projection worker loop."""
        self._running = True
        logger.info(f"CG01ResearchProjectionWorker started for scope {self.scope}")
        while self._running:
            try:
                await asyncio.to_thread(self.process_pending_events)
                await asyncio.sleep(self.poll_interval_s)
            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as e:
                logger.error(f"CG01ResearchProjectionWorker loop error: {e}", exc_info=True)
                await asyncio.sleep(self.poll_interval_s)

    def stop(self) -> None:
        """Signal projection worker to stop."""
        self._running = False
        self._stopped = True


# Global singleton instance
cg01_projection_worker = CG01ResearchProjectionWorker()
