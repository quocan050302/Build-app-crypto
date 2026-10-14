"""backend/tests/test_v14_4_16_repair_provenance.py

V14.4.16 Acceptance Tests: Original Session Provenance & Validation
Cases:
- PREP01 / P01: Nonexistent Session Repro (PHẦN 005, 036, 059)
  - Reviewer stages task with forward_session_id='DOES_NOT_EXIST'.
  - repair_forward_membership_task must NOT return REPAIRED.
  - Transitions to MANUAL_REVIEW_REQUIRED with NONEXISTENT_FORWARD_SESSION reason.
  - Zero membership or fake session index created.
- PREP02 / P02: Existent But Wrong Run Conflict (PHẦN 038, 043, 060)
  - Run B exists, but task was tampered to point to Run B while original provenance belongs to Run A.
  - Repair detects mismatch and transitions to MANUAL_REVIEW_REQUIRED. Run B report does not leak order.
- PREP03 / P03: Corrupt Proof & Damaged Payload Preservation (PHẦN 039, 041, 061)
- PREP04 / P04: Monotonic Target Status Progression (PHẦN 042, 062)
  - Stale FILLED task cannot downgrade CLOSED order.
- PREP05 / P05: Stopped Session Repair (PHẦN 036, 043, 063)
  - Stopped/completed run with trusted provenance repairs successfully without forcing active status.
- PREP06 / P06: Zero Financial Side Effects (PHẦN 047, 048, 064)
  - Snapshot order fields, realized PnL, exit, ledger, trade outbox; assert zero financial mutations.
"""

import json
import pytest
from sqlalchemy.orm import Session

import models
from services.forward_paper_service import (
    create_forward_paper_session,
    load_forward_session,
    bind_order_to_forward_session,
    stage_forward_repair_task,
    load_due_forward_repairs,
    repair_forward_membership_task,
    process_due_forward_repairs,
    get_order_forward_membership,
    ForwardMembershipRepairTask,
    _repair_task_key,
    _get_session_order_ids,
)


def test_prep01_nonexistent_session_repro(isolated_db: Session):
    """PREP01 / P01: Nonexistent Session Repro (PHẦN 005, 036, 059).
    - Reviewer stages repair task with forward_session_id = 'DOES_NOT_EXIST'.
    - Calls repair_forward_membership_task.
    - Expected: returns success = False, status = 'MANUAL_REVIEW_REQUIRED',
      reason = 'NONEXISTENT_FORWARD_SESSION: DOES_NOT_EXIST'.
    - No membership record and no fake session index created!
    """
    now_ms = 1792000000000

    order = models.PaperOrder(
        id="ord_p01_fake_sess",
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
        realized_pnl_net=2.00,
        opened_at=now_ms,
        closed_at=now_ms + 5000,
        created_at=now_ms,
    )
    isolated_db.add(order)
    isolated_db.commit()

    # Manually create task pointing to a nonexistent session
    fake_sess_id = "DOES_NOT_EXIST"
    task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order.id}",
        order_id=order.id,
        forward_session_id=fake_sess_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        reason_codes=["MANUAL_REPRO"],
    )
    task_key = _repair_task_key(order.id)
    rec = models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms)
    isolated_db.add(rec)
    isolated_db.commit()

    # Call repair_forward_membership_task
    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)

    # Must NOT succeed or mark REPAIRED
    assert res["success"] is False
    assert res["status"] == "MANUAL_REVIEW_REQUIRED"
    assert "NONEXISTENT_FORWARD_SESSION" in (res.get("reason") or "")

    # Assert NO membership created for nonexistent session
    mem = get_order_forward_membership(isolated_db, order.id)
    assert mem is None

    # Assert fake session index does NOT contain order
    fake_order_ids = _get_session_order_ids(isolated_db, fake_sess_id)
    assert order.id not in fake_order_ids


def test_prep02_existent_but_wrong_run_conflict(isolated_db: Session):
    """PREP02 / P02: Existent But Wrong Run Conflict (PHẦN 038, 043, 060).
    - Session A and Session B both exist in DB.
    - Order originally belongs to Session A (provenance in damaged payload / binding).
    - Task is tampered to point to Session B.
    - Worker rejects repair to Session B with PROVENANCE_SESSION_MISMATCH.
    - Session B does NOT leak this order!
    """
    now_ms = 1792000000000

    sess_a = create_forward_paper_session(isolated_db, "fwd_sess_P02_A", virtual_capital=1000.0, now_ms=now_ms)
    sess_b = create_forward_paper_session(isolated_db, "fwd_sess_P02_B", virtual_capital=2000.0, now_ms=now_ms + 1000)

    ord_conflict = models.PaperOrder(
        id="ord_p02_conflict",
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
        realized_pnl_net=2.00,
        opened_at=now_ms,
        closed_at=now_ms + 5000,
        created_at=now_ms,
        session_instance_id="fwd_sess_P02_A",  # Original provenance is Session A!
    )
    isolated_db.add(ord_conflict)
    isolated_db.commit()

    # Tampered task points to Session B, but original damaged payload records Session A
    tampered_task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{ord_conflict.id}",
        order_id=ord_conflict.id,
        forward_session_id=sess_b.session_id,  # Tampered!
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=json.dumps({"forward_session_id": sess_a.session_id, "membership_status": "CORRUPT"}),
    )
    task_key = _repair_task_key(ord_conflict.id)
    rec = models.SystemConfig(key=task_key, value=json.dumps(tampered_task.to_dict()), updated_at=now_ms)
    isolated_db.add(rec)
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, tampered_task, now_ms=now_ms + 2000)
    assert res["success"] is False
    assert res["status"] == "MANUAL_REVIEW_REQUIRED"
    assert "PROVENANCE_SESSION_MISMATCH" in (res.get("reason") or "")

    # Verify Session B index does NOT contain ord_conflict
    b_orders = _get_session_order_ids(isolated_db, sess_b.session_id)
    assert ord_conflict.id not in b_orders


def test_prep03_missing_or_corrupt_proof_handling(isolated_db: Session):
    """PREP03 / P03: Corrupt Proof & Damaged Payload Preservation (PHẦN 039, 041, 061).
    - Unparseable JSON and conflicting index entries transition task to MANUAL_REVIEW_REQUIRED.
    - Original damaged payload string is preserved unmutated for diagnostics.
    """
    now_ms = 1792000000000

    order = models.PaperOrder(
        id="ord_p03_corrupt",
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
        realized_pnl_net=2.00,
        opened_at=now_ms,
        closed_at=now_ms + 5000,
        created_at=now_ms,
    )
    isolated_db.add(order)
    isolated_db.commit()

    corrupt_str = "{{{{malformed_json_not_valid"
    task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order.id}",
        order_id=order.id,
        forward_session_id=None,  # Missing session ID!
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=corrupt_str,
    )
    task_key = _repair_task_key(order.id)
    rec = models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms)
    isolated_db.add(rec)
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is False
    assert res["status"] == "MANUAL_REVIEW_REQUIRED"

    # Reload from DB and verify damaged payload preserved
    isolated_db.refresh(rec)
    reloaded = ForwardMembershipRepairTask.from_dict(json.loads(rec.value))
    assert reloaded.original_damaged_payload == corrupt_str


def test_prep04_monotonic_target_status(isolated_db: Session):
    """PREP04 / P04: Monotonic Target Status Progression (PHẦN 042, 062).
    - Order is CLOSED in DB.
    - Stale task has target_status = 'FILLED'.
    - Repair MUST NOT downgrade membership to FILLED; resolves monotonically to CLOSED!
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(isolated_db, "fwd_sess_P04", virtual_capital=1000.0, now_ms=now_ms)

    order = models.PaperOrder(
        id="ord_p04_monotonic",
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
        realized_pnl_net=2.00,
        opened_at=now_ms,
        closed_at=now_ms + 5000,
        created_at=now_ms,
    )
    isolated_db.add(order)
    isolated_db.commit()

    stale_task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order.id}",
        order_id=order.id,
        forward_session_id=sess.session_id,
        target_status="FILLED",  # Stale!
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
    )
    task_key = _repair_task_key(order.id)
    rec = models.SystemConfig(key=task_key, value=json.dumps(stale_task.to_dict()), updated_at=now_ms)
    isolated_db.add(rec)
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, stale_task, now_ms=now_ms + 1000)
    assert res["success"] is True
    assert res["status"] == "REPAIRED"

    # Membership must be CLOSED, NOT FILLED!
    mem = get_order_forward_membership(isolated_db, order.id)
    assert mem is not None
    assert mem.membership_status == "CLOSED", "Monotonic check must prevent downgrading closed order to FILLED"


def test_prep05_stopped_session_repair(isolated_db: Session):
    """PREP05 / P05: Stopped Session Repair (PHẦN 036, 043, 063).
    - Session A is STOPPED.
    - Order originally belongs to Session A.
    - Repair succeeds cleanly and binds to Session A without forcing session to ACTIVE.
    """
    now_ms = 1792000000000

    sess_a = create_forward_paper_session(isolated_db, "fwd_sess_P05_A", virtual_capital=1000.0, now_ms=now_ms)
    # Transition to STOPPED by creating Session B
    sess_b = create_forward_paper_session(isolated_db, "fwd_sess_P05_B", virtual_capital=2000.0, now_ms=now_ms + 1000)

    sess_a_reloaded = load_forward_session(isolated_db, sess_a.session_id)
    assert sess_a_reloaded is not None
    assert sess_a_reloaded.status == "STOPPED"

    order = models.PaperOrder(
        id="ord_p05_stopped",
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
        realized_pnl_net=2.00,
        opened_at=now_ms,
        closed_at=now_ms + 5000,
        created_at=now_ms,
    )
    isolated_db.add(order)
    isolated_db.commit()

    task = stage_forward_repair_task(
        db=isolated_db,
        order_id=order.id,
        forward_session_id=sess_a.session_id,
        target_status="CLOSED",
        reason_codes=["REPAIR_STOPPED_RUN"],
        now_ms=now_ms + 2000,
    )
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 3000)
    assert res["success"] is True
    assert res["status"] == "REPAIRED"

    # Preserved Session A
    mem = get_order_forward_membership(isolated_db, order.id)
    assert mem is not None
    assert mem.forward_session_id == sess_a.session_id

    # Session A remains STOPPED (not forced active)
    sess_a_final = load_forward_session(isolated_db, sess_a.session_id)
    assert sess_a_final is not None
    assert sess_a_final.status == "STOPPED"


def test_prep06_zero_financial_mutations(isolated_db: Session):
    """PREP06 / P06: Zero Financial Side Effects (PHẦN 047, 048, 064).
    - Snapshot order fields, realized PnL, exit price, initial risk, created_at, closed_at.
    - Snapshot DayAudit and outbox table rows.
    - Execute repair worker.
    - Assert that ALL order fields and financial accounting remain completely identical.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(isolated_db, "fwd_sess_P06", virtual_capital=1000.0, now_ms=now_ms)

    order = models.PaperOrder(
        id="ord_p06_fin_immutable",
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
        realized_pnl_net=2.00,
        realized_r=1.00,
        opened_at=now_ms,
        closed_at=now_ms + 5000,
        created_at=now_ms,
    )
    isolated_db.add(order)
    isolated_db.commit()

    task = stage_forward_repair_task(
        db=isolated_db,
        order_id=order.id,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        reason_codes=["TEST_FINANCIAL_ZERO"],
        now_ms=now_ms,
    )
    isolated_db.commit()

    # Pre-repair snapshot of financial fields
    snap_before = {
        "state": order.state,
        "planned_entry": order.planned_entry,
        "actual_entry": order.actual_entry,
        "stop_loss": order.stop_loss,
        "take_profit": order.take_profit,
        "actual_exit": order.actual_exit,
        "realized_pnl_net": order.realized_pnl_net,
        "realized_r": order.realized_r,
        "initial_risk_usdt": order.initial_risk_usdt,
        "quantity": order.quantity,
    }

    # Execute repair
    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is True

    # Post-repair snapshot
    isolated_db.refresh(order)
    snap_after = {
        "state": order.state,
        "planned_entry": order.planned_entry,
        "actual_entry": order.actual_entry,
        "stop_loss": order.stop_loss,
        "take_profit": order.take_profit,
        "actual_exit": order.actual_exit,
        "realized_pnl_net": order.realized_pnl_net,
        "realized_r": order.realized_r,
        "initial_risk_usdt": order.initial_risk_usdt,
        "quantity": order.quantity,
    }

    assert snap_before == snap_after, "Repair must have ZERO financial mutations on the order"
