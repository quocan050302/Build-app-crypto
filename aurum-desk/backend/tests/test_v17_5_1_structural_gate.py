"""
Aurum V17.5.1 — Structural Gate & ARM Barrier Tests
Verifies fail-closed execution barrier in PAPER_STRUCTURAL,
pure shadow compatibility in SHADOW_COMPARE, and ARM barrier checks.
"""
from types import SimpleNamespace
import pytest
from services.stop_placement_types import (
    StopPlacementPolicy,
    StopAnchorResult,
    StopATRResult,
    StopPlacementDecision,
)
from domain_calculator import CalculationResult
from services.scalp_m1_model_service import apply_stop_placement_to_candidate, ScalpCandidate
from services.scalp_m1_types import get_scalp_config
from services.stop_placement_service import validate_applied_stop_decision


def test_structural_gate_missing_anchor_blocks_paper_structural():
    stop_policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", version=1)
    result = apply_stop_placement_to_candidate(
        model_id="SMC_M1_CONTINUATION",
        direction="SHORT",
        entry_price=2000.0,
        baseline_sl=2005.0,
        baseline_target=1980.0,
        closed_1m=[],
        now_ms=1000000,
        cfg=get_scalp_config(),
        policy_settings={"capital_usdt": 1000, "risk_pct": 0.25, "leverage": 5},
        stop_policy=stop_policy,
        stop_context={"atr_m1": StopATRResult(status="AVAILABLE", value=1.5, period=14, timeframe="1M", method="SMA_TR_V1", last_closed_at_ms=990000, bars_count=15)},
        candidate_key="cand_missing_anchor",
        anchor_result=StopAnchorResult(status="WAITING_EVIDENCE", anchor=None),
        atr_1m_val=1.5,
        bid=2000.0,
        ask=2000.2,
    )
    assert not result.allowed, "Structural application must not be allowed when anchor is missing"
    assert result.decision.status == "WAITING_EVIDENCE"
    assert result.decision.applied_variant != "C"
    if result.calculation is not None:
        assert not result.calculation.can_execute


def test_shadow_compare_allows_legacy_execution_even_if_c_missing():
    stop_policy = StopPlacementPolicy(mode="SHADOW_COMPARE", version=1)
    result = apply_stop_placement_to_candidate(
        model_id="SMC_M1_CONTINUATION",
        direction="SHORT",
        entry_price=2000.0,
        baseline_sl=2005.0,
        baseline_target=1980.0,
        closed_1m=[],
        now_ms=1000000,
        cfg=get_scalp_config(),
        policy_settings={"capital_usdt": 1000, "risk_pct": 0.25, "leverage": 5},
        stop_policy=stop_policy,
        stop_context={"atr_m1": StopATRResult(status="UNAVAILABLE", value=0.0, period=14, timeframe="1M", method="SMA_TR_V1", last_closed_at_ms=0, bars_count=0)},
        candidate_key="cand_shadow",
        anchor_result=StopAnchorResult(status="WAITING_EVIDENCE", anchor=None),
        atr_1m_val=1.5,
        bid=2000.0,
        ask=2000.2,
    )
    assert result.allowed, "SHADOW_COMPARE must allow Variant A execution even if Variant C anchor is missing"
    assert result.decision.applied_variant == "A"
    assert result.calculation is not None
    assert result.calculation.can_execute


def test_validate_applied_stop_decision_rejects_tampered_candidate():
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", version=2)

    # Tampered candidate: claims mode PAPER_STRUCTURAL but applied Variant A
    tampered_decision = StopPlacementDecision(
        status="VALID",
        mode="PAPER_STRUCTURAL",
        policy_version=2,
        applied_variant="A",  # Invalid for PAPER_STRUCTURAL
        selected_sl=2005.0,
        anchor=None,
        buffer=None,
        metrics=None,
    )
    calc = SimpleNamespace(
        can_execute=True,
        meets_min_rr=True,
        stop_loss=2005.0,
        execution_price=2000.0,
    )
    candidate = SimpleNamespace(
        stop_loss=2005.0,
        stop_placement=tampered_decision,
        calculation=calc,
    )

    is_valid, reason = validate_applied_stop_decision(candidate=candidate, current_policy=policy)
    assert not is_valid, "Tampered candidate must be rejected by ARM barrier"
    assert "STRUCTURAL_MODE_EXPECTS_VARIANT_C" in reason
