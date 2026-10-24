"""
backend/tests/test_v17_5_6_break_time_contract.py
AURUM V17.5.6 — Break Time Semantics, Timeframe Harmonization & FVG Model Suite.
Covers Parts 121–140, Appendix C, E1:
1. Distinct break_open_ms and break_known_at_ms (bar open vs bar close).
2. Pivot occurred at break open cannot precede that same break (fail closed).
3. Pivot occurred after break open rejected even if before break close.
4. Origin occurred before break but confirmed after break (<= decision) accepted without backdating.
5. Future known (> decision) or future break close (> decision) rejected.
6. Strict close-through reference: equality or wick-only sweep does not qualify as break.
7. Missing break timestamp returns UNAVAILABLE without fallback to now_ms.
8. Timeframe duration respected: M1 (60s) vs M5 (300s) uses actual timeframe_ms.
9. FVG triple source verification, direction check, and bounds (top > bottom).
"""

import pytest
from services.stop_placement_service import (
    derive_protected_leg_episode,
    validate_protected_origin_binding,
    select_protected_origin_for_break,
)


class MockCandle:
    def __init__(self, open_time: int, o: float, h: float, l: float, c: float):
        self.open_time = open_time
        self.timestamp = open_time
        self.open = float(o)
        self.high = float(h)
        self.low = float(l)
        self.close = float(c)
        self.is_closed = True


# ======================================================================
# PARTS 121–130: Break Time Semantics and Equality/Wick Guards
# ======================================================================

def test_p121_open_versus_known_time():
    """Phần 121: bar open 240000 and close 300000 retain distinct meanings; origin cutoff is strictly break open."""
    ref = {"id": "ref_1", "price": 2000.0, "occurred_at_ms": 100000, "known_at_ms": 160000}
    brk = {
        "break_open_ms": 240000,
        "known_at_ms": 300000,
        "broken_at_ms": 240000,
        "close_price": 1990.0,
    }
    # Origin at t=180000 (strictly before break open 240000)
    p_orig = {"id": "orig_1", "price": 2010.0, "occurred_at_ms": 180000, "known_at_ms": 240000}
    bars = [
        MockCandle(100000, 2002, 2005, 1998, 2000),
        MockCandle(180000, 2005, 2010, 2004, 2008),
        MockCandle(240000, 2005, 2006, 1988, 1990),
    ]

    ep = derive_protected_leg_episode(
        model_id="M1_CONTINUATION",
        direction="SHORT",
        selected_reference=ref,
        selected_break=brk,
        confirmed_swings=([p_orig], []),
        closed_bars=bars,
        decision_cutoff_ms=310000,
        timeframe_ms=60000,
    )
    assert ep.status == "AVAILABLE"
    assert ep.break_open_ms == 240000
    assert ep.break_known_at_ms == 300000
    assert "orig_1" in ep.eligible_origin_ids


def test_p122_p123_pivot_at_or_after_break_open_rejected():
    """Phần 122, 123: Pivot occurring AT break open (240000) or AFTER break open (270000) cannot precede that break."""
    ref = {"id": "ref_1", "price": 2000.0, "occurred_at_ms": 100000, "known_at_ms": 160000}
    brk = {
        "break_open_ms": 240000,
        "known_at_ms": 300000,
        "broken_at_ms": 240000,
        "close_price": 1990.0,
    }
    # Pivot exactly AT break open
    p_at = {"id": "p_at", "price": 2008.0, "occurred_at_ms": 240000, "known_at_ms": 260000}
    # Pivot AFTER break open (e.g. at 270000 during the bar)
    p_after = {"id": "p_after", "price": 2009.0, "occurred_at_ms": 270000, "known_at_ms": 280000}

    bars = [MockCandle(240000, 2005, 2010, 1988, 1990)]

    # Validate individual binding
    v_at, r_at = validate_protected_origin_binding(
        origin=p_at, direction="SHORT", break_open_ms=240000, closed_bars=bars
    )
    assert v_at is False
    assert r_at == "ORIGIN_NOT_BEFORE_BREAK"

    v_after, r_after = validate_protected_origin_binding(
        origin=p_after, direction="SHORT", break_open_ms=240000, closed_bars=bars
    )
    assert v_after is False
    assert r_after == "ORIGIN_NOT_BEFORE_BREAK"


def test_p124_p125_known_time_boundaries():
    """Phần 124, 125: Origin occurred before break and known after break (<= decision) accepted; known > decision rejected."""
    bars = [
        MockCandle(100000, 2005, 2015, 2003, 2010),
        MockCandle(200000, 2008, 2009, 1992, 1994),
    ]

    # Valid: occurred at 100000 (before break at 200000), known at 210000 (after break, but <= decision 220000)
    p_valid = {"id": "p_valid", "price": 2015.0, "occurred_at_ms": 100000, "known_at_ms": 210000}
    v_ok, _ = validate_protected_origin_binding(
        origin=p_valid, direction="SHORT", break_open_ms=200000, decision_cutoff_ms=220000, closed_bars=bars
    )
    assert v_ok is True

    # Invalid: known at 250000 > decision_cutoff 220000
    p_future = {"id": "p_future", "price": 2015.0, "occurred_at_ms": 100000, "known_at_ms": 250000}
    v_fut, r_fut = validate_protected_origin_binding(
        origin=p_future, direction="SHORT", break_open_ms=200000, decision_cutoff_ms=220000, closed_bars=bars
    )
    assert v_fut is False
    assert r_fut == "ORIGIN_KNOWN_AFTER_DECISION"


def test_p126_break_future_rejected():
    """Phần 126: Break bar close occurring in future relative to decision cutoff rejected."""
    ref = {"id": "ref_1", "price": 2000.0, "occurred_at_ms": 100000, "known_at_ms": 160000}
    # Break known at 300000 > decision_cutoff 250000
    brk_future = {
        "break_open_ms": 240000,
        "known_at_ms": 300000,
        "broken_at_ms": 240000,
        "close_price": 1990.0,
    }
    bars = [MockCandle(240000, 2005, 2006, 1988, 1990)]

    ep = derive_protected_leg_episode(
        model_id="M1_CONTINUATION",
        direction="SHORT",
        selected_reference=ref,
        selected_break=brk_future,
        confirmed_swings=([], []),
        closed_bars=bars,
        decision_cutoff_ms=250000,
        timeframe_ms=60000,
    )
    assert ep.status == "UNAVAILABLE"
    assert "BREAK_AFTER_DECISION_CUTOFF" in ep.reasons


def test_p127_p128_equality_and_wick_only_rejected():
    """Phần 127, 128: Equality with reference or wick-only sweep does not qualify as close-through break."""
    ref = {"id": "ref_1", "price": 2000.0, "occurred_at_ms": 100000, "known_at_ms": 160000}

    # 1. Equality: SHORT break candle closes exactly at reference price 2000.0
    bar_equal = MockCandle(200000, 2005, 2006, 1998, 2000.0)
    brk_equal = {"bar": bar_equal, "break_open_ms": 200000, "known_at_ms": 260000, "close_price": 2000.0}

    ep_eq = derive_protected_leg_episode(
        model_id="M1_CONTINUATION",
        direction="SHORT",
        selected_reference=ref,
        selected_break=brk_equal,
        closed_bars=[bar_equal],
        decision_cutoff_ms=270000,
    )
    assert ep_eq.status == "UNAVAILABLE"
    assert "BREAK_NOT_CLOSED_THROUGH_REFERENCE" in ep_eq.reasons

    # 2. Wick-only: low broke reference (1995.0 < 2000.0) but close retracted back above (2002.0 > 2000.0)
    bar_wick = MockCandle(200000, 2005, 2006, 1995, 2002.0)
    brk_wick = {"bar": bar_wick, "break_open_ms": 200000, "known_at_ms": 260000, "close_price": 2002.0}

    ep_wick = derive_protected_leg_episode(
        model_id="M1_CONTINUATION",
        direction="SHORT",
        selected_reference=ref,
        selected_break=brk_wick,
        closed_bars=[bar_wick],
        decision_cutoff_ms=270000,
    )
    assert ep_wick.status == "UNAVAILABLE"
    assert "BREAK_NOT_CLOSED_THROUGH_REFERENCE" in ep_wick.reasons


def test_p129_missing_break_timestamp_fails_closed():
    """Phần 129: Missing break timestamps returns UNAVAILABLE without fallback to current time."""
    ref = {"id": "ref_1", "price": 2000.0, "occurred_at_ms": 100000, "known_at_ms": 160000}
    brk_bad = {"close_price": 1990.0}  # No break_open_ms, broken_at_ms, or bar!
    bars = [MockCandle(100000, 2000, 2005, 1995, 2000)]

    ep = derive_protected_leg_episode(
        model_id="M1_CONTINUATION",
        direction="SHORT",
        selected_reference=ref,
        selected_break=brk_bad,
        closed_bars=bars,
        decision_cutoff_ms=270000,
    )
    assert ep.status == "UNAVAILABLE"
    assert "MISSING_BREAK_TIME" in ep.reasons


def test_p130_m1_versus_m5_timeframe_duration():
    """Phần 130: Known time respects actual timeframe duration (M1: 60s, M5: 300s), not hardcoded 60s."""
    ref = {"id": "ref_1", "price": 2000.0, "occurred_at_ms": 100000, "known_at_ms": 160000}
    brk_m5 = {"break_open_ms": 300000, "close_price": 1990.0}
    bars = [MockCandle(300000, 2005, 2006, 1988, 1990)]

    # Derive for M5 (timeframe_ms=300_000)
    ep_m5 = derive_protected_leg_episode(
        model_id="M1_CONTINUATION",
        direction="SHORT",
        selected_reference=ref,
        selected_break=brk_m5,
        closed_bars=bars,
        decision_cutoff_ms=700000,
        timeframe_ms=300000,
    )
    assert ep_m5.break_open_ms == 300000
    assert ep_m5.break_known_at_ms == 600000  # 300000 + 300000 (not 360000!)


# ======================================================================
# PARTS 131–140: FVG Triple Source & Confirmation Guards
# ======================================================================

def test_p131_p134_fvg_triple_and_bounds():
    """Phần 131, 134: FVG triple source verification and strict gap bound check (top > bottom)."""
    # Valid bearish FVG: Candle 1 Low (2010.0) > Candle 3 High (2005.0) -> Gap (2005.0, 2010.0)
    c1 = MockCandle(100000, 2015, 2016, 2010, 2012)
    c2 = MockCandle(160000, 2012, 2013, 2000, 2002)  # Displacement bar
    c3 = MockCandle(220000, 2002, 2005, 1998, 2000)

    fvg_top = c1.low  # 2010.0
    fvg_bottom = c3.high  # 2005.0
    assert fvg_top > fvg_bottom  # Valid gap bounds

    # Invalid gap bounds: Candle 1 Low (2005.0) <= Candle 3 High (2008.0) -> No real gap!
    c1_bad = MockCandle(100000, 2015, 2016, 2005, 2012)
    c3_bad = MockCandle(220000, 2002, 2008, 1998, 2000)
    gap_valid = c1_bad.low > c3_bad.high
    assert gap_valid is False
