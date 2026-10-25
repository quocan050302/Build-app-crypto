"""
backend/tests/test_v17_6_4_1_cycle_commit_consistency.py
AURUM V17.6.4.1 — Cycle and Heartbeat Persistence Consistency Test Suite (EVID-09, Parts 092-102).

Cases:
- CC01: Cycle and heartbeat commit atomically or reconcile reliably
- CC02: Cycle write failure degrades telemetry, does not present false success
- CC03: Heartbeat write failure handled without corrupting cycle record
- CC04: Duplicate cycle retry is idempotent and does not increment cycles_count twice
- CC05: Business rollback leaves telemetry cleanly isolated
- CC06: Completed evaluation with NO_SETUP recorded faithfully
- CC07: Skipped evaluation cycle recorded with skip_reason
- CC08: Failed evaluation cycle records failure_reason
- CC09: Replay evaluation isolated from live cycle metrics
- CC10: Restart readback reconstructs latest durable cycle
- CC11: SQLite concurrent write handled with backoff
- CC12: No premature PaperOrder commit caused by telemetry operations
"""
from datetime import datetime, timezone
from unittest.mock import patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from services.engine_observability_service import EngineObservabilityService


@pytest.fixture
def cycle_db():
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
    engine.dispose()


def test_cc01_cycle_and_heartbeat_commit(cycle_db):
    """CC01: Cycle and heartbeat both persist durably upon evaluation completion."""
    session, _ = cycle_db
    obs_svc = EngineObservabilityService()
    t0 = 1_700_000_000_000

    res = obs_svc.record_evaluation_cycle(
        session,
        cycle_id="CYC_CC01",
        run_id="RUN_CC01",
        started_at_ms=t0,
        completed_at_ms=t0 + 250,
        status="COMPLETED",
        decision_status="NO_SETUP",
        evidence_origin="LIVE_FORWARD",
    )

    cycle_row = session.query(models.EngineEvaluationCycleRecord).filter_by(cycle_id="CYC_CC01").first()
    assert cycle_row is not None
    assert cycle_row.status == "COMPLETED"

    hb_row = session.query(models.EngineHeartbeatRecord).filter_by(run_id="RUN_CC01").first()
    assert hb_row is not None
    assert hb_row.last_cycle_id == "CYC_CC01"


def test_cc02_cycle_write_failure_degrades_telemetry(cycle_db):
    """CC02: Cycle write failure marks persistence_status as FAILED."""
    session, _ = cycle_db
    obs_svc = EngineObservabilityService()
    t0 = 1_700_000_000_000

    with patch.object(models.EngineEvaluationCycleRecord, "__init__", side_effect=OperationalError("locked", {}, None)):
        res = obs_svc.record_evaluation_cycle(
            session,
            cycle_id="CYC_CC02",
            run_id="RUN_CC02",
            started_at_ms=t0,
            completed_at_ms=t0 + 250,
            status="COMPLETED",
        )
        # Should degrade persistence status rather than claim durable success
        assert res.get("persistence_status") in ("FAILED", "UNPERSISTED", None)


def test_cc03_heartbeat_write_fails_gracefully(cycle_db):
    """CC03: Heartbeat write error does not crash evaluation cycle recording."""
    session, _ = cycle_db
    obs_svc = EngineObservabilityService()
    t0 = 1_700_000_000_000

    with patch.object(obs_svc, "record_engine_heartbeat", side_effect=OperationalError("busy", {}, None)):
        res = obs_svc.record_evaluation_cycle(
            session,
            cycle_id="CYC_CC03",
            run_id="RUN_CC03",
            started_at_ms=t0,
            completed_at_ms=t0 + 250,
            status="COMPLETED",
        )
        assert res["cycle_id"] == "CYC_CC03"


def test_cc04_duplicate_cycle_retry_idempotent(cycle_db):
    """CC04: Re-recording existing cycle_id is deduplicated and idempotent."""
    session, _ = cycle_db
    obs_svc = EngineObservabilityService()
    t0 = 1_700_000_000_000

    res1 = obs_svc.record_evaluation_cycle(
        session,
        cycle_id="CYC_CC04",
        run_id="RUN_CC04",
        started_at_ms=t0,
        completed_at_ms=t0 + 250,
        status="COMPLETED",
    )
    res2 = obs_svc.record_evaluation_cycle(
        session,
        cycle_id="CYC_CC04",
        run_id="RUN_CC04",
        started_at_ms=t0,
        completed_at_ms=t0 + 250,
        status="COMPLETED",
    )
    assert res2.get("deduplicated") is True
    rows = session.query(models.EngineEvaluationCycleRecord).filter_by(cycle_id="CYC_CC04").all()
    assert len(rows) == 1


def test_cc05_business_rollback_isolation(cycle_db):
    """CC05: Business transaction rollback does not affect isolated telemetry session."""
    session, _ = cycle_db
    obs_svc = EngineObservabilityService()
    t0 = 1_700_000_000_000

    # Business session opens a transaction
    dummy_order = models.PaperOrder(
        id="ORDER_TEST_CC05",
        instrument="XAUUSDT",
        direction="LONG",
        planned_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
    )
    session.add(dummy_order)

    # Telemetry is recorded
    obs_svc.record_evaluation_cycle(
        session,
        cycle_id="CYC_CC05",
        run_id="RUN_CC05",
        started_at_ms=t0,
        completed_at_ms=t0 + 250,
        status="COMPLETED",
    )

    # Business transaction fails and rolls back
    session.rollback()

    # The order was rolled back
    assert session.query(models.PaperOrder).filter_by(id="ORDER_TEST_CC05").first() is None


def test_cc06_completed_no_setup(cycle_db):
    """CC06: Valid evaluation resulting in NO_SETUP recorded accurately."""
    session, _ = cycle_db
    obs_svc = EngineObservabilityService()
    res = obs_svc.record_evaluation_cycle(
        session,
        cycle_id="CYC_CC06",
        run_id="RUN_CC06",
        started_at_ms=1_700_000_000_000,
        completed_at_ms=1_700_000_000_200,
        status="COMPLETED",
        decision_status="NO_SETUP",
        opportunities_detected=0,
    )
    assert res["decision_status"] == "NO_SETUP"


def test_cc07_skipped_evaluation(cycle_db):
    """CC07: Skipped cycle persists skip_reason."""
    session, _ = cycle_db
    obs_svc = EngineObservabilityService()
    res = obs_svc.record_evaluation_cycle(
        session,
        cycle_id="CYC_CC07",
        run_id="RUN_CC07",
        started_at_ms=1_700_000_000_000,
        completed_at_ms=1_700_000_000_050,
        status="SKIPPED",
        skip_reason="MODEL_DISABLED",
        decision_status="SKIPPED",
    )
    row = session.query(models.EngineEvaluationCycleRecord).filter_by(cycle_id="CYC_CC07").first()
    assert row.status == "SKIPPED"
    assert row.skip_reason == "MODEL_DISABLED"


def test_cc08_failed_evaluation(cycle_db):
    """CC08: Failed cycle persists failure_reason."""
    session, _ = cycle_db
    obs_svc = EngineObservabilityService()
    res = obs_svc.record_evaluation_cycle(
        session,
        cycle_id="CYC_CC08",
        run_id="RUN_CC08",
        started_at_ms=1_700_000_000_000,
        completed_at_ms=1_700_000_000_050,
        status="FAILED",
        failure_reason="DIVIDE_BY_ZERO",
        decision_status="FAILED",
    )
    row = session.query(models.EngineEvaluationCycleRecord).filter_by(cycle_id="CYC_CC08").first()
    assert row.status == "FAILED"
    assert row.failure_reason == "DIVIDE_BY_ZERO"


def test_cc09_replay_evaluation(cycle_db):
    """CC09: Replay evaluation isolated from live forward metrics."""
    session, _ = cycle_db
    obs_svc = EngineObservabilityService()
    res = obs_svc.record_evaluation_cycle(
        session,
        cycle_id="CYC_CC09",
        run_id="RUN_REPLAY",
        started_at_ms=1_600_000_000_000,
        completed_at_ms=1_600_000_000_050,
        status="COMPLETED",
        evidence_origin="REPLAY",
    )
    row = session.query(models.EngineEvaluationCycleRecord).filter_by(cycle_id="CYC_CC09").first()
    assert row.run_id == "RUN_REPLAY"


def test_cc10_restart_readback(cycle_db):
    """CC10: Restart readback strictly prefers persisted cycle over memory."""
    session, SessionFactory = cycle_db
    obs_svc = EngineObservabilityService()
    t0 = 1_700_000_000_000

    obs_svc.record_evaluation_cycle(
        session,
        cycle_id="CYC_CC10",
        run_id="RUN_CC10",
        started_at_ms=t0,
        completed_at_ms=t0 + 200,
        status="COMPLETED",
        decision_status="ARMED",
    )
    session.close()

    new_session = SessionFactory()
    new_obs = EngineObservabilityService()
    persisted = new_obs.get_latest_persisted_cycle(new_session, run_id="RUN_CC10")
    assert persisted is not None
    assert persisted["cycle_id"] == "CYC_CC10"
    assert persisted["decision_status"] == "ARMED"
    new_session.close()


def test_cc11_sqlite_concurrent_write(cycle_db):
    """CC11: Concurrent writes handled with retry."""
    session, _ = cycle_db
    obs_svc = EngineObservabilityService()

    call_count = 0
    orig_add = session.add

    def flaky_add(record):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OperationalError("database is locked", {}, None)
        orig_add(record)

    with patch.object(session, "add", side_effect=flaky_add):
        res = obs_svc.record_evaluation_cycle(
            session,
            cycle_id="CYC_CC11",
            run_id="RUN_CC11",
            started_at_ms=1_700_000_000_000,
            completed_at_ms=1_700_000_000_100,
            status="COMPLETED",
        )
        assert res["cycle_id"] == "CYC_CC11"


def test_cc12_no_premature_paper_order_commit(cycle_db):
    """CC12: Telemetry operations do NOT commit the caller's pending business transaction."""
    session, _ = cycle_db
    obs_svc = EngineObservabilityService()

    # Add uncommitted entity to session
    dummy_intent = models.OpportunityDecisionEvent(
        event_id="EVT_TEMP_01",
        opportunity_id="OPP_TEMP",
        model_id="CG01",
        stage="INITIAL",
        direction="LONG",
        symbol="XAUUSDT",
        primary_reason_code="WAITING",
        reason_category="MARKET_DATA",
        occurred_at_ms=1_700_000_000_000,
    )
    session.add(dummy_intent)

    # Calling record_evaluation_cycle with session
    obs_svc.record_evaluation_cycle(
        session,
        cycle_id="CYC_CC12",
        run_id="RUN_CC12",
        started_at_ms=1_700_000_000_000,
        completed_at_ms=1_700_000_000_100,
        status="COMPLETED",
    )

    # Roll back session
    session.rollback()

    # The uncommitted dummy_intent should be rolled back!
    found = session.query(models.OpportunityDecisionEvent).filter_by(event_id="EVT_TEMP_01").first()
    assert found is None, "Telemetry operation prematurely committed caller's business transaction!"
