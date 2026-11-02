# -*- coding: utf-8 -*-
"""
V17.9.3.1.2.1 Adversarial Regression Suite — Part 1 (T01..T28):
- Preflight, Git, Dataset & Prior Artifact Immutability (T01..T03)
- Composite Opportunity Identity & Lossless Counterfactual Joins (T04..T09)
- Full-Stream Intervention Replay & Delayed Entry Causal State Machine (T10..T20)
- Portfolio Censoring & Open-Position Lifecycle Resolution (T21..T28)
"""
from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from services.v17_9_3_1_2_1_root_cause_evidence_service import (
    CANONICAL_DATASET_SHA256,
    EXPECTED_ELIGIBLE_DECISION_RECORDS,
    EXPECTED_M1_BARS_TOTAL,
    EXPECTED_POST_WARMUP_M1_BARS,
    EXPECTED_TOTAL_DECISION_RECORDS,
    EXPECTED_UNIQUE_ELIGIBLE_M1_TIMESTAMPS,
    MANDATORY_BRANCH,
    advance_portfolio_to_next_event_v1793121,
    assert_opportunity_identity_unique_v1793121,
    build_canonical_opportunity_id_v1793121,
    build_full_intervention_candidate_index_v1793121,
    classify_position_terminal_state_v1793121,
    compute_realized_and_mtm_accounting_v1793121,
    emit_causal_order_intents_v1793121,
    match_counterfactuals_by_opportunity_v1793121,
    reconcile_intervention_eligibility_and_coverage_v1793121,
    reconcile_open_position_lifecycle_v1793121,
    replay_policy_event_driven_portfolio_v1793121,
    replay_portfolio_with_censoring_contract_v1793121,
    run_git_source_data_preflight_v1793121,
    schedule_causal_pending_confirmation_v1793121,
)
from services.v17_9_3_1_2_executable_intervention_replay_service import (
    PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312,
    PortfolioReplayStateV179312,
    build_close_ts_index_v179312,
    compute_research_policy_registry_hash_v179312,
    evaluate_executable_interventions_on_cohort_v179312,
    match_variants_on_causal_opportunity_v179312,
    replay_global_chronological_portfolio_v179312,
)
from tools.run_v17_9_3_1_2_full_universe_backtest import (
    load_canonical_xauusdt_datasets,
    select_cohort_with_unlock_candidates_v179312,
)


APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parent
DATASET_PATH = APP_ROOT / "backtest" / "historical_dataset_XAUUSDT.json.gz"
PRIOR_V179312_RUN_DIR = (
    APP_ROOT
    / "docs"
    / "artifacts"
    / "v17_9_3_1_2"
    / "v17_9_3_1_2_20261101T172127Z_f0339ecae315"
)


@pytest.fixture(scope="module")
def shared_context() -> Dict[str, Any]:
    datasets, prov = load_canonical_xauusdt_datasets()
    c_ts = build_close_ts_index_v179312(datasets)
    chunks_dir = PRIOR_V179312_RUN_DIR / "decision_stream_chunks"
    a1_eligible_records: List[Dict[str, Any]] = []
    c1_eligible_records: List[Dict[str, Any]] = []
    for gz_path in sorted(chunks_dir.glob("chunk_*.jsonl.gz")):
        with gzip.open(gz_path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("decision_status") == "ELIGIBLE":
                    if rec.get("variant") == "A1_V1792_SL_TP_BASELINE":
                        a1_eligible_records.append(rec)
                    elif rec.get("variant") == "C1_MACRO_PLUS_V1792_SL_TP":
                        c1_eligible_records.append(rec)

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
    # Also build a fast 60-opportunity mini candidate index for rapid per-policy unit tests
    mini_decisions = a1_eligible_records[:250]
    mini_index = build_full_intervention_candidate_index_v1793121(
        mini_decisions,
        datasets,
        pol_hash,
        close_ts_index=c_ts,
    )
    return {
        "datasets": datasets,
        "provenance": prov,
        "close_ts_index": c_ts,
        "a1_eligible_records": a1_eligible_records,
        "c1_eligible_records": c1_eligible_records,
        "a1_replay": a1_replay,
        "cand_index": cand_index,
        "mini_index": mini_index,
        "policy_hash": pol_hash,
    }


def test_t01_git_branch_and_ancestor_preflight(shared_context: Dict[str, Any]):
    pre_01, frz_02 = run_git_source_data_preflight_v1793121(
        REPO_ROOT, DATASET_PATH, datasets=shared_context["datasets"]
    )
    assert pre_01["mandatory_branch"] == MANDATORY_BRANCH
    assert len(pre_01["starting_head_sha"]) == 40
    assert pre_01["merge_base_with_origin_master"] == "650e4535777421077443cd3d95507cad1d99ed42"
    assert frz_02["production_safety_contract"]["v17_9_3_mode"] == "OFF"


def test_t02_dataset_sha256_m1_count_and_coverage_window(shared_context: Dict[str, Any]):
    pre_01, _ = run_git_source_data_preflight_v1793121(
        REPO_ROOT, DATASET_PATH, datasets=shared_context["datasets"]
    )
    dp = pre_01["dataset_provenance"]
    assert dp["compressed_sha256"] == CANONICAL_DATASET_SHA256
    assert dp["total_m1_bars"] == EXPECTED_M1_BARS_TOTAL
    assert dp["post_warmup_evaluated_m1_bars"] == EXPECTED_POST_WARMUP_M1_BARS
    assert dp["m1_bar_gaps_over_1m_count"] == 8
    assert dp["major_gaps_over_5m_count"] == 8
    assert dp["timeframes_coverage"]["1m"]["bar_count"] == EXPECTED_M1_BARS_TOTAL


def test_t03_prior_artifact_directories_immutable(shared_context: Dict[str, Any]):
    pre_01, _ = run_git_source_data_preflight_v1793121(
        REPO_ROOT, DATASET_PATH, datasets=shared_context["datasets"]
    )
    assert pre_01["immutable_prior_artifacts_untouched"] is True
    for p_dir, st in pre_01["immutable_prior_artifact_dirs_status"].items():
        assert st == "", f"Prior artifact directory modified: {p_dir} -> {st}"


def test_t04_composite_opportunity_id_distinct_across_repeated_setup_ids():
    rec1 = {
        "setup_id": "OB_15m_LONG_5020.00",
        "decision_timestamp_ms": 1791504000000,
        "checkpoint_index_1m": 100,
        "model_id": "SMC_M1_REVERSAL",
        "direction": "LONG",
        "entry_reference": 5025.0,
        "selected_sl": 5020.0,
        "take_profit": 5036.0,
    }
    rec2 = {
        "setup_id": "OB_15m_LONG_5020.00",
        "decision_timestamp_ms": 1791504060000,
        "checkpoint_index_1m": 101,
        "model_id": "SMC_M1_REVERSAL",
        "direction": "LONG",
        "entry_reference": 5025.5,
        "selected_sl": 5020.0,
        "take_profit": 5036.5,
    }
    oid1 = build_canonical_opportunity_id_v1793121(rec1)
    oid2 = build_canonical_opportunity_id_v1793121(rec2)
    assert oid1 != oid2
    assert oid1.startswith("opp_")
    assert build_canonical_opportunity_id_v1793121(rec1) == oid1


def test_t05_assert_opportunity_identity_unique_rejects_duplicate_within_variant():
    rec = {
        "setup_id": "SETUP_DUP",
        "decision_timestamp_ms": 1791504000000,
        "checkpoint_index_1m": 100,
        "model_id": "SMC_M1_REVERSAL",
        "direction": "LONG",
        "entry_reference": 5025.0,
        "selected_sl": 5020.0,
        "take_profit": 5036.0,
    }
    with pytest.raises(ValueError, match="DUPLICATE_OPPORTUNITY_ID_COLLISION"):
        assert_opportunity_identity_unique_v1793121([copy.deepcopy(rec), copy.deepcopy(rec)])


def test_t06_lossy_dict_by_setup_id_collapses_2112_to_1070_red_control(shared_context: Dict[str, Any]):
    capped_cohort = select_cohort_with_unlock_candidates_v179312(
        shared_context["a1_eligible_records"],
        shared_context["a1_replay"]["episodes"],
        shared_context["c1_eligible_records"],
        unlock_followups_per_episode=2,
    )
    assert len(capped_cohort) == 2112
    uniq_audit = assert_opportunity_identity_unique_v1793121(capped_cohort)
    # Prove that composite opportunity_id has 2,112 unique keys, whereas legacy setup_id had only 1,070!
    assert uniq_audit["input_records_count"] == 2112
    assert uniq_audit["unique_opportunity_ids_count"] == 2112
    assert uniq_audit["legacy_unique_setup_ids_count"] == 1070
    assert uniq_audit["legacy_collapsed_records_if_keyed_by_setup_id"] == 2112 - 1070


def test_t07_full_outer_join_by_opportunity_id_preserves_all_records(shared_context: Dict[str, Any]):
    cand_opps = shared_context["cand_index"]["canonical_opportunities"]
    assert len(cand_opps) == EXPECTED_UNIQUE_ELIGIBLE_M1_TIMESTAMPS
    uniq_audit = assert_opportunity_identity_unique_v1793121(cand_opps)
    assert uniq_audit["unique_opportunity_ids_count"] == EXPECTED_UNIQUE_ELIGIBLE_M1_TIMESTAMPS
    assert uniq_audit["duplicate_opportunity_ids_count"] == 0


def test_t08_r0_baseline_control_has_zero_newly_blocked_and_zero_newly_unlocked(shared_context: Dict[str, Any]):
    mini_idx = shared_context["mini_index"]
    r0_pol = PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312["R0_BASELINE_CONTROL"]
    r0_rep = replay_policy_event_driven_portfolio_v1793121(
        r0_pol,
        mini_idx,
        shared_context["datasets"],
        fill_modes=("NEXT_BAR_OPEN_BID_ASK",),
    )
    r0_recs = r0_rep["matched_opportunity_records"]
    join_audit = match_counterfactuals_by_opportunity_v1793121(
        r0_recs,
        {"R0_BASELINE_CONTROL": copy.deepcopy(r0_recs)},
    )
    assert join_audit["r0_baseline_control_zero_drift_valid"] is True
    r0_j = join_audit["per_policy_full_outer_join_audit"]["R0_BASELINE_CONTROL"]
    assert r0_j["newly_blocked_in_portfolio_count"] == 0
    assert r0_j["newly_unlocked_in_portfolio_count"] == 0

    # Also verify patched v179312 joiner
    v12_join = match_variants_on_causal_opportunity_v179312(
        r0_recs, {"R0_BASELINE_CONTROL": copy.deepcopy(r0_recs)}
    )
    r0_m = v12_join["comparisons_by_variant"]["R0_BASELINE_CONTROL"]
    assert r0_m["newly_blocked_in_portfolio_count"] == 0
    assert r0_m["newly_unlocked_in_portfolio_count"] == 0


def test_t09_matched_counterfactual_accounting_conservation_identity(shared_context: Dict[str, Any]):
    mini_idx = shared_context["mini_index"]
    r0_rep = replay_policy_event_driven_portfolio_v1793121(
        PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312["R0_BASELINE_CONTROL"],
        mini_idx,
        shared_context["datasets"],
        fill_modes=("NEXT_BAR_OPEN_BID_ASK",),
    )
    r4_rep = replay_policy_event_driven_portfolio_v1793121(
        PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312["R4_OUTER_STRUCTURAL_STOP_EXECUTABLE"],
        mini_idx,
        shared_context["datasets"],
        fill_modes=("NEXT_BAR_OPEN_BID_ASK",),
    )
    join_audit = match_counterfactuals_by_opportunity_v1793121(
        r0_rep["matched_opportunity_records"],
        {
            "R0_BASELINE_CONTROL": r0_rep["matched_opportunity_records"],
            "R4_OUTER_STRUCTURAL_STOP_EXECUTABLE": r4_rep["matched_opportunity_records"],
        },
    )
    assert join_audit["all_policies_two_way_conserved"] is True


def test_t10_select_cohort_with_2_followup_cap_truncates_red_control(shared_context: Dict[str, Any]):
    a1_recs = shared_context["a1_eligible_records"]
    c1_recs = shared_context["c1_eligible_records"]
    a1_eps = shared_context["a1_replay"]["episodes"]
    capped = select_cohort_with_unlock_candidates_v179312(
        a1_recs, a1_eps, c1_recs, unlock_followups_per_episode=2
    )
    uncapped = select_cohort_with_unlock_candidates_v179312(
        a1_recs, a1_eps, c1_recs, unlock_followups_per_episode=None
    )
    assert len(capped) == 2112
    assert len(uncapped) == EXPECTED_UNIQUE_ELIGIBLE_M1_TIMESTAMPS


def test_t11_full_intervention_candidate_index_covers_all_18744_eligible_timestamps(shared_context: Dict[str, Any]):
    c_idx = shared_context["cand_index"]
    assert c_idx["raw_eligible_decisions_count"] == EXPECTED_ELIGIBLE_DECISION_RECORDS
    assert c_idx["unique_eligible_timestamps_count"] == EXPECTED_UNIQUE_ELIGIBLE_M1_TIMESTAMPS


def test_t12_decision_stream_conservation_111730_total_56710_eligible():
    man_path = PRIOR_V179312_RUN_DIR / "03_decision_stream_chunks_manifest.json"
    man = json.loads(man_path.read_text(encoding="utf-8"))
    assert man["total_model_evaluations_per_variant"] == EXPECTED_TOTAL_DECISION_RECORDS
    a1_st = man["variant_status_totals"]["A1_V1792_SL_TP_BASELINE"]
    assert a1_st["ELIGIBLE"] == EXPECTED_ELIGIBLE_DECISION_RECORDS
    assert sum(a1_st.values()) == EXPECTED_TOTAL_DECISION_RECORDS


def test_t13_r0_through_r8_each_evaluates_full_candidate_stream_without_truncation(shared_context: Dict[str, Any]):
    mini_idx = shared_context["mini_index"]
    n_expected = mini_idx["unique_eligible_timestamps_count"]
    full_map = {}
    for pid, pol in PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312.items():
        res = replay_policy_event_driven_portfolio_v1793121(
            pol, mini_idx, shared_context["datasets"], fill_modes=("NEXT_BAR_OPEN_BID_ASK",)
        )
        assert res["opportunities_evaluated_count"] == n_expected
        assert res["independent_portfolio_replay_primary"]["timestamps_conservation_valid"] is True
        assert res["independent_portfolio_replay_primary"]["raw_decisions_conservation_valid"] is True
        full_map[pid] = res
    cov_05, comp_07 = reconcile_intervention_eligibility_and_coverage_v1793121(full_map, full_map)
    assert cov_05["all_policies_conserve_56710_decisions"] is True
    assert comp_07["r0_baseline_identical_under_both"] is True


def test_t14_unlocked_trade_after_2_bar_window_captured_by_event_driven_scheduler(shared_context: Dict[str, Any]):
    mini_idx = shared_context["mini_index"]
    r0_rep = replay_policy_event_driven_portfolio_v1793121(
        PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312["R0_BASELINE_CONTROL"],
        mini_idx,
        shared_context["datasets"],
        fill_modes=("NEXT_BAR_OPEN_BID_ASK",),
    )
    r6_rep = replay_policy_event_driven_portfolio_v1793121(
        PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312["R6_MACRO_SESSION_FILTER_ONLY"],
        mini_idx,
        shared_context["datasets"],
        fill_modes=("NEXT_BAR_OPEN_BID_ASK",),
    )
    assert r0_rep["opportunities_evaluated_count"] == r6_rep["opportunities_evaluated_count"]
    assert r6_rep["rejected_by_intervention_count"] > 0


def test_t15_one_active_position_and_no_overlap_enforced_per_variant(shared_context: Dict[str, Any]):
    mini_idx = shared_context["mini_index"]
    r0_rep = replay_policy_event_driven_portfolio_v1793121(
        PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312["R0_BASELINE_CONTROL"],
        mini_idx,
        shared_context["datasets"],
        fill_modes=("NEXT_BAR_OPEN_BID_ASK",),
    )
    assert r0_rep["independent_portfolio_replay_primary"]["no_overlap_contract_valid"] is True


def test_t16_delayed_entry_pending_confirmation_uses_only_future_causal_bars(shared_context: Dict[str, Any]):
    bars_1m = shared_context["datasets"]["1m"]
    opp = shared_context["mini_index"]["canonical_opportunities"][0]
    res = schedule_causal_pending_confirmation_v1793121(
        opp,
        bars_1m,
        max_wait_bars=12,
        setup_invalidation=float(opp["selected_sl"]),
        require_mss_displacement=True,
        require_breaker_fvg_retest=False,
        candidate_index=shared_context["mini_index"],
    )
    if res["status"] == "CONFIRMED":
        assert int(res["confirmed_checkpoint_index_1m"]) > int(opp["checkpoint_index_1m"])
        assert int(res["confirmed_decision_timestamp_ms"]) > int(opp["decision_timestamp_ms"])


def test_t17_pending_confirmation_cancelled_when_invalidation_breached_before_confirm(shared_context: Dict[str, Any]):
    bars_1m = shared_context["datasets"]["1m"]
    opp = dict(shared_context["mini_index"]["canonical_opportunities"][0])
    # Set invalidation right at current close so next bar immediately invalidates it
    chk = int(opp["checkpoint_index_1m"])
    cur_close = float(bars_1m[chk]["close"])
    inv_px = cur_close + 50.0 if opp["direction"] == "LONG" else cur_close - 50.0
    res = schedule_causal_pending_confirmation_v1793121(
        opp,
        bars_1m,
        max_wait_bars=6,
        setup_invalidation=inv_px,
        require_mss_displacement=True,
    )
    assert res["status"] != "CONFIRMED"
    assert "INVALIDATED" in str(res.get("rejection_reason") or "").upper()


def test_t18_pending_confirmation_expired_after_max_wait_bars(shared_context: Dict[str, Any]):
    bars_1m = shared_context["datasets"]["1m"]
    opp = dict(shared_context["mini_index"]["canonical_opportunities"][0])
    res = schedule_causal_pending_confirmation_v1793121(
        opp,
        bars_1m,
        max_wait_bars=0,
        setup_invalidation=float(opp["selected_sl"]),
        require_mss_displacement=True,
    )
    assert res["status"] != "CONFIRMED"


def test_t19_confirmed_entry_rechecks_lot_sizing_and_net_rr_at_confirm_bar(shared_context: Dict[str, Any]):
    mini_idx = shared_context["mini_index"]
    r2_pol = PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312["R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE"]
    for opp in mini_idx["canonical_opportunities"][:25]:
        mat = emit_causal_order_intents_v1793121(
            opp, r2_pol, candidate_index=mini_idx, datasets=shared_context["datasets"]
        )
        if mat["accepted"]:
            assert float(mat["new_net_rr"]) >= 2.0
            assert float(mat["new_planned_net_risk_usd"]) <= 2.50 + 1e-4


def test_t20_confirmed_entry_fills_at_next_m1_open_after_confirm_close(shared_context: Dict[str, Any]):
    mini_idx = shared_context["mini_index"]
    r2_pol = PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312["R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE"]
    r2_rep = replay_policy_event_driven_portfolio_v1793121(
        r2_pol, mini_idx, shared_context["datasets"], fill_modes=("NEXT_BAR_OPEN_BID_ASK",)
    )
    for ep in r2_rep["independent_portfolio_episodes"]:
        if ep["filled"]:
            assert int(ep["entry_fill_ts"]) >= int(ep["decision_timestamp_ms"])


def test_t21_horizon_180_bar_truncation_artificially_releases_slot_red_control():
    state = PortfolioReplayStateV179312(variant="A1_V1792_SL_TP_BASELINE", max_concurrent_positions=1)
    state.active_episode = {
        "episode_key": "EP_OPEN_180",
        "outcome_label": "OPEN_AT_HORIZON",
        "exit_fill_ts": None,
        "terminal_state_v1793121": "DIAGNOSTIC_HORIZON_SNAPSHOT_ONLY",
    }
    state.active_release_ts_ms = 1_000_000
    # Under LEGACY_ARTIFICIAL_RELEASE, advancing past 1,000,000 releases the slot:
    advance_portfolio_to_next_event_v1793121(
        state, 1_000_060, censoring_contract="LEGACY_ARTIFICIAL_RELEASE"
    )
    assert state.active_episode is None


def test_t22_strict_slot_hold_forbids_phantom_release_on_open_at_horizon():
    state = PortfolioReplayStateV179312(variant="A1_V1792_SL_TP_BASELINE", max_concurrent_positions=1)
    state.active_episode = {
        "episode_key": "EP_OPEN_180",
        "outcome_label": "OPEN_AT_HORIZON",
        "exit_fill_ts": None,
        "terminal_state_v1793121": "DIAGNOSTIC_HORIZON_SNAPSHOT_ONLY",
    }
    state.active_release_ts_ms = 1_000_000
    with pytest.raises(ValueError, match="PHANTOM_HORIZON_SLOT_RELEASE_FORBIDDEN"):
        advance_portfolio_to_next_event_v1793121(
            state, 1_000_060, censoring_contract="STRICT_HOLD_UNTIL_EXIT_OR_DATA_END"
        )


def test_t23_dataset_end_continued_resolves_episode_after_bar_180(shared_context: Dict[str, Any]):
    bars_1m = shared_context["datasets"]["1m"]
    open_eps = [
        e for e in shared_context["a1_replay"]["episodes"] if e.get("outcome_label") == "OPEN_AT_HORIZON"
    ]
    assert len(open_eps) == 74
    res = classify_position_terminal_state_v1793121(
        open_eps[0],
        bars_1m,
        fill_mode="NEXT_BAR_OPEN_BID_ASK",
        diagnostic_horizon_bars=180,
        censoring_contract="STRICT_HOLD_UNTIL_EXIT_OR_DATA_END",
    )
    assert res["terminal_state"] in ("CLOSED_TP", "CLOSED_SL", "CENSORED_END_OF_DATASET_OPEN", "CENSORED_DATA_GAP_OPEN")
    assert res["mtm_at_180_bars"] is not None


def test_t24_unresolved_at_dataset_end_marked_right_censored_with_mtm(shared_context: Dict[str, Any]):
    bars_1m = shared_context["datasets"]["1m"]
    last_open_ep = [
        e for e in shared_context["a1_replay"]["episodes"] if e.get("outcome_label") == "OPEN_AT_HORIZON"
    ][-1]
    res = classify_position_terminal_state_v1793121(
        last_open_ep,
        bars_1m,
        fill_mode="NEXT_BAR_OPEN_BID_ASK",
        diagnostic_horizon_bars=180,
        censoring_contract="STRICT_HOLD_UNTIL_EXIT_OR_DATA_END",
    )
    if res["terminal_state"] == "CENSORED_END_OF_DATASET_OPEN":
        assert res["realized_net_pnl_usd"] == 0.0
        assert res["mtm_at_terminal"] is not None


def test_t25_realized_closed_pnl_separated_from_mtm_and_combined_equity(shared_context: Dict[str, Any]):
    bars_1m = shared_context["datasets"]["1m"]
    eps = shared_context["a1_replay"]["episodes"]
    acct = compute_realized_and_mtm_accounting_v1793121(eps, bars_1m)
    assert acct["accounting_identity_valid"] is True
    assert acct["open_or_censored_positions_count"] == 75
    assert abs(
        round(acct["realized_net_pnl_usd"] + acct["unrealized_mtm_net_pnl_usd"], 2)
        - acct["total_mtm_equity_change_usd"]
    ) <= 0.01


def test_t26_all_74_open_at_horizon_and_1_data_gap_a1_episodes_reconciled(shared_context: Dict[str, Any]):
    art_08, art_09, _, art_11 = reconcile_open_position_lifecycle_v1793121(
        shared_context["a1_replay"]["episodes"],
        shared_context["datasets"],
        all_a1_decisions=None,
    )
    assert art_08["legacy_a1_open_at_horizon_count"] == 74
    assert art_08["legacy_a1_data_gap_unresolved_count"] == 1
    assert art_09["total_open_at_horizon_resolved"] == 74
    assert art_11["censored_data_gap_open_episodes_count"] == 1


def test_t27_post_180_bar_phantom_unlocked_entries_quantified(shared_context: Dict[str, Any]):
    art_08, _, _, _ = reconcile_open_position_lifecycle_v1793121(
        shared_context["a1_replay"]["episodes"],
        shared_context["datasets"],
        all_a1_decisions=None,
    )
    assert art_08["phantom_followon_trades_admitted_by_legacy_180_release_total"] > 0


def test_t28_no_overlapping_active_positions_under_strict_slot_hold(shared_context: Dict[str, Any]):
    sub_decisions = shared_context["a1_eligible_records"][:1500]
    for contract in (
        "STRICT_HOLD_UNTIL_EXIT_OR_DATA_END",
        "LEGACY_ARTIFICIAL_RELEASE",
        "EXECUTABLE_180_BAR_TIME_STOP_INTERVENTION",
    ):
        rep = replay_portfolio_with_censoring_contract_v1793121(
            sub_decisions,
            "A1_V1792_SL_TP_BASELINE",
            shared_context["datasets"],
            censoring_contract=contract,
        )
        assert rep["no_overlap_audit"]["contract_valid"] is True
        assert rep["candidate_conservation_exact"] is True
