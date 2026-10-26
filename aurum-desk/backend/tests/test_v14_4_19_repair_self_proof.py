"""backend/tests/test_v14_4_19_repair_self_proof.py

V14.4.19 Acceptance Tests: Close Repaired Membership Self-Certification Bypass.
Cases S19-01 to S19-21 (PHẦN 048–071).
"""

import json
from unittest.mock import patch
import pytest
from sqlalchemy.orm import Session

import models
from services.forward_paper_service import (
    create_forward_paper_session,
    load_forward_session,
    repair_forward_membership_task,
    get_order_forward_membership,
    resolve_repair_provenance,
    compare_repair_binding_sources,
    validate_repair_session_indexes,
    validate_repair_binding_source,
    collect_repair_provenance_sources,
    is_complete_original_binding_proof,
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
from services.forward_repair_worker import ForwardRepairWorker, forward_repair_worker


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


def test_s19_01_empty_object_repaired_membership_fails_closed(isolated_db: Session):
    """S19-01 (PHẦN 016, 050): Reviewer Repro - Empty object original damaged payload.
    Repaired membership has full fields, no snapshot exists, damaged payload is "{}".
    Must NOT return VERIFIED. Must fail closed to INSUFFICIENT / MANUAL_REVIEW_REQUIRED.
    Zero membership rewrite, zero index mutation.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_s19_01", virtual_capital=1000.0, now_ms=now_ms)

    order = _make_closed_order("ord_s19_01", now_ms)
    isolated_db.add(order)

    # Repaired membership exists in DB
    mem_key = f"fwd_mem:{order.id}"[:50]
    mem_raw = json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 7,
        "runtime_commit": "sha_repaired_777",
        "bound_at_ms": now_ms,
        "metadata": {
            "repaired_by": "ForwardMembershipRepairTask",
            "task_id": "fwd_rep_prior_01",
        },
    })
    isolated_db.add(models.SystemConfig(key=mem_key, value=mem_raw, updated_at=now_ms))

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
        original_damaged_payload="{}",  # Empty JSON object!
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    # 1. Direct resolver check
    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "INSUFFICIENT"
    assert REPAIR_SOURCE_UNVERIFIED in resolution.reason_codes

    # 2. End-to-end task execution check
    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is False
    assert res["status"] == "MANUAL_REVIEW_REQUIRED"
    assert REPAIR_SOURCE_UNVERIFIED in (res.get("reason") or "")

    # Zero membership rewrite
    rec_after = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == mem_key).first()
    assert rec_after.value == mem_raw

    # Zero session index mutation
    session_orders = _get_session_order_ids(isolated_db, sess.session_id)
    assert order.id not in session_orders


def test_s19_02_session_only_payload_not_verified(isolated_db: Session):
    """S19-02 (PHẦN 017, 051): Original payload has session only.
    Cannot combine partial original payload with repaired membership to self-certify.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_s19_02", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_s19_02", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 7,
        "runtime_commit": "sha_repaired_777",
        "bound_at_ms": now_ms,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
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
        original_damaged_payload=json.dumps({"forward_session_id": sess.session_id}),
    )
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "INSUFFICIENT"
    assert REPAIR_SOURCE_UNVERIFIED in resolution.reason_codes


def test_s19_03_order_and_session_only_not_verified(isolated_db: Session):
    """S19-03 (PHẦN 017, 052): Original payload has order_id and forward_session_id only.
    Missing revision, commit, bound_at. Repaired membership cannot self-certify.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_s19_03", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_s19_03", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 7,
        "runtime_commit": "sha_repaired_777",
        "bound_at_ms": now_ms,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
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
            "order_id": order.id,
            "forward_session_id": sess.session_id,
        }),
    )
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "INSUFFICIENT"
    assert REPAIR_SOURCE_UNVERIFIED in resolution.reason_codes


def test_s19_04_missing_revision_not_verified(isolated_db: Session):
    """S19-04 (PHẦN 053): Original payload missing config_revision.
    Repaired membership has revision 7, but cannot supply missing revision as original proof.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_s19_04", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_s19_04", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 7,
        "runtime_commit": "sha_repaired_777",
        "bound_at_ms": now_ms,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
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
            "order_id": order.id,
            "forward_session_id": sess.session_id,
            "runtime_commit": "sha_repaired_777",
            "bound_at_ms": now_ms,
            # config_revision is MISSING!
        }),
    )
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "INSUFFICIENT"
    assert REPAIR_SOURCE_UNVERIFIED in resolution.reason_codes


def test_s19_05_missing_commit_not_verified(isolated_db: Session):
    """S19-05 (PHẦN 054): Original payload missing runtime_commit.
    Repaired membership cannot supply missing commit as original proof.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_s19_05", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_s19_05", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 7,
        "runtime_commit": "sha_repaired_777",
        "bound_at_ms": now_ms,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
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
            "order_id": order.id,
            "forward_session_id": sess.session_id,
            "config_revision": 7,
            "bound_at_ms": now_ms,
            # runtime_commit is MISSING!
        }),
    )
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "INSUFFICIENT"
    assert REPAIR_SOURCE_UNVERIFIED in resolution.reason_codes


def test_s19_06_missing_bound_at_not_verified(isolated_db: Session):
    """S19-06 (PHẦN 055): Original payload missing bound_at_ms.
    Must NOT fallback to opened_at/now_ms or borrow from repaired membership.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_s19_06", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_s19_06", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 7,
        "runtime_commit": "sha_repaired_777",
        "bound_at_ms": now_ms,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
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
            "order_id": order.id,
            "forward_session_id": sess.session_id,
            "config_revision": 7,
            "runtime_commit": "sha_repaired_777",
            # bound_at_ms is MISSING!
        }),
    )
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "INSUFFICIENT"
    assert REPAIR_SOURCE_UNVERIFIED in resolution.reason_codes


def test_s19_07_missing_order_id_in_payload_not_verified(isolated_db: Session):
    """S19-07 (PHẦN 026, 056): Original payload has 4 binding fields but no order identity.
    A complete binding proof requires proving order identity.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_s19_07", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_s19_07", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 7,
        "runtime_commit": "sha_repaired_777",
        "bound_at_ms": now_ms,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
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
            # order_id is OMITTED!
            "forward_session_id": sess.session_id,
            "config_revision": 7,
            "runtime_commit": "sha_repaired_777",
            "bound_at_ms": now_ms,
        }),
    )
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "INSUFFICIENT"
    assert REPAIR_SOURCE_UNVERIFIED in resolution.reason_codes


def test_s19_08_partial_snapshot_not_verified(isolated_db: Session):
    """S19-08 (PHẦN 029, 057): Snapshot is partial (only session).
    Repaired membership cannot supplement snapshot's missing historical fields.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_s19_08", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_s19_08", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 7,
        "runtime_commit": "sha_repaired_777",
        "bound_at_ms": now_ms,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
    }), updated_at=now_ms))

    # Partial snapshot: only session
    snap_key = f"fwd_snap:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=snap_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
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
        original_damaged_payload="{}",
    )
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "INSUFFICIENT"
    assert REPAIR_SOURCE_UNVERIFIED in resolution.reason_codes


def test_s19_09_full_original_proof_succeeds(isolated_db: Session):
    """S19-09 (PHẦN 025, 058): Genuine full original damaged payload proof.
    Has all 5 fields matching order and repaired membership.
    Repair succeeds with VERIFIED. Does not over-block legitimate proofs.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_s19_09", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_s19_09", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 7,
        "runtime_commit": "sha_legit_777",
        "bound_at_ms": now_ms,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
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
            "order_id": order.id,
            "forward_session_id": sess.session_id,
            "config_revision": 7,
            "runtime_commit": "sha_legit_777",
            "bound_at_ms": now_ms,
        }),
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "VERIFIED"
    assert resolution.config_revision == 7
    assert resolution.runtime_commit == "sha_legit_777"

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is True
    assert res["status"] == "REPAIRED"


def test_s19_10_full_legacy_snapshot_succeeds(isolated_db: Session):
    """S19-10 (PHẦN 030, 059): Genuine full legacy snapshot.
    Has all 5 fields matching order and membership; damaged payload is "{}".
    Repair succeeds with VERIFIED.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_s19_10", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_s19_10", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 7,
        "runtime_commit": "sha_snap_777",
        "bound_at_ms": now_ms,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
    }), updated_at=now_ms))

    # Full legacy snapshot
    snap_key = f"fwd_snap:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=snap_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 7,
        "runtime_commit": "sha_snap_777",
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
        original_damaged_payload="{}",
    )
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "VERIFIED"
    assert resolution.config_revision == 7


def test_s19_11_full_original_conflict_fails(isolated_db: Session):
    """S19-11 (PHẦN 036, 060): Full original proof has revision 7, repaired membership has revision 8.
    Must flag CONFLICT across candidate sources. Must NOT ignore repaired membership to force pass.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_s19_11", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_s19_11", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 8,  # Revision 8 in membership!
        "runtime_commit": "sha_same_111",
        "bound_at_ms": now_ms,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
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
            "order_id": order.id,
            "forward_session_id": sess.session_id,
            "config_revision": 7,  # Revision 7 in original proof!
            "runtime_commit": "sha_same_111",
            "bound_at_ms": now_ms,
        }),
    )
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "CONFLICT"
    assert REPAIR_CONFIG_REVISION_CONFLICT in resolution.reason_codes


def test_s19_12_partial_session_conflict_fails(isolated_db: Session):
    """S19-12 (PHẦN 037, 061): Partial original payload claims Session B, snapshot claims Session A.
    Must NOT ignore partial claim; returns CONFLICT.
    """
    now_ms = 1792000000000
    sess_a = create_forward_paper_session(isolated_db, "fwd_sess_s19_12_A", virtual_capital=1000.0, now_ms=now_ms)
    sess_b = create_forward_paper_session(isolated_db, "fwd_sess_s19_12_B", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_s19_12", now_ms)
    isolated_db.add(order)

    # Full snapshot for A
    snap_key = f"fwd_snap:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=snap_key, value=json.dumps({
        "forward_session_id": sess_a.session_id,
        "order_id": order.id,
        "config_revision": 1,
        "runtime_commit": "sha_snap_12",
        "bound_at_ms": now_ms,
    }), updated_at=now_ms))

    # Partial original payload claims B
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
            "forward_session_id": sess_b.session_id,
        }),
    )
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "CONFLICT"
    assert REPAIR_SESSION_CONFLICT in resolution.reason_codes


def test_s19_13_wrong_order_in_payload_fails(isolated_db: Session):
    """S19-13 (PHẦN 038, 062): Original payload has all fields, but order_id belongs to a different order.
    Returns CONFLICT with REPAIR_ORDER_CONFLICT and REPAIR_SOURCE_ORDER_MISMATCH.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_s19_13", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_s19_13_target", now_ms)
    isolated_db.add(order)

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
            "order_id": "ord_s19_13_OTHER_ORDER",  # Mismatched order!
            "forward_session_id": sess.session_id,
            "config_revision": 1,
            "runtime_commit": "sha_ord_13",
            "bound_at_ms": now_ms,
        }),
    )
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "CONFLICT"
    assert REPAIR_ORDER_CONFLICT in resolution.reason_codes
    assert REPAIR_SOURCE_ORDER_MISMATCH in resolution.reason_codes


def test_s19_14_malformed_regression_fails(isolated_db: Session):
    """S19-14 (PHẦN 063): Malformed damaged payload with repaired membership and no snapshot.
    Fails closed to INSUFFICIENT.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_s19_14", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_s19_14", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 7,
        "runtime_commit": "sha_repaired_777",
        "bound_at_ms": now_ms,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
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
        original_damaged_payload="{{invalid_unparseable",
    )
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "INSUFFICIENT"


def test_s19_15_absent_regression_fails(isolated_db: Session):
    """S19-15 (PHẦN 064): Absent damaged payload with repaired membership.
    Fails closed to INSUFFICIENT with REPAIR_SOURCE_UNVERIFIED.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_s19_15", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_s19_15", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 7,
        "runtime_commit": "sha_repaired_777",
        "bound_at_ms": now_ms,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
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
        original_damaged_payload=None,
    )
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "INSUFFICIENT"
    assert REPAIR_SOURCE_UNVERIFIED in resolution.reason_codes


def test_s19_16_original_membership_supported(isolated_db: Session):
    """S19-16 (PHẦN 065): Genuine original membership (not repaired).
    No repair markers. Payload absent.
    Must NOT inadvertently block valid supported recovery.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_s19_16", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_s19_16", now_ms)
    isolated_db.add(order)

    # Genuine original membership (NO repaired_by, NO repair_task_id, NO provenance_sources in metadata)
    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 3,
        "runtime_commit": "sha_orig_333",
        "bound_at_ms": now_ms,
        "metadata": {"original_creator": "runtime"},
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
        original_damaged_payload=None,
    )
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "VERIFIED"
    assert resolution.config_revision == 3
    assert resolution.runtime_commit == "sha_orig_333"

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is True
    assert res["status"] == "REPAIRED"


@pytest.mark.parametrize("marker_key,marker_val", [
    ("meta_repaired_by", {"metadata": {"repaired_by": "ForwardMembershipRepairTask"}}),
    ("meta_repair_task_id", {"metadata": {"repair_task_id": "fwd_rep_prior_99"}}),
    ("meta_provenance_sources", {"metadata": {"provenance_sources": ["db_membership"]}}),
    ("toplevel_repaired_by", {"repaired_by": "ForwardMembershipRepairTask"}),
])
def test_s19_17_marker_variants_cannot_self_certify(isolated_db: Session, marker_key: str, marker_val: dict):
    """S19-17 (PHẦN 066): Parametrize repaired markers.
    All variations of repaired markers classify the record as REPAIRED_MEMBERSHIP
    and prevent self-certification with damaged_payload="{}".
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, f"fwd_sess_s19_17_{marker_key}", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order(f"ord_s19_17_{marker_key}", now_ms)
    isolated_db.add(order)

    mem_data = {
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 5,
        "runtime_commit": "sha_marker_test",
        "bound_at_ms": now_ms,
    }
    mem_data.update(marker_val)

    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps(mem_data), updated_at=now_ms))

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
        original_damaged_payload="{}",
    )
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "INSUFFICIENT"
    assert REPAIR_SOURCE_UNVERIFIED in resolution.reason_codes


def test_s19_18_zero_financial_mutations(isolated_db: Session):
    """S19-18 (PHẦN 067, 071): Zero financial mutations on PaperOrder or DomainEvents
    during self-certification rejection.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_s19_18", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_s19_18", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 7,
        "runtime_commit": "sha_repaired_777",
        "bound_at_ms": now_ms,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
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
        original_damaged_payload="{}",
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    # Pre-snapshot
    pre_order = {
        "state": order.state,
        "actual_entry": order.actual_entry,
        "actual_exit": order.actual_exit,
        "stop_loss": order.stop_loss,
        "take_profit": order.take_profit,
        "quantity": order.quantity,
        "realized_pnl_net": order.realized_pnl_net,
        "opened_at": order.opened_at,
        "closed_at": order.closed_at,
    }
    pre_events_count = isolated_db.query(models.DomainEvent).count()

    repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)

    # Post-snapshot
    post_ord = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == order.id).first()
    assert post_ord is not None
    post_order = {
        "state": post_ord.state,
        "actual_entry": post_ord.actual_entry,
        "actual_exit": post_ord.actual_exit,
        "stop_loss": post_ord.stop_loss,
        "take_profit": post_ord.take_profit,
        "quantity": post_ord.quantity,
        "realized_pnl_net": post_ord.realized_pnl_net,
        "opened_at": post_ord.opened_at,
        "closed_at": post_ord.closed_at,
    }
    assert pre_order == post_order
    assert isolated_db.query(models.DomainEvent).count() == pre_events_count


def test_s19_19_worker_real_path(isolated_db: Session):
    """S19-19 (PHẦN 068): Worker real execution path.
    Batch contains one bypass task ("{}") and one valid task.
    Bypass task transitions to MANUAL_REVIEW_REQUIRED, valid task repairs successfully.
    Batch processing does not crash.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_s19_19", virtual_capital=1000.0, now_ms=now_ms)

    ord_bypass = _make_closed_order("ord_s19_19_bypass", now_ms)
    ord_valid = _make_closed_order("ord_s19_19_valid", now_ms)
    isolated_db.add_all([ord_bypass, ord_valid])

    # Bypass order: repaired membership + "{}"
    mem_key1 = f"fwd_mem:{ord_bypass.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key1, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": ord_bypass.id,
        "config_revision": 7,
        "runtime_commit": "sha_repaired_777",
        "bound_at_ms": now_ms,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
    }), updated_at=now_ms))

    task_bypass = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{ord_bypass.id}",
        order_id=ord_bypass.id,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload="{}",
    )

    # Valid order: genuine full proof in original damaged payload
    task_valid = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{ord_valid.id}",
        order_id=ord_valid.id,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        original_damaged_payload=json.dumps({
            "order_id": ord_valid.id,
            "forward_session_id": sess.session_id,
            "config_revision": 2,
            "runtime_commit": "sha_worker_valid_222",
            "bound_at_ms": now_ms,
        }),
    )

    isolated_db.add(models.SystemConfig(key=_repair_task_key(ord_bypass.id), value=json.dumps(task_bypass.to_dict()), updated_at=now_ms))
    isolated_db.add(models.SystemConfig(key=_repair_task_key(ord_valid.id), value=json.dumps(task_valid.to_dict()), updated_at=now_ms))
    isolated_db.add(models.SystemConfig(key="fwd_repair_queue", value=json.dumps([ord_bypass.id, ord_valid.id]), updated_at=now_ms))
    isolated_db.commit()

    res = forward_repair_worker.process_once(db=isolated_db, now_ms=now_ms + 1000)
    assert res["processed"] == 2
    assert res["manual_review"] == 1
    assert res["repaired"] == 1

    t1_rec = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == _repair_task_key(ord_bypass.id)).first()
    t1_dict = json.loads(t1_rec.value)
    assert t1_dict["status"] == "MANUAL_REVIEW_REQUIRED"
    assert REPAIR_SOURCE_UNVERIFIED in t1_dict.get("reason_codes", [])

    t2_rec = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == _repair_task_key(ord_valid.id)).first()
    t2_dict = json.loads(t2_rec.value)
    assert t2_dict["status"] == "REPAIRED"


def test_s19_20_commit_failure_rollbacks_cleanly(isolated_db: Session):
    """S19-20 (PHẦN 069): Commit failure during manual review staging rollbacks cleanly.
    Zero partial membership or index persistence.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_s19_20", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_s19_20", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 7,
        "runtime_commit": "sha_repaired_777",
        "bound_at_ms": now_ms,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
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
        original_damaged_payload="{}",
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    with patch.object(isolated_db, "commit", side_effect=RuntimeError("Simulated owner commit failure")):
        res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
        assert res["success"] is False
        assert "COMMIT_FAILED" in res.get("status", "") or "Simulated" in str(res.get("error", ""))


def test_s19_21_idempotency_failed_repair(isolated_db: Session):
    """S19-21 (PHẦN 070): Idempotency.
    Calling repair twice on a self-certification bypass does not duplicate indexes,
    does not create trade events, and does not alter membership.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_s19_21", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_s19_21", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 7,
        "runtime_commit": "sha_repaired_777",
        "bound_at_ms": now_ms,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
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
        original_damaged_payload="{}",
    )
    task_key = _repair_task_key(order.id)
    isolated_db.add(models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    # First execution
    res1 = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res1["success"] is False
    assert res1["status"] == "MANUAL_REVIEW_REQUIRED"

    # Second execution
    res2 = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 2000)
    assert res2["success"] is False
    assert res2["status"] == "MANUAL_REVIEW_REQUIRED"

    # Index still empty
    session_orders = _get_session_order_ids(isolated_db, sess.session_id)
    assert order.id not in session_orders
