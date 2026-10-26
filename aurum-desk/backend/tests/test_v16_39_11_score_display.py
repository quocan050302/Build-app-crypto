"""
backend/tests/test_v16_39_11_score_display.py
V16.39.11 Acceptance & Regression Suite: Candidate Quality Score Display & Preservation

Covers Rules 051-100 & 153-167:
- Pure score parser validation (valid 78, zero score 0, boolean rejection, range bounds)
- Preservation of quality_assessment across build_fill_plan_snapshot (no metadata loss, no mutable alias)
- Order score projection via build_order_score_display and build_order_score_detail
- PaperOrder.candidate_score_summary and PaperOrderResponse Pydantic serialization
- Legacy order graceful degradation (UNAVAILABLE / LEGACY_NO_SCORE without crashing or scorer invocation)
- Pre-ARM volatile orchestrator display cache
"""

import json
import pytest
from unittest.mock import MagicMock

import models
import schemas
from services.order_quality_display_service import (
    CandidateScoreDisplay,
    CandidateScoreDetail,
    parse_candidate_quality_assessment,
    build_order_score_display,
    build_order_score_detail,
)
from services.order_plan_snapshot_service import (
    build_fill_plan_snapshot,
    extract_immutable_entry_metadata,
)
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator


def test_pure_score_parser_valid_and_zero():
    """
    Rules 053-054, 153:
    Valid score 78.0, base 83.0, delta -5.0, threshold 70.0.
    Score 0.0 must be valid and preserved (not coerced to None or default 70).
    """
    valid_payload = {
        "assessment_id": "assess_123",
        "candidate_key": "cand_key_1",
        "model_id": "CG01",
        "base_score": 83.0,
        "lesson_delta": -5.0,
        "final_score": 78.0,
        "threshold": 70.0,
        "decision": "ELIGIBLE_BY_SCORE",
        "as_of_ms": 1700000000000,
        "profile_version": "v16.39",
        "config_fingerprint": "fp_abc",
        "factors": [
            {
                "factor_id": "f_fvg",
                "name": "FVG Alignment",
                "group": "STRUCTURE",
                "earned_points": 18.0,
                "max_points": 20.0,
                "status": "PASS",
                "reason": "Clear 1M FVG",
            }
        ],
        "group_scores": {"STRUCTURE": {"earned": 18.0, "max": 20.0}},
        "missing_data": [],
        "blockers": [],
    }

    parsed, status = parse_candidate_quality_assessment(valid_payload)
    assert status == "VALID"
    assert parsed["final_score"] == 78.0
    assert parsed["base_score"] == 83.0
    assert parsed["lesson_delta"] == -5.0
    assert parsed["threshold"] == 70.0
    assert parsed["decision"] == "ELIGIBLE_BY_SCORE"

    # Zero score case (Rule 063: score=0 must remain 0/100, not missing or unassessed)
    zero_payload = dict(valid_payload)
    zero_payload["final_score"] = 0.0
    parsed_zero, status_zero = parse_candidate_quality_assessment(zero_payload)
    assert status_zero == "VALID"
    assert parsed_zero["final_score"] == 0.0


def test_pure_score_parser_rejects_invalid_types_and_ranges():
    """
    Rules 054, 153:
    Boolean masquerading as float/int must be rejected.
    Scores outside [0.0, 100.0] must be rejected (no silent clamping of 150 to 100).
    Malformed JSON or empty input must be rejected with proper reason codes.
    """
    # Boolean rejection
    bool_payload = {"final_score": True}
    _, status = parse_candidate_quality_assessment(bool_payload)
    assert status == "INVALID_FINAL_SCORE"

    # Out of range rejection (no clamping)
    over_payload = {"final_score": 150.0}
    _, status_over = parse_candidate_quality_assessment(over_payload)
    assert status_over == "SCORE_OUT_OF_RANGE"

    neg_payload = {"final_score": -10.0}
    _, status_neg = parse_candidate_quality_assessment(neg_payload)
    assert status_neg == "SCORE_OUT_OF_RANGE"

    # Malformed JSON
    _, status_json = parse_candidate_quality_assessment("{not_valid_json")
    assert status_json == "INVALID_JSON"

    # Empty
    _, status_empty = parse_candidate_quality_assessment("")
    assert status_empty == "EMPTY_OR_NONE"
    _, status_none = parse_candidate_quality_assessment(None)
    assert status_none == "EMPTY_OR_NONE"


def test_fill_plan_snapshot_preserves_quality_assessment_immutable():
    """
    Rules 066-067, 155-156:
    Calling build_fill_plan_snapshot on an order with quality_assessment in cost_snapshot
    preserves the entire assessment without mutable aliasing.
    """
    raw_assessment = {
        "assessment_id": "assess_fill_test",
        "candidate_key": "cand_key_99",
        "model_id": "CG01",
        "base_score": 83.0,
        "lesson_delta": -5.0,
        "final_score": 78.0,
        "threshold": 70.0,
        "decision": "ELIGIBLE_BY_SCORE",
        "as_of_ms": 1700000000000,
        "factors": [{"factor_id": "f1", "earned_points": 10.0}],
    }

    arm_snapshot = {
        "phase": "ARMED_ESTIMATE",
        "direction": "LONG",
        "planned_entry": 2000.0,
        "stop_loss": 1990.0,
        "take_profit": 2020.0,
        "setup_id": "ws_1",
        "setup_instance_id": "cand_key_99",
        "quality_assessment": raw_assessment,
    }

    order = MagicMock()
    order.id = "ord_fill_test"
    order.direction = "LONG"
    order.planned_entry = 2000.0
    order.stop_loss = 1990.0
    order.take_profit = 2020.0
    order.quantity = 1.0
    order.capital_usdt = 1000.0
    order.cost_snapshot = json.dumps(arm_snapshot)
    order.setup_instance_id = "cand_key_99"

    calc_res = MagicMock()
    calc_res.quantity = 1.0
    calc_res.multiplier = 1.0
    calc_res.net_rr = 1.95
    calc_res.gross_rr = 2.0
    calc_res.slippage_usd = 0.10
    calc_res.leverage = 5
    calc_res.margin_mode = "ISOLATED"
    calc_res.initial_margin_usdt = 200.0
    calc_res.estimated_liquidation = 1600.0

    fill_snap = build_fill_plan_snapshot(
        calc_res,
        order,
        fill_price=2000.50,
        now_ms=1700000010000,
    )

    fill_dict = fill_snap.to_dict() if hasattr(fill_snap, "to_dict") else dict(fill_snap)
    assert "quality_assessment" in fill_dict
    assert fill_dict["quality_assessment"]["final_score"] == 78.0
    assert fill_dict["quality_assessment"]["assessment_id"] == "assess_fill_test"

    # Rule 156: Verify deep copy (modifying fill copy does NOT mutate arm_snapshot)
    fill_dict["quality_assessment"]["factors"][0]["earned_points"] = 999.0
    assert arm_snapshot["quality_assessment"]["factors"][0]["earned_points"] == 10.0


def test_build_order_score_display_and_detail():
    """
    Rules 060-061, 159:
    build_order_score_display returns CandidateScoreDisplay with frozen=True, source=ARM_SNAPSHOT.
    build_order_score_detail extends with factors and group_scores.
    """
    assessment = {
        "assessment_id": "as_1",
        "candidate_key": "inst_1",
        "model_id": "CG01",
        "base_score": 83.0,
        "lesson_delta": -5.0,
        "final_score": 78.0,
        "threshold": 70.0,
        "decision": "ELIGIBLE_BY_SCORE",
        "as_of_ms": 1700000000000,
        "factors": [{"factor_id": "f_1", "name": "Factor 1", "group": "SMC", "earned_points": 78.0, "max_points": 100.0, "status": "PASS", "reason": "OK"}],
        "group_scores": {"SMC": {"earned": 78.0, "max": 100.0}},
        "missing_data": [],
        "blockers": [],
    }
    cost_snap = json.dumps({"quality_assessment": assessment})

    order = MagicMock()
    order.id = "ord_test_1"
    order.cost_snapshot = cost_snap
    order.setup_instance_id = "inst_1"
    order.armed_at = 1700000001000

    disp = build_order_score_display(order)
    assert disp.status == "AVAILABLE"
    assert disp.score == 78.0
    assert disp.base_score == 83.0
    assert disp.lesson_delta == -5.0
    assert disp.threshold == 70.0
    assert disp.source == "ARM_SNAPSHOT"
    assert disp.frozen is True

    detail = build_order_score_detail(order)
    assert detail.status == "AVAILABLE"
    assert detail.score == 78.0
    assert len(detail.factors) == 1
    assert "SMC" in detail.group_scores


def test_legacy_order_without_score_reports_unavailable():
    """
    Rules 070, 161: Legacy order filled prior to patch without quality_assessment.
    Returns UNAVAILABLE with reason_code LEGACY_NO_SCORE; does not crash or invoke scorer.
    """
    order = MagicMock()
    order.id = "ord_legacy_1"
    order.cost_snapshot = json.dumps({"phase": "FILLED_ESTIMATE", "actual_entry": 2000.0})
    order.setup_instance_id = "legacy_inst"
    order.armed_at = 1600000000000

    disp = build_order_score_display(order)
    assert disp.status == "UNAVAILABLE"
    assert disp.reason_code == "LEGACY_NO_SCORE"
    assert disp.score is None
    assert disp.source == "NONE"


def test_order_model_and_pydantic_schema_serialization():
    """
    Rules 075, 159:
    PaperOrder.candidate_score_summary property is read by PaperOrderResponse.model_validate.
    """
    assessment = {
        "assessment_id": "as_model_1",
        "candidate_key": "inst_model_1",
        "model_id": "CG01",
        "base_score": 80.0,
        "lesson_delta": 0.0,
        "final_score": 80.0,
        "threshold": 70.0,
        "decision": "ELIGIBLE_BY_SCORE",
        "as_of_ms": 1700000000000,
    }

    order = models.PaperOrder(
        id="ord_pydantic_1",
        setup_id="ws_1",
        setup_instance_id="inst_model_1",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="MARKET",
        timeframe="15M",
        planned_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        risk_pct=0.25,
        gross_rr=2.0,
        estimated_net_rr=1.9,
        fees_assumption=0.10,
        slippage_assumption=0.10,
        leverage=5,
        margin_mode="ISOLATED",
        strategy_version="1.0.0",
        strategy_family="STANDARD_SMC",
        origin="AUTO_STRATEGY",
        execution_mode="AUTO",
        created_at=1700000000000,
        cost_snapshot=json.dumps({"quality_assessment": assessment}),
    )

    # Verify model property
    summary = order.candidate_score_summary
    assert summary is not None
    assert summary["score"] == 80.0
    assert summary["status"] == "AVAILABLE"

    # Verify Pydantic response serialization
    resp = schemas.PaperOrderResponse.model_validate(order)
    dumped = resp.model_dump()
    assert dumped["candidate_score_summary"] is not None
    assert dumped["candidate_score_summary"]["score"] == 80.0
    assert dumped["candidate_score_summary"]["status"] == "AVAILABLE"


def test_pre_arm_volatile_cache_in_orchestrator():
    """
    Rules 083-086, 164:
    ScalpWatcherOrchestrator caches pre-ARM evaluation in bounded volatile display cache.
    Reads back exact binding matching symbol, setup_id, candidate_key, and direction.
    """
    orchestrator = ScalpWatcherOrchestrator()

    mock_assessment = MagicMock()
    mock_assessment.to_dict.return_value = {
        "assessment_id": "as_cache_1",
        "candidate_key": "cand_cache_1",
        "model_id": "CG01",
        "base_score": 82.0,
        "lesson_delta": -15.0,
        "final_score": 67.0,
        "threshold": 70.0,
        "decision": "BELOW_THRESHOLD",
        "as_of_ms": 1700000000000,
    }

    orchestrator.record_candidate_quality_display(
        symbol="XAUUSDT",
        setup_id="setup_cache_1",
        setup_instance_id="cand_cache_1",
        candidate_key="cand_cache_1",
        direction="SHORT",
        assessment=mock_assessment,
    )

    # Exact read
    disp = orchestrator.get_candidate_quality_display(
        symbol="XAUUSDT",
        setup_id="setup_cache_1",
        setup_instance_id="cand_cache_1",
        direction="SHORT",
    )
    assert disp is not None
    assert disp["score"] == 67.0
    assert disp["decision"] == "BELOW_THRESHOLD"
    assert disp["source"] == "VOLATILE_LATEST_EVALUATION"

    # Opposite direction must NOT match (no direction bleed)
    disp_opp = orchestrator.get_candidate_quality_display(
        symbol="XAUUSDT",
        setup_id="setup_cache_1",
        setup_instance_id="cand_cache_1",
        direction="LONG",
    )
    assert disp_opp is None
