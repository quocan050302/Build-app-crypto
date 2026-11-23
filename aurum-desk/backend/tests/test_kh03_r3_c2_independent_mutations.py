"""
aurum-desk/backend/tests/test_kh03_r3_c2_independent_mutations.py

KH-03.R3.C2 Separate-OS Independent Verifier Mutation Suite (18 NEW Non-Redundant Attacks):
Executes 14 NEW separate-OS mutations against the hardened legacy R3 independent verifier
(`kh_03_r3_independent_verifier.py`) + 4 NEW separate-OS mutations against the C2 independent
verifier (`kh_03_r3_c2_independent_verifier.py`), plus archived R3 `LEGACY_DESCRIPTIVE_ONLY`
separation proof (strict mode rejects archived R3; descriptive mode refuses new C2 certification).
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
    select_measured_worker_policy_strict,
)
from tests.test_kh03_r3_resource_async_and_verifier_mutations import (
    build_synthetic_valid_kh03_r3_run_dir,
    run_kh03_r3_verifier_subprocess,
)
from tools.kh_03_r3_c2_independent_verifier import (
    SCHEMA_VERSION_KH03_R3_C2,
    SEALED_BASELINE_PARITY_FINGERPRINTS,
    SEALED_R3_1X_ECONOMIC_ANCHORS,
    SPEC_VERSION_KH03_R3_C2,
    _audit_telemetry_gz_vs_13_c2,
    _independent_recompute_raw_bench_c2,
    recompute_kh03_r3_c2_26_gates_and_verdicts,
)
from tools.kh_03_r3_independent_verifier import (
    ARCHIVED_ORIGINAL_R3_RUN_ID_VERIF,
    ARCHIVED_ORIGINAL_R3_TESTED_SHA_VERIF,
)


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _mut_json(p: Path, fn: Callable[[Dict[str, Any]], None]) -> None:
    d = json.loads(p.read_text("utf-8"))
    fn(d)
    p.write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")


def build_synthetic_valid_kh03_r3_c2_run_dir(run_dir: Path) -> Tuple[str, str]:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "junit").mkdir(exist_ok=True)
    (run_dir / "checksums").mkdir(exist_ok=True)

    tsha = "b" * 40
    rid = f"20261123T223000Z_{tsha[:12]}"

    f06 = {
        "spec_version": SPEC_VERSION_KH03_R3_C2, "schema_version": SCHEMA_VERSION_KH03_R3_C2,
        "run_id": rid, "tested_executable_sha": tsha, "benchmark_kind": BENCHMARK_KIND_PYTEST,
        "status": "PASS", "all_4_runs_identical": True,
        "measured_speedup_w2_vs_w1": 1.5, "measured_speedup_w4_vs_w1": 2.0,
        "runs": [
            {"mode_label": "w=1", "workers": 1, "worker_pids": [101], "wall_clock_seconds": 30.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
            {"mode_label": "w=2", "workers": 2, "worker_pids": [201, 202], "wall_clock_seconds": 20.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
            {"mode_label": "w=4", "workers": 4, "worker_pids": [401, 402, 403, 404], "wall_clock_seconds": 15.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
            {"mode_label": "w=1_repeat", "workers": 1, "worker_pids": [501], "wall_clock_seconds": 29.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
        ],
    }
    f07 = {
        "spec_version": SPEC_VERSION_KH03_R3_C2, "schema_version": SCHEMA_VERSION_KH03_R3_C2,
        "run_id": rid, "tested_executable_sha": tsha, "benchmark_kind": BENCHMARK_KIND_SCIENCE,
        "status": "PASS", "all_4_runs_identical": True,
        "measured_speedup_w2_vs_w1": 1.25, "measured_speedup_w4_vs_w1": 1.15,
        "runs": [
            {"mode_label": "w=1", "workers": 1, "chunks_count": 1, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": 1.38, "canonical_sorted_sha256": "2" * 64},
            {"mode_label": "w=2", "workers": 2, "chunks_count": 2, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": 1.104, "canonical_sorted_sha256": "2" * 64},
            {"mode_label": "w=4", "workers": 4, "chunks_count": 4, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": 1.2, "canonical_sorted_sha256": "2" * 64},
            {"mode_label": "w=1_repeat", "workers": 1, "chunks_count": 1, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": 1.30, "canonical_sorted_sha256": "2" * 64},
        ],
    }
    p_pol = select_measured_worker_policy_strict(f06, benchmark_kind=BENCHMARK_KIND_PYTEST, system_limits={"configured_child_process_cap": 4, "currently_allowed_worker_cap": 3, "full_suite_safe_worker_cap": 3, "shard_exclusive_cap": 3}, expected_run_id=rid, expected_tested_sha=tsha)
    s_pol = select_measured_worker_policy_strict(f07, benchmark_kind=BENCHMARK_KIND_SCIENCE, system_limits={"configured_child_process_cap": 4, "currently_allowed_worker_cap": 4, "full_suite_safe_worker_cap": 4, "shard_exclusive_cap": 4}, expected_run_id=rid, expected_tested_sha=tsha)

    n_tc = 5800
    c_ids = [f"backend/tests/test_kh03_r3_c2_synth.py::test_c_{i:04d}" for i in range(n_tc)]
    xp = run_dir / "junit" / "SHARD_01.xml"
    tcs = "".join(f'<testcase classname="backend.tests.test_kh03_r3_c2_synth" name="test_c_{i:04d}" time="0.001"/>' for i in range(n_tc))
    xp.write_text(f'<?xml version="1.0" encoding="utf-8"?><testsuite tests="{n_tc}" failures="0" errors="0" skipped="0">{tcs}</testsuite>', encoding="utf-8")

    base_arms = {ac: {"closed": anc["closed"], "wins": anc["wins"], "losses": anc["losses"], "gross_pnl_usd": round(anc["net_pnl_usd"] + 100.0, 4), "total_fees_usd": 100.0, "net_pnl_usd": anc["net_pnl_usd"], "expectancy_r": anc["expectancy_r"]} for ac, anc in SEALED_R3_1X_ECONOMIC_ANCHORS.items()}
    scen_11 = {sc: {"arms": base_arms} for sc in EXPECTED_SIX_SCENARIOS_KH03}

    mem_gz = run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    with gzip.open(mem_gz, "wt", encoding="utf-8") as mgz:
        for i in range(4):
            mgz.write(json.dumps({"ts_ms": 1795439000000 + i * 500, "available_ram_bytes": 3_500_000_000, "available_mib": 3337.86, "child_count": 3, "worker_pids": [1, 2, 3], "disk_free_gib": 32.5}) + "\n")

    docs: Dict[str, Dict[str, Any]] = {
        "00": {"spec_version": SPEC_VERSION_KH03_R3_C2, "run_id": rid, "branch": "feature/v17-9-2-structural-sl-tp-from-qa", "start_head_sha": tsha, "end_head_sha": tsha, "tested_executable_sha": tsha, "git_clean_at_start": True, "local_uncommitted_changes": False, "orchestrator_pid": -9999, "owner_disk_preflight_waiver": False, "strict_multi_mount_probe": {"can_run_heavy_stages": True, "owner_waiver_permitted": False, "probe_errors": [], "distinct_mounts": [{"free_gib": 32.5, "projected_post_reserve_gib": 31.5}]}, "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"}, "ACTIVATION_READY": "NO", "status": "PASS"},
        "01": {"spec_version": SPEC_VERSION_KH03_R3_C2, "status": "PASS", "locked_files_modified_count": 0, "unauthorized_changed_files_count": 0, "forbidden_order_import_hits": [], "cost_assumptions_defaults_intact": True, "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"}, "ACTIVATION_READY": "NO"},
        "02": {"spec_version": SPEC_VERSION_KH03_R3_C2, "status": "PASS", "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02, "canonical_dataset_sha256_match": True, "all_prior_sealed_artifacts_intact": True, "prior_sealed_artifacts_immutability": {"C1": {"match": True}}, "c4_provenance_and_oos_boundary": {"network_capture_enabled": False, "live_network_capture_executed": False, "LIVE_NETWORK_VALIDATION": "NOT_RUN_BY_DESIGN", "real_oos_escrow_final_record_count": 0}},
        "03": {"spec_version": SPEC_VERSION_KH03_R3_C2, "status": "PASS", "red_cases_reproduced_count": 6, "pre_fix_verifier_swallowed_forged_f19_proved": True},
        "04": {"spec_version": SPEC_VERSION_KH03_R3_C2, "status": "PASS", "cases_passed": 40, "cases_failed": 0, "nonfinite_mixed_missing_forged_all_rejected": True, "g22_invalid_blocks_overall_engineering_pass": True},
        "05": {"spec_version": SPEC_VERSION_KH03_R3_C2, "status": "PASS", "clean_c2_r3_run_passes": True, "forged_f19_rejected_fail_closed": True, "archived_r3_descriptive_audit_classification": "LEGACY_DESCRIPTIVE_ONLY", "archived_r3_admitted_for_new_c2_certification": False, "archived_r3_strict_mode_rejected": True},
        "06": f06, "07": f07,
        "08": {"spec_version": SPEC_VERSION_KH03_R3_C2, "status": "PASS", "run_id": rid, "tested_executable_sha": tsha, "pytest_worker_policy": p_pol, "science_worker_policy": s_pol, "full_suite_speedup_extrapolated": False},
        "09": {"spec_version": SPEC_VERSION_KH03_R3_C2, "status": "PASS", "collected_count": n_tc, "executed_count": n_tc, "passed_count": n_tc, "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0, "junit_bijection_exact": True, "collection_manifest": {"run_id": rid, "tested_executable_sha": tsha, "collected_node_ids_count": n_tc, "collected_node_ids": c_ids}, "shard_manifest": {"run_id": rid, "tested_executable_sha": tsha, "nonzero_exit_shards_count": 0, "shards": [{"shard_id": "SHARD_01", "exit_code": 0, "timed_out": False, "reused": False, "junit_xml_path": "junit/SHARD_01.xml", "xml_sha256": _sha256_file(xp), "scheduled_node_ids": c_ids}]}},
        "10": {"spec_version": SPEC_VERSION_KH03_R3_C2, "BASELINE_PARITY_VERDICT": "PASS", "a1_population_count": 56710, "a1_mismatches_count": 0, "si8_population_count": 6375, "si8_mismatches_count": 0, "three_adapter_population_count": 246, "three_adapter_mismatches_count": 0, "d4_historical_closed_trades": 0, "per_decision_and_episode_fingerprints": {**SEALED_BASELINE_PARITY_FINGERPRINTS, "all_sealed_fingerprints_exact_match": True}},
        "11": {"spec_version": SPEC_VERSION_KH03_R3_C2, "status": "PASS", "historical_r3_1x_anchors_exact_match": True, "dynamic_vs_fixed_fill_populations_separated": True, "zero_fee_display_bugs_count": 0, "dynamic_full_rerisk_six_scenarios": scen_11, "fixed_fills_descriptive_repricing": {"NC_NULL_TRIGGER": {"fixed_1x_closed_count": 203}}, "passing_dev_candidate_arms": [], "DEV_RESEARCH_CANDIDATE": "NO_ECONOMIC_ADVANCEMENT_CANDIDATE", "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE", "FRESH_OOS": "BLOCKED_NO_UNTOUCHED_DATA", "OOS_EVALUATIONS": 0, "observed_l1_bbo_available": False, "H3_RESIZE_EXECUTION_STATUS": "BLOCKED_ADVISORY_FILTER_ONLY", "h3_promoted_as_executable": False, "h3_r2_excluded_from_executable_arms": True, "r2_forward_only_embargo_240m_verified": True, "ACCOUNT_EFFECTIVE_FEE_VERDICT": "UNKNOWN_NOT_LOCALLY_VERIFIED", "LEGACY_METHOD_GUARD": "PASS", "unauthorized_r1_callers_count": 0, "ACTIVATION_READY": "NO", "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"}},
        "12": {"spec_version": SPEC_VERSION_KH03_R3_C2, "status": "PASS", "c1_worker_policy_service_untouched": True},
        "13": {"spec_version": SPEC_VERSION_KH03_R3_C2, "min_disk_free_gib": 32.5, "min_available_ram_mib": 3337.86, "max_descendant_children": 3, "orphan_children_after_cleanup": 0, "sentinel_temp_cleaned_up": True, "owner_disk_preflight_waiver": False},
        "14": {"spec_version": SPEC_VERSION_KH03_R3_C2, "status": "PASS", "inherited_c1_r3_controls_count": 208, "inherited_c1_r3_failed_controls": 0, "new_c2_controls_count": 16, "new_c2_failed_controls": 0, "total_controls": 224, "unique_fault_ids": 224, "failed_controls": 0},
        "15": {"spec_version": SPEC_VERSION_KH03_R3_C2, "status": "PASS", "clean_clone_verifier_green": True, "inherited_r3_c1_mutations_tested_count": 33, "inherited_r3_c1_mutations_failed_detection_count": 0, "new_c2_mutations_tested_count": 18, "new_c2_mutations_failed_detection_count": 0},
        "18": {"spec_version": SPEC_VERSION_KH03_R3_C2, "run_id": rid, "tested_executable_sha": tsha, "publication_status": "PRE_PUSH_PENDING", "remote_readback_sha256_verified": False, "evidence_commit_sha": None, "ci_status": "CI_UNKNOWN_NO_ACTIONS_RUN", "owner_disk_preflight_waiver": False},
    }
    p_rec = _independent_recompute_raw_bench_c2(f06, p_pol, kind=BENCHMARK_KIND_PYTEST, expected_run_id=rid, expected_tested_sha=tsha, max_safe_cap=3)
    s_rec = _independent_recompute_raw_bench_c2(f07, s_pol, kind=BENCHMARK_KIND_SCIENCE, expected_run_id=rid, expected_tested_sha=tsha, max_safe_cap=4)
    m_aud = _audit_telemetry_gz_vs_13_c2(mem_gz, docs["13"])
    r17 = recompute_kh03_r3_c2_26_gates_and_verdicts(docs=docs, pytest_recomp=p_rec, science_recomp=s_rec, mem_audit=m_aud, junit_bijection_ok=True, economic_ok=True, for_final_verifier_manifest=False)
    docs["17"] = {"spec_version": SPEC_VERSION_KH03_R3_C2, "run_id": rid, "tested_executable_sha": tsha, "overall_classification": r17["overall_classification"], "gates_26": r17["gates_26"], "verdicts": r17["verdicts"]}

    fn_map = {
        "00": "00_branch_remote_sha_dirty_preflight.json",
        "01": "01_locked_code_ownership_and_ast_parity.json",
        "02": "02_prior_r1_r2_r3_c1_artifact_sha_immutability.json",
        "03": "03_legacy_r3_false_pass_red_proof.json",
        "04": "04_r3_f19_policy_condition_green_boundary_suite.json",
        "05": "05_r3_independent_g22_raw_benchmark_recompute.json",
        "06": "06_fresh_pytest_w1_w2_w4_w1repeat.json",
        "07": "07_fresh_science_w1_w2_w4_w1repeat.json",
        "08": "08_measured_fastest_vs_safe_applied_worker_caps.json",
        "09": "09_full_repo_pytest_junit_1to1.json",
        "10": "10_full_A1_SI8_adapter_D4_parity.json",
        "11": "11_six_cost_e1_e2_e3_null_regression.json",
        "12": "12_legacy_and_c1_old_source_do_not_change.json",
        "13": "13_memory_disk_pid_telemetry.json",
        "14": "14_inherited_plus_new_red_green_fault_controls.json",
        "15": "15_independent_verifier_mutation_test_results.json",
        "17": "17_orthogonal_gate_matrix_and_verdicts.json",
        "18": "18_phase1_code_commit_push_receipt.json",
    }
    for k, fn in fn_map.items():
        (run_dir / fn).write_text(json.dumps(docs[k], indent=2, sort_keys=True), encoding="utf-8")
    (run_dir / "README_KH03_R3_C2.md").write_text(f"# KH-03.R3.C2 {rid}\nACTIVATION_READY=NO\nNO_PROVEN_EDGE\nOOS_EVALUATIONS=0\nCI_UNKNOWN_NO_ACTIONS_RUN\n", encoding="utf-8")
    (run_dir / "HANDOVER_KH03_R3_C2_VI.txt").write_text(f"KH-03.R3.C2 {rid}\nACTIVATION_READY=NO\nNO_PROVEN_EDGE\nOOS_EVALUATIONS=0\nCI_UNKNOWN_NO_ACTIONS_RUN\n", encoding="utf-8")
    return rid, tsha


def run_kh03_r3_c2_verifier_subprocess(run_dir: Path, run_id: str, tested_sha: str) -> Tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)])
    p = subprocess.run(
        [sys.executable, str(APP_ROOT / "tools" / "kh_03_r3_c2_independent_verifier.py"), "--run-dir", str(run_dir), "--expected-run-id", run_id, "--expected-tested-sha", tested_sha, "--skip-live-repo-pointers", "--no-write-manifest"],
        cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=60,
    )
    return int(p.returncode), (p.stdout or "") + "\n" + (p.stderr or "")


def _inject_raw_nan_json(p: Path) -> None:
    txt = p.read_text("utf-8").replace('"wall_clock_seconds": 30.0', '"wall_clock_seconds": NaN', 1)
    p.write_text(txt, encoding="utf-8")


# 14 NEW separate-OS mutations directly attacking hardened legacy R3 verifier (`kh_03_r3_independent_verifier.py`)
KH03_R3_C2_LEGACY_R3_MUTATIONS_SPECS: List[Tuple[str, Callable[[Path], None], str]] = [
    ("C2M01_R3_FORGED_F19_PASS_ON_BLOCKED_POLICY", lambda d: _mut_json(d / "19_w1_w2_w4_w1repeat_hashes_timings_speedups.json", lambda o: o["pytest_optimum_worker_policy"].__setitem__("status", "BLOCKED_INVALID_BENCHMARK")), "R3V_PYTEST_POLICY_STATUS_NOT_PASS"),
    ("C2M02_R3_FORGED_CAN_CERTIFY_TRUE_MISSING_W4", lambda d: _mut_json(d / "19_w1_w2_w4_w1repeat_hashes_timings_speedups.json", lambda o: o["pytest_controlled_subset_w1_w2_w4_repeat"].__setitem__("runs", [r for r in o["pytest_controlled_subset_w1_w2_w4_repeat"]["runs"] if r["mode_label"] != "w=4"])), "R3V_PYTEST_MISSING_MODE:w=4"),
    ("C2M03_R3_STRING_TRUE_CAN_CERTIFY", lambda d: _mut_json(d / "19_w1_w2_w4_w1repeat_hashes_timings_speedups.json", lambda o: o.__setitem__("can_certify", "true")), "F19_CAN_CERTIFY_NOT_STRICT_TRUE"),
    ("C2M04_R3_EMPTY_MEAN_WALL_MAP", lambda d: _mut_json(d / "19_w1_w2_w4_w1repeat_hashes_timings_speedups.json", lambda o: o["pytest_optimum_worker_policy"].__setitem__("mean_wall_sec_by_mode", {})), "R3V_PYTEST_EMPTY_MEAN_WALL_MAP"),
    ("C2M05_R3_JSON_NAN_DURATION_IN_19", lambda d: _inject_raw_nan_json(d / "19_w1_w2_w4_w1repeat_hashes_timings_speedups.json"), "NONSTANDARD_JSON_CONSTANT_FORBIDDEN"),
    ("C2M06_R3_NEGATIVE_DURATION_IN_19", lambda d: _mut_json(d / "19_w1_w2_w4_w1repeat_hashes_timings_speedups.json", lambda o: o["pytest_controlled_subset_w1_w2_w4_repeat"]["runs"][2].__setitem__("wall_clock_seconds", -5.0)), "R3V_PYTEST_INVALID_DURATION_RANGE"),
    ("C2M07_R3_CROSS_KIND_KEY_IN_SCIENCE", lambda d: _mut_json(d / "19_w1_w2_w4_w1repeat_hashes_timings_speedups.json", lambda o: o["r2_science_slice_w1_w2_w4_repeat"]["runs"][0].__setitem__("wall_clock_seconds", 2.0)), "R3V_SCIENCE_CROSS_KIND_KEY:wall_clock_seconds"),
    ("C2M08_R3_SEMANTIC_SHA_MISMATCH_FORGED_TRUE", lambda d: _mut_json(d / "19_w1_w2_w4_w1repeat_hashes_timings_speedups.json", lambda o: o["pytest_controlled_subset_w1_w2_w4_repeat"]["runs"][1].__setitem__("canonical_sorted_outcome_sha256", "9" * 64)), "R3V_PYTEST_SEMANTIC_SHA_MISMATCH"),
    ("C2M09_R3_APPLIED_WORKER_COUNT_5_OVER_CAP", lambda d: _mut_json(d / "19_w1_w2_w4_w1repeat_hashes_timings_speedups.json", lambda o: o["pytest_optimum_worker_policy"].__setitem__("applied_worker_count", 5)), "R3V_PYTEST_APPLIED_WORKER_CAP_EXCEEDED"),
    ("C2M10_R3_MISSING_W1_REPEAT", lambda d: _mut_json(d / "19_w1_w2_w4_w1repeat_hashes_timings_speedups.json", lambda o: o["r2_science_slice_w1_w2_w4_repeat"].__setitem__("runs", [r for r in o["r2_science_slice_w1_w2_w4_repeat"]["runs"] if r["mode_label"] != "w=1_repeat"])), "R3V_SCIENCE_MISSING_MODE:w=1_repeat"),
    ("C2M11_R3_FASTEST_CLAIMED_W1_WHEN_W4_FASTEST", lambda d: _mut_json(d / "19_w1_w2_w4_w1repeat_hashes_timings_speedups.json", lambda o: (o["pytest_optimum_worker_policy"].__setitem__("measured_fastest_mode", "w=1"), o["pytest_optimum_worker_policy"].__setitem__("optimum_worker_policy", "w=1"))), "R3V_PYTEST_FASTEST_MODE_MISMATCH"),
    ("C2M12_R3_STALE_ARCHIVED_R3_ID_IN_19", lambda d: _mut_json(d / "19_w1_w2_w4_w1repeat_hashes_timings_speedups.json", lambda o: o["raw_benchmark_identity"].__setitem__("run_id", ARCHIVED_ORIGINAL_R3_RUN_ID_VERIF)), "F19_IDENTITY_RUN_ID_MISMATCH"),
    ("C2M13_R3_VERSION_STRIP_DOWNGRADE_ATTACK", lambda d: _mut_json(d / "19_w1_w2_w4_w1repeat_hashes_timings_speedups.json", lambda o: o.pop("worker_policy_version", None)), "F19_MISSING_OR_FORGED_C2_WORKER_POLICY_VERSION"),
    ("C2M14_R3_TAMPERED_G22_STORED_VS_RECOMPUTED", lambda d: _mut_json(d / "23_orthogonal_gate_matrix_economic_kh04_oos_activation.json", lambda o: o["gates_30"].__setitem__("G22_W1_W2_W4_W1REPEAT_SEMANTIC_DETERMINISM", "FAIL")), "GATE_TAMPER_OR_DRIFT:G22_W1_W2_W4_W1REPEAT_SEMANTIC_DETERMINISM"),
]

# 4 NEW separate-OS mutations attacking C2 verifier (`kh_03_r3_c2_independent_verifier.py`)
KH03_R3_C2_OWN_VERIFIER_MUTATIONS_SPECS: List[Tuple[str, Callable[[Path], None], str]] = [
    ("C2M15_C2_FORGED_POLICY_PASS_EMPTY_MEANS", lambda d: _mut_json(d / "08_measured_fastest_vs_safe_applied_worker_caps.json", lambda o: o["pytest_worker_policy"].__setitem__("mean_wall_sec_by_mode", {})), "C2V_EMPTY_MEANS"),
    ("C2M16_C2_FORGED_TELEMETRY_VS_GZ", lambda d: _mut_json(d / "13_memory_disk_pid_telemetry.json", lambda o: o.__setitem__("min_available_ram_mib", 9999.0)), "TELEMETRY_13_RAM_MUTATED_VS_GZ"),
    ("C2M17_C2_FORGED_REMOTE_VERIFIED_NO_READBACK", lambda d: _mut_json(d / "18_phase1_code_commit_push_receipt.json", lambda o: o.__setitem__("publication_status", "PASS_REMOTE_VERIFIED")), "FALSE_REMOTE_READBACK_CLAIM"),
    ("C2M18_C2_FORGED_PROVEN_EDGE_AND_OOS_ONE", lambda d: _mut_json(d / "11_six_cost_e1_e2_e3_null_regression.json", lambda o: (o.__setitem__("RESEARCH_ECONOMIC_VERDICT", "PROVEN_EDGE"), o.__setitem__("OOS_EVALUATIONS", 1))), "FORGED_RESEARCH_ECONOMIC_VERDICT"),
]


def test_kh03_r3_c2_clean_clone_passes_separate_os_verifier(tmp_path):
    cdir = tmp_path / "clean_c2"
    rid, tsha = build_synthetic_valid_kh03_r3_c2_run_dir(cdir)
    rc, out = run_kh03_r3_c2_verifier_subprocess(cdir, rid, tsha)
    assert rc == 0, f"Clean C2 clone MUST pass verifier with exit 0, got rc={rc}:\n{out}"


@pytest.mark.parametrize("mid,mut_fn,exp_tok", KH03_R3_C2_LEGACY_R3_MUTATIONS_SPECS, ids=[m[0] for m in KH03_R3_C2_LEGACY_R3_MUTATIONS_SPECS])
def test_kh03_r3_c2_legacy_r3_verifier_mutation_rejected(tmp_path, mid, mut_fn, exp_tok):
    cdir = tmp_path / f"r3_{mid}"
    rid, tsha = build_synthetic_valid_kh03_r3_run_dir(cdir)
    mut_fn(cdir)
    rc, out = run_kh03_r3_verifier_subprocess(cdir, rid, tsha)
    assert rc != 0, f"Legacy R3 mutation {mid} MUST fail closed (non-zero exit)"
    assert exp_tok in out, f"Legacy R3 mutation {mid} expected {exp_tok!r}, got:\n{out}"


@pytest.mark.parametrize("mid,mut_fn,exp_tok", KH03_R3_C2_OWN_VERIFIER_MUTATIONS_SPECS, ids=[m[0] for m in KH03_R3_C2_OWN_VERIFIER_MUTATIONS_SPECS])
def test_kh03_r3_c2_own_verifier_mutation_rejected(tmp_path, mid, mut_fn, exp_tok):
    cdir = tmp_path / f"c2_{mid}"
    rid, tsha = build_synthetic_valid_kh03_r3_c2_run_dir(cdir)
    mut_fn(cdir)
    rc, out = run_kh03_r3_c2_verifier_subprocess(cdir, rid, tsha)
    assert rc != 0, f"C2 mutation {mid} MUST fail closed (non-zero exit)"
    assert exp_tok in out, f"C2 mutation {mid} expected {exp_tok!r}, got:\n{out}"


def test_archived_original_r3_rejected_strict_and_descriptive_only_in_legacy_mode():
    r3_archived_dir = REPO_ROOT / f"aurum-desk/docs/artifacts/kh_03_r3_entry_exit_diagnosis/{ARCHIVED_ORIGINAL_R3_RUN_ID_VERIF}"
    assert r3_archived_dir.is_dir()

    # 1. Default strict mode MUST refuse archived R3 run (cannot masquerade as new C2 certification!)
    rc_strict, out_strict = run_kh03_r3_verifier_subprocess(
        r3_archived_dir, ARCHIVED_ORIGINAL_R3_RUN_ID_VERIF, ARCHIVED_ORIGINAL_R3_TESTED_SHA_VERIF
    )
    assert rc_strict != 0
    assert "STALE_ARCHIVED_R3_REFUSED_FOR_NEW_CERTIFICATION" in out_strict

    # 2. Explicit --legacy-descriptive-audit-only reads it cleanly without mutating or promoting to C2 PASS
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)])
    p_desc = subprocess.run(
        [
            sys.executable, str(APP_ROOT / "tools" / "kh_03_r3_independent_verifier.py"),
            "--run-dir", str(r3_archived_dir),
            "--expected-run-id", ARCHIVED_ORIGINAL_R3_RUN_ID_VERIF,
            "--expected-tested-sha", ARCHIVED_ORIGINAL_R3_TESTED_SHA_VERIF,
            "--no-write-manifest", "--legacy-descriptive-audit-only",
        ],
        cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=60,
    )
    assert p_desc.returncode == 0, f"Descriptive audit of archived R3 failed:\n{p_desc.stdout}\n{p_desc.stderr}"
