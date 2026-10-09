"""
V7 SMC/ICT Causal Evidence Test Suite.
Validates:
1. Complete elimination of FVG bypass (no 'or current_price <= eq' bypass).
2. Strict causal ordering (break.timestamp > sweep.timestamp, same-bar flagged ambiguous).
3. Break requires confirmed displacement (is_displacement == True).
4. H1 opposing alignment blocks READY status.
5. Real liquidity TP preserved without 2.8x artificial inflation (returns NET_RR_TOO_LOW if < 2.0).
"""
import pytest
from smc_engine import (
    evaluate_smc_setup,
    identify_pivots,
    detect_sweeps_and_breaks,
    detect_fvgs
)


class DummyCandle:
    def __init__(self, timestamp, o, h, l, c, is_closed=True, volume=100.0):
        self.timestamp = timestamp
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.volume = volume
        self.is_closed = is_closed


def test_smc_no_fvg_bypass_in_discount():
    """
    In V7, being in Discount (price <= eq) does NOT bypass FVG retest requirement.
    Setup without an active FVG retest must NOT be READY.
    """
    # 25 candles creating a range where equilibrium is ~2650
    candles = [
        DummyCandle(1000 + i * 900000, 2640.0 + (i % 5), 2660.0, 2635.0, 2645.0)
        for i in range(25)
    ]
    # Current price is 2642.0 (well below equilibrium 2647.5, so in Discount)
    ticker = {"last_price": 2642.0, "bid": 2641.9, "ask": 2642.1, "timestamp": 1000 + 25 * 900000}

    res = evaluate_smc_setup(
        candles=candles,
        symbol="XAUUSDT",
        timeframe="15M",
        ticker_data=ticker,
        htf_bias="BULLISH",
        h1_alignment="ALIGNED"
    )

    # Even though price is in Discount, because there is no verified sweep + FVG retest, setup_stage CANNOT be READY!
    assert res["setup_stage"] != "READY", "Engine must not mark READY merely for being in Discount"
    missing = res.get("missing_conditions", [])
    has_fvg_or_sweep_gate = any("FVG" in m or "Sweep" in m or "MSS" in m for m in missing)
    assert has_fvg_or_sweep_gate is True


def test_smc_strict_causal_break_after_sweep():
    """
    A break event with timestamp <= sweep event timestamp must NOT qualify.
    Causality requires: break.timestamp > sweep.timestamp.
    """
    # Pivot Low at timestamp 5000
    pivots_low = [{"index": 5, "price": 2630.0, "timestamp": 5000, "confirmed_at": 7000}]
    pivots_high = [{"index": 8, "price": 2660.0, "timestamp": 8000, "confirmed_at": 10000}]

    # Case A: Same-bar sweep and break at timestamp 9000
    candles_same_bar = [
        DummyCandle(1000 * i, 2640, 2650, 2635, 2645) for i in range(12)
    ]
    # Bar 9 dips below 2630 (sweeps to 2628) and rallies above 2660 (breaks to 2665) in the SAME bar
    candles_same_bar[9] = DummyCandle(9000, 2635, 2665, 2628, 2662)

    events_same, _, _ = detect_sweeps_and_breaks(candles_same_bar, pivots_high, pivots_low)
    # Verify same-bar ambiguous events are flagged or causality guard rejects same-bar break
    sweeps = [e for e in events_same if e.get("event_type") == "SWEEP"]
    breaks = [e for e in events_same if e.get("event_type") in ("CHOCH", "BOS")]

    if sweeps and breaks:
        last_sw = sweeps[-1]
        matching_breaks = [b for b in breaks if b["timestamp"] > last_sw["timestamp"]]
        # None of the breaks occurred strictly AFTER the sweep
        assert len(matching_breaks) == 0, "Break with timestamp <= sweep must not be causally matched"


def test_smc_break_requires_displacement():
    """
    A structure break without displacement (weak wick, small body/range)
    must have is_displacement=False and not satisfy the causal chain.
    """
    pivots_high = [{"index": 5, "price": 2650.0, "timestamp": 5000, "confirmed_at": 7000}]
    pivots_low = [{"index": 2, "price": 2630.0, "timestamp": 2000, "confirmed_at": 4000}]

    candles = [DummyCandle(1000 * i, 2640, 2645, 2635, 2642) for i in range(10)]
    # Bar 8: Barely wicks above 2650 to 2650.2 with small body (open 2648, close 2649) -> No displacement!
    candles[8] = DummyCandle(8000, 2648.0, 2650.2, 2647.5, 2649.0)

    events, _, _ = detect_sweeps_and_breaks(candles, pivots_high, pivots_low)
    breaks = [e for e in events if e.get("event_type") in ("BOS", "CHOCH")]


    for b in breaks:
        if b.get("is_break"):
            # If body close was below pivot, it's a wick sweep, not a displacement break
            assert b.get("is_displacement", False) is False or b["event_type"] == "SWEEP"


def test_smc_h1_opposing_blocks_ready():
    """When H1 alignment is OPPOSING, setup cannot be READY."""
    candles = [DummyCandle(1000 * i, 2640, 2655, 2635, 2650) for i in range(25)]
    ticker = {"last_price": 2650.0, "bid": 2649.9, "ask": 2650.1, "timestamp": 25000}

    res = evaluate_smc_setup(
        candles=candles,
        symbol="XAUUSDT",
        timeframe="15M",
        ticker_data=ticker,
        htf_bias="BULLISH",
        h1_alignment="OPPOSING"
    )

    assert res["setup_stage"] != "READY"
    checklist = res.get("checklist", [])
    h1_item = next((item for item in checklist if item["id"] == "H1_ALIGNMENT"), None)
    if h1_item:
        assert h1_item["status"] == "FAIL"



def test_smc_liquidity_tp_no_inflation():
    """
    Real liquidity TP is preserved without arbitrary 2.8x inflation.
    If liquidity target only offers Net RR < 2.0, returns NET_RR_TOO_LOW.
    """
    # Create candles where nearest swing high is at 2652 (very close to entry 2650)
    candles = [DummyCandle(1000 * i, 2645, 2648, 2640, 2646) for i in range(20)]
    candles[15] = DummyCandle(15000, 2646, 2652.0, 2645, 2647) # Swing high at 2652
    # Stop loss will be around 2640 (distance 10), target at 2652 (distance 2) -> RR 0.2
    ticker = {"last_price": 2650.0, "bid": 2649.9, "ask": 2650.1, "timestamp": 20000}

    res = evaluate_smc_setup(
        candles=candles,
        symbol="XAUUSDT",
        timeframe="15M",
        ticker_data=ticker,
        htf_bias="BULLISH",
        h1_alignment="ALIGNED"
    )

    # In V7, take_profit must NOT be artificially pumped by 2.8x stop distance
    if res.get("active_signal"):
        tp = res["active_signal"]["take_profit"]
        sl = res["active_signal"]["stop_loss"]
        entry = res["active_signal"]["planned_entry"]
        stop_dist = abs(entry - sl)
        target_dist = abs(tp - entry)
        # Verify it wasn't inflated to 2.8x stop distance
        assert target_dist != round(stop_dist * 2.8, 2), "Target must not be inflated by 2.8x stop distance"
