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

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

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

    for i in range(left_bars, n - right_bars):
        mid = candles[i]
        mid_high = float(getattr(mid, "high", 0.0))
        mid_low = float(getattr(mid, "low", 0.0))
        open_ms = int(getattr(mid, "open_time", None) or getattr(mid, "timestamp", 0))

        # Confirmation candle is candles[i + right_bars]
        confirm_candle = candles[i + right_bars]
        confirm_open = int(getattr(confirm_candle, "open_time", None) or getattr(confirm_candle, "timestamp", 0))
        known_at_ms = confirm_open + duration_ms
        if as_of_ms and known_at_ms > as_of_ms:
            continue

        # Swing High
        is_sh = True
        for j in range(i - left_bars, i + right_bars + 1):
            if j == i:
                continue
            bar_h = float(getattr(candles[j], "high", 0.0))
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
            bar_l = float(getattr(candles[j], "low", 0.0))
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

        # Prefix ATR for causal displacement measurement (SECTIONS 045, 046)
        prefix_atr = calc_atr if atr is not None else smc_engine.compute_atr(candles[:i + 1], period=14)
        if prefix_atr <= 0:
            prefix_atr = calc_atr

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


def build_liquidity_hypotheses(
    zones: Sequence[WatchedZone],
    levels: Sequence[LiquidityLevel],
    dealing_range: DealingRange,
    current_price: float,
    regime: str,
    now_ms: int,
    session_cap_ms: Optional[int] = None,
) -> List[LiquidityHypothesis]:
    """
    Builds primary and alternative causal liquidity hypotheses (PHẦN 091, 092).
    Phased conditionally: 'Nếu A xác nhận, theo dõi B. Hủy nếu phá C.'
    Target must be a real unconsumed liquidity level. Never fabricates +-10 fake levels (SECTIONS 101, 103).
    """
    hypotheses: List[LiquidityHypothesis] = []
    expiry = session_cap_ms or (now_ms + 4 * 3600 * 1000)

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
