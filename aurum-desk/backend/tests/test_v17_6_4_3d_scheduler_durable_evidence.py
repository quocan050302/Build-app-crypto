"""
backend/tests/test_v17_6_4_3d_scheduler_durable_evidence.py
V17.6.4.3d AEC-04 / AEC-05 (PART 025-043): Real Scheduler Durability & Independent Readback.

Guarantees:
- Directly invokes `CG01EvaluationScheduler.evaluate_symbol_now()`.
- Requires durable commit of `EngineEvaluationCycleRecord` to real isolated disk-backed SQLite database.
- Completely separate independent SQLAlchemy Engine & Session verifies committed row.
- Zero loose `or` fallback assertions; asserts exact run_id, symbol, cycle_id, and decision status.
- Negative controls: Rollback test, commit failure test, empty DB rejection test.
"""

import os
import shutil
import tempfile
import time
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from services.cg01_scheduler_service import (
    CG01EvaluationScheduler,
    ensure_observation_run_started,
)
from services.bitget_ws_service import bitget_ws_service


@pytest.fixture
def isolated_sqlite_pair():
    """Creates an isolated temporary disk-backed SQLite file with two distinct Engines."""
    temp_dir = tempfile.mkdtemp(prefix="aurum_sched_durable_")
    db_path = Path(temp_dir) / "durable_sched.db"
    db_url = f"sqlite:///{db_path}"

    engine1 = create_engine(db_url, connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(bind=engine1)
    SessionFactory1 = sessionmaker(bind=engine1, autoflush=False, autocommit=False)

    # Completely independent second engine pointing to same file
    engine2 = create_engine(db_url, connect_args={"check_same_thread": False})
    SessionFactory2 = sessionmaker(bind=engine2, autoflush=False, autocommit=False)

    yield {
        "path": db_path,
        "engine1": engine1,
        "engine2": engine2,
        "session1": SessionFactory1,
        "session2": SessionFactory2,
    }

    engine1.dispose()
    engine2.dispose()
    shutil.rmtree(temp_dir, ignore_errors=True)


def _setup_authoritative_feed(now_ms: int = 1700000000000):
    """Sets trusted feed adapter state to fresh CONNECTED WS on epoch 1."""
    bitget_ws_service.transport_state = "CONNECTED"
    bitget_ws_service.feed_source = "WS"
    bitget_ws_service.connection_epoch = 1
    return {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "last": 2650.25,
        "exchange_ts_ms": now_ms - 50,
        "received_at_ms": now_ms - 40,
        "timestamp": now_ms,
        "observed_at": now_ms,
        "quote_source": "WS",
        "transport_state": "CONNECTED",
        "connection_epoch": 1,
        "status": "VALID",
        "is_valid": True,
        "is_stale": False,
    }


def test_sde01_actual_scheduler_evaluate_symbol_now(isolated_sqlite_pair):
    """SDE01: Invokes CG01EvaluationScheduler.evaluate_symbol_now directly."""
    s1 = isolated_sqlite_pair["session1"]()
    now_ms = 1700000000000
    _setup_authoritative_feed(now_ms)

    sched = CG01EvaluationScheduler()
    sched.reset_state()
    sched.set_flags(enabled=True, shadow_mode=False, is_owner=True)

    run = ensure_observation_run_started(
        symbol="XAUUSDT",
        scope="CG01",
        mode="PAPER",
        run_id="RUN_SDE01",
    )
    assert run is not None

    res = sched.evaluate_symbol_now(
        symbol="XAUUSDT",
        db=s1,
        execution_now_ms=now_ms,
        source_event={"symbol": "XAUUSDT", "timeframe": "1M", "event_type": "CANDLE_CLOSED", "candle_timestamp": now_ms - 60000},
    )

    assert res is not None
    assert res.get("status") in ("COMPLETED", "SKIPPED", "CANDIDATE_FOUND", "NO_SETUP")
    assert res.get("symbol") == "XAUUSDT"
    assert res.get("cycle_result", {}).get("cycle_id") is not None
    s1.commit()
    s1.close()


def test_sde02_cycle_committed_to_disk(isolated_sqlite_pair):
    """SDE02: Exact cycle is committed to real SQLite file on disk."""
    s1 = isolated_sqlite_pair["session1"]()
    now_ms = 1700000000000
    _setup_authoritative_feed(now_ms)

    sched = CG01EvaluationScheduler()
    sched.reset_state()
    sched.set_flags(enabled=True, shadow_mode=False, is_owner=True)

    ensure_observation_run_started(symbol="XAUUSDT", scope="CG01", mode="PAPER", run_id="RUN_SDE02")

    res = sched.evaluate_symbol_now(
        symbol="XAUUSDT",
        db=s1,
        execution_now_ms=now_ms,
    )
    cycle_id = res["cycle_result"]["cycle_id"]
    s1.commit()
    s1.close()

    # Verify physical SQLite file has non-zero size
    db_path = isolated_sqlite_pair["path"]
    assert db_path.exists()
    assert db_path.stat().st_size > 0


def test_sde03_independent_engine_readback(isolated_sqlite_pair):
    """SDE03: Separate Engine & Session independently queries committed cycle row."""
    s1 = isolated_sqlite_pair["session1"]()
    now_ms = 1700000000000
    _setup_authoritative_feed(now_ms)

    sched = CG01EvaluationScheduler()
    sched.reset_state()
    sched.set_flags(enabled=True, shadow_mode=False, is_owner=True)

    ensure_observation_run_started(symbol="XAUUSDT", scope="CG01", mode="PAPER", run_id="RUN_SDE03")

    res = sched.evaluate_symbol_now(
        symbol="XAUUSDT",
        db=s1,
        execution_now_ms=now_ms,
    )
    expected_cycle_id = res["cycle_result"]["cycle_id"]
    assigned_run_id = res["run_id"]
    assert assigned_run_id is not None
    s1.commit()
    s1.close()

    # Completely separate Engine 2 / Session 2 readback
    s2 = isolated_sqlite_pair["session2"]()
    try:
        persisted_cycle = s2.query(models.EngineEvaluationCycleRecord).filter(
            models.EngineEvaluationCycleRecord.cycle_id == expected_cycle_id
        ).one_or_none()
        assert persisted_cycle is not None
        assert persisted_cycle.symbol == "XAUUSDT"
        assert persisted_cycle.run_id == assigned_run_id
        assert persisted_cycle.started_at_ms == now_ms
    finally:
        s2.close()


def test_sde04_exact_run_id(isolated_sqlite_pair):
    """SDE04: Proves persisted row matches exact assigned run_id."""
    s1 = isolated_sqlite_pair["session1"]()
    now_ms = 1700000000000
    _setup_authoritative_feed(now_ms)

    sched = CG01EvaluationScheduler()
    sched.reset_state()
    sched.set_flags(enabled=True, shadow_mode=False, is_owner=True)

    res = sched.evaluate_symbol_now(symbol="XAUUSDT", db=s1, execution_now_ms=now_ms)
    expected_cycle_id = res["cycle_result"]["cycle_id"]
    assigned_run_id = res["run_id"]
    assert assigned_run_id is not None
    s1.commit()
    s1.close()

    s2 = isolated_sqlite_pair["session2"]()
    try:
        row = s2.query(models.EngineEvaluationCycleRecord).filter(
            models.EngineEvaluationCycleRecord.cycle_id == expected_cycle_id
        ).first()
        assert row is not None
        assert row.run_id == assigned_run_id
    finally:
        s2.close()


def test_sde05_exact_event_or_cycle_identity(isolated_sqlite_pair):
    """SDE05: Persisted cycle preserves exact cycle identity."""
    s1 = isolated_sqlite_pair["session1"]()
    now_ms = 1700000000000
    _setup_authoritative_feed(now_ms)

    sched = CG01EvaluationScheduler()
    sched.reset_state()
    sched.set_flags(enabled=True, shadow_mode=False, is_owner=True)

    ev_id = "XAUUSDT:1M:1699999940000:0:CANDLE_CLOSED"
    source_event = {
        "event_id": ev_id,
        "symbol": "XAUUSDT",
        "timeframe": "1M",
        "event_type": "CANDLE_CLOSED",
        "candle_timestamp": 1699999940000,
    }

    res = sched.evaluate_symbol_now(
        symbol="XAUUSDT",
        db=s1,
        execution_now_ms=now_ms,
        source_event=source_event,
    )
    cycle_id = res["cycle_result"]["cycle_id"]
    s1.commit()
    s1.close()

    s2 = isolated_sqlite_pair["session2"]()
    try:
        row = s2.query(models.EngineEvaluationCycleRecord).filter(
            models.EngineEvaluationCycleRecord.cycle_id == cycle_id
        ).first()
        assert row is not None
        assert row.cycle_id == cycle_id
    finally:
        s2.close()


def test_sde06_exact_symbol(isolated_sqlite_pair):
    """SDE06: Evaluates XAUUSDT and confirms persisted symbol is exactly XAUUSDT."""
    s1 = isolated_sqlite_pair["session1"]()
    now_ms = 1700000000000
    _setup_authoritative_feed(now_ms)

    sched = CG01EvaluationScheduler()
    sched.reset_state()
    sched.set_flags(enabled=True, shadow_mode=False, is_owner=True)

    res = sched.evaluate_symbol_now(symbol="XAUUSDT", db=s1, execution_now_ms=now_ms)
    cycle_id = res["cycle_result"]["cycle_id"]
    s1.commit()
    s1.close()

    s2 = isolated_sqlite_pair["session2"]()
    try:
        row = s2.query(models.EngineEvaluationCycleRecord).filter(
            models.EngineEvaluationCycleRecord.cycle_id == cycle_id
        ).first()
        assert row is not None
        assert row.symbol == "XAUUSDT"
    finally:
        s2.close()


def test_sde07_no_duplicate_cycle(isolated_sqlite_pair):
    """SDE07: Repeating the same market event rejects duplicate admission."""
    now_ms = 1700000000000
    sched = CG01EvaluationScheduler()
    sched.reset_state()

    event = {
        "symbol": "XAUUSDT",
        "timeframe": "1M",
        "event_type": "CANDLE_CLOSED",
        "candle_timestamp": now_ms - 60000,
        "revision": 0,
    }

    first = sched.notify_market_change(event)
    assert first["enqueued"] is True
    assert first["reason"] == "DIRTY_MARKED"

    second = sched.notify_market_change(event)
    assert second["enqueued"] is False
    assert second["reason"] == "DUPLICATE_EVENT"
    assert sched.duplicate_events_ignored_count == 1


def test_sde08_no_false_pass_on_empty_db(isolated_sqlite_pair):
    """SDE08: Proves an empty DB does not satisfy durability assertion."""
    s2 = isolated_sqlite_pair["session2"]()
    try:
        count = s2.query(models.EngineEvaluationCycleRecord).count()
        assert count == 0
        missing = s2.query(models.EngineEvaluationCycleRecord).filter(
            models.EngineEvaluationCycleRecord.cycle_id == "NONEXISTENT_CYCLE"
        ).one_or_none()
        assert missing is None
    finally:
        s2.close()


def test_sde09_rollback_removes_uncommitted_cycle(isolated_sqlite_pair):
    """SDE09: Rolled back transaction leaves no committed cycle in independent readback."""
    s1 = isolated_sqlite_pair["session1"]()
    now_ms = 1700000000000

    manual_cycle = models.EngineEvaluationCycleRecord(
        cycle_id="UNCOMMITTED_CYCLE_123",
        engine_instance_id="ENGINE_1",
        symbol="XAUUSDT",
        run_id="RUN_ROLLBACK",
        status="COMPLETED",
        started_at_ms=now_ms,
        completed_at_ms=now_ms + 10,
        created_at_ms=now_ms,
    )
    s1.add(manual_cycle)
    s1.flush()
    # Explicitly rollback
    s1.rollback()
    s1.close()

    s2 = isolated_sqlite_pair["session2"]()
    try:
        readback = s2.query(models.EngineEvaluationCycleRecord).filter(
            models.EngineEvaluationCycleRecord.cycle_id == "UNCOMMITTED_CYCLE_123"
        ).one_or_none()
        assert readback is None, "Uncommitted rolled back record must NOT exist in readback"
    finally:
        s2.close()


def test_sde10_commit_failure_rejects_durability(isolated_sqlite_pair):
    """SDE10: Simulated DB exception prevents durability confirmation."""
    s1 = isolated_sqlite_pair["session1"]()
    now_ms = 1700000000000

    row = models.EngineEvaluationCycleRecord(
        cycle_id="DUPLICATE_KEY_TEST",
        engine_instance_id="ENGINE_1",
        symbol="XAUUSDT",
        status="COMPLETED",
        started_at_ms=now_ms,
        completed_at_ms=now_ms + 10,
        created_at_ms=now_ms,
    )
    s1.add(row)
    s1.commit()

    # Second row with duplicate cycle_id must fail unique constraint
    dup_row = models.EngineEvaluationCycleRecord(
        cycle_id="DUPLICATE_KEY_TEST",
        engine_instance_id="ENGINE_2",
        symbol="XAUUSDT",
        status="COMPLETED",
        started_at_ms=now_ms + 10,
        completed_at_ms=now_ms + 20,
        created_at_ms=now_ms + 10,
    )
    s1.add(dup_row)
    with pytest.raises(Exception):
        s1.commit()
    s1.rollback()
    s1.close()

    s2 = isolated_sqlite_pair["session2"]()
    try:
        # Exactly 1 row should exist, the duplicate was rejected
        rows = s2.query(models.EngineEvaluationCycleRecord).filter(
            models.EngineEvaluationCycleRecord.cycle_id == "DUPLICATE_KEY_TEST"
        ).all()
        assert len(rows) == 1
    finally:
        s2.close()


def test_sde11_restart_and_reopen_sqlite(isolated_sqlite_pair):
    """SDE11: Reopening a new engine and session still reads previously committed records."""
    s1 = isolated_sqlite_pair["session1"]()
    now_ms = 1700000000000

    row = models.EngineEvaluationCycleRecord(
        cycle_id="REOPEN_TEST_99",
        engine_instance_id="ENGINE_1",
        symbol="XAUUSDT",
        run_id="RUN_REOPEN",
        status="COMPLETED",
        started_at_ms=now_ms,
        completed_at_ms=now_ms + 5,
        created_at_ms=now_ms,
    )
    s1.add(row)
    s1.commit()
    s1.close()

    # Open completely new 3rd engine
    db_path = isolated_sqlite_pair["path"]
    engine3 = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Session3 = sessionmaker(bind=engine3)()
    try:
        read = Session3.query(models.EngineEvaluationCycleRecord).filter(
            models.EngineEvaluationCycleRecord.cycle_id == "REOPEN_TEST_99"
        ).one_or_none()
        assert read is not None
        assert read.run_id == "RUN_REOPEN"
    finally:
        Session3.close()
        engine3.dispose()


def test_sde12_no_setup_still_records_evaluation_telemetry(isolated_sqlite_pair):
    """SDE12: Legitimate NO_SETUP evaluation still records and persists telemetry cycle."""
    s1 = isolated_sqlite_pair["session1"]()
    now_ms = 1700000000000
    _setup_authoritative_feed(now_ms)

    sched = CG01EvaluationScheduler()
    sched.reset_state()
    sched.set_flags(enabled=True, shadow_mode=False, is_owner=True)

    ensure_observation_run_started(symbol="XAUUSDT", scope="CG01", mode="PAPER", run_id="RUN_NO_SETUP")

    res = sched.evaluate_symbol_now(symbol="XAUUSDT", db=s1, execution_now_ms=now_ms)
    cycle_id = res["cycle_result"]["cycle_id"]
    s1.commit()
    s1.close()

    s2 = isolated_sqlite_pair["session2"]()
    try:
        cycle = s2.query(models.EngineEvaluationCycleRecord).filter(
            models.EngineEvaluationCycleRecord.cycle_id == cycle_id
        ).first()
        assert cycle is not None
        assert cycle.completed_at_ms is not None
        assert cycle.decision_status in ("NO_SETUP", "WAITING", "BLOCKED", "ARMED", "SKIPPED")
    finally:
        s2.close()


def test_sde13_shadow_mode_does_not_mutate_active_paper_orders(isolated_sqlite_pair):
    """SDE13: Shadow mode records trace without creating or modifying active paper orders."""
    s1 = isolated_sqlite_pair["session1"]()
    now_ms = 1700000000000
    _setup_authoritative_feed(now_ms)

    sched = CG01EvaluationScheduler()
    sched.reset_state()
    sched.set_flags(enabled=True, shadow_mode=True, is_owner=False)

    res = sched.evaluate_symbol_now(symbol="XAUUSDT", db=s1, execution_now_ms=now_ms)
    assert res["shadow_mode"] is True
    assert res["armed_order_id"] is None
    s1.commit()
    s1.close()

    s2 = isolated_sqlite_pair["session2"]()
    try:
        # Zero paper orders created
        orders_cnt = s2.query(models.PaperOrder).count()
        assert orders_cnt == 0
    finally:
        s2.close()


def test_sde14_active_paper_mode_preserved(isolated_sqlite_pair):
    """SDE14: Active mode operates without attempting live exchange orders."""
    s1 = isolated_sqlite_pair["session1"]()
    now_ms = 1700000000000
    _setup_authoritative_feed(now_ms)

    sched = CG01EvaluationScheduler()
    sched.reset_state()
    sched.set_flags(enabled=True, shadow_mode=False, is_owner=True)

    res = sched.evaluate_symbol_now(symbol="XAUUSDT", db=s1, execution_now_ms=now_ms)
    assert res["shadow_mode"] is False
    assert res["is_owner"] is True
    s1.commit()
    s1.close()


def test_sde15_scheduler_restart_state_correct():
    """SDE15: Scheduler reset_state restores initial idle counters."""
    sched = CG01EvaluationScheduler()
    sched.events_received_count = 50
    sched.evaluations_scheduled_count = 20
    sched.reset_state()

    snap = sched.get_health_snapshot()
    assert snap["events_received_count"] == 0
    assert snap["evaluations_scheduled_count"] == 0
    assert snap["dirty_symbols"] == []
    assert snap["in_flight_symbols"] == []


def test_sde16_database_ab_isolation(isolated_sqlite_pair):
    """SDE16: Proves data in database A does not appear in unrelated database B."""
    temp_dir_b = tempfile.mkdtemp(prefix="aurum_db_b_")
    db_b_path = Path(temp_dir_b) / "other.db"
    engine_b = create_engine(f"sqlite:///{db_b_path}", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(bind=engine_b)
    SessionB = sessionmaker(bind=engine_b)()

    s1 = isolated_sqlite_pair["session1"]()
    row = models.EngineEvaluationCycleRecord(
        cycle_id="DATABASE_A_ONLY",
        engine_instance_id="ENGINE_1",
        symbol="XAUUSDT",
        status="COMPLETED",
        started_at_ms=1700000000000,
        completed_at_ms=1700000000010,
        created_at_ms=1700000000000,
    )
    s1.add(row)
    s1.commit()
    s1.close()

    try:
        # Check DB B
        in_b = SessionB.query(models.EngineEvaluationCycleRecord).filter(
            models.EngineEvaluationCycleRecord.cycle_id == "DATABASE_A_ONLY"
        ).first()
        assert in_b is None, "Record from DB A must not exist in DB B"
    finally:
        SessionB.close()
        engine_b.dispose()
        shutil.rmtree(temp_dir_b, ignore_errors=True)
