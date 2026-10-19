"""
backend/services/cg01_projection_worker.py
V16.18 - Hardened Asynchronous Projection Worker for CG-01 Paper Research Milestones.

Guarantees (Rules 018, 071-085, 089):
- Reads committed domain events (order.armed, trade.opened, trade.closed) from DomainEvent table.
- Projects causal milestones (ARMED, FILLED, CLOSED) into paper_research_milestones.
- Employs explicit ProjectionOutcome (PROJECTED, ALREADY_PROJECTED, RETRY_PENDING, UNVERIFIABLE, UNSUPPORTED).
- Persists unlinked events into durable paper_research_pending_projections in the same transaction as checkpoint.
- Retries pending projections with exponential backoff without dropping unlinked historical events.
- Never attaches an older event to a newer active run just because it is currently collecting.
- Independent database transactions with durable watermark in paper_research_checkpoints.
- Never poisons synchronous order execution, fill, or exit transactions.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
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


class ProjectionOutcome(str, Enum):
    """V16.18 Rule 074: Explicit outcome for domain event projection handlers."""
    PROJECTED = "PROJECTED"
    ALREADY_PROJECTED = "ALREADY_PROJECTED"
    RETRY_PENDING = "RETRY_PENDING"
    UNVERIFIABLE = "UNVERIFIABLE"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass
class RetryProjectionResult:
    """Rules 091-093: Explicit outcome contract for projection retries."""
    attempted: int = 0
    resolved: int = 0
    deferred: int = 0
    needs_review: int = 0
    state_changed: bool = False
    errors: List[str] = field(default_factory=list)


def record_pending_projection(
    db: Session,
    event_id: str,
    event_type: str,
    aggregate_id: Optional[str],
    reason: str,
    payload_json: Optional[str] = None,
    now_ms: Optional[int] = None,
    scope: str = "CG01",
) -> models.PaperResearchPendingProjection:
    """Rules 078, 079, 101: Atomically record an unlinked or deferred event for durable retry."""
    t_now = now_ms or int(time.time() * 1000)
    pending = db.query(models.PaperResearchPendingProjection).filter(
        models.PaperResearchPendingProjection.event_id == event_id,
        models.PaperResearchPendingProjection.scope == scope,
    ).first()
    if not pending:
        pending = models.PaperResearchPendingProjection(
            event_id=event_id,
            scope=scope,
            event_type=event_type,
            aggregate_id=aggregate_id,
            reason=reason,
            attempt_count=1,
            status="PENDING",
            payload_json=payload_json,
            first_seen_at_ms=t_now,
            next_retry_at_ms=t_now + 5000,
            created_at_ms=t_now,
            updated_at_ms=t_now,
        )
        db.add(pending)
    else:
        pending.attempt_count = (pending.attempt_count or 0) + 1
        pending.last_error = reason
        pending.next_retry_at_ms = t_now + min(60000, 5000 * pending.attempt_count)
        pending.updated_at_ms = t_now
    db.flush()
    return pending


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
        """Return truthful health and checkpoint metrics for projection worker (Rule 089)."""
        owns_db = False
        if db is None:
            db = SessionLocal()
            owns_db = True
        try:
            cp = self.get_or_create_checkpoint(db)
            pending_count = (
                db.query(models.PaperResearchPendingProjection)
                .filter(models.PaperResearchPendingProjection.resolution_status == "PENDING")
                .count()
            )
            return {
                "scope": self.scope,
                "is_running": self._running,
                "poll_interval_s": self.poll_interval_s,
                "last_event_id": cp.last_event_id,
                "last_event_time_ms": cp.last_event_time_ms,
                "last_sequence": cp.last_sequence,
                "updated_at_ms": cp.updated_at_ms,
                "pending_projections_count": pending_count,
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

            # Rule 080, 091: Retry previously pending projections whose links might now exist
            retry_result = self.retry_pending_projections(target_db)

            # Rule 091: Commit whenever domain events processed OR retry state changed
            if processed_count > 0 or retry_result.state_changed:
                target_db.commit()
                self.events_processed_count += processed_count

            return {
                "status": "OK",
                "scope": self.scope,
                "processed": processed_count,
                "retried": retry_result.attempted,
                "resolved": retry_result.resolved,
                "deferred": retry_result.deferred,
                "state_changed": retry_result.state_changed,
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
        ev: Any,
        order_id: Optional[str],
        payload: Dict[str, Any],
    ) -> ProjectionOutcome:
        """
        Rules 024, 072, 075, 082-084:
        Project order.armed to ARMED milestone using strict causal association.
        Never blindly attach an order to the currently active run if order predates that run!
        """
        if not order_id:
            record_pending_projection(
                db, ev.event_id, ev.event_type, order_id, "MISSING_ORDER_ID", getattr(ev, "payload", None), scope=self.scope
            )
            return ProjectionOutcome.UNVERIFIABLE

        link = db.query(models.PaperResearchOrderLink).filter(
            models.PaperResearchOrderLink.order_id == order_id
        ).first()

        target_run_id = link.run_id if link else payload.get("run_id")
        if not target_run_id:
            order = db.query(models.PaperOrder).filter(models.PaperOrder.id == order_id).first()
            if order:
                strat_ctx = getattr(order, "strategy_context", None) or {}
                if isinstance(strat_ctx, str):
                    try:
                        strat_ctx = json.loads(strat_ctx)
                    except Exception:
                        strat_ctx = {}
                notes = getattr(order, "notes", None)
                target_run_id = strat_ctx.get("run_id") or (notes if str(notes or "").startswith("run_") else None)
                if target_run_id:
                    associate_order_with_research_run(
                        db=db,
                        run_id=target_run_id,
                        order_id=order_id,
                        opportunity_id=payload.get("opportunity_id") or strat_ctx.get("opportunity_id"),
                        now_ms=ev.occurred_at,
                    )

        if target_run_id:
            ok = record_committed_order_milestone(
                db=db,
                run_id=target_run_id,
                order_id=order_id,
                milestone="ARMED",
                timestamp_ms=ev.occurred_at,
                payload=payload,
            )
            return ProjectionOutcome.PROJECTED if ok else ProjectionOutcome.RETRY_PENDING
        else:
            # Rule 078, 084: Durably store as pending projection for later causal linkage
            record_pending_projection(
                db, ev.event_id, ev.event_type, order_id, "UNLINKED_CAUSAL_RUN", getattr(ev, "payload", None), scope=self.scope
            )
            return ProjectionOutcome.RETRY_PENDING

    def _handle_opened_event(
        self,
        db: Session,
        ev: Any,
        order_id: Optional[str],
        payload: Dict[str, Any],
    ) -> ProjectionOutcome:
        """
        Rules 073, 076, 097:
        Project trade.opened to FILLED milestone. If order is not yet linked,
        store in durable pending projections rather than silently dropping!
        """
        if not order_id:
            record_pending_projection(
                db, ev.event_id, ev.event_type, order_id, "MISSING_ORDER_ID", getattr(ev, "payload", None), scope=self.scope
            )
            return ProjectionOutcome.UNVERIFIABLE

        links = db.query(models.PaperResearchOrderLink).filter(
            models.PaperResearchOrderLink.order_id == order_id
        ).all()
        if not links:
            record_pending_projection(
                db, ev.event_id, ev.event_type, order_id, "MISSING_ORDER_LINK", getattr(ev, "payload", None), scope=self.scope
            )
            return ProjectionOutcome.RETRY_PENDING

        all_ok = True
        for link in links:
            ok = record_committed_order_milestone(
                db=db,
                run_id=link.run_id,
                order_id=order_id,
                milestone="FILLED",
                timestamp_ms=ev.occurred_at,
                payload=payload,
            )
            if not ok:
                all_ok = False
        return ProjectionOutcome.PROJECTED if all_ok else ProjectionOutcome.RETRY_PENDING

    def _handle_closed_event(
        self,
        db: Session,
        ev: Any,
        order_id: Optional[str],
        payload: Dict[str, Any],
    ) -> ProjectionOutcome:
        """
        Rules 073, 077, 097:
        Project trade.closed to CLOSED milestone. If unlinked, store in durable pending projections.
        """
        if not order_id:
            record_pending_projection(
                db, ev.event_id, ev.event_type, order_id, "MISSING_ORDER_ID", getattr(ev, "payload", None), scope=self.scope
            )
            return ProjectionOutcome.UNVERIFIABLE

        links = db.query(models.PaperResearchOrderLink).filter(
            models.PaperResearchOrderLink.order_id == order_id
        ).all()
        if not links:
            record_pending_projection(
                db, ev.event_id, ev.event_type, order_id, "MISSING_ORDER_LINK", getattr(ev, "payload", None), scope=self.scope
            )
            return ProjectionOutcome.RETRY_PENDING

        all_ok = True
        for link in links:
            ok = record_committed_order_milestone(
                db=db,
                run_id=link.run_id,
                order_id=order_id,
                milestone="CLOSED",
                timestamp_ms=ev.occurred_at,
                payload=payload,
            )
            if not ok:
                all_ok = False
        return ProjectionOutcome.PROJECTED if all_ok else ProjectionOutcome.RETRY_PENDING

    def retry_pending_projections(self, db: Session, limit: int = 50) -> RetryProjectionResult:
        """
        Rules 027, 028, 080, 091-097: Retries durable pending projections using original
        event timestamps and returning structured RetryProjectionResult.
        """
        t_now = int(time.time() * 1000)
        pending_rows = (
            db.query(models.PaperResearchPendingProjection)
            .filter(
                models.PaperResearchPendingProjection.scope == self.scope,
                models.PaperResearchPendingProjection.resolution_status == "PENDING",
                models.PaperResearchPendingProjection.next_retry_at <= t_now,
            )
            .limit(limit)
            .all()
        )
        result = RetryProjectionResult()
        for row in pending_rows:
            result.attempted += 1
            try:
                payload = json.loads(row.payload_json) if row.payload_json else {}
            except Exception:
                payload = {}

            order_id = row.aggregate_id or payload.get("order_id")

            # Rule 028, 094: Retrieve original occurred_at from DomainEvent
            orig_dev = db.query(models.DomainEvent).filter(
                models.DomainEvent.event_id == row.event_id
            ).first()
            orig_occurred_at = orig_dev.occurred_at if orig_dev is not None else (
                getattr(row, "first_seen_at_ms", None) or getattr(row, "first_seen_at", None) or t_now
            )

            # Typed event snapshot preserving original timestamp (Rule 096)
            class _EventSnapshot:
                event_id = row.event_id
                event_type = row.event_type
                aggregate_id = order_id
                occurred_at = orig_occurred_at
                payload = row.payload_json

            ev = _EventSnapshot()
            outcome = ProjectionOutcome.RETRY_PENDING
            if row.event_type == "order.armed":
                outcome = self._handle_armed_event(db, ev, order_id, payload)
            elif row.event_type == "trade.opened":
                outcome = self._handle_opened_event(db, ev, order_id, payload)
            elif row.event_type == "trade.closed":
                outcome = self._handle_closed_event(db, ev, order_id, payload)

            if outcome == ProjectionOutcome.PROJECTED:
                row.resolution_status = "RESOLVED"
                setattr(row, "resolved_at_ms", t_now)
                row.updated_at_ms = t_now
                result.resolved += 1
                result.state_changed = True
            else:
                row.attempt_count = (row.attempt_count or 0) + 1
                row.next_retry_at_ms = t_now + min(60000, 5000 * row.attempt_count)
                row.updated_at_ms = t_now
                if row.attempt_count > 10:
                    row.resolution_status = "NEEDS_REVIEW"
                    result.needs_review += 1
                else:
                    result.deferred += 1
                result.state_changed = True

        if result.state_changed:
            db.flush()
        return result

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
