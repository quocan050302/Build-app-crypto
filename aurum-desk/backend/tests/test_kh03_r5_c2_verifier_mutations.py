"""
aurum-desk/backend/tests/test_kh03_r5_c2_verifier_mutations.py

KH-03.R5.C2 — 24 NEW Separate-OS Independent Verifier Mutation Attacks (`M01`..`M24`).
Exports `build_synthetic_valid_kh03_r5_c2_run_dir`, `run_kh03_r5_c2_verifier_subprocess`,
and `KH03_R5_C2_OWN_VERIFIER_MUTATIONS_SPECS` for both unit pytest and acceptance runner.
Every mutation recomputes public digests where applicable so semantic validation is truly tested!
"""
from __future__ import annotations

import copy
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
from services.kh_03_r5_c2_fault_truth_and_readiness_service import (
    BASELINE_REVIEWED_HEAD_SHA_C2,
    FaultCaseSpec,
    MANDATORY_BRANCH_KH03_R5_C2,
    PRIOR_C1_TESTED_EXECUTABLE_SHA,
    PRIOR_R5_BASELINE_SHA,
    VERIFIED_R5_BASELINE_WITNESS_MAP_C2,
    derive_r5_c2_readiness_verdicts,
    execute_fault_case_pair,
)
from services.kh_03_r5_market_data_foundation_service import (
    SCHEMA_VERSION_KH03_R5_C2,
    SPEC_VERSION_KH03_R5_C2,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tests.test_kh03_r4_economic_accounting import _make_synthetic_anchor_forensic_rows
from tools.kh_03_r3_c1_independent_verifier import SEALED_BASELINE_PARITY_FINGERPRINTS
from tools.kh_03_r5_c2_independent_verifier import (
    _audit_paired_fault_receipts_05_c2,
    _audit_r4_replay_ledger_12_c2,
    _audit_telemetry_gz_vs_17_c2,
    _audit_worker_lifecycle_traces_07_c2,
    _verify_benchmark_16_c2,
)


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _mut_json(p: Path, fn: Callable[[Dict[str, Any]], None]) -> None:
    d = json.loads(p.read_text("utf-8"))
    fn(d)
    p.write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")


def _recompute_branch_trace(br: Dict[str, Any]) -> None:
    body = {
        "fault_id": br["fault_id"],
        "branch_role": br["branch_role"],
        "execution_id": br["execution_id"],
        "invocation_seq": br["invocation_seq"],
        "target_qualified_name": br["target_qualified_name"],
        "executable_sha": br["executable_sha"],
        "run_id": br["run_id"],
        "observed_fields": dict(br["observed_fields"]),
        "predicate_id": br["predicate_id"],
        "exception_type": br["exception_type"],
        "pid": br["pid"],
    }
    br["trace_digest"] = canonical_hash(body)


def _mut_jsonl_gz(gz_p: Path, fn: Callable[[List[Dict[str, Any]]], None], *, sync_sha_file: Path | None = None, sha_key: str = "") -> None:
    with gzip.open(gz_p, "rt", encoding="utf-8") as fh:
        rows = [json.loads(ln) for ln in fh if ln.strip()]
    fn(rows)
    with gzip.open(gz_p, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
    if sync_sha_file is not None and sha_key:
        _mut_json(sync_sha_file, lambda d: d.__setitem__(sha_key, _sha256_file(gz_p)))


def _make_fast_valid_91_paired_records(tsha: str, rid: str) -> List[Dict[str, Any]]:
    fids_c1 = (
        [f"C1-A{i:02d}" for i in range(1, 19)]
        + [f"C1-B{i:02d}" for i in range(1, 16)]
        + [f"C1-C{i:02d}" for i in range(1, 16)]
    )
    fids_c2 = (
        [f"C2-D{i:02d}" for i in range(1, 13)]
        + [f"C2-E{i:02d}" for i in range(1, 17)]
        + [f"C2-F{i:02d}" for i in range(1, 16)]
    )
    recs: List[Dict[str, Any]] = []
    for fid in fids_c1 + fids_c2:
        wk = VERIFIED_R5_BASELINE_WITNESS_MAP_C2.get(fid)
        kind = "BASELINE_BUG_RED" if wk is not None else "NEGATIVE_CONTRACT"
        bw = {"baseline_sha": PRIOR_R5_BASELINE_SHA, "witness_key": wk, "baseline_negative_reproduced": True} if wk else None
        spec = FaultCaseSpec(
            fault_id=fid,
            control_name=f"SYNTH_{fid}",
            target_qualified_name="validate_r5_operator_capture_scope",
            kind=kind,
            negative_perturbation=f"Negative perturbation for {fid}",
            positive_clean_description=f"Clean positive for {fid}",
            negative_predicate_id="PRED_SCOPE_REJECTED",
            positive_predicate_id="PRED_SCOPE_ADMITTED_QA",
            negative_runner=lambda: {"admitted": False, "live_network_permitted": False, "violations_count": 1},
            positive_runner=lambda: {"admitted": True, "live_network_permitted": False, "mode": "QA_FIXTURE_OFFLINE_ONLY", "violations_count": 0, "scope_receipt_id": f"scp_{fid}"},
            baseline_witness_key=wk,
            baseline_sha=PRIOR_R5_BASELINE_SHA if wk else None,
        )
        recs.append(execute_fault_case_pair(spec, executable_sha=tsha, run_id=rid, baseline_witness=bw))
    return recs


def build_synthetic_valid_kh03_r5_c2_run_dir(run_dir: Path) -> Tuple[str, str]:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "junit").mkdir(exist_ok=True)
    (run_dir / "checksums").mkdir(exist_ok=True)

    tsha = "b" * 40
    rid = f"20261125T130000Z_{tsha[:12]}"

    # 05_paired_fault_execution_receipts.jsonl.gz (91 valid pairs: 48 C1 + 43 C2!)
    paired_91 = _make_fast_valid_91_paired_records(tsha, rid)
    p05_gz = run_dir / "05_paired_fault_execution_receipts.jsonl.gz"
    with gzip.open(p05_gz, "wt", encoding="utf-8") as gz:
        for rec in paired_91:
            gz.write(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n")

    # 07_worker_lifecycle_actual_entry_exit_traces.jsonl.gz (clean exit, cancelled-before-start, blocked-quarantined!)
    w07_rows = [
        {
            "scenario_id": "W_CLEAN_EXIT",
            "capture_execution_status": "QA_FIXTURE_OFFLINE_PASS",
            "aborted": False,
            "accepted_records_count": 1,
            "worker_lifecycle_receipt": {
                "session_id": "s_clean", "total_workers_submitted": 1, "async_tasks_pending": 0,
                "workers_queued": 0, "workers_running_actual": 0, "workers_exited_actual": 1,
                "cancelled_before_start": 0, "undrained_inflight_workers": 0, "late_results_discarded": 0,
                "owned_executor_shutdown_complete": True, "session_quarantined": False,
                "terminal_state_conservation_exact": True,
                "attempts": [{"attempt_id": "a_clean", "state": "EXITED_OK", "started_mono": 100.1, "exited_mono": 100.2, "effective_timeout_sec_at_start": 2.0}],
            },
        },
        {
            "scenario_id": "W_CANCEL_BEFORE_START",
            "capture_execution_status": "ABORTED_RESOURCE_OR_CANCEL",
            "aborted": True,
            "accepted_records_count": 0,
            "worker_lifecycle_receipt": {
                "session_id": "s_cbs", "total_workers_submitted": 1, "async_tasks_pending": 0,
                "workers_queued": 0, "workers_running_actual": 0, "workers_exited_actual": 0,
                "cancelled_before_start": 1, "undrained_inflight_workers": 0, "late_results_discarded": 0,
                "owned_executor_shutdown_complete": True, "session_quarantined": False,
                "terminal_state_conservation_exact": True,
                "attempts": [{"attempt_id": "a_cbs", "state": "CANCELLED_BEFORE_START", "started_mono": None, "exited_mono": None, "effective_timeout_sec_at_start": None}],
            },
        },
        {
            "scenario_id": "W_BLOCKED_QUARANTINED",
            "capture_execution_status": "FAIL_UNDRAINED_INFLIGHT_WORKER",
            "aborted": True,
            "accepted_records_count": 0,
            "worker_lifecycle_receipt": {
                "session_id": "s_blk", "total_workers_submitted": 1, "async_tasks_pending": 0,
                "workers_queued": 0, "workers_running_actual": 1, "workers_exited_actual": 0,
                "cancelled_before_start": 0, "undrained_inflight_workers": 1, "late_results_discarded": 0,
                "owned_executor_shutdown_complete": False, "session_quarantined": True,
                "terminal_state_conservation_exact": True,
                "attempts": [{"attempt_id": "a_blk", "state": "STILL_RUNNING_AT_RETURN", "started_mono": 200.1, "exited_mono": None, "effective_timeout_sec_at_start": 2.0}],
            },
        },
    ]
    w07_gz = run_dir / "07_worker_lifecycle_actual_entry_exit_traces.jsonl.gz"
    with gzip.open(w07_gz, "wt", encoding="utf-8") as gz:
        for row in w07_rows:
            gz.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")

    # R4 replay forensic ledger gz
    rows = _make_synthetic_anchor_forensic_rows()
    led_rel = "logs/r4_replay_forensic_ledger_1x.jsonl.gz"
    led_gz = run_dir / led_rel
    with gzip.open(led_gz, "wt", encoding="utf-8") as gz:
        for r in rows:
            gz.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")

    # 0.5s telemetry gz
    mem_gz = run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    with gzip.open(mem_gz, "wt", encoding="utf-8") as mgz:
        for i in range(4):
            mgz.write(json.dumps({"ts_ms": 1795550000000 + i * 500, "available_ram_bytes": 3_500_000_000, "available_mib": 3337.86, "child_count": 2, "worker_pids": [1, 2], "disk_free_gib": 31.5}) + "\n")

    # JUnit XML + nodeids gz
    n_tc = 5980
    c_ids = [f"backend/tests/test_kh03_r5_c2_synth.py::test_c2_{i:04d}" for i in range(n_tc)]
    xp = run_dir / "junit" / "SHARD_01.xml"
    tcs = "".join(f'<testcase classname="backend.tests.test_kh03_r5_c2_synth" name="test_c2_{i:04d}" time="0.001"/>' for i in range(n_tc))
    xp.write_text(f'<?xml version="1.0" encoding="utf-8"?><testsuite tests="{n_tc}" failures="0" errors="0" skipped="0">{tcs}</testsuite>', encoding="utf-8")
    nodes_rel = "logs/pytest_collected_executed_nodeids.jsonl.gz"
    nodes_gz = run_dir / nodes_rel
    with gzip.open(nodes_gz, "wt", encoding="utf-8") as ngz:
        for cid in c_ids:
            ngz.write(cid + "\n")

    pb_raw = {
        "spec_version": SPEC_VERSION_KH03_R5_C2, "schema_version": SCHEMA_VERSION_KH03_R5_C2,
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
        "spec_version": SPEC_VERSION_KH03_R5_C2, "schema_version": SCHEMA_VERSION_KH03_R5_C2,
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
        "00": {"branch": MANDATORY_BRANCH_KH03_R5_C2, "baseline_reviewed_head_sha": BASELINE_REVIEWED_HEAD_SHA_C2, "start_head_sha": tsha, "end_head_sha": tsha, "git_clean_at_start": True, "local_uncommitted_changes": False, "orchestrator_pid": -9999, "owner_disk_preflight_waiver": False, "strict_multi_mount_probe": {"can_run_heavy_stages": True, "owner_waiver_permitted": False, "probe_errors": [], "distinct_mounts": [{"free_gib": 31.5, "projected_post_reserve_gib": 31.0}]}, "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"}, "ACTIVATION_READY": "NO", "status": "PASS"},
        "01": {"status": "PASS", "locked_files_modified_count": 0, "unauthorized_changed_files_count": 0, "forbidden_order_import_hits": [], "cost_assumptions_defaults_intact": True, "legacy_guard_status": "PASS", "unauthorized_r1_callers_count": 0},
        "02": {"status": "PASS", "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02, "canonical_dataset_sha256_match": True, "all_prior_sealed_artifacts_intact": True, "c4_provenance_and_oos_boundary": {"real_oos_escrow_final_record_count": 0}},
        "03": {"status": "PASS", "baseline_sha": PRIOR_C1_TESTED_EXECUTABLE_SHA, "all_c1_def_red_reproduced": True, "p1_d_red_reproduced": True, "p1_e_red_reproduced": True, "p1_f_red_reproduced": True, "working_tree_mutated": False, "external_network_sockets_used": False, "subprocess_exit_code": 0, "stdout_sha256": "c" * 64},
        "04": {"status": "PASS", "c1_source_sha": PRIOR_C1_TESTED_EXECUTABLE_SHA, "total_c1_controls_audited": 48, "unique_c1_fault_ids": 48, "c2_baseline_bug_red_count": 8, "c2_negative_contract_count": 40, "historical_c1_artifacts_mutated": False},
        "06": {"status": "PASS", "legacy_c1_gate_rejects_boolean_only_proof": True, "legacy_c1_verifier_rejects_boolean_only_proof": True, "ast_certification_path_no_literal_flags": True, "historical_c1_artifacts_modified": False},
        "08": {"status": "PASS", "worker_traces_gz_sha256": _sha256_file(w07_gz), "owned_bounded_executor_verified": True, "terminal_state_conservation_verified": True, "global_worker_cap_enforced_across_sessions": True, "queue_wait_deadline_zero_new_start": True, "queue_wait_expiry_zero_new_start": True, "actual_start_remaining_timeout_verified": True, "queued_future_cancelled_before_start_zero_calls": True, "cooperative_cancel_clean_drain_verified": True, "outer_cancellation_propagated_with_receipt": True, "blocked_worker_detected_undrained_gt_zero": True, "blocked_worker_undrained_at_return": 1, "blocked_worker_marked_fail_and_quarantined": True, "blocked_worker_claimed_shutdown_complete": False, "blocked_worker_eventually_exited_after_release": True, "late_success_after_abort_zero_accepted": True, "failed_unlink_quarantined_honestly": True, "unrelated_files_preserved_on_cleanup": True},
        "09": {"status": "PASS", "stable_logical_identity_verified": True, "same_id_changed_payload_conflict_denied": True, "cloned_or_tampered_receipt_denied": True, "sequential_replay_one_winner_only": True, "abort_burns_claimed_grant": True, "second_plan_from_same_scope_denied": True, "reissued_scope_receipt_same_grant_denied": True, "distinct_new_qa_grant_succeeds": True},
        "10": {"status": "PASS", "concurrent_race_winners_count": 1, "concurrent_race_denied_count": 2, "concurrent_race_total_calls": 1, "concurrent_race_cap": 1, "duplicate_attempt_settle_release_blocked": True, "unknown_bytes_conservatively_retained": True, "uncertain_bytes_retained_observed": 12000, "ledger_unhealthy_and_capacity_exhausted_denied": True, "no_negative_ledger_counters": True, "ledger_min_counter_observed": 0},
        "11": {"status": "PASS", "live_hard_blocked_even_with_stub_verifier": True, "live_grant_replay_durability": "NOT_PROVEN_BLOCKED", "live_transport_termination": "NOT_PROVEN_BLOCKED", "external_market_requests_attempted": 0, "accepted_real_observed_records": 0, "oos_reads_executed": 0, "oos_evaluations": 0, "qa_grant_cannot_authorize_live": True, "fake_transport_cannot_mint_observed_verified": True, "qa_cannot_use_live_stdlib_transport": True},
        "12": {"status": "PASS", "fresh_replay_executed_not_copied": True, "new_data_used_in_backtest": False, "forensic_ledger_rel_path": led_rel, "forensic_ledger_file_sha256": _sha256_file(led_gz), "r4_row_ids_and_ledger_sha_match": True, "r4_decimal_bridge_exact_match": True, "planned_cap_is_physical_guarantee": False, "realized_losses_gt_2p50_count": 139, "null_1x_closed": 203, "null_1x_net_usd": -441.6118, "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE", "KH04_ENTRY": "BLOCKED_NO_DEV_CANDIDATE", "OOS_EVALUATIONS": 0},
        "13": {"status": "PASS", "six_cost_dynamic_vs_fixed_fills_separated": True, "arms_e1_e2_e3_anchors_exact_match": True},
        "14": {"BASELINE_PARITY_VERDICT": "PASS", "a1_population_count": 56710, "a1_mismatches_count": 0, "si8_population_count": 6375, "si8_mismatches_count": 0, "three_adapter_population_count": 246, "three_adapter_mismatches_count": 0, "d4_historical_closed_trades": 0, "per_decision_and_episode_fingerprints": {**SEALED_BASELINE_PARITY_FINGERPRINTS, "all_sealed_fingerprints_exact_match": True}},
        "15": {"status": "PASS", "collected_count": n_tc, "executed_count": n_tc, "passed_count": n_tc, "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0, "junit_bijection_exact": True, "collected_nodeids_gz_rel": nodes_rel, "collected_nodeids_gz_sha256": _sha256_file(nodes_gz), "shard_manifest": {"run_id": rid, "tested_executable_sha": tsha, "nonzero_exit_shards_count": 0, "shards": [{"shard_id": "SHARD_01", "exit_code": 0, "timed_out": False, "reused": False, "junit_xml_path": "junit/SHARD_01.xml", "xml_sha256": _sha256_file(xp), "scheduled_node_ids": c_ids}]}},
        "16": {"status": "PASS", "pytest_controlled_subset_raw": pb_raw, "science_slice_8000_raw": sb_raw, "pytest_worker_policy": p_pol, "science_worker_policy": s_pol, "c2_capture_fixture_w1_w2_w4_identical": True, "full_suite_speedup_extrapolated": False},
        "17": {"status": "PASS", "memory_samples_log_rel": "logs/runtime_memory_samples_0p5s.jsonl.gz", "min_disk_free_gib": 31.5, "min_available_ram_mib": 3337.86, "max_descendant_children": 2, "orphan_children_after_cleanup": 0, "sentinel_temp_cleaned_up": True, "active_capture_workers_after_run": 0, "quarantined_capture_workers_after_run": 0, "owner_disk_preflight_waiver": False},
        "18": {"status": "PASS", "paired_receipts_gz_sha256": _sha256_file(p05_gz), "repaired_c1_paired_verified_count": 48, "new_c2_paired_verified_count": 43, "paired_controls_failed_count": 0, "baseline_bug_red_proven_count": 8, "inherited_regression_executed_count": 288, "inherited_regression_failed_count": 0, "inherited_not_relabelled_paired": True},
        "19": {"status": "PASS", "clean_clone_verifier_green": True, "inherited_mutations_tested_count": 111, "inherited_mutations_failed_detection_count": 0, "new_c2_mutations_tested_count": 24, "new_c2_mutations_detected_count": 24, "new_c2_mutations_failed_detection_count": 0},
        "22": {"publication_status": "PRE_PUSH_PENDING", "remote_readback_sha256_verified": False, "publication_head_sha": None, "ci_status": "CI_UNKNOWN_NO_ACTIONS_RUN", "actual_github_actions_run_verified": False, "owner_disk_preflight_waiver": False},
    }
    for d in docs.values():
        d["spec_version"] = SPEC_VERSION_KH03_R5_C2
        d["schema_version"] = SCHEMA_VERSION_KH03_R5_C2
        d["run_id"] = rid
        d["tested_executable_sha"] = tsha

    mem_au = _audit_telemetry_gz_vs_17_c2(mem_gz, docs["17"])
    r4_au = _audit_r4_replay_ledger_12_c2(run_dir, docs["12"])
    pf_au = _audit_paired_fault_receipts_05_c2(run_dir, docs["18"], expected_run_id=rid, expected_tested_sha=tsha)
    wt_au = _audit_worker_lifecycle_traces_07_c2(run_dir, docs["08"])
    p_rec = _verify_benchmark_16_c2(pb_raw, p_pol, kind=BENCHMARK_KIND_PYTEST, expected_run_id=rid, expected_tested_sha=tsha, max_safe_cap=3)
    s_rec = _verify_benchmark_16_c2(sb_raw, s_pol, kind=BENCHMARK_KIND_SCIENCE, expected_run_id=rid, expected_tested_sha=tsha, max_safe_cap=4)
    r20 = derive_r5_c2_readiness_verdicts(raw_evidence=docs, mem_audit=mem_au, pytest_recomp=p_rec, science_recomp=s_rec, junit_ok=True, r4_replay_ok=bool(r4_au["valid"]), paired_faults_recomp=pf_au, worker_traces_recomp=wt_au, for_final_verifier_manifest=False)
    docs["20"] = {"spec_version": SPEC_VERSION_KH03_R5_C2, "schema_version": SCHEMA_VERSION_KH03_R5_C2, "run_id": rid, "tested_executable_sha": tsha, **r20}

    fn_map = {
        "00": "00_preflight_branch_sha_resource_lock.json",
        "01": "01_function_ownership_keep_as_is_manifest.json",
        "02": "02_prior_dataset_sealed_integrity_no_oos.json",
        "03": "03_baseline_c1_red_fault_truth_worker_replay.json",
        "04": "04_c1_all48_control_inventory_and_correction_ledger.json",
        "06": "06_legacy_c1_certification_gate_closure.json",
        "08": "08_cancellation_deadline_drain_quarantine_receipts.json",
        "09": "09_grant_identity_and_one_shot_ledger_receipts.json",
        "10": "10_concurrent_replay_request_byte_conservation.json",
        "11": "11_restart_boundary_live_block_and_provenance.json",
        "12": "12_r4_replay_decimal_economic_bridge.json",
        "13": "13_six_cost_scenarios_and_arm_regression.json",
        "14": "14_A1_SI8_adapter_D4_full_parity.json",
        "15": "15_full_pytest_fresh_collection_junit_bijection.json",
        "16": "16_W1_W2_W4_W1repeat_benchmark_policy.json",
        "17": "17_actual_resource_telemetry_cleanup.json",
        "18": "18_new_and_inherited_fault_control_truth_summary.json",
        "19": "19_separate_OS_mutation_results.json",
        "20": "20_orthogonal_gates_and_verdicts.json",
        "22": "22_phase1_code_commit_push_receipt.json",
    }
    for k, fn in fn_map.items():
        (run_dir / fn).write_text(json.dumps(docs[k], indent=2, sort_keys=True), encoding="utf-8")
    (run_dir / "README.md").write_text("# Synthetic Valid KH-03.R5.C2 Run\n", encoding="utf-8")
    (run_dir / "HANDOVER_KH03_R5_C2_VI.txt").write_text("Synthetic Valid KH-03.R5.C2 Handover\n", encoding="utf-8")
    return rid, tsha


def run_kh03_r5_c2_verifier_subprocess(run_dir: Path, rid: str, tsha: str) -> Tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)])
    proc = subprocess.run(
        [sys.executable, str(APP_ROOT / "tools" / "kh_03_r5_c2_independent_verifier.py"), "--run-dir", str(run_dir), "--expected-run-id", rid, "--expected-tested-sha", tsha, "--no-write-manifest"],
        cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=60,
    )
    return int(proc.returncode), proc.stdout + "\n" + proc.stderr


# --- Targeted Semantic Mutations M01..M24 (updating public hashes so semantic validation is proven!) ---
def _m01_missing_positive(rd: Path) -> None:
    _mut_jsonl_gz(rd / "05_paired_fault_execution_receipts.jsonl.gz", lambda rows: rows[0].__setitem__("positive_receipt", None), sync_sha_file=rd / "18_new_and_inherited_fault_control_truth_summary.json", sha_key="paired_receipts_gz_sha256")


def _m02_missing_negative(rd: Path) -> None:
    _mut_jsonl_gz(rd / "05_paired_fault_execution_receipts.jsonl.gz", lambda rows: rows[0].__setitem__("negative_receipt", None), sync_sha_file=rd / "18_new_and_inherited_fault_control_truth_summary.json", sha_key="paired_receipts_gz_sha256")


def _m03_dup_exec_id(rd: Path) -> None:
    def _fn(rows: List[Dict[str, Any]]) -> None:
        rows[0]["positive_receipt"]["execution_id"] = rows[0]["negative_receipt"]["execution_id"]
        _recompute_branch_trace(rows[0]["positive_receipt"])
    _mut_jsonl_gz(rd / "05_paired_fault_execution_receipts.jsonl.gz", _fn, sync_sha_file=rd / "18_new_and_inherited_fault_control_truth_summary.json", sha_key="paired_receipts_gz_sha256")


def _m04_shared_positive_across_cases(rd: Path) -> None:
    def _fn(rows: List[Dict[str, Any]]) -> None:
        rows[1]["positive_receipt"]["execution_id"] = rows[0]["positive_receipt"]["execution_id"]
        _recompute_branch_trace(rows[1]["positive_receipt"])
    _mut_jsonl_gz(rd / "05_paired_fault_execution_receipts.jsonl.gz", _fn, sync_sha_file=rd / "18_new_and_inherited_fault_control_truth_summary.json", sha_key="paired_receipts_gz_sha256")


def _m05_neg_accepted_declared_pass(rd: Path) -> None:
    def _fn(rows: List[Dict[str, Any]]) -> None:
        rows[0]["negative_receipt"]["observed_fields"]["admitted"] = True
        rows[0]["negative_receipt"]["predicate_passed"] = True
        _recompute_branch_trace(rows[0]["negative_receipt"])
    _mut_jsonl_gz(rd / "05_paired_fault_execution_receipts.jsonl.gz", _fn, sync_sha_file=rd / "18_new_and_inherited_fault_control_truth_summary.json", sha_key="paired_receipts_gz_sha256")


def _m06_pos_fail_declared_pass(rd: Path) -> None:
    def _fn(rows: List[Dict[str, Any]]) -> None:
        rows[0]["positive_receipt"]["observed_fields"]["admitted"] = False
        rows[0]["positive_receipt"]["predicate_passed"] = True
        _recompute_branch_trace(rows[0]["positive_receipt"])
    _mut_jsonl_gz(rd / "05_paired_fault_execution_receipts.jsonl.gz", _fn, sync_sha_file=rd / "18_new_and_inherited_fault_control_truth_summary.json", sha_key="paired_receipts_gz_sha256")


def _m07_false_baseline_red(rd: Path) -> None:
    def _fn(rows: List[Dict[str, Any]]) -> None:
        for r in rows:
            if r["control_kind"] == "BASELINE_BUG_RED":
                r["baseline_witness"] = None
                break
    _mut_jsonl_gz(rd / "05_paired_fault_execution_receipts.jsonl.gz", _fn, sync_sha_file=rd / "18_new_and_inherited_fault_control_truth_summary.json", sha_key="paired_receipts_gz_sha256")


def _m08_missing_actual_trace(rd: Path) -> None:
    (rd / "05_paired_fault_execution_receipts.jsonl.gz").unlink()


def _m09_worker_running_undrained_zero(rd: Path) -> None:
    def _fn(rows: List[Dict[str, Any]]) -> None:
        rows[2]["worker_lifecycle_receipt"]["undrained_inflight_workers"] = 0
        rows[2]["worker_lifecycle_receipt"]["workers_running_actual"] = 0
    _mut_jsonl_gz(rd / "07_worker_lifecycle_actual_entry_exit_traces.jsonl.gz", _fn, sync_sha_file=rd / "08_cancellation_deadline_drain_quarantine_receipts.json", sha_key="worker_traces_gz_sha256")


def _m10_cbs_with_start_time(rd: Path) -> None:
    def _fn(rows: List[Dict[str, Any]]) -> None:
        rows[1]["worker_lifecycle_receipt"]["attempts"][0]["started_mono"] = 123.45
    _mut_jsonl_gz(rd / "07_worker_lifecycle_actual_entry_exit_traces.jsonl.gz", _fn, sync_sha_file=rd / "08_cancellation_deadline_drain_quarantine_receipts.json", sha_key="worker_traces_gz_sha256")


def _m11_exit_before_entry(rd: Path) -> None:
    def _fn(rows: List[Dict[str, Any]]) -> None:
        rows[0]["worker_lifecycle_receipt"]["attempts"][0]["exited_mono"] = 99.0
    _mut_jsonl_gz(rd / "07_worker_lifecycle_actual_entry_exit_traces.jsonl.gz", _fn, sync_sha_file=rd / "08_cancellation_deadline_drain_quarantine_receipts.json", sha_key="worker_traces_gz_sha256")


def _m12_missing_worker_traces(rd: Path) -> None:
    (rd / "07_worker_lifecycle_actual_entry_exit_traces.jsonl.gz").unlink()


def _m13_late_accepted_after_abort(rd: Path) -> None:
    def _fn(rows: List[Dict[str, Any]]) -> None:
        rows[1]["accepted_records_count"] = 1
    _mut_jsonl_gz(rd / "07_worker_lifecycle_actual_entry_exit_traces.jsonl.gz", _fn, sync_sha_file=rd / "08_cancellation_deadline_drain_quarantine_receipts.json", sha_key="worker_traces_gz_sha256")


def _m14_invalid_actual_start_timeout(rd: Path) -> None:
    def _fn(rows: List[Dict[str, Any]]) -> None:
        rows[0]["worker_lifecycle_receipt"]["attempts"][0]["effective_timeout_sec_at_start"] = 0.0
    _mut_jsonl_gz(rd / "07_worker_lifecycle_actual_entry_exit_traces.jsonl.gz", _fn, sync_sha_file=rd / "08_cancellation_deadline_drain_quarantine_receipts.json", sha_key="worker_traces_gz_sha256")


def _m15_quarantine_hidden(rd: Path) -> None:
    def _fn(rows: List[Dict[str, Any]]) -> None:
        rows[2]["worker_lifecycle_receipt"]["owned_executor_shutdown_complete"] = True
        rows[2]["worker_lifecycle_receipt"]["session_quarantined"] = False
    _mut_jsonl_gz(rd / "07_worker_lifecycle_actual_entry_exit_traces.jsonl.gz", _fn, sync_sha_file=rd / "08_cancellation_deadline_drain_quarantine_receipts.json", sha_key="worker_traces_gz_sha256")


def _m23_junit_missing_nodeid(rd: Path) -> None:
    ngz = rd / "logs" / "pytest_collected_executed_nodeids.jsonl.gz"
    with gzip.open(ngz, "rt", encoding="utf-8") as fh:
        lines = [ln.strip() for ln in fh if ln.strip()]
    lines[-1] = lines[0]  # duplicate nodeid!
    with gzip.open(ngz, "wt", encoding="utf-8") as fh:
        for ln in lines:
            fh.write(ln + "\n")
    _mut_json(rd / "15_full_pytest_fresh_collection_junit_bijection.json", lambda d: d.__setitem__("collected_nodeids_gz_sha256", _sha256_file(ngz)))


def _m24_backtest_tampered_recomputed_hash(rd: Path) -> None:
    lgz = rd / "logs" / "r4_replay_forensic_ledger_1x.jsonl.gz"
    def _tamper(rows: List[Dict[str, Any]]) -> None:
        for r in rows:
            if r.get("filled") and r.get("outcome_label") == "LOSS":
                r["realized_gross_pnl_usd"] = 50.0
                r["realized_net_pnl_usd"] = round(50.0 - float(r["total_fees_usd"]), 4)
                break
    _mut_jsonl_gz(lgz, _tamper, sync_sha_file=rd / "12_r4_replay_decimal_economic_bridge.json", sha_key="forensic_ledger_file_sha256")


KH03_R5_C2_OWN_VERIFIER_MUTATIONS_SPECS: List[Tuple[str, Callable[[Path], None], str]] = [
    ("M01_LITERAL_OR_MISSING_POSITIVE_RECEIPT", _m01_missing_positive, "MISSING_POSITIVE_RECEIPT"),
    ("M02_MISSING_NEGATIVE_RECEIPT", _m02_missing_negative, "MISSING_NEGATIVE_RECEIPT"),
    ("M03_DUPLICATE_EXECUTION_ID", _m03_dup_exec_id, "NEGATIVE_AND_POSITIVE_SHARE_EXECUTION_ID"),
    ("M04_SHARED_POSITIVE_ACROSS_CASES", _m04_shared_positive_across_cases, "SHARED_POSITIVE_EXECUTION_ID"),
    ("M05_NEGATIVE_ACCEPTED_DECLARED_PASS", _m05_neg_accepted_declared_pass, "PREDICATE_RECOMPUTE_FAILED:NEGATIVE"),
    ("M06_POSITIVE_FAILED_DECLARED_PASS", _m06_pos_fail_declared_pass, "PREDICATE_RECOMPUTE_FAILED:POSITIVE"),
    ("M07_FALSE_BASELINE_BUG_RED_NO_WITNESS", _m07_false_baseline_red, "BASELINE_BUG_RED_MISSING_BASELINE_WITNESS"),
    ("M08_MISSING_PAIRED_RECEIPTS_TRACE", _m08_missing_actual_trace, "C2V_MISSING_PAIRED_FAULT_RECEIPTS_GZ"),
    ("M09_WORKER_RUNNING_CLAIMED_UNDRAINED_ZERO", _m09_worker_running_undrained_zero, "C2V_WORKER_RUNNING_CLAIMED_ZERO_UNDRAINED"),
    ("M10_CANCELLED_BEFORE_START_WITH_START_TIME", _m10_cbs_with_start_time, "C2V_CANCELLED_BEFORE_START_HAS_START_TIME"),
    ("M11_WORKER_EXIT_BEFORE_ENTRY_TIMESTAMP", _m11_exit_before_entry, "C2V_WORKER_EXIT_BEFORE_ENTRY"),
    ("M12_MISSING_WORKER_LIFECYCLE_TRACE_GZ", _m12_missing_worker_traces, "C2V_MISSING_WORKER_LIFECYCLE_TRACES_GZ"),
    ("M13_LATE_ACCEPTED_ITEM_AFTER_ABORT", _m13_late_accepted_after_abort, "C2V_LATE_ACCEPTED_ITEM_AFTER_ABORT"),
    ("M14_INVALID_ACTUAL_START_TIMEOUT", _m14_invalid_actual_start_timeout, "C2V_INVALID_ACTUAL_START_TIMEOUT"),
    ("M15_QUARANTINE_OR_SHUTDOWN_HIDDEN", _m15_quarantine_hidden, "C2V_ORPHAN_OR_QUARANTINE_HIDDEN"),
    ("M16_TWO_SAME_GRANT_SESSION_WINNERS", lambda rd: _mut_json(rd / "10_concurrent_replay_request_byte_conservation.json", lambda d: d.__setitem__("concurrent_race_winners_count", 2)), "C2V_MULTIPLE_SAME_GRANT_SESSION_WINNERS"),
    ("M17_SAME_GRANT_ID_CHANGED_PAYLOAD_ACCEPTED", lambda rd: _mut_json(rd / "09_grant_identity_and_one_shot_ledger_receipts.json", lambda d: d.__setitem__("same_id_changed_payload_conflict_denied", False)), "C2V_GRANT_PAYLOAD_CONFLICT_ACCEPTED"),
    ("M18_UNKNOWN_BYTES_SETTLED_AS_ZERO", lambda rd: _mut_json(rd / "10_concurrent_replay_request_byte_conservation.json", lambda d: d.__setitem__("uncertain_bytes_retained_observed", 0)), "C2V_UNKNOWN_BYTES_SETTLED_AS_ZERO"),
    ("M19_LEDGER_OVER_RELEASE_NEGATIVE_COUNTER", lambda rd: _mut_json(rd / "10_concurrent_replay_request_byte_conservation.json", lambda d: d.__setitem__("ledger_min_counter_observed", -1)), "C2V_LEDGER_OVER_RELEASE_OR_NEGATIVE_COUNTER"),
    ("M20_REPLAY_CAP_RESET_OR_FREE_RETRY", lambda rd: _mut_json(rd / "10_concurrent_replay_request_byte_conservation.json", lambda d: d.__setitem__("concurrent_race_total_calls", 3)), "C2V_REPLAY_CAP_RESET_OR_FREE_RETRY"),
    ("M21_LIVE_PROMOTED_FROM_OFFLINE_NONDURABLE", lambda rd: _mut_json(rd / "11_restart_boundary_live_block_and_provenance.json", lambda d: d.__setitem__("live_grant_replay_durability", "PASS")), "C2V_LIVE_PROMOTED_FROM_OFFLINE_OR_NONDURABLE"),
    ("M22_FALSE_C1_CERTIFICATION_GATE_OPEN", lambda rd: _mut_json(rd / "06_legacy_c1_certification_gate_closure.json", lambda d: d.__setitem__("legacy_c1_gate_rejects_boolean_only_proof", False)), "C2V_LEGACY_C1_CERTIFICATION_NOT_CLOSED"),
    ("M23_JUNIT_DUPLICATE_NODEID", _m23_junit_missing_nodeid, "C2V_JUNIT_COLLECTED_DUPLICATES"),
    ("M24_BACKTEST_LEDGER_TAMPER_RECOMPUTED_SHA", _m24_backtest_tampered_recomputed_hash, "C2V_R4_DECIMAL_BRIDGE_DRIFT"),
]


def test_c2_clean_synthetic_clone_passes_separate_os_verifier(tmp_path: Path) -> None:
    rdir = tmp_path / "clean_c2"
    rid, tsha = build_synthetic_valid_kh03_r5_c2_run_dir(rdir)
    rc, out = run_kh03_r5_c2_verifier_subprocess(rdir, rid, tsha)
    assert rc == 0, f"Clean C2 clone failed verifier (rc={rc}):\n{out}"
    assert '"status": "PASS"' in out


@pytest.mark.parametrize("mut_id,mut_fn,expected_token", KH03_R5_C2_OWN_VERIFIER_MUTATIONS_SPECS, ids=[m[0] for m in KH03_R5_C2_OWN_VERIFIER_MUTATIONS_SPECS])
def test_c2_independent_verifier_catches_all_24_mutations(
    tmp_path: Path, mut_id: str, mut_fn: Callable[[Path], None], expected_token: str
) -> None:
    rdir = tmp_path / f"mut_{mut_id}"
    rid, tsha = build_synthetic_valid_kh03_r5_c2_run_dir(rdir)
    mut_fn(rdir)
    rc, out = run_kh03_r5_c2_verifier_subprocess(rdir, rid, tsha)
    assert rc != 0, f"Mutation {mut_id} NOT caught (rc=0)!\n{out}"
    assert expected_token in out, f"Mutation {mut_id} missing expected token {expected_token}:\n{out}"
