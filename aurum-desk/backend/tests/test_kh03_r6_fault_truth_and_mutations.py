"""
aurum-desk/backend/tests/test_kh03_r6_fault_truth_and_mutations.py

KH-03.R6 Separate-OS Independent Verifier Mutation Resistance Suite (32 NEW semantic mutations
R6-M01..R6-M32 on isolated synthetic run clones; clean clone PASSES, every mutation FAILS its
exact target violation token!).
Exports:
  - build_synthetic_valid_kh03_r6_run_dir
  - run_kh03_r6_verifier_subprocess
  - KH03_R6_OWN_VERIFIER_MUTATIONS_SPECS (32 specs >= 30 required!)
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
from services.kh_03_r6_acceptance_service import (
    R6FaultCaseSpec,
    build_r6_unsigned_owner_review_template,
    execute_r6_fault_pair,
    run_r6_fixture_ingest_w1_w2_w4_benchmark,
)
from services.kh_03_r6_custody_readiness_service import (
    MIN_EXPECTED_PYTEST_COLLECTED_R6,
    MIN_NEW_PAIRED_CONTROLS_R6,
    derive_r6_readiness_verdicts,
)
from services.kh_03_r6_source_contract_service import (
    API_FAMILY_CLASSIC_V2,
    MANDATORY_BRANCH_KH03_R6,
    PRIOR_C2_TESTED_EXECUTABLE_SHA,
    SCHEMA_VERSION_KH03_R6,
    SPEC_VERSION_KH03_R6,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = REPO_ROOT / "aurum-desk"
VERIFIER_PATH = APP_ROOT / "tools" / "kh_03_r6_independent_verifier.py"


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _write_json(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _stamp(obj: Dict[str, Any], rid: str, sha: str) -> Dict[str, Any]:
    out = dict(obj)
    out["spec_version"] = SPEC_VERSION_KH03_R6
    out["schema_version"] = SCHEMA_VERSION_KH03_R6
    out["run_id"] = rid
    out["tested_executable_sha"] = sha
    return out


def build_synthetic_valid_kh03_r6_run_dir(base_dir: Path) -> Tuple[str, str]:
    """Builds a minimal, 100% semantically valid synthetic KH-03.R6 Stage A run directory on disk."""
    rid = "20261125T190000Z_999999999999"
    sha = "9" * 40
    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / "logs").mkdir(exist_ok=True)
    (base_dir / "junit").mkdir(exist_ok=True)

    # 1. Raw R4 forensic ledger gz matching EXACT real schema (opportunity_id, filled, outcome_label, realized_gross_pnl_usd, total_fees_usd, realized_net_pnl_usd, accrued_open_entry_fee_usd)
    ledger_gz = base_dir / "logs" / "r4_replay_forensic_ledger_1x.jsonl.gz"
    rows: List[Dict[str, Any]] = []
    # 7 wins: +10.0000 gross, 1.0000 fee -> +9.0000 net each (sum gross=+70, fee=7, net=+63)
    for i in range(7):
        rows.append({"opportunity_id": f"W{i}", "filled": True, "outcome_label": "WIN", "realized_gross_pnl_usd": "10.0000", "total_fees_usd": "1.0000", "realized_net_pnl_usd": "9.0000"})
    # 139 losses > 2.50: gross=-1.5000, fee=1.5000 -> net=-3.0000 each (sum gross=-208.5, fee=208.5, net=-417.0)
    for i in range(139):
        rows.append({"opportunity_id": f"L_GT_{i}", "filled": True, "outcome_label": "LOSS", "realized_gross_pnl_usd": "-1.5000", "total_fees_usd": "1.5000", "realized_net_pnl_usd": "-3.0000"})
    # 56 small losses: gross=-0.4600, fee=1.0800 -> net=-1.5400 (sum gross=-25.76, fee=60.48, net=-86.24)
    for i in range(56):
        rows.append({"opportunity_id": f"L_SM_{i}", "filled": True, "outcome_label": "LOSS", "realized_gross_pnl_usd": "-0.4600", "total_fees_usd": "1.0800", "realized_net_pnl_usd": "-1.5400"})
    # 1 final small loss: gross=-0.6941, fee=0.6777 -> net=-1.3718 (exact totals: gross=-164.9541, fee=276.6577, net=-441.6118)
    rows.append({"opportunity_id": "L_SM_56", "filled": True, "outcome_label": "LOSS", "realized_gross_pnl_usd": "-0.6941", "total_fees_usd": "0.6777", "realized_net_pnl_usd": "-1.3718"})
    # 28 OPEN_AT_HORIZON: total accrued_open_entry_fee_usd = 14.7151 (27 * 0.5255 + 0.5266 = 14.7151)
    for i in range(27):
        rows.append({"opportunity_id": f"OPEN_{i}", "filled": True, "outcome_label": "OPEN_AT_HORIZON", "accrued_open_entry_fee_usd": "0.5255"})
    rows.append({"opportunity_id": "OPEN_27", "filled": True, "outcome_label": "OPEN_AT_HORIZON", "accrued_open_entry_fee_usd": "0.5266"})
    # 2 DATA_GAP_UNRESOLVED
    for i in range(2):
        rows.append({"opportunity_id": f"GAP_{i}", "filled": True, "outcome_label": "DATA_GAP_UNRESOLVED"})
    with gzip.open(ledger_gz, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")

    # 2. Raw 0.5s telemetry gz (exact _BoundedResourceSampler keys!)
    tel_gz = base_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    with gzip.open(tel_gz, "wt", encoding="utf-8") as fh:
        for i in range(4):
            fh.write(json.dumps({"seq": i, "available_mib": 4000.0, "disk_free_gib": 42.0, "child_count": 2}) + "\n")

    # 3. Raw pytest nodeids gz & matching 1:1 JUnit XML (>= MIN_EXPECTED_PYTEST_COLLECTED_R6)
    n_tests = MIN_EXPECTED_PYTEST_COLLECTED_R6 + 5
    nodes_gz = base_dir / "logs" / "pytest_collected_executed_nodeids.jsonl.gz"
    with gzip.open(nodes_gz, "wt", encoding="utf-8") as fh:
        for i in range(n_tests):
            fh.write(f"backend/tests/test_synth.py::test_case_{i}\n")
    xml_lines = [f'<testsuite name="pytest" tests="{n_tests}" errors="0" failures="0" skipped="0">']
    for i in range(n_tests):
        xml_lines.append(f'<testcase classname="backend.tests.test_synth" name="test_case_{i}" time="0.001"/>')
    xml_lines.append("</testsuite>")
    (base_dir / "junit" / "shard_00.xml").write_text("\n".join(xml_lines), encoding="utf-8")

    # 4. Raw 07_transport_actual_process_lifecycle.jsonl.gz
    lc_gz = base_dir / "07_transport_actual_process_lifecycle.jsonl.gz"
    lc_rows = [
        {"event": "BUDGET_RESERVED_DURABLE", "attempt_id": "a1", "scenario": "C01_LOOPBACK_CLEAN"},
        {"event": "CHILD_PROCESS_LAUNCHED", "attempt_id": "a1", "child_pid": 12345, "containment_kind": "WINDOWS_JOB_OBJECT", "scenario": "C01_LOOPBACK_CLEAN"},
        {"event": "CHILD_PROCESS_REAPED", "attempt_id": "a1", "child_pid": 12345, "reaped_verified": True, "surviving_descendant_pids": [], "scenario": "C01_LOOPBACK_CLEAN"},
    ]
    with gzip.open(lc_gz, "wt", encoding="utf-8") as fh:
        for r in lc_rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")

    # 5. Raw 22_paired_fault_execution_receipts.jsonl.gz (56 valid distinct pairs)
    pf_gz = base_dir / "22_paired_fault_execution_receipts.jsonl.gz"
    pairs = []
    for i in range(MIN_NEW_PAIRED_CONTROLS_R6):
        pairs.append(execute_r6_fault_pair(
            R6FaultCaseSpec(f"R6-SYN-{i+1:02d}", "SYNTH", "NEGATIVE_CONTRACT", "services.kh_03_r6_source_contract_service.validate_r6_endpoint_contract", "rejected_with_violations", "accepted_clean", f"Synthetic valid control {i+1}"),
            negative_fn=lambda idx=i: ({"i": idx}, {"valid": False, "violations_count": 1}),
            positive_fn=lambda idx=i: ({"i": idx}, {"valid": True, "violations_count": 0}),
            executable_sha=sha,
            run_id=rid,
        ))
    with gzip.open(pf_gz, "wt", encoding="utf-8") as fh:
        for r in pairs:
            fh.write(json.dumps(r, sort_keys=True) + "\n")

    # 6. Unsigned owner review template
    tmpl_rel = "logs/unsigned_owner_review_template_stage_b_smoke.json"
    _write_json(base_dir / tmpl_rel, build_r6_unsigned_owner_review_template(tested_executable_sha=sha, stage_a_run_id=rid, source_registry_digest="d" * 64))

    # 7. Real valid worker policy receipts (exact R3.C1 raw_doc + policy format!) + R6 ingest benchmark
    pb_raw = _stamp({
        "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02,
        "benchmark_kind": BENCHMARK_KIND_PYTEST,
        "all_4_runs_identical": True,
        "status": "PASS",
        "runs": [
            {"mode_label": "w=1", "workers": 1, "wall_clock_seconds": 10.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "a" * 64, "passed_count": 155, "failed_count": 0, "worker_pids": [101]},
            {"mode_label": "w=2", "workers": 2, "wall_clock_seconds": 6.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "a" * 64, "passed_count": 155, "failed_count": 0, "worker_pids": [102, 103]},
            {"mode_label": "w=4", "workers": 4, "wall_clock_seconds": 4.5, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "a" * 64, "passed_count": 155, "failed_count": 0, "worker_pids": [104, 105, 106, 107]},
            {"mode_label": "w=1_repeat", "workers": 1, "wall_clock_seconds": 10.1, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "a" * 64, "passed_count": 155, "failed_count": 0, "worker_pids": [108]},
        ],
    }, rid, sha)
    sb_raw = _stamp({
        "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02,
        "benchmark_kind": BENCHMARK_KIND_SCIENCE,
        "all_4_runs_identical": True,
        "status": "PASS",
        "runs": [
            {"mode_label": "w=1", "workers": 1, "wall_seconds": 4.0, "rows_count": 8000, "canonical_sorted_sha256": "b" * 64, "worker_pids": [201]},
            {"mode_label": "w=2", "workers": 2, "wall_seconds": 2.4, "rows_count": 8000, "canonical_sorted_sha256": "b" * 64, "worker_pids": [202, 203]},
            {"mode_label": "w=4", "workers": 4, "wall_seconds": 1.8, "rows_count": 8000, "canonical_sorted_sha256": "b" * 64, "worker_pids": [204, 205, 206, 207]},
            {"mode_label": "w=1_repeat", "workers": 1, "wall_seconds": 4.05, "rows_count": 8000, "canonical_sorted_sha256": "b" * 64, "worker_pids": [208]},
        ],
    }, rid, sha)
    lim = {"configured_child_process_cap": 4, "currently_allowed_worker_cap": 3, "full_suite_safe_worker_cap": 3, "shard_exclusive_cap": 3, "preflight_disk_free_gib": 42.0, "disk_free_gib": 42.0, "available_ram_mib": 4000.0}
    py_pol = select_measured_worker_policy_strict(pb_raw, benchmark_kind=BENCHMARK_KIND_PYTEST, system_limits=lim, expected_run_id=rid, expected_tested_sha=sha, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02)
    sci_pol = select_measured_worker_policy_strict(sb_raw, benchmark_kind=BENCHMARK_KIND_SCIENCE, system_limits=lim, expected_run_id=rid, expected_tested_sha=sha, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02)
    r6_bench = run_r6_fixture_ingest_w1_w2_w4_benchmark(num_partitions=4, bars_per_partition=10)

    # 8. Write JSON artifacts 00..25, 27
    arts: Dict[str, Dict[str, Any]] = {
        "00": _stamp({"branch": MANDATORY_BRANCH_KH03_R6, "worktree_clean_at_start": True, "origin_url": "https://github.com/quocan050302/Build-app-crypto.git", "disk_waiver_used": False, "preflight_disk_free_gib": 42.0, "preflight_ram_available_mib": 4000.0}, rid, sha),
        "01": _stamp({"locked_files_modified_count": 0, "unauthorized_changed_files_count": 0, "forbidden_order_imports_count": 0, "legacy_r1_unauthorized_callers_count": 0, "production_cost_defaults_intact": True, "c2_live_hard_block_intact": True, "secrets_detected_count": 0}, rid, sha),
        "02": _stamp({"canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02, "all_prior_sealed_artifacts_intact": True, "prior_sealed_artifacts_checked_count": 25, "prior_c2_manifest_sha256_verified": True, "oos_reads_executed": 0, "oos_evaluations": 0}, rid, sha),
        "03": _stamp({"selected_api_family": API_FAMILY_CLASSIC_V2, "registry_status": "DOC_VERIFIED_READY_FOR_STAGE_B_REVIEW", "doc_verified_endpoints_count": 7, "official_docs_retrieved_count": 8, "rest_quote_snapshot_can_be_continuous_bbo": False, "mixed_v2_v3_rejected": True, "substitute_symbol_rejected": True, "private_or_redirect_rejected": True}, rid, sha),
        "04": _stamp({"default_embedded_owner_key_present": False, "unprovisioned_owner_trust_blocked": True, "unsigned_template_rejected": True, "qa_trust_cannot_authorize_external": True, "cli_env_prompt_bypass_rejected": True, "qa_isolated_ed25519_verified": True, "payload_tamper_rejected": True, "duplicate_key_rejected": True, "expired_or_clock_rollback_rejected": True, "revoked_trust_root_rejected": True, "plan_exceeding_grant_rejected_no_clamp": True, "sha_branch_store_drift_rejected": True}, rid, sha),
        "05": _stamp({"sequential_replay_denied": True, "cross_process_race_single_winner": True, "cross_process_contenders_count": 3, "cross_process_winners_count": 1, "replan_reissue_denied": True, "same_identity_different_payload_rejected": True, "post_kill_restart_replay_denied": True}, rid, sha),
        "06": _stamp({"missing_db_no_auto_init_blocked": True, "corrupt_db_quarantined": True, "anchor_rollback_quarantined": True, "post_commit_pre_anchor_forward_recovered": True, "kill_after_start_retains_unknown_bytes": True, "alternate_store_path_rejected": True, "duplicate_attempt_id_rejected": True, "double_settle_rejected": True, "negative_counter_rejected": True, "budget_overflow_rejected": True, "unknown_bytes_conservatively_retained": True, "capacity_full_blocks_without_eviction": True}, rid, sha),
        "08": _stamp({"lifecycle_gz_sha256": _sha256_file(lc_gz), "loopback_worker_launched_and_completed": True, "loopback_worker_pid": 12345, "loopback_worker_os_exit_verified": True, "actual_start_expiry_zero_dispatch": True, "external_socket_without_grant_blocked": True, "noncooperative_tree_terminated_and_reaped": True, "surviving_descendants_after_reap_count": 0, "unverified_os_proof_quarantined": True, "late_result_after_cancel_discarded": True, "slow_drip_total_deadline_enforced": True, "oversized_and_chunked_rejected": True, "gzip_bomb_rejected_bounded": True, "retry_after_honored_no_shortening": True, "nonretryable_401_403_redirect_stopped": True, "ws_valid_subscribe_ack_and_snapshot": True, "ws_invalid_ack_or_private_op_rejected": True, "ws_oversized_frame_rejected": True, "ws_disconnect_epoch_gap_and_bounded_reconnect": True, "ws_queue_overflow_no_silent_drop": True, "resource_missing_stale_nan_wrong_vol_rejected": True, "qa_fixture_telemetry_blocked_in_live": True}, rid, sha),
        "09": _stamp({"external_market_requests_attempted": 0, "accepted_real_observed_records": 0, "production_mode": "OFF", "live_trading_mode": "OFF", "paper_trading_mode": "OFF", "auto_trading_mode": "OFF", "activation_ready": "NO", "economic_verdict": "NO_PROVEN_EDGE", "fresh_oos_verdict": "BLOCKED_NO_UNTOUCHED_DATA", "kh04_entry_verdict": "BLOCKED_NO_DEV_CANDIDATE", "oos_evaluations": 0, "unsigned_owner_review_template_rel": tmpl_rel}, rid, sha),
        "10": _stamp({"qa_valid_metadata_accepted": True, "missing_tick_lot_blocked_no_default": True, "identity_mismatch_halts_grant": True}, rid, sha),
        "11": _stamp({"valid_closed_bars_accepted": True, "invalid_geometry_rejected": True, "cursor_replay_stopped": True, "contradictory_duplicate_quarantined": True, "unclosed_future_excluded_from_canonical": True, "unknown_calendar_marked_incomplete": True}, rid, sha),
        "12": _stamp({"crossed_tick_ooo_rejected": True, "rest_snapshot_rejected_as_streaming_bbo": True, "no_carry_across_disconnect_or_missing_snapshot": True, "duplicates_do_not_inflate_coverage": True, "stale_quote_not_observed_despite_heartbeat": True, "observed_vs_state_known_reported_separately": True}, rid, sha),
        "13": _stamp({"documented_settlements_validated": True, "missing_settlements_not_zero_filled": True, "unknown_interval_blocked_no_8h_default": True, "conflicting_settlement_quarantined": True}, rid, sha),
        "14": _stamp({"owner_fee_status": "UNKNOWN_NOT_LOCALLY_VERIFIED", "public_fee_rejected_as_owner_effective": True, "slippage_status": "MISSING_NO_OWNER_FILL_EVIDENCE", "public_bbo_proves_execution_slippage": False, "orders_placed_count": 0}, rid, sha),
        "15": _stamp({"qa_custody_chain_verified": True, "symlink_junction_oos_denied_before_open": True, "qa_promotion_to_dev_denied": True, "tamper_and_duplicate_receipt_rejected": True, "anchor_rollback_detected": True, "unrelated_files_preserved_on_cleanup": True, "raw_payloads_committed_to_git": False}, rid, sha),
        "16": _stamp({"forensic_ledger_gz_rel": "logs/r4_replay_forensic_ledger_1x.jsonl.gz", "forensic_ledger_gz_sha256": _sha256_file(ledger_gz), "r4_decimal_bridge_exact_match": True, "n_closed": 203, "n_wins": 7, "n_losses": 196, "n_open_censored": 28, "n_gap_events": 2, "realized_losses_gt_2p50_count": 139}, rid, sha),
        "17": _stamp({"all_six_scenarios_exact_match": True, "all_three_arms_exact_match": True}, rid, sha),
        "18": _stamp({"a1_checked": 56710, "a1_mismatches": 0, "si8_checked": 6375, "si8_mismatches": 0, "adapters_checked": 246, "adapters_mismatches": 0, "d4_mismatches": 0}, rid, sha),
        "19": _stamp({"junit_bijection_exact": True, "collected_count": n_tests, "executed_count": n_tests, "passed_count": n_tests, "skipped_count": 0, "failed_count": 0, "error_count": 0, "mandatory_skipped_count": 0, "nonzero_exit_shards_count": 0, "collected_nodeids_gz_rel": "logs/pytest_collected_executed_nodeids.jsonl.gz", "collected_nodeids_gz_sha256": _sha256_file(nodes_gz)}, rid, sha),
        "20": _stamp({"science_w1_w2_w4_all_identical": True, "r6_ingest_w1_w2_w4_all_identical": True, "r6_ingest_canonical_semantic_sha256": r6_bench["canonical_semantic_sha256"], "pytest_policy_valid": True, "science_policy_valid": True, "r6_ingest_benchmark_valid": True, "applied_safe_workers_cap": r6_bench["applied_safe_workers_cap"], "pytest_controlled_subset_raw": pb_raw, "pytest_worker_policy": py_pol, "science_slice_8000_raw": sb_raw, "science_worker_policy": sci_pol, "r6_ingest_benchmark": r6_bench}, rid, sha),
        "21": _stamp({"telemetry_samples_gz_rel": "logs/runtime_memory_samples_0p5s.jsonl.gz", "telemetry_samples_gz_sha256": _sha256_file(tel_gz), "disk_waiver_exit2_proven": True, "min_observed_disk_free_gib": 42.0, "min_observed_ram_available_mib": 4000.0, "samples_count": 4, "max_observed_descendants": 2, "owned_temp_cleaned_verified": True, "orphan_workers_count": 0}, rid, sha),
        "23": _stamp({"paired_receipts_gz_sha256": _sha256_file(pf_gz), "new_r6_paired_verified_count": len(pairs), "new_r6_paired_failed_count": 0, "r6_fault_ast_literal_flags_count": 0, "unique_r6_execution_ids_ok": True, "inherited_c1_c2_paired_passed_count": 91, "inherited_c1_c2_paired_failed_count": 0, "inherited_regression_passed_count": 288, "inherited_regression_failed_count": 0, "inherited_not_relabelled_as_new_r6": True}, rid, sha),
        "24": _stamp({"clean_clone_verifier_green": True, "new_r6_mutations_tested_count": 32, "new_r6_mutations_detected_count": 32, "inherited_mutations_tested_count": 135, "inherited_mutations_detected_count": 135}, rid, sha),
        "27": _stamp({"phase1_executable_committed": True}, rid, sha),
    }
    arts["25"] = _stamp(derive_r6_readiness_verdicts({**arts, "26": {"distinct_os_process_verified": True, "verifier_status": "PASS", "violations_count": 0, "verifier_pid": 9999, "orchestrator_pid": 8888}}, stage="STAGE_A_OFFLINE", expected_run_id=rid, expected_tested_sha=sha), rid, sha)

    name_map = {
        "00": "00_preflight_branch_sha_resource_lock.json",
        "01": "01_function_ownership_keep_as_is_manifest.json",
        "02": "02_prior_c2_dataset_sealed_integrity_no_oos.json",
        "03": "03_official_source_contracts_and_api_family_registry.json",
        "04": "04_owner_trust_scope_and_plan_redacted.json",
        "05": "05_durable_grant_claim_cross_process_restart_receipts.json",
        "06": "06_ledger_crash_anchor_and_conservation_receipts.json",
        "08": "08_rest_ws_bounds_loopback_and_cancel_reap.json",
        "09": "09_capture_authorization_external_dispatch_provenance.json",
        "10": "10_instrument_identity_metadata_validation.json",
        "11": "11_ohlcv_pagination_lineage_gap_coverage.json",
        "12": "12_bbo_epoch_event_state_coverage.json",
        "13": "13_funding_settlement_completeness.json",
        "14": "14_fee_slippage_execution_fidelity_register.json",
        "15": "15_dev_partition_custody_dataset_manifest.json",
        "16": "16_r4_replay_decimal_economic_bridge.json",
        "17": "17_six_cost_scenarios_and_arm_regression.json",
        "18": "18_A1_SI8_adapter_D4_full_parity.json",
        "19": "19_full_pytest_fresh_collection_junit_bijection.json",
        "20": "20_W1_W2_W4_W1repeat_benchmark_worker_policy.json",
        "21": "21_actual_resource_telemetry_cleanup.json",
        "23": "23_inherited_regression_and_new_fault_truth.json",
        "24": "24_independent_verifier_mutation_results.json",
        "25": "25_orthogonal_gates_and_verdicts.json",
        "27": "27_phase1_code_commit_push_receipt.json",
    }
    for k, fname in name_map.items():
        _write_json(base_dir / fname, arts[k])
    (base_dir / "README.md").write_text("# Synthetic Valid R6 Run\n", encoding="utf-8")
    (base_dir / "HANDOVER_KH03_R6_VI.txt").write_text("Synthetic handover\n", encoding="utf-8")
    return rid, sha


def run_kh03_r6_verifier_subprocess(run_dir: Path, run_id: str, tested_sha: str) -> Tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONPATH"] = os.pathsep.join([str(APP_ROOT / "backend"), str(APP_ROOT), str(REPO_ROOT)])
    proc = subprocess.run(
        [
            sys.executable,
            str(VERIFIER_PATH),
            "--run-dir",
            str(run_dir),
            "--expected-run-id",
            run_id,
            "--expected-tested-sha",
            tested_sha,
            "--orchestrator-pid",
            str(os.getpid()),
            "--no-write-manifest",
            "--skip-prior-disk-check",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return int(proc.returncode), (proc.stdout + "\n" + proc.stderr)


def _edit_json(d: Path, fname: str, fn: Callable[[Dict[str, Any]], None]) -> None:
    p = d / fname
    obj = json.loads(p.read_text("utf-8"))
    fn(obj)
    _write_json(p, obj)


def _mut_m01_wrong_api_family(d: Path) -> None:
    _edit_json(d, "03_official_source_contracts_and_api_family_registry.json", lambda o: o.__setitem__("selected_api_family", "BITGET_UTA_V3"))

def _mut_m02_rest_snapshot_as_bbo(d: Path) -> None:
    _edit_json(d, "03_official_source_contracts_and_api_family_registry.json", lambda o: o.__setitem__("rest_quote_snapshot_can_be_continuous_bbo", True))

def _mut_m03_embedded_owner_key(d: Path) -> None:
    _edit_json(d, "04_owner_trust_scope_and_plan_redacted.json", lambda o: o.__setitem__("default_embedded_owner_key_present", True))

def _mut_m04_qa_can_authorize_external(d: Path) -> None:
    _edit_json(d, "04_owner_trust_scope_and_plan_redacted.json", lambda o: o.__setitem__("qa_trust_cannot_authorize_external", False))

def _mut_m05_cross_process_2_winners(d: Path) -> None:
    _edit_json(d, "05_durable_grant_claim_cross_process_restart_receipts.json", lambda o: o.__setitem__("cross_process_winners_count", 2))

def _mut_m06_restart_replay_allowed(d: Path) -> None:
    _edit_json(d, "05_durable_grant_claim_cross_process_restart_receipts.json", lambda o: o.__setitem__("post_kill_restart_replay_denied", False))

def _mut_m07_missing_db_auto_init(d: Path) -> None:
    _edit_json(d, "06_ledger_crash_anchor_and_conservation_receipts.json", lambda o: o.__setitem__("missing_db_no_auto_init_blocked", False))

def _mut_m08_anchor_rollback_ignored(d: Path) -> None:
    _edit_json(d, "06_ledger_crash_anchor_and_conservation_receipts.json", lambda o: o.__setitem__("anchor_rollback_quarantined", False))

def _mut_m09_unknown_bytes_refunded(d: Path) -> None:
    _edit_json(d, "06_ledger_crash_anchor_and_conservation_receipts.json", lambda o: o.__setitem__("kill_after_start_retains_unknown_bytes", False))

def _mut_m10_transport_lifecycle_pid_mismatch(d: Path) -> None:
    _edit_json(d, "08_rest_ws_bounds_loopback_and_cancel_reap.json", lambda o: o.__setitem__("loopback_worker_pid", 999999))

def _mut_m11_transport_surviving_descendants_rehashed(d: Path) -> None:
    gz = d / "07_transport_actual_process_lifecycle.jsonl.gz"
    rows = [json.loads(ln) for ln in gzip.open(gz, "rt", encoding="utf-8") if ln.strip()]
    rows[-1]["surviving_descendant_pids"] = [55555]
    with gzip.open(gz, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    _edit_json(d, "08_rest_ws_bounds_loopback_and_cancel_reap.json", lambda o: o.__setitem__("lifecycle_gz_sha256", _sha256_file(gz)))

def _mut_m12_transport_gz_sha_mismatch(d: Path) -> None:
    _edit_json(d, "08_rest_ws_bounds_loopback_and_cancel_reap.json", lambda o: o.__setitem__("lifecycle_gz_sha256", "0" * 64))

def _mut_m13_unauthorized_external_request_stage_a(d: Path) -> None:
    _edit_json(d, "09_capture_authorization_external_dispatch_provenance.json", lambda o: o.__setitem__("external_market_requests_attempted", 1))

def _mut_m14_owner_template_marked_valid_auth(d: Path) -> None:
    _edit_json(d, "logs/unsigned_owner_review_template_stage_b_smoke.json", lambda o: o.__setitem__("valid_authorization", True))

def _mut_m15_oos_gt_zero(d: Path) -> None:
    _edit_json(d, "09_capture_authorization_external_dispatch_provenance.json", lambda o: o.__setitem__("oos_evaluations", 1))

def _mut_m16_activation_ready_yes(d: Path) -> None:
    _edit_json(d, "09_capture_authorization_external_dispatch_provenance.json", lambda o: o.__setitem__("activation_ready", "YES"))

def _mut_m17_bbo_merged_event_state(d: Path) -> None:
    _edit_json(d, "12_bbo_epoch_event_state_coverage.json", lambda o: o.__setitem__("observed_vs_state_known_reported_separately", False))

def _mut_m18_funding_zero_filled(d: Path) -> None:
    _edit_json(d, "13_funding_settlement_completeness.json", lambda o: o.__setitem__("missing_settlements_not_zero_filled", False))

def _mut_m19_fake_owner_fee_verified(d: Path) -> None:
    _edit_json(d, "14_fee_slippage_execution_fidelity_register.json", lambda o: o.__setitem__("owner_fee_status", "LOCALLY_VERIFIED"))

def _mut_m20_qa_promoted_to_dev(d: Path) -> None:
    _edit_json(d, "15_dev_partition_custody_dataset_manifest.json", lambda o: o.__setitem__("qa_promotion_to_dev_denied", False))

def _mut_m21_r4_ledger_tampered_rehashed(d: Path) -> None:
    gz = d / "logs" / "r4_replay_forensic_ledger_1x.jsonl.gz"
    rows = [json.loads(ln) for ln in gzip.open(gz, "rt", encoding="utf-8") if ln.strip()]
    rows[0]["realized_gross_pnl_usd"] = "999.0000"
    rows[0]["realized_net_pnl_usd"] = "998.0000"
    with gzip.open(gz, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    _edit_json(d, "16_r4_replay_decimal_economic_bridge.json", lambda o: o.__setitem__("forensic_ledger_gz_sha256", _sha256_file(gz)))

def _mut_m22_parity_a1_mismatch(d: Path) -> None:
    _edit_json(d, "18_A1_SI8_adapter_D4_full_parity.json", lambda o: o.__setitem__("a1_mismatches", 1))

def _mut_m23_junit_missing_testcase(d: Path) -> None:
    xp = d / "junit" / "shard_00.xml"
    lines = xp.read_text("utf-8").splitlines()
    xp.write_text("\n".join(lines[:-2] + [lines[-1]]), encoding="utf-8")

def _mut_m24_junit_mandatory_skip(d: Path) -> None:
    xp = d / "junit" / "shard_00.xml"
    txt = xp.read_text("utf-8").replace('classname="backend.tests.test_synth" name="test_case_0" time="0.001"/>', 'classname="backend.tests.test_kh03_r6_source_contracts" name="test_case_0" time="0.001"><skipped/></testcase>')
    xp.write_text(txt, encoding="utf-8")

def _mut_m25_benchmark_semantic_mismatch(d: Path) -> None:
    def _m(o: Dict[str, Any]) -> None:
        o["r6_ingest_benchmark"]["runs"][1]["semantic_result_sha256"] = "0" * 64
    _edit_json(d, "20_W1_W2_W4_W1repeat_benchmark_worker_policy.json", _m)

def _mut_m26_telemetry_ram_below_floor_rehashed(d: Path) -> None:
    gz = d / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    rows = [json.loads(ln) for ln in gzip.open(gz, "rt", encoding="utf-8") if ln.strip()]
    rows[0]["available_mib"] = 256.0
    with gzip.open(gz, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    _edit_json(d, "21_actual_resource_telemetry_cleanup.json", lambda o: o.__setitem__("telemetry_samples_gz_sha256", _sha256_file(gz)))

def _mut_m27_paired_fault_predicate_forged_rehashed(d: Path) -> None:
    gz = d / "22_paired_fault_execution_receipts.jsonl.gz"
    rows = [json.loads(ln) for ln in gzip.open(gz, "rt", encoding="utf-8") if ln.strip()]
    rows[0]["negative"]["observed_fields"]["valid"] = True  # violates rejected_with_violations!
    with gzip.open(gz, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    _edit_json(d, "23_inherited_regression_and_new_fault_truth.json", lambda o: o.__setitem__("paired_receipts_gz_sha256", _sha256_file(gz)))

def _mut_m28_paired_fault_below_56_rehashed(d: Path) -> None:
    gz = d / "22_paired_fault_execution_receipts.jsonl.gz"
    rows = [json.loads(ln) for ln in gzip.open(gz, "rt", encoding="utf-8") if ln.strip()][:50]
    with gzip.open(gz, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    def _upd(o: Dict[str, Any]) -> None:
        o["paired_receipts_gz_sha256"] = _sha256_file(gz)
        o["new_r6_paired_verified_count"] = 50
    _edit_json(d, "23_inherited_regression_and_new_fault_truth.json", _upd)

def _mut_m29_mutations_below_30(d: Path) -> None:
    def _upd(o: Dict[str, Any]) -> None:
        o["new_r6_mutations_tested_count"] = 20
        o["new_r6_mutations_detected_count"] = 20
    _edit_json(d, "24_independent_verifier_mutation_results.json", _upd)

def _mut_m30_fake_stage_bc_pass_in_stage_a(d: Path) -> None:
    def _upd(o: Dict[str, Any]) -> None:
        o["gates"]["G31_EXTERNAL_AUTHORIZATION"] = "PASS"
        o["stage_denominators"]["stage_bc_executed_count"] = 1
    _edit_json(d, "25_orthogonal_gates_and_verdicts.json", _upd)

def _mut_m31_stale_prior_c2_sha(d: Path) -> None:
    _edit_json(d, "00_preflight_branch_sha_resource_lock.json", lambda o: o.__setitem__("tested_executable_sha", PRIOR_C2_TESTED_EXECUTABLE_SHA))

def _mut_m32_duplicate_json_key(d: Path) -> None:
    p = d / "10_instrument_identity_metadata_validation.json"
    txt = p.read_text("utf-8").rstrip()
    p.write_text(txt[:-1] + ', "qa_valid_metadata_accepted": true}\n', encoding="utf-8")


KH03_R6_OWN_VERIFIER_MUTATIONS_SPECS: Tuple[Tuple[str, Callable[[Path], None], str], ...] = (
    ("R6-M01_wrong_api_family", _mut_m01_wrong_api_family, "R6V_SOURCE_CONTRACT_03_INVALID"),
    ("R6-M02_rest_snapshot_as_bbo", _mut_m02_rest_snapshot_as_bbo, "R6V_SOURCE_CONTRACT_03_INVALID"),
    ("R6-M03_embedded_owner_key", _mut_m03_embedded_owner_key, "R6V_OWNER_TRUST_04_INVALID"),
    ("R6-M04_qa_can_authorize_ext", _mut_m04_qa_can_authorize_external, "R6V_OWNER_TRUST_04_INVALID"),
    ("R6-M05_cross_proc_2_winners", _mut_m05_cross_process_2_winners, "R6V_DURABLE_CLAIM_05_INVALID"),
    ("R6-M06_restart_replay_allowed", _mut_m06_restart_replay_allowed, "R6V_DURABLE_CLAIM_05_INVALID"),
    ("R6-M07_missing_db_auto_init", _mut_m07_missing_db_auto_init, "R6V_LEDGER_CRASH_06_INVALID"),
    ("R6-M08_anchor_rollback_ignored", _mut_m08_anchor_rollback_ignored, "R6V_LEDGER_CRASH_06_INVALID"),
    ("R6-M09_unknown_bytes_refunded", _mut_m09_unknown_bytes_refunded, "R6V_LEDGER_CRASH_06_INVALID"),
    ("R6-M10_transport_pid_mismatch", _mut_m10_transport_lifecycle_pid_mismatch, "R6V_TRANSPORT_LIFECYCLE_PID_MISMATCH_VS_08"),
    ("R6-M11_surviving_desc_rehashed", _mut_m11_transport_surviving_descendants_rehashed, "R6V_TRANSPORT_LIFECYCLE_SURVIVING_DESCENDANTS"),
    ("R6-M12_transport_gz_sha_mismatch", _mut_m12_transport_gz_sha_mismatch, "R6V_TRANSPORT_LIFECYCLE_GZ_SHA_MISMATCH"),
    ("R6-M13_unauth_ext_req_stage_a", _mut_m13_unauthorized_external_request_stage_a, "R6V_UNAUTHORIZED_EXTERNAL_TRAFFIC_IN_STAGE_A"),
    ("R6-M14_owner_tmpl_marked_valid", _mut_m14_owner_template_marked_valid_auth, "R6V_OWNER_TEMPLATE_NOT_STRICTLY_UNSIGNED"),
    ("R6-M15_oos_gt_zero", _mut_m15_oos_gt_zero, "R6V_ECONOMIC_OOS_ACTIVATION_09_VIOLATION"),
    ("R6-M16_activation_ready_yes", _mut_m16_activation_ready_yes, "R6V_ECONOMIC_OOS_ACTIVATION_09_VIOLATION"),
    ("R6-M17_bbo_merged_event_state", _mut_m17_bbo_merged_event_state, "R6V_BBO_COVERAGE_12_INVALID"),
    ("R6-M18_funding_zero_filled", _mut_m18_funding_zero_filled, "R6V_FUNDING_13_INVALID"),
    ("R6-M19_fake_owner_fee_verified", _mut_m19_fake_owner_fee_verified, "R6V_FEE_SLIPPAGE_14_INVALID"),
    ("R6-M20_qa_promoted_to_dev", _mut_m20_qa_promoted_to_dev, "R6V_CUSTODY_15_INVALID"),
    ("R6-M21_r4_ledger_tampered", _mut_m21_r4_ledger_tampered_rehashed, "R6V_R4_DECIMAL_SUM_MISMATCH"),
    ("R6-M22_parity_a1_mismatch", _mut_m22_parity_a1_mismatch, "R6V_BASELINE_PARITY_18_INVALID"),
    ("R6-M23_junit_missing_testcase", _mut_m23_junit_missing_testcase, "R6V_JUNIT_XML_COUNT_MISMATCH"),
    ("R6-M24_junit_mandatory_skip", _mut_m24_junit_mandatory_skip, "R6V_JUNIT_XML_NONZERO_FAIL_ERR_MANDATORY_SKIP"),
    ("R6-M25_benchmark_sem_mismatch", _mut_m25_benchmark_semantic_mismatch, "R6V_R6_INGEST_SEMANTIC_HASH_MISMATCH"),
    ("R6-M26_telemetry_ram_below_512", _mut_m26_telemetry_ram_below_floor_rehashed, "R6V_TELEMETRY_RAM_BELOW_512"),
    ("R6-M27_paired_fault_forged", _mut_m27_paired_fault_predicate_forged_rehashed, "R6V_PAIRED_FAULT_INVALID"),
    ("R6-M28_paired_fault_below_56", _mut_m28_paired_fault_below_56_rehashed, "R6V_PAIRED_FAULT_BELOW_56"),
    ("R6-M29_mutations_below_30", _mut_m29_mutations_below_30, "R6V_MUTATIONS_24_INVALID"),
    ("R6-M30_fake_stage_bc_pass", _mut_m30_fake_stage_bc_pass_in_stage_a, "R6V_FORBIDDEN_STAGE_BC_PASS_IN_STAGE_A"),
    ("R6-M31_stale_prior_c2_sha", _mut_m31_stale_prior_c2_sha, "R6V_STALE_PRIOR_SHA"),
    ("R6-M32_duplicate_json_key", _mut_m32_duplicate_json_key, "R6V_JSON_STRICT_PARSE_ERR"),
)


def test_kh03_r6_verifier_clean_clone_and_all_32_mutations() -> None:
    assert len(KH03_R6_OWN_VERIFIER_MUTATIONS_SPECS) >= 30
    with tempfile.TemporaryDirectory(prefix="r6_mut32_") as td:
        tdir = Path(td)
        base = tdir / "base"
        rid, sha = build_synthetic_valid_kh03_r6_run_dir(base)
        rc_clean, out_clean = run_kh03_r6_verifier_subprocess(base, rid, sha)
        assert rc_clean == 0, out_clean

        def _run_one(spec: Tuple[str, Callable[[Path], None], str]) -> Tuple[str, bool, str]:
            mid, mfn, tok = spec
            cdir = tdir / f"clone_{mid}"
            shutil.copytree(base, cdir)
            try:
                mfn(cdir)
                rc, out = run_kh03_r6_verifier_subprocess(cdir, rid, sha)
                return mid, (rc != 0 and tok in out), f"rc={rc} tok={tok} out={out[:200]}"
            finally:
                shutil.rmtree(cdir, ignore_errors=True)

        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(_run_one, KH03_R6_OWN_VERIFIER_MUTATIONS_SPECS))

        failures = [r for r in results if not r[1]]
        assert not failures, f"Missed verifier mutations: {failures}"
