"""
aurum-desk/backend/tests/test_kh_02_r2_c3_backtest_parity.py

AURUM DESK — KH-02.R2.C3 Integration & Independent Verifier Tests (Attacks 23-28):
  - CLI/env/API disk waiver strictly rejected (exit code 2 / RuntimeError).
  - Full 102/102 unique RED/GREEN fault suite (42 R2 + 14 C1 + 18 C2 + 28 C3).
  - Separate-OS-process `tools/kh_02_r2_c3_independent_verifier.py` GREEN on clean full-pass
    and clean blocked-low-disk runs (exit 0 / exit 2) PLUS 12 adversarial real-byte tamper
    RED controls (exit 1) executed concurrently with `max_workers=3` (global <= 4 descendants!).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Callable, Dict, Tuple

import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_prospective_data_service import (
    R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS,
    R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
    R2_AUTHORITATIVE_FROZEN_CODE_SHA,
    R2_AUTHORITATIVE_FROZEN_CONFIG_SHA256,
)
from services.kh_02_r2_research_integrity_service import (
    ACTIVATION_READY_KH02_R2,
    MANDATORY_BRANCH_KH02_R2,
    RUNTIME_SAFETY_MODES_KH02_R2,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tools.kh_02_r2_c3_independent_verifier import (
    SPEC_VERSION_KH02_R2_C3,
    recompute_c3_17_gates_and_17_verdicts,
)
from tools.run_kh_02_r2_c3_acceptance import (
    _run_c3_102_red_green_fault_suite,
    run_kh02_r2_c3_acceptance,
)

VERIFIER_SCRIPT = APP_ROOT / "tools" / "kh_02_r2_c3_independent_verifier.py"
ORCH_SCRIPT = APP_ROOT / "tools" / "run_kh_02_r2_c3_acceptance.py"


def _write_synthetic_c3_run_dir(
    run_dir: Path,
    *,
    blocked_low_disk: bool = False,
    orch_pid: int = 999993,
    tested_sha: str = "e" * 40,
) -> Tuple[str, str]:
    run_id = f"20261122T123000Z_{tested_sha[:12]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    with gzip.open(run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz", "wt", encoding="utf-8") as gz:
        gz.write(json.dumps({"available_ram_bytes": 1800 * 1024 * 1024, "child_count": 2, "worker_pids": [101, 102]}) + "\n")

    free_gib = 8.0 if blocked_low_disk else 36.0
    proj_gib = 7.9 if blocked_low_disk else 35.9
    disk_st = "BLOCKED_LOW_DISK" if blocked_low_disk else "PASS_STRICT_AND_ADVISORY"
    heavy_st = "NOT_RUN_BLOCKED_LOW_DISK" if blocked_low_disk else "PASS"
    pub_st = "NOT_PUBLISHED_BLOCKED_LOW_DISK" if blocked_low_disk else "PRE_PUSH_PENDING"

    if not blocked_low_disk:
        junit_dir = run_dir / "junit"
        junit_dir.mkdir(parents=True, exist_ok=True)
        xml_p = junit_dir / "SHARD_01.xml"
        xml_p.write_text(
            '<?xml version="1.0" encoding="utf-8"?>'
            '<testsuite name="pytest" tests="1" errors="0" failures="0" skipped="0">'
            '<testcase classname="backend.tests.test_kh_02_r2_c3_provenance_boundary" name="test_ok" time="0.01"/>'
            "</testsuite>\n",
            encoding="utf-8",
        )
        xml_sha = hashlib.sha256(xml_p.read_bytes()).hexdigest()
        nid = "backend/tests/test_kh_02_r2_c3_provenance_boundary.py::test_ok"
        f15 = {
            "status": "PASS",
            "collected_count": 1,
            "executed_count": 1,
            "passed_count": 1,
            "skipped_count": 0,
            "mandatory_skipped_count": 0,
            "failed_count": 0,
            "error_count": 0,
            "junit_bijection_exact": True,
            "collection_manifest": {"run_id": run_id, "tested_executable_sha": tested_sha, "collect_exit_code": 0, "collected_node_ids_count": 1, "collected_node_ids": [nid]},
            "shard_manifest": {"run_id": run_id, "tested_executable_sha": tested_sha, "nonzero_exit_shards_count": 0, "shards": [{"shard_id": "SHARD_01", "shard_name": "SHARD_01", "run_id": run_id, "tested_executable_sha": tested_sha, "reused": False, "pid": 12345, "exit_code": 0, "timed_out": False, "junit_xml_path": "junit/SHARD_01.xml", "xml_sha256": xml_sha, "scheduled_node_ids": [nid]}]},
        }
    else:
        f15 = {"status": "NOT_RUN_BLOCKED_LOW_DISK", "collected_count": 0, "executed_count": 0, "passed_count": 0, "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0, "junit_bijection_exact": False}

    h_p = "1" * 64
    h_s = "2" * 64
    docs: Dict[str, Dict[str, Any]] = {
        "00": {"spec_version": SPEC_VERSION_KH02_R2_C3, "run_id": run_id, "branch": MANDATORY_BRANCH_KH02_R2, "start_head_sha": tested_sha, "end_head_sha": tested_sha, "tested_executable_sha": tested_sha, "git_clean_at_start": True, "local_uncommitted_c3_changes": False, "orchestrator_pid": orch_pid, "owner_disk_preflight_waiver": False, "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2), "ACTIVATION_READY": ACTIVATION_READY_KH02_R2},
        "01": {"status": "PASS", "locked_files_modified_count": 0, "forbidden_order_import_hits": [], "cost_assumptions_defaults_intact": True, "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2), "ACTIVATION_READY": ACTIVATION_READY_KH02_R2},
        "02": {"owner_disk_preflight_waiver": False, "owner_waiver_permitted": False, "strict_multi_mount_probe": {"owner_waiver_permitted": False, "can_run_heavy_stages": not blocked_low_disk, "min_free_gib": free_gib, "status": disk_st, "probe_errors": [], "distinct_mounts": [{"free_gib": free_gib, "projected_post_reserve_gib": proj_gib}]}},
        "03": {"status": "PASS", "failed_controls": 0, "total_controls": 9},
        "04": {"authoritative_r2_freeze_audit": {"status": "PASS", "observed_freeze_coordinates": {"frozen_candidate_code_sha": R2_AUTHORITATIVE_FROZEN_CODE_SHA, "frozen_config_sha256": R2_AUTHORITATIVE_FROZEN_CONFIG_SHA256, "freeze_timestamp_ms": R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS, "embargo_cutoff_ms": R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS}}, "synthetic_20_oos_validator_status": "PASS", "OOS_EVALUATIONS": 0, "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2), "ACTIVATION_READY": ACTIVATION_READY_KH02_R2},
        "05": {"status": "PASS", "failed_attacks": 0, "fake_transport_blocked_from_real_oos": True, "stripped_synthetic_blocked_from_real_oos": True, "metadata_only_blocked_from_real_oos": True},
        "06": {"status": "PASS", "failed_checks": 0, "raw_bytes_reparse_verified": True, "parsed_forgery_rejected": True, "event_timestamp_tamper_rejected": True},
        "07": {"status": "PASS", "failed_cases": 0, "total_cases": 20, "passed_cases": 20, "live_network_capture_executed": False, "real_prospective_snapshots_collected": 0, "live_network_test_status": "LIVE_NETWORK_TEST_NOT_RUN_BY_DESIGN", "redirect_302_blocked": True, "real_default_sleep_verified": True, "forged_caller_disk_probe_blocked": True, "gzip_bomb_and_oversized_blocked": True, "default_collector_readiness": {"read_only": True, "network_capture_enabled": False}},
        "08": {"status": "PASS", "prior_journal_bytes_untouched_verified": True, "concurrent_writers_2_4_8_exact_once_passed": True, "interprocess_flock_passed": True},
        "09": {"status": "PASS", "crash_recovery_and_double_quarantine_passed": True, "unanchored_pending_attestation_detected": True, "unanchored_immutability_class": "LOCAL_TAMPER_EVIDENT_ONLY", "unanchored_worm_verified": False, "anchored_rollback_detected": True},
        "10": {"status": "PASS", "swept_tp_accepted_count": 0, "t01_to_t10_all_passed": True, "future_mutation_invariance_passed": True, "live_causal_ledger_recomputed": True},
        "11": {"status": "PASS", "b0_vs_b2_identifiability_status": "COMPARISON_NOT_IDENTIFIABLE", "matching_inadequate_guard_verified": True},
        "12": {"status": "PASS", "fresh_live_historical_replay_executed": True, "exact_match_with_sealed_r2_baseline": True, "fill_time_cap_violations": 0},
        "13": {"status": "PASS", "fixed_fill_zero_dropped_ids_verified": True, "full_rerisk_starts_from_full_universe": True, "realized_tail_risk_disclosed": True, "quote_mode": "MODELED_BID_ASK", "observed_l1_bbo_available": False, "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED", "fixed_fill_decimal_checks": {"B2_H1_1x": {"gross_pnl_usd": 100.0, "fees_usd": 20.0, "net_pnl_usd": 80.0}}},
        "14": {"status": "PASS", "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE", "FUTURE_OOS_READINESS_VERDICT": "BLOCKED_NO_VERIFIED_NEW_DATA", "OOS_EVALUATIONS": 0, "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2), "ACTIVATION_READY": ACTIVATION_READY_KH02_R2},
        "15": f15,
        "16": {"status": heavy_st, "all_determinism_identical": not blocked_low_disk, "pytest_controlled_subset_w1_w2_w4_repeat": {"all_4_runs_identical": not blocked_low_disk, "runs": [{"canonical_sorted_outcome_sha256": h_p, "executed_tests_count": 155}] * 4}, "r2_science_slice_w1_w2_w4_repeat": {"all_4_runs_identical": not blocked_low_disk, "runs": [{"canonical_sorted_sha256": h_s, "rows_count": 12062}] * 4}},
        "17": {"prior_evidence_immutability_verified": True, "prior_sealed_artifacts_immutability": {"KH02_R2_C2_MANIFEST_17": {"match": True}}, "BASELINE_PARITY_VERDICT": heavy_st, "a1_population_count": 56710, "a1_mismatches_count": 0, "si8_population_count": 6375, "si8_mismatches_count": 0, "three_adapter_population_count": 246, "three_adapter_mismatches_count": 0, "d4_historical_closed_trades": 0, "per_decision_and_episode_fingerprints": {"all_sealed_fingerprints_exact_match": not blocked_low_disk}},
        "18": {"min_available_ram_mib": 1800.0, "min_disk_free_gib": free_gib, "max_descendant_children": 2, "orphan_children_after_cleanup": 0, "sentinel_temp_cleaned_up": True, "owner_disk_preflight_waiver": False},
        "19": {"status": "PASS", "total_controls": 102, "failed_controls": 0, "unique_fault_ids": 102},
        "22": {"run_id": run_id, "tested_executable_sha": tested_sha, "evidence_commit_sha": None, "remote_readback_sha256_verified": False, "latest_run_pointer_updated": False, "owner_disk_preflight_waiver": False, "publication_status": pub_st},
    }
    mem_ok = {"status": "PASS", "below_floor_samples_count": 0, "max_workers_observed_in_samples": 2}
    recomp = recompute_c3_17_gates_and_17_verdicts(docs=docs, mem_audit=mem_ok, junit_bijection_ok=not blocked_low_disk)
    docs["21"] = {"run_id": run_id, "tested_executable_sha": tested_sha, "OOS_EVALUATIONS": 0, "ACTIVATION_READY": ACTIVATION_READY_KH02_R2, "gates_17": recomp["gates_17"], "verdicts_17": recomp["verdicts_17"]}
    docs["21"]["artifact_canonical_sha256"] = canonical_hash(docs["21"])

    fname_map = {
        "00": "00_git_branch_tested_sha_remote_preflight.json",
        "01": "01_function_ownership_ast_keep_as_is_locked_diff.json",
        "02": "02_distinct_mount_disk_ram_preflight_readonly_inventory.json",
        "03": "03_disk_waiver_and_runtime_abort_controls.json",
        "04": "04_freeze_registry_raw_provenance_contract.json",
        "05": "05_fake_live_boundary_forgery_rejections.json",
        "06": "06_raw_bytes_reparse_checksum_admission.json",
        "07": "07_http_allowlist_redirect_rate_limit_retries.json",
        "08": "08_escrow_concurrent_writers_hash_integrity.json",
        "09": "09_escrow_torn_write_crash_quarantine_anchor_truth.json",
        "10": "10_unswept_tp_future_mutation_regression.json",
        "11": "11_entry_geometry_matching_common_support.json",
        "12": "12_replay_backtest_r1_r2_strata_risk_fees.json",
        "13": "13_cost_fixed_fill_vs_full_rerisk_1x_2x_3x.json",
        "14": "14_research_uncertainty_economic_oos_verdict.json",
        "15": "15_full_pytest_collection_execution_junit_bijection.json",
        "16": "16_async_w1_w2_w4_w1repeat_hash_timing_ram.json",
        "17": "17_full_a1_si8_adapter_d4_parity.json",
        "18": "18_resource_runtime_telemetry_memory_disk_pid.json",
        "19": "19_fault_injection_red_green_unique_receipts.json",
        "21": "21_orthogonal_science_engineering_gate_matrix.json",
        "22": "22_publication_tested_sha_prepush_receipt.json",
    }
    for k, fn in fname_map.items():
        (run_dir / fn).write_text(json.dumps(docs[k], indent=2, sort_keys=True), encoding="utf-8")
    (run_dir / "README_KH02_R2_C3.md").write_text(f"# KH-02.R2.C3 {run_id}\nACTIVATION_READY=NO\nNO_PROVEN_EDGE\n", encoding="utf-8")
    (run_dir / "HANDOVER_KH02_R2_C3_VI.txt").write_text(f"Run {run_id}\nACTIVATION_READY=NO\nNO_PROVEN_EDGE\n", encoding="utf-8")
    return run_id, tested_sha


def _run_c3_verifier_subproc(run_dir: Path, run_id: str, tested_sha: str) -> Tuple[int, Dict[str, Any]]:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{BACKEND_ROOT}:{APP_ROOT}:{REPO_ROOT}"
    proc = subprocess.run(
        [sys.executable, str(VERIFIER_SCRIPT), "--run-dir", str(run_dir), "--expected-run-id", run_id, "--expected-tested-sha", tested_sha, "--skip-live-repo-pointers", "--no-write-manifest"],
        cwd=str(APP_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return int(proc.returncode), (json.loads(proc.stdout) if proc.stdout.strip() else {})


def test_c3_cli_env_api_disk_waiver_rejected() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{BACKEND_ROOT}:{APP_ROOT}:{REPO_ROOT}"
    p_cli = subprocess.run([sys.executable, str(ORCH_SCRIPT), "--owner-waive-disk-preflight"], cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=30)
    assert p_cli.returncode == 2
    p_env = subprocess.run([sys.executable, str(ORCH_SCRIPT)], cwd=str(APP_ROOT), env=dict(env, KH02_OWNER_WAIVE_DISK_PREFLIGHT="1"), capture_output=True, text=True, timeout=30)
    assert p_env.returncode == 2
    with pytest.raises(RuntimeError, match="UNAPPROVED_AUTONOMOUS_DISK_WAIVER_FORBIDDEN"):
        run_kh02_r2_c3_acceptance(owner_waive_disk_preflight=True)


def test_c3_102_unique_red_green_fault_suite_passes(tmp_path: Path) -> None:
    res = _run_c3_102_red_green_fault_suite(REPO_ROOT, tmp_path / "faults_102")
    assert res["status"] == "PASS", [c for c in res["controls"] if c["status"] != "PASS"]
    assert res["total_controls"] >= 102
    assert res["unique_fault_ids"] == res["total_controls"]
    assert res["failed_controls"] == 0


def test_c3_separate_os_verifier_green_blocked_and_12_adversarial_tampers(tmp_path: Path) -> None:
    # 1. Clean full-pass synthetic run -> exit 0, PASS_ENGINEERING_RESEARCH_NO_EDGE
    good_dir = tmp_path / "good_c3"
    rid_g, sha_g = _write_synthetic_c3_run_dir(good_dir, blocked_low_disk=False)
    rc_g, pay_g = _run_c3_verifier_subproc(good_dir, rid_g, sha_g)
    assert rc_g == 0, pay_g
    assert pay_g["independent_verifier_status"] == "PASS"
    assert pay_g["overall_classification"] == "PASS_ENGINEERING_RESEARCH_NO_EDGE"
    assert pay_g["recomputed_17_verdicts"]["ENGINEERING"] == "PASS"
    assert pay_g["recomputed_17_verdicts"]["ECONOMIC"] == "NO_PROVEN_EDGE"
    assert pay_g["recomputed_17_verdicts"]["OOS_READINESS"] == "BLOCKED_NO_VERIFIED_NEW_DATA"

    # 2. Clean blocked-low-disk synthetic run -> exit 2 (NEVER normalized to 0!)
    blk_dir = tmp_path / "blk_c3"
    rid_b, sha_b = _write_synthetic_c3_run_dir(blk_dir, blocked_low_disk=True)
    rc_b, pay_b = _run_c3_verifier_subproc(blk_dir, rid_b, sha_b)
    assert rc_b == 2, pay_b
    assert pay_b["recomputed_17_verdicts"]["ENGINEERING"] == "BLOCKED_LOW_DISK"

    # 3. 12 real-byte adversarial tamper RED controls -> ALL must exit 1 (max_workers=3 -> global <= 4!)
    def _ed(d: Path, fn: str, mut: Callable[[Dict[str, Any]], None]) -> None:
        p = d / fn
        obj = json.loads(p.read_text("utf-8"))
        mut(obj)
        p.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")

    tampers: list[tuple[str, Callable[[Path], None], str]] = [
        ("AT01_JUNIT_COUNT_TAMPER", lambda d: _ed(d, "15_full_pytest_collection_execution_junit_bijection.json", lambda o: o.__setitem__("passed_count", 99)), "JUNIT_COUNTS_TAMPERED_VS_RAW_XML"),
        ("AT02_W1_W4_HASH_MUTATION", lambda d: _ed(d, "16_async_w1_w2_w4_w1repeat_hash_timing_ram.json", lambda o: o["r2_science_slice_w1_w2_w4_repeat"]["runs"][2].__setitem__("canonical_sorted_sha256", "9" * 64)), "W1_W2_W4_DETERMINISM_HASH_MISMATCH"),
        ("AT03_DECIMAL_PNL_TAMPER", lambda d: _ed(d, "13_cost_fixed_fill_vs_full_rerisk_1x_2x_3x.json", lambda o: o["fixed_fill_decimal_checks"]["B2_H1_1x"].__setitem__("net_pnl_usd", 999.0)), "FIXED_FILL_DECIMAL_MISMATCH"),
        ("AT04_PREPUSH_FALSE_REMOTE", lambda d: _ed(d, "22_publication_tested_sha_prepush_receipt.json", lambda o: o.update({"publication_status": "PASS_REMOTE_VERIFIED", "remote_readback_sha256_verified": False})), "FALSE_REMOTE_READBACK_CLAIM"),
        ("AT05_OOS_EVAL_NONZERO", lambda d: _ed(d, "14_research_uncertainty_economic_oos_verdict.json", lambda o: o.__setitem__("OOS_EVALUATIONS", 1)), "NONZERO_OOS_EVALUATIONS_PEEK"),
        ("AT06_ACTIVATION_TURNED_ON", lambda d: _ed(d, "14_research_uncertainty_economic_oos_verdict.json", lambda o: o.__setitem__("ACTIVATION_READY", "YES")), "FORGED_ACTIVATION_READY_ON"),
        ("AT07_FORGED_PROVEN_EDGE", lambda d: _ed(d, "14_research_uncertainty_economic_oos_verdict.json", lambda o: o.__setitem__("RESEARCH_ECONOMIC_VERDICT", "PROVEN_EDGE")), "FORGED_ECONOMIC_VERDICT"),
        ("AT08_FALSE_WORM_CLAIM", lambda d: _ed(d, "09_escrow_torn_write_crash_quarantine_anchor_truth.json", lambda o: o.__setitem__("unanchored_worm_verified", True)), "FALSE_WORM_CLAIM"),
        ("AT09_OWNER_DISK_WAIVER", lambda d: _ed(d, "02_distinct_mount_disk_ram_preflight_readonly_inventory.json", lambda o: o.__setitem__("owner_disk_preflight_waiver", True)), "UNAPPROVED_OWNER_DISK_WAIVER_FORBIDDEN"),
        ("AT10_RAM_FLOOR_BREACH", lambda d: gzip.open(d / "logs" / "runtime_memory_samples_0p5s.jsonl.gz", "wt", encoding="utf-8").write(json.dumps({"available_ram_bytes": 200 * 1024 * 1024, "child_count": 1}) + "\n"), "MEMORY_TELEMETRY_BREACHED_512MIB_FLOOR"),
        ("AT11_MUTATED_C2_MANIFEST", lambda d: _ed(d, "17_full_a1_si8_adapter_d4_parity.json", lambda o: o["prior_sealed_artifacts_immutability"]["KH02_R2_C2_MANIFEST_17"].__setitem__("match", False)), "PRIOR_SEALED_ARTIFACT_MUTATED"),
        ("AT12_GATE_MATRIX_TAMPER", lambda d: _ed(d, "21_orthogonal_science_engineering_gate_matrix.json", lambda o: o["gates_17"].__setitem__("G16_ECONOMIC_TRUTH_NO_EDGE", "PROVEN_EDGE")), "GATE_TAMPER_OR_DRIFT"),
    ]

    def _run_t(item: tuple[str, Callable[[Path], None], str]) -> tuple[str, int, list[str], str]:
        nm, fn, tok = item
        td = tmp_path / f"t_{nm}"
        shutil.copytree(good_dir, td)
        fn(td)
        rc, pay = _run_c3_verifier_subproc(td, rid_g, sha_g)
        return nm, rc, list(pay.get("violations") or []), tok

    with ThreadPoolExecutor(max_workers=3) as pool:
        outs = list(pool.map(_run_t, tampers))

    for nm, rc, viols, tok in outs:
        assert rc == 1, f"{nm} expected exit 1, got {rc}: {viols}"
        assert any(tok in v for v in viols), f"{nm} missing {tok} in {viols}"
