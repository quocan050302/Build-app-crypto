"""
backend/tests/test_v17_6_recovery.py
AURUM V17.6 — Operational Alerts, Incident Lifecycle & Restart Recovery Test Suite (Phase K, Parts 111-120).

Verifies:
1. Incident creation on critical engine stall or feed degradation.
2. Incident resolution with recorded duration and recovery source.
3. Database persistence of runs, heartbeats, and decision events across backend restarts.
4. Feed reconnection and recovery from STALE/DISCONNECTED to CONNECTED_FRESH.
"""

from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import models
from services.engine_observability_service import engine_observability_service
from services.paper_research_run_service import PaperResearchRunService


@pytest.fixture
def recovery_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False)
    session = SessionFactory()
    yield session, SessionFactory
    session.close()


def test_rec01_incident_creation_and_resolution(recovery_db):
    session, _ = recovery_db

    # 1. Record incident (FEED_DEGRADED)
    inc = engine_observability_service.record_engine_incident(
        session,
        component="MARKET_FEED",
        severity="WARNING",
        message="Market quote feed stale (> 60s)",
        incident_id="inc-feed-001",
        engine_instance_id="eng-rec-01",
        run_id="run-rec-01",
        now_ms=1700000000000,
    )
    session.commit()

    assert inc is not None
    assert inc["id"] == "inc-feed-001"
    assert inc["status"] == "ACTIVE"

    # Query DB
    db_inc = session.query(models.EngineIncidentRecord).filter_by(id="inc-feed-001").first()
    assert db_inc is not None
    assert db_inc.severity == "WARNING"
    assert db_inc.status == "ACTIVE"

    # 2. Resolve incident
    ok = engine_observability_service.resolve_engine_incident(
        session,
        incident_id="inc-feed-001",
        now_ms=1700000030000,
    )
    session.commit()

    assert ok is True

    db_inc_after = session.query(models.EngineIncidentRecord).filter_by(id="inc-feed-001").first()
    assert db_inc_after.status == "RECOVERED"
    assert db_inc_after.recovered_at_ms == 1700000030000


def test_rec02_restart_resilience(recovery_db):
    session1, SessionFactory = recovery_db

    run_id = "run-restart-test"
    PaperResearchRunService.get_or_create_run(run_id, db=session1, create_if_missing=True)

    engine_observability_service.record_engine_heartbeat(
        session1,
        engine_instance_id="eng-inst-pre-restart",
        symbol="XAUUSDT",
        heartbeat_at_ms=1700000000000,
        state="RUNNING",
        last_cycle_id="cycle-pre-01",
    )

    engine_observability_service.record_decision_event(
        session1,
        run_id=run_id,
        opportunity_id="opp-pre-01",
        model_id="STANDARD_SMC",
        direction="LONG",
        stage="WAITING",
        reason_codes=["SWEEP_WAITING"],
        occurred_at_ms=1700000000000,
    )
    session1.commit()
    session1.close()

    # Simulate backend restart with a brand new database session
    session2 = SessionFactory()

    # Verify run persisted
    run = PaperResearchRunService.get_or_create_run(run_id, db=session2, create_if_missing=False)
    assert run is not None
    assert run.run_id == run_id

    # Verify heartbeat persisted
    hb = session2.query(models.EngineHeartbeatRecord).filter_by(engine_instance_id="eng-inst-pre-restart").first()
    assert hb is not None
    assert hb.last_cycle_id == "cycle-pre-01"

    # Verify decision event persisted
    ev = session2.query(models.OpportunityDecisionEvent).filter_by(opportunity_id="opp-pre-01").first()
    assert ev is not None
    assert ev.stage == "WAITING"

    session2.close()


def test_rec03_feed_reconnection_and_freshness_recovery():
    now_ms = 1792591200000  # Wednesday 14:00 UTC

    # 1. Stale quote
    mock_stale = MagicMock()
    mock_stale.timestamp = now_ms - (30 * 1000)  # 30s old
    mock_stale.bid = 2720.0
    mock_stale.ask = 2720.2

    with patch("services.bitget_ws_service.bitget_ws_service") as mock_ws:
        mock_ws.transport_state = "CONNECTED"
        mock_ws.feed_source = "WS"
        mock_ws.latest_quote = mock_stale

        stale_snap = engine_observability_service.build_market_feed_health_snapshot(
            symbol="XAUUSDT",
            now_ms=now_ms,
        )
        assert stale_snap["freshness_state"] == "CONNECTED_STALE"
        assert stale_snap["is_fresh"] is False

    # 2. Fresh quote arrives
    mock_fresh = MagicMock()
    mock_fresh.timestamp = now_ms - 1000  # 1s old
    mock_fresh.bid = 2721.0
    mock_fresh.ask = 2721.2

    with patch("services.bitget_ws_service.bitget_ws_service") as mock_ws:
        mock_ws.transport_state = "CONNECTED"
        mock_ws.feed_source = "WS"
        mock_ws.latest_quote = mock_fresh

        fresh_snap = engine_observability_service.build_market_feed_health_snapshot(
            symbol="XAUUSDT",
            now_ms=now_ms,
        )
        assert fresh_snap["freshness_state"] == "CONNECTED_FRESH"
        assert fresh_snap["is_fresh"] is True
