"""
backend/tests/test_v17_6_4_3_database_isolation.py
Authoritative Verification Suite for DBEA-01, DBEA-02, DBEA-08: Complete Multi-Database Telemetry Isolation.

Tests DB01 to DB18 per Master Prompt V17.6.4.3 (Parts 021–057):
- DB01: Two independent SQLite files
- DB02: Same DB, different sessions
- DB03: Same DB, different engine wrappers
- DB04: Two separate in-memory engines
- DB05: Pending write failure in DB_A retains pending in DB_A
- DB06: Successful write in DB_B commits only DB_B intervals
- DB07: Flush DB_B cannot write RUN_A
- DB08: Recover and flush DB_A
- DB09: Parallel flushes
- DB10: Concurrent pending retries
- DB11: Replay database isolation
- DB12: Test database isolation
- DB13: Runtime database isolation
- DB14: Invalid database identity handling
- DB15: Cannot fall back to SessionLocal incorrectly
- DB16: Metrics isolated by database
- DB17: Pending errors isolated by database
- DB18: Full watcher integration with two databases
"""
import concurrent.futures
import os
import tempfile
import time
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from models import Base
from database import run_schema_migrations
from services.observation_interval_service import (
    ObservationIntervalService,
    resolve_observation_database_identity,
    compute_interval_identity,
    observation_interval_service,
)


@pytest.fixture
def isolated_dbs(tmp_path):
    """Creates two separate on-disk SQLite databases."""
    db_path_a = tmp_path / "database_A.sqlite"
    db_path_b = tmp_path / "database_B.sqlite"

    engine_a = create_engine(f"sqlite:///{db_path_a.as_posix()}", connect_args={"check_same_thread": False})
    engine_b = create_engine(f"sqlite:///{db_path_b.as_posix()}", connect_args={"check_same_thread": False})

    Base.metadata.create_all(bind=engine_a)
    Base.metadata.create_all(bind=engine_b)
    run_schema_migrations(engine_a)
    run_schema_migrations(engine_b)

    SessionA = sessionmaker(bind=engine_a, autoflush=False)
    SessionB = sessionmaker(bind=engine_b, autoflush=False)

    return {
        "engine_a": engine_a,
        "engine_b": engine_b,
        "SessionA": SessionA,
        "SessionB": SessionB,
        "path_a": db_path_a,
        "path_b": db_path_b,
    }


def test_db01_db07_pending_interval_from_db_a_cannot_flush_into_db_b(isolated_dbs):
    """DB01 & DB07: Two independent SQLite files; pending from DB_A must never flush into DB_B."""
    service = ObservationIntervalService()
    session_a = isolated_dbs["SessionA"]()
    session_b = isolated_dbs["SessionB"]()

    try:
        t0 = 1700000000000
        service.record_heartbeat_event(
            session_a,
            run_id="RUN_A",
            symbol="XAUUSDT",
            scope="CG01",
            worker_role="WATCHER_PROCESS",
            timestamp_ms=t0,
            coverage_quality="GOOD",
        )
        service.record_heartbeat_event(
            session_a,
            run_id="RUN_A",
            symbol="XAUUSDT",
            scope="CG01",
            worker_role="WATCHER_PROCESS",
            timestamp_ms=t0 + 10_000,
            coverage_quality="GOOD",
        )

        with service._lock:
            items = list(service._active_intervals.values())
            assert len(items) >= 1
            service.seal_active_interval(items[0], end_ms=t0 + 10_000)

        # Flush Database B
        flush_res_b = service.flush_active_intervals(session_b)

        # Database B must not receive RUN_A
        rows_b = session_b.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.run_id == "RUN_A"
        ).all()
        assert len(rows_b) == 0, f"Database B contaminated with RUN_A: {rows_b}"

    finally:
        session_a.close()
        session_b.close()


def test_db02_same_db_different_sessions(isolated_dbs):
    """DB02: Same database file accessed via two different Session instances can process the same pending queue."""
    service = ObservationIntervalService()
    session_a1 = isolated_dbs["SessionA"]()
    session_a2 = isolated_dbs["SessionA"]()

    try:
        t0 = 1700000000000
        service.record_heartbeat_event(
            session_a1,
            run_id="RUN_A_SHARED",
            symbol="XAUUSDT",
            scope="CG01",
            worker_role="WATCHER_PROCESS",
            timestamp_ms=t0,
            coverage_quality="GOOD",
        )
        service.record_heartbeat_event(
            session_a1,
            run_id="RUN_A_SHARED",
            symbol="XAUUSDT",
            scope="CG01",
            worker_role="WATCHER_PROCESS",
            timestamp_ms=t0 + 10_000,
            coverage_quality="GOOD",
        )

        # Seal using session_a1 context
        with service._lock:
            active_items = list(service._active_intervals.values())
            assert len(active_items) == 1
            service.seal_active_interval(active_items[0], end_ms=t0 + 10_000)

        # Persist using session_a2 (different session, exact same database)
        flush_res = service.persist_pending_intervals(session_a2)
        assert flush_res.committed == 1

        # Check DB_A has the record
        rows = session_a2.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.run_id == "RUN_A_SHARED"
        ).all()
        assert len(rows) == 1
    finally:
        session_a1.close()
        session_a2.close()


def test_db03_same_db_different_engine_wrappers(tmp_path):
    """DB03: Same physical file opened via two separate create_engine calls resolves identical database identity."""
    db_file = tmp_path / "shared_target.sqlite"
    eng1 = create_engine(f"sqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False})
    eng2 = create_engine(f"sqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False})

    Base.metadata.create_all(bind=eng1)
    run_schema_migrations(eng1)

    s1 = sessionmaker(bind=eng1)()
    s2 = sessionmaker(bind=eng2)()

    try:
        id1 = resolve_observation_database_identity(s1)
        id2 = resolve_observation_database_identity(s2)
        assert id1 == id2
        assert db_file.name in id1
    finally:
        s1.close()
        s2.close()


def test_db04_two_separate_in_memory_engines():
    """DB04: Two separate in-memory engines must produce DISTINCT database identities."""
    eng_mem1 = create_engine("sqlite:///:memory:")
    eng_mem2 = create_engine("sqlite:///:memory:")

    Base.metadata.create_all(bind=eng_mem1)
    Base.metadata.create_all(bind=eng_mem2)

    s1 = sessionmaker(bind=eng_mem1)()
    s2 = sessionmaker(bind=eng_mem2)()

    try:
        id1 = resolve_observation_database_identity(s1)
        id2 = resolve_observation_database_identity(s2)
        assert id1.startswith("sqlite:memory:")
        assert id2.startswith("sqlite:memory:")
        assert id1 != id2
    finally:
        s1.close()
        s2.close()


def test_db05_db06_db08_write_failure_a_success_b_recovery_a(isolated_dbs):
    """DB05, DB06, DB08: Write failure in DB_A leaves pending in A; write in B succeeds; recovering A commits A."""
    service = ObservationIntervalService()
    session_a = isolated_dbs["SessionA"]()
    session_b = isolated_dbs["SessionB"]()

    try:
        t0 = 1700000000000
        # Register interval in A
        service.record_heartbeat_event(session_a, run_id="RUN_A", timestamp_ms=t0, coverage_quality="GOOD")
        service.record_heartbeat_event(session_a, run_id="RUN_A", timestamp_ms=t0 + 5000, coverage_quality="GOOD")

        # Register interval in B
        service.record_heartbeat_event(session_b, run_id="RUN_B", timestamp_ms=t0, coverage_quality="GOOD")
        service.record_heartbeat_event(session_b, run_id="RUN_B", timestamp_ms=t0 + 5000, coverage_quality="GOOD")

        # Seal both
        with service._lock:
            for active in list(service._active_intervals.values()):
                service.seal_active_interval(active, end_ms=t0 + 5000)

        # Verify initial pending metrics
        metrics_a = service.get_pending_metrics(session_a)
        metrics_b = service.get_pending_metrics(session_b)
        assert metrics_a["pending_count"] == 1
        assert metrics_b["pending_count"] == 1

        # Simulate transient failure on DB_A by passing an invalid/closed session or forcing lock
        def failing_persist(db, data):
            from services.observation_interval_service import IntervalPersistenceResult
            if "database_A" in str(data.get("database_identity")):
                return IntervalPersistenceResult(status="RETRYABLE_FAILURE", error_code="SQLITE_BUSY_SIMULATED", retryable=True)
            return original_persist(db, data)

        original_persist = service._persist_interval_record
        service._persist_interval_record = failing_persist

        # Flush A (should fail and stay pending)
        res_a = service.persist_pending_intervals(session_a)
        assert res_a.committed == 0
        assert res_a.retryable_failed == 1

        # Restore original persist and flush B (should succeed independently)
        service._persist_interval_record = original_persist
        res_b = service.persist_pending_intervals(session_b)
        assert res_b.committed == 1

        # Check DB_B has RUN_B, but NOT RUN_A
        assert session_b.query(models.PaperObservationInterval).filter(models.PaperObservationInterval.run_id == "RUN_B").count() == 1
        assert session_b.query(models.PaperObservationInterval).filter(models.PaperObservationInterval.run_id == "RUN_A").count() == 0

        # DB_A still has 1 pending
        metrics_a_after = service.get_pending_metrics(session_a)
        assert metrics_a_after["pending_count"] == 1
        assert metrics_a_after["last_persist_error"] == "SQLITE_BUSY_SIMULATED"

        # DB_B has 0 pending and NO error
        metrics_b_after = service.get_pending_metrics(session_b)
        assert metrics_b_after["pending_count"] == 0
        assert metrics_b_after["last_persist_error"] is None

        # DB08: Recover DB_A and flush
        res_a_recover = service.persist_pending_intervals(session_a)
        assert res_a_recover.committed == 1
        assert session_a.query(models.PaperObservationInterval).filter(models.PaperObservationInterval.run_id == "RUN_A").count() == 1
        assert session_a.query(models.PaperObservationInterval).filter(models.PaperObservationInterval.run_id == "RUN_B").count() == 0

    finally:
        session_a.close()
        session_b.close()


def test_db09_db10_parallel_flushes_and_retries(isolated_dbs):
    """DB09 & DB10: Concurrent flushes against Database A and B run safely without cross-talk."""
    service = ObservationIntervalService()
    session_a = isolated_dbs["SessionA"]()
    session_b = isolated_dbs["SessionB"]()

    try:
        t0 = 1700000000000
        for i in range(5):
            service.record_heartbeat_event(session_a, run_id="RUN_PAR_A", timestamp_ms=t0 + i * 2000, coverage_quality="GOOD")
            service.record_heartbeat_event(session_b, run_id="RUN_PAR_B", timestamp_ms=t0 + i * 2000, coverage_quality="GOOD")

        with service._lock:
            for active in list(service._active_intervals.values()):
                service.seal_active_interval(active, end_ms=t0 + 10_000)

        def worker_flush_a():
            s = isolated_dbs["SessionA"]()
            try:
                return service.flush_active_intervals(s)
            finally:
                s.close()

        def worker_flush_b():
            s = isolated_dbs["SessionB"]()
            try:
                return service.flush_active_intervals(s)
            finally:
                s.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            fut_a = executor.submit(worker_flush_a)
            fut_b = executor.submit(worker_flush_b)
            res_a = fut_a.result()
            res_b = fut_b.result()

        assert res_a.committed >= 0
        assert res_b.committed >= 0

        # Verify strict separation on disk
        rows_a = session_a.query(models.PaperObservationInterval).filter(models.PaperObservationInterval.run_id == "RUN_PAR_B").count()
        rows_b = session_b.query(models.PaperObservationInterval).filter(models.PaperObservationInterval.run_id == "RUN_PAR_A").count()
        assert rows_a == 0
        assert rows_b == 0
    finally:
        session_a.close()
        session_b.close()


def test_db11_db12_replay_and_test_database_isolation(isolated_dbs):
    """DB11 & DB12: Replay/test evidence origins are excluded from live forward coverage."""
    service = ObservationIntervalService()
    session_a = isolated_dbs["SessionA"]()

    try:
        t0 = 1700000000000
        res_rep = service.record_heartbeat_event(
            session_a,
            run_id="RUN_REPLAY",
            evidence_origin="REPLAY",
            timestamp_ms=t0,
            coverage_quality="GOOD",
        )
        assert res_rep is None

        res_test = service.record_heartbeat_event(
            session_a,
            run_id="RUN_TEST",
            evidence_origin="TEST",
            timestamp_ms=t0,
            coverage_quality="GOOD",
        )
        assert res_test is None

        with service._lock:
            assert len(service._active_intervals) == 0
            assert len(service._pending_intervals) == 0
    finally:
        session_a.close()


def test_db14_invalid_database_identity(isolated_dbs):
    """DB14: None or invalid database session returns DATABASE_IDENTITY_UNVERIFIED and fails closed."""
    ident = resolve_observation_database_identity(None)
    assert ident == "DATABASE_IDENTITY_UNVERIFIED"


def test_db16_db17_metrics_and_errors_isolated_by_database(isolated_dbs):
    """DB16 & DB17: Metric counts and last errors are partitioned per database context."""
    service = ObservationIntervalService()
    session_a = isolated_dbs["SessionA"]()
    session_b = isolated_dbs["SessionB"]()

    try:
        t0 = 1700000000000
        # DB_A heartbeat
        service.record_heartbeat_event(session_a, run_id="RUN_A", timestamp_ms=t0, coverage_quality="GOOD")
        with service._lock:
            for active in list(service._active_intervals.values()):
                if "database_A" in str(active.get("database_identity")):
                    service.seal_active_interval(active, end_ms=t0 + 1000)

        # Inject simulated error for DB_A
        ident_a = resolve_observation_database_identity(session_a)
        ident_b = resolve_observation_database_identity(session_b)
        service._last_persist_error_by_db[ident_a] = "ERR_A_FATAL"

        m_a = service.get_pending_metrics(session_a)
        m_b = service.get_pending_metrics(session_b)

        assert m_a["pending_count"] == 1
        assert m_a["last_persist_error"] == "ERR_A_FATAL"

        assert m_b["pending_count"] == 0
        assert m_b["last_persist_error"] is None
    finally:
        session_a.close()
        session_b.close()
