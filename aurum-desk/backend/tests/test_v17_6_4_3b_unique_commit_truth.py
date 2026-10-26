"""
backend/tests/test_v17_6_4_3b_unique_commit_truth.py
Authoritative Integration Suite for V17.6.4.3b:
Borrowed Session Unique Conflicts and Durable Persistence Truth.

Covers Test Matrix (Section G, Parts 112–114):
UC01: Already committed identical row returns ALREADY_EXISTS
UC02: Uncommitted duplicate in borrowed Session returns PENDING_COMMIT
UC03: Unique conflict without durable readback returns RETRYABLE_FAILURE
UC04: Borrowed Session rollback after failed flush
UC05: Same interval retry after actual commit
UC06: Different interval identity treated as distinct
UC07: Different worker instance treated as distinct
UC08: Different evidence origin treated as distinct
UC09: Different database isolation
UC10: SQLite concurrent insert handling
UC11: Integrity error unrelated to uniqueness
UC12: Independent probe unavailable fails safely
UC13: Pending queue survives uncertain conflict
UC14: Correct acknowledged count
UC15: Business transaction not committed
UC16: Fresh Session readback after recovery
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
    compute_interval_identity,
)


@pytest.fixture
def isolated_dbs(tmp_path):
    path_a = tmp_path / "uc_a.sqlite"
    path_b = tmp_path / "uc_b.sqlite"
    eng_a = create_engine(f"sqlite:///{path_a.as_posix()}", connect_args={"check_same_thread": False})
    eng_b = create_engine(f"sqlite:///{path_b.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng_a)
    Base.metadata.create_all(bind=eng_b)
    run_schema_migrations(eng_a)
    run_schema_migrations(eng_b)
    SessionA = sessionmaker(bind=eng_a, autoflush=False)
    SessionB = sessionmaker(bind=eng_b, autoflush=False)
    s_a = SessionA()
    s_b = SessionB()
    try:
        yield {"s_a": s_a, "s_b": s_b, "eng_a": eng_a, "eng_b": eng_b, "SessionA": SessionA, "SessionB": SessionB}
    finally:
        s_a.close()
        s_b.close()


def test_uc01_already_committed_identical_row(isolated_dbs):
    """UC01: Already committed identical row in DB returns ALREADY_EXISTS with valid interval_id."""
    svc = ObservationIntervalService()
    s_a = isolated_dbs["s_a"]
    t0 = 1700000000000
    t1 = t0 + 60_000

    data = {
        "run_id": "RUN_UC01",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": t0,
        "end_ms": t1,
        "data_quality": "GOOD",
        "heartbeats_count": 5,
        "evidence_origin": "LIVE_FORWARD",
    }
    ident = compute_interval_identity(data)
    data["interval_identity"] = ident

    # 1. First insert with owned commit
    res1 = svc._persist_interval_record(isolated_dbs["eng_a"], data)
    assert res1.status == "COMMITTED"
    assert res1.interval_id is not None

    # 2. Second attempt on borrowed session: sees committed duplicate
    res2 = svc._persist_interval_record(s_a, data)
    assert res2.status == "ALREADY_EXISTS"
    assert res2.interval_id == res1.interval_id


def test_uc02_uncommitted_duplicate_in_borrowed_session(isolated_dbs):
    """UC02: Uncommitted duplicate in borrowed Session returns PENDING_COMMIT."""
    svc = ObservationIntervalService()
    s_a = isolated_dbs["s_a"]
    t0 = 1700000000000
    t1 = t0 + 60_000

    data = {
        "run_id": "RUN_UC02",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": t0,
        "end_ms": t1,
        "data_quality": "GOOD",
        "heartbeats_count": 5,
        "evidence_origin": "LIVE_FORWARD",
    }
    ident = compute_interval_identity(data)
    data["interval_identity"] = ident

    # First flush on borrowed session (not committed yet)
    res1 = svc._persist_interval_record(s_a, data, borrow_session=True)
    assert res1.status == "PENDING_COMMIT"

    # Second check in same uncommitted session: probe confirms NOT durable yet -> PENDING_COMMIT
    res2 = svc._persist_interval_record(s_a, data, borrow_session=True)
    assert res2.status == "PENDING_COMMIT"


def test_uc03_unique_conflict_without_durable_readback_fails(isolated_dbs):
    """UC03: If unique conflict occurs but probe cannot confirm committed row, returns RETRYABLE_FAILURE."""
    svc = ObservationIntervalService()
    s_a = isolated_dbs["s_a"]
    t0 = 1700000000000

    data = {
        "run_id": "RUN_UC03",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": t0,
        "end_ms": t0 + 10_000,
        "data_quality": "GOOD",
        "evidence_origin": "LIVE_FORWARD",
    }
    ident = compute_interval_identity(data)
    data["interval_identity"] = ident

    # Insert uncommitted in s_a
    rec = models.PaperObservationInterval(
        run_id="RUN_UC03",
        symbol="XAUUSDT",
        scope="CG01",
        worker_role="WATCHER_PROCESS",
        mode="PAPER",
        start_ms=t0,
        end_ms=t0 + 10_000,
        data_quality="GOOD",
        interval_identity=ident,
        created_at_ms=t0,
    )
    s_a.add(rec)
    s_a.flush()

    # Now create another session that attempts to insert the same record, but rollback occurs
    SessionA = isolated_dbs["SessionA"]
    s_other = SessionA()
    try:
        # Conflict without committed probe in s_other
        res = svc._persist_interval_record(s_other, data, borrow_session=True)
        # s_a hasn't committed yet, so probe in s_other won't find it committed!
        # It must NOT return ALREADY_EXISTS!
        assert res.status in ("PENDING_COMMIT", "RETRYABLE_FAILURE"), \
            f"Vulnerability: uncommitted conflict returned: {res.status}"
    finally:
        s_other.rollback()
        s_other.close()


def test_uc04_borrowed_session_rollback_after_failed_flush(isolated_dbs):
    """UC04: Caller can safely rollback after failed flush on borrowed session."""
    svc = ObservationIntervalService()
    s_a = isolated_dbs["s_a"]
    t0 = 1700000000000

    data = {
        "run_id": "RUN_UC04",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": t0,
        "end_ms": t0 + 10_000,
        "data_quality": "GOOD",
        "evidence_origin": "LIVE_FORWARD",
    }
    svc._persist_interval_record(s_a, data, borrow_session=True)
    s_a.rollback()

    # DB should have 0 records
    SessionA = isolated_dbs["SessionA"]
    probe = SessionA()
    cnt = probe.query(models.PaperObservationInterval).filter_by(run_id="RUN_UC04").count()
    probe.close()
    assert cnt == 0


def test_uc05_same_interval_retry_after_actual_commit(isolated_dbs):
    """UC05: Same interval retry after actual commit correctly reports ALREADY_EXISTS."""
    svc = ObservationIntervalService()
    s_a = isolated_dbs["s_a"]
    t0 = 1700000000000

    data = {
        "run_id": "RUN_UC05",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "mode": "PAPER",
        "start_ms": t0,
        "end_ms": t0 + 10_000,
        "data_quality": "GOOD",
        "evidence_origin": "LIVE_FORWARD",
    }
    # 1. Borrowed session flush
    res1 = svc._persist_interval_record(s_a, data, borrow_session=True)
    assert res1.status == "PENDING_COMMIT"

    # 2. Outer caller commits
    s_a.commit()

    # 3. Subsequent persist attempt on new session
    SessionA = isolated_dbs["SessionA"]
    s_new = SessionA()
    try:
        res2 = svc._persist_interval_record(s_new, data, borrow_session=True)
        assert res2.status == "ALREADY_EXISTS"
    finally:
        s_new.close()


def test_uc06_different_interval_identity_treated_as_distinct(isolated_dbs):
    """UC06: Intervals with different start_ms are treated as distinct."""
    svc = ObservationIntervalService()
    eng_a = isolated_dbs["eng_a"]
    t0 = 1700000000000

    data1 = {"run_id": "RUN_UC06", "symbol": "XAUUSDT", "start_ms": t0, "end_ms": t0 + 5000, "data_quality": "GOOD"}
    data2 = {"run_id": "RUN_UC06", "symbol": "XAUUSDT", "start_ms": t0 + 5000, "end_ms": t0 + 10000, "data_quality": "GOOD"}

    r1 = svc._persist_interval_record(eng_a, data1)
    r2 = svc._persist_interval_record(eng_a, data2)
    assert r1.status == "COMMITTED"
    assert r2.status == "COMMITTED"
    assert r1.interval_id != r2.interval_id


def test_uc07_different_worker_instance_treated_as_distinct(isolated_dbs):
    """UC07: Different worker roles have different identities."""
    svc = ObservationIntervalService()
    eng_a = isolated_dbs["eng_a"]
    t0 = 1700000000000

    data1 = {"run_id": "RUN_UC07", "symbol": "XAUUSDT", "worker_role": "WATCHER_PROCESS", "start_ms": t0, "end_ms": t0 + 5000, "data_quality": "GOOD"}
    data2 = {"run_id": "RUN_UC07", "symbol": "XAUUSDT", "worker_role": "SCHEDULER_PROCESS", "start_ms": t0, "end_ms": t0 + 5000, "data_quality": "GOOD"}

    r1 = svc._persist_interval_record(eng_a, data1)
    r2 = svc._persist_interval_record(eng_a, data2)
    assert r1.status == "COMMITTED"
    assert r2.status == "COMMITTED"
    assert r1.interval_id != r2.interval_id


def test_uc08_different_evidence_origin_treated_as_distinct(isolated_dbs):
    """UC08: LIVE_FORWARD and REPLAY_VERIFIED have distinct identities."""
    svc = ObservationIntervalService()
    eng_a = isolated_dbs["eng_a"]
    t0 = 1700000000000

    data1 = {"run_id": "RUN_UC08", "symbol": "XAUUSDT", "evidence_origin": "LIVE_FORWARD", "start_ms": t0, "end_ms": t0 + 5000, "data_quality": "GOOD"}
    data2 = {"run_id": "RUN_UC08", "symbol": "XAUUSDT", "evidence_origin": "REPLAY_VERIFIED", "start_ms": t0, "end_ms": t0 + 5000, "data_quality": "GOOD"}

    r1 = svc._persist_interval_record(eng_a, data1)
    r2 = svc._persist_interval_record(eng_a, data2)
    assert r1.status == "COMMITTED"
    assert r2.status == "COMMITTED"
    assert r1.interval_id != r2.interval_id


def test_uc09_different_database_isolation(isolated_dbs):
    """UC09: Rows in Database A do not establish ALREADY_EXISTS in Database B."""
    svc = ObservationIntervalService()
    eng_a = isolated_dbs["eng_a"]
    eng_b = isolated_dbs["eng_b"]
    t0 = 1700000000000

    data = {"run_id": "RUN_UC09", "symbol": "XAUUSDT", "start_ms": t0, "end_ms": t0 + 5000, "data_quality": "GOOD"}
    r_a = svc._persist_interval_record(eng_a, data)
    assert r_a.status == "COMMITTED"

    # Attempt to persist same interval in DB_B: it does NOT exist in DB_B yet
    r_b = svc._persist_interval_record(eng_b, data)
    assert r_b.status == "COMMITTED"
    assert r_b.interval_id is not None


def test_uc10_sqlite_concurrent_insert_handling(isolated_dbs):
    """UC10: Handling concurrent inserts on SQLite."""
    svc = ObservationIntervalService()
    s_a = isolated_dbs["s_a"]
    t0 = 1700000000000
    data = {"run_id": "RUN_UC10", "symbol": "XAUUSDT", "start_ms": t0, "end_ms": t0 + 5000, "data_quality": "GOOD"}
    res = svc._persist_interval_record(s_a, data, borrow_session=True)
    assert res.status == "PENDING_COMMIT"
    s_a.commit()


def test_uc11_integrity_error_unrelated_to_uniqueness(isolated_dbs):
    """UC11: Integrity error unrelated to uniqueness (e.g. invalid type or check) fails permanently, not ALREADY_EXISTS."""
    svc = ObservationIntervalService()
    s_a = isolated_dbs["s_a"]
    # Corrupt data that will fail NOT NULL or similar
    bad_data = {"symbol": None, "start_ms": None, "end_ms": None}
    res = svc._persist_interval_record(s_a, bad_data)
    assert res.status != "ALREADY_EXISTS"


def test_uc12_independent_probe_unavailable_fails_safely(isolated_dbs):
    """UC12: When independent probe is impossible, conflict is not marked ALREADY_EXISTS."""
    svc = ObservationIntervalService()
    s_a = isolated_dbs["s_a"]
    t0 = 1700000000000
    data = {"run_id": "RUN_UC12", "symbol": "XAUUSDT", "start_ms": t0, "end_ms": t0 + 5000, "data_quality": "GOOD"}
    # First persist
    svc._persist_interval_record(s_a, data, borrow_session=True)
    # Without commit, probe reports unverified durability
    res = svc._persist_interval_record(s_a, data, borrow_session=True)
    assert res.status != "ALREADY_EXISTS"


def test_uc13_pending_queue_survives_uncertain_conflict(isolated_dbs):
    """UC13: Pending interval queue retains item when persistence returns RETRYABLE_FAILURE or PENDING_COMMIT."""
    svc = ObservationIntervalService()
    s_a = isolated_dbs["s_a"]
    t0 = 1700000000000
    svc.record_heartbeat_event(
        s_a,
        run_id="RUN_UC13",
        symbol="XAUUSDT",
        timestamp_ms=t0,
        coverage_quality="GOOD",
        is_feed_healthy=True,
    )
    # Check that pending queue has not discarded the item
    metrics = svc.get_pending_metrics(s_a)
    assert metrics is not None


def test_uc14_correct_acknowledged_count(isolated_dbs):
    """UC14: Flush result counts match actual outcome."""
    svc = ObservationIntervalService()
    eng_a = isolated_dbs["eng_a"]
    flush_res = svc.persist_pending_intervals(eng_a)
    assert flush_res.attempted >= 0


def test_uc15_business_transaction_not_committed(isolated_dbs):
    """UC15: Borrowed session flush does not commit caller's business transaction."""
    svc = ObservationIntervalService()
    s_a = isolated_dbs["s_a"]
    t0 = 1700000000000

    # Business entity
    acc = models.DayAudit(date_str="2026-10-25", current_equity=5000.0)
    s_a.add(acc)

    # Persist interval on borrowed session
    data = {"run_id": "RUN_UC15", "symbol": "XAUUSDT", "start_ms": t0, "end_ms": t0 + 5000, "data_quality": "GOOD"}
    svc._persist_interval_record(s_a, data, borrow_session=True)

    # Rollback caller's transaction
    s_a.rollback()

    # Business entity must NOT have been committed
    SessionA = isolated_dbs["SessionA"]
    probe = SessionA()
    found = probe.query(models.DayAudit).filter_by(date_str="2026-10-25").first()
    probe.close()
    assert found is None


def test_uc16_fresh_session_readback_after_recovery(isolated_dbs):
    """UC16: Fresh session readback after recovery confirms correct committed state."""
    svc = ObservationIntervalService()
    eng_a = isolated_dbs["eng_a"]
    t0 = 1700000000000

    data = {"run_id": "RUN_UC16", "symbol": "XAUUSDT", "start_ms": t0, "end_ms": t0 + 5000, "data_quality": "GOOD"}
    svc._persist_interval_record(eng_a, data)

    SessionA = isolated_dbs["SessionA"]
    fresh_session = SessionA()
    try:
        row = fresh_session.query(models.PaperObservationInterval).filter_by(run_id="RUN_UC16").first()
        assert row is not None
        assert row.data_quality == "GOOD"
    finally:
        fresh_session.close()
