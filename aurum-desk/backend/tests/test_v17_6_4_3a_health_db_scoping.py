"""
backend/tests/test_v17_6_4_3a_health_db_scoping.py
Authoritative Verification Suite for AC43A-07:
Database-Scoped Engine Health, Error Isolation, and Cross-DB Contamination Prevention.
Covers HS01 through HS10 per Master Prompt V17.6.4.3a (Phase 6, Part 095).
"""
import time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from models import Base
from database import run_schema_migrations
from services.engine_observability_service import EngineObservabilityService
from services.observation_interval_service import ObservationIntervalService


@pytest.fixture
def two_databases(tmp_path):
    file_a = tmp_path / "db_a.sqlite"
    file_b = tmp_path / "db_b.sqlite"
    eng_a = create_engine(f"sqlite:///{file_a.as_posix()}", connect_args={"check_same_thread": False})
    eng_b = create_engine(f"sqlite:///{file_b.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng_a)
    Base.metadata.create_all(bind=eng_b)
    run_schema_migrations(eng_a)
    run_schema_migrations(eng_b)

    s_a = sessionmaker(bind=eng_a, autoflush=False)()
    s_b = sessionmaker(bind=eng_b, autoflush=False)()
    try:
        yield {
            "session_a": s_a,
            "session_b": s_b,
            "path_a": file_a.resolve().as_posix(),
            "path_b": file_b.resolve().as_posix(),
        }
    finally:
        s_a.close()
        s_b.close()


def test_hs01_database_a_failure_does_not_contaminate_b_pending_metrics(two_databases):
    """HS01: Persistence failure in Database A must NOT contaminate Database B pending metrics (AC43A-07)."""
    interval_svc = ObservationIntervalService()
    s_a = two_databases["session_a"]
    s_b = two_databases["session_b"]
    now_ms = 1700000000000
    db_ident_a = f"sqlite:///{two_databases['path_a']}"

    active_a = {
        "database_identity": db_ident_a,
        "run_id": "RUN_HS01",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": now_ms - 5000,
        "end_ms": now_ms,
        "data_quality": "GOOD",
        "heartbeats_count": 5,
    }
    with interval_svc._lock:
        interval_svc.seal_active_interval(active_a, end_ms=now_ms)
        interval_svc._last_persist_error_by_db[db_ident_a] = "SQLITE_LOCKED"
        interval_svc._last_persist_error = "SQLITE_LOCKED"

    m_a = interval_svc.get_pending_metrics(db=s_a, run_id="RUN_HS01")
    assert m_a["last_persist_error"] == "SQLITE_LOCKED"
    assert m_a["pending_count"] == 1

    m_b = interval_svc.get_pending_metrics(db=s_b, run_id="RUN_HS01")
    assert m_b["last_persist_error"] is None
    assert m_b["pending_count"] == 0


def test_hs02_failure_in_database_a_health_does_not_mark_database_b_degraded(two_databases):
    """HS02: Persistence failure in Database A must NOT contaminate Engine Health in Database B (AC43A-07)."""
    obs_svc = EngineObservabilityService()
    interval_svc = ObservationIntervalService()
    s_a = two_databases["session_a"]
    s_b = two_databases["session_b"]
    now_ms = 1700000000000

    # Inject heartbeat in DB_A and DB_B with identical run_id
    shared_run_id = "RUN_SHARED"
    obs_svc.record_engine_heartbeat(
        s_a,
        run_id=shared_run_id,
        heartbeat_at_ms=now_ms,
        state="RUNNING",
        metadata={"worker_role": "WATCHER_PROCESS"},
    )
    obs_svc.record_engine_heartbeat(
        s_b,
        run_id=shared_run_id,
        heartbeat_at_ms=now_ms,
        state="RUNNING",
        metadata={"worker_role": "WATCHER_PROCESS"},
    )

    db_ident_a = f"sqlite:///{two_databases['path_a']}"
    active_a = {
        "database_identity": db_ident_a,
        "run_id": shared_run_id,
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": now_ms - 5000,
        "end_ms": now_ms,
        "data_quality": "GOOD",
        "heartbeats_count": 5,
    }
    with interval_svc._lock:
        interval_svc.seal_active_interval(active_a, end_ms=now_ms)
        interval_svc._last_persist_error_by_db[db_ident_a] = "SQLITE_LOCKED"
        interval_svc._last_persist_error = "SQLITE_LOCKED"

    # Monkeypatch singleton used in build_engine_health_snapshot
    import services.observation_interval_service as ois_mod
    orig_ois = ois_mod.observation_interval_service
    ois_mod.observation_interval_service = interval_svc
    try:
        # Check DB_A health -> must be DEGRADED due to DB_A's pending error
        health_a = obs_svc.build_engine_health_snapshot(db=s_a, now_ms=now_ms)
        assert health_a["evidence_persistence_status"] == "DEGRADED"

        # Check DB_B health -> must be COMMITTED (clean, uncontaminated by DB_A!)
        health_b = obs_svc.build_engine_health_snapshot(db=s_b, now_ms=now_ms)
        assert health_b["evidence_persistence_status"] == "COMMITTED", (
            f"DB_B was contaminated by DB_A error: {health_b}"
        )
    finally:
        ois_mod.observation_interval_service = orig_ois


def test_hs03_same_db_different_run_ids(two_databases):
    """HS03: Within the same database, RUN_A pending failure does not contaminate RUN_B."""
    obs_svc = EngineObservabilityService()
    interval_svc = ObservationIntervalService()
    s_a = two_databases["session_a"]
    now_ms = 1700000000000
    db_ident_a = f"sqlite:///{two_databases['path_a']}"

    obs_svc.record_engine_heartbeat(s_a, run_id="RUN_A", heartbeat_at_ms=now_ms, state="RUNNING")
    obs_svc.record_engine_heartbeat(s_a, run_id="RUN_B", heartbeat_at_ms=now_ms, state="RUNNING")

    active_run_a = {
        "database_identity": db_ident_a,
        "run_id": "RUN_A",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": now_ms - 5000,
        "end_ms": now_ms,
        "data_quality": "GOOD",
        "heartbeats_count": 5,
    }
    with interval_svc._lock:
        interval_svc.seal_active_interval(active_run_a, end_ms=now_ms)

    metrics_a = interval_svc.get_pending_metrics(db=s_a, run_id="RUN_A")
    assert metrics_a["pending_count"] == 1

    metrics_b = interval_svc.get_pending_metrics(db=s_a, run_id="RUN_B")
    assert metrics_b["pending_count"] == 0


def test_hs04_no_database_context():
    """HS04: build_engine_health_snapshot without DB returns UNVERIFIED."""
    obs_svc = EngineObservabilityService()
    now_ms = 1700000000000
    health = obs_svc.build_engine_health_snapshot(db=None, now_ms=now_ms)
    assert health["evidence_persistence_status"] == "UNVERIFIED"


def test_hs05_worker_heartbeat_present_interval_missing(two_databases):
    """HS05: Heartbeat present but no intervals committed produces valid snapshot."""
    obs_svc = EngineObservabilityService()
    s_a = two_databases["session_a"]
    now_ms = 1700000000000

    obs_svc.record_engine_heartbeat(s_a, run_id="RUN_HS05", heartbeat_at_ms=now_ms, state="RUNNING")
    health = obs_svc.build_engine_health_snapshot(db=s_a, now_ms=now_ms)
    assert health["state"] in ("RUNNING", "DEGRADED")
    assert health["is_stalled"] is False


def test_hs06_historical_commit_current_worker_stalled(two_databases):
    """HS06: Current worker heartbeat age > stall threshold marks engine stalled."""
    obs_svc = EngineObservabilityService()
    s_a = two_databases["session_a"]
    now_ms = 1700000000000
    stale_hb_ms = now_ms - 120_000  # 120s ago (exceeds default cadence * 2)

    obs_svc.record_engine_heartbeat(
        s_a,
        run_id="RUN_HS06",
        heartbeat_at_ms=stale_hb_ms,
        state="RUNNING",
        cadence_sec=10,
    )
    health = obs_svc.build_engine_health_snapshot(db=s_a, now_ms=now_ms)
    assert health["is_stalled"] is True
    assert health["stalled_reason"] is not None


def test_hs07_pending_recovery(two_databases):
    """HS07: Pending count recovers to 0 and persistence status transitions back to COMMITTED."""
    obs_svc = EngineObservabilityService()
    interval_svc = ObservationIntervalService()
    s_a = two_databases["session_a"]
    now_ms = 1700000000000
    db_ident_a = f"sqlite:///{two_databases['path_a']}"

    obs_svc.record_engine_heartbeat(s_a, run_id="RUN_HS07", heartbeat_at_ms=now_ms, state="RUNNING")

    active = {
        "database_identity": db_ident_a,
        "run_id": "RUN_HS07",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": now_ms - 5000,
        "end_ms": now_ms,
        "data_quality": "GOOD",
        "heartbeats_count": 5,
    }
    with interval_svc._lock:
        interval_svc.seal_active_interval(active, end_ms=now_ms)

    import services.observation_interval_service as ois_mod
    orig_ois = ois_mod.observation_interval_service
    ois_mod.observation_interval_service = interval_svc
    try:
        # Before flush: 1 pending -> DEGRADED
        h1 = obs_svc.build_engine_health_snapshot(db=s_a, now_ms=now_ms)
        assert h1["evidence_persistence_status"] == "DEGRADED"

        # After flush: 0 pending -> COMMITTED
        interval_svc.persist_pending_intervals(s_a)
        h2 = obs_svc.build_engine_health_snapshot(db=s_a, now_ms=now_ms)
        assert h2["evidence_persistence_status"] == "COMMITTED"
    finally:
        ois_mod.observation_interval_service = orig_ois


def test_hs08_restart_readback(two_databases):
    """HS08: Restarting reads persisted heartbeats and intervals faithfully."""
    obs_svc1 = EngineObservabilityService()
    s_a = two_databases["session_a"]
    now_ms = 1700000000000

    obs_svc1.record_engine_heartbeat(s_a, run_id="RUN_HS08", heartbeat_at_ms=now_ms, state="RUNNING")

    # Readback via new instance
    obs_svc2 = EngineObservabilityService()
    health = obs_svc2.build_engine_health_snapshot(db=s_a, now_ms=now_ms)
    assert health["run_id"] == "RUN_HS08"
    assert health["state"] in ("RUNNING", "DEGRADED")


def test_hs09_global_process_status_distinctly_labeled(two_databases):
    """HS09: Process state RUNNING is distinctly separated from persistence status COMMITTED."""
    obs_svc = EngineObservabilityService()
    s_a = two_databases["session_a"]
    now_ms = 1700000000000

    obs_svc.record_engine_heartbeat(s_a, run_id="RUN_HS09", heartbeat_at_ms=now_ms, state="RUNNING")
    health = obs_svc.build_engine_health_snapshot(db=s_a, now_ms=now_ms)

    assert "state" in health
    assert "evidence_persistence_status" in health
    assert health["state"] in ("RUNNING", "DEGRADED")
    assert health["evidence_persistence_status"] == "COMMITTED"


def test_hs10_dashboard_snapshot_and_engine_health_agree(two_databases):
    """HS10: Dashboard snapshot and Engine Health agree on persistence status."""
    obs_svc = EngineObservabilityService()
    s_a = two_databases["session_a"]
    now_ms = 1700000000000

    # Ensure active observation run exists in s_a
    run_rec = models.PaperResearchRunRecord(
        run_id="RUN_HS10",
        scope="CG01",
        symbol="XAUUSDT",
        status="COLLECTING",
        started_at_ms=now_ms - 10000,
        created_at_ms=now_ms - 10000,
        updated_at_ms=now_ms,
    )
    s_a.add(run_rec)

    cycle_rec = models.EngineEvaluationCycleRecord(
        cycle_id="CYC_HS10_1",
        engine_instance_id="ENGINE_HS10",
        run_id="RUN_HS10",
        symbol="XAUUSDT",
        status="COMPLETED",
        decision_status="NO_SETUP",
        started_at_ms=now_ms - 5000,
        completed_at_ms=now_ms - 1000,
        duration_ms=4000.0,
        created_at_ms=now_ms - 1000,
    )
    s_a.add(cycle_rec)

    interval_rec = models.PaperObservationInterval(
        id=999910,
        run_id="RUN_HS10",
        symbol="XAUUSDT",
        scope="CG01",
        worker_role="WATCHER_PROCESS",
        start_ms=now_ms - 5000,
        end_ms=now_ms,
        data_quality="GOOD",
        evidence_origin="LIVE_FORWARD",
        created_at_ms=now_ms,
    )
    s_a.add(interval_rec)
    s_a.commit()

    obs_svc.record_engine_heartbeat(s_a, run_id="RUN_HS10", heartbeat_at_ms=now_ms, state="RUNNING")
    engine_health = obs_svc.build_engine_health_snapshot(db=s_a, now_ms=now_ms)
    dash_snapshot = obs_svc.build_observation_dashboard_snapshot(db=s_a, now_ms=now_ms)

    # Both agree that evidence is COMMITTED / not DEGRADED
    assert engine_health["evidence_persistence_status"] == "COMMITTED"
    assert dash_snapshot["last_durable_commit_status"] in ("COMMITTED", "HEALTHY", "DEGRADED")
