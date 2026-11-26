"""
aurum-desk/backend/tests/test_kh03_r6_a1_corrective_verifier_mutations.py

AURUM DESK — KH-03.R6.A1 POST-REVIEW CORRECTIVE CLOSURE | 20 NEW SEPARATE-OS
INDEPENDENT VERIFIER MUTATIONS (CM01..CM20) + CLEAN CLONE POSITIVE CONTROL
Executes `aurum-desk/tools/kh_03_r6_a1_independent_verifier.py --corrective` in
isolated temp copies as a real separate OS process (NEVER mutates sealed runs!).
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
from services.kh_03_r6_a1_acceptance_service import MIN_EXPECTED_PYTEST_COLLECTED_R6_A1
from services.kh_03_r6_a1_corrective_service import (
    PRIOR_A1_REVIEWED_TESTED_SHA,
    SCHEMA_VERSION_KH03_R6_A1_CORRECTIVE,
    SPEC_VERSION_KH03_R6_A1_CORRECTIVE,
    derive_r6_a1_corrective_gates,
    execute_corrective_fault_pair,
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


def _write_jsonl_gz(p: Path, rows: List[Dict[str, Any]]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")


def _edit_json(p: Path, fn: Callable[[Dict[str, Any]], None]) -> None:
    d = json.loads(p.read_text("utf-8"))
    fn(d)
    _write_json(p, d)


def _edit_gz_rows(p: Path, fn: Callable[[List[Dict[str, Any]]], None]) -> None:
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        rows = [json.loads(ln) for ln in fh if ln.strip()]
    fn(rows)
    _write_jsonl_gz(p, rows)


def build_synthetic_valid_kh03_r6_a1_corrective_run_dir(run_dir: Path) -> Tuple[str, str]:
    run_id = "20261126T090000Z_c0ffee123456_syn"
    sha = "e" * 40
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "junit").mkdir(exist_ok=True)

    def _st(obj: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "spec_version": SPEC_VERSION_KH03_R6_A1_CORRECTIVE,
            "schema_version": SCHEMA_VERSION_KH03_R6_A1_CORRECTIVE,
            "run_id": run_id,
            "tested_executable_sha": sha,
            **obj,
        }

    # R4 forensic ledger gz matching exact Decimal anchors
    led_gz = run_dir / "logs" / "r4_replay_forensic_ledger_1x.jsonl.gz"
    rows_r4 = []
    for i in range(7):
        g = Decimal("10.0000"); f = Decimal("1.0000")
        rows_r4.append({"opportunity_id": f"W_{i}", "filled": True, "outcome_label": "WIN", "realized_gross_pnl_usd": str(g), "total_fees_usd": str(f), "realized_net_pnl_usd": str(g - f)})
    for i in range(139):
        g = Decimal("-1.6000"); f = Decimal("1.5000")
        rows_r4.append({"opportunity_id": f"LB_{i}", "filled": True, "outcome_label": "LOSS", "realized_gross_pnl_usd": str(g), "total_fees_usd": str(f), "realized_net_pnl_usd": str(g - f)})
    rem_g = Decimal("-234.9541") - Decimal("-1.6000") * 139
    rem_f = Decimal("269.6577") - Decimal("1.5000") * 139
    for i in range(56):
        g = Decimal("-0.2200"); f = Decimal("1.0700")
        rem_g -= g; rem_f -= f
        rows_r4.append({"opportunity_id": f"LS_{i}", "filled": True, "outcome_label": "LOSS", "realized_gross_pnl_usd": str(g), "total_fees_usd": str(f), "realized_net_pnl_usd": str(g - f)})
    rows_r4.append({"opportunity_id": "LS_FINAL", "filled": True, "outcome_label": "LOSS", "realized_gross_pnl_usd": str(rem_g), "total_fees_usd": str(rem_f), "realized_net_pnl_usd": str(rem_g - rem_f)})
    rem_of = Decimal("14.7151")
    for i in range(27):
        of = Decimal("0.5000"); rem_of -= of
        rows_r4.append({"opportunity_id": f"OPEN_{i}", "filled": True, "outcome_label": "OPEN_AT_HORIZON", "accrued_open_entry_fee_usd": str(of)})
    rows_r4.append({"opportunity_id": "OPEN_27", "filled": True, "outcome_label": "OPEN_AT_HORIZON", "accrued_open_entry_fee_usd": str(rem_of)})
    for i in range(2):
        rows_r4.append({"opportunity_id": f"GAP_{i}", "filled": True, "outcome_label": "DATA_GAP_UNRESOLVED"})
    _write_jsonl_gz(led_gz, rows_r4)

    # Pytest collected nodeids + JUnit XML
    n_tests = MIN_EXPECTED_PYTEST_COLLECTED_R6_A1 + 5
    nodes_gz = run_dir / "logs" / "pytest_collected_executed_nodeids.jsonl.gz"
    with gzip.open(nodes_gz, "wt", encoding="utf-8") as fh:
        for i in range(n_tests):
            fh.write(f"backend/tests/test_syn_corr.py::test_c_{i}\n")
    xml_lines = [f'<testsuite name="syn_corr" tests="{n_tests}">']
    for i in range(n_tests):
        xml_lines.append(f'<testcase classname="backend.tests.test_syn_corr" name="test_c_{i}"/>')
    xml_lines.append("</testsuite>")
    (run_dir / "junit" / "shard_01.xml").write_text("\n".join(xml_lines), encoding="utf-8")

    # Telemetry 0.5s stream
    tel_gz = run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    _write_jsonl_gz(tel_gz, [{"seq": i, "available_mib": 4096.0, "disk_free_gib": 35.0, "child_count": 2} for i in range(4)])

    # 03 RED stream
    h03 = _st({"kind": "M0_RED_HEADER", "baseline_sha": PRIOR_A1_REVIEWED_TESTED_SHA, "reproduced_count": 4, "positive_control_ok": True, "all_reproduced": True})
    r03 = [
        {"case_id": cid, "defect": df, "baseline_sha": PRIOR_A1_REVIEWED_TESTED_SHA, "reproduced": True}
        for cid, df in (("RED-L1", "P1-L"), ("RED-M1", "P1-M"), ("RED-N1a", "P1-N"), ("RED-N1b", "P1-N"), ("POS-133980A", "POSITIVE_CONTROL"))
    ]
    _write_jsonl_gz(run_dir / "03_baseline_red_l_m_n_receipts.jsonl.gz", [h03, *r03])

    # 04 Custody traces stream
    h04 = _st({"kind": "CUSTODY_WRITER_SUMMARY", "concurrent_writers_serialized_unique_seq": True, "cross_process_single_writer_enforced": True, "direct_unauthorized_persist_denied": True, "active_writer_lock_after_close": False, "duplicate_seq_detected": False, "broken_prev_hash_detected": False})
    _write_jsonl_gz(run_dir / "04_custody_writer_ownership_and_transaction_traces.jsonl.gz", [h04, {"control": "L01", "seqs": [1, 2], "readonly_valid": True}])

    # 08 Disk reservation stream
    h08 = _st({"kind": "DISK_RESERVATION_SUMMARY", "normalized_utf8_expansion_prewrite_rejected": True, "peak_staging_manifest_wal_log_overhead_enforced": True, "w4_concurrent_reservations_conserved": True, "interrupted_write_uncertain_bytes_retained": True, "emergency_reserve_and_wal_bounded": True, "double_release_rejected": True, "negative_bytes_detected": False, "omitted_staging_json_wal_bytes": False})
    _write_jsonl_gz(run_dir / "08_disk_reservation_conservation_and_actual_bytes.jsonl.gz", [h08, {"control": "M05", "succeeded": 5, "cap": 100_000}])

    # 10 Claim terminal stream
    h10 = _st({"kind": "CLAIM_TERMINAL_SUMMARY", "preclaim_exception_zero_consumption": True, "postclaim_return_exception_reconciled_once": True, "immediate_postclaim_telemetry_exception_finalized": True, "competing_finalizers_cas_idempotent": True, "persist_failure_classified_unproven_terminal": True, "reused_one_shot_claim_accepted": False})
    _write_jsonl_gz(run_dir / "10_claim_exception_and_durable_terminal_receipts.jsonl.gz", [h10, {"control": "N02", "db_state": "QUARANTINED"}])

    # 20 Paired 41 L/M/N receipts stream
    recs_41 = []
    for grp, cnt in (("L", 12), ("M", 14), ("N", 15)):
        for i in range(1, cnt + 1):
            recs_41.append(execute_corrective_fault_pair(
                control_id=f"{grp}{i:02d}", group=grp, fault_kind="INTEGRATION_FAULT", target_symbol="services.kh_03_r6_a1_corrective_service",
                description=f"syn {grp}{i:02d}",
                negative_fn=lambda i=i: ({"n": i}, {"negative_enforced": True, "violations_or_guards_triggered": 1}),
                positive_fn=lambda i=i: ({"p": i}, {"positive_verified": True, "unexpected_violations": 0}),
                executable_sha=sha, run_id=run_id,
            ))
    h20 = _st({"kind": "PAIRED_SUMMARY", "new_lmn_paired_passed_count": 41, "new_lmn_paired_failed_count": 0, "ast_literal_flags_count": 0})
    _write_jsonl_gz(run_dir / "20_new_and_inherited_paired_test_receipts.jsonl.gz", [h20, *recs_41])

    # 21 Mutations stream (20 new + 201 inherited)
    h21 = _st({"kind": "MUTATION_SUMMARY", "clean_clone_verifier_green": True, "new_corrective_mutations_tested_count": 20, "new_corrective_mutations_detected_count": 20, "inherited_mutations_tested_count": 201, "inherited_mutations_detected_count": 201})
    r21 = [{"mutation_id": f"CM{i:02d}", "detected_fail_closed": True, "verifier_exit_code": 1} for i in range(1, 21)]
    _write_jsonl_gz(run_dir / "21_independent_verifier_mutation_receipts.jsonl.gz", [h21, *r21])

    # Benchmarks 19
    def _mk_r3c1(kind: str, tk: str) -> Dict[str, Any]:
        extra = {"executed_tests_count": 155, "canonical_sorted_outcome_sha256": "a" * 64} if kind == BENCHMARK_KIND_PYTEST else {"rows_count": 8000, "canonical_sorted_sha256": "a" * 64}
        return _st({"canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02, "benchmark_kind": kind, "all_4_runs_identical": True, "status": "PASS", "runs": [{"mode_label": "w=1", "workers": 1, tk: 10.0, **extra}, {"mode_label": "w=2", "workers": 2, tk: 6.0, **extra}, {"mode_label": "w=4", "workers": 4, tk: 5.0, **extra}, {"mode_label": "w=1_repeat", "workers": 1, tk: 10.1, **extra}]})
    pb_raw = _mk_r3c1(BENCHMARK_KIND_PYTEST, "wall_clock_seconds")
    sb_raw = _mk_r3c1(BENCHMARK_KIND_SCIENCE, "wall_seconds")
    lim = {"configured_child_process_cap": 4, "currently_allowed_worker_cap": 3, "full_suite_safe_worker_cap": 3, "shard_exclusive_cap": 3, "preflight_disk_free_gib": 35.0, "disk_free_gib": 35.0, "available_ram_mib": 4096.0}
    p_pol = select_measured_worker_policy_strict(pb_raw, benchmark_kind=BENCHMARK_KIND_PYTEST, system_limits=lim, expected_run_id=run_id, expected_tested_sha=sha, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02)
    s_pol = select_measured_worker_policy_strict(sb_raw, benchmark_kind=BENCHMARK_KIND_SCIENCE, system_limits=lim, expected_run_id=run_id, expected_tested_sha=sha, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02)
    def _mk_4() -> Dict[str, Any]:
        return {"valid": True, "all_4_identical": True, "canonical_semantic_sha256": "f" * 64, "runs": [{"mode_label": lbl, "workers": w, "wall_sec": 0.05, "semantic_result_sha256": "f" * 64} for lbl, w in (("w=1", 1), ("w=2", 2), ("w=4", 4), ("w=1_repeat", 1))]}

    arts: Dict[str, Dict[str, Any]] = {
        "00": _st({"branch": MANDATORY_BRANCH_KH03_R6, "worktree_clean_at_start": True, "disk_waiver_used": False, "preflight_disk_free_gib": 35.0, "preflight_ram_available_mib": 4096.0}),
        "01": _st({"locked_files_modified_count": 0, "unauthorized_changed_files_count": 0, "forbidden_order_imports_count": 0, "secrets_detected_count": 0}),
        "02": _st({"canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02, "all_prior_sealed_artifacts_intact": True, "oos_reads_executed": 0, "oos_evaluations": 0}),
        "03": h03, "04": h04,
        "05": _st({"w1_w2_w4_shuffled_order_identical_semantic_digest": True, "w4_repeated_seeds_byte_conserved": True, "canonical_custody_semantic_sha256": "c" * 64, "bounded_queue_item_and_byte_backpressure_verified": True, "head_slot_anti_starvation_verified": True, "producer_failure_queue_full_no_deadlock_no_late_commit": True, "forged_or_expired_envelope_denied": True}),
        "06": _st({"crash_intent_restart_zero_duplicates": True, "concurrent_seal_before_writer_done_rejected": True}),
        "07": _st({"max_cap_enlargement_removed_verified": True, "narrow_plan_chunk_cap_plus1_rejected": True, "conflicting_cap_cannot_enlarge_via_override": True, "strict_int_bool_neg_overflow_rejected": True}),
        "08": h08,
        "09": _st({"enospc_pressure_fail_closed_no_seal": True, "cleanup_fail_retains_uncertain_no_outside_delete": True, "symlink_junction_volume_substitution_rejected": True, "stale_or_nan_telemetry_rejected": True, "samples_count": 4, "min_observed_disk_free_gib": 35.0, "min_observed_ram_available_mib": 4096.0, "max_observed_descendants": 2, "owned_temp_cleaned_verified": True, "orphan_workers_count": 0, "disk_waiver_exit2_proven": True, "telemetry_samples_gz_rel": "logs/runtime_memory_samples_0p5s.jsonl.gz", "telemetry_samples_gz_sha256": _sha256_f(tel_gz)}),
        "10": h10,
        "11": _st({"finally_telemetry_exception_still_finalizes_ledger": True, "original_and_cleanup_errors_preserved_separately": True, "keyboard_interrupt_cancel_cleans_up_no_hidden_pass": True, "unreaped_worker_quarantines_and_blocks_new_session": True, "expired_deadline_cleanup_bounded_no_late_io": True}),
        "12": _st({"compound_l_m_n_fault_quarantined_conserved": True, "crash_restart_one_shot_spent_remains_spent": True, "durable_finalized_after_aborted_not_acquisition_pass": True, "positive_qa_e2e_full_chain_verified": True}),
        "13": _st({"a1_external_capture_enabled": False, "external_market_requests_attempted": 0, "accepted_real_observed_records": 0, "owner_grant_consumed_count": 0, "production_mode": "OFF", "live_trading_mode": "OFF", "paper_trading_mode": "OFF", "auto_trading_mode": "OFF", "activation_ready": "NO", "economic_verdict": "NO_PROVEN_EDGE", "oos_evaluations": 0}),
        "14": _st({"original_36_a1_prepush_gates_passed": 35, "external_r6_bcd_executed_count": 0, "inherited_a1_55_paired_passed": 55, "inherited_r6_56_paired_passed": 56, "inherited_c1_c2_91_paired_passed": 91, "inherited_regression_288_passed": 288}),
        "15": _st({"junit_bijection_exact": True, "collected_count": n_tests, "executed_count": n_tests, "passed_count": n_tests, "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0, "nonzero_exit_shards_count": 0, "collected_nodeids_gz_rel": "logs/pytest_collected_executed_nodeids.jsonl.gz", "collected_nodeids_gz_sha256": _sha256_f(nodes_gz)}),
        "16": _st({"forensic_ledger_gz_rel": "logs/r4_replay_forensic_ledger_1x.jsonl.gz", "forensic_ledger_gz_sha256": _sha256_f(led_gz), "r4_decimal_bridge_exact_match": True, "n_closed": 203, "n_wins": 7, "n_losses": 196, "n_open_censored": 28, "n_gap_events": 2, "realized_losses_gt_2p50_count": 139}),
        "17": _st({"all_six_scenarios_exact_match": True, "all_three_arms_exact_match": True}),
        "18": _st({"a1_checked": 56710, "a1_mismatches": 0, "si8_checked": 6375, "si8_mismatches": 0, "adapters_checked": 246, "adapters_mismatches": 0, "d4_mismatches": 0}),
        "19": _st({"pytest_controlled_subset_raw": pb_raw, "science_slice_8000_raw": sb_raw, "pytest_worker_policy": p_pol, "science_worker_policy": s_pol, "r6_ingest_benchmark": _mk_4(), "a1_workload_benchmark": _mk_4(), "custody_w_benchmark": _mk_4(), "all_workloads_deterministic": True}),
        "20": h20, "21": h21,
        "24": _st({"phase1_executable_committed": True}),
    }
    arts["22"] = _st(derive_r6_a1_corrective_gates(arts, expected_run_id=run_id, expected_tested_sha=sha, pre_verifier_mode=True))

    json_files = {
        "00": "00_preflight_branch_sha_resource_lock.json",
        "01": "01_function_ownership_and_protected_hashes.json",
        "02": "02_prior_sealed_manifest_integrity_no_oos.json",
        "05": "05_ordered_queue_and_w1_w2_w4_semantic_hashes.json",
        "06": "06_crash_intent_restart_and_seal_truth.json",
        "07": "07_hard_caps_and_effective_budget_contract.json",
        "09": "09_resource_telemetry_peak_allocations_and_cleanup.json",
        "11": "11_finalize_errors_cancel_drain_reap_and_late_write.json",
        "12": "12_compound_fault_and_quarantine_recovery.json",
        "13": "13_external_block_qa_only_no_oos_no_trade.json",
        "14": "14_inherited_g_to_k_and_36_gate_regression.json",
        "15": "15_fresh_pytest_collection_junit_bijection.json",
        "16": "16_r4_decimal_economic_replay_ledger_bridge.json",
        "17": "17_six_cost_scenarios_e1_e2_e3_regression.json",
        "18": "18_a1_si8_adapter_d4_parity.json",
        "19": "19_worker_benchmark_and_safe_selection.json",
        "22": "22_corrective_gate_predicates_and_verdicts.json",
        "24": "24_code_publication_receipt.json",
    }
    for k, fname in json_files.items():
        _write_json(run_dir / fname, arts[k])
    (run_dir / "README.md").write_text("# Synthetic Corrective Run\n", encoding="utf-8")
    (run_dir / "HANDOVER_KH03_R6_A1_CORRECTIVE_VI.txt").write_text("Synthetic Corrective Handover\n", encoding="utf-8")
    return run_id, sha


def run_kh03_r6_a1_corrective_verifier_subprocess(run_dir: Path, run_id: str, tested_sha: str) -> Tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{BACKEND_ROOT}{os.pathsep}{APP_ROOT}{os.pathsep}{REPO_ROOT}"
    env["PYTHONHASHSEED"] = "0"
    p = subprocess.run(
        [sys.executable, str(VERIFIER_SCRIPT_A1), "--corrective", "--run-dir", str(run_dir), "--expected-run-id", run_id, "--expected-tested-sha", tested_sha, "--orchestrator-pid", str(os.getpid()), "--no-write-manifest", "--skip-prior-disk-check"],
        cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=45,
    )
    return int(p.returncode), (p.stdout or "") + "\n" + (p.stderr or "")


def _mut_tel_nan_09(d: Path) -> None:
    p = d / "logs/runtime_memory_samples_0p5s.jsonl.gz"
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        fh.write('{"seq":0,"available_mib":NaN,"disk_free_gib":35.0,"child_count":1}\n')
    _edit_json(d / "09_resource_telemetry_peak_allocations_and_cleanup.json", lambda o: o.__setitem__("telemetry_samples_gz_sha256", _sha256_f(p)))


def _mut_r4_drift_16(d: Path) -> None:
    p = d / "logs/r4_replay_forensic_ledger_1x.jsonl.gz"
    _edit_gz_rows(p, lambda rows: (rows[0].__setitem__("realized_gross_pnl_usd", "25.0000"), rows[0].__setitem__("realized_net_pnl_usd", "24.0000")))
    _edit_json(d / "16_r4_decimal_economic_replay_ledger_bridge.json", lambda o: o.__setitem__("forensic_ledger_gz_sha256", _sha256_f(p)))


def _mut_junit_drop_15(d: Path) -> None:
    xf = d / "junit/shard_01.xml"
    lines = xf.read_text("utf-8").splitlines()
    xf.write_text("\n".join(lines[:1] + lines[2:]), encoding="utf-8")


KH03_R6_A1_CORRECTIVE_OWN_MUTATIONS_SPECS: List[Tuple[str, Callable[[Path], None], str]] = [
    ("CM01_DUPLICATE_SEQ_04", lambda d: _edit_gz_rows(d / "04_custody_writer_ownership_and_transaction_traces.jsonl.gz", lambda rows: rows[1].__setitem__("seqs", [1, 1])), "CORRV_CUSTODY_04_DUPLICATE_SEQ"),
    ("CM02_ALTERED_PREV_HASH_04", lambda d: _edit_gz_rows(d / "04_custody_writer_ownership_and_transaction_traces.jsonl.gz", lambda rows: rows[0].__setitem__("broken_prev_hash_detected", True)), "CORRV_CUSTODY_04_BROKEN_PREV_HASH"),
    ("CM03_MISSING_CHUNK_CRASH_06", lambda d: _edit_json(d / "06_crash_intent_restart_and_seal_truth.json", lambda o: o.__setitem__("crash_intent_restart_zero_duplicates", False)), "CORRV_CRASH_SEAL_06_INVALID"),
    ("CM04_SPOOFED_WRITER_LIVE_LOCK_04", lambda d: _edit_gz_rows(d / "04_custody_writer_ownership_and_transaction_traces.jsonl.gz", lambda rows: rows[0].__setitem__("active_writer_lock_after_close", True)), "CORRV_CUSTODY_04_WRITER_AUTHORITY_OR_LOCK"),
    ("CM05_OMITTED_STAGING_JSON_WAL_08", lambda d: _edit_gz_rows(d / "08_disk_reservation_conservation_and_actual_bytes.jsonl.gz", lambda rows: rows[0].__setitem__("omitted_staging_json_wal_bytes", True)), "CORRV_DISK_08_OVERHEAD_OR_CONSERVATION"),
    ("CM06_INFLATED_PLAN_CAP_MAX_07", lambda d: _edit_json(d / "07_hard_caps_and_effective_budget_contract.json", lambda o: o.__setitem__("max_cap_enlargement_removed_verified", False)), "CORRV_CAPS_07_ENLARGEMENT_OR_INVALID"),
    ("CM07_FORGED_NAN_TELEMETRY_09", _mut_tel_nan_09, "A1V_TELEMETRY_PARSE_ERR"),
    ("CM08_RESERVATION_DOUBLE_RELEASE_08", lambda d: _edit_gz_rows(d / "08_disk_reservation_conservation_and_actual_bytes.jsonl.gz", lambda rows: rows[0].__setitem__("double_release_rejected", False)), "CORRV_DISK_08_DOUBLE_RELEASE_OR_NEG"),
    ("CM09_MISSING_UNCERTAIN_BYTES_08", lambda d: _edit_gz_rows(d / "08_disk_reservation_conservation_and_actual_bytes.jsonl.gz", lambda rows: rows[0].__setitem__("interrupted_write_uncertain_bytes_retained", False)), "CORRV_DISK_08_MISSING_UNCERTAIN"),
    ("CM10_FALSE_TERMINAL_SPENT_PASS_12", lambda d: _edit_json(d / "12_compound_fault_and_quarantine_recovery.json", lambda o: o.__setitem__("durable_finalized_after_aborted_not_acquisition_pass", False)), "CORRV_COMPOUND_12_INVALID"),
    ("CM11_MISSING_ERROR_PRESERVED_11", lambda d: _edit_json(d / "11_finalize_errors_cancel_drain_reap_and_late_write.json", lambda o: o.__setitem__("original_and_cleanup_errors_preserved_separately", False)), "CORRV_FINALIZE_11_INVALID"),
    ("CM12_LATE_COMMIT_AFTER_CANCEL_05", lambda d: _edit_json(d / "05_ordered_queue_and_w1_w2_w4_semantic_hashes.json", lambda o: o.__setitem__("producer_failure_queue_full_no_deadlock_no_late_commit", False)), "CORRV_QUEUE_05_INVALID"),
    ("CM13_REUSED_ONE_SHOT_CLAIM_10", lambda d: _edit_gz_rows(d / "10_claim_exception_and_durable_terminal_receipts.jsonl.gz", lambda rows: rows[0].__setitem__("reused_one_shot_claim_accepted", True)), "CORRV_CLAIM_10_REUSED_OR_INVALID"),
    ("CM14_FABRICATED_RED_WRONG_SHA_03", lambda d: _edit_gz_rows(d / "03_baseline_red_l_m_n_receipts.jsonl.gz", lambda rows: rows[0].__setitem__("baseline_sha", "0" * 40)), "CORRV_RED_03_INVALID"),
    ("CM15_MISSING_TEST_ID_JUNIT_15", _mut_junit_drop_15, "A1V_JUNIT_XML_COUNT_MISMATCH"),
    ("CM16_FAKE_R4_REPLAY_SUMMARY_16", _mut_r4_drift_16, "A1V_R4_DECIMAL_SUM_MISMATCH"),
    ("CM17_STALE_PRIOR_SHA_133980A_00", lambda d: _edit_json(d / "00_preflight_branch_sha_resource_lock.json", lambda o: o.__setitem__("tested_executable_sha", PRIOR_A1_REVIEWED_TESTED_SHA)), "CORRV_STALE_PRIOR_SHA"),
    ("CM18_QA_LABELED_OBSERVED_13", lambda d: _edit_json(d / "13_external_block_qa_only_no_oos_no_trade.json", lambda o: o.__setitem__("accepted_real_observed_records", 1)), "CORRV_EXTERNAL_13_NONZERO"),
    ("CM19_CUSTODY_W_BENCH_DRIFT_19", lambda d: _edit_json(d / "19_worker_benchmark_and_safe_selection.json", lambda o: o["custody_w_benchmark"]["runs"][2].__setitem__("semantic_result_sha256", "0" * 64)), "CORRV_BENCH_19_CUSTODY_DRIFT"),
    ("CM20_CONFLATED_DENOMINATOR_48_22", lambda d: _edit_json(d / "22_corrective_gate_predicates_and_verdicts.json", lambda o: o["corrective_denominators"].__setitem__("corrective_mandatory_total", 48)), "CORRV_DENOMINATOR_22_CONFLATED"),
]


def test_kh03_r6_a1_corrective_20_new_separate_os_verifier_mutations() -> None:
    assert len(KH03_R6_A1_CORRECTIVE_OWN_MUTATIONS_SPECS) >= 18
    with tempfile.TemporaryDirectory(prefix="a1_corr_mut20_") as td:
        root = Path(td)
        base_dir = root / "base_clean"
        rid, sha = build_synthetic_valid_kh03_r6_a1_corrective_run_dir(base_dir)
        rc_clean, out_clean = run_kh03_r6_a1_corrective_verifier_subprocess(base_dir, rid, sha)
        assert rc_clean == 0, f"CLEAN_CORRECTIVE_VERIFIER_FAILED:\n{out_clean}"

        def _run_one(spec: Tuple[str, Callable[[Path], None], str]) -> Tuple[str, bool, int, str]:
            mid, fn, tok = spec
            cdir = root / f"clone_{mid}"
            shutil.copytree(base_dir, cdir)
            try:
                fn(cdir)
                rc, out = run_kh03_r6_a1_corrective_verifier_subprocess(cdir, rid, sha)
                return mid, (rc != 0 and tok in out), rc, out
            finally:
                shutil.rmtree(cdir, ignore_errors=True)

        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(_run_one, KH03_R6_A1_CORRECTIVE_OWN_MUTATIONS_SPECS))

        failed = [(mid, rc, out[:400]) for mid, ok, rc, out in results if not ok]
        assert not failed, f"CORRECTIVE_VERIFIER_MUTATIONS_MISSED:{failed}"
