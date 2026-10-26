"""
backend/services/smc_probability_service.py
V14.4.1 - Honest statistics helpers. Nothing here produces a live win probability by itself.

wilson_interval / expected_net_r follow the reviewed reference helpers (Appendix D). A Wilson interval is a
DESCRIPTIVE binomial cohort interval; it does not handle dependence between trades of the same day/regime.
"""
from __future__ import annotations

from math import isfinite, sqrt
from typing import Any, Dict, Optional

from services.smc_probability_types import (
    HistoricalSupport, MODE_RULE_BASED_PAPER, MODE_VALIDATED_PROBABILITY_FILTER,
    STATUS_INSUFFICIENT_SUPPORT, STATUS_UNAVAILABLE,
)

# Hypothesis thresholds (config, NOT a guarantee of profit)
MIN_SUPPORT_N = 30
MAX_CI_WIDTH = 0.30

MODES = (MODE_RULE_BASED_PAPER, MODE_VALIDATED_PROBABILITY_FILTER)


def wilson_interval(wins: int, total: int, z: float = 1.959963984540054) -> Dict[str, Any]:
    if type(wins) is not int or type(total) is not int or total <= 0 or not 0 <= wins <= total:
        raise ValueError("INVALID_WINRATE_SAMPLE")
    if not isfinite(z) or z <= 0:
        raise ValueError("INVALID_CONFIDENCE_LEVEL")
    p = wins / total
    denominator = 1.0 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return {"wins": wins, "n": total, "observed_winrate": p,
            "low": max(0.0, center - half), "high": min(1.0, center + half),
            "interpretation": "DESCRIPTIVE_COHORT_INTERVAL_NOT_LIVE_WIN_PROBABILITY"}


def get_historical_support(wins: Optional[int], total: Optional[int], *, min_n: int = MIN_SUPPORT_N,
                           max_ci_width: float = MAX_CI_WIDTH) -> HistoricalSupport:
    """Cohort support. Small cohorts (3/3) are INSUFFICIENT_SUPPORT - never a certified 100%."""
    if total is None or wins is None or total <= 0:
        return HistoricalSupport(n=0, wins=0, observed_winrate=None, ci_low=None, ci_high=None,
                                 status=STATUS_UNAVAILABLE, min_n=min_n, max_ci_width=max_ci_width)
    w = wilson_interval(int(wins), int(total))
    width = w["high"] - w["low"]
    enough = total >= min_n and width <= max_ci_width
    return HistoricalSupport(
        n=total, wins=wins, observed_winrate=w["observed_winrate"], ci_low=w["low"], ci_high=w["high"],
        status="SUPPORTED" if enough else STATUS_INSUFFICIENT_SUPPORT, min_n=min_n, max_ci_width=max_ci_width)


def expected_net_r(*, probability: float, win_net_r: float, loss_net_r: float) -> float:
    """Inputs are already NET of modelled costs. Costs are not subtracted again."""
    if not all(isfinite(v) for v in (probability, win_net_r, loss_net_r)):
        raise ValueError("EV_DATA_UNAVAILABLE")
    if not 0 <= probability <= 1 or loss_net_r < 0:
        raise ValueError("EV_INPUT_INVALID")
    return probability * win_net_r - (1 - probability) * loss_net_r


def estimate_expected_net_r(*, probability: Optional[float], win_net_r: Optional[float], loss_net_r: Optional[float],
                            p_low: Optional[float] = None, other_outcomes: Optional[Dict[str, float]] = None
                            ) -> Dict[str, Any]:
    """EV only from a VALIDATED probability. Timeout/unfilled outcomes with non-zero weight make the two-state
    formula invalid -> UNAVAILABLE (never a silently optimistic number). Returns the conservative EV with p_low."""
    if probability is None or win_net_r is None or loss_net_r is None:
        return {"status": STATUS_UNAVAILABLE, "ev_net_r": None, "ev_conservative_net_r": None}
    if other_outcomes and any(abs(v) > 0 for v in other_outcomes.values()):
        return {"status": STATUS_UNAVAILABLE, "ev_net_r": None, "ev_conservative_net_r": None,
                "reason": "MULTI_OUTCOME_EXPECTATION_REQUIRED"}
    try:
        ev = expected_net_r(probability=probability, win_net_r=win_net_r, loss_net_r=loss_net_r)
        cons = expected_net_r(probability=p_low if p_low is not None else probability, win_net_r=win_net_r,
                              loss_net_r=loss_net_r)
    except ValueError as exc:
        return {"status": STATUS_UNAVAILABLE, "ev_net_r": None, "ev_conservative_net_r": None, "reason": str(exc)}
    return {"status": "OK", "ev_net_r": ev, "ev_conservative_net_r": cons,
            "assumptions": {"inputs_are_net_of_costs": True, "two_outcome_model": True}}


def resolve_mode(configured: Optional[str]) -> str:
    """Default RULE_BASED_PAPER. An unknown value is a configuration error -> default (no silent filter)."""
    return configured if configured in MODES else MODE_RULE_BASED_PAPER


def probability_filter_decision(mode: str, model_status: Optional[str]) -> Dict[str, Any]:
    """RULE_BASED_PAPER never gates on a model. VALIDATED_PROBABILITY_FILTER with no eligible model -> NO_ENTRY
    with a reason (never a silent downgrade to rule-based)."""
    if resolve_mode(mode) == MODE_RULE_BASED_PAPER:
        return {"allowed": True, "mode": MODE_RULE_BASED_PAPER, "reason": None}
    if model_status != "VALIDATED":
        return {"allowed": False, "mode": MODE_VALIDATED_PROBABILITY_FILTER, "reason": "NO_VALIDATED_MODEL"}
    return {"allowed": True, "mode": MODE_VALIDATED_PROBABILITY_FILTER, "reason": None}
