"""
backend/services/cg01_types.py
V16.2 - Types, configurations, enums, and contracts for CG-01 Paper Profile.
Implements Rules 001 - 035, 121 - 122.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
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
        if self.pivot_left_bars < 1 or self.pivot_right_bars < 1:
            raise ValueError("INVALID_CONFIG: pivot bars must be >= 1")


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
