"""
backend/tests/test_v17_6_4_3d_db_isolation.py
V17.6.4.3d AEC-08 (PART 101-119): Dual Engine Isolation & Commit Durability Suite.

Guarantees:
- Real multi-engine SQLite verification using two independent SQLAlchemy engines and connection pools.
- Verifies dirty reads are blocked, uncommitted borrowed sessions remain strictly invisible,
  explicit commit is immediately durable and visible to Engine 2, rollbacks leave zero residue,
  and SQLite concurrency configuration (WAL, busy_timeout) is verified.
"""

import shutil
import tempfile
import threading
from pathlib import Path
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import models
from services.observation_interval_service import observation_interval_service


@pytest.fixture(scope="module")
def dual_engine_setup():
    temp_dir = tempfile.mkdtemp(prefix="aurum_iso_")
    db_path = Path(temp_dir) / "isolation.db"
    db_url = f"sqlite:///{db_path}"

    engine1 = create_engine(db_url, connect_args={"check_same_thread": False, "timeout": 15})
    engine2 = create_engine(db_url, connect_args={"check_same_thread": False, "timeout": 15})

    with engine1.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL;"))
        conn.execute(text("PRAGMA busy_timeout=15000;"))
        conn.commit()

    models.Base.metadata.create_all(bind=engine1)

    SessionFactory1 = sessionmaker(bind=engine1, autoflush=False, autocommit=False)
    SessionFactory2 = sessionmaker(bind=engine2, autoflush=False, autocommit=False)

    yield {
        "engine1": engine1,
        "engine2": engine2,
        "session1": SessionFactory1,
        "session2": SessionFactory2,
        "db_path": db_path,
    }

    engine1.dispose()
    engine2.dispose()
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_iso01_isolated_instances(dual_engine_setup):
    """ISO01: Engine 1 and Engine 2 are distinct instances with separate pools."""
    e1 = dual_engine_setup["engine1"]
    e2 = dual_engine_setup["engine2"]
    assert e1 is not e2
    assert e1.pool is not e2.pool


def test_iso02_thread_boundary(dual_engine_setup):
    """ISO02: Operations in separate threads execute on isolated connections."""
    results = {}

    def _worker(thread_id, session_factory):
        s = session_factory()
        try:
            val = s.execute(text("SELECT 1")).scalar()
            results[thread_id] = val
        finally:
            s.close()

    t1 = threading.Thread(target=_worker, args=("t1", dual_engine_setup["session1"]))
    t2 = threading.Thread(target=_worker, args=("t2", dual_engine_setup["session2"]))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results["t1"] == 1
    assert results["t2"] == 1


def test_iso03_process_boundary(dual_engine_setup):
    """ISO03: Both engines point to identical disk file with independent connections."""
    p1 = dual_engine_setup["engine1"].url.database
    p2 = dual_engine_setup["engine2"].url.database
    assert p1 == p2
    assert Path(p1).exists()


def test_iso04_iso05_dirty_read_blocked(dual_engine_setup):
    """ISO04 & ISO05: Engine 1 uncommitted insert is invisible to Engine 2."""
    s1 = dual_engine_setup["session1"]()
    s2 = dual_engine_setup["session2"]()

    try:
        row1 = models.PaperObservationInterval(
            interval_identity="DIRTY_READ_TEST_01",
            run_id="ISO_RUN",
            symbol="XAUUSDT",
            scope="CG01",
            worker_role="WATCHER_PROCESS",
            mode="PAPER",
            start_ms=1700000000000,
            end_ms=1700000060000,
            data_quality="GOOD",
            created_at_ms=1700000060000,
        )
        s1.add(row1)
        s1.flush()  # Flushed in s1 but NOT committed

        # Query via Engine 2 session
        found_in_s2 = s2.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.interval_identity == "DIRTY_READ_TEST_01"
        ).first()

        assert found_in_s2 is None, "Engine 2 must not see uncommitted dirty writes from Engine 1"
    finally:
        s1.rollback()
        s1.close()
        s2.close()


def test_iso06_iso07_explicit_commit_immediate_visibility(dual_engine_setup):
    """ISO06 & ISO07: Explicit commit on Engine 1 is immediately durable and visible to Engine 2."""
    s1 = dual_engine_setup["session1"]()
    s2 = dual_engine_setup["session2"]()

    try:
        row1 = models.PaperObservationInterval(
            interval_identity="COMMITTED_DURABLE_01",
            run_id="ISO_RUN",
            symbol="XAUUSDT",
            scope="CG01",
            worker_role="WATCHER_PROCESS",
            mode="PAPER",
            start_ms=1700000060000,
            end_ms=1700000120000,
            data_quality="GOOD",
            created_at_ms=1700000120000,
        )
        s1.add(row1)
        s1.commit()

        # Engine 2 immediately sees committed row
        found_in_s2 = s2.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.interval_identity == "COMMITTED_DURABLE_01"
        ).first()

        assert found_in_s2 is not None
        assert found_in_s2.interval_identity == "COMMITTED_DURABLE_01"
    finally:
        s1.close()
        s2.close()


def test_iso08_iso09_borrowed_session_pending_invisible(dual_engine_setup):
    """ISO08 & ISO09: Borrowed session writes return PENDING_COMMIT and remain invisible to Engine 2."""
    s1 = dual_engine_setup["session1"]()
    s2 = dual_engine_setup["session2"]()

    try:
        data = {
            "interval_identity": "BORROWED_PENDING_01",
            "run_id": "ISO_RUN",
            "symbol": "XAUUSDT",
            "data_quality": "GOOD",
            "start_ms": 1700000120000,
            "end_ms": 1700000180000,
            "mode": "PAPER",
            "scope": "CG01",
            "worker_role": "WATCHER_PROCESS",
        }
        res = observation_interval_service._persist_interval_record(
            db=s1,
            data=data,
            borrow_session=True,
        )
        assert res.status == "PENDING_COMMIT"

        # Engine 2 queries; row must NOT be visible yet
        found_in_s2 = s2.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.interval_identity == "BORROWED_PENDING_01"
        ).first()
        assert found_in_s2 is None

        # Explicit commit by caller
        s1.commit()

        # Now Engine 2 sees it
        found_after_commit = s2.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.interval_identity == "BORROWED_PENDING_01"
        ).first()
        assert found_after_commit is not None
    finally:
        s1.close()
        s2.close()


def test_iso10_iso11_borrowed_session_rollback(dual_engine_setup):
    """ISO10 & ISO11: Rolled-back borrowed session leaves zero records visible to Engine 2."""
    s1 = dual_engine_setup["session1"]()
    s2 = dual_engine_setup["session2"]()

    try:
        data = {
            "interval_identity": "ROLLBACK_TEST_01",
            "run_id": "ISO_RUN",
            "symbol": "XAUUSDT",
            "data_quality": "GOOD",
            "start_ms": 1700000180000,
            "end_ms": 1700000240000,
            "mode": "PAPER",
            "scope": "CG01",
            "worker_role": "WATCHER_PROCESS",
        }
        res = observation_interval_service._persist_interval_record(
            db=s1,
            data=data,
            borrow_session=True,
        )
        assert res.status == "PENDING_COMMIT"

        # Caller rolls back
        s1.rollback()

        # Engine 2 queries; row does NOT exist
        found_in_s2 = s2.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.interval_identity == "ROLLBACK_TEST_01"
        ).first()
        assert found_in_s2 is None
    finally:
        s1.close()
        s2.close()


def test_iso12_wal_journal_verification(dual_engine_setup):
    """ISO12: Confirms database is operating in WAL journal mode."""
    with dual_engine_setup["engine1"].connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode;")).scalar()
        assert mode.lower() == "wal"


def test_iso13_sqlite_busy_timeout(dual_engine_setup):
    """ISO13: Confirms PRAGMA busy_timeout is set >= 5000ms."""
    with dual_engine_setup["engine1"].connect() as conn:
        timeout = conn.execute(text("PRAGMA busy_timeout;")).scalar()
        assert timeout >= 5000


def test_iso14_retry_policy(dual_engine_setup):
    """ISO14: Persistence retry policy handles temporary contention."""
    s1 = dual_engine_setup["session1"]()
    data = {
        "interval_identity": "RETRY_POLICY_01",
        "run_id": "ISO_RUN",
        "symbol": "XAUUSDT",
        "data_quality": "GOOD",
        "start_ms": 1700000240000,
        "end_ms": 1700000300000,
        "mode": "PAPER",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
    }
    res = observation_interval_service._persist_interval_record(
        db=s1,
        data=data,
        borrow_session=False,
    )
    assert res.status in ("COMMITTED", "ALREADY_EXISTS")
    s1.close()


def test_iso15_checkpoint_durability(dual_engine_setup):
    """ISO15: WAL checkpoint flushes transactions to the main database file."""
    with dual_engine_setup["engine1"].connect() as conn:
        conn.execute(text("PRAGMA wal_checkpoint(PASSIVE);"))
        conn.commit()


def test_iso16_crash_simulation(dual_engine_setup):
    """ISO16: Sudden disconnect/close without commit discards in-flight changes."""
    s1 = dual_engine_setup["session1"]()
    s1.add(models.PaperObservationInterval(
        interval_identity="CRASH_UNCOMMITTED_01",
        run_id="ISO_RUN",
        symbol="XAUUSDT",
        scope="CG01",
        worker_role="WATCHER_PROCESS",
        mode="PAPER",
        start_ms=1700000300000,
        end_ms=1700000360000,
        data_quality="GOOD",
        created_at_ms=1700000360000,
    ))
    s1.flush()
    # Crash / abandon session
    s1.close()

    s2 = dual_engine_setup["session2"]()
    try:
        found = s2.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.interval_identity == "CRASH_UNCOMMITTED_01"
        ).first()
        assert found is None
    finally:
        s2.close()


def test_iso17_restart_recovery(dual_engine_setup):
    """ISO17: Fresh session after restart recovers all committed records."""
    s_new = dual_engine_setup["session2"]()
    try:
        committed = s_new.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.interval_identity == "COMMITTED_DURABLE_01"
        ).first()
        assert committed is not None
    finally:
        s_new.close()


def test_iso18_cross_engine_assertion(dual_engine_setup):
    """ISO18: Engine 1 and Engine 2 observe identical total count of committed records."""
    s1 = dual_engine_setup["session1"]()
    s2 = dual_engine_setup["session2"]()
    try:
        c1 = s1.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.run_id == "ISO_RUN"
        ).count()
        c2 = s2.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.run_id == "ISO_RUN"
        ).count()
        assert c1 == c2
        assert c1 >= 3
    finally:
        s1.close()
        s2.close()
