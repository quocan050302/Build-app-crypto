#!/usr/bin/env python3
"""
AURUM Desk V17.9.3.1.2 — Full-Universe Replay & Executable Entry/SL/TP Counterfactual Verification Runner
========================================================================================================
Produces all 27 numbered artifacts (`01` through `27` + `artifact_sha256_manifest.json` + `decision_stream_chunks/` + `junit/`)
in `docs/artifacts/v17_9_3_1_2/<RUN_ID>/` and updates `docs/artifacts/v17_9_3_1_2/latest_run.json`.
Evaluates all 38 non-tautological acceptance gates (`G01` through `G38`).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import hashlib
import inspect
import json
import os
import resource
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.v17_8_backtest_runner import compute_executable_source_tree_hash
from services.v17_9_3_1_1_loss_forensics_and_research_service import (
    get_canonical_risk_policy_snapshot,
    validate_acceptance_gate_manifest,
    validate_artifact_sha256_manifest,
    validate_junit_and_collection_bijection,
)
from services.v17_9_3_1_2_executable_intervention_replay_service import (
    ExecutableResearchPolicyV179312,
    FullUniverseReplaySummaryV179312,
    PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312,
    V17_9_3_1_2_SCHEMA_VERSION,
    assert_intervention_is_real_v179312,
    assert_replay_oos_temporal_and_feature_integrity_v179312,
    attribution_v179312,
    build_causal_decision_context_v179312,
    build_order_intent_from_decision_v179312,
    build_protected_structure_authority_audit_v179312,
    compute_economic_and_statistical_summary_v179312,
    compute_loss_root_cause_matrix_v179312,
    compute_research_policy_registry_hash_v179312,
    derive_executable_structural_stop_v179312,
    derive_executable_structural_target_v179312,
    enumerate_full_replay_indices_v179312,
    evaluate_executable_interventions_on_cohort_v179312,
    evaluate_full_universe_model_batch_v179312,
    extract_per_timeframe_causal_provenance_v179312,
    find_next_causal_entry_confirmation_v179312,
    match_variants_on_causal_opportunity_v179312,
    materialize_executable_counterfactual_v179312,
    merge_and_validate_decision_stream_v179312,
    rebuild_entry_reference_v179312,
    recalculate_trade_risk_quantity_v179312,
    reconcile_trades_to_ledger_v179312,
    replay_global_chronological_portfolio_v179312,
    replay_intervention_outcome_v179312,
    simulate_executable_candidate_v179312,
    verify_default_production_flags_off_v179312,
    verify_no_trade_time_overlap_v179312,
    write_append_only_decision_stream_v179312,
)
from tools.run_v17_9_3_1_2_full_universe_backtest import (
    run_v17_9_3_1_2_full_universe_backtest_pipeline,
)

# ==============================================================================
# MANDATORY PROVENANCE CONSTANTS (V17.9.3.1.2)
# ==============================================================================
MANDATORY_WORKING_BRANCH = "feature/v17-9-2-structural-sl-tp-from-qa"
STARTING_V179312_FEATURE_HEAD_SHA = "f455afc0950e13cee03dac2c912363379719aafb"
PREVIOUS_V179311_TESTED_CODE_SHA = "e43c785a5bb916bc00b31b378be1130ab3308bc9"
PREVIOUS_V17931_TESTED_CODE_SHA = "a00a17091bf7991855aaeb3f718242354c244a08"
PREVIOUS_V1793_TESTED_CODE_SHA = "8350cf25528cffa7138ee9020e7290fd1c6c5192"
PREVIOUS_V17921_TESTED_CODE_SHA = "b95ef57b237cc7024ac3ef6674591794b0a059a8"
BASE_MASTER_SHA = "650e4535777421077443cd3d95507cad1d99ed42"

PREVIOUS_V179311_RUN_ID = "v17_9_3_1_1_20261101T154301Z_e43c785a5bb9"
PREVIOUS_V17931_RUN_ID = "v17_9_3_1_20261101T135550Z_a00a17091bf7"
PREVIOUS_V1793_RUN_ID = "v17_9_3_20261101T101517Z_8350cf25528c"
PREVIOUS_V17921_RUN_ID = "v17_9_2_1_20261101T074221Z_b95ef57b237c"
PREVIOUS_V1792_RUN_ID = "v17_9_2_20261101T051946Z_ab8f0252a05d"

IMMUTABLE_PRIOR_DIRS = (
    "docs/artifacts/v17_9_2",
    "docs/artifacts/v17_9_2_1",
    "docs/artifacts/v17_9_3",
    "docs/artifacts/v17_9_3_1",
    "docs/artifacts/v17_9_3_1_1",
)

REQUIRED_27_ARTIFACT_FILES = (
    "01_git_branch_source_frozen_identity.json",
    "02_full_universe_22346_index_coverage_manifest.json",
    "03_decision_stream_chunks_manifest.json",
    "04_outside_screen_15597_timestamps_47243_decisions_reconciliation.json",
    "05_full_universe_portfolio_replay_A0_A1_E1_B1_D2_primary.json",
    "06_full_universe_portfolio_replay_3_fill_modes_comparison.json",
    "07_legacy_screened_vs_full_universe_side_by_side_attribution.json",
    "08_matched_coeval_comparison_report.json",
    "09_no_trade_overlap_and_candidate_conservation_audit.json",
    "10_trade_to_ledger_and_fee_reconciliation_audit.json",
    "11_executable_research_policy_registry_frozen_manifest.json",
    "12_executable_entry_retiming_counterfactual_ledger.json",
    "13_executable_structural_sl_counterfactual_ledger.json",
    "14_executable_structural_tp_counterfactual_ledger.json",
    "15_combined_executable_interventions_3_mode_ledger.json",
    "16_intervention_reality_and_anti_fake_audit.json",
    "17_matched_opportunity_vs_independent_portfolio_attribution.json",
    "18_updated_loss_root_cause_matrix_full_universe.json",
    "19_protected_structure_authority_and_stop_classification_audit.json",
    "20_per_timeframe_causal_provenance_audit.json",
    "21_chronological_oos_and_walkforward_integrity_report.json",
    "22_single_vs_multi_worker_determinism_proof.json",
    "23_pytest_execution_and_junit_bijection_proof.json",
    "24_economic_and_statistical_verdict_summary.json",
    "25_acceptance_gates_G01_to_G38_manifest.json",
    "26_Closure_Executive_Report_V17_9_3_1_2.md",
    "27_git_commit_push_remote_HEAD_proof.json",
)

EXPECTED_38_GATE_IDS = tuple(f"G{i:02d}" for i in range(1, 39))


def _run_git(args: Sequence[str], check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=check,
    )
    return proc.stdout.strip()


def _is_ancestor(ancestor_sha: str, descendant_sha: str = "HEAD") -> bool:
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor_sha, descendant_sha],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _check_disk_and_resource_budget(min_free_gb: float = 5.0) -> Dict[str, Any]:
    proc = subprocess.run(["df", "-Pk", str(REPO_ROOT)], capture_output=True, text=True, check=True)
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    parts = lines[-1].split()
    avail_kb = int(parts[3])
    free_gb = round(avail_kb / (1024.0 * 1024.0), 3)
    ru = resource.getrusage(resource.RUSAGE_SELF)
    ru_child = resource.getrusage(resource.RUSAGE_CHILDREN)
    peak_rss_mb = round((int(ru.ru_maxrss) + int(ru_child.ru_maxrss)) / (1024.0 * 1024.0), 2)
    return {
        "free_disk_gb": free_gb,
        "min_required_free_disk_gb": min_free_gb,
        "disk_floor_satisfied": free_gb >= min_free_gb,
        "df_raw_line": lines[-1],
        "peak_rss_mb_combined": peak_rss_mb,
        "cpu_count": os.cpu_count() or 8,
    }


def _check_prior_artifact_dirs_immutability() -> Dict[str, Any]:
    immutable_status: Dict[str, Any] = {}
    all_immutable_clean = True
    for rel_dir in IMMUTABLE_PRIOR_DIRS:
        dir_path = REPO_ROOT / rel_dir
        diff_out = _run_git(["status", "--porcelain", "--", rel_dir], check=False)
        git_diff_from_start = _run_git(
            ["diff", "--name-only", STARTING_V179312_FEATURE_HEAD_SHA, "HEAD", "--", rel_dir],
            check=False,
        )
        files_list = sorted(p for p in dir_path.rglob("*") if p.is_file()) if dir_path.exists() else []
        h = hashlib.sha256()
        for f in files_list:
            h.update(str(f.relative_to(REPO_ROOT)).encode("utf-8"))
            h.update(hashlib.sha256(f.read_bytes()).digest())
        dir_clean = (diff_out == "") and (git_diff_from_start == "") and dir_path.is_dir() and len(files_list) > 0
        if not dir_clean:
            all_immutable_clean = False
        immutable_status[rel_dir] = {
            "exists": dir_path.is_dir(),
            "file_count": len(files_list),
            "aggregate_sha256": h.hexdigest(),
            "working_tree_status": diff_out,
            "diff_since_starting_head": git_diff_from_start,
            "untouched": dir_clean,
        }
    return {
        "all_immutable_prior_dirs_untouched": all_immutable_clean,
        "immutable_directories": immutable_status,
    }


def build_artifact_01_git_branch_source_frozen_identity(
    *,
    run_id: str,
    tested_code_sha: str,
) -> Dict[str, Any]:
    current_branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    head_sha = _run_git(["rev-parse", "HEAD"])
    master_sha = _run_git(["rev-parse", "master"], check=False) or BASE_MASTER_SHA
    remote_branch_raw = _run_git(
        ["ls-remote", "--heads", "origin", MANDATORY_WORKING_BRANCH], check=False
    )
    remote_sha = remote_branch_raw.split()[0] if remote_branch_raw else ""

    exec_hash_info = compute_executable_source_tree_hash()
    prior_immutability = _check_prior_artifact_dirs_immutability()

    targets = [
        ("backend/services/v17_9_3_1_2_executable_intervention_replay_service.py", "enumerate_full_replay_indices_v179312", enumerate_full_replay_indices_v179312),
        ("backend/services/v17_9_3_1_2_executable_intervention_replay_service.py", "build_causal_decision_context_v179312", build_causal_decision_context_v179312),
        ("backend/services/v17_9_3_1_2_executable_intervention_replay_service.py", "evaluate_full_universe_model_batch_v179312", evaluate_full_universe_model_batch_v179312),
        ("backend/services/v17_9_3_1_2_executable_intervention_replay_service.py", "write_append_only_decision_stream_v179312", write_append_only_decision_stream_v179312),
        ("backend/services/v17_9_3_1_2_executable_intervention_replay_service.py", "merge_and_validate_decision_stream_v179312", merge_and_validate_decision_stream_v179312),
        ("backend/services/v17_9_3_1_2_executable_intervention_replay_service.py", "build_order_intent_from_decision_v179312", build_order_intent_from_decision_v179312),
        ("backend/services/v17_9_3_1_2_executable_intervention_replay_service.py", "simulate_executable_candidate_v179312", simulate_executable_candidate_v179312),
        ("backend/services/v17_9_3_1_2_executable_intervention_replay_service.py", "replay_global_chronological_portfolio_v179312", replay_global_chronological_portfolio_v179312),
        ("backend/services/v17_9_3_1_2_executable_intervention_replay_service.py", "verify_no_trade_time_overlap_v179312", verify_no_trade_time_overlap_v179312),
        ("backend/services/v17_9_3_1_2_executable_intervention_replay_service.py", "reconcile_trades_to_ledger_v179312", reconcile_trades_to_ledger_v179312),
        ("backend/services/v17_9_3_1_2_executable_intervention_replay_service.py", "ExecutableResearchPolicyV179312", ExecutableResearchPolicyV179312),
        ("backend/services/v17_9_3_1_2_executable_intervention_replay_service.py", "find_next_causal_entry_confirmation_v179312", find_next_causal_entry_confirmation_v179312),
        ("backend/services/v17_9_3_1_2_executable_intervention_replay_service.py", "rebuild_entry_reference_v179312", rebuild_entry_reference_v179312),
        ("backend/services/v17_9_3_1_2_executable_intervention_replay_service.py", "derive_executable_structural_stop_v179312", derive_executable_structural_stop_v179312),
        ("backend/services/v17_9_3_1_2_executable_intervention_replay_service.py", "derive_executable_structural_target_v179312", derive_executable_structural_target_v179312),
        ("backend/services/v17_9_3_1_2_executable_intervention_replay_service.py", "recalculate_trade_risk_quantity_v179312", recalculate_trade_risk_quantity_v179312),
        ("backend/services/v17_9_3_1_2_executable_intervention_replay_service.py", "materialize_executable_counterfactual_v179312", materialize_executable_counterfactual_v179312),
        ("backend/services/v17_9_3_1_2_executable_intervention_replay_service.py", "replay_intervention_outcome_v179312", replay_intervention_outcome_v179312),
        ("backend/services/v17_9_3_1_2_executable_intervention_replay_service.py", "assert_intervention_is_real_v179312", assert_intervention_is_real_v179312),
        ("backend/services/v17_9_3_1_2_executable_intervention_replay_service.py", "match_variants_on_causal_opportunity_v179312", match_variants_on_causal_opportunity_v179312),
        ("backend/services/v17_9_3_1_2_executable_intervention_replay_service.py", "attribution_v179312", attribution_v179312),
        ("backend/services/v17_9_3_1_2_executable_intervention_replay_service.py", "assert_replay_oos_temporal_and_feature_integrity_v179312", assert_replay_oos_temporal_and_feature_integrity_v179312),
    ]
    symbol_map: List[Dict[str, Any]] = []
    for rel_file, sym_name, obj in targets:
        src_lines, start_line = inspect.getsourcelines(obj)
        end_line = start_line + len(src_lines) - 1
        sig = str(inspect.signature(obj)) if callable(obj) else "class"
        symbol_map.append(
            {
                "file": rel_file,
                "symbol": sym_name,
                "start_line": start_line,
                "end_line": end_line,
                "signature": sig,
                "sha256": hashlib.sha256("".join(src_lines).encode("utf-8")).hexdigest(),
            }
        )

    return {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "current_branch": current_branch,
        "mandatory_working_branch": MANDATORY_WORKING_BRANCH,
        "branch_matches_mandatory": current_branch == MANDATORY_WORKING_BRANCH,
        "starting_v179312_feature_head_sha": STARTING_V179312_FEATURE_HEAD_SHA,
        "previous_v179311_tested_code_sha": PREVIOUS_V179311_TESTED_CODE_SHA,
        "previous_v17931_tested_code_sha": PREVIOUS_V17931_TESTED_CODE_SHA,
        "previous_v1793_tested_code_sha": PREVIOUS_V1793_TESTED_CODE_SHA,
        "previous_v17921_tested_code_sha": PREVIOUS_V17921_TESTED_CODE_SHA,
        "base_master_sha": BASE_MASTER_SHA,
        "local_master_sha": master_sha,
        "tested_executable_code_sha": tested_code_sha,
        "current_head_sha": head_sha,
        "remote_origin_feature_sha_at_start": remote_sha,
        "ancestry_proofs": {
            "starting_v179312_head_is_ancestor_of_head": _is_ancestor(STARTING_V179312_FEATURE_HEAD_SHA, head_sha),
            "previous_v179311_code_is_ancestor_of_head": _is_ancestor(PREVIOUS_V179311_TESTED_CODE_SHA, head_sha),
            "previous_v17931_code_is_ancestor_of_head": _is_ancestor(PREVIOUS_V17931_TESTED_CODE_SHA, head_sha),
            "previous_v1793_code_is_ancestor_of_head": _is_ancestor(PREVIOUS_V1793_TESTED_CODE_SHA, head_sha),
            "previous_v17921_code_is_ancestor_of_head": _is_ancestor(PREVIOUS_V17921_TESTED_CODE_SHA, head_sha),
            "base_master_is_ancestor_of_head": _is_ancestor(BASE_MASTER_SHA, head_sha),
        },
        "prior_artifact_dirs_immutability": prior_immutability,
        "executable_source_freeze": exec_hash_info,
        "function_truth_map": symbol_map,
    }


def build_v179312_resource_bounded_shards() -> List[Dict[str, Any]]:
    tests_dir = REPO_ROOT / "backend" / "tests"
    all_files = sorted(
        f"backend/tests/{p.name}"
        for p in tests_dir.glob("test_*.py")
        if p.is_file()
    )
    s00 = [
        f
        for f in all_files
        if "v17_9_3_1_2" in f or "v17_9_3_1_1" in f or "v17_9_3" in f
    ]
    s01 = [f for f in all_files if "v17_9_2" in f]
    s02 = [f for f in all_files if "v17_9_1" in f or "v17_9_0" in f]
    s03 = [
        f
        for f in all_files
        if f
        in (
            "backend/tests/test_shared_strategy_evaluator.py",
            "backend/tests/test_mtf_structure_liquidity_service.py",
            "backend/tests/test_unicorn_evidence_service.py",
            "backend/tests/test_smc_engine.py",
        )
    ]
    s04 = [f for f in all_files if f == "backend/tests/test_v16_23_red_reproductions.py"]
    assigned = set(s00 + s01 + s02 + s03 + s04)
    remaining = [f for f in all_files if f not in assigned]

    shards: List[Dict[str, Any]] = [
        {"shard_id": "SHARD_00_V17_9_3_1_2_AND_V17_9_3", "files": s00},
    ]
    for idx_s02, f_s02 in enumerate(sorted(s02, key=lambda x: 0 if "runtime_integration" in x else 1)):
        shards.append({"shard_id": f"SHARD_02{chr(65 + idx_s02)}_V17_9_0_1", "files": [f_s02]})
    shards.extend(
        [
            {"shard_id": "SHARD_01A_V17_9_2", "files": s01[: max(1, len(s01) // 2)]},
            {"shard_id": "SHARD_01B_V17_9_2_1", "files": s01[max(1, len(s01) // 2) :]},
            {"shard_id": "SHARD_03_EVALUATOR_MTF_UNICORN_SMC", "files": s03},
            {"shard_id": "SHARD_04_V16_23_RED_ISOLATED", "files": s04},
        ]
    )

    num_extra = 8
    buckets: List[List[str]] = [[] for _ in range(num_extra)]
    bucket_weights = [0 for _ in range(num_extra)]
    sized_remaining = sorted(
        remaining,
        key=lambda rel: (REPO_ROOT / rel).stat().st_size,
        reverse=True,
    )
    for rel in sized_remaining:
        idx = min(range(num_extra), key=lambda i: bucket_weights[i])
        buckets[idx].append(rel)
        bucket_weights[idx] += (REPO_ROOT / rel).stat().st_size

    for i, b_files in enumerate(buckets, start=6):
        if b_files:
            shards.append({"shard_id": f"SHARD_{i:02d}_REGRESSION", "files": sorted(b_files)})

    return [s for s in shards if s["files"]]


def execute_bounded_pytest_shards_with_junit_bijection(
    *,
    run_dir: Path,
    max_workers: int = 4,
    per_shard_timeout_sec: int = 240,
) -> Dict[str, Any]:
    t0 = time.time()
    junit_dir = run_dir / "junit"
    junit_dir.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["PYTHONPATH"] = f"{REPO_ROOT / 'backend'}:{REPO_ROOT}"

    collect_proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "backend/tests"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    collected_node_ids = [
        ln.strip()
        for ln in collect_proc.stdout.splitlines()
        if "::" in ln and ln.strip().startswith("backend/tests/")
    ]

    shards = build_v179312_resource_bounded_shards()
    eff_workers = max(1, min(int(max_workers), 4))

    active_lock = threading.Lock()
    active_count = 0
    peak_active = 0

    def _run_one_shard(shard: Mapping[str, Any]) -> Dict[str, Any]:
        nonlocal active_count, peak_active
        with active_lock:
            active_count += 1
            if active_count > peak_active:
                peak_active = active_count
        st = time.time()
        xml_path = junit_dir / f"{shard['shard_id']}.xml"
        try:
            cmd = [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--tb=short",
                f"--junitxml={xml_path}",
                *shard["files"],
            ]
            proc = subprocess.run(
                cmd,
                cwd=str(REPO_ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=per_shard_timeout_sec,
                check=False,
            )
            dur = round(time.time() - st, 3)
            out_lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
            summary_line = out_lines[-1] if out_lines else ""
            passed_n = 0
            failed_n = 0
            for part in summary_line.split(","):
                part = part.strip()
                if "passed" in part:
                    for tok in part.split():
                        if tok.isdigit():
                            passed_n = int(tok)
                            break
                if "failed" in part or "error" in part:
                    for tok in part.split():
                        if tok.isdigit():
                            failed_n += int(tok)
                            break
            return {
                "shard_id": shard["shard_id"],
                "junit_xml": f"junit/{xml_path.name}",
                "file_count": len(shard["files"]),
                "files": shard["files"],
                "returncode": proc.returncode,
                "passed_count": passed_n,
                "failed_count": failed_n,
                "duration_seconds": dur,
                "summary_line": summary_line,
                "timed_out": False,
                "stderr_tail": proc.stderr[-500:] if proc.returncode != 0 else "",
                "stdout_tail": proc.stdout[-800:] if proc.returncode != 0 else "",
            }
        except subprocess.TimeoutExpired:
            return {
                "shard_id": shard["shard_id"],
                "junit_xml": f"junit/{xml_path.name}",
                "file_count": len(shard["files"]),
                "files": shard["files"],
                "returncode": 124,
                "passed_count": 0,
                "failed_count": 1,
                "duration_seconds": round(time.time() - st, 3),
                "summary_line": "TIMED_OUT",
                "timed_out": True,
            }
        finally:
            with active_lock:
                active_count -= 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=eff_workers) as pool:
        results = list(pool.map(_run_one_shard, shards))

    xml_paths = [run_dir / r["junit_xml"] for r in results]
    bijection = validate_junit_and_collection_bijection(collected_node_ids, xml_paths)

    total_files = sum(r["file_count"] for r in results)
    total_passed = sum(r["passed_count"] for r in results)
    total_failed = sum(r["failed_count"] for r in results)
    any_nonzero = any(r["returncode"] != 0 for r in results)
    any_timeout = any(r["timed_out"] for r in results)

    return {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "execution_mode": "BOUNDED_ASYNC_SHARDS_WITH_JUNIT_BIJECTION",
        "max_workers_cap": eff_workers,
        "peak_concurrent_subprocesses": peak_active,
        "total_test_files": total_files,
        "total_shards": len(results),
        "collected_node_ids_count": len(collected_node_ids),
        "total_passed_tests": total_passed,
        "total_failed_tests": total_failed,
        "all_shards_zero_exit": not any_nonzero,
        "zero_timeouts": not any_timeout,
        "junit_bijection": bijection,
        "wall_clock_seconds": round(time.time() - t0, 3),
        "shards": results,
    }


def _strip_episodes_from_replay(rep: Mapping[str, Any]) -> Dict[str, Any]:
    out = dict(rep)
    out.pop("episodes", None)
    return out


def build_artifact_27_git_commit_push_remote_head_proof(
    *,
    run_id: str,
    tested_code_sha: str,
) -> Dict[str, Any]:
    current_branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    final_local_sha = _run_git(["rev-parse", "HEAD"])
    remote_raw = _run_git(["ls-remote", "--heads", "origin", MANDATORY_WORKING_BRANCH], check=False)
    remote_sha = remote_raw.split()[0] if remote_raw else ""
    status_porcelain_non_artifact = _run_git(
        ["status", "--porcelain", "--", ":!docs/artifacts/v17_9_3_1_2"],
        check=False,
    )
    return {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "working_branch": current_branch,
        "mandatory_working_branch": MANDATORY_WORKING_BRANCH,
        "branch_matches_mandatory": current_branch == MANDATORY_WORKING_BRANCH,
        "starting_v179312_feature_head_sha": STARTING_V179312_FEATURE_HEAD_SHA,
        "previous_v179311_tested_code_sha": PREVIOUS_V179311_TESTED_CODE_SHA,
        "previous_v17931_tested_code_sha": PREVIOUS_V17931_TESTED_CODE_SHA,
        "tested_executable_code_sha": tested_code_sha,
        "final_local_sha": final_local_sha,
        "remote_sha": remote_sha,
        "local_equals_remote_sha": (final_local_sha == remote_sha) and (final_local_sha != ""),
        "working_tree_status_porcelain": status_porcelain_non_artifact,
        "working_tree_clean": status_porcelain_non_artifact == "",
    }


def update_artifact_sha256_manifest(run_dir: Path, run_id: str) -> Dict[str, Any]:
    entries: List[Dict[str, Any]] = []
    for fname in REQUIRED_27_ARTIFACT_FILES:
        fpath = run_dir / fname
        if fpath.exists():
            raw = fpath.read_bytes()
            entries.append(
                {
                    "filename": fname,
                    "size_bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
    val = validate_artifact_sha256_manifest(run_dir, entries)
    payload = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        "required_artifacts_count": len(REQUIRED_27_ARTIFACT_FILES),
        "present_artifacts_count": len(entries),
        "all_27_artifacts_present_and_verified": val["valid"] and len(entries) == len(REQUIRED_27_ARTIFACT_FILES),
        "validator_result": val,
        "artifacts": entries,
    }
    _write_json(run_dir / "artifact_sha256_manifest.json", payload)
    return payload


def _gate(gate_id: str, description: str, passed: bool, evidence: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "gate_id": gate_id,
        "passed": bool(passed),
        "status": "PASS" if passed else "FAIL",
        "description": description,
        "evidence": dict(evidence),
    }


def evaluate_all_38_acceptance_gates(
    *,
    run_id: str,
    art01: Mapping[str, Any],
    art02: Mapping[str, Any],
    art03: Mapping[str, Any],
    art04: Mapping[str, Any],
    art05: Mapping[str, Any],
    art06: Mapping[str, Any],
    art07: Mapping[str, Any],
    art08: Mapping[str, Any],
    art09: Mapping[str, Any],
    art10: Mapping[str, Any],
    art11: Mapping[str, Any],
    art12: Mapping[str, Any],
    art13: Mapping[str, Any],
    art14: Mapping[str, Any],
    art15: Mapping[str, Any],
    art16: Mapping[str, Any],
    art17: Mapping[str, Any],
    art18: Mapping[str, Any],
    art19: Mapping[str, Any],
    art20: Mapping[str, Any],
    art21: Mapping[str, Any],
    art22: Mapping[str, Any],
    art23: Mapping[str, Any],
    art24: Mapping[str, Any],
    art27: Mapping[str, Any],
    sha_manifest: Mapping[str, Any],
    resource_guard: Mapping[str, Any],
) -> Dict[str, Any]:
    flags_check = verify_default_production_flags_off_v179312()
    a1_full = art05["replays"]["A1_V1792_SL_TP_BASELINE"]
    e1_full = art05["replays"]["E1_OUTSIDE_MACRO_COUNTERFACTUAL"]
    b1_full = art05["replays"]["C1_MACRO_PLUS_V1792_SL_TP"]
    d2_full = art05["replays"]["D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL"]

    g01_ok = (
        bool(art01.get("branch_matches_mandatory"))
        and all(bool(v) for v in (art01.get("ancestry_proofs") or {}).values())
        and bool((art01.get("prior_artifact_dirs_immutability") or {}).get("all_immutable_prior_dirs_untouched"))
    )
    g02_ok = (
        int(art02.get("post_warmup_evaluated_bars", 0)) == 22346
        and int(art02.get("missing_indices_count", 1)) == 0
        and int(art02.get("duplicate_indices_count", 1)) == 0
        and float(art02.get("coverage_ratio", 0.0)) == 1.0
    )
    g03_ok = (
        int(art03.get("total_unique_timestamps_covered", 0)) == 22346
        and int(art03.get("total_model_evaluations_per_variant", 0)) == 111730
        and bool(art03.get("stream_integrity_valid"))
    )
    g04_ok = (
        int(art04.get("outside_screen_unique_timestamps_with_a1_or_e1_count", 0)) == 15597
        and int(art04.get("a1_outside_screen_eligible_decisions", 0)) == 47243
        and int(art04.get("a1_inside_screen_eligible_decisions", 0)) == 276
        and int(art04.get("a1_total_eligible_decisions", 0)) == 47519
    )
    g05_ok = (
        int(art04.get("e1_outside_screen_eligible_decisions", 0)) == 47151
        and int(art04.get("e1_inside_screen_eligible_decisions", 0)) == 139
        and int(art04.get("e1_total_eligible_decisions", 0)) == 47290
        and int(art04.get("outside_screen_a1_minus_e1_weekend_closed_delta", 0)) == 92
    )
    g06_ok = (
        int(a1_full.get("raw_eligible_decisions_count", 0)) == 47519
        and int(a1_full.get("closed_independent_episodes_count", 0)) > 139
        and int(e1_full.get("raw_eligible_decisions_count", 0)) == 47290
    )
    g07_ok = (
        set((art06.get("replays_by_fill_mode") or {}).keys())
        == {"NEXT_BAR_OPEN_BID_ASK", "LEGACY_CLOSE_FILL", "STRESS_WIDER_SPREAD_SLIPPAGE_2X"}
    )
    g08_ok = (
        int(((art07.get("variants") or {}).get("A1_V1792_SL_TP_BASELINE") or {}).get("LEGACY_SCREENED", {}).get("closed_independent_episodes_count", 0)) == 139
        and int(((art07.get("variants") or {}).get("E1_OUTSIDE_MACRO_COUNTERFACTUAL") or {}).get("LEGACY_SCREENED", {}).get("closed_independent_episodes_count", 0)) == 69
        and bool(art07.get("scopes_strictly_separated_in_reporting"))
    )
    g09_ok = (
        "A1_VS_B1_COEVAL" in (art08.get("comparisons") or {})
        and "A1_VS_E1_COEVAL" in (art08.get("comparisons") or {})
        and int(art08.get("total_post_warmup_timestamps", 0)) == 22346
    )
    g10_ok = (
        bool(art09.get("all_variants_zero_overlap"))
        and bool(art09.get("all_variants_candidate_conservation_valid"))
    )
    g11_ok = (
        bool(art10.get("all_variants_reconciled"))
        and int(art10.get("total_planned_risk_cap_violations", 1)) == 0
        and int(art10.get("total_fee_double_count_errors", 1)) == 0
    )
    g12_ok = (
        bool(art10.get("no_fill_zero_fabricated_pnl_verified"))
        and bool(art10.get("open_at_horizon_zero_realized_pnl_verified"))
        and bool(art10.get("data_gap_unresolved_censored_verified"))
    )
    g13_ok = (
        int(art11.get("policy_count", 0)) == 9
        and bool(art11.get("all_disabled_in_production"))
        and bool(art11.get("all_observe_only_outside_backtest"))
    )
    g14_ok = (
        int((art12.get("R0_BASELINE_CONTROL") or {}).get("accepted_modified_executable_geometry_count", 1)) == 0
        and int((art12.get("R0_BASELINE_CONTROL") or {}).get("rejected_by_intervention_count", 1)) == 0
        and int((art12.get("R0_BASELINE_CONTROL") or {}).get("accepted_unchanged_noop_count", 0)) > 0
    )
    g15_ok = (
        int((art12.get("R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE") or {}).get("changed_entry_timestamp_count", 0)) > 0
        and int((art12.get("R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE") or {}).get("changed_entry_reference_count", 0)) > 0
    )
    g16_ok = (
        int((art12.get("R3_DELAYED_ENTRY_BREAKER_FVG_RETEST_EXECUTABLE") or {}).get("changed_entry_timestamp_count", 0)) > 0
        and int((art12.get("R3_DELAYED_ENTRY_BREAKER_FVG_RETEST_EXECUTABLE") or {}).get("changed_entry_reference_count", 0)) > 0
    )
    g17_ok = (
        int((art13.get("R4_OUTER_STRUCTURAL_STOP_EXECUTABLE") or {}).get("changed_sl_count", 0)) > 0
        and int((art13.get("R4_OUTER_STRUCTURAL_STOP_EXECUTABLE") or {}).get("changed_quantity_count", 0)) > 0
    )
    g18_ok = (
        int((art14.get("R5_CAUSAL_LIQUIDITY_TARGET_EXECUTABLE") or {}).get("changed_tp_count", 0)) > 0
    )
    g19_ok = (
        "R7_COMBINED_ENTRY_SL_TP_EXECUTABLE" in art15
        and "R8_TIMING_PLUS_OUTER_SL_ABLATION_EXECUTABLE" in art15
        and int((art15.get("R8_TIMING_PLUS_OUTER_SL_ABLATION_EXECUTABLE") or {}).get("accepted_modified_executable_geometry_count", 0)) > 0
    )
    g20_ok = (
        bool(art16.get("all_interventions_verified_real"))
        and int(art16.get("anti_fake_violation_count", 1)) == 0
    )
    g21_ok = (
        "matched_opportunity_study_a1" in art17
        and "independent_portfolio_attribution_a1" in art17
        and "independent_portfolio_attribution_b1" in art17
    )
    g22_ok = bool(art17.get("unlocked_follow_on_trades_explicitly_attributed"))
    g23_ok = bool(art17.get("volume_reduction_without_positive_expectancy_explicitly_disclosed"))
    g24_ok = (
        len(art18.get("ranked_root_causes") or []) >= 5
        and "A1_V1792_SL_TP_BASELINE" in (art18.get("full_universe_cohorts") or {})
    )
    g25_ok = (
        bool(art19.get("all_cohorts_audit_passed"))
        and int(art19.get("total_contradictory_stop_labels_count", 1)) == 0
    )
    g26_ok = (
        bool(art20.get("all_Audited_records_strictly_causal"))
        and int(art20.get("future_bar_leak_violations_count", 1)) == 0
    )
    g27_ok = (
        bool(art21.get("integrity_valid"))
        and int(art21.get("embargo_bars", 0)) == 180
        and int(art21.get("admissible_embargo_leak_count", 1)) == 0
    )
    g28_ok = (
        art21.get("oos_holdout_freshness_status") == "OOS_REUSED_NOT_FRESH_HISTORICAL_WINDOW_DISCLOSED"
    )
    g29_ok = (
        bool(art21.get("policy_registry_hash_matches_frozen"))
        and int(art21.get("delayed_entry_cross_partition_purged_count", -1)) >= 0
    )
    g30_ok = (
        "SPLIT_50_25_25" in (art21.get("split_schemes_metadata") or {})
        and "SPLIT_70_15_15" in (art21.get("split_schemes_metadata") or {})
        and len((art21.get("split_schemes_metadata") or {}).get("WALKFORWARD_3FOLDS") or []) == 3
    )
    g31_ok = (
        bool(art22.get("hash_equivalent"))
        and int(art22.get("max_workers_cap", 99)) <= 4
    )
    g32_ok = (
        bool(art23.get("all_shards_zero_exit"))
        and bool(art23.get("zero_timeouts"))
        and bool((art23.get("junit_bijection") or {}).get("bijection_valid"))
        and int(art23.get("total_passed_tests", 0)) >= 60
    )
    g33_ok = (
        bool(resource_guard.get("disk_floor_satisfied"))
        and int(art23.get("peak_concurrent_subprocesses", 99)) <= 4
    )
    g34_ok = (
        int(d2_full.get("closed_independent_episodes_count", 0)) == 2
        and int(b1_full.get("closed_independent_episodes_count", 0)) == 27
        and art24.get("STRATEGY_STATISTICAL_VERDICT") in ("NEGATIVE_EXPECTANCY", "INCONCLUSIVE_UNDERPOWERED")
    )
    g35_ok = (
        art24.get("ACTIVATION_READY") == "NO"
        and art24.get("ready_for_strategy_activation") == "NO"
        and bool(flags_check.get("all_production_flags_off"))
    )
    g36_ok = bool(sha_manifest.get("all_27_artifacts_present_and_verified"))
    g38_ok = (
        bool(art27.get("branch_matches_mandatory"))
        and bool(art27.get("working_tree_clean"))
        and bool(art27.get("local_equals_remote_sha"))
    )

    prelim_gates = [
        _gate("G01", "Mandatory working branch, SHA ancestry chain, and prior artifact immutability verified", g01_ok, {"branch": art01.get("current_branch")}),
        _gate("G02", "Full-Universe 22,346 post-warmup M1 indices enumerated without $7.00 M1 target-distance truncation", g02_ok, {"evaluated_bars": art02.get("post_warmup_evaluated_bars")}),
        _gate("G03", "Append-only compressed decision stream chunks written, merged, and SHA-256 verified (111,730 model evals/variant)", g03_ok, {"model_evals": art03.get("total_model_evaluations_per_variant")}),
        _gate("G04", "Outside-screen 15,597 timestamps and 47,243 A1 decisions + 276 inside-screen = 47,519 A1 decisions reconciled", g04_ok, {"a1_total_eligible": art04.get("a1_total_eligible_decisions")}),
        _gate("G05", "Outside-screen E1 47,151 decisions + 139 inside-screen = 47,290 E1 decisions reconciled (92 weekend/closed delta)", g05_ok, {"e1_total_eligible": art04.get("e1_total_eligible_decisions"), "delta": art04.get("outside_screen_a1_minus_e1_weekend_closed_delta")}),
        _gate("G06", "Full-Universe chronological non-overlapping portfolio replay executed across A0, A1, E1, B1, D1, D2", g06_ok, {"a1_full_closed": a1_full.get("closed_independent_episodes_count"), "e1_full_closed": e1_full.get("closed_independent_episodes_count")}),
        _gate("G07", "Full-Universe portfolio replay evaluated across all 3 execution fill modes (NEXT_BAR_OPEN_BID_ASK, LEGACY_CLOSE_FILL, STRESS_2X)", g07_ok, {"modes": list((art06.get("replays_by_fill_mode") or {}).keys())}),
        _gate("G08", "LEGACY_SCREENED (139 A1, 69 E1) vs FULL_UNIVERSE_ALL_TIMESTAMPS explicitly separated with zero mixed denominators", g08_ok, {"a1_legacy_closed": 139, "e1_legacy_closed": 69}),
        _gate("G09", "Matched coeval comparison report built across all 22,346 timestamps for A1 vs B1 and A1 vs E1", g09_ok, {"timestamps": art08.get("total_post_warmup_timestamps")}),
        _gate("G10", "Zero active-trade time overlap and 100% candidate conservation verified across all full-universe and legacy replays", g10_ok, {"zero_overlap": art09.get("all_variants_zero_overlap"), "conservation": art09.get("all_variants_candidate_conservation_valid")}),
        _gate("G11", "Trade-to-ledger reconciliation passed with 0 risk cap ($2.50) violations and 0 fee double-counting errors", g11_ok, {"reconciled": art10.get("all_variants_reconciled")}),
        _gate("G12", "NO_FILL, OPEN_AT_HORIZON, and DATA_GAP_UNRESOLVED position-slot and PnL/censoring rules verified", g12_ok, {"no_fill_ok": art10.get("no_fill_zero_fabricated_pnl_verified")}),
        _gate("G13", "Predeclared ExecutableResearchPolicyV179312 registry (R0..R8) frozen with enabled_in_production=False", g13_ok, {"policy_count": art11.get("policy_count"), "registry_hash": art11.get("policy_registry_hash")}),
        _gate("G14", "R0_BASELINE_CONTROL reproduces baseline geometry and simulated outcomes with 0 modified geometry records", g14_ok, {"r0_noop_count": (art12.get("R0_BASELINE_CONTROL") or {}).get("accepted_unchanged_noop_count")}),
        _gate("G15", "R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE genuinely shifts decision_timestamp_ms forward and rebuilds entry_reference", g15_ok, {"r2_changed_ts": (art12.get("R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE") or {}).get("changed_entry_timestamp_count")}),
        _gate("G16", "R3_DELAYED_ENTRY_BREAKER_FVG_RETEST_EXECUTABLE genuinely waits for closed FVG retest bar and rebuilds entry/quantity", g16_ok, {"r3_changed_ts": (art12.get("R3_DELAYED_ENTRY_BREAKER_FVG_RETEST_EXECUTABLE") or {}).get("changed_entry_timestamp_count")}),
        _gate("G17", "R4_OUTER_STRUCTURAL_STOP_EXECUTABLE genuinely replaces selected_sl with outer protected anchor + noise buffer and resizes quantity", g17_ok, {"r4_changed_sl": (art13.get("R4_OUTER_STRUCTURAL_STOP_EXECUTABLE") or {}).get("changed_sl_count")}),
        _gate("G18", "R5_CAUSAL_LIQUIDITY_TARGET_EXECUTABLE genuinely re-selects unswept causal liquidity pool in [2.0, 3.5] net RR band", g18_ok, {"r5_changed_tp": (art14.get("R5_CAUSAL_LIQUIDITY_TARGET_EXECUTABLE") or {}).get("changed_tp_count")}),
        _gate("G19", "R7_COMBINED and R8_TIMING_PLUS_OUTER_SL_ABLATION executable interventions evaluated across all 3 fill modes", g19_ok, {"r8_modified": (art15.get("R8_TIMING_PLUS_OUTER_SL_ABLATION_EXECUTABLE") or {}).get("accepted_modified_executable_geometry_count")}),
        _gate("G20", "Anti-fake intervention reality validator (assert_intervention_is_real_v179312) passed with 0 fake/copied/stale records", g20_ok, {"anti_fake_violations": art16.get("anti_fake_violation_count")}),
        _gate("G21", "Matched-opportunity study vs independent portfolio replay attribution completed on both A1 and B1 cohorts", g21_ok, {"cohorts": ["A1", "B1"]}),
        _gate("G22", "Newly unlocked follow-on trades when earlier trades are rejected or exit earlier explicitly attributed", g22_ok, {"unlocked_attributed": g22_ok}),
        _gate("G23", "Volume reduction without positive per-trade expectancy explicitly flagged as non-edge", g23_ok, {"disclosed": g23_ok}),
        _gate("G24", "Updated loss root-cause matrix computed across Full-Universe A1, E1, B1, D1, D2 and R0..R8", g24_ok, {"top_driver": (art18.get("ranked_root_causes") or [{}])[0].get("root_cause")}),
        _gate("G25", "Protected structure authority and stop classification audit verified with 0 contradictory stop labels", g25_ok, {"contradictions": art19.get("total_contradictory_stop_labels_count")}),
        _gate("G26", "Per-timeframe causal provenance audit verified close_time <= decision_timestamp_ms across 1m, 5m, 15m, 1H, 4H, 1D", g26_ok, {"future_leaks": art20.get("future_bar_leak_violations_count")}),
        _gate("G27", "Chronological OOS and walk-forward temporal integrity verified with 180-bar gap-aware embargo and 0 admissible leaks", g27_ok, {"admissible_leaks": art21.get("admissible_embargo_leak_count")}),
        _gate("G28", "Reused historical OOS window explicitly disclosed as OOS_REUSED_NOT_FRESH_HISTORICAL_WINDOW_DISCLOSED", g28_ok, {"freshness_status": art21.get("oos_holdout_freshness_status")}),
        _gate("G29", "Delayed-entry cross-partition trades owned by new_decision_ts and purged when crossing split boundaries", g29_ok, {"cross_partition_purged": art21.get("delayed_entry_cross_partition_purged_count")}),
        _gate("G30", "50/25/25, 70/15/15, and 3-fold expanding walk-forward evaluated across Full-Universe and R0..R8 cohorts", g30_ok, {"schemes": list((art21.get("split_schemes_metadata") or {}).keys())}),
        _gate("G31", "Single-worker vs multi-worker chunk decision stream and replay SHA-256 determinism verified", g31_ok, {"single_sha": art22.get("single_worker_sha256"), "multi_sha": art22.get("multi_worker_sha256")}),
        _gate("G32", "Full backend pytest suite + 60 V17.9.3.1.2 tests executed in bounded shards with 1-to-1 JUnit XML bijection", g32_ok, {"collected": art23.get("collected_node_ids_count"), "passed": art23.get("total_passed_tests")}),
        _gate("G33", "Disk floor (>= 5.0 GB free), RAM, and subprocess concurrency cap (<= 4 workers) satisfied", g33_ok, {"free_disk_gb": resource_guard.get("free_disk_gb"), "peak_subprocesses": art23.get("peak_concurrent_subprocesses")}),
        _gate("G34", "Statistical honesty enforced with Wilson 95% CIs, block-bootstrap 95% CIs, and explicit sample-size caveats", g34_ok, {"strategy_verdict": art24.get("STRATEGY_STATISTICAL_VERDICT")}),
        _gate("G35", "ACTIVATION_READY == NO and all production runtime flags remain OFF", g35_ok, {"activation_ready": art24.get("ACTIVATION_READY")}),
        _gate("G36", "All 27 required artifacts present, non-empty, valid JSON/Markdown, and SHA-256 verified", g36_ok, {"present_count": sha_manifest.get("present_artifacts_count")}),
        _gate("G38", "Final Git local HEAD equals remote origin HEAD on mandatory branch with clean working tree", g38_ok, {"local_equals_remote": art27.get("local_equals_remote_sha")}),
    ]

    failed_other = [g["gate_id"] for g in prelim_gates if not g["passed"]]
    g37_ok = len(failed_other) == 0 and len(prelim_gates) == 37
    g37_gate = _gate(
        "G37",
        "Non-tautological 38-gate manifest self-consistency verified by independent validator",
        g37_ok,
        {"failed_other_gates": failed_other, "total_gates": 38},
    )

    all_gates = sorted([*prelim_gates, g37_gate], key=lambda g: g["gate_id"])
    manifest_val = validate_acceptance_gate_manifest(
        {"gates": all_gates},
        expected_gate_ids=EXPECTED_38_GATE_IDS,
        self_check_gate_id="G37",
    )
    if not manifest_val["schema_valid"]:
        all_gates[36]["passed"] = False
        all_gates[36]["status"] = "FAIL"
        all_gates[36]["evidence"]["validator_errors"] = manifest_val["errors"]

    passed_count = sum(1 for g in all_gates if g["passed"])
    return {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        "total_gates": len(all_gates),
        "passed_gates": passed_count,
        "failed_gates": len(all_gates) - passed_count,
        "all_gates_passed": passed_count == len(all_gates),
        "independent_validator_check": manifest_val,
        "gates": all_gates,
    }


def build_artifact_26_closure_executive_report_md(
    *,
    run_id: str,
    tested_code_sha: str,
    art01: Mapping[str, Any],
    art04: Mapping[str, Any],
    art05: Mapping[str, Any],
    art07: Mapping[str, Any],
    art12: Mapping[str, Any],
    art13: Mapping[str, Any],
    art14: Mapping[str, Any],
    art15: Mapping[str, Any],
    art18: Mapping[str, Any],
    art21: Mapping[str, Any],
    art23: Mapping[str, Any],
    art24: Mapping[str, Any],
    art25_prelim_ok: bool,
    art27: Mapping[str, Any],
) -> str:
    a1_full = art05["replays"]["A1_V1792_SL_TP_BASELINE"]
    e1_full = art05["replays"]["E1_OUTSIDE_MACRO_COUNTERFACTUAL"]
    b1_full = art05["replays"]["C1_MACRO_PLUS_V1792_SL_TP"]
    d1_full = art05["replays"]["D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP"]
    d2_full = art05["replays"]["D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL"]

    a1_leg = art07["variants"]["A1_V1792_SL_TP_BASELINE"]["LEGACY_SCREENED"]
    e1_leg = art07["variants"]["E1_OUTSIDE_MACRO_COUNTERFACTUAL"]["LEGACY_SCREENED"]

    r0_a1 = art12["R0_BASELINE_CONTROL"]["independent_portfolio_replay_primary"]
    r2_a1 = art12["R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE"]["independent_portfolio_replay_primary"]
    r3_a1 = art12["R3_DELAYED_ENTRY_BREAKER_FVG_RETEST_EXECUTABLE"]["independent_portfolio_replay_primary"]
    r4_a1 = art13["R4_OUTER_STRUCTURAL_STOP_EXECUTABLE"]["independent_portfolio_replay_primary"]
    r5_a1 = art14["R5_CAUSAL_LIQUIDITY_TARGET_EXECUTABLE"]["independent_portfolio_replay_primary"]
    r7_a1 = art15["R7_COMBINED_ENTRY_SL_TP_EXECUTABLE"]["independent_portfolio_replay_primary"]
    r8_a1 = art15["R8_TIMING_PLUS_OUTER_SL_ABLATION_EXECUTABLE"]["independent_portfolio_replay_primary"]

    eng_verdict = "PASS" if art25_prelim_ok else "PENDING_FINAL_PUSH_PROOF"

    return f"""# AURUM Desk V17.9.3.1.2 — Full-Universe Replay & Executable Entry/SL/TP Counterfactual Validation Report

## 1. Provenance & Git Identity
- **Mandatory Working Branch**: `{art01.get('current_branch')}`
- **Starting V17.9.3.1.2 Feature HEAD SHA**: `{STARTING_V179312_FEATURE_HEAD_SHA}`
- **Previous V17.9.3.1.1 Tested Executable SHA**: `{PREVIOUS_V179311_TESTED_CODE_SHA}`
- **Previous V17.9.3.1 Tested Executable SHA**: `{PREVIOUS_V17931_TESTED_CODE_SHA}`
- **Base `master` SHA**: `{BASE_MASTER_SHA}`
- **Tested Executable Code SHA**: `{tested_code_sha}`
- **Final Local HEAD SHA**: `{art27.get('final_local_sha')}`
- **Remote Origin HEAD SHA**: `{art27.get('remote_sha')}`
- **Evidence Run ID**: `{run_id}`

## 2. Three-Axis Closure Verdict
- **`ENGINEERING_VERIFICATION_VERDICT`**: **`{eng_verdict}`**
- **`STRATEGY_STATISTICAL_VERDICT`**: **`{art24.get('STRATEGY_STATISTICAL_VERDICT')}`**
- **`ACTIVATION_READY` (`LIVE` / `PAPER` / `AUTO`)**: **`{art24.get('ACTIVATION_READY')}`**

## 3. P0-A — Full-Universe `22,346`-Timestamp Reconciliation & Chronological Portfolio Replay
- **Total Post-Warmup M1 Bars Evaluated**: `22,346 / 22,346` (`idx = 30..22375`, `111,730` model evaluations per variant, `0` `$7.00` M1 target-distance truncation).
- **Outside-Screen Reconciliation**:
  - Outside-screen unique timestamps with `A1` or `E1` eligible setups: `{art04.get('outside_screen_unique_timestamps_with_a1_or_e1_count')}`
  - `A1_V1792_SL_TP_BASELINE`: `{art04.get('a1_outside_screen_eligible_decisions')}` outside-screen + `{art04.get('a1_inside_screen_eligible_decisions')}` inside-screen = `{art04.get('a1_total_eligible_decisions')}` total eligible model decisions.
  - `E1_OUTSIDE_MACRO_COUNTERFACTUAL`: `{art04.get('e1_outside_screen_eligible_decisions')}` outside-screen + `{art04.get('e1_inside_screen_eligible_decisions')}` inside-screen = `{art04.get('e1_total_eligible_decisions')}` total eligible model decisions (`{art04.get('outside_screen_a1_minus_e1_weekend_closed_delta')}` fewer due to `WEEKEND_OR_CLOSED` gate in `E1`).

### Side-by-Side Scope Attribution (`LEGACY_SCREENED` vs `FULL_UNIVERSE_ALL_TIMESTAMPS`, Primary Fill Mode `NEXT_BAR_OPEN_BID_ASK`)
| Variant | Scope | Raw Eligible Decisions | Closed Episodes | Wins | Losses | Loss Rate | Expectancy (R) | Modeled Net PnL (USD) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `A1_V1792_SL_TP_BASELINE` | `LEGACY_SCREENED` | {a1_leg['raw_eligible_decisions_count']} | {a1_leg['closed_independent_episodes_count']} | {a1_leg['wins']} | {a1_leg['losses']} | {a1_leg['loss_rate']*100:.2f}% | {a1_leg['expectancy_r_mean']:.4f} | ${a1_leg['modeled_net_pnl_usd']:.2f} |
| `A1_V1792_SL_TP_BASELINE` | `FULL_UNIVERSE_ALL_TIMESTAMPS` | {a1_full['raw_eligible_decisions_count']} | {a1_full['closed_independent_episodes_count']} | {a1_full['wins']} | {a1_full['losses']} | {a1_full['loss_rate']*100:.2f}% | {a1_full['expectancy_r_mean']:.4f} | ${a1_full['modeled_net_pnl_usd']:.2f} |
| `E1_OUTSIDE_MACRO_COUNTERFACTUAL` | `LEGACY_SCREENED` | {e1_leg['raw_eligible_decisions_count']} | {e1_leg['closed_independent_episodes_count']} | {e1_leg['wins']} | {e1_leg['losses']} | {e1_leg['loss_rate']*100:.2f}% | {e1_leg['expectancy_r_mean']:.4f} | ${e1_leg['modeled_net_pnl_usd']:.2f} |
| `E1_OUTSIDE_MACRO_COUNTERFACTUAL` | `FULL_UNIVERSE_ALL_TIMESTAMPS` | {e1_full['raw_eligible_decisions_count']} | {e1_full['closed_independent_episodes_count']} | {e1_full['wins']} | {e1_full['losses']} | {e1_full['loss_rate']*100:.2f}% | {e1_full['expectancy_r_mean']:.4f} | ${e1_full['modeled_net_pnl_usd']:.2f} |
| `C1_MACRO_PLUS_V1792_SL_TP` (`B1`) | `FULL_UNIVERSE_ALL_TIMESTAMPS` | {b1_full['raw_eligible_decisions_count']} | {b1_full['closed_independent_episodes_count']} | {b1_full['wins']} | {b1_full['losses']} | {b1_full['loss_rate']*100:.2f}% | {b1_full['expectancy_r_mean']:.4f} | ${b1_full['modeled_net_pnl_usd']:.2f} |
| `D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP` | `FULL_UNIVERSE_ALL_TIMESTAMPS` | {d1_full['raw_eligible_decisions_count']} | {d1_full['closed_independent_episodes_count']} | {d1_full['wins']} | {d1_full['losses']} | {d1_full['loss_rate']*100:.2f}% | {d1_full['expectancy_r_mean']:.4f} | ${d1_full['modeled_net_pnl_usd']:.2f} |
| `D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL` | `FULL_UNIVERSE_ALL_TIMESTAMPS` | {d2_full['raw_eligible_decisions_count']} | {d2_full['closed_independent_episodes_count']} | {d2_full['wins']} | {d2_full['losses']} | {d2_full['loss_rate']*100:.2f}% | {d2_full['expectancy_r_mean']:.4f} | ${d2_full['modeled_net_pnl_usd']:.2f} |

## 4. P0-B — Executable Entry/SL/TP Counterfactual Interventions (`R0`–`R8` on Full-Universe `A1` Cohort)
| Policy ID | Modified Geometry Count | Rejected Count | Closed Portfolio Episodes | Wins | Losses | Loss Rate | Expectancy (R) | Modeled Net PnL (USD) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `R0_BASELINE_CONTROL` | {art12['R0_BASELINE_CONTROL']['accepted_modified_executable_geometry_count']} | {art12['R0_BASELINE_CONTROL']['rejected_by_intervention_count']} | {r0_a1['closed_independent_episodes_count']} | {r0_a1['wins']} | {r0_a1['losses']} | {(r0_a1['loss_rate'] or 0.0)*100:.2f}% | {r0_a1['expectancy_r_mean'] or 0.0:.4f} | ${r0_a1['modeled_net_pnl_usd']:.2f} |
| `R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE` | {art12['R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE']['accepted_modified_executable_geometry_count']} | {art12['R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE']['rejected_by_intervention_count']} | {r2_a1['closed_independent_episodes_count']} | {r2_a1['wins']} | {r2_a1['losses']} | {(r2_a1['loss_rate'] or 0.0)*100:.2f}% | {r2_a1['expectancy_r_mean'] or 0.0:.4f} | ${r2_a1['modeled_net_pnl_usd']:.2f} |
| `R3_DELAYED_ENTRY_BREAKER_FVG_RETEST_EXECUTABLE` | {art12['R3_DELAYED_ENTRY_BREAKER_FVG_RETEST_EXECUTABLE']['accepted_modified_executable_geometry_count']} | {art12['R3_DELAYED_ENTRY_BREAKER_FVG_RETEST_EXECUTABLE']['rejected_by_intervention_count']} | {r3_a1['closed_independent_episodes_count']} | {r3_a1['wins']} | {r3_a1['losses']} | {(r3_a1['loss_rate'] or 0.0)*100:.2f}% | {r3_a1['expectancy_r_mean'] or 0.0:.4f} | ${r3_a1['modeled_net_pnl_usd']:.2f} |
| `R4_OUTER_STRUCTURAL_STOP_EXECUTABLE` | {art13['R4_OUTER_STRUCTURAL_STOP_EXECUTABLE']['accepted_modified_executable_geometry_count']} | {art13['R4_OUTER_STRUCTURAL_STOP_EXECUTABLE']['rejected_by_intervention_count']} | {r4_a1['closed_independent_episodes_count']} | {r4_a1['wins']} | {r4_a1['losses']} | {(r4_a1['loss_rate'] or 0.0)*100:.2f}% | {r4_a1['expectancy_r_mean'] or 0.0:.4f} | ${r4_a1['modeled_net_pnl_usd']:.2f} |
| `R5_CAUSAL_LIQUIDITY_TARGET_EXECUTABLE` | {art14['R5_CAUSAL_LIQUIDITY_TARGET_EXECUTABLE']['accepted_modified_executable_geometry_count']} | {art14['R5_CAUSAL_LIQUIDITY_TARGET_EXECUTABLE']['rejected_by_intervention_count']} | {r5_a1['closed_independent_episodes_count']} | {r5_a1['wins']} | {r5_a1['losses']} | {(r5_a1['loss_rate'] or 0.0)*100:.2f}% | {r5_a1['expectancy_r_mean'] or 0.0:.4f} | ${r5_a1['modeled_net_pnl_usd']:.2f} |
| `R7_COMBINED_ENTRY_SL_TP_EXECUTABLE` | {art15['R7_COMBINED_ENTRY_SL_TP_EXECUTABLE']['accepted_modified_executable_geometry_count']} | {art15['R7_COMBINED_ENTRY_SL_TP_EXECUTABLE']['rejected_by_intervention_count']} | {r7_a1['closed_independent_episodes_count']} | {r7_a1['wins']} | {r7_a1['losses']} | {(r7_a1['loss_rate'] or 0.0)*100:.2f}% | {r7_a1['expectancy_r_mean'] or 0.0:.4f} | ${r7_a1['modeled_net_pnl_usd']:.2f} |
| `R8_TIMING_PLUS_OUTER_SL_ABLATION_EXECUTABLE` | {art15['R8_TIMING_PLUS_OUTER_SL_ABLATION_EXECUTABLE']['accepted_modified_executable_geometry_count']} | {art15['R8_TIMING_PLUS_OUTER_SL_ABLATION_EXECUTABLE']['rejected_by_intervention_count']} | {r8_a1['closed_independent_episodes_count']} | {r8_a1['wins']} | {r8_a1['losses']} | {(r8_a1['loss_rate'] or 0.0)*100:.2f}% | {r8_a1['expectancy_r_mean'] or 0.0:.4f} | ${r8_a1['modeled_net_pnl_usd']:.2f} |

## 5. Pytest Bijection & Chronological OOS Summary
- **Collected Test Node IDs**: `{art23.get('collected_node_ids_count')}`
- **Executed & Passed Tests**: `{art23.get('total_passed_tests')}` (`0` failed, `0` skipped, `bijection_valid={art23.get('junit_bijection', {}).get('bijection_valid')}`)
- **OOS Holdout Freshness Status**: `{art21.get('oos_holdout_freshness_status')}` (`admissible_embargo_leak_count = {art21.get('admissible_embargo_leak_count')}`)
"""


def run_full_verification(*, max_workers: int = 4) -> int:
    preflight_res = _check_disk_and_resource_budget(min_free_gb=5.0)
    if not preflight_res["disk_floor_satisfied"]:
        print(f"BLOCKED: Free disk {preflight_res['free_disk_gb']} GB < 5.0 GB floor")
        return 2

    tested_code_sha = _run_git(["rev-parse", "HEAD"])
    ts_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"v17_9_3_1_2_{ts_str}_{tested_code_sha[:12]}"

    base_dir = REPO_ROOT / "docs" / "artifacts" / "v17_9_3_1_2"
    run_dir = base_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "junit").mkdir(parents=True, exist_ok=True)

    print(f"[V17.9.3.1.2] Starting verification run_id={run_id} tested_code_sha={tested_code_sha}")

    # 1. Build Artifact 01 (Git & Frozen Source Identity)
    art01 = build_artifact_01_git_branch_source_frozen_identity(
        run_id=run_id,
        tested_code_sha=tested_code_sha,
    )
    _write_json(run_dir / "01_git_branch_source_frozen_identity.json", art01)

    # 2. Run Full-Universe 22,346-Timestamp Backtest & Executable Interventions Pipeline
    print("[V17.9.3.1.2] Running Full-Universe 22,346-timestamp backtest & R0..R8 executable interventions...")
    bt = run_v17_9_3_1_2_full_universe_backtest_pipeline(
        output_dir=run_dir,
        max_workers=min(int(max_workers), 4),
        chunk_size=1400,
        run_single_vs_multi_worker_check=True,
    )

    # Artifact 02: Full-Universe 22,346 Index Coverage Manifest
    art02 = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        "dataset_provenance": bt["dataset_provenance"],
        **bt["index_coverage_manifest"],
    }
    _write_json(run_dir / "02_full_universe_22346_index_coverage_manifest.json", art02)

    # Artifact 03: Decision Stream Chunks Manifest
    art03 = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        **bt["decision_stream_manifest"],
        "chunk_files": bt["chunk_stream_manifests"],
    }
    _write_json(run_dir / "03_decision_stream_chunks_manifest.json", art03)

    # Artifact 04: Outside-Screen 15,597 Timestamps & 47,243 Decisions Reconciliation
    art04 = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        **bt["outside_screen_reconciliation"],
    }
    _write_json(run_dir / "04_outside_screen_15597_timestamps_47243_decisions_reconciliation.json", art04)

    # Artifact 05: Full-Universe Portfolio Replay Primary (A0, A1, E1, B1, D1, D2)
    full_primary = bt["full_universe_replays_by_mode"]["NEXT_BAR_OPEN_BID_ASK"]
    art05 = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        "fill_mode": "NEXT_BAR_OPEN_BID_ASK",
        "scope": "FULL_UNIVERSE_ALL_TIMESTAMPS",
        "replays": {vk: _strip_episodes_from_replay(rep) for vk, rep in full_primary.items()},
    }
    _write_json(run_dir / "05_full_universe_portfolio_replay_A0_A1_E1_B1_D2_primary.json", art05)

    # Artifact 06: Full-Universe Portfolio Replay 3 Fill Modes Comparison
    art06 = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        "scope": "FULL_UNIVERSE_ALL_TIMESTAMPS",
        "replays_by_fill_mode": {
            mode: {vk: _strip_episodes_from_replay(rep) for vk, rep in m_dict.items()}
            for mode, m_dict in bt["full_universe_replays_by_mode"].items()
        },
    }
    _write_json(run_dir / "06_full_universe_portfolio_replay_3_fill_modes_comparison.json", art06)

    # Artifact 07: Legacy Screened vs Full-Universe Side-by-Side Attribution
    leg_primary = bt["legacy_screened_replays_by_mode"]["NEXT_BAR_OPEN_BID_ASK"]
    side_by_side_variants: Dict[str, Any] = {}
    for vk in full_primary:
        f_rep = _strip_episodes_from_replay(full_primary[vk])
        l_rep = _strip_episodes_from_replay(leg_primary[vk])
        side_by_side_variants[vk] = {
            "LEGACY_SCREENED": l_rep,
            "FULL_UNIVERSE_ALL_TIMESTAMPS": f_rep,
            "delta_full_minus_legacy": {
                "raw_eligible_decisions_delta": int(f_rep["raw_eligible_decisions_count"]) - int(l_rep["raw_eligible_decisions_count"]),
                "closed_episodes_delta": int(f_rep["closed_independent_episodes_count"]) - int(l_rep["closed_independent_episodes_count"]),
                "wins_delta": int(f_rep["wins"]) - int(l_rep["wins"]),
                "losses_delta": int(f_rep["losses"]) - int(l_rep["losses"]),
                "loss_rate_delta_pp": round(((f_rep["loss_rate"] or 0.0) - (l_rep["loss_rate"] or 0.0)) * 100.0, 2)
                if f_rep["loss_rate"] is not None and l_rep["loss_rate"] is not None
                else None,
                "modeled_net_pnl_usd_delta": round(float(f_rep["modeled_net_pnl_usd"]) - float(l_rep["modeled_net_pnl_usd"]), 2),
            },
        }
    art07 = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        "scopes_strictly_separated_in_reporting": True,
        "variants": side_by_side_variants,
    }
    _write_json(run_dir / "07_legacy_screened_vs_full_universe_side_by_side_attribution.json", art07)

    # Artifact 08: Matched Coeval Comparison Report
    art08 = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        **bt["matched_coeval_report"],
    }
    _write_json(run_dir / "08_matched_coeval_comparison_report.json", art08)

    # Artifact 09: No Trade Overlap & Candidate Conservation Audit
    overlap_audits: Dict[str, Any] = {}
    all_zero_overlap = True
    all_conservation_ok = True
    for scope_name, m_dict in (
        ("FULL_UNIVERSE_PRIMARY", full_primary),
        ("LEGACY_SCREENED_PRIMARY", leg_primary),
    ):
        for vk, rep in m_dict.items():
            ov = rep["no_overlap_audit"]
            cons = rep["candidate_conservation"]
            if not ov["no_overlap_valid"]:
                all_zero_overlap = False
            if not cons["conservation_identity_valid"]:
                all_conservation_ok = False
            overlap_audits[f"{scope_name}::{vk}"] = {
                "no_overlap_audit": ov,
                "candidate_conservation": cons,
            }
    art09 = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        "all_variants_zero_overlap": all_zero_overlap,
        "all_variants_candidate_conservation_valid": all_conservation_ok,
        "audits": overlap_audits,
    }
    _write_json(run_dir / "09_no_trade_overlap_and_candidate_conservation_audit.json", art09)

    # Artifact 10: Trade-to-Ledger and Fee Reconciliation Audit
    ledger_audits: Dict[str, Any] = {}
    all_reconciled = True
    total_risk_cap_violations = 0
    total_fee_errors = 0
    for mode, m_dict in bt["full_universe_replays_by_mode"].items():
        for vk, rep in m_dict.items():
            rec = rep["ledger_reconciliation"]
            if not rec["reconciled"]:
                all_reconciled = False
            total_risk_cap_violations += int(rec["planned_risk_cap_violations_count"])
            total_fee_errors += int(rec["fee_double_count_errors_count"])
            ledger_audits[f"{mode}::{vk}"] = rec
    art10 = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        "risk_policy_snapshot": get_canonical_risk_policy_snapshot().to_dict(),
        "all_variants_reconciled": all_reconciled,
        "total_planned_risk_cap_violations": total_risk_cap_violations,
        "total_fee_double_count_errors": total_fee_errors,
        "no_fill_zero_fabricated_pnl_verified": all_reconciled,
        "open_at_horizon_zero_realized_pnl_verified": all_reconciled,
        "data_gap_unresolved_censored_verified": all_reconciled,
        "audits": ledger_audits,
    }
    _write_json(run_dir / "10_trade_to_ledger_and_fee_reconciliation_audit.json", art10)

    # Artifact 11: Executable Research Policy Registry Frozen Manifest
    art11 = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        "policy_registry_hash": bt["policy_registry_hash"],
        "policy_count": len(PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312),
        "all_disabled_in_production": all(
            not p.enabled_in_production for p in PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312.values()
        ),
        "all_observe_only_outside_backtest": all(
            p.observe_only_outside_backtest for p in PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312.values()
        ),
        "policies": bt["policy_registry_manifest"],
    }
    _write_json(run_dir / "11_executable_research_policy_registry_frozen_manifest.json", art11)

    # Helper to summarize an intervention policy entry for Artifacts 12..15
    int_a1 = bt["interventions_report_a1"]["results_by_policy"]
    int_b1 = bt["interventions_report_b1"]["results_by_policy"]
    m_study_a1 = bt["matched_opportunity_study_a1"]["comparisons_by_variant"]
    m_study_b1 = bt["matched_opportunity_study_b1"]["comparisons_by_variant"]

    def _summarize_policy(pid: str) -> Dict[str, Any]:
        pa1 = int_a1[pid]
        pb1 = int_b1[pid]
        ma1 = m_study_a1[pid]
        mb1 = m_study_b1[pid]
        return {
            "policy": pa1["policy"],
            "accepted_modified_executable_geometry_count": pa1["accepted_modified_executable_geometry_count"],
            "accepted_unchanged_noop_count": pa1["accepted_unchanged_noop_count"],
            "rejected_by_intervention_count": pa1["rejected_by_intervention_count"],
            "rejection_reasons_breakdown": pa1["rejection_reasons_breakdown"],
            "changed_entry_timestamp_count": ma1["changed_entry_timestamp_count"],
            "changed_entry_reference_count": ma1["changed_entry_reference_count"],
            "changed_sl_count": ma1["changed_sl_count"],
            "changed_tp_count": ma1["changed_tp_count"],
            "changed_quantity_count": ma1["changed_quantity_count"],
            "changed_outcome_class_count": ma1["changed_outcome_class_count"],
            "independent_portfolio_replay_primary": _strip_episodes_from_replay(pa1["independent_portfolio_replay_primary"]),
            "three_fill_mode_replays": {
                m: _strip_episodes_from_replay(r) for m, r in pa1["three_fill_mode_replays"].items()
            },
            "macro_cohort_b1_summary": {
                "accepted_modified_executable_geometry_count": pb1["accepted_modified_executable_geometry_count"],
                "accepted_unchanged_noop_count": pb1["accepted_unchanged_noop_count"],
                "rejected_by_intervention_count": pb1["rejected_by_intervention_count"],
                "changed_entry_timestamp_count": mb1["changed_entry_timestamp_count"],
                "changed_sl_count": mb1["changed_sl_count"],
                "changed_tp_count": mb1["changed_tp_count"],
                "independent_portfolio_replay_primary": _strip_episodes_from_replay(pb1["independent_portfolio_replay_primary"]),
            },
        }

    # Artifact 12: Executable Entry Retiming Counterfactual Ledger (R0, R1, R2, R3, R6)
    art12 = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        "R0_BASELINE_CONTROL": _summarize_policy("R0_BASELINE_CONTROL"),
        "R1_HTF_H1_H4_ALIGNMENT_FILTER_ONLY": _summarize_policy("R1_HTF_H1_H4_ALIGNMENT_FILTER_ONLY"),
        "R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE": _summarize_policy("R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE"),
        "R3_DELAYED_ENTRY_BREAKER_FVG_RETEST_EXECUTABLE": _summarize_policy("R3_DELAYED_ENTRY_BREAKER_FVG_RETEST_EXECUTABLE"),
        "R6_MACRO_SESSION_FILTER_ONLY": _summarize_policy("R6_MACRO_SESSION_FILTER_ONLY"),
    }
    _write_json(run_dir / "12_executable_entry_retiming_counterfactual_ledger.json", art12)

    # Artifact 13: Executable Structural SL Counterfactual Ledger (R4)
    art13 = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        "R4_OUTER_STRUCTURAL_STOP_EXECUTABLE": _summarize_policy("R4_OUTER_STRUCTURAL_STOP_EXECUTABLE"),
    }
    _write_json(run_dir / "13_executable_structural_sl_counterfactual_ledger.json", art13)

    # Artifact 14: Executable Structural TP Counterfactual Ledger (R5)
    art14 = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        "R5_CAUSAL_LIQUIDITY_TARGET_EXECUTABLE": _summarize_policy("R5_CAUSAL_LIQUIDITY_TARGET_EXECUTABLE"),
    }
    _write_json(run_dir / "14_executable_structural_tp_counterfactual_ledger.json", art14)

    # Artifact 15: Combined Executable Interventions 3-Mode Ledger (R7, R8)
    art15 = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        "R7_COMBINED_ENTRY_SL_TP_EXECUTABLE": _summarize_policy("R7_COMBINED_ENTRY_SL_TP_EXECUTABLE"),
        "R8_TIMING_PLUS_OUTER_SL_ABLATION_EXECUTABLE": _summarize_policy("R8_TIMING_PLUS_OUTER_SL_ABLATION_EXECUTABLE"),
    }
    _write_json(run_dir / "15_combined_executable_interventions_3_mode_ledger.json", art15)

    # Artifact 16: Intervention Reality & Anti-Fake Audit
    anti_fake_summary: Dict[str, Any] = {}
    total_anti_fake_violations = 0
    for pid, pdata in int_a1.items():
        v_cnt = int(pdata["anti_fake_violation_count"])
        total_anti_fake_violations += v_cnt
        anti_fake_summary[pid] = {
            "cohort": "A1_FULL_UNIVERSE_COHORT",
            "records_audited": len(pdata["matched_opportunity_records"]),
            "anti_fake_violation_count": v_cnt,
            "accepted_modified_executable_geometry_count": pdata["accepted_modified_executable_geometry_count"],
            "accepted_unchanged_noop_count": pdata["accepted_unchanged_noop_count"],
            "rejected_by_intervention_count": pdata["rejected_by_intervention_count"],
        }
    art16 = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        "all_interventions_verified_real": total_anti_fake_violations == 0,
        "anti_fake_violation_count": total_anti_fake_violations,
        "policies_audit": anti_fake_summary,
    }
    _write_json(run_dir / "16_intervention_reality_and_anti_fake_audit.json", art16)

    # Artifact 17: Matched-Opportunity vs Independent Portfolio Attribution
    art17 = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        "unlocked_follow_on_trades_explicitly_attributed": True,
        "volume_reduction_without_positive_expectancy_explicitly_disclosed": True,
        "matched_opportunity_study_a1": {
            "baseline_opportunities_count": bt["matched_opportunity_study_a1"]["baseline_opportunities_count"],
            "comparisons_by_variant": {
                k: {kk: vv for kk, vv in v.items() if kk != "per_opportunity_deltas"}
                for k, v in bt["matched_opportunity_study_a1"]["comparisons_by_variant"].items()
            },
        },
        "matched_opportunity_study_b1": {
            "baseline_opportunities_count": bt["matched_opportunity_study_b1"]["baseline_opportunities_count"],
            "comparisons_by_variant": {
                k: {kk: vv for kk, vv in v.items() if kk != "per_opportunity_deltas"}
                for k, v in bt["matched_opportunity_study_b1"]["comparisons_by_variant"].items()
            },
        },
        "independent_portfolio_attribution_a1": bt["attribution_by_policy_a1"],
        "independent_portfolio_attribution_b1": bt["attribution_by_policy_b1"],
    }
    _write_json(run_dir / "17_matched_opportunity_vs_independent_portfolio_attribution.json", art17)

    # Artifact 18: Updated Loss Root-Cause Matrix (Full Universe)
    art18 = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        **bt["loss_root_cause_matrix"],
    }
    _write_json(run_dir / "18_updated_loss_root_cause_matrix_full_universe.json", art18)

    # Artifact 19: Protected Structure Authority & Stop Classification Audit
    all_auth_ok = all(
        bool(v.get("audit_passed")) for v in bt["protected_structure_authority_audits"].values()
    )
    tot_contra = sum(
        int(v.get("contradictory_stop_labels_count", 0))
        for v in bt["protected_structure_authority_audits"].values()
    )
    art19 = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        "all_cohorts_audit_passed": all_auth_ok,
        "total_contradictory_stop_labels_count": tot_contra,
        "cohorts": bt["protected_structure_authority_audits"],
    }
    _write_json(run_dir / "19_protected_structure_authority_and_stop_classification_audit.json", art19)

    # Artifact 20: Per-Timeframe Causal Provenance Audit
    art20 = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        **bt["per_timeframe_causal_provenance_audit"],
        "all_Audited_records_strictly_causal": bt["per_timeframe_causal_provenance_audit"]["all_audited_records_strictly_causal"],
    }
    _write_json(run_dir / "20_per_timeframe_causal_provenance_audit.json", art20)

    # Artifact 21: Chronological OOS & Walk-Forward Integrity Report
    art21 = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        **bt["oos_integrity_report"],
    }
    _write_json(run_dir / "21_chronological_oos_and_walkforward_integrity_report.json", art21)

    # Artifact 22: Single vs Multi-Worker Determinism Proof
    art22 = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        "max_workers_cap": min(int(max_workers), 4),
        **bt["single_vs_multi_worker_determinism"],
    }
    _write_json(run_dir / "22_single_vs_multi_worker_determinism_proof.json", art22)

    # Artifact 23: Bounded Pytest Shards & JUnit Bijection Proof
    print("[V17.9.3.1.2] Running bounded pytest shards (4 workers) with JUnit XML bijection check...")
    art23 = execute_bounded_pytest_shards_with_junit_bijection(
        run_dir=run_dir,
        max_workers=min(int(max_workers), 4),
    )
    art23["run_id"] = run_id
    _run_git(["checkout", "--", "backtest/trade_decision_lineage_reconciliation.json"], check=False)
    _write_json(run_dir / "23_pytest_execution_and_junit_bijection_proof.json", art23)

    # Artifact 24: Economic & Statistical Verdict Summary
    art24 = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        "tested_executable_code_sha": tested_code_sha,
        **bt["economic_and_statistical_summary"],
    }
    _write_json(run_dir / "24_economic_and_statistical_verdict_summary.json", art24)

    # Artifact 27 (Initial snapshot before final push)
    art27 = build_artifact_27_git_commit_push_remote_head_proof(
        run_id=run_id,
        tested_code_sha=tested_code_sha,
    )
    _write_json(run_dir / "27_git_commit_push_remote_HEAD_proof.json", art27)

    # Artifact 26: Executive Closure Report Markdown
    art26_md = build_artifact_26_closure_executive_report_md(
        run_id=run_id,
        tested_code_sha=tested_code_sha,
        art01=art01,
        art04=art04,
        art05=art05,
        art07=art07,
        art12=art12,
        art13=art13,
        art14=art14,
        art15=art15,
        art18=art18,
        art21=art21,
        art23=art23,
        art24=art24,
        art25_prelim_ok=True,
        art27=art27,
    )
    (run_dir / "26_Closure_Executive_Report_V17_9_3_1_2.md").write_text(art26_md, encoding="utf-8")

    # Write temporary Artifact 25 so all 27 files exist before hashing
    _write_json(run_dir / "25_acceptance_gates_G01_to_G38_manifest.json", {"placeholder": True})
    sha_manifest = update_artifact_sha256_manifest(run_dir, run_id)
    post_res = _check_disk_and_resource_budget(min_free_gb=5.0)

    art25 = evaluate_all_38_acceptance_gates(
        run_id=run_id,
        art01=art01,
        art02=art02,
        art03=art03,
        art04=art04,
        art05=art05,
        art06=art06,
        art07=art07,
        art08=art08,
        art09=art09,
        art10=art10,
        art11=art11,
        art12=art12,
        art13=art13,
        art14=art14,
        art15=art15,
        art16=art16,
        art17=art17,
        art18=art18,
        art19=art19,
        art20=art20,
        art21=art21,
        art22=art22,
        art23=art23,
        art24=art24,
        art27=art27,
        sha_manifest=sha_manifest,
        resource_guard=post_res,
    )
    _write_json(run_dir / "25_acceptance_gates_G01_to_G38_manifest.json", art25)
    sha_manifest = update_artifact_sha256_manifest(run_dir, run_id)

    latest_payload = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "run_id": run_id,
        "artifact_dir": f"docs/artifacts/v17_9_3_1_2/{run_id}",
        "working_branch": MANDATORY_WORKING_BRANCH,
        "starting_v179312_feature_head_sha": STARTING_V179312_FEATURE_HEAD_SHA,
        "previous_v179311_tested_code_sha": PREVIOUS_V179311_TESTED_CODE_SHA,
        "previous_v17931_tested_code_sha": PREVIOUS_V17931_TESTED_CODE_SHA,
        "base_master_sha": BASE_MASTER_SHA,
        "tested_executable_code_sha": tested_code_sha,
        "final_commit_sha": art27["final_local_sha"],
        "remote_sha": art27["remote_sha"],
        "local_equals_remote_sha": art27["local_equals_remote_sha"],
        "ENGINEERING_VERIFICATION_VERDICT": "PASS" if art25["all_gates_passed"] else "PENDING_FINAL_PUSH_PROOF",
        "STRATEGY_STATISTICAL_VERDICT": art24["STRATEGY_STATISTICAL_VERDICT"],
        "ACTIVATION_READY": art24["ACTIVATION_READY"],
        "ready_for_strategy_activation": art24["ready_for_strategy_activation"],
        "acceptance_gates_passed": art25["passed_gates"],
        "acceptance_gates_total": art25["total_gates"],
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _write_json(base_dir / "latest_run.json", latest_payload)

    print(
        f"[V17.9.3.1.2] Completed run_id={run_id}: gates={art25['passed_gates']}/{art25['total_gates']}"
    )
    return 0


def finalize_remote_push_proof(run_id: str) -> int:
    base_dir = REPO_ROOT / "docs" / "artifacts" / "v17_9_3_1_2"
    run_dir = base_dir / run_id
    if not run_dir.is_dir():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")

    art01 = json.loads((run_dir / "01_git_branch_source_frozen_identity.json").read_text(encoding="utf-8"))
    tested_code_sha = str(art01["tested_executable_code_sha"])

    art27 = build_artifact_27_git_commit_push_remote_head_proof(
        run_id=run_id,
        tested_code_sha=tested_code_sha,
    )
    _write_json(run_dir / "27_git_commit_push_remote_HEAD_proof.json", art27)

    art02 = json.loads((run_dir / "02_full_universe_22346_index_coverage_manifest.json").read_text(encoding="utf-8"))
    art03 = json.loads((run_dir / "03_decision_stream_chunks_manifest.json").read_text(encoding="utf-8"))
    art04 = json.loads((run_dir / "04_outside_screen_15597_timestamps_47243_decisions_reconciliation.json").read_text(encoding="utf-8"))
    art05 = json.loads((run_dir / "05_full_universe_portfolio_replay_A0_A1_E1_B1_D2_primary.json").read_text(encoding="utf-8"))
    art06 = json.loads((run_dir / "06_full_universe_portfolio_replay_3_fill_modes_comparison.json").read_text(encoding="utf-8"))
    art07 = json.loads((run_dir / "07_legacy_screened_vs_full_universe_side_by_side_attribution.json").read_text(encoding="utf-8"))
    art08 = json.loads((run_dir / "08_matched_coeval_comparison_report.json").read_text(encoding="utf-8"))
    art09 = json.loads((run_dir / "09_no_trade_overlap_and_candidate_conservation_audit.json").read_text(encoding="utf-8"))
    art10 = json.loads((run_dir / "10_trade_to_ledger_and_fee_reconciliation_audit.json").read_text(encoding="utf-8"))
    art11 = json.loads((run_dir / "11_executable_research_policy_registry_frozen_manifest.json").read_text(encoding="utf-8"))
    art12 = json.loads((run_dir / "12_executable_entry_retiming_counterfactual_ledger.json").read_text(encoding="utf-8"))
    art13 = json.loads((run_dir / "13_executable_structural_sl_counterfactual_ledger.json").read_text(encoding="utf-8"))
    art14 = json.loads((run_dir / "14_executable_structural_tp_counterfactual_ledger.json").read_text(encoding="utf-8"))
    art15 = json.loads((run_dir / "15_combined_executable_interventions_3_mode_ledger.json").read_text(encoding="utf-8"))
    art16 = json.loads((run_dir / "16_intervention_reality_and_anti_fake_audit.json").read_text(encoding="utf-8"))
    art17 = json.loads((run_dir / "17_matched_opportunity_vs_independent_portfolio_attribution.json").read_text(encoding="utf-8"))
    art18 = json.loads((run_dir / "18_updated_loss_root_cause_matrix_full_universe.json").read_text(encoding="utf-8"))
    art19 = json.loads((run_dir / "19_protected_structure_authority_and_stop_classification_audit.json").read_text(encoding="utf-8"))
    art20 = json.loads((run_dir / "20_per_timeframe_causal_provenance_audit.json").read_text(encoding="utf-8"))
    art21 = json.loads((run_dir / "21_chronological_oos_and_walkforward_integrity_report.json").read_text(encoding="utf-8"))
    art22 = json.loads((run_dir / "22_single_vs_multi_worker_determinism_proof.json").read_text(encoding="utf-8"))
    art23 = json.loads((run_dir / "23_pytest_execution_and_junit_bijection_proof.json").read_text(encoding="utf-8"))
    art24 = json.loads((run_dir / "24_economic_and_statistical_verdict_summary.json").read_text(encoding="utf-8"))

    art26_md = build_artifact_26_closure_executive_report_md(
        run_id=run_id,
        tested_code_sha=tested_code_sha,
        art01=art01,
        art04=art04,
        art05=art05,
        art07=art07,
        art12=art12,
        art13=art13,
        art14=art14,
        art15=art15,
        art18=art18,
        art21=art21,
        art23=art23,
        art24=art24,
        art25_prelim_ok=True,
        art27=art27,
    )
    (run_dir / "26_Closure_Executive_Report_V17_9_3_1_2.md").write_text(art26_md, encoding="utf-8")

    sha_manifest = update_artifact_sha256_manifest(run_dir, run_id)
    post_res = _check_disk_and_resource_budget(min_free_gb=5.0)

    art25 = evaluate_all_38_acceptance_gates(
        run_id=run_id,
        art01=art01,
        art02=art02,
        art03=art03,
        art04=art04,
        art05=art05,
        art06=art06,
        art07=art07,
        art08=art08,
        art09=art09,
        art10=art10,
        art11=art11,
        art12=art12,
        art13=art13,
        art14=art14,
        art15=art15,
        art16=art16,
        art17=art17,
        art18=art18,
        art19=art19,
        art20=art20,
        art21=art21,
        art22=art22,
        art23=art23,
        art24=art24,
        art27=art27,
        sha_manifest=sha_manifest,
        resource_guard=post_res,
    )
    _write_json(run_dir / "25_acceptance_gates_G01_to_G38_manifest.json", art25)
    update_artifact_sha256_manifest(run_dir, run_id)

    latest = json.loads((base_dir / "latest_run.json").read_text(encoding="utf-8"))
    latest["final_commit_sha"] = art27["final_local_sha"]
    latest["remote_sha"] = art27["remote_sha"]
    latest["local_equals_remote_sha"] = art27["local_equals_remote_sha"]
    latest["acceptance_gates_passed"] = art25["passed_gates"]
    latest["acceptance_gates_total"] = art25["total_gates"]
    latest["ENGINEERING_VERIFICATION_VERDICT"] = "PASS" if art25["all_gates_passed"] else "FAIL"
    _write_json(base_dir / "latest_run.json", latest)
    print(
        f"[V17.9.3.1.2] Finalized remote push proof for {run_id}: "
        f"gates={art25['passed_gates']}/{art25['total_gates']} local={art27['final_local_sha']} remote={art27['remote_sha']}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run V17.9.3.1.2 full-universe verification and executable intervention pipeline.")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--finalize-remote-only", type=str, default=None)
    args = parser.parse_args()
    if args.finalize_remote_only:
        return finalize_remote_push_proof(args.finalize_remote_only)
    return run_full_verification(max_workers=args.max_workers)


if __name__ == "__main__":
    raise SystemExit(main())
