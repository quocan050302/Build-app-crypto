"""
backend/tests/test_v17_6_4_engine_owner_truth.py
Tests for Engine Heartbeat & Worker Ownership Truth (Phase H, EH01-EH10).
"""
import time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from services.engine_observability_service import engine_observability_service
from services.observation_interval_service import observation_interval_service


@pytest.fixture
def isolated_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False)
    session = TestingSession()
    observation_interval_service.clear()
    try:
        yield session
    finally:
        session.close()
        models.Base.metadata.drop_all(bind=engine)
        observation_interval_service.clear()


def test_eh01_watcher_owns_evaluation(isolated_db):
    """EH01: Heartbeat records WATCHER_PROCESS role when watcher is execution owner."""
    res = engine_observability_service.record_engine_heartbeat(
        isolated_db,
        symbol="XAUUSDT",
        run_id="RUN_EH01",
        state="RUNNING",
        metadata={
            "worker_role": "WATCHER_PROCESS",
            "execution_owner": "WATCHER",
            "can_execute": True,
        },
    )
    row = isolated_db.query(models.EngineHeartbeatRecord).first()
    assert row is not None
    assert row.state == "RUNNING"
    assert "WATCHER" in row.metadata_json


def test_eh02_scheduler_owns_evaluation(isolated_db):
    """EH02: Heartbeat reflects SCHEDULER owner when scheduler is active."""
    res = engine_observability_service.record_engine_heartbeat(
        isolated_db,
        symbol="XAUUSDT",
        run_id="RUN_EH02",
        state="RUNNING",
        metadata={
            "worker_role": "WATCHER_PROCESS",
            "execution_owner": "SCHEDULER",
            "can_execute": True,
        },
    )
    row = isolated_db.query(models.EngineHeartbeatRecord).first()
    assert row is not None
    assert "SCHEDULER" in row.metadata_json


def test_eh03_watcher_alive_scheduler_stalled(isolated_db):
    """EH03: Watcher process alive but evaluation cycle stalled is detected as STALLED."""
    now_ms = 1000000
    hb = models.EngineHeartbeatRecord(
        engine_instance_id="inst-eh03",
        symbol="XAUUSDT",
        run_id="RUN_EH03",
        state="RUNNING",
        last_process_heartbeat_at_ms=now_ms - 2000,   # Process alive 2s ago
        last_evaluation_completed_at_ms=now_ms - 120000, # Eval stalled 120s ago (cadence=15s)
        cadence_sec=15,
        updated_at_ms=now_ms,
    )
    is_stalled, reason = engine_observability_service.detect_stalled_engine(
        heartbeat_record=hb,
        now_ms=now_ms,
        feed_health={"freshness_state": "CONNECTED_FRESH"},
    )
    assert is_stalled is True
    assert "EVALUATION_CYCLE_STALLED" in reason


def test_eh04_weekend_live_contract_dead_worker(isolated_db):
    """EH04: On weekend with open 24/7 contract, dead worker is flagged as STALLED."""
    now_ms = 1792790400000  # Saturday 00:00 UTC
    hb = models.EngineHeartbeatRecord(
        engine_instance_id="inst-eh04",
        symbol="XAUUSDT",
        run_id="RUN_EH04",
        state="RUNNING",
        last_process_heartbeat_at_ms=now_ms - 200000,  # 200s stale
        cadence_sec=15,
        updated_at_ms=now_ms - 200000,
    )
    is_stalled, reason = engine_observability_service.detect_stalled_engine(
        heartbeat_record=hb,
        now_ms=now_ms,
    )
    assert is_stalled is True
    assert "HEARTBEAT_STALE" in reason


def test_eh05_feed_disconnected_worker_alive(isolated_db):
    """EH05: Feed disconnected does not cause false stall accusation for an alive worker."""
    now_ms = 1000000
    hb = models.EngineHeartbeatRecord(
        engine_instance_id="inst-eh05",
        symbol="XAUUSDT",
        run_id="RUN_EH05",
        state="RUNNING",
        last_process_heartbeat_at_ms=now_ms - 5000,
        cadence_sec=15,
        updated_at_ms=now_ms,
    )
    is_stalled, reason = engine_observability_service.detect_stalled_engine(
        heartbeat_record=hb,
        now_ms=now_ms,
        feed_health={"freshness_state": "DISCONNECTED"},
    )
    assert is_stalled is False
    assert reason == "WAITING_FEED_CONNECTION"


def test_eh06_no_heartbeat_record_stalled(isolated_db):
    """EH06: Engine with no heartbeat record reports stalled."""
    is_stalled, reason = engine_observability_service.detect_stalled_engine(
        heartbeat_record=None,
        now_ms=1000000,
    )
    assert is_stalled is True
    assert reason == "NO_HEARTBEAT_RECORD"


def test_eh07_replay_does_not_touch_live_heartbeat(isolated_db):
    """EH07: Replay execution does not contaminate live worker heartbeat table."""
    engine_observability_service.record_engine_heartbeat(
        isolated_db,
        engine_instance_id="live-worker-01",
        symbol="XAUUSDT",
        run_id="RUN_LIVE",
        heartbeat_at_ms=5000,
        state="RUNNING",
    )
    row_before = isolated_db.query(models.EngineHeartbeatRecord).filter_by(engine_instance_id="live-worker-01").first()
    assert row_before.last_process_heartbeat_at_ms == 5000

    # Simulate replay interval attempt with different instance
    observation_interval_service.record_heartbeat_event(
        isolated_db,
        run_id="RUN_REPLAY",
        symbol="XAUUSDT",
        timestamp_ms=1000,
        evidence_origin="REPLAY",
    )
    row_after = isolated_db.query(models.EngineHeartbeatRecord).filter_by(engine_instance_id="live-worker-01").first()
    assert row_after.last_process_heartbeat_at_ms == 5000


def test_eh08_worker_restart_upsert(isolated_db):
    """EH08: Restarted worker instance updates its record cleanly."""
    inst = "aurum-engine-restart-01"
    engine_observability_service.record_engine_heartbeat(
        isolated_db,
        engine_instance_id=inst,
        symbol="XAUUSDT",
        heartbeat_at_ms=1000,
        state="RUNNING",
    )
    engine_observability_service.record_engine_heartbeat(
        isolated_db,
        engine_instance_id=inst,
        symbol="XAUUSDT",
        heartbeat_at_ms=15000,
        state="RUNNING",
    )
    rows = isolated_db.query(models.EngineHeartbeatRecord).filter_by(engine_instance_id=inst).all()
    assert len(rows) == 1
    assert rows[0].last_process_heartbeat_at_ms == 15000


def test_eh09_stale_instance_fallback(isolated_db):
    """EH09: build_engine_health_snapshot queries symbol if instance not found."""
    hb = models.EngineHeartbeatRecord(
        engine_instance_id="other-instance-99",
        symbol="XAUUSDT",
        run_id="RUN_EH09",
        state="RUNNING",
        last_process_heartbeat_at_ms=1000000,
        cadence_sec=15,
        updated_at_ms=1000000,
    )
    isolated_db.add(hb)
    isolated_db.commit()

    snap = engine_observability_service.build_engine_health_snapshot(
        isolated_db, symbol="XAUUSDT", now_ms=1005000
    )
    assert snap["symbol"] == "XAUUSDT"
    assert snap["last_process_heartbeat_at_ms"] == 1000000


def test_eh10_process_heartbeat_newer_than_evaluation(isolated_db):
    """EH10: Process heartbeat updates independently from evaluation cycle completion."""
    inst = "engine-eh10"
    engine_observability_service.record_engine_heartbeat(
        isolated_db,
        engine_instance_id=inst,
        symbol="XAUUSDT",
        heartbeat_at_ms=20000,
        evaluation_completed_at_ms=10000,
        state="RUNNING",
    )
    row = isolated_db.query(models.EngineHeartbeatRecord).filter_by(engine_instance_id=inst).first()
    assert row.last_process_heartbeat_at_ms == 20000
    assert row.last_evaluation_completed_at_ms == 10000
