"""
backend/tests/test_v17_5_4_anchor_proof.py
AURUM V17.5.4 — Structural Anchor SL Proof Acceptance Tests (Appendix C & Parts 111-150).
Verifies:
1. Source candle check: Missing source candle in closed_bars returns None (fails closed).
2. Tick tolerance: Enforces instrument tick metadata (not hardcoded 0.05).
3. Multi-leg & noise pivot isolation: Correct origin bound despite noise pivots, post-break pivots, and out-of-episode extremes.
4. Strict close-through: Equality and wick-only breaks return None; exception returns None.
"""

from types import SimpleNamespace
from services.stop_placement_service import (
    resolve_bound_stop_leg,
    build_selected_break_descriptor,
)


class CandleBar:
    def __init__(self, open_time, o, h, l, c):
        self.open_time = open_time
        self.timestamp = open_time
        self.open = float(o)
        self.high = float(h)
        self.low = float(l)
        self.close = float(c)
        self.is_closed = True


def test_resolve_bound_stop_leg_missing_source_candle_fails():
    """C2: If closed_bars are provided, origin pivot timestamp MUST match a closed bar; otherwise fail closed."""
    pivot = {"id": "p_valid_time", "price": 2050.0, "occurred_at_ms": 100_000, "known_at_ms": 120_000}
    break_bar = CandleBar(200_000, 2040.0, 2042.0, 2030.0, 2032.0)
    selected_break = {
        "id": "brk_1",
        "broken_at_ms": 200_000,
        "known_at_ms": 260_000,
        "bar": break_bar,
        "broken_price": 2038.0,
    }

    # 1. Closed bars provided, but DOES NOT contain candle at timestamp 100_000 (missing source candle)
    unmatched_bars = [
        CandleBar(50_000, 2045.0, 2048.0, 2042.0, 2044.0),
        break_bar,
    ]
    leg_missing = resolve_bound_stop_leg(
        model_id="SMC_M1_CONTINUATION",
        direction="SHORT",
        candidate_pivot=pivot,
        closed_bars=unmatched_bars,
        selected_break=selected_break,
        decision_cutoff_ms=300_000,
        tick_size=0.01,
    )
    assert leg_missing is None, "Missing source candle in closed_bars must return None"

    # 2. Positive twin: closed_bars contains the exact matching source candle
    matched_bars = [
        CandleBar(100_000, 2045.0, 2050.0, 2042.0, 2046.0),  # High=2050.0 matches pivot price
        break_bar,
    ]
    leg_matched = resolve_bound_stop_leg(
        model_id="SMC_M1_CONTINUATION",
        direction="SHORT",
        candidate_pivot=pivot,
        closed_bars=matched_bars,
        selected_break=selected_break,
        decision_cutoff_ms=300_000,
        tick_size=0.01,
    )
    assert leg_matched is not None, "Matched source candle must resolve valid leg"
    assert leg_matched.origin_pivot_id == "p_valid_time"
    assert leg_matched.origin_price == 2050.0


def test_resolve_bound_stop_leg_tick_tolerance_from_instrument():
    """C2: Pivot extreme matching strictly obeys instrument tick metadata (not hardcoded 0.05)."""
    break_bar = CandleBar(200_000, 2040.0, 2042.0, 2030.0, 2032.0)
    selected_break = {
        "id": "brk_1",
        "broken_at_ms": 200_000,
        "known_at_ms": 260_000,
        "bar": break_bar,
        "broken_price": 2038.0,
    }

    # SHORT: Candle high is 2050.00, but pivot price is 2050.03
    # With tick_size=0.01, delta is 0.03 > tick_size -> MUST FAIL!
    # (Under legacy 0.05, 0.03 would have erroneously passed!)
    pivot_short_mismatch = {"id": "p1", "price": 2050.03, "occurred_at_ms": 100_000, "known_at_ms": 120_000}
    bars_short = [
        CandleBar(100_000, 2045.0, 2050.00, 2042.0, 2046.0),
        break_bar,
    ]
    leg_short_fail = resolve_bound_stop_leg(
        model_id="SMC_M1_CONTINUATION",
        direction="SHORT",
        candidate_pivot=pivot_short_mismatch,
        closed_bars=bars_short,
        selected_break=selected_break,
        decision_cutoff_ms=300_000,
        tick_size=0.01,
    )
    assert leg_short_fail is None, "Delta 0.03 > tick_size 0.01 must fail extreme check"

    # Positive twin within tick_size
    pivot_short_valid = {"id": "p1", "price": 2050.005, "occurred_at_ms": 100_000, "known_at_ms": 120_000}
    leg_short_pass = resolve_bound_stop_leg(
        model_id="SMC_M1_CONTINUATION",
        direction="SHORT",
        candidate_pivot=pivot_short_valid,
        closed_bars=bars_short,
        selected_break=selected_break,
        decision_cutoff_ms=300_000,
        tick_size=0.01,
    )
    assert leg_short_pass is not None

    # LONG: Candle low is 2000.00, pivot price is 1999.96 (delta 0.04 > tick_size 0.01) -> MUST FAIL
    long_break_bar = CandleBar(200_000, 2010.0, 2025.0, 2008.0, 2022.0)
    long_break = {"id": "lbrk", "broken_at_ms": 200_000, "known_at_ms": 260_000, "bar": long_break_bar, "broken_price": 2015.0}
    pivot_long_mismatch = {"id": "pl", "price": 1999.96, "occurred_at_ms": 100_000, "known_at_ms": 120_000}
    bars_long = [
        CandleBar(100_000, 2005.0, 2010.0, 2000.00, 2008.0),
        long_break_bar,
    ]
    leg_long_fail = resolve_bound_stop_leg(
        model_id="SMC_M1_CONTINUATION",
        direction="LONG",
        candidate_pivot=pivot_long_mismatch,
        closed_bars=bars_long,
        selected_break=long_break,
        decision_cutoff_ms=300_000,
        tick_size=0.01,
    )
    assert leg_long_fail is None, "Delta 0.04 > tick_size 0.01 must fail for LONG"


def test_resolve_bound_stop_leg_multiple_legs_and_noise_isolation():
    """C1 & C5: Isolates true origin from noise pivots, out-of-episode pivots, and post-break pivots."""
    break_bar = CandleBar(200_000, 2030.0, 2032.0, 2020.0, 2022.0)
    selected_break = {
        "id": "brk_leg",
        "broken_at_ms": 200_000,
        "known_at_ms": 260_000,
        "bar": break_bar,
        "broken_price": 2025.0,
        "target_origin_id": "origin_real",
        "episode_start_ms": 90_000,
    }

    # 4 pivots:
    # - extreme_outside: occurred at 50_000 (before episode start)
    # - origin_real: occurred at 100_000 (true origin of the episode)
    # - noise_closer_invalid: occurred at 150_000, but has NO matching source candle
    # - post_break: occurred at 220_000 (after break bar)
    pivots = [
        {"id": "extreme_outside", "price": 2070.0, "occurred_at_ms": 50_000, "known_at_ms": 70_000},
        {"id": "origin_real", "price": 2050.0, "occurred_at_ms": 100_000, "known_at_ms": 120_000},
        {"id": "noise_closer_invalid", "price": 2045.0, "occurred_at_ms": 150_000, "known_at_ms": 170_000},
        {"id": "post_break", "price": 2040.0, "occurred_at_ms": 220_000, "known_at_ms": 250_000},
    ]

    closed_bars = [
        CandleBar(100_000, 2045.0, 2050.0, 2040.0, 2048.0),  # Valid source candle for origin_real
        break_bar,
    ]

    leg = resolve_bound_stop_leg(
        model_id="SMC_M1_CONTINUATION",
        direction="SHORT",
        candidate_pivots=pivots,
        closed_bars=closed_bars,
        selected_break=selected_break,
        decision_cutoff_ms=300_000,
        tick_size=0.01,
    )

    assert leg is not None
    assert leg.origin_pivot_id == "origin_real"
    assert leg.origin_price == 2050.0
    assert leg.origin_occurred_at_ms == 100_000


def test_resolve_bound_stop_leg_strict_close_through():
    """C3: Strict close-through required; equality or wick touching does NOT constitute break."""
    origin_bar = CandleBar(100_000, 2045.0, 2050.0, 2040.0, 2048.0)
    pivot = {"id": "p_origin", "price": 2050.0, "occurred_at_ms": 100_000, "known_at_ms": 120_000}

    # SHORT break reference price is 2030.00
    # Case 1: Equality close = 2030.00 -> NOT closed through -> MUST FAIL
    equality_bar = CandleBar(200_000, 2035.0, 2036.0, 2028.0, 2030.00)
    brk_equal = {
        "id": "brk_eq",
        "broken_at_ms": 200_000,
        "known_at_ms": 260_000,
        "bar": equality_bar,
        "broken_price": 2030.00,
    }
    leg_equal = resolve_bound_stop_leg(
        model_id="SMC_M1_CONTINUATION",
        direction="SHORT",
        candidate_pivot=pivot,
        closed_bars=[origin_bar, equality_bar],
        selected_break=brk_equal,
        decision_cutoff_ms=300_000,
    )
    assert leg_equal is None, "Close at equality (2030.00 == 2030.00) must return None"

    # Case 2: Wick touched 2025.00 but close is 2031.00 (above ref) -> MUST FAIL
    wick_bar = CandleBar(200_000, 2035.0, 2036.0, 2025.0, 2031.00)
    brk_wick = {
        "id": "brk_wk",
        "broken_at_ms": 200_000,
        "known_at_ms": 260_000,
        "bar": wick_bar,
        "broken_price": 2030.00,
    }
    leg_wick = resolve_bound_stop_leg(
        model_id="SMC_M1_CONTINUATION",
        direction="SHORT",
        candidate_pivot=pivot,
        closed_bars=[origin_bar, wick_bar],
        selected_break=brk_wick,
        decision_cutoff_ms=300_000,
    )
    assert leg_wick is None, "Wick-only breach without close through must return None"

    # Case 3: Confirmed close through: close is 2028.00 < 2030.00 -> MUST PASS
    strict_bar = CandleBar(200_000, 2035.0, 2036.0, 2025.0, 2028.00)
    brk_strict = {
        "id": "brk_st",
        "broken_at_ms": 200_000,
        "known_at_ms": 260_000,
        "bar": strict_bar,
        "broken_price": 2030.00,
    }
    leg_strict = resolve_bound_stop_leg(
        model_id="SMC_M1_CONTINUATION",
        direction="SHORT",
        candidate_pivot=pivot,
        closed_bars=[origin_bar, strict_bar],
        selected_break=brk_strict,
        decision_cutoff_ms=300_000,
    )
    assert leg_strict is not None
    assert leg_strict.break_bar_id == "brk_st"
