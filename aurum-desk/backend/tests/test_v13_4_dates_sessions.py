"""
Aurum Desk V13.4 — Unit tests for dates, session intervals, deadline 00 minutes, and actual data audit.
Phần 10, 11, 12, 16, 18, 81.
"""
import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from schemas import ReplayRunRequest
from lab.daily_research_scheduler import (
    resolve_research_range,
    build_session_interval,
    assess_session_data,
    should_schedule_daily_entry,
    SessionResearchState,
    SessionEligibility,
    VN_TZ,
    NY_TZ
)


def test_01_deadline_minute_zero_preserved():
    """D01: scheduled_deadline_minute=0 is strictly preserved, NOT replaced with 30."""
    req = ReplayRunRequest(
        scheduled_deadline_hour=14,
        scheduled_deadline_minute=0,
        symbol="XAUUSDT"
    )
    req.validate_and_normalize()
    assert req.scheduled_deadline_minute == 0
    assert req.ny_deadline_minute == 0

    interval = build_session_interval("2026-07-15", req)
    assert interval["deadline_minute"] == 0


def test_02_resolve_research_range_end_exclusive():
    """D02: Range end-date inclusive in VN_TZ is converted to exclusive start of next day."""
    req = ReplayRunRequest(
        start_date="2026-07-09",
        end_date="2026-07-10",
        symbol="XAUUSDT"
    )
    now_ms = int(datetime(2026, 10, 10, 12, 0, 0, tzinfo=VN_TZ).timestamp() * 1000)
    s_ts, e_ts, meta = resolve_research_range(req, now_ms=now_ms)

    expected_s_dt = datetime(2026, 7, 9, 0, 0, 0, tzinfo=VN_TZ)
    expected_e_dt = datetime(2026, 7, 11, 0, 0, 0, tzinfo=VN_TZ)  # Next day start

    assert s_ts == int(expected_s_dt.timestamp() * 1000)
    assert e_ts == int(expected_e_dt.timestamp() * 1000)
    assert meta["start_date_vn"] == "2026-07-09"
    assert meta["end_date_vn"] == "2026-07-10"


def test_03_assess_session_data_weekend_and_insufficient_bars():
    """D03: Weekend detection and bar count audit in session window."""
    # Saturday 2026-07-11
    interval_sat = build_session_interval("2026-07-11")
    m_open, d_comp, _, reasons = assess_session_data(interval_sat, candles_15m=[])
    assert m_open is False
    assert "WEEKEND_MARKET_CLOSED" in reasons

    # Wednesday 2026-07-15 with only 2 bars (insufficient)
    interval_wed = build_session_interval("2026-07-15")
    s_ms = interval_wed["start_ms"]
    sparse_candles = [
        {"timestamp": s_ms + 1000, "close": 2650.0},
        {"timestamp": s_ms + 2000, "close": 2651.0}
    ]
    m_open_w, d_comp_w, _, reasons_w = assess_session_data(interval_wed, candles_15m=sparse_candles)
    assert m_open_w is True
    assert d_comp_w is False
    assert any("INSUFFICIENT_BARS" in r for r in reasons_w)
