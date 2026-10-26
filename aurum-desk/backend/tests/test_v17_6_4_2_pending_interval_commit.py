"""
backend/tests/test_v17_6_4_2_pending_interval_commit.py
======================================================
ECI-01: Pending Interval Commit Durability & Lifecycle Test Suite (PI01 - PI20).
Verifies that closed/checkpointed/rotated intervals are never dropped on database commit
failure, retain their immutable logical identity in a pending queue, retry safely upon DB
recovery, and avoid duplicate logical rows.
"""
from dataclasses import dataclass
import threading
import time
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from services.observation_interval_service import (
    ObservationIntervalService,
    IntervalFlushResult,
    IntervalPersistenceResult,
)


@pytest.fixture
def mem_db():
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


class TestPendingIntervalDurability:
    """PI01 - PI20: Test suite for ECI-01 pending interval lifecycle and durability."""

    def test_pi01_rotation_commit_succeeds(self, mem_db):
        """PI01: When DB is healthy, rotated interval commits and is evicted from pending."""
        session, SessionFactory = mem_db
        svc = ObservationIntervalService(checkpoint_interval_ms=10_000, max_interval_span_ms=20_000)

        t0 = 1_700_000_000_000
        svc.record_heartbeat_event(session, run_id="RUN_PI01", timestamp_ms=t0, coverage_quality="GOOD")
        # Trigger checkpoint rotation
        svc.record_heartbeat_event(session, run_id="RUN_PI01", timestamp_ms=t0 + 15_000, coverage_quality="GOOD")

        rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_PI01").all()
        assert len(rows) >= 1
        metrics = svc.get_pending_metrics()
        assert metrics["pending_intervals_count"] == 0

    def test_pi02_rotation_commit_fails_retains_pending(self, mem_db):
        """PI02: When DB commit fails during rotation, interval remains in pending queue."""
        session, SessionFactory = mem_db
        svc = ObservationIntervalService(checkpoint_interval_ms=10_000, max_interval_span_ms=20_000)

        t0 = 1_700_000_000_000
        svc.record_heartbeat_event(session, run_id="RUN_PI02", timestamp_ms=t0, coverage_quality="GOOD")

        # Mock DB failure during rotation
        with patch.object(svc, "_persist_interval_record", return_value=IntervalPersistenceResult(
            status="RETRYABLE_FAILURE", error_code="database is locked", retryable=True
        )):
            svc.record_heartbeat_event(session, run_id="RUN_PI02", timestamp_ms=t0 + 15_000, coverage_quality="GOOD")

        metrics = svc.get_pending_metrics()
        assert metrics["pending_intervals_count"] == 1
        assert metrics["last_persist_error"] is not None

    def test_pi03_checkpoint_commit_fails(self, mem_db):
        """PI03: Checkpoint interval failure preserves pending segment."""
        session, _ = mem_db
        svc = ObservationIntervalService(checkpoint_interval_ms=30_000)
        t0 = 1_700_000_000_000
        svc.record_heartbeat_event(session, run_id="RUN_PI03", timestamp_ms=t0, coverage_quality="GOOD")

        with patch.object(svc, "_persist_interval_record", return_value=IntervalPersistenceResult(
            status="RETRYABLE_FAILURE", error_code="sqlite busy", retryable=True
        )):
            svc.record_heartbeat_event(session, run_id="RUN_PI03", timestamp_ms=t0 + 35_000, coverage_quality="GOOD")

        metrics = svc.get_pending_metrics()
        assert metrics["pending_intervals_count"] >= 1

    def test_pi04_quality_transition_commit_fails(self, mem_db):
        """PI04: Quality transition from GOOD to DEGRADED retains previous interval when DB write fails."""
        session, _ = mem_db
        svc = ObservationIntervalService()
        t0 = 1_700_000_000_000
        svc.record_heartbeat_event(session, run_id="RUN_PI04", timestamp_ms=t0, coverage_quality="GOOD")
        svc.record_heartbeat_event(session, run_id="RUN_PI04", timestamp_ms=t0 + 10_000, coverage_quality="GOOD")

        with patch.object(svc, "_persist_interval_record", return_value=IntervalPersistenceResult(
            status="RETRYABLE_FAILURE", error_code="disk I/O error", retryable=True
        )):
            # Transition to DEGRADED
            svc.record_heartbeat_event(session, run_id="RUN_PI04", timestamp_ms=t0 + 20_000, coverage_quality="DEGRADED")

        metrics = svc.get_pending_metrics()
        assert metrics["pending_intervals_count"] >= 1

    def test_pi05_heartbeat_gap_commit_fails(self, mem_db):
        """PI05: Gap exceeding max_gap_ms retains previous interval on DB failure."""
        session, _ = mem_db
        svc = ObservationIntervalService(max_gap_ms=45_000)
        t0 = 1_700_000_000_000
        svc.record_heartbeat_event(session, run_id="RUN_PI05", timestamp_ms=t0, coverage_quality="GOOD")
        svc.record_heartbeat_event(session, run_id="RUN_PI05", timestamp_ms=t0 + 15_000, coverage_quality="GOOD")

        with patch.object(svc, "_persist_interval_record", return_value=IntervalPersistenceResult(
            status="RETRYABLE_FAILURE", error_code="locked", retryable=True
        )):
            # 60s gap after t0 + 15_000
            svc.record_heartbeat_event(session, run_id="RUN_PI05", timestamp_ms=t0 + 75_000, coverage_quality="GOOD")

        metrics = svc.get_pending_metrics()
        assert metrics["pending_intervals_count"] >= 1

    def test_pi06_pending_remains_after_failure(self, mem_db):
        """PI06: Subsequent heartbeats do not erase previous uncommitted pending interval."""
        session, _ = mem_db
        svc = ObservationIntervalService(checkpoint_interval_ms=10_000)
        t0 = 1_700_000_000_000
        svc.record_heartbeat_event(session, run_id="RUN_PI06", timestamp_ms=t0, coverage_quality="GOOD")

        with patch.object(svc, "_persist_interval_record", return_value=IntervalPersistenceResult(
            status="RETRYABLE_FAILURE", error_code="locked", retryable=True
        )):
            svc.record_heartbeat_event(session, run_id="RUN_PI06", timestamp_ms=t0 + 15_000, coverage_quality="GOOD")
            # Next heartbeat while DB is still locked
            svc.record_heartbeat_event(session, run_id="RUN_PI06", timestamp_ms=t0 + 20_000, coverage_quality="GOOD")

        metrics = svc.get_pending_metrics()
        assert metrics["pending_intervals_count"] >= 1

    def test_pi07_retry_pending_succeeds(self, mem_db):
        """PI07: Calling flush_active_intervals retries pending intervals and clears them upon DB success."""
        session, _ = mem_db
        svc = ObservationIntervalService(checkpoint_interval_ms=10_000)
        t0 = 1_700_000_000_000
        svc.record_heartbeat_event(session, run_id="RUN_PI07", timestamp_ms=t0, coverage_quality="GOOD")

        # Fail rotation
        with patch.object(svc, "_persist_interval_record", return_value=IntervalPersistenceResult(
            status="RETRYABLE_FAILURE", error_code="locked", retryable=True
        )):
            svc.record_heartbeat_event(session, run_id="RUN_PI07", timestamp_ms=t0 + 15_000, coverage_quality="GOOD")

        assert svc.get_pending_metrics()["pending_intervals_count"] == 1

        # Now DB recovers: flush_active_intervals succeeds
        flushed = svc.flush_active_intervals(session)
        assert flushed >= 1
        assert svc.get_pending_metrics()["pending_intervals_count"] == 0
        rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_PI07").all()
        assert len(rows) >= 1

    def test_pi08_retry_does_not_duplicate(self, mem_db):
        """PI08: Retrying persistence does not duplicate already-committed intervals."""
        session, _ = mem_db
        svc = ObservationIntervalService(checkpoint_interval_ms=10_000)
        t0 = 1_700_000_000_000
        svc.record_heartbeat_event(session, run_id="RUN_PI08", timestamp_ms=t0, coverage_quality="GOOD")
        svc.record_heartbeat_event(session, run_id="RUN_PI08", timestamp_ms=t0 + 15_000, coverage_quality="GOOD")

        # Flush again
        res = svc.flush_active_intervals(session)
        rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_PI08").all()
        # Verify no duplicate row created
        keys = [(r.start_ms, r.end_ms, r.data_quality) for r in rows]
        assert len(keys) == len(set(keys))

    def test_pi09_multiple_pending_intervals(self, mem_db):
        """PI09: Multiple consecutive rotation failures queue distinct pending segments."""
        session, _ = mem_db
        svc = ObservationIntervalService(checkpoint_interval_ms=10_000)
        t0 = 1_700_000_000_000
        svc.record_heartbeat_event(session, run_id="RUN_PI09", timestamp_ms=t0, coverage_quality="GOOD")

        with patch.object(svc, "_persist_interval_record", return_value=IntervalPersistenceResult(
            status="RETRYABLE_FAILURE", error_code="locked", retryable=True
        )):
            svc.record_heartbeat_event(session, run_id="RUN_PI09", timestamp_ms=t0 + 12_000, coverage_quality="GOOD")
            svc.record_heartbeat_event(session, run_id="RUN_PI09", timestamp_ms=t0 + 25_000, coverage_quality="GOOD")

        metrics = svc.get_pending_metrics()
        assert metrics["pending_intervals_count"] >= 2

    def test_pi10_new_heartbeat_during_pending_commit(self, mem_db):
        """PI10: Incoming heartbeats advance active interval without mutating sealed pending segment."""
        session, _ = mem_db
        svc = ObservationIntervalService(checkpoint_interval_ms=10_000)
        t0 = 1_700_000_000_000
        svc.record_heartbeat_event(session, run_id="RUN_PI10", timestamp_ms=t0, coverage_quality="GOOD")

        with patch.object(svc, "_persist_interval_record", return_value=IntervalPersistenceResult(
            status="RETRYABLE_FAILURE", error_code="locked", retryable=True
        )):
            svc.record_heartbeat_event(session, run_id="RUN_PI10", timestamp_ms=t0 + 15_000, coverage_quality="GOOD")

        # Snapshot pending
        pending = list(svc._pending_intervals.values())
        assert len(pending) == 1
        sealed_end = pending[0]["data"]["end_ms"]
        assert sealed_end == t0 + 15_000

        # Heartbeat arrives
        svc.record_heartbeat_event(session, run_id="RUN_PI10", timestamp_ms=t0 + 18_000, coverage_quality="GOOD")
        assert pending[0]["data"]["end_ms"] == sealed_end

    def test_pi11_concurrent_flush(self, mem_db):
        """PI11: Concurrent flush invocations do not race or cause inconsistency."""
        session, SessionFactory = mem_db
        svc = ObservationIntervalService(checkpoint_interval_ms=5_000)
        t0 = 1_700_000_000_000
        svc.record_heartbeat_event(session, run_id="RUN_PI11", timestamp_ms=t0, coverage_quality="GOOD")
        svc.record_heartbeat_event(session, run_id="RUN_PI11", timestamp_ms=t0 + 10_000, coverage_quality="GOOD")

        errors = []
        def worker():
            s = SessionFactory()
            try:
                svc.flush_active_intervals(s)
            except Exception as e:
                errors.append(e)
            finally:
                s.close()

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert len(errors) == 0

    def test_pi12_duplicate_worker_events(self, mem_db):
        """PI12: Duplicate events from same worker with same timestamp are safely deduplicated."""
        session, _ = mem_db
        svc = ObservationIntervalService()
        t0 = 1_700_000_000_000
        svc.record_heartbeat_event(session, run_id="RUN_PI12", timestamp_ms=t0, coverage_quality="GOOD")
        # Same timestamp duplicate
        res = svc.record_heartbeat_event(session, run_id="RUN_PI12", timestamp_ms=t0, coverage_quality="GOOD")
        assert res is None

    def test_pi13_different_run_ids(self, mem_db):
        """PI13: Run A and Run B maintain strictly separated pending queues."""
        session, _ = mem_db
        svc = ObservationIntervalService(checkpoint_interval_ms=10_000)
        t0 = 1_700_000_000_000
        svc.record_heartbeat_event(session, run_id="RUN_A", timestamp_ms=t0, coverage_quality="GOOD")
        svc.record_heartbeat_event(session, run_id="RUN_B", timestamp_ms=t0, coverage_quality="GOOD")

        with patch.object(svc, "_persist_interval_record", return_value=IntervalPersistenceResult(
            status="RETRYABLE_FAILURE", error_code="locked", retryable=True
        )):
            svc.record_heartbeat_event(session, run_id="RUN_A", timestamp_ms=t0 + 15_000, coverage_quality="GOOD")

        metrics = svc.get_pending_metrics()
        assert metrics["pending_intervals_count"] == 1
        pending_runs = [item["data"]["run_id"] for item in svc._pending_intervals.values()]
        assert "RUN_A" in pending_runs
        assert "RUN_B" not in pending_runs

    def test_pi14_different_engine_instances(self, mem_db):
        """PI14: Distinct engine instances maintain distinct pending keys."""
        session, _ = mem_db
        svc = ObservationIntervalService(checkpoint_interval_ms=10_000)
        t0 = 1_700_000_000_000
        svc.record_heartbeat_event(session, run_id="RUN_PI14", engine_instance_id="ENG_1", timestamp_ms=t0, coverage_quality="GOOD")
        svc.record_heartbeat_event(session, run_id="RUN_PI14", engine_instance_id="ENG_2", timestamp_ms=t0, coverage_quality="GOOD")

        with patch.object(svc, "_persist_interval_record", return_value=IntervalPersistenceResult(
            status="RETRYABLE_FAILURE", error_code="locked", retryable=True
        )):
            svc.record_heartbeat_event(session, run_id="RUN_PI14", engine_instance_id="ENG_1", timestamp_ms=t0 + 15_000, coverage_quality="GOOD")
            svc.record_heartbeat_event(session, run_id="RUN_PI14", engine_instance_id="ENG_2", timestamp_ms=t0 + 15_000, coverage_quality="GOOD")

        metrics = svc.get_pending_metrics()
        assert metrics["pending_intervals_count"] == 2

    def test_pi15_replay_excluded(self, mem_db):
        """PI15: REPLAY origin does not create pending intervals or live coverage."""
        session, _ = mem_db
        svc = ObservationIntervalService()
        t0 = 1_700_000_000_000
        res = svc.record_heartbeat_event(session, run_id="RUN_PI15", timestamp_ms=t0, evidence_origin="REPLAY", coverage_quality="GOOD")
        assert res is None
        assert svc.get_pending_metrics()["pending_intervals_count"] == 0

    def test_pi16_test_excluded(self, mem_db):
        """PI16: TEST origin does not create pending intervals or live coverage."""
        session, _ = mem_db
        svc = ObservationIntervalService()
        t0 = 1_700_000_000_000
        res = svc.record_heartbeat_event(session, run_id="RUN_PI16", timestamp_ms=t0, evidence_origin="TEST", coverage_quality="GOOD")
        assert res is None
        assert svc.get_pending_metrics()["pending_intervals_count"] == 0

    def test_pi17_permanent_db_failure_visible(self, mem_db):
        """PI17: Permanent DB failure is recorded in pending metrics with error code."""
        session, _ = mem_db
        svc = ObservationIntervalService(checkpoint_interval_ms=10_000)
        t0 = 1_700_000_000_000
        svc.record_heartbeat_event(session, run_id="RUN_PI17", timestamp_ms=t0, coverage_quality="GOOD")

        with patch.object(svc, "_persist_interval_record", return_value=IntervalPersistenceResult(
            status="PERMANENT_FAILURE", error_code="SCHEMA_MISMATCH", retryable=False
        )):
            svc.record_heartbeat_event(session, run_id="RUN_PI17", timestamp_ms=t0 + 15_000, coverage_quality="GOOD")

        metrics = svc.get_pending_metrics()
        assert metrics["pending_intervals_count"] == 1
        assert "SCHEMA_MISMATCH" in str(metrics["last_persist_error"])

    def test_pi18_shutdown_with_pending_intervals(self, mem_db):
        """PI18: Shutdown with uncommitted pending intervals exposes pending count via flush result."""
        session, _ = mem_db
        svc = ObservationIntervalService(checkpoint_interval_ms=10_000)
        t0 = 1_700_000_000_000
        svc.record_heartbeat_event(session, run_id="RUN_PI18", timestamp_ms=t0, coverage_quality="GOOD")

        with patch.object(svc, "_persist_interval_record", return_value=IntervalPersistenceResult(
            status="RETRYABLE_FAILURE", error_code="locked", retryable=True
        )):
            svc.record_heartbeat_event(session, run_id="RUN_PI18", timestamp_ms=t0 + 15_000, coverage_quality="GOOD")
            flush_res = svc.flush_active_intervals(session)

        assert flush_res.pending_count >= 1
        assert flush_res.committed == 0

    def test_pi19_restart_after_committed_checkpoint(self, mem_db):
        """PI19: After committed checkpoint, memory cleared and restarted reads persistent rows."""
        session, _ = mem_db
        svc1 = ObservationIntervalService(checkpoint_interval_ms=10_000)
        t0 = 1_700_000_000_000
        svc1.record_heartbeat_event(session, run_id="RUN_PI19", timestamp_ms=t0, coverage_quality="GOOD")
        svc1.record_heartbeat_event(session, run_id="RUN_PI19", timestamp_ms=t0 + 15_000, coverage_quality="GOOD")

        # Simulate process restart
        svc2 = ObservationIntervalService(checkpoint_interval_ms=10_000)
        rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_PI19").all()
        assert len(rows) >= 1
        assert svc2.get_pending_metrics()["pending_intervals_count"] == 0

    def test_pi20_crash_with_uncommitted_pending_remains_unverified(self, mem_db):
        """PI20: Crash before DB commit means restarted process does not see false COMMITTED evidence."""
        session, _ = mem_db
        svc1 = ObservationIntervalService(checkpoint_interval_ms=10_000)
        t0 = 1_700_000_000_000
        svc1.record_heartbeat_event(session, run_id="RUN_PI20", timestamp_ms=t0, coverage_quality="GOOD")

        with patch.object(svc1, "_persist_interval_record", return_value=IntervalPersistenceResult(
            status="RETRYABLE_FAILURE", error_code="locked", retryable=True
        )):
            svc1.record_heartbeat_event(session, run_id="RUN_PI20", timestamp_ms=t0 + 15_000, coverage_quality="GOOD")

        # Process crashes and restarts
        svc2 = ObservationIntervalService(checkpoint_interval_ms=10_000)
        rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_PI20").all()
        assert len(rows) == 0  # Not committed!
        assert svc2.get_pending_metrics()["pending_intervals_count"] == 0
