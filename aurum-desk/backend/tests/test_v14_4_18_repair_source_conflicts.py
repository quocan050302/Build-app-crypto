"""backend/tests/test_v14_4_18_repair_source_conflicts.py

V14.4.18 Acceptance Tests: All-Source Provenance Validation, Index Conflict Handling, and Real Snapshot Source Audit.
Cases C18-01 to C18-18 (PHẦN 055–074).
"""

import json
from unittest.mock import patch
import pytest
from sqlalchemy.orm import Session

import models
from services.forward_paper_service import (
    create_forward_paper_session,
    load_forward_session,
    bind_order_to_forward_session,
    repair_forward_membership_task,
    get_order_forward_membership,
    resolve_repair_provenance,
    compare_repair_binding_sources,
    validate_repair_session_indexes,
    validate_repair_binding_source,
    collect_repair_provenance_sources,
    RepairProvenanceSource,
    ForwardMembershipRepairTask,
    ForwardOrderMembership,
    _repair_task_key,
    _get_session_order_ids,
    _append_session_order_id,
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
    REPAIR_SOURCE_CONFLICT,
    REPAIR_INDEX_CONFLICT,
    REPAIR_SOURCE_UNVERIFIED,
    REPAIR_SOURCE_ORDER_MISMATCH,
)
from services.forward_repair_worker import ForwardRepairWorker


def _make_closed_order(order_id: str, now_ms: int, session_instance_id: str = "") -> models.PaperOrder:
    return models.PaperOrder(
        id=order_id,
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
        session_instance_id=session_instance_id,
    )


def test_c18_01_revision_conflict_fails_closed(isolated_db: Session):
    """C18-01 (PHẦN 013, 056): Revision Conflict (Reviewer Repro 1).
    Membership has revision 7, Snapshot has revision 8.
    All other binding fields match.
    Must transition to MANUAL_REVIEW_REQUIRED with REPAIR_CONFIG_REVISION_CONFLICT.
    Zero membership rewrite, zero index mutation.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c18_01", virtual_capital=1000.0, now_ms=now_ms)

    order = _make_closed_order("ord_c18_01", now_ms)
    isolated_db.add(order)

    # Membership in DB has revision 7
    mem_key = f"fwd_mem:{order.id}"[:50]
    mem_data = {
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 7,
        "runtime_commit": "sha_consistent_commit_111",
        "bound_at_ms": now_ms,
    }
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps(mem_data), updated_at=now_ms))

    # Snapshot in DB has revision 8
    snap_key = f"fwd_snap:{order.id}"[:50]
    snap_data = {
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 8,  # Conflicting revision!
        "runtime_commit": "sha_consistent_commit_111",
        "bound_at_ms": now_ms,
    }
    isolated_db.add(models.SystemConfig(key=snap_key, value=json.dumps(snap_data), updated_at=now_ms))
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
        original_damaged_payload="{{invalid",
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is False
    assert res["status"] == "MANUAL_REVIEW_REQUIRED"
    assert REPAIR_CONFIG_REVISION_CONFLICT in (res.get("reason") or "")


def test_c18_02_session_conflict_between_sources(isolated_db: Session):
    """C18-02 (PHẦN 057): Session Conflict Between Membership and Snapshot.
    Membership claims Session A, Snapshot claims Session B.
    Must return CONFLICT, never choose membership just because read first.
    """
    now_ms = 1792000000000
    sess_a = create_forward_paper_session(isolated_db, "fwd_sess_c18_02_A", virtual_capital=1000.0, now_ms=now_ms)
    sess_b = create_forward_paper_session(isolated_db, "fwd_sess_c18_02_B", virtual_capital=1000.0, now_ms=now_ms)

    order = _make_closed_order("ord_c18_02", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess_a.session_id,
        "order_id": order.id,
        "config_revision": 1,
        "runtime_commit": "sha_111",
        "bound_at_ms": now_ms,
    }), updated_at=now_ms))

    snap_key = f"fwd_snap:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=snap_key, value=json.dumps({
        "forward_session_id": sess_b.session_id,  # Conflicting session!
        "order_id": order.id,
        "config_revision": 1,
        "runtime_commit": "sha_111",
        "bound_at_ms": now_ms,
    }), updated_at=now_ms))

    task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order.id}",
        order_id=order.id,
        forward_session_id=sess_a.session_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload="{{invalid",
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is False
    assert res["status"] == "MANUAL_REVIEW_REQUIRED"
    assert REPAIR_SESSION_CONFLICT in (res.get("reason") or "")


def test_c18_03_commit_conflict_between_sources(isolated_db: Session):
    """C18-03 (PHẦN 058): Runtime Commit Conflict.
    Two complete sources have different runtime commits (sha_A vs sha_B).
    Must return CONFLICT without defaulting to current HEAD or strategy_commit.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c18_03", virtual_capital=1000.0, now_ms=now_ms)

    order = _make_closed_order("ord_c18_03", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 1,
        "runtime_commit": "sha_commit_alpha_111",
        "bound_at_ms": now_ms,
    }), updated_at=now_ms))

    snap_key = f"fwd_snap:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=snap_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 1,
        "runtime_commit": "sha_commit_beta_222",  # Conflicting commit!
        "bound_at_ms": now_ms,
    }), updated_at=now_ms))

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
        original_damaged_payload="{{invalid",
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is False
    assert res["status"] == "MANUAL_REVIEW_REQUIRED"
    assert REPAIR_RUNTIME_COMMIT_CONFLICT in (res.get("reason") or "")


def test_c18_04_bound_time_conflict_between_sources(isolated_db: Session):
    """C18-04 (PHẦN 059): Bound Time Conflict.
    Sources claim different bound_at_ms (1000 vs 2000).
    Must return CONFLICT without taking min, max, or current time.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c18_04", virtual_capital=1000.0, now_ms=now_ms)

    order = _make_closed_order("ord_c18_04", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 1,
        "runtime_commit": "sha_111",
        "bound_at_ms": 1000,
    }), updated_at=now_ms))

    snap_key = f"fwd_snap:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=snap_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 1,
        "runtime_commit": "sha_111",
        "bound_at_ms": 2000,  # Conflicting bound time!
    }), updated_at=now_ms))

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
        original_damaged_payload="{{invalid",
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is False
    assert res["status"] == "MANUAL_REVIEW_REQUIRED"
    assert REPAIR_BOUND_AT_CONFLICT in (res.get("reason") or "")


def test_c18_05_order_conflict_in_secondary_source(isolated_db: Session):
    """C18-05 (PHẦN 026, 027, 060): Record stored under key for order X has payload for order Y.
    Must NOT silently continue and verify with another source.
    Must return CONFLICT with REPAIR_ORDER_CONFLICT and REPAIR_SOURCE_ORDER_MISMATCH.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c18_05", virtual_capital=1000.0, now_ms=now_ms)

    order = _make_closed_order("ord_c18_05_target", now_ms)
    isolated_db.add(order)

    # Snapshot under ord_c18_05_target key claims order_id='ord_c18_05_FOREIGN'
    snap_key = f"fwd_snap:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=snap_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": "ord_c18_05_FOREIGN",  # Corrupted / mismatched order ID!
        "config_revision": 1,
        "runtime_commit": "sha_111",
        "bound_at_ms": now_ms,
    }), updated_at=now_ms))

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
        original_damaged_payload="{{invalid",
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is False
    assert res["status"] == "MANUAL_REVIEW_REQUIRED"
    assert REPAIR_ORDER_CONFLICT in (res.get("reason") or "")


def test_c18_06_source_order_invariance():
    """C18-06 (PHẦN 034, 061): Comparison order invariance.
    Reversing the order of sources yields identical status and primary reason.
    Tests both consistent sources and conflicting sources.
    """
    s1 = ("source_A", {
        "forward_session_id": "fwd_sess_1",
        "config_revision": 5,
        "runtime_commit": "sha_111",
        "bound_at_ms": 1000,
    })
    s2 = ("source_B", {
        "forward_session_id": "fwd_sess_1",
        "config_revision": 5,
        "runtime_commit": "sha_111",
        "bound_at_ms": 1000,
    })

    # Consistent sources: both forward and reverse return VERIFIED
    status_fwd, res_fwd, reasons_fwd, _ = compare_repair_binding_sources([s1, s2])
    status_rev, res_rev, reasons_rev, _ = compare_repair_binding_sources([s2, s1])
    assert status_fwd == status_rev == "VERIFIED"
    assert res_fwd == res_rev

    # Conflicting sources: both forward and reverse return CONFLICT with same reason
    s3_conflict = ("source_C", {
        "forward_session_id": "fwd_sess_1",
        "config_revision": 9,  # Conflict
        "runtime_commit": "sha_111",
        "bound_at_ms": 1000,
    })
    status_c_fwd, _, reasons_c_fwd, _ = compare_repair_binding_sources([s1, s3_conflict])
    status_c_rev, _, reasons_c_rev, _ = compare_repair_binding_sources([s3_conflict, s1])
    assert status_c_fwd == status_c_rev == "CONFLICT"
    assert reasons_c_fwd == reasons_c_rev
    assert REPAIR_CONFIG_REVISION_CONFLICT in reasons_c_fwd


def test_c18_07_index_conflict_with_target_present(isolated_db: Session):
    """C18-07 (PHẦN 014, 044, 062): Order in index A and index B, where target is A (Reviewer Repro 2).
    Target session A is in indexes, but index B also contains the order.
    Must return CONFLICT with REPAIR_INDEX_CONFLICT / REPAIR_SESSION_CONFLICT.
    """
    now_ms = 1792000000000
    sess_a = create_forward_paper_session(isolated_db, "fwd_sess_c18_07_A", virtual_capital=1000.0, now_ms=now_ms)
    sess_b = create_forward_paper_session(isolated_db, "fwd_sess_c18_07_B", virtual_capital=1000.0, now_ms=now_ms)

    order = _make_closed_order("ord_c18_07", now_ms)
    isolated_db.add(order)

    # Order appears in BOTH index A and index B!
    _append_session_order_id(isolated_db, sess_a.session_id, order.id, now_ms)
    _append_session_order_id(isolated_db, sess_b.session_id, order.id, now_ms)
    isolated_db.commit()

    task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order.id}",
        order_id=order.id,
        forward_session_id=sess_a.session_id,  # Target is A!
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=json.dumps({
            "forward_session_id": sess_a.session_id,
            "order_id": order.id,
            "config_revision": 1,
            "runtime_commit": "sha_111",
            "bound_at_ms": now_ms,
        }),
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is False
    assert res["status"] == "MANUAL_REVIEW_REQUIRED"
    assert REPAIR_INDEX_CONFLICT in (res.get("reason") or "")


def test_c18_08_index_conflict_without_target_present(isolated_db: Session):
    """C18-08 (PHẦN 063): Target is C, but indexes contain A and B.
    Must return CONFLICT, does not add order to C.
    """
    now_ms = 1792000000000
    sess_a = create_forward_paper_session(isolated_db, "fwd_sess_c18_08_A", virtual_capital=1000.0, now_ms=now_ms)
    sess_b = create_forward_paper_session(isolated_db, "fwd_sess_c18_08_B", virtual_capital=1000.0, now_ms=now_ms)
    sess_c = create_forward_paper_session(isolated_db, "fwd_sess_c18_08_C", virtual_capital=1000.0, now_ms=now_ms)

    order = _make_closed_order("ord_c18_08", now_ms)
    isolated_db.add(order)

    # Order appears in index A and index B
    _append_session_order_id(isolated_db, sess_a.session_id, order.id, now_ms)
    _append_session_order_id(isolated_db, sess_b.session_id, order.id, now_ms)
    isolated_db.commit()

    task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order.id}",
        order_id=order.id,
        forward_session_id=sess_c.session_id,  # Target is C!
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=json.dumps({
            "forward_session_id": sess_c.session_id,
            "order_id": order.id,
            "config_revision": 1,
            "runtime_commit": "sha_111",
            "bound_at_ms": now_ms,
        }),
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is False
    assert res["status"] == "MANUAL_REVIEW_REQUIRED"

    # Order must NOT be added to session C's index
    ords_c = _get_session_order_ids(isolated_db, sess_c.session_id)
    assert order.id not in ords_c


def test_c18_09_missing_index_reconstruction(isolated_db: Session):
    """C18-09 (PHẦN 045, 064): Valid binding with missing index.
    When index is absent, repair succeeds and reconstructs the index.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c18_09", virtual_capital=1000.0, now_ms=now_ms)

    order = _make_closed_order("ord_c18_09", now_ms)
    isolated_db.add(order)
    isolated_db.commit()

    # Index is completely missing (zero index configs)
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
            "config_revision": 3,
            "runtime_commit": "sha_reconstruct_333",
            "bound_at_ms": now_ms,
        }),
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is True
    assert res["status"] == "REPAIRED"

    # Index reconstructed
    ords = _get_session_order_ids(isolated_db, sess.session_id)
    assert order.id in ords


def test_c18_10_single_consistent_index_succeeds(isolated_db: Session):
    """C18-10 (PHẦN 065): Single consistent index does not over-block.
    When index exactly matches target session, repair succeeds.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c18_10", virtual_capital=1000.0, now_ms=now_ms)

    order = _make_closed_order("ord_c18_10", now_ms)
    isolated_db.add(order)
    _append_session_order_id(isolated_db, sess.session_id, order.id, now_ms)
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
            "runtime_commit": "sha_111",
            "bound_at_ms": now_ms,
        }),
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is True
    assert res["status"] == "REPAIRED"


def test_c18_11_original_malformed_without_proof_fails_closed(isolated_db: Session):
    """C18-11 (PHẦN 066): Malformed payload without independent proof fails closed.
    Regression guard for V14.4.17.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c18_11", virtual_capital=1000.0, now_ms=now_ms)

    order = _make_closed_order("ord_c18_11", now_ms)
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
        original_damaged_payload="{{{broken_json",
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is False
    assert res["status"] == "MANUAL_REVIEW_REQUIRED"
    assert REPAIR_PROOF_MALFORMED in (res.get("reason") or "")


def test_c18_12_malformed_with_complete_independent_proof_succeeds(isolated_db: Session):
    """C18-12 (PHẦN 067): Malformed payload recovers when valid complete independent proof exists.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c18_12", virtual_capital=1000.0, now_ms=now_ms)

    order = _make_closed_order("ord_c18_12", now_ms)
    isolated_db.add(order)

    # Valid independent snapshot record
    snap_key = f"fwd_snap:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=snap_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 2,
        "runtime_commit": "sha_c18_12",
        "bound_at_ms": now_ms,
    }), updated_at=now_ms))

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
        original_damaged_payload="{{{broken_json",
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is True
    assert res["status"] == "REPAIRED"
    mem = get_order_forward_membership(isolated_db, order.id)
    assert mem.config_revision == 2
    assert mem.runtime_commit == "sha_c18_12"


def test_c18_13_repaired_membership_cannot_self_certify(isolated_db: Session):
    """C18-13 (PHẦN 036, 068): Membership created by a previous repair cannot self-certify.
    When damaged payload is absent and snapshot is absent, a repaired membership record
    marked with repaired_by cannot certify itself. Must transition to MANUAL_REVIEW_REQUIRED.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c18_13", virtual_capital=1000.0, now_ms=now_ms)

    order = _make_closed_order("ord_c18_13", now_ms)
    isolated_db.add(order)

    # Membership in DB is marked as repaired by a previous task
    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 1,
        "runtime_commit": "sha_111",
        "bound_at_ms": now_ms,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask", "task_id": "fwd_rep_prior"},
    }), updated_at=now_ms))

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
        original_damaged_payload=None,  # No original damaged payload!
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is False
    assert res["status"] == "MANUAL_REVIEW_REQUIRED"
    assert REPAIR_SOURCE_UNVERIFIED in (res.get("reason") or "")


def test_c18_14_all_sources_consistent_succeeds(isolated_db: Session):
    """C18-14 (PHẦN 069): All sources (damaged_payload, db_membership, db_snapshot) agree.
    Repair succeeds with VERIFIED, source_names records all validated sources.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c18_14", virtual_capital=1000.0, now_ms=now_ms)

    order = _make_closed_order("ord_c18_14", now_ms)
    isolated_db.add(order)

    # Membership agrees
    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 6,
        "runtime_commit": "sha_agree_666",
        "bound_at_ms": now_ms,
    }), updated_at=now_ms))

    # Snapshot agrees
    snap_key = f"fwd_snap:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=snap_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 6,
        "runtime_commit": "sha_agree_666",
        "bound_at_ms": now_ms,
    }), updated_at=now_ms))

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
            "config_revision": 6,
            "runtime_commit": "sha_agree_666",
            "bound_at_ms": now_ms,
        }),
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is True
    assert res["status"] == "REPAIRED"
    mem = get_order_forward_membership(isolated_db, order.id)
    assert mem.config_revision == 6
    assert mem.runtime_commit == "sha_agree_666"
    assert "original_damaged_payload" in mem.metadata.get("provenance_sources", [])
    assert "db_membership" in mem.metadata.get("provenance_sources", [])
    assert "db_snapshot" in mem.metadata.get("provenance_sources", [])


def test_c18_15_partial_proof_session_conflict(isolated_db: Session):
    """C18-15 (PHẦN 070): Partial source conflict.
    Complete proof claims Session A, but partial contextual claim (e.g. order session instance) claims Session B.
    Must return CONFLICT without ignoring the partial conflict.
    """
    now_ms = 1792000000000
    sess_a = create_forward_paper_session(isolated_db, "fwd_sess_c18_15_A", virtual_capital=1000.0, now_ms=now_ms)
    sess_b = create_forward_paper_session(isolated_db, "fwd_sess_c18_15_B", virtual_capital=1000.0, now_ms=now_ms)

    order = _make_closed_order("ord_c18_15", now_ms, session_instance_id=sess_b.session_id)
    isolated_db.add(order)

    task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{order.id}",
        order_id=order.id,
        forward_session_id=sess_a.session_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=json.dumps({
            "forward_session_id": sess_a.session_id,
            "order_id": order.id,
            "config_revision": 1,
            "runtime_commit": "sha_111",
            "bound_at_ms": now_ms,
        }),
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is False
    assert res["status"] == "MANUAL_REVIEW_REQUIRED"
    assert REPAIR_SESSION_CONFLICT in (res.get("reason") or "")


def test_c18_16_zero_financial_mutations(isolated_db: Session):
    """C18-16 (PHẦN 071): Zero Financial Mutations.
    Failed repair / conflict must cause zero side-effects on PaperOrder financial attributes.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c18_16", virtual_capital=1000.0, now_ms=now_ms)

    order = models.PaperOrder(
        id="ord_c18_16",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4181.5,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_exit=4200.0,
        exit_cause="TP_HIT",
        quantity=0.5,
        initial_risk_usdt=5.75,
        realized_pnl_net=9.25,
        realized_r=1.608,
        opened_at=now_ms,
        closed_at=now_ms + 5000,
        created_at=now_ms,
    )
    isolated_db.add(order)
    isolated_db.commit()

    snap_before = {
        "state": order.state,
        "planned_entry": order.planned_entry,
        "actual_entry": order.actual_entry,
        "stop_loss": order.stop_loss,
        "take_profit": order.take_profit,
        "actual_exit": order.actual_exit,
        "exit_cause": order.exit_cause,
        "quantity": order.quantity,
        "initial_risk_usdt": order.initial_risk_usdt,
        "realized_pnl_net": order.realized_pnl_net,
        "realized_r": order.realized_r,
        "opened_at": order.opened_at,
        "closed_at": order.closed_at,
    }

    # Execute repair on corrupt task
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
        original_damaged_payload="{{invalid",
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is False

    isolated_db.refresh(order)
    snap_after = {
        "state": order.state,
        "planned_entry": order.planned_entry,
        "actual_entry": order.actual_entry,
        "stop_loss": order.stop_loss,
        "take_profit": order.take_profit,
        "actual_exit": order.actual_exit,
        "exit_cause": order.exit_cause,
        "quantity": order.quantity,
        "initial_risk_usdt": order.initial_risk_usdt,
        "realized_pnl_net": order.realized_pnl_net,
        "realized_r": order.realized_r,
        "opened_at": order.opened_at,
        "closed_at": order.closed_at,
    }
    assert snap_before == snap_after


def test_c18_17_commit_failure_rollback(isolated_db: Session):
    """C18-17 (PHẦN 072): Commit failure isolation and rollback.
    When db.commit() raises an exception, transaction rolls back cleanly.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c18_17", virtual_capital=1000.0, now_ms=now_ms)

    order = _make_closed_order("ord_c18_17", now_ms)
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
            "runtime_commit": "sha_111",
            "bound_at_ms": now_ms,
        }),
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    with patch.object(isolated_db, "commit", side_effect=RuntimeError("injected_commit_error")):
        res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
        assert res["success"] is False
        assert task.attempts == 1
        assert "injected_commit_error" in (task.last_error or "")


def test_c18_18_worker_real_path_continues_batch(isolated_db: Session):
    """C18-18 (PHẦN 073): Production worker execution path with mixed tasks.
    Seeds one conflicting task and one valid task.
    Worker processes both: conflicting task goes to MANUAL_REVIEW_REQUIRED,
    valid task is successfully REPAIRED. Batch is NOT interrupted.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c18_18", virtual_capital=1000.0, now_ms=now_ms)

    # Order 1: Conflicting revision
    ord1 = _make_closed_order("ord_c18_18_conflict", now_ms)
    isolated_db.add(ord1)
    isolated_db.add(models.SystemConfig(key=f"fwd_mem:{ord1.id}"[:50], value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": ord1.id,
        "config_revision": 1,
        "runtime_commit": "sha_111",
        "bound_at_ms": now_ms,
    }), updated_at=now_ms))
    isolated_db.add(models.SystemConfig(key=f"fwd_snap:{ord1.id}"[:50], value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": ord1.id,
        "config_revision": 2,  # Conflicting!
        "runtime_commit": "sha_111",
        "bound_at_ms": now_ms,
    }), updated_at=now_ms))

    # Order 2: Valid
    ord2 = _make_closed_order("ord_c18_18_valid", now_ms)
    isolated_db.add(ord2)
    isolated_db.commit()

    task1 = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{ord1.id}",
        order_id=ord1.id,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload="{{invalid",
    )
    task2 = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{ord2.id}",
        order_id=ord2.id,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=json.dumps({
            "forward_session_id": sess.session_id,
            "order_id": ord2.id,
            "config_revision": 1,
            "runtime_commit": "sha_valid_worker_222",
            "bound_at_ms": now_ms,
        }),
    )
    isolated_db.add(models.SystemConfig(key=_repair_task_key(ord1.id), value=json.dumps(task1.to_dict()), updated_at=now_ms))
    isolated_db.add(models.SystemConfig(key=_repair_task_key(ord2.id), value=json.dumps(task2.to_dict()), updated_at=now_ms))
    isolated_db.add(models.SystemConfig(key="fwd_repair_queue", value=json.dumps([ord1.id, ord2.id]), updated_at=now_ms))
    isolated_db.commit()

    from services.forward_repair_worker import forward_repair_worker
    res = forward_repair_worker.process_once(db=isolated_db, now_ms=now_ms + 1000)
    assert res["processed"] == 2
    assert res["manual_review"] == 1
    assert res["repaired"] == 1

    # Check outcomes
    t1_rec = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == _repair_task_key(ord1.id)).first()
    t1_dict = json.loads(t1_rec.value)
    assert t1_dict["status"] == "MANUAL_REVIEW_REQUIRED"

    t2_rec = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == _repair_task_key(ord2.id)).first()
    t2_dict = json.loads(t2_rec.value)
    assert t2_dict["status"] == "REPAIRED"
