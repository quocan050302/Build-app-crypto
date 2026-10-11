"""
backend/services/smc_regime_service.py
V14.4 — Market regime classification and causal dealing range analysis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class DealingRange:
    high: Optional[float] = None
    low: Optional[float] = None
    equilibrium: Optional[float] = None
    price_zone: str = "UNKNOWN"              # PREMIUM, DISCOUNT, EQUILIBRIUM, UNKNOWN
    high_anchor_id: Optional[str] = None
    low_anchor_id: Optional[str] = None
    is_valid: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "high": self.high,
            "low": self.low,
            "equilibrium": self.equilibrium,
            "price_zone": self.price_zone,
            "high_anchor_id": self.high_anchor_id,
            "low_anchor_id": self.low_anchor_id,
            "is_valid": self.is_valid,
        }


def build_dealing_range(
    high: Optional[float],
    low: Optional[float],
    current_price: Optional[float] = None,
    high_anchor_id: Optional[str] = None,
    low_anchor_id: Optional[str] = None,
) -> DealingRange:
    """Builds dealing range from confirmed structural anchors.
    Never invents current_price +/- 10 if anchors are missing.
    """
    if high is None or low is None or high <= low:
        return DealingRange(is_valid=False)

    eq = round((high + low) / 2.0, 2)
    zone = "UNKNOWN"
    if current_price is not None and current_price > 0:
        if abs(current_price - eq) <= (high - low) * 0.05:
            zone = "EQUILIBRIUM"
        elif current_price > eq:
            zone = "PREMIUM"
        else:
            zone = "DISCOUNT"

    return DealingRange(
        high=round(high, 2),
        low=round(low, 2),
        equilibrium=eq,
        price_zone=zone,
        high_anchor_id=high_anchor_id,
        low_anchor_id=low_anchor_id,
        is_valid=True,
    )


def classify_regime(
    htf_bias: str,
    h1_alignment: str,
    atr_relative: Optional[float] = None,
) -> str:
    """Classifies market regime into TREND_UP, TREND_DOWN, RANGE, SHOCK, or UNKNOWN.
    Context-only explainable feature, does not arbitrarily block valid entries.
    """
    bias = (htf_bias or "UNKNOWN").upper()
    h1 = (h1_alignment or "UNKNOWN").upper()

    if atr_relative is not None and atr_relative > 2.5:
        return "SHOCK"

    if bias in ("BULLISH", "UP") and h1 in ("ALIGNED", "BULLISH", "UP"):
        return "TREND_UP"
    if bias in ("BEARISH", "DOWN") and h1 in ("ALIGNED", "BEARISH", "DOWN"):
        return "TREND_DOWN"
    if bias in ("NEUTRAL", "RANGE", "SIDEWAYS") or (bias != "UNKNOWN" and h1 in ("DIVERGENT", "OPPOSITE")):
        return "RANGE"

    return "UNKNOWN"


def get_research_context(symbol: str = "XAUUSDT") -> Dict[str, Any]:
    """Returns research context adapter.
    Per Phần 026: XAU-5F is research context only. If point-in-time data is absent, returns UNAVAILABLE.
    Never fakes macro scores, DXY, or pseudo probabilities.
    """
    return {
        "status": "UNAVAILABLE",
        "symbol": symbol,
        "note": "XAU-5F research factors unavailable point-in-time; not used for execution gates.",
    }
