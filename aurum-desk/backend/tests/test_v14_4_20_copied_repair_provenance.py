"""backend/tests/test_v14_4_20_copied_repair_provenance.py

V14.4.20 Acceptance Tests: Prevent Copied Repaired Data from Becoming Original Binding Proof.
Cases C20-01 to C20-22 (PHẦN 050–073).
"""

import copy
import json
from unittest.mock import patch
import pytest
from sqlalchemy.orm import Session

import models
from services.forward_paper_service import (
    create_forward_paper_session,
    stage_forward_repair_task,
    repair_forward_membership_task,
    get_order_forward_membership,
    resolve_repair_provenance,
    collect_repair_provenance_sources,
    classify_repair_binding_payload_origin,
    is_complete_original_binding_proof,
    RepairProvenanceSource,
    ForwardMembershipRepairTask,
    ForwardOrderMembership,
    _repair_task_key,
    _get_session_order_ids,
    REPAIR_PROOF_MISSING,
    REPAIR_PROOF_MALFORMED,
    REPAIR_SESSION_NOT_FOUND,
    REPAIR_SESSION_CONFLICT,
    REPAIR_ORDER_CONFLICT,
    REPAIR_CONFIG_REVISION_MISSING,
    REPAIR_CONFIG_REVISION_CONFLICT,
    REPAIR_INDEX_CONFLICT,
    REPAIR_SOURCE_UNVERIFIED,
    REPAIR_SOURCE_ORDER_MISMATCH,
)
from services.forward_repair_worker import forward_repair_worker


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


def test_c20_01_copied_repaired_membership_staging_fails_closed(isolated_db: Session):
    """C20-01 (PHẦN 050, 051): Reviewer Production Repro.
    1. Seed DB membership with metadata.repaired_by.
    2. stage_forward_repair_task() copies membership into original_damaged_payload.
    3. Owner commit and reload task from DB.
    4. Resolver and repair must NOT treat the copy as original proof -> MANUAL_REVIEW_REQUIRED.
    Zero membership rewrite, zero index mutation.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c20_01", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_c20_01", now_ms)
    isolated_db.add(order)

    # Seed repaired membership in DB
    mem_key = f"fwd_mem:{order.id}"[:50]
    mem_raw = json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 7,
        "runtime_commit": "sha_repaired_777",
        "bound_at_ms": now_ms,
        "metadata": {
            "repaired_by": "ForwardMembershipRepairTask",
            "task_id": "fwd_rep_prior_20_01",
        },
    })
    isolated_db.add(models.SystemConfig(key=mem_key, value=mem_raw, updated_at=now_ms))
    isolated_db.commit()

    # Stage task via production helper WITHOUT original_damaged_payload
    # Staging helper copies rec_mem.value into original_damaged_payload
    staged_task = stage_forward_repair_task(
        isolated_db,
        order_id=order.id,
        forward_session_id=sess.session_id,
        now_ms=now_ms,
    )
    assert staged_task is not None
    assert staged_task.original_damaged_payload == mem_raw
    isolated_db.commit()

    # Reload task from DB
    task_rec = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == _repair_task_key(order.id)).first()
    assert task_rec is not None
    reloaded_task = ForwardMembershipRepairTask.from_dict(json.loads(task_rec.value))

    # Collector check: original_damaged_payload must be classified as REPAIRED_MEMBERSHIP
    sources = collect_repair_provenance_sources(isolated_db, reloaded_task, order)
    dp_source = sources["structured_sources"][0]
    assert dp_source.kind == "REPAIRED_MEMBERSHIP"

    # Direct resolver check
    resolution = resolve_repair_provenance(isolated_db, reloaded_task, order)
    assert resolution.status == "INSUFFICIENT"
    assert REPAIR_SOURCE_UNVERIFIED in resolution.reason_codes

    # End-to-end repair task execution
    res = repair_forward_membership_task(isolated_db, reloaded_task, now_ms=now_ms + 1000)
    assert res["success"] is False
    assert res["status"] == "MANUAL_REVIEW_REQUIRED"
    assert REPAIR_SOURCE_UNVERIFIED in (res.get("reason") or "")

    # Zero membership rewrite
    rec_after = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == mem_key).first()
    assert rec_after.value == mem_raw

    # Zero session index mutation
    session_orders = _get_session_order_ids(isolated_db, sess.session_id)
    assert order.id not in session_orders


def test_c20_02_top_level_marker_via_staging(isolated_db: Session):
    """C20-02 (PHẦN 052): Top-level repaired_by marker in membership.
    Copied via stage_forward_repair_task() -> reload -> resolve -> INSUFFICIENT.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c20_02", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_c20_02", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    mem_raw = json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 5,
        "runtime_commit": "sha_top_level_555",
        "bound_at_ms": now_ms,
        "repaired_by": "ForwardMembershipRepairTask",  # Top-level marker
    })
    isolated_db.add(models.SystemConfig(key=mem_key, value=mem_raw, updated_at=now_ms))
    isolated_db.commit()

    stage_forward_repair_task(isolated_db, order_id=order.id, forward_session_id=sess.session_id, now_ms=now_ms)
    isolated_db.commit()

    task_rec = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == _repair_task_key(order.id)).first()
    reloaded_task = ForwardMembershipRepairTask.from_dict(json.loads(task_rec.value))

    resolution = resolve_repair_provenance(isolated_db, reloaded_task, order)
    assert resolution.status == "INSUFFICIENT"
    assert REPAIR_SOURCE_UNVERIFIED in resolution.reason_codes


def test_c20_03_repair_task_id_marker_via_staging(isolated_db: Session):
    """C20-03 (PHẦN 053): metadata.repair_task_id marker in membership.
    Copied via stage_forward_repair_task() -> reload -> resolve -> INSUFFICIENT.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c20_03", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_c20_03", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    mem_raw = json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 4,
        "runtime_commit": "sha_task_id_444",
        "bound_at_ms": now_ms,
        "metadata": {"repair_task_id": "fwd_rep_prior_20_03"},
    })
    isolated_db.add(models.SystemConfig(key=mem_key, value=mem_raw, updated_at=now_ms))
    isolated_db.commit()

    stage_forward_repair_task(isolated_db, order_id=order.id, forward_session_id=sess.session_id, now_ms=now_ms)
    isolated_db.commit()

    task_rec = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == _repair_task_key(order.id)).first()
    reloaded_task = ForwardMembershipRepairTask.from_dict(json.loads(task_rec.value))

    resolution = resolve_repair_provenance(isolated_db, reloaded_task, order)
    assert resolution.status == "INSUFFICIENT"
    assert REPAIR_SOURCE_UNVERIFIED in resolution.reason_codes


def test_c20_04_provenance_sources_marker_via_staging(isolated_db: Session):
    """C20-04 (PHẦN 054): metadata.provenance_sources marker in membership.
    Copied via stage_forward_repair_task() -> reload -> resolve -> INSUFFICIENT.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c20_04", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_c20_04", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    mem_raw = json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 3,
        "runtime_commit": "sha_prov_sources_333",
        "bound_at_ms": now_ms,
        "metadata": {"provenance_sources": ["db_membership"]},
    })
    isolated_db.add(models.SystemConfig(key=mem_key, value=mem_raw, updated_at=now_ms))
    isolated_db.commit()

    stage_forward_repair_task(isolated_db, order_id=order.id, forward_session_id=sess.session_id, now_ms=now_ms)
    isolated_db.commit()

    task_rec = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == _repair_task_key(order.id)).first()
    reloaded_task = ForwardMembershipRepairTask.from_dict(json.loads(task_rec.value))

    resolution = resolve_repair_provenance(isolated_db, reloaded_task, order)
    assert resolution.status == "INSUFFICIENT"
    assert REPAIR_SOURCE_UNVERIFIED in resolution.reason_codes


def test_c20_05_membership_missing_after_copy(isolated_db: Session):
    """C20-05 (PHẦN 033, 055): Membership missing from DB after being copied to damaged payload.
    Repaired copy in damaged payload must NOT become independent original proof on its own.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c20_05", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_c20_05", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    mem_raw = json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 7,
        "runtime_commit": "sha_repaired_777",
        "bound_at_ms": now_ms,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
    })
    isolated_db.add(models.SystemConfig(key=mem_key, value=mem_raw, updated_at=now_ms))
    isolated_db.commit()

    # Stage task (copies mem_raw)
    stage_forward_repair_task(isolated_db, order_id=order.id, forward_session_id=sess.session_id, now_ms=now_ms)
    isolated_db.commit()

    # Delete membership from DB to simulate missing metadata record
    mem_rec = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == mem_key).first()
    isolated_db.delete(mem_rec)
    isolated_db.commit()

    task_rec = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == _repair_task_key(order.id)).first()
    reloaded_task = ForwardMembershipRepairTask.from_dict(json.loads(task_rec.value))

    resolution = resolve_repair_provenance(isolated_db, reloaded_task, order)
    assert resolution.status == "INSUFFICIENT"
    assert REPAIR_SOURCE_UNVERIFIED in resolution.reason_codes


def test_c20_06_all_sources_repaired(isolated_db: Session):
    """C20-06 (PHẦN 030, 056): All sources (damaged payload, db membership, legacy snapshot)
    contain repaired markers. None qualify as independent original proof.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c20_06", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_c20_06", now_ms)
    isolated_db.add(order)

    repaired_payload = {
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 7,
        "runtime_commit": "sha_same_777",
        "bound_at_ms": now_ms,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
    }

    isolated_db.add(models.SystemConfig(key=f"fwd_mem:{order.id}"[:50], value=json.dumps(repaired_payload), updated_at=now_ms))
    isolated_db.add(models.SystemConfig(key=f"fwd_snap:{order.id}"[:50], value=json.dumps(repaired_payload), updated_at=now_ms))

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
        original_damaged_payload=json.dumps(repaired_payload),
    )
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "INSUFFICIENT"
    assert REPAIR_SOURCE_UNVERIFIED in resolution.reason_codes


def test_c20_07_real_original_membership_via_staging(isolated_db: Session):
    """C20-07 (PHẦN 035, 057): Real original membership without repair markers.
    Staging copies it into damaged payload; both are correctly classified as ORIGINAL_BINDING.
    Recovery succeeds with VERIFIED.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c20_07", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_c20_07", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    mem_raw = json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 2,
        "runtime_commit": "sha_genuine_original_222",
        "bound_at_ms": now_ms,
        "metadata": {"creator": "live_engine"},  # No repair markers
    })
    isolated_db.add(models.SystemConfig(key=mem_key, value=mem_raw, updated_at=now_ms))
    isolated_db.commit()

    stage_forward_repair_task(isolated_db, order_id=order.id, forward_session_id=sess.session_id, now_ms=now_ms)
    isolated_db.commit()

    task_rec = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == _repair_task_key(order.id)).first()
    reloaded_task = ForwardMembershipRepairTask.from_dict(json.loads(task_rec.value))

    resolution = resolve_repair_provenance(isolated_db, reloaded_task, order)
    assert resolution.status == "VERIFIED"
    assert resolution.config_revision == 2
    assert resolution.runtime_commit == "sha_genuine_original_222"

    res = repair_forward_membership_task(isolated_db, reloaded_task, now_ms=now_ms + 1000)
    assert res["success"] is True
    assert res["status"] == "REPAIRED"


def test_c20_08_real_original_payload_with_repaired_membership(isolated_db: Session):
    """C20-08 (PHẦN 036, 058): Repaired membership in DB, but explicit genuine original proof
    provided in original_damaged_payload.
    Succeeds with VERIFIED when values are consistent.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c20_08", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_c20_08", now_ms)
    isolated_db.add(order)

    # Repaired membership
    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 6,
        "runtime_commit": "sha_agree_666",
        "bound_at_ms": now_ms,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
    }), updated_at=now_ms))

    # Genuine original damaged payload proof (no repair markers)
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
            "config_revision": 6,
            "runtime_commit": "sha_agree_666",
            "bound_at_ms": now_ms,
        }),
    )
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "VERIFIED"
    assert resolution.config_revision == 6

    res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res["success"] is True
    assert res["status"] == "REPAIRED"


def test_c20_09_repaired_payload_with_original_snapshot(isolated_db: Session):
    """C20-09 (PHẦN 059): Damaged payload has repaired markers, but legacy snapshot
    has genuine complete proof without repair markers.
    Succeeds with VERIFIED.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c20_09", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_c20_09", now_ms)
    isolated_db.add(order)

    # Genuine snapshot
    snap_key = f"fwd_snap:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=snap_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 4,
        "runtime_commit": "sha_snap_genuine_444",
        "bound_at_ms": now_ms,
    }), updated_at=now_ms))

    # Repaired damaged payload
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
            "config_revision": 4,
            "runtime_commit": "sha_snap_genuine_444",
            "bound_at_ms": now_ms,
            "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
        }),
    )
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "VERIFIED"
    assert resolution.config_revision == 4


def test_c20_10_repaired_snapshot_only(isolated_db: Session):
    """C20-10 (PHẦN 060): Snapshot has complete fields but has repaired markers.
    Cannot serve as independent original authority.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c20_10", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_c20_10", now_ms)
    isolated_db.add(order)

    # Repaired snapshot
    snap_key = f"fwd_snap:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=snap_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 4,
        "runtime_commit": "sha_snap_repaired_444",
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
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "INSUFFICIENT"
    assert REPAIR_SOURCE_UNVERIFIED in resolution.reason_codes


def test_c20_11_partial_original_payload(isolated_db: Session):
    """C20-11 (PHẦN 061): Partial original payload + full repaired membership.
    Must NOT combine to self-certify.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c20_11", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_c20_11", now_ms)
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


def test_c20_12_empty_payload(isolated_db: Session):
    """C20-12 (PHẦN 062): Empty payload "{}" with full repaired membership.
    Fails closed to INSUFFICIENT.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c20_12", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_c20_12", now_ms)
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
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "INSUFFICIENT"
    assert REPAIR_SOURCE_UNVERIFIED in resolution.reason_codes


def test_c20_13_original_repaired_conflict(isolated_db: Session):
    """C20-13 (PHẦN 063): Genuine original proof has revision 7, repaired membership has revision 8.
    All-source comparison identifies revision conflict.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c20_13", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_c20_13", now_ms)
    isolated_db.add(order)

    mem_key = f"fwd_mem:{order.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 8,  # Conflicting rev in membership
        "runtime_commit": "sha_same_commit",
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
            "config_revision": 7,  # Genuine proof says 7
            "runtime_commit": "sha_same_commit",
            "bound_at_ms": now_ms,
        }),
    )
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "CONFLICT"
    assert REPAIR_CONFIG_REVISION_CONFLICT in resolution.reason_codes


def test_c20_14_index_conflict(isolated_db: Session):
    """C20-14 (PHẦN 064): Valid original proof, but order appears in both Session A and Session B indexes.
    Target is Session A. Fails closed with REPAIR_INDEX_CONFLICT.
    """
    now_ms = 1792000000000
    sess_a = create_forward_paper_session(isolated_db, "fwd_sess_c20_14_A", virtual_capital=1000.0, now_ms=now_ms)
    sess_b = create_forward_paper_session(isolated_db, "fwd_sess_c20_14_B", virtual_capital=2000.0, now_ms=now_ms)
    order = _make_closed_order("ord_c20_14", now_ms)
    isolated_db.add(order)

    # Order in both indexes
    isolated_db.add(models.SystemConfig(key=f"fwd_sess_ords:{sess_a.session_id}", value=json.dumps([order.id]), updated_at=now_ms))
    isolated_db.add(models.SystemConfig(key=f"fwd_sess_ords:{sess_b.session_id}", value=json.dumps([order.id]), updated_at=now_ms))

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
            "order_id": order.id,
            "forward_session_id": sess_a.session_id,
            "config_revision": 1,
            "runtime_commit": "sha_idx_conflict_14",
            "bound_at_ms": now_ms,
        }),
    )
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "CONFLICT"
    assert REPAIR_INDEX_CONFLICT in resolution.reason_codes


def test_c20_15_wrong_order(isolated_db: Session):
    """C20-15 (PHẦN 065): Copied payload has order Y while task is for order X.
    Returns CONFLICT.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c20_15", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_c20_15_target", now_ms)
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
            "order_id": "ord_c20_15_OTHER",
            "forward_session_id": sess.session_id,
            "config_revision": 1,
            "runtime_commit": "sha_ord_15",
            "bound_at_ms": now_ms,
        }),
    )
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(task.to_dict()), updated_at=now_ms))
    isolated_db.commit()

    resolution = resolve_repair_provenance(isolated_db, task, order)
    assert resolution.status == "CONFLICT"
    assert REPAIR_ORDER_CONFLICT in resolution.reason_codes


def test_c20_16_existing_task_restage(isolated_db: Session):
    """C20-16 (PHẦN 044, 066): Existing task was created previously.
    DB membership later becomes repaired. stage_forward_repair_task() updates the existing task.
    Updated task's copied payload is classified as REPAIRED_MEMBERSHIP and fails closed.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c20_16", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_c20_16", now_ms)
    isolated_db.add(order)

    # Initial task created with empty payload
    initial_task = ForwardMembershipRepairTask(
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
    isolated_db.add(models.SystemConfig(key=_repair_task_key(order.id), value=json.dumps(initial_task.to_dict()), updated_at=now_ms))

    # Repaired membership saved later
    mem_key = f"fwd_mem:{order.id}"[:50]
    mem_raw = json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": order.id,
        "config_revision": 7,
        "runtime_commit": "sha_restaged_777",
        "bound_at_ms": now_ms,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
    })
    isolated_db.add(models.SystemConfig(key=mem_key, value=mem_raw, updated_at=now_ms + 100))
    isolated_db.commit()

    # Re-stage task: updates existing task's original_damaged_payload from membership
    stage_forward_repair_task(isolated_db, order_id=order.id, forward_session_id=sess.session_id, now_ms=now_ms + 200)
    isolated_db.commit()

    task_rec = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == _repair_task_key(order.id)).first()
    reloaded_task = ForwardMembershipRepairTask.from_dict(json.loads(task_rec.value))
    assert reloaded_task.original_damaged_payload == mem_raw

    resolution = resolve_repair_provenance(isolated_db, reloaded_task, order)
    assert resolution.status == "INSUFFICIENT"
    assert REPAIR_SOURCE_UNVERIFIED in resolution.reason_codes


def test_c20_17_roundtrip_classification(isolated_db: Session):
    """C20-17 (PHẦN 067): JSON serialization roundtrip.
    Origin classification does not depend on in-memory object references;
    persisting to SystemConfig and deserializing preserves REPAIRED_MEMBERSHIP classification.
    """
    payload = {
        "forward_session_id": "sess_c20_17",
        "order_id": "ord_c20_17",
        "config_revision": 3,
        "runtime_commit": "sha_roundtrip",
        "bound_at_ms": 1792000000000,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
    }
    # Pre-persistence classification
    assert classify_repair_binding_payload_origin(payload) == "REPAIRED_MEMBERSHIP"

    # Persist and reload
    serialized = json.dumps(payload)
    reloaded = json.loads(serialized)

    # Post-reload classification
    assert classify_repair_binding_payload_origin(reloaded) == "REPAIRED_MEMBERSHIP"


@pytest.mark.parametrize("meta_val", [
    None,
    "string_not_dict",
    [1, 2, 3],
    {"creator": "runtime", "version": "1.0"},
])
def test_c20_18_malformed_metadata(meta_val):
    """C20-18 (PHẦN 022, 068): Malformed or non-repaired metadata values.
    Helper does not crash, does not raise AttributeError, and does not misclassify as repaired.
    """
    payload = {
        "forward_session_id": "sess_c20_18",
        "order_id": "ord_c20_18",
        "metadata": meta_val,
    }
    kind = classify_repair_binding_payload_origin(payload, default_kind="ORIGINAL_BINDING")
    assert kind == "ORIGINAL_BINDING"


def test_c20_19_zero_financial_mutation(isolated_db: Session):
    """C20-19 (PHẦN 069): Zero financial mutations on PaperOrder or DomainEvents
    during copied repair failure.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c20_19", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_c20_19", now_ms)
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
    isolated_db.commit()

    stage_forward_repair_task(isolated_db, order_id=order.id, forward_session_id=sess.session_id, now_ms=now_ms)
    isolated_db.commit()

    task_rec = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == _repair_task_key(order.id)).first()
    task = ForwardMembershipRepairTask.from_dict(json.loads(task_rec.value))

    # Snapshot before
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
    pre_events = isolated_db.query(models.DomainEvent).count()

    repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)

    # Snapshot after
    post_ord = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == order.id).first()
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
    assert isolated_db.query(models.DomainEvent).count() == pre_events


def test_c20_20_worker_production_path(isolated_db: Session):
    """C20-20 (PHẦN 070): Worker production path.
    Batch contains one copied repaired task and one valid task.
    Copied task -> MANUAL_REVIEW_REQUIRED, valid task -> REPAIRED.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c20_20", virtual_capital=1000.0, now_ms=now_ms)

    ord_copied = _make_closed_order("ord_c20_20_copied", now_ms)
    ord_valid = _make_closed_order("ord_c20_20_valid", now_ms)
    isolated_db.add_all([ord_copied, ord_valid])

    # Copied task order: has repaired membership in DB
    mem_key = f"fwd_mem:{ord_copied.id}"[:50]
    isolated_db.add(models.SystemConfig(key=mem_key, value=json.dumps({
        "forward_session_id": sess.session_id,
        "order_id": ord_copied.id,
        "config_revision": 7,
        "runtime_commit": "sha_repaired_777",
        "bound_at_ms": now_ms,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
    }), updated_at=now_ms))
    isolated_db.commit()

    # Stage task for copied order via production helper (copies repaired membership)
    stage_forward_repair_task(isolated_db, order_id=ord_copied.id, forward_session_id=sess.session_id, now_ms=now_ms)

    # Valid task order: stage via production helper with genuine original proof
    valid_payload = json.dumps({
        "order_id": ord_valid.id,
        "forward_session_id": sess.session_id,
        "config_revision": 2,
        "runtime_commit": "sha_worker_valid_20",
        "bound_at_ms": now_ms,
    })
    stage_forward_repair_task(
        isolated_db,
        order_id=ord_valid.id,
        forward_session_id=sess.session_id,
        original_damaged_payload=valid_payload,
        now_ms=now_ms,
    )
    isolated_db.commit()

    res = forward_repair_worker.process_once(db=isolated_db, now_ms=now_ms + 1000)
    assert res["processed"] == 2
    assert res["manual_review"] == 1
    assert res["repaired"] == 1

    t1_rec = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == _repair_task_key(ord_copied.id)).first()
    assert json.loads(t1_rec.value)["status"] == "MANUAL_REVIEW_REQUIRED"

    t2_rec = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == _repair_task_key(ord_valid.id)).first()
    assert json.loads(t2_rec.value)["status"] == "REPAIRED"


def test_c20_21_owner_commit_fail(isolated_db: Session):
    """C20-21 (PHẦN 071): Owner commit failure during manual review staging rollbacks cleanly."""
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c20_21", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_c20_21", now_ms)
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
    isolated_db.commit()

    stage_forward_repair_task(isolated_db, order_id=order.id, forward_session_id=sess.session_id, now_ms=now_ms)
    isolated_db.commit()

    task_rec = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == _repair_task_key(order.id)).first()
    task = ForwardMembershipRepairTask.from_dict(json.loads(task_rec.value))

    with patch.object(isolated_db, "commit", side_effect=RuntimeError("Simulated owner commit error")):
        res = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
        assert res["success"] is False
        assert "COMMIT_FAILED" in res.get("status", "") or "Simulated" in str(res.get("error", ""))


def test_c20_22_idempotency(isolated_db: Session):
    """C20-22 (PHẦN 072): Calling repair twice on copied repaired task
    does not duplicate index, does not alter membership, does not generate events.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, "fwd_sess_c20_22", virtual_capital=1000.0, now_ms=now_ms)
    order = _make_closed_order("ord_c20_22", now_ms)
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
    isolated_db.commit()

    stage_forward_repair_task(isolated_db, order_id=order.id, forward_session_id=sess.session_id, now_ms=now_ms)
    isolated_db.commit()

    task_rec = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == _repair_task_key(order.id)).first()
    task = ForwardMembershipRepairTask.from_dict(json.loads(task_rec.value))

    res1 = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 1000)
    assert res1["success"] is False
    assert res1["status"] == "MANUAL_REVIEW_REQUIRED"

    res2 = repair_forward_membership_task(isolated_db, task, now_ms=now_ms + 2000)
    assert res2["success"] is False
    assert res2["status"] == "MANUAL_REVIEW_REQUIRED"

    session_orders = _get_session_order_ids(isolated_db, sess.session_id)
    assert order.id not in session_orders


def test_c20_23_helper_purity():
    """PHẦN 073: Purity test for classify_repair_binding_payload_origin.
    Ensures input dictionary is never mutated and returns identical classification across sources.
    """
    original_dict = {
        "forward_session_id": "sess_pure",
        "order_id": "ord_pure",
        "config_revision": 1,
        "metadata": {"repaired_by": "ForwardMembershipRepairTask"},
    }
    input_copy = copy.deepcopy(original_dict)

    res_dp = classify_repair_binding_payload_origin(input_copy, default_kind="ORIGINAL_BINDING")
    res_mem = classify_repair_binding_payload_origin(input_copy, default_kind="ORIGINAL_BINDING")
    res_snap = classify_repair_binding_payload_origin(input_copy, default_kind="LEGACY_BINDING")

    # Assert purity
    assert input_copy == original_dict
    # Assert consistency across sources
    assert res_dp == "REPAIRED_MEMBERSHIP"
    assert res_mem == "REPAIRED_MEMBERSHIP"
    assert res_snap == "REPAIRED_MEMBERSHIP"
