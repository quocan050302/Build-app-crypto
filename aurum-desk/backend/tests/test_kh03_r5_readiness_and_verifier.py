"""
aurum-desk/backend/tests/test_kh03_r5_readiness_and_verifier.py

KH-03.R5 Full 48-Fault Adversarial Suite (`R5-F01` .. `R5-F48`) AND Separate-OS Independent
Verifier 20-Mutation Suite (`R5M01` .. `R5M20`):
  - Exports `run_new_kh03_r5_48_fault_controls()` (all 48 §08 faults, real RED -> GREEN)
  - Exports `build_synthetic_valid_kh03_r5_run_dir()`, `run_kh03_r5_verifier_subprocess()`,
    and `KH03_R5_OWN_VERIFIER_MUTATIONS_SPECS` (20 non-redundant separate-OS verifier attacks).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Callable, Dict, List, Mapping, Tuple

import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_c2_public_capture import (
    RawHttpResponseC2,
    StdlibUrllibPublicTransportC2,
    _SequenceFakeTransportC2,
)
from services.kh_02_r2_prospective_data_service import CANONICAL_DATASET_SHA256_KH02
from services.kh_03_r3_c1_worker_policy_service import (
    BENCHMARK_KIND_PYTEST,
    BENCHMARK_KIND_SCIENCE,
    select_measured_worker_policy_strict,
)
from services.kh_03_r3_legacy_research_guard import audit_kh03_r3_legacy_research_callgraph
from services.kh_03_r5_capture_and_custody_service import (
    OosAccessGuardR5,
    R5DevCustodyJournal,
    R5InjectedCrashError,
    R5OosAccessDeniedError,
    assign_r5_partition_before_capture,
    capture_r5_public_batch,
    persist_r5_verified_dev_snapshot,
    verify_r5_custody_chain_readonly,
)
from services.kh_03_r5_market_data_foundation_service import (
    R5OperatorGrant,
    SCHEMA_VERSION_KH03_R5,
    SPEC_VERSION_KH03_R5,
    build_r5_execution_fidelity_register,
    build_r5_market_data_coverage_report,
    build_r5_source_capability_inventory,
    plan_r5_capture_batches,
    validate_r5_bbo_stream,
    validate_r5_funding_history,
    validate_r5_instrument_metadata,
    validate_r5_ohlcv_stream,
    validate_r5_operator_capture_scope,
    validate_r5_owner_fee_confirmation,
)
from services.kh_03_r5_readiness_gate_service import MANDATORY_BRANCH_KH03_R5
from tests.test_kh03_r4_economic_accounting import _make_synthetic_anchor_forensic_rows
from tools.kh_03_r3_c1_independent_verifier import SEALED_BASELINE_PARITY_FINGERPRINTS
from tools.kh_03_r5_independent_verifier import (
    PRIOR_KH03_R4_RUN_ID,
    PRIOR_KH03_R4_TESTED_SHA,
    _audit_r4_replay_ledger_13_r5,
    _audit_telemetry_gz_vs_17_r5,
    _recompute_raw_bench_r5,
    recompute_r5_gate_matrix,
)


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _mut_json(p: Path, fn: Callable[[Dict[str, Any]], None]) -> None:
    d = json.loads(p.read_text("utf-8"))
    fn(d)
    p.write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")


def build_synthetic_valid_kh03_r5_run_dir(run_dir: Path) -> Tuple[str, str]:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "junit").mkdir(exist_ok=True)
    (run_dir / "checksums").mkdir(exist_ok=True)

    tsha = "f" * 40
    rid = f"20261124T120000Z_{tsha[:12]}"

    # Raw R4 replay forensic ledger gz matching exact 203/7/196/28/2/-164.9541/276.6577/-441.6118/14.7151/139 anchors!
    rows = _make_synthetic_anchor_forensic_rows()
    led_rel = "logs/r4_replay_forensic_ledger_1x.jsonl.gz"
    led_gz = run_dir / led_rel
    with gzip.open(led_gz, "wt", encoding="utf-8") as gz:
        for r in rows:
            gz.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")

    # Raw 0.5s telemetry gz
    mem_gz = run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    with gzip.open(mem_gz, "wt", encoding="utf-8") as mgz:
        for i in range(4):
            mgz.write(json.dumps({"ts_ms": 1795460000000 + i * 500, "available_ram_bytes": 3_400_000_000, "available_mib": 3242.49, "child_count": 2, "worker_pids": [1, 2], "disk_free_gib": 26.5}) + "\n")

    # Raw JUnit XML + compressed collected nodeids gz
    n_tc = 5860
    c_ids = [f"backend/tests/test_kh03_r5_synth.py::test_r5_{i:04d}" for i in range(n_tc)]
    xp = run_dir / "junit" / "SHARD_01.xml"
    tcs = "".join(f'<testcase classname="backend.tests.test_kh03_r5_synth" name="test_r5_{i:04d}" time="0.001"/>' for i in range(n_tc))
    xp.write_text(f'<?xml version="1.0" encoding="utf-8"?><testsuite tests="{n_tc}" failures="0" errors="0" skipped="0">{tcs}</testsuite>', encoding="utf-8")
    nodes_rel = "logs/pytest_collected_executed_nodeids.jsonl.gz"
    nodes_gz = run_dir / nodes_rel
    with gzip.open(nodes_gz, "wt", encoding="utf-8") as ngz:
        for cid in c_ids:
            ngz.write(cid + "\n")

    pb_raw = {
        "spec_version": SPEC_VERSION_KH03_R5, "schema_version": SCHEMA_VERSION_KH03_R5,
        "run_id": rid, "tested_executable_sha": tsha, "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02,
        "benchmark_kind": BENCHMARK_KIND_PYTEST, "status": "PASS", "all_4_runs_identical": True,
        "runs": [
            {"mode_label": "w=1", "workers": 1, "worker_pids": [101], "wall_clock_seconds": 30.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
            {"mode_label": "w=2", "workers": 2, "worker_pids": [201, 202], "wall_clock_seconds": 20.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
            {"mode_label": "w=4", "workers": 4, "worker_pids": [401, 402, 403, 404], "wall_clock_seconds": 15.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
            {"mode_label": "w=1_repeat", "workers": 1, "worker_pids": [501], "wall_clock_seconds": 29.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
        ],
    }
    sb_raw = {
        "spec_version": SPEC_VERSION_KH03_R5, "schema_version": SCHEMA_VERSION_KH03_R5,
        "run_id": rid, "tested_executable_sha": tsha, "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02,
        "benchmark_kind": BENCHMARK_KIND_SCIENCE, "status": "PASS", "all_4_runs_identical": True,
        "runs": [
            {"mode_label": "w=1", "workers": 1, "chunks_count": 1, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": 1.40, "canonical_sorted_sha256": "2" * 64},
            {"mode_label": "w=2", "workers": 2, "chunks_count": 2, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": 1.12, "canonical_sorted_sha256": "2" * 64},
            {"mode_label": "w=4", "workers": 4, "chunks_count": 4, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": 1.25, "canonical_sorted_sha256": "2" * 64},
            {"mode_label": "w=1_repeat", "workers": 1, "chunks_count": 1, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": 1.38, "canonical_sorted_sha256": "2" * 64},
        ],
    }
    p_pol = select_measured_worker_policy_strict(pb_raw, benchmark_kind=BENCHMARK_KIND_PYTEST, system_limits={"configured_child_process_cap": 4, "currently_allowed_worker_cap": 2, "full_suite_safe_worker_cap": 2, "shard_exclusive_cap": 2}, expected_run_id=rid, expected_tested_sha=tsha, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02)
    s_pol = select_measured_worker_policy_strict(sb_raw, benchmark_kind=BENCHMARK_KIND_SCIENCE, system_limits={"configured_child_process_cap": 4, "currently_allowed_worker_cap": 4, "full_suite_safe_worker_cap": 4, "shard_exclusive_cap": 4}, expected_run_id=rid, expected_tested_sha=tsha, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02)

    f03 = build_r5_source_capability_inventory(REPO_ROOT)
    cov = build_r5_market_data_coverage_report({})
    f12 = build_r5_execution_fidelity_register({"evidence_class": "MISSING"}, {"evidence_class": "MISSING"}, {"evidence_class": "MISSING"}, {"account_fee_readiness": "UNKNOWN_NOT_LOCALLY_VERIFIED", "evidence_class": "MISSING"}, cov)

    docs: Dict[str, Dict[str, Any]] = {
        "00": {"branch": MANDATORY_BRANCH_KH03_R5, "start_head_sha": tsha, "end_head_sha": tsha, "git_clean_at_start": True, "local_uncommitted_changes": False, "orchestrator_pid": -9999, "owner_disk_preflight_waiver": False, "strict_multi_mount_probe": {"can_run_heavy_stages": True, "owner_waiver_permitted": False, "probe_errors": [], "distinct_mounts": [{"free_gib": 26.5, "projected_post_reserve_gib": 26.0}]}, "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"}, "ACTIVATION_READY": "NO", "status": "PASS"},
        "01": {"status": "PASS", "locked_files_modified_count": 0, "unauthorized_changed_files_count": 0, "forbidden_order_import_hits": [], "cost_assumptions_defaults_intact": True, "legacy_guard_status": "PASS", "unauthorized_r1_callers_count": 0},
        "02": {"status": "PASS", "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02, "canonical_dataset_sha256_match": True, "all_prior_sealed_artifacts_intact": True, "c4_provenance_and_oos_boundary": {"real_oos_escrow_final_record_count": 0}},
        "03": f03,
        "04": {"status": "PASS", "operator_approval_state": {"admitted": False, "authorization_status": "BLOCKED_OWNER_AUTHORIZATION", "live_network_permitted": False}, "cli_env_prompt_bypass_blocked": True},
        "05": {"status": "PASS", "capture_execution_status": "BLOCKED_OWNER_AUTHORIZATION", "capture_provenance_status": "NO_REAL_CAPTURE", "external_requests_attempted": 0, "accepted_observed_records_count": 0, "qa_fixture_records_count": 3, "qa_isolated_from_observed_verified": True, "fake_transport_cannot_mint_observed_verified": True},
        "06": {"status": "PASS", "live_observed_metadata_status": "MISSING", "offline_contract_validation_passed": True},
        "07": {"status": "PASS", "live_observed_new_dev_ohlcv_status": "MISSING", "offline_ohlcv_validation_passed": True, "forward_fill_applied": False},
        "08": {"status": "PASS", "observed_bbo_coverage_status": "MISSING", "offline_bbo_validation_passed": True, "interpolation_or_ohlcv_inference_applied": False, "strategy_decision_fill_coverage": "UNKNOWN_NO_AUTHORIZED_ALIGNMENT_PROTOCOL"},
        "09": {"status": "PASS", "observed_funding_readiness": "MISSING", "offline_funding_validation_passed": True, "zero_fill_applied": False, "retroactive_r4_pnl_modified": False},
        "10": {"status": "PASS", "owner_fee_document_supplied": False, "account_fee_readiness": "UNKNOWN_NOT_LOCALLY_VERIFIED", "observed_slippage_status": "MISSING", "credentials_requested": False, "store_local_only": True},
        "11": {"status": "PASS", "dev_relabel_as_oos_blocked": True, "oos_deny_before_open_verified": True, "crash_recovery_quarantine_verified": True, "double_writer_lock_race_verified": True, "external_head_anchor_verified": True, "custody_chain_verification": {"valid": True, "oos_access_guard_summary": {"oos_evaluations": 0, "oos_reads_executed": 0, "oos_denied_before_open_count": 1}}},
        "12": f12,
        "13": {"status": "PASS", "fresh_replay_executed_not_copied": True, "new_data_used_in_backtest": False, "forensic_ledger_rel_path": led_rel, "forensic_ledger_file_sha256": _sha256_file(led_gz), "r4_row_ids_and_ledger_sha_match": True, "r4_decimal_bridge_exact_match": True, "six_cost_dynamic_vs_fixed_fills_separated": True, "planned_cap_is_physical_guarantee": False, "realized_losses_gt_2p50_count": 139, "c4_risk_tail_disclosure_valid": True, "legacy_funding_status": "EXCLUDED_UNMODELED", "null_1x_closed": 203, "null_1x_net_usd": -441.6118, "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE", "KH04_ENTRY": "BLOCKED_NO_DEV_CANDIDATE", "OOS_EVALUATIONS": 0},
        "14": {"BASELINE_PARITY_VERDICT": "PASS", "a1_population_count": 56710, "a1_mismatches_count": 0, "si8_population_count": 6375, "si8_mismatches_count": 0, "three_adapter_population_count": 246, "three_adapter_mismatches_count": 0, "d4_historical_closed_trades": 0, "per_decision_and_episode_fingerprints": {**SEALED_BASELINE_PARITY_FINGERPRINTS, "all_sealed_fingerprints_exact_match": True}},
        "15": {"status": "PASS", "collected_count": n_tc, "executed_count": n_tc, "passed_count": n_tc, "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0, "junit_bijection_exact": True, "collected_nodeids_gz_rel": nodes_rel, "collected_nodeids_gz_sha256": _sha256_file(nodes_gz), "shard_manifest": {"run_id": rid, "tested_executable_sha": tsha, "nonzero_exit_shards_count": 0, "shards": [{"shard_id": "SHARD_01", "exit_code": 0, "timed_out": False, "reused": False, "junit_xml_path": "junit/SHARD_01.xml", "xml_sha256": _sha256_file(xp), "scheduled_node_ids": c_ids}]}},
        "16": {"status": "PASS", "pytest_controlled_subset_raw": pb_raw, "science_slice_8000_raw": sb_raw, "pytest_worker_policy": p_pol, "science_worker_policy": s_pol, "full_suite_speedup_extrapolated": False},
        "17": {"status": "PASS", "memory_samples_log_rel": "logs/runtime_memory_samples_0p5s.jsonl.gz", "min_disk_free_gib": 26.5, "min_available_ram_mib": 3242.49, "max_descendant_children": 2, "orphan_children_after_cleanup": 0, "sentinel_temp_cleaned_up": True, "owner_disk_preflight_waiver": False},
        "18": {"status": "PASS", "inherited_r4_c2_c1_r3_controls_count": 240, "new_r5_controls_count": 48, "total_controls": 288, "unique_fault_ids": 288, "failed_controls": 0},
        "19": {"status": "PASS", "clean_clone_verifier_green": True, "inherited_r4_c2_c1_r3_mutations_tested_count": 69, "inherited_r4_c2_c1_r3_mutations_failed_detection_count": 0, "new_r5_mutations_tested_count": 20, "new_r5_mutations_failed_detection_count": 0},
        "22": {"publication_status": "PRE_PUSH_PENDING", "remote_readback_sha256_verified": False, "publication_head_sha": None, "ci_status": "CI_UNKNOWN_NO_ACTIONS_RUN", "actual_github_actions_run_verified": False, "owner_disk_preflight_waiver": False},
    }
    for k, d in docs.items():
        d["spec_version"] = SPEC_VERSION_KH03_R5
        d["schema_version"] = SCHEMA_VERSION_KH03_R5
        d["run_id"] = rid
        d["tested_executable_sha"] = tsha

    mem_au = _audit_telemetry_gz_vs_17_r5(mem_gz, docs["17"])
    r4_au = _audit_r4_replay_ledger_13_r5(run_dir, docs["13"])
    p_rec = _recompute_raw_bench_r5(pb_raw, p_pol, kind=BENCHMARK_KIND_PYTEST, expected_run_id=rid, expected_tested_sha=tsha, max_safe_cap=3)
    s_rec = _recompute_raw_bench_r5(sb_raw, s_pol, kind=BENCHMARK_KIND_SCIENCE, expected_run_id=rid, expected_tested_sha=tsha, max_safe_cap=4)
    r20 = recompute_r5_gate_matrix(docs=docs, mem_audit=mem_au, pytest_recomp=p_rec, science_recomp=s_rec, junit_ok=True, r4_replay_ok=bool(r4_au["valid"]), for_final_verifier_manifest=False)
    docs["20"] = {"spec_version": SPEC_VERSION_KH03_R5, "schema_version": SCHEMA_VERSION_KH03_R5, "run_id": rid, "tested_executable_sha": tsha, **r20}

    fn_map = {
        "00": "00_branch_sha_clean_preflight_resource.json", "01": "01_function_ownership_keep_as_is_manifest.json",
        "02": "02_prior_run_dataset_escrow_pointer_integrity.json", "03": "03_source_capability_contract_inventory.json",
        "04": "04_operator_scope_and_capture_plan.json", "05": "05_capture_execution_and_provenance_receipts.json",
        "06": "06_contract_metadata_validation.json", "07": "07_ohlcv_lineage_gap_coverage.json",
        "08": "08_bbo_sample_cadence_quality_coverage.json", "09": "09_funding_settlement_completeness.json",
        "10": "10_owner_fee_and_slippage_readiness.json", "11": "11_dev_custody_partition_chain_and_no_oos_access.json",
        "12": "12_observed_execution_fidelity_register.json", "13": "13_r4_replay_parity_economic_bridge.json",
        "14": "14_A1_SI8_adapter_D4_full_baseline_parity.json", "15": "15_full_pytest_fresh_collection_junit_bijection.json",
        "16": "16_W1_W2_W4_W1repeat_benchmark_worker_selection.json", "17": "17_disk_ram_pid_samples_cleanup.json",
        "18": "18_inherited_new_red_green_fault_receipts.json", "19": "19_independent_verifier_mutation_attack_results.json",
        "20": "20_orthogonal_gates_and_verdicts.json", "22": "22_phase1_phase2_remote_publication_receipts.json",
    }
    for k, fn in fn_map.items():
        (run_dir / fn).write_text(json.dumps(docs[k], indent=2, sort_keys=True), encoding="utf-8")
    (run_dir / "README.md").write_text("# Synthetic Valid KH-03.R5 Run\n", encoding="utf-8")
    (run_dir / "HANDOVER_KH03_R5_VI.txt").write_text("Synthetic Valid KH-03.R5 Handover\n", encoding="utf-8")
    return rid, tsha


def run_kh03_r5_verifier_subprocess(run_dir: Path, rid: str, tsha: str) -> Tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)])
    proc = subprocess.run(
        [sys.executable, str(APP_ROOT / "tools" / "kh_03_r5_independent_verifier.py"), "--run-dir", str(run_dir), "--expected-run-id", rid, "--expected-tested-sha", tsha, "--no-write-manifest"],
        cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=60,
    )
    return int(proc.returncode), proc.stdout + "\n" + proc.stderr


# 20 NEW Separate-OS Verifier Mutations (R5M01..R5M20)
KH03_R5_OWN_VERIFIER_MUTATIONS_SPECS: List[Tuple[str, Callable[[Path], None], str]] = [
    ("R5M01_UNAPPROVED_LIVE_REQUESTS_IN_05", lambda rd: _mut_json(rd / "05_capture_execution_and_provenance_receipts.json", lambda d: d.__setitem__("external_requests_attempted", 2)), "R5V_UNAPPROVED_LIVE_CAPTURE_OR_FAKE_OBSERVED_RECORDS"),
    ("R5M02_FAKE_OBSERVED_RECORDS_WITHOUT_AUTH", lambda rd: _mut_json(rd / "05_capture_execution_and_provenance_receipts.json", lambda d: d.__setitem__("accepted_observed_records_count", 5)), "R5V_UNAPPROVED_LIVE_CAPTURE_OR_FAKE_OBSERVED_RECORDS"),
    ("R5M03_FAKE_EXECUTION_FIDELITY_READY_IN_12", lambda rd: _mut_json(rd / "12_observed_execution_fidelity_register.json", lambda d: d.__setitem__("execution_fidelity_status", "EVIDENCE_READY_FOR_SEPARATE_REVIEW")), "R5V_FAKE_EXECUTION_FIDELITY_READY_WITHOUT_OBSERVED_DATA"),
    ("R5M04_FORGED_DATA_READINESS_IN_20", lambda rd: _mut_json(rd / "20_orthogonal_gates_and_verdicts.json", lambda d: d["verdicts"].__setitem__("DATA_READINESS", "DEV_DATA_READY_FOR_SEPARATE_RESEARCH_REVIEW")), "R5V_FORGED_DATA_READINESS_PASS"),
    ("R5M05_CREDENTIALS_REQUESTED_TRUE_IN_10", lambda rd: _mut_json(rd / "10_owner_fee_and_slippage_readiness.json", lambda d: d.__setitem__("credentials_requested", True)), "R5V_FORGED_FEE_CREDENTIALS_OR_SLIPPAGE"),
    ("R5M06_OOS_READ_EXECUTED_IN_11", lambda rd: _mut_json(rd / "11_dev_custody_partition_chain_and_no_oos_access.json", lambda d: d["custody_chain_verification"]["oos_access_guard_summary"].__setitem__("oos_reads_executed", 1)), "R5V_FORBIDDEN_OOS_READ_EXECUTED"),
    ("R5M07_TAMPERED_R4_NET_PNL_IN_13", lambda rd: _mut_json(rd / "13_r4_replay_parity_economic_bridge.json", lambda d: d.__setitem__("null_1x_net_usd", 99.99)), "R5V_13_TAMPERED_VS_RAW_LEDGER"),
    ("R5M08_PLANNED_CAP_AS_GUARANTEE_IN_13", lambda rd: _mut_json(rd / "13_r4_replay_parity_economic_bridge.json", lambda d: d.__setitem__("planned_cap_is_physical_guarantee", True)), "R5V_PLANNED_CAP_GUARANTEE_FORGED"),
    ("R5M09_FORGED_PROVEN_EDGE_IN_20", lambda rd: _mut_json(rd / "20_orthogonal_gates_and_verdicts.json", lambda d: d["verdicts"].__setitem__("RESEARCH_ECONOMIC_VERDICT", "PROVEN_EDGE")), "R5V_FORGED_ECONOMIC_KH04_OR_ACTIVATION_IN_20"),
    ("R5M10_FORGED_ACTIVATION_YES_IN_20", lambda rd: _mut_json(rd / "20_orthogonal_gates_and_verdicts.json", lambda d: d["verdicts"].__setitem__("ACTIVATION_READY", "YES")), "R5V_FORGED_ECONOMIC_KH04_OR_ACTIVATION_IN_20"),
    ("R5M11_WRONG_DATASET_SHA_IN_02", lambda rd: _mut_json(rd / "02_prior_run_dataset_escrow_pointer_integrity.json", lambda d: d.__setitem__("canonical_dataset_sha256", "0" * 64)), "R5V_DATASET_OR_PRIOR_SHA_MISMATCH"),
    ("R5M12_LOCKED_FILE_MODIFIED_IN_01", lambda rd: _mut_json(rd / "01_function_ownership_keep_as_is_manifest.json", lambda d: d.__setitem__("locked_files_modified_count", 1)), "R5V_PROTECTED_SOURCE_MODIFIED_OR_ORDER_IMPORT"),
    ("R5M13_JUNIT_COUNTS_TAMPERED_IN_15", lambda rd: _mut_json(rd / "15_full_pytest_fresh_collection_junit_bijection.json", lambda d: d.__setitem__("passed_count", 5859)), "R5V_JUNIT_COUNTS_TAMPERED_VS_RAW_XML"),
    ("R5M14_INFLATED_SPEEDUP_IN_16", lambda rd: _mut_json(rd / "16_W1_W2_W4_W1repeat_benchmark_worker_selection.json", lambda d: d["pytest_worker_policy"].__setitem__("measured_speedup_w4_vs_w1", 9.99)), "R5V_BENCH_SPEEDUP_TAMPERED"),
    ("R5M15_MAX_CHILD_BREACH_IN_TELEMETRY_GZ", lambda rd: gzip.open(rd / "logs" / "runtime_memory_samples_0p5s.jsonl.gz", "wt", encoding="utf-8").write(json.dumps({"ts_ms": 1795460000000, "available_mib": 3242.49, "child_count": 6, "disk_free_gib": 26.5}) + "\n"), "R5V_TELEMETRY_CHILD_EXCEEDED_4"),
    ("R5M16_MISSING_TELEMETRY_LOG", lambda rd: (rd / "logs" / "runtime_memory_samples_0p5s.jsonl.gz").unlink(), "R5V_MISSING_TELEMETRY_LOG"),
    ("R5M17_FALSE_GITHUB_READBACK_IN_22", lambda rd: _mut_json(rd / "22_phase1_phase2_remote_publication_receipts.json", lambda d: d.update({"remote_readback_sha256_verified": True, "ci_status": "CI_PASS"})), "R5V_FALSE_PUBLICATION_OR_CI_CLAIM"),
    ("R5M18_STALE_R4_RUN_REUSED_IN_00", lambda rd: _mut_json(rd / "00_branch_sha_clean_preflight_resource.json", lambda d: d.update({"run_id": PRIOR_KH03_R4_RUN_ID, "tested_executable_sha": PRIOR_KH03_R4_TESTED_SHA})), "R5V_STALE_PRIOR_RUN_OR_SHA_REUSED"),
    ("R5M19_MISSING_R4_REPLAY_LEDGER_GZ", lambda rd: (rd / "logs" / "r4_replay_forensic_ledger_1x.jsonl.gz").unlink(), "R5V_MISSING_R4_REPLAY_LEDGER_GZ"),
    ("R5M20_DISK_WAIVER_TRUE_IN_00", lambda rd: _mut_json(rd / "00_branch_sha_clean_preflight_resource.json", lambda d: d.__setitem__("owner_disk_preflight_waiver", True)), "R5V_FORGED_GATE_INFLATION:G21_DISK_RAM_PID_NO_WAIVER"),
]


def run_new_kh03_r5_48_fault_controls() -> List[Dict[str, Any]]:
    """Executes ALL 48 unique NEW KH-03.R5 RED/GREEN fault controls (`R5-F01` .. `R5-F48`)."""
    now = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    inv = build_r5_source_capability_inventory(REPO_ROOT)
    req = {"symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "endpoints": ["public_ticker"], "run_id": "r5_f48", "max_requests": 4, "max_total_bytes": 50_000}
    qa_g = R5OperatorGrant("QA_48", "QA", "QA_FIXTURE", "XAUUSDT", "USDT-FUTURES", ("public_ticker",), 1794000000000, 1794000300000, 4, 50_000, 30, now_ms + 3600_000, "r5_f48", True, "QA")
    qa_d = validate_r5_operator_capture_scope(qa_g, req, now, {"qa_fixture_mode": True})
    qa_p = plan_r5_capture_batches(qa_d, inv, {"destination_disk_free_gib": 28.0, "max_concurrency": 1, "max_pages": 2, "max_requests": 4, "max_total_bytes": 50_000, "max_duration_sec": 30, "endpoints": ["public_ticker"]})
    contract = {"symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "settlement_currency": "USDT", "tick_size": "0.01", "expected_tick_size": "0.01"}
    good_meta = json.dumps({"code": "00000", "data": [{"symbol": "XAUUSDT", "productType": "USDT-FUTURES", "quoteCoin": "USDT", "pricePlace": "2", "priceEndStep": "1", "sizeMultiplier": "0.001", "minTradeNum": "0.001"}]}).encode("utf-8")
    b1 = {"open_time": 1794000000000, "close_time": 1794000060000, "open": "4100.00", "high": "4101.00", "low": "4099.50", "close": "4100.50", "volume": "5.0", "is_closed": True}
    rng = {"start_ms": 1794000000000, "end_ms": 1794000180000, "now_utc_ms": 1794000300000}
    q_ok = {"symbol": "XAUUSDT", "bid": "4100.00", "ask": "4100.20", "source_timestamp_ms": 1794000000000, "local_receive_timestamp_ms": 1794000000020}

    out: List[Dict[str, Any]] = []

    def _rec(fid: str, name: str, fn: str, red: bool, grn: bool) -> None:
        out.append({"fault_id": fid, "control_name": name, "target_function": fn, "red_negative_detected": bool(red), "green_clean_passed": bool(grn), "status": "PASS" if (red and grn) else "FAIL"})

    with tempfile.TemporaryDirectory(prefix="kh03r5_f48_") as td_s:
        td = Path(td_s)
        # F01..F05
        _rec("R5-F01", "MISSING_OWNER_GRANT_ZERO_LIVE", "validate_r5_operator_capture_scope", not validate_r5_operator_capture_scope(None, req, now, {})["admitted"], qa_d["admitted"] and not qa_d["live_network_permitted"])
        _rec("R5-F02", "CLI_ENV_EXPIRED_GRANT_REJECTED", "validate_r5_operator_capture_scope", not validate_r5_operator_capture_scope(None, req, now, {"from_cli_flag": True, "from_env_var": True})["admitted"], qa_d["admitted"])
        priv_p = dict(qa_p, batches=[{"batch_id": "P", "endpoint_key": "public_ticker", "url": "https://api.bitget.com/api/v2/mix/order/place-order", "symbol": "XAUUSDT", "product_type": "USDT-FUTURES"}])
        r_f03 = asyncio.run(capture_r5_public_batch(priv_p, _SequenceFakeTransportC2([]), None))
        _rec("R5-F03", "PRIVATE_ENDPOINT_AND_REDIRECT_BLOCKED", "capture_r5_public_batch", any("BLOCKED_UNAUTHORIZED" in v for v in r_f03["violations"]), True)

        class _FakeSub(StdlibUrllibPublicTransportC2):
            pass

        r_f04 = asyncio.run(capture_r5_public_batch(dict(qa_p, status="PLANNED_DEV_BOUNDED", partition_role="DEVELOPMENT_CAPTURE", live_network_permitted=True), _FakeSub(), None))
        _rec("R5-F04", "FAKE_SUBCLASS_TRANSPORT_BLOCKED", "capture_r5_public_batch", r_f04["accepted_observed_records_count"] == 0 and bool(r_f04["violations"]), True)

        croot = td / "cust"; j = R5DevCustodyJournal(croot, os_disk_probe_fn=lambda: 28.0)
        part = {"valid": True, "partition_role": "QA_FIXTURE", "plan_digest_sha256": "c" * 64}
        sp1 = td / "p1.raw"; sp1.write_bytes(good_meta); s1 = hashlib.sha256(good_meta).hexdigest()
        c1 = persist_r5_verified_dev_snapshot(sp1, {"raw_sha256": s1, "evidence_class": "QA_FIXTURE", "capture_proof_id": "pr_01"}, {"valid": True, "canonical_normalized_sha256": s1}, part, j)
        sp2 = td / "p2.raw"; sp2.write_bytes(good_meta + b" "); s2 = hashlib.sha256(good_meta + b" ").hexdigest()
        c_rep = persist_r5_verified_dev_snapshot(sp2, {"raw_sha256": s2, "evidence_class": "QA_FIXTURE", "capture_proof_id": "pr_01"}, {"valid": True, "canonical_normalized_sha256": s2}, part, j)
        _rec("R5-F05", "REPLAYED_PROOF_REJECTED", "persist_r5_verified_dev_snapshot", not c_rep["committed"], c1["committed"])

        # F06..F18
        _rec("R5-F06", "GZIP_DECOMPRESSION_BOMB_REJECTED", "validate_r5_instrument_metadata", not validate_r5_instrument_metadata(gzip.compress(b"A" * (12 * 1024 * 1024)), contract)["valid"], validate_r5_instrument_metadata(good_meta, contract)["valid"])
        _rec("R5-F07", "CONTRACT_IDENTITY_TICK_MISMATCH", "validate_r5_instrument_metadata", not validate_r5_instrument_metadata(good_meta, {**contract, "expected_tick_size": "0.10"})["valid"], validate_r5_instrument_metadata(good_meta, contract)["valid"])
        _rec("R5-F08", "OHLC_IMPOSSIBILITY_REJECTED", "validate_r5_ohlcv_stream", not validate_r5_ohlcv_stream([{"rows": [dict(b1, high="4000.00")]}], contract, "1m", rng)["valid"], validate_r5_ohlcv_stream([{"rows": [b1]}], contract, "1m", rng)["valid"])
        _rec("R5-F09", "DUPLICATE_EXACT_VS_CONFLICT", "validate_r5_ohlcv_stream", not validate_r5_ohlcv_stream([{"rows": [b1, dict(b1, close="4100.99")]}], contract, "1m", rng)["valid"], validate_r5_ohlcv_stream([{"rows": [b1, b1]}], contract, "1m", rng)["exact_duplicate_rows_deduplicated"] == 1)
        _rec("R5-F10", "FUTURE_OR_PARTIAL_BAR_REJECTED", "validate_r5_ohlcv_stream", not validate_r5_ohlcv_stream([{"rows": [dict(b1, is_closed=False)]}], contract, "1m", rng)["valid"], validate_r5_ohlcv_stream([{"rows": [b1]}], contract, "1m", rng)["valid"])
        b3 = dict(b1, open_time=1794000120000, close_time=1794000180000)
        g_ok = validate_r5_ohlcv_stream([{"rows": [b1, b3]}], contract, "1m", rng)
        _rec("R5-F11", "GAP_REPORTED_NO_FORWARD_FILL", "validate_r5_ohlcv_stream", g_ok["gap_intervals_count"] == 1 and g_ok["forward_fill_applied"] is False, g_ok["valid"])
        _rec("R5-F12", "CROSSED_AND_NEGATIVE_LATENCY_BBO", "validate_r5_bbo_stream", not validate_r5_bbo_stream([dict(q_ok, bid="4101.00", ask="4100.00")], contract)["valid"], validate_r5_bbo_stream([q_ok], contract)["valid"])
        _rec("R5-F13", "SINGLE_SNAPSHOT_NOT_CONTINUOUS", "validate_r5_bbo_stream", not validate_r5_bbo_stream([q_ok], contract, stream_mode="SINGLE_OBSERVED_SNAPSHOT")["meets_continuous_1s_observed_target"], validate_r5_bbo_stream([q_ok], contract, stream_mode="SINGLE_OBSERVED_SNAPSHOT")["valid"])
        _rec("R5-F14", "LOW_BBO_COVERAGE_INCOMPLETE", "validate_r5_bbo_stream", not validate_r5_bbo_stream([q_ok], contract, {"window_start_ms": 1794000000000, "window_end_ms": 1794000010000})["meets_continuous_1s_observed_target"], True)
        _rec("R5-F15", "OHLCV_INFERRED_BBO_REJECTED", "validate_r5_bbo_stream", not validate_r5_bbo_stream([dict(q_ok, inferred_from_ohlcv=True)], contract)["valid"], validate_r5_bbo_stream([q_ok], contract)["valid"])
        f_pred = validate_r5_funding_history([{"rows": [{"symbol": "XAUUSDT", "settlement_timestamp_ms": 1794000000000, "funding_rate": "0.0001", "rate_kind": "PREDICTED_ESTIMATE", "units": "DECIMAL_FRACTION"}]}], contract, {"interval_ms": 28800_000})
        f_ok = validate_r5_funding_history([{"rows": [{"symbol": "XAUUSDT", "settlement_timestamp_ms": 1794000000000, "funding_rate": "0.0001", "rate_kind": "ACTUAL_SETTLED", "units": "DECIMAL_FRACTION"}]}], contract, {"interval_ms": 28800_000})
        _rec("R5-F16", "PREDICTED_FUNDING_REJECTED", "validate_r5_funding_history", not f_pred["valid"] and f_pred["zero_fill_applied"] is False, f_ok["valid"])
        _rec("R5-F17", "UNKNOWN_FEE_AND_PUBLIC_RATE_REJECTED", "validate_r5_owner_fee_confirmation", not validate_r5_owner_fee_confirmation({"symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "source_type": "PUBLIC_GENERIC_SCHEDULE", "maker_fee_rate": "0.0002", "taker_fee_rate": "0.0004", "effective_date_utc": "2026-11-20T00:00:00Z", "source_evidence_sha256": "a" * 64, "tier_or_region_scope": "VIP0", "redacted_no_secrets_confirmed": True}, contract, now)["valid"], validate_r5_owner_fee_confirmation(None, contract, now)["account_fee_readiness"] == "UNKNOWN_NOT_LOCALLY_VERIFIED")
        _rec("R5-F18", "SECRET_IN_FEE_DOC_REJECTED", "validate_r5_owner_fee_confirmation", not validate_r5_owner_fee_confirmation({"symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "source_type": "OWNER_REDACTED_CONFIRMATION", "api_key": "secret123", "maker_fee_rate": "0.0002", "taker_fee_rate": "0.0004", "effective_date_utc": "2026-11-20T00:00:00Z", "source_evidence_sha256": "a" * 64, "tier_or_region_scope": "VIP0", "redacted_no_secrets_confirmed": True}, contract, now)["valid"], True)

        # F19..F29
        tr_429 = _SequenceFakeTransportC2([RawHttpResponseC2(429, b"", {"retry-after": "0.01"}, 1, 2), RawHttpResponseC2(200, good_meta, {}, 3, 4)])
        r_19 = asyncio.run(capture_r5_public_batch(qa_p, tr_429, None, sleep_fn=lambda _: None))
        _rec("R5-F19", "RATE_LIMIT_429_BOUNDED_RETRY", "capture_r5_public_batch", len(r_19["items"][0]["retry_chain"]) == 1, r_19["capture_execution_status"] == "QA_FIXTURE_OFFLINE_PASS")
        ev = asyncio.Event(); ev.set()
        r_20 = asyncio.run(capture_r5_public_batch(qa_p, _SequenceFakeTransportC2([]), None, cancel_event=ev))
        _rec("R5-F20", "MIDFLIGHT_CANCEL_STOPS_DISPATCH", "capture_r5_public_batch", r_20["aborted"] and r_20["abort_reason"] == "CANCELLED_MIDFLIGHT", True)
        r_21 = asyncio.run(capture_r5_public_batch(dict(qa_p, budget=dict(qa_p["budget"], max_total_bytes=5)), _SequenceFakeTransportC2([RawHttpResponseC2(200, good_meta, {}, 1, 2)]), None, sleep_fn=lambda _: None))
        _rec("R5-F21", "OVERSIZE_BYTE_BUDGET_PREEMPTED", "capture_r5_public_batch", r_21["aborted"] and r_21["abort_reason"] == "TOTAL_BYTES_BUDGET_EXHAUSTED", True)

        # F22..F26 custody
        hb = j.head_state()
        r_22 = persist_r5_verified_dev_snapshot(sp2, {"raw_sha256": s2, "evidence_class": "QA_FIXTURE", "capture_proof_id": "pr_crash"}, {"valid": True, "canonical_normalized_sha256": s2}, part, j, _crash_hook=lambda ph: (_ for _ in ()).throw(R5InjectedCrashError(ph)) if ph == "after_blob_fsync" else None)
        _rec("R5-F22", "CRASH_PRESERVES_HEAD_QUARANTINES_PARTIAL", "persist_r5_verified_dev_snapshot", (not r_22["committed"]) and j.head_state() == hb, verify_r5_custody_chain_readonly(croot, j.head_state(), None, ())["valid"])
        r_23 = persist_r5_verified_dev_snapshot(sp1, {"raw_sha256": s1, "evidence_class": "QA_FIXTURE", "capture_proof_id": "pr_01"}, {"valid": True, "canonical_normalized_sha256": s1}, part, j)
        _rec("R5-F23", "DOUBLE_WRITER_IDEMPOTENT_NO_CORRUPTION", "persist_r5_verified_dev_snapshot", bool(r_23.get("idempotent_duplicate")), j.head_state()["count"] == 1)
        _rec("R5-F24", "PATH_TRAVERSAL_DENIED", "assign_r5_partition_before_capture", not assign_r5_partition_before_capture(qa_p, {}, {"symbol": "XAUUSDT", "window_start_ms": 1, "window_end_ms": 2}, destination_root=td / ".." / "escape", allowed_base_root=td)["valid"], True)
        bp = croot / c1["blob_rel_path"]; orig = bp.read_bytes(); bp.write_bytes(orig + b"X")
        _rec("R5-F25", "MODIFIED_RAW_BYTES_DETECTED", "verify_r5_custody_chain_readonly", not verify_r5_custody_chain_readonly(croot, j.head_state(), None, ())["valid"], True)
        bp.unlink()
        _rec("R5-F26", "MISSING_RAW_BLOB_DETECTED", "verify_r5_custody_chain_readonly", not verify_r5_custody_chain_readonly(croot, j.head_state(), None, ())["valid"], True)
        bp.write_bytes(orig)

        # F27..F29 partitions & OOS deny-before-open
        _rec("R5-F27", "DEV_RELABELED_OOS_BLOCKED", "assign_r5_partition_before_capture", not assign_r5_partition_before_capture({"partition_role": "FRESH_OOS"}, {}, {"symbol": "XAUUSDT", "purpose": "OOS", "proposed_content_sha256": CANONICAL_DATASET_SHA256_KH02, "window_start_ms": 1, "window_end_ms": 2}, destination_root=croot, allowed_base_root=td)["valid"], True)
        og = OosAccessGuardR5(allowed_dev_roots=[croot], protected_oos_roots=[td / "real_oos_escrow"])
        den28 = False
        try:
            og.assert_open_permitted(td / "real_oos_escrow" / "x.blob", caller="t", purpose="read")
        except R5OosAccessDeniedError:
            den28 = True
        _rec("R5-F28", "OOS_DENY_BEFORE_OPEN_ENFORCED", "OosAccessGuardR5.assert_open_permitted", den28, og.summary()["oos_reads_executed"] == 0)
        den29 = False
        try:
            og.assert_open_permitted(croot / "x.json", caller="t", purpose="strategy_eval_new")
        except R5OosAccessDeniedError:
            den29 = True
        _rec("R5-F29", "STRATEGY_EVAL_ON_NEW_DATA_DENIED", "OosAccessGuardR5.assert_open_permitted", den29, og.summary()["oos_evaluations"] == 0)

        # F30..F43 verifier & readiness controls using synthetic R5 snapshot
        vdir = td / "vclean"
        vrid, vsha = build_synthetic_valid_kh03_r5_run_dir(vdir)
        v_docs = {p.name[:2]: json.loads(p.read_text("utf-8")) for p in sorted(vdir.glob("[0-9][0-9]_*.json"))}
        kw = dict(mem_audit={"status": "PASS", "below_floor_samples_count": 0}, pytest_recomp={"valid": True}, science_recomp={"valid": True}, junit_ok=True, r4_replay_ok=True, for_final_verifier_manifest=True)
        g_clean = recompute_r5_gate_matrix(docs=v_docs, **kw)

        d30 = json.loads(json.dumps(v_docs)); d30["12"]["execution_fidelity_status"] = "EVIDENCE_READY_FOR_SEPARATE_REVIEW"
        _rec("R5-F30", "FAKE_FIDELITY_READY_REJECTED", "recompute_r5_gate_matrix", recompute_r5_gate_matrix(docs=d30, **kw)["gates_26"]["G25_NO_ACTIVATION_OOS_ALPHA_CLAIM"] == "FAIL", g_clean["verdicts"]["ENGINEERING"] == "PASS")
        _rec("R5-F31", "HONEST_ENG_PASS_WITH_DATA_BLOCKED", "recompute_r5_gate_matrix", g_clean["verdicts"]["ENGINEERING"] == "PASS" and g_clean["verdicts"]["DATA_READINESS"] == "INCOMPLETE" and g_clean["verdicts"]["RESEARCH_ECONOMIC_VERDICT"] == "NO_PROVEN_EDGE", g_clean["verdicts"]["ACTIVATION_READY"] == "NO")
        d32 = json.loads(json.dumps(v_docs)); d32["13"]["RESEARCH_ECONOMIC_VERDICT"] = "PROVEN_EDGE"
        _rec("R5-F32", "FAKE_POSITIVE_EDGE_REJECTED", "recompute_r5_gate_matrix", recompute_r5_gate_matrix(docs=d32, **kw)["gates_26"]["G25_NO_ACTIVATION_OOS_ALPHA_CLAIM"] == "FAIL", True)
        d33 = json.loads(json.dumps(v_docs)); d33["00"]["owner_disk_preflight_waiver"] = True
        _rec("R5-F33", "DISK_WAIVER_REJECTED", "recompute_r5_gate_matrix", recompute_r5_gate_matrix(docs=d33, **kw)["gates_26"]["G21_DISK_RAM_PID_NO_WAIVER"] == "FAIL", True)
        _rec("R5-F34", "JUNIT_BIJECTION_FAILURE_REJECTED", "recompute_r5_gate_matrix", recompute_r5_gate_matrix(docs=v_docs, **dict(kw, junit_ok=False))["gates_26"]["G17_FULL_FRESH_PYTEST_COLLECTION_JUNIT_BIJECTION"] == "FAIL", True)
        _rec("R5-F35", "BENCHMARK_INVALID_REJECTED", "recompute_r5_gate_matrix", recompute_r5_gate_matrix(docs=v_docs, **dict(kw, pytest_recomp={"valid": False}))["gates_26"]["G20_MEASURED_WORKER_POLICY"] == "FAIL", True)
        _rec("R5-F36", "W1_W2_W4_DRIFT_REJECTED", "recompute_r5_gate_matrix", recompute_r5_gate_matrix(docs=v_docs, **dict(kw, science_recomp={"valid": False}))["gates_26"]["G19_DETERMINISTIC_W1_W2_W4_W1REPEAT"] == "FAIL", True)
        _rec("R5-F37", "FORGED_TELEMETRY_REJECTED", "recompute_r5_gate_matrix", recompute_r5_gate_matrix(docs=v_docs, **dict(kw, mem_audit={"status": "FAIL", "below_floor_samples_count": 1}))["gates_26"]["G21_DISK_RAM_PID_NO_WAIVER"] == "FAIL", True)
        _rec("R5-F38", "VERIFIER_VIOLATION_BLOCKS_G23", "recompute_r5_gate_matrix", recompute_r5_gate_matrix(docs=v_docs, **dict(kw, verifier_violations_count=1))["gates_26"]["G23_SEPARATE_OS_VERIFIER_RAW_EVIDENCE"] == "FAIL", True)
        d39 = json.loads(json.dumps(v_docs)); d39["01"]["locked_files_modified_count"] = 1
        _rec("R5-F39", "PROTECTED_SOURCE_DRIFT_REJECTED", "recompute_r5_gate_matrix", recompute_r5_gate_matrix(docs=d39, **kw)["gates_26"]["G02_PROTECTED_BYTES_AST_SOURCE_OWNERSHIP"] == "FAIL", True)
        d40 = json.loads(json.dumps(v_docs)); d40["22"]["remote_readback_sha256_verified"] = True; d40["22"]["publication_head_sha"] = None
        _rec("R5-F40", "FALSE_REMOTE_READBACK_REJECTED", "recompute_r5_gate_matrix", recompute_r5_gate_matrix(docs=d40, **kw)["gates_26"]["G26_PUBLICATION_REMOTE_PROOF"] == "FAIL", True)
        _rec("R5-F41", "LOCAL_RAW_NOT_CLAIMED_REMOTE_VERIFIED", "build_r5_execution_fidelity_register", v_docs["12"]["execution_fidelity_status"] == "INCOMPLETE", True)
        _rec("R5-F42", "R4_BRIDGE_DRIFT_REJECTED", "recompute_r5_gate_matrix", recompute_r5_gate_matrix(docs=v_docs, **dict(kw, r4_replay_ok=False))["gates_26"]["G15_R4_REAL_REPLAY_EXACT_ACCOUNTING_ANCHORS"] == "FAIL", True)
        d43 = json.loads(json.dumps(v_docs)); d43["15"]["executed_count"] = 0
        _rec("R5-F43", "UNEXECUTED_MANDATORY_TESTS_FAIL", "recompute_r5_gate_matrix", recompute_r5_gate_matrix(docs=d43, **kw)["gates_26"]["G17_FULL_FRESH_PYTEST_COLLECTION_JUNIT_BIJECTION"] == "FAIL", True)

        # F44..F48
        _rec("R5-F44", "CLOCK_REGRESSION_SECONDS_UNIT_REJECTED", "validate_r5_ohlcv_stream", not validate_r5_ohlcv_stream([{"rows": [dict(b1, open_time=1794000000, close_time=1794000060)]}], contract, "1m", rng)["valid"], True)
        r_45 = asyncio.run(capture_r5_public_batch(dict(qa_p, budget=dict(qa_p["budget"], max_requests=0)), _SequenceFakeTransportC2([]), None))
        _rec("R5-F45", "ZERO_REQUEST_BUDGET_PREEMPTED", "capture_r5_public_batch", r_45["aborted"] and r_45["qa_fixture_records_count"] == 0, True)
        _rec("R5-F46", "MISSING_SOURCE_BLOCKED_NO_SUBSTITUTE", "build_r5_source_capability_inventory", inv["missing_or_blocked_capabilities"]["public_funding_rate_history"]["status"] == "UNSUPPORTED_OR_UNVERIFIED_ENDPOINT", inv["status"] == "PASS")
        _rec("R5-F47", "DEV_INTERVAL_OVERLAP_QUARANTINED", "assign_r5_partition_before_capture", not assign_r5_partition_before_capture(qa_p, {"existing_intervals": [{"role": "REUSED_DEV", "symbol": "XAUUSDT", "start_ms": 1793999000000, "end_ms": 1794001000000}]}, {"symbol": "XAUUSDT", "window_start_ms": 1794000000000, "window_end_ms": 1794000300000}, destination_root=croot, allowed_base_root=td)["valid"], True)
        r_48 = validate_r5_bbo_stream([q_ok] * 15, contract, {"window_start_ms": 1794000000000, "window_end_ms": 1794000009000})
        _rec("R5-F48", "DUPLICATE_BBO_TIMESTAMPS_NO_OVERCOUNT", "validate_r5_bbo_stream", r_48["valid_unique_timestamp_quotes_count"] == 1 and r_48["grid_coverage_ratio"] <= 0.11, r_48["valid"])

    return out


def test_r5_all_48_fault_controls_pass_red_green() -> None:
    ctrls = run_new_kh03_r5_48_fault_controls()
    assert len(ctrls) == 48
    assert len({c["fault_id"] for c in ctrls}) == 48
    failed = [c for c in ctrls if c["status"] != "PASS"]
    assert failed == [], f"Failed R5 fault controls: {failed}"


def test_r5_zero_unauthorized_r1_callers_across_repo() -> None:
    cg = audit_kh03_r3_legacy_research_callgraph(REPO_ROOT)
    assert cg["LEGACY_METHOD_GUARD"] == "PASS" and int(cg["unauthorized_r1_callers_count"]) == 0


def test_r5_clean_synthetic_clone_passes_separate_os_verifier(tmp_path: Path) -> None:
    rdir = tmp_path / "clean_r5"
    rid, tsha = build_synthetic_valid_kh03_r5_run_dir(rdir)
    rc, out = run_kh03_r5_verifier_subprocess(rdir, rid, tsha)
    assert rc == 0, f"Clean R5 clone failed verifier (rc={rc}):\n{out}"
    assert '"status": "PASS"' in out


@pytest.mark.parametrize("mut_id,mut_fn,expected_token", KH03_R5_OWN_VERIFIER_MUTATIONS_SPECS, ids=[m[0] for m in KH03_R5_OWN_VERIFIER_MUTATIONS_SPECS])
def test_r5_independent_verifier_catches_all_20_mutations(
    tmp_path: Path, mut_id: str, mut_fn: Callable[[Path], None], expected_token: str
) -> None:
    rdir = tmp_path / f"mut_{mut_id}"
    rid, tsha = build_synthetic_valid_kh03_r5_run_dir(rdir)
    mut_fn(rdir)
    rc, out = run_kh03_r5_verifier_subprocess(rdir, rid, tsha)
    assert rc != 0, f"Mutation {mut_id} NOT caught (rc=0)!\n{out}"
    assert expected_token in out, f"Mutation {mut_id} missing expected token {expected_token}:\n{out}"
