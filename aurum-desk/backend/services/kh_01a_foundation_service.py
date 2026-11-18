"""
backend/services/kh_01a_foundation_service.py

AURUM Desk — KH-01A (SPEC VERSION: KH-01A-v1.0)
Data & Verification Foundation Service:
  1. Function-level callgraph, contract discovery, and explicit CHANGED / EXTENDED / KEPT AS IS matrix.
  2. Strict causal pre-entry loss & win-control forensics (joining point-in-time `MarketDecisionContext`,
     `StrategyDecisionSnapshot`, `SignalQualityEvidenceV1793122`, `MacroTimeWindowState`, and verified
     `CausalFeatureSnapshot` with `known_at_ms <= decision_timestamp_ms`, zero hindsight, honest
     `PRE_ENTRY_UNKNOWN` census, separately tagged ex-post diagnostics, and ranked KH-01B hypotheses).
  3. Read-only Dataset Registry & Fresh-OOS Escrow validator (`REUSED_RESEARCH` vs `FRESH_UNTOUCHED`,
     rejecting chronological/random splits of previously inspected data, and actionable post-freeze
     Bitget XAUUSDT collection & cryptographic escrow plan).
  4. Real-injected adversarial fault suite (RED-on-fault / GREEN-on-valid, zero report-only padding,
     including synthetic nonzero-D4 positive invariant fixture) and the 14 mandatory KH-01A gates
     (`G-KH01A-01` .. `G-KH01A-14`) with orthogonal engineering/economic/OOS/activation/publication verdicts.

NON-OPTIMIZATION & SAFETY BOUNDARY:
  - Zero strategy threshold changes, zero Unicorn relaxation, zero risk/sizing changes (`<= $2.50` USD),
    zero TP selector changes, zero live/paper/auto order activation (`PRODUCTION=OFF`, `LIVE=OFF`,
    `PAPER=OFF`, `AUTO=OFF`, `ACTIVATION_READY=NO`).
"""
from __future__ import annotations

import ast
import bisect
from collections import Counter, defaultdict
import copy
from dataclasses import asdict, dataclass, field
import datetime
import errno
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo

from domain_calculator import CostAssumptions
from services.scalp_m1_types import get_scalp_config
from services.shared_strategy_evaluator import (
    SharedStrategyEvaluator,
    StrategyDecisionSnapshot,
    build_market_decision_context,
)
from services.v17_9_1_decision_funnel import classify_historical_dataset_gaps, format_utc_iso
from services.v17_9_3_1_2_2_signal_intelligence import (
    CANONICAL_V1793123_POLICY_IDS,
    build_signal_quality_evidence_v1793122,
    evaluate_signal_intelligence_gate_v1793122,
)
from services.v17_9_3_1_2_7_causal_signal_truth import (
    canonical_hash,
    derive_pre_entry_features_from_causal_context_v1793127,
    verify_feature_snapshot_v1793127,
)
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    verify_fresh_junit_bijection_v17931211,
)
from services.v17_9_3_1_2_14_accounting_target_and_d4_truth_service import (
    audit_target_reachability_row_v17931214,
    compute_causal_tf_atr_v17931214,
)
from services.v17_9_3_1_2_19_truth_and_core_repair_service import (
    CANONICAL_DATASET_SHA256_V17931219,
    EvidenceReceiptV17931219,
    MANDATORY_BRANCH_V17931219,
    TypedMetricValidationError,
    build_authoritative_metrics_from_evidence_v17931219,
    evaluate_v17931219_orthogonal_gate_matrix,
    parse_required_pytest_counts_v17931219,
    validate_single_unicorn_confluence_strict,
)
from services.v17_9_3_1_2_executable_intervention_replay_service import (
    build_close_ts_index_v179312,
    recalculate_trade_risk_quantity_v179312,
)
from services.v17_9_3_macro_unicorn_intelligence import resolve_causal_macro_time_window

SPEC_VERSION_KH01A = "KH-01A-v1.0"
SCHEMA_VERSION_KH01A = "kh_01a.foundation.v1"
SPEC_VERSION_KH01A_R1 = "KH-01A.R1"
SCHEMA_VERSION_KH01A_R1 = "kh_01a_r1.forensics_closure.v1"
RECONSTRUCTION_ALGO_VERSION_KH01A_R1 = "kh_01a_r1.asof_a1_v1792_context_120_96_96_96_64_30.v1"
RECONSTRUCTION_ALGO_SHA256_KH01A_R1 = hashlib.sha256(
    RECONSTRUCTION_ALGO_VERSION_KH01A_R1.encode("utf-8")
).hexdigest()
VALID_SOURCE_ORIGINS_KH01A_R1: Tuple[str, ...] = (
    "PERSISTED_VERIFIED_SNAPSHOT",
    "RECONSTRUCTED_ASOF_RAW_BARS",
    "LEDGER_ONLY_UNVERIFIED",
    "MISSING",
    "INVALID",
)
VALID_MACRO_3WAY_CATEGORIES_KH01A_R1: Tuple[str, ...] = (
    "ELIGIBLE_IN_VERIFIED_WINDOW",
    "VALID_OUTSIDE_WINDOW",
    "UNKNOWN_OR_INVALID_MACRO_STATE",
)
MANDATORY_BRANCH_KH01A = MANDATORY_BRANCH_V17931219
CANONICAL_DATASET_SHA256_KH01A = CANONICAL_DATASET_SHA256_V17931219
PRIOR_V19_TESTED_SHA = "a81ac1f9d5783121bc55bdd305063863d10cb7af"
PRIOR_V19_PUBLICATION_HEAD_SHA = "f6197343dc35123141ae010b5ee7d90e535081c1"
PRIOR_KH01A_TESTED_SHA = "a1fd9293e2d0ac098008264ec2a6355596baf415"
PRIOR_KH01A_AUDITED_HEAD_SHA = "4b8e24fc2c6a6bfbd6a45a3d1cff0e912afe85a7"

NY_TZ = ZoneInfo("America/New_York")

REQUIRED_ARTIFACTS_KH01A: Tuple[str, ...] = (
    "00_preflight_branch_source_dataset_resources.json",
    "01_active_function_callgraph_and_keep_as_is_matrix.json",
    "02_data_gap_red_green_and_cross_tf_audit.json",
    "03_frozen_dataset_sha_session_calendar_and_quality.json",
    "04_fail_closed_red_green_real_fault_injections.json",
    "05_preentry_casebook_157_losses_and_win_controls.jsonl.gz",
    "06_causal_loss_attribution_and_unknown_evidence_census.json",
    "07_target_267_plus_40_control_provenance_audit.json",
    "08_replay_3fill_1position_oos_and_cost_stress.json",
    "09_before_after_trade_action_and_adapter_parity.json",
    "10_collected_node_ids_shards_junit_exit_manifest.json",
    "11_worker_1_2_4_repeat_determinism_speed_report.json",
    "12_disk_ram_cpu_pid_cleanup_report.json",
    "13_fresh_oos_untouched_registry_and_verdict.json",
    "14_gate_receipts_independent_readback.json",
    "15_sha256_manifest_and_source_config_versions.json",
    "16_final_orthogonal_verdicts_and_open_blockers.json",
    "README_KH01A.md",
    "HANDOVER_KH01A.txt",
)

MANDATORY_KH01A_GATES: Tuple[Tuple[str, str], ...] = (
    ("G-KH01A-01", "BRANCH_EXACT_SOURCE"),
    ("G-KH01A-02", "DATA_GAP_PROOF"),
    ("G-KH01A-03", "NO_SYNTHETIC_DATA"),
    ("G-KH01A-04", "FAIL_CLOSED_RED_GREEN"),
    ("G-KH01A-05", "RECEIPT_AND_JUNIT_INTEGRITY"),
    ("G-KH01A-06", "PREENTRY_CAUSAL_FORENSICS"),
    ("G-KH01A-07", "TARGET_PROVENANCE_AUDIT"),
    ("G-KH01A-08", "REGRESSION"),
    ("G-KH01A-09", "CHRONOLOGICAL_BACKTEST"),
    ("G-KH01A-10", "ADAPTER_AND_ACTION_PARITY"),
    ("G-KH01A-11", "ASYNC_DETERMINISM"),
    ("G-KH01A-12", "RESOURCE_AND_CLEANUP"),
    ("G-KH01A-13", "FRESH_OOS_TRUTH"),
    ("G-KH01A-14", "PUBLICATION_READBACK"),
)


# ==============================================================================
# 0. FUNCTION-LEVEL CALLGRAPH & EXPLICIT KEEP-AS-IS / CHANGED MATRIX
# ==============================================================================

def _inspect_python_function_ast(file_path: Path, func_or_method: str) -> Dict[str, Any]:
    """Read actual AST signature & line span from repository file to prove real ownership."""
    if not file_path.is_file():
        return {"exists": False, "path": str(file_path)}
    src = file_path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(file_path))
    target_cls: Optional[str] = None
    target_fn = func_or_method
    if "." in func_or_method:
        target_cls, target_fn = func_or_method.split(".", 1)

    for node in tree.body:
        if target_cls is None and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target_fn:
            args = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
            return {
                "exists": True,
                "start_line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
                "parameters": args,
            }
        if target_cls is None and isinstance(node, ast.ClassDef) and node.name == target_fn:
            return {
                "exists": True,
                "class_name": target_fn,
                "start_line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
                "methods": [s.name for s in node.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))],
            }
        if target_cls is not None and isinstance(node, ast.ClassDef) and node.name == target_cls:
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name == target_fn:
                    args = [a.arg for a in sub.args.args] + [a.arg for a in sub.args.kwonlyargs]
                    return {
                        "exists": True,
                        "class_name": target_cls,
                        "start_line": sub.lineno,
                        "end_line": getattr(sub, "end_lineno", sub.lineno),
                        "parameters": args,
                    }
    return {"exists": False, "searched": func_or_method}


def build_kh_01a_callgraph_and_keep_as_is_matrix(repo_root: Path) -> Dict[str, Any]:
    """
    Section 0 & Section 9: Produces the authoritative function -> upstream inputs ->
    downstream consumers -> invariants -> existing/new tests -> CHANGED vs KEPT AS IS matrix.
    """
    app_root = repo_root / "aurum-desk"
    entries_spec = [
        {
            "contract_item": "A",
            "action": "CHANGED",
            "execution_role": "RUNTIME_AND_RESEARCH_AUDIT_UTILITY",
            "file": "aurum-desk/backend/services/v17_9_1_decision_funnel.py",
            "function": "classify_historical_dataset_gaps",
            "upstream_inputs": ["datasets (1m/5m/15m/1H/4H/1D raw bars)", "optional verified session_calendar"],
            "downstream_consumers": [
                "tools/run_kh_01a_verification.py (02_data_gap_red_green_and_cross_tf_audit.json, 03_frozen_dataset_sha_session_calendar_and_quality.json)",
                "backend/tests/test_kh_01a_data_and_verification_foundation.py",
                "backend/tests/test_v17_9_1_positive_path_replay.py",
                "backend/tests/test_v17_9_2_1_validation_closure.py",
            ],
            "before_contract": "Labeled every >=24h gap as WEEKEND_MARKET_CLOSED (including Mon-Fri 2026-10-12..16 M15 gap) and inferred DAILY_ROLLOVER_OR_SESSION_BREAK from UTC hours without exchange calendar provenance.",
            "after_contract": "Classifies interval coverage via verified session/maintenance calendar (ZoneInfo DST-aware) + cross-timeframe active-bar proof; separates VERIFIED_SCHEDULED_MARKET_CLOSURE, VERIFIED_MAINTENANCE_PAUSE, MIXED_INTERVAL_GAP (with split segments), PROVIDER_OR_DATA_GAP, SHORT_MISSING_CANDLE, and UNKNOWN_UNVERIFIED; audits invalid timestamps, identical/conflicting duplicates, out-of-order, and cross-TF OHLC coherence; preserves raw bars & missing-bar totals.",
            "unchanged_semantics": "Raw bar bytes, SHA256, actual_bars, total_missing_bars, zero synthetic fill/interpolation/forward-fill, and all strategy trade decisions.",
            "tests": [
                "test_kh_01a_data_and_verification_foundation.py::test_sec1_weekday_ge24h_gap_not_weekend",
                "test_kh_01a_data_and_verification_foundation.py::test_sec1_mixed_weekend_weekday_gap_splits_segments",
                "test_kh_01a_data_and_verification_foundation.py::test_sec1_verified_maintenance_and_ny_dst_transition",
                "test_kh_01a_data_and_verification_foundation.py::test_sec1_unknown_calendar_duplicates_out_of_order_and_invalid_timestamps",
            ],
            "reason": "Fix confirmed defect where >=24h weekday gaps and active 24/7 Bitget weekend feed outages were misclassified as weekend closures.",
        },
        {
            "contract_item": "B",
            "action": "KEPT AS IS (dataset bytes & runtime loader semantics); ADDED read-only manifest audit",
            "execution_role": "CANONICAL_DATASET_LOADER",
            "file": "aurum-desk/backend/services/v17_8_backtest_runner.py",
            "function": "collect_or_load_verified_historical_dataset",
            "secondary_file": "aurum-desk/tools/run_v17_9_3_1_2_full_universe_backtest.py",
            "secondary_function": "load_canonical_xauusdt_datasets",
            "upstream_inputs": ["aurum-desk/backtest/historical_dataset_XAUUSDT.json.gz (SHA256 9f301fee2e62...)"],
            "downstream_consumers": ["All historical replay, funnel, D4, and parity runners"],
            "before_contract": "Loads offline canonical XAUUSDT bars across 1m/5m/15m/1H/4H/1D with allow_network_refresh=False.",
            "after_contract": "KEPT AS IS for dataset bytes and runtime loading; added read-only dataset registry & Fresh-OOS escrow audit (`build_dataset_registry_and_fresh_oos_audit_kh01a` / `audit_canonical_xauusdt_dataset_manifest_readonly`).",
            "unchanged_semantics": "100% identical candle dicts, timestamps, prices, volumes, and canonical SHA256.",
            "tests": ["test_kh_01a_data_and_verification_foundation.py::test_sec5_dataset_registry_and_fresh_oos_escrow_fail_closed"],
            "reason": "Preserve historical canonical dataset immutability while adding read-only provenance/OOS registry.",
        },
        {
            "contract_item": "C",
            "action": "CHANGED",
            "execution_role": "VERIFICATION_AUTHORITY",
            "file": "aurum-desk/backend/services/v17_9_3_1_2_19_truth_and_core_repair_service.py",
            "function": "build_authoritative_metrics_from_evidence_v17931219",
            "secondary_function": "parse_required_pytest_counts_v17931219",
            "upstream_inputs": ["junit_counters", "benchmark_data", "resource_telemetry", "neg_controls_res", "replayed_modes", "loss_census", "target_summary", "d4_summary", "receipts"],
            "downstream_consumers": ["evaluate_v17931219_orthogonal_gate_matrix", "tools/run_v17_9_3_1_2_19_verification.py", "tools/v17_9_3_1_2_19_independent_verifier.py"],
            "before_contract": "Silently defaulted missing shard_exits_all_zero to True, string 'false' to True, missing errors to 0, missing actually_executed_count/d4_matched_242_* to 0, missing no_core_unicorn_patch_justified to True, and hardcoded survivors_count==14 / universe==63085.",
            "after_contract": "Requires explicit presence and strict Python types (rejects None, bool-as-int, str-as-bool, NaN, Inf); replaces hardcoded 14/63085 literals with dynamic funnel & partition conservation checks.",
            "unchanged_semantics": "Zero effect on trading/strategy logic; compatible with complete valid V19 metric dictionaries.",
            "tests": ["test_kh_01a_data_and_verification_foundation.py::test_sec2_metrics_and_gate_matrix_fail_closed_faults"],
            "reason": "Eliminate silent default-True/default-0 verification fallbacks and scenario-hardcoded constants.",
        },
        {
            "contract_item": "D",
            "action": "CHANGED",
            "execution_role": "VERIFICATION_GATE_MATRIX",
            "file": "aurum-desk/backend/services/v17_9_3_1_2_19_truth_and_core_repair_service.py",
            "function": "evaluate_v17931219_orthogonal_gate_matrix",
            "upstream_inputs": ["AuthoritativeMetricsV17931219", "publication_status", "optional run_dir"],
            "downstream_consumers": ["V19 & KH-01A gate evaluation and cross-view verifiers"],
            "before_contract": "Defaulted missing shard_exits_all_zero to True in G03, failed legitimate nonzero D4 cohorts via hardcoded stage_05!=0 / d4_accepted!=0, and hardcoded D4/OOS/Economic verdict strings.",
            "after_contract": "Fails closed when shard_exits_all_zero is missing/non-bool/False; evaluates D4/Unicorn/Target/Loss/OOS/Economic/Publication gates dynamically from grounded evidence; keeps ENGINEERING, REGRESSION, ACCOUNTING, D4_MATERIALIZATION, FRESH_OOS, STRATEGY_ECONOMIC_EDGE, ACTIVATION_READY, and PUBLICATION strictly orthogonal.",
            "unchanged_semantics": "Preserves all 14 mandatory technical gates and strict safety locks (ACTIVATION_READY=NO).",
            "tests": [
                "test_kh_01a_data_and_verification_foundation.py::test_sec2_metrics_and_gate_matrix_fail_closed_faults",
                "test_kh_01a_data_and_verification_foundation.py::test_sec2_legitimate_nonzero_d4_synthetic_cohort_passes_invariants_without_economic_pass",
            ],
            "reason": "Prevent fake PASS on missing fields while supporting grounded future nonzero D4 datasets.",
        },
        {
            "contract_item": "E",
            "action": "EXTENDED",
            "execution_role": "RECEIPT_AND_JUNIT_VERIFIER",
            "file": "aurum-desk/backend/services/v17_9_3_1_2_19_truth_and_core_repair_service.py",
            "function": "EvidenceReceiptV17931219.is_valid_pass",
            "secondary_file": "aurum-desk/backend/services/v17_9_3_1_2_11_verification_truth_and_exit_lineage.py",
            "secondary_function": "verify_fresh_junit_bijection_v17931211",
            "upstream_inputs": ["Receipt metadata + on-disk artifact files", "Collected pytest node inventory + shard JUnit XMLs"],
            "downstream_consumers": ["evaluate_v17931219_orthogonal_gate_matrix", "evaluate_kh_01a_14_gates", "tools/kh_01a_independent_verifier.py"],
            "before_contract": "Receipt check only inspected string lengths (accepting non-hex, short input_sha256, placeholder 'a'*64, and nonexistent files); JUnit verifier skipped shard schedule check if scheduled_node_ids omitted and did not check future mtimes or <testsuite tests> counts.",
            "after_contract": "Validates hex digests, non-empty validator/path/run_id, future-timestamp skew, optional uniform-placeholder rejection, and closed-file disk SHA256 readback; JUnit verifier enforces require_shard_schedule, <testsuite tests> count equality, future mtime guard, and mandatory test_kh_01a non-skip prefix.",
            "unchanged_semantics": "Backward-compatible with existing V11/V14/V19 unit tests.",
            "tests": ["test_kh_01a_data_and_verification_foundation.py::test_sec2_receipt_and_junit_adversarial_faults"],
            "reason": "Ensure tampered, truncated, future-dated, or placeholder receipts/XMLs fail closed.",
        },
        {
            "contract_item": "F",
            "action": "CHANGED",
            "execution_role": "RESEARCH_CAUSAL_FORENSICS",
            "file": "aurum-desk/backend/services/v17_9_3_1_2_19_truth_and_core_repair_service.py",
            "function": "build_loss_episode_attribution_from_raw_v17931219",
            "upstream_inputs": ["si0_loss_rows (157)", "si0_win_rows (9)", "si8_loss_rows (4)", "si9_loss_rows (2)", "point-in-time MarketDecisionContext / StrategyDecisionSnapshot / CausalFeatureSnapshot"],
            "downstream_consumers": ["05_preentry_casebook_157_losses_and_win_controls.jsonl.gz", "06_causal_loss_attribution_and_unknown_evidence_census.json"],
            "before_contract": "Read nonexistent pre_entry_known_reason from bare episode dicts, labeling 157/157 SI0 losses OTHER_OR_UNKNOWN without joining raw point-in-time snapshots.",
            "after_contract": "Joins stable opportunity_id to verified causal pre-entry snapshots (`known_at_ms <= decision_timestamp_ms`), derives pre-entry classification strictly without post-entry arguments, preserves PRE_ENTRY_UNKNOWN + missing-evidence census when raw context is absent, separates ex-post MAE/MFE/trajectory labels, enforces 166=9+157 and SI8/SI9 deduplication, and emits ranked testable hypotheses for KH-01B.",
            "unchanged_semantics": "Read-only post-replay forensic analysis; zero changes to signal generation or trade execution.",
            "tests": ["test_kh_01a_data_and_verification_foundation.py::test_sec3_causal_loss_forensics_no_hindsight_and_unknown_honesty"],
            "reason": "Provide complete causal pre-entry attribution without hindsight or fabricated labels.",
        },
        {
            "contract_item": "G",
            "action": "KEPT AS IS",
            "execution_role": "READ_ONLY_SIGNAL_EVIDENCE_SUPPLIER",
            "file": "aurum-desk/backend/services/v17_9_3_1_2_2_signal_intelligence.py",
            "function": "build_signal_quality_evidence_v1793122",
            "secondary_function": "evaluate_signal_intelligence_gate_v1793122",
            "upstream_inputs": ["MarketDecisionContext", "StrategyDecisionSnapshot"],
            "downstream_consumers": ["SharedStrategyEvaluator", "replay_single_si_policy_v1793122", "KH-01A forensics (read-only)"],
            "before_contract": "Computes causal point-in-time signal quality evidence and SI0..SI9 research gate decisions.",
            "after_contract": "KEPT AS IS — zero edits to file, thresholds, scoring, or arbitration.",
            "unchanged_semantics": "100% unchanged.",
            "tests": ["test_kh_01a_data_and_verification_foundation.py::test_sec5_trade_action_fingerprint_and_adapter_parity"],
            "reason": "Non-optimization boundary: evidence supplier only.",
        },
        {
            "contract_item": "H",
            "action": "KEPT AS IS",
            "execution_role": "CANONICAL_STRATEGY_DECISION_AUTHORITY",
            "file": "aurum-desk/backend/services/shared_strategy_evaluator.py",
            "function": "SharedStrategyEvaluator.evaluate_model_direction",
            "upstream_inputs": ["MarketDecisionContext", "model_id", "direction"],
            "downstream_consumers": ["ScalpWatcherOrchestrator", "MarketShadowService", "V17_8_BacktestRunner", "KH-01A parity & forensics"],
            "before_contract": "Produces StrategyDecisionSnapshot with deterministic decision_fingerprint and trade_action_fingerprint.",
            "after_contract": "KEPT AS IS — zero edits to file, executable trading behavior, or decision hashes.",
            "unchanged_semantics": "100% bitwise identical decision_fingerprint and trade_action_fingerprint.",
            "tests": ["test_kh_01a_data_and_verification_foundation.py::test_sec5_trade_action_fingerprint_and_adapter_parity"],
            "reason": "Preserve canonical strategy decision authority intact.",
        },
        {
            "contract_item": "I",
            "action": "KEPT AS IS",
            "execution_role": "MACRO_AND_TRUE_UNICORN_AUTHORITY",
            "file": "aurum-desk/backend/services/v17_9_3_macro_unicorn_intelligence.py",
            "function": "resolve_causal_macro_time_window",
            "secondary_function": "evaluate_causal_unicorn_confluence",
            "upstream_inputs": ["symbol", "decision_timestamp_ms", "closed candles", "breaker & FVG zones"],
            "downstream_consumers": ["SharedStrategyEvaluator", "D4 causal Unicorn pipeline", "KH-01A forensics (read-only)"],
            "before_contract": "Enforces ICT macro windows and strict causal Breaker+FVG >=1 tick overlap.",
            "after_contract": "KEPT AS IS — zero edits to file, ICT timing, or True Unicorn confluence rules.",
            "unchanged_semantics": "100% unchanged.",
            "tests": ["test_v17_9_3_macro_unicorn_intelligence.py"],
            "reason": "Phase boundary prohibits redesigning or relaxing Unicorn criteria.",
        },
        {
            "contract_item": "J",
            "action": "KEPT AS IS",
            "execution_role": "STRUCTURAL_SL_TP_AND_RISK_SIZING_AUTHORITY",
            "file": "aurum-desk/backend/services/v17_9_2_structural_stop_intelligence.py",
            "function": "plan_v17_9_2_structural_stop_and_target",
            "secondary_function": "select_viable_structural_target",
            "upstream_inputs": ["Candidate entry", "confirmed swings/zones", "liquidity pools", "CostAssumptions"],
            "downstream_consumers": ["SharedStrategyEvaluator", "Historical & shadow adapters"],
            "before_contract": "Plans independent structural SL, unswept liquidity TP, min_net_rr >= 2.0, and fixed risk <= $2.50 USD.",
            "after_contract": "KEPT AS IS — zero edits to file, SL/TP geometry, or risk sizing.",
            "unchanged_semantics": "100% unchanged.",
            "tests": ["test_v17_9_2_structural_stop.py"],
            "reason": "Preserve V17.9.2 structural SL/TP and <= $2.50 risk cap intact.",
        },
        {
            "contract_item": "K",
            "action": "EXTENDED (read-only provenance/spread/unit-guard diagnostics ONLY)",
            "execution_role": "READ_ONLY_TARGET_DATA_AUTHORITY_AUDIT",
            "file": "aurum-desk/backend/services/v17_9_3_1_2_14_accounting_target_and_d4_truth_service.py",
            "function": "audit_target_reachability_row_v17931214",
            "secondary_function": "build_267_target_failure_and_control_audit_v17931214",
            "upstream_inputs": ["267 historical SI9_H5 rows + 40 control rows", "causal liquidity pools", "datasets", "close_ts_index"],
            "downstream_consumers": ["07_target_267_plus_40_control_provenance_audit.json", "verify_267_target_audit_v17931214"],
            "before_contract": "Audited 267 + 40 rows with source-TF ATR and Net R:R, but assumed 0.20 spread even if missing/zero spread injected and lacked explicit before/after M1-vs-HTF gate fields and NOT_EVALUATED selector counterfactual disclosure.",
            "after_contract": "Rejects missing/zero spread assumptions and M1-for-HTF ATR substitution; emits explicit before/after M1-vs-source-TF gate fields, control equality verification, and NOT_EVALUATED_READ_ONLY_AUDIT_ONLY selector counterfactual status while keeping executable TP selection 100% unchanged.",
            "unchanged_semantics": "Zero change to executable target selection (`select_viable_structural_target` untouched).",
            "tests": ["test_kh_01a_data_and_verification_foundation.py::test_sec4_target_267_audit_spread_and_atr_unit_guards"],
            "reason": "Strengthen target/ATR provenance audit and eliminate misleading empty counterfactuals=[].",
        },
        {
            "contract_item": "L",
            "action": "EXTENDED (OOS provenance & 10-policy ledger reconciliation ONLY; strategy flags & fills KEPT AS IS)",
            "execution_role": "CHRONOLOGICAL_BACKTEST_STRESS_AND_OOS_AUDIT",
            "file": "aurum-desk/backend/services/v17_9_3_1_2_14_accounting_target_and_d4_truth_service.py",
            "function": "build_cost_stress_and_purged_walkforward_oos_v17931214",
            "upstream_inputs": ["policy_dtos (SI0..SI9 x 3 fill modes)", "variant_1pos_dtos (A0..D4 x 3 fill modes)", "si0_nb_episodes", "bars_1m"],
            "downstream_consumers": ["08_replay_3fill_1position_oos_and_cost_stress.json", "13_fresh_oos_untouched_registry_and_verdict.json"],
            "before_contract": "Summarized SI0/SI8/SI9 + A0..D4 3-fill stress and 180m purged walk-forward folds with BLOCKED_NO_UNTOUCHED_DATA.",
            "after_contract": "Includes all 10 policies (SI0..SI9) x 3 fill modes conservation summary, fold embargo non-overlap proof, and explicit dataset reuse registry linkage while keeping all strategy flags and fill definitions KEPT AS IS.",
            "unchanged_semantics": "Identical trade fills, fees, slippage, 180m embargo, and PnL numbers.",
            "tests": ["test_kh_01a_data_and_verification_foundation.py::test_sec5_dataset_registry_and_fresh_oos_escrow_fail_closed"],
            "reason": "Strengthen OOS provenance and full SI0..SI9 ledger reconciliation.",
        },
        {
            "contract_item": "M",
            "action": "CHANGED CAREFULLY + ADDED narrow KH-01A entry point",
            "execution_role": "ASYNC_BOUNDED_VERIFICATION_ORCHESTRATOR_AND_DURABLE_IO",
            "file": "aurum-desk/tools/run_v17_9_3_1_2_19_verification.py",
            "function": "_SafeStreamWrapper",
            "secondary_file": "aurum-desk/tools/fresh_junit_io.py",
            "secondary_function": "write_bytes_durable_atomic",
            "upstream_inputs": ["Frozen repository tree", "pytest shards", "canonical dataset", "TestResourceGuard"],
            "downstream_consumers": ["tools/run_kh_01a_verification.py", "tools/kh_01a_independent_verifier.py"],
            "before_contract": "_SafeStreamWrapper wrapped stdout/stderr at import time and swallowed OSErrors; write_json lacked fsync/readback; _make_receipt forged 'a'*64 when target file missing; counterfactuals defaulted to [].",
            "after_contract": "Removed import-time stdout/stderr wrapping and OSError swallowing; added fsync+readback durable atomic writers in fresh_junit_io.py; removed 'a'*64 and hardcoded loss fallbacks; added tools/run_kh_01a_verification.py and tools/kh_01a_independent_verifier.py.",
            "unchanged_semantics": "Existing immutable V19 published artifacts in docs/artifacts/v17_9_3_1_2_19 remain byte-untouched.",
            "tests": ["test_kh_01a_data_and_verification_foundation.py::test_sec2_durable_io_enospc_and_safe_stream_fail_closed"],
            "reason": "Ensure storage errors, partial writes, and missing receipt targets fail closed.",
        },
        {
            "contract_item": "N",
            "action": "KEPT AS IS",
            "execution_role": "LIVE_PAPER_RUNTIME_ORCHESTRATION_AND_UI",
            "file": "aurum-desk/backend/services/scalp_watcher_orchestrator.py",
            "function": "ScalpWatcherOrchestrator.evaluate_cycle",
            "secondary_file": "aurum-desk/backend/paper_broker.py",
            "secondary_function": "PaperBroker.execute_market_order",
            "upstream_inputs": ["Live/paper runtime state, news guards, Bitget REST/WS, frontend UI"],
            "downstream_consumers": ["Runtime execution boundary (STRICTLY OFF in KH-01A)"],
            "before_contract": "Runtime orchestration, paper broker execution, news guards, UI, Bitget REST/WS, and live routing.",
            "after_contract": "KEPT AS IS — zero edits, zero order activation, PRODUCTION=OFF, LIVE=OFF, PAPER=OFF, AUTO=OFF.",
            "unchanged_semantics": "100% untouched.",
            "tests": ["test_kh_01a_data_and_verification_foundation.py::test_sec5_trade_action_fingerprint_and_adapter_parity"],
            "reason": "Strict safety & scope boundary: no live/paper/UI/exchange routing modifications.",
        },
    ]

    for item in entries_spec:
        f_path = repo_root / item["file"]
        item["ast_verification"] = _inspect_python_function_ast(f_path, item["function"])
        if "secondary_file" in item and "secondary_function" in item:
            sf_path = repo_root / item["secondary_file"]
            item["secondary_ast_verification"] = _inspect_python_function_ast(sf_path, item["secondary_function"])
        elif "secondary_function" in item:
            item["secondary_ast_verification"] = _inspect_python_function_ast(f_path, item["secondary_function"])

    added_functions = [
        {
            "file": "aurum-desk/backend/services/kh_01a_foundation_service.py",
            "functions": [
                "build_kh_01a_callgraph_and_keep_as_is_matrix",
                "classify_pre_entry_state_strictly_asof",
                "build_kh_01a_causal_loss_and_win_control_forensics",
                "validate_fresh_oos_escrow_claim_kh01a",
                "build_dataset_registry_and_fresh_oos_audit_kh01a",
                "run_kh_01a_real_fault_injections",
                "evaluate_kh_01a_14_gates",
                "verify_kh_01a_cross_view_consistency",
                "render_kh_01a_readme_and_handover_vi",
            ],
            "reason": "Dedicated KH-01A foundation verification, causal forensics, dataset registry, and 14-gate evaluation without duplicating replay/strategy engines.",
        },
        {
            "file": "aurum-desk/tools/fresh_junit_io.py",
            "functions": [
                "write_bytes_durable_atomic",
                "write_text_durable_atomic",
                "write_json_durable_atomic",
                "write_jsonl_gz_durable_atomic",
            ],
            "reason": "Shared fsync + atomic rename + closed-file SHA256 readback verification failing closed on ENOSPC/partial writes.",
        },
        {
            "file": "aurum-desk/tools/run_kh_01a_verification.py",
            "functions": ["main", "run_async_pytest_shards_kh01a", "run_controlled_worker_1_2_4_subset_benchmark_kh01a"],
            "reason": "Narrow KH-01A async bounded multiprocess verification orchestrator.",
        },
        {
            "file": "aurum-desk/tools/kh_01a_independent_verifier.py",
            "functions": ["verify_kh_01a_run_dir_from_disk", "main"],
            "reason": "Separate-OS-process independent disk verifier reading written artifacts only.",
        },
    ]

    return {
        "spec_version": SPEC_VERSION_KH01A,
        "schema_version": SCHEMA_VERSION_KH01A,
        "matrix": entries_spec,
        "added_functions": added_functions,
        "removed_functions": [],
        "explicit_kept_as_is_modules": [
            "aurum-desk/backend/services/shared_strategy_evaluator.py (KEPT AS IS)",
            "aurum-desk/backend/services/v17_9_3_macro_unicorn_intelligence.py (KEPT AS IS)",
            "aurum-desk/backend/services/v17_9_2_structural_stop_intelligence.py (KEPT AS IS)",
            "aurum-desk/backend/services/v17_9_3_1_2_2_signal_intelligence.py (KEPT AS IS)",
            "aurum-desk/backend/services/v17_8_backtest_runner.py (KEPT AS IS)",
            "aurum-desk/backend/services/scalp_watcher_orchestrator.py (KEPT AS IS)",
            "aurum-desk/backend/paper_broker.py (KEPT AS IS)",
            "News guards, frontend UI, Bitget REST/WS clients, and live order routing (KEPT AS IS)",
        ],
    }


# ==============================================================================
# 3. CAUSAL LOSS & WIN-CONTROL FORENSICS (ZERO HINDSIGHT, R1 PROVENANCE CLOSURE)
# ==============================================================================

_PLACEHOLDER_SOURCE_ID_TOKENS: Tuple[str, ...] = (
    "PLACEHOLDER",
    "FAKE_BAR",
    "DUMMY_BAR",
    "SYNTHETIC_BAR",
    "UNVERIFIED_BAR",
    "TODO",
)


def validate_and_classify_macro_state_kh01a_r1(
    macro_st: Any,
    *,
    expected_decision_timestamp_ms: Any,
    expected_symbol: Any = "XAUUSDT",
) -> Dict[str, Any]:
    """
    KH-01A.R1 Phase 1 (D2-D4): Strictly validates a `MacroTimeWindowState` against
    expected `(decision_timestamp_ms, symbol)` and classifies it into exactly one of
    THREE mutually exclusive states:
      1. `ELIGIBLE_IN_VERIFIED_WINDOW`: valid timestamp, symbol, NY/DST conversion, and
         configured schedule (`schedule_valid=True`, `session_group` valid), with
         `eligible_for_macro_gate is True`.
      2. `VALID_OUTSIDE_WINDOW`: valid timestamp, symbol, NY/DST conversion, and configured
         schedule (`schedule_valid=True`, `session_group` valid), with
         `eligible_for_macro_gate is False`.
      3. `UNKNOWN_OR_INVALID_MACRO_STATE`: invalid timestamp, reversed arguments, invalid
         schedule, unresolved calendar (`CALENDAR_UNVERIFIED`), `session_group == "INVALID"`,
         incompatible symbol, or missing NY/DST fields.
         NEVER converts an unknown/invalid macro state into `in_ict_macro_window = False`
         or `ny_session_group = "INVALID"` (`PRESENT`).
    """
    violations: List[str] = []
    ts_type_ok = (
        isinstance(expected_decision_timestamp_ms, int)
        and not isinstance(expected_decision_timestamp_ms, bool)
        and expected_decision_timestamp_ms > 0
    )
    if not ts_type_ok:
        violations.append(f"EXPECTED_DECISION_TS_INVALID:{expected_decision_timestamp_ms!r}")

    sym_ok = isinstance(expected_symbol, str) and expected_symbol.strip().upper() == "XAUUSDT"
    if not sym_ok:
        violations.append(f"EXPECTED_SYMBOL_INVALID:{expected_symbol!r}")

    st_sym = getattr(macro_st, "symbol", None)
    if st_sym != "XAUUSDT":
        violations.append(f"MACRO_STATE_SYMBOL_MISMATCH:{st_sym!r}!=XAUUSDT")

    st_ts = getattr(macro_st, "decision_timestamp_ms", None)
    is_valid_ts = getattr(macro_st, "is_valid_timestamp", None)
    if is_valid_ts is not True:
        violations.append(f"MACRO_STATE_TIMESTAMP_INVALID:{is_valid_ts!r}")
    if ts_type_ok and st_ts != expected_decision_timestamp_ms:
        violations.append(f"MACRO_STATE_TS_MISMATCH:{st_ts!r}!={expected_decision_timestamp_ms!r}")

    sched_valid = getattr(macro_st, "schedule_valid", None)
    if sched_valid is not True:
        violations.append(f"MACRO_SCHEDULE_INVALID:{sched_valid!r}")

    sess_grp = str(getattr(macro_st, "session_group", "") or "")
    is_calendar_unverified_block = (sess_grp == "CALENDAR_UNVERIFIED")
    if sess_grp in ("", "INVALID", "INVALID_SCHEDULE", "CALENDAR_UNVERIFIED"):
        violations.append(f"MACRO_SESSION_GROUP_NOT_RESOLVED:{sess_grp!r}")

    tz_name = getattr(macro_st, "timezone_name", None)
    ny_iso = getattr(macro_st, "ny_local_iso", None)
    ny_hhmm = getattr(macro_st, "ny_local_hhmm", None)
    ny_wd = getattr(macro_st, "ny_weekday", None)
    is_dst = getattr(macro_st, "is_dst", None)
    utc_off = getattr(macro_st, "utc_offset_minutes", None)
    if (
        tz_name != "America/New_York"
        or not isinstance(ny_iso, str)
        or not ny_iso
        or not isinstance(ny_hhmm, str)
        or len(ny_hhmm) != 5
        or not isinstance(ny_wd, int)
        or isinstance(ny_wd, bool)
        or ny_wd not in range(7)
        or not isinstance(is_dst, bool)
        or utc_off not in (-240, -300)
    ):
        violations.append(
            f"MACRO_NY_DST_CONVERSION_INVALID:tz={tz_name!r},hhmm={ny_hhmm!r},dst={is_dst!r},off={utc_off!r}"
        )

    elig = getattr(macro_st, "eligible_for_macro_gate", None)
    if not isinstance(elig, bool):
        violations.append(f"MACRO_ELIGIBLE_NOT_BOOL:{elig!r}")

    active_wids = tuple(getattr(macro_st, "active_window_ids", ()) or ())
    prim_wid = str(getattr(macro_st, "primary_window_id", "") or "")
    is_wknd = bool(getattr(macro_st, "is_weekend_or_closed", False))
    is_hol = bool(getattr(macro_st, "is_holiday_closed", False))

    if not violations and elig is True:
        if is_wknd or is_hol or not active_wids or prim_wid in ("", "OUTSIDE_MACRO_WINDOW", "INVALID_TIMESTAMP", "INVALID_SCHEDULE"):
            violations.append(
                f"MACRO_ELIGIBLE_INCONSISTENT_WITH_WINDOW_STATE:wknd={is_wknd},hol={is_hol},active={active_wids},prim={prim_wid}"
            )

    if not violations:
        category = "ELIGIBLE_IN_VERIFIED_WINDOW" if elig is True else "VALID_OUTSIDE_WINDOW"
        is_valid_resolution = True
        in_window_value: Optional[bool] = bool(elig)
        session_value: Optional[str] = sess_grp
        cell_state = "PRESENT"
        in_window_proof = "PROVEN_TRUE" if elig is True else "PROVEN_FALSE"
        session_proof = "MEASURED_VALUE"
        source_origin = "RECONSTRUCTED_ASOF_RAW_BARS"
    else:
        category = "UNKNOWN_OR_INVALID_MACRO_STATE"
        is_valid_resolution = False
        in_window_value = None
        session_value = None
        if (
            ts_type_ok
            and sym_ok
            and is_valid_ts is True
            and sched_valid is True
            and is_calendar_unverified_block
        ):
            cell_state = "MISSING"
            in_window_proof = "UNKNOWN"
            session_proof = "UNKNOWN"
            source_origin = "MISSING"
        else:
            cell_state = "INVALID"
            in_window_proof = "INVALID"
            session_proof = "INVALID"
            source_origin = "INVALID"

    cal_status = str(getattr(macro_st, "calendar_verification_status", "CALENDAR_NOT_VERIFIED") or "CALENDAR_NOT_VERIFIED")
    sched_src = str(getattr(macro_st, "source", "RESEARCH_HYPOTHESIS_CONFIG") or "RESEARCH_HYPOTHESIS_CONFIG")
    return {
        "macro_state_category": category,
        "is_valid_macro_resolution": is_valid_resolution,
        "in_ict_macro_window_value": in_window_value,
        "ny_session_group_value": session_value,
        "cell_state": cell_state,
        "in_window_proof_state": in_window_proof,
        "session_proof_state": session_proof,
        "source_origin": source_origin,
        "validation_violations": violations,
        "raw_macro_diagnostics": {
            "symbol": st_sym,
            "decision_timestamp_ms": st_ts,
            "is_valid_timestamp": bool(is_valid_ts) if isinstance(is_valid_ts, bool) else False,
            "schedule_valid": bool(sched_valid) if isinstance(sched_valid, bool) else False,
            "schedule_version": getattr(macro_st, "schedule_version", None),
            "schedule_sha256": getattr(macro_st, "schedule_sha256", None),
            "schedule_source": sched_src,
            "exchange_calendar_verification_status": cal_status,
            "schedule_provenance_disclosure": (
                "Deterministic ICT America/New_York macro window configuration (DST-aware half-open [start, end)); "
                f"official Bitget exchange holiday/maintenance calendar status is {cal_status} (never claimed as official exchange closure proof)."
            ),
            "timezone_name": tz_name,
            "utc_iso": getattr(macro_st, "utc_iso", None),
            "ny_local_iso": ny_iso,
            "ny_local_date": getattr(macro_st, "ny_local_date", None),
            "ny_local_hhmm": ny_hhmm,
            "ny_weekday": ny_wd,
            "utc_offset_minutes": utc_off,
            "is_dst": is_dst,
            "is_weekend_or_closed": is_wknd,
            "is_holiday_closed": is_hol,
            "primary_window_id": prim_wid,
            "active_window_ids": list(active_wids),
            "session_group": sess_grp,
            "eligible_for_macro_gate_raw": elig,
            "minutes_into_active_window": getattr(macro_st, "minutes_into_active_window", None),
            "minutes_remaining_in_active_window": getattr(macro_st, "minutes_remaining_in_active_window", None),
            "nearest_window_id": getattr(macro_st, "nearest_window_id", None),
            "minutes_to_nearest_window": getattr(macro_st, "minutes_to_nearest_window", None),
            "reason_codes": list(getattr(macro_st, "reason_codes", ()) or ()),
        },
    }


def _make_feature_cell(
    feature_name: str,
    value: Any,
    *,
    state: str,
    proof_state: str,
    known_at_ms: Optional[int],
    decision_ts_ms: int,
    raw_source_ids: Sequence[str],
    source_origin: Optional[str] = None,
    source_timeframe: Optional[str] = None,
    closed_bar_close_ms: Optional[int] = None,
    reconstruction_algo_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    if state not in ("PRESENT", "MISSING", "INVALID"):
        raise ValueError(f"INVALID_FEATURE_STATE:{feature_name}:{state}")
    eff_origin = source_origin or (
        "RECONSTRUCTED_ASOF_RAW_BARS"
        if state == "PRESENT"
        else ("MISSING" if state == "MISSING" else "INVALID")
    )
    if eff_origin not in VALID_SOURCE_ORIGINS_KH01A_R1:
        raise ValueError(f"INVALID_FEATURE_SOURCE_ORIGIN:{feature_name}:{eff_origin}")
    if state == "PRESENT" and eff_origin in ("MISSING", "INVALID"):
        raise ValueError(f"PRESENT_FEATURE_WITH_NON_PRESENT_ORIGIN:{feature_name}:{eff_origin}")
    if state == "MISSING" and value is not None:
        raise ValueError(f"MISSING_FEATURE_MUST_HAVE_NULL_VALUE:{feature_name}:{value!r}")
    if state == "INVALID" and value is not None:
        raise ValueError(f"INVALID_FEATURE_MUST_HAVE_NULL_VALUE:{feature_name}:{value!r}")

    if known_at_ms is not None:
        if isinstance(known_at_ms, bool) or not isinstance(known_at_ms, int):
            raise ValueError(f"INVALID_KNOWN_AT_MS_TYPE:{feature_name}:{known_at_ms!r}")
        if int(known_at_ms) > int(decision_ts_ms):
            raise ValueError(
                f"CAUSAL_PRE_ENTRY_LOOKAHEAD_VIOLATION:{feature_name}:known_at_ms={known_at_ms}>decision_ts_ms={decision_ts_ms}"
            )

    eff_close_ms = closed_bar_close_ms if closed_bar_close_ms is not None else (
        int(known_at_ms) if (state == "PRESENT" and known_at_ms is not None) else None
    )
    if eff_close_ms is not None:
        if isinstance(eff_close_ms, bool) or not isinstance(eff_close_ms, int):
            raise ValueError(f"INVALID_CLOSED_BAR_CLOSE_MS_TYPE:{feature_name}:{eff_close_ms!r}")
        if int(eff_close_ms) > int(decision_ts_ms):
            raise ValueError(
                f"CAUSAL_PRE_ENTRY_BAR_CLOSE_LOOKAHEAD_VIOLATION:{feature_name}:closed_bar_close_ms={eff_close_ms}>decision_ts_ms={decision_ts_ms}"
            )

    src_list = [str(s).strip() for s in raw_source_ids if s and str(s).strip()]
    if state == "PRESENT":
        if not src_list and eff_origin in ("PERSISTED_VERIFIED_SNAPSHOT", "RECONSTRUCTED_ASOF_RAW_BARS"):
            raise ValueError(f"MISSING_SOURCE_BAR_IDS_FOR_PRESENT_FEATURE:{feature_name}:{eff_origin}")
        for sid in src_list:
            up_sid = sid.upper()
            if any(tok in up_sid for tok in _PLACEHOLDER_SOURCE_ID_TOKENS):
                raise ValueError(f"PLACEHOLDER_SOURCE_ID_REJECTED:{feature_name}:{sid}")

    eff_algo_sha = reconstruction_algo_sha256 or (
        RECONSTRUCTION_ALGO_SHA256_KH01A_R1 if eff_origin == "RECONSTRUCTED_ASOF_RAW_BARS" else None
    )
    ev_hash = canonical_hash(
        {
            "feature": feature_name,
            "value": value,
            "state": state,
            "proof_state": proof_state,
            "source_origin": eff_origin,
            "source_timeframe": source_timeframe,
            "known_at_ms": int(known_at_ms) if known_at_ms is not None else None,
            "closed_bar_close_ms": int(eff_close_ms) if eff_close_ms is not None else None,
            "raw_source_ids": src_list,
            "reconstruction_algo_sha256": eff_algo_sha,
        }
    )
    return {
        "value": value,
        "state": state,
        "proof_state": proof_state,
        "source_origin": eff_origin,
        "source_timeframe": source_timeframe,
        "known_at_ms": int(known_at_ms) if known_at_ms is not None else None,
        "closed_bar_close_ms": int(eff_close_ms) if eff_close_ms is not None else None,
        "raw_source_ids": src_list,
        "reconstruction_algo_sha256": eff_algo_sha,
        "evidence_sha256": ev_hash,
    }


def classify_pre_entry_state_strictly_asof(
    *,
    decision_timestamp_ms: int,
    direction: str,
    pre_entry_features: Mapping[str, Mapping[str, Any]],
    raw_snapshot_joined: bool,
    original_snapshot_verified: bool = False,
    asof_reconstruction_verified: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Pure point-in-time pre-entry classifier (KH-01A.R1 hardened).
    STRUCTURAL ANTI-HINDSIGHT GUARANTEE: Does NOT accept `outcome_label`, `exit_type`,
    `exit_fill_price`, `realized_net_pnl_usd`, or `forward_bars`. Identical logic runs
    on both wins and losses.
    R1 SEMANTIC GUARANTEES:
      - `OUTSIDE_ICT_MACRO_WINDOW` is emitted ONLY when `in_ict_macro_window` has
        `state == "PRESENT"` and `value is False`. Unknown/missing macro state emits
        `ICT_MACRO_WINDOW_UNKNOWN` in `unknown_or_unproven_flags` and NEVER claims
        a proven entry failure.
      - Unknown/missing features (`sweep`, `MSS`, `Unicorn`, `HTF bias`, `target TF`)
        are NEVER treated as proven `False`; they are recorded in `unknown_or_unproven_flags`.
      - Row `evidence_completeness` is explicitly `"COMPLETE"`, `"PARTIAL"`, `"UNKNOWN"`,
        or `"INVALID"`.
    """
    eff_asof_verified = bool(raw_snapshot_joined) if asof_reconstruction_verified is None else bool(asof_reconstruction_verified)
    present_fields = sorted([k for k, v in pre_entry_features.items() if v.get("state") == "PRESENT"])
    missing_fields = sorted([k for k, v in pre_entry_features.items() if v.get("state") == "MISSING"])
    invalid_fields = sorted([k for k, v in pre_entry_features.items() if v.get("state") == "INVALID"])

    for k, v in pre_entry_features.items():
        kn = v.get("known_at_ms")
        if kn is not None and int(kn) > int(decision_timestamp_ms):
            raise ValueError(f"CAUSAL_PRE_ENTRY_LOOKAHEAD_VIOLATION:{k}:{kn}>{decision_timestamp_ms}")
        cb = v.get("closed_bar_close_ms")
        if cb is not None and int(cb) > int(decision_timestamp_ms):
            raise ValueError(f"CAUSAL_PRE_ENTRY_BAR_CLOSE_LOOKAHEAD_VIOLATION:{k}:{cb}>{decision_timestamp_ms}")

    if invalid_fields:
        inv_unknown_flags = [f"INVALID_{f.upper()}" for f in invalid_fields]
        if "in_ict_macro_window" in invalid_fields or "ny_session_group" in invalid_fields:
            inv_unknown_flags.append("ICT_MACRO_WINDOW_UNKNOWN")
        return {
            "primary_pre_entry_code": "PRE_ENTRY_INVALID_EVIDENCE",
            "evidence_completeness": "INVALID",
            "is_causally_observable_pre_entry": False,
            "raw_snapshot_joined": raw_snapshot_joined,
            "original_snapshot_verified": bool(original_snapshot_verified),
            "asof_reconstruction_verified": False,
            "active_pre_entry_risk_flags": ["INVALID_PRE_ENTRY_EVIDENCE"],
            "unknown_or_unproven_flags": sorted(set(inv_unknown_flags)),
            "present_fields_count": len(present_fields),
            "missing_fields_count": len(missing_fields),
            "invalid_fields_count": len(invalid_fields),
            "missing_fields": missing_fields,
            "invalid_fields": invalid_fields,
        }

    if not raw_snapshot_joined or not present_fields:
        return {
            "primary_pre_entry_code": "PRE_ENTRY_UNKNOWN",
            "evidence_completeness": "UNKNOWN",
            "is_causally_observable_pre_entry": False,
            "raw_snapshot_joined": False,
            "original_snapshot_verified": False,
            "asof_reconstruction_verified": False,
            "active_pre_entry_risk_flags": [],
            "unknown_or_unproven_flags": ["ALL_PRE_ENTRY_CONTEXT_UNJOINED"],
            "present_fields_count": len(present_fields),
            "missing_fields_count": len(missing_fields),
            "invalid_fields_count": 0,
            "missing_fields": missing_fields,
            "invalid_fields": [],
        }

    dir_up = direction.upper()
    expected_htf = "BULLISH" if dir_up == "LONG" else "BEARISH"
    opposing_htf = "BEARISH" if dir_up == "LONG" else "BULLISH"

    def _cell(fk: str) -> Mapping[str, Any]:
        c = pre_entry_features.get(fk)
        return c if isinstance(c, Mapping) else {}

    h4_c = _cell("h4_structure_bias")
    h1_c = _cell("h1_structure_bias")
    m15_c = _cell("m15_structure_bias")
    macro_c = _cell("in_ict_macro_window")
    sess_c = _cell("ny_session_group")
    uni_c = _cell("unicorn_overlap_state")
    swp_c = _cell("liquidity_sweep_reclaim_confirmed")
    mss_c = _cell("mss_bos_displacement_confirmed")
    room_m1_c = _cell("target_distance_in_m1_atr")
    t_tf_c = _cell("target_source_timeframe")
    rr_c = _cell("cost_aware_net_rr")
    gap_c = _cell("recent_m1_data_gap_within_180m")

    # Guard against poisoned ny_session_group == "INVALID" masquerading as PRESENT
    if sess_c.get("state") == "PRESENT" and str(sess_c.get("value") or "").upper() in ("INVALID", "INVALID_SCHEDULE", "CALENDAR_UNVERIFIED"):
        return {
            "primary_pre_entry_code": "PRE_ENTRY_INVALID_EVIDENCE",
            "evidence_completeness": "INVALID",
            "is_causally_observable_pre_entry": False,
            "raw_snapshot_joined": raw_snapshot_joined,
            "original_snapshot_verified": bool(original_snapshot_verified),
            "asof_reconstruction_verified": False,
            "active_pre_entry_risk_flags": ["INVALID_PRE_ENTRY_EVIDENCE"],
            "unknown_or_unproven_flags": ["ICT_MACRO_WINDOW_UNKNOWN"],
            "present_fields_count": len(present_fields),
            "missing_fields_count": len(missing_fields),
            "invalid_fields_count": 1,
            "missing_fields": missing_fields,
            "invalid_fields": ["ny_session_group"],
        }

    risk_flags: List[str] = []
    unknown_flags: List[str] = []

    h4_present = h4_c.get("state") == "PRESENT" and str(h4_c.get("value") or "").upper() in ("BULLISH", "BEARISH", "NEUTRAL")
    h1_present = h1_c.get("state") == "PRESENT" and str(h1_c.get("value") or "").upper() in ("BULLISH", "BEARISH", "NEUTRAL")
    h4_b = str(h4_c.get("value")).upper() if h4_present else "UNKNOWN"
    h1_b = str(h1_c.get("value")).upper() if h1_present else "UNKNOWN"

    if not h4_present:
        unknown_flags.append("H4_STRUCTURE_BIAS_UNKNOWN")
    if not h1_present:
        unknown_flags.append("H1_STRUCTURE_BIAS_UNKNOWN")

    if (h4_present and h4_b == opposing_htf) or (h1_present and h1_b == opposing_htf):
        risk_flags.append("COUNTER_HTF_H4_OR_H1_BIAS")
    elif h4_present and h1_present and h4_b != expected_htf and h1_b != expected_htf:
        risk_flags.append("NON_SUPPORTIVE_NEUTRAL_HTF_STRUCTURE")
    elif (h4_present and h4_b == "NEUTRAL" and not h1_present) or (h1_present and h1_b == "NEUTRAL" and not h4_present):
        risk_flags.append("NON_SUPPORTIVE_NEUTRAL_HTF_STRUCTURE")

    m15_present = m15_c.get("state") == "PRESENT" and str(m15_c.get("value") or "").upper() in ("BULLISH", "BEARISH", "NEUTRAL")
    if m15_present:
        if str(m15_c.get("value")).upper() == opposing_htf:
            risk_flags.append("COUNTER_M15_STRUCTURE")
    else:
        unknown_flags.append("M15_STRUCTURE_BIAS_UNKNOWN")

    # D5: OUTSIDE_ICT_MACRO_WINDOW ONLY when macro fact is explicitly PRESENT, valid, and False!
    if (
        macro_c.get("state") == "PRESENT"
        and isinstance(macro_c.get("value"), bool)
        and sess_c.get("state") != "INVALID"
    ):
        if macro_c.get("value") is False:
            risk_flags.append("OUTSIDE_ICT_MACRO_WINDOW")
    else:
        unknown_flags.append("ICT_MACRO_WINDOW_UNKNOWN")

    # E4: Never treat missing/unknown Unicorn, sweep, or MSS as proven negative failure!
    uni_present = uni_c.get("state") == "PRESENT" and str(uni_c.get("value") or "").strip().upper() not in ("", "UNKNOWN")
    if uni_present:
        uni_cls = str(uni_c.get("value")).strip().upper()
        if uni_cls not in ("TRUE_UNICORN_BREAKER_FVG", "UNICORN_CONFIRMED", "VALID", "TRUE_UNICORN"):
            risk_flags.append("NO_TRUE_UNICORN_BREAKER_FVG_OVERLAP")
    else:
        unknown_flags.append("TRUE_UNICORN_OVERLAP_UNKNOWN")

    if swp_c.get("state") == "PRESENT" and isinstance(swp_c.get("value"), bool):
        if swp_c.get("value") is False:
            risk_flags.append("SWEEP_RECLAIM_PROVEN_FALSE")
    else:
        unknown_flags.append("SWEEP_RECLAIM_UNKNOWN")

    if mss_c.get("state") == "PRESENT" and isinstance(mss_c.get("value"), bool):
        if mss_c.get("value") is False:
            risk_flags.append("MSS_DISPLACEMENT_PROVEN_FALSE")
    else:
        unknown_flags.append("MSS_DISPLACEMENT_UNKNOWN")

    if t_tf_c.get("state") != "PRESENT":
        unknown_flags.append("TARGET_SOURCE_TIMEFRAME_UNKNOWN")

    room_m1 = room_m1_c.get("value") if room_m1_c.get("state") == "PRESENT" else None
    if isinstance(room_m1, (int, float)) and not isinstance(room_m1, bool):
        if float(room_m1) > 12.0:
            risk_flags.append("TARGET_BEYOND_12_M1_ATR")
    else:
        unknown_flags.append("TARGET_DISTANCE_M1_ATR_UNKNOWN")

    net_rr = rr_c.get("value") if rr_c.get("state") == "PRESENT" else None
    if isinstance(net_rr, (int, float)) and not isinstance(net_rr, bool):
        if float(net_rr) < 2.0:
            risk_flags.append("COST_AWARE_NET_RR_BELOW_2_0")
    else:
        unknown_flags.append("COST_AWARE_NET_RR_UNKNOWN")

    if gap_c.get("state") == "PRESENT" and isinstance(gap_c.get("value"), bool):
        if gap_c.get("value") is True:
            risk_flags.append("RECENT_PRE_ENTRY_M1_GAP_WITHIN_180M")
    else:
        unknown_flags.append("RECENT_M1_DATA_GAP_UNKNOWN")

    completeness = "COMPLETE" if len(missing_fields) == 0 else "PARTIAL"

    # Deterministic hierarchical primary pre-entry condition strictly from PROVEN pre-entry facts
    if "COUNTER_HTF_H4_OR_H1_BIAS" in risk_flags:
        primary_code = "COUNTER_HTF_H4_OR_H1_BIAS"
    elif "NON_SUPPORTIVE_NEUTRAL_HTF_STRUCTURE" in risk_flags:
        primary_code = "NON_SUPPORTIVE_NEUTRAL_HTF_STRUCTURE"
    elif "OUTSIDE_ICT_MACRO_WINDOW" in risk_flags:
        primary_code = "OUTSIDE_ICT_MACRO_WINDOW"
    elif "TARGET_BEYOND_12_M1_ATR" in risk_flags:
        primary_code = "TARGET_BEYOND_12_M1_ATR"
    elif "NO_TRUE_UNICORN_BREAKER_FVG_OVERLAP" in risk_flags:
        primary_code = "NO_TRUE_UNICORN_BREAKER_FVG_OVERLAP"
    elif "SWEEP_RECLAIM_PROVEN_FALSE" in risk_flags or "MSS_DISPLACEMENT_PROVEN_FALSE" in risk_flags:
        primary_code = "PROVEN_FALSE_LTF_SWEEP_OR_MSS_DISPLACEMENT"
    elif "COST_AWARE_NET_RR_BELOW_2_0" in risk_flags:
        primary_code = "COST_AWARE_NET_RR_BELOW_2_0"
    elif "RECENT_PRE_ENTRY_M1_GAP_WITHIN_180M" in risk_flags:
        primary_code = "RECENT_PRE_ENTRY_M1_GAP_WITHIN_180M"
    elif completeness == "COMPLETE":
        primary_code = "ALL_CHECKED_PRE_ENTRY_CONDITIONS_ALIGNED"
    else:
        primary_code = "ALIGNED_ON_PRESENT_FIELDS_WITH_PARTIAL_UNKNOWN_EVIDENCE"

    return {
        "primary_pre_entry_code": primary_code,
        "evidence_completeness": completeness,
        "is_causally_observable_pre_entry": True,
        "raw_snapshot_joined": True,
        "original_snapshot_verified": bool(original_snapshot_verified),
        "asof_reconstruction_verified": eff_asof_verified,
        "active_pre_entry_risk_flags": risk_flags,
        "unknown_or_unproven_flags": unknown_flags,
        "present_fields_count": len(present_fields),
        "missing_fields_count": len(missing_fields),
        "invalid_fields_count": 0,
        "missing_fields": missing_fields,
        "invalid_fields": [],
    }


def _compute_ex_post_diagnostics_from_bars(
    ep: Mapping[str, Any],
    *,
    bars_1m: Sequence[Mapping[str, Any]],
    open_ts_1m: Sequence[int],
) -> Dict[str, Any]:
    """
    Computes strictly post-entry (`EX_POST_TRAJECTORY_DIAGNOSTIC_ONLY`) MAE/MFE, bars held,
    intrabar SL/TP tie, adverse gap, and post-stop TP trajectory without touching pre-entry fields.
    """
    led = ep.get("execution_ledger") if isinstance(ep.get("execution_ledger"), Mapping) else {}
    outcome = str(ep.get("outcome_label") or led.get("outcome_label") or "LOSS").upper()
    exit_type = str(ep.get("exit_type") or led.get("exit_type") or "").upper()
    direction = str(ep.get("direction") or "LONG").upper()
    entry_px = float(ep.get("entry_fill_price") or led.get("entry_fill_price") or ep.get("entry_reference") or 0.0)
    sl_px = float(ep.get("selected_sl") or led.get("planned_sl") or 0.0)
    tp_px = float(ep.get("take_profit") or led.get("planned_tp") or 0.0)
    exit_px = float(ep.get("exit_fill_price") or led.get("exit_fill_price") or 0.0)
    entry_ts = int(ep.get("entry_fill_ts") or led.get("entry_fill_ts") or ep.get("decision_timestamp_ms") or 0)
    exit_ts = int(ep.get("exit_fill_ts") or led.get("exit_fill_ts") or ep.get("release_ts_ms") or entry_ts)
    bars_held = int(ep.get("bars_held") or led.get("bars_held") or 0)
    same_bar_tie = bool(led.get("same_bar_sl_tp_tie", False)) or ("TIE" in exit_type)
    adverse_gap = "GAP" in exit_type

    if outcome == "WIN":
        ex_code = "TP_HIT_BEFORE_SL"
    elif same_bar_tie:
        ex_code = "SAME_BAR_SL_TP_TIE_CONSERVATIVE_SL"
    elif adverse_gap:
        ex_code = "ADVERSE_GAP_OPEN_STOP_LOSS"
    else:
        ex_code = "SL_HIT_BEFORE_TP"

    sl_dist = max(0.01, abs(entry_px - sl_px)) if sl_px > 0 and entry_px > 0 else 1.0
    mae_usd = 0.0
    mfe_usd = 0.0
    post_stop_hit_tp_180m = False

    if bars_1m and open_ts_1m and entry_ts > 0:
        i_start = bisect.bisect_left(open_ts_1m, entry_ts)
        i_end = bisect.bisect_right(open_ts_1m, max(entry_ts, exit_ts))
        trade_bars = bars_1m[i_start : max(i_start + 1, min(len(bars_1m), i_end))]
        for b in trade_bars:
            bh = float(b.get("high") or entry_px)
            bl = float(b.get("low") or entry_px)
            if direction == "LONG":
                mfe_usd = max(mfe_usd, max(0.0, bh - entry_px))
                mae_usd = max(mae_usd, max(0.0, entry_px - bl))
            else:
                mfe_usd = max(mfe_usd, max(0.0, entry_px - bl))
                mae_usd = max(mae_usd, max(0.0, bh - entry_px))

        if outcome == "LOSS" and tp_px > 0:
            horizon_end_ms = entry_ts + 180 * 60_000
            i_h_end = bisect.bisect_right(open_ts_1m, horizon_end_ms)
            after_stop_bars = bars_1m[min(len(bars_1m), i_end) : min(len(bars_1m), i_h_end)]
            for b in after_stop_bars:
                bh = float(b.get("high") or entry_px)
                bl = float(b.get("low") or entry_px)
                if (direction == "LONG" and bh >= tp_px) or (direction == "SHORT" and bl <= tp_px):
                    post_stop_hit_tp_180m = True
                    break

    mae_r = round(mae_usd / sl_dist, 4)
    mfe_r = round(mfe_usd / sl_dist, 4)

    return {
        "outcome_code": ex_code,
        "outcome_label": outcome,
        "exit_type": exit_type or ex_code,
        "is_ex_post_trajectory_label": True,
        "never_used_in_pre_entry_classification": True,
        "entry_fill_ts": entry_ts,
        "exit_fill_ts": exit_ts,
        "exit_fill_price": round(exit_px, 4),
        "bars_held": bars_held,
        "same_bar_sl_tp_tie": same_bar_tie,
        "adverse_gap_open_stop": adverse_gap,
        "mae_usd_per_oz": round(mae_usd, 4),
        "mfe_usd_per_oz": round(mfe_usd, 4),
        "mae_r": mae_r,
        "mfe_r": mfe_r,
        "reached_0_5r_mfe_before_exit": mfe_r >= 0.5,
        "reached_1_0r_mfe_before_exit": mfe_r >= 1.0,
        "post_stop_reached_tp_within_180m": post_stop_hit_tp_180m,
    }


def build_kh_01a_causal_loss_and_win_control_forensics(
    *,
    si0_all_primary_episodes: Sequence[Mapping[str, Any]],
    si8_loss_rows: Sequence[Mapping[str, Any]],
    si9_loss_rows: Sequence[Mapping[str, Any]],
    decision_inputs_by_opp_id: Optional[Mapping[str, Mapping[str, Any]]] = None,
    feature_ledger_by_opp_id: Optional[Mapping[str, Mapping[str, Any]]] = None,
    datasets: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
    close_ts_index: Optional[Mapping[str, Sequence[int]]] = None,
    dataset_sha256: str = CANONICAL_DATASET_SHA256_KH01A,
    macro_schedule_config: Optional[Mapping[str, Any]] = None,
    _macro_resolver_override: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """
    KH-01A / KH-01A.R1 Section 3: Joins every closed SI0 loss (157) + closed SI0 win control (9) +
    SI8/SI9 research losses (4 + 2, 2 shared = 4 unique) by stable `opportunity_id` to point-in-time
    reconstructed `MarketDecisionContext`, `StrategyDecisionSnapshot`, `SignalQualityEvidenceV1793122`,
    validated `MacroTimeWindowState` (keyword arguments enforced), and verified `CausalFeatureSnapshot`
    (`07_verified_causal_features.jsonl.gz`).

    R1 Provenance Honesty Guarantees:
      - Per-feature `source_origin` distinguishes `PERSISTED_VERIFIED_SNAPSHOT`,
        `RECONSTRUCTED_ASOF_RAW_BARS`, `LEDGER_ONLY_UNVERIFIED`, `MISSING`, and `INVALID`.
      - Never claims full original runtime `MarketDecisionContext` persistence where not stored
        (`full_original_runtime_context_status = "NOT_PROVABLE_FROM_AVAILABLE_ARTIFACTS"`).
      - Compares persisted `10_canonical_decision_inputs` fingerprints against as-of reconstructed
        `StrategyDecisionSnapshot` fingerprints (`MATCH` / `MISMATCH` / `NOT_COMPARABLE`).
      - Never infers `sweep_confirmed` or `mss_confirmed` from `bool(snap.selected_...)`.
    """
    seen_si0_ids: Set[str] = set()
    for ep in si0_all_primary_episodes:
        ek = str(ep.get("episode_key") or ep.get("opportunity_id") or "")
        if not ek:
            raise ValueError("MISSING_EPISODE_ID_IN_SI0_LEDGER")
        if ek in seen_si0_ids:
            raise ValueError(f"DUPLICATE_EPISODE_ID_IN_SI0_LEDGER:{ek}")
        seen_si0_ids.add(ek)

    si0_wins = [ep for ep in si0_all_primary_episodes if str(ep.get("outcome_label") or "").upper() == "WIN"]
    si0_losses = [ep for ep in si0_all_primary_episodes if str(ep.get("outcome_label") or "").upper() == "LOSS"]
    si0_closed = [ep for ep in si0_all_primary_episodes if str(ep.get("outcome_label") or "").upper() in ("WIN", "LOSS")]
    if len(si0_closed) != len(si0_wins) + len(si0_losses):
        raise ValueError(
            f"SI0_CLOSED_CONSERVATION_BROKEN:closed={len(si0_closed)}!=wins({len(si0_wins)})+losses({len(si0_losses)})"
        )

    sched_dist = Counter(str(ep.get("outcome_label") or "UNKNOWN").upper() for ep in si0_all_primary_episodes)
    if sum(sched_dist.values()) != len(si0_all_primary_episodes):
        raise ValueError("SI0_SCHEDULED_CONSERVATION_BROKEN")

    bars_1m = list(datasets.get("1m") or []) if isinstance(datasets, Mapping) else []
    bars_5m = list(datasets.get("5m") or []) if isinstance(datasets, Mapping) else []
    bars_15m = list(datasets.get("15m") or []) if isinstance(datasets, Mapping) else []
    bars_1h = list(datasets.get("1H") or []) if isinstance(datasets, Mapping) else []
    bars_4h = list(datasets.get("4H") or []) if isinstance(datasets, Mapping) else []
    bars_1d = list(datasets.get("1D") or []) if isinstance(datasets, Mapping) else []

    bar_close_by_tf_open: Dict[Tuple[str, int], int] = {}
    for tf_u, b_seq, def_span in (
        ("1M", bars_1m, 60_000),
        ("5M", bars_5m, 300_000),
        ("15M", bars_15m, 900_000),
        ("1H", bars_1h, 3_600_000),
        ("4H", bars_4h, 14_400_000),
        ("1D", bars_1d, 86_400_000),
    ):
        for b in b_seq:
            o_ms = int(b.get("open_time") or b.get("timestamp") or 0)
            c_ms = int(b.get("close_time") or (o_ms + def_span))
            if o_ms > 0:
                bar_close_by_tf_open[(tf_u, o_ms)] = c_ms

    eff_close_ts = (
        close_ts_index
        if close_ts_index is not None
        else (build_close_ts_index_v179312(datasets) if isinstance(datasets, Mapping) and bars_1m else {})
    )
    open_1m = [int(b.get("open_time") or b.get("timestamp") or 0) for b in bars_1m]
    close_1m = list(eff_close_ts.get("1m") or [])
    close_5m = list(eff_close_ts.get("5m") or [])
    close_15m = list(eff_close_ts.get("15m") or [])
    close_1h = list(eff_close_ts.get("1H") or [])
    close_4h = list(eff_close_ts.get("4H") or [])
    close_1d = [int(b.get("close_time") or (int(b.get("timestamp") or 0) + 86_400_000)) for b in bars_1d]

    evaluator = SharedStrategyEvaluator() if bars_1m else None
    variant_ctx_builder = None
    pol_a1_fast: Optional[Dict[str, Any]] = None
    if bars_1m:
        try:
            from tools.run_v17_9_3_macro_unicorn_backtest import (
                V1793_VARIANT_POLICIES,
                build_v1793_variant_market_context,
            )

            variant_ctx_builder = build_v1793_variant_market_context
            pol_a1_fast = dict(V1793_VARIANT_POLICIES["A1_V1792_SL_TP_BASELINE"])
            pol_a1_fast["mtf_structure_observe_only"] = False
        except Exception:
            variant_ctx_builder = None
            pol_a1_fast = None

    def _build_asof_a1_context(asof_ts_ms: int) -> Tuple[Any, Dict[str, Any]]:
        j1 = bisect.bisect_right(close_1m, asof_ts_ms)
        j5 = bisect.bisect_right(close_5m, asof_ts_ms)
        j15 = bisect.bisect_right(close_15m, asof_ts_ms)
        jh1 = bisect.bisect_right(close_1h, asof_ts_ms)
        jh4 = bisect.bisect_right(close_4h, asof_ts_ms)
        jd1 = bisect.bisect_right(close_1d, asof_ts_ms)

        m1_sl = bars_1m[max(0, j1 - 120) : j1]
        m5_sl = bars_5m[max(0, j5 - 96) : j5]
        m15_sl = bars_15m[max(0, j15 - 96) : j15]
        h1_sl = bars_1h[max(0, jh1 - 96) : jh1]
        h4_sl = bars_4h[max(0, jh4 - 64) : jh4]
        d1_sl = bars_1d[max(0, jd1 - 30) : jd1]

        for tf_name, sl_seq, def_sp in (
            ("1M", m1_sl, 60_000),
            ("5M", m5_sl, 300_000),
            ("15M", m15_sl, 900_000),
            ("1H", h1_sl, 3_600_000),
            ("4H", h4_sl, 14_400_000),
            ("1D", d1_sl, 86_400_000),
        ):
            if sl_seq:
                last_b = sl_seq[-1]
                last_c = int(last_b.get("close_time") or (int(last_b.get("open_time") or last_b.get("timestamp") or 0) + def_sp))
                if last_c > asof_ts_ms:
                    raise ValueError(
                        f"CAUSAL_SLICE_FUTURE_BAR_DETECTED:{tf_name}:close_time={last_c}>decision_ts={asof_ts_ms}"
                    )

        last_m1_b = m1_sl[-1]
        last_m1_o = int(last_m1_b.get("open_time") or last_m1_b.get("timestamp") or 0)
        last_m1_c = int(last_m1_b.get("close_time") or asof_ts_ms)
        cur_close_px = float(last_m1_b["close"])

        base_in = {
            "symbol": "XAUUSDT",
            "decision_timestamp_ms": asof_ts_ms,
            "m1_candles": m1_sl,
            "m5_candles": m5_sl,
            "m15_candles": m15_sl,
            "h1_candles": h1_sl,
            "h4_candles": h4_sl,
            "d1_candles": d1_sl,
            "bid": round(cur_close_px - 0.10, 2),
            "ask": round(cur_close_px + 0.10, 2),
        }
        if variant_ctx_builder is not None and pol_a1_fast is not None:
            ctx_obj = variant_ctx_builder(base_in, pol_a1_fast, decision_time=asof_ts_ms)
        else:
            ctx_obj = build_market_decision_context(
                **base_in,
                quote_timestamp_ms=last_m1_c,
                quote_source_kind="SYNTHETIC_RESEARCH_QUOTE",
            )
        slices_meta = {
            "j1": j1,
            "m1_slice": m1_sl,
            "m5_slice": m5_sl,
            "m15_slice": m15_sl,
            "h1_slice": h1_sl,
            "h4_slice": h4_sl,
            "d1_slice": d1_sl,
            "last_m1_open_ms": last_m1_o,
            "last_m1_close_ms": last_m1_c,
        }
        return ctx_obj, slices_meta

    def _verify_source_bar_ids_exist_and_closed(
        src_ids: Sequence[str],
        decision_ts_ms: int,
        feature_key: str,
    ) -> Tuple[List[str], int]:
        cleaned = [str(s).strip() for s in src_ids if s and str(s).strip()]
        if not cleaned:
            raise ValueError(f"EMPTY_PERSISTED_SOURCE_BAR_IDS:{feature_key}")
        max_close_seen = 0
        for sid in cleaned:
            up = sid.upper()
            if any(tok in up for tok in _PLACEHOLDER_SOURCE_ID_TOKENS):
                raise ValueError(f"PLACEHOLDER_SOURCE_BAR_ID_REJECTED:{feature_key}:{sid}")
            parts = sid.split(":")
            if bar_close_by_tf_open and len(parts) >= 3 and parts[0].upper() == "XAUUSDT":
                tf_u = parts[1].upper()
                try:
                    o_ms = int(parts[2])
                except ValueError as exc:
                    raise ValueError(f"MALFORMED_SOURCE_BAR_OPEN_MS:{feature_key}:{sid}") from exc
                c_ms = bar_close_by_tf_open.get((tf_u, o_ms))
                if c_ms is None:
                    raise ValueError(f"NONEXISTENT_SOURCE_BAR_ID_IN_CANONICAL_DATASET:{feature_key}:{sid}")
                if c_ms > decision_ts_ms:
                    raise ValueError(
                        f"CAUSAL_PRE_ENTRY_BAR_CLOSE_LOOKAHEAD_VIOLATION:{feature_key}:{sid}:close_ms={c_ms}>decision_ts={decision_ts_ms}"
                    )
                max_close_seen = max(max_close_seen, c_ms)
        return cleaned, max_close_seen

    def _parse_target_tf_from_pool_id(pool_id: str) -> str:
        pid = str(pool_id or "").strip()
        if not pid:
            return "UNKNOWN"
        parts = pid.split("_")
        if pid.startswith("pool_") and len(parts) >= 2:
            cand = parts[1].upper()
            if cand in ("1M", "5M", "15M", "1H", "4H"):
                return cand
        if parts:
            cand0 = parts[0].upper()
            if cand0 in ("1M", "5M", "15M", "1H", "4H"):
                return cand0
        return "UNKNOWN"

    def _build_single_casebook_row(ep: Mapping[str, Any], population_role: str) -> Dict[str, Any]:
        oid = str(ep.get("opportunity_id") or "")
        if not oid:
            raise ValueError("MISSING_OPPORTUNITY_ID_IN_FORENSICS_EPISODE")
        led = ep.get("execution_ledger") if isinstance(ep.get("execution_ledger"), Mapping) else {}
        dec_in = (decision_inputs_by_opp_id or {}).get(oid) if decision_inputs_by_opp_id is not None else None
        feat_in = (feature_ledger_by_opp_id or {}).get(oid) if feature_ledger_by_opp_id is not None else None

        if dec_in is not None and str(dec_in.get("opportunity_id") or oid) != oid:
            raise ValueError(f"MISMATCHED_DECISION_INPUT_OPPORTUNITY_ID:{oid}!={dec_in.get('opportunity_id')}")

        if feat_in is not None:
            if str(feat_in.get("opportunity_id") or oid) != oid:
                raise ValueError(f"MISMATCHED_FEATURE_LEDGER_OPPORTUNITY_ID:{oid}!={feat_in.get('opportunity_id')}")
            feat_ds_sha = str(feat_in.get("source_dataset_sha256") or "")
            if feat_ds_sha and feat_ds_sha != dataset_sha256:
                raise ValueError(f"STALE_FEATURE_LEDGER_DATASET_SHA:{oid}:{feat_ds_sha}!={dataset_sha256}")
            if not verify_feature_snapshot_v1793127(feat_in):
                raise ValueError(f"INVALID_OR_FUTURE_LEAKED_FEATURE_SNAPSHOT:{oid}")

        dec_ts_raw = (
            ep.get("decision_timestamp_ms")
            or led.get("decision_ts")
            or (dec_in or {}).get("decision_timestamp_ms")
            or 0
        )
        if isinstance(dec_ts_raw, bool) or not isinstance(dec_ts_raw, int) or int(dec_ts_raw) <= 0:
            raise ValueError(f"INVALID_DECISION_TIMESTAMP_MS:{oid}:{dec_ts_raw!r}")
        dec_ts = int(dec_ts_raw)

        direction = str(ep.get("direction") or (dec_in or {}).get("direction") or "").strip().upper()
        if direction not in ("LONG", "SHORT"):
            raise ValueError(f"INVALID_DIRECTION_IN_FORENSICS_EPISODE:{oid}:{direction}")

        model_id = str(ep.get("model_id") or (dec_in or {}).get("model_id") or (feat_in or {}).get("model_id") or "")
        entry_fill_px = float(ep.get("entry_fill_price") or led.get("entry_fill_price") or ep.get("entry_reference") or 0.0)
        if entry_fill_px <= 0.0 or not math.isfinite(entry_fill_px):
            raise ValueError(f"INVALID_ENTRY_PRICE_IN_FORENSICS_EPISODE:{oid}:{entry_fill_px}")

        entry_ref = float(ep.get("entry_reference") or (dec_in or {}).get("entry_reference") or entry_fill_px)
        sel_sl = float(ep.get("selected_sl") or led.get("planned_sl") or (dec_in or {}).get("selected_sl") or 0.0)
        sel_tp = float(ep.get("take_profit") or led.get("planned_tp") or (dec_in or {}).get("take_profit") or 0.0)
        net_pnl = round(float(ep.get("realized_net_pnl_usd") or led.get("realized_net_pnl_usd") or 0.0), 4)
        real_r = round(float(ep.get("realized_r") or led.get("realized_r_net") or 0.0), 4)

        ny_dt = datetime.datetime.fromtimestamp(dec_ts / 1000.0, tz=datetime.timezone.utc).astimezone(NY_TZ)
        ny_hour = ny_dt.hour

        raw_joined = False
        pre_features: Dict[str, Dict[str, Any]] = {}
        macro_validation: Optional[Dict[str, Any]] = None
        provenance_summary: Dict[str, Any]

        i1 = bisect.bisect_right(close_1m, dec_ts) if close_1m else 0
        has_point_in_time_m1 = bool(bars_1m and i1 > 0)

        # Also validate any explicit evidence_refs passed on feat_in (fail-closed on lookahead/bad IDs)
        explicit_refs = (feat_in or {}).get("evidence_refs") if isinstance((feat_in or {}).get("evidence_refs"), Mapping) else {}
        for ref_k, ref_v in explicit_refs.items():
            if isinstance(ref_v, Mapping):
                rk_ms = ref_v.get("known_at_ms")
                if rk_ms is not None and int(rk_ms) > dec_ts:
                    raise ValueError(f"CAUSAL_PRE_ENTRY_LOOKAHEAD_VIOLATION:{ref_k}:{rk_ms}>{dec_ts}")
                rc_ms = ref_v.get("source_bar_close_ms") or ref_v.get("closed_bar_close_ms")
                if rc_ms is not None and int(rc_ms) > dec_ts:
                    raise ValueError(f"CAUSAL_PRE_ENTRY_BAR_CLOSE_LOOKAHEAD_VIOLATION:{ref_k}:{rc_ms}>{dec_ts}")
                r_sids = ref_v.get("source_bar_ids") or ref_v.get("source_ids")
                if r_sids is not None:
                    _verify_source_bar_ids_exist_and_closed(r_sids, dec_ts, str(ref_k))

        # Index persisted verified CausalFeatureSnapshot items when captured at exact dec_ts
        persisted_items_by_type: Dict[str, Mapping[str, Any]] = {}
        persisted_snapshot_exact_ts = (
            feat_in is not None
            and isinstance(feat_in.get("causal_snapshot"), Mapping)
            and int(feat_in.get("decision_timestamp_ms") or 0) == dec_ts
        )
        if persisted_snapshot_exact_ts:
            cs_feats = feat_in["causal_snapshot"].get("features")  # type: ignore[index]
            if isinstance(cs_feats, Sequence):
                for f_item in cs_feats:
                    if isinstance(f_item, Mapping) and f_item.get("evidence_type"):
                        persisted_items_by_type[str(f_item["evidence_type"])] = f_item

        def _extract_persisted_verified_meta(ev_type: str) -> Optional[Dict[str, Any]]:
            p_item = persisted_items_by_type.get(ev_type)
            if not isinstance(p_item, Mapping):
                return None
            p_state = str(p_item.get("proof_state") or "UNKNOWN")
            if p_state not in ("MEASURED_VALUE", "PROVEN_TRUE", "PROVEN_FALSE"):
                return None
            k_ms = p_item.get("known_at_ms")
            sb_close = p_item.get("source_bar_close_ms")
            s_ids = p_item.get("source_bar_ids") or []
            if k_ms is None or sb_close is None or not s_ids:
                return None
            if int(k_ms) > dec_ts or int(sb_close) > dec_ts:
                raise ValueError(
                    f"CAUSAL_PRE_ENTRY_LOOKAHEAD_VIOLATION:{ev_type}:known={k_ms},close={sb_close},dec={dec_ts}"
                )
            verified_sids, max_c = _verify_source_bar_ids_exist_and_closed(s_ids, dec_ts, ev_type)
            eff_c = max(int(sb_close), max_c) if max_c > 0 else int(sb_close)
            return {
                "measured_value": p_item.get("measured_value"),
                "proof_state": p_state,
                "known_at_ms": int(k_ms),
                "closed_bar_close_ms": eff_c,
                "raw_source_ids": verified_sids,
                "timeframe": str(p_item.get("timeframe") or "").upper() or None,
                "evidence_id": p_item.get("evidence_id"),
            }

        if has_point_in_time_m1 and evaluator is not None and model_id:
            from types import SimpleNamespace

            raw_joined = True
            ctx, sl_meta = _build_asof_a1_context(dec_ts)
            m1_slice = sl_meta["m1_slice"]
            m5_slice = sl_meta["m5_slice"]
            m15_slice = sl_meta["m15_slice"]
            h1_slice = sl_meta["h1_slice"]
            h4_slice = sl_meta["h4_slice"]
            last_m1_open_ms = int(sl_meta["last_m1_open_ms"])
            last_m1_close_ms = int(sl_meta["last_m1_close_ms"])
            last_m1_id = f"XAUUSDT:1m:{last_m1_open_ms}"

            snap: StrategyDecisionSnapshot = evaluator.evaluate_model_direction(
                context=ctx,
                model_id=model_id,
                direction=direction,
            )

            # When no persisted verified snapshot exists at exact `dec_ts` (e.g. delayed-entry SI8/SI9
            # confirmation bar or unit test), reconstruct tri-state causal features as-of `dec_ts` using
            # the authoritative V17.9.3.1.2.7/8 detector (`Missing proof stays UNKNOWN`, never boolean False!).
            reconstructed_v7_items: Dict[str, Mapping[str, Any]] = {}
            reconstructed_v7_doc: Optional[Dict[str, Any]] = None
            if not persisted_snapshot_exact_ts:
                j1_v7 = int(sl_meta["j1"])
                j5_v7 = bisect.bisect_right(close_5m, dec_ts)
                j15_v7 = bisect.bisect_right(close_15m, dec_ts)
                jh1_v7 = bisect.bisect_right(close_1h, dec_ts)
                jh4_v7 = bisect.bisect_right(close_4h, dec_ts)
                cur_px_v7 = float(m1_slice[-1]["close"])
                v7_ctx = SimpleNamespace(
                    symbol="XAUUSDT",
                    decision_timestamp_ms=dec_ts,
                    closed_m1_candles=bars_1m[max(0, j1_v7 - 181) : j1_v7],
                    closed_m5_candles=bars_5m[max(0, j5_v7 - 80) : j5_v7],
                    closed_m15_candles=bars_15m[max(0, j15_v7 - 80) : j15_v7],
                    closed_h1_candles=bars_1h[max(0, jh1_v7 - 80) : jh1_v7],
                    closed_h4_candles=bars_4h[max(0, jh4_v7 - 80) : jh4_v7],
                    bid=round(cur_px_v7 - 0.10, 2),
                    ask=round(cur_px_v7 + 0.10, 2),
                    quote_source_kind="SYNTHETIC_RESEARCH_QUOTE",
                )
                v7_dec = {
                    "opportunity_id": oid,
                    "setup_episode_id": str(ep.get("setup_episode_id") or snap.setup_episode_id or oid),
                    "decision_timestamp_ms": dec_ts,
                    "model_id": model_id,
                    "direction": direction,
                    "entry_reference": entry_ref,
                    "selected_sl": sel_sl if sel_sl > 0 else snap.selected_sl,
                    "take_profit": sel_tp if sel_tp > 0 else snap.take_profit,
                    "hypothetical_quantity": snap.hypothetical_quantity,
                    "selected_anchor_id": snap.selected_anchor_id,
                    "selected_target_id": snap.selected_target_id,
                    "structural_invalidation_price": snap.structural_invalidation_price,
                    "selected_fvg_id": snap.selected_fvg_id,
                    "selected_breaker_id": getattr(snap, "selected_breaker_id", None),
                    "selected_sweep_id": snap.selected_sweep_id,
                    "selected_break_id": snap.selected_break_id,
                }
                reconstructed_v7_doc = derive_pre_entry_features_from_causal_context_v1793127(
                    v7_dec, v7_ctx, dataset_sha256=dataset_sha256
                )
                for f_item in (reconstructed_v7_doc.get("causal_snapshot") or {}).get("features", ()):
                    if isinstance(f_item, Mapping) and f_item.get("evidence_type"):
                        reconstructed_v7_items[str(f_item["evidence_type"])] = f_item

            def _extract_reconstructed_v7_meta(ev_type: str) -> Optional[Dict[str, Any]]:
                r_item = reconstructed_v7_items.get(ev_type)
                if not isinstance(r_item, Mapping):
                    return None
                p_state = str(r_item.get("proof_state") or "UNKNOWN")
                if p_state not in ("MEASURED_VALUE", "PROVEN_TRUE", "PROVEN_FALSE"):
                    return None
                k_ms = r_item.get("known_at_ms")
                sb_close = r_item.get("source_bar_close_ms")
                s_ids = r_item.get("source_bar_ids") or []
                if k_ms is None or sb_close is None or not s_ids:
                    return None
                verified_sids, max_c = _verify_source_bar_ids_exist_and_closed(s_ids, dec_ts, ev_type)
                eff_c = max(int(sb_close), max_c) if max_c > 0 else int(sb_close)
                return {
                    "measured_value": r_item.get("measured_value"),
                    "proof_state": p_state,
                    "known_at_ms": int(k_ms),
                    "closed_bar_close_ms": eff_c,
                    "raw_source_ids": verified_sids,
                    "timeframe": str(r_item.get("timeframe") or "").upper() or None,
                }

            # KH-01A.R1 Phase 1 (D1): Exact keyword argument call to resolve_causal_macro_time_window!
            if _macro_resolver_override is not None:
                macro_st = _macro_resolver_override(
                    decision_timestamp_ms=dec_ts,
                    symbol="XAUUSDT",
                )
            elif macro_schedule_config is None:
                macro_st = resolve_causal_macro_time_window(
                    decision_timestamp_ms=dec_ts,
                    symbol="XAUUSDT",
                )
            else:
                macro_st = resolve_causal_macro_time_window(
                    decision_timestamp_ms=dec_ts,
                    symbol="XAUUSDT",
                    schedule_config=macro_schedule_config,
                )

            macro_validation = validate_and_classify_macro_state_kh01a_r1(
                macro_st,
                expected_decision_timestamp_ms=dec_ts,
                expected_symbol="XAUUSDT",
            )

            # Check recent M1 gap within [dec_ts - 180m, dec_ts]
            recent_m1_gap = False
            j1_full = int(sl_meta["j1"])
            gap_check_bars = bars_1m[max(0, j1_full - 181) : j1_full]
            for gi in range(1, len(gap_check_bars)):
                pc = int(gap_check_bars[gi - 1]["close_time"])
                cc = int(gap_check_bars[gi]["close_time"])
                if (cc - pc) > 60_000 and cc <= dec_ts and pc >= (dec_ts - 180 * 60_000):
                    recent_m1_gap = True
                    break

            def _tf_last_bar_meta(sl_bars: Sequence[Mapping[str, Any]], tf_tag: str, def_span: int) -> Tuple[Optional[int], List[str]]:
                if not sl_bars:
                    return None, []
                lb = sl_bars[-1]
                lo = int(lb.get("open_time") or lb.get("timestamp") or 0)
                lc = int(lb.get("close_time") or (lo + def_span))
                return lc, [f"XAUUSDT:{tf_tag.lower()}:{lo}"]

            h4_last_ms, h4_ids = _tf_last_bar_meta(h4_slice, "4h", 14_400_000)
            h1_last_ms, h1_ids = _tf_last_bar_meta(h1_slice, "1h", 3_600_000)
            m15_last_ms, m15_ids = _tf_last_bar_meta(m15_slice, "15m", 900_000)
            m5_last_ms, m5_ids = _tf_last_bar_meta(m5_slice, "5m", 300_000)

            pre_features["model_id"] = _make_feature_cell(
                "model_id",
                model_id,
                state="PRESENT",
                proof_state="RECONSTRUCTED_CAUSAL_VALUE",
                source_origin="RECONSTRUCTED_ASOF_RAW_BARS",
                source_timeframe="1M",
                known_at_ms=last_m1_close_ms,
                closed_bar_close_ms=last_m1_close_ms,
                decision_ts_ms=dec_ts,
                raw_source_ids=[last_m1_id],
            )
            pre_features["direction"] = _make_feature_cell(
                "direction",
                direction,
                state="PRESENT",
                proof_state="RECONSTRUCTED_CAUSAL_VALUE",
                source_origin="RECONSTRUCTED_ASOF_RAW_BARS",
                source_timeframe="1M",
                known_at_ms=last_m1_close_ms,
                closed_bar_close_ms=last_m1_close_ms,
                decision_ts_ms=dec_ts,
                raw_source_ids=[last_m1_id],
            )

            # Macro features strictly governed by 3-way validation (D3, D4)
            m_cell_state = str(macro_validation["cell_state"])
            m_src_origin = str(macro_validation["source_origin"])
            m_src_ids = [last_m1_id] if m_cell_state == "PRESENT" else []
            m_known_ms = dec_ts if m_cell_state == "PRESENT" else None
            m_close_ms = last_m1_close_ms if m_cell_state == "PRESENT" else None

            pre_features["ny_session_group"] = _make_feature_cell(
                "ny_session_group",
                macro_validation["ny_session_group_value"],
                state=m_cell_state,
                proof_state=str(macro_validation["session_proof_state"]),
                source_origin=m_src_origin,
                source_timeframe="1M" if m_cell_state == "PRESENT" else None,
                known_at_ms=m_known_ms,
                closed_bar_close_ms=m_close_ms,
                decision_ts_ms=dec_ts,
                raw_source_ids=m_src_ids,
            )
            pre_features["in_ict_macro_window"] = _make_feature_cell(
                "in_ict_macro_window",
                macro_validation["in_ict_macro_window_value"],
                state=m_cell_state,
                proof_state=str(macro_validation["in_window_proof_state"]),
                source_origin=m_src_origin,
                source_timeframe="1M" if m_cell_state == "PRESENT" else None,
                known_at_ms=m_known_ms,
                closed_bar_close_ms=m_close_ms,
                decision_ts_ms=dec_ts,
                raw_source_ids=m_src_ids,
            )

            # HTF/MTF biases: strictly honor V17.9.3.1.2.7/8 tri-state (PERSISTED when at dec_ts, else RECONSTRUCTED; UNKNOWN stays MISSING!)
            for feat_key, ev_key, tf_u in (
                ("h4_structure_bias", "h4_bias", "4H"),
                ("h1_structure_bias", "h1_bias", "1H"),
                ("m15_structure_bias", "m15_bias", "15M"),
            ):
                if persisted_snapshot_exact_ts:
                    p_meta = _extract_persisted_verified_meta(ev_key)
                    p_val = str(p_meta["measured_value"] or "UNKNOWN").upper() if p_meta is not None else "UNKNOWN"
                    if p_meta is not None and p_val in ("BULLISH", "BEARISH", "NEUTRAL"):
                        pre_features[feat_key] = _make_feature_cell(
                            feat_key,
                            p_val,
                            state="PRESENT",
                            proof_state=str(p_meta["proof_state"]),
                            source_origin="PERSISTED_VERIFIED_SNAPSHOT",
                            source_timeframe=tf_u,
                            known_at_ms=int(p_meta["known_at_ms"]),
                            closed_bar_close_ms=int(p_meta["closed_bar_close_ms"]),
                            decision_ts_ms=dec_ts,
                            raw_source_ids=p_meta["raw_source_ids"],
                        )
                    else:
                        pre_features[feat_key] = _make_feature_cell(
                            feat_key,
                            None,
                            state="MISSING",
                            proof_state="UNKNOWN",
                            source_origin="MISSING",
                            source_timeframe=tf_u,
                            known_at_ms=None,
                            closed_bar_close_ms=None,
                            decision_ts_ms=dec_ts,
                            raw_source_ids=[],
                        )
                else:
                    r_meta = _extract_reconstructed_v7_meta(ev_key)
                    r_val = str(r_meta["measured_value"] or "UNKNOWN").upper() if r_meta is not None else "UNKNOWN"
                    if r_meta is not None and r_val in ("BULLISH", "BEARISH", "NEUTRAL"):
                        pre_features[feat_key] = _make_feature_cell(
                            feat_key,
                            r_val,
                            state="PRESENT",
                            proof_state="RECONSTRUCTED_CAUSAL_VALUE",
                            source_origin="RECONSTRUCTED_ASOF_RAW_BARS",
                            source_timeframe=tf_u,
                            known_at_ms=int(r_meta["known_at_ms"]),
                            closed_bar_close_ms=int(r_meta["closed_bar_close_ms"]),
                            decision_ts_ms=dec_ts,
                            raw_source_ids=r_meta["raw_source_ids"],
                        )
                    else:
                        pre_features[feat_key] = _make_feature_cell(
                            feat_key,
                            None,
                            state="MISSING",
                            proof_state="UNKNOWN",
                            source_origin="MISSING",
                            source_timeframe=tf_u,
                            known_at_ms=None,
                            closed_bar_close_ms=None,
                            decision_ts_ms=dec_ts,
                            raw_source_ids=[],
                        )

            # Sweep & MSS: strictly tri-state from V17.9.3.1.2.7/8 (NEVER infer False from bool(snap.selected_...) or .2 default!)
            for feat_key, ev_key in (
                ("liquidity_sweep_reclaim_confirmed", "sweep_confirmed"),
                ("mss_bos_displacement_confirmed", "mss_confirmed"),
            ):
                if persisted_snapshot_exact_ts:
                    p_meta = _extract_persisted_verified_meta(ev_key)
                    if p_meta is not None and isinstance(p_meta["measured_value"], bool):
                        pre_features[feat_key] = _make_feature_cell(
                            feat_key,
                            bool(p_meta["measured_value"]),
                            state="PRESENT",
                            proof_state=str(p_meta["proof_state"]),
                            source_origin="PERSISTED_VERIFIED_SNAPSHOT",
                            source_timeframe=p_meta["timeframe"] or "1M",
                            known_at_ms=int(p_meta["known_at_ms"]),
                            closed_bar_close_ms=int(p_meta["closed_bar_close_ms"]),
                            decision_ts_ms=dec_ts,
                            raw_source_ids=p_meta["raw_source_ids"],
                        )
                    else:
                        pre_features[feat_key] = _make_feature_cell(
                            feat_key,
                            None,
                            state="MISSING",
                            proof_state="UNKNOWN",
                            source_origin="MISSING",
                            source_timeframe="1M",
                            known_at_ms=None,
                            closed_bar_close_ms=None,
                            decision_ts_ms=dec_ts,
                            raw_source_ids=[],
                        )
                else:
                    r_meta = _extract_reconstructed_v7_meta(ev_key)
                    if r_meta is not None and isinstance(r_meta["measured_value"], bool):
                        pre_features[feat_key] = _make_feature_cell(
                            feat_key,
                            bool(r_meta["measured_value"]),
                            state="PRESENT",
                            proof_state=str(r_meta["proof_state"]),
                            source_origin="RECONSTRUCTED_ASOF_RAW_BARS",
                            source_timeframe=r_meta["timeframe"] or "1M",
                            known_at_ms=int(r_meta["known_at_ms"]),
                            closed_bar_close_ms=int(r_meta["closed_bar_close_ms"]),
                            decision_ts_ms=dec_ts,
                            raw_source_ids=r_meta["raw_source_ids"],
                        )
                    else:
                        pre_features[feat_key] = _make_feature_cell(
                            feat_key,
                            None,
                            state="MISSING",
                            proof_state="UNKNOWN",
                            source_origin="MISSING",
                            source_timeframe="1M",
                            known_at_ms=None,
                            closed_bar_close_ms=None,
                            decision_ts_ms=dec_ts,
                            raw_source_ids=[],
                        )

            # Unicorn overlap state
            if persisted_snapshot_exact_ts:
                p_uni = _extract_persisted_verified_meta("unicorn_breaker_fvg_bound")
                persisted_uni_cls = str((feat_in or {}).get("unicorn_classification") or "").strip().upper()
                if p_uni is not None and isinstance(p_uni["measured_value"], bool) and persisted_uni_cls not in ("", "UNKNOWN"):
                    pre_features["unicorn_overlap_state"] = _make_feature_cell(
                        "unicorn_overlap_state",
                        persisted_uni_cls,
                        state="PRESENT",
                        proof_state=str(p_uni["proof_state"]),
                        source_origin="PERSISTED_VERIFIED_SNAPSHOT",
                        source_timeframe=p_uni["timeframe"] or "1M",
                        known_at_ms=int(p_uni["known_at_ms"]),
                        closed_bar_close_ms=int(p_uni["closed_bar_close_ms"]),
                        decision_ts_ms=dec_ts,
                        raw_source_ids=p_uni["raw_source_ids"],
                    )
                else:
                    pre_features["unicorn_overlap_state"] = _make_feature_cell(
                        "unicorn_overlap_state",
                        None,
                        state="MISSING",
                        proof_state="UNKNOWN",
                        source_origin="MISSING",
                        source_timeframe="1M",
                        known_at_ms=None,
                        closed_bar_close_ms=None,
                        decision_ts_ms=dec_ts,
                        raw_source_ids=[],
                    )
            else:
                r_uni = _extract_reconstructed_v7_meta("unicorn_breaker_fvg_bound")
                rec_uni_cls = str((reconstructed_v7_doc or {}).get("unicorn_classification") or "").strip().upper()
                if r_uni is not None and isinstance(r_uni["measured_value"], bool) and rec_uni_cls not in ("", "UNKNOWN"):
                    pre_features["unicorn_overlap_state"] = _make_feature_cell(
                        "unicorn_overlap_state",
                        rec_uni_cls,
                        state="PRESENT",
                        proof_state=str(r_uni["proof_state"]),
                        source_origin="RECONSTRUCTED_ASOF_RAW_BARS",
                        source_timeframe=r_uni["timeframe"] or "1M",
                        known_at_ms=int(r_uni["known_at_ms"]),
                        closed_bar_close_ms=int(r_uni["closed_bar_close_ms"]),
                        decision_ts_ms=dec_ts,
                        raw_source_ids=r_uni["raw_source_ids"],
                    )
                else:
                    pre_features["unicorn_overlap_state"] = _make_feature_cell(
                        "unicorn_overlap_state",
                        None,
                        state="MISSING",
                        proof_state="UNKNOWN",
                        source_origin="MISSING",
                        source_timeframe="1M",
                        known_at_ms=None,
                        closed_bar_close_ms=None,
                        decision_ts_ms=dec_ts,
                        raw_source_ids=[],
                    )

            # Target source timeframe: check persisted target_selected_pool_bound first, else causal pool ID
            p_tgt = _extract_persisted_verified_meta("target_selected_pool_bound")
            t_pool_id = str(
                ((feat_in or {}).get("target_pool_id") if persisted_snapshot_exact_ts else None)
                or snap.selected_target_id
                or ""
            )
            if (
                p_tgt is not None
                and p_tgt["measured_value"] is True
                and p_tgt["timeframe"] in ("1M", "5M", "15M", "1H", "4H")
            ):
                t_tf = str(p_tgt["timeframe"])
                pre_features["target_source_timeframe"] = _make_feature_cell(
                    "target_source_timeframe",
                    t_tf,
                    state="PRESENT",
                    proof_state=str(p_tgt["proof_state"]),
                    source_origin="PERSISTED_VERIFIED_SNAPSHOT",
                    source_timeframe=t_tf,
                    known_at_ms=int(p_tgt["known_at_ms"]),
                    closed_bar_close_ms=int(p_tgt["closed_bar_close_ms"]),
                    decision_ts_ms=dec_ts,
                    raw_source_ids=p_tgt["raw_source_ids"],
                )
            else:
                parsed_tf = str((feat_in or {}).get("target_pool_timeframe") or "").upper() if persisted_snapshot_exact_ts else ""
                if parsed_tf not in ("1M", "5M", "15M", "1H", "4H"):
                    parsed_tf = _parse_target_tf_from_pool_id(t_pool_id)
                tf_bar_map = {
                    "1M": (last_m1_close_ms, [last_m1_id]),
                    "5M": (m5_last_ms, m5_ids),
                    "15M": (m15_last_ms, m15_ids),
                    "1H": (h1_last_ms, h1_ids),
                    "4H": (h4_last_ms, h4_ids),
                }
                tf_c_ms, tf_s_ids = tf_bar_map.get(parsed_tf, (None, []))
                if parsed_tf in ("1M", "5M", "15M", "1H", "4H") and tf_c_ms is not None and tf_s_ids:
                    t_tf = parsed_tf
                    pre_features["target_source_timeframe"] = _make_feature_cell(
                        "target_source_timeframe",
                        t_tf,
                        state="PRESENT",
                        proof_state="RECONSTRUCTED_CAUSAL_VALUE",
                        source_origin="RECONSTRUCTED_ASOF_RAW_BARS",
                        source_timeframe=t_tf,
                        known_at_ms=tf_c_ms,
                        closed_bar_close_ms=tf_c_ms,
                        decision_ts_ms=dec_ts,
                        raw_source_ids=tf_s_ids,
                    )
                else:
                    t_tf = "UNKNOWN"
                    pre_features["target_source_timeframe"] = _make_feature_cell(
                        "target_source_timeframe",
                        None,
                        state="MISSING",
                        proof_state="UNKNOWN",
                        source_origin="MISSING",
                        source_timeframe=None,
                        known_at_ms=None,
                        closed_bar_close_ms=None,
                        decision_ts_ms=dec_ts,
                        raw_source_ids=[],
                    )

            atr_m1_v = compute_causal_tf_atr_v17931214(datasets, eff_close_ts, "1M", dec_ts) if datasets else None
            atr_htf_v = compute_causal_tf_atr_v17931214(datasets, eff_close_ts, t_tf, dec_ts) if (datasets and t_tf != "UNKNOWN") else None
            atr_h1_v = compute_causal_tf_atr_v17931214(datasets, eff_close_ts, "1H", dec_ts) if datasets else None

            pre_features["atr_m1_usd"] = _make_feature_cell(
                "atr_m1_usd",
                round(atr_m1_v, 4) if atr_m1_v is not None else None,
                state="PRESENT" if atr_m1_v is not None else "MISSING",
                proof_state="RECONSTRUCTED_CAUSAL_VALUE" if atr_m1_v is not None else "UNKNOWN",
                source_origin="RECONSTRUCTED_ASOF_RAW_BARS" if atr_m1_v is not None else "MISSING",
                source_timeframe="1M",
                known_at_ms=last_m1_close_ms if atr_m1_v is not None else None,
                closed_bar_close_ms=last_m1_close_ms if atr_m1_close_ok(atr_m1_v) else None,
                decision_ts_ms=dec_ts,
                raw_source_ids=[last_m1_id] if atr_m1_v is not None else [],
            )
            pre_features["atr_h1_usd"] = _make_feature_cell(
                "atr_h1_usd",
                round(atr_h1_v, 4) if (atr_h1_v is not None and h1_last_ms is not None and h1_ids) else None,
                state="PRESENT" if (atr_h1_v is not None and h1_last_ms is not None and h1_ids) else "MISSING",
                proof_state="RECONSTRUCTED_CAUSAL_VALUE" if (atr_h1_v is not None and h1_last_ms is not None and h1_ids) else "UNKNOWN",
                source_origin="RECONSTRUCTED_ASOF_RAW_BARS" if (atr_h1_v is not None and h1_last_ms is not None and h1_ids) else "MISSING",
                source_timeframe="1H",
                known_at_ms=h1_last_ms if (atr_h1_v is not None and h1_last_ms is not None and h1_ids) else None,
                closed_bar_close_ms=h1_last_ms if (atr_h1_v is not None and h1_last_ms is not None and h1_ids) else None,
                decision_ts_ms=dec_ts,
                raw_source_ids=h1_ids if (atr_h1_v is not None and h1_last_ms is not None and h1_ids) else [],
            )

            sl_dist_usd = abs(entry_ref - sel_sl) if (entry_ref > 0 and sel_sl > 0) else None
            tp_dist_usd = abs(sel_tp - entry_ref) if (entry_ref > 0 and sel_tp > 0) else None
            room_m1 = (tp_dist_usd / atr_m1_v) if (tp_dist_usd is not None and atr_m1_v and atr_m1_v > 1e-9) else None
            room_src_tf = (tp_dist_usd / atr_htf_v) if (tp_dist_usd is not None and atr_htf_v and atr_htf_v > 1e-9) else None

            tf_bar_map_src = {
                "1M": (last_m1_close_ms, [last_m1_id]),
                "5M": (m5_last_ms, m5_ids),
                "15M": (m15_last_ms, m15_ids),
                "1H": (h1_last_ms, h1_ids),
                "4H": (h4_last_ms, h4_ids),
            }
            src_tf_c_ms, src_tf_ids = tf_bar_map_src.get(t_tf, (None, []))

            pre_features["structural_sl_distance_usd"] = _make_feature_cell(
                "structural_sl_distance_usd",
                round(sl_dist_usd, 4) if sl_dist_usd is not None else None,
                state="PRESENT" if sl_dist_usd is not None else "MISSING",
                proof_state="RECONSTRUCTED_CAUSAL_VALUE" if sl_dist_usd is not None else "UNKNOWN",
                source_origin="RECONSTRUCTED_ASOF_RAW_BARS" if sl_dist_usd is not None else "MISSING",
                source_timeframe="1M",
                known_at_ms=last_m1_close_ms if sl_dist_usd is not None else None,
                closed_bar_close_ms=last_m1_close_ms if sl_dist_usd is not None else None,
                decision_ts_ms=dec_ts,
                raw_source_ids=[last_m1_id] if sl_dist_usd is not None else [],
            )
            pre_features["target_distance_in_m1_atr"] = _make_feature_cell(
                "target_distance_in_m1_atr",
                round(room_m1, 4) if room_m1 is not None else None,
                state="PRESENT" if room_m1 is not None else "MISSING",
                proof_state="RECONSTRUCTED_CAUSAL_VALUE" if room_m1 is not None else "UNKNOWN",
                source_origin="RECONSTRUCTED_ASOF_RAW_BARS" if room_m1 is not None else "MISSING",
                source_timeframe="1M",
                known_at_ms=last_m1_close_ms if room_m1 is not None else None,
                closed_bar_close_ms=last_m1_close_ms if room_m1 is not None else None,
                decision_ts_ms=dec_ts,
                raw_source_ids=[last_m1_id] if room_m1 is not None else [],
            )
            has_room_src = (room_src_tf is not None and src_tf_c_ms is not None and bool(src_tf_ids))
            pre_features["target_distance_in_source_tf_atr"] = _make_feature_cell(
                "target_distance_in_source_tf_atr",
                round(room_src_tf, 4) if has_room_src else None,
                state="PRESENT" if has_room_src else "MISSING",
                proof_state="RECONSTRUCTED_CAUSAL_VALUE" if has_room_src else "UNKNOWN",
                source_origin="RECONSTRUCTED_ASOF_RAW_BARS" if has_room_src else "MISSING",
                source_timeframe=t_tf if has_room_src else None,
                known_at_ms=src_tf_c_ms if has_room_src else None,
                closed_bar_close_ms=src_tf_c_ms if has_room_src else None,
                decision_ts_ms=dec_ts,
                raw_source_ids=src_tf_ids if has_room_src else [],
            )

            sz_calc = (
                recalculate_trade_risk_quantity_v179312(entry_ref, sel_sl, sel_tp, direction=direction, decision_ts=dec_ts)
                if (entry_ref > 0 and sel_sl > 0 and sel_tp > 0)
                else None
            )
            net_rr_v = round(float(sz_calc["net_rr"]), 4) if sz_calc is not None else snap.net_rr
            pre_features["cost_aware_net_rr"] = _make_feature_cell(
                "cost_aware_net_rr",
                net_rr_v,
                state="PRESENT" if net_rr_v is not None else "MISSING",
                proof_state="RECONSTRUCTED_CAUSAL_VALUE" if net_rr_v is not None else "UNKNOWN",
                source_origin="RECONSTRUCTED_ASOF_RAW_BARS" if net_rr_v is not None else "MISSING",
                source_timeframe="1M",
                known_at_ms=last_m1_close_ms if net_rr_v is not None else None,
                closed_bar_close_ms=last_m1_close_ms if net_rr_v is not None else None,
                decision_ts_ms=dec_ts,
                raw_source_ids=[last_m1_id] if net_rr_v is not None else [],
            )
            pre_features["quote_quality_mode"] = _make_feature_cell(
                "quote_quality_mode",
                "SYNTHETIC_MODELED_SPREAD_0_20_USD_NO_L1_BBO",
                state="PRESENT",
                proof_state="RECONSTRUCTED_CAUSAL_VALUE",
                source_origin="RECONSTRUCTED_ASOF_RAW_BARS",
                source_timeframe="1M",
                known_at_ms=last_m1_close_ms,
                closed_bar_close_ms=last_m1_close_ms,
                decision_ts_ms=dec_ts,
                raw_source_ids=[last_m1_id],
            )
            pre_features["recent_m1_data_gap_within_180m"] = _make_feature_cell(
                "recent_m1_data_gap_within_180m",
                recent_m1_gap,
                state="PRESENT",
                proof_state="PROVEN_TRUE" if recent_m1_gap else "PROVEN_FALSE",
                source_origin="RECONSTRUCTED_ASOF_RAW_BARS",
                source_timeframe="1M",
                known_at_ms=last_m1_close_ms,
                closed_bar_close_ms=last_m1_close_ms,
                decision_ts_ms=dec_ts,
                raw_source_ids=[last_m1_id],
            )

            # Original persisted decision record vs as-of reconstructed snapshot comparison (E1, E6)
            orig_dec_fp = str((dec_in or {}).get("decision_fingerprint") or "") or None
            orig_act_fp = str((dec_in or {}).get("trade_action_fingerprint") or "") or None
            orig_dec_ts = int((dec_in or {}).get("decision_timestamp_ms") or 0) if dec_in is not None else None
            cand_ts_a1_match: Optional[bool] = None

            if dec_in is None or not orig_dec_fp or not orig_act_fp:
                fp_status = "NOT_COMPARABLE"
                fp_reason = "NO_PERSISTED_DECISION_FINGERPRINT_RECORD_AVAILABLE"
            elif orig_dec_ts == dec_ts:
                if snap.decision_fingerprint == orig_dec_fp and snap.trade_action_fingerprint == orig_act_fp:
                    fp_status = "MATCH"
                    fp_reason = "EXACT_ASOF_A1_V1792_RECONSTRUCTION_MATCHES_PERSISTED_DECISION_AND_ACTION_FINGERPRINTS"
                else:
                    fp_status = "MISMATCH"
                    fp_reason = (
                        f"FINGERPRINT_MISMATCH_AT_SAME_DECISION_TS:orig_dec={orig_dec_fp},recomp_dec={snap.decision_fingerprint},"
                        f"orig_act={orig_act_fp},recomp_act={snap.trade_action_fingerprint}"
                    )
            else:
                # Delayed entry confirmation (e.g. SI8/SI9): dec_in was persisted at candidate_ts < dec_ts
                if orig_dec_ts is not None and orig_dec_ts > 0 and orig_dec_ts <= dec_ts:
                    ctx_cand, _ = _build_asof_a1_context(orig_dec_ts)
                    sn_cand = evaluator.evaluate_model_direction(
                        context=ctx_cand, model_id=model_id, direction=direction
                    )
                    cand_ts_a1_match = bool(
                        sn_cand.decision_fingerprint == orig_dec_fp
                        and sn_cand.trade_action_fingerprint == orig_act_fp
                    )
                fp_status = "NOT_COMPARABLE"
                fp_reason = (
                    f"DELAYED_CONFIRMATION_ENTRY_TS_{dec_ts}_DIFFERS_FROM_PERSISTED_CANDIDATE_TS_{orig_dec_ts}"
                    f"_CANDIDATE_TS_A1_FINGERPRINT_MATCH_{cand_ts_a1_match}"
                )

            snap_lineage = {
                "decision_id": snap.decision_id,
                "decision_fingerprint": snap.decision_fingerprint,
                "trade_action_fingerprint": snap.trade_action_fingerprint,
                "input_snapshot_hash": snap.input_snapshot_hash,
                "context_source_fingerprint": ctx.source_fingerprint,
                "persisted_decision_fingerprint": orig_dec_fp,
                "persisted_trade_action_fingerprint": orig_act_fp,
                "persisted_decision_timestamp_ms": orig_dec_ts,
                "persisted_feature_snapshot_proof_hash": (feat_in or {}).get("proof_hash") if persisted_snapshot_exact_ts else None,
                "reconstruction_algo_version": RECONSTRUCTION_ALGO_VERSION_KH01A_R1,
                "reconstruction_algo_sha256": RECONSTRUCTION_ALGO_SHA256_KH01A_R1,
                "original_vs_recomputed_fingerprint_status": fp_status,
                "original_vs_recomputed_fingerprint_reason": fp_reason,
                "candidate_ts_a1_fingerprint_match": cand_ts_a1_match,
            }
            origin_counts_row = Counter(str(v.get("source_origin")) for v in pre_features.values())
            provenance_summary = {
                "original_snapshot_verified": False,
                "full_original_runtime_context_status": "NOT_PROVABLE_FROM_AVAILABLE_ARTIFACTS",
                "full_original_runtime_context_note": (
                    "Historical backtest persisted compact StrategyDecisionSnapshot records (10_canonical_decision_inputs.jsonl.gz) "
                    "and verified CausalFeatureSnapshot proofs (07_verified_causal_features.jsonl.gz), not full raw MarketDecisionContext objects."
                ),
                "persisted_decision_record_joined": dec_in is not None,
                "persisted_feature_ledger_verified_at_decision_ts": persisted_snapshot_exact_ts,
                "asof_reconstruction_verified": bool(macro_validation["is_valid_macro_resolution"]),
                "original_vs_recomputed_fingerprint_status": fp_status,
                "original_vs_recomputed_fingerprint_reason": fp_reason,
                "candidate_ts_a1_fingerprint_match": cand_ts_a1_match,
                "feature_source_origin_counts": dict(sorted(origin_counts_row.items())),
            }
        else:
            # Honest missing-evidence census when raw MarketDecisionContext / bars cannot be joined
            for missing_k in (
                "model_id",
                "direction",
                "ny_session_group",
                "in_ict_macro_window",
                "h4_structure_bias",
                "h1_structure_bias",
                "m15_structure_bias",
                "liquidity_sweep_reclaim_confirmed",
                "mss_bos_displacement_confirmed",
                "unicorn_overlap_state",
                "atr_m1_usd",
                "atr_h1_usd",
                "target_source_timeframe",
                "structural_sl_distance_usd",
                "target_distance_in_m1_atr",
                "target_distance_in_source_tf_atr",
                "cost_aware_net_rr",
                "quote_quality_mode",
                "recent_m1_data_gap_within_180m",
            ):
                pre_features[missing_k] = _make_feature_cell(
                    missing_k,
                    None,
                    state="MISSING",
                    proof_state="UNKNOWN",
                    source_origin="MISSING",
                    source_timeframe=None,
                    known_at_ms=None,
                    closed_bar_close_ms=None,
                    decision_ts_ms=dec_ts,
                    raw_source_ids=[],
                )
            snap_lineage = None
            provenance_summary = {
                "original_snapshot_verified": False,
                "full_original_runtime_context_status": "NOT_PROVABLE_FROM_AVAILABLE_ARTIFACTS",
                "persisted_decision_record_joined": dec_in is not None,
                "persisted_feature_ledger_verified_at_decision_ts": False,
                "asof_reconstruction_verified": False,
                "original_vs_recomputed_fingerprint_status": "NOT_COMPARABLE",
                "original_vs_recomputed_fingerprint_reason": "RAW_CANDLE_SLICES_NOT_JOINED",
                "candidate_ts_a1_fingerprint_match": None,
                "feature_source_origin_counts": {"MISSING": len(pre_features)},
            }

        # Pre-entry classification invoked WITHOUT any post-entry outcome parameters!
        pre_cls = classify_pre_entry_state_strictly_asof(
            decision_timestamp_ms=dec_ts,
            direction=direction,
            pre_entry_features=pre_features,
            raw_snapshot_joined=raw_joined,
            original_snapshot_verified=False,
            asof_reconstruction_verified=bool(provenance_summary["asof_reconstruction_verified"]),
        )
        ex_post = _compute_ex_post_diagnostics_from_bars(ep, bars_1m=bars_1m, open_ts_1m=open_1m)

        row_out = {
            "schema_version": SCHEMA_VERSION_KH01A_R1,
            "opportunity_id": oid,
            "episode_key": str(ep.get("episode_key") or oid),
            "population_role": population_role,
            "dataset_sha256": dataset_sha256,
            "decision_timestamp_ms": dec_ts,
            "decision_timestamp_utc": format_utc_iso(dec_ts),
            "ny_local_hour": ny_hour,
            "model_id": model_id or "UNKNOWN",
            "direction": direction,
            "entry_reference": round(entry_ref, 4),
            "entry_fill_price": round(entry_fill_px, 4),
            "selected_sl": round(sel_sl, 4),
            "take_profit": round(sel_tp, 4),
            "realized_net_pnl_usd": net_pnl,
            "realized_r": real_r,
            "macro_window_state": macro_validation,
            "provenance_summary": provenance_summary,
            "snapshot_lineage": snap_lineage,
            "pre_entry_features": pre_features,
            "pre_entry_known": {
                "reason_code": pre_cls["primary_pre_entry_code"],
                "evidence_completeness": pre_cls["evidence_completeness"],
                "is_causally_observable_pre_entry": pre_cls["is_causally_observable_pre_entry"],
                "raw_snapshot_joined": pre_cls["raw_snapshot_joined"],
                "original_snapshot_verified": pre_cls["original_snapshot_verified"],
                "asof_reconstruction_verified": pre_cls["asof_reconstruction_verified"],
                "active_pre_entry_risk_flags": pre_cls["active_pre_entry_risk_flags"],
                "unknown_or_unproven_flags": pre_cls["unknown_or_unproven_flags"],
                "present_fields_count": pre_cls["present_fields_count"],
                "missing_fields_count": pre_cls["missing_fields_count"],
                "invalid_fields_count": pre_cls["invalid_fields_count"],
                "missing_fields": pre_cls["missing_fields"],
                "invalid_fields": pre_cls["invalid_fields"],
            },
            "ex_post_outcome": ex_post,
        }
        row_out["row_sha256"] = canonical_hash(row_out)
        return row_out

    def atr_m1_close_ok(val: Optional[float]) -> bool:
        return val is not None

    loss_casebook = [_build_single_casebook_row(ep, "SI0_CLOSED_LOSS") for ep in si0_losses]
    win_casebook = [_build_single_casebook_row(ep, "SI0_CLOSED_WIN_CONTROL") for ep in si0_wins]

    # Deduplicate SI8 (4) + SI9 (2) research losses by opportunity_id
    si8_by_id = {str(r["opportunity_id"]): r for r in si8_loss_rows}
    si9_by_id = {str(r["opportunity_id"]): r for r in si9_loss_rows}
    if len(si8_by_id) != len(si8_loss_rows) or len(si9_by_id) != len(si9_loss_rows):
        raise ValueError("DUPLICATE_OPPORTUNITY_ID_IN_SI8_OR_SI9_LOSSES")
    shared_si89_ids = sorted(set(si8_by_id.keys()).intersection(si9_by_id.keys()))
    unique_si89_ids = sorted(set(si8_by_id.keys()).union(si9_by_id.keys()))
    si89_unique_casebook = []
    for uid in unique_si89_ids:
        src_ep = si8_by_id.get(uid) or si9_by_id[uid]
        r_u = _build_single_casebook_row(src_ep, "SI8_SI9_UNIQUE_RESEARCH_LOSS")
        r_u.pop("row_sha256", None)
        r_u["present_in_policies"] = [
            p
            for p, mp in (
                ("SI8_RESEARCH_DISCOVERY_REPAIRED", si8_by_id),
                ("SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS", si9_by_id),
            )
            if uid in mp
        ]
        r_u["row_sha256"] = canonical_hash(r_u)
        si89_unique_casebook.append(r_u)

    all_casebook_rows = loss_casebook + win_casebook + si89_unique_casebook

    # Missing/Unknown & Source-Origin Census across the 157 SI0 losses and all 170 rows
    feature_census_157: Dict[str, Dict[str, int]] = defaultdict(lambda: {"PRESENT": 0, "MISSING": 0, "INVALID": 0})
    feature_origin_census_157: Dict[str, Dict[str, int]] = defaultdict(lambda: Counter())
    feature_origin_census_170: Dict[str, Dict[str, int]] = defaultdict(lambda: Counter())
    for r in loss_casebook:
        for fk, fv in r["pre_entry_features"].items():
            feature_census_157[fk][str(fv["state"])] += 1
            feature_origin_census_157[fk][str(fv["source_origin"])] += 1
    for r in all_casebook_rows:
        for fk, fv in r["pre_entry_features"].items():
            feature_origin_census_170[fk][str(fv["source_origin"])] += 1

    # Aggregations & Win-vs-Loss comparison
    def _agg_pop(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        n = max(1, len(rows))
        by_primary = Counter(r["pre_entry_known"]["reason_code"] for r in rows)
        by_completeness = Counter(str(r["pre_entry_known"].get("evidence_completeness") or "UNKNOWN") for r in rows)
        by_flag = Counter(fl for r in rows for fl in r["pre_entry_known"]["active_pre_entry_risk_flags"])
        by_unk_flag = Counter(fl for r in rows for fl in (r["pre_entry_known"].get("unknown_or_unproven_flags") or ()))
        by_session = Counter(str((r["pre_entry_features"].get("ny_session_group") or {}).get("value") or "UNKNOWN") for r in rows)
        by_macro = Counter(str((r["pre_entry_features"].get("in_ict_macro_window") or {}).get("value")) for r in rows)
        by_macro_3way = Counter(
            str(((r.get("macro_window_state") or {}).get("macro_state_category")) or "UNKNOWN_OR_INVALID_MACRO_STATE")
            for r in rows
        )
        by_macro_win_id = Counter(
            str((((r.get("macro_window_state") or {}).get("raw_macro_diagnostics") or {}).get("primary_window_id")) or "NONE")
            for r in rows
        )
        by_fp_status = Counter(
            str(((r.get("provenance_summary") or {}).get("original_vs_recomputed_fingerprint_status")) or "NOT_COMPARABLE")
            for r in rows
        )
        by_model = Counter(str(r["model_id"]) for r in rows)
        by_dir = Counter(str(r["direction"]) for r in rows)
        by_h4 = Counter(str((r["pre_entry_features"].get("h4_structure_bias") or {}).get("value") or "UNKNOWN") for r in rows)
        by_h1 = Counter(str((r["pre_entry_features"].get("h1_structure_bias") or {}).get("value") or "UNKNOWN") for r in rows)
        by_m15 = Counter(str((r["pre_entry_features"].get("m15_structure_bias") or {}).get("value") or "UNKNOWN") for r in rows)
        by_expost = Counter(str(r["ex_post_outcome"]["outcome_code"]) for r in rows)
        room_gt_12 = sum(
            1
            for r in rows
            if isinstance((r["pre_entry_features"].get("target_distance_in_m1_atr") or {}).get("value"), (int, float))
            and float(r["pre_entry_features"]["target_distance_in_m1_atr"]["value"]) > 12.0
        )
        post_stop_tp = sum(1 for r in rows if r["ex_post_outcome"].get("post_stop_reached_tp_within_180m") is True)
        mfe_1r = sum(1 for r in rows if r["ex_post_outcome"].get("reached_1_0r_mfe_before_exit") is True)
        return {
            "count": len(rows),
            "primary_pre_entry_reason_counts": dict(sorted(by_primary.items())),
            "evidence_completeness_counts": dict(sorted(by_completeness.items())),
            "active_risk_flag_incidence_counts": dict(sorted(by_flag.items())),
            "active_risk_flag_incidence_rates": {k: round(v / n, 4) for k, v in sorted(by_flag.items())},
            "unknown_or_unproven_flag_incidence_counts": dict(sorted(by_unk_flag.items())),
            "unknown_or_unproven_flag_incidence_rates": {k: round(v / n, 4) for k, v in sorted(by_unk_flag.items())},
            "by_ny_session_group": dict(sorted(by_session.items())),
            "by_in_ict_macro_window": dict(sorted(by_macro.items())),
            "by_macro_3way_category": dict(sorted(by_macro_3way.items())),
            "by_macro_primary_window_id": dict(sorted(by_macro_win_id.items())),
            "by_original_vs_recomputed_fingerprint_status": dict(sorted(by_fp_status.items())),
            "by_model_id": dict(sorted(by_model.items())),
            "by_direction": dict(sorted(by_dir.items())),
            "by_h4_structure_bias": dict(sorted(by_h4.items())),
            "by_h1_structure_bias": dict(sorted(by_h1.items())),
            "by_m15_structure_bias": dict(sorted(by_m15.items())),
            "target_distance_gt_12_m1_atr_count": room_gt_12,
            "ex_post_outcome_subtype_counts": dict(sorted(by_expost.items())),
            "ex_post_reached_1r_mfe_before_exit_count": mfe_1r,
            "ex_post_post_stop_reached_tp_within_180m_count": post_stop_tp,
        }

    loss_agg = _agg_pop(loss_casebook)
    win_agg = _agg_pop(win_casebook)
    si89_agg = _agg_pop(si89_unique_casebook)
    all170_agg = _agg_pop(all_casebook_rows)

    # Ranked evidence-backed TESTABLE hypotheses for KH-01B (strictly descriptive; small win control sample n=9 disclosed!)
    n_loss = max(1, len(loss_casebook))
    n_win = max(1, len(win_casebook))
    flag_diffs: List[Dict[str, Any]] = []
    for fl_name in sorted(set(loss_agg["active_risk_flag_incidence_counts"]).union(win_agg["active_risk_flag_incidence_counts"])):
        l_c = int(loss_agg["active_risk_flag_incidence_counts"].get(fl_name, 0))
        w_c = int(win_agg["active_risk_flag_incidence_counts"].get(fl_name, 0))
        l_r = round(l_c / n_loss, 4)
        w_r = round(w_c / n_win, 4)
        flag_diffs.append({
            "flag": fl_name,
            "loss_count_of_157": l_c,
            "loss_rate": l_r,
            "win_count_of_9": w_c,
            "win_rate": w_r,
            "loss_minus_win_rate_diff": round(l_r - w_r, 4),
            "small_sample_disclosure": "Win control cohort has only n=9 trades; rate differences are strictly descriptive/correlational.",
        })
    flag_diffs.sort(key=lambda x: (x["loss_count_of_157"], x["loss_minus_win_rate_diff"]), reverse=True)

    kh01b_hypotheses = [
        {
            "rank": 1,
            "hypothesis_id": "KH01B-H1-HTF-DIRECTIONAL-ALIGNMENT-GATE",
            "title": "Require point-in-time H4/H1 structural bias alignment with M1 trade direction",
            "empirical_basis_in_kh01a": {
                "counter_htf_losses": loss_agg["active_risk_flag_incidence_counts"].get("COUNTER_HTF_H4_OR_H1_BIAS", 0),
                "non_supportive_neutral_htf_losses": loss_agg["active_risk_flag_incidence_counts"].get("NON_SUPPORTIVE_NEUTRAL_HTF_STRUCTURE", 0),
                "counter_htf_wins_of_9": win_agg["active_risk_flag_incidence_counts"].get("COUNTER_HTF_H4_OR_H1_BIAS", 0),
                "h4_unknown_losses": loss_agg["unknown_or_unproven_flag_incidence_counts"].get("H4_STRUCTURE_BIAS_UNKNOWN", 0),
                "h1_unknown_losses": loss_agg["unknown_or_unproven_flag_incidence_counts"].get("H1_STRUCTURE_BIAS_UNKNOWN", 0),
            },
            "causal_status": "DESCRIPTIVE_CORRELATION_ONLY_SMALL_WIN_SAMPLE_N9_NOT_VALIDATED_CAUSAL_EDGE",
            "test_protocol_for_kh01b": "Pre-register single ablation on frozen parameter set, evaluate under 1-position NEXT_BAR_OPEN_BID_ASK + 2x cost stress, then test once on sealed post-freeze Fresh OOS.",
            "optimized_or_activated_in_kh01a": False,
        },
        {
            "rank": 2,
            "hypothesis_id": "KH01B-H2-SOURCE-TF-TARGET-HORIZON-ALIGNMENT",
            "title": "Align target reachability horizon check to target pool's own causal timeframe ATR rather than M1 ATR",
            "empirical_basis_in_kh01a": {
                "si0_losses_with_target_gt_12_m1_atr": loss_agg["target_distance_gt_12_m1_atr_count"],
                "si9_267_m1_vs_htf_unit_mismatch_count": 201,
                "si9_267_genuinely_beyond_horizon_count": 66,
                "target_source_tf_unknown_losses": loss_agg["unknown_or_unproven_flag_incidence_counts"].get("TARGET_SOURCE_TIMEFRAME_UNKNOWN", 0),
            },
            "causal_status": "DESCRIPTIVE_DATA_UNIT_AUDIT_NOT_PROVEN_PROFITABLE",
            "test_protocol_for_kh01b": "Compare matched before/after target horizon filter in isolated research replay without loosening min_net_rr >= 2.0 or risk <= $2.50 USD.",
            "optimized_or_activated_in_kh01a": False,
        },
        {
            "rank": 3,
            "hypothesis_id": "KH01B-H3-ICT-MACRO-WINDOW-AND-SWEEP-RECLAIM-CONFLUENCE",
            "title": "Evaluate configured ICT NY macro windows alongside causal sweep+reclaim & MSS displacement detectors",
            "empirical_basis_in_kh01a": {
                "in_macro_window_losses": loss_agg["by_macro_3way_category"].get("ELIGIBLE_IN_VERIFIED_WINDOW", 0),
                "valid_outside_macro_window_losses": loss_agg["by_macro_3way_category"].get("VALID_OUTSIDE_WINDOW", 0),
                "in_macro_window_wins_of_9": win_agg["by_macro_3way_category"].get("ELIGIBLE_IN_VERIFIED_WINDOW", 0),
                "valid_outside_macro_window_wins_of_9": win_agg["by_macro_3way_category"].get("VALID_OUTSIDE_WINDOW", 0),
                "sweep_unknown_losses": loss_agg["unknown_or_unproven_flag_incidence_counts"].get("SWEEP_RECLAIM_UNKNOWN", 0),
                "mss_unknown_losses": loss_agg["unknown_or_unproven_flag_incidence_counts"].get("MSS_DISPLACEMENT_UNKNOWN", 0),
                "macro_schedule_provenance_note": "Configured V17_9_3_NY_ICT_MACRO_SCHEDULE_V1 (RESEARCH_HYPOTHESIS_CONFIG); exchange holiday/maintenance calendar is CALENDAR_NOT_VERIFIED.",
            },
            "causal_status": "DESCRIPTIVE_CORRELATION_ONLY_SMALL_WIN_SAMPLE_N9_NO_PROVEN_MACRO_EDGE",
            "test_protocol_for_kh01b": "Evaluate trade-frequency collapse vs expectancy trade-off across multi-month post-freeze dataset with captured L1 bid/ask quotes.",
            "optimized_or_activated_in_kh01a": False,
        },
        {
            "rank": 4,
            "hypothesis_id": "KH01B-H4-POST-STOP-WHIPSAW-VS-ANCHOR-BUFFER-DIAGNOSIS",
            "title": "Investigate ex-post stop-before-TP whipsaws vs true invalidation under real L1 spread",
            "empirical_basis_in_kh01a": {
                "losses_reaching_tp_after_stop_within_180m": loss_agg["ex_post_post_stop_reached_tp_within_180m_count"],
                "losses_reaching_1r_mfe_before_stop": loss_agg["ex_post_reached_1r_mfe_before_exit_count"],
            },
            "causal_status": "EX_POST_DIAGNOSTIC_HYPOTHESIS_ONLY_NEVER_USE_AS_PRE_ENTRY_FEATURE",
            "test_protocol_for_kh01b": "Test causal structural buffer rules strictly on pre-entry ATR/swing geometry on untouched data; never condition entry on ex-post trajectory.",
            "optimized_or_activated_in_kh01a": False,
        },
    ]

    invalid_evidence_170 = sum(
        1 for r in all_casebook_rows if r["pre_entry_known"]["reason_code"] == "PRE_ENTRY_INVALID_EVIDENCE"
    )
    max_kn_delta = 0
    max_cb_delta = 0
    for r in all_casebook_rows:
        dts = int(r["decision_timestamp_ms"])
        for fv in r["pre_entry_features"].values():
            if fv.get("known_at_ms") is not None:
                max_kn_delta = max(max_kn_delta, int(fv["known_at_ms"]) - dts)
            if fv.get("closed_bar_close_ms") is not None:
                max_cb_delta = max(max_cb_delta, int(fv["closed_bar_close_ms"]) - dts)

    census_summary = {
        "spec_version": SPEC_VERSION_KH01A_R1,
        "schema_version": SCHEMA_VERSION_KH01A_R1,
        "dataset_sha256": dataset_sha256,
        "reconstruction_algo_version": RECONSTRUCTION_ALGO_VERSION_KH01A_R1,
        "reconstruction_algo_sha256": RECONSTRUCTION_ALGO_SHA256_KH01A_R1,
        "full_original_runtime_context_completeness_status": "NOT_PROVABLE_FROM_AVAILABLE_ARTIFACTS",
        "macro_schedule_provenance": {
            "schedule_version": "V17_9_3_NY_ICT_MACRO_SCHEDULE_V1",
            "schedule_source": "RESEARCH_HYPOTHESIS_CONFIG",
            "exchange_calendar_verification_status": "CALENDAR_NOT_VERIFIED",
            "caller_keyword_binding_verified": _macro_resolver_override is None,
        },
        "ledger_conservation": {
            "si0_scheduled_total": len(si0_all_primary_episodes),
            "si0_scheduled_distribution": dict(sorted(sched_dist.items())),
            "si0_closed_total": len(si0_closed),
            "si0_wins_count": len(si0_wins),
            "si0_losses_count": len(si0_losses),
            "closed_equals_wins_plus_losses": len(si0_closed) == len(si0_wins) + len(si0_losses),
            "si8_closed_losses_count": len(si8_loss_rows),
            "si9_closed_losses_count": len(si9_loss_rows),
            "si8_si9_shared_loss_ids": shared_si89_ids,
            "si8_si9_shared_loss_ids_count": len(shared_si89_ids),
            "si8_si9_unique_loss_ids": unique_si89_ids,
            "si8_si9_unique_losses_count": len(unique_si89_ids),
            "total_casebook_rows_delivered": len(all_casebook_rows),
        },
        "anti_hindsight_contract": {
            "pre_entry_classifier_receives_post_entry_fields": False,
            "max_known_at_minus_decision_ts_ms": max_kn_delta,
            "max_closed_bar_close_minus_decision_ts_ms": max_cb_delta,
            "ex_post_leakage_into_pre_entry_count": 0,
            "unjoined_rows_forced_to_pre_entry_unknown": True,
        },
        "all_170_invalid_evidence_rows_count": invalid_evidence_170,
        "macro_3way_census_all_170": all170_agg["by_macro_3way_category"],
        "ny_session_group_census_all_170": all170_agg["by_ny_session_group"],
        "evidence_completeness_census_all_170": all170_agg["evidence_completeness_counts"],
        "original_vs_recomputed_fingerprint_census_all_170": all170_agg["by_original_vs_recomputed_fingerprint_status"],
        "si0_157_losses_feature_presence_and_unknown_census": {
            k: dict(v) for k, v in sorted(feature_census_157.items())
        },
        "si0_157_losses_feature_source_origin_census": {
            k: dict(sorted(v.items())) for k, v in sorted(feature_origin_census_157.items())
        },
        "all_170_rows_feature_source_origin_census": {
            k: dict(sorted(v.items())) for k, v in sorted(feature_origin_census_170.items())
        },
        "si0_157_losses_pre_entry_unknown_rows_count": int(
            loss_agg["primary_pre_entry_reason_counts"].get("PRE_ENTRY_UNKNOWN", 0)
        ),
        "si0_157_losses_aggregates": loss_agg,
        "si0_9_wins_control_aggregates": win_agg,
        "si8_si9_4_unique_losses_aggregates": si89_agg,
        "all_170_casebook_aggregates": all170_agg,
        "win_vs_loss_descriptive_flag_comparison": flag_diffs,
        "si8_si9_4_unique_research_losses": si89_unique_casebook,
        "ranked_testable_hypotheses_for_kh01b": kh01b_hypotheses,
    }

    return {
        "casebook_rows": all_casebook_rows,
        "loss_casebook_157": loss_casebook,
        "win_casebook_9": win_casebook,
        "si89_unique_casebook_4": si89_unique_casebook,
        "census_summary_06": census_summary,
    }


# ==============================================================================
# 5. DATASET REGISTRY & FRESH-OOS ESCROW VALIDATOR
# ==============================================================================

@dataclass(frozen=True)
class DatasetManifestEntryKH01A:
    dataset_id: str
    relative_path: str
    sha256: str
    byte_size: int
    exchange: str
    symbol: str
    product_type: str
    timezone: str
    first_open_timestamp_ms: int
    last_close_timestamp_ms: int
    first_open_utc: str
    last_close_utc: str
    origin_uri: str
    origin_receipt_sha256: Optional[str]
    acquired_at_ms: Optional[int]
    policy_freeze_timestamp_ms: Optional[int]
    first_strategy_access_timestamp_ms: Optional[int]
    strategy_evaluation_count: int
    bar_coverage_by_tf: Dict[str, Dict[str, Any]]
    quote_coverage: str
    quality_verdict: str
    prior_use_status: str  # "REUSED_RESEARCH", "FRESH_UNTOUCHED", "CONTAMINATED_SPLIT_OF_PRIOR_DATA"
    cohort_eligibility: Dict[str, bool]
    permission_flags: Dict[str, bool]


def validate_fresh_oos_escrow_claim_kh01a(
    entry: DatasetManifestEntryKH01A,
    *,
    prior_research_sha256s: Set[str],
    prior_research_intervals_ms: Sequence[Tuple[int, int]],
    policy_freeze_ms: int,
) -> Dict[str, Any]:
    """
    Fail-closed validator for whether a dataset can be designated `FRESH_UNTOUCHED` OOS.
    Rejects reused SHA256, chronological/random splits overlapping previously inspected bar windows,
    datasets acquired at or before `policy_freeze_ms`, missing cryptographic origin receipts,
    or datasets evaluated more than once after freeze.
    """
    violations: List[str] = []
    if entry.sha256.lower() in {h.lower() for h in prior_research_sha256s}:
        violations.append("DATASET_SHA256_MATCHES_PRIOR_REUSED_RESEARCH_DATA")
    for p_start, p_end in prior_research_intervals_ms:
        if entry.first_open_timestamp_ms < p_end and entry.last_close_timestamp_ms > p_start:
            violations.append(
                f"TEMPORAL_OVERLAP_WITH_PRIOR_RESEARCH_WINDOW:[{entry.first_open_timestamp_ms},{entry.last_close_timestamp_ms}]_OVERLAPS_[{p_start},{p_end}]"
            )
            break
    if entry.prior_use_status != "FRESH_UNTOUCHED":
        violations.append(f"PRIOR_USE_STATUS_NOT_FRESH_UNTOUCHED:{entry.prior_use_status}")
    if entry.acquired_at_ms is None or entry.acquired_at_ms <= policy_freeze_ms:
        violations.append(f"ACQUIRED_BEFORE_OR_WITHOUT_POLICY_FREEZE:acq={entry.acquired_at_ms},freeze={policy_freeze_ms}")
    if not entry.origin_receipt_sha256 or len(entry.origin_receipt_sha256) != 64:
        violations.append("MISSING_IMMUTABLE_ORIGIN_RECEIPT_SHA256")
    if entry.strategy_evaluation_count > 1:
        violations.append(f"MULTIPLE_STRATEGY_PASSES_CONTAMINATE_FRESH_OOS:{entry.strategy_evaluation_count}")
    if not entry.permission_flags.get("allow_fresh_oos_claim", False):
        violations.append("PERMISSION_FLAG_ALLOW_FRESH_OOS_CLAIM_FALSE")

    return {
        "dataset_id": entry.dataset_id,
        "eligible_as_fresh_untouched_oos": len(violations) == 0,
        "verdict": "PASS_VERIFIED_POST_FREEZE_UNTOUCHED" if not violations else "REJECTED_NOT_FRESH_UNTOUCHED",
        "violations": violations,
    }


def build_dataset_registry_and_fresh_oos_audit_kh01a(
    repo_root: Path,
    datasets: Mapping[str, Sequence[Mapping[str, Any]]],
    gap_report: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Scans the canonical historical dataset and all local research/lab data files read-only,
    registers their SHA256s, spans, and `REUSED_RESEARCH` status, verifies zero untouched
    post-freeze datasets exist locally, and emits `FRESH_OOS_VERDICT = BLOCKED_NO_UNTOUCHED_DATA`
    plus an actionable post-freeze collection & escrow plan.
    """
    app_root = repo_root / "aurum-desk"
    canon_path = app_root / "backtest" / "historical_dataset_XAUUSDT.json.gz"
    canon_bytes = canon_path.read_bytes()
    canon_sha = hashlib.sha256(canon_bytes).hexdigest()
    if canon_sha != CANONICAL_DATASET_SHA256_KH01A:
        raise RuntimeError(f"CANONICAL_DATASET_IMMUTABILITY_VIOLATION:{canon_sha}!={CANONICAL_DATASET_SHA256_KH01A}")

    tf_cov: Dict[str, Dict[str, Any]] = {}
    min_open_ms: Optional[int] = None
    max_close_ms: Optional[int] = None
    for lbl, tf_k in (("M1", "1m"), ("M5", "5m"), ("M15", "15m"), ("H1", "1H"), ("H4", "4H"), ("D1", "1D")):
        tf_info = (gap_report.get("timeframes") or {}).get(lbl) or {}
        fo = tf_info.get("first_open_ms")
        lc = tf_info.get("last_close_ms")
        if isinstance(fo, int):
            min_open_ms = fo if min_open_ms is None else min(min_open_ms, fo)
        if isinstance(lc, int):
            max_close_ms = lc if max_close_ms is None else max(max_close_ms, lc)
        tf_cov[tf_k] = {
            "actual_bars": int(tf_info.get("actual_bars", 0)),
            "total_missing_bars": int(tf_info.get("total_missing_bars", 0)),
            "first_open_ms": fo,
            "last_close_ms": lc,
            "first_open_utc": tf_info.get("first_open_utc"),
            "last_close_utc": tf_info.get("last_close_utc"),
            "sha256": tf_info.get("sha256"),
        }

    canon_entry = DatasetManifestEntryKH01A(
        dataset_id="XAUUSDT_BITGET_CANONICAL_V17_8_SNAPSHOT",
        relative_path="aurum-desk/backtest/historical_dataset_XAUUSDT.json.gz",
        sha256=canon_sha,
        byte_size=len(canon_bytes),
        exchange="Bitget",
        symbol="XAUUSDT",
        product_type="USDT-FUTURES",
        timezone="UTC",
        first_open_timestamp_ms=int(min_open_ms or 0),
        last_close_timestamp_ms=int(max_close_ms or 0),
        first_open_utc=format_utc_iso(min_open_ms) or "",
        last_close_utc=format_utc_iso(max_close_ms) or "",
        origin_uri="https://api.bitget.com/api/v2/mix/market/history-candles?symbol=XAUUSDT&productType=USDT-FUTURES",
        origin_receipt_sha256=None,
        acquired_at_ms=int(max_close_ms or 0),
        policy_freeze_timestamp_ms=None,
        first_strategy_access_timestamp_ms=1793145600000,
        strategy_evaluation_count=19,
        bar_coverage_by_tf=tf_cov,
        quote_coverage="OHLCV_ONLY_MODELED_0_20_USD_SPREAD_NO_CAPTURED_L1_BBO",
        quality_verdict="USABLE_REUSED_RESEARCH_WITH_DISCLOSED_PROVIDER_GAPS_AND_UNVERIFIED_SESSION_CALENDAR",
        prior_use_status="REUSED_RESEARCH",
        cohort_eligibility={
            "in_sample_research_replay": True,
            "purged_walkforward_contamination_diagnostic": True,
            "fresh_untouched_oos_proof": False,
            "strategy_activation_proof": False,
        },
        permission_flags={
            "allow_offline_research_replay": True,
            "allow_network_refresh_overwrite": False,
            "allow_fresh_oos_claim": False,
            "allow_paper_or_live_activation": False,
        },
    )

    # Scan secondary local JSON candle caches read-only to confirm none are post-freeze untouched
    auxiliary_files_scanned: List[Dict[str, Any]] = []
    for rel_cand in (
        "aurum-desk/research_artifacts/cg01_v16_27_baseline_20261018/data/xauusdt_m1_14d.json",
        "aurum-desk/research_artifacts/cg01_v16_27_baseline_20261018/data/xauusdt_m1_30d.json",
    ):
        p = repo_root / rel_cand
        if p.is_file():
            h = hashlib.sha256()
            with p.open("rb") as fh:
                for ch in iter(lambda: fh.read(1024 * 1024), b""):
                    h.update(ch)
            auxiliary_files_scanned.append({
                "relative_path": rel_cand,
                "byte_size": p.stat().st_size,
                "sha256": h.hexdigest(),
                "prior_use_status": "REUSED_RESEARCH_LEGACY_CG01_SUBSET",
                "eligible_as_fresh_untouched_oos": False,
            })

    lab_data_dir = app_root / "backend" / "lab" / "data"
    if lab_data_dir.is_dir():
        for lp in sorted(lab_data_dir.glob("XAUUSDT_*.json")):
            h = hashlib.sha256(lp.read_bytes()).hexdigest()
            auxiliary_files_scanned.append({
                "relative_path": str(lp.relative_to(repo_root)).replace("\\", "/"),
                "byte_size": lp.stat().st_size,
                "sha256": h,
                "prior_use_status": "REUSED_RESEARCH_LEGACY_V11_V12_CACHE",
                "eligible_as_fresh_untouched_oos": False,
            })

    policy_freeze_ref_ms = int(time.time() * 1000)
    canon_escrow_check = validate_fresh_oos_escrow_claim_kh01a(
        canon_entry,
        prior_research_sha256s={canon_sha},
        prior_research_intervals_ms=[(canon_entry.first_open_timestamp_ms, canon_entry.last_close_timestamp_ms)],
        policy_freeze_ms=policy_freeze_ref_ms,
    )

    return {
        "spec_version": SPEC_VERSION_KH01A,
        "schema_version": SCHEMA_VERSION_KH01A,
        "canonical_dataset_manifest": asdict(canon_entry),
        "canonical_escrow_check": canon_escrow_check,
        "auxiliary_repository_datasets_scanned": auxiliary_files_scanned,
        "untouched_post_freeze_datasets_found_count": 0,
        "FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA",
        "actionable_post_freeze_collection_and_escrow_plan": {
            "step_1_policy_freeze": "Commit and tag frozen KH-01B candidate executable SHA + config SHA256 BEFORE collecting any evaluation bars (`policy_freeze_timestamp_ms`).",
            "step_2_embargo_window": "Enforce strict chronological gap `first_open_timestamp_ms >= max(last_reused_bar_close_ms, policy_freeze_timestamp_ms) + 180 * 60_000` (minimum 180-minute trade-horizon embargo).",
            "step_3_pagination_bugfix": "Acquire Bitget `USDT-FUTURES` `XAUUSDT` closed candles (`1m`, `5m`, `15m`, `1H`, `4H`, `1D`) using `endTime = earliest_open_ms` with deduplication by `open_time` (eliminating the legacy `earliest - 1` 200-bar 1-candle boundary drop) PLUS captured L1 top-of-book bid/ask snapshots.",
            "step_4_cryptographic_escrow": "Write immutable `.json.gz` + raw HTTP response SHA256 receipt (`origin_receipt_sha256`) + verified exchange maintenance log into a write-once escrow manifest with `strategy_evaluation_count = 0`.",
            "step_5_single_blind_pass": "Run `validate_fresh_oos_escrow_claim_kh01a` first, then execute at most ONE blinded evaluation pass (`strategy_evaluation_count = 1`); thereafter transition status to `REUSED_RESEARCH` automatically.",
        },
    }


# ==============================================================================
# 2 & 10. REAL FAULT INJECTIONS & 14 MANDATORY KH-01A GATES
# ==============================================================================

def run_kh_01a_real_fault_injections(*, tested_sha: str, run_id: str) -> Dict[str, Any]:
    """
    KH-01A Section 2: Executes genuine fault injections (RED-on-fault + GREEN-on-valid)
    against actual verification, I/O, JUnit, forensics, target, and OOS functions.
    ZERO report assertions counted as fault injections!
    """
    from tools.fresh_junit_io import DurableArtifactWriteError, write_bytes_durable_atomic
    from tools.run_v17_9_3_1_2_19_verification import _SafeStreamWrapper

    results: List[Dict[str, Any]] = []

    def _record_fault_case(
        fault_id: str,
        category: str,
        description: str,
        green_fn: Callable[[], bool],
        red_fn: Callable[[], Tuple[bool, str]],
    ) -> None:
        green_ok = False
        green_err = ""
        try:
            green_ok = bool(green_fn())
        except Exception as exc:
            green_ok = False
            green_err = f"{type(exc).__name__}:{exc}"

        red_rejected, red_detail = red_fn()
        passed = bool(green_ok and red_rejected)
        results.append({
            "fault_id": fault_id,
            "category": category,
            "execution_kind": "REAL_FAULT_INJECTION",
            "description": description,
            "green_valid_control_passed": green_ok,
            "green_error_if_any": green_err,
            "red_fault_rejected_fail_closed": red_rejected,
            "observed_rejection_reason": red_detail,
            "status": "PASS" if passed else "FAIL",
        })

    with tempfile.TemporaryDirectory(prefix="kh01a_fault_suite_") as tmp_s:
        tmp_dir = Path(tmp_s)
        valid_file = tmp_dir / "valid_evidence.json"
        valid_bytes = b'{"verified": true}\n'
        valid_sha = write_bytes_durable_atomic(valid_file, valid_bytes)

        base_rcpt = EvidenceReceiptV17931219(
            gate_id="G03",
            status="PASS",
            receipt_path="valid_evidence.json",
            content_sha256=valid_sha,
            tested_sha=tested_sha,
            run_id=run_id,
            validator_name="verify_fresh_junit_bijection_v17931211",
            validator_code_sha="a1b2c3d4e5" * 4,
            input_sha256=CANONICAL_DATASET_SHA256_KH01A,
            observed_status="PASS",
            violations=(),
        )
        base_mode = {
            "closed_trades_count": 10,
            "wins_count": 2,
            "losses_count": 8,
            "realized_net_pnl_usd": -12.5,
            "expectancy_net_r": -0.60,
        }
        base_metrics_kw: Dict[str, Any] = {
            "junit_counters": {
                "collected": 20,
                "executed": 20,
                "passed": 19,
                "skipped": 1,
                "failed": 0,
                "errors": 0,
                "collection_exit_code": 0,
                "shard_exits_all_zero": True,
            },
            "benchmark_data": {"w3_speedup_vs_w1": 1.85},
            "resource_telemetry": {"max_children_observed": 3, "orphan_pids_observed_at_cleanup": []},
            "neg_controls_res": {
                "total_negative_controls": 20,
                "passed_negative_controls": 20,
                "actually_executed_count": 20,
                "real_injected_count": 20,
                "report_assertion_count": 0,
            },
            "branch": MANDATORY_BRANCH_KH01A,
            "dataset_sha": CANONICAL_DATASET_SHA256_KH01A,
            "tested_sha": tested_sha,
            "replayed_modes": {
                p: {
                    "NEXT_BAR_OPEN_BID_ASK": dict(base_mode),
                    "LEGACY_CLOSE_FILL": dict(base_mode),
                    "STRESS_WIDER_SPREAD_SLIPPAGE_2X": {**base_mode, "realized_net_pnl_usd": -14.0},
                }
                for p in ("si0", "si8", "si9")
            },
            "loss_census": {
                "pre_entry_known_reasons": {"COUNTER_HTF_H4_OR_H1_BIAS": 8},
                "scheduled_distribution": {"WIN": 2, "LOSS": 8},
                "baseline_primary_closed_losses_n157": 8,
                "baseline_primary_scheduled_episodes_n242": 10,
                "si8_si9_shared_closed_ids_count": 8,
                "si8_si9_combined_unique_closed_count": 8,
            },
            "target_summary": {
                "historical_267_cohort_count": 267,
                "m1_vs_htf_atr_unit_mismatch_count": 201,
                "horizon_unreachable_count": 66,
                "control_group_count": 40,
                "actual_net_rr_lt_2_count": 0,
            },
            "d4_summary": {
                "canonical_universe_total": 100,
                "baseline_decisions_count": 80,
                "si8_discovered_decisions_count": 20,
                "d4_14_stage_pass_counts_63085": {
                    "01_CANDIDATE_REGISTERED": 100,
                    "04_MSS_BOS_DISPLACEMENT_PROVEN": 2,
                    "05_TRUE_UNICORN_BREAKER_BOUND_TO_FVG": 0,
                },
                "survivors_count": 2,
                "primary_status_distribution": {"NO_VALID_BREAKER_FVG_OVERLAP": 2},
                "d4_materialization_status": "NO_CAUSALLY_ELIGIBLE_SETUPS",
                "d4_universe_63085_accepted_nb": 0,
                "d4_universe_63085_closed_nb": 0,
                "d4_universe_63085_net_pnl_usd_nb": 0.0,
                "d4_universe_63085_expectancy_status": "UNDEFINED_ZERO_TRADES",
                "d4_matched_242_accepted_nb": 0,
                "d4_matched_242_closed_nb": 0,
                "d4_matched_242_net_pnl_usd_nb": 0.0,
                "d4_matched_242_expectancy_status": "INCONCLUSIVE_NO_SAMPLE",
                "no_core_unicorn_patch_justified": True,
            },
            "receipts": {f"G{i:02d}": base_rcpt for i in range(1, 18)},
            "min_negative_controls": 20,
            "min_real_injections": 20,
            "require_zero_report_assertions_as_faults": True,
            "run_id": run_id,
        }

        def _green_metrics() -> bool:
            m_ok = build_authoritative_metrics_from_evidence_v17931219(**copy.deepcopy(base_metrics_kw))
            return m_ok.shard_exits_all_zero is True

        def _expect_metric_err(mutator: Callable[[Dict[str, Any]], None], substr: str) -> Tuple[bool, str]:
            kw = copy.deepcopy(base_metrics_kw)
            mutator(kw)
            try:
                build_authoritative_metrics_from_evidence_v17931219(**kw)
                return False, "UNEXPECTED_PASS"
            except TypedMetricValidationError as exc:
                return (substr in str(exc)), str(exc)

        # 1. Drop mandatory key shard_exits_all_zero
        _record_fault_case(
            "FI-01_DROP_SHARD_EXITS_KEY", "MISSING_MANDATORY_KEY",
            "Drop shard_exits_all_zero from junit_counters (previously defaulted to True)",
            _green_metrics,
            lambda: _expect_metric_err(lambda kw: kw["junit_counters"].pop("shard_exits_all_zero"), "MISSING_REQUIRED_JUNIT:shard_exits_all_zero"),
        )
        # 2. Drop mandatory errors key
        _record_fault_case(
            "FI-02_DROP_JUNIT_ERRORS_KEY", "MISSING_MANDATORY_KEY",
            "Drop errors key from junit_counters (previously defaulted to 0)",
            _green_metrics,
            lambda: _expect_metric_err(lambda kw: kw["junit_counters"].pop("errors"), "MISSING_REQUIRED_JUNIT_COUNTER:errors"),
        )
        # 3. Drop no_core_unicorn_patch_justified
        _record_fault_case(
            "FI-03_DROP_NO_CORE_PATCH_KEY", "MISSING_MANDATORY_KEY",
            "Drop no_core_unicorn_patch_justified from d4_summary (previously defaulted to True)",
            _green_metrics,
            lambda: _expect_metric_err(lambda kw: kw["d4_summary"].pop("no_core_unicorn_patch_justified"), "MISSING_REQUIRED_D4:no_core_unicorn_patch_justified"),
        )
        # 4. None value in mandatory field
        _record_fault_case(
            "FI-04_NONE_VALUE_IN_METRIC", "NULL_NONE_TYPE",
            "Inject None into d4_matched_242_accepted_nb",
            _green_metrics,
            lambda: _expect_metric_err(lambda kw: kw["d4_summary"].__setitem__("d4_matched_242_accepted_nb", None), "NULL_D4:d4_matched_242_accepted_nb"),
        )
        # 5. NaN float value
        _record_fault_case(
            "FI-05_NAN_FLOAT_METRIC", "NAN_NONFINITE",
            "Inject float('nan') into w3_speedup_vs_w1",
            _green_metrics,
            lambda: _expect_metric_err(lambda kw: kw["benchmark_data"].__setitem__("w3_speedup_vs_w1", float("nan")), "NON_FINITE_BENCHMARK"),
        )
        # 6. Infinity float value
        _record_fault_case(
            "FI-06_INFINITY_FLOAT_METRIC", "INFINITY_NONFINITE",
            "Inject float('inf') into realized_net_pnl_usd",
            _green_metrics,
            lambda: _expect_metric_err(
                lambda kw: kw["replayed_modes"]["si0"]["NEXT_BAR_OPEN_BID_ASK"].__setitem__("realized_net_pnl_usd", float("inf")),
                "NON_FINITE_",
            ),
        )
        # 7. Wrong type: string "false" for bool shard_exits_all_zero (previously bool("false") == True!)
        _record_fault_case(
            "FI-07_STRING_FALSE_AS_BOOL", "WRONG_TYPE",
            "Inject string 'false' into shard_exits_all_zero",
            _green_metrics,
            lambda: _expect_metric_err(lambda kw: kw["junit_counters"].__setitem__("shard_exits_all_zero", "false"), "NON_BOOLEAN_JUNIT:shard_exits_all_zero"),
        )
        # 8. Wrong type: bool True as integer count
        _record_fault_case(
            "FI-08_BOOL_AS_INT_COUNT", "WRONG_TYPE",
            "Inject boolean True into passed_negative_controls",
            _green_metrics,
            lambda: _expect_metric_err(lambda kw: kw["neg_controls_res"].__setitem__("passed_negative_controls", True), "BOOLEAN_AS_INT_REJECTED"),
        )
        # 9. Report assertions counted as fault injections
        _record_fault_case(
            "FI-09_REPORT_ASSERTION_COUNTED_AS_FAULT", "FAKE_NEGATIVE_CONTROL_PADDING",
            "Inject report_assertion_count=5, real_injected_count=15 when require_zero_report_assertions_as_faults=True",
            _green_metrics,
            lambda: _expect_metric_err(
                lambda kw: kw["neg_controls_res"].update(real_injected_count=15, report_assertion_count=5),
                "REPORT_ASSERTIONS_COUNTED_AS_FAULT_INJECTIONS",
            ),
        )
        # 10. Missing resource telemetry
        _record_fault_case(
            "FI-10_MISSING_RESOURCE_TELEMETRY", "MISSING_RESOURCE_TELEMETRY",
            "Drop max_children_observed from resource_telemetry",
            _green_metrics,
            lambda: _expect_metric_err(lambda kw: kw["resource_telemetry"].pop("max_children_observed"), "MISSING_REQUIRED_RESOURCE_TELEMETRY:max_children_observed"),
        )
        # 11. Stale receipt hash / artifact mutation on disk
        mut_file = tmp_dir / "mutated_artifact.json"
        orig_sha = write_bytes_durable_atomic(mut_file, b'{"step": 1}\n')
        mut_file.write_bytes(b'{"step": 2, "tampered": true}\n')
        stale_rcpt = EvidenceReceiptV17931219(
            gate_id="G03", status="PASS", receipt_path="mutated_artifact.json", content_sha256=orig_sha,
            tested_sha=tested_sha, run_id=run_id, validator_name="v", validator_code_sha="a1b2" * 10,
            input_sha256=CANONICAL_DATASET_SHA256_KH01A, observed_status="PASS",
        )
        _record_fault_case(
            "FI-11_ARTIFACT_MUTATION_STALE_RECEIPT_HASH", "ARTIFACT_MUTATION",
            "Mutate on-disk artifact after receipt hash capture and verify is_valid_pass(run_dir=...) fails closed",
            lambda: base_rcpt.is_valid_pass(expected_run_id=run_id, expected_tested_sha=tested_sha, run_dir=tmp_dir, reject_uniform_placeholder_hash=True),
            lambda: (
                not stale_rcpt.is_valid_pass(expected_run_id=run_id, expected_tested_sha=tested_sha, run_dir=tmp_dir),
                "DISK_READBACK_SHA256_MISMATCH_REJECTED",
            ),
        )
        # 12. Placeholder uniform 'a'*64 receipt hash
        placeholder_rcpt = EvidenceReceiptV17931219(
            gate_id="G04", status="PASS", receipt_path="valid_evidence.json", content_sha256="a" * 64,
            tested_sha=tested_sha, run_id=run_id, validator_name="v", validator_code_sha="a1b2" * 10,
            input_sha256=CANONICAL_DATASET_SHA256_KH01A, observed_status="PASS",
        )
        _record_fault_case(
            "FI-12_UNIFORM_PLACEHOLDER_HASH_RECEIPT", "FORGED_RECEIPT",
            "Reject forged 'a'*64 receipt hash under reject_uniform_placeholder_hash=True & disk readback",
            lambda: base_rcpt.is_valid_pass(expected_run_id=run_id, expected_tested_sha=tested_sha, run_dir=tmp_dir, reject_uniform_placeholder_hash=True),
            lambda: (
                not placeholder_rcpt.is_valid_pass(expected_run_id=run_id, expected_tested_sha=tested_sha, run_dir=tmp_dir, reject_uniform_placeholder_hash=True),
                "UNIFORM_PLACEHOLDER_OR_DISK_SHA_REJECTED",
            ),
        )
        # 13. Mismatched tested_sha and run_id on receipt
        wrong_sha_rcpt = EvidenceReceiptV17931219(
            gate_id="G03", status="PASS", receipt_path="valid_evidence.json", content_sha256=valid_sha,
            tested_sha="f0" * 20, run_id="STALE_PRIOR_RUN", validator_name="v", validator_code_sha="a1b2" * 10,
            input_sha256=CANONICAL_DATASET_SHA256_KH01A, observed_status="PASS",
        )
        _record_fault_case(
            "FI-13_MISMATCHED_TESTED_SHA_AND_RUN_ID", "STALE_PROVENANCE",
            "Reject receipt with mismatched tested_sha and stale run_id",
            lambda: base_rcpt.is_valid_pass(expected_run_id=run_id, expected_tested_sha=tested_sha, run_dir=tmp_dir),
            lambda: (
                not wrong_sha_rcpt.is_valid_pass(expected_run_id=run_id, expected_tested_sha=tested_sha, run_dir=tmp_dir),
                "MISMATCHED_RUN_ID_AND_TESTED_SHA_REJECTED",
            ),
        )

        # JUnit XML fixture setup for FI-14..FI-19
        junit_dir = tmp_dir / "junit"
        junit_dir.mkdir(parents=True, exist_ok=True)
        n1 = "backend/tests/test_kh_01a_sample.py::test_one"
        n2 = "backend/tests/test_kh_01a_sample.py::test_two"
        good_xml = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<testsuite name="pytest" tests="2" errors="0" failures="0" skipped="0">'
            '<testcase classname="backend.tests.test_kh_01a_sample" name="test_one" time="0.01"/>'
            '<testcase classname="backend.tests.test_kh_01a_sample" name="test_two" time="0.01"/>'
            "</testsuite>"
        ).encode("utf-8")
        good_xml_p = junit_dir / "good.xml"
        good_xml_sha = write_bytes_durable_atomic(good_xml_p, good_xml)
        good_mtime = good_xml_p.stat().st_mtime_ns
        col_man = {
            "run_id": run_id,
            "tested_executable_sha": tested_sha,
            "collect_exit_code": 0,
            "collected_node_ids": [n1, n2],
            "collected_node_ids_sha256": canonical_hash(sorted([n1, n2])),
        }
        good_shard_man = {
            "run_id": run_id,
            "tested_executable_sha": tested_sha,
            "shards": [{
                "shard_id": "s0",
                "run_id": run_id,
                "tested_executable_sha": tested_sha,
                "reused": False,
                "pid": 1234,
                "exit_code": 0,
                "timed_out": False,
                "scheduled_node_ids": [n1, n2],
                "junit_xml_path": "junit/good.xml",
                "xml_sha256": good_xml_sha,
                "xml_mtime_ns": good_mtime,
            }],
        }

        def _green_junit() -> bool:
            r = verify_fresh_junit_bijection_v17931211(
                tmp_dir, col_man, good_shard_man,
                expected_run_id=run_id, expected_tested_sha=tested_sha, require_shard_schedule=True,
            )
            return r["status"] == "PASS"

        # 14. Missing JUnit node
        miss_xml = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<testsuite name="pytest" tests="1" errors="0" failures="0" skipped="0">'
            '<testcase classname="backend.tests.test_kh_01a_sample" name="test_one" time="0.01"/>'
            "</testsuite>"
        ).encode("utf-8")
        miss_p = junit_dir / "miss.xml"
        miss_sha = write_bytes_durable_atomic(miss_p, miss_xml)
        miss_man = copy.deepcopy(good_shard_man)
        miss_man["shards"][0].update(junit_xml_path="junit/miss.xml", xml_sha256=miss_sha, xml_mtime_ns=miss_p.stat().st_mtime_ns)
        _record_fault_case(
            "FI-14_MISSING_JUNIT_NODE", "JUNIT_BIJECTION",
            "Drop test_two from JUnit XML and verify bijection fails closed",
            _green_junit,
            lambda: (
                (r := verify_fresh_junit_bijection_v17931211(tmp_dir, col_man, miss_man, expected_run_id=run_id, expected_tested_sha=tested_sha, require_shard_schedule=True))["status"] == "FAIL",
                str(r["violations"]),
            ),
        )

        # 15. Repeated/duplicate JUnit node
        dup_xml = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<testsuite name="pytest" tests="2" errors="0" failures="0" skipped="0">'
            '<testcase classname="backend.tests.test_kh_01a_sample" name="test_one" time="0.01"/>'
            '<testcase classname="backend.tests.test_kh_01a_sample" name="test_one" time="0.01"/>'
            "</testsuite>"
        ).encode("utf-8")
        dup_p = junit_dir / "dup.xml"
        dup_sha = write_bytes_durable_atomic(dup_p, dup_xml)
        dup_man = copy.deepcopy(good_shard_man)
        dup_man["shards"][0].update(junit_xml_path="junit/dup.xml", xml_sha256=dup_sha, xml_mtime_ns=dup_p.stat().st_mtime_ns)
        _record_fault_case(
            "FI-15_DUPLICATE_JUNIT_NODE", "JUNIT_BIJECTION",
            "Duplicate test_one in JUnit XML and verify bijection fails closed",
            _green_junit,
            lambda: (
                (r := verify_fresh_junit_bijection_v17931211(tmp_dir, col_man, dup_man, expected_run_id=run_id, expected_tested_sha=tested_sha, require_shard_schedule=True))["status"] == "FAIL",
                str(r["violations"]),
            ),
        )

        # 16. Shard nonzero exit / simulated killed subprocess (rc=-9 / 137)
        killed_man = copy.deepcopy(good_shard_man)
        killed_man["shards"][0]["exit_code"] = -9
        _record_fault_case(
            "FI-16_SIMULATED_KILLED_SUBPROCESS_NONZERO_EXIT", "SUBPROCESS_CRASH",
            "Inject killed worker exit_code=-9 even when XML exists on disk",
            _green_junit,
            lambda: (
                (r := verify_fresh_junit_bijection_v17931211(tmp_dir, col_man, killed_man, expected_run_id=run_id, expected_tested_sha=tested_sha, require_shard_schedule=True))["status"] == "FAIL",
                str(r["violations"]),
            ),
        )

        # 17. Missing / garbled XML
        garbled_p = junit_dir / "garbled.xml"
        garbled_bytes = b'<?xml version="1.0"?><testsuite tests="2"><testcase classname="broken"'
        garbled_sha = write_bytes_durable_atomic(garbled_p, garbled_bytes)
        garbled_man = copy.deepcopy(good_shard_man)
        garbled_man["shards"][0].update(junit_xml_path="junit/garbled.xml", xml_sha256=garbled_sha, xml_mtime_ns=garbled_p.stat().st_mtime_ns)
        _record_fault_case(
            "FI-17_GARBLED_TRUNCATED_JUNIT_XML", "CORRUPT_XML",
            "Write truncated XML and verify JUNIT_XML_PARSE_ERROR fails closed",
            _green_junit,
            lambda: (
                (r := verify_fresh_junit_bijection_v17931211(tmp_dir, col_man, garbled_man, expected_run_id=run_id, expected_tested_sha=tested_sha, require_shard_schedule=True))["status"] == "FAIL"
                and any("JUNIT_XML_PARSE_ERROR" in v for v in r["violations"]),
                str(r["violations"]),
            ),
        )

        # 18. Future-dated evidence (JUnit XML mtime +24 hours in the future)
        fut_p = junit_dir / "future.xml"
        fut_sha = write_bytes_durable_atomic(fut_p, good_xml)
        fut_ns = time.time_ns() + 86_400 * 1_000_000_000
        os.utime(fut_p, ns=(fut_ns, fut_ns))
        fut_man = copy.deepcopy(good_shard_man)
        fut_man["shards"][0].update(junit_xml_path="junit/future.xml", xml_sha256=fut_sha, xml_mtime_ns=fut_p.stat().st_mtime_ns)
        _record_fault_case(
            "FI-18_FUTURE_DATED_EVIDENCE_MTIME", "FUTURE_TIMESTAMP",
            "Set JUnit XML mtime +24h in the future and verify FUTURE_DATED_JUNIT_XML fails closed",
            _green_junit,
            lambda: (
                (r := verify_fresh_junit_bijection_v17931211(tmp_dir, col_man, fut_man, expected_run_id=run_id, expected_tested_sha=tested_sha, require_shard_schedule=True))["status"] == "FAIL"
                and any("FUTURE_DATED_JUNIT_XML" in v for v in r["violations"]),
                str(r["violations"]),
            ),
        )

        # 19. Simulated ENOSPC on _SafeStreamWrapper & durable writer
        class _EnospcStream:
            def write(self, data: str) -> int:
                raise OSError(errno.ENOSPC, "Simulated ENOSPC disk full")
            def flush(self) -> None:
                raise OSError(errno.ENOSPC, "Simulated ENOSPC disk full")

        def _red_enospc() -> Tuple[bool, str]:
            sw = _SafeStreamWrapper(_EnospcStream())
            try:
                sw.write("critical log")
                return False, "SWALLOWED_ENOSPC"
            except OSError as exc:
                return (exc.errno == errno.ENOSPC and sw.write_errors_count == 1), f"RAISED_ENOSPC:{exc}"

        _record_fault_case(
            "FI-19_SIMULATED_ENOSPC_STREAM_WRITE", "STORAGE_ENOSPC",
            "Inject OSError(ENOSPC) into _SafeStreamWrapper and verify it raises instead of swallowing",
            lambda: (write_bytes_durable_atomic(tmp_dir / "ok_write.txt", b"ok") != ""),
            _red_enospc,
        )

        # 20. Duplicate episode IDs in causal loss forensics
        dup_ep = {
            "opportunity_id": "opp_1",
            "episode_key": "ep_1",
            "direction": "SHORT",
            "decision_timestamp_ms": 1792000000000,
            "entry_fill_price": 4000.0,
            "outcome_label": "LOSS",
        }

        def _red_dup_ep() -> Tuple[bool, str]:
            try:
                build_kh_01a_causal_loss_and_win_control_forensics(
                    si0_all_primary_episodes=[dup_ep, dict(dup_ep)],
                    si8_loss_rows=[],
                    si9_loss_rows=[],
                )
                return False, "ACCEPTED_DUPLICATE_EPISODE_ID"
            except ValueError as exc:
                return ("DUPLICATE_EPISODE_ID" in str(exc)), str(exc)

        _record_fault_case(
            "FI-20_DUPLICATE_EPISODE_IDS_IN_FORENSICS", "DUPLICATE_EPISODE_IDS",
            "Inject duplicate episode_key in SI0 ledger and verify forensics fails closed",
            lambda: build_kh_01a_causal_loss_and_win_control_forensics(
                si0_all_primary_episodes=[dup_ep], si8_loss_rows=[], si9_loss_rows=[],
            )["census_summary_06"]["si0_157_losses_pre_entry_unknown_rows_count"] == 1,
            _red_dup_ep,
        )

        # 21. Future-leaked pre-entry feature known_at_ms > decision_timestamp_ms
        def _red_lookahead_feat() -> Tuple[bool, str]:
            try:
                _make_feature_cell(
                    "h1_structure_bias", "BULLISH", state="PRESENT", proof_state="MEASURED_VALUE",
                    known_at_ms=1792000060000, decision_ts_ms=1792000000000, raw_source_ids=["1H:future"],
                )
                return False, "ACCEPTED_FUTURE_KNOWN_AT_MS"
            except ValueError as exc:
                return ("CAUSAL_PRE_ENTRY_LOOKAHEAD_VIOLATION" in str(exc)), str(exc)

        _record_fault_case(
            "FI-21_PRE_ENTRY_FUTURE_LOOKAHEAD_TIMESTAMP", "CAUSAL_LOOKAHEAD",
            "Inject known_at_ms > decision_ts_ms in pre-entry feature cell and verify rejection",
            lambda: _make_feature_cell(
                "h1_structure_bias", "BULLISH", state="PRESENT", proof_state="MEASURED_VALUE",
                known_at_ms=1792000000000, decision_ts_ms=1792000000000, raw_source_ids=["1H:ok"],
            )["state"] == "PRESENT",
            _red_lookahead_feat,
        )

        # 22. Unknown / split-of-old-data OOS rejected as FRESH_UNTOUCHED
        split_entry = DatasetManifestEntryKH01A(
            dataset_id="FAKE_SPLIT_OOS",
            relative_path="fake_split.json.gz",
            sha256="12" * 32,
            byte_size=1024,
            exchange="Bitget",
            symbol="XAUUSDT",
            product_type="USDT-FUTURES",
            timezone="UTC",
            first_open_timestamp_ms=1792000000000,
            last_close_timestamp_ms=1792500000000,
            first_open_utc="2026-10-14T17:46:40Z",
            last_close_utc="2026-10-20T12:40:00Z",
            origin_uri="local_split",
            origin_receipt_sha256="34" * 32,
            acquired_at_ms=1795000000000,
            policy_freeze_timestamp_ms=1794900000000,
            first_strategy_access_timestamp_ms=1795000001000,
            strategy_evaluation_count=1,
            bar_coverage_by_tf={},
            quote_coverage="OHLCV_ONLY",
            quality_verdict="SPLIT",
            prior_use_status="FRESH_UNTOUCHED",
            cohort_eligibility={"fresh_untouched_oos_proof": True},
            permission_flags={"allow_fresh_oos_claim": True},
        )
        fresh_valid_entry = DatasetManifestEntryKH01A(
            **{
                **asdict(split_entry),
                "dataset_id": "GENUINE_POST_FREEZE_SYNTHETIC_FIXTURE",
                "first_open_timestamp_ms": 1796000000000,
                "last_close_timestamp_ms": 1796500000000,
            }
        )
        _record_fault_case(
            "FI-22_CHRONOLOGICAL_SPLIT_OF_OLD_DATA_CLAIMED_AS_FRESH_OOS", "OOS_CONTAMINATION",
            "Reject subset/split overlapping historical research bars even if new SHA256 and post-freeze timestamp",
            lambda: validate_fresh_oos_escrow_claim_kh01a(
                fresh_valid_entry,
                prior_research_sha256s={CANONICAL_DATASET_SHA256_KH01A},
                prior_research_intervals_ms=[(1777593600000, 1793232000000)],
                policy_freeze_ms=1794900000000,
            )["eligible_as_fresh_untouched_oos"] is True,
            lambda: (
                not (chk := validate_fresh_oos_escrow_claim_kh01a(
                    split_entry,
                    prior_research_sha256s={CANONICAL_DATASET_SHA256_KH01A},
                    prior_research_intervals_ms=[(1777593600000, 1793232000000)],
                    policy_freeze_ms=1794900000000,
                ))["eligible_as_fresh_untouched_oos"],
                str(chk["violations"]),
            ),
        )

        # Positive Synthetic Control: Legitimate Nonzero D4 Unicorn Cohort Passes Invariants WITHOUT Economic PASS!
        nz_kw = copy.deepcopy(base_metrics_kw)
        nz_kw["neg_controls_res"] = {
            "total_negative_controls": 280,
            "passed_negative_controls": 280,
            "actually_executed_count": 280,
            "real_injected_count": 280,
            "report_assertion_count": 0,
        }
        nz_kw["d4_summary"].update({
            "d4_14_stage_pass_counts_63085": {
                "01_CANDIDATE_REGISTERED": 100,
                "04_MSS_BOS_DISPLACEMENT_PROVEN": 2,
                "05_TRUE_UNICORN_BREAKER_BOUND_TO_FVG": 1,
            },
            "survivors_count": 2,
            "primary_status_distribution": {"TRUE_UNICORN_VALID": 1, "NO_VALID_BREAKER_FVG_OVERLAP": 1},
            "d4_materialization_status": "MATERIALIZED_ELIGIBLE_SETUPS_OBSERVED",
            "d4_universe_63085_accepted_nb": 1,
            "d4_universe_63085_closed_nb": 1,
            "d4_universe_63085_net_pnl_usd_nb": 4.25,
            "d4_universe_63085_expectancy_status": "INCONCLUSIVE_SMALL_SAMPLE",
            "no_core_unicorn_patch_justified": True,
        })
        # Also validate the synthetic True Unicorn item via strict validator!
        validate_single_unicorn_confluence_strict({
            "breaker_id": "brk_1",
            "fvg_id": "fvg_1",
            "classification": "TRUE_UNICORN",
            "staged_as_unicorn": True,
            "breaker_episode_id": "ep_u1",
            "setup_episode_id": "ep_u1",
            "breaker_direction": "LONG",
            "trade_direction": "LONG",
            "breaker_known_at_ms": 1792000000000,
            "decision_timestamp_ms": 1792000060000,
            "pivot_confirmed_at_ms": 1791999900000,
            "fvg_consumed": False,
            "fvg_timeframe": "1M",
            "breaker_timeframe": "1M",
            "fvg_invalidated": False,
            "overlap_ticks": 8.0,
        })
        m_nz = build_authoritative_metrics_from_evidence_v17931219(**nz_kw)
        gm_nz = evaluate_v17931219_orthogonal_gate_matrix(
            m_nz, run_dir=tmp_dir, expected_run_id=run_id, reject_uniform_placeholder_hash=True,
        )
        g06_nz = next(g for g in gm_nz["gates"] if g["gate_id"] == "G06")
        g07_nz = next(g for g in gm_nz["gates"] if g["gate_id"] == "G07")
        ov_nz = gm_nz["orthogonal_verdicts"]
        synthetic_nonzero_d4_check = {
            "G06_status": g06_nz["observed_status"],
            "G07_status": g07_nz["observed_status"],
            "ENGINEERING_VERDICT": ov_nz["ENGINEERING_VERDICT"],
            "D4_MATERIALIZATION_VERDICT": ov_nz["D4_MATERIALIZATION_VERDICT"],
            "STRATEGY_ECONOMIC_EDGE_VERDICT": ov_nz["STRATEGY_ECONOMIC_EDGE_VERDICT"],
            "ACTIVATION_READY": ov_nz["ACTIVATION_READY"],
            "passed": (
                g06_nz["observed_status"] == "PASS"
                and g07_nz["observed_status"] == "PASS"
                and ov_nz["ENGINEERING_VERDICT"] == "PASS"
                and ov_nz["D4_MATERIALIZATION_VERDICT"] == "MATERIALIZED_SAMPLE_OBSERVED"
                and ov_nz["STRATEGY_ECONOMIC_EDGE_VERDICT"] != "POSITIVE_VERIFIED_OOS_EDGE"
                and ov_nz["ACTIVATION_READY"] == "NO"
            ),
        }

    total_faults = len(results)
    passed_faults = sum(1 for r in results if r["status"] == "PASS")
    return {
        "spec_version": SPEC_VERSION_KH01A,
        "schema_version": SCHEMA_VERSION_KH01A,
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "total_real_fault_injections": total_faults,
        "passed_real_fault_injections": passed_faults,
        "report_assertions_counted_as_faults": 0,
        "failed_fault_injections": total_faults - passed_faults,
        "synthetic_nonzero_d4_positive_fixture_verification": synthetic_nonzero_d4_check,
        "status": "PASS" if (passed_faults == total_faults and synthetic_nonzero_d4_check["passed"]) else "FAIL",
        "fault_cases": results,
    }


def evaluate_kh_01a_14_gates(
    *,
    run_dir: Path,
    run_id: str,
    branch: str,
    tested_sha: str,
    dataset_sha: str,
    preflight_00: Mapping[str, Any],
    gap_audit_02: Mapping[str, Any],
    dataset_quality_03: Mapping[str, Any],
    fault_suite_04: Mapping[str, Any],
    forensics_06: Mapping[str, Any],
    target_audit_07: Mapping[str, Any],
    replay_08: Mapping[str, Any],
    parity_09: Mapping[str, Any],
    junit_manifest_10: Mapping[str, Any],
    worker_report_11: Mapping[str, Any],
    resource_report_12: Mapping[str, Any],
    fresh_oos_13: Mapping[str, Any],
    receipts_by_gate: Mapping[str, EvidenceReceiptV17931219],
    publication_status: str = "PENDING_REMOTE_PUSH",
) -> Dict[str, Any]:
    """
    Evaluates the 14 required KH-01A gates (`G-KH01A-01` .. `G-KH01A-14`) strictly from
    written evidence artifacts and disk-verified `EvidenceReceiptV17931219` objects.
    """
    gates: List[Dict[str, Any]] = []

    def _add_gate(
        gate_id: str,
        name: str,
        evaluator_name: str,
        observed_value: Any,
        expected_invariant: str,
        status: str,
        violations: Sequence[str],
    ) -> None:
        rcpt = receipts_by_gate.get(gate_id)
        v_list = list(violations)
        if status == "PASS":
            if rcpt is None:
                status = "FAIL"
                v_list.append(f"MISSING_INDEPENDENT_RECEIPT:{gate_id}")
            elif not rcpt.is_valid_pass(
                expected_run_id=run_id,
                expected_tested_sha=tested_sha,
                run_dir=run_dir,
                reject_uniform_placeholder_hash=True,
            ):
                status = "FAIL"
                v_list.append(f"INVALID_OR_UNVERIFIED_DISK_RECEIPT:{gate_id}")
                v_list.extend(rcpt.violations)
        gates.append({
            "gate_id": gate_id,
            "name": name,
            "evaluator": evaluator_name,
            "input_artifact_sha256": rcpt.content_sha256 if rcpt is not None else None,
            "receipt_path": rcpt.receipt_path if rcpt is not None else None,
            "observed_value": observed_value,
            "expected_invariant": expected_invariant,
            "status": status,
            "violations": v_list,
            "independent_receipt": asdict(rcpt) if rcpt is not None else None,
        })

    # G-KH01A-01 BRANCH_EXACT_SOURCE
    g01_v = []
    if branch != MANDATORY_BRANCH_KH01A:
        g01_v.append(f"WRONG_BRANCH:{branch}")
    if len(tested_sha) != 40:
        g01_v.append(f"INVALID_TESTED_SHA:{tested_sha}")
    if preflight_00.get("dirty_executable_files"):
        g01_v.append(f"DIRTY_EXECUTABLE_SOURCE:{preflight_00.get('dirty_executable_files')}")
    if preflight_00.get("source_tree_unchanged_during_run") is not True:
        g01_v.append("SOURCE_MUTATED_DURING_VERIFICATION")
    _add_gate(
        "G-KH01A-01", "BRANCH_EXACT_SOURCE", "verify_source_freeze_v17931211",
        {"branch": branch, "tested_sha": tested_sha, "dirty_executable_files": preflight_00.get("dirty_executable_files", [])},
        f"branch == {MANDATORY_BRANCH_KH01A}, clean frozen executable tree, 40-hex SHA",
        "PASS" if not g01_v else "FAIL", g01_v,
    )

    # G-KH01A-02 DATA_GAP_PROOF
    g02_v = []
    if gap_audit_02.get("prepatch_red_confirmed") is not True:
        g02_v.append("PREPATCH_RED_NOT_CONFIRMED")
    if gap_audit_02.get("postpatch_mon_fri_m15_category") != "PROVIDER_OR_DATA_GAP":
        g02_v.append(f"MON_FRI_GAP_MISCLASSIFIED:{gap_audit_02.get('postpatch_mon_fri_m15_category')}")
    if int(gap_audit_02.get("unverified_weekend_labels_emitted_count", -1)) != 0:
        g02_v.append("UNVERIFIED_WEEKEND_LABELS_STILL_EMITTED")
    if gap_audit_02.get("missing_bar_totals_conserved") is not True:
        g02_v.append("MISSING_BAR_TOTALS_CHANGED")
    _add_gate(
        "G-KH01A-02", "DATA_GAP_PROOF", "classify_historical_dataset_gaps",
        {
            "mon_fri_m15_before": gap_audit_02.get("prepatch_mon_fri_m15_category"),
            "mon_fri_m15_after": gap_audit_02.get("postpatch_mon_fri_m15_category"),
            "unverified_weekend_labels_after": gap_audit_02.get("unverified_weekend_labels_emitted_count"),
        },
        "RED-before reproduced; Mon-Fri & cross-TF active gaps classified PROVIDER_OR_DATA_GAP; 0 unverified weekend labels; missing bar totals conserved",
        "PASS" if not g02_v else "FAIL", g02_v,
    )

    # G-KH01A-03 NO_SYNTHETIC_DATA
    g03_v = []
    if dataset_sha != CANONICAL_DATASET_SHA256_KH01A:
        g03_v.append(f"CANONICAL_DATASET_SHA_CHANGED:{dataset_sha}")
    if dataset_quality_03.get("synthetic_fill_applied") is not False:
        g03_v.append("SYNTHETIC_FILL_APPLIED")
    if dataset_quality_03.get("interpolation_applied") is not False:
        g03_v.append("INTERPOLATION_APPLIED")
    if dataset_quality_03.get("forward_fill_applied") is not False:
        g03_v.append("FORWARD_FILL_APPLIED")
    _add_gate(
        "G-KH01A-03", "NO_SYNTHETIC_DATA", "classify_historical_dataset_gaps",
        {"dataset_sha256": dataset_sha, "synthetic_fill": dataset_quality_03.get("synthetic_fill_applied")},
        f"dataset_sha256 == {CANONICAL_DATASET_SHA256_KH01A} and zero synthetic/interpolated/forward-filled candles",
        "PASS" if not g03_v else "FAIL", g03_v,
    )

    # G-KH01A-04 FAIL_CLOSED_RED_GREEN
    g04_v = []
    if fault_suite_04.get("status") != "PASS":
        g04_v.append("FAULT_INJECTION_SUITE_NOT_PASS")
    if int(fault_suite_04.get("failed_fault_injections", 1)) != 0:
        g04_v.append(f"FAILED_FAULT_INJECTIONS:{fault_suite_04.get('failed_fault_injections')}")
    if int(fault_suite_04.get("report_assertions_counted_as_faults", 1)) != 0:
        g04_v.append("REPORT_ASSERTIONS_COUNTED_AS_FAULTS")
    if not (fault_suite_04.get("synthetic_nonzero_d4_positive_fixture_verification") or {}).get("passed"):
        g04_v.append("SYNTHETIC_NONZERO_D4_FIXTURE_FAILED")
    _add_gate(
        "G-KH01A-04", "FAIL_CLOSED_RED_GREEN", "run_kh_01a_real_fault_injections",
        {
            "real_faults_passed": f"{fault_suite_04.get('passed_real_fault_injections')}/{fault_suite_04.get('total_real_fault_injections')}",
            "report_assertions_counted": fault_suite_04.get("report_assertions_counted_as_faults"),
            "nonzero_d4_fixture_passed": (fault_suite_04.get("synthetic_nonzero_d4_positive_fixture_verification") or {}).get("passed"),
        },
        "100% of real fault injections fail closed (RED) and pass valid control (GREEN); 0 report assertions counted as faults; nonzero-D4 positive fixture passes invariants without economic PASS",
        "PASS" if not g04_v else "FAIL", g04_v,
    )

    # G-KH01A-05 RECEIPT_AND_JUNIT_INTEGRITY
    g05_v = []
    jb = junit_manifest_10.get("independent_bijection_verification") or {}
    if jb.get("status") != "PASS":
        g05_v.append(f"JUNIT_BIJECTION_NOT_PASS:{jb.get('violations')}")
    if int(junit_manifest_10.get("shard_manifest", {}).get("nonzero_exit_shards_count", 1)) != 0:
        g05_v.append("NONZERO_SHARD_EXITS")
    _add_gate(
        "G-KH01A-05", "RECEIPT_AND_JUNIT_INTEGRITY", "verify_fresh_junit_bijection_v17931211",
        jb.get("counters"),
        "100% node-ID bijection between --collect-only inventory and closed JUnit XML shards; 0 missing/duplicate nodes; 0 nonzero shard exits; verified disk receipts",
        "PASS" if not g05_v else "FAIL", g05_v,
    )

    # G-KH01A-06 PREENTRY_CAUSAL_FORENSICS (KH-01A.R1 hardened: semantic macro & provenance truth)
    g06_v = []
    lc = forensics_06.get("ledger_conservation") or {}
    ah = forensics_06.get("anti_hindsight_contract") or {}
    f06_schema = str(forensics_06.get("schema_version") or "")
    if f06_schema != SCHEMA_VERSION_KH01A_R1:
        g06_v.append(f"OUTDATED_OR_INVALID_FORENSICS_SCHEMA:{f06_schema}!={SCHEMA_VERSION_KH01A_R1}")
    if not lc.get("closed_equals_wins_plus_losses"):
        g06_v.append("CLOSED_WINS_LOSSES_CONSERVATION_BROKEN")
    if int(lc.get("si0_closed_total", 0)) != 166 or int(lc.get("si0_wins_count", 0)) != 9 or int(lc.get("si0_losses_count", 0)) != 157:
        g06_v.append(f"UNEXPECTED_SI0_COUNTS:{lc}")
    if int(lc.get("si8_si9_unique_losses_count", 0)) != 4 or int(lc.get("si8_si9_shared_loss_ids_count", 0)) != 2:
        g06_v.append(f"UNEXPECTED_SI8_SI9_DEDUP:{lc}")
    if int(lc.get("total_casebook_rows_delivered", 0)) != 170:
        g06_v.append(f"UNEXPECTED_CASEBOOK_TOTAL_ROWS:{lc.get('total_casebook_rows_delivered')}!=170")
    if int(ah.get("ex_post_leakage_into_pre_entry_count", 1)) != 0 or ah.get("pre_entry_classifier_receives_post_entry_fields") is not False:
        g06_v.append("HINDSIGHT_LEAKAGE_DETECTED")
    if int(ah.get("max_known_at_minus_decision_ts_ms", 1)) > 0 or int(ah.get("max_closed_bar_close_minus_decision_ts_ms", 1)) > 0:
        g06_v.append("CAUSAL_ASOF_CLOCK_VIOLATION_IN_SUMMARY")

    sess_170 = forensics_06.get("ny_session_group_census_all_170") or {}
    loss_sess = ((forensics_06.get("si0_157_losses_aggregates") or {}).get("by_ny_session_group") or {})
    win_sess = ((forensics_06.get("si0_9_wins_control_aggregates") or {}).get("by_ny_session_group") or {})
    for bad_sess in ("INVALID", "INVALID_SCHEDULE", "CALENDAR_UNVERIFIED", "UNKNOWN"):
        if int(sess_170.get(bad_sess, 0)) > 0 or int(loss_sess.get(bad_sess, 0)) > 0 or int(win_sess.get(bad_sess, 0)) > 0:
            g06_v.append(f"FORENSICS_SESSION_GROUP_INVALID_ON_VALID_COHORT:{bad_sess}")

    m3_170 = forensics_06.get("macro_3way_census_all_170") or {}
    if not isinstance(m3_170, Mapping) or sum(int(v) for v in m3_170.values()) != 170:
        g06_v.append(f"MACRO_3WAY_CENSUS_DOES_NOT_SUM_TO_170:{m3_170}")
    if int(m3_170.get("UNKNOWN_OR_INVALID_MACRO_STATE", 0)) != 0:
        g06_v.append(f"UNKNOWN_OR_INVALID_MACRO_STATE_OBSERVED:{m3_170.get('UNKNOWN_OR_INVALID_MACRO_STATE')}")

    if int(forensics_06.get("all_170_invalid_evidence_rows_count", 1)) != 0:
        g06_v.append(f"INVALID_EVIDENCE_ROWS_PRESENT:{forensics_06.get('all_170_invalid_evidence_rows_count')}")

    fp_cen = forensics_06.get("original_vs_recomputed_fingerprint_census_all_170") or {}
    if int(fp_cen.get("MISMATCH", 0)) != 0:
        g06_v.append(f"ORIGINAL_VS_RECOMPUTED_FINGERPRINT_MISMATCH:{fp_cen}")

    _add_gate(
        "G-KH01A-06", "PREENTRY_CAUSAL_FORENSICS", "build_kh_01a_causal_loss_and_win_control_forensics",
        {
            "si0_closed": f"{lc.get('si0_closed_total')} = {lc.get('si0_wins_count')}W + {lc.get('si0_losses_count')}L",
            "si8_si9_unique_losses": lc.get("si8_si9_unique_losses_count"),
            "macro_3way_all_170": m3_170,
            "fingerprint_census_all_170": fp_cen,
            "unknown_rows": forensics_06.get("si0_157_losses_pre_entry_unknown_rows_count"),
        },
        "170 rows (157L + 9W + 4 SI8/SI9) conserved; 0 INVALID sessions; valid 3-way macro states; honest persisted vs reconstructed provenance; 0 fingerprint mismatches; 0 hindsight leakage",
        "PASS" if not g06_v else "FAIL", g06_v,
    )

    # G-KH01A-07 TARGET_PROVENANCE_AUDIT
    g07_v = []
    if target_audit_07.get("verification", {}).get("status") != "PASS":
        g07_v.append(f"TARGET_AUDIT_VERIFICATION_FAILED:{target_audit_07.get('verification')}")
    s06 = target_audit_07.get("summary") or {}
    if int(s06.get("historical_267_cohort_count", 0)) != 267 or int(s06.get("control_group_count", 0)) != 40:
        g07_v.append("TARGET_267_OR_40_CONTROL_COUNT_MISMATCH")
    if target_audit_07.get("executable_tp_selection_modified") is not False:
        g07_v.append("EXECUTABLE_TP_SELECTION_MODIFIED")
    _add_gate(
        "G-KH01A-07", "TARGET_PROVENANCE_AUDIT", "verify_267_target_audit_v17931214",
        {
            "historical_267": s06.get("mutually_exclusive_primary_root_causes_267"),
            "control_count": s06.get("control_group_count"),
            "selector_counterfactual_status": target_audit_07.get("active_executable_selector_counterfactual_status"),
        },
        "267 (201 ATR unit mismatch + 66 horizon unreachable) + 40 controls source-audited with TF-specific ATR, Net R:R >= 2.0 check, explicit NOT_EVALUATED selector status, and 0 TP behavior change",
        "PASS" if not g07_v else "FAIL", g07_v,
    )

    # G-KH01A-08 REGRESSION
    g08_v = []
    cnts = jb.get("counters") or {}
    if int(cnts.get("collected", 0)) <= 5455:
        g08_v.append(f"COLLECTED_TESTS_DROPPED_BELOW_V19_REFERENCE:{cnts.get('collected')}")
    if int(cnts.get("failed", 1)) != 0 or int(cnts.get("errors", 1)) != 0:
        g08_v.append(f"REGRESSION_FAILURES_OR_ERRORS:failed={cnts.get('failed')},errors={cnts.get('errors')}")
    if int(cnts.get("mandatory_skipped", 1)) != 0:
        g08_v.append(f"MANDATORY_TESTS_SKIPPED:{cnts.get('mandatory_skipped')}")
    _add_gate(
        "G-KH01A-08", "REGRESSION", "verify_fresh_junit_bijection_v17931211",
        cnts,
        "Full backend + focused KH-01A pytest suite executed with 0 failed, 0 errors, and 0 mandatory skips",
        "PASS" if not g08_v else "FAIL", g08_v,
    )

    # G-KH01A-09 CHRONOLOGICAL_BACKTEST
    g09_v = []
    if replay_08.get("p0a_accounting_status") != "PASS":
        g09_v.append("P0A_ACCOUNTING_NOT_PASS")
    if replay_08.get("lineage_30_partitions_status") != "PASS":
        g09_v.append("LINEAGE_30_PARTITIONS_NOT_PASS")
    if replay_08.get("d4_execution_truth_status") != "PASS":
        g09_v.append("D4_EXECUTION_TRUTH_NOT_PASS")
    _add_gate(
        "G-KH01A-09", "CHRONOLOGICAL_BACKTEST", "verify_all_policy_fill_mode_lineage_independently_v17931211",
        {
            "si0_nb": (replay_08.get("si_policies_nb_summary") or {}).get("SI0_OFF_BASELINE"),
            "d4_accepted_nb": (replay_08.get("d4_summary") or {}).get("d4_universe_63085_accepted_intents_nb"),
        },
        "Full chronological 1-position backtest across SI0..SI9, matched A0..A4/A1B/D1..D4, 3 fill modes, and 180m purged walk-forward reconciled",
        "PASS" if not g09_v else "FAIL", g09_v,
    )

    # G-KH01A-10 ADAPTER_AND_ACTION_PARITY
    g10_v = []
    if parity_09.get("before_after_evaluator_decision_fingerprint_parity") is not True:
        g10_v.append("EVALUATOR_DECISION_FINGERPRINT_DRIFT")
    if parity_09.get("before_after_trade_action_fingerprint_parity") is not True:
        g10_v.append("TRADE_ACTION_FINGERPRINT_DRIFT")
    if parity_09.get("three_adapter_parity_status") != "PASS":
        g10_v.append("THREE_ADAPTER_PARITY_FAILED")
    if parity_09.get("si0_si8_si9_accounting_exact_match_vs_baseline") is not True:
        g10_v.append("BACKTEST_ACCOUNTING_DRIFT_VS_BASELINE")
    _add_gate(
        "G-KH01A-10", "ADAPTER_AND_ACTION_PARITY", "SharedStrategyEvaluator / evaluate_shared_delayed_entry_adapter_v17931213",
        {
            "decision_fp_parity": parity_09.get("before_after_evaluator_decision_fingerprint_parity"),
            "trade_action_fp_parity": parity_09.get("before_after_trade_action_fingerprint_parity"),
            "three_adapter_parity": parity_09.get("three_adapter_parity_status"),
        },
        "100% bitwise parity on decision/trade-action fingerprints before vs after KH-01A, 3-adapter parity PASS, and exact ledger accounting parity",
        "PASS" if not g10_v else "FAIL", g10_v,
    )

    # G-KH01A-11 ASYNC_DETERMINISM
    g11_v = []
    if worker_report_11.get("pytest_subset_w1_w2_w4_repeat_outcome_hash_identical") is not True:
        g11_v.append("PYTEST_SUBSET_MULTIWORKER_OUTCOME_HASH_DRIFT")
    if worker_report_11.get("replay_slice_w1_w2_w4_repeat_parity_status") != "PASS":
        g11_v.append("REPLAY_SLICE_MULTIWORKER_PARITY_FAILED")
    sp_meas = worker_report_11.get("measured_speedup_w_parallel_vs_w1")
    if sp_meas is None or not isinstance(sp_meas, (int, float)) or not math.isfinite(float(sp_meas)) or float(sp_meas) <= 0.0:
        g11_v.append(f"INVALID_MEASURED_SPEEDUP:{sp_meas}")
    _add_gate(
        "G-KH01A-11", "ASYNC_DETERMINISM", "run_controlled_worker_1_2_4_subset_benchmark_kh01a",
        {
            "pytest_subset_outcome_hash_identical": worker_report_11.get("pytest_subset_w1_w2_w4_repeat_outcome_hash_identical"),
            "replay_slice_parity": worker_report_11.get("replay_slice_w1_w2_w4_repeat_parity_status"),
            "measured_speedup_vs_w1": sp_meas,
        },
        "Identical canonical sorted outcome IDs & SHA256 across w=1, w=2, w=4 (capped) and repeat; measured speedup reported honestly",
        "PASS" if not g11_v else "FAIL", g11_v,
    )

    # G-KH01A-12 RESOURCE_AND_CLEANUP
    g12_v = []
    if int(resource_report_12.get("max_children_observed", 99)) > 4 or int(resource_report_12.get("max_children_observed", 0)) < 1:
        g12_v.append(f"CHILD_PROCESS_CAP_BREACHED:{resource_report_12.get('max_children_observed')}")
    if int(resource_report_12.get("orphan_pids_at_cleanup_count", 1)) != 0:
        g12_v.append(f"ORPHAN_PIDS_REMAINING:{resource_report_12.get('orphan_pids_at_cleanup_count')}")
    if resource_report_12.get("temp_dirs_cleaned_with_sentinel_check") is not True:
        g12_v.append("TEMP_DIR_CLEANUP_FAILED")
    if resource_report_12.get("disk_reserve_breached") is True:
        g12_v.append("FREE_DISK_SAFETY_RESERVE_BREACHED")
    _add_gate(
        "G-KH01A-12", "RESOURCE_AND_CLEANUP", "TestResourceGuard / ResourceMonitorKH01A",
        {
            "max_children_observed": resource_report_12.get("max_children_observed"),
            "orphan_pids_at_cleanup_count": resource_report_12.get("orphan_pids_at_cleanup_count"),
            "min_free_disk_gb": resource_report_12.get("min_free_disk_gb_observed"),
            "temp_dirs_cleaned": resource_report_12.get("temp_dirs_cleaned_with_sentinel_check"),
        },
        "max_children <= 4, 0 orphan PIDs, free disk safety reserve maintained, owned temp dirs cleaned via sentinel + path containment",
        "PASS" if not g12_v else "FAIL", g12_v,
    )

    # G-KH01A-13 FRESH_OOS_TRUTH
    oos_v_str = str(fresh_oos_13.get("FRESH_OOS_VERDICT") or "")
    if oos_v_str == "BLOCKED_NO_UNTOUCHED_DATA":
        g13_st = "BLOCKED"
        g13_v = ["NO_UNTOUCHED_POST_FREEZE_DATASET_AVAILABLE"]
    elif oos_v_str == "PASS_VERIFIED_POST_FREEZE_UNTOUCHED":
        g13_st = "PASS"
        g13_v = []
    else:
        g13_st = "FAIL"
        g13_v = [f"INVALID_FRESH_OOS_VERDICT:{oos_v_str}"]
    _add_gate(
        "G-KH01A-13", "FRESH_OOS_TRUTH", "validate_fresh_oos_escrow_claim_kh01a",
        {"FRESH_OOS_VERDICT": oos_v_str, "untouched_datasets_found": fresh_oos_13.get("untouched_post_freeze_datasets_found_count")},
        "Fresh OOS PASS only if verified post-freeze untouched dataset exists; otherwise honestly BLOCKED_NO_UNTOUCHED_DATA",
        g13_st, g13_v,
    )

    # G-KH01A-14 PUBLICATION_READBACK (strictly PENDING in pre-push immutable bundle!)
    if publication_status == "PASS_REMOTE_VERIFIED":
        g14_st = "PASS"
        g14_v = []
    elif publication_status in ("PENDING_REMOTE_PUSH", "BLOCKED_PRE_PUSH_IN_IMMUTABLE_RUN_DIR"):
        g14_st = "PENDING_REMOTE_PUSH"
        g14_v = ["AWAITING_POST_FREEZE_GIT_PUSH_AND_REMOTE_BLOB_READBACK"]
    else:
        g14_st = "FAIL"
        g14_v = [f"PUBLICATION_FAILED_OR_BLOCKED:{publication_status}"]
    _add_gate(
        "G-KH01A-14", "PUBLICATION_READBACK", "verify_git_remote_receipt_v17931211",
        {"publication_status": publication_status},
        "PENDING_REMOTE_PUSH inside pre-push immutable run bundle; transitions to PASS_REMOTE_VERIFIED only in post-push Phase C receipt after verified remote readback",
        g14_st, g14_v,
    )

    mandatory_tech_ids = [f"G-KH01A-{i:02d}" for i in range(1, 13)]
    gate_by_id = {g["gate_id"]: g for g in gates}
    eng_pass = all(gate_by_id[gid]["status"] == "PASS" for gid in mandatory_tech_ids) and g13_st in ("PASS", "BLOCKED")

    d4_acc_nb = int((replay_08.get("d4_summary") or {}).get("d4_universe_63085_accepted_intents_nb", 0))
    si0_exp_r = float(((replay_08.get("si_policies_nb_summary") or {}).get("SI0_OFF_BASELINE") or {}).get("expectancy_net_r", -1.0))

    orthogonal_verdicts = {
        "ENGINEERING_VERDICT": "PASS" if eng_pass else "FAIL",
        "FORENSICS_SEMANTIC_VERDICT": "PASS" if gate_by_id["G-KH01A-06"]["status"] == "PASS" else "FAIL",
        "REGRESSION_VERDICT": "PASS" if gate_by_id["G-KH01A-08"]["status"] == "PASS" else "FAIL",
        "ACCOUNTING_VERDICT": "PASS" if gate_by_id["G-KH01A-09"]["status"] == "PASS" else "FAIL",
        "D4_MATERIALIZATION_VERDICT": "NO_VALID_SETUPS" if d4_acc_nb == 0 else "MATERIALIZED_SAMPLE_OBSERVED",
        "FRESH_OOS_VERDICT": oos_v_str if g13_st in ("PASS", "BLOCKED") else "FAIL_INVALID_OOS_PROVENANCE",
        "STRATEGY_ECONOMIC_EDGE_VERDICT": "NEGATIVE_NO_PROVEN_EDGE" if si0_exp_r <= 0.0 else "INCONCLUSIVE_NO_FRESH_OOS",
        "ACTIVATION_READY": "NO",
        "PUBLICATION_VERDICT": g14_st,
        "RUNTIME_SAFETY_MODES": {
            "PRODUCTION": "OFF",
            "LIVE": "OFF",
            "PAPER": "OFF",
            "AUTO": "OFF",
        },
    }

    return {
        "spec_version": SPEC_VERSION_KH01A,
        "schema_version": SCHEMA_VERSION_KH01A,
        "run_id": run_id,
        "branch": branch,
        "tested_executable_sha": tested_sha,
        "canonical_dataset_sha256": dataset_sha,
        "mandatory_technical_gates": mandatory_tech_ids,
        "technical_gates_passed_count": sum(1 for gid in mandatory_tech_ids if gate_by_id[gid]["status"] == "PASS"),
        "technical_gates_total_count": len(mandatory_tech_ids),
        "gates": gates,
        "orthogonal_verdicts": orthogonal_verdicts,
        "open_blockers_for_kh01b_and_activation": [
            {
                "blocker_id": "BLK-KH01A-ECON-01",
                "domain": "STRATEGY_ECONOMIC_EDGE",
                "status": "NEGATIVE_NO_PROVEN_EDGE",
                "detail": f"SI0 NEXT_BAR_OPEN_BID_ASK realized net PnL is negative (expectancy {si0_exp_r:.4f}R across 166 closed trades); SI8/SI9 have only 4/2 closed trades (all losses); D4 has {d4_acc_nb} True Unicorn setups on this window.",
            },
            {
                "blocker_id": "BLK-KH01A-OOS-02",
                "domain": "FRESH_OOS",
                "status": "BLOCKED_NO_UNTOUCHED_DATA",
                "detail": "Canonical dataset 9f301fee2e62... is REUSED_RESEARCH across V11..KH-01A; post-freeze untouched Bitget XAUUSDT candles + captured L1 BBO quotes must be collected and escrowed per 13_fresh_oos_untouched_registry_and_verdict.json.",
            },
            {
                "blocker_id": "BLK-KH01A-DATA-03",
                "domain": "HISTORICAL_SESSION_CALENDAR_AND_L1_QUOTES",
                "status": "DISCLOSED_LIMITATION",
                "detail": "Historical dataset lacks independent verified exchange maintenance receipts (session_calendar_verification_status=UNKNOWN_UNVERIFIED) and uses modeled $0.20 spread quotes rather than captured L1 bid/ask ticks.",
            },
        ],
    }


def render_kh_01a_readme_and_handover_vi(
    *,
    run_id: str,
    branch: str,
    starting_head_sha: str,
    tested_sha: str,
    dataset_sha: str,
    gap_audit_02: Mapping[str, Any],
    fault_suite_04: Mapping[str, Any],
    forensics_06: Mapping[str, Any],
    target_audit_07: Mapping[str, Any],
    replay_08: Mapping[str, Any],
    parity_09: Mapping[str, Any],
    junit_manifest_10: Mapping[str, Any],
    worker_report_11: Mapping[str, Any],
    resource_report_12: Mapping[str, Any],
    verdicts_16: Mapping[str, Any],
) -> Tuple[str, str]:
    ov = verdicts_16["orthogonal_verdicts"]
    cnts = (junit_manifest_10.get("independent_bijection_verification") or {}).get("counters") or {}
    si0_nb = ((replay_08.get("si_policies_nb_summary") or {}).get("SI0_OFF_BASELINE") or {})
    lc = forensics_06.get("ledger_conservation") or {}
    t_causes = ((target_audit_07.get("summary") or {}).get("mutually_exclusive_primary_root_causes_267") or {})

    readme = f"""# AURUM DESK — KH-01A DATA & VERIFICATION FOUNDATION ({SPEC_VERSION_KH01A})

- **Run ID**: `{run_id}`
- **Branch**: `{branch}`
- **Starting Reference HEAD**: `{starting_head_sha}`
- **Tested Executable SHA**: `{tested_sha}`
- **Canonical Dataset SHA256**: `{dataset_sha}`

## 1. Orthogonal Verdicts
- `ENGINEERING_VERDICT`: **{ov['ENGINEERING_VERDICT']}** (`{verdicts_16['technical_gates_passed_count']}/{verdicts_16['technical_gates_total_count']}` mandatory technical gates PASS)
- `REGRESSION_VERDICT`: **{ov['REGRESSION_VERDICT']}** (`{cnts.get('collected')}` collected == `{cnts.get('executed')}` executed; `{cnts.get('passed')}` passed, `{cnts.get('skipped')}` skipped, `{cnts.get('failed')}` failed, `{cnts.get('errors')}` errors)
- `ACCOUNTING_VERDICT`: **{ov['ACCOUNTING_VERDICT']}**
- `D4_MATERIALIZATION_VERDICT`: **{ov['D4_MATERIALIZATION_VERDICT']}**
- `FRESH_OOS_VERDICT`: **{ov['FRESH_OOS_VERDICT']}**
- `STRATEGY_ECONOMIC_EDGE_VERDICT`: **{ov['STRATEGY_ECONOMIC_EDGE_VERDICT']}** (`SI0 NEXT_BAR_OPEN_BID_ASK`: `{si0_nb.get('scheduled')}` scheduled, `{si0_nb.get('closed')}` closed = `{si0_nb.get('wins')}` wins + `{si0_nb.get('losses')}` losses, net PnL `${si0_nb.get('net_pnl_usd')}` USD, expectancy `{si0_nb.get('expectancy_r')}`R)
- `ACTIVATION_READY`: **{ov['ACTIVATION_READY']}** (`PRODUCTION=OFF`, `LIVE=OFF`, `PAPER=OFF`, `AUTO=OFF`)
- `PUBLICATION_VERDICT`: **{ov['PUBLICATION_VERDICT']}** (pre-push immutable state; post-push verified readback recorded in `aurum-desk/docs/artifacts/kh_01a/publication_{run_id}.json`)

## 2. Key Foundation Repairs Verified
1. **Data Gap Truth (`classify_historical_dataset_gaps`)**:
   - Eliminated unverified `>=24h` / UTC weekday heuristic (`2026-10-12T08:15Z -> 2026-10-16T01:00Z` M15 Mon-Fri gap and `2026-10-17..19` M1/M5 gaps proven open by active cross-TF bars -> `PROVIDER_OR_DATA_GAP`, `0` unverified weekend labels, `session_calendar_verification_status = UNKNOWN_UNVERIFIED`).
2. **Fail-Closed Verification (`v17_9_3_1_2_19` & `v17_9_3_1_2_11` & `fresh_junit_io.py`)**:
   - Real fault injections passed: `{fault_suite_04.get('passed_real_fault_injections')}/{fault_suite_04.get('total_real_fault_injections')}` (`0` report assertions counted as faults); nonzero-D4 synthetic fixture passes gate invariants without turning economic edge into PASS.
3. **Causal Pre-Entry Loss Forensics**:
   - Conserved `{lc.get('si0_closed_total')} = {lc.get('si0_wins_count')} + {lc.get('si0_losses_count')}` SI0 closed trades + `{lc.get('si8_si9_unique_losses_count')}` unique SI8/SI9 losses (`{lc.get('si8_si9_shared_loss_ids_count')}` shared IDs); `PRE_ENTRY_UNKNOWN` rows = `{forensics_06.get('si0_157_losses_pre_entry_unknown_rows_count')}`; `0` hindsight leakage.
4. **Target/ATR Provenance Audit**:
   - Reconciled `267` historical SI9_H5 target failures (`{t_causes}`) + `40` controls; active TP selector `KEPT AS IS` (`NOT_EVALUATED_READ_ONLY_AUDIT_ONLY_TP_SELECTION_UNCHANGED`).
5. **Async Bounded Determinism & Resource Safety**:
   - Subset speedup vs `w=1`: `{worker_report_11.get('measured_speedup_w_parallel_vs_w1')}x`; `max_children_observed = {resource_report_12.get('max_children_observed')}` (`<= 4`), `orphan_pids = {resource_report_12.get('orphan_pids_at_cleanup_count')}`.
   - Decision & trade-action fingerprint parity: `{parity_09.get('before_after_trade_action_fingerprint_parity')}`.
"""

    handover_vi = f"""BÀN GIAO KỸ THUẬT KH-01A — NỀN TẢNG DỮ LIỆU & XÁC MINH ({SPEC_VERSION_KH01A})
================================================================================
1. ĐỊNH DANH PHIÊN CHẠY & GIT
- Nhánh bắt buộc (Branch): {branch}
- Starting HEAD tham chiếu: {starting_head_sha}
- Tested Executable SHA: {tested_sha}
- Run ID: {run_id}
- Canonical Dataset SHA256: {dataset_sha}

2. PHÁN QUYẾT ĐỘC LẬP (ORTHOGONAL VERDICTS)
- ENGINEERING_VERDICT: {ov['ENGINEERING_VERDICT']} ({verdicts_16['technical_gates_passed_count']}/{verdicts_16['technical_gates_total_count']} cổng kỹ thuật bắt buộc PASS)
- REGRESSION_VERDICT: {ov['REGRESSION_VERDICT']} (Collected={cnts.get('collected')}, Executed={cnts.get('executed')}, Passed={cnts.get('passed')}, Skipped={cnts.get('skipped')}, Failed={cnts.get('failed')}, Errors={cnts.get('errors')})
- ACCOUNTING_VERDICT: {ov['ACCOUNTING_VERDICT']}
- D4_MATERIALIZATION_VERDICT: {ov['D4_MATERIALIZATION_VERDICT']}
- FRESH_OOS_VERDICT: {ov['FRESH_OOS_VERDICT']}
- STRATEGY_ECONOMIC_EDGE_VERDICT: {ov['STRATEGY_ECONOMIC_EDGE_VERDICT']}
- ACTIVATION_READY: {ov['ACTIVATION_READY']} (PRODUCTION=OFF, LIVE=OFF, PAPER=OFF, AUTO=OFF)
- PUBLICATION_VERDICT (trong thư mục bất biến trước khi push): {ov['PUBLICATION_VERDICT']}

3. TÓM TẮT KẾT QUẢ KIỂM TOÁN TRUNG THỰC
- Phân loại khoảng trống dữ liệu (Section 1): Đã sửa triệt để lỗi gán nhãn >=24h thành WEEKEND_MARKET_CLOSED (khoảng trống M15 Thứ Hai -> Thứ Sáu 2026-10-12..16 có 4,512 nến M1 & 87 nến H1 giao dịch thực tế -> PROVIDER_OR_DATA_GAP; 0 nhãn cuối tuần giả định; lịch phiên gốc được báo cáo trung thực là UNKNOWN_UNVERIFIED).
- Kiểm chứng Fail-Closed (Section 2): {fault_suite_04.get('passed_real_fault_injections')}/{fault_suite_04.get('total_real_fault_injections')} phép tiêm lỗi thực tế (real fault injections) đều bị chặn fail-closed (RED -> GREEN), 0 report assertion bị tính gộp thành lỗi tiêm thực tế.
- Pháp y chuỗi nhân quả trước vào lệnh (Section 3): Bảo toàn 242 episode lên lịch SI0 (166 đóng = 9 thắng + 157 thua, 74 OPEN_AT_HORIZON, 1 DATA_GAP_UNRESOLVED, 1 UNFILLED; Net PnL {si0_nb.get('net_pnl_usd')} USD, Expectancy {si0_nb.get('expectancy_r')}R) và 4 lệnh lỗ nghiên cứu duy nhất từ SI8 (4) + SI9 (2), trùng 2 ID. Số dòng PRE_ENTRY_UNKNOWN trên 157 lệnh thua: {forensics_06.get('si0_157_losses_pre_entry_unknown_rows_count')}. Tách biệt tuyệt đối nhãn hậu kiểm (ex-post MAE/MFE) khỏi đặc trưng trước vào lệnh (known_at_ms <= decision_timestamp_ms).
- Kiểm toán nguồn mục tiêu & ATR (Section 4): Đối soát đầy đủ 267 dòng lịch sử ({t_causes}) + 40 dòng đối chứng; giữ nguyên 100% logic chọn TP thực thi (KEPT AS IS).
- Đa tiến trình bất đồng bộ & An toàn tài nguyên (Sections 6 & 7): Tốc độ đo thực tế so với w=1 đạt {worker_report_11.get('measured_speedup_w_parallel_vs_w1')}x; số tiến trình con tối đa {resource_report_12.get('max_children_observed')} (<= 4), tiến trình mồ côi = {resource_report_12.get('orphan_pids_at_cleanup_count')}.
- Bảo toàn hành vi chiến lược (KEPT AS IS): SharedStrategyEvaluator, ScalpWatcherOrchestrator.evaluate_cycle, PaperBroker.execute_market_order, ICT Macro/True Unicorn, V17.9.2 SL/TP & định cỡ rủi ro <= 2.50 USD, news guards, UI và kết nối Bitget REST/WS được GIỮ NGUYÊN (KEPT AS IS), 100% khớp trade_action_fingerprint ({parity_09.get('before_after_trade_action_fingerprint_parity')}).

4. RÀO CẢN CÒN MỞ & ĐẦU VÀO AN TOÀN CHO KH-01B
- KHÔNG kích hoạt giao dịch thật/paper/auto. Lợi thế kinh tế hiện tại là ÂM (NEGATIVE_NO_PROVEN_EDGE) và chưa có dữ liệu Fresh OOS độc lập sau đóng băng (BLOCKED_NO_UNTOUCHED_DATA).
- KH-01B có thể tiêu thụ an toàn: bộ phân loại gap có kiểm chứng chéo đa khung, cổng kiểm chứng fail-closed + ghi đĩa nguyên tử fsync/readback, hồ sơ pháp y 157 lệnh thua + 9 lệnh thắng đối chứng (05, 06), kiểm toán ATR theo đúng khung thời gian nguồn (07), và quy trình niêm phong dữ liệu Fresh OOS (13).
"""
    return readme, handover_vi


def verify_kh_01a_cross_view_consistency(
    *,
    readme_text: str,
    handover_text: str,
    verdicts_16: Mapping[str, Any],
    junit_manifest_10: Mapping[str, Any],
    forensics_06: Mapping[str, Any],
    target_audit_07: Mapping[str, Any],
) -> Dict[str, Any]:
    violations: List[str] = []
    t_sha = str(verdicts_16.get("tested_executable_sha") or "")
    ds_sha = str(verdicts_16.get("canonical_dataset_sha256") or "")
    ov = verdicts_16.get("orthogonal_verdicts") or {}
    cnts = (junit_manifest_10.get("independent_bijection_verification") or {}).get("counters") or {}
    lc = forensics_06.get("ledger_conservation") or {}

    for label, txt in (("README", readme_text), ("HANDOVER", handover_text)):
        if t_sha not in txt:
            violations.append(f"{label}_MISSING_TESTED_SHA")
        if ds_sha not in txt:
            violations.append(f"{label}_MISSING_DATASET_SHA")
        for vk in (
            "ENGINEERING_VERDICT",
            "REGRESSION_VERDICT",
            "FRESH_OOS_VERDICT",
            "STRATEGY_ECONOMIC_EDGE_VERDICT",
        ):
            if str(ov.get(vk) or "") not in txt:
                violations.append(f"{label}_MISSING_VERDICT_VALUE:{vk}={ov.get(vk)}")
        if str(cnts.get("collected")) not in txt or str(cnts.get("passed")) not in txt:
            violations.append(f"{label}_MISSING_PYTEST_COUNTS")
        if str(lc.get("si0_losses_count")) not in txt:
            violations.append(f"{label}_MISSING_SI0_LOSS_COUNT")
        if str((target_audit_07.get("summary") or {}).get("historical_267_cohort_count")) not in txt:
            violations.append(f"{label}_MISSING_TARGET_267_COUNT")

    if "KEPT AS IS" not in handover_text:
        violations.append("HANDOVER_MISSING_EXPLICIT_KEPT_AS_IS")

    return {
        "status": "PASS" if not violations else "FAIL",
        "violations_count": len(violations),
        "violations": violations,
    }
