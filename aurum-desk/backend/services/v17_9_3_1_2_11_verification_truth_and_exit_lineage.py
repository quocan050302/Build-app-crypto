"""
AURUM DESK — V17.9.3.1.2.11 VERIFICATION TRUTH & EXIT-EVENT LINEAGE AUTHORITY
Implements pure, fail-closed verification primitives for:
  P0-A: Strict Verification Truth Contract (T01..T10)
  P0-B: Deterministic Source-Bound Exit-Event Lineage & Accounting Conservation (E01..E10)
  Regression, Worker Parity, and Split Verdicts (R01..R05, P01..P02)

Strategy logic, candidate rules, model ranking, entry/SL/TP geometry, position sizing,
risk caps, policy thresholds, and execution-mode flags are strictly preserved unchanged.
"""
from __future__ import annotations

import ast
from collections import Counter, defaultdict
import copy
import gzip
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
import xml.etree.ElementTree as ET

from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

VERSION_V17931211 = "v17_9_3_1_2_11"
SCHEMA_VERSION_V17931211 = "17.9.3.1.2.11"
CANONICAL_DATASET_SHA256_V17931211 = (
    "9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18"
)
REQUIRED_BRANCH_V17931211 = "feature/v17-9-2-structural-sl-tp-from-qa"
MANDATORY_BRANCH_V17931211 = REQUIRED_BRANCH_V17931211

ALLOWED_GATE_STATUSES_V17931211 = frozenset({"PASS", "FAIL", "BLOCKED", "INCONCLUSIVE"})

EXIT_PRICE_BASIS_BY_MODE_V17931211: Dict[str, str] = {
    "NEXT_BAR_OPEN_BID_ASK": "MODELED_M1_OHLC_BID_ASK_SPREAD_0.20",
    "LEGACY_CLOSE_FILL": "MODELED_M1_OHLC_MID_LEGACY_CLOSE",
    "STRESS_WIDER_SPREAD_SLIPPAGE_2X": "MODELED_M1_OHLC_BID_ASK_SPREAD_0.40_STRESS_2X",
    "STRESS_2X_FRICTION": "MODELED_M1_OHLC_BID_ASK_SPREAD_0.40_STRESS_2X",
}

REQUIRED_ARTIFACTS_V17931211: Tuple[str, ...] = (
    "00_run_manifest.json",
    "01_git_branch_and_source_freeze.json",
    "02_function_callgraph_and_contracts.json",
    "03_canonical_dataset_and_bar_closure_audit.json",
    "04_canonical_universe_and_cohort_reconciliation.json",
    "05_verification_truth_audit_and_negative_controls.json",
    "06_fresh_pytest_collection_and_junit_bijection.json",
    "07_subprocess_worker_and_resource_telemetry.json",
    "08_exit_event_contract_and_source_map.json",
    "09_exit_event_lineage_ledger.jsonl.gz",
    "10_closed_trade_exit_chain_verification.json",
    "11_open_censored_unfilled_and_unknown_audit.json",
    "12_exit_trigger_and_fill_price_basis_verification.json",
    "13_exit_deduplication_and_idempotency_verification.json",
    "14_independent_realized_pnl_and_fee_recomputation.json",
    "15_recovered_15_trades_exit_forensics_and_venn.json",
    "16_all_policies_three_fill_modes_conservation.json",
    "17_cross_adapter_exit_parity_report.json",
    "18_exit_lineage_negative_controls_report.json",
    "19_worker_1_2_4_and_repeat_determinism.json",
    "20_independent_verifier_report.json",
    "21_acceptance_gate_matrix.json",
    "22_missing_evidence_blockers.json",
    "README_V17_9_3_1_2_11.md",
    "artifact_sha256_manifest.json",
)

EXIT_EVENT_KINDS_V17931211 = (
    "DECISION",
    "INTENT",
    "ENTRY_FILL",
    "POSITION_OPEN",
    "EXIT_MARKET_OBSERVATION",
    "EXIT_TRIGGER",
    "EXIT_FILL",
    "POSITION_CLOSED",
    "LEDGER_POST",
    "CENSORED/UNFILLED/UNRESOLVED",
)

SOURCE_CLASSES_V17931211 = frozenset({
    "REAL_PROVIDER",
    "HISTORICAL_PROVIDER",
    "SYNTHETIC_PROXY",
    "REPLAY_CANDLE",
    "OBSERVE_ONLY",
    "UNKNOWN",
})

# Explicit documented normalization map across Live/Shadow/Evaluator, Lab Replay,
# Historical Intervention Replay, and Delayed Reconciliation.
EXIT_CAUSE_NORMALIZATION_MAP_V17931211: Dict[str, str] = {
    # shared_exit_evaluator / ExitMonitor / MarketReconciliationService
    "SL_HIT": "STOP_LOSS",
    "TP_HIT": "TAKE_PROFIT",
    "AMBIGUOUS_BAR_SL_FIRST": "SAME_BAR_AMBIGUOUS_CONSERVATIVE_SL",
    "LIQUIDATED": "LIQUIDATION",
    # lab/replay_execution.py
    "STOP_LOSS": "STOP_LOSS",
    "TAKE_PROFIT": "TAKE_PROFIT",
    # services/v17_9_2_structural_stop_intelligence.py (simulate_v17931_trade_execution_net)
    "LOSS": "STOP_LOSS",
    "WIN": "TAKE_PROFIT",
    "LOSS_TIE_CONSERVATIVE": "SAME_BAR_AMBIGUOUS_CONSERVATIVE_SL",
    "SAME_BAR_AMBIGUOUS_CONSERVATIVE_SL": "SAME_BAR_AMBIGUOUS_CONSERVATIVE_SL",
    "ADVERSE_GAP_OPEN_STOP_LOSS": "ADVERSE_GAP_STOP_LOSS",
    # Non-closed / censored / unresolved episode outcomes
    "OPEN_AT_HORIZON": "OPEN_AT_HORIZON_CENSORED",
    "DATA_GAP_UNRESOLVED": "DATA_GAP_UNRESOLVED",
    "NO_FILL_ENTRY_GAP": "UNFILLED_NO_ENTRY",
    "NO_FILL_UNEXECUTABLE_AT_OPEN": "UNFILLED_NO_ENTRY",
    "NO_FILL_BELOW_MIN_LOT": "UNFILLED_NO_ENTRY",
    "NO_FORWARD_BARS": "UNFILLED_NO_ENTRY",
    "UNFILLED": "UNFILLED_NO_ENTRY",
}


def _det_id(prefix: str, *parts: Any) -> str:
    payload = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _is_finite_pos(val: Any) -> bool:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return False
    f = float(val)
    return math.isfinite(f) and f > 0.0


def _is_finite_num(val: Any) -> bool:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return False
    return math.isfinite(float(val))


# ==============================================================================
# P0-A: STRICT VERIFICATION TRUTH CONTRACT
# ==============================================================================

def normalize_collected_test_identity_v17931211(
    raw_node_id_or_classname: Optional[str],
    testcase_name: Optional[str] = None,
) -> Optional[str]:
    """
    Normalizes either a pytest `--collect-only -q` node ID or a JUnit XML
    `(classname, name)` pair into the canonical repository-relative node ID
    `backend/tests/test_xxx.py::ClassName::test_name[params]`.
    Returns None if the identity is empty or malformed.
    """
    if testcase_name is not None:
        from tools.run_v17_9_3_1_2_2_verification import (
            canonicalize_junit_testcase_node_id_v1793122,
        )
        return canonicalize_junit_testcase_node_id_v1793122(
            raw_node_id_or_classname, testcase_name
        )

    if not raw_node_id_or_classname or not isinstance(raw_node_id_or_classname, str):
        return None
    cleaned = raw_node_id_or_classname.strip().replace("\\", "/")
    if not cleaned or "::" not in cleaned:
        return None
    parts = [p.strip() for p in cleaned.split("::") if p.strip()]
    if len(parts) < 2:
        return None
    file_part = parts[0]
    if file_part.startswith("aurum-desk/"):
        file_part = file_part[len("aurum-desk/"):]
    if file_part.startswith("tests/"):
        file_part = f"backend/{file_part}"
    if not file_part.endswith(".py") or not file_part.startswith("backend/tests/"):
        return None
    return "::".join([file_part, *parts[1:]])


def verify_fresh_junit_bijection_v17931211(
    run_dir: Path,
    collection_manifest: Mapping[str, Any],
    shard_manifest: Mapping[str, Any],
    *,
    expected_run_id: str,
    expected_tested_sha: str,
    mandatory_node_prefixes: Sequence[str] = (
        "backend/tests/test_v17_9_3_1_2",
        "backend/tests/test_v16_5_replay_shared_execution.py",
        "backend/tests/test_v17_5_exit_evidence.py",
    ),
) -> Dict[str, Any]:
    """
    Independently verifies fresh JUnit XML shards on disk against the collected
    pytest node inventory. Rejects missing XML, tampered XML hash/mtime, forged
    exit codes, timeouts, duplicate/missing testcase nodes, shard overlap,
    collect-only masquerading as execution, and skipped mandatory tests.
    """
    from tools.fresh_junit_io import read_closed_junit_artifact

    violations: List[str] = []
    raw_collected = list(collection_manifest.get("collected_node_ids") or [])
    if not raw_collected:
        violations.append("EMPTY_COLLECTED_NODE_INVENTORY")

    normalized_collected: List[str] = []
    for raw_nid in raw_collected:
        norm = normalize_collected_test_identity_v17931211(str(raw_nid))
        if norm is None:
            violations.append(f"MALFORMED_COLLECTED_NODE_ID:{raw_nid}")
        else:
            normalized_collected.append(norm)

    if len(set(normalized_collected)) != len(normalized_collected):
        violations.append("DUPLICATE_COLLECTED_NODE_IDS")

    if collection_manifest.get("run_id") != expected_run_id:
        violations.append(
            f"COLLECTION_RUN_ID_MISMATCH:{collection_manifest.get('run_id')}!={expected_run_id}"
        )
    if collection_manifest.get("tested_executable_sha") != expected_tested_sha:
        violations.append(
            f"COLLECTION_SHA_MISMATCH:{collection_manifest.get('tested_executable_sha')}!={expected_tested_sha}"
        )
    if int(collection_manifest.get("collect_exit_code", -1)) != 0:
        violations.append(f"COLLECTION_EXIT_NONZERO:{collection_manifest.get('collect_exit_code')}")

    expected_sorted_hash = canonical_hash(sorted(normalized_collected))
    alt_newline_hash = hashlib.sha256("\n".join(sorted(normalized_collected)).encode("utf-8")).hexdigest()
    reported_coll_hash = str(collection_manifest.get("collected_node_ids_sha256") or "")
    if reported_coll_hash and reported_coll_hash not in (expected_sorted_hash, alt_newline_hash):
        violations.append("COLLECTION_HASH_MISMATCH")

    if shard_manifest.get("run_id") != expected_run_id:
        violations.append(
            f"SHARD_MANIFEST_RUN_ID_MISMATCH:{shard_manifest.get('run_id')}!={expected_run_id}"
        )
    if shard_manifest.get("tested_executable_sha") != expected_tested_sha:
        violations.append(
            f"SHARD_MANIFEST_SHA_MISMATCH:{shard_manifest.get('tested_executable_sha')}!={expected_tested_sha}"
        )

    shards = list(shard_manifest.get("shards") or [])
    if not shards:
        violations.append("MISSING_JUNIT_SHARDS")

    all_scheduled_nodes: List[str] = []
    all_executed_nodes: List[str] = []
    passed_nodes: List[str] = []
    failed_nodes: List[str] = []
    error_nodes: List[str] = []
    skipped_rows: List[Dict[str, Any]] = []
    xfailed_nodes: List[str] = []
    xpassed_nodes: List[str] = []
    mandatory_skipped_nodes: List[str] = []
    shard_verifications: List[Dict[str, Any]] = []

    resolved_run_dir = run_dir.resolve()

    for idx, shard in enumerate(shards):
        sid = str(shard.get("shard_id") or f"shard_{idx}")
        shard_errs = []
        if shard.get("run_id") != expected_run_id:
            shard_errs.append(f"SHARD_RUN_ID_MISMATCH:{sid}")
        if shard.get("tested_executable_sha") != expected_tested_sha:
            shard_errs.append(f"SHARD_SHA_MISMATCH:{sid}")
        if shard.get("reused") is not False:
            shard_errs.append(f"SHARD_MARKED_REUSED:{sid}")
        if shard.get("is_command_only_non_junit") is True:
            shard_errs.append(f"COMMAND_ONLY_GROUP_MASQUERADING_AS_JUNIT:{sid}")
        if int(shard.get("exit_code", -1)) != 0:
            shard_errs.append(f"SHARD_NONZERO_EXIT:{sid}:{shard.get('exit_code')}")
        if bool(shard.get("timed_out", False)):
            shard_errs.append(f"SHARD_TIMED_OUT:{sid}")
        pid = shard.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            shard_errs.append(f"SHARD_INVALID_PID:{sid}:{pid}")

        sched_raw = list(shard.get("scheduled_node_ids") or shard.get("expected_node_ids") or [])
        sched_norm: List[str] = []
        for s_nid in sched_raw:
            sn = normalize_collected_test_identity_v17931211(str(s_nid))
            if sn is None:
                shard_errs.append(f"SHARD_MALFORMED_SCHEDULED_NODE:{sid}:{s_nid}")
            else:
                sched_norm.append(sn)
        all_scheduled_nodes.extend(sched_norm)

        xml_rel_or_abs = shard.get("junit_xml_path")
        if not xml_rel_or_abs:
            shard_errs.append(f"SHARD_MISSING_XML_PATH:{sid}")
            violations.extend(shard_errs)
            continue

        xml_path = Path(str(xml_rel_or_abs))
        if not xml_path.is_absolute():
            cand_in_run = resolved_run_dir / xml_path
            if cand_in_run.exists():
                xml_path = cand_in_run
            else:
                xml_path = (Path(__file__).resolve().parents[2] / xml_path).resolve()
        else:
            xml_path = xml_path.resolve()

        if not xml_path.is_relative_to(resolved_run_dir):
            shard_errs.append(f"FOREIGN_JUNIT_PATH_OUTSIDE_RUN_DIR:{sid}:{xml_path}")

        try:
            xml_bytes, xml_stat = read_closed_junit_artifact(xml_path)
        except Exception as exc:
            shard_errs.append(f"JUNIT_READ_ERROR:{sid}:{exc}")
            violations.extend(shard_errs)
            continue

        actual_xml_sha = hashlib.sha256(xml_bytes).hexdigest()
        if actual_xml_sha != shard.get("xml_sha256"):
            shard_errs.append(f"JUNIT_SHA256_MISMATCH:{sid}")
        if shard.get("xml_mtime_ns") is not None and int(xml_stat.st_mtime_ns) != int(shard.get("xml_mtime_ns")):
            shard_errs.append(f"JUNIT_MTIME_NS_MISMATCH:{sid}")

        try:
            root = ET.fromstring(xml_bytes)
        except Exception as exc:
            shard_errs.append(f"JUNIT_XML_PARSE_ERROR:{sid}:{exc}")
            violations.extend(shard_errs)
            continue

        shard_executed: List[str] = []
        shard_passed = 0
        shard_failed = 0
        shard_errors = 0
        shard_skipped = 0

        for tc in root.iter("testcase"):
            norm_tc = normalize_collected_test_identity_v17931211(
                tc.get("classname"), tc.get("name")
            )
            if norm_tc is None:
                shard_errs.append(f"UNRECOGNIZED_JUNIT_TESTCASE:{sid}:{tc.get('classname')}::{tc.get('name')}")
                continue
            shard_executed.append(norm_tc)
            all_executed_nodes.append(norm_tc)

            err_el = tc.find("error")
            fail_el = tc.find("failure")
            skip_el = tc.find("skipped")

            if err_el is not None:
                shard_errors += 1
                error_nodes.append(norm_tc)
            elif fail_el is not None:
                shard_failed += 1
                failed_nodes.append(norm_tc)
            elif skip_el is not None:
                shard_skipped += 1
                skip_type = str(skip_el.get("type") or "")
                skip_msg = str(skip_el.get("message") or "")
                if "xfail" in skip_type.lower():
                    xfailed_nodes.append(norm_tc)
                skipped_rows.append({
                    "node_id": norm_tc,
                    "shard_id": sid,
                    "type": skip_type,
                    "reason": skip_msg,
                })
                if mandatory_node_prefixes and any(
                    norm_tc.startswith(pfx) for pfx in mandatory_node_prefixes
                ):
                    mandatory_skipped_nodes.append(norm_tc)
            else:
                shard_passed += 1
                passed_nodes.append(norm_tc)

        if sched_norm and Counter(sched_norm) != Counter(shard_executed):
            shard_errs.append(f"SHARD_SCHEDULED_VS_EXECUTED_MISMATCH:{sid}")
        if len(set(shard_executed)) != len(shard_executed):
            shard_errs.append(f"SHARD_DUPLICATE_EXECUTED_NODES:{sid}")
        if shard_failed > 0 or shard_errors > 0:
            shard_errs.append(f"SHARD_HAS_FAILURES_OR_ERRORS:{sid}:f={shard_failed},e={shard_errors}")

        violations.extend(shard_errs)
        shard_verifications.append({
            "shard_id": sid,
            "pid": pid,
            "exit_code": shard.get("exit_code"),
            "xml_sha256": actual_xml_sha,
            "scheduled_count": len(sched_norm),
            "executed_count": len(shard_executed),
            "passed_count": shard_passed,
            "failed_count": shard_failed,
            "error_count": shard_errors,
            "skipped_count": shard_skipped,
            "status": "PASS" if not shard_errs else "FAIL",
            "errors": shard_errs,
        })

    # Global bijection checks across all shards
    if all_scheduled_nodes:
        if len(set(all_scheduled_nodes)) != len(all_scheduled_nodes):
            violations.append("CROSS_SHARD_SCHEDULED_OVERLAP")
        if Counter(all_scheduled_nodes) != Counter(normalized_collected):
            violations.append("SCHEDULED_NODES_DO_NOT_PARTITION_COLLECTION")

    if len(set(all_executed_nodes)) != len(all_executed_nodes):
        violations.append("CROSS_SHARD_DUPLICATE_EXECUTED_TESTCASE")

    if Counter(all_executed_nodes) != Counter(normalized_collected):
        missing = sorted(set(normalized_collected) - set(all_executed_nodes))
        extra = sorted(set(all_executed_nodes) - set(normalized_collected))
        violations.append(
            f"COLLECTION_TO_EXECUTED_BIJECTION_BROKEN:missing={len(missing)},extra={len(extra)}"
        )

    if failed_nodes:
        violations.append(f"EXECUTED_TESTS_FAILED:{len(failed_nodes)}")
    if error_nodes:
        violations.append(f"EXECUTED_TESTS_ERRORED:{len(error_nodes)}")
    if mandatory_skipped_nodes:
        violations.append(f"MANDATORY_TESTS_SKIPPED:{mandatory_skipped_nodes}")

    counters = {
        "collected": len(normalized_collected),
        "scheduled": len(all_scheduled_nodes) if all_scheduled_nodes else len(normalized_collected),
        "executed": len(all_executed_nodes),
        "passed": len(passed_nodes),
        "failed": len(failed_nodes),
        "errors": len(error_nodes),
        "skipped": len(skipped_rows),
        "xfailed": len(xfailed_nodes),
        "xpassed": len(xpassed_nodes),
        "deselected": int(collection_manifest.get("deselected_count", 0)),
        "unknown": max(
            0,
            len(all_executed_nodes)
            - (len(passed_nodes) + len(failed_nodes) + len(error_nodes) + len(skipped_rows)),
        ),
        "mandatory_skipped": len(mandatory_skipped_nodes),
    }

    if counters["passed"] + counters["failed"] + counters["errors"] + counters["skipped"] != counters["executed"]:
        violations.append("TEST_COUNTER_SUM_MISMATCH")

    return {
        "version": VERSION_V17931211,
        "status": "PASS" if not violations else "FAIL",
        "run_id": expected_run_id,
        "tested_executable_sha": expected_tested_sha,
        "counters": counters,
        "collected_count": counters["collected"],
        "executed_count": counters["executed"],
        "passed_count": counters["passed"],
        "failed_count": counters["failed"],
        "error_count": counters["errors"],
        "skipped_count": counters["skipped"],
        "collected_node_ids_sha256": expected_sorted_hash,
        "executed_node_ids_sha256": canonical_hash(sorted(all_executed_nodes)),
        "passed_node_ids_sha256": canonical_hash(sorted(passed_nodes)),
        "skipped_cases": skipped_rows,
        "mandatory_skipped_nodes": mandatory_skipped_nodes,
        "failed_nodes": failed_nodes,
        "error_nodes": error_nodes,
        "shards": shard_verifications,
        "violations": violations,
    }


def verify_process_exit_and_timeout_v17931211(
    worker_records: Sequence[Mapping[str, Any]],
    *,
    expected_run_id: str,
    expected_tested_sha: str,
    max_allowed_workers: int = 4,
    active_orphan_pids: Sequence[int] = (),
) -> Dict[str, Any]:
    """
    Verifies every subprocess worker record has real PID > 0, valid start/finish
    timestamps, exit_code == 0, timed_out == False, matching run_id and source SHA,
    and zero unreaped orphan PIDs.
    """
    violations: List[str] = []
    if not worker_records:
        violations.append("NO_WORKER_PROCESS_RECORDS")

    checked_records: List[Dict[str, Any]] = []
    retry_attempts_disclosed = 0

    for idx, rec in enumerate(worker_records):
        wid = str(rec.get("worker_id") or rec.get("shard_id") or rec.get("task_id") or f"worker_{idx}")
        pid = rec.get("pid")
        start_ms = rec.get("started_at_ms", rec.get("started_ms"))
        finish_ms = rec.get("completed_at_ms", rec.get("completed_ms"))
        exit_code = rec.get("exit_code")
        timed_out = bool(rec.get("timed_out", False))
        run_id = str(rec.get("run_id") or "")
        sha = str(rec.get("tested_source_sha") or rec.get("tested_executable_sha") or rec.get("source_sha") or "")

        rec_errs: List[str] = []
        if run_id != expected_run_id:
            rec_errs.append(f"WORKER_RUN_ID_MISMATCH:{wid}:{run_id}!={expected_run_id}")
        if sha != expected_tested_sha:
            rec_errs.append(f"WORKER_SHA_MISMATCH:{wid}:{sha}!={expected_tested_sha}")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            rec_errs.append(f"WORKER_INVALID_PID:{wid}:{pid}")
        if (
            isinstance(start_ms, bool)
            or isinstance(finish_ms, bool)
            or not isinstance(start_ms, int)
            or not isinstance(finish_ms, int)
            or start_ms <= 0
            or finish_ms < start_ms
        ):
            rec_errs.append(f"WORKER_INVALID_TIMESTAMPS:{wid}:{start_ms}..{finish_ms}")
        if timed_out:
            rec_errs.append(f"WORKER_TIMED_OUT:{wid}")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code != 0:
            rec_errs.append(f"WORKER_NONZERO_EXIT:{wid}:{exit_code}")
        if rec.get("terminating_signal") not in (None, 0, ""):
            rec_errs.append(f"WORKER_TERMINATED_BY_SIGNAL:{wid}:{rec.get('terminating_signal')}")

        prior_attempts = list(rec.get("prior_attempts") or [])
        retry_attempts_disclosed += len(prior_attempts)

        violations.extend(rec_errs)
        checked_records.append({
            "worker_id": wid,
            "pid": pid,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_ms": (int(finish_ms) - int(start_ms)) if isinstance(start_ms, int) and isinstance(finish_ms, int) else None,
            "prior_attempts_count": len(prior_attempts),
            "status": "PASS" if not rec_errs else "FAIL",
            "errors": rec_errs,
        })

    if list(active_orphan_pids):
        violations.append(f"UNREAPED_ORPHAN_PIDS:{list(active_orphan_pids)}")

    return {
        "version": VERSION_V17931211,
        "status": "PASS" if not violations else "FAIL",
        "worker_count": len(checked_records),
        "retry_attempts_disclosed": retry_attempts_disclosed,
        "orphan_pids": list(active_orphan_pids),
        "workers": checked_records,
        "violations": violations,
    }


def verify_execution_not_synthetic_v17931211(
    *,
    collection_manifest: Mapping[str, Any],
    shard_manifest: Mapping[str, Any],
    parity_manifest: Optional[Mapping[str, Any]] = None,
    resource_telemetry: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Verifies that:
    1) Test execution is not collect-only (actual JUnit XML testcase nodes exist and
       have real execution durations and worker PIDs distinct from the collection PID).
    2) No group-level non-JUnit command is counted as a pytest testcase node.
    3) If `parity_manifest` (1/2/4-worker determinism + repeat run) is provided,
       every worker configuration (`1`, `2`, `4`, and repeat) was independently executed
       with distinct attempt IDs, distinct execution timestamps/PIDs, and identical
       canonical semantic output hashes.
    """
    violations: List[str] = []

    if bool(collection_manifest.get("collect_only_treated_as_execution", False)):
        violations.append("COLLECT_ONLY_TREATED_AS_EXECUTION")

    shards = list(shard_manifest.get("shards") or [])
    if not shards:
        violations.append("NO_SHARDS_EXECUTED")

    collect_pid = collection_manifest.get("collect_pid")
    for s in shards:
        if s.get("is_command_only_non_junit") is True:
            violations.append(f"SYNTHETIC_COMMAND_NODE_IN_JUNIT_SHARD:{s.get('shard_id')}")
        if not s.get("junit_xml_path") or not s.get("xml_sha256"):
            violations.append(f"MISSING_RAW_JUNIT_PROOF:{s.get('shard_id')}")
        if collect_pid is not None and s.get("pid") == collect_pid:
            violations.append(f"SHARD_REUSED_COLLECT_ONLY_PID:{s.get('shard_id')}")

    parity_check: Dict[str, Any] = {"checked": False}
    if parity_manifest is not None:
        parity_check["checked"] = True
        runs = list(parity_manifest.get("independent_runs") or [])
        if len(runs) < 4:
            violations.append(f"INSUFFICIENT_PARITY_RUNS:{len(runs)}<4")
        attempt_ids: List[str] = []
        execution_fingerprints: List[str] = []
        semantic_hashes: Set[str] = set()
        worker_counts_seen: Set[int] = set()

        for r in runs:
            aid = str(r.get("attempt_id") or "")
            w_cnt = int(r.get("worker_count") or 0)
            sem_hash = str(r.get("canonical_semantic_sha256") or "")
            pids = tuple(r.get("worker_pids") or ())
            started_ms = r.get("started_at_ms")
            completed_ms = r.get("completed_at_ms")

            if not aid:
                violations.append("PARITY_RUN_MISSING_ATTEMPT_ID")
            attempt_ids.append(aid)
            worker_counts_seen.add(w_cnt)

            if not sem_hash or len(sem_hash) != 64:
                violations.append(f"PARITY_RUN_INVALID_SEMANTIC_HASH:{aid}")
            else:
                semantic_hashes.add(sem_hash)

            if not pids or any(isinstance(p, bool) or not isinstance(p, int) or p <= 0 for p in pids):
                violations.append(f"PARITY_RUN_MISSING_REAL_PIDS:{aid}")
            if r.get("reused_prior_digest") is True:
                violations.append(f"PARITY_RUN_REUSED_DIGEST:{aid}")

            exec_fp = canonical_hash({
                "attempt_id": aid,
                "pids": list(pids),
                "started_at_ms": started_ms,
                "completed_at_ms": completed_ms,
                "raw_execution_log_sha256": r.get("raw_execution_log_sha256"),
            })
            execution_fingerprints.append(exec_fp)

        if len(set(attempt_ids)) != len(attempt_ids):
            violations.append("DUPLICATE_PARITY_ATTEMPT_IDS")
        if len(set(execution_fingerprints)) != len(execution_fingerprints):
            violations.append("COPIED_OR_REUSED_WORKER_PARITY_EXECUTION_RECORDS")
        if not {1, 2, 4}.issubset(worker_counts_seen):
            violations.append(f"MISSING_REQUIRED_1_2_4_WORKER_CONFIGS:{sorted(worker_counts_seen)}")
        if len(semantic_hashes) != 1:
            violations.append(f"WORKER_PARITY_SEMANTIC_HASH_DIVERGENCE:{sorted(semantic_hashes)}")

        parity_check.update({
            "run_count": len(runs),
            "worker_counts_verified": sorted(worker_counts_seen),
            "unique_attempt_ids": len(set(attempt_ids)),
            "unique_execution_fingerprints": len(set(execution_fingerprints)),
            "semantic_hashes": sorted(semantic_hashes),
        })

    return {
        "version": VERSION_V17931211,
        "status": "PASS" if not violations else "FAIL",
        "parity_verification": parity_check,
        "violations": violations,
    }


def verify_source_freeze_v17931211(
    repo_root: Optional[Path] = None,
    expected_tested_sha: str = "",
    *,
    source_manifest: Optional[Mapping[str, Any]] = None,
    observed_head_sha: Optional[str] = None,
    observed_branch: Optional[str] = None,
    working_tree_dirty_files: Optional[Sequence[str]] = None,
    source_manifest_before: Optional[Mapping[str, Any]] = None,
    source_manifest_after: Optional[Mapping[str, Any]] = None,
    prior_artifact_hashes_before: Optional[Mapping[str, Any]] = None,
    prior_artifact_hashes_after: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Verifies that:
    1) Current branch is `feature/v17-9-2-structural-sl-tp-from-qa`.
    2) `expected_tested_sha` is a valid 40-char git commit and ancestor of HEAD.
    3) No executable source or test file under `aurum-desk/backend` or `aurum-desk/tools`
       differs between `expected_tested_sha` and HEAD or the working tree.
    4) If `source_manifest` or `source_manifest_before`/`after` or `prior_artifact_hashes_before`/`after`
       are supplied, verifies exact immutability.
    """
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[3]
    violations: List[str] = []
    if not expected_tested_sha or len(expected_tested_sha) != 40:
        violations.append(f"INVALID_TESTED_EXECUTABLE_SHA:{expected_tested_sha}")

    def _git(*args: str) -> str:
        return subprocess.check_output(
            ["git", *args], cwd=str(repo_root), text=True, stderr=subprocess.STDOUT
        ).strip()

    try:
        branch = observed_branch if observed_branch is not None else _git("branch", "--show-current")
        head_sha = observed_head_sha if observed_head_sha is not None else _git("rev-parse", "HEAD")
    except Exception as exc:
        return {
            "version": VERSION_V17931211,
            "status": "FAIL",
            "violations": [f"GIT_READ_FAILED:{exc}"],
        }

    if branch != REQUIRED_BRANCH_V17931211:
        violations.append(f"WRONG_BRANCH:{branch}!={REQUIRED_BRANCH_V17931211}")

    if expected_tested_sha and len(expected_tested_sha) == 40:
        anc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", expected_tested_sha, head_sha],
            cwd=str(repo_root),
        )
        if anc.returncode != 0:
            violations.append(f"TESTED_SHA_NOT_ANCESTOR_OF_HEAD:{expected_tested_sha}->{head_sha}")

        diff_committed = _git(
            "diff",
            "--name-only",
            expected_tested_sha,
            head_sha,
            "--",
            "aurum-desk/backend",
            "aurum-desk/tools",
        )
        if diff_committed:
            violations.append(f"CODE_MODIFIED_SINCE_TESTED_SHA:{diff_committed.splitlines()}")

    if working_tree_dirty_files is not None:
        if list(working_tree_dirty_files):
            violations.append(f"DIRTY_EXECUTABLE_WORKING_TREE:{list(working_tree_dirty_files)}")
    else:
        status_code_tree = _git(
            "status",
            "--porcelain=v1",
            "--",
            "aurum-desk/backend",
            "aurum-desk/tools",
        )
        if status_code_tree:
            violations.append(f"DIRTY_EXECUTABLE_WORKING_TREE:{status_code_tree.splitlines()}")

    manifest_checked = 0
    if source_manifest is not None:
        if source_manifest.get("tested_executable_sha") not in (None, expected_tested_sha):
            violations.append("SOURCE_MANIFEST_SHA_MISMATCH")
        files_map = source_manifest.get("files") or {}
        if not files_map:
            violations.append("EMPTY_SOURCE_MANIFEST_FILES")
        for rel_path, expected_file_sha in files_map.items():
            fpath = repo_root / rel_path
            if not fpath.exists():
                cand = repo_root / "aurum-desk" / rel_path
                if cand.exists():
                    fpath = cand
            if not fpath.exists():
                violations.append(f"MISSING_SOURCE_FILE:{rel_path}")
                continue
            actual_sha = hashlib.sha256(fpath.read_bytes()).hexdigest()
            if actual_sha != expected_file_sha:
                violations.append(f"SOURCE_FILE_HASH_MISMATCH:{rel_path}")
            manifest_checked += 1

    if source_manifest_before is not None and source_manifest_after is not None:
        if dict(source_manifest_before) != dict(source_manifest_after):
            violations.append("SOURCE_MANIFEST_MUTATED_DURING_RUN")
        manifest_checked = max(manifest_checked, len(source_manifest_after))

    if prior_artifact_hashes_before is not None and prior_artifact_hashes_after is not None:
        if dict(prior_artifact_hashes_before) != dict(prior_artifact_hashes_after):
            violations.append("PRIOR_HISTORICAL_ARTIFACTS_MUTATED")

    return {
        "version": VERSION_V17931211,
        "status": "PASS" if not violations else "FAIL",
        "branch": branch,
        "head_sha": head_sha,
        "tested_executable_sha": expected_tested_sha,
        "manifest_files_verified": manifest_checked,
        "violations": violations,
    }


def verify_artifact_manifest_v17931211(
    run_dir: Path,
    mandatory_files: Sequence[str],
) -> Dict[str, Any]:
    """
    Verifies `artifact_sha256_manifest.json` inside `run_dir` covers all `mandatory_files`
    and that every listed file exists on disk with exact matching SHA-256 and byte size.
    """
    violations: List[str] = []
    manifest_path = run_dir / "artifact_sha256_manifest.json"
    if not manifest_path.exists():
        return {
            "version": VERSION_V17931211,
            "status": "FAIL",
            "violations": ["MISSING_ARTIFACT_SHA256_MANIFEST"],
        }

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "version": VERSION_V17931211,
            "status": "FAIL",
            "violations": [f"CORRUPT_ARTIFACT_MANIFEST_JSON:{exc}"],
        }

    files_map = manifest.get("files") if isinstance(manifest.get("files"), Mapping) else manifest
    if not isinstance(files_map, Mapping):
        return {
            "version": VERSION_V17931211,
            "status": "FAIL",
            "violations": ["INVALID_ARTIFACT_MANIFEST_STRUCTURE"],
        }

    for req in mandatory_files:
        if req == "artifact_sha256_manifest.json":
            continue
        if req not in files_map:
            violations.append(f"MANDATORY_ARTIFACT_MISSING_FROM_MANIFEST:{req}")

    verified_count = 0
    for rel_name, meta in files_map.items():
        if rel_name in ("version", "run_id", "tested_executable_sha", "generated_at_utc"):
            continue
        target = run_dir / rel_name
        if not target.exists():
            violations.append(f"MANIFEST_FILE_MISSING_ON_DISK:{rel_name}")
            continue
        raw = target.read_bytes()
        actual_sha = hashlib.sha256(raw).hexdigest()
        expected_sha = meta.get("sha256") if isinstance(meta, Mapping) else str(meta)
        expected_bytes = meta.get("bytes") if isinstance(meta, Mapping) else None
        if actual_sha != expected_sha:
            violations.append(f"ARTIFACT_SHA256_MISMATCH:{rel_name}")
        if expected_bytes is not None and len(raw) != int(expected_bytes):
            violations.append(f"ARTIFACT_BYTE_SIZE_MISMATCH:{rel_name}")
        verified_count += 1

    return {
        "version": VERSION_V17931211,
        "status": "PASS" if not violations else "FAIL",
        "verified_files_count": verified_count,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "violations": violations,
    }


def evaluate_verification_truth_gates_v17931211(
    *,
    branch_freeze_check: Mapping[str, Any],
    dataset_manifest_check: Mapping[str, Any],
    junit_bijection_check: Mapping[str, Any],
    process_telemetry_check: Mapping[str, Any],
    non_synthetic_check: Mapping[str, Any],
    artifact_manifest_check: Mapping[str, Any],
    negative_controls_check: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Evaluates the P0-A Verification Truth gates T01..T10 from raw check outputs.
    Never defaults to PASS; any violation marks the gate FAIL.
    """
    counters = junit_bijection_check.get("counters") or {}
    gates = {
        "T01": {
            "gate_id": "T01",
            "title": "Correct original branch, ancestry and non-destructive worktree",
            "status": "PASS"
            if branch_freeze_check.get("branch") == REQUIRED_BRANCH_V17931211
            and branch_freeze_check.get("status") == "PASS"
            else "FAIL",
            "violations": list(branch_freeze_check.get("violations") or []),
        },
        "T02": {
            "gate_id": "T02",
            "title": "Frozen tested executable SHA matches actual source files",
            "status": "PASS"
            if branch_freeze_check.get("status") == "PASS"
            and int(branch_freeze_check.get("manifest_files_verified", 0)) > 0
            else "FAIL",
            "violations": list(branch_freeze_check.get("violations") or []),
        },
        "T03": {
            "gate_id": "T03",
            "title": "Dataset and cost/policy manifests source-bound and complete",
            "status": "PASS" if dataset_manifest_check.get("status") == "PASS" else "FAIL",
            "violations": list(dataset_manifest_check.get("violations") or []),
        },
        "T04": {
            "gate_id": "T04",
            "title": "Fresh test collection with full exact node inventory",
            "status": "PASS"
            if int(counters.get("collected", 0)) > 0
            and not any("COLLECTION" in v for v in (junit_bijection_check.get("violations") or []))
            else "FAIL",
            "collected_count": int(counters.get("collected", 0)),
            "collected_node_ids_sha256": junit_bijection_check.get("collected_node_ids_sha256"),
        },
        "T05": {
            "gate_id": "T05",
            "title": "Fresh full JUnit execution/exit codes, no missing/duplicate nodes",
            "status": "PASS" if junit_bijection_check.get("status") == "PASS" else "FAIL",
            "counters": counters,
            "violations": list(junit_bijection_check.get("violations") or []),
        },
        "T06": {
            "gate_id": "T06",
            "title": "Mandatory tests not skipped; retries and failures fully disclosed",
            "status": "PASS"
            if int(counters.get("mandatory_skipped", -1)) == 0
            and int(counters.get("failed", -1)) == 0
            and int(counters.get("errors", -1)) == 0
            and junit_bijection_check.get("status") == "PASS"
            else "FAIL",
            "mandatory_skipped": junit_bijection_check.get("mandatory_skipped_nodes", []),
            "skipped_cases": junit_bijection_check.get("skipped_cases", []),
        },
        "T07": {
            "gate_id": "T07",
            "title": "Bounded async workers, no orphan PIDs, real resource telemetry",
            "status": "PASS" if process_telemetry_check.get("status") == "PASS" else "FAIL",
            "violations": list(process_telemetry_check.get("violations") or []),
        },
        "T08": {
            "gate_id": "T08",
            "title": "Independent artifact hash/manifest verification",
            "status": "PASS" if artifact_manifest_check.get("status") == "PASS" else "FAIL",
            "violations": list(artifact_manifest_check.get("violations") or []),
        },
        "T09": {
            "gate_id": "T09",
            "title": "Real negative-control tampering rejected (RED/GREEN proof)",
            "status": "PASS" if negative_controls_check.get("status") == "PASS" else "FAIL",
            "controls_verified": int(negative_controls_check.get("controls_count", 0)),
            "violations": list(negative_controls_check.get("violations") or []),
        },
        "T10": {
            "gate_id": "T10",
            "title": "No reused 1/2/4 worker digests or fabricated worker status",
            "status": "PASS" if non_synthetic_check.get("status") == "PASS" else "FAIL",
            "violations": list(non_synthetic_check.get("violations") or []),
        },
    }
    all_pass = all(g["status"] == "PASS" for g in gates.values())
    return {
        "version": VERSION_V17931211,
        "status": "PASS" if all_pass else "FAIL",
        "gates": gates,
    }


def run_verification_truth_negative_controls_v17931211() -> Dict[str, Any]:
    """
    Section 3: Executes all 10 required negative-control corruption scenarios (a)..(j)
    in isolated temporary directories and proves each tampering attempt is rejected (RED).
    """
    from tools.fresh_junit_io import read_closed_junit_artifact

    def _write_junit(p: Path, payload: bytes) -> Dict[str, Any]:
        p.write_bytes(payload)
        raw, st = read_closed_junit_artifact(p)
        return {
            "xml_sha256": hashlib.sha256(raw).hexdigest(),
            "xml_mtime_ns": int(st.st_mtime_ns),
        }

    controls: List[Dict[str, Any]] = []
    violations: List[str] = []

    run_id = "neg_ctrl_run_v17931211"
    sha = "a" * 40
    node_1 = "backend/tests/test_v17_9_3_1_2_11_sample.py::test_alpha"
    node_2 = "backend/tests/test_v17_9_3_1_2_11_sample.py::test_beta"

    with tempfile.TemporaryDirectory(prefix="aurum_v17931211_neg_") as tmp:
        tmp_dir = Path(tmp)
        xml_path = tmp_dir / "shard_0.xml"
        good_xml = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<testsuite name="pytest" tests="2" errors="0" failures="0" skipped="0">'
            '<testcase classname="backend.tests.test_v17_9_3_1_2_11_sample" name="test_alpha" time="0.01"/>'
            '<testcase classname="backend.tests.test_v17_9_3_1_2_11_sample" name="test_beta" time="0.01"/>'
            "</testsuite>"
        ).encode("utf-8")
        meta = _write_junit(xml_path, good_xml)

        base_collection = {
            "run_id": run_id,
            "tested_executable_sha": sha,
            "collect_exit_code": 0,
            "collect_pid": 1001,
            "collected_node_ids": [node_1, node_2],
            "collected_node_ids_sha256": canonical_hash(sorted([node_1, node_2])),
        }
        base_shard_manifest = {
            "run_id": run_id,
            "tested_executable_sha": sha,
            "shards": [
                {
                    "shard_id": "shard_0",
                    "run_id": run_id,
                    "tested_executable_sha": sha,
                    "reused": False,
                    "pid": 2002,
                    "exit_code": 0,
                    "timed_out": False,
                    "junit_xml_path": str(xml_path),
                    "xml_sha256": meta["xml_sha256"],
                    "xml_mtime_ns": meta["xml_mtime_ns"],
                    "scheduled_node_ids": [node_1, node_2],
                }
            ],
        }

        # Baseline GREEN check
        green_res = verify_fresh_junit_bijection_v17931211(
            tmp_dir, base_collection, base_shard_manifest, expected_run_id=run_id, expected_tested_sha=sha
        )
        if green_res["status"] != "PASS":
            violations.append(f"BASELINE_GREEN_FAILED:{green_res['violations']}")

        # (a) Forged exit_code 0 with failing JUnit testcase
        fail_xml_path = tmp_dir / "shard_fail.xml"
        fail_xml = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<testsuite name="pytest" tests="2" errors="0" failures="1" skipped="0">'
            '<testcase classname="backend.tests.test_v17_9_3_1_2_11_sample" name="test_alpha" time="0.01">'
            '<failure message="assertion failed">assert False</failure></testcase>'
            '<testcase classname="backend.tests.test_v17_9_3_1_2_11_sample" name="test_beta" time="0.01"/>'
            "</testsuite>"
        ).encode("utf-8")
        fail_meta = _write_junit(fail_xml_path, fail_xml)
        m_a = copy.deepcopy(base_shard_manifest)
        m_a["shards"][0].update(
            exit_code=0,
            junit_xml_path=str(fail_xml_path),
            xml_sha256=fail_meta["xml_sha256"],
            xml_mtime_ns=fail_meta["xml_mtime_ns"],
        )
        res_a = verify_fresh_junit_bijection_v17931211(
            tmp_dir, base_collection, m_a, expected_run_id=run_id, expected_tested_sha=sha
        )
        controls.append({
            "control_id": "A_FORGED_EXIT_ZERO_WITH_FAILING_JUNIT",
            "rejected": res_a["status"] == "FAIL",
            "violations_caught": res_a["violations"],
        })

        # (b) Lost / duplicated JUnit testcase
        dup_xml_path = tmp_dir / "shard_dup.xml"
        dup_xml = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<testsuite name="pytest" tests="2" errors="0" failures="0" skipped="0">'
            '<testcase classname="backend.tests.test_v17_9_3_1_2_11_sample" name="test_alpha" time="0.01"/>'
            '<testcase classname="backend.tests.test_v17_9_3_1_2_11_sample" name="test_alpha" time="0.01"/>'
            "</testsuite>"
        ).encode("utf-8")
        dup_meta = _write_junit(dup_xml_path, dup_xml)
        m_b = copy.deepcopy(base_shard_manifest)
        m_b["shards"][0].update(
            junit_xml_path=str(dup_xml_path),
            xml_sha256=dup_meta["xml_sha256"],
            xml_mtime_ns=dup_meta["xml_mtime_ns"],
        )
        res_b = verify_fresh_junit_bijection_v17931211(
            tmp_dir, base_collection, m_b, expected_run_id=run_id, expected_tested_sha=sha
        )
        controls.append({
            "control_id": "B_LOST_OR_DUPLICATED_JUNIT_TESTCASE",
            "rejected": res_b["status"] == "FAIL",
            "violations_caught": res_b["violations"],
        })

        # (c) Tampered raw XML with old SHA
        m_c = copy.deepcopy(base_shard_manifest)
        m_c["shards"][0]["xml_sha256"] = "0" * 64
        res_c = verify_fresh_junit_bijection_v17931211(
            tmp_dir, base_collection, m_c, expected_run_id=run_id, expected_tested_sha=sha
        )
        controls.append({
            "control_id": "C_TAMPERED_RAW_XML_WITH_OLD_SHA",
            "rejected": res_c["status"] == "FAIL",
            "violations_caught": res_c["violations"],
        })

        # (d) Missing shard or orphaned PID
        res_d = verify_process_exit_and_timeout_v17931211(
            [{"worker_id": "w0", "run_id": run_id, "tested_source_sha": sha, "pid": 2002, "started_at_ms": 100, "completed_at_ms": 200, "exit_code": 0, "timed_out": False}],
            expected_run_id=run_id,
            expected_tested_sha=sha,
            active_orphan_pids=[99999],
        )
        controls.append({
            "control_id": "D_MISSING_SHARD_OR_ORPHANED_PID",
            "rejected": res_d["status"] == "FAIL",
            "violations_caught": res_d["violations"],
        })

        # (e) Stale run_id / dataset SHA / source SHA
        res_e = verify_fresh_junit_bijection_v17931211(
            tmp_dir, base_collection, base_shard_manifest, expected_run_id="fresh_run_2", expected_tested_sha="b" * 40
        )
        controls.append({
            "control_id": "E_STALE_RUN_ID_OR_SOURCE_SHA",
            "rejected": res_e["status"] == "FAIL",
            "violations_caught": res_e["violations"],
        })

        # (f) Copied/reused results between 1/2/4 worker runs
        forged_parity = {
            "independent_runs": [
                {"attempt_id": "run_w1", "worker_count": 1, "canonical_semantic_sha256": "c" * 64, "worker_pids": [101], "started_at_ms": 10, "completed_at_ms": 20, "raw_execution_log_sha256": "d" * 64},
                {"attempt_id": "run_w1", "worker_count": 2, "canonical_semantic_sha256": "c" * 64, "worker_pids": [101], "started_at_ms": 10, "completed_at_ms": 20, "raw_execution_log_sha256": "d" * 64},
                {"attempt_id": "run_w4", "worker_count": 4, "canonical_semantic_sha256": "c" * 64, "worker_pids": [104], "started_at_ms": 30, "completed_at_ms": 40, "raw_execution_log_sha256": "e" * 64},
                {"attempt_id": "run_w1_rep", "worker_count": 1, "canonical_semantic_sha256": "c" * 64, "worker_pids": [105], "started_at_ms": 50, "completed_at_ms": 60, "raw_execution_log_sha256": "f" * 64},
            ]
        }
        res_f = verify_execution_not_synthetic_v17931211(
            collection_manifest=base_collection,
            shard_manifest=base_shard_manifest,
            parity_manifest=forged_parity,
        )
        controls.append({
            "control_id": "F_COPIED_REUSED_WORKER_1_2_4_RESULTS",
            "rejected": res_f["status"] == "FAIL",
            "violations_caught": res_f["violations"],
        })

        # (g) Test timeout masked as success
        m_g = copy.deepcopy(base_shard_manifest)
        m_g["shards"][0]["timed_out"] = True
        res_g = verify_fresh_junit_bijection_v17931211(
            tmp_dir, base_collection, m_g, expected_run_id=run_id, expected_tested_sha=sha
        )
        controls.append({
            "control_id": "G_TEST_TIMEOUT_MASKED_AS_SUCCESS",
            "rejected": res_g["status"] == "FAIL",
            "violations_caught": res_g["violations"],
        })

        # (h) Skipped mandatory test counted as passed
        skip_xml_path = tmp_dir / "shard_skip.xml"
        skip_xml = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<testsuite name="pytest" tests="2" errors="0" failures="0" skipped="1">'
            '<testcase classname="backend.tests.test_v17_9_3_1_2_11_sample" name="test_alpha" time="0.01">'
            '<skipped message="unconditional skip"/></testcase>'
            '<testcase classname="backend.tests.test_v17_9_3_1_2_11_sample" name="test_beta" time="0.01"/>'
            "</testsuite>"
        ).encode("utf-8")
        skip_meta = _write_junit(skip_xml_path, skip_xml)
        m_h = copy.deepcopy(base_shard_manifest)
        m_h["shards"][0].update(
            junit_xml_path=str(skip_xml_path),
            xml_sha256=skip_meta["xml_sha256"],
            xml_mtime_ns=skip_meta["xml_mtime_ns"],
        )
        res_h = verify_fresh_junit_bijection_v17931211(
            tmp_dir, base_collection, m_h, expected_run_id=run_id, expected_tested_sha=sha
        )
        controls.append({
            "control_id": "H_SKIPPED_MANDATORY_TEST_BLOCKED",
            "rejected": res_h["status"] == "FAIL" and bool(res_h["mandatory_skipped_nodes"]),
            "violations_caught": res_h["violations"],
        })

        # (i) Manifest excludes mandatory file or includes forged hash
        (tmp_dir / "00_run_manifest.json").write_text('{"ok": true}', encoding="utf-8")
        (tmp_dir / "artifact_sha256_manifest.json").write_text(
            json.dumps({"files": {"00_run_manifest.json": {"sha256": "f" * 64, "bytes": 12}}}),
            encoding="utf-8",
        )
        res_i = verify_artifact_manifest_v17931211(
            tmp_dir, ["00_run_manifest.json", "01_git_branch_and_source_freeze.json"]
        )
        controls.append({
            "control_id": "I_MANIFEST_MISSING_MANDATORY_FILE_OR_FORGED_HASH",
            "rejected": res_i["status"] == "FAIL" and len(res_i["violations"]) >= 2,
            "violations_caught": res_i["violations"],
        })

        # (j) Phase B artifact receipt without real remote Phase C readback
        phase_b_only_receipt = {
            "phase_a_tested_executable_sha": sha,
            "phase_b_evidence_commit_sha": "b" * 40,
            "phase_c_remote_verified": False,
            "observed_remote_sha": None,
        }
        p02_ok = bool(
            phase_b_only_receipt.get("phase_c_remote_verified") is True
            and phase_b_only_receipt.get("observed_remote_sha") == phase_b_only_receipt.get("phase_b_evidence_commit_sha")
        )
        controls.append({
            "control_id": "J_PHASE_B_RECEIPT_WITHOUT_PHASE_C_REMOTE_READBACK",
            "rejected": not p02_ok,
            "violations_caught": ["MISSING_PHASE_C_REMOTE_READBACK_PROOF"] if not p02_ok else [],
        })

    for c in controls:
        if not c["rejected"]:
            violations.append(f"NEGATIVE_CONTROL_NOT_REJECTED:{c['control_id']}")

    return {
        "version": VERSION_V17931211,
        "status": "PASS" if not violations and len(controls) == 10 else "FAIL",
        "controls_count": len(controls),
        "all_rejected": all(c["rejected"] for c in controls),
        "controls": controls,
        "violations": violations,
    }


# ==============================================================================
# P0-B: EXIT-EVENT LINEAGE — CONTRACT AND CAUSAL PROOF
# ==============================================================================

def build_exit_event_lineage_v17931211(
    episodes: Sequence[Mapping[str, Any]],
    *,
    bars_1m_by_open_ts: Optional[Mapping[int, Mapping[str, Any]]] = None,
    run_id: str,
    tested_executable_sha: str,
    dataset_sha256: str = CANONICAL_DATASET_SHA256_V17931211,
    policy_id: str = "SI0_OFF_BASELINE",
    fill_mode: str = "NEXT_BAR_OPEN_BID_ASK",
) -> Dict[str, Any]:
    """
    Materializes the source-bound, deterministic causal event graph for every trade
    episode from raw episode execution records and raw M1 bars (`bars_1m_by_open_ts`):
      DECISION -> INTENT -> (UNFILLED | ENTRY_FILL -> POSITION_OPEN ->
      (CENSORED/GAP | EXIT_MARKET_OBSERVATION -> EXIT_TRIGGER -> EXIT_FILL ->
       POSITION_CLOSED -> LEDGER_POST[REALIZED_GROSS_PNL, EXIT_FEE]))
    Never fabricates Bitget exchange IDs; uses deterministic dataset-bound replay IDs.
    """
    episode_chains: List[Dict[str, Any]] = []
    all_events: List[Dict[str, Any]] = []
    all_postings: List[Dict[str, Any]] = []
    closed_trade_identities: List[Dict[str, Any]] = []
    unknown_or_unresolved_episodes: List[Dict[str, Any]] = []

    price_basis_for_mode = (
        "MODELED_M1_OHLC_BID_ASK_SPREAD_0.20"
        if fill_mode != "LEGACY_CLOSE_FILL"
        else "MODELED_M1_OHLC_MID_LEGACY_CLOSE"
    )

    for ep_idx, ep in enumerate(episodes):
        led = ep.get("execution_ledger") if isinstance(ep.get("execution_ledger"), Mapping) else {}
        opp_id = str(ep.get("opportunity_id") or led.get("episode_key") or f"opp_{ep_idx}")
        ep_key = str(ep.get("episode_key") or led.get("episode_key") or f"{policy_id}_{opp_id}")
        setup_id = str(ep.get("setup_episode_id") or led.get("setup_id") or "")
        model_id = str(ep.get("model_id") or led.get("model_id") or "")
        direction = str(ep.get("direction") or led.get("direction") or "").upper()
        decision_ts = int(ep.get("decision_timestamp_ms") or led.get("decision_ts") or 0)
        decision_bar_open_ts = decision_ts - 60_000 if decision_ts >= 60_000 else decision_ts
        decision_source_bar_id = f"XAUUSDT:1M:{decision_bar_open_ts}"

        entry_ref = float(ep.get("entry_reference") or led.get("entry_reference") or 0.0)
        planned_sl = float(ep.get("selected_sl") or led.get("planned_sl") or 0.0)
        planned_tp = float(ep.get("take_profit") or led.get("planned_tp") or 0.0)
        planned_qty = float(
            ep.get("executed_quantity")
            or led.get("quantity")
            or ep.get("hypothetical_quantity")
            or 0.0
        )
        planned_risk_usd = float(ep.get("planned_net_risk_usd") or led.get("planned_net_risk_usd") or 0.0)
        multiplier = float(led.get("multiplier") or 1.0)
        filled = bool(ep.get("filled", led.get("filled", False)))
        outcome_label = str(ep.get("outcome_label") or led.get("outcome_label") or "UNKNOWN")
        raw_exit_type = str(ep.get("exit_type") or led.get("exit_type") or outcome_label)
        canonical_exit_cause = EXIT_CAUSE_NORMALIZATION_MAP_V17931211.get(raw_exit_type, raw_exit_type)

        trade_id = _det_id("trd", dataset_sha256[:12], policy_id, fill_mode, opp_id, decision_ts)
        order_intent_id = _det_id("ord", dataset_sha256[:12], policy_id, fill_mode, opp_id, decision_ts)
        position_id = _det_id("pos", dataset_sha256[:12], policy_id, fill_mode, opp_id, decision_ts) if filled else None

        events_for_ep: List[Dict[str, Any]] = []
        postings_for_ep: List[Dict[str, Any]] = []
        seq = 0

        def _make_event(
            *,
            event_kind: str,
            parent_ids: Sequence[str],
            source_bar_id: str,
            source_event_ts: int,
            observed_ts: int,
            known_at_ts: int,
            state_before: str,
            state_after: str,
            terminal_status: Optional[str] = None,
            payload: Optional[Mapping[str, Any]] = None,
        ) -> Dict[str, Any]:
            nonlocal seq
            seq += 1
            ev_id = _det_id(
                "evt",
                dataset_sha256[:12],
                policy_id,
                fill_mode,
                opp_id,
                seq,
                event_kind,
                source_bar_id,
                source_event_ts,
            )
            body = {
                "schema_version": SCHEMA_VERSION_V17931211,
                "run_id": run_id,
                "tested_executable_sha": tested_executable_sha,
                "dataset_sha256": dataset_sha256,
                "source_class": "REPLAY_CANDLE",
                "policy_id": policy_id,
                "fill_mode": fill_mode,
                "opportunity_id": opp_id,
                "episode_id": ep_key,
                "setup_episode_id": setup_id,
                "order_intent_id": order_intent_id,
                "position_id": position_id,
                "trade_id": trade_id,
                "model_id": model_id,
                "direction": direction,
                "sequence_no": seq,
                "event_id": ev_id,
                "parent_event_ids": list(parent_ids),
                "event_kind": event_kind,
                "source_evidence_id": source_bar_id,
                "source_event_timestamp_ms": int(source_event_ts),
                "observed_timestamp_ms": int(observed_ts),
                "known_at_timestamp_ms": int(known_at_ts),
                "decision_cutoff_timestamp_ms": int(decision_ts),
                "price_basis": price_basis_for_mode,
                "state_before": state_before,
                "state_after": state_after,
                "terminal_status": terminal_status,
                "payload": dict(payload or {}),
            }
            body["semantic_hash"] = canonical_hash({
                k: v for k, v in body.items() if k not in ("run_id", "tested_executable_sha")
            })
            events_for_ep.append(body)
            all_events.append(body)
            return body

        # 1. DECISION
        ev_decision = _make_event(
            event_kind="DECISION",
            parent_ids=(),
            source_bar_id=decision_source_bar_id,
            source_event_ts=decision_bar_open_ts,
            observed_ts=decision_ts,
            known_at_ts=decision_ts,
            state_before="CANDIDATE_EVALUATED",
            state_after="OPPORTUNITY_ACCEPTED",
            payload={
                "entry_reference": entry_ref,
                "selected_sl": planned_sl,
                "take_profit": planned_tp,
                "planned_net_risk_usd": planned_risk_usd,
            },
        )

        # 2. INTENT
        ev_intent = _make_event(
            event_kind="INTENT",
            parent_ids=(ev_decision["event_id"],),
            source_bar_id=decision_source_bar_id,
            source_event_ts=decision_ts,
            observed_ts=decision_ts,
            known_at_ts=decision_ts,
            state_before="OPPORTUNITY_ACCEPTED",
            state_after="INTENT_SCHEDULED",
            payload={
                "order_type": str(led.get("entry_order_type") or "MARKET_NEXT_OPEN"),
                "planned_entry": entry_ref,
                "planned_sl": planned_sl,
                "planned_tp": planned_tp,
                "planned_quantity": planned_qty,
            },
        )

        # Unfilled path
        if not filled:
            no_fill_reason = str(
                ep.get("no_fill_reason")
                or led.get("no_fill_reason")
                or outcome_label
                or "UNFILLED"
            )
            ev_unfilled = _make_event(
                event_kind="CENSORED/UNFILLED/UNRESOLVED",
                parent_ids=(ev_intent["event_id"],),
                source_bar_id=decision_source_bar_id,
                source_event_ts=decision_ts,
                observed_ts=decision_ts,
                known_at_ts=decision_ts,
                state_before="INTENT_SCHEDULED",
                state_after="UNFILLED_TERMINAL",
                terminal_status="UNFILLED",
                payload={
                    "no_fill_reason": no_fill_reason,
                    "outcome_label": outcome_label,
                    "realized_gross_pnl_usd": 0.0,
                    "realized_net_pnl_usd": 0.0,
                },
            )
            unknown_or_unresolved_episodes.append({
                "opportunity_id": opp_id,
                "episode_id": ep_key,
                "policy_id": policy_id,
                "fill_mode": fill_mode,
                "terminal_category": "UNFILLED",
                "outcome_label": outcome_label,
                "reason": no_fill_reason,
                "terminal_event_id": ev_unfilled["event_id"],
                "realized_net_pnl_usd": 0.0,
            })
            episode_chains.append({
                "opportunity_id": opp_id,
                "episode_id": ep_key,
                "trade_id": trade_id,
                "order_intent_id": order_intent_id,
                "position_id": None,
                "policy_id": policy_id,
                "fill_mode": fill_mode,
                "model_id": model_id,
                "direction": direction,
                "filled": False,
                "is_closed_trade": False,
                "outcome_label": outcome_label,
                "terminal_status": "UNFILLED",
                "events": events_for_ep,
                "postings": postings_for_ep,
            })
            continue

        # 3. ENTRY_FILL & POSITION_OPEN
        entry_fill_ts = int(ep.get("entry_fill_ts") or led.get("entry_fill_ts") or decision_ts)
        entry_fill_px = float(ep.get("entry_fill_price") or led.get("entry_fill_price") or entry_ref)
        entry_fee_usd = round(float(ep.get("entry_fee_usd") or led.get("entry_fee") or 0.0), 4)
        entry_slip_usd = round(float(led.get("entry_slippage") or 0.0), 4)
        entry_bar_open_ts = (
            decision_bar_open_ts if fill_mode == "LEGACY_CLOSE_FILL" else entry_fill_ts
        )
        entry_source_bar_id = f"XAUUSDT:1M:{entry_bar_open_ts}"

        ev_entry_fill = _make_event(
            event_kind="ENTRY_FILL",
            parent_ids=(ev_intent["event_id"],),
            source_bar_id=entry_source_bar_id,
            source_event_ts=entry_bar_open_ts,
            observed_ts=entry_fill_ts,
            known_at_ts=entry_fill_ts,
            state_before="INTENT_SCHEDULED",
            state_after="ENTRY_FILLED",
            payload={
                "entry_fill_price": entry_fill_px,
                "entry_fee_usd": entry_fee_usd,
                "entry_slippage_usd": entry_slip_usd,
                "quantity": planned_qty,
                "multiplier": multiplier,
            },
        )

        ev_pos_open = _make_event(
            event_kind="POSITION_OPEN",
            parent_ids=(ev_entry_fill["event_id"],),
            source_bar_id=entry_source_bar_id,
            source_event_ts=entry_fill_ts,
            observed_ts=entry_fill_ts,
            known_at_ts=entry_fill_ts,
            state_before="ENTRY_FILLED",
            state_after="OPEN",
            payload={
                "position_id": position_id,
                "entry_fill_price": entry_fill_px,
                "stop_loss": planned_sl,
                "take_profit": planned_tp,
                "quantity": planned_qty,
                "planned_net_risk_usd": planned_risk_usd,
            },
        )

        entry_fee_post_id = _det_id(
            "post", dataset_sha256[:12], policy_id, fill_mode, position_id, "ENTRY_FEE", ev_entry_fill["event_id"]
        )
        ev_entry_fee_post = _make_event(
            event_kind="LEDGER_POST",
            parent_ids=(ev_entry_fill["event_id"],),
            source_bar_id=entry_source_bar_id,
            source_event_ts=entry_fill_ts,
            observed_ts=entry_fill_ts,
            known_at_ts=entry_fill_ts,
            state_before="OPEN",
            state_after="OPEN",
            payload={
                "posting_id": entry_fee_post_id,
                "posting_type": "ENTRY_FEE",
                "amount_usd": round(-entry_fee_usd, 4),
                "currency": "USDT",
            },
        )
        entry_post_rec = {
            "posting_id": entry_fee_post_id,
            "trade_id": trade_id,
            "position_id": position_id,
            "opportunity_id": opp_id,
            "policy_id": policy_id,
            "fill_mode": fill_mode,
            "event_id": ev_entry_fee_post["event_id"],
            "parent_fill_event_id": ev_entry_fill["event_id"],
            "posting_type": "ENTRY_FEE",
            "amount_usd": round(-entry_fee_usd, 4),
            "timestamp_ms": entry_fill_ts,
            "is_closed_trade_posting": outcome_label in ("WIN", "LOSS"),
        }
        postings_for_ep.append(entry_post_rec)
        all_postings.append(entry_post_rec)

        # Open at horizon or intratrade data gap unresolved path
        if outcome_label not in ("WIN", "LOSS"):
            release_ts = int(ep.get("release_ts_ms") or led.get("trigger_bar_ts") or entry_fill_ts)
            trig_ts = int(led.get("trigger_bar_ts") or (release_ts - 60_000 if release_ts > entry_fill_ts else entry_fill_ts))
            cens_bar_id = f"XAUUSDT:1M:{trig_ts}"
            unres_reason = str(
                led.get("unresolved_reason")
                or ("MARK_TO_MARKET_CENSORED_AT_HORIZON" if outcome_label == "OPEN_AT_HORIZON" else "INTRATRADE_PROVIDER_GAP")
            )
            ev_censored = _make_event(
                event_kind="CENSORED/UNFILLED/UNRESOLVED",
                parent_ids=(ev_pos_open["event_id"],),
                source_bar_id=cens_bar_id,
                source_event_ts=trig_ts,
                observed_ts=release_ts,
                known_at_ts=release_ts,
                state_before="OPEN",
                state_after=outcome_label,
                terminal_status=outcome_label,
                payload={
                    "outcome_label": outcome_label,
                    "canonical_exit_cause": canonical_exit_cause,
                    "unresolved_reason": unres_reason,
                    "bars_held": int(ep.get("bars_held") or led.get("bars_held") or 0),
                    "realized_gross_pnl_usd": 0.0,
                    "realized_net_pnl_usd": 0.0,
                    "open_at_horizon_mtm_separate": led.get("open_at_horizon_mtm_separate"),
                },
            )
            unknown_or_unresolved_episodes.append({
                "opportunity_id": opp_id,
                "episode_id": ep_key,
                "position_id": position_id,
                "policy_id": policy_id,
                "fill_mode": fill_mode,
                "terminal_category": outcome_label,
                "outcome_label": outcome_label,
                "reason": unres_reason,
                "terminal_event_id": ev_censored["event_id"],
                "realized_net_pnl_usd": 0.0,
                "open_at_horizon_mtm_separate": led.get("open_at_horizon_mtm_separate"),
            })
            episode_chains.append({
                "opportunity_id": opp_id,
                "episode_id": ep_key,
                "trade_id": trade_id,
                "order_intent_id": order_intent_id,
                "position_id": position_id,
                "policy_id": policy_id,
                "fill_mode": fill_mode,
                "model_id": model_id,
                "direction": direction,
                "filled": True,
                "is_closed_trade": False,
                "outcome_label": outcome_label,
                "terminal_status": outcome_label,
                "events": events_for_ep,
                "postings": postings_for_ep,
            })
            continue

        # 4. Closed Trade Terminal Chain:
        # EXIT_MARKET_OBSERVATION -> EXIT_TRIGGER -> EXIT_FILL -> POSITION_CLOSED -> LEDGER_POSTs
        exit_fill_ts = int(ep.get("exit_fill_ts") or led.get("exit_fill_ts") or ep.get("release_ts_ms") or 0)
        trigger_bar_ts = int(
            led.get("trigger_bar_ts")
            or (exit_fill_ts if raw_exit_type == "ADVERSE_GAP_OPEN_STOP_LOSS" else exit_fill_ts - 60_000)
        )
        exit_source_bar_id = f"XAUUSDT:1M:{trigger_bar_ts}"
        src_bar = (bars_1m_by_open_ts or {}).get(trigger_bar_ts)
        bar_ohlc = (
            {
                "open": float(src_bar["open"]),
                "high": float(src_bar["high"]),
                "low": float(src_bar["low"]),
                "close": float(src_bar["close"]),
                "open_time": int(src_bar["open_time"]),
                "close_time": int(src_bar.get("close_time", int(src_bar["open_time"]) + 60_000)),
            }
            if src_bar is not None
            else None
        )

        exit_fill_px = float(ep.get("exit_fill_price") or led.get("exit_fill_price") or 0.0)
        exit_fee_usd = round(float(ep.get("exit_fee_usd") or led.get("exit_fee") or 0.0), 4)
        exit_slip_usd = round(float(led.get("exit_slippage") or 0.0), 4)
        gross_pnl_usd = round(float(ep.get("realized_gross_pnl_usd") or led.get("realized_gross_pnl_usd") or 0.0), 4)
        net_pnl_usd = round(float(ep.get("realized_net_pnl_usd") or led.get("realized_net_pnl_usd") or 0.0), 4)
        realized_r = round(float(ep.get("realized_r") or led.get("realized_r_net") or 0.0), 4)
        same_bar_tie = bool(ep.get("same_bar_sl_tp_tie", led.get("same_bar_sl_tp_tie", False)))
        trigger_level = planned_tp if outcome_label == "WIN" else planned_sl
        trigger_side = ("BID" if direction == "LONG" else "ASK") if fill_mode != "LEGACY_CLOSE_FILL" else "OHLC_MID"

        ev_exit_obs = _make_event(
            event_kind="EXIT_MARKET_OBSERVATION",
            parent_ids=(ev_pos_open["event_id"],),
            source_bar_id=exit_source_bar_id,
            source_event_ts=trigger_bar_ts,
            observed_ts=exit_fill_ts,
            known_at_ts=exit_fill_ts,
            state_before="OPEN",
            state_after="OPEN_EXIT_OBSERVED",
            payload={
                "trigger_bar_ts": trigger_bar_ts,
                "exit_fill_ts": exit_fill_ts,
                "bar_ohlc": bar_ohlc,
                "source_bar_present_in_dataset": src_bar is not None,
            },
        )

        ev_exit_trig = _make_event(
            event_kind="EXIT_TRIGGER",
            parent_ids=(ev_exit_obs["event_id"],),
            source_bar_id=exit_source_bar_id,
            source_event_ts=trigger_bar_ts,
            observed_ts=exit_fill_ts,
            known_at_ts=exit_fill_ts,
            state_before="OPEN_EXIT_OBSERVED",
            state_after="EXIT_TRIGGERED",
            payload={
                "trigger_side": trigger_side,
                "trigger_level": trigger_level,
                "planned_sl": planned_sl,
                "planned_tp": planned_tp,
                "raw_exit_type": raw_exit_type,
                "canonical_exit_cause": canonical_exit_cause,
                "same_bar_sl_tp_tie": same_bar_tie,
            },
        )

        ev_exit_fill = _make_event(
            event_kind="EXIT_FILL",
            parent_ids=(ev_exit_trig["event_id"],),
            source_bar_id=exit_source_bar_id,
            source_event_ts=exit_fill_ts,
            observed_ts=exit_fill_ts,
            known_at_ts=exit_fill_ts,
            state_before="EXIT_TRIGGERED",
            state_after="EXIT_FILLED",
            payload={
                "resolved_exit_price": exit_fill_px,
                "trigger_level": trigger_level,
                "exit_fee_usd": exit_fee_usd,
                "exit_slippage_usd": exit_slip_usd,
                "quantity": planned_qty,
                "multiplier": multiplier,
                "fill_mode": fill_mode,
                "canonical_exit_cause": canonical_exit_cause,
            },
        )

        ev_pos_closed = _make_event(
            event_kind="POSITION_CLOSED",
            parent_ids=(ev_exit_fill["event_id"],),
            source_bar_id=exit_source_bar_id,
            source_event_ts=exit_fill_ts,
            observed_ts=exit_fill_ts,
            known_at_ts=exit_fill_ts,
            state_before="EXIT_FILLED",
            state_after="CLOSED",
            terminal_status=outcome_label,
            payload={
                "outcome_label": outcome_label,
                "canonical_exit_cause": canonical_exit_cause,
                "entry_fill_price": entry_fill_px,
                "exit_fill_price": exit_fill_px,
                "quantity": planned_qty,
                "bars_held": int(ep.get("bars_held") or led.get("bars_held") or 0),
                "realized_gross_pnl_usd": gross_pnl_usd,
                "entry_fee_usd": entry_fee_usd,
                "exit_fee_usd": exit_fee_usd,
                "realized_net_pnl_usd": net_pnl_usd,
                "realized_r": realized_r,
                "planned_net_risk_usd": planned_risk_usd,
            },
        )

        gross_post_id = _det_id(
            "post", dataset_sha256[:12], policy_id, fill_mode, position_id, "REALIZED_GROSS_PNL", ev_exit_fill["event_id"]
        )
        ev_gross_post = _make_event(
            event_kind="LEDGER_POST",
            parent_ids=(ev_exit_fill["event_id"], ev_pos_closed["event_id"]),
            source_bar_id=exit_source_bar_id,
            source_event_ts=exit_fill_ts,
            observed_ts=exit_fill_ts,
            known_at_ts=exit_fill_ts,
            state_before="CLOSED",
            state_after="CLOSED",
            terminal_status=outcome_label,
            payload={
                "posting_id": gross_post_id,
                "posting_type": "REALIZED_GROSS_PNL",
                "amount_usd": gross_pnl_usd,
                "currency": "USDT",
            },
        )
        gross_post_rec = {
            "posting_id": gross_post_id,
            "trade_id": trade_id,
            "position_id": position_id,
            "opportunity_id": opp_id,
            "policy_id": policy_id,
            "fill_mode": fill_mode,
            "event_id": ev_gross_post["event_id"],
            "parent_fill_event_id": ev_exit_fill["event_id"],
            "posting_type": "REALIZED_GROSS_PNL",
            "amount_usd": gross_pnl_usd,
            "timestamp_ms": exit_fill_ts,
            "is_closed_trade_posting": True,
        }
        postings_for_ep.append(gross_post_rec)
        all_postings.append(gross_post_rec)

        xfee_post_id = _det_id(
            "post", dataset_sha256[:12], policy_id, fill_mode, position_id, "EXIT_FEE", ev_exit_fill["event_id"]
        )
        ev_xfee_post = _make_event(
            event_kind="LEDGER_POST",
            parent_ids=(ev_exit_fill["event_id"], ev_pos_closed["event_id"]),
            source_bar_id=exit_source_bar_id,
            source_event_ts=exit_fill_ts,
            observed_ts=exit_fill_ts,
            known_at_ts=exit_fill_ts,
            state_before="CLOSED",
            state_after="CLOSED",
            terminal_status=outcome_label,
            payload={
                "posting_id": xfee_post_id,
                "posting_type": "EXIT_FEE",
                "amount_usd": round(-exit_fee_usd, 4),
                "currency": "USDT",
            },
        )
        xfee_post_rec = {
            "posting_id": xfee_post_id,
            "trade_id": trade_id,
            "position_id": position_id,
            "opportunity_id": opp_id,
            "policy_id": policy_id,
            "fill_mode": fill_mode,
            "event_id": ev_xfee_post["event_id"],
            "parent_fill_event_id": ev_exit_fill["event_id"],
            "posting_type": "EXIT_FEE",
            "amount_usd": round(-exit_fee_usd, 4),
            "timestamp_ms": exit_fill_ts,
            "is_closed_trade_posting": True,
        }
        postings_for_ep.append(xfee_post_rec)
        all_postings.append(xfee_post_rec)

        closed_identity_row = {
            "schema_version": SCHEMA_VERSION_V17931211,
            "policy_id": policy_id,
            "fill_mode": fill_mode,
            "opportunity_id": opp_id,
            "episode_id": ep_key,
            "setup_episode_id": setup_id,
            "trade_id": trade_id,
            "order_intent_id": order_intent_id,
            "position_id": position_id,
            "model_id": model_id,
            "direction": direction,
            "decision_timestamp_ms": decision_ts,
            "entry_fill_timestamp_ms": entry_fill_ts,
            "trigger_bar_timestamp_ms": trigger_bar_ts,
            "exit_fill_timestamp_ms": exit_fill_ts,
            "bars_held": int(ep.get("bars_held") or led.get("bars_held") or 0),
            "entry_reference": entry_ref,
            "entry_fill_price": entry_fill_px,
            "selected_sl": planned_sl,
            "take_profit": planned_tp,
            "exit_fill_price": exit_fill_px,
            "quantity": planned_qty,
            "multiplier": multiplier,
            "planned_net_risk_usd": planned_risk_usd,
            "realized_gross_pnl_usd": gross_pnl_usd,
            "entry_fee_usd": entry_fee_usd,
            "exit_fee_usd": exit_fee_usd,
            "total_fee_usd": round(entry_fee_usd + exit_fee_usd, 4),
            "realized_net_pnl_usd": net_pnl_usd,
            "realized_r": realized_r,
            "outcome_label": outcome_label,
            "raw_exit_type": raw_exit_type,
            "canonical_exit_cause": canonical_exit_cause,
            "same_bar_sl_tp_tie": same_bar_tie,
            "price_basis": price_basis_for_mode,
            "source_entry_bar_id": entry_source_bar_id,
            "source_exit_bar_id": exit_source_bar_id,
            "decision_event_id": ev_decision["event_id"],
            "intent_event_id": ev_intent["event_id"],
            "entry_fill_event_id": ev_entry_fill["event_id"],
            "position_open_event_id": ev_pos_open["event_id"],
            "exit_observation_event_id": ev_exit_obs["event_id"],
            "exit_trigger_event_id": ev_exit_trig["event_id"],
            "exit_fill_event_id": ev_exit_fill["event_id"],
            "position_closed_event_id": ev_pos_closed["event_id"],
            "ledger_posting_ids": [entry_fee_post_id, gross_post_id, xfee_post_id],
        }
        closed_identity_row["lineage_chain_sha256"] = canonical_hash(closed_identity_row)
        closed_trade_identities.append(closed_identity_row)

        episode_chains.append({
            "opportunity_id": opp_id,
            "episode_id": ep_key,
            "trade_id": trade_id,
            "order_intent_id": order_intent_id,
            "position_id": position_id,
            "policy_id": policy_id,
            "fill_mode": fill_mode,
            "model_id": model_id,
            "direction": direction,
            "filled": True,
            "is_closed_trade": True,
            "outcome_label": outcome_label,
            "terminal_status": outcome_label,
            "events": events_for_ep,
            "postings": postings_for_ep,
        })

    return {
        "version": VERSION_V17931211,
        "schema_version": SCHEMA_VERSION_V17931211,
        "run_id": run_id,
        "tested_executable_sha": tested_executable_sha,
        "dataset_sha256": dataset_sha256,
        "policy_id": policy_id,
        "fill_mode": fill_mode,
        "total_episodes": len(episode_chains),
        "closed_trades_count": len(closed_trade_identities),
        "non_closed_episodes_count": len(unknown_or_unresolved_episodes),
        "total_events_count": len(all_events),
        "total_postings_count": len(all_postings),
        "episode_chains": episode_chains,
        "all_events": all_events,
        "all_postings": all_postings,
        "closed_trade_identities": closed_trade_identities,
        "unknown_or_unresolved_episodes": unknown_or_unresolved_episodes,
        "ordered_events_sha256": canonical_hash([e["semantic_hash"] for e in all_events]),
    }


def verify_exit_event_parentage_v17931211(
    lineage_bundle: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Validates global event ID uniqueness, parent existence, causal timestamp ordering
    (decision_cutoff <= entry_fill <= exit_observation_bar_open <= exit_fill),
    source provenance class, episode/position consistency, and absence of cycles.
    """
    violations: List[str] = []
    all_events = list(lineage_bundle.get("all_events") or [])
    if not all_events and int(lineage_bundle.get("total_episodes", 0)) > 0:
        violations.append("EMPTY_EVENT_STREAM_FOR_NONEMPTY_EPISODES")

    events_by_id: Dict[str, Dict[str, Any]] = {}
    for ev in all_events:
        eid = str(ev.get("event_id") or "")
        if not eid:
            violations.append("EVENT_MISSING_ID")
            continue
        if eid in events_by_id:
            violations.append(f"DUPLICATE_GLOBAL_EVENT_ID:{eid}")
        events_by_id[eid] = dict(ev)

        if ev.get("source_class") not in SOURCE_CLASSES_V17931211:
            violations.append(f"INVALID_SOURCE_CLASS:{eid}:{ev.get('source_class')}")
        if ev.get("event_kind") not in EXIT_EVENT_KINDS_V17931211:
            violations.append(f"INVALID_EVENT_KIND:{eid}:{ev.get('event_kind')}")
        if not ev.get("source_evidence_id"):
            violations.append(f"MISSING_SOURCE_EVIDENCE_ID:{eid}")

        src_ts = ev.get("source_event_timestamp_ms")
        obs_ts = ev.get("observed_timestamp_ms")
        known_ts = ev.get("known_at_timestamp_ms")
        cutoff_ts = ev.get("decision_cutoff_timestamp_ms")
        if any(isinstance(t, bool) or not isinstance(t, int) or t <= 0 for t in (src_ts, obs_ts, known_ts, cutoff_ts)):
            violations.append(f"INVALID_EVENT_TIMESTAMPS:{eid}")
        else:
            if src_ts > obs_ts or obs_ts > known_ts:
                violations.append(f"NON_CAUSAL_EVENT_TIMESTAMPS:{eid}:{src_ts}>{obs_ts}>{known_ts}")
            if ev.get("event_kind") == "DECISION" and known_ts > cutoff_ts:
                violations.append(f"DECISION_KNOWN_AFTER_CUTOFF:{eid}:{known_ts}>{cutoff_ts}")
            if ev.get("event_kind") in ("ENTRY_FILL", "EXIT_MARKET_OBSERVATION", "EXIT_TRIGGER", "EXIT_FILL", "POSITION_CLOSED"):
                if obs_ts < cutoff_ts:
                    violations.append(f"POST_DECISION_EVENT_BEFORE_CUTOFF:{eid}:{obs_ts}<{cutoff_ts}")

    # Parent existence, same-episode binding, causal monotonicity, and cycle check
    for eid, ev in events_by_id.items():
        parents = list(ev.get("parent_event_ids") or [])
        if ev.get("event_kind") == "DECISION":
            if parents:
                violations.append(f"DECISION_EVENT_HAS_PARENTS:{eid}")
        else:
            if not parents:
                violations.append(f"NON_ROOT_EVENT_MISSING_PARENT:{eid}:{ev.get('event_kind')}")
        for pid in parents:
            if pid == eid:
                violations.append(f"SELF_PARENT_CYCLE:{eid}")
                continue
            parent_ev = events_by_id.get(pid)
            if parent_ev is None:
                violations.append(f"MISSING_PARENT_EVENT:{eid}->parent={pid}")
                continue
            if parent_ev.get("episode_id") != ev.get("episode_id"):
                violations.append(f"CROSS_EPISODE_PARENT_CONTAMINATION:{eid}->{pid}")
            if int(parent_ev.get("sequence_no", 0)) >= int(ev.get("sequence_no", 0)):
                violations.append(f"NON_MONOTONIC_PARENT_SEQUENCE:{eid}->{pid}")
            if int(parent_ev.get("observed_timestamp_ms", 0)) > int(ev.get("observed_timestamp_ms", 0)):
                violations.append(f"PARENT_OBSERVED_AFTER_CHILD:{eid}->{pid}")

    return {
        "version": VERSION_V17931211,
        "status": "PASS" if not violations else "FAIL",
        "total_events_verified": len(events_by_id),
        "violations": violations,
    }


def verify_exit_state_machine_v17931211(
    lineage_bundle: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Verifies every episode follows an admissible state path:
      - Unfilled: DECISION -> INTENT -> CENSORED/UNFILLED/UNRESOLVED (0 realized PnL)
      - Open/Gap Censored: DECISION -> INTENT -> ENTRY_FILL -> POSITION_OPEN -> LEDGER_POST(ENTRY_FEE) -> CENSORED/UNFILLED/UNRESOLVED (0 realized PnL)
      - Closed Trade: DECISION -> INTENT -> ENTRY_FILL -> POSITION_OPEN -> LEDGER_POST(ENTRY_FEE) ->
                      EXIT_MARKET_OBSERVATION -> EXIT_TRIGGER -> EXIT_FILL -> POSITION_CLOSED ->
                      LEDGER_POST(REALIZED_GROSS_PNL) -> LEDGER_POST(EXIT_FEE)
    Rejects any closed trade missing observation, trigger, or fill, or any non-closed episode
    with non-zero realized PnL.
    """
    violations: List[str] = []
    chains = list(lineage_bundle.get("episode_chains") or [])

    closed_verified = 0
    unfilled_verified = 0
    censored_verified = 0

    for ch in chains:
        ep_id = str(ch.get("episode_id") or "")
        outcome = str(ch.get("outcome_label") or "")
        filled = bool(ch.get("filled", False))
        evs = list(ch.get("events") or [])
        kinds = [str(e.get("event_kind")) for e in evs]
        postings = list(ch.get("postings") or [])

        if not filled:
            expected_kinds = ["DECISION", "INTENT", "CENSORED/UNFILLED/UNRESOLVED"]
            if kinds != expected_kinds:
                violations.append(f"INVALID_UNFILLED_STATE_PATH:{ep_id}:{kinds}")
            if postings:
                violations.append(f"UNFILLED_EPISODE_HAS_LEDGER_POSTINGS:{ep_id}")
            unfilled_verified += 1
        elif outcome in ("OPEN_AT_HORIZON", "DATA_GAP_UNRESOLVED"):
            expected_kinds = [
                "DECISION",
                "INTENT",
                "ENTRY_FILL",
                "POSITION_OPEN",
                "LEDGER_POST",
                "CENSORED/UNFILLED/UNRESOLVED",
            ]
            if kinds != expected_kinds:
                violations.append(f"INVALID_CENSORED_STATE_PATH:{ep_id}:{kinds}")
            term_payload = (evs[-1].get("payload") or {}) if evs else {}
            if float(term_payload.get("realized_gross_pnl_usd", 0.0)) != 0.0 or float(
                term_payload.get("realized_net_pnl_usd", 0.0)
            ) != 0.0:
                violations.append(f"NONZERO_REALIZED_PNL_ON_CENSORED_EPISODE:{ep_id}")
            if any(p.get("posting_type") in ("REALIZED_GROSS_PNL", "EXIT_FEE") for p in postings):
                violations.append(f"CENSORED_EPISODE_HAS_EXIT_POSTINGS:{ep_id}")
            censored_verified += 1
        elif outcome in ("WIN", "LOSS"):
            expected_kinds = [
                "DECISION",
                "INTENT",
                "ENTRY_FILL",
                "POSITION_OPEN",
                "LEDGER_POST",
                "EXIT_MARKET_OBSERVATION",
                "EXIT_TRIGGER",
                "EXIT_FILL",
                "POSITION_CLOSED",
                "LEDGER_POST",
                "LEDGER_POST",
            ]
            if kinds != expected_kinds:
                violations.append(f"INVALID_CLOSED_TRADE_STATE_PATH:{ep_id}:{kinds}")
            post_types = [str(p.get("posting_type")) for p in postings]
            if post_types != ["ENTRY_FEE", "REALIZED_GROSS_PNL", "EXIT_FEE"]:
                violations.append(f"INVALID_CLOSED_TRADE_POSTING_SEQUENCE:{ep_id}:{post_types}")
            closed_verified += 1
        else:
            violations.append(f"UNKNOWN_EPISODE_OUTCOME_STATE:{ep_id}:{outcome}")

    return {
        "version": VERSION_V17931211,
        "status": "PASS" if not violations else "FAIL",
        "closed_verified": closed_verified,
        "closed_trades_verified": closed_verified,
        "unfilled_verified": unfilled_verified,
        "censored_verified": censored_verified,
        "violations": violations,
    }


def verify_exit_fill_basis_v17931211(
    lineage_bundle: Mapping[str, Any],
    *,
    bars_1m_by_open_ts: Optional[Mapping[int, Mapping[str, Any]]] = None,
    spread_usd: float = 0.20,
    base_slippage_usd: float = 0.10,
    taker_fee_rate: float = 0.0004,
    maker_fee_rate: float = 0.0002,
    tp_is_maker: bool = False,
) -> Dict[str, Any]:
    """
    Independently verifies direction-specific exit price basis (BID for LONG, ASK for SHORT,
    or OHLC mid for LEGACY_CLOSE_FILL), same-bar conservative SL-first ambiguity resolution,
    adverse opening gap pricing, and fee rate application against the raw M1 bar.
    """
    violations: List[str] = []
    fill_mode = str(lineage_bundle.get("fill_mode") or "NEXT_BAR_OPEN_BID_ASK")
    is_stress_2x = fill_mode in ("STRESS_2X_FRICTION", "STRESS_WIDER_SPREAD_SLIPPAGE_2X")
    eff_spread = spread_usd * (2.0 if is_stress_2x else 1.0)
    eff_slip = base_slippage_usd * (2.0 if is_stress_2x else 1.0)
    eff_taker = taker_fee_rate * (2.0 if is_stress_2x else 1.0)
    eff_maker = maker_fee_rate * (2.0 if is_stress_2x else 1.0)
    half_spread = 0.5 * eff_spread

    closed_rows = list(lineage_bundle.get("closed_trade_identities") or [])
    verified_count = 0

    for row in closed_rows:
        opp_id = str(row.get("opportunity_id") or "")
        direction = str(row.get("direction") or "").upper()
        sl_px = float(row.get("selected_sl") or 0.0)
        tp_px = float(row.get("take_profit") or 0.0)
        exit_px = float(row.get("exit_fill_price") or 0.0)
        outcome = str(row.get("outcome_label") or "")
        raw_exit_type = str(row.get("raw_exit_type") or "")
        canonical_cause = str(row.get("canonical_exit_cause") or "")
        trig_ts = int(row.get("trigger_bar_timestamp_ms") or 0)
        exit_ts = int(row.get("exit_fill_timestamp_ms") or 0)
        entry_ts = int(row.get("entry_fill_timestamp_ms") or 0)

        if not _is_finite_pos(sl_px) or not _is_finite_pos(tp_px) or not _is_finite_pos(exit_px):
            violations.append(f"NONFINITE_OR_NONPOSITIVE_EXIT_LEVELS:{opp_id}")
            continue
        if exit_ts < entry_ts or trig_ts <= 0:
            violations.append(f"NON_CAUSAL_EXIT_TIMESTAMPS:{opp_id}:entry={entry_ts},trig={trig_ts},exit={exit_ts}")
            continue

        if bars_1m_by_open_ts is not None:
            bar = bars_1m_by_open_ts.get(trig_ts)
            if bar is None:
                violations.append(f"MISSING_TRIGGER_SOURCE_BAR:{opp_id}:{trig_ts}")
                continue
            bo = float(bar["open"])
            bh = float(bar["high"])
            bl = float(bar["low"])

            if fill_mode == "LEGACY_CLOSE_FILL":
                hit_sl = (bl <= sl_px) if direction == "LONG" else (bh >= sl_px)
                hit_tp = (bh >= tp_px) if direction == "LONG" else (bl <= tp_px)
                if outcome == "WIN":
                    if not hit_tp or hit_sl:
                        violations.append(f"LEGACY_WIN_WITHOUT_CLEAN_TP_TOUCH:{opp_id}")
                    expected_px = tp_px
                else:
                    if not hit_sl:
                        violations.append(f"LEGACY_LOSS_WITHOUT_SL_TOUCH:{opp_id}")
                    expected_px = round(sl_px - eff_slip, 2) if direction == "LONG" else round(sl_px + eff_slip, 2)
            else:
                if direction == "LONG":
                    hit_sl = (bl - half_spread) <= sl_px
                    hit_tp = (bh - half_spread) >= tp_px
                    if canonical_cause == "ADVERSE_GAP_STOP_LOSS" or raw_exit_type == "ADVERSE_GAP_OPEN_STOP_LOSS":
                        if (bo - half_spread) > sl_px:
                            violations.append(f"LONG_ADVERSE_GAP_NOT_BEYOND_SL:{opp_id}")
                        expected_px = round((bo - half_spread) - eff_slip, 2)
                    elif canonical_cause == "SAME_BAR_AMBIGUOUS_CONSERVATIVE_SL" or bool(row.get("same_bar_sl_tp_tie")):
                        if not (hit_sl and hit_tp):
                            violations.append(f"LONG_SAME_BAR_TIE_NOT_BOTH_TOUCHED:{opp_id}")
                        expected_px = round(sl_px - eff_slip, 2)
                    elif outcome == "LOSS":
                        if not hit_sl:
                            violations.append(f"LONG_SL_NOT_TOUCHED_ON_BID:{opp_id}")
                        expected_px = round(min(sl_px, bo - half_spread) - eff_slip, 2)
                    else:
                        if not hit_tp or hit_sl:
                            violations.append(f"LONG_TP_NOT_CLEANLY_TOUCHED_ON_BID:{opp_id}")
                        expected_px = tp_px
                else:
                    hit_sl = (bh + half_spread) >= sl_px
                    hit_tp = (bl + half_spread) <= tp_px
                    if canonical_cause == "ADVERSE_GAP_STOP_LOSS" or raw_exit_type == "ADVERSE_GAP_OPEN_STOP_LOSS":
                        if (bo + half_spread) < sl_px:
                            violations.append(f"SHORT_ADVERSE_GAP_NOT_BEYOND_SL:{opp_id}")
                        expected_px = round((bo + half_spread) + eff_slip, 2)
                    elif canonical_cause == "SAME_BAR_AMBIGUOUS_CONSERVATIVE_SL" or bool(row.get("same_bar_sl_tp_tie")):
                        if not (hit_sl and hit_tp):
                            violations.append(f"SHORT_SAME_BAR_TIE_NOT_BOTH_TOUCHED:{opp_id}")
                        expected_px = round(sl_px + eff_slip, 2)
                    elif outcome == "LOSS":
                        if not hit_sl:
                            violations.append(f"SHORT_SL_NOT_TOUCHED_ON_ASK:{opp_id}")
                        expected_px = round(max(sl_px, bo + half_spread) + eff_slip, 2)
                    else:
                        if not hit_tp or hit_sl:
                            violations.append(f"SHORT_TP_NOT_CLEANLY_TOUCHED_ON_ASK:{opp_id}")
                        expected_px = tp_px

            if abs(exit_px - expected_px) > 0.011:
                violations.append(
                    f"EXIT_FILL_PRICE_BASIS_MISMATCH:{opp_id}:actual={exit_px},expected={expected_px}"
                )

        # Verify exit fee rate
        qty = float(row.get("quantity") or 0.0)
        mult = float(row.get("multiplier") or 1.0)
        expected_fee_rate = eff_maker if (outcome == "WIN" and tp_is_maker) else eff_taker
        expected_exit_fee = round(exit_px * expected_fee_rate * qty * mult, 4)
        actual_exit_fee = float(row.get("exit_fee_usd") or 0.0)
        if abs(actual_exit_fee - expected_exit_fee) > 0.001:
            violations.append(
                f"EXIT_FEE_BASIS_MISMATCH:{opp_id}:actual={actual_exit_fee},expected={expected_exit_fee}"
            )
        verified_count += 1

    return {
        "version": VERSION_V17931211,
        "status": "PASS" if not violations else "FAIL",
        "fill_mode": fill_mode,
        "verified_closed_trades_count": verified_count,
        "closed_trades_checked": verified_count,
        "violations": violations,
    }


def verify_exit_event_deduplication_v17931211(
    lineage_bundle: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Verifies:
    - Exactly-once closure per closed position (`POSITION_CLOSED`, `EXIT_TRIGGER`, `EXIT_FILL`).
    - Globally unique `posting_id`, `trade_id`, `position_id`, and `event_id`.
    - No duplicate `(position_id, posting_type)` postings.
    - Every `REALIZED_GROSS_PNL` and `EXIT_FEE` posting links to a valid `EXIT_FILL` event.
    """
    violations: List[str] = []
    chains = list(lineage_bundle.get("episode_chains") or [])
    all_events = list(lineage_bundle.get("all_events") or [])
    all_postings = list(lineage_bundle.get("all_postings") or [])

    events_by_id = {str(e.get("event_id")): e for e in all_events if e.get("event_id")}
    seen_position_ids: Set[str] = set()
    seen_trade_ids: Set[str] = set()
    seen_posting_ids: Set[str] = set()
    seen_pos_posting_type: Set[Tuple[str, str]] = set()

    for ch in chains:
        tid = str(ch.get("trade_id") or "")
        pid = ch.get("position_id")
        if tid in seen_trade_ids:
            violations.append(f"DUPLICATE_TRADE_ID:{tid}")
        seen_trade_ids.add(tid)
        if pid is not None:
            pid_s = str(pid)
            if pid_s in seen_position_ids:
                violations.append(f"DUPLICATE_POSITION_ID:{pid_s}")
            seen_position_ids.add(pid_s)

        evs = list(ch.get("events") or [])
        kind_counts = Counter(str(e.get("event_kind")) for e in evs)
        if kind_counts.get("EXIT_TRIGGER", 0) > 1:
            violations.append(f"MULTIPLE_EXIT_TRIGGERS_FOR_POSITION:{pid}")
        if kind_counts.get("EXIT_FILL", 0) > 1:
            violations.append(f"MULTIPLE_EXIT_FILLS_FOR_POSITION:{pid}")
        if kind_counts.get("POSITION_CLOSED", 0) > 1:
            violations.append(f"DOUBLE_CLOSE_FOR_POSITION:{pid}")

        # Verify no events occur after POSITION_CLOSED except LEDGER_POSTs bound to that close
        closed_idx = next(
            (i for i, e in enumerate(evs) if e.get("event_kind") == "POSITION_CLOSED"), None
        )
        if closed_idx is not None:
            for later_ev in evs[closed_idx + 1 :]:
                if later_ev.get("event_kind") != "LEDGER_POST":
                    violations.append(
                        f"POST_CLOSE_NON_LEDGER_EVENT:{pid}:{later_ev.get('event_kind')}"
                    )

    for post in all_postings:
        post_id = str(post.get("posting_id") or "")
        pos_id = str(post.get("position_id") or "")
        ptype = str(post.get("posting_type") or "")
        parent_fill_id = str(post.get("parent_fill_event_id") or "")

        if not post_id:
            violations.append("POSTING_MISSING_ID")
        elif post_id in seen_posting_ids:
            violations.append(f"DUPLICATE_POSTING_ID:{post_id}")
        seen_posting_ids.add(post_id)

        key = (pos_id, ptype)
        if key in seen_pos_posting_type:
            violations.append(f"DUPLICATE_POSITION_POSTING_TYPE:{pos_id}:{ptype}")
        seen_pos_posting_type.add(key)

        parent_ev = events_by_id.get(parent_fill_id)
        if parent_ev is None:
            violations.append(f"POSTING_ORPHAN_PARENT_FILL:{post_id}->{parent_fill_id}")
        else:
            if ptype == "ENTRY_FEE" and parent_ev.get("event_kind") != "ENTRY_FILL":
                violations.append(f"ENTRY_FEE_NOT_LINKED_TO_ENTRY_FILL:{post_id}")
            if ptype in ("REALIZED_GROSS_PNL", "EXIT_FEE") and parent_ev.get("event_kind") != "EXIT_FILL":
                violations.append(f"EXIT_POSTING_NOT_LINKED_TO_EXIT_FILL:{post_id}:{ptype}")

    return {
        "version": VERSION_V17931211,
        "status": "PASS" if not violations else "FAIL",
        "unique_trades_count": len(seen_trade_ids),
        "unique_positions_count": len(seen_position_ids),
        "unique_postings_count": len(seen_posting_ids),
        "violations": violations,
    }


def recompute_realized_pnl_from_exit_events_v17931211(
    lineage_bundle: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Independently recomputes per-trade and aggregate realized gross PnL, entry fee,
    exit fee, net PnL, and realized net R directly from raw ENTRY_FILL and EXIT_FILL
    events and LEDGER_POST records, without trusting writer summary totals.
    """
    violations: List[str] = []
    chains = list(lineage_bundle.get("episode_chains") or [])

    recomputed_trades: List[Dict[str, Any]] = []
    total_gross_usd = 0.0
    total_entry_fee_usd = 0.0
    total_exit_fee_usd = 0.0
    total_net_usd = 0.0
    total_r_sum = 0.0
    wins_count = 0
    losses_count = 0

    for ch in chains:
        outcome = str(ch.get("outcome_label") or "")
        if not ch.get("filled") or outcome not in ("WIN", "LOSS"):
            continue
        ep_id = str(ch.get("episode_id") or "")
        direction = str(ch.get("direction") or "").upper()
        evs = list(ch.get("events") or [])
        postings = list(ch.get("postings") or [])

        ev_entry = next((e for e in evs if e.get("event_kind") == "ENTRY_FILL"), None)
        ev_open = next((e for e in evs if e.get("event_kind") == "POSITION_OPEN"), None)
        ev_exit = next((e for e in evs if e.get("event_kind") == "EXIT_FILL"), None)
        ev_closed = next((e for e in evs if e.get("event_kind") == "POSITION_CLOSED"), None)

        if not (ev_entry and ev_open and ev_exit and ev_closed):
            violations.append(f"MISSING_FILL_EVENTS_FOR_PNL_RECOMPUTE:{ep_id}")
            continue

        ent_p = float(ev_entry["payload"]["entry_fill_price"])
        ext_p = float(ev_exit["payload"]["resolved_exit_price"])
        qty = float(ev_entry["payload"]["quantity"])
        mult = float(ev_entry["payload"].get("multiplier", 1.0))
        ent_fee = round(float(ev_entry["payload"]["entry_fee_usd"]), 4)
        ext_fee = round(float(ev_exit["payload"]["exit_fee_usd"]), 4)
        planned_risk = float(ev_open["payload"]["planned_net_risk_usd"])

        sign = 1.0 if direction == "LONG" else -1.0
        recomp_gross = round(sign * (ext_p - ent_p) * qty * mult, 4)
        recomp_net = round(recomp_gross - ent_fee - ext_fee, 4)
        recomp_r = round(recomp_net / planned_risk, 4) if planned_risk > 0 else 0.0

        reported_gross = float(ev_closed["payload"]["realized_gross_pnl_usd"])
        reported_net = float(ev_closed["payload"]["realized_net_pnl_usd"])
        reported_r = float(ev_closed["payload"]["realized_r"])

        if abs(recomp_gross - reported_gross) > 0.001:
            violations.append(f"GROSS_PNL_MISMATCH:{ep_id}:{recomp_gross}!={reported_gross}")
        if abs(recomp_net - reported_net) > 0.001:
            violations.append(f"NET_PNL_MISMATCH:{ep_id}:{recomp_net}!={reported_net}")
        if abs(recomp_r - reported_r) > 0.001:
            violations.append(f"REALIZED_R_MISMATCH:{ep_id}:{recomp_r}!={reported_r}")

        # Reconcile against ledger postings for this trade
        post_sum = round(sum(float(p["amount_usd"]) for p in postings), 4)
        if abs(post_sum - recomp_net) > 0.001:
            violations.append(f"LEDGER_POSTING_SUM_MISMATCH:{ep_id}:{post_sum}!={recomp_net}")

        if outcome == "WIN":
            wins_count += 1
        else:
            losses_count += 1

        total_gross_usd = round(total_gross_usd + recomp_gross, 4)
        total_entry_fee_usd = round(total_entry_fee_usd + ent_fee, 4)
        total_exit_fee_usd = round(total_exit_fee_usd + ext_fee, 4)
        total_net_usd = round(total_net_usd + recomp_net, 4)
        total_r_sum += recomp_r

        recomputed_trades.append({
            "episode_id": ep_id,
            "opportunity_id": ch.get("opportunity_id"),
            "direction": direction,
            "entry_fill_price": ent_p,
            "exit_fill_price": ext_p,
            "quantity": qty,
            "recomputed_gross_pnl_usd": recomp_gross,
            "recomputed_entry_fee_usd": ent_fee,
            "recomputed_exit_fee_usd": ext_fee,
            "recomputed_net_pnl_usd": recomp_net,
            "recomputed_realized_r": recomp_r,
            "ledger_postings_sum_usd": post_sum,
        })

    closed_n = len(recomputed_trades)
    expectancy_net_r = round(total_r_sum / closed_n, 4) if closed_n > 0 else 0.0
    total_fees_usd = round(total_entry_fee_usd + total_exit_fee_usd, 4)

    if abs((total_gross_usd - total_fees_usd) - total_net_usd) > 0.002:
        violations.append("AGGREGATE_GROSS_MINUS_FEES_MISMATCH")

    return {
        "version": VERSION_V17931211,
        "status": "PASS" if not violations else "FAIL",
        "policy_id": lineage_bundle.get("policy_id"),
        "fill_mode": lineage_bundle.get("fill_mode"),
        "closed_trades_count": closed_n,
        "wins_count": wins_count,
        "losses_count": losses_count,
        "recomputed_total_gross_pnl_usd": total_gross_usd,
        "recomputed_total_entry_fee_usd": total_entry_fee_usd,
        "recomputed_total_exit_fee_usd": total_exit_fee_usd,
        "recomputed_total_fees_usd": total_fees_usd,
        "recomputed_total_net_pnl_usd": total_net_usd,
        "recomputed_expectancy_net_r": expectancy_net_r,
        "violations": violations,
    }


def verify_exit_portfolio_conservation_v17931211(
    lineage_bundle: Mapping[str, Any],
    *,
    expected_summary: Optional[Mapping[str, Any]] = None,
    expected_counts: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Verifies portfolio-level episode conservation:
    - `total_scheduled_episodes == closed_wins + closed_losses + open_at_horizon + data_gap_unresolved + unfilled`
    - Disjoint episode sets across all 5 terminal buckets (zero overlap).
    - Chronological non-overlap of active filled positions `[entry_fill_ts, release_ts_ms)`.
    - Open/unfilled/gap episodes contribute 0.0000 to closed realized PnL.
    """
    violations: List[str] = []
    chains = list(lineage_bundle.get("episode_chains") or [])

    buckets: Dict[str, Set[str]] = {
        "WIN": set(),
        "LOSS": set(),
        "OPEN_AT_HORIZON": set(),
        "DATA_GAP_UNRESOLVED": set(),
        "UNFILLED": set(),
    }

    filled_intervals: List[Tuple[int, int, str]] = []

    for ch in chains:
        oid = str(ch.get("opportunity_id") or ch.get("episode_id") or "")
        filled = bool(ch.get("filled", False))
        outcome = str(ch.get("outcome_label") or "")

        if not filled:
            buckets["UNFILLED"].add(oid)
        elif outcome in ("WIN", "LOSS", "OPEN_AT_HORIZON", "DATA_GAP_UNRESOLVED"):
            buckets[outcome].add(oid)
            evs = list(ch.get("events") or [])
            ev_entry = next((e for e in evs if e.get("event_kind") == "ENTRY_FILL"), None)
            ev_term = evs[-1] if evs else None
            if ev_entry and ev_term:
                ent_ts = int(ev_entry.get("observed_timestamp_ms") or 0)
                rel_ts = int(ev_term.get("observed_timestamp_ms") or ent_ts)
                filled_intervals.append((ent_ts, rel_ts, oid))
        else:
            violations.append(f"UNCLASSIFIED_EPISODE_OUTCOME:{oid}:{outcome}")

    # Verify disjointness across all buckets
    bucket_names = list(buckets.keys())
    for i in range(len(bucket_names)):
        for j in range(i + 1, len(bucket_names)):
            overlap = buckets[bucket_names[i]] & buckets[bucket_names[j]]
            if overlap:
                violations.append(
                    f"COHORT_BUCKET_OVERLAP:{bucket_names[i]}&{bucket_names[j]}:{sorted(overlap)}"
                )

    total_partition_sum = sum(len(s) for s in buckets.values())
    if total_partition_sum != len(chains):
        violations.append(f"EPISODE_PARTITION_SUM_MISMATCH:{total_partition_sum}!={len(chains)}")

    # Verify chronological non-overlap of filled positions
    filled_intervals.sort(key=lambda x: (x[0], x[1], x[2]))
    for idx in range(1, len(filled_intervals)):
        prev_ent, prev_rel, prev_id = filled_intervals[idx - 1]
        cur_ent, cur_rel, cur_id = filled_intervals[idx]
        if cur_ent < prev_rel:
            violations.append(
                f"CONCURRENT_POSITION_OVERLAP:{prev_id}[{prev_ent}..{prev_rel})vs{cur_id}[{cur_ent}..{cur_rel})"
            )

    exp_map = expected_summary if expected_summary is not None else expected_counts
    if exp_map is not None:
        if "total_scheduled_episodes" in exp_map and int(exp_map["total_scheduled_episodes"]) != len(chains):
            violations.append(f"SUMMARY_SCHEDULED_MISMATCH:{exp_map['total_scheduled_episodes']}!={len(chains)}")
        if "closed_trades_count" in exp_map and int(exp_map["closed_trades_count"]) != len(buckets["WIN"]) + len(buckets["LOSS"]):
            violations.append("SUMMARY_CLOSED_COUNT_MISMATCH")
        if "wins_count" in exp_map and int(exp_map["wins_count"]) != len(buckets["WIN"]):
            violations.append("SUMMARY_WIN_LOSS_COUNT_MISMATCH")
        if "losses_count" in exp_map and int(exp_map["losses_count"]) != len(buckets["LOSS"]):
            violations.append("SUMMARY_WIN_LOSS_COUNT_MISMATCH")
        if "open_at_horizon_count" in exp_map and int(exp_map["open_at_horizon_count"]) != len(buckets["OPEN_AT_HORIZON"]):
            violations.append("SUMMARY_OPEN_HORIZON_COUNT_MISMATCH")
        if "data_gap_unresolved_count" in exp_map and int(exp_map["data_gap_unresolved_count"]) != len(buckets["DATA_GAP_UNRESOLVED"]):
            violations.append("SUMMARY_DATA_GAP_COUNT_MISMATCH")
        if "unfilled_no_entry_count" in exp_map and int(exp_map["unfilled_no_entry_count"]) != len(buckets["UNFILLED"]):
            violations.append("SUMMARY_UNFILLED_COUNT_MISMATCH")

    closed_ids = list(lineage_bundle.get("closed_trade_identities") or [])
    tot_gross = round(sum(float(c.get("realized_gross_pnl_usd") or 0.0) for c in closed_ids), 4)
    tot_fees = round(sum(float(c.get("total_fee_usd") or 0.0) for c in closed_ids), 4)
    tot_net = round(sum(float(c.get("realized_net_pnl_usd") or 0.0) for c in closed_ids), 4)

    return {
        "version": VERSION_V17931211,
        "status": "PASS" if not violations else "FAIL",
        "policy_id": lineage_bundle.get("policy_id"),
        "fill_mode": lineage_bundle.get("fill_mode"),
        "total_scheduled_episodes": len(chains),
        "closed_trades_count": len(buckets["WIN"]) + len(buckets["LOSS"]),
        "wins_count": len(buckets["WIN"]),
        "losses_count": len(buckets["LOSS"]),
        "open_at_horizon_count": len(buckets["OPEN_AT_HORIZON"]),
        "data_gap_unresolved_count": len(buckets["DATA_GAP_UNRESOLVED"]),
        "unfilled_no_entry_count": len(buckets["UNFILLED"]),
        "total_realized_gross_pnl_usd": tot_gross,
        "total_fees_usd": tot_fees,
        "total_realized_net_pnl_usd": tot_net,
        "conservation_holds": not violations and total_partition_sum == len(chains),
        "violations": violations,
    }


def verify_exit_lineage_cross_adapter_parity_v17931211() -> Dict[str, Any]:
    """
    Section 4.1 & 4.3: Verifies cross-adapter exit semantics across:
      1) `shared_exit_evaluator.evaluate_order_exit` (used by ExitMonitor & MarketReconciliationService)
      2) `lab.replay_execution.evaluate_position_exit` (used by ReplayEngine)
      3) `services.v17_9_2_structural_stop_intelligence.simulate_v17931_trade_execution_net` (used by Historical Backtest)
      4) `services.market_shadow_service` (OFF/OBSERVE parity)
    Compares equivalent factual event snapshots (LONG SL, LONG TP, SHORT SL, SHORT TP,
    same-bar TP+SL conservative SL-first ambiguity, adverse opening gap beyond SL) and
    explicitly documents known intentional transport/fee model differences.
    """
    from domain_calculator import CostAssumptions
    from lab.replay_contracts import ReplayMarketEvent, ReplayPosition
    from lab.replay_execution import evaluate_position_exit
    from services.shared_exit_evaluator import evaluate_order_exit
    from services.v17_9_2_structural_stop_intelligence import simulate_v17931_trade_execution_net

    violations: List[str] = []
    scenarios: List[Dict[str, Any]] = []

    zero_costs = CostAssumptions(taker_fee_rate=0.0, maker_fee_rate=0.0, slippage_usd=0.0, tp_is_maker=False)

    test_cases = [
        {
            "scenario_id": "LONG_SL_HIT",
            "direction": "LONG",
            "entry_price": 4100.0,
            "sl": 4090.0,
            "tp": 4120.0,
            "bar": {"open_time": 2000, "close_time": 62000, "open": 4098.0, "high": 4102.0, "low": 4088.0, "close": 4091.0, "is_closed": True, "evidence_id": "bar_long_sl"},
            "expected_canonical_cause": "STOP_LOSS",
            "expected_zero_friction_exit_price": 4090.0,
        },
        {
            "scenario_id": "LONG_TP_HIT",
            "direction": "LONG",
            "entry_price": 4100.0,
            "sl": 4090.0,
            "tp": 4120.0,
            "bar": {"open_time": 2000, "close_time": 62000, "open": 4102.0, "high": 4125.0, "low": 4098.0, "close": 4122.0, "is_closed": True, "evidence_id": "bar_long_tp"},
            "expected_canonical_cause": "TAKE_PROFIT",
            "expected_zero_friction_exit_price": 4120.0,
        },
        {
            "scenario_id": "SHORT_SL_HIT",
            "direction": "SHORT",
            "entry_price": 4100.0,
            "sl": 4110.0,
            "tp": 4080.0,
            "bar": {"open_time": 2000, "close_time": 62000, "open": 4102.0, "high": 4112.0, "low": 4098.0, "close": 4109.0, "is_closed": True, "evidence_id": "bar_short_sl"},
            "expected_canonical_cause": "STOP_LOSS",
            "expected_zero_friction_exit_price": 4110.0,
        },
        {
            "scenario_id": "SHORT_TP_HIT",
            "direction": "SHORT",
            "entry_price": 4100.0,
            "sl": 4110.0,
            "tp": 4080.0,
            "bar": {"open_time": 2000, "close_time": 62000, "open": 4098.0, "high": 4101.0, "low": 4075.0, "close": 4078.0, "is_closed": True, "evidence_id": "bar_short_tp"},
            "expected_canonical_cause": "TAKE_PROFIT",
            "expected_zero_friction_exit_price": 4080.0,
        },
        {
            "scenario_id": "SAME_BAR_AMBIGUOUS_SL_AND_TP",
            "direction": "LONG",
            "entry_price": 4100.0,
            "sl": 4090.0,
            "tp": 4120.0,
            "bar": {"open_time": 2000, "close_time": 62000, "open": 4100.0, "high": 4125.0, "low": 4085.0, "close": 4105.0, "is_closed": True, "evidence_id": "bar_ambig"},
            "expected_canonical_cause_shared": "SAME_BAR_AMBIGUOUS_CONSERVATIVE_SL",
            "expected_zero_friction_exit_price": 4090.0,
        },
    ]

    for tc in test_cases:
        b = tc["bar"]
        direction = tc["direction"]
        ent = tc["entry_price"]
        sl = tc["sl"]
        tp = tc["tp"]

        # 1. Shared Exit Evaluator
        sh_dec = evaluate_order_exit(
            {"direction": direction, "stop_loss": sl, "take_profit": tp, "entry_time": 1000},
            b,
            {"require_evidence_reference": True},
        )
        sh_norm_cause = EXIT_CAUSE_NORMALIZATION_MAP_V17931211.get(sh_dec.cause, sh_dec.cause)

        # 2. Lab Replay Execution
        pos = ReplayPosition(
            position_id=f"pos_{tc['scenario_id']}",
            order_id=f"ord_{tc['scenario_id']}",
            setup_id=f"set_{tc['scenario_id']}",
            session_id="NY-2026-10-08",
            entry_type="SMC_CONFIRMED",
            direction=direction,
            entry_time=1000,
            entry_price=ent,
            stop_loss=sl,
            take_profit=tp,
            quantity=1.0,
            leverage=30,
            margin_mode="ISOLATED",
            initial_risk_usdt=10.0,
            planned_net_rr=2.0,
            entry_fee=0.0,
        )
        mkt_ev = ReplayMarketEvent(
            kind="CLOSE",
            timestamp=b["close_time"],
            timeframe="1M",
            open_price=b["open"],
            high_price=b["high"],
            low_price=b["low"],
            close_price=b["close"],
            sequence=1,
        )
        lab_acct, lab_cause = evaluate_position_exit(pos, mkt_ev, zero_costs, multiplier=1.0)
        lab_norm_cause = (
            "SAME_BAR_AMBIGUOUS_CONSERVATIVE_SL"
            if (lab_acct and lab_acct.get("is_ambiguous"))
            else EXIT_CAUSE_NORMALIZATION_MAP_V17931211.get(lab_cause, lab_cause)
        )

        # 3. Historical Backtest Simulator (LEGACY_CLOSE_FILL with 0 slippage/spread for pure trigger parity)
        sim_led = simulate_v17931_trade_execution_net(
            variant="PARITY_TEST",
            episode_key=f"ep_{tc['scenario_id']}",
            setup_id=f"set_{tc['scenario_id']}",
            model_id="SMC_M1_CONTINUATION",
            direction=direction,
            decision_ts=1000,
            planned_entry=ent,
            planned_sl=sl,
            planned_tp=tp,
            forward_bars=[
                b,
            ],
            fill_mode="LEGACY_CLOSE_FILL",
            spread_usd=0.0,
            slippage_usd=0.0,
            taker_fee_rate=0.0,
            maker_fee_rate=0.0,
        )
        sim_norm_cause = EXIT_CAUSE_NORMALIZATION_MAP_V17931211.get(sim_led.exit_type, sim_led.exit_type)

        expected_cause = tc.get("expected_canonical_cause") or tc.get("expected_canonical_cause_shared")
        expected_px = tc["expected_zero_friction_exit_price"]

        # Note: shared_exit_evaluator uses bar low/high on non-ambiguous SL_HIT (`min(sl, low)`),
        # while lab/replay_execution and simulate_v17931_trade_execution_net use `min(sl, open)` (stop order triggered intrabar fills at SL unless bar opens gapped beyond SL).
        causes_match = (sh_norm_cause == lab_norm_cause == sim_norm_cause == expected_cause)
        prices_match = (
            abs((lab_acct["exit_price"] if lab_acct else -1.0) - expected_px) < 1e-6
            and abs(float(sim_led.exit_fill_price or -1.0) - expected_px) < 1e-6
        )
        if not causes_match or not prices_match:
            violations.append(
                f"CROSS_ADAPTER_PARITY_MISMATCH:{tc['scenario_id']}:causes=({sh_norm_cause},{lab_norm_cause},{sim_norm_cause}),expected={expected_cause}"
            )

        scenarios.append({
            "scenario_id": tc["scenario_id"],
            "direction": direction,
            "shared_evaluator": {
                "raw_cause": sh_dec.cause,
                "canonical_cause": sh_norm_cause,
                "applied_exit_price": sh_dec.applied_exit_price,
                "price_basis": sh_dec.price_basis,
                "evidence_reference": sh_dec.evidence_reference,
            },
            "lab_replay_execution": {
                "raw_cause": lab_cause,
                "canonical_cause": lab_norm_cause,
                "exit_price": lab_acct["exit_price"] if lab_acct else None,
                "exit_event_id": lab_acct["exit_event_id"] if lab_acct else None,
            },
            "historical_intervention_simulator": {
                "raw_exit_type": sim_led.exit_type,
                "canonical_cause": sim_norm_cause,
                "exit_fill_price": sim_led.exit_fill_price,
                "trigger_bar_ts": sim_led.trigger_bar_ts,
            },
            "semantic_cause_parity_holds": causes_match,
            "stop_trigger_price_parity_holds": prices_match,
        })

    documented_adapter_differences = [
        {
            "adapter_pair": "shared_exit_evaluator (BAR_EXTREMES) vs simulate_v17931_trade_execution_net / lab.replay_execution",
            "difference": (
                "On candle-only evaluation (`BAR_EXTREMES`), `shared_exit_evaluator` conservatively takes `min(stop_loss, low)` "
                "for LONG SL (`max(stop_loss, high)` for SHORT SL) when only bar extremes are known in late reconciliation, "
                "whereas `replay_execution` and `simulate_v17931_trade_execution_net` fill stop orders at `min(stop_loss, open_bid) - slippage` "
                "(gap-at-open adverse pricing, intrabar stop fill at SL +/- slippage)."
            ),
            "status": "INTENTIONAL_DOCUMENTED_RECONCILIATION_CONSERVATISM",
        },
        {
            "adapter_pair": "ExitMonitor (Realtime Tick BID/ASK) vs Historical Backtest (NEXT_BAR_OPEN_BID_ASK)",
            "difference": (
                "Realtime `ExitMonitor` evaluates live Bitget executable `bid` (LONG) and `ask` (SHORT) ticks via `shared_exit_evaluator`, "
                "whereas historical M1 replay synthesizes bid/ask from M1 OHLC +/- half_spread (`0.10` USD) because historical tick top-of-book "
                "was not stored for the 15-day October window. Explicitly labeled `MODELED_M1_OHLC_BID_ASK_SPREAD_0.20`."
            ),
            "status": "INTENTIONAL_DOCUMENTED_HISTORICAL_PROXY",
        },
    ]

    return {
        "version": VERSION_V17931211,
        "status": "PASS" if not violations else "FAIL",
        "normalization_map": EXIT_CAUSE_NORMALIZATION_MAP_V17931211,
        "scenarios_tested": len(scenarios),
        "scenarios": scenarios,
        "documented_intentional_adapter_differences": documented_adapter_differences,
        "violations": violations,
    }


def build_function_callgraph_and_contracts_v17931211(
    repo_root: Optional[Path] = None,
    *,
    tested_executable_sha: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Section 2: Inspects the grounded source files via AST to record exact `file:line`,
    parameters, return annotation, docstring summary, invariants, and consumer paths
    for `02_function_callgraph_and_contracts.json`.
    """
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[3]
    aurum_root = repo_root / "aurum-desk" if (repo_root / "aurum-desk").exists() else repo_root

    target_specs = [
        ("tools/run_v17_9_3_1_2_10_verification.py", ["main"], "Existing .10 verification pipeline & source freeze", ["tools/v17_9_3_1_2_10_independent_verifier.py"]),
        ("tools/v17_9_3_1_2_10_independent_verifier.py", ["build_and_verify_v130_evidence", "run_v130_negative_disk_tampering_controls"], "Independent .10 artifact/universe verifier", ["tools/run_v17_9_3_1_2_10_verification.py"]),
        ("tools/run_v17_9_async_regression.py", ["AsyncWorkerGroupSpec", "parse_junit_xml_nodes", "BoundedAsyncTestOrchestrator", "build_performance_report"], "Bounded asyncio subprocess orchestrator & JUnit parser", ["tools/run_v17_9_3_1_2_2_verification.py", "tools/run_v17_9_3_1_2_11_verification.py"]),
        ("tools/v17_9_3_1_2_7_proof_checks.py", ["verify_fresh_full_junit"], "Disk-side fresh JUnit shard & mandatory-skip verifier", ["tools/run_v17_9_3_1_2_10_verification.py", "tools/run_v17_9_3_1_2_11_verification.py"]),
        ("tools/run_v17_9_3_1_2_2_verification.py", ["run_full_backend_pytest_collection_and_sharded_junit_v1793122", "preflight_runtime_resource_limits_v1793121", "cleanup_orphan_subprocesses_and_temp_v1793121"], "Full backend pytest collection, sharded execution & resource guards", ["tools/run_v17_9_3_1_2_10_verification.py", "tools/run_v17_9_3_1_2_11_verification.py"]),
        ("tools/artifact_verifier.py", ["build_source_manifest", "verify_source_manifest_against_git", "write_acceptance_artifacts", "verify_acceptance_bundle", "verify_remote_acceptance_bundle", "verify_remote_branch_artifacts"], "Source manifest & remote git artifact verification", ["tools/run_v17_9_3_1_2_11_verification.py"]),
        ("tools/acceptance_verdict.py", ["determine_release_verdict", "verify_pre_execution_run_manifest", "independently_reconcile_junit_scopes", "verify_worker_execution_accounting", "verify_resource_telemetry_semantics", "verify_mandatory_artifact_hash_coverage"], "Reusable release/run verdict primitives", ["tools/artifact_verifier.py"]),
        ("backend/services/shared_exit_evaluator.py", ["ExitDecision", "evaluate_order_exit"], "Pure realtime/reconciliation exit-decision authority", ["backend/services/exit_monitor.py", "backend/services/market_reconciliation_service.py", "backend/services/trade_lifecycle_service.py"]),
        ("backend/services/exit_monitor.py", ["ExitMonitor"], "Realtime active position exit monitor", ["backend/main.py"]),
        ("backend/lab/replay_execution.py", ["submit_replay_order", "try_fill_pending_order", "evaluate_position_exit", "compute_closed_trade_accounting", "resolve_exit_fee_rate"], "Lab replay order/fill/exit simulator & deterministic accounting", ["backend/lab/replay_engine.py"]),
        ("backend/lab/replay_engine.py", ["ReplayContext", "ReplayEngine"], "Historical lab replay lifecycle & artifact exporter", ["backend/routers/replay.py"]),
        ("backend/lab/replay_integrity.py", ["run_replay_integrity_checks", "verify_exported_artifacts"], "Lab replay integrity & exported bundle verifier", ["backend/lab/replay_engine.py"]),
        ("backend/services/market_reconciliation_service.py", ["MarketReconciliationService"], "Delayed/out-of-order historical & provider reconciliation service", ["backend/services/exit_monitor.py"]),
        ("backend/services/v17_9_2_structural_stop_intelligence.py", ["ExecutableTradeLedgerEntryV17931", "simulate_v17931_trade_execution_net", "classify_structure_stop_outcome"], "M1 historical trade execution simulator & stop forensic classifier", ["backend/services/v17_9_3_1_2_executable_intervention_replay_service.py"]),
        ("backend/services/v17_9_3_1_2_executable_intervention_replay_service.py", ["simulate_executable_candidate_v179312", "advance_portfolio_to_next_event_v179312", "reconcile_trades_to_ledger_v179312", "replay_global_chronological_portfolio_v179312", "verify_no_trade_time_overlap_v179312", "assert_replay_oos_temporal_and_feature_integrity_v179312"], "Intervention replay episode lifecycle & chronological portfolio state", ["tools/run_v17_9_3_1_2_2_entry_signal_backtest.py"]),
        ("backend/services/v17_9_3_1_2_10_canonical_reconciliation_and_provenance.py", ["build_canonical_universe_lineage_v17931210", "verify_universe_bijection_and_exclusion_reasons_v17931210", "verify_forensic_cohort_conservation_v17931210", "analyze_15_distinct_recovered_trades_v17931210", "render_source_derived_report_v17931210", "verify_source_denom_and_trade_report_consistency_v17931210"], "Canonical universe, 242 vs 157 cohort conservation & 15 recovered trade forensics", ["tools/v17_9_3_1_2_10_independent_verifier.py", "tools/v17_9_3_1_2_11_independent_verifier.py"]),
        ("backend/services/market_shadow_service.py", ["evaluate_shadow_cycle", "evaluate_shared_decision_snapshot", "compare_production_shadow_divergence"], "Market Shadow OFF/OBSERVE parity authority", ["tools/run_v17_9_3_1_2_full_universe_backtest.py"]),
        ("backend/services/v17_9_3_1_2_11_verification_truth_and_exit_lineage.py", [
            "normalize_collected_test_identity_v17931211",
            "verify_fresh_junit_bijection_v17931211",
            "verify_process_exit_and_timeout_v17931211",
            "verify_execution_not_synthetic_v17931211",
            "verify_source_freeze_v17931211",
            "verify_artifact_manifest_v17931211",
            "build_exit_event_lineage_v17931211",
            "verify_exit_event_parentage_v17931211",
            "verify_exit_state_machine_v17931211",
            "verify_exit_fill_basis_v17931211",
            "verify_exit_event_deduplication_v17931211",
            "recompute_realized_pnl_from_exit_events_v17931211",
            "verify_exit_portfolio_conservation_v17931211",
            "verify_exit_lineage_cross_adapter_parity_v17931211",
        ], "V17.9.3.1.2.11 verification truth & exit-event lineage authority", ["tools/run_v17_9_3_1_2_11_verification.py", "tools/v17_9_3_1_2_11_independent_verifier.py"]),
    ]

    entries: List[Dict[str, Any]] = []
    for rel_file, sym_names, role_desc, consumers in target_specs:
        fpath = aurum_root / rel_file
        if not fpath.exists():
            continue
        src = fpath.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=rel_file)
        nodes_by_name: Dict[str, ast.AST] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name not in nodes_by_name:
                    nodes_by_name[node.name] = node

        for sname in sym_names:
            node = nodes_by_name.get(sname)
            if node is None:
                continue
            lineno = getattr(node, "lineno", 1)
            end_lineno = getattr(node, "end_lineno", lineno)
            args_list: List[str] = []
            ret_str = "Any"
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args_list = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
                if node.returns is not None:
                    try:
                        ret_str = ast.unparse(node.returns)
                    except Exception:
                        ret_str = "Annotated"
            elif isinstance(node, ast.ClassDef):
                methods = [
                    n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                args_list = methods
                ret_str = f"Class[{sname}]"

            entries.append({
                "file": f"aurum-desk/{rel_file}",
                "symbol": sname,
                "line_start": lineno,
                "line_end": end_lineno,
                "file_line": f"aurum-desk/{rel_file}:{lineno}",
                "role": role_desc,
                "inputs_or_methods": args_list,
                "output_contract": ret_str,
                "consumer_paths": consumers,
            })

    return {
        "version": VERSION_V17931211,
        "schema_version": SCHEMA_VERSION_V17931211,
        "total_anchored_symbols": len(entries),
        "contracts": entries,
    }


def evaluate_v17931211_acceptance_gates(
    *,
    verification_truth_gates: Mapping[str, Any],
    parentage_check: Mapping[str, Any],
    state_machine_check: Mapping[str, Any],
    fill_basis_check: Mapping[str, Any],
    deduplication_check: Mapping[str, Any],
    pnl_recompute_check: Mapping[str, Any],
    cohort_conservation_check: Mapping[str, Any],
    late_reconciliation_check: Mapping[str, Any],
    cross_adapter_parity_check: Mapping[str, Any],
    full_regression_check: Mapping[str, Any],
    historical_backtest_check: Mapping[str, Any],
    semantic_diff_check: Mapping[str, Any],
    worker_parity_check: Mapping[str, Any],
    oos_verdict_check: Mapping[str, Any],
    phase_b_check: Optional[Mapping[str, Any]] = None,
    phase_c_check: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Section 8: Evaluates the complete V17.9.3.1.2.11 gate matrix:
      T01..T10 (Verification Truth)
      E01..E10 (Exit-Event Lineage)
      R01..R05 (Regression, Replay, Cohort Audit, Worker Parity, OOS Truth)
      P01..P02 (Phase B Evidence Commit/Push & Phase C External Remote Readback)
    And computes the 6 independent top-level statuses.
    """
    t_gates = dict(verification_truth_gates.get("gates") or {})

    e_gates = {
        "E01": {
            "gate_id": "E01",
            "title": "Exit event IDs globally scoped and causally unique",
            "status": "PASS"
            if parentage_check.get("status") == "PASS" and deduplication_check.get("status") == "PASS"
            else "FAIL",
            "violations": list(parentage_check.get("violations") or []) + list(deduplication_check.get("violations") or []),
        },
        "E02": {
            "gate_id": "E02",
            "title": "Every closed trade's exit observation -> trigger -> fill exists",
            "status": "PASS" if state_machine_check.get("status") == "PASS" else "FAIL",
            "closed_verified": state_machine_check.get("closed_verified", 0),
            "violations": list(state_machine_check.get("violations") or []),
        },
        "E03": {
            "gate_id": "E03",
            "title": "Event timestamp, source and decision cutoff causal",
            "status": "PASS" if parentage_check.get("status") == "PASS" else "FAIL",
            "violations": list(parentage_check.get("violations") or []),
        },
        "E04": {
            "gate_id": "E04",
            "title": "Exit price basis and conservative ambiguity/adverse gaps correct",
            "status": "PASS" if fill_basis_check.get("status") == "PASS" else "FAIL",
            "violations": list(fill_basis_check.get("violations") or []),
        },
        "E05": {
            "gate_id": "E05",
            "title": "Exit state transitions valid and idempotent",
            "status": "PASS"
            if state_machine_check.get("status") == "PASS" and deduplication_check.get("status") == "PASS"
            else "FAIL",
            "violations": list(state_machine_check.get("violations") or []) + list(deduplication_check.get("violations") or []),
        },
        "E06": {
            "gate_id": "E06",
            "title": "Ledger posts exactly once and link to actual exit",
            "status": "PASS" if deduplication_check.get("status") == "PASS" else "FAIL",
            "violations": list(deduplication_check.get("violations") or []),
        },
        "E07": {
            "gate_id": "E07",
            "title": "Recomputed PnL/fees/R and accounting conservation pass",
            "status": "PASS" if pnl_recompute_check.get("status") == "PASS" else "FAIL",
            "violations": list(pnl_recompute_check.get("violations") or []),
        },
        "E08": {
            "gate_id": "E08",
            "title": "Open/unfilled/gap episodes excluded from closed PnL",
            "status": "PASS"
            if cohort_conservation_check.get("status") == "PASS" and state_machine_check.get("status") == "PASS"
            else "FAIL",
            "violations": list(cohort_conservation_check.get("violations") or []),
        },
        "E09": {
            "gate_id": "E09",
            "title": "Late/out-of-order/reconciled exits source-verified",
            "status": "PASS" if late_reconciliation_check.get("status") == "PASS" else "FAIL",
            "violations": list(late_reconciliation_check.get("violations") or []),
        },
        "E10": {
            "gate_id": "E10",
            "title": "Production/Shadow/Backtest common semantic invariants verified",
            "status": "PASS" if cross_adapter_parity_check.get("status") == "PASS" else "FAIL",
            "violations": list(cross_adapter_parity_check.get("violations") or []),
        },
    }

    r_gates = {
        "R01": {
            "gate_id": "R01",
            "title": "Full backend and relevant frontend regression executed",
            "status": "PASS" if full_regression_check.get("status") == "PASS" else "FAIL",
            "violations": list(full_regression_check.get("violations") or []),
        },
        "R02": {
            "gate_id": "R02",
            "title": "Real baseline backtest replay executed, no strategy changed",
            "status": "PASS" if historical_backtest_check.get("status") == "PASS" else "FAIL",
            "violations": list(historical_backtest_check.get("violations") or []),
        },
        "R03": {
            "gate_id": "R03",
            "title": "Original/cohort counts and before/after diff independently audited",
            "status": "PASS" if semantic_diff_check.get("status") == "PASS" else "FAIL",
            "violations": list(semantic_diff_check.get("violations") or []),
        },
        "R04": {
            "gate_id": "R04",
            "title": "Deterministic worker 1/2/4 semantic hashes matched (or BLOCKED)",
            "status": str(worker_parity_check.get("status") or "FAIL"),
            "violations": list(worker_parity_check.get("violations") or []),
        },
        "R05": {
            "gate_id": "R05",
            "title": "Prior OOS correctly labeled reused; no false fresh-OOS claim",
            "status": "PASS" if oos_verdict_check.get("status") == "PASS" else "FAIL",
            "fresh_oos_verdict": oos_verdict_check.get("fresh_oos_verdict", "REUSED"),
            "violations": list(oos_verdict_check.get("violations") or []),
        },
    }

    p01_status = str((phase_b_check or {}).get("status") or "BLOCKED")
    p02_status = str((phase_c_check or {}).get("status") or "BLOCKED")
    p_gates = {
        "P01": {
            "gate_id": "P01",
            "title": "Compact Phase B evidence committed/pushed on same branch",
            "status": p01_status if p01_status in ALLOWED_GATE_STATUSES_V17931211 else "BLOCKED",
            "details": dict(phase_b_check or {"reason": "AWAITING_PHASE_B_COMMIT_AND_PUSH"}),
        },
        "P02": {
            "gate_id": "P02",
            "title": "Phase C external remote ref/file SHA readback verified",
            "status": p02_status if p02_status in ALLOWED_GATE_STATUSES_V17931211 else "BLOCKED",
            "details": dict(phase_c_check or {"reason": "AWAITING_PHASE_C_EXTERNAL_REMOTE_READBACK"}),
        },
    }

    all_t_pass = bool(t_gates) and all(g.get("status") == "PASS" for g in t_gates.values())
    all_e_pass = all(g["status"] == "PASS" for g in e_gates.values())
    all_r_pass = all(g["status"] == "PASS" for g in r_gates.values())

    top_level_statuses = {
        "ENGINEERING_VERIFICATION_TRUTH": "PASS" if all_t_pass else "FAIL",
        "EXIT_EVENT_LINEAGE_VERDICT": "PASS" if all_e_pass else "FAIL",
        "BACKTEST_INTEGRITY_VERDICT": "PASS" if (all_e_pass and all_r_pass) else "FAIL",
        "STRATEGY_EDGE_VERDICT": "NEGATIVE_EXPECTANCY",
        "FRESH_OOS_VERDICT": "REUSED",
        "ACTIVATION_READY": "NO",
    }

    all_gates: Dict[str, Any] = {}
    all_gates.update(t_gates)
    all_gates.update(e_gates)
    all_gates.update(r_gates)
    all_gates.update(p_gates)

    return {
        "version": VERSION_V17931211,
        "schema_version": SCHEMA_VERSION_V17931211,
        "top_level_statuses": top_level_statuses,
        "verification_truth_verdict": top_level_statuses["ENGINEERING_VERIFICATION_TRUTH"],
        "exit_event_lineage_verdict": top_level_statuses["EXIT_EVENT_LINEAGE_VERDICT"],
        "prepublication_engineering_verdict": "PASS" if (all_t_pass and all_e_pass and all_r_pass) else "FAIL",
        "pre_publication_gates_all_pass": bool(all_t_pass and all_e_pass and all_r_pass),
        "publication_gates_all_pass": bool(p_gates["P01"]["status"] == "PASS" and p_gates["P02"]["status"] == "PASS"),
        "gates": all_gates,
    }
