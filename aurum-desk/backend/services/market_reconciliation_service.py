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
import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple, Union
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
class ValidationIssue:
    code: str
    field: str
    detail: str = ""


@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool
    issues: List[ValidationIssue] = field(default_factory=list)


VALID_EXIT_CAUSES: Set[str] = {
    "TP_HIT",
    "SL_HIT",
    "AMBIGUOUS_BAR_SL_FIRST",
    "LIQUIDATED",
    "LIQUIDATION",
    "MANUAL",
    "EXPIRY",
    "GAP_SL",
    "GAP_TP",
    "SL_HIT_RECONCILED",
    "TP_HIT_RECONCILED",
}

VALID_PRICE_BASES: Set[str] = {
    "BID",
    "ASK",
    "BID_ASK",
    "LAST",
    "BAR_EXTREMES",
    "MID",
    "OHLC",
}


def is_finite_real(value: Any) -> bool:
    """Returns True if value is an int or float, not a boolean, and is finite (not NaN, Inf, -Inf)."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def parse_positive_price(
    value: Any,
    field_name: str,
    optional: bool = False,
) -> Tuple[Optional[float], Optional[ValidationIssue]]:
    """Strictly parses a positive finite real price."""
    if value is None:
        if optional:
            return None, None
        return None, ValidationIssue(code="MISSING_PRICE", field=field_name, detail="Price is required")
    if not is_finite_real(value):
        return None, ValidationIssue(
            code="INVALID_PRICE_NON_FINITE",
            field=field_name,
            detail=f"Price must be finite real number, got {value!r}",
        )
    val_float = float(value)
    if val_float <= 0:
        return None, ValidationIssue(
            code="INVALID_PRICE_NON_POSITIVE",
            field=field_name,
            detail=f"Price must be > 0, got {val_float}",
        )
    return val_float, None


def parse_timestamp(
    value: Any,
    field_name: str,
    optional: bool = False,
) -> Tuple[Optional[int], Optional[ValidationIssue]]:
    """Strictly parses a non-negative integer millisecond timestamp."""
    if value is None:
        if optional:
            return None, None
        return None, ValidationIssue(code="MISSING_TIMESTAMP", field=field_name, detail="Timestamp is required")
    if not is_finite_real(value):
        return None, ValidationIssue(
            code="INVALID_TIMESTAMP_TYPE",
            field=field_name,
            detail=f"Timestamp must be real finite int, got {value!r}",
        )
    val_float = float(value)
    if val_float < 0:
        return None, ValidationIssue(
            code="INVALID_TIMESTAMP_NEGATIVE",
            field=field_name,
            detail=f"Timestamp must be >= 0, got {val_float}",
        )
    if int(val_float) != val_float:
        return None, ValidationIssue(
            code="INVALID_TIMESTAMP_FRACTIONAL",
            field=field_name,
            detail=f"Timestamp must be integer ms, got {val_float}",
        )
    return int(val_float), None


@dataclass(frozen=True)
class CandidateDecodeResult:
    is_valid: bool
    candidate: Optional[TerminalCandidate] = None
    reason: str = ""
    issues: List[ValidationIssue] = field(default_factory=list)


def encode_candidate(cand: TerminalCandidate) -> Dict[str, Any]:
    if not is_finite_real(cand.executable_price) or cand.executable_price <= 0:
        raise ValueError(f"Cannot encode candidate with non-finite/non-positive executable_price: {cand.executable_price!r}")
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
        "epoch": int(cand.epoch if isinstance(cand.epoch, int) and not isinstance(cand.epoch, bool) and cand.epoch >= 0 else 1),
        "source": str(cand.source or "UNKNOWN"),
    }


def decode_candidate(data: Any) -> CandidateDecodeResult:
    if not isinstance(data, dict):
        return CandidateDecodeResult(
            is_valid=False,
            reason="NOT_A_DICT",
            issues=[ValidationIssue(code="NOT_A_DICT", field="candidate", detail=f"Expected dict, got {type(data)}")],
        )
    issues: List[ValidationIssue] = []

    # 1. Identifiers
    order_id = data.get("order_id")
    if not isinstance(order_id, str) or not order_id.strip():
        issues.append(ValidationIssue(code="MISSING_ORDER_ID", field="order_id", detail=f"Invalid order_id: {order_id!r}"))

    evidence_id = data.get("evidence_id")
    if not isinstance(evidence_id, str) or not evidence_id.strip() or evidence_id == "UNKNOWN":
        issues.append(ValidationIssue(code="MISSING_OR_UNKNOWN_EVIDENCE_ID", field="evidence_id", detail=f"Invalid evidence_id: {evidence_id!r}"))

    cause = data.get("cause")
    if not isinstance(cause, str) or cause not in VALID_EXIT_CAUSES:
        issues.append(ValidationIssue(code="INVALID_EXIT_CAUSE", field="cause", detail=f"Invalid exit cause: {cause!r}"))

    price_basis = data.get("price_basis", "BID_ASK")
    if not isinstance(price_basis, str) or price_basis not in VALID_PRICE_BASES:
        issues.append(ValidationIssue(code="INVALID_PRICE_BASIS", field="price_basis", detail=f"Invalid price basis: {price_basis!r}"))

    # 2. Timestamps
    src_ts, ts_issue = parse_timestamp(data.get("source_timestamp"), "source_timestamp", optional=False)
    if ts_issue:
        issues.append(ts_issue)

    known_at, known_issue = parse_timestamp(data.get("known_at_ms"), "known_at_ms", optional=True)
    if known_issue:
        issues.append(known_issue)
    elif known_at is None:
        known_at = src_ts if src_ts is not None else 0

    # 3. Numeric prices
    exec_px, exec_issue = parse_positive_price(data.get("executable_price"), "executable_price", optional=False)
    if exec_issue:
        issues.append(exec_issue)

    bid, bid_issue = parse_positive_price(data.get("bid"), "bid", optional=True)
    if bid_issue:
        issues.append(bid_issue)

    ask, ask_issue = parse_positive_price(data.get("ask"), "ask", optional=True)
    if ask_issue:
        issues.append(ask_issue)

    last, last_issue = parse_positive_price(data.get("last"), "last", optional=True)
    if last_issue:
        issues.append(last_issue)

    # 4. Epoch & Source
    epoch_raw = data.get("epoch", 1)
    if not isinstance(epoch_raw, int) or isinstance(epoch_raw, bool) or epoch_raw < 0:
        issues.append(ValidationIssue(code="INVALID_SOURCE_EPOCH", field="epoch", detail=f"Epoch must be non-negative int, got {epoch_raw!r}"))
        epoch = 1
    else:
        epoch = epoch_raw

    source_raw = data.get("source", "UNKNOWN")
    if not isinstance(source_raw, str) or not source_raw.strip():
        issues.append(ValidationIssue(code="INVALID_SOURCE", field="source", detail=f"Source must be non-empty string, got {source_raw!r}"))
        source = "UNKNOWN"
    else:
        source = source_raw

    if issues:
        primary_reason = issues[0].code
        return CandidateDecodeResult(
            is_valid=False,
            candidate=None,
            reason=primary_reason,
            issues=issues,
        )

    cand = TerminalCandidate(
        order_id=str(order_id),
        evidence_id=str(evidence_id),
        source_timestamp=src_ts,
        known_at_ms=known_at,
        bid=bid,
        ask=ask,
        last=last,
        price_basis=str(price_basis),
        cause=str(cause),
        executable_price=exec_px,
        epoch=epoch,
        source=source,
    )
    return CandidateDecodeResult(is_valid=True, candidate=cand)


@dataclass(frozen=True)
class AccumulatorDecodeResult:
    is_valid: bool
    accumulator: Optional[CandidateAccumulator] = None
    issues: List[ValidationIssue] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)


def decode_candidate_accumulator(data: Any) -> AccumulatorDecodeResult:
    if not isinstance(data, dict):
        iss = ValidationIssue(code="NOT_A_DICT", field="accumulator", detail=f"Expected dict, got {type(data)}")
        return AccumulatorDecodeResult(is_valid=False, issues=[iss], reasons=[iss.code])

    issues: List[ValidationIssue] = []

    schema_ver = str(data.get("schema_version", "1.0.0"))
    if schema_ver not in ("1.0.0",):
        issues.append(ValidationIssue(code="UNSUPPORTED_SCHEMA_VERSION", field="schema_version", detail=f"Schema {schema_ver} not supported"))

    window_id = str(data.get("window_id", ""))

    query_gen_raw = data.get("query_generation", 1)
    if not isinstance(query_gen_raw, int) or isinstance(query_gen_raw, bool) or query_gen_raw < 1:
        issues.append(ValidationIssue(code="INVALID_QUERY_GENERATION", field="query_generation", detail=f"Invalid query generation: {query_gen_raw!r}"))
        query_gen = 1
    else:
        query_gen = query_gen_raw

    arrival_rev_raw = data.get("arrival_revision", 1)
    if not isinstance(arrival_rev_raw, int) or isinstance(arrival_rev_raw, bool) or arrival_rev_raw < 1:
        issues.append(ValidationIssue(code="INVALID_ARRIVAL_REVISION", field="arrival_revision", detail=f"Invalid arrival revision: {arrival_rev_raw!r}"))
        arrival_rev = 1
    else:
        arrival_rev = arrival_rev_raw

    raw_cands = data.get("earliest_candidates")
    if raw_cands is None:
        raw_cands = []
    elif not isinstance(raw_cands, list):
        issues.append(ValidationIssue(code="INVALID_CANDIDATES_LIST", field="earliest_candidates", detail=f"Expected list, got {type(raw_cands)}"))
        raw_cands = []

    decoded_cands: List[TerminalCandidate] = []
    for idx, rc in enumerate(raw_cands):
        c_res = decode_candidate(rc)
        if not c_res.is_valid or not c_res.candidate:
            issues.append(ValidationIssue(
                code="CORRUPT_CANDIDATE",
                field=f"earliest_candidates[{idx}]",
                detail=f"Candidate failed decode: {c_res.reason}",
            ))
        else:
            decoded_cands.append(c_res.candidate)

    raw_int = data.get("processed_interval") or [0, 0]
    if isinstance(raw_int, (list, tuple)) and len(raw_int) == 2 and is_finite_real(raw_int[0]) and is_finite_real(raw_int[1]):
        p_int = (int(raw_int[0]), int(raw_int[1]))
    else:
        issues.append(ValidationIssue(code="INVALID_PROCESSED_INTERVAL", field="processed_interval", detail=f"Invalid interval: {raw_int!r}"))
        p_int = (0, 0)

    raw_ctx = data.get("validation_context")
    if raw_ctx is None:
        val_ctx = {}
    elif isinstance(raw_ctx, dict):
        val_ctx = dict(raw_ctx)
    else:
        issues.append(ValidationIssue(code="INVALID_VALIDATION_CONTEXT", field="validation_context", detail=f"Expected dict, got {type(raw_ctx)}"))
        val_ctx = {}

    raw_flags = data.get("conflict_flags")
    conflict_flags = list(raw_flags) if isinstance(raw_flags, list) else []

    raw_earliest_ts = data.get("earliest_source_ts")
    if decoded_cands:
        expected_min_ts = min(c.source_timestamp for c in decoded_cands)
        if raw_earliest_ts != expected_min_ts:
            issues.append(ValidationIssue(
                code="EARLIEST_SOURCE_TS_MISMATCH",
                field="earliest_source_ts",
                detail=f"Stored ts {raw_earliest_ts} != min ts {expected_min_ts}",
            ))
        for idx, c in enumerate(decoded_cands):
            if c.source_timestamp != expected_min_ts:
                issues.append(ValidationIssue(
                    code="CANDIDATE_NOT_IN_EARLIEST_GROUP",
                    field=f"earliest_candidates[{idx}]",
                    detail=f"Candidate ts {c.source_timestamp} != min ts {expected_min_ts}",
                ))
    else:
        if raw_earliest_ts is not None:
            issues.append(ValidationIssue(
                code="EMPTY_CANDIDATES_WITH_NON_NULL_TS",
                field="earliest_source_ts",
                detail=f"Expected None for empty candidates, got {raw_earliest_ts}",
            ))

    reasons = [iss.code for iss in issues]
    all_flags = list(conflict_flags) + reasons

    acc = CandidateAccumulator(
        schema_version=schema_ver,
        window_id=window_id,
        query_generation=query_gen,
        arrival_revision=arrival_rev,
        earliest_source_ts=raw_earliest_ts if not issues else None,
        earliest_candidates=decoded_cands if not issues else [],
        conflict_flags=all_flags,
        processed_interval=p_int,
        validation_context=val_ctx,
    )

    if issues:
        return AccumulatorDecodeResult(
            is_valid=False,
            accumulator=acc,
            issues=issues,
            reasons=reasons,
        )

    return AccumulatorDecodeResult(is_valid=True, accumulator=acc)


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
        res = decode_candidate_accumulator(data)
        if res.accumulator:
            return res.accumulator
        return cls(conflict_flags=["CORRUPT_ACCUMULATOR_PAYLOAD"])


@dataclass(frozen=True)
class FrozenExitContextResult:
    status: str  # "VALID", "UNVERIFIED", "INVALID"
    order_id: str
    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    exit_policy_version: str
    policy_config_version: int = 1
    fingerprint: str = ""
    issues: List[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return self.status == "VALID"


def canonical_num(val: Any) -> str:
    """Formats numeric value deterministically for hashing/fingerprinting."""
    if val is None:
        return "none"
    try:
        f = float(val)
        if not math.isfinite(f):
            return "nan"
        return f"{f:.8f}".rstrip("0").rstrip(".")
    except Exception:
        return str(val)


def fingerprint_exit_context(ctx: Union[FrozenExitContextResult, Dict[str, Any]]) -> str:
    """Computes stable, deterministic SHA256 fingerprint for frozen exit context."""
    if isinstance(ctx, FrozenExitContextResult):
        if not ctx.is_valid and ctx.status != "UNVERIFIED":
            return ""
        order_id = str(ctx.order_id).strip()
        symbol = str(ctx.symbol).strip().upper()
        direction = str(ctx.direction).strip().upper()
        entry = canonical_num(ctx.entry_price)
        sl = canonical_num(ctx.stop_loss)
        tp = canonical_num(ctx.take_profit)
        version = str(ctx.exit_policy_version).strip()
    elif isinstance(ctx, dict):
        order_id = str(ctx.get("order_id") or ctx.get("window_id") or "").strip()
        symbol = str(ctx.get("symbol") or "").strip().upper()
        direction = str(ctx.get("direction") or "").strip().upper()
        entry = canonical_num(ctx.get("entry_price") or ctx.get("actual_entry") or ctx.get("planned_entry"))
        sl = canonical_num(ctx.get("stop_loss"))
        tp = canonical_num(ctx.get("take_profit"))
        version = str(ctx.get("exit_policy_version") or "1.0.0").strip()
    else:
        return ""

    payload = f"{order_id}|{symbol}|{direction}|{entry}|{sl}|{tp}|{version}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def read_frozen_exit_context(order: Any) -> FrozenExitContextResult:
    """Pure extraction of order ID, symbol, direction, entry, SL, TP and actual exit-policy version."""
    if order is None:
        return FrozenExitContextResult(
            status="INVALID",
            order_id="",
            symbol="",
            direction="",
            entry_price=0.0,
            stop_loss=0.0,
            take_profit=0.0,
            exit_policy_version="",
            issues=["MISSING_ORDER"],
        )

    order_id = str(getattr(order, "id", "") or "")
    symbol = str(getattr(order, "symbol", "") or getattr(order, "instrument", "") or "").upper()
    direction = str(getattr(order, "direction", "") or "").upper()

    snapshot_raw = getattr(order, "cost_snapshot", None)
    snapshot_dict: Optional[Dict[str, Any]] = None
    if snapshot_raw is not None:
        if isinstance(snapshot_raw, str):
            try:
                parsed = json.loads(snapshot_raw)
            except Exception:
                return FrozenExitContextResult(
                    status="INVALID",
                    order_id=order_id,
                    symbol=symbol,
                    direction=direction,
                    entry_price=0.0,
                    stop_loss=0.0,
                    take_profit=0.0,
                    exit_policy_version="",
                    issues=["CORRUPT_COST_SNAPSHOT_JSON"],
                )
            if not isinstance(parsed, dict):
                return FrozenExitContextResult(
                    status="INVALID",
                    order_id=order_id,
                    symbol=symbol,
                    direction=direction,
                    entry_price=0.0,
                    stop_loss=0.0,
                    take_profit=0.0,
                    exit_policy_version="",
                    issues=["ROOT_NOT_DICT"],
                )
            snapshot_dict = parsed
        elif isinstance(snapshot_raw, dict):
            snapshot_dict = snapshot_raw
        else:
            return FrozenExitContextResult(
                status="INVALID",
                order_id=order_id,
                symbol=symbol,
                direction=direction,
                entry_price=0.0,
                stop_loss=0.0,
                take_profit=0.0,
                exit_policy_version="",
                issues=["ROOT_NOT_DICT"],
            )

    exit_policy_version = "1.0.0"
    policy_config_version = 1
    if snapshot_dict is not None:
        direction = str(snapshot_dict.get("direction") or direction).upper()
        entry_val = snapshot_dict.get("actual_entry") if snapshot_dict.get("actual_entry") is not None else snapshot_dict.get("planned_entry")
        sl_val = snapshot_dict.get("stop_loss")
        tp_val = snapshot_dict.get("take_profit")
        exit_policy_version = str(snapshot_dict.get("cost_source") or snapshot_dict.get("cost_version") or snapshot_dict.get("policy_config_version") or "1.0.0")
        policy_config_version = int(snapshot_dict.get("policy_config_version") or 1)
        is_legacy = False
    else:
        entry_val = getattr(order, "entry_price", None) or getattr(order, "actual_entry", None) or getattr(order, "planned_entry", None)
        sl_val = getattr(order, "stop_loss", None)
        tp_val = getattr(order, "take_profit", None)
        exit_policy_version = "legacy_1.0.0"
        is_legacy = True

    issues: List[str] = []
    if not order_id:
        issues.append("MISSING_ORDER_ID")
    if not symbol:
        issues.append("MISSING_SYMBOL")
    if direction not in ("LONG", "SHORT"):
        issues.append("INVALID_DIRECTION")

    for name, val in [("entry_price", entry_val), ("stop_loss", sl_val), ("take_profit", tp_val)]:
        if not is_finite_real(val) or float(val) <= 0:
            issues.append(f"INVALID_{name.upper()}")

    entry_f = float(entry_val) if is_finite_real(entry_val) else 0.0
    sl_f = float(sl_val) if is_finite_real(sl_val) else 0.0
    tp_f = float(tp_val) if is_finite_real(tp_val) else 0.0

    if not issues:
        if direction == "LONG":
            if sl_f >= entry_f:
                issues.append("LONG_SL_GTE_ENTRY")
            if tp_f <= entry_f:
                issues.append("LONG_TP_LTE_ENTRY")
        elif direction == "SHORT":
            if sl_f <= entry_f:
                issues.append("SHORT_SL_LTE_ENTRY")
            if tp_f >= entry_f:
                issues.append("SHORT_TP_GTE_ENTRY")

    status = "INVALID" if issues else ("UNVERIFIED" if is_legacy else "VALID")

    result = FrozenExitContextResult(
        status=status,
        order_id=order_id,
        symbol=symbol,
        direction=direction,
        entry_price=entry_f,
        stop_loss=sl_f,
        take_profit=tp_f,
        exit_policy_version=exit_policy_version,
        policy_config_version=policy_config_version,
        issues=issues,
    )
    if status in ("VALID", "UNVERIFIED"):
        fp = fingerprint_exit_context(result)
        result = FrozenExitContextResult(
            status=result.status,
            order_id=result.order_id,
            symbol=result.symbol,
            direction=result.direction,
            entry_price=result.entry_price,
            stop_loss=result.stop_loss,
            take_profit=result.take_profit,
            exit_policy_version=result.exit_policy_version,
            policy_config_version=result.policy_config_version,
            fingerprint=fp,
            issues=result.issues,
        )
    return result


@dataclass(frozen=True)
class ContextBuildResult:
    status: str  # "VALID", "INVALID", "UNVERIFIED"
    context: Optional[Dict[str, Any]]
    issues: List[str] = field(default_factory=list)
    source_status: str = "UNKNOWN"


REQUIRED_CONTEXT_FIELDS: List[str] = [
    "context_schema_version",
    "order_id",
    "window_id",
    "symbol",
    "direction",
    "opened_at",
    "scan_cutoff",
    "source_epoch",
    "query_generation",
    "arrival_revision",
    "exit_policy_version",
    "exit_context_fingerprint",
]


def validate_context_schema(
    payload: Any,
    mode: str = "STRICT",
) -> ValidationResult:
    """PHẦN 025, 026, 054: Validates context dictionary schema strictly."""
    issues: List[ValidationIssue] = []
    if not isinstance(payload, dict) or not payload:
        issues.append(ValidationIssue(code="EMPTY_CONTEXT", field="payload", detail="Context must be a non-empty dictionary"))
        return ValidationResult(is_valid=False, issues=issues)

    if mode == "STRICT":
        # Check presence and nullability of all required fields
        for f in REQUIRED_CONTEXT_FIELDS:
            if f not in payload:
                issues.append(ValidationIssue(
                    code=f"MISSING_FIELD_{f.upper()}",
                    field=f,
                    detail=f"Field {f} is missing in context"
                ))
            elif payload[f] is None:
                issues.append(ValidationIssue(
                    code=f"NULL_FIELD_{f.upper()}",
                    field=f,
                    detail=f"Field {f} cannot be null"
                ))

        if issues:
            return ValidationResult(is_valid=False, issues=issues)

        # String fields: order_id, window_id, symbol, direction, exit_policy_version, exit_context_fingerprint
        for str_f in ["order_id", "window_id", "symbol", "direction", "exit_policy_version", "exit_context_fingerprint"]:
            val = payload.get(str_f)
            if not isinstance(val, str) or not val.strip():
                issues.append(ValidationIssue(
                    code=f"INVALID_TYPE_{str_f.upper()}",
                    field=str_f,
                    detail=f"{str_f} must be a non-empty string"
                ))

        # direction must be LONG or SHORT
        direction_val = str(payload.get("direction", "")).strip().upper()
        if direction_val not in ("LONG", "SHORT"):
            issues.append(ValidationIssue(
                code="INVALID_DIRECTION",
                field="direction",
                detail=f"direction must be LONG or SHORT, got {payload.get('direction')}"
            ))

        # context_schema_version must be str or int, not bool
        csv = payload.get("context_schema_version")
        if isinstance(csv, bool) or not (isinstance(csv, str) or isinstance(csv, int)):
            issues.append(ValidationIssue(
                code="INVALID_TYPE_CONTEXT_SCHEMA_VERSION",
                field="context_schema_version",
                detail="context_schema_version must be str or int"
            ))

        # Positive ints, not bool: source_epoch, query_generation, arrival_revision
        for int_pos_f in ["source_epoch", "query_generation", "arrival_revision"]:
            v = payload.get(int_pos_f)
            if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                issues.append(ValidationIssue(
                    code=f"INVALID_VALUE_{int_pos_f.upper()}",
                    field=int_pos_f,
                    detail=f"{int_pos_f} must be a positive integer, got {type(v).__name__}: {v}"
                ))

        # Non-negative timestamps, not bool: opened_at, scan_cutoff
        for ts_f in ["opened_at", "scan_cutoff"]:
            v = payload.get(ts_f)
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                issues.append(ValidationIssue(
                    code=f"INVALID_VALUE_{ts_f.upper()}",
                    field=ts_f,
                    detail=f"{ts_f} must be a non-negative integer, got {type(v).__name__}: {v}"
                ))

        opened_at = payload.get("opened_at")
        scan_cutoff = payload.get("scan_cutoff")
        if (
            isinstance(opened_at, int) and not isinstance(opened_at, bool)
            and isinstance(scan_cutoff, int) and not isinstance(scan_cutoff, bool)
        ):
            if scan_cutoff > 0 and opened_at > scan_cutoff:
                issues.append(ValidationIssue(
                    code="OPENED_AT_EXCEEDS_SCAN_CUTOFF",
                    field="opened_at",
                    detail=f"opened_at {opened_at} cannot exceed scan_cutoff {scan_cutoff}"
                ))

    return ValidationResult(is_valid=len(issues) == 0, issues=issues)


def validate_context_compatibility(
    stored: Any,
    expected: Any,
    migration_mode: str = "STRICT",
) -> ValidationResult:
    """PHẦN 003, 025, 027, 028: Validates compatibility between stored validation_context and expected context."""
    issues: List[ValidationIssue] = []
    if not isinstance(stored, dict) or not stored:
        issues.append(ValidationIssue(code="EMPTY_VALIDATION_CONTEXT", field="validation_context", detail="Stored context is empty"))
        return ValidationResult(is_valid=False, issues=issues)
    if not isinstance(expected, dict) or not expected:
        issues.append(ValidationIssue(code="EMPTY_EXPECTED_CONTEXT", field="expected_context", detail="Expected context is empty"))
        return ValidationResult(is_valid=False, issues=issues)

    if migration_mode == "STRICT":
        s_val = validate_context_schema(stored, mode="STRICT")
        e_val = validate_context_schema(expected, mode="STRICT")
        if not s_val.is_valid or not e_val.is_valid:
            return ValidationResult(is_valid=False, issues=list(s_val.issues) + list(e_val.issues))

        for f in REQUIRED_CONTEXT_FIELDS:
            s_item = stored.get(f)
            e_item = expected.get(f)

            mismatch = False
            if f in ("symbol", "direction"):
                if str(s_item or "").strip().upper() != str(e_item or "").strip().upper():
                    mismatch = True
            elif f in ("order_id", "window_id", "exit_policy_version", "exit_context_fingerprint", "context_schema_version"):
                if str(s_item or "").strip() != str(e_item or "").strip():
                    mismatch = True
            else:
                if s_item != e_item:
                    mismatch = True

            if mismatch:
                code_map = {
                    "scan_cutoff": "CONTEXT_CUTOFF_MISMATCH",
                    "exit_context_fingerprint": "CONTEXT_FINGERPRINT_MISMATCH",
                }
                code = code_map.get(f, f"CONTEXT_{f.upper()}_MISMATCH")
                issues.append(ValidationIssue(
                    code=code,
                    field=f,
                    detail=f"Stored {s_item} != expected {e_item}"
                ))

        return ValidationResult(is_valid=len(issues) == 0, issues=issues)

    elif migration_mode == "LEGACY":
        # Explicit authorized legacy migration branch (PHẦN 027, 031)
        if not isinstance(stored, dict) or not stored:
            return ValidationResult(is_valid=False, issues=[ValidationIssue(code="EMPTY_VALIDATION_CONTEXT", field="validation_context")])
        if not isinstance(expected, dict) or not expected:
            return ValidationResult(is_valid=False, issues=[ValidationIssue(code="EMPTY_EXPECTED_CONTEXT", field="expected_context")])

        for f in ["order_id", "symbol", "direction"]:
            s_item = str(stored.get(f) or "").strip().upper() if stored else ""
            e_item = str(expected.get(f) or "").strip().upper() if expected else ""
            if s_item != e_item:
                issues.append(ValidationIssue(
                    code=f"CONTEXT_{f.upper()}_MISMATCH",
                    field=f,
                    detail=f"Stored {s_item} != expected {e_item}"
                ))
        return ValidationResult(is_valid=len(issues) == 0, issues=issues)

    else:
        issues.append(ValidationIssue(
            code="UNSUPPORTED_MIGRATION_MODE",
            field="migration_mode",
            detail=f"Unsupported migration mode {migration_mode}"
        ))
        return ValidationResult(is_valid=False, issues=issues)


def build_reconciliation_context_result(
    window: OrderReconciliationWindow,
    progress: Optional[EvidenceProgress] = None,
) -> ContextBuildResult:
    """PHẦN 014, 015, 019: Strict reconciliation context builder from verified frozen sources."""
    if not window.order:
        return ContextBuildResult(
            status="INVALID",
            context=None,
            issues=["MISSING_ORDER"],
            source_status="INVALID",
        )

    frozen = read_frozen_exit_context(window.order)
    if frozen.status == "INVALID":
        return ContextBuildResult(
            status="INVALID",
            context=None,
            issues=list(frozen.issues),
            source_status="INVALID",
        )

    issues: List[str] = []
    if str(window.order_id) != str(frozen.order_id):
        issues.append("FIELD_SOURCE_CONFLICT_ORDER_ID")
    if str(window.symbol).upper() != str(frozen.symbol).upper():
        issues.append("FIELD_SOURCE_CONFLICT_SYMBOL")

    ord_dir = str(getattr(window.order, "direction", "") or "").upper()
    if ord_dir and ord_dir != frozen.direction:
        issues.append("FIELD_SOURCE_CONFLICT_DIRECTION")

    if issues:
        return ContextBuildResult(
            status="INVALID",
            context=None,
            issues=issues,
            source_status=frozen.status,
        )

    cutoff = progress.scan_cutoff if (progress and progress.scan_cutoff > 0) else window.end_as_of
    rev = progress.arrival_revision if progress else 1
    query_gen = progress.accumulator.query_generation if (progress and progress.accumulator and progress.accumulator.query_generation) else 1

    fp = frozen.fingerprint or fingerprint_exit_context(frozen)

    ctx = {
        "context_schema_version": "1.0.0",
        "order_id": window.order_id,
        "window_id": window.order_id,
        "symbol": frozen.symbol,
        "direction": frozen.direction,
        "opened_at": window.opened_at,
        "scan_cutoff": cutoff,
        "source_epoch": window.source_epoch,
        "query_generation": query_gen,
        "arrival_revision": rev,
        "exit_policy_version": frozen.exit_policy_version,
        "exit_context_fingerprint": fp,
    }

    schema_val = validate_context_schema(ctx, mode="STRICT")
    if not schema_val.is_valid:
        return ContextBuildResult(
            status="INVALID",
            context=None,
            issues=[iss.code for iss in schema_val.issues],
            source_status=frozen.status,
        )

    if frozen.status == "UNVERIFIED":
        return ContextBuildResult(
            status="UNVERIFIED",
            context=ctx,
            issues=list(frozen.issues),
            source_status="UNVERIFIED",
        )

    return ContextBuildResult(
        status="VALID",
        context=ctx,
        issues=[],
        source_status="VALID",
    )


def build_reconciliation_context(
    window: OrderReconciliationWindow,
    progress: Optional[EvidenceProgress] = None,
) -> Optional[Dict[str, Any]]:
    """Legacy wrapper delegating to strict builder."""
    res = build_reconciliation_context_result(window, progress)
    return res.context


def validate_candidate_context(
    candidate: TerminalCandidate,
    window: OrderReconciliationWindow,
    context: Optional[Dict[str, Any]] = None,
) -> ValidationResult:
    issues: List[ValidationIssue] = []
    expected_order_id = str(window.order.id if window.order else window.order_id)
    if candidate.order_id != expected_order_id:
        issues.append(ValidationIssue(
            code="ORDER_ID_MISMATCH",
            field="order_id",
            detail=f"Candidate order {candidate.order_id} != window order {expected_order_id}",
        ))
    if candidate.source_timestamp < window.opened_at:
        issues.append(ValidationIssue(
            code="TIMESTAMP_BEFORE_ORDER_OPEN",
            field="source_timestamp",
            detail=f"Candidate ts {candidate.source_timestamp} < opened_at {window.opened_at}",
        ))
    if candidate.epoch != window.source_epoch:
        issues.append(ValidationIssue(
            code="EPOCH_MISMATCH",
            field="epoch",
            detail=f"Candidate epoch {candidate.epoch} != window epoch {window.source_epoch}",
        ))
    if candidate.source == "UNKNOWN":
        issues.append(ValidationIssue(
            code="UNKNOWN_PROVENANCE_SOURCE",
            field="source",
            detail="Candidate has UNKNOWN provenance source",
        ))

    # PHẦN 055: source_timestamp <= frozen cutoff
    cutoff = (context.get("scan_cutoff") if context else None) or window.end_as_of
    if cutoff and candidate.source_timestamp > cutoff:
        issues.append(ValidationIssue(
            code="TIMESTAMP_AFTER_SCAN_CUTOFF",
            field="source_timestamp",
            detail=f"Candidate ts {candidate.source_timestamp} > cutoff {cutoff}",
        ))

    # PHẦN 055: Price & basis validation
    if not is_finite_real(candidate.executable_price) or candidate.executable_price <= 0:
        issues.append(ValidationIssue(
            code="NON_FINITE_CANDIDATE_PRICE",
            field="executable_price",
            detail="Candidate price is non-finite or <= 0",
        ))
    if candidate.price_basis not in VALID_PRICE_BASES:
        issues.append(ValidationIssue(
            code="INVALID_PRICE_BASIS",
            field="price_basis",
            detail=f"Invalid price basis {candidate.price_basis}",
        ))
    if candidate.cause not in VALID_EXIT_CAUSES:
        issues.append(ValidationIssue(
            code="INVALID_EXIT_CAUSE",
            field="cause",
            detail=f"Invalid exit cause {candidate.cause}",
        ))

    return ValidationResult(is_valid=len(issues) == 0, issues=issues)


def validate_accumulator_context(
    accumulator: CandidateAccumulator,
    window: OrderReconciliationWindow,
    context: Optional[Dict[str, Any]] = None,
) -> ValidationResult:
    issues: List[ValidationIssue] = []
    expected_order_id = str(window.order.id if window.order else window.order_id)
    if accumulator.window_id and accumulator.window_id != expected_order_id:
        issues.append(ValidationIssue(
            code="WINDOW_ID_MISMATCH",
            field="window_id",
            detail=f"Accumulator window_id {accumulator.window_id} != expected {expected_order_id}",
        ))
    if accumulator.conflict_flags:
        for flag in accumulator.conflict_flags:
            issues.append(ValidationIssue(code=flag, field="conflict_flags"))

    # Validate stored validation_context against expected context (PHẦN 001, 003, 052, 054)
    if context is not None:
        if not accumulator.validation_context:
            issues.append(ValidationIssue(
                code="MISSING_VALIDATION_CONTEXT",
                field="validation_context",
                detail="Accumulator lacks validation_context",
            ))
        else:
            compat_res = validate_context_compatibility(accumulator.validation_context, context)
            if not compat_res.is_valid:
                issues.extend(compat_res.issues)

    for cand in accumulator.earliest_candidates:
        c_val = validate_candidate_context(cand, window, context)
        if not c_val.is_valid:
            issues.extend(c_val.issues)
    return ValidationResult(is_valid=len(issues) == 0, issues=issues)


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
    res = merge_candidate_accumulator_with_result(previous, new_candidates, window_context)
    return res.accumulator


def merge_candidate_accumulator_with_result(
    previous: Optional[CandidateAccumulator],
    new_candidates: List[TerminalCandidate],
    window_context: Optional[Dict[str, Any]] = None,
) -> AccumulatorMergeResult:
    ctx = dict(window_context or {})
    w_id = ctx.get("window_id", "")
    query_gen = ctx.get("query_generation", 1)
    arrival_rev = ctx.get("arrival_revision", 1)

    has_previous = previous is not None

    if previous is None:
        previous = CandidateAccumulator(
            window_id=w_id,
            query_generation=query_gen,
            arrival_revision=arrival_rev,
            validation_context=ctx,
        )

    conflict_flags = list(previous.conflict_flags)

    # PHẦN 053: Validate previous context compatibility before merging
    if has_previous and previous.validation_context and ctx:
        compat = validate_context_compatibility(previous.validation_context, ctx)
        if not compat.is_valid:
            for iss in compat.issues:
                conflict_flags.append(iss.code)

    if previous.window_id and w_id and previous.window_id != w_id:
        conflict_flags.append(f"WINDOW_ID_MISMATCH:{previous.window_id}!={w_id}")

    validated_new: List[TerminalCandidate] = []
    for c in new_candidates:
        if w_id and c.order_id != w_id:
            conflict_flags.append(f"ORDER_ID_MISMATCH:{c.order_id}!={w_id}")
        else:
            validated_new.append(c)

    all_cands: List[TerminalCandidate] = list(previous.earliest_candidates) + validated_new

    seen_map: Dict[str, TerminalCandidate] = {}
    conflict_detected = False
    for c in all_cands:
        if c.evidence_id in seen_map:
            prior_c = seen_map[c.evidence_id]
            # PHẦN 056: Full duplicate fingerprint checking
            if (
                prior_c.order_id != c.order_id
                or prior_c.cause != c.cause
                or prior_c.source_timestamp != c.source_timestamp
                or prior_c.executable_price != c.executable_price
                or prior_c.epoch != c.epoch
                or prior_c.bid != c.bid
                or prior_c.ask != c.ask
                or prior_c.last != c.last
                or prior_c.price_basis != c.price_basis
                or prior_c.source != c.source
                or prior_c.known_at_ms != c.known_at_ms
            ):
                conflict_flags.append(f"EVIDENCE_ID_CONFLICT:{c.evidence_id}")
                conflict_detected = True
        else:
            seen_map[c.evidence_id] = c

    unique_cands = list(seen_map.values())

    if not unique_cands or conflict_detected or len(conflict_flags) > 0:
        acc = CandidateAccumulator(
            schema_version="1.0.0",
            window_id=previous.window_id or w_id,
            query_generation=query_gen,
            arrival_revision=arrival_rev,
            earliest_source_ts=previous.earliest_source_ts if not conflict_detected and not conflict_flags else None,
            earliest_candidates=[] if (conflict_detected or conflict_flags) else previous.earliest_candidates,
            conflict_flags=conflict_flags,
            processed_interval=previous.processed_interval,
            validation_context=previous.validation_context if previous.validation_context else ctx,
        )
        return AccumulatorMergeResult(
            is_valid=len(conflict_flags) == 0,
            accumulator=acc,
            reasons=conflict_flags,
        )

    min_ts = min(c.source_timestamp for c in unique_cands)
    earliest_group = [c for c in unique_cands if c.source_timestamp == min_ts]
    earliest_group.sort(key=lambda c: (c.source_timestamp, c.evidence_id))

    acc = CandidateAccumulator(
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
    return AccumulatorMergeResult(
        is_valid=len(conflict_flags) == 0,
        accumulator=acc,
        reasons=conflict_flags,
    )


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
        return self.allows_window() and self.allows_page() and self.remaining_records() > 0

    def remaining_records(self) -> int:
        if self.max_records <= 0:
            return 0
        return max(0, self.max_records - self.records_processed)

    def remaining_pages(self) -> int:
        if self.max_pages <= 0:
            return 0
        return max(0, self.max_pages - self.pages_processed)

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

    # Defense: Validate frozen exit context authority (PHẦN 020, 053)
    frozen = read_frozen_exit_context(order)
    if frozen.status == "INVALID":
        return ResolutionDecision(
            action="REVIEW_REQUIRED",
            candidate=None,
            reason_code="FROZEN_CONTEXT_INVALID",
            details={"issues": frozen.issues},
        )

    # Defense: Validate candidate integrity and order identity (PHẦN 041)
    for c in candidates:
        if c.order_id != order.id:
            return ResolutionDecision(
                action="REVIEW_REQUIRED",
                candidate=None,
                reason_code="CANDIDATE_ORDER_ID_MISMATCH",
                details={"expected_order_id": order.id, "candidate_order_id": c.order_id},
            )
        if not is_finite_real(c.executable_price) or c.executable_price <= 0:
            return ResolutionDecision(
                action="REVIEW_REQUIRED",
                candidate=None,
                reason_code="CANDIDATE_PRICE_INVALID",
                details={"executable_price": c.executable_price},
            )
        opened_at_val = getattr(order, "opened_at", None) or 0
        if c.source_timestamp < opened_at_val:
            return ResolutionDecision(
                action="REVIEW_REQUIRED",
                candidate=None,
                reason_code="CANDIDATE_TIMESTAMP_BEFORE_OPEN",
                details={"opened_at": opened_at_val, "candidate_ts": c.source_timestamp},
            )

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
    """PHẦN 032, 038, 086, 088: Loads evidence progress with key collision protection."""
    key = f"ev_prog:{window_id}"[:50]
    rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if not rec or not rec.value:
        return None
    try:
        data = json.loads(rec.value)
        # PHẦN 038: Detect key truncation collision
        stored_id = data.get("window_id")
        if stored_id and stored_id != window_id:
            logger.warning(f"Key collision detected: stored {stored_id} != requested {window_id}")
            return None
        return EvidenceProgress.from_dict(data)
    except Exception:
        return None


def save_window_progress(db: Session, progress: EvidenceProgress) -> bool:
    """PHẦN 023, 032, 086, 088: Persists evidence progress for a specific reconciliation window."""
    key = f"ev_prog:{progress.window_id}"[:50]
    rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    progress.updated_at = now_ms
    try:
        # PHẦN 023: Disallow NaN/Infinity serialization
        val_json = json.dumps(progress.to_dict(), allow_nan=False)
    except (ValueError, TypeError) as e:
        logger.error(f"Failed to serialize progress with allow_nan=False for {key}: {e}")
        return False

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

    ctx_res = build_reconciliation_context_result(window, progress)
    if ctx_res.status == "INVALID":
        progress.read_status = "REPLAY_NEEDED"
        save_window_progress(db, progress)
        return WindowSliceResult(
            progressed=False,
            has_more=False,
            next_progress=progress,
            read_complete=False,
            decision=None,
            reasons=list(ctx_res.issues),
            processed_count=0,
        )

    context = ctx_res.context

    # Validate injected progress and accumulator context (PHẦN 032, 040)
    if progress and progress.accumulator:
        acc_ctx_val = validate_accumulator_context(progress.accumulator, window, context)
        if not acc_ctx_val.is_valid:
            progress.read_status = "REPLAY_NEEDED"
            save_window_progress(db, progress)
            return WindowSliceResult(
                progressed=False,
                has_more=False,
                next_progress=progress,
                read_complete=False,
                decision=None,
                reasons=[iss.code for iss in acc_ctx_val.issues],
                processed_count=0,
            )

    # Preflight zero / exhausted budget check (PHẦN 023, 024, 046, 047, 070, 088)
    if not budget.allows_page() or budget.remaining_records() <= 0:
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
        rem_records = budget.remaining_records()
        if rem_records <= 0:
            has_more = True
            break
        effective_limit = min(page_size, rem_records)
        if effective_limit <= 0:
            has_more = True
            break

        cutoff_ms = progress.scan_cutoff if progress.scan_cutoff > 0 else window.end_as_of
        page = load_persisted_window_evidence(
            db,
            window,
            as_of_ms=cutoff_ms,
            limit=effective_limit,
            cursor=current_cursor,
        )
        # Empty page also costs 1 page query (PHẦN 050)
        budget.record_page(len(page.items))

        if page.items:
            all_evidence.extend(page.items)
            if window.order:
                page_candidates = collect_terminal_candidates(window.order, page.items)
                merge_res = merge_candidate_accumulator_with_result(accumulator, page_candidates, context)
                if not merge_res.is_valid:
                    # Invalid merge: fail closed, mark REPLAY_NEEDED
                    progress.read_status = "REPLAY_NEEDED"
                    save_window_progress(db, progress)
                    return WindowSliceResult(
                        progressed=False,
                        has_more=False,
                        next_progress=progress,
                        read_complete=False,
                        decision=None,
                        reasons=merge_res.reasons,
                        processed_count=budget.records_processed - records_before,
                    )
                accumulator = merge_res.accumulator

        current_cursor = page.next_cursor
        if page.next_cursor is None:
            has_more = False
            break
        else:
            has_more = True
            if not budget.allows_page() or budget.remaining_records() <= 0:
                break

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

    # Check if accumulator has conflict flags before completing (PHẦN 024, 033)
    if accumulator and accumulator.conflict_flags:
        new_progress = EvidenceProgress(
            window_id=window.order_id,
            source_cursor=current_cursor,
            arrival_revision=progress.arrival_revision,
            scan_cutoff=progress.scan_cutoff,
            candidate_refs=[],
            read_status="REPLAY_NEEDED",
            updated_at=now_ms,
            accumulator=accumulator,
        )
        save_window_progress(db, new_progress)
        return WindowSliceResult(
            progressed=records_in_slice > 0,
            has_more=False,
            next_progress=new_progress,
            read_complete=False,
            decision=None,
            reasons=list(accumulator.conflict_flags),
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
    """PHẦN 043, 044, 090: Persists resolution decision atomically or appends audit for review."""
    if not window.order:
        return ResolutionApplyResult(action=decision.action, applied=False, committed=False)

    if decision.action == "CLOSE" and decision.candidate:
        cand = decision.candidate
        # Apply boundary defense (PHẦN 022, 043)
        frozen = read_frozen_exit_context(window.order)
        if frozen.status == "INVALID":
            logger.error(
                f"Apply boundary rejected CLOSE for order {window.order.id} with INVALID frozen snapshot: {frozen.issues}"
            )
            return ResolutionApplyResult(action="CLOSE", applied=False, committed=False)

        if (
            cand.order_id != window.order.id
            or not is_finite_real(cand.executable_price)
            or cand.executable_price <= 0
            or cand.source_timestamp < (getattr(window.order, "opened_at", 0) or 0)
        ):
            logger.error(
                f"Apply boundary rejected invalid candidate for order {window.order.id}: "
                f"cand_order={cand.order_id}, price={cand.executable_price}, ts={cand.source_timestamp}"
            )
            return ResolutionApplyResult(action="CLOSE", applied=False, committed=False)

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
        frozen = read_frozen_exit_context(order)
        if decision.action == "CLOSE" and frozen.status == "INVALID":
            logger.error(f"apply_reconciliation_decision rejected forged CLOSE for order {order.id}: {frozen.issues}")
            return ResolutionApplyResult(action="CLOSE", applied=False, committed=False)

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

        save_res = save_scan_progress(db, scheduler, scan_scope="default")
        if not save_res.success:
            logger.error(f"Failed to save scheduler scan progress for {symbol}: {save_res.reason}")

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
