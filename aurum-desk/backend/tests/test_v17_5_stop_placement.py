"""
backend/tests/test_v17_5_stop_placement.py
V17.5: Unit tests for Pure Stop Placement (PHẦN 032, 051 - 060, 161 - 163).

Covers:
- compute_stop_atr: Period+1 requirement, SMA_TR_V1, finite float, future candle exclusion.
- quantize_stop_outward: Decimal rounding ceiling for SHORT, floor for LONG, tick sizes (0.01, 0.1, 0.25).
- compute_stop_buffer: max(tick_floor, k_m1*ATR_M1, k_m5*ATR_M5, k_spread*spread).
- plan_structural_stop: Pure candidate planning with status and reasons.
- Hand-calculated fixtures: SHORT 100/94/101/0.01 -> SL 101.50; LONG 100/106/99/0.01 -> SL 98.50.
"""
import pytest
from services.stop_placement_types import (
    StopPlacementPolicy,
    StopAnchor,
    StopAnchorResult,
    StopATRResult,
    StopBufferResult,
    PriceBasis,
    StopAnchorKind,
)
from services.stop_placement_service import (
    compute_stop_atr,
    quantize_stop_outward,
    compute_stop_buffer,
    plan_structural_stop,
    validate_anchor_not_invalidated,
)


class MockBar:
    def __init__(self, open_time: int, o: float, h: float, l: float, c: float):
        self.open_time = open_time
        self.open = o
        self.high = h
        self.low = l
        self.close = c


def test_compute_stop_atr_golden():
    """PHẦN 163: ATR requires period+1 bars, computes SMA of True Range, excludes future bars."""
    base_t = 1_000_000
    bars = []
    # 15 bars (period=14 needs 15 bars)
    for i in range(15):
        t = base_t + i * 60_000
        # TR for each: high - low = 2.0, close=open, prev_close match
        bars.append(MockBar(t, 100.0, 101.0, 99.0, 100.0))

    # All 15 bars closed before cutoff: cutoff = base_t + 15*60_000
    cutoff = base_t + 15 * 60_000
    res = compute_stop_atr(bars, period=14, timeframe="1M", as_of_ms=cutoff)

    assert res.status == "AVAILABLE"
    assert res.method == "SMA_TR_V1"
    assert res.timeframe == "1M"
    assert pytest.approx(res.value, rel=1e-4) == 2.0
    assert res.bars_count == 15

    # Insufficient bars: only 10 bars
    res_short = compute_stop_atr(bars[:10], period=14, timeframe="1M", as_of_ms=cutoff)
    assert res_short.status == "UNAVAILABLE"
    assert "INSUFFICIENT_BARS" in res_short.reason_code


def test_compute_stop_atr_future_exclusion():
    """Future candles beyond decision cutoff must be excluded."""
    base_t = 1_000_000
    bars = [MockBar(base_t + i * 60_000, 100.0, 101.0, 99.0, 100.0) for i in range(20)]
    cutoff = base_t + 15 * 60_000  # bar 14 closes at base_t + 15*60_000

    res = compute_stop_atr(bars, period=14, timeframe="1M", as_of_ms=cutoff)
    assert res.status == "AVAILABLE"
    assert res.last_closed_at_ms == cutoff


def test_quantize_stop_outward_rounding():
    """PHẦN 162: SHORT SL ceil outward, LONG SL floor outward to exact tick grids."""
    # SHORT: anchor 101.00 + buffer 0.50 -> 101.50 on 0.01 tick
    sl_short = quantize_stop_outward(anchor_price=101.00, buffer_price=0.50, direction="SHORT", tick_size=0.01)
    assert sl_short == 101.50

    # LONG: anchor 99.00 - buffer 0.50 -> 98.50 on 0.01 tick
    sl_long = quantize_stop_outward(anchor_price=99.00, buffer_price=0.50, direction="LONG", tick_size=0.01)
    assert sl_long == 98.50

    # 0.25 tick size grid:
    # SHORT: anchor 101.03 + buffer 0.50 = 101.53 -> ceil to 0.25 grid -> 101.75 (NOT 101.50)
    sl_short_25 = quantize_stop_outward(anchor_price=101.03, buffer_price=0.50, direction="SHORT", tick_size=0.25)
    assert sl_short_25 == 101.75
    assert sl_short_25 >= 101.53

    # LONG: anchor 98.97 - buffer 0.50 = 98.47 -> floor to 0.25 grid -> 98.25
    sl_long_25 = quantize_stop_outward(anchor_price=98.97, buffer_price=0.50, direction="LONG", tick_size=0.25)
    assert sl_long_25 == 98.25
    assert sl_long_25 <= 98.47

    # Non-finite or invalid input rejects
    with pytest.raises(ValueError):
        quantize_stop_outward(anchor_price=float("nan"), buffer_price=0.5, direction="SHORT", tick_size=0.01)
    with pytest.raises(ValueError):
        quantize_stop_outward(anchor_price=100.0, buffer_price=-1.0, direction="SHORT", tick_size=0.01)


def test_compute_stop_buffer_dominant_source():
    """PHẦN 051: Buffer is max of components, records dominant source."""
    policy = StopPlacementPolicy(
        mode="PAPER_STRUCTURAL",
        version=1,
        min_buffer_ticks=5,
        atr_m1_multiplier=0.25,
        atr_m5_multiplier=0.10,
        spread_multiplier=1.50,
    )
    atr_m1 = StopATRResult(value=2.0, status="AVAILABLE")  # 0.25 * 2.0 = 0.50
    atr_m5 = StopATRResult(value=4.0, status="AVAILABLE")  # 0.10 * 4.0 = 0.40
    # spread = 0.20 -> 1.5 * 0.20 = 0.30
    # tick_floor = 5 * 0.01 = 0.05
    # max is atr_m1 (0.50)

    buf = compute_stop_buffer(
        policy=policy,
        tick_size=0.01,
        atr_m1=atr_m1,
        atr_m5=atr_m5,
        bid=100.0,
        ask=100.20,
    )

    assert pytest.approx(buf.buffer_price, rel=1e-4) == 0.50
    assert buf.dominant_source == "atr_m1"
    assert pytest.approx(buf.components["tick_floor"]) == 0.05
    assert pytest.approx(buf.components["atr_m1"]) == 0.50
    assert pytest.approx(buf.components["atr_m5"]) == 0.40
    assert pytest.approx(buf.components["spread"]) == 0.30


def test_plan_structural_stop_hand_calculated_fixture():
    """
    Appendix B Hand-calculated Fixture:
    SHORT: entry 100, target 94, anchor 101, tick 0.01, ATR_M1=2, ATR_M5=4, spread 0.20
    Tick floor 0.05, M1 0.50, M5 0.40, spread 0.30 -> buffer 0.50 -> C stop 101.50.
    """
    policy = StopPlacementPolicy(
        mode="PAPER_STRUCTURAL",
        version=1,
        min_buffer_ticks=5,
        atr_m1_multiplier=0.25,
        atr_m5_multiplier=0.10,
        spread_multiplier=1.50,
    )
    anchor = StopAnchor(
        id="anc_short_1",
        kind=StopAnchorKind.PROTECTED_LEG_HIGH.value,
        price=101.0,
        timeframe="1M",
        occurred_at_ms=100_000,
        known_at_ms=160_000,
        source_leg_id="leg_1",
        source_break_id="break_1",
        price_basis=PriceBasis.CANDLE_LAST.value,
    )
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)

    atr_m1 = StopATRResult(
        value=2.0,
        status="AVAILABLE",
        bars_count=15,
        source_candle_count=15,
        timeframe="1M",
        symbol="XAUUSDT",
        last_closed_at_ms=190_000,
        last_closed_candle_timestamp=190_000,
        calculated_as_of=200_000,
        decision_cutoff=200_000,
        method="SMA_TR_V1",
        period=14,
    )
    atr_m5 = StopATRResult(
        value=4.0,
        status="AVAILABLE",
        bars_count=15,
        source_candle_count=15,
        timeframe="5M",
        symbol="XAUUSDT",
        last_closed_at_ms=190_000,
        last_closed_candle_timestamp=190_000,
        calculated_as_of=200_000,
        decision_cutoff=200_000,
        method="SMA_TR_V1",
        period=14,
    )
    buf_res = compute_stop_buffer(
        policy=policy,
        tick_size=0.01,
        atr_m1=atr_m1,
        atr_m5=atr_m5,
        bid=100.0,
        ask=100.20,
    )

    decision = plan_structural_stop(
        direction="SHORT",
        planned_entry=100.0,
        take_profit=94.0,
        legacy_sl=100.15,
        anchor_result=anchor_res,
        buffer_result=buf_res,
        policy=policy,
        as_of_ms=200_000,
        tick_size=0.01,
        atr_m1_obj=atr_m1,
        symbol="XAUUSDT",
    )

    assert decision.status == "VALID"
    assert decision.selected_sl == 101.50
    assert decision.metrics.price_distance == 1.50
    assert decision.metrics.delta_vs_legacy_sl == pytest.approx(101.50 - 100.15, rel=1e-4)
    assert decision.anchor.price == 101.0


def test_validate_anchor_not_invalidated():
    """Anchor breached before as_of_ms must be flagged as invalidated."""
    anchor = StopAnchor(
        id="anc_1",
        kind=StopAnchorKind.PROTECTED_LEG_HIGH.value,
        price=105.0,
        timeframe="1M",
        occurred_at_ms=100_000,
        known_at_ms=160_000,
        source_leg_id="leg_1",
        source_break_id=None,
        price_basis=PriceBasis.CANDLE_LAST.value,
    )

    # Candle reaches 106.0 after anchor occurred -> invalid
    bars = [
        MockBar(170_000, 104.0, 106.0, 103.0, 104.5),
    ]
    assert validate_anchor_not_invalidated(anchor, bars, "SHORT", as_of_ms=250_000) is False

    # Candle stays below 105.0 -> valid
    safe_bars = [
        MockBar(170_000, 103.0, 104.5, 102.0, 103.5),
    ]
    assert validate_anchor_not_invalidated(anchor, safe_bars, "SHORT", as_of_ms=250_000) is True
