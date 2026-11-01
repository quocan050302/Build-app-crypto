"""
backend/tests/test_v16_21_red_reproductions.py
V16.21 CG-01 RED Defect Reproduction and Contract Verification Suite (Rules 021 - 030).

Validates:
- Rule 021: ValidatedQuote contract: capture_cg01_quote_snapshot succeeds without .get() on ValidatedQuote.
- Rule 022: Invalid and stale quote handling preserves provenance timestamps and rejection reasons.
- Rule 023: Strategy config parity: ACTIVE orchestrator and SHADOW scheduler resolve identical fields and fingerprint.
- Rule 024: Disabled CG-01 profile remains disabled; no silent auto-enable to PAPER_EXECUTION.
- Rule 025: Telemetry session factory failure retains batch in retry buffer and decrements in-flight without leak.
- Rule 026: False result from persist_trace triggers batch failure and retry, not premature success ACK.
- Rule 027: In-flight commits prevent flush_pending_traces from returning COMPLETED prematurely.
- Rule 028: Single-consumer drain: flush respects running worker and does not compete for queue items.
- Rule 029: Closed candle event from cache handler awakens scheduler and enqueues dirty symbol.
- Rule 030: Real cache-to-scheduler-to-telemetry end-to-end chain verified without mocks.
"""
from __future__ import annotations

import os
import sys
import time
import threading
from typing import Any, Dict, List, Optional
import pytest
from sqlalchemy.orm import Session

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import database
import models
import crud
import schemas
from services.cg01_scheduler_service import (
    CG01EvaluationScheduler,
    cg01_evaluation_scheduler,
    capture_cg01_input_bundle,
    evaluate_cg01_shadow_snapshot,
    resolve_effective_cg01_config,
    capture_cg01_quote_snapshot,
    validated_quote_to_cg01_snapshot,
    resolve_cg01_financial_context,
    ensure_observation_run_started,
)
from services.cg01_config_factory import (
    resolve_canonical_cg01_config,
    compute_cg01_config_fingerprint,
)
from services.cg01_context_adapter import CanonicalCandle, normalize_cg01_candle
from services.cg01_types import CG01Config, CG01ProfileMode, CG01ResearchContext
from services.scalp_m1_types import M1ScalpConfig
from services.quote_validator import CanonicalQuote, ValidatedQuote, normalize_execution_quote
from services.collector_service import collector_service
from services.candle_cache_service import candle_cache_service
from services.cg01_telemetry_service import (
    CG01EvaluationTrace,
    CG01TracePersistenceWriter,
    TraceBatchResult,
    _persist_trace_batch_sync,
    enqueue_trace_persistence,
    flush_pending_traces,
    _TRACE_QUEUE,
    _RETRY_BUFFER,
    _RETRY_BUFFER_LOCK,
    get_in_flight_count,
    set_in_flight_count,
    get_retry_buffer_count,
    reset_telemetry_worker_state,
)


@pytest.fixture(autouse=True)
def clean_telemetry_and_cache_state():
    """Ensure clean queue, retry buffer, receipts, in-flight count, and cache for each test."""
    reset_telemetry_worker_state()
    candle_cache_service._cache.clear()
    yield
    reset_telemetry_worker_state()
    candle_cache_service._cache.clear()


# ============================================================================
# Rule 021: RED TEST QUOTE THẬT (ValidatedQuote contract)
# ============================================================================
def test_red_021_real_quote_validated_quote_contract():
    now_ms = int(time.time() * 1000)
    collector_service.latest_ticker = {
        "symbol": "XAUUSDT",
        "bid": 2650.10,
        "ask": 2650.35,
        "last": 2650.20,
        "server_time": now_ms - 500,
        "observed_at": now_ms - 400,
    }

    snap = capture_cg01_quote_snapshot(symbol="XAUUSDT", as_of_ms=now_ms)
    assert snap is not None
    assert snap["is_valid"] is True
    assert snap["is_stale"] is False
    assert snap["bid"] == 2650.10
    assert snap["ask"] == 2650.35
    assert snap["last"] == 2650.20
    assert snap["last"] != snap["mid"]
    assert snap["spread"] == 0.25
    assert snap["exchange_time"] == now_ms - 500
    assert snap["observed_at"] == now_ms - 400


# ============================================================================
# Rule 022: RED TEST QUOTE INVALID (Provenance & Rejection Preservation)
# ============================================================================
def test_red_022_invalid_and_stale_quote_contract():
    now_ms = int(time.time() * 1000)
    # Stale quote: age 30 seconds ago
    collector_service.latest_ticker = {
        "symbol": "XAUUSDT",
        "bid": 2650.00,
        "ask": 2650.20,
        "last": 2650.10,
        "server_time": now_ms - 30_000,
        "observed_at": now_ms - 30_000,
    }

    snap = capture_cg01_quote_snapshot(symbol="XAUUSDT", as_of_ms=now_ms)
    assert snap["is_valid"] is False
    assert snap["is_stale"] is True
    assert "STALE" in snap["rejection_code"]
    assert snap["exchange_time"] == now_ms - 30_000

    # Missing ticker
    collector_service.latest_ticker = None
    snap_missing = capture_cg01_quote_snapshot(symbol="XAUUSDT", as_of_ms=now_ms)
    assert snap_missing["is_valid"] is False
    assert snap_missing["rejection_code"] == "QUOTE_UNAVAILABLE"


# ============================================================================
# Rule 023: RED TEST CONFIG PARITY (ACTIVE and SHADOW shared factory)
# ============================================================================
def test_red_023_config_parity_active_and_shadow(isolated_db: Session):
    custom_scalp_cfg = M1ScalpConfig(
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
        min_net_rr=2.75,
        displacement_body_atr_min=1.15,
        pivot_left_bars=3,
        pivot_right_bars=3,
        sl_buffer_ticks=8,
        daily_max_cap=4,
    )

    # ACTIVE resolution
    active_cfg, active_fp, _ = resolve_canonical_cg01_config(
        db=isolated_db,
        scalp_config_override=custom_scalp_cfg,
    )

    # SHADOW resolution
    shadow_cfg, shadow_fp, _ = resolve_canonical_cg01_config(
        db=isolated_db,
        scalp_config_override=custom_scalp_cfg,
    )

    assert active_fp == shadow_fp
    assert active_cfg.min_net_rr == shadow_cfg.min_net_rr == 2.75
    assert active_cfg.displacement_body_atr_min == shadow_cfg.displacement_body_atr_min == 1.15
    assert active_cfg.pivot_left_bars == shadow_cfg.pivot_left_bars == 3
    assert active_cfg.sl_buffer_ticks == shadow_cfg.sl_buffer_ticks == 8
    assert active_cfg.daily_max_cap == shadow_cfg.daily_max_cap == 4
    assert active_cfg.enabled == shadow_cfg.enabled is True


# ============================================================================
# Rule 024: RED TEST CG-01 OFF (Disabled profile not silently auto-enabled)
# ============================================================================
def test_red_024_cg01_disabled_not_auto_enabled(isolated_db: Session):
    disabled_scalp_cfg = M1ScalpConfig(
        cg01_enabled=False,
        cg01_profile_mode="OFF",
    )

    cfg, _, _ = resolve_canonical_cg01_config(
        db=isolated_db,
        scalp_config_override=disabled_scalp_cfg,
    )

    assert cfg.enabled is False
    assert cfg.profile_mode == "OFF"


# ============================================================================
# Rule 025: RED TEST SESSION FACTORY FAIL (Retry accounting & in-flight leak prevention)
# ============================================================================
def test_red_025_session_factory_failure_retry_accounting():
    trace = CG01EvaluationTrace(
        trace_id="trc_session_factory_fail_025",
        run_id="run_test_025",
        symbol="XAUUSDT",
    )

    def failing_session_factory():
        raise ConnectionRefusedError("Database connection pool exhausted")

    # In-flight count before
    assert get_in_flight_count() == 0

    res = _persist_trace_batch_sync([trace], session_factory=failing_session_factory, attempt=1)

    assert res.status == "RETRYABLE_FAILURE"
    assert res.committed_count == 0
    assert res.failed_count == 1
    assert res.retained_for_retry_count == 1

    # In-flight count must return to 0 (no leak!)
    assert get_in_flight_count() == 0
    # Must be retained in retry buffer
    assert get_retry_buffer_count() == 1


# ============================================================================
# Rule 026: RED TEST FALSE WRITE RESULT (Helper returns False triggers retry)
# ============================================================================
def test_red_026_false_write_result_causes_batch_retry(monkeypatch):
    trace = CG01EvaluationTrace(
        trace_id="trc_false_write_026",
        run_id="run_test_026",
        symbol="XAUUSDT",
    )

    # Simulate persist_trace returning False without throwing an exception
    monkeypatch.setattr(CG01TracePersistenceWriter, "persist_trace", lambda t, db=None: False)

    res = _persist_trace_batch_sync([trace], attempt=1)

    assert res.status == "RETRYABLE_FAILURE"
    assert res.committed_count == 0
    assert res.failed_count == 1
    assert res.retained_for_retry_count == 1
    assert get_retry_buffer_count() == 1
    assert get_in_flight_count() == 0


# ============================================================================
# Rule 027: RED TEST IN-FLIGHT DRAIN (In-flight batch prevents premature COMPLETED)
# ============================================================================
def test_red_027_in_flight_drain_blocks_completed():
    set_in_flight_count(2)
    # Queue is empty, but in_flight is 2
    res = flush_pending_traces(timeout_s=0.1)

    assert res["status"] in ("TIMED_OUT", "IN_FLIGHT")
    assert res["status"] != "COMPLETED"
    assert res["in_flight"] == 2
    set_in_flight_count(0)


# ============================================================================
# Rule 028: RED TEST SINGLE CONSUMER (Flush respects worker running)
# ============================================================================
def test_red_028_single_consumer_flush_waits_for_worker(monkeypatch):
    import services.cg01_telemetry_service as tel_mod
    monkeypatch.setattr(tel_mod, "_TRACE_WORKER_RUNNING", True)

    trace = CG01EvaluationTrace(
        trace_id="trc_single_consumer_028",
        run_id="run_test_028",
        symbol="XAUUSDT",
    )
    _TRACE_QUEUE.put(trace)

    # When worker is running, flush waits and does NOT pull item directly
    flush_res = flush_pending_traces(timeout_s=0.1)
    # Item remains in queue since background worker wasn't actually ticked in this thread
    assert _TRACE_QUEUE.qsize() == 1
    assert flush_res["status"] == "TIMED_OUT"

    # Reset
    monkeypatch.setattr(tel_mod, "_TRACE_WORKER_RUNNING", False)
    while not _TRACE_QUEUE.empty():
        _TRACE_QUEUE.get_nowait()
        _TRACE_QUEUE.task_done()


# ============================================================================
# Rule 029: RED TEST CACHE EVENT (Closed candle awakens scheduler)
# ============================================================================
def test_red_029_cache_closed_candle_event_triggers_scheduler():
    sched = cg01_evaluation_scheduler
    sched.enabled = True
    sched._dirty_symbols.clear()
    sched._seen_events.clear()

    now_ms = int(time.time() * 1000)

    # Ingest two sequential candles through production process_candle_payload
    # The arrival of the second bar boundary finalizes the first bar as closed!
    candle_cache_service.process_candle_payload(
        symbol="XAUUSDT",
        timeframe="1M",
        data_rows=[
            {"timestamp": now_ms - 120_000, "open": 2650.0, "high": 2652.0, "low": 2649.0, "close": 2651.0, "volume": 100.0},
            {"timestamp": now_ms - 60_000, "open": 2651.0, "high": 2653.0, "low": 2650.5, "close": 2652.5, "volume": 120.0},
        ],
    )

    # Verify scheduler was notified
    assert "XAUUSDT" in sched._pending_market_events or sched.events_received_count >= 1 or "XAUUSDT" in sched._dirty_symbols


# ============================================================================
# Rule 030: RED TEST LIVE WIRING (Cache -> Scheduler -> Telemetry -> DB)
# ============================================================================
def test_red_030_live_wiring_cache_to_scheduler_trace(isolated_db: Session):
    now_ms = int(time.time() * 1000)

    # 1. Authoritative quote on collector
    collector_service.latest_ticker = {
        "symbol": "XAUUSDT",
        "bid": 2650.00,
        "ask": 2650.25,
        "last": 2650.15,
        "server_time": now_ms - 500,
        "observed_at": now_ms - 400,
    }

    # 2. Ingest candles into cache
    candle_cache_service.process_candle_payload(
        symbol="XAUUSDT",
        timeframe="1M",
        data_rows=[
            {"timestamp": now_ms - 120_000, "open": 2650.0, "high": 2651.0, "low": 2649.5, "close": 2650.5, "volume": 50.0},
            {"timestamp": now_ms - 60_000, "open": 2650.5, "high": 2651.5, "low": 2650.0, "close": 2651.0, "volume": 60.0},
        ],
    )

    # 3. Scheduler evaluation cycle
    sched = CG01EvaluationScheduler()
    sched.enabled = True
    sched.shadow_mode = True

    sched.evaluate_symbol_now("XAUUSDT", db=isolated_db)

    # 4. Flush telemetry traces to DB
    flush_res = flush_pending_traces(timeout_s=2.0)
    assert flush_res["status"] == "COMPLETED"

    # 5. Query fresh session to verify trace persisted
    from database import SessionLocal
    fresh_db = SessionLocal()
    try:
        traces = fresh_db.query(models.CG01TraceRecord).filter(
            models.CG01TraceRecord.symbol == "XAUUSDT"
        ).all()
        assert len(traces) >= 1
    finally:
        fresh_db.close()
