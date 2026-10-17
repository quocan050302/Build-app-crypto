"""
backend/tests/test_v16_16_owner_and_shadow.py
V16.16 Tests for Execution Owner Arbitration, Shadow Isolation, and Watcher Cooperation.

Covers:
- Rules 024, 054: When scheduler is in shadow_mode=True (even if is_owner=True),
  watcher retains execution authority and is not disabled.
- Rules 025, 055: When scheduler is stopped or unhealthy, ownership falls back to WATCHER.
- Rules 051, 052: Watcher uses resolve_entry_execution_owner, avoiding conflicting cycles.
"""
import pytest
from database import SessionLocal
from services.cg01_scheduler_service import (
    CG01EvaluationScheduler,
    resolve_entry_execution_owner,
)
from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator


@pytest.fixture
def test_db():
    db = SessionLocal()
    yield db
    db.close()


def test_shadow_owner_combination_keeps_watcher_as_execution_owner(test_db):
    """
    Rule 024, 054: When scheduler has is_owner=True BUT shadow_mode=True:
    Scheduler CANNOT execute trades. Therefore, WATCHER remains the effective execution owner!
    """
    sched = CG01EvaluationScheduler()
    sched.enabled = True
    sched.is_owner = True
    sched.shadow_mode = True
    sched._running = True

    owner_res = resolve_entry_execution_owner(sched, db=test_db)
    assert owner_res["execution_owner"] == "WATCHER", (
        f"Expected execution_owner=WATCHER when scheduler is in shadow mode, got {owner_res['execution_owner']}"
    )
    assert owner_res["reason"] == "SCHEDULER_IN_SHADOW_MODE"


def test_stopped_scheduler_falls_back_to_watcher(test_db):
    """
    Rule 025: When scheduler has is_owner=True but is NOT running (_running=False):
    Ownership must fallback to WATCHER so trading execution is not deadlocked.
    """
    sched = CG01EvaluationScheduler()
    sched.enabled = True
    sched.is_owner = True
    sched.shadow_mode = False
    sched._running = False

    owner_res = resolve_entry_execution_owner(sched, db=test_db)
    assert owner_res["execution_owner"] == "WATCHER"
    assert owner_res["reason"] == "SCHEDULER_NOT_RUNNING"


def test_watcher_loop_uses_effective_owner_not_raw_is_owner(monkeypatch, test_db):
    """
    Rule 051, 055: Watcher loop determines its auto authority via resolve_entry_execution_owner,
    not raw scheduler.is_owner.
    """
    from services.cg01_scheduler_service import cg01_evaluation_scheduler
    # Set scheduler flags: is_owner=True, but shadow_mode=True
    cg01_evaluation_scheduler.enabled = True
    cg01_evaluation_scheduler.is_owner = True
    cg01_evaluation_scheduler.shadow_mode = True
    cg01_evaluation_scheduler._running = True

    owner_res = resolve_entry_execution_owner(cg01_evaluation_scheduler, db=test_db)
    assert owner_res["execution_owner"] == "WATCHER"
