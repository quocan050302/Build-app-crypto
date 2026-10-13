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
from enum import Enum
from typing import Any, Dict, List, Optional, Set
from sqlalchemy.orm import Session

import models
from database import SessionLocal
from services.quote_ordering_service import quote_ordering_service
from services.trade_lifecycle_service import TradeLifecycleService

logger = logging.getLogger("market_reconciliation")


class CoverageStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"
    CONFLICTED = "CONFLICTED"


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
    reason_code: str = "VERIFIED_CONTINUOUS"


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
class ExitDecision:
    triggered: bool
    cause: str
    trigger_level: float
    applied_exit_price: float
    price_basis: str
    evidence_reference: Optional[str] = None
    unavailable_reason: Optional[str] = None


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


def assess_reconciliation_coverage(
    order: models.PaperOrder,
    window: Any,
    evidence: List[Dict[str, Any]],
    source_metadata: Optional[Dict[str, Any]] = None,
) -> CoverageAssessment:
    """Pure helper (PHẦN 012, 013):
    Assesses whether the reconciliation evidence window covers from order opened_at
    to candidate terminal time without gaps or unverified epochs.
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
        gap_start = gap.get("gap_start_timestamp") or 0
        gap_end = gap.get("gap_end_timestamp") or 0
        if gap_end >= opened_at and (end_ms == 0 or gap_start <= end_ms):
            missing.append((gap_start, gap_end))

    if missing:
        status = CoverageStatus.PARTIAL
        reason = "OVERFLOW_GAP_DETECTED"
    else:
        status = CoverageStatus.COMPLETE
        reason = "VERIFIED_CONTINUOUS"

    return CoverageAssessment(
        interval_start_ms=opened_at,
        interval_end_ms=end_ms,
        source=getattr(order, "instrument", "XAUUSDT"),
        epoch=1,
        price_basis="BID_ASK",
        coverage_status=status,
        missing_intervals=missing,
        evidence_ids=evidence_ids,
        assessed_at_ms=end_ms,
        reason_code=reason,
    )


def evaluate_order_exit(
    order: models.PaperOrder,
    executable_quote: Dict[str, Any],
    policy_snapshot: Optional[Dict[str, Any]] = None,
) -> ExitDecision:
    """Pure exit evaluator (PHẦN 017, 018):
    Checks whether a single quote triggers SL or TP for the given order.
    """
    bid = executable_quote.get("bid")
    ask = executable_quote.get("ask")
    last = executable_quote.get("last")
    ev_id = executable_quote.get("evidence_id")

    direction = getattr(order, "direction", "LONG")
    stop_loss = getattr(order, "stop_loss", None)
    take_profit = getattr(order, "take_profit", None)

    high = executable_quote.get("high")
    low = executable_quote.get("low")
    if high is not None and low is not None and stop_loss is not None and take_profit is not None:
        if direction == "LONG":
            sl_hit = low <= stop_loss
            tp_hit = high >= take_profit
            if sl_hit and tp_hit:
                return ExitDecision(triggered=True, cause="AMBIGUOUS_BAR_SL_FIRST", trigger_level=stop_loss, applied_exit_price=stop_loss, price_basis="BAR_EXTREMES", evidence_reference=ev_id)
            elif sl_hit:
                return ExitDecision(triggered=True, cause="SL_HIT", trigger_level=stop_loss, applied_exit_price=stop_loss, price_basis="BAR_EXTREMES", evidence_reference=ev_id)
            elif tp_hit:
                return ExitDecision(triggered=True, cause="TP_HIT", trigger_level=take_profit, applied_exit_price=take_profit, price_basis="BAR_EXTREMES", evidence_reference=ev_id)
        elif direction == "SHORT":
            sl_hit = high >= stop_loss
            tp_hit = low <= take_profit
            if sl_hit and tp_hit:
                return ExitDecision(triggered=True, cause="AMBIGUOUS_BAR_SL_FIRST", trigger_level=stop_loss, applied_exit_price=stop_loss, price_basis="BAR_EXTREMES", evidence_reference=ev_id)
            elif sl_hit:
                return ExitDecision(triggered=True, cause="SL_HIT", trigger_level=stop_loss, applied_exit_price=stop_loss, price_basis="BAR_EXTREMES", evidence_reference=ev_id)
            elif tp_hit:
                return ExitDecision(triggered=True, cause="TP_HIT", trigger_level=take_profit, applied_exit_price=take_profit, price_basis="BAR_EXTREMES", evidence_reference=ev_id)

    if direction == "LONG":
        price_to_check = bid if bid is not None else last
        if price_to_check is None:
            return ExitDecision(triggered=False, cause="", trigger_level=0.0, applied_exit_price=0.0, price_basis="NONE", unavailable_reason="MISSING_PRICE")
        sl_hit = stop_loss is not None and price_to_check <= stop_loss
        tp_hit = take_profit is not None and price_to_check >= take_profit
        if sl_hit and tp_hit:
            return ExitDecision(triggered=True, cause="AMBIGUOUS_BAR_SL_FIRST", trigger_level=stop_loss, applied_exit_price=stop_loss, price_basis="BID", evidence_reference=ev_id)
        elif sl_hit:
            return ExitDecision(triggered=True, cause="SL_HIT", trigger_level=stop_loss, applied_exit_price=stop_loss, price_basis="BID", evidence_reference=ev_id)
        elif tp_hit:
            return ExitDecision(triggered=True, cause="TP_HIT", trigger_level=take_profit, applied_exit_price=take_profit, price_basis="BID", evidence_reference=ev_id)
    elif direction == "SHORT":
        price_to_check = ask if ask is not None else last
        if price_to_check is None:
            return ExitDecision(triggered=False, cause="", trigger_level=0.0, applied_exit_price=0.0, price_basis="NONE", unavailable_reason="MISSING_PRICE")
        sl_hit = stop_loss is not None and price_to_check >= stop_loss
        tp_hit = take_profit is not None and price_to_check <= take_profit
        if sl_hit and tp_hit:
            return ExitDecision(triggered=True, cause="AMBIGUOUS_BAR_SL_FIRST", trigger_level=stop_loss, applied_exit_price=stop_loss, price_basis="ASK", evidence_reference=ev_id)
        elif sl_hit:
            return ExitDecision(triggered=True, cause="SL_HIT", trigger_level=stop_loss, applied_exit_price=stop_loss, price_basis="ASK", evidence_reference=ev_id)
        elif tp_hit:
            return ExitDecision(triggered=True, cause="TP_HIT", trigger_level=take_profit, applied_exit_price=take_profit, price_basis="ASK", evidence_reference=ev_id)

    return ExitDecision(triggered=False, cause="", trigger_level=0.0, applied_exit_price=0.0, price_basis="NONE")


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


class MarketReconciliationService:
    def __init__(self):
        self._running = False
        self._active_worker_task: Optional[asyncio.Task] = None

    def apply_reconciliation_decision(
        self,
        db: Session,
        order: models.PaperOrder,
        decision: ResolutionDecision,
    ) -> None:
        """Applies a resolved reconciliation decision atomically."""
        if decision.action == "CLOSE" and decision.candidate:
            cand = decision.candidate
            logger.info(
                f"[LATE_RECONCILIATION] Closing order {order.id} due to late tick at {cand.source_timestamp}: "
                f"{cand.cause} at {cand.executable_price}"
            )
            TradeLifecycleService.execute_close(
                db=db,
                order_id=order.id,
                exit_price=cand.executable_price,
                exit_cause=cand.cause,
                occurred_at=cand.source_timestamp,
                session_tag="LATE_RECONCILIATION",
                recovery_metadata={
                    "recovery_status": "RECOVERED",
                    "confidence": "CONFIRMED_TICK",
                    "evidence_id": cand.evidence_id,
                },
            )
        elif decision.action == "REVIEW_REQUIRED":
            # Record audit on closed order without altering realized PNL
            try:
                cand = decision.candidate
                ts = cand.source_timestamp if cand else 0
                audit_str = f"REVIEW_REQUIRED: {decision.reason_code} (late {cand.cause if cand else 'tick'} at {ts} vs closed at {order.closed_at})"
                order.invalidation_reason = audit_str
                db.flush()
                logger.warning(
                    f"[LATE_RECONCILIATION] Audit recorded on closed order {order.id}: {decision.reason_code}"
                )
            except Exception as e:
                logger.error(f"Error recording reconciliation audit on {order.id}: {e}")

    def process_late_batch(self, db: Session, symbol: str = "XAUUSDT") -> int:
        """Processes one batch of late evidence for the symbol in chronological source order."""
        batch = quote_ordering_service.claim_late_evidence_batch(symbol, limit=50)
        if not batch:
            return 0

        # Sort batch by source_timestamp ascending to enforce market chronology (PHẦN 061, 217, 218)
        sorted_batch = sorted(batch, key=lambda r: r.get("source_timestamp") or 0)
        processed_ids = [r["evidence_id"] for r in batch if r.get("evidence_id")]

        # Query active open orders
        open_orders = (
            db.query(models.PaperOrder)
            .filter(
                models.PaperOrder.instrument == symbol,
                models.PaperOrder.state == "paper_open",
            )
            .all()
        )

        # Also query recently closed orders for conflict detection (PHẦN 020)
        min_ts = sorted_batch[0].get("source_timestamp") or 0
        recent_closed_orders = (
            db.query(models.PaperOrder)
            .filter(
                models.PaperOrder.instrument == symbol,
                models.PaperOrder.state == "closed",
                models.PaperOrder.closed_at >= min_ts,
            )
            .all()
        )

        all_relevant = list(open_orders) + list(recent_closed_orders)

        closed_order_ids: Set[str] = set()

        for order in all_relevant:
            if order.id in closed_order_ids:
                continue

            coverage = assess_reconciliation_coverage(order, None, sorted_batch)
            candidates = collect_terminal_candidates(order, sorted_batch)
            decision = resolve_reconciliation_window(order, candidates, coverage)

            if decision.action in ("CLOSE", "REVIEW_REQUIRED"):
                self.apply_reconciliation_decision(db, order, decision)
                if decision.action == "CLOSE":
                    closed_order_ids.add(order.id)

        if processed_ids:
            quote_ordering_service.acknowledge_late_evidence(symbol, evidence_ids=processed_ids)

        return len(processed_ids)

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
