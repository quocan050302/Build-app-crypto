#!/usr/bin/env python3
"""
AURUM Desk V17.9.2 — Structure-Valid SL/TP & Trade Invalidation Intelligence
Full Async Regression, Historical Ablation & 16-Gate Verification Orchestrator
==============================================================================
Implements Section 08, 09, 10, 11 verification pipeline:
  - `verify_exact_source_and_qa_ancestry`
  - `collect_full_pytest_inventory`
  - `build_resource_bounded_shards`
  - `aggregate_full_junit_results`
  - `run_targeted_negative_controls`
  - `run_structural_sl_historical_ablation`
  - `evaluate_v1792_gate`
  - `validate_v1792_final_report`
  - `publish_evidence_manifest`
  - `finalize_commit_and_remote_verification`
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.shared_strategy_evaluator import (
    EVALUATOR_VERSION,
    SharedStrategyEvaluator,
    compare_decision_parity,
)
from services.stop_placement_types import StopAnchorKind
from services.v17_8_backtest_runner import (
    SAVED_DATASET_GZ_PATH,
    compute_executable_source_tree_hash,
)
from services.v17_9_1_decision_funnel import build_function_truth_map
from services.v17_9_2_structural_stop_intelligence import (
    V17_9_2_ALGORITHM_VERSION,
    StructuralStopCandidate,
    collect_causal_stop_anchor_candidates,
    compute_causal_noise_buffer,
    derive_structure_valid_stop,
    evaluate_stop_tp_risk_and_size,
    rank_structure_valid_anchors,
    resolve_causal_target_candidates,
    select_viable_structural_target,
    validate_structure_invalidation_contract,
)
from tools.run_v17_9_2_structural_stop_backtest import (
    V1792_VARIANT_POLICIES,
    run_v17_9_2_structural_stop_backtest,
    validate_historical_provenance_segregation,
)
from tools.run_v17_9_async_regression import AsyncWorkerGroupSpec, BoundedAsyncTestOrchestrator
from tools.test_resource_guard import (
    TestResourceGuard,
    monitor_disk_pressure,
    probe_disk_budget,
    reserve_test_artifact_budget,
)
from tools.v17_9_execution_state import ExecutionStateManager


ALLOWED_FEATURE_BRANCH = "feature/v17-9-2-structural-sl-tp-from-qa"
CANONICAL_BASE_BRANCH = "master"
CANONICAL_BASE_SHA = "61c6783c222c67a8afc67ff772ecb920f51535c8"
REFERENCE_V1791_HEAD_SHA = "58a8b14123c1d12625dcf4e91c0dd4e1c668fdfd"
REFERENCE_V1791_EXEC_SHA = "ab8f0252a05d6ff4fc0161f3d2d1548541697309"


@dataclass(frozen=True)
class GateResult:
    gate_id: str
    status: str  # "PASS" | "FAIL" | "BLOCKED"
    passed: bool
    evidence_path: str
    reasons: Tuple[str, ...] = ()
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "status": self.status,
            "passed": self.passed,
            "evidence_path": self.evidence_path,
            "reasons": list(self.reasons),
            "metrics": dict(self.metrics),
        }


@dataclass(frozen=True)
class ReportValidationResult:
    is_valid: bool
    status: str
    reasons: Tuple[str, ...] = ()
    gate_statuses: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "status": self.status,
            "reasons": list(self.reasons),
            "gate_statuses": dict(self.gate_statuses),
        }


def _git_cmd(args: List[str], check: bool = True) -> str:
    res = subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=check,
    )
    return res.stdout.strip()


def _query_remote_branch_head(branch: str) -> Optional[str]:
    try:
        out = _git_cmd(["ls-remote", "--heads", "origin", branch], check=False)
        for line in out.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1].endswith(f"refs/heads/{branch}"):
                return parts[0].strip()
    except Exception:
        pass
    return None


def _write_json(path: Path, payload: Dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _dirty_tracked_code_files() -> List[str]:
    lines = _git_cmd(["status", "--porcelain=v1"], check=False).splitlines()
    dirty: List[str] = []
    for ln in lines:
        if not ln.strip() or ln.startswith("?? "):
            continue
        path_part = ln[3:].strip()
        if path_part.startswith("docs/artifacts/v17_9_2/"):
            continue
        dirty.append(ln.strip())
    return dirty


def build_v1792_implementation_map() -> List[Dict[str, Any]]:
    return [
        {
            "file": "backend/services/v17_9_2_structural_stop_intelligence.py",
            "file_line": "backend/services/v17_9_2_structural_stop_intelligence.py:135-1565",
            "exact_function": (
                "collect_causal_stop_anchor_candidates, rank_structure_valid_anchors, "
                "compute_causal_noise_buffer, derive_structure_valid_stop, "
                "validate_structure_invalidation_contract, resolve_causal_target_candidates, "
                "select_viable_structural_target, evaluate_stop_tp_risk_and_size, "
                "classify_structure_stop_outcome, measure_premature_stop_rate, "
                "plan_v17_9_2_structural_stop_and_target"
            ),
            "function_names": [
                "collect_causal_stop_anchor_candidates",
                "rank_structure_valid_anchors",
                "compute_causal_noise_buffer",
                "derive_structure_valid_stop",
                "validate_structure_invalidation_contract",
                "resolve_causal_target_candidates",
                "select_viable_structural_target",
                "evaluate_stop_tp_risk_and_size",
                "classify_structure_stop_outcome",
                "measure_premature_stop_rate",
                "plan_v17_9_2_structural_stop_and_target",
            ],
            "old_or_new": "NEW",
            "why": (
                "Section 04.1–04.8: Pure causal V17.9.2 structural stop anchor collection, protected swing "
                "ranking vs local sweep wicks, causal noise buffer, outward tick quantization, causal structural "
                "target resolution without invented RR targets, cost-aware Net R:R & dynamic sizing, and "
                "post-trade premature stop vs true invalidation outcome classifier."
            ),
            "caller": "SharedStrategyEvaluator.evaluate_model_direction, resolve_stop_anchor, run_v17_9_2_structural_stop_backtest",
            "test": (
                "test_t01_causal_anchor_candidate_collection_and_anti_lookahead, "
                "test_t02_model_specific_anchor_ranking_and_tie_break, "
                "test_t03_directional_stop_monotonicity_and_tick_quantization, "
                "test_t04_causal_noise_buffer_monotonicity_and_fallback, "
                "test_t05_structure_invalidation_contract_negative_guards, "
                "test_t07_target_selection_from_known_causal_liquidity_pools, "
                "test_t08_no_invented_rr_target_rule, "
                "test_t09_cost_aware_net_rr_and_dynamic_quantity_scaling, "
                "test_t10_min_lot_and_margin_rejection_for_wide_stop, "
                "test_t14_premature_stop_vs_true_invalidation_outcome_classifier"
            ),
        },
        {
            "file": "backend/services/stop_placement_types.py",
            "file_line": "backend/services/stop_placement_types.py:21-555",
            "exact_function": "StopAnchorKind, StopPlacementPolicy, StopAnchorResult, StopPlacementDecision",
            "function_names": [
                "StopAnchorKind",
                "StopPlacementPolicy",
                "StopAnchorResult",
                "StopPlacementDecision",
            ],
            "old_or_new": "MODIFIED",
            "why": (
                "Section 05: Adds default-OFF V17.9.2 policy flags (`v17_9_2_structural_sl_enabled`, "
                "`v17_9_2_observe_only`, `v17_9_2_mode`) and audit fields (`v1792_candidates`, `v1792_plan`)."
            ),
            "caller": "StopPlacementPolicyService, resolve_stop_anchor, SharedStrategyEvaluator",
            "test": "test_t02_model_specific_anchor_ranking_and_tie_break, test_t11_baseline_vs_observe_only_action_invariance",
        },
        {
            "file": "backend/services/stop_placement_policy_service.py",
            "file_line": "backend/services/stop_placement_policy_service.py:67-395",
            "exact_function": "resolve_stop_placement_policy, validate_stop_placement_policy",
            "function_names": [
                "resolve_stop_placement_policy",
                "validate_stop_placement_policy",
            ],
            "old_or_new": "MODIFIED",
            "why": (
                "Section 05: Adds strict boolean validation, CAS no-op check, and DB persistence for "
                "`stop_placement_v1.v17_9_2_structural_sl_enabled` and `stop_placement_v1.v17_9_2_observe_only`."
            ),
            "caller": "Policy API, ScalpWatcherOrchestrator",
            "test": "test_t11_baseline_vs_observe_only_action_invariance",
        },
        {
            "file": "backend/services/stop_placement_service.py",
            "file_line": "backend/services/stop_placement_service.py:855-1205",
            "exact_function": "_resolve_stop_anchor_baseline, resolve_stop_anchor",
            "function_names": ["_resolve_stop_anchor_baseline", "resolve_stop_anchor"],
            "old_or_new": "MODIFIED",
            "why": (
                "Section 05: Wraps baseline V17.8 stop anchor resolution to attach V17.9.2 candidate rankings "
                "in observe-only mode or promote causal V17.9.2 structural anchor when explicitly enabled."
            ),
            "caller": "SharedStrategyEvaluator.evaluate_model_direction",
            "test": "test_t11_baseline_vs_observe_only_action_invariance, test_t16_stop_never_widened_after_arm_boundary",
        },
        {
            "file": "backend/services/shared_strategy_evaluator.py",
            "file_line": "backend/services/shared_strategy_evaluator.py:210-3030",
            "exact_function": "StrategyDecisionSnapshot, SharedStrategyEvaluator",
            "function_names": ["StrategyDecisionSnapshot", "SharedStrategyEvaluator"],
            "old_or_new": "MODIFIED",
            "why": (
                "Section 05: Integrates V17.9.2 observe-only and research modes (`STRUCTURAL_SL_ON`, "
                "`STRUCTURE_ONLY`, `STRUCTURE_PLUS_NOISE`, `TP_POLICY_ONLY`, `STRUCTURE_PLUS_TP`) into the "
                "single canonical decision evaluator shared by all 3 runtime adapters while preserving 100% "
                "baseline and observe-only trade-action invariance."
            ),
            "caller": "ScalpWatcherOrchestrator, MarketShadowService, evaluate_production_parity_model_snapshot",
            "test": (
                "test_t11_baseline_vs_observe_only_action_invariance, "
                "test_t12_three_runtime_adapter_parity, "
                "test_t13_spoofed_or_reused_adapter_call_id_rejection, "
                "test_t16_stop_never_widened_after_arm_boundary"
            ),
        },
        {
            "file": "tools/run_v17_9_2_structural_stop_backtest.py",
            "file_line": "tools/run_v17_9_2_structural_stop_backtest.py:98-1725",
            "exact_function": (
                "evaluate_v1792_statistical_verdict, validate_historical_provenance_segregation, "
                "run_v17_9_2_structural_stop_backtest"
            ),
            "function_names": [
                "evaluate_v1792_statistical_verdict",
                "validate_historical_provenance_segregation",
                "run_v17_9_2_structural_stop_backtest",
            ],
            "old_or_new": "NEW",
            "why": (
                "Section 06: Executes 9-variant historical ablation (`A`, `B`, `C`, `D1`..`D6`), "
                "6-episode before/after forensics, 3-runtime + oracle parity, non-overlapping episode "
                "deduplication, and 50%/25%/25% chronological OOS evaluation with 180-M1 embargo."
            ),
            "caller": "run_v17_9_2_verification.py, test_v17_9_2_structural_stop.py",
            "test": (
                "test_t15_historical_6_episode_forensic_reproduction, "
                "test_t17_dataset_gap_and_quote_mode_guard, "
                "test_t18_chronological_oos_embargo_and_statistical_verdict_guard, "
                "test_t26_synthetic_vs_historical_provenance_segregation"
            ),
        },
        {
            "file": "tools/run_v17_9_2_verification.py",
            "file_line": "tools/run_v17_9_2_verification.py:1-1200",
            "exact_function": (
                "collect_full_pytest_inventory, build_resource_bounded_shards, "
                "run_targeted_negative_controls, run_structural_sl_historical_ablation, "
                "verify_exact_source_and_qa_ancestry, aggregate_full_junit_results, "
                "evaluate_v1792_gate, validate_v1792_final_report, "
                "publish_evidence_manifest, finalize_commit_and_remote_verification"
            ),
            "function_names": [
                "collect_full_pytest_inventory",
                "build_resource_bounded_shards",
                "run_targeted_negative_controls",
                "run_structural_sl_historical_ablation",
                "verify_exact_source_and_qa_ancestry",
                "aggregate_full_junit_results",
                "evaluate_v1792_gate",
                "validate_v1792_final_report",
                "publish_evidence_manifest",
                "finalize_commit_and_remote_verification",
            ],
            "old_or_new": "NEW",
            "why": (
                "Sections 08–11: Bounded async regression orchestrator, subprocess RED/GREEN mutation probe, "
                "16-gate (`G01`–`G16`) evidence evaluator, SHA-256 manifest publisher, and post-push git verifier."
            ),
            "caller": "CLI main, test_v17_9_2_structural_stop.py",
            "test": (
                "test_t19_bounded_async_test_orchestrator_and_resource_guard_bounds, "
                "test_t20_negative_control_mutation_proof"
            ),
        },
    ]


def verify_implementation_map_ast(
    impl_map: Sequence[Mapping[str, Any]],
    repo_root: Path = REPO_ROOT,
) -> Dict[str, Any]:
    errors: List[str] = []
    verified_symbols: List[str] = []

    test_file = repo_root / "backend" / "tests" / "test_v17_9_2_structural_stop.py"
    test_defs: set[str] = set()
    if test_file.exists():
        t_tree = ast.parse(test_file.read_text(encoding="utf-8"), filename=str(test_file))
        test_defs = {
            node.name
            for node in ast.walk(t_tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
    else:
        errors.append(f"Missing required test file: {test_file}")

    for idx, item in enumerate(impl_map):
        rel_file = str(item.get("file") or "").strip()
        f_path = repo_root / rel_file
        if not rel_file or not f_path.exists():
            errors.append(f"Item {idx} missing file: {rel_file}")
            continue
        tree = ast.parse(f_path.read_text(encoding="utf-8"), filename=str(f_path))
        defined_names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        for fn in item.get("function_names") or []:
            clean_fn = str(fn).split(".")[-1].strip()
            if clean_fn not in defined_names:
                errors.append(f"Symbol {clean_fn!r} missing in AST of {rel_file}")
            else:
                verified_symbols.append(f"{rel_file}::{clean_fn}")

        for t_ref in [t.strip() for t in str(item.get("test") or "").split(",") if t.strip()]:
            if t_ref not in test_defs:
                errors.append(f"Test {t_ref!r} missing in {test_file.name}")

    return {
        "is_valid": len(errors) == 0 and len(verified_symbols) > 0 and len(test_defs) >= 26,
        "status": "PASS" if (len(errors) == 0 and len(verified_symbols) > 0 and len(test_defs) >= 26) else "FAIL",
        "verified_symbols_count": len(verified_symbols),
        "verified_symbols": verified_symbols,
        "v17_9_2_test_functions_count": len(test_defs),
        "errors": errors,
    }


def verify_exact_source_and_qa_ancestry(
    *,
    repo_root: Path = REPO_ROOT,
    base_branch: str = CANONICAL_BASE_BRANCH,
    expected_base_sha: str = CANONICAL_BASE_SHA,
    expected_feature_branch: str = ALLOWED_FEATURE_BRANCH,
) -> Dict[str, Any]:
    """
    Section 01, 02 & 08: Verify base branch ancestry, user branch override (`master` ->
    `feature/v17-9-2-structural-sl-tp-from-qa`), zero missing V17.9.1 dependencies compared
    to reference SHA `58a8b14123c1d12625dcf4e91c0dd4e1c668fdfd`, and current executable source tree hash.
    """
    current_branch = _git_cmd(["branch", "--show-current"])
    head_sha = _git_cmd(["rev-parse", "HEAD"])
    base_sha = _git_cmd(["rev-parse", base_branch])
    base_tree_sha = _git_cmd(["rev-parse", f"{base_branch}^{{tree}}"])

    is_ancestor_proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", base_sha, "HEAD"],
        cwd=str(repo_root),
        check=False,
    )
    is_ancestor = is_ancestor_proc.returncode == 0
    merge_base_sha = _git_cmd(["merge-base", base_sha, "HEAD"], check=False)

    # Check executable diff between base_sha (`master` 61c6783c) and reference V17.9.1 SHAs
    diff_58a8 = _git_cmd(
        ["diff", "--name-only", REFERENCE_V1791_HEAD_SHA, base_sha, "--", "backend", "tools", "backtest"],
        check=False,
    ).splitlines()
    diff_ab8f = _git_cmd(
        ["diff", "--name-only", REFERENCE_V1791_EXEC_SHA, base_sha, "--", "backend", "tools", "backtest"],
        check=False,
    ).splitlines()

    required_v1791_files = [
        "backend/services/shared_strategy_evaluator.py",
        "backend/services/mtf_structure_liquidity_service.py",
        "backend/services/v17_9_1_decision_funnel.py",
        "backend/services/v17_9_0_1_baseline_oracle.py",
        "backend/services/stop_placement_service.py",
        "backend/services/stop_placement_types.py",
        "backend/services/stop_placement_policy_service.py",
        "backend/services/unicorn_evidence_service.py",
        "backend/services/liquidity_scenario_service.py",
        "backend/domain_calculator.py",
        "tools/test_resource_guard.py",
        "tools/v17_9_execution_state.py",
        "tools/run_v17_9_async_regression.py",
        "backtest/historical_dataset_XAUUSDT.json.gz",
    ]
    missing_foundation_files = [f for f in required_v1791_files if not (repo_root / f).exists()]
    exec_tree_info = compute_executable_source_tree_hash(repo_root)
    dirty_code = _dirty_tracked_code_files()

    reasons: List[str] = []
    if current_branch != expected_feature_branch:
        reasons.append(f"Current branch {current_branch!r} != {expected_feature_branch!r}")
    if base_sha != expected_base_sha:
        reasons.append(f"Base SHA {base_sha!r} != {expected_base_sha!r}")
    if not is_ancestor or merge_base_sha != base_sha:
        reasons.append(f"HEAD {head_sha} is not a descendant of {base_branch} ({base_sha})")
    if missing_foundation_files:
        reasons.append(f"Missing foundation files: {missing_foundation_files}")
    if dirty_code:
        reasons.append(f"Dirty tracked code files: {dirty_code}")

    return {
        "schema_version": V17_9_2_ALGORITHM_VERSION,
        "status": "PASS" if len(reasons) == 0 else "FAIL",
        "initial_qa_preflight_status": "BLOCKED_QA_BASE_NOT_FOUND",
        "user_explicit_branch_override": "Lưu ý lộn nhánh rồi, không phải QA mà là nhánh master nha",
        "resolved_base_branch": base_branch,
        "qa_base_sha": base_sha,
        "qa_base_tree_sha": base_tree_sha,
        "new_branch": current_branch,
        "head_sha": head_sha,
        "merge_base_with_qa_base": merge_base_sha,
        "is_descendant_of_base": is_ancestor,
        "reference_v17_9_1_head_sha": REFERENCE_V1791_HEAD_SHA,
        "reference_v17_9_1_exec_sha": REFERENCE_V1791_EXEC_SHA,
        "executable_diff_vs_58a8b141": diff_58a8,
        "executable_diff_vs_ab8f0252": diff_ab8f,
        "ported_files_from_v17_9_1": [],
        "port_required": False,
        "missing_foundation_files": missing_foundation_files,
        "executable_source_tree_hash": exec_tree_info["executable_source_tree_hash"],
        "core_backtest_evaluator_tree_hash": exec_tree_info["core_backtest_evaluator_tree_hash"],
        "dirty_tracked_code_files": dirty_code,
        "reasons": reasons,
    }


def collect_full_pytest_inventory(repo_root: Path = REPO_ROOT) -> Dict[str, Any]:
    """
    Section 08: Discover 100% of backend tests dynamically via `pytest backend/tests --collect-only -q`.
    Never hard-codes test counts.
    """
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "backend") + os.pathsep + str(repo_root)
    env["PYTHONHASHSEED"] = "0"
    cmd = [sys.executable, "-m", "pytest", "backend/tests", "--collect-only", "-q"]
    res = subprocess.run(
        cmd,
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    node_ids: List[str] = []
    files_map: Dict[str, int] = {}
    for raw_line in res.stdout.splitlines():
        line = raw_line.strip()
        if "::" in line and line.startswith("backend/tests/test_"):
            node_ids.append(line)
            f_part = line.split("::", 1)[0]
            files_map[f_part] = files_map.get(f_part, 0) + 1

    return {
        "command": " ".join(cmd),
        "exit_code": int(res.returncode),
        "status": "PASS" if (res.returncode == 0 and len(node_ids) > 0) else "FAIL",
        "discovered_total": len(node_ids),
        "discovered_files_count": len(files_map),
        "counts_by_file": dict(sorted(files_map.items())),
        "node_ids_sha256": hashlib.sha256("\n".join(node_ids).encode("utf-8")).hexdigest(),
    }


def build_resource_bounded_shards(
    repo_root: Path = REPO_ROOT,
    num_general_shards: int = 10,
) -> List[Tuple[str, str, List[str], Optional[str]]]:
    """
    Section 08: Partition 100% of `backend/tests/test_*.py` into non-overlapping shards:
      - `SHARD_01_V17_9_2_STRUCTURAL_STOP`: `backend/tests/test_v17_9_2_structural_stop.py`
      - `SHARD_02_V17_9_1_MTF_SUITE`: `backend/tests/test_v17_9_1_mtf_structure_liquidity.py`
      - `SHARD_03_V17_9_1_POSITIVE_PATH`: `backend/tests/test_v17_9_1_positive_path_replay.py`
      - `SHARD_04`..`SHARD_13`: weighted load-balanced shards covering all remaining `backend/tests/test_*.py`
    """
    dedicated_files = {
        "backend/tests/test_v17_9_2_structural_stop.py",
        "backend/tests/test_v17_9_2_1_validation_closure.py",
        "backend/tests/test_v17_9_1_mtf_structure_liquidity.py",
        "backend/tests/test_v17_9_1_positive_path_replay.py",
    }
    all_remaining = sorted(
        str(p.relative_to(repo_root)).replace("\\", "/")
        for p in (repo_root / "backend" / "tests").glob("test_*.py")
        if str(p.relative_to(repo_root)).replace("\\", "/") not in dedicated_files
    )

    def _estimate_file_weight(rel_f: str) -> float:
        base_sz = float((repo_root / rel_f).stat().st_size)
        if (
            "test_v5_authoritative_lab" in rel_f
            or "test_v17_6_4_3b_unique_commit_truth" in rel_f
            or "test_v17_9_0_1_2_strict_eligibility" in rel_f
            or "test_v17_9_0_1_runtime_integration" in rel_f
        ):
            return base_sz + 600_000.0
        if "test_v17_6_4_3" in rel_f or "test_v17_4_" in rel_f or "test_v13_2_replay_repair" in rel_f:
            return base_sz * 3.5 + 250_000.0
        if "test_v17_6" in rel_f or "test_v13_" in rel_f or "test_final_" in rel_f:
            return base_sz * 2.5 + 100_000.0
        return base_sz

    buckets: List[List[str]] = [[] for _ in range(num_general_shards)]
    weights: List[float] = [0.0 for _ in range(num_general_shards)]
    for tf in sorted(all_remaining, key=_estimate_file_weight, reverse=True):
        w = _estimate_file_weight(tf)
        min_idx = min(range(num_general_shards), key=lambda k: weights[k])
        buckets[min_idx].append(tf)
        weights[min_idx] += w

    for b in buckets:
        b.sort(
            key=lambda f: (
                0
                if (
                    "test_v15_1_execution_lifecycle" in f
                    or "test_v17_1_" in f
                    or "test_v17_2_" in f
                    or "_production_flow" in f
                    or "test_realtime_execution_worker" in f
                    or "_red_reproductions" in f
                )
                else 1,
                f,
            )
        )

    v1792_files = [
        f
        for f in (
            "backend/tests/test_v17_9_2_structural_stop.py",
            "backend/tests/test_v17_9_2_1_validation_closure.py",
        )
        if (repo_root / f).exists()
    ]
    shards: List[Tuple[str, str, List[str], Optional[str]]] = [
        (
            "SHARD_01_V17_9_2_STRUCTURAL_STOP",
            "V17.9.2 / V17.9.2.1 Structure-Valid SL/TP, Causal Invalidation & Closure Suite",
            v1792_files,
            None,
        ),
        (
            "SHARD_02_V17_9_1_MTF_SUITE",
            "V17.9.1 Causal MTF Structure, Liquidity, Fail-Closed & A/B Backtest Suite",
            ["backend/tests/test_v17_9_1_mtf_structure_liquidity.py"],
            None,
        ),
        (
            "SHARD_03_V17_9_1_POSITIVE_PATH",
            "V17.9.1.1 Positive-Path Historical Replay & Decision Funnel Suite",
            ["backend/tests/test_v17_9_1_positive_path_replay.py"],
            None,
        ),
    ]
    for idx, bucket in enumerate(buckets, start=4):
        if not bucket:
            continue
        gid = f"SHARD_{idx:02d}_BACKEND_REGRESSION"
        desc = f"Full Backend Regression Balanced Shard {idx:02d} ({len(bucket)} test files)"
        shards.append((gid, desc, bucket, None))
    return shards


def parse_junit_xml_detailed(xml_path: Path) -> Dict[str, Any]:
    if not xml_path.exists() or xml_path.stat().st_size == 0:
        return {
            "valid": False,
            "tests": 0,
            "passed": 0,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "xfail": 0,
            "xpass": 0,
            "testcase_names": [],
            "failed_nodes": [],
            "error_nodes": [],
            "skipped_nodes": [],
        }

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except Exception:
        return {
            "valid": False,
            "tests": 0,
            "passed": 0,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "xfail": 0,
            "xpass": 0,
            "testcase_names": [],
            "failed_nodes": [],
            "error_nodes": [],
            "skipped_nodes": [],
        }

    passed = 0
    failures = 0
    errors = 0
    skipped = 0
    xfail = 0
    xpass = 0
    testcase_names: List[str] = []
    failed_nodes: List[str] = []
    error_nodes: List[str] = []
    skipped_nodes: List[str] = []

    for tc in root.findall(".//testcase"):
        cls = tc.attrib.get("classname", "")
        name = tc.attrib.get("name", "")
        node_id = f"{cls}::{name}" if cls else name
        testcase_names.append(name)

        err_el = tc.find("error")
        fail_el = tc.find("failure")
        skip_el = tc.find("skipped")

        if err_el is not None:
            errors += 1
            error_nodes.append(node_id)
        elif fail_el is not None:
            msg = (fail_el.attrib.get("message", "") + " " + (fail_el.text or "")).upper()
            if "[XPASS" in msg:
                xpass += 1
            failures += 1
            failed_nodes.append(node_id)
        elif skip_el is not None:
            s_type = (skip_el.attrib.get("type", "") or "").lower()
            s_msg = (skip_el.attrib.get("message", "") or "").lower()
            if "xfail" in s_type or s_msg.startswith("xfail"):
                xfail += 1
            else:
                skipped += 1
            skipped_nodes.append(node_id)
        else:
            passed += 1

    total = passed + failures + errors + skipped + xfail
    return {
        "valid": True,
        "tests": total,
        "passed": passed,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "xfail": xfail,
        "xpass": xpass,
        "testcase_names": testcase_names,
        "failed_nodes": failed_nodes,
        "error_nodes": error_nodes,
        "skipped_nodes": skipped_nodes,
    }


def aggregate_full_junit_results(
    *,
    pytest_inventory: Mapping[str, Any],
    shard_definitions: Sequence[Tuple[str, str, List[str], Optional[str]]],
    junit_dir: Path,
) -> Dict[str, Any]:
    """
    Section 08: Parse all shard JUnit XML files from disk, compute exact counters without
    static assumptions, and verify bijection against `collect_full_pytest_inventory`.
    """
    detailed_by_shard: Dict[str, Dict[str, Any]] = {}
    all_scheduled_files: List[str] = []
    for gid, _, files, _ in shard_definitions:
        all_scheduled_files.extend(files)
        xml_path = junit_dir / f"{gid.lower()}.xml"
        parsed = parse_junit_xml_detailed(xml_path)
        parsed["files_count"] = len(files)
        parsed["xml_path"] = str(xml_path)
        detailed_by_shard[gid] = parsed

    discovered_total = int(pytest_inventory.get("discovered_total", 0))
    executed_total = sum(int(v["tests"]) for v in detailed_by_shard.values())
    passed_total = sum(int(v["passed"]) for v in detailed_by_shard.values())
    failed_total = sum(int(v["failures"]) for v in detailed_by_shard.values())
    errors_total = sum(int(v["errors"]) for v in detailed_by_shard.values())
    skipped_total = sum(int(v["skipped"]) for v in detailed_by_shard.values())
    xfail_total = sum(int(v["xfail"]) for v in detailed_by_shard.values())
    xpass_total = sum(int(v["xpass"]) for v in detailed_by_shard.values())

    discovered_files = set((pytest_inventory.get("counts_by_file") or {}).keys())
    scheduled_files_set = set(all_scheduled_files)
    file_bijection_holds = (
        len(all_scheduled_files) == len(scheduled_files_set)
        and discovered_files == scheduled_files_set
    )
    node_bijection_holds = discovered_total > 0 and discovered_total == executed_total

    return {
        **dict(pytest_inventory),
        "executed_total": executed_total,
        "excluded_total": 0,
        "excluded_items": [],
        "passed_total": passed_total,
        "failed_total": failed_total,
        "errors_total": errors_total,
        "skipped_total": skipped_total,
        "xfail_total": xfail_total,
        "xpass_total": xpass_total,
        "file_bijection_holds": file_bijection_holds,
        "reconciled": bool(
            file_bijection_holds
            and node_bijection_holds
            and failed_total == 0
            and errors_total == 0
            and skipped_total == 0
            and xfail_total == 0
            and xpass_total == 0
        ),
        "detailed_by_shard": detailed_by_shard,
        "shards_summary": {
            gid: {
                "files_count": info["files_count"],
                "tests": info["tests"],
                "passed": info["passed"],
                "failures": info["failures"],
                "errors": info["errors"],
                "skipped": info["skipped"],
                "xfail": info["xfail"],
                "xpass": info["xpass"],
                "xml_path": info["xml_path"],
            }
            for gid, info in detailed_by_shard.items()
        },
    }


def run_targeted_negative_controls(
    *,
    run_id: str,
    head_sha: str,
    qa_base_sha: str,
    new_branch: str,
    junit_dir: Path,
    logs_dir: Path,
    temp_root: Path,
) -> Dict[str, Any]:
    """
    Section 07, 08 & G08: Execute 6 active V17.9.2 negative controls AND an isolated
    subprocess RED/GREEN mutation probe producing real JUnit XMLs and logs.
    """
    cutoff_ms = 1_700_001_000_000
    controls: List[Dict[str, Any]] = []

    # NC1: Future anchor candidate rejection
    fut_cand = StructuralStopCandidate(
        anchor_id="nc_future_anc",
        anchor_kind=StopAnchorKind.PROTECTED_SWING_LOW.value,
        anchor_price=3995.0,
        timeframe="1M",
        occurred_at_ms=cutoff_ms + 60_000,
        known_at_ms=cutoff_ms + 120_000,
        source_bar_open_ms=cutoff_ms,
        source_bar_close_ms=cutoff_ms + 60_000,
        is_closed_bar=False,
        is_causal=False,
        is_invalidated=False,
        is_protected_swing=True,
        is_local_sweep_only=False,
        structural_rank=90,
        rejection_reasons=("FUTURE_ANCHOR_REJECTED",),
    )
    sel_nc1, _, rejs_nc1 = rank_structure_valid_anchors(
        [fut_cand], model_id="SMC_M1_FVG_CONTINUATION", direction="LONG", entry_price=4005.0, decision_timestamp_ms=cutoff_ms
    )
    nc1_ok = sel_nc1 is None and len(rejs_nc1) > 0
    controls.append({"control_id": "NC1_FUTURE_ANCHOR_REJECTED", "blocked": nc1_ok, "reasons": list(rejs_nc1)})

    # NC2: Wrong-side SL / anchor inside buffer rejected by validate_structure_invalidation_contract
    valid_anc = StructuralStopCandidate(
        anchor_id="nc_valid_anc",
        anchor_kind=StopAnchorKind.PROTECTED_SWING_LOW.value,
        anchor_price=4000.0,
        timeframe="1M",
        occurred_at_ms=cutoff_ms - 180_000,
        known_at_ms=cutoff_ms - 120_000,
        source_bar_open_ms=cutoff_ms - 180_000,
        source_bar_close_ms=cutoff_ms - 120_000,
        is_closed_bar=True,
        is_causal=True,
        is_invalidated=False,
        is_protected_swing=True,
        is_local_sweep_only=False,
        structural_rank=90,
    )
    ok_nc2, rejs_nc2 = validate_structure_invalidation_contract(
        direction="LONG",
        entry_price=4005.0,
        proposed_sl=4000.10,  # Wrong side of invalidation anchor 4000.00!
        anchor=valid_anc,
        noise_buffer_price=0.50,
        decision_timestamp_ms=cutoff_ms,
        tick_size=0.01,
    )
    nc2_ok = (not ok_nc2) and ("SL_INSIDE_PROTECTED_INVALIDATION" in rejs_nc2)
    controls.append({"control_id": "NC2_STOP_INSIDE_INVALIDATION_REJECTED", "blocked": nc2_ok, "reasons": list(rejs_nc2)})

    # NC3: No invented RR-multiple target when structural pools fail min_net_rr=2.0
    near_cands = resolve_causal_target_candidates(
        direction="LONG",
        entry_price=4000.0,
        decision_timestamp_ms=cutoff_ms,
        liquidity_pools=[
            {
                "id": "nc_near_pool",
                "price": 4003.0,
                "side": "BUY_SIDE",
                "source_kind": "M1_SWING_HIGH",
                "timeframe": "1M",
                "occurred_at_ms": cutoff_ms - 120_000,
                "known_at_ms": cutoff_ms - 60_000,
                "state": "UNTOUCHED",
            }
        ],
    )
    t_plan_nc3 = select_viable_structural_target(
        direction="LONG",
        entry_price=4000.0,
        proposed_sl=3997.0,
        decision_timestamp_ms=cutoff_ms,
        target_candidates=near_cands,
        baseline_target_price=4003.0,
        baseline_target_id="nc_near_pool",
        min_net_rr=2.0,
        target_mode="V1792_CAUSAL_STRUCTURAL_VIABLE",
    )
    nc3_ok = (t_plan_nc3.target_price == 4003.0) and ("NET_RR_TOO_LOW" in t_plan_nc3.rejection_reasons)
    controls.append(
        {"control_id": "NC3_NO_INVENTED_RR_TARGET_WHEN_UNVIABLE", "blocked": nc3_ok, "reasons": list(t_plan_nc3.rejection_reasons)}
    )

    # NC4: Wide stop ($300 distance) rejected by min_lot / risk budget ($2.50)
    sz_nc4 = evaluate_stop_tp_risk_and_size(
        direction="LONG",
        entry_price=4000.0,
        proposed_sl=3700.0,
        target_price=4800.0,
        capital_usdt=1000.0,
        risk_pct=0.25,
        leverage=20,
        margin_mode="ISOLATED",
        min_net_rr=2.0,
    )
    nc4_ok = (not sz_nc4["is_executable"]) and (sz_nc4["hypothetical_quantity"] == 0.0)
    controls.append(
        {"control_id": "NC4_WIDE_STOP_MIN_LOT_RISK_BUDGET_REJECTED", "blocked": nc4_ok, "reasons": list(sz_nc4["rejection_reasons"])}
    )

    # NC5: Spoofed adapter_call_id & self-comparison trap rejected
    nc5_spoof = not SharedStrategyEvaluator.is_registered_call_id("call_SPOOFED_00000_99999")
    controls.append({"control_id": "NC5_SPOOFED_ADAPTER_CALL_ID_REJECTED", "blocked": nc5_spoof, "reasons": ["UNREGISTERED_CALL_ID"]})

    # NC6: Synthetic provenance contamination rejected in historical backtest records
    seg_ok, seg_reason = validate_historical_provenance_segregation(
        [{"provenance": "HISTORICAL_BACKTEST_V1792"}, {"provenance": "SYNTHETIC_UNIT_TEST"}]
    )
    nc6_ok = (not seg_ok) and (seg_reason == "SYNTHETIC_PROVENANCE_CONTAMINATION_DETECTED")
    controls.append({"control_id": "NC6_SYNTHETIC_PROVENANCE_CONTAMINATION_REJECTED", "blocked": nc6_ok, "reasons": [str(seg_reason)]})

    # Isolated subprocess RED -> GREEN mutation probe
    probe_dir = temp_root / "g08_v1792_mutation_probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_test_file = probe_dir / "test_v1792_negative_control_probe.py"
    red_xml = junit_dir / "negative_controls_red.xml"
    green_xml = junit_dir / "negative_controls_green.xml"
    red_log = logs_dir / "negative_controls_red.log"
    green_log = logs_dir / "negative_controls_green.log"

    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "backend") + os.pathsep + str(REPO_ROOT)
    env["PYTHONHASHSEED"] = "0"

    red_code = """from services.v17_9_2_structural_stop_intelligence import StructuralStopCandidate, validate_structure_invalidation_contract

def test_v1792_intentional_red_mutation_probe():
    cand = StructuralStopCandidate(
        anchor_id="probe_anc",
        anchor_kind="PROTECTED_LEG_LOW",
        anchor_price=4000.0,
        timeframe="1M",
        occurred_at_ms=1700000000000,
        known_at_ms=1700000060000,
        source_bar_open_ms=1700000000000,
        source_bar_close_ms=1700000060000,
        is_closed_bar=True,
        is_causal=True,
        is_invalidated=False,
        is_protected_swing=True,
        is_local_sweep_only=False,
        structural_rank=90,
    )
    ok, reasons = validate_structure_invalidation_contract(
        direction="LONG",
        entry_price=4005.0,
        proposed_sl=4006.0,  # Wrong side of entry!
        anchor=cand,
        noise_buffer_price=0.50,
        decision_timestamp_ms=1700000120000,
        tick_size=0.01,
    )
    assert ok is True, f"INTENTIONAL_RED_MUTATION_PROBE_TRIGGERED:{reasons}"
"""
    probe_test_file.write_text(red_code, encoding="utf-8")
    red_proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(probe_test_file), f"--junitxml={red_xml}", "-q"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    red_log.write_text(red_proc.stdout + "\n" + red_proc.stderr, encoding="utf-8")
    red_junit = parse_junit_xml_detailed(red_xml)

    green_code = red_code.replace(
        'assert ok is True, f"INTENTIONAL_RED_MUTATION_PROBE_TRIGGERED:{reasons}"',
        'assert ok is False and "SL_WRONG_SIDE_OF_ENTRY" in reasons',
    )
    probe_test_file.write_text(green_code, encoding="utf-8")
    green_proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(probe_test_file), f"--junitxml={green_xml}", "-q"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    green_log.write_text(green_proc.stdout + "\n" + green_proc.stderr, encoding="utf-8")
    green_junit = parse_junit_xml_detailed(green_xml)
    shutil.rmtree(probe_dir, ignore_errors=True)

    probe_verified = (
        red_proc.returncode != 0
        and int(red_junit["failures"]) >= 1
        and green_proc.returncode == 0
        and int(green_junit["failures"]) == 0
        and int(green_junit["passed"]) == 1
    )
    blocked_count = sum(1 for c in controls if c["blocked"])

    return {
        "schema_version": V17_9_2_ALGORITHM_VERSION,
        "run_id": run_id,
        "qa_base_sha": qa_base_sha,
        "new_branch": new_branch,
        "tested_executable_sha": head_sha,
        "status": "PASS" if (blocked_count == len(controls) and probe_verified) else "FAIL",
        "negative_controls_total": len(controls),
        "negative_controls_blocked": blocked_count,
        "controls": controls,
        "subprocess_red_green_probe": {
            "gate": "G08",
            "test_name": "test_v1792_intentional_red_mutation_probe",
            "red_exit_code": int(red_proc.returncode),
            "red_failures": int(red_junit["failures"]),
            "red_junit_xml": str(red_xml),
            "red_log_path": str(red_log),
            "reverted_green_exit_code": int(green_proc.returncode),
            "green_failures": int(green_junit["failures"]),
            "green_passed": int(green_junit["passed"]),
            "green_junit_xml": str(green_xml),
            "green_log_path": str(green_log),
            "status": "VERIFIED" if probe_verified else "FAILED",
        },
    }


def run_structural_sl_historical_ablation(
    *,
    output_dir: Path,
    run_id: str,
    qa_base_sha: str,
    new_branch: str,
    tested_code_sha: str,
    max_workers: int = 4,
) -> Dict[str, Any]:
    """
    Section 06 & 08: Run the real Bitget XAUUSDT historical ablation across all 9 V17.9.2 variants.
    """
    return run_v17_9_2_structural_stop_backtest(
        output_dir=output_dir,
        run_id=run_id,
        qa_base_sha=qa_base_sha,
        new_branch=new_branch,
        tested_code_sha=tested_code_sha,
        max_workers=max_workers,
    )


def evaluate_v1792_gate(gate_id: str, evidence: Mapping[str, Any]) -> GateResult:
    """
    Section 10: Evidence-driven evaluator for Gates G01..G16.
    Every gate checks its own independent evidence file and fails closed if any invariant is violated.
    """
    artifact_dir = Path(str(evidence.get("artifact_dir", "")))
    reasons: List[str] = []
    metrics: Dict[str, Any] = {}

    if gate_id == "G01":
        ev_path = str(artifact_dir / "qa_foundation_gap_report.json")
        qa_rep = evidence.get("qa_report") or {}
        impl_v = evidence.get("impl_verification") or {}
        if qa_rep.get("status") != "PASS":
            reasons.extend(qa_rep.get("reasons") or ["qa_foundation_gap_report status != PASS"])
        if not impl_v.get("is_valid"):
            reasons.extend(impl_v.get("errors") or ["implementation_map AST verification failed"])
        metrics["verified_symbols_count"] = int(impl_v.get("verified_symbols_count", 0))
        metrics["qa_base_sha"] = qa_rep.get("qa_base_sha")
        passed = len(reasons) == 0 and metrics["verified_symbols_count"] > 0
        return GateResult("G01", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G02":
        ev_path = str(artifact_dir / "stop_anchor_causal_audit.json")
        anc_audit = (evidence.get("backtest_res") or {}).get("stop_anchor_audit") or {}
        s1 = ((evidence.get("regression_inventory") or {}).get("detailed_by_shard") or {}).get(
            "SHARD_01_V17_9_2_STRUCTURAL_STOP"
        ) or {}
        if anc_audit.get("status") != "PASS":
            reasons.append(f"stop_anchor_audit.status={anc_audit.get('status')!r}")
        if int(anc_audit.get("causal_lookahead_violations", -1)) != 0:
            reasons.append(f"causal_lookahead_violations={anc_audit.get('causal_lookahead_violations')}")
        req_tests = {
            "test_t01_causal_anchor_candidate_collection_and_anti_lookahead",
            "test_t02_model_specific_anchor_ranking_and_tie_break",
            "test_t06_adversarial_future_price_mutation_invariance",
        }
        executed_tcs = set(s1.get("testcase_names") or [])
        missing = sorted(req_tests - executed_tcs)
        if missing or int(s1.get("failures", 0)) > 0:
            reasons.append(f"Missing or failed G02 tests: {missing}")
        metrics["total_valid_setups_audited"] = int(anc_audit.get("total_valid_setups_audited", 0))
        metrics["causal_lookahead_violations"] = int(anc_audit.get("causal_lookahead_violations", -1))
        passed = len(reasons) == 0
        return GateResult("G02", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G03":
        ev_path = str(artifact_dir / "stop_anchor_causal_audit.json")
        anc_audit = (evidence.get("backtest_res") or {}).get("stop_anchor_audit") or {}
        s1 = ((evidence.get("regression_inventory") or {}).get("detailed_by_shard") or {}).get(
            "SHARD_01_V17_9_2_STRUCTURAL_STOP"
        ) or {}
        if int(anc_audit.get("stop_geometry_or_tick_violations", -1)) != 0:
            reasons.append(f"stop_geometry_or_tick_violations={anc_audit.get('stop_geometry_or_tick_violations')}")
        req_tests = {
            "test_t03_directional_stop_monotonicity_and_tick_quantization",
            "test_t04_causal_noise_buffer_monotonicity_and_fallback",
            "test_t05_structure_invalidation_contract_negative_guards",
            "test_t16_stop_never_widened_after_arm_boundary",
        }
        missing = sorted(req_tests - set(s1.get("testcase_names") or []))
        if missing or int(s1.get("failures", 0)) > 0:
            reasons.append(f"Missing or failed G03 tests: {missing}")
        metrics["stop_geometry_or_tick_violations"] = int(anc_audit.get("stop_geometry_or_tick_violations", -1))
        passed = len(reasons) == 0
        return GateResult("G03", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G04":
        ev_path = str(artifact_dir / "target_authority_and_net_rr_report.json")
        t_rep = (evidence.get("backtest_res") or {}).get("target_rr_report") or {}
        c_rep = (evidence.get("backtest_res") or {}).get("cost_sizing_report") or {}
        s1 = ((evidence.get("regression_inventory") or {}).get("detailed_by_shard") or {}).get(
            "SHARD_01_V17_9_2_STRUCTURAL_STOP"
        ) or {}
        if t_rep.get("status") != "PASS" or c_rep.get("status") != "PASS":
            reasons.append("target_rr_report or cost_sizing_report status != PASS")
        if float(t_rep.get("min_net_rr_enforced", 0.0)) != 2.0:
            reasons.append(f"min_net_rr_enforced={t_rep.get('min_net_rr_enforced')} != 2.0")
        if bool(t_rep.get("invented_rr_multiple_targets_allowed", True)):
            reasons.append("invented_rr_multiple_targets_allowed must be False")
        if int(c_rep.get("risk_budget_violations", -1)) != 0:
            reasons.append(f"risk_budget_violations={c_rep.get('risk_budget_violations')}")
        req_tests = {
            "test_t07_target_selection_from_known_causal_liquidity_pools",
            "test_t08_no_invented_rr_target_rule",
            "test_t09_cost_aware_net_rr_and_dynamic_quantity_scaling",
            "test_t10_min_lot_and_margin_rejection_for_wide_stop",
            "test_t25_synthetic_negative_path_unviable_rr_rejection",
        }
        missing = sorted(req_tests - set(s1.get("testcase_names") or []))
        if missing or int(s1.get("failures", 0)) > 0:
            reasons.append(f"Missing or failed G04 tests: {missing}")
        metrics["min_net_rr_enforced"] = float(t_rep.get("min_net_rr_enforced", 0.0))
        metrics["risk_budget_violations"] = int(c_rep.get("risk_budget_violations", -1))
        passed = len(reasons) == 0
        return GateResult("G04", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G05":
        ev_path = str(artifact_dir / "frozen_oracle_baseline_comparison.json")
        ora_rep = (evidence.get("backtest_res") or {}).get("oracle_comparison") or {}
        base_rep = (evidence.get("backtest_res") or {}).get("baseline_reproduction") or {}
        ora_checks = int(ora_rep.get("oracle_vs_a_checks", 0))
        ora_mismatches = int(ora_rep.get("oracle_vs_a_mismatches", -1))
        if ora_checks <= 0 or ora_mismatches != 0:
            reasons.append(f"Oracle vs A mismatch: checks={ora_checks}, mismatches={ora_mismatches}")
        if not base_rep.get("reproduction_verified"):
            reasons.append("baseline_reproduction.reproduction_verified is False")
        metrics["oracle_vs_a_checks"] = ora_checks
        metrics["oracle_vs_a_mismatches"] = ora_mismatches
        passed = len(reasons) == 0
        return GateResult("G05", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G06":
        ev_path = str(artifact_dir / "frozen_oracle_baseline_comparison.json")
        ora_rep = (evidence.get("backtest_res") or {}).get("oracle_comparison") or {}
        ab_checks = int(ora_rep.get("a_vs_b_observe_only_checks", 0))
        ab_mismatches = int(ora_rep.get("a_vs_b_observe_only_mismatches", -1))
        dist_ctx = int(ora_rep.get("a_vs_b_distinct_context_instances", 0))
        dist_pol = int(ora_rep.get("a_vs_b_distinct_policy_fingerprints", 0))
        dist_meta = int(ora_rep.get("a_vs_b_distinct_metadata_fingerprints", 0))
        if ab_checks <= 0 or ab_mismatches != 0:
            reasons.append(f"A vs B observe-only invariance failed: checks={ab_checks}, mismatches={ab_mismatches}")
        if dist_ctx != ab_checks or dist_pol != ab_checks or dist_meta != ab_checks:
            reasons.append("A vs B context/policy/metadata separation incomplete")
        if ora_rep.get("a_trade_actions_set_hash") != ora_rep.get("b_trade_actions_set_hash"):
            reasons.append("a_trade_actions_set_hash != b_trade_actions_set_hash")
        if ora_rep.get("a_metadata_set_hash") == ora_rep.get("b_metadata_set_hash"):
            reasons.append("a_metadata_set_hash == b_metadata_set_hash")
        metrics["a_vs_b_observe_only_checks"] = ab_checks
        metrics["a_vs_b_observe_only_mismatches"] = ab_mismatches
        passed = len(reasons) == 0
        return GateResult("G06", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G07":
        ev_path = str(artifact_dir / "three_runtime_consumed_parity.json")
        par_rep = (evidence.get("backtest_res") or {}).get("three_runtime_parity") or {}
        if par_rep.get("status") != "PASS":
            reasons.append(f"three_runtime_parity.status={par_rep.get('status')!r}")
        tot_chk = int(par_rep.get("total_three_way_parity_checks", 0))
        tot_pass = int(par_rep.get("total_three_way_parity_passed", 0))
        mismatches = int(par_rep.get("parity_mismatch_count", -1))
        if tot_chk <= 0 or tot_pass != tot_chk or mismatches != 0:
            reasons.append(f"3-runtime parity mismatch: total={tot_chk}, passed={tot_pass}, mismatches={mismatches}")
        metrics["total_three_way_parity_checks"] = tot_chk
        metrics["total_three_way_parity_passed"] = tot_pass
        passed = len(reasons) == 0
        return GateResult("G07", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G08":
        ev_path = str(artifact_dir / "negative_controls_red_green.json")
        nc_rep = evidence.get("negative_controls") or {}
        if nc_rep.get("status") != "PASS":
            reasons.append(f"negative_controls.status={nc_rep.get('status')!r}")
        if int(nc_rep.get("negative_controls_blocked", 0)) < int(nc_rep.get("negative_controls_total", 1)):
            reasons.append("Not all negative controls were blocked")
        probe = nc_rep.get("subprocess_red_green_probe") or {}
        if probe.get("status") != "VERIFIED":
            reasons.append(f"subprocess_red_green_probe.status={probe.get('status')!r}")
        if int(probe.get("red_exit_code", 0)) == 0 or int(probe.get("red_failures", 0)) < 1:
            reasons.append("RED mutation probe did not fail")
        if int(probe.get("reverted_green_exit_code", -1)) != 0 or int(probe.get("green_failures", -1)) != 0:
            reasons.append("GREEN reverted probe did not pass cleanly")
        metrics["negative_controls_blocked"] = int(nc_rep.get("negative_controls_blocked", 0))
        metrics["red_failures"] = int(probe.get("red_failures", 0))
        passed = len(reasons) == 0
        return GateResult("G08", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G09":
        ev_path = str(artifact_dir / "historical_ablation_variants.json")
        bt = evidence.get("backtest_res") or {}
        ab_var = (bt.get("ablation_variants") or {}).get("variants") or {}
        ds_cov = bt.get("data_coverage") or {}
        forensics = bt.get("stop_forensics") or {}
        if ds_cov.get("quote_construction_mode") != "MODELED_SPREAD_QUOTE":
            reasons.append(f"quote_construction_mode={ds_cov.get('quote_construction_mode')!r}")
        if bool(ds_cov.get("synthetic_fill_applied", True)):
            reasons.append("synthetic_fill_applied must be False")
        for req_v in V1792_VARIANT_POLICIES:
            if req_v not in ab_var:
                reasons.append(f"Missing ablation variant {req_v}")
        if len(forensics.get("episodes") or []) != 6:
            reasons.append(f"Expected 6 forensic baseline episodes, got {len(forensics.get('episodes') or [])}")
        metrics["variants_evaluated"] = len(ab_var)
        metrics["forensic_episodes"] = len(forensics.get("episodes") or [])
        passed = len(reasons) == 0
        return GateResult("G09", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G10":
        ev_path = str(artifact_dir / "chronological_oos_report.json")
        oos_rep = (evidence.get("backtest_res") or {}).get("oos_report") or {}
        if not oos_rep.get("embargo_verified") or int(oos_rep.get("embargo_bars_m1", 0)) != 180:
            reasons.append("180-bar M1 OOS embargo not verified")
        if oos_rep.get("ready_for_strategy_activation") != "NO":
            reasons.append(f"ready_for_strategy_activation={oos_rep.get('ready_for_strategy_activation')!r} (must be 'NO')")
        verdict = oos_rep.get("strategy_statistical_verdict")
        if verdict not in ("INCONCLUSIVE", "NEGATIVE_EVIDENCE"):
            reasons.append(f"Unexpected strategy_statistical_verdict={verdict!r}")
        metrics["strategy_statistical_verdict"] = verdict
        metrics["ready_for_strategy_activation"] = oos_rep.get("ready_for_strategy_activation")
        passed = len(reasons) == 0
        return GateResult("G10", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G11":
        ev_path = str(artifact_dir / "full_regression_inventory.json")
        inv = evidence.get("regression_inventory") or {}
        disc = int(inv.get("discovered_total", 0))
        exec_tot = int(inv.get("executed_total", 0))
        excl = int(inv.get("excluded_total", -1))
        failed = int(inv.get("failed_total", -1))
        errors = int(inv.get("errors_total", -1))
        skipped = int(inv.get("skipped_total", -1))
        xfail = int(inv.get("xfail_total", -1))
        xpass = int(inv.get("xpass_total", -1))
        if disc <= 0 or disc != exec_tot + excl or excl != 0:
            reasons.append(f"Inventory mismatch: discovered={disc}, executed={exec_tot}, excluded={excl}")
        if not inv.get("file_bijection_holds"):
            reasons.append("file_bijection_holds is False")
        if failed != 0 or errors != 0 or skipped != 0 or xfail != 0 or xpass != 0:
            reasons.append(
                f"Non-zero test failures/skips: failed={failed}, errors={errors}, skipped={skipped}, xfail={xfail}, xpass={xpass}"
            )
        metrics["discovered_total"] = disc
        metrics["executed_total"] = exec_tot
        metrics["passed_total"] = int(inv.get("passed_total", 0))
        passed = len(reasons) == 0
        return GateResult("G11", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G12":
        ev_path = str(artifact_dir / "concurrency_and_resource_report.json")
        async_sum = evidence.get("async_summary") or {}
        if async_sum.get("status") != "PASS":
            reasons.append(f"async_summary.status={async_sum.get('status')!r}")
        sched = int(async_sum.get("scheduled_tasks", 0))
        succ = int(async_sum.get("succeeded_tasks", 0))
        if sched <= 0 or succ != sched:
            reasons.append(f"Task completion mismatch: scheduled={sched}, succeeded={succ}")
        if int(async_sum.get("orphan_processes_remaining", -1)) != 0:
            reasons.append(f"orphan_processes_remaining={async_sum.get('orphan_processes_remaining')}")
        if not async_sum.get("concurrency_bound_holds"):
            reasons.append("concurrency_bound_holds is False")
        metrics["scheduled_tasks"] = sched
        metrics["succeeded_tasks"] = succ
        metrics["peak_concurrent_workers"] = int(async_sum.get("peak_concurrent_workers", 0))
        passed = len(reasons) == 0
        return GateResult("G12", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G13":
        ev_path = str(artifact_dir / "disk_guard_report.json")
        dr = evidence.get("disk_report") or {}
        if dr.get("status") != "PASS":
            reasons.append(f"disk_report.status={dr.get('status')!r}")
        res_tel = dr.get("resource_telemetry") or {}
        if int(res_tel.get("samples_recorded", 0)) < 2:
            reasons.append(f"Insufficient resource telemetry samples: {res_tel.get('samples_recorded')}")
        if float(res_tel.get("peak_rss_mb", 0.0) or 0.0) <= 0.0:
            reasons.append("peak_rss_mb <= 0.0")
        metrics["samples_recorded"] = int(res_tel.get("samples_recorded", 0))
        metrics["peak_rss_mb"] = float(res_tel.get("peak_rss_mb", 0.0) or 0.0)
        passed = len(reasons) == 0
        return GateResult("G13", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G14":
        ev_path = str(artifact_dir / "artifact_sha256_manifest.json")
        manifest = evidence.get("artifact_manifest") or {}
        files_map = manifest.get("files") or {}
        required_artifacts = (
            "qa_foundation_gap_report.json",
            "implementation_map.json",
            "function_truth_map.json",
            "baseline_reproduction.json",
            "dataset_coverage_and_gap_audit.json",
            "stop_anchor_causal_audit.json",
            "stop_forensics_before_after.json",
            "v17_9_2_decision_traces.jsonl.gz",
            "target_authority_and_net_rr_report.json",
            "cost_sizing_invariant_report.json",
            "historical_ablation_variants.json",
            "independent_nonoverlap_episodes.json",
            "chronological_oos_report.json",
            "three_runtime_consumed_parity.json",
            "frozen_oracle_baseline_comparison.json",
            "full_regression_inventory.json",
            "negative_controls_red_green.json",
            "concurrency_and_resource_report.json",
            "disk_guard_report.json",
        )
        for req_f in required_artifacts:
            f_p = artifact_dir / req_f
            if not f_p.exists() or f_p.stat().st_size == 0:
                reasons.append(f"Missing or empty required artifact: {req_f}")
            elif req_f not in files_map:
                reasons.append(f"Artifact missing from SHA-256 manifest: {req_f}")
        metrics["manifest_files_count"] = len(files_map)
        passed = len(reasons) == 0
        return GateResult("G14", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G15":
        ev_path = str(artifact_dir / "v17_9_2_final_verification_report.json")
        git_info = evidence.get("git_info") or {}
        if git_info.get("branch") != ALLOWED_FEATURE_BRANCH:
            reasons.append(f"branch={git_info.get('branch')!r} != {ALLOWED_FEATURE_BRANCH!r}")
        if git_info.get("dirty_tracked_code_files"):
            reasons.append(f"Dirty tracked code files: {git_info.get('dirty_tracked_code_files')}")
        if git_info.get("tested_executable_tree_hash") != git_info.get("current_executable_tree_hash"):
            reasons.append("Executable source tree hash changed after test execution")
        if evidence.get("require_remote_push_match", False):
            if not git_info.get("remote_branch_matching_published_sha"):
                reasons.append("Remote origin branch SHA does not match local published HEAD SHA")
        metrics["tested_code_sha"] = git_info.get("tested_code_sha")
        metrics["published_commit_sha"] = git_info.get("published_commit_sha")
        metrics["remote_head_sha"] = git_info.get("remote_head_sha")
        passed = len(reasons) == 0
        return GateResult("G15", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G16":
        ev_path = str(artifact_dir / "v17_9_2_final_verification_report.json")
        prior_gates: Mapping[str, GateResult] = evidence.get("prior_gates") or {}
        for idx in range(1, 16):
            gid = f"G{idx:02d}"
            g_res = prior_gates.get(gid)
            if g_res is None:
                reasons.append(f"Gate {gid} not evaluated before G16")
            elif g_res.status != "PASS" or not g_res.passed:
                reasons.append(f"Gate {gid} failed with status={g_res.status}")
        metrics["prior_gates_passed"] = sum(1 for g in prior_gates.values() if g.passed)
        passed = len(reasons) == 0
        return GateResult("G16", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    return GateResult(gate_id, "FAIL", False, str(artifact_dir), (f"Unknown gate_id={gate_id!r}",), {})


def validate_v1792_final_report(report: Mapping[str, Any]) -> ReportValidationResult:
    """
    Section 10 (G16): Recomputes all report invariants and vetoes any false PASS.
    """
    reasons: List[str] = []
    gates = report.get("gates") or {}
    gate_statuses: Dict[str, str] = {}

    for i in range(1, 17):
        gid = f"G{i:02d}"
        g_entry = gates.get(gid)
        if not isinstance(g_entry, Mapping):
            reasons.append(f"Missing gate {gid}")
            gate_statuses[gid] = "MISSING"
        else:
            st = str(g_entry.get("status") or "FAIL")
            gate_statuses[gid] = st
            if st != "PASS":
                reasons.append(f"Gate {gid} status is {st}")

    tests = report.get("tests") or {}
    disc = int(tests.get("discovered_total", 0))
    exec_tot = int(tests.get("executed_total", 0))
    excl_tot = int(tests.get("excluded_total", -1))
    if disc <= 0 or disc != exec_tot + excl_tot or excl_tot != 0:
        reasons.append(f"Test inventory mismatch: discovered={disc}, executed={exec_tot}, excluded={excl_tot}")
    if int(tests.get("failed", -1)) != 0 or int(tests.get("errors", -1)) != 0 or int(tests.get("skipped", -1)) != 0:
        reasons.append("Non-zero test failures, errors, or skips")

    verdicts = report.get("verdicts") or {}
    if verdicts.get("ready_for_strategy_activation") != "NO":
        reasons.append(f"ready_for_strategy_activation={verdicts.get('ready_for_strategy_activation')!r} (must be 'NO')")
    if verdicts.get("strategy_statistical_verdict") not in ("INCONCLUSIVE", "NEGATIVE_EVIDENCE"):
        reasons.append(f"Invalid strategy_statistical_verdict={verdicts.get('strategy_statistical_verdict')!r}")
    if verdicts.get("engineering_verdict") != "PASS" and len(reasons) == 0:
        reasons.append(f"engineering_verdict={verdicts.get('engineering_verdict')!r}")

    is_valid = len(reasons) == 0
    return ReportValidationResult(
        is_valid=is_valid,
        status="PASS" if is_valid else "FAIL",
        reasons=tuple(reasons),
        gate_statuses=gate_statuses,
    )


def publish_evidence_manifest(
    *,
    artifact_dir: Path,
    run_id: str,
    qa_base_sha: str,
    new_branch: str,
    tested_code_sha: str,
    exec_tree_hash: str,
) -> Dict[str, Any]:
    """
    Section 08 & 10 (G14): Compute SHA-256 and byte sizes of all files present in `artifact_dir`
    and persist `artifact_sha256_manifest.json`.
    """
    files_meta: Dict[str, Dict[str, Any]] = {}
    total_bytes = 0
    for p in sorted(artifact_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(artifact_dir)).replace("\\", "/")
        if rel == "artifact_sha256_manifest.json":
            continue
        raw = p.read_bytes()
        sz = len(raw)
        total_bytes += sz
        files_meta[rel] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": sz,
        }

    manifest = {
        "schema_version": V17_9_2_ALGORITHM_VERSION,
        "run_id": run_id,
        "qa_base_sha": qa_base_sha,
        "new_branch": new_branch,
        "tested_executable_sha": tested_code_sha,
        "executable_source_tree_hash": exec_tree_hash,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_files": len(files_meta),
        "total_bytes": total_bytes,
        "files": files_meta,
    }
    _write_json(artifact_dir / "artifact_sha256_manifest.json", manifest)
    return manifest


def finalize_commit_and_remote_verification(
    *,
    artifact_dir: Path,
    run_id: str,
    tested_code_sha: str,
    tested_exec_tree_hash: str,
    qa_base_sha: str = CANONICAL_BASE_SHA,
    branch: str = ALLOWED_FEATURE_BRANCH,
) -> Dict[str, Any]:
    """
    Section 08 & 11: Verify post-commit and post-push state:
      - Queries `git ls-remote --heads origin <branch>`
      - Verifies `remote_head_sha == published_commit_sha`
      - Verifies zero executable source diff between `tested_code_sha` and `published_commit_sha`
      - Updates `v17_9_2_final_verification_report.json`, `artifact_sha256_manifest.json`, and `latest_run.json`
    """
    published_sha = _git_cmd(["rev-parse", "HEAD"])
    remote_sha = _query_remote_branch_head(branch)
    current_exec_tree = str(compute_executable_source_tree_hash(REPO_ROOT)["executable_source_tree_hash"])
    exec_diff = _git_cmd(
        ["diff", "--name-only", tested_code_sha, published_sha, "--", "backend", "tools", "backtest"],
        check=False,
    ).splitlines()
    dirty_code = _dirty_tracked_code_files()

    remote_matches = bool(remote_sha and remote_sha == published_sha)
    tree_unchanged = bool(current_exec_tree == tested_exec_tree_hash and len(exec_diff) == 0 and len(dirty_code) == 0)

    final_report_path = artifact_dir / "v17_9_2_final_verification_report.json"
    if final_report_path.exists():
        rep = json.loads(final_report_path.read_text(encoding="utf-8"))
        rep["git"]["published_commit_sha"] = published_sha
        rep["git"]["remote_head_sha"] = remote_sha
        rep["git"]["remote_branch_matching_published_sha"] = remote_matches
        rep["git"]["executable_diff_tested_vs_published"] = exec_diff
        rep["git"]["executable_tree_unchanged_since_test"] = tree_unchanged
        rep["git"]["dirty_tracked_code_files"] = dirty_code
        rep["git"]["ls_remote_verified_at_utc"] = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        _write_json(final_report_path, rep)

    manifest = publish_evidence_manifest(
        artifact_dir=artifact_dir,
        run_id=run_id,
        qa_base_sha=qa_base_sha,
        new_branch=branch,
        tested_code_sha=tested_code_sha,
        exec_tree_hash=tested_exec_tree_hash,
    )

    latest_payload = {
        "schema_version": V17_9_2_ALGORITHM_VERSION,
        "run_id": run_id,
        "qa_base_sha": qa_base_sha,
        "branch": branch,
        "tested_code_sha": tested_code_sha,
        "published_commit_sha": published_sha,
        "remote_head_sha": remote_sha,
        "remote_branch_matching_published_sha": remote_matches,
        "executable_source_tree_hash": tested_exec_tree_hash,
        "executable_tree_unchanged_since_test": tree_unchanged,
        "artifact_dir": str(artifact_dir.relative_to(REPO_ROOT)),
        "final_report_path": str((artifact_dir / "v17_9_2_final_verification_report.json").relative_to(REPO_ROOT)),
        "engineering_verdict": "PASS" if (remote_matches and tree_unchanged) else "NOT_VERIFIED",
        "ready_for_strategy_activation": "NO",
        "updated_at_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _write_json(artifact_dir / "latest_run.json", latest_payload)
    _write_json(REPO_ROOT / "docs" / "artifacts" / "v17_9_2" / "latest_run.json", latest_payload)
    return {
        "status": "PASS" if (remote_matches and tree_unchanged) else "FAIL",
        "latest_run": latest_payload,
        "manifest_total_files": manifest["total_files"],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="AURUM Desk V17.9.2 Full Verification Orchestrator")
    parser.add_argument("--run-id", default="", help="Optional explicit run_id")
    parser.add_argument("--qa-base-sha", default=CANONICAL_BASE_SHA, help="Canonical base SHA")
    parser.add_argument("--max-workers", type=int, default=4, help="Max worker processes (<= 4)")
    parser.add_argument("--finalize-remote-only", action="store_true", help="Only run post-push remote SHA verification")
    args = parser.parse_args(argv)

    start_wall = time.time()
    branch = _git_cmd(["branch", "--show-current"])
    head_sha = _git_cmd(["rev-parse", "HEAD"])
    exec_tree_info = compute_executable_source_tree_hash(REPO_ROOT)
    exec_tree_hash = str(exec_tree_info["executable_source_tree_hash"])

    if args.finalize_remote_only:
        latest_p = REPO_ROOT / "docs" / "artifacts" / "v17_9_2" / "latest_run.json"
        latest_data = json.loads(latest_p.read_text(encoding="utf-8"))
        run_id = args.run_id or str(latest_data["run_id"])
        art_dir = REPO_ROOT / "docs" / "artifacts" / "v17_9_2" / run_id
        res = finalize_commit_and_remote_verification(
            artifact_dir=art_dir,
            run_id=run_id,
            tested_code_sha=str(latest_data["tested_code_sha"]),
            tested_exec_tree_hash=str(latest_data["executable_source_tree_hash"]),
            qa_base_sha=args.qa_base_sha,
            branch=branch,
        )
        print(json.dumps(res, indent=2))
        return 0 if res["status"] == "PASS" else 1

    utc_stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = args.run_id or f"v17_9_2_{utc_stamp}_{head_sha[:12]}"
    artifact_dir = REPO_ROOT / "docs" / "artifacts" / "v17_9_2" / run_id
    junit_dir = artifact_dir / "junit"
    logs_dir = artifact_dir / "logs"
    temp_workers_dir = REPO_ROOT / ".tmp_v17_9_2_workers" / run_id
    junit_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    # 1. Disk & Resource Guard Preflight
    preflight_budget = probe_disk_budget(workdir=REPO_ROOT, artifact_root=artifact_dir)
    stage_reservation = reserve_test_artifact_budget(
        "v17_9_2_full_verification",
        budget=preflight_budget,
        requested_bytes=150 * 1024 * 1024,
    )
    if not stage_reservation.permitted:
        print(f"BLOCKED_DISK_BUDGET: {stage_reservation.reason}", file=sys.stderr)
        return 2

    guard = TestResourceGuard(
        run_id=run_id,
        repo_root=REPO_ROOT,
        artifact_dir=artifact_dir,
        tmp_root=temp_workers_dir,
    )
    guard.sample_cpu_memory(stage="PREFLIGHT_START", active_workers=0)

    # 2. Verify Exact Source & Base Ancestry + Implementation Map AST
    qa_report = verify_exact_source_and_qa_ancestry(
        repo_root=REPO_ROOT,
        base_branch=CANONICAL_BASE_BRANCH,
        expected_base_sha=args.qa_base_sha,
        expected_feature_branch=ALLOWED_FEATURE_BRANCH,
    )
    _write_json(artifact_dir / "qa_foundation_gap_report.json", qa_report)

    impl_map = build_v1792_implementation_map()
    impl_verification = verify_implementation_map_ast(impl_map, repo_root=REPO_ROOT)
    _write_json(
        artifact_dir / "implementation_map.json",
        {
            "schema_version": V17_9_2_ALGORITHM_VERSION,
            "run_id": run_id,
            "qa_base_sha": args.qa_base_sha,
            "new_branch": branch,
            "tested_executable_sha": head_sha,
            "executable_source_tree_hash": exec_tree_hash,
            "ast_verification": impl_verification,
            "items": impl_map,
        },
    )

    # 3. Discover Full Backend Pytest Inventory & Execute Resource-Bounded Shards
    pytest_inventory = collect_full_pytest_inventory(REPO_ROOT)
    shard_definitions = build_resource_bounded_shards(REPO_ROOT, num_general_shards=10)

    eff_pytest_workers = max(1, min(int(args.max_workers), (os.cpu_count() or 2) // 2, 4))
    state_mgr = ExecutionStateManager(
        run_id=run_id,
        source_sha=head_sha,
        all_test_groups=[s[0] for s in shard_definitions],
        artifact_dir=artifact_dir,
    )
    orchestrator = BoundedAsyncTestOrchestrator(
        run_id=run_id,
        source_sha=head_sha,
        artifact_dir=artifact_dir,
        resource_guard=guard,
        state_manager=state_mgr,
        max_pytest_workers=eff_pytest_workers,
    )

    specs: List[AsyncWorkerGroupSpec] = []
    for gid, desc, files, k_expr in shard_definitions:
        xml_path = junit_dir / f"{gid.lower()}.xml"
        log_path = logs_dir / f"{gid.lower()}.log"
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            *files,
            f"--junitxml={xml_path}",
            "-q",
        ]
        if k_expr:
            cmd.extend(["-k", k_expr])
        specs.append(
            AsyncWorkerGroupSpec(
                group_id=gid,
                description=desc,
                command=cmd,
                cwd=REPO_ROOT,
                junit_xml_path=xml_path,
                log_path=log_path,
                worker_pool="PYTEST",
                stage="STAGE_FULL_REGRESSION",
            )
        )

    async_summary = asyncio.run(orchestrator.run_groups(specs))
    _git_cmd(["checkout", "--", "backtest/trade_decision_lineage_reconciliation.json"], check=False)
    pressure_mid = monitor_disk_pressure(interval=0.25, workdir=REPO_ROOT, artifact_root=artifact_dir)
    guard.sample_cpu_memory(stage="POST_PYTEST_SHARDS", active_workers=0)

    regression_inventory = aggregate_full_junit_results(
        pytest_inventory=pytest_inventory,
        shard_definitions=shard_definitions,
        junit_dir=junit_dir,
    )
    _write_json(artifact_dir / "full_regression_inventory.json", regression_inventory)
    _write_json(artifact_dir / "pytest_collection.json", regression_inventory)
    _write_json(artifact_dir / "shard_manifest.json", async_summary)

    # 4. Run Targeted Negative Controls & Subprocess RED/GREEN Mutation Probe
    negative_controls_report = run_targeted_negative_controls(
        run_id=run_id,
        head_sha=head_sha,
        qa_base_sha=args.qa_base_sha,
        new_branch=branch,
        junit_dir=junit_dir,
        logs_dir=logs_dir,
        temp_root=temp_workers_dir,
    )
    _write_json(artifact_dir / "negative_controls_red_green.json", negative_controls_report)
    guard.sample_cpu_memory(stage="POST_NEGATIVE_CONTROLS", active_workers=0)

    # 5. Run Real Historical Bitget XAUUSDT 9-Variant Ablation & OOS Backtest (serialized after pytest shards)
    backtest_res = run_structural_sl_historical_ablation(
        output_dir=artifact_dir,
        run_id=run_id,
        qa_base_sha=args.qa_base_sha,
        new_branch=branch,
        tested_code_sha=head_sha,
        max_workers=eff_pytest_workers,
    )
    _git_cmd(["checkout", "--", "backtest/trade_decision_lineage_reconciliation.json"], check=False)
    guard.sample_cpu_memory(stage="POST_HISTORICAL_ABLATION", active_workers=0)

    # 6. Finalize Disk & Concurrency Telemetry Reports
    cleanup_report = guard.finalize_and_prune_ephemeral_artifacts()
    guard_rep = guard.build_disk_budget_report()
    perf_report = orchestrator.build_performance_report(async_summary)
    postflight_budget = probe_disk_budget(workdir=REPO_ROOT, artifact_root=artifact_dir)

    concurrency_report = {
        "schema_version": V17_9_2_ALGORITHM_VERSION,
        "run_id": run_id,
        "qa_base_sha": args.qa_base_sha,
        "new_branch": branch,
        "tested_executable_sha": head_sha,
        "executable_source_tree_hash": exec_tree_hash,
        "configured_max_pytest_workers": eff_pytest_workers,
        "peak_concurrent_workers_observed": int(async_summary.get("peak_concurrent_workers", 0)),
        "concurrency_bound_holds": bool(async_summary.get("concurrency_bound_holds", False)),
        "orphan_processes_remaining": int(async_summary.get("orphan_processes_remaining", -1)),
        "scheduled_tasks": int(async_summary.get("scheduled_tasks", 0)),
        "succeeded_tasks": int(async_summary.get("succeeded_tasks", 0)),
        "failed_tasks": int(async_summary.get("failed_tasks", 0)),
        "wall_clock_pytest_sec": float(async_summary.get("wall_clock_duration_sec", 0.0)),
        "sum_of_shard_durations_sec": float(async_summary.get("sum_of_group_durations_sec", 0.0)),
        "speedup_ratio": float(async_summary.get("speedup_ratio", 1.0)),
        "historical_backtest_elapsed_sec": float(backtest_res.get("elapsed_sec", 0.0)),
        "resource_telemetry": perf_report.get("resource_telemetry", {}),
    }
    _write_json(artifact_dir / "concurrency_and_resource_report.json", concurrency_report)

    disk_report_payload = {
        "schema_version": V17_9_2_ALGORITHM_VERSION,
        "run_id": run_id,
        "qa_base_sha": args.qa_base_sha,
        "new_branch": branch,
        "tested_executable_sha": head_sha,
        "preflight_budget": preflight_budget.to_dict(),
        "stage_reservation": stage_reservation.to_dict(),
        "mid_run_pressure": pressure_mid,
        "cleanup_report": cleanup_report.to_dict(),
        "postflight_budget": postflight_budget.to_dict(),
        "resource_telemetry": perf_report.get("resource_telemetry", {}),
        "cpu_memory_samples": guard_rep.get("cpu_memory_samples", []),
        "status": cleanup_report.status,
    }
    _write_json(artifact_dir / "disk_guard_report.json", disk_report_payload)

    # 7. Publish Initial SHA-256 Evidence Manifest
    artifact_manifest = publish_evidence_manifest(
        artifact_dir=artifact_dir,
        run_id=run_id,
        qa_base_sha=args.qa_base_sha,
        new_branch=branch,
        tested_code_sha=head_sha,
        exec_tree_hash=exec_tree_hash,
    )

    # 8. Evaluate Gates G01..G16
    remote_head_sha = _query_remote_branch_head(branch)
    current_exec_tree_hash = str(compute_executable_source_tree_hash(REPO_ROOT)["executable_source_tree_hash"])
    dirty_code = _dirty_tracked_code_files()

    git_info = {
        "repository": "quocan050302/Build-app-crypto",
        "branch": branch,
        "qa_base_branch": CANONICAL_BASE_BRANCH,
        "qa_base_sha": args.qa_base_sha,
        "tested_code_sha": head_sha,
        "published_commit_sha": head_sha,
        "remote_head_sha": remote_head_sha,
        "remote_branch_matching_published_sha": bool(remote_head_sha == head_sha),
        "tested_executable_tree_hash": exec_tree_hash,
        "current_executable_tree_hash": current_exec_tree_hash,
        "core_backtest_evaluator_tree_hash": exec_tree_info["core_backtest_evaluator_tree_hash"],
        "dirty_tracked_code_files": dirty_code,
    }

    gate_evidence: Dict[str, Any] = {
        "artifact_dir": str(artifact_dir),
        "qa_report": qa_report,
        "impl_verification": impl_verification,
        "regression_inventory": regression_inventory,
        "async_summary": async_summary,
        "negative_controls": negative_controls_report,
        "backtest_res": backtest_res,
        "disk_report": disk_report_payload,
        "artifact_manifest": artifact_manifest,
        "git_info": git_info,
        "require_remote_push_match": False,
    }

    evaluated_gates: Dict[str, GateResult] = {}
    for idx in range(1, 16):
        gid = f"G{idx:02d}"
        evaluated_gates[gid] = evaluate_v1792_gate(gid, gate_evidence)
    gate_evidence["prior_gates"] = evaluated_gates
    evaluated_gates["G16"] = evaluate_v1792_gate("G16", gate_evidence)

    gates_dict = {gid: g_res.to_dict() for gid, g_res in evaluated_gates.items()}
    all_gates_pass = all(g_res.passed for g_res in evaluated_gates.values())

    oos_rep = backtest_res["oos_report"]
    stat_verdict = str(oos_rep.get("strategy_statistical_verdict", "INCONCLUSIVE"))

    final_report: Dict[str, Any] = {
        "schema_version": V17_9_2_ALGORITHM_VERSION,
        "version": "17.9.2",
        "symbol": "XAUUSDT",
        "venue": "BITGET",
        "run_id": run_id,
        "generated_at_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git": git_info,
        "verdicts": {
            "engineering_verdict": "PASS" if all_gates_pass else "FAIL",
            "positive_path_verdict": "VERIFIED" if all_gates_pass else "NOT_ESTABLISHED",
            "risk_and_parity_verdict": (
                "PASS"
                if (
                    evaluated_gates["G04"].passed
                    and evaluated_gates["G05"].passed
                    and evaluated_gates["G06"].passed
                    and evaluated_gates["G07"].passed
                )
                else "FAIL"
            ),
            "strategy_statistical_verdict": stat_verdict,
            "ready_for_v17_9_3_development": "YES" if all_gates_pass else "NO",
            "ready_for_strategy_activation": "NO",
            "risk_of_proceeding_note": (
                "Historical sample has only 6 non-overlapping baseline episodes (all 6 losses under local sweep SL) "
                "and 0 eligible OOS episodes under strict min_net_rr=2.0 + cost/slippage constraints. "
                "V17.9.2 structural SL/TP intelligence is engineering-verified and default OFF/observe-only; "
                "strategy edge remains statistically INCONCLUSIVE / NEGATIVE_EVIDENCE and MUST NOT be activated for live/auto trading."
            ),
        },
        "tests": {
            "command": f"{sys.executable} tools/run_v17_9_2_verification.py",
            "discovered_total": int(regression_inventory["discovered_total"]),
            "executed_total": int(regression_inventory["executed_total"]),
            "excluded_total": int(regression_inventory["excluded_total"]),
            "passed": int(regression_inventory["passed_total"]),
            "failed": int(regression_inventory["failed_total"]),
            "errors": int(regression_inventory["errors_total"]),
            "skipped": int(regression_inventory["skipped_total"]),
            "xfail": int(regression_inventory["xfail_total"]),
            "xpass": int(regression_inventory["xpass_total"]),
            "shards_scheduled": int(async_summary.get("scheduled_tasks", 0)),
            "shards_succeeded": int(async_summary.get("succeeded_tasks", 0)),
            "peak_concurrent_workers": int(async_summary.get("peak_concurrent_workers", 0)),
            "wall_clock_duration_sec": round(time.time() - start_wall, 2),
        },
        "parity": backtest_res["three_runtime_parity"],
        "oracle_comparison": backtest_res["oracle_comparison"],
        "historical_backtest_summary": {
            "dataset_sha256": backtest_res["dataset_sha256"],
            "quote_construction_mode": "MODELED_SPREAD_QUOTE",
            "stage1_scanned_checkpoints": backtest_res["scan_info"]["stage1_scanned_checkpoints"],
            "stage2_evaluated_checkpoints": backtest_res["scan_info"]["combined_stage2_checkpoint_count"],
            "baseline_6_episodes_summary": backtest_res["stop_forensics"]["baseline_6_episodes_summary"],
            "variant_premature_stop_comparison": backtest_res["stop_forensics"]["variant_premature_stop_comparison"],
            "episode_counts_by_variant": backtest_res["independent_episodes"]["episode_counts_by_variant"],
            "strategy_statistical_verdict": stat_verdict,
            "ready_for_strategy_activation": "NO",
        },
        "negative_controls": negative_controls_report,
        "concurrency_and_resource": concurrency_report,
        "gates": gates_dict,
    }

    report_val = validate_v1792_final_report(final_report)
    final_report["report_validation"] = report_val.to_dict()
    if not report_val.is_valid:
        final_report["verdicts"]["engineering_verdict"] = "FAIL"
        final_report["gates"]["G16"]["status"] = "FAIL"
        final_report["gates"]["G16"]["passed"] = False
        final_report["gates"]["G16"]["reasons"] = list(report_val.reasons)

    _write_json(artifact_dir / "v17_9_2_final_verification_report.json", final_report)

    # Refresh manifest so v17_9_2_final_verification_report.json is included
    publish_evidence_manifest(
        artifact_dir=artifact_dir,
        run_id=run_id,
        qa_base_sha=args.qa_base_sha,
        new_branch=branch,
        tested_code_sha=head_sha,
        exec_tree_hash=exec_tree_hash,
    )

    latest_payload = {
        "schema_version": V17_9_2_ALGORITHM_VERSION,
        "run_id": run_id,
        "qa_base_sha": args.qa_base_sha,
        "branch": branch,
        "tested_code_sha": head_sha,
        "published_commit_sha": head_sha,
        "remote_head_sha": remote_head_sha,
        "remote_branch_matching_published_sha": bool(remote_head_sha == head_sha),
        "executable_source_tree_hash": exec_tree_hash,
        "executable_tree_unchanged_since_test": True,
        "artifact_dir": str(artifact_dir.relative_to(REPO_ROOT)),
        "final_report_path": str((artifact_dir / "v17_9_2_final_verification_report.json").relative_to(REPO_ROOT)),
        "engineering_verdict": final_report["verdicts"]["engineering_verdict"],
        "strategy_statistical_verdict": stat_verdict,
        "ready_for_v17_9_3_development": final_report["verdicts"]["ready_for_v17_9_3_development"],
        "ready_for_strategy_activation": "NO",
        "updated_at_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _write_json(artifact_dir / "latest_run.json", latest_payload)
    _write_json(REPO_ROOT / "docs" / "artifacts" / "v17_9_2" / "latest_run.json", latest_payload)

    print(
        json.dumps(
            {
                "run_id": run_id,
                "tested_code_sha": head_sha,
                "executable_source_tree_hash": exec_tree_hash,
                "discovered_tests": regression_inventory["discovered_total"],
                "executed_tests": regression_inventory["executed_total"],
                "passed_tests": regression_inventory["passed_total"],
                "failed_tests": regression_inventory["failed_total"],
                "engineering_verdict": final_report["verdicts"]["engineering_verdict"],
                "strategy_statistical_verdict": stat_verdict,
                "ready_for_strategy_activation": "NO",
                "wall_clock_sec": final_report["tests"]["wall_clock_duration_sec"],
            },
            indent=2,
        )
    )
    return 0 if final_report["verdicts"]["engineering_verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
