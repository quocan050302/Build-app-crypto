"""
backend/services/liquidity_scenario_service.py
V15.2 - Causal Zone, Order Block, Liquidity Scenario & Multi-Dimensional Ranking Engine.
Sections 041 - 100, 159 - 165.

Core Components:
1. Direction Normalization & Price Formatting.
2. Causal FVG & Order Block (OB) Detectors with strict known_at and bounds policy.
3. Zone Confluence Evaluation (OB + FVG overlap interval and ratio).
4. Causal Liquidity Map, Equal Highs/Lows, and Sweep Confirmation.
5. Dealing Range, Premium/Discount, Obstacle Assessment, and Market Regime.
6. Liquidity Hypothesis Contract (Primary & Alternative conditional scenarios).
7. Multi-Dimensional Zone Ranking & Human-Readable Vietnamese Explanation Drawer.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from sqlalchemy.orm import Session

import models
import smc_engine
from services.ny_quota_helpers import closed_candles_at
from services.scalp_m1_types import M1ScalpConfig, WatchedZone, ZoneState, get_scalp_config
from services.smc_liquidity_service import (
    build_liquidity_map,
    cluster_equal_levels,
    select_unconsumed_target,
    update_liquidity_states,
)
from services.smc_scenario_types import LiquidityLevel, make_evidence_id

logger = logging.getLogger(__name__)


# =====================================================================
# 1. DIRECTION NORMALIZATION & PRICE FORMATTING (SECTIONS 044, 045)
# =====================================================================

def normalize_direction(raw: str) -> str:
    """Normalize direction to BULLISH or BEARISH. Fails closed if unknown."""
    if not raw:
        raise ValueError("Direction cannot be empty")
    s = str(raw).strip().upper()
    if s in ("BULLISH", "LONG", "BUY"):
        return "BULLISH"
    elif s in ("BEARISH", "SHORT", "SELL"):
        return "BEARISH"
    raise ValueError(f"Unknown direction '{raw}', cannot default")


def round_price(symbol: str, price: float) -> float:
    """Normalizes price using symbol precision (2 decimals for XAUUSDT)."""
    if "XAU" in symbol.upper():
        return round(float(price), 2)
    return round(float(price), 4)


TIMEFRAME_DURATIONS_MS: Dict[str, int] = {
    "1M": 60_000,
    "5M": 300_000,
    "15M": 900_000,
    "1H": 3_600_000,
    "4H": 14_400_000,
    "D": 86_400_000,
}


def resolve_timeframe_duration_ms(timeframe: str) -> int:
    """
    Resolves timeframe string to millisecond duration (SECTIONS 023, 025).
    Fails closed on unsupported timeframes (no silent M1 fallback).
    """
    if not timeframe:
        raise ValueError("timeframe cannot be empty")
    tf_clean = str(timeframe).strip().upper()
    if tf_clean in TIMEFRAME_DURATIONS_MS:
        return TIMEFRAME_DURATIONS_MS[tf_clean]
    if tf_clean.endswith("M"):
        try:
            return int(tf_clean[:-1]) * 60_000
        except ValueError:
            pass
    elif tf_clean.endswith("H"):
        try:
            return int(tf_clean[:-1]) * 3_600_000
        except ValueError:
            pass
    raise ValueError(f"UNSUPPORTED_TIMEFRAME: cannot resolve duration for '{timeframe}'")


@dataclass(frozen=True)
class ConfirmedSwing:
    """
    Canonical confirmed swing high or swing low (SECTIONS 021 - 024).
    Contains strict causal timestamps:
    - occurred_at_ms: Open timestamp of the pivot candle.
    - known_at_ms: Close timestamp of the right-bars confirmation candle.
    Provides backward-compatibility properties for legacy consumers (timestamp, confirmed_at).
    """
    id: str
    symbol: str
    timeframe: str
    side: str                # BUY_SIDE (highs) or SELL_SIDE (lows)
    price: float
    occurred_at_ms: int
    known_at_ms: int
    source_ids: Tuple[str, ...] = ()
    detector_version: str = "v15.3"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "side": self.side,
            "type": "HIGH" if self.side == "BUY_SIDE" else "LOW",
            "price": self.price,
            "occurred_at_ms": self.occurred_at_ms,
            "known_at_ms": self.known_at_ms,
            "timestamp": self.occurred_at_ms,      # legacy compatibility
            "confirmed_at": self.known_at_ms,      # legacy compatibility
            "source_ids": list(self.source_ids),
            "detector_version": self.detector_version,
        }


def identify_confirmed_swings(
    candles: Sequence[Any],
    timeframe: str = "1M",
    left_bars: int = 2,
    right_bars: int = 2,
    *,
    symbol: str = "XAUUSDT",
    as_of_ms: int = 0,
    detector_version: str = "v15.3",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Causally identify swing highs and swing lows for any timeframe (SECTIONS 021 - 032).
    Confirmation strictly requires right_bars closed candles.
    known_at_ms = confirm_open + duration_ms (when right_bars confirmation bar closes).
    """
    if len(candles) < left_bars + right_bars + 1:
        return [], []

    duration_ms = resolve_timeframe_duration_ms(timeframe)
    highs: List[Dict[str, Any]] = []
    lows: List[Dict[str, Any]] = []
    n = len(candles)
    tf_lower = timeframe.lower()

    def _bar_high(c: Any) -> float:
        return float(c.get("high", 0.0) if isinstance(c, dict) else getattr(c, "high", 0.0))

    def _bar_low(c: Any) -> float:
        return float(c.get("low", 0.0) if isinstance(c, dict) else getattr(c, "low", 0.0))

    def _bar_open_ms(c: Any) -> int:
        if isinstance(c, dict):
            return int(c.get("open_time") or c.get("timestamp") or 0)
        return int(getattr(c, "open_time", None) or getattr(c, "timestamp", 0) or 0)

    for i in range(left_bars, n - right_bars):
        mid = candles[i]
        mid_high = _bar_high(mid)
        mid_low = _bar_low(mid)
        open_ms = _bar_open_ms(mid)

        # Confirmation candle is candles[i + right_bars]
        confirm_candle = candles[i + right_bars]
        confirm_open = _bar_open_ms(confirm_candle)
        known_at_ms = confirm_open + duration_ms
        if as_of_ms and known_at_ms > as_of_ms:
            continue

        # Swing High
        is_sh = True
        for j in range(i - left_bars, i + right_bars + 1):
            if j == i:
                continue
            bar_h = _bar_high(candles[j])
            if j < i and bar_h >= mid_high:
                is_sh = False
                break
            elif j > i and bar_h > mid_high:
                is_sh = False
                break
        if is_sh:
            sh_id = f"{tf_lower}_sh_{open_ms}"
            swing_obj = ConfirmedSwing(
                id=sh_id,
                symbol=symbol,
                timeframe=timeframe,
                side="BUY_SIDE",
                price=mid_high,
                occurred_at_ms=open_ms,
                known_at_ms=known_at_ms,
                source_ids=(f"bar_{open_ms}", f"bar_{confirm_open}"),
                detector_version=detector_version,
            )
            highs.append(swing_obj.to_dict())

        # Swing Low
        is_sl = True
        for j in range(i - left_bars, i + right_bars + 1):
            if j == i:
                continue
            bar_l = _bar_low(candles[j])
            if j < i and bar_l <= mid_low:
                is_sl = False
                break
            elif j > i and bar_l < mid_low:
                is_sl = False
                break
        if is_sl:
            sl_id = f"{tf_lower}_sl_{open_ms}"
            swing_obj = ConfirmedSwing(
                id=sl_id,
                symbol=symbol,
                timeframe=timeframe,
                side="SELL_SIDE",
                price=mid_low,
                occurred_at_ms=open_ms,
                known_at_ms=known_at_ms,
                source_ids=(f"bar_{open_ms}", f"bar_{confirm_open}"),
                detector_version=detector_version,
            )
            lows.append(swing_obj.to_dict())

    return highs, lows


# =====================================================================
# 2. CAUSAL ORDER BLOCK (OB) DETECTOR (SECTIONS 055 - 064)
# =====================================================================

@dataclass(frozen=True)
class OrderBlockCandidate:
    """Operational definition of an Order Block (not institutional proof)."""
    symbol: str
    timeframe: str
    direction: str          # BULLISH or BEARISH
    top: float
    bottom: float
    midpoint: float
    created_at_ms: int      # Base candle open time
    known_at_ms: int        # Displacement break bar close time (confirmation time)
    base_candle_id: str
    break_bar_id: str
    break_level: float
    invalidation_level: float
    displacement_atr: float
    bounds_policy: str      # FULL_CANDLE or BODY


def detect_order_blocks(
    candles: Sequence[Any],
    swing_pivots: Sequence[Dict[str, Any]],
    timeframe: str = "15M",
    symbol: str = "XAUUSDT",
    *,
    as_of_ms: int = 0,
    atr: Optional[float] = None,
    bounds_policy: str = "FULL_CANDLE",
    min_displacement_atr: float = 0.50,
) -> List[WatchedZone]:
    """
    Detects causal Order Blocks from closed candles and confirmed swings (PHẦN 055 - 064).
    Rules:
    - Bullish OB: Last bearish candle before a bullish displacement leg that causally breaks
      a confirmed swing high known before the break bar.
    - Bearish OB: Last bullish candle before a bearish displacement leg that causally breaks
      a confirmed swing low known before the break bar.
    - known_at_ms: Closed time of the break candle (NO hindsight).
    - Invalidation: Extreme of base candle (low for bullish, high for bearish).
    """
    if len(candles) < 4:
        return []

    calc_atr = atr or smc_engine.compute_atr(candles, period=14)
    if calc_atr <= 0:
        calc_atr = 1.0

    duration_ms = resolve_timeframe_duration_ms(timeframe)
    zones: List[WatchedZone] = []

    # Map confirmed swings
    swing_highs = [s for s in swing_pivots if s.get("side", s.get("type")) in ("BUY_SIDE", "HIGH", "SH")]
    swing_lows = [s for s in swing_pivots if s.get("side", s.get("type")) in ("SELL_SIDE", "LOW", "SL")]

    n = len(candles)
    for i in range(1, n):
        curr_bar = candles[i]
        curr_close = float(curr_bar.close)
        curr_open = float(curr_bar.open)
        curr_high = float(curr_bar.high)
        curr_low = float(curr_bar.low)
        curr_ts = int(getattr(curr_bar, "open_time", None) or getattr(curr_bar, "timestamp", 0))
        curr_confirmed_at = curr_ts + duration_ms

        if as_of_ms and curr_confirmed_at > as_of_ms:
            continue

        # Prefix ATR for causal displacement measurement (SECTIONS 045, 046, 061 - 065)
        prefix_atr = smc_engine.compute_atr(candles[:i + 1], period=14)
        if prefix_atr <= 0:
            prefix_atr = smc_engine.compute_atr(candles[:min(len(candles), 14)], period=min(len(candles), 14))
            if prefix_atr <= 0:
                prefix_atr = float(atr) if atr is not None and atr > 0 else calc_atr
            if prefix_atr <= 0:
                prefix_atr = 1.0

        # --- Check Bullish OB: Current bar broke a prior confirmed swing high ---
        prior_shs = [
            sh for sh in swing_highs
            if sh.get("confirmed_at", sh.get("known_at_ms", sh.get("timestamp", 0))) <= curr_ts
            and float(sh["price"]) < curr_close
            and float(candles[i - 1].close) <= float(sh["price"])
        ]
        if prior_shs:
            broken_sh = prior_shs[-1]
            break_lvl = float(broken_sh["price"])
            # Trace backward to find displacement leg and base candle
            base_idx = None
            for j in range(i, max(-1, i - 5), -1):
                bar_j = candles[j]
                if float(bar_j.close) < float(bar_j.open):  # Bearish opposing candle
                    base_idx = j
                    break

            if base_idx is not None:
                base_bar = candles[base_idx]
                disp_bars = candles[base_idx + 1: i + 1]
                if disp_bars:
                    leg_body = max(float(b.close) - float(b.open) for b in disp_bars)
                    if leg_body >= min_displacement_atr * prefix_atr:
                        base_low = float(base_bar.low)
                        base_high = float(base_bar.high)
                        base_open = float(base_bar.open)
                        base_close = float(base_bar.close)
                        base_ts = int(getattr(base_bar, "open_time", None) or getattr(base_bar, "timestamp", 0))

                        if bounds_policy == "BODY":
                            bot = min(base_open, base_close)
                            top = max(base_open, base_close)
                        else:
                            bot = base_low
                            top = base_high

                        if top > bot:
                            inv_level = round_price(symbol, base_low - (0.1 * prefix_atr))
                            zone_id = f"ob-{symbol}-{timeframe}-BULLISH-{base_ts}"
                            zones.append(WatchedZone(
                                zone_id=zone_id,
                                symbol=symbol,
                                timeframe=timeframe,
                                zone_type="OB",
                                direction="BULLISH",
                                top=round_price(symbol, top),
                                bottom=round_price(symbol, bot),
                                created_at=base_ts,
                                known_at=curr_confirmed_at,
                                source_evidence_ids=(f"base-{base_ts}", f"break-{curr_ts}"),
                                invalidation_level=inv_level,
                                model_version="v15.3",
                                state=ZoneState.WATCHING.value,
                                last_evaluated_at=as_of_ms or curr_confirmed_at,
                            ))

        # --- Check Bearish OB: Current bar broke a prior confirmed swing low ---
        prior_sls = [
            sl for sl in swing_lows
            if sl.get("confirmed_at", sl.get("known_at_ms", sl.get("timestamp", 0))) <= curr_ts
            and float(sl["price"]) > curr_close
            and float(candles[i - 1].close) >= float(sl["price"])
        ]
        if prior_sls:
            broken_sl = prior_sls[-1]
            break_lvl = float(broken_sl["price"])
            base_idx = None
            for j in range(i, max(-1, i - 5), -1):
                bar_j = candles[j]
                if float(bar_j.close) > float(bar_j.open):  # Bullish opposing candle
                    base_idx = j
                    break

            if base_idx is not None:
                base_bar = candles[base_idx]
                disp_bars = candles[base_idx + 1: i + 1]
                if disp_bars:
                    leg_body = max(float(b.open) - float(b.close) for b in disp_bars)
                    if leg_body >= min_displacement_atr * prefix_atr:
                        base_low = float(base_bar.low)
                        base_high = float(base_bar.high)
                        base_open = float(base_bar.open)
                        base_close = float(base_bar.close)
                        base_ts = int(getattr(base_bar, "open_time", None) or getattr(base_bar, "timestamp", 0))

                        if bounds_policy == "BODY":
                            bot = min(base_open, base_close)
                            top = max(base_open, base_close)
                        else:
                            bot = base_low
                            top = base_high

                        if top > bot:
                            inv_level = round_price(symbol, base_high + (0.1 * prefix_atr))
                            zone_id = f"ob-{symbol}-{timeframe}-BEARISH-{base_ts}"
                            zones.append(WatchedZone(
                                zone_id=zone_id,
                                symbol=symbol,
                                timeframe=timeframe,
                                zone_type="OB",
                                direction="BEARISH",
                                top=round_price(symbol, top),
                                bottom=round_price(symbol, bot),
                                created_at=base_ts,
                                known_at=curr_confirmed_at,
                                source_evidence_ids=(f"base-{base_ts}", f"break-{curr_ts}"),
                                invalidation_level=inv_level,
                                model_version="v15.3",
                                state=ZoneState.WATCHING.value,
                                last_evaluated_at=as_of_ms or curr_confirmed_at,
                            ))

    return zones


# =====================================================================
# 3. ZONE CONFLUENCE & OVERLAP GEOMETRY (SECTIONS 065 - 067)
# =====================================================================

@dataclass(frozen=True)
class ZoneConfluence:
    has_confluence: bool
    overlap_interval: Optional[Tuple[float, float]] = None
    overlap_ratio: float = 0.0
    zone_ids: Tuple[str, ...] = ()
    description_vi: str = ""


def evaluate_zone_confluence(zone_a: WatchedZone, zone_b: WatchedZone) -> ZoneConfluence:
    """
    Evaluates geometric overlap between two zones (e.g. OB and FVG).
    Adjacent boundaries (touching without interval) is NOT overlapping.
    Does not inflate score or invent fake win probabilities.
    """
    if zone_a.direction != zone_b.direction or zone_a.symbol != zone_b.symbol:
        return ZoneConfluence(has_confluence=False)

    overlap_bot = max(zone_a.bottom, zone_b.bottom)
    overlap_top = min(zone_a.top, zone_b.top)

    if overlap_top <= overlap_bot:
        return ZoneConfluence(has_confluence=False)

    overlap_height = overlap_top - overlap_bot
    min_zone_height = min(zone_a.top - zone_a.bottom, zone_b.top - zone_b.bottom)
    ratio = round(overlap_height / min_zone_height, 3) if min_zone_height > 0 else 0.0

    desc = f"Hội tụ {zone_a.zone_type} & {zone_b.zone_type} tại [{overlap_bot:.2f} - {overlap_top:.2f}] (tỷ lệ {ratio * 100:.0f}%)"
    return ZoneConfluence(
        has_confluence=True,
        overlap_interval=(round(overlap_bot, 2), round(overlap_top, 2)),
        overlap_ratio=ratio,
        zone_ids=(zone_a.zone_id, zone_b.zone_id),
        description_vi=desc,
    )


# =====================================================================
# 4. SWEEP CONFIRMATION & LIQUIDITY LEVELS (SECTIONS 071 - 080)
# =====================================================================

@dataclass(frozen=True)
class SweepConfirmation:
    is_confirmed: bool
    level_id: str
    breach_price: float
    reclaim_price: float
    occurred_at_ms: int
    known_at_ms: int
    candle_id: str
    rejection_ratio: float


def confirm_liquidity_sweep(
    level: LiquidityLevel,
    candles: Sequence[Any],
    now_ms: int,
    candle_duration_ms: int = 60_000,
) -> Optional[SweepConfirmation]:
    """
    Confirms genuine sweep with breach + reclaim (PHẦN 078, 079, 080).
    - Sell-side (low): Wick breaches below level, candle close reclaims >= level.
    - Buy-side (high): Wick breaches above level, candle close reclaims <= level.
    Touch without breach is NOT a sweep.
    Breach without reclaim is BREAKOUT, not reversal sweep.
    """
    subsequent_bars = [
        c for c in candles
        if int(getattr(c, "open_time", None) or getattr(c, "timestamp", 0)) >= level.known_at_ms
        and int(getattr(c, "open_time", None) or getattr(c, "timestamp", 0)) + candle_duration_ms <= now_ms
    ]

    for bar in subsequent_bars:
        b_low = float(bar.low)
        b_high = float(bar.high)
        b_close = float(bar.close)
        b_open = float(bar.open)
        b_ts = int(getattr(bar, "open_time", None) or getattr(bar, "timestamp", 0))
        b_known = b_ts + candle_duration_ms

        if level.side == "SELL_SIDE":
            if b_low < level.price and b_close >= level.price:
                # Reclaim confirmed! Calculate lower wick rejection ratio
                body_bottom = min(b_open, b_close)
                total_range = max(0.01, b_high - b_low)
                wick_size = body_bottom - b_low
                rej_ratio = round(wick_size / total_range, 2)
                return SweepConfirmation(
                    is_confirmed=True,
                    level_id=level.id,
                    breach_price=b_low,
                    reclaim_price=b_close,
                    occurred_at_ms=b_ts,
                    known_at_ms=b_known,
                    candle_id=f"c_{b_ts}",
                    rejection_ratio=rej_ratio,
                )

        elif level.side == "BUY_SIDE":
            if b_high > level.price and b_close <= level.price:
                # Reclaim confirmed! Calculate upper wick rejection ratio
                body_top = max(b_open, b_close)
                total_range = max(0.01, b_high - b_low)
                wick_size = b_high - body_top
                rej_ratio = round(wick_size / total_range, 2)
                return SweepConfirmation(
                    is_confirmed=True,
                    level_id=level.id,
                    breach_price=b_high,
                    reclaim_price=b_close,
                    occurred_at_ms=b_ts,
                    known_at_ms=b_known,
                    candle_id=f"c_{b_ts}",
                    rejection_ratio=rej_ratio,
                )

    return None


# =====================================================================
# 5. DEALING RANGE & PREMIUM / DISCOUNT (SECTIONS 083 - 085)
# =====================================================================

@dataclass(frozen=True)
class DealingRange:
    range_high: float
    range_low: float
    equilibrium: float
    range_id: str
    known_at_ms: int
    is_valid: bool


def compute_dealing_range(
    swings: Sequence[Dict[str, Any]],
    now_ms: int,
    symbol: str = "XAUUSDT",
) -> DealingRange:
    """Computes active dealing range [low, high] and 50% equilibrium from confirmed swings."""
    valid_highs = [
        s for s in swings
        if s.get("side", s.get("type")) in ("BUY_SIDE", "HIGH", "SH")
        and s.get("known_at_ms", s.get("confirmed_at", 0)) <= now_ms
    ]
    valid_lows = [
        s for s in swings
        if s.get("side", s.get("type")) in ("SELL_SIDE", "LOW", "SL")
        and s.get("known_at_ms", s.get("confirmed_at", 0)) <= now_ms
    ]

    if not valid_highs or not valid_lows:
        return DealingRange(0.0, 0.0, 0.0, "UNKNOWN", now_ms, False)

    recent_h = max(valid_highs, key=lambda s: s.get("known_at_ms", s.get("confirmed_at", 0)))
    recent_l = max(valid_lows, key=lambda s: s.get("known_at_ms", s.get("confirmed_at", 0)))

    rh = float(recent_h["price"])
    rl = float(recent_l["price"])

    if rh <= rl:
        return DealingRange(0.0, 0.0, 0.0, "INVALID_GEOMETRY", now_ms, False)

    eq = round_price(symbol, (rh + rl) / 2.0)
    range_id = f"range-{recent_l.get('id', rl)}-{recent_h.get('id', rh)}"
    max_known = max(
        recent_h.get("known_at_ms", recent_h.get("confirmed_at", 0)),
        recent_l.get("known_at_ms", recent_l.get("confirmed_at", 0)),
    )

    return DealingRange(
        range_high=rh,
        range_low=rl,
        equilibrium=eq,
        range_id=range_id,
        known_at_ms=max_known,
        is_valid=True,
    )


def assess_range_position(price: float, dealing_range: DealingRange) -> str:
    """Returns DISCOUNT (0-50%), EQUILIBRIUM (45-55%), or PREMIUM (50-100%)."""
    if not dealing_range.is_valid:
        return "UNKNOWN"
    total_range = dealing_range.range_high - dealing_range.range_low
    if total_range <= 0:
        return "UNKNOWN"

    ratio = (price - dealing_range.range_low) / total_range
    if 0.45 <= ratio <= 0.55:
        return "EQUILIBRIUM"
    elif ratio < 0.45:
        return "DISCOUNT"
    else:
        return "PREMIUM"


# =====================================================================
# 6. INTERVENING OBSTACLES & MARKET REGIME (SECTIONS 082, 086)
# =====================================================================

def find_intervening_obstacles(
    levels: Sequence[LiquidityLevel],
    zones: Sequence[WatchedZone],
    direction: str,
    entry_price: float,
    target_price: float,
) -> List[Dict[str, Any]]:
    """Checks for opposing active zones or liquidity pools situated between entry and target."""
    obstacles: List[Dict[str, Any]] = []

    if direction == "LONG":
        low_bound = min(entry_price, target_price)
        high_bound = max(entry_price, target_price)
        # Opposing bearish zones
        for z in zones:
            if z.direction in ("BEARISH", "SHORT") and z.state != ZoneState.INVALIDATED.value:
                if low_bound < z.bottom < high_bound:
                    obstacles.append({
                        "id": z.zone_id,
                        "type": z.zone_type,
                        "price": z.bottom,
                        "distance_price": round(z.bottom - entry_price, 2),
                        "label_vi": f"Vùng cản {z.zone_type} giảm tại {z.bottom:.2f}",
                    })
    elif direction == "SHORT":
        low_bound = min(entry_price, target_price)
        high_bound = max(entry_price, target_price)
        # Opposing bullish zones
        for z in zones:
            if z.direction in ("BULLISH", "LONG") and z.state != ZoneState.INVALIDATED.value:
                if low_bound < z.top < high_bound:
                    obstacles.append({
                        "id": z.zone_id,
                        "type": z.zone_type,
                        "price": z.top,
                        "distance_price": round(entry_price - z.top, 2),
                        "label_vi": f"Vùng hỗ trợ {z.zone_type} tăng tại {z.top:.2f}",
                    })

    return sorted(obstacles, key=lambda o: o["distance_price"])


def classify_market_regime(
    candles: Sequence[Any],
    atr: float,
    now_ms: int,
) -> str:
    """Classifies regime into TREND, RANGE, EXPANSION, or UNCERTAIN."""
    if len(candles) < 10:
        return "UNCERTAIN"

    recent_10 = candles[-10:]
    highs = [float(c.high) for c in recent_10]
    lows = [float(c.low) for c in recent_10]
    overall_range = max(highs) - min(lows)

    last_body = abs(float(recent_10[-1].close) - float(recent_10[-1].open))
    if last_body > 2.5 * atr:
        return "EXPANSION"

    # Swing slope
    if overall_range < 2.0 * atr:
        return "RANGE"

    first_half_avg = sum(float(c.close) for c in recent_10[:5]) / 5.0
    second_half_avg = sum(float(c.close) for c in recent_10[5:]) / 5.0

    if second_half_avg - first_half_avg > 1.0 * atr:
        return "TREND_BULLISH"
    elif first_half_avg - second_half_avg > 1.0 * atr:
        return "TREND_BEARISH"

    return "RANGE"


# =====================================================================
# 7. LIQUIDITY HYPOTHESIS CONTRACT (SECTIONS 091 - 096)
# =====================================================================

@dataclass
class LiquidityHypothesis:
    """
    Causal scenario hypothesis: conditional route with target and invalidation.
    Never guarantees market direction (PHẦN 009, 091 - 096).
    """
    hypothesis_id: str
    direction: str                                  # BULLISH / BEARISH
    is_primary: bool                                # True = primary, False = alternative
    watched_zone: WatchedZone
    target_level: Optional[LiquidityLevel]
    target_price: Optional[float]                   # None if no unconsumed liquidity level is available
    dealing_range: DealingRange
    range_position: str                             # DISCOUNT / PREMIUM / EQUILIBRIUM
    prerequisites: List[str]
    invalidation_condition: str
    expiry_ms: int
    created_at_ms: int
    status: str = "ACTIVE"                          # ACTIVE, INVALIDATED, FULFILLED, EXPIRED
    revision: int = 1
    evidence_ids: List[str] = field(default_factory=list)
    explanation_vi: str = ""
    target_type: str = "LIQUIDITY_LEVEL"            # LIQUIDITY_LEVEL or UNAVAILABLE
    target_status: str = "CONFIRMED"                # CONFIRMED or TARGET_UNAVAILABLE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "direction": self.direction,
            "is_primary": self.is_primary,
            "zone_id": self.watched_zone.zone_id,
            "zone_type": self.watched_zone.zone_type,
            "zone_bounds": [self.watched_zone.bottom, self.watched_zone.top],
            "target_price": self.target_price,
            "target_level_id": self.target_level.id if self.target_level else None,
            "target_label": self.target_level.label_vi if self.target_level else ("Mục tiêu cấu trúc" if self.target_price is not None else "Chưa có mục tiêu"),
            "target_type": self.target_type,
            "target_status": self.target_status,
            "range_position": self.range_position,
            "prerequisites": self.prerequisites,
            "invalidation_condition": self.invalidation_condition,
            "expiry_ms": self.expiry_ms,
            "status": self.status,
            "revision": self.revision,
            "explanation_vi": self.explanation_vi,
        }


class HypothesisLoadStatus(str, Enum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    CORRUPT = "CORRUPT"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"


class LifecycleStageResult(str, Enum):
    """Result of staging durable hypothesis lifecycle or status transition (SECTIONS 034, 127)."""
    STAGED = "STAGED"
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    NO_CHANGE = "NO_CHANGE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


def is_lifecycle_stage_success(result: Any) -> bool:
    """Pure helper to check whether a LifecycleStageResult grants stage authority (SECTIONS 036, 049).
    Returns True ONLY for CREATED, UPDATED, NO_CHANGE, STAGED.
    Returns False for BLOCKED, FAILED, None, or any unrecognized value.
    """
    if result is None:
        return False
    return result in (
        LifecycleStageResult.CREATED,
        LifecycleStageResult.UPDATED,
        LifecycleStageResult.NO_CHANGE,
        LifecycleStageResult.STAGED,
    )


@dataclass(frozen=True)
class HypothesisWriteOutcome:
    """Outcome of staging/persisting a single hypothesis lifecycle record in a cycle (SECTIONS 043 - 047)."""
    hypothesis_id: str
    storage_key: str
    zone_id: Optional[str]
    operation: str                           # "UPSERT_HYPOTHESIS" or "EXPIRE_DURABLE"
    result: LifecycleStageResult
    is_success: bool
    reason_code: Optional[str] = None
    diagnostics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LifecycleCycleAuthority:
    """Cycle-level authority summary for hypothesis persistence and entry gating (SECTIONS 043, 044, 126)."""
    write_outcomes: Dict[str, HypothesisWriteOutcome] = field(default_factory=dict)
    zone_outcomes: Dict[str, HypothesisWriteOutcome] = field(default_factory=dict)
    cluster_outcomes: Dict[str, Any] = field(default_factory=dict)
    entry_authorized_hypotheses: List[Any] = field(default_factory=list)
    has_write_failures: bool = False
    has_write_blocks: bool = False
    write_status: str = "OK"                  # "OK", "BLOCKED", "FAILED"
    write_issues_count: int = 0


def derive_cycle_status(
    read_status: str,
    write_status: str,
    commit_ok: bool,
    has_failed_write: bool,
    has_blocked_write: bool,
) -> str:
    """
    Deterministic resolution of overall cycle status (SECTIONS 126 - 130).
    Priority:
    1. If commit failed or read failed or any write failed -> DEGRADED
    2. If write blocked or read partial -> PARTIAL
    3. If read COMPLETE, write OK, commit OK -> OK
    """
    if not commit_ok or read_status == "READ_FAILED" or has_failed_write or write_status == "FAILED":
        return "DEGRADED"
    if has_blocked_write or write_status == "BLOCKED" or read_status == "PARTIAL":
        return "PARTIAL"
    return "OK"


def check_candidate_lifecycle_authority(
    candidate: Any,
    authority_context: Optional[Any] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Validates lifecycle authority for a candidate before arming (SECTIONS 088 - 093, V16 Rules 085-088).
    Returns: (is_allowed, reason_code_if_blocked)
    """
    model_id = getattr(candidate, "model_id", None)
    if not model_id:
        return False, "MODEL_UNREGISTERED"

    from services.smc_model_registry import get_model_spec
    spec = get_model_spec(model_id)
    if spec is None:
        return False, "MODEL_UNREGISTERED"

    # Direct models do not require zone write authority
    if spec.authority_type == "DIRECT":
        return True, None

    if spec.authority_type == "CG01_LIFECYCLE":
        zone_id = getattr(candidate, "zone_id", None)
        origin_episode_id = getattr(candidate, "origin_episode_id", None)
        if not zone_id or not origin_episode_id:
            return False, "CG01_AUTHORITY_MISSING_ZONE_OR_EPISODE"
        if authority_context is not None:
            if hasattr(authority_context, "has_write_failures") and authority_context.has_write_failures:
                return False, "HYPOTHESIS_WRITE_FAILED"
            zone_outcomes = getattr(authority_context, "zone_outcomes", {})
            if zone_outcomes and zone_id in zone_outcomes:
                outcome = zone_outcomes[zone_id]
                if hasattr(outcome, "is_success") and not outcome.is_success:
                    return False, getattr(outcome, "reason_code", None) or "HYPOTHESIS_WRITE_BLOCKED"
        return True, None

    if authority_context is None:
        return False, "HYPOTHESIS_AUTHORITY_MISSING"

    if hasattr(authority_context, "has_write_failures") and authority_context.has_write_failures:
        return False, "HYPOTHESIS_WRITE_FAILED"

    if spec.authority_type == "CLUSTER_LIFECYCLE":
        cluster_id = getattr(candidate, "cluster_id", None)
        if not cluster_id and getattr(candidate, "evidence", None):
            for ev in candidate.evidence:
                if ev.get("kind") == "LIQUIDITY_CLUSTER":
                    cluster_id = ev.get("extra", {}).get("cluster_id") or ev.get("id", "").replace("ev_cluster_", "")
                    break
        if not cluster_id and getattr(candidate, "candidate_key", None):
            parts = candidate.candidate_key.split("_")
            if len(parts) >= 4:
                cluster_id = parts[3]

        cluster_outcomes = getattr(authority_context, "cluster_outcomes", {})
        if not cluster_outcomes:
            return False, "CLUSTER_AUTHORITY_MISSING"

        cluster_obj = cluster_outcomes.get(cluster_id)
        if cluster_obj is None:
            cluster_obj = next((c for cid, c in cluster_outcomes.items() if cid == cluster_id or getattr(c, "id", "") == cluster_id), None)

        if cluster_obj is None:
            return False, "CLUSTER_NOT_IN_AUTHORITY"

        c_state = getattr(cluster_obj, "state", "")
        if c_state not in ("SWEPT", "RECLAIMED"):
            return False, f"CLUSTER_STATE_UNAUTHORIZED_{c_state}"

        return True, None

    zone_id = getattr(candidate, "zone_id", None)
    hyp_id = getattr(candidate, "hypothesis_id", None)

    zone_outcomes = getattr(authority_context, "zone_outcomes", {})
    write_outcomes = getattr(authority_context, "write_outcomes", {})

    outcome = None
    if zone_id and zone_id in zone_outcomes:
        outcome = zone_outcomes[zone_id]
    elif hyp_id and hyp_id in write_outcomes:
        outcome = write_outcomes[hyp_id]

    if outcome is None:
        return False, "HYPOTHESIS_AUTHORITY_MISSING"

    if not outcome.is_success:
        return False, outcome.reason_code or "HYPOTHESIS_WRITE_BLOCKED"

    return True, None


@dataclass(frozen=True)
class DurableHypothesisRecord:
    """
    Durable persisted anchor for a liquidity hypothesis lifecycle (SECTIONS 021 - 040).
    Immutable anchor fields preserve original creation and expiry authority across restarts.
    """
    schema_version: int
    hypothesis_id: str
    symbol: str
    direction: str
    is_primary: bool
    created_at_ms: int
    expiry_ms: int
    status: str
    revision: int
    source_zone_id: Optional[str] = None
    target_price: Optional[float] = None
    target_level_id: Optional[str] = None
    target_type: str = "LIQUIDITY_LEVEL"
    target_status: str = "CONFIRMED"
    source_fingerprint: str = ""
    updated_at_ms: int = 0


@dataclass(frozen=True)
class HypothesisLoadIssue:
    """Diagnostic detail for a corrupt, unsupported, or failed durable hypothesis record (SECTIONS 064 - 066)."""
    storage_key: str
    status: HypothesisLoadStatus
    reason: str
    hypothesis_id: Optional[str] = None
    schema_version: Optional[Any] = None
    raw_preview: str = ""


@dataclass
class HypothesisBatchLoadResult:
    """
    Structured result of batch loading durable hypotheses for a symbol (SECTIONS 061 - 087).
    Distinguishes COMPLETE, PARTIAL, and READ_FAILED statuses.
    Tracks blocked keys and identities to prevent recreation or blind overwriting of corrupt state.
    Implements dict-like helper methods for seamless backward compatibility.
    """
    records: Dict[str, DurableHypothesisRecord]
    issues: List[HypothesisLoadIssue]
    read_status: str  # "COMPLETE", "PARTIAL", "READ_FAILED"
    blocked_keys: Set[str]
    blocked_identities: Set[str]
    diagnostics: Dict[str, Any]

    def get(self, key: str, default: Any = None) -> Any:
        return self.records.get(key, default)

    def items(self):
        return self.records.items()

    def values(self):
        return self.records.values()

    def keys(self):
        return self.records.keys()

    def __iter__(self):
        return iter(self.records)

    def __getitem__(self, item: str) -> DurableHypothesisRecord:
        return self.records[item]

    def __contains__(self, item: str) -> bool:
        return item in self.records

    def __len__(self) -> int:
        return len(self.records)

    def __bool__(self) -> bool:
        return True


def compute_hypothesis_source_fingerprint(
    hypothesis_id: str,
    symbol: str,
    direction: str,
    source_zone_id: Optional[str],
    created_at_ms: int,
) -> str:
    """
    Deterministic SHA-256 fingerprint from immutable identity and context anchors (SECTIONS 035, 073).
    Does NOT include current quotes, transient volatility, or evaluation time.
    """
    anchor_str = f"{hypothesis_id}|{symbol.upper()}|{direction.upper()}|{source_zone_id or ''}|{int(created_at_ms)}"
    return hashlib.sha256(anchor_str.encode("utf-8")).hexdigest()


def get_hypothesis_storage_key(symbol: str, hypothesis_id: str) -> str:
    """
    Computes a bounded <=50 char storage key for SystemConfig (SECTIONS 026, 027).
    Scheme: 'hlc:' + symbol[:7] + ':' + 24-char SHA-256 hex. Length <= 36 chars.
    """
    sym = (symbol or "UNKNOWN")[:7].upper()
    hyp_hash = hashlib.sha256(hypothesis_id.encode("utf-8")).hexdigest()[:24]
    return f"hlc:{sym}:{hyp_hash}"


def parse_hypothesis_lifecycle_row(
    key: str,
    raw_value: Any,
    expected_symbol: str,
) -> Tuple[HypothesisLoadStatus, Optional[DurableHypothesisRecord], Optional[str], str]:
    """
    Pure validation parser for a persisted SystemConfig row (SECTIONS 070 - 084).
    DOES NOT MUTATE DB OR SESSION.
    Returns: (status, record_or_none, hypothesis_id_if_known, reason_str)
    """
    if not isinstance(raw_value, str) or not raw_value.strip():
        return HypothesisLoadStatus.CORRUPT, None, None, "Empty or non-string raw payload"

    try:
        data = json.loads(raw_value)
    except Exception as e:
        return HypothesisLoadStatus.CORRUPT, None, None, f"Corrupt JSON payload: {e}"

    if not isinstance(data, dict):
        return HypothesisLoadStatus.CORRUPT, None, None, "Payload is not a JSON object"

    stored_id = data.get("hypothesis_id")
    hyp_id = stored_id if isinstance(stored_id, str) and stored_id.strip() else None

    schema_ver = data.get("schema_version")
    if schema_ver != 1:
        return HypothesisLoadStatus.UNSUPPORTED_VERSION, None, hyp_id, f"Unsupported schema version: {schema_ver}"

    if not hyp_id:
        return HypothesisLoadStatus.CORRUPT, None, None, "Missing or non-string hypothesis_id in payload"

    expected_key = get_hypothesis_storage_key(expected_symbol, hyp_id)
    if key != expected_key:
        return (
            HypothesisLoadStatus.CORRUPT,
            None,
            hyp_id,
            f"Storage key mismatch: row key '{key}' != derived key '{expected_key}'",
        )

    rec_symbol = data.get("symbol")
    if not rec_symbol or str(rec_symbol).upper() != expected_symbol.upper():
        return (
            HypothesisLoadStatus.CORRUPT,
            None,
            hyp_id,
            f"Symbol mismatch: payload symbol '{rec_symbol}' != requested '{expected_symbol}'",
        )

    c_at = data.get("created_at_ms")
    e_at = data.get("expiry_ms")
    rev = data.get("revision")
    stat = data.get("status")

    # Strict integer checks (Section 075: reject bool masquerading as int)
    if type(c_at) is not int or type(e_at) is not int or type(rev) is not int:
        return HypothesisLoadStatus.CORRUPT, None, hyp_id, "created_at_ms, expiry_ms, and revision must be strict int"

    if not (math.isfinite(c_at) and math.isfinite(e_at)):
        return HypothesisLoadStatus.CORRUPT, None, hyp_id, "Non-finite timestamps"

    if c_at <= 0 or e_at < c_at or rev < 1:
        return HypothesisLoadStatus.CORRUPT, None, hyp_id, f"Invalid timestamps or revision: c_at={c_at}, e_at={e_at}, rev={rev}"

    if stat not in ("ACTIVE", "EXPIRED", "INVALIDATED", "FULFILLED"):
        return HypothesisLoadStatus.CORRUPT, None, hyp_id, f"Invalid status: '{stat}'"

    target_price = data.get("target_price")
    if target_price is not None:
        if not isinstance(target_price, (int, float)) or isinstance(target_price, bool) or not math.isfinite(target_price):
            return HypothesisLoadStatus.CORRUPT, None, hyp_id, f"Invalid target_price: {target_price}"

    updated_at_ms = data.get("updated_at_ms", c_at)
    if type(updated_at_ms) is not int or isinstance(updated_at_ms, bool) or not math.isfinite(updated_at_ms) or updated_at_ms < c_at:
        return HypothesisLoadStatus.CORRUPT, None, hyp_id, f"Invalid updated_at_ms: {updated_at_ms}"

    rec_dir = str(data.get("direction", ""))
    rec_zone_id = data.get("zone_id")
    expected_fp = compute_hypothesis_source_fingerprint(
        hypothesis_id=hyp_id,
        symbol=expected_symbol,
        direction=rec_dir,
        source_zone_id=rec_zone_id,
        created_at_ms=c_at,
    )
    if data.get("source_fingerprint") != expected_fp:
        return HypothesisLoadStatus.CORRUPT, None, hyp_id, "Source fingerprint mismatch"

    record = DurableHypothesisRecord(
        schema_version=schema_ver,
        hypothesis_id=hyp_id,
        symbol=expected_symbol.upper(),
        direction=rec_dir,
        is_primary=bool(data.get("is_primary", True)),
        created_at_ms=c_at,
        expiry_ms=e_at,
        status=stat,
        revision=rev,
        source_zone_id=rec_zone_id,
        target_price=float(target_price) if target_price is not None else None,
        target_level_id=data.get("target_level_id"),
        target_type=str(data.get("target_type", "LIQUIDITY_LEVEL")),
        target_status=str(data.get("target_status", "CONFIRMED")),
        source_fingerprint=expected_fp,
        updated_at_ms=updated_at_ms,
    )
    return HypothesisLoadStatus.FOUND, record, hyp_id, "OK"


def load_hypothesis_lifecycle(
    db: Session,
    hypothesis_id: str,
    symbol: str = "XAUUSDT",
) -> Tuple[HypothesisLoadStatus, Optional[DurableHypothesisRecord]]:
    """
    Loads and validates a single durable hypothesis lifecycle from SystemConfig (SECTIONS 031 - 035).
    Pure reader: DOES NOT COMMIT OR CREATE RECORDS.
    Distinguishes FOUND, NOT_FOUND, CORRUPT, UNSUPPORTED_VERSION.
    """
    if not hypothesis_id:
        return HypothesisLoadStatus.NOT_FOUND, None

    key = get_hypothesis_storage_key(symbol, hypothesis_id)
    try:
        rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    except Exception as e:
        logger.warning(f"Error querying hypothesis lifecycle key '{key}': {e}")
        return HypothesisLoadStatus.CORRUPT, None

    if rec is None:
        return HypothesisLoadStatus.NOT_FOUND, None

    status, record, _, reason = parse_hypothesis_lifecycle_row(key, rec.value, symbol)
    if status != HypothesisLoadStatus.FOUND:
        logger.warning(f"Hypothesis lifecycle load failed for '{hypothesis_id}': {reason}")
        return status, None

    return HypothesisLoadStatus.FOUND, record


def load_durable_hypotheses_for_symbol(
    db: Session,
    symbol: str,
) -> HypothesisBatchLoadResult:
    """
    Batch loads valid durable hypothesis lifecycles for a given symbol (SECTIONS 042, 061 - 087).
    Bounds query using key prefix 'hlc:{symbol}:%'.
    Fails closed on corrupt entries without throwing unexpected exceptions.
    Returns structured HypothesisBatchLoadResult with records, issues, and blocked keys/identities.
    """
    sym = (symbol or "UNKNOWN")[:7].upper()
    prefix = f"hlc:{sym}:"
    try:
        records = db.query(models.SystemConfig).filter(models.SystemConfig.key.like(f"{prefix}%")).all()
    except Exception as e:
        logger.warning(f"Error querying durable hypotheses for {symbol}: {e}")
        issue = HypothesisLoadIssue(
            storage_key=prefix,
            status=HypothesisLoadStatus.CORRUPT,
            reason=f"Database query error: {e}",
            hypothesis_id=None,
        )
        return HypothesisBatchLoadResult(
            records={},
            issues=[issue],
            read_status="READ_FAILED",
            blocked_keys={prefix},
            blocked_identities=set(),
            diagnostics={"error": str(e)},
        )

    loaded: Dict[str, DurableHypothesisRecord] = {}
    issues: List[HypothesisLoadIssue] = []
    blocked_keys: Set[str] = set()
    blocked_identities: Set[str] = set()

    for r in records:
        status, d_rec, hyp_id, reason = parse_hypothesis_lifecycle_row(r.key, r.value, symbol)
        if status == HypothesisLoadStatus.FOUND and d_rec:
            loaded[d_rec.hypothesis_id] = d_rec
        else:
            issue = HypothesisLoadIssue(
                storage_key=r.key,
                status=status,
                reason=reason,
                hypothesis_id=hyp_id,
                raw_preview=str(r.value)[:100] if r.value else "",
            )
            issues.append(issue)
            blocked_keys.add(r.key)
            if hyp_id:
                blocked_identities.add(hyp_id)

    read_status = "COMPLETE" if not issues else "PARTIAL"
    return HypothesisBatchLoadResult(
        records=loaded,
        issues=issues,
        read_status=read_status,
        blocked_keys=blocked_keys,
        blocked_identities=blocked_identities,
        diagnostics={"total_rows": len(records), "valid_count": len(loaded), "issues_count": len(issues)},
    )


def stage_hypothesis_status_transition(
    db: Session,
    symbol: str,
    record_or_identity: Union[DurableHypothesisRecord, str],
    desired_status: str,
    now_ms: int,
) -> LifecycleStageResult:
    """
    Directly stages a status transition for a durable hypothesis record without synthetic zones (SECTIONS 031 - 047).
    Pure stage-only: caller owns transaction commit/rollback.
    Preconditions:
    - Only ACTIVE records can transition to EXPIRED when now_ms >= expiry_ms.
    - Terminal states (EXPIRED, INVALIDATED, FULFILLED) cannot transition back to ACTIVE.
    - Preserves all immutable anchors (created_at_ms, expiry_ms, source_fingerprint, revision).
    """
    hyp_id = record_or_identity.hypothesis_id if isinstance(record_or_identity, DurableHypothesisRecord) else str(record_or_identity)
    if not hyp_id:
        return LifecycleStageResult.BLOCKED

    key = get_hypothesis_storage_key(symbol, hyp_id)
    try:
        rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    except Exception as e:
        logger.warning(f"Error checking hypothesis key '{key}' for status transition: {e}")
        return LifecycleStageResult.FAILED

    if rec is None:
        return LifecycleStageResult.BLOCKED

    status, d_rec, parsed_id, reason = parse_hypothesis_lifecycle_row(key, rec.value, symbol)
    if status != HypothesisLoadStatus.FOUND or not d_rec:
        logger.warning(f"Refusing status transition on corrupt or invalid record '{key}': {reason}")
        return LifecycleStageResult.BLOCKED

    if d_rec.status == desired_status:
        return LifecycleStageResult.NO_CHANGE

    # Terminal monotonicity
    if d_rec.status in ("EXPIRED", "INVALIDATED", "FULFILLED") and desired_status == "ACTIVE":
        logger.warning(f"Refusing to transition terminal status '{d_rec.status}' to ACTIVE for '{hyp_id}'")
        return LifecycleStageResult.BLOCKED

    # Expiry precondition
    if desired_status == "EXPIRED" and now_ms < d_rec.expiry_ms:
        return LifecycleStageResult.NO_CHANGE

    new_rev = d_rec.revision + 1 if desired_status != d_rec.status else d_rec.revision
    payload = {
        "schema_version": d_rec.schema_version,
        "hypothesis_id": d_rec.hypothesis_id,
        "symbol": d_rec.symbol,
        "direction": d_rec.direction,
        "is_primary": bool(d_rec.is_primary),
        "zone_id": d_rec.source_zone_id,
        "created_at_ms": int(d_rec.created_at_ms),
        "expiry_ms": int(d_rec.expiry_ms),
        "status": desired_status,
        "revision": int(new_rev),
        "target_price": float(d_rec.target_price) if d_rec.target_price is not None else None,
        "target_level_id": d_rec.target_level_id,
        "target_type": str(d_rec.target_type),
        "target_status": str(d_rec.target_status),
        "source_fingerprint": d_rec.source_fingerprint,
        "updated_at_ms": int(now_ms),
    }
    rec.value = json.dumps(payload, ensure_ascii=False)
    rec.updated_at = int(now_ms)
    db.merge(rec)
    return LifecycleStageResult.STAGED


def stage_hypothesis_lifecycle(
    db: Session,
    symbol: str,
    hypothesis: LiquidityHypothesis,
    now_ms: int,
) -> LifecycleStageResult:
    """
    Stages durable hypothesis lifecycle to SystemConfig with write-side defense (SECTIONS 121 - 140).
    Stage-only: DOES NOT COMMIT OR ROLLBACK. Caller transaction owns commit/rollback.
    Does NOT blind merge over corrupt records. Preserves existing anchors.
    """
    if not hypothesis or not hypothesis.hypothesis_id:
        return LifecycleStageResult.BLOCKED

    key = get_hypothesis_storage_key(symbol, hypothesis.hypothesis_id)
    zone_id = hypothesis.watched_zone.zone_id if hypothesis.watched_zone else None

    try:
        existing_rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    except Exception as e:
        logger.warning(f"Error querying key '{key}' during stage_hypothesis_lifecycle: {e}")
        return LifecycleStageResult.FAILED

    # If key exists, validate it first (double defense)
    if existing_rec is not None:
        status, d_rec, parsed_id, reason = parse_hypothesis_lifecycle_row(key, existing_rec.value, symbol)
        if status != HypothesisLoadStatus.FOUND or not d_rec:
            # Corrupt, unsupported or invalid: REFUSE TO OVERWRITE RAW RECORD!
            logger.warning(f"Write defense blocked stage_hypothesis_lifecycle on corrupt key '{key}': {reason}")
            return LifecycleStageResult.BLOCKED

        # Terminal state cannot be downgraded to ACTIVE
        if d_rec.status in ("EXPIRED", "INVALIDATED", "FULFILLED") and hypothesis.status == "ACTIVE":
            logger.warning(f"Write defense blocked downgrading terminal status '{d_rec.status}' to ACTIVE for '{hypothesis.hypothesis_id}'")
            return LifecycleStageResult.BLOCKED

        target_lvl_id = hypothesis.target_level.id if getattr(hypothesis, "target_level", None) else None
        # Check if no-op
        if (
            d_rec.status == hypothesis.status
            and d_rec.revision == hypothesis.revision
            and d_rec.target_price == hypothesis.target_price
            and d_rec.target_level_id == target_lvl_id
        ):
            return LifecycleStageResult.NO_CHANGE

        # Preserve authoritative anchors from existing durable record
        c_at = d_rec.created_at_ms
        e_at = d_rec.expiry_ms
        fp = d_rec.source_fingerprint
        rev = max(d_rec.revision, int(hypothesis.revision))
        direction = d_rec.direction
        is_primary = d_rec.is_primary
        source_zone = d_rec.source_zone_id
    else:
        # First-time creation validation
        c_at = int(hypothesis.created_at_ms)
        e_at = int(hypothesis.expiry_ms)
        rev = int(hypothesis.revision)
        if c_at <= 0 or e_at < c_at or rev < 1:
            logger.warning(f"Write defense blocked invalid timestamps for new hypothesis '{hypothesis.hypothesis_id}'")
            return LifecycleStageResult.BLOCKED
        if hypothesis.status not in ("ACTIVE", "EXPIRED", "INVALIDATED", "FULFILLED"):
            return LifecycleStageResult.BLOCKED

        direction = hypothesis.direction
        is_primary = bool(hypothesis.is_primary)
        source_zone = zone_id
        fp = compute_hypothesis_source_fingerprint(
            hypothesis_id=hypothesis.hypothesis_id,
            symbol=symbol,
            direction=direction,
            source_zone_id=source_zone,
            created_at_ms=c_at,
        )

    target_lvl_id = hypothesis.target_level.id if getattr(hypothesis, "target_level", None) else None
    payload = {
        "schema_version": 1,
        "hypothesis_id": hypothesis.hypothesis_id,
        "symbol": symbol,
        "direction": direction,
        "is_primary": is_primary,
        "zone_id": source_zone,
        "created_at_ms": c_at,
        "expiry_ms": e_at,
        "status": str(hypothesis.status),
        "revision": rev,
        "target_price": float(hypothesis.target_price) if hypothesis.target_price is not None else None,
        "target_level_id": target_lvl_id,
        "target_type": str(hypothesis.target_type),
        "target_status": str(hypothesis.target_status),
        "source_fingerprint": fp,
        "updated_at_ms": int(now_ms),
    }
    rec = models.SystemConfig(
        key=key,
        value=json.dumps(payload, ensure_ascii=False),
        updated_at=int(now_ms),
    )
    db.merge(rec)
    return LifecycleStageResult.CREATED if existing_rec is None else LifecycleStageResult.UPDATED


def reconcile_hypothesis_lifecycle(
    existing_hypotheses: Sequence[Any],
    new_hypotheses: Sequence[LiquidityHypothesis],
    now_ms: int,
) -> List[LiquidityHypothesis]:
    """
    Reconciles existing and newly generated hypotheses preserving causal creation and expiry anchors (PHẦN 101 - 120).
    - Preserves created_at_ms and expiry_ms of existing hypotheses across evaluation cycles and restarts.
    - If now_ms >= expiry_ms, marks hypothesis status as 'EXPIRED'.
    - Terminal statuses ('EXPIRED', 'INVALIDATED', 'FULFILLED') are immutable and never revert to 'ACTIVE' (SECTIONS 048, 049).
    - If material context changed (target level, direction, invalidation), increments revision.
    """
    existing_map = {}
    for h in (existing_hypotheses or []):
        hid = getattr(h, "hypothesis_id", None) or (h.get("hypothesis_id") if isinstance(h, dict) else None)
        if hid:
            existing_map[hid] = h

    reconciled: List[LiquidityHypothesis] = []

    for hyp in new_hypotheses:
        existing = existing_map.get(hyp.hypothesis_id)
        if existing is not None:
            # Preserve original created_at_ms and expiry_ms
            c_at = int(getattr(existing, "created_at_ms", hyp.created_at_ms))
            e_at = int(getattr(existing, "expiry_ms", hyp.expiry_ms))
            existing_stat = getattr(existing, "status", "ACTIVE")
            existing_rev = int(getattr(existing, "revision", 1))

            # Check for material change
            existing_dir = getattr(existing, "direction", "")
            existing_tl = getattr(existing, "target_level", None)
            existing_tl_id = getattr(existing_tl, "id", None) if existing_tl else getattr(existing, "target_level_id", None)
            hyp_tl_id = hyp.target_level.id if hyp.target_level else None
            existing_inv = getattr(existing, "invalidation_condition", "")

            is_material_change = (
                existing_dir != hyp.direction
                or (existing_tl_id and hyp_tl_id and existing_tl_id != hyp_tl_id)
                or (bool(existing_tl_id) != bool(hyp_tl_id))
                or (existing_inv and existing_inv != hyp.invalidation_condition)
            )
            rev = (existing_rev + 1) if is_material_change else existing_rev

            # Terminal status protection (SECTIONS 048, 049): terminal statuses are immutable
            if existing_stat in ("EXPIRED", "INVALIDATED", "FULFILLED"):
                stat = existing_stat
            elif now_ms >= e_at:
                stat = "EXPIRED"
            else:
                stat = "ACTIVE"

            reconciled.append(
                LiquidityHypothesis(
                    hypothesis_id=hyp.hypothesis_id,
                    direction=hyp.direction,
                    is_primary=hyp.is_primary,
                    watched_zone=hyp.watched_zone,
                    target_level=hyp.target_level,
                    target_price=hyp.target_price,
                    dealing_range=hyp.dealing_range,
                    range_position=hyp.range_position,
                    prerequisites=hyp.prerequisites,
                    invalidation_condition=hyp.invalidation_condition,
                    expiry_ms=e_at,
                    created_at_ms=c_at,
                    status=stat,
                    revision=rev,
                    evidence_ids=hyp.evidence_ids,
                    explanation_vi=hyp.explanation_vi,
                    target_type=hyp.target_type,
                    target_status=hyp.target_status,
                )
            )
        else:
            # Fresh hypothesis
            stat = "EXPIRED" if now_ms >= hyp.expiry_ms else "ACTIVE"
            reconciled.append(
                LiquidityHypothesis(
                    hypothesis_id=hyp.hypothesis_id,
                    direction=hyp.direction,
                    is_primary=hyp.is_primary,
                    watched_zone=hyp.watched_zone,
                    target_level=hyp.target_level,
                    target_price=hyp.target_price,
                    dealing_range=hyp.dealing_range,
                    range_position=hyp.range_position,
                    prerequisites=hyp.prerequisites,
                    invalidation_condition=hyp.invalidation_condition,
                    expiry_ms=hyp.expiry_ms,
                    created_at_ms=hyp.created_at_ms,
                    status=stat,
                    revision=hyp.revision,
                    evidence_ids=hyp.evidence_ids,
                    explanation_vi=hyp.explanation_vi,
                    target_type=hyp.target_type,
                    target_status=hyp.target_status,
                )
            )

    return reconciled


def build_liquidity_hypotheses(
    zones: Sequence[WatchedZone],
    levels: Sequence[LiquidityLevel],
    dealing_range: DealingRange,
    current_price: float,
    regime: str,
    now_ms: int,
    session_cap_ms: Optional[int] = None,
    existing_hypotheses: Optional[Sequence[LiquidityHypothesis]] = None,
    ttl_ms: int = 4 * 3600 * 1000,
) -> List[LiquidityHypothesis]:
    """
    Builds primary and alternative causal liquidity hypotheses (PHẦN 091, 092).
    Phased conditionally: 'Nếu A xác nhận, theo dõi B. Hủy nếu phá C.'
    Target must be a real unconsumed liquidity level. Never fabricates +-10 fake levels (SECTIONS 101, 103).
    Preserves created_at_ms and expiry_ms of existing hypotheses to prevent expiry drift (PHẦN 101 - 120).
    """
    hypotheses: List[LiquidityHypothesis] = []
    calc_expiry = now_ms + ttl_ms
    expiry = min(calc_expiry, session_cap_ms) if session_cap_ms else calc_expiry

    # 1. Primary Hypothesis
    # Filter active watched zones
    active_zones = [z for z in zones if z.state in (ZoneState.WATCHING.value, ZoneState.NEAR.value, ZoneState.TOUCHED.value, ZoneState.WAITING_TRIGGER.value)]
    if not active_zones:
        return []

    # Prefer zone aligned with regime or closest
    preferred_dir = "BULLISH" if "BULLISH" in regime else ("BEARISH" if "BEARISH" in regime else None)
    aligned_zones = [z for z in active_zones if z.direction == preferred_dir] if preferred_dir else active_zones
    candidate_zones = aligned_zones or active_zones

    primary_zone = min(candidate_zones, key=lambda z: abs(current_price - z.midpoint))
    p_dir = primary_zone.direction

    # Select target level honestly
    target_lvl = select_unconsumed_target(levels, "LONG" if p_dir == "BULLISH" else "SHORT", current_price, as_of_ms=now_ms)
    if target_lvl is not None:
        t_price = round(target_lvl.price, 2)
        t_type = "LIQUIDITY_LEVEL"
        t_status = "CONFIRMED"
        target_clause = f"theo dõi mục tiêu thanh khoản {t_price:.2f}"
    else:
        t_price = None
        t_type = "UNAVAILABLE"
        t_status = "TARGET_UNAVAILABLE"
        target_clause = "chưa xác định được mục tiêu thanh khoản chưa hấp thụ; tiếp tục theo dõi phản ứng vùng"

    pos = assess_range_position(current_price, dealing_range)
    prereqs = [
        f"Giá giữ vững vùng {primary_zone.zone_type} [{primary_zone.bottom:.2f} - {primary_zone.top:.2f}]",
        "Có tín hiệu quét thanh khoản (Sweep) hoặc từ chối tại biên",
        "Nến M1 đóng xác nhận phá vỡ cấu trúc vi mô (CHOCH)",
        "Nến M1 retest vùng kích hoạt mà không vi phạm invalidation",
    ]
    inv = f"Giá đóng nến vượt qua mức {primary_zone.invalidation_level:.2f}"
    exp_vi = (
        f"Kịch bản chính ({p_dir}): Nếu giá phản ứng tại {primary_zone.zone_type} "
        f"[{primary_zone.bottom:.2f} - {primary_zone.top:.2f}] và xác nhận đảo chiều M1, "
        f"{target_clause}. Kịch bản vô hiệu nếu phá {primary_zone.invalidation_level:.2f}."
    )

    p_hyp = LiquidityHypothesis(
        hypothesis_id=f"hyp-primary-{primary_zone.zone_id}",
        direction=p_dir,
        is_primary=True,
        watched_zone=primary_zone,
        target_level=target_lvl,
        target_price=t_price,
        dealing_range=dealing_range,
        range_position=pos,
        prerequisites=prereqs,
        invalidation_condition=inv,
        expiry_ms=expiry,
        created_at_ms=now_ms,
        evidence_ids=list(primary_zone.source_evidence_ids),
        explanation_vi=exp_vi,
        target_type=t_type,
        target_status=t_status,
    )
    hypotheses.append(p_hyp)

    # 2. Alternative Hypothesis (Opposing scenario if primary invalidated)
    alt_dir = "BEARISH" if p_dir == "BULLISH" else "BULLISH"
    alt_zones = [z for z in active_zones if z.direction == alt_dir]
    if alt_zones:
        alt_zone = min(alt_zones, key=lambda z: abs(current_price - z.midpoint))
        alt_target = select_unconsumed_target(levels, "LONG" if alt_dir == "BULLISH" else "SHORT", current_price, as_of_ms=now_ms)
        if alt_target is not None:
            alt_t_price = round(alt_target.price, 2)
            alt_t_type = "LIQUIDITY_LEVEL"
            alt_t_status = "CONFIRMED"
            alt_target_clause = f"hướng về mục tiêu thanh khoản {alt_t_price:.2f}"
        else:
            alt_t_price = None
            alt_t_type = "UNAVAILABLE"
            alt_t_status = "TARGET_UNAVAILABLE"
            alt_target_clause = "chưa xác định được mục tiêu thanh khoản dự phòng hợp lệ"

        alt_prereqs = [
            f"Kịch bản chính bị vô hiệu khi giá xuyên thủng {primary_zone.invalidation_level:.2f}",
            f"Giá tiếp cận và phản ứng tại vùng {alt_zone.zone_type} [{alt_zone.bottom:.2f} - {alt_zone.top:.2f}]",
            "Xác nhận đảo chiều/tiếp diễn trên khung M1",
        ]
        alt_inv = f"Giá đóng nến vượt qua mức {alt_zone.invalidation_level:.2f}"
        alt_exp_vi = (
            f"Kịch bản dự phòng ({alt_dir}): Nếu kịch bản chính bị phá vỡ qua {primary_zone.invalidation_level:.2f}, "
            f"chuyển hướng theo dõi vùng {alt_zone.zone_type} [{alt_zone.bottom:.2f} - {alt_zone.top:.2f}] "
            f"{alt_target_clause}."
        )

        a_hyp = LiquidityHypothesis(
            hypothesis_id=f"hyp-alt-{alt_zone.zone_id}",
            direction=alt_dir,
            is_primary=False,
            watched_zone=alt_zone,
            target_level=alt_target,
            target_price=alt_t_price,
            dealing_range=dealing_range,
            range_position=pos,
            prerequisites=alt_prereqs,
            invalidation_condition=alt_inv,
            expiry_ms=expiry,
            created_at_ms=now_ms,
            evidence_ids=list(alt_zone.source_evidence_ids),
            explanation_vi=alt_exp_vi,
            target_type=alt_t_type,
            target_status=alt_t_status,
        )
        hypotheses.append(a_hyp)

    if existing_hypotheses is not None:
        return reconcile_hypothesis_lifecycle(existing_hypotheses, hypotheses, now_ms)

    return hypotheses


def build_execution_hypotheses_for_zones(
    zones: Sequence[WatchedZone],
    levels: Optional[Sequence[LiquidityLevel]] = None,
    dealing_range: Optional[DealingRange] = None,
    current_price: float = 0.0,
    regime: str = "BALANCED",
    now_ms: Optional[int] = None,
    session_cap_ms: Optional[int] = None,
    existing_hypotheses: Optional[Sequence[Any]] = None,
    ttl_ms: int = 4 * 3600 * 1000,
    as_of_ms: Optional[int] = None,
) -> List[LiquidityHypothesis]:
    """
    V16 Multi-Zone Execution Hypotheses Builder (Rules 070 - 080).
    Builds causal execution hypotheses for ALL bounded active eligible zones.
    Preserves existing durable hypothesis IDs and anchors per zone to prevent expiry drift.
    Determines primary and alternative display selection without restricting execution coverage.
    """
    t_now = now_ms if now_ms is not None else (as_of_ms if as_of_ms is not None else int(time.time() * 1000))
    active_zones = [
        z for z in zones
        if z.state in (ZoneState.CONFIRMED.value, ZoneState.WATCHING.value, ZoneState.NEAR.value, ZoneState.TOUCHED.value, ZoneState.WAITING_TRIGGER.value)
    ]
    if not active_zones:
        return []

    calc_expiry = t_now + ttl_ms
    expiry = min(calc_expiry, session_cap_ms) if session_cap_ms else calc_expiry

    # Existing map by zone_id
    existing_zone_map: Dict[str, Any] = {}
    for h in (existing_hypotheses or []):
        z_id = getattr(h, "source_zone_id", None)
        if not z_id and getattr(h, "watched_zone", None):
            z_id = getattr(h.watched_zone, "zone_id", None)
        if not z_id and isinstance(h, dict):
            z_id = h.get("source_zone_id") or h.get("zone_id")
        if z_id:
            existing_zone_map[z_id] = h

    # Determine primary zone for presentation
    preferred_dir = "BULLISH" if "BULLISH" in regime else ("BEARISH" if "BEARISH" in regime else None)
    aligned_zones = [z for z in active_zones if z.direction == preferred_dir] if preferred_dir else active_zones
    candidate_zones = aligned_zones or active_zones
    primary_zone = min(candidate_zones, key=lambda z: abs(current_price - z.midpoint))
    p_dir = primary_zone.direction

    # Determine alternative opposing zone
    alt_dir = "BEARISH" if p_dir == "BULLISH" else "BULLISH"
    alt_zones = [z for z in active_zones if z.direction == alt_dir]
    alt_zone = min(alt_zones, key=lambda z: abs(current_price - z.midpoint)) if alt_zones else None

    pos = assess_range_position(current_price, dealing_range) if (dealing_range and getattr(dealing_range, "is_valid", False)) else "EQUILIBRIUM"
    hypotheses: List[LiquidityHypothesis] = []

    for z in active_zones:
        is_primary = (z.zone_id == primary_zone.zone_id)
        is_alt = (alt_zone is not None and z.zone_id == alt_zone.zone_id)

        # Stable hypothesis ID: execution identity is decoupled from presentation rank (Rule 073)
        existing_h = existing_zone_map.get(z.zone_id)
        if existing_h:
            h_id = getattr(existing_h, "hypothesis_id", None) or (existing_h.get("hypothesis_id") if isinstance(existing_h, dict) else None)
            if not h_id:
                h_id = f"hyp-zone-{z.zone_id}"
        else:
            h_id = f"hyp-zone-{z.zone_id}"

        target_dir = "LONG" if z.direction == "BULLISH" else "SHORT"
        target_lvl = select_unconsumed_target(levels, target_dir, current_price, as_of_ms=now_ms)
        if target_lvl is not None:
            t_price = round(target_lvl.price, 2)
            t_type = "LIQUIDITY_LEVEL"
            t_status = "CONFIRMED"
            target_clause = f"theo dõi mục tiêu thanh khoản {t_price:.2f}"
        else:
            t_price = None
            t_type = "UNAVAILABLE"
            t_status = "TARGET_UNAVAILABLE"
            target_clause = "chưa xác định được mục tiêu thanh khoản chưa hấp thụ; tiếp tục theo dõi phản ứng vùng"

        prereqs = [
            f"Giá giữ vững vùng {z.zone_type} [{z.bottom:.2f} - {z.top:.2f}]",
            "Có tín hiệu quét thanh khoản (Sweep) hoặc từ chối tại biên",
            "Nến M1 đóng xác nhận phá vỡ cấu trúc vi mô (CHOCH)",
            "Nến M1 retest vùng kích hoạt mà không vi phạm invalidation",
        ]
        inv = f"Giá đóng nến vượt qua mức {z.invalidation_level:.2f}"
        role_label = "Kịch bản chính" if is_primary else ("Kịch bản dự phòng" if is_alt else f"Kịch bản theo dõi ({z.direction})")
        exp_vi = (
            f"{role_label}: Nếu giá phản ứng tại {z.zone_type} "
            f"[{z.bottom:.2f} - {z.top:.2f}] và xác nhận đảo chiều M1, "
            f"{target_clause}. Kịch bản vô hiệu nếu phá {z.invalidation_level:.2f}."
        )

        hyp = LiquidityHypothesis(
            hypothesis_id=h_id,
            direction=z.direction,
            is_primary=is_primary,
            watched_zone=z,
            target_level=target_lvl,
            target_price=t_price,
            dealing_range=dealing_range,
            range_position=pos,
            prerequisites=prereqs,
            invalidation_condition=inv,
            expiry_ms=expiry,
            created_at_ms=now_ms,
            evidence_ids=list(z.source_evidence_ids),
            explanation_vi=exp_vi,
            target_type=t_type,
            target_status=t_status,
        )
        hypotheses.append(hyp)

    if existing_hypotheses is not None:
        return reconcile_hypothesis_lifecycle(existing_hypotheses, hypotheses, now_ms)

    return hypotheses


# =====================================================================
# 8. MULTI-DIMENSIONAL ZONE RANKING & EXPLANATION (SECTIONS 097 - 100, 164)
# =====================================================================

@dataclass(frozen=True)
class ZoneRankScore:
    zone_id: str
    total_score: float
    zone_quality_score: float
    context_score: float
    readiness_score: float
    execution_score: float
    confluence: ZoneConfluence
    intervening_obstacles: List[Dict[str, Any]]
    explanation_vi: str


def rank_zones_multi_dimensional(
    zones: Sequence[WatchedZone],
    levels: Sequence[LiquidityLevel],
    dealing_range: DealingRange,
    current_price: float,
    atr: float,
    htf_bias: Optional[str] = None,
    now_ms: int = 0,
) -> List[Tuple[WatchedZone, ZoneRankScore]]:
    """
    Ranks zones across 4 distinct dimensions (PHẦN 097, 098):
    1. Zone Quality: Freshness, displacement strength, confluence.
    2. Context Alignment: Dealing range (discount/premium), HTF bias.
    3. Trigger Readiness: Distance ATR, proximity hysteresis state.
    4. Execution Feasibility: Clear runway to target, minimal intervening obstacles.
    Deterministic tie-breaking. Never produces win probability.
    """
    scored: List[Tuple[WatchedZone, ZoneRankScore]] = []

    for z in zones:
        if z.state == ZoneState.INVALIDATED.value:
            continue

        # 1. Zone Quality (0-100)
        q_score = 50.0
        if z.freshness_state == "FRESH":
            q_score += 25.0
        elif z.freshness_state == "PARTIALLY_MITIGATED":
            q_score += 10.0
        if z.zone_type == "OB":
            q_score += 15.0  # OB carries structural anchor

        # Check confluence with any other active zone
        conf = ZoneConfluence(has_confluence=False)
        for other in zones:
            if other.zone_id != z.zone_id:
                c = evaluate_zone_confluence(z, other)
                if c.has_confluence:
                    conf = c
                    q_score += 10.0
                    break
        q_score = min(100.0, q_score)

        # 2. Context Alignment (0-100)
        c_score = 50.0
        pos = assess_range_position(current_price, dealing_range)
        if z.direction == "BULLISH" and pos == "DISCOUNT":
            c_score += 30.0
        elif z.direction == "BEARISH" and pos == "PREMIUM":
            c_score += 30.0
        elif pos == "EQUILIBRIUM":
            c_score += 10.0

        if htf_bias:
            norm_bias = "BULLISH" if "BULL" in htf_bias.upper() else ("BEARISH" if "BEAR" in htf_bias.upper() else None)
            if norm_bias == z.direction:
                c_score += 20.0
        c_score = min(100.0, c_score)

        # 3. Trigger Readiness (0-100)
        dist_px = abs(current_price - z.midpoint)
        dist_atr = dist_px / max(0.50, atr)
        r_score = max(0.0, 100.0 - (dist_atr * 20.0))
        if z.state == ZoneState.TOUCHED.value:
            r_score = 100.0
        elif z.state == ZoneState.NEAR.value:
            r_score = max(r_score, 80.0)

        # 4. Execution Feasibility (0-100)
        target = select_unconsumed_target(levels, "LONG" if z.direction == "BULLISH" else "SHORT", current_price, as_of_ms=now_ms)
        if target is not None:
            t_price = target.price
            obstacles = find_intervening_obstacles(levels, zones, "LONG" if z.direction == "BULLISH" else "SHORT", current_price, t_price)
            e_score = 80.0
            if obstacles:
                e_score -= min(40.0, len(obstacles) * 15.0)
        else:
            obstacles = []
            e_score = 45.0  # Reduced feasibility when no unconsumed liquidity target is found (SECTIONS 101, 103)

        # Weighted total
        total = round(0.30 * q_score + 0.30 * c_score + 0.25 * r_score + 0.15 * e_score, 1)

        expl = (
            f"Chất lượng vùng: {q_score:.0f}/100, Bối cảnh ({pos}): {c_score:.0f}/100, "
            f"Khoảng cách ({dist_atr:.2f} ATR): {r_score:.0f}/100, Khả thi thực thi: {e_score:.0f}/100."
        )

        rank_item = ZoneRankScore(
            zone_id=z.zone_id,
            total_score=total,
            zone_quality_score=q_score,
            context_score=c_score,
            readiness_score=r_score,
            execution_score=e_score,
            confluence=conf,
            intervening_obstacles=obstacles,
            explanation_vi=expl,
        )
        scored.append((z, rank_item))

    # Deterministic sorting: total_score desc, then distance asc, then known_at desc
    scored.sort(
        key=lambda pair: (
            -pair[1].total_score,
            abs(current_price - pair[0].midpoint),
            -pair[0].known_at,
            pair[0].zone_id,
        )
    )
    return scored


def build_zone_assessment_explanation(
    zone: WatchedZone,
    rank_score: ZoneRankScore,
    hypothesis: Optional[LiquidityHypothesis],
    current_price: float,
    atr: float,
    *,
    as_of_ms: int = 0,
    is_stale: bool = False,
) -> Dict[str, Any]:
    """
    Builds comprehensive explanation drawer DTO in clear Vietnamese (PHẦN 099, 164).
    Explains why zone is watched, missing conditions, target, invalidation, and guards.
    Never produces fake win probability (strictly null when uncalibrated).
    """
    dist_atr = round(abs(current_price - zone.midpoint) / max(0.50, atr), 2)

    missing = []
    if zone.state == ZoneState.WATCHING.value:
        missing.append(f"Giá tiếp cận biên vùng (hiện cách {dist_atr} ATR)")
    if zone.state in (ZoneState.WATCHING.value, ZoneState.NEAR.value):
        missing.append("Giá chạm vào vùng (Zone Touch)")
    missing.extend([
        "Xác nhận quét thanh khoản (Sweep) hoặc từ chối tại biên",
        "Nến M1 đóng vượt mức phá vỡ cấu trúc vi mô (CHOCH)",
        "Nến M1 retest vùng kích hoạt sau phá vỡ",
        "Tỷ lệ Net R:R tối thiểu đạt yêu cầu sau tính phí và trượt giá",
    ])

    return {
        "zone_id": zone.zone_id,
        "symbol": zone.symbol,
        "timeframe": zone.timeframe,
        "zone_type": zone.zone_type,
        "direction": zone.direction,
        "bounds": [zone.bottom, zone.top],
        "invalidation_level": zone.invalidation_level,
        "state": zone.state,
        "rank_score": rank_score.total_score,
        "quality_score": rank_score.zone_quality_score,
        "context_score": rank_score.context_score,
        "readiness_score": rank_score.readiness_score,
        "execution_score": rank_score.execution_score,
        "win_probability": None,
        "has_confluence": rank_score.confluence.has_confluence,
        "confluence_description": rank_score.confluence.description_vi,
        "intervening_obstacles_count": len(rank_score.intervening_obstacles),
        "target_price": hypothesis.target_price if hypothesis else None,
        "target_type": getattr(hypothesis, "target_type", "UNAVAILABLE") if hypothesis else "UNAVAILABLE",
        "target_status": getattr(hypothesis, "target_status", "TARGET_UNAVAILABLE") if hypothesis else "TARGET_UNAVAILABLE",
        "target_label": hypothesis.target_level.label_vi if (hypothesis and hypothesis.target_level) else ("Mục tiêu cấu trúc" if (hypothesis and hypothesis.target_price is not None) else "Chưa có mục tiêu"),
        "missing_conditions": missing,
        "why_watched": (
            f"Vùng {zone.zone_type} {zone.direction} trên khung {zone.timeframe} hình thành tại [{zone.bottom:.2f} - {zone.top:.2f}]. "
            f"{rank_score.explanation_vi}"
        ),
        "hypothesis_summary": hypothesis.explanation_vi if hypothesis else "",
        "as_of_ms": as_of_ms,
        "is_stale": is_stale,
    }
