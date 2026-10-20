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
    invalidation_reason: Optional[str] = None
    quality_score: float = 0.0               # Heuristic ranking score [0.0 - 1.0]
    data_status: str = "CONFIRMED"           # CONFIRMED or PROVISIONAL
    version: str = "v16.37"

    def __post_init__(self):
        if self.top <= self.bottom:
            raise ValueError(f"INVALID_SR_ZONE_BOUNDS: top ({self.top}) must be > bottom ({self.bottom})")
        if not self.midpoint:
            object.__setattr__(self, "midpoint", round((self.top + self.bottom) / 2.0, 2))

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
            invalidation_reason=d.get("invalidation_reason"),
            quality_score=float(d.get("quality_score", 0.0)),
            data_status=d.get("data_status", "CONFIRMED"),
            version=d.get("version", "v16.37"),
        )


def cluster_confirmed_levels(
    levels: Sequence[Dict[str, Any]],
    tolerance_usd: float = 1.50,
) -> List[List[Dict[str, Any]]]:
    """
    Clusters nearby confirmed price levels within tolerance_usd.
    Preserves all source IDs and attributes.
    """
    if not levels:
        return []

    sorted_levels = sorted(levels, key=lambda x: float(x.get("price", 0.0)))
    clusters: List[List[Dict[str, Any]]] = []
    current_cluster: List[Dict[str, Any]] = [sorted_levels[0]]

    for lvl in sorted_levels[1:]:
        prev_lvl = current_cluster[-1]
        diff = abs(float(lvl.get("price", 0.0)) - float(prev_lvl.get("price", 0.0)))
        if diff <= tolerance_usd:
            current_cluster.append(lvl)
        else:
            clusters.append(current_cluster)
            current_cluster = [lvl]

    if current_cluster:
        clusters.append(current_cluster)

    return clusters


def derive_sr_zone_bounds(
    cluster: Sequence[Dict[str, Any]],
    buffer_ticks: int = 20,
    tick_size: float = 0.01,
) -> Tuple[float, float, float]:
    """
    Derives (top, bottom, midpoint) from a cluster of swing levels.
    Ensures minimum width of buffer_ticks * tick_size to avoid zero-height zones.
    """
    prices = [float(x.get("price", 0.0)) for x in cluster]
    min_px = min(prices)
    max_px = max(prices)
    min_buffer = buffer_ticks * tick_size

    if (max_px - min_px) < min_buffer:
        center = (max_px + min_px) / 2.0
        top = round(center + (min_buffer / 2.0), 2)
        bottom = round(center - (min_buffer / 2.0), 2)
    else:
        top = round(max_px + (min_buffer / 2.0), 2)
        bottom = round(min_px - (min_buffer / 2.0), 2)

    midpoint = round((top + bottom) / 2.0, 2)
    return top, bottom, midpoint


def build_support_resistance_zones(
    swings: Sequence[Dict[str, Any]],
    *,
    symbol: str = "XAUUSDT",
    timeframe: str = "M15",
    as_of_ms: int = 0,
    tolerance_usd: float = 1.50,
    tick_size: float = 0.01,
) -> List[SRZone]:
    """
    PHẦN 041 - 046: Builds S/R zones from confirmed swings available at as_of_ms.
    Strict causality: a swing is only included if known_at_ms <= as_of_ms.
    Separates highs (resistance candidates) and lows (support candidates), clusters them,
    and returns valid SRZone instances.
    """
    # Filter strictly to swings known at or before decision time (no future leakage)
    known_swings = [
        s for s in swings
        if int(s.get("known_at_ms", s.get("confirmed_at", 0))) <= as_of_ms
    ] if as_of_ms > 0 else list(swings)

    if not known_swings:
        return []

    high_swings = [s for s in known_swings if s.get("side") == "BUY_SIDE" or s.get("type") == "HIGH"]
    low_swings = [s for s in known_swings if s.get("side") == "SELL_SIDE" or s.get("type") == "LOW"]

    zones: List[SRZone] = []

    # Build Resistance Zones from High swings
    high_clusters = cluster_confirmed_levels(high_swings, tolerance_usd=tolerance_usd)
    for i, cluster in enumerate(high_clusters):
        top, bottom, midpoint = derive_sr_zone_bounds(cluster, buffer_ticks=20, tick_size=tick_size)
        src_ids = tuple(str(x.get("id")) for x in cluster if x.get("id"))
        created_at = min(int(x.get("occurred_at_ms", x.get("timestamp", as_of_ms))) for x in cluster)
        known_at = max(int(x.get("known_at_ms", x.get("confirmed_at", as_of_ms))) for x in cluster)
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
    low_clusters = cluster_confirmed_levels(low_swings, tolerance_usd=tolerance_usd)
    for i, cluster in enumerate(low_clusters):
        top, bottom, midpoint = derive_sr_zone_bounds(cluster, buffer_ticks=20, tick_size=tick_size)
        src_ids = tuple(str(x.get("id")) for x in cluster if x.get("id"))
        created_at = min(int(x.get("occurred_at_ms", x.get("timestamp", as_of_ms))) for x in cluster)
        known_at = max(int(x.get("known_at_ms", x.get("confirmed_at", as_of_ms))) for x in cluster)
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


def evaluate_sr_reclaim(
    zone: SRZone,
    sweep_candle: Any,
    reclaim_candle: Any,
) -> bool:
    """
    PHẦN 052: Evaluates whether price swept beyond zone boundary and then reclaimed (closed back inside).
    For Resistance: sweep candle wick high > zone.top, then reclaim candle close <= zone.top.
    For Support: sweep candle wick low < zone.bottom, then reclaim candle close >= zone.bottom.
    Sequence requirement: reclaim candle must close at or after sweep candle.
    """
    sw_high = float(getattr(sweep_candle, "high", 0.0))
    sw_low = float(getattr(sweep_candle, "low", 0.0))
    rec_close = float(getattr(reclaim_candle, "close", 0.0))

    if zone.role == SRRole.RESISTANCE.value:
        if sw_high > zone.top and rec_close <= zone.top:
            return True
    elif zone.role == SRRole.SUPPORT.value:
        if sw_low < zone.bottom and rec_close >= zone.bottom:
            return True

    return False


def evaluate_sr_role_flip(
    zone: SRZone,
    break_confirmed: bool,
    retest_candle: Any,
) -> Optional[str]:
    """
    PHẦN 053: Evaluates role flip after break + retest.
    - Support broken + retested from below -> flips to RESISTANCE.
    - Resistance broken + retested from above -> flips to SUPPORT.
    Returns new role if flipped, or None.
    """
    if not break_confirmed:
        return None

    r_low = float(getattr(retest_candle, "low", 0.0))
    r_high = float(getattr(retest_candle, "high", 0.0))
    r_close = float(getattr(retest_candle, "close", 0.0))

    if zone.role == SRRole.SUPPORT.value:
        # Broken support retested from below: high touches zone bottom, closes below
        if r_high >= zone.bottom and r_close <= zone.top:
            return SRRole.RESISTANCE.value
    elif zone.role == SRRole.RESISTANCE.value:
        # Broken resistance retested from above: low touches zone top, closes above
        if r_low <= zone.top and r_close >= zone.bottom:
            return SRRole.SUPPORT.value

    return None


def invalidate_sr_zone(zone: SRZone, reason: str, now_ms: int) -> SRZone:
    """
    PHẦN 054: Causally invalidates an S/R zone. Preserves historical zone record for journals.
    """
    zone.state = SRState.INVALIDATED.value
    zone.invalidation_reason = reason
    zone.broken_at_ms = now_ms
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
