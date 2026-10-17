"""
backend/services/cg01_types.py
V16.2 - Types, configurations, enums, and contracts for CG-01 Paper Profile.
Implements Rules 001 - 035, 121 - 122.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple
import time

from domain_calculator import CalculationResult
from services.scalp_m1_types import ModelIdentifier
from services.scalp_m1_model_service import ScalpCandidate


class CG01ProfileMode(str, Enum):
    OFF = "OFF"
    SHADOW = "SHADOW"
    PAPER_EXECUTION = "PAPER_EXECUTION"


class CG01Stage(str, Enum):
    OBSERVING = "OBSERVING"
    WAIT_WINDOW = "WAIT_WINDOW"
    WAIT_POI = "WAIT_POI"
    WAIT_SWEEP = "WAIT_SWEEP"
    WAIT_MSS = "WAIT_MSS"
    WAIT_FVG = "WAIT_FVG"
    WAIT_ENTRY = "WAIT_ENTRY"
    EXECUTABLE = "EXECUTABLE"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"


class CG01EntryMode(str, Enum):
    LIMIT_AT_CE_AFTER_CONFIRMATION = "LIMIT_AT_CE_AFTER_CONFIRMATION"
    CONFIRMED_RETEST_MARKET = "CONFIRMED_RETEST_MARKET"


# Rule 033: Parameter Origin Attribution
PARAMETER_ORIGINS: Dict[str, str] = {
    "htf_poi_required": "SOURCE_DOCUMENT",
    "liquidity_sweep_required": "SOURCE_DOCUMENT",
    "mss_after_sweep_required": "SOURCE_DOCUMENT",
    "displacement_required": "SOURCE_DOCUMENT",
    "fvg_same_leg_required": "SOURCE_DOCUMENT",
    "fvg_retest_entry_required": "SOURCE_DOCUMENT",
    "structural_sl_required": "SOURCE_DOCUMENT",
    "liquidity_target_required": "SOURCE_DOCUMENT",
    "silver_bullet_london_window": "SOURCE_DOCUMENT",
    "silver_bullet_ny_window": "SOURCE_DOCUMENT",
    "pivot_left_bars": "IMPLEMENTATION_PARAMETER",
    "pivot_right_bars": "IMPLEMENTATION_PARAMETER",
    "displacement_body_atr_min": "IMPLEMENTATION_PARAMETER",
    "sl_buffer_ticks": "IMPLEMENTATION_PARAMETER",
    "max_touch_episodes": "IMPLEMENTATION_PARAMETER",
    "max_fvg_mitigation_ratio": "IMPLEMENTATION_PARAMETER",
    "max_trigger_age_ms": "IMPLEMENTATION_PARAMETER",
    "candidate_ttl_ms": "IMPLEMENTATION_PARAMETER",
    "min_net_rr": "IMPLEMENTATION_PARAMETER",
    "htf_min_bars": "IMPLEMENTATION_PARAMETER",
    "htf_break_buffer_ticks": "IMPLEMENTATION_PARAMETER",
}


@dataclass
class CG01Config:
    """
    Rule 031: CG-01 Configuration.
    Default profile is OFF; does not auto-enable on live accounts (Rule 018).
    """
    enabled: bool = False
    profile_mode: str = CG01ProfileMode.OFF.value
    model_version: str = "v16.2"
    config_version: int = 1
    timing_profile: str = "SILVER_BULLET_LONDON_NY"
    entry_timeframe: str = "1M"
    entry_mode: str = CG01EntryMode.LIMIT_AT_CE_AFTER_CONFIRMATION.value

    # Structural parameters
    pivot_left_bars: int = 2
    pivot_right_bars: int = 2
    displacement_body_atr_min: float = 0.80
    displacement_atr_period: int = 14
    sl_buffer_ticks: int = 5
    tick_size: float = 0.01

    # HTF structure parameters (V16.12 Section D)
    htf_min_bars: int = 12
    htf_break_buffer_ticks: int = 0

    # POI & FVG Freshness
    max_touch_episodes: int = 2
    max_fvg_mitigation_ratio: float = 0.50
    max_trigger_age_ms: int = 600_000   # 10 minutes max trigger age
    candidate_ttl_ms: int = 300_000     # 5 minutes pending limit TTL

    # Risk & Objectives
    min_net_rr: float = 2.0
    fallback_risk_pct: float = 0.10     # 0.10% safe research default
    daily_min_goal: int = 2
    daily_max_cap: int = 3
    daily_ny_goal: int = 1

    # Timezone
    ny_timezone: str = "America/New_York"

    def __post_init__(self):
        if self.displacement_body_atr_min <= 0:
            raise ValueError("INVALID_CONFIG: displacement_body_atr_min must be > 0")
        if self.min_net_rr <= 0:
            raise ValueError("INVALID_CONFIG: min_net_rr must be > 0")
        if self.max_touch_episodes <= 0:
            raise ValueError("INVALID_CONFIG: max_touch_episodes must be > 0")
        if type(self.pivot_left_bars) is not int or self.pivot_left_bars < 1 or type(self.pivot_right_bars) is not int or self.pivot_right_bars < 1:
            raise ValueError("INVALID_CONFIG: pivot bars must be int >= 1")
        if type(self.htf_min_bars) is not int or self.htf_min_bars <= 0:
            raise ValueError("INVALID_CONFIG: htf_min_bars must be positive int")
        if type(self.htf_break_buffer_ticks) is not int or self.htf_break_buffer_ticks < 0:
            raise ValueError("INVALID_CONFIG: htf_break_buffer_ticks must be non-negative int")
        if not math.isfinite(self.tick_size) or self.tick_size <= 0:
            raise ValueError("INVALID_CONFIG: tick_size must be positive finite float")


@dataclass(frozen=True)
class CG01TimeContext:
    """Rule 051 - 060: NY and Local Time Window Context."""
    now_ms: int
    ny_time_str: str
    local_time_str: str
    is_in_window: bool
    active_window_id: Optional[str]
    permission_reason: str
    window_start_ms: Optional[int] = None
    window_end_ms: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "now_ms": self.now_ms,
            "ny_time_str": self.ny_time_str,
            "local_time_str": self.local_time_str,
            "is_in_window": self.is_in_window,
            "active_window_id": self.active_window_id,
            "permission_reason": self.permission_reason,
            "window_start_ms": self.window_start_ms,
            "window_end_ms": self.window_end_ms,
        }


@dataclass(frozen=True)
class CG01EvidenceRefs:
    """Rule 034: Explicit required evidence IDs."""
    htf_context_id: Optional[str] = None
    htf_poi_id: Optional[str] = None
    liquidity_reference_id: Optional[str] = None
    sweep_id: Optional[str] = None
    structure_reference_id: Optional[str] = None
    mss_id: Optional[str] = None
    displacement_id: Optional[str] = None
    fvg_id: Optional[str] = None
    entry_trigger_id: Optional[str] = None
    invalidation_id: Optional[str] = None
    target_id: Optional[str] = None

    def to_tuple(self) -> Tuple[str, ...]:
        refs = []
        for fld in (
            self.htf_context_id,
            self.htf_poi_id,
            self.liquidity_reference_id,
            self.sweep_id,
            self.structure_reference_id,
            self.mss_id,
            self.displacement_id,
            self.fvg_id,
            self.entry_trigger_id,
            self.invalidation_id,
            self.target_id,
        ):
            if fld:
                refs.append(fld)
        return tuple(refs)


@dataclass
class CG01Decision:
    """
    Rule 121: CG01 Decision type / adapter on ScalpCandidate.
    Contains stage, eligibility, model/config version, direction, episode ID, plan, evidence refs and reasons.
    """
    stage: CG01Stage
    is_eligible: bool
    model_id: str = ModelIdentifier.CG01_SWEEP_MSS_FVG.value
    model_version: str = "v16.2"
    config_version: int = 1
    direction: str = "NONE"
    episode_id: str = ""
    poi_id: Optional[str] = None
    planned_entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    gross_rr: Optional[float] = None
    net_rr: Optional[float] = None
    calculation: Optional[CalculationResult] = None
    candidate: Optional[ScalpCandidate] = None
    evidence_refs: Tuple[str, ...] = ()
    reason_codes: Tuple[str, ...] = ()
    missing_conditions: Tuple[str, ...] = ()
    as_of_ms: int = 0
    time_context: Optional[CG01TimeContext] = None
    ui_summary_vi: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage.value if hasattr(self.stage, "value") else str(self.stage),
            "is_eligible": self.is_eligible,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "config_version": self.config_version,
            "direction": self.direction,
            "episode_id": self.episode_id,
            "poi_id": self.poi_id,
            "planned_entry": self.planned_entry,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "gross_rr": self.gross_rr,
            "net_rr": self.net_rr,
            "evidence_refs": list(self.evidence_refs),
            "reason_codes": list(self.reason_codes),
            "missing_conditions": list(self.missing_conditions),
            "as_of_ms": self.as_of_ms,
            "time_context": self.time_context.to_dict() if self.time_context else None,
            "ui_summary_vi": self.ui_summary_vi,
        }


@dataclass(frozen=True)
class CG01EntryIntent:
    """
    Rules 041 - 043: Explicit execution intent for CG-01 candidate.
    Distinguishes limit at CE from confirmed retest market order.
    """
    entry_mode: str               # "LIMIT_AT_CE_AFTER_CONFIRMATION" or "CONFIRMED_RETEST_MARKET"
    order_type: str               # "LIMIT" or "MARKET"
    reference_ce: float           # 50% CE price of FVG
    planned_entry: float          # CE for LIMIT, or executable market price (ask/bid + slippage) for MARKET
    price_basis: str              # "LIMIT_CE" or "MARKET_QUOTE_ASK" / "MARKET_QUOTE_BID"
    trigger_known_at: int         # Milliseconds UTC when trigger was confirmed
    valid_until: int              # Milliseconds UTC when intent expires
    quote_timestamp: int          # Timestamp of the quote used for market estimation

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_mode": self.entry_mode,
            "order_type": self.order_type,
            "reference_ce": self.reference_ce,
            "planned_entry": self.planned_entry,
            "price_basis": self.price_basis,
            "trigger_known_at": self.trigger_known_at,
            "valid_until": self.valid_until,
            "quote_timestamp": self.quote_timestamp,
        }


def resolve_cg01_order_type(entry_mode: str) -> str:
    """
    Rule 042: Maps entry_mode enum/string to standard order_type ('LIMIT' or 'MARKET').
    Rejects unknown modes fail-closed.
    """
    if entry_mode in (CG01EntryMode.LIMIT_AT_CE_AFTER_CONFIRMATION.value, "LIMIT_AT_CE_AFTER_CONFIRMATION", "LIMIT"):
        return "LIMIT"
    elif entry_mode in (CG01EntryMode.CONFIRMED_RETEST_MARKET.value, "CONFIRMED_RETEST_MARKET", "MARKET"):
        return "MARKET"
    else:
        raise ValueError(f"UNSUPPORTED_ENTRY_MODE: {entry_mode}. Only LIMIT_AT_CE_AFTER_CONFIRMATION and CONFIRMED_RETEST_MARKET are supported.")


def candidate_execution_order_type(cand: Any, legacy_default: str = "MARKET") -> str:
    """
    Rule 009 & Appendix B: Determines authoritative order_type for paper execution.
    Prioritizes cand.order_type or cand.entry_intent, with legacy_default for non-CG01 models.
    """
    if cand is None:
        return legacy_default
    ot = getattr(cand, "order_type", None)
    if ot:
        return str(ot).upper()
    intent = getattr(cand, "entry_intent", None)
    if isinstance(intent, dict) and "order_type" in intent:
        return str(intent["order_type"]).upper()
    return legacy_default


@dataclass(frozen=True)
class ExecutionDeadline:
    """
    Rule 024 & Appendix A: Execution deadline breakdown.
    Contains authoritative expiry timestamp in milliseconds and constraints.
    """
    expires_at_ms: int
    constraints: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "expires_at_ms": self.expires_at_ms,
            "constraints": self.constraints,
        }


def resolve_cg01_execution_deadline(
    *,
    trigger_known_at_ms: int,
    candidate_ttl_ms: int,
    max_trigger_age_ms: int,
    entry_window_end_ms: Optional[int] = None,
    existing_deadline_ms: Optional[int] = None,
) -> ExecutionDeadline:
    """
    Rule 024, 025 & Appendix A: Resolves immutable deadline for CG-01 setup.
    Guarantees no drifting and strictly respects session window and TTL upper bounds.
    """
    if trigger_known_at_ms <= 0:
        raise ValueError(f"INVALID_DEADLINE_INPUT: trigger_known_at_ms must be > 0, got {trigger_known_at_ms}")
    if candidate_ttl_ms <= 0:
        raise ValueError(f"INVALID_DEADLINE_INPUT: candidate_ttl_ms must be > 0, got {candidate_ttl_ms}")
    if max_trigger_age_ms <= 0:
        raise ValueError(f"INVALID_DEADLINE_INPUT: max_trigger_age_ms must be > 0, got {max_trigger_age_ms}")

    constraints: Dict[str, int] = {
        "ttl": int(trigger_known_at_ms + candidate_ttl_ms),
        "max_age": int(trigger_known_at_ms + max_trigger_age_ms),
    }
    if entry_window_end_ms is not None and entry_window_end_ms > 0:
        constraints["session_end"] = int(entry_window_end_ms)
    if existing_deadline_ms is not None and existing_deadline_ms > 0:
        constraints["existing"] = int(existing_deadline_ms)

    expires_at_ms = min(constraints.values())
    return ExecutionDeadline(expires_at_ms=expires_at_ms, constraints=constraints)


def resolve_slippage_usd(value: Any, *, default: float = 0.10) -> float:
    """
    Rule 052 & Appendix B: Authoritative slippage resolver.
    Only uses default when value is None. 0.0 is valid.
    Rejects negative, NaN, or infinite values.
    """
    raw = default if value is None else value
    try:
        resolved = float(raw)
    except (ValueError, TypeError) as e:
        raise ValueError(f"slippage_usd must be a valid float: {e}")
    if not math.isfinite(resolved) or resolved < 0:
        raise ValueError(f"slippage_usd must be finite and non-negative, got {resolved}")
    return resolved


def resolve_candidate_order_expiry(
    cand: Any,
    now_ms: int,
    session_context: Optional[Any] = None,
    legacy_default_hours: float = 2.0,
) -> int:
    """
    Rule 031: Authoritative expiry resolver for paper candidates.
    For CG-01: extracts valid_until_ms from candidate or entry_intent.
    If session_context has window_end_ms, ensures order does not outlive window.
    For legacy models: now_ms + legacy_default_hours.
    """
    model_id = getattr(cand, "model_id", "")
    if model_id == ModelIdentifier.CG01_SWEEP_MSS_FVG.value:
        exp = getattr(cand, "valid_until_ms", None)
        if exp is None:
            intent = getattr(cand, "entry_intent", None)
            if isinstance(intent, dict):
                exp = intent.get("valid_until")
        if exp is not None and int(exp) > 0:
            res_exp = int(exp)
            if session_context and hasattr(session_context, "window_end_ms") and session_context.window_end_ms:
                res_exp = min(res_exp, int(session_context.window_end_ms))
            return res_exp
        raise ValueError("CG01_CANDIDATE_MISSING_EXPIRY: CG-01 candidate must define valid_until_ms")
    return int(now_ms + (legacy_default_hours * 3600 * 1000))


@dataclass(frozen=True)
class CG01ResearchContext:
    """
    V16.17 Frozen, immutable research context for telemetry and run attribution (Rules 034-036).
    Never contains database Session objects, mutable collectors, or live trading credentials.
    """
    trace_id: str
    run_id: Optional[str] = None
    segment_id: Optional[str] = None
    symbol: str = "XAUUSDT"
    config_fingerprint: str = "DEFAULT"
    input_versions: Dict[str, Any] = field(default_factory=dict)
    evaluation_as_of_ms: int = 0
    observation_mode: str = "PAPER_EXECUTION"
