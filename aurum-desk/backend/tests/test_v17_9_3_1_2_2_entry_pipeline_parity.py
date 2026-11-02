"""
backend/tests/test_v17_9_3_1_2_2_entry_pipeline_parity.py

Adversarial & Functional Unit Tests T17..T28 for V17.9.3.1.2.2
Unicorn / MTF / Liquidity / Both-Direction Evaluator Parity.
"""
from __future__ import annotations

from dataclasses import replace
import pytest

from services.scalp_m1_types import M1ScalpConfig, ModelIdentifier
from services.shared_strategy_evaluator import (
    SharedStrategyContext,
    SharedStrategyEvaluator,
)
from services.v17_9_3_1_2_1_root_cause_evidence_service import (
    classify_venue_market_state_asof_v1793121,
    validate_ic_timing_and_unicorn_episode_binding_v1793121,
)
from services.v17_9_3_1_2_2_signal_intelligence import (
    PREDECLARED_SIGNAL_INTELLIGENCE_POLICIES_V1793122,
    build_signal_quality_evidence_v1793122,
    evaluate_signal_intelligence_gate_v1793122,
    resolve_research_preferred_direction_v1793122,
    score_candidate_record_pre_entry_v1793122,
)
from tests.test_v17_9_3_1_2_2_signal_intelligence import _make_shared_context


def test_t17_true_breaker_and_fvg_same_causal_episode_accepted():
    """T17: True Breaker+FVG overlap in the same causal episode and direction is classified as UNICORN_CONFIRMED."""
    ctx = _make_shared_context(mode="RESEARCH_APPLY", policy_id="SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN")
    ev = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id=ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
        direction="LONG",
        setup_descriptors={
            "selected_fvg_id": "fvg_101",
            "fvg_formed_at_ms": ctx.now_ms - 180_000,
            "fvg_zone_low": 2653.5,
            "fvg_zone_high": 2654.8,
        },
        sh_1m=[{"id": "t1", "price": 2662.0, "occurred_at_ms": ctx.now_ms - 600_000, "known_at_ms": ctx.now_ms - 480_000}],
        sl_1m=[{"id": "s1", "price": 2651.0, "occurred_at_ms": ctx.now_ms - 600_000, "known_at_ms": ctx.now_ms - 480_000}],
        entry_price=2654.2,
        selected_sl=2651.5,
        take_profit=2661.5,
        selected_target_id="t1",
        atr_m1=1.0,
        atr_m5=2.0,
        prospective_net_rr=2.5,
        planned_risk_usd=2.0,
        unicorn_evidence={
            "fvg_present": True,
            "breaker_present": True,
            "overlap_valid": True,
            "overlap_low": 2653.8,
            "overlap_high": 2654.5,
            "breaker_id": "brk_101",
            "breaker_known_at_ms": ctx.now_ms - 120_000,
        },
    )
    assert ev.unicorn_classification == "UNICORN_CONFIRMED"
    assert ev.unicorn_overlap_low == 2653.8
    assert ev.unicorn_overlap_high == 2654.5


def test_t18_isolated_fvg_cannot_pass_strict_unicorn():
    """T18: Isolated 3-bar FVG without Breaker overlap is classified as FVG_ONLY and rejected by strict Unicorn policy SI4."""
    ctx = _make_shared_context(mode="RESEARCH_APPLY", policy_id="SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN")
    plan = evaluate_signal_intelligence_gate_v1793122(
        context=ctx,
        model_id=ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
        direction="LONG",
        mode="RESEARCH_APPLY",
        policy_id="SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN",
        adapter_origin="BACKTEST",
        baseline_status="ELIGIBLE",
        baseline_rejection_reasons=(),
        baseline_entry=2655.0,
        baseline_sl=2653.0,
        baseline_tp=2660.0,
        baseline_qty=0.05,
        baseline_gross_rr=2.5,
        baseline_net_rr=2.2,
        baseline_anchor_id="a1",
        baseline_anchor_kind="SWING",
        baseline_invalidation_price=2653.0,
        baseline_target_id="t1",
        setup_descriptors={"selected_fvg_id": "fvg_iso", "fvg_formed_at_ms": ctx.now_ms - 120_000},
        sh_1m=[],
        sl_1m=[],
        atr_m1=1.0,
        atr_m5=2.0,
        unicorn_evidence={"fvg_present": True, "breaker_present": False, "overlap_valid": False},
    )
    assert plan.evidence.unicorn_classification == "FVG_ONLY"
    assert plan.applied_status in ("REJECTED", "WAITING_CONFIRMATION")
    assert "SI_FVG_ONLY_NOT_TRUE_UNICORN" in plan.proposed_rejection_reasons


def test_t19_wrong_or_non_overlapping_breaker_rejected():
    """T19: Breaker with no price overlap or wrong causal episode is rejected."""
    res = validate_ic_timing_and_unicorn_episode_binding_v1793121(
        {
            "opportunity_id": "opp_t19",
            "model_id": "SMC_M1_FVG_CONTINUATION",
            "direction": "LONG",
            "decision_timestamp_ms": 280_000,
            "checkpoint_index_1m": 4,
            "entry_reference": 2650.5,
            "selected_sl": 2648.0,
            "take_profit": 2662.0,
        },
        {
            "1m": [
                {"open_time": 0, "close_time": 60_000, "open": 2650.0, "high": 2650.5, "low": 2649.5, "close": 2650.1},
                {"open_time": 60_000, "close_time": 120_000, "open": 2650.1, "high": 2650.5, "low": 2649.5, "close": 2650.1},
                {"open_time": 120_000, "close_time": 180_000, "open": 2650.1, "high": 2650.5, "low": 2649.5, "close": 2650.1},
                {"open_time": 180_000, "close_time": 240_000, "open": 2650.1, "high": 2650.5, "low": 2649.5, "close": 2650.1},
                {"open_time": 240_000, "close_time": 280_000, "open": 2650.1, "high": 2650.5, "low": 2649.5, "close": 2650.1},
            ],
            "5m": [],
            "15m": [],
        },
    )
    assert res["unicorn_breaker_overlap_verified"] is False
    assert res["true_unicorn_status"] != "CONFIRMED"


def test_t20_retest_before_setup_or_future_retest_rejected():
    """T20: Retest occurring before FVG/Breaker formation or after decision_ts_ms is rejected."""
    ctx = _make_shared_context(mode="RESEARCH_APPLY", policy_id="SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN")
    ev_future = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id=ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
        direction="LONG",
        setup_descriptors={"selected_fvg_id": "fvg_fut", "fvg_formed_at_ms": ctx.now_ms + 120_000},
        sh_1m=[],
        sl_1m=[],
        entry_price=2655.0,
        selected_sl=2653.0,
        take_profit=2660.0,
        selected_target_id="t1",
        atr_m1=1.0,
        atr_m5=2.0,
        prospective_net_rr=2.5,
        planned_risk_usd=2.0,
        unicorn_evidence={
            "fvg_present": True,
            "breaker_present": True,
            "overlap_valid": True,
            "overlap_low": 2654.0,
            "overlap_high": 2654.8,
            "breaker_known_at_ms": ctx.now_ms + 120_000,
        },
    )
    assert ev_future.unicorn_classification != "UNICORN_CONFIRMED"
    assert ev_future.causal_feature_violations_count > 0


def test_t21_mtf_m5_m15_h1_h4_closed_bar_known_at_anti_lookahead():
    """T21: M5/M15/H1/H4 bias computation uses only closed bars with close_time <= decision_timestamp_ms."""
    ctx = _make_shared_context(mode="RESEARCH_APPLY", policy_id="SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION")
    ev = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id=ModelIdentifier.SMC_M1_CONTINUATION.value,
        direction="LONG",
        setup_descriptors={},
        sh_1m=[],
        sl_1m=[],
        entry_price=2655.0,
        selected_sl=2653.0,
        take_profit=2660.0,
        selected_target_id="t1",
        atr_m1=1.0,
        atr_m5=2.0,
        prospective_net_rr=2.5,
        planned_risk_usd=2.0,
    )
    assert ev.h4_known_at_ms <= ctx.now_ms
    assert ev.h1_known_at_ms <= ctx.now_ms
    assert ev.m15_known_at_ms <= ctx.now_ms
    assert ev.m5_known_at_ms <= ctx.now_ms
    assert ev.causal_feature_violations_count == 0


def test_t22_liquidity_sweep_and_reclaim_causal_timing():
    """T22: Liquidity sweep and reclaim timestamps must satisfy sweep_occurred <= sweep_reclaimed <= decision_ts_ms."""
    ctx = _make_shared_context(mode="RESEARCH_APPLY", policy_id="SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN")
    # Future sweep_reclaimed_at_ms > now_ms must fail causal feature check
    ev_bad = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id=ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
        direction="LONG",
        setup_descriptors={
            "selected_sweep_id": "sw_future",
            "sweep_occurred_at_ms": ctx.now_ms - 60_000,
            "sweep_reclaimed_at_ms": ctx.now_ms + 60_000,
        },
        sh_1m=[],
        sl_1m=[],
        entry_price=2655.0,
        selected_sl=2653.0,
        take_profit=2660.0,
        selected_target_id="t1",
        atr_m1=1.0,
        atr_m5=2.0,
        prospective_net_rr=2.5,
        planned_risk_usd=2.0,
    )
    assert ev_bad.sweep_reclaimed is False
    assert ev_bad.causal_feature_violations_count > 0


def test_t23_unknown_or_insufficient_htf_quality_explicit_fail_closed():
    """T23: Insufficient/absent HTF bars produce INSUFFICIENT_DATA and fail closed under SI2/SI6."""
    ctx_empty_htf = replace(
        _make_shared_context(mode="RESEARCH_APPLY", policy_id="SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION"),
        closed_h1_candles=(),
        closed_h4_candles=(),
    )
    plan = evaluate_signal_intelligence_gate_v1793122(
        context=ctx_empty_htf,
        model_id=ModelIdentifier.SMC_M1_CONTINUATION.value,
        direction="LONG",
        mode="RESEARCH_APPLY",
        policy_id="SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION",
        adapter_origin="BACKTEST",
        baseline_status="ELIGIBLE",
        baseline_rejection_reasons=(),
        baseline_entry=2655.0,
        baseline_sl=2653.0,
        baseline_tp=2660.0,
        baseline_qty=0.05,
        baseline_gross_rr=2.5,
        baseline_net_rr=2.2,
        baseline_anchor_id="a1",
        baseline_anchor_kind="SWING",
        baseline_invalidation_price=2653.0,
        baseline_target_id="t1",
        setup_descriptors={},
        sh_1m=[],
        sl_1m=[],
        atr_m1=1.0,
        atr_m5=2.0,
        unicorn_evidence={},
    )
    assert plan.evidence.htf_alignment_state == "INSUFFICIENT_DATA"
    assert plan.applied_status == "REJECTED"
    assert "SI_HTF_DATA_INSUFFICIENT_FAIL_CLOSED" in plan.applied_rejection_reasons


def test_t24_predeclared_si0_through_si6_ablation_policies_distinct():
    """T24: Pre-registered policies SI0..SI6 configure distinct structure, timing, liquidity, and geometry gates."""
    p_si0 = PREDECLARED_SIGNAL_INTELLIGENCE_POLICIES_V1793122["SI0_OFF_BASELINE"]
    p_si2 = PREDECLARED_SIGNAL_INTELLIGENCE_POLICIES_V1793122["SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION"]
    p_si4 = PREDECLARED_SIGNAL_INTELLIGENCE_POLICIES_V1793122["SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN"]
    p_si5 = PREDECLARED_SIGNAL_INTELLIGENCE_POLICIES_V1793122["SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET"]
    p_si6 = PREDECLARED_SIGNAL_INTELLIGENCE_POLICIES_V1793122["SI6_COMBINED_PREDECLARED"]

    assert not p_si0.require_causal_htf_alignment
    assert p_si2.require_causal_htf_alignment and not p_si2.require_true_unicorn_overlap
    assert p_si4.require_true_unicorn_overlap and not p_si4.require_causal_htf_alignment
    assert p_si5.require_protected_structure_sl and p_si5.require_reachable_pre_entry_target
    assert p_si6.require_causal_htf_alignment and p_si6.require_true_unicorn_overlap and p_si6.require_protected_structure_sl


def test_t25_market_hours_vs_exchange_availability_not_conflated():
    """T25: Spot gold market hours are separated from unverified Bitget perpetual service availability."""
    st = classify_venue_market_state_asof_v1793121(
        1_791_072_000_000,  # Weekend timestamp
        {"open_time": 1_791_071_940_000, "close_time": 1_791_072_000_000, "open": 2650.0, "high": 2650.5, "low": 2649.5, "close": 2650.1, "volume": 10.0},
        {"open_time": 1_791_071_880_000, "close_time": 1_791_071_940_000, "open": 2650.0, "high": 2650.5, "low": 2649.5, "close": 2650.0, "volume": 10.0},
    )
    assert st["exchange_service_state"] == "NOT_VERIFIED_FROM_HISTORICAL_CANDLES"


def test_t26_all_5_models_sampled_at_eligible_timestamp():
    """T26: SharedStrategyEvaluator evaluates all 5 canonical production models at any timestamp."""
    evaluator = SharedStrategyEvaluator()
    ctx = _make_shared_context(mode="OFF", policy_id="SI0_OFF_BASELINE")
    models = [
        ModelIdentifier.SMC_M1_REVERSAL.value,
        ModelIdentifier.SMC_M1_CONTINUATION.value,
        ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
        ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
        ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
    ]
    snaps = [evaluator.evaluate_model_best_direction(ctx, m) for m in models]
    assert len(snaps) == 5
    assert {s.model_id for s in snaps} == set(models)


def test_t27_evaluate_both_directions_causally_in_research_mode():
    """T27: SharedStrategyEvaluator.evaluate_model_best_direction evaluates LONG and SHORT causally under RESEARCH_APPLY without hindsight."""
    evaluator = SharedStrategyEvaluator()
    ctx_res = _make_shared_context(mode="RESEARCH_APPLY", policy_id="SI1_MODEL_QUALITY_AND_ARBITRATION")
    snap = evaluator.evaluate_model_best_direction(ctx_res, ModelIdentifier.SMC_M1_REVERSAL.value)
    assert snap.direction in ("LONG", "SHORT")
    assert snap.v1793122_mode == "RESEARCH_APPLY"


def test_t28_model_factor_confidence_derived_only_from_pre_entry_features():
    """T28: Candidate quality score uses only pre-entry causal features and never post-entry outcome labels."""
    r1 = {"opportunity_id": "o1", "model_id": "SMC_M1_REVERSAL", "net_rr": 2.4, "outcome_label": "LOSS"}
    r2 = {"opportunity_id": "o1", "model_id": "SMC_M1_REVERSAL", "net_rr": 2.4, "outcome_label": "WIN"}
    feat = {"htf_alignment_state": "ALIGNED", "unicorn_classification": "NONE", "displacement_valid": True}
    # Changing post-entry outcome_label must have zero effect on pre-entry score tuple
    assert score_candidate_record_pre_entry_v1793122(r1, feature_row=feat) == score_candidate_record_pre_entry_v1793122(r2, feature_row=feat)
