"""
backend/services/zone_watchlist_service.py
V15.0 - SMC Zone Watchlist, Ranking & Near-Zone Proximity Service (PHẦN 033 - 041).

Features:
- extract_zones_from_candles: Discovers real unmitigated FVG and validated structure zones.
- rank_watch_zones: Ranks zones based on freshness, displacement, HTF confluence, and proximity.
- evaluate_zone_proximity: Hysteresis-protected proximity evaluation to prevent alert flapping.
- prune_and_bound_watchlist: Enforces expiration, invalidation, and bounded watchlist capacity.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import smc_engine
from services.scalp_m1_types import M1ScalpConfig, WatchedZone, ZoneState, get_scalp_config

logger = logging.getLogger(__name__)


class ZoneWatchlistService:
    @staticmethod
    def extract_zones_from_candles(
        candles: Sequence[Any],
        timeframe: str = "15M",
        symbol: str = "XAUUSDT",
        *,
        as_of_ms: int = 0,
        model_version: str = "v15.0",
        atr: Optional[float] = None,
    ) -> List[WatchedZone]:
        """
        Extract real FVG zones from closed candles (PHẦN 034).
        Never synthesizes fake rectangles or future bars.
        """
        if len(candles) < 3:
            return []

        calc_atr = atr or smc_engine.compute_atr(candles, period=14)
        min_size = max(0.15 * calc_atr, 0.50) if calc_atr > 0 else 0.50

        fvgs = smc_engine.detect_fvgs(candles, timeframe=timeframe)
        zones: List[WatchedZone] = []

        for fvg in fvgs:
            top = float(fvg.get("top", 0.0))
            bottom = float(fvg.get("bottom", 0.0))
            if top <= bottom or (top - bottom) < min_size:
                continue

            direction = "BULLISH" if "BULL" in fvg.get("type", "") else "BEARISH"
            open_ts = int(fvg.get("open_time", 0))
            confirmed_ts = int(fvg.get("confirmed_time") or open_ts)

            # Invalidation level: beyond the anchor candle extreme
            if direction == "BULLISH":
                inv_lvl = bottom - (0.2 * calc_atr)
            else:
                inv_lvl = top + (0.2 * calc_atr)

            zone_id = f"zone-{symbol}-{timeframe}-{direction}-{open_ts}"
            zones.append(WatchedZone(
                zone_id=zone_id,
                symbol=symbol,
                timeframe=timeframe,
                zone_type="FVG",
                direction=direction,
                top=round(top, 2),
                bottom=round(bottom, 2),
                created_at=open_ts,
                known_at=confirmed_ts,
                source_evidence_ids=(f"fvg-{open_ts}",),
                freshness_state="FRESH",
                touch_count=0,
                invalidation_level=round(inv_lvl, 2),
                model_version=model_version,
                state=ZoneState.WATCHING.value,
                last_evaluated_at=as_of_ms,
            ))

        # Extract Order Blocks (SECTIONS 028, 041 - 064)
        from services.liquidity_scenario_service import detect_order_blocks, identify_confirmed_swings
        sh_list, sl_list = identify_confirmed_swings(
            candles,
            timeframe=timeframe,
            left_bars=2,
            right_bars=2,
            symbol=symbol,
            as_of_ms=as_of_ms,
        )
        swings = sh_list + sl_list
        ob_zones = detect_order_blocks(
            candles, swings, timeframe=timeframe, symbol=symbol, as_of_ms=as_of_ms
        )
        zones.extend(ob_zones)

        return zones

    @staticmethod
    def evaluate_zone_proximity(
        zone: WatchedZone,
        bid: float,
        ask: float,
        atr: float,
        config: Optional[M1ScalpConfig] = None,
    ) -> Tuple[str, float]:
        """
        Evaluate price distance to zone using hysteresis (PHẦN 039).
        Returns (new_state, distance_atr).
        Hysteresis avoids flapping between NEAR and WATCHING at boundaries.
        """
        cfg = config or get_scalp_config()
        safe_atr = max(atr, 0.50)

        # Check for invalidation first (broken zone)
        if zone.direction == "BULLISH" and bid < zone.invalidation_level:
            return ZoneState.INVALIDATED.value, 999.0
        if zone.direction == "BEARISH" and ask > zone.invalidation_level:
            return ZoneState.INVALIDATED.value, 999.0

        # Check for touch (inside zone)
        current_px = (bid + ask) / 2.0
        if zone.bottom <= current_px <= zone.top or (zone.direction == "BULLISH" and bid <= zone.top and ask >= zone.bottom) or (zone.direction == "BEARISH" and ask >= zone.bottom and bid <= zone.top):
            return ZoneState.TOUCHED.value, 0.0

        # Calculate distance to nearest boundary
        if current_px > zone.top:
            dist_px = current_px - zone.top
        else:
            dist_px = zone.bottom - current_px

        dist_atr = round(dist_px / safe_atr, 3)

        # Hysteresis transition logic
        current_state = zone.state
        if current_state == ZoneState.TOUCHED.value:
            # If leaving touched zone, transition to WAITING_TRIGGER if still within max entry distance, else WATCHING
            if dist_atr <= cfg.max_entry_distance_atr:
                new_state = ZoneState.WAITING_TRIGGER.value
            else:
                new_state = ZoneState.WATCHING.value
        elif current_state == ZoneState.WAITING_TRIGGER.value:
            if dist_atr <= cfg.max_entry_distance_atr:
                new_state = ZoneState.WAITING_TRIGGER.value
            else:
                new_state = ZoneState.WATCHING.value
        elif current_state == ZoneState.NEAR.value:
            # Must exceed exit threshold to leave NEAR
            if dist_atr > cfg.near_zone_exit_atr:
                new_state = ZoneState.WATCHING.value
            else:
                new_state = ZoneState.NEAR.value
        else:
            # Must penetrate enter threshold to enter NEAR
            if dist_atr <= cfg.near_zone_enter_atr:
                new_state = ZoneState.NEAR.value
            else:
                new_state = ZoneState.WATCHING.value

        return new_state, dist_atr

    @staticmethod
    def track_zone_touch_episode(zone: WatchedZone, bar_id_or_ts: Any = None, is_touching: bool = True) -> bool:
        """
        Rule 075, 076: Single authoritative point for touch tracking and freshness.
        An episode is when price enters the zone from the outside.
        It must exit the zone before a subsequent entry counts as a new episode.
        Multiple ticks or candles inside the zone do NOT increment touch_count multiple times.
        """
        inside = getattr(zone, "_inside_zone", False)
        if not is_touching:
            setattr(zone, "_inside_zone", False)
            return False

        if not inside:
            setattr(zone, "_inside_zone", True)
            setattr(zone, "_last_touch_bar", bar_id_or_ts)
            zone.touch_count += 1
            if zone.touch_count == 1:
                zone.freshness_state = "PARTIALLY_MITIGATED"
            elif zone.touch_count >= 2:
                zone.freshness_state = "MITIGATED"
            return True
        return False

    @staticmethod
    def rank_watch_zones(
        zones: Sequence[WatchedZone],
        current_price: float,
        atr: float,
        htf_bias: Optional[str] = None,
        h1_alignment: Optional[str] = None,
        as_of_ms: int = 0,
    ) -> List[WatchedZone]:
        """
        Rank zones for priority monitoring (PHẦN 036, 037).
        Factors:
        - HTF confluence (+30 pts if direction aligns with 4H / 1H trend)
        - Freshness (+20 pts if 0 touches, +10 pts if 1 touch)
        - Age decay (-1 pt per 30 mins)
        - Proximity: closer zones receive higher priority score (+0 to +30 pts)
        - Zone thickness / displacement (+0 to +15 pts)

        NOTE: This rank score is for observation priority only; it is NEVER
        referred to as win probability and never overrides entry guards.
        """
        safe_atr = max(atr, 0.50)
        ranked: List[WatchedZone] = []

        for z in zones:
            if z.state in (ZoneState.INVALIDATED.value, ZoneState.EXPIRED.value):
                continue

            score = 50.0  # Base observation weight

            # 1. HTF Confluence
            is_bullish = z.direction in ("BULLISH", "LONG")
            if is_bullish:
                if htf_bias == "BULLISH":
                    score += 20.0
                if h1_alignment == "ALIGNED" or h1_alignment == "BULLISH":
                    score += 10.0
            else:
                if htf_bias == "BEARISH":
                    score += 20.0
                if h1_alignment == "ALIGNED" or h1_alignment == "BEARISH":
                    score += 10.0

            # 2. Freshness & touches
            if z.touch_count == 0:
                score += 20.0
            elif z.touch_count == 1:
                score += 10.0
            else:
                score -= 10.0

            # 3. Age decay
            if as_of_ms > z.created_at:
                age_hours = (as_of_ms - z.created_at) / (3600 * 1000.0)
                score -= min(age_hours * 2.0, 20.0)

            # 4. Proximity to current price
            dist_to_mid = abs(current_price - z.midpoint) / safe_atr
            score += max(0.0, 30.0 - (dist_to_mid * 5.0))

            # 5. Zone thickness
            thickness_atr = z.height / safe_atr
            score += min(thickness_atr * 5.0, 15.0)

            z.rank_score = round(max(0.0, score), 1)
            ranked.append(z)

        # Sort descending by rank score, then proximity
        ranked.sort(key=lambda z: -z.rank_score)
        return ranked

    @staticmethod
    def prune_and_bound_watchlist(
        zones: List[WatchedZone],
        current_price: float,
        as_of_ms: int,
        config: Optional[M1ScalpConfig] = None,
    ) -> List[WatchedZone]:
        """
        Enforce expiration, invalidation and bounded watchlist size (PHẦN 040, 041).
        """
        cfg = config or get_scalp_config()
        active: List[WatchedZone] = []

        for z in zones:
            # Expiration check
            if as_of_ms > z.created_at and (as_of_ms - z.created_at) > cfg.max_zone_age_ms:
                z.state = ZoneState.EXPIRED.value
                continue

            # Invalidation check
            if z.direction in ("BULLISH", "LONG") and current_price < z.invalidation_level:
                z.state = ZoneState.INVALIDATED.value
                continue
            if z.direction in ("BEARISH", "SHORT") and current_price > z.invalidation_level:
                z.state = ZoneState.INVALIDATED.value
                continue

            if z.state not in (ZoneState.INVALIDATED.value, ZoneState.EXPIRED.value):
                active.append(z)

        # Keep top active zones within max capacity
        active.sort(key=lambda z: -z.rank_score)
        return active[:cfg.max_active_zones]
