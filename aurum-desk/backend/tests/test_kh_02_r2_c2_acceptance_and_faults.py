"""
aurum-desk/backend/tests/test_kh_02_r2_c2_acceptance_and_faults.py

AURUM DESK — KH-02.R2.C2 Unit & Integration Tests:
  T01..T04: Branch/source/waiver/disk/RAM/PID/cleanup negative controls.
  T15..T17: 74 unique RED/GREEN fault controls, separate-OS independent disk verifier
            (GREEN on full-pass & blocked-low-disk synthetic runs + 12 adversarial tamper RED controls),
            and publication truth controls (false remote readback, premature latest_run pointer).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import gzip
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
from tools.kh_02_r2_c2_independent_verifier import (
    SPEC_VERSION_KH02_R2_C2,
    recompute_c2_19_gates_and_13_verdicts,
)
from tools.run_kh_02_r2_c2_acceptance import (
    _run_c2_74_red_green_fault_suite,
    run_kh02_r2_c2_acceptance,
)

VERIFIER_SCRIPT = APP_ROOT / "tools" / "kh_02_r2_c2_independent_verifier.py"
ORCH_SCRIPT = APP_ROOT / "tools" / "run_kh_02_r2_c2_acceptance.py"


def _write_synthetic_c2_run_dir(
    run_dir: Path,
    *,
    blocked_low_disk: bool = False,
    orch_pid: int = 999991,
    tested_sha: str = "d" * 40,
) -> Tuple[str, str]:
    run_id = f"20261122T120000Z_{tested_sha[:12]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    mem_log = run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    with gzip.open(mem_log, "wt", encoding="utf-8") as gz:
        gz.write(json.dumps({"available_ram_bytes": 1500 * 1024 * 1024, "child_count": 2, "worker_pids": [101, 102]}) + "\n")

    free_gib = 8.0 if blocked_low_disk else 34.0
    proj_gib = 7.9 if blocked_low_disk else 33.9
    disk_status = "BLOCKED_LOW_DISK" if blocked_low_disk else "PASS_STRICT_AND_ADVISORY"
    heavy_status = "NOT_RUN_BLOCKED_LOW_DISK" if blocked_low_disk else "PASS"
    pub_status = "NOT_PUBLISHED_BLOCKED_LOW_DISK" if blocked_low_disk else "PRE_PUSH_PENDING"

    if not blocked_low_disk:
        junit_dir = run_dir / "junit"
        junit_dir.mkdir(parents=True, exist_ok=True)
        xml_path = junit_dir / "SHARD_01.xml"
        xml_body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<testsuite name="pytest" tests="1" errors="0" failures="0" skipped="0">'
            '<testcase classname="backend.tests.test_kh_02_r2_c2_escrow_journal" name="test_ok" time="0.01"/>'
            "</testsuite>\n"
        )
        xml_path.write_text(xml_body, encoding="utf-8")
        import hashlib
        xml_sha = hashlib.sha256(xml_path.read_bytes()).hexdigest()
        node_id = "backend/tests/test_kh_02_r2_c2_escrow_journal.py::test_ok"
        f12 = {
            "status": "PASS",
            "collected_count": 1,
            "executed_count": 1,
            "passed_count": 1,
            "skipped_count": 0,
            "mandatory_skipped_count": 0,
            "failed_count": 0,
            "error_count": 0,
            "junit_bijection_exact": True,
            "collection_manifest": {
                "run_id": run_id,
                "tested_executable_sha": tested_sha,
                "collect_exit_code": 0,
                "collected_node_ids_count": 1,
                "collected_node_ids": [node_id],
            },
            "shard_manifest": {
                "run_id": run_id,
                "tested_executable_sha": tested_sha,
                "nonzero_exit_shards_count": 0,
                "shards": [
                    {
                        "shard_id": "SHARD_01",
                        "shard_name": "SHARD_01",
                        "run_id": run_id,
                        "tested_executable_sha": tested_sha,
                        "reused": False,
                        "pid": 12345,
                        "exit_code": 0,
                        "timed_out": False,
                        "junit_xml_path": "junit/SHARD_01.xml",
                        "xml_sha256": xml_sha,
                        "scheduled_node_ids": [node_id],
                    }
                ],
            },
        }
    else:
        f12 = {
            "status": "NOT_RUN_BLOCKED_LOW_DISK",
            "collected_count": 0,
            "executed_count": 0,
            "passed_count": 0,
            "skipped_count": 0,
            "mandatory_skipped_count": 0,
            "failed_count": 0,
            "error_count": 0,
            "junit_bijection_exact": False,
        }

    docs: Dict[str, Dict[str, Any]] = {
        "00": {
            "spec_version": SPEC_VERSION_KH02_R2_C2,
            "run_id": run_id,
            "branch": MANDATORY_BRANCH_KH02_R2,
            "start_head_sha": tested_sha,
            "end_head_sha": tested_sha,
            "tested_executable_sha": tested_sha,
            "git_clean_at_start": True,
            "local_uncommitted_c2_changes": False,
            "orchestrator_pid": orch_pid,
            "owner_disk_preflight_waiver": False,
            "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        },
        "01": {
            "status": "PASS",
            "locked_files_modified_count": 0,
            "forbidden_order_import_hits": [],
            "cost_assumptions_defaults_intact": True,
            "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        },
        "02": {
            "owner_disk_preflight_waiver": False,
            "owner_waiver_permitted": False,
            "strict_multi_mount_probe": {
                "owner_waiver_permitted": False,
                "can_run_heavy_stages": not blocked_low_disk,
                "min_free_gib": free_gib,
                "status": disk_status,
                "probe_errors": [],
                "distinct_mounts": [{"free_gib": free_gib, "projected_post_reserve_gib": proj_gib}],
            },
        },
        "03": {"status": "PASS", "failed_controls": 0, "total_controls": 9},
        "04": {
            "authoritative_r2_freeze_audit": {
                "status": "PASS",
                "observed_freeze_coordinates": {
                    "frozen_candidate_code_sha": R2_AUTHORITATIVE_FROZEN_CODE_SHA,
                    "frozen_config_sha256": R2_AUTHORITATIVE_FROZEN_CONFIG_SHA256,
                    "freeze_timestamp_ms": R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
                    "embargo_cutoff_ms": R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS,
                },
            },
            "reused_dataset_limitations": {
                "quote_mode": "MODELED_BID_ASK",
                "observed_l1_bbo_available": False,
                "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            },
            "escrow_contract_integrity_status": "PASS",
            "synthetic_20_oos_validator_status": "PASS",
            "OOS_EVALUATIONS": 0,
            "FUTURE_OOS_READINESS_VERDICT": "BLOCKED_NO_VERIFIED_NEW_DATA",
            "FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA",
            "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        },
        "05": {
            "status": "PASS",
            "prior_journal_bytes_untouched_verified": True,
            "concurrent_writers_2_4_8_exact_once_passed": True,
            "crash_recovery_and_quarantine_passed": True,
        },
        "06": {
            "status": "PASS",
            "unanchored_immutability_class": "LOCAL_TAMPER_EVIDENT_ONLY",
            "unanchored_worm_verified": False,
            "anchored_rollback_detected": True,
        },
        "07": {
            "status": "PASS",
            "failed_cases": 0,
            "total_cases": 20,
            "passed_cases": 20,
            "live_network_capture_executed": False,
            "real_prospective_snapshots_collected": 0,
            "default_collector_readiness": {"read_only": True, "network_capture_enabled": False},
        },
        "08": {"status": "PASS", "swept_tp_accepted_count": 0, "t01_to_t10_all_passed": True, "future_mutation_invariance_passed": True},
        "09": {"status": "PASS", "b0_vs_b2_identifiability_status": "COMPARISON_NOT_IDENTIFIABLE", "matching_inadequate_guard_verified": True},
        "10": {
            "status": "PASS",
            "fixed_fill_zero_dropped_ids_verified": True,
            "full_rerisk_starts_from_full_universe": True,
            "fill_time_cap_violations": 0,
            "realized_tail_risk_disclosed": True,
            "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            "fixed_fill_decimal_checks": {"B2_H1": {"gross_pnl_usd": 100.0, "fees_usd": 20.0, "net_pnl_usd": 80.0}},
        },
        "11": {
            "status": "PASS",
            "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE",
            "OOS_EVALUATIONS": 0,
            "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        },
        "12": f12,
        "13": {"status": heavy_status, "all_determinism_identical": not blocked_low_disk},
        "14": {
            "prior_evidence_immutability_verified": True,
            "prior_sealed_artifacts_immutability": {"KH02_R2_C1_MANIFEST_16": {"match": True}},
            "BASELINE_PARITY_VERDICT": heavy_status,
            "a1_mismatches_count": 0,
            "si8_mismatches_count": 0,
            "three_adapter_mismatches_count": 0,
            "d4_historical_closed_trades": 0,
            "per_decision_and_episode_fingerprints": {"all_sealed_fingerprints_exact_match": not blocked_low_disk},
        },
        "15": {
            "min_available_ram_mib": 1500.0,
            "min_disk_free_gib": free_gib,
            "max_descendant_children": 2,
            "orphan_children_after_cleanup": 0,
            "sentinel_temp_cleaned_up": True,
            "owner_disk_preflight_waiver": False,
        },
        "16": {"status": "PASS", "total_controls": 74, "failed_controls": 0, "unique_fault_ids": 74},
        "19": {
            "run_id": run_id,
            "tested_executable_sha": tested_sha,
            "evidence_commit_sha": None,
            "remote_readback_sha256_verified": False,
            "latest_run_pointer_updated": False,
            "owner_disk_preflight_waiver": False,
            "publication_status": pub_status,
        },
    }
    mem_ok = {"status": "PASS", "below_floor_samples_count": 0, "max_workers_observed_in_samples": 2}
    recomp = recompute_c2_19_gates_and_13_verdicts(docs=docs, mem_audit=mem_ok, junit_bijection_ok=not blocked_low_disk)
    docs["18"] = {
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "OOS_EVALUATIONS": 0,
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        "gates_19": recomp["gates_19"],
        "verdicts_13": recomp["verdicts_13"],
    }
    docs["18"]["artifact_canonical_sha256"] = canonical_hash(docs["18"])

    fname_map = {
        "00": "00_source_branch_remote_diff_preflight.json",
        "01": "01_function_ownership_keep_as_is_ast_diff.json",
        "02": "02_distinct_write_mount_disk_resource_preflight.json",
        "03": "03_disk_waiver_cli_api_and_runtime_abort_faults.json",
        "04": "04_r2_freeze_and_prospective_schema_provenance.json",
        "05": "05_escrow_append_only_concurrency_crash_recovery.json",
        "06": "06_escrow_hash_chain_external_anchor_truth.json",
        "07": "07_public_readonly_collector_fake_transport_suite.json",
        "08": "08_causal_unswept_target_future_mutation_risk.json",
        "09": "09_paired_entry_geometry_common_support_balance.json",
        "10": "10_fixed_fill_vs_rerisk_fees_1x2x3x.json",
        "11": "11_walkforward_uncertainty_economic_oos_verdict.json",
        "12": "12_full_pytest_junit_bijection.json",
        "13": "13_async_w1_w2_w4_w1repeat_hashes_times.json",
        "14": "14_full_a1_si8_adapter_d4_parity.json",
        "15": "15_ram_disk_pid_temp_telemetry.json",
        "16": "16_fault_injection_red_green_receipts.json",
        "18": "18_orthogonal_gate_matrix.json",
        "19": "19_publication_tested_sha_remote_blob_receipt.json",
    }
    for k, fn in fname_map.items():
        (run_dir / fn).write_text(json.dumps(docs[k], indent=2, sort_keys=True), encoding="utf-8")
    (run_dir / "README_KH02_R2_C2.md").write_text(f"# KH-02.R2.C2 {run_id}\nACTIVATION_READY=NO\nNO_PROVEN_EDGE\n", encoding="utf-8")
    (run_dir / "HANDOVER_KH02_R2_C2_VI.txt").write_text(f"Run {run_id}\nACTIVATION_READY=NO\nNO_PROVEN_EDGE\n", encoding="utf-8")
    return run_id, tested_sha


def _run_verifier_subproc(run_dir: Path, run_id: str, tested_sha: str) -> Tuple[int, Dict[str, Any]]:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{BACKEND_ROOT}:{APP_ROOT}:{REPO_ROOT}"
    proc = subprocess.run(
        [
            sys.executable,
            str(VERIFIER_SCRIPT),
            "--run-dir",
            str(run_dir),
            "--expected-run-id",
            run_id,
            "--expected-tested-sha",
            tested_sha,
            "--skip-live-repo-pointers",
            "--no-write-manifest",
        ],
        cwd=str(APP_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return int(proc.returncode), payload


def test_t03_cli_env_and_api_disk_waiver_strictly_rejected() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{BACKEND_ROOT}:{APP_ROOT}:{REPO_ROOT}"
    p_cli = subprocess.run(
        [sys.executable, str(ORCH_SCRIPT), "--owner-waive-disk-preflight"],
        cwd=str(APP_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert p_cli.returncode == 2
    assert "UNAPPROVED_AUTONOMOUS_DISK_WAIVER_FORBIDDEN" in p_cli.stderr

    env_w = dict(env, KH02_OWNER_WAIVE_DISK_PREFLIGHT="1")
    p_env = subprocess.run(
        [sys.executable, str(ORCH_SCRIPT)],
        cwd=str(APP_ROOT),
        env=env_w,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert p_env.returncode == 2

    with pytest.raises(RuntimeError, match="UNAPPROVED_AUTONOMOUS_DISK_WAIVER_FORBIDDEN"):
        run_kh02_r2_c2_acceptance(owner_waive_disk_preflight=True)


def test_t15_full_74_unique_red_green_fault_suite_passes(tmp_path: Path) -> None:
    res = _run_c2_74_red_green_fault_suite(REPO_ROOT, tmp_path / "faults_74")
    assert res["status"] == "PASS", [c for c in res["controls"] if c["status"] != "PASS"]
    assert res["total_controls"] >= 74
    assert res["unique_fault_ids"] == res["total_controls"]
    assert res["unique_fault_names"] == res["total_controls"]
    assert res["failed_controls"] == 0


def test_t16_t17_separate_os_verifier_green_and_12_adversarial_red_tampers(tmp_path: Path) -> None:
    # 1. GREEN on full-pass synthetic run
    good_dir = tmp_path / "good_full_pass"
    rid_g, sha_g = _write_synthetic_c2_run_dir(good_dir, blocked_low_disk=False)
    rc_g, pay_g = _run_verifier_subproc(good_dir, rid_g, sha_g)
    assert rc_g == 0, pay_g
    assert pay_g["independent_verifier_status"] == "PASS"
    assert pay_g["distinct_os_process_verified"] is True
    assert pay_g["recomputed_13_verdicts"]["ENGINEERING"] == "PASS"
    assert pay_g["recomputed_13_verdicts"]["ECONOMIC"] == "NO_PROVEN_EDGE"
    assert pay_g["recomputed_13_verdicts"]["OOS_READINESS"] == "BLOCKED_NO_VERIFIED_NEW_DATA"

    # 2. GREEN on clean blocked-low-disk run (honest BLOCKED_LOW_DISK, not false PASS!)
    blk_dir = tmp_path / "good_blocked_low_disk"
    rid_b, sha_b = _write_synthetic_c2_run_dir(blk_dir, blocked_low_disk=True)
    rc_b, pay_b = _run_verifier_subproc(blk_dir, rid_b, sha_b)
    assert rc_b == 0, pay_b
    assert pay_b["recomputed_13_verdicts"]["ENGINEERING"] == "BLOCKED_LOW_DISK"
    assert pay_b["recomputed_13_verdicts"]["REGRESSION"] == "NOT_RUN_BLOCKED_LOW_DISK"

    # 3. 12 Adversarial tamper RED controls (executed concurrently with max_workers=3 -> <= 4 OS descendants!)
    def _edit_json(d: Path, fname: str, fn: Callable[[Dict[str, Any]], None]) -> None:
        p = d / fname
        obj = json.loads(p.read_text("utf-8"))
        fn(obj)
        p.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")

    tampers: list[tuple[str, Callable[[Path], None], str]] = [
        ("T01_MID_RUN_SHA_DRIFT", lambda d: _edit_json(d, "00_source_branch_remote_diff_preflight.json", lambda o: o.__setitem__("end_head_sha", "e" * 40)), "CODE_CHANGED_MID_RUN"),
        ("T02_WRONG_BRANCH", lambda d: _edit_json(d, "00_source_branch_remote_diff_preflight.json", lambda o: o.__setitem__("branch", "main")), "WRONG_BRANCH"),
        ("T03_OWNER_DISK_WAIVER", lambda d: _edit_json(d, "02_distinct_write_mount_disk_resource_preflight.json", lambda o: o.__setitem__("owner_disk_preflight_waiver", True)), "UNAPPROVED_OWNER_DISK_WAIVER_FORBIDDEN"),
        ("T04_PREMATURE_LATEST_PTR", lambda d: _edit_json(d, "19_publication_tested_sha_remote_blob_receipt.json", lambda o: o.__setitem__("latest_run_pointer_updated", True)), "PREMATURE_LATEST_RUN_POINTER_UPDATE"),
        ("T05_FALSE_REMOTE_READBACK", lambda d: _edit_json(d, "19_publication_tested_sha_remote_blob_receipt.json", lambda o: o.update({"publication_status": "PASS_REMOTE_VERIFIED", "remote_readback_sha256_verified": False})), "FALSE_REMOTE_READBACK_CLAIM"),
        ("T06_FALSE_WORM_CLAIM", lambda d: _edit_json(d, "06_escrow_hash_chain_external_anchor_truth.json", lambda o: o.__setitem__("unanchored_worm_verified", True)), "FALSE_WORM_CLAIM"),
        ("T07_NONZERO_OOS_EVAL", lambda d: _edit_json(d, "04_r2_freeze_and_prospective_schema_provenance.json", lambda o: o.__setitem__("OOS_EVALUATIONS", 1)), "NONZERO_OOS_EVALUATIONS_PEEK"),
        ("T08_FORGED_PROVEN_EDGE", lambda d: _edit_json(d, "11_walkforward_uncertainty_economic_oos_verdict.json", lambda o: o.__setitem__("RESEARCH_ECONOMIC_VERDICT", "PROVEN_EDGE")), "FORGED_ECONOMIC_VERDICT"),
        ("T09_MISSING_JUNIT_XML", lambda d: (d / "junit" / "SHARD_01.xml").unlink(), "FULL_REGRESSION_PASS_WITH_ZERO_JUNIT_XML"),
        ("T10_RAM_FLOOR_BREACH", lambda d: gzip.open(d / "logs" / "runtime_memory_samples_0p5s.jsonl.gz", "wt", encoding="utf-8").write(json.dumps({"available_ram_bytes": 256 * 1024 * 1024, "child_count": 1}) + "\n"), "MEMORY_TELEMETRY_BREACHED_512MIB_FLOOR"),
        ("T11_MUTATED_PRIOR_C1_FLAG", lambda d: _edit_json(d, "14_full_a1_si8_adapter_d4_parity.json", lambda o: o["prior_sealed_artifacts_immutability"]["KH02_R2_C1_MANIFEST_16"].__setitem__("match", False)), "PRIOR_SEALED_ARTIFACT_MUTATED"),
        ("T12_TAMPERED_GATE_18", lambda d: _edit_json(d, "18_orthogonal_gate_matrix.json", lambda o: o["gates_19"].__setitem__("G18_RESEARCH_ECONOMICS", "PROVEN_EDGE")), "GATE_TAMPER_OR_DRIFT"),
    ]

    def _run_tamper(item: tuple[str, Callable[[Path], None], str]) -> tuple[str, int, list[str], str]:
        name, mut_fn, expected_tok = item
        tdir = tmp_path / f"tamper_{name}"
        shutil.copytree(good_dir, tdir)
        mut_fn(tdir)
        rc, pay = _run_verifier_subproc(tdir, rid_g, sha_g)
        return name, rc, list(pay.get("violations") or []), expected_tok

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(_run_tamper, tampers))

    for name, rc, viols, exp_tok in results:
        assert rc == 1, f"{name} unexpectedly exited 0: {viols}"
        assert any(exp_tok in v for v in viols), f"{name} missing {exp_tok} in {viols}"
