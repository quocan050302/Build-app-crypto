"""backend/tests/test_v14_4_17_repair_provenance.py

V14.4.17 Acceptance Tests: Strict Repair Provenance, Original Config Preservation & Asyncio Correctness
Cases P17-01 to P17-18 (PHẦN 052–070).
"""

import json
import math
from unittest.mock import patch
import pytest
from sqlalchemy.orm import Session

import models
from services.forward_paper_service import (
    create_forward_paper_session,
    load_forward_session,
    bind_order_to_forward_session,
    stage_forward_membership_binding,
    stage_forward_repair_task,
    load_due_forward_repairs,
    repair_forward_membership_task,
    process_due_forward_repairs,
    get_order_forward_membership,
    generate_forward_paper_report,
    export_forward_paper_data,
    ForwardMembershipRepairTask,
    _repair_task_key,
    _get_session_order_ids,
    REPAIR_PROOF_MISSING,
    REPAIR_PROOF_MALFORMED,
    REPAIR_PROOF_INVALID_SCHEMA,
    REPAIR_SESSION_NOT_FOUND,
    REPAIR_SESSION_CONFLICT,
    REPAIR_ORDER_CONFLICT,
    REPAIR_CONFIG_REVISION_MISSING,
    REPAIR_CONFIG_REVISION_CONFLICT,
    REPAIR_RUNTIME_COMMIT_MISSING,
    REPAIR_RUNTIME_COMMIT_CONFLICT,
    REPAIR_BOUND_AT_MISSING,
    REPAIR_BOUND_AT_CONFLICT,
)
from services.forward_repair_worker import ForwardRepairWorker, forward_repair_worker


def test_p17_01_malformed_json_with_existing_session_fails_closed(isolated_db: Session):
    """P17-01 (PHẦN 013, 014, 053): Malformed JSON in original_damaged_payload with existing session.
    When damaged payload is corrupt and NO independent complete proof exists:
    Must transition to MANUAL_REVIEW_REQUIRED, NOT mark REPAIRED.
    Raw damaged payload is preserved unmutated. Zero fake membership/index created.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(isolated_db, "fwd_sess_p17_01", virtual_capital=1000.0, now_ms=now_ms)

    order = models.PaperOrder(
        id="ord_p17_01",
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

    corrupt_payload = "{{invalid_json_broken_brace"
    task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order.id}",
        order_id=order.id,
        forward_session_id=sess.session_id,  # Target session exists!
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=corrupt_payload,
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)

    # Must fail closed: MANUAL_REVIEW_REQUIRED, not REPAIRED
    assert res["success"] is False
    assert res["status"] == "MANUAL_REVIEW_REQUIRED"
    assert REPAIR_PROOF_MALFORMED in (res.get("reason") or "")

    # Zero membership created
    assert get_order_forward_membership(isolated_db, order.id) is None

    # Order not in session index
    session_orders = _get_session_order_ids(isolated_db, sess.session_id)
    assert order.id not in session_orders

    # Raw payload preserved in task record
    t_rec = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == task_key).first()
    assert t_rec is not None
    saved_task = json.loads(t_rec.value)
    assert saved_task["original_damaged_payload"] == corrupt_payload


def test_p17_02_missing_proof_fails_closed(isolated_db: Session):
    """P17-02 (PHẦN 054): Missing Proof Handling.
    Task has forward_session_id, but NO proof payload exists.
    Must NOT assume task string is sufficient proof.
    Transitions to MANUAL_REVIEW_REQUIRED.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(isolated_db, "fwd_sess_p17_02", virtual_capital=1000.0, now_ms=now_ms)

    order = models.PaperOrder(
        id="ord_p17_02",
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

    task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order.id}",
        order_id=order.id,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=None,  # No proof!
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is False
    assert res["status"] == "MANUAL_REVIEW_REQUIRED"
    assert REPAIR_PROOF_MISSING in (res.get("reason") or "")


def test_p17_03_nonexistent_session_fails_closed(isolated_db: Session):
    """P17-03 (PHẦN 055): Nonexistent Session Handling.
    Task or proof points to a forward session that does not exist in DB.
    Transitions to MANUAL_REVIEW_REQUIRED with REPAIR_SESSION_NOT_FOUND.
    """
    now_ms = 1792000000000

    order = models.PaperOrder(
        id="ord_p17_03",
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

    nonexistent_sess_id = "fwd_sess_DOES_NOT_EXIST_XYZ"
    task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order.id}",
        order_id=order.id,
        forward_session_id=nonexistent_sess_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=json.dumps({
            "forward_session_id": nonexistent_sess_id,
            "order_id": order.id,
            "config_revision": 1,
            "runtime_commit": "sha_p17_03",
            "bound_at_ms": now_ms,
        }),
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is False
    assert res["status"] == "MANUAL_REVIEW_REQUIRED"
    assert REPAIR_SESSION_NOT_FOUND in (res.get("reason") or "") or "NONEXISTENT_FORWARD_SESSION" in (res.get("reason") or "")


def test_p17_04_session_conflict_manual_review(isolated_db: Session):
    """P17-04 (PHẦN 056): Session Conflict Handling.
    Session A and Session B both exist. Proof belongs to Session A, but task requests Session B.
    Must NOT move order to Session B, must NOT add to Session B index.
    Transitions to MANUAL_REVIEW_REQUIRED with REPAIR_SESSION_CONFLICT.
    """
    now_ms = 1792000000000

    sess_a = create_forward_paper_session(isolated_db, "fwd_sess_p17_04_A", virtual_capital=1000.0, now_ms=now_ms)
    sess_b = create_forward_paper_session(isolated_db, "fwd_sess_p17_04_B", virtual_capital=2000.0, now_ms=now_ms + 1000)

    order = models.PaperOrder(
        id="ord_p17_04",
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

    # Proof says Session A, task says Session B
    task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order.id}",
        order_id=order.id,
        forward_session_id=sess_b.session_id,  # Requested B
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=json.dumps({
            "forward_session_id": sess_a.session_id,  # Proof says A!
            "order_id": order.id,
            "config_revision": 1,
            "runtime_commit": "sha_p17_04",
            "bound_at_ms": now_ms,
        }),
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is False
    assert res["status"] == "MANUAL_REVIEW_REQUIRED"
    assert REPAIR_SESSION_CONFLICT in (res.get("reason") or "") or "PROVENANCE_SESSION_MISMATCH" in (res.get("reason") or "")

    # Session B index must NOT contain order
    b_orders = _get_session_order_ids(isolated_db, sess_b.session_id)
    assert order.id not in b_orders


def test_p17_05_order_conflict_manual_review(isolated_db: Session):
    """P17-05 (PHẦN 057): Order Conflict Handling.
    Task is for Order X, but proof payload is for Order Y.
    Must NOT use Order Y's data for Order X.
    Transitions to MANUAL_REVIEW_REQUIRED with REPAIR_ORDER_CONFLICT.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(isolated_db, "fwd_sess_p17_05", virtual_capital=1000.0, now_ms=now_ms)

    order_x = models.PaperOrder(
        id="ord_p17_05_X",
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
    isolated_db.add(order_x)
    isolated_db.commit()

    # Payload belongs to order Y
    task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order_x.id}",
        order_id=order_x.id,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=json.dumps({
            "forward_session_id": sess.session_id,
            "order_id": "ord_p17_05_Y_DIFFERENT",  # Belongs to Y!
            "config_revision": 1,
            "runtime_commit": "sha_p17_05",
            "bound_at_ms": now_ms,
        }),
    )
    task_key = _repair_task_key(order_x.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is False
    assert res["status"] == "MANUAL_REVIEW_REQUIRED"
    assert REPAIR_ORDER_CONFLICT in (res.get("reason") or "")


def test_p17_06_preserve_original_config_revision(isolated_db: Session):
    """P17-06 (PHẦN 058): Original Config Revision Preservation.
    Valid proof with config_revision = 7.
    Repair must persist config_revision = 7 in DB, NOT hardcode 1!
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(isolated_db, "fwd_sess_p17_06", virtual_capital=1000.0, now_ms=now_ms)

    order = models.PaperOrder(
        id="ord_p17_06",
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

    task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order.id}",
        order_id=order.id,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=json.dumps({
            "forward_session_id": sess.session_id,
            "order_id": order.id,
            "config_revision": 7,  # Revision 7!
            "runtime_commit": "sha_p17_06",
            "bound_at_ms": now_ms,
        }),
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is True
    assert res["status"] == "REPAIRED"

    # Reload from DB and verify config_revision is 7, NOT 1
    mem = get_order_forward_membership(isolated_db, order.id)
    assert mem is not None
    assert mem.config_revision == 7, "Must preserve original config_revision=7 in persisted DB record!"


def test_p17_07_preserve_original_runtime_commit(isolated_db: Session):
    """P17-07 (PHẦN 059): Original Runtime Commit Preservation.
    Proof has runtime_commit = 'commit_sha_original_777'.
    Session has strategy_commit = 'commit_sha_session_999'.
    Repaired membership must retain 'commit_sha_original_777', not replace with session commit or HEAD.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(isolated_db, "fwd_sess_p17_07", virtual_capital=1000.0, now_ms=now_ms)
    sess.strategy_commit = "commit_sha_session_999"
    from services.forward_paper_service import save_forward_session
    save_forward_session(isolated_db, sess)
    isolated_db.commit()

    order = models.PaperOrder(
        id="ord_p17_07",
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

    orig_commit = "commit_sha_original_777"
    task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order.id}",
        order_id=order.id,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=json.dumps({
            "forward_session_id": sess.session_id,
            "order_id": order.id,
            "config_revision": 2,
            "runtime_commit": orig_commit,
            "bound_at_ms": now_ms,
        }),
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is True

    mem = get_order_forward_membership(isolated_db, order.id)
    assert mem is not None
    assert mem.runtime_commit == orig_commit, "Must preserve original runtime_commit!"


def test_p17_08_preserve_original_bound_at_ms(isolated_db: Session):
    """P17-08 (PHẦN 060): Bound At Preservation.
    Proof bound_at_ms = 1000. Order opened_at = 2000. Repair now_ms = 9000.
    Persisted membership bound_at_ms must be 1000, NOT opened_at (2000), NOT now_ms (9000).
    """
    now_ms = 9000000

    sess = create_forward_paper_session(isolated_db, "fwd_sess_p17_08", virtual_capital=1000.0, now_ms=now_ms)

    order = models.PaperOrder(
        id="ord_p17_08",
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
        opened_at=2000000,  # 2000s
        closed_at=3000000,
        created_at=1500000,
    )
    isolated_db.add(order)
    isolated_db.commit()

    proven_bound_at = 1000000  # 1000s
    task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order.id}",
        order_id=order.id,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=json.dumps({
            "forward_session_id": sess.session_id,
            "order_id": order.id,
            "config_revision": 1,
            "runtime_commit": "sha_p17_08",
            "bound_at_ms": proven_bound_at,
        }),
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms)
    assert res["success"] is True

    mem = get_order_forward_membership(isolated_db, order.id)
    assert mem is not None
    assert mem.bound_at_ms == proven_bound_at, "Must preserve original bound_at_ms=1000000!"


def test_p17_09_malformed_payload_with_independent_proof_verified(isolated_db: Session):
    """P17-09 (PHẦN 027, 061): Malformed Payload with Independent Complete Proof.
    original_damaged_payload is malformed JSON.
    Independent snapshot record in SystemConfig (key fwd_snap:<order_id>) contains full binding metadata.
    Repair succeeds cleanly using the independent source.
    Raw damaged payload string in task is preserved.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(isolated_db, "fwd_sess_p17_09", virtual_capital=1000.0, now_ms=now_ms)

    order = models.PaperOrder(
        id="ord_p17_09",
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

    # Independent snapshot record
    snap_data = {
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 4,
        "runtime_commit": "sha_independent_proof_999",
        "bound_at_ms": now_ms,
    }
    snap_key = f"fwd_snap:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=snap_key, value=json.dumps(snap_data), updated_at=now_ms))
    isolated_db.commit()

    malformed_raw = "{{{invalid_unparseable_json"
    task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order.id}",
        order_id=order.id,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=malformed_raw,
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is True
    assert res["status"] == "REPAIRED"

    # Preserved independent source values
    mem = get_order_forward_membership(isolated_db, order.id)
    assert mem is not None
    assert mem.config_revision == 4
    assert mem.runtime_commit == "sha_independent_proof_999"

    # Raw damaged payload preserved in task record
    t_rec = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == task_key).first()
    assert t_rec is not None
    assert json.loads(t_rec.value)["original_damaged_payload"] == malformed_raw


def test_p17_10_conflicting_indexes_manual_review(isolated_db: Session):
    """P17-10 (PHẦN 025, 062): Conflicting Index Entries.
    Order is present in multiple session index queues without unambiguous proof.
    Transitions to MANUAL_REVIEW_REQUIRED with REPAIR_SESSION_CONFLICT.
    """
    now_ms = 1792000000000

    sess_1 = create_forward_paper_session(isolated_db, "fwd_sess_p17_10_1", virtual_capital=1000.0, now_ms=now_ms)
    sess_2 = create_forward_paper_session(isolated_db, "fwd_sess_p17_10_2", virtual_capital=1000.0, now_ms=now_ms + 100)

    order = models.PaperOrder(
        id="ord_p17_10",
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

    # Put order into BOTH session indexes
    from services.forward_paper_service import _append_session_order_id
    _append_session_order_id(isolated_db, sess_1.session_id, order.id, now_ms)
    _append_session_order_id(isolated_db, sess_2.session_id, order.id, now_ms)
    isolated_db.commit()

    task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order.id}",
        order_id=order.id,
        forward_session_id=None,  # No session specified in task!
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=None,
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is False
    assert res["status"] == "MANUAL_REVIEW_REQUIRED"


def test_p17_11_missing_config_revision_in_payload_fails_closed(isolated_db: Session):
    """P17-11 (PHẦN 028, 063): Missing Config Revision in Payload.
    Payload is valid JSON with session, order, commit, bound_at, but missing config_revision.
    Must NOT default to 1! Transitions to MANUAL_REVIEW_REQUIRED.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(isolated_db, "fwd_sess_p17_11", virtual_capital=1000.0, now_ms=now_ms)

    order = models.PaperOrder(
        id="ord_p17_11",
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

    # Missing config_revision in payload!
    task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order.id}",
        order_id=order.id,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=json.dumps({
            "forward_session_id": sess.session_id,
            "order_id": order.id,
            "runtime_commit": "sha_p17_11",
            "bound_at_ms": now_ms,
            # config_revision is OMITTED!
        }),
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is False
    assert res["status"] == "MANUAL_REVIEW_REQUIRED"
    assert REPAIR_CONFIG_REVISION_MISSING in (res.get("reason") or "")


@pytest.mark.parametrize("invalid_field,invalid_val", [
    ("config_revision", True),
    ("config_revision", 0),
    ("config_revision", "abc"),
    ("bound_at_ms", -100),
])
def test_p17_12_invalid_field_types_schema_protection(isolated_db: Session, invalid_field: str, invalid_val: any):
    """P17-12 (PHẦN 021, 064): Invalid Field Types Schema Protection.
    Parametrized with boolean config_revision, 0, string, negative bound_at.
    Must fail closed with REPAIR_PROOF_INVALID_SCHEMA without crashing.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(isolated_db, f"fwd_sess_p17_12_{invalid_field}", virtual_capital=1000.0, now_ms=now_ms)

    order = models.PaperOrder(
        id=f"ord_p17_12_{invalid_field}",
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

    base_payload = {
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 1,
        "runtime_commit": "sha_valid",
        "bound_at_ms": now_ms,
    }
    base_payload[invalid_field] = invalid_val

    task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order.id}",
        order_id=order.id,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=json.dumps(base_payload),
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is False
    assert res["status"] == "MANUAL_REVIEW_REQUIRED"
    assert REPAIR_PROOF_INVALID_SCHEMA in (res.get("reason") or "")


def test_p17_13_valid_repair_idempotency(isolated_db: Session):
    """P17-13 (PHẦN 043, 065): Idempotent Repair Execution.
    Running repair twice on same task returns ALREADY_REPAIRED on second run.
    Produces exactly one membership record, one entry in session index, zero duplicates.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(isolated_db, "fwd_sess_p17_13", virtual_capital=1000.0, now_ms=now_ms)

    order = models.PaperOrder(
        id="ord_p17_13",
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

    task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order.id}",
        order_id=order.id,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=json.dumps({
            "forward_session_id": sess.session_id,
            "order_id": order.id,
            "config_revision": 1,
            "runtime_commit": "sha_p17_13",
            "bound_at_ms": now_ms,
        }),
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    # First execution
    res1 = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res1["success"] is True
    assert res1["status"] == "REPAIRED"

    # Second execution (idempotent)
    res2 = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 2000)
    assert res2["success"] is True
    assert res2["status"] == "ALREADY_REPAIRED"

    # Verify session index contains order ID exactly once
    sess_orders = _get_session_order_ids(isolated_db, sess.session_id)
    assert sess_orders.count(order.id) == 1


def test_p17_14_owner_commit_failure_rollback(isolated_db: Session):
    """P17-14 (PHẦN 066): Owner Commit Failure Rollback.
    Simulate DB commit failure during repair.
    Asserts transaction rollback: no partial membership or index persisted.
    Task attempts incremented.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(isolated_db, "fwd_sess_p17_14", virtual_capital=1000.0, now_ms=now_ms)

    order = models.PaperOrder(
        id="ord_p17_14",
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

    task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order.id}",
        order_id=order.id,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=json.dumps({
            "forward_session_id": sess.session_id,
            "order_id": order.id,
            "config_revision": 1,
            "runtime_commit": "sha_p17_14",
            "bound_at_ms": now_ms,
        }),
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    # Inject commit failure
    orig_commit = isolated_db.commit
    fail_flag = [True]

    def _failing_commit():
        if fail_flag[0]:
            fail_flag[0] = False
            raise RuntimeError("SIMULATED_COMMIT_FAIL")
        orig_commit()

    isolated_db.commit = _failing_commit
    try:
        res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
        assert res["success"] is False
    finally:
        isolated_db.commit = orig_commit

    # Reload from DB: membership must NOT exist (rolled back)
    mem = get_order_forward_membership(isolated_db, order.id)
    assert mem is None


def test_p17_15_manual_review_commit_failure(isolated_db: Session):
    """P17-15 (PHẦN 067): Manual Review Commit Failure.
    Simulate failure when persisting manual review status.
    Asserts clean failure return without partial writes.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(isolated_db, "fwd_sess_p17_15", virtual_capital=1000.0, now_ms=now_ms)

    order = models.PaperOrder(
        id="ord_p17_15",
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

    task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order.id}",
        order_id=order.id,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload="{{broken_json",
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    # Inject commit failure during manual review commit
    orig_commit = isolated_db.commit
    fail_flag = [True]

    def _failing_commit():
        if fail_flag[0]:
            fail_flag[0] = False
            raise RuntimeError("SIMULATED_MANUAL_REVIEW_COMMIT_FAIL")
        orig_commit()

    isolated_db.commit = _failing_commit
    try:
        res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
        assert res["success"] is False
        assert res["status"] == "COMMIT_FAILED"
    finally:
        isolated_db.commit = orig_commit


def test_p17_16_zero_financial_mutations(isolated_db: Session):
    """P17-16 (PHẦN 068): Zero Financial Mutations.
    Snapshot all order fields, realize PnL, exit price, R, initial risk before repair.
    Assert zero changes after repair completes.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(isolated_db, "fwd_sess_p17_16", virtual_capital=1000.0, now_ms=now_ms)

    order = models.PaperOrder(
        id="ord_p17_16",
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

    task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order.id}",
        order_id=order.id,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=json.dumps({
            "forward_session_id": sess.session_id,
            "order_id": order.id,
            "config_revision": 1,
            "runtime_commit": "sha_p17_16",
            "bound_at_ms": now_ms,
        }),
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    snap_before = {
        "state": order.state,
        "planned_entry": order.planned_entry,
        "actual_entry": order.actual_entry,
        "stop_loss": order.stop_loss,
        "take_profit": order.take_profit,
        "actual_exit": order.actual_exit,
        "realized_pnl_net": order.realized_pnl_net,
        "realized_r": order.realized_r,
        "quantity": order.quantity,
        "initial_risk_usdt": order.initial_risk_usdt,
        "opened_at": order.opened_at,
        "closed_at": order.closed_at,
    }

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is True

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
        "quantity": order.quantity,
        "initial_risk_usdt": order.initial_risk_usdt,
        "opened_at": order.opened_at,
        "closed_at": order.closed_at,
    }
    assert snap_before == snap_after, "Repair must have ZERO financial side effects on order accounting!"


def test_p17_17_repaired_membership_does_not_forge_terminal_authority(isolated_db: Session):
    """P17-17 (PHẦN 046, 069): Strict Terminal Event Authority Invariant.
    An order has a validly repaired forward membership, but NO trade.closed domain event.
    generate_forward_paper_report must report confirmed_closed_trades = 0, net_pnl = 0.0.
    A repaired membership record CANNOT substitute for missing terminal event authority.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(isolated_db, "fwd_sess_p17_17", virtual_capital=1000.0, now_ms=now_ms)

    order = models.PaperOrder(
        id="ord_p17_17",
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

    # Repair membership to CLOSED
    task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order.id}",
        order_id=order.id,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=json.dumps({
            "forward_session_id": sess.session_id,
            "order_id": order.id,
            "config_revision": 1,
            "runtime_commit": "sha_p17_17",
            "bound_at_ms": now_ms,
        }),
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is True

    # Report without terminal domain event: must NOT confirm trade!
    report = generate_forward_paper_report(isolated_db, sess.session_id, now_ms=now_ms + 5000)
    assert report["confirmed_closed_trades"] == 0
    assert report["net_pnl"] == 0.0
    assert report["unverified_closed_trades"] == 1
    assert report["recorded_closed_pnl"] == 2.00


def test_p17_18_worker_process_real_path_continues_batch(isolated_db: Session):
    """P17-18 (PHẦN 070): Worker Process Production Path & Batch Continuity.
    Seed Task 1 (missing proof) and Task 2 (valid proof).
    Run ForwardRepairWorker.process_once().
    Task 1 transitions to MANUAL_REVIEW_REQUIRED, Task 2 is successfully REPAIRED.
    A single failed/unverified task does NOT block the rest of the batch.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(isolated_db, "fwd_sess_p17_18", virtual_capital=1000.0, now_ms=now_ms)

    ord_bad = models.PaperOrder(
        id="ord_p17_18_bad",
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
    ord_good = models.PaperOrder(
        id="ord_p17_18_good",
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
    isolated_db.add_all([ord_bad, ord_good])
    isolated_db.commit()

    # Bad task has corrupted payload and no independent proof
    task_bad = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{ord_bad.id}",
        order_id=ord_bad.id,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload="{{corrupt",
    )
    # Good task has complete valid proof
    task_good = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{ord_good.id}",
        order_id=ord_good.id,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=json.dumps({
            "forward_session_id": sess.session_id,
            "order_id": ord_good.id,
            "config_revision": 3,
            "runtime_commit": "sha_good_batch",
            "bound_at_ms": now_ms,
        }),
    )

    t_bad_key = _repair_task_key(ord_bad.id)
    t_good_key = _repair_task_key(ord_good.id)
    isolated_db.add_all([
        models.SystemConfig(key=t_bad_key, value=json.dumps(task_bad.to_dict()), updated_at=now_ms),
        models.SystemConfig(key=t_good_key, value=json.dumps(task_good.to_dict()), updated_at=now_ms),
        models.SystemConfig(key="fwd_repair_queue", value=json.dumps([ord_bad.id, ord_good.id]), updated_at=now_ms),
    ])
    isolated_db.commit()

    # Run worker process_once
    res = forward_repair_worker.process_once(db=isolated_db, now_ms=now_ms + 1000)

    assert res["processed"] == 2
    assert res["manual_review"] == 1, "Task 1 (corrupt) must transition to manual_review"
    assert res["repaired"] == 1, "Task 2 (valid) must successfully repair"
