# -*- coding: utf-8 -*-
"""
V17.9.3.1.2.1 Adversarial Regression Suite — Part 2 (T29..T55):
- Independent Pre-Entry Protected Structure & Stop Forensics (T29..T36)
- 5-Model Eligibility, Tie Arbitration & Single/Leave-One-Out Ablations (T37..T43)
- Venue Market State, Session Confounding & Quote Provenance (T44..T46)
- True Causal Unicorn (Breaker+FVG) vs Isolated FVG Retest & HTF Conflict (T47..T49)
- Quote-Side MFE/MAE Trajectories, Target Reachability & SL/TP Factorial (T50..T55)
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from services.v17_9_3_1_2_1_root_cause_evidence_service import (
    audit_session_feature_confounding_v1793121,
    audit_stop_classification_consistency_v1793121,
    audit_target_reachability_at_entry_v1793121,
    build_full_intervention_candidate_index_v1793121,
    build_model_eligibility_and_selection_funnel_v1793121,
    build_pre_entry_feature_ledger_v1793121,
    classify_stop_vs_anchor_with_ohlc_uncertainty_v1793121,
    classify_venue_market_state_asof_v1793121,
    compute_model_conditional_trade_metrics_v1793121,
    compute_quote_side_MFE_MAE_path_v1793121,
    evaluate_model_disable_ablation_v1793121,
    evaluate_no_trade_control_v1793121,
    evaluate_true_unicorn_vs_fvg_only_v1793121,
    replay_policy_event_driven_portfolio_v1793121,
    resolve_pre_entry_protected_structure_authority_v1793121,
    validate_ic_timing_and_unicorn_episode_binding_v1793121,
    validate_protected_anchor_independence_v1793121,
)
from services.v17_9_3_1_2_executable_intervention_replay_service import (
    PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312,
    build_close_ts_index_v179312,
    compute_research_policy_registry_hash_v179312,
    recalculate_trade_risk_quantity_v179312,
    replay_global_chronological_portfolio_v179312,
)
from tools.run_v17_9_3_1_2_full_universe_backtest import load_canonical_xauusdt_datasets


APP_ROOT = Path(__file__).resolve().parents[2]
PRIOR_V179312_RUN_DIR = (
    APP_ROOT
    / "docs"
    / "artifacts"
    / "v17_9_3_1_2"
    / "v17_9_3_1_2_20261101T172127Z_f0339ecae315"
)


@pytest.fixture(scope="module")
def censoring_and_forensics_context() -> Dict[str, Any]:
    datasets, prov = load_canonical_xauusdt_datasets()
    c_ts = build_close_ts_index_v179312(datasets)
    chunks_dir = PRIOR_V179312_RUN_DIR / "decision_stream_chunks"
    a1_eligible_records: List[Dict[str, Any]] = []
    for gz_path in sorted(chunks_dir.glob("chunk_*.jsonl.gz")):
        with gzip.open(gz_path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("variant") == "A1_V1792_SL_TP_BASELINE" and rec.get("decision_status") == "ELIGIBLE":
                    a1_eligible_records.append(rec)

    a1_replay = replay_global_chronological_portfolio_v179312(
        a1_eligible_records,
        ["A1_V1792_SL_TP_BASELINE"],
        datasets,
        fill_mode="NEXT_BAR_OPEN_BID_ASK",
    )["A1_V1792_SL_TP_BASELINE"].to_dict(include_episodes=True)

    pol_hash = compute_research_policy_registry_hash_v179312()
    cand_index = build_full_intervention_candidate_index_v1793121(
        a1_eligible_records,
        datasets,
        pol_hash,
        close_ts_index=c_ts,
    )
    mini_index = build_full_intervention_candidate_index_v1793121(
        a1_eligible_records[:300],
        datasets,
        pol_hash,
        close_ts_index=c_ts,
    )
    return {
        "datasets": datasets,
        "provenance": prov,
        "close_ts_index": c_ts,
        "a1_eligible_records": a1_eligible_records,
        "a1_replay": a1_replay,
        "cand_index": cand_index,
        "mini_index": mini_index,
    }


def test_t29_circular_stop_equals_anchor_without_provenance_rejected():
    fake_circular_anchor = {
        "anchor_established": True,
        "protected_structure_price": 5010.0,
        "anchor_id": None,
        "anchor_kind": "CIRCULAR_INV_PX",
        "occurred_at_ms": None,
        "known_at_ms": None,
    }
    res = validate_protected_anchor_independence_v1793121(
        fake_circular_anchor,
        selected_sl=5010.0,
        invalidation_px=5010.0,
        decision_ts=1791504000000,
    )
    assert res["independent"] is False
    assert res["reason"] == "CIRCULAR_STOP_EQUALS_ANCHOR_WITHOUT_PROVENANCE"


def test_t30_unknown_protected_structure_not_established_when_no_causal_anchor():
    empty_datasets = {"1m": [], "5m": [], "15m": []}
    sig = {
        "direction": "LONG",
        "entry_reference": 5020.0,
        "selected_sl": 5015.0,
        "structural_invalidation_price": 5015.0,
        "checkpoint_index_1m": 0,
    }
    anc = resolve_pre_entry_protected_structure_authority_v1793121(
        empty_datasets, sig, decision_ts=1791504000000, candidate_index=None
    )
    assert anc["status"] == "UNKNOWN_PROTECTED_STRUCTURE_NOT_ESTABLISHED"
    assert anc["anchor_established"] is False
    assert anc["protected_structure_price"] is None


def test_t31_future_pivot_known_after_decision_ts_rejected():
    future_anchor = {
        "anchor_established": True,
        "protected_structure_price": 5008.0,
        "anchor_id": "5M_2_2_SL_1791504000000",
        "anchor_kind": "5M_CONFIRMED_SWING_LOW",
        "occurred_at_ms": 1791504000000,
        "known_at_ms": 1791504600000,  # 10 minutes AFTER decision_ts!
    }
    res = validate_protected_anchor_independence_v1793121(
        future_anchor,
        selected_sl=5010.0,
        invalidation_px=5010.0,
        decision_ts=1791504000000,
    )
    assert res["independent"] is False
    assert "FUTURE_ANCHOR_LEAK" in res["reason"]


def test_t32_long_wick_through_sl_with_protected_structure_held():
    ind_anchor = {
        "anchor_established": True,
        "protected_structure_price": 4990.0,
        "anchor_id": "15M_2_2_SL_1000",
        "anchor_timeframe": "15M",
        "anchor_kind": "15M_CONFIRMED_SWING_LOW",
        "occurred_at_ms": 1791500000000,
        "known_at_ms": 1791501800000,
    }
    ep = {
        "direction": "LONG",
        "selected_sl": 4998.0,
        "take_profit": 5020.0,
        "decision_timestamp_ms": 1791504000000,
        "exit_type": "LOSS",
    }
    fwd_bars = [
        {
            "open_time": 1791504000000,
            "close_time": 1791504060000,
            "open": 5000.0,
            "high": 5002.0,
            "low": 4997.0,  # hits SL 4998.0, but stays well above 4990.0 anchor
            "close": 5001.0,
        }
    ]
    cls_res = classify_stop_vs_anchor_with_ohlc_uncertainty_v1793121(
        ep, ind_anchor, fwd_bars, direction="LONG", decision_ts=1791504000000
    )
    assert cls_res["forensic_class"] == "SL_WICK_HIT_STRUCTURE_HELD"
    assert cls_res["legacy_mapped_class"] == "PREMATURE_NOISE_STOPOUT"


def test_t33_short_wick_through_sl_with_protected_structure_held():
    ind_anchor = {
        "anchor_established": True,
        "protected_structure_price": 5015.0,
        "anchor_id": "15M_2_2_SH_1000",
        "anchor_timeframe": "15M",
        "anchor_kind": "15M_CONFIRMED_SWING_HIGH",
        "occurred_at_ms": 1791500000000,
        "known_at_ms": 1791501800000,
    }
    ep = {
        "direction": "SHORT",
        "selected_sl": 5005.0,
        "take_profit": 4985.0,
        "decision_timestamp_ms": 1791504000000,
        "exit_type": "LOSS",
    }
    fwd_bars = [
        {
            "open_time": 1791504000000,
            "close_time": 1791504060000,
            "open": 5000.0,
            "high": 5006.0,  # hits SL 5005.0, stays below 5015.0 anchor
            "close": 4999.0,
        }
    ]
    cls_res = classify_stop_vs_anchor_with_ohlc_uncertainty_v1793121(
        ep, ind_anchor, fwd_bars, direction="SHORT", decision_ts=1791504000000
    )
    assert cls_res["forensic_class"] == "SL_WICK_HIT_STRUCTURE_HELD"


def test_t34_same_bar_sl_and_structure_close_break():
    ind_anchor = {
        "anchor_established": True,
        "protected_structure_price": 4995.0,
        "anchor_id": "5M_2_2_SL_1000",
        "anchor_timeframe": "5M",
        "anchor_kind": "5M_CONFIRMED_SWING_LOW",
        "occurred_at_ms": 1791500000000,
        "known_at_ms": 1791500600000,
    }
    ep = {
        "direction": "LONG",
        "selected_sl": 4998.0,
        "take_profit": 5020.0,
        "decision_timestamp_ms": 1791504000000,
        "exit_type": "LOSS",
    }
    fwd_bars = [
        {
            "open_time": 1791504000000,
            "close_time": 1791504060000,
            "open": 5000.0,
            "high": 5000.5,
            "low": 4992.0,
            "close": 4993.0,  # closes below 4995.0 anchor on the same bar SL 4998.0 is hit
        }
    ]
    cls_res = classify_stop_vs_anchor_with_ohlc_uncertainty_v1793121(
        ep, ind_anchor, fwd_bars, direction="LONG", decision_ts=1791504000000
    )
    assert cls_res["forensic_class"] == "SL_AND_STRUCTURE_BROKEN_SAME_BAR"
    assert cls_res["legacy_mapped_class"] == "TRUE_INVALIDATION_LOSS"


def test_t35_structure_broke_after_sl_classified_accurately():
    ind_anchor = {
        "anchor_established": True,
        "protected_structure_price": 4995.0,
        "anchor_id": "5M_2_2_SL_1000",
        "anchor_timeframe": "5M",
        "anchor_kind": "5M_CONFIRMED_SWING_LOW",
        "occurred_at_ms": 1791500000000,
        "known_at_ms": 1791500600000,
    }
    ep = {
        "direction": "LONG",
        "selected_sl": 4998.0,
        "take_profit": 5020.0,
        "decision_timestamp_ms": 1791504000000,
        "exit_type": "LOSS",
    }
    fwd_bars = [
        {
            "open_time": 1791504000000,
            "close_time": 1791504060000,
            "open": 5000.0,
            "high": 5000.5,
            "low": 4997.0,  # hits SL 4998 on bar 0, closes at 4997.5 > 4995
            "close": 4997.5,
        },
        {
            "open_time": 1791504060000,
            "close_time": 1791504120000,
            "open": 4997.5,
            "high": 4998.0,
            "low": 4991.0,
            "close": 4992.0,  # closes below 4995 on bar 1
        },
    ]
    cls_res = classify_stop_vs_anchor_with_ohlc_uncertainty_v1793121(
        ep, ind_anchor, fwd_bars, direction="LONG", decision_ts=1791504000000
    )
    assert cls_res["forensic_class"] == "STRUCTURE_BROKE_AFTER_SL"
    assert cls_res["legacy_mapped_class"] == "DELAYED_INVALIDATION_LOSS"


def test_t36_all_157_a1_losses_and_6_frozen_episodes_reclassified_independently(
    censoring_and_forensics_context: Dict[str, Any]
):
    art_12, art_13, art_14 = audit_stop_classification_consistency_v1793121(
        {"A1_V1792_SL_TP_BASELINE": censoring_and_forensics_context["a1_replay"]},
        censoring_and_forensics_context["datasets"],
        candidate_index=censoring_and_forensics_context["cand_index"],
    )
    assert art_12["total_a1_loss_episodes_audited"] == 157
    assert art_12["zero_circular_stop_equals_anchor_without_provenance"] is True
    a1_rec = art_13["reclassification_by_variant"]["A1_V1792_SL_TP_BASELINE"]
    assert a1_rec["total_closed_losses"] == 157
    assert sum(a1_rec["new_independent_forensic_counts"].values()) == 157
    assert art_14["reconciled_count"] == 6


def test_t37_five_model_full_eligibility_and_arbitration_funnel(
    censoring_and_forensics_context: Dict[str, Any]
):
    funnel = build_model_eligibility_and_selection_funnel_v1793121(
        censoring_and_forensics_context["a1_eligible_records"],
        censoring_and_forensics_context["a1_replay"]["episodes"],
    )
    assert funnel["total_a1_eligible_decisions"] == 56710
    assert funnel["unique_eligible_timestamps"] == 18744
    assert len(funnel["per_model_funnel"]) == 5


def test_t38_continuation_losses_and_cg01_tie_arbitration_explained(
    censoring_and_forensics_context: Dict[str, Any]
):
    funnel = build_model_eligibility_and_selection_funnel_v1793121(
        censoring_and_forensics_context["a1_eligible_records"],
        censoring_and_forensics_context["a1_replay"]["episodes"],
    )
    pm = funnel["per_model_funnel"]
    # SMC_M1_REVERSAL has priority rank 1 and accounts for 90 of 166 closed A1 trades (84L / 6W)
    assert pm["SMC_M1_REVERSAL"]["closed_trades"] == 90
    assert pm["SMC_M1_REVERSAL"]["losses"] == 84
    # SMC_M1_CONTINUATION accounts for 24 closed A1 trades (24L / 0W -> 100% loss rate)
    assert pm["SMC_M1_CONTINUATION"]["closed_trades"] == 24
    assert pm["SMC_M1_CONTINUATION"]["losses"] == 24
    # CG01_SWEEP_MSS_FVG has 17,898 eligible decisions but is outcompeted at same-bar ties
    assert pm["CG01_SWEEP_MSS_FVG"]["eligible_decisions_count"] == 17898
    assert pm["CG01_SWEEP_MSS_FVG"]["discarded_at_same_bar_tie_count"] > 0


def test_t39_single_model_only_portfolio_ablations(
    censoring_and_forensics_context: Dict[str, Any]
):
    sub_recs = censoring_and_forensics_context["a1_eligible_records"][:2500]
    abl = evaluate_model_disable_ablation_v1793121(
        sub_recs,
        censoring_and_forensics_context["datasets"],
        a1_baseline_replay=censoring_and_forensics_context["a1_replay"],
    )
    assert len(abl["single_model_only_replays"]) == 5


def test_t40_leave_one_model_out_ablations_unlock_competing_models(
    censoring_and_forensics_context: Dict[str, Any]
):
    sub_recs = censoring_and_forensics_context["a1_eligible_records"][:2500]
    abl = evaluate_model_disable_ablation_v1793121(
        sub_recs,
        censoring_and_forensics_context["datasets"],
        a1_baseline_replay=censoring_and_forensics_context["a1_replay"],
    )
    assert len(abl["leave_one_model_out_replays"]) == 5
    assert abl["key_finding_smc_m1_reversal_dominance"]["drop_smc_m1_reversal_closed_trades"] > 0


def test_t41_priority_reversal_and_highest_net_rr_arbitration_counterfactuals(
    censoring_and_forensics_context: Dict[str, Any]
):
    sub_recs = censoring_and_forensics_context["a1_eligible_records"][:2500]
    abl = evaluate_model_disable_ablation_v1793121(
        sub_recs,
        censoring_and_forensics_context["datasets"],
        a1_baseline_replay=censoring_and_forensics_context["a1_replay"],
    )
    arb_cf = abl["arbitration_priority_counterfactuals"]
    assert "REVERSE_PRIORITY_ORDER" in arb_cf
    assert "HIGHEST_NET_RR_ARBITRATION" in arb_cf


def test_t42_no_trade_cash_preservation_control_comparator(
    censoring_and_forensics_context: Dict[str, Any]
):
    ctrl = evaluate_no_trade_control_v1793121(censoring_and_forensics_context["a1_replay"])
    assert ctrl["closed_trades"] == 0
    assert ctrl["ending_equity_usd"] == 1000.0
    assert ctrl["net_pnl_advantage_over_a1_baseline_usd"] == pytest.approx(345.90, abs=0.05)


def test_t43_model_by_session_by_htf_conditional_loss_decomposition(
    censoring_and_forensics_context: Dict[str, Any]
):
    decomp = compute_model_conditional_trade_metrics_v1793121(
        censoring_and_forensics_context["a1_replay"]["episodes"],
        censoring_and_forensics_context["datasets"],
        close_ts_index=censoring_and_forensics_context["close_ts_index"],
    )
    assert decomp["total_closed_a1_trades"] == 166
    assert "SMC_M1_REVERSAL" in decomp["model_by_session_matrix"]
    assert "SMC_M1_REVERSAL" in decomp["model_by_htf_regime_matrix"]


def test_t44_venue_market_state_marks_exchange_service_not_verified(
    censoring_and_forensics_context: Dict[str, Any]
):
    bars_1m = censoring_and_forensics_context["datasets"]["1m"]
    st = classify_venue_market_state_asof_v1793121(
        int(bars_1m[100]["close_time"]), bars_1m[100], bars_1m[99]
    )
    assert st["exchange_service_state"] == "NOT_VERIFIED_FROM_HISTORICAL_CANDLES"
    assert st["quote_source_type"] == "SYNTHETIC_FROM_OHLC_SPREAD_TABLE"


def test_t45_weekend_closed_and_ny_pm_session_failure_forensics(
    censoring_and_forensics_context: Dict[str, Any]
):
    art_18, art_19, _ = audit_session_feature_confounding_v1793121(
        censoring_and_forensics_context["a1_replay"]["episodes"],
        censoring_and_forensics_context["a1_eligible_records"],
        censoring_and_forensics_context["datasets"],
    )
    assert art_18["exchange_service_state_verification"] == "NOT_VERIFIED_FROM_HISTORICAL_CANDLES"
    assert art_19["weekend_closed_forensics"]["wins"] == 1
    assert art_19["weekend_closed_forensics"]["losses"] == 34
    assert art_19["ny_pm_forensics"]["wins"] == 0
    assert art_19["ny_pm_forensics"]["losses"] == 30
    assert art_19["london_open_forensics"]["losses"] == 14


def test_t46_quote_spread_slippage_and_gap_data_quality_matrix(
    censoring_and_forensics_context: Dict[str, Any]
):
    _, _, art_20 = audit_session_feature_confounding_v1793121(
        censoring_and_forensics_context["a1_replay"]["episodes"],
        censoring_and_forensics_context["a1_eligible_records"],
        censoring_and_forensics_context["datasets"],
    )
    assert art_20["quote_provenance"] == "SYNTHETIC_FROM_OHLC_SPREAD_TABLE"
    assert art_20["m1_gaps_over_1m_count"] == 8
    assert art_20["major_gaps_over_5m_count"] == 6


def test_t47_isolated_fvg_retest_distinguished_from_true_unicorn_breaker_overlap(
    censoring_and_forensics_context: Dict[str, Any]
):
    mini_idx = censoring_and_forensics_context["mini_index"]
    found_fvg_only = False
    for opp in mini_idx["canonical_opportunities"][:60]:
        val = validate_ic_timing_and_unicorn_episode_binding_v1793121(
            opp, censoring_and_forensics_context["datasets"], candidate_index=mini_idx
        )
        if val["fvg_retest_status"] == "CONFIRMED" and val["true_unicorn_status"] == "REJECTED":
            assert val["fvg_retest_subtype"] == "FVG_RETEST_ONLY"
            assert val["true_unicorn_rejection_reason"] == "MISSING_CAUSAL_BREAKER_BINDING"
            found_fvg_only = True
            break
    assert found_fvg_only is True


def test_t48_strict_true_unicorn_policy_rejects_missing_causal_breaker_binding(
    censoring_and_forensics_context: Dict[str, Any]
):
    mini_idx = censoring_and_forensics_context["mini_index"]
    r3_fvg = replay_policy_event_driven_portfolio_v1793121(
        PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312["R3_DELAYED_ENTRY_BREAKER_FVG_RETEST_EXECUTABLE"],
        mini_idx,
        censoring_and_forensics_context["datasets"],
        fill_modes=("NEXT_BAR_OPEN_BID_ASK",),
    )
    uni_audit = evaluate_true_unicorn_vs_fvg_only_v1793121(
        mini_idx,
        censoring_and_forensics_context["datasets"],
        r3_fvg_only_policy_result=r3_fvg,
    )
    strict_res = uni_audit["r3_strict_true_unicorn_breaker_fvg_overlap"]
    assert strict_res["rejected_missing_causal_breaker_binding_count"] > 0


def test_t49_pre_entry_feature_ledger_and_htf_conflict_matrix(
    censoring_and_forensics_context: Dict[str, Any]
):
    art_21, art_23 = build_pre_entry_feature_ledger_v1793121(
        censoring_and_forensics_context["a1_replay"]["episodes"],
        censoring_and_forensics_context["datasets"],
        candidate_index=censoring_and_forensics_context["cand_index"],
    )
    assert art_21["total_filled_a1_episodes"] == 241
    assert sum(art_23["htf_status_counts"].values()) == 241


def test_t50_quote_side_mfe_mae_excursion_path_horizons(
    censoring_and_forensics_context: Dict[str, Any]
):
    bars_1m = censoring_and_forensics_context["datasets"]["1m"]
    ep = censoring_and_forensics_context["a1_replay"]["episodes"][0]
    chk = int(ep["checkpoint_index_1m"])
    fwd = bars_1m[chk + 1 : chk + 181]
    path = compute_quote_side_MFE_MAE_path_v1793121(ep, fwd)
    assert path["max_mfe_r"] >= 0.0
    assert path["max_mae_r"] >= 0.0
    assert "mfe_r_bar_1" in path["mfe_by_horizon"]
    assert "mae_r_bar_1" in path["mae_by_horizon"]


def test_t51_target_reachability_and_intervening_liquidity_pool_audit(
    censoring_and_forensics_context: Dict[str, Any]
):
    art_24, art_25, _ = audit_target_reachability_at_entry_v1793121(
        censoring_and_forensics_context["a1_replay"]["episodes"],
        censoring_and_forensics_context["datasets"],
        {},
        candidate_index=censoring_and_forensics_context["cand_index"],
    )
    assert art_24["total_a1_closed_losses_audited"] == 157
    assert art_24["losses_with_closer_causal_liquidity_pool_in_2_to_3_5_rr_band_count"] >= 0
    assert art_25["total_a1_closed_losses"] == 157


def test_t52_mfe_thresholds_before_sl_and_time_to_stop_distribution(
    censoring_and_forensics_context: Dict[str, Any]
):
    _, art_25, _ = audit_target_reachability_at_entry_v1793121(
        censoring_and_forensics_context["a1_replay"]["episodes"],
        censoring_and_forensics_context["datasets"],
        {},
        candidate_index=censoring_and_forensics_context["cand_index"],
    )
    mfe_cnts = art_25["losses_reaching_mfe_r_thresholds_before_sl"]
    assert mfe_cnts["ge_0.25R"] >= mfe_cnts["ge_0.50R"] >= mfe_cnts["ge_1.00R"] >= mfe_cnts["ge_2.00R"]
    assert sum(art_25["time_to_stop_bars_held_distribution"].values()) == 157


def test_t53_joint_entry_sl_tp_factorial_matrix(
    censoring_and_forensics_context: Dict[str, Any]
):
    mini_idx = censoring_and_forensics_context["mini_index"]
    mini_results = {}
    for vid, pol in PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312.items():
        mini_results[vid] = replay_policy_event_driven_portfolio_v1793121(
            pol, mini_idx, censoring_and_forensics_context["datasets"], fill_modes=("NEXT_BAR_OPEN_BID_ASK",)
        )
    _, _, art_26 = audit_target_reachability_at_entry_v1793121(
        censoring_and_forensics_context["a1_replay"]["episodes"],
        censoring_and_forensics_context["datasets"],
        mini_results,
        candidate_index=censoring_and_forensics_context["cand_index"],
    )
    assert len(art_26["factorial_variants_summary"]) == 9


def test_t54_r4_outer_sl_and_r5_causal_tp_modify_geometry_and_recompute_quantity(
    censoring_and_forensics_context: Dict[str, Any]
):
    mini_idx = censoring_and_forensics_context["mini_index"]
    r4 = replay_policy_event_driven_portfolio_v1793121(
        PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312["R4_OUTER_STRUCTURAL_STOP_EXECUTABLE"],
        mini_idx,
        censoring_and_forensics_context["datasets"],
        fill_modes=("NEXT_BAR_OPEN_BID_ASK",),
    )
    r5 = replay_policy_event_driven_portfolio_v1793121(
        PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312["R5_CAUSAL_LIQUIDITY_TARGET_EXECUTABLE"],
        mini_idx,
        censoring_and_forensics_context["datasets"],
        fill_modes=("NEXT_BAR_OPEN_BID_ASK",),
    )
    assert r4["accepted_modified_executable_geometry_count"] > 0
    assert r4["changed_dimensions_counts"].get("selected_sl", 0) > 0
    assert r5["accepted_modified_executable_geometry_count"] > 0
    assert r5["changed_dimensions_counts"].get("take_profit", 0) > 0


def test_t55_risk_sizing_enforces_2_50_usd_cap_and_2_0_net_rr_floor():
    sz = recalculate_trade_risk_quantity_v179312(
        entry=4200.0,
        sl=4195.0,
        tp=4226.0,
        direction="LONG",
        decision_ts=1791504000000,
    )
    assert sz["can_execute"] is True
    assert float(sz["planned_net_risk_usd"]) <= 2.50 + 1e-4
    assert float(sz["net_rr"]) >= 2.0
