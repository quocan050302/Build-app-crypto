"""
backend/tests/test_v16_23_red_reproductions.py
V16.23 Red Defect Reproductions & Precision Contract Verification:
- Rule 021: Worker variable scope (no UnboundLocalError on _IN_FLIGHT_COUNT).
- Rule 022: Worker ready handshake prevents fake fallback pass.
- Rule 023: Queue item ownership retained on failure without data loss.
- Rule 024: Retry group ownership barrier tracking.
- Rule 025: Event-driven scheduler execution via cache closed candle without manual calls.
- Rule 026: Lifespan shutdown captures uncompleted evaluations and drain timeouts/loss.
- Rule 027: Thread evaluation completion tracking detects in-flight OS thread after task cancellation.
- Rule 028: Telemetry ERROR trace distinguished from successful candidate arming.
- Rule 029: Exact source event ID linkage from candle cache closed event.
- Rule 030: Worker start/stop/restart idempotence and state lifecycle.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

# Ensure test DB isolation (Rule 014)
os.environ.setdefault("AURUM_DB_PATH", ":memory:")

from database import SessionLocal, engine
from models import Base
import models
import crud
from services.candle_cache_service import candle_cache_service
from services.cg01_config_factory import (
    compute_cg01_config_fingerprint,
    resolve_canonical_cg01_config,
)
from services.cg01_scheduler_service import (
    CG01EvaluationScheduler,
    cg01_evaluation_scheduler,
    resolve_effective_cg01_config,
)
from services.cg01_telemetry_service import (
    CG01EvaluationTrace,
    CG01TracePersistenceWriter,
    cg01_trace_recorder,
    _IN_FLIGHT_COUNT,
    _RETRY_BUFFER,
    _RETRY_BUFFER_LOCK,
    _TRACE_PERSISTED_COUNT,
    _TRACE_PERSIST_FAILURES,
    _TRACE_QUEUE,
    _TRACE_WORKER_RUNNING,
    _TRACE_WORKER_STATE,
    _persist_trace_batch_sync,
    drain_trace_persistence,
    enqueue_trace_persistence,
    flush_pending_traces,
    get_in_flight_count,
    get_retry_buffer_count,
    get_trace_worker_state,
    is_trace_worker_running,
    reset_telemetry_worker_state,
    run_trace_persistence_worker,
    set_in_flight_count,
    start_trace_persistence_worker,
    stop_trace_persistence_worker,
    wait_trace_persistence_worker_stopped,
)
from services.cg01_types import CG01Config
from services.scalp_m1_types import WatchedZone
from services.collector_service import collector_service
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator, scalp_watcher_orchestrator
from services.strategy_service import strategy_service
from services.trading_policy_service import TradingPolicyService
from tests.test_v16_3_cg01_pipeline import (
    build_long_fixture_series,
    make_valid_quote,
)


@pytest.fixture(autouse=True)
def setup_isolated_db():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        session.query(models.SMCScenarioSnapshot).delete()
        session.query(models.PaperOrder).delete()
        session.query(models.WatchSetup).delete()
        session.query(models.PaperResearchOrderLink).delete()
        session.query(models.PaperResearchMilestone).delete()
        session.query(models.PaperResearchOrderMilestone).delete()
        session.query(models.CG01TraceRecord).delete()
        session.query(models.PaperResearchRunRecord).delete()
        session.query(models.SystemConfig).delete()
        session.query(models.DayAudit).delete()
        session.commit()
        reset_telemetry_worker_state()
        yield session
    finally:
        reset_telemetry_worker_state()
        session.close()


@pytest.fixture
def db(setup_isolated_db):
    yield setup_isolated_db


@pytest.mark.anyio
async def test_red_021_worker_unbound_local_error_on_batch():
    """
    Rule 021, 031-033: Real worker execution on queue and retry items must NOT
    raise UnboundLocalError on _IN_FLIGHT_COUNT and must process batches cleanly.
    """
    reset_telemetry_worker_state()
    t1 = CG01EvaluationTrace(trace_id="trc_scope_test_1", symbol="XAUUSDT")
    t2 = CG01EvaluationTrace(trace_id="trc_scope_test_2", symbol="XAUUSDT")

    _TRACE_QUEUE.put_nowait(t1)
    with _RETRY_BUFFER_LOCK:
        _RETRY_BUFFER.append((t2, 1))

    started = await start_trace_persistence_worker(timeout_s=3.0)
    assert started is True
    assert is_trace_worker_running() is True
    assert get_trace_worker_state() == "RUNNING"

    drain_res = await drain_trace_persistence(timeout_s=3.0)
    assert drain_res["status"] == "COMPLETED"
    assert drain_res["pending"] == 0
    assert drain_res["retry_buffer"] == 0
    assert drain_res["in_flight"] == 0

    stop_trace_persistence_worker()
    await wait_trace_persistence_worker_stopped(timeout_s=2.0)

    # Verify rows in DB
    db = SessionLocal()
    try:
        r1 = db.query(models.CG01TraceRecord).filter(models.CG01TraceRecord.trace_id == "trc_scope_test_1").first()
        r2 = db.query(models.CG01TraceRecord).filter(models.CG01TraceRecord.trace_id == "trc_scope_test_2").first()
        assert r1 is not None
        assert r2 is not None
    finally:
        db.close()


@pytest.mark.anyio
async def test_red_022_worker_ready_race_prevents_fake_pass():
    """
    Rule 022, 037-040: Creating worker task must perform ready handshake so callers
    never encounter STARTING state and fall back to duplicate synchronous consumers.
    """
    reset_telemetry_worker_state()

    # Before start, state is STOPPED
    assert get_trace_worker_state() == "STOPPED"
    assert is_trace_worker_running() is False

    ready = await start_trace_persistence_worker(timeout_s=2.0)
    assert ready is True
    assert get_trace_worker_state() == "RUNNING"
    assert is_trace_worker_running() is True

    # Immediate drain must recognize worker is running and NOT execute fallback flush
    with patch("services.cg01_telemetry_service.flush_pending_traces") as mock_flush:
        res = await drain_trace_persistence(timeout_s=1.0)
        assert res["status"] == "COMPLETED"
        assert mock_flush.call_count == 0

    stop_trace_persistence_worker()
    await wait_trace_persistence_worker_stopped(timeout_s=2.0)
    assert get_trace_worker_state() == "STOPPED"


@pytest.mark.anyio
async def test_red_023_queue_item_ownership_retained_on_failure():
    """
    Rule 023, 043, 045: When a worker pops a trace and DB flush/commit fails,
    the trace must be retained in _RETRY_BUFFER and in-flight accounting maintained.
    """
    reset_telemetry_worker_state()
    t = CG01EvaluationTrace(trace_id="trc_fail_retain_023", symbol="XAUUSDT")
    _TRACE_QUEUE.put_nowait(t)

    # Mock SessionLocal to fail commit
    class FailingSession:
        def merge(self, rec): pass
        def flush(self): pass
        def commit(self): raise RuntimeError("Simulated DB commit error")
        def rollback(self): pass
        def close(self): pass

    with patch("database.SessionLocal", return_value=FailingSession()):
        started = await start_trace_persistence_worker(timeout_s=2.0)
        assert started is True

        # Wait a tick for worker to attempt persist
        await asyncio.sleep(0.3)

        # Worker should have moved the item into _RETRY_BUFFER
        with _RETRY_BUFFER_LOCK:
            retained_ids = [item[0].trace_id for item in _RETRY_BUFFER]
            assert "trc_fail_retain_023" in retained_ids

        stop_trace_persistence_worker()
        await wait_trace_persistence_worker_stopped(timeout_s=2.0)


@pytest.mark.anyio
async def test_red_024_retry_item_ownership_barrier():
    """
    Rule 024, 044, 046: Multiple retry items popped by worker must all be counted
    as owned (in-flight) even if held at a processing barrier before writing.
    """
    reset_telemetry_worker_state()
    t1 = CG01EvaluationTrace(trace_id="trc_retry_bar_1", symbol="XAUUSDT")
    t2 = CG01EvaluationTrace(trace_id="trc_retry_bar_2", symbol="XAUUSDT")

    with _RETRY_BUFFER_LOCK:
        _RETRY_BUFFER.append((t1, 1))
        _RETRY_BUFFER.append((t2, 1))

    barrier = threading.Event()
    orig_persist = _persist_trace_batch_sync

    def _slow_persist(*args, **kwargs):
        barrier.wait()
        return orig_persist(*args, **kwargs)

    with patch("services.cg01_telemetry_service._persist_trace_batch_sync", side_effect=_slow_persist):
        started = await start_trace_persistence_worker(timeout_s=2.0)
        assert started is True

        # Wait for worker to pop retry items and hit the barrier
        for _ in range(20):
            await asyncio.sleep(0.05)
            if get_in_flight_count() == 2:
                break

        # When barrier is held, worker popped both items and _IN_FLIGHT_COUNT is 2
        assert get_in_flight_count() == 2

        # A drain started now must NOT report completed
        drain_task = asyncio.create_task(drain_trace_persistence(timeout_s=1.0))
        await asyncio.sleep(0.1)
        assert not drain_task.done()

        # Release barrier
        barrier.set()
        drain_res = await drain_task

        assert drain_res["status"] == "COMPLETED"
        assert get_in_flight_count() == 0

        stop_trace_persistence_worker()
        await wait_trace_persistence_worker_stopped(timeout_s=2.0)


@pytest.mark.anyio
async def test_red_025_event_driven_scheduler_execution(db: Session):
    """
    Rule 025, 091, 097, 098: Scheduler must execute from a closed candle event
    emitted by candle_cache_service.process_candle_payload() without manual trigger.
    """
    strategy_service.set_auto_state(db, True)
    audit = crud.get_or_create_today_audit(db)
    audit.cooldown_until = None
    audit.fills_count = 0
    audit.realized_pnl = 0.0
    audit.current_equity = 1000.0
    db.commit()

    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 19, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000

    db.add(models.SystemConfig(key="scalp_m1_mode", value="PAPER", updated_at=now_ms))
    db.add(models.SystemConfig(key="cg01_enabled", value="true", updated_at=now_ms))
    db.add(models.SystemConfig(key="cg01_profile_mode", value="PAPER_EXECUTION", updated_at=now_ms))
    db.add(models.SystemConfig(key="execution_profile", value="CG01", updated_at=now_ms))
    db.commit()

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)

    def _ingest(tf, bars):
        rows = [[int(b["timestamp"]), float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"]), 100.0] for b in bars]
        candle_cache_service.process_candle_payload("XAUUSDT", tf, rows)

    _ingest("4H", c_4h)
    _ingest("1H", c_1h)
    _ingest("15M", c_15m)

    poi = WatchedZone(
        zone_id="poi_long_event_025",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BULLISH",
        top=2625.0,
        bottom=2620.0,
        created_at=t_start - 3600_000,
        known_at=t_start - 1800_000,
        touch_count=0,
    )
    scalp_watcher_orchestrator.set_active_zones("XAUUSDT", [poi])

    last_ts = c_1m[-1]["timestamp"]
    trigger_ts = last_ts + 60_000
    now_ms = trigger_ts + 60_000

    collector_service.latest_ticker = {
        "symbol": "XAUUSDT",
        "bid": 2625.5,
        "ask": 2625.7,
        "last": 2625.6,
        "server_time": now_ms,
        "observed_at": now_ms,
    }

    # Configure scheduler singleton with controlled clock for fixture scenario (Rule 018, 101)
    from services.clock import ReplayClock
    cg01_evaluation_scheduler.set_clock(ReplayClock(now_ms))
    cg01_evaluation_scheduler.set_flags(enabled=True, shadow_mode=False, is_owner=True)
    sched_task = asyncio.create_task(cg01_evaluation_scheduler.run())
    worker_started = await start_trace_persistence_worker(timeout_s=2.0)
    assert worker_started is True

    try:
        # Ingest trigger bar and boundary bar to close the trigger bar
        trigger_bar = {"timestamp": trigger_ts, "open": 2625.5, "high": 2626.0, "low": 2625.0, "close": 2625.6}
        boundary_bar = {"timestamp": now_ms, "open": 2625.6, "high": 2626.0, "low": 2625.5, "close": 2625.6}
        _ingest("1M", c_1m + [trigger_bar, boundary_bar])

        # Wait for scheduler to automatically process the closed candle event
        armed_order = None
        for _ in range(30):
            await asyncio.sleep(0.1)
            db.expire_all()
            armed_order = db.query(models.PaperOrder).filter(models.PaperOrder.state.in_(["armed", "placed"])).first()
            if armed_order:
                break

        assert armed_order is not None, "Scheduler failed to automatically arm order from cache closed candle"
        assert armed_order.direction.upper() == "LONG"
        assert armed_order.take_profit > armed_order.planned_entry > armed_order.stop_loss
    finally:
        cg01_evaluation_scheduler.stop()
        sched_task.cancel()
        await asyncio.gather(sched_task, return_exceptions=True)
        stop_trace_persistence_worker()
        await wait_trace_persistence_worker_stopped(timeout_s=2.0)


@pytest.mark.anyio
async def test_red_026_shutdown_timeout_or_loss_recorded():
    """
    Rule 026, 076, 077, 136: Lifespan shutdown must record uncompleted evaluations
    or drain timeouts/loss in structured shutdown result without silently claiming success.
    """
    from main import lifespan, get_last_shutdown_result
    from fastapi import FastAPI

    app = FastAPI()

    async def _noop_loop(*args, **kwargs):
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    # Simulate evaluation timeout in scheduler and isolate external network services
    with patch.object(cg01_evaluation_scheduler, "wait_evaluations_drained", return_value=False), \
         patch("services.bitget_ws_service.bitget_ws_service.run_ws_loop", side_effect=_noop_loop), \
         patch("services.collector_service.collector_service.run_collector_loop", side_effect=_noop_loop):
        async with lifespan(app):
            pass

        res = get_last_shutdown_result()
        assert res["scheduler_drained"] is False
        assert res["status"] in ("TIMED_OUT", "FAILED")


@pytest.mark.anyio
async def test_red_027_thread_evaluation_still_running_detected():
    """
    Rule 027, 072, 073, 133: If synchronous evaluation in thread is still running,
    wait_evaluations_drained must detect active thread and NOT return True prematurely
    even if the async task wrapper was cancelled.
    """
    sched = CG01EvaluationScheduler()
    sched._running = True

    thread_barrier = threading.Event()
    thread_started = threading.Event()

    def _blocking_eval():
        cur_th = threading.current_thread()
        with sched._lock:
            sched._active_eval_threads.add(cur_th)
        thread_started.set()
        try:
            thread_barrier.wait()
        finally:
            with sched._lock:
                sched._active_eval_threads.discard(cur_th)

    eval_thread = threading.Thread(target=_blocking_eval, daemon=True)
    eval_thread.start()
    thread_started.wait(timeout=2.0)

    # Coroutine task wrapper is done/cancelled, but OS thread is still blocked
    assert len(sched._active_eval_threads) == 1
    drained_early = await sched.wait_evaluations_drained(timeout_s=0.2)
    assert drained_early is False, "wait_evaluations_drained returned True while thread was still running!"

    # Unblock thread
    thread_barrier.set()
    eval_thread.join(timeout=2.0)

    # Now it should drain cleanly
    drained_after = await sched.wait_evaluations_drained(timeout_s=1.0)
    assert drained_after is True


@pytest.mark.anyio
async def test_red_028_trace_error_causes_acceptance_failure():
    """
    Rule 028, 100, 102, 139: An evaluation that completes with status ERROR persists
    a trace, but must be recognized as an evaluation failure, not a success.
    """
    sched = CG01EvaluationScheduler()
    sched.set_flags(enabled=True, shadow_mode=False, is_owner=True)

    # Simulate evaluation throwing error
    with patch("services.cg01_scheduler_service.capture_cg01_input_bundle", side_effect=ValueError("Corrupt cache")):
        res = sched.evaluate_symbol_now("XAUUSDT")
        assert res["status"] == "ERROR"
        assert "error" in res

    # The persisted trace must have evaluation_status == ERROR
    trace = cg01_trace_recorder.get_traces(symbol="XAUUSDT", limit=1)[0]
    assert trace.evaluation_status == "ERROR"
    assert "Corrupt cache" in (trace.skip_reason or "")


@pytest.mark.anyio
async def test_red_029_exact_source_event_linkage(db: Session):
    """
    Rule 029, 100, 174: Trace source_event_id must match the exact event format
    emitted by candle_cache_service when closing a candle bar.
    """
    candle_ts = 1760000000000
    expected_event_id = f"XAUUSDT:1M:{candle_ts}:0:CANDLE_CLOSED"

    sched = CG01EvaluationScheduler()
    ev = {
        "symbol": "XAUUSDT",
        "timeframe": "1M",
        "event_type": "CANDLE_CLOSED",
        "candle_timestamp": candle_ts,
        "revision": 0,
        "received_at_ms": candle_ts + 50,
    }
    notify_res = sched.notify_market_change(ev)
    assert notify_res["enqueued"] is True
    assert notify_res["event_id"] == expected_event_id

    # Evaluate using the stored pending event
    source_ev = sched._pending_market_events.get("XAUUSDT")
    assert source_ev is not None
    assert source_ev["event_id"] == expected_event_id

    eval_res = sched.evaluate_symbol_now(db, "XAUUSDT", source_event=source_ev)
    trace_id = eval_res["trace_id"]
    trace = cg01_trace_recorder.get_traces(symbol="XAUUSDT", limit=1)[0]
    assert trace.trace_id == trace_id
    assert trace.source_event_id == expected_event_id


@pytest.mark.anyio
async def test_red_030_worker_start_stop_restart_idempotence():
    """
    Rule 030, 040, 049, 138: Worker start -> stop -> restart lifecycle must be
    completely idempotent, with accurate state transitions and no task leaks.
    """
    reset_telemetry_worker_state()

    # 1. First start
    s1 = await start_trace_persistence_worker(timeout_s=2.0)
    assert s1 is True
    assert get_trace_worker_state() == "RUNNING"

    # 2. Duplicate start is idempotent
    s_dup = await start_trace_persistence_worker(timeout_s=2.0)
    assert s_dup is True
    assert get_trace_worker_state() == "RUNNING"

    # 3. Stop
    stop_trace_persistence_worker()
    stopped = await wait_trace_persistence_worker_stopped(timeout_s=2.0)
    assert stopped is True
    assert get_trace_worker_state() == "STOPPED"

    # 4. Restart
    s2 = await start_trace_persistence_worker(timeout_s=2.0)
    assert s2 is True
    assert get_trace_worker_state() == "RUNNING"

    stop_trace_persistence_worker()
    await wait_trace_persistence_worker_stopped(timeout_s=2.0)
    assert get_trace_worker_state() == "STOPPED"
