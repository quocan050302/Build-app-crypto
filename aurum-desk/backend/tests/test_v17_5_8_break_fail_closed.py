"""
backend/tests/test_v17_5_8_break_fail_closed.py
AURUM V17.5.8 — Break Validation & Fail-Closed Evidence Suite (Parts 021–030, 083).

Audits and proves:
1. resolve_verified_break_candle enforces strict close-through and eliminates permissive exception paths:
   - Wick-only break (low crosses but close does not) -> UNAVAILABLE ('BREAK_NOT_CLOSED_THROUGH_REFERENCE')
   - Equality at reference (close == reference_price) -> UNAVAILABLE ('BREAK_NOT_CLOSED_THROUGH_REFERENCE')
   - Malformed OHLC (NaN, negative, or low > high) -> UNAVAILABLE ('BREAK_CANDLE_INVALID')
   - Missing break source candle in closed_bars -> UNAVAILABLE ('MISSING_BREAK_SOURCE_CANDLE')
   - Break confirmed after decision cutoff -> UNAVAILABLE ('BREAK_AFTER_DECISION_CUTOFF')
   - Contradictory supplied candle vs closed_bars candle -> UNAVAILABLE ('BREAK_CANDLE_MISMATCH')
2. Integration: derive_protected_leg_episode fails closed on malformed break without throwing unhandled exceptions.
3. Positive test: valid close-through break source candle returns AVAILABLE with complete break metadata.
"""

import math
import pytest
from services.stop_placement_service import (
    resolve_verified_break_candle,
    derive_protected_leg_episode,
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


def test_p083_wick_only_break_rejected():
    """Negative: Low wicks below reference (1998 < 2000), but close is above (2001 > 2000) for SHORT -> fails."""
    # SHORT requires break_close < reference_price
    wick_bar = CandleBar(10000, 2005.0, 2006.0, 1998.0, 2001.0)
    res = resolve_verified_break_candle(
        selected_break={"break_open_ms": 10000, "known_at_ms": 10060, "bar": wick_bar},
        reference_price=2000.0,
        closed_bars=[wick_bar],
        direction="SHORT",
        decision_cutoff_ms=20000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "BREAK_NOT_CLOSED_THROUGH_REFERENCE" in res.get("reasons", ())


def test_p083_break_equality_rejected():
    """Negative: Break close equals reference price exactly -> does not satisfy strict close-through."""
    eq_bar = CandleBar(10000, 2005.0, 2006.0, 1998.0, 2000.0)
    res = resolve_verified_break_candle(
        selected_break={"break_open_ms": 10000, "known_at_ms": 10060, "bar": eq_bar},
        reference_price=2000.0,
        closed_bars=[eq_bar],
        direction="SHORT",
        decision_cutoff_ms=20000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "BREAK_NOT_CLOSED_THROUGH_REFERENCE" in res.get("reasons", ())


def test_p083_malformed_ohlc_rejected():
    """Negative: NaN or inverted low > high in break candle fails with BREAK_CANDLE_INVALID."""
    # NaN close
    nan_bar = CandleBar(10000, 2005.0, 2006.0, 1995.0, float("nan"))
    res_nan = resolve_verified_break_candle(
        selected_break={"break_open_ms": 10000, "known_at_ms": 10060, "bar": nan_bar},
        reference_price=2000.0,
        closed_bars=[nan_bar],
        direction="SHORT",
        decision_cutoff_ms=20000,
    )
    assert res_nan.get("status") == "UNAVAILABLE"
    assert "BREAK_CANDLE_INVALID" in res_nan.get("reasons", ())

    # Inverted low > high
    inverted_bar = CandleBar(10000, 2005.0, 1990.0, 2010.0, 1995.0)
    res_inv = resolve_verified_break_candle(
        selected_break={"break_open_ms": 10000, "known_at_ms": 10060, "bar": inverted_bar},
        reference_price=2000.0,
        closed_bars=[inverted_bar],
        direction="SHORT",
        decision_cutoff_ms=20000,
    )
    assert res_inv.get("status") == "UNAVAILABLE"
    assert "BREAK_CANDLE_INVALID" in res_inv.get("reasons", ())


def test_p083_missing_break_source_candle_rejected():
    """Negative: Break open timestamp not present in closed_bars and no bar in selected_break."""
    res = resolve_verified_break_candle(
        selected_break={"break_open_ms": 10000, "known_at_ms": 10060},
        reference_price=2000.0,
        closed_bars=[],
        direction="SHORT",
        decision_cutoff_ms=20000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "MISSING_BREAK_SOURCE_CANDLE" in res.get("reasons", ())


def test_p083_break_confirmed_after_cutoff_rejected():
    """Negative: Break confirmation time exceeds decision cutoff -> rejected with BREAK_AFTER_DECISION_CUTOFF."""
    bar = CandleBar(10000, 2005.0, 2006.0, 1995.0, 1996.0)
    res = resolve_verified_break_candle(
        selected_break={"break_open_ms": 10000, "known_at_ms": 10060, "bar": bar},
        reference_price=2000.0,
        closed_bars=[bar],
        direction="SHORT",
        decision_cutoff_ms=10030,  # cutoff earlier than confirmation 10060
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "BREAK_AFTER_DECISION_CUTOFF" in res.get("reasons", ())


def test_p083_contradictory_supplied_candle_rejected():
    """Negative: Supplied bar differs from source candle in closed_bars -> fails with BREAK_CANDLE_MISMATCH."""
    source_bar = CandleBar(10000, 2005.0, 2006.0, 1995.0, 1996.0)
    contradictory_bar = CandleBar(10000, 2005.0, 2015.0, 1995.0, 1996.0)  # different high
    res = resolve_verified_break_candle(
        selected_break={"break_open_ms": 10000, "known_at_ms": 10060, "bar": contradictory_bar},
        reference_price=2000.0,
        closed_bars=[source_bar],
        direction="SHORT",
        decision_cutoff_ms=20000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "BREAK_CANDLE_MISMATCH" in res.get("reasons", ())


def test_p083_positive_break_candle_verified():
    """Positive: Valid close-through candle returns AVAILABLE with verified attributes."""
    bar = CandleBar(10000, 2005.0, 2006.0, 1992.0, 1995.0)
    res = resolve_verified_break_candle(
        selected_break={"break_open_ms": 10000, "known_at_ms": 10060, "bar": bar},
        reference_price=2000.0,
        closed_bars=[bar],
        direction="SHORT",
        decision_cutoff_ms=20000,
    )
    assert res.get("status") == "AVAILABLE"
    assert res.get("break_open_ms") == 10000
    assert res.get("b_close") == 1995.0
    assert res.get("break_bar") is bar


def test_p083_derive_protected_leg_episode_fails_closed_on_corrupt_break():
    """Integration: derive_protected_leg_episode fails closed when break bar is corrupt or missing."""
    corrupt_bar = CandleBar(10000, 2005.0, float("inf"), 1995.0, 1996.0)
    episode = derive_protected_leg_episode(
        model_id="M1_FVG",
        direction="SHORT",
        selected_reference={"id": "ref_1", "price": 2000.0, "occurred_at_ms": 5000, "known_at_ms": 5060},
        selected_break={"break_open_ms": 10000, "bar": corrupt_bar},
        confirmed_swings=([], []),
        closed_bars=[CandleBar(5000, 2005, 2010, 2000, 2002), corrupt_bar],
        decision_cutoff_ms=20000,
    )
    assert episode.status == "UNAVAILABLE"
    assert "BREAK_CANDLE_INVALID" in episode.reasons
