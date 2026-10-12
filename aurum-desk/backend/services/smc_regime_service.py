"""
backend/services/smc_regime_service.py
V14.4 — Market regime classification and causal dealing range analysis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence


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


@dataclass
class HTFContext:
    """Higher-timeframe bias derived ONLY from that timeframe's own closed candles (never from M5)."""
    timeframe: str
    bias: str                                # BULLISH | BEARISH | RANGE | UNKNOWN
    bars: int = 0
    last_closed_ms: Optional[int] = None
    provenance: str = "NO_DATA"              # CLOSED_CANDLES_SWING_STRUCTURE | NO_DATA | INSUFFICIENT_BARS

    def to_dict(self) -> Dict[str, Any]:
        return {"timeframe": self.timeframe, "bias": self.bias, "bars": self.bars,
                "last_closed_ms": self.last_closed_ms, "provenance": self.provenance}


def build_htf_context(closed_candles: Sequence[Any], timeframe: str, *, min_bars: int = 12) -> HTFContext:
    """Adapter closed candles -> HTFContext. Missing/short history returns UNKNOWN with provenance."""
    if not closed_candles:
        return HTFContext(timeframe=timeframe, bias="UNKNOWN", provenance="NO_DATA")
    if len(closed_candles) < min_bars:
        return HTFContext(timeframe=timeframe, bias="UNKNOWN", bars=len(closed_candles),
                          last_closed_ms=closed_candles[-1].timestamp, provenance="INSUFFICIENT_BARS")
    import smc_engine
    sh, sl = smc_engine.identify_pivots(list(closed_candles), timeframe=timeframe)
    trend = smc_engine.determine_causal_trend_at(
        list(closed_candles), len(closed_candles) - 1, sh, sl,
        closed_candles[-1].timestamp + smc_engine.TIMEFRAME_MS.get(timeframe, 0),
    ) if (sh and sl) else "UNKNOWN"
    bias = {"BULLISH": "BULLISH", "BEARISH": "BEARISH", "RANGING": "RANGE"}.get(trend, "UNKNOWN")
    return HTFContext(timeframe=timeframe, bias=bias, bars=len(closed_candles),
                      last_closed_ms=closed_candles[-1].timestamp,
                      provenance="CLOSED_CANDLES_SWING_STRUCTURE" if bias != "UNKNOWN" else "NO_STRUCTURE")


def h1_alignment_from(h4: HTFContext, h1: HTFContext) -> str:
    if h4.bias in ("UNKNOWN",) or h1.bias in ("UNKNOWN",):
        return "UNKNOWN"
    if h4.bias == "RANGE" or h1.bias == "RANGE":
        return "DIVERGENT"
    return "ALIGNED" if h4.bias == h1.bias else "DIVERGENT"


def atr_relative(closed_candles: Sequence[Any], short: int = 3, long: int = 20) -> Optional[float]:
    """Mean true range of the last `short` bars divided by the mean over the `long` bars before them."""
    n = len(closed_candles)
    if n < short + long + 1:
        return None

    def tr(i: int) -> float:
        c, p = closed_candles[i], closed_candles[i - 1]
        return max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))

    recent = [tr(i) for i in range(n - short, n)]
    base = [tr(i) for i in range(n - short - long, n - short)]
    base_mean = sum(base) / len(base)
    if base_mean <= 0:
        return None
    return round((sum(recent) / len(recent)) / base_mean, 3)


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
