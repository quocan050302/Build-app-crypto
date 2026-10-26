import pytest
from services.post_trade_review_service import (
    PostTradeReview,
    EntryComplianceResult,
    classify_post_trade_lesson,
)


def test_compliant_loss_missing_stop_evidence_returns_factual_limitation():
    """
    Part 008, 134, 135:
    When a trade hits SL and has verified plan compliance, but has NO stop evidence
    (missing anchor / ATR proof), the lesson must NOT state 'dừng lỗ theo đúng cấu trúc'.
    It must factually state that plan compliance was verified, while stop quality is unproven.
    """
    review = PostTradeReview(
        order_id="test_order_loss_1",
        symbol="XAUUSDT",
        model_id="M1_CONTINUATION",
        direction="SHORT",
        planned_entry=4000.0,
        actual_entry=4000.0,
        planned_sl=4005.0,
        planned_tp=3980.0,
        exit_price=4005.0,
        exit_cause="SL_HIT",
        gross_pnl=-5.0,
        net_pnl=-5.5,
        realized_rr=-1.0,
        hold_time_ms=60000,
        entry_slippage_usd=0.0,
        loss_classification="STRATEGY_VALID_LOSS",
        outcome_status="LOSS",
    )

    comp = EntryComplianceResult(
        status="VERIFIED_COMPLIANT",
        reason="Kế hoạch hợp lệ",
        missing_evidence=[],
    )

    # When entry_snapshot has NO stop_placement
    lesson = classify_post_trade_lesson(review=review, entry_snapshot={}, compliance_result=comp)
    assert lesson is not None
    # Must NOT claim valid structural stop without evidence
    assert "dừng lỗ hợp lệ theo đúng cấu trúc" not in lesson.reason_vi
    assert "chất lượng điểm SL chưa được kiểm chứng" in lesson.reason_vi


def test_verified_violation_returns_red_lesson():
    """
    When entry compliance is a verified violation (e.g. chased entry or breached guard),
    the post-trade lesson category is RED.
    """
    review = PostTradeReview(
        order_id="test_order_loss_2",
        symbol="XAUUSDT",
        model_id="M1_CONTINUATION",
        direction="LONG",
        planned_entry=4000.0,
        actual_entry=4000.0,
        planned_sl=3995.0,
        planned_tp=4020.0,
        exit_price=3995.0,
        exit_cause="SL_HIT",
        gross_pnl=-5.0,
        net_pnl=-5.5,
        realized_rr=-1.0,
        hold_time_ms=60000,
        entry_slippage_usd=0.0,
        loss_classification="STRATEGY_VALID_LOSS",
        outcome_status="LOSS",
    )

    comp = EntryComplianceResult(
        status="VERIFIED_VIOLATION",
        reason="Vi phạm entry muộn",
        missing_evidence=[],
    )

    lesson = classify_post_trade_lesson(review=review, compliance_result=comp)
    assert lesson is not None
    assert lesson.suggested_color == "RED"
    assert lesson.classification_code == "VERIFIED_RULE_VIOLATION"
