"""
aurum-desk/backend/tests/test_kh03_r2_verifier_mutations.py

KH-03.R2 Separate-OS Independent Verifier Mutation-Resistance Suite (16 NEW Attacks):
Constructs a clean synthetic valid KH-03.R2 run folder in `tmp_path` (NEVER touches
any sealed repo run), verifies exit code 0 on the clean clone, then mutates 16 distinct
scientific/integrity fields in isolated clones and proves `kh_03_r2_independent_verifier.py`
exits non-zero with exact violation tokens!
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Callable, Dict, List, Tuple
import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_prospective_data_service import CANONICAL_DATASET_SHA256_KH02
from services.kh_03_fee_integrity_service import EXPECTED_SIX_SCENARIOS_KH03
from services.kh_03_r2_methodology_service import (
    KH03_R2_H3_ADVISORY_ARM,
    SCHEMA_VERSION_KH03_R2,
    SPEC_VERSION_KH03_R2,
    evaluate_kh03_dev_ci_and_advancement,
    validate_kh03_r2_h3_ledger_independently,
)
from services.kh_03_research_service import KH03_PRIMARY_ARMS
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tools.kh_02_r2_c3_independent_verifier import _audit_memory_samples_c3
from tools.kh_03_r2_independent_verifier import recompute_kh03_r2_28_gates_and_verdicts


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build_synthetic_valid_kh03_r2_run_dir(run_dir: Path) -> Tuple[str, str]:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "junit").mkdir(exist_ok=True)

    tsha = "e" * 40
    rid = f"20261123T150000Z_{tsha[:12]}"

    # 07: Frozen H3 PATH B Policy Choice
    f07: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R2,
        "schema_version": SCHEMA_VERSION_KH03_R2,
        "frozen_before_r2_h3_recomputation": True,
        "tested_code_sha": tsha,
        "chosen_path": "PATH_B_FILTER_ONLY_RESEARCH_ADVISORY",
        "H3_ECONOMIC_CLAIM_TIER": "H3_FILL_TIME_FILTER_ONLY_RESEARCH_ADVISORY",
        "H3_RESIZE_EXECUTION_STATUS": "NOT_VERIFIABLE_WITH_M1",
        "H3_FILTER_ONLY_RESEARCH": "PASS",
        "H3_EXECUTABLE_EDGE_CANDIDATE_ELIGIBLE": False,
        "authoritative_simulator_mutated": False,
        "dataset_execution_feasibility_audit": {"observed_l1_bbo_available": False, "historical_quote_mode": "MODELED_BID_ASK"},
        "status": "PASS",
    }
    f07["policy_choice_frozen_sha256"] = canonical_hash(
        {k: v for k, v in f07.items() if k not in ("policy_choice_frozen_sha256", "artifact_canonical_sha256")}
    )

    # 08: Streaming H3 advisory ledger (1 valid closed LOSS row, size_applied_to_execution=False)
    h3_gz = run_dir / "08_h3_per_episode_policy_qty_time_ledger.jsonl.gz"
    h3_row = {
        "episode_uid": "KH03_R2_H3_ADV|1791500000000|opp_1|0",
        "opportunity_id": "opp_1",
        "direction": "LONG",
        "policy_mode": "H3_FILL_TIME_FILTER_ONLY_RESEARCH_ADVISORY",
        "decided_at_ms": 1791500000000,
        "observed_at_ms": 1791500060000,
        "assumed_tradeable_at_ms": None,
        "decision_data_cutoff_ms": 1791500000000,
        "execution_data_cutoff_ms": 1791500060000,
        "entry_price_origin": "R1_FILTER_ONLY_COMPATIBILITY_NEXT_BAR_OPEN_MODELED_BID_ASK",
        "fill_time_action": "RESIZED_CAUSALLY_AT_FILL_TIME",
        "original_qty": 0.42,
        "proposed_final_qty": 0.41,
        "actual_simulated_qty": 0.42,
        "advisory_qty_delta": -0.01,
        "size_applied_to_execution": False,
        "filled": True,
        "outcome_label": "LOSS",
        "planned_net_risk_usd": 2.45,
        "realized_gross_pnl_usd": -1.443,
        "entry_fee_usd": 0.5058,
        "exit_fee_usd": 0.5052,
        "total_fees_usd": 1.011,
        "accrued_open_entry_fee_usd": 0.0,
        "realized_net_pnl_usd": -2.454,
        "gross_fee_net_residual_usd": 0.0,
        "status": "ADVISORY_ONLY_SIZE_NOT_APPLIED_TO_EXECUTION",
    }
    ln = json.dumps(h3_row, sort_keys=True, separators=(",", ":"))
    with gzip.open(h3_gz, "wt", encoding="utf-8") as gz:
        gz.write(ln + "\n")

    f09: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R2,
        "policy_choice_frozen_sha256": f07["policy_choice_frozen_sha256"],
        "chosen_path": "PATH_B_FILTER_ONLY_RESEARCH_ADVISORY",
        "H3_ECONOMIC_CLAIM_TIER": "H3_FILL_TIME_FILTER_ONLY_RESEARCH_ADVISORY",
        "H3_RESIZE_EXECUTION_STATUS": "NOT_VERIFIABLE_WITH_M1",
        "H3_FILTER_ONLY_RESEARCH": "PASS",
        "H3_EXECUTABLE_EDGE_CANDIDATE_ELIGIBLE": False,
        "size_applied_to_execution_any_row": False,
        "linked_advisory_ledger": {
            "ledger_filename": h3_gz.name,
            "ledger_file_sha256": _sha256_file(h3_gz),
            "total_episode_rows": 1,
            "episode_uids_sha256": canonical_hash([h3_row["episode_uid"]]),
            "canonical_rows_sha256": canonical_hash([hashlib.sha256(ln.encode("utf-8")).hexdigest()]),
        },
        "legacy_r1_filter_only_compatibility_summary": {
            "scheduled": 1, "closed": 1, "wins": 0, "losses": 1, "open_at_horizon": 0,
            "gross_pnl_usd": -1.443, "total_fees_usd": 1.011, "accrued_open_entry_fees_usd": 0.0,
            "net_pnl_usd": -2.454, "expectancy_r": -1.0016, "max_gross_fee_net_residual_usd": 0.0,
            "overlap_violations": 0,
        },
        "status": "PASS",
    }

    # 11 & 13: Valid expanding forward-only folds (2 folds, embargo 240m, valid selection freeze SHA)
    t0 = 1791400000000
    emb = 240 * 60_000
    folds_11 = [
        {
            "fold_id": "R2_FWD_FOLD_1", "role": "FORWARD_ONLY_DEV_VALIDATION", "is_fresh_oos": False,
            "train_min_ts_ms": t0, "train_max_known_at_ts_ms": t0 + 100 * 60_000,
            "train_last_label_exit_ts_ms": t0 + 90 * 60_000, "future_train_bars_count": 0,
            "embargo_start_ms": t0 + 100 * 60_000, "embargo_end_ms": t0 + 100 * 60_000 + emb,
            "embargo_minutes": 240, "val_min_ts_ms": t0 + 100 * 60_000 + emb,
            "val_max_ts_ms": t0 + 200 * 60_000 + emb,
        },
        {
            "fold_id": "R2_FWD_FOLD_2", "role": "FORWARD_ONLY_DEV_VALIDATION", "is_fresh_oos": False,
            "train_min_ts_ms": t0, "train_max_known_at_ts_ms": t0 + 250 * 60_000 + emb,
            "train_last_label_exit_ts_ms": t0 + 240 * 60_000 + emb, "future_train_bars_count": 0,
            "embargo_start_ms": t0 + 250 * 60_000 + emb, "embargo_end_ms": t0 + 250 * 60_000 + 2 * emb,
            "embargo_minutes": 240, "val_min_ts_ms": t0 + 250 * 60_000 + 2 * emb,
            "val_max_ts_ms": t0 + 350 * 60_000 + 2 * emb,
        },
    ]
    replays_13 = []
    for f in folds_11:
        sf = {
            "fold_id": f["fold_id"],
            "tested_code_sha": tsha,
            "selected_arm_on_past_train_only": "KH03_H1_MTF_UNSWEPT_ALIGN",
            "selection_frozen_before_validation_replay": True,
        }
        sf["selection_freeze_sha256"] = canonical_hash(sf)
        replays_13.append(
            {
                "fold_id": f["fold_id"],
                "fold_local_selection_freeze": sf,
                "static_preregistered_arm_validation_results": {"KH03_H1_MTF_UNSWEPT_ALIGN": {"summary": {"closed": 10}}},
            }
        )

    # 14: Raw arm stats matching evaluate_kh03_dev_ci_and_advancement strictly
    prim_14: Dict[str, Any] = {}
    match_14: Dict[str, Any] = {}
    for ac in KH03_PRIMARY_ARMS:
        is_h3 = (ac == KH03_R2_H3_ADVISORY_ARM)
        mr = {
            "matching_quality_status": "BALANCED_COMMON_SUPPORT",
            "matched_mean_delta_r_vs_null": -0.10,
            "expectancy_r_vs_placebo_shuffled": -0.05,
            "expectancy_r_vs_time_offset": -0.04,
        }
        match_14[ac] = mr
        raw_ci = [-1.01, -0.85]
        ev = evaluate_kh03_dev_ci_and_advancement(
            arm_code=ac, closed_trades_1x=75, effective_ny_days_1x=14,
            expectancy_r_1x=-0.95, net_pnl_usd_1x=-180.0, expectancy_r_fee_only_0p0006=-0.85,
            expectancy_r_2x=-1.01, raw_ny_day_95ci_r=raw_ci, holm_bonferroni_adjusted_pvalue=1.0,
            matching_quality_status=mr["matching_quality_status"], matched_mean_delta_r_vs_null=mr["matched_mean_delta_r_vs_null"],
            expectancy_r_vs_placebo_shuffled=mr["expectancy_r_vs_placebo_shuffled"], expectancy_r_vs_time_offset=mr["expectancy_r_vs_time_offset"],
            wf_median_fold_expectancy_r=-0.95, wf_positive_fold_ratio=0.0, wf_valid_forward_only_folds_count=2,
            executable_edge_eligible=(not is_h3),
            disqualification_reason=("H3_DISQUALIFIED_PATH_B_FILTER_ONLY_NOT_EXECUTABLE_WITH_M1" if is_h3 else None),
        )
        prim_14[ac] = {
            "closed_trades_1x": 75, "effective_ny_days_1x": 14, "expectancy_r_1x": -0.95, "net_pnl_usd_1x": -180.0,
            "expectancy_r_fee_only_0p0006": -0.85, "expectancy_r_2x": -1.01, "ny_day_cluster_95ci_r": raw_ci,
            "holm_bonferroni_adjusted_pvalue": 1.0, "forward_only_wf_median_fold_expectancy_r": -0.95,
            "forward_only_wf_positive_fold_ratio": 0.0, "executable_edge_eligible": (not is_h3),
            "ci_parsed": ev["ci_parsed"], "ci_schema_valid": ev["ci_schema_valid"],
            "failed_advancement_criteria": ev["failed_advancement_criteria"],
            "passes_preregistered_dev_advancement": ev["passes_preregistered_dev_advancement"],
        }

    # 15: 6 scenarios with valid reconciled fees
    scen_15 = {
        sc: {"arms": {"KH03_H1_MTF_UNSWEPT_ALIGN": {"closed": 10, "gross_pnl_usd": -10.0, "total_fees_usd": 12.0, "net_pnl_usd": -22.0}}}
        for sc in EXPECTED_SIX_SCENARIOS_KH03
    }

    # Memory telemetry log & JUnit XML
    mem_gz = run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    with gzip.open(mem_gz, "wt", encoding="utf-8") as mgz:
        for i in range(4):
            mgz.write(json.dumps({"ts_ms": 1791500000000 + i * 500, "available_ram_bytes": 4_000_000_000, "available_mib": 3814.7, "child_count": 2, "worker_pids": [101, 102], "disk_free_gib": 35.0}) + "\n")

    n_tc = 5600
    c_ids = [f"backend/tests/test_kh03_r2_synth.py::test_case_{i:04d}" for i in range(n_tc)]
    xml_path = run_dir / "junit" / "SHARD_01.xml"
    tcs = "".join(f'<testcase classname="backend.tests.test_kh03_r2_synth" name="test_case_{i:04d}" time="0.001"/>' for i in range(n_tc))
    xml_path.write_text(f'<?xml version="1.0" encoding="utf-8"?><testsuite tests="{n_tc}" failures="0" errors="0" skipped="0">{tcs}</testsuite>', encoding="utf-8")

    docs: Dict[str, Dict[str, Any]] = {
        "00": {
            "spec_version": SPEC_VERSION_KH03_R2, "run_id": rid, "branch": "feature/v17-9-2-structural-sl-tp-from-qa",
            "start_head_sha": tsha, "end_head_sha": tsha, "tested_executable_sha": tsha,
            "git_clean_at_start": True, "local_uncommitted_changes": False, "orchestrator_pid": -9999,
            "owner_disk_preflight_waiver": False,
            "strict_multi_mount_probe": {"can_run_heavy_stages": True, "owner_waiver_permitted": False, "probe_errors": [], "distinct_mounts": [{"free_gib": 35.0, "projected_post_reserve_gib": 34.0}]},
            "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
            "ACTIVATION_READY": "NO", "status": "PASS",
        },
        "01": {
            "status": "PASS", "locked_files_modified_count": 0, "forbidden_order_import_hits": [],
            "cost_assumptions_defaults_intact": True,
            "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
            "ACTIVATION_READY": "NO",
        },
        "02": {
            "status": "PASS", "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02,
            "canonical_dataset_sha256_match": True, "all_prior_sealed_artifacts_intact": True,
            "prior_sealed_artifacts_immutability": {"R1": {"match": True}},
            "c4_provenance_and_oos_boundary": {
                "network_capture_enabled": False, "live_network_capture_executed": False,
                "LIVE_NETWORK_VALIDATION": "NOT_RUN_BY_DESIGN", "real_oos_escrow_final_record_count": 0,
            },
        },
        "03": {"classification": "REAL_RED", "status": "PASS", "all_3_r1_arms_bug_reproduced": True},
        "04": {"status": "PASS", "total_boundary_cases": 14, "passed_boundary_cases": 14},
        "05": {"status": "PASS", "synthetic_fixtures_never_prove_real_edge": True},
        "06": {"classification": "REAL_RED", "status": "PASS", "real_dev_replayed_episodes_where_r1_discarded_final_qty_count": 156},
        "07": f07,
        "09": f09,
        "10": {"classification": "REAL_RED", "status": "PASS", "r1_total_future_train_bars_across_4_folds": 33000},
        "11": {"status": "PASS", "is_fresh_oos": False, "OOS_EVALUATIONS": 0, "valid_folds_count": 2, "folds": folds_11},
        "12": {
            "status": "PASS", "embargo_minutes": 240, "all_train_exits_strictly_le_embargo_start": True,
            "all_train_val_episode_overlaps_zero": True, "all_cross_fold_val_duplicates_zero": True,
            "total_overlap_violations_across_wf_replays": 0,
        },
        "13": {"status": "PASS", "future_win_injection_invariance_passed": True, "per_fold_selection_and_validation_replays": replays_13},
        "14": {
            "status": "PASS", "preregistered_hypothesis_budget_m": 3,
            "matching_disclosure": "NEVER proof that filtering causes alpha on reused DEV.",
            "primary_arm_evaluations": prim_14, "matching_reports": match_14,
            "negative_control_summaries_1x": {"NC_NULL_TRIGGER": {}, "NC_PLACEBO_SHUFFLED_TRIGGER": {}, "NC_TIME_OFFSET_SHIFT": {}},
        },
        "15": {
            "status": "PASS", "zero_fee_display_bugs_count": 0, "total_overlap_violations": 0,
            "ACCOUNT_EFFECTIVE_FEE_VERDICT": "UNKNOWN_NOT_LOCALLY_VERIFIED", "observed_l1_bbo_available": False,
            "scenarios": scen_15,
        },
        "16": {"status": "PASS", "planned_cap_is_physical_guarantee": False, "total_overlap_violations_1x": 0},
        "17": {
            "BASELINE_PARITY_VERDICT": "PASS", "per_decision_and_episode_fingerprints": {"all_sealed_fingerprints_exact_match": True},
            "a1_population_count": 56710, "a1_mismatches_count": 0, "si8_population_count": 6375, "si8_mismatches_count": 0,
            "three_adapter_population_count": 246, "three_adapter_mismatches_count": 0, "d4_historical_closed_trades": 0,
        },
        "18": {
            "status": "PASS", "collected_count": n_tc, "executed_count": n_tc, "passed_count": n_tc,
            "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0, "junit_bijection_exact": True,
            "collection_manifest": {"run_id": rid, "tested_executable_sha": tsha, "collected_node_ids_count": n_tc, "collected_node_ids": c_ids},
            "shard_manifest": {
                "run_id": rid, "tested_executable_sha": tsha, "nonzero_exit_shards_count": 0,
                "shards": [{"shard_id": "SHARD_01", "exit_code": 0, "timed_out": False, "reused": False, "junit_xml_path": "junit/SHARD_01.xml", "xml_sha256": _sha256_file(xml_path), "scheduled_node_ids": c_ids}],
            },
        },
        "19": {
            "status": "PASS",
            "pytest_controlled_subset_w1_w2_w4_repeat": {
                "all_4_runs_identical": True,
                "runs": [
                    {"mode_label": "w=1", "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64, "worker_pids": [1]},
                    {"mode_label": "w=2", "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64, "worker_pids": [2, 3]},
                    {"mode_label": "w=4", "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64, "worker_pids": [4, 5, 6, 7]},
                    {"mode_label": "w=1_repeat", "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64, "worker_pids": [8]},
                ],
            },
            "r2_science_slice_w1_w2_w4_repeat": {
                "all_4_runs_identical": True,
                "runs": [
                    {"mode_label": "w=1", "rows_count": 12062, "canonical_sorted_sha256": "2" * 64},
                    {"mode_label": "w=2", "rows_count": 12062, "canonical_sorted_sha256": "2" * 64},
                    {"mode_label": "w=4", "rows_count": 12062, "canonical_sorted_sha256": "2" * 64},
                    {"mode_label": "w=1_repeat", "rows_count": 12062, "canonical_sorted_sha256": "2" * 64},
                ],
            },
        },
        "20": {"min_disk_free_gib": 35.0, "min_available_ram_mib": 3800.0, "max_descendant_children": 4, "orphan_children_after_cleanup": 0, "sentinel_temp_cleaned_up": True, "owner_disk_preflight_waiver": False},
        "21": {"status": "PASS", "inherited_r1_controls_count": 150, "new_kh03_r2_controls_count": 24, "total_controls": 174, "unique_fault_ids": 174, "failed_controls": 0},
        "22": {"status": "PASS", "clean_clone_verifier_green": True, "mutations_tested_count": 16, "mutations_failed_detection_count": 0},
        "23": {
            "passing_dev_candidate_arms": [], "KH03_R2_DEV_CANDIDATE_VERDICT": "NO_ECONOMIC_ADVANCEMENT_CANDIDATE",
            "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE", "FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA",
            "KH04_ENTRY": "BLOCKED_NO_DEV_CANDIDATE", "LIVE_NETWORK_VALIDATION": "NOT_RUN_BY_DESIGN",
            "OOS_EVALUATIONS": 0, "ACTIVATION_READY": "NO",
            "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
        },
        "26": {"run_id": rid, "tested_executable_sha": tsha, "publication_status": "PRE_PUSH_PENDING", "remote_readback_sha256_verified": False, "owner_disk_preflight_waiver": False},
    }

    mem_aud = _audit_memory_samples_c3(mem_gz)
    h3_aud = validate_kh03_r2_h3_ledger_independently(h3_gz, f09)
    rec24 = recompute_kh03_r2_28_gates_and_verdicts(docs=docs, mem_audit=mem_aud, h3_ledger_audit=h3_aud, junit_bijection_ok=True, for_final_verifier_manifest=False)
    docs["24"] = {
        "spec_version": SPEC_VERSION_KH03_R2, "schema_version": SCHEMA_VERSION_KH03_R2,
        "run_id": rid, "tested_executable_sha": tsha,
        "overall_classification": rec24["overall_classification"], "gates_28": rec24["gates_28"], "verdicts": rec24["verdicts"],
    }

    fn_map = {
        "00": "00_branch_head_preflight_resource_and_sha.json",
        "01": "01_function_ast_ownership_and_immutable_diff.json",
        "02": "02_prior_R1_C4_and_canonical_dataset_immutability.json",
        "03": "03_ci_old_bug_red_proof.json",
        "04": "04_ci_strict_adapter_green_boundary_matrix.json",
        "05": "05_ci_independent_positive_negative_verdicts.json",
        "06": "06_h3_old_filter_only_vs_resize_red_proof.json",
        "07": "07_h3_policy_choice_and_execution_feasibility.json",
        "09": "09_h3_reconciliation_research_only_or_filter_only_disclosure.json",
        "10": "10_wf_old_posthoc_and_future_train_red_proofs.json",
        "11": "11_wf_forward_only_folds_train_val_timestamps.json",
        "12": "12_wf_purged_labels_embargo_episode_nonoverlap.json",
        "13": "13_wf_fold_local_selection_freeze_and_replay.json",
        "14": "14_statistical_ci_holm_matching_controls_recomputed.json",
        "15": "15_six_cost_full_rerisk_and_fee_integrity.json",
        "16": "16_realized_tail_loss_and_1position_portfolio.json",
        "17": "17_historical_baseline_parity_and_prior_hashes.json",
        "18": "18_full_pytest_collected_executed_junit_bijection.json",
        "19": "19_w1_w2_w4_w1repeat_timing_hashes.json",
        "20": "20_disk_ram_pid_temp_telemetry.json",
        "21": "21_inherited_plus_R2_new_unique_red_green_faults.json",
        "22": "22_independent_verifier_mutation_tests.json",
        "23": "23_economic_win_loss_dev_eligibility_and_KH04_gate.json",
        "24": "24_prepublication_28_gate_matrix.json",
        "26": "26_phase1_code_commit_push_receipt.json",
    }
    for k, fn in fn_map.items():
        (run_dir / fn).write_text(json.dumps(docs[k], indent=2, sort_keys=True), encoding="utf-8")
    (run_dir / "README_KH03_R2.md").write_text(f"# KH03 R2 {rid}\nACTIVATION_READY=NO\nNO_PROVEN_EDGE\nNOT_VERIFIABLE_WITH_M1\n", encoding="utf-8")
    (run_dir / "HANDOVER_KH03_R2_VI.txt").write_text(f"KH03 R2 {rid}\nACTIVATION_READY=NO\nNO_PROVEN_EDGE\nNOT_VERIFIABLE_WITH_M1\n", encoding="utf-8")
    return rid, tsha


def run_kh03_r2_verifier_subprocess(run_dir: Path, run_id: str, tested_sha: str) -> Tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)])
    p = subprocess.run(
        [
            sys.executable,
            str(APP_ROOT / "tools" / "kh_03_r2_independent_verifier.py"),
            "--run-dir", str(run_dir),
            "--expected-run-id", run_id,
            "--expected-tested-sha", tested_sha,
            "--skip-live-repo-pointers",
            "--no-write-manifest",
        ],
        cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=60,
    )
    return int(p.returncode), (p.stdout or "") + "\n" + (p.stderr or "")


def _mut_json(p: Path, fn: Callable[[Dict[str, Any]], None]) -> None:
    d = json.loads(p.read_text("utf-8"))
    fn(d)
    p.write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")


# 16 NEW KH-03.R2 Verifier Mutation Attacks (§08.1D)
KH03_R2_VERIFIER_MUTATIONS_SPECS: List[Tuple[str, Callable[[Path], None], str]] = [
    ("R2M01_FORGE_CI_BOUNDS_SWAPPED", lambda d: _mut_json(d / "14_statistical_ci_holm_matching_controls_recomputed.json", lambda o: o["primary_arm_evaluations"]["KH03_H1_MTF_UNSWEPT_ALIGN"].__setitem__("ny_day_cluster_95ci_r", [0.5, 0.1])), "RAW_CI_INVALID_SCHEMA"),
    ("R2M02_FORGE_DEV_ADVANCEMENT_TRUE", lambda d: _mut_json(d / "14_statistical_ci_holm_matching_controls_recomputed.json", lambda o: o["primary_arm_evaluations"]["KH03_H1_MTF_UNSWEPT_ALIGN"].__setitem__("passes_preregistered_dev_advancement", True)), "FORGED_DEV_ADVANCEMENT_BOOLEAN"),
    ("R2M03_FORGE_FALSE_CROSSES_ZERO_ON_NEG_CI", lambda d: _mut_json(d / "14_statistical_ci_holm_matching_controls_recomputed.json", lambda o: o["primary_arm_evaluations"]["KH03_H1_MTF_UNSWEPT_ALIGN"]["failed_advancement_criteria"].append("NY_DAY_95CI_CROSSES_ZERO:ci_low=None")), "FALSE_CROSSES_ZERO_ON_STRICTLY_NEGATIVE_CI"),
    ("R2M04_FORGE_H3_EXECUTION_STATUS_PASS", lambda d: _mut_json(d / "07_h3_policy_choice_and_execution_feasibility.json", lambda o: o.__setitem__("H3_RESIZE_EXECUTION_STATUS", "PASS")), "H3_RESIZE_EXECUTION_FALSE_CLAIM"),
    ("R2M05_FORGE_H3_SIZE_APPLIED_TRUE", lambda d: _mut_json(d / "09_h3_reconciliation_research_only_or_filter_only_disclosure.json", lambda o: o.__setitem__("size_applied_to_execution_any_row", True)), "H3_PATH_B_CLAIMED_SIZE_APPLIED_TO_EXECUTION"),
    ("R2M06_TAMPER_H3_LEDGER_FEE_SUMMARY", lambda d: _mut_json(d / "09_h3_reconciliation_research_only_or_filter_only_disclosure.json", lambda o: o["legacy_r1_filter_only_compatibility_summary"].__setitem__("total_fees_usd", 0.0)), "H3_LEDGER:H3_SUMMARY_FEES_MISMATCH"),
    ("R2M07_INJECT_AFTER_VAL_TRAIN_TIMESTAMP", lambda d: _mut_json(d / "11_wf_forward_only_folds_train_val_timestamps.json", lambda o: o["folds"][0].__setitem__("train_max_known_at_ts_ms", o["folds"][0]["val_min_ts_ms"] + 60_000)), "WF_CAUSALITY:TRAIN_NOT_STRICTLY_BEFORE_VAL"),
    ("R2M08_EMBARGO_239_MINUTES", lambda d: _mut_json(d / "11_wf_forward_only_folds_train_val_timestamps.json", lambda o: o["folds"][0].__setitem__("embargo_minutes", 239)), "WF_CAUSALITY:EMBARGO_MINUTES_BELOW_MIN"),
    ("R2M09_TAMPER_SELECTION_FREEZE_SHA", lambda d: _mut_json(d / "13_wf_fold_local_selection_freeze_and_replay.json", lambda o: o["per_fold_selection_and_validation_replays"][0]["fold_local_selection_freeze"].__setitem__("selection_freeze_sha256", "0" * 64)), "WF_SELECTION_FREEZE_SHA_TAMPERED"),
    ("R2M10_DROP_FOLD_RAW_RESULTS", lambda d: _mut_json(d / "13_wf_fold_local_selection_freeze_and_replay.json", lambda o: o["per_fold_selection_and_validation_replays"][0].__setitem__("static_preregistered_arm_validation_results", {})), "WF_MISSING_RAW_FOLD_RESULTS"),
    ("R2M11_TAMPER_1X_FEES_TO_ZERO_IN_15", lambda d: _mut_json(d / "15_six_cost_full_rerisk_and_fee_integrity.json", lambda o: o["scenarios"]["1X_BASE_0p0004"]["arms"]["KH03_H1_MTF_UNSWEPT_ALIGN"].__setitem__("total_fees_usd", 0.0)), "ZERO_FEE_BUG_IN_15"),
    ("R2M12_FORGE_PROVEN_EDGE_VERDICT", lambda d: _mut_json(d / "23_economic_win_loss_dev_eligibility_and_KH04_gate.json", lambda o: o.__setitem__("RESEARCH_ECONOMIC_VERDICT", "PROVEN_EDGE")), "FORGED_RESEARCH_ECONOMIC_VERDICT"),
    ("R2M13_FORGE_OOS_EVALUATIONS_ONE", lambda d: _mut_json(d / "23_economic_win_loss_dev_eligibility_and_KH04_gate.json", lambda o: o.__setitem__("OOS_EVALUATIONS", 1)), "NONZERO_OOS_EVALUATIONS_PEEK"),
    ("R2M14_FORGE_ACTIVATION_READY_YES", lambda d: _mut_json(d / "23_economic_win_loss_dev_eligibility_and_KH04_gate.json", lambda o: o.__setitem__("ACTIVATION_READY", "YES")), "FORGED_ACTIVATION_READY_ON"),
    ("R2M15_SWAP_TESTED_SHA_IN_26", lambda d: _mut_json(d / "26_phase1_code_commit_push_receipt.json", lambda o: o.__setitem__("tested_executable_sha", "1" * 40)), "TESTED_SHA_MISMATCH_ACROSS_00_24_26"),
    ("R2M16_FORGE_OBSERVED_L1_BBO_TRUE", lambda d: _mut_json(d / "15_six_cost_full_rerisk_and_fee_integrity.json", lambda o: o.__setitem__("observed_l1_bbo_available", True)), "FORGED_OBSERVED_L1_BBO_AVAILABLE_TRUE"),
]


def test_kh03_r2_clean_clone_passes_separate_os_verifier(tmp_path):
    cdir = tmp_path / "clean_r2"
    rid, tsha = build_synthetic_valid_kh03_r2_run_dir(cdir)
    rc, out = run_kh03_r2_verifier_subprocess(cdir, rid, tsha)
    assert rc == 0, f"Clean R2 clone must pass verifier, got rc={rc}:\n{out}"


@pytest.mark.parametrize("mid,mut_fn,expected_tok", KH03_R2_VERIFIER_MUTATIONS_SPECS, ids=[m[0] for m in KH03_R2_VERIFIER_MUTATIONS_SPECS])
def test_kh03_r2_verifier_mutation_detected_fail_closed(tmp_path, mid, mut_fn, expected_tok):
    cdir = tmp_path / f"mut_{mid}"
    rid, tsha = build_synthetic_valid_kh03_r2_run_dir(cdir)
    mut_fn(cdir)
    rc, out = run_kh03_r2_verifier_subprocess(cdir, rid, tsha)
    assert rc != 0, f"Mutation {mid} MUST cause verifier non-zero exit, got rc=0"
    assert expected_tok in out, f"Mutation {mid} expected token {expected_tok!r}, got:\n{out}"
