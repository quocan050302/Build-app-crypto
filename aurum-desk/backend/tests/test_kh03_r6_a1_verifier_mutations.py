"""
aurum-desk/backend/tests/test_kh03_r6_a1_verifier_mutations.py

AURUM DESK — KH-03.R6.A1 | 34 NEW SEPARATE-OS INDEPENDENT VERIFIER MUTATIONS + CLEAN CLONE PASS
Executes `aurum-desk/tools/kh_03_r6_a1_independent_verifier.py` in isolated temp copies as a real
separate OS process (NEVER mutates sealed runs!).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable, Dict, List, Tuple

from services.kh_02_r2_prospective_data_service import CANONICAL_DATASET_SHA256_KH02
from services.kh_03_r3_c1_worker_policy_service import (
    BENCHMARK_KIND_PYTEST,
    BENCHMARK_KIND_SCIENCE,
    select_measured_worker_policy_strict,
)
from services.kh_03_r6_a1_acceptance_service import (
    MIN_EXPECTED_PYTEST_COLLECTED_R6_A1,
    R6A1FaultCaseSpec,
    derive_r6_a1_readiness_verdicts,
    execute_r6_a1_fault_pair,
)
from services.kh_03_r6_a1_release_service import build_r6_a1_unsigned_owner_review_template
from services.kh_03_r6_a1_trust_service import (
    PRIOR_R6_TESTED_EXECUTABLE_SHA,
    SCHEMA_VERSION_KH03_R6_A1,
    SPEC_VERSION_KH03_R6_A1,
)
from services.kh_03_r6_source_contract_service import MANDATORY_BRANCH_KH03_R6

APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
VERIFIER_SCRIPT_A1 = APP_ROOT / "tools" / "kh_03_r6_a1_independent_verifier.py"


def _sha256_f(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _write_json(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _edit_json(p: Path, fn: Callable[[Dict[str, Any]], None]) -> None:
    d = json.loads(p.read_text("utf-8"))
    fn(d)
    _write_json(p, d)


def build_synthetic_valid_kh03_r6_a1_run_dir(run_dir: Path) -> Tuple[str, str]:
    run_id = "20261126T040000Z_deadbeef1234_syn"
    sha = "d" * 40
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "junit").mkdir(exist_ok=True)

    def _st(obj: Dict[str, Any]) -> Dict[str, Any]:
        return {"spec_version": SPEC_VERSION_KH03_R6_A1, "schema_version": SCHEMA_VERSION_KH03_R6_A1, "run_id": run_id, "tested_executable_sha": sha, **obj}

    # 1. Synthetic valid R4 forensic ledger gz matching exact Decimal anchors!
    led_gz = run_dir / "logs" / "r4_replay_forensic_ledger_1x.jsonl.gz"
    rows_r4 = []
    # 7 wins (+10.0000 gross, 1.3628 fee -> +8.6372 net each -> +70.0000 gross)
    # 196 losses: 139 with |net|>2.50, 57 with |net|<=2.50, totaling exact gross=-164.9541, fee=276.6577, net=-441.6118
    # Let's construct exact Decimal rows so sum matches to 0.0001!
    for i in range(7):
        g = Decimal("10.0000"); f = Decimal("1.0000"); n = g - f
        rows_r4.append({"opportunity_id": f"W_{i}", "filled": True, "outcome_label": "WIN", "realized_gross_pnl_usd": str(g), "total_fees_usd": str(f), "realized_net_pnl_usd": str(n)})
    # Remaining target across 196 losses: gross = -234.9541, fee = 269.6577, net = -504.6118
    # 139 big losses: gross = -1.6000, fee = 1.5000 -> net = -3.1000 each (|net|=3.10 > 2.50)
    rem_g = Decimal("-234.9541"); rem_f = Decimal("269.6577")
    for i in range(139):
        g = Decimal("-1.6000"); f = Decimal("1.5000"); n = g - f
        rem_g -= g; rem_f -= f
        rows_r4.append({"opportunity_id": f"L_BIG_{i}", "filled": True, "outcome_label": "LOSS", "realized_gross_pnl_usd": str(g), "total_fees_usd": str(f), "realized_net_pnl_usd": str(n)})
    # 56 small losses: gross = -0.2200, fee = 1.0700 -> net = -1.2900 each (<= 2.50), final loss net = -1.4718 (<= 2.50) -> exact 139!
    rem_g = Decimal("-234.9541") - Decimal("-1.6000") * 139
    rem_f = Decimal("269.6577") - Decimal("1.5000") * 139
    for i in range(56):
        g = Decimal("-0.2200"); f = Decimal("1.0700"); n = g - f
        rem_g -= g; rem_f -= f
        rows_r4.append({"opportunity_id": f"L_SM_{i}", "filled": True, "outcome_label": "LOSS", "realized_gross_pnl_usd": str(g), "total_fees_usd": str(f), "realized_net_pnl_usd": str(n)})
    rows_r4.append({"opportunity_id": "L_SM_FINAL", "filled": True, "outcome_label": "LOSS", "realized_gross_pnl_usd": str(rem_g), "total_fees_usd": str(rem_f), "realized_net_pnl_usd": str(rem_g - rem_f)})
    # 28 OPEN_AT_HORIZON totaling 14.7151 open fee
    rem_of = Decimal("14.7151")
    for i in range(27):
        of = Decimal("0.5000"); rem_of -= of
        rows_r4.append({"opportunity_id": f"OPEN_{i}", "filled": True, "outcome_label": "OPEN_AT_HORIZON", "accrued_open_entry_fee_usd": str(of)})
    rows_r4.append({"opportunity_id": "OPEN_27", "filled": True, "outcome_label": "OPEN_AT_HORIZON", "accrued_open_entry_fee_usd": str(rem_of)})
    for i in range(2):
        rows_r4.append({"opportunity_id": f"GAP_{i}", "filled": True, "outcome_label": "DATA_GAP_UNRESOLVED"})
    with gzip.open(led_gz, "wt", encoding="utf-8") as fh:
        for r in rows_r4:
            fh.write(json.dumps(r) + "\n")

    # 2. Lifecycle stream 10
    lc10 = run_dir / "10_process_lifecycle_cancel_reap_traces.jsonl.gz"
    lc_rows = []
    for i in range(6):
        lc_rows.append({"event": "CHILD_PROCESS_LAUNCHED", "child_pid": 5000 + i})
        lc_rows.append({"event": "CHILD_PROCESS_REAPED", "child_pid": 5000 + i, "reaped_verified": True, "surviving_descendant_pids": []})
    with gzip.open(lc10, "wt", encoding="utf-8") as fh:
        for r in lc_rows:
            fh.write(json.dumps(r) + "\n")

    # 3. 55 valid A1 paired fault receipts in 22
    p22 = run_dir / "22_paired_fault_execution_receipts.jsonl.gz"
    recs_55 = []
    for i in range(55):
        recs_55.append(execute_r6_a1_fault_pair(
            R6A1FaultCaseSpec(f"SYN-{i+1:02d}", "G", "NEGATIVE_CONTRACT", "services.kh_03_r6_a1_trust_service.authorize_r6_a1_dispatch_request", "a1_rejected", "a1_accepted", f"Synthetic pair {i+1}"),
            negative_fn=lambda i=i: ({"n": i}, {"allowed": False, "violations_count": 1}),
            positive_fn=lambda i=i: ({"p": i}, {"allowed": True, "violations_count": 0}),
            executable_sha=sha, run_id=run_id,
        ))
    with gzip.open(p22, "wt", encoding="utf-8") as fh:
        for r in recs_55:
            fh.write(json.dumps(r, sort_keys=True) + "\n")

    # 4. Pytest collected nodeids + JUnit XML matching MIN_EXPECTED_PYTEST_COLLECTED_R6_A1
    n_tests = MIN_EXPECTED_PYTEST_COLLECTED_R6_A1
    nodes_gz = run_dir / "logs" / "pytest_collected_executed_nodeids.jsonl.gz"
    with gzip.open(nodes_gz, "wt", encoding="utf-8") as fh:
        for i in range(n_tests):
            fh.write(f"backend/tests/test_syn.py::test_case_{i}\n")
    xml_lines = [f'<testsuite name="syn" tests="{n_tests}">']
    for i in range(n_tests):
        xml_lines.append(f'<testcase classname="backend.tests.test_syn" name="test_case_{i}"/>')
    xml_lines.append("</testsuite>")
    (run_dir / "junit" / "shard_01.xml").write_text("\n".join(xml_lines), encoding="utf-8")

    # 5. Telemetry 0.5s stream
    tel_gz = run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    with gzip.open(tel_gz, "wt", encoding="utf-8") as fh:
        for i in range(4):
            fh.write(json.dumps({"seq": i, "available_mib": 4096.0, "disk_free_gib": 35.0, "child_count": 2}) + "\n")

    # 6. Unsigned owner template
    tmpl_rel = "logs/unsigned_owner_review_template_stage_b_smoke.json"
    _write_json(run_dir / tmpl_rel, build_r6_a1_unsigned_owner_review_template(
        tested_executable_sha=sha, publication_head_sha=None, stage_a1_run_id=run_id,
        manifest_26_sha256="a" * 64, source_registry_digest="b" * 64,
    ))

    # 7. Benchmarks 20 (strict R3.C1 producer + R6 ingest + A1 workload)
    def _mk_r3c1_raw(kind: str, tk: str) -> Dict[str, Any]:
        extra = (
            {"executed_tests_count": 155, "canonical_sorted_outcome_sha256": "a" * 64}
            if kind == BENCHMARK_KIND_PYTEST
            else {"rows_count": 8000, "canonical_sorted_sha256": "a" * 64}
        )
        return _st({
            "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02,
            "benchmark_kind": kind,
            "all_4_runs_identical": True,
            "runs": [
                {"mode_label": "w=1", "workers": 1, tk: 10.0, **extra},
                {"mode_label": "w=2", "workers": 2, tk: 6.0, **extra},
                {"mode_label": "w=4", "workers": 4, tk: 5.0, **extra},
                {"mode_label": "w=1_repeat", "workers": 1, tk: 10.1, **extra},
            ],
            "status": "PASS",
        })

    pb_raw = _mk_r3c1_raw(BENCHMARK_KIND_PYTEST, "wall_clock_seconds")
    sb_raw = _mk_r3c1_raw(BENCHMARK_KIND_SCIENCE, "wall_seconds")
    lim = {"configured_child_process_cap": 4, "currently_allowed_worker_cap": 3, "full_suite_safe_worker_cap": 3, "shard_exclusive_cap": 3, "preflight_disk_free_gib": 35.0, "disk_free_gib": 35.0, "available_ram_mib": 4096.0}
    p_pol = select_measured_worker_policy_strict(pb_raw, benchmark_kind=BENCHMARK_KIND_PYTEST, system_limits=lim, expected_run_id=run_id, expected_tested_sha=sha, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02)
    s_pol = select_measured_worker_policy_strict(sb_raw, benchmark_kind=BENCHMARK_KIND_SCIENCE, system_limits=lim, expected_run_id=run_id, expected_tested_sha=sha, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02)

    def _mk_4run() -> Dict[str, Any]:
        return {"valid": True, "all_4_identical": True, "canonical_semantic_sha256": "f" * 64, "runs": [
            {"mode_label": lbl, "workers": w, "wall_sec": 0.05, "semantic_result_sha256": "f" * 64}
            for lbl, w in (("w=1", 1), ("w=2", 2), ("w=4", 4), ("w=1_repeat", 1))
        ]}

    arts: Dict[str, Dict[str, Any]] = {
        "00": _st({"branch": MANDATORY_BRANCH_KH03_R6, "origin_url": "https://github.com/quocan050302/Build-app-crypto.git", "worktree_clean_at_start": True, "disk_waiver_used": False, "preflight_disk_free_gib": 35.0, "preflight_ram_available_mib": 4096.0}),
        "01": _st({"locked_files_modified_count": 0, "unauthorized_changed_files_count": 0, "forbidden_order_imports_count": 0, "legacy_r1_unauthorized_callers_count": 0, "production_cost_defaults_intact": True, "c2_live_hard_block_intact": True, "secrets_detected_count": 0}),
        "02": _st({"canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02, "all_prior_sealed_artifacts_intact": True, "prior_sealed_artifacts_checked_count": 84, "prior_r6_manifest_sha256_verified": True, "oos_reads_executed": 0, "oos_evaluations": 0}),
        "03": _st({"baseline_sha": PRIOR_R6_TESTED_EXECUTABLE_SHA, "all_reproduced": True, "reproduced_count": 6}),
        "04": _st({"dryrun_invalid_plan_nonzero_exit": True, "dryrun_valid_plan_zero_claim_zero_socket": True, "handshake_only_success_removed": True, "qa_cli_smoke_e2e_pass": True, "qa_cli_dev_e2e_pass": True, "qa_cli_smoke_pid": 4242, "qa_cli_dev_committed_chunks": 5}),
        "05": _st({"empty_task_plan_blocked_no_claim": True, "partial_second_task_failure_truthful": True, "cancel_between_claim_and_first_attempt_zero_io": True, "repeat_same_grant_denied_after_restart": True}),
        "06": _st({"nested_caps_immutable_and_tamper_rejected": True, "dataclass_replace_endpoints_window_rejected": True, "recomputed_digest_old_mac_rejected": True, "forged_grant_object_rejected": True, "foreign_lease_rejected_zero_dispatch": True, "narrow_plan_cap_enforced_by_ledger": True, "registry_drift_rejected": True}),
        "07": _st({"unauthorized_path_or_query_or_ws_op_rejected": True, "post_enqueue_expiry_zero_io": True, "nonloopback_dispatch_hard_blocked": True, "child_cap_override_rejected": True, "child_deadline_override_and_missing_fields_rejected": True, "direct_child_cli_external_blocked": True}),
        "08": _st({"zero_request_or_missing_dim_reservation_rejected": True, "retry_over_cap_rejected": True, "reservation_receipt_verified": True, "rest_error_and_retry_cumulative_bytes_bounded": True, "retry_after_beyond_budget_aborted": True, "sentinel_never_reads_beyond_reservation": True, "ws_reconnect_sub_conn_bounded_by_reservation": True, "ws_frame_queue_message_caps_enforced": True}),
        "09": _st({"completed_missing_dim_rejected": True, "overrun_quarantined_without_clamping": True, "supervisor_rejects_on_settlement_failure_or_exception": True, "noncooperative_child_and_grandchild_reaped": True, "child_launch_or_pipe_error_cleaned_no_orphan": True, "cancel_after_child_exit_before_accept_discarded": True, "cancel_before_custody_persist_zero_write": True, "lifecycle_gz_sha256": _sha256_f(lc10), "lifecycle_traces_count": len(lc_rows)}),
        "11": _st({"cross_process_parallel_reservations_conserved": True, "anchor_monotonic_ordering_verified": True, "crash_pre_anchor_and_unknown_io_restart_safe": True}),
        "12": _st({"fabricated_mapping_cannot_mint_observed": True, "forged_or_copied_token_rejected": True, "wrong_binding_ids_rejected": True, "raw_bytes_length_or_hash_mismatch_rejected": True, "unsettled_or_overrun_rejected": True, "late_or_nonzero_exit_rejected": True, "replay_same_token_or_chunk_denied": True, "qa_promotion_to_observed_dev_rejected_despite_tls": True}),
        "13": _st({"qa_custody_readonly_post_restart_valid": True, "tampered_raw_or_norm_file_detected": True, "crash_and_failed_unlink_quarantined_safe_cleanup": True, "qa_e2e_committed_chunks_count": 5}),
        "14": _st({"published_evidence_only_descendant_verified": True, "detached_worktree_exact_tested_sha_verified": True, "code_or_config_drift_rejected": True, "dirty_or_untrusted_pythonpath_or_dep_drift_rejected": True, "forged_manifest_pass_rejected": True, "pointer_receipt_ancestry_mismatch_rejected": True, "missing_execute_authorized_or_conflict_rejected": True, "unsigned_template_as_grant_rejected": True, "qa_cli_on_frozen_executable_verified": True}),
        "15": _st({"a1_external_capture_enabled": False, "external_cli_with_plausible_input_hard_blocked": True, "owner_grant_consumed_count": 0, "external_market_requests_attempted": 0, "accepted_real_observed_records": 0, "private_or_trading_calls": 0, "oos_reads_executed": 0, "oos_evaluations": 0, "production_mode": "OFF", "live_trading_mode": "OFF", "paper_trading_mode": "OFF", "auto_trading_mode": "OFF", "activation_ready": "NO", "economic_verdict": "NO_PROVEN_EDGE", "fresh_oos_verdict": "BLOCKED_NO_UNTOUCHED_DATA", "kh04_entry_verdict": "BLOCKED_NO_DEV_CANDIDATE", "unsigned_owner_review_template_rel": tmpl_rel}),
        "16": _st({"forensic_ledger_gz_rel": "logs/r4_replay_forensic_ledger_1x.jsonl.gz", "forensic_ledger_gz_sha256": _sha256_f(led_gz), "r4_decimal_bridge_exact_match": True, "n_closed": 203, "n_wins": 7, "n_losses": 196, "n_open_censored": 28, "n_gap_events": 2, "realized_losses_gt_2p50_count": 139}),
        "17": _st({"all_six_scenarios_exact_match": True, "all_three_arms_exact_match": True}),
        "18": _st({"a1_checked": 56710, "a1_mismatches": 0, "si8_checked": 6375, "si8_mismatches": 0, "adapters_checked": 246, "adapters_mismatches": 0, "d4_mismatches": 0}),
        "19": _st({"junit_bijection_exact": True, "collected_count": n_tests, "executed_count": n_tests, "passed_count": n_tests, "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0, "nonzero_exit_shards_count": 0, "collected_nodeids_gz_rel": "logs/pytest_collected_executed_nodeids.jsonl.gz", "collected_nodeids_gz_sha256": _sha256_f(nodes_gz)}),
        "20": _st({"pytest_controlled_subset_raw": pb_raw, "science_slice_8000_raw": sb_raw, "pytest_worker_policy": p_pol, "science_worker_policy": s_pol, "r6_ingest_benchmark": _mk_4run(), "a1_workload_benchmark": _mk_4run(), "science_w1_w2_w4_all_identical": True, "r6_ingest_w1_w2_w4_all_identical": True, "a1_workload_w1_w2_w4_all_identical": True, "pytest_policy_valid": True, "science_policy_valid": True, "r6_ingest_benchmark_valid": True, "applied_safe_workers_cap": 3, "full_suite_speedup_extrapolated": False}),
        "21": _st({"samples_count": 4, "max_observed_descendants": 2, "owned_temp_cleaned_verified": True, "orphan_workers_count": 0, "disk_waiver_exit2_proven": True, "min_observed_disk_free_gib": 35.0, "min_observed_ram_available_mib": 4096.0, "telemetry_samples_gz_rel": "logs/runtime_memory_samples_0p5s.jsonl.gz", "telemetry_samples_gz_sha256": _sha256_f(tel_gz)}),
        "23": _st({"paired_receipts_gz_sha256": _sha256_f(p22), "new_a1_paired_verified_count": 55, "new_a1_paired_failed_count": 0, "a1_fault_ast_literal_flags_count": 0, "unique_a1_execution_ids_ok": True, "inherited_r6_paired_passed_count": 56, "inherited_r6_paired_failed_count": 0, "inherited_c1_c2_paired_passed_count": 91, "inherited_c1_c2_paired_failed_count": 0, "inherited_regression_passed_count": 288, "inherited_regression_failed_count": 0, "inherited_not_relabelled_as_new_a1": True}),
        "24": _st({"clean_clone_verifier_green": True, "new_a1_mutations_tested_count": 34, "new_a1_mutations_detected_count": 34, "inherited_mutations_tested_count": 167, "inherited_mutations_detected_count": 167}),
        "27": _st({"phase1_executable_committed": True}),
    }
    arts_for_25 = {**arts, "26": {"verifier_status": "PENDING"}}
    arts["25"] = _st(derive_r6_a1_readiness_verdicts(arts_for_25, expected_run_id=run_id, expected_tested_sha=sha, pre_verifier_mode=True))

    name_map = {
        "00": "00_preflight_branch_sha_resource_lock.json",
        "01": "01_function_ownership_keep_as_is_manifest.json",
        "02": "02_prior_r6_and_c2_sealed_integrity_no_oos.json",
        "03": "03_baseline_real_red_p1_g_to_k.json",
        "04": "04_acquisition_cli_dryrun_zero_claim_and_qa_e2e.json",
        "05": "05_session_state_task_coverage_and_finalization.json",
        "06": "06_immutable_plan_grant_registry_lease_binding.json",
        "07": "07_request_contract_and_ipc_envelope_receipts.json",
        "08": "08_reservation_actual_io_budget_conservation.json",
        "09": "09_settlement_failure_overrun_quarantine_receipts.json",
        "11": "11_ledger_cross_process_anchor_crash_restart.json",
        "12": "12_trusted_supervisor_settlement_provenance_chain.json",
        "13": "13_custody_actual_qa_raw_normalized_hashes_and_manifest.json",
        "14": "14_frozen_executable_release_ancestry_and_cli_receipts.json",
        "15": "15_external_block_no_observed_no_oos_no_trade.json",
        "16": "16_r4_replay_decimal_economic_bridge.json",
        "17": "17_six_cost_scenarios_and_arm_regression.json",
        "18": "18_A1_SI8_adapter_D4_full_parity.json",
        "19": "19_full_pytest_collection_junit_bijection.json",
        "20": "20_W1_W2_W4_W1repeat_benchmark_policy.json",
        "21": "21_actual_resource_telemetry_cleanup.json",
        "23": "23_inherited_regression_and_new_fault_truth.json",
        "24": "24_independent_verifier_mutation_results.json",
        "25": "25_orthogonal_gates_pre_verifier.json",
        "27": "27_phase1_code_commit_push_receipt.json",
    }
    for k, fname in name_map.items():
        _write_json(run_dir / fname, arts[k])
    (run_dir / "README.md").write_text("# Synthetic A1 Run\n", encoding="utf-8")
    (run_dir / "HANDOVER_KH03_R6_A1_VI.txt").write_text("Synthetic Handover\n", encoding="utf-8")
    return run_id, sha


def run_kh03_r6_a1_verifier_subprocess(run_dir: Path, run_id: str, tested_sha: str) -> Tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{BACKEND_ROOT}{os.pathsep}{APP_ROOT}{os.pathsep}{REPO_ROOT}"
    env["PYTHONHASHSEED"] = "0"
    p = subprocess.run(
        [sys.executable, str(VERIFIER_SCRIPT_A1), "--run-dir", str(run_dir), "--expected-run-id", run_id, "--expected-tested-sha", tested_sha, "--orchestrator-pid", str(os.getpid()), "--no-write-manifest", "--skip-prior-disk-check"],
        cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=45,
    )
    return int(p.returncode), (p.stdout or "") + "\n" + (p.stderr or "")


def _mut_dup_exec_id_22(d: Path) -> None:
    p = d / "22_paired_fault_execution_receipts.jsonl.gz"
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        rows = [json.loads(ln) for ln in fh if ln.strip()]
    rows[1]["negative"]["execution_id"] = rows[0]["negative"]["execution_id"]
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    _edit_json(d / "23_inherited_regression_and_new_fault_truth.json", lambda o: o.__setitem__("paired_receipts_gz_sha256", _sha256_f(p)))


def _mut_r4_net_drift(d: Path) -> None:
    p = d / "logs/r4_replay_forensic_ledger_1x.jsonl.gz"
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        rows = [json.loads(ln) for ln in fh if ln.strip()]
    rows[0]["realized_gross_pnl_usd"] = "20.0000"
    rows[0]["realized_net_pnl_usd"] = "19.0000"
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    _edit_json(d / "16_r4_replay_decimal_economic_bridge.json", lambda o: o.__setitem__("forensic_ledger_gz_sha256", _sha256_f(p)))


def _mut_lifecycle_survivor(d: Path) -> None:
    p = d / "10_process_lifecycle_cancel_reap_traces.jsonl.gz"
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        rows = [json.loads(ln) for ln in fh if ln.strip()]
    for r in rows:
        if r.get("event") == "CHILD_PROCESS_REAPED":
            r["surviving_descendant_pids"] = [99999]
            break
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    _edit_json(d / "09_settlement_failure_overrun_quarantine_receipts.json", lambda o: o.__setitem__("lifecycle_gz_sha256", _sha256_f(p)))


def _mut_tel_desc_5(d: Path) -> None:
    p = d / "logs/runtime_memory_samples_0p5s.jsonl.gz"
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        rows = [json.loads(ln) for ln in fh if ln.strip()]
    rows[0]["child_count"] = 5
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    _edit_json(d / "21_actual_resource_telemetry_cleanup.json", lambda o: o.__setitem__("telemetry_samples_gz_sha256", _sha256_f(p)))


def _mut_tel_nan(d: Path) -> None:
    p = d / "logs/runtime_memory_samples_0p5s.jsonl.gz"
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        fh.write('{"seq":0,"available_mib":NaN,"disk_free_gib":35.0,"child_count":1}\n')
    _edit_json(d / "21_actual_resource_telemetry_cleanup.json", lambda o: o.__setitem__("telemetry_samples_gz_sha256", _sha256_f(p)))


def _mut_junit_drop1(d: Path) -> None:
    xf = d / "junit/shard_01.xml"
    lines = xf.read_text("utf-8").splitlines()
    xf.write_text("\n".join(lines[:1] + lines[2:]), encoding="utf-8")


def _mut_dup_key_04(d: Path) -> None:
    p = d / "04_acquisition_cli_dryrun_zero_claim_and_qa_e2e.json"
    p.write_text('{"run_id":"x","run_id":"y"}', encoding="utf-8")


KH03_R6_A1_OWN_VERIFIER_MUTATIONS_SPECS: List[Tuple[str, Callable[[Path], None], str]] = [
    ("A1M01_MISSING_ARTIFACT_03", lambda d: (d / "03_baseline_real_red_p1_g_to_k.json").unlink(), "A1V_MISSING_OR_EMPTY_ARTIFACT"),
    ("A1M02_RUN_ID_MISMATCH_04", lambda d: _edit_json(d / "04_acquisition_cli_dryrun_zero_claim_and_qa_e2e.json", lambda o: o.__setitem__("run_id", "WRONG_RID")), "A1V_RUN_ID_MISMATCH"),
    ("A1M03_STALE_PRIOR_SHA_00", lambda d: _edit_json(d / "00_preflight_branch_sha_resource_lock.json", lambda o: o.__setitem__("tested_executable_sha", PRIOR_R6_TESTED_EXECUTABLE_SHA)), "A1V_STALE_PRIOR_SHA"),
    ("A1M04_DUPLICATE_JSON_KEY_04", _mut_dup_key_04, "A1V_JSON_STRICT_PARSE_ERR"),
    ("A1M05_TELEMETRY_NAN_21", _mut_tel_nan, "A1V_TELEMETRY_PARSE_ERR"),
    ("A1M06_LOCKED_FILE_MODIFIED_01", lambda d: _edit_json(d / "01_function_ownership_keep_as_is_manifest.json", lambda o: o.__setitem__("locked_files_modified_count", 1)), "A1V_KEEP_AS_IS_01_INVALID"),
    ("A1M07_CANONICAL_DATASET_DRIFT_02", lambda d: _edit_json(d / "02_prior_r6_and_c2_sealed_integrity_no_oos.json", lambda o: o.__setitem__("canonical_dataset_sha256", "0" * 64)), "A1V_CANONICAL_DATASET_SHA_MISMATCH"),
    ("A1M08_M0_RED_INCOMPLETE_03", lambda d: _edit_json(d / "03_baseline_real_red_p1_g_to_k.json", lambda o: o.__setitem__("reproduced_count", 5)), "A1V_M0_REAL_RED_03_INVALID"),
    ("A1M09_M0_WRONG_BASELINE_SHA_03", lambda d: _edit_json(d / "03_baseline_real_red_p1_g_to_k.json", lambda o: o.__setitem__("baseline_sha", "0" * 40)), "A1V_M0_REAL_RED_03_INVALID"),
    ("A1M10_EMPTY_QA_E2E_CHUNKS_04", lambda d: _edit_json(d / "04_acquisition_cli_dryrun_zero_claim_and_qa_e2e.json", lambda o: o.__setitem__("qa_cli_dev_committed_chunks", 0)), "A1V_QA_CLI_04_INVALID"),
    ("A1M11_HANDSHAKE_NOT_REMOVED_04", lambda d: _edit_json(d / "04_acquisition_cli_dryrun_zero_claim_and_qa_e2e.json", lambda o: o.__setitem__("handshake_only_success_removed", False)), "A1V_QA_CLI_04_INVALID"),
    ("A1M12_PLAN_MUTATION_UNBLOCKED_06", lambda d: _edit_json(d / "06_immutable_plan_grant_registry_lease_binding.json", lambda o: o.__setitem__("nested_caps_immutable_and_tamper_rejected", False)), "A1V_PLAN_BINDING_06_INVALID"),
    ("A1M13_CHILD_OVERRIDE_UNBLOCKED_07", lambda d: _edit_json(d / "07_request_contract_and_ipc_envelope_receipts.json", lambda o: o.__setitem__("child_cap_override_rejected", False)), "A1V_ENVELOPE_07_INVALID"),
    ("A1M14_RESERVATION_ZERO_CAP_08", lambda d: _edit_json(d / "08_reservation_actual_io_budget_conservation.json", lambda o: o.__setitem__("zero_request_or_missing_dim_reservation_rejected", False)), "A1V_BUDGET_08_INVALID"),
    ("A1M15_OVERRUN_CLAMPED_09", lambda d: _edit_json(d / "09_settlement_failure_overrun_quarantine_receipts.json", lambda o: o.__setitem__("overrun_quarantined_without_clamping", False)), "A1V_SETTLEMENT_09_INVALID"),
    ("A1M16_LIFECYCLE_SURVIVOR_10", _mut_lifecycle_survivor, "A1V_LIFECYCLE_10_SURVIVING_DESCENDANTS"),
    ("A1M17_LIFECYCLE_GZ_SHA_DRIFT_09", lambda d: _edit_json(d / "09_settlement_failure_overrun_quarantine_receipts.json", lambda o: o.__setitem__("lifecycle_gz_sha256", "0" * 64)), "A1V_LIFECYCLE_10_GZ_SHA_MISMATCH"),
    ("A1M18_LEDGER_ANCHOR_ROLLBACK_11", lambda d: _edit_json(d / "11_ledger_cross_process_anchor_crash_restart.json", lambda o: o.__setitem__("anchor_monotonic_ordering_verified", False)), "A1V_LEDGER_11_INVALID"),
    ("A1M19_FORGED_MAPPING_PROV_12", lambda d: _edit_json(d / "12_trusted_supervisor_settlement_provenance_chain.json", lambda o: o.__setitem__("fabricated_mapping_cannot_mint_observed", False)), "A1V_PROVENANCE_12_INVALID"),
    ("A1M20_QA_PROMOTION_DEV_12", lambda d: _edit_json(d / "12_trusted_supervisor_settlement_provenance_chain.json", lambda o: o.__setitem__("qa_promotion_to_observed_dev_rejected_despite_tls", False)), "A1V_PROVENANCE_12_INVALID"),
    ("A1M21_CUSTODY_READONLY_FAIL_13", lambda d: _edit_json(d / "13_custody_actual_qa_raw_normalized_hashes_and_manifest.json", lambda o: o.__setitem__("qa_custody_readonly_post_restart_valid", False)), "A1V_CUSTODY_13_INVALID"),
    ("A1M22_RELEASE_CODE_DRIFT_14", lambda d: _edit_json(d / "14_frozen_executable_release_ancestry_and_cli_receipts.json", lambda o: o.__setitem__("code_or_config_drift_rejected", False)), "A1V_RELEASE_14_INVALID"),
    ("A1M23_EXTERNAL_REQ_NONZERO_15", lambda d: _edit_json(d / "15_external_block_no_observed_no_oos_no_trade.json", lambda o: o.__setitem__("external_market_requests_attempted", 1)), "A1V_EXTERNAL_OR_OBSERVED_NONZERO_15"),
    ("A1M24_LIVE_TRADING_ON_15", lambda d: _edit_json(d / "15_external_block_no_observed_no_oos_no_trade.json", lambda o: o.__setitem__("live_trading_mode", "ON")), "A1V_ECONOMIC_OOS_TRADING_15_VIOLATION"),
    ("A1M25_OWNER_TEMPLATE_SIGNED_15", lambda d: _edit_json(d / "logs/unsigned_owner_review_template_stage_b_smoke.json", lambda o: o.__setitem__("valid_authorization", True)), "A1V_OWNER_TEMPLATE_NOT_UNSIGNED"),
    ("A1M26_R4_FORENSIC_NET_DRIFT_16", _mut_r4_net_drift, "A1V_R4_DECIMAL_SUM_MISMATCH"),
    ("A1M27_SIX_COST_MISMATCH_17", lambda d: _edit_json(d / "17_six_cost_scenarios_and_arm_regression.json", lambda o: o.__setitem__("all_six_scenarios_exact_match", False)), "A1V_SIX_COST_ARMS_17_INVALID"),
    ("A1M28_PARITY_MISMATCH_18", lambda d: _edit_json(d / "18_A1_SI8_adapter_D4_full_parity.json", lambda o: o.__setitem__("a1_mismatches", 1)), "A1V_BASELINE_PARITY_18_INVALID"),
    ("A1M29_JUNIT_MISSING_TEST_19", _mut_junit_drop1, "A1V_JUNIT_XML_COUNT_MISMATCH"),
    ("A1M30_A1_WORKLOAD_HASH_DRIFT_20", lambda d: _edit_json(d / "20_W1_W2_W4_W1repeat_benchmark_policy.json", lambda o: o["a1_workload_benchmark"]["runs"][2].__setitem__("semantic_result_sha256", "0" * 64)), "A1V_A1_WORKLOAD_SEMANTIC_MISMATCH"),
    ("A1M31_TELEMETRY_DESC_GT_4_21", _mut_tel_desc_5, "A1V_TELEMETRY_DESCENDANTS_GT_4"),
    ("A1M32_PAIRED_DUP_EXEC_ID_22", _mut_dup_exec_id_22, "A1V_PAIRED_FAULT_INVALID"),
    ("A1M33_PAIRED_LITERAL_FLAG_23", lambda d: _edit_json(d / "23_inherited_regression_and_new_fault_truth.json", lambda o: o.__setitem__("a1_fault_ast_literal_flags_count", 1)), "A1V_PAIRED_FAULT_AST_LITERAL_FLAGS"),
    ("A1M34_EXTERNAL_DENOM_NONZERO_25", lambda d: _edit_json(d / "25_orthogonal_gates_pre_verifier.json", lambda o: o["a1_denominators"].__setitem__("external_r6_bcd_executed", 1)), "A1V_EXTERNAL_DENOMINATOR_NONZERO_25"),
]


def test_kh03_r6_a1_34_new_separate_os_verifier_mutations() -> None:
    assert len(KH03_R6_A1_OWN_VERIFIER_MUTATIONS_SPECS) >= 32
    with tempfile.TemporaryDirectory(prefix="a1_mut34_") as td:
        root = Path(td)
        base_dir = root / "base_clean"
        rid, sha = build_synthetic_valid_kh03_r6_a1_run_dir(base_dir)
        rc_clean, out_clean = run_kh03_r6_a1_verifier_subprocess(base_dir, rid, sha)
        assert rc_clean == 0, f"CLEAN_A1_VERIFIER_FAILED:\n{out_clean}"

        def _run_one(spec: Tuple[str, Callable[[Path], None], str]) -> Tuple[str, bool, int, str]:
            mid, fn, tok = spec
            cdir = root / f"clone_{mid}"
            shutil.copytree(base_dir, cdir)
            try:
                fn(cdir)
                rc, out = run_kh03_r6_a1_verifier_subprocess(cdir, rid, sha)
                return mid, (rc != 0 and tok in out), rc, out
            finally:
                shutil.rmtree(cdir, ignore_errors=True)

        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(_run_one, KH03_R6_A1_OWN_VERIFIER_MUTATIONS_SPECS))

        failed = [(mid, rc, out[:400]) for mid, ok, rc, out in results if not ok]
        assert not failed, f"A1_VERIFIER_MUTATIONS_MISSED:{failed}"
