import os
import tempfile
import pytest
import time
import uuid
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, SystemConfig, PaperOrder, EngineHeartbeatRecord, EngineIncidentRecord
from services.engine_observability_service import EngineObservabilityService


@pytest.fixture
def disk_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession()
    try:
        yield session, TestingSession
    finally:
        session.close()
        engine.dispose()
        if os.path.exists(path):
            os.remove(path)


def test_tx01_borrowed_session_does_not_commit_unrelated_mutations(disk_db):
    """
    FIX-01 Reproduction:
    Caller creates an uncommitted mutation on an unrelated business record (SystemConfig).
    Caller passes db=session to record_engine_heartbeat.
    If record_engine_heartbeat commits the caller's session, rolling back caller session
    will fail to revert the mutation, corrupting transaction ownership.
    """
    session, TestingSession = disk_db
    service = EngineObservabilityService()

    t_now = int(time.time() * 1000)
    cfg = SystemConfig(key="uncommitted_test_key", value="dirty_value", updated_at=t_now)
    session.add(cfg)
    # Note: caller does NOT commit!

    # Telemetry call with borrowed session
    service.record_engine_heartbeat(
        db=session,
        engine_instance_id="inst-test-01",
        symbol="XAUUSDT",
        state="RUNNING",
    )

    # Caller encounters a business error and rolls back
    session.rollback()

    # Open fresh session and verify dirty_value was NOT committed
    fresh_session = TestingSession()
    persisted_cfg = fresh_session.query(SystemConfig).filter(SystemConfig.key == "uncommitted_test_key").first()
    fresh_session.close()

    assert persisted_cfg is None, "CRITICAL ERROR (FIX-01): Telemetry committed caller's uncommitted business mutation!"


def test_tx02_incident_writer_does_not_commit_borrowed_session(disk_db):
    """
    FIX-01 Reproduction for record_engine_incident:
    Caller's borrowed session must not be committed.
    """
    session, TestingSession = disk_db
    service = EngineObservabilityService()

    t_now = int(time.time() * 1000)
    cfg = SystemConfig(key="uncommitted_incident_key", value="dirty_order", updated_at=t_now)
    session.add(cfg)

    # Telemetry incident call
    service.record_engine_incident(
        db=session,
        component="FEED",
        severity="WARNING",
        message="Test warning",
    )

    # Caller rolls back
    session.rollback()

    fresh_session = TestingSession()
    persisted_cfg = fresh_session.query(SystemConfig).filter(SystemConfig.key == "uncommitted_incident_key").first()
    fresh_session.close()

    assert persisted_cfg is None, "CRITICAL ERROR (FIX-01): Incident writer committed caller's uncommitted record!"


def test_tx03_incident_resolver_does_not_commit_borrowed_session(disk_db):
    """
    FIX-01 Reproduction for resolve_active_incidents:
    Caller's borrowed session must not be committed.
    """
    session, TestingSession = disk_db
    service = EngineObservabilityService()

    t_now = int(time.time() * 1000)
    cfg = SystemConfig(key="rollback_key", value="123", updated_at=t_now)
    session.add(cfg)

    service.resolve_active_incidents(
        db=session,
        component="FEED",
    )

    session.rollback()

    fresh_session = TestingSession()
    persisted_cfg = fresh_session.query(SystemConfig).filter(SystemConfig.key == "rollback_key").first()
    fresh_session.close()

    assert persisted_cfg is None, "CRITICAL ERROR (FIX-01): Incident resolver committed caller's uncommitted mutation!"


def test_tx04_owned_session_commits_when_db_is_none(disk_db):
    """
    When db is None, record_engine_heartbeat opens an owned session and commits properly.
    """
    session, TestingSession = disk_db
    service = EngineObservabilityService()

    import database
    orig_session_local = database.SessionLocal
    database.SessionLocal = TestingSession
    try:
        service.record_engine_heartbeat(
            db=None,
            engine_instance_id="inst-owned-01",
            symbol="XAUUSDT",
            state="RUNNING",
        )
        fresh_session = TestingSession()
        hb = fresh_session.query(EngineHeartbeatRecord).filter(
            EngineHeartbeatRecord.engine_instance_id == "inst-owned-01"
        ).first()
        fresh_session.close()
        assert hb is not None
        assert hb.state == "RUNNING"
    finally:
        database.SessionLocal = orig_session_local
