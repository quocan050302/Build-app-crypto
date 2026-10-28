"""
backend/services/unicorn_evidence_types.py
AURUM V17.8 — ICT Unicorn Evidence Engine Types & Evidence Contract.
Zero database mutations, zero network I/O, zero wall-clock dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple


class UnicornSetupStatus(str, Enum):
    VALID = "VALID"
    WAITING_RETEST = "WAITING_RETEST"
    WAITING_EVIDENCE = "WAITING_EVIDENCE"
    INVALID_DATA = "INVALID_DATA"
    UNICORN_INVALIDATED = "UNICORN_INVALIDATED"
    MISSING_BREAKER = "MISSING_BREAKER"
    MISSING_FVG = "MISSING_FVG"
    NO_GEOMETRIC_OVERLAP = "NO_GEOMETRIC_OVERLAP"
    EPISODE_MISMATCH = "EPISODE_MISMATCH"
    DISPLACEMENT_WEAK = "DISPLACEMENT_WEAK"
    FUTURE_DATA_REJECTED = "FUTURE_DATA_REJECTED"
    CONSUMED_OR_INVALIDATED_FVG = "CONSUMED_OR_INVALIDATED_FVG"
    UNCLOSED_EVIDENCE_REJECTED = "UNCLOSED_EVIDENCE_REJECTED"
    RETEST_BEFORE_CONFIRMATION = "RETEST_BEFORE_CONFIRMATION"
    MISSING_CAUSAL_PROVENANCE = "MISSING_CAUSAL_PROVENANCE"
    WRONG_DIRECTION_OVERLAP = "WRONG_DIRECTION_OVERLAP"


class BreakerType(str, Enum):
    BULLISH_BREAKER = "BULLISH_BREAKER"
    BEARISH_BREAKER = "BEARISH_BREAKER"


@dataclass(frozen=True)
class BreakerBlock:
    """
    Verified ICT Breaker Block structure.
    Symmetrical for Bullish and Bearish.
    """
    id: str
    symbol: str
    breaker_type: BreakerType
    low: float
    high: float
    distal_price: float
    proximal_price: float
    source_ob_id: str
    source_ob_low: float
    source_ob_high: float
    source_ob_open_ms: int
    source_ob_close_ms: int
    break_bar_open_ms: int
    break_bar_close_ms: int
    confirmed_at_ms: int
    broken_swing_id: Optional[str] = None
    broken_swing_price: Optional[float] = None
    mss_or_bos_id: Optional[str] = None
    displacement_id: Optional[str] = None
    episode_id: Optional[str] = None
    displacement_ratio: float = 1.0
    known_at_ms: int = 0

    def __post_init__(self) -> None:
        if self.known_at_ms <= 0 and self.confirmed_at_ms > 0:
            object.__setattr__(self, "known_at_ms", self.confirmed_at_ms)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "breaker_type": self.breaker_type.value if hasattr(self.breaker_type, "value") else str(self.breaker_type),
            "low": self.low,
            "high": self.high,
            "distal_price": self.distal_price,
            "proximal_price": self.proximal_price,
            "source_ob_id": self.source_ob_id,
            "source_ob_low": self.source_ob_low,
            "source_ob_high": self.source_ob_high,
            "source_ob_open_ms": self.source_ob_open_ms,
            "source_ob_close_ms": self.source_ob_close_ms,
            "break_bar_open_ms": self.break_bar_open_ms,
            "break_bar_close_ms": self.break_bar_close_ms,
            "confirmed_at_ms": self.confirmed_at_ms,
            "known_at_ms": self.known_at_ms,
            "broken_swing_id": self.broken_swing_id,
            "broken_swing_price": self.broken_swing_price,
            "mss_or_bos_id": self.mss_or_bos_id,
            "displacement_id": self.displacement_id,
            "episode_id": self.episode_id,
            "displacement_ratio": self.displacement_ratio,
        }


@dataclass(frozen=True)
class UnicornEvidence:
    """
    Master ICT Unicorn Evidence Contract (V17.8.0.1 Section 3 & 6).
    Contains full causal provenance for Breaker + FVG confluence.
    """
    unicorn_id: str
    symbol: str
    direction: str  # "LONG" or "SHORT"
    setup_episode_id: str

    breaker_id: Optional[str] = None
    breaker_type: Optional[str] = None
    breaker_low: Optional[float] = None
    breaker_high: Optional[float] = None
    breaker_distal_price: Optional[float] = None
    breaker_confirmed_at_ms: Optional[int] = None
    breaker_source_ob_id: Optional[str] = None
    broken_swing_id: Optional[str] = None

    fvg_id: Optional[str] = None
    selected_fvg_id: Optional[str] = None
    fvg_low: Optional[float] = None
    fvg_high: Optional[float] = None
    fvg_confirmed_at_ms: Optional[int] = None

    overlap_low: Optional[float] = None
    overlap_high: Optional[float] = None

    mss_or_bos_id: Optional[str] = None
    displacement_id: Optional[str] = None
    sweep_id: Optional[str] = None

    invalidation_source_id: Optional[str] = None
    invalidation_price: Optional[float] = None

    retest_timestamp_ms: Optional[int] = None
    confirmed_at_ms: int = 0
    known_at_ms: int = 0
    decision_cutoff_ms: int = 0

    validity_status: str = UnicornSetupStatus.VALID.value  # UnicornSetupStatus
    reason_codes: Tuple[str, ...] = field(default_factory=tuple)
    algorithm_version: str = "V17_8_0_1"

    def __post_init__(self) -> None:
        if self.selected_fvg_id is None and self.fvg_id is not None:
            object.__setattr__(self, "selected_fvg_id", self.fvg_id)
        if self.fvg_id is None and self.selected_fvg_id is not None:
            object.__setattr__(self, "fvg_id", self.selected_fvg_id)
        if self.confirmed_at_ms <= 0:
            b_conf = int(self.breaker_confirmed_at_ms or 0)
            f_conf = int(self.fvg_confirmed_at_ms or 0)
            conf = max(b_conf, f_conf)
            if conf > 0:
                object.__setattr__(self, "confirmed_at_ms", conf)

    @property
    def is_valid(self) -> bool:
        return self.validity_status in (
            UnicornSetupStatus.VALID.value,
            "CONFIRMED",
        )

    @property
    def has_overlap(self) -> bool:
        if self.overlap_low is not None and self.overlap_high is not None:
            return self.overlap_high > self.overlap_low
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "unicorn_id": self.unicorn_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "setup_episode_id": self.setup_episode_id,
            "breaker_id": self.breaker_id,
            "breaker_type": self.breaker_type,
            "breaker_low": self.breaker_low,
            "breaker_high": self.breaker_high,
            "breaker_distal_price": self.breaker_distal_price,
            "breaker_confirmed_at_ms": self.breaker_confirmed_at_ms,
            "breaker_source_ob_id": self.breaker_source_ob_id,
            "broken_swing_id": self.broken_swing_id,
            "fvg_id": self.fvg_id,
            "selected_fvg_id": self.selected_fvg_id,
            "fvg_low": self.fvg_low,
            "fvg_high": self.fvg_high,
            "fvg_confirmed_at_ms": self.fvg_confirmed_at_ms,
            "overlap_low": self.overlap_low,
            "overlap_high": self.overlap_high,
            "mss_or_bos_id": self.mss_or_bos_id,
            "displacement_id": self.displacement_id,
            "sweep_id": self.sweep_id,
            "invalidation_source_id": self.invalidation_source_id,
            "invalidation_price": self.invalidation_price,
            "retest_timestamp_ms": self.retest_timestamp_ms,
            "confirmed_at_ms": self.confirmed_at_ms,
            "known_at_ms": self.known_at_ms,
            "decision_cutoff_ms": self.decision_cutoff_ms,
            "validity_status": self.validity_status,
            "reason_codes": list(self.reason_codes),
            "algorithm_version": self.algorithm_version,
            "is_valid": self.is_valid,
            "has_overlap": self.has_overlap,
        }
