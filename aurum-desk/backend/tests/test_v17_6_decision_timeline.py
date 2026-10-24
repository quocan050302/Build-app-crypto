"""
backend/tests/test_v17_6_decision_timeline.py
AURUM V17.6 — Decision Intelligence & Opportunity Timeline Test Suite (Phase E, Parts 041-055).

Verifies:
1. Decision event recording in database with structured schema.
2. Suppression of repetitive/duplicate events within 60 seconds for identical stage and reason.
3. Accurate categorization and localization of primary decision reasons.
4. Waiting conditions inspector breakdown into structural, FVG, and Net R:R prerequisites.
5. Deterministic cursor-based pagination of recent decision history.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import models
from services.engine_observability_service import (
    engine_observability_service,
    categorize_reason_code,
    get_primary_decision_reason,
    inspect_waiting_conditions,
)


@pytest.fixture
def decision_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False)
    session = SessionFactory()
    yield session
    session.close()


def test_d01_record_decision_event(decision_db: Session):
    event = engine_observability_service.record_decision_event(
        decision_db,
        run_id="run-test-001",
        cycle_id="cycle-test-001",
        opportunity_id="opp-test-101",
        model_id="STANDARD_SMC",
        direction="LONG",
        stage="WAITING",
        reason_codes=["SWEEP_WAITING", "MSS_PENDING"],
        evidence_ids=["ev-1", "ev-2"],
        occurred_at_ms=1700000000000,
        payload={"zone_low": 2645.0, "zone_high": 2648.0},
    )

    assert event is not None
    assert event["event_id"].startswith("ev-")
    assert event["opportunity_id"] == "opp-test-101"
    assert event["stage"] == "WAITING"

    # Verify query
    persisted = decision_db.query(models.OpportunityDecisionEvent).filter_by(event_id=event["event_id"]).first()
    assert persisted is not None
    assert persisted.direction == "LONG"
    assert persisted.reason_category == "STRUCTURE"


def test_d02_suppress_duplicate_events_within_window(decision_db: Session):
    # First record
    ev1 = engine_observability_service.record_decision_event(
        decision_db,
        run_id="run-test-002",
        cycle_id="cycle-test-002-1",
        opportunity_id="opp-dedup-001",
        model_id="STANDARD_SMC",
        direction="SHORT",
        stage="WAITING",
        reason_codes=["MSS_PENDING"],
        occurred_at_ms=1700000000000,
    )
    assert ev1 is not None

    # Second record with identical stage & reason 5 seconds later
    ev2 = engine_observability_service.record_decision_event(
        decision_db,
        run_id="run-test-002",
        cycle_id="cycle-test-002-2",
        opportunity_id="opp-dedup-001",
        model_id="STANDARD_SMC",
        direction="SHORT",
        stage="WAITING",
        reason_codes=["MSS_PENDING"],
        occurred_at_ms=1700000005000,
    )
    # Should be suppressed (returns existing)
    assert ev2["event_id"] == ev1["event_id"]

    # Count records in DB
    records = decision_db.query(models.OpportunityDecisionEvent).filter_by(opportunity_id="opp-dedup-001").all()
    assert len(records) == 1

    # Third record 65 seconds later -> allowed
    ev3 = engine_observability_service.record_decision_event(
        decision_db,
        run_id="run-test-002",
        cycle_id="cycle-test-002-3",
        opportunity_id="opp-dedup-001",
        model_id="STANDARD_SMC",
        direction="SHORT",
        stage="WAITING",
        reason_codes=["MSS_PENDING"],
        occurred_at_ms=1700000070000,
    )
    assert ev3["event_id"] != ev1["event_id"]
    records_after = decision_db.query(models.OpportunityDecisionEvent).filter_by(opportunity_id="opp-dedup-001").all()
    assert len(records_after) == 2


def test_d03_reason_categorization_and_primary_reason():
    assert categorize_reason_code("NET_RR_BELOW_THRESHOLD") == "NET_RR"
    assert categorize_reason_code("LIQUIDITY_NOT_SWEPT") == "STRUCTURE"
    assert categorize_reason_code("DAILY_LOSS_LIMIT_REACHED") == "RISK"
    assert categorize_reason_code("STALE_MARKET_FEED") == "MARKET_DATA"
    assert categorize_reason_code("NEWS_BLACKOUT_WINDOW") == "NEWS"
    assert categorize_reason_code("OUTSIDE_ELIGIBLE_SESSION") == "SESSION"
    assert categorize_reason_code("INSUFFICIENT_MARGIN") == "CAPITAL"
    assert categorize_reason_code("EXECUTION_TIMEOUT") == "EXECUTION"
    assert categorize_reason_code("MODEL_FEATURE_DISABLED") == "CONFIGURATION"

    primary = get_primary_decision_reason(["NET_RR_BELOW_THRESHOLD", "SWEEP_WAITING"], "BLOCKED")
    assert primary["primary_reason_code"] == "NET_RR_BELOW_THRESHOLD"
    assert primary["category"] == "NET_RR"
    assert "Net R:R" in primary["short_message_vi"]


def test_d04_waiting_conditions_inspector():
    payload = {
        "sweep_confirmed": True,
        "mss_confirmed": False,
        "fvg_confirmed": False,
        "estimated_net_rr": 1.5,
        "min_net_rr": 2.0,
    }
    conditions = inspect_waiting_conditions("WAITING", payload=payload)
    assert len(conditions) == 5

    cond_dict = {c["id"]: c for c in conditions}
    assert cond_dict["LIQUIDITY_SWEEP"]["status"] == "FULFILLED"
    assert cond_dict["MSS_CONFIRMATION"]["status"] == "WAITING"
    assert cond_dict["FVG_IMBALANCE"]["status"] == "WAITING"
    assert cond_dict["NET_RR_CRITERIA"]["status"] == "FAILED"
    assert cond_dict["SESSION_WINDOW"]["status"] == "FULFILLED"


def test_d05_list_recent_decisions_pagination(decision_db: Session):
    for i in range(15):
        engine_observability_service.record_decision_event(
            decision_db,
            run_id="run-page-01",
            cycle_id=f"cycle-p-{i}",
            opportunity_id=f"opp-p-{i}",
            model_id="STANDARD_SMC",
            direction="LONG" if i % 2 == 0 else "SHORT",
            stage="QUALIFIED" if i % 3 == 0 else "BLOCKED",
            reason_codes=[f"REASON_{i}"],
            occurred_at_ms=1700000000000 + i * 1000,
        )

    # Page 1 (limit 10)
    page1 = engine_observability_service.list_recent_decisions(
        decision_db,
        limit=10,
        cursor=None,
    )
    assert len(page1["items"]) == 10
    assert page1["has_more"] is True
    assert page1["next_cursor"] is not None

    # Page 2
    page2 = engine_observability_service.list_recent_decisions(
        decision_db,
        limit=10,
        cursor=page1["next_cursor"],
    )
    assert len(page2["items"]) == 5
    assert page2["has_more"] is False
