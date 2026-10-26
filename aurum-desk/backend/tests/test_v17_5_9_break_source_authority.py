"""
backend/tests/test_v17_5_9_break_source_authority.py
AURUM V17.5.9 — Break Source Authority & Timestamp Suite (D02, D06, Section 03 & Section 09 Part 072).

Cases:
- B01: Supplied bar without canonical proof -> REJECT ('MISSING_BREAK_SOURCE_CANDLE')
- B02: Canonical bar exists, supplied bar conflicts -> REJECT ('BREAK_CANDLE_MISMATCH')
- B03: Missing break time -> REJECT ('MISSING_BREAK_TIME')
- B04: Invalid break-time string -> REJECT ('INVALID_BREAK_TIMESTAMP')
- B05: NaN timestamp -> REJECT ('INVALID_BREAK_TIMESTAMP')
- B06: Negative timestamp -> REJECT ('INVALID_BREAK_TIMESTAMP')
- B07: Confirmation before valid candle close -> REJECT ('BREAK_CONFIRMATION_BEFORE_CLOSE')
- B08: Confirmation after decision cutoff -> REJECT ('BREAK_AFTER_DECISION_CUTOFF')
- B09: Wick-only break -> REJECT ('BREAK_NOT_CLOSED_THROUGH_REFERENCE')
- B10: Close equal reference -> REJECT ('BREAK_NOT_CLOSED_THROUGH_REFERENCE')
- B11: Valid confirmed LONG break -> AVAILABLE
- B12: Valid confirmed SHORT break -> AVAILABLE
"""

import math
import pytest
from services.stop_placement_service import resolve_verified_break_candle


class DummyBar:
    def __init__(self, open_time: int, o: float, h: float, l: float, c: float, is_closed: bool = True):
        self.open_time = int(open_time)
        self.timestamp = int(open_time)
        self.open = float(o)
        self.high = float(h)
        self.low = float(l)
        self.close = float(c)
        self.is_closed = is_closed


def test_b01_supplied_bar_without_canonical_proof():
    """B01: Caller-supplied bar cannot establish provenance if missing from closed_bars -> REJECT ('MISSING_BREAK_SOURCE_CANDLE')."""
    supplied = DummyBar(10000, 2650.0, 2652.0, 2640.0, 2642.0)
    # closed_bars is empty; supplied bar alone must NOT be accepted!
    res = resolve_verified_break_candle(
        selected_break={"break_open_ms": 10000, "known_at_ms": 10060, "bar": supplied},
        reference_price=2645.0,
        closed_bars=[],
        direction="SHORT",
        decision_cutoff_ms=20000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "MISSING_BREAK_SOURCE_CANDLE" in res.get("reasons", ())


def test_b02_canonical_bar_exists_supplied_bar_conflicts():
    """B02: Canonical bar exists in closed_bars, but supplied bar has conflicting OHLC -> REJECT ('BREAK_CANDLE_MISMATCH')."""
    canonical = DummyBar(10000, 2650.0, 2652.0, 2640.0, 2642.0)
    conflicting = DummyBar(10000, 2650.0, 2660.0, 2640.0, 2642.0)  # different high
    res = resolve_verified_break_candle(
        selected_break={"break_open_ms": 10000, "known_at_ms": 10060, "bar": conflicting},
        reference_price=2645.0,
        closed_bars=[canonical],
        direction="SHORT",
        decision_cutoff_ms=20000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "BREAK_CANDLE_MISMATCH" in res.get("reasons", ())


def test_b03_missing_break_time():
    """B03: Missing break timestamp rejected with MISSING_BREAK_TIME."""
    res = resolve_verified_break_candle(
        selected_break={},
        reference_price=2645.0,
        closed_bars=[],
        direction="SHORT",
        decision_cutoff_ms=20000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "MISSING_BREAK_TIME" in res.get("reasons", ())


def test_b04_invalid_break_time_string():
    """B04: Malformed non-numeric timestamp string rejected with INVALID_BREAK_TIMESTAMP."""
    res = resolve_verified_break_candle(
        selected_break={"break_open_ms": "NOT_A_TIMESTAMP"},
        reference_price=2645.0,
        closed_bars=[],
        direction="SHORT",
        decision_cutoff_ms=20000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "INVALID_BREAK_TIMESTAMP" in res.get("reasons", ())


def test_b05_nan_timestamp():
    """B05: NaN timestamp rejected with INVALID_BREAK_TIMESTAMP."""
    res = resolve_verified_break_candle(
        selected_break={"break_open_ms": float("nan")},
        reference_price=2645.0,
        closed_bars=[],
        direction="SHORT",
        decision_cutoff_ms=20000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "INVALID_BREAK_TIMESTAMP" in res.get("reasons", ())


def test_b06_negative_timestamp():
    """B06: Negative timestamp rejected with INVALID_BREAK_TIMESTAMP."""
    res = resolve_verified_break_candle(
        selected_break={"break_open_ms": -5000},
        reference_price=2645.0,
        closed_bars=[],
        direction="SHORT",
        decision_cutoff_ms=20000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "INVALID_BREAK_TIMESTAMP" in res.get("reasons", ())


def test_b07_confirmation_before_valid_candle_close():
    """B07: Break confirmed before or at candle open (break_known_ms <= b_open_ms) -> REJECT ('BREAK_CONFIRMATION_BEFORE_CLOSE')."""
    bar = DummyBar(10000, 2650.0, 2652.0, 2640.0, 2642.0)
    res = resolve_verified_break_candle(
        selected_break={"break_open_ms": 10000, "known_at_ms": 10000, "bar": bar},
        reference_price=2645.0,
        closed_bars=[bar],
        direction="SHORT",
        decision_cutoff_ms=20000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "BREAK_CONFIRMATION_BEFORE_CLOSE" in res.get("reasons", ())


def test_b08_confirmation_after_decision_cutoff():
    """B08: Break confirmed at 25000 after decision cutoff 20000 -> REJECT ('BREAK_AFTER_DECISION_CUTOFF')."""
    bar = DummyBar(10000, 2650.0, 2652.0, 2640.0, 2642.0)
    res = resolve_verified_break_candle(
        selected_break={"break_open_ms": 10000, "known_at_ms": 25000, "bar": bar},
        reference_price=2645.0,
        closed_bars=[bar],
        direction="SHORT",
        decision_cutoff_ms=20000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "BREAK_AFTER_DECISION_CUTOFF" in res.get("reasons", ())


def test_b09_wick_only_break_rejected():
    """B09: Low wicks below reference (2640 < 2645) but close is above (2648 > 2645) for SHORT -> fails strict close-through."""
    wick_bar = DummyBar(10000, 2650.0, 2652.0, 2640.0, 2648.0)
    res = resolve_verified_break_candle(
        selected_break={"break_open_ms": 10000, "known_at_ms": 10060, "bar": wick_bar},
        reference_price=2645.0,
        closed_bars=[wick_bar],
        direction="SHORT",
        decision_cutoff_ms=20000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "BREAK_NOT_CLOSED_THROUGH_REFERENCE" in res.get("reasons", ())


def test_b10_close_equal_reference_rejected():
    """B10: Candle close equals reference price exactly -> does not satisfy strict close-through."""
    eq_bar = DummyBar(10000, 2650.0, 2652.0, 2640.0, 2645.0)
    res = resolve_verified_break_candle(
        selected_break={"break_open_ms": 10000, "known_at_ms": 10060, "bar": eq_bar},
        reference_price=2645.0,
        closed_bars=[eq_bar],
        direction="SHORT",
        decision_cutoff_ms=20000,
    )
    assert res.get("status") == "UNAVAILABLE"
    assert "BREAK_NOT_CLOSED_THROUGH_REFERENCE" in res.get("reasons", ())


def test_b11_valid_confirmed_long_break():
    """B11: Valid confirmed LONG break closes strictly above reference price -> AVAILABLE."""
    long_break_bar = DummyBar(10000, 2640.0, 2655.0, 2638.0, 2652.0)
    res = resolve_verified_break_candle(
        selected_break={"break_open_ms": 10000, "known_at_ms": 10060, "bar": long_break_bar},
        reference_price=2648.0,  # 2652.0 > 2648.0 strictly closes through
        closed_bars=[long_break_bar],
        direction="LONG",
        decision_cutoff_ms=20000,
    )
    assert res.get("status") == "AVAILABLE"
    assert res.get("break_open_ms") == 10000
    assert res.get("b_close") == 2652.0


def test_b12_valid_confirmed_short_break():
    """B12: Valid confirmed SHORT break closes strictly below reference price -> AVAILABLE."""
    short_break_bar = DummyBar(10000, 2650.0, 2652.0, 2638.0, 2641.0)
    res = resolve_verified_break_candle(
        selected_break={"break_open_ms": 10000, "known_at_ms": 10060, "bar": short_break_bar},
        reference_price=2645.0,  # 2641.0 < 2645.0 strictly closes through
        closed_bars=[short_break_bar],
        direction="SHORT",
        decision_cutoff_ms=20000,
    )
    assert res.get("status") == "AVAILABLE"
    assert res.get("break_open_ms") == 10000
    assert res.get("b_close") == 2641.0
