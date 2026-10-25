"""
backend/tests/test_v17_6_4_telemetry_database_isolation.py
Tests for Telemetry Database Isolation, Session Binding & Durability (Phase E, DB01-DB12).
"""
import time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from services.engine_observability_service import engine_observability_service
from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator


def make_test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False)
    return TestingSession()


def test_db01_live_session_binding():
    """DB01: Telemetry binds to caller session's engine."""
    db1 = make_test_db()
    try:
        engine_observability_service.record_engine_heartbeat(
            db1,
            symbol="XAUUSDT",
            run_id="RUN_LIVE_01",
            state="RUNNING",
        )
        row = db1.query(models.EngineHeartbeatRecord).first()
        assert row is not None
        assert row.run_id == "RUN_LIVE_01"
    finally:
        db1.close()


def test_db02_replay_database_isolation():
    """DB02: Replay database writes stay inside replay database, not touching separate db."""
    db_live = make_test_db()
    db_replay = make_test_db()
    try:
        engine_observability_service.record_evaluation_cycle(
            db_replay,
            cycle_id="cycle-replay-01",
            run_id="RUN_REPLAY",
            started_at_ms=1000,
            completed_at_ms=1050,
            status="COMPLETED",
            symbol="XAUUSDT",
        )
        # Verify db_replay has it
        r_rep = db_replay.query(models.EngineEvaluationCycleRecord).filter_by(cycle_id="cycle-replay-01").first()
        assert r_rep is not None

        # Verify db_live has nothing
        r_live = db_live.query(models.EngineEvaluationCycleRecord).filter_by(cycle_id="cycle-replay-01").first()
        assert r_live is None
    finally:
        db_live.close()
        db_replay.close()


def test_db03_test_database_isolation():
    """DB03: Test database receives telemetry when passed as db fixture."""
    test_db = make_test_db()
    try:
        engine_observability_service.record_evaluation_cycle(
            test_db,
            cycle_id="cycle-test-db03",
            run_id="RUN_TEST_03",
            started_at_ms=2000,
            completed_at_ms=2050,
            status="COMPLETED",
            symbol="XAUUSDT",
        )
        row = test_db.query(models.EngineEvaluationCycleRecord).filter_by(cycle_id="cycle-test-db03").first()
        assert row is not None
        assert row.run_id == "RUN_TEST_03"
    finally:
        test_db.close()


def test_db04_contexts_cannot_cross():
    """DB04: Two independent test databases never leak records to each other."""
    db_a = make_test_db()
    db_b = make_test_db()
    try:
        engine_observability_service.record_engine_heartbeat(
            db_a, symbol="XAUUSDT", run_id="RUN_A", engine_instance_id="inst-a"
        )
        engine_observability_service.record_engine_heartbeat(
            db_b, symbol="XAUUSDT", run_id="RUN_B", engine_instance_id="inst-b"
        )

        assert db_a.query(models.EngineHeartbeatRecord).filter_by(engine_instance_id="inst-b").first() is None
        assert db_b.query(models.EngineHeartbeatRecord).filter_by(engine_instance_id="inst-a").first() is None
    finally:
        db_a.close()
        db_b.close()


def test_db05_caller_session_rollback_preserves_telemetry():
    """DB05: Caller business session rollback does NOT delete committed telemetry."""
    db = make_test_db()
    try:
        # Add uncommitted business record
        uncommitted_cfg = models.SystemConfig(
            key="uncommitted_dirty_key",
            value="dirty_value",
            updated_at=int(time.time() * 1000),
        )
        db.add(uncommitted_cfg)

        # Record telemetry (commits independently to db's engine)
        engine_observability_service.record_evaluation_cycle(
            db,
            cycle_id="cycle-db05-rollback",
            run_id="RUN_DB05",
            started_at_ms=3000,
            completed_at_ms=3050,
            status="COMPLETED",
            symbol="XAUUSDT",
        )

        # Rollback caller's business transaction
        db.rollback()

        # Business mutation must be rolled back
        assert db.query(models.SystemConfig).filter_by(key="uncommitted_dirty_key").first() is None

        # Telemetry must remain durable in database
        cyc = db.query(models.EngineEvaluationCycleRecord).filter_by(cycle_id="cycle-db05-rollback").first()
        assert cyc is not None
        assert cyc.run_id == "RUN_DB05"
    finally:
        db.close()


def test_db06_independent_persistence_after_close():
    """DB06: Telemetry remains in DB after caller session is closed."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False)
    s1 = TestingSession()

    engine_observability_service.record_evaluation_cycle(
        s1,
        cycle_id="cycle-db06-durability",
        run_id="RUN_DB06",
        started_at_ms=4000,
        completed_at_ms=4050,
        status="COMPLETED",
        symbol="XAUUSDT",
    )
    s1.close()

    # Fresh session reads record
    s2 = TestingSession()
    try:
        row = s2.query(models.EngineEvaluationCycleRecord).filter_by(cycle_id="cycle-db06-durability").first()
        assert row is not None
        assert row.run_id == "RUN_DB06"
    finally:
        s2.close()
        models.Base.metadata.drop_all(bind=engine)


def test_db07_retry_idempotent():
    """DB07: Retrying same cycle_id returns deduplicated record and does not duplicate rows."""
    db = make_test_db()
    try:
        res1 = engine_observability_service.record_evaluation_cycle(
            db,
            cycle_id="cycle-db07-dedup",
            run_id="RUN_DB07",
            started_at_ms=5000,
            completed_at_ms=5050,
            status="COMPLETED",
            symbol="XAUUSDT",
        )
        assert res1.get("deduplicated") is not True

        res2 = engine_observability_service.record_evaluation_cycle(
            db,
            cycle_id="cycle-db07-dedup",
            run_id="RUN_DB07",
            started_at_ms=5000,
            completed_at_ms=5050,
            status="COMPLETED",
            symbol="XAUUSDT",
        )
        assert res2.get("deduplicated") is True

        count = db.query(models.EngineEvaluationCycleRecord).filter_by(cycle_id="cycle-db07-dedup").count()
        assert count == 1
    finally:
        db.close()


def test_db08_heartbeat_and_cycle_consistency():
    """DB08: Both cycle and heartbeat are updated in database."""
    db = make_test_db()
    try:
        engine_observability_service.record_evaluation_cycle(
            db,
            cycle_id="cycle-db08-atomic",
            run_id="RUN_DB08",
            started_at_ms=6000,
            completed_at_ms=6050,
            status="COMPLETED",
            symbol="XAUUSDT",
        )
        cyc = db.query(models.EngineEvaluationCycleRecord).filter_by(cycle_id="cycle-db08-atomic").first()
        hb = db.query(models.EngineHeartbeatRecord).first()
        assert cyc is not None
        assert hb is not None
        assert hb.last_cycle_id == "cycle-db08-atomic"
    finally:
        db.close()


def test_db09_zero_live_orders():
    """DB09: Zero live exchange orders exist; only paper orders supported."""
    db = make_test_db()
    try:
        # Assert schema does not contain live exchange credentials or real order tables
        assert hasattr(models, "PaperOrder")
        assert not hasattr(models, "LiveExchangeOrder")
    finally:
        db.close()
