import pytest
import json
import time
import math
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from models import Base, PaperOrder, Lesson, PostTradeReviewRecord, LessonRepairTask
from services.order_plan_snapshot_service import (
    build_arm_plan_snapshot,
    build_fill_plan_snapshot,
    extract_immutable_entry_metadata,
)
from services.post_trade_review_service import (
    build_entry_review_context,
    assess_entry_compliance_for_review,
    classify_post_trade_lesson,
    classify_review_provenance_issues,
    resolve_model_review_requirements,
    LossClassification,
    PostTradeReview,
)
from services.trade_lifecycle_service import TradeLifecycleService
from services.lesson_rule_service import LessonRuleService
from services.lesson_repair_worker import LessonRepairWorker
from services.execution_coordinator import ExecutionCoordinator
from services.trading_policy_service import TradingPolicyService


@pytest.fixture
def isolated_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        policy = TradingPolicyService.get_or_create_policy(session)
        policy.max_daily_fills = 3
        policy.daily_min_goal = 2
        policy.ny_min_fills = 1
        policy.is_active = False
        session.commit()
        yield session
    finally:
        session.close()


# ==============================================================================
# SECTION 027 & 078: LESSON SESSION NOT OVERWRITTEN BY LIVE_PAPER
# ==============================================================================
def test_027_078_reproduce_lesson_session_preserves_market_session_not_live_paper(isolated_db):
    """
    Section 027, 061-065, 078:
    Verify that when execute_close is invoked with default session_tag='LIVE_PAPER',
    the created Lesson preserves the frozen market entry session ('NY') in lesson.session,
    and stores 'LIVE_PAPER' as execution origin in facts_snapshot.
    """
    now_ms = 1700000000000
    cost_data = {
        "entry_session": "NY",
        "session": "NY",
        "analysis_session": "NY",
        "session_policy_version": "v1.2",
        "decision_as_of_ms": now_ms - 60000,
        "planned_entry": 2650.0,
        "stop_loss": 2645.0,
        "take_profit": 2660.0,
    }
    order = PaperOrder(
        id="ord_sess_027",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=2650.0,
        actual_entry=2650.1,
        stop_loss=2645.0,
        take_profit=2660.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=now_ms - 120000,
        strategy_family="CG01",
        opened_at=now_ms - 60000,
        cost_snapshot=json.dumps(cost_data),
    )
    isolated_db.add(order)
    isolated_db.commit()

    # Normal close with default session_tag="LIVE_PAPER"
    closed_order = TradeLifecycleService.execute_close(
        isolated_db,
        order_id="ord_sess_027",
        exit_price=2660.0,
        exit_cause="TP_HIT",
        now_ms=now_ms,
        session_tag="LIVE_PAPER",
    )
    isolated_db.commit()

    assert closed_order.state == "closed"
    lesson = isolated_db.query(Lesson).filter_by(related_trade_id="ord_sess_027").first()
    assert lesson is not None
    # Section 027 / 078: Lesson.session MUST be 'NY', NOT 'LIVE_PAPER'!
    assert lesson.session == "NY"

    facts = json.loads(lesson.facts_snapshot)
    assert facts.get("entry_session") == "NY"
    assert facts.get("execution_origin") == "LIVE_PAPER"
    assert facts.get("origin") == "LIVE_PAPER"


# ==============================================================================
# SECTION 028, 049, 052, 059: FILL SNAPSHOT PRESERVES ARM METADATA
# ==============================================================================
def test_028_059_cost_snapshot_preserves_arm_metadata_after_fill():
    """
    Section 028, 049, 051, 052, 059:
    extract_immutable_entry_metadata whitelists immutable entry metadata from arm_snapshot,
    and build_fill_plan_snapshot injects it into fill plan snapshot without blind overwrites.
    """
    arm_plan = {
        "phase": "ARMED_ESTIMATE",
        "entry_session": "NY",
        "analysis_session": "NY",
        "session": "NY",
        "session_policy_version": "2026.1",
        "decision_as_of_ms": 1700000000000,
        "armed_at_ms": 1700000000000,
        "setup_id": "setup_xau_01",
        "setup_instance_id": "inst_cg01_99",
        "signal_id": "sig_555",
        "idempotency_key": "idem_123",
        "min_net_rr_at_arm": 1.5,
        "planned_entry": 2650.0,
        "stop_loss": 2645.0,
        "take_profit": 2660.0,
        "estimated_fee_usd": 1.0,
    }

    meta = extract_immutable_entry_metadata(arm_plan)
    assert meta["entry_session"] == "NY"
    assert meta["analysis_session"] == "NY"
    assert meta["session_policy_version"] == "2026.1"
    assert meta["decision_as_of_ms"] == 1700000000000
    assert meta["setup_instance_id"] == "inst_cg01_99"
    assert meta["min_net_rr_at_arm"] == 1.5
    assert "estimated_fee_usd" not in meta  # Estimated financials NOT copied!

    # Test build_fill_plan_snapshot
    calc_res = MagicMock()
    calc_res.quantity = 1.0
    calc_res.multiplier = 1.0
    calc_res.net_rr = 2.1
    calc_res.gross_rr = 2.2
    calc_res.net_risk_usdt = 5.0
    calc_res.gross_loss_usdt = 5.0
    calc_res.gross_reward_usdt = 11.0
    calc_res.net_reward_usdt = 10.5
    calc_res.entry_fee_usdt = 0.5
    calc_res.sl_exit_fee_usdt = 0.5
    calc_res.tp_exit_fee_usdt = 0.5
    calc_res.entry_slippage_usdt = 0.1
    calc_res.sl_exit_slippage_usdt = 0.1
    calc_res.tp_exit_slippage_usdt = 0.1
    calc_res.fees_total_usdt = 1.5
    calc_res.slippage_total_usdt = 0.3
    calc_res.maker_fee_rate = 0.0002
    calc_res.taker_fee_rate = 0.0004
    calc_res.leverage = 5
    calc_res.margin_mode = "ISOLATED"
    calc_res.initial_margin_usdt = 530.0
    calc_res.estimated_liquidation = 2100.0

    dummy_order = MagicMock()
    dummy_order.id = "ord_fill_028"
    dummy_order.direction = "LONG"
    dummy_order.planned_entry = 2650.0
    dummy_order.stop_loss = 2645.0
    dummy_order.take_profit = 2660.0
    dummy_order.quantity = 1.0
    dummy_order.cost_snapshot = json.dumps(arm_plan)
    dummy_order.opened_at = 1700000010000

    fill_snap = build_fill_plan_snapshot(
        calc_result=calc_res,
        order=dummy_order,
        fill_price=2650.2,
        now_ms=1700000010000,
    )

    assert fill_snap["entry_session"] == "NY"
    assert fill_snap["analysis_session"] == "NY"
    assert fill_snap["session_policy_version"] == "2026.1"
    assert fill_snap["decision_as_of_ms"] == 1700000000000
    assert fill_snap["setup_instance_id"] == "inst_cg01_99"
    assert fill_snap["min_net_rr_at_arm"] == 1.5
    assert fill_snap["actual_entry"] == 2650.2
    assert fill_snap["net_rr"] == 2.1


# ==============================================================================
# SECTION 029, 084 - 088, 165: PROVENANCE CONFLICT REJECTED
# ==============================================================================
def test_029_165_provenance_conflict_rejected():
    """
    Section 029, 084 - 088, 165:
    When provenance issues contain hard conflicts (e.g. INSTANCE_MISMATCH, SNAPSHOT_ID_MISMATCH),
    assess_entry_compliance_for_review must fail closed with UNVERIFIABLE,
    record PROVENANCE_INTEGRITY in checks_failed, and reject verification.
    """
    provenance_issues = [
        "INSTANCE_MISMATCH: order setup_instance_id 'order_inst_1' does not match snapshot 'snap_inst_2'"
    ]
    has_conflict, conflicts = classify_review_provenance_issues(provenance_issues)
    assert has_conflict is True
    assert len(conflicts) == 1

    entry_snap = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "model_id": "CG01",
        "planned_entry": 2650.0,
        "planned_sl": 2645.0,
        "planned_tp": 2660.0,
        "decision_as_of_ms": 1700000000000,
        "evidence": [
            {"id": "e1", "kind": "POI", "known_at_ms": 1699999990000, "occurred_at_ms": 1699999990000},
            {"id": "e2", "kind": "MSS", "known_at_ms": 1699999992000, "occurred_at_ms": 1699999992000},
            {"id": "e3", "kind": "RETEST", "known_at_ms": 1699999995000, "occurred_at_ms": 1699999995000},
        ],
        "trigger_chain": {"verified": True, "model_id": "CG01", "node_ids": ["e1", "e2", "e3"]},
    }

    comp = assess_entry_compliance_for_review(
        entry_snapshot=entry_snap,
        provenance_issues=provenance_issues,
    )
    assert comp.status == "UNVERIFIABLE"
    assert "PROVENANCE_INTEGRITY" in comp.checks_failed
    assert "RESOLVED_PROVENANCE" in comp.missing_evidence

    # Classify post trade lesson must NOT be GREEN
    rev = PostTradeReview(
        order_id="ord_conf_01",
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
    )
    cls_res = classify_post_trade_lesson(rev, entry_snapshot=entry_snap, compliance_result=comp)
    assert cls_res.suggested_color != "GREEN"
    assert cls_res.suggested_color in ("YELLOW", "RED")


# ==============================================================================
# SECTION 030, 094, 161: MISSING PLANNED_TP NOT VERIFIED GEOMETRY
# ==============================================================================
def test_030_161_missing_planned_tp_not_verified_geometry():
    """
    Section 030, 094, 161:
    A complete PRICE_GEOMETRY requires direction, entry, SL, AND TP!
    If planned_tp is missing, PRICE_GEOMETRY is NOT passed, missing_evidence includes PLANNED_TP,
    status is UNVERIFIABLE, and a winning trade cannot be GREEN.
    """
    entry_snap = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "model_id": "CG01",
        "planned_entry": 2650.0,
        "planned_sl": 2645.0,
        "planned_tp": None,  # MISSING TP!
        "decision_as_of_ms": 1700000000000,
        "evidence": [
            {"id": "e1", "kind": "POI", "known_at_ms": 1699999990000, "occurred_at_ms": 1699999990000},
            {"id": "e2", "kind": "MSS", "known_at_ms": 1699999992000, "occurred_at_ms": 1699999992000},
            {"id": "e3", "kind": "RETEST", "known_at_ms": 1699999995000, "occurred_at_ms": 1699999995000},
        ],
        "trigger_chain": {"verified": True, "model_id": "CG01", "node_ids": ["e1", "e2", "e3"]},
    }

    comp = assess_entry_compliance_for_review(entry_snapshot=entry_snap)
    assert comp.status == "UNVERIFIABLE"
    assert "PRICE_GEOMETRY" not in comp.checks_passed
    assert "PLANNED_TP" in comp.missing_evidence

    rev = PostTradeReview(
        order_id="ord_no_tp",
        symbol="XAUUSDT",
        model_id="CG01",
        direction="LONG",
        planned_entry=2650.0,
        actual_entry=2650.0,
        planned_sl=2645.0,
        planned_tp=None,
        exit_price=2660.0,
        exit_cause="TP_HIT",
        gross_pnl=10.0,
        net_pnl=9.5,
        realized_rr=2.0,
        hold_time_ms=60000,
        entry_slippage_usd=0.0,
        loss_classification=None,
        outcome_status="WIN",
    )
    cls_res = classify_post_trade_lesson(rev, entry_snapshot=entry_snap, compliance_result=comp)
    assert cls_res.suggested_color != "GREEN"
    assert "PLANNED_TP" in cls_res.missing_evidence


# ==============================================================================
# SECTION 031, 101, 102, 162: MISSING DECISION CUTOFF NO SYNTHETIC TIMESTAMP
# ==============================================================================
def test_031_162_missing_decision_cutoff_no_synthetic_timestamp():
    """
    Section 031, 101, 102, 162:
    When decision cutoff is missing (no decision_as_of_ms, no armed_at, no as_of_ms),
    compliance evaluator must NOT invent a synthetic timestamp (no knw=1000).
    It must return UNVERIFIABLE with missing DECISION_CUTOFF and decision_time_ms=None.
    """
    entry_snap = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "model_id": "CG01",
        "planned_entry": 2650.0,
        "planned_sl": 2645.0,
        "planned_tp": 2660.0,
        "evidence": [
            {"id": "e1", "kind": "POI"},
            {"id": "e2", "kind": "MSS"},
            {"id": "e3", "kind": "RETEST"},
        ],
        "trigger_chain": {"verified": True, "model_id": "CG01", "node_ids": ["e1", "e2", "e3"]},
    }

    comp = assess_entry_compliance_for_review(order=None, entry_snapshot=entry_snap)
    assert comp.status == "UNVERIFIABLE"
    assert comp.decision_time_ms is None
    assert "DECISION_CUTOFF" in comp.missing_evidence
    assert "thời gian quyết định" in comp.reason.lower()


# ==============================================================================
# SECTION 106, 163, 164: MISSING NODE TIMESTAMP & FUTURE EVIDENCE REJECTED
# ==============================================================================
def test_163_164_missing_node_timestamp_and_future_timestamp_rejected():
    """
    Section 106, 163, 164:
    - Node with missing known_at_ms is rejected as unproven.
    - Node with known_at_ms > decision_cutoff is rejected as future evidence.
    In both cases, trigger chain cannot resolve required nodes and returns UNVERIFIABLE.
    """
    cutoff = 1700000000000

    # Case A: Missing timestamp on MSS node
    snap_missing = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "model_id": "CG01",
        "planned_entry": 2650.0,
        "planned_sl": 2645.0,
        "planned_tp": 2660.0,
        "decision_as_of_ms": cutoff,
        "evidence": [
            {"id": "e1", "kind": "POI", "known_at_ms": cutoff - 5000, "occurred_at_ms": cutoff - 5000},
            {"id": "e2", "kind": "MSS"},  # Missing timestamps!
            {"id": "e3", "kind": "RETEST", "known_at_ms": cutoff - 1000, "occurred_at_ms": cutoff - 1000},
        ],
        "trigger_chain": {"verified": True, "model_id": "CG01", "node_ids": ["e1", "e2", "e3"]},
    }
    comp_missing = assess_entry_compliance_for_review(entry_snapshot=snap_missing)
    assert comp_missing.status == "UNVERIFIABLE"
    assert any("MISSING_TIMESTAMP" in iss for iss in comp_missing.provenance_issues)

    # Case B: Future timestamp on RETEST node (known after cutoff)
    snap_future = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "model_id": "CG01",
        "planned_entry": 2650.0,
        "planned_sl": 2645.0,
        "planned_tp": 2660.0,
        "decision_as_of_ms": cutoff,
        "evidence": [
            {"id": "e1", "kind": "POI", "known_at_ms": cutoff - 5000, "occurred_at_ms": cutoff - 5000},
            {"id": "e2", "kind": "MSS", "known_at_ms": cutoff - 3000, "occurred_at_ms": cutoff - 3000},
            {"id": "e3", "kind": "RETEST", "known_at_ms": cutoff + 2000, "occurred_at_ms": cutoff + 1000},  # Future!
        ],
        "trigger_chain": {"verified": True, "model_id": "CG01", "node_ids": ["e1", "e2", "e3"]},
    }
    comp_future = assess_entry_compliance_for_review(entry_snapshot=snap_future)
    assert comp_future.status == "UNVERIFIABLE"
    assert any("FUTURE_TIMESTAMP" in iss for iss in comp_future.provenance_issues)


# ==============================================================================
# SECTION 079, 171, 172: REPAIR SESSION & WORKER IDEMPOTENCY
# ==============================================================================
def test_079_171_repair_session_and_worker_idempotency(isolated_db):
    """
    Section 079, 171, 172:
    - Worker repairs closed order missing lesson.
    - Repaired lesson maintains frozen entry_session ('NY') and facts origin is 'REPAIR'.
    - Repeated repair task execution is idempotent: no duplicate lessons.
    """
    now_ms = 1700000000000
    cost_data = {
        "entry_session": "NY",
        "session": "NY",
        "planned_entry": 2650.0,
        "stop_loss": 2645.0,
        "take_profit": 2660.0,
    }
    order = PaperOrder(
        id="ord_rep_079",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=2650.0,
        actual_entry=2650.1,
        stop_loss=2645.0,
        take_profit=2660.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=now_ms - 120000,
        opened_at=now_ms - 60000,
        closed_at=now_ms,
        exit_cause="TP_HIT",
        strategy_family="CG01",
        cost_snapshot=json.dumps(cost_data),
    )
    isolated_db.add(order)
    task = LessonRepairTask(
        id="task_rep_001",
        order_id="ord_rep_079",
        status="PENDING",
        created_at=now_ms,
        updated_at=now_ms,
    )
    isolated_db.add(task)
    isolated_db.commit()

    worker = LessonRepairWorker()
    res = worker.process_once(db=isolated_db, now_ms=now_ms)
    assert res.get("repaired_count") == 1

    lessons = isolated_db.query(Lesson).filter_by(related_trade_id="ord_rep_079").all()
    assert len(lessons) == 1
    rep_lesson = lessons[0]
    assert rep_lesson.session == "NY"
    facts = json.loads(rep_lesson.facts_snapshot)
    assert facts.get("origin") == "REPAIR"
    assert facts.get("execution_origin") == "REPAIR"

    # Second run: Idempotent, no new lessons created
    res_again = worker.process_once(db=isolated_db, now_ms=now_ms + 1000)
    assert res_again.get("repaired_count") == 0
    lessons_after = isolated_db.query(Lesson).filter_by(related_trade_id="ord_rep_079").all()
    assert len(lessons_after) == 1


# ==============================================================================
# SECTION 125 - 136, 149 - 152: ADAPTIVE SCORING & APPROVED LESSON EFFECT
# ==============================================================================
def test_adaptive_scoring_threshold_70_and_approved_lesson_effect(isolated_db):
    """
    Section 125 - 136, 149 - 152:
    - Base candidate score 75 passes quality arm threshold (70).
    - An APPROVED and ENABLED lesson rule with DEDUCT_SCORE = 10 for NY session reduces score to 65.
    - Candidate with effective score 65 is below threshold 70 and blocked.
    - Pending (unapproved) or disabled lesson rules have zero score adjustment.
    - Toggling the rule to disabled invalidates cache and restores score to 75.
    """
    now_ms = 1700000000000
    LessonRuleService.invalidate_cache()

    # Rule in DB: Approved and Enabled
    rule = models.Lesson(
        title="Deduct 10 points for high spread in NY",
        session="NY",
        reflection="High spread penalty in NY",
        action_rule="Deduct score when spread_bps > 1.5",
        category="PROCESS",
        status="APPROVED",
        is_approved=True,
        enabled=True,
        validation_status="VALID",
        effective_at=now_ms - 10000,
        effect="DEDUCT_SCORE",
        predicate=json.dumps({
            "score_delta": -10,
            "metric": "spread_bps",
            "operator": ">",
            "threshold": 1.5,
        }),
        scope=json.dumps({
            "symbol": "XAUUSDT",
            "strategy_family": "CG01",
            "session": "NY",
        }),
        created_at=now_ms - 20000,
    )
    isolated_db.add(rule)
    isolated_db.commit()

    context_matching = {
        "symbol": "XAUUSDT",
        "strategy_family": "CG01",
        "session": "NY",
        "spread_bps": 2.0,  # Matches > 1.5
    }

    # Evaluate adjustments with active approved rule
    adj = LessonRuleService.evaluate_lesson_score_adjustments(context_matching, db=isolated_db, now_ms=now_ms)
    assert adj["applied_delta"] == -10
    assert len(adj["matched_rules"]) == 1
    assert adj["matched_rules"][0]["rule_id"] == rule.id

    # Base score 75 + (-10) = 65 -> Fails threshold 70!
    base_score = 75
    effective_score = base_score + adj["applied_delta"]
    assert effective_score == 65
    arm_threshold = 70
    assert effective_score < arm_threshold  # Blocked!

    # Test pending rule (not approved): Zero impact
    rule.is_approved = False
    rule.status = "PENDING_REVIEW"
    isolated_db.commit()
    LessonRuleService.invalidate_cache()

    adj_pending = LessonRuleService.evaluate_lesson_score_adjustments(context_matching, db=isolated_db, now_ms=now_ms)
    assert adj_pending["applied_delta"] == 0
    assert len(adj_pending["matched_rules"]) == 0

    # Test disabled rule: Zero impact
    rule.is_approved = True
    rule.status = "APPROVED"
    rule.enabled = False
    isolated_db.commit()
    LessonRuleService.invalidate_cache()

    adj_disabled = LessonRuleService.evaluate_lesson_score_adjustments(context_matching, db=isolated_db, now_ms=now_ms)
    assert adj_disabled["applied_delta"] == 0
    assert len(adj_disabled["matched_rules"]) == 0

    # Test different session scope (LONDON): Rule for NY must NOT match LONDON
    context_london = {
        "symbol": "XAUUSDT",
        "strategy_family": "CG01",
        "session": "LONDON",
        "spread_bps": 2.0,
    }
    rule.enabled = True
    isolated_db.commit()
    LessonRuleService.invalidate_cache()

    adj_london = LessonRuleService.evaluate_lesson_score_adjustments(context_london, db=isolated_db, now_ms=now_ms)
    assert adj_london["applied_delta"] == 0
    assert len(adj_london["matched_rules"]) == 0


# ==============================================================================
# SECTION 143, 144, 156 - 160: FULL PIPELINE POSITIVE LONG & SHORT WITH SESSION
# ==============================================================================
def test_full_pipeline_long_and_short_geometry_and_compliance(isolated_db):
    """
    Section 143, 144, 156 - 160:
    Tests LONG and SHORT candidates through full lifecycle:
    - Geometry: LONG (SL < entry < TP), SHORT (TP < entry < SL).
    - Session preserved across execution and review.
    - Compliance verified when causal evidence is complete.
    """
    now_ms = 1700000000000

    # 1. LONG Case
    long_order = PaperOrder(
        id="ord_full_long",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=2650.0,
        actual_entry=2650.1,
        stop_loss=2645.0,
        take_profit=2660.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=now_ms - 120000,
        opened_at=now_ms - 60000,
        strategy_family="CG01",
        cost_snapshot=json.dumps({
            "entry_session": "NY",
            "session": "NY",
            "analysis_session": "NY",
            "decision_as_of_ms": now_ms - 70000,
            "planned_entry": 2650.0,
            "stop_loss": 2645.0,
            "take_profit": 2660.0,
        }),
    )
    isolated_db.add(long_order)
    isolated_db.commit()

    closed_long = TradeLifecycleService.execute_close(
        isolated_db,
        order_id="ord_full_long",
        exit_price=2660.0,
        exit_cause="TP_HIT",
        now_ms=now_ms,
    )
    isolated_db.commit()
    assert closed_long.state == "closed"
    assert closed_long.realized_pnl_net > 0

    rev_long = isolated_db.query(PostTradeReviewRecord).filter_by(order_id="ord_full_long").first()
    assert rev_long is not None
    assert rev_long.outcome_status == "WIN"

    lesson_long = isolated_db.query(Lesson).filter_by(related_trade_id="ord_full_long").first()
    assert lesson_long is not None
    assert lesson_long.session == "NY"
    assert lesson_long.is_approved is False  # Default pending!

    # 2. SHORT Case
    short_order = PaperOrder(
        id="ord_full_short",
        instrument="XAUUSDT",
        direction="SHORT",
        state="paper_open",
        planned_entry=2650.0,
        actual_entry=2649.9,
        stop_loss=2655.0,
        take_profit=2640.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=now_ms - 120000,
        opened_at=now_ms - 60000,
        strategy_family="CG01",
        cost_snapshot=json.dumps({
            "entry_session": "ASIA",
            "session": "ASIA",
            "analysis_session": "ASIA",
            "decision_as_of_ms": now_ms - 70000,
            "planned_entry": 2650.0,
            "stop_loss": 2655.0,
            "take_profit": 2640.0,
        }),
    )
    isolated_db.add(short_order)
    isolated_db.commit()

    closed_short = TradeLifecycleService.execute_close(
        isolated_db,
        order_id="ord_full_short",
        exit_price=2640.0,
        exit_cause="TP_HIT",
        now_ms=now_ms,
    )
    isolated_db.commit()
    assert closed_short.state == "closed"
    assert closed_short.realized_pnl_net > 0

    rev_short = isolated_db.query(PostTradeReviewRecord).filter_by(order_id="ord_full_short").first()
    assert rev_short is not None
    assert rev_short.outcome_status == "WIN"

    lesson_short = isolated_db.query(Lesson).filter_by(related_trade_id="ord_full_short").first()
    assert lesson_short is not None
    assert lesson_short.session == "ASIA"
    assert lesson_short.is_approved is False
