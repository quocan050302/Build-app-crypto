"""
aurum-desk/backend/tests/test_kh_02_r2_c4_verifier_mutations.py

AURUM DESK — KH-02.R2.C4 Independent Separate-OS Verifier & Mutation Resistance Tests:
  - Clean full-pass synthetic C4 run with real per-episode compressed ledger -> exit 0, PASS.
  - Clean blocked-low-disk synthetic C4 run -> exit 2 (never promoted to 0!).
  - 14 distinct real-byte adversarial artifact mutations in isolated temp clones -> ALL exit 1
    with named fault codes (run with max_workers=3 so global OS descendants <= 4!).
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

from services.kh_02_r2_c4_risk_tail_evidence import (
    EXPECTED_R2_TAIL_ARMS_C4,
    EXPECTED_R2_TAIL_MODE_NAMES_C4,
    build_c4_per_episode_risk_ledger,
    build_c4_realized_tail_evidence_from_r2_replays,
    recompute_c4_tail_counters_from_episode_ledger,
    validate_c4_tail_disclosure_vs_risk_ledger,
)
from services.kh_02_r2_c4_trusted_transport_boundary import (
    SPEC_VERSION_KH02_R2_C4,
    TCB_THREAT_MODEL_DISCLOSURE_C4,
)
from services.kh_02_r2_research_integrity_service import (
    ACTIVATION_READY_KH02_R2,
    MANDATORY_BRANCH_KH02_R2,
    RUNTIME_SAFETY_MODES_KH02_R2,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tools.kh_02_r2_c4_independent_verifier import recompute_c4_23_gates_and_verdicts

VERIFIER_SCRIPT = APP_ROOT / "tools" / "kh_02_r2_c4_independent_verifier.py"


def _synthetic_tail_inputs() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    base_ts = 1791500000000
    bars_1m = [
        {"open_time": base_ts + i * 60_000, "close_time": base_ts + (i + 1) * 60_000, "open": 4000.0, "high": 4005.0, "low": 3995.0, "close": 4001.0}
        for i in range(8)
    ]
    reps: Dict[str, Any] = {}
    for ac in EXPECTED_R2_TAIL_ARMS_C4:
        ebm: Dict[str, Any] = {}
        for md in EXPECTED_R2_TAIL_MODE_NAMES_C4:
            ebm[md] = [
                {
                    "opportunity_id": f"{ac}_{md}_0",
                    "direction": "LONG",
                    "decision_timestamp_ms": base_ts + 60_000,
                    "checkpoint_index_1m": 1,
                    "entry_reference": 4000.0,
                    "selected_sl": 3995.0,
                    "take_profit": 4015.0,
                    "executed_quantity": 0.40,
                    "planned_net_risk_usd": 2.40,
                    "planned_net_rr": 2.5,
                    "filled": True,
                    "outcome_label": "LOSS",
                    "realized_gross_pnl_usd": -2.20,
                    "total_fees_usd": 0.65,
                    "realized_net_pnl_usd": -2.85,
                    "realized_net_r": -1.1875,
                    "execution_ledger": {"exit_type": "ADVERSE_GAP_OPEN_STOP_LOSS", "entry_fill_ts": base_ts + 60_000, "exit_fill_ts": base_ts + 120_000},
                }
            ]
        reps[ac] = {"episodes_by_mode": ebm}
    return {"1m": bars_1m}, reps


def write_synthetic_c4_run_dir(
    run_dir: Path,
    *,
    blocked_low_disk: bool = False,
    orch_pid: int = 999994,
    tested_sha: str = "f" * 40,
) -> Tuple[str, str]:
    run_id = f"20261123T023000Z_{tested_sha[:12]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    with gzip.open(run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz", "wt", encoding="utf-8") as gz:
        gz.write(json.dumps({"available_ram_bytes": 2400 * 1024 * 1024, "child_count": 2, "worker_pids": [201, 202]}) + "\n")

    ds, reps = _synthetic_tail_inputs()
    ledger_p = run_dir / "12_per_episode_risk_ledger.jsonl.gz"
    lmeta = build_c4_per_episode_risk_ledger(datasets=ds, r2_replays=reps, out_jsonl_gz_path=ledger_p)
    lrecomp = recompute_c4_tail_counters_from_episode_ledger(ledger_p)
    f11 = build_c4_realized_tail_evidence_from_r2_replays(datasets=ds, r2_replays=reps, ledger_meta=lmeta, ledger_recomputed=lrecomp)
    val11 = validate_c4_tail_disclosure_vs_risk_ledger(f11, ledger_p)

    free_gib = 9.0 if blocked_low_disk else 40.0
    proj_gib = 8.9 if blocked_low_disk else 39.9
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
            '<testcase classname="backend.tests.test_kh_02_r2_c4_provenance_adversarial" name="test_ok" time="0.01"/>'
            "</testsuite>\n",
            encoding="utf-8",
        )
        xml_sha = hashlib.sha256(xml_p.read_bytes()).hexdigest()
        nid = "backend/tests/test_kh_02_r2_c4_provenance_adversarial.py::test_ok"
        f16 = {
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
        f16 = {"status": "NOT_RUN_BLOCKED_LOW_DISK", "collected_count": 0, "executed_count": 0, "passed_count": 0, "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0, "junit_bijection_exact": False}

    h_p, h_s = "a" * 64, "b" * 64
    docs: Dict[str, Dict[str, Any]] = {
        "00": {"spec_version": SPEC_VERSION_KH02_R2_C4, "run_id": run_id, "branch": MANDATORY_BRANCH_KH02_R2, "start_head_sha": tested_sha, "end_head_sha": tested_sha, "tested_executable_sha": tested_sha, "git_clean_at_start": True, "local_uncommitted_c4_changes": False, "orchestrator_pid": orch_pid, "owner_disk_preflight_waiver": False, "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2), "ACTIVATION_READY": ACTIVATION_READY_KH02_R2},
        "01": {"status": "PASS", "locked_files_modified_count": 0, "forbidden_order_import_hits": [], "cost_assumptions_defaults_intact": True, "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2), "ACTIVATION_READY": ACTIVATION_READY_KH02_R2},
        "02": {"all_prior_sealed_artifacts_intact": True, "canonical_dataset_sha256_match": True, "prior_sealed_artifacts_immutability": {"KH02_R2_C3_MANIFEST_20": {"match": True}}},
        "03": {"owner_disk_preflight_waiver": False, "waiver_negative_controls_status": "PASS", "strict_multi_mount_probe": {"owner_waiver_permitted": False, "can_run_heavy_stages": not blocked_low_disk, "min_free_gib": free_gib, "status": disk_st, "probe_errors": [], "distinct_mounts": [{"free_gib": free_gib, "projected_post_reserve_gib": proj_gib}]}},
        "04": {"direct_mint_red_proven": True, "evil_subclass_red_proven": True, "instance_get_monkeypatch_red_proven": True},
        "05": dict(TCB_THREAT_MODEL_DISCLOSURE_C4),
        "06": {"direct_mint_spoof_blocked": True, "direct_mint_green_provenance_never_live": True, "evil_subclass_blocked": True, "instance_get_monkeypatch_blocked": True, "post_init_opener_mutation_blocked": True, "naked_raw_response_blocked": True, "real_oos_escrow_final_record_count": 0, "raw_bytes_reparse_verified": True, "parsed_forgery_rejected": True, "event_timestamp_tamper_rejected": True, "expired_proof_rejected": True, "modified_bytes_after_capture_rejected": True},
        "07": {"status": "PASS", "inherited_20_cases_passed": 20, "c4_security_cases_passed": 8, "c4_security_cases_failed": 0, "live_network_capture_executed": False, "real_prospective_snapshots_collected": 0, "live_network_test_status": "LIVE_NETWORK_TEST_NOT_RUN_BY_DESIGN"},
        "08": {"status": "PASS", "concurrent_writers_2_4_8_exact_once_passed": True, "interprocess_os_lock_passed": True, "crash_recovery_and_quarantine_passed": True, "proof_replay_different_snapshot_blocked": True, "unanchored_worm_verified": False},
        "09": {"status": "PASS", "t01_to_t10_all_passed": True, "future_mutation_invariance_passed": True, "swept_tp_accepted_count": 0},
        "10": {"status": "PASS", "fresh_live_historical_replay_executed": True, "exact_match_with_sealed_r2_c3_baseline": True, "baseline_reconciliation_diffs": []},
        "11": f11,
        "13": {"status": "PASS", "mutation_controls_passed": 10, "mutation_controls_failed": 0, "ledger_reconciliation": val11},
        "14": {"status": "PASS", "fixed_fill_zero_dropped_ids_verified": True, "full_rerisk_starts_from_full_universe": True, "full_universe_fee_reconciliation": {"status": "PASS"}, "quote_mode": "MODELED_BID_ASK", "observed_l1_bbo_available": False, "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED", "fixed_fill_decimal_checks": {"B2_H1_1x": {"gross_pnl_usd": -73.1555, "fees_usd": 102.2825, "net_pnl_usd": -175.438}}},
        "15": {"status": "PASS", "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE", "FUTURE_OOS_READINESS_VERDICT": "BLOCKED_NO_VERIFIED_NEW_DATA", "FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA", "OOS_EVALUATIONS": 0, "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2), "ACTIVATION_READY": ACTIVATION_READY_KH02_R2},
        "16": f16,
        "17": {"status": heavy_st, "pytest_controlled_subset_w1_w2_w4_repeat": {"all_4_runs_identical": not blocked_low_disk, "runs": [{"mode_label": "w=1", "canonical_sorted_outcome_sha256": h_p, "executed_tests_count": 155, "worker_pids": [1]}, {"mode_label": "w=2", "canonical_sorted_outcome_sha256": h_p, "executed_tests_count": 155, "worker_pids": [2, 3]}, {"mode_label": "w=4", "canonical_sorted_outcome_sha256": h_p, "executed_tests_count": 155, "worker_pids": [4, 5, 6, 7]}, {"mode_label": "w=1_repeat", "canonical_sorted_outcome_sha256": h_p, "executed_tests_count": 155, "worker_pids": [8]}]}, "r2_science_slice_w1_w2_w4_repeat": {"all_4_runs_identical": not blocked_low_disk, "runs": [{"mode_label": m, "canonical_sorted_sha256": h_s, "rows_count": 12062} for m in ("w=1", "w=2", "w=4", "w=1_repeat")]}},
        "18": {"BASELINE_PARITY_VERDICT": heavy_st, "a1_population_count": 56710, "a1_mismatches_count": 0, "si8_population_count": 6375, "si8_mismatches_count": 0, "three_adapter_population_count": 246, "three_adapter_mismatches_count": 0, "d4_historical_closed_trades": 0, "per_decision_and_episode_fingerprints": {"all_sealed_fingerprints_exact_match": not blocked_low_disk}},
        "19": {"min_available_ram_mib": 2400.0, "min_disk_free_gib": free_gib, "max_descendant_children": 2, "orphan_children_after_cleanup": 0, "sentinel_temp_cleaned_up": True, "owner_disk_preflight_waiver": False},
        "20": {"status": "PASS", "total_controls": 126, "new_c4_controls_count": 24, "unique_fault_ids": 126, "failed_controls": 0},
        "21": {"status": "PASS", "mutations_tested_count": 14, "mutations_failed_detection_count": 0},
        "23": {"run_id": run_id, "tested_executable_sha": tested_sha, "evidence_commit_sha": None, "remote_readback_sha256_verified": False, "latest_run_pointer_updated": False, "owner_disk_preflight_waiver": False, "publication_status": pub_st},
    }
    mem_ok = {"status": "PASS", "below_floor_samples_count": 0, "max_workers_observed_in_samples": 2}
    recomp = recompute_c4_23_gates_and_verdicts(docs=docs, mem_audit=mem_ok, tail_ledger_audit=val11, junit_bijection_ok=not blocked_low_disk, for_final_verifier_manifest=False)
    docs["22"] = {"run_id": run_id, "tested_executable_sha": tested_sha, "OOS_EVALUATIONS": 0, "ACTIVATION_READY": ACTIVATION_READY_KH02_R2, "overall_classification": recomp["overall_classification"], "gates_23": recomp["gates_23"], "verdicts": recomp["verdicts"]}
    docs["22"]["artifact_canonical_sha256"] = canonical_hash(docs["22"])

    fnames = {
        "00": "00_git_branch_sha_remote_preflight.json",
        "01": "01_function_inventory_keep_as_is_ast_diff.json",
        "02": "02_prior_sealed_artifacts_sha256_dataset_immutability.json",
        "03": "03_disk_ram_multimount_resource_policy_and_faults.json",
        "04": "04_p0a_direct_mint_subclass_fake_transport_red_proofs.json",
        "05": "05_trusted_transport_boundary_design_threat_model.json",
        "06": "06_p0a_green_capture_provenance_admission_journal_matrix.json",
        "07": "07_http_public_readonly_20_existing_plus_c4_security_cases.json",
        "08": "08_escrow_flock_concurrency_crash_idempotency_anchor_limitations.json",
        "09": "09_causal_unswept_target_future_mutation_parity.json",
        "10": "10_historical_replay_cost_risk_per_arm_science.json",
        "11": "11_realized_tail_complete_by_arm_and_mode.json",
        "13": "13_tail_ledger_numeric_reconciliation_and_mutation_tests.json",
        "14": "14_cost_fixed_fill_vs_full_rerisk_all_scenarios.json",
        "15": "15_statistical_matching_wf_oos_economic_status.json",
        "16": "16_full_pytest_collection_execution_junit_bijection.json",
        "17": "17_w1_w2_w4_w1repeat_sha256_timing_rss.json",
        "18": "18_a1_si8_adapter_d4_full_parity_fingerprints.json",
        "19": "19_runtime_ram_disk_pid_orphan_telemetry.json",
        "20": "20_all_faults_unique_red_green_receipts.json",
        "21": "21_independent_verifier_mutation_resistance_report.json",
        "22": "22_orthogonal_gate_matrix_and_verdicts_prepush.json",
        "23": "23_publication_two_phase_prepush_receipt.json",
    }
    for k, fn in fnames.items():
        (run_dir / fn).write_text(json.dumps(docs[k], indent=2, sort_keys=True), encoding="utf-8")
    (run_dir / "README_KH02_R2_C4.md").write_text(f"# KH-02.R2.C4 {run_id}\nACTIVATION_READY=NO\nNO_PROVEN_EDGE\n", encoding="utf-8")
    (run_dir / "HANDOVER_KH02_R2_C4_VI.txt").write_text(f"Run {run_id}\nACTIVATION_READY=NO\nNO_PROVEN_EDGE\n", encoding="utf-8")
    return run_id, tested_sha


def run_c4_verifier_subproc(run_dir: Path, run_id: str, tested_sha: str) -> Tuple[int, Dict[str, Any]]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)])
    proc = subprocess.run(
        [sys.executable, str(VERIFIER_SCRIPT), "--run-dir", str(run_dir), "--expected-run-id", run_id, "--expected-tested-sha", tested_sha, "--skip-live-repo-pointers", "--no-write-manifest"],
        cwd=str(APP_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return int(proc.returncode), (json.loads(proc.stdout) if proc.stdout.strip() else {})


def test_c4_independent_verifier_green_blocked_and_14_adversarial_mutations(tmp_path: Path) -> None:
    good_dir = tmp_path / "good_c4"
    rid_g, sha_g = write_synthetic_c4_run_dir(good_dir, blocked_low_disk=False)
    rc_g, pay_g = run_c4_verifier_subproc(good_dir, rid_g, sha_g)
    assert rc_g == 0, pay_g
    assert pay_g["independent_verifier_status"] == "PASS"
    assert pay_g["overall_classification"] == "PASS_ENGINEERING_RESEARCH_NO_EDGE_OOS_BLOCKED"
    assert pay_g["recomputed_verdicts"]["ENGINEERING_C4"] == "PASS"
    assert pay_g["recomputed_verdicts"]["ECONOMIC"] == "NO_PROVEN_EDGE"
    assert pay_g["recomputed_verdicts"]["ACTIVATION_READY"] == "NO"

    blk_dir = tmp_path / "blk_c4"
    rid_b, sha_b = write_synthetic_c4_run_dir(blk_dir, blocked_low_disk=True)
    rc_b, pay_b = run_c4_verifier_subproc(blk_dir, rid_b, sha_b)
    assert rc_b == 2, pay_b
    assert pay_b["recomputed_verdicts"]["ENGINEERING_C4"] == "BLOCKED_LOW_DISK"

    def _ed(d: Path, fn: str, mut: Callable[[Dict[str, Any]], None]) -> None:
        p = d / fn
        obj = json.loads(p.read_text("utf-8"))
        mut(obj)
        p.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")

    def _drop_ledger_row(d: Path) -> None:
        p = d / "12_per_episode_risk_ledger.jsonl.gz"
        with gzip.open(p, "rt", encoding="utf-8") as src:
            lines = [ln for ln in src if ln.strip()]
        with gzip.open(p, "wt", encoding="utf-8") as dst:
            for ln in lines[:-1]:
                dst.write(ln)

    mutations: list[tuple[str, Callable[[Path], None], str]] = [
        ("M01_EMPTY_TAIL_SUMMARY", lambda d: _ed(d, "11_realized_tail_complete_by_arm_and_mode.json", lambda o: o.__setitem__("arm_fill_time_and_tail_audits", {})), "INDEPENDENT_TAIL_LEDGER"),
        ("M02_FAKE_ZERO_LOSS_GT_CAP", lambda d: _ed(d, "11_realized_tail_complete_by_arm_and_mode.json", lambda o: o["arm_fill_time_and_tail_audits"]["B2_H1"]["NEXT_BAR_OPEN_BID_ASK"].__setitem__("realized_losses_exceeding_2p50_count", 0)), "CELL_INT_MISMATCH"),
        ("M03_ALTERED_MAX_LOSS", lambda d: _ed(d, "11_realized_tail_complete_by_arm_and_mode.json", lambda o: o["arm_fill_time_and_tail_audits"]["B2_H1"]["NEXT_BAR_OPEN_BID_ASK"].__setitem__("max_realized_net_loss_usd", 1.50)), "CELL_FLOAT_MISMATCH"),
        ("M04_DROPPED_LEDGER_ROW", _drop_ledger_row, "INDEPENDENT_TAIL_LEDGER"),
        ("M05_JUNIT_COUNT_TAMPER", lambda d: _ed(d, "16_full_pytest_collection_execution_junit_bijection.json", lambda o: o.__setitem__("passed_count", 99)), "JUNIT_COUNTS_TAMPERED_VS_RAW_XML"),
        ("M06_W1_W4_HASH_MUTATION", lambda d: _ed(d, "17_w1_w2_w4_w1repeat_sha256_timing_rss.json", lambda o: o["r2_science_slice_w1_w2_w4_repeat"]["runs"][2].__setitem__("canonical_sorted_sha256", "9" * 64)), "W1_W2_W4_DETERMINISM_HASH_MISMATCH"),
        ("M07_DECIMAL_PNL_TAMPER", lambda d: _ed(d, "14_cost_fixed_fill_vs_full_rerisk_all_scenarios.json", lambda o: o["fixed_fill_decimal_checks"]["B2_H1_1x"].__setitem__("net_pnl_usd", 999.0)), "FIXED_FILL_DECIMAL_MISMATCH"),
        ("M08_PREPUSH_FALSE_REMOTE", lambda d: _ed(d, "23_publication_two_phase_prepush_receipt.json", lambda o: o.update({"publication_status": "PASS_REMOTE_VERIFIED", "remote_readback_sha256_verified": False})), "FALSE_REMOTE_READBACK_CLAIM"),
        ("M09_OOS_EVAL_NONZERO", lambda d: _ed(d, "15_statistical_matching_wf_oos_economic_status.json", lambda o: o.__setitem__("OOS_EVALUATIONS", 1)), "NONZERO_OOS_EVALUATIONS_PEEK"),
        ("M10_ACTIVATION_TURNED_ON", lambda d: _ed(d, "15_statistical_matching_wf_oos_economic_status.json", lambda o: o.__setitem__("ACTIVATION_READY", "YES")), "FORGED_ACTIVATION_READY_ON"),
        ("M11_FORGED_PROVEN_EDGE", lambda d: _ed(d, "15_statistical_matching_wf_oos_economic_status.json", lambda o: o.__setitem__("RESEARCH_ECONOMIC_VERDICT", "PROVEN_EDGE")), "FORGED_ECONOMIC_VERDICT"),
        ("M12_OWNER_DISK_WAIVER", lambda d: _ed(d, "03_disk_ram_multimount_resource_policy_and_faults.json", lambda o: o.__setitem__("owner_disk_preflight_waiver", True)), "UNAPPROVED_OWNER_DISK_WAIVER_FORBIDDEN"),
        ("M13_RAM_FLOOR_BREACH", lambda d: gzip.open(d / "logs" / "runtime_memory_samples_0p5s.jsonl.gz", "wt", encoding="utf-8").write(json.dumps({"available_ram_bytes": 150 * 1024 * 1024, "child_count": 1}) + "\n"), "MEMORY_TELEMETRY_BREACHED_512MIB_FLOOR"),
        ("M14_GATE_MATRIX_TAMPER", lambda d: _ed(d, "22_orthogonal_gate_matrix_and_verdicts_prepush.json", lambda o: o["gates_23"].__setitem__("G19_ECONOMIC_TRUTH", "PROVEN_EDGE")), "GATE_TAMPER_OR_DRIFT"),
    ]

    def _run_m(item: tuple[str, Callable[[Path], None], str]) -> tuple[str, int, list[str], str]:
        nm, fn, tok = item
        td = tmp_path / f"mut_{nm}"
        shutil.copytree(good_dir, td)
        fn(td)
        rc, pay = run_c4_verifier_subproc(td, rid_g, sha_g)
        return nm, rc, list(pay.get("violations") or []), tok

    with ThreadPoolExecutor(max_workers=3) as pool:
        outs = list(pool.map(_run_m, mutations))

    for nm, rc, viols, tok in outs:
        assert rc == 1, f"{nm} expected exit 1, got {rc}: {viols}"
        assert any(tok in v for v in viols), f"{nm} missing {tok} in {viols}"
