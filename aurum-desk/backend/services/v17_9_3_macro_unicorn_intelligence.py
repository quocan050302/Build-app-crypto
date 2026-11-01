"""
backend/services/v17_9_3_macro_unicorn_intelligence.py
AURUM Desk V17.9.3 — Causal ICT Macro Time + Unicorn Confluence Intelligence.

Pure calculation module:
  - Zero wall-clock (`datetime.now`, `time.time`, `date.today`, `os.times`) dependencies
  - Zero database mutations, zero network I/O, zero global mutable state
  - Consumes V17.9.2.1 structural SL/TP (`plan_v17_9_2_structural_stop_and_target`) and
    existing ICT Unicorn evidence engine (`detect_breaker_blocks`, `detect_unicorn_evidence`,
    `_is_same_causal_episode`) without rewrite or risk-cap weakening.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from services.mtf_structure_liquidity_service import (
    deterministic_hash,
    evaluate_mtf_eligibility_for_direction,
)
from services.unicorn_evidence_service import (
    _candle_is_closed,
    _candle_ohlc,
    _candle_open_ms,
    _is_same_causal_episode,
    detect_breaker_blocks,
    detect_unicorn_evidence,
)
from services.unicorn_evidence_types import (
    BreakerBlock,
    UnicornEvidence,
    UnicornSetupStatus,
)


V17_9_3_ALGORITHM_VERSION = "V17_9_3_0"
DEFAULT_MACRO_SCHEDULE_VERSION = "V17_9_3_NY_ICT_MACRO_SCHEDULE_V1"
DEFAULT_MACRO_TIMEZONE = "America/New_York"

# Plausible millisecond epoch bounds: [2000-01-01T00:00:00Z, 2100-01-01T00:00:00Z]
# Catches seconds-scale timestamps (e.g. 1_775_000_000) or negative/zero values fail-closed.
MIN_PLAUSIBLE_EPOCH_MS = 946_684_800_000
MAX_PLAUSIBLE_EPOCH_MS = 4_102_444_800_000

CANONICAL_V1793_MODES: Tuple[str, ...] = (
    "OFF",
    "OBSERVE_ONLY",
    "MACRO_FILTER_ONLY",
    "UNICORN_ONLY",
    "MACRO_PLUS_UNICORN_V1792_SL_TP",
    "MACRO_PLUS_UNICORN_MTF_FULL",
    "OUTSIDE_MACRO_COUNTERFACTUAL",
)

V1793_MODE_ALIASES: Dict[str, str] = {
    "MACRO_PLUS_UNICORN_Plus_V1792_SL_TP": "MACRO_PLUS_UNICORN_V1792_SL_TP",
}

DEFAULT_NY_MACRO_WINDOWS_RAW: Tuple[Dict[str, Any], ...] = (
    {
        "window_id": "LONDON_OPEN_MACRO_0233_0300",
        "start_hhmm": "02:33",
        "end_hhmm": "03:00",
        "session_group": "LONDON_OPEN",
        "source": "RESEARCH_HYPOTHESIS_CONFIG",
        "enabled": True,
    },
    {
        "window_id": "LONDON_MACRO_0403_0430",
        "start_hhmm": "04:03",
        "end_hhmm": "04:30",
        "session_group": "LONDON",
        "source": "RESEARCH_HYPOTHESIS_CONFIG",
        "enabled": True,
    },
    {
        "window_id": "NY_AM_MACRO_0850_0910",
        "start_hhmm": "08:50",
        "end_hhmm": "09:10",
        "session_group": "NY_AM",
        "source": "RESEARCH_HYPOTHESIS_CONFIG",
        "enabled": True,
    },
    {
        "window_id": "NY_AM_MACRO_0950_1010",
        "start_hhmm": "09:50",
        "end_hhmm": "10:10",
        "session_group": "NY_AM",
        "source": "RESEARCH_HYPOTHESIS_CONFIG",
        "enabled": True,
    },
    {
        "window_id": "NY_AM_MACRO_1050_1110",
        "start_hhmm": "10:50",
        "end_hhmm": "11:10",
        "session_group": "NY_AM",
        "source": "RESEARCH_HYPOTHESIS_CONFIG",
        "enabled": True,
    },
    {
        "window_id": "NY_LUNCH_PM_MACRO_1150_1210",
        "start_hhmm": "11:50",
        "end_hhmm": "12:10",
        "session_group": "NY_LUNCH_PM",
        "source": "RESEARCH_HYPOTHESIS_CONFIG",
        "enabled": True,
    },
    {
        "window_id": "NY_PM_MACRO_1310_1340",
        "start_hhmm": "13:10",
        "end_hhmm": "13:40",
        "session_group": "NY_PM",
        "source": "RESEARCH_HYPOTHESIS_CONFIG",
        "enabled": True,
    },
)


def normalize_v1793_mode(raw_mode: Any) -> Tuple[str, bool, Optional[str]]:
    """
    Normalizes V17.9.3 mode string with explicit versioned migration alias support.
    Returns `(canonical_mode, is_valid_mode, migration_note)`.
    """
    if raw_mode is None:
        return "OFF", True, None
    if not isinstance(raw_mode, str):
        return "INVALID_V1793_MODE", False, None
    cleaned = raw_mode.strip()
    if not cleaned:
        return "OFF", True, None
    if cleaned in V1793_MODE_ALIASES:
        target = V1793_MODE_ALIASES[cleaned]
        return target, True, f"MODE_ALIAS_NORMALIZED:{cleaned}->{target}"
    if cleaned in CANONICAL_V1793_MODES:
        return cleaned, True, None
    return "INVALID_V1793_MODE", False, f"UNKNOWN_V1793_MODE:{cleaned}"


def _parse_hhmm(val: Any) -> Optional[Tuple[int, int, int]]:
    """
    Parses a strict 'HH:MM' 24-hour string into (hour, minute, minute_of_day).
    Returns None if malformed.
    """
    if not isinstance(val, str):
        return None
    s = val.strip()
    parts = s.split(":")
    if len(parts) != 2:
        return None
    if not (parts[0].isdigit() and parts[1].isdigit() and len(parts[0]) == 2 and len(parts[1]) == 2):
        return None
    hh = int(parts[0])
    mm = int(parts[1])
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        return None
    return hh, mm, hh * 60 + mm


@dataclass(frozen=True)
class MacroWindowSpec:
    """Immutable specification of a single ICT Macro Time Window in local schedule time."""

    window_id: str
    start_hhmm: str
    end_hhmm: str
    start_minute_of_day: int
    end_minute_of_day: int
    session_group: str
    source: str = "RESEARCH_HYPOTHESIS_CONFIG"
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "window_id": self.window_id,
            "start_hhmm": self.start_hhmm,
            "end_hhmm": self.end_hhmm,
            "start_minute_of_day": self.start_minute_of_day,
            "end_minute_of_day": self.end_minute_of_day,
            "duration_minutes": self.end_minute_of_day - self.start_minute_of_day,
            "session_group": self.session_group,
            "source": self.source,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class ValidatedMacroSchedule:
    """Immutable result of validating a V17.9.3 macro time schedule configuration."""

    is_valid: bool
    schedule_version: str
    timezone_name: str
    source: str
    weekdays: Tuple[int, ...]
    windows: Tuple[MacroWindowSpec, ...]
    allow_overlapping_windows: bool
    require_verified_holiday_calendar: bool
    verified_holiday_dates: Tuple[str, ...]
    calendar_verification_status: str
    xau_fix_window_status: str
    schedule_sha256: str
    validation_errors: Tuple[str, ...] = field(default_factory=tuple)
    reason_codes: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "schedule_version": self.schedule_version,
            "timezone_name": self.timezone_name,
            "source": self.source,
            "weekdays": list(self.weekdays),
            "windows": [w.to_dict() for w in self.windows],
            "allow_overlapping_windows": self.allow_overlapping_windows,
            "require_verified_holiday_calendar": self.require_verified_holiday_calendar,
            "verified_holiday_dates": list(self.verified_holiday_dates),
            "calendar_verification_status": self.calendar_verification_status,
            "xau_fix_window_status": self.xau_fix_window_status,
            "schedule_sha256": self.schedule_sha256,
            "validation_errors": list(self.validation_errors),
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class MacroTimeWindowState:
    """
    Pure causal ICT Macro Time Window evaluation state at `decision_timestamp_ms`.
    Half-open `[start, end)` semantics in `America/New_York` DST-aware local time.
    """

    symbol: str
    decision_timestamp_ms: int
    is_valid_timestamp: bool
    schedule_valid: bool
    schedule_version: str
    schedule_sha256: str
    source: str
    timezone_name: str
    utc_iso: Optional[str]
    ny_local_iso: Optional[str]
    ny_local_date: Optional[str]
    ny_local_hhmm: Optional[str]
    ny_weekday: Optional[int]
    utc_offset_minutes: Optional[int]
    is_dst: Optional[bool]
    is_weekend_or_closed: bool
    is_holiday_closed: bool
    calendar_verification_status: str
    xau_fix_window_status: str
    primary_window_id: str
    active_window_ids: Tuple[str, ...]
    session_group: str
    eligible_for_macro_gate: bool
    minutes_into_active_window: Optional[float]
    minutes_remaining_in_active_window: Optional[float]
    nearest_window_id: Optional[str]
    minutes_to_nearest_window: Optional[float]
    reason_codes: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "decision_timestamp_ms": self.decision_timestamp_ms,
            "is_valid_timestamp": self.is_valid_timestamp,
            "schedule_valid": self.schedule_valid,
            "schedule_version": self.schedule_version,
            "schedule_sha256": self.schedule_sha256,
            "source": self.source,
            "timezone_name": self.timezone_name,
            "utc_iso": self.utc_iso,
            "ny_local_iso": self.ny_local_iso,
            "ny_local_date": self.ny_local_date,
            "ny_local_hhmm": self.ny_local_hhmm,
            "ny_weekday": self.ny_weekday,
            "utc_offset_minutes": self.utc_offset_minutes,
            "is_dst": self.is_dst,
            "is_weekend_or_closed": self.is_weekend_or_closed,
            "is_holiday_closed": self.is_holiday_closed,
            "calendar_verification_status": self.calendar_verification_status,
            "xau_fix_window_status": self.xau_fix_window_status,
            "primary_window_id": self.primary_window_id,
            "active_window_ids": list(self.active_window_ids),
            "session_group": self.session_group,
            "eligible_for_macro_gate": self.eligible_for_macro_gate,
            "minutes_into_active_window": self.minutes_into_active_window,
            "minutes_remaining_in_active_window": self.minutes_remaining_in_active_window,
            "nearest_window_id": self.nearest_window_id,
            "minutes_to_nearest_window": self.minutes_to_nearest_window,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class MacroQualityAssessment:
    """Contextual macro quality assessment incorporating session restrictions and data freshness."""

    is_eligible: bool
    quality_tier: str
    primary_window_id: str
    session_restricted_override: bool
    causal_valid: bool
    quote_fresh: bool
    m1_bars_sufficient: bool
    macro_state: MacroTimeWindowState
    reason_codes: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_eligible": self.is_eligible,
            "quality_tier": self.quality_tier,
            "primary_window_id": self.primary_window_id,
            "session_restricted_override": self.session_restricted_override,
            "causal_valid": self.causal_valid,
            "quote_fresh": self.quote_fresh,
            "m1_bars_sufficient": self.m1_bars_sufficient,
            "macro_state": self.macro_state.to_dict(),
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class UnicornEpisodeBindingVerdict:
    """Causal episode binding verdict linking Unicorn evidence to the selected setup and MTF context."""

    is_bound: bool
    binding_mode: str  # "CANDIDATE_BOUND", "CAUSAL_SCAN_BOUND", "UNBOUND"
    setup_episode_id: Optional[str]
    unicorn_episode_id: Optional[str]
    breaker_id: Optional[str]
    fvg_id: Optional[str]
    broken_swing_id: Optional[str]
    mss_or_bos_id: Optional[str]
    displacement_id: Optional[str]
    sweep_id: Optional[str]
    same_causal_episode: bool
    causal_timestamps_valid: bool
    htf_alignment_status: str  # "ALIGNED", "CONFLICT", "MISSING_OR_UNVERIFIED"
    htf_bias_summary: Optional[str]
    reason_codes: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_bound": self.is_bound,
            "binding_mode": self.binding_mode,
            "setup_episode_id": self.setup_episode_id,
            "unicorn_episode_id": self.unicorn_episode_id,
            "breaker_id": self.breaker_id,
            "fvg_id": self.fvg_id,
            "broken_swing_id": self.broken_swing_id,
            "mss_or_bos_id": self.mss_or_bos_id,
            "displacement_id": self.displacement_id,
            "sweep_id": self.sweep_id,
            "same_causal_episode": self.same_causal_episode,
            "causal_timestamps_valid": self.causal_timestamps_valid,
            "htf_alignment_status": self.htf_alignment_status,
            "htf_bias_summary": self.htf_bias_summary,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class UnicornConfluenceAssessment:
    """
    Complete causal ICT Unicorn (Breaker Block + FVG + Displacement + Sweep + Retest + HTF)
    confluence assessment at `decision_timestamp_ms`.
    """

    symbol: str
    direction: str
    decision_timestamp_ms: int
    is_valid_unicorn: bool
    validity_status: str
    failure_stage: Optional[str]
    unicorn_id: Optional[str]
    setup_episode_id: Optional[str]
    breaker_id: Optional[str]
    breaker_type: Optional[str]
    breaker_low: Optional[float]
    breaker_high: Optional[float]
    breaker_distal_price: Optional[float]
    breaker_confirmed_at_ms: Optional[int]
    fvg_id: Optional[str]
    fvg_low: Optional[float]
    fvg_high: Optional[float]
    fvg_confirmed_at_ms: Optional[int]
    overlap_zone: Optional[Tuple[float, float]]
    overlap_width_price: Optional[float]
    invalidation_price: Optional[float]
    invalidation_source_id: Optional[str]
    retest_confirmed: bool
    retest_timestamp_ms: Optional[int]
    confirmed_at_ms: int
    known_at_ms: int
    sweep_provenance_verified: bool
    sweep_id: Optional[str]
    htf_aligned: bool
    htf_alignment_status: str
    episode_binding: UnicornEpisodeBindingVerdict
    scan_level_unicorn_status: Optional[str] = None
    scan_level_unicorn_id: Optional[str] = None
    rejection_reasons: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "decision_timestamp_ms": self.decision_timestamp_ms,
            "is_valid_unicorn": self.is_valid_unicorn,
            "validity_status": self.validity_status,
            "failure_stage": self.failure_stage,
            "unicorn_id": self.unicorn_id,
            "setup_episode_id": self.setup_episode_id,
            "breaker_id": self.breaker_id,
            "breaker_type": self.breaker_type,
            "breaker_low": self.breaker_low,
            "breaker_high": self.breaker_high,
            "breaker_distal_price": self.breaker_distal_price,
            "breaker_confirmed_at_ms": self.breaker_confirmed_at_ms,
            "fvg_id": self.fvg_id,
            "fvg_low": self.fvg_low,
            "fvg_high": self.fvg_high,
            "fvg_confirmed_at_ms": self.fvg_confirmed_at_ms,
            "overlap_zone": list(self.overlap_zone) if self.overlap_zone is not None else None,
            "overlap_width_price": self.overlap_width_price,
            "invalidation_price": self.invalidation_price,
            "invalidation_source_id": self.invalidation_source_id,
            "retest_confirmed": self.retest_confirmed,
            "retest_timestamp_ms": self.retest_timestamp_ms,
            "confirmed_at_ms": self.confirmed_at_ms,
            "known_at_ms": self.known_at_ms,
            "sweep_provenance_verified": self.sweep_provenance_verified,
            "sweep_id": self.sweep_id,
            "htf_aligned": self.htf_aligned,
            "htf_alignment_status": self.htf_alignment_status,
            "episode_binding": self.episode_binding.to_dict(),
            "scan_level_unicorn_status": self.scan_level_unicorn_status,
            "scan_level_unicorn_id": self.scan_level_unicorn_id,
            "rejection_reasons": list(self.rejection_reasons),
        }


@dataclass(frozen=True)
class UnicornInvalidationVerdict:
    """
    Verdict verifying that V17.9.2.1 structural SL respects Unicorn invalidation
    (`breaker_distal_price` and `overlap_zone`) with outward tick rounding.
    """

    is_valid: bool
    direction: str
    entry_price: float
    proposed_sl: Optional[float]
    v1792_invalidation_price: Optional[float]
    unicorn_invalidation_price: Optional[float]
    effective_invalidation_boundary: Optional[float]
    respects_entry_side: bool
    respects_unicorn_distal: bool
    respects_v1792_invalidation: bool
    outward_tick_aligned: bool
    reason_codes: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "proposed_sl": self.proposed_sl,
            "v1792_invalidation_price": self.v1792_invalidation_price,
            "unicorn_invalidation_price": self.unicorn_invalidation_price,
            "effective_invalidation_boundary": self.effective_invalidation_boundary,
            "respects_entry_side": self.respects_entry_side,
            "respects_unicorn_distal": self.respects_unicorn_distal,
            "respects_v1792_invalidation": self.respects_v1792_invalidation,
            "outward_tick_aligned": self.outward_tick_aligned,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class MacroUnicornPlanV1793:
    """
    Master V17.9.3 plan combining causal ICT Macro Time Window intelligence,
    causal Unicorn Breaker+FVG confluence, and V17.9.2.1 structural SL/TP validation.
    """

    schema_version: str
    mode: str
    raw_mode_input: str
    mode_valid: bool
    mode_migration_note: Optional[str]
    model_id: str
    symbol: str
    direction: str
    decision_timestamp_ms: int
    macro_state: MacroTimeWindowState
    macro_quality: MacroQualityAssessment
    unicorn_assessment: UnicornConfluenceAssessment
    invalidation_verdict: UnicornInvalidationVerdict
    macro_gate_passed: bool
    unicorn_gate_passed: bool
    invalidation_gate_passed: bool
    mtf_full_gate_passed: bool
    v1793_gates_passed: bool
    rejection_stage: Optional[str]
    rejection_reasons: Tuple[str, ...]
    observe_metadata_fingerprint: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "raw_mode_input": self.raw_mode_input,
            "mode_valid": self.mode_valid,
            "mode_migration_note": self.mode_migration_note,
            "model_id": self.model_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "decision_timestamp_ms": self.decision_timestamp_ms,
            "macro_state": self.macro_state.to_dict(),
            "macro_quality": self.macro_quality.to_dict(),
            "unicorn_assessment": self.unicorn_assessment.to_dict(),
            "invalidation_verdict": self.invalidation_verdict.to_dict(),
            "macro_gate_passed": self.macro_gate_passed,
            "unicorn_gate_passed": self.unicorn_gate_passed,
            "invalidation_gate_passed": self.invalidation_gate_passed,
            "mtf_full_gate_passed": self.mtf_full_gate_passed,
            "v1793_gates_passed": self.v1793_gates_passed,
            "rejection_stage": self.rejection_stage,
            "rejection_reasons": list(self.rejection_reasons),
            "observe_metadata_fingerprint": self.observe_metadata_fingerprint,
        }


# ==============================================================================
# FUNCTION #2: validate_macro_schedule_config
# ==============================================================================
def validate_macro_schedule_config(
    schedule_config: Optional[Mapping[str, Any]] = None,
) -> ValidatedMacroSchedule:
    """
    Validates a macro-time schedule configuration fail-closed.
    Enforces:
      - Valid IANA timezone (`America/New_York` default)
      - Non-empty `schedule_version`
      - Valid `weekdays` list (`0..6`)
      - Unique `window_id`s, valid `HH:MM` (`00:00`..`23:59`), `start < end` (no inverted or 0-len windows)
      - Non-overlapping enabled windows unless `allow_overlapping_windows=True`
      - `XAU_FIX_WINDOW` fails closed unless explicitly configured AND `xau_fix_verified=True`
    """
    errors: List[str] = []
    reasons: List[str] = []

    if schedule_config is not None and not isinstance(schedule_config, Mapping):
        return ValidatedMacroSchedule(
            is_valid=False,
            schedule_version="INVALID_SCHEDULE_CONFIG",
            timezone_name=DEFAULT_MACRO_TIMEZONE,
            source="INVALID",
            weekdays=(),
            windows=(),
            allow_overlapping_windows=False,
            require_verified_holiday_calendar=False,
            verified_holiday_dates=(),
            calendar_verification_status="CALENDAR_NOT_VERIFIED",
            xau_fix_window_status="NOT_CONFIGURED",
            schedule_sha256=hashlib.sha256(b"INVALID_SCHEDULE_CONFIG").hexdigest(),
            validation_errors=("SCHEDULE_CONFIG_NOT_A_MAPPING",),
            reason_codes=("MACRO_SCHEDULE_INVALID",),
        )

    cfg = dict(schedule_config) if schedule_config is not None else {}

    schedule_version = str(cfg.get("schedule_version", DEFAULT_MACRO_SCHEDULE_VERSION) or "").strip()
    if not schedule_version:
        errors.append("MISSING_SCHEDULE_VERSION")
        schedule_version = "INVALID_VERSION"

    tz_name = str(cfg.get("timezone", cfg.get("timezone_name", DEFAULT_MACRO_TIMEZONE)) or "").strip()
    if not tz_name:
        errors.append("MISSING_TIMEZONE")
        tz_name = DEFAULT_MACRO_TIMEZONE
    else:
        try:
            ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, KeyError, ValueError):
            errors.append(f"INVALID_TIMEZONE:{tz_name}")

    source = str(cfg.get("source", "RESEARCH_HYPOTHESIS_CONFIG") or "").strip()
    if not source:
        errors.append("MISSING_SCHEDULE_SOURCE")
        source = "UNKNOWN_SOURCE"

    raw_weekdays = cfg.get("weekdays", (0, 1, 2, 3, 4))
    weekdays_list: List[int] = []
    if not isinstance(raw_weekdays, (list, tuple)) or len(raw_weekdays) == 0:
        errors.append("INVALID_WEEKDAYS_COLLECTION")
    else:
        for d in raw_weekdays:
            if isinstance(d, bool) or not isinstance(d, int) or d < 0 or d > 6:
                errors.append(f"INVALID_WEEKDAY_VALUE:{d}")
            elif d not in weekdays_list:
                weekdays_list.append(d)

    allow_overlap = bool(cfg.get("allow_overlapping_windows", False))
    require_holiday_cal = bool(cfg.get("require_verified_holiday_calendar", False))
    raw_holidays = cfg.get("verified_holiday_dates", ())
    holiday_dates: List[str] = []
    if isinstance(raw_holidays, (list, tuple)):
        for h in raw_holidays:
            if isinstance(h, str) and len(h.strip()) == 10:
                holiday_dates.append(h.strip())
    cal_verified = bool(cfg.get("holiday_calendar_verified", False)) and len(holiday_dates) > 0
    cal_status = "VERIFIED" if cal_verified else "CALENDAR_NOT_VERIFIED"

    xau_fix_status = str(cfg.get("xau_fix_window_status", "NOT_CONFIGURED") or "NOT_CONFIGURED").strip()
    xau_fix_verified = bool(cfg.get("xau_fix_verified", False))

    raw_windows = cfg.get("windows", DEFAULT_NY_MACRO_WINDOWS_RAW)
    parsed_windows: List[MacroWindowSpec] = []
    seen_ids: set[str] = set()

    if not isinstance(raw_windows, (list, tuple)) or len(raw_windows) == 0:
        errors.append("EMPTY_OR_INVALID_WINDOWS_LIST")
    else:
        for idx, w in enumerate(raw_windows):
            if not isinstance(w, Mapping):
                errors.append(f"WINDOW_ITEM_NOT_MAPPING:{idx}")
                continue
            w_id = str(w.get("window_id", "") or "").strip()
            if not w_id:
                errors.append(f"MISSING_WINDOW_ID:{idx}")
                continue
            if w_id in seen_ids:
                errors.append(f"DUPLICATE_WINDOW_ID:{w_id}")
                continue
            seen_ids.add(w_id)

            w_enabled = bool(w.get("enabled", True))
            if w_id == "XAU_FIX_WINDOW":
                if not w_enabled:
                    xau_fix_status = "NOT_CONFIGURED"
                    continue
                if not xau_fix_verified or not w.get("source_authority"):
                    errors.append("XAU_FIX_WINDOW_UNVERIFIED_SOURCE")
                    xau_fix_status = "UNVERIFIED_REJECTED"
                    continue
                xau_fix_status = "VERIFIED_CONFIGURED"

            st_parsed = _parse_hhmm(w.get("start_hhmm"))
            en_parsed = _parse_hhmm(w.get("end_hhmm"))
            if st_parsed is None:
                errors.append(f"INVALID_START_HHMM:{w_id}:{w.get('start_hhmm')}")
                continue
            if en_parsed is None:
                errors.append(f"INVALID_END_HHMM:{w_id}:{w.get('end_hhmm')}")
                continue

            _, _, st_min = st_parsed
            _, _, en_min = en_parsed
            if st_min >= en_min:
                errors.append(
                    f"INVERTED_OR_ZERO_WINDOW:{w_id}:{w.get('start_hhmm')}->{w.get('end_hhmm')}"
                )
                continue

            w_group = str(w.get("session_group", "MACRO") or "MACRO").strip()
            w_src = str(w.get("source", source) or source).strip()
            parsed_windows.append(
                MacroWindowSpec(
                    window_id=w_id,
                    start_hhmm=str(w.get("start_hhmm")).strip(),
                    end_hhmm=str(w.get("end_hhmm")).strip(),
                    start_minute_of_day=st_min,
                    end_minute_of_day=en_min,
                    session_group=w_group,
                    source=w_src,
                    enabled=w_enabled,
                )
            )

    enabled_windows = [w for w in parsed_windows if w.enabled]
    if not enabled_windows and not errors:
        errors.append("NO_ENABLED_WINDOWS")

    if not allow_overlap and len(enabled_windows) > 1:
        sorted_w = sorted(enabled_windows, key=lambda x: (x.start_minute_of_day, x.end_minute_of_day))
        for i in range(len(sorted_w) - 1):
            w_curr = sorted_w[i]
            w_next = sorted_w[i + 1]
            if w_curr.end_minute_of_day > w_next.start_minute_of_day:
                errors.append(
                    f"OVERLAPPING_MACRO_WINDOWS:{w_curr.window_id}[{w_curr.start_hhmm}-{w_curr.end_hhmm})"
                    f"~{w_next.window_id}[{w_next.start_hhmm}-{w_next.end_hhmm})"
                )

    is_valid = len(errors) == 0
    if not is_valid:
        reasons.append("MACRO_SCHEDULE_INVALID")

    canonical_payload = {
        "schedule_version": schedule_version,
        "timezone_name": tz_name,
        "source": source,
        "weekdays": sorted(weekdays_list),
        "allow_overlapping_windows": allow_overlap,
        "require_verified_holiday_calendar": require_holiday_cal,
        "verified_holiday_dates": sorted(holiday_dates),
        "xau_fix_window_status": xau_fix_status,
        "windows": [w.to_dict() for w in parsed_windows],
        "validation_errors": errors,
    }
    sched_sha = hashlib.sha256(
        json.dumps(canonical_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    return ValidatedMacroSchedule(
        is_valid=is_valid,
        schedule_version=schedule_version,
        timezone_name=tz_name,
        source=source,
        weekdays=tuple(sorted(weekdays_list)),
        windows=tuple(parsed_windows),
        allow_overlapping_windows=allow_overlap,
        require_verified_holiday_calendar=require_holiday_cal,
        verified_holiday_dates=tuple(sorted(holiday_dates)),
        calendar_verification_status=cal_status,
        xau_fix_window_status=xau_fix_status,
        schedule_sha256=sched_sha,
        validation_errors=tuple(errors),
        reason_codes=tuple(reasons),
    )


_DEFAULT_VALIDATED_SCHEDULE: Optional[ValidatedMacroSchedule] = None
_DEFAULT_MACRO_STATE_MEMO: Dict[Tuple[int, str], MacroTimeWindowState] = {}


# ==============================================================================
# FUNCTION #1: resolve_causal_macro_time_window
# ==============================================================================
def resolve_causal_macro_time_window(
    decision_timestamp_ms: int,
    symbol: str = "XAUUSDT",
    *,
    schedule_config: Optional[Mapping[str, Any]] = None,
) -> MacroTimeWindowState:
    """
    Pure causal resolution of `decision_timestamp_ms` into ICT Macro Time Window state.
    Zero wall-clock usage. Uses `ZoneInfo("America/New_York")` for exact US DST handling
    and half-open `[start, end)` window boundaries.
    """
    global _DEFAULT_VALIDATED_SCHEDULE
    norm_symbol = str(symbol or "XAUUSDT").strip().upper() or "XAUUSDT"
    if (
        schedule_config is None
        and isinstance(decision_timestamp_ms, int)
        and not isinstance(decision_timestamp_ms, bool)
    ):
        memo_key = (decision_timestamp_ms, norm_symbol)
        hit = _DEFAULT_MACRO_STATE_MEMO.get(memo_key)
        if hit is not None:
            return hit
        if _DEFAULT_VALIDATED_SCHEDULE is None:
            _DEFAULT_VALIDATED_SCHEDULE = validate_macro_schedule_config(None)
        sched = _DEFAULT_VALIDATED_SCHEDULE
    else:
        memo_key = None
        sched = validate_macro_schedule_config(schedule_config)

    # 1. Fail-closed timestamp validation
    if (
        isinstance(decision_timestamp_ms, bool)
        or not isinstance(decision_timestamp_ms, int)
        or decision_timestamp_ms < MIN_PLAUSIBLE_EPOCH_MS
        or decision_timestamp_ms > MAX_PLAUSIBLE_EPOCH_MS
    ):
        rej = ["MACRO_TIMESTAMP_INVALID"]
        if not sched.is_valid:
            rej.append("MACRO_SCHEDULE_INVALID")
        return MacroTimeWindowState(
            symbol=norm_symbol,
            decision_timestamp_ms=int(decision_timestamp_ms) if isinstance(decision_timestamp_ms, int) and not isinstance(decision_timestamp_ms, bool) else -1,
            is_valid_timestamp=False,
            schedule_valid=sched.is_valid,
            schedule_version=sched.schedule_version,
            schedule_sha256=sched.schedule_sha256,
            source=sched.source,
            timezone_name=sched.timezone_name,
            utc_iso=None,
            ny_local_iso=None,
            ny_local_date=None,
            ny_local_hhmm=None,
            ny_weekday=None,
            utc_offset_minutes=None,
            is_dst=None,
            is_weekend_or_closed=False,
            is_holiday_closed=False,
            calendar_verification_status=sched.calendar_verification_status,
            xau_fix_window_status=sched.xau_fix_window_status,
            primary_window_id="INVALID_TIMESTAMP",
            active_window_ids=(),
            session_group="INVALID",
            eligible_for_macro_gate=False,
            minutes_into_active_window=None,
            minutes_remaining_in_active_window=None,
            nearest_window_id=None,
            minutes_to_nearest_window=None,
            reason_codes=tuple(rej),
        )

    # 2. Fail-closed schedule validation
    if not sched.is_valid:
        dt_utc = datetime.fromtimestamp(decision_timestamp_ms / 1000.0, tz=timezone.utc)
        return MacroTimeWindowState(
            symbol=norm_symbol,
            decision_timestamp_ms=decision_timestamp_ms,
            is_valid_timestamp=True,
            schedule_valid=False,
            schedule_version=sched.schedule_version,
            schedule_sha256=sched.schedule_sha256,
            source=sched.source,
            timezone_name=sched.timezone_name,
            utc_iso=dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            ny_local_iso=None,
            ny_local_date=None,
            ny_local_hhmm=None,
            ny_weekday=None,
            utc_offset_minutes=None,
            is_dst=None,
            is_weekend_or_closed=False,
            is_holiday_closed=False,
            calendar_verification_status=sched.calendar_verification_status,
            xau_fix_window_status=sched.xau_fix_window_status,
            primary_window_id="INVALID_SCHEDULE",
            active_window_ids=(),
            session_group="INVALID_SCHEDULE",
            eligible_for_macro_gate=False,
            minutes_into_active_window=None,
            minutes_remaining_in_active_window=None,
            nearest_window_id=None,
            minutes_to_nearest_window=None,
            reason_codes=("MACRO_SCHEDULE_INVALID",),
        )

    dt_utc = datetime.fromtimestamp(decision_timestamp_ms / 1000.0, tz=timezone.utc)
    ny_tz = ZoneInfo(sched.timezone_name)
    dt_ny = dt_utc.astimezone(ny_tz)

    utc_iso = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    ny_iso = dt_ny.isoformat()
    ny_date = dt_ny.strftime("%Y-%m-%d")
    ny_hhmm = dt_ny.strftime("%H:%M")
    ny_weekday = dt_ny.weekday()
    offset_sec = dt_ny.utcoffset().total_seconds() if dt_ny.utcoffset() is not None else 0.0
    utc_offset_min = int(round(offset_sec / 60.0))
    dst_delta = dt_ny.dst()
    is_dst = bool(dst_delta is not None and dst_delta.total_seconds() != 0.0)

    exact_minute_of_day = (
        dt_ny.hour * 60.0
        + dt_ny.minute
        + (dt_ny.second / 60.0)
        + (dt_ny.microsecond / 60_000_000.0)
    )

    enabled_windows = [w for w in sched.windows if w.enabled]

    # Compute nearest window distance in minutes on the same day
    nearest_id: Optional[str] = None
    nearest_dist: Optional[float] = None
    for w in enabled_windows:
        if w.start_minute_of_day <= exact_minute_of_day < w.end_minute_of_day:
            dist = 0.0
        elif exact_minute_of_day < w.start_minute_of_day:
            dist = round(w.start_minute_of_day - exact_minute_of_day, 4)
        else:
            dist = round(exact_minute_of_day - w.end_minute_of_day, 4)
        if nearest_dist is None or dist < nearest_dist:
            nearest_dist = dist
            nearest_id = w.window_id

    # 3. Weekend / non-scheduled weekday check
    if ny_weekday not in sched.weekdays:
        return MacroTimeWindowState(
            symbol=norm_symbol,
            decision_timestamp_ms=decision_timestamp_ms,
            is_valid_timestamp=True,
            schedule_valid=True,
            schedule_version=sched.schedule_version,
            schedule_sha256=sched.schedule_sha256,
            source=sched.source,
            timezone_name=sched.timezone_name,
            utc_iso=utc_iso,
            ny_local_iso=ny_iso,
            ny_local_date=ny_date,
            ny_local_hhmm=ny_hhmm,
            ny_weekday=ny_weekday,
            utc_offset_minutes=utc_offset_min,
            is_dst=is_dst,
            is_weekend_or_closed=True,
            is_holiday_closed=False,
            calendar_verification_status=sched.calendar_verification_status,
            xau_fix_window_status=sched.xau_fix_window_status,
            primary_window_id="OUTSIDE_MACRO_WINDOW",
            active_window_ids=(),
            session_group="WEEKEND_CLOSED",
            eligible_for_macro_gate=False,
            minutes_into_active_window=None,
            minutes_remaining_in_active_window=None,
            nearest_window_id=nearest_id,
            minutes_to_nearest_window=nearest_dist,
            reason_codes=("MACRO_WEEKEND_CLOSED", "MACRO_OUTSIDE_WINDOW"),
        )

    # 4. Holiday calendar check
    if ny_date in sched.verified_holiday_dates:
        return MacroTimeWindowState(
            symbol=norm_symbol,
            decision_timestamp_ms=decision_timestamp_ms,
            is_valid_timestamp=True,
            schedule_valid=True,
            schedule_version=sched.schedule_version,
            schedule_sha256=sched.schedule_sha256,
            source=sched.source,
            timezone_name=sched.timezone_name,
            utc_iso=utc_iso,
            ny_local_iso=ny_iso,
            ny_local_date=ny_date,
            ny_local_hhmm=ny_hhmm,
            ny_weekday=ny_weekday,
            utc_offset_minutes=utc_offset_min,
            is_dst=is_dst,
            is_weekend_or_closed=False,
            is_holiday_closed=True,
            calendar_verification_status=sched.calendar_verification_status,
            xau_fix_window_status=sched.xau_fix_window_status,
            primary_window_id="OUTSIDE_MACRO_WINDOW",
            active_window_ids=(),
            session_group="HOLIDAY_CLOSED",
            eligible_for_macro_gate=False,
            minutes_into_active_window=None,
            minutes_remaining_in_active_window=None,
            nearest_window_id=nearest_id,
            minutes_to_nearest_window=nearest_dist,
            reason_codes=("MACRO_HOLIDAY_CLOSED", "MACRO_OUTSIDE_WINDOW"),
        )

    if sched.require_verified_holiday_calendar and sched.calendar_verification_status != "VERIFIED":
        return MacroTimeWindowState(
            symbol=norm_symbol,
            decision_timestamp_ms=decision_timestamp_ms,
            is_valid_timestamp=True,
            schedule_valid=True,
            schedule_version=sched.schedule_version,
            schedule_sha256=sched.schedule_sha256,
            source=sched.source,
            timezone_name=sched.timezone_name,
            utc_iso=utc_iso,
            ny_local_iso=ny_iso,
            ny_local_date=ny_date,
            ny_local_hhmm=ny_hhmm,
            ny_weekday=ny_weekday,
            utc_offset_minutes=utc_offset_min,
            is_dst=is_dst,
            is_weekend_or_closed=False,
            is_holiday_closed=False,
            calendar_verification_status=sched.calendar_verification_status,
            xau_fix_window_status=sched.xau_fix_window_status,
            primary_window_id="OUTSIDE_MACRO_WINDOW",
            active_window_ids=(),
            session_group="CALENDAR_UNVERIFIED",
            eligible_for_macro_gate=False,
            minutes_into_active_window=None,
            minutes_remaining_in_active_window=None,
            nearest_window_id=nearest_id,
            minutes_to_nearest_window=nearest_dist,
            reason_codes=("MACRO_CALENDAR_NOT_VERIFIED",),
        )

    # 5. Half-open interval [start_minute_of_day, end_minute_of_day) match
    active_matches = [
        w
        for w in enabled_windows
        if float(w.start_minute_of_day) <= exact_minute_of_day < float(w.end_minute_of_day)
    ]

    if active_matches:
        primary = active_matches[0]
        into_min = round(exact_minute_of_day - float(primary.start_minute_of_day), 4)
        rem_min = round(float(primary.end_minute_of_day) - exact_minute_of_day, 4)
        res = MacroTimeWindowState(
            symbol=norm_symbol,
            decision_timestamp_ms=decision_timestamp_ms,
            is_valid_timestamp=True,
            schedule_valid=True,
            schedule_version=sched.schedule_version,
            schedule_sha256=sched.schedule_sha256,
            source=primary.source,
            timezone_name=sched.timezone_name,
            utc_iso=utc_iso,
            ny_local_iso=ny_iso,
            ny_local_date=ny_date,
            ny_local_hhmm=ny_hhmm,
            ny_weekday=ny_weekday,
            utc_offset_minutes=utc_offset_min,
            is_dst=is_dst,
            is_weekend_or_closed=False,
            is_holiday_closed=False,
            calendar_verification_status=sched.calendar_verification_status,
            xau_fix_window_status=sched.xau_fix_window_status,
            primary_window_id=primary.window_id,
            active_window_ids=tuple(w.window_id for w in active_matches),
            session_group=primary.session_group,
            eligible_for_macro_gate=True,
            minutes_into_active_window=into_min,
            minutes_remaining_in_active_window=rem_min,
            nearest_window_id=primary.window_id,
            minutes_to_nearest_window=0.0,
            reason_codes=(),
        )
    else:
        res = MacroTimeWindowState(
            symbol=norm_symbol,
            decision_timestamp_ms=decision_timestamp_ms,
            is_valid_timestamp=True,
            schedule_valid=True,
            schedule_version=sched.schedule_version,
            schedule_sha256=sched.schedule_sha256,
            source=sched.source,
            timezone_name=sched.timezone_name,
            utc_iso=utc_iso,
            ny_local_iso=ny_iso,
            ny_local_date=ny_date,
            ny_local_hhmm=ny_hhmm,
            ny_weekday=ny_weekday,
            utc_offset_minutes=utc_offset_min,
            is_dst=is_dst,
            is_weekend_or_closed=False,
            is_holiday_closed=False,
            calendar_verification_status=sched.calendar_verification_status,
            xau_fix_window_status=sched.xau_fix_window_status,
            primary_window_id="OUTSIDE_MACRO_WINDOW",
            active_window_ids=(),
            session_group="OUTSIDE_MACRO",
            eligible_for_macro_gate=False,
            minutes_into_active_window=None,
            minutes_remaining_in_active_window=None,
            nearest_window_id=nearest_id,
            minutes_to_nearest_window=nearest_dist,
            reason_codes=("MACRO_OUTSIDE_WINDOW",),
        )
    if memo_key is not None:
        if len(_DEFAULT_MACRO_STATE_MEMO) >= 256:
            _DEFAULT_MACRO_STATE_MEMO.clear()
        _DEFAULT_MACRO_STATE_MEMO[memo_key] = res
    return res


# ==============================================================================
# FUNCTION #3: classify_macro_context_quality
# ==============================================================================
def classify_macro_context_quality(
    macro_state: MacroTimeWindowState,
    context: Any,
) -> MacroQualityAssessment:
    """
    Combines `MacroTimeWindowState` with `MarketDecisionContext` session restrictions,
    causal validity, and market data freshness.
    Hard session restrictions (`session_restricted=True`) ALWAYS take precedence.
    """
    if context is None:
        return MacroQualityAssessment(
            is_eligible=False,
            quality_tier="MISSING_CONTEXT",
            primary_window_id=macro_state.primary_window_id,
            session_restricted_override=False,
            causal_valid=False,
            quote_fresh=False,
            m1_bars_sufficient=False,
            macro_state=macro_state,
            reason_codes=("MACRO_CONTEXT_MISSING",),
        )

    policy_dict = context.get_policy_dict() if hasattr(context, "get_policy_dict") else {}
    sess_restricted = bool(
        getattr(context, "session_restricted", False)
        or policy_dict.get("session_restricted", False)
    )
    sess_reason = str(
        getattr(context, "session_restriction_reason", None)
        or policy_dict.get("session_restriction_reason", None)
        or "SESSION_TIME_RESTRICTED"
    )
    causal_ok = bool(getattr(context, "causal_valid", True))
    closed_m1 = getattr(context, "closed_m1_candles", None) or ()
    m1_ok = len(closed_m1) >= 15

    bid = getattr(context, "bid", None)
    ask = getattr(context, "ask", None)
    quote_stale = bool(getattr(context, "quote_stale", False))
    quote_ok = (
        not quote_stale
        and bid is not None
        and ask is not None
        and float(bid) > 0.0
        and float(ask) >= float(bid)
    )

    if sess_restricted:
        reasons = [sess_reason]
        if "SESSION_TIME_RESTRICTED" not in reasons:
            reasons.append("SESSION_TIME_RESTRICTED")
        for r in macro_state.reason_codes:
            if r not in reasons:
                reasons.append(r)
        return MacroQualityAssessment(
            is_eligible=False,
            quality_tier="SESSION_RESTRICTED",
            primary_window_id=macro_state.primary_window_id,
            session_restricted_override=True,
            causal_valid=causal_ok,
            quote_fresh=quote_ok,
            m1_bars_sufficient=m1_ok,
            macro_state=macro_state,
            reason_codes=tuple(reasons),
        )

    if not causal_ok:
        return MacroQualityAssessment(
            is_eligible=False,
            quality_tier="CAUSAL_INVALID",
            primary_window_id=macro_state.primary_window_id,
            session_restricted_override=False,
            causal_valid=False,
            quote_fresh=quote_ok,
            m1_bars_sufficient=m1_ok,
            macro_state=macro_state,
            reason_codes=("CAUSAL_CUTOFF_VIOLATION",),
        )

    if not macro_state.is_valid_timestamp:
        return MacroQualityAssessment(
            is_eligible=False,
            quality_tier="INVALID_TIMESTAMP",
            primary_window_id=macro_state.primary_window_id,
            session_restricted_override=False,
            causal_valid=causal_ok,
            quote_fresh=quote_ok,
            m1_bars_sufficient=m1_ok,
            macro_state=macro_state,
            reason_codes=macro_state.reason_codes or ("MACRO_TIMESTAMP_INVALID",),
        )

    if not macro_state.schedule_valid:
        return MacroQualityAssessment(
            is_eligible=False,
            quality_tier="INVALID_SCHEDULE",
            primary_window_id=macro_state.primary_window_id,
            session_restricted_override=False,
            causal_valid=causal_ok,
            quote_fresh=quote_ok,
            m1_bars_sufficient=m1_ok,
            macro_state=macro_state,
            reason_codes=macro_state.reason_codes or ("MACRO_SCHEDULE_INVALID",),
        )

    if not quote_ok or not m1_ok:
        return MacroQualityAssessment(
            is_eligible=False,
            quality_tier="STALE_OR_MISSING_MARKET_DATA",
            primary_window_id=macro_state.primary_window_id,
            session_restricted_override=False,
            causal_valid=causal_ok,
            quote_fresh=quote_ok,
            m1_bars_sufficient=m1_ok,
            macro_state=macro_state,
            reason_codes=("MACRO_MARKET_DATA_STALE_OR_MISSING",),
        )

    if not macro_state.eligible_for_macro_gate:
        return MacroQualityAssessment(
            is_eligible=False,
            quality_tier="OUTSIDE_MACRO_WINDOW",
            primary_window_id=macro_state.primary_window_id,
            session_restricted_override=False,
            causal_valid=causal_ok,
            quote_fresh=quote_ok,
            m1_bars_sufficient=m1_ok,
            macro_state=macro_state,
            reason_codes=macro_state.reason_codes or ("MACRO_OUTSIDE_WINDOW",),
        )

    return MacroQualityAssessment(
        is_eligible=True,
        quality_tier="MACRO_WINDOW_ACTIVE",
        primary_window_id=macro_state.primary_window_id,
        session_restricted_override=False,
        causal_valid=True,
        quote_fresh=True,
        m1_bars_sufficient=True,
        macro_state=macro_state,
        reason_codes=(),
    )


# ==============================================================================
# FUNCTION #5: bind_unicorn_to_causal_episode
# ==============================================================================
def bind_unicorn_to_causal_episode(
    unicorn_evidence: Optional[UnicornEvidence],
    selected_setup: Optional[Mapping[str, Any]],
    mtf_context: Any,
    decision_timestamp_ms: int,
) -> UnicornEpisodeBindingVerdict:
    """
    Verifies that a `UnicornEvidence` instance is causally bound to the active setup
    episode and checks HTF (`5M`/`15M`/`1H`/`4H`) directional alignment.
    """
    reasons: List[str] = []
    setup_map = dict(selected_setup) if isinstance(selected_setup, Mapping) else {}
    setup_ep_id = str(setup_map.get("episode_id") or "") or None

    # Evaluate HTF alignment from mtf_context
    htf_status = "MISSING_OR_UNVERIFIED"
    htf_bias_summary: Optional[str] = None
    norm_dir = str(
        (unicorn_evidence.direction if unicorn_evidence is not None else setup_map.get("direction"))
        or "LONG"
    ).upper()

    if mtf_context is not None:
        mtf_struct = getattr(mtf_context, "mtf_structure", None)
        tf_states = getattr(mtf_context, "timeframe_states", None)
        if mtf_struct is not None:
            biases_real: Dict[str, str] = {}
            for tf_label, tf_attr in (("15M", "m15"), ("1H", "h1"), ("4H", "h4")):
                tf_ctx = getattr(mtf_struct, tf_attr, None)
                if tf_ctx is not None:
                    t_val = getattr(tf_ctx, "trend", None)
                    t_str = t_val.value if hasattr(t_val, "value") else str(t_val or "UNKNOWN")
                    biases_real[tf_label] = t_str
            htf_bias_summary = ",".join(f"{k}:{v}" for k, v in biases_real.items()) if biases_real else None
            mtf_q = str(getattr(mtf_context, "mtf_quality_state", "VALID") or "VALID")
            mtf_ok, mtf_rej, _ = evaluate_mtf_eligibility_for_direction(
                direction=norm_dir,
                mtf_context=mtf_context,
                ablation_mode="STRUCTURE_ONLY",
                mtf_quality_state=mtf_q,
            )
            if mtf_ok:
                htf_status = "ALIGNED"
            elif mtf_rej and ("MISSING" in mtf_rej or "INSUFFICIENT" in mtf_rej or "FAILED" in mtf_rej or "INVALID" in mtf_rej):
                htf_status = "MISSING_OR_UNVERIFIED"
            else:
                htf_status = "CONFLICT"
        elif isinstance(tf_states, Mapping) and len(tf_states) > 0:
            biases: Dict[str, str] = {}
            for tf_k in ("5M", "15M", "1H", "4H"):
                st = tf_states.get(tf_k)
                if st is not None:
                    b_val = getattr(st, "structure_bias", None) or getattr(st, "trend", None)
                    b_str = b_val.value if hasattr(b_val, "value") else str(b_val or "UNKNOWN")
                    biases[tf_k] = b_str
            htf_bias_summary = ",".join(f"{k}:{v}" for k, v in biases.items())
            opposing = "BEARISH" if norm_dir == "LONG" else "BULLISH"
            if any(biases.get(k, "UNKNOWN") in ("MISSING", "UNKNOWN") for k in ("15M", "1H", "4H") if k in biases):
                htf_status = "MISSING_OR_UNVERIFIED"
            elif (
                (biases.get("4H") == opposing and biases.get("1H") == opposing)
                or (biases.get("1H") == opposing and biases.get("15M") == opposing)
            ):
                htf_status = "CONFLICT"
            else:
                htf_status = "ALIGNED"

    if unicorn_evidence is None:
        reasons.append("UNICORN_MISSING_DATA")
        return UnicornEpisodeBindingVerdict(
            is_bound=False,
            binding_mode="UNBOUND",
            setup_episode_id=setup_ep_id,
            unicorn_episode_id=None,
            breaker_id=None,
            fvg_id=None,
            broken_swing_id=None,
            mss_or_bos_id=None,
            displacement_id=None,
            sweep_id=None,
            same_causal_episode=False,
            causal_timestamps_valid=False,
            htf_alignment_status=htf_status,
            htf_bias_summary=htf_bias_summary,
            reason_codes=tuple(reasons),
        )

    # Check causal timestamps on unicorn_evidence
    b_conf = int(unicorn_evidence.breaker_confirmed_at_ms or 0)
    f_conf = int(unicorn_evidence.fvg_confirmed_at_ms or 0)
    u_conf = int(unicorn_evidence.confirmed_at_ms or 0)
    u_known = int(unicorn_evidence.known_at_ms or 0)
    r_ts = unicorn_evidence.retest_timestamp_ms

    causal_ts_ok = (
        (b_conf <= decision_timestamp_ms if b_conf > 0 else True)
        and (f_conf <= decision_timestamp_ms if f_conf > 0 else True)
        and (u_conf <= decision_timestamp_ms if u_conf > 0 else True)
        and (u_known <= decision_timestamp_ms if u_known > 0 else True)
        and (int(r_ts) + 60_000 <= decision_timestamp_ms if r_ts is not None else True)
    )
    if not causal_ts_ok:
        reasons.append("UNICORN_FUTURE_DATA_REJECTED")

    sel_fvg = setup_map.get("selected_fvg")
    sel_break = setup_map.get("selected_break")
    sel_sweep = setup_map.get("selected_sweep")

    # Verify selected_fvg match if setup explicitly specified a selected_fvg
    fvg_matches = True
    if isinstance(sel_fvg, Mapping) and sel_fvg.get("id"):
        req_fvg_id = str(sel_fvg["id"])
        if unicorn_evidence.fvg_id and str(unicorn_evidence.fvg_id) != req_fvg_id:
            fvg_matches = False
            reasons.append("UNICORN_EPISODE_MISMATCH")

    # Verify selected_break match if setup explicitly specified a broken_swing_id
    break_matches = True
    if isinstance(sel_break, Mapping) and sel_break.get("broken_swing_id") and isinstance(sel_fvg, Mapping):
        req_sw_id = str(sel_break["broken_swing_id"])
        if unicorn_evidence.broken_swing_id and str(unicorn_evidence.broken_swing_id) != req_sw_id:
            break_matches = False
            if "UNICORN_EPISODE_MISMATCH" not in reasons:
                reasons.append("UNICORN_EPISODE_MISMATCH")

    same_ep = bool(
        unicorn_evidence.is_valid
        and unicorn_evidence.breaker_id
        and unicorn_evidence.fvg_id
        and unicorn_evidence.broken_swing_id
        and unicorn_evidence.mss_or_bos_id
        and unicorn_evidence.displacement_id
        and fvg_matches
        and break_matches
    )
    if not same_ep and "UNICORN_EPISODE_MISMATCH" not in reasons:
        if unicorn_evidence.validity_status == UnicornSetupStatus.EPISODE_MISMATCH.value:
            reasons.append("UNICORN_EPISODE_MISMATCH")

    eff_sweep_id = unicorn_evidence.sweep_id or (
        str(sel_sweep.get("id")) if isinstance(sel_sweep, Mapping) and sel_sweep.get("id") else None
    )
    if eff_sweep_id is None and mtf_context is not None:
        eval_liq = getattr(mtf_context, "evaluator_liquidity", None)
        last_sw_obj = getattr(eval_liq, "last_sweep", None) if eval_liq is not None else None
        if last_sw_obj is not None and getattr(last_sw_obj, "event_id", None):
            avail_dt = getattr(last_sw_obj, "available_at", None) or getattr(last_sw_obj, "confirmed_at", None)
            if avail_dt is not None and int(avail_dt.timestamp() * 1000) <= decision_timestamp_ms:
                eff_sweep_id = str(last_sw_obj.event_id)
        if eff_sweep_id is None:
            tf_states = getattr(mtf_context, "timeframe_states", None)
            if isinstance(tf_states, Mapping):
                for tf_k in ("1M", "5M", "15M"):
                    st = tf_states.get(tf_k)
                    last_sw = getattr(st, "last_sweep", None) if st is not None else None
                    if isinstance(last_sw, Mapping) and last_sw.get("sweep_id"):
                        sw_known = int(last_sw.get("known_at_ms") or last_sw.get("confirmed_at_ms") or 0)
                        if 0 < sw_known <= decision_timestamp_ms:
                            eff_sweep_id = str(last_sw["sweep_id"])
                            break

    binding_mode = "UNBOUND"
    if same_ep and causal_ts_ok:
        binding_mode = "CANDIDATE_BOUND" if isinstance(sel_fvg, Mapping) else "CAUSAL_SCAN_BOUND"

    is_bound = bool(same_ep and causal_ts_ok and len(reasons) == 0)
    return UnicornEpisodeBindingVerdict(
        is_bound=is_bound,
        binding_mode=binding_mode,
        setup_episode_id=setup_ep_id,
        unicorn_episode_id=unicorn_evidence.setup_episode_id,
        breaker_id=unicorn_evidence.breaker_id,
        fvg_id=unicorn_evidence.fvg_id,
        broken_swing_id=unicorn_evidence.broken_swing_id,
        mss_or_bos_id=unicorn_evidence.mss_or_bos_id,
        displacement_id=unicorn_evidence.displacement_id,
        sweep_id=eff_sweep_id,
        same_causal_episode=same_ep,
        causal_timestamps_valid=causal_ts_ok,
        htf_alignment_status=htf_status,
        htf_bias_summary=htf_bias_summary,
        reason_codes=tuple(reasons),
    )


def _map_unicorn_status_to_v1793_reason(status_str: str, raw_reasons: Sequence[str]) -> str:
    """Maps `UnicornSetupStatus` to canonical V17.9.3 `UNICORN_*` reason code."""
    mapping = {
        UnicornSetupStatus.INVALID_DATA.value: "UNICORN_MISSING_DATA",
        UnicornSetupStatus.WAITING_EVIDENCE.value: "UNICORN_MISSING_DATA",
        UnicornSetupStatus.FUTURE_DATA_REJECTED.value: "UNICORN_FUTURE_DATA_REJECTED",
        UnicornSetupStatus.UNCLOSED_EVIDENCE_REJECTED.value: "UNICORN_UNCLOSED_BAR_REJECTED",
        UnicornSetupStatus.MISSING_BREAKER.value: "UNICORN_MISSING_BREAKER",
        UnicornSetupStatus.MISSING_FVG.value: "UNICORN_MISSING_FVG",
        UnicornSetupStatus.NO_GEOMETRIC_OVERLAP.value: "UNICORN_NO_GEOMETRIC_OVERLAP",
        UnicornSetupStatus.WRONG_DIRECTION_OVERLAP.value: "UNICORN_NO_GEOMETRIC_OVERLAP",
        UnicornSetupStatus.EPISODE_MISMATCH.value: "UNICORN_EPISODE_MISMATCH",
        UnicornSetupStatus.DISPLACEMENT_WEAK.value: "UNICORN_DISPLACEMENT_WEAK",
        UnicornSetupStatus.CONSUMED_OR_INVALIDATED_FVG.value: "UNICORN_CONSUMED_OR_INVALIDATED_FVG",
        UnicornSetupStatus.RETEST_BEFORE_CONFIRMATION.value: "UNICORN_RETEST_BEFORE_CONFIRMATION",
        UnicornSetupStatus.WAITING_RETEST.value: "UNICORN_WAITING_CONFIRMED_RETEST",
        UnicornSetupStatus.UNICORN_INVALIDATED.value: "UNICORN_INVALIDATED_ZONE",
        UnicornSetupStatus.MISSING_CAUSAL_PROVENANCE.value: "UNICORN_EPISODE_MISMATCH",
        UnicornSetupStatus.UNICORN_EVIDENCE_INSUFFICIENT.value: "UNICORN_MISSING_DATA",
    }
    if status_str in mapping:
        return mapping[status_str]
    for r in raw_reasons:
        if "FUTURE" in r:
            return "UNICORN_FUTURE_DATA_REJECTED"
        if "UNCLOSED" in r:
            return "UNICORN_UNCLOSED_BAR_REJECTED"
    return f"UNICORN_{status_str}"


_SCAN_UNICORN_MEMO: Dict[Tuple[Any, ...], Tuple[Any, UnicornEvidence]] = {}


def _detect_scan_unicorn_memoized(
    raw_m1_seq: Any,
    closed_m1: List[Any],
    direction: str,
    as_of_ms: int,
    symbol: str,
    strict: bool,
) -> UnicornEvidence:
    key = (
        id(raw_m1_seq),
        as_of_ms,
        len(closed_m1),
        _candle_ohlc(closed_m1[-1]) if closed_m1 else (),
        _candle_ohlc(closed_m1[-2]) if len(closed_m1) >= 2 else (),
        direction,
        symbol,
        bool(strict),
    )
    hit = _SCAN_UNICORN_MEMO.get(key)
    if hit is not None and hit[0] is raw_m1_seq:
        return hit[1]
    ev = detect_unicorn_evidence(
        closed_bars=closed_m1,
        direction=direction,
        as_of_ms=as_of_ms,
        symbol=symbol,
        timeframe="1M",
        strict_causal_binding=bool(strict),
    )
    if len(_SCAN_UNICORN_MEMO) >= 256:
        _SCAN_UNICORN_MEMO.clear()
    _SCAN_UNICORN_MEMO[key] = (raw_m1_seq, ev)
    return ev


_ASSESS_UNICORN_NO_SETUP_MEMO: Dict[Tuple[Any, ...], Tuple[Any, UnicornConfluenceAssessment]] = {}


# ==============================================================================
# FUNCTION #4: evaluate_causal_unicorn_confluence
# ==============================================================================
def evaluate_causal_unicorn_confluence(
    context: Any,
    direction: str,
    decision_timestamp_ms: int,
    *,
    setup_descriptors: Optional[Mapping[str, Any]] = None,
    strict: bool = True,
    require_confirmed_retest: bool = True,
    require_htf_alignment: bool = True,
    require_sweep_provenance: bool = False,
) -> UnicornConfluenceAssessment:
    """
    Evaluates causal ICT Unicorn (Breaker Block + FVG + Displacement + Retest + HTF alignment)
    confluence at `decision_timestamp_ms` by reusing `detect_unicorn_evidence` and
    `detect_breaker_blocks` from `backend/services/unicorn_evidence_service.py`.

    Strict Binding Rule:
      - When `setup_descriptors` contains a non-None `selected_fvg`, `detect_unicorn_evidence`
        is called with that exact `selected_fvg` and NEVER silently substitutes another FVG.
      - Also records `scan_level_unicorn_status` across the closed M1 window for forensic observability.
    """
    norm_dir = str(direction or "LONG").strip().upper()
    symbol = str(getattr(context, "symbol", "XAUUSDT") if context is not None else "XAUUSDT")

    if context is None or norm_dir not in ("LONG", "SHORT"):
        empty_bind = bind_unicorn_to_causal_episode(None, setup_descriptors, None, decision_timestamp_ms)
        return UnicornConfluenceAssessment(
            symbol=symbol,
            direction=norm_dir,
            decision_timestamp_ms=decision_timestamp_ms,
            is_valid_unicorn=False,
            validity_status=UnicornSetupStatus.INVALID_DATA.value,
            failure_stage="UNICORN_MISSING_DATA",
            unicorn_id=None,
            setup_episode_id=None,
            breaker_id=None,
            breaker_type=None,
            breaker_low=None,
            breaker_high=None,
            breaker_distal_price=None,
            breaker_confirmed_at_ms=None,
            fvg_id=None,
            fvg_low=None,
            fvg_high=None,
            fvg_confirmed_at_ms=None,
            overlap_zone=None,
            overlap_width_price=None,
            invalidation_price=None,
            invalidation_source_id=None,
            retest_confirmed=False,
            retest_timestamp_ms=None,
            confirmed_at_ms=0,
            known_at_ms=0,
            sweep_provenance_verified=False,
            sweep_id=None,
            htf_aligned=False,
            htf_alignment_status="MISSING_OR_UNVERIFIED",
            episode_binding=empty_bind,
            scan_level_unicorn_status=None,
            scan_level_unicorn_id=None,
            rejection_reasons=("UNICORN_MISSING_DATA",),
        )

    closed_m1 = list(getattr(context, "closed_m1_candles", None) or ())
    mtf_ctx = getattr(context, "mtf_context", None)

    # Check for unclosed or future bars in closed_m1 if passed directly
    has_unclosed = any(not _candle_is_closed(b) for b in closed_m1)
    has_future_bar = any(_candle_open_ms(b) + 60_000 > decision_timestamp_ms for b in closed_m1 if _candle_is_closed(b))

    if len(closed_m1) < 10:
        empty_bind = bind_unicorn_to_causal_episode(None, setup_descriptors, mtf_ctx, decision_timestamp_ms)
        rej_code = "UNICORN_UNCLOSED_BAR_REJECTED" if has_unclosed else "UNICORN_MISSING_DATA"
        return UnicornConfluenceAssessment(
            symbol=symbol,
            direction=norm_dir,
            decision_timestamp_ms=decision_timestamp_ms,
            is_valid_unicorn=False,
            validity_status=UnicornSetupStatus.INVALID_DATA.value,
            failure_stage=rej_code,
            unicorn_id=None,
            setup_episode_id=None,
            breaker_id=None,
            breaker_type=None,
            breaker_low=None,
            breaker_high=None,
            breaker_distal_price=None,
            breaker_confirmed_at_ms=None,
            fvg_id=None,
            fvg_low=None,
            fvg_high=None,
            fvg_confirmed_at_ms=None,
            overlap_zone=None,
            overlap_width_price=None,
            invalidation_price=None,
            invalidation_source_id=None,
            retest_confirmed=False,
            retest_timestamp_ms=None,
            confirmed_at_ms=0,
            known_at_ms=0,
            sweep_provenance_verified=False,
            sweep_id=None,
            htf_aligned=False,
            htf_alignment_status=empty_bind.htf_alignment_status,
            episode_binding=empty_bind,
            scan_level_unicorn_status=None,
            scan_level_unicorn_id=None,
            rejection_reasons=(rej_code,),
        )

    setup_map = dict(setup_descriptors) if isinstance(setup_descriptors, Mapping) else {}
    sel_fvg = setup_map.get("selected_fvg")
    sel_break = setup_map.get("selected_break")
    sel_ref = setup_map.get("selected_reference")
    sel_sweep = setup_map.get("selected_sweep")

    # Note: When a non-FVG model (e.g. CONTINUATION, REVERSAL, SWEEP_RECLAIM) has `selected_fvg=None`
    # and auto-extracted `has_setup=True`, its `selected_break` / `selected_reference` represent the
    # sweep or BOS trigger rather than an FVG-bound Breaker. When `selected_fvg` is provided or
    # when explicit descriptors are passed in direct tests (`has_setup` not set), bind strictly.
    is_auto_extracted_non_fvg = bool(setup_map.get("has_setup") is True and sel_fvg is None)
    bound_break = None if is_auto_extracted_non_fvg else (
        sel_break if isinstance(sel_break, Mapping) else None
    )
    bound_ref = None if is_auto_extracted_non_fvg else (
        sel_ref if isinstance(sel_ref, Mapping) else None
    )

    raw_m1_attr = getattr(context, "closed_m1_candles", None)
    no_setup_fast_key: Optional[Tuple[Any, ...]] = None
    if (
        not isinstance(sel_fvg, Mapping)
        and bound_break is None
        and bound_ref is None
        and sel_sweep is None
        and not bool(setup_map.get("has_setup"))
        and raw_m1_attr is not None
        and not has_unclosed
        and not has_future_bar
    ):
        no_setup_fast_key = (
            id(raw_m1_attr),
            len(closed_m1),
            _candle_ohlc(closed_m1[-1]) if closed_m1 else (),
            id(mtf_ctx),
            getattr(mtf_ctx, "mtf_context_hash", None),
            norm_dir,
            int(decision_timestamp_ms),
            str(symbol),
            bool(strict),
            bool(require_confirmed_retest),
            bool(require_htf_alignment),
            bool(require_sweep_provenance),
        )
        fast_hit = _ASSESS_UNICORN_NO_SETUP_MEMO.get(no_setup_fast_key)
        if fast_hit is not None and fast_hit[0] is raw_m1_attr:
            return fast_hit[1]

    if not isinstance(sel_fvg, Mapping) and bound_break is None and bound_ref is None and raw_m1_attr is not None:
        unicorn_ev = _detect_scan_unicorn_memoized(
            raw_m1_attr,
            closed_m1,
            norm_dir,
            decision_timestamp_ms,
            symbol,
            bool(strict),
        )
    else:
        bound_memo_key = (
            (
                id(raw_m1_attr),
                len(closed_m1),
                norm_dir,
                int(decision_timestamp_ms),
                str(symbol),
                bool(strict),
                str(sel_fvg.get("id")) if isinstance(sel_fvg, Mapping) else None,
                float(sel_fvg.get("top", 0.0)) if isinstance(sel_fvg, Mapping) else None,
                float(sel_fvg.get("bottom", 0.0)) if isinstance(sel_fvg, Mapping) else None,
                int(sel_fvg.get("confirmed_at") or sel_fvg.get("timestamp") or 0) if isinstance(sel_fvg, Mapping) else None,
                str(bound_break.get("break_id")) if isinstance(bound_break, Mapping) else None,
                str(bound_ref.get("id")) if isinstance(bound_ref, Mapping) else None,
            )
            if raw_m1_attr is not None
            else None
        )
        bound_hit = _SCAN_UNICORN_MEMO.get(bound_memo_key) if bound_memo_key is not None else None
        if bound_hit is not None and bound_hit[0] is raw_m1_attr:
            unicorn_ev = bound_hit[1]
        else:
            unicorn_ev = detect_unicorn_evidence(
                closed_bars=closed_m1,
                direction=norm_dir,
                as_of_ms=decision_timestamp_ms,
                symbol=symbol,
                timeframe="1M",
                selected_fvg=sel_fvg if isinstance(sel_fvg, Mapping) else None,
                selected_break=bound_break,
                selected_reference=bound_ref,
                strict_causal_binding=bool(strict),
            )
            if bound_memo_key is not None:
                if len(_SCAN_UNICORN_MEMO) >= 256:
                    _SCAN_UNICORN_MEMO.clear()
                _SCAN_UNICORN_MEMO[bound_memo_key] = (raw_m1_attr, unicorn_ev)

    # Also compute scan-level Unicorn status when sel_fvg was provided, for forensic comparison
    if isinstance(sel_fvg, Mapping):
        if raw_m1_attr is not None:
            scan_ev = _detect_scan_unicorn_memoized(
                raw_m1_attr,
                closed_m1,
                norm_dir,
                decision_timestamp_ms,
                symbol,
                True,
            )
        else:
            scan_ev = detect_unicorn_evidence(
                closed_bars=closed_m1,
                direction=norm_dir,
                as_of_ms=decision_timestamp_ms,
                symbol=symbol,
                timeframe="1M",
                strict_causal_binding=True,
            )
        scan_status = scan_ev.validity_status
        scan_id = scan_ev.unicorn_id if scan_ev.is_valid else None
    else:
        scan_status = unicorn_ev.validity_status
        scan_id = unicorn_ev.unicorn_id if unicorn_ev.is_valid else None

    binding = bind_unicorn_to_causal_episode(
        unicorn_evidence=unicorn_ev,
        selected_setup=setup_map,
        mtf_context=mtf_ctx,
        decision_timestamp_ms=decision_timestamp_ms,
    )

    reasons: List[str] = []

    if has_unclosed and strict and not unicorn_ev.is_valid:
        reasons.append("UNICORN_UNCLOSED_BAR_REJECTED")
    if has_future_bar and strict and not unicorn_ev.is_valid:
        reasons.append("UNICORN_FUTURE_DATA_REJECTED")

    if not unicorn_ev.is_valid:
        mapped_r = _map_unicorn_status_to_v1793_reason(
            str(unicorn_ev.validity_status), unicorn_ev.reason_codes
        )
        if mapped_r not in reasons:
            reasons.append(mapped_r)

    for br in binding.reason_codes:
        if br not in reasons:
            reasons.append(br)

    # Retest check: retest_timestamp_ms is the open_ms of the retest bar; its close_ms (open_ms + 60_000)
    # must be strictly after setup confirmation (`confirmed_at_ms`) and <= `decision_timestamp_ms`.
    retest_ok = bool(
        unicorn_ev.retest_timestamp_ms is not None
        and unicorn_ev.confirmed_at_ms > 0
        and int(unicorn_ev.retest_timestamp_ms) + 60_000 > int(unicorn_ev.confirmed_at_ms)
        and int(unicorn_ev.retest_timestamp_ms) + 60_000 <= decision_timestamp_ms
    )
    if require_confirmed_retest and not retest_ok:
        if (
            "UNICORN_RETEST_BEFORE_CONFIRMATION" not in reasons
            and "UNICORN_WAITING_CONFIRMED_RETEST" not in reasons
            and unicorn_ev.is_valid
        ):
            reasons.append("UNICORN_WAITING_CONFIRMED_RETEST")

    # Sweep provenance check
    sweep_verified = bool(binding.sweep_id is not None or isinstance(sel_sweep, Mapping))
    if require_sweep_provenance and not sweep_verified:
        if "UNICORN_MISSING_SWEEP_PROVENANCE" not in reasons:
            reasons.append("UNICORN_MISSING_SWEEP_PROVENANCE")

    # HTF alignment check
    htf_aligned = binding.htf_alignment_status == "ALIGNED"
    if require_htf_alignment and not htf_aligned:
        if binding.htf_alignment_status == "CONFLICT":
            if "UNICORN_HTF_CONFLICT" not in reasons:
                reasons.append("UNICORN_HTF_CONFLICT")
        else:
            if "UNICORN_HTF_NOT_VERIFIED" not in reasons:
                reasons.append("UNICORN_HTF_NOT_VERIFIED")

    # Overlap zone calculation
    overlap_zone: Optional[Tuple[float, float]] = None
    overlap_width: Optional[float] = None
    if (
        unicorn_ev.overlap_low is not None
        and unicorn_ev.overlap_high is not None
        and float(unicorn_ev.overlap_low) < float(unicorn_ev.overlap_high)
    ):
        overlap_zone = (round(float(unicorn_ev.overlap_low), 2), round(float(unicorn_ev.overlap_high), 2))
        overlap_width = round(float(unicorn_ev.overlap_high) - float(unicorn_ev.overlap_low), 4)
    elif unicorn_ev.is_valid and "UNICORN_NO_GEOMETRIC_OVERLAP" not in reasons:
        reasons.append("UNICORN_NO_GEOMETRIC_OVERLAP")

    is_valid_overall = bool(
        unicorn_ev.is_valid
        and binding.is_bound
        and overlap_zone is not None
        and (retest_ok if require_confirmed_retest else True)
        and (htf_aligned if require_htf_alignment else True)
        and (sweep_verified if require_sweep_provenance else True)
        and len(reasons) == 0
    )

    failure_stage = None if is_valid_overall else (reasons[0] if reasons else str(unicorn_ev.validity_status))

    assessment_res = UnicornConfluenceAssessment(
        symbol=symbol,
        direction=norm_dir,
        decision_timestamp_ms=decision_timestamp_ms,
        is_valid_unicorn=is_valid_overall,
        validity_status=UnicornSetupStatus.VALID.value if is_valid_overall else str(unicorn_ev.validity_status),
        failure_stage=failure_stage,
        unicorn_id=unicorn_ev.unicorn_id if unicorn_ev.is_valid else None,
        setup_episode_id=binding.setup_episode_id or unicorn_ev.setup_episode_id,
        breaker_id=unicorn_ev.breaker_id,
        breaker_type=unicorn_ev.breaker_type,
        breaker_low=unicorn_ev.breaker_low,
        breaker_high=unicorn_ev.breaker_high,
        breaker_distal_price=unicorn_ev.breaker_distal_price,
        breaker_confirmed_at_ms=unicorn_ev.breaker_confirmed_at_ms,
        fvg_id=unicorn_ev.fvg_id,
        fvg_low=unicorn_ev.fvg_low,
        fvg_high=unicorn_ev.fvg_high,
        fvg_confirmed_at_ms=unicorn_ev.fvg_confirmed_at_ms,
        overlap_zone=overlap_zone,
        overlap_width_price=overlap_width,
        invalidation_price=unicorn_ev.invalidation_price,
        invalidation_source_id=unicorn_ev.invalidation_source_id,
        retest_confirmed=retest_ok,
        retest_timestamp_ms=unicorn_ev.retest_timestamp_ms,
        confirmed_at_ms=int(unicorn_ev.confirmed_at_ms or 0),
        known_at_ms=int(unicorn_ev.known_at_ms or 0),
        sweep_provenance_verified=sweep_verified,
        sweep_id=binding.sweep_id,
        htf_aligned=htf_aligned,
        htf_alignment_status=binding.htf_alignment_status,
        episode_binding=binding,
        scan_level_unicorn_status=scan_status,
        scan_level_unicorn_id=scan_id,
        rejection_reasons=tuple(reasons),
    )
    if no_setup_fast_key is not None and raw_m1_attr is not None:
        if len(_ASSESS_UNICORN_NO_SETUP_MEMO) >= 256:
            _ASSESS_UNICORN_NO_SETUP_MEMO.clear()
        _ASSESS_UNICORN_NO_SETUP_MEMO[no_setup_fast_key] = (raw_m1_attr, assessment_res)
    return assessment_res


# ==============================================================================
# FUNCTION #6: validate_unicorn_invalidation_against_v1792
# ==============================================================================
def validate_unicorn_invalidation_against_v1792(
    unicorn_assessment: UnicornConfluenceAssessment,
    v1792_plan: Any,
    direction: str,
    entry_price: float,
    context: Any = None,
    *,
    tick_size: float = 0.01,
) -> UnicornInvalidationVerdict:
    """
    Verifies that the proposed stop-loss from V17.9.2.1 (`v1792_plan`) is strictly beyond:
      1. `entry_price` (`proposed_sl < entry_price` for LONG, `proposed_sl > entry_price` for SHORT)
      2. V17.9.2.1 structural `invalidation_price` (`proposed_sl < invalidation_price` for LONG,
         `proposed_sl > invalidation_price` for SHORT)
      3. Unicorn `invalidation_price` / `breaker_distal_price` and `overlap_zone` boundary when
         Unicorn confluence is valid (`proposed_sl < min(unicorn_invalidation, overlap_low)` for LONG,
         `proposed_sl > max(unicorn_invalidation, overlap_high)` for SHORT)
      4. Outward tick alignment (`ROUND_FLOOR` for LONG, `ROUND_CEILING` for SHORT).
    """
    norm_dir = str(direction or "LONG").strip().upper()
    reasons: List[str] = []

    proposed_sl: Optional[float] = None
    v1792_inv: Optional[float] = None

    if v1792_plan is not None:
        if isinstance(v1792_plan, Mapping):
            if v1792_plan.get("proposed_sl") is not None:
                proposed_sl = float(v1792_plan["proposed_sl"])
            if v1792_plan.get("invalidation_price") is not None:
                v1792_inv = float(v1792_plan["invalidation_price"])
        else:
            if getattr(v1792_plan, "proposed_sl", None) is not None:
                proposed_sl = float(v1792_plan.proposed_sl)
            if getattr(v1792_plan, "invalidation_price", None) is not None:
                v1792_inv = float(v1792_plan.invalidation_price)

    uni_inv: Optional[float] = None
    if unicorn_assessment is not None:
        cands: List[float] = []
        if unicorn_assessment.invalidation_price is not None:
            cands.append(float(unicorn_assessment.invalidation_price))
        if unicorn_assessment.breaker_distal_price is not None:
            cands.append(float(unicorn_assessment.breaker_distal_price))
        if unicorn_assessment.overlap_zone is not None:
            cands.append(
                float(unicorn_assessment.overlap_zone[0])
                if norm_dir == "LONG"
                else float(unicorn_assessment.overlap_zone[1])
            )
        if cands:
            uni_inv = min(cands) if norm_dir == "LONG" else max(cands)

    eff_bounds: List[float] = []
    if v1792_inv is not None:
        eff_bounds.append(v1792_inv)
    if uni_inv is not None:
        eff_bounds.append(uni_inv)
    eff_boundary = (min(eff_bounds) if norm_dir == "LONG" else max(eff_bounds)) if eff_bounds else None

    if proposed_sl is None or not math.isfinite(proposed_sl) or proposed_sl <= 0.0:
        reasons.append("UNICORN_STOP_INSIDE_INVALIDATION")
        return UnicornInvalidationVerdict(
            is_valid=False,
            direction=norm_dir,
            entry_price=float(entry_price),
            proposed_sl=proposed_sl,
            v1792_invalidation_price=v1792_inv,
            unicorn_invalidation_price=uni_inv,
            effective_invalidation_boundary=eff_boundary,
            respects_entry_side=False,
            respects_unicorn_distal=False,
            respects_v1792_invalidation=False,
            outward_tick_aligned=False,
            reason_codes=tuple(reasons),
        )

    # 1. Check entry side
    respects_entry = (
        proposed_sl < float(entry_price)
        if norm_dir == "LONG"
        else proposed_sl > float(entry_price)
    )
    if not respects_entry:
        reasons.append("UNICORN_STOP_WRONG_SIDE_OF_ENTRY")

    # 2. Check V17.9.2 invalidation boundary
    respects_v1792 = True
    if v1792_inv is not None:
        respects_v1792 = (
            proposed_sl < v1792_inv
            if norm_dir == "LONG"
            else proposed_sl > v1792_inv
        )
        if not respects_v1792:
            reasons.append("UNICORN_STOP_INSIDE_INVALIDATION")

    # 3. Check Unicorn distal / overlap boundary
    respects_uni = True
    if uni_inv is not None:
        respects_uni = (
            proposed_sl < uni_inv
            if norm_dir == "LONG"
            else proposed_sl > uni_inv
        )
        if not respects_uni and "UNICORN_STOP_INSIDE_INVALIDATION" not in reasons:
            reasons.append("UNICORN_STOP_INSIDE_INVALIDATION")

    # 4. Check tick alignment
    d_tick = Decimal(str(tick_size))
    d_sl = Decimal(str(round(proposed_sl, 6)))
    q_sl = (
        float((d_sl / d_tick).to_integral_value(rounding=ROUND_FLOOR) * d_tick)
        if norm_dir == "LONG"
        else float((d_sl / d_tick).to_integral_value(rounding=ROUND_CEILING) * d_tick)
    )
    tick_aligned = abs(q_sl - proposed_sl) <= 1e-6

    is_valid = bool(
        respects_entry
        and respects_v1792
        and respects_uni
        and tick_aligned
        and len(reasons) == 0
    )

    return UnicornInvalidationVerdict(
        is_valid=is_valid,
        direction=norm_dir,
        entry_price=float(entry_price),
        proposed_sl=round(proposed_sl, 2),
        v1792_invalidation_price=round(v1792_inv, 2) if v1792_inv is not None else None,
        unicorn_invalidation_price=round(uni_inv, 2) if uni_inv is not None else None,
        effective_invalidation_boundary=round(eff_boundary, 2) if eff_boundary is not None else None,
        respects_entry_side=respects_entry,
        respects_unicorn_distal=respects_uni,
        respects_v1792_invalidation=respects_v1792,
        outward_tick_aligned=tick_aligned,
        reason_codes=tuple(reasons),
    )


# ==============================================================================
# FUNCTION #7: plan_v17_9_3_macro_unicorn_setup
# ==============================================================================
def plan_v17_9_3_macro_unicorn_setup(
    context: Any,
    model_id: str,
    direction: str,
    *,
    setup_descriptors: Optional[Mapping[str, Any]] = None,
    v1792_plan: Any = None,
    mode: str = "OFF",
    schedule_config: Optional[Mapping[str, Any]] = None,
    entry_price: Optional[float] = None,
    baseline_sl: Optional[float] = None,
    baseline_invalidation_price: Optional[float] = None,
    require_confirmed_retest: bool = True,
    require_htf_alignment: bool = True,
) -> MacroUnicornPlanV1793:
    """
    Master V17.9.3 planner orchestrating:
      1. Mode normalization & validation (fail-closed on unknown mode)
      2. Causal ICT Macro Time Window resolution (`resolve_causal_macro_time_window`)
      3. Contextual macro quality classification (`classify_macro_context_quality`)
      4. Causal Unicorn Breaker+FVG+Retest+HTF evaluation (`evaluate_causal_unicorn_confluence`)
      5. Unicorn vs V17.9.2.1 invalidation verification (`validate_unicorn_invalidation_against_v1792`)
      6. Mode-specific gating (`OFF`, `OBSERVE_ONLY`, `MACRO_FILTER_ONLY`, `UNICORN_ONLY`,
         `MACRO_PLUS_UNICORN_V1792_SL_TP`, `MACRO_PLUS_UNICORN_MTF_FULL`, `OUTSIDE_MACRO_COUNTERFACTUAL`).

    Monotonicity Contract:
      - V17.9.3 is a strictly narrowing entry-quality filter + diagnostic intelligence layer.
      - It NEVER turns a setup already rejected by baseline/V17.9.2.1 into `ELIGIBLE`.
    """
    raw_mode_str = str(mode if mode is not None else "OFF")
    norm_mode, mode_valid, migration_note = normalize_v1793_mode(mode)
    norm_dir = str(direction or "LONG").strip().upper()
    symbol = str(getattr(context, "symbol", "XAUUSDT") if context is not None else "XAUUSDT")
    ts_ms = int(getattr(context, "decision_timestamp_ms", -1) if context is not None else -1)

    if entry_price is None and context is not None:
        bid = float(getattr(context, "bid", 0.0) or 0.0)
        ask = float(getattr(context, "ask", 0.0) or 0.0)
        entry_price = round(ask if norm_dir == "LONG" else bid, 2)
    eff_entry = float(entry_price or 0.0)

    macro_state = resolve_causal_macro_time_window(
        ts_ms,
        symbol=symbol,
        schedule_config=schedule_config,
    )
    macro_quality = classify_macro_context_quality(macro_state, context)

    unicorn_assessment = evaluate_causal_unicorn_confluence(
        context=context,
        direction=norm_dir,
        decision_timestamp_ms=ts_ms,
        setup_descriptors=setup_descriptors,
        strict=True,
        require_confirmed_retest=require_confirmed_retest,
        require_htf_alignment=require_htf_alignment,
    )

    eff_v1792_plan = v1792_plan
    if eff_v1792_plan is None and baseline_sl is not None:
        eff_v1792_plan = {
            "proposed_sl": baseline_sl,
            "invalidation_price": baseline_invalidation_price,
        }

    invalidation_verdict = validate_unicorn_invalidation_against_v1792(
        unicorn_assessment=unicorn_assessment,
        v1792_plan=eff_v1792_plan,
        direction=norm_dir,
        entry_price=eff_entry,
        context=context,
    )

    macro_gate_passed = bool(macro_quality.is_eligible)
    unicorn_gate_passed = bool(unicorn_assessment.is_valid_unicorn)
    invalidation_gate_passed = bool(invalidation_verdict.is_valid)

    # Full MTF gate check
    mtf_ctx = getattr(context, "mtf_context", None) if context is not None else None
    mtf_q = str(getattr(context, "mtf_quality_state", "VALID") if context is not None else "DISABLED_OFF")
    mtf_full_ok = False
    mtf_full_rej: Optional[str] = None
    if mtf_ctx is not None:
        mtf_full_ok, mtf_full_rej, _ = evaluate_mtf_eligibility_for_direction(
            direction=norm_dir,
            mtf_context=mtf_ctx,
            ablation_mode="FULL",
            mtf_quality_state=mtf_q,
        )
    else:
        mtf_full_rej = "MTF_CONTEXT_BUILD_FAILED"

    rejection_reasons: List[str] = []
    rejection_stage: Optional[str] = None
    v1793_gates_passed = True

    if not mode_valid:
        v1793_gates_passed = False
        rejection_stage = "V1793_MODE_VALIDATION"
        rejection_reasons.append("INVALID_V1793_MODE")
    elif norm_mode in ("OFF", "OBSERVE_ONLY"):
        # OFF and OBSERVE_ONLY never block trade actions
        v1793_gates_passed = True
        rejection_stage = None
    elif norm_mode == "MACRO_FILTER_ONLY":
        if not macro_gate_passed:
            v1793_gates_passed = False
            rejection_stage = "V1793_MACRO_TIME_GATE"
            for r in macro_quality.reason_codes:
                if r not in rejection_reasons:
                    rejection_reasons.append(r)
            if not rejection_reasons:
                rejection_reasons.append("MACRO_OUTSIDE_WINDOW")
    elif norm_mode == "UNICORN_ONLY":
        if not unicorn_gate_passed:
            v1793_gates_passed = False
            rejection_stage = "V1793_UNICORN_CONFLUENCE_GATE"
            for r in unicorn_assessment.rejection_reasons:
                if r not in rejection_reasons:
                    rejection_reasons.append(r)
            if not rejection_reasons:
                rejection_reasons.append("UNICORN_MISSING_DATA")
        if not invalidation_gate_passed:
            v1793_gates_passed = False
            if rejection_stage is None:
                rejection_stage = "V1793_UNICORN_INVALIDATION_GATE"
            for r in invalidation_verdict.reason_codes:
                if r not in rejection_reasons:
                    rejection_reasons.append(r)
    elif norm_mode == "MACRO_PLUS_UNICORN_V1792_SL_TP":
        if not macro_gate_passed:
            v1793_gates_passed = False
            rejection_stage = "V1793_MACRO_TIME_GATE"
            for r in macro_quality.reason_codes:
                if r not in rejection_reasons:
                    rejection_reasons.append(r)
        if not unicorn_gate_passed:
            v1793_gates_passed = False
            if rejection_stage is None:
                rejection_stage = "V1793_UNICORN_CONFLUENCE_GATE"
            for r in unicorn_assessment.rejection_reasons:
                if r not in rejection_reasons:
                    rejection_reasons.append(r)
        if not invalidation_gate_passed:
            v1793_gates_passed = False
            if rejection_stage is None:
                rejection_stage = "V1793_UNICORN_INVALIDATION_GATE"
            for r in invalidation_verdict.reason_codes:
                if r not in rejection_reasons:
                    rejection_reasons.append(r)
    elif norm_mode == "MACRO_PLUS_UNICORN_MTF_FULL":
        if not macro_gate_passed:
            v1793_gates_passed = False
            rejection_stage = "V1793_MACRO_TIME_GATE"
            for r in macro_quality.reason_codes:
                if r not in rejection_reasons:
                    rejection_reasons.append(r)
        if not unicorn_gate_passed:
            v1793_gates_passed = False
            if rejection_stage is None:
                rejection_stage = "V1793_UNICORN_CONFLUENCE_GATE"
            for r in unicorn_assessment.rejection_reasons:
                if r not in rejection_reasons:
                    rejection_reasons.append(r)
        if not invalidation_gate_passed:
            v1793_gates_passed = False
            if rejection_stage is None:
                rejection_stage = "V1793_UNICORN_INVALIDATION_GATE"
            for r in invalidation_verdict.reason_codes:
                if r not in rejection_reasons:
                    rejection_reasons.append(r)
        if not mtf_full_ok:
            v1793_gates_passed = False
            if rejection_stage is None:
                rejection_stage = "V1793_MTF_FULL_GATE"
            rej_c = mtf_full_rej or "UNICORN_HTF_CONFLICT"
            if rej_c not in rejection_reasons:
                rejection_reasons.append(rej_c)
    elif norm_mode == "OUTSIDE_MACRO_COUNTERFACTUAL":
        # Diagnostic counterfactual mode E1: permits setups ONLY when outside active macro windows
        # (and not session restricted / weekend closed) to compare inside-macro vs outside-macro cohorts.
        is_clean_outside = (
            not macro_state.eligible_for_macro_gate
            and macro_state.primary_window_id == "OUTSIDE_MACRO_WINDOW"
            and not macro_state.is_weekend_or_closed
            and not macro_state.is_holiday_closed
            and not macro_quality.session_restricted_override
            and macro_quality.causal_valid
            and macro_quality.quote_fresh
            and macro_quality.m1_bars_sufficient
        )
        if not is_clean_outside:
            v1793_gates_passed = False
            rejection_stage = "V1793_OUTSIDE_MACRO_COUNTERFACTUAL_GATE"
            if macro_state.eligible_for_macro_gate:
                rejection_reasons.append("COUNTERFACTUAL_REJECTED_INSIDE_MACRO_WINDOW")
            else:
                for r in macro_quality.reason_codes:
                    if r not in rejection_reasons:
                        rejection_reasons.append(r)
                if not rejection_reasons:
                    rejection_reasons.append("MACRO_OUTSIDE_COUNTERFACTUAL_INELIGIBLE")

    obs_payload = {
        "schema_version": V17_9_3_ALGORITHM_VERSION,
        "mode": norm_mode,
        "model_id": model_id,
        "symbol": symbol,
        "direction": norm_dir,
        "decision_timestamp_ms": ts_ms,
        "primary_window_id": macro_state.primary_window_id,
        "eligible_for_macro_gate": macro_state.eligible_for_macro_gate,
        "is_valid_unicorn": unicorn_assessment.is_valid_unicorn,
        "unicorn_status": unicorn_assessment.validity_status,
        "unicorn_id": unicorn_assessment.unicorn_id,
        "invalidation_valid": invalidation_verdict.is_valid,
        "v1793_gates_passed": v1793_gates_passed,
        "rejection_reasons": rejection_reasons,
    }
    obs_fp = deterministic_hash(obs_payload, 24)

    return MacroUnicornPlanV1793(
        schema_version=V17_9_3_ALGORITHM_VERSION,
        mode=norm_mode,
        raw_mode_input=raw_mode_str,
        mode_valid=mode_valid,
        mode_migration_note=migration_note,
        model_id=model_id,
        symbol=symbol,
        direction=norm_dir,
        decision_timestamp_ms=ts_ms,
        macro_state=macro_state,
        macro_quality=macro_quality,
        unicorn_assessment=unicorn_assessment,
        invalidation_verdict=invalidation_verdict,
        macro_gate_passed=macro_gate_passed,
        unicorn_gate_passed=unicorn_gate_passed,
        invalidation_gate_passed=invalidation_gate_passed,
        mtf_full_gate_passed=mtf_full_ok,
        v1793_gates_passed=v1793_gates_passed,
        rejection_stage=rejection_stage,
        rejection_reasons=tuple(rejection_reasons),
        observe_metadata_fingerprint=obs_fp,
    )


# ==============================================================================
# FUNCTION #8: serialize_v1793_decision_evidence
# ==============================================================================
def serialize_v1793_decision_evidence(
    plan: Optional[MacroUnicornPlanV1793],
    snapshot: Any = None,
    context: Any = None,
) -> Dict[str, Any]:
    """
    Serializes V17.9.3 decision evidence into a deterministic, JSON-serializable
    dictionary suitable for `StrategyDecisionSnapshot`, 3-runtime parity checks,
    and historical replay traces.
    """
    if plan is None:
        return {
            "schema_version": V17_9_3_ALGORITHM_VERSION,
            "v1793_mode": "OFF",
            "macro_time_state": None,
            "unicorn_confluence": None,
            "invalidation_verdict": None,
            "v1793_gates_passed": True,
            "v1793_rejection_stage": None,
            "v1793_rejection_reasons": [],
            "observe_metadata_fingerprint": None,
        }

    return {
        "schema_version": plan.schema_version,
        "v1793_mode": plan.mode,
        "raw_mode_input": plan.raw_mode_input,
        "mode_valid": plan.mode_valid,
        "mode_migration_note": plan.mode_migration_note,
        "macro_time_state": plan.macro_state.to_dict(),
        "macro_quality": plan.macro_quality.to_dict(),
        "unicorn_confluence": plan.unicorn_assessment.to_dict(),
        "invalidation_verdict": plan.invalidation_verdict.to_dict(),
        "macro_gate_passed": plan.macro_gate_passed,
        "unicorn_gate_passed": plan.unicorn_gate_passed,
        "invalidation_gate_passed": plan.invalidation_gate_passed,
        "mtf_full_gate_passed": plan.mtf_full_gate_passed,
        "v1793_gates_passed": plan.v1793_gates_passed,
        "v1793_rejection_stage": plan.rejection_stage,
        "v1793_rejection_reasons": list(plan.rejection_reasons),
        "observe_metadata_fingerprint": plan.observe_metadata_fingerprint,
        "snapshot_decision_status": getattr(snapshot, "decision_status", None) if snapshot is not None else None,
        "context_fingerprint": getattr(context, "context_fingerprint", None) if context is not None else None,
    }


def diagnose_v17931_d1_vs_d2_mtf_gate(
    context: Any,
    model_id: str,
    direction: str,
    *,
    snap_d1: Any = None,
    snap_d2: Any = None,
) -> Dict[str, Any]:
    """
    V17.9.3.1 Section 05: Decomposes `D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP` vs
    `D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL` across all MTF sub-gates (`H4_ONLY`, `H1_ONLY`,
    `M15_ONLY`, `M15_H1_ONLY`, `STRUCTURE_ONLY`, `LIQUIDITY_ONLY`, `FULL`) and classifies
    the exact root cause (`ELIGIBLE_IN_D2`, `ACTUAL_MARKET_CONFLICT`, `STALE_HTF_BAR`,
    `HISTORICAL_GAP`, `MISSING_HTF_SOURCE`, `CROSS_TF_STRUCTURE_STATE_BUG`,
    `STOP_INVALIDATION_RR_SIZING_REJECT`).
    """
    norm_dir = str(direction or "LONG").strip().upper()
    ts_ms = int(getattr(context, "decision_timestamp_ms", 0) if context is not None else 0)
    mtf_ctx = getattr(context, "mtf_context", None) if context is not None else None
    mtf_q = str(getattr(context, "mtf_quality_state", "DISABLED_OFF") if context is not None else "DISABLED_OFF")

    sub_gates: Dict[str, Dict[str, Any]] = {}
    for ab_mode in ("H4_ONLY", "H1_ONLY", "M15_ONLY", "M15_H1_ONLY", "STRUCTURE_ONLY", "LIQUIDITY_ONLY", "FULL"):
        if mtf_ctx is not None:
            ok, rej, detail = evaluate_mtf_eligibility_for_direction(
                direction=norm_dir,
                mtf_context=mtf_ctx,
                ablation_mode=ab_mode,
                mtf_quality_state=mtf_q,
            )
        else:
            ok, rej, detail = False, "MTF_MISSING_DATA", "mtf_context is None"
        sub_gates[ab_mode] = {
            "passed": bool(ok),
            "is_eligible": bool(ok),
            "rejection_code": rej,
            "detail": detail,
        }

    h4_trend = "UNKNOWN"
    h1_trend = "UNKNOWN"
    m15_trend = "UNKNOWN"
    h4_q = "UNKNOWN"
    h1_q = "UNKNOWN"
    m15_q = "UNKNOWN"
    alignment = "UNKNOWN"
    has_fresh_sweep = False
    last_sweep_dir = None
    last_closed_ms_by_tf: Dict[str, Optional[int]] = {"M15": None, "H1": None, "H4": None}

    if mtf_ctx is not None:
        s = getattr(mtf_ctx, "mtf_structure", None)
        if s is not None:
            alignment = str(getattr(s, "alignment", "UNKNOWN"))
            if getattr(s, "h4", None) is not None:
                h4_trend = str(s.h4.trend.value if hasattr(s.h4.trend, "value") else s.h4.trend)
                h4_q = str(s.h4.quality_state.value if hasattr(s.h4.quality_state, "value") else s.h4.quality_state)
            if getattr(s, "h1", None) is not None:
                h1_trend = str(s.h1.trend.value if hasattr(s.h1.trend, "value") else s.h1.trend)
                h1_q = str(s.h1.quality_state.value if hasattr(s.h1.quality_state, "value") else s.h1.quality_state)
            if getattr(s, "m15", None) is not None:
                m15_trend = str(s.m15.trend.value if hasattr(s.m15.trend, "value") else s.m15.trend)
                m15_q = str(s.m15.quality_state.value if hasattr(s.m15.quality_state, "value") else s.m15.quality_state)
        liq = getattr(mtf_ctx, "evaluator_liquidity", None)
        if liq is not None:
            has_fresh_sweep = bool(getattr(liq, "has_fresh_sweep", False))
            sw = getattr(liq, "last_sweep", None)
            if sw is not None:
                last_sweep_dir = str(getattr(sw, "implied_reversal_direction", ""))
        snap_obj = getattr(mtf_ctx, "snapshot", None)
        if snap_obj is not None and hasattr(snap_obj, "get_window"):
            for tf_k in ("M15", "H1", "H4"):
                w = snap_obj.get_window(tf_k)
                if w is not None and getattr(w, "last_closed_at", None) is not None:
                    last_closed_ms_by_tf[tf_k] = int(w.last_closed_at.timestamp() * 1000)

    d1_status = str(getattr(snap_d1, "decision_status", "UNKNOWN")) if snap_d1 is not None else "UNKNOWN"
    d2_status = str(getattr(snap_d2, "decision_status", "UNKNOWN")) if snap_d2 is not None else "UNKNOWN"
    d2_reasons = list(getattr(snap_d2, "rejection_reason_codes", ()) or ()) if snap_d2 is not None else []

    opposing_trend = "BEARISH" if norm_dir == "LONG" else "BULLISH"
    full_gate = sub_gates["FULL"]
    if d2_status == "ELIGIBLE" or bool(full_gate["passed"]):
        primary_root_cause = "ELIGIBLE_IN_D2"
        root_cause_classification = "D2_ELIGIBLE_AFTER_MTF_BINDING_FIX"
        legacy_v1793_defect = "CROSS_TF_STRUCTURE_STATE_BUG"
    elif mtf_ctx is None or mtf_q in ("DISABLED_OFF", "MTF_CONTEXT_BUILD_FAILED"):
        primary_root_cause = "MISSING_HTF_SOURCE"
        root_cause_classification = "MISSING_HTF_SOURCE"
        legacy_v1793_defect = "NONE"
    elif mtf_q == "STALE_HTF" or any(q == "STALE_HTF" for q in (h4_q, h1_q, m15_q)):
        primary_root_cause = "STALE_HTF_BAR"
        if h1_trend == opposing_trend:
            root_cause_classification = "TRUE_H1_STRUCTURE_CONFLICT"
        elif h4_trend == opposing_trend:
            root_cause_classification = "TRUE_H4_STRUCTURE_CONFLICT"
        elif m15_trend == opposing_trend:
            root_cause_classification = "TRUE_M15_STRUCTURE_CONFLICT"
        else:
            root_cause_classification = "STALE_HTF_BAR"
        legacy_v1793_defect = "CROSS_TF_STRUCTURE_STATE_BUG"
    elif mtf_q in ("MISSING_BARS", "INSUFFICIENT_HISTORY") or any(
        q in ("MISSING_BARS", "INSUFFICIENT_HISTORY") for q in (h4_q, h1_q, m15_q)
    ):
        primary_root_cause = "HISTORICAL_GAP"
        root_cause_classification = "HISTORICAL_GAP"
        legacy_v1793_defect = "CROSS_TF_STRUCTURE_STATE_BUG"
    elif full_gate["rejection_code"] in (
        "MTF_CONFLICT",
        "MTF_STRUCTURE_FILTER_REJECT",
        "MTF_LIQUIDITY_FILTER_REJECT",
    ):
        primary_root_cause = "ACTUAL_MARKET_CONFLICT"
        if h1_trend == opposing_trend:
            root_cause_classification = "TRUE_H1_STRUCTURE_CONFLICT"
        elif h4_trend == opposing_trend:
            root_cause_classification = "TRUE_H4_STRUCTURE_CONFLICT"
        elif m15_trend == opposing_trend:
            root_cause_classification = "TRUE_M15_STRUCTURE_CONFLICT"
        else:
            root_cause_classification = "ACTUAL_MARKET_CONFLICT"
        legacy_v1793_defect = "CROSS_TF_STRUCTURE_STATE_BUG"
    else:
        primary_root_cause = "STOP_INVALIDATION_RR_SIZING_REJECT"
        root_cause_classification = "STOP_INVALIDATION_RR_SIZING_REJECT"
        legacy_v1793_defect = "NONE"

    # Secondary market conflict check even when STALE_HTF triggered first
    has_concurrent_market_conflict = bool(
        (h4_trend == opposing_trend and h1_trend == opposing_trend)
        or (h1_trend == opposing_trend and m15_trend == opposing_trend)
        or (has_fresh_sweep and last_sweep_dir == opposing_trend)
        or not sub_gates["STRUCTURE_ONLY"]["passed"]
        or not sub_gates["LIQUIDITY_ONLY"]["passed"]
    )

    return {
        "model_id": str(model_id),
        "direction": norm_dir,
        "decision_timestamp_ms": ts_ms,
        "d1_decision_status": d1_status,
        "d2_decision_status": d2_status,
        "d1_eligible": d1_status == "ELIGIBLE",
        "d2_eligible": d2_status == "ELIGIBLE",
        "d2_rejection_reasons": d2_reasons,
        "mtf_quality_state": mtf_q,
        "mtf_alignment": alignment,
        "timeframe_trends": {"H4": h4_trend, "H1": h1_trend, "M15": m15_trend},
        "timeframe_quality_states": {"H4": h4_q, "H1": h1_q, "M15": m15_q},
        "last_closed_bar_ms_by_tf": last_closed_ms_by_tf,
        "has_fresh_sweep": has_fresh_sweep,
        "last_sweep_implied_reversal_direction": last_sweep_dir,
        "sub_gate_verdicts": sub_gates,
        "ablation_results": sub_gates,
        "primary_root_cause": primary_root_cause,
        "root_cause_classification": root_cause_classification,
        "has_concurrent_market_conflict": has_concurrent_market_conflict,
        "legacy_v1793_bind_unicorn_defect": legacy_v1793_defect,
    }

