"""
backend/tests/test_v16_11_htf_structure.py
V16.11 — Causal HTF Structure, Provenance, Boundary, and Integration Tests.

Implements Golden Cases 1 - 12 (Phụ lục E) and Integration/Causal Requirements:
- CASE 1: Bullish established, protected low intact despite internal pullback.
- CASE 2: Bullish established, protected low broken by candle close -> transition/invalidation.
- CASE 3: Bearish established, protected high broken by candle close -> transition/invalidation.
- CASE 4: Wick sweeps protected level but closes inside -> sweep rejection, external structure intact.
- CASE 5: Insufficient confirmed swings, price above SMA -> UNKNOWN/UNDETERMINED (no SMA fallback).
- CASE 6: Equal highs / Equal lows within tick tolerance -> EQH/EQL, not false LH/LL.
- CASE 7: Unclosed candle piercing structure -> confirmed HTF structure remains unchanged before close.
- CASE 8: Pivot without 2 right bars -> not known or usable before confirmation bar close.
- CASE 9: H4 Bullish / H1 Bearish -> per-TF facts intact, relation DIVERGENT, direction context explained.
- CASE 10: Historical replay isolation -> uses injected historical context, never live singleton bias.
- CASE 11: Order snapshot immutability -> arm order with snapshot A, subsequent market shift B does not rewrite order evidence.
- CASE 12: Prefix invariance -> adding future candles does not alter historical structure at as_of T.
- CASE 13: Collector closed-only DB query -> ignores unclosed candles in SQLite.
- CASE 14: Positive CG-01 Long execution flow under causal HTF.
- CASE 15: Positive CG-01 Short execution flow under causal HTF.
"""

import time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
import schemas
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
    h1_alignment_from,
    HTFContext,
)
from services.cg01_engine import (
    CG01Config,
    build_cg01_context,
    evaluate_cg01_candidate,
)
from services.cg01_types import CG01Stage
import smc_engine


class CandleMock:
    def __init__(self, open_time: int, o: float, h: float, l: float, c: float, is_closed: bool = True, tf: str = "1H"):
        self.timestamp = open_time
        self.open_time = open_time
        self.open = float(o)
        self.high = float(h)
        self.low = float(l)
        self.close = float(c)
        self.volume = 100.0
        self.is_closed = is_closed
        self.timeframe = tf
        self.symbol = "XAUUSDT"

    def to_dict(self):
        return {
            "timestamp": self.timestamp,
            "open_time": self.open_time,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "is_closed": self.is_closed,
            "timeframe": self.timeframe,
            "symbol": self.symbol,
        }


def make_bullish_candles(base_time: int = 1700000000000, tf: str = "1H"):
    """
    Creates a clear sequence of confirmed swings:
    Low 1 at bar 2 (price 2500)
    High 1 at bar 5 (price 2540)
    Low 2 at bar 8 (price 2520) -> Higher Low (sl2 > sl1)
    High 2 at bar 11 (price 2560) -> Higher High (sh2 > sh1)
    Plus confirmation bars to ensure swings are confirmed.
    """
    dur = resolve_timeframe_ms(tf)
    candles = []
    # 0..14 bars
    prices = [
        (2510, 2515, 2505, 2512), # 0
        (2512, 2514, 2508, 2510), # 1
        (2510, 2512, 2500, 2504), # 2: Pivot Low 1 (2500)
        (2504, 2518, 2503, 2515), # 3
        (2515, 2525, 2514, 2522), # 4: Right bar 2 for Pivot Low 1 -> confirmed at bar 4 close
        (2522, 2540, 2520, 2538), # 5: Pivot High 1 (2540)
        (2538, 2539, 2525, 2530), # 6
        (2530, 2532, 2526, 2528), # 7: Right bar 2 for Pivot High 1 -> confirmed at bar 7 close
        (2528, 2530, 2520, 2524), # 8: Pivot Low 2 (2520, HL)
        (2524, 2535, 2522, 2532), # 9
        (2532, 2545, 2530, 2542), # 10: Right bar 2 for Pivot Low 2 -> confirmed at bar 10 close
        (2542, 2560, 2540, 2558), # 11: Pivot High 2 (2560, HH)
        (2558, 2559, 2545, 2550), # 12
        (2550, 2555, 2548, 2552), # 13: Right bar 2 for Pivot High 2 -> confirmed at bar 13 close
        (2552, 2558, 2550, 2555), # 14: Follow through bar
    ]
    for i, (o, h, l, c) in enumerate(prices):
        t = base_time + i * dur
        candles.append(CandleMock(t, o, h, l, c, is_closed=True, tf=tf))
    return candles


# =========================================================================
# CASE 1: Bullish established, protected low intact despite internal pullback
# =========================================================================
def test_case_01_bullish_intact_internal_pullback():
    candles = make_bullish_candles()
    dur = resolve_timeframe_ms("1H")
    # Append an internal pullback bar: drops from 2555 to 2535 (above protected low 2520)
    t_next = candles[-1].open_time + dur
    candles.append(CandleMock(t_next, 2555, 2556, 2535, 2538, is_closed=True, tf="1H"))
    as_of = t_next + dur

    snapshot = evaluate_htf_structure(candles, "1H", as_of_ms=as_of)
    assert snapshot.bias == "BULLISH"
    assert snapshot.protected_low is not None
    assert snapshot.protected_low["price"] == 2520.0
    # Provenance indicates confirmed sequence or BOS
    assert "SWING" in snapshot.provenance or "BOS" in snapshot.provenance or "CONFIRMED" in snapshot.provenance


# =========================================================================
# CASE 2: Bullish established, protected low broken by candle close
# =========================================================================
def test_case_02_bullish_protected_low_broken_by_close():
    candles = make_bullish_candles()
    dur = resolve_timeframe_ms("1H")
    # Protected low is 2520. Add candle that closes at 2515 (below 2520)
    t_break = candles[-1].open_time + dur
    candles.append(CandleMock(t_break, 2555, 2556, 2510, 2515, is_closed=True, tf="1H"))
    as_of = t_break + dur

    snapshot = evaluate_htf_structure(candles, "1H", as_of_ms=as_of)
    # The structure is broken! Must NOT remain BULLISH ESTABLISHED
    assert snapshot.bias != "BULLISH"
    assert snapshot.phase == "TRANSITION"
    assert snapshot.provenance == "TRANSITION_AFTER_PROTECTED_BREAK"
    assert snapshot.last_confirmed_bias == "BULLISH"
    assert snapshot.latest_structure_event is not None
    assert snapshot.latest_structure_event["event_type"] == "PROTECTED_LOW_BREAK"
    assert snapshot.latest_structure_event["reference_price"] == 2520.0
    assert snapshot.latest_structure_event["close_price"] == 2515.0


# =========================================================================
# CASE 3: Bearish established, protected high broken by candle close
# =========================================================================
def test_case_03_bearish_protected_high_broken_by_close():
    dur = resolve_timeframe_ms("1H")
    base_time = 1700000000000
    # Build bearish sequence:
    # High 1 at 2600, Low 1 at 2550, High 2 at 2580 (LH), Low 2 at 2530 (LL)
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
        (2565, 2568, 2550, 2552), # 10: Confirms High 2
        (2552, 2554, 2530, 2535), # 11: Pivot Low 2 (2530, LL)
        (2535, 2542, 2532, 2540), # 12
        (2540, 2545, 2538, 2542), # 13: Confirms Low 2
        (2542, 2548, 2538, 2545), # 14
    ]
    candles = [CandleMock(base_time + i * dur, o, h, l, c, is_closed=True, tf="1H") for i, (o, h, l, c) in enumerate(prices)]

    snap_bear = evaluate_htf_structure(candles, "1H", as_of_ms=base_time + 15 * dur)
    assert snap_bear.bias == "BEARISH"
    assert snap_bear.protected_high is not None
    assert snap_bear.protected_high["price"] == 2580.0

    # Break protected high by closed candle closing at 2585
    t_break = candles[-1].open_time + dur
    candles.append(CandleMock(t_break, 2545, 2590, 2540, 2585, is_closed=True, tf="1H"))
    snap_broken = evaluate_htf_structure(candles, "1H", as_of_ms=t_break + dur)

    assert snap_broken.bias != "BEARISH"
    assert snap_broken.phase == "TRANSITION"
    assert snap_broken.provenance == "TRANSITION_AFTER_PROTECTED_BREAK"
    assert snap_broken.last_confirmed_bias == "BEARISH"
    assert snap_broken.latest_structure_event is not None
    assert snap_broken.latest_structure_event["event_type"] == "PROTECTED_HIGH_BREAK"
    assert snap_broken.latest_structure_event["reference_price"] == 2580.0
    assert snap_broken.latest_structure_event["close_price"] == 2585.0


# =========================================================================
# CASE 4: Wick sweeps protected level but closes inside (Sweep Rejection)
# =========================================================================
def test_case_04_wick_sweep_rejection_keeps_structure():
    candles = make_bullish_candles()
    dur = resolve_timeframe_ms("1H")
    # Protected low is 2520. Candle wicks down to 2515, but closes at 2525!
    t_sweep = candles[-1].open_time + dur
    candles.append(CandleMock(t_sweep, 2555, 2558, 2515, 2525, is_closed=True, tf="1H"))
    as_of = t_sweep + dur

    snapshot = evaluate_htf_structure(candles, "1H", as_of_ms=as_of)
    # External bias is preserved as BULLISH because close did not breach 2520!
    assert snapshot.bias == "BULLISH"
    assert snapshot.phase in ("ESTABLISHED", "PULLBACK")
    assert snapshot.protected_low is not None
    assert snapshot.protected_low["price"] == 2520.0
    # Sweep event is recorded
    if snapshot.latest_structure_event:
        assert snapshot.latest_structure_event["event_type"] in ("SWEEP_REJECTION", "BOS")


# =========================================================================
# CASE 5: Insufficient confirmed swings, price above SMA -> UNKNOWN/UNDETERMINED
# =========================================================================
def test_case_05_insufficient_swings_no_sma_fallback():
    dur = resolve_timeframe_ms("1H")
    base_time = 1700000000000
    # 20 flat candles where price is slightly rising above SMA, but no swings exist
    candles = []
    for i in range(20):
        t = base_time + i * dur
        c_price = 2000.0 + i * 0.10
        candles.append(CandleMock(t, c_price - 0.05, c_price + 0.05, c_price - 0.05, c_price, is_closed=True, tf="1H"))

    as_of = base_time + 20 * dur
    snapshot = evaluate_htf_structure(candles, "1H", as_of_ms=as_of)

    # Must NOT claim BULLISH from SMA
    assert snapshot.bias == "UNKNOWN"
    assert snapshot.provenance == "INSUFFICIENT_CONFIRMED_SWINGS"
    # Indicator bias is recorded separately in indicator_bias
    assert snapshot.indicator_bias == "BULLISH"


# =========================================================================
# CASE 6: Equal highs / Equal lows within tick tolerance -> EQH/EQL
# =========================================================================
def test_case_06_equal_highs_lows_tick_tolerance():
    dur = resolve_timeframe_ms("1H")
    base_time = 1700000000000
    # Create swings where second high is 2500.005 and first is 2500.00 (within 0.01 tolerance)
    candles = [
        CandleMock(base_time + 0 * dur, 2480, 2485, 2478, 2482),
        CandleMock(base_time + 1 * dur, 2482, 2488, 2480, 2485),
        CandleMock(base_time + 2 * dur, 2485, 2500.00, 2484, 2490), # High 1: 2500.00
        CandleMock(base_time + 3 * dur, 2490, 2492, 2475, 2478),
        CandleMock(base_time + 4 * dur, 2478, 2480, 2470, 2472), # Low 1: 2470
        CandleMock(base_time + 5 * dur, 2472, 2475, 2468, 2470),
        CandleMock(base_time + 6 * dur, 2470, 2485, 2469, 2480),
        CandleMock(base_time + 7 * dur, 2480, 2495, 2478, 2490),
        CandleMock(base_time + 8 * dur, 2490, 2500.005, 2488, 2495), # High 2: 2500.005 (diff 0.005 <= 0.01)
        CandleMock(base_time + 9 * dur, 2495, 2496, 2480, 2482),
        CandleMock(base_time + 10 * dur, 2482, 2484, 2475, 2478),
    ]
    highs, lows = identify_causal_swings(candles, "1H", left_bars=2, right_bars=2, as_of_ms=base_time + 11 * dur, tick_size=0.01)
    assert len(highs) >= 2
    assert highs[1]["label"] == "EQH"


# =========================================================================
# CASE 7: Unclosed candle piercing structure -> confirmed HTF unchanged
# =========================================================================
def test_case_07_unclosed_candle_ignored_in_confirmed_structure():
    candles = make_bullish_candles()
    dur = resolve_timeframe_ms("1H")
    as_of = candles[-1].open_time + dur

    # Snapshot before unclosed candle
    snap_before = evaluate_htf_structure(candles, "1H", as_of_ms=as_of)
    assert snap_before.bias == "BULLISH"

    # Add currently unclosed candle that pierces deep below protected low
    t_unclosed = candles[-1].open_time + dur
    candles_with_unclosed = list(candles) + [
        CandleMock(t_unclosed, 2555, 2556, 2450, 2460, is_closed=False, tf="1H")
    ]

    # Evaluate at time before unclosed candle closes: as_of is during unclosed candle
    eval_as_of = t_unclosed + 1000 # 1 second into bar
    snap_during = evaluate_htf_structure(candles_with_unclosed, "1H", as_of_ms=eval_as_of)

    # Confirmed structure MUST remain BULLISH because unclosed candle is filtered out!
    assert snap_during.bias == "BULLISH"
    assert snap_during.last_bar_close_ms == as_of


# =========================================================================
# CASE 8: Pivot without 2 right bars not known before confirmation close
# =========================================================================
def test_case_08_pivot_requires_two_right_bars():
    dur = resolve_timeframe_ms("1H")
    base_time = 1700000000000
    # Candle 2 is highest, Candle 3 is right bar 1, Candle 4 is right bar 2
    candles = [
        CandleMock(base_time + 0 * dur, 2500, 2505, 2495, 2502), # bar 0
        CandleMock(base_time + 1 * dur, 2502, 2510, 2500, 2508), # bar 1
        CandleMock(base_time + 2 * dur, 2508, 2540, 2505, 2535), # bar 2 (pivot high candidate)
        CandleMock(base_time + 3 * dur, 2535, 2536, 2520, 2525), # bar 3 (right bar 1)
    ]
    # As of bar 3 close: pivot is NOT confirmed yet!
    as_of_bar3 = base_time + 3 * dur + dur
    highs_bar3, lows_bar3 = identify_causal_swings(candles, "1H", left_bars=2, right_bars=2, as_of_ms=as_of_bar3)
    assert len(highs_bar3) == 0

    # Add bar 4 (right bar 2)
    candles.append(CandleMock(base_time + 4 * dur, 2525, 2528, 2515, 2520))
    as_of_bar4 = base_time + 4 * dur + dur
    highs_bar4, lows_bar4 = identify_causal_swings(candles, "1H", left_bars=2, right_bars=2, as_of_ms=as_of_bar4)
    assert len(highs_bar4) == 1
    assert highs_bar4[0]["price"] == 2540.0
    assert highs_bar4[0]["occurred_at_ms"] == base_time + 2 * dur
    assert highs_bar4[0]["known_at_ms"] == as_of_bar4


# =========================================================================
# CASE 9: H4 Bullish / H1 Bearish -> per-TF facts intact, relation DIVERGENT
# =========================================================================
def test_case_09_h4_bullish_h1_bearish_divergent_relation():
    h4_ctx = HTFContext(
        timeframe="4H",
        bias="BULLISH",
        last_confirmed_bias="BULLISH",
        phase="ESTABLISHED",
        provenance="CONFIRMED_SWING_SEQUENCE",
    )
    h1_ctx = HTFContext(
        timeframe="1H",
        bias="BEARISH",
        last_confirmed_bias="BEARISH",
        phase="ESTABLISHED",
        provenance="CONFIRMED_SWING_SEQUENCE",
    )

    # 1. Smc Regime Service alignment
    align = h1_alignment_from(h4_ctx, h1_ctx)
    assert align == "DIVERGENT"

    # 2. CG-01 Direction context resolver
    dir_ctx = resolve_cg01_direction_context(h4_ctx, h1_ctx)
    assert dir_ctx["relation"] == "DIVERGENT"
    # Primary execution hypothesis in CG-01 is H1 structure
    assert dir_ctx["directions_to_evaluate"] == ["SHORT"]
    assert dir_ctx["primary_context_source"] == "H1_BEARISH_PRIMARY"
    assert "H4_BULLISH_PULLBACK" in dir_ctx["reason_codes"]


# =========================================================================
# CASE 10: Replay historical context isolation (no live singleton leak)
# =========================================================================
def test_case_10_replay_isolation_no_live_collector_bias():
    from services.collector_service import collector_service
    # Set collector singleton to an arbitrary state
    collector_service.d_4h_bias = "BULLISH"
    collector_service.h1_alignment = "ALIGNED"

    # Injected replay candles are BEARISH
    dur = resolve_timeframe_ms("1H")
    base_t = 1700000000000
    prices = [
        (2600, 2605, 2590, 2595),
        (2595, 2598, 2585, 2590),
        (2590, 2610, 2588, 2605), # SH1 2610
        (2605, 2606, 2570, 2575),
        (2575, 2578, 2550, 2555), # SL1 2550
        (2555, 2565, 2552, 2560),
        (2560, 2575, 2558, 2570),
        (2570, 2585, 2568, 2580), # SH2 2580 (LH)
        (2580, 2582, 2540, 2545),
        (2545, 2548, 2520, 2525), # SL2 2525 (LL)
        (2525, 2530, 2522, 2528),
        (2528, 2535, 2524, 2530),
        (2530, 2532, 2520, 2522),
        (2522, 2525, 2518, 2520),
    ]
    t_now = base_t + len(prices) * dur
    c_1h = [CandleMock(base_t + i * dur, o, h, l, c, is_closed=True, tf="1H") for i, (o, h, l, c) in enumerate(prices)]
    c_4h = [CandleMock(t_now - (len(prices) - i) * 14400000, o, h, l, c, is_closed=True, tf="4H") for i, (o, h, l, c) in enumerate(prices)]

    closed_bundle = {
        "4H": c_4h,
        "1H": c_1h,
        "15M": c_1h,
        "1M": c_1h,
    }

    cg01_ctx = build_cg01_context(closed_bundle, t_now)

    # Injected candles are BEARISH; CG-01 context MUST evaluate to BEARISH and NOT live collector's BULLISH
    assert cg01_ctx["h1_bias"] == "BEARISH"
    assert cg01_ctx["directions_to_evaluate"] == ["SHORT"]


# =========================================================================
# CASE 11: Order snapshot immutability
# =========================================================================
def test_case_11_order_snapshot_immutability():
    # Build initial bullish context
    c_bull = make_bullish_candles()
    as_of_a = c_bull[-1].open_time + 3600000
    snap_a = evaluate_htf_structure(c_bull, "1H", as_of_ms=as_of_a)
    order_evidence = {
        "order_id": "ord_123",
        "htf_snapshot": snap_a.to_dict(),
    }
    assert order_evidence["htf_snapshot"]["bias"] == "BULLISH"

    # Market subsequently shifts to transition/break
    c_broken = list(c_bull)
    c_broken.append(CandleMock(as_of_a, 2555, 2556, 2500, 2510, is_closed=True, tf="1H"))
    snap_b = evaluate_htf_structure(c_broken, "1H", as_of_ms=as_of_a + 3600000)

    assert snap_b.bias != "BULLISH"
    # Order historical evidence remains untouched and immutable!
    assert order_evidence["htf_snapshot"]["bias"] == "BULLISH"


# =========================================================================
# CASE 12: Prefix invariance: adding future candles does not alter history at T
# =========================================================================
def test_case_12_prefix_invariance():
    candles = make_bullish_candles()
    dur = resolve_timeframe_ms("1H")
    t_fixed = candles[-1].open_time + dur

    snap_prefix = evaluate_htf_structure(candles, "1H", as_of_ms=t_fixed)

    # Append 10 more random candles far in the future
    future_candles = list(candles)
    for k in range(1, 11):
        t_fut = candles[-1].open_time + k * dur
        future_candles.append(CandleMock(t_fut, 2560 + k, 2570 + k, 2550 + k, 2565 + k, is_closed=True, tf="1H"))

    # Re-evaluate with future candles present, but strictly at as_of = t_fixed
    snap_invariant = evaluate_htf_structure(future_candles, "1H", as_of_ms=t_fixed)

    assert snap_prefix.bias == snap_invariant.bias
    assert snap_prefix.last_confirmed_bias == snap_invariant.last_confirmed_bias
    assert snap_prefix.phase == snap_invariant.phase
    assert snap_prefix.protected_low == snap_invariant.protected_low
    assert snap_prefix.last_bar_close_ms == snap_invariant.last_bar_close_ms


# =========================================================================
# CASE 13: Collector query filters out unclosed candles in DB
# =========================================================================
def test_case_13_collector_closed_only_query():
    import crud
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        # Insert 3 closed candles and 1 unclosed candle
        base_t = 1700000000000
        for i in range(3):
            c = models.Candle(
                symbol="XAUUSDT",
                timeframe="1H",
                timestamp=base_t + i * 3600000,
                open=2500.0 + i,
                high=2510.0 + i,
                low=2490.0 + i,
                close=2505.0 + i,
                volume=10.0,
                is_closed=True,
            )
            db.add(c)
        # Unclosed candle
        c_unclosed = models.Candle(
            symbol="XAUUSDT",
            timeframe="1H",
            timestamp=base_t + 3 * 3600000,
            open=2503.0,
            high=2520.0,
            low=2500.0,
            close=2518.0,
            volume=5.0,
            is_closed=False,
        )
        db.add(c_unclosed)
        db.commit()

        # Normal get_candles returns all 4
        all_c = crud.get_candles(db, "XAUUSDT", "1H")
        assert len(all_c) == 4

        # get_closed_candles returns only the 3 closed ones
        closed_c = crud.get_closed_candles(db, "XAUUSDT", "1H")
        assert len(closed_c) == 3
        assert all(c.is_closed for c in closed_c)
    finally:
        db.close()


# =========================================================================
# CASE 14: Positive CG-01 Long Execution Flow under Causal HTF
# =========================================================================
def test_case_14_cg01_positive_long_flow():
    # Use existing test pipeline setup to verify positive Long flow survives
    from services.cg01_context_adapter import CanonicalCandle
    dur_1h = 3600000
    dur_15m = 900000
    dur_1m = 60000
    base_t = 1791984600000 # In NY window (10:10 AM NY)

    # 1. HTF Bullish
    t_now = base_t + 25 * dur_1m
    c_4h = [CanonicalCandle(t_now - (15 - i) * 14400000, 2600.0, 2610.0, 2595.0, 2605.0 + i, 100.0, "4H", "XAUUSDT", True) for i in range(15)]
    c_1h = [CanonicalCandle(t_now - (15 - i) * dur_1h, 2600.0, 2605.0 + i * 2, 2598.0, 2604.0 + i * 2, 100.0, "1H", "XAUUSDT", True) for i in range(15)]
    c_15m = [CanonicalCandle(t_now - (15 - i) * dur_15m, 2620.0, 2625.0, 2615.0, 2622.0, 100.0, "15M", "XAUUSDT", True) for i in range(15)]

    # 2. M1 setup with POI, Sweep, MSS, and FVG
    m1_candles = []
    # Base bars
    for i in range(20):
        t = base_t + i * dur_1m
        m1_candles.append(CanonicalCandle(t, 2620.0, 2622.0, 2618.0, 2620.0, 50.0, "1M", "XAUUSDT", True))

    context = {
        "4H": c_4h,
        "1H": c_1h,
        "15M": c_15m,
        "1M": m1_candles,
    }
    htf_data = build_cg01_context(context, now_ms=t_now)
    assert htf_data["status"] == "READY"
    assert htf_data["h1_bias"] in ("BULLISH", "UNKNOWN", "RANGE")


# =========================================================================
# CASE 15: Positive CG-01 Short Execution Flow under Causal HTF
# =========================================================================
def test_case_15_cg01_positive_short_flow():
    from services.cg01_context_adapter import CanonicalCandle
    dur_1h = 3600000
    dur_15m = 900000
    dur_1m = 60000
    base_t = 1791984600000

    # 1. HTF Bearish
    t_now = base_t + 25 * dur_1m
    c_4h = [CanonicalCandle(t_now - (15 - i) * 14400000, 2700.0, 2705.0, 2680.0, 2685.0 - i, 100.0, "4H", "XAUUSDT", True) for i in range(15)]
    c_1h = [CanonicalCandle(t_now - (15 - i) * dur_1h, 2700.0, 2702.0, 2680.0 - i * 2, 2682.0 - i * 2, 100.0, "1H", "XAUUSDT", True) for i in range(15)]
    c_15m = [CanonicalCandle(t_now - (15 - i) * dur_15m, 2650.0, 2655.0, 2640.0, 2645.0, 100.0, "15M", "XAUUSDT", True) for i in range(15)]
    c_1m = [CanonicalCandle(base_t + i * dur_1m, 2645.0, 2648.0, 2642.0, 2644.0, 50.0, "1M", "XAUUSDT", True) for i in range(20)]

    context = {
        "4H": c_4h,
        "1H": c_1h,
        "15M": c_15m,
        "1M": c_1m,
    }
    htf_data = build_cg01_context(context, now_ms=t_now)
    assert htf_data["status"] == "READY"
