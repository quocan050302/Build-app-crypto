"""
backend/tests/test_v16_39_7_mode_trust_lesson_api_acceptance.py
V16.39.7 — CLOSE ENTRY-MODE VERIFICATION AND LESSON APPROVAL ACCEPTANCE

Covers the 3 core objectives (Ba đầu việc):
1. Historical entry mode resolver multi-source conflict detection (Rules 026-027, 041-070).
2. Compliance consumer respect for verification_status fail-closed on UNVERIFIED/CONFLICT (Rules 025, 071-085).
3. HTTP approve-and-activate API -> DB persistence & cache invalidation -> Scorer/Orchestrator threshold crossing -> Disable restoration (Rules 121-170).
"""
import json
import time
import math
import pytest
from typing import Dict, Any, List, Optional

import models
from models import PaperOrder, Lesson
from services.smc_scenario_types import ScenarioSnapshot, ScenarioState
from services.smc_scenario_repository import save_immutable_scenario_snapshot
from services.post_trade_review_service import (
    resolve_historical_entry_mode,
    collect_historical_entry_mode_sources,
    HistoricalEntryModeResolution,
    assess_entry_compliance_for_review,
    classify_post_trade_lesson,
    PostTradeReview,
    LossClassification,
)
from services.lesson_rule_service import LessonRuleService
from services.setup_quality_service import (
    assess_candidate_quality,
    build_candidate_scoring_context,
    normalize_candidate_evidence,
)
from services.trade_lifecycle_service import TradeLifecycleService
from domain_calculator import calculate_risk_reward
from services.scalp_m1_model_service import ScalpCandidate
from services.scalp_m1_types import ModelIdentifier, build_adaptive_smc_paper_profile


class PriceQuote:
    def __init__(self, symbol="XAUUSDT", bid=2650.0, ask=2650.3, timestamp=None):
        self.symbol = symbol
        self.bid = bid
        self.ask = ask
        self.spread = round(ask - bid, 4)
        self.timestamp = timestamp or 1700000000000
        self.is_stale = False
        self.is_valid = True


def make_test_scenario_snapshot(
    scenario_id: str = "scen_test",
    instance_id: str = "inst_test",
    entry_mode: Optional[str] = "LIMIT_AT_CE_AFTER_CONFIRMATION",
    order_type: Optional[str] = "LIMIT",
    planned_entry: float = 2650.0,
    stop_loss: float = 2645.0,
    take_profit: float = 2660.0,
    now_ms: int = 1700000000000,
    evidence: Optional[List[Dict[str, Any]]] = None,
    trigger_chain: Optional[Dict[str, Any]] = None,
) -> ScenarioSnapshot:
    return ScenarioSnapshot(
        scenario_id=scenario_id,
        instance_id=instance_id,
        revision=1,
        symbol="XAUUSDT",
        setup_timeframe="M1",
        context_timeframes=["M1", "M15"],
        family="CG01",
        direction="LONG",
        state=ScenarioState.STRUCTURE_CONFIRMED,
        as_of_ms=now_ms,
        evidence_cutoff_ms=now_ms,
        evidence_ids=[e["id"] for e in (evidence or []) if isinstance(e, dict) and "id" in e],
        liquidity_map={},
        trigger_chain=trigger_chain or {"verified": True, "model_id": "CG01", "node_ids": []},
        target=None,
        invalidation=None,
        summary_vi="Test snapshot",
        config_version=1,
        detector_version="1.0",
        plan={"entry_mode": entry_mode, "order_type": order_type, "entry": planned_entry, "stop_loss": stop_loss, "take_profit": take_profit} if (entry_mode or order_type) else None,
        evidence=evidence or [],
    )


# ==============================================================================
# GROUP 1: HISTORICAL ENTRY MODE RESOLVER CONFLICT & FALLBACK (Rules 021 - 070)
# ==============================================================================

def test_001_resolver_valid_limit():
    """Rule 103: Scenario LIMIT, order LIMIT, fill LIMIT -> VERIFIED, source SCENARIO_SNAPSHOT."""
    order = PaperOrder(id="po_1", instrument="XAUUSDT", direction="LONG", order_type="LIMIT")
    scen = make_test_scenario_snapshot(entry_mode="LIMIT_AT_CE_AFTER_CONFIRMATION", order_type="LIMIT")
    fill = {"entry_mode": "LIMIT", "order_type": "LIMIT"}

    res = resolve_historical_entry_mode(order=order, scenario_snapshot=scen, fill_snapshot=fill)
    assert res.verification_status == "VERIFIED"
    assert res.entry_mode == "LIMIT_AT_CE_AFTER_CONFIRMATION"
    assert res.order_type == "LIMIT"
    assert res.source == "SCENARIO_SNAPSHOT"
    assert len(res.issues) == 0


def test_002_resolver_valid_market():
    """Rule 104: Scenario MARKET, order MARKET -> VERIFIED, canonical output CONFIRMED_RETEST_MARKET."""
    order = PaperOrder(id="po_2", instrument="XAUUSDT", direction="LONG", order_type="MARKET")
    scen = make_test_scenario_snapshot(entry_mode="CONFIRMED_RETEST_MARKET", order_type="MARKET")

    res = resolve_historical_entry_mode(order=order, scenario_snapshot=scen)
    assert res.verification_status == "VERIFIED"
    assert res.entry_mode == "CONFIRMED_RETEST_MARKET"
    assert res.order_type == "MARKET"
    assert res.source == "SCENARIO_SNAPSHOT"
    assert len(res.issues) == 0


def test_003_resolver_order_metadata_self_conflict():
    """Rule 026 / 057: Order has order_type LIMIT but entry_mode MARKET -> PROVENANCE_CONFLICT."""
    order = PaperOrder(id="po_conflict", instrument="XAUUSDT", direction="LONG", order_type="LIMIT")
    order.entry_mode = "MARKET"

    res = resolve_historical_entry_mode(order=order)
    assert res.verification_status == "PROVENANCE_CONFLICT"
    assert any("ORDER_METADATA_SELF_CONFLICT" in iss or "PROVENANCE_CONFLICT" in iss for iss in res.issues)


def test_004_resolver_scenario_vs_fill_conflict():
    """Rule 027 / 053 / 106: Scenario LIMIT, fill snapshot MARKET -> PROVENANCE_CONFLICT."""
    order = PaperOrder(id="po_scen_fill", instrument="XAUUSDT", direction="LONG", order_type="LIMIT")
    scen = make_test_scenario_snapshot(entry_mode="LIMIT_AT_CE_AFTER_CONFIRMATION", order_type="LIMIT")
    fill = {"entry_mode": "CONFIRMED_RETEST_MARKET", "order_type": "MARKET"}

    res = resolve_historical_entry_mode(order=order, scenario_snapshot=scen, fill_snapshot=fill)
    assert res.verification_status == "PROVENANCE_CONFLICT"
    assert any("PROVENANCE_CONFLICT_ENTRY_MODE" in iss for iss in res.issues)


def test_005_resolver_arm_vs_fill_conflict():
    """Rule 055 / 107: No scenario, ARM LIMIT, fill MARKET -> PROVENANCE_CONFLICT."""
    order = PaperOrder(id="po_arm_fill", instrument="XAUUSDT", direction="LONG", order_type="LIMIT")
    arm = {"entry_mode": "LIMIT", "order_type": "LIMIT"}
    fill = {"entry_mode": "MARKET", "order_type": "MARKET"}

    res = resolve_historical_entry_mode(order=order, scenario_snapshot=None, arm_snapshot=arm, fill_snapshot=fill)
    assert res.verification_status == "PROVENANCE_CONFLICT"
    assert any("PROVENANCE_CONFLICT_ENTRY_MODE" in iss for iss in res.issues)


def test_006_resolver_intra_source_snapshot_conflict():
    """Rule 051 / 108: Snapshot internally has entry_mode LIMIT but order_type MARKET -> conflict."""
    order = PaperOrder(id="po_intra", instrument="XAUUSDT", direction="LONG", order_type="LIMIT")
    scen = make_test_scenario_snapshot(entry_mode="LIMIT_AT_CE_AFTER_CONFIRMATION", order_type="MARKET")

    res = resolve_historical_entry_mode(order=order, scenario_snapshot=scen)
    assert res.verification_status == "PROVENANCE_CONFLICT"
    assert any("CONFLICT" in iss for iss in res.issues)


def test_007_resolver_fallback_from_order_type():
    """Rule 025 / 109: Only order_type LIMIT, no mode -> UNVERIFIED, FALLBACK_ORDER_TYPE."""
    order = PaperOrder(id="po_fallback", instrument="XAUUSDT", direction="LONG", order_type="LIMIT")

    res = resolve_historical_entry_mode(order=order)
    assert res.verification_status == "UNVERIFIED"
    assert res.source == "FALLBACK_ORDER_TYPE"
    assert res.entry_mode == "LIMIT_AT_CE_AFTER_CONFIRMATION"
    assert "FALLBACK_MODE_FROM_ORDER_TYPE" in res.issues


def test_008_resolver_missing_and_invalid_mode():
    """Rule 036, 063, 064: Completely missing mode returns UNVERIFIED; invalid string returns INVALID_MODE."""
    # Completely missing
    order_none = PaperOrder(id="po_none", instrument="XAUUSDT", direction="LONG", order_type=None)
    res_none = resolve_historical_entry_mode(order=order_none)
    assert res_none.verification_status == "UNVERIFIED"
    assert res_none.source == "UNKNOWN"
    assert "MISSING_HISTORICAL_ENTRY_MODE" in res_none.issues

    # Invalid mode
    arm_invalid = {"entry_mode": "TURBO_FAST_ENTRY"}
    res_invalid = resolve_historical_entry_mode(order=None, arm_snapshot=arm_invalid)
    assert res_invalid.verification_status == "INVALID_MODE"
    assert any("INVALID_ENTRY_MODE" in iss for iss in res_invalid.issues)


# ==============================================================================
# GROUP 2: COMPLIANCE INTEGRATION & FAIL-CLOSED BEHAVIOR (Rules 071 - 100, 110 - 115)
# ==============================================================================

def test_010_compliance_fallback_unverified_fails_closed():
    """
    Rule 025 / 110:
    When resolver returns UNVERIFIED (from FALLBACK_ORDER_TYPE),
    compliance for CG01 MUST NOT return VERIFIED_COMPLIANT!
    It must return UNVERIFIABLE with reason 'Chưa đủ dữ liệu xác minh chế độ vào lệnh lịch sử.'
    checks_failed has HISTORICAL_ENTRY_MODE_UNVERIFIED.
    Partial checks (PRICE_GEOMETRY, TRIGGER_CHAIN_VERIFIED) are preserved in checks_passed.
    Lesson classification is YELLOW (UNVERIFIABLE_CAUSE).
    """
    now_ms = 1700000000000
    ev_nodes = [
        {"id": "p1", "kind": "POI", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 10000, "occurred_at_ms": now_ms - 10000},
        {"id": "sw1", "kind": "SWEEP", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 8000, "occurred_at_ms": now_ms - 8000},
        {"id": "ms1", "kind": "MSS", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 5000, "occurred_at_ms": now_ms - 5000},
        {"id": "f1", "kind": "FVG", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 3000, "occurred_at_ms": now_ms - 3000},
        {"id": "tr1", "kind": "ENTRY_TRIGGER", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 1000, "occurred_at_ms": now_ms - 1000},
    ]
    tc = {"verified": True, "model_id": "CG01", "node_ids": [n["id"] for n in ev_nodes]}

    # Order has only order_type="LIMIT" (legacy fallback, no mode)
    order = PaperOrder(
        id="po_legacy_1",
        instrument="XAUUSDT",
        direction="LONG",
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        order_type="LIMIT",
        strategy_family="CG01",
    )

    entry_snap = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "planned_entry": 2650.0,
        "planned_sl": 2645.0,
        "planned_tp": 2660.0,
        "decision_as_of_ms": now_ms,
        "model_id": "CG01",
        "evidence": ev_nodes,
        "trigger_chain": tc,
        # NO entry_mode or plan here; resolver falls back to order.order_type LIMIT
    }

    comp = assess_entry_compliance_for_review(order=order, entry_snapshot=entry_snap, decision_as_of_ms=now_ms)
    assert comp.status == "UNVERIFIABLE"
    assert comp.status != "VERIFIED_COMPLIANT"
    assert "Chưa đủ dữ liệu xác minh chế độ vào lệnh lịch sử." in comp.reason
    assert "HISTORICAL_ENTRY_MODE_UNVERIFIED" in comp.checks_failed
    assert "HISTORICAL_ENTRY_MODE_VERIFICATION" in comp.missing_evidence
    # Rule 080: Partial checks preserved!
    assert "PRICE_GEOMETRY" in comp.checks_passed
    assert "TRIGGER_CHAIN_VERIFIED" in comp.checks_passed

    # Rule 083: Lesson classification must be YELLOW
    rev = PostTradeReview(
        order_id="po_legacy_1",
        symbol="XAUUSDT",
        model_id="CG01",
        direction="LONG",
        planned_entry=2650.0,
        actual_entry=2650.1,
        planned_sl=2645.0,
        planned_tp=2660.0,
        exit_price=2660.0,
        exit_cause="TP_HIT",
        gross_pnl=9.9,
        net_pnl=9.0,
        realized_rr=2.0,
        hold_time_ms=60000,
        entry_slippage_usd=0.1,
        loss_classification=None,
        outcome_status="WIN",
        as_of_ms=now_ms + 60000,
    )
    classification = classify_post_trade_lesson(rev, entry_snapshot=entry_snap, compliance_result=comp)
    assert classification.suggested_color == "YELLOW"
    assert classification.classification_code == "UNVERIFIABLE_CAUSE"


def test_011_compliance_provenance_conflict_fails_closed():
    """Rule 073: Mode PROVENANCE_CONFLICT results in UNVERIFIABLE with conflict reason."""
    now_ms = 1700000000000
    ev_nodes = [
        {"id": "p1", "kind": "POI", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 10000, "occurred_at_ms": now_ms - 10000},
        {"id": "ms1", "kind": "MSS", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 5000, "occurred_at_ms": now_ms - 5000},
        {"id": "f1", "kind": "FVG", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 3000, "occurred_at_ms": now_ms - 3000},
    ]
    tc = {"verified": True, "model_id": "CG01", "node_ids": [n["id"] for n in ev_nodes]}

    order = PaperOrder(id="po_conf_1", instrument="XAUUSDT", direction="LONG", order_type="LIMIT", strategy_family="CG01")
    # Scenario has LIMIT, but fill has MARKET -> PROVENANCE_CONFLICT
    entry_snap = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "planned_entry": 2650.0,
        "planned_sl": 2645.0,
        "planned_tp": 2660.0,
        "decision_as_of_ms": now_ms,
        "model_id": "CG01",
        "plan": {"entry_mode": "LIMIT_AT_CE_AFTER_CONFIRMATION", "order_type": "LIMIT"},
        "evidence": ev_nodes,
        "trigger_chain": tc,
    }
    fill_snap = {"entry_mode": "CONFIRMED_RETEST_MARKET", "order_type": "MARKET"}

    comp = assess_entry_compliance_for_review(
        order=order,
        entry_snapshot=entry_snap,
        fill_snapshot=fill_snap,
        decision_as_of_ms=now_ms,
    )
    assert comp.status == "UNVERIFIABLE"
    assert "không thống nhất" in comp.reason or "xung đột" in comp.reason.lower()
    assert "HISTORICAL_ENTRY_MODE_CONFLICT" in comp.checks_failed


def test_012_compliance_non_cg01_models_preserved():
    """Rule 075: Non-CG01 models (CONTINUATION, SWEEP_RECLAIM) do not require CG01 modes and are not broken."""
    now_ms = 1700000000000
    cont_snap = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "planned_entry": 2650.0,
        "planned_sl": 2645.0,
        "planned_tp": 2660.0,
        "model_id": "CONTINUATION",
        "decision_as_of_ms": now_ms,
        "evidence": [
            {"id": "t1", "kind": "BOS", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 1000},
        ],
        "trigger_chain": {"verified": True, "model_id": "CONTINUATION", "node_ids": ["t1"]},
    }
    comp_cont = assess_entry_compliance_for_review(entry_snapshot=cont_snap, decision_as_of_ms=now_ms)
    assert comp_cont.status == "VERIFIED_COMPLIANT"


def test_013_compliance_verified_limit_compliant():
    """Rule 100 / 103: Verified LIMIT returns VERIFIED_COMPLIANT and GREEN lesson."""
    now_ms = 1700000000000
    ev_nodes = [
        {"id": "p1", "kind": "POI", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 10000, "occurred_at_ms": now_ms - 10000},
        {"id": "sw1", "kind": "SWEEP", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 8000, "occurred_at_ms": now_ms - 8000},
        {"id": "ms1", "kind": "MSS", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 5000, "occurred_at_ms": now_ms - 5000},
        {"id": "f1", "kind": "FVG", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 3000, "occurred_at_ms": now_ms - 3000},
        {"id": "tr1", "kind": "ENTRY_TRIGGER", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 1000, "occurred_at_ms": now_ms - 1000},
    ]
    tc = {"verified": True, "model_id": "CG01", "node_ids": [n["id"] for n in ev_nodes]}

    order = PaperOrder(id="po_verif_1", instrument="XAUUSDT", direction="LONG", order_type="LIMIT", strategy_family="CG01")
    entry_snap = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "planned_entry": 2650.0,
        "planned_sl": 2645.0,
        "planned_tp": 2660.0,
        "decision_as_of_ms": now_ms,
        "model_id": "CG01",
        "plan": {"entry_mode": "LIMIT_AT_CE_AFTER_CONFIRMATION", "order_type": "LIMIT"},
        "evidence": ev_nodes,
        "trigger_chain": tc,
    }

    comp = assess_entry_compliance_for_review(order=order, entry_snapshot=entry_snap, decision_as_of_ms=now_ms)
    assert comp.status == "VERIFIED_COMPLIANT"
    assert "Lệnh chờ tại CE sau khi cấu trúc được xác nhận" in comp.reason

    rev = PostTradeReview(
        order_id="po_verif_1",
        symbol="XAUUSDT",
        model_id="CG01",
        direction="LONG",
        planned_entry=2650.0,
        actual_entry=2650.0,
        planned_sl=2645.0,
        planned_tp=2660.0,
        exit_price=2660.0,
        exit_cause="TP_HIT",
        gross_pnl=10.0,
        net_pnl=9.5,
        realized_rr=2.0,
        hold_time_ms=60000,
        entry_slippage_usd=0.0,
        loss_classification=None,
        outcome_status="WIN",
        as_of_ms=now_ms + 60000,
    )
    classification = classify_post_trade_lesson(rev, entry_snapshot=entry_snap, compliance_result=comp)
    assert classification.suggested_color == "GREEN"
    assert classification.classification_code == "PLAN_COMPLIANT_WIN"


# ==============================================================================
# GROUP 3: HTTP API APPROVAL & ACTIVATION ACCEPTANCE (Rules 121 - 140, 165 - 170)
# ==============================================================================

def test_020_api_approval_not_found_404(client):
    """Rule 167: Unknown lesson ID returns 404 with code LESSON_NOT_FOUND."""
    resp = client.post("/api/v1/lessons/999999/approve-and-activate", json={"expected_revision": 1})
    assert resp.status_code == 404
    detail = resp.json().get("detail", {})
    assert detail.get("code") == "LESSON_NOT_FOUND"


def test_021_api_approval_stale_revision_409(client, isolated_db):
    """Rule 166: Stale revision returns 409 and leaves DB state intact."""
    now_ms = int(time.time() * 1000)
    lesson = Lesson(
        id=888,
        created_at=now_ms,
        related_trade_id="po_test_409",
        strategy_family="CG01_SWEEP_MSS_FVG",
        title="Stale revision test lesson",
        reflection="Test reflection",
        action_rule="Test action rule",
        status="PENDING_REVIEW",
        is_approved=False,
        enabled=False,
        severity="WARNING",
        effect="DEDUCT_SCORE",
        revision=1,
    )
    isolated_db.add(lesson)
    isolated_db.commit()

    resp = client.post("/api/v1/lessons/888/approve-and-activate", json={"expected_revision": 99})
    assert resp.status_code == 409
    assert resp.json().get("detail", {}).get("code") == "REVISION_MISMATCH"

    # Verify DB unchanged
    l_db = isolated_db.query(Lesson).filter_by(id=888).first()
    assert l_db.is_approved is False
    assert l_db.enabled is False
    assert l_db.revision == 1


def test_022_api_approval_invalid_predicate_422_atomicity(client, isolated_db):
    """Rule 165, 169: Invalid predicate returns 422, rollbacks transaction atomically."""
    now_ms = int(time.time() * 1000)
    lesson = Lesson(
        id=889,
        created_at=now_ms,
        related_trade_id="po_test_422",
        strategy_family="CG01_SWEEP_MSS_FVG",
        title="Invalid predicate test lesson",
        reflection="Test reflection",
        action_rule="Test action rule",
        status="PENDING_REVIEW",
        is_approved=False,
        enabled=False,
        severity="WARNING",
        effect="DEDUCT_SCORE",
        revision=1,
    )
    isolated_db.add(lesson)
    isolated_db.commit()

    bad_payload = {
        "expected_revision": 1,
        "selected_severity": "WARNING",
        "selected_effect": "DEDUCT_SCORE",
        "score_delta": -5.0,
        "predicate": {"metric": "magic_unsupported_indicator", "operator": "<=", "value": 0.5},
        "activate": True,
    }
    resp = client.post("/api/v1/lessons/889/approve-and-activate", json=bad_payload)
    assert resp.status_code == 422

    # Atomicity: verify lesson is completely untouched
    l_db = isolated_db.query(Lesson).filter_by(id=889).first()
    assert l_db.is_approved is False
    assert l_db.enabled is False
    assert l_db.status == "PENDING_REVIEW"
    assert l_db.revision == 1


def test_023_api_approve_and_activate_success_200(client, isolated_db):
    """
    Rules 122, 137, 138, 139, 140:
    Full HTTP approve-and-activate acceptance:
    Valid payload returns 200, persists fields, increments revision, writes audit trail, invalidates cache.
    """
    now_ms = int(time.time() * 1000)
    lesson = Lesson(
        id=890,
        created_at=now_ms,
        related_trade_id="po_test_200",
        strategy_family="CG01_SWEEP_MSS_FVG",
        title="High spread penalty lesson",
        reflection="Spread exceeded budget before ARM.",
        action_rule="Deduct score when spread is high.",
        status="PENDING_REVIEW",
        is_approved=False,
        enabled=False,
        severity="WARNING",
        effect="DEDUCT_SCORE",
        revision=1,
    )
    isolated_db.add(lesson)
    isolated_db.commit()

    good_payload = {
        "expected_revision": 1,
        "selected_severity": "WARNING",
        "selected_effect": "DEDUCT_SCORE",
        "score_delta": -5.0,
        "predicate": {"metric": "spread", "operator": "<=", "threshold": 0.50, "score_delta": -5.0},
        "scope": {"symbol": "XAUUSDT", "strategy_family": "CG01_SWEEP_MSS_FVG", "stage": "BEFORE_ARM", "execution_mode": "PAPER", "session": "ALL"},
        "activate": True,
        "review_note": "Approved by senior trader after morning review",
    }
    resp = client.post("/api/v1/lessons/890/approve-and-activate", json=good_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_approved"] is True
    assert data["enabled"] is True
    assert data["status"] == "APPROVED"
    assert data["validation_status"] == "VALID"
    assert data["revision"] == 2
    assert data["effective_at"] is not None

    # DB persistence assertion (Rule 139)
    db_l = isolated_db.query(Lesson).filter_by(id=890).first()
    assert db_l.is_approved is True
    assert db_l.enabled is True
    assert db_l.status == "APPROVED"
    assert db_l.revision == 2
    assert db_l.validation_status == "VALID"

    # Audit trail assertion (Rule 140)
    audit = json.loads(db_l.audit_trail)
    assert any(a.get("action") == "APPROVED_AND_ACTIVATED" and a.get("revision") == 2 for a in audit)


# ==============================================================================
# GROUP 4: ACTIVE LESSON EFFECT ON SCORER COMPONENT THRESHOLD CROSSING (Rules 042, 043, 141 - 164)
# ==============================================================================

def test_030_scorer_component_lesson_effect_threshold_crossing(client, isolated_db):
    """
    Component-level test (Scorer & LessonRuleService):
    1. Baseline candidate has base score = 72.5 (>= 70.0 threshold) -> ELIGIBLE_BY_SCORE.
    2. Pending lesson has lesson_delta = 0.0 -> no effect.
    3. HTTP approve-and-activate activates penalty rule (-5.0).
    4. Evaluated through production scorer and rule service: applied_delta = -5.0.
    5. Final score = 72.5 - 5.0 = 67.5 < 70.0 -> decision crosses to BELOW_THRESHOLD!
    6. HTTP disable endpoint restores decision to ELIGIBLE_BY_SCORE.
    Note: Full production integration through orchestrator evaluate_cycle -> _arm_paper_candidate
    is officially tested in test_v16_39_8_production_lesson_arm_acceptance.py.
    """
    now_ms = 1700000000000

    # 1. Seed candidate with base score 72.5
    ev_nodes = [
        {"id": "c1", "kind": "CONTEXT", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 20000, "extra": {"htf_bias": "BULLISH"}},
        {"id": "a1", "kind": "ALIGNMENT", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 15000, "extra": {"h1_direction": "LONG", "m15_direction": "LONG"}},
        {"id": "p1", "kind": "POI", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 10000, "extra": {"mitigated": False, "fresh": True}},
        {"id": "sw1", "kind": "SWEEP", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 8000, "occurred_at_ms": now_ms - 8000},
        {"id": "ms1", "kind": "MSS", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 5000, "occurred_at_ms": now_ms - 5000},
        {"id": "dp1", "kind": "DISPLACEMENT", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 4000, "occurred_at_ms": now_ms - 4000, "extra": {"displacement_ratio": 0.85}},
        {"id": "f1", "kind": "FVG", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 3000, "occurred_at_ms": now_ms - 3000},
        {"id": "tr1", "kind": "ENTRY_TRIGGER", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 1000, "occurred_at_ms": now_ms - 1000},
    ]

    cand = ScalpCandidate(
        is_eligible=True,
        candidate_key="cand_crossing_test",
        model_id=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        direction="LONG",
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        gross_rr=2.0,
        net_rr=1.8,
        as_of_ms=now_ms,
        evidence=ev_nodes,
    )

    quote = PriceQuote(symbol="XAUUSDT", bid=2649.85, ask=2650.15, timestamp=now_ms)  # spread = 0.30
    norm_ev = normalize_candidate_evidence(cand, as_of_ms=now_ms)
    ctx_cand = build_candidate_scoring_context(
        cand,
        normalized_evidence=norm_ev,
        quote=quote,
        as_of_ms=now_ms,
        market_context={"context_source": "BOUND_SNAPSHOT", "htf_bias": "BULLISH", "session": "NY"},
    )
    ctx_cand["calculation"] = {"slippage_usd": 0.20, "net_rr": 1.8}
    ctx_cand["sr_zones"] = [{"zone_id": "sr_1", "type": "SUPPORT"}]
    profile = build_adaptive_smc_paper_profile()

    # Baseline evaluation before lesson: base_score == 72.5 >= 70.0 -> ELIGIBLE_BY_SCORE
    as_base = assess_candidate_quality(cand, context=ctx_cand, profile=profile, lesson_delta=0.0, as_of_ms=now_ms)
    assert as_base.base_score == 72.5
    assert as_base.final_score == 72.5
    assert as_base.decision == "ELIGIBLE_BY_SCORE"

    # 2. Seed pending lesson in DB
    lesson = Lesson(
        id=905,
        created_at=now_ms,
        related_trade_id="po_crossing_source",
        strategy_family="CG01_SWEEP_MSS_FVG",
        title="Excessive spread penalty rule",
        reflection="Spread widened beyond budget before entry.",
        action_rule="Deduct score when spread is high.",
        status="PENDING_REVIEW",
        is_approved=False,
        enabled=False,
        severity="WARNING",
        effect="DEDUCT_SCORE",
        revision=1,
    )
    isolated_db.add(lesson)
    isolated_db.commit()

    # Scope context for orchestrator evaluation
    eval_ctx = dict(ctx_cand)
    eval_ctx["symbol"] = cand.symbol
    eval_ctx["direction"] = cand.direction
    eval_ctx["strategy_family"] = cand.model_id
    eval_ctx["execution_mode"] = "PAPER"
    eval_ctx["stage"] = "BEFORE_ARM"
    eval_ctx["session"] = "NY"
    eval_ctx["spread"] = 0.30

    # Pending lesson has delta 0 (Rule 141)
    adj_pending = LessonRuleService.evaluate_lesson_score_adjustments(eval_ctx, db=isolated_db, now_ms=now_ms)
    assert adj_pending["applied_delta"] == 0.0

    # 3. HTTP Approve & Activate (Rules 122, 142)
    resp = client.post(
        "/api/v1/lessons/905/approve-and-activate",
        json={
            "expected_revision": 1,
            "selected_severity": "WARNING",
            "selected_effect": "DEDUCT_SCORE",
            "score_delta": -5.0,
            "predicate": {"metric": "spread", "operator": "<=", "threshold": 0.50, "score_delta": -5.0},
            "scope": {"symbol": "XAUUSDT", "strategy_family": "CG01_SWEEP_MSS_FVG", "stage": "BEFORE_ARM", "execution_mode": "PAPER", "session": "ALL"},
            "activate": True,
        },
    )
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["enabled"] is True
    effective_at = res_data["effective_at"]

    # Rule 164: Candidate before effective_at is NOT affected
    adj_before = LessonRuleService.evaluate_lesson_score_adjustments(eval_ctx, db=isolated_db, now_ms=effective_at - 1000)
    assert adj_before["applied_delta"] == 0.0

    # 4. Active rule applies -5.0 penalty after effective_at (Rule 143)
    eval_ms = effective_at + 1000
    adj_active = LessonRuleService.evaluate_lesson_score_adjustments(eval_ctx, db=isolated_db, now_ms=eval_ms)
    assert adj_active["applied_delta"] == -5.0
    assert any(r.get("rule_id") == 905 for r in adj_active["matched_rules"])

    # 5. Threshold crossing (Rules 150, 151, 154):
    # 72.5 - 5.0 = 67.5 < 70.0 -> decision becomes BELOW_THRESHOLD!
    as_penalized = assess_candidate_quality(
        cand,
        context=ctx_cand,
        profile=profile,
        lesson_delta=adj_active["applied_delta"],
        as_of_ms=now_ms,
    )
    assert as_penalized.base_score == 72.5
    assert as_penalized.lesson_delta == -5.0
    assert as_penalized.final_score == 67.5
    assert as_penalized.decision == "BELOW_THRESHOLD"
    assert as_penalized.final_score < as_penalized.threshold

    # 6. Disable rule via API (Rules 158, 159, 160)
    resp_disable = client.post("/api/v1/lessons/905/set-enable", json={"enabled": False, "expected_revision": 2})
    assert resp_disable.status_code == 200
    assert resp_disable.json()["enabled"] is False

    # After disable: delta restores to 0.0 and decision restores to ELIGIBLE_BY_SCORE
    adj_restored = LessonRuleService.evaluate_lesson_score_adjustments(eval_ctx, db=isolated_db, now_ms=eval_ms)
    assert adj_restored["applied_delta"] == 0.0

    as_restored = assess_candidate_quality(
        cand,
        context=ctx_cand,
        profile=profile,
        lesson_delta=adj_restored["applied_delta"],
        as_of_ms=now_ms,
    )
    assert as_restored.final_score == 72.5
    assert as_restored.decision == "ELIGIBLE_BY_SCORE"


# Compatibility alias without test_ prefix to avoid duplicate pytest collection (Rule 025, 026)
orchestrator_lesson_effect_threshold_crossing = test_030_scorer_component_lesson_effect_threshold_crossing


def test_031_scope_negative_tests(isolated_db):
    """Rules 161, 162, 163: Out of scope candidates (model, symbol, session) are NOT penalized."""
    now_ms = 1700000000000
    lesson = Lesson(
        id=906,
        created_at=now_ms,
        related_trade_id="po_scope_test",
        strategy_family="CG01_SWEEP_MSS_FVG",
        title="Scope test rule",
        reflection="Scope reflection",
        action_rule="Scope action rule",
        status="APPROVED",
        is_approved=True,
        enabled=True,
        validation_status="VALID",
        effective_at=now_ms - 1000,
        severity="WARNING",
        effect="DEDUCT_SCORE",
        scope=json.dumps({"symbol": "XAUUSDT", "strategy_family": "CG01_SWEEP_MSS_FVG", "session": "NY"}),
        predicate=json.dumps({"metric": "spread", "operator": "<=", "threshold": 0.50, "score_delta": -5.0}),
        revision=2,
    )
    isolated_db.add(lesson)
    isolated_db.commit()
    LessonRuleService.invalidate_cache()

    # In-scope: matching symbol, family, session NY
    ctx_in = {"symbol": "XAUUSDT", "strategy_family": "CG01_SWEEP_MSS_FVG", "session": "NY", "spread": 0.30}
    adj_in = LessonRuleService.evaluate_lesson_score_adjustments(ctx_in, db=isolated_db, now_ms=now_ms)
    assert adj_in["applied_delta"] == -5.0

    # Model mismatch: CONTINUATION
    ctx_model = {"symbol": "XAUUSDT", "strategy_family": "CONTINUATION", "session": "NY", "spread": 0.30}
    adj_model = LessonRuleService.evaluate_lesson_score_adjustments(ctx_model, db=isolated_db, now_ms=now_ms)
    assert adj_model["applied_delta"] == 0.0

    # Symbol mismatch: BTCUSDT
    ctx_sym = {"symbol": "BTCUSDT", "strategy_family": "CG01_SWEEP_MSS_FVG", "session": "NY", "spread": 0.30}
    adj_sym = LessonRuleService.evaluate_lesson_score_adjustments(ctx_sym, db=isolated_db, now_ms=now_ms)
    assert adj_sym["applied_delta"] == 0.0

    # Session mismatch: LONDON
    ctx_sess = {"symbol": "XAUUSDT", "strategy_family": "CG01_SWEEP_MSS_FVG", "session": "LONDON", "spread": 0.30}
    adj_sess = LessonRuleService.evaluate_lesson_score_adjustments(ctx_sess, db=isolated_db, now_ms=now_ms)
    assert adj_sess["applied_delta"] == 0.0


# ==============================================================================
# GROUP 5: FULL PRODUCTION LIFECYCLE WITH HISTORICAL MODE TRUST (Rules 171 - 180)
# ==============================================================================

def test_040_full_paper_lifecycle_long_limit_verified_provenance(isolated_db):
    """
    Rules 171 - 177:
    Full verified paper lifecycle for LONG LIMIT:
    Candidate -> ARM -> execute_fill -> process_exit_tick -> _create_lesson.
    Verifies lesson facts_snapshot has verified entry_mode and source.
    """
    now_ms = 1700000000000

    calc = calculate_risk_reward(
        direction="LONG",
        entry=2650.0,
        sl=2645.0,
        tp=2660.0,
        capital=1000.0,
        risk_pct=0.01,
        min_net_rr=1.5,
        now_ms=now_ms,
    )
    assert calc.is_valid

    ev_long = [
        {"id": "p1", "kind": "POI", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 10000, "occurred_at_ms": now_ms - 10000},
        {"id": "sw1", "kind": "SWEEP", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 8000, "occurred_at_ms": now_ms - 8000},
        {"id": "ms1", "kind": "MSS", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 5000, "occurred_at_ms": now_ms - 5000},
        {"id": "f1", "kind": "FVG", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 3000, "occurred_at_ms": now_ms - 3000},
        {"id": "tr1", "kind": "ENTRY_TRIGGER", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 1000, "occurred_at_ms": now_ms - 1000},
    ]
    tc_long = {"verified": True, "model_id": "CG01", "node_ids": [x["id"] for x in ev_long]}

    snap_long = make_test_scenario_snapshot(
        scenario_id="scen_prod_long",
        instance_id="inst_prod_long",
        entry_mode="LIMIT_AT_CE_AFTER_CONFIRMATION",
        order_type="LIMIT",
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        now_ms=now_ms,
        evidence=ev_long,
        trigger_chain=tc_long,
    )
    save_immutable_scenario_snapshot(isolated_db, snap_long)

    arm_decision_snap = {
        "scenario_snapshot_id": snap_long.snapshot_id,
        "entry_mode": "LIMIT_AT_CE_AFTER_CONFIRMATION",
        "order_type": "LIMIT",
        "analysis_session": "NY",
        "evidence": ev_long,
        "trigger_chain": tc_long,
    }

    order_long = PaperOrder(
        id="po_prod_long_1",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="LIMIT",
        quantity=calc.quantity,
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        initial_risk_usdt=calc.net_risk_usdt,
        created_at=now_ms,
        armed_at=now_ms,
        strategy_family=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        setup_instance_id="cand_prod_long_1",
        scenario_snapshot_id=snap_long.snapshot_id,
        arm_decision_snapshot=json.dumps(arm_decision_snap),
    )
    isolated_db.add(order_long)
    isolated_db.commit()

    # Fill
    filled = TradeLifecycleService.execute_fill(
        db=isolated_db,
        order=order_long,
        fill_price=2650.0,
        calc_result=calc,
        now_ms=now_ms + 1000,
    )
    assert filled.state == "paper_open"

    # Exit TP
    closed = TradeLifecycleService.process_exit_tick(
        db=isolated_db,
        current_bid=2660.5,
        current_ask=2660.7,
        now_ms=now_ms + 15000,
    )
    assert closed is not None
    assert closed.state == "closed"

    lesson = isolated_db.query(Lesson).filter_by(related_trade_id=order_long.id).first()
    assert lesson is not None
    assert lesson.status == "PENDING_REVIEW"
    assert lesson.is_approved is False
    assert lesson.enabled is False
    facts = json.loads(lesson.facts_snapshot)
    assert facts.get("entry_mode") == "LIMIT_AT_CE_AFTER_CONFIRMATION"
    assert facts.get("order_type") == "LIMIT"
    assert facts.get("entry_mode_source") == "SCENARIO_SNAPSHOT"
    assert facts.get("verification_status") == "VERIFIED"


def test_041_full_paper_lifecycle_legacy_order_unverified_provenance(isolated_db):
    """
    Rules 025, 083, 114:
    Legacy order without bound scenario snapshot has UNVERIFIED mode provenance.
    After close, review is UNVERIFIABLE and lesson color is YELLOW.
    """
    now_ms = 1700000000000

    calc = calculate_risk_reward(
        direction="LONG",
        entry=2650.0,
        sl=2645.0,
        tp=2660.0,
        capital=1000.0,
        risk_pct=0.01,
        min_net_rr=1.5,
        now_ms=now_ms,
    )

    # Legacy order: no scenario snapshot, no arm snapshot
    order_legacy = PaperOrder(
        id="po_legacy_review_1",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="LIMIT",
        quantity=calc.quantity,
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        initial_risk_usdt=calc.net_risk_usdt,
        created_at=now_ms,
        armed_at=now_ms,
        strategy_family=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        setup_instance_id="cand_legacy_1",
        scenario_snapshot_id=None,
        arm_decision_snapshot=None,
    )
    isolated_db.add(order_legacy)
    isolated_db.commit()

    filled = TradeLifecycleService.execute_fill(
        db=isolated_db,
        order=order_legacy,
        fill_price=2650.0,
        calc_result=calc,
        now_ms=now_ms + 1000,
    )
    assert filled.state == "paper_open"

    closed = TradeLifecycleService.process_exit_tick(
        db=isolated_db,
        current_bid=2660.5,
        current_ask=2660.7,
        now_ms=now_ms + 15000,
    )
    assert closed is not None
    assert closed.state == "closed"

    lesson = isolated_db.query(Lesson).filter_by(related_trade_id=order_legacy.id).first()
    assert lesson is not None
    facts = json.loads(lesson.facts_snapshot)
    assert facts.get("entry_mode_source") == "FALLBACK_ORDER_TYPE"
    assert facts.get("verification_status") == "UNVERIFIED"
    assert facts.get("suggested_color") == "YELLOW"
    assert facts.get("classification_code") == "UNVERIFIABLE_CAUSE"
