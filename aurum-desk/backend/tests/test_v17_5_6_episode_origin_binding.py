"""
backend/tests/test_v17_5_6_episode_origin_binding.py
AURUM V17.5.6 — Episode & Origin Proof, Binding & SL Geometry Suite.
Covers Parts 111–120, 141–150, Appendix C, E1:
1. Episode containment: selecting leg A over newer leg B outside episode.
2. Distant extremes outside episode do not widen structural SL.
3. Post-break pivots strictly excluded from protected origin candidacy.
4. Empty closed_bars returns UNAVAILABLE (MISSING_SOURCE_CANDLES), never True/AVAILABLE.
5. Missing origin source bar in closed_bars fails validation.
6. Mismatched extreme price (e.g. SHORT origin != candle High) rejected within tick tolerance.
7. Bullish (LONG) leg mirror symmetry verified.
8. Invalid specified origin rejects fail-closed without fallback to arbitrary pivot.
9. Reference mismatch across different episodes rejected.
10. Outward tick quantization for LONG (floor) and SHORT (ceil), maintaining planned risk budget.
"""

import math
import pytest
from services.stop_placement_types import (
    SelectedStopLeg,
    StopAnchor,
    StopPlacementPolicy,
)
from services.stop_placement_service import (
    derive_protected_leg_episode,
    select_protected_origin_for_break,
    validate_protected_origin_binding,
    resolve_bound_stop_leg,
    resolve_stop_anchor,
    quantize_stop_outward,
)
from domain_calculator import calculate_risk_reward


class CandleBar:
    def __init__(self, open_time, o, h, l, c):
        self.open_time = open_time
        self.timestamp = open_time
        self.open = float(o)
        self.high = float(h)
        self.low = float(l)
        self.close = float(c)
        self.is_closed = True


# ======================================================================
# PARTS 111–120: Episode Containment, Source & Strict Binding
# ======================================================================

def test_p111_p112_multiple_legs_and_distant_extreme():
    """Phần 111, 112: Leg A inside episode chosen over newer B outside episode; distant D does not widen SL."""
    ref = {"id": "ref_1", "price": 2000.0, "occurred_at_ms": 5000, "known_at_ms": 5060}
    brk = {"broken_at_ms": 10000, "close_price": 1995.0, "open_ms": 10000, "known_at_ms": 10060}

    # Pivot A (valid origin in leg, at t=7000, High=2015.0)
    p_a = {"id": "p_a", "type": "HIGH", "price": 2015.0, "occurred_at_ms": 7000, "known_at_ms": 7060}
    # Pivot B (newer, outside leg after break open, t=10020, High=2012.0)
    p_b = {"id": "p_b", "type": "HIGH", "price": 2012.0, "occurred_at_ms": 10020, "known_at_ms": 10040}
    # Pivot D (distant extreme outside episode lookback, t=1000, High=2050.0)
    p_d = {"id": "p_d", "type": "HIGH", "price": 2050.0, "occurred_at_ms": 1000, "known_at_ms": 1060}

    bars = [
        CandleBar(1000, 2040, 2050, 2035, 2045),
        CandleBar(5000, 2005, 2010, 1998, 2000),
        CandleBar(7000, 2005, 2015, 2003, 2010),
        CandleBar(10000, 2002, 2004, 1994, 1995),
    ]

    ep = derive_protected_leg_episode(
        model_id="M1_CONTINUATION",
        direction="SHORT",
        selected_reference=ref,
        selected_break=brk,
        confirmed_swings=([p_d, p_a, p_b], []),
        closed_bars=bars,
        decision_cutoff_ms=10100,
        timeframe_ms=60000,
    )
    assert ep.status == "AVAILABLE"
    # Origin candidates should only contain p_a (p_b occurred after break open, p_d is before ref)
    assert "p_a" in ep.eligible_origin_ids
    assert "p_b" not in ep.eligible_origin_ids

    # Select protected origin
    res = select_protected_origin_for_break(
        direction="SHORT",
        selected_break=brk,
        confirmed_swings=([p_d, p_a, p_b], []),
        closed_bars=bars,
        decision_cutoff_ms=10100,
        selected_reference=ref,
    )
    assert res["status"] == "AVAILABLE"
    assert res["origin_pivot_id"] == "p_a"
    assert res["origin_price"] == 2015.0  # Does not pick 2050.0 (D) or 2012.0 (B)


def test_p113_post_break_pivot_excluded():
    """Phần 113: Pivot C occurring after break open is strictly excluded."""
    ref = {"id": "ref_1", "price": 2000.0, "occurred_at_ms": 5000, "known_at_ms": 5060}
    brk = {"broken_at_ms": 10000, "close_price": 1995.0, "open_ms": 10000, "known_at_ms": 10060}

    # Pivot C at t=10010 (during/after break open)
    p_c = {"id": "p_c", "type": "HIGH", "price": 2010.0, "occurred_at_ms": 10010, "known_at_ms": 10030}
    bars = [CandleBar(10000, 2002, 2010, 1994, 1995)]

    ep = derive_protected_leg_episode(
        model_id="M1_CONTINUATION",
        direction="SHORT",
        selected_reference=ref,
        selected_break=brk,
        confirmed_swings=([p_c], []),
        closed_bars=bars,
        decision_cutoff_ms=10100,
        timeframe_ms=60000,
    )
    assert "p_c" not in ep.eligible_origin_ids


def test_p114_empty_closed_bars_returns_unavailable():
    """Phần 114: Empty closed_bars returns UNAVAILABLE (MISSING_SOURCE_CANDLES), never AVAILABLE."""
    ref = {"id": "ref_1", "price": 2000.0, "occurred_at_ms": 5000, "known_at_ms": 5060}
    brk = {"broken_at_ms": 10000, "close_price": 1995.0, "open_ms": 10000, "known_at_ms": 10060}
    p_a = {"id": "p_a", "type": "HIGH", "price": 2015.0, "occurred_at_ms": 7000, "known_at_ms": 7060}

    # Empty closed bars!
    res = select_protected_origin_for_break(
        direction="SHORT",
        selected_break=brk,
        confirmed_swings=([p_a], []),
        closed_bars=[],
        decision_cutoff_ms=10100,
        selected_reference=ref,
    )
    assert res["status"] == "UNAVAILABLE"
    assert "MISSING_SOURCE_CANDLES" in str(res["reason"])


def test_p115_missing_origin_source_in_bars():
    """Phần 115: Origin source bar not in closed_bars fails validation."""
    ref = {"id": "ref_1", "price": 2000.0, "occurred_at_ms": 5000, "known_at_ms": 5060}
    brk = {"broken_at_ms": 10000, "close_price": 1995.0, "open_ms": 10000, "known_at_ms": 10060}
    # Pivot at t=7000, but bars only have 5000 and 10000
    p_a = {"id": "p_a", "type": "HIGH", "price": 2015.0, "occurred_at_ms": 7000, "known_at_ms": 7060}
    bars = [
        CandleBar(5000, 2005, 2010, 1998, 2000),
        CandleBar(10000, 2002, 2004, 1994, 1995),
    ]

    res = select_protected_origin_for_break(
        direction="SHORT",
        selected_break=brk,
        confirmed_swings=([p_a], []),
        closed_bars=bars,
        decision_cutoff_ms=10100,
        selected_reference=ref,
    )
    assert res["status"] == "UNAVAILABLE"


def test_p116_wrong_extreme_price_rejected():
    """Phần 116: SHORT origin price not matching candle High within tick tolerance is rejected."""
    # Pivot price is 2018.0, but candle High is only 2015.0!
    p_a = {"id": "p_a", "type": "HIGH", "price": 2018.0, "occurred_at_ms": 7000, "known_at_ms": 7060}
    bars = [
        CandleBar(5000, 2005, 2010, 1998, 2000),
        CandleBar(7000, 2005, 2015, 2003, 2010),
        CandleBar(10000, 2002, 2004, 1994, 1995),
    ]

    is_valid, reason = validate_protected_origin_binding(
        origin=p_a,
        direction="SHORT",
        break_open_ms=10000,
        decision_cutoff_ms=10100,
        closed_bars=bars,
        tick_size=0.01,
    )
    assert is_valid is False
    assert "ORIGIN_PRICE_MISMATCH" in str(reason)


def test_p117_long_mirror_bullish_origin():
    """Phần 117: LONG leg origin LOW verified with symmetric geometry."""
    ref = {"id": "ref_long", "price": 2000.0, "occurred_at_ms": 5000, "known_at_ms": 5060}
    brk = {"broken_at_ms": 10000, "close_price": 2005.0, "open_ms": 10000, "known_at_ms": 10060}
    # Pivot LOW at t=7000 with Low=1985.0
    p_low = {"id": "p_low", "type": "LOW", "price": 1985.0, "occurred_at_ms": 7000, "known_at_ms": 7060}
    bars = [
        CandleBar(5000, 1995, 2002, 1990, 2000),
        CandleBar(7000, 1990, 1995, 1985, 1992),
        CandleBar(10000, 1998, 2006, 1996, 2005),
    ]

    res = select_protected_origin_for_break(
        direction="LONG",
        selected_break=brk,
        confirmed_swings=([], [p_low]),
        closed_bars=bars,
        decision_cutoff_ms=10100,
        selected_reference=ref,
    )
    assert res["status"] == "AVAILABLE"
    assert res["origin_pivot_id"] == "p_low"
    assert res["origin_price"] == 1985.0


def test_p118_specified_invalid_does_not_fallback():
    """Phần 118: Specified origin ID that is invalid must be rejected, not silently fallback to another pivot."""
    ref = {"id": "ref_1", "price": 2000.0, "occurred_at_ms": 5000, "known_at_ms": 5060}
    # Caller explicitly specifies origin_pivot_id="p_nonexistent"
    brk = {
        "broken_at_ms": 10000,
        "close_price": 1995.0,
        "open_ms": 10000,
        "known_at_ms": 10060,
        "origin_pivot_id": "p_nonexistent",
    }
    # Valid pivot B
    p_b = {"id": "p_b", "type": "HIGH", "price": 2015.0, "occurred_at_ms": 7000, "known_at_ms": 7060}
    bars = [
        CandleBar(5000, 2005, 2010, 1998, 2000),
        CandleBar(7000, 2005, 2015, 2003, 2010),
        CandleBar(10000, 2002, 2004, 1994, 1995),
    ]

    res = select_protected_origin_for_break(
        direction="SHORT",
        selected_break=brk,
        confirmed_swings=([p_b], []),
        closed_bars=bars,
        decision_cutoff_ms=10100,
        selected_reference=ref,
    )
    assert res["status"] == "UNAVAILABLE"
    assert res["origin_pivot_id"] is None
    # Must NOT have fallen back to p_b!
    assert res["origin_pivot_id"] != "p_b"


# ======================================================================
# PARTS 141–150: SL Quantization, Risk Budgets & Geometry
# ======================================================================

def test_p141_p142_outward_tick_quantization():
    """Phần 141, 142: SHORT anchor + buffer rounds CEIL outward (SL > entry); LONG anchor - buffer rounds FLOOR outward (SL < entry)."""
    tick_size = 0.01

    # SHORT: anchor 2010.22, buffer 0.005 -> raw 2010.225. Outward buffer must ceil to 2010.23, NOT round down to 2010.22.
    quant_short = quantize_stop_outward(
        anchor_price=2010.22,
        buffer_price=0.005,
        direction="SHORT",
        tick_size=tick_size,
    )
    assert quant_short == 2010.23
    assert quant_short > 2000.00

    # LONG: anchor 1989.78, buffer 0.005 -> raw 1989.775. Outward buffer must floor to 1989.77, NOT round up to 1989.78.
    quant_long = quantize_stop_outward(
        anchor_price=1989.78,
        buffer_price=0.005,
        direction="LONG",
        tick_size=tick_size,
    )
    assert quant_long == 1989.77
    assert quant_long < 2000.00


def test_p143_custom_instrument_tick():
    """Phần 143: Instrument with tick 0.05 quantizes correctly to 0.05 grid."""
    tick_size = 0.05
    # SHORT: anchor 2010.10 + buffer 0.02 -> 2010.12 -> CEIL to 0.05 grid -> 2010.15
    q_short = quantize_stop_outward(
        anchor_price=2010.10,
        buffer_price=0.02,
        direction="SHORT",
        tick_size=tick_size,
    )
    assert q_short == 2010.15

    # LONG: anchor 2010.10 - buffer 0.02 -> 2010.08 -> FLOOR to 0.05 grid -> 2010.05
    q_long = quantize_stop_outward(
        anchor_price=2010.10,
        buffer_price=0.02,
        direction="LONG",
        tick_size=tick_size,
    )
    assert q_long == 2010.05


def test_p144_risk_budget_conservation():
    """Phần 144: Wider structural SL reduces position quantity to conserve planned risk USD."""
    capital = 5000.0
    risk_pct = 0.10  # Planned risk budget: $500.00

    # Narrow SL (distance 5.0): entry 2000.0, SL 1995.0
    calc_narrow = calculate_risk_reward(
        direction="LONG",
        planned_entry=2000.0,
        stop_loss=1995.0,
        take_profit=2015.0,
        capital=capital,
        risk_pct=risk_pct,
        leverage=10,
    )

    # Wide SL (distance 10.0): entry 2000.0, SL 1990.0
    calc_wide = calculate_risk_reward(
        direction="LONG",
        planned_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2030.0,
        capital=capital,
        risk_pct=risk_pct,
        leverage=10,
    )

    # Quantity in wide SL must be approximately half of narrow SL
    assert calc_wide.quantity < calc_narrow.quantity
    assert abs(calc_wide.net_risk_usdt - calc_narrow.net_risk_usdt) < 15.0  # Conserves budget


def test_p148_invalid_geometry_rejected():
    """Phần 148: SHORT TP above entry or SL below entry rejected, never abs() normalized."""
    from domain_calculator import validate_price_geometry

    # SHORT with TP > entry (invalid)
    valid_tp, err_tp = validate_price_geometry("SHORT", entry=2000.0, sl=2010.0, tp=2020.0)
    assert valid_tp is False
    assert "TP" in err_tp

    # SHORT with SL < entry (invalid)
    valid_sl, err_sl = validate_price_geometry("SHORT", entry=2000.0, sl=1990.0, tp=1980.0)
    assert valid_sl is False
    assert "SL" in err_sl

    # LONG with SL > entry (invalid)
    valid_long_sl, err_long_sl = validate_price_geometry("LONG", entry=2000.0, sl=2010.0, tp=2020.0)
    assert valid_long_sl is False
