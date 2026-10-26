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
    provenance: str = "NO_DATA"              # CLOSED_CANDLES_SWING_STRUCTURE | NO_DATA | INSUFFICIENT_BARS | ...
    # Additive V16.11 fields
    last_confirmed_bias: str = "UNKNOWN"
    phase: str = "UNDETERMINED"              # INSUFFICIENT_DATA, UNDETERMINED, ESTABLISHED, PULLBACK, TRANSITION, RANGE
    as_of_ms: Optional[int] = None
    last_bar_open_ms: Optional[int] = None
    last_bar_close_ms: Optional[int] = None
    reference_high: Optional[Dict[str, Any]] = None
    reference_low: Optional[Dict[str, Any]] = None
    protected_high: Optional[Dict[str, Any]] = None
    protected_low: Optional[Dict[str, Any]] = None
    latest_structure_event: Optional[Dict[str, Any]] = None
    indicator_bias: Optional[str] = None
    algorithm_version: str = "v16.13"
    data_status: str = "READY"
    effective_settings: Optional[Dict[str, Any]] = None
    config_version: Optional[int] = None
    settings_fingerprint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timeframe": self.timeframe,
            "bias": self.bias,
            "bars": self.bars,
            "last_closed_ms": self.last_closed_ms,
            "provenance": self.provenance,
            "last_confirmed_bias": self.last_confirmed_bias,
            "phase": self.phase,
            "as_of_ms": self.as_of_ms,
            "last_bar_open_ms": self.last_bar_open_ms,
            "last_bar_close_ms": self.last_bar_close_ms,
            "reference_high": self.reference_high,
            "reference_low": self.reference_low,
            "protected_high": self.protected_high,
            "protected_low": self.protected_low,
            "latest_structure_event": self.latest_structure_event,
            "indicator_bias": self.indicator_bias,
            "algorithm_version": self.algorithm_version,
            "data_status": self.data_status,
            "effective_settings": self.effective_settings,
            "config_version": self.config_version,
            "settings_fingerprint": self.settings_fingerprint,
        }


def build_htf_context(
    closed_candles: Sequence[Any],
    timeframe: str,
    *,
    min_bars: Optional[int] = None,
    as_of_ms: Optional[int] = None,
    config: Optional[Any] = None,
    symbol: str = "XAUUSDT",
) -> HTFContext:
    """
    Adapter closed candles -> HTFContext using V16.13 Causal HTF Structure Engine.
    Missing/short history returns UNKNOWN with provenance.
    Never uses SMA fallback as market structure evidence.
    """
    from services.htf_structure_service import evaluate_htf_structure, resolve_timeframe_ms, build_htf_settings_fingerprint

    if not closed_candles:
        return HTFContext(
            timeframe=timeframe,
            bias="UNKNOWN",
            last_confirmed_bias="UNKNOWN",
            phase="INSUFFICIENT_DATA",
            provenance="NO_DATA",
            data_status="NO_DATA",
            as_of_ms=as_of_ms,
        )

    dur = resolve_timeframe_ms(timeframe)
    # Determine default evaluation time from actual close time of the last candle if not provided
    if as_of_ms is None:
        last_c = closed_candles[-1]
        last_open = int(last_c.get("open_time") or last_c.get("timestamp") or 0) if isinstance(last_c, dict) else int(getattr(last_c, "open_time", None) or getattr(last_c, "timestamp", 0))
        computed_as_of = last_open + dur
    else:
        computed_as_of = as_of_ms

    snapshot = evaluate_htf_structure(
        candles=closed_candles,
        timeframe=timeframe,
        as_of_ms=computed_as_of,
        config=config,
        symbol=symbol,
        min_bars=min_bars,
    )

    eff_settings = snapshot.effective_settings if snapshot else None
    cfg_ver = getattr(config, "config_version", None) if config else (snapshot.config_version if snapshot else None)
    fingerprint = build_htf_settings_fingerprint(eff_settings) if eff_settings else None

    return HTFContext(
        timeframe=timeframe,
        bias=snapshot.bias,
        bars=snapshot.bars,
        last_closed_ms=snapshot.last_bar_close_ms,
        provenance=snapshot.provenance,
        last_confirmed_bias=snapshot.last_confirmed_bias,
        phase=snapshot.phase,
        as_of_ms=snapshot.as_of_ms,
        last_bar_open_ms=snapshot.last_bar_open_ms,
        last_bar_close_ms=snapshot.last_bar_close_ms,
        reference_high=snapshot.reference_high if isinstance(snapshot.reference_high, dict) else (snapshot.reference_high.to_dict() if snapshot.reference_high else None),
        reference_low=snapshot.reference_low if isinstance(snapshot.reference_low, dict) else (snapshot.reference_low.to_dict() if snapshot.reference_low else None),
        protected_high=snapshot.protected_high if isinstance(snapshot.protected_high, dict) else (snapshot.protected_high.to_dict() if snapshot.protected_high else None),
        protected_low=snapshot.protected_low if isinstance(snapshot.protected_low, dict) else (snapshot.protected_low.to_dict() if snapshot.protected_low else None),
        latest_structure_event=snapshot.latest_structure_event if isinstance(snapshot.latest_structure_event, dict) else (snapshot.latest_structure_event.to_dict() if snapshot.latest_structure_event else None),
        indicator_bias=snapshot.indicator_bias,
        algorithm_version=snapshot.algorithm_version,
        data_status=snapshot.data_status,
        effective_settings=eff_settings,
        config_version=cfg_ver,
        settings_fingerprint=fingerprint,
    )


def h1_alignment_from(h4: HTFContext, h1: HTFContext) -> str:
    h4_bias = getattr(h4, "bias", "UNKNOWN")
    h1_bias = getattr(h1, "bias", "UNKNOWN")
    h4_phase = getattr(h4, "phase", "UNDETERMINED")
    h1_phase = getattr(h1, "phase", "UNDETERMINED")

    if h4_bias in ("UNKNOWN",) or h1_bias in ("UNKNOWN",):
        return "UNKNOWN"
    if h4_phase == "TRANSITION" or h1_phase == "TRANSITION":
        return "DIVERGENT"
    if h4_bias == "RANGE" or h1_bias == "RANGE":
        return "DIVERGENT"
    return "ALIGNED" if h4_bias == h1_bias else "DIVERGENT"


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
