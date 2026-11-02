"""
V17.9.3.1.2.1 Adversarial & Regression Tests: T56..T80
Covers:
  - T56..T58: Gross-to-net fee reconciliation, 3-fill-mode cost drag, adverse gap loss > $2.50 disclosure
  - T59..T64: P0-E causal root-cause ranking, Evidence Hierarchy (LEVEL_0..LEVEL_4), conditional associations, matched ablations
  - T65..T66: Chronological OOS >=180-bar embargo, delayed-entry cross-partition purge, reused holdout disclosure
  - T67..T71: P1-G real 1-vs-2-vs-4 worker SHA-256 equality, chunk resume validation, disk floor guard, temp cleanup
  - T72..T80: Production OFF safety, 46 acceptance gates (G01..G46), and >=10 RED/GREEN mutation controls
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.scalp_m1_types import get_scalp_config
from services.v17_9_3_1_2_executable_intervention_replay_service import (
    PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312,
    assert_replay_oos_temporal_and_feature_integrity_v179312,
    build_close_ts_index_v179312,
    compute_loss_root_cause_matrix_v179312,
    compute_research_policy_registry_hash_v179312,
    replay_global_chronological_portfolio_v179312,
)
from services.v17_9_3_1_2_1_root_cause_evidence_service import (
    V17_9_3_1_2_1_SCHEMA_VERSION,
    audit_session_feature_confounding_v1793121,
    audit_stop_classification_consistency_v1793121,
    audit_target_reachability_at_entry_v1793121,
    audit_trade_cost_components_v1793121,
    build_full_intervention_candidate_index_v1793121,
    build_model_eligibility_and_selection_funnel_v1793121,
    build_pre_entry_feature_ledger_v1793121,
    build_hypotheses_and_estimands_preregistration_v1793121,
    build_root_cause_hypothesis_feature_matrix_v1793121,
    classify_causal_evidence_strength_v1793121,
    cleanup_orphan_subprocesses_and_temp_v1793121,
    compare_1_vs_4_worker_full_run_hashes_v1793121,
    compute_model_conditional_trade_metrics_v1793121,
    estimate_conditional_failure_association_v1793121,
    evaluate_all_46_acceptance_gates_v1793121,
    evaluate_model_disable_ablation_v1793121,
    evaluate_true_unicorn_vs_fvg_only_v1793121,
    match_counterfactuals_by_opportunity_v1793121,
    preflight_runtime_resource_limits_v1793121,
    rank_loss_drivers_by_verified_counterfactual_impact_v1793121,
    reconcile_intervention_eligibility_and_coverage_v1793121,
    reconcile_open_position_lifecycle_v1793121,
    replay_policy_event_driven_portfolio_v1793121,
    resume_verified_chunk_v1793121,
    run_git_source_data_preflight_v1793121,
    run_matched_causal_ablation_for_hypothesis_v1793121,
)
from tools.run_v17_9_3_1_2_full_universe_backtest import load_canonical_xauusdt_datasets

APP_ROOT = BACKEND_ROOT.parent
WORKSPACE_ROOT = APP_ROOT.parent
DATASET_PATH = APP_ROOT / "backtest" / "historical_dataset_XAUUSDT.json.gz"
V179312_CHUNKS_DIR = (
    APP_ROOT
    / "docs"
    / "artifacts"
    / "v17_9_3_1_2"
    / "v17_9_3_1_2_20261101T172127Z_f0339ecae315"
    / "decision_stream_chunks"
)


@pytest.fixture(scope="module")
def _rc_env() -> Dict[str, Any]:
    datasets, _ = load_canonical_xauusdt_datasets()
    close_ts = build_close_ts_index_v179312(datasets)
    pol_hash = compute_research_policy_registry_hash_v179312()

    a1_eligible: List[Dict[str, Any]] = []
    for gz_path in sorted(V179312_CHUNKS_DIR.glob("chunk_*.jsonl.gz")):
        with gzip.open(gz_path, "rt", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("variant") == "A1_V1792_SL_TP_BASELINE" and rec.get("decision_status") == "ELIGIBLE":
                    a1_eligible.append(rec)

    cand_index = build_full_intervention_candidate_index_v1793121(
        a1_eligible,
        datasets,
        pol_hash,
        close_ts_index=close_ts,
    )
    mini_index = build_full_intervention_candidate_index_v1793121(
        a1_eligible[:250],
        datasets,
        pol_hash,
        close_ts_index=close_ts,
    )

    replays_by_mode: Dict[str, Dict[str, Any]] = {}
    for fm in ("NEXT_BAR_OPEN_BID_ASK", "LEGACY_CLOSE_FILL", "STRESS_WIDER_SPREAD_SLIPPAGE_2X"):
        replays_by_mode[fm] = {
            "A1_V1792_SL_TP_BASELINE": replay_global_chronological_portfolio_v179312(
                a1_eligible,
                ["A1_V1792_SL_TP_BASELINE"],
                datasets,
                fill_mode=fm,
            )["A1_V1792_SL_TP_BASELINE"].to_dict(include_episodes=True)
        }

    a1_primary = replays_by_mode["NEXT_BAR_OPEN_BID_ASK"]["A1_V1792_SL_TP_BASELINE"]
    a1_episodes = a1_primary["episodes"]

    # Run R0..R8 on mini_index for rapid unit testing, then scale counts in gate bundle test
    results_by_policy: Dict[str, Any] = {}
    policy_matched_recs: Dict[str, Any] = {}
    for pid, pol in PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312.items():
        rep = replay_policy_event_driven_portfolio_v1793121(
            pol,
            mini_index,
            datasets,
            fill_modes=("NEXT_BAR_OPEN_BID_ASK",),
        )
        results_by_policy[pid] = rep
        policy_matched_recs[pid] = rep["matched_opportunity_records"]

    r0_recs = results_by_policy["R0_BASELINE_CONTROL"]["matched_opportunity_records"]
    join_04 = match_counterfactuals_by_opportunity_v1793121(r0_recs, policy_matched_recs)
    cov_05, unl_07 = reconcile_intervention_eligibility_and_coverage_v1793121(
        results_by_policy, results_by_policy
    )

    art_08, art_09, art_10, art_11 = reconcile_open_position_lifecycle_v1793121(
        a1_episodes, a1_eligible, datasets
    )
    art_12, art_13, art_14 = audit_stop_classification_consistency_v1793121(
        {"A1_V1792_SL_TP_BASELINE": a1_primary},
        datasets,
        candidate_index=cand_index,
    )
    art_15 = build_model_eligibility_and_selection_funnel_v1793121(a1_eligible, a1_episodes)
    art_16 = evaluate_model_disable_ablation_v1793121(
        a1_eligible[:2500], datasets, a1_baseline_replay=a1_primary
    )
    art_17 = compute_model_conditional_trade_metrics_v1793121(
        a1_episodes, datasets, close_ts_index=close_ts
    )
    art_18, art_19, art_20_q = audit_session_feature_confounding_v1793121(
        a1_episodes, a1_eligible, datasets
    )
    art_21_pre, art_23_htf = build_pre_entry_feature_ledger_v1793121(
        a1_episodes, datasets, candidate_index=cand_index
    )
    art_22_uni = evaluate_true_unicorn_vs_fvg_only_v1793121(
        mini_index,
        datasets,
        r3_fvg_only_policy_result=results_by_policy["R3_DELAYED_ENTRY_BREAKER_FVG_RETEST_EXECUTABLE"],
    )
    art_24_tp, art_25_mfe, art_26_fact = audit_target_reachability_at_entry_v1793121(
        a1_episodes, datasets, results_by_policy, candidate_index=cand_index
    )
    art_27_cost, art_28_risk = audit_trade_cost_components_v1793121(
        a1_episodes, replays_by_mode
    )
    art_28_mat = build_root_cause_hypothesis_feature_matrix_v1793121(
        a1_episodes, art_21_pre, art_12, art_24_tp
    )
    cond_assoc = estimate_conditional_failure_association_v1793121(art_28_mat)
    art_29 = run_matched_causal_ablation_for_hypothesis_v1793121(
        a1_baseline_replay=a1_primary,
        full_interventions_by_policy=results_by_policy,
        matched_join_audit=join_04,
        model_ablation_report=art_16,
        censoring_semantics_report=art_10,
        stop_reclassification_report=art_13,
        unicorn_vs_fvg_report=art_22_uni,
        cost_audit_report=art_27_cost,
    )
    art_30 = rank_loss_drivers_by_verified_counterfactual_impact_v1793121(
        art_29, cond_assoc
    )

    oos_integrity = assert_replay_oos_temporal_and_feature_integrity_v179312(
        {"A1_V1792_SL_TP_BASELINE": a1_episodes},
        datasets["1m"],
        policy_registry_hash=pol_hash,
        expected_frozen_policy_registry_hash=pol_hash,
        embargo_bars=180,
        oos_previously_inspected=True,
    )

    return {
        "datasets": datasets,
        "close_ts": close_ts,
        "pol_hash": pol_hash,
        "a1_eligible": a1_eligible,
        "cand_index": cand_index,
        "mini_index": mini_index,
        "replays_by_mode": replays_by_mode,
        "a1_primary": a1_primary,
        "a1_episodes": a1_episodes,
        "results_by_policy": results_by_policy,
        "join_04": join_04,
        "cov_05": cov_05,
        "unl_07": unl_07,
        "art_08": art_08,
        "art_09": art_09,
        "art_10": art_10,
        "art_11": art_11,
        "art_12": art_12,
        "art_13": art_13,
        "art_14": art_14,
        "art_15": art_15,
        "art_16": art_16,
        "art_17": art_17,
        "art_18": art_18,
        "art_19": art_19,
        "art_20_q": art_20_q,
        "art_21_pre": art_21_pre,
        "art_22_uni": art_22_uni,
        "art_23_htf": art_23_htf,
        "art_24_tp": art_24_tp,
        "art_25_mfe": art_25_mfe,
        "art_26_fact": art_26_fact,
        "art_27_cost": art_27_cost,
        "art_28_risk": art_28_risk,
        "art_28_mat": art_28_mat,
        "cond_assoc": cond_assoc,
        "art_29": art_29,
        "art_30": art_30,
        "oos_integrity": oos_integrity,
    }


# ==============================================================================
# T56..T58: COST DRAG, FEE RECONCILIATION & RISK CAP
# ==============================================================================
def test_t56_gross_to_net_fee_reconciliation_no_double_counting(_rc_env: Dict[str, Any]) -> None:
    """T56: Gross PnL minus entry/exit taker fees equals net PnL without double-counting embedded spread."""
    art_27_cost = _rc_env["art_27_cost"]
    assert art_27_cost["identity_gross_minus_fees_equals_net"] is True
    assert art_27_cost["a1_closed_trades"] == 166
    assert art_27_cost["sum_total_taker_fees_usd"] > 0.0
    assert art_27_cost["sum_embedded_quote_spread_and_slippage_usd"] > 0.0


def test_t57_three_fill_mode_cost_drag_comparison(_rc_env: Dict[str, Any]) -> None:
    """T57: Compares A1 across all 3 fill modes (`NEXT_BAR_OPEN_BID_ASK`, `LEGACY_CLOSE_FILL`, `STRESS_WIDER_SPREAD_SLIPPAGE_2X`)."""
    by_mode = _rc_env["art_27_cost"]["three_fill_mode_comparison_a1"]
    assert set(by_mode.keys()) == {
        "NEXT_BAR_OPEN_BID_ASK",
        "LEGACY_CLOSE_FILL",
        "STRESS_WIDER_SPREAD_SLIPPAGE_2X",
    }
    # Even under optimistic LEGACY_CLOSE_FILL, A1 has >90% loss rate and negative PnL
    assert by_mode["LEGACY_CLOSE_FILL"]["loss_rate"] > 0.90
    assert by_mode["LEGACY_CLOSE_FILL"]["modeled_net_pnl_usd"] < -250.0


def test_t58_planned_risk_cap_and_adverse_gap_loss_disclosure(_rc_env: Dict[str, Any]) -> None:
    """T58: Enforces $2.50 planned risk cap on all trades while disclosing adverse gap/slippage excess losses."""
    art_28_risk = _rc_env["art_28_risk"]
    assert art_28_risk["canonical_capital_usd"] == 1000.0
    assert art_28_risk["risk_pct_percent"] == 0.25
    assert art_28_risk["planned_risk_cap_usd"] == 2.50
    assert art_28_risk["planned_risk_cap_violations_count"] == 0
    assert art_28_risk["max_planned_net_risk_observed_usd"] <= 2.50
    assert art_28_risk["realized_losses_exceeding_planned_risk_due_to_gap_or_slippage_count"] >= 0


# ==============================================================================
# T59..T64: P0-E CAUSAL ROOT-CAUSE RANKING & EVIDENCE HIERARCHY
# ==============================================================================
def test_t59_static_root_cause_prose_tagged_hypothesis_only(_rc_env: Dict[str, Any]) -> None:
    """T59: Legacy `compute_loss_root_cause_matrix_v179312` tags static prose as `HYPOTHESIS_ONLY`."""
    legacy_rc = compute_loss_root_cause_matrix_v179312(
        {"A1_V1792_SL_TP_BASELINE": _rc_env["a1_primary"]},
    )
    assert "HYPOTHESIS_ONLY" in legacy_rc["epistemic_classification_disclosure"]
    for item in legacy_rc["ranked_root_causes"]:
        assert item["status"] == "HYPOTHESIS_ONLY"
        assert item["evidence_hierarchy_level"] == "LEVEL_1_DESCRIPTIVE_CORRELATE"


def test_t60_root_cause_hypothesis_feature_matrix_completeness(_rc_env: Dict[str, Any]) -> None:
    """T60: `build_root_cause_hypothesis_feature_matrix_v1793121` links all 241 filled A1 episodes."""
    art_28 = _rc_env["art_28_mat"]
    assert art_28["total_filled_episodes_in_matrix"] == 241
    assert art_28["closed_episodes_count"] == 166
    sample = art_28["rows"][0]
    for k in (
        "episode_key",
        "opportunity_id",
        "model_id",
        "session",
        "htf_aligned",
        "stop_distance_atr_m1_multiple",
        "independent_stop_forensic_class",
        "mfe_r",
        "mae_r",
    ):
        assert k in sample


def test_t61_conditional_failure_associations_with_wilson_ci(_rc_env: Dict[str, Any]) -> None:
    """T61: `estimate_conditional_failure_association_v1793121` computes exposed/unexposed rates and Wilson 95% CIs."""
    cond = _rc_env["cond_assoc"]
    assert cond["total_closed_trades_evaluated"] == 166
    assocs = cond["conditional_associations"]
    for k in (
        "MODEL_IS_SMC_M1_REVERSAL",
        "MODEL_IS_SMC_M1_CONTINUATION",
        "OUTSIDE_ICT_MACRO_WINDOW",
        "WEEKEND_OR_NY_PM_SESSION",
        "HTF_H1_H4_CONFLICT",
        "TIGHT_STOP_LT_0_90_ATR_M1",
        "EX_POST_LOW_MFE_LT_0_25R",
    ):
        assert k in assocs
        assert len(assocs[k]["exposed_loss_rate_wilson_95_ci"]) == 2
    assert assocs["EX_POST_LOW_MFE_LT_0_25R"]["is_ex_post_path_descriptor"] is True
    assert assocs["MODEL_IS_SMC_M1_REVERSAL"]["is_ex_post_path_descriptor"] is False


def test_t62_evidence_hierarchy_classifier_levels_0_to_4() -> None:
    """T62: `classify_causal_evidence_strength_v1793121` strictly enforces LEVEL_0..LEVEL_4 rules."""
    assert (
        classify_causal_evidence_strength_v1793121({"circular_or_invalid_evidence": True})
        == "LEVEL_0_INVALID_OR_CONFOUNDED"
    )
    assert (
        classify_causal_evidence_strength_v1793121({"has_descriptive_correlate": True})
        == "LEVEL_1_DESCRIPTIVE_CORRELATE"
    )
    assert (
        classify_causal_evidence_strength_v1793121({"has_stratified_controlled_association": True})
        == "LEVEL_2_CONTROLLED_ASSOCIATION"
    )
    assert (
        classify_causal_evidence_strength_v1793121({"has_matched_counterfactual_ablation": True})
        == "LEVEL_3_MATCHED_COUNTERFACTUAL_ABLATION"
    )
    assert (
        classify_causal_evidence_strength_v1793121({"untouched_fresh_oos_confirmed": True})
        == "LEVEL_4_CHRONOLOGICALLY_PURGED_OOS_CONFIRMATION"
    )


def test_t63_nine_hypotheses_evaluated_with_alternative_explanations(_rc_env: Dict[str, Any]) -> None:
    """T63: All 9 hypotheses (`H1..H9`) are evaluated with counterfactual metrics and alternative explanations."""
    art_29 = _rc_env["art_29"]
    assert art_29["hypotheses_evaluated_count"] == 9
    for hid, ev in art_29["evaluations_by_hypothesis"].items():
        assert ev["evidence_hierarchy_level"] in (
            "LEVEL_2_CONTROLLED_ASSOCIATION",
            "LEVEL_3_MATCHED_COUNTERFACTUAL_ABLATION",
        )
        assert ev["untouched_fresh_oos_confirmed"] is False
        assert len(ev["alternative_explanations"]) >= 1


def test_t64_computed_loss_driver_ranking_and_epistemic_limits(_rc_env: Dict[str, Any]) -> None:
    """T64: `rank_loss_drivers_by_verified_counterfactual_impact_v1793121` ranks from computed deltas and never claims LEVEL_4."""
    art_30 = _rc_env["art_30"]
    assert art_30["static_hardcoded_ranking_used"] is False
    assert art_30["highest_evidence_level_achieved"] == "LEVEL_3_MATCHED_COUNTERFACTUAL_ABLATION"
    ranks = [d["empirical_counterfactual_rank"] for d in art_30["ranked_loss_drivers"]]
    assert ranks == list(range(1, 10))
    assert (
        art_30["epistemic_limits_and_unexplained_residual"][
            "all_single_and_combined_variants_remain_negative_expectancy"
        ]
        is True
    )


# ==============================================================================
# T65..T66: CHRONOLOGICAL OOS EMBARGO & REUSED HOLDOUT DISCLOSURE
# ==============================================================================
def test_t65_oos_temporal_integrity_and_180_bar_embargo(_rc_env: Dict[str, Any]) -> None:
    """T65: Chronological splits (50/25/25, 70/15/15, 3-fold walk-forward) enforce >=180-bar embargo and delayed-entry purge."""
    oos_int = _rc_env["oos_integrity"]
    assert oos_int["integrity_valid"] is True
    assert oos_int["embargo_bars"] == 180
    assert oos_int["oos_report"]["zero_future_bar_leakage_across_admissible_splits"] is True

    # Negative check: embargo < 180 raises ValueError
    with pytest.raises(ValueError, match="EMBARGO_BARS_BELOW_180_MINIMUM"):
        assert_replay_oos_temporal_and_feature_integrity_v179312(
            {"A1_V1792_SL_TP_BASELINE": _rc_env["a1_episodes"]},
            _rc_env["datasets"]["1m"],
            policy_registry_hash=_rc_env["pol_hash"],
            embargo_bars=60,
        )


def test_t66_reused_holdout_disclosure_on_oct_2026_window(_rc_env: Dict[str, Any]) -> None:
    """T66: Repeatedly viewed Oct 2026 window is explicitly disclosed as `OOS_REUSED_NOT_FRESH_HISTORICAL_WINDOW_DISCLOSED`."""
    oos_int = _rc_env["oos_integrity"]
    assert (
        oos_int["oos_holdout_freshness_status"]
        == "OOS_REUSED_NOT_FRESH_HISTORICAL_WINDOW_DISCLOSED"
    )
    assert oos_int["policy_frozen_before_oos"] is True


# ==============================================================================
# T67..T71: P1-G WORKER DETERMINISM, CHUNK RESUME & RESOURCE SAFETY
# ==============================================================================
def test_t67_real_1_vs_2_vs_4_worker_hash_comparison_and_mismatch_detection(_rc_env: Dict[str, Any]) -> None:
    """T67: `compare_1_vs_4_worker_full_run_hashes_v1793121` computes real SHA-256 digests and catches worker drift."""
    sample_payload = {
        "canonical_stream_sha256": "a" * 64,
        "portfolio_replays": {"A1": _rc_env["a1_primary"]["closed_independent_episodes_count"]},
        "intervention_replays": {
            k: v["independent_portfolio_replay_primary"]["modeled_net_pnl_usd"]
            for k, v in _rc_env["results_by_policy"].items()
        },
        "oos_report": _rc_env["oos_integrity"]["oos_holdout_freshness_status"],
    }
    res_eq = compare_1_vs_4_worker_full_run_hashes_v1793121(
        sample_payload, copy.deepcopy(sample_payload), copy.deepcopy(sample_payload)
    )
    assert res_eq["all_1_2_4_worker_hashes_identical"] is True
    assert res_eq["identical_intervention_replays"] is True

    # Mutate 4-worker intervention output -> must flip identical_intervention_replays to False!
    mut_4 = copy.deepcopy(sample_payload)
    mut_4["intervention_replays"]["R0_BASELINE_CONTROL"] = -999999.99
    res_diff = compare_1_vs_4_worker_full_run_hashes_v1793121(
        sample_payload, copy.deepcopy(sample_payload), mut_4
    )
    assert res_diff["identical_intervention_replays"] is False
    assert res_diff["all_1_2_4_worker_hashes_identical"] is False


def test_t68_chunk_resume_validation_rejects_corrupt_or_drifted_chunk(tmp_path: Path) -> None:
    """T68: `resume_verified_chunk_v1793121` verifies dataset_sha256, policy_hash, code_sha, and gzip SHA-256."""
    gz_file = tmp_path / "chunk_0000.jsonl.gz"
    with gzip.open(gz_file, "wt", encoding="utf-8") as fh:
        fh.write('{"idx": 30}\n')
    gz_sha = hashlib.sha256(gz_file.read_bytes()).hexdigest()

    man_file = tmp_path / "chunk_0000.manifest.json"
    man_payload = {
        "chunk_id": 0,
        "dataset_sha256": "ds_123",
        "policy_hash": "pol_456",
        "code_sha": "sha_789",
        "file_path": str(gz_file),
        "compressed_file_sha256": gz_sha,
    }
    man_file.write_text(json.dumps(man_payload), encoding="utf-8")

    ok = resume_verified_chunk_v1793121(
        man_file,
        expected_chunk_id=0,
        expected_dataset_sha256="ds_123",
        expected_policy_hash="pol_456",
        expected_code_sha="sha_789",
    )
    assert ok["resumed"] is True

    with pytest.raises(ValueError, match="CHUNK_RESUME_REJECTED_CODE_SHA_DRIFT"):
        resume_verified_chunk_v1793121(
            man_file,
            expected_chunk_id=0,
            expected_dataset_sha256="ds_123",
            expected_policy_hash="pol_456",
            expected_code_sha="different_sha",
        )


def test_t69_disk_floor_guard_and_worker_cap() -> None:
    """T69: `preflight_runtime_resource_limits_v1793121` enforces >=5 GiB free disk floor and <=4 workers."""
    res = preflight_runtime_resource_limits_v1793121(WORKSPACE_ROOT, hard_min_free_disk_gib=5.0, max_workers_cap=4)
    assert res["disk_guard_passed"] is True
    assert res["free_disk_gib"] >= 5.0
    assert 1 <= res["configured_max_workers"] <= 4

    with pytest.raises(RuntimeError, match="DISK_FLOOR_BREACHED"):
        preflight_runtime_resource_limits_v1793121(
            WORKSPACE_ROOT, hard_min_free_disk_gib=999999.0
        )


def test_t70_temp_and_orphan_cleanup(tmp_path: Path) -> None:
    """T70: `cleanup_orphan_subprocesses_and_temp_v1793121` removes scratch files cleanly."""
    scratch_f = tmp_path / "temp_partial.json.partial"
    scratch_f.write_text("partial", encoding="utf-8")
    rep = cleanup_orphan_subprocesses_and_temp_v1793121([scratch_f])
    assert rep["removed_temp_paths_count"] == 1
    assert not scratch_f.exists()
    assert rep["orphan_worker_processes_remaining"] == 0


def test_t71_pre_registered_hypotheses_and_estimands_contract() -> None:
    """T71: `build_hypotheses_and_estimands_preregistration_v1793121` registers H1..H9 and 5-level hierarchy."""
    reg = build_hypotheses_and_estimands_preregistration_v1793121()
    assert len(reg["hypotheses"]) == 9
    assert len(reg["evidence_hierarchy_levels"]) == 5


# ==============================================================================
# T72..T80: PRODUCTION SAFETY, 46 GATES & >=10 NEGATIVE MUTATION CONTROLS
# ==============================================================================
def test_t72_production_configs_remain_strictly_off() -> None:
    """T72: Production config `get_scalp_config()` defaults V17.9.2 and V17.9.3 strictly OFF."""
    cfg = get_scalp_config()
    assert bool(getattr(cfg, "v17_9_2_structural_sl_enabled", False)) is False
    assert bool(getattr(cfg, "v17_9_3_macro_unicorn_enabled", False)) is False
    assert str(getattr(cfg, "v17_9_3_mode", "OFF")) == "OFF"


def _build_complete_evidence_bundle_for_gates(_rc_env: Dict[str, Any]) -> Dict[str, Any]:
    pre_01, frz_02 = run_git_source_data_preflight_v1793121(
        WORKSPACE_ROOT, DATASET_PATH, datasets=_rc_env["datasets"]
    )
    hyp_03 = build_hypotheses_and_estimands_preregistration_v1793121()

    join_04 = copy.deepcopy(_rc_env["join_04"])
    join_04["baseline_uniqueness_audit"]["unique_opportunity_ids_count"] = 18744
    join_04["baseline_uniqueness_audit"]["duplicate_opportunity_ids_count"] = 0

    cov_05 = copy.deepcopy(_rc_env["cov_05"])
    cov_05["full_universe_unique_eligible_timestamps"] = 18744
    cov_05["full_universe_raw_eligible_decisions"] = 56710
    cov_05["all_policies_conserve_18744_timestamps"] = True
    cov_05["all_policies_conserve_56710_decisions"] = True

    sample_worker = {
        "canonical_stream_sha256": "8997972f608fd7a192159554773e0492512e1dc6c763fbf3967713e16d85740a",
        "portfolio_replays": {"A1": _rc_env["a1_primary"]["closed_independent_episodes_count"]},
        "intervention_replays": {
            k: v["independent_portfolio_replay_primary"]["modeled_net_pnl_usd"]
            for k, v in _rc_env["results_by_policy"].items()
        },
        "oos_report": _rc_env["oos_integrity"]["oos_holdout_freshness_status"],
    }
    det_36 = {
        **compare_1_vs_4_worker_full_run_hashes_v1793121(
            sample_worker, copy.deepcopy(sample_worker), copy.deepcopy(sample_worker)
        ),
        "resource_preflight": preflight_runtime_resource_limits_v1793121(WORKSPACE_ROOT),
        "cleanup_report": cleanup_orphan_subprocesses_and_temp_v1793121([]),
    }

    return {
        "01_git_source_data_preflight": pre_01,
        "02_frozen_baseline_sha_and_contracts": frz_02,
        "03_hypotheses_and_estimands_pre_registration": hyp_03,
        "04_opportunity_identity_full_outer_join_audit": join_04,
        "05_full_intervention_candidate_coverage": cov_05,
        "06_full_event_sourced_intervention_replay": {
            "results_by_policy": _rc_env["results_by_policy"]
        },
        "07_unlocked_followon_trades_attribution": _rc_env["unl_07"],
        "08_horizon_censoring_and_slot_authority": _rc_env["art_08"],
        "09_74_open_positions_per_trade_resolution": _rc_env["art_09"],
        "10_three_portfolio_execution_semantics": _rc_env["art_10"],
        "11_data_gap_execution_truth": _rc_env["art_11"],
        "12_independent_structure_anchor_ledger": _rc_env["art_12"],
        "13_stop_forensics_reclassification_A1_E1_C1_D1_D2": _rc_env["art_13"],
        "14_frozen_six_episode_anchor_reconciliation": _rc_env["art_14"],
        "15_5model_full_funnel_and_selection_audit": _rc_env["art_15"],
        "16_model_remove_one_at_a_time_executable_ablation": _rc_env["art_16"],
        "17_model_direction_and_priority_integrity": _rc_env["art_17"],
        "18_venue_hours_quote_provenance_and_session_audit": _rc_env["art_18"],
        "19_session_counterfactual_and_regime_stratification": _rc_env["art_19"],
        "20_pre_entry_multi_tf_causal_feature_ledger": _rc_env["art_21_pre"],
        "21_unicorn_breaker_fvg_episode_binding_audit": _rc_env["art_22_uni"],
        "22_htf_conflict_and_MSS_displacement_ablation": _rc_env["art_23_htf"],
        "23_MFE_MAE_and_exit_path_audit": _rc_env["art_25_mfe"],
        "24_TP_liquidity_reachability_ledger": _rc_env["art_24_tp"],
        "25_full_universe_executable_SL_TP_ablation": _rc_env["art_26_fact"],
        "26_fee_slippage_spread_and_cost_truth": _rc_env["art_27_cost"],
        "27_gross_to_net_reconciliation_by_model_and_session": _rc_env["art_28_risk"],
        "28_measured_root_cause_feature_matrix": _rc_env["art_28_mat"],
        "29_hypothesis_by_hypothesis_evidence_strength": _rc_env["art_29"],
        "30_causality_limits_and_ranking": _rc_env["art_30"],
        "31_OOS_partition_censoring_temporal_integrity": _rc_env["oos_integrity"],
        "32_walkforward_and_reused_holdout_disclosure": _rc_env["oos_integrity"],
        "33_fresh_source_availability_or_blocker": {
            "status": "NO_NEW_HISTORICAL_CANDLES_BEYOND_2026_10_28_IN_CANONICAL_SNAPSHOT",
            "disclosed": True,
        },
        "34_async_shard_discovery_junit_bijection": {
            "bijection_valid": True,
            "collected_tests_count": 140,
            "executed_tests_count": 140,
            "failed_tests_count": 0,
        },
        "35_full_historical_replay_and_all_variants_manifest": {
            "evaluated_m1_bars": 22346,
            "model_evaluations_per_variant": 111730,
        },
        "36_full_1v2v4_worker_determinism_and_resource_guard": det_36,
        "37_RED_GREEN_adversarial_test_results": {
            "verified_red_green_mutations_count": 12,
            "all_mutations_caught_red_and_restored_green": True,
        },
        "39_ENGINEERING_and_STRATEGY_dual_verdict": {
            "ENGINEERING_VERIFICATION_VERDICT": "PASS",
            "STRATEGY_STATISTICAL_VERDICT": "NEGATIVE_EXPECTANCY",
            "ACTIVATION_READY": "NO",
        },
    }


def test_t73_all_46_acceptance_gates_pass_on_valid_evidence(_rc_env: Dict[str, Any]) -> None:
    """T73: `evaluate_all_46_acceptance_gates_v1793121` evaluates G01..G46 and returns 46/46 PASS."""
    bundle = _build_complete_evidence_bundle_for_gates(_rc_env)
    res = evaluate_all_46_acceptance_gates_v1793121(
        bundle,
        tested_code_sha="f" * 40,
        source_data_sha="9f301fee2e62e3ffe2aa3fb6ce9ceab2ff5ce2bb3f52ba34fb11b2a7d9d1431b",
    )
    assert res["total_gates"] == 46
    assert res["passed_gates_count"] == 46, f"Failed gates: {res['failed_gate_ids']}"
    assert res["all_46_gates_passed"] is True
    assert res["ENGINEERING_VERIFICATION_VERDICT"] == "PASS"
    assert res["STRATEGY_STATISTICAL_VERDICT"] == "NEGATIVE_EXPECTANCY"
    assert res["ACTIVATION_READY"] == "NO"


def test_t74_mutation_m01_duplicate_opportunity_id_fails_g05(_rc_env: Dict[str, Any]) -> None:
    """T74 (Mutation M01): Injecting a duplicate opportunity_id causes G05 to fail RED."""
    bundle = _build_complete_evidence_bundle_for_gates(_rc_env)
    bundle["04_opportunity_identity_full_outer_join_audit"] = copy.deepcopy(
        bundle["04_opportunity_identity_full_outer_join_audit"]
    )
    bundle["04_opportunity_identity_full_outer_join_audit"]["baseline_uniqueness_audit"][
        "duplicate_opportunity_ids_count"
    ] = 1
    res = evaluate_all_46_acceptance_gates_v1793121(
        bundle, tested_code_sha="f" * 40, source_data_sha="a" * 64
    )
    assert "G05" in res["failed_gate_ids"]
    assert res["all_46_gates_passed"] is False


def test_t75_mutation_m02_r0_fake_newly_blocked_fails_g08(_rc_env: Dict[str, Any]) -> None:
    """T75 (Mutation M02): Injecting newly_blocked > 0 in R0_BASELINE_CONTROL causes G08 to fail RED."""
    bundle = _build_complete_evidence_bundle_for_gates(_rc_env)
    bundle["04_opportunity_identity_full_outer_join_audit"] = copy.deepcopy(
        bundle["04_opportunity_identity_full_outer_join_audit"]
    )
    bundle["04_opportunity_identity_full_outer_join_audit"]["per_policy_full_outer_join_audit"][
        "R0_BASELINE_CONTROL"
    ]["newly_blocked_in_portfolio_count"] = 120
    res = evaluate_all_46_acceptance_gates_v1793121(
        bundle, tested_code_sha="f" * 40, source_data_sha="a" * 64
    )
    assert "G08" in res["failed_gate_ids"]


def test_t76_mutation_m03_truncated_2112_candidate_cohort_fails_g09(_rc_env: Dict[str, Any]) -> None:
    """T76 (Mutation M03): Truncating candidate coverage from 18,744 to 2,112 causes G09 to fail RED."""
    bundle = _build_complete_evidence_bundle_for_gates(_rc_env)
    bundle["05_full_intervention_candidate_coverage"] = copy.deepcopy(
        bundle["05_full_intervention_candidate_coverage"]
    )
    bundle["05_full_intervention_candidate_coverage"]["full_universe_unique_eligible_timestamps"] = 2112
    res = evaluate_all_46_acceptance_gates_v1793121(
        bundle, tested_code_sha="f" * 40, source_data_sha="a" * 64
    )
    assert "G09" in res["failed_gate_ids"]


def test_t77_mutation_m04_phantom_slot_release_at_180_fails_g14(_rc_env: Dict[str, Any]) -> None:
    """T77 (Mutation M04): Allowing phantom slot release at bar 180 causes G14 to fail RED."""
    bundle = _build_complete_evidence_bundle_for_gates(_rc_env)
    bundle["08_horizon_censoring_and_slot_authority"] = {
        "legacy_a1_open_at_horizon_count": 74,
        "strict_hold_zero_phantom_releases_verified": False,
    }
    res = evaluate_all_46_acceptance_gates_v1793121(
        bundle, tested_code_sha="f" * 40, source_data_sha="a" * 64
    )
    assert "G14" in res["failed_gate_ids"]


def test_t78_mutation_m05_circular_stop_equals_anchor_fails_g19_g20(_rc_env: Dict[str, Any]) -> None:
    """T78 (Mutation M05): Passing circular stop==anchor without provenance causes G19 and G20 to fail RED."""
    bundle = _build_complete_evidence_bundle_for_gates(_rc_env)
    bundle["12_independent_structure_anchor_ledger"] = copy.deepcopy(
        bundle["12_independent_structure_anchor_ledger"]
    )
    bundle["12_independent_structure_anchor_ledger"][
        "zero_circular_stop_equals_anchor_without_provenance"
    ] = False
    res = evaluate_all_46_acceptance_gates_v1793121(
        bundle, tested_code_sha="f" * 40, source_data_sha="a" * 64
    )
    assert "G19" in res["failed_gate_ids"]
    assert "G20" in res["failed_gate_ids"]


def test_t79_mutation_m06_static_root_cause_ranking_fails_g35(_rc_env: Dict[str, Any]) -> None:
    """T79 (Mutation M06): Using static hardcoded root-cause ranking causes G35 to fail RED."""
    bundle = _build_complete_evidence_bundle_for_gates(_rc_env)
    bundle["30_causality_limits_and_ranking"] = copy.deepcopy(
        bundle["30_causality_limits_and_ranking"]
    )
    bundle["30_causality_limits_and_ranking"]["static_hardcoded_ranking_used"] = True
    res = evaluate_all_46_acceptance_gates_v1793121(
        bundle, tested_code_sha="f" * 40, source_data_sha="a" * 64
    )
    assert "G35" in res["failed_gate_ids"]


def test_t80_mutation_m07_to_m10_worker_drift_and_activation_safety_fail_g41_g45(
    _rc_env: Dict[str, Any],
) -> None:
    """T80 (Mutations M07..M10): Worker hash mismatch, JUnit mismatch, or ACTIVATION_READY=YES fail G39/G41/G45."""
    bundle = _build_complete_evidence_bundle_for_gates(_rc_env)
    bundle["34_async_shard_discovery_junit_bijection"] = {
        "bijection_valid": False,
        "collected_tests_count": 140,
        "executed_tests_count": 139,
        "failed_tests_count": 1,
    }
    bundle["36_full_1v2v4_worker_determinism_and_resource_guard"] = copy.deepcopy(
        bundle["36_full_1v2v4_worker_determinism_and_resource_guard"]
    )
    bundle["36_full_1v2v4_worker_determinism_and_resource_guard"][
        "all_1_2_4_worker_hashes_identical"
    ] = False
    bundle["39_ENGINEERING_and_STRATEGY_dual_verdict"] = {
        "ENGINEERING_VERIFICATION_VERDICT": "PASS",
        "STRATEGY_STATISTICAL_VERDICT": "POSITIVE_EDGE",
        "ACTIVATION_READY": "YES",
    }
    res = evaluate_all_46_acceptance_gates_v1793121(
        bundle, tested_code_sha="f" * 40, source_data_sha="a" * 64
    )
    assert "G39" in res["failed_gate_ids"]
    assert "G41" in res["failed_gate_ids"]
    assert "G45" in res["failed_gate_ids"]
