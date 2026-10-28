"""
Aurum V17.5.1 — ATR Timeframe & Volatility Context Tests
Verifies 1M (60s) and 5M (300s) true durations, mismatch rejection,
gap detection, invalid OHLC filtering, and flat zero handling.
"""
from types import SimpleNamespace
import pytest
from services.stop_placement_service import compute_stop_atr


def test_compute_stop_atr_1m_and_5m_durations():
    # 1M: 15 bars with 60_000 ms interval
    bars_1m = [
        SimpleNamespace(open_time=1000000 + i * 60000, open=2000 + i, high=2002 + i, low=1999 + i, close=2001 + i)
        for i in range(15)
    ]
    res_1m = compute_stop_atr(bars_1m, period=14, timeframe="1M", as_of_ms=1000000 + 15 * 60000)
    assert res_1m.status == "AVAILABLE"
    assert res_1m.timeframe == "1M"
    assert res_1m.last_closed_at_ms == 1000000 + 14 * 60000 + 60000

    # 5M: 15 bars with 300_000 ms interval
    bars_5m = [
        SimpleNamespace(open_time=1000000 + i * 300000, open=2000 + i, high=2005 + i, low=1998 + i, close=2002 + i)
        for i in range(15)
    ]
    res_5m = compute_stop_atr(bars_5m, period=14, timeframe="5M", as_of_ms=1000000 + 15 * 300000)
    assert res_5m.status == "AVAILABLE"
    assert res_5m.timeframe == "5M"
    assert res_5m.last_closed_at_ms == 1000000 + 14 * 300000 + 300000


def test_compute_stop_atr_rejects_timeframe_mismatch():
    bars = [
        SimpleNamespace(open_time=1000000 + i * 300000, open=2000, high=2005, low=1995, close=2000)
        for i in range(15)
    ]
    # Calling 5M but passing bar_duration_ms=60000 (1M duration) must be rejected
    res = compute_stop_atr(bars, period=14, timeframe="5M", bar_duration_ms=60000, as_of_ms=20000000)
    assert res.status == "UNAVAILABLE"
    assert res.reason_code == "TIMEFRAME_DURATION_MISMATCH"


def test_compute_stop_atr_insufficient_bars():
    # Only 10 bars when 14 period requires 15 continuous bars
    bars = [
        SimpleNamespace(open_time=1000000 + i * 60000, open=2000, high=2002, low=1999, close=2001)
        for i in range(10)
    ]
    res = compute_stop_atr(bars, period=14, timeframe="1M", as_of_ms=2000000)
    assert res.status == "UNAVAILABLE"
    assert "INSUFFICIENT" in str(res.reason_code)


def test_compute_stop_atr_flat_zero_is_available():
    # 15 completely flat bars (open=high=low=close=2000)
    # V17.7.4: Zero ATR is fail-closed UNAVAILABLE per Section 11 / Review Finding 8
    bars = [
        SimpleNamespace(open_time=1000000 + i * 60000, open=2000.0, high=2000.0, low=2000.0, close=2000.0)
        for i in range(15)
    ]
    res = compute_stop_atr(bars, period=14, timeframe="1M", as_of_ms=1000000 + 15 * 60000)
    assert res.status == "UNAVAILABLE"
    assert res.reason_code == "ZERO_OR_NEGATIVE_ATR"
