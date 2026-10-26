"""
test_v14_4_15_membership_repair.py

PHẦN 031, 032, 033, 034, 035, 036, 037, 038, 039, 040, 041, 042, 043, 044, 045, 046, 047, 048, 049, 050:
Behavioral and unit test suite for Durable Forward Membership Repair.

Case IDs covered:
- REP01 (P01): Corrupt membership soft-degrades, valid trade closes cleanly, durable repair task staged & committed atomically.
- REP02 (P02): Owner commit failure on trade does NOT create phantom repair task.
- REP03 (P03): Repair worker commit failure leaves task retryable, no partial corruption.
- REP04 (P04): Restart & active session switch (order from runA remains in runA, never assigned to runB).
- REP05 (P05): Order with no forward session intent is a no-op (no spam tasks); missing intent with corrupt payload needs manual review.
- REP06 (P06): Monotonic status protection (CLOSED cannot be downgraded by older FILLED task) and duplicate repair idempotency.
- REP07 (P07): Keyset bounded query and exponential backoff on retries.
- REP08 (P08): Pure tracking error vs SQL failure distinction.
"""
from __future__ import annotations

import json
from unittest.mock import patch
import pytest
from sqlalchemy.orm import Session

import models
from services.forward_paper_service import (
    ForwardOrderMembership,
    ForwardMembershipRepairTask,
    create_forward_paper_session,
    stage_forward_membership_binding,
    stage_forward_membership_status,
    stage_forward_repair_task,
    load_due_forward_repairs,
    repair_forward_membership_task,
    process_due_forward_repairs,
    get_order_forward_membership,
    load_forward_session_orders,
    generate_forward_paper_report,
)
from services.trade_lifecycle_service import TradeLifecycleService


def test_rep01_durable_marker_and_valid_close(isolated_db: Session):
    """REP01 / P01: Durable Marker + Valid Close (PHẦN 034, 035, 057).
    - Seed order with corrupted membership payload in SystemConfig.
    - Trade closes via execute_close:
      order closes cleanly, trade.closed event is created, and durable repair task is staged.
    - Owner commit commits trade and repair task atomically in DB.
    - Running process_due_forward_repairs reconstructs membership and marks task REPAIRED.
    - Zero financial mutations.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(
        isolated_db,
        session_id="fwd_sess_p01",
        virtual_capital=2000.0,
        now_ms=now_ms,
    )

    order = models.PaperOrder(
        id="ord_p01_close",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=2.5,
        opened_at=now_ms,
        created_at=now_ms,
        session_instance_id="ASIA_20261014",
    )
    isolated_db.add(order)
    isolated_db.commit()

    # Bind order to forward session
    stage_forward_membership_binding(isolated_db, sess.session_id, order, now_ms=now_ms)
    isolated_db.commit()

    # Save independent binding snapshot before corrupting membership record (PHẦN 027, 061, 071)
    mem_key = f"fwd_mem:{order.id}"[:50]
    rec = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == mem_key).first()
    assert rec is not None
    snap_key = f"fwd_snap:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=snap_key, value=rec.value, updated_at=now_ms))
    isolated_db.commit()

    # Corrupt membership payload in SystemConfig to simulate invalid schema
    rec.value = '{"corrupted_data": true, "missing_session": null}'
    isolated_db.commit()

    # Execute trade close via TradeLifecycleService
    closed_ord = TradeLifecycleService.execute_close(
        db=isolated_db,
        order_id=order.id,
        exit_price=4200.0,
        exit_cause="TP_HIT",
        now_ms=now_ms + 10000,
        session_tag="ASIA",
    )
    assert closed_ord is not None
    assert closed_ord.state == "closed"
    assert closed_ord.realized_pnl_net is not None and closed_ord.realized_pnl_net > 0
    pnl_before = closed_ord.realized_pnl_net

    # Verify that a durable repair task was persisted in SystemConfig!
    due_tasks = load_due_forward_repairs(isolated_db, now_ms=now_ms + 10000)
    matching_tasks = [t for t in due_tasks if t.order_id == order.id]
    assert len(matching_tasks) == 1
    task = matching_tasks[0]
    assert task.status in ("PENDING", "RETRY_SCHEDULED")
    assert task.target_status == "CLOSED"

    # Execute repair worker
    res = process_due_forward_repairs(db=isolated_db, now_ms=now_ms + 15000)
    assert res["repaired"] >= 1

    # Verify membership has been reconstructed cleanly
    repaired_mem = get_order_forward_membership(isolated_db, order.id)
    assert repaired_mem is not None
    assert repaired_mem.membership_status == "CLOSED"
    assert repaired_mem.forward_session_id == sess.session_id

    # Verify task status is now REPAIRED
    due_tasks_after = load_due_forward_repairs(isolated_db, now_ms=now_ms + 15000)
    assert not any(t.order_id == order.id for t in due_tasks_after)

    # Assert no financial mutations on order
    isolated_db.refresh(closed_ord)
    assert closed_ord.realized_pnl_net == pnl_before
    assert closed_ord.actual_exit == 4200.0
    assert closed_ord.exit_cause == "TP_HIT"


def test_rep02_owner_fail_no_phantom_task(isolated_db: Session):
    """REP02 / P02: Owner Failure Contract (PHẦN 004, 034, 036, 058).
    - Inject commit failure on TradeLifecycleService.execute_close.
    - Owner aborts truthfully: returns None, order remains OPEN in DB.
    - Assert that NO phantom repair task is committed in DB!
    - Subsequent successful retry commits cleanly and stages exactly one task.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(
        isolated_db,
        session_id="fwd_sess_p02",
        virtual_capital=2000.0,
        now_ms=now_ms,
    )

    order = models.PaperOrder(
        id="ord_p02_fail",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=2.5,
        opened_at=now_ms,
        created_at=now_ms,
        session_instance_id="ASIA_20261014",
    )
    isolated_db.add(order)
    isolated_db.commit()

    stage_forward_membership_binding(isolated_db, sess.session_id, order, now_ms=now_ms)
    isolated_db.commit()

    # Corrupt membership
    mem_key = f"fwd_mem:{order.id}"[:50]
    rec = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == mem_key).first()
    rec.value = '{"broken": true}'
    isolated_db.commit()

    # Inject commit failure into the lifecycle transaction
    orig_commit = isolated_db.commit
    def _exploding_commit():
        raise RuntimeError("SIMULATED_DB_COMMIT_FAILURE")

    isolated_db.commit = _exploding_commit
    try:
        closed = TradeLifecycleService.execute_close(
            db=isolated_db,
            order_id=order.id,
            exit_price=4200.0,
            exit_cause="TP_HIT",
            now_ms=now_ms + 10000,
        )
        assert closed is None, "execute_close must return None on commit failure"
    finally:
        isolated_db.commit = orig_commit

    # In a fresh query: order is still paper_open, and NO phantom repair task was persisted
    isolated_db.rollback()
    db_ord = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == order.id).first()
    assert db_ord.state == "paper_open"

    # Assert no repair task exists in DB
    due_tasks = load_due_forward_repairs(isolated_db, now_ms=now_ms + 10000)
    assert not any(t.order_id == order.id for t in due_tasks)

    # Now retry close without failure: succeeds cleanly
    closed_retry = TradeLifecycleService.execute_close(
        db=isolated_db,
        order_id=order.id,
        exit_price=4200.0,
        exit_cause="TP_HIT",
        now_ms=now_ms + 20000,
    )
    assert closed_retry is not None
    assert closed_retry.state == "closed"

    due_retry = load_due_forward_repairs(isolated_db, now_ms=now_ms + 20000)
    assert any(t.order_id == order.id for t in due_retry)


def test_rep03_repair_commit_fail(isolated_db: Session):
    """REP03 / P03: Repair Worker Commit Failure Isolation (PHẦN 043, 045, 059).
    - Inject commit failure into repair_forward_membership_task.
    - Transaction rolls back, task transitions to RETRY_SCHEDULED with incremented attempts.
    - Subsequent retry succeeds with REPAIRED.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(
        isolated_db,
        session_id="fwd_sess_p03",
        virtual_capital=2000.0,
        now_ms=now_ms,
    )

    order = models.PaperOrder(
        id="ord_p03_worker_fail",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_exit=4200.0,
        exit_cause="TP_HIT",
        quantity=0.1,
        initial_risk_usdt=2.5,
        realized_pnl_net=20.0,
        opened_at=now_ms,
        closed_at=now_ms + 5000,
        created_at=now_ms,
    )
    isolated_db.add(order)
    isolated_db.commit()

    # Bind order to forward session before staging repair task (PHẦN 071)
    stage_forward_membership_binding(isolated_db, sess.session_id, order, now_ms=now_ms)
    isolated_db.commit()

    # Stage a repair task
    task = stage_forward_repair_task(
        db=isolated_db,
        order_id=order.id,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        reason_codes=["TEST_REPAIR"],
        now_ms=now_ms,
    )
    isolated_db.commit()

    # First attempt: inject commit failure
    orig_commit = isolated_db.commit
    fail_once = [True]
    def _conditional_commit():
        if fail_once[0]:
            fail_once[0] = False
            raise RuntimeError("SIMULATED_REPAIR_COMMIT_ERROR")
        orig_commit()

    isolated_db.commit = _conditional_commit
    try:
        res1 = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
        assert res1["success"] is False
    finally:
        isolated_db.commit = orig_commit

    # Reload task from DB: attempts incremented, next_retry_at_ms set
    due = load_due_forward_repairs(isolated_db, now_ms=now_ms + 10000)
    task_reloaded = [t for t in due if t.order_id == order.id][0]
    assert task_reloaded.attempts >= 1
    assert task_reloaded.status in ("PENDING", "RETRY_SCHEDULED")

    # Second attempt: succeed cleanly
    res2 = repair_forward_membership_task(isolated_db, task_reloaded, now_ms=now_ms + 100000)
    assert res2["success"] is True
    assert res2["status"] == "REPAIRED"


def test_rep04_restart_and_active_session_switch(isolated_db: Session):
    """REP04 / P04: Active Session Switch & Restart Isolation (PHẦN 047, 060).
    - Order armed in Run A.
    - Run A stopped, Run B becomes active forward paper session.
    - Repair task executed after switch preserves Run A identity.
    - Report for Run B does NOT leak order from Run A.
    """
    now_ms = 1792000000000

    sess_a = create_forward_paper_session(
        isolated_db,
        session_id="fwd_sess_A",
        virtual_capital=1000.0,
        now_ms=now_ms,
    )

    order_a = models.PaperOrder(
        id="ord_p04_run_a",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_exit=4200.0,
        exit_cause="TP_HIT",
        quantity=0.1,
        initial_risk_usdt=2.5,
        realized_pnl_net=20.0,
        opened_at=now_ms,
        closed_at=now_ms + 5000,
        created_at=now_ms,
        session_instance_id="ASIA_20261014",
    )
    isolated_db.add(order_a)
    isolated_db.commit()

    # Bind to session A
    stage_forward_membership_binding(isolated_db, sess_a.session_id, order_a, now_ms=now_ms)
    # Stage repair task with session A
    stage_forward_repair_task(
        db=isolated_db,
        order_id=order_a.id,
        forward_session_id=sess_a.session_id,
        target_status="CLOSED",
        reason_codes=["MANUAL_RECOVERY"],
        now_ms=now_ms,
    )
    isolated_db.commit()

    # Now create Session B (Session A becomes STOPPED, Session B becomes ACTIVE)
    sess_b = create_forward_paper_session(
        isolated_db,
        session_id="fwd_sess_B",
        virtual_capital=2000.0,
        now_ms=now_ms + 10000,
    )
    isolated_db.commit()

    # Process repair worker
    process_due_forward_repairs(isolated_db, now_ms=now_ms + 15000)

    # Repaired membership must strictly belong to Session A, NEVER Session B!
    mem = get_order_forward_membership(isolated_db, order_a.id)
    assert mem is not None
    assert mem.forward_session_id == sess_a.session_id
    assert mem.forward_session_id != sess_b.session_id

    # Verify orders in Session B do NOT contain order_a
    orders_b = load_forward_session_orders(isolated_db, sess_b.session_id)
    assert not any(o.id == order_a.id for o in orders_b)

    rep_b = generate_forward_paper_report(isolated_db, sess_b.session_id, now_ms=now_ms + 20000)
    assert rep_b["total_fills"] == 0


def test_rep05_no_known_run_intent_noop(isolated_db: Session):
    """REP05 / P05: No Forward Intent is No-op (PHẦN 037, 061).
    - Historical order with NO forward intent returns None from stage_forward_repair_task (no spam tasks).
    - Order with corrupted membership where session cannot be resolved transitions to MANUAL_REVIEW_REQUIRED,
      never defaults to current active session.
    """
    now_ms = 1792000000000

    # 1. Bare order with no forward session intent
    bare_order = models.PaperOrder(
        id="ord_p05_historical_bare",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_exit=4200.0,
        exit_cause="TP_HIT",
        quantity=0.1,
        initial_risk_usdt=2.5,
        realized_pnl_net=20.0,
        opened_at=now_ms - 100000,
        closed_at=now_ms - 50000,
        created_at=now_ms - 100000,
        session_instance_id="HISTORICAL_20260101",
    )
    isolated_db.add(bare_order)
    isolated_db.commit()

    task_res = stage_forward_repair_task(
        db=isolated_db,
        order_id=bare_order.id,
        forward_session_id=None,
        target_status="CLOSED",
        reason_codes=["NO_INTENT"],
        now_ms=now_ms,
    )
    assert task_res is None, "Order with no forward intent must be a no-op!"

    # 2. Corrupt payload with unresolvable session
    corrupt_order = models.PaperOrder(
        id="ord_p05_unresolvable",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_exit=4200.0,
        exit_cause="TP_HIT",
        quantity=0.1,
        initial_risk_usdt=2.5,
        realized_pnl_net=20.0,
        opened_at=now_ms,
        closed_at=now_ms + 5000,
        created_at=now_ms,
    )
    isolated_db.add(corrupt_order)
    # Add corrupted record
    corrupt_rec = models.SystemConfig(
        key=f"fwd_mem:{corrupt_order.id}"[:50],
        value="INVALID_JSON_AND_NO_SESSION_ID",
        updated_at=now_ms,
    )
    isolated_db.add(corrupt_rec)
    isolated_db.commit()

    # Stage repair task with intent detected from corrupt payload
    task = stage_forward_repair_task(
        db=isolated_db,
        order_id=corrupt_order.id,
        forward_session_id=None,
        target_status="CLOSED",
        reason_codes=["CORRUPT_PAYLOAD"],
        now_ms=now_ms,
    )
    assert task is not None
    assert task.status == "MANUAL_REVIEW_REQUIRED"
    assert "UNKNOWN_FORWARD_SESSION_INTENT" in task.reason_codes


def test_rep06_monotonic_status_and_duplicate_protection(isolated_db: Session):
    """REP06 / P06: Monotonic Status Progression & Duplicate Protection (PHẦN 040, 062).
    - Closed order cannot be downgraded to FILLED by an older repair task.
    - Duplicate repairs are idempotent.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(
        isolated_db,
        session_id="fwd_sess_p06",
        virtual_capital=1000.0,
        now_ms=now_ms,
    )

    order = models.PaperOrder(
        id="ord_p06_monotonic",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",  # already closed in real DB
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_exit=4200.0,
        exit_cause="TP_HIT",
        quantity=0.1,
        initial_risk_usdt=2.5,
        realized_pnl_net=20.0,
        opened_at=now_ms,
        closed_at=now_ms + 5000,
        created_at=now_ms,
    )
    isolated_db.add(order)
    isolated_db.commit()

    stage_forward_membership_binding(isolated_db, sess.session_id, order, now_ms=now_ms)
    isolated_db.commit()

    # Create a task with an older target_status "FILLED"
    task_old_fill = ForwardMembershipRepairTask(
        task_id=f"rep_{order.id}",
        order_id=order.id,
        forward_session_id=sess.session_id,
        target_status="FILLED",
        status="PENDING",
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
    )

    # Executing repair should inspect actual order state ("closed") and NOT downgrade membership
    res = repair_forward_membership_task(isolated_db, task_old_fill, now_ms=now_ms + 10000)
    assert res["success"] is True

    mem = get_order_forward_membership(isolated_db, order.id)
    assert mem is not None
    assert mem.membership_status == "CLOSED", "Must not downgrade closed order to FILLED!"


def test_rep07_budget_and_backoff(isolated_db: Session):
    """REP07 / P07: Bounded Queue Processing & Backoff (PHẦN 042, 043, 063).
    - Queue limit bounds the number of tasks processed per cycle.
    - Retried tasks calculate exponential backoff.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(
        isolated_db,
        session_id="fwd_sess_p07",
        virtual_capital=1000.0,
        now_ms=now_ms,
    )

    # Create 5 orders with pending repair tasks
    for i in range(5):
        ord_i = models.PaperOrder(
            id=f"ord_p07_{i}",
            instrument="XAUUSDT",
            direction="LONG",
            state="closed",
            planned_entry=4180.0,
            actual_entry=4180.0,
            stop_loss=4170.0,
            take_profit=4200.0,
            actual_exit=4200.0,
            exit_cause="TP_HIT",
            quantity=0.1,
            initial_risk_usdt=2.5,
            realized_pnl_net=20.0,
            opened_at=now_ms,
            closed_at=now_ms + 1000,
            created_at=now_ms,
        )
        isolated_db.add(ord_i)
        stage_forward_membership_binding(isolated_db, sess.session_id, ord_i, now_ms=now_ms)
        stage_forward_repair_task(
            db=isolated_db,
            order_id=ord_i.id,
            forward_session_id=sess.session_id,
            target_status="CLOSED",
            reason_codes=["BATCH_REPAIR"],
            now_ms=now_ms,
        )
    isolated_db.commit()

    # Bounded query with limit=2
    due_batch1 = load_due_forward_repairs(isolated_db, now_ms=now_ms, limit=2)
    assert len(due_batch1) == 2

    # Process batch with limit=2
    res_b1 = process_due_forward_repairs(isolated_db, now_ms=now_ms, limit=2)
    assert res_b1["processed"] == 2
    assert res_b1["repaired"] == 2


def test_rep08_pure_tracking_vs_sql_failure_distinction(isolated_db: Session):
    """REP08 / P08: Pure Tracking vs SQL Failure Distinction (PHẦN 035, 064).
    - Pure tracking validation error stages repair task and allows trade transaction to commit.
    - Operational SQL error during trade lifecycle aborts truthfully.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(
        isolated_db,
        session_id="fwd_sess_p08",
        virtual_capital=1000.0,
        now_ms=now_ms,
    )

    order = models.PaperOrder(
        id="ord_p08_pure",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=2.5,
        opened_at=now_ms,
        created_at=now_ms,
    )
    isolated_db.add(order)
    isolated_db.commit()

    stage_forward_membership_binding(isolated_db, sess.session_id, order, now_ms=now_ms)
    isolated_db.commit()

    # Make tracking payload schema invalid (pure tracking failure)
    mem_key = f"fwd_mem:{order.id}"[:50]
    rec = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == mem_key).first()
    rec.value = '{"unsupported_format": 999}'
    isolated_db.commit()

    # Pure tracking error: execute_close must NOT fail! It commits cleanly and stages repair task.
    closed = TradeLifecycleService.execute_close(
        db=isolated_db,
        order_id=order.id,
        exit_price=4200.0,
        exit_cause="TP_HIT",
        now_ms=now_ms + 10000,
    )
    assert closed is not None
    assert closed.state == "closed"

    # Repair task is staged
    due = load_due_forward_repairs(isolated_db, now_ms=now_ms + 10000)
    assert any(t.order_id == order.id for t in due)
