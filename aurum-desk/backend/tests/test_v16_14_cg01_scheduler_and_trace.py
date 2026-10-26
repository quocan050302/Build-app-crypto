"""
backend/tests/test_v16_14_cg01_scheduler_and_trace.py
V16.14 Automated Tests for Event-Driven Scheduler, Input Bundle, and Trace Telemetry.

Covers:
- Rules 031-040: Time contract, monotonic span durations, nullable missing milestones, ring buffer boundedness.
- Rules 041-050: Latency percentiles (p50, p95, p99) without hiding tail latency.
- Rules 051-060: notify_market_change, single-flight per symbol, event deduplication.
- Rules 061-064: capture_cg01_input_bundle immutability and causality.
- Rules 070, 161-166: Watchdog mode, feature flags, rollback without data loss.
"""
import pytest
import time
from unittest.mock import MagicMock, patch

from database import SessionLocal
import schemas
from services.cg01_scheduler_service import (
    CG01EvaluationScheduler,
    capture_cg01_input_bundle,
)
from services.cg01_telemetry_service import (
    CG01EvaluationTrace,
    CG01Span,
    CG01TraceRecorder,
)


# =========================================================================
# 1. Trace Contracts and Latency Percentiles (Rules 031-050)
# =========================================================================
def test_cg01_evaluation_trace_delays_and_null_handling():
    """
    Rules 031, 039: Missing timestamps must remain None, not clamped to 0 or negative numbers.
    Valid milestones compute accurate feed, scheduler, engine, arm, and fill delays.
    """
    # Case A: Complete milestone trace
    trace = CG01EvaluationTrace(
        trace_id="trc_001",
        symbol="XAUUSDT",
        exchange_event_time_ms=1000,
        event_received_at=1050,         # 50ms feed delay
        evidence_available_at=1050,
        evidence_known_at=1000,
        evaluation_started_at=1120,     # 70ms scheduler delay
        evaluation_completed_at=1150,   # 30ms detector runtime
        armed_at=1180,                  # 30ms arm delay
        filled_at=1300,                 # 120ms fill delay
    )
    delays = trace.calculate_delays()
    assert delays["feed_delay_ms"] == 50.0
    assert delays["scheduler_delay_ms"] == 70.0
    assert delays["detector_runtime_ms"] == 30.0
    assert delays["arm_delay_ms"] == 30.0
    assert delays["fill_delay_ms"] == 120.0

    # Case B: Incomplete trace with missing exchange time and unfilled order
    incomplete = CG01EvaluationTrace(
        trace_id="trc_002",
        symbol="XAUUSDT",
        exchange_event_time_ms=None,
        event_received_at=2000,
        evaluation_started_at=2020,
        evaluation_completed_at=2050,
        armed_at=None,
        filled_at=None,
    )
    delays_inc = incomplete.calculate_delays()
    assert delays_inc["feed_delay_ms"] is None
    assert delays_inc["scheduler_delay_ms"] == 20.0
    assert delays_inc["detector_runtime_ms"] == 30.0
    assert delays_inc["arm_delay_ms"] is None
    assert delays_inc["fill_delay_ms"] is None


def test_cg01_trace_recorder_percentiles_and_tail_latency():
    """
    Rules 044, 049: Latency metrics must report p50, p95, p99 and not conceal tail spikes with averages.
    """
    recorder = CG01TraceRecorder(maxlen=100)

    # Populate 100 traces with detector runtimes: 95 fast (10ms) and 5 slow tail spikes (200ms)
    for i in range(95):
        recorder.record_trace(
            CG01EvaluationTrace(
                trace_id=f"t_{i}",
                evaluation_started_at=1000,
                evaluation_completed_at=1010,  # 10ms
            )
        )
    for i in range(95, 100):
        recorder.record_trace(
            CG01EvaluationTrace(
                trace_id=f"t_{i}",
                evaluation_started_at=1000,
                evaluation_completed_at=1200,  # 200ms spike
            )
        )

    summary = recorder.get_latency_summary()
    assert summary["sample_count"] == 100
    rt = summary["detector_runtime"]
    assert rt["count"] == 100
    assert rt["p50"] == 10.0
    assert rt["p95"] == 200.0  # Captures tail latency!
    assert rt["p99"] == 200.0
    assert rt["max"] == 200.0


def test_cg01_trace_recorder_bounded_ring_buffer_counts_drops():
    """
    Rule 040: In-memory ring buffer must be strictly bounded and increment dropped counter on overflow.
    """
    recorder = CG01TraceRecorder(maxlen=5)
    for i in range(12):
        recorder.record_trace(CG01EvaluationTrace(trace_id=f"trc_{i}"))

    assert recorder.total_recorded_count == 12
    assert recorder.dropped_traces_count == 7
    traces = recorder.get_traces()
    assert len(traces) == 5
    assert traces[0].trace_id == "trc_11"  # Most recent first


# =========================================================================
# 2. Immutable Input Bundle (Rules 061-064)
# =========================================================================
def test_capture_cg01_input_bundle_immutability_and_causality():
    """
    Rules 061, 062, 064: capture_cg01_input_bundle returns deep-copied immutable closed candles
    without mutating or being mutated by the cache.
    """
    from services.candle_cache_service import candle_cache_service

    # Setup dummy cache entries
    now_ms = 1000000000000
    dur_1m = 60000
    c1 = schemas.CandleCreate(
        symbol="XAUUSDT",
        timeframe="1M",
        timestamp=now_ms - 2 * dur_1m,
        open=2500.0,
        high=2505.0,
        low=2495.0,
        close=2502.0,
        volume=10.0,
        is_closed=True,
    )
    c2 = schemas.CandleCreate(
        symbol="XAUUSDT",
        timeframe="1M",
        timestamp=now_ms - dur_1m,
        open=2502.0,
        high=2508.0,
        low=2500.0,
        close=2507.0,
        volume=12.0,
        is_closed=True,
    )
    # Open unclosed candle
    c_open = schemas.CandleCreate(
        symbol="XAUUSDT",
        timeframe="1M",
        timestamp=now_ms,
        open=2507.0,
        high=2510.0,
        low=2506.0,
        close=2509.0,
        volume=5.0,
        is_closed=False,
    )

    with patch.object(candle_cache_service, "get_candles", return_value=[c1, c2, c_open]):
        bundle = capture_cg01_input_bundle("XAUUSDT", as_of_ms=now_ms)

        candles_1m = bundle["1M"]
        assert len(candles_1m) == 2  # Open candle excluded!
        assert candles_1m[0].close == 2502.0
        assert candles_1m[1].close == 2507.0

        # Verify deep copy isolation: mutating bundle object does not mutate cache object
        candles_1m[0].close = 9999.0
        assert c1.close == 2502.0, "Cache object was mutated in-place!"


# =========================================================================
# 3. Event-Driven Scheduler & Single-Flight (Rules 051-060, 161-166)
# =========================================================================
def test_cg01_scheduler_deduplication_and_dirty_marking():
    """
    Rules 056, 057: Duplicate market events are discarded; new closed candle events mark symbol dirty.
    """
    scheduler = CG01EvaluationScheduler()
    event = {
        "symbol": "XAUUSDT",
        "timeframe": "1M",
        "event_type": "CANDLE_CLOSED",
        "candle_timestamp": 1700000000000,
        "revision": 0,
    }

    res1 = scheduler.notify_market_change(event)
    assert res1["enqueued"] is True
    assert "XAUUSDT" in scheduler._dirty_symbols

    # Duplicate call with exact same event identity
    res2 = scheduler.notify_market_change(event)
    assert res2["enqueued"] is False
    assert res2["reason"] == "DUPLICATE_EVENT"
    assert scheduler.duplicate_events_ignored_count == 1


def test_cg01_scheduler_single_flight_per_symbol():
    """
    Rule 057: Only one evaluation owner runs per symbol concurrently; secondary evaluation is deferred.
    """
    scheduler = CG01EvaluationScheduler()
    symbol = "XAUUSDT"

    # Simulate symbol currently in flight
    with scheduler._lock:
        scheduler._symbol_in_flight.add(symbol)

    db = MagicMock()
    res = scheduler.evaluate_symbol_now(db, symbol)

    assert res["status"] == "DEFERRED_IN_FLIGHT"
    assert symbol in scheduler._dirty_symbols, "Symbol must be re-dirtied so it evaluates once in-flight finishes"


def test_cg01_scheduler_feature_flags_and_shadow_mode():
    """
    Rules 161, 162: Scheduler flags allow clean toggling between shadow mode and execution ownership.
    """
    scheduler = CG01EvaluationScheduler()
    assert scheduler.enabled is True
    assert scheduler.shadow_mode is True
    assert scheduler.is_owner is False

    scheduler.set_flags(shadow_mode=False, is_owner=True)
    assert scheduler.shadow_mode is False
    assert scheduler.is_owner is True

    # Rollback safely without crash (Rule 166)
    scheduler.set_flags(shadow_mode=True, is_owner=False)
    assert scheduler.shadow_mode is True
    assert scheduler.is_owner is False
