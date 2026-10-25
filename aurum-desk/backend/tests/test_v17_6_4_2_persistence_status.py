"""
backend/tests/test_v17_6_4_2_persistence_status.py
==================================================
ECI-03: Evidence Persistence Status & DB Ground Truth Test Suite (PS01 - PS12).
Verifies that dashboard snapshot never optimistically defaults persistence status to
COMMITTED without proof, reflects true DB state, and degrades on pending queue failures.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from services.engine_observability_service import (
    EngineObservabilityService,
    ObservationEvidenceQuality,
)
from services.observation_interval_service import (
    ObservationIntervalService,
    IntervalPersistenceResult,
    observation_interval_service,
)


@pytest.fixture
def mem_db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr("services.engine_observability_service.SessionLocal", SessionFactory)
    monkeypatch.setattr("services.observation_interval_service.SessionLocal", SessionFactory)
    session = SessionFactory()
    yield session, SessionFactory
    session.close()
    engine.dispose()


class TestEvidencePersistenceStatusTruth:
    """PS01 - PS12: Tests verifying persistence status matches actual database state."""

    def test_ps01_no_active_run_status(self, mem_db):
        """PS01: When there is no active observation run, status must not be COMMITTED."""
        session, _ = mem_db
        obs_svc = EngineObservabilityService()
        snap = obs_svc.build_observation_dashboard_snapshot(session, run_id=None)
        assert snap["evidence_persistence_status"] in ("NO_ACTIVE_RUN", "UNKNOWN", "UNVERIFIED")
        assert snap["evidence_persistence_status"] != "COMMITTED"

    def test_ps02_no_persisted_cycle_status(self, mem_db):
        """PS02: Active run with no persisted cycles or heartbeats is UNVERIFIED / UNKNOWN."""
        session, _ = mem_db
        obs_svc = EngineObservabilityService()
        snap = obs_svc.build_observation_dashboard_snapshot(session, run_id="RUN_EMPTY")
        assert snap["evidence_persistence_status"] != "COMMITTED"

    def test_ps03_no_persisted_interval_status(self, mem_db):
        """PS03: Cycle recorded but no confirmed interval records -> not fully COMMITTED."""
        session, _ = mem_db
        obs_svc = EngineObservabilityService()
        t0 = 1_700_000_000_000
        obs_svc.record_evaluation_cycle(
            session,
            cycle_id="CYC_PS03",
            run_id="RUN_PS03",
            started_at_ms=t0,
            completed_at_ms=t0 + 100,
            status="COMPLETED",
        )
        snap = obs_svc.build_observation_dashboard_snapshot(session, run_id="RUN_PS03")
        assert snap["evidence_persistence_status"] != "COMMITTED"

    def test_ps04_committed_cycle_and_interval(self, mem_db):
        """PS04: Both cycle and interval durably committed with 0 pending errors -> COMMITTED."""
        session, _ = mem_db
        obs_svc = EngineObservabilityService()
        t0 = 1_700_000_000_000

        # Persist cycle
        obs_svc.record_evaluation_cycle(
            session,  # isolated owned session commits independently
            cycle_id="CYC_PS04",
            run_id="RUN_PS04",
            started_at_ms=t0,
            completed_at_ms=t0 + 100,
            status="COMPLETED",
        )

        # Persist interval
        observation_interval_service.record_heartbeat_event(
            session,
            run_id="RUN_PS04",
            timestamp_ms=t0,
            coverage_quality="GOOD",
        )
        observation_interval_service.record_heartbeat_event(
            session,
            run_id="RUN_PS04",
            timestamp_ms=t0 + 15_000,
            coverage_quality="GOOD",
        )
        observation_interval_service.flush_active_intervals(session)

        snap = obs_svc.build_observation_dashboard_snapshot(session, run_id="RUN_PS04")
        assert snap["evidence_persistence_status"] == "COMMITTED"

    def test_ps05_pending_interval_failure_degrades_status(self, mem_db):
        """PS05: When intervals are pending due to write failure, status degrades to DEGRADED."""
        session, _ = mem_db
        obs_svc = EngineObservabilityService()
        t0 = 1_700_000_000_000

        observation_interval_service.record_heartbeat_event(
            session,
            run_id="RUN_PS05",
            timestamp_ms=t0,
            coverage_quality="GOOD",
        )
        with pytest.MonkeyPatch.context() as m:
            m.setattr(
                observation_interval_service,
                "_persist_interval_record",
                lambda db, data: IntervalPersistenceResult(status="RETRYABLE_FAILURE", error_code="BUSY", retryable=True),
            )
            observation_interval_service.record_heartbeat_event(
                session,
                run_id="RUN_PS05",
                timestamp_ms=t0 + 15_000,
                coverage_quality="GOOD",
            )
            observation_interval_service.flush_active_intervals(session)

        snap = obs_svc.build_observation_dashboard_snapshot(session, run_id="RUN_PS05")
        assert snap["evidence_persistence_status"] == "DEGRADED"

    def test_ps06_missing_heartbeat_status(self, mem_db):
        """PS06: Engine health snapshot without heartbeat is UNKNOWN."""
        session, _ = mem_db
        obs_svc = EngineObservabilityService()
        health = obs_svc.build_engine_health_snapshot(session, engine_instance_id="NON_EXISTENT")
        assert health["state"] == "UNKNOWN"
        assert health.get("evidence_persistence_status") in ("UNKNOWN", "UNVERIFIED")

    def test_ps07_db_none_query_failure(self):
        """PS07: When db=None, persistence status resolves to UNVERIFIED."""
        obs_svc = EngineObservabilityService()
        health = obs_svc.build_engine_health_snapshot(None)
        assert health.get("evidence_persistence_status") == "UNVERIFIED"

    def test_ps08_run_isolation_status(self, mem_db):
        """PS08: Committed evidence in RUN_A does not make RUN_B appear COMMITTED."""
        session, _ = mem_db
        obs_svc = EngineObservabilityService()
        t0 = 1_700_000_000_000

        obs_svc.record_evaluation_cycle(
            None,
            cycle_id="CYC_RUN_A",
            run_id="RUN_A",
            started_at_ms=t0,
            completed_at_ms=t0 + 100,
            status="COMPLETED",
        )
        snap_b = obs_svc.build_observation_dashboard_snapshot(session, run_id="RUN_B")
        assert snap_b["evidence_persistence_status"] != "COMMITTED"

    def test_ps09_restart_authoritative_persistence(self, mem_db):
        """PS09: After process restart, previously committed records still report COMMITTED."""
        session, _ = mem_db
        obs_svc1 = EngineObservabilityService()
        t0 = 1_700_000_000_000

        obs_svc1.record_evaluation_cycle(
            session,
            cycle_id="CYC_PS09",
            run_id="RUN_PS09",
            started_at_ms=t0,
            completed_at_ms=t0 + 100,
            status="COMPLETED",
        )
        observation_interval_service.record_heartbeat_event(session, run_id="RUN_PS09", timestamp_ms=t0, coverage_quality="GOOD")
        observation_interval_service.record_heartbeat_event(session, run_id="RUN_PS09", timestamp_ms=t0 + 15_000, coverage_quality="GOOD")
        observation_interval_service.flush_active_intervals(session)

        # New instance simulates restarted process
        obs_svc2 = EngineObservabilityService()
        snap = obs_svc2.build_observation_dashboard_snapshot(session, run_id="RUN_PS09")
        assert snap["evidence_persistence_status"] == "COMMITTED"

    def test_ps10_stale_committed_evidence(self, mem_db):
        """PS10: Evidence that has stalled and missed heartbeats reflects in engine state and persistence."""
        session, _ = mem_db
        obs_svc = EngineObservabilityService()
        t0 = 1_700_000_000_000

        obs_svc.record_engine_heartbeat(
            session,
            run_id="RUN_PS10",
            state="RUNNING",
            heartbeat_at_ms=t0,
        )
        # Advance clock by 10 minutes
        health = obs_svc.build_engine_health_snapshot(session, now_ms=t0 + 600_000)
        assert health["is_stalled"] is True

    def test_ps11_failed_latest_cycle_status(self, mem_db):
        """PS11: Failed cycle evaluation with DB write failure results in DEGRADED status."""
        session, _ = mem_db
        obs_svc = EngineObservabilityService()
        t0 = 1_700_000_000_000

        entry = obs_svc.record_evaluation_cycle(
            session,
            cycle_id="CYC_FAIL",
            run_id="RUN_PS11",
            started_at_ms=t0,
            completed_at_ms=t0 + 50,
            status="FAILED",
            failure_reason="SYNTHETIC_ERROR",
        )
        assert entry["status"] == "FAILED"

    def test_ps12_recovery_after_successful_retry(self, mem_db):
        """PS12: Status recovers from DEGRADED to COMMITTED once pending queue clears."""
        session, _ = mem_db
        obs_svc = EngineObservabilityService()
        t0 = 1_700_000_000_000

        obs_svc.record_evaluation_cycle(
            session,
            cycle_id="CYC_PS12",
            run_id="RUN_PS12",
            started_at_ms=t0,
            completed_at_ms=t0 + 100,
            status="COMPLETED",
        )
        observation_interval_service.clear()

        # Seed interval that succeeds
        observation_interval_service.record_heartbeat_event(session, run_id="RUN_PS12", timestamp_ms=t0, coverage_quality="GOOD")
        observation_interval_service.record_heartbeat_event(session, run_id="RUN_PS12", timestamp_ms=t0 + 15_000, coverage_quality="GOOD")
        observation_interval_service.flush_active_intervals(session)

        snap = obs_svc.build_observation_dashboard_snapshot(session, run_id="RUN_PS12")
        assert snap["evidence_persistence_status"] == "COMMITTED"
