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
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field


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
    Rules 053-055: Pure parser and validator for candidate quality assessment payload.
    Accepts JSON string or dict.
    Returns (validated_dict, "VALID") or (None, reason_code).
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
    if base_score is not None:
        if not _is_valid_number(base_score):
            return None, "INVALID_BASE_SCORE"
        base_score_f = float(base_score)
        if not (0.0 <= base_score_f <= 100.0):
            return None, "SCORE_OUT_OF_RANGE"
    else:
        base_score_f = final_score_f

    threshold = data.get("threshold")
    threshold_f = 70.0
    if threshold is not None:
        if not _is_valid_number(threshold):
            return None, "INVALID_THRESHOLD"
        threshold_f = float(threshold)

    lesson_delta = data.get("lesson_delta")
    lesson_delta_f = 0.0
    if lesson_delta is not None:
        if not _is_valid_number(lesson_delta):
            return None, "INVALID_LESSON_DELTA"
        lesson_delta_f = float(lesson_delta)

    as_of_ms = data.get("as_of_ms")
    as_of_ms_int = int(as_of_ms) if (isinstance(as_of_ms, (int, float)) and not isinstance(as_of_ms, bool)) else None

    # Factors sanitization
    raw_factors = data.get("factors") or []
    sanitized_factors: List[Dict[str, Any]] = []
    if isinstance(raw_factors, (list, tuple)):
        for f in raw_factors:
            if isinstance(f, dict):
                sanitized_factors.append({
                    "factor_id": str(f.get("factor_id", "")),
                    "name": str(f.get("name", "")),
                    "group": str(f.get("group", "")),
                    "earned_points": float(f.get("earned_points", 0.0)) if _is_valid_number(f.get("earned_points")) else 0.0,
                    "max_points": float(f.get("max_points", 0.0)) if _is_valid_number(f.get("max_points")) else 0.0,
                    "status": str(f.get("status", "")),
                    "reason": str(f.get("reason", "")),
                })

    # Group scores
    raw_group_scores = data.get("group_scores") or {}
    sanitized_groups: Dict[str, Dict[str, float]] = {}
    if isinstance(raw_group_scores, dict):
        for g_k, g_v in raw_group_scores.items():
            if isinstance(g_v, dict):
                sanitized_groups[str(g_k)] = {
                    "earned": float(g_v.get("earned", 0.0)) if _is_valid_number(g_v.get("earned")) else 0.0,
                    "max": float(g_v.get("max", 0.0)) if _is_valid_number(g_v.get("max")) else 0.0,
                }

    raw_missing = data.get("missing_data") or []
    missing_data = [str(x) for x in raw_missing] if isinstance(raw_missing, (list, tuple)) else []

    raw_blockers = data.get("blockers") or []
    blockers = [str(x) for x in raw_blockers] if isinstance(raw_blockers, (list, tuple)) else []

    validated: Dict[str, Any] = {
        "assessment_id": str(data.get("assessment_id")) if data.get("assessment_id") else None,
        "candidate_key": str(data.get("candidate_key")) if data.get("candidate_key") else None,
        "model_id": str(data.get("model_id")) if data.get("model_id") else None,
        "base_score": round(base_score_f, 2),
        "lesson_delta": round(lesson_delta_f, 2),
        "final_score": round(final_score_f, 2),
        "threshold": round(threshold_f, 2),
        "decision": str(data.get("decision", "UNKNOWN")),
        "as_of_ms": as_of_ms_int,
        "profile_version": str(data.get("profile_version", "v16.39")),
        "config_fingerprint": str(data.get("config_fingerprint", "")),
        "factors": sanitized_factors,
        "group_scores": sanitized_groups,
        "missing_data": missing_data,
        "blockers": blockers,
    }
    return validated, "VALID"


def build_order_score_display(order: Any) -> CandidateScoreDisplay:
    """
    Rules 052, 060: Builds CandidateScoreDisplay from an order's frozen cost_snapshot.
    Pure projection, no DB queries, no network calls, no mutation.
    """
    cost_snap_raw = getattr(order, "cost_snapshot", None)
    if not cost_snap_raw:
        return CandidateScoreDisplay(
            status="UNAVAILABLE",
            reason_code="LEGACY_NO_SCORE",
            source="NONE",
            frozen=False,
        )

    snap_dict = {}
    if isinstance(cost_snap_raw, str):
        try:
            snap_dict = json.loads(cost_snap_raw)
        except Exception:
            return CandidateScoreDisplay(
                status="INVALID",
                reason_code="MALFORMED_COST_SNAPSHOT_JSON",
                source="ARM_SNAPSHOT",
                frozen=True,
            )
    elif isinstance(cost_snap_raw, dict):
        snap_dict = cost_snap_raw

    raw_assessment = snap_dict.get("quality_assessment")
    if not raw_assessment:
        return CandidateScoreDisplay(
            status="UNAVAILABLE",
            reason_code="LEGACY_NO_SCORE",
            source="NONE",
            frozen=False,
        )

    validated, parse_status = parse_candidate_quality_assessment(raw_assessment)
    if parse_status != "VALID" or not validated:
        return CandidateScoreDisplay(
            status="INVALID",
            reason_code=parse_status,
            source="ARM_SNAPSHOT",
            frozen=True,
        )

    # Rule 055: Identity verification
    order_instance_id = getattr(order, "setup_instance_id", None)
    if order_instance_id and validated.get("candidate_key"):
        if str(order_instance_id) != str(validated["candidate_key"]):
            return CandidateScoreDisplay(
                status="INVALID",
                reason_code="CANDIDATE_KEY_MISMATCH",
                source="ARM_SNAPSHOT",
                frozen=True,
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

    cost_snap_raw = getattr(order, "cost_snapshot", None)
    snap_dict = {}
    if isinstance(cost_snap_raw, str):
        try:
            snap_dict = json.loads(cost_snap_raw)
        except Exception:
            pass
    elif isinstance(cost_snap_raw, dict):
        snap_dict = cost_snap_raw

    raw_assessment = snap_dict.get("quality_assessment")
    validated, _ = parse_candidate_quality_assessment(raw_assessment)
    if not validated:
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
