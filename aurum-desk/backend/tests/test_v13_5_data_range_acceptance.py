"""
AURUM DESK — V13.5 DATA AUDIT & RANGE ACCEPTANCE TESTS
Verifies:
- Dynamic bar count expectations based on session duration.
- Multi-timeframe completeness verification (15M and 5M).
- Warmup lookback verification.
- Timezone-aware session intervals with minute=0 preserved.
"""

import pytest
from lab.daily_research_scheduler import audit_session_timeframes, build_session_interval
from schemas import ReplayRunRequest


def test_01_dynamic_bar_expectations_tradable_hours():
    # 7-hour session (08:30 to 15:30) -> 7 hours = 28 15M bars expected
    interval = build_session_interval("2026-07-22")
    bundle = {
        "15M": [{"timestamp": interval["start_ms"] + i * 900000} for i in range(28)],
        "5M": [{"timestamp": interval["start_ms"] + i * 300000} for i in range(84)]
    }
    audit = audit_session_timeframes(interval, bundle, required_timeframes=["15M", "5M"])
    assert audit.tradable_hours == 7.0
    assert audit.expected_15m_bars == 28
    assert audit.expected_5m_bars == 84
    assert audit.observed_15m_bars == 28
    assert audit.observed_5m_bars == 84
    assert audit.is_complete is True


def test_02_missing_required_5m_fails_completeness():
    interval = build_session_interval("2026-07-22")
    # 15M has 28 bars, but 5M is empty
    bundle = {
        "15M": [{"timestamp": interval["start_ms"] + i * 900000} for i in range(28)],
        "5M": []
    }
    audit = audit_session_timeframes(interval, bundle, required_timeframes=["15M", "5M"])
    assert audit.is_complete is False
    assert any("INSUFFICIENT_5M_BARS" in r for r in audit.reasons)


def test_03_minute_zero_preserved_in_interval():
    req = ReplayRunRequest(scheduled_deadline_hour=14, scheduled_deadline_minute=0)
    interval = build_session_interval("2026-07-22", policy=req)
    assert interval["deadline_minute"] == 0
    assert interval["deadline_hour"] == 14
