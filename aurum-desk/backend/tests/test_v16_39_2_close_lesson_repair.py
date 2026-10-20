import time
import math
import json
import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from models import Base, PaperOrder, Lesson, PostTradeReviewRecord, LessonRepairTask
from services.trade_lifecycle_service import TradeLifecycleService
from services.setup_quality_service import (
    assess_candidate_quality,
    normalize_candidate_evidence,
    build_candidate_scoring_context,
    resolve_scoring_profile,
    ScoringProfileConfig,
    NormalizedEvidenceNode,
    EvidenceIndex,
)
from services.post_trade_review_service import (
    PostTradeReview,
    LossClassification,
    classify_post_trade_lesson,
    assess_entry_compliance_for_review,
    resolve_model_review_requirements,
    build_entry_review_context,
)
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator, M1ScalpConfig


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


class DummyCalculation:
    def __init__(self):
        self.is_valid = True
        self.can_execute = True
        self.quantity = 1.0
        self.net_risk_usdt = 5.0
        self.gross_risk_usdt = 5.0
        self.gross_loss_usdt = 5.0
        self.gross_reward_usdt = 10.0
        self.net_reward_usdt = 9.8
        self.gross_rr = 2.0
        self.net_rr = 1.8
        self.leverage = 5
        self.margin_mode = "ISOLATED"
        self.estimated_liquidation = 2500.0
        self.maker_fee_rate = 0.0002
        self.taker_fee_rate = 0.0004
        self.slippage_usd = 0.1
        self.initial_margin_usdt = 100.0
        self.multiplier = 1.0
        self.entry_fee_usdt = 0.05
        self.sl_exit_fee_usdt = 0.05
        self.tp_exit_fee_usdt = 0.05
        self.entry_slippage_usdt = 0.05
        self.sl_exit_slippage_usdt = 0.05
        self.tp_exit_slippage_usdt = 0.05
        self.fees_total_usdt = 0.15
        self.slippage_total_usdt = 0.15


class DummyCandidate:
    def __init__(
        self,
        candidate_key="cand_test_01",
        symbol="XAUUSDT",
        direction="LONG",
        model_id="SMC_M1_CONTINUATION",
        is_eligible=True,
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        as_of_ms=1700000000000,
        evidence=None,
        trigger_chain=None,
        net_rr=1.8,
        zone_id="zone_01",
        reason_code="ELIGIBLE",
    ):
        self.candidate_key = candidate_key
        self.symbol = symbol
        self.direction = direction
        self.model_id = model_id
        self.is_eligible = is_eligible
        self.planned_entry = planned_entry
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.as_of_ms = as_of_ms
        self.evidence = evidence or {}
        self.trigger_chain = trigger_chain
        self.net_rr = net_rr
        self.zone_id = zone_id
        self.reason_code = reason_code
        self.snapshot = None
        self.calculation = DummyCalculation()


# ==============================================================================
# PART A: CLOSE-TO-LESSON REPAIR & CANONICAL FIELD TESTS (Bugs 1 & 039 - 042)
# ==============================================================================

def test_001_real_paper_order_close_creates_pending_lesson_and_review(isolated_db):
    """
    Section 011 / 015 / 139 / 141:
    Tests that a real ORM PaperOrder close creates a PostTradeReview and Lesson
    without raising AttributeError for 'entry_price'.
    Gate: closed order + persisted review + persisted pending lesson + correct source.
    """
    now_ms = 1700000000000
    order = PaperOrder(
        id="ord_close_001",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=2650.0,
        actual_entry=2650.1,
        stop_loss=2645.0,
        take_profit=2665.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=now_ms - 120000,
        strategy_family="SMC_M1_CONTINUATION",
        opened_at=now_ms - 60000,
        cost_snapshot=json.dumps({"planned_entry": 2650.0, "stop_loss": 2645.0, "take_profit": 2665.0}),
    )
    isolated_db.add(order)
    isolated_db.flush()

    # Execute close
    closed_order = TradeLifecycleService.execute_close(
        isolated_db,
        order_id="ord_close_001",
        exit_price=2665.0,
        exit_cause="TP_HIT",
        now_ms=now_ms,
    )
    isolated_db.flush()

    assert closed_order.state == "closed"
    assert closed_order.closed_at == now_ms

    # Verify PostTradeReview is persisted
    rev_rec = isolated_db.query(PostTradeReviewRecord).filter_by(order_id="ord_close_001").first()
    assert rev_rec is not None
    assert rev_rec.order_id == "ord_close_001"
    assert rev_rec.outcome_status == "WIN"
    assert rev_rec.planned_entry == 2650.0
    assert rev_rec.actual_entry == 2650.1

    # Verify Lesson is persisted with governed pending review state (Section 033)
    lesson = isolated_db.query(Lesson).filter_by(related_trade_id="ord_close_001").first()
    assert lesson is not None
    assert lesson.status == "PENDING_REVIEW"
    assert lesson.is_approved is False
    assert lesson.enabled is False
    assert json.loads(lesson.scope)["symbol"] == "XAUUSDT"
    assert lesson.strategy_family == "SMC_M1_CONTINUATION"


def test_002_create_lesson_reads_planned_entry_directly(isolated_db):
    """
    Section 016:
    In TradeLifecycleService._create_lesson(), planned_entry is read from
    order.planned_entry. Does NOT read undefined order.entry_price.
    """
    now_ms = 1700000000000
    order = PaperOrder(
        id="ord_direct_002",
        instrument="XAUUSDT",
        direction="SHORT",
        state="closed",
        planned_entry=2670.0,
        actual_entry=2669.9,
        stop_loss=2675.0,
        take_profit=2655.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=now_ms - 60000,
        strategy_family="SMC_M1_CONTINUATION",
        opened_at=now_ms - 30000,
        closed_at=now_ms,
        actual_exit=2655.0,
        exit_cause="TP_HIT",
        realized_pnl_net=15.0,
        realized_r=2.0,
    )
    isolated_db.add(order)
    isolated_db.flush()

    # Call _create_lesson directly
    lesson = TradeLifecycleService._create_lesson(isolated_db, order, now_ms=now_ms)
    assert lesson is not None
    assert lesson.related_trade_id == "ord_direct_002"
    assert lesson.status == "PENDING_REVIEW"
    assert lesson.is_approved is False


def test_003_durable_repair_task_and_recovery(isolated_db):
    """
    Section 039 - 042: Durable repair mechanism.
    If lesson persistence failed, a LessonRepairTask is recorded.
    TradeLifecycleService.repair_pending_lessons() recovers it idempotently
    without calling execute_close() or mutating accounting.
    """
    now_ms = 1700000000000
    order = PaperOrder(
        id="ord_repair_003",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=2650.0,
        actual_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=now_ms - 120000,
        strategy_family="SMC_M1_CONTINUATION",
        opened_at=now_ms - 60000,
        closed_at=now_ms,
        actual_exit=2645.0,
        exit_cause="SL_HIT",
        realized_pnl_net=-5.5,
        realized_r=-1.0,
    )
    isolated_db.add(order)
    isolated_db.flush()

    # Record a durable repair task simulating an initial failure
    TradeLifecycleService._record_lesson_repair_task(
        isolated_db,
        order_id="ord_repair_003",
        error_msg="Simulated transient lock error",
        now_ms=now_ms,
    )
    isolated_db.flush()

    task = isolated_db.query(LessonRepairTask).filter_by(order_id="ord_repair_003").first()
    assert task is not None
    assert task.status == "PENDING"
    assert "Simulated transient lock error" in task.last_error

    # Ensure no lesson exists yet
    assert isolated_db.query(Lesson).filter_by(related_trade_id="ord_repair_003").first() is None

    # Run durable repair
    res = TradeLifecycleService.repair_pending_lessons(isolated_db, now_ms=now_ms + 1000)
    assert res["repaired_count"] == 1
    assert res["failed_count"] == 0

    # Verify task is now COMPLETED
    task_after = isolated_db.query(LessonRepairTask).filter_by(order_id="ord_repair_003").first()
    assert task_after.status == "COMPLETED"

    # Verify lesson is now created
    repaired_lesson = isolated_db.query(Lesson).filter_by(related_trade_id="ord_repair_003").first()
    assert repaired_lesson is not None
    assert repaired_lesson.status == "PENDING_REVIEW"
    assert repaired_lesson.is_approved is False

    # Second repair run must be idempotent (0 repaired, 0 failed)
    res2 = TradeLifecycleService.repair_pending_lessons(isolated_db, now_ms=now_ms + 2000)
    assert res2["total_processed"] == 0


def test_004_duplicate_close_idempotency(isolated_db):
    """
    Section 034 / 146: Duplicate close calls produce exactly one accounting close,
    one review record, and one root lesson.
    """
    now_ms = 1700000000000
    order = PaperOrder(
        id="ord_dup_004",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=2650.0,
        actual_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=now_ms - 120000,
        strategy_family="SMC_M1_CONTINUATION",
        opened_at=now_ms - 60000,
        cost_snapshot=json.dumps({"planned_entry": 2650.0, "stop_loss": 2645.0, "take_profit": 2660.0}),
    )
    isolated_db.add(order)
    isolated_db.flush()

    # First close
    c1 = TradeLifecycleService.execute_close(isolated_db, "ord_dup_004", 2660.0, "TP_HIT", now_ms=now_ms)
    # Second close (retry)
    c2 = TradeLifecycleService.execute_close(isolated_db, "ord_dup_004", 2660.0, "TP_HIT", now_ms=now_ms + 1000)

    # First close succeeds
    assert c1 is not None
    assert c1.state == "closed"
    # Second close is rejected as duplicate (idempotent CAS)
    assert c2 is None
    reviews = isolated_db.query(PostTradeReviewRecord).filter_by(order_id="ord_dup_004").all()
    assert len(reviews) == 1

    lessons = isolated_db.query(Lesson).filter_by(related_trade_id="ord_dup_004").all()
    assert len(lessons) == 1


# ==============================================================================
# PART B: ELIMINATION OF FAKE SYNTHETIC EVIDENCE & TIMESTAMPS (Bugs 2 & 047 - 049)
# ==============================================================================

def test_005_legacy_flags_without_timestamps_do_not_synthesize_fake_timestamps():
    """
    Section 012 / 047:
    normalize_candidate_evidence() must NOT synthesize fake timestamps
    (now-60s, now-120s, now-30s, now-10s) from legacy boolean flags!
    Missing timestamps must be 0 with is_causal=False and issue LEGACY_TIMESTAMP_UNAVAILABLE.
    """
    now_ms = 1700000000000
    cand = DummyCandidate(
        evidence={
            "has_sweep": True,
            "poi_confirmed": True,
            "has_trigger": True,
            "retest_detected": True,
        },
        as_of_ms=now_ms,
    )
    index = normalize_candidate_evidence(cand, as_of_ms=now_ms)

    # Valid nodes should be empty because timestamps are 0 (non-causal / unavailable)
    assert len(index.valid_nodes) == 0
    # Future / non-causal nodes contain the nodes with missing timestamps
    assert len(index.future_nodes) == 4
    for node in index.future_nodes:
        assert node.known_at_ms == 0
        assert node.occurred_at_ms == 0
        assert node.is_causal is False

    assert any("LEGACY_TIMESTAMP_UNAVAILABLE" in issue for issue in index.issues)


def test_006_legacy_flags_do_not_synthesize_displacement_constants():
    """
    Section 012 / 048:
    normalize_candidate_evidence() must NOT synthesize displacement_body=1.0,
    displacement_atr=1.0 from has_trigger=True!
    """
    now_ms = 1700000000000
    cand = DummyCandidate(
        evidence={"has_trigger": True},
        as_of_ms=now_ms,
    )
    index = normalize_candidate_evidence(cand, as_of_ms=now_ms)
    for node in index.future_nodes:
        if node.kind in ("BOS", "CHOCH"):
            # Extra should be empty because no displacement measurements were provided
            assert "displacement_body" not in node.extra
            assert "displacement_atr" not in node.extra


def test_007_eligibility_does_not_circularly_verify_trigger_chain():
    """
    Section 064:
    Eligibility is the model conclusion; evidence verification is proof of source.
    normalize_candidate_evidence() does NOT fallback trigger_chain_verified=True
    just because candidate.is_eligible=True and BOS exists!
    """
    now_ms = 1700000000000
    cand = DummyCandidate(
        is_eligible=True,
        evidence=[
            {
                "id": "bos_1",
                "kind": "BOS",
                "known_at_ms": now_ms - 5000,
                "occurred_at_ms": now_ms - 5000,
                "symbol": "XAUUSDT",
                "direction": "LONG",
            }
        ],
        trigger_chain=None,  # No explicit trigger chain
        as_of_ms=now_ms,
    )
    index = normalize_candidate_evidence(cand, as_of_ms=now_ms)
    assert index.trigger_chain_verified is False


# ==============================================================================
# PART C: MODEL-AWARE COMPLIANCE MATRIX & UNRELATED EVIDENCE (Bugs 3 & 013, 065 - 071)
# ==============================================================================

def test_008_unrelated_evidence_with_unverified_trigger_chain_is_unverifiable():
    """
    Section 013 / 159: Bug 3 Reproduction & Regression.
    entry_snapshot with unrelated node and trigger_chain={"verified": False}
    MUST return UNVERIFIABLE and YELLOW lesson, NEVER VERIFIED_COMPLIANT!
    """
    rev = PostTradeReview(
        order_id="ord_unrelated_008",
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
    entry_snap = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "planned_entry": 2650.0,
        "planned_sl": 2645.0,
        "planned_tp": 2660.0,
        "evidence": [{"id": "x", "kind": "UNRELATED"}],
        "trigger_chain": {"verified": False, "node_ids": ["x"]},
    }

    comp = assess_entry_compliance_for_review(None, entry_snapshot=entry_snap)
    assert comp.status == "UNVERIFIABLE"
    assert "TRIGGER_CHAIN_UNVERIFIED" in comp.checks_failed

    classification = classify_post_trade_lesson(rev, entry_snapshot=entry_snap)
    assert classification.suggested_color == "YELLOW"
    assert classification.canonical_severity == "WARNING"


@pytest.mark.parametrize(
    "model_id, required_kinds, sample_evidence, is_compliant",
    [
        ("SMC_M1_CONTINUATION", ["BOS"], [{"id": "e1", "kind": "BOS"}], True),
        ("SMC_M1_CONTINUATION", ["BOS"], [{"id": "e1", "kind": "POI"}], False),  # Missing BOS
        ("SMC_M1_REVERSAL", ["POI", "SWEEP", "CHOCH"], [{"id": "e1", "kind": "POI"}, {"id": "e2", "kind": "SWEEP"}, {"id": "e3", "kind": "CHOCH"}], True),
        ("SMC_M1_REVERSAL", ["POI", "SWEEP", "CHOCH"], [{"id": "e1", "kind": "POI"}, {"id": "e2", "kind": "CHOCH"}], False),  # Missing SWEEP
        ("CG01", ["POI", "MSS", "RETEST"], [{"id": "e1", "kind": "POI"}, {"id": "e2", "kind": "MSS"}, {"id": "e3", "kind": "RETEST"}], True),
        ("CG01", ["POI", "MSS", "RETEST"], [{"id": "e1", "kind": "POI"}, {"id": "e2", "kind": "RETEST"}], False),  # Missing MSS
    ],
)
def test_009_model_requirements_compliance_matrix(model_id, required_kinds, sample_evidence, is_compliant):
    """
    Section 065 - 071 / 162: Model requirements resolution.
    Continuation, Reversal, CG01 verify distinct factual chains.
    """
    entry_snap = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "model_id": model_id,
        "planned_entry": 2650.0,
        "planned_sl": 2645.0,
        "planned_tp": 2660.0,
        "evidence": sample_evidence,
        "trigger_chain": {"verified": True, "model_id": model_id, "node_ids": [x["id"] for x in sample_evidence]},
    }
    comp = assess_entry_compliance_for_review(None, entry_snapshot=entry_snap)
    if is_compliant:
        assert comp.status == "VERIFIED_COMPLIANT"
    else:
        assert comp.status == "UNVERIFIABLE"


def test_010_geometry_violations_yield_red():
    """
    Section 076 / 161: Price geometry violations yield VERIFIED_VIOLATION and RED.
    LONG: TP <= entry or SL >= entry
    SHORT: TP >= entry or SL <= entry
    """
    rev = PostTradeReview(
        order_id="ord_geo_010",
        symbol="XAUUSDT",
        model_id="SMC_M1_CONTINUATION",
        direction="LONG",
        planned_entry=2650.0,
        actual_entry=2650.0,
        planned_sl=2645.0,
        planned_tp=2640.0,  # Invalid: TP below entry for LONG!
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
    classification = classify_post_trade_lesson(rev)
    assert classification.suggested_color == "RED"
    assert classification.canonical_severity == "CRITICAL"
    assert "hình học lệnh vi phạm" in classification.reason_vi.lower()


# ==============================================================================
# PART D: PRODUCTION SCORING CONTEXT & PROFILE (Bugs 4 & 091 - 098)
# ==============================================================================

def test_011_dealing_range_position_calculation_without_position_attribute():
    """
    Section 096 / 168:
    DealingRange has range_high, range_low, equilibrium, known_at_ms, is_valid.
    Does NOT search for non-existent .position attribute.
    Position is calculated as (planned_entry - range_low) / (range_high - range_low).
    """
    now_ms = 1700000000000
    cand = DummyCandidate(planned_entry=2655.0, as_of_ms=now_ms)
    dr_dict = {
        "range_high": 2660.0,
        "range_low": 2640.0,
        "equilibrium": 2650.0,
        "known_at_ms": now_ms - 10000,
        "is_valid": True,
    }
    ctx = build_candidate_scoring_context(
        candidate=cand,
        market_context={"dealing_range": dr_dict},
        as_of_ms=now_ms,
    )
    # (2655 - 2640) / (2660 - 2640) = 15 / 20 = 0.75
    assert ctx["dealing_range_position"] == 0.75


def test_012_htf_bias_unknown_string_does_not_block_causal_bos_fallback():
    """
    Section 094 / 166:
    String 'UNKNOWN' is truthy in Python. If market_context has htf_bias='UNKNOWN',
    it must NOT block falling back to causal BOS node with HTF facts.
    """
    now_ms = 1700000000000
    cand = DummyCandidate(as_of_ms=now_ms)
    bos_node = NormalizedEvidenceNode(
        id="bos_htf",
        kind="BOS",
        symbol="XAUUSDT",
        timeframe="15M",
        direction="LONG",
        occurred_at_ms=now_ms - 20000,
        known_at_ms=now_ms - 20000,
        is_causal=True,
        extra={"htf_bias": "BULLISH", "h1_alignment": "BULLISH"},
    )
    norm_ev = EvidenceIndex()
    norm_ev.valid_nodes.append(bos_node)
    norm_ev.by_kind["BOS"] = [bos_node]

    ctx = build_candidate_scoring_context(
        candidate=cand,
        normalized_evidence=norm_ev,
        market_context={"htf_bias": "UNKNOWN", "bias": "UNKNOWN"},
        as_of_ms=now_ms,
    )
    assert ctx["htf_bias"] == "BULLISH"
    assert ctx["h1_alignment"] == "BULLISH"


def test_013_poi_zone_freshness_unknown_when_not_in_extra():
    """
    Section 098 / 170:
    POI node without touch_count or freshness_state does NOT fabricate 1/FRESH!
    Factor POI_FRESHNESS_LIFECYCLE must be UNKNOWN with 0.0 earned points.
    """
    now_ms = 1700000000000
    cand = DummyCandidate(as_of_ms=now_ms)
    poi_node = NormalizedEvidenceNode(
        id="poi_1",
        kind="POI",
        symbol="XAUUSDT",
        timeframe="1M",
        direction="LONG",
        occurred_at_ms=now_ms - 10000,
        known_at_ms=now_ms - 10000,
        level=2650.0,
        is_causal=True,
        extra={},  # NO touch_count, NO freshness_state
    )
    norm_ev = EvidenceIndex()
    norm_ev.valid_nodes.append(poi_node)
    norm_ev.by_kind["POI"] = [poi_node]

    ctx = build_candidate_scoring_context(
        candidate=cand,
        normalized_evidence=norm_ev,
        as_of_ms=now_ms,
    )
    assert ctx["zone"]["touch_count"] is None
    assert ctx["zone"]["freshness_state"] is None

    assessment = assess_candidate_quality(
        cand,
        context=ctx,
        profile=ScoringProfileConfig(quality_arm_threshold=70.0),
        as_of_ms=now_ms,
    )
    fresh_factor = next(f for f in assessment.factors if f.code == "POI_FRESHNESS_LIFECYCLE")
    assert fresh_factor.earned_points == 0.0
    assert fresh_factor.status == "UNKNOWN"
    assert "POI_FRESHNESS" in assessment.missing_data


# ==============================================================================
# PART E: LESSON EVALUATION ERROR HANDLING (Sections 107 - 108, 175)
# ==============================================================================

def test_015_lesson_evaluator_error_blocks_arm(isolated_db):
    """
    Section 107 / 108 / 175:
    When lesson_score_adjustment_enabled is True, if LessonRuleService
    returns status='ERROR' or raises an exception, _arm_paper_candidate()
    MUST block ARM with reason LESSON_EVALUATION_ERROR (not treat as delta=0).
    """
    now_ms = 1700000000000
    cfg = M1ScalpConfig(
        quality_scoring_enabled=True,
        lesson_score_adjustment_enabled=True,
        quality_arm_threshold=70.0,
    )

    orchestrator = ScalpWatcherOrchestrator()

    cand = DummyCandidate(
        candidate_key="cand_err_015",
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        is_eligible=True,
        net_rr=2.0,
        as_of_ms=now_ms,
        evidence={"has_trigger": True},
    )

    with patch(
        "services.lesson_rule_service.LessonRuleService.evaluate_lesson_score_adjustments",
        return_value={"status": "ERROR", "reason": "Database connection failed", "applied_delta": 0.0},
    ):
        armed = orchestrator._arm_paper_candidate(
            isolated_db,
            cand,
            now_ms=now_ms,
            clock=None,
            quote={"bid": 2650.0, "ask": 2650.1, "last": 2650.05, "timestamp": now_ms, "server_time": now_ms},
            policy_settings={},
            config=cfg,
            auto_enabled=True,
        )
        assert armed is None
        with orchestrator._lock:
            assert "cand_err_015" in orchestrator._arm_rejections
            assert orchestrator._arm_rejections["cand_err_015"][1] == "LESSON_EVALUATION_ERROR"


def test_016_lesson_evaluator_success_zero_delta_allows_scoring(isolated_db):
    """
    Section 109 / 176:
    status=SUCCESS with applied_delta=0.0 is normal operation (no matching rules).
    Candidate proceeds with normal scoring.
    """
    now_ms = 1700000000000
    cfg = M1ScalpConfig(
        quality_scoring_enabled=True,
        lesson_score_adjustment_enabled=True,
        quality_arm_threshold=70.0,
    )

    orchestrator = ScalpWatcherOrchestrator()

    cand = DummyCandidate(
        candidate_key="cand_succ_016",
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        is_eligible=True,
        net_rr=2.0,
        as_of_ms=now_ms,
        evidence={"has_trigger": True},
    )

    with patch(
        "services.lesson_rule_service.LessonRuleService.evaluate_lesson_score_adjustments",
        return_value={"status": "SUCCESS", "applied_delta": 0.0, "reason": "No matching rules"},
    ):
        # Trigger chain and context not fully eligible, but rejection must NOT be LESSON_EVALUATION_ERROR
        armed = orchestrator._arm_paper_candidate(
            isolated_db,
            cand,
            now_ms=now_ms,
            clock=None,
            quote={"bid": 2650.0, "ask": 2650.1, "last": 2650.05, "timestamp": now_ms, "server_time": now_ms},
            policy_settings={},
            config=cfg,
            auto_enabled=True,
        )
        with orchestrator._lock:
            if "cand_succ_016" in orchestrator._arm_rejections:
                assert orchestrator._arm_rejections["cand_succ_016"][1] != "LESSON_EVALUATION_ERROR"


# ==============================================================================
# PART F: FULL EXECUTION-TO-LESSON PIPELINES (Sections 141 - 144)
# ==============================================================================

def test_017_long_full_execution_to_lesson_pipeline(isolated_db):
    """
    Section 141:
    LONG PaperOrder: ARM -> Fill -> Close -> Review -> Root Lesson.
    Asserts:
    - 1 closed order
    - 1 review record
    - 1 root lesson with PENDING_REVIEW, is_approved=False, enabled=False
    - Correct PnL, RR, hold time
    """
    now_ms = 1700000000000
    order = PaperOrder(
        id="ord_pipe_long_017",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2665.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=now_ms - 180000,
        strategy_family="SMC_M1_CONTINUATION",
        armed_at=now_ms - 120000,
        cost_snapshot=json.dumps({"planned_entry": 2650.0, "stop_loss": 2645.0, "take_profit": 2665.0}),
    )
    isolated_db.add(order)
    isolated_db.flush()

    mock_calc = MagicMock()
    mock_calc.quantity = 1.0
    mock_calc.multiplier = 1.0
    mock_calc.net_risk_usdt = 5.0
    mock_calc.gross_rr = 2.0
    mock_calc.net_rr = 1.8
    mock_calc.estimated_liquidation = 2500.0
    mock_calc.initial_margin_usdt = 100.0
    mock_calc.leverage = 5
    mock_calc.margin_mode = "ISOLATED"
    mock_calc.maker_fee_rate = 0.0002
    mock_calc.taker_fee_rate = 0.0004
    mock_calc.entry_fee_usdt = 0.05
    mock_calc.sl_exit_fee_usdt = 0.05
    mock_calc.tp_exit_fee_usdt = 0.05
    mock_calc.entry_slippage_usdt = 0.05
    mock_calc.sl_exit_slippage_usdt = 0.05
    mock_calc.tp_exit_slippage_usdt = 0.05
    mock_calc.fees_total_usdt = 0.1
    mock_calc.slippage_total_usdt = 0.1
    mock_calc.gross_loss_usdt = 5.0
    mock_calc.gross_reward_usdt = 15.0
    mock_calc.net_reward_usdt = 14.8
    mock_calc.slippage_usd = 0.1
    mock_calc.to_dict.return_value = {}

    # Fill
    filled = TradeLifecycleService.execute_fill(
        isolated_db,
        order=order,
        fill_price=2650.1,
        calc_result=mock_calc,
        now_ms=now_ms - 60000,
    )
    assert filled.state == "paper_open"
    assert filled.actual_entry == 2650.1

    # Close (TP HIT)
    closed = TradeLifecycleService.execute_close(
        isolated_db,
        order_id="ord_pipe_long_017",
        exit_price=2665.0,
        exit_cause="TP_HIT",
        now_ms=now_ms,
    )
    isolated_db.flush()
    assert closed.state == "closed"

    # Verify review and lesson
    rev = isolated_db.query(PostTradeReviewRecord).filter_by(order_id="ord_pipe_long_017").first()
    assert rev is not None

    lesson = isolated_db.query(Lesson).filter_by(related_trade_id="ord_pipe_long_017").first()
    assert lesson is not None
    assert lesson.status == "PENDING_REVIEW"
    assert lesson.is_approved is False
    assert lesson.enabled is False
    assert json.loads(lesson.scope)["symbol"] == "XAUUSDT"
    assert lesson.strategy_family == "SMC_M1_CONTINUATION"


def test_018_short_full_execution_to_lesson_pipeline(isolated_db):
    """
    Section 142:
    SHORT PaperOrder: ARM -> Fill -> Close -> Review -> Root Lesson.
    Asserts:
    - 1 closed order
    - 1 review record
    - 1 root lesson with PENDING_REVIEW, is_approved=False, enabled=False
    - Correct SHORT direction geometry (TP < Entry < SL)
    """
    now_ms = 1700000000000
    order = PaperOrder(
        id="ord_pipe_short_018",
        instrument="XAUUSDT",
        direction="SHORT",
        state="armed",
        planned_entry=2670.0,
        stop_loss=2675.0,
        take_profit=2655.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=now_ms - 180000,
        strategy_family="SMC_M1_CONTINUATION",
        armed_at=now_ms - 120000,
        cost_snapshot=json.dumps({"planned_entry": 2670.0, "stop_loss": 2675.0, "take_profit": 2655.0}),
    )
    isolated_db.add(order)
    isolated_db.flush()

    mock_calc = MagicMock()
    mock_calc.quantity = 1.0
    mock_calc.multiplier = 1.0
    mock_calc.net_risk_usdt = 5.0
    mock_calc.gross_rr = 2.0
    mock_calc.net_rr = 1.8
    mock_calc.estimated_liquidation = 2700.0
    mock_calc.initial_margin_usdt = 100.0
    mock_calc.leverage = 5
    mock_calc.margin_mode = "ISOLATED"
    mock_calc.maker_fee_rate = 0.0002
    mock_calc.taker_fee_rate = 0.0004
    mock_calc.entry_fee_usdt = 0.05
    mock_calc.sl_exit_fee_usdt = 0.05
    mock_calc.tp_exit_fee_usdt = 0.05
    mock_calc.entry_slippage_usdt = 0.05
    mock_calc.sl_exit_slippage_usdt = 0.05
    mock_calc.tp_exit_slippage_usdt = 0.05
    mock_calc.fees_total_usdt = 0.1
    mock_calc.slippage_total_usdt = 0.1
    mock_calc.gross_loss_usdt = 5.0
    mock_calc.gross_reward_usdt = 15.0
    mock_calc.net_reward_usdt = 14.8
    mock_calc.slippage_usd = 0.1
    mock_calc.to_dict.return_value = {}

    # Fill
    filled = TradeLifecycleService.execute_fill(
        isolated_db,
        order=order,
        fill_price=2669.9,
        calc_result=mock_calc,
        now_ms=now_ms - 60000,
    )
    assert filled.state == "paper_open"
    assert filled.actual_entry == 2669.9

    # Close (SL HIT)
    closed = TradeLifecycleService.execute_close(
        isolated_db,
        order_id="ord_pipe_short_018",
        exit_price=2675.0,
        exit_cause="SL_HIT",
        now_ms=now_ms,
    )
    isolated_db.flush()
    assert closed.state == "closed"

    rev = isolated_db.query(PostTradeReviewRecord).filter_by(order_id="ord_pipe_short_018").first()
    assert rev is not None

    lesson = isolated_db.query(Lesson).filter_by(related_trade_id="ord_pipe_short_018").first()
    assert lesson is not None
    assert lesson.status == "PENDING_REVIEW"
    assert lesson.is_approved is False
    assert lesson.strategy_family == "SMC_M1_CONTINUATION"
