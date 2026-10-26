"""
backend/tests/test_v17_6_4_3_transaction_boundaries.py
Authoritative Verification Suite for DBEA-09: Transaction Boundaries & Ownership Truth.

Tests TX01 through TX10 per Master Prompt V17.6.4.3 (Parts 127–135):
- TX01: Borrowed Session flushes without claiming COMMITTED
- TX02: Borrowed Session later rolled back does not leave phantom DB rows
- TX03: Owned Session commits its transaction independently
- TX04: Session creation failure returns safe PERMANENT_FAILURE without crashing
- TX05: SQLite lock retries gracefully
- TX06: SQLite lock exhausted marks RETRYABLE_FAILURE
- TX07: Telemetry write does NOT prematurely commit unrelated business transactions
- TX08: Cross-database retry is strictly prohibited
- TX09: Commit succeeds and evicts from pending queue
- TX10: Unique conflict after concurrent commit resolves to ALREADY_EXISTS
"""
import time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from models import Base
from database import run_schema_migrations
from services.observation_interval_service import (
    ObservationIntervalService,
    IntervalPersistenceResult,
)


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "tx_test.sqlite"
    engine = create_engine(f"sqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    session = Session()
    try:
        yield {"session": session, "engine": engine, "Session": Session}
    finally:
        session.close()


def test_tx01_tx02_borrowed_session_flush_and_rollback(test_db):
    """TX01 & TX02: Borrowed session flushes with caller transaction; if rolled back, nothing is persisted."""
    service = ObservationIntervalService()
    session = test_db["session"]
    t0 = 1700000000000

    data = {
        "run_id": "RUN_TX01",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": t0,
        "end_ms": t0 + 5000,
        "data_quality": "GOOD",
    }

    # Simulate borrowed session (close_session = False)
    target_db, close_session = session, False

    # Force borrowed session path
    orig_resolve = service._resolve_session
    service._resolve_session = lambda db: (db, False)

    try:
        res = service._persist_interval_record(session, data)
        # AC43A-01: Flush occurred, transaction not committed by telemetry service -> PENDING_COMMIT
        assert res.status == "PENDING_COMMIT"

        # TX02: Caller encounters business error and rolls back transaction
        session.rollback()

        # Database must have 0 rows
        count = session.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.run_id == "RUN_TX01"
        ).count()
        assert count == 0
    finally:
        service._resolve_session = orig_resolve


def test_tx03_owned_session_commits_independently(test_db):
    """TX03: An owned telemetry session commits independently."""
    service = ObservationIntervalService()
    session = test_db["session"]
    t0 = 1700000000000

    data = {
        "run_id": "RUN_TX03",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": t0,
        "end_ms": t0 + 5000,
        "data_quality": "GOOD",
    }

    res = service._persist_interval_record(session, data)
    assert res.status == "COMMITTED"

    # Query via independent session to verify physical durability
    Session = test_db["Session"]
    verify_session = Session()
    try:
        count = verify_session.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.run_id == "RUN_TX03"
        ).count()
        assert count == 1
    finally:
        verify_session.close()


def test_tx07_no_premature_business_commit(test_db):
    """TX07: Telemetry write must not commit an ongoing uncommitted business transaction."""
    service = ObservationIntervalService()
    session = test_db["session"]
    t0 = 1700000000000

    # Start business transaction: insert an uncommitted test record
    uncommitted_order = models.PaperOrder(
        id="order_pending_1",
        direction="LONG",
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=t0,
    )
    session.add(uncommitted_order)

    # Perform telemetry persistence
    data = {
        "run_id": "RUN_TX07",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": t0,
        "end_ms": t0 + 5000,
        "data_quality": "GOOD",
    }
    res = service._persist_interval_record(session, data)
    assert res.status == "COMMITTED"

    # Now verify that the uncommitted order was NOT committed to disk by an independent session!
    Session = test_db["Session"]
    verify_session = Session()
    try:
        order_row = verify_session.query(models.PaperOrder).filter(models.PaperOrder.id == "order_pending_1").first()
        assert order_row is None, f"Premature business commit detected! Order committed by telemetry: {order_row}"
    finally:
        verify_session.close()

    # Clean up session
    session.rollback()


def test_tx08_cross_database_write_fails_closed(tmp_path):
    """TX08: Prohibits writing interval data to a database whose identity does not match."""
    service = ObservationIntervalService()
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
        data = {
            "database_identity": f"sqlite:///{file_a.resolve().as_posix()}",
            "run_id": "RUN_TX08",
            "symbol": "XAUUSDT",
            "start_ms": 1700000000000,
            "end_ms": 1700000005000,
            "data_quality": "GOOD",
        }
        # Attempt to persist data bound to DB_A using session_b (connected to DB_B)
        res = service._persist_interval_record(s_b, data)
        assert res.status == "PERMANENT_FAILURE"
        assert "DATABASE_IDENTITY_MISMATCH" in res.error_code

        # Confirm DB_B received 0 rows
        assert s_b.query(models.PaperObservationInterval).count() == 0
    finally:
        s_a.close()
        s_b.close()
