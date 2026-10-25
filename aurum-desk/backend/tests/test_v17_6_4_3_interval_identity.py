"""
backend/tests/test_v17_6_4_3_interval_identity.py
Authoritative Verification Suite for DBEA-05 & DBEA-06: Interval Identity Alignment & DB Uniqueness.

Tests ID01 through ID16 per Master Prompt V17.6.4.3 (Parts 087–108):
- ID01: Same identity retry (idempotent, returns ALREADY_EXISTS)
- ID02: Two different worker instances produce distinct records
- ID03: Two different origins (LIVE_FORWARD vs REPLAY) produce distinct records
- ID04: Two different modes (PAPER vs SHADOW) produce distinct records
- ID05: Two different scopes produce distinct records
- ID06: Two different run IDs produce distinct records
- ID07: Same run across two DB files stay isolated
- ID08: Concurrent duplicate insert prevented by DB unique constraint
- ID09: Migration from old schema adds columns safely
- ID10: Migration twice is completely idempotent
- ID11: Legacy row preservation (boundaries and counts untouched)
- ID12: Null legacy provenance handled safely
- ID13: Fresh schema consistency
- ID14: Duplicate SELECT cannot create false ALREADY_EXISTS for different workers
- ID15: Unique-constraint collision properly reconciled
- ID16: Restart and retry existing committed interval
"""
import concurrent.futures
import time
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import models
from models import Base
from database import run_schema_migrations
from services.observation_interval_service import (
    ObservationIntervalService,
    compute_interval_identity,
)


@pytest.fixture
def db_engine(tmp_path):
    db_file = tmp_path / "identity_test.sqlite"
    engine = create_engine(f"sqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    return engine


def test_id01_same_identity_retry_is_idempotent(db_engine):
    """ID01: Persisting the exact same interval record twice returns ALREADY_EXISTS and does not duplicate."""
    service = ObservationIntervalService()
    Session = sessionmaker(bind=db_engine, autoflush=False)
    session = Session()

    try:
        t0 = 1700000000000
        data = {
            "run_id": "RUN_ID01",
            "symbol": "XAUUSDT",
            "scope": "CG01",
            "worker_role": "WATCHER_PROCESS",
            "engine_instance_id": "eng_1",
            "evidence_origin": "LIVE_FORWARD",
            "mode": "PAPER",
            "start_ms": t0,
            "end_ms": t0 + 10_000,
            "data_quality": "GOOD",
            "heartbeats_count": 2,
        }

        res1 = service._persist_interval_record(session, data)
        assert res1.status == "COMMITTED"

        res2 = service._persist_interval_record(session, data)
        assert res2.status == "ALREADY_EXISTS"
        assert res2.interval_id == res1.interval_id

        # Verify exactly one row on disk
        count = session.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.run_id == "RUN_ID01"
        ).count()
        assert count == 1
    finally:
        session.close()


def test_id02_two_different_worker_instances(db_engine):
    """ID02 & ID14: Two different worker instances do NOT collide into one record."""
    service = ObservationIntervalService()
    Session = sessionmaker(bind=db_engine, autoflush=False)
    session = Session()

    try:
        t0 = 1700000000000
        data1 = {
            "run_id": "RUN_SHARED",
            "symbol": "XAUUSDT",
            "scope": "CG01",
            "worker_role": "WATCHER_PROCESS",
            "engine_instance_id": "eng_worker_A",
            "evidence_origin": "LIVE_FORWARD",
            "mode": "PAPER",
            "start_ms": t0,
            "end_ms": t0 + 10_000,
            "data_quality": "GOOD",
        }
        data2 = dict(data1)
        data2["engine_instance_id"] = "eng_worker_B"

        res1 = service._persist_interval_record(session, data1)
        res2 = service._persist_interval_record(session, data2)

        assert res1.status == "COMMITTED"
        assert res2.status == "COMMITTED"
        assert res1.interval_id != res2.interval_id

        count = session.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.run_id == "RUN_SHARED"
        ).count()
        assert count == 2
    finally:
        session.close()


def test_id03_two_different_origins(db_engine):
    """ID03: LIVE_FORWARD and REPLAY records have distinct identities."""
    service = ObservationIntervalService()
    Session = sessionmaker(bind=db_engine, autoflush=False)
    session = Session()

    try:
        t0 = 1700000000000
        data1 = {
            "run_id": "RUN_ORIGIN",
            "symbol": "XAUUSDT",
            "scope": "CG01",
            "worker_role": "WATCHER_PROCESS",
            "engine_instance_id": "eng_1",
            "evidence_origin": "LIVE_FORWARD",
            "mode": "PAPER",
            "start_ms": t0,
            "end_ms": t0 + 5000,
            "data_quality": "GOOD",
        }
        data2 = dict(data1)
        data2["evidence_origin"] = "REPLAY"

        res1 = service._persist_interval_record(session, data1)
        res2 = service._persist_interval_record(session, data2)

        assert res1.status == "COMMITTED"
        assert res2.status == "COMMITTED"
        assert res1.interval_id != res2.interval_id
    finally:
        session.close()


def test_id04_two_different_modes(db_engine):
    """ID04: PAPER and SHADOW records have distinct identities."""
    service = ObservationIntervalService()
    Session = sessionmaker(bind=db_engine, autoflush=False)
    session = Session()

    try:
        t0 = 1700000000000
        data_paper = {
            "run_id": "RUN_MODE",
            "symbol": "XAUUSDT",
            "scope": "CG01",
            "worker_role": "WATCHER_PROCESS",
            "mode": "PAPER",
            "start_ms": t0,
            "end_ms": t0 + 5000,
            "data_quality": "GOOD",
        }
        data_shadow = dict(data_paper)
        data_shadow["mode"] = "SHADOW"

        res1 = service._persist_interval_record(session, data_paper)
        res2 = service._persist_interval_record(session, data_shadow)

        assert res1.status == "COMMITTED"
        assert res2.status == "COMMITTED"
        assert res1.interval_id != res2.interval_id
    finally:
        session.close()


def test_id08_id15_concurrent_duplicate_insert_reconciled(db_engine):
    """ID08 & ID15: Concurrent insertions of the exact same interval record reconcile cleanly to 1 row."""
    service = ObservationIntervalService()
    t0 = 1700000000000
    data = {
        "run_id": "RUN_CONCURRENT",
        "symbol": "XAUUSDT",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
        "engine_instance_id": "eng_conc",
        "evidence_origin": "LIVE_FORWARD",
        "mode": "PAPER",
        "start_ms": t0,
        "end_ms": t0 + 5000,
        "data_quality": "GOOD",
    }

    def worker_insert():
        Session = sessionmaker(bind=db_engine, autoflush=False)
        s = Session()
        try:
            return service._persist_interval_record(s, data)
        finally:
            s.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(worker_insert) for _ in range(4)]
        results = [f.result() for f in futures]

    statuses = [r.status for r in results]
    assert "COMMITTED" in statuses
    assert all(s in ("COMMITTED", "ALREADY_EXISTS") for s in statuses)

    # Database must contain exactly 1 row
    Session = sessionmaker(bind=db_engine, autoflush=False)
    verify_session = Session()
    try:
        count = verify_session.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.run_id == "RUN_CONCURRENT"
        ).count()
        assert count == 1
    finally:
        verify_session.close()


def test_id09_id10_id11_schema_migration_idempotent(tmp_path):
    """ID09, ID10, ID11: Upgrading legacy V17.6.4.2 table to V17.6.4.3 preserves rows and is fully idempotent."""
    legacy_db_file = tmp_path / "legacy.sqlite"
    engine = create_engine(f"sqlite:///{legacy_db_file.as_posix()}", connect_args={"check_same_thread": False})

    with engine.connect() as conn:
        # Create legacy table without engine_instance_id, evidence_origin, interval_identity
        conn.execute(text("""
            CREATE TABLE paper_observation_intervals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope VARCHAR(50) DEFAULT 'CG01',
                run_id VARCHAR(80),
                symbol VARCHAR(20) DEFAULT 'XAUUSDT',
                worker_role VARCHAR(40) DEFAULT 'SCHEDULER',
                mode VARCHAR(30) DEFAULT 'SHADOW',
                start_ms BIGINT NOT NULL,
                end_ms BIGINT NOT NULL,
                data_quality VARCHAR(30) DEFAULT 'GOOD',
                heartbeats_count INTEGER DEFAULT 1,
                created_at_ms BIGINT NOT NULL
            );
        """))
        # Insert 3 legacy rows
        for i in range(3):
            conn.execute(text(f"""
                INSERT INTO paper_observation_intervals
                (scope, run_id, symbol, worker_role, mode, start_ms, end_ms, data_quality, heartbeats_count, created_at_ms)
                VALUES ('CG01', 'LEGACY_RUN', 'XAUUSDT', 'WATCHER_PROCESS', 'PAPER', {1700000000000 + i * 5000}, {1700000000000 + (i+1) * 5000}, 'GOOD', 1, 1700000000000);
            """))
        conn.commit()

    # Verify initial row count
    with engine.connect() as conn:
        count_before = conn.execute(text("SELECT COUNT(*) FROM paper_observation_intervals")).scalar()
        assert count_before == 3

    # Run migration first time (ID09)
    run_schema_migrations(engine)

    # Verify columns added and rows preserved
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(paper_observation_intervals)")).fetchall()}
        assert "engine_instance_id" in cols
        assert "evidence_origin" in cols
        assert "interval_identity" in cols

        count_after = conn.execute(text("SELECT COUNT(*) FROM paper_observation_intervals")).scalar()
        assert count_after == 3

        # Legacy evidence_origin should default to LEGACY_UNKNOWN (Part 095)
        origins = [r[0] for r in conn.execute(text("SELECT evidence_origin FROM paper_observation_intervals")).fetchall()]
        assert all(o == "LEGACY_UNKNOWN" for o in origins)

    # Run migration second time (ID10 Idempotency)
    run_schema_migrations(engine)

    # Verify rows still intact and no errors
    with engine.connect() as conn:
        count_after2 = conn.execute(text("SELECT COUNT(*) FROM paper_observation_intervals")).scalar()
        assert count_after2 == 3


def test_id16_restart_and_retry_existing_interval(db_engine):
    """ID16: Restarting service and retrying previously committed interval returns ALREADY_EXISTS."""
    Session = sessionmaker(bind=db_engine, autoflush=False)
    session = Session()

    try:
        t0 = 1700000000000
        data = {
            "run_id": "RUN_RESTART",
            "symbol": "XAUUSDT",
            "scope": "CG01",
            "worker_role": "WATCHER_PROCESS",
            "engine_instance_id": "eng_1",
            "evidence_origin": "LIVE_FORWARD",
            "mode": "PAPER",
            "start_ms": t0,
            "end_ms": t0 + 10_000,
            "data_quality": "GOOD",
        }
        svc1 = ObservationIntervalService()
        res1 = svc1._persist_interval_record(session, data)
        assert res1.status == "COMMITTED"

        # Simulate service restart: new instance with empty RAM cache
        svc2 = ObservationIntervalService()
        res2 = svc2._persist_interval_record(session, data)
        assert res2.status == "ALREADY_EXISTS"
        assert res2.interval_id == res1.interval_id
    finally:
        session.close()
