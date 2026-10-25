"""
backend/tests/test_v17_6_4_3a_commit_truth.py
Authoritative Verification Suite for AC43A-01 & AC43A-02:
Transaction Commit Truth, Borrowed Session Flush Semantics, and Pending Acknowledgement Lifecycle.
Covers CT01 through CT15 per Master Prompt V17.6.4.3a (Phase 2, Part 039).
"""
import time
import threading
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from models import Base
from database import run_schema_migrations
from services.observation_interval_service import (
    ObservationIntervalService,
    IntervalPersistenceResult,
    compute_interval_identity,
)


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "commit_truth_test.sqlite"
    engine = create_engine(f"sqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    session = Session()
    try:
        yield {"session": session, "engine": engine, "Session": Session, "db_path": db_file.as_posix()}
    finally:
        session.close()


def test_ct01_owned_session_commits(test_db):
    """CT01: An owned session commits its transaction and returns COMMITTED."""
    service = ObservationIntervalService()
    session = test_db["session"]
    t0 = 1700000000000

    data = {
        "run_id": "RUN_CT01",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": t0,
        "end_ms": t0 + 5000,
        "data_quality": "GOOD",
    }
    # When session is passed to _persist_interval_record without forcing borrowed,
    # _resolve_session opens an owned session bound to the engine and commits.
    res = service._persist_interval_record(session, data)
    assert res.status == "COMMITTED"
    assert res.interval_id is not None

    # Verify physical durability in an independent probe session
    Session = test_db["Session"]
    with Session() as verify_session:
        row = verify_session.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.run_id == "RUN_CT01"
        ).first()
        assert row is not None
        assert row.data_quality == "GOOD"


def test_ct02_borrowed_session_returns_pending_commit(test_db):
    """CT02: Borrowed session flushes without owned transaction commit and returns PENDING_COMMIT."""
    service = ObservationIntervalService()
    session = test_db["session"]
    t0 = 1700000000000

    data = {
        "run_id": "RUN_CT02",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": t0,
        "end_ms": t0 + 5000,
        "data_quality": "GOOD",
    }

    orig_resolve = service._resolve_session
    service._resolve_session = lambda db: (db, False)
    try:
        res = service._persist_interval_record(session, data)
        assert res.status == "PENDING_COMMIT", f"Expected PENDING_COMMIT, got {res.status}"
        assert res.interval_id is not None

        # Verify that an independent session cannot see this uncommitted row yet
        Session = test_db["Session"]
        with Session() as verify_session:
            row = verify_session.query(models.PaperObservationInterval).filter(
                models.PaperObservationInterval.run_id == "RUN_CT02"
            ).first()
            assert row is None, "Uncommitted row was visible to independent session!"
    finally:
        service._resolve_session = orig_resolve
        session.rollback()


def test_ct03_borrowed_rollback_removes_row(test_db):
    """CT03: Caller-owned transaction rollback clears flushed row with 0 durable rows."""
    service = ObservationIntervalService()
    session = test_db["session"]
    t0 = 1700000000000

    data = {
        "run_id": "RUN_CT03",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": t0,
        "end_ms": t0 + 5000,
        "data_quality": "GOOD",
    }

    orig_resolve = service._resolve_session
    service._resolve_session = lambda db: (db, False)
    try:
        res = service._persist_interval_record(session, data)
        assert res.status == "PENDING_COMMIT"

        # Caller aborts and rolls back
        session.rollback()

        # Independent session must confirm 0 rows
        Session = test_db["Session"]
        with Session() as verify_session:
            count = verify_session.query(models.PaperObservationInterval).filter(
                models.PaperObservationInterval.run_id == "RUN_CT03"
            ).count()
            assert count == 0
    finally:
        service._resolve_session = orig_resolve


def test_ct04_borrowed_commit_confirms_row(test_db):
    """CT04: Borrowed session flushes, outer caller commits, independent session confirms row."""
    service = ObservationIntervalService()
    session = test_db["session"]
    t0 = 1700000000000

    data = {
        "run_id": "RUN_CT04",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": t0,
        "end_ms": t0 + 5000,
        "data_quality": "GOOD",
    }

    orig_resolve = service._resolve_session
    service._resolve_session = lambda db: (db, False)
    try:
        res = service._persist_interval_record(session, data)
        assert res.status == "PENDING_COMMIT"

        # Caller completes business unit of work and commits outer transaction
        session.commit()

        # Independent session confirms durability
        Session = test_db["Session"]
        with Session() as verify_session:
            count = verify_session.query(models.PaperObservationInterval).filter(
                models.PaperObservationInterval.run_id == "RUN_CT04"
            ).count()
            assert count == 1
    finally:
        service._resolve_session = orig_resolve


def test_ct05_pending_interval_retained_until_acknowledgement(test_db):
    """CT05: Pending interval retained in pending queue across borrowed session flush."""
    service = ObservationIntervalService()
    session = test_db["session"]
    t0 = 1700000000000

    orig_resolve = service._resolve_session
    service._resolve_session = lambda db: (db, False)
    try:
        active = {
            "database_identity": f"sqlite:///{test_db['db_path']}",
            "run_id": "RUN_CT05",
            "symbol": "XAUUSDT",
            "scope": "CG01",
            "worker_role": "WATCHER_PROCESS",
            "mode": "PAPER",
            "start_ms": t0,
            "end_ms": t0 + 5000,
            "data_quality": "GOOD",
            "heartbeats_count": 5,
        }
        with service._lock:
            service.seal_active_interval(active, end_ms=t0 + 5000)

        # Flush pending intervals on borrowed session
        flush_res = service.persist_pending_intervals(session)
        assert flush_res.committed == 0, "Borrowed session flush must not report committed count > 0"
        assert flush_res.pending_count == 1, "Interval must be retained in pending queue"

        # Verify pending metrics shows 1 pending interval
        metrics = service.get_pending_metrics(session, run_id="RUN_CT05")
        assert metrics["pending_count"] == 1

        # Now outer transaction commits
        session.commit()

        # Subsequent persistence / acknowledgement evicts interval from pending queue
        flush_res2 = service.persist_pending_intervals(session)
        assert flush_res2.already_exists == 1
        assert flush_res2.pending_count == 0
    finally:
        service._resolve_session = orig_resolve


def test_ct06_commit_failure(test_db):
    """CT06: Simulates DB error during persistence; pending interval is retained with RETRYABLE_FAILURE."""
    service = ObservationIntervalService()
    session = test_db["session"]
    t0 = 1700000000000

    orig_persist = service._persist_interval_record
    service._persist_interval_record = lambda db, data: IntervalPersistenceResult(
        status="RETRYABLE_FAILURE",
        error_code="database is locked",
        retryable=True,
    )
    try:
        active = {
            "database_identity": f"sqlite:///{test_db['db_path']}",
            "run_id": "RUN_CT06",
            "symbol": "XAUUSDT",
            "scope": "CG01",
            "worker_role": "WATCHER_PROCESS",
            "mode": "PAPER",
            "start_ms": t0,
            "end_ms": t0 + 5000,
            "data_quality": "GOOD",
            "heartbeats_count": 5,
        }
        with service._lock:
            service.seal_active_interval(active, end_ms=t0 + 5000)

        res = service.persist_pending_intervals(session)
        assert res.committed == 0
        assert res.retryable_failed == 1
        assert res.pending_count == 1

        metrics = service.get_pending_metrics(session, run_id="RUN_CT06")
        assert metrics["pending_count"] == 1
        assert metrics["last_persist_error"] is not None
    finally:
        service._persist_interval_record = orig_persist


def test_ct07_session_closed_before_commit(test_db):
    """CT07: Caller closes session without committing; independent readback confirms 0 rows."""
    service = ObservationIntervalService()
    Session = test_db["Session"]
    s = Session()
    t0 = 1700000000000

    data = {
        "run_id": "RUN_CT07",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": t0,
        "end_ms": t0 + 5000,
        "data_quality": "GOOD",
    }

    orig_resolve = service._resolve_session
    service._resolve_session = lambda db: (db, False)
    try:
        res = service._persist_interval_record(s, data)
        assert res.status == "PENDING_COMMIT"

        # Session closed without commit
        s.close()

        with Session() as verify_session:
            count = verify_session.query(models.PaperObservationInterval).filter(
                models.PaperObservationInterval.run_id == "RUN_CT07"
            ).count()
            assert count == 0
    finally:
        service._resolve_session = orig_resolve


def test_ct08_duplicate_retry(test_db):
    """CT08: Duplicate interval retry resolves to ALREADY_EXISTS without duplicate rows."""
    service = ObservationIntervalService()
    session = test_db["session"]
    t0 = 1700000000000

    data = {
        "run_id": "RUN_CT08",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": t0,
        "end_ms": t0 + 5000,
        "data_quality": "GOOD",
    }

    res1 = service._persist_interval_record(session, data)
    assert res1.status == "COMMITTED"

    res2 = service._persist_interval_record(session, data)
    assert res2.status == "ALREADY_EXISTS"
    assert res2.interval_id == res1.interval_id

    # Verify exactly 1 row exists
    Session = test_db["Session"]
    with Session() as verify_session:
        count = verify_session.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.run_id == "RUN_CT08"
        ).count()
        assert count == 1


def test_ct09_no_premature_paper_order_commit(test_db):
    """CT09: Telemetry persistence does NOT prematurely commit unrelated PaperOrder mutations."""
    service = ObservationIntervalService()
    session = test_db["session"]
    t0 = 1700000000000

    # Add uncommitted order to caller's transaction
    uncommitted_order = models.PaperOrder(
        id="order_ct09",
        direction="SHORT",
        planned_entry=2650.0,
        stop_loss=2655.0,
        take_profit=2640.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=t0,
    )
    session.add(uncommitted_order)

    data = {
        "run_id": "RUN_CT09",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": t0,
        "end_ms": t0 + 5000,
        "data_quality": "GOOD",
    }
    # Telemetry persistence creates an owned session bound to the engine, committing only the interval
    res = service._persist_interval_record(session, data)
    assert res.status == "COMMITTED"

    # Verify order is NOT committed to disk by probing via independent session
    Session = test_db["Session"]
    with Session() as verify_session:
        order_in_db = verify_session.query(models.PaperOrder).filter(models.PaperOrder.id == "order_ct09").first()
        assert order_in_db is None, "Uncommitted PaperOrder was committed by telemetry service!"

    session.rollback()


def test_ct10_concurrent_sqlite_writer(test_db):
    """CT10: Concurrent threads writing telemetry to SQLite handle locks safely."""
    service = ObservationIntervalService()
    session = test_db["session"]
    Session = test_db["Session"]
    t0 = 1700000000000
    errors = []

    def worker(idx):
        try:
            with Session() as s:
                data = {
                    "run_id": f"RUN_CT10_{idx}",
                    "symbol": "XAUUSDT",
                    "scope": "CG01",
                    "worker_role": "WATCHER_PROCESS",
                    "mode": "PAPER",
                    "start_ms": t0 + idx * 1000,
                    "end_ms": t0 + (idx + 1) * 1000,
                    "data_quality": "GOOD",
                }
                res = service._persist_interval_record(s, data)
                if res.status not in ("COMMITTED", "ALREADY_EXISTS"):
                    errors.append(f"Worker {idx} failed with {res.status}: {res.error_code}")
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Concurrent persistence errors: {errors}"
    with Session() as s:
        count = s.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.run_id.like("RUN_CT10_%")
        ).count()
        assert count == 5


def test_ct11_different_database_context(tmp_path):
    """CT11: Target database identity mismatch fails closed with PERMANENT_FAILURE."""
    service = ObservationIntervalService()
    db_a = tmp_path / "a.sqlite"
    db_b = tmp_path / "b.sqlite"
    eng_a = create_engine(f"sqlite:///{db_a.as_posix()}", connect_args={"check_same_thread": False})
    eng_b = create_engine(f"sqlite:///{db_b.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng_a)
    Base.metadata.create_all(bind=eng_b)
    run_schema_migrations(eng_a)
    run_schema_migrations(eng_b)

    s_b = sessionmaker(bind=eng_b, autoflush=False)()
    try:
        data = {
            "database_identity": f"sqlite:///{db_a.resolve().as_posix()}",
            "run_id": "RUN_CT11",
            "symbol": "XAUUSDT",
            "start_ms": 1700000000000,
            "end_ms": 1700000005000,
            "data_quality": "GOOD",
        }
        res = service._persist_interval_record(s_b, data)
        assert res.status == "PERMANENT_FAILURE"
        assert "DATABASE_IDENTITY_MISMATCH" in (res.error_code or "")
    finally:
        s_b.close()


def test_ct12_restart_after_durable_commit(test_db):
    """CT12: Restarting telemetry service preserves committed records."""
    service1 = ObservationIntervalService()
    session = test_db["session"]
    t0 = 1700000000000

    data = {
        "run_id": "RUN_CT12",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": t0,
        "end_ms": t0 + 5000,
        "data_quality": "GOOD",
    }
    res = service1._persist_interval_record(session, data)
    assert res.status == "COMMITTED"

    # Simulate restart with fresh service instance
    service2 = ObservationIntervalService()
    Session = test_db["Session"]
    with Session() as s:
        row = s.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.run_id == "RUN_CT12"
        ).first()
        assert row is not None
        assert row.data_quality == "GOOD"


def test_ct13_restart_after_uncommitted_write(test_db):
    """CT13: Restarting after an uncommitted flush confirms 0 rows exist."""
    service1 = ObservationIntervalService()
    session = test_db["session"]
    t0 = 1700000000000

    data = {
        "run_id": "RUN_CT13",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": t0,
        "end_ms": t0 + 5000,
        "data_quality": "GOOD",
    }

    orig_resolve = service1._resolve_session
    service1._resolve_session = lambda db: (db, False)
    try:
        res = service1._persist_interval_record(session, data)
        assert res.status == "PENDING_COMMIT"
        session.close()  # closed without commit
    finally:
        service1._resolve_session = orig_resolve

    # Simulate restart with fresh service and session
    Session = test_db["Session"]
    with Session() as s:
        count = s.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.run_id == "RUN_CT13"
        ).count()
        assert count == 0


def test_ct14_accurate_pending_count(test_db):
    """CT14: Pending metrics accurately reflect pending intervals count."""
    service = ObservationIntervalService()
    session = test_db["session"]
    t0 = 1700000000000

    orig_resolve = service._resolve_session
    service._resolve_session = lambda db: (db, False)
    try:
        active = {
            "database_identity": f"sqlite:///{test_db['db_path']}",
            "run_id": "RUN_CT14",
            "symbol": "XAUUSDT",
            "scope": "CG01",
            "worker_role": "WATCHER_PROCESS",
            "mode": "PAPER",
            "start_ms": t0,
            "end_ms": t0 + 5000,
            "data_quality": "GOOD",
            "heartbeats_count": 5,
        }
        with service._lock:
            service.seal_active_interval(active, end_ms=t0 + 5000)

        metrics = service.get_pending_metrics(session, run_id="RUN_CT14")
        assert metrics["pending_count"] == 1
    finally:
        service._resolve_session = orig_resolve


def test_ct15_accurate_flush_count(test_db):
    """CT15: Flush result returns accurate committed count."""
    service = ObservationIntervalService()
    session = test_db["session"]
    t0 = 1700000000000

    active = {
        "database_identity": f"sqlite:///{test_db['db_path']}",
        "run_id": "RUN_CT15",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": t0,
        "end_ms": t0 + 5000,
        "data_quality": "GOOD",
        "heartbeats_count": 5,
    }
    with service._lock:
        service.seal_active_interval(active, end_ms=t0 + 5000)

    flush_res = service.persist_pending_intervals(session)
    assert flush_res.committed == 1
    assert flush_res.pending_count == 0
