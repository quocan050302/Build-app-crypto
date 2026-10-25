"""
backend/tests/test_v17_6_4_cycle_run_identity.py
Tests for Evaluation Cycle Run ID Propagation & Isolation (Phase D, RID01-RID12).
"""
import time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from services.engine_observability_service import engine_observability_service
from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator
from services.paper_research_run_service import (
    PaperResearchRunService,
    start_observation_run,
)


@pytest.fixture
def isolated_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False)
    session = TestingSession()
    PaperResearchRunService._active_runs.clear()
    try:
        yield session
    finally:
        session.close()
        models.Base.metadata.drop_all(bind=engine)
        PaperResearchRunService._active_runs.clear()


class MockResearchContext:
    def __init__(self, run_id: str):
        self.run_id = run_id


def test_rid01_scheduler_run_a(isolated_db):
    """RID01: Scheduler research context provides explicit run_A which is attached to cycle."""
    ctx = MockResearchContext("RUN_SCHED_01")
    res = scalp_watcher_orchestrator.evaluate_cycle(
        isolated_db,
        symbol="XAUUSDT",
        research_context=ctx,
        caller_identity="SCHEDULER",
        auto_enabled=False,
    )
    cycle_id = res["cycle_id"]
    row = isolated_db.query(models.EngineEvaluationCycleRecord).filter(
        models.EngineEvaluationCycleRecord.cycle_id == cycle_id
    ).first()
    assert row is not None
    assert row.run_id == "RUN_SCHED_01"


def test_rid02_watcher_resolves_active_run_a(isolated_db):
    """RID02: Watcher with no explicit context resolves active run_A in DB."""
    run = start_observation_run(db=isolated_db, symbol="XAUUSDT", scope="CG01")
    res = scalp_watcher_orchestrator.evaluate_cycle(
        isolated_db,
        symbol="XAUUSDT",
        caller_identity="WATCHER",
        auto_enabled=False,
    )
    cycle_id = res["cycle_id"]
    row = isolated_db.query(models.EngineEvaluationCycleRecord).filter(
        models.EngineEvaluationCycleRecord.cycle_id == cycle_id
    ).first()
    assert row is not None
    assert row.run_id == run.run_id


def test_rid03_watcher_no_active_run(isolated_db):
    """RID03: Watcher with no active run produces cycle with run_id = None."""
    res = scalp_watcher_orchestrator.evaluate_cycle(
        isolated_db,
        symbol="XAUUSDT",
        caller_identity="WATCHER",
        auto_enabled=False,
    )
    cycle_id = res["cycle_id"]
    row = isolated_db.query(models.EngineEvaluationCycleRecord).filter(
        models.EngineEvaluationCycleRecord.cycle_id == cycle_id
    ).first()
    assert row is not None
    assert row.run_id is None


def test_rid04_explicit_run_b_context(isolated_db):
    """RID04: Explicit context for run_B takes precedence."""
    ctx = MockResearchContext("RUN_B_EXPLICIT")
    res = scalp_watcher_orchestrator.evaluate_cycle(
        isolated_db,
        symbol="XAUUSDT",
        research_context=ctx,
        caller_identity="SCHEDULER",
        auto_enabled=False,
    )
    cycle_id = res["cycle_id"]
    row = isolated_db.query(models.EngineEvaluationCycleRecord).filter(
        models.EngineEvaluationCycleRecord.cycle_id == cycle_id
    ).first()
    assert row is not None
    assert row.run_id == "RUN_B_EXPLICIT"


def test_rid05_wrong_symbol_not_resolved(isolated_db):
    """RID05: Active run for EURUSD is not attached to XAUUSDT evaluation."""
    start_observation_run(db=isolated_db, symbol="EURUSD", scope="CG01")
    res = scalp_watcher_orchestrator.evaluate_cycle(
        isolated_db,
        symbol="XAUUSDT",
        caller_identity="WATCHER",
        auto_enabled=False,
    )
    cycle_id = res["cycle_id"]
    row = isolated_db.query(models.EngineEvaluationCycleRecord).filter(
        models.EngineEvaluationCycleRecord.cycle_id == cycle_id
    ).first()
    assert row is not None
    assert row.run_id is None


def test_rid06_invalid_scope_not_resolved(isolated_db):
    """RID06: Active run with different scope is not attached to default scope."""
    start_observation_run(db=isolated_db, symbol="XAUUSDT", scope="SWING01")
    res = scalp_watcher_orchestrator.evaluate_cycle(
        isolated_db,
        symbol="XAUUSDT",
        caller_identity="WATCHER",
        auto_enabled=False,
        execution_scope="CG01",
    )
    cycle_id = res["cycle_id"]
    row = isolated_db.query(models.EngineEvaluationCycleRecord).filter(
        models.EngineEvaluationCycleRecord.cycle_id == cycle_id
    ).first()
    assert row is not None
    assert row.run_id is None


def test_rid07_disabled_evaluation_with_run_a(isolated_db):
    """RID07: Disabled model evaluation attaches effective run_A and status = SKIPPED."""
    ctx = MockResearchContext("RUN_DISABLED_TEST")
    res = scalp_watcher_orchestrator.evaluate_cycle(
        isolated_db,
        symbol="XAUUSDT",
        auto_enabled=False,
        research_context=ctx,
    )
    assert res["status"] in ("DISABLED", "NO_QUOTE")
    cycle_id = res["cycle_id"]
    row = isolated_db.query(models.EngineEvaluationCycleRecord).filter(
        models.EngineEvaluationCycleRecord.cycle_id == cycle_id
    ).first()
    assert row is not None
    assert row.run_id == "RUN_DISABLED_TEST"
    assert row.status == "SKIPPED"


def test_rid08_failed_evaluation_retains_run_a(isolated_db):
    """RID08: Failure finalization retains run_A identity."""
    class DummyAuthority:
        write_status = "FAILED"
        has_write_failures = True
        write_issues_count = 1
        write_outcomes = {}

    res = scalp_watcher_orchestrator._finalize_failed_cycle(
        isolated_db,
        symbol="XAUUSDT",
        t_recv=int(time.time() * 1000),
        t_start=int(time.time() * 1000),
        batch_result=None,
        cycle_authority=DummyAuthority(),
        cycle_id="failed-rid08-cycle",
        effective_run_id="RUN_FAILED_TEST",
    )
    assert res["status"] == "DEGRADED"
    row = isolated_db.query(models.EngineEvaluationCycleRecord).filter(
        models.EngineEvaluationCycleRecord.cycle_id == "failed-rid08-cycle"
    ).first()
    assert row is not None
    assert row.run_id == "RUN_FAILED_TEST"
    assert row.status == "FAILED"


def test_rid09_armed_cycle_linked_run(isolated_db):
    """RID09: Record evaluation cycle with ARMED status and run_id."""
    res = engine_observability_service.record_evaluation_cycle(
        isolated_db,
        cycle_id="cycle-armed-01",
        run_id="RUN_ARMED_01",
        started_at_ms=1000,
        completed_at_ms=1050,
        status="COMPLETED",
        symbol="XAUUSDT",
        armed_order_id="order-paper-01",
        decision_status="ARMED",
    )
    assert res["run_id"] == "RUN_ARMED_01"
    row = isolated_db.query(models.EngineEvaluationCycleRecord).filter(
        models.EngineEvaluationCycleRecord.cycle_id == "cycle-armed-01"
    ).first()
    assert row.armed_order_id == "order-paper-01"
    assert row.run_id == "RUN_ARMED_01"


def test_rid10_two_simultaneous_runs(isolated_db):
    """RID10: Cycles recorded for different runs remain segregated."""
    engine_observability_service.record_evaluation_cycle(
        isolated_db,
        cycle_id="cycle-r1",
        run_id="RUN_1",
        started_at_ms=1000,
        completed_at_ms=1050,
        status="COMPLETED",
        symbol="XAUUSDT",
    )
    engine_observability_service.record_evaluation_cycle(
        isolated_db,
        cycle_id="cycle-r2",
        run_id="RUN_2",
        started_at_ms=2000,
        completed_at_ms=2050,
        status="COMPLETED",
        symbol="XAUUSDT",
    )
    c1 = engine_observability_service.get_latest_persisted_cycle(isolated_db, "XAUUSDT", run_id="RUN_1")
    c2 = engine_observability_service.get_latest_persisted_cycle(isolated_db, "XAUUSDT", run_id="RUN_2")
    assert c1["cycle_id"] == "cycle-r1"
    assert c2["cycle_id"] == "cycle-r2"


def test_rid11_restart_and_read_run_a_only(isolated_db):
    """RID11: get_latest_persisted_cycle filters strictly by run_id from DB."""
    engine_observability_service.record_evaluation_cycle(
        isolated_db,
        cycle_id="cycle-older-a",
        run_id="RUN_A",
        started_at_ms=1000,
        completed_at_ms=1050,
        status="COMPLETED",
        symbol="XAUUSDT",
    )
    engine_observability_service.record_evaluation_cycle(
        isolated_db,
        cycle_id="cycle-newer-b",
        run_id="RUN_B",
        started_at_ms=2000,
        completed_at_ms=2050,
        status="COMPLETED",
        symbol="XAUUSDT",
    )
    # Clear in-memory diagnostic
    with engine_observability_service._lock:
        engine_observability_service._latest_cycle_diagnostic = None

    c_a = engine_observability_service.get_latest_persisted_cycle(isolated_db, "XAUUSDT", run_id="RUN_A")
    assert c_a is not None
    assert c_a["cycle_id"] == "cycle-older-a"
    assert c_a["run_id"] == "RUN_A"


def test_rid12_null_run_not_leaked_into_run_a(isolated_db):
    """RID12: Cycle with null run is never returned when run_A is requested."""
    engine_observability_service.record_evaluation_cycle(
        isolated_db,
        cycle_id="cycle-null-run",
        run_id=None,
        started_at_ms=5000,
        completed_at_ms=5050,
        status="COMPLETED",
        symbol="XAUUSDT",
    )
    c_a = engine_observability_service.get_latest_persisted_cycle(isolated_db, "XAUUSDT", run_id="RUN_A")
    assert c_a is None
