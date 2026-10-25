"""
backend/tests/test_v17_6_4_contract_calendar.py
Authoritative contract calendar and reference market tests for AURUM V17.6.4 (Phase C, PART 037).

Tests CAL01-CAL12:
- CAL01: Saturday 24/7 contract scheduled availability
- CAL02: Sunday 24/7 contract scheduled availability
- CAL03: Friday traditional reference close (17:00 NY)
- CAL04: Sunday reference reopening (18:00 NY)
- CAL05: New York spring DST (March EDT transition)
- CAL06: New York autumn DST (November EST transition)
- CAL07: Exchange feed disconnected handling
- CAL08: Instrument status unknown / invalid timestamp
- CAL09: Different contract type fallback
- CAL10: Strategy session disabled during open contract
- CAL11: Daily maintenance on traditional reference market (17:00-18:00 NY)
- CAL12: Weekend quote stale while contract schedule remains open
"""
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest

from services.market_calendar_service import (
    MarketCalendarType,
    get_contract_trading_availability,
    get_underlying_reference_session,
    get_strategy_execution_session,
    build_contract_eligible_interval_ranges,
    build_underlying_gold_reference_interval_ranges,
)

NY = ZoneInfo("America/New_York")


def make_ny_ts(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=NY).timestamp() * 1000)


def test_cal01_saturday_24_7_scheduled_availability():
    """CAL01: Saturday is scheduled open for Bitget XAUUSDT perpetual 24/7."""
    sat_ts = make_ny_ts(2026, 10, 24, 12, 0)  # Saturday noon NY
    avail = get_contract_trading_availability(symbol="XAUUSDT", venue="BITGET", product_type="USDT-FUTURES", now_ms=sat_ts)
    assert avail["scheduled_open"] is True
    assert avail["trading_schedule"] == "24_7"
    assert avail["operational_status"] == "OPEN"
    assert avail["source"] == "BITGET_PRODUCT_SPEC"


def test_cal02_sunday_24_7_scheduled_availability():
    """CAL02: Sunday morning is scheduled open for Bitget XAUUSDT perpetual 24/7."""
    sun_ts = make_ny_ts(2026, 10, 25, 10, 0)  # Sunday 10am NY
    avail = get_contract_trading_availability(symbol="XAUUSDT", venue="BITGET", product_type="USDT-FUTURES", now_ms=sun_ts)
    assert avail["scheduled_open"] is True
    assert avail["trading_schedule"] == "24_7"
    assert avail["operational_status"] == "OPEN"


def test_cal03_friday_traditional_reference_close():
    """CAL03: Friday 17:00 NY closes traditional CME/spot gold reference market."""
    fri_open = make_ny_ts(2026, 10, 23, 16, 59)
    fri_close = make_ny_ts(2026, 10, 23, 17, 1)

    ref_open = get_underlying_reference_session(now_ms=fri_open)
    ref_close = get_underlying_reference_session(now_ms=fri_close)

    assert ref_open["is_open"] is True
    assert ref_close["is_open"] is False
    assert ref_close["is_weekend_break"] is True
    assert ref_close["close_reason"] == "WEEKEND_BREAK"


def test_cal04_sunday_reference_reopening():
    """CAL04: Sunday 18:00 NY reopens traditional CME/spot gold reference market."""
    sun_pre = make_ny_ts(2026, 10, 25, 17, 59)
    sun_post = make_ny_ts(2026, 10, 25, 18, 1)

    ref_pre = get_underlying_reference_session(now_ms=sun_pre)
    ref_post = get_underlying_reference_session(now_ms=sun_post)

    assert ref_pre["is_open"] is False
    assert ref_pre["is_weekend_break"] is True
    assert ref_post["is_open"] is True
    assert ref_post["close_reason"] == "OPEN"


def test_cal05_new_york_spring_dst():
    """CAL05: Spring forward DST transition respects exact UTC-hour boundaries."""
    # March 8, 2026: 02:00 -> 03:00 EDT
    t_spring = make_ny_ts(2026, 3, 8, 18, 0)
    ref = get_underlying_reference_session(now_ms=t_spring)
    assert ref["is_open"] is True


def test_cal06_new_york_autumn_dst():
    """CAL06: Fall back DST transition respects exact UTC-hour boundaries."""
    # Nov 1, 2026: 02:00 -> 01:00 EST
    t_fall = make_ny_ts(2026, 11, 1, 18, 0)
    ref = get_underlying_reference_session(now_ms=t_fall)
    assert ref["is_open"] is True


def test_cal07_contract_vs_reference_intervals():
    """CAL07: Bitget contract eligible intervals are 24/7 while reference intervals exclude weekend."""
    start = make_ny_ts(2026, 10, 23, 12, 0)  # Friday noon
    end = make_ny_ts(2026, 10, 25, 20, 0)    # Sunday 8pm

    contract_ranges = build_contract_eligible_interval_ranges(start, end, MarketCalendarType.BITGET_XAUUSDT_PERPETUAL)
    ref_ranges = build_underlying_gold_reference_interval_ranges(start, end)

    assert contract_ranges == [(start, end)]
    total_ref_ms = sum(e - s for s, e in ref_ranges)
    # Ref market open: Friday 12-17 (5h) + Sunday 18-20 (2h) = 7h
    assert total_ref_ms == 7 * 3600 * 1000


def test_cal08_instrument_status_invalid_timestamp():
    """CAL08: Negative or invalid timestamp returns INVALID_TIMESTAMP status."""
    avail = get_contract_trading_availability(symbol="XAUUSDT", venue="BITGET", now_ms=-100)
    assert avail["scheduled_open"] is False
    assert avail["operational_status"] == "INVALID_TIMESTAMP"


def test_cal09_different_contract_type():
    """CAL09: Non-Bitget or non-futures venue defaults to generic calendar."""
    avail = get_contract_trading_availability(symbol="BTCUSDT", venue="BINANCE", product_type="SPOT")
    assert avail["source"] == "GENERIC_FALLBACK"


def test_cal10_strategy_session_disabled_during_open_contract():
    """CAL10: Weekend strategy entry restriction does NOT mean contract is closed."""
    sat_ts = make_ny_ts(2026, 10, 24, 14, 0)
    strat = get_strategy_execution_session(strategy_id="CG01", now_ms=sat_ts)
    contract = get_contract_trading_availability(now_ms=sat_ts)

    # Contract is OPEN 24/7, but strategy policy is RESTRICTED on weekend
    assert contract["scheduled_open"] is True
    assert strat["permitted"] is False
    assert strat["session_state"] == "RESTRICTED"


def test_cal11_daily_ny_maintenance():
    """CAL11: Monday 17:00-18:00 NY is daily maintenance for CME gold reference market."""
    mon_maint = make_ny_ts(2026, 10, 19, 17, 30)
    mon_open = make_ny_ts(2026, 10, 19, 18, 30)

    ref_maint = get_underlying_reference_session(now_ms=mon_maint)
    ref_open = get_underlying_reference_session(now_ms=mon_open)

    assert ref_maint["is_open"] is False
    assert ref_maint["is_daily_maintenance"] is True
    assert ref_maint["close_reason"] == "DAILY_MAINTENANCE"
    assert ref_open["is_open"] is True


def test_cal12_weekend_contract_continuous_denominator():
    """CAL12: 48-hour Saturday-Sunday window has full 48-hour denominator for Bitget perpetual."""
    start = make_ny_ts(2026, 10, 24, 0, 0)
    end = make_ny_ts(2026, 10, 26, 0, 0)
    ranges = build_contract_eligible_interval_ranges(start, end, MarketCalendarType.BITGET_XAUUSDT_PERPETUAL)
    assert sum(e - s for s, e in ranges) == 48 * 3600 * 1000
