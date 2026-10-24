"""
backend/tests/test_v17_5_9_reference_source_authority.py
AURUM V17.5.9 — Reference Source Authority & Fail-Closed Suite (D01, D06, Section 02 & Section 09 Part 071).

Cases:
- R01: Reference has timestamp but no authoritative source -> UNAVAILABLE ('REFERENCE_CANDLE_MISSING')
- R02: Two source candles match the same price -> AMBIGUOUS ('AMBIGUOUS_REFERENCE_SOURCE')
- R03: Supplied ref ID conflicts with canonical price -> REJECT ('REFERENCE_METADATA_MISMATCH')
- R04: Supplied ref ID conflicts with canonical time -> REJECT ('REFERENCE_METADATA_MISMATCH')
- R05: Ref price lies inside candle range but is not valid expected swing extreme -> REJECT ('REFERENCE_EXTREME_MISMATCH')
- R06: Future-confirmed swing -> REJECT ('REFERENCE_KNOWN_AFTER_DECISION')
- R07: NaN reference price -> REJECT ('INVALID_REFERENCE_PRICE')
- R08: Unsupported direction -> REJECT ('INVALID_DIRECTION')
- R09: Valid source-backed SHORT reference -> AVAILABLE
- R10: Valid source-backed LONG reference -> AVAILABLE
"""

import math
import pytest
from services.stop_placement_service import resolve_verified_reference_node


class DummyBar:
    def __init__(self, open_time: int, o: float, h: float, l: float, c: float, is_closed: bool = True):
        self.open_time = int(open_time)
        self.timestamp = int(open_time)
        self.open = float(o)
        self.high = float(h)
        self.low = float(l)
        self.close = float(c)
        self.is_closed = is_closed


def test_r01_reference_timestamp_without_authoritative_source_candle():
    """R01: Reference has timestamp but source candle is missing from closed_bars -> UNAVAILABLE ('REFERENCE_CANDLE_MISSING')."""
    ref = {
        "id": "ref_sl_1",
        "price": 2650.0,
        "occurred_at_ms": 5000,
        "known_at_ms": 5060,
    }
    # closed_bars does not contain candle at 5000
    bars = [DummyBar(6000, 2651, 2655, 2649, 2652)]
    res = resolve_verified_reference_node(
        selected_reference=ref,
        confirmed_swings=([], []),
        closed_bars=bars,
        model_id="M1_FVG",
        direction="SHORT",
        decision_cutoff_ms=10000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "REFERENCE_CANDLE_MISSING" in res.get("reasons", ())


def test_r02_two_source_candles_match_same_price_ambiguous():
    """R02: When price-only reference matches multiple independent candles, fail closed with AMBIGUOUS_REFERENCE_SOURCE."""
    bars = [
        DummyBar(3000, 2655, 2660, 2650.0, 2652),
        DummyBar(5000, 2654, 2658, 2650.0, 2651),
    ]
    ref = {"price": 2650.0}
    res = resolve_verified_reference_node(
        selected_reference=ref,
        confirmed_swings=([], []),
        closed_bars=bars,
        model_id="M1_FVG",
        direction="SHORT",
        break_open_ms=8000,
        decision_cutoff_ms=10000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "AMBIGUOUS_REFERENCE_SOURCE" in res.get("reasons", ())


def test_r03_supplied_ref_id_conflicts_with_canonical_price():
    """R03: Supplied ref ID has price contradicting canonical swing price -> REJECT ('REFERENCE_METADATA_MISMATCH')."""
    canonical_swing = {"id": "ref_sl_1", "price": 2650.0, "timestamp": 5000, "confirmed_at": 5060}
    ref_contradictory = {
        "id": "ref_sl_1",
        "price": 2660.0,  # Contradicts canonical 2650.0
        "occurred_at_ms": 5000,
        "known_at_ms": 5060,
    }
    bars = [DummyBar(5000, 2652, 2655, 2650.0, 2651)]
    res = resolve_verified_reference_node(
        selected_reference=ref_contradictory,
        confirmed_swings=([], [canonical_swing]),
        closed_bars=bars,
        model_id="M1_FVG",
        direction="SHORT",
        decision_cutoff_ms=10000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "REFERENCE_METADATA_MISMATCH" in res.get("reasons", ())


def test_r04_supplied_ref_id_conflicts_with_canonical_time():
    """R04: Supplied ref ID has timestamp contradicting canonical swing time -> REJECT ('REFERENCE_METADATA_MISMATCH')."""
    canonical_swing = {"id": "ref_sl_1", "price": 2650.0, "timestamp": 5000, "confirmed_at": 5060}
    ref_contradictory_time = {
        "id": "ref_sl_1",
        "price": 2650.0,
        "occurred_at_ms": 7000,  # Contradicts canonical 5000
        "known_at_ms": 7060,
    }
    bars = [DummyBar(5000, 2652, 2655, 2650.0, 2651)]
    res = resolve_verified_reference_node(
        selected_reference=ref_contradictory_time,
        confirmed_swings=([], [canonical_swing]),
        closed_bars=bars,
        model_id="M1_FVG",
        direction="SHORT",
        decision_cutoff_ms=10000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "REFERENCE_METADATA_MISMATCH" in res.get("reasons", ())


def test_r05_ref_price_inside_range_but_not_expected_swing_extreme():
    """R05: Ref price 2655.0 lies inside candle range [2650, 2660] but is not the swing low extreme 2650 -> REJECT ('REFERENCE_EXTREME_MISMATCH')."""
    # SHORT swing low extreme must match Low (2650.0) or Close (2652.0)
    bar = DummyBar(5000, 2658, 2660, 2650.0, 2652)
    ref = {
        "id": "ref_sl_1",
        "price": 2655.0,  # inside range but not extreme
        "occurred_at_ms": 5000,
        "known_at_ms": 5060,
    }
    res = resolve_verified_reference_node(
        selected_reference=ref,
        confirmed_swings=([], []),
        closed_bars=[bar],
        model_id="M1_FVG",
        direction="SHORT",
        decision_cutoff_ms=10000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "REFERENCE_EXTREME_MISMATCH" in res.get("reasons", ())


def test_r06_future_confirmed_swing_rejected():
    """R06: Swing confirmed at 10500 after decision cutoff 10000 is rejected with REFERENCE_KNOWN_AFTER_DECISION."""
    bar = DummyBar(5000, 2652, 2655, 2650.0, 2651)
    ref = {
        "id": "ref_sl_1",
        "price": 2650.0,
        "occurred_at_ms": 5000,
        "known_at_ms": 10500,  # after decision cutoff 10000
    }
    res = resolve_verified_reference_node(
        selected_reference=ref,
        confirmed_swings=([], [{"id": "ref_sl_1", "price": 2650.0, "timestamp": 5000, "confirmed_at": 10500}]),
        closed_bars=[bar],
        model_id="M1_FVG",
        direction="SHORT",
        decision_cutoff_ms=10000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "REFERENCE_KNOWN_AFTER_DECISION" in res.get("reasons", ())


def test_r07_nan_reference_price_rejected():
    """R07: NaN reference price rejected with INVALID_REFERENCE_PRICE."""
    ref = {
        "id": "ref_sl_1",
        "price": float("nan"),
        "occurred_at_ms": 5000,
        "known_at_ms": 5060,
    }
    bar = DummyBar(5000, 2652, 2655, 2650.0, 2651)
    res = resolve_verified_reference_node(
        selected_reference=ref,
        confirmed_swings=([], []),
        closed_bars=[bar],
        model_id="M1_FVG",
        direction="SHORT",
        decision_cutoff_ms=10000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "INVALID_REFERENCE_PRICE" in res.get("reasons", ())


def test_r08_unsupported_direction_rejected():
    """R08: Unsupported direction value 'SIDEWAYS' rejected with INVALID_DIRECTION."""
    ref = {
        "id": "ref_sl_1",
        "price": 2650.0,
        "occurred_at_ms": 5000,
        "known_at_ms": 5060,
    }
    bar = DummyBar(5000, 2652, 2655, 2650.0, 2651)
    res = resolve_verified_reference_node(
        selected_reference=ref,
        confirmed_swings=([], []),
        closed_bars=[bar],
        model_id="M1_FVG",
        direction="SIDEWAYS",
        decision_cutoff_ms=10000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "INVALID_DIRECTION" in res.get("reasons", ())


def test_r09_valid_source_backed_short_reference():
    """R09: Valid source-backed SHORT swing low reference resolves AVAILABLE with correct metadata."""
    bar = DummyBar(5000, 2654.0, 2656.0, 2650.0, 2651.0)
    swing = {"id": "sw_low_1", "type": "SWING_LOW", "price": 2650.0, "timestamp": 5000, "confirmed_at": 5060}
    ref = {
        "id": "sw_low_1",
        "price": 2650.0,
        "occurred_at_ms": 5000,
        "known_at_ms": 5060,
    }
    res = resolve_verified_reference_node(
        selected_reference=ref,
        confirmed_swings=([], [swing]),
        closed_bars=[bar],
        model_id="M1_FVG",
        direction="SHORT",
        break_open_ms=8000,
        decision_cutoff_ms=10000,
    )
    assert res.get("status") == "AVAILABLE"
    assert res.get("ref_id") == "sw_low_1"
    assert res.get("ref_price") == 2650.0
    assert res.get("ref_occurred_at_ms") == 5000
    assert res.get("ref_known_at_ms") == 5060


def test_r10_valid_source_backed_long_reference():
    """R10: Valid source-backed LONG swing high reference resolves AVAILABLE with correct metadata."""
    bar = DummyBar(5000, 2645.0, 2660.0, 2642.0, 2658.0)
    swing = {"id": "sw_high_1", "type": "SWING_HIGH", "price": 2660.0, "timestamp": 5000, "confirmed_at": 5060}
    ref = {
        "id": "sw_high_1",
        "price": 2660.0,
        "occurred_at_ms": 5000,
        "known_at_ms": 5060,
    }
    res = resolve_verified_reference_node(
        selected_reference=ref,
        confirmed_swings=([swing], []),
        closed_bars=[bar],
        model_id="M1_FVG",
        direction="LONG",
        break_open_ms=8000,
        decision_cutoff_ms=10000,
    )
    assert res.get("status") == "AVAILABLE"
    assert res.get("ref_id") == "sw_high_1"
    assert res.get("ref_price") == 2660.0
    assert res.get("ref_occurred_at_ms") == 5000
    assert res.get("ref_known_at_ms") == 5060
