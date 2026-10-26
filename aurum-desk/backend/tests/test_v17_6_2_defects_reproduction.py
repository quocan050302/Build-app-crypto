import os
import tempfile
import pytest
import time
import uuid
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import (
    Base, PaperOrder, PaperObservationInterval, OpportunityDecisionEvent,
    EngineIncidentRecord, EngineHeartbeatRecord, PaperResearchRunRecord, PaperResearchOrderLink
)
from services.engine_observability_service import EngineObservabilityService


@pytest.fixture
def test_db():
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


def test_repro_fix06_dead_engine_on_weekend_must_report_stalled():
    """
    FIX-06: Dead engine on Saturday must report STALLED, not False (MARKET_WEEKEND_BREAK).
    """
    service = EngineObservabilityService()
    # Saturday 2026-10-24 12:00:00 UTC = Saturday 08:00:00 NY
    saturday_ms = 1792843200000

    dead_hb = EngineHeartbeatRecord(
        engine_instance_id="dead-worker",
        symbol="XAUUSDT",
        last_process_heartbeat_at_ms=saturday_ms - (3600 * 1000),  # 1 hour ago
        cadence_sec=15,
        updated_at_ms=saturday_ms - (3600 * 1000),
    )

    is_stalled, reason = service.detect_stalled_engine(
        heartbeat_record=dead_hb,
        now_ms=saturday_ms,
    )
    # On current code, this returns (False, "MARKET_WEEKEND_BREAK")
    assert is_stalled is True, f"Dead engine was hidden by weekend break! Got reason={reason}"
    assert "STALLED" in reason or "HEARTBEAT" in reason


def test_repro_fix08_armed_opportunity_must_not_appear_in_waiting_setups(test_db):
    """
    FIX-08: An opportunity that was WAITING at 10:00 and then ARMED at 10:05
    must NOT appear in waiting_setups list!
    """
    session, _ = test_db
    service = EngineObservabilityService()

    run_id = "run-test-waiting"
    opp_id = "opp-123"
    t_start = 1000000
    t_now = 1010000

    # Event 1: WAITING at 10:00
    ev1 = OpportunityDecisionEvent(
        event_id="ev-1",
        run_id=run_id,
        opportunity_id=opp_id,
        model_id="M1_SCALP",
        direction="BUY",
        stage="WAITING",
        primary_reason_code="WAITING_SWEEP",
        occurred_at_ms=1001000,
        created_at_ms=1001000,
    )
    # Event 2: Later transitioned to ARMED at 10:05
    ev2 = OpportunityDecisionEvent(
        event_id="ev-2",
        run_id=run_id,
        opportunity_id=opp_id,
        model_id="M1_SCALP",
        direction="BUY",
        stage="ARMED",
        primary_reason_code="ARMED_SUCCESS",
        occurred_at_ms=1005000,
        created_at_ms=1005000,
    )
    session.add(ev1)
    session.add(ev2)
    session.commit()

    snapshot = service.build_observation_dashboard_snapshot(
        db=session,
        symbol="XAUUSDT",
        run_id=run_id,
        start_ms=t_start,
        end_ms=t_now,
        now_ms=t_now,
    )

    waiting_opp_ids = [w["opportunity_id"] for w in snapshot.get("waiting_setups", [])]
    assert opp_id not in waiting_opp_ids, f"Opportunity {opp_id} transitioned to ARMED but still appears in waiting_setups!"


def test_repro_fix10_active_incidents_must_be_read_from_database(test_db):
    """
    FIX-10: On fresh service instance (simulating restart), active incidents in DB must be returned.
    """
    session, _ = test_db
    t_now = int(time.time() * 1000)
    t_start = t_now - 3600 * 1000

    # Insert an ACTIVE incident into DB
    inc = EngineIncidentRecord(
        id="inc-persisted-1",
        severity="WARNING",
        component="FEED",
        reason_code="FEED_DISCONNECTED",
        message="Websocket disconnected",
        status="ACTIVE",
        created_at_ms=t_now - 5000,
    )
    session.add(inc)
    session.commit()

    # Create fresh service instance with empty in-memory cache
    fresh_service = EngineObservabilityService()
    assert len(fresh_service._active_incidents) == 0

    snapshot = fresh_service.build_observation_dashboard_snapshot(
        db=session,
        symbol="XAUUSDT",
        start_ms=t_start,
        end_ms=t_now,
        now_ms=t_now,
    )

    incident_ids = [i["id"] for i in snapshot.get("incidents", [])]
    assert "inc-persisted-1" in incident_ids, "Active incident persisted in DB was ignored because memory cache was empty!"
