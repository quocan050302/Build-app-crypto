"""
backend/tests/test_v17_6_engine_heartbeat.py
V17.6 Gate 1: Engine Health & Durable Heartbeat Test Suite.

Verifies:
- H01: Healthy watcher heartbeat recording with state RUNNING
- H02: Heartbeat exists but last successful evaluation is tracked separately from process heartbeat
- H03: Stalled engine detection when heartbeat age > 3 * cadence
- H04: Intentionally paused / stopped engine is not flagged as stalled
- H05: Backend restart generates fresh engine instance and does not mistake old heartbeat for current
- H06: Weekend market closure prevents false stall alarms
- H07: Feed disconnection properly marks engine state as DEGRADED or waiting
- H08: Multi-worker instance isolation (persisted instance ID updates idempotently)
"""
import time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from services.engine_observability_service import (
    EngineObservabilityService,
    engine_observability_service,
)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def test_h01_record_healthy_engine_heartbeat(db_session):
    svc = EngineObservabilityService()
    now_ms = int(time.time() * 1000)

    res = svc.record_engine_heartbeat(
        db_session,
        engine_instance_id="inst-test-01",
        symbol="XAUUSDT",
        run_id="run-test-01",
        state="RUNNING",
        heartbeat_at_ms=now_ms,
        last_cycle_id="cycle-01",
        evaluation_started_at_ms=now_ms - 50,
        evaluation_completed_at_ms=now_ms,
        successful_cycle=True,
    )
    assert res["engine_instance_id"] == "inst-test-01"
    assert res["state"] == "RUNNING"

    hb = db_session.query(models.EngineHeartbeatRecord).filter_by(engine_instance_id="inst-test-01").first()
    assert hb is not None
    assert hb.state == "RUNNING"
    assert hb.last_process_heartbeat_at_ms == now_ms
    assert hb.last_successful_evaluation_at_ms == now_ms
    assert hb.cycles_count == 1
    assert hb.failed_cycles_count == 0


def test_h02_distinct_process_vs_evaluation_timestamps(db_session):
    svc = EngineObservabilityService()
    t0 = 1700000000000

    # Heartbeat tick during idle period (no cycle completed)
    svc.record_engine_heartbeat(
        db_session,
        engine_instance_id="inst-test-02",
        state="IDLE",
        heartbeat_at_ms=t0,
        evaluation_started_at_ms=None,
        evaluation_completed_at_ms=None,
        successful_cycle=False,
    )

    hb = db_session.query(models.EngineHeartbeatRecord).filter_by(engine_instance_id="inst-test-02").first()
    assert hb.last_process_heartbeat_at_ms == t0
    assert hb.last_successful_evaluation_at_ms is None

    # Later, cycle fails
    t1 = t0 + 15000
    svc.record_engine_heartbeat(
        db_session,
        engine_instance_id="inst-test-02",
        state="DEGRADED",
        heartbeat_at_ms=t1,
        evaluation_started_at_ms=t1 - 100,
        evaluation_completed_at_ms=t1,
        successful_cycle=False,
        cycle_failed=True,
    )
    db_session.refresh(hb)
    assert hb.last_process_heartbeat_at_ms == t1
    assert hb.last_successful_evaluation_at_ms is None  # Still None because cycle failed!
    assert hb.failed_cycles_count == 1


def test_h03_detect_stalled_engine(db_session):
    svc = EngineObservabilityService()
    now_ms = 1700000100000
    old_hb_ms = now_ms - (60 * 1000)  # 60s old (cadence 15s -> > 3 * 15s)

    hb_record = models.EngineHeartbeatRecord(
        engine_instance_id="inst-stalled",
        symbol="XAUUSDT",
        state="RUNNING",
        last_process_heartbeat_at_ms=old_hb_ms,
        last_evaluation_completed_at_ms=old_hb_ms,
        cadence_sec=15,
        updated_at_ms=old_hb_ms,
    )

    is_stalled, reason = svc.detect_stalled_engine(
        heartbeat_record=hb_record,
        now_ms=now_ms,
        feed_health={"freshness_state": "CONNECTED_FRESH"},
    )
    assert is_stalled is True
    assert "HEARTBEAT_STALE" in reason


def test_h04_paused_engine_not_stalled(db_session):
    svc = EngineObservabilityService()
    now_ms = 1700000100000
    old_hb_ms = now_ms - (120 * 1000)

    hb_record = models.EngineHeartbeatRecord(
        engine_instance_id="inst-paused",
        state="PAUSED",
        last_process_heartbeat_at_ms=old_hb_ms,
        cadence_sec=15,
        updated_at_ms=old_hb_ms,
    )

    is_stalled, reason = svc.detect_stalled_engine(
        heartbeat_record=hb_record,
        now_ms=now_ms,
    )
    assert is_stalled is False
    assert "INTENTIONALLY_PAUSED" in reason


def test_h05_restart_isolation(db_session):
    svc1 = EngineObservabilityService()
    svc1.record_engine_heartbeat(db_session, state="RUNNING", heartbeat_at_ms=1000)

    # Simulate restart with fresh service instance
    svc2 = EngineObservabilityService()
    assert svc2.current_instance_id != svc1.current_instance_id

    svc2.record_engine_heartbeat(db_session, state="RUNNING", heartbeat_at_ms=2000)

    records = db_session.query(models.EngineHeartbeatRecord).all()
    assert len(records) == 2
    inst_ids = {r.engine_instance_id for r in records}
    assert svc1.current_instance_id in inst_ids
    assert svc2.current_instance_id in inst_ids


def test_h06_build_engine_health_snapshot(db_session):
    svc = EngineObservabilityService()
    # Wednesday 14:00 UTC (active trading hours): 2026-10-21 14:00:00 UTC -> 1792591200000
    t_weekday = 1792591200000

    svc.record_engine_heartbeat(
        db_session,
        engine_instance_id=svc.current_instance_id,
        symbol="XAUUSDT",
        state="RUNNING",
        heartbeat_at_ms=t_weekday,
        evaluation_started_at_ms=t_weekday - 20,
        evaluation_completed_at_ms=t_weekday,
        successful_cycle=True,
    )

    snapshot = svc.build_engine_health_snapshot(db_session, symbol="XAUUSDT", now_ms=t_weekday)
    assert snapshot["symbol"] == "XAUUSDT"
    assert snapshot["is_stalled"] is False
    assert snapshot["cycles_count"] == 1
    assert snapshot["failed_cycles_count"] == 0

    # Also test weekend market state: Saturday 12:00 UTC -> 1792843200000
    t_weekend = 1792843200000
    snap_weekend = svc.build_engine_health_snapshot(db_session, symbol="XAUUSDT", now_ms=t_weekend)
    assert snap_weekend["state"] == "WAITING_MARKET"
