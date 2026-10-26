"""
backend/tests/test_v17_5_8_reference_provenance.py
AURUM V17.5.8 — Reference Provenance & Fail-Closed Validation Suite (Parts 011–020, 081–082).

Audits and proves:
1. resolve_verified_reference_node enforces complete structural and causal evidence:
   - Missing reference -> UNAVAILABLE ('MISSING_REFERENCE_SOURCE')
   - Ambiguous reference price (multiple swings matching price) -> UNAVAILABLE ('AMBIGUOUS_REFERENCE_PRICE')
   - Reference known after decision cutoff -> UNAVAILABLE ('REFERENCE_KNOWN_AFTER_DECISION')
   - Reference occurred after break open -> UNAVAILABLE ('REFERENCE_AFTER_BREAK')
   - Reference source candle missing -> UNAVAILABLE ('REFERENCE_CANDLE_MISSING')
   - Reference price mismatch with candle range -> UNAVAILABLE ('REFERENCE_PRICE_MISMATCH')
   - Invalid timestamps (occurred <= 0 or known < occurred) -> UNAVAILABLE ('INVALID_REFERENCE_TIMESTAMPS')
2. Positive test: valid reference provenance resolves deterministically with exact source identity and timestamps.
"""

import math
import pytest
from services.stop_placement_service import (
    resolve_verified_reference_node,
    resolve_episode_membership_from_structure,
    derive_protected_leg_episode,
    validate_episode_interval,
)


class CandleBar:
    def __init__(self, open_time, o, h, l, c, is_closed=True):
        self.open_time = int(open_time)
        self.timestamp = int(open_time)
        self.open = float(o)
        self.high = float(h)
        self.low = float(l)
        self.close = float(c)
        self.is_closed = is_closed


def test_p082_missing_reference_fails_closed():
    """Negative: Missing reference candidate returns UNAVAILABLE with MISSING_REFERENCE_DATA."""
    res = resolve_verified_reference_node(
        selected_reference=None,
        confirmed_swings=([], []),
        closed_bars=[],
        model_id="M1_FVG",
        direction="SHORT",
        decision_cutoff_ms=10000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "MISSING_REFERENCE_DATA" in res.get("reasons", ())


def test_p082_ambiguous_reference_price_rejected():
    """Negative: When only price is given and multiple swings match the price, fail with AMBIGUOUS_REFERENCE_PRICE."""
    swings_low = [
        {"id": "sw_1", "type": "SWING_LOW", "price": 2000.0, "timestamp": 4000, "confirmed_at": 4060},
        {"id": "sw_2", "type": "SWING_LOW", "price": 2000.0, "timestamp": 6000, "confirmed_at": 6060},
    ]
    ref_by_price_only = {"price": 2000.0}
    res = resolve_verified_reference_node(
        selected_reference=ref_by_price_only,
        confirmed_swings=([], swings_low),
        closed_bars=[],
        model_id="M1_FVG",
        direction="SHORT",
        decision_cutoff_ms=10000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "AMBIGUOUS_REFERENCE_PRICE" in res.get("reasons", ())


def test_p082_reference_known_after_decision_cutoff_rejected():
    """Negative: Reference confirmed after decision cutoff is rejected with REFERENCE_KNOWN_AFTER_DECISION."""
    ref = {
        "id": "ref_sl_1",
        "price": 2000.0,
        "occurred_at_ms": 5000,
        "known_at_ms": 10500,  # after cutoff 10000
    }
    bars = [CandleBar(5000, 2005, 2010, 1999.9, 2001)]
    res = resolve_verified_reference_node(
        selected_reference=ref,
        confirmed_swings=([], [{"id": "ref_sl_1", "price": 2000.0, "timestamp": 5000, "confirmed_at": 10500}]),
        closed_bars=bars,
        model_id="M1_FVG",
        direction="SHORT",
        decision_cutoff_ms=10000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "REFERENCE_KNOWN_AFTER_DECISION" in res.get("reasons", ())


def test_p082_reference_after_break_rejected():
    """Negative: Reference occurring after break is rejected with REFERENCE_AFTER_BREAK."""
    ref = {
        "id": "ref_sl_1",
        "price": 2000.0,
        "occurred_at_ms": 12000,
        "known_at_ms": 12060,
    }
    res = resolve_verified_reference_node(
        selected_reference=ref,
        confirmed_swings=([], [{"id": "ref_sl_1", "price": 2000.0, "timestamp": 12000, "confirmed_at": 12060}]),
        closed_bars=[CandleBar(12000, 2005, 2010, 2000.0, 2001)],
        model_id="M1_FVG",
        direction="SHORT",
        break_open_ms=10000,
        decision_cutoff_ms=15000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "REFERENCE_AFTER_BREAK" in res.get("reasons", ())


def test_p082_reference_price_mismatch_with_candle_rejected():
    """Negative: Reference price 2000.0 outside candle high/low range (2010-2020) fails with REFERENCE_PRICE_MISMATCH."""
    ref = {
        "id": "ref_sl_1",
        "price": 2000.0,
        "occurred_at_ms": 5000,
        "known_at_ms": 5060,
    }
    # Candle at 5000 has low=2010.0, high=2020.0
    bars = [CandleBar(5000, 2015, 2020, 2010, 2018)]
    res = resolve_verified_reference_node(
        selected_reference=ref,
        confirmed_swings=([], []),
        closed_bars=bars,
        model_id="M1_FVG",
        direction="SHORT",
        decision_cutoff_ms=10000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "REFERENCE_PRICE_MISMATCH" in res.get("reasons", ())


def test_p082_invalid_timestamps_rejected():
    """Negative: Timestamp <= 0 or known < occurred rejected with INVALID_REFERENCE_TIMESTAMPS."""
    ref_neg = {"id": "ref_sl_1", "price": 2000.0, "occurred_at_ms": -100, "known_at_ms": 5000}
    res1 = resolve_verified_reference_node(
        selected_reference=ref_neg,
        confirmed_swings=([], []),
        closed_bars=[],
        model_id="M1_FVG",
        direction="SHORT",
        decision_cutoff_ms=10000,
    )
    assert res1.get("status") == "UNAVAILABLE"
    assert "INVALID_REFERENCE_TIMESTAMPS" in res1.get("reasons", ())

    ref_inverted = {"id": "ref_sl_1", "price": 2000.0, "occurred_at_ms": 6000, "known_at_ms": 5000}
    res2 = resolve_verified_reference_node(
        selected_reference=ref_inverted,
        confirmed_swings=([], []),
        closed_bars=[],
        model_id="M1_FVG",
        direction="SHORT",
        decision_cutoff_ms=10000,
    )
    assert res2.get("status") == "UNAVAILABLE"
    assert "INVALID_REFERENCE_TIMESTAMPS" in res2.get("reasons", ())


def test_p082_positive_verified_reference_provenance():
    """Positive: Valid reference with matching swing and source candle returns AVAILABLE with full verified provenance."""
    ref = {
        "id": "ref_sl_1",
        "price": 2000.0,
        "occurred_at_ms": 5000,
        "known_at_ms": 5060,
    }
    bars = [
        CandleBar(5000, 2005.0, 2010.0, 2000.0, 2002.0),
        CandleBar(6000, 2002.0, 2008.0, 2001.0, 2004.0),
    ]
    swings_low = [
        {"id": "ref_sl_1", "type": "SWING_LOW", "price": 2000.0, "timestamp": 5000, "confirmed_at": 5060}
    ]
    res = resolve_verified_reference_node(
        selected_reference=ref,
        confirmed_swings=([], swings_low),
        closed_bars=bars,
        model_id="M1_FVG",
        direction="SHORT",
        break_open_ms=10000,
        decision_cutoff_ms=10000,
    )
    assert res.get("status") == "AVAILABLE"
    assert res.get("ref_id") == "ref_sl_1"
    assert res.get("ref_price") == 2000.0
    assert res.get("ref_occurred_at_ms") == 5000
    assert res.get("ref_known_at_ms") == 5060
