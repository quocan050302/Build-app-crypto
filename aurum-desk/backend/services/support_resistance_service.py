"""
backend/services/support_resistance_service.py
V16.37 - Support & Resistance (S/R) Zone Mapping, Break/Reclaim/Role-Flip, and Regime Routing Engine.

Provides:
- SRZone: Additive dataclass for Support/Resistance zones with causal known_at.
- cluster_confirmed_levels: Clusters nearby confirmed swing levels within tick/ATR tolerance.
- derive_sr_zone_bounds: Computes exact zone bounds from price clusters and candle bodies/wicks.
- build_support_resistance_zones: Builds causal S/R zones from confirmed swings & closed candles.
- evaluate_sr_break: Evaluates whether a candle close confirms a break of an S/R zone.
- evaluate_sr_reclaim: Evaluates whether a sweep beyond zone was closed back inside (reclaimed).
- evaluate_sr_role_flip: Evaluates whether a broken zone has flipped role (support <-> resistance).
- invalidate_sr_zone: Marks a zone invalidated with causal known_at and audit reason.
- explain_sr_zone_quality: Generates descriptive quality metrics and explanation for a zone.
- resolve_models_for_regime: Canonical regime-to-model routing matrix for SMC models.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.scalp_m1_types import ModelIdentifier

logger = logging.getLogger(__name__)


class SRRole(str, Enum):
    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"
    BROKEN = "BROKEN"
    FLIPPED = "FLIPPED"


class SRState(str, Enum):
    ACTIVE = "ACTIVE"
    TESTED = "TESTED"
    BROKEN = "BROKEN"
    RECLAIMED = "RECLAIMED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


@dataclass
class SRZone:
    """
    Additive Support / Resistance Zone entity (PHẦN 041 - 060).
    Strict causal known_at: cannot be known before confirmed swing right bars close.
    """
    zone_id: str
    symbol: str
    timeframe: str                           # "H1", "M15", "M5", "M1"
    role: str                                # SUPPORT, RESISTANCE, BROKEN, FLIPPED
    top: float
    bottom: float
    midpoint: float
    source_swing_ids: Tuple[str, ...] = ()
    created_at_ms: int = 0                   # Timestamp when swing occurred
    known_at_ms: int = 0                     # Timestamp when swing was fully confirmed
    state: str = SRState.ACTIVE.value
    touch_count: int = 0
    last_touch_ms: int = 0
    broken_at_ms: Optional[int] = None
    reclaimed_at_ms: Optional[int] = None
    flipped_at_ms: Optional[int] = None
    invalidated_at_ms: Optional[int] = None
    invalidation_reason: Optional[str] = None
    quality_score: float = 0.0               # Heuristic ranking score [0.0 - 1.0]
    data_status: str = "CONFIRMED"           # CONFIRMED or PROVISIONAL
    version: str = "v16.37"

    def __post_init__(self):
        if not (math.isfinite(self.top) and math.isfinite(self.bottom) and math.isfinite(self.midpoint)):
            raise ValueError(f"NON_FINITE_SR_ZONE_BOUNDS: top={self.top}, bottom={self.bottom}, midpoint={self.midpoint}")
        if self.top <= 0 or self.bottom <= 0 or self.midpoint <= 0:
            raise ValueError(f"NON_POSITIVE_SR_ZONE_BOUNDS: top={self.top}, bottom={self.bottom}, midpoint={self.midpoint}")
        if self.top <= self.bottom:
            raise ValueError(f"INVALID_SR_ZONE_BOUNDS: top ({self.top}) must be > bottom ({self.bottom})")
        if not (self.bottom <= self.midpoint <= self.top):
            raise ValueError(f"MIDPOINT_OUT_OF_BOUNDS: midpoint ({self.midpoint}) must be between bottom ({self.bottom}) and top ({self.top})")

    @property
    def height(self) -> float:
        return self.top - self.bottom

    def to_dict(self) -> Dict[str, Any]:
        return {
            "zone_id": self.zone_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "role": self.role,
            "top": self.top,
            "bottom": self.bottom,
            "midpoint": self.midpoint,
            "height": self.height,
            "source_swing_ids": list(self.source_swing_ids),
            "created_at_ms": self.created_at_ms,
            "known_at_ms": self.known_at_ms,
            "state": self.state,
            "touch_count": self.touch_count,
            "last_touch_ms": self.last_touch_ms,
            "broken_at_ms": self.broken_at_ms,
            "reclaimed_at_ms": self.reclaimed_at_ms,
            "flipped_at_ms": self.flipped_at_ms,
            "invalidated_at_ms": self.invalidated_at_ms,
            "invalidation_reason": self.invalidation_reason,
            "quality_score": self.quality_score,
            "data_status": self.data_status,
            "version": self.version,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> SRZone:
        return SRZone(
            zone_id=d["zone_id"],
            symbol=d.get("symbol", "XAUUSDT"),
            timeframe=d.get("timeframe", "M15"),
            role=d.get("role", SRRole.SUPPORT.value),
            top=float(d["top"]),
            bottom=float(d["bottom"]),
            midpoint=float(d.get("midpoint", (float(d["top"]) + float(d["bottom"])) / 2.0)),
            source_swing_ids=tuple(d.get("source_swing_ids") or ()),
            created_at_ms=int(d.get("created_at_ms", 0)),
            known_at_ms=int(d.get("known_at_ms", 0)),
            state=d.get("state", SRState.ACTIVE.value),
            touch_count=int(d.get("touch_count", 0)),
            last_touch_ms=int(d.get("last_touch_ms", 0)),
            broken_at_ms=d.get("broken_at_ms"),
            reclaimed_at_ms=d.get("reclaimed_at_ms"),
            flipped_at_ms=d.get("flipped_at_ms"),
            invalidated_at_ms=d.get("invalidated_at_ms"),
            invalidation_reason=d.get("invalidation_reason"),
            quality_score=float(d.get("quality_score", 0.0)),
            data_status=d.get("data_status", "CONFIRMED"),
            version=d.get("version", "v16.37"),
        )


def _extract_level_price(x: Any) -> float:
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, dict):
        return float(x.get("price", x.get("level", 0.0)))
    return float(getattr(x, "price", getattr(x, "level", 0.0)))


def cluster_confirmed_levels(
    levels: Sequence[Any],
    tolerance_usd: float = 1.50,
    max_span_usd: Optional[float] = None,
) -> List[List[Any]]:
    """
    V16.38 Rules 056, 057:
    Clusters nearby confirmed price levels within tolerance_usd AND max_span_usd.
    Prevents endless chaining of levels into an overly wide zone.
    """
    if not levels:
        return []

    eff_max_span = max_span_usd if max_span_usd is not None else (tolerance_usd * 2.0)
    sorted_levels = sorted(levels, key=lambda x: _extract_level_price(x))
    clusters: List[List[Any]] = []
    current_cluster: List[Any] = [sorted_levels[0]]

    for lvl in sorted_levels[1:]:
        step_diff = abs(_extract_level_price(lvl) - _extract_level_price(current_cluster[-1]))
        total_span = abs(_extract_level_price(lvl) - _extract_level_price(current_cluster[0]))
        if step_diff <= tolerance_usd and total_span <= eff_max_span:
            current_cluster.append(lvl)
        else:
            clusters.append(current_cluster)
            current_cluster = [lvl]

    if current_cluster:
        clusters.append(current_cluster)

    return clusters


def derive_sr_zone_bounds(
    cluster: Sequence[Any],
    buffer_ticks: int = 20,
    tick_size: float = 0.01,
) -> Tuple[float, float, float]:
    """
    V16.38 Rule 058:
    Derives (top, bottom, midpoint) from a cluster of swing levels.
    Quantizes strictly according to instrument tick_size.
    Ensures minimum width of buffer_ticks * tick_size to avoid zero-height zones.
    """
    prices = [_extract_level_price(x) for x in cluster]
    min_px = min(prices)
    max_px = max(prices)
    min_buffer = buffer_ticks * tick_size

    def _quantize(val: float, ts: float) -> float:
        decimals = max(0, -int(math.floor(math.log10(ts)))) if ts < 1.0 else 0
        return round(round(val / ts) * ts, decimals)

    if (max_px - min_px) < min_buffer:
        center = (max_px + min_px) / 2.0
        top = _quantize(center + (min_buffer / 2.0), tick_size)
        bottom = _quantize(center - (min_buffer / 2.0), tick_size)
    else:
        top = _quantize(max_px + (min_buffer / 2.0), tick_size)
        bottom = _quantize(min_px - (min_buffer / 2.0), tick_size)

    if top <= bottom:
        top = _quantize(bottom + min_buffer, tick_size)

    midpoint = _quantize((top + bottom) / 2.0, tick_size)
    if not (bottom <= midpoint <= top):
        midpoint = round((top + bottom) / 2.0, 4)

    return top, bottom, midpoint


def build_support_resistance_zones(
    swings: Sequence[Any],
    *,
    symbol: str = "XAUUSDT",
    timeframe: str = "M15",
    as_of_ms: int = 0,
    tolerance_usd: float = 1.50,
    max_span_usd: Optional[float] = None,
    tick_size: float = 0.01,
    closed_candles: Optional[Sequence[Any]] = None,
    atr: Optional[float] = None,
    **kwargs: Any,
) -> List[SRZone]:
    """
    PHẦN 041 - 046, V16.38 Rules 052 - 055: Builds S/R zones from confirmed swings available at as_of_ms.
    Strict causality: a swing is only included if 0 < known_at_ms <= as_of_ms.
    Discards future swings or swings lacking valid known_at.
    """
    known_swings = []
    for s in swings:
        if isinstance(s, dict):
            k_at = int(s.get("known_at_ms", s.get("confirmed_at", s.get("known_at", 0))) or 0)
        else:
            k_at = int(getattr(s, "known_at_ms", getattr(s, "confirmed_at", getattr(s, "known_at", 0))) or 0)
        if k_at <= 0:
            continue
        if as_of_ms > 0 and k_at > as_of_ms:
            continue
        known_swings.append(s)

    if not known_swings:
        return []

    def _get_side(s: Any) -> str:
        if isinstance(s, dict):
            return str(s.get("side", s.get("type", ""))).upper()
        return str(getattr(s, "side", getattr(s, "type", ""))).upper()

    high_swings = [s for s in known_swings if _get_side(s) in ("BUY_SIDE", "HIGH")]
    low_swings = [s for s in known_swings if _get_side(s) in ("SELL_SIDE", "LOW")]

    zones: List[SRZone] = []

    def _get_attr(x: Any, key: str, fallback: Any) -> Any:
        if isinstance(x, dict):
            return x.get(key, fallback)
        return getattr(x, key, fallback)

    # Build Resistance Zones from High swings
    high_clusters = cluster_confirmed_levels(high_swings, tolerance_usd=tolerance_usd, max_span_usd=max_span_usd)
    for cluster in high_clusters:
        top, bottom, midpoint = derive_sr_zone_bounds(cluster, buffer_ticks=20, tick_size=tick_size)
        src_ids = tuple(str(_get_attr(x, "id", "")) for x in cluster if _get_attr(x, "id", None))
        created_at = min(int(_get_attr(x, "occurred_at_ms", _get_attr(x, "timestamp", as_of_ms))) for x in cluster)
        known_at = max(int(_get_attr(x, "known_at_ms", _get_attr(x, "confirmed_at", _get_attr(x, "known_at", as_of_ms)))) for x in cluster)
        z_id = f"sr-res-{symbol}-{timeframe}-{midpoint:.2f}-{known_at}"
        zones.append(
            SRZone(
                zone_id=z_id,
                symbol=symbol,
                timeframe=timeframe,
                role=SRRole.RESISTANCE.value,
                top=top,
                bottom=bottom,
                midpoint=midpoint,
                source_swing_ids=src_ids,
                created_at_ms=created_at,
                known_at_ms=known_at,
                state=SRState.ACTIVE.value,
                touch_count=len(cluster),
                quality_score=min(1.0, 0.4 + 0.15 * len(cluster)),
            )
        )

    # Build Support Zones from Low swings
    low_clusters = cluster_confirmed_levels(low_swings, tolerance_usd=tolerance_usd, max_span_usd=max_span_usd)
    for cluster in low_clusters:
        top, bottom, midpoint = derive_sr_zone_bounds(cluster, buffer_ticks=20, tick_size=tick_size)
        src_ids = tuple(str(_get_attr(x, "id", "")) for x in cluster if _get_attr(x, "id", None))
        created_at = min(int(_get_attr(x, "occurred_at_ms", _get_attr(x, "timestamp", as_of_ms))) for x in cluster)
        known_at = max(int(_get_attr(x, "known_at_ms", _get_attr(x, "confirmed_at", _get_attr(x, "known_at", as_of_ms)))) for x in cluster)
        z_id = f"sr-sup-{symbol}-{timeframe}-{midpoint:.2f}-{known_at}"
        zones.append(
            SRZone(
                zone_id=z_id,
                symbol=symbol,
                timeframe=timeframe,
                role=SRRole.SUPPORT.value,
                top=top,
                bottom=bottom,
                midpoint=midpoint,
                source_swing_ids=src_ids,
                created_at_ms=created_at,
                known_at_ms=known_at,
                state=SRState.ACTIVE.value,
                touch_count=len(cluster),
                quality_score=min(1.0, 0.4 + 0.15 * len(cluster)),
            )
        )
    return zones


def evaluate_sr_break(
    zone: SRZone,
    candle: Any,
    buffer_ticks: int = 5,
    tick_size: float = 0.01,
) -> str:
    """
    PHẦN 051: Evaluates whether a closed candle confirms an S/R zone break.
    - Not broken: candle closed within or on valid side of zone.
    - Break confirmed:
      - Resistance broken: candle close > zone.top + buffer.
      - Support broken: candle close < zone.bottom - buffer.
    Returns: 'NOT_BROKEN' | 'BREAK_CONFIRMED'
    """
    c_close = float(getattr(candle, "close", 0.0))
    buffer_usd = buffer_ticks * tick_size

    if zone.role in (SRRole.RESISTANCE.value, SRRole.FLIPPED.value) and zone.top > 0:
        if c_close > (zone.top + buffer_usd):
            return "BREAK_CONFIRMED"
    elif zone.role in (SRRole.SUPPORT.value, SRRole.FLIPPED.value) and zone.bottom > 0:
        if c_close < (zone.bottom - buffer_usd):
            return "BREAK_CONFIRMED"

    return "NOT_BROKEN"


def _get_candle_val(c: Any, key: str, fallback: Any = 0.0) -> Any:
    if isinstance(c, dict):
        return c.get(key, fallback)
    return getattr(c, key, fallback)


def evaluate_sr_reclaim(
    zone: SRZone,
    sweep_candle: Any,
    reclaim_candle: Any,
) -> bool:
    """
    PHẦN 052, Rules 064, 065:
    Evaluates whether price swept beyond zone boundary and then reclaimed (closed back inside).
    Enforces chronology: reclaim_candle close_time >= sweep_candle close_time.
    For Resistance: sweep candle wick high > zone.top, then reclaim candle close <= zone.top.
    For Support: sweep candle wick low < zone.bottom, then reclaim candle close >= zone.bottom.
    """
    sw_time = int(_get_candle_val(sweep_candle, "close_time", _get_candle_val(sweep_candle, "timestamp", _get_candle_val(sweep_candle, "open_time", 0))))
    rec_time = int(_get_candle_val(reclaim_candle, "close_time", _get_candle_val(reclaim_candle, "timestamp", _get_candle_val(reclaim_candle, "open_time", 0))))
    if rec_time < sw_time:
        return False

    sw_high = float(_get_candle_val(sweep_candle, "high", 0.0))
    sw_low = float(_get_candle_val(sweep_candle, "low", 0.0))
    rec_close = float(_get_candle_val(reclaim_candle, "close", 0.0))

    if zone.role == SRRole.RESISTANCE.value:
        if sw_high > zone.top and rec_close <= zone.top:
            return True
    elif zone.role == SRRole.SUPPORT.value:
        if sw_low < zone.bottom and rec_close >= zone.bottom:
            return True

    return False


def evaluate_sr_role_flip(
    zone: SRZone,
    retest_candle_or_break: Any,
    break_confirmed_or_retest: Any = True,
    break_candle_or_time: Optional[Any] = None,
) -> Optional[str]:
    """
    PHẦN 053, Rules 066, 067:
    Evaluates role flip after break + retest.
    - Support broken + retested from below -> flips to RESISTANCE when close confirms below zone.
    - Resistance broken + retested from above -> flips to SUPPORT when close confirms above zone.
    If candle close is inside zone (pending retest): returns None.
    """
    if isinstance(retest_candle_or_break, bool):
        break_confirmed = retest_candle_or_break
        retest_candle = break_confirmed_or_retest
    else:
        retest_candle = retest_candle_or_break
        break_confirmed = bool(break_confirmed_or_retest)

    if not break_confirmed:
        return None

    if break_candle_or_time is not None:
        b_time = break_candle_or_time if isinstance(break_candle_or_time, int) else int(_get_candle_val(break_candle_or_time, "close_time", _get_candle_val(break_candle_or_time, "timestamp", 0)))
        r_time = int(_get_candle_val(retest_candle, "close_time", _get_candle_val(retest_candle, "timestamp", 0)))
        if r_time < b_time:
            return None

    r_low = float(_get_candle_val(retest_candle, "low", 0.0))
    r_high = float(_get_candle_val(retest_candle, "high", 0.0))
    r_close = float(_get_candle_val(retest_candle, "close", 0.0))

    if zone.role == SRRole.SUPPORT.value:
        # Broken support retested from below: high touches zone bottom, closes below bottom
        if r_high >= zone.bottom:
            if r_close < zone.bottom:
                return SRRole.RESISTANCE.value
            # Close inside zone or above is pending / unconfirmed
    elif zone.role == SRRole.RESISTANCE.value:
        # Broken resistance retested from above: low touches zone top, closes above top
        if r_low <= zone.top:
            if r_close > zone.top:
                return SRRole.SUPPORT.value

    return None


def invalidate_sr_zone(zone: SRZone, reason: str, now_ms: int) -> SRZone:
    """
    PHẦN 054, Rule 069:
    Causally invalidates an S/R zone. Preserves historical zone record and actual broken_at_ms.
    """
    zone.state = SRState.INVALIDATED.value
    zone.invalidation_reason = reason
    zone.invalidated_at_ms = now_ms
    return zone


def explain_sr_zone_quality(zone: SRZone) -> Dict[str, Any]:
    """
    PHẦN 056: Returns descriptive quality explanation (no fake win probabilities).
    """
    return {
        "zone_id": zone.zone_id,
        "timeframe": zone.timeframe,
        "role": zone.role,
        "touch_count": zone.touch_count,
        "quality_score": round(zone.quality_score, 2),
        "data_status": zone.data_status,
        "status": zone.state,
        "summary_vi": (
            f"Vùng {zone.role.lower()} ({zone.timeframe}) từ {zone.bottom:.2f} đến {zone.top:.2f} "
            f"đã được kiểm tra {zone.touch_count} lần cấu trúc."
        ),
    }


def resolve_models_for_regime(regime: str) -> List[str]:
    """
    PHẦN 070: Maps market regime to eligible SMC model IDs.
    Returns allowable model IDs for evaluation under the given regime.
    - TREND_BULLISH / TREND_BEARISH: Continuation, FVG Continuation, CG-01
    - RANGE: Reversal, Sweep Reclaim
    - EXPANSION: FVG Continuation, Continuation
    - UNCERTAIN / TRANSITION: CG-01, Reversal
    """
    r_clean = str(regime).strip().upper()
    if r_clean in ("TREND_BULLISH", "TREND_BEARISH", "TREND"):
        return [
            ModelIdentifier.SMC_M1_CONTINUATION.value,
            ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
            ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        ]
    elif r_clean == "RANGE":
        return [
            ModelIdentifier.SMC_M1_REVERSAL.value,
            ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
        ]
    elif r_clean == "EXPANSION":
        return [
            ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
            ModelIdentifier.SMC_M1_CONTINUATION.value,
        ]
    else:  # UNCERTAIN, TRANSITION, etc.
        return [
            ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
            ModelIdentifier.SMC_M1_REVERSAL.value,
        ]
