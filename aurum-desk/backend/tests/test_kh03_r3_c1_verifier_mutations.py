"""
aurum-desk/backend/tests/test_kh03_r3_c1_verifier_mutations.py

KH-03.R3.C1 Separate-OS Independent Verifier Mutation Suite (17 NEW Non-Redundant Attacks):
Constructs a clean synthetic valid KH-03.R3.C1 run folder in `tmp_path` (NEVER touches any
sealed repo run), proves exit 0 on the clean clone, and executes 17 distinct NEW mutations
(C1M01..C1M17 covering P19..P26, P28..P29 & §08) proving `kh_03_r3_c1_independent_verifier.py`
fails closed with non-zero exit and exact violation tokens in a separate OS process!
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
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_prospective_data_service import CANONICAL_DATASET_SHA256_KH02
from services.kh_03_fee_integrity_service import EXPECTED_SIX_SCENARIOS_KH03
from services.kh_03_r3_c1_worker_policy_service import (
    BENCHMARK_KIND_PYTEST,
    BENCHMARK_KIND_SCIENCE,
    SCHEMA_VERSION_KH03_R3_C1,
    SPEC_VERSION_KH03_R3_C1,
    select_measured_worker_policy_strict,
)
from tools.kh_03_r3_c1_independent_verifier import (
    PRIOR_KH03_R3_ARTIFACT_19_SHA256,
    PRIOR_KH03_R3_RUN_ID,
    PRIOR_KH03_R3_TESTED_SHA,
    SEALED_BASELINE_PARITY_FINGERPRINTS,
    SEALED_R3_1X_ECONOMIC_ANCHORS,
    _audit_memory_and_disk_samples_c1,
    _independent_recompute_raw_benchmark_policy_c1,
    recompute_kh03_r3_c1_28_gates_and_verdicts,
)


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build_synthetic_valid_kh03_r3_c1_run_dir(run_dir: Path) -> Tuple[str, str]:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "junit").mkdir(exist_ok=True)
    (run_dir / "checksums").mkdir(exist_ok=True)

    tsha = "e" * 40
    rid = f"20261123T210000Z_{tsha[:12]}"

    # 06 & 07: Raw fresh benchmark receipts
    f06 = {
        "spec_version": SPEC_VERSION_KH03_R3_C1, "schema_version": SCHEMA_VERSION_KH03_R3_C1,
        "run_id": rid, "tested_executable_sha": tsha, "benchmark_kind": BENCHMARK_KIND_PYTEST,
        "status": "PASS", "all_4_runs_identical": True,
        "measured_speedup_w2_vs_w1": 1.25, "measured_speedup_w4_vs_w1": 1.667,
        "runs": [
            {"mode_label": "w=1", "workers": 1, "worker_pids": [101], "wall_clock_seconds": 50.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
            {"mode_label": "w=2", "workers": 2, "worker_pids": [201, 202], "wall_clock_seconds": 40.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
            {"mode_label": "w=4", "workers": 4, "worker_pids": [401, 402, 403, 404], "wall_clock_seconds": 30.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
            {"mode_label": "w=1_repeat", "workers": 1, "worker_pids": [501], "wall_clock_seconds": 52.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
        ],
    }
    f07 = {
        "spec_version": SPEC_VERSION_KH03_R3_C1, "schema_version": SCHEMA_VERSION_KH03_R3_C1,
        "run_id": rid, "tested_executable_sha": tsha, "benchmark_kind": BENCHMARK_KIND_SCIENCE,
        "status": "PASS", "all_4_runs_identical": True,
        "measured_speedup_w2_vs_w1": 0.909, "measured_speedup_w4_vs_w1": 0.833,
        "runs": [
            {"mode_label": "w=1", "workers": 1, "chunks_count": 1, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": 2.0, "canonical_sorted_sha256": "2" * 64},
            {"mode_label": "w=2", "workers": 2, "chunks_count": 2, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": 2.2, "canonical_sorted_sha256": "2" * 64},
            {"mode_label": "w=4", "workers": 4, "chunks_count": 4, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": 2.4, "canonical_sorted_sha256": "2" * 64},
            {"mode_label": "w=1_repeat", "workers": 1, "chunks_count": 1, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": 2.1, "canonical_sorted_sha256": "2" * 64},
        ],
    }

    p_pol = select_measured_worker_policy_strict(
        f06, benchmark_kind=BENCHMARK_KIND_PYTEST,
        system_limits={"configured_child_process_cap": 4, "full_suite_safe_worker_cap": 3},
        expected_run_id=rid, expected_tested_sha=tsha,
    )
    s_pol = select_measured_worker_policy_strict(
        f07, benchmark_kind=BENCHMARK_KIND_SCIENCE,
        system_limits={"configured_child_process_cap": 4, "full_suite_safe_worker_cap": 4},
        expected_run_id=rid, expected_tested_sha=tsha,
    )

    # 11: Fresh JUnit XML (>= 5683 tests)
    n_tc = 5710
    c_ids = [f"backend/tests/test_kh03_r3_c1_synth.py::test_case_{i:04d}" for i in range(n_tc)]
    xml_path = run_dir / "junit" / "SHARD_01.xml"
    tcs = "".join(f'<testcase classname="backend.tests.test_kh03_r3_c1_synth" name="test_case_{i:04d}" time="0.001"/>' for i in range(n_tc))
    xml_path.write_text(f'<?xml version="1.0" encoding="utf-8"?><testsuite tests="{n_tc}" failures="0" errors="0" skipped="0">{tcs}</testsuite>', encoding="utf-8")

    # 13: Six-scenario economic regression anchored exactly to sealed R3 1x
    base_arms = {}
    for ac, anc in SEALED_R3_1X_ECONOMIC_ANCHORS.items():
        base_arms[ac] = {
            "closed": anc["closed"], "wins": anc["wins"], "losses": anc["losses"],
            "gross_pnl_usd": round(anc["net_pnl_usd"] + 100.0, 4),
            "total_fees_usd": 100.0,
            "net_pnl_usd": anc["net_pnl_usd"],
            "expectancy_r": anc["expectancy_r"],
        }
    scen_13 = {sc: {"arms": base_arms} for sc in EXPECTED_SIX_SCENARIOS_KH03}

    # 15: 0.5s Telemetry log
    mem_gz = run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    with gzip.open(mem_gz, "wt", encoding="utf-8") as mgz:
        for i in range(4):
            mgz.write(json.dumps({"ts_ms": 1795430000000 + i * 500, "available_ram_bytes": 4_000_000_000, "available_mib": 3814.7, "child_count": 3, "worker_pids": [11, 12, 13], "disk_free_gib": 33.5}) + "\n")

    docs: Dict[str, Dict[str, Any]] = {
        "00": {
            "spec_version": SPEC_VERSION_KH03_R3_C1, "schema_version": SCHEMA_VERSION_KH03_R3_C1,
            "run_id": rid, "branch": "feature/v17-9-2-structural-sl-tp-from-qa",
            "start_head_sha": tsha, "end_head_sha": tsha, "tested_executable_sha": tsha,
            "git_clean_at_start": True, "local_uncommitted_changes": False, "orchestrator_pid": -9999,
            "owner_disk_preflight_waiver": False,
            "strict_multi_mount_probe": {"can_run_heavy_stages": True, "owner_waiver_permitted": False, "probe_errors": [], "distinct_mounts": [{"free_gib": 33.5, "projected_post_reserve_gib": 32.5}]},
            "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
            "ACTIVATION_READY": "NO", "status": "PASS",
        },
        "01": {
            "spec_version": SPEC_VERSION_KH03_R3_C1, "status": "PASS",
            "locked_files_modified_count": 0, "unauthorized_changed_files_count": 0,
            "forbidden_order_import_hits": [], "cost_assumptions_defaults_intact": True,
            "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
            "ACTIVATION_READY": "NO",
        },
        "02": {
            "spec_version": SPEC_VERSION_KH03_R3_C1, "status": "PASS",
            "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02, "canonical_dataset_sha256_match": True,
            "all_prior_sealed_artifacts_intact": True, "prior_sealed_artifacts_immutability": {"R3": {"match": True}},
            "c4_provenance_and_oos_boundary": {"network_capture_enabled": False, "live_network_capture_executed": False, "LIVE_NETWORK_VALIDATION": "NOT_RUN_BY_DESIGN", "real_oos_escrow_final_record_count": 0},
        },
        "03": {
            "spec_version": SPEC_VERSION_KH03_R3_C1, "status": "PASS", "classification": "REAL_RED_REPRODUCED",
            "source_fixture_sha256": PRIOR_KH03_R3_ARTIFACT_19_SHA256,
            "old_buggy_pytest_policy": {"mean_wall_sec_by_mode": {}, "optimum_worker_policy": "w=1"},
            "old_buggy_science_policy": {"mean_wall_sec_by_mode": {}, "optimum_worker_policy": "w=1"},
        },
        "04": {"spec_version": SPEC_VERSION_KH03_R3_C1, "status": "PASS", "cases_passed": 16, "cases_failed": 0},
        "05": {
            "spec_version": SPEC_VERSION_KH03_R3_C1, "status": "PASS",
            "source_fixture_sha256": PRIOR_KH03_R3_ARTIFACT_19_SHA256,
            "pytest_fixture_green_policy": {"provenance_role": "HISTORICAL_REFERENCE_ONLY", "measured_fastest_mode": "w=4", "mean_wall_sec_by_mode": {"w=1": 51.698, "w=2": 41.123, "w=4": 32.947}},
            "science_fixture_green_policy": {"provenance_role": "HISTORICAL_REFERENCE_ONLY", "measured_fastest_mode": "w=1", "mean_wall_sec_by_mode": {"w=1": 2.069, "w=2": 2.181, "w=4": 2.277}},
        },
        "06": f06,
        "07": f07,
        "08": {"spec_version": SPEC_VERSION_KH03_R3_C1, "status": "PASS", "run_id": rid, "tested_executable_sha": tsha, "pytest_worker_policy": p_pol, "science_worker_policy": s_pol},
        "09": {
            "spec_version": SPEC_VERSION_KH03_R3_C1, "status": "PASS", "timing_determinism_claimed": False,
            "full_suite_speedup_extrapolated": False,
            "pytest_stability": {"w1_repeat_variance_pct": p_pol["w1_repeat_variance_pct"], "timing_stability_status": p_pol["timing_stability_status"]},
            "science_stability": {"w1_repeat_variance_pct": s_pol["w1_repeat_variance_pct"], "timing_stability_status": s_pol["timing_stability_status"]},
        },
        "10": {"spec_version": SPEC_VERSION_KH03_R3_C1, "status": "PASS", "unmeasured_full_suite_speedup_claimed": False},
        "11": {
            "spec_version": SPEC_VERSION_KH03_R3_C1, "status": "PASS",
            "collected_count": n_tc, "executed_count": n_tc, "passed_count": n_tc,
            "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0,
            "junit_bijection_exact": True,
            "collection_manifest": {"run_id": rid, "tested_executable_sha": tsha, "collected_node_ids_count": n_tc, "collected_node_ids": c_ids},
            "shard_manifest": {"run_id": rid, "tested_executable_sha": tsha, "nonzero_exit_shards_count": 0, "shards": [{"shard_id": "SHARD_01", "exit_code": 0, "timed_out": False, "reused": False, "junit_xml_path": "junit/SHARD_01.xml", "xml_sha256": _sha256_file(xml_path), "scheduled_node_ids": c_ids}]},
        },
        "12": {
            "spec_version": SPEC_VERSION_KH03_R3_C1, "BASELINE_PARITY_VERDICT": "PASS",
            "a1_population_count": 56710, "a1_mismatches_count": 0,
            "si8_population_count": 6375, "si8_mismatches_count": 0,
            "three_adapter_population_count": 246, "three_adapter_mismatches_count": 0,
            "d4_historical_closed_trades": 0,
            "per_decision_and_episode_fingerprints": {**SEALED_BASELINE_PARITY_FINGERPRINTS, "all_sealed_fingerprints_exact_match": True},
        },
        "13": {
            "spec_version": SPEC_VERSION_KH03_R3_C1, "status": "PASS",
            "historical_r3_1x_anchors_exact_match": True,
            "dynamic_vs_fixed_fill_populations_separated": True,
            "zero_fee_display_bugs_count": 0,
            "dynamic_full_rerisk_six_scenarios": scen_13,
            "fixed_fills_descriptive_repricing": {"NC_NULL_TRIGGER": {"fixed_1x_closed_count": 203}},
            "passing_dev_candidate_arms": [],
            "DEV_RESEARCH_CANDIDATE": "NO_ECONOMIC_ADVANCEMENT_CANDIDATE",
            "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE",
            "FRESH_OOS": "BLOCKED_NO_UNTOUCHED_DATA",
            "OOS_EVALUATIONS": 0,
            "observed_l1_bbo_available": False,
            "ACTIVATION_READY": "NO",
            "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
        },
        "14": {
            "spec_version": SPEC_VERSION_KH03_R3_C1, "status": "PASS",
            "LEGACY_METHOD_GUARD": "PASS", "unauthorized_r1_callers_count": 0,
            "r2_forward_only_embargo_240m_verified": True,
            "h3_r2_excluded_from_executable_arms": True,
            "h3_promoted_as_executable": False,
            "H3_RESIZE_EXECUTION_STATUS": "BLOCKED_ADVISORY_FILTER_ONLY",
            "ACCOUNT_EFFECTIVE_FEE_VERDICT": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            "observed_l1_bbo_available": False,
            "planned_cap_is_physical_guarantee": False,
            "OOS_EVALUATIONS": 0,
            "ACTIVATION_READY": "NO",
            "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
        },
        "15": {
            "spec_version": SPEC_VERSION_KH03_R3_C1, "min_disk_free_gib": 33.5, "min_available_ram_mib": 3814.7,
            "max_descendant_children": 3, "orphan_children_after_cleanup": 0,
            "sentinel_temp_cleaned_up": True, "atomic_checksums_verified": True, "owner_disk_preflight_waiver": False,
        },
        "16": {
            "spec_version": SPEC_VERSION_KH03_R3_C1, "status": "PASS",
            "inherited_r3_controls_count": 194, "inherited_r3_failed_controls": 0,
            "new_c1_controls_count": 14, "new_c1_failed_controls": 0,
            "total_controls": 208, "unique_fault_ids": 208, "failed_controls": 0,
        },
        "17": {
            "spec_version": SPEC_VERSION_KH03_R3_C1, "status": "PASS",
            "clean_clone_verifier_green": True,
            "inherited_r3_mutations_tested_count": 16, "inherited_r3_mutations_failed_detection_count": 0,
            "new_c1_mutations_tested_count": 17, "new_c1_mutations_failed_detection_count": 0,
            "mutations_failed_detection_count": 0,
        },
        "20": {
            "spec_version": SPEC_VERSION_KH03_R3_C1, "run_id": rid, "tested_executable_sha": tsha,
            "publication_status": "PRE_PUSH_PENDING", "remote_readback_sha256_verified": False,
            "evidence_commit_sha": None, "ci_status": "CI_UNKNOWN_NO_ACTIONS_RUN",
            "owner_disk_preflight_waiver": False,
        },
    }

    p_rec = _independent_recompute_raw_benchmark_policy_c1(f06, p_pol, kind=BENCHMARK_KIND_PYTEST, expected_run_id=rid, expected_tested_sha=tsha, max_safe_cap=3)
    s_rec = _independent_recompute_raw_benchmark_policy_c1(f07, s_pol, kind=BENCHMARK_KIND_SCIENCE, expected_run_id=rid, expected_tested_sha=tsha, max_safe_cap=4)
    m_aud = _audit_memory_and_disk_samples_c1(mem_gz, docs["15"])
    rec19 = recompute_kh03_r3_c1_28_gates_and_verdicts(
        docs=docs, pytest_recomp=p_rec, science_recomp=s_rec, mem_audit=m_aud,
        junit_bijection_ok=True, economic_ok=True, for_final_verifier_manifest=False,
    )
    docs["19"] = {
        "spec_version": SPEC_VERSION_KH03_R3_C1, "run_id": rid, "tested_executable_sha": tsha,
        "overall_classification": rec19["overall_classification"], "gates_28": rec19["gates_28"], "verdicts": rec19["verdicts"],
    }

    fn_map = {
        "00": "00_preflight_branch_sha_resource_and_lock.json",
        "01": "01_ast_function_ownership_and_locked_file_diffs.json",
        "02": "02_prior_r1_r2_r3_dataset_and_sealed_sha_integrity.json",
        "03": "03_r3_benchmark_schema_bug_real_red_reproduction.json",
        "04": "04_strict_duration_schema_and_worker_row_falsification.json",
        "05": "05_r3_historical_fixture_green_pytest_w4_science_w1.json",
        "06": "06_fresh_pytest_w1_w2_w4_w1repeat_raw_receipts.json",
        "07": "07_fresh_science_w1_w2_w4_w1repeat_raw_receipts.json",
        "08": "08_worker_policy_measured_vs_applied_caps_and_provenance.json",
        "09": "09_worker_policy_noise_stability_and_speedup_limits.json",
        "10": "10_independent_benchmark_recomputation_and_claim_checks.json",
        "11": "11_full_pytest_junit_collected_executed_bijection.json",
        "12": "12_full_A1_SI8_adapter_D4_parity_and_fingerprints.json",
        "13": "13_six_scenario_backtest_and_E1_E2_E3_null_economic_regression.json",
        "14": "14_entry_exit_ledger_fee_risk_OOS_and_legacy_guard.json",
        "15": "15_disk_ram_pid_0p5s_telemetry_and_cleanup.json",
        "16": "16_inherited_R3_faults_plus_new_C1_red_green_controls.json",
        "17": "17_new_verifier_mutation_resistance.json",
        "19": "19_orthogonal_engineering_economic_resource_release_gates.json",
        "20": "20_phase1_code_push_and_phase2_evidence_remote_readback.json",
    }
    for k, fn in fn_map.items():
        (run_dir / fn).write_text(json.dumps(docs[k], indent=2, sort_keys=True), encoding="utf-8")
    (run_dir / "README_KH03_R3_C1.md").write_text(
        f"# KH-03.R3.C1 {rid}\nACTIVATION_READY=NO\nNO_PROVEN_EDGE\nOOS_EVALUATIONS=0\nCI_UNKNOWN_NO_ACTIONS_RUN\n",
        encoding="utf-8",
    )
    (run_dir / "HANDOVER_KH03_R3_C1_VI.txt").write_text(
        f"KH-03.R3.C1 {rid}\nACTIVATION_READY=NO\nNO_PROVEN_EDGE\nOOS_EVALUATIONS=0\nCI_UNKNOWN_NO_ACTIONS_RUN\n",
        encoding="utf-8",
    )
    return rid, tsha


def run_kh03_r3_c1_verifier_subprocess(run_dir: Path, run_id: str, tested_sha: str) -> Tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)])
    p = subprocess.run(
        [
            sys.executable, str(APP_ROOT / "tools" / "kh_03_r3_c1_independent_verifier.py"),
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


# 17 NEW Unique, Non-Redundant KH-03.R3.C1 Separate-OS Independent Verifier Mutations
KH03_R3_C1_VERIFIER_MUTATIONS_SPECS: List[Tuple[str, Callable[[Path], None], str]] = [
    ("C1M01_EDIT_ONE_PYTEST_DURATION", lambda d: _mut_json(d / "06_fresh_pytest_w1_w2_w4_w1repeat_raw_receipts.json", lambda o: o["runs"][2].__setitem__("wall_clock_seconds", 45.0)), "VERIFIER_MEAN_WALL_DRIFT"),
    ("C1M02_REMOVE_W4_ROW_FROM_PYTEST", lambda d: _mut_json(d / "06_fresh_pytest_w1_w2_w4_w1repeat_raw_receipts.json", lambda o: o.__setitem__("runs", [r for r in o["runs"] if r["mode_label"] != "w=4"])), "BENCH_MISSING_MODE:pytest_controlled_subset:w=4"),
    ("C1M03_INSERT_STRING_NAN_DURATION", lambda d: _mut_json(d / "06_fresh_pytest_w1_w2_w4_w1repeat_raw_receipts.json", lambda o: o["runs"][0].__setitem__("wall_clock_seconds", "NaN")), "BENCH_NON_NUMERIC_DURATION"),
    ("C1M04_FLIP_PYTEST_SEMANTIC_SHA", lambda d: _mut_json(d / "06_fresh_pytest_w1_w2_w4_w1repeat_raw_receipts.json", lambda o: o["runs"][1].__setitem__("canonical_sorted_outcome_sha256", "9" * 64)), "BENCH_SEMANTIC_SHA_MISMATCH"),
    ("C1M05_FALSIFY_W1_REPEAT_SHA", lambda d: _mut_json(d / "07_fresh_science_w1_w2_w4_w1repeat_raw_receipts.json", lambda o: o["runs"][3].__setitem__("canonical_sorted_sha256", "8" * 64)), "BENCH_W1_REPEAT_SHA_MISMATCH"),
    ("C1M06_EMPTY_MEANS_MAP_P19", lambda d: _mut_json(d / "08_worker_policy_measured_vs_applied_caps_and_provenance.json", lambda o: o["pytest_worker_policy"].__setitem__("mean_wall_sec_by_mode", {})), "VERIFIER_EMPTY_MEAN_WALL_MAP"),
    ("C1M07_FAKE_W1_FASTEST_ON_PYTEST_P20", lambda d: _mut_json(d / "08_worker_policy_measured_vs_applied_caps_and_provenance.json", lambda o: (o["pytest_worker_policy"].__setitem__("measured_fastest_mode", "w=1"), o["pytest_worker_policy"].__setitem__("optimum_worker_policy", "w=1"))), "VERIFIER_FASTEST_MODE_MISMATCH:pytest_controlled_subset"),
    ("C1M08_FAKE_W4_FASTEST_ON_SCIENCE", lambda d: _mut_json(d / "08_worker_policy_measured_vs_applied_caps_and_provenance.json", lambda o: (o["science_worker_policy"].__setitem__("measured_fastest_mode", "w=4"), o["science_worker_policy"].__setitem__("optimum_worker_policy", "w=4"))), "VERIFIER_FASTEST_MODE_MISMATCH:r2_science_slice"),
    ("C1M09_FORGED_SPEEDUP_999_P21", lambda d: _mut_json(d / "08_worker_policy_measured_vs_applied_caps_and_provenance.json", lambda o: o["pytest_worker_policy"].__setitem__("measured_speedup_w4_vs_w1", 999.0)), "VERIFIER_SPEEDUP_W4_MISMATCH"),
    ("C1M10_ALTER_APPLIED_CAP_ABOVE_MAX_P22", lambda d: _mut_json(d / "08_worker_policy_measured_vs_applied_caps_and_provenance.json", lambda o: o["pytest_worker_policy"].__setitem__("applied_worker_count", 8)), "VERIFIER_APPLIED_WORKER_CAP_EXCEEDED"),
    ("C1M11_EXTRAPOLATE_FULL_SUITE_SPEEDUP_G25", lambda d: _mut_json(d / "09_worker_policy_noise_stability_and_speedup_limits.json", lambda o: o.__setitem__("full_suite_speedup_extrapolated", True)), "FORBIDDEN_FULL_SUITE_SPEEDUP_EXTRAPOLATION"),
    ("C1M12_MUTATE_ECONOMIC_PROVEN_EDGE_P25", lambda d: _mut_json(d / "13_six_scenario_backtest_and_E1_E2_E3_null_economic_regression.json", lambda o: o.__setitem__("RESEARCH_ECONOMIC_VERDICT", "PROVEN_EDGE")), "FORGED_RESEARCH_ECONOMIC_VERDICT"),
    ("C1M13_MUTATE_JUNIT_NODE_ID_P24", lambda d: _mut_json(d / "11_full_pytest_junit_collected_executed_bijection.json", lambda o: o["collection_manifest"]["collected_node_ids"].__setitem__(0, "backend/tests/test_forged.py::test_fake")), "JUNIT_BIJECTION_MISMATCH"),
    ("C1M14_MUTATE_TELEMETRY_VS_GZ_P23", lambda d: _mut_json(d / "15_disk_ram_pid_0p5s_telemetry_and_cleanup.json", lambda o: o.__setitem__("min_available_ram_mib", 9999.0)), "TELEMETRY_15_RAM_MUTATED_VS_GZ"),
    ("C1M15_FAKE_REMOTE_VERIFIED_P29", lambda d: _mut_json(d / "20_phase1_code_push_and_phase2_evidence_remote_readback.json", lambda o: o.__setitem__("publication_status", "PASS_REMOTE_VERIFIED")), "FALSE_REMOTE_READBACK_CLAIM"),
    ("C1M16_FAKE_OOS_AND_H3_PASS_AND_LIVE_ON_P25_P26", lambda d: _mut_json(d / "14_entry_exit_ledger_fee_risk_OOS_and_legacy_guard.json", lambda o: (o.__setitem__("OOS_EVALUATIONS", 1), o.__setitem__("H3_RESIZE_EXECUTION_STATUS", "PASS"), o["runtime_safety_modes"].__setitem__("LIVE", "ON"))), "FORGED_H3_RESIZE_EXECUTION_PASS"),
    ("C1M17_REUSE_STALE_R3_RUN_ID_P28", lambda d: _mut_json(d / "06_fresh_pytest_w1_w2_w4_w1repeat_raw_receipts.json", lambda o: (o.__setitem__("run_id", PRIOR_KH03_R3_RUN_ID), o.__setitem__("tested_executable_sha", PRIOR_KH03_R3_TESTED_SHA))), "BENCH_STALE_R3_REUSED"),
]


def test_kh03_r3_c1_clean_clone_passes_separate_os_verifier(tmp_path):
    cdir = tmp_path / "clean_c1"
    rid, tsha = build_synthetic_valid_kh03_r3_c1_run_dir(cdir)
    rc, out = run_kh03_r3_c1_verifier_subprocess(cdir, rid, tsha)
    assert rc == 0, f"Clean C1 clone MUST pass verifier with exit 0, got rc={rc}:\n{out}"


@pytest.mark.parametrize(
    "mid,mut_fn,expected_tok",
    KH03_R3_C1_VERIFIER_MUTATIONS_SPECS,
    ids=[m[0] for m in KH03_R3_C1_VERIFIER_MUTATIONS_SPECS],
)
def test_kh03_r3_c1_verifier_mutation_detected_fail_closed(tmp_path, mid, mut_fn, expected_tok):
    cdir = tmp_path / f"mut_{mid}"
    rid, tsha = build_synthetic_valid_kh03_r3_c1_run_dir(cdir)
    mut_fn(cdir)
    rc, out = run_kh03_r3_c1_verifier_subprocess(cdir, rid, tsha)
    assert rc != 0, f"Mutation {mid} MUST cause separate-OS verifier non-zero exit"
    assert expected_tok in out, f"Mutation {mid} expected {expected_tok!r}, got:\n{out}"
