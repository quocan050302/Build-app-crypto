"""
aurum-desk/backend/tests/test_kh_02_asof_mtf_and_fault_controls.py

Strict as-of M15/H1 causality, leakage negative controls, complete 33/33 RED/GREEN fault suite
(`F01`..`F33`), and separate-OS-process independent verifier tamper-rejection tests.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT.parent
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_01b_r1_ram_safety_r3_service import (
    SEALED_R2_A1_56710_IDS_SHA256,
    SEALED_R2_E00_BUNDLE_SHA256,
    SEALED_R2_SI0_242_TUPLES_SHA256,
    SEALED_R2_SI8_6375_IDS_SHA256,
)
from services.kh_02_entry_foundation_research_service import (
    CANONICAL_DATASET_SHA256_KH02,
    MANDATORY_BRANCH_KH02,
    SCHEMA_VERSION_KH02,
    SPEC_VERSION_KH02,
    build_kh02_asof_mtf_features,
    evaluate_kh02_independent_gates_from_disk,
    preregister_kh02_hypotheses,
    run_kh02_33_red_green_fault_suite,
    run_kh02_future_mutation_invariance_check,
)
from services.kh_01a_r1_forensics_closure_service import build_dynamic_validated_gate_receipt_r1
from tools.fresh_junit_io import (
    write_json_durable_atomic,
    write_jsonl_gz_durable_atomic,
    write_text_durable_atomic,
)
from tools.run_v17_9_3_1_2_full_universe_backtest import load_canonical_xauusdt_datasets


def test_kh02_real_xauusdt_future_mutation_invariance_zero_lookahead() -> None:
    datasets, _ = load_canonical_xauusdt_datasets()
    res = run_kh02_future_mutation_invariance_check(datasets, cutoff_m1_index=600, window_end_index=1200)
    assert res["status"] == "PASS"
    assert res["future_bars_mutated_count"] > 0
    assert res["past_bars_checked_count"] == 571
    assert res["past_feature_hash_mismatches"] == 0


def test_kh02_33_real_red_green_fault_suite_all_pass() -> None:
    suite = run_kh02_33_red_green_fault_suite(REPO_ROOT)
    assert suite["total_fault_controls"] == 33
    assert suite["passed_fault_controls"] == 33, [m for m in suite["mutations"] if m["status"] != "PASS"]
    assert suite["failed_fault_controls"] == 0
    assert suite["status"] == "PASS"
    ids = [m["fault_id"] for m in suite["mutations"]]
    assert ids == [f"F{i:02d}" for i in range(1, 34)]


def _build_synthetic_valid_kh02_run_dir(run_dir: Path, run_id: str, tested_sha: str) -> None:
    """Builds a minimal self-consistent KH-02 artifact tree on disk for subprocess verifier tamper testing."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "receipts").mkdir(exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "junit").mkdir(exist_ok=True)

    cand_sha = write_jsonl_gz_durable_atomic(
        run_dir / "06_b0_b1_b2_entry_candidates_and_reason_codes.jsonl.gz",
        [{"arm_code": "B2_H1", "opportunity_id": "opp1", "eligible": True}],
    )
    mem_sha = write_jsonl_gz_durable_atomic(
        run_dir / "logs/runtime_memory_samples_0p5s.jsonl.gz",
        [
            {"epoch_ns": 1000, "available_ram_bytes": 4096 * 1024**2, "free_disk_bytes": 40 * 1024**3, "rss_bytes": 400 * 1024**2, "child_rss_bytes": 250 * 1024**2, "worker_pids": [1, 2], "descendant_pids": [1, 2]},
            {"epoch_ns": 2000, "available_ram_bytes": 3800 * 1024**2, "free_disk_bytes": 40 * 1024**3, "rss_bytes": 420 * 1024**2, "child_rss_bytes": 260 * 1024**2, "worker_pids": [1, 2], "descendant_pids": [1, 2]},
        ],
    )
    xml_p = run_dir / "junit/s1.xml"
    xml_p.write_text(
        '<?xml version="1.0"?><testsuite tests="1" failures="0" errors="0" skipped="0">'
        '<testcase classname="backend.tests.test_kh_02_entry_foundation_research" name="test_ok" time="0.01"/>'
        '</testsuite>',
        encoding="utf-8",
    )
    import hashlib
    xml_sha = hashlib.sha256(xml_p.read_bytes()).hexdigest()

    plan = preregister_kh02_hypotheses(dataset_sha256=CANONICAL_DATASET_SHA256_KH02, config_sha256="cfg", code_sha256=tested_sha)
    from dataclasses import asdict
    plan_d = asdict(plan)

    write_json_durable_atomic(run_dir / "00_preflight_git_branch_head_resource_runtime.json", {
        "spec_version": SPEC_VERSION_KH02, "schema_version": SCHEMA_VERSION_KH02,
        "run_id": run_id, "branch": MANDATORY_BRANCH_KH02, "head_at_start_sha": "startsha123456",
        "tested_executable_sha": tested_sha, "orchestrator_pid": 999999,
        "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02, "ACTIVATION_READY": "NO",
        "runtime_safety_verification": {"runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"}},
        "source_tree_unchanged_during_run": True, "prior_artifacts_unchanged_during_run": True,
    })
    write_json_durable_atomic(run_dir / "01_function_callgraph_keep_as_is_diff_immutability.json", {
        "status": "PASS", "locked_files_modified_count": 0, "modified_existing_files_count": 0, "forbidden_order_import_hits": [],
    })
    write_json_durable_atomic(run_dir / "02_dataset_registry_lineage_m1_m15_h1_quality.json", {
        "status": "PASS", "dataset_sha256": CANONICAL_DATASET_SHA256_KH02,
        "data_origin_enum": {"l1_bid_ask_quotes": "SIMULATED"}, "mtf_m15_h1_covers_m1_window": True,
        "per_timeframe_quality": {"1m": {"bar_count": 22376}, "15m": {"bar_count": 15090}, "1H": {"bar_count": 3863}},
    })
    write_json_durable_atomic(run_dir / "03_frozen_hypotheses_budget_splits_power_falsification.json", plan_d)
    write_json_durable_atomic(run_dir / "04_asof_mtf_feature_provenance_and_leakage_controls.json", {
        "status": "PASS", "post_warmup_bar_count": 22346, "invalidation_violations_count": 0,
        "future_mutation_invariance_test": {"status": "PASS", "past_feature_hash_mismatches": 0},
    })
    write_json_durable_atomic(run_dir / "05_opportunity_universe_denominators_and_cohort_ids.json", {
        "total_directional_bar_opportunities": 44692, "candidate_audit_jsonl_gz_sha256": cand_sha,
    })
    write_json_durable_atomic(run_dir / "07_matched_cohort_confounder_balance_ablations.json", {
        "foundation_b2_ablations_and_negative_controls": [{"arm_code": f"A{i}"} for i in range(12)],
    })
    write_json_durable_atomic(run_dir / "08_chronological_one_position_three_fill_replay.json", {
        "status": "PASS", "total_overlap_violations_across_all_arms": 0,
        "total_risk_cap_violations_across_all_arms": 0, "total_min_net_rr_violations_across_all_arms": 0,
        "arms": {"B2_H1": {}},
    })
    write_json_durable_atomic(run_dir / "09_fee_spread_slippage_1x_2x_3x_counterfactuals.json", {"status": "PASS"})
    write_json_durable_atomic(run_dir / "10_purged_walkforward_and_cluster_uncertainty.json", {
        "status": "PASS", "any_fold_leakage_detected": False,
        "overall_research_economic_verdict": "NO_PROVEN_EDGE",
        "arms_uncertainty": {"B2_H1": {"closed_trades_nb": 0, "expectancy_1x_nb_r": None, "arm_economic_verdict": "NOT_ESTIMABLE"}},
    })
    write_json_durable_atomic(run_dir / "11_oos_escrow_no_overlap_no_peek_status.json", {
        "status": "PASS", "FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA", "OOS_EVALUATIONS": 0,
        "ACTIVATION_READY": "NO", "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
    })
    from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
    nid_list = ["backend/tests/test_kh_02_entry_foundation_research.py::test_ok"]
    write_json_durable_atomic(run_dir / "12_full_pytest_collection_junit_bijection_exit.json", {
        "execution_status": "EXECUTED",
        "collection_manifest": {
            "run_id": run_id,
            "tested_executable_sha": tested_sha,
            "collect_exit_code": 0,
            "collect_pid": 12345,
            "collected_node_ids": nid_list,
            "collected_node_ids_count": 1,
            "collected_node_ids_sha256": canonical_hash(nid_list),
        },
        "shard_manifest": {
            "run_id": run_id,
            "tested_executable_sha": tested_sha,
            "nonzero_exit_shards_count": 0,
            "shards": [{
                "shard_name": "s1",
                "junit_xml_path": "junit/s1.xml",
                "xml_sha256": xml_sha,
                "junit_sha256": xml_sha,
                "xml_mtime_ns": xml_p.stat().st_mtime_ns,
                "exit_code": 0,
                "pid": 12346,
                "reused": False,
                "run_id": run_id,
                "tested_executable_sha": tested_sha,
                "scheduled_node_ids": nid_list,
            }],
        },
        "bijection_verification": {"status": "PASS", "collected_count": 1, "executed_count": 1, "passed_count": 1, "skipped_count": 0, "failed_count": 0, "errors_count": 0, "mandatory_skipped_count": 0},
    })
    write_json_durable_atomic(run_dir / "13_async_w1_w2_w4_w1repeat_hashes_speedups.json", {
        "execution_status": "EXECUTED", "pytest_subset_w1_w2_w4_repeat_identical": True,
        "kh02_science_slice_w1_w2_w4_repeat_identical": True, "measured_speedup_w4_vs_w1": 1.4,
        "pytest_controlled_subset_w1_w2_w4_repeat": {"runs": [{"mode_label": m, "canonical_sorted_outcome_sha256": "abc"} for m in ("w=1", "w=2", "w=4", "w=1_repeat")]},
        "kh02_science_slice_w1_w2_w4_repeat": {"runs": [{"mode_label": m, "canonical_sorted_sha256": "def"} for m in ("w=1", "w=2", "w=4", "w=1_repeat")]},
    })
    write_json_durable_atomic(run_dir / "14_resource_telemetry_ram_disk_pid_cleanup.json", {
        "disk_reserve_policy": {"disk_safety_verdict": "PASS_STRICT_20GIB"},
        "memory_samples_rel_path": "logs/runtime_memory_samples_0p5s.jsonl.gz",
        "memory_samples_sha256": mem_sha,
        "memory_samples_audit": {"min_available_ram_mib": 3800.0, "below_floor_samples_count": 0, "peak_parent_rss_mib": 420.0},
        "runtime_telemetry": {"max_children_observed": 2, "orphan_pids_at_cleanup_count": 0, "peak_child_tree_rss_mib": 260.0, "min_free_disk_gib": 40.0, "post_cleanup_free_gib": 40.0, "temp_dirs_cleaned_with_sentinel_check": True, "unowned_paths_deleted_count": 0},
    })
    write_json_durable_atomic(run_dir / "15_red_green_fault_injection_independent_controls.json", {
        "status": "PASS", "total_fault_controls": 33, "passed_fault_controls": 33, "failed_fault_controls": 0,
    })
    write_json_durable_atomic(run_dir / "16_production_baseline_adapter_fingerprint_parity.json", {
        "status": "PASS", "a1_evaluated_count": 56710, "a1_mismatches_count": 0,
        "si8_separate_population_count": 6375, "three_adapter_episodes_denominator": 246, "three_adapter_mismatches_count": 0,
        "si0_e00_primary_nb_summary": {"total_scheduled_episodes": 242, "closed_trades_count": 166, "wins_count": 9, "losses_count": 157, "total_realized_net_usd": -345.8971},
        "per_decision_and_episode_fingerprints": {
            "a1_56710_ids_sha256": SEALED_R2_A1_56710_IDS_SHA256,
            "si8_6375_ids_sha256": SEALED_R2_SI8_6375_IDS_SHA256,
            "si0_242_episode_matched_tuples_sha256": SEALED_R2_SI0_242_TUPLES_SHA256,
            "e00_bundle_sha256": SEALED_R2_E00_BUNDLE_SHA256,
        },
    })
    for i in range(1, 18):
        build_dynamic_validated_gate_receipt_r1(
            gate_id=f"G-KH02-{i:02d}", run_dir=run_dir, run_id=run_id, tested_sha=tested_sha,
            target_rel_path="00_preflight_git_branch_head_resource_runtime.json",
            validator_fn=lambda: {"status": "PASS", "violations": []},
            write_json_fn=write_json_durable_atomic,
        )
    f17 = evaluate_kh02_independent_gates_from_disk(run_dir, run_id, tested_sha)
    write_json_durable_atomic(run_dir / "17_engineering_economic_oos_activation_gate_matrix.json", f17)
    write_json_durable_atomic(run_dir / "19_git_tested_sha_evidence_publication_receipt.json", {
        "tested_executable_sha": tested_sha, "run_id": run_id,
    })
    write_text_durable_atomic(run_dir / "README_KH02_ENTRY_FOUNDATION.md", f"KH-02 {run_id} {tested_sha}\n")
    write_text_durable_atomic(run_dir / "HANDOVER_KH02_ENTRY_FOUNDATION_VI.txt", f"KH-02 {run_id} {tested_sha}\n")


def test_kh02_separate_process_verifier_accepts_clean_and_rejects_tampered(tmp_path: Path) -> None:
    verifier = APP_ROOT / "tools/kh_02_entry_foundation_independent_verifier.py"
    tested_sha = "abcdef1234567890abcdef1234567890abcdef12"
    run_id = f"20261121T120000Z_{tested_sha[:12]}"
    clean_dir = tmp_path / "clean_run"
    _build_synthetic_valid_kh02_run_dir(clean_dir, run_id, tested_sha)

    def _run_v(target_dir: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable, str(verifier),
                "--run-dir", str(target_dir),
                "--expected-run-id", run_id,
                "--expected-tested-sha", tested_sha,
                "--skip-git-freeze",
                "--skip-repo-pointers",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )

    ok_proc = _run_v(clean_dir)
    assert ok_proc.returncode == 0, ok_proc.stdout + ok_proc.stderr
    ok_res = json.loads(ok_proc.stdout)
    assert ok_res["independent_verifier_status"] == "PASS"
    assert ok_res["separate_os_process_verified"] is True

    # Tamper 1: Forged PROVEN_EDGE without fresh OOS -> FAIL
    t1 = tmp_path / "tamper_proven_edge"
    _build_synthetic_valid_kh02_run_dir(t1, run_id, tested_sha)
    p10 = t1 / "10_purged_walkforward_and_cluster_uncertainty.json"
    d10 = json.loads(p10.read_text("utf-8"))
    d10["arms_uncertainty"]["B2_H1"]["arm_economic_verdict"] = "PROVEN_EDGE"
    p10.write_text(json.dumps(d10), encoding="utf-8")
    r1 = _run_v(t1)
    assert r1.returncode != 0
    assert "ILLEGAL_PROVEN_EDGE_WITHOUT_FRESH_OOS" in r1.stdout

    # Tamper 2: Raw RAM sample < 512 MiB hidden under PASS -> FAIL
    t2 = tmp_path / "tamper_low_ram"
    _build_synthetic_valid_kh02_run_dir(t2, run_id, tested_sha)
    bad_mem_sha = write_jsonl_gz_durable_atomic(
        t2 / "logs/runtime_memory_samples_0p5s.jsonl.gz",
        [{"epoch_ns": 1000, "available_ram_bytes": 300 * 1024**2, "free_disk_bytes": 40 * 1024**3, "rss_bytes": 400 * 1024**2, "child_rss_bytes": 250 * 1024**2, "worker_pids": [1], "descendant_pids": [1]}],
    )
    p14 = t2 / "14_resource_telemetry_ram_disk_pid_cleanup.json"
    d14 = json.loads(p14.read_text("utf-8"))
    d14["memory_samples_sha256"] = bad_mem_sha
    p14.write_text(json.dumps(d14), encoding="utf-8")
    r2 = _run_v(t2)
    assert r2.returncode != 0
    assert "RAW_RAM_SAMPLES_BELOW_512MIB" in r2.stdout

    # Tamper 3: Post-freeze hypothesis edit -> FAIL
    t3 = tmp_path / "tamper_hyp"
    _build_synthetic_valid_kh02_run_dir(t3, run_id, tested_sha)
    p03 = t3 / "03_frozen_hypotheses_budget_splits_power_falsification.json"
    d03 = json.loads(p03.read_text("utf-8"))
    d03["min_net_rr"] = 1.0
    p03.write_text(json.dumps(d03), encoding="utf-8")
    r3 = _run_v(t3)
    assert r3.returncode != 0
    assert "FROZEN_HYPOTHESIS_03_SHA256_TAMPERED" in r3.stdout

    # Tamper 4: Non-zero OOS evaluation peek -> FAIL
    t4 = tmp_path / "tamper_oos"
    _build_synthetic_valid_kh02_run_dir(t4, run_id, tested_sha)
    p11 = t4 / "11_oos_escrow_no_overlap_no_peek_status.json"
    d11 = json.loads(p11.read_text("utf-8"))
    d11["OOS_EVALUATIONS"] = 1
    p11.write_text(json.dumps(d11), encoding="utf-8")
    r4 = _run_v(t4)
    assert r4.returncode != 0
    assert "OOS_11_ACTIVATION_OR_EVAL_VIOLATION" in r4.stdout
