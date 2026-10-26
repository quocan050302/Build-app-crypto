"""
backend/tests/test_v16_13_htf_confirmation.py
V16.13 — HTF Structural Confirmation, True Close Break, Causal Timing & Invalidation Cutoff.

Sections C & O:
- Repro 1: CHOCH / BOS bullish must NOT trigger when candle close is below reference high (Rules 022, 024, 041, 042).
- Repro 2: CHOCH / BOS bearish must NOT trigger when candle close is above reference low (Rules 023, 043).
- Repro 3: Event price predicate consistency: close_price must exceed reference_price for bullish break, and be below reference_price for bearish break (Rules 024, 049).
- Repro 4: Pivot occurring before invalidation but confirmed late (known_at > cutoff) must NOT be counted as a new post-invalidation swing (Rules 025, 026, 053).
- Repro 5: Break buffer must be strictly enforced when configured (Rule 027).
- Repro 6: Valid breakout with true close break successfully confirms structure (Rules 057, 078, 142).
"""

import pytest
from services.htf_structure_service import (
    evaluate_htf_structure,
    resolve_timeframe_ms,
    HTFSettings,
)
from services.cg01_types import CG01Config
from tests.test_v16_11_htf_structure import CandleMock, make_bullish_candles


def build_invalidated_bullish_fixture():
    """
    Creates a bullish sequence invalidated at bar 15 by closing at 2515 below protected low 2520.
    Returns (candles, t_break, dur).
    """
    candles = make_bullish_candles()
    dur = resolve_timeframe_ms("1H")
    t_break = candles[-1].open_time + dur
    # Invalidation bar: close=2515 < 2520 (protected low), low=2514
    candles.append(CandleMock(t_break, 2555, 2556, 2514, 2515, is_closed=True, tf="1H"))
    return candles, t_break, dur


# =========================================================================
# REPRO 1 & 3: CHOCH Bullish without actual close break above reference
# =========================================================================
def test_reproduce_choch_bullish_without_actual_close_break():
    """
    Rule 022, 024, 041, 042:
    After invalidation, a new sequence forms with High 2 at 2560.
    A subsequent bar closes at 2550 (BELOW 2560).
    BASELINE BUG: It declares STRUCTURE_RECONFIRMED / CHOCH with reference_price=2560,
    even though close_price=2550 < 2560!
    EXPECTED IN V16.13: State must remain TRANSITION until a bar actually closes > 2560.
    """
    candles, t_break, dur = build_invalidated_bullish_fixture()

    # Sequence forming strictly post-invalidation:
    # Low 1 at 2505 (bar 17)
    # High 1 at 2540 (bar 20)
    # Low 2 at 2515 (bar 23, HL > 2505)
    # High 2 at 2560 (bar 26, HH > 2540)
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
        (2545, 2555, 2540, 2550),  # 29: Close=2550 (STILL BELOW 2560!)
    ]
    cur_t = t_break
    for o, h, l, c in new_prices:
        cur_t += dur
        candles.append(CandleMock(cur_t, o, h, l, c, is_closed=True, tf="1H"))

    snap = evaluate_htf_structure(candles, "1H", as_of_ms=cur_t + dur)

    # In baseline, snap.phase was falsely ESTABLISHED and event says reference_price=2560, close_price=2550
    assert snap.phase == "TRANSITION", (
        f"Expected phase TRANSITION because close (2550) never broke reference high (2560), got {snap.phase}"
    )
    if snap.latest_structure_event:
        evt = snap.latest_structure_event
        if evt.get("direction") == "BULLISH" and evt.get("event_type") in ("CHOCH", "STRUCTURE_RECONFIRMED", "BOS"):
            assert evt["close_price"] > evt["reference_price"], (
                f"Contradiction: Bullish break event has close_price {evt['close_price']} <= reference_price {evt['reference_price']}"
            )


# =========================================================================
# REPRO 2: CHOCH Bearish without actual close break below reference
# =========================================================================
def test_reproduce_choch_bearish_without_actual_close_break():
    """
    Rule 023, 043:
    Bearish structural break must require close < reference_low.
    A candle closing above reference low must NOT trigger a bearish structural break event.
    """
    dur = resolve_timeframe_ms("1H")
    base_t = 1700000000000

    # Start with established bearish sequence invalidated to TRANSITION
    from tests.test_v16_12_htf_state_machine import make_bearish_candles
    candles = make_bearish_candles(base_t, tf="1H")
    t_break = candles[-1].open_time + dur
    # Invalidation bar: close=2585 > 2580 (protected high)
    candles.append(CandleMock(t_break, 2570, 2590, 2568, 2585, is_closed=True, tf="1H"))

    # Now add sequence aiming for bearish confirmation:
    # High 1 at 2585, Low 1 at 2540, High 2 at 2570 (LH), Low 2 at 2520 (LL)
    new_prices = [
        (2585, 2588, 2575, 2580), # 13
        (2580, 2582, 2570, 2572), # 14
        (2572, 2575, 2565, 2568), # 15: Confirms High 1 (2588)
        (2568, 2570, 2540, 2545), # 16: Low 1 at 2540
        (2545, 2560, 2542, 2555), # 17
        (2555, 2565, 2550, 2560), # 18: Confirms Low 1 (2540)
        (2560, 2570, 2555, 2565), # 19: High 2 at 2570 (LH < 2588)
        (2565, 2568, 2550, 2552), # 20
        (2552, 2555, 2530, 2535), # 21: Confirms High 2 (2570)
        (2535, 2540, 2520, 2525), # 22: Low 2 at 2520 (LL < 2540)
        (2525, 2535, 2522, 2530), # 23
        (2530, 2535, 2525, 2528), # 24: Confirms Low 2 (2520)
        (2528, 2535, 2525, 2530), # 25: Close=2530 (ABOVE Low 2 at 2520!)
    ]
    cur_t = t_break
    for o, h, l, c in new_prices:
        cur_t += dur
        candles.append(CandleMock(cur_t, o, h, l, c, is_closed=True, tf="1H"))

    snap = evaluate_htf_structure(candles, "1H", as_of_ms=cur_t + dur)
    assert snap.phase == "TRANSITION", (
        f"Expected phase TRANSITION because close (2530) never broke reference low (2520), got {snap.phase}"
    )


# =========================================================================
# REPRO 4: Old pivot with late confirmation (occurred < cutoff, known > cutoff)
# =========================================================================
def test_reproduce_pivot_occurring_before_invalidation_cannot_be_post_invalidation_swing():
    """
    Rules 025, 026, 053:
    A swing that OCCURRED before invalidation cutoff (bar 14) but was only CONFIRMED
    after invalidation (at bar 16, because right_bars=2) must NOT be counted as a
    new post-invalidation pivot.
    Only pivots with occurred_at_ms > invalidation_cutoff represent genuine post-invalidation market structure.
    """
    candles, t_break, dur = build_invalidated_bullish_fixture()
    # At t_break (bar 15), invalidation happened at t_break + dur.
    # Suppose a pivot high formed at bar 14 (before t_break) and was only confirmed at bar 16.
    # Baseline checks only known_at_ms > inval_cutoff, so it includes bar 14 as post_h!
    # With V16.13: post_h must require occurred_at_ms > inval_cutoff.

    # Append 2 bars:
    candles.append(CandleMock(t_break + dur, 2515, 2525, 2512, 2520, is_closed=True, tf="1H"))
    candles.append(CandleMock(t_break + 2 * dur, 2520, 2530, 2515, 2525, is_closed=True, tf="1H"))

    snap = evaluate_htf_structure(candles, "1H", as_of_ms=t_break + 3 * dur)
    # Must remain TRANSITION; cannot assemble swings from pre-invalidation occurrences
    assert snap.phase == "TRANSITION"


# =========================================================================
# REPRO 5: Break buffer strictly enforced
# =========================================================================
def test_reproduce_break_buffer_strictly_enforced():
    """
    Rule 027, 044:
    With htf_break_buffer_ticks = 2 (buffer = 2 * tick_size = 0.02 for tick_size 0.01),
    a close that is only 0.01 above reference high does NOT count as a confirmed break.
    """
    candles, t_break, dur = build_invalidated_bullish_fixture()
    cfg = CG01Config(htf_break_buffer_ticks=2, tick_size=0.01)

    # Sequence with High 2 at 2560.0
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
        # Bar 29: Close=2560.01 (exceeds reference 2560.0 by 0.01, but buffer requires 2560.02!)
        (2545, 2561.0, 2540, 2560.01),
    ]
    cur_t = t_break
    for o, h, l, c in new_prices:
        cur_t += dur
        candles.append(CandleMock(cur_t, o, h, l, c, is_closed=True, tf="1H"))

    snap = evaluate_htf_structure(candles, "1H", as_of_ms=cur_t + dur, config=cfg)
    assert snap.phase == "TRANSITION", (
        f"Close at 2560.01 is within buffer [2560.0, 2560.02]; expected TRANSITION, got {snap.phase}"
    )


# =========================================================================
# TEST 6: True close break successfully reconfirms structure
# =========================================================================
def test_valid_close_break_confirms_structure():
    """
    Rules 057, 078, 142:
    When a genuine new sequence has formed post-invalidation and a bar
    CLOSES ABOVE reference high + buffer (e.g. close=2565 > 2560),
    structure is successfully confirmed with phase=ESTABLISHED and event.
    """
    candles, t_break, dur = build_invalidated_bullish_fixture()
    cfg = CG01Config(htf_break_buffer_ticks=0, tick_size=0.01)

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
        # Bar 29: Close=2565.0 (CLEARLY ABOVE 2560.0!)
        (2545, 2570.0, 2540, 2565.0),
    ]
    cur_t = t_break
    for o, h, l, c in new_prices:
        cur_t += dur
        candles.append(CandleMock(cur_t, o, h, l, c, is_closed=True, tf="1H"))

    snap = evaluate_htf_structure(candles, "1H", as_of_ms=cur_t + dur, config=cfg)
    assert snap.phase == "ESTABLISHED", f"Expected ESTABLISHED on true close break (2565 > 2560), got {snap.phase}"
    assert snap.bias == "BULLISH"
    assert snap.latest_structure_event is not None
    assert snap.latest_structure_event["close_price"] == 2565.0
    assert snap.latest_structure_event["reference_price"] == 2560.0
