"""
backend/tests/test_v17_6_2_cycle_durability.py
AURUM V17.6.2 — Durable Evaluation Cycle Telemetry & Idempotency (FIX-13).

Verifies:
- CY01: Completed evaluation cycle persisted into engine_evaluation_cycles table.
- CY02: Failed evaluation cycle persisted with structured failure_reason.
- CY03: Duplicate cycle_id is rejected/deduplicated (idempotent).
- CY04: Fresh service instance (simulating backend restart) reconstructs latest persisted cycle from DB.
"""
import time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from services.engine_observability_service import (
    EngineObservabilityService,
    engine_observability_service,
)


@pytest.fixture
def cycle_db(tmp_path):
    db_path = tmp_path / "cycle_test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    models.Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def test_cy01_completed_cycle_persisted(cycle_db):
    session = cycle_db
    t_start = 1700000000000
    t_end = t_start + 120

    res = engine_observability_service.record_evaluation_cycle(
        db=session,
        cycle_id="cycle-001",
        engine_instance_id="eng-inst-1",
        symbol="XAUUSDT",
        run_id="run-cg01-1",
        started_at_ms=t_start,
        completed_at_ms=t_end,
        duration_ms=120.0,
        status="COMPLETED",
        decision_status="ARMED",
        armed_order_id="ord-armed-1",
        opportunities_detected=1,
        opportunities_qualified=1,
    )
    session.commit()

    assert res["cycle_id"] == "cycle-001"
    assert res["status"] == "COMPLETED"

    # Query DB directly to verify persistence
    row = session.query(models.EngineEvaluationCycleRecord).filter(
        models.EngineEvaluationCycleRecord.cycle_id == "cycle-001"
    ).first()

    assert row is not None
    assert row.symbol == "XAUUSDT"
    assert row.status == "COMPLETED"
    assert row.decision_status == "ARMED"
    assert row.armed_order_id == "ord-armed-1"
    assert row.duration_ms == 120.0


def test_cy02_failed_cycle_persisted(cycle_db):
    session = cycle_db
    t_start = 1700000001000
    t_end = t_start + 50

    res = engine_observability_service.record_evaluation_cycle(
        db=session,
        cycle_id="cycle-failed-002",
        engine_instance_id="eng-inst-1",
        symbol="XAUUSDT",
        started_at_ms=t_start,
        completed_at_ms=t_end,
        duration_ms=50.0,
        status="FAILED",
        decision_status="FAILED",
        failure_reason="Feed timeout exception",
    )
    session.commit()

    row = session.query(models.EngineEvaluationCycleRecord).filter(
        models.EngineEvaluationCycleRecord.cycle_id == "cycle-failed-002"
    ).first()

    assert row is not None
    assert row.status == "FAILED"
    assert row.failure_reason == "Feed timeout exception"


def test_cy03_duplicate_cycle_id_is_idempotent(cycle_db):
    session = cycle_db
    t_start = 1700000002000

    # First call
    engine_observability_service.record_evaluation_cycle(
        db=session,
        cycle_id="cycle-dup-003",
        engine_instance_id="eng-inst-1",
        symbol="XAUUSDT",
        started_at_ms=t_start,
        completed_at_ms=t_start + 10,
        duration_ms=10.0,
        status="COMPLETED",
    )
    session.commit()

    # Second call with identical cycle_id
    res_dup = engine_observability_service.record_evaluation_cycle(
        db=session,
        cycle_id="cycle-dup-003",
        engine_instance_id="eng-inst-1",
        symbol="XAUUSDT",
        started_at_ms=t_start,
        completed_at_ms=t_start + 10,
        duration_ms=10.0,
        status="COMPLETED",
    )
    session.commit()

    assert res_dup.get("deduplicated") is True

    # Count rows in DB - must be exactly 1!
    cnt = session.query(models.EngineEvaluationCycleRecord).filter(
        models.EngineEvaluationCycleRecord.cycle_id == "cycle-dup-003"
    ).count()
    assert cnt == 1


def test_cy04_restart_readback(cycle_db):
    session = cycle_db
    t_start = 1700000005000

    engine_observability_service.record_evaluation_cycle(
        db=session,
        cycle_id="cycle-restart-004",
        engine_instance_id="eng-inst-old",
        symbol="XAUUSDT",
        started_at_ms=t_start,
        completed_at_ms=t_start + 25,
        duration_ms=25.0,
        status="COMPLETED",
        decision_status="NO_SETUP",
    )
    session.commit()

    # Create fresh service instance with empty in-memory buffer
    fresh_service = EngineObservabilityService()
    assert len(fresh_service._recent_cycles_buffer) == 0

    latest = fresh_service.get_latest_persisted_cycle(session, symbol="XAUUSDT")
    assert latest is not None
    assert latest["cycle_id"] == "cycle-restart-004"
    assert latest["status"] == "COMPLETED"
    assert latest["decision_status"] == "NO_SETUP"
