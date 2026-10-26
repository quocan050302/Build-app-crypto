"""
backend/services/smc_probability_types.py
V14.4.1 - What a "probability" or "winrate" may mean in this app. Three different things, never mixed:

  * ForecastEventDefinition   a SESSION event (e.g. "NY closes above its open" / "Asia high taken before Asia low").
  * TradeOutcomeDefinition    a TRADE event: TP touched before SL inside a fixed horizon, plus net PnL after costs.
  * Empirical net winrate     closed trades with net PnL > 0 on a real dataset/window (descriptive, not a promise).

A checklist / ranking score is NOT a probability and must never carry a % win label.
`p` is None whenever the support is not enough: the UI then says "Chưa có xác suất được kiểm định".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

PROBABILITY_SCHEMA_VERSION = "v14.4.1-prob"

STATUS_UNAVAILABLE = "UNAVAILABLE"
STATUS_INSUFFICIENT_SUPPORT = "INSUFFICIENT_SUPPORT"
STATUS_NOT_VERIFIED = "NOT_VERIFIED"
STATUS_VALIDATED = "VALIDATED"

MODE_RULE_BASED_PAPER = "RULE_BASED_PAPER"
MODE_VALIDATED_PROBABILITY_FILTER = "VALIDATED_PROBABILITY_FILTER"


@dataclass(frozen=True)
class ForecastEventDefinition:
    """A session-level event. NOT a trade result."""
    name: str                       # e.g. NY_CLOSE_ABOVE_OPEN
    session: str                    # ASIA | LONDON | NY
    horizon: str                    # e.g. SESSION_END
    labels: tuple = ("TARGET_FIRST", "ALTERNATIVE_FIRST", "UNRESOLVED")


@dataclass(frozen=True)
class TradeOutcomeDefinition:
    """A trade-level event: TP touched before SL within `horizon_bars` closed M5 bars after a real PAPER fill."""
    name: str = "TP_BEFORE_SL"
    horizon_bars: int = 72
    same_bar_policy: str = "AMBIGUOUS"          # both touched in one bar -> AMBIGUOUS (never wins-first)
    net_of_costs: bool = True
    timeout_label: str = "TIMEOUT"              # tagged separately; never counted as TP
    labels: tuple = ("TP_FIRST", "SL_FIRST", "AMBIGUOUS", "TIMEOUT", "CENSORED")


@dataclass(frozen=True)
class HistoricalSupport:
    """Descriptive support of a cohort (not a live win probability)."""
    n: int
    wins: int
    observed_winrate: Optional[float]
    ci_low: Optional[float]
    ci_high: Optional[float]
    status: str                                 # VALIDATED-support | INSUFFICIENT_SUPPORT | UNAVAILABLE
    min_n: int
    max_ci_width: float
    interpretation: str = "DESCRIPTIVE_COHORT_INTERVAL_NOT_LIVE_WIN_PROBABILITY"

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class ProbabilityEstimate:
    """The ONLY shape in which a probability may reach the UI / snapshots."""
    status: str = STATUS_UNAVAILABLE
    event: Optional[str] = None                 # TP_BEFORE_SL | session event name
    horizon: Optional[str] = None
    p: Optional[float] = None                   # None unless a validated model produced it
    support_n: Optional[int] = None
    model_version: Optional[str] = None
    cohort_version: Optional[str] = None
    oos_from_ms: Optional[int] = None
    oos_to_ms: Optional[int] = None
    calibration_status: str = "NOT_CALIBRATED"
    coverage: Optional[float] = None
    uncertainty: Optional[Dict[str, Any]] = None
    assumptions: Dict[str, Any] = field(default_factory=dict)
    note_vi: str = "Chưa có xác suất được kiểm định."
    schema_version: str = PROBABILITY_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status, "event": self.event, "horizon": self.horizon,
            "p_tp_before_sl": self.p if self.event == "TP_BEFORE_SL" else None,
            "p": self.p, "support_n": self.support_n, "model_version": self.model_version,
            "cohort_version": self.cohort_version, "oos_from_ms": self.oos_from_ms, "oos_to_ms": self.oos_to_ms,
            "calibration_status": self.calibration_status, "coverage": self.coverage,
            "uncertainty": self.uncertainty, "assumptions": dict(self.assumptions), "note_vi": self.note_vi,
            "schema_version": self.schema_version,
        }

    @staticmethod
    def unavailable(reason_vi: str = "Chưa có xác suất được kiểm định.") -> "ProbabilityEstimate":
        return ProbabilityEstimate(status=STATUS_UNAVAILABLE, note_vi=reason_vi)
