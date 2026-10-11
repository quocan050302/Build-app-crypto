import pytest
from lab.daily_research_scheduler import audit_session_timeframes

def test_data_audit_rejects_half_session():
    # PHẦN 21, 61, 103, 135: Having half of expected bars must NOT be marked complete
    # 5 hours session = 20 15M bars expected
    interval = {
        "ny_date": "2026-07-15",
        "start_ms": 100000000,
        "end_ms": 100000000 + 5 * 3600 * 1000
    }
    # Only 10 bars observed (half of 20)
    bars_15m = [{"timestamp": 100000000 + i * 15 * 60 * 1000} for i in range(10)]
    # Warmup bars strictly prior
    prior_bars = [{"timestamp": 100000000 - (30 - i) * 15 * 60 * 1000} for i in range(25)]
    bundle = {"15M": prior_bars + bars_15m, "5M": []}

    audit = audit_session_timeframes(
        session_interval=interval,
        bundle_data=bundle,
        required_timeframes=["15M"]
    )
    assert audit.is_complete is False
    assert any("INSUFFICIENT_BARS_IN_SESSION" in r for r in audit.reasons)

def test_data_audit_accepts_complete_session():
    # Full 20 bars observed
    interval = {
        "ny_date": "2026-07-15",
        "start_ms": 100000000,
        "end_ms": 100000000 + 5 * 3600 * 1000
    }
    bars_15m = [{"timestamp": 100000000 + i * 15 * 60 * 1000} for i in range(20)]
    prior_bars = [{"timestamp": 100000000 - (30 - i) * 15 * 60 * 1000} for i in range(25)]
    bundle = {"15M": prior_bars + bars_15m, "5M": []}

    audit = audit_session_timeframes(
        session_interval=interval,
        bundle_data=bundle,
        required_timeframes=["15M"]
    )
    assert audit.is_complete is True
    assert audit.warmup_status == "COMPLETE"

def test_data_audit_rejects_missing_warmup():
    interval = {
        "ny_date": "2026-07-15",
        "start_ms": 100000000,
        "end_ms": 100000000 + 5 * 3600 * 1000
    }
    bars_15m = [{"timestamp": 100000000 + i * 15 * 60 * 1000} for i in range(20)]
    # Only 2 warmup bars (requires >= 20)
    prior_bars = [{"timestamp": 100000000 - (2 - i) * 15 * 60 * 1000} for i in range(2)]
    bundle = {"15M": prior_bars + bars_15m, "5M": []}

    audit = audit_session_timeframes(
        session_interval=interval,
        bundle_data=bundle,
        required_timeframes=["15M"]
    )
    assert audit.warmup_status == "INCOMPLETE"
    assert any("INSUFFICIENT_WARMUP_LOOKBACK" in r for r in audit.reasons)
