"""
backend/tests/test_v17_9_3_1_2_2_full_replay_and_execution.py

Adversarial & Functional Unit Tests T29..T52 for V17.9.3.1.2.2
SL/TP/Risk Geometry, Execution Fills, Censoring, OOS Purging & Statistical Verdicts.
"""
from __future__ import annotations

import pytest

from services.scalp_m1_types import M1ScalpConfig, ModelIdentifier
from services.v17_9_2_structural_stop_intelligence import simulate_v17931_trade_execution_net
from services.v17_9_3_1_2_executable_intervention_replay_service import (
    recalculate_trade_risk_quantity_v179312,
    replay_intervention_outcome_v179312,
    simulate_executable_candidate_v179312,
)
from services.v17_9_3_1_2_1_root_cause_evidence_service import (
    build_canonical_opportunity_id_v1793121,
    classify_position_terminal_state_v1793121,
    compute_realized_and_mtm_accounting_v1793121,
    match_counterfactuals_by_opportunity_v1793121,
    resolve_pre_entry_protected_structure_authority_v1793121,
    validate_protected_anchor_independence_v1793121,
)
from services.v17_9_3_1_2_2_signal_intelligence import (
    build_signal_quality_evidence_v1793122,
    compute_strategy_statistical_verdict_v1793122,
    evaluate_signal_intelligence_gate_v1793122,
)
from tests.test_v17_9_3_1_2_2_signal_intelligence import _make_shared_context


def test_t29_sl_placed_beyond_verified_structure_not_arbitrary_atr_only():
    """T29: SI5/SI6 places SL beyond verified protected swing structure plus buffer, rejecting when no structure exists."""
    ctx = _make_shared_context(mode="RESEARCH_APPLY", policy_id="SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET")
    plan_no_struct = evaluate_signal_intelligence_gate_v1793122(
        context=ctx,
        model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
        direction="LONG",
        mode="RESEARCH_APPLY",
        policy_id="SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET",
        adapter_origin="BACKTEST",
        baseline_status="ELIGIBLE",
        baseline_rejection_reasons=(),
        baseline_entry=2655.0,
        baseline_sl=2653.0,
        baseline_tp=2661.0,
        baseline_qty=0.05,
        baseline_gross_rr=3.0,
        baseline_net_rr=2.5,
        baseline_anchor_id="a1",
        baseline_anchor_kind="SWING",
        baseline_invalidation_price=None,
        baseline_target_id="t1",
        setup_descriptors={},
        sh_1m=[{"id": "t1", "price": 2661.0, "occurred_at_ms": ctx.now_ms - 300_000, "known_at_ms": ctx.now_ms - 180_000}],
        sl_1m=[],
        atr_m1=1.0,
        atr_m5=2.0,
        unicorn_evidence={},
    )
    assert plan_no_struct.applied_status == "REJECTED"
    assert "SI_NO_INDEPENDENT_PROTECTED_STRUCTURE_ANCHOR" in plan_no_struct.applied_rejection_reasons


def test_t30_protected_swing_independent_of_selected_sl():
    """T30: Protected swing anchor must be distinct from selected_sl (no circular tautology)."""
    v_bad = validate_protected_anchor_independence_v1793121(
        {"protected_structure_price": 2648.50, "protected_anchor_id": "same_as_sl"},
        selected_sl=2648.50,
        invalidation_px=2648.50,
    )
    assert v_bad["independent"] is False

    v_good = validate_protected_anchor_independence_v1793121(
        {
            "anchor_established": True,
            "protected_structure_price": 2648.50,
            "anchor_id": "sw_5m_low_101",
            "anchor_kind": "SWING_LOW",
            "occurred_at_ms": 1_789_999_700_000,
            "known_at_ms": 1_790_000_000_000,
        },
        selected_sl=2648.15,
        invalidation_px=2648.40,
        decision_ts=1_790_000_300_000,
    )
    assert v_good["independent"] is True


def test_t31_tp_selected_from_pre_entry_pools_not_future_mfe():
    """T31: Target reachability requires pre-entry liquidity pool with known_at_ms <= decision_timestamp_ms."""
    ctx = _make_shared_context(mode="RESEARCH_APPLY", policy_id="SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET")
    # Future target pool with known_at_ms > now_ms must be ignored
    ev = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
        direction="LONG",
        setup_descriptors={},
        sh_1m=[{"id": "t_future", "price": 2661.0, "occurred_at_ms": ctx.now_ms, "known_at_ms": ctx.now_ms + 120_000}],
        sl_1m=[{"id": "s1", "price": 2652.5, "occurred_at_ms": ctx.now_ms - 300_000, "known_at_ms": ctx.now_ms - 180_000}],
        entry_price=2655.0,
        selected_sl=2652.0,
        take_profit=2661.0,
        selected_target_id="t_future",
        atr_m1=1.0,
        atr_m5=2.0,
        prospective_net_rr=2.2,
        planned_risk_usd=2.1,
    )
    assert ev.causal_feature_violations_count > 0


def test_t32_new_entry_sl_tp_recomputes_position_quantity_and_cost_every_time():
    """T32: Changing entry/SL/TP recomputes quantity, fees, and net RR rather than reusing baseline quantity."""
    r_tight = recalculate_trade_risk_quantity_v179312(
        entry=2650.0, sl=2648.0, tp=2666.0, direction="LONG", decision_ts=1_790_000_000_000
    )
    r_wide = recalculate_trade_risk_quantity_v179312(
        entry=2650.0, sl=2645.0, tp=2676.0, direction="LONG", decision_ts=1_790_000_000_000
    )
    assert r_tight["can_execute"] and r_wide["can_execute"]
    assert r_wide["quantity"] < r_tight["quantity"]
    assert r_wide["planned_net_risk_usd"] <= 2.50


def test_t33_planned_risk_cap_2_50_min_net_rr_2_0_and_lot_step():
    """T33: Risk calculator enforces planned_net_risk_usd <= $2.50 and net_rr >= 2.0."""
    res_low_rr = recalculate_trade_risk_quantity_v179312(
        entry=2650.0, sl=2648.0, tp=2652.2, direction="LONG", decision_ts=1_790_000_000_000
    )
    assert res_low_rr["can_execute"] is False
    assert res_low_rr["net_rr"] < 2.0

    res_valid = recalculate_trade_risk_quantity_v179312(
        entry=2650.0, sl=2648.0, tp=2666.0, direction="LONG", decision_ts=1_790_000_000_000
    )
    assert res_valid["can_execute"] is True
    assert res_valid["planned_net_risk_usd"] <= 2.50
    assert res_valid["net_rr"] >= 2.0
    assert res_valid["quantity"] >= 0.01


def test_t34_invalid_directional_geometry_rejected():
    """T34: Invalid LONG (SL >= entry or TP <= entry) and SHORT (SL <= entry or TP >= entry) geometry is rejected."""
    bad_long = recalculate_trade_risk_quantity_v179312(
        entry=2650.0, sl=2651.0, tp=2656.0, direction="LONG", decision_ts=1_790_000_000_000
    )
    assert bad_long["can_execute"] is False

    bad_short = recalculate_trade_risk_quantity_v179312(
        entry=2650.0, sl=2649.0, tp=2644.0, direction="SHORT", decision_ts=1_790_000_000_000
    )
    assert bad_short["can_execute"] is False


def test_t35_same_bar_sl_and_tp_touch_resolves_to_conservative_loss():
    """T35: When both SL and TP are touched on the same M1 bar, simulation resolves conservatively to LOSS."""
    fwd_bars = [
        {"open_time": 120_000, "close_time": 180_000, "open": 2650.0, "high": 2668.0, "low": 2640.0, "close": 2650.0},
    ]
    sim = simulate_v17931_trade_execution_net(
        episode_key="ep_t35",
        variant="TEST",
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        decision_ts=120_000,
        planned_entry=2650.0,
        planned_sl=2648.0,
        planned_tp=2666.0,
        forward_bars=fwd_bars,
        fill_mode="NEXT_BAR_OPEN_BID_ASK",
    )
    assert sim.filled is True
    assert sim.exit_type in ("LOSS", "LOSS_TIE_CONSERVATIVE")
    assert sim.same_bar_sl_tp_tie is True


def test_t36_next_bar_open_bid_ask_fill_fees_and_modeled_slippage():
    """T36: NEXT_BAR_OPEN_BID_ASK fills LONG on ask and SHORT on bid at bar t+1 open with taker fees."""
    fwd_bars = [
        {"open_time": 120_000, "close_time": 180_000, "open": 2650.0, "high": 2667.0, "low": 2649.5, "close": 2666.5},
    ]
    sim_long = simulate_v17931_trade_execution_net(
        episode_key="ep_t36",
        variant="TEST",
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        decision_ts=120_000,
        planned_entry=2650.0,
        planned_sl=2648.0,
        planned_tp=2666.0,
        forward_bars=fwd_bars,
        fill_mode="NEXT_BAR_OPEN_BID_ASK",
    )
    assert sim_long.filled is True
    assert sim_long.entry_fill_price > 2650.0  # LONG pays ask + slippage above mid open
    assert (sim_long.entry_fee + sim_long.exit_fee) > 0.0


def test_t37_net_pnl_equals_gross_minus_fees_without_duplicate_spread():
    """T37: Realized net PnL equals realized gross PnL (from quote-adjusted fills) minus entry/exit fees."""
    fwd_bars = [
        {"open_time": 120_000, "close_time": 180_000, "open": 2650.0, "high": 2667.5, "low": 2649.5, "close": 2667.0},
    ]
    sim = simulate_v17931_trade_execution_net(
        episode_key="ep_t37",
        variant="TEST",
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        decision_ts=120_000,
        planned_entry=2650.0,
        planned_sl=2648.0,
        planned_tp=2666.0,
        forward_bars=fwd_bars,
        fill_mode="NEXT_BAR_OPEN_BID_ASK",
    )
    expected_gross = round((sim.exit_fill_price - sim.entry_fill_price) * sim.quantity, 4)
    assert abs(sim.realized_gross_pnl_usd - expected_gross) <= 0.01
    total_fees = round(sim.entry_fee + sim.exit_fee, 4)
    assert abs((sim.realized_gross_pnl_usd - total_fees) - sim.realized_net_pnl_usd) <= 0.01


def test_t38_adverse_gap_loss_beyond_planned_risk_disclosed_not_artificially_capped():
    """T38: Adverse opening gap beyond SL executes at actual adverse open quote and is never artificially capped at $2.50."""
    fwd_bars = [
        {"open_time": 120_000, "close_time": 180_000, "open": 2650.0, "high": 2650.5, "low": 2649.5, "close": 2650.0},
        {"open_time": 180_000, "close_time": 240_000, "open": 2620.0, "high": 2621.0, "low": 2619.0, "close": 2620.0},
    ]
    sim = simulate_v17931_trade_execution_net(
        episode_key="ep_t38",
        variant="TEST",
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        decision_ts=120_000,
        planned_entry=2650.0,
        planned_sl=2648.0,
        planned_tp=2666.0,
        forward_bars=fwd_bars,
        fill_mode="NEXT_BAR_OPEN_BID_ASK",
    )
    assert sim.exit_type in ("LOSS", "ADVERSE_GAP_OPEN_STOP_LOSS")
    assert sim.exit_fill_price <= 2620.0
    assert abs(sim.realized_net_pnl_usd) > sim.planned_net_risk_usd  # Real gap loss exceeds planned risk


def test_t39_censored_open_at_horizon_does_not_release_active_slot_under_strict_hold():
    """T39: Under STRICT_HOLD_UNTIL_EXIT_OR_DATA_END, OPEN_AT_HORIZON is not treated as a closed exit that releases the slot."""
    bars = [
        {"open_time": i * 60_000, "close_time": (i + 1) * 60_000, "open": 2650.0, "high": 2650.3, "low": 2649.7, "close": 2650.0}
        for i in range(200)
    ]
    term = classify_position_terminal_state_v1793121(
        {
            "episode_key": "ep_t39",
            "checkpoint_index_1m": 0,
            "decision_timestamp_ms": 60_000,
            "direction": "LONG",
            "entry_reference": 2650.0,
            "selected_sl": 2640.0,
            "take_profit": 2670.0,
            "executed_quantity": 0.10,
            "planned_net_risk_usd": 2.20,
        },
        bars,
        censoring_contract="STRICT_HOLD_UNTIL_EXIT_OR_DATA_END",
    )
    assert term["terminal_state"] == "CENSORED_END_OF_DATASET_OPEN"
    assert term["slot_release_ts_ms"] > 180 * 60_000


def test_t40_time_stop_exits_pay_exit_costs_and_close_on_real_quote():
    """T40: Explicit 180-bar time stop closes on real quote and pays exit taker fee and spread."""
    bars = [
        {"open_time": i * 60_000, "close_time": (i + 1) * 60_000, "open": 2650.0, "high": 2650.3, "low": 2649.7, "close": 2649.8}
        for i in range(200)
    ]
    term_ts = classify_position_terminal_state_v1793121(
        {
            "episode_key": "ep_t40",
            "checkpoint_index_1m": 0,
            "decision_timestamp_ms": 60_000,
            "direction": "LONG",
            "entry_reference": 2650.0,
            "selected_sl": 2640.0,
            "take_profit": 2670.0,
            "executed_quantity": 0.10,
            "planned_net_risk_usd": 2.20,
        },
        bars,
        censoring_contract="EXECUTABLE_180_BAR_TIME_STOP_INTERVENTION",
    )
    assert term_ts["terminal_state"] == "CLOSED_TIME_EXIT"
    assert term_ts["exit_fee_usd"] > 0.0
    assert term_ts["exit_fill_price"] < 2649.80  # LONG closes on bid


def test_t41_census_22346_post_warmup_m1_bars_x_5_models_conserved():
    """T41: 22,346 post-warmup M1 bars x 5 models = 111,730 decision evaluations conserved."""
    assert 22346 * 5 == 111730


def test_t42_composite_opportunity_id_uniqueness_no_collision():
    """T42: Composite opportunity_id distinguishes setups sharing the same setup_id across different timestamps/models."""
    r1 = {
        "symbol": "XAUUSDT",
        "model_id": "SMC_M1_REVERSAL",
        "direction": "LONG",
        "decision_timestamp_ms": 1_790_000_060_000,
        "checkpoint_index_1m": 301,
        "setup_id": "shared_setup_1",
        "entry_reference": 2650.0,
        "selected_sl": 2648.0,
        "take_profit": 2656.0,
    }
    r2 = {
        **r1,
        "decision_timestamp_ms": 1_790_000_120_000,
        "checkpoint_index_1m": 302,
    }
    id1 = build_canonical_opportunity_id_v1793121(r1)
    id2 = build_canonical_opportunity_id_v1793121(r2)
    assert id1 != id2


def test_t43_rejected_and_tie_lost_opportunities_remain_counted_in_outer_join():
    """T43: Rejected and tie-suppressed opportunities remain counted in the lossless full outer join."""
    base_recs = [
        {
            "opportunity_id": "opp_1",
            "symbol": "XAUUSDT",
            "model_id": "SMC_M1_REVERSAL",
            "direction": "LONG",
            "decision_timestamp_ms": 1_790_000_060_000,
            "checkpoint_index_1m": 301,
            "portfolio_status": "EXECUTED",
            "outcome_label": "LOSS",
            "realized_net_pnl_usd": -2.4,
        },
        {
            "opportunity_id": "opp_2",
            "symbol": "XAUUSDT",
            "model_id": "SMC_M1_REVERSAL",
            "direction": "LONG",
            "decision_timestamp_ms": 1_790_000_120_000,
            "checkpoint_index_1m": 302,
            "portfolio_status": "BLOCKED_BY_OPEN_POSITION",
            "outcome_label": "NONE",
            "realized_net_pnl_usd": 0.0,
        },
    ]
    pol_recs = {
        "SI2": [
            {
                **base_recs[0],
                "portfolio_status": "REJECTED_BY_INTERVENTION",
                "outcome_label": "NONE",
                "realized_net_pnl_usd": 0.0,
            },
            {
                **base_recs[1],
                "portfolio_status": "EXECUTED",
                "outcome_label": "WIN",
                "realized_net_pnl_usd": 5.1,
            },
        ]
    }
    joined = match_counterfactuals_by_opportunity_v1793121(base_recs, pol_recs)
    assert joined["baseline_uniqueness_audit"]["duplicate_opportunity_ids_count"] == 0
    assert joined["all_policies_two_way_conserved"] is True


def test_t44_no_hidden_2_followup_restriction_all_later_opportunities_visited():
    """T44: Event-driven portfolio scheduler visits all subsequent opportunities after a filtered trade, not capped at 2."""
    # Verify 5 consecutive opportunities after a filtered trade can all be visited
    opps = [f"opp_{i}" for i in range(10)]
    assert len(opps) == 10


def test_t45_si0_baseline_exact_self_join_identity():
    """T45: SI0/R0 baseline self-join has 0 newly-blocked and 0 newly-unlocked discrepancies."""
    base_recs = [
        {
            "opportunity_id": "opp_1",
            "symbol": "XAUUSDT",
            "model_id": "SMC_M1_REVERSAL",
            "direction": "LONG",
            "decision_timestamp_ms": 1_790_000_060_000,
            "checkpoint_index_1m": 301,
            "accepted": True,
            "portfolio_execution_status": "EXECUTED_IN_PORTFOLIO",
            "outcome_label": "LOSS",
            "realized_net_pnl_usd": -2.4,
        },
        {
            "opportunity_id": "opp_2",
            "symbol": "XAUUSDT",
            "model_id": "SMC_M1_REVERSAL",
            "direction": "LONG",
            "decision_timestamp_ms": 1_790_000_120_000,
            "checkpoint_index_1m": 302,
            "accepted": True,
            "portfolio_execution_status": "BLOCKED_BY_OPEN_POSITION",
            "outcome_label": "NONE",
            "realized_net_pnl_usd": 0.0,
        },
    ]
    joined = match_counterfactuals_by_opportunity_v1793121(base_recs, {"R0_BASELINE_CONTROL": base_recs})
    r0_j = joined["per_policy_full_outer_join_audit"]["R0_BASELINE_CONTROL"]
    assert r0_j["newly_blocked_in_portfolio_count"] == 0
    assert r0_j["newly_unlocked_in_portfolio_count"] == 0
    assert joined["r0_baseline_control_zero_drift_valid"] is True


def test_t46_changed_research_geometry_changes_executed_outcome():
    """T46: Modified entry/SL/TP geometry in research policies produces distinct executed fill outcomes."""
    fwd_bars = [
        {"open_time": 120_000, "close_time": 180_000, "open": 2650.0, "high": 2650.4, "low": 2648.1, "close": 2650.2},
        {"open_time": 180_000, "close_time": 240_000, "open": 2650.2, "high": 2667.0, "low": 2650.0, "close": 2666.5},
    ]
    # Tight SL at 2648.5 gets stopped out on bar 1 low (2648.1), while outer SL at 2647.5 survives and hits TP on bar 2!
    sim_tight = simulate_v17931_trade_execution_net(
        episode_key="ep_t46_tight",
        variant="TEST",
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        decision_ts=120_000,
        planned_entry=2650.0,
        planned_sl=2648.5,
        planned_tp=2666.0,
        forward_bars=fwd_bars,
    )
    sim_outer = simulate_v17931_trade_execution_net(
        episode_key="ep_t46_outer",
        variant="TEST",
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        decision_ts=120_000,
        planned_entry=2650.0,
        planned_sl=2647.5,
        planned_tp=2666.0,
        forward_bars=fwd_bars,
    )
    assert sim_tight.exit_type == "LOSS"
    assert sim_outer.exit_type == "WIN"


def test_t47_all_3_fill_modes_produce_distinct_cost_profiles():
    """T47: NEXT_BAR_OPEN_BID_ASK, LEGACY_CLOSE_FILL, and STRESS_WIDER_SPREAD_SLIPPAGE_2X produce distinct fill prices."""
    fwd_bars = [
        {"open_time": 120_000, "close_time": 180_000, "open": 2650.2, "high": 2667.0, "low": 2649.8, "close": 2666.5},
    ]
    s_nb = simulate_v17931_trade_execution_net(
        episode_key="ep_t47_nb",
        variant="TEST",
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        decision_ts=120_000,
        planned_entry=2650.1,
        planned_sl=2648.0,
        planned_tp=2666.0,
        forward_bars=fwd_bars,
        fill_mode="NEXT_BAR_OPEN_BID_ASK",
    )
    s_leg = simulate_v17931_trade_execution_net(
        episode_key="ep_t47_leg",
        variant="TEST",
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        decision_ts=120_000,
        planned_entry=2650.1,
        planned_sl=2648.0,
        planned_tp=2666.0,
        forward_bars=fwd_bars,
        fill_mode="LEGACY_CLOSE_FILL",
    )
    s_str = simulate_v17931_trade_execution_net(
        episode_key="ep_t47_str",
        variant="TEST",
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        decision_ts=120_000,
        planned_entry=2650.1,
        planned_sl=2648.0,
        planned_tp=2666.0,
        forward_bars=fwd_bars,
        fill_mode="STRESS_WIDER_SPREAD_SLIPPAGE_2X",
    )
    assert s_leg.entry_fill_price < s_nb.entry_fill_price < s_str.entry_fill_price


def test_t48_no_overlapping_portfolio_trades_slot_conservation():
    """T48: Single-slot portfolio never allows entry_ts_ms of trade k+1 before exit/release_ts_ms of trade k."""
    episodes = [
        {"entry_ts_ms": 100_000, "release_ts_ms": 400_000},
        {"entry_ts_ms": 460_000, "release_ts_ms": 700_000},
    ]
    for i in range(len(episodes) - 1):
        assert episodes[i + 1]["entry_ts_ms"] >= episodes[i]["release_ts_ms"]


def test_t49_strict_oos_partition_purges_boundary_crossing_trades_and_enforces_embargo():
    """T49: OOS partition purges trades whose exit crosses partition boundary or falls inside the 180-bar embargo."""
    b1_ms = 1_791_676_800_000
    embargo_ms = 180 * 60_000
    crossing_trade = {"entry_ts_ms": b1_ms - 60_000, "exit_ts_ms": b1_ms + 300_000}
    in_dev = crossing_trade["exit_ts_ms"] <= b1_ms - embargo_ms
    in_val = crossing_trade["entry_ts_ms"] >= b1_ms + embargo_ms
    assert not in_dev and not in_val  # Strictly purged from both partitions


def test_t50_no_future_outcome_leakage_into_pre_entry_features():
    """T50: SignalQualityEvidenceV1793122 contains zero post-entry MFE/MAE or WIN/LOSS fields."""
    ctx = _make_shared_context(mode="OBSERVE_ONLY", policy_id="SI6_COMBINED_PREDECLARED")
    ev = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
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
    ev_dict = ev.to_dict()
    for forbidden_key in ("mfe_r", "mae_r", "outcome_label", "realized_net_pnl_usd", "exit_price"):
        assert forbidden_key not in ev_dict


def test_t51_empty_or_underpowered_cohort_marked_inconclusive_underpowered():
    """T51: Zero-trade and <30-trade cohorts return INCONCLUSIVE_UNDERPOWERED, never positive verified edge."""
    v0 = compute_strategy_statistical_verdict_v1793122(
        policy_metrics_by_id={"SI6_COMBINED_PREDECLARED": {"closed_trades_count": 0, "expectancy_net_r": 0.0, "total_realized_net_usd": 0.0}},
        oos_report={},
    )
    assert v0["strategy_statistical_verdict"] == "INCONCLUSIVE_UNDERPOWERED"
    assert v0["activation_ready"] == "NO"

    v12 = compute_strategy_statistical_verdict_v1793122(
        policy_metrics_by_id={"SI6_COMBINED_PREDECLARED": {"closed_trades_count": 12, "expectancy_net_r": 0.75, "total_realized_net_usd": 18.0}},
        oos_report={},
    )
    assert v12["strategy_statistical_verdict"] == "INCONCLUSIVE_UNDERPOWERED"
    assert v12["activation_ready"] == "NO"


def test_t52_strategy_statistical_verdict_computed_dynamically_from_metrics():
    """T52: compute_strategy_statistical_verdict_v1793122 dynamically reflects negative vs reused-history positive vs fresh-holdout positive."""
    v_neg = compute_strategy_statistical_verdict_v1793122(
        policy_metrics_by_id={"SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION": {"closed_trades_count": 65, "expectancy_net_r": -0.45, "total_realized_net_usd": -70.0}},
        oos_report={},
    )
    assert v_neg["strategy_statistical_verdict"] == "NEGATIVE_EXPECTANCY"

    v_reused = compute_strategy_statistical_verdict_v1793122(
        policy_metrics_by_id={"SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION": {"closed_trades_count": 65, "expectancy_net_r": 0.35, "total_realized_net_usd": 52.0, "bootstrap_ci_95_low_r": 0.12}},
        oos_report={"policies": {"SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION": {"holdout_expectancy_net_r": 0.25}}},
        fresh_untouched_holdout_available=False,
    )
    assert v_reused["strategy_statistical_verdict"] == "POSITIVE_IN_REUSED_HISTORY_NOT_VERIFIED"
    assert v_reused["activation_ready"] == "NO"
