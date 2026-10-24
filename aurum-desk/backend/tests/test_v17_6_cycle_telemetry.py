"""
backend/tests/test_v17_6_cycle_telemetry.py
AURUM V17.6 — Evaluation Cycle Telemetry Test Suite (Phase D, Parts 031-040).

Verifies:
1. Successful cycle without setup records COMPLETED, NO_SETUP, duration_ms >= 0.
2. Skipped cycle when AUTO_PAPER is disabled records SKIPPED with reason.
3. Skipped cycle when quote is missing/stale records SKIPPED with NO_VALID_QUOTE.
4. Cycle failure recording produces FAILED cycle telemetry and diagnostic.
5. Heartbeat is updated during evaluation cycle.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import models
from services.engine_observability_service import engine_observability_service
from services.scalp_m1_types import M1ScalpConfig, ScalpMode
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.quote_validator import CanonicalQuote


@pytest.fixture
def telemetry_db():
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


def test_e01_cycle_telemetry_no_setup(telemetry_db: Session):
    config = M1ScalpConfig(
        scalp_m1_enabled=True,
        scalp_m1_mode=ScalpMode.PAPER.value,
    )
    orchestrator = ScalpWatcherOrchestrator()

    quote = CanonicalQuote(
        symbol="XAUUSDT",
        last=2650.1,
        bid=2650.0,
        ask=2650.2,
        received_at_ms=1700000000000,
        source="CANONICAL",
    )

    res = orchestrator.evaluate_cycle(
        telemetry_db,
        symbol="XAUUSDT",
        now_ms=1700000000000,
        quote=quote,
        candles_1m=[],
        candles_5m=[],
        config=config,
    )

    assert "cycle_id" in res
    assert res.get("decision_status") == "NO_SETUP"

    recent = engine_observability_service.get_recent_cycles(limit=5)
    assert len(recent) >= 1
    latest = recent[0]
    assert latest["cycle_id"] == res["cycle_id"]
    assert latest["status"] == "COMPLETED"
    assert latest["decision_status"] == "NO_SETUP"
    assert latest["opportunities_detected"] == 0
    assert latest["duration_ms"] >= 0


def test_e02_cycle_telemetry_skipped_disabled(telemetry_db: Session):
    config = M1ScalpConfig(
        scalp_m1_enabled=False,  # Disabled
        scalp_m1_mode=ScalpMode.PAPER.value,
    )
    orchestrator = ScalpWatcherOrchestrator()

    res = orchestrator.evaluate_cycle(
        telemetry_db,
        symbol="XAUUSDT",
        now_ms=1700000000000,
        quote=None,
        candles_1m=[],
        candles_5m=[],
        config=config,
    )

    assert res.get("decision_status") == "SKIPPED"
    assert "cycle_id" in res

    recent = engine_observability_service.get_recent_cycles(limit=5)
    assert len(recent) >= 1
    latest = recent[0]
    assert latest["status"] == "SKIPPED"
    assert latest["skip_reason"] == "SCALP_AUTO_PAPER_DISABLED"


def test_e03_cycle_telemetry_skipped_no_quote(telemetry_db: Session):
    config = M1ScalpConfig(
        scalp_m1_enabled=True,
        scalp_m1_mode=ScalpMode.PAPER.value,
    )
    orchestrator = ScalpWatcherOrchestrator()

    res = orchestrator.evaluate_cycle(
        telemetry_db,
        symbol="XAUUSDT",
        now_ms=1700000000000,
        quote=None,  # Missing quote
        candles_1m=[],
        candles_5m=[],
        config=config,
    )

    assert res.get("decision_status") == "SKIPPED"
    recent = engine_observability_service.get_recent_cycles(limit=5)
    assert len(recent) >= 1
    assert recent[0]["status"] == "SKIPPED"
    assert recent[0]["skip_reason"] == "NO_VALID_QUOTE"


def test_e04_cycle_telemetry_failure_recording(telemetry_db: Session):
    engine_observability_service.record_evaluation_cycle(
        telemetry_db,
        cycle_id="cycle-err-101",
        engine_instance_id="engine-test-1",
        symbol="XAUUSDT",
        started_at_ms=1700000000000,
        completed_at_ms=1700000000150,
        status="FAILED",
        duration_ms=150.0,
        decision_status="ERROR",
        failure_reason="MOCK_DATABASE_DISCONNECTED",
        models_evaluated=["M1_SCALP_SMC"],
        opportunities_detected=0,
        opportunities_qualified=0,
        opportunities_blocked=0,
    )

    diagnostic = engine_observability_service.get_latest_cycle_diagnostic()
    assert diagnostic is not None
    assert diagnostic["cycle_id"] == "cycle-err-101"
    assert diagnostic["status"] == "FAILED"
    assert diagnostic["failure_reason"] == "MOCK_DATABASE_DISCONNECTED"


def test_e05_engine_heartbeat_updated_on_cycle(telemetry_db: Session):
    config = M1ScalpConfig(
        scalp_m1_enabled=True,
        scalp_m1_mode=ScalpMode.PAPER.value,
    )
    orchestrator = ScalpWatcherOrchestrator()

    quote = CanonicalQuote(
        symbol="XAUUSDT",
        last=2650.1,
        bid=2650.0,
        ask=2650.2,
        received_at_ms=1700000000000,
        source="CANONICAL",
    )

    orchestrator.evaluate_cycle(
        telemetry_db,
        symbol="XAUUSDT",
        now_ms=1700000000000,
        quote=quote,
        candles_1m=[],
        candles_5m=[],
        config=config,
    )

    hb = telemetry_db.query(models.EngineHeartbeatRecord).filter_by(symbol="XAUUSDT").first()
    assert hb is not None
    assert hb.last_evaluation_completed_at_ms >= 1700000000000
    assert hb.last_evaluation_completed_at_ms <= 1700000005000
    assert hb.last_successful_evaluation_at_ms >= 1700000000000
