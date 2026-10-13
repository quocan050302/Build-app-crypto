"""
backend/services/market_reconciliation_service.py
V14.4.3 — Background Late Evidence Reconciliation Consumer.

Enforces:
1. Regularly drains late evidence from QuoteOrderingService.
2. Checks active paper_open orders against historical late quotes in a bounded interval.
3. Coverage Assessment (COMPLETE, PARTIAL, UNKNOWN, CONFLICTED):
   Only executes terminal exit if chronology and coverage are proven.
4. If SL/TP was reached, executes single CAS close via TradeLifecycleService.
5. If earlier conflicting quote arrives for an ALREADY CLOSED order, does NOT reopen order or alter PNL;
   records REVIEW_REQUIRED audit.
6. Acknowledges processed items using stable evidence_ids to prevent concurrent arrival loss.
7. Tracks in-flight worker task with clean drain / shutdown contract.
8. Runs as background task wired into FastAPI lifespan.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
from sqlalchemy.orm import Session

import models
from database import SessionLocal
from services.quote_ordering_service import quote_ordering_service
from services.trade_lifecycle_service import TradeLifecycleService
from services.shared_exit_evaluator import ExitDecision, evaluate_order_exit

logger = logging.getLogger("market_reconciliation")


class CoverageStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"
    CONFLICTED = "CONFLICTED"


@dataclass(frozen=True)
class CoverageValidation:
    is_valid: bool
    status: CoverageStatus
    reason_code: str
    details: Dict[str, Any] = field(default_factory=dict)


def validate_coverage_proof(
    proof: Optional[Dict[str, Any]],
    requested_interval: Tuple[int, int],
    required_price_basis: str = "BID_ASK",
    source_epoch: int = 1,
) -> CoverageValidation:
    """Pure validator (PHẦN 104):
    Validates explicit continuity proof before declaring COMPLETE.
    """
    if not proof:
        return CoverageValidation(
            is_valid=False,
            status=CoverageStatus.UNKNOWN,
            reason_code="MISSING_COVERAGE_PROOF",
        )
    resolution = proof.get("resolution")
    if resolution == "OHLC" and required_price_basis == "BID_ASK":
        return CoverageValidation(
            is_valid=False,
            status=CoverageStatus.PARTIAL,
            reason_code="INSUFFICIENT_RESOLUTION_OHLC_FOR_TICK",
            details={"requested_basis": required_price_basis, "proof_resolution": resolution},
        )
    proof_epoch = proof.get("epoch", 1)
    if proof_epoch != source_epoch:
        return CoverageValidation(
            is_valid=False,
            status=CoverageStatus.CONFLICTED,
            reason_code="EPOCH_MISMATCH",
            details={"requested_epoch": source_epoch, "proof_epoch": proof_epoch},
        )
    p_start = proof.get("interval_start_ms", 0)
    p_end = proof.get("interval_end_ms", 0)
    req_start, req_end = requested_interval
    if p_start > req_start or (req_end > 0 and p_end < req_end):
        return CoverageValidation(
            is_valid=False,
            status=CoverageStatus.PARTIAL,
            reason_code="PROOF_DOES_NOT_COVER_REQUESTED_INTERVAL",
            details={"requested": requested_interval, "proof_range": (p_start, p_end)},
        )
    if proof.get("has_gap", False) or proof.get("missing_intervals"):
        return CoverageValidation(
            is_valid=False,
            status=CoverageStatus.PARTIAL,
            reason_code="PROOF_CONTAINS_GAPS",
            details={"missing_intervals": proof.get("missing_intervals", [])},
        )
    return CoverageValidation(
        is_valid=True,
        status=CoverageStatus.COMPLETE,
        reason_code="VERIFIED_CONTINUOUS_PROOF",
    )


@dataclass(frozen=True)
class CoverageAssessment:
    interval_start_ms: int
    interval_end_ms: int
    source: str
    epoch: int
    price_basis: str
    coverage_status: CoverageStatus
    missing_intervals: List[Any] = field(default_factory=list)
    evidence_ids: List[str] = field(default_factory=list)
    assessed_at_ms: int = 0
    reason_code: str = "UNKNOWN_NO_PROOF"


@dataclass(frozen=True)
class TerminalCandidate:
    order_id: str
    evidence_id: str
    source_timestamp: int
    known_at_ms: int
    bid: Optional[float]
    ask: Optional[float]
    last: Optional[float]
    price_basis: str
    cause: str
    executable_price: float
    epoch: int = 1
    source: str = "UNKNOWN"


@dataclass(frozen=True)
class ResolutionDecision:
    action: str  # "CLOSE", "NEEDS_RECONCILIATION", "AMBIGUOUS", "REVIEW_REQUIRED", "NONE"
    candidate: Optional[TerminalCandidate] = None
    reason_code: str = "NO_ACTION"
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ShutdownResult:
    status: str  # "COMPLETE", "TIMED_OUT", "NO_ACTIVE_WORKER"
    in_flight_done: bool
    unprocessed_count: int = 0
    reason: Optional[str] = None


@dataclass(frozen=True)
class OrderReconciliationWindow:
    order_id: str
    symbol: str
    opened_at: int
    end_as_of: int
    source_epoch: int = 1
    status: str = "PENDING"  # "PENDING", "RESOLVED", "REVIEW_REQUIRED"
    cursor: int = 0
    order: Optional[models.PaperOrder] = None


@dataclass(frozen=True)
class WindowPage:
    items: List[OrderReconciliationWindow]
    next_cursor: Optional[int] = None


@dataclass(frozen=True)
class EvidencePage:
    items: List[Dict[str, Any]]
    next_cursor: Optional[int] = None


@dataclass(frozen=True)
class ResolutionApplyResult:
    action: str
    applied: bool
    audit_id: Optional[str] = None
    version: int = 1
    committed: bool = False



def assess_reconciliation_coverage(
    order: models.PaperOrder,
    window: Any,
    evidence: List[Dict[str, Any]],
    source_metadata: Optional[Dict[str, Any]] = None,
) -> CoverageAssessment:
    """Pure helper (PHẦN 032, 033, 104):
    Assesses whether the reconciliation evidence window covers from order opened_at
    to candidate terminal time without gaps or unverified epochs.
    Defaults to UNKNOWN without explicit continuity proof; never assumes COMPLETE.
    """
    opened_at = getattr(order, "opened_at", None) or 0
    end_ms = 0
    evidence_ids = []
    if evidence:
        for r in evidence:
            ts = r.get("source_timestamp") or 0
            if ts > end_ms:
                end_ms = ts
            ev_id = r.get("evidence_id")
            if ev_id:
                evidence_ids.append(ev_id)
    else:
        end_ms = opened_at

    # Check for overflow gap in quote ordering service
    symbol = getattr(order, "instrument", "XAUUSDT")
    gap = quote_ordering_service.get_overflow_gap(symbol)
    missing = []
    if gap:
        gap_start = gap.get("gap_start_timestamp") or gap.get("gap_start_ms") or 0
        gap_end = gap.get("gap_end_timestamp") or gap.get("gap_end_ms") or 0
        if gap_end >= opened_at and (end_ms == 0 or gap_start <= end_ms):
            missing.append((gap_start, gap_end))

    if missing:
        return CoverageAssessment(
            interval_start_ms=opened_at,
            interval_end_ms=end_ms,
            source=source_metadata.get("source") if source_metadata else getattr(order, "instrument", "XAUUSDT"),
            epoch=source_metadata.get("epoch", 1) if source_metadata else 1,
            price_basis="BID_ASK",
            coverage_status=CoverageStatus.PARTIAL,
            missing_intervals=missing,
            evidence_ids=evidence_ids,
            assessed_at_ms=end_ms,
            reason_code="OVERFLOW_GAP_DETECTED",
        )

    # Empty or single-tick evidence without explicit proof is UNKNOWN (PHẦN 032, 104)
    if not evidence or len(evidence) <= 1:
        if not source_metadata or not source_metadata.get("continuity_proof"):
            return CoverageAssessment(
                interval_start_ms=opened_at,
                interval_end_ms=end_ms,
                source=source_metadata.get("source") if source_metadata else getattr(order, "instrument", "XAUUSDT"),
                epoch=source_metadata.get("epoch", 1) if source_metadata else 1,
                price_basis="BID_ASK",
                coverage_status=CoverageStatus.UNKNOWN,
                missing_intervals=[],
                evidence_ids=evidence_ids,
                assessed_at_ms=end_ms,
                reason_code="INSUFFICIENT_EVIDENCE_NO_CONTINUITY_PROOF",
            )

    # If continuity_proof provided in source_metadata, validate it
    if source_metadata and "continuity_proof" in source_metadata:
        proof_val = validate_coverage_proof(
            source_metadata["continuity_proof"],
            requested_interval=(opened_at, end_ms),
            required_price_basis="BID_ASK",
            source_epoch=source_metadata.get("epoch", 1),
        )
        return CoverageAssessment(
            interval_start_ms=opened_at,
            interval_end_ms=end_ms,
            source=source_metadata.get("source", "UNKNOWN"),
            epoch=source_metadata.get("epoch", 1),
            price_basis="BID_ASK",
            coverage_status=proof_val.status,
            missing_intervals=proof_val.details.get("missing_intervals", []),
            evidence_ids=evidence_ids,
            assessed_at_ms=end_ms,
            reason_code=proof_val.reason_code,
        )

    # Without explicit continuity proof, default to UNKNOWN
    return CoverageAssessment(
        interval_start_ms=opened_at,
        interval_end_ms=end_ms,
        source=source_metadata.get("source") if source_metadata else getattr(order, "instrument", "XAUUSDT"),
        epoch=source_metadata.get("epoch", 1) if source_metadata else 1,
        price_basis="BID_ASK",
        coverage_status=CoverageStatus.UNKNOWN,
        missing_intervals=[],
        evidence_ids=evidence_ids,
        assessed_at_ms=end_ms,
        reason_code="NO_CONTINUITY_PROOF_PROVIDED",
    )


def collect_terminal_candidates(
    order: models.PaperOrder,
    evidence_records: List[Dict[str, Any]],
) -> List[TerminalCandidate]:
    """Pure collector (PHẦN 016):
    Gathers all exit terminal triggers sorted chronologically by source_timestamp.
    """
    candidates = []
    opened_at = getattr(order, "opened_at", None) or 0

    for r in evidence_records:
        src_ts = r.get("source_timestamp") or 0
        if opened_at and src_ts < opened_at:
            continue

        decision = evaluate_order_exit(order, r)
        if decision.triggered:
            candidates.append(
                TerminalCandidate(
                    order_id=order.id,
                    evidence_id=r.get("evidence_id") or "UNKNOWN",
                    source_timestamp=src_ts,
                    known_at_ms=r.get("known_at_ms") or src_ts,
                    bid=r.get("bid"),
                    ask=r.get("ask"),
                    last=r.get("last"),
                    price_basis=decision.price_basis,
                    cause=decision.cause,
                    executable_price=decision.applied_exit_price,
                    epoch=r.get("epoch", 1),
                    source=r.get("source", "UNKNOWN"),
                )
            )

    # Sort strictly by source_timestamp ascending, then stable evidence_id
    candidates.sort(key=lambda c: (c.source_timestamp, c.evidence_id))
    return candidates


def resolve_reconciliation_window(
    order: models.PaperOrder,
    candidates: List[TerminalCandidate],
    coverage: CoverageAssessment,
) -> ResolutionDecision:
    """Pure resolver (PHẦN 019, 020):
    Resolves the chronological terminal decision for an order given candidates and coverage.
    """
    if not candidates:
        return ResolutionDecision(action="NONE", reason_code="NO_CANDIDATE")

    first_cand = candidates[0]

    # Check if order is already closed
    is_closed = getattr(order, "state", "") == "closed" or getattr(order, "closed_at", None) is not None
    if is_closed:
        closed_at = getattr(order, "closed_at", None) or 0
        existing_cause = getattr(order, "exit_cause", "")
        # If candidate occurred BEFORE closed_at and conflicts with existing cause:
        if first_cand.source_timestamp < closed_at and first_cand.cause != existing_cause:
            return ResolutionDecision(
                action="REVIEW_REQUIRED",
                candidate=first_cand,
                reason_code="EARLIER_CONFLICTING_TERMINAL_ARRIVED_POST_CLOSE",
                details={
                    "closed_at": closed_at,
                    "existing_cause": existing_cause,
                    "candidate_ts": first_cand.source_timestamp,
                    "candidate_cause": first_cand.cause,
                },
            )
        return ResolutionDecision(action="NONE", reason_code="ALREADY_CLOSED_NO_CONFLICT")

    # If coverage is incomplete (PARTIAL, UNKNOWN, CONFLICTED)
    if coverage.coverage_status != CoverageStatus.COMPLETE:
        return ResolutionDecision(
            action="NEEDS_RECONCILIATION",
            candidate=first_cand,
            reason_code=f"INCOMPLETE_COVERAGE_{coverage.coverage_status.value}",
            details={"missing_intervals": coverage.missing_intervals},
        )

    # Check if two candidates with different causes have the exact same timestamp
    same_ts_candidates = [c for c in candidates if c.source_timestamp == first_cand.source_timestamp]
    causes = set(c.cause for c in same_ts_candidates)
    if len(causes) > 1 and "SL_HIT" in causes and "TP_HIT" in causes:
        return ResolutionDecision(
            action="CLOSE",
            candidate=TerminalCandidate(
                order_id=first_cand.order_id,
                evidence_id=first_cand.evidence_id,
                source_timestamp=first_cand.source_timestamp,
                known_at_ms=first_cand.known_at_ms,
                bid=first_cand.bid,
                ask=first_cand.ask,
                last=first_cand.last,
                price_basis=first_cand.price_basis,
                cause="AMBIGUOUS_BAR_SL_FIRST",
                executable_price=first_cand.executable_price,
            ),
            reason_code="AMBIGUOUS_SAME_TIMESTAMP_CONSERVATIVE_SL",
        )

    return ResolutionDecision(
        action="CLOSE",
        candidate=first_cand,
        reason_code="FIRST_CHRONOLOGICAL_TERMINAL_CONFIRMED",
    )


def persist_reconciliation_evidence(db: Session, records: List[Dict[str, Any]]) -> List[str]:
    """PHẦN 034, 103: Persists late evidence records durably to DB before ACK."""
    persisted_ids: List[str] = []
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    for r in records:
        ev_id = r.get("evidence_id")
        if not ev_id:
            continue
        event_uid = f"late_ev_{ev_id}"
        existing = db.query(models.DomainEvent).filter(models.DomainEvent.event_id == event_uid).first()
        if not existing:
            event = models.DomainEvent(
                event_id=event_uid,
                sequence=1,
                schema_version="1.0.0",
                event_type="LATE_RECONCILIATION_EVIDENCE",
                aggregate_id=ev_id,
                aggregate_version=1,
                occurred_at=r.get("source_timestamp") or now_ms,
                published_at=now_ms,
                payload=json.dumps(r),
            )
            db.add(event)
        persisted_ids.append(ev_id)
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to persist reconciliation evidence batch: {e}")
        return []
    return persisted_ids


def append_reconciliation_audit(
    db: Session,
    order_id: str,
    decision: ResolutionDecision,
    *,
    evidence_ids: List[str],
    run_context: Optional[Dict[str, Any]] = None,
) -> Optional[models.DomainEvent]:
    """PHẦN 037, 105: Appends durable audit record for reconciliation conflict/action and commits to DB."""
    cand = decision.candidate
    cand_ts = cand.source_timestamp if cand else 0
    ev_ref = evidence_ids[0] if evidence_ids else (cand.evidence_id if cand else "none")
    unique_event_id = f"recon_audit_{order_id}_{decision.reason_code}_{cand_ts}_{ev_ref}"

    existing = db.query(models.DomainEvent).filter(models.DomainEvent.event_id == unique_event_id).first()
    if existing:
        return existing

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    audit_payload = {
        "order_id": order_id,
        "action": decision.action,
        "reason_code": decision.reason_code,
        "candidate": {
            "evidence_id": cand.evidence_id if cand else None,
            "source_timestamp": cand_ts,
            "cause": cand.cause if cand else None,
            "executable_price": cand.executable_price if cand else None,
        } if cand else None,
        "details": decision.details,
        "evidence_ids": evidence_ids,
        "run_context": run_context or {},
    }

    event = models.DomainEvent(
        event_id=unique_event_id,
        sequence=1,
        schema_version="1.0.0",
        event_type="RECONCILIATION_AUDIT",
        aggregate_id=order_id,
        aggregate_version=1,
        occurred_at=cand_ts or now_ms,
        published_at=now_ms,
        payload=json.dumps(audit_payload),
    )
    db.add(event)

    # Also update order invalidation_reason durably without overwriting earlier reasons
    order = db.query(models.PaperOrder).filter(models.PaperOrder.id == order_id).first()
    if order:
        audit_note = f"REVIEW_REQUIRED: {decision.reason_code} (late {cand.cause if cand else 'tick'} at {cand_ts} vs closed at {order.closed_at})"
        if order.invalidation_reason:
            if audit_note not in order.invalidation_reason:
                order.invalidation_reason = f"{order.invalidation_reason} | {audit_note}"
        else:
            order.invalidation_reason = audit_note

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to commit reconciliation audit for order {order_id}: {e}")
        return None

    return event


def assess_provider_coverage(
    provider_proof: Optional[Dict[str, Any]],
    window: OrderReconciliationWindow,
    required_resolution: str = "TICK",
) -> CoverageAssessment:
    """PHẦN 088: Assesses authority coverage provided by an external provider contract."""
    if not provider_proof:
        return CoverageAssessment(
            interval_start_ms=window.opened_at,
            interval_end_ms=window.end_as_of,
            source=window.symbol,
            epoch=window.source_epoch,
            price_basis="BID_ASK",
            coverage_status=CoverageStatus.UNKNOWN,
            missing_intervals=[],
            evidence_ids=[],
            assessed_at_ms=window.end_as_of,
            reason_code="MISSING_PROVIDER_PROOF",
        )

    proof_val = validate_coverage_proof(
        provider_proof,
        requested_interval=(window.opened_at, window.end_as_of),
        required_price_basis="BID_ASK",
        source_epoch=window.source_epoch,
    )
    return CoverageAssessment(
        interval_start_ms=window.opened_at,
        interval_end_ms=window.end_as_of,
        source=provider_proof.get("source", window.symbol),
        epoch=provider_proof.get("epoch", window.source_epoch),
        price_basis="BID_ASK",
        coverage_status=proof_val.status,
        missing_intervals=proof_val.details.get("missing_intervals", []),
        evidence_ids=provider_proof.get("proof_references", []),
        assessed_at_ms=window.end_as_of,
        reason_code=proof_val.reason_code,
    )


def load_reconciliation_coverage(
    db: Session,
    window: OrderReconciliationWindow,
    source_state: Optional[Dict[str, Any]] = None,
) -> CoverageAssessment:
    """PHẦN 017: Evaluates reconciliation coverage from authoritative source contract."""
    if source_state and "continuity_proof" in source_state:
        return assess_provider_coverage(source_state["continuity_proof"], window, required_resolution="TICK")
    return CoverageAssessment(
        interval_start_ms=window.opened_at,
        interval_end_ms=window.end_as_of,
        source=window.symbol,
        epoch=window.source_epoch,
        price_basis="BID_ASK",
        coverage_status=CoverageStatus.UNKNOWN,
        missing_intervals=[],
        evidence_ids=[],
        assessed_at_ms=window.end_as_of,
        reason_code="NO_AUTHORITATIVE_PROVIDER_PROOF",
    )


def load_pending_reconciliation_windows(
    db: Session,
    symbol: str = "XAUUSDT",
    limit: int = 50,
    cursor: int = 0,
) -> WindowPage:
    """PHẦN 020, 086: Loads active paper_open orders and recently closed orders for reconciliation window replay."""
    open_orders = (
        db.query(models.PaperOrder)
        .filter(
            models.PaperOrder.instrument == symbol,
            models.PaperOrder.state == "paper_open",
        )
        .order_by(models.PaperOrder.opened_at.asc())
        .limit(limit)
        .all()
    )

    query_closed = (
        db.query(models.PaperOrder)
        .filter(
            models.PaperOrder.instrument == symbol,
            models.PaperOrder.state == "closed",
        )
    )
    if cursor > 0:
        query_closed = query_closed.filter(models.PaperOrder.closed_at >= cursor)
    recent_closed = (
        query_closed
        .order_by(models.PaperOrder.closed_at.desc())
        .limit(limit)
        .all()
    )

    windows: List[OrderReconciliationWindow] = []
    for ord in list(open_orders) + list(recent_closed):
        op_at = getattr(ord, "opened_at", None) or getattr(ord, "created_at", None) or 0
        cl_at = getattr(ord, "closed_at", None) or 0
        windows.append(
            OrderReconciliationWindow(
                order_id=ord.id,
                symbol=symbol,
                opened_at=op_at,
                end_as_of=cl_at,
                source_epoch=1,
                status="RESOLVED" if ord.state == "closed" else "PENDING",
                cursor=op_at,
                order=ord,
            )
        )

    return WindowPage(items=windows[:limit], next_cursor=None)


def load_persisted_window_evidence(
    db: Session,
    window: OrderReconciliationWindow,
    as_of_ms: int = 0,
    limit: int = 200,
    cursor: int = 0,
) -> EvidencePage:
    """PHẦN 019, 087: Replays persisted late reconciliation evidence from DomainEvent table across polls/restart."""
    start_ts = max(window.opened_at, cursor)
    query = (
        db.query(models.DomainEvent)
        .filter(
            models.DomainEvent.event_type == "LATE_RECONCILIATION_EVIDENCE",
            models.DomainEvent.occurred_at >= start_ts,
        )
    )
    if as_of_ms > 0:
        query = query.filter(models.DomainEvent.occurred_at <= as_of_ms)

    events = query.order_by(models.DomainEvent.occurred_at.asc(), models.DomainEvent.sequence.asc()).limit(limit).all()

    evidence_items: List[Dict[str, Any]] = []
    next_cursor = cursor
    for ev in events:
        if ev.payload:
            try:
                payload = json.loads(ev.payload) if isinstance(ev.payload, str) else ev.payload
                ev_sym = payload.get("symbol") or payload.get("instrument")
                if not ev_sym or ev_sym == window.symbol:
                    evidence_items.append(payload)
                    ev_ts = payload.get("source_timestamp") or ev.occurred_at
                    if ev_ts > next_cursor:
                        next_cursor = ev_ts
            except Exception:
                continue

    return EvidencePage(items=evidence_items, next_cursor=next_cursor if events else None)


def persist_resolution_or_review(
    db: Session,
    window: OrderReconciliationWindow,
    decision: ResolutionDecision,
) -> ResolutionApplyResult:
    """PHẦN 090: Persists resolution decision atomically or appends audit for review."""
    if not window.order:
        return ResolutionApplyResult(action=decision.action, applied=False, committed=False)

    if decision.action == "CLOSE" and decision.candidate:
        cand = decision.candidate
        confidence = "SIMULATED_AMBIGUOUS" if cand.cause == "AMBIGUOUS_BAR_SL_FIRST" else "CONFIRMED_TICK"
        TradeLifecycleService.execute_close(
            db=db,
            order_id=window.order.id,
            exit_price=cand.executable_price,
            exit_cause=cand.cause,
            occurred_at=cand.source_timestamp,
            session_tag="LATE_RECONCILIATION",
            recovery_metadata={
                "recovery_status": "RECOVERED",
                "confidence": confidence,
                "evidence_id": cand.evidence_id,
            },
        )
        return ResolutionApplyResult(
            action="CLOSE",
            applied=True,
            audit_id=None,
            version=getattr(window.order, "version", 1) or 1,
            committed=True,
        )

    if decision.action == "REVIEW_REQUIRED":
        evidence_ids = [decision.candidate.evidence_id] if (decision.candidate and decision.candidate.evidence_id) else []
        audit_event = append_reconciliation_audit(db, window.order.id, decision, evidence_ids=evidence_ids)
        return ResolutionApplyResult(
            action="REVIEW_REQUIRED",
            applied=True,
            audit_id=audit_event.event_id if audit_event else None,
            version=getattr(window.order, "version", 1) or 1,
            committed=True,
        )

    if decision.action == "NEEDS_RECONCILIATION":
        return ResolutionApplyResult(
            action="NEEDS_RECONCILIATION",
            applied=False,
            audit_id=None,
            version=getattr(window.order, "version", 1) or 1,
            committed=True,
        )

    return ResolutionApplyResult(
        action=decision.action,
        applied=False,
        audit_id=None,
        version=getattr(window.order, "version", 1) or 1,
        committed=True,
    )


class MarketReconciliationService:
    def __init__(self):
        self._running = False
        self._active_worker_task: Optional[asyncio.Task] = None

    def apply_reconciliation_decision(
        self,
        db: Session,
        order: models.PaperOrder,
        decision: ResolutionDecision,
    ) -> ResolutionApplyResult:
        """Applies a resolved reconciliation decision atomically and durably commits."""
        window = OrderReconciliationWindow(
            order_id=order.id,
            symbol=getattr(order, "instrument", "XAUUSDT"),
            opened_at=getattr(order, "opened_at", 0) or 0,
            end_as_of=getattr(order, "closed_at", 0) or int(datetime.now(timezone.utc).timestamp() * 1000),
            order=order,
        )
        return persist_resolution_or_review(db, window, decision)

    def process_late_batch(
        self,
        db: Session,
        symbol: str = "XAUUSDT",
        source_state: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Processes late evidence and pending reconciliation windows in chronological order.
        PHẦN 016, 019, 089: Replays persisted window evidence; requires authoritative coverage proof.
        """
        batch = quote_ordering_service.claim_late_evidence_batch(symbol, limit=50)
        sorted_batch = sorted(batch, key=lambda r: r.get("source_timestamp") or 0) if batch else []

        committed_ids = persist_reconciliation_evidence(db, sorted_batch) if sorted_batch else []

        # Load pending windows to assess full historical interval (PHẦN 019, 020, 086)
        window_page = load_pending_reconciliation_windows(db, symbol=symbol, limit=50)
        closed_order_ids: Set[str] = set()

        for window in window_page.items:
            if window.order_id in closed_order_ids:
                continue

            evidence_page = load_persisted_window_evidence(
                db, window, as_of_ms=int(datetime.now(timezone.utc).timestamp() * 1000), limit=200
            )
            # Replay all persisted evidence for this order's window
            all_evidence = evidence_page.items
            if not all_evidence and sorted_batch:
                all_evidence = [r for r in sorted_batch if (r.get("source_timestamp") or 0) >= window.opened_at]

            # Determine target interval end for this order's reconciliation
            max_ev_ts = max((r.get("source_timestamp") or 0 for r in all_evidence), default=window.opened_at)
            eval_end = window.end_as_of if (window.order and window.order.state == "closed" and window.end_as_of > 0) else max_ev_ts
            eval_window = OrderReconciliationWindow(
                order_id=window.order_id,
                symbol=window.symbol,
                opened_at=window.opened_at,
                end_as_of=eval_end,
                source_epoch=window.source_epoch,
                status=window.status,
                cursor=window.cursor,
                order=window.order,
            )

            coverage = load_reconciliation_coverage(db, eval_window, source_state=source_state)
            candidates = collect_terminal_candidates(window.order, all_evidence)
            decision = resolve_reconciliation_window(window.order, candidates, coverage)

            res = persist_resolution_or_review(db, eval_window, decision)
            if decision.action == "CLOSE" and res.applied:
                closed_order_ids.add(window.order_id)

        if committed_ids:
            quote_ordering_service.acknowledge_late_evidence(symbol, evidence_ids=committed_ids)

        return len(committed_ids)


    async def run_late_reconciliation_loop(self, poll_interval_s: float = 2.0):
        """Continuous background reconciliation loop.
        Dispatches synchronous DB batch processing to a worker thread via asyncio.to_thread
        to keep the asyncio event loop completely non-blocking (PHẦN 221).
        """
        self._running = True
        logger.info("MarketReconciliationService background loop started.")
        while self._running:
            try:
                def _worker():
                    db = SessionLocal()
                    try:
                        return self.process_late_batch(db, symbol="XAUUSDT")
                    finally:
                        db.close()

                worker_task = asyncio.create_task(asyncio.to_thread(_worker))
                self._active_worker_task = worker_task
                try:
                    await worker_task
                finally:
                    self._active_worker_task = None
            except Exception as e:
                logger.error(f"Error in late reconciliation loop: {e}", exc_info=True)
            await asyncio.sleep(poll_interval_s)

    async def shutdown(self, timeout_s: float = 5.0) -> ShutdownResult:
        """Gracefully stops the background loop and awaits active worker completion (PHẦN 026, 159)."""
        self._running = False
        task = self._active_worker_task
        if task and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
                return ShutdownResult(status="COMPLETE", in_flight_done=True)
            except asyncio.TimeoutError:
                return ShutdownResult(status="TIMED_OUT", in_flight_done=False, reason="Worker in-flight timeout")
            except Exception as e:
                return ShutdownResult(status="COMPLETE", in_flight_done=True, reason=str(e))
        return ShutdownResult(status="NO_ACTIVE_WORKER", in_flight_done=True)

    def stop(self):
        self._running = False


market_reconciliation_service = MarketReconciliationService()
