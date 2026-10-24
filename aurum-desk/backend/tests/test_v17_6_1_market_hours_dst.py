"""
backend/tests/test_v17_6_1_market_hours_dst.py
V17.6.1 OBS-06 & OBS-07: New York Wall-Clock Market Hours & DST Correctness Tests.
"""

import pytest
from datetime import datetime
import zoneinfo
from services.engine_observability_service import (
    is_xauusdt_market_closed,
    is_xauusdt_weekend_break,
    calculate_market_open_duration_ms,
)

TZ_NY = zoneinfo.ZoneInfo("America/New_York")


def _make_ny_timestamp(year: int, month: int, day: int, hour: int, minute: int) -> int:
    dt = datetime(year, month, day, hour, minute, tzinfo=TZ_NY)
    return int(dt.timestamp() * 1000)


def test_dst_01_friday_market_close_transition_ny():
    """OBS-07: Friday 16:59 NY is OPEN; Friday 17:00 NY transitions to WEEKEND_BREAK."""
    # 2026-10-23 is Friday (EDT - Daylight Saving Time active)
    t_open = _make_ny_timestamp(2026, 10, 23, 16, 59)
    t_closed = _make_ny_timestamp(2026, 10, 23, 17, 0)

    is_c1, r1 = is_xauusdt_market_closed(t_open)
    assert is_c1 is False
    assert r1 == "OPEN"

    is_c2, r2 = is_xauusdt_market_closed(t_closed)
    assert is_c2 is True
    assert r2 == "WEEKEND_BREAK"
    assert is_xauusdt_weekend_break(t_closed) is True


def test_dst_02_saturday_full_weekend_closure():
    """OBS-07: Entire Saturday is WEEKEND_BREAK in New York."""
    t_sat_morning = _make_ny_timestamp(2026, 10, 24, 8, 30)
    t_sat_night = _make_ny_timestamp(2026, 10, 24, 23, 59)

    is_c1, r1 = is_xauusdt_market_closed(t_sat_morning)
    assert is_c1 is True
    assert r1 == "WEEKEND_BREAK"

    is_c2, r2 = is_xauusdt_market_closed(t_sat_night)
    assert is_c2 is True
    assert r2 == "WEEKEND_BREAK"


def test_dst_03_sunday_market_reopen_transition_ny():
    """OBS-07: Sunday 17:59 NY is WEEKEND_BREAK; Sunday 18:00 NY reopens."""
    t_before_reopen = _make_ny_timestamp(2026, 10, 25, 17, 59)
    t_reopen = _make_ny_timestamp(2026, 10, 25, 18, 0)

    is_c1, r1 = is_xauusdt_market_closed(t_before_reopen)
    assert is_c1 is True
    assert r1 == "WEEKEND_BREAK"

    is_c2, r2 = is_xauusdt_market_closed(t_reopen)
    assert is_c2 is False
    assert r2 == "OPEN"


def test_dst_04_daily_maintenance_closure():
    """OBS-07: Mon-Thu 17:00-18:00 NY is DAILY_MAINTENANCE; 18:00 NY is OPEN."""
    # 2026-10-21 is Wednesday
    t_maint = _make_ny_timestamp(2026, 10, 21, 17, 30)
    t_post_maint = _make_ny_timestamp(2026, 10, 21, 18, 0)

    is_c1, r1 = is_xauusdt_market_closed(t_maint)
    assert is_c1 is True
    assert r1 == "DAILY_MAINTENANCE"
    assert is_xauusdt_weekend_break(t_maint) is False  # Maintenance is not weekend

    is_c2, r2 = is_xauusdt_market_closed(t_post_maint)
    assert is_c2 is False
    assert r2 == "OPEN"


def test_dst_05_daylight_savings_transition_awareness():
    """OBS-07: Compares Summer EDT (UTC-4) and Winter EST (UTC-5) schedule consistency."""
    # Summer (EDT, July 2026): Wednesday 17:15 NY is DAILY_MAINTENANCE
    t_summer = _make_ny_timestamp(2026, 7, 15, 17, 15)
    is_c1, r1 = is_xauusdt_market_closed(t_summer)
    assert is_c1 is True
    assert r1 == "DAILY_MAINTENANCE"

    # Winter (EST, January 2026): Wednesday 17:15 NY is DAILY_MAINTENANCE
    t_winter = _make_ny_timestamp(2026, 1, 14, 17, 15)
    is_c2, r2 = is_xauusdt_market_closed(t_winter)
    assert is_c2 is True
    assert r2 == "DAILY_MAINTENANCE"


def test_dst_06_market_open_duration_calculation():
    """OBS-06: Verifies calculate_market_open_duration_ms accurately subtracts weekend closures."""
    # From Friday 12:00 NY to Sunday 20:00 NY:
    # Friday 12:00 to 17:00 = 5 hours open
    # Friday 17:00 to Sunday 18:00 = 49 hours closed (weekend)
    # Sunday 18:00 to 20:00 = 2 hours open
    # Total expected open = 7 hours (25,200,000 ms)
    t_start = _make_ny_timestamp(2026, 10, 23, 12, 0)
    t_end = _make_ny_timestamp(2026, 10, 25, 20, 0)

    open_ms = calculate_market_open_duration_ms(t_start, t_end, step_minutes=15)
    expected_open_ms = 7 * 3600 * 1000
    # Tolerance of step_minutes (15 min)
    assert abs(open_ms - expected_open_ms) <= 15 * 60 * 1000
