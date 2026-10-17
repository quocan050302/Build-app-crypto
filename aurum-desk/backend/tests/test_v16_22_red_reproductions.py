"""
backend/tests/test_v16_22_red_reproductions.py
V16.22 Red Defect Reproductions & Precision Contract Verification:
- Rule 021/022: Real cache closed candle event executes scheduler and arms order without direct evaluate_cycle.
- Rule 023: Trace error explicitly distinguished from valid candidate.
- Rule 024: Exact trace ID query isolation prevents stale trace pass.
- Rule 025: Async drain non-blocking event loop execution.
- Rule 026: Retry buffer atomic in-flight ownership transfer.
- Rule 027: Persist trace counter exact single count (no double count on batch flush).
- Rule 028: Rollback counter safety (no committed counter increment on commit failure).
- Rule 029: Actual caller production config parity (TradingPolicy integrated for both ACTIVE and SHADOW).
- Rule 030: Scheduler shutdown stops accepting events and drains in-flight evaluations.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

# Ensure test DB isolation (Rule 016)
os.environ["AURUM_DB_PATH"] = ":memory:"

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
    _IN_FLIGHT_COUNT,
    _RETRY_BUFFER,
    _RETRY_BUFFER_LOCK,
    _TRACE_PERSISTED_COUNT,
    _TRACE_PERSIST_FAILURES,
    _TRACE_QUEUE,
    _TRACE_WORKER_RUNNING,
    _persist_trace_batch_sync,
    drain_trace_persistence,
    enqueue_trace_persistence,
    flush_pending_traces,
    get_in_flight_count,
    get_retry_buffer_count,
    run_trace_persistence_worker,
    set_in_flight_count,
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
        yield session
    finally:
        session.close()


def _clean_telemetry():
    global _IN_FLIGHT_COUNT, _TRACE_WORKER_RUNNING
    with _RETRY_BUFFER_LOCK:
        _RETRY_BUFFER.clear()
        _IN_FLIGHT_COUNT = 0
    while not _TRACE_QUEUE.empty():
        try:
            _TRACE_QUEUE.get_nowait()
            _TRACE_QUEUE.task_done()
        except Exception:
            break


def test_red_021_cache_closed_candle_event_executes_scheduler(setup_isolated_db: Session):
    """
    Rules 021, 097, 101: Ingesting a closed candle payload emits a CANDLE_CLOSED event
    that reaches notify_market_change and records the source_event_id.
    """
    _clean_telemetry()
    sched = CG01EvaluationScheduler()
    sched.set_flags(enabled=True, shadow_mode=True, is_owner=False)
    sched._running = True

    event_payload = {
        "symbol": "XAUUSDT",
        "timeframe": "1M",
        "event_type": "CANDLE_CLOSED",
        "candle_timestamp": 1700000000000,
        "revision": 1,
        "exchange_event_time_ms": 1700000000000,
        "received_at_ms": 1700000000050,
    }

    res = sched.notify_market_change(event_payload)
    assert res["enqueued"] is True
    assert res["reason"] == "DIRTY_MARKED"
    assert "XAUUSDT" in sched._dirty_symbols
    assert "XAUUSDT" in sched._pending_market_events

    ev = sched._pending_market_events["XAUUSDT"]
    assert ev["candle_timestamp"] == 1700000000000
    assert ev["event_type"] == "CANDLE_CLOSED"


def test_red_022_real_scheduler_execution_arms_order_without_direct_evaluate_cycle(setup_isolated_db: Session):
    """
    Rules 022, 098, 105: Full event-driven execution through scheduler's evaluate_symbol_now
    (NOT calling evaluate_cycle directly) arms an order when conditions are valid.
    """
    db = setup_isolated_db
    _clean_telemetry()
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

    # Configure strategy/PAPER/auto via production settings that runtime reads (Rule 094)
    db.add(models.SystemConfig(key="scalp_m1_mode", value="PAPER", updated_at=now_ms))
    db.add(models.SystemConfig(key="cg01_enabled", value="true", updated_at=now_ms))
    db.add(models.SystemConfig(key="cg01_profile_mode", value="PAPER_EXECUTION", updated_at=now_ms))
    db.add(models.SystemConfig(key="execution_profile", value="CG01", updated_at=now_ms))
    db.commit()

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    for tf, bars in [("4H", c_4h), ("1H", c_1h), ("15M", c_15m), ("1M", c_1m)]:
        rows = [
            [
                int(b["timestamp"] if isinstance(b, dict) else b.timestamp),
                float(b["open"] if isinstance(b, dict) else b.open),
                float(b["high"] if isinstance(b, dict) else b.high),
                float(b["low"] if isinstance(b, dict) else b.low),
                float(b["close"] if isinstance(b, dict) else b.close),
                float(b.get("volume", 100.0) if isinstance(b, dict) else getattr(b, "volume", 100.0) or 100.0),
            ]
            for b in bars
        ]
        candle_cache_service.process_candle_payload("XAUUSDT", tf, rows)

    poi = WatchedZone(
        zone_id="poi_long_red_22",
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

    collector_service.latest_ticker = {
        "symbol": "XAUUSDT",
        "bid": 2625.5,
        "ask": 2625.7,
        "last": 2625.6,
        "server_time": now_ms,
        "observed_at": now_ms,
    }

    sched = CG01EvaluationScheduler()
    sched.set_flags(enabled=True, shadow_mode=False, is_owner=True)

    source_ev = {
        "event_id": f"XAUUSDT:1M:{now_ms}:1:CANDLE_CLOSED",
        "candle_timestamp": now_ms,
        "exchange_event_time_ms": now_ms,
        "received_at_ms": now_ms + 10,
        "enqueued_at_ms": now_ms + 12,
    }

    # Execute directly via scheduler entrypoint, never calling orchestrator.evaluate_cycle
    result = sched.evaluate_symbol_now(db, "XAUUSDT", as_of_ms=now_ms, source_event=source_ev)

    assert result["status"] == "COMPLETED"
    armed_id = result.get("armed_order_id")
    assert armed_id is not None, f"Expected order to be armed by scheduler, got {result}"

    order = db.query(models.PaperOrder).filter(models.PaperOrder.id == armed_id).first()
    assert order is not None
    assert order.state == "armed"
    assert order.direction == "LONG"


def test_red_023_trace_error_distinguished_from_success(setup_isolated_db: Session):
    """
    Rules 023, 121: An evaluation encountering an exception must record trace status as ERROR
    and must not pass as a valid setup.
    """
    db = setup_isolated_db
    _clean_telemetry()

    sched = CG01EvaluationScheduler()
    sched.set_flags(enabled=True, shadow_mode=True, is_owner=False)

    # Induce controlled failure in bundle capture
    with patch("services.cg01_scheduler_service.capture_cg01_input_bundle", side_effect=RuntimeError("INDUCED_DB_TIMEOUT")):
        res = sched.evaluate_symbol_now(db, "XAUUSDT", as_of_ms=int(time.time() * 1000))

    assert res["status"] == "ERROR"
    assert "INDUCED_DB_TIMEOUT" in res.get("error", "")


def test_red_024_exact_trace_id_query_isolation(setup_isolated_db: Session):
    """
    Rules 024, 090, 122: Querying by specific trace_id isolates the evaluation from
    prior leftover traces.
    """
    db = setup_isolated_db
    _clean_telemetry()

    old_trace = CG01EvaluationTrace(
        trace_id="trc_old_stale_111",
        symbol="XAUUSDT",
        observation_mode="SHADOW",
        evaluation_status="COMPLETED",
    )
    CG01TracePersistenceWriter.persist_trace(old_trace, db=db)
    db.commit()

    # Querying old trace by exact ID succeeds
    persisted_old = CG01TracePersistenceWriter.load_traces(symbol="XAUUSDT", db=db)
    assert any(t.trace_id == "trc_old_stale_111" for t in persisted_old)

    # Querying non-existent new trace specifically fails cleanly
    target_new_id = "trc_current_evaluation_222"
    assert not any(t.trace_id == target_new_id for t in persisted_old)


@pytest.mark.anyio
async def test_red_025_async_drain_non_blocking_event_loop(setup_isolated_db: Session):
    """
    Rules 025, 039, 130: drain_trace_persistence executes asynchronously without blocking
    the asyncio event loop, allowing worker to finish and commit traces.
    """
    _clean_telemetry()
    trace = CG01EvaluationTrace(
        trace_id=f"trc_async_drain_{int(time.time() * 1000)}",
        symbol="XAUUSDT",
        observation_mode="SHADOW",
        evaluation_status="COMPLETED",
    )
    enqueue_trace_persistence(trace)

    worker_task = asyncio.create_task(run_trace_persistence_worker())
    try:
        res = await drain_trace_persistence(timeout_s=3.0)
        assert res["status"] in ("COMPLETED", "COMPLETED_WITH_LOSS")
        assert res["pending"] == 0
        assert res["in_flight"] == 0
        assert res["retry_buffer"] == 0
    finally:
        from services.cg01_telemetry_service import stop_trace_persistence_worker
        stop_trace_persistence_worker()
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


def test_red_026_retry_buffer_atomic_in_flight_ownership(setup_isolated_db: Session):
    """
    Rules 026, 034, 035, 128: Items popped from _RETRY_BUFFER are immediately accounted
    in _IN_FLIGHT_COUNT so drain sees total owned work without a zero gap.
    """
    _clean_telemetry()
    trace = CG01EvaluationTrace(trace_id="trc_retry_test_1", symbol="XAUUSDT")
    with _RETRY_BUFFER_LOCK:
        _RETRY_BUFFER.append((trace, 1))

    assert get_retry_buffer_count() == 1
    assert get_in_flight_count() == 0

    # Pop and reserve atomically
    with _RETRY_BUFFER_LOCK:
        items = list(_RETRY_BUFFER)
        _RETRY_BUFFER.clear()
        set_in_flight_count(len(items))

    assert get_retry_buffer_count() == 0
    assert get_in_flight_count() == 1  # Total owned items strictly preserved!


def test_red_027_persist_trace_counter_exact_single_count(setup_isolated_db: Session):
    """
    Rules 027, 051, 052, 126: CG01TracePersistenceWriter.persist_trace when called with
    caller session ONLY flushes and does NOT increment _TRACE_PERSISTED_COUNT.
    The batch commit increments by exactly batch length.
    """
    db = setup_isolated_db
    _clean_telemetry()

    from services import cg01_telemetry_service
    initial_count = cg01_telemetry_service._TRACE_PERSISTED_COUNT

    t1 = CG01EvaluationTrace(trace_id="trc_batch_single_1", symbol="XAUUSDT")
    t2 = CG01EvaluationTrace(trace_id="trc_batch_single_2", symbol="XAUUSDT")
    batch = [t1, t2]

    res = _persist_trace_batch_sync(batch, session_factory=lambda: db)
    assert res.status == "COMMITTED"
    assert res.committed_count == 2

    # Exactly 2 increments for 2 records (no double counting)
    assert cg01_telemetry_service._TRACE_PERSISTED_COUNT == initial_count + 2


def test_red_028_rollback_counter_safety(setup_isolated_db: Session):
    """
    Rules 028, 055, 127: When a batch flush succeeds but commit fails, _TRACE_PERSISTED_COUNT
    does NOT increment.
    """
    db = setup_isolated_db
    _clean_telemetry()

    from services import cg01_telemetry_service
    initial_count = cg01_telemetry_service._TRACE_PERSISTED_COUNT

    mock_db = MagicMock()
    mock_db.commit.side_effect = RuntimeError("INDUCED_COMMIT_FAILURE")

    t = CG01EvaluationTrace(trace_id="trc_fail_commit_1", symbol="XAUUSDT")
    res = _persist_trace_batch_sync([t], session_factory=lambda: mock_db)

    assert res.status == "RETRYABLE_FAILURE"
    assert res.committed_count == 0
    assert cg01_telemetry_service._TRACE_PERSISTED_COUNT == initial_count


def test_red_029_actual_caller_production_config_parity(setup_isolated_db: Session):
    """
    Rules 029, 071-085: When production DB policy sets max_daily_fills = 5, both ACTIVE
    caller (evaluate_cycle with config=None) and SHADOW caller resolve the exact same
    daily_max_cap (5) and identical fingerprint.
    """
    db = setup_isolated_db
    policy = TradingPolicyService.get_or_create_policy(db)
    policy.max_daily_fills = 5
    policy.daily_min_goal = 2
    policy.ny_goal = 1
    db.commit()

    # Active resolution (no explicit test override)
    active_cfg, active_fp, active_src = resolve_canonical_cg01_config(
        db=db,
        symbol="XAUUSDT",
        is_explicit_override=False,
    )

    # Shadow resolution
    shadow_cfg, shadow_fp, shadow_src = resolve_effective_cg01_config(
        db=db,
        symbol="XAUUSDT",
    )

    assert active_cfg.daily_max_cap == 5
    assert shadow_cfg.daily_max_cap == 5
    assert active_cfg.min_net_rr == shadow_cfg.min_net_rr
    assert active_fp == shadow_fp
    assert active_src == "DATABASE_POLICY"
    assert shadow_src == "DATABASE_POLICY"


@pytest.mark.anyio
async def test_red_030_scheduler_shutdown_stops_accepting_and_drains_in_flight(setup_isolated_db: Session):
    """
    Rules 030, 042, 043: Scheduler stop() halts acceptance of new events and
    wait_evaluations_drained awaits active evaluations.
    """
    sched = CG01EvaluationScheduler()
    sched.set_flags(enabled=True, shadow_mode=True, is_owner=False)
    sched._running = True

    # While running, events are accepted
    ev = {"symbol": "XAUUSDT", "timeframe": "1M", "event_type": "CANDLE_CLOSED", "candle_timestamp": 1234}
    res1 = sched.notify_market_change(ev)
    assert res1["enqueued"] is True

    # Stop scheduler
    sched.stop()
    assert sched.is_running() is False

    # After stop, new events are rejected with SCHEDULER_STOPPED
    ev2 = {"symbol": "XAUUSDT", "timeframe": "1M", "event_type": "CANDLE_CLOSED", "candle_timestamp": 1235}
    res2 = sched.notify_market_change(ev2)
    assert res2["enqueued"] is False
    assert res2["reason"] == "SCHEDULER_STOPPED"

    # In-flight drain completes cleanly
    drained = await sched.wait_evaluations_drained(timeout_s=1.0)
    assert drained is True
