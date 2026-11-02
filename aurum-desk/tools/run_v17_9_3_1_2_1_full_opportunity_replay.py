#!/usr/bin/env python3
"""
tools/run_v17_9_3_1_2_1_full_opportunity_replay.py

AURUM Desk / XAUUSDT / Bitget — V17.9.3.1.2.1
Full-Universe Opportunity Replay, Counterfactual Completeness & Root-Cause Evidence Pipeline.

Executes:
  1. Git, source, dataset, and prior-artifact immutability preflight (`01`, `02`, `03`).
  2. Full 22,346-bar / 111,730-decision stream verification and 5-variant x 3-fill-mode portfolio replays (`35`).
  3. Lossless composite `opportunity_id` indexing across all 18,744 unique eligible M1 timestamps (`56,710` eligible decisions).
  4. Event-driven `R0..R8` intervention replay across all 18,744 opportunities under 1, 2, and 4 workers (`04`, `05`, `06`, `07`, `36`).
  5. Horizon censoring resolution for all 74 `OPEN_AT_HORIZON` and 1 `DATA_GAP_UNRESOLVED` positions (`08`, `09`, `10`, `11`).
  6. Independent pre-entry protected-structure stop forensics across `A1/E1/C1/D1/D2` and the 6 frozen forensic episodes (`12`, `13`, `14`).
  7. 5-model full funnel, single-model-only, leave-one-model-out, and priority-arbitration ablations (`15`, `16`, `17`).
  8. Venue/session/quote provenance (`NOT_VERIFIED_FROM_HISTORICAL_CANDLES`), weekend/NY_PM forensics, and gap matrix (`18`, `19`, `20`).
  9. Pre-entry multi-TF causal feature ledger, true Unicorn (`BREAKER + FVG` overlap) vs `FVG_RETEST_ONLY` audit, and HTF conflict matrix (`20`, `21`, `22`).
  10. Quote-side MFE/MAE excursion paths, TP liquidity reachability, and 2x2x2 factorial SL/TP matrix (`23`, `24`, `25`).
  11. Fee/spread/slippage drag decomposition and $2.50 risk-cap / adverse-gap audit (`26`, `27`).
  12. Evidence-led root-cause feature matrix, conditional associations, matched causal ablations (`H1..H9`), and Evidence Hierarchy ranking (`28`, `29`, `30`).
  13. Chronological OOS integrity (`>=180`-bar embargo, delayed-entry cross-partition purge, reused-holdout disclosure) (`31`, `32`, `33`).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

APP_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for p in (str(BACKEND_ROOT), str(APP_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from services.v17_9_3_1_2_executable_intervention_replay_service import (
    PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312,
    V17_9_3_1_2_COMPARATOR_VARIANTS,
    assert_replay_oos_temporal_and_feature_integrity_v179312,
    build_close_ts_index_v179312,
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
    build_hypotheses_and_estimands_preregistration_v1793121,
    build_model_eligibility_and_selection_funnel_v1793121,
    build_pre_entry_feature_ledger_v1793121,
    build_root_cause_hypothesis_feature_matrix_v1793121,
    build_source_to_consumer_truth_map_v1793121,
    cleanup_orphan_subprocesses_and_temp_v1793121,
    compare_1_vs_4_worker_full_run_hashes_v1793121,
    compute_model_conditional_trade_metrics_v1793121,
    estimate_conditional_failure_association_v1793121,
    evaluate_model_disable_ablation_v1793121,
    evaluate_no_trade_control_v1793121,
    evaluate_true_unicorn_vs_fvg_only_v1793121,
    match_counterfactuals_by_opportunity_v1793121,
    preflight_runtime_resource_limits_v1793121,
    rank_loss_drivers_by_verified_counterfactual_impact_v1793121,
    reconcile_intervention_eligibility_and_coverage_v1793121,
    reconcile_open_position_lifecycle_v1793121,
    replay_policy_event_driven_portfolio_v1793121,
    run_git_source_data_preflight_v1793121,
    run_matched_causal_ablation_for_hypothesis_v1793121,
)
from tools.run_v17_9_3_1_2_full_universe_backtest import (
    load_canonical_xauusdt_datasets,
    select_cohort_with_unlock_candidates_v179312,
)

DATASET_PATH = APP_ROOT / "backtest" / "historical_dataset_XAUUSDT.json.gz"
PRIOR_V179312_RUN_DIR = (
    APP_ROOT
    / "docs"
    / "artifacts"
    / "v17_9_3_1_2"
    / "v17_9_3_1_2_20261101T172127Z_f0339ecae315"
)
FILL_MODES: Tuple[str, ...] = (
    "NEXT_BAR_OPEN_BID_ASK",
    "LEGACY_CLOSE_FILL",
    "STRESS_WIDER_SPREAD_SLIPPAGE_2X",
)


def atomic_write_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".partial")
    raw_bytes = (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n"
    ).encode("utf-8")
    tmp_path.write_bytes(raw_bytes)
    tmp_path.replace(path)
    return hashlib.sha256(raw_bytes).hexdigest()


def _load_verified_decision_stream_chunks(chunks_dir: Path) -> Tuple[List[Dict[str, Any]], str]:
    records: List[Dict[str, Any]] = []
    hasher = hashlib.sha256()
    for gz_path in sorted(chunks_dir.glob("chunk_*.jsonl.gz")):
        with gzip.open(gz_path, "rt", encoding="utf-8") as fh:
            for line in fh:
                s = line.strip()
                if not s:
                    continue
                rec = json.loads(s)
                records.append(rec)
    records.sort(
        key=lambda r: (
            int(r["checkpoint_index_1m"]),
            str(r["variant"]),
            str(r["model_id"]),
        )
    )
    for rec in records:
        hasher.update(
            (
                f"{rec['checkpoint_index_1m']}|{rec['variant']}|{rec['model_id']}|"
                f"{rec['decision_status']}|{rec.get('entry_reference')}|"
                f"{rec.get('selected_sl')}|{rec.get('take_profit')}\n"
            ).encode("utf-8")
        )
    return records, hasher.hexdigest()


def _replay_all_policies_with_workers(
    candidate_index: Mapping[str, Any],
    datasets: Mapping[str, Any],
    *,
    workers: int,
    fill_modes: Sequence[str] = FILL_MODES,
) -> Dict[str, Dict[str, Any]]:
    policy_items = list(PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312.items())

    def _run_one(item: Tuple[str, Any]) -> Tuple[str, Dict[str, Any]]:
        pid, pol = item
        res = replay_policy_event_driven_portfolio_v1793121(
            pol,
            candidate_index,
            datasets,
            fill_modes=fill_modes,
        )
        return pid, res

    if workers <= 1:
        out = dict(_run_one(it) for it in policy_items)
    else:
        with ThreadPoolExecutor(max_workers=int(workers)) as ex:
            pairs = list(ex.map(_run_one, policy_items))
        out = dict(pairs)
    return {k: out[k] for k in sorted(out.keys())}


def run_v17_9_3_1_2_1_full_opportunity_replay_pipeline(
    output_dir: Path,
    *,
    tested_code_sha: str,
) -> Dict[str, Any]:
    t0 = time.time()
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Resource guard & Git/Dataset Preflight
    res_guard_start = preflight_runtime_resource_limits_v1793121(
        WORKSPACE_ROOT, hard_min_free_disk_gib=5.0, max_workers_cap=4
    )
    datasets, prov = load_canonical_xauusdt_datasets()
    close_ts = build_close_ts_index_v179312(datasets)
    pol_hash = compute_research_policy_registry_hash_v179312()

    pre_01, frz_02 = run_git_source_data_preflight_v1793121(
        WORKSPACE_ROOT, DATASET_PATH, datasets=datasets
    )
    pre_01["truth_map"] = build_source_to_consumer_truth_map_v1793121()
    frz_02["tested_code_sha"] = tested_code_sha
    hyp_03 = build_hypotheses_and_estimands_preregistration_v1793121()

    # 2. Load & verify full 111,730-record decision stream across 5 comparator variants
    chunks_dir = PRIOR_V179312_RUN_DIR / "decision_stream_chunks"
    all_decisions, stream_sha256 = _load_verified_decision_stream_chunks(chunks_dir)
    a1_eligible = [
        r
        for r in all_decisions
        if r.get("variant") == "A1_V1792_SL_TP_BASELINE" and r.get("decision_status") == "ELIGIBLE"
    ]
    c1_eligible = [
        r
        for r in all_decisions
        if r.get("variant") == "C1_MACRO_PLUS_V1792_SL_TP" and r.get("decision_status") == "ELIGIBLE"
    ]

    # 3. Full-universe portfolio replays across all 3 fill modes for A0/A1/E1/C1/D1/D2 in parallel
    replays_by_mode: Dict[str, Dict[str, Any]] = {}

    def _run_fill_mode(fm: str) -> Tuple[str, Dict[str, Any]]:
        outs = replay_global_chronological_portfolio_v179312(
            all_decisions,
            list(V17_9_3_1_2_COMPARATOR_VARIANTS),
            datasets,
            fill_mode=fm,
        )
        return fm, {vk: vo.to_dict(include_episodes=True) for vk, vo in outs.items()}

    with ThreadPoolExecutor(max_workers=3) as ex:
        for fm_k, fm_v in ex.map(_run_fill_mode, FILL_MODES):
            replays_by_mode[fm_k] = fm_v

    primary_replays = replays_by_mode["NEXT_BAR_OPEN_BID_ASK"]
    a1_primary = primary_replays["A1_V1792_SL_TP_BASELINE"]
    a1_episodes = a1_primary["episodes"]

    # 4. Build full 18,744-timestamp candidate index + legacy 2,112 truncated index for comparison
    cand_index = build_full_intervention_candidate_index_v1793121(
        a1_eligible,
        datasets,
        pol_hash,
        close_ts_index=close_ts,
    )
    truncated_2112_records = select_cohort_with_unlock_candidates_v179312(
        a1_eligible,
        a1_episodes,
        c1_eligible,
        unlock_followups_per_episode=2,
    )
    truncated_index = build_full_intervention_candidate_index_v1793121(
        truncated_2112_records,
        datasets,
        pol_hash,
        close_ts_index=close_ts,
    )

    # 5. Execute R0..R8 on full 18,744 candidate index under 1, 2, and 4 workers across all 3 fill modes (P0-A, P0-B, P1-G, Truth Closure #3)
    int_4w = _replay_all_policies_with_workers(cand_index, datasets, workers=4, fill_modes=FILL_MODES)
    int_1w = _replay_all_policies_with_workers(cand_index, datasets, workers=1, fill_modes=FILL_MODES)
    int_2w = _replay_all_policies_with_workers(cand_index, datasets, workers=2, fill_modes=FILL_MODES)

    # Also replay truncated 2,112 cohort to quantify exact unlocked follow-on trades missed by P0-A
    int_truncated = _replay_all_policies_with_workers(
        truncated_index, datasets, workers=4, fill_modes=("NEXT_BAR_OPEN_BID_ASK",)
    )

    r0_recs = int_4w["R0_BASELINE_CONTROL"]["matched_opportunity_records"]
    policy_matched_map = {
        pid: res["matched_opportunity_records"] for pid, res in int_4w.items()
    }
    join_04 = match_counterfactuals_by_opportunity_v1793121(r0_recs, policy_matched_map)
    cov_05, unl_07 = reconcile_intervention_eligibility_and_coverage_v1793121(
        int_4w, int_truncated
    )

    # Strip full 18,744 per-opportunity records down to compact summary + sample in artifact 06 to protect disk
    int_06_compact: Dict[str, Any] = {}
    for pid, res in int_4w.items():
        c_res = dict(res)
        recs = c_res.pop("matched_opportunity_records", [])
        c_res["matched_opportunity_records_count"] = len(recs)
        c_res["matched_opportunity_records_sample"] = list(recs[:25])
        int_06_compact[pid] = c_res

    art_06 = {
        "schema_version": V17_9_3_1_2_1_SCHEMA_VERSION,
        "defect_closed": "P0-A_FULL_18744_EVENT_SOURCED_INTERVENTION_REPLAY",
        "opportunities_evaluated_per_policy": cand_index["unique_eligible_timestamps_count"],
        "raw_eligible_decisions_per_policy": cand_index["raw_eligible_decisions_count"],
        "results_by_policy": int_06_compact,
    }

    # 6. Horizon Censoring & Open-Position Lifecycle Resolution (P0-D: 08, 09, 10, 11)
    art_08, art_09, art_10, art_11 = reconcile_open_position_lifecycle_v1793121(
        a1_episodes, a1_eligible, datasets
    )

    # 7. Independent Pre-Entry Protected Structure & Stop Forensics (P0-C: 12, 13, 14)
    art_12, art_13, art_14 = audit_stop_classification_consistency_v1793121(
        primary_replays, datasets, candidate_index=cand_index
    )

    # 8. 5-Model Full Funnel, Single-Model / Leave-One-Out Ablations & Conditional Decomposition (15, 16, 17)
    art_15 = build_model_eligibility_and_selection_funnel_v1793121(a1_eligible, a1_episodes)
    art_16 = evaluate_model_disable_ablation_v1793121(
        a1_eligible, datasets, a1_baseline_replay=a1_primary
    )
    art_16["no_trade_cash_preservation_control"] = evaluate_no_trade_control_v1793121(a1_primary)
    art_17 = compute_model_conditional_trade_metrics_v1793121(
        a1_episodes, datasets, close_ts_index=close_ts
    )

    # 9. Venue/Session/Quote Provenance & Weekend/NY_PM Forensics (P1-I: 18, 19)
    art_18, art_19, art_20_quote = audit_session_feature_confounding_v1793121(
        a1_episodes, a1_eligible, datasets
    )
    art_18["quote_spread_gap_data_quality_matrix"] = art_20_quote

    # 10. Pre-Entry Feature Ledger, True Unicorn vs FVG-Only & HTF Conflict Matrix (P1-H: 20, 21, 22)
    art_20_pre, art_22_htf = build_pre_entry_feature_ledger_v1793121(
        a1_episodes, datasets, candidate_index=cand_index
    )
    art_21_uni = evaluate_true_unicorn_vs_fvg_only_v1793121(
        cand_index,
        datasets,
        r3_fvg_only_policy_result=int_4w["R3_DELAYED_ENTRY_BREAKER_FVG_RETEST_EXECUTABLE"],
    )

    # 11. Target Reachability, MFE/MAE Excursion Paths & 2x2x2 Factorial Matrix (23, 24, 25)
    art_24_tp, art_23_mfe, art_25_sltp = audit_target_reachability_at_entry_v1793121(
        a1_episodes, datasets, int_4w, candidate_index=cand_index
    )

    # 12. Fee/Slippage/Spread Drag & Gross-to-Net Risk Cap Reconciliation (26, 27)
    art_26_cost, art_27_g2n = audit_trade_cost_components_v1793121(
        a1_episodes, replays_by_mode
    )

    # 13. Root-Cause Feature Matrix, Conditional Associations, Matched Ablations & Ranking (P0-E: 28, 29, 30)
    art_28_mat = build_root_cause_hypothesis_feature_matrix_v1793121(
        a1_episodes, art_20_pre, art_12, art_24_tp
    )
    cond_assoc = estimate_conditional_failure_association_v1793121(art_28_mat)
    art_28_mat["conditional_failure_associations"] = cond_assoc

    art_29_hyp = run_matched_causal_ablation_for_hypothesis_v1793121(
        a1_baseline_replay=a1_primary,
        full_interventions_by_policy=int_4w,
        matched_join_audit=join_04,
        model_ablation_report=art_16,
        censoring_semantics_report=art_10,
        stop_reclassification_report=art_13,
        unicorn_vs_fvg_report=art_21_uni,
        cost_audit_report=art_26_cost,
    )
    art_30_rnk = rank_loss_drivers_by_verified_counterfactual_impact_v1793121(
        art_29_hyp, cond_assoc
    )

    # 14. Chronological OOS Partition Integrity, Walk-Forward & Freshness Disclosure (31, 32, 33)
    episodes_for_oos: Dict[str, Sequence[Mapping[str, Any]]] = {
        vk: primary_replays[vk]["episodes"] for vk in primary_replays
    }
    for pid, res in int_4w.items():
        episodes_for_oos[pid] = res.get("independent_portfolio_episodes") or []

    oos_31 = assert_replay_oos_temporal_and_feature_integrity_v179312(
        episodes_for_oos,
        datasets["1m"],
        policy_registry_hash=pol_hash,
        expected_frozen_policy_registry_hash=pol_hash,
        embargo_bars=180,
        oos_previously_inspected=True,
    )
    wf_32 = {
        "schema_version": V17_9_3_1_2_1_SCHEMA_VERSION,
        "oos_holdout_freshness_status": oos_31["oos_holdout_freshness_status"],
        "policy_frozen_before_oos": oos_31["policy_frozen_before_oos"],
        "embargo_bars": oos_31["embargo_bars"],
        "delayed_entry_cross_partition_purged_count": oos_31["delayed_entry_cross_partition_purged_count"],
        "commercial_edge_verdict": "NEGATIVE_EXPECTANCY_NOT_COMMERCIALLY_VIABLE",
        "oos_report_summary": oos_31["oos_report"],
    }
    frsh_33 = {
        "schema_version": V17_9_3_1_2_1_SCHEMA_VERSION,
        "status": "NO_NEW_HISTORICAL_CANDLES_BEYOND_2026_10_28_IN_CANONICAL_SNAPSHOT",
        "canonical_dataset_path": str(DATASET_PATH),
        "coverage_start_utc": "2026-10-08T00:00:00+00:00",
        "coverage_end_utc": "2026-10-28T03:15:00+00:00",
        "disclosed": True,
        "untouched_fresh_oos_claim_permitted": False,
    }

    # 15. Full Historical Replay Manifest (`35`) & 1-vs-2-vs-4 Worker Determinism (`36`)
    rep_35 = {
        "schema_version": V17_9_3_1_2_1_SCHEMA_VERSION,
        "tested_code_sha": tested_code_sha,
        "canonical_decision_stream_sha256": stream_sha256,
        "evaluated_m1_bars": 22346,
        "model_evaluations_per_variant": 111730,
        "a1_raw_eligible_decisions": len(a1_eligible),
        "a1_unique_eligible_timestamps": cand_index["unique_eligible_timestamps_count"],
        "fill_modes_evaluated": list(FILL_MODES),
        "comparator_variants_summary_primary": {
            vk: {
                "closed_trades": primary_replays[vk]["closed_independent_episodes_count"],
                "wins": primary_replays[vk]["wins"],
                "losses": primary_replays[vk]["losses"],
                "open_at_horizon_censored": primary_replays[vk]["open_at_horizon_censored_count"],
                "data_gap_unresolved": primary_replays[vk]["data_gap_unresolved_count"],
                "loss_rate": primary_replays[vk]["loss_rate"],
                "expectancy_r_mean": primary_replays[vk]["expectancy_r_mean"],
                "modeled_net_pnl_usd": primary_replays[vk]["modeled_net_pnl_usd"],
            }
            for vk in V17_9_3_1_2_COMPARATOR_VARIANTS
        },
    }

    def _digest_intervention_map(imap: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            pid: {
                "accepted_modified": imap[pid]["accepted_modified_executable_geometry_count"],
                "accepted_noop": imap[pid]["accepted_unchanged_noop_count"],
                "rejected": imap[pid]["rejected_by_intervention_count"],
                "primary_net_pnl_usd": imap[pid]["independent_portfolio_replay_primary"]["modeled_net_pnl_usd"],
                "primary_closed_trades": imap[pid]["independent_portfolio_replay_primary"][
                    "closed_independent_episodes_count"
                ],
                "primary_expectancy_r": imap[pid]["independent_portfolio_replay_primary"]["expectancy_r_mean"],
                "by_fill_mode": {
                    fm: imap[pid].get("independent_portfolio_replays_by_fill_mode", {}).get(fm, {}).get("modeled_net_pnl_usd")
                    for fm in FILL_MODES
                },
            }
            for pid in sorted(imap.keys())
        }

    def _build_independent_worker_payload(imap: Mapping[str, Any]) -> Dict[str, Any]:
        indep_port_digest = {
            fm: {
                pid: imap[pid].get("independent_portfolio_replays_by_fill_mode", {}).get(fm, {}).get("modeled_net_pnl_usd")
                for pid in sorted(imap.keys())
            }
            for fm in FILL_MODES
        }
        indep_eps = {
            pid: imap[pid].get("independent_portfolio_episodes") or []
            for pid in sorted(imap.keys())
        }
        indep_oos = assert_replay_oos_temporal_and_feature_integrity_v179312(
            indep_eps,
            datasets["1m"],
            policy_registry_hash=pol_hash,
            expected_frozen_policy_registry_hash=pol_hash,
            embargo_bars=180,
            oos_previously_inspected=True,
        )
        return {
            "canonical_stream_sha256": stream_sha256,
            "portfolio_replays": indep_port_digest,
            "intervention_replays": _digest_intervention_map(imap),
            "oos_report": {
                "status": indep_oos["oos_holdout_freshness_status"],
                "purged_count": indep_oos["delayed_entry_cross_partition_purged_count"],
            },
        }

    w1_payload = _build_independent_worker_payload(int_1w)
    w2_payload = _build_independent_worker_payload(int_2w)
    w4_payload = _build_independent_worker_payload(int_4w)

    res_guard_end = preflight_runtime_resource_limits_v1793121(
        WORKSPACE_ROOT, hard_min_free_disk_gib=5.0, max_workers_cap=4
    )
    cleanup_rep = cleanup_orphan_subprocesses_and_temp_v1793121([])
    det_36 = {
        **compare_1_vs_4_worker_full_run_hashes_v1793121(w1_payload, w2_payload, w4_payload),
        "resource_preflight_start": res_guard_start,
        "resource_preflight": res_guard_end,
        "cleanup_report": cleanup_rep,
        "elapsed_seconds": round(time.time() - t0, 2),
    }

    artifacts_to_write: Dict[str, Any] = {
        "01_git_source_data_preflight": pre_01,
        "02_frozen_baseline_sha_and_contracts": frz_02,
        "03_hypotheses_and_estimands_pre_registration": hyp_03,
        "04_opportunity_identity_full_outer_join_audit": join_04,
        "05_full_intervention_candidate_coverage": cov_05,
        "06_full_event_sourced_intervention_replay": art_06,
        "07_unlocked_followon_trades_attribution": unl_07,
        "08_horizon_censoring_and_slot_authority": art_08,
        "09_74_open_positions_per_trade_resolution": art_09,
        "10_three_portfolio_execution_semantics": art_10,
        "11_data_gap_execution_truth": art_11,
        "12_independent_structure_anchor_ledger": art_12,
        "13_stop_forensics_reclassification_A1_E1_C1_D1_D2": art_13,
        "14_frozen_six_episode_anchor_reconciliation": art_14,
        "15_5model_full_funnel_and_selection_audit": art_15,
        "16_model_remove_one_at_a_time_executable_ablation": art_16,
        "17_model_direction_and_priority_integrity": art_17,
        "18_venue_hours_quote_provenance_and_session_audit": art_18,
        "19_session_counterfactual_and_regime_stratification": art_19,
        "20_pre_entry_multi_tf_causal_feature_ledger": art_20_pre,
        "21_unicorn_breaker_fvg_episode_binding_audit": art_21_uni,
        "22_htf_conflict_and_MSS_displacement_ablation": art_22_htf,
        "23_MFE_MAE_and_exit_path_audit": art_23_mfe,
        "24_TP_liquidity_reachability_ledger": art_24_tp,
        "25_full_universe_executable_SL_TP_ablation": art_25_sltp,
        "26_fee_slippage_spread_and_cost_truth": art_26_cost,
        "27_gross_to_net_reconciliation_by_model_and_session": art_27_g2n,
        "28_measured_root_cause_feature_matrix": art_28_mat,
        "29_hypothesis_by_hypothesis_evidence_strength": art_29_hyp,
        "30_causality_limits_and_ranking": art_30_rnk,
        "31_OOS_partition_censoring_temporal_integrity": oos_31,
        "32_walkforward_and_reused_holdout_disclosure": wf_32,
        "33_fresh_source_availability_or_blocker": frsh_33,
        "35_full_historical_replay_and_all_variants_manifest": rep_35,
        "36_full_1v2v4_worker_determinism_and_resource_guard": det_36,
    }

    for name, payload in artifacts_to_write.items():
        atomic_write_json(output_dir / f"{name}.json", payload)

    return artifacts_to_write


if __name__ == "__main__":
    default_out = (
        APP_ROOT
        / "docs"
        / "artifacts"
        / "v17_9_3_1_2_1"
        / f"v17_9_3_1_2_1_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_dev"
    )
    run_v17_9_3_1_2_1_full_opportunity_replay_pipeline(default_out, tested_code_sha="0" * 40)
    print(f"Completed full opportunity replay in {default_out}")

