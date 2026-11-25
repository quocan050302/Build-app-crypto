"""
aurum-desk/backend/tests/test_kh03_r5_c1_independent_verifier_mutations.py

KH-03.R5.C1 — 22 NEW Separate-OS Independent Verifier Mutation Attacks (`C1M01`..`C1M22` / D01..D22).
Exports `build_synthetic_valid_kh03_r5_c1_run_dir`, `run_kh03_r5_c1_verifier_subprocess`,
and `KH03_R5_C1_OWN_VERIFIER_MUTATIONS_SPECS` for both unit pytest and acceptance runner.
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
from services.kh_03_r3_c1_worker_policy_service import (
    BENCHMARK_KIND_PYTEST,
    BENCHMARK_KIND_SCIENCE,
    select_measured_worker_policy_strict,
)
from services.kh_03_r5_c1_readiness_and_red_service import (
    BASELINE_REVIEW_HEAD_SHA_R5_C1,
    MANDATORY_BRANCH_KH03_R5_C1,
    derive_r5_c1_readiness_verdicts,
)
from services.kh_03_r5_market_data_foundation_service import (
    SCHEMA_VERSION_KH03_R5_C1,
    SPEC_VERSION_KH03_R5_C1,
)
from tests.test_kh03_r4_economic_accounting import _make_synthetic_anchor_forensic_rows
from tools.kh_03_r3_c1_independent_verifier import SEALED_BASELINE_PARITY_FINGERPRINTS
from tools.kh_03_r5_c1_independent_verifier import (
    PRIOR_KH03_R5_RUN_ID,
    PRIOR_KH03_R5_TESTED_SHA,
    _audit_r4_replay_ledger_12_c1,
    _audit_telemetry_gz_vs_17_c1,
    _verify_benchmark_16_c1,
)


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _mut_json(p: Path, fn: Callable[[Dict[str, Any]], None]) -> None:
    d = json.loads(p.read_text("utf-8"))
    fn(d)
    p.write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")


def build_synthetic_valid_kh03_r5_c1_run_dir(run_dir: Path) -> Tuple[str, str]:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "junit").mkdir(exist_ok=True)
    (run_dir / "checksums").mkdir(exist_ok=True)

    tsha = "a" * 40
    rid = f"20261125T080000Z_{tsha[:12]}"

    rows = _make_synthetic_anchor_forensic_rows()
    led_rel = "logs/r4_replay_forensic_ledger_1x.jsonl.gz"
    led_gz = run_dir / led_rel
    with gzip.open(led_gz, "wt", encoding="utf-8") as gz:
        for r in rows:
            gz.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")

    mem_gz = run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    with gzip.open(mem_gz, "wt", encoding="utf-8") as mgz:
        for i in range(4):
            mgz.write(json.dumps({"ts_ms": 1795500000000 + i * 500, "available_ram_bytes": 3_400_000_000, "available_mib": 3242.49, "child_count": 2, "worker_pids": [1, 2], "disk_free_gib": 30.5}) + "\n")

    n_tc = 5940
    c_ids = [f"backend/tests/test_kh03_r5_c1_synth.py::test_c1_{i:04d}" for i in range(n_tc)]
    xp = run_dir / "junit" / "SHARD_01.xml"
    tcs = "".join(f'<testcase classname="backend.tests.test_kh03_r5_c1_synth" name="test_c1_{i:04d}" time="0.001"/>' for i in range(n_tc))
    xp.write_text(f'<?xml version="1.0" encoding="utf-8"?><testsuite tests="{n_tc}" failures="0" errors="0" skipped="0">{tcs}</testsuite>', encoding="utf-8")
    nodes_rel = "logs/pytest_collected_executed_nodeids.jsonl.gz"
    nodes_gz = run_dir / nodes_rel
    with gzip.open(nodes_gz, "wt", encoding="utf-8") as ngz:
        for cid in c_ids:
            ngz.write(cid + "\n")

    pb_raw = {
        "spec_version": SPEC_VERSION_KH03_R5_C1, "schema_version": SCHEMA_VERSION_KH03_R5_C1,
        "run_id": rid, "tested_executable_sha": tsha, "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02,
        "benchmark_kind": BENCHMARK_KIND_PYTEST, "status": "PASS", "all_4_runs_identical": True,
        "runs": [
            {"mode_label": "w=1", "workers": 1, "worker_pids": [101], "wall_clock_seconds": 30.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
            {"mode_label": "w=2", "workers": 2, "worker_pids": [201, 202], "wall_clock_seconds": 20.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
            {"mode_label": "w=4", "workers": 4, "worker_pids": [401, 402, 403, 404], "wall_clock_seconds": 15.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
            {"mode_label": "w=1_repeat", "workers": 1, "worker_pids": [501], "wall_clock_seconds": 29.5, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
        ],
    }
    sb_raw = {
        "spec_version": SPEC_VERSION_KH03_R5_C1, "schema_version": SCHEMA_VERSION_KH03_R5_C1,
        "run_id": rid, "tested_executable_sha": tsha, "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02,
        "benchmark_kind": BENCHMARK_KIND_SCIENCE, "status": "PASS", "all_4_runs_identical": True,
        "runs": [
            {"mode_label": "w=1", "workers": 1, "chunks_count": 1, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": 1.40, "canonical_sorted_sha256": "2" * 64},
            {"mode_label": "w=2", "workers": 2, "chunks_count": 2, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": 1.12, "canonical_sorted_sha256": "2" * 64},
            {"mode_label": "w=4", "workers": 4, "chunks_count": 4, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": 1.25, "canonical_sorted_sha256": "2" * 64},
            {"mode_label": "w=1_repeat", "workers": 1, "chunks_count": 1, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": 1.38, "canonical_sorted_sha256": "2" * 64},
        ],
    }
    p_pol = select_measured_worker_policy_strict(pb_raw, benchmark_kind=BENCHMARK_KIND_PYTEST, system_limits={"configured_child_process_cap": 4, "currently_allowed_worker_cap": 3, "full_suite_safe_worker_cap": 3, "shard_exclusive_cap": 3}, expected_run_id=rid, expected_tested_sha=tsha, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02)
    s_pol = select_measured_worker_policy_strict(sb_raw, benchmark_kind=BENCHMARK_KIND_SCIENCE, system_limits={"configured_child_process_cap": 4, "currently_allowed_worker_cap": 4, "full_suite_safe_worker_cap": 4, "shard_exclusive_cap": 4}, expected_run_id=rid, expected_tested_sha=tsha, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02)

    docs: Dict[str, Dict[str, Any]] = {
        "00": {"branch": MANDATORY_BRANCH_KH03_R5_C1, "baseline_review_head_sha": BASELINE_REVIEW_HEAD_SHA_R5_C1, "start_head_sha": tsha, "end_head_sha": tsha, "git_clean_at_start": True, "local_uncommitted_changes": False, "orchestrator_pid": -9999, "owner_disk_preflight_waiver": False, "strict_multi_mount_probe": {"can_run_heavy_stages": True, "owner_waiver_permitted": False, "probe_errors": [], "distinct_mounts": [{"free_gib": 30.5, "projected_post_reserve_gib": 30.0}]}, "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"}, "ACTIVATION_READY": "NO", "status": "PASS"},
        "01": {"status": "PASS", "locked_files_modified_count": 0, "unauthorized_changed_files_count": 0, "forbidden_order_import_hits": [], "cost_assumptions_defaults_intact": True, "legacy_guard_status": "PASS", "unauthorized_r1_callers_count": 0},
        "02": {"status": "PASS", "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02, "canonical_dataset_sha256_match": True, "all_prior_sealed_artifacts_intact": True, "c4_provenance_and_oos_boundary": {"real_oos_escrow_final_record_count": 0}},
        "03": {"status": "PASS", "baseline_sha": BASELINE_REVIEW_HEAD_SHA_R5_C1, "all_p1_red_reproduced": True, "p1_a_red_reproduced": True, "p1_b_red_reproduced": True, "p1_c_red_reproduced": True, "working_tree_mutated": False, "external_network_sockets_used": False, "subprocess_exit_code": 0, "stdout_sha256": "b" * 64, "observations": {f"k{i}": {"weakness_reproduced": True} for i in range(8)}},
        "04": {"status": "PASS", "strict_grant_schema_verified": True, "bool_nan_inf_missing_rejected": True, "cli_env_prompt_bypass_blocked": True, "oos_purpose_rejected": True, "default_deny_attestation_verified": True, "forged_trust_context_dict_rejected": True, "live_capture_authorization_status": "BLOCKED_OWNER_AUTHORIZATION", "live_network_permitted": False, "full_window_budget_endpoint_binding_verified": True, "grant_a_planner_b_bypass_blocked": True, "silent_clamp_forbidden_verified": True},
        "05": {"status": "PASS", "mutated_plan_same_digest_rejected": True, "mutated_plan_recomputed_digest_rejected": True, "forged_unbound_plan_rejected": True, "recomputed_digest_zero_dispatches": True, "recomputed_digest_observed_calls": 0, "post_enqueue_mutation_rejected": True, "expiry_during_wait_rejected": True, "run_id_and_destination_mismatch_rejected": True, "post_expiry_observed_calls": 0},
        "06": {"status": "PASS", "deadline_expired_during_wait_blocked": True, "cancel_during_wait_blocked": True, "post_deadline_observed_calls": 0, "timeout_bounded_by_remaining_session_and_grant": True, "observed_remaining_timeout_sec": 1.4, "expected_max_remaining_timeout_sec": 1.5},
        "07": {"status": "PASS", "retry_after_never_shortened_verified": True, "retry_after_exceeding_budget_stops": True, "non_retryable_classes_never_retried": True, "actual_dispatch_cadence_monotonic": True, "late_inflight_response_discarded": True, "bounded_drain_verified": True, "undrained_inflight_workers": 0},
        "08": {"status": "PASS", "concurrent_request_budget_no_oversubscribe": True, "cancelled_reservation_not_counted_dispatched": True, "observed_dispatched_vs_cap_excess": 0, "wire_vs_accepted_bytes_separated": True, "parallel_byte_reservation_no_oversubscribe": True, "unsupported_legacy_transport_read_cap_blocked": True},
        "09": {"status": "PASS", "monitor_none_blocked": True, "empty_and_missing_keys_blocked": True, "nan_inf_bool_str_neg_blocked": True, "stale_and_future_timestamp_blocked": True, "wrong_volume_and_exception_blocked": True, "invalid_monitor_observed_calls": 0},
        "10": {"status": "PASS", "staging_containment_and_symlink_denied": True, "mandatory_expected_raw_sha_enforced": True, "stat_before_read_oversize_blocked": True, "custody_chain_verified": True, "owned_cleanup_only_verified": True, "aborted_deleted_staging_counted_zero_accepted": True, "aborted_accepted_records_count": 0, "aborted_removed_staged_files_count": 1, "no_verified_dev_from_aborted_session": True},
        "11": {"status": "PASS", "external_market_requests_attempted": 0, "accepted_real_observed_records": 0, "oos_reads_executed": 0, "oos_evaluations": 0, "qa_grant_cannot_authorize_live": True, "fake_transport_cannot_mint_observed_verified": True, "qa_cannot_use_live_stdlib_transport": True},
        "12": {"status": "PASS", "fresh_replay_executed_not_copied": True, "new_data_used_in_backtest": False, "forensic_ledger_rel_path": led_rel, "forensic_ledger_file_sha256": _sha256_file(led_gz), "r4_row_ids_and_ledger_sha_match": True, "r4_decimal_bridge_exact_match": True, "planned_cap_is_physical_guarantee": False, "realized_losses_gt_2p50_count": 139, "null_1x_closed": 203, "null_1x_net_usd": -441.6118, "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE", "KH04_ENTRY": "BLOCKED_NO_DEV_CANDIDATE", "OOS_EVALUATIONS": 0},
        "13": {"status": "PASS", "six_cost_dynamic_vs_fixed_fills_separated": True, "arms_e1_e2_e3_anchors_exact_match": True},
        "14": {"BASELINE_PARITY_VERDICT": "PASS", "a1_population_count": 56710, "a1_mismatches_count": 0, "si8_population_count": 6375, "si8_mismatches_count": 0, "three_adapter_population_count": 246, "three_adapter_mismatches_count": 0, "d4_historical_closed_trades": 0, "per_decision_and_episode_fingerprints": {**SEALED_BASELINE_PARITY_FINGERPRINTS, "all_sealed_fingerprints_exact_match": True}},
        "15": {"status": "PASS", "collected_count": n_tc, "executed_count": n_tc, "passed_count": n_tc, "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0, "junit_bijection_exact": True, "collected_nodeids_gz_rel": nodes_rel, "collected_nodeids_gz_sha256": _sha256_file(nodes_gz), "shard_manifest": {"run_id": rid, "tested_executable_sha": tsha, "nonzero_exit_shards_count": 0, "shards": [{"shard_id": "SHARD_01", "exit_code": 0, "timed_out": False, "reused": False, "junit_xml_path": "junit/SHARD_01.xml", "xml_sha256": _sha256_file(xp), "scheduled_node_ids": c_ids}]}},
        "16": {"status": "PASS", "pytest_controlled_subset_raw": pb_raw, "science_slice_8000_raw": sb_raw, "pytest_worker_policy": p_pol, "science_worker_policy": s_pol, "c1_capture_fixture_w1_w2_w4_identical": True, "full_suite_speedup_extrapolated": False},
        "17": {"status": "PASS", "memory_samples_log_rel": "logs/runtime_memory_samples_0p5s.jsonl.gz", "min_disk_free_gib": 30.5, "min_available_ram_mib": 3242.49, "max_descendant_children": 2, "orphan_children_after_cleanup": 0, "sentinel_temp_cleaned_up": True, "owner_disk_preflight_waiver": False},
        "18": {"status": "PASS", "inherited_r5_r4_c2_c1_r3_controls_count": 288, "new_c1_controls_count": 48, "total_controls": 336, "unique_fault_ids": 336, "failed_controls": 0, "paired_fault_receipts_verified": True, "paired_c1_controls_verified_count": 48},
        "19": {"status": "PASS", "clean_clone_verifier_green": True, "inherited_r5_r4_c2_c1_r3_mutations_tested_count": 89, "inherited_r5_r4_c2_c1_r3_mutations_failed_detection_count": 0, "new_c1_mutations_tested_count": 22, "new_c1_mutations_failed_detection_count": 0},
        "22": {"publication_status": "PRE_PUSH_PENDING", "remote_readback_sha256_verified": False, "publication_head_sha": None, "ci_status": "CI_UNKNOWN_NO_ACTIONS_RUN", "actual_github_actions_run_verified": False, "owner_disk_preflight_waiver": False},
    }
    for d in docs.values():
        d["spec_version"] = SPEC_VERSION_KH03_R5_C1
        d["schema_version"] = SCHEMA_VERSION_KH03_R5_C1
        d["run_id"] = rid
        d["tested_executable_sha"] = tsha

    mem_au = _audit_telemetry_gz_vs_17_c1(mem_gz, docs["17"])
    r4_au = _audit_r4_replay_ledger_12_c1(run_dir, docs["12"])
    p_rec = _verify_benchmark_16_c1(pb_raw, p_pol, kind=BENCHMARK_KIND_PYTEST, expected_run_id=rid, expected_tested_sha=tsha, max_safe_cap=3)
    s_rec = _verify_benchmark_16_c1(sb_raw, s_pol, kind=BENCHMARK_KIND_SCIENCE, expected_run_id=rid, expected_tested_sha=tsha, max_safe_cap=4)
    r20 = derive_r5_c1_readiness_verdicts(raw_evidence=docs, mem_audit=mem_au, pytest_recomp=p_rec, science_recomp=s_rec, junit_ok=True, r4_replay_ok=bool(r4_au["valid"]), for_final_verifier_manifest=False)
    docs["20"] = {"spec_version": SPEC_VERSION_KH03_R5_C1, "schema_version": SCHEMA_VERSION_KH03_R5_C1, "run_id": rid, "tested_executable_sha": tsha, **r20}

    fn_map = {
        "00": "00_preflight_branch_sha_resource_lock.json",
        "01": "01_function_ownership_keep_as_is_manifest.json",
        "02": "02_prior_dataset_sealed_integrity.json",
        "03": "03_real_baseline_red_reproduction.json",
        "04": "04_strict_authorization_scope_schema.json",
        "05": "05_plan_binding_tamper_and_expiry_receipts.json",
        "06": "06_dispatch_guard_remaining_timeout_trace.json",
        "07": "07_rate_limiter_retry_cancellation_trace.json",
        "08": "08_atomic_request_byte_budget_and_transport_receipts.json",
        "09": "09_resource_sample_strictness_and_volume_identity.json",
        "10": "10_staging_custody_abort_cleanup_inventory.json",
        "11": "11_offline_only_provenance_partition_no_oos.json",
        "12": "12_r4_replay_decimal_economic_bridge.json",
        "13": "13_six_cost_scenarios_and_arm_regression.json",
        "14": "14_A1_SI8_adapter_D4_full_parity.json",
        "15": "15_full_pytest_fresh_collection_junit_bijection.json",
        "16": "16_W1_W2_W4_W1repeat_benchmark_policy.json",
        "17": "17_actual_resource_telemetry_cleanup.json",
        "18": "18_inherited_new_red_green_faults.json",
        "19": "19_separate_OS_mutation_results.json",
        "20": "20_orthogonal_gates_and_verdicts.json",
        "22": "22_phase1_code_commit_push_receipt.json",
    }
    for k, fn in fn_map.items():
        (run_dir / fn).write_text(json.dumps(docs[k], indent=2, sort_keys=True), encoding="utf-8")
    (run_dir / "README.md").write_text("# Synthetic Valid KH-03.R5.C1 Run\n", encoding="utf-8")
    (run_dir / "HANDOVER_KH03_R5_C1_VI.txt").write_text("Synthetic Valid KH-03.R5.C1 Handover\n", encoding="utf-8")
    return rid, tsha


def run_kh03_r5_c1_verifier_subprocess(run_dir: Path, rid: str, tsha: str) -> Tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)])
    proc = subprocess.run(
        [sys.executable, str(APP_ROOT / "tools" / "kh_03_r5_c1_independent_verifier.py"), "--run-dir", str(run_dir), "--expected-run-id", rid, "--expected-tested-sha", tsha, "--no-write-manifest"],
        cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=60,
    )
    return int(proc.returncode), proc.stdout + "\n" + proc.stderr


def _inject_mandatory_skip_xml(rd: Path) -> None:
    xp = rd / "junit" / "SHARD_01.xml"
    txt = xp.read_text("utf-8").replace(
        '<testcase classname="backend.tests.test_kh03_r5_c1_synth" name="test_c1_0000" time="0.001"/>',
        '<testcase classname="backend.tests.test_kh03_r5_c1_synth" name="test_c1_0000" time="0.001"><skipped message="forced"/></testcase>',
        1,
    )
    xp.write_text(txt, encoding="utf-8")
    _mut_json(rd / "15_full_pytest_fresh_collection_junit_bijection.json", lambda d: (
        d["shard_manifest"]["shards"][0].__setitem__("xml_sha256", _sha256_file(xp)),
        d.__setitem__("passed_count", int(d["passed_count"]) - 1),
        d.__setitem__("skipped_count", 1),
    ))


# 22 NEW Separate-OS Verifier Mutations (C1M01..C1M22 covering D01..D22!)
KH03_R5_C1_OWN_VERIFIER_MUTATIONS_SPECS: List[Tuple[str, Callable[[Path], None], str]] = [
    ("C1M01_D01_STALE_R5_RUN_SHA_REUSED", lambda rd: _mut_json(rd / "00_preflight_branch_sha_resource_lock.json", lambda d: d.update({"run_id": PRIOR_KH03_R5_RUN_ID, "tested_executable_sha": PRIOR_KH03_R5_TESTED_SHA})), "C1V_STALE_PRIOR_RUN_OR_SHA_REUSED"),
    ("C1M02_D02_FORGED_PASS_STATUS_IN_20", lambda rd: (_mut_json(rd / "04_strict_authorization_scope_schema.json", lambda d: d.__setitem__("strict_grant_schema_verified", False))), "C1V_FORGED_GATE_INFLATION:G05_STRICT_GRANT_SCHEMA"),
    ("C1M03_D03_MISSING_RAW_RED_TRACE_03", lambda rd: _mut_json(rd / "03_real_baseline_red_reproduction.json", lambda d: d.__setitem__("observations", {})), "C1V_MISSING_OR_FORGED_REAL_RED_TRACE"),
    ("C1M04_D04_RECOMPUTED_DIGEST_BYPASS_05", lambda rd: _mut_json(rd / "05_plan_binding_tamper_and_expiry_receipts.json", lambda d: d.__setitem__("recomputed_digest_observed_calls", 1)), "C1V_PLAN_RECOMPUTED_DIGEST_BYPASS_NOT_BLOCKED"),
    ("C1M05_D05_DISPATCH_AFTER_EXPIRY_05", lambda rd: _mut_json(rd / "05_plan_binding_tamper_and_expiry_receipts.json", lambda d: d.__setitem__("post_expiry_observed_calls", 1)), "C1V_DISPATCH_AFTER_EXPIRY_DETECTED"),
    ("C1M06_D06_DEADLINE_OVERRUN_06", lambda rd: _mut_json(rd / "06_dispatch_guard_remaining_timeout_trace.json", lambda d: d.__setitem__("post_deadline_observed_calls", 1)), "C1V_DEADLINE_OVERRUN_OR_POST_WAIT_DISPATCH"),
    ("C1M07_D07_INVALID_MONITOR_CLAIMED_SAFE_09", lambda rd: _mut_json(rd / "09_resource_sample_strictness_and_volume_identity.json", lambda d: d.__setitem__("invalid_monitor_observed_calls", 1)), "C1V_INVALID_RESOURCE_MONITOR_CLAIMED_SAFE"),
    ("C1M08_D08_REQUEST_COUNTER_EXCESS_08", lambda rd: _mut_json(rd / "08_atomic_request_byte_budget_and_transport_receipts.json", lambda d: d.__setitem__("observed_dispatched_vs_cap_excess", 1)), "C1V_REQUEST_COUNTER_OR_CAP_MISMATCH"),
    ("C1M09_D09_BYTE_BUDGET_FALSE_PASS_08", lambda rd: _mut_json(rd / "08_atomic_request_byte_budget_and_transport_receipts.json", lambda d: d.__setitem__("unsupported_legacy_transport_read_cap_blocked", False)), "C1V_BYTE_BUDGET_FALSE_PASS"),
    ("C1M10_D10_ABORT_DELETED_FILES_ACCEPTED_10", lambda rd: _mut_json(rd / "10_staging_custody_abort_cleanup_inventory.json", lambda d: d.__setitem__("aborted_accepted_records_count", 1)), "C1V_ABORT_DELETED_FILES_COUNTED_ACCEPTED"),
    ("C1M11_D11_DISK_WAIVER_OR_ORPHAN_17", lambda rd: _mut_json(rd / "17_actual_resource_telemetry_cleanup.json", lambda d: d.__setitem__("orphan_children_after_cleanup", 1)), "C1V_DISK_WAIVER_OR_ORPHAN_BREACH"),
    ("C1M12_D12_FAKE_OBSERVED_RECORDS_11", lambda rd: _mut_json(rd / "11_offline_only_provenance_partition_no_oos.json", lambda d: d.__setitem__("accepted_real_observed_records", 3)), "C1V_NONZERO_EXTERNAL_MARKET_REQUESTS_OR_FAKE_OBSERVED"),
    ("C1M13_D13_FORGED_OOS_OR_ACTIVATION_20", lambda rd: _mut_json(rd / "20_orthogonal_gates_and_verdicts.json", lambda d: d["verdicts"].__setitem__("ACTIVATION_READY", "YES")), "C1V_FORGED_ECONOMIC_OOS_OR_ACTIVATION_IN_20"),
    ("C1M14_D14_JUNIT_MISSING_NODEID_15", lambda rd: _mut_json(rd / "15_full_pytest_fresh_collection_junit_bijection.json", lambda d: d.__setitem__("passed_count", int(d["passed_count"]) - 1)), "C1V_JUNIT_COUNTS_TAMPERED_VS_RAW_XML"),
    ("C1M15_D15_MANDATORY_SKIP_IN_JUNIT_XML", _inject_mandatory_skip_xml, "C1V_JUNIT_FAIL_ERR_OR_MANDATORY_SKIP"),
    ("C1M16_D16_FORGED_BENCHMARK_W1_FALLBACK_16", lambda rd: _mut_json(rd / "16_W1_W2_W4_W1repeat_benchmark_policy.json", lambda d: d["pytest_worker_policy"].__setitem__("measured_fastest_mode", "w=1")), "C1V_BENCH:pytest_controlled_subset"),
    ("C1M17_D17_NONFINITE_NAN_IN_JSON_16", lambda rd: (rd / "16_W1_W2_W4_W1repeat_benchmark_policy.json").write_text((rd / "16_W1_W2_W4_W1repeat_benchmark_policy.json").read_text("utf-8").replace("15.0", "NaN", 1), encoding="utf-8"), "C1V_INVALID_JSON"),
    ("C1M18_D18_PARITY_AND_ECONOMIC_MUTATION", lambda rd: _mut_json(rd / "14_A1_SI8_adapter_D4_full_parity.json", lambda d: d.__setitem__("a1_mismatches_count", 1)), "C1V_PARITY_ANCHOR_MUTATED"),
    ("C1M19_D19_REMOTE_RECEIPT_FALSE_CI_22", lambda rd: _mut_json(rd / "22_phase1_code_commit_push_receipt.json", lambda d: d.update({"remote_readback_sha256_verified": True, "ci_status": "CI_PASS"})), "C1V_FALSE_REMOTE_PUBLICATION_OR_WRONG_SHA"),
    ("C1M20_D20_DUPLICATE_JSON_KEY_TAMPER_04", lambda rd: (rd / "04_strict_authorization_scope_schema.json").write_text('{"status": "PASS", "status": "PASS"}', encoding="utf-8"), "DUPLICATE_JSON_KEY"),
    ("C1M21_D21_MISSING_REQUIRED_ARTIFACT_09", lambda rd: (rd / "09_resource_sample_strictness_and_volume_identity.json").unlink(), "C1V_MISSING_OR_EMPTY_ARTIFACT"),
    ("C1M22_D22_NONZERO_EXTERNAL_MARKET_REQUESTS_11", lambda rd: _mut_json(rd / "11_offline_only_provenance_partition_no_oos.json", lambda d: d.__setitem__("external_market_requests_attempted", 1)), "C1V_NONZERO_EXTERNAL_MARKET_REQUESTS_OR_FAKE_OBSERVED"),
]


def test_c1_clean_synthetic_clone_passes_separate_os_verifier(tmp_path: Path) -> None:
    rdir = tmp_path / "clean_c1"
    rid, tsha = build_synthetic_valid_kh03_r5_c1_run_dir(rdir)
    rc, out = run_kh03_r5_c1_verifier_subprocess(rdir, rid, tsha)
    assert rc == 0, f"Clean C1 clone failed verifier (rc={rc}):\n{out}"
    assert '"status": "PASS"' in out


def test_c1_verifier_rejects_legacy_boolean_only_18_proof(tmp_path: Path) -> None:
    rdir = tmp_path / "legacy_bool_c1"
    rid, tsha = build_synthetic_valid_kh03_r5_c1_run_dir(rdir)
    _mut_json(rdir / "18_inherited_new_red_green_faults.json", lambda d: (d.pop("paired_fault_receipts_verified", None), d.pop("paired_c1_controls_verified_count", None)))
    rc, out = run_kh03_r5_c1_verifier_subprocess(rdir, rid, tsha)
    assert rc != 0 and "LEGACY_CONTROL_PROOF_INSUFFICIENT_USE_C2" in out


@pytest.mark.parametrize("mut_id,mut_fn,expected_token", KH03_R5_C1_OWN_VERIFIER_MUTATIONS_SPECS, ids=[m[0] for m in KH03_R5_C1_OWN_VERIFIER_MUTATIONS_SPECS])
def test_c1_independent_verifier_catches_all_22_mutations(
    tmp_path: Path, mut_id: str, mut_fn: Callable[[Path], None], expected_token: str
) -> None:
    rdir = tmp_path / f"mut_{mut_id}"
    rid, tsha = build_synthetic_valid_kh03_r5_c1_run_dir(rdir)
    mut_fn(rdir)
    rc, out = run_kh03_r5_c1_verifier_subprocess(rdir, rid, tsha)
    assert rc != 0, f"Mutation {mut_id} NOT caught (rc=0)!\n{out}"
    assert expected_token in out, f"Mutation {mut_id} missing expected token {expected_token}:\n{out}"
