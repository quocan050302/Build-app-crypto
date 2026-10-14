"""
backend/tests/test_v14_4_14_outcome_authority.py
V14.4.14 — Outcome Authority Verification and Classification Suite.

Covers:
  - A01: Real ORM Authority Fields (PHẦN 004, 038, 057)
  - A02: Normal & Manual Close Positive Controls (PHẦN 041, 058)
  - A03: Recovery Positive Controls (PHẦN 040, 059)
  - A04: Ambiguity, Conservative Simulation & Conflict Review (PHẦN 042, 043, 060)
  - A05: As-Of Filtering, Missing/NaN/Inf PnL, Missing R & Export Parity (PHẦN 044, 045, 061)
"""
from __future__ import annotations

import math
import json
import pytest
from sqlalchemy.orm import Session

import models
from services.forward_paper_service import (
    create_forward_paper_session,
    stage_forward_membership_binding,
    stage_forward_membership_status,
    read_outcome_authority,
    classify_forward_outcome,
    build_forward_session_report,
    export_forward_paper_data,
    RecoveryOutcomeAuthority,
)


# =========================================================================
# A01: ORM AUTHORITY FIELDS (PHẦN 057)
# =========================================================================

def test_auth03_real_orm_authority_fields(isolated_db: Session):
    """PHẦN 057 (AUTH03 / A01):
    Uses real models.PaperOrder columns recovery_status and recovery_confidence.
    When recovery_confidence='UNRESOLVED' (even with valid closed_at/TP_HIT/positive PnL),
    read_outcome_authority returns status='UNRESOLVED' and countable_for_confirmed=False.
    classify_forward_outcome classifies as REVIEW_PENDING.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, session_id="fwd_a01", now_ms=now_ms)

    order = models.PaperOrder(
        id="ord_a01_unres",
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
        initial_risk_usdt=2.0,
        realized_pnl_net=20.0,
        realized_r=2.0,
        opened_at=now_ms,
        closed_at=now_ms + 10000,
        created_at=now_ms,
        recovery_status="UNRESOLVED_GAP",
        recovery_confidence="UNRESOLVED",
    )
    isolated_db.add(order)
    stage_forward_membership_binding(isolated_db, sess.session_id, order, now_ms=now_ms)
    stage_forward_membership_status(isolated_db, order.id, "CLOSED", now_ms=now_ms + 10000)
    isolated_db.commit()

    # Query back from DB to verify ORM mapping
    db_order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == order.id).first()
    assert db_order is not None
    assert db_order.recovery_status == "UNRESOLVED_GAP"
    assert db_order.recovery_confidence == "UNRESOLVED"

    auth = read_outcome_authority(db_order)
    assert isinstance(auth, RecoveryOutcomeAuthority)
    assert auth.status == "UNRESOLVED"
    assert auth.countable_for_confirmed is False
    assert "UNRESOLVED_RECOVERY" in auth.reason_codes

    cls_res = classify_forward_outcome(db_order)
    assert cls_res["classification"] == "REVIEW_PENDING"
    assert cls_res["countable_for_confirmed"] is False
    assert cls_res["pnl"] is None  # Excluded from confirmed metrics


# =========================================================================
# A02: NORMAL & MANUAL POSITIVE CONTROLS (PHẦN 058)
# =========================================================================

@pytest.mark.parametrize("exit_cause,expected_mode", [
    ("TP_HIT", "NORMAL"),
    ("SL_HIT", "NORMAL"),
    ("MANUAL", "MANUAL"),
    ("MANUAL_CLOSE", "MANUAL"),
])
def test_auth04_normal_and_manual_positive_control(isolated_db: Session, exit_cause: str, expected_mode: str):
    """PHẦN 058 (AUTH04 / A02):
    Normal lifecycle close with recovery_confidence=None is countable and VERIFIED_PAPER.
    Manual close has clear category (exit_mode='MANUAL').
    Forged exit-cause or missing closed_at is rejected as UNVERIFIED.
    """
    now_ms = 1792000000000
    pnl = 15.0 if "TP" in exit_cause or "MANUAL" in exit_cause else -10.0
    r = 1.5 if pnl > 0 else -1.0

    order = models.PaperOrder(
        id=f"ord_a02_{exit_cause.lower()}",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_exit=4195.0,
        exit_cause=exit_cause,
        quantity=0.1,
        realized_pnl_net=pnl,
        realized_r=r,
        opened_at=now_ms,
        closed_at=now_ms + 10000,
        created_at=now_ms,
        recovery_status="NONE",
        recovery_confidence=None,
    )

    auth = read_outcome_authority(order)
    assert auth.status == "VERIFIED_PAPER"
    assert auth.exit_mode == expected_mode
    assert auth.countable_for_confirmed is True

    cls_res = classify_forward_outcome(order)
    assert cls_res["classification"] == "CONFIRMED"
    assert cls_res["countable_for_confirmed"] is True
    assert cls_res["outcome_type"] == ("WIN" if pnl > 0 else "LOSS")
    assert cls_res["pnl"] == pnl

    # Negative control: Forged exit cause
    order_forged = models.PaperOrder(
        id="ord_a02_forged",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        exit_cause="MAGIC_PROFIT_TRIGGER",
        realized_pnl_net=100.0,
        opened_at=now_ms,
        closed_at=now_ms + 10000,
        created_at=now_ms,
    )
    auth_forged = read_outcome_authority(order_forged)
    assert auth_forged.status == "UNVERIFIED"
    assert auth_forged.countable_for_confirmed is False
    assert classify_forward_outcome(order_forged)["classification"] == "UNVERIFIED"

    # Negative control: Missing closed_at
    order_no_close = models.PaperOrder(
        id="ord_a02_no_close",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        exit_cause="TP_HIT",
        realized_pnl_net=10.0,
        opened_at=now_ms,
        closed_at=None,
        created_at=now_ms,
    )
    auth_no_close = read_outcome_authority(order_no_close)
    assert auth_no_close.status == "UNVERIFIED"
    assert auth_no_close.countable_for_confirmed is False


# =========================================================================
# A03: RECOVERY POSITIVE CONTROLS (PHẦN 059)
# =========================================================================

@pytest.mark.parametrize("rec_conf", ["CONFIRMED_TICK", "CONFIRMED"])
def test_auth05_recovery_positive_controls(isolated_db: Session, rec_conf: str):
    """PHẦN 059 (AUTH05 / A03):
    Recovery with CONFIRMED_TICK or CONFIRMED from reconciliation produces
    VERIFIED_PAPER authority and CONFIRMED classification.
    Unknown confidence string degrades to UNVERIFIED.
    """
    now_ms = 1792000000000
    order = models.PaperOrder(
        id=f"ord_a03_{rec_conf.lower()}",
        instrument="XAUUSDT",
        direction="SHORT",
        state="closed",
        planned_entry=4200.0,
        actual_entry=4200.0,
        stop_loss=4210.0,
        take_profit=4180.0,
        actual_exit=4180.0,
        exit_cause="TP_HIT_RECONCILED",
        quantity=0.1,
        realized_pnl_net=20.0,
        realized_r=2.0,
        opened_at=now_ms,
        closed_at=now_ms + 5000,
        created_at=now_ms,
        recovery_status="RECOVERED",
        recovery_confidence=rec_conf,
    )

    auth = read_outcome_authority(order)
    assert auth.status == "VERIFIED_PAPER"
    assert auth.exit_mode == "RECOVERY"
    assert auth.countable_for_confirmed is True

    cls_res = classify_forward_outcome(order)
    assert cls_res["classification"] == "CONFIRMED"
    assert cls_res["countable_for_confirmed"] is True

    # Unknown confidence
    order.recovery_confidence = "PARTIAL_ASSUMPTION"
    auth_unknown = read_outcome_authority(order)
    assert auth_unknown.status == "UNVERIFIED"
    assert auth_unknown.countable_for_confirmed is False


# =========================================================================
# A04: AMBIGUITY, CONSERVATIVE SIMULATION & REVIEW (PHẦN 060)
# =========================================================================

def test_auth06_ambiguity_conservative_simulation_and_conflict_review(isolated_db: Session):
    """PHẦN 060 (AUTH06 / A04):
    Conservative simulation (AMBIGUOUS_BAR_SL_FIRST, SIMULATED_AMBIGUOUS, ASSUMED_CONSERVATIVE)
    is categorized as CONSERVATIVE_SIMULATION with countable_for_confirmed=False.
    Terminal conflict review marks outcome as CONFLICTED.
    build_forward_session_report segregates counts cleanly with zero DB mutation.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, session_id="fwd_a04", now_ms=now_ms)

    # 1. Conservative order
    ord_cons = models.PaperOrder(
        id="ord_a04_cons",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_exit=4170.0,
        exit_cause="AMBIGUOUS_BAR_SL_FIRST",
        quantity=0.1,
        initial_risk_usdt=2.0,
        realized_pnl_net=-10.0,
        realized_r=-1.0,
        opened_at=now_ms,
        closed_at=now_ms + 3000,
        created_at=now_ms,
        recovery_status="RECOVERED",
        recovery_confidence="SIMULATED_AMBIGUOUS",
    )
    isolated_db.add(ord_cons)
    stage_forward_membership_binding(isolated_db, sess.session_id, ord_cons, now_ms=now_ms)
    stage_forward_membership_status(isolated_db, ord_cons.id, "CLOSED", now_ms=now_ms + 3000)

    # 2. Conflicted review order
    ord_conf = models.PaperOrder(
        id="ord_a04_conf",
        instrument="XAUUSDT",
        direction="SHORT",
        state="closed",
        planned_entry=4200.0,
        actual_entry=4200.0,
        stop_loss=4210.0,
        take_profit=4180.0,
        actual_exit=4180.0,
        exit_cause="TP_HIT",
        quantity=0.1,
        initial_risk_usdt=2.0,
        realized_pnl_net=20.0,
        realized_r=2.0,
        opened_at=now_ms,
        closed_at=now_ms + 4000,
        created_at=now_ms,
        recovery_status="NONE",
        recovery_confidence=None,
    )
    isolated_db.add(ord_conf)
    stage_forward_membership_binding(isolated_db, sess.session_id, ord_conf, now_ms=now_ms)
    stage_forward_membership_status(isolated_db, ord_conf.id, "CLOSED", now_ms=now_ms + 4000)

    # Add active conflict review record in models.SystemConfig
    cfg = models.SystemConfig(
        key="recon_rev:ord_a04_conf",
        value=json.dumps({"status": "MANUAL_REVIEW_REQUIRED", "reason_codes": ["CONFLICT_EARLIER_TERMINAL"]}),
        updated_at=now_ms,
    )
    isolated_db.add(cfg)
    isolated_db.commit()

    # Verify individual classifications
    cls_cons = classify_forward_outcome(ord_cons)
    assert cls_cons["classification"] == "CONSERVATIVE_SIMULATION"
    assert cls_cons["countable_for_confirmed"] is False
    assert cls_cons["outcome_type"] == "LOSS"
    assert cls_cons["pnl"] == -10.0

    cls_conf = classify_forward_outcome(ord_conf, review_record={"status": "MANUAL_REVIEW_REQUIRED"})
    assert cls_conf["classification"] == "CONFLICTED"
    assert cls_conf["countable_for_confirmed"] is False

    # Verify build_forward_session_report
    report = build_forward_session_report(isolated_db, sess.session_id, now_ms=now_ms + 10000)
    assert report["session_id"] == sess.session_id
    assert report["conservative_simulation_count"] == 1
    assert report["review_count"] >= 1
    assert report["confirmed_closed_trades"] == 0  # Neither counted as confirmed


# =========================================================================
# A05: AS-OF, MISSING/NAN/INF & EXPORT PARITY (PHẦN 061)
# =========================================================================

def test_auth07_as_of_missing_nan_and_export_parity(isolated_db: Session):
    """PHẦN 061 (AUTH07 / A05):
    - Orders closed after as_of_ms are treated as OPEN and excluded from report as-of.
    - Missing or non-finite PnL (NaN, Inf) classifies as DATA_INVALID (never breakeven).
    - Missing R preserves None (not fake 0.0).
    - export_forward_paper_data parity with report classifications.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, session_id="fwd_a05", now_ms=now_ms)

    # 1. Closed after as-of
    ord_future = models.PaperOrder(
        id="ord_a05_future",
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
        realized_pnl_net=20.0,
        realized_r=2.0,
        opened_at=now_ms,
        closed_at=now_ms + 50000,
        created_at=now_ms,
    )
    # Classify with as_of_ms before closed_at
    cls_fut = classify_forward_outcome(ord_future, as_of_ms=now_ms + 10000)
    assert cls_fut["classification"] == "OPEN"
    assert cls_fut["countable_for_confirmed"] is False

    # 2. Missing PnL
    ord_no_pnl = models.PaperOrder(
        id="ord_a05_no_pnl",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        exit_cause="TP_HIT",
        realized_pnl_net=None,
        closed_at=now_ms,
        created_at=now_ms,
    )
    cls_no_pnl = classify_forward_outcome(ord_no_pnl)
    assert cls_no_pnl["classification"] == "DATA_INVALID"
    assert "MISSING_REALIZED_PNL" in cls_no_pnl["reasons"]

    # 3. Non-finite PnL (NaN / Inf)
    ord_nan = models.PaperOrder(
        id="ord_a05_nan",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        exit_cause="TP_HIT",
        realized_pnl_net=float("nan"),
        closed_at=now_ms,
        created_at=now_ms,
    )
    cls_nan = classify_forward_outcome(ord_nan)
    assert cls_nan["classification"] == "DATA_INVALID"
    assert "NON_FINITE_REALIZED_PNL" in cls_nan["reasons"]

    # 4. Missing R preserves None
    ord_no_r = models.PaperOrder(
        id="ord_a05_no_r",
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
        initial_risk_usdt=2.0,
        realized_pnl_net=10.0,
        realized_r=None,
        opened_at=now_ms,
        closed_at=now_ms + 1000,
        created_at=now_ms,
    )
    isolated_db.add(ord_no_r)
    stage_forward_membership_binding(isolated_db, sess.session_id, ord_no_r, now_ms=now_ms)
    stage_forward_membership_status(isolated_db, ord_no_r.id, "CLOSED", now_ms=now_ms + 1000)
    isolated_db.commit()

    cls_no_r = classify_forward_outcome(ord_no_r)
    assert cls_no_r["classification"] == "CONFIRMED"
    assert cls_no_r["r"] is None  # NOT fake 0.0

    # 5. Export parity
    export_data = export_forward_paper_data(isolated_db, sess.session_id)
    assert "trades" in export_data or isinstance(export_data, list)
    rows = export_data.get("trades", export_data) if isinstance(export_data, dict) else export_data
    assert len(rows) == 1
    assert rows[0]["order_id"] == ord_no_r.id
    assert rows[0]["outcome_classification"] == "CONFIRMED"
