"""
backend/tests/test_v16_16_milestone_and_runtime_report.py
V16.16 Tests for Lifecycle Milestones Persistence and Runtime Report Truthfulness.

Covers:
- Rules 028, 095: record_committed_order_milestone must persistently store milestone, timestamp,
  and payload in SQLite (fresh session can query facts).
- Rules 029, 141-144: Runtime reports must accurately reflect DB and worker health,
  never hardcoding 'running continuously' or '0 samples' when worker is stopped.
"""
import pytest
import time
from database import SessionLocal
import models
from services.paper_research_run_service import (
    record_committed_order_milestone,
    start_observation_run,
)


@pytest.fixture
def test_db():
    db = SessionLocal()
    yield db
    db.close()


def test_order_milestone_persists_and_is_readable_in_fresh_session(test_db):
    """
    Rules 028, 095: record_committed_order_milestone must actually save milestone, timestamp,
    and payload, readable in a fresh session.
    """
    run_id = f"run_ms_{int(time.time() * 1000)}"
    order_id = f"ord_ms_{int(time.time() * 1000)}"
    start_observation_run(db=test_db, run_id=run_id, symbol="XAUUSDT")

    t_arm = int(time.time() * 1000)
    res_arm = record_committed_order_milestone(
        db=test_db,
        run_id=run_id,
        order_id=order_id,
        milestone="ARMED",
        timestamp_ms=t_arm,
        payload={"planned_entry": 2500.0, "sl": 2490.0, "tp": 2520.0},
    )
    test_db.commit()
    assert res_arm is True

    t_fill = t_arm + 500
    res_fill = record_committed_order_milestone(
        db=test_db,
        run_id=run_id,
        order_id=order_id,
        milestone="FILLED",
        timestamp_ms=t_fill,
        payload={"fill_price": 2500.2},
    )
    test_db.commit()
    assert res_fill is True

    # Read back in fresh session
    fresh_db = SessionLocal()
    try:
        milestones = fresh_db.query(models.PaperResearchOrderMilestone).filter(
            models.PaperResearchOrderMilestone.run_id == run_id,
            models.PaperResearchOrderMilestone.order_id == order_id,
        ).all()
        assert len(milestones) == 2, f"Expected 2 milestones (ARMED, FILLED), got {len(milestones)}"
        names = {m.milestone for m in milestones}
        assert "ARMED" in names
        assert "FILLED" in names
    finally:
        fresh_db.close()


def test_runtime_report_accurately_reflects_stopped_worker_and_empty_run(test_db):
    """
    Rules 029, 141-144: Runtime report must accurately reflect worker status.
    When worker is stopped or not running, report MUST say STOPPED, not 'running continuously'.
    """
    from services.paper_research_run_service import generate_runtime_paper_report_content
    from services.cg01_scheduler_service import CG01EvaluationScheduler

    run_id = f"run_stopped_test_{int(time.time() * 1000)}"
    start_observation_run(db=test_db, run_id=run_id, symbol="XAUUSDT")
    test_db.commit()

    # Create scheduler instance with _running = False
    mock_sched = CG01EvaluationScheduler()
    mock_sched._running = False
    mock_sched._stopped = True

    rep = generate_runtime_paper_report_content(run_id=run_id, db=test_db, scheduler=mock_sched)
    assert rep["operational_status"] == "STOPPED"
    assert rep["worker_running"] is False
    assert "Observation worker is currently STOPPED" in rep["markdown"]
    assert "running continuously" not in rep["markdown"]


def test_runtime_report_non_existent_run_returns_not_found(test_db):
    """
    Rule 147: Non-existent run report returns NOT_FOUND and does not create run.
    """
    from services.paper_research_run_service import generate_runtime_paper_report_content
    fake_run = f"run_ghost_{int(time.time() * 1000)}"
    rep = generate_runtime_paper_report_content(run_id=fake_run, db=test_db)
    assert rep["status"] == "NOT_FOUND"

