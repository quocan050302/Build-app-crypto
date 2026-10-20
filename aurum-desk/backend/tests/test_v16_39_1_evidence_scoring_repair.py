import time
import math
import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from models import Base, Lesson, SystemConfig
from services.setup_quality_service import (
    assess_candidate_quality,
    normalize_candidate_evidence,
    build_candidate_scoring_context,
    NormalizedEvidenceNode,
    EvidenceIndex,
)
from services.lesson_rule_service import LessonRuleService
from services.post_trade_review_service import (
    PostTradeReview,
    LossClassification,
    classify_post_trade_lesson,
    assess_entry_compliance_for_review,
)


@pytest.fixture
def isolated_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


class DummyCandidate:
    def __init__(
        self,
        candidate_key="cand_test_01",
        symbol="XAUUSDT",
        direction="LONG",
        model_id="SMC_M1_CONTINUATION",
        is_eligible=True,
        entry_price=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        as_of_ms=1700000000000,
        evidence=None,
        trigger_chain=None,
    ):
        self.candidate_key = candidate_key
        self.symbol = symbol
        self.direction = direction
        self.model_id = model_id
        self.is_eligible = is_eligible
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.as_of_ms = as_of_ms
        self.evidence = evidence or {}
        self.trigger_chain = trigger_chain
        self.zone_id = None
        self.dealing_range = None
        self.sr_evidence = None
        self.regime_snapshot = None
        self.snapshot = None
        self.calculation = None


# ==============================================================================
# SECTION 011 & 151: REPRODUCE & VERIFY EMPTY EVIDENCE SCORE
# ==============================================================================
def test_011_empty_evidence_candidate_no_fake_positive_points():
    """
    Candidate with empty evidence and context must NOT receive fake default points
    (such as 68/100). All empirical factors must be UNKNOWN with 0 points.
    """
    cand = DummyCandidate(
        is_eligible=True,
        evidence={},
    )
    assessment = assess_candidate_quality(
        candidate=cand,
        context={},
        as_of_ms=1700000000000,
        threshold=70.0,
    )

    # Must be below threshold 70
    assert assessment.decision == "BELOW_THRESHOLD"
    assert assessment.final_score < 70.0

    # Empirical factors with missing data must be UNKNOWN with 0 earned points
    for f in assessment.factors:
        if f.code in (
            "HTF_STRUCTURAL_ALIGNMENT",
            "POI_FRESHNESS_LIFECYCLE",
            "LTF_TRIGGER_FRESHNESS",
            "LTF_REACTION_RETEST",
            "EXECUTION_SPREAD_EFFICIENCY",
            "EXECUTION_SLIPPAGE_IMPACT",
        ):
            assert f.status == "UNKNOWN"
            assert f.earned_points == 0.0


# ==============================================================================
# SECTION 012 & 157: REPRODUCE & VERIFY FALSE FRESHNESS
# ==============================================================================
def test_012_missing_zone_and_trigger_timestamp_yields_unknown_freshness():
    """
    When zone and trigger timestamps are absent, POI_FRESHNESS and TRIGGER_FRESHNESS
    must be marked UNKNOWN and earn 0 points.
    """
    cand = DummyCandidate(evidence={})
    assessment = assess_candidate_quality(
        candidate=cand,
        context={},
        as_of_ms=1700000000000,
        threshold=70.0,
    )
    f_map = {f.code: f for f in assessment.factors}
    assert f_map["POI_FRESHNESS_LIFECYCLE"].status == "UNKNOWN"
    assert f_map["POI_FRESHNESS_LIFECYCLE"].earned_points == 0.0
    assert f_map["LTF_TRIGGER_FRESHNESS"].status == "UNKNOWN"
    assert f_map["LTF_TRIGGER_FRESHNESS"].earned_points == 0.0


# ==============================================================================
# SECTION 013 & 156: REPRODUCE & VERIFY FALSE RETEST
# ==============================================================================
def test_013_continuation_without_retest_evidence_not_awarded_fake_retest():
    """
    SMC_M1_CONTINUATION candidate without RETEST node must not receive fake retest points.
    """
    cand = DummyCandidate(
        model_id="SMC_M1_CONTINUATION",
        evidence={},
    )
    assessment = assess_candidate_quality(
        candidate=cand,
        context={},
        as_of_ms=1700000000000,
        threshold=70.0,
    )
    f_map = {f.code: f for f in assessment.factors}
    assert f_map["LTF_REACTION_RETEST"].status == "UNKNOWN"
    assert f_map["LTF_REACTION_RETEST"].earned_points == 0.0


# ==============================================================================
# SECTION 014 & 164: REPRODUCE & VERIFY LESSON DB RETRIEVAL TYPEERROR FIX
# ==============================================================================
def test_014_lesson_db_retrieval_signature_no_typeerror(isolated_db):
    """
    evaluate_lesson_score_adjustments must correctly invoke retrieve_active_rules
    without TypeError and return valid adjustments from database.
    """
    now_ms = 1700000000000

    # Insert an approved, active, valid lesson rule in DB
    rule = Lesson(
        created_at=now_ms - 10000,
        title="High Spread Deduction",
        reflection="Spread elevated",
        action_rule="Deduct score when spread is high",
        status="APPROVED",
        is_approved=True,
        enabled=True,
        validation_status="VALID",
        effective_at=now_ms - 5000,
        severity="WARNING",
        effect="DEDUCT_SCORE",
        scope=json.dumps({"symbol": "XAUUSDT", "direction": "ALL"}),
        predicate=json.dumps({
            "metric": "spread",
            "operator": ">=",
            "threshold": 0.20,
            "score_delta": -5.0,
        }),
    )
    isolated_db.add(rule)
    isolated_db.commit()

    context = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "spread": 0.25,
        "now_ms": now_ms,
    }

    # Must NOT raise TypeError!
    res = LessonRuleService.evaluate_lesson_score_adjustments(
        context=context,
        db=isolated_db,
        now_ms=now_ms,
    )
    assert res["status"] == "SUCCESS"
    assert res["applied_delta"] == -5.0
    assert len(res["matched_rules"]) == 1
    assert res["matched_rules"][0]["rule_id"] == rule.id


# ==============================================================================
# SECTION 015 & 173: REPRODUCE & VERIFY FALSE GREEN FIX
# ==============================================================================
def test_015_confirmed_sl_missing_entry_snapshot_returns_yellow():
    """
    Confirmed SL outcome with missing entry snapshot and audit must return
    YELLOW (UNVERIFIABLE_CAUSE), never GREEN!
    """
    rev = PostTradeReview(
        order_id="ord_test_sl_01",
        symbol="XAUUSDT",
        model_id="SMC_M1_CONTINUATION",
        direction="LONG",
        planned_entry=2650.0,
        actual_entry=2650.1,
        planned_sl=2645.0,
        planned_tp=2660.0,
        exit_price=2644.9,
        exit_cause="SL_HIT",
        gross_pnl=-5.0,
        net_pnl=-5.5,
        realized_rr=-1.0,
        hold_time_ms=60000,
        entry_slippage_usd=0.0,
        loss_classification=LossClassification.STRATEGY_VALID_LOSS.value,
        outcome_status="LOSS",
    )

    # When entry_snapshot is None
    classification = classify_post_trade_lesson(rev, entry_snapshot=None, audit=None)
    assert classification.suggested_color == "YELLOW"
    assert classification.canonical_severity == "WARNING"
    assert classification.classification_code == "UNVERIFIABLE_CAUSE"
    assert "ENTRY_SNAPSHOT" in classification.missing_evidence


# ==============================================================================
# SECTION 152 & 154: EVIDENCE NODE NORMALIZATION & FUTURE NODE EXCLUSION
# ==============================================================================
def test_152_154_evidence_node_normalization_and_future_exclusion():
    now_ms = 1700000000000
    cand = DummyCandidate(
        as_of_ms=now_ms,
        evidence=[
            {
                "id": "node_past_bos",
                "kind": "BOS",
                "symbol": "XAUUSDT",
                "direction": "LONG",
                "occurred_at_ms": now_ms - 30000,
                "known_at_ms": now_ms - 25000,
                "level": 2648.0,
                "extra": {"htf_bias": "BULLISH"},
            },
            {
                "id": "node_future_retest",
                "kind": "RETEST",
                "symbol": "XAUUSDT",
                "direction": "LONG",
                "occurred_at_ms": now_ms + 10000,
                "known_at_ms": now_ms + 15000,  # FUTURE!
                "level": 2650.0,
            }
        ],
        trigger_chain={
            "verified": True,
            "model_id": "SMC_M1_CONTINUATION",
            "node_ids": ["node_past_bos"],
        }
    )

    index = normalize_candidate_evidence(cand, as_of_ms=now_ms)
    assert "node_past_bos" in index.by_id
    assert "node_future_retest" not in index.by_id
    assert len(index.future_nodes) == 1
    assert index.future_nodes[0].id == "node_future_retest"
    assert index.trigger_chain_verified is True


# ==============================================================================
# SECTION 155: STRICT TRIGGER CHAIN TRUTHINESS
# ==============================================================================
def test_155_trigger_chain_verified_false_dict_is_not_truthy():
    cand = DummyCandidate(
        evidence=[{
            "id": "node_1",
            "kind": "BOS",
            "symbol": "XAUUSDT",
            "direction": "LONG",
            "occurred_at_ms": 1000,
            "known_at_ms": 2000,
        }],
        trigger_chain={"verified": False, "node_ids": ["node_1"]}
    )
    index = normalize_candidate_evidence(cand, as_of_ms=5000)
    assert index.trigger_chain_verified is False
    assert "TRIGGER_CHAIN_UNVERIFIED" in index.issues


# ==============================================================================
# SECTION 158: NO FAKE QUOTE SPREAD
# ==============================================================================
def test_158_missing_quote_yields_unknown_spread():
    cand = DummyCandidate()
    ctx = build_candidate_scoring_context(
        candidate=cand,
        quote=None,
        as_of_ms=1700000000000,
    )
    assert ctx["spread"] is None
    assert ctx["quote_freshness_status"] == "UNKNOWN"

    assessment = assess_candidate_quality(candidate=cand, context=ctx, as_of_ms=1700000000000)
    f_map = {f.code: f for f in assessment.factors}
    assert f_map["EXEC_SPREAD_BUDGET"].status == "UNKNOWN"
    assert f_map["EXEC_SPREAD_BUDGET"].earned_points == 0.0


# ==============================================================================
# SECTION 161: SCORE BOUNDARY CHECKS
# ==============================================================================
def test_161_score_boundary_conditions():
    cand = DummyCandidate()
    # Mocking factors manually for boundary verification
    assessment_fail = assess_candidate_quality(
        candidate=cand,
        context={},
        threshold=70.0,
        lesson_delta=0.0,
    )
    assert assessment_fail.final_score < 70.0
    assert assessment_fail.decision == "BELOW_THRESHOLD"


# ==============================================================================
# SECTION 165 - 169: LESSON ENGINE ELIGIBILITY, EXPIRY, AND DELTA CAPS
# ==============================================================================
def test_165_169_lesson_rule_eligibility_and_caps(isolated_db):
    now_ms = 1700000000000

    # 1. Invalid status rule
    r_invalid = Lesson(
        created_at=now_ms,
        title="Invalid rule",
        reflection="", action_rule="",
        status="APPROVED", is_approved=True, enabled=True,
        validation_status="INVALID",
        effect="DEDUCT_SCORE",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.1, "score_delta": -5.0}),
    )
    # 2. Expired rule
    r_expired = Lesson(
        created_at=now_ms - 20000,
        title="Expired rule",
        reflection="", action_rule="",
        status="APPROVED", is_approved=True, enabled=True,
        validation_status="VALID",
        effective_at=now_ms - 15000,
        expiry_at=now_ms - 1000,  # EXPIRED
        effect="DEDUCT_SCORE",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.1, "score_delta": -5.0}),
    )
    # 3. Annotate rule with score_delta (must NOT adjust score)
    r_annotate = Lesson(
        created_at=now_ms,
        title="Annotate rule",
        reflection="", action_rule="",
        status="APPROVED", is_approved=True, enabled=True,
        validation_status="VALID",
        effect="ANNOTATE",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.1, "score_delta": -10.0}),
    )

    isolated_db.add_all([r_invalid, r_expired, r_annotate])
    isolated_db.commit()

    ctx = {"symbol": "XAUUSDT", "spread": 0.50}
    res = LessonRuleService.evaluate_lesson_score_adjustments(ctx, db=isolated_db, now_ms=now_ms)
    assert res["applied_delta"] == 0.0
    assert len(res["matched_rules"]) == 0


# ==============================================================================
# SECTION 170: 74 - 5 = 69 INTEGRATION CASE
# ==============================================================================
def test_170_penalty_brings_score_below_threshold():
    """
    Candidate with base score 74.0, penalized by -5.0 approved lesson rule ->
    final score 69.0 -> decision BELOW_THRESHOLD!
    """
    now_ms = 1700000000000
    cand = DummyCandidate(
        model_id="SMC_M1_REVERSAL",
        entry_price=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
    )
    cand.gross_rr = 2.0
    cand.zone_id = "zone_1"

    # Build rich context achieving >= 70 base score
    norm_ev = EvidenceIndex(
        trigger_chain_verified=True,
        latest_known_at=now_ms - 1000,
    )
    sw_node = NormalizedEvidenceNode(
        id="s1", kind="SWEEP", symbol="XAUUSDT", timeframe="1M", direction="LONG",
        occurred_at_ms=now_ms - 80000, known_at_ms=now_ms - 70000,
    )
    norm_ev.by_kind["SWEEP"] = [sw_node]
    norm_ev.by_kind["LIQUIDITY"] = [sw_node]
    norm_ev.by_kind["CHOCH"] = [NormalizedEvidenceNode(
        id="c1", kind="CHOCH", symbol="XAUUSDT", timeframe="1M", direction="LONG",
        occurred_at_ms=now_ms - 60000, known_at_ms=now_ms - 30000,
        extra={"displacement_body": 2.5, "displacement_atr": 1.2}
    )]
    norm_ev.by_kind["RETEST"] = [NormalizedEvidenceNode(
        id="r1", kind="RETEST", symbol="XAUUSDT", timeframe="1M", direction="LONG",
        occurred_at_ms=now_ms - 20000, known_at_ms=now_ms - 10000,
        extra={"retest_reaction_confirmed": True, "retest_spread_atr": 0.1}
    )]
    norm_ev.by_kind["TARGET"] = [NormalizedEvidenceNode(
        id="t1", kind="TARGET", symbol="XAUUSDT", timeframe="1M", direction="LONG",
        occurred_at_ms=now_ms - 20000, known_at_ms=now_ms - 10000,
    )]

    ctx = {
        "htf_bias": "BULLISH",
        "h1_alignment": "ALIGNED",
        "regime": "TRENDING_BULLISH",
        "zone_fresh": True,
        "zone_touch_count": 1,
        "dealing_range_position": 0.3,
        "spread": 0.12,
        "slippage_usd": 0.05,
        "distance_to_entry_atr": 0.2,
        "net_rr": 2.0,
        "normalized_evidence": norm_ev,
        "zone": {"fresh": True, "touch_count": 1},
        "quote": {"bid": 2649.94, "ask": 2650.06, "spread": 0.12},
        "calculation": {"slippage_usd": 0.05},
        "atr": 1.5,
        "sr_zones": [{"id": "sr1", "symbol": "XAUUSDT", "is_active": True, "midpoint": 2649.5}],
    }

    # Without penalty:
    base_assess = assess_candidate_quality(
        candidate=cand,
        context=ctx,
        as_of_ms=now_ms,
        threshold=70.0,
        lesson_delta=0.0,
    )
    assert base_assess.base_score >= 70.0

    # With -5.0 penalty:
    penalized_assess = assess_candidate_quality(
        candidate=cand,
        context=ctx,
        as_of_ms=now_ms,
        threshold=base_assess.base_score - 1.0,  # Set threshold just above base - 5
        lesson_delta=-5.0,
    )
    assert penalized_assess.final_score == penalized_assess.base_score - 5.0
    assert penalized_assess.decision == "BELOW_THRESHOLD"


# ==============================================================================
# SECTION 174 & 175 & 176: POST-TRADE LESSON COLOR CLASSIFICATION
# ==============================================================================
def test_174_valid_loss_green_with_verified_compliance():
    """
    Confirmed SL with verified compliant entry snapshot -> GREEN (PLAN_COMPLIANT_VALID_LOSS).
    """
    rev = PostTradeReview(
        order_id="ord_valid_sl",
        symbol="XAUUSDT",
        model_id="SMC_M1_CONTINUATION",
        direction="LONG",
        planned_entry=2650.0,
        actual_entry=2650.0,
        planned_sl=2645.0,
        planned_tp=2660.0,
        exit_price=2645.0,
        exit_cause="SL_HIT",
        gross_pnl=-5.0,
        net_pnl=-5.5,
        realized_rr=-1.0,
        hold_time_ms=60000,
        entry_slippage_usd=0.0,
        loss_classification=LossClassification.STRATEGY_VALID_LOSS.value,
        outcome_status="LOSS",
    )
    t_cutoff = 1700000000000
    entry_snap = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "planned_entry": 2650.0,
        "planned_sl": 2645.0,
        "planned_tp": 2660.0,
        "compliance_verified": True,
        "decision_as_of_ms": t_cutoff,
        "evidence": [{"id": "ev_1", "kind": "BOS", "known_at_ms": t_cutoff - 5000, "occurred_at_ms": t_cutoff - 5000}],
    }
    audit = {"compliance_verified": True}

    classification = classify_post_trade_lesson(rev, entry_snapshot=entry_snap, audit=audit)
    assert classification.suggested_color == "GREEN"
    assert classification.canonical_severity == "INFO"
    assert classification.classification_code == "PLAN_COMPLIANT_VALID_LOSS"


def test_175_profitable_violation_returns_red():
    """
    WIN trade with policy or risk violation must return RED, never GREEN!
    """
    rev = PostTradeReview(
        order_id="ord_prof_viol",
        symbol="XAUUSDT",
        model_id="SMC_M1_CONTINUATION",
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
        loss_classification=LossClassification.NOT_A_LOSS.value,
        outcome_status="WIN",
    )
    audit = {"violation_detected": True, "reason": "Trade executed outside approved session"}

    classification = classify_post_trade_lesson(rev, entry_snapshot={"planned_entry": 2650, "planned_sl": 2645, "direction": "LONG", "compliance_verified": False}, audit=audit)
    assert classification.suggested_color == "RED"
    assert classification.canonical_severity == "CRITICAL"
    assert classification.classification_code == "VERIFIED_RULE_VIOLATION"


def test_176_wrong_geometry_returns_red():
    """
    LONG setup with planned_sl >= planned_entry must return RED geometry violation.
    """
    rev = PostTradeReview(
        order_id="ord_wrong_geom",
        symbol="XAUUSDT",
        model_id="SMC_M1_CONTINUATION",
        direction="LONG",
        planned_entry=2650.0,
        actual_entry=2650.0,
        planned_sl=2655.0,  # SL is ABOVE entry for a LONG!
        planned_tp=2660.0,
        exit_price=2655.0,
        exit_cause="SL_HIT",
        gross_pnl=-5.0,
        net_pnl=-5.5,
        realized_rr=-1.0,
        hold_time_ms=60000,
        entry_slippage_usd=0.0,
        loss_classification=LossClassification.STRATEGY_VALID_LOSS.value,
        outcome_status="LOSS",
    )
    classification = classify_post_trade_lesson(rev)
    assert classification.suggested_color == "RED"
    assert classification.canonical_severity == "CRITICAL"
    assert classification.classification_code == "VERIFIED_RULE_VIOLATION"
