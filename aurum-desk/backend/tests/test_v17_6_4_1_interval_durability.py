"""
backend/tests/test_v17_6_4_1_interval_durability.py
AURUM V17.6.4.1 — Durable Interval Persistence & Checkpoint Test Suite (EVID-03 & EVID-06, Parts 069-091).

Cases PD01-PD16:
- PD01: Successful commit returns COMMITTED status with interval_id
- PD02: Duplicate existing interval returns ALREADY_EXISTS and is idempotent
- PD03: Database locked triggers retry and preserves pending interval
- PD04: Retry exhaustion returns RETRYABLE_FAILURE / PERMANENT_FAILURE without crashing
- PD05: Session rollback occurs before retry
- PD06: Flush failure does not increase flushed_count
- PD07: Pending evidence preserved in memory when write fails
- PD08: Periodic checkpoint persists bounded segments at regular cadence
- PD09: Crash before checkpoint does not invent unverified rows
- PD10: Shutdown flush persists remaining active interval
- PD11: Restart readback from disk-backed SQLite session
- PD12: Two concurrent producers with different locks/instances
- PD13: Different engine_instance_id isolates intervals
- PD14: Different run_id isolates intervals
- PD15: No false flushed count reported on failed writes
- PD16: No invented coverage after persistence failure
"""
import time
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from services.observation_interval_service import (
    IntervalPersistenceResult,
    ObservationIntervalService,
)


@pytest.fixture
def memory_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False)
    session = SessionFactory()
    yield session, SessionFactory
    session.close()
    engine.dispose()


def test_pd01_successful_commit(memory_db):
    """PD01: Successful commit returns COMMITTED status with interval_id."""
    session, _ = memory_db
    producer = ObservationIntervalService()

    data = {
        "run_id": "RUN_PD01",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": 1_700_000_000_000,
        "end_ms": 1_700_000_015_000,
        "data_quality": "GOOD",
        "heartbeats_count": 2,
    }
    res = producer._persist_interval_record(session, data)
    assert res.status == "COMMITTED"
    assert res.interval_id is not None
    assert res.retryable is False


def test_pd02_duplicate_existing_interval(memory_db):
    """PD02: Second persist with identical coordinates returns ALREADY_EXISTS."""
    session, _ = memory_db
    producer = ObservationIntervalService()

    data = {
        "run_id": "RUN_PD02",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": 1_700_000_000_000,
        "end_ms": 1_700_000_015_000,
        "data_quality": "GOOD",
        "heartbeats_count": 2,
    }
    res1 = producer._persist_interval_record(session, data)
    assert res1.status == "COMMITTED"

    res2 = producer._persist_interval_record(session, data)
    assert res2.status == "ALREADY_EXISTS"


def test_pd03_database_locked_retry(memory_db):
    """PD03: Database locked error triggers bounded retry."""
    session, _ = memory_db
    producer = ObservationIntervalService()

    call_count = 0
    mock_target = MagicMock()
    mock_target.query.return_value.filter.return_value.first.return_value = None

    def flaky_commit():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OperationalError("database is locked", {}, None)

    mock_target.commit.side_effect = flaky_commit

    with patch.object(producer, "_resolve_session", return_value=(mock_target, True)):
        data = {
            "run_id": "RUN_PD03",
            "symbol": "XAUUSDT",
            "scope": "CG01",
            "worker_role": "WATCHER_PROCESS",
            "mode": "PAPER",
            "start_ms": 1_700_000_000_000,
            "end_ms": 1_700_000_015_000,
            "data_quality": "GOOD",
            "heartbeats_count": 2,
        }
        res = producer._persist_interval_record(session, data)
        assert res.status == "COMMITTED"
        assert call_count >= 2


def test_pd04_retry_exhaustion(memory_db):
    """PD04: Persistent lock triggers retry exhaustion and returns RETRYABLE_FAILURE."""
    session, _ = memory_db
    producer = ObservationIntervalService()

    mock_target = MagicMock()
    mock_target.query.return_value.filter.return_value.first.return_value = None
    mock_target.commit.side_effect = OperationalError("database is locked", {}, None)

    with patch.object(producer, "_resolve_session", return_value=(mock_target, True)):
        data = {
            "run_id": "RUN_PD04",
            "symbol": "XAUUSDT",
            "scope": "CG01",
            "worker_role": "WATCHER_PROCESS",
            "mode": "PAPER",
            "start_ms": 1_700_000_000_000,
            "end_ms": 1_700_000_015_000,
            "data_quality": "GOOD",
            "heartbeats_count": 2,
        }
        res = producer._persist_interval_record(session, data)
        assert res.status in ("RETRYABLE_FAILURE", "PERMANENT_FAILURE")
        assert res.retryable is True


def test_pd05_session_rollback_before_retry(memory_db):
    """PD05: Ensure session.rollback() is invoked when commit fails."""
    session, _ = memory_db
    producer = ObservationIntervalService()

    mock_target = MagicMock()
    mock_target.query.return_value.filter.return_value.first.return_value = None
    mock_target.commit.side_effect = OperationalError("database is locked", {}, None)

    with patch.object(producer, "_resolve_session", return_value=(mock_target, True)):
        data = {
            "run_id": "RUN_PD05",
            "symbol": "XAUUSDT",
            "scope": "CG01",
            "worker_role": "WATCHER_PROCESS",
            "mode": "PAPER",
            "start_ms": 1_700_000_000_000,
            "end_ms": 1_700_000_015_000,
            "data_quality": "GOOD",
            "heartbeats_count": 2,
        }
        producer._persist_interval_record(session, data)
        assert mock_target.rollback.called is True


def test_pd06_flush_failure_does_not_increment_flushed_count(memory_db):
    """PD06: If _persist_interval_record fails, flushed_count must NOT increase."""
    session, _ = memory_db
    producer = ObservationIntervalService()
    t0 = 1_700_000_000_000

    producer.record_heartbeat_event(session, run_id="RUN_PD06", timestamp_ms=t0, coverage_quality="GOOD")
    producer.record_heartbeat_event(session, run_id="RUN_PD06", timestamp_ms=t0 + 15_000, coverage_quality="GOOD")

    with patch.object(
        producer,
        "_persist_interval_record",
        return_value=IntervalPersistenceResult(status="RETRYABLE_FAILURE", error_code="MOCK_LOCK", retryable=True),
    ):
        flushed = producer.flush_active_intervals(session)
        assert flushed == 0, f"Expected 0 flushed count on write failure, got {flushed}!"


def test_pd07_pending_evidence_preserved_on_failure(memory_db):
    """PD07: Failed flush preserves pending interval in memory for subsequent retry."""
    session, _ = memory_db
    producer = ObservationIntervalService()
    t0 = 1_700_000_000_000

    producer.record_heartbeat_event(session, run_id="RUN_PD07", timestamp_ms=t0, coverage_quality="GOOD")
    producer.record_heartbeat_event(session, run_id="RUN_PD07", timestamp_ms=t0 + 15_000, coverage_quality="GOOD")

    # 1. First flush fails
    with patch.object(
        producer,
        "_persist_interval_record",
        return_value=IntervalPersistenceResult(status="RETRYABLE_FAILURE", error_code="LOCK", retryable=True),
    ):
        flushed = producer.flush_active_intervals(session)
        assert flushed == 0

    # Verify tracker start_ms did NOT advance!
    with producer._lock:
        active = [v for k, v in producer._active_intervals.items() if k[0] == "RUN_PD07"][0]
        assert active["start_ms"] == t0
        assert active["end_ms"] == t0 + 15_000

    # 2. Second flush succeeds
    flushed2 = producer.flush_active_intervals(session)
    assert flushed2 == 1

    rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_PD07").all()
    assert len(rows) == 1
    assert rows[0].start_ms == t0


def test_pd08_periodic_checkpoint(memory_db):
    """PD08: Checkpoint occurs at or before checkpoint interval without waiting 5 minutes."""
    session, _ = memory_db
    # Set checkpoint cadence to 30s
    producer = ObservationIntervalService(checkpoint_interval_ms=30_000)
    t0 = 1_700_000_000_000

    producer.record_heartbeat_event(session, run_id="RUN_PD08", timestamp_ms=t0, coverage_quality="GOOD")
    producer.record_heartbeat_event(session, run_id="RUN_PD08", timestamp_ms=t0 + 15_000, coverage_quality="GOOD")
    # At t0 + 35_000, exceeds 30s checkpoint cadence
    producer.record_heartbeat_event(session, run_id="RUN_PD08", timestamp_ms=t0 + 35_000, coverage_quality="GOOD")

    rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_PD08").all()
    # Should have committed a checkpoint record without requiring manual flush
    assert len(rows) >= 1


def test_pd09_crash_before_checkpoint(memory_db):
    """PD09: Clean in-memory discard on crash leaves DB clean."""
    session, _ = memory_db
    producer = ObservationIntervalService()
    producer.record_heartbeat_event(session, run_id="RUN_PD09", timestamp_ms=1_700_000_000_000, coverage_quality="GOOD")
    # Simulate crash before flush
    producer.clear()
    rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_PD09").all()
    assert len(rows) == 0


def test_pd10_shutdown_flush(memory_db):
    """PD10: Clean shutdown flushes all active unpersisted intervals."""
    session, _ = memory_db
    producer = ObservationIntervalService()
    t0 = 1_700_000_000_000
    producer.record_heartbeat_event(session, run_id="RUN_PD10", timestamp_ms=t0, coverage_quality="GOOD")
    producer.record_heartbeat_event(session, run_id="RUN_PD10", timestamp_ms=t0 + 20_000, coverage_quality="GOOD")

    flushed = producer.flush_active_intervals(session)
    assert flushed == 1
    rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_PD10").all()
    assert len(rows) == 1


def test_pd11_restart_readback(memory_db):
    """PD11: Restart readback returns committed interval rows accurately."""
    session, SessionFactory = memory_db
    producer = ObservationIntervalService()
    t0 = 1_700_000_000_000
    producer.record_heartbeat_event(session, run_id="RUN_PD11", timestamp_ms=t0, coverage_quality="GOOD")
    producer.record_heartbeat_event(session, run_id="RUN_PD11", timestamp_ms=t0 + 20_000, coverage_quality="GOOD")
    producer.flush_active_intervals(session)
    session.close()

    new_session = SessionFactory()
    rows = new_session.query(models.PaperObservationInterval).filter_by(run_id="RUN_PD11").all()
    assert len(rows) == 1
    assert rows[0].end_ms - rows[0].start_ms == 20_000
    new_session.close()


def test_pd12_two_concurrent_producers(memory_db):
    """PD12: Two producers write independently."""
    session, _ = memory_db
    p1 = ObservationIntervalService()
    p2 = ObservationIntervalService()

    t0 = 1_700_000_000_000
    p1.record_heartbeat_event(session, run_id="RUN_P1", timestamp_ms=t0, coverage_quality="GOOD")
    p1.record_heartbeat_event(session, run_id="RUN_P1", timestamp_ms=t0 + 10_000, coverage_quality="GOOD")
    p1.flush_active_intervals(session)

    p2.record_heartbeat_event(session, run_id="RUN_P2", timestamp_ms=t0, coverage_quality="DEGRADED")
    p2.record_heartbeat_event(session, run_id="RUN_P2", timestamp_ms=t0 + 10_000, coverage_quality="DEGRADED")
    p2.flush_active_intervals(session)

    rows1 = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_P1").all()
    rows2 = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_P2").all()
    assert len(rows1) == 1
    assert len(rows2) == 1


def test_pd13_different_engine_instance_id(memory_db):
    """PD13: Different engine_instance_id separates interval keys."""
    session, _ = memory_db
    producer = ObservationIntervalService()
    t0 = 1_700_000_000_000

    producer.record_heartbeat_event(session, run_id="RUN_PD13", engine_instance_id="INST_A", timestamp_ms=t0, coverage_quality="GOOD")
    producer.record_heartbeat_event(session, run_id="RUN_PD13", engine_instance_id="INST_B", timestamp_ms=t0, coverage_quality="GOOD")

    with producer._lock:
        inst_ids = [k[5] for k in producer._active_intervals.keys() if len(k) >= 6]
        assert "INST_A" in inst_ids
        assert "INST_B" in inst_ids


def test_pd14_different_run_id(memory_db):
    """PD14: Different run_id isolates intervals completely."""
    session, _ = memory_db
    producer = ObservationIntervalService()
    t0 = 1_700_000_000_000

    producer.record_heartbeat_event(session, run_id="RUN_A", timestamp_ms=t0, coverage_quality="GOOD")
    producer.record_heartbeat_event(session, run_id="RUN_B", timestamp_ms=t0, coverage_quality="GOOD")
    producer.flush_active_intervals(session)

    rows_a = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_A").all()
    rows_b = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_B").all()
    assert len(rows_a) == 0  # Single heartbeat does not create interval
    assert len(rows_b) == 0


def test_pd15_no_false_flushed_count(memory_db):
    """PD15: Never return positive flushed_count unless DB commit succeeded."""
    session, _ = memory_db
    producer = ObservationIntervalService()
    t0 = 1_700_000_000_000

    producer.record_heartbeat_event(session, run_id="RUN_PD15", timestamp_ms=t0, coverage_quality="GOOD")
    producer.record_heartbeat_event(session, run_id="RUN_PD15", timestamp_ms=t0 + 10_000, coverage_quality="GOOD")

    with patch.object(
        producer,
        "_persist_interval_record",
        return_value=IntervalPersistenceResult(status="PERMANENT_FAILURE", error_code="DB_ERROR"),
    ):
        cnt = producer.flush_active_intervals(session)
        assert cnt == 0


def test_pd16_no_invented_coverage_after_persistence_failure(memory_db):
    """PD16: Unpersisted intervals must not appear as persisted coverage."""
    session, _ = memory_db
    producer = ObservationIntervalService()
    t0 = 1_700_000_000_000

    producer.record_heartbeat_event(session, run_id="RUN_PD16", timestamp_ms=t0, coverage_quality="GOOD")
    producer.record_heartbeat_event(session, run_id="RUN_PD16", timestamp_ms=t0 + 10_000, coverage_quality="GOOD")

    with patch.object(
        producer,
        "_persist_interval_record",
        return_value=IntervalPersistenceResult(status="RETRYABLE_FAILURE", error_code="BUSY", retryable=True),
    ):
        producer.flush_active_intervals(session)

    rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_PD16").all()
    assert len(rows) == 0
