"""
backend/tests/test_v16_39_12_score_binding_contract.py
V16.39.12 Regression Suite: Candidate Quality Score Binding, Factor Canonicalization, & Snapshot Parsing Robustness

Tests Bugs 1, 2, 4, 5:
- Bug 1: Exact identity binding in record/get_candidate_quality_display (no fallback bleed across setup_instance_id, candidate keys with colons)
- Bug 2: QualityScoreFactor canonical fields preservation (code, group, earned_points, max_points, status, explanation_vi, evidence_ids)
- Bug 4: Cost snapshot non-dict handling ([], null, 5, "hello" must return INVALID/MALFORMED_COST_SNAPSHOT_TYPE, not AttributeError)
- Bug 5: No fabricated defaults (missing base_score, threshold, lesson_delta, profile_version must remain None)
"""

import pytest
from unittest.mock import MagicMock

from services.order_quality_display_service import (
    CandidateScoreDisplay,
    CandidateScoreDetail,
    parse_candidate_quality_assessment,
    build_order_score_display,
    build_order_score_detail,
)
from services.setup_quality_service import QualityScoreFactor, CandidateQualityAssessment
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator


def test_021_024_cache_exact_instance_identity_reproduce_and_repair():
    """
    Bug 1 & Rules 021-024:
    Record setup_id='setup_cache_1', setup_instance_id='old', candidate_key='old' with score 78.0.
    Query with setup_instance_id='NEW' must return None (NOT 78.0 from 'old').
    After recording 'NEW' with score 64.0, 'NEW' -> 64.0 and 'old' -> 78.0.
    """
    orchestrator = ScalpWatcherOrchestrator()

    mock_old = MagicMock()
    mock_old.to_dict.return_value = {
        "assessment_id": "assess_old",
        "candidate_key": "old",
        "model_id": "CG01",
        "base_score": 83.0,
        "lesson_delta": -5.0,
        "final_score": 78.0,
        "threshold": 70.0,
        "decision": "ELIGIBLE_BY_SCORE",
        "as_of_ms": 1700000000000,
    }

    orchestrator.record_candidate_quality_display(
        symbol="XAUUSDT",
        setup_id="setup_cache_1",
        setup_instance_id="old",
        candidate_key="old",
        direction="SHORT",
        assessment=mock_old,
    )

    # EXACT identity check: Query with setup_instance_id='NEW' must NOT return 'old' score 78.0!
    disp_new = orchestrator.get_candidate_quality_display(
        symbol="XAUUSDT",
        setup_id="setup_cache_1",
        setup_instance_id="NEW",
        direction="SHORT",
    )
    assert disp_new is None, f"Expected None for unrecorded instance 'NEW', got {disp_new}"

    # Now record 'NEW' with 64.0
    mock_new = MagicMock()
    mock_new.to_dict.return_value = {
        "assessment_id": "assess_new",
        "candidate_key": "NEW",
        "model_id": "CG01",
        "base_score": 64.0,
        "lesson_delta": 0.0,
        "final_score": 64.0,
        "threshold": 70.0,
        "decision": "BELOW_THRESHOLD",
        "as_of_ms": 1700000005000,
    }

    orchestrator.record_candidate_quality_display(
        symbol="XAUUSDT",
        setup_id="setup_cache_1",
        setup_instance_id="NEW",
        candidate_key="NEW",
        direction="SHORT",
        assessment=mock_new,
    )

    disp_new_after = orchestrator.get_candidate_quality_display(
        symbol="XAUUSDT",
        setup_id="setup_cache_1",
        setup_instance_id="NEW",
        direction="SHORT",
    )
    assert disp_new_after is not None
    assert disp_new_after["score"] == 64.0
    assert disp_new_after["candidate_key"] == "NEW"

    disp_old_after = orchestrator.get_candidate_quality_display(
        symbol="XAUUSDT",
        setup_id="setup_cache_1",
        setup_instance_id="old",
        direction="SHORT",
    )
    assert disp_old_after is not None
    assert disp_old_after["score"] == 78.0
    assert disp_old_after["candidate_key"] == "old"


def test_021_038_candidate_key_with_colons_no_split_breakage():
    """
    Rule 038: Candidate key containing colons (e.g. 'XAUUSDT:15M:SHORT:2650.0:cand_1')
    must not break cache key parsing or exact retrieval.
    """
    orchestrator = ScalpWatcherOrchestrator()
    cand_k = "XAUUSDT:15M:SHORT:2650.0:cand_1"

    mock_assess = MagicMock()
    mock_assess.to_dict.return_value = {
        "assessment_id": "assess_colon",
        "candidate_key": cand_k,
        "model_id": "CG01",
        "base_score": 75.0,
        "lesson_delta": 0.0,
        "final_score": 75.0,
        "threshold": 70.0,
        "decision": "ELIGIBLE_BY_SCORE",
        "as_of_ms": 1700000010000,
    }

    orchestrator.record_candidate_quality_display(
        symbol="XAUUSDT",
        setup_id="setup_colon",
        setup_instance_id=cand_k,
        candidate_key=cand_k,
        direction="SHORT",
        assessment=mock_assess,
    )

    disp = orchestrator.get_candidate_quality_display(
        symbol="XAUUSDT",
        setup_id="setup_colon",
        setup_instance_id=cand_k,
        candidate_key=cand_k,
        direction="SHORT",
    )
    assert disp is not None
    assert disp["score"] == 75.0
    assert disp["candidate_key"] == cand_k


def test_041_042_malformed_cost_snapshot_no_attribute_error():
    """
    Bug 4 & Rules 041-042:
    cost_snapshot containing JSON non-dict scalars or lists:
    - '[]'
    - 'null'
    - '5'
    - '"hello"'
    must return CandidateScoreDisplay with status='INVALID' and reason_code='MALFORMED_COST_SNAPSHOT_TYPE'
    instead of raising AttributeError.
    """
    malformed_cases = ["[]", "null", "5", '"hello"']

    for malformed_raw in malformed_cases:
        order = MagicMock()
        order.cost_snapshot = malformed_raw
        order.setup_instance_id = "inst_1"
        order.armed_at = 1700000000000

        # Must not raise AttributeError
        disp = build_order_score_display(order)
        assert disp.status == "INVALID"
        assert disp.reason_code == "MALFORMED_COST_SNAPSHOT_TYPE"

        detail = build_order_score_detail(order)
        assert detail.status == "INVALID"
        assert detail.reason_code == "MALFORMED_COST_SNAPSHOT_TYPE"
        assert detail.factors == []


def test_045_047_no_fabricated_defaults_when_metadata_missing():
    """
    Bug 5 & Rules 045-047:
    When raw assessment only contains 'final_score', missing metadata must NOT be fabricated:
    - base_score must be None (NOT final_score 78.0)
    - threshold must be None (NOT 70.0)
    - lesson_delta must be None (NOT 0.0)
    - profile_version must be None (NOT 'v16.39')
    """
    minimal_payload = {
        "final_score": 78.0,
        "as_of_ms": 1700000000000,
    }

    parsed, status = parse_candidate_quality_assessment(minimal_payload)
    assert status == "VALID"
    assert parsed["final_score"] == 78.0
    assert parsed["base_score"] is None, f"Expected base_score None, got {parsed['base_score']}"
    assert parsed["threshold"] is None, f"Expected threshold None, got {parsed['threshold']}"
    assert parsed["lesson_delta"] is None, f"Expected lesson_delta None, got {parsed['lesson_delta']}"
    assert parsed["profile_version"] is None, f"Expected profile_version None, got {parsed['profile_version']}"


def test_055_058_canonical_quality_score_factor_preserved():
    """
    Bug 2 & Rules 055-058:
    QualityScoreFactor from production has canonical fields:
    code, group, earned_points, max_points, status, explanation_vi, evidence_ids, known_at_ms.
    parse_candidate_quality_assessment must preserve these canonical fields, NOT drop them.
    """
    factor = QualityScoreFactor(
        code="FVG_ALIGNMENT",
        group="LTF_CONFIRMATION",
        earned_points=18.0,
        max_points=20.0,
        status="FULL",
        explanation_vi="Vùng FVG M1 rõ ràng và chưa bị kiểm tra quá mức",
        evidence_ids=("ev_fvg_1",),
        known_at_ms=1700000000000,
        measurement={"gap_atr": 1.8},
        rule_version="v16.39",
    )

    assessment_dict = {
        "assessment_id": "assess_f",
        "candidate_key": "cand_f",
        "model_id": "CG01",
        "base_score": 83.0,
        "lesson_delta": -5.0,
        "final_score": 78.0,
        "threshold": 70.0,
        "decision": "ELIGIBLE_BY_SCORE",
        "as_of_ms": 1700000000000,
        "factors": [factor.to_dict()],
    }

    parsed, status = parse_candidate_quality_assessment(assessment_dict)
    assert status == "VALID"
    factors = parsed["factors"]
    assert len(factors) == 1
    f0 = factors[0]

    assert f0["code"] == "FVG_ALIGNMENT"
    assert f0["group"] == "LTF_CONFIRMATION"
    assert f0["earned_points"] == 18.0
    assert f0["max_points"] == 20.0
    assert f0["status"] == "FULL"
    assert f0["explanation_vi"] == "Vùng FVG M1 rõ ràng và chưa bị kiểm tra quá mức"
    assert f0["evidence_ids"] == ["ev_fvg_1"]
    assert f0["known_at_ms"] == 1700000000000
