"""
test_v15_0_zone_watchlist.py
V15.0 - Unit tests for Zone Watchlist, Ranking, Hysteresis Proximity & Bounded Maintenance (PHẦN 033 - 041).
"""
from __future__ import annotations

import pytest
from dataclasses import dataclass
from services.scalp_m1_types import M1ScalpConfig, WatchedZone, ZoneState
from services.zone_watchlist_service import ZoneWatchlistService


@dataclass
class CandleStub:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 10.0

    @property
    def open_time(self) -> int:
        return self.timestamp


def test_zone_extraction_from_candles():
    """PHẦN 034: Real FVG extraction with valid bounds and invalidation level."""
    # Bullish FVG: Bar 0 high < Bar 2 low (gap between Bar 0 and Bar 2)
    candles = [
        CandleStub(1000, 2600.0, 2605.0, 2595.0, 2604.0),
        CandleStub(2000, 2604.0, 2620.0, 2603.0, 2618.0), # Big green expansion
        CandleStub(3000, 2618.0, 2625.0, 2612.0, 2622.0), # Gap between 2605 and 2612
    ]

    zones = ZoneWatchlistService.extract_zones_from_candles(candles, timeframe="15M", symbol="XAUUSDT", atr=5.0)
    assert len(zones) == 1
    z = zones[0]
    assert z.direction == "BULLISH"
    assert z.bottom == 2605.0
    assert z.top == 2612.0
    assert z.invalidation_level < z.bottom
    assert z.state == ZoneState.WATCHING.value


def test_near_zone_proximity_hysteresis():
    """PHẦN 039: Proximity evaluation with hysteresis prevents alert flapping."""
    cfg = M1ScalpConfig(near_zone_enter_atr=0.30, near_zone_exit_atr=0.50)
    atr = 10.0 # enter threshold = 3.0 px, exit threshold = 5.0 px

    # Bullish zone: [2600, 2610]
    zone = WatchedZone(
        zone_id="z_hysteresis", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2610.0, bottom=2600.0, created_at=1000, known_at=1000,
        invalidation_level=2595.0, state=ZoneState.WATCHING.value
    )

    # Price = 2620.0 (dist = 10.0 px = 1.0 ATR > 0.30) -> WATCHING
    st, d = ZoneWatchlistService.evaluate_zone_proximity(zone, 2620.0, 2620.0, atr, cfg)
    assert st == ZoneState.WATCHING.value
    zone.state = st

    # Price moves to 2612.5 (dist = 2.5 px = 0.25 ATR <= 0.30) -> enters NEAR
    st, d = ZoneWatchlistService.evaluate_zone_proximity(zone, 2612.5, 2612.5, atr, cfg)
    assert st == ZoneState.NEAR.value
    zone.state = st

    # Price bounces back to 2614.0 (dist = 4.0 px = 0.40 ATR):
    # Above enter (0.30) but BELOW exit (0.50). Hysteresis keeps it NEAR (no flapping)!
    st, d = ZoneWatchlistService.evaluate_zone_proximity(zone, 2614.0, 2614.0, atr, cfg)
    assert st == ZoneState.NEAR.value
    zone.state = st

    # Price retreats further to 2616.0 (dist = 6.0 px = 0.60 ATR > 0.50) -> exits back to WATCHING
    st, d = ZoneWatchlistService.evaluate_zone_proximity(zone, 2616.0, 2616.0, atr, cfg)
    assert st == ZoneState.WATCHING.value
    zone.state = st

    # Price touches zone [2605.0] -> TOUCHED
    st, d = ZoneWatchlistService.evaluate_zone_proximity(zone, 2605.0, 2605.0, atr, cfg)
    assert st == ZoneState.TOUCHED.value
    zone.state = st

    # Price breaks invalidation level (2590 < 2595) -> INVALIDATED
    st, d = ZoneWatchlistService.evaluate_zone_proximity(zone, 2590.0, 2590.0, atr, cfg)
    assert st == ZoneState.INVALIDATED.value


def test_zone_ranking_and_capacity_bounding():
    """PHẦN 036, 041: Zone ranking rewards HTF confluence & freshness and bounds capacity."""
    cfg = M1ScalpConfig(max_active_zones=2, max_zone_age_ms=3600_000)

    # 3 zones
    z_fresh_aligned = WatchedZone(
        zone_id="z_best", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2650.0, bottom=2645.0, created_at=1000, known_at=1000,
        invalidation_level=2635.0, touch_count=0
    )
    z_tested = WatchedZone(
        zone_id="z_tested", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2630.0, bottom=2625.0, created_at=1000, known_at=1000,
        invalidation_level=2615.0, touch_count=2
    )
    z_expired = WatchedZone(
        zone_id="z_expired", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2600.0, bottom=2590.0, created_at=1000, known_at=1000,
        invalidation_level=2580.0
    )

    zones = [z_tested, z_fresh_aligned, z_expired]
    ranked = ZoneWatchlistService.rank_watch_zones(
        zones, current_price=2648.0, atr=5.0, htf_bias="BULLISH", h1_alignment="ALIGNED", as_of_ms=2000
    )
    # Best zone ranked first due to alignment and freshness
    assert ranked[0].zone_id == "z_best"
    assert ranked[0].rank_score > ranked[1].rank_score

    # Prune & bound with max_capacity = 2 and age cutoff
    bounded = ZoneWatchlistService.prune_and_bound_watchlist(
        ranked, current_price=2648.0, as_of_ms=5000_000, config=cfg # 5000s > 3600s -> z_expired expires
    )
    assert len(bounded) <= 2
    assert "z_expired" not in [b.zone_id for b in bounded]
