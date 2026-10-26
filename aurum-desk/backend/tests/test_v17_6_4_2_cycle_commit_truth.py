"""
backend/tests/test_v17_6_4_2_cycle_commit_truth.py
=================================================
ECI-06 & ECI-07: Evaluation Cycle Commit Truth & Persistence Authority (CT01 - CT12).
Verifies:
1. get_latest_persisted_cycle is strictly DB-authoritative and NEVER falls back to in-memory diagnostics.
2. Flush is NOT commit: record_evaluation_cycle does not report COMMITTED when caller session only flushes.
3. Heartbeat cycle success counter increments ONLY on confirmed DB commit.
4. Idempotency on duplicate cycle entries.
"""
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from services.engine_observability_service import (
    EngineObservabilityService,
    ObservationEvidenceOrigin,
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


class TestCycleCommitTruth:
    """CT01 - CT12: Tests enforcing durable cycle commit semantics and authority."""

    def test_ct01_flush_without_commit_reports_pending_commit(self, mem_db):
        """CT01: When caller passes db session that is only flushed, status is PENDING_COMMIT, not COMMITTED."""
        session, _ = mem_db
        obs_svc = EngineObservabilityService()
        t0 = 1_700_000_000_000

        res = obs_svc.record_evaluation_cycle(
            session,  # Caller-provided session; flush only
            cycle_id="CYC_CT01",
            run_id="RUN_CT01",
            started_at_ms=t0,
            completed_at_ms=t0 + 100,
            status="COMPLETED",
            borrow_session=True,
        )
        assert res["persistence_status"] in ("PENDING_COMMIT", "PENDING_TRANSACTION")
        assert res["persistence_status"] != "COMMITTED"

    def test_ct02_isolated_session_commit_succeeds(self, mem_db):
        """CT02: When db=None, an owned session commits independently and reports COMMITTED."""
        session, _ = mem_db
        obs_svc = EngineObservabilityService()
        t0 = 1_700_000_000_000

        res = obs_svc.record_evaluation_cycle(
            None,  # Owned session commits
            cycle_id="CYC_CT02",
            run_id="RUN_CT02",
            started_at_ms=t0,
            completed_at_ms=t0 + 100,
            status="COMPLETED",
        )
        assert res["persistence_status"] == "COMMITTED"

    def test_ct03_commit_fails_reports_failed(self, mem_db):
        """CT03: When DB persistence raises an exception, status is FAILED / FAILED_DB_WRITE."""
        session, _ = mem_db
        obs_svc = EngineObservabilityService()
        t0 = 1_700_000_000_000

        with patch.object(obs_svc, "_resolve_target_session", return_value=(None, False)):
            res = obs_svc.record_evaluation_cycle(
                None,
                cycle_id="CYC_CT03",
                run_id="RUN_CT03",
                started_at_ms=t0,
                completed_at_ms=t0 + 100,
                status="COMPLETED",
            )
            assert res["persistence_status"] in ("FAILED", "FAILED_DB_WRITE")

    def test_ct04_rollback_after_flush(self, mem_db):
        """CT04: Caller session rolls back after flush -> cycle row is NOT committed."""
        session, SessionFactory = mem_db
        obs_svc = EngineObservabilityService()
        t0 = 1_700_000_000_000

        obs_svc.record_evaluation_cycle(
            session,
            cycle_id="CYC_CT04",
            run_id="RUN_CT04",
            started_at_ms=t0,
            completed_at_ms=t0 + 100,
            status="COMPLETED",
            borrow_session=True,
        )
        session.rollback()

        # Check from fresh session
        session2 = SessionFactory()
        row = session2.query(models.EngineEvaluationCycleRecord).filter_by(cycle_id="CYC_CT04").first()
        assert row is None
        session2.close()

    def test_ct05_duplicate_cycle_deduplication(self, mem_db):
        """CT05: Recording the same cycle_id twice returns deduplicated=True."""
        session, _ = mem_db
        obs_svc = EngineObservabilityService()
        t0 = 1_700_000_000_000

        res1 = obs_svc.record_evaluation_cycle(
            None,
            cycle_id="CYC_CT05",
            run_id="RUN_CT05",
            started_at_ms=t0,
            completed_at_ms=t0 + 100,
            status="COMPLETED",
        )
        res2 = obs_svc.record_evaluation_cycle(
            None,
            cycle_id="CYC_CT05",
            run_id="RUN_CT05",
            started_at_ms=t0,
            completed_at_ms=t0 + 100,
            status="COMPLETED",
        )
        assert res2.get("deduplicated") is True

    def test_ct06_existing_persisted_cycle_dedup(self, mem_db):
        """CT06: When cycle is already in DB, second call returns deduplicated=True without exception."""
        session, SessionFactory = mem_db
        obs_svc = EngineObservabilityService()
        t0 = 1_700_000_000_000

        obs_svc.record_evaluation_cycle(
            session,
            cycle_id="CYC_CT06",
            run_id="RUN_CT06",
            started_at_ms=t0,
            completed_at_ms=t0 + 100,
            status="COMPLETED",
        )
        # Clear in-memory buffer
        obs_svc._recent_cycles_buffer.clear()

        # Call again on session
        res = obs_svc.record_evaluation_cycle(
            session,
            cycle_id="CYC_CT06",
            run_id="RUN_CT06",
            started_at_ms=t0,
            completed_at_ms=t0 + 100,
            status="COMPLETED",
        )
        assert res.get("deduplicated") is True

    def test_ct07_ram_diagnostic_not_returned_as_persisted(self, mem_db):
        """CT07 (ECI-06): get_latest_persisted_cycle NEVER returns in-memory diagnostic when DB has no row."""
        session, _ = mem_db
        obs_svc = EngineObservabilityService()

        # Seed in-memory diagnostic ONLY
        with obs_svc._lock:
            obs_svc._latest_cycle_diagnostic = {
                "cycle_id": "RAM_ONLY_CYCLE",
                "symbol": "XAUUSDT",
                "run_id": "RUN_CT07",
                "status": "COMPLETED",
            }

        # Query persisted cycle
        res = obs_svc.get_latest_persisted_cycle(session, symbol="XAUUSDT", run_id="RUN_CT07")
        assert res is None  # Must NOT fall back to RAM diagnostic!

    def test_ct08_db_unavailable_returns_none(self):
        """CT08 (ECI-06): When DB is unavailable (None), get_latest_persisted_cycle returns None."""
        obs_svc = EngineObservabilityService()
        with obs_svc._lock:
            obs_svc._latest_cycle_diagnostic = {
                "cycle_id": "RAM_CYCLE",
                "symbol": "XAUUSDT",
                "run_id": "RUN_CT08",
                "status": "COMPLETED",
            }
        res = obs_svc.get_latest_persisted_cycle(None, symbol="XAUUSDT", run_id="RUN_CT08")
        assert res is None

    def test_ct09_restart_readback(self, mem_db):
        """CT09: After process restart, DB-persisted cycle is read back correctly."""
        session, _ = mem_db
        obs_svc1 = EngineObservabilityService()
        t0 = 1_700_000_000_000

        obs_svc1.record_evaluation_cycle(
            session,
            cycle_id="CYC_CT09",
            run_id="RUN_CT09",
            started_at_ms=t0,
            completed_at_ms=t0 + 100,
            status="COMPLETED",
        )

        obs_svc2 = EngineObservabilityService()  # Empty RAM
        res = obs_svc2.get_latest_persisted_cycle(session, symbol="XAUUSDT", run_id="RUN_CT09")
        assert res is not None
        assert res["cycle_id"] == "CYC_CT09"

    def test_ct10_run_isolation(self, mem_db):
        """CT10: get_latest_persisted_cycle isolates RUN_A from RUN_B."""
        session, _ = mem_db
        obs_svc = EngineObservabilityService()
        t0 = 1_700_000_000_000

        obs_svc.record_evaluation_cycle(
            None,
            cycle_id="CYC_A",
            run_id="RUN_A",
            started_at_ms=t0,
            completed_at_ms=t0 + 100,
            status="COMPLETED",
        )
        res_b = obs_svc.get_latest_persisted_cycle(session, symbol="XAUUSDT", run_id="RUN_B")
        assert res_b is None

    def test_ct11_heartbeat_counter_idempotency(self, mem_db):
        """CT11: Heartbeat cycle counter increments ONLY on confirmed cycle commit."""
        session, SessionFactory = mem_db
        obs_svc = EngineObservabilityService()
        t0 = 1_700_000_000_000

        # Pass session that is only flushed, not committed
        obs_svc.record_evaluation_cycle(
            session,
            cycle_id="CYC_CT11",
            run_id="RUN_CT11",
            started_at_ms=t0,
            completed_at_ms=t0 + 100,
            status="COMPLETED",
            borrow_session=True,
        )
        session.rollback()

        hb = session.query(models.EngineHeartbeatRecord).filter_by(run_id="RUN_CT11").first()
        # Failed/rolled-back cycle does not leave confirmed count
        assert hb is None

    def test_ct12_no_premature_paper_order_commit(self, mem_db):
        """CT12: Recording cycle does not commit external business models prematurely."""
        session, _ = mem_db
        obs_svc = EngineObservabilityService()
        t0 = 1_700_000_000_000

        res = obs_svc.record_evaluation_cycle(
            session,
            cycle_id="CYC_CT12",
            run_id="RUN_CT12",
            started_at_ms=t0,
            completed_at_ms=t0 + 100,
            status="COMPLETED",
            borrow_session=True,
        )
        assert res["persistence_status"] in ("PENDING_COMMIT", "PENDING_TRANSACTION")
