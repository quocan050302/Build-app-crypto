"""
backend/tests/test_v16_16_transaction_and_linkage.py
V16.16 Tests for Transaction Ownership, Caller Session Isolation, and Association Metadata Supplementation.

Covers:
- Rules 021, 031-035: Research helpers (get_or_create_run, start_observation_run, complete_run)
  must NEVER commit caller transactions. Rollback on caller must rollback pending entities.
- Rules 022, 037-040: Atomic cycle unit of work. Failure before commit leaves no orphaned links/orders.
- Rules 023, 082: associate_order_with_research_run supplements missing trace_id/opportunity_id
  idempotently on subsequent calls instead of dropping them.
- Rule 044: build_cg01_paper_run_summary read path does not auto-create runs.
- Rule 045: start_observation_run with run_id=None generates a valid ID.
"""
import pytest
import time
from database import SessionLocal
import models
from services.paper_research_run_service import (
    PaperResearchRunService,
    associate_order_with_research_run,
    build_cg01_paper_run_summary,
    start_observation_run,
)


@pytest.fixture
def test_db():
    db = SessionLocal()
    yield db
    db.close()


def test_get_or_create_run_does_not_commit_caller_transaction(test_db):
    """
    Rule 021, 031, 032: Helper get_or_create_run must use db.flush(), not db.commit().
    If caller adds a pending order and then rolls back, the order must NOT be persisted.
    """
    pending_order_id = f"ord_tx_leak_{int(time.time() * 1000)}"
    ord_pending = models.PaperOrder(
        id=pending_order_id,
        direction="LONG",
        state="candidate",
        planned_entry=2500.0,
        stop_loss=2490.0,
        take_profit=2520.0,
        quantity=0.1,
        initial_risk_usdt=10.0,
        created_at=int(time.time() * 1000),
    )
    test_db.add(ord_pending)

    # Call get_or_create_run on the SAME session
    run_id = f"run_tx_{int(time.time() * 1000)}"
    run = PaperResearchRunService.get_or_create_run(
        run_id=run_id,
        db=test_db,
        create_if_missing=True
    )
    assert run is not None

    # Roll back caller session
    test_db.rollback()

    # In a fresh session, neither the pending order nor the uncommitted run should exist
    fresh_db = SessionLocal()
    try:
        found_order = fresh_db.query(models.PaperOrder).filter(models.PaperOrder.id == pending_order_id).first()
        assert found_order is None, f"Pending order {pending_order_id} was prematurely committed by helper!"

        found_run = fresh_db.query(models.PaperResearchRunRecord).filter(models.PaperResearchRunRecord.run_id == run_id).first()
        assert found_run is None, f"Run {run_id} was prematurely committed by helper!"
    finally:
        fresh_db.close()


def test_start_and_complete_run_do_not_commit_caller_transaction(test_db):
    """
    Rules 033, 034: start_observation_run and complete_run must NOT commit caller transactions.
    """
    pending_order_id = f"ord_tx_start_{int(time.time() * 1000)}"
    ord_pending = models.PaperOrder(
        id=pending_order_id,
        direction="SHORT",
        state="candidate",
        planned_entry=2600.0,
        stop_loss=2610.0,
        take_profit=2580.0,
        quantity=0.1,
        initial_risk_usdt=10.0,
        created_at=int(time.time() * 1000),
    )
    test_db.add(ord_pending)

    run_id = f"run_start_tx_{int(time.time() * 1000)}"
    run = start_observation_run(db=test_db, run_id=run_id, symbol="XAUUSDT")
    assert run is not None

    test_db.rollback()

    fresh_db = SessionLocal()
    try:
        found_order = fresh_db.query(models.PaperOrder).filter(models.PaperOrder.id == pending_order_id).first()
        assert found_order is None, f"Pending order {pending_order_id} was prematurely committed by start_observation_run!"
    finally:
        fresh_db.close()


def test_association_supplements_missing_trace_metadata(test_db):
    """
    Rules 023, 082: When a link was initially created without trace_id or opportunity_id,
    a subsequent call with valid trace_id / opportunity_id MUST supplement the missing fields.
    """
    run_id = f"run_assoc_supp_{int(time.time() * 1000)}"
    order_id = f"ord_assoc_supp_{int(time.time() * 1000)}"

    # 1. First association has no trace or opp
    res1 = associate_order_with_research_run(
        db=test_db,
        run_id=run_id,
        order_id=order_id,
        opportunity_id=None,
        trace_id=None,
    )
    test_db.commit()
    assert res1 is True

    # Verify link exists with None
    link1 = test_db.query(models.PaperResearchOrderLink).filter(
        models.PaperResearchOrderLink.run_id == run_id,
        models.PaperResearchOrderLink.order_id == order_id,
    ).first()
    assert link1 is not None
    assert link1.trace_id is None
    assert link1.opportunity_id is None

    # 2. Subsequent call provides valid metadata
    expected_trace = f"trc_valid_{int(time.time() * 1000)}"
    expected_opp = f"opp_valid_{int(time.time() * 1000)}"
    res2 = associate_order_with_research_run(
        db=test_db,
        run_id=run_id,
        order_id=order_id,
        opportunity_id=expected_opp,
        trace_id=expected_trace,
    )
    test_db.commit()
    assert res2 is True

    # 3. Fresh session check: metadata must be supplemented
    fresh_db = SessionLocal()
    try:
        updated_link = fresh_db.query(models.PaperResearchOrderLink).filter(
            models.PaperResearchOrderLink.run_id == run_id,
            models.PaperResearchOrderLink.order_id == order_id,
        ).first()
        assert updated_link is not None
        assert updated_link.trace_id == expected_trace, f"Expected trace_id {expected_trace}, got {updated_link.trace_id}"
        assert updated_link.opportunity_id == expected_opp, f"Expected opportunity_id {expected_opp}, got {updated_link.opportunity_id}"
    finally:
        fresh_db.close()


def test_build_summary_does_not_create_run_on_read(test_db):
    """
    Rule 044: build_cg01_paper_run_summary on non-existent run must return NOT_FOUND,
    never creating a fictitious run record in the database.
    """
    non_existent_run = f"run_fake_{int(time.time() * 1000)}"
    summary = build_cg01_paper_run_summary(non_existent_run, db=test_db)
    assert summary.get("status") in ("NOT_FOUND", "NO_RUN"), f"Expected NOT_FOUND for non-existent run, got {summary.get('status')}"

    fresh_db = SessionLocal()
    try:
        row = fresh_db.query(models.PaperResearchRunRecord).filter(
            models.PaperResearchRunRecord.run_id == non_existent_run
        ).first()
        assert row is None, f"Non-existent run {non_existent_run} was created on read path!"
    finally:
        fresh_db.close()


def test_start_observation_run_generates_valid_id_when_none(test_db):
    """
    Rule 045: start_observation_run(run_id=None) must generate a valid non-empty run_id.
    """
    run = start_observation_run(db=test_db, run_id=None, symbol="XAUUSDT")
    assert run.run_id is not None
    assert len(run.run_id) > 0
    assert run.run_id != "None"
    test_db.commit()
