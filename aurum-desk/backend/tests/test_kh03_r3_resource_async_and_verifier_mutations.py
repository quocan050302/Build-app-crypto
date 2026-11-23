"""
aurum-desk/backend/tests/test_kh03_r3_resource_async_and_verifier_mutations.py

KH-03.R3 Resource Guard + Separate-OS Independent Verifier Mutation Suite (16 NEW Attacks):
Constructs a clean synthetic valid KH-03.R3 run folder in `tmp_path` (NEVER touches any
sealed repo run), verifies exit code 0 on the clean clone, verifies CLI disk waiver refusal
(exit 2), and executes 16 distinct NEW §10 mutation attacks proving `kh_03_r3_independent_verifier.py`
fails closed with exact violation tokens!
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
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
    evaluate_kh03_dev_ci_and_advancement,
)
from services.kh_03_r3_diagnostic_service import validate_r3_forensic_ledger_independently
from services.kh_03_r3_experiment_service import KH03_R3_PRIMARY_ARMS
from services.kh_03_r3_legacy_research_guard import SCHEMA_VERSION_KH03_R3, SPEC_VERSION_KH03_R3
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tools.kh_02_r2_c3_independent_verifier import _audit_memory_samples_c3
from tools.kh_03_r3_independent_verifier import recompute_kh03_r3_30_gates_and_verdicts


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build_synthetic_valid_kh03_r3_run_dir(run_dir: Path) -> Tuple[str, str]:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "junit").mkdir(exist_ok=True)

    tsha = "d" * 40
    rid = f"20261123T180000Z_{tsha[:12]}"

    # 06: Forensic ledger (1 closed LOSS row)
    gz06 = run_dir / "06_per_trade_entry_exit_fee_forensic_ledger.jsonl.gz"
    r06 = {
        "episode_uid": "KH03_R3_FORENSIC|1791500000000|opp_1|0",
        "opportunity_id": "opp_1", "direction": "LONG", "filled": True, "outcome_label": "LOSS",
        "realized_gross_pnl_usd": -1.443, "entry_fee_usd": 0.5058, "exit_fee_usd": 0.5052,
        "total_fees_usd": 1.011, "accrued_open_entry_fee_usd": 0.0, "realized_net_pnl_usd": -2.454,
        "gross_fee_net_residual_usd": 0.0,
    }
    ln06 = json.dumps(r06, sort_keys=True, separators=(",", ":"))
    with gzip.open(gz06, "wt", encoding="utf-8") as gz:
        gz.write(ln06 + "\n")

    f07 = {
        "spec_version": SPEC_VERSION_KH03_R3, "status": "PASS",
        "linked_forensic_ledger_06": {
            "ledger_filename": gz06.name, "ledger_file_sha256": _sha256_file(gz06),
            "total_episode_rows": 1, "episode_uids_sha256": canonical_hash([r06["episode_uid"]]),
            "canonical_rows_sha256": canonical_hash([hashlib.sha256(ln06.encode("utf-8")).hexdigest()]),
        },
        "overall_pooled_null_1x": {
            "scheduled_count": 1, "filled_count": 1, "closed_count": 1, "wins": 0, "losses": 1,
            "gross_pnl_usd": -1.443, "total_fees_usd": 1.011, "net_pnl_usd": -2.454,
        },
    }

    f11: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R3, "written_and_hashed_before_validation_replay": True,
        "h3_r2_excluded_from_executable_arms": True,
        "finite_search_budget": {"max_primary_arms": 3, "declared_primary_arms_count": 3},
        "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
        "OOS_EVALUATIONS": 0, "ACTIVATION_READY": "NO", "status": "PASS",
    }
    f11["plan_frozen_sha256"] = canonical_hash(f11)

    t0, emb = 1791400000000, 240 * 60_000
    folds_13 = [
        {
            "fold_id": "R2_FWD_FOLD_1", "role": "FORWARD_ONLY_DEV_VALIDATION", "is_fresh_oos": False,
            "train_min_ts_ms": t0, "train_max_known_at_ts_ms": t0 + 100 * 60_000,
            "train_last_label_exit_ts_ms": t0 + 90 * 60_000, "future_train_bars_count": 0,
            "embargo_start_ms": t0 + 100 * 60_000, "embargo_end_ms": t0 + 100 * 60_000 + emb,
            "embargo_minutes": 240, "val_min_ts_ms": t0 + 100 * 60_000 + emb, "val_max_ts_ms": t0 + 200 * 60_000 + emb,
        },
        {
            "fold_id": "R2_FWD_FOLD_2", "role": "FORWARD_ONLY_DEV_VALIDATION", "is_fresh_oos": False,
            "train_min_ts_ms": t0, "train_max_known_at_ts_ms": t0 + 250 * 60_000 + emb,
            "train_last_label_exit_ts_ms": t0 + 240 * 60_000 + emb, "future_train_bars_count": 0,
            "embargo_start_ms": t0 + 250 * 60_000 + emb, "embargo_end_ms": t0 + 250 * 60_000 + 2 * emb,
            "embargo_minutes": 240, "val_min_ts_ms": t0 + 250 * 60_000 + 2 * emb, "val_max_ts_ms": t0 + 350 * 60_000 + 2 * emb,
        },
    ]
    replays_14 = []
    for f in folds_13:
        sf = {"fold_id": f["fold_id"], "tested_code_sha": tsha, "selected_arm_on_past_train_only": KH03_R3_PRIMARY_ARMS[0], "selection_frozen_before_validation_replay": True}
        sf["selection_freeze_sha256"] = canonical_hash(sf)
        replays_14.append({"fold_id": f["fold_id"], "fold_local_selection_freeze": sf})

    prim_16, match_16 = {}, {}
    for ac in KH03_R3_PRIMARY_ARMS:
        mr = {"matching_quality_status": "BALANCED_COMMON_SUPPORT", "matched_mean_delta_r_vs_null": -0.1, "expectancy_r_vs_placebo_shuffled": -0.05, "expectancy_r_vs_time_offset": -0.04}
        match_16[ac] = mr
        raw_ci = [-1.01, -0.85]
        ev = evaluate_kh03_dev_ci_and_advancement(
            arm_code=ac, closed_trades_1x=75, effective_ny_days_1x=14, expectancy_r_1x=-0.95, net_pnl_usd_1x=-180.0,
            expectancy_r_fee_only_0p0006=-0.85, expectancy_r_2x=-1.01, raw_ny_day_95ci_r=raw_ci,
            holm_bonferroni_adjusted_pvalue=1.0, matching_quality_status=mr["matching_quality_status"],
            matched_mean_delta_r_vs_null=mr["matched_mean_delta_r_vs_null"],
            expectancy_r_vs_placebo_shuffled=mr["expectancy_r_vs_placebo_shuffled"],
            expectancy_r_vs_time_offset=mr["expectancy_r_vs_time_offset"],
            wf_median_fold_expectancy_r=-0.95, wf_positive_fold_ratio=0.0, wf_valid_forward_only_folds_count=2,
            executable_edge_eligible=True,
        )
        prim_16[ac] = {
            "closed_trades_1x": 75, "effective_ny_days_1x": 14, "expectancy_r_1x": -0.95, "net_pnl_usd_1x": -180.0,
            "expectancy_r_fee_only_0p0006": -0.85, "expectancy_r_2x": -1.01, "ny_day_cluster_95ci_r": raw_ci,
            "holm_bonferroni_adjusted_pvalue": 1.0, "forward_only_wf_median_fold_expectancy_r": -0.95,
            "forward_only_wf_positive_fold_ratio": 0.0, "ci_parsed": ev["ci_parsed"], "ci_schema_valid": True,
            "failed_advancement_criteria": ev["failed_advancement_criteria"],
            "passes_preregistered_dev_advancement": ev["passes_preregistered_dev_advancement"],
        }

    scen_15 = {sc: {"arms": {KH03_R3_PRIMARY_ARMS[0]: {"closed": 10, "gross_pnl_usd": -10.0, "total_fees_usd": 12.0, "net_pnl_usd": -22.0}}} for sc in EXPECTED_SIX_SCENARIOS_KH03}

    mem_gz = run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    with gzip.open(mem_gz, "wt", encoding="utf-8") as mgz:
        for i in range(4):
            mgz.write(json.dumps({"ts_ms": 1791500000000 + i * 500, "available_ram_bytes": 4_000_000_000, "available_mib": 3814.7, "child_count": 2, "worker_pids": [101, 102], "disk_free_gib": 35.0}) + "\n")

    n_tc = 5660
    c_ids = [f"backend/tests/test_kh03_r3_synth.py::test_case_{i:04d}" for i in range(n_tc)]
    xml_path = run_dir / "junit" / "SHARD_01.xml"
    tcs = "".join(f'<testcase classname="backend.tests.test_kh03_r3_synth" name="test_case_{i:04d}" time="0.001"/>' for i in range(n_tc))
    xml_path.write_text(f'<?xml version="1.0" encoding="utf-8"?><testsuite tests="{n_tc}" failures="0" errors="0" skipped="0">{tcs}</testsuite>', encoding="utf-8")

    docs: Dict[str, Dict[str, Any]] = {
        "00": {
            "spec_version": SPEC_VERSION_KH03_R3, "run_id": rid, "branch": "feature/v17-9-2-structural-sl-tp-from-qa",
            "start_head_sha": tsha, "end_head_sha": tsha, "tested_executable_sha": tsha,
            "git_clean_at_start": True, "local_uncommitted_changes": False, "orchestrator_pid": -9999,
            "owner_disk_preflight_waiver": False,
            "strict_multi_mount_probe": {"can_run_heavy_stages": True, "owner_waiver_permitted": False, "probe_errors": [], "distinct_mounts": [{"free_gib": 35.0, "projected_post_reserve_gib": 34.0}]},
            "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
            "ACTIVATION_READY": "NO", "status": "PASS",
        },
        "01": {"status": "PASS", "locked_files_modified_count": 0, "forbidden_order_import_hits": [], "cost_assumptions_defaults_intact": True, "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"}, "ACTIVATION_READY": "NO"},
        "02": {"status": "PASS", "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02, "canonical_dataset_sha256_match": True, "all_prior_sealed_artifacts_intact": True, "prior_sealed_artifacts_immutability": {"R2": {"match": True}}, "c4_provenance_and_oos_boundary": {"network_capture_enabled": False, "live_network_capture_executed": False, "LIVE_NETWORK_VALIDATION": "NOT_RUN_BY_DESIGN", "real_oos_escrow_final_record_count": 0}},
        "03": {"classification": "REAL_RED", "status": "PASS"},
        "04": {"status": "PASS", "all_synthetic_bypass_vectors_detected": True, "callgraph_audit": {"LEGACY_METHOD_GUARD": "PASS", "unauthorized_r1_callers_count": 0}},
        "05": {"status": "PASS", "conservation_checks": {"eligible_eq_scheduled_plus_busy_skipped": True, "scheduled_eq_terminal_outcome_partition": True, "zero_duplicate_opportunity_ids": True, "zero_1pos_overlap_violations": True}},
        "07": f07,
        "08": {"status": "PASS", "planned_cap_is_physical_guarantee": False, "attribution_epistemic_label": "ASSOCIATION_ONLY_NO_CAUSAL_ATTRIBUTION", "three_axis_diagnosis": {"ENGINE_AXIS": {"accounting_or_simulator_bug_detected": False}}},
        "09": {"status": "PASS", "zero_fee_display_bugs_count": 0, "observed_l1_bbo_available": False},
        "10": {"status": "PASS", "direction_inverted_symmetric_sl_tp_control": {"inverted_closed": 162}, "future_bar_mutation_invariance": {"passed": True, "mismatches_count": 0}, "synthetic_label_and_conservative_tie_controls": {"passed": True}},
        "11": f11,
        "12": {"status": "PASS", "all_arms_conserved_exact": True},
        "13": {"status": "PASS", "preregistered_plan_sha256": f11["plan_frozen_sha256"], "is_fresh_oos": False, "OOS_EVALUATIONS": 0, "valid_folds_count": 2, "future_win_injection_invariance_passed": True, "folds": folds_13},
        "14": {"status": "PASS", "total_overlap_violations": 0, "per_fold_selection_and_validation_replays": replays_14},
        "15": {"status": "PASS", "zero_fee_display_bugs_count": 0, "total_overlap_violations": 0, "observed_l1_bbo_available": False, "ACCOUNT_EFFECTIVE_FEE_VERDICT": "UNKNOWN_NOT_LOCALLY_VERIFIED", "dynamic_full_rerisk_six_scenarios": scen_15, "fixed_fills_descriptive_repricing": {KH03_R3_PRIMARY_ARMS[0]: {"fixed_1x_closed_count": 10}}},
        "16": {"spec_version": SPEC_VERSION_KH03_R3, "status": "PASS", "preregistered_plan_sha256": f11["plan_frozen_sha256"], "preregistered_hypothesis_budget_m": 3, "primary_arm_evaluations": prim_16, "matching_reports": match_16, "negative_control_summaries_1x": {"NC_NULL_TRIGGER": {}, "NC_PLACEBO_SHUFFLED_TRIGGER": {}, "NC_TIME_OFFSET_SHIFT": {}}, "synthetic_positive_gate_capability_proof": {"passes_preregistered_dev_advancement": True}, "passing_dev_candidate_arms": [], "DEV_RESEARCH_CANDIDATE": "NO_ECONOMIC_ADVANCEMENT_CANDIDATE", "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE", "FRESH_OOS": "BLOCKED_NO_UNTOUCHED_DATA", "KH04_ENTRY": "BLOCKED_NO_DEV_CANDIDATE", "OOS_EVALUATIONS": 0, "ACTIVATION_READY": "NO", "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"}},
        "17": {"BASELINE_PARITY_VERDICT": "PASS", "per_decision_and_episode_fingerprints": {"all_sealed_fingerprints_exact_match": True}, "a1_population_count": 56710, "a1_mismatches_count": 0, "si8_population_count": 6375, "si8_mismatches_count": 0, "three_adapter_population_count": 246, "three_adapter_mismatches_count": 0, "d4_historical_closed_trades": 0},
        "18": {"status": "PASS", "collected_count": n_tc, "executed_count": n_tc, "passed_count": n_tc, "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0, "junit_bijection_exact": True, "collection_manifest": {"run_id": rid, "tested_executable_sha": tsha, "collected_node_ids_count": n_tc, "collected_node_ids": c_ids}, "shard_manifest": {"run_id": rid, "tested_executable_sha": tsha, "nonzero_exit_shards_count": 0, "shards": [{"shard_id": "SHARD_01", "exit_code": 0, "timed_out": False, "reused": False, "junit_xml_path": "junit/SHARD_01.xml", "xml_sha256": _sha256_file(xml_path), "scheduled_node_ids": c_ids}]}},
        "19": {"status": "PASS", "pytest_controlled_subset_w1_w2_w4_repeat": {"all_4_runs_identical": True, "runs": [{"mode_label": m, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64, "worker_pids": [1, 2]} for m in ("w=1", "w=2", "w=4", "w=1_repeat")]}, "r2_science_slice_w1_w2_w4_repeat": {"all_4_runs_identical": True, "runs": [{"mode_label": m, "rows_count": 12062, "canonical_sorted_sha256": "2" * 64} for m in ("w=1", "w=2", "w=4", "w=1_repeat")]}},
        "20": {"min_disk_free_gib": 35.0, "min_available_ram_mib": 3800.0, "max_descendant_children": 4, "orphan_children_after_cleanup": 0, "sentinel_temp_cleaned_up": True, "owner_disk_preflight_waiver": False},
        "21": {"status": "PASS", "inherited_r2_controls_count": 174, "new_kh03_r3_controls_count": 20, "total_controls": 194, "unique_fault_ids": 194, "failed_controls": 0},
        "22": {"status": "PASS", "clean_clone_verifier_green": True, "mutations_tested_count": 16, "mutations_failed_detection_count": 0},
        "25": {"run_id": rid, "tested_executable_sha": tsha, "publication_status": "PRE_PUSH_PENDING", "remote_readback_sha256_verified": False, "owner_disk_preflight_waiver": False},
    }

    mem_aud = _audit_memory_samples_c3(mem_gz)
    for_aud = validate_r3_forensic_ledger_independently(gz06, f07)
    rec23 = recompute_kh03_r3_30_gates_and_verdicts(docs=docs, mem_audit=mem_aud, forensic_ledger_audit=for_aud, junit_bijection_ok=True, for_final_verifier_manifest=False)
    docs["23"] = {"spec_version": SPEC_VERSION_KH03_R3, "run_id": rid, "tested_executable_sha": tsha, "overall_classification": rec23["overall_classification"], "gates_30": rec23["gates_30"], "verdicts": rec23["verdicts"]}

    fn_map = {
        "00": "00_source_branch_clean_sha_disk_ram_preflight.json",
        "01": "01_ast_ownership_legacy_guard_and_keep_as_is_delta.json",
        "02": "02_prior_sealed_sha_and_canonical_dataset_integrity.json",
        "03": "03_r1_legacy_false_pass_red_reproductions.json",
        "04": "04_r2_method_guard_green_and_entrypoint_scans.json",
        "05": "05_full_opportunity_lineage_and_hash_manifest.json",
        "07": "07_win_loss_breakdown_denominator_ci_coverage.json",
        "08": "08_stop_target_gap_tie_holding_risk_tail_attribution.json",
        "09": "09_six_cost_scenarios_and_breakeven_accounting.json",
        "10": "10_negative_controls_matching_and_causal_disclaimers.json",
        "11": "11_training_only_bounded_prereg_plan.json",
        "12": "12_candidate_asof_features_and_exact_conservation.json",
        "13": "13_r2_forward_only_fold_boundaries_freeze_no_peek.json",
        "14": "14_validation_fold_independent_results_by_arm.json",
        "15": "15_fixed_fill_vs_dynamic_rerisk_and_expense_truth.json",
        "16": "16_strict_dev_gate_power_holm_cluster_bootstrap.json",
        "17": "17_baseline_parity_A1_SI8_adapter_D4.json",
        "18": "18_full_pytest_junit_collected_executed_bijection.json",
        "19": "19_w1_w2_w4_w1repeat_hashes_timings_speedups.json",
        "20": "20_disk_ram_pid_samples_and_cleanup.json",
        "21": "21_inherited_plus_new_unique_red_green_fault_receipts.json",
        "22": "22_independent_verifier_new_mutation_resistance.json",
        "23": "23_orthogonal_gate_matrix_economic_kh04_oos_activation.json",
        "25": "25_phase1_code_commit_push_readback.json",
    }
    for k, fn in fn_map.items():
        (run_dir / fn).write_text(json.dumps(docs[k], indent=2, sort_keys=True), encoding="utf-8")
    (run_dir / "README_KH03_R3.md").write_text(f"# KH03 R3 {rid}\nACTIVATION_READY=NO\nNO_PROVEN_EDGE\nOOS_EVALUATIONS=0\n", encoding="utf-8")
    (run_dir / "HANDOVER_KH03_R3_VI.txt").write_text(f"KH03 R3 {rid}\nACTIVATION_READY=NO\nNO_PROVEN_EDGE\nOOS_EVALUATIONS=0\n", encoding="utf-8")
    return rid, tsha


def run_kh03_r3_verifier_subprocess(run_dir: Path, run_id: str, tested_sha: str) -> Tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)])
    p = subprocess.run(
        [
            sys.executable, str(APP_ROOT / "tools" / "kh_03_r3_independent_verifier.py"),
            "--run-dir", str(run_dir), "--expected-run-id", run_id, "--expected-tested-sha", tested_sha,
            "--skip-live-repo-pointers", "--no-write-manifest",
        ],
        cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=60,
    )
    return int(p.returncode), (p.stdout or "") + "\n" + (p.stderr or "")


def _mut_json(p: Path, fn: Callable[[Dict[str, Any]], None]) -> None:
    d = json.loads(p.read_text("utf-8"))
    fn(d)
    p.write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")


# 16 NEW KH-03.R3 Verifier Mutation Attacks (§10)
KH03_R3_VERIFIER_MUTATIONS_SPECS: List[Tuple[str, Callable[[Path], None], str]] = [
    ("R3M01_FORGE_R1_PROTOCOL_IN_16", lambda d: _mut_json(d / "16_strict_dev_gate_power_holm_cluster_bootstrap.json", lambda o: o.__setitem__("spec_version", "KH-03.R1-v1.0")), "PROVENANCE_16:INVALID_SPEC_VERSION"),
    ("R3M02_UNAUTHORIZED_R1_CALLER_IN_04", lambda d: _mut_json(d / "04_r2_method_guard_green_and_entrypoint_scans.json", lambda o: o["callgraph_audit"].__setitem__("unauthorized_r1_callers_count", 1)), "UNAUTHORIZED_R1_CALLER_DETECTED"),
    ("R3M03_FORGE_H3_INTO_R3_PRIMARY_ARMS", lambda d: _mut_json(d / "16_strict_dev_gate_power_holm_cluster_bootstrap.json", lambda o: o["primary_arm_evaluations"].__setitem__(KH03_R2_H3_ADVISORY_ARM, {"executable_edge_eligible": True, "passes_preregistered_dev_advancement": True})), "PROVENANCE_16:H3_PROMOTED_AS_EXECUTABLE_CANDIDATE"),
    ("R3M04_FORGE_FALSE_CI_PASS_BOOLEAN", lambda d: _mut_json(d / "16_strict_dev_gate_power_holm_cluster_bootstrap.json", lambda o: o["primary_arm_evaluations"][KH03_R3_PRIMARY_ARMS[0]].__setitem__("passes_preregistered_dev_advancement", True)), "FORGED_DEV_ADVANCEMENT_BOOLEAN"),
    ("R3M05_FORGE_WIN_RATE_EXCLUDES_LOSSES_07", lambda d: _mut_json(d / "07_win_loss_breakdown_denominator_ci_coverage.json", lambda o: o["overall_pooled_null_1x"].__setitem__("losses", 0)), "FORENSIC_06:FORENSIC_07_WIN_LOSS_DENOMINATOR_MISMATCH"),
    ("R3M06_ZERO_FEE_IN_07", lambda d: _mut_json(d / "07_win_loss_breakdown_denominator_ci_coverage.json", lambda o: o["overall_pooled_null_1x"].__setitem__("total_fees_usd", 0.0)), "FORENSIC_06:FORENSIC_07_FEES_MISMATCH"),
    ("R3M07_CONCEAL_RISK_CAP_AS_GUARANTEE", lambda d: _mut_json(d / "08_stop_target_gap_tie_holding_risk_tail_attribution.json", lambda o: o.__setitem__("planned_cap_is_physical_guarantee", True)), "FORGED_RISK_CAP_PHYSICAL_GUARANTEE"),
    ("R3M08_TAMPER_PREREG_PLAN_SHA_11", lambda d: _mut_json(d / "11_training_only_bounded_prereg_plan.json", lambda o: o.__setitem__("plan_frozen_sha256", "0" * 64)), "PREREG_PLAN_SHA256_TAMPERED_OR_UNLINKED"),
    ("R3M09_INJECT_FUTURE_TRAIN_BAR_IN_13", lambda d: _mut_json(d / "13_r2_forward_only_fold_boundaries_freeze_no_peek.json", lambda o: o["folds"][0].__setitem__("future_train_bars_count", 5)), "WF_CAUSALITY:FUTURE_TRAIN_BARS_LEAKED"),
    ("R3M10_TAMPER_FOLD_SELECTION_FREEZE_SHA", lambda d: _mut_json(d / "14_validation_fold_independent_results_by_arm.json", lambda o: o["per_fold_selection_and_validation_replays"][0]["fold_local_selection_freeze"].__setitem__("selection_freeze_sha256", "9" * 64)), "WF_SELECTION_FREEZE_SHA_TAMPERED"),
    ("R3M11_ZERO_FEE_BUG_IN_15", lambda d: _mut_json(d / "15_fixed_fill_vs_dynamic_rerisk_and_expense_truth.json", lambda o: o["dynamic_full_rerisk_six_scenarios"]["1X_BASE_0p0004"]["arms"][KH03_R3_PRIMARY_ARMS[0]].__setitem__("total_fees_usd", 0.0)), "ZERO_FEE_BUG_IN_15"),
    ("R3M12_FABRICATE_OBSERVED_L1_BBO_TRUE", lambda d: _mut_json(d / "15_fixed_fill_vs_dynamic_rerisk_and_expense_truth.json", lambda o: o.__setitem__("observed_l1_bbo_available", True)), "FORGED_OBSERVED_L1_BBO_AVAILABLE_TRUE"),
    ("R3M13_FABRICATE_FRESH_OOS_EVAL_ONE", lambda d: _mut_json(d / "16_strict_dev_gate_power_holm_cluster_bootstrap.json", lambda o: o.__setitem__("OOS_EVALUATIONS", 1)), "NONZERO_OOS_EVALUATIONS_PEEK"),
    ("R3M14_TAMPER_DATASET_SHA_02", lambda d: _mut_json(d / "02_prior_sealed_sha_and_canonical_dataset_integrity.json", lambda o: o.__setitem__("canonical_dataset_sha256", "0" * 64)), "CANONICAL_DATASET_SHA256_TAMPERED"),
    ("R3M15_FAKE_CHILD_PROCESS_COUNT_20", lambda d: _mut_json(d / "20_disk_ram_pid_samples_and_cleanup.json", lambda o: o.__setitem__("max_descendant_children", 9)), "GATE_TAMPER_OR_DRIFT:G23_DISK_RAM_CHILD_PROCESS_SAFETY"),
    ("R3M16_FAKE_REMOTE_PUBLICATION_NO_READBACK", lambda d: _mut_json(d / "25_phase1_code_commit_push_readback.json", lambda o: o.__setitem__("publication_status", "PASS_REMOTE_VERIFIED")), "FALSE_REMOTE_READBACK_CLAIM"),
]


def test_kh03_r3_cli_refuses_disk_waiver():
    env = os.environ.copy()
    env["KH02_OWNER_WAIVE_DISK_PREFLIGHT"] = "1"
    p = subprocess.run([sys.executable, str(APP_ROOT / "tools" / "run_kh_03_r3_acceptance.py"), "--help"], cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=20)
    assert p.returncode == 2


def test_kh03_r3_clean_clone_passes_separate_os_verifier(tmp_path):
    cdir = tmp_path / "clean_r3"
    rid, tsha = build_synthetic_valid_kh03_r3_run_dir(cdir)
    rc, out = run_kh03_r3_verifier_subprocess(cdir, rid, tsha)
    assert rc == 0, f"Clean R3 clone must pass verifier, got rc={rc}:\n{out}"


@pytest.mark.parametrize("mid,mut_fn,expected_tok", KH03_R3_VERIFIER_MUTATIONS_SPECS, ids=[m[0] for m in KH03_R3_VERIFIER_MUTATIONS_SPECS])
def test_kh03_r3_verifier_mutation_detected_fail_closed(tmp_path, mid, mut_fn, expected_tok):
    cdir = tmp_path / f"mut_{mid}"
    rid, tsha = build_synthetic_valid_kh03_r3_run_dir(cdir)
    mut_fn(cdir)
    rc, out = run_kh03_r3_verifier_subprocess(cdir, rid, tsha)
    assert rc != 0, f"Mutation {mid} MUST cause verifier non-zero exit"
    assert expected_tok in out, f"Mutation {mid} expected {expected_tok!r}, got:\n{out}"
