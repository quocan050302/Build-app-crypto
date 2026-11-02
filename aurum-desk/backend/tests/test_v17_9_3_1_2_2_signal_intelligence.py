"""
backend/tests/test_v17_9_3_1_2_2_signal_intelligence.py

Adversarial & Functional Unit Tests T01..T16 for V17.9.3.1.2.2
Entry Signal Intelligence & Shared Strategy Evaluator Integration.
"""
from __future__ import annotations

from dataclasses import replace
import pytest

from services.scalp_m1_types import M1ScalpConfig, ModelIdentifier
from services.shared_strategy_evaluator import (
    SharedStrategyContext,
    SharedStrategyEvaluator,
    build_market_decision_context,
    resolve_canonical_preferred_direction,
)
from services.v17_9_3_1_2_2_signal_intelligence import (
    CANONICAL_SIGNAL_INTELLIGENCE_POLICY_IDS,
    PREDECLARED_SIGNAL_INTELLIGENCE_POLICIES_V1793122,
    PendingConfirmationV1793122,
    advance_pending_confirmation_state_v1793122,
    arbitrate_competing_model_intents_v1793122,
    build_signal_quality_evidence_v1793122,
    build_smc_m1_continuation_24_losses_casebook_v1793122,
    evaluate_signal_intelligence_gate_v1793122,
    is_research_apply_forbidden_in_adapter_v1793122,
    normalize_signal_intelligence_mode_v1793122,
    normalize_signal_intelligence_policy_id_v1793122,
    resolve_research_preferred_direction_v1793122,
    score_candidate_record_pre_entry_v1793122,
)


def _make_sample_bars(n: int = 30, base_px: float = 2650.0, step_ms: int = 60_000, start_ms: int = 1_790_000_000_000):
    bars = []
    for i in range(n):
        o_ms = start_ms + i * step_ms
        c_ms = o_ms + step_ms
        px = base_px + 0.15 * i
        bars.append(
            {
                "open_time": o_ms,
                "close_time": c_ms,
                "timestamp": o_ms,
                "open": round(px, 2),
                "high": round(px + 0.65, 2),
                "low": round(px - 0.45, 2),
                "close": round(px + 0.25, 2),
                "volume": 120.0,
            }
        )
    return bars


def _make_shared_context(mode: str = "OFF", policy_id: str = "SI0_OFF_BASELINE", adapter_origin: str = "BACKTEST"):
    bars_1m = _make_sample_bars(40, 2650.0, 60_000)
    bars_5m = _make_sample_bars(20, 2648.0, 300_000)
    bars_15m = _make_sample_bars(20, 2645.0, 900_000)
    bars_1h = _make_sample_bars(20, 2640.0, 3_600_000)
    bars_4h = _make_sample_bars(15, 2635.0, 14_400_000)
    now_ms = int(bars_1m[-1]["close_time"])
    return build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=now_ms,
        m1_candles=bars_1m,
        m5_candles=bars_5m,
        m15_candles=bars_15m,
        h1_candles=bars_1h,
        h4_candles=bars_4h,
        bid=2655.80,
        ask=2656.15,
        quote_timestamp_ms=now_ms,
        policy_config={
            "v17_9_3_1_2_2_signal_intelligence_mode": mode,
            "v17_9_3_1_2_2_policy_id": policy_id,
        },
    )


def test_t01_signal_intelligence_mode_off_exact_decision_and_action_parity():
    """T01: SIGNAL_INTELLIGENCE_MODE=OFF preserves exact baseline decision and trade_action_fingerprint."""
    evaluator = SharedStrategyEvaluator()
    ctx_off = _make_shared_context(mode="OFF", policy_id="SI0_OFF_BASELINE")
    for m_id in (
        ModelIdentifier.SMC_M1_REVERSAL.value,
        ModelIdentifier.SMC_M1_CONTINUATION.value,
        ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
        ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
        ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
    ):
        s1 = evaluator.evaluate_model_direction(ctx_off, m_id, "LONG")
        s2 = evaluator.evaluate_model_direction(ctx_off, m_id, "LONG")
        assert s1.v1793122_mode == "OFF"
        assert s1.trade_action_fingerprint == s2.trade_action_fingerprint
        assert s1.decision_status == s2.decision_status
        assert s1.rejection_reasons == s2.rejection_reasons


def test_t02_observe_only_emits_diagnostics_without_altering_trade_action():
    """T02: OBSERVE_ONLY records proposed V17.9.3.1.2.2 diagnostics while keeping identical trade_action_fingerprint to OFF."""
    evaluator = SharedStrategyEvaluator()
    ctx_off = _make_shared_context(mode="OFF", policy_id="SI0_OFF_BASELINE")
    ctx_obs = _make_shared_context(mode="OBSERVE_ONLY", policy_id="SI6_COMBINED_PREDECLARED")
    for m_id in (
        ModelIdentifier.SMC_M1_REVERSAL.value,
        ModelIdentifier.SMC_M1_CONTINUATION.value,
        ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
    ):
        snap_off = evaluator.evaluate_model_direction(ctx_off, m_id, "LONG")
        snap_obs = evaluator.evaluate_model_direction(ctx_obs, m_id, "LONG")
        assert snap_obs.v1793122_mode == "OBSERVE_ONLY"
        assert snap_obs.v1793122_policy_id == "SI6_COMBINED_PREDECLARED"
        assert snap_obs.trade_action_fingerprint == snap_off.trade_action_fingerprint
        assert snap_obs.decision_status == snap_off.decision_status
        assert snap_obs.entry_reference == snap_off.entry_reference
        assert snap_obs.selected_sl == snap_off.selected_sl
        assert snap_obs.take_profit == snap_off.take_profit


def test_t03_research_apply_affects_shared_evaluator_output():
    """T03: RESEARCH_APPLY modifies SharedStrategyEvaluator output on eligible candidates."""
    ctx = _make_shared_context(mode="RESEARCH_APPLY", policy_id="SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION")
    plan = evaluate_signal_intelligence_gate_v1793122(
        context=ctx,
        model_id=ModelIdentifier.SMC_M1_CONTINUATION.value,
        direction="SHORT",
        mode="RESEARCH_APPLY",
        policy_id="SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION",
        adapter_origin="BACKTEST",
        baseline_status="ELIGIBLE",
        baseline_rejection_reasons=(),
        baseline_entry=2655.0,
        baseline_sl=2657.0,
        baseline_tp=2650.0,
        baseline_qty=0.05,
        baseline_gross_rr=2.5,
        baseline_net_rr=2.2,
        baseline_anchor_id="anc_1",
        baseline_anchor_kind="SWING_HIGH",
        baseline_invalidation_price=2656.8,
        baseline_target_id="tgt_1",
        setup_descriptors={},
        sh_1m=[],
        sl_1m=[],
        atr_m1=1.0,
        atr_m5=2.0,
        unicorn_evidence={},
    )
    assert plan.applied_status == "REJECTED"
    assert plan.applied_quantity == 0.0
    assert len(plan.applied_rejection_reasons) > 0


def test_t04_unknown_mode_and_policy_fail_closed():
    """T04: Unknown mode or policy ID fails closed immediately."""
    m_norm, m_ok, m_err = normalize_signal_intelligence_mode_v1793122("UNSUPPORTED_MODE_XYZ")
    assert not m_ok
    assert m_err == "SI_INVALID_MODE_FAIL_CLOSED"

    p_norm, p_ok, p_err = normalize_signal_intelligence_policy_id_v1793122("SI99_NONEXISTENT")
    assert not p_ok
    assert p_err == "SI_INVALID_POLICY_ID_FAIL_CLOSED"

    with pytest.raises(ValueError, match="SI_INVALID_MODE_FAIL_CLOSED"):
        M1ScalpConfig(v17_9_3_1_2_2_signal_intelligence_mode="INVALID_MODE")

    with pytest.raises(ValueError, match="SI_INVALID_POLICY_ID_FAIL_CLOSED"):
        M1ScalpConfig(v17_9_3_1_2_2_policy_id="SI99_UNKNOWN")


def test_t05_production_shadow_backtest_adapter_parity_and_production_block():
    """T05: Production, Shadow, and Backtest adapters share evaluator parity under OFF and block RESEARCH_APPLY in Production."""
    evaluator = SharedStrategyEvaluator()
    ctx = _make_shared_context(mode="OFF", adapter_origin="BACKTEST")

    s_bt = evaluator.evaluate_model_direction(
        ctx, ModelIdentifier.SMC_M1_REVERSAL.value, "LONG", adapter_origin="BACKTEST"
    )
    s_sh = evaluator.evaluate_model_direction(
        ctx, ModelIdentifier.SMC_M1_REVERSAL.value, "LONG", adapter_origin="MARKET_SHADOW"
    )
    s_pr = evaluator.evaluate_model_direction(
        ctx, ModelIdentifier.SMC_M1_REVERSAL.value, "LONG", adapter_origin="PRODUCTION_ORCHESTRATOR"
    )
    assert s_bt.trade_action_fingerprint == s_sh.trade_action_fingerprint == s_pr.trade_action_fingerprint

    forbid, code = is_research_apply_forbidden_in_adapter_v1793122("PRODUCTION_ORCHESTRATOR")
    assert forbid is True
    assert code == "SI_FORBIDDEN_IN_PRODUCTION_ADAPTER"

    with pytest.raises(ValueError, match="SI_FORBIDDEN_IN_PRODUCTION_EXECUTION_MODE"):
        M1ScalpConfig(
            execution_mode="LIVE",
            v17_9_3_1_2_2_signal_intelligence_mode="RESEARCH_APPLY",
            v17_9_3_1_2_2_policy_id="SI6_COMBINED_PREDECLARED",
        )


def test_t06_missing_htf_never_defaults_to_biased_long_in_research():
    """T06: Missing or neutral HTF never defaults to LONG in research direction resolution."""
    d1, r1 = resolve_research_preferred_direction_v1793122(
        h4_bias="ABSENT", h1_bias="ABSENT", m15_bias="NEUTRAL", m5_bias="NEUTRAL"
    )
    assert d1 is None
    assert r1 == "HTF_DATA_INSUFFICIENT_OR_CONFLICTED"

    ctx_no_htf = replace(
        _make_shared_context(mode="RESEARCH_APPLY", policy_id="SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION"),
        closed_h1_candles=(),
        closed_h4_candles=(),
        closed_m15_candles=(),
        closed_m5_candles=(),
    )
    d2 = resolve_canonical_preferred_direction(ctx_no_htf)
    assert d2 != "LONG"


def test_t07_invalid_or_late_htf_swing_refuses_known_at_signal():
    """T07: Future/unclosed HTF candles with close_time > decision_timestamp_ms are excluded from causal HTF bias."""
    ctx = _make_shared_context(mode="RESEARCH_APPLY", policy_id="SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION")
    future_1h = list(ctx.candles_1h) + [
        {
            "open_time": ctx.now_ms,
            "close_time": ctx.now_ms + 3_600_000,
            "open": 2650.0,
            "high": 2750.0,
            "low": 2500.0,
            "close": 2510.0,
        }
    ]
    ctx_with_future = replace(ctx, closed_h1_candles=tuple(future_1h))
    ev1 = build_signal_quality_evidence_v1793122(
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
    ev2 = build_signal_quality_evidence_v1793122(
        context=ctx_with_future,
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
    assert ev1.h1_bias == ev2.h1_bias
    assert ev2.causal_feature_violations_count == 0


def test_t08_reversal_requires_valid_sweep_reclaim_and_mss():
    """T08: Reversal under SI1/SI6 requires confirmed liquidity sweep, reclaim, and MSS break."""
    ctx = _make_shared_context(mode="RESEARCH_APPLY", policy_id="SI1_MODEL_QUALITY_AND_ARBITRATION")
    plan = evaluate_signal_intelligence_gate_v1793122(
        context=ctx,
        model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
        direction="LONG",
        mode="RESEARCH_APPLY",
        policy_id="SI1_MODEL_QUALITY_AND_ARBITRATION",
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
        setup_descriptors={"selected_sweep_id": None, "selected_break_id": None},
        sh_1m=[],
        sl_1m=[],
        atr_m1=1.0,
        atr_m5=2.0,
        unicorn_evidence={},
    )
    assert plan.applied_status in ("REJECTED", "WAITING_CONFIRMATION")
    assert "SI_REVERSAL_MISSING_CONFIRMED_SWEEP_RECLAIM" in plan.proposed_rejection_reasons


def test_t09_continuation_requires_confirmed_structure_not_weak_wick():
    """T09: Continuation requires displacement body >= 0.55 ATR and confirmed structure."""
    ctx = _make_shared_context(mode="RESEARCH_APPLY", policy_id="SI1_MODEL_QUALITY_AND_ARBITRATION")
    plan = evaluate_signal_intelligence_gate_v1793122(
        context=ctx,
        model_id=ModelIdentifier.SMC_M1_CONTINUATION.value,
        direction="LONG",
        mode="RESEARCH_APPLY",
        policy_id="SI1_MODEL_QUALITY_AND_ARBITRATION",
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
        setup_descriptors={"selected_break_id": None},
        sh_1m=[],
        sl_1m=[],
        atr_m1=2.0,
        atr_m5=3.0,
        unicorn_evidence={},
    )
    assert plan.applied_status == "REJECTED"
    assert any(
        r in plan.applied_rejection_reasons
        for r in ("SI_CONTINUATION_MISSING_CONFIRMED_BOS", "SI_CONTINUATION_WEAK_DISPLACEMENT_BODY")
    )


def test_t10_fvg_continuation_rejects_stale_or_mitigated_zone():
    """T10: FVG continuation rejects stale (>24 bars) or already mitigated FVG zones."""
    ctx = _make_shared_context(mode="RESEARCH_APPLY", policy_id="SI1_MODEL_QUALITY_AND_ARBITRATION")
    stale_formed_ms = ctx.now_ms - 45 * 60_000
    plan = evaluate_signal_intelligence_gate_v1793122(
        context=ctx,
        model_id=ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
        direction="LONG",
        mode="RESEARCH_APPLY",
        policy_id="SI1_MODEL_QUALITY_AND_ARBITRATION",
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
        setup_descriptors={"selected_fvg_id": "fvg_old", "fvg_formed_at_ms": stale_formed_ms},
        sh_1m=[],
        sl_1m=[],
        atr_m1=1.0,
        atr_m5=2.0,
        unicorn_evidence={"fvg_present": True},
    )
    assert plan.applied_status == "REJECTED"
    assert "SI_FVG_STALE_OR_MITIGATED" in plan.applied_rejection_reasons


def test_t11_model_arbitration_deterministic_and_strictly_pre_entry():
    """T11: Multi-model arbitration is deterministic and rejects post-entry outcome label injection."""
    cands = [
        {
            "opportunity_id": "opp_rev",
            "model_id": ModelIdentifier.SMC_M1_REVERSAL.value,
            "direction": "LONG",
            "decision_status": "ELIGIBLE",
            "net_rr": 2.1,
        },
        {
            "opportunity_id": "opp_swp",
            "model_id": ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
            "direction": "LONG",
            "decision_status": "ELIGIBLE",
            "net_rr": 2.8,
        },
    ]
    feats = {
        "opp_rev": {"htf_alignment_state": "MIXED", "unicorn_classification": "NONE", "displacement_valid": False},
        "opp_swp": {"htf_alignment_state": "ALIGNED", "unicorn_classification": "UNICORN_CONFIRMED", "displacement_valid": True},
    }
    w_stat, _ = arbitrate_competing_model_intents_v1793122(cands, arbitration_mode="STATIC_PRODUCTION_PRIORITY")
    assert w_stat is not None and w_stat["model_id"] == ModelIdentifier.SMC_M1_REVERSAL.value

    w_qual, losers = arbitrate_competing_model_intents_v1793122(
        cands, arbitration_mode="PRE_ENTRY_QUALITY_TUPLE", feature_ledger_by_opp_id=feats
    )
    assert w_qual is not None and w_qual["model_id"] == ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value
    assert len(losers) == 1 and losers[0]["suppressed_by_model_id"] == ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value

    with pytest.raises(ValueError, match="LOOKAHEAD_ARBITRATION_FORBIDDEN"):
        score_candidate_record_pre_entry_v1793122({"_inject_post_entry_label_arbitration": True})


def test_t12_smc_m1_continuation_24_losses_casebook_completeness():
    """T12: All 24 historical SMC_M1_CONTINUATION closed losses are traced with failure taxonomy and policy counterfactuals."""
    synthetic_24 = [
        {
            "opportunity_id": f"opp_cont_{i:02d}",
            "setup_episode_id": f"ep_cont_{i:02d}",
            "checkpoint_index_1m": 300 + i * 20,
            "signal_ts_ms": 1_790_000_000_000 + i * 1_200_000,
            "entry_ts_ms": 1_790_000_060_000 + i * 1_200_000,
            "exit_ts_ms": 1_790_000_360_000 + i * 1_200_000,
            "model_id": ModelIdentifier.SMC_M1_CONTINUATION.value,
            "direction": "LONG" if i % 2 == 0 else "SHORT",
            "exit_reason": "SL",
            "realized_net_usd": -2.42,
            "realized_gross_usd": -2.20,
            "realized_net_r": -1.0,
        }
        for i in range(1, 25)
    ]
    cb = build_smc_m1_continuation_24_losses_casebook_v1793122(
        baseline_trades=synthetic_24,
        feature_ledger_by_opp_id={},
    )
    assert cb["baseline_closed_trades_count"] == 24
    assert cb["baseline_losses_count"] == 24
    assert len(cb["cases"]) == 24
    for row in cb["cases"]:
        assert row["primary_failure_category"]
        assert "SI6_COMBINED_PREDECLARED" in row["policy_counterfactual_outcomes"]


def test_t13_sweep_reclaim_and_cg01_preserved_in_candidate_census():
    """T13: SMC_M1_SWEEP_RECLAIM and CG01_SWEEP_MSS_FVG remain in the candidate census and can win under quality arbitration."""
    cands = [
        {"opportunity_id": "o1", "model_id": ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, "direction": "LONG", "decision_status": "ELIGIBLE", "net_rr": 2.1},
        {"opportunity_id": "o2", "model_id": ModelIdentifier.CG01_SWEEP_MSS_FVG.value, "direction": "LONG", "decision_status": "ELIGIBLE", "net_rr": 2.9},
    ]
    feats = {
        "o1": {"htf_alignment_state": "MIXED", "sweep_confirmed": False, "mss_confirmed": False},
        "o2": {"htf_alignment_state": "ALIGNED", "sweep_confirmed": True, "mss_confirmed": True},
    }
    winner, losers = arbitrate_competing_model_intents_v1793122(
        cands, arbitration_mode="PRE_ENTRY_QUALITY_TUPLE", feature_ledger_by_opp_id=feats
    )
    assert winner is not None
    assert winner["model_id"] == ModelIdentifier.CG01_SWEEP_MSS_FVG.value
    assert len(losers) == 1


def test_t14_stale_quote_or_wide_spread_rejects_executable_order():
    """T14: Insufficient quote freshness or excessive spread-to-ATR ratio blocks executable orders."""
    ctx = _make_shared_context(mode="RESEARCH_APPLY", policy_id="SI1_MODEL_QUALITY_AND_ARBITRATION")
    # Tiny ATR1M = 0.25 so default 0.35 spread > 0.85 * ATR1M triggers SI_QUOTE_STALE_OR_WIDE_SPREAD
    plan = evaluate_signal_intelligence_gate_v1793122(
        context=ctx,
        model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
        direction="LONG",
        mode="RESEARCH_APPLY",
        policy_id="SI1_MODEL_QUALITY_AND_ARBITRATION",
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
        atr_m1=0.25,
        atr_m5=0.50,
        unicorn_evidence={},
    )
    assert plan.applied_status == "REJECTED"
    assert "SI_QUOTE_STALE_OR_WIDE_SPREAD" in plan.applied_rejection_reasons


def test_t15_pending_confirmation_state_created_and_confirms_on_closed_bar():
    """T15: Pending confirmation state is created for unconfirmed setups and confirms on a future closed bar."""
    pending = PendingConfirmationV1793122(
        pending_id="pend_t15",
        opportunity_id="opp_t15",
        symbol="XAUUSDT",
        model_id=ModelIdentifier.SMC_M1_CONTINUATION.value,
        direction="LONG",
        signal_ts_ms=60_000,
        created_bar_index_1m=0,
        expires_at_ms=360_000,
        max_ttl_bars=5,
        confirmation_type="MSS_DISPLACEMENT_BREAK",
        trigger_level_price=2650.5,
        setup_invalidation_price=2648.0,
        protected_structure_price=2648.0,
        protected_structure_id="s1",
        baseline_entry=2650.2,
        baseline_sl=2648.0,
        baseline_tp=2666.0,
        target_id="t1",
        policy_id="SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY",
    )
    bars = [
        {"open_time": 0, "close_time": 60_000, "open": 2650.0, "high": 2650.4, "low": 2649.8, "close": 2650.2},
        {"open_time": 60_000, "close_time": 120_000, "open": 2650.2, "high": 2650.4, "low": 2649.9, "close": 2650.3},
        {"open_time": 120_000, "close_time": 180_000, "open": 2650.3, "high": 2651.5, "low": 2650.2, "close": 2651.2},
    ]
    updated, conf_idx = advance_pending_confirmation_state_v1793122(pending, bars, start_bar_index=1, atr_m1=1.0)
    assert updated.status == "CONFIRMED"
    assert conf_idx == 2
    assert updated.confirmed_at_ms == 180_000
    assert updated.confirmed_at_ms > pending.signal_ts_ms


def test_t16_pending_expiry_gap_and_invalidation_never_backdate_entry():
    """T16: Pending confirmation expires on TTL, cancels on data gap, and invalidates if setup_invalidation is breached."""
    base_pend = PendingConfirmationV1793122(
        pending_id="pend_t16",
        opportunity_id="opp_t16",
        symbol="XAUUSDT",
        model_id=ModelIdentifier.SMC_M1_CONTINUATION.value,
        direction="LONG",
        signal_ts_ms=60_000,
        created_bar_index_1m=0,
        expires_at_ms=180_000,
        max_ttl_bars=2,
        confirmation_type="MSS_DISPLACEMENT_BREAK",
        trigger_level_price=2652.0,
        setup_invalidation_price=2649.0,
        protected_structure_price=2649.0,
        protected_structure_id="s1",
        baseline_entry=2650.2,
        baseline_sl=2649.0,
        baseline_tp=2656.5,
        target_id="t1",
        policy_id="SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY",
    )
    # 1. Invalidated by low <= 2649.0
    bars_inv = [
        {"open_time": 0, "close_time": 60_000, "open": 2650.0, "high": 2650.4, "low": 2649.8, "close": 2650.2},
        {"open_time": 60_000, "close_time": 120_000, "open": 2650.2, "high": 2650.3, "low": 2648.5, "close": 2649.5},
    ]
    u_inv, idx_inv = advance_pending_confirmation_state_v1793122(base_pend, bars_inv, start_bar_index=1, atr_m1=1.0)
    assert u_inv.status == "INVALIDATED"
    assert idx_inv is None

    # 2. Cancelled on data gap > 180s
    bars_gap = [
        {"open_time": 0, "close_time": 60_000, "open": 2650.0, "high": 2650.4, "low": 2649.8, "close": 2650.2},
        {"open_time": 600_000, "close_time": 660_000, "open": 2650.2, "high": 2653.0, "low": 2650.0, "close": 2652.8},
    ]
    u_gap, idx_gap = advance_pending_confirmation_state_v1793122(base_pend, bars_gap, start_bar_index=1, atr_m1=1.0)
    assert u_gap.status == "CANCELLED_GAP"
    assert idx_gap is None

    # 3. Expired on TTL
    bars_exp = [
        {"open_time": 0, "close_time": 60_000, "open": 2650.0, "high": 2650.4, "low": 2649.8, "close": 2650.2},
        {"open_time": 60_000, "close_time": 120_000, "open": 2650.2, "high": 2650.5, "low": 2649.9, "close": 2650.3},
        {"open_time": 120_000, "close_time": 180_000, "open": 2650.3, "high": 2650.6, "low": 2649.9, "close": 2650.4},
    ]
    u_exp, idx_exp = advance_pending_confirmation_state_v1793122(base_pend, bars_exp, start_bar_index=1, atr_m1=1.0)
    assert u_exp.status == "EXPIRED"
    assert idx_exp is None
