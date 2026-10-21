"""
backend/services/order_quality_display_service.py
V16.39.11 Candidate Quality Score Display Service

Authoritative, pure, and non-mutating display adapter for candidate quality assessments.
Enforces the immutable truth that:
1. Quality score (e.g. 78/100) is a setup criteria match metric, NOT win probability.
2. Orders from ARMED onwards freeze their score from PaperOrder.cost_snapshot.
3. Legacy orders without snapshots report transparent UNAVAILABLE/LEGACY_NO_SCORE without backfilling.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field


@dataclass(frozen=True)
class CandidateQualityDisplayKey:
    """
    Rule 021: Structured key for pre-ARM candidate quality score cache.
    Prevents delimiter collisions when candidate_key or setup_id contains ':'.
    """
    symbol: str
    setup_id: Optional[str]
    setup_instance_id: Optional[str]
    candidate_key: Optional[str]
    direction: str


def make_candidate_quality_display_key(
    *,
    symbol: str = "XAUUSDT",
    setup_id: Optional[str] = None,
    setup_instance_id: Optional[str] = None,
    candidate_key: Optional[str] = None,
    direction: str = "LONG",
) -> CandidateQualityDisplayKey:
    """
    Rule 022: Pure helper to normalize fields and construct CandidateQualityDisplayKey.
    Normalizes symbol and direction to uppercase; strips whitespace.
    """
    clean_sym = str(symbol).upper().strip() if symbol else "XAUUSDT"
    clean_dir = str(direction).upper().strip() if direction else "UNKNOWN"
    clean_sid = str(setup_id).strip() if setup_id else None
    clean_inst = str(setup_instance_id).strip() if setup_instance_id else None
    clean_ck = str(candidate_key).strip() if candidate_key else None

    return CandidateQualityDisplayKey(
        symbol=clean_sym,
        setup_id=clean_sid,
        setup_instance_id=clean_inst,
        candidate_key=clean_ck,
        direction=clean_dir,
    )


class CandidateScoreDisplay(BaseModel):
    """
    Rules 053-060: Summary DTO for setup and order quality scores.
    Compact, lightweight, and suitable for badges and list views.
    """
    status: str = "NOT_ASSESSED"  # AVAILABLE, NOT_ASSESSED, UNAVAILABLE, INVALID, CONFIG_CHANGED
    score: Optional[float] = None
    max_score: float = 100.0
    base_score: Optional[float] = None
    lesson_delta: Optional[float] = None
    threshold: Optional[float] = None
    decision: Optional[str] = None
    as_of_ms: Optional[int] = None
    armed_at_ms: Optional[int] = None
    source: str = "NONE"  # ARM_SNAPSHOT, VOLATILE_LATEST_EVALUATION, NONE
    frozen: bool = False
    assessment_id: Optional[str] = None
    candidate_key: Optional[str] = None
    model_id: Optional[str] = None
    profile_version: Optional[str] = None
    config_fingerprint: Optional[str] = None
    reason_code: Optional[str] = None
    disclaimer: str = "Điểm chất lượng setup theo tiêu chuẩn CG-01, không phải xác suất thắng."

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class CandidateScoreDetail(CandidateScoreDisplay):
    """
    Rule 061: Extended detail DTO for trade drawer and drill-downs.
    Contains factor points, group breakdown, blockers, and missing data.
    """
    factors: List[Dict[str, Any]] = Field(default_factory=list)
    group_scores: Dict[str, Dict[str, float]] = Field(default_factory=dict)
    missing_data: List[str] = Field(default_factory=list)
    blockers: List[str] = Field(default_factory=list)


def parse_candidate_quality_assessment(raw: Any) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Rules 053-055, V16.39.12: Pure parser and validator for candidate quality assessment payload.
    Accepts JSON string or dict.
    Returns (validated_dict, "VALID") or (None, reason_code).
    Does NOT fabricate base_score, threshold, lesson_delta, or profile_version when missing.
    """
    if raw is None:
        return None, "EMPTY_OR_NONE"

    data = raw
    if isinstance(raw, str):
        raw_trimmed = raw.strip()
        if not raw_trimmed:
            return None, "EMPTY_OR_NONE"
        try:
            data = json.loads(raw_trimmed)
        except Exception:
            return None, "INVALID_JSON"

    if not isinstance(data, dict):
        return None, "INVALID_DATA_TYPE"

    # Numeric validations (Rule 054: reject boolean masquerading as int/float)
    def _is_valid_number(val: Any) -> bool:
        if isinstance(val, bool):
            return False
        if not isinstance(val, (int, float)):
            return False
        return not (math.isnan(val) or math.isinf(val))

    final_score = data.get("final_score")
    if not _is_valid_number(final_score):
        return None, "INVALID_FINAL_SCORE"
    final_score_f = float(final_score)
    if not (0.0 <= final_score_f <= 100.0):
        return None, "SCORE_OUT_OF_RANGE"

    base_score = data.get("base_score")
    base_score_f: Optional[float] = None
    if base_score is not None:
        if not _is_valid_number(base_score):
            return None, "INVALID_BASE_SCORE"
        val = float(base_score)
        if not (0.0 <= val <= 100.0):
            return None, "SCORE_OUT_OF_RANGE"
        base_score_f = round(val, 2)

    threshold = data.get("threshold")
    threshold_f: Optional[float] = None
    if threshold is not None:
        if not _is_valid_number(threshold):
            return None, "INVALID_THRESHOLD"
        threshold_f = round(float(threshold), 2)

    lesson_delta = data.get("lesson_delta")
    lesson_delta_f: Optional[float] = None
    if lesson_delta is not None:
        if not _is_valid_number(lesson_delta):
            return None, "INVALID_LESSON_DELTA"
        lesson_delta_f = round(float(lesson_delta), 2)

    as_of_ms = data.get("as_of_ms")
    as_of_ms_int: Optional[int] = None
    if as_of_ms is not None:
        if isinstance(as_of_ms, bool) or not isinstance(as_of_ms, (int, float)) or not math.isfinite(float(as_of_ms)):
            return None, "INVALID_TIMESTAMP"
        as_of_ms_int = int(as_of_ms)

    # Canonical factor sanitization (Rule 055-058)
    raw_factors = data.get("factors") or []
    sanitized_factors: List[Dict[str, Any]] = []
    if isinstance(raw_factors, (list, tuple)):
        for f in raw_factors:
            if isinstance(f, dict):
                code = str(f.get("code") or f.get("factor_id") or "")
                name = str(f.get("name") or f.get("code") or "")
                group = str(f.get("group", ""))
                earned = round(float(f.get("earned_points", 0.0)), 2) if _is_valid_number(f.get("earned_points")) else (
                    round(float(f.get("score", 0.0)), 2) if _is_valid_number(f.get("score")) else 0.0
                )
                max_pts = round(float(f.get("max_points", 0.0)), 2) if _is_valid_number(f.get("max_points")) else (
                    round(float(f.get("max_score", 0.0)), 2) if _is_valid_number(f.get("max_score")) else 0.0
                )
                status = str(f.get("status", ""))
                explanation_vi = str(f.get("explanation_vi") or f.get("reason") or "")
                ev_ids = [str(x) for x in (f.get("evidence_ids") or [])] if isinstance(f.get("evidence_ids"), (list, tuple)) else []
                known_at = int(f.get("known_at_ms")) if (
                    isinstance(f.get("known_at_ms"), (int, float))
                    and not isinstance(f.get("known_at_ms"), bool)
                    and math.isfinite(float(f.get("known_at_ms")))
                ) else None
                measurement = dict(f.get("measurement")) if isinstance(f.get("measurement"), dict) else None

                sanitized_factors.append({
                    "code": code,
                    "name": name,
                    "group": group,
                    "earned_points": earned,
                    "max_points": max_pts,
                    "status": status,
                    "explanation_vi": explanation_vi,
                    "evidence_ids": ev_ids,
                    "known_at_ms": known_at,
                    "measurement": measurement,
                    "rule_version": str(f.get("rule_version", "v16.39")),
                    # Backward-compatible aliases for legacy readers
                    "factor_id": str(f.get("factor_id") or code),
                    "score": earned,
                    "max_score": max_pts,
                    "reason": str(f.get("reason") or explanation_vi),
                })

    # Group scores
    raw_group_scores = data.get("group_scores") or {}
    sanitized_groups: Dict[str, Dict[str, float]] = {}
    if isinstance(raw_group_scores, dict):
        for g_k, g_v in raw_group_scores.items():
            if isinstance(g_v, dict):
                sanitized_groups[str(g_k)] = {
                    "earned": round(float(g_v.get("earned", 0.0)), 2) if _is_valid_number(g_v.get("earned")) else 0.0,
                    "max": round(float(g_v.get("max", 0.0)), 2) if _is_valid_number(g_v.get("max")) else 0.0,
                }

    raw_missing = data.get("missing_data") or []
    missing_data = [str(x) for x in raw_missing] if isinstance(raw_missing, (list, tuple)) else []

    raw_blockers = data.get("blockers") or []
    blockers = [str(x) for x in raw_blockers] if isinstance(raw_blockers, (list, tuple)) else []

    validated: Dict[str, Any] = {
        "assessment_id": str(data.get("assessment_id")) if data.get("assessment_id") else None,
        "candidate_key": str(data.get("candidate_key")) if data.get("candidate_key") else None,
        "model_id": str(data.get("model_id")) if data.get("model_id") else None,
        "base_score": base_score_f,
        "lesson_delta": lesson_delta_f,
        "final_score": round(final_score_f, 2),
        "threshold": threshold_f,
        "decision": str(data.get("decision", "UNKNOWN")),
        "as_of_ms": as_of_ms_int,
        "profile_version": str(data.get("profile_version")) if data.get("profile_version") else None,
        "config_fingerprint": str(data.get("config_fingerprint")) if data.get("config_fingerprint") else None,
        "factors": sanitized_factors,
        "group_scores": sanitized_groups,
        "missing_data": missing_data,
        "blockers": blockers,
    }
    return validated, "VALID"


def resolve_order_assessment(order: Any) -> Tuple[Optional[Dict[str, Any]], str, str]:
    """
    Rules 052, 060, V16.39.12: Canonical resolver for an order's quality assessment.
    Returns (validated_dict, status, reason_code).
    """
    cost_snap_raw = getattr(order, "cost_snapshot", None)
    if not cost_snap_raw:
        return None, "UNAVAILABLE", "LEGACY_NO_SCORE"

    snap_dict = {}
    if isinstance(cost_snap_raw, str):
        raw_trimmed = cost_snap_raw.strip()
        if not raw_trimmed:
            return None, "UNAVAILABLE", "LEGACY_NO_SCORE"
        try:
            snap_dict = json.loads(raw_trimmed)
        except Exception:
            return None, "INVALID", "MALFORMED_COST_SNAPSHOT_JSON"
    elif isinstance(cost_snap_raw, dict):
        snap_dict = cost_snap_raw
    else:
        return None, "INVALID", "MALFORMED_COST_SNAPSHOT_TYPE"

    if not isinstance(snap_dict, dict):
        return None, "INVALID", "MALFORMED_COST_SNAPSHOT_TYPE"

    raw_assessment = snap_dict.get("quality_assessment")
    if not raw_assessment:
        return None, "UNAVAILABLE", "LEGACY_NO_SCORE"

    validated, parse_status = parse_candidate_quality_assessment(raw_assessment)
    if parse_status != "VALID" or not validated:
        return None, "INVALID", parse_status

    # Rule 055: Identity verification
    order_instance_id = getattr(order, "setup_instance_id", None)
    if order_instance_id and validated.get("candidate_key"):
        if str(order_instance_id) != str(validated["candidate_key"]):
            return None, "INVALID", "CANDIDATE_KEY_MISMATCH"

    return validated, "AVAILABLE", "VALID"


def build_order_score_display(order: Any) -> CandidateScoreDisplay:
    """
    Rules 052, 060: Builds CandidateScoreDisplay from an order's frozen cost_snapshot.
    Pure projection, no DB queries, no network calls, no mutation.
    """
    validated, status, reason_code = resolve_order_assessment(order)
    if status != "AVAILABLE" or not validated:
        return CandidateScoreDisplay(
            status=status,
            reason_code=reason_code,
            source="ARM_SNAPSHOT" if status == "INVALID" else "NONE",
            frozen=True if status == "INVALID" else False,
        )

    armed_at = getattr(order, "armed_at", None) or validated.get("as_of_ms")

    return CandidateScoreDisplay(
        status="AVAILABLE",
        score=validated["final_score"],
        max_score=100.0,
        base_score=validated["base_score"],
        lesson_delta=validated["lesson_delta"],
        threshold=validated["threshold"],
        decision=validated["decision"],
        as_of_ms=validated["as_of_ms"],
        armed_at_ms=armed_at,
        source="ARM_SNAPSHOT",
        frozen=True,
        assessment_id=validated["assessment_id"],
        candidate_key=validated["candidate_key"],
        model_id=validated["model_id"],
        profile_version=validated["profile_version"],
        config_fingerprint=validated["config_fingerprint"],
        reason_code=None,
    )


def build_order_score_detail(order: Any) -> CandidateScoreDetail:
    """
    Rule 061: Expands CandidateScoreDisplay with detailed factor breakdown and group scores.
    Shares the exact same validated source as build_order_score_display.
    """
    summary = build_order_score_display(order)
    if summary.status != "AVAILABLE":
        return CandidateScoreDetail(
            **summary.model_dump(),
            factors=[],
            group_scores={},
            missing_data=[],
            blockers=[],
        )

    validated, status, _ = resolve_order_assessment(order)
    if not validated or status != "AVAILABLE":
        return CandidateScoreDetail(
            **summary.model_dump(),
            factors=[],
            group_scores={},
            missing_data=[],
            blockers=[],
        )

    return CandidateScoreDetail(
        **summary.model_dump(),
        factors=validated.get("factors", []),
        group_scores=validated.get("group_scores", {}),
        missing_data=validated.get("missing_data", []),
        blockers=validated.get("blockers", []),
    )
