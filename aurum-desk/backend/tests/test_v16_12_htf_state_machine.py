"""
backend/tests/test_v16_12_htf_state_machine.py
V16.12 — HTF State Machine Repair, Transition Confirmation, Range Breakout & Contract Tests.

Sections C & O:
- Repro 1: Bullish protected low break -> transition, subsequent rebound must NOT restore BULLISH with old swings.
- Repro 2: Bearish protected high break -> transition, subsequent pullback must NOT restore BEARISH with old swings.
- Repro 3: Range stuck -> breakout candle (105 -> 110) must produce RANGE_BREAK_UP event & TRANSITION, not stuck in RANGE.
- Repro 4: min_bars=100 must yield INSUFFICIENT_DATA / INSUFFICIENT_BARS in build_htf_context.
- Repro 5: H4 BULLISH / H1 TRANSITION must yield relation TRANSITION, not masked by UNKNOWN.
"""

import time
import pytest
from services.htf_structure_service import (
    filter_closed_candles,
    identify_causal_swings,
    evaluate_htf_structure,
    resolve_cg01_direction_context,
    resolve_timeframe_ms,
    HTFStructureSnapshot,
)
from services.smc_regime_service import (
    build_htf_context,
    HTFContext,
)
from tests.test_v16_11_htf_structure import CandleMock, make_bullish_candles


def make_range_candles(base_time: int = 1700000000000, tf: str = "1H"):
    """
    Creates a clear RANGE structure:
    Repeats 4 times the sequence high [101, 102, 105, 102, 101], low = 200 - high, open = close = 100.
    With high=105, low=95, open=100, close=100.
    Produces equal swing highs at 105 and equal swing lows at 95.
    """
    dur = resolve_timeframe_ms(tf)
    candles = []
    high_pattern = [101, 102, 105, 102, 101]
    full_pattern = high_pattern * 4  # 20 bars
    for i, h in enumerate(full_pattern):
        l = 200.0 - h
        o = 100.0
        c = 100.0
        t = base_time + i * dur
        candles.append(CandleMock(t, o, float(h), float(l), c, is_closed=True, tf=tf))
    return candles


def make_bearish_candles(base_time: int = 1700000000000, tf: str = "1H"):
    """
    Build bearish sequence:
    High 1 at bar 2 (2600)
    Low 1 at bar 5 (2550)
    High 2 at bar 8 (2580, LH) -> Protected High
    Low 2 at bar 11 (2530, LL)
    """
    dur = resolve_timeframe_ms(tf)
    prices = [
        (2590, 2595, 2585, 2592), # 0
        (2592, 2596, 2590, 2594), # 1
        (2594, 2600, 2590, 2595), # 2: Pivot High 1 (2600)
        (2595, 2596, 2580, 2582), # 3
        (2582, 2584, 2570, 2572), # 4: Confirms High 1
        (2572, 2575, 2550, 2555), # 5: Pivot Low 1 (2550)
        (2555, 2565, 2554, 2560), # 6
        (2560, 2570, 2558, 2565), # 7: Confirms Low 1
        (2565, 2580, 2562, 2578), # 8: Pivot High 2 (2580, LH)
        (2578, 2579, 2560, 2565), # 9
        (2565, 2568, 2550, 2552), # 10: Confirms High 2 -> protected_high=2580
        (2552, 2554, 2530, 2535), # 11: Pivot Low 2 (2530, LL)
        (2535, 2542, 2532, 2540), # 12
        (2540, 2545, 2538, 2542), # 13: Confirms Low 2
        (2542, 2548, 2538, 2545), # 14
    ]
    return [CandleMock(base_time + i * dur, o, h, l, c, is_closed=True, tf=tf) for i, (o, h, l, c) in enumerate(prices)]


# =========================================================================
# REPRO 1 (Sections 022, 023): Bullish break & false rebound
# =========================================================================
def test_reproduce_bullish_break_and_false_rebound():
    candles = make_bullish_candles()
    dur = resolve_timeframe_ms("1H")

    snap_init = evaluate_htf_structure(candles, "1H", as_of_ms=candles[-1].open_time + dur)
    assert snap_init.bias == "BULLISH"
    assert snap_init.phase == "ESTABLISHED"
    assert snap_init.protected_low is not None
    assert snap_init.protected_low["price"] == 2520.0

    # Candle breaking protected low (close=2515 < 2520)
    t_break = candles[-1].open_time + dur
    candles.append(CandleMock(t_break, 2555, 2556, 2510, 2515, is_closed=True, tf="1H"))
    snap_break = evaluate_htf_structure(candles, "1H", as_of_ms=t_break + dur)
    assert snap_break.bias == "UNKNOWN"
    assert snap_break.phase == "TRANSITION"
    assert snap_break.latest_structure_event["event_type"] == "PROTECTED_LOW_BREAK"

    # Subsequent rebound bar (close=2530 > 2520) WITHOUT new structural confirmation
    t_rebound = t_break + dur
    candles.append(CandleMock(t_rebound, 2515, 2535, 2512, 2530, is_closed=True, tf="1H"))
    snap_rebound = evaluate_htf_structure(candles, "1H", as_of_ms=t_rebound + dur)

    # BUG IN BASELINE: Baseline resets bias to BULLISH ESTABLISHED using old swings!
    # Expected behavior under repair: MUST NOT become BULLISH ESTABLISHED with old swings.
    assert snap_rebound.bias != "BULLISH", "Faulty rebound: engine restored BULLISH without new causal swings!"
    assert snap_rebound.phase == "TRANSITION", "Expected TRANSITION to be maintained until new causal structure confirms"


# =========================================================================
# REPRO 2 (Section 025): Bearish break & false recovery
# =========================================================================
def test_reproduce_bearish_break_and_false_recovery():
    candles = make_bearish_candles()
    dur = resolve_timeframe_ms("1H")

    snap_init = evaluate_htf_structure(candles, "1H", as_of_ms=candles[-1].open_time + dur)
    assert snap_init.bias == "BEARISH"
    assert snap_init.phase == "ESTABLISHED"
    assert snap_init.protected_high is not None
    assert snap_init.protected_high["price"] == 2580.0

    # Candle breaking protected high (close=2585 > 2580)
    t_break = candles[-1].open_time + dur
    candles.append(CandleMock(t_break, 2545, 2590, 2540, 2585, is_closed=True, tf="1H"))
    snap_break = evaluate_htf_structure(candles, "1H", as_of_ms=t_break + dur)
    assert snap_break.bias == "UNKNOWN"
    assert snap_break.phase == "TRANSITION"
    assert snap_break.latest_structure_event["event_type"] == "PROTECTED_HIGH_BREAK"

    # Subsequent downward bar (close=2575 < 2580) WITHOUT new structural confirmation
    t_down = t_break + dur
    candles.append(CandleMock(t_down, 2585, 2586, 2570, 2575, is_closed=True, tf="1H"))
    snap_down = evaluate_htf_structure(candles, "1H", as_of_ms=t_down + dur)

    assert snap_down.bias != "BEARISH", "Faulty recovery: engine restored BEARISH without new causal swings!"
    assert snap_down.phase == "TRANSITION", "Expected TRANSITION to be maintained"


# =========================================================================
# REPRO 3 (Sections 026, 027): Range stuck & breakout
# =========================================================================
def test_reproduce_range_stuck_and_breakout():
    candles = make_range_candles()
    dur = resolve_timeframe_ms("1H")

    snap_init = evaluate_htf_structure(candles, "1H", as_of_ms=candles[-1].open_time + dur)
    assert snap_init.bias == "RANGE"
    assert snap_init.phase == "RANGE"
    assert snap_init.reference_high is not None
    assert snap_init.reference_high["price"] == 105.0

    # Breakout bar: close=110 > 105
    t_break = candles[-1].open_time + dur
    candles.append(CandleMock(t_break, 100, 112, 99, 110, is_closed=True, tf="1H"))
    snap_break = evaluate_htf_structure(candles, "1H", as_of_ms=t_break + dur)

    # BUG IN BASELINE: Baseline remains RANGE with latest_structure_event=None
    assert snap_break.phase == "TRANSITION", f"Expected TRANSITION on range breakout, got {snap_break.phase}"
    assert snap_break.latest_structure_event is not None, "Range breakout must produce latest_structure_event"
    assert snap_break.latest_structure_event["event_type"] == "RANGE_BREAK_UP"
    assert snap_break.latest_structure_event["reference_price"] == 105.0
    assert snap_break.latest_structure_event["close_price"] == 110.0


# =========================================================================
# REPRO 4 (Section 028): min_bars ignored in build_htf_context
# =========================================================================
def test_reproduce_min_bars_ignored():
    candles = make_bullish_candles()  # 15 bars
    dur = resolve_timeframe_ms("1H")
    as_of = candles[-1].open_time + dur

    # Caller requests min_bars=100
    ctx = build_htf_context(candles, "1H", as_of_ms=as_of, min_bars=100)

    # BUG IN BASELINE: build_htf_context returns BULLISH ESTABLISHED because evaluate_htf_structure was called with min_bars=12 default
    assert ctx.phase == "INSUFFICIENT_DATA"
    assert ctx.bias == "UNKNOWN"
    assert ctx.provenance == "INSUFFICIENT_BARS"
    assert ctx.data_status == "INSUFFICIENT_DATA"


# =========================================================================
# REPRO 5 (Section 029): relation transition masked by UNKNOWN
# =========================================================================
def test_reproduce_relation_transition():
    h4_ctx = HTFContext(
        timeframe="4H",
        bias="BULLISH",
        last_confirmed_bias="BULLISH",
        phase="ESTABLISHED",
        provenance="CONFIRMED_SWING_SEQUENCE",
        data_status="READY",
    )
    # H1 is in TRANSITION after a break with sufficient data
    h1_ctx = HTFContext(
        timeframe="1H",
        bias="UNKNOWN",
        last_confirmed_bias="BULLISH",
        phase="TRANSITION",
        provenance="TRANSITION_AFTER_PROTECTED_BREAK",
        data_status="READY",
    )

    res = resolve_cg01_direction_context(h4_ctx=h4_ctx, h1_ctx=h1_ctx)

    # BUG IN BASELINE: Baseline checks h1_bias in ("UNKNOWN",) first -> relation="UNKNOWN"
    assert res["relation"] == "TRANSITION", f"Expected relation 'TRANSITION', got '{res['relation']}'"
    assert res["readiness"] == "READY"
    assert set(res["directions_to_evaluate"]) == {"LONG", "SHORT"}
    assert "H1_STRUCTURE_TRANSITION" in res["reason_codes"]


# =========================================================================
# TEST 6 (Section 078, 144): Valid recovery after invalidation
# =========================================================================
def test_valid_recovery_after_bullish_invalidation():
    candles = make_bullish_candles()
    dur = resolve_timeframe_ms("1H")

    # Invalidate bullish trend
    t_break = candles[-1].open_time + dur
    candles.append(CandleMock(t_break, 2555, 2556, 2514, 2515, is_closed=True, tf="1H"))
    snap_break = evaluate_htf_structure(candles, "1H", as_of_ms=t_break + dur)
    assert snap_break.phase == "TRANSITION"

    # Now append a brand new sequence forming post-invalidation:
    # Low 1 at 2505, High 1 at 2540, Low 2 at 2515 (HL > 2505), High 2 at 2560 (HH > 2540)
    # plus bar 29 closing at 2565 > 2560 (genuine close break!)
    new_prices = [
        (2515, 2520, 2512, 2516),  # 16
        (2516, 2520, 2505, 2508),  # 17: Swing Low 1 at 2505
        (2508, 2522, 2510, 2518),  # 18
        (2518, 2530, 2515, 2525),  # 19: Confirms Low 1
        (2525, 2540, 2522, 2535),  # 20: Swing High 1 at 2540
        (2535, 2536, 2522, 2525),  # 21
        (2525, 2528, 2518, 2520),  # 22: Confirms High 1
        (2520, 2525, 2515, 2522),  # 23: Swing Low 2 at 2515 (> 2505)
        (2522, 2535, 2520, 2530),  # 24
        (2530, 2545, 2528, 2540),  # 25: Confirms Low 2
        (2540, 2560, 2538, 2555),  # 26: Swing High 2 at 2560 (> 2540)
        (2555, 2558, 2545, 2550),  # 27
        (2550, 2552, 2540, 2545),  # 28: Confirms High 2
        (2545, 2570, 2542, 2565),  # 29: True break closing at 2565 > 2560
    ]
    cur_t = t_break
    for o, h, l, c in new_prices:
        cur_t += dur
        candles.append(CandleMock(cur_t, o, h, l, c, is_closed=True, tf="1H"))

    snap_recovered = evaluate_htf_structure(candles, "1H", as_of_ms=cur_t + dur)
    assert snap_recovered.bias == "BULLISH"
    assert snap_recovered.phase == "ESTABLISHED"
    assert snap_recovered.protected_low is not None
    assert snap_recovered.protected_low["price"] == 2515.0
    assert snap_recovered.latest_structure_event["event_type"] in ("STRUCTURE_RECONFIRMED", "BOS", "CHOCH")


# =========================================================================
# TEST 7 (Section 084, 088, 146): Range breakdown, reentry and second breakout
# =========================================================================
def test_range_breakdown_reentry_and_second_breakout():
    candles = make_range_candles()
    dur = resolve_timeframe_ms("1H")

    # 1. Breakdown bar: close=90 < 95
    t1 = candles[-1].open_time + dur
    candles.append(CandleMock(t1, 100, 101, 88, 90, is_closed=True, tf="1H"))
    s1 = evaluate_htf_structure(candles, "1H", as_of_ms=t1 + dur)
    assert s1.phase == "TRANSITION"
    assert s1.latest_structure_event["event_type"] == "RANGE_BREAK_DOWN"
    assert s1.latest_structure_event["reference_price"] == 95.0

    # 2. Failed breakdown: close returns back inside range (close=98)
    t2 = t1 + dur
    candles.append(CandleMock(t2, 90, 100, 89, 98, is_closed=True, tf="1H"))
    s2 = evaluate_htf_structure(candles, "1H", as_of_ms=t2 + dur)
    assert s2.phase == "RANGE"
    assert s2.bias == "RANGE"
    assert s2.latest_structure_event["event_type"] == "RANGE_REENTRY"

    # 3. New breakout upwards: close=110 > 105
    t3 = t2 + dur
    candles.append(CandleMock(t3, 98, 112, 97, 110, is_closed=True, tf="1H"))
    s3 = evaluate_htf_structure(candles, "1H", as_of_ms=t3 + dur)
    assert s3.phase == "TRANSITION"
    assert s3.latest_structure_event["event_type"] == "RANGE_BREAK_UP"
    assert s3.latest_structure_event["reference_price"] == 105.0


# =========================================================================
# TEST 8 (Section 035, 147): Closed boundary exact causality
# =========================================================================
def test_closed_boundary_exact():
    candles = make_bullish_candles()
    dur = resolve_timeframe_ms("1H")
    last_bar = candles[-1]
    last_close_ms = last_bar.open_time + dur

    # At last_close_ms - 1 ms, the last bar is NOT yet closed
    closed_before = filter_closed_candles(candles, "1H", as_of_ms=last_close_ms - 1)
    assert len(closed_before) == len(candles) - 1

    # At exact last_close_ms, the last bar IS closed
    closed_exact = filter_closed_candles(candles, "1H", as_of_ms=last_close_ms)
    assert len(closed_exact) == len(candles)


# =========================================================================
# TEST 9 (Section 039, 150): Settings resolution and validation
# =========================================================================
def test_settings_resolution_and_overrides():
    from services.htf_structure_service import resolve_htf_settings, HTFSettings

    # Default
    s_def = resolve_htf_settings()
    assert s_def.min_bars == 12
    assert s_def.left_bars == 2
    assert s_def.right_bars == 2
    assert s_def.tick_size == 0.01
    assert s_def.break_buffer_ticks == 0

    # Overrides
    s_ov = resolve_htf_settings(min_bars=20, left_bars=3, break_buffer_ticks=5)
    assert s_ov.min_bars == 20
    assert s_ov.left_bars == 3
    assert s_ov.break_buffer_ticks == 5

    # Reject boolean as int
    with pytest.raises(ValueError, match="INVALID_SETTING"):
        resolve_htf_settings(min_bars=True)  # type: ignore

    # Reject negative or zero min_bars
    with pytest.raises(ValueError, match="INVALID_SETTING"):
        resolve_htf_settings(min_bars=0)


# =========================================================================
# TEST 10 (Section 100): Direction context relation matrix
# =========================================================================
@pytest.mark.parametrize(
    "h4_bias,h4_phase,h1_bias,h1_phase,exp_relation,exp_readiness,exp_dirs",
    [
        ("BULLISH", "ESTABLISHED", "BULLISH", "ESTABLISHED", "ALIGNED", "READY", ["LONG"]),
        ("BEARISH", "ESTABLISHED", "BEARISH", "ESTABLISHED", "ALIGNED", "READY", ["SHORT"]),
        ("BEARISH", "ESTABLISHED", "BULLISH", "ESTABLISHED", "DIVERGENT", "READY", ["LONG"]),
        ("BULLISH", "ESTABLISHED", "BEARISH", "ESTABLISHED", "DIVERGENT", "READY", ["SHORT"]),
        ("BULLISH", "ESTABLISHED", "RANGE", "RANGE", "MIXED", "READY", ["LONG", "SHORT"]),
        ("BULLISH", "ESTABLISHED", "UNKNOWN", "TRANSITION", "TRANSITION", "READY", ["LONG", "SHORT"]),
        ("UNKNOWN", "INSUFFICIENT_DATA", "UNKNOWN", "INSUFFICIENT_DATA", "UNKNOWN", "NOT_READY", []),
    ]
)
def test_relation_matrix(h4_bias, h4_phase, h1_bias, h1_phase, exp_relation, exp_readiness, exp_dirs):
    h4 = HTFContext(timeframe="4H", bias=h4_bias, phase=h4_phase, data_status="NO_DATA" if h4_phase == "INSUFFICIENT_DATA" else "READY")
    h1 = HTFContext(timeframe="1H", bias=h1_bias, phase=h1_phase, data_status="NO_DATA" if h1_phase == "INSUFFICIENT_DATA" else "READY")

    ctx = resolve_cg01_direction_context(h4, h1)
    assert ctx["relation"] == exp_relation
    assert ctx["readiness"] == exp_readiness
    assert sorted(ctx["directions_to_evaluate"]) == sorted(exp_dirs)

