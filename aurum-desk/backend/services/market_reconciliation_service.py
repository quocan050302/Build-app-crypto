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
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
from sqlalchemy import and_, or_
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
class CandidateDecodeResult:
    is_valid: bool
    candidate: Optional[TerminalCandidate] = None
    reason: str = ""


def encode_candidate(cand: TerminalCandidate) -> Dict[str, Any]:
    return {
        "order_id": str(cand.order_id),
        "evidence_id": str(cand.evidence_id),
        "source_timestamp": int(cand.source_timestamp),
        "known_at_ms": int(cand.known_at_ms),
        "bid": float(cand.bid) if cand.bid is not None else None,
        "ask": float(cand.ask) if cand.ask is not None else None,
        "last": float(cand.last) if cand.last is not None else None,
        "price_basis": str(cand.price_basis),
        "cause": str(cand.cause),
        "executable_price": float(cand.executable_price),
        "epoch": int(cand.epoch or 1),
        "source": str(cand.source or "UNKNOWN"),
    }


def decode_candidate(data: Any) -> CandidateDecodeResult:
    if not isinstance(data, dict):
        return CandidateDecodeResult(is_valid=False, reason="NOT_A_DICT")
    try:
        order_id = str(data.get("order_id", ""))
        evidence_id = str(data.get("evidence_id", ""))
        cause = str(data.get("cause", ""))
        if not order_id or not evidence_id or not cause:
            return CandidateDecodeResult(is_valid=False, reason="MISSING_REQUIRED_IDENTIFIERS")

        src_ts = data.get("source_timestamp")
        if src_ts is None or isinstance(src_ts, bool) or not isinstance(src_ts, (int, float)):
            return CandidateDecodeResult(is_valid=False, reason="INVALID_SOURCE_TIMESTAMP")

        known_at = data.get("known_at_ms", src_ts)
        if isinstance(known_at, bool) or not isinstance(known_at, (int, float)):
            known_at = src_ts

        exec_px = data.get("executable_price")
        if exec_px is None or isinstance(exec_px, bool) or not isinstance(exec_px, (int, float)):
            return CandidateDecodeResult(is_valid=False, reason="INVALID_EXECUTABLE_PRICE")

        def _parse_opt_px(val: Any) -> Optional[float]:
            if val is None or isinstance(val, bool):
                return None
            if isinstance(val, (int, float)):
                return float(val)
            return None

        bid = _parse_opt_px(data.get("bid"))
        ask = _parse_opt_px(data.get("ask"))
        last = _parse_opt_px(data.get("last"))

        cand = TerminalCandidate(
            order_id=order_id,
            evidence_id=evidence_id,
            source_timestamp=int(src_ts),
            known_at_ms=int(known_at),
            bid=bid,
            ask=ask,
            last=last,
            price_basis=str(data.get("price_basis", "BID_ASK")),
            cause=cause,
            executable_price=float(exec_px),
            epoch=int(data.get("epoch", 1) or 1),
            source=str(data.get("source", "UNKNOWN")),
        )
        return CandidateDecodeResult(is_valid=True, candidate=cand)
    except Exception as e:
        return CandidateDecodeResult(is_valid=False, reason=f"DECODE_ERROR:{e}")


@dataclass(frozen=True)
class CandidateAccumulator:
    schema_version: str = "1.0.0"
    window_id: str = ""
    query_generation: int = 1
    arrival_revision: int = 1
    earliest_source_ts: Optional[int] = None
    earliest_candidates: List[TerminalCandidate] = field(default_factory=list)
    conflict_flags: List[str] = field(default_factory=list)
    processed_interval: Tuple[int, int] = (0, 0)
    validation_context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "window_id": self.window_id,
            "query_generation": self.query_generation,
            "arrival_revision": self.arrival_revision,
            "earliest_source_ts": self.earliest_source_ts,
            "earliest_candidates": [encode_candidate(c) for c in self.earliest_candidates],
            "conflict_flags": list(self.conflict_flags),
            "processed_interval": list(self.processed_interval),
            "validation_context": dict(self.validation_context),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CandidateAccumulator:
        if not isinstance(data, dict):
            return cls()
        raw_cands = data.get("earliest_candidates") or []
        decoded_cands: List[TerminalCandidate] = []
        for rc in raw_cands:
            res = decode_candidate(rc)
            if res.is_valid and res.candidate:
                decoded_cands.append(res.candidate)
        raw_int = data.get("processed_interval") or [0, 0]
        p_int = (int(raw_int[0]), int(raw_int[1])) if len(raw_int) == 2 else (0, 0)
        return cls(
            schema_version=str(data.get("schema_version", "1.0.0")),
            window_id=str(data.get("window_id", "")),
            query_generation=int(data.get("query_generation", 1) or 1),
            arrival_revision=int(data.get("arrival_revision", 1) or 1),
            earliest_source_ts=data.get("earliest_source_ts"),
            earliest_candidates=decoded_cands,
            conflict_flags=list(data.get("conflict_flags") or []),
            processed_interval=p_int,
            validation_context=dict(data.get("validation_context") or {}),
        )


@dataclass(frozen=True)
class AccumulatorMergeResult:
    is_valid: bool
    accumulator: CandidateAccumulator
    reasons: List[str] = field(default_factory=list)


def merge_candidate_accumulator(
    previous: Optional[CandidateAccumulator],
    new_candidates: List[TerminalCandidate],
    window_context: Optional[Dict[str, Any]] = None,
) -> CandidateAccumulator:
    """Pure, deterministic merge of TerminalCandidates preserving earliest terminal group.
    PHẦN 014, 015, 066:
    - Deduplicates by evidence_id.
    - Preserves earliest terminal group: if new candidate has earlier timestamp, replaces earliest group.
    - If new candidate has equal timestamp, retains distinct causes (e.g. SL and TP ambiguity).
    - Later candidates are discarded (do NOT overwrite early candidates).
    """
    ctx = dict(window_context or {})
    w_id = ctx.get("window_id", "")
    query_gen = ctx.get("query_generation", 1)
    arrival_rev = ctx.get("arrival_revision", 1)

    if previous is None:
        previous = CandidateAccumulator(
            window_id=w_id,
            query_generation=query_gen,
            arrival_revision=arrival_rev,
            validation_context=ctx,
        )

    conflict_flags = list(previous.conflict_flags)
    if previous.window_id and w_id and previous.window_id != w_id:
        conflict_flags.append(f"WINDOW_ID_MISMATCH:{previous.window_id}!={w_id}")

    all_cands: List[TerminalCandidate] = list(previous.earliest_candidates) + list(new_candidates)

    seen_ids: Set[str] = set()
    unique_cands: List[TerminalCandidate] = []
    for c in all_cands:
        if c.evidence_id not in seen_ids:
            seen_ids.add(c.evidence_id)
            unique_cands.append(c)

    if not unique_cands:
        return CandidateAccumulator(
            schema_version="1.0.0",
            window_id=previous.window_id or w_id,
            query_generation=query_gen,
            arrival_revision=arrival_rev,
            earliest_source_ts=None,
            earliest_candidates=[],
            conflict_flags=conflict_flags,
            processed_interval=previous.processed_interval,
            validation_context=ctx,
        )

    min_ts = min(c.source_timestamp for c in unique_cands)
    earliest_group = [c for c in unique_cands if c.source_timestamp == min_ts]
    earliest_group.sort(key=lambda c: (c.source_timestamp, c.evidence_id))

    return CandidateAccumulator(
        schema_version="1.0.0",
        window_id=previous.window_id or w_id,
        query_generation=query_gen,
        arrival_revision=arrival_rev,
        earliest_source_ts=min_ts,
        earliest_candidates=earliest_group,
        conflict_flags=conflict_flags,
        processed_interval=previous.processed_interval,
        validation_context=ctx,
    )


def merge_candidate_accumulator_with_result(
    previous: Optional[CandidateAccumulator],
    new_candidates: List[TerminalCandidate],
    window_context: Optional[Dict[str, Any]] = None,
) -> AccumulatorMergeResult:
    acc = merge_candidate_accumulator(previous, new_candidates, window_context)
    is_valid = len(acc.conflict_flags) == 0
    return AccumulatorMergeResult(is_valid=is_valid, accumulator=acc, reasons=acc.conflict_flags)


@dataclass(frozen=True)
class ScanCheckpoint:
    schema_version: str = "1.0.0"
    phase: str = "OPEN"
    last_completed_window_key: Optional[Tuple[int, str]] = None
    resume_window_id: Optional[str] = None
    resume_generation: int = 1
    page_scope: str = "default"
    revision: int = 1
    updated_at: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "phase": self.phase,
            "last_completed_window_key": list(self.last_completed_window_key) if self.last_completed_window_key else None,
            "resume_window_id": self.resume_window_id,
            "resume_generation": self.resume_generation,
            "page_scope": self.page_scope,
            "revision": self.revision,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ScanCheckpoint:
        if not isinstance(data, dict):
            return cls()
        raw_key = data.get("last_completed_window_key")
        key = (int(raw_key[0]), str(raw_key[1])) if (isinstance(raw_key, (list, tuple)) and len(raw_key) == 2) else None
        return cls(
            schema_version=str(data.get("schema_version", "1.0.0")),
            phase=str(data.get("phase", "OPEN")),
            last_completed_window_key=key,
            resume_window_id=data.get("resume_window_id"),
            resume_generation=int(data.get("resume_generation", 1) or 1),
            page_scope=str(data.get("page_scope", "default")),
            revision=int(data.get("revision", 1) or 1),
            updated_at=int(data.get("updated_at", 0) or 0),
        )


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
class WindowCursor:
    schema_version: str = "1.0.0"
    phase: str = "OPEN"  # "OPEN" or "CLOSED"
    last_time: int = 0
    last_order_id: str = ""
    scan_revision: int = 1
    symbol: str = "XAUUSDT"
    resume_window_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "phase": self.phase,
            "last_time": self.last_time,
            "last_order_id": self.last_order_id,
            "scan_revision": self.scan_revision,
            "symbol": self.symbol,
            "resume_window_id": self.resume_window_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WindowCursor:
        return cls(
            schema_version=str(data.get("schema_version", "1.0.0")),
            phase=str(data.get("phase", "OPEN")),
            last_time=int(data.get("last_time", 0) or 0),
            last_order_id=str(data.get("last_order_id", "") or ""),
            scan_revision=int(data.get("scan_revision", 1) or 1),
            symbol=str(data.get("symbol", "XAUUSDT")),
            resume_window_id=data.get("resume_window_id"),
        )


@dataclass
class ReconciliationScanProgress:
    category: str = "DEFAULT"
    window_cursor: Optional[WindowCursor] = None
    query_revision: int = 1
    symbol: str = "XAUUSDT"
    updated_at: int = 0
    reason: str = "INITIAL"
    resume_window_id: Optional[str] = None
    checkpoint: Optional[ScanCheckpoint] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "window_cursor": self.window_cursor.to_dict() if self.window_cursor else None,
            "query_revision": self.query_revision,
            "symbol": self.symbol,
            "updated_at": self.updated_at,
            "reason": self.reason,
            "resume_window_id": self.resume_window_id,
            "checkpoint": self.checkpoint.to_dict() if self.checkpoint else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ReconciliationScanProgress:
        wc_dict = data.get("window_cursor")
        wc = WindowCursor.from_dict(wc_dict) if isinstance(wc_dict, dict) else None
        cp_dict = data.get("checkpoint")
        cp = ScanCheckpoint.from_dict(cp_dict) if isinstance(cp_dict, dict) else None
        return cls(
            category=str(data.get("category", "DEFAULT")),
            window_cursor=wc,
            query_revision=int(data.get("query_revision", 1) or 1),
            symbol=str(data.get("symbol", "XAUUSDT")),
            updated_at=int(data.get("updated_at", 0) or 0),
            reason=str(data.get("reason", "INITIAL")),
            resume_window_id=data.get("resume_window_id"),
            checkpoint=cp,
        )


@dataclass(frozen=True)
class SaveResult:
    success: bool
    revision: int
    reason: str = ""


@dataclass
class EvidenceProgress:
    window_id: str = ""
    source_cursor: Optional[Tuple[int, str]] = None
    arrival_revision: int = 1
    scan_cutoff: int = 0
    candidate_refs: List[str] = field(default_factory=list)
    read_status: str = "PENDING"  # PENDING, MORE_WORK, COMPLETE, REPLAY_NEEDED, RESET
    updated_at: int = 0
    accumulator: Optional[CandidateAccumulator] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "window_id": self.window_id,
            "source_cursor": list(self.source_cursor) if isinstance(self.source_cursor, tuple) else self.source_cursor,
            "arrival_revision": self.arrival_revision,
            "scan_cutoff": self.scan_cutoff,
            "candidate_refs": self.candidate_refs,
            "read_status": self.read_status,
            "updated_at": self.updated_at,
            "accumulator": self.accumulator.to_dict() if self.accumulator else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EvidenceProgress:
        raw_sc = data.get("source_cursor")
        sc = (int(raw_sc[0]), str(raw_sc[1])) if (isinstance(raw_sc, (list, tuple)) and len(raw_sc) == 2) else None
        raw_acc = data.get("accumulator")
        acc = CandidateAccumulator.from_dict(raw_acc) if isinstance(raw_acc, dict) else None
        return cls(
            window_id=str(data.get("window_id", "")),
            source_cursor=sc,
            arrival_revision=int(data.get("arrival_revision", 1) or 1),
            scan_cutoff=int(data.get("scan_cutoff", 0) or 0),
            candidate_refs=list(data.get("candidate_refs") or []),
            read_status=str(data.get("read_status", "PENDING")),
            updated_at=int(data.get("updated_at", 0) or 0),
            accumulator=acc,
        )


@dataclass
class CycleBudget:
    max_windows: int = 50
    max_pages: int = 20
    max_records: int = 4000
    deadline_monotonic: float = 0.0
    pages_processed: int = 0
    records_processed: int = 0
    windows_processed: int = 0

    def allows_page(self) -> bool:
        if self.max_pages <= 0 or self.max_records <= 0:
            return False
        if self.deadline_monotonic > 0 and time.monotonic() >= self.deadline_monotonic:
            return False
        return self.pages_processed < self.max_pages and self.records_processed < self.max_records

    def allows_window(self) -> bool:
        if self.max_windows <= 0:
            return False
        if self.deadline_monotonic > 0 and time.monotonic() >= self.deadline_monotonic:
            return False
        return self.windows_processed < self.max_windows

    def can_start_window_read(self) -> bool:
        return self.allows_window() and self.allows_page()

    def record_page(self, count: int):
        self.pages_processed += 1
        self.records_processed += count

    def record_window(self):
        self.windows_processed += 1


@dataclass(frozen=True)
class WindowSliceResult:
    progressed: bool
    has_more: bool
    next_progress: Optional[EvidenceProgress] = None
    read_complete: bool = False
    decision: Optional[ResolutionDecision] = None
    reasons: List[str] = field(default_factory=list)
    processed_count: int = 0


@dataclass(frozen=True)
class TaskRegistrationResult:
    registered_count: int
    affected_order_ids: List[str]


@dataclass(frozen=True)
class WindowPage:
    items: List[OrderReconciliationWindow]
    next_cursor: Any = None


@dataclass(frozen=True)
class EvidencePage:
    items: List[Dict[str, Any]]
    next_cursor: Any = None


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
            ev_id = r.get("evidence_id") or r.get("event_id") or "UNKNOWN"
            candidates.append(
                TerminalCandidate(
                    order_id=order.id,
                    evidence_id=ev_id,
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
        ev_id = r.get("evidence_id") or r.get("event_id")
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
                aggregate_id=r.get("symbol") or r.get("instrument") or ev_id,
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


def load_scan_progress(db: Session, symbol: str = "XAUUSDT", scan_scope: str = "default") -> ReconciliationScanProgress:
    """PHẦN 030, 085: Loads persistent reconciliation scan progress across coordinator cycles."""
    key = f"recon_scan:{symbol}:{scan_scope}"[:50]
    rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if not rec or not rec.value:
        return ReconciliationScanProgress(category="DEFAULT", window_cursor=None, symbol=symbol)
    try:
        data = json.loads(rec.value)
        return ReconciliationScanProgress.from_dict(data)
    except Exception:
        return ReconciliationScanProgress(category="DEFAULT", window_cursor=None, symbol=symbol)


def save_scan_progress(
    db: Session,
    progress: ReconciliationScanProgress,
    scan_scope: str = "default",
    expected_revision: Optional[int] = None,
) -> SaveResult:
    """PHẦN 030, 085: Saves persistent reconciliation scan progress atomically."""
    key = f"recon_scan:{progress.symbol}:{scan_scope}"[:50]
    rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    current_rev = progress.query_revision
    if rec and expected_revision is not None:
        try:
            curr_data = json.loads(rec.value)
            if curr_data.get("query_revision", 1) != expected_revision:
                return SaveResult(success=False, revision=curr_data.get("query_revision", 1), reason="REVISION_CONFLICT")
        except Exception:
            pass

    progress.updated_at = now_ms
    val_json = json.dumps(progress.to_dict())
    if not rec:
        rec = models.SystemConfig(key=key, value=val_json, updated_at=now_ms)
        db.add(rec)
    else:
        rec.value = val_json
        rec.updated_at = now_ms
    try:
        db.commit()
        return SaveResult(success=True, revision=current_rev)
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to save scan progress for {key}: {e}")
        return SaveResult(success=False, revision=current_rev, reason=str(e))


def load_window_progress(db: Session, window_id: str) -> Optional[EvidenceProgress]:
    """PHẦN 032, 086, 088: Loads evidence progress for a specific reconciliation window."""
    key = f"ev_prog:{window_id}"[:50]
    rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if not rec or not rec.value:
        return None
    try:
        data = json.loads(rec.value)
        return EvidenceProgress.from_dict(data)
    except Exception:
        return None


def save_window_progress(db: Session, progress: EvidenceProgress) -> bool:
    """PHẦN 032, 086, 088: Persists evidence progress for a specific reconciliation window."""
    key = f"ev_prog:{progress.window_id}"[:50]
    rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    progress.updated_at = now_ms
    val_json = json.dumps(progress.to_dict())
    if not rec:
        rec = models.SystemConfig(key=key, value=val_json, updated_at=now_ms)
        db.add(rec)
    else:
        rec.value = val_json
        rec.updated_at = now_ms
    try:
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to save window progress for {key}: {e}")
        return False


def invalidate_affected_window_progress(db: Session, order_ids: List[str], arrival_ts: int) -> int:
    """PHẦN 036, 091, 106: Marks affected windows for replay when late older evidence arrives."""
    invalidated = 0
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    for oid in order_ids:
        prog = load_window_progress(db, oid)
        if prog:
            prog.read_status = "REPLAY_NEEDED"
            prog.updated_at = now_ms
            save_window_progress(db, prog)
            invalidated += 1
    return invalidated


def register_affected_reconciliation_tasks(db: Session, committed_evidence_ids: List[str]) -> TaskRegistrationResult:
    """PHẦN 038, 090: Links durable evidence to relevant orders and registers tasks for replay."""
    if not committed_evidence_ids:
        return TaskRegistrationResult(registered_count=0, affected_order_ids=[])

    events = (
        db.query(models.DomainEvent)
        .filter(models.DomainEvent.event_id.in_([f"late_ev_{eid}" for eid in committed_evidence_ids]))
        .all()
    )
    affected_ids: Set[str] = set()
    for ev in events:
        try:
            payload = json.loads(ev.payload) if isinstance(ev.payload, str) else ev.payload
            ts = payload.get("source_timestamp") or ev.occurred_at
            sym = payload.get("symbol") or payload.get("instrument") or "XAUUSDT"
            matching = (
                db.query(models.PaperOrder)
                .filter(
                    models.PaperOrder.instrument == sym,
                    models.PaperOrder.opened_at <= ts,
                )
                .all()
            )
            for m in matching:
                if m.state == "paper_open" or (m.closed_at and ts <= m.closed_at):
                    affected_ids.add(m.id)
                    invalidate_affected_window_progress(db, [m.id], arrival_ts=ts)
        except Exception:
            continue
    return TaskRegistrationResult(registered_count=len(affected_ids), affected_order_ids=list(affected_ids))



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
    cursor: Any = None,
) -> WindowPage:
    """PHẦN 020, 026, 028, 037, 083, 084, 086:
    Loads active paper_open orders and recently closed orders with phased keyset pagination.
    OPEN phase: opened_at ASC, id ASC with keyset (opened_at > last_time) | (opened_at == last_time & id > last_id).
    CLOSED phase: closed_at DESC, id DESC with keyset (closed_at < last_time) | (closed_at == last_time & id < last_id).
    """
    phase = "OPEN"
    last_time = 0
    last_id = ""

    if cursor is not None:
        if isinstance(cursor, WindowCursor):
            phase = cursor.phase
            last_time = cursor.last_time
            last_id = cursor.last_order_id
        elif isinstance(cursor, tuple) and len(cursor) == 2:
            phase = "OPEN"
            last_time, last_id = cursor
        elif isinstance(cursor, dict):
            phase = cursor.get("phase", "OPEN")
            last_time = cursor.get("last_time", 0) or 0
            last_id = str(cursor.get("last_order_id", "") or "")
        elif isinstance(cursor, int) and cursor > 0:
            phase = "OPEN"
            last_time = cursor
            last_id = ""

    windows: List[OrderReconciliationWindow] = []

    if phase == "OPEN":
        query_open = (
            db.query(models.PaperOrder)
            .filter(
                models.PaperOrder.instrument == symbol,
                models.PaperOrder.state == "paper_open",
            )
        )
        if last_time > 0 or last_id:
            query_open = query_open.filter(
                or_(
                    models.PaperOrder.opened_at > last_time,
                    and_(
                        models.PaperOrder.opened_at == last_time,
                        models.PaperOrder.id > last_id,
                    ),
                )
            )

        open_orders = (
            query_open
            .order_by(models.PaperOrder.opened_at.asc(), models.PaperOrder.id.asc())
            .limit(limit + 1)
            .all()
        )

        has_more_open = len(open_orders) > limit
        if has_more_open:
            open_orders = open_orders[:limit]
            next_cursor = WindowCursor(
                schema_version="1.0.0",
                phase="OPEN",
                last_time=getattr(open_orders[-1], "opened_at", 0) or 0,
                last_order_id=str(open_orders[-1].id),
                scan_revision=1,
                symbol=symbol,
            )
        else:
            next_cursor = None

        for ord in open_orders:
            op_at = getattr(ord, "opened_at", None) or getattr(ord, "created_at", None) or 0
            cl_at = getattr(ord, "closed_at", None) or 0
            windows.append(
                OrderReconciliationWindow(
                    order_id=ord.id,
                    symbol=symbol,
                    opened_at=op_at,
                    end_as_of=cl_at,
                    source_epoch=1,
                    status="PENDING",
                    cursor=op_at,
                    order=ord,
                )
            )

        # If open_orders didn't fill limit and no more open, query recently closed
        if not has_more_open and len(windows) < limit:
            remaining = limit - len(windows)
            query_closed = (
                db.query(models.PaperOrder)
                .filter(
                    models.PaperOrder.instrument == symbol,
                    models.PaperOrder.state == "closed",
                )
            )
            closed_orders = (
                query_closed
                .order_by(models.PaperOrder.closed_at.desc(), models.PaperOrder.id.desc())
                .limit(remaining + 1)
                .all()
            )
            has_more_closed = len(closed_orders) > remaining
            if has_more_closed:
                closed_orders = closed_orders[:remaining]
                next_cursor = WindowCursor(
                    schema_version="1.0.0",
                    phase="CLOSED",
                    last_time=getattr(closed_orders[-1], "closed_at", 0) or 0,
                    last_order_id=str(closed_orders[-1].id),
                    scan_revision=1,
                    symbol=symbol,
                )
            else:
                next_cursor = None

            for ord in closed_orders:
                op_at = getattr(ord, "opened_at", None) or getattr(ord, "created_at", None) or 0
                cl_at = getattr(ord, "closed_at", None) or 0
                windows.append(
                    OrderReconciliationWindow(
                        order_id=ord.id,
                        symbol=symbol,
                        opened_at=op_at,
                        end_as_of=cl_at,
                        source_epoch=1,
                        status="RESOLVED",
                        cursor=op_at,
                        order=ord,
                    )
                )

        return WindowPage(items=windows, next_cursor=next_cursor)

    else:
        # Phase is "CLOSED"
        query_closed = (
            db.query(models.PaperOrder)
            .filter(
                models.PaperOrder.instrument == symbol,
                models.PaperOrder.state == "closed",
            )
        )
        if last_time > 0 or last_id:
            query_closed = query_closed.filter(
                or_(
                    models.PaperOrder.closed_at < last_time,
                    and_(
                        models.PaperOrder.closed_at == last_time,
                        models.PaperOrder.id < last_id,
                    ),
                )
            )

        closed_orders = (
            query_closed
            .order_by(models.PaperOrder.closed_at.desc(), models.PaperOrder.id.desc())
            .limit(limit + 1)
            .all()
        )

        has_more_closed = len(closed_orders) > limit
        if has_more_closed:
            closed_orders = closed_orders[:limit]
            next_cursor = WindowCursor(
                schema_version="1.0.0",
                phase="CLOSED",
                last_time=getattr(closed_orders[-1], "closed_at", 0) or 0,
                last_order_id=str(closed_orders[-1].id),
                scan_revision=1,
                symbol=symbol,
            )
        else:
            next_cursor = None

        for ord in closed_orders:
            op_at = getattr(ord, "opened_at", None) or getattr(ord, "created_at", None) or 0
            cl_at = getattr(ord, "closed_at", None) or 0
            windows.append(
                OrderReconciliationWindow(
                    order_id=ord.id,
                    symbol=symbol,
                    opened_at=op_at,
                    end_as_of=cl_at,
                    source_epoch=1,
                    status="RESOLVED",
                    cursor=op_at,
                    order=ord,
                )
            )

        return WindowPage(items=windows, next_cursor=next_cursor)


def load_persisted_window_evidence(
    db: Session,
    window: OrderReconciliationWindow,
    as_of_ms: int = 0,
    limit: int = 200,
    cursor: Any = None,
) -> EvidencePage:
    """PHẦN 019, 036, 038, 042, 087, 093, 094: Replays persisted late reconciliation evidence with keyset and pre-LIMIT symbol filtering."""
    last_ts = window.opened_at
    last_id = ""
    if cursor is not None:
        if isinstance(cursor, tuple) and len(cursor) == 2:
            last_ts, last_id = cursor
        elif isinstance(cursor, dict):
            last_ts = cursor.get("last_ts", window.opened_at)
            last_id = cursor.get("last_id", "")
        elif isinstance(cursor, int) and cursor > 0:
            last_ts = cursor
        elif hasattr(cursor, "last_ts"):
            last_ts = getattr(cursor, "last_ts", window.opened_at)
            last_id = getattr(cursor, "last_id", "")

    last_ts = max(window.opened_at, last_ts)

    query = (
        db.query(models.DomainEvent)
        .filter(
            models.DomainEvent.event_type == "LATE_RECONCILIATION_EVIDENCE",
        )
    )

    # PHẦN 038, 094: Pre-LIMIT symbol filtering so other symbols cannot starve quota
    if window.symbol:
        query = query.filter(
            or_(
                models.DomainEvent.aggregate_id == window.symbol,
                models.DomainEvent.payload.like(f'%"symbol": "{window.symbol}"%'),
                models.DomainEvent.payload.like(f'%"symbol":"{window.symbol}"%'),
                models.DomainEvent.payload.like(f'%"instrument": "{window.symbol}"%'),
                models.DomainEvent.payload.like(f'%"instrument":"{window.symbol}"%'),
            )
        )

    # PHẦN 036, 042, 094: Keyset condition (occurred_at > last_ts) | (occurred_at == last_ts & event_id > last_id)
    if last_id:
        query = query.filter(
            or_(
                models.DomainEvent.occurred_at > last_ts,
                and_(
                    models.DomainEvent.occurred_at == last_ts,
                    models.DomainEvent.event_id > last_id,
                )
            )
        )
    else:
        query = query.filter(models.DomainEvent.occurred_at >= last_ts)

    if as_of_ms <= 0 and window.end_as_of > 0:
        as_of_ms = window.end_as_of

    if as_of_ms > 0:
        query = query.filter(models.DomainEvent.occurred_at <= as_of_ms)

    events = (
        query
        .order_by(models.DomainEvent.occurred_at.asc(), models.DomainEvent.event_id.asc())
        .limit(limit + 1)
        .all()
    )

    has_more = len(events) > limit
    if has_more:
        events = events[:limit]

    evidence_items: List[Dict[str, Any]] = []
    for ev in events:
        if ev.payload:
            try:
                payload = json.loads(ev.payload) if isinstance(ev.payload, str) else ev.payload
                ev_sym = payload.get("symbol") or payload.get("instrument")
                if not ev_sym or ev_sym == window.symbol:
                    evidence_items.append(payload)
            except Exception:
                continue

    next_cursor = None
    if has_more and events:
        next_cursor = (events[-1].occurred_at, events[-1].event_id)

    return EvidencePage(items=evidence_items, next_cursor=next_cursor)


def _window_cursor_for_item(
    window: OrderReconciliationWindow,
    symbol: str = "XAUUSDT",
    scan_revision: int = 1,
) -> WindowCursor:
    """Helper to construct a keyset WindowCursor anchored to a specific window."""
    is_open = window.order and getattr(window.order, "state", "") == "paper_open"
    if is_open:
        return WindowCursor(
            schema_version="1.0.0",
            phase="OPEN",
            last_time=window.opened_at,
            last_order_id=str(window.order_id),
            scan_revision=scan_revision,
            symbol=symbol,
        )
    else:
        cl_at = (window.order and getattr(window.order, "closed_at", None)) or window.end_as_of or window.opened_at
        return WindowCursor(
            schema_version="1.0.0",
            phase="CLOSED",
            last_time=cl_at,
            last_order_id=str(window.order_id),
            scan_revision=scan_revision,
            symbol=symbol,
        )


def process_window_slice(
    db: Session,
    window: OrderReconciliationWindow,
    progress: Optional[EvidenceProgress] = None,
    budget: Optional[CycleBudget] = None,
    coverage_source: Optional[Dict[str, Any]] = None,
    page_size: int = 200,
) -> WindowSliceResult:
    """PHẦN 010–017, 023, 024, 033, 087, 088: Reads bounded pages, accumulates candidates durably,
    persists progress, and returns complete/has_more/cursor/decision.
    """
    if progress is None:
        progress = load_window_progress(db, window.order_id)
        if (
            progress is None
            or progress.read_status in ("REPLAY_NEEDED", "RESET")
            or (progress.source_cursor is not None and progress.accumulator is None and progress.read_status != "COMPLETE")
        ):
            progress = EvidenceProgress(
                window_id=window.order_id,
                source_cursor=None,
                arrival_revision=1,
                scan_cutoff=window.end_as_of,
                candidate_refs=[],
                read_status="PENDING",
                accumulator=None,
            )

    if budget is None:
        budget = CycleBudget()

    context = {
        "window_id": window.order_id,
        "symbol": window.symbol,
        "direction": getattr(window.order, "direction", "LONG"),
        "opened_at": window.opened_at,
        "end_as_of": window.end_as_of,
        "source_epoch": window.source_epoch,
        "query_generation": 1,
        "arrival_revision": progress.arrival_revision,
    }

    # Preflight zero / exhausted budget check (PHẦN 023, 024, 070, 088)
    if not budget.allows_page():
        return WindowSliceResult(
            progressed=False,
            has_more=True,
            next_progress=progress,
            read_complete=False,
            decision=None,
            reasons=["NO_BUDGET"],
            processed_count=0,
        )

    all_evidence: List[Dict[str, Any]] = []
    current_cursor = progress.source_cursor
    accumulator = progress.accumulator
    has_more = False
    records_before = budget.records_processed

    while budget.allows_page():
        cutoff_ms = progress.scan_cutoff if progress.scan_cutoff > 0 else window.end_as_of
        page = load_persisted_window_evidence(
            db,
            window,
            as_of_ms=cutoff_ms,
            limit=page_size,
            cursor=current_cursor,
        )
        if page.items:
            all_evidence.extend(page.items)
            budget.record_page(len(page.items))
            if window.order:
                page_candidates = collect_terminal_candidates(window.order, page.items)
                accumulator = merge_candidate_accumulator(accumulator, page_candidates, context)

        current_cursor = page.next_cursor
        if not page.next_cursor or not page.items or len(page.items) < page_size:
            has_more = False
            break
        else:
            has_more = True

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    cand_refs = [c.evidence_id for c in accumulator.earliest_candidates] if accumulator else []
    records_in_slice = budget.records_processed - records_before

    if has_more:
        new_progress = EvidenceProgress(
            window_id=window.order_id,
            source_cursor=current_cursor,
            arrival_revision=progress.arrival_revision,
            scan_cutoff=progress.scan_cutoff,
            candidate_refs=cand_refs,
            read_status="MORE_WORK",
            updated_at=now_ms,
            accumulator=accumulator,
        )
        saved = save_window_progress(db, new_progress)
        if not saved:
            return WindowSliceResult(
                progressed=False,
                has_more=True,
                next_progress=progress,
                read_complete=False,
                decision=None,
                reasons=["PROGRESS_SAVE_FAILED"],
                processed_count=0,
            )
        return WindowSliceResult(
            progressed=records_in_slice > 0,
            has_more=True,
            next_progress=new_progress,
            read_complete=False,
            decision=None,
            reasons=["RECONCILIATION_BUDGET_EXHAUSTED"] if records_in_slice > 0 else ["NO_BUDGET"],
            processed_count=records_in_slice,
        )

    new_progress = EvidenceProgress(
        window_id=window.order_id,
        source_cursor=current_cursor,
        arrival_revision=progress.arrival_revision,
        scan_cutoff=progress.scan_cutoff,
        candidate_refs=cand_refs,
        read_status="COMPLETE",
        updated_at=now_ms,
        accumulator=accumulator,
    )
    saved = save_window_progress(db, new_progress)
    if not saved:
        return WindowSliceResult(
            progressed=False,
            has_more=True,
            next_progress=progress,
            read_complete=False,
            decision=None,
            reasons=["PROGRESS_SAVE_FAILED"],
            processed_count=0,
        )

    decision = None
    if window.order:
        coverage = load_reconciliation_coverage(db, window, source_state=coverage_source)
        accumulated_candidates = accumulator.earliest_candidates if accumulator else []
        decision = resolve_reconciliation_window(window.order, accumulated_candidates, coverage)

    return WindowSliceResult(
        progressed=True,
        has_more=False,
        next_progress=new_progress,
        read_complete=True,
        decision=decision,
        reasons=[],
        processed_count=records_in_slice,
    )


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
        budget: Optional[CycleBudget] = None,
    ) -> int:
        """Processes late evidence and pending reconciliation windows in chronological order.
        PHẦN 016, 019, 025, 027, 029, 075, 088, 089: Replays persisted window evidence;
        checkpoints last fully handled window or current unfinished window to prevent skipping items.
        """
        if budget is None:
            budget = CycleBudget()

        batch = quote_ordering_service.claim_late_evidence_batch(symbol, limit=50)
        sorted_batch = sorted(batch, key=lambda r: r.get("source_timestamp") or 0) if batch else []

        committed_ids = persist_reconciliation_evidence(db, sorted_batch) if sorted_batch else []
        if committed_ids:
            register_affected_reconciliation_tasks(db, committed_ids)

        scheduler = load_scan_progress(db, symbol=symbol, scan_scope="default")

        # Load pending windows using persistent scan cursor (PHẦN 019, 020, 029, 086, 089)
        window_page = load_pending_reconciliation_windows(
            db, symbol=symbol, limit=50, cursor=scheduler.window_cursor
        )
        closed_order_ids: Set[str] = set()

        completed_all_items = True
        last_fully_handled_cursor: Optional[WindowCursor] = scheduler.window_cursor
        unfinished_window_id: Optional[str] = None

        for window in window_page.items:
            # Check if budget allows starting a new window read (PHẦN 023, 025, 075)
            if not budget.can_start_window_read():
                completed_all_items = False
                unfinished_window_id = window.order_id
                break

            budget.record_window()

            if window.order_id in closed_order_ids:
                continue

            slice_result = process_window_slice(
                db, window, budget=budget, coverage_source=source_state
            )

            if not slice_result.read_complete:
                completed_all_items = False
                unfinished_window_id = window.order_id
                break

            if slice_result.decision:
                res = persist_resolution_or_review(db, window, slice_result.decision)
                if slice_result.decision.action == "CLOSE" and res.applied:
                    closed_order_ids.add(window.order_id)

            last_fully_handled_cursor = _window_cursor_for_item(window, symbol, scheduler.query_revision)

        # Update scheduler cursor for the next coordinator cycle (PHẦN 027, 029, 075, 089)
        if completed_all_items:
            scheduler.window_cursor = window_page.next_cursor
            scheduler.resume_window_id = None
        else:
            scheduler.window_cursor = last_fully_handled_cursor
            scheduler.resume_window_id = unfinished_window_id

        save_scan_progress(db, scheduler, scan_scope="default")

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
