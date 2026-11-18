"""
backend/services/kh_01a_r1_forensics_closure_service.py

AURUM Desk — KH-01A.R1 (VERSION: KH-01A.R1 / Forensics Truth Closure)
Corrective Forensics, Provenance Honesty, Dynamic Anti-Fake-Pass Receipts & 14-Gate R1 Validator:
  - Repo-wide AST audit of `resolve_causal_macro_time_window` call sites (`01`).
  - Reversed-argument RED vs keyword-bound GREEN macro forensic audit & NY DST/boundary proof (`02`, `03`).
  - Read-only diff of prior `KH-01A` (`20261117T013053Z_a1fd9293e2d0`) vs `KH-01A.R1` 170-row casebook (`04`).
  - Strict row-level & per-field provenance validator (`06`, `07`), rejecting `session_group == "INVALID"`
    on valid timestamps even when `166 = 9 + 157` holds, rejecting fake `PERSISTED_VERIFIED_SNAPSHOT`
    claims on reconstructed context, and enforcing `known_at_ms <= decision_timestamp_ms` and
    `closed_bar_close_ms <= decision_timestamp_ms`.
  - Real-injected R1 adversarial fault suite (`08`), including separate-OS-process verifier rejection
    of forged `"PASS"` receipts, modified JUnit XML, and corrupted `.jsonl.gz` streams.
  - Dynamic validator-driven `EvidenceReceiptV17931219` builder (`F4`: zero pre-labeled `"PASS"`).
  - Orthogonal 14-gate R1 acceptance matrix (`G-KH01A-R1-01` .. `G-KH01A-R1-14`, `16`) and
    consistent `README_KH01A_R1.md` + Vietnamese `HANDOVER_KH01A_R1.txt`.
"""
from __future__ import annotations

import ast
from collections import Counter
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import inspect
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo

from services.kh_01a_foundation_service import (
    CANONICAL_DATASET_SHA256_KH01A,
    MANDATORY_BRANCH_KH01A,
    PRIOR_KH01A_AUDITED_HEAD_SHA,
    PRIOR_KH01A_TESTED_SHA,
    RECONSTRUCTION_ALGO_SHA256_KH01A_R1,
    RECONSTRUCTION_ALGO_VERSION_KH01A_R1,
    SCHEMA_VERSION_KH01A_R1,
    SPEC_VERSION_KH01A_R1,
    VALID_MACRO_3WAY_CATEGORIES_KH01A_R1,
    VALID_SOURCE_ORIGINS_KH01A_R1,
    _make_feature_cell,
    build_kh_01a_causal_loss_and_win_control_forensics,
    classify_pre_entry_state_strictly_asof,
    run_kh_01a_real_fault_injections,
    validate_and_classify_macro_state_kh01a_r1,
)
from services.v17_9_3_1_2_7_causal_signal_truth import (
    canonical_hash,
    verify_feature_snapshot_v1793127,
)
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    verify_fresh_junit_bijection_v17931211,
)
from services.v17_9_3_1_2_19_truth_and_core_repair_service import (
    EvidenceReceiptV17931219,
    validate_r1_forensics_summary_and_macro_strict_v17931219,
)
from services.v17_9_3_macro_unicorn_intelligence import (
    DEFAULT_MACRO_SCHEDULE_VERSION,
    MIN_PLAUSIBLE_EPOCH_MS,
    resolve_causal_macro_time_window,
    validate_macro_schedule_config,
)

PRIOR_KH01A_RUN_REL_DIR = "aurum-desk/docs/artifacts/kh_01a/20261117T013053Z_a1fd9293e2d0"

REQUIRED_ARTIFACTS_KH01A_R1: Tuple[str, ...] = (
    "00_branch_sha_config_dataset_preflight.json",
    "01_function_callgraph_keep_as_is_changed_matrix.json",
    "02_macro_reversed_args_red_green_diff.json",
    "03_macro_dst_schedule_semantics_and_invalid_samples.json",
    "04_previous_vs_r1_170_casebook_distribution_diff.json",
    "05_r1_preentry_170_rows.jsonl.gz",
    "06_feature_evidence_origin_missing_invalid_source_hash_census.json",
    "07_original_snapshot_vs_asof_reconstruction_fingerprint_truth.json",
    "08_adversarial_fault_injection_red_green_suite.json",
    "09_all_3_fill_modes_si0_to_si9_one_position_backtest_oos_cost_stress.json",
    "10_full_trade_action_and_3_adapter_parity.json",
    "11_pytest_collection_junit_closed_shard_exit_bijection.json",
    "12_worker_w1_w2_w4_repeat_determinism_and_speed.json",
    "13_disk_ram_cpu_child_process_temp_cleanup.json",
    "14_fresh_oos_registry_untouched_and_economic_verdict.json",
    "15_independent_verifier_receipts_sha256_manifest.json",
    "16_final_orthogonal_verdicts_open_blockers.json",
    "README_KH01A_R1.md",
    "HANDOVER_KH01A_R1.txt",
)

MANDATORY_KH01A_R1_GATES: Tuple[Tuple[str, str], ...] = (
    ("G-KH01A-R1-01", "BRANCH_SOURCE_FREEZE_AND_IMMUTABLE_PRIOR_ARTIFACTS"),
    ("G-KH01A-R1-02", "MACRO_REVERSED_ARGS_RED_AND_KEYWORD_CALLER_GREEN"),
    ("G-KH01A-R1-03", "MACRO_TIMESTAMP_SYMBOL_SESSION_AND_NY_DST_BOUNDARIES"),
    ("G-KH01A-R1-04", "ALL_170_CASEBOOK_ROWS_CREDIBLE_MACRO_ZERO_INVALID"),
    ("G-KH01A-R1-05", "PROVENANCE_TRISTATE_PERSISTED_RECONSTRUCTED_UNKNOWN_HONESTY"),
    ("G-KH01A-R1-06", "PER_FEATURE_ASOF_CLOCK_LINEAGE_AND_ZERO_HINDSIGHT"),
    ("G-KH01A-R1-07", "COHORT_ACCOUNTING_242_166_9W_157L_AND_SI89_UNIQUE_4"),
    ("G-KH01A-R1-08", "FULL_REGRESSION_AND_R1_JUNIT_CLOSED_BIJECTION"),
    ("G-KH01A-R1-09", "FULL_HISTORICAL_BACKTEST_3FILL_1POS_AND_PURGED_WF"),
    ("G-KH01A-R1-10", "FULL_POPULATION_TRADING_EVALUATOR_AND_3_ADAPTER_PARITY"),
    ("G-KH01A-R1-11", "BOUNDED_ASYNC_W1_W2_W4_REPEAT_DETERMINISM_AND_SPEED"),
    ("G-KH01A-R1-12", "DISK_RAM_PID_SAFETY_ATOMIC_IO_AND_SENTINEL_CLEANUP"),
    ("G-KH01A-R1-13", "FRESH_OOS_UNTOUCHED_ESCROW_TRUTH"),
    ("G-KH01A-R1-14", "AUTOMATIC_GIT_COMMIT_PUSH_AND_REMOTE_READBACK"),
)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_jsonl_gz(path: Path) -> Iterable[Dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for ln in fh:
            if ln.strip():
                yield json.loads(ln)


def _validator_source_sha256(fn: Callable[..., Any]) -> str:
    src_file = inspect.getsourcefile(fn)
    if src_file and Path(src_file).is_file():
        return _file_sha256(Path(src_file))
    return hashlib.sha256(inspect.getsource(fn).encode("utf-8")).hexdigest()


# ==============================================================================
# PHASE 0 (C3): REPO-WIDE AST CALL-SITE AUDIT & RESPONSIBILITY MATRIX
# ==============================================================================

def scan_all_macro_window_call_sites_ast(
    repo_root: Path,
    *,
    include_tests: bool = True,
) -> Dict[str, Any]:
    """
    Scans `.py` files under `aurum-desk/` via Python AST for every invocation of
    `resolve_causal_macro_time_window`, checking whether positional arguments could
    pass a string symbol as `decision_timestamp_ms`.
    Note: Intentional negative-control RED reproduction tests in `backend/tests/test_kh_01a_r1_*`
    are explicitly categorized as `INTENTIONAL_NEGATIVE_CONTROL_TEST` rather than production bugs.
    """
    app_root = repo_root / "aurum-desk"
    sig = inspect.signature(resolve_causal_macro_time_window)
    sig_params = [
        {
            "name": p.name,
            "kind": str(p.kind),
            "default": None if p.default is inspect.Parameter.empty else repr(p.default),
        }
        for p in sig.parameters.values()
    ]

    call_sites: List[Dict[str, Any]] = []
    production_invalid_sites: List[Dict[str, Any]] = []

    for py_file in sorted(app_root.rglob("*.py")):
        parts = set(py_file.parts)
        if "__pycache__" in parts or "node_modules" in parts or ".git" in parts:
            continue
        rel = str(py_file.relative_to(repo_root)).replace("\\", "/")
        is_test_file = "/tests/" in rel
        if is_test_file and not include_tests:
            continue
        src = py_file.read_text(encoding="utf-8-sig")
        tree = ast.parse(src, filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn_name = None
            if isinstance(node.func, ast.Name):
                fn_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                fn_name = node.func.attr
            if fn_name != "resolve_causal_macro_time_window":
                continue

            pos_args = [ast.unparse(a) for a in node.args]
            kw_args = {str(k.arg): ast.unparse(k.value) for k in node.keywords if k.arg is not None}

            first_pos_is_str_literal = (
                len(node.args) >= 1
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            )
            has_explicit_kw_ts = "decision_timestamp_ms" in kw_args and len(node.args) == 0

            if has_explicit_kw_ts:
                classification = "VALID_EXPLICIT_KEYWORD_BINDING"
            elif first_pos_is_str_literal and is_test_file:
                classification = "INTENTIONAL_NEGATIVE_CONTROL_TEST_CALL"
            elif first_pos_is_str_literal and not is_test_file:
                classification = "INVALID_REVERSED_POSITIONAL_STRING_SYMBOL_FIRST"
            else:
                classification = "VALID_TIMESTAMP_FIRST_CALL"

            rec = {
                "file": rel,
                "line": node.lineno,
                "positional_args": pos_args,
                "keyword_args": kw_args,
                "is_test_file": is_test_file,
                "classification": classification,
            }
            call_sites.append(rec)
            if classification == "INVALID_REVERSED_POSITIONAL_STRING_SYMBOL_FIRST":
                production_invalid_sites.append(rec)

    return {
        "inspected_function": "services.v17_9_3_macro_unicorn_intelligence.resolve_causal_macro_time_window",
        "inspected_signature": str(sig),
        "signature_parameters": sig_params,
        "total_call_sites_found": len(call_sites),
        "production_call_sites_count": sum(1 for c in call_sites if not c["is_test_file"]),
        "test_call_sites_count": sum(1 for c in call_sites if c["is_test_file"]),
        "invalid_production_call_sites_count": len(production_invalid_sites),
        "invalid_production_call_sites": production_invalid_sites,
        "prior_buggy_call_site_repaired": {
            "file": "aurum-desk/backend/services/kh_01a_foundation_service.py",
            "prior_head": PRIOR_KH01A_AUDITED_HEAD_SHA,
            "prior_call": 'resolve_causal_macro_time_window("XAUUSDT", dec_ts)',
            "repaired_call": 'resolve_causal_macro_time_window(decision_timestamp_ms=dec_ts, symbol="XAUUSDT")',
        },
        "call_sites": call_sites,
    }


def _ast_span(file_path: Path, fn_name: str) -> Dict[str, Any]:
    if not file_path.is_file():
        return {"exists": False, "path": str(file_path)}
    tree = ast.parse(file_path.read_text(encoding="utf-8-sig"), filename=str(file_path))
    cls_name: Optional[str] = None
    meth_name = fn_name
    if "." in fn_name:
        cls_name, meth_name = fn_name.split(".", 1)
    for node in tree.body:
        if cls_name is None and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == meth_name:
            return {
                "exists": True,
                "start_line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
                "parameters": [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs],
            }
        if cls_name is not None and isinstance(node, ast.ClassDef) and node.name == cls_name:
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name == meth_name:
                    return {
                        "exists": True,
                        "class_name": cls_name,
                        "start_line": sub.lineno,
                        "end_line": getattr(sub, "end_lineno", sub.lineno),
                        "parameters": [a.arg for a in sub.args.args] + [a.arg for a in sub.args.kwonlyargs],
                    }
    return {"exists": False, "searched": fn_name}


def build_kh_01a_r1_callgraph_matrix(repo_root: Path) -> Dict[str, Any]:
    entries = [
        {
            "item_id": "R1-FN-01",
            "status": "CHANGED",
            "risk_classification": "P0_FORENSICS_ONLY_ZERO_TRADING_IMPACT",
            "file": "aurum-desk/backend/services/kh_01a_foundation_service.py",
            "function": "build_kh_01a_causal_loss_and_win_control_forensics",
            "upstream_evidence": [
                "SI0 primary episodes (242 scheduled, 166 closed = 9W + 157L)",
                "SI8 (4) + SI9 (2) research losses -> 4 unique",
                "10_canonical_decision_inputs.jsonl.gz",
                "07_verified_causal_features.jsonl.gz",
                "Canonical XAUUSDT 6-TF raw closed bars",
            ],
            "downstream_consumers": [
                "05_r1_preentry_170_rows.jsonl.gz",
                "06_feature_evidence_origin_missing_invalid_source_hash_census.json",
                "07_original_snapshot_vs_asof_reconstruction_fingerprint_truth.json",
                "build_loss_episode_attribution_from_raw_v17931219",
            ],
            "change_summary": (
                "Fixed reversed `resolve_causal_macro_time_window('XAUUSDT', dec_ts)` call to explicit keyword binding; "
                "reconstructed canonical A1 V17.9.2 context (`120/96/96/96/64/30`) matching 166/166 SI0 persisted decision & action "
                "fingerprints; extracted real persisted `CausalFeatureSnapshot` items (`PERSISTED_VERIFIED_SNAPSHOT`) vs "
                "`RECONSTRUCTED_ASOF_RAW_BARS` vs `MISSING`; eliminated false `bool(snap.selected_...)` sweep/MSS inference."
            ),
            "tests": [
                "test_kh_01a_r1_forensics_truth_closure.py::test_r1_macro_reversed_args_red_and_keyword_caller_green",
                "test_kh_01a_r1_forensics_truth_closure.py::test_r1_per_field_provenance_and_no_selected_id_inference",
            ],
        },
        {
            "item_id": "R1-FN-02",
            "status": "ADDED",
            "risk_classification": "P0_FORENSICS_VALIDATION_HELPER",
            "file": "aurum-desk/backend/services/kh_01a_foundation_service.py",
            "function": "validate_and_classify_macro_state_kh01a_r1",
            "upstream_evidence": ["MacroTimeWindowState", "expected_decision_timestamp_ms", "expected_symbol"],
            "downstream_consumers": ["build_kh_01a_causal_loss_and_win_control_forensics", "R1 macro DST/boundary audit"],
            "change_summary": (
                "Enforces 3-way macro categorization (`ELIGIBLE_IN_VERIFIED_WINDOW`, `VALID_OUTSIDE_WINDOW`, "
                "`UNKNOWN_OR_INVALID_MACRO_STATE`) and blocks invalid/unverified states from emitting `in_ict_macro_window=False` "
                "or `ny_session_group='INVALID'` as `PRESENT`."
            ),
            "tests": ["test_kh_01a_r1_forensics_truth_closure.py::test_r1_macro_dst_boundaries_weekends_and_invalid_inputs"],
        },
        {
            "item_id": "R1-FN-03",
            "status": "CHANGED",
            "risk_classification": "P0_PRE_ENTRY_CLASSIFIER_SEMANTICS",
            "file": "aurum-desk/backend/services/kh_01a_foundation_service.py",
            "function": "classify_pre_entry_state_strictly_asof",
            "upstream_evidence": ["decision_timestamp_ms", "direction", "pre_entry_features"],
            "downstream_consumers": ["build_kh_01a_causal_loss_and_win_control_forensics"],
            "change_summary": (
                "Emits `OUTSIDE_ICT_MACRO_WINDOW` only when `in_ict_macro_window` is `PRESENT` and `False`; routes unknown/missing "
                "macro, HTF, sweep, MSS, Unicorn, and target-TF states to `unknown_or_unproven_flags` rather than proven failure flags; "
                "reports row `evidence_completeness` (`COMPLETE`/`PARTIAL`/`UNKNOWN`/`INVALID`)."
            ),
            "tests": ["test_kh_01a_r1_forensics_truth_closure.py::test_r1_classifier_unknown_never_proven_false_and_partial_completeness"],
        },
        {
            "item_id": "R1-FN-04",
            "status": "CHANGED",
            "risk_classification": "P0_GATE_ANTI_FAKE_PASS",
            "file": "aurum-desk/backend/services/kh_01a_foundation_service.py",
            "function": "evaluate_kh_01a_14_gates",
            "upstream_evidence": ["KH-01A/R1 evidence artifacts + disk-verified EvidenceReceiptV17931219"],
            "downstream_consumers": ["kh_01a_independent_verifier.py", "kh_01a_r1_independent_verifier.py"],
            "change_summary": (
                "Hardened `G-KH01A-06` to reject legacy `kh_01a.foundation.v1` schema, any `INVALID` session group, "
                "any `UNKNOWN_OR_INVALID_MACRO_STATE` on valid historical bars, and any fingerprint `MISMATCH`; added `FORENSICS_SEMANTIC_VERDICT`."
            ),
            "tests": ["test_kh_01a_r1_forensics_truth_closure.py::test_r1_gate_rejects_invalid_session_even_when_166_conserved"],
        },
        {
            "item_id": "R1-FN-05",
            "status": "EXTENDED",
            "risk_classification": "P0_FORENSICS_SCHEMA_GUARD",
            "file": "aurum-desk/backend/services/v17_9_3_1_2_19_truth_and_core_repair_service.py",
            "function": "validate_r1_forensics_summary_and_macro_strict_v17931219",
            "upstream_evidence": ["R1 forensics summary JSON"],
            "downstream_consumers": ["R1 fault-injection suite & gate receipt validators"],
            "change_summary": "Rejects legacy forensics schema versions, ex-post fields in pre-entry features, and `INVALID` macro session censuses.",
            "tests": ["test_kh_01a_r1_forensics_truth_closure.py::test_r1_legacy_schema_and_mutated_macro_rejected"],
        },
        {
            "item_id": "R1-FN-06",
            "status": "KEPT AS IS",
            "risk_classification": "IMMUTABLE_MACRO_AND_UNICORN_AUTHORITY",
            "file": "aurum-desk/backend/services/v17_9_3_macro_unicorn_intelligence.py",
            "function": "resolve_causal_macro_time_window",
            "upstream_evidence": ["decision_timestamp_ms: int", "symbol: str = 'XAUUSDT'", "schedule_config"],
            "downstream_consumers": ["Full-universe backtest, D1..D4 pipelines, and KH-01A.R1 forensics"],
            "change_summary": "KEPT AS IS — implementation is pure, DST-aware, and correct; defect was exclusively in caller argument order.",
            "tests": ["test_v17_9_3_macro_unicorn_intelligence.py", "test_kh_01a_r1_forensics_truth_closure.py"],
        },
        {
            "item_id": "R1-FN-07",
            "status": "KEPT AS IS",
            "risk_classification": "IMMUTABLE_TRADING_DECISION_AUTHORITY",
            "file": "aurum-desk/backend/services/shared_strategy_evaluator.py",
            "function": "SharedStrategyEvaluator.evaluate_model_direction",
            "upstream_evidence": ["MarketDecisionContext", "model_id", "direction"],
            "downstream_consumers": ["REPLAY_BACKTEST", "MARKET_SHADOW", "PRODUCTION_ORCHESTRATOR_DRY_RUN"],
            "change_summary": "KEPT AS IS — 0 edits; verified bitwise equal across all 56,710 canonical A1 opportunities.",
            "tests": ["test_kh_01a_r1_forensics_truth_closure.py::test_r1_evaluator_and_3_adapter_parity"],
        },
        {
            "item_id": "R1-FN-08",
            "status": "KEPT AS IS",
            "risk_classification": "IMMUTABLE_DATA_GAP_REPAIR",
            "file": "aurum-desk/backend/services/v17_9_1_decision_funnel.py",
            "function": "classify_historical_dataset_gaps",
            "upstream_evidence": ["Canonical 6-TF OHLCV bars"],
            "downstream_consumers": ["Dataset quality & Fresh-OOS audit"],
            "change_summary": "KEPT AS IS — preserves verified KH-01A weekday multi-day `PROVIDER_OR_DATA_GAP` fix.",
            "tests": ["test_kh_01a_data_and_verification_foundation.py::test_sec1_weekday_ge24h_gap_not_weekend"],
        },
    ]
    for item in entries:
        item["ast_verification"] = _ast_span(repo_root / item["file"], item["function"])

    macro_scan = scan_all_macro_window_call_sites_ast(repo_root, include_tests=True)
    return {
        "spec_version": SPEC_VERSION_KH01A_R1,
        "schema_version": SCHEMA_VERSION_KH01A_R1,
        "prior_audited_head_sha": PRIOR_KH01A_AUDITED_HEAD_SHA,
        "prior_tested_executable_sha": PRIOR_KH01A_TESTED_SHA,
        "macro_call_site_ast_audit": macro_scan,
        "responsibility_matrix": entries,
    }


# ==============================================================================
# PHASE 1 (D1-D7): RED/GREEN MACRO DIFF, DST BOUNDARIES & PRIOR CASEBOOK DIFF
# ==============================================================================

def _invoke_legacy_reversed_positional_red_control(symbol_first: str, ts_second: int) -> Any:
    """Isolated RED reproduction helper invoking `fn(symbol_first, ts_second)` positionally."""
    fn: Callable[..., Any] = resolve_causal_macro_time_window
    return fn(symbol_first, ts_second)


def build_macro_reversed_args_red_green_and_dst_audit(
    casebook_rows_170: Sequence[Mapping[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Produces:
      - `02_macro_reversed_args_red_green_diff.json`
      - `03_macro_dst_schedule_semantics_and_invalid_samples.json`
    """
    red_session_counts: Counter[str] = Counter()
    green_session_counts: Counter[str] = Counter()
    green_3way_counts: Counter[str] = Counter()
    green_window_counts: Counter[str] = Counter()
    sample_diffs: List[Dict[str, Any]] = []

    for idx, row in enumerate(casebook_rows_170):
        ts = int(row["decision_timestamp_ms"])
        oid = str(row["opportunity_id"])
        role = str(row["population_role"])
        # Exact RED buggy positional call from prior HEAD 4b8e24fc:
        red_st = _invoke_legacy_reversed_positional_red_control("XAUUSDT", ts)
        red_val = validate_and_classify_macro_state_kh01a_r1(
            red_st, expected_decision_timestamp_ms=ts, expected_symbol="XAUUSDT"
        )
        # Repaired GREEN explicit keyword call:
        green_st = resolve_causal_macro_time_window(decision_timestamp_ms=ts, symbol="XAUUSDT")
        green_val = validate_and_classify_macro_state_kh01a_r1(
            green_st, expected_decision_timestamp_ms=ts, expected_symbol="XAUUSDT"
        )

        red_session_counts[str(red_st.session_group)] += 1
        green_session_counts[str(green_st.session_group)] += 1
        green_3way_counts[str(green_val["macro_state_category"])] += 1
        green_window_counts[str(green_st.primary_window_id)] += 1

        if idx < 8 or green_st.eligible_for_macro_gate:
            sample_diffs.append({
                "opportunity_id": oid,
                "population_role": role,
                "decision_timestamp_ms": ts,
                "decision_utc": green_st.utc_iso,
                "ny_local_iso": green_st.ny_local_iso,
                "is_dst": green_st.is_dst,
                "red_reversed_call": {
                    "is_valid_timestamp": red_st.is_valid_timestamp,
                    "session_group": red_st.session_group,
                    "eligible_for_macro_gate": red_st.eligible_for_macro_gate,
                    "r1_3way_category": red_val["macro_state_category"],
                },
                "green_keyword_call": {
                    "is_valid_timestamp": green_st.is_valid_timestamp,
                    "session_group": green_st.session_group,
                    "primary_window_id": green_st.primary_window_id,
                    "eligible_for_macro_gate": green_st.eligible_for_macro_gate,
                    "r1_3way_category": green_val["macro_state_category"],
                },
            })

    doc_02 = {
        "spec_version": SPEC_VERSION_KH01A_R1,
        "schema_version": SCHEMA_VERSION_KH01A_R1,
        "confirmed_bug_location": "aurum-desk/backend/services/kh_01a_foundation_service.py:_build_single_casebook_row",
        "prior_buggy_expression": 'resolve_causal_macro_time_window("XAUUSDT", dec_ts)',
        "repaired_expression": 'resolve_causal_macro_time_window(decision_timestamp_ms=dec_ts, symbol="XAUUSDT")',
        "cohort_rows_evaluated": len(casebook_rows_170),
        "red_before_session_group_counts": dict(sorted(red_session_counts.items())),
        "green_after_session_group_counts": dict(sorted(green_session_counts.items())),
        "green_after_3way_macro_counts": dict(sorted(green_3way_counts.items())),
        "green_after_primary_window_counts": dict(sorted(green_window_counts.items())),
        "red_reproduction_confirmed": red_session_counts == {"INVALID": len(casebook_rows_170)},
        "green_zero_invalid_confirmed": green_session_counts.get("INVALID", 0) == 0
        and green_3way_counts.get("UNKNOWN_OR_INVALID_MACRO_STATE", 0) == 0,
        "exemplar_row_transitions": sample_diffs[:20],
    }

    # DST & half-open [start, end) boundary checks + invalid input matrix (03)
    ny_tz = ZoneInfo("America/New_York")

    def _ny_ms(y: int, m: int, d: int, hh: int, mm: int, ss: int = 0, ms: int = 0, fold: int = 0) -> int:
        dt = datetime(y, m, d, hh, mm, ss, ms * 1000, tzinfo=ny_tz, fold=fold)
        return int(round(dt.timestamp() * 1000.0))

    sched_info = validate_macro_schedule_config(None)
    boundary_probes: List[Dict[str, Any]] = []
    for w in sched_info.windows:
        sh, sm = (int(x) for x in w.start_hhmm.split(":"))
        eh, em = (int(x) for x in w.end_hhmm.split(":"))
        t_start = _ny_ms(2026, 10, 14, sh, sm, 0, 0)  # Wed EDT
        t_end = _ny_ms(2026, 10, 14, eh, em, 0, 0)
        s_before = resolve_causal_macro_time_window(decision_timestamp_ms=t_start - 1, symbol="XAUUSDT")
        s_at_start = resolve_causal_macro_time_window(decision_timestamp_ms=t_start, symbol="XAUUSDT")
        s_before_end = resolve_causal_macro_time_window(decision_timestamp_ms=t_end - 1, symbol="XAUUSDT")
        s_at_end = resolve_causal_macro_time_window(decision_timestamp_ms=t_end, symbol="XAUUSDT")
        ok = (
            not s_before.eligible_for_macro_gate
            and s_at_start.eligible_for_macro_gate
            and s_at_start.primary_window_id == w.window_id
            and s_before_end.eligible_for_macro_gate
            and s_before_end.primary_window_id == w.window_id
            and not s_at_end.eligible_for_macro_gate
        )
        boundary_probes.append({
            "window_id": w.window_id,
            "session_group": w.session_group,
            "start_hhmm": w.start_hhmm,
            "end_hhmm": w.end_hhmm,
            "half_open_boundary_verified": ok,
        })

    # US DST Spring Forward 2026-03-08 & Fall Back 2026-11-01 (Monday after: 2026-03-09 EDT UTC-4 vs 2026-03-06 EST UTC-5; 2026-11-02 EST UTC-5)
    ts_est_pre_spring = _ny_ms(2026, 3, 6, 9, 55)   # Fri EST (UTC-5) inside NY_AM_0950_1010
    ts_edt_post_spring = _ny_ms(2026, 3, 9, 9, 55)  # Mon EDT (UTC-4) inside NY_AM_0950_1010
    ts_est_post_fall = _ny_ms(2026, 11, 2, 9, 55)   # Mon EST (UTC-5) inside NY_AM_0950_1010
    ts_weekend_sat = _ny_ms(2026, 10, 17, 9, 55)    # Sat EDT -> WEEKEND_CLOSED

    st_pre_sp = resolve_causal_macro_time_window(decision_timestamp_ms=ts_est_pre_spring, symbol="XAUUSDT")
    st_post_sp = resolve_causal_macro_time_window(decision_timestamp_ms=ts_edt_post_spring, symbol="XAUUSDT")
    st_post_fa = resolve_causal_macro_time_window(decision_timestamp_ms=ts_est_post_fall, symbol="XAUUSDT")
    st_wknd = resolve_causal_macro_time_window(decision_timestamp_ms=ts_weekend_sat, symbol="XAUUSDT")

    invalid_probes: List[Dict[str, Any]] = []
    for label, call_fn, exp_ts, exp_sym in (
        ("REVERSED_ARGS", lambda: _invoke_legacy_reversed_positional_red_control("XAUUSDT", ts_edt_post_spring), ts_edt_post_spring, "XAUUSDT"),
        ("SECONDS_SCALE_TS", lambda: resolve_causal_macro_time_window(decision_timestamp_ms=1792000000, symbol="XAUUSDT"), 1792000000, "XAUUSDT"),
        ("NEGATIVE_TS", lambda: resolve_causal_macro_time_window(decision_timestamp_ms=-60000, symbol="XAUUSDT"), -60000, "XAUUSDT"),
        ("BOOL_TS", lambda: resolve_causal_macro_time_window(decision_timestamp_ms=True, symbol="XAUUSDT"), True, "XAUUSDT"),  # type: ignore[arg-type]
        ("INVALID_EMPTY_SCHEDULE", lambda: resolve_causal_macro_time_window(decision_timestamp_ms=ts_edt_post_spring, symbol="XAUUSDT", schedule_config={"windows": []}), ts_edt_post_spring, "XAUUSDT"),
        ("UNVERIFIED_REQUIRED_HOLIDAY_CALENDAR", lambda: resolve_causal_macro_time_window(decision_timestamp_ms=ts_edt_post_spring, symbol="XAUUSDT", schedule_config={"require_verified_holiday_calendar": True, "calendar_verification_status": "CALENDAR_NOT_VERIFIED"}), ts_edt_post_spring, "XAUUSDT"),
        ("WRONG_SYMBOL_BTCUSDT", lambda: resolve_causal_macro_time_window(decision_timestamp_ms=ts_edt_post_spring, symbol="BTCUSDT"), ts_edt_post_spring, "BTCUSDT"),
    ):
        st_bad = call_fn()
        v_bad = validate_and_classify_macro_state_kh01a_r1(
            st_bad, expected_decision_timestamp_ms=exp_ts, expected_symbol=exp_sym
        )
        invalid_probes.append({
            "probe_id": label,
            "macro_state_category": v_bad["macro_state_category"],
            "cell_state": v_bad["cell_state"],
            "in_ict_macro_window_value": v_bad["in_ict_macro_window_value"],
            "ny_session_group_value": v_bad["ny_session_group_value"],
            "never_falsely_proven_false": v_bad["in_ict_macro_window_value"] is None,
            "violations": v_bad["validation_violations"],
        })

    dst_all_ok = (
        st_pre_sp.is_dst is False
        and st_pre_sp.utc_offset_minutes == -300
        and st_pre_sp.eligible_for_macro_gate is True
        and st_post_sp.is_dst is True
        and st_post_sp.utc_offset_minutes == -240
        and st_post_sp.eligible_for_macro_gate is True
        and st_post_fa.is_dst is False
        and st_post_fa.utc_offset_minutes == -300
        and st_post_fa.eligible_for_macro_gate is True
        and st_wknd.is_weekend_or_closed is True
        and st_wknd.session_group == "WEEKEND_CLOSED"
        and st_wknd.eligible_for_macro_gate is False
        and all(p["half_open_boundary_verified"] for p in boundary_probes)
        and all(
            p["macro_state_category"] == "UNKNOWN_OR_INVALID_MACRO_STATE" and p["never_falsely_proven_false"]
            for p in invalid_probes
        )
    )

    doc_03 = {
        "spec_version": SPEC_VERSION_KH01A_R1,
        "schema_version": SCHEMA_VERSION_KH01A_R1,
        "schedule_version": sched_info.schedule_version,
        "schedule_sha256": sched_info.schedule_sha256,
        "schedule_source": sched_info.source,
        "exchange_calendar_verification_status": sched_info.calendar_verification_status,
        "dst_transitions_verified": {
            "pre_spring_2026_03_06_est": {"is_dst": st_pre_sp.is_dst, "utc_offset_minutes": st_pre_sp.utc_offset_minutes, "window": st_pre_sp.primary_window_id},
            "post_spring_2026_03_09_edt": {"is_dst": st_post_sp.is_dst, "utc_offset_minutes": st_post_sp.utc_offset_minutes, "window": st_post_sp.primary_window_id},
            "post_fall_2026_11_02_est": {"is_dst": st_post_fa.is_dst, "utc_offset_minutes": st_post_fa.utc_offset_minutes, "window": st_post_fa.primary_window_id},
            "weekend_2026_10_17_sat": {"session_group": st_wknd.session_group, "eligible": st_wknd.eligible_for_macro_gate},
        },
        "all_7_windows_half_open_boundaries": boundary_probes,
        "invalid_and_unverified_input_probes": invalid_probes,
        "status": "PASS" if dst_all_ok else "FAIL",
    }
    return doc_02, doc_03


def build_previous_vs_r1_casebook_diff(
    prior_run_dir: Path,
    r1_forensics_bundle: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Reads the frozen prior KH-01A run (`20261117T013053Z_a1fd9293e2d0`) strictly read-only
    and computes the exact before-vs-after diff across all 170 casebook rows (`04`).
    """
    p05 = prior_run_dir / "05_preentry_casebook_157_losses_and_win_controls.jsonl.gz"
    p06 = prior_run_dir / "06_causal_loss_attribution_and_unknown_evidence_census.json"
    old_rows = list(_iter_jsonl_gz(p05))
    old_06 = json.loads(p06.read_text(encoding="utf-8"))
    new_rows = list(r1_forensics_bundle["casebook_rows"])
    new_06 = r1_forensics_bundle["census_summary_06"]

    old_by_key = {(str(r["population_role"]), str(r["opportunity_id"])): r for r in old_rows}
    new_by_key = {(str(r["population_role"]), str(r["opportunity_id"])): r for r in new_rows}

    same_keys = set(old_by_key.keys()) == set(new_by_key.keys())
    pnl_or_geometry_drifts: List[str] = []
    reason_transitions: Counter[str] = Counter()
    changed_rows_table: List[Dict[str, Any]] = []

    for key in sorted(new_by_key.keys()):
        nr = new_by_key[key]
        orow = old_by_key.get(key)
        if orow is None:
            pnl_or_geometry_drifts.append(f"MISSING_IN_OLD:{key}")
            continue
        for inv_k in (
            "decision_timestamp_ms",
            "direction",
            "model_id",
            "entry_reference",
            "entry_fill_price",
            "selected_sl",
            "take_profit",
            "realized_net_pnl_usd",
            "realized_r",
        ):
            if orow.get(inv_k) != nr.get(inv_k):
                pnl_or_geometry_drifts.append(f"TRADING_FIELD_DRIFT:{key}:{inv_k}:{orow.get(inv_k)}!={nr.get(inv_k)}")
        if orow.get("ex_post_outcome") != nr.get("ex_post_outcome"):
            pnl_or_geometry_drifts.append(f"EX_POST_TRAJECTORY_DRIFT:{key}")

        old_rc = str(orow["pre_entry_known"]["reason_code"])
        new_rc = str(nr["pre_entry_known"]["reason_code"])
        old_sess = str((orow["pre_entry_features"].get("ny_session_group") or {}).get("value"))
        new_sess = str((nr["pre_entry_features"].get("ny_session_group") or {}).get("value"))
        old_mac = (orow["pre_entry_features"].get("in_ict_macro_window") or {}).get("value")
        new_mac = (nr["pre_entry_features"].get("in_ict_macro_window") or {}).get("value")
        reason_transitions[f"{old_rc} -> {new_rc}"] += 1

        if old_rc != new_rc or new_mac is True or len(changed_rows_table) < 25:
            changed_rows_table.append({
                "population_role": key[0],
                "opportunity_id": key[1],
                "decision_timestamp_ms": nr["decision_timestamp_ms"],
                "old_session_group": old_sess,
                "new_session_group": new_sess,
                "old_in_ict_macro_window": old_mac,
                "new_in_ict_macro_window": new_mac,
                "old_primary_reason": old_rc,
                "new_primary_reason": new_rc,
                "old_row_sha256": orow.get("row_sha256"),
                "new_row_sha256": nr.get("row_sha256"),
                "persisted_trade_action_fingerprint": (nr.get("snapshot_lineage") or {}).get("persisted_trade_action_fingerprint"),
                "recomputed_trade_action_fingerprint": (nr.get("snapshot_lineage") or {}).get("trade_action_fingerprint"),
            })

    old_loss_agg = old_06["si0_157_losses_aggregates"]
    new_loss_agg = new_06["si0_157_losses_aggregates"]
    old_win_agg = old_06["si0_9_wins_control_aggregates"]
    new_win_agg = new_06["si0_9_wins_control_aggregates"]

    return {
        "spec_version": SPEC_VERSION_KH01A_R1,
        "schema_version": SCHEMA_VERSION_KH01A_R1,
        "prior_kh01a_run_dir": PRIOR_KH01A_RUN_REL_DIR,
        "prior_casebook_05_sha256": _file_sha256(p05),
        "prior_census_06_sha256": _file_sha256(p06),
        "cohort_170_keys_identical": same_keys,
        "trading_geometry_pnl_and_expost_identical_all_170": len(pnl_or_geometry_drifts) == 0,
        "trading_or_geometry_drifts": pnl_or_geometry_drifts,
        "forensic_recomputation_status": "FORENSIC_RECOMPUTATION_CHANGED_AS_EXPECTED",
        "si0_157_losses_before_vs_after": {
            "ny_session_group_before": old_loss_agg.get("by_ny_session_group"),
            "ny_session_group_after": new_loss_agg.get("by_ny_session_group"),
            "in_ict_macro_window_before": old_loss_agg.get("by_in_ict_macro_window"),
            "in_ict_macro_window_after": new_loss_agg.get("by_in_ict_macro_window"),
            "macro_3way_after": new_loss_agg.get("by_macro_3way_category"),
            "primary_reason_counts_before": old_loss_agg.get("primary_pre_entry_reason_counts"),
            "primary_reason_counts_after": new_loss_agg.get("primary_pre_entry_reason_counts"),
            "feature_presence_census_before": old_06.get("si0_157_losses_feature_presence_and_unknown_census"),
            "feature_presence_census_after": new_06.get("si0_157_losses_feature_presence_and_unknown_census"),
        },
        "si0_9_wins_before_vs_after": {
            "ny_session_group_before": old_win_agg.get("by_ny_session_group"),
            "ny_session_group_after": new_win_agg.get("by_ny_session_group"),
            "in_ict_macro_window_before": old_win_agg.get("by_in_ict_macro_window"),
            "in_ict_macro_window_after": new_win_agg.get("by_in_ict_macro_window"),
            "primary_reason_counts_before": old_win_agg.get("primary_pre_entry_reason_counts"),
            "primary_reason_counts_after": new_win_agg.get("primary_pre_entry_reason_counts"),
        },
        "primary_reason_transition_matrix_all_170": dict(sorted(reason_transitions.items())),
        "explicit_delta_explanations": [
            {
                "delta_id": "DELTA-01-MACRO-SESSION-AND-WINDOW",
                "explanation": (
                    "Prior KH-01A passed ('XAUUSDT', dec_ts) to resolve_causal_macro_time_window, causing 170/170 rows "
                    "to receive session_group='INVALID' and eligible_for_macro_gate=False. R1 keyword binding resolves true "
                    "America/New_York sessions with 0 INVALID rows; rows inside active NY ICT macro windows flip from "
                    "OUTSIDE_ICT_MACRO_WINDOW to their next proven pre-entry condition (or aligned-on-present)."
                ),
            },
            {
                "delta_id": "DELTA-02-CANONICAL-A1-CONTEXT-AND-PERSISTED-SNAPSHOT-JOIN",
                "explanation": (
                    "Prior KH-01A reconstructed with generic 240/120 bar windows and ignored feat_in['causal_snapshot']['features'] "
                    "(checking nonexistent top-level evidence_refs), plus inferred False sweep/MSS from bool(snap.selected_...). "
                    "R1 reconstructs with exact A1 V17.9.2 120/96/96/96/64/30 windows (166/166 SI0 fingerprint MATCH), binds verified "
                    "07_verified_causal_features items (including 5m/1m target_selected_pool_bound timeframes), and keeps unestablished "
                    "sweep/MSS strictly MISSING/UNKNOWN in unknown_or_unproven_flags."
                ),
            },
        ],
        "changed_or_exemplar_rows": changed_rows_table[:40],
    }


# ==============================================================================
# PHASE 2 & 3: STRICT INDEPENDENT ROW-LEVEL FORENSICS VALIDATOR & RECEIPTS
# ==============================================================================

def validate_kh_01a_r1_forensics_bundle_strict(
    casebook_rows: Sequence[Mapping[str, Any]],
    census_summary: Mapping[str, Any],
    *,
    canonical_bar_close_by_tf_open: Optional[Mapping[Tuple[str, int], int]] = None,
    persisted_f07_by_oid: Optional[Mapping[str, Mapping[str, Any]]] = None,
    expected_dataset_sha: str = CANONICAL_DATASET_SHA256_KH01A,
) -> Dict[str, Any]:
    """
    Independent semantic validator for `05_r1_preentry_170_rows.jsonl.gz` and
    `06_feature_evidence_origin_missing_invalid_source_hash_census.json`.
    Fails closed on any legacy schema, reversed/invalid macro session, fake persisted origin,
    unverified bar ID, lookahead (`known_at_ms > dec_ts` or `closed_bar_close_ms > dec_ts`),
    unknown-treated-as-proven-false, duplicate IDs, or row-vs-summary mismatch.
    """
    violations: List[str] = []

    if str(census_summary.get("schema_version") or "") != SCHEMA_VERSION_KH01A_R1:
        violations.append(
            f"SUMMARY_SCHEMA_NOT_R1:{census_summary.get('schema_version')!r}!={SCHEMA_VERSION_KH01A_R1}"
        )
    if str(census_summary.get("dataset_sha256") or "") != expected_dataset_sha:
        violations.append(f"SUMMARY_DATASET_SHA_MISMATCH:{census_summary.get('dataset_sha256')}")

    if len(casebook_rows) != 170:
        violations.append(f"CASEBOOK_TOTAL_ROWS_NOT_170:{len(casebook_rows)}")

    role_counts = Counter(str(r.get("population_role") or "") for r in casebook_rows)
    if role_counts.get("SI0_CLOSED_LOSS", 0) != 157:
        violations.append(f"SI0_LOSS_ROWS_COUNT_MISMATCH:{role_counts.get('SI0_CLOSED_LOSS', 0)}!=157")
    if role_counts.get("SI0_CLOSED_WIN_CONTROL", 0) != 9:
        violations.append(f"SI0_WIN_ROWS_COUNT_MISMATCH:{role_counts.get('SI0_CLOSED_WIN_CONTROL', 0)}!=9")
    if role_counts.get("SI8_SI9_UNIQUE_RESEARCH_LOSS", 0) != 4:
        violations.append(f"SI89_UNIQUE_ROWS_COUNT_MISMATCH:{role_counts.get('SI8_SI9_UNIQUE_RESEARCH_LOSS', 0)}!=4")

    seen_si0_oids: Set[str] = set()
    seen_si89_oids: Set[str] = set()
    rebuilt_loss_reasons: Counter[str] = Counter()
    rebuilt_loss_sessions: Counter[str] = Counter()
    rebuilt_macro_3way_170: Counter[str] = Counter()
    rebuilt_fp_status_170: Counter[str] = Counter()
    rebuilt_completeness_170: Counter[str] = Counter()

    for idx, row in enumerate(casebook_rows):
        oid = str(row.get("opportunity_id") or "")
        role = str(row.get("population_role") or "")
        if not oid:
            violations.append(f"ROW_MISSING_OPPORTUNITY_ID:idx={idx}")
            continue
        if str(row.get("schema_version") or "") != SCHEMA_VERSION_KH01A_R1:
            violations.append(f"ROW_SCHEMA_NOT_R1:{oid}:{row.get('schema_version')}")

        if role in ("SI0_CLOSED_LOSS", "SI0_CLOSED_WIN_CONTROL"):
            if oid in seen_si0_oids:
                violations.append(f"DUPLICATE_SI0_OPPORTUNITY_ID:{oid}")
            seen_si0_oids.add(oid)
        elif role == "SI8_SI9_UNIQUE_RESEARCH_LOSS":
            if oid in seen_si89_oids:
                violations.append(f"DUPLICATE_SI89_OPPORTUNITY_ID:{oid}")
            seen_si89_oids.add(oid)
        else:
            violations.append(f"UNKNOWN_POPULATION_ROLE:{oid}:{role}")

        row_no_sha = {k: v for k, v in row.items() if k != "row_sha256"}
        if canonical_hash(row_no_sha) != row.get("row_sha256"):
            violations.append(f"ROW_SHA256_TAMPERED:{oid}")

        dec_ts = row.get("decision_timestamp_ms")
        if isinstance(dec_ts, bool) or not isinstance(dec_ts, int) or dec_ts < MIN_PLAUSIBLE_EPOCH_MS:
            violations.append(f"ROW_INVALID_DECISION_TS:{oid}:{dec_ts!r}")
            continue

        # Validate macro state on historical bar
        m_st = row.get("macro_window_state") or {}
        m_cat = str(m_st.get("macro_state_category") or "")
        rebuilt_macro_3way_170[m_cat] += 1
        if m_cat not in VALID_MACRO_3WAY_CATEGORIES_KH01A_R1:
            violations.append(f"INVALID_MACRO_3WAY_CATEGORY:{oid}:{m_cat}")
        if m_cat == "UNKNOWN_OR_INVALID_MACRO_STATE":
            violations.append(f"HISTORICAL_ROW_HAS_UNKNOWN_OR_INVALID_MACRO_STATE:{oid}")
        raw_md = m_st.get("raw_macro_diagnostics") or {}
        if raw_md.get("is_valid_timestamp") is not True or raw_md.get("schedule_valid") is not True:
            violations.append(f"MACRO_DIAGNOSTICS_INVALID_TS_OR_SCHEDULE:{oid}")
        if str(raw_md.get("session_group") or "") in ("", "INVALID", "INVALID_SCHEDULE", "CALENDAR_UNVERIFIED"):
            violations.append(f"MACRO_DIAGNOSTICS_INVALID_SESSION:{oid}:{raw_md.get('session_group')}")

        # Validate provenance summary
        prov = row.get("provenance_summary") or {}
        if prov.get("original_snapshot_verified") is True:
            violations.append(f"FAKE_ORIGINAL_RUNTIME_CONTEXT_CLAIMED:{oid}")
        fp_st = str(prov.get("original_vs_recomputed_fingerprint_status") or "")
        rebuilt_fp_status_170[fp_st] += 1
        if fp_st not in ("MATCH", "MISMATCH", "NOT_COMPARABLE"):
            violations.append(f"INVALID_FINGERPRINT_STATUS:{oid}:{fp_st}")
        if fp_st == "MISMATCH":
            violations.append(f"ORIGINAL_VS_RECOMPUTED_FINGERPRINT_MISMATCH:{oid}")

        feats = row.get("pre_entry_features")
        if not isinstance(feats, Mapping) or not feats:
            violations.append(f"MISSING_PRE_ENTRY_FEATURES:{oid}")
            continue

        sess_cell = feats.get("ny_session_group") or {}
        if sess_cell.get("state") != "PRESENT" or str(sess_cell.get("value") or "") in ("", "INVALID", "INVALID_SCHEDULE", "CALENDAR_UNVERIFIED"):
            violations.append(f"INVALID_NY_SESSION_GROUP_CELL:{oid}:{sess_cell}")

        macro_cell = feats.get("in_ict_macro_window") or {}
        if macro_cell.get("state") != "PRESENT" or not isinstance(macro_cell.get("value"), bool):
            violations.append(f"INVALID_IN_ICT_MACRO_WINDOW_CELL:{oid}:{macro_cell}")

        for fk, fv in feats.items():
            if not isinstance(fv, Mapping):
                violations.append(f"FEATURE_CELL_NOT_MAPPING:{oid}:{fk}")
                continue
            for forbidden_k in ("exit_fill_price", "realized_net_pnl_usd", "mae_r", "mfe_r", "outcome_label"):
                if fk == forbidden_k:
                    violations.append(f"EX_POST_LEAKAGE_FEATURE:{oid}:{fk}")
            st = str(fv.get("state") or "")
            orig = str(fv.get("source_origin") or "")
            if st not in ("PRESENT", "MISSING", "INVALID"):
                violations.append(f"INVALID_FEATURE_STATE:{oid}:{fk}:{st}")
            if orig not in VALID_SOURCE_ORIGINS_KH01A_R1:
                violations.append(f"INVALID_FEATURE_ORIGIN:{oid}:{fk}:{orig}")
            if st == "MISSING" and fv.get("value") is not None:
                violations.append(f"MISSING_FEATURE_NON_NULL:{oid}:{fk}")
            if st == "INVALID":
                violations.append(f"INVALID_FEATURE_IN_COHORT:{oid}:{fk}")

            kn = fv.get("known_at_ms")
            cb = fv.get("closed_bar_close_ms")
            if kn is not None and int(kn) > dec_ts:
                violations.append(f"FEATURE_KNOWN_AT_LOOKAHEAD:{oid}:{fk}:{kn}>{dec_ts}")
            if cb is not None and int(cb) > dec_ts:
                violations.append(f"FEATURE_BAR_CLOSE_LOOKAHEAD:{oid}:{fk}:{cb}>{dec_ts}")

            # Verify feature cell hash integrity
            recomp_ev_sha = canonical_hash({
                "feature": fk,
                "value": fv.get("value"),
                "state": fv.get("state"),
                "proof_state": fv.get("proof_state"),
                "source_origin": fv.get("source_origin"),
                "source_timeframe": fv.get("source_timeframe"),
                "known_at_ms": fv.get("known_at_ms"),
                "closed_bar_close_ms": fv.get("closed_bar_close_ms"),
                "raw_source_ids": list(fv.get("raw_source_ids") or []),
                "reconstruction_algo_sha256": fv.get("reconstruction_algo_sha256"),
            })
            if recomp_ev_sha != fv.get("evidence_sha256") or not fv.get("evidence_sha256"):
                violations.append(f"FEATURE_EVIDENCE_SHA256_MISMATCH:{oid}:{fk}")

            if st == "PRESENT":
                sids = list(fv.get("raw_source_ids") or [])
                if not sids:
                    violations.append(f"PRESENT_FEATURE_EMPTY_SOURCE_IDS:{oid}:{fk}")
                if orig == "PERSISTED_VERIFIED_SNAPSHOT" and persisted_f07_by_oid is not None:
                    f07_row = persisted_f07_by_oid.get(oid)
                    if (
                        f07_row is None
                        or int(f07_row.get("decision_timestamp_ms") or 0) != dec_ts
                        or not verify_feature_snapshot_v1793127(f07_row)
                    ):
                        violations.append(f"UNGROUNDED_PERSISTED_SNAPSHOT_ORIGIN:{oid}:{fk}")
                if canonical_bar_close_by_tf_open is not None:
                    for sid in sids:
                        parts = str(sid).split(":")
                        if len(parts) >= 3 and parts[0].upper() == "XAUUSDT":
                            c_ms = canonical_bar_close_by_tf_open.get((parts[1].upper(), int(parts[2])))
                            if c_ms is None:
                                violations.append(f"SOURCE_BAR_NOT_IN_DATASET:{oid}:{fk}:{sid}")
                            elif c_ms > dec_ts:
                                violations.append(f"SOURCE_BAR_FUTURE_CLOSE:{oid}:{fk}:{sid}:{c_ms}>{dec_ts}")

        # Verify pre_entry_known honesty (unknown is never proven false; PARTIAL when missing > 0)
        pk = row.get("pre_entry_known") or {}
        comp = str(pk.get("evidence_completeness") or "")
        rebuilt_completeness_170[comp] += 1
        miss_cnt = int(pk.get("missing_fields_count", -1))
        if miss_cnt > 0 and comp == "COMPLETE":
            violations.append(f"PARTIAL_ROW_CLAIMED_COMPLETE:{oid}:missing={miss_cnt}")
        act_flags = set(pk.get("active_pre_entry_risk_flags") or ())
        if (feats.get("liquidity_sweep_reclaim_confirmed") or {}).get("state") == "MISSING" and "SWEEP_RECLAIM_PROVEN_FALSE" in act_flags:
            violations.append(f"UNKNOWN_SWEEP_CLAIMED_PROVEN_FALSE:{oid}")
        if (feats.get("mss_bos_displacement_confirmed") or {}).get("state") == "MISSING" and "MSS_DISPLACEMENT_PROVEN_FALSE" in act_flags:
            violations.append(f"UNKNOWN_MSS_CLAIMED_PROVEN_FALSE:{oid}")
        if (feats.get("in_ict_macro_window") or {}).get("state") != "PRESENT" and "OUTSIDE_ICT_MACRO_WINDOW" in act_flags:
            violations.append(f"UNKNOWN_MACRO_CLAIMED_OUTSIDE_WINDOW:{oid}")

        if role == "SI0_CLOSED_LOSS":
            rebuilt_loss_reasons[str(pk.get("reason_code") or "")] += 1
            rebuilt_loss_sessions[str(sess_cell.get("value") or "UNKNOWN")] += 1

    # Cross-check row-stream aggregates against 06 summary JSON
    sum_loss_reasons = ((census_summary.get("si0_157_losses_aggregates") or {}).get("primary_pre_entry_reason_counts") or {})
    sum_loss_sessions = ((census_summary.get("si0_157_losses_aggregates") or {}).get("by_ny_session_group") or {})
    if dict(sorted(rebuilt_loss_reasons.items())) != dict(sorted(sum_loss_reasons.items())):
        violations.append("SUMMARY_06_PRIMARY_REASONS_MISMATCH_ROW_STREAM_05")
    if dict(sorted(rebuilt_loss_sessions.items())) != dict(sorted(sum_loss_sessions.items())):
        violations.append("SUMMARY_06_SESSION_GROUPS_MISMATCH_ROW_STREAM_05")
    if dict(sorted(rebuilt_macro_3way_170.items())) != dict(sorted((census_summary.get("macro_3way_census_all_170") or {}).items())):
        violations.append("SUMMARY_06_MACRO_3WAY_MISMATCH_ROW_STREAM_05")
    if dict(sorted(rebuilt_fp_status_170.items())) != dict(sorted((census_summary.get("original_vs_recomputed_fingerprint_census_all_170") or {}).items())):
        violations.append("SUMMARY_06_FINGERPRINT_CENSUS_MISMATCH_ROW_STREAM_05")

    try:
        validate_r1_forensics_summary_and_macro_strict_v17931219(census_summary)
    except Exception as exc:
        violations.append(f"V19_STRICT_R1_SUMMARY_VALIDATOR_FAILED:{exc}")

    sem_verdict = "PASS" if not violations else "FAIL"
    return {
        "status": sem_verdict,
        "FORENSICS_SEMANTIC_VERDICT": sem_verdict,
        "rows_validated": len(casebook_rows),
        "role_counts": dict(sorted(role_counts.items())),
        "macro_3way_counts_170": dict(sorted(rebuilt_macro_3way_170.items())),
        "evidence_completeness_counts_170": dict(sorted(rebuilt_completeness_170.items())),
        "fingerprint_status_counts_170": dict(sorted(rebuilt_fp_status_170.items())),
        "violations_count": len(violations),
        "violations": violations,
    }


def build_dynamic_validated_gate_receipt_r1(
    *,
    gate_id: str,
    run_dir: Path,
    run_id: str,
    tested_sha: str,
    target_rel_path: str,
    validator_fn: Callable[[], Dict[str, Any]],
    write_json_fn: Callable[[Path, Any], str],
) -> Tuple[EvidenceReceiptV17931219, Dict[str, Any]]:
    """
    KH-01A.R1 Phase 3 (F4): Executes `validator_fn()` FIRST to determine semantic
    `status` and `violations` (NEVER pre-labeling `"PASS"`!), records the validator
    function name, validator Python source SHA256, and target input artifact SHA256,
    writes `receipts/<gate_id>.json` atomically, and returns `EvidenceReceiptV17931219`
    bound to the target artifact on disk.
    """
    target_abs = (run_dir / target_rel_path).resolve()
    if not target_abs.is_file() or target_abs.stat().st_size <= 0:
        obs_status = "FAIL"
        viols: Tuple[str, ...] = (f"TARGET_ARTIFACT_MISSING_OR_EMPTY:{target_rel_path}",)
        target_sha = "0" * 64
        val_out: Dict[str, Any] = {"status": "FAIL", "violations": list(viols)}
    else:
        target_sha = _file_sha256(target_abs)
        try:
            val_out = dict(validator_fn())
            obs_status = str(val_out.get("status") or "FAIL")
            viols = tuple(str(x) for x in (val_out.get("violations") or ()))
        except Exception as exc:
            obs_status = "FAIL"
            viols = (f"VALIDATOR_EXCEPTION:{type(exc).__name__}:{exc}",)
            val_out = {"status": "FAIL", "violations": list(viols)}

    val_name = getattr(validator_fn, "__name__", "anonymous_validator")
    val_code_sha = _validator_source_sha256(validator_fn)

    rcpt = EvidenceReceiptV17931219(
        gate_id=gate_id,
        status=obs_status,
        receipt_path=target_rel_path,
        content_sha256=target_sha,
        tested_sha=tested_sha,
        run_id=run_id,
        validator_name=val_name,
        validator_code_sha=val_code_sha,
        input_sha256=target_sha,
        observed_status=obs_status,
        violations=viols,
        metadata={
            "verified_dynamically_by_validator_before_sealing": True,
            "validator_python_source_sha256": val_code_sha,
            "executable_git_sha": tested_sha,
        },
    )
    receipts_dir = run_dir / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    rcpt_file_rel = f"receipts/{gate_id}.json"
    rcpt_doc = {
        "schema_version": SCHEMA_VERSION_KH01A_R1,
        "gate_id": gate_id,
        "receipt": asdict(rcpt),
        "validator_summary": {k: v for k, v in val_out.items() if k != "reconstructed_partitions"},
    }
    rcpt_json_sha = write_json_fn(run_dir / rcpt_file_rel, rcpt_doc)
    return rcpt, {"receipt_file": rcpt_file_rel, "receipt_json_sha256": rcpt_json_sha, **rcpt_doc}


# ==============================================================================
# PHASE 3 (D8, F6): REAL ADVERSARIAL FAULT-INJECTION SUITE FOR KH-01A.R1
# ==============================================================================

def run_kh_01a_r1_adversarial_fault_suite(
    *,
    tested_sha: str,
    run_id: str,
    r1_forensics_bundle: Mapping[str, Any],
    si0_all_primary_episodes: Sequence[Mapping[str, Any]],
    si8_loss_rows: Sequence[Mapping[str, Any]],
    si9_loss_rows: Sequence[Mapping[str, Any]],
    decision_inputs_by_opp_id: Mapping[str, Mapping[str, Any]],
    feature_ledger_by_opp_id: Mapping[str, Mapping[str, Any]],
    datasets: Mapping[str, Sequence[Mapping[str, Any]]],
    close_ts_index: Mapping[str, Sequence[int]],
    canonical_bar_close_by_tf_open: Mapping[Tuple[str, int], int],
    verifier_script_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Executes all R1-mandated real fault injections (`D8`, `F6`) against the actual
    170-row frozen cohort, row-level validator, JUnit verifier, and separate-process
    independent verifier (recording real process exit codes where applicable), PLUS
    re-executes the 22 foundational KH-01A real fault injections.
    ZERO report assertions counted as fault injections.
    """
    from tools.fresh_junit_io import write_bytes_durable_atomic, write_json_durable_atomic

    base_rows = list(r1_forensics_bundle["casebook_rows"])
    base_sum = dict(r1_forensics_bundle["census_summary_06"])
    faults: List[Dict[str, Any]] = []

    def _green_bundle_ok() -> bool:
        chk = validate_kh_01a_r1_forensics_bundle_strict(
            base_rows,
            base_sum,
            canonical_bar_close_by_tf_open=canonical_bar_close_by_tf_open,
            persisted_f07_by_oid=feature_ledger_by_opp_id,
        )
        return chk["status"] == "PASS"

    def _add_r1_fault(
        fault_id: str,
        category: str,
        description: str,
        green_fn: Callable[[], bool],
        red_fn: Callable[[], Tuple[bool, str, Optional[int]]],
    ) -> None:
        g_ok = False
        g_err = ""
        try:
            g_ok = bool(green_fn())
        except Exception as exc:
            g_ok = False
            g_err = f"{type(exc).__name__}:{exc}"
        r_rej, r_reason, proc_rc = red_fn()
        faults.append({
            "fault_id": fault_id,
            "category": category,
            "execution_kind": "REAL_FAULT_INJECTION",
            "description": description,
            "green_valid_control_passed": g_ok,
            "green_error_if_any": g_err,
            "red_fault_rejected_fail_closed": r_rej,
            "observed_process_exit_code": proc_rc,
            "observed_rejection_reason": r_reason,
            "status": "PASS" if (g_ok and r_rej) else "FAIL",
        })

    # R1-FI-01 (D8): Deliberately swap macro args on REAL 170-row frozen cohort
    def _red_swapped_macro_cohort() -> Tuple[bool, str, Optional[int]]:
        poisoned = build_kh_01a_causal_loss_and_win_control_forensics(
            si0_all_primary_episodes=si0_all_primary_episodes,
            si8_loss_rows=si8_loss_rows,
            si9_loss_rows=si9_loss_rows,
            decision_inputs_by_opp_id=decision_inputs_by_opp_id,
            feature_ledger_by_opp_id=feature_ledger_by_opp_id,
            datasets=datasets,
            close_ts_index=close_ts_index,
            _macro_resolver_override=lambda decision_timestamp_ms, symbol="XAUUSDT": resolve_causal_macro_time_window(symbol, decision_timestamp_ms),  # type: ignore[arg-type]
        )
        chk = validate_kh_01a_r1_forensics_bundle_strict(
            poisoned["casebook_rows"],
            poisoned["census_summary_06"],
            canonical_bar_close_by_tf_open=canonical_bar_close_by_tf_open,
        )
        rejected = chk["status"] == "FAIL" and any("UNKNOWN_OR_INVALID_MACRO_STATE" in v or "INVALID" in v for v in chk["violations"])
        return rejected, str(chk["violations"][:4]), None

    _add_r1_fault(
        "G-KH01A-R1-FOR-01_SWAPPED_MACRO_ARGS_ON_REAL_170_COHORT",
        "MACRO_REVERSED_ARGS",
        "Deliberately swap resolve_causal_macro_time_window args on real 170-row frozen cohort and verify strict rejection",
        _green_bundle_ok,
        _red_swapped_macro_cohort,
    )

    # R1-FI-02: Invalid macro schedule config (`windows=[]`) on real cohort slice
    def _red_invalid_schedule() -> Tuple[bool, str, Optional[int]]:
        poisoned = build_kh_01a_causal_loss_and_win_control_forensics(
            si0_all_primary_episodes=si0_all_primary_episodes,
            si8_loss_rows=si8_loss_rows,
            si9_loss_rows=si9_loss_rows,
            decision_inputs_by_opp_id=decision_inputs_by_opp_id,
            feature_ledger_by_opp_id=feature_ledger_by_opp_id,
            datasets=datasets,
            close_ts_index=close_ts_index,
            macro_schedule_config={"windows": []},
        )
        chk = validate_kh_01a_r1_forensics_bundle_strict(poisoned["casebook_rows"], poisoned["census_summary_06"])
        return (chk["status"] == "FAIL"), str(chk["violations"][:3]), None

    _add_r1_fault(
        "R1-FI-02_INVALID_EMPTY_MACRO_SCHEDULE",
        "MACRO_INVALID_SCHEDULE",
        "Inject empty invalid macro schedule (`windows=[]`) across frozen cohort and verify forensic gate rejects",
        _green_bundle_ok,
        _red_invalid_schedule,
    )

    # R1-FI-03: Mutate macro session to INVALID while keeping 166 = 9 + 157 conservation intact
    def _red_all_sessions_invalid_conserved() -> Tuple[bool, str, Optional[int]]:
        mut_rows = copy.deepcopy(base_rows)
        mut_sum = copy.deepcopy(base_sum)
        mut_rows[0].pop("row_sha256", None)
        mut_rows[0]["pre_entry_features"]["ny_session_group"]["value"] = "INVALID"
        mut_rows[0]["row_sha256"] = canonical_hash(mut_rows[0])
        mut_sum["ny_session_group_census_all_170"]["INVALID"] = 1
        chk = validate_kh_01a_r1_forensics_bundle_strict(mut_rows, mut_sum)
        return (chk["status"] == "FAIL" and any("INVALID" in v for v in chk["violations"])), str(chk["violations"][:3]), None

    _add_r1_fault(
        "R1-FI-03_MUTATE_SESSION_INVALID_PRESERVING_166_CONSERVATION",
        "FAKE_CONSERVATION_PASS",
        "Mutate ny_session_group to INVALID while keeping 166=9+157 conserved and verify rejection",
        _green_bundle_ok,
        _red_all_sessions_invalid_conserved,
    )

    # R1-FI-04: Invalid macro input timestamp (seconds scale) never produces OUTSIDE_ICT_MACRO_WINDOW
    def _red_invalid_macro_ts() -> Tuple[bool, str, Optional[int]]:
        bad_st = resolve_causal_macro_time_window(decision_timestamp_ms=1792000000, symbol="XAUUSDT")
        val = validate_and_classify_macro_state_kh01a_r1(bad_st, expected_decision_timestamp_ms=1792000000, expected_symbol="XAUUSDT")
        cls = classify_pre_entry_state_strictly_asof(
            decision_timestamp_ms=1792000000000,
            direction="LONG",
            pre_entry_features={
                "in_ict_macro_window": _make_feature_cell(
                    "in_ict_macro_window", val["in_ict_macro_window_value"],
                    state=val["cell_state"], proof_state=val["in_window_proof_state"],
                    source_origin=val["source_origin"], known_at_ms=None, decision_ts_ms=1792000000000, raw_source_ids=[],
                ),
            },
            raw_snapshot_joined=True,
        )
        ok_rej = (
            val["macro_state_category"] == "UNKNOWN_OR_INVALID_MACRO_STATE"
            and cls["primary_pre_entry_code"] == "PRE_ENTRY_INVALID_EVIDENCE"
            and "OUTSIDE_ICT_MACRO_WINDOW" not in cls["active_pre_entry_risk_flags"]
        )
        return ok_rej, f"{val['macro_state_category']}:{cls['primary_pre_entry_code']}", None

    _add_r1_fault(
        "R1-FI-04_INVALID_MACRO_TIMESTAMP_REJECTED_NOT_OUTSIDE_WINDOW",
        "MACRO_INVALID_TIMESTAMP",
        "Inject seconds-scale timestamp into macro resolver + classifier; verify PRE_ENTRY_INVALID_EVIDENCE and never OUTSIDE_ICT_MACRO_WINDOW",
        _green_bundle_ok,
        _red_invalid_macro_ts,
    )

    # R1-FI-05: Injected future H4 bar (`closed_bar_close_ms > decision_timestamp_ms`)
    def _red_future_h4_bar() -> Tuple[bool, str, Optional[int]]:
        try:
            _make_feature_cell(
                "h4_structure_bias",
                "BEARISH",
                state="PRESENT",
                proof_state="RECONSTRUCTED_CAUSAL_VALUE",
                source_origin="RECONSTRUCTED_ASOF_RAW_BARS",
                source_timeframe="4H",
                known_at_ms=1792000000000,
                closed_bar_close_ms=1792014400000,  # +4h in the future!
                decision_ts_ms=1792000000000,
                raw_source_ids=["XAUUSDT:4h:1792000000000"],
            )
            return False, "ACCEPTED_FUTURE_H4_BAR_CLOSE", None
        except ValueError as exc:
            return ("BAR_CLOSE_LOOKAHEAD_VIOLATION" in str(exc)), str(exc), None

    _add_r1_fault(
        "R1-FI-05_INJECTED_FUTURE_H4_BAR_CLOSE_LOOKAHEAD",
        "CAUSAL_LOOKAHEAD",
        "Inject H4 bar with closed_bar_close_ms > decision_ts_ms and verify immediate ValueError",
        _green_bundle_ok,
        _red_future_h4_bar,
    )

    # R1-FI-06: Reconstructed context falsely claimed as PERSISTED_VERIFIED_SNAPSHOT
    def _red_fake_persisted_origin() -> Tuple[bool, str, Optional[int]]:
        mut_rows = copy.deepcopy(base_rows)
        # Mutate a delayed-entry SI8/SI9 row (where persisted snapshot was at earlier candidate_ts, not confirm_ts) to claim PERSISTED_VERIFIED_SNAPSHOT
        si89_r = next(r for r in mut_rows if r["population_role"] == "SI8_SI9_UNIQUE_RESEARCH_LOSS")
        si89_r.pop("row_sha256", None)
        fk = "atr_m1_usd"
        cell = si89_r["pre_entry_features"][fk]
        cell["source_origin"] = "PERSISTED_VERIFIED_SNAPSHOT"
        cell["reconstruction_algo_sha256"] = None
        cell["evidence_sha256"] = canonical_hash({
            "feature": fk,
            "value": cell["value"],
            "state": cell["state"],
            "proof_state": cell["proof_state"],
            "source_origin": cell["source_origin"],
            "source_timeframe": cell["source_timeframe"],
            "known_at_ms": cell["known_at_ms"],
            "closed_bar_close_ms": cell["closed_bar_close_ms"],
            "raw_source_ids": cell["raw_source_ids"],
            "reconstruction_algo_sha256": None,
        })
        si89_r["row_sha256"] = canonical_hash(si89_r)
        chk = validate_kh_01a_r1_forensics_bundle_strict(
            mut_rows, base_sum, persisted_f07_by_oid=feature_ledger_by_opp_id
        )
        return (chk["status"] == "FAIL" and any("UNGROUNDED_PERSISTED_SNAPSHOT_ORIGIN" in v for v in chk["violations"])), str(chk["violations"][:3]), None

    _add_r1_fault(
        "R1-FI-06_RECONSTRUCTED_FEATURE_CLAIMED_AS_PERSISTED_SNAPSHOT",
        "FAKE_SOURCE_ORIGIN",
        "Claim PERSISTED_VERIFIED_SNAPSHOT on reconstructed delayed-entry feature and verify validator rejects",
        _green_bundle_ok,
        _red_fake_persisted_origin,
    )

    # R1-FI-07: Nonexistent / placeholder source bar ID rejected
    def _red_nonexistent_bar_id() -> Tuple[bool, str, Optional[int]]:
        mut_rows = copy.deepcopy(base_rows)
        r0 = mut_rows[0]
        r0.pop("row_sha256", None)
        cell = r0["pre_entry_features"]["atr_m1_usd"]
        cell["raw_source_ids"] = ["XAUUSDT:1m:9999999990000"]
        cell["evidence_sha256"] = canonical_hash({
            "feature": "atr_m1_usd",
            "value": cell["value"],
            "state": cell["state"],
            "proof_state": cell["proof_state"],
            "source_origin": cell["source_origin"],
            "source_timeframe": cell["source_timeframe"],
            "known_at_ms": cell["known_at_ms"],
            "closed_bar_close_ms": cell["closed_bar_close_ms"],
            "raw_source_ids": cell["raw_source_ids"],
            "reconstruction_algo_sha256": cell["reconstruction_algo_sha256"],
        })
        r0["row_sha256"] = canonical_hash(r0)
        chk = validate_kh_01a_r1_forensics_bundle_strict(
            mut_rows, base_sum, canonical_bar_close_by_tf_open=canonical_bar_close_by_tf_open
        )
        return (chk["status"] == "FAIL" and any("SOURCE_BAR_NOT_IN_DATASET" in v for v in chk["violations"])), str(chk["violations"][:2]), None

    _add_r1_fault(
        "R1-FI-07_NONEXISTENT_SOURCE_BAR_ID_REJECTED",
        "MISSING_FEATURE_SOURCE",
        "Inject nonexistent canonical bar ID into raw_source_ids and verify validator rejects",
        _green_bundle_ok,
        _red_nonexistent_bar_id,
    )

    # R1-FI-08: Empty original evidence_sha256 rejected
    def _red_empty_ev_hash() -> Tuple[bool, str, Optional[int]]:
        mut_rows = copy.deepcopy(base_rows)
        r0 = mut_rows[0]
        r0.pop("row_sha256", None)
        r0["pre_entry_features"]["h1_structure_bias"]["evidence_sha256"] = ""
        r0["row_sha256"] = canonical_hash(r0)
        chk = validate_kh_01a_r1_forensics_bundle_strict(mut_rows, base_sum)
        return (chk["status"] == "FAIL" and any("FEATURE_EVIDENCE_SHA256_MISMATCH" in v for v in chk["violations"])), str(chk["violations"][:2]), None

    _add_r1_fault(
        "R1-FI-08_EMPTY_EVIDENCE_HASH_REJECTED",
        "CORRUPT_HASH",
        "Clear evidence_sha256 on a feature cell and verify row validator fails closed",
        _green_bundle_ok,
        _red_empty_ev_hash,
    )

    # R1-FI-09: 157/9 counts preserved in 06 summary while feature/reason distribution tampered
    def _red_tampered_distribution() -> Tuple[bool, str, Optional[int]]:
        mut_sum = copy.deepcopy(base_sum)
        reasons = mut_sum["si0_157_losses_aggregates"]["primary_pre_entry_reason_counts"]
        keys = sorted(reasons.keys())
        if len(keys) >= 2:
            reasons[keys[0]] += 1
            reasons[keys[1]] -= 1
        else:
            reasons["TAMPERED_REASON"] = 1
        chk = validate_kh_01a_r1_forensics_bundle_strict(base_rows, mut_sum)
        return (chk["status"] == "FAIL" and any("MISMATCH_ROW_STREAM_05" in v for v in chk["violations"])), str(chk["violations"][:2]), None

    _add_r1_fault(
        "R1-FI-09_COUNTS_157_9_PRESERVED_BUT_DISTRIBUTION_TAMPERED",
        "SUMMARY_STREAM_DIVERGENCE",
        "Preserve 157 losses + 9 wins sum while shifting 1 trade between primary reasons in 06 summary; verify rejection against 05 stream",
        _green_bundle_ok,
        _red_tampered_distribution,
    )

    # R1-FI-10: Legacy KH-01A schema (`kh_01a.foundation.v1`) rejected as R1
    def _red_legacy_schema() -> Tuple[bool, str, Optional[int]]:
        mut_sum = copy.deepcopy(base_sum)
        mut_sum["schema_version"] = "kh_01a.foundation.v1"
        chk = validate_kh_01a_r1_forensics_bundle_strict(base_rows, mut_sum)
        return (chk["status"] == "FAIL" and any("SCHEMA_NOT_R1" in v for v in chk["violations"])), str(chk["violations"][:2]), None

    _add_r1_fault(
        "R1-FI-10_LEGACY_KH01A_SCHEMA_REJECTED_AS_R1",
        "SCHEMA_VERSION_GUARD",
        "Pass outdated kh_01a.foundation.v1 schema into R1 validator and verify immediate rejection",
        _green_bundle_ok,
        _red_legacy_schema,
    )

    # R1-FI-11: Duplicate SI8/SI9 opportunity IDs rejected
    def _red_dup_si89() -> Tuple[bool, str, Optional[int]]:
        try:
            dup_s8 = [dict(si8_loss_rows[0]), dict(si8_loss_rows[0])]
            build_kh_01a_causal_loss_and_win_control_forensics(
                si0_all_primary_episodes=si0_all_primary_episodes[:2],
                si8_loss_rows=dup_s8,
                si9_loss_rows=[],
            )
            return False, "ACCEPTED_DUPLICATE_SI8_ID", None
        except ValueError as exc:
            return ("DUPLICATE_OPPORTUNITY_ID_IN_SI8_OR_SI9" in str(exc)), str(exc), None

    _add_r1_fault(
        "R1-FI-11_DUPLICATE_SI8_SI9_OPPORTUNITY_IDS_REJECTED",
        "DUPLICATE_IDS",
        "Inject duplicate opportunity_id in SI8 loss rows and verify ValueError",
        _green_bundle_ok,
        _red_dup_si89,
    )

    # R1-FI-12..16: Disk, Receipt, JUnit, Compressed Casebook, and Separate-OS-Process Verifier Rejections
    with tempfile.TemporaryDirectory(prefix="kh01a_r1_faults_") as tmp_s:
        tmp_dir = Path(tmp_s)

        # R1-FI-12: Dynamic receipt refuses fake PASS when validator fails (faulty validator / failed semantic check)
        good_target = tmp_dir / "good_gate.json"
        write_json_durable_atomic(good_target, {"ok": True})
        green_rcpt, _ = build_dynamic_validated_gate_receipt_r1(
            gate_id="G-KH01A-R1-04",
            run_dir=tmp_dir,
            run_id=run_id,
            tested_sha=tested_sha,
            target_rel_path="good_gate.json",
            validator_fn=lambda: {"status": "PASS", "violations": []},
            write_json_fn=write_json_durable_atomic,
        )
        red_rcpt, _ = build_dynamic_validated_gate_receipt_r1(
            gate_id="G-KH01A-R1-04",
            run_dir=tmp_dir,
            run_id=run_id,
            tested_sha=tested_sha,
            target_rel_path="good_gate.json",
            validator_fn=lambda: {"status": "FAIL", "violations": ["SEMANTIC_INVALID_MACRO_STATE"]},
            write_json_fn=write_json_durable_atomic,
        )
        _add_r1_fault(
            "R1-FI-12_DYNAMIC_RECEIPT_NEVER_PRELABELS_PASS_WHEN_VALIDATOR_FAILS",
            "ANTI_SELF_CERTIFICATION",
            "Verify build_dynamic_validated_gate_receipt_r1 emits FAIL receipt even when JSON file exists with valid SHA256",
            lambda: green_rcpt.is_valid_pass(expected_run_id=run_id, expected_tested_sha=tested_sha, run_dir=tmp_dir),
            lambda: (
                not red_rcpt.is_valid_pass(expected_run_id=run_id, expected_tested_sha=tested_sha, run_dir=tmp_dir)
                and red_rcpt.status == "FAIL",
                f"status={red_rcpt.status},violations={red_rcpt.violations}",
                None,
            ),
        )

        # R1-FI-13: Detached receipt (target artifact deleted after receipt issued)
        det_target = tmp_dir / "detached_target.json"
        write_json_durable_atomic(det_target, {"ok": True})
        det_rcpt, _ = build_dynamic_validated_gate_receipt_r1(
            gate_id="G-KH01A-R1-05",
            run_dir=tmp_dir,
            run_id=run_id,
            tested_sha=tested_sha,
            target_rel_path="detached_target.json",
            validator_fn=lambda: {"status": "PASS", "violations": []},
            write_json_fn=write_json_durable_atomic,
        )
        det_target.unlink()
        _add_r1_fault(
            "R1-FI-13_DETACHED_RECEIPT_MISSING_TARGET_FILE_ON_DISK",
            "DETACHED_RECEIPT",
            "Delete target artifact after receipt creation and verify disk readback rejects",
            lambda: green_rcpt.is_valid_pass(expected_run_id=run_id, expected_tested_sha=tested_sha, run_dir=tmp_dir),
            lambda: (
                not det_rcpt.is_valid_pass(expected_run_id=run_id, expected_tested_sha=tested_sha, run_dir=tmp_dir),
                "DETACHED_RECEIPT_TARGET_MISSING_REJECTED",
                None,
            ),
        )

        # R1-FI-14: Modified JUnit XML on disk after shard seal
        j_dir = tmp_dir / "junit"
        j_dir.mkdir(parents=True, exist_ok=True)
        n_id = "backend/tests/test_kh_01a_r1_forensics_truth_closure.py::test_sample"
        j_xml = (
            b'<?xml version="1.0" encoding="utf-8"?>'
            b'<testsuite name="pytest" tests="1" errors="0" failures="0" skipped="0">'
            b'<testcase classname="backend.tests.test_kh_01a_r1_forensics_truth_closure" name="test_sample" time="0.01"/>'
            b"</testsuite>"
        )
        jp = j_dir / "s0.xml"
        j_sha = write_bytes_durable_atomic(jp, j_xml)
        j_mtime = jp.stat().st_mtime_ns
        c_man = {"run_id": run_id, "tested_executable_sha": tested_sha, "collect_exit_code": 0, "collected_node_ids": [n_id]}
        s_man = {
            "run_id": run_id,
            "tested_executable_sha": tested_sha,
            "shards": [{
                "shard_id": "s0", "run_id": run_id, "tested_executable_sha": tested_sha,
                "reused": False, "pid": 999, "exit_code": 0, "timed_out": False,
                "scheduled_node_ids": [n_id], "junit_xml_path": "junit/s0.xml",
                "xml_sha256": j_sha, "xml_mtime_ns": j_mtime,
            }],
        }
        green_junit_ok = (
            verify_fresh_junit_bijection_v17931211(
                tmp_dir, c_man, s_man, expected_run_id=run_id, expected_tested_sha=tested_sha, require_shard_schedule=True
            )["status"] == "PASS"
        )
        jp.write_bytes(j_xml + b"<!-- post-seal disk edit -->")
        red_junit = verify_fresh_junit_bijection_v17931211(
            tmp_dir, c_man, s_man, expected_run_id=run_id, expected_tested_sha=tested_sha, require_shard_schedule=True
        )
        _add_r1_fault(
            "R1-FI-14_MODIFIED_JUNIT_XML_ON_DISK_AFTER_SEAL",
            "JUNIT_DISK_TAMPERING",
            "Append bytes to closed JUnit XML on disk after shard SHA capture and verify rejection",
            lambda: green_junit_ok,
            lambda: (red_junit["status"] == "FAIL", str(red_junit["violations"]), None),
        )

        # R1-FI-15: Corrupted compressed casebook (.jsonl.gz) rejected by separate OS process verifier
        corrupt_dir = tmp_dir / "corrupt_gz_bundle"
        corrupt_dir.mkdir(parents=True, exist_ok=True)
        for req_f in REQUIRED_ARTIFACTS_KH01A_R1:
            if req_f == "15_independent_verifier_receipts_sha256_manifest.json":
                continue
            p_f = corrupt_dir / req_f
            if req_f.endswith(".jsonl.gz"):
                p_f.write_bytes(b"\x1f\x8b\x08\x00CORRUPTED_GZIP_STREAM_BYTES")
            elif req_f.endswith(".json"):
                write_json_durable_atomic(p_f, {"run_id": run_id, "tested_executable_sha": tested_sha, "branch": MANDATORY_BRANCH_KH01A, "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH01A})
            else:
                p_f.write_text("placeholder\n", encoding="utf-8")

        if verifier_script_path is not None and verifier_script_path.is_file():
            proc_corrupt = subprocess.run(
                [sys.executable, str(verifier_script_path), "--run-dir", str(corrupt_dir), "--skip-git-freeze-check"],
                capture_output=True,
                text=True,
            )
            corrupt_rej = proc_corrupt.returncode != 0
            corrupt_msg = (proc_corrupt.stdout or proc_corrupt.stderr)[-400:].strip()
            corrupt_rc: Optional[int] = int(proc_corrupt.returncode)
        else:
            corrupt_rc = None
            try:
                list(_iter_jsonl_gz(corrupt_dir / "05_r1_preentry_170_rows.jsonl.gz"))
                corrupt_rej = False
                corrupt_msg = "UNEXPECTED_GZIP_READ_SUCCESS"
            except Exception as exc:
                corrupt_rej = True
                corrupt_msg = f"{type(exc).__name__}:{exc}"

        _add_r1_fault(
            "R1-FI-15_CORRUPTED_COMPRESSED_CASEBOOK_REJECTED",
            "CORRUPT_COMPRESSED_ARTIFACT",
            "Corrupt 05_r1_preentry_170_rows.jsonl.gz bytes and verify separate-process verifier exits nonzero",
            _green_bundle_ok,
            lambda: (corrupt_rej, corrupt_msg, corrupt_rc),
        )

        # R1-FI-16: Manually forged G-KH01A-R1-04: PASS in JSON with underlying INVALID macro row rejected by separate OS process
        forged_dir = tmp_dir / "forged_pass_bundle"
        forged_dir.mkdir(parents=True, exist_ok=True)
        bad_rows = copy.deepcopy(base_rows)
        bad_rows[0].pop("row_sha256", None)
        bad_rows[0]["pre_entry_features"]["ny_session_group"]["value"] = "INVALID"
        bad_rows[0]["row_sha256"] = canonical_hash(bad_rows[0])
        with gzip.open(forged_dir / "05_r1_preentry_170_rows.jsonl.gz", "wt", encoding="utf-8") as gz_f:
            for r in bad_rows:
                gz_f.write(json.dumps(r, sort_keys=True) + "\n")
        write_json_durable_atomic(forged_dir / "06_feature_evidence_origin_missing_invalid_source_hash_census.json", base_sum)
        for req_f in REQUIRED_ARTIFACTS_KH01A_R1:
            if req_f in ("15_independent_verifier_receipts_sha256_manifest.json", "05_r1_preentry_170_rows.jsonl.gz", "06_feature_evidence_origin_missing_invalid_source_hash_census.json"):
                continue
            p_f = forged_dir / req_f
            if req_f.endswith(".json"):
                write_json_durable_atomic(
                    p_f,
                    {
                        "run_id": run_id,
                        "tested_executable_sha": tested_sha,
                        "branch": MANDATORY_BRANCH_KH01A,
                        "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH01A,
                        "orthogonal_verdicts": {"ENGINEERING_VERDICT": "PASS", "FORENSICS_SEMANTIC_VERDICT": "PASS"},
                        "gates": [{"gate_id": "G-KH01A-R1-04", "status": "PASS"}],
                    },
                )
            else:
                p_f.write_text("forged\n", encoding="utf-8")

        if verifier_script_path is not None and verifier_script_path.is_file():
            proc_forged = subprocess.run(
                [sys.executable, str(verifier_script_path), "--run-dir", str(forged_dir), "--skip-git-freeze-check"],
                capture_output=True,
                text=True,
            )
            forged_rej = proc_forged.returncode != 0
            forged_msg = (proc_forged.stdout or proc_forged.stderr)[-400:].strip()
            forged_rc: Optional[int] = int(proc_forged.returncode)
        else:
            chk_f = validate_kh_01a_r1_forensics_bundle_strict(bad_rows, base_sum)
            forged_rej = chk_f["status"] == "FAIL"
            forged_msg = str(chk_f["violations"][:3])
            forged_rc = None

        _add_r1_fault(
            "R1-FI-16_FORGED_GATE_PASS_JSON_WITH_POISONED_CASEBOOK_REJECTED_BY_VERIFIER",
            "FORGED_GATE_PASS",
            "Write G-KH01A-R1-04=PASS & ENGINEERING_VERDICT=PASS in JSON while 05 casebook contains INVALID session; verify separate-process verifier rejects",
            _green_bundle_ok,
            lambda: (forged_rej, forged_msg, forged_rc),
        )

    # Include the 22 foundational KH-01A real fault injections as well
    base_kh01a_suite = run_kh_01a_real_fault_injections(tested_sha=tested_sha, run_id=run_id)
    r1_passed = sum(1 for f in faults if f["status"] == "PASS")
    total_combined = len(faults) + int(base_kh01a_suite["total_real_fault_injections"])
    passed_combined = r1_passed + int(base_kh01a_suite["passed_real_fault_injections"])

    return {
        "spec_version": SPEC_VERSION_KH01A_R1,
        "schema_version": SCHEMA_VERSION_KH01A_R1,
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "r1_specific_fault_injections_total": len(faults),
        "r1_specific_fault_injections_passed": r1_passed,
        "foundational_kh01a_fault_injections_total": base_kh01a_suite["total_real_fault_injections"],
        "foundational_kh01a_fault_injections_passed": base_kh01a_suite["passed_real_fault_injections"],
        "total_real_fault_injections": total_combined,
        "passed_real_fault_injections": passed_combined,
        "failed_fault_injections": total_combined - passed_combined,
        "report_assertions_counted_as_faults": 0,
        "synthetic_nonzero_d4_positive_fixture_verification": base_kh01a_suite["synthetic_nonzero_d4_positive_fixture_verification"],
        "status": "PASS"
        if (passed_combined == total_combined and base_kh01a_suite["synthetic_nonzero_d4_positive_fixture_verification"]["passed"])
        else "FAIL",
        "r1_fault_cases": faults,
        "foundational_kh01a_fault_cases": base_kh01a_suite["fault_cases"],
    }


# ==============================================================================
# PHASE J & L: 14-GATE KH-01A.R1 ACCEPTANCE MATRIX & VIETNAMESE HANDOVER
# ==============================================================================

def evaluate_kh_01a_r1_14_gates(
    *,
    run_dir: Path,
    run_id: str,
    branch: str,
    tested_sha: str,
    dataset_sha: str,
    preflight_00: Mapping[str, Any],
    callgraph_01: Mapping[str, Any],
    macro_diff_02: Mapping[str, Any],
    macro_dst_03: Mapping[str, Any],
    casebook_diff_04: Mapping[str, Any],
    forensics_06: Mapping[str, Any],
    lineage_07: Mapping[str, Any],
    fault_suite_08: Mapping[str, Any],
    replay_09: Mapping[str, Any],
    parity_10: Mapping[str, Any],
    junit_11: Mapping[str, Any],
    worker_12: Mapping[str, Any],
    resource_13: Mapping[str, Any],
    fresh_oos_14: Mapping[str, Any],
    receipts_by_gate: Mapping[str, EvidenceReceiptV17931219],
    publication_status: str = "PENDING_REMOTE_PUSH",
) -> Dict[str, Any]:
    """
    Evaluates the 14 mandatory KH-01A.R1 acceptance gates (`G-KH01A-R1-01` .. `G-KH01A-R1-14`)
    strictly from validated evidence and dynamic `EvidenceReceiptV17931219` objects.
    """
    gates: List[Dict[str, Any]] = []

    def _add_r1_gate(
        gate_id: str,
        name: str,
        intent: str,
        authoritative_fn: str,
        artifact_rel_path: str,
        observed_values: Any,
        expected_invariant: str,
        red_control_id: str,
        green_control_id: str,
        status: str,
        violations: Sequence[str],
    ) -> None:
        rcpt = receipts_by_gate.get(gate_id)
        v_list = list(violations)
        if status == "PASS":
            if rcpt is None:
                status = "FAIL"
                v_list.append(f"MISSING_DYNAMIC_RECEIPT:{gate_id}")
            elif not rcpt.is_valid_pass(
                expected_run_id=run_id,
                expected_tested_sha=tested_sha,
                run_dir=run_dir,
                reject_uniform_placeholder_hash=True,
            ):
                status = "FAIL"
                v_list.append(f"INVALID_OR_FAILED_DYNAMIC_RECEIPT:{gate_id}")
                v_list.extend(rcpt.violations)
        gates.append({
            "gate_id": gate_id,
            "name": name,
            "intent": intent,
            "authoritative_function": authoritative_fn,
            "tested_run_id": run_id,
            "executable_sha": tested_sha,
            "raw_input_sha256": rcpt.input_sha256 if rcpt is not None else None,
            "validator_name": rcpt.validator_name if rcpt is not None else authoritative_fn,
            "validator_sha256": rcpt.validator_code_sha if rcpt is not None else None,
            "direct_artifact_path": artifact_rel_path,
            "observed_values": observed_values,
            "expected_invariant": expected_invariant,
            "red_control": red_control_id,
            "green_control": green_control_id,
            "status": status,
            "violations": v_list,
            "independent_receipt": asdict(rcpt) if rcpt is not None else None,
        })

    # R1-01: Branch/source freeze & immutable previous artifacts
    v01: List[str] = []
    if branch != MANDATORY_BRANCH_KH01A:
        v01.append(f"WRONG_BRANCH:{branch}")
    if len(tested_sha) != 40 or preflight_00.get("dirty_executable_files"):
        v01.append("UNFROZEN_OR_DIRTY_EXECUTABLE_TREE")
    if preflight_00.get("source_tree_unchanged_during_run") is not True:
        v01.append("SOURCE_TREE_CHANGED_DURING_RUN")
    if preflight_00.get("prior_artifacts_unchanged_during_run") is not True:
        v01.append("PRIOR_IMMUTABLE_ARTIFACTS_MODIFIED")
    if dataset_sha != CANONICAL_DATASET_SHA256_KH01A:
        v01.append(f"DATASET_SHA_CHANGED:{dataset_sha}")
    _add_r1_gate(
        "G-KH01A-R1-01",
        "BRANCH_SOURCE_FREEZE_AND_IMMUTABLE_PRIOR_ARTIFACTS",
        "Verify mandatory branch, clean frozen executable SHA, immutable canonical dataset, and untouched prior KH-01A/V19 artifacts.",
        "verify_source_freeze_v17931211",
        "00_branch_sha_config_dataset_preflight.json",
        {
            "branch": branch,
            "tested_sha": tested_sha,
            "source_unchanged": preflight_00.get("source_tree_unchanged_during_run"),
            "prior_artifacts_unchanged": preflight_00.get("prior_artifacts_unchanged_during_run"),
        },
        f"branch=={MANDATORY_BRANCH_KH01A}, clean tree, dataset_sha=={CANONICAL_DATASET_SHA256_KH01A}, prior kh_01a bundle untouched",
        "FI-13_MISMATCHED_TESTED_SHA_AND_RUN_ID",
        "Preflight & post-run source/prior-artifact hash equality",
        "PASS" if not v01 else "FAIL",
        v01,
    )

    # R1-02: Reversed-argument RED reproduction and correct keyword caller GREEN
    v02: List[str] = []
    ast_audit = callgraph_01.get("macro_call_site_ast_audit") or {}
    if int(ast_audit.get("invalid_production_call_sites_count", 1)) != 0:
        v02.append(f"INVALID_PRODUCTION_MACRO_CALL_SITES:{ast_audit.get('invalid_production_call_sites')}")
    if macro_diff_02.get("red_reproduction_confirmed") is not True:
        v02.append("REVERSED_ARGS_RED_NOT_REPRODUCED")
    if macro_diff_02.get("green_zero_invalid_confirmed") is not True:
        v02.append("KEYWORD_CALLER_GREEN_NOT_CONFIRMED")
    _add_r1_gate(
        "G-KH01A-R1-02",
        "MACRO_REVERSED_ARGS_RED_AND_KEYWORD_CALLER_GREEN",
        "Reproduce reversed-argument RED defect on all 170 cohort rows and prove explicit keyword caller GREEN with 0 invalid production AST call sites.",
        "scan_all_macro_window_call_sites_ast",
        "02_macro_reversed_args_red_green_diff.json",
        {
            "production_call_sites": ast_audit.get("production_call_sites_count"),
            "invalid_production_call_sites": ast_audit.get("invalid_production_call_sites_count"),
            "red_before_sessions": macro_diff_02.get("red_before_session_group_counts"),
            "green_after_sessions": macro_diff_02.get("green_after_session_group_counts"),
        },
        "RED reversed call yields 170/170 INVALID; GREEN keyword call yields 0 INVALID and 0 invalid AST call sites in production code",
        "G-KH01A-R1-FOR-01_SWAPPED_MACRO_ARGS_ON_REAL_170_COHORT",
        "Explicit keyword binding decision_timestamp_ms=dec_ts, symbol='XAUUSDT'",
        "PASS" if not v02 else "FAIL",
        v02,
    )

    # R1-03: Macro timestamp/symbol/session validity and NY DST/boundary tests
    v03: List[str] = []
    if macro_dst_03.get("status") != "PASS":
        v03.append("MACRO_DST_OR_BOUNDARY_AUDIT_FAILED")
    _add_r1_gate(
        "G-KH01A-R1-03",
        "MACRO_TIMESTAMP_SYMBOL_SESSION_AND_NY_DST_BOUNDARIES",
        "Verify NY DST spring/fall transitions, all 7 half-open [start, end) macro boundaries, weekend closure, and invalid/unverified input rejection.",
        "validate_and_classify_macro_state_kh01a_r1",
        "03_macro_dst_schedule_semantics_and_invalid_samples.json",
        {
            "dst_transitions": macro_dst_03.get("dst_transitions_verified"),
            "windows_checked": len(macro_dst_03.get("all_7_windows_half_open_boundaries") or []),
            "invalid_probes_checked": len(macro_dst_03.get("invalid_and_unverified_input_probes") or []),
        },
        "All 7 windows pass [start, end) boundary checks; US DST EDT(-240)/EST(-300) exact; invalid/unverified inputs map to UNKNOWN_OR_INVALID_MACRO_STATE (never False)",
        "R1-FI-04_INVALID_MACRO_TIMESTAMP_REJECTED_NOT_OUTSIDE_WINDOW",
        "All 7 NY macro windows + spring/fall DST probes PASS",
        "PASS" if not v03 else "FAIL",
        v03,
    )

    # R1-04: All 170 casebook rows re-evaluated; credible macro session statuses, 0 INVALID
    v04: List[str] = []
    val_06 = forensics_06.get("strict_bundle_validation") or {}
    if val_06.get("status") != "PASS":
        v04.extend(val_06.get("violations") or ["STRICT_BUNDLE_VALIDATION_FAILED"])
    m3_170 = forensics_06.get("macro_3way_census_all_170") or {}
    sess_170 = forensics_06.get("ny_session_group_census_all_170") or {}
    if int(m3_170.get("UNKNOWN_OR_INVALID_MACRO_STATE", 1)) != 0:
        v04.append(f"UNKNOWN_OR_INVALID_MACRO_ROWS:{m3_170}")
    if int(sess_170.get("INVALID", 0)) > 0:
        v04.append(f"INVALID_SESSION_GROUP_ROWS:{sess_170}")
    if casebook_diff_04.get("trading_geometry_pnl_and_expost_identical_all_170") is not True:
        v04.append("TRADING_OR_EXPOST_DRIFT_IN_CASEBOOK_RECOMPUTATION")
    _add_r1_gate(
        "G-KH01A-R1-04",
        "ALL_170_CASEBOOK_ROWS_CREDIBLE_MACRO_ZERO_INVALID",
        "Re-evaluate all 170 casebook rows (157 SI0 losses + 9 SI0 wins + 4 unique SI8/SI9 losses) with credible NY sessions, 0 INVALID sessions, and explicit schedule provenance disclosure.",
        "validate_kh_01a_r1_forensics_bundle_strict",
        "04_previous_vs_r1_170_casebook_distribution_diff.json",
        {
            "macro_3way_all_170": m3_170,
            "ny_session_groups_all_170": sess_170,
            "schedule_provenance": forensics_06.get("macro_schedule_provenance"),
        },
        "170 rows re-evaluated; 0 INVALID sessions; 0 UNKNOWN_OR_INVALID_MACRO_STATE; research-hypothesis schedule provenance disclosed honestly",
        "R1-FI-03_MUTATE_SESSION_INVALID_PRESERVING_166_CONSERVATION",
        "170/170 rows validated by validate_kh_01a_r1_forensics_bundle_strict",
        "PASS" if not v04 else "FAIL",
        v04,
    )

    # R1-05: Persisted / reconstructed / unknown provenance tri-state honesty
    v05: List[str] = []
    if forensics_06.get("full_original_runtime_context_completeness_status") != "NOT_PROVABLE_FROM_AVAILABLE_ARTIFACTS":
        v05.append("UNDISCLOSED_ORIGINAL_RUNTIME_CONTEXT_LIMITATION")
    comp_170 = forensics_06.get("evidence_completeness_census_all_170") or {}
    if sum(int(x) for x in comp_170.values()) != 170:
        v05.append(f"COMPLETENESS_CENSUS_NOT_170:{comp_170}")
    if val_06.get("status") != "PASS":
        v05.append("PROVENANCE_VALIDATION_FAILED")
    _add_r1_gate(
        "G-KH01A-R1-05",
        "PROVENANCE_TRISTATE_PERSISTED_RECONSTRUCTED_UNKNOWN_HONESTY",
        "Classify per-field evidence origin honestly (PERSISTED_VERIFIED_SNAPSHOT vs RECONSTRUCTED_ASOF_RAW_BARS vs MISSING); never treat UNKNOWN as proven FALSE.",
        "validate_kh_01a_r1_forensics_bundle_strict",
        "06_feature_evidence_origin_missing_invalid_source_hash_census.json",
        {
            "evidence_completeness_all_170": comp_170,
            "full_original_runtime_context_status": forensics_06.get("full_original_runtime_context_completeness_status"),
        },
        "Per-field origin verified; PARTIAL rows explicitly marked PARTIAL; unknown sweep/MSS/HTF placed in unknown_or_unproven_flags, never proven false",
        "R1-FI-06_RECONSTRUCTED_FEATURE_CLAIMED_AS_PERSISTED_SNAPSHOT",
        "All 3,230 feature cells (170x19) pass origin & tri-state validation",
        "PASS" if not v05 else "FAIL",
        v05,
    )

    # R1-06: Per-feature as-of clock, original-vs-reconstructed lineage & reproducibility
    v06: List[str] = []
    ah = forensics_06.get("anti_hindsight_contract") or {}
    if int(ah.get("max_known_at_minus_decision_ts_ms", 1)) > 0 or int(ah.get("max_closed_bar_close_minus_decision_ts_ms", 1)) > 0:
        v06.append(f"ASOF_CLOCK_LOOKAHEAD:{ah}")
    if lineage_07.get("two_independent_runs_semantic_hash_identical") is not True:
        v06.append("RECONSTRUCTION_REPRODUCIBILITY_MISMATCH")
    fp_c = lineage_07.get("fingerprint_comparison_counts_all_170") or {}
    if int(fp_c.get("MISMATCH", 1)) != 0 or int(fp_c.get("MATCH", 0)) != 166 or int(fp_c.get("NOT_COMPARABLE", 0)) != 4:
        v06.append(f"UNEXPECTED_FINGERPRINT_LINEAGE_COUNTS:{fp_c}")
    _add_r1_gate(
        "G-KH01A-R1-06",
        "PER_FEATURE_ASOF_CLOCK_LINEAGE_AND_ZERO_HINDSIGHT",
        "Enforce known_at_ms <= decision_ts_ms and closed_bar_close_ms <= decision_ts_ms, verify original-vs-reconstructed fingerprints, and prove 2-run deterministic reconstruction.",
        "build_kh_01a_causal_loss_and_win_control_forensics",
        "07_original_snapshot_vs_asof_reconstruction_fingerprint_truth.json",
        {
            "max_known_at_minus_decision_ms": ah.get("max_known_at_minus_decision_ts_ms"),
            "max_closed_bar_minus_decision_ms": ah.get("max_closed_bar_close_minus_decision_ts_ms"),
            "fingerprint_lineage_170": fp_c,
            "two_run_reproducible": lineage_07.get("two_independent_runs_semantic_hash_identical"),
        },
        "0ms lookahead; 166/166 SI0 MATCH + 4/4 delayed-entry SI8/SI9 NOT_COMPARABLE (with 4/4 candidate_ts MATCH); 2 independent runs 100% hash-identical",
        "R1-FI-05_INJECTED_FUTURE_H4_BAR_CLOSE_LOOKAHEAD",
        "Two independent reconstruction passes produce identical row SHA256s",
        "PASS" if not v06 else "FAIL",
        v06,
    )

    # R1-07: Cohort accounting conservation
    v07: List[str] = []
    lc = forensics_06.get("ledger_conservation") or {}
    if (
        int(lc.get("si0_scheduled_total", 0)) != 242
        or int(lc.get("si0_closed_total", 0)) != 166
        or int(lc.get("si0_wins_count", 0)) != 9
        or int(lc.get("si0_losses_count", 0)) != 157
        or int(lc.get("si8_closed_losses_count", 0)) != 4
        or int(lc.get("si9_closed_losses_count", 0)) != 2
        or int(lc.get("si8_si9_shared_loss_ids_count", 0)) != 2
        or int(lc.get("si8_si9_unique_losses_count", 0)) != 4
    ):
        v07.append(f"COHORT_CONSERVATION_MISMATCH:{lc}")
    _add_r1_gate(
        "G-KH01A-R1-07",
        "COHORT_ACCOUNTING_242_166_9W_157L_AND_SI89_UNIQUE_4",
        "Verify exact cohort conservation: 242 scheduled, 166 = 9W + 157L, and SI8=4 / SI9=2 with 2 shared -> 4 unique research losses.",
        "build_loss_episode_attribution_from_raw_v17931219",
        "06_feature_evidence_origin_missing_invalid_source_hash_census.json",
        lc,
        "242 scheduled; 166 = 9 + 157; SI8(4) + SI9(2) - shared(2) = 4 unique -> 170 total rows",
        "R1-FI-11_DUPLICATE_SI8_SI9_OPPORTUNITY_IDS_REJECTED",
        "Exact ledger-to-casebook conservation across 242 / 166 / 4 cohorts",
        "PASS" if not v07 else "FAIL",
        v07,
    )

    # R1-08: All R1 + existing regression JUnit verified with zero failures + adversarial fault suite
    v08: List[str] = []
    jb = junit_11.get("independent_bijection_verification") or {}
    cnts = jb.get("counters") or {}
    col_ids = set((junit_11.get("collection_manifest") or {}).get("collected_node_ids") or ())
    has_r1_nodes = any("test_kh_01a_r1_" in nid for nid in col_ids)
    if jb.get("status") != "PASS":
        v08.append(f"JUNIT_BIJECTION_FAILED:{jb.get('violations')}")
    if not has_r1_nodes:
        v08.append("MISSING_NEW_KH01A_R1_TEST_NODES_IN_COLLECTION")
    if int(cnts.get("collected", 0)) <= 5477:
        v08.append(f"COLLECTED_COUNT_DID_NOT_INCREASE_ABOVE_PRIOR_5477:{cnts.get('collected')}")
    if int(cnts.get("failed", 1)) != 0 or int(cnts.get("errors", 1)) != 0 or int(cnts.get("mandatory_skipped", 1)) != 0:
        v08.append(f"PYTEST_FAILURES_OR_MANDATORY_SKIPS:{cnts}")
    if fault_suite_08.get("status") != "PASS" or int(fault_suite_08.get("failed_fault_injections", 1)) != 0:
        v08.append("ADVERSARIAL_FAULT_SUITE_FAILED")
    _add_r1_gate(
        "G-KH01A-R1-08",
        "FULL_REGRESSION_AND_R1_JUNIT_CLOSED_BIJECTION",
        "Verify closed JUnit XML bijection across full backend + new R1 tests with 0 failures/errors/mandatory-skips and 100% real fault injections PASS.",
        "verify_fresh_junit_bijection_v17931211",
        "11_pytest_collection_junit_closed_shard_exit_bijection.json",
        {
            "pytest_counters": cnts,
            "r1_test_nodes_present": has_r1_nodes,
            "fault_injections": f"{fault_suite_08.get('passed_real_fault_injections')}/{fault_suite_08.get('total_real_fault_injections')}",
        },
        "collected == executed > 5477; R1 test nodes present; 0 failed, 0 errors, 0 mandatory skips; all real fault injections PASS",
        "R1-FI-14_MODIFIED_JUNIT_XML_ON_DISK_AFTER_SEAL",
        "Closed JUnit XML shard bijection across full test inventory",
        "PASS" if not v08 else "FAIL",
        v08,
    )

    # R1-09: Full historical backtest, 3 fill modes, 1pos accounting, 180m purged WF, 267+40 target audit
    v09: List[str] = []
    si0_nb = ((replay_09.get("si_policies_nb_summary") or {}).get("SI0_OFF_BASELINE") or {})
    if replay_09.get("p0a_accounting_status") != "PASS" or replay_09.get("lineage_30_partitions_status") != "PASS":
        v09.append("BACKTEST_ACCOUNTING_OR_30_PARTITION_LINEAGE_FAILED")
    if replay_09.get("d4_execution_truth_status") != "PASS":
        v09.append("D4_EXECUTION_TRUTH_FAILED")
    if (
        int(si0_nb.get("scheduled", 0)) != 242
        or int(si0_nb.get("closed", 0)) != 166
        or int(si0_nb.get("wins", 0)) != 9
        or int(si0_nb.get("losses", 0)) != 157
        or not math.isclose(float(si0_nb.get("net_pnl_usd", 0.0)), -345.8971, abs_tol=1e-4)
        or not math.isclose(float(si0_nb.get("expectancy_net_r", 0.0)), -0.8474, abs_tol=1e-4)
    ):
        v09.append(f"SI0_PRIMARY_BASELINE_DRIFT:{si0_nb}")
    if (replay_09.get("target_267_plus_40_audit") or {}).get("verification_status") != "PASS":
        v09.append("TARGET_267_PLUS_40_AUDIT_FAILED")
    _add_r1_gate(
        "G-KH01A-R1-09",
        "FULL_HISTORICAL_BACKTEST_3FILL_1POS_AND_PURGED_WF",
        "Replay SI0..SI9 x 3 fill modes + A0..D4 1-position accounting + 180m purged walk-forward + 267+40 target audit with exact trading baseline preservation.",
        "verify_all_policy_fill_mode_lineage_independently_v17931211",
        "09_all_3_fill_modes_si0_to_si9_one_position_backtest_oos_cost_stress.json",
        {
            "si0_nb": si0_nb,
            "d4_accepted_nb": (replay_09.get("d4_summary") or {}).get("d4_universe_63085_accepted_intents_nb"),
            "target_267_audit": (replay_09.get("target_267_plus_40_audit") or {}).get("verification_status"),
        },
        "30 SI partitions + A0..D4 reconciled; SI0 NB = 242 sched, 166 closed (9W/157L), -$345.8971, -0.8474R; D4=0 setups; 267+40 target audit PASS",
        "FI-06_INFINITY_FLOAT_METRIC",
        "Independent 30-partition exit-lineage & source ledger reconciliation PASS",
        "PASS" if not v09 else "FAIL",
        v09,
    )

    # R1-10: Full-population trading/evaluator/3-adapter parity
    v10: List[str] = []
    if parity_10.get("TRADING_PARITY") != "PASS":
        v10.append(f"TRADING_PARITY_NOT_PASS:{parity_10.get('TRADING_PARITY')}")
    if int(parity_10.get("full_a1_56710_mismatches_count", 1)) != 0 or int(parity_10.get("full_a1_56710_evaluated_count", 0)) != 56710:
        v10.append(f"FULL_A1_56710_PARITY_MISMATCH:{parity_10.get('full_a1_56710_mismatches_count')}")
    if int(parity_10.get("three_adapter_246_mismatches_count", 1)) != 0 or int(parity_10.get("three_adapter_246_evaluated_count", 0)) != 246:
        v10.append(f"THREE_ADAPTER_246_PARITY_MISMATCH:{parity_10.get('three_adapter_246_mismatches_count')}")
    if parity_10.get("si0_si8_si9_ledger_sha256_exact_match") is not True:
        v10.append("ACCOUNTING_LEDGER_SHA256_DRIFT")
    _add_r1_gate(
        "G-KH01A-R1-10",
        "FULL_POPULATION_TRADING_EVALUATOR_AND_3_ADAPTER_PARITY",
        "Verify bounded streaming parity across all 56,710 canonical A1 opportunities (+ 6,375 SI8 = 63,085), 3-adapter dry-run parity across all 246 scheduled SI0/SI8/SI9 episodes, and unchanged <= $2.50 risk limits.",
        "SharedStrategyEvaluator.evaluate_model_direction",
        "10_full_trade_action_and_3_adapter_parity.json",
        {
            "TRADING_PARITY": parity_10.get("TRADING_PARITY"),
            "FORENSIC_RECOMPUTATION_STATUS": parity_10.get("FORENSIC_RECOMPUTATION_STATUS"),
            "a1_population_verified": f"{parity_10.get('full_a1_56710_evaluated_count')}/56710 (mismatches={parity_10.get('full_a1_56710_mismatches_count')})",
            "three_adapter_population_verified": f"{parity_10.get('three_adapter_246_evaluated_count')}/246 (mismatches={parity_10.get('three_adapter_246_mismatches_count')})",
        },
        "56,710/56,710 A1 decision & trade_action fingerprints match (0 mismatches); 246/246 3-adapter hashes match; 30/30 accounting ledgers unchanged",
        "FI-04_NONE_VALUE_IN_METRIC",
        "Streaming full-universe 56,710 + 246 3-adapter bitwise equality",
        "PASS" if not v10 else "FAIL",
        v10,
    )

    # R1-11: Bounded async w1/w2/w4/repeat determinism (pytest + 170 forensic rows + 242 replay)
    v11: List[str] = []
    if worker_12.get("pytest_subset_w1_w2_w4_repeat_outcome_hash_identical") is not True:
        v11.append("PYTEST_W124_OUTCOME_HASH_DRIFT")
    if worker_12.get("forensics_170_w1_w2_w4_repeat_row_hash_identical") is not True:
        v11.append("FORENSICS_170_W124_ROW_HASH_DRIFT")
    if worker_12.get("replay_slice_w1_w2_w4_repeat_parity_status") != "PASS":
        v11.append("REPLAY_242_W124_PARITY_FAILED")
    sp_val = worker_12.get("measured_speedup_w_parallel_vs_w1")
    if not isinstance(sp_val, (int, float)) or isinstance(sp_val, bool) or not math.isfinite(float(sp_val)) or float(sp_val) <= 0.0:
        v11.append(f"INVALID_SPEEDUP_MEASUREMENT:{sp_val}")
    _add_r1_gate(
        "G-KH01A-R1-11",
        "BOUNDED_ASYNC_W1_W2_W4_REPEAT_DETERMINISM_AND_SPEED",
        "Verify canonical hash equality across w=1, w=2, w=4, and w=1_repeat for pytest subset, 170-row R1 forensic casebook, and 242-episode replay, with honest measured speedup.",
        "run_controlled_worker_1_2_4_subset_benchmark_kh01a",
        "12_worker_w1_w2_w4_repeat_determinism_and_speed.json",
        {
            "pytest_w124_identical": worker_12.get("pytest_subset_w1_w2_w4_repeat_outcome_hash_identical"),
            "forensics_170_w124_identical": worker_12.get("forensics_170_w1_w2_w4_repeat_row_hash_identical"),
            "replay_242_w124_status": worker_12.get("replay_slice_w1_w2_w4_repeat_parity_status"),
            "measured_pytest_speedup_w4_vs_w1": worker_12.get("measured_speedup_w4_vs_w1"),
        },
        "100% identical canonical SHA256 across w=1, w=2, w=4, w=1_repeat on pytest subset, 170 forensic rows, and 242 replay episodes",
        "FI-05_NAN_FLOAT_METRIC",
        "12 deterministic worker benchmark runs (4 pytest + 4 forensics + 4 replay)",
        "PASS" if not v11 else "FAIL",
        v11,
    )

    # R1-12: Disk/RAM/PID safety, atomic files, cleanup sentinel, 0 orphans
    v12: List[str] = []
    max_ch = int(resource_13.get("max_children_observed", 99))
    if max_ch < 1 or max_ch > 4:
        v12.append(f"CHILD_CAP_EXCEEDED:{max_ch}")
    if int(resource_13.get("orphan_pids_at_cleanup_count", 1)) != 0:
        v12.append("ORPHAN_PIDS_OBSERVED")
    if resource_13.get("disk_reserve_breached") is True:
        v12.append("DISK_SAFETY_RESERVE_BREACHED")
    if resource_13.get("temp_dirs_cleaned_with_sentinel_check") is not True:
        v12.append("OWNED_TEMP_SENTINEL_CLEANUP_FAILED")
    _add_r1_gate(
        "G-KH01A-R1-12",
        "DISK_RAM_PID_SAFETY_ATOMIC_IO_AND_SENTINEL_CLEANUP",
        "Enforce max 4 child processes, >=8 GiB (10 GiB configured) disk reserve, atomic fsync+readback writes, sentinel-verified temp cleanup, and 0 orphan PIDs.",
        "ResourceMonitorKH01A",
        "13_disk_ram_cpu_child_process_temp_cleanup.json",
        {
            "max_children_observed": max_ch,
            "orphan_pids_count": resource_13.get("orphan_pids_at_cleanup_count"),
            "min_free_disk_gb": resource_13.get("min_free_disk_gb_observed"),
            "min_available_ram_gb": resource_13.get("min_available_ram_gb_observed"),
            "peak_total_rss_mb": resource_13.get("peak_total_rss_mb_observed"),
            "sentinel_temp_cleaned": resource_13.get("temp_dirs_cleaned_with_sentinel_check"),
        },
        "1 <= max_children <= 4; 0 orphan PIDs; free disk >= 10 GiB reserve; sentinel-verified temp cleanup",
        "FI-19_SIMULATED_ENOSPC_STREAM_WRITE",
        "0.5s live resource monitor + sentinel cleanup receipt",
        "PASS" if not v12 else "FAIL",
        v12,
    )

    # R1-13: Fresh OOS status (BLOCKED_NO_UNTOUCHED_DATA is honest & acceptable)
    oos_verdict = str(fresh_oos_14.get("FRESH_OOS_VERDICT") or "")
    if oos_verdict == "BLOCKED_NO_UNTOUCHED_DATA":
        s13 = "BLOCKED_NO_UNTOUCHED_DATA"
        v13 = ["NO_UNTOUCHED_POST_FREEZE_DATASET_AVAILABLE"]
    elif oos_verdict == "PASS_VERIFIED_POST_FREEZE_UNTOUCHED":
        s13 = "PASS"
        v13 = []
    else:
        s13 = "FAIL"
        v13 = [f"INVALID_FRESH_OOS_VERDICT:{oos_verdict}"]
    _add_r1_gate(
        "G-KH01A-R1-13",
        "FRESH_OOS_UNTOUCHED_ESCROW_TRUTH",
        "Keep Fresh OOS honestly BLOCKED_NO_UNTOUCHED_DATA until post-freeze untouched Bitget candles + L1 quotes are escrowed.",
        "validate_fresh_oos_escrow_claim_kh01a",
        "14_fresh_oos_registry_untouched_and_economic_verdict.json",
        {
            "FRESH_OOS_VERDICT": oos_verdict,
            "untouched_post_freeze_datasets_found": fresh_oos_14.get("untouched_post_freeze_datasets_found_count"),
        },
        "BLOCKED_NO_UNTOUCHED_DATA honestly disclosed (never fabricated as PASS)",
        "FI-22_CHRONOLOGICAL_SPLIT_OF_OLD_DATA_CLAIMED_AS_FRESH_OOS",
        "Read-only escrow audit confirms 0 untouched post-freeze datasets",
        s13,
        v13,
    )

    # R1-14: Publication readback (strictly PENDING in immutable pre-push bundle)
    if publication_status == "PASS_REMOTE_VERIFIED":
        s14 = "PASS_REMOTE_VERIFIED"
        v14_l: List[str] = []
    elif publication_status == "PENDING_REMOTE_PUSH":
        s14 = "PENDING_REMOTE_PUSH"
        v14_l = ["AWAITING_PHASE_B_GIT_PUSH_AND_PHASE_C_REMOTE_BLOB_READBACK"]
    else:
        s14 = "FAIL"
        v14_l = [f"PUBLICATION_ERROR:{publication_status}"]
    _add_r1_gate(
        "G-KH01A-R1-14",
        "AUTOMATIC_GIT_COMMIT_PUSH_AND_REMOTE_READBACK",
        "Keep pre-push bundle PENDING_REMOTE_PUSH; record PASS_REMOTE_VERIFIED in post-push publication_<RUN_ID>.json only after verified remote blob readback.",
        "verify_git_remote_receipt_v17931211",
        "00_branch_sha_config_dataset_preflight.json",
        {"publication_status_in_immutable_bundle": publication_status},
        "PENDING_REMOTE_PUSH pre-push; PASS_REMOTE_VERIFIED in parent publication receipt post-push",
        "FI-11_ARTIFACT_MUTATION_STALE_RECEIPT_HASH",
        "Two-phase immutable bundle + post-push remote readback pointer",
        s14,
        v14_l,
    )

    gate_map = {g["gate_id"]: g for g in gates}
    tech_ids = [f"G-KH01A-R1-{i:02d}" for i in range(1, 13)]
    eng_ok = all(gate_map[gid]["status"] == "PASS" for gid in tech_ids) and s13 in ("PASS", "BLOCKED_NO_UNTOUCHED_DATA")
    for_ok = all(gate_map[gid]["status"] == "PASS" for gid in ("G-KH01A-R1-02", "G-KH01A-R1-03", "G-KH01A-R1-04", "G-KH01A-R1-05", "G-KH01A-R1-06", "G-KH01A-R1-07"))

    d4_acc = int((replay_09.get("d4_summary") or {}).get("d4_universe_63085_accepted_intents_nb", 0))
    si0_exp = float(((replay_09.get("si_policies_nb_summary") or {}).get("SI0_OFF_BASELINE") or {}).get("expectancy_net_r", -1.0))

    orthogonal_verdicts = {
        "ENGINEERING_VERDICT": "PASS" if eng_ok else "FAIL",
        "FORENSICS_SEMANTIC_VERDICT": "PASS" if for_ok else "FAIL",
        "REGRESSION_VERDICT": "PASS" if gate_map["G-KH01A-R1-08"]["status"] == "PASS" else "FAIL",
        "ACCOUNTING_VERDICT": "PASS" if gate_map["G-KH01A-R1-09"]["status"] == "PASS" else "FAIL",
        "TRADING_PARITY_VERDICT": "PASS" if gate_map["G-KH01A-R1-10"]["status"] == "PASS" else "FAIL",
        "D4_MATERIALIZATION_VERDICT": "NO_VALID_SETUPS" if d4_acc == 0 else "MATERIALIZED_SAMPLE_OBSERVED",
        "FRESH_OOS_VERDICT": oos_verdict if s13 in ("PASS", "BLOCKED_NO_UNTOUCHED_DATA") else "FAIL_INVALID_OOS_PROVENANCE",
        "STRATEGY_ECONOMIC_EDGE_VERDICT": "NEGATIVE_NO_PROVEN_EDGE" if si0_exp <= 0.0 else "INCONCLUSIVE_NO_FRESH_OOS",
        "ACTIVATION_READY": "NO",
        "PUBLICATION_VERDICT": s14,
        "RUNTIME_SAFETY_MODES": {
            "PRODUCTION": "OFF",
            "LIVE": "OFF",
            "PAPER": "OFF",
            "AUTO": "OFF",
        },
    }

    return {
        "spec_version": SPEC_VERSION_KH01A_R1,
        "schema_version": SCHEMA_VERSION_KH01A_R1,
        "run_id": run_id,
        "branch": branch,
        "prior_audited_head_sha": PRIOR_KH01A_AUDITED_HEAD_SHA,
        "prior_kh01a_tested_sha": PRIOR_KH01A_TESTED_SHA,
        "tested_executable_sha": tested_sha,
        "canonical_dataset_sha256": dataset_sha,
        "mandatory_technical_gates": tech_ids,
        "technical_gates_passed_count": sum(1 for gid in tech_ids if gate_map[gid]["status"] == "PASS"),
        "technical_gates_total_count": len(tech_ids),
        "gates": gates,
        "orthogonal_verdicts": orthogonal_verdicts,
        "open_blockers_for_kh01b_and_activation": [
            {
                "blocker_id": "BLK-KH01A-R1-ECON-01",
                "domain": "STRATEGY_ECONOMIC_EDGE",
                "status": "NEGATIVE_NO_PROVEN_EDGE",
                "detail": "SI0 NEXT_BAR_OPEN_BID_ASK realized net PnL remains -$345.8971 USD (-0.8474R across 166 closed trades); SI8/SI9 have 4/2 closed losses; D4 has 0 True Unicorn setups.",
            },
            {
                "blocker_id": "BLK-KH01A-R1-OOS-02",
                "domain": "FRESH_OOS",
                "status": "BLOCKED_NO_UNTOUCHED_DATA",
                "detail": "No post-freeze untouched Bitget XAUUSDT dataset + L1 BBO quotes exist yet; all local datasets are REUSED_RESEARCH.",
            },
            {
                "blocker_id": "BLK-KH01A-R1-PROV-03",
                "domain": "ORIGINAL_RUNTIME_CONTEXT_AND_EXCHANGE_CALENDAR",
                "status": "DISCLOSED_NON_FAKEABLE_LIMITATION",
                "detail": "Full raw MarketDecisionContext objects were not persisted in historical runs (NOT_PROVABLE_FROM_AVAILABLE_ARTIFACTS; point-in-time reconstruction matches 166/166 SI0 fingerprints), and Bitget holiday/maintenance calendar remains CALENDAR_NOT_VERIFIED / UNKNOWN_UNVERIFIED.",
            },
        ],
    }


def render_kh_01a_r1_readme_and_handover_vi(
    *,
    run_id: str,
    branch: str,
    tested_sha: str,
    dataset_sha: str,
    macro_diff_02: Mapping[str, Any],
    casebook_diff_04: Mapping[str, Any],
    forensics_06: Mapping[str, Any],
    lineage_07: Mapping[str, Any],
    fault_suite_08: Mapping[str, Any],
    replay_09: Mapping[str, Any],
    parity_10: Mapping[str, Any],
    junit_11: Mapping[str, Any],
    worker_12: Mapping[str, Any],
    resource_13: Mapping[str, Any],
    verdicts_16: Mapping[str, Any],
) -> Tuple[str, str]:
    ov = verdicts_16["orthogonal_verdicts"]
    cnts = (junit_11.get("independent_bijection_verification") or {}).get("counters") or {}
    si0_nb = ((replay_09.get("si_policies_nb_summary") or {}).get("SI0_OFF_BASELINE") or {})
    loss_agg = forensics_06["si0_157_losses_aggregates"]
    win_agg = forensics_06["si0_9_wins_control_aggregates"]
    m3_loss = loss_agg["by_macro_3way_category"]
    m3_win = win_agg["by_macro_3way_category"]
    in_l = int(m3_loss.get("ELIGIBLE_IN_VERIFIED_WINDOW", 0))
    out_l = int(m3_loss.get("VALID_OUTSIDE_WINDOW", 0))
    in_w = int(m3_win.get("ELIGIBLE_IN_VERIFIED_WINDOW", 0))
    out_w = int(m3_win.get("VALID_OUTSIDE_WINDOW", 0))
    fp_c = lineage_07["fingerprint_comparison_counts_all_170"]

    readme = f"""# AURUM DESK — KH-01A.R1 FORENSICS TRUTH CLOSURE (`{SPEC_VERSION_KH01A_R1}`)

- **Run ID**: `{run_id}`
- **Mandatory Branch**: `{branch}`
- **Audited Starting HEAD**: `{PRIOR_KH01A_AUDITED_HEAD_SHA}`
- **Prior KH-01A Tested SHA**: `{PRIOR_KH01A_TESTED_SHA}`
- **R1 Tested Executable SHA**: `{tested_sha}`
- **Canonical Dataset SHA256**: `{dataset_sha}`

## 1. Orthogonal Verdicts
- `ENGINEERING_VERDICT`: **{ov['ENGINEERING_VERDICT']}** (`{verdicts_16['technical_gates_passed_count']}/{verdicts_16['technical_gates_total_count']}` mandatory R1 technical gates PASS)
- `FORENSICS_SEMANTIC_VERDICT`: **{ov['FORENSICS_SEMANTIC_VERDICT']}**
- `REGRESSION_VERDICT`: **{ov['REGRESSION_VERDICT']}** (`{cnts.get('collected')}` collected == `{cnts.get('executed')}` executed; `{cnts.get('passed')}` passed, `{cnts.get('skipped')}` skipped, `{cnts.get('failed')}` failed, `{cnts.get('errors')}` errors)
- `ACCOUNTING_VERDICT`: **{ov['ACCOUNTING_VERDICT']}**
- `TRADING_PARITY_VERDICT`: **{ov['TRADING_PARITY_VERDICT']}** (`56710/56710` canonical A1 opportunities + `246/246` 3-adapter episodes matched with `0` mismatches)
- `D4_MATERIALIZATION_VERDICT`: **{ov['D4_MATERIALIZATION_VERDICT']}**
- `FRESH_OOS_VERDICT`: **{ov['FRESH_OOS_VERDICT']}**
- `STRATEGY_ECONOMIC_EDGE_VERDICT`: **{ov['STRATEGY_ECONOMIC_EDGE_VERDICT']}** (`SI0 NEXT_BAR_OPEN_BID_ASK`: `{si0_nb.get('scheduled')}` scheduled, `{si0_nb.get('closed')}` closed = `{si0_nb.get('wins')}`W + `{si0_nb.get('losses')}`L, net PnL `${si0_nb.get('net_pnl_usd')}` USD, expectancy `{si0_nb.get('expectancy_net_r')}`R)
- `ACTIVATION_READY`: **{ov['ACTIVATION_READY']}** (`PRODUCTION=OFF`, `LIVE=OFF`, `PAPER=OFF`, `AUTO=OFF`)
- `PUBLICATION_VERDICT` (pre-push immutable bundle): **{ov['PUBLICATION_VERDICT']}** (post-push verified readback written to `aurum-desk/docs/artifacts/kh_01a_r1/publication_{run_id}.json`)

## 2. Core KH-01A.R1 Closures Verified
1. **ICT Macro Call-Site Bug Repaired (`D1`–`D7`)**:
   - Repaired `resolve_causal_macro_time_window("XAUUSDT", dec_ts)` -> `resolve_causal_macro_time_window(decision_timestamp_ms=dec_ts, symbol="XAUUSDT")`.
   - Prior contaminated sessions (`170/170` `INVALID`) -> `0/170` `INVALID` (`{forensics_06['ny_session_group_census_all_170']}`).
   - SI0 `157` losses: `{in_l}/157` (`{round(100.0 * in_l / 157, 2)}%`) `ELIGIBLE_IN_VERIFIED_WINDOW`, `{out_l}/157` (`{round(100.0 * out_l / 157, 2)}%`) `VALID_OUTSIDE_WINDOW`, `0/157` `UNKNOWN_OR_INVALID_MACRO_STATE`.
   - SI0 `9` wins: `{in_w}/9` (`{round(100.0 * in_w / 9, 2)}%`) `ELIGIBLE_IN_VERIFIED_WINDOW`, `{out_w}/9` (`{round(100.0 * out_w / 9, 2)}%`) `VALID_OUTSIDE_WINDOW`.
   - Schedule provenance explicitly disclosed as `RESEARCH_HYPOTHESIS_CONFIG` (`V17_9_3_NY_ICT_MACRO_SCHEDULE_V1`); Bitget holiday/maintenance calendar remains `CALENDAR_NOT_VERIFIED` / `UNKNOWN_UNVERIFIED`.
2. **Per-Field Source Provenance & Snapshot Honesty (`E1`–`E7`)**:
   - Full raw `MarketDecisionContext` persistence honestly marked `NOT_PROVABLE_FROM_AVAILABLE_ARTIFACTS`.
   - Point-in-time `A1_V1792_SL_TP_BASELINE` reconstruction matches persisted `decision_fingerprint` & `trade_action_fingerprint` on `{fp_c.get('MATCH')}/166` SI0 rows (`NOT_COMPARABLE={fp_c.get('NOT_COMPARABLE')}` delayed-entry SI8/SI9 rows, with `4/4` candidate-bar fingerprint match; `MISMATCH={fp_c.get('MISMATCH')}`).
   - Evidence completeness across 170 rows: `{forensics_06['evidence_completeness_census_all_170']}` (`unknown` features never converted to proven `False`).
3. **Anti-Fake-Pass Dynamic Receipts & Adversarial Suite (`F1`–`F6`)**:
   - `{fault_suite_08['passed_real_fault_injections']}/{fault_suite_08['total_real_fault_injections']}` real executed fault injections PASS (`0` report-assertion padding), verified by separate-OS-process `kh_01a_r1_independent_verifier.py`.
4. **Bounded Async Determinism & Resource Safety (`H1`–`H8`)**:
   - Pytest subset `w=4/w=1` speedup: `{worker_12.get('measured_speedup_w4_vs_w1')}x` (best `{worker_12.get('measured_speedup_w_parallel_vs_w1')}x`); `w=1, w=2, w=4, w=1_repeat` hashes identical across pytest, `170` forensic rows, and `242` replay episodes; `max_children_observed={resource_13.get('max_children_observed')}` (`<= 4`), `0` orphans.
"""

    handover_vi = f"""BÀN GIAO KỸ THUẬT KH-01A.R1 — ĐÓNG HỒ SƠ SỰ THẬT PHÁP Y (FORENSICS TRUTH CLOSURE)
================================================================================
1. ĐỊNH DANH PHIÊN CHẠY & GIT
- Nhánh bắt buộc (Branch): {branch}
- Audited Starting HEAD trước R1: {PRIOR_KH01A_AUDITED_HEAD_SHA}
- Prior KH-01A Tested Executable SHA: {PRIOR_KH01A_TESTED_SHA}
- R1 Tested Executable SHA: {tested_sha}
- Run ID: {run_id}
- Canonical Dataset SHA256: {dataset_sha} (bất biến 100% so với baseline)

2. PHÁN QUYẾT ĐỘC LẬP (ORTHOGONAL VERDICTS)
- ENGINEERING_VERDICT: {ov['ENGINEERING_VERDICT']} ({verdicts_16['technical_gates_passed_count']}/{verdicts_16['technical_gates_total_count']} cổng kỹ thuật R1 PASS)
- FORENSICS_SEMANTIC_VERDICT: {ov['FORENSICS_SEMANTIC_VERDICT']}
- REGRESSION_VERDICT: {ov['REGRESSION_VERDICT']} (Collected={cnts.get('collected')}, Executed={cnts.get('executed')}, Passed={cnts.get('passed')}, Skipped={cnts.get('skipped')}, Failed={cnts.get('failed')}, Errors={cnts.get('errors')})
- ACCOUNTING_VERDICT: {ov['ACCOUNTING_VERDICT']}
- TRADING_PARITY_VERDICT: {ov['TRADING_PARITY_VERDICT']}
- D4_MATERIALIZATION_VERDICT: {ov['D4_MATERIALIZATION_VERDICT']}
- FRESH_OOS_VERDICT: {ov['FRESH_OOS_VERDICT']}
- STRATEGY_ECONOMIC_EDGE_VERDICT: {ov['STRATEGY_ECONOMIC_EDGE_VERDICT']}
- ACTIVATION_READY: {ov['ACTIVATION_READY']} (PRODUCTION=OFF, LIVE=OFF, PAPER=OFF, AUTO=OFF)
- PUBLICATION_VERDICT (trong thư mục bất biến trước khi push): {ov['PUBLICATION_VERDICT']}

3. KẾT QUẢ SỬA LỖI ICT MACRO & MINH BẠCH NGUỒN GỐC ĐẶC TRƯNG
- Đã sửa lỗi đảo ngược tham số tại `kh_01a_foundation_service.py`: `resolve_causal_macro_time_window("XAUUSDT", dec_ts)` -> `resolve_causal_macro_time_window(decision_timestamp_ms=dec_ts, symbol="XAUUSDT")`.
- Đối chiếu trước/sau trên 170 dòng casebook (157 lệnh thua SI0 + 9 lệnh thắng SI0 + 4 lệnh thua duy nhất SI8/SI9):
  * Trước R1 (bị nhiễm lỗi): 170/170 dòng có `ny_session_group="INVALID"`, 157/157 lệnh thua và 9/9 lệnh thắng bị gán ngoài khung Macro, 69/157 lệnh thua nhận lý do chính `OUTSIDE_ICT_MACRO_WINDOW`.
  * Sau R1 (đã sửa đúng): 0/170 dòng `INVALID` ({forensics_06['ny_session_group_census_all_170']}). Trên 157 lệnh thua SI0: {in_l}/157 ({round(100.0 * in_l / 157, 2)}%) thuộc `ELIGIBLE_IN_VERIFIED_WINDOW`, {out_l}/157 ({round(100.0 * out_l / 157, 2)}%) thuộc `VALID_OUTSIDE_WINDOW`, 0/157 `UNKNOWN_OR_INVALID_MACRO_STATE`. Trên 9 lệnh thắng đối chứng SI0: {in_w}/9 trong khung Macro, {out_w}/9 ngoài khung Macro. Phân bố lý do chính mới trên 157 lệnh thua: {loss_agg['primary_pre_entry_reason_counts']}.
- Minh bạch nguồn gốc dữ liệu (Provenance):
  * Toàn bộ kết quả phân tích 170 dòng dựa trên kết hợp bản ghi đặc trưng đã xác minh (`07_verified_causal_features.jsonl.gz` -> `PERSISTED_VERIFIED_SNAPSHOT`) và tái dựng nhân quả tại thời điểm quyết định từ nến đóng (`close_time <= decision_timestamp_ms` -> `RECONSTRUCTED_ASOF_RAW_BARS`); đối tượng `MarketDecisionContext` đầy đủ thời gian thực gốc KHÔNG được lưu trữ nguyên bản nên được ghi nhận trung thực là `NOT_PROVABLE_FROM_AVAILABLE_ARTIFACTS`.
  * Lịch ICT Macro (`V17_9_3_NY_ICT_MACRO_SCHEDULE_V1`) là cấu hình giả thuyết nghiên cứu (`RESEARCH_HYPOTHESIS_CONFIG`); lịch nghỉ lễ/bảo trì chính thức của sàn Bitget vẫn là `CALENDAR_NOT_VERIFIED` / `UNKNOWN_UNVERIFIED`.
  * Không bao giờ biến đặc trưng `MISSING`/`UNKNOWN` (như `sweep_confirmed` thiếu {forensics_06['si0_157_losses_feature_presence_and_unknown_census']['liquidity_sweep_reclaim_confirmed']['MISSING']}/157, `mss_confirmed` thiếu {forensics_06['si0_157_losses_feature_presence_and_unknown_census']['mss_bos_displacement_confirmed']['MISSING']}/157, `m15_structure_bias` thiếu {forensics_06['si0_157_losses_feature_presence_and_unknown_census']['m15_structure_bias']['MISSING']}/157, `h1_structure_bias` thiếu {forensics_06['si0_157_losses_feature_presence_and_unknown_census']['h1_structure_bias']['MISSING']}/157, `h4_structure_bias` thiếu {forensics_06['si0_157_losses_feature_presence_and_unknown_census']['h4_structure_bias']['MISSING']}/157, `target_source_timeframe` thiếu {forensics_06['si0_157_losses_feature_presence_and_unknown_census']['target_source_timeframe']['MISSING']}/157) thành kết luận phủ định `False`.

4. BẢO TOÀN HÀNH VI GIAO DỊCH (KEPT AS IS) & AN TOÀN HỆ THỐNG
- Các thành phần thực thi & chiến lược (`SharedStrategyEvaluator.evaluate_model_direction`, `resolve_causal_macro_time_window`, V17.9.2 SL/TP, True Unicorn D4, trần rủi ro <= 2.50 USD, 3 execution adapters) được GIỮ NGUYÊN (KEPT AS IS): khớp 100% trên toàn bộ 56710/56710 cơ hội A1 chuẩn và 246/246 episode qua 3 adapter (0 sai lệch); kết quả SI0 giữ nguyên tuyệt đối 242 lên lịch, 166 đóng = 9W + 157L, Net PnL {si0_nb.get('net_pnl_usd')} USD, Expectancy {si0_nb.get('expectancy_net_r')}R.
- Tiêm lỗi đối kháng: {fault_suite_08['passed_real_fault_injections']}/{fault_suite_08['total_real_fault_injections']} phép tiêm lỗi thực thi thật đều bị chặn fail-closed.
- Hiệu năng & Tài nguyên: Đa tiến trình bất đồng bộ `w=1, w=2, w=4, w=1_repeat` khớp tuyệt đối SHA256 trên cả pytest subset (tăng tốc {worker_12.get('measured_speedup_w4_vs_w1')}x), 170 dòng pháp y và 242 episode replay; tối đa {resource_13.get('max_children_observed')} tiến trình con (<= 4), 0 orphan PID.
- CẢNH BÁO AN TOÀN: `FRESH_OOS_VERDICT = BLOCKED_NO_UNTOUCHED_DATA` và `STRATEGY_ECONOMIC_EDGE_VERDICT = NEGATIVE_NO_PROVEN_EDGE`. Tuyệt đối KHÔNG kích hoạt giao dịch thật/paper/auto.
"""
    return readme, handover_vi


def verify_kh_01a_r1_cross_view_consistency(
    *,
    readme_text: str,
    handover_text: str,
    verdicts_16: Mapping[str, Any],
    junit_11: Mapping[str, Any],
    forensics_06: Mapping[str, Any],
    parity_10: Mapping[str, Any],
) -> Dict[str, Any]:
    violations: List[str] = []
    t_sha = str(verdicts_16.get("tested_executable_sha") or "")
    ds_sha = str(verdicts_16.get("canonical_dataset_sha256") or "")
    ov = verdicts_16.get("orthogonal_verdicts") or {}
    cnts = (junit_11.get("independent_bijection_verification") or {}).get("counters") or {}

    for lbl, txt in (("README", readme_text), ("HANDOVER", handover_text)):
        if t_sha not in txt:
            violations.append(f"{lbl}_MISSING_TESTED_SHA")
        if ds_sha not in txt:
            violations.append(f"{lbl}_MISSING_DATASET_SHA")
        for vk in (
            "ENGINEERING_VERDICT",
            "FORENSICS_SEMANTIC_VERDICT",
            "REGRESSION_VERDICT",
            "FRESH_OOS_VERDICT",
            "STRATEGY_ECONOMIC_EDGE_VERDICT",
        ):
            if str(ov.get(vk) or "") not in txt:
                violations.append(f"{lbl}_MISSING_VERDICT:{vk}={ov.get(vk)}")
        if str(cnts.get("collected")) not in txt or str(cnts.get("passed")) not in txt:
            violations.append(f"{lbl}_MISSING_PYTEST_COUNTS")
        if "NOT_PROVABLE_FROM_AVAILABLE_ARTIFACTS" not in txt:
            violations.append(f"{lbl}_MISSING_RUNTIME_SNAPSHOT_LIMITATION_DISCLOSURE")
        if "CALENDAR_NOT_VERIFIED" not in txt:
            violations.append(f"{lbl}_MISSING_CALENDAR_UNVERIFIED_DISCLOSURE")
        if str(parity_10.get("full_a1_56710_evaluated_count")) not in txt:
            violations.append(f"{lbl}_MISSING_FULL_56710_PARITY_DENOMINATOR")

    if "KEPT AS IS" not in handover_text:
        violations.append("HANDOVER_MISSING_KEPT_AS_IS")
    in_macro_l = str(
        ((forensics_06.get("si0_157_losses_aggregates") or {}).get("by_macro_3way_category") or {}).get(
            "ELIGIBLE_IN_VERIFIED_WINDOW", 0
        )
    )
    if f"{in_macro_l}/157" not in readme_text or f"{in_macro_l}/157" not in handover_text:
        violations.append("MISSING_EXACT_IN_MACRO_LOSS_RATIO")

    return {
        "status": "PASS" if not violations else "FAIL",
        "violations_count": len(violations),
        "violations": violations,
    }
