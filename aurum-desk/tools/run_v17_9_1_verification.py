"""
AURUM Desk V17.9.1 — Bounded Async Verification, Full Regression & Historical Backtest Runner
=============================================================================================
Executes the complete V17.9.1 acceptance closure & verification suite against the committed HEAD tree:
  1. Git preflight, remote HEAD verification (`git ls-remote --heads origin <branch>`), and executable source tree SHA
  2. Disk budget probe & reservation (`probe_disk_budget`, `reserve_test_artifact_budget`, `TestResourceGuard`)
  3. Full backend pytest inventory discovery (`pytest backend/tests --collect-only -q` -> `pytest_collection.json`)
  4. Bounded asynchronous pytest shard execution via `BoundedAsyncTestOrchestrator` (`tools/run_v17_9_async_regression.py`)
     covering 100% of discovered `backend/tests/test_*.py` files with JUnit XMLs, per-shard logs, and `shard_manifest.json`
  5. Real causal audit + isolated subprocess RED/GREEN failure-sensitivity probe (`causal_audit_report.json`)
  6. Real historical Bitget XAUUSDT A/B/C/D backtest & 3-mode + independent oracle consumed decision parity
     via `run_v17_9_1_historical_ab_backtest` (`backtest/historical_dataset_XAUUSDT.json.gz`)
  7. Evidence-driven gate evaluation (`collect_gate_evidence`, `evaluate_gate`, `validate_final_verification_report`)
     with zero static PASS values
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import copy
import hashlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.services.mtf_structure_liquidity_service import (
    MTF_ALGORITHM_VERSION,
    MtfDataQualityState,
    MtfStructureLiquidityConfig,
    build_mtf_bar_snapshot,
    build_mtf_context,
    classify_swing_sequence,
    derive_reference_liquidity_levels,
    detect_confirmed_swings,
    detect_liquidity_sweep,
    detect_structure_break,
    ensure_utc_datetime,
    evaluate_mtf_eligibility_for_direction,
    get_closed_bars_asof,
    normalize_market_bars,
    run_v17_9_1_historical_ab_backtest,
    update_liquidity_state,
    update_structure_state,
    validate_causal_window,
)
from backend.services.shared_strategy_evaluator import (
    EVALUATOR_VERSION,
    SharedStrategyEvaluator,
    build_market_decision_context,
)
from backend.services.v17_8_backtest_runner import (
    collect_or_load_verified_historical_dataset,
    compute_executable_source_tree_hash,
)
from tools.run_v17_9_async_regression import AsyncWorkerGroupSpec, BoundedAsyncTestOrchestrator
from tools.test_resource_guard import (
    TestResourceGuard,
    monitor_disk_pressure,
    probe_disk_budget,
    reserve_test_artifact_budget,
)
from tools.v17_9_execution_state import ExecutionStateManager


ALLOWED_BRANCH = "fix/v17-9-0-1-3-historical-parity-hardening"
DEFAULT_HEAD_SHA_BEFORE = "496325a5d0cfa8ad3ecab77747d560db7c630a8f"


@dataclass(frozen=True)
class GateResult:
    gate_id: str
    status: str  # "PASS" | "FAIL"
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
    status: str  # "PASS" | "FAIL"
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
    try:
        return _git_cmd(["rev-parse", f"origin/{branch}"], check=False) or None
    except Exception:
        return None


def _write_json(path: Path, payload: Dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, indent=2, sort_keys=False) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def parse_junit_xml_detailed(xml_path: Path) -> Dict[str, Any]:
    """
    Parse JUnit XML file without static zero assumptions: separates passed, failures,
    errors, skipped, xfail, and xpass testcases.
    """
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


def build_implementation_map() -> List[Dict[str, Any]]:
    return [
        {
            "file": "backend/services/mtf_structure_liquidity_service.py",
            "file_line": "backend/services/mtf_structure_liquidity_service.py:420-666",
            "exact_function": "normalize_market_bars(symbol, timeframe, raw_bars, source_metadata=None) -> NormalizedBars",
            "function_names": ["normalize_market_bars"],
            "old_or_new": "NEW",
            "why": "Section 3.A: Canonical Bitget XAUUSDT OHLCV normalization, strict UTC alignment (including 1D session anchor), tick rounding, duplicate/gap/corrupt rejection without synthetic forward-fill.",
            "caller": "get_closed_bars_asof, run_v17_9_1_historical_ab_backtest",
            "test": "test_01_normalize_market_bars_valid_and_negative_controls, test_16_negative_controls_stale_htf_missing_gaps_and_conflicting_duplicates",
        },
        {
            "file": "backend/services/mtf_structure_liquidity_service.py",
            "file_line": "backend/services/mtf_structure_liquidity_service.py:669-759",
            "exact_function": "get_closed_bars_asof(snapshot, timeframe, decision_time, source_metadata=None) -> ClosedBarWindow",
            "function_names": ["get_closed_bars_asof"],
            "old_or_new": "NEW",
            "why": "Section 3.B: Enforces strict closed-candle cutoff (close_time <= decision_time and available_at <= decision_time), excluding partial/future bars.",
            "caller": "build_mtf_bar_snapshot, detect_confirmed_swings, detect_liquidity_sweep",
            "test": "test_02_get_closed_bars_asof_excludes_future_and_partial_candles, test_17_exact_close_time_boundary_and_expired_pool_non_recycling",
        },
        {
            "file": "backend/services/mtf_structure_liquidity_service.py",
            "file_line": "backend/services/mtf_structure_liquidity_service.py:762-1007",
            "exact_function": "build_mtf_bar_snapshot(decision_time, bar_store, settings=None) -> MtfSnapshot",
            "function_names": ["build_mtf_bar_snapshot"],
            "old_or_new": "NEW",
            "why": "Section 3.C: Synchronizes H4/H1/M15 (and M5/M1/D1) closed-candle windows from a single source-of-truth, supporting exact UTC bucket resampling without partial HTF leakage and propagating STALE_HTF to per-timeframe window state.",
            "caller": "build_mtf_context, validate_causal_window",
            "test": "test_03_build_mtf_bar_snapshot_resampling_and_no_partial_htf_leak, test_16_negative_controls_stale_htf_missing_gaps_and_conflicting_duplicates",
        },
        {
            "file": "backend/services/mtf_structure_liquidity_service.py",
            "file_line": "backend/services/mtf_structure_liquidity_service.py:1010-1096",
            "exact_function": "validate_causal_window(mtf_snapshot, decision_time) -> CausalValidation",
            "function_names": ["validate_causal_window"],
            "old_or_new": "NEW",
            "why": "Section 3.D: Hard causal validator failing closed on any bar with close_time > decision_time, available_at > decision_time, or unclosed flag.",
            "caller": "build_mtf_context",
            "test": "test_04_validate_causal_window_detects_future_leak_hard_failure",
        },
        {
            "file": "backend/services/mtf_structure_liquidity_service.py",
            "file_line": "backend/services/mtf_structure_liquidity_service.py:1323-1855",
            "exact_function": "detect_confirmed_swings, classify_swing_sequence, detect_structure_break",
            "function_names": ["detect_confirmed_swings", "classify_swing_sequence", "detect_structure_break"],
            "old_or_new": "NEW",
            "why": "Section 4.A-C: Causal pivot detector requiring full right-bar closure, HH/HL/LH/LL/EH/EL + protected swing classification, and closed-candle BOS/CHoCH vs wick-only rejection.",
            "caller": "build_mtf_context",
            "test": "test_05_detect_confirmed_swings_right_confirmation_delay_and_prefix_invariance, test_06_horizontal_tie_swings_and_insufficient_history, test_07_bos_vs_choch_and_wick_only_false_break_rejection",
        },
        {
            "file": "backend/services/mtf_structure_liquidity_service.py",
            "file_line": "backend/services/mtf_structure_liquidity_service.py:2375-3170",
            "exact_function": "cluster_equal_high_low, derive_reference_liquidity_levels, detect_liquidity_sweep, update_liquidity_state, summarize_liquidity_for_evaluator",
            "function_names": [
                "cluster_equal_high_low",
                "derive_reference_liquidity_levels",
                "detect_liquidity_sweep",
                "update_liquidity_state",
                "summarize_liquidity_for_evaluator",
            ],
            "old_or_new": "NEW",
            "why": "Section 5.A-E: Bounded-diameter EQH/EQL clustering, completed prior-day PDH/PDL derivation, closed-candle sweep vs touch-only vs breakout classification, and absorbing lifecycle state machine.",
            "caller": "build_mtf_context",
            "test": "test_08_cluster_equal_high_low_bounded_diameter_and_tolerance_boundary, test_09_pdh_pdl_completed_prior_day_only_vs_not_available, test_10_liquidity_sweep_detection_and_lifecycle_state_machine",
        },
        {
            "file": "backend/services/mtf_structure_liquidity_service.py",
            "file_line": "backend/services/mtf_structure_liquidity_service.py:3585-4920",
            "exact_function": "evaluate_mtf_eligibility_for_direction, build_variant_market_context, compare_ab_variant_decisions, compare_actual_consumed_decisions, run_v17_9_1_historical_ab_backtest",
            "function_names": [
                "evaluate_mtf_eligibility_for_direction",
                "build_variant_market_context",
                "compare_ab_variant_decisions",
                "compare_actual_consumed_decisions",
                "run_v17_9_1_historical_ab_backtest",
            ],
            "old_or_new": "MODIFIED",
            "why": "DEF-02, DEF-03, DEF-05: Enforces fail-closed MTF eligibility across all timeframes/directions, builds isolated A vs B contexts, verifies adapter call_id/origin lineage and mandatory non-null parity fields, and runs true A/B/C/D historical backtest with IndependentPreRefactorBaselineOracle.",
            "caller": "SharedStrategyEvaluator.evaluate_model_direction, run_v17_9_1_verification.py",
            "test": "test_12_baseline_flag_off_invariance_and_3_mode_consumed_parity, test_13_mtf_eligibility_flag_on_filtering_and_ablation_modes, test_15_three_runtime_adapters_production_shadow_backtest_identical_consumed_decisions, test_19_real_historical_ab_backtest_and_ablations, test_20_true_ab_variant_separation_distinct_contexts_and_action_invariance, test_21_cloned_dict_and_spoofed_lineage_fail_closed",
        },
        {
            "file": "backend/services/shared_strategy_evaluator.py",
            "file_line": "backend/services/shared_strategy_evaluator.py:450-1695",
            "exact_function": "build_market_decision_context, StrategyDecisionSnapshot, SharedStrategyEvaluator.evaluate_model_direction",
            "function_names": ["build_market_decision_context", "StrategyDecisionSnapshot", "SharedStrategyEvaluator"],
            "old_or_new": "MODIFIED",
            "why": "DEF-03: Replaces silent except-pass in MTF context construction with MTF_CONTEXT_BUILD_FAILED fail-closed state, enforces mtf_context!=None invariant when VALID, registers unique adapter_call_id per invocation, and propagates MTF eligibility rejection across all return paths.",
            "caller": "ScalpWatcherOrchestrator, MarketShadowService, v17_8_backtest_runner",
            "test": "test_22_mtf_context_build_exception_fail_closed_and_invariant_guard, test_23_early_return_mtf_eligibility_consistency_and_invalid_direction",
        },
        {
            "file": "tools/run_v17_9_1_verification.py",
            "file_line": "tools/run_v17_9_1_verification.py:1-950",
            "exact_function": "collect_gate_evidence, evaluate_gate, validate_final_verification_report, run_causal_audit_and_failure_sensitivity_probe, collect_pytest_inventory",
            "function_names": [
                "collect_gate_evidence",
                "evaluate_gate",
                "validate_final_verification_report",
                "run_causal_audit_and_failure_sensitivity_probe",
                "collect_pytest_inventory",
            ],
            "old_or_new": "MODIFIED",
            "why": "DEF-01 & DEF-04: Replaces static PASS/0/VERIFIED values with evidence-driven gate evaluators, isolated subprocess failure-sensitivity RED/GREEN probe, full pytest collection inventory reconciliation, and git ls-remote verification.",
            "caller": "main, test_24_gate_evaluator_fail_closed_and_report_validation, test_25_pytest_inventory_reconciliation_and_mandatory_non_null_parity",
            "test": "test_24_gate_evaluator_fail_closed_and_report_validation, test_25_pytest_inventory_reconciliation_and_mandatory_non_null_parity",
        },
        {
            "file": "backend/services/v17_9_1_decision_funnel.py",
            "file_line": "backend/services/v17_9_1_decision_funnel.py:103-1924",
            "exact_function": "normalize_reason_code, classify_rejection_stage, classify_historical_dataset_gaps, extract_decision_numerical_operands, evaluate_decision_counterfactuals_research_only, scan_full_m1_candidate_universe, build_decision_funnel_report, build_per_decision_trace_record, build_function_truth_map",
            "function_names": [
                "normalize_reason_code",
                "classify_rejection_stage",
                "classify_historical_dataset_gaps",
                "extract_decision_numerical_operands",
                "evaluate_decision_counterfactuals_research_only",
                "scan_full_m1_candidate_universe",
                "build_decision_funnel_report",
                "build_per_decision_trace_record",
                "build_function_truth_map",
            ],
            "old_or_new": "NEW",
            "why": "V17.9.1.1 Sections 02-05: AST function truth map, M1/M5/M15/H1/H4/D1 gap classification without synthetic fill, exact NET_RR_TOO_LOW numerical decomposition, 2-stage full 22,346-bar M1 candidate scanner, Decision Funnel reconciliation, and versioned per-decision trace builder.",
            "caller": "run_v17_9_1_positive_path_replay.py, test_v17_9_1_positive_path_replay.py",
            "test": "test_01_function_truth_map_ast_completeness, test_02_dataset_sha256_and_gap_classification_no_synthetic_fill, test_03_net_rr_algebraic_decomposition_and_no_gate_loosening, test_04_two_stage_candidate_scan_and_121_checkpoint_funnel_reconciliation",
        },
        {
            "file": "tools/run_v17_9_1_positive_path_replay.py",
            "file_line": "tools/run_v17_9_1_positive_path_replay.py:850-1650",
            "exact_function": "evaluate_positive_path_gates, run_positive_path_replay_and_bundle",
            "function_names": [
                "evaluate_positive_path_gates",
                "run_positive_path_replay_and_bundle",
            ],
            "old_or_new": "NEW",
            "why": "V17.9.1.1 Sections 05-10: Bounded multi-worker chronological positive-path historical replay, 3-runtime + oracle parity on 106 real ELIGIBLE decisions, OOS split, P01..P12 gate evaluator, and 20-artifact bundle publisher.",
            "caller": "main, test_v17_9_1_positive_path_replay.py",
            "test": "test_05_real_historical_positive_path_eligible_decisions_and_3way_oracle_parity, test_06_prefix_invariance_and_future_candle_mutation_isolation, test_07_negative_controls_tampered_parity_and_execution_guards, test_08_multi_worker_vs_single_worker_deterministic_equivalence",
        },
    ]


def verify_implementation_map_entries(
    impl_map: Sequence[Mapping[str, Any]],
    repo_root: Path = REPO_ROOT,
) -> Dict[str, Any]:
    """
    Section 02 & 03 (G01): Inspects AST of every file in `impl_map`, verifying that the file
    exists, every listed function/class exists in the AST, and every referenced test function
    exists in `backend/tests/test_v17_9_1_mtf_structure_liquidity.py` or `backend/tests/test_v17_9_1_positive_path_replay.py`.
    """
    if not impl_map:
        return {"is_valid": False, "status": "FAIL", "errors": ["implementation_map is empty"]}

    errors: List[str] = []
    verified_symbols: List[str] = []

    test_files = [
        repo_root / "backend" / "tests" / "test_v17_9_1_mtf_structure_liquidity.py",
        repo_root / "backend" / "tests" / "test_v17_9_1_positive_path_replay.py",
    ]
    test_defs: set[str] = set()
    for tf_p in test_files:
        if tf_p.exists():
            test_tree = ast.parse(tf_p.read_text(encoding="utf-8"), filename=str(tf_p))
            test_defs |= {
                node.name
                for node in ast.walk(test_tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
        else:
            errors.append(f"Missing test file: {tf_p}")

    for idx, item in enumerate(impl_map):
        rel_file = str(item.get("file") or str(item.get("file_line", "")).split(":")[0]).strip()
        if not rel_file:
            errors.append(f"Item {idx} missing file path")
            continue
        f_path = repo_root / rel_file
        if not f_path.exists():
            errors.append(f"Item {idx} file does not exist: {rel_file}")
            continue

        tree = ast.parse(f_path.read_text(encoding="utf-8"), filename=str(f_path))
        defined_names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }

        fn_names = list(item.get("function_names") or [])
        if not fn_names:
            raw_fn = str(item.get("exact_function", "")).split("(")[0]
            fn_names = [p.strip().split(".")[-1] for p in raw_fn.replace("&", ",").split(",") if p.strip()]

        for fn in fn_names:
            clean_fn = fn.split(".")[-1].strip()
            if clean_fn not in defined_names:
                errors.append(f"Symbol {clean_fn!r} not found in AST of {rel_file}")
            else:
                verified_symbols.append(f"{rel_file}::{clean_fn}")

        test_refs = [t.strip() for t in str(item.get("test", "")).split(",") if t.strip()]
        if not test_refs:
            errors.append(f"Item {idx} ({rel_file}) has no linked tests")
        for t_name in test_refs:
            if t_name.startswith("test_v17_") and not t_name.startswith("test_v17_9_1"):
                continue
            if t_name.startswith("test_") and t_name not in test_defs:
                errors.append(f"Referenced test {t_name!r} not found in {[p.name for p in test_files]}")

    return {
        "is_valid": len(errors) == 0 and len(verified_symbols) > 0,
        "status": "PASS" if (len(errors) == 0 and len(verified_symbols) > 0) else "FAIL",
        "verified_symbols_count": len(verified_symbols),
        "verified_symbols": verified_symbols,
        "errors": errors,
    }


def collect_pytest_inventory(repo_root: Path = REPO_ROOT) -> Dict[str, Any]:
    """
    Section 06 (DEF-04): Run `pytest backend/tests --collect-only -q` to discover the
    complete backend test inventory dynamically.
    """
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "backend") + os.pathsep + str(repo_root)
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


def build_full_regression_shards(
    repo_root: Path = REPO_ROOT,
    num_general_shards: int = 10,
) -> List[Tuple[str, str, List[str], Optional[str]]]:
    """
    Section 06 (DEF-04): Partition 100% of `backend/tests/test_*.py` into non-overlapping
    parallel shards:
      - `SHARD_01_V17_9_1_MTF_SUITE`: dedicated to `backend/tests/test_v17_9_1_mtf_structure_liquidity.py`
      - `SHARD_02`..`SHARD_11`: weighted load-balanced shards covering all remaining `backend/tests/test_*.py`
    Zero files and zero tests are excluded.
    """
    mtf_suite_files = [
        "backend/tests/test_v17_9_1_mtf_structure_liquidity.py",
        "backend/tests/test_v17_9_1_positive_path_replay.py",
    ]
    mtf_suite_set = set(mtf_suite_files)
    all_files = sorted(
        str(p.relative_to(repo_root)).replace("\\", "/")
        for p in (repo_root / "backend" / "tests").glob("test_*.py")
        if str(p.relative_to(repo_root)).replace("\\", "/") not in mtf_suite_set
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
    for tf in sorted(all_files, key=_estimate_file_weight, reverse=True):
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

    shards: List[Tuple[str, str, List[str], Optional[str]]] = [
        (
            "SHARD_01_V17_9_1_MTF_SUITE",
            "V17.9.1 Causal MTF Structure, Liquidity, Fail-Closed, A/B Backtest & Positive-Path Replay Suite",
            [f for f in mtf_suite_files if (repo_root / f).exists()],
            None,
        )
    ]
    for idx, bucket in enumerate(buckets, start=2):
        if not bucket:
            continue
        gid = f"SHARD_{idx:02d}_BACKEND_REGRESSION"
        desc = f"Full Backend Regression Balanced Shard {idx:02d} ({len(bucket)} test files)"
        shards.append((gid, desc, bucket, None))
    return shards


def run_causal_audit_and_failure_sensitivity_probe(
    *,
    run_id: str,
    head_sha: str,
    junit_dir: Path,
    logs_dir: Path,
    temp_root: Path,
) -> Dict[str, Any]:
    """
    Section 03 (DEF-01 / G07):
      1. Actively audits historical XAUUSDT checkpoints for future candle leaks, partial HTF leaks,
         unconfirmed swing leaks, same-bar wick false sweeps, and PDH/PDL current-day contamination.
      2. Executes 6 active negative-control probes and verifies all 6 are blocked.
      3. Executes an isolated subprocess failure-sensitivity RED/GREEN probe:
         - RED run: injects a broken causal assertion in an isolated probe test file, runs pytest,
           and verifies nonzero exit code + JUnit failure >= 1.
         - GREEN run: restores the causal assertion, re-runs pytest, and verifies exit code 0 + 0 failures.
    """
    datasets = collect_or_load_verified_historical_dataset(symbol="XAUUSDT", allow_network_refresh=False)
    bars_15m = datasets.get("15m", [])
    bars_1h = datasets.get("1H", [])
    bars_4h = datasets.get("4H", [])
    bars_1d = datasets.get("1D", [])

    future_candle_leaks = 0
    partial_htf_candle_leaks = 0
    unconfirmed_swing_leaks = 0
    same_bar_wick_false_sweeps = 0
    pdh_pdl_current_day_contamination = 0
    audited_checkpoints = 0

    if len(bars_15m) >= 64:
        step = max(1, (len(bars_15m) - 40) // 16)
        for idx in range(32, len(bars_15m), step):
            audited_checkpoints += 1
            asof_dt = ensure_utc_datetime(bars_15m[idx]["close_time"])
            ctx = build_mtf_context(
                asof_dt,
                {
                    "symbol": "XAUUSDT",
                    "M15": bars_15m[: idx + 1],
                    "H1": bars_1h,
                    "H4": bars_4h,
                    "D1": bars_1d,
                },
            )
            if not ctx.causal_validation.is_valid:
                future_candle_leaks += 1
            for tf_name, win in ctx.snapshot.windows.items():
                for b in win.bars:
                    if b.close_time > asof_dt or b.available_at > asof_dt or not b.is_closed:
                        if tf_name in ("H1", "H4", "D1"):
                            partial_htf_candle_leaks += 1
                        else:
                            future_candle_leaks += 1

            for tf_ctx in (ctx.mtf_structure.h4, ctx.mtf_structure.h1, ctx.mtf_structure.m15):
                win_bars = ctx.snapshot.windows[tf_ctx.timeframe].bars
                max_idx = len(win_bars) - 1
                for sw in tf_ctx.state.swings:
                    if sw.confirmed_at > asof_dt or sw.available_at > asof_dt or sw.confirmed_index > max_idx:
                        unconfirmed_swing_leaks += 1

            for swp in ctx.liquidity_context.sweeps:
                if swp.confirmed_at > asof_dt or swp.swept_ticks < ctx.config.sweep_min_penetration_ticks:
                    same_bar_wick_false_sweeps += 1
                if swp.side == "BUY_SIDE_SWEEP" and swp.reclaim_close_price > swp.swept_level_price:
                    same_bar_wick_false_sweeps += 1
                if swp.side == "SELL_SIDE_SWEEP" and swp.reclaim_close_price < swp.swept_level_price:
                    same_bar_wick_false_sweeps += 1

            if ctx.liquidity_context.pdh_pdl_status == "AVAILABLE":
                day_start = datetime(asof_dt.year, asof_dt.month, asof_dt.day, 0, 0, tzinfo=timezone.utc)
                prior_start = day_start - timedelta(days=1)
                prior_m15 = [
                    b
                    for b in ctx.snapshot.windows["M15"].bars
                    if prior_start <= b.open_time and b.close_time <= day_start
                ]
                if prior_m15 and not ctx.snapshot.windows["D1"].bars:
                    exp_pdh = max(b.high for b in prior_m15)
                    exp_pdl = min(b.low for b in prior_m15)
                    if ctx.liquidity_context.pdh_price != exp_pdh or ctx.liquidity_context.pdl_price != exp_pdl:
                        pdh_pdl_current_day_contamination += 1

    # Execute 6 explicit negative-control probes
    negative_controls_blocked = 0
    negative_controls_total = 6

    # NC1: Future bar injection blocked by validate_causal_window
    if bars_15m:
        asof_nc = ensure_utc_datetime(bars_15m[20]["close_time"])
        snap_nc = build_mtf_bar_snapshot(asof_nc, {"symbol": "XAUUSDT", "M15": bars_15m[:21]})
        leaked_dt = asof_nc - timedelta(minutes=15)
        cv_nc = validate_causal_window(snap_nc, leaked_dt)
        if not cv_nc.is_valid and any("FUTURE_LEAK" in r for r in cv_nc.reason_codes):
            negative_controls_blocked += 1

    # NC2: Partial HTF bucket exclusion
    if len(bars_15m) >= 10:
        asof_partial = ensure_utc_datetime(bars_15m[0]["open_time"]) + timedelta(minutes=45)
        snap_p = build_mtf_bar_snapshot(asof_partial, {"symbol": "XAUUSDT", "M15": bars_15m[:3]})
        if all(b.close_time <= asof_partial for b in snap_p.windows["H1"].bars):
            negative_controls_blocked += 1

    # NC3: Unconfirmed right-bar swing delay
    if len(bars_15m) >= 10:
        sw_nc = detect_confirmed_swings(bars_15m[:10], left=2, right=2, tolerance_ticks=3)
        if all(s.confirmed_index <= 9 and s.pivot_index <= 7 for s in sw_nc.events):
            negative_controls_blocked += 1

    # NC4: Invalid direction rejected by evaluate_mtf_eligibility_for_direction
    ok_dir, rej_dir, _ = evaluate_mtf_eligibility_for_direction("INVALID_DIR", None)
    if not ok_dir and rej_dir == "MTF_INVALID_DIRECTION":
        negative_controls_blocked += 1

    # NC5: Impossible state (mtf_context=None, mtf_quality_state='VALID') raises ValueError
    try:
        evaluate_mtf_eligibility_for_direction("LONG", None, mtf_quality_state=MtfDataQualityState.VALID)
    except ValueError:
        negative_controls_blocked += 1

    # NC6: MTF_CONTEXT_BUILD_FAILED rejected when eligibility enabled
    ok_bf, rej_bf, _ = evaluate_mtf_eligibility_for_direction(
        "LONG", None, mtf_quality_state=MtfDataQualityState.MTF_CONTEXT_BUILD_FAILED
    )
    if not ok_bf and rej_bf == "MTF_CONTEXT_BUILD_FAILED":
        negative_controls_blocked += 1

    # Isolated subprocess Failure-Sensitivity Probe (RED -> GREEN)
    probe_dir = temp_root / "g07_failure_sensitivity_probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_test_file = probe_dir / "test_g07_failure_sensitivity_probe.py"
    red_xml = junit_dir / "g07_failure_sensitivity_red.xml"
    green_xml = junit_dir / "g07_failure_sensitivity_green.xml"
    red_log = logs_dir / "g07_failure_sensitivity_red.log"
    green_log = logs_dir / "g07_failure_sensitivity_green.log"

    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "backend") + os.pathsep + str(REPO_ROOT)

    red_code = """from datetime import datetime, timedelta, timezone
from backend.services.mtf_structure_liquidity_service import build_mtf_bar_snapshot, validate_causal_window

def test_g07_intentional_failure_sensitivity_probe():
    base_dt = datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc)
    bars = [
        {
            "open_time": base_dt + timedelta(minutes=15 * i),
            "close_time": base_dt + timedelta(minutes=15 * (i + 1)),
            "open": 2650.0,
            "high": 2651.0,
            "low": 2649.0,
            "close": 2650.5,
            "volume": 10.0,
            "is_closed": True,
        }
        for i in range(4)
    ]
    snap = build_mtf_bar_snapshot(base_dt + timedelta(minutes=60), {"symbol": "XAUUSDT", "M15": bars})
    res = validate_causal_window(snap, base_dt + timedelta(minutes=30))
    assert res.is_valid, "INTENTIONAL_FAILURE_SENSITIVITY_PROBE_FOR_GATE_G07"
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
        'assert res.is_valid, "INTENTIONAL_FAILURE_SENSITIVITY_PROBE_FOR_GATE_G07"',
        'assert not res.is_valid and any("FUTURE_LEAK" in r for r in res.reason_codes)',
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
        and red_junit["failures"] >= 1
        and green_proc.returncode == 0
        and green_junit["failures"] == 0
        and green_junit["passed"] == 1
    )

    all_clean = (
        audited_checkpoints > 0
        and future_candle_leaks == 0
        and partial_htf_candle_leaks == 0
        and unconfirmed_swing_leaks == 0
        and same_bar_wick_false_sweeps == 0
        and pdh_pdl_current_day_contamination == 0
        and negative_controls_blocked == negative_controls_total
        and probe_verified
    )

    return {
        "commit_sha": head_sha,
        "run_id": run_id,
        "status": "PASS" if all_clean else "FAIL",
        "audited_checkpoints": audited_checkpoints,
        "future_candle_leaks": future_candle_leaks,
        "partial_htf_candle_leaks": partial_htf_candle_leaks,
        "unconfirmed_swing_leaks": unconfirmed_swing_leaks,
        "same_bar_wick_false_sweeps": same_bar_wick_false_sweeps,
        "pdh_pdl_current_day_contamination": pdh_pdl_current_day_contamination,
        "negative_controls_total": negative_controls_total,
        "negative_controls_blocked": negative_controls_blocked,
        "failure_sensitivity_probe": {
            "gate": "G07",
            "test_name": "test_g07_intentional_failure_sensitivity_probe",
            "injected_mutation": "assert res.is_valid, 'INTENTIONAL_FAILURE_SENSITIVITY_PROBE_FOR_GATE_G07'",
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


def collect_gate_evidence(
    *,
    artifact_dir: Path,
    head_sha: str,
    branch: str,
    remote_head_sha: Optional[str],
    dirty_tracked_files: Sequence[str],
    impl_map: Sequence[Mapping[str, Any]],
    pytest_collection: Mapping[str, Any],
    async_summary: Mapping[str, Any],
    detailed_junit_by_shard: Mapping[str, Mapping[str, Any]],
    causal_audit: Mapping[str, Any],
    backtest_res: Mapping[str, Any],
    disk_report: Mapping[str, Any],
    artifact_hashes: Mapping[str, str],
) -> Dict[str, Any]:
    """
    Section 03 (DEF-01): Collects raw, unforgeable runtime evidence for gates G01..G13.
    """
    impl_verification = verify_implementation_map_entries(impl_map, repo_root=REPO_ROOT)
    return {
        "artifact_dir": str(artifact_dir),
        "head_sha": head_sha,
        "branch": branch,
        "remote_head_sha": remote_head_sha,
        "dirty_tracked_files": list(dirty_tracked_files),
        "impl_verification": impl_verification,
        "pytest_collection": dict(pytest_collection),
        "async_summary": dict(async_summary),
        "detailed_junit_by_shard": {k: dict(v) for k, v in detailed_junit_by_shard.items()},
        "causal_audit": dict(causal_audit),
        "backtest_res": dict(backtest_res),
        "disk_report": dict(disk_report),
        "artifact_hashes": dict(artifact_hashes),
    }


def evaluate_gate(gate_id: str, evidence: Mapping[str, Any]) -> GateResult:
    """
    Section 03 (DEF-01): Evidence-driven gate evaluator for G01..G13.
    Never returns a static PASS; fails closed if any required evidence is missing or invalid.
    """
    artifact_dir = Path(str(evidence.get("artifact_dir", "")))
    reasons: List[str] = []
    metrics: Dict[str, Any] = {}

    if gate_id == "G01":
        ev_path = str(artifact_dir / "implementation_map.json")
        impl_v = evidence.get("impl_verification") or {}
        if not impl_v.get("is_valid"):
            reasons.extend(impl_v.get("errors") or ["implementation_map AST/signature verification failed"])
        branch = str(evidence.get("branch") or "")
        if branch != ALLOWED_BRANCH:
            reasons.append(f"Branch mismatch: {branch!r} != {ALLOWED_BRANCH!r}")
        head_sha = str(evidence.get("head_sha") or "")
        if len(head_sha) < 12:
            reasons.append("Invalid or missing head_sha")
        metrics["verified_symbols_count"] = int(impl_v.get("verified_symbols_count", 0))
        passed = len(reasons) == 0 and metrics["verified_symbols_count"] > 0
        return GateResult("G01", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id in ("G02", "G03", "G04"):
        ev_path = str(artifact_dir / "junit" / "shard_01_v17_9_1_mtf_suite.xml")
        s1 = (evidence.get("detailed_junit_by_shard") or {}).get("SHARD_01_V17_9_1_MTF_SUITE") or {}
        if not s1.get("valid"):
            reasons.append("SHARD_01_V17_9_1_MTF_SUITE JUnit XML missing or invalid")
        if int(s1.get("failures", 0)) > 0 or int(s1.get("errors", 0)) > 0 or int(s1.get("skipped", 0)) > 0:
            reasons.append(
                f"SHARD_01 had failures={s1.get('failures')}, errors={s1.get('errors')}, skipped={s1.get('skipped')}"
            )
        tc_names = set(s1.get("testcase_names") or [])
        required_by_gate = {
            "G02": {
                "test_01_normalize_market_bars_valid_and_negative_controls",
                "test_02_get_closed_bars_asof_excludes_future_and_partial_candles",
                "test_03_build_mtf_bar_snapshot_resampling_and_no_partial_htf_leak",
                "test_16_negative_controls_stale_htf_missing_gaps_and_conflicting_duplicates",
                "test_17_exact_close_time_boundary_and_expired_pool_non_recycling",
            },
            "G03": {
                "test_05_detect_confirmed_swings_right_confirmation_delay_and_prefix_invariance",
                "test_06_horizontal_tie_swings_and_insufficient_history",
                "test_07_bos_vs_choch_and_wick_only_false_break_rejection",
            },
            "G04": {
                "test_08_cluster_equal_high_low_bounded_diameter_and_tolerance_boundary",
                "test_09_pdh_pdl_completed_prior_day_only_vs_not_available",
                "test_10_liquidity_sweep_detection_and_lifecycle_state_machine",
            },
        }[gate_id]
        missing_tcs = sorted(required_by_gate - tc_names)
        if missing_tcs:
            reasons.append(f"Missing required testcase executions: {missing_tcs}")
        metrics["executed_required_tests"] = len(required_by_gate - set(missing_tcs))
        passed = len(reasons) == 0
        return GateResult(gate_id, "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G05":
        ev_path = str(artifact_dir / "three_way_parity_report.json")
        bt = evidence.get("backtest_res") or {}
        par = bt.get("parity") or {}
        total_chk = int(par.get("parity_checks_total", 0))
        passed_chk = int(par.get("parity_checks_passed", 0))
        mismatches = int(par.get("parity_mismatch_count", -1))
        ora_mismatches = int(par.get("oracle_vs_a_mismatches", -1))
        if total_chk <= 0 or passed_chk != total_chk or mismatches != 0 or ora_mismatches != 0:
            reasons.append(
                f"3-way parity failed: total={total_chk}, passed={passed_chk}, mismatches={mismatches}, oracle_mismatches={ora_mismatches}"
            )
        if bt.get("THREE_RUNTIME_CONSUMED_PARITY") != "PASS":
            reasons.append(f"THREE_RUNTIME_CONSUMED_PARITY={bt.get('THREE_RUNTIME_CONSUMED_PARITY')!r}")
        metrics["parity_checks_total"] = total_chk
        metrics["parity_checks_passed"] = passed_chk
        passed = len(reasons) == 0
        return GateResult("G05", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G06":
        ev_path = str(artifact_dir / "mtf_backtest_ab_metrics.json")
        bt = evidence.get("backtest_res") or {}
        par = bt.get("parity") or {}
        variants = bt.get("variants") or {}
        ab_checks = int(par.get("a_vs_b_decision_invariance_checks", 0))
        ab_mismatches = int(par.get("a_vs_b_decision_invariance_mismatches", -1))
        dist_ctx = int(par.get("a_vs_b_distinct_context_checks", 0))
        dist_pol = int(par.get("a_vs_b_distinct_policy_fp_checks", 0))
        dist_meta = int(par.get("a_vs_b_distinct_metadata_fp_checks", 0))
        if ab_checks <= 0 or ab_mismatches != 0:
            reasons.append(f"A vs B invariance failed: checks={ab_checks}, mismatches={ab_mismatches}")
        if dist_ctx != ab_checks or dist_pol != ab_checks or dist_meta != ab_checks:
            reasons.append(
                f"A vs B separation failed: ab_checks={ab_checks}, distinct_ctx={dist_ctx}, distinct_pol={dist_pol}, distinct_meta={dist_meta}"
            )
        va = variants.get("A_BASELINE_OFF") or {}
        vb = variants.get("B_OBSERVE_ONLY_DEFAULT") or {}
        if not va or not vb:
            reasons.append("Missing A_BASELINE_OFF or B_OBSERVE_ONLY_DEFAULT in variants")
        else:
            if va.get("trade_actions_set_hash") != vb.get("trade_actions_set_hash"):
                reasons.append("A and B trade_actions_set_hash diverged")
            if va.get("policy_set_hash") == vb.get("policy_set_hash"):
                reasons.append("A and B policy_set_hash are identical (context not separated)")
            if va.get("metadata_set_hash") == vb.get("metadata_set_hash"):
                reasons.append("A and B metadata_set_hash are identical (MTF metadata not separated)")
        metrics["a_vs_b_checks"] = ab_checks
        passed = len(reasons) == 0
        return GateResult("G06", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G07":
        ev_path = str(artifact_dir / "causal_audit_report.json")
        ca = evidence.get("causal_audit") or {}
        if ca.get("status") != "PASS":
            reasons.append(f"causal_audit.status={ca.get('status')!r}")
        if int(ca.get("audited_checkpoints", 0)) <= 0:
            reasons.append("audited_checkpoints <= 0")
        for leak_field in (
            "future_candle_leaks",
            "partial_htf_candle_leaks",
            "unconfirmed_swing_leaks",
            "same_bar_wick_false_sweeps",
            "pdh_pdl_current_day_contamination",
        ):
            val = ca.get(leak_field)
            if val is None or int(val) != 0:
                reasons.append(f"{leak_field}={val!r} (must be 0)")
        if int(ca.get("negative_controls_blocked", 0)) < int(ca.get("negative_controls_total", 1)):
            reasons.append(
                f"negative_controls_blocked={ca.get('negative_controls_blocked')} < {ca.get('negative_controls_total')}"
            )
        probe = ca.get("failure_sensitivity_probe") or {}
        if probe.get("status") != "VERIFIED":
            reasons.append(f"failure_sensitivity_probe.status={probe.get('status')!r}")
        if int(probe.get("red_exit_code", 0)) == 0 or int(probe.get("red_failures", 0)) < 1:
            reasons.append("failure_sensitivity_probe RED run did not fail as required")
        if int(probe.get("reverted_green_exit_code", -1)) != 0 or int(probe.get("green_failures", -1)) != 0:
            reasons.append("failure_sensitivity_probe GREEN run did not pass cleanly")
        metrics["audited_checkpoints"] = int(ca.get("audited_checkpoints", 0))
        metrics["negative_controls_blocked"] = int(ca.get("negative_controls_blocked", 0))
        passed = len(reasons) == 0
        return GateResult("G07", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G08":
        ev_path = str(artifact_dir / "pytest_collection.json")
        col = evidence.get("pytest_collection") or {}
        shards = evidence.get("detailed_junit_by_shard") or {}
        discovered_total = int(col.get("discovered_total", 0))
        excluded_total = int(col.get("excluded_total", 0))
        executed_total = sum(int(v.get("tests", 0)) for v in shards.values())
        passed_total = sum(int(v.get("passed", 0)) for v in shards.values())
        failed_total = sum(int(v.get("failures", 0)) for v in shards.values())
        errors_total = sum(int(v.get("errors", 0)) for v in shards.values())
        skipped_total = sum(int(v.get("skipped", 0)) for v in shards.values())
        xfail_total = sum(int(v.get("xfail", 0)) for v in shards.values())
        xpass_total = sum(int(v.get("xpass", 0)) for v in shards.values())

        if discovered_total <= 0:
            reasons.append("discovered_total <= 0")
        if discovered_total != executed_total + excluded_total:
            reasons.append(
                f"Inventory mismatch: discovered_total ({discovered_total}) != executed_total ({executed_total}) + excluded_total ({excluded_total})"
            )
        if excluded_total != 0:
            reasons.append(f"excluded_total ({excluded_total}) != 0 in full regression")
        if failed_total > 0 or errors_total > 0 or skipped_total > 0 or xfail_total > 0 or xpass_total > 0:
            reasons.append(
                f"Non-clean test counts: failed={failed_total}, errors={errors_total}, skipped={skipped_total}, xfail={xfail_total}, xpass={xpass_total}"
            )
        metrics.update(
            {
                "discovered_total": discovered_total,
                "executed_total": executed_total,
                "excluded_total": excluded_total,
                "passed_total": passed_total,
                "failed_total": failed_total,
                "errors_total": errors_total,
                "skipped_total": skipped_total,
                "xfail_total": xfail_total,
                "xpass_total": xpass_total,
            }
        )
        passed = len(reasons) == 0
        return GateResult("G08", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G09":
        ev_path = str(artifact_dir / "mtf_backtest_ab_metrics.json")
        bt = evidence.get("backtest_res") or {}
        if bt.get("status") != "PASS":
            reasons.append(f"backtest.status={bt.get('status')!r}")
        if not bt.get("DATA_VALIDATED") or not bt.get("ENGINEERING_REPLAY_COMPLETED") or not bt.get("PARITY_VERIFIED"):
            reasons.append("Backtest missing DATA_VALIDATED / ENGINEERING_REPLAY_COMPLETED / PARITY_VERIFIED")
        ds = bt.get("dataset") or {}
        if ds.get("quote_construction_mode") != "MODELED_SPREAD_QUOTE":
            reasons.append(f"quote_construction_mode={ds.get('quote_construction_mode')!r}")
        if not bt.get("STRATEGY_STATISTICAL_CONCLUSION"):
            reasons.append("Missing STRATEGY_STATISTICAL_CONCLUSION")
        variants = bt.get("variants") or {}
        for req_v in (
            "A_BASELINE_OFF",
            "B_OBSERVE_ONLY_DEFAULT",
            "C_MTF_ELIGIBILITY_ON_FULL",
            "D1_ABLATION_H4_ONLY",
            "D2_ABLATION_H1_ONLY",
            "D3_ABLATION_M15_ONLY",
            "D4_ABLATION_STRUCTURE_ONLY",
            "D5_ABLATION_LIQUIDITY_ONLY",
            "D6_ABLATION_M15_H1_ONLY",
        ):
            if req_v not in variants:
                reasons.append(f"Missing backtest variant {req_v}")
        metrics["evaluation_checkpoints"] = int(ds.get("evaluation_checkpoints", 0))
        metrics["strategy_statistical_conclusion"] = bt.get("STRATEGY_STATISTICAL_CONCLUSION")
        passed = len(reasons) == 0
        return GateResult("G09", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G10":
        ev_path = str(artifact_dir / "shard_manifest.json")
        summary = evidence.get("async_summary") or {}
        if summary.get("status") != "PASS":
            reasons.append(f"async_summary.status={summary.get('status')!r}")
        sched = int(summary.get("scheduled_tasks", 0))
        succ = int(summary.get("succeeded_tasks", 0))
        if sched <= 0 or succ != sched:
            reasons.append(f"Shard completion mismatch: scheduled={sched}, succeeded={succ}")
        if int(summary.get("orphan_processes_remaining", -1)) != 0:
            reasons.append(f"orphan_processes_remaining={summary.get('orphan_processes_remaining')}")
        if not summary.get("concurrency_bound_holds", False):
            reasons.append("concurrency_bound_holds is False")
        metrics["shards_scheduled"] = sched
        metrics["shards_succeeded"] = succ
        passed = len(reasons) == 0
        return GateResult("G10", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G11":
        ev_path = str(artifact_dir / "disk_guard_report.json")
        dr = evidence.get("disk_report") or {}
        if dr.get("status") != "PASS":
            reasons.append(f"disk_report.status={dr.get('status')!r}")
        res_tel = dr.get("resource_telemetry") or {}
        if int(res_tel.get("samples_recorded", 0)) < 2:
            reasons.append(f"Insufficient live resource telemetry samples: {res_tel.get('samples_recorded')}")
        if float(res_tel.get("peak_rss_mb", 0.0) or 0.0) <= 0.0:
            reasons.append("peak_rss_mb <= 0.0 (live memory telemetry missing)")
        metrics["samples_recorded"] = int(res_tel.get("samples_recorded", 0))
        metrics["peak_rss_mb"] = float(res_tel.get("peak_rss_mb", 0.0) or 0.0)
        passed = len(reasons) == 0
        return GateResult("G11", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G12":
        ev_path = str(artifact_dir)
        hashes = evidence.get("artifact_hashes") or {}
        required_files = (
            "implementation_map.json",
            "pytest_collection.json",
            "shard_manifest.json",
            "mtf_backtest_ab_metrics.json",
            "three_way_parity_report.json",
            "causal_audit_report.json",
            "disk_guard_report.json",
        )
        for req_f in required_files:
            f_p = artifact_dir / req_f
            if not f_p.exists() or f_p.stat().st_size == 0:
                reasons.append(f"Missing or empty artifact file: {req_f}")
            elif req_f not in hashes:
                reasons.append(f"Missing SHA-256 hash entry for {req_f}")
        metrics["verified_artifacts_count"] = len(required_files) - len(reasons)
        passed = len(reasons) == 0
        return GateResult("G12", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    if gate_id == "G13":
        ev_path = str(artifact_dir / "v17_9_1_final_verification_report.json")
        prior_gates: Dict[str, GateResult] = evidence.get("prior_gates") or {}
        for req_gid in (f"G{i:02d}" for i in range(1, 13)):
            g_obj = prior_gates.get(req_gid)
            if g_obj is None:
                reasons.append(f"Gate {req_gid} not evaluated before G13")
            elif g_obj.status != "PASS" or not g_obj.passed:
                reasons.append(f"Gate {req_gid} status={g_obj.status}")
        dirty_tracked = list(evidence.get("dirty_tracked_files") or [])
        if dirty_tracked:
            reasons.append(f"Tracked source tree has uncommitted changes during verification: {dirty_tracked}")
        passed = len(reasons) == 0
        return GateResult("G13", "PASS" if passed else "FAIL", passed, ev_path, tuple(reasons), metrics)

    return GateResult(gate_id, "FAIL", False, str(artifact_dir), (f"Unknown gate_id={gate_id!r}",), {})


def validate_final_verification_report(report: Mapping[str, Any]) -> ReportValidationResult:
    """
    Section 03 (DEF-01): Validates the complete `v17_9_1_final_verification_report.json`
    invariants. Fails closed if any gate G01..G13 is not PASS, if test inventory does not
    reconcile, or if causal audit / parity checks have any failures.
    """
    reasons: List[str] = []
    gates = report.get("gates") or {}
    gate_statuses: Dict[str, str] = {}

    for i in range(1, 14):
        gid = f"G{i:02d}"
        g_entry = gates.get(gid)
        if not isinstance(g_entry, Mapping):
            reasons.append(f"Missing gate {gid}")
            gate_statuses[gid] = "MISSING"
        else:
            st = str(g_entry.get("status") or "FAIL")
            gate_statuses[gid] = st
            if st != "PASS":
                reasons.append(f"Gate {gid} is {st}")

    tests = report.get("tests") or {}
    disc = int(tests.get("discovered_total", 0))
    exec_tot = int(tests.get("executed_total", tests.get("total", 0)))
    excl_tot = int(tests.get("excluded_total", 0))
    if disc <= 0 or disc != exec_tot + excl_tot:
        reasons.append(f"Test inventory mismatch: discovered={disc}, executed={exec_tot}, excluded={excl_tot}")
    if int(tests.get("failed", 0)) > 0 or int(tests.get("errors", 0)) > 0:
        reasons.append(f"Test failures/errors present: failed={tests.get('failed')}, errors={tests.get('errors')}")

    parity = report.get("parity") or {}
    if parity.get("status") != "PASS" or int(parity.get("parity_mismatch_count", -1)) != 0:
        reasons.append("Parity status is not PASS or parity_mismatch_count != 0")

    git_info = report.get("git") or {}
    if git_info.get("branch") != ALLOWED_BRANCH:
        reasons.append(f"Invalid branch {git_info.get('branch')!r}")
    if git_info.get("dirty_tracked_files_after"):
        reasons.append(f"Dirty tracked files after run: {git_info.get('dirty_tracked_files_after')}")

    if report.get("final_verdict") != "PASS" and len(reasons) == 0:
        reasons.append(f"final_verdict={report.get('final_verdict')!r}")

    is_valid = len(reasons) == 0
    return ReportValidationResult(
        is_valid=is_valid,
        status="PASS" if is_valid else "FAIL",
        reasons=tuple(reasons),
        gate_statuses=gate_statuses,
    )


def _dirty_tracked_files() -> List[str]:
    lines = _git_cmd(["status", "--porcelain=v1"]).splitlines()
    tracked_dirty: List[str] = []
    for ln in lines:
        if not ln.strip():
            continue
        if ln.startswith("?? "):
            continue
        path_part = ln[3:].strip()
        if path_part.startswith("docs/artifacts/v17_9_1/") or path_part.startswith("docs/artifacts/v17_9_1_positive_path/"):
            continue
        tracked_dirty.append(ln.strip())
    return tracked_dirty


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="AURUM Desk V17.9.1 Verification Runner")
    parser.add_argument("--run-id", default=None, help="Optional explicit run_id")
    parser.add_argument("--head-sha-before", default=DEFAULT_HEAD_SHA_BEFORE, help="Initial HEAD SHA before fixes")
    parser.add_argument("--backtest-points", type=int, default=120, help="Max historical backtest checkpoints")
    args = parser.parse_args(argv)

    start_wall = time.time()
    branch = _git_cmd(["branch", "--show-current"])
    head_sha = _git_cmd(["rev-parse", "HEAD"])
    short_sha = head_sha[:12]
    utc_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = args.run_id or f"v17_9_1_{utc_stamp}_{short_sha}"

    artifact_dir = REPO_ROOT / "docs" / "artifacts" / "v17_9_1" / run_id
    junit_dir = artifact_dir / "junit"
    logs_dir = artifact_dir / "logs"
    temp_workers_dir = REPO_ROOT / ".tmp_v17_9_1_workers" / run_id
    junit_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    # 1. Disk Guard Preflight & Reservation
    preflight_budget = probe_disk_budget(workdir=REPO_ROOT, artifact_root=artifact_dir)
    stage_reservation = reserve_test_artifact_budget(
        "v17_9_1_full_verification",
        budget=preflight_budget,
        requested_bytes=150 * 1024 * 1024,
    )
    if not stage_reservation.permitted:
        print(f"DISK_GUARD_BLOCKED: {stage_reservation.reason}", file=sys.stderr)
        return 2

    guard = TestResourceGuard(
        run_id=run_id,
        repo_root=REPO_ROOT,
        artifact_dir=artifact_dir,
        tmp_root=temp_workers_dir,
    )
    guard.sample_cpu_memory(stage="VERIFICATION_START", active_workers=0)

    # 2. Discover Full Pytest Inventory (`pytest backend/tests --collect-only -q`)
    pytest_inventory = collect_pytest_inventory(REPO_ROOT)

    # 3. Build Full Backend Regression Shards (11 shards covering 100% of `backend/tests/test_*.py`)
    shard_definitions = build_full_regression_shards(REPO_ROOT, num_general_shards=10)

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
                stage="STAGE_B",
            )
        )

    async_summary = asyncio.run(orchestrator.run_groups(specs))
    _git_cmd(["checkout", "--", "backtest/trade_decision_lineage_reconciliation.json"], check=False)
    pressure_mid = monitor_disk_pressure(interval=0.5, workdir=REPO_ROOT, artifact_root=artifact_dir)
    guard.sample_cpu_memory(stage="POST_PYTEST_SHARDS", active_workers=0)

    # Parse detailed JUnit XML metrics per shard
    detailed_junit_by_shard: Dict[str, Dict[str, Any]] = {}
    for gid, _, files, _ in shard_definitions:
        xml_path = junit_dir / f"{gid.lower()}.xml"
        parsed = parse_junit_xml_detailed(xml_path)
        parsed["files_count"] = len(files)
        detailed_junit_by_shard[gid] = parsed

    executed_total = sum(int(v["tests"]) for v in detailed_junit_by_shard.values())
    passed_total = sum(int(v["passed"]) for v in detailed_junit_by_shard.values())
    failed_total = sum(int(v["failures"]) for v in detailed_junit_by_shard.values())
    errors_total = sum(int(v["errors"]) for v in detailed_junit_by_shard.values())
    skipped_total = sum(int(v["skipped"]) for v in detailed_junit_by_shard.values())
    xfail_total = sum(int(v["xfail"]) for v in detailed_junit_by_shard.values())
    xpass_total = sum(int(v["xpass"]) for v in detailed_junit_by_shard.values())

    pytest_collection_payload = {
        **pytest_inventory,
        "executed_total": executed_total,
        "excluded_total": 0,
        "excluded_items": [],
        "passed_total": passed_total,
        "failed_total": failed_total,
        "errors_total": errors_total,
        "skipped_total": skipped_total,
        "xfail_total": xfail_total,
        "xpass_total": xpass_total,
        "reconciled": int(pytest_inventory["discovered_total"]) == executed_total,
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
            }
            for gid, info in detailed_junit_by_shard.items()
        },
    }

    # 4. Execute Real Causal Audit & Subprocess Failure-Sensitivity Probe
    causal_audit_payload = run_causal_audit_and_failure_sensitivity_probe(
        run_id=run_id,
        head_sha=head_sha,
        junit_dir=junit_dir,
        logs_dir=logs_dir,
        temp_root=temp_workers_dir,
    )
    guard.sample_cpu_memory(stage="POST_CAUSAL_AUDIT", active_workers=0)

    # 5. Execute Real Historical Bitget XAUUSDT A/B/C/D Backtest & 3-Mode Parity
    backtest_res = run_v17_9_1_historical_ab_backtest(max_evaluation_points=args.backtest_points)
    _git_cmd(["checkout", "--", "backtest/trade_decision_lineage_reconciliation.json"], check=False)
    guard.sample_cpu_memory(stage="POST_HISTORICAL_BACKTEST", active_workers=0)

    # 6. Finalize Disk Guard & Prune Ephemeral Temp Directories
    cleanup_report = guard.finalize_and_prune_ephemeral_artifacts()
    guard_rep = guard.build_disk_budget_report()
    perf_report = orchestrator.build_performance_report(async_summary)
    postflight_budget = probe_disk_budget(workdir=REPO_ROOT, artifact_root=artifact_dir)
    exec_tree_info = compute_executable_source_tree_hash(REPO_ROOT)

    disk_report_payload = {
        "commit_sha": head_sha,
        "run_id": run_id,
        "preflight_budget": preflight_budget.to_dict(),
        "stage_reservation": stage_reservation.to_dict(),
        "mid_run_pressure": pressure_mid,
        "cleanup_report": cleanup_report.to_dict(),
        "postflight_budget": postflight_budget.to_dict(),
        "resource_telemetry": perf_report.get("resource_telemetry", {}),
        "cpu_memory_samples": guard_rep.get("cpu_memory_samples", []),
        "status": cleanup_report.status,
    }

    # 7. Write Primary Artifact Bundle & Record SHA-256 Hashes
    impl_map = build_implementation_map()
    artifact_hashes: Dict[str, str] = {}
    artifact_hashes["implementation_map.json"] = _write_json(
        artifact_dir / "implementation_map.json",
        {
            "commit_sha": head_sha,
            "head_sha_before": args.head_sha_before,
            "branch": branch,
            "items": impl_map,
        },
    )
    artifact_hashes["pytest_collection.json"] = _write_json(
        artifact_dir / "pytest_collection.json", pytest_collection_payload
    )
    artifact_hashes["shard_manifest.json"] = _write_json(
        artifact_dir / "shard_manifest.json", async_summary
    )
    artifact_hashes["mtf_backtest_ab_metrics.json"] = _write_json(
        artifact_dir / "mtf_backtest_ab_metrics.json", backtest_res
    )
    artifact_hashes["three_way_parity_report.json"] = _write_json(
        artifact_dir / "three_way_parity_report.json",
        {
            "commit_sha": head_sha,
            "executable_source_tree_hash": exec_tree_info["executable_source_tree_hash"],
            "evaluator_version": EVALUATOR_VERSION,
            "mtf_algorithm_version": MTF_ALGORITHM_VERSION,
            "THREE_RUNTIME_CONSUMED_PARITY": backtest_res.get("THREE_RUNTIME_CONSUMED_PARITY"),
            "BASELINE_ORACLE_PARITY": backtest_res.get("BASELINE_ORACLE_PARITY"),
            "OBSERVE_ONLY_ACTION_INVARIANCE": backtest_res.get("OBSERVE_ONLY_ACTION_INVARIANCE"),
            "parity": backtest_res.get("parity", {}),
        },
    )
    artifact_hashes["causal_audit_report.json"] = _write_json(
        artifact_dir / "causal_audit_report.json", causal_audit_payload
    )
    artifact_hashes["disk_guard_report.json"] = _write_json(
        artifact_dir / "disk_guard_report.json", disk_report_payload
    )

    # 8. Evaluate Gates G01..G12 from real collected evidence, then evaluate G13
    remote_head_sha = _query_remote_branch_head(branch)
    dirty_tracked = _dirty_tracked_files()
    gate_evidence = collect_gate_evidence(
        artifact_dir=artifact_dir,
        head_sha=head_sha,
        branch=branch,
        remote_head_sha=remote_head_sha,
        dirty_tracked_files=dirty_tracked,
        impl_map=impl_map,
        pytest_collection=pytest_collection_payload,
        async_summary=async_summary,
        detailed_junit_by_shard=detailed_junit_by_shard,
        causal_audit=causal_audit_payload,
        backtest_res=backtest_res,
        disk_report=disk_report_payload,
        artifact_hashes=artifact_hashes,
    )

    evaluated_gates: Dict[str, GateResult] = {}
    for i in range(1, 13):
        gid = f"G{i:02d}"
        evaluated_gates[gid] = evaluate_gate(gid, gate_evidence)
    gate_evidence["prior_gates"] = evaluated_gates
    evaluated_gates["G13"] = evaluate_gate("G13", gate_evidence)

    gates_dict = {gid: g_res.to_dict() for gid, g_res in evaluated_gates.items()}
    all_gates_pass = all(g_res.passed for g_res in evaluated_gates.values())

    final_report: Dict[str, Any] = {
        "version": "17.9.1",
        "symbol": "XAUUSDT",
        "venue": "BITGET",
        "run_id": run_id,
        "git": {
            "branch": branch,
            "head_sha_before": args.head_sha_before,
            "tested_commit_sha": head_sha,
            "pushed_commit_sha": remote_head_sha,
            "remote_head_sha": remote_head_sha,
            "executable_source_tree_hash": exec_tree_info["executable_source_tree_hash"],
            "core_backtest_evaluator_tree_hash": exec_tree_info["core_backtest_evaluator_tree_hash"],
            "remote_branch_matching_head": bool(remote_head_sha == head_sha),
            "dirty_tracked_files_after": dirty_tracked,
            "dirty_files_after": [
                line for line in _git_cmd(["status", "--short"]).splitlines() if line.strip()
            ],
        },
        "implementation_map": impl_map,
        "tests": {
            "command": f"{sys.executable} tools/run_v17_9_1_verification.py",
            "discovered_total": int(pytest_inventory["discovered_total"]),
            "executed_total": executed_total,
            "excluded_total": 0,
            "total": executed_total,
            "passed": passed_total,
            "failed": failed_total,
            "errors": errors_total,
            "skipped": skipped_total,
            "xfail": xfail_total,
            "xpass": xpass_total,
            "shards_expected": int(async_summary.get("scheduled_tasks", 0)),
            "shards_completed": int(async_summary.get("succeeded_tasks", 0)),
            "junit_paths": [str(junit_dir / f"{s[0].lower()}.xml") for s in shard_definitions],
            "wall_clock_duration_sec": round(time.time() - start_wall, 2),
        },
        "parity": backtest_res.get("parity", {}),
        "backtest": {
            "DATA_VALIDATED": backtest_res.get("DATA_VALIDATED"),
            "ENGINEERING_REPLAY_COMPLETED": backtest_res.get("ENGINEERING_REPLAY_COMPLETED"),
            "PARITY_VERIFIED": backtest_res.get("PARITY_VERIFIED"),
            "BASELINE_ORACLE_PARITY": backtest_res.get("BASELINE_ORACLE_PARITY"),
            "OBSERVE_ONLY_ACTION_INVARIANCE": backtest_res.get("OBSERVE_ONLY_ACTION_INVARIANCE"),
            "THREE_RUNTIME_CONSUMED_PARITY": backtest_res.get("THREE_RUNTIME_CONSUMED_PARITY"),
            "MTF_ON_STRATEGY_BEHAVIOR": backtest_res.get("MTF_ON_STRATEGY_BEHAVIOR"),
            "EXPERIMENT_STATISTICAL_COVERAGE": backtest_res.get("EXPERIMENT_STATISTICAL_COVERAGE"),
            "STRATEGY_STATISTICAL_CONCLUSION": backtest_res.get("STRATEGY_STATISTICAL_CONCLUSION"),
            "dataset": backtest_res.get("dataset", {}),
            "variants": backtest_res.get("variants", {}),
            "coverage_limitations": backtest_res.get("coverage_limitations"),
        },
        "causal_audit": causal_audit_payload,
        "disk_guard": {
            "min_free_bytes_observed": guard_rep.get("minimum_free_disk_bytes_observed", cleanup_report.disk_peak_bytes),
            "peak_temp_bytes": guard_rep.get("peak_temporary_bytes", 0),
            "Peak_artifact_bytes": cleanup_report.bytes_retained,
            "bytes_pruned": cleanup_report.bytes_pruned_ephemeral,
            "resource_samples_recorded": perf_report.get("resource_telemetry", {}).get("samples_recorded", 0),
            "peak_rss_mb": perf_report.get("resource_telemetry", {}).get("peak_rss_mb", 0.0),
            "status": cleanup_report.status,
        },
        "artifact_sha256_manifest": artifact_hashes,
        "gates": gates_dict,
        "final_verdict": "PASS" if all_gates_pass else "FAIL",
    }

    validation_res = validate_final_verification_report(final_report)
    final_report["report_validation"] = validation_res.to_dict()
    if not validation_res.is_valid:
        final_report["final_verdict"] = "FAIL"
        final_report["gates"]["G13"]["status"] = "FAIL"
        final_report["gates"]["G13"]["passed"] = False
        final_report["gates"]["G13"]["reasons"] = list(validation_res.reasons)

    _write_json(artifact_dir / "v17_9_1_final_verification_report.json", final_report)

    _write_json(
        REPO_ROOT / "docs" / "artifacts" / "v17_9_1" / "latest_run.json",
        {
            "run_id": run_id,
            "commit_sha": head_sha,
            "executable_source_tree_hash": exec_tree_info["executable_source_tree_hash"],
            "artifact_dir": str(artifact_dir),
            "final_report_path": str(artifact_dir / "v17_9_1_final_verification_report.json"),
            "final_verdict": final_report["final_verdict"],
        },
    )

    print(
        json.dumps(
            {
                "run_id": run_id,
                "commit_sha": head_sha,
                "discovered_tests": pytest_inventory["discovered_total"],
                "executed_tests": executed_total,
                "final_verdict": final_report["final_verdict"],
            },
            indent=2,
        )
    )
    return 0 if final_report["final_verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
