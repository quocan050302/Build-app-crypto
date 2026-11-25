"""
aurum-desk/backend/services/kh_03_r6_acceptance_service.py

AURUM DESK — KH-03.R6 (M6) | PAIRED FAULT TRUTH ENGINE, OWNERSHIP AUDIT, UNSIGNED TEMPLATE & R6 INGEST BENCHMARK
SPEC VERSION: KH-03.R6-v1.0 | NO LITERAL FLAGS | INDEPENDENT PREDICATE RE-EVALUATION

Provides:
  1. R6 paired negative+positive fault execution engine (`R6FaultCaseSpec`, `REGISTERED_FAULT_PREDICATES_R6`,
     `execute_r6_fault_pair`, `validate_r6_fault_pair`, `derive_r6_fault_verdict`,
     `audit_r6_fault_files_no_literal_flags`) supporting all 56+ R6 controls (`A01..E06`).
  2. `build_r6_function_ownership_matrix()` documenting all new additive KH-03.R6 modules and zero locked edits.
  3. `build_r6_unsigned_owner_review_template(...)` emitting a clean unsigned review template with exact
     conservative Stage B/C proposed caps and local CLI commands (ZERO signatures, ZERO private keys).
  4. `run_r6_fixture_ingest_w1_w2_w4_benchmark(...)` running deterministic W1/W2/W4/W1_repeat R6 market-data
     validation & ingest benchmark over a fixed seeded XAUUSDT fixture manifest.
"""
from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
import uuid

import psutil

from services.kh_03_r6_durable_grant_service import (
    _is_strict_int,
    _sha256_canonical,
)
from services.kh_03_r6_market_data_service import (
    build_r6_bbo_coverage_report,
    build_r6_market_data_gap_ledger,
    normalize_r6_bbo_event,
    paginate_r6_ohlcv_history,
    validate_r6_funding_settlements,
)
from services.kh_03_r6_source_contract_service import (
    API_FAMILY_CLASSIC_V2,
    APPROVED_PRODUCT_TYPE_V2_R6,
    APPROVED_SYMBOL_R6,
    APPROVED_VENUE_R6,
    MANDATORY_BRANCH_KH03_R6,
    SCHEMA_VERSION_KH03_R6,
    SPEC_VERSION_KH03_R6,
)

PAIRED_FAULT_RECEIPT_SCHEMA_R6 = "kh_03_r6_paired_fault_v1"
ALLOWED_FAULT_KINDS_R6: Tuple[str, ...] = ("NEGATIVE_CONTRACT", "INTEGRATION_FAULT", "BASELINE_BUG_RED")


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def _contains_nonfinite(obj: Any) -> bool:
    if isinstance(obj, float):
        return not math.isfinite(obj)
    if isinstance(obj, Mapping):
        return any(_contains_nonfinite(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_contains_nonfinite(x) for x in obj)
    return False


# =====================================================================
# 1. Registered Pure Predicates over Raw Observed Fields (M6 / G26)
# =====================================================================
def _p_rejected_with_violations(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = o.get("valid") is False and _is_strict_int(o.get("violations_count")) and int(o["violations_count"]) >= 1
    return ok, "rejected_with_violations"


def _p_accepted_clean(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = o.get("valid") is True and _is_strict_int(o.get("violations_count")) and int(o["violations_count"]) == 0
    return ok, "accepted_clean"


def _p_blocked_external_zero_io(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("allowed") is False
        and o.get("external_requests_attempted") == 0
        and _is_strict_int(o.get("violations_count")) and int(o["violations_count"]) >= 1
    )
    return ok, "blocked_external_zero_io"


def _p_claim_denied_no_lease(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = o.get("claimed") is False and o.get("lease_issued") is False and _is_strict_int(o.get("violations_count")) and int(o["violations_count"]) >= 1
    return ok, "claim_denied_no_lease"


def _p_claim_accepted_once(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = o.get("claimed") is True and o.get("lease_issued") is True and _is_strict_int(o.get("violations_count")) and int(o["violations_count"]) == 0
    return ok, "claim_accepted_once"


def _p_cross_process_single_winner(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        _is_strict_int(o.get("contenders")) and int(o["contenders"]) >= 2
        and _is_strict_int(o.get("winners")) and int(o["winners"]) == 1
        and _is_strict_int(o.get("losers")) and int(o["losers"]) == int(o["contenders"]) - 1
    )
    return ok, "cross_process_single_winner"


def _p_crash_unknown_retained(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("child_killed_abruptly") is True
        and o.get("post_restart_grant_state") in ("SPENT_CRASH_RECOVERED", "SPENT", "QUARANTINED")
        and o.get("replay_after_restart_denied") is True
        and _is_strict_int(o.get("uncertain_bytes_retained")) and int(o["uncertain_bytes_retained"]) >= int(o.get("expected_min_uncertain_bytes", 0))
    )
    return ok, "crash_unknown_retained"


def _p_ledger_open_failed_closed(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = o.get("opened") is False and o.get("auto_initialized_empty") is False and _is_strict_int(o.get("violations_count")) and int(o["violations_count"]) >= 1
    return ok, "ledger_open_failed_closed"


def _p_ledger_open_verified(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = o.get("opened") is True and _is_strict_int(o.get("generation")) and int(o["generation"]) >= 1 and _is_strict_int(o.get("violations_count")) and int(o["violations_count"]) == 0
    return ok, "ledger_open_verified"


def _p_budget_action_rejected(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = o.get("action_ok") is False and _is_strict_int(o.get("violations_count")) and int(o["violations_count"]) >= 1
    return ok, "budget_action_rejected"


def _p_budget_action_ok(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = o.get("action_ok") is True and _is_strict_int(o.get("violations_count")) and int(o["violations_count"]) == 0
    return ok, "budget_action_ok"


def _p_transport_reaped_aborted(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("ok") is False
        and o.get("reaped_verified") is True
        and o.get("surviving_descendants") == 0
        and o.get("accepted_result_is_none") is True
    )
    return ok, "transport_reaped_aborted"


def _p_transport_quarantined_undrained(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("ok") is False
        and o.get("status") == "FAIL_UNDRAINED_TRANSPORT"
        and o.get("ledger_quarantined") is True
    )
    return ok, "transport_quarantined_undrained"


def _p_transport_loopback_ok(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("ok") is True
        and o.get("reaped_verified") is True
        and o.get("surviving_descendants") == 0
        and _is_strict_int(o.get("child_pid")) and int(o["child_pid"]) > 0
    )
    return ok, "transport_loopback_ok"


def _p_rest_or_ws_bounded_rejected(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("ok") is False
        and _is_strict_int(o.get("violations_count")) and int(o["violations_count"]) >= 1
        and o.get("expected_token_matched") is True
    )
    return ok, "rest_or_ws_bounded_rejected"


def _p_rest_or_ws_bounded_ok(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = o.get("ok") is True and _is_strict_int(o.get("violations_count")) and int(o["violations_count"]) == 0
    return ok, "rest_or_ws_bounded_ok"


def _p_data_anomaly_caught(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = o.get("anomaly_caught") is True and o.get("fabricated_or_inflated") is False
    return ok, "data_anomaly_caught"


def _p_data_clean_valid(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = o.get("clean_valid") is True and o.get("violations_count") == 0
    return ok, "data_clean_valid"


def _p_custody_denied_or_caught(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = o.get("denied_or_caught") is True and _is_strict_int(o.get("violations_count")) and int(o["violations_count"]) >= 1
    return ok, "custody_denied_or_caught"


def _p_custody_verified_clean(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = o.get("verified_clean") is True and o.get("violations_count") == 0
    return ok, "custody_verified_clean"


def _p_release_gate_rejected(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = o.get("gate_passed") is False and o.get("expected_rejection_matched") is True
    return ok, "release_gate_rejected"


def _p_release_gate_passed(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = o.get("gate_passed") is True and o.get("violations_count") == 0
    return ok, "release_gate_passed"


REGISTERED_FAULT_PREDICATES_R6: Dict[str, Callable[[Mapping[str, Any]], Tuple[bool, str]]] = {
    "rejected_with_violations": _p_rejected_with_violations,
    "accepted_clean": _p_accepted_clean,
    "blocked_external_zero_io": _p_blocked_external_zero_io,
    "claim_denied_no_lease": _p_claim_denied_no_lease,
    "claim_accepted_once": _p_claim_accepted_once,
    "cross_process_single_winner": _p_cross_process_single_winner,
    "crash_unknown_retained": _p_crash_unknown_retained,
    "ledger_open_failed_closed": _p_ledger_open_failed_closed,
    "ledger_open_verified": _p_ledger_open_verified,
    "budget_action_rejected": _p_budget_action_rejected,
    "budget_action_ok": _p_budget_action_ok,
    "transport_reaped_aborted": _p_transport_reaped_aborted,
    "transport_quarantined_undrained": _p_transport_quarantined_undrained,
    "transport_loopback_ok": _p_transport_loopback_ok,
    "rest_or_ws_bounded_rejected": _p_rest_or_ws_bounded_rejected,
    "rest_or_ws_bounded_ok": _p_rest_or_ws_bounded_ok,
    "data_anomaly_caught": _p_data_anomaly_caught,
    "data_clean_valid": _p_data_clean_valid,
    "custody_denied_or_caught": _p_custody_denied_or_caught,
    "custody_verified_clean": _p_custody_verified_clean,
    "release_gate_rejected": _p_release_gate_rejected,
    "release_gate_passed": _p_release_gate_passed,
}


@dataclass(frozen=True)
class R6FaultCaseSpec:
    control_id: str
    category: str
    kind: str  # "NEGATIVE_CONTRACT" | "INTEGRATION_FAULT" | "BASELINE_BUG_RED"
    target_qualified_name: str
    negative_predicate_id: str
    positive_predicate_id: str
    description: str


def _compute_side_trace_digest(side: Mapping[str, Any]) -> str:
    body = {
        "execution_id": side["execution_id"],
        "predicate_id": side["predicate_id"],
        "input_summary": side["input_summary"],
        "observed_fields": side["observed_fields"],
        "pid": side["pid"],
        "executable_sha": side["executable_sha"],
        "run_id": side["run_id"],
    }
    return _sha256_canonical(body)


def execute_r6_fault_pair(
    spec: R6FaultCaseSpec,
    *,
    negative_fn: Callable[[], Tuple[Mapping[str, Any], Mapping[str, Any]]],
    positive_fn: Callable[[], Tuple[Mapping[str, Any], Mapping[str, Any]]],
    executable_sha: str,
    run_id: str,
) -> Dict[str, Any]:
    """
    Executes BOTH the negative perturbation and clean positive control separately against the same
    contract with distinct execution IDs, PIDs, timings, registered predicates, and raw trace digests.
    """
    if spec.kind not in ALLOWED_FAULT_KINDS_R6:
        raise ValueError(f"INVALID_FAULT_KIND:{spec.kind}")
    if spec.negative_predicate_id not in REGISTERED_FAULT_PREDICATES_R6:
        raise ValueError(f"UNKNOWN_NEG_PREDICATE:{spec.negative_predicate_id}")
    if spec.positive_predicate_id not in REGISTERED_FAULT_PREDICATES_R6:
        raise ValueError(f"UNKNOWN_POS_PREDICATE:{spec.positive_predicate_id}")

    neg_pred = REGISTERED_FAULT_PREDICATES_R6[spec.negative_predicate_id]
    pos_pred = REGISTERED_FAULT_PREDICATES_R6[spec.positive_predicate_id]

    t0_n = time.perf_counter()
    neg_in, neg_obs = negative_fn()
    neg_ms = round((time.perf_counter() - t0_n) * 1000.0, 3)
    neg_ok, neg_note = neg_pred(neg_obs)
    neg_side: Dict[str, Any] = {
        "execution_id": f"{spec.control_id}_NEG_{uuid.uuid4().hex[:12]}",
        "predicate_id": spec.negative_predicate_id,
        "predicate_passed": bool(neg_ok),
        "predicate_note": neg_note,
        "input_summary": dict(neg_in),
        "observed_fields": dict(neg_obs),
        "elapsed_ms": neg_ms,
        "pid": os.getpid(),
        "executable_sha": executable_sha,
        "run_id": run_id,
    }
    neg_side["trace_digest"] = _compute_side_trace_digest(neg_side)

    t0_p = time.perf_counter()
    pos_in, pos_obs = positive_fn()
    pos_ms = round((time.perf_counter() - t0_p) * 1000.0, 3)
    pos_ok, pos_note = pos_pred(pos_obs)
    pos_side: Dict[str, Any] = {
        "execution_id": f"{spec.control_id}_POS_{uuid.uuid4().hex[:12]}",
        "predicate_id": spec.positive_predicate_id,
        "predicate_passed": bool(pos_ok),
        "predicate_note": pos_note,
        "input_summary": dict(pos_in),
        "observed_fields": dict(pos_obs),
        "elapsed_ms": pos_ms,
        "pid": os.getpid(),
        "executable_sha": executable_sha,
        "run_id": run_id,
    }
    pos_side["trace_digest"] = _compute_side_trace_digest(pos_side)

    rec = {
        "receipt_schema": PAIRED_FAULT_RECEIPT_SCHEMA_R6,
        "control_id": spec.control_id,
        "category": spec.category,
        "kind": spec.kind,
        "target_qualified_name": spec.target_qualified_name,
        "description": spec.description,
        "executable_sha": executable_sha,
        "run_id": run_id,
        "negative": neg_side,
        "positive": pos_side,
        "status": "PASS" if (neg_ok and pos_ok) else "FAIL",
    }
    return rec


def validate_r6_fault_pair(
    rec: Mapping[str, Any],
    *,
    expected_executable_sha: str,
    expected_run_id: str,
) -> Tuple[bool, List[str]]:
    viols: List[str] = []
    if rec.get("receipt_schema") != PAIRED_FAULT_RECEIPT_SCHEMA_R6:
        viols.append("BAD_RECEIPT_SCHEMA")
    if rec.get("kind") not in ALLOWED_FAULT_KINDS_R6:
        viols.append(f"BAD_KIND:{rec.get('kind')}")
    if rec.get("executable_sha") != expected_executable_sha:
        viols.append("RECEIPT_SHA_MISMATCH")
    if rec.get("run_id") != expected_run_id:
        viols.append("RECEIPT_RUN_ID_MISMATCH")
    if _contains_nonfinite(rec):
        viols.append("RECEIPT_CONTAINS_NONFINITE")

    neg = rec.get("negative")
    pos = rec.get("positive")
    if not isinstance(neg, Mapping) or not isinstance(pos, Mapping):
        return False, viols + ["MISSING_NEG_OR_POS_SIDE"]

    if neg.get("execution_id") == pos.get("execution_id"):
        viols.append("SHARED_NEG_POS_EXECUTION_ID")

    for label, side in (("negative", neg), ("positive", pos)):
        pid_key = str(side.get("predicate_id") or "")
        pfn = REGISTERED_FAULT_PREDICATES_R6.get(pid_key)
        if pfn is None:
            viols.append(f"UNREGISTERED_PREDICATE:{label}:{pid_key}")
            continue
        if side.get("executable_sha") != expected_executable_sha or side.get("run_id") != expected_run_id:
            viols.append(f"SIDE_SHA_OR_RUN_MISMATCH:{label}")
        if not _is_strict_int(side.get("pid")) or int(side["pid"]) <= 0:
            viols.append(f"INVALID_SIDE_PID:{label}")
        if not _is_num(side.get("elapsed_ms")) or float(side["elapsed_ms"]) < 0.0:
            viols.append(f"INVALID_SIDE_ELAPSED:{label}")
        recomp_dig = _compute_side_trace_digest(side)
        if recomp_dig != side.get("trace_digest"):
            viols.append(f"TRACE_DIGEST_TAMPERED:{label}")
        re_ok, _ = pfn(side.get("observed_fields") or {})
        if not re_ok:
            viols.append(f"PREDICATE_RECOMPUTATION_FAILED:{label}:{pid_key}")
        if side.get("predicate_passed") is not True:
            viols.append(f"PREDICATE_NOT_PASSED:{label}")

    if rec.get("status") != "PASS":
        viols.append("RECEIPT_STATUS_NOT_PASS")
    return len(viols) == 0, viols


def derive_r6_fault_verdict(
    receipts: Sequence[Mapping[str, Any]],
    *,
    expected_executable_sha: str,
    expected_run_id: str,
) -> Dict[str, Any]:
    seen_cids: set[str] = set()
    seen_neg_eids: set[str] = set()
    seen_pos_eids: set[str] = set()
    passed = 0
    failed = 0
    violations: List[str] = []
    by_cat: Dict[str, int] = {}
    by_kind: Dict[str, int] = {}

    for rec in receipts:
        cid = str(rec.get("control_id") or "")
        if not cid or cid in seen_cids:
            violations.append(f"DUPLICATE_OR_EMPTY_CONTROL_ID:{cid}")
            failed += 1
            continue
        seen_cids.add(cid)

        neg_eid = str((rec.get("negative") or {}).get("execution_id") or "")
        pos_eid = str((rec.get("positive") or {}).get("execution_id") or "")
        if not neg_eid or neg_eid in seen_neg_eids or neg_eid in seen_pos_eids:
            violations.append(f"DUPLICATE_NEG_EXECUTION_ID:{cid}:{neg_eid}")
            failed += 1
            continue
        if not pos_eid or pos_eid in seen_pos_eids or pos_eid in seen_neg_eids:
            violations.append(f"DUPLICATE_POS_EXECUTION_ID:{cid}:{pos_eid}")
            failed += 1
            continue
        seen_neg_eids.add(neg_eid)
        seen_pos_eids.add(pos_eid)

        ok, rv = validate_r6_fault_pair(rec, expected_executable_sha=expected_executable_sha, expected_run_id=expected_run_id)
        if ok:
            passed += 1
            cat = str(rec.get("category") or "UNKNOWN")
            knd = str(rec.get("kind") or "UNKNOWN")
            by_cat[cat] = by_cat.get(cat, 0) + 1
            by_kind[knd] = by_kind.get(knd, 0) + 1
        else:
            failed += 1
            violations.extend(f"{cid}:{v}" for v in rv)

    return {
        "valid": failed == 0 and passed == len(receipts) and passed > 0,
        "total_controls": len(receipts),
        "passed_controls": passed,
        "failed_controls": failed,
        "unique_control_ids_count": len(seen_cids),
        "unique_negative_execution_ids_count": len(seen_neg_eids),
        "unique_positive_execution_ids_count": len(seen_pos_eids),
        "counts_by_category": by_cat,
        "counts_by_kind": by_kind,
        "violations": violations,
    }


def audit_r6_fault_files_no_literal_flags(paths: Sequence[Path]) -> Dict[str, Any]:
    """AST scanner verifying zero literal `red_failed=True` / `green_passed=True` shortcuts exist."""
    bad_keys = {"red_failed", "green_passed", "predicate_passed"}
    hits: List[str] = []
    for p in paths:
        if not p.is_file():
            continue
        tree = ast.parse(p.read_text("utf-8", errors="replace"), filename=str(p))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and k.value in bad_keys and isinstance(v, ast.Constant) and isinstance(v.value, bool):
                        hits.append(f"{p.name}:L{node.lineno}:literal_{k.value}={v.value}")
    return {"clean": len(hits) == 0, "literal_flag_hits_count": len(hits), "hits": hits}


# =====================================================================
# 2. Function Ownership Matrix (100% Additive R6 Modules)
# =====================================================================
def build_r6_function_ownership_matrix() -> List[Dict[str, Any]]:
    return [
        {
            "file": "aurum-desk/backend/services/kh_03_r6_source_contract_service.py",
            "module": "M1",
            "change_type": "NEW_R6_ADDITIVE",
            "functions": ["build_r6_source_contract_registry", "validate_r6_endpoint_contract", "validate_r6_instrument_identity", "build_r6_source_capability_matrix"],
            "reason": "Bitget Classic v2 official docs-verified contract registry, non-mixing v2/v3 validator, strict Decimal instrument identity & capability matrix.",
        },
        {
            "file": "aurum-desk/backend/services/kh_03_r6_durable_grant_service.py",
            "module": "M2",
            "change_type": "NEW_R6_ADDITIVE",
            "functions": ["load_r6_owner_trust_root", "verify_r6_owner_grant", "canonical_r6_grant_identity", "provision_r6_durable_ledger", "open_r6_durable_ledger", "R6DurableGrantLedger", "verify_r6_ledger_readonly"],
            "reason": "Cross-process/restart durable SQLite + external anchor one-shot owner authorization ledger using Ed25519 (cryptography) with fail-closed crash/rollback recovery.",
        },
        {
            "file": "aurum-desk/backend/services/kh_03_r6_bounded_transport_service.py",
            "module": "M3",
            "change_type": "NEW_R6_ADDITIVE",
            "functions": ["build_r6_bound_capture_plan", "start_r6_owned_transport_worker", "execute_r6_bounded_rest_get", "execute_r6_bounded_ws_session", "supervise_r6_session", "cancel_and_reap_r6_transport", "validate_r6_resource_guard", "R6BoundedQueueWriter"],
            "reason": "Separate R6 owned-subprocess transport with Windows Job Object / POSIX PGID process-tree kill & OS reap proof, streaming bounded REST/WS clients, and multi-volume resource guard.",
        },
        {
            "file": "aurum-desk/backend/services/kh_03_r6_market_data_service.py",
            "module": "M4",
            "change_type": "NEW_R6_ADDITIVE",
            "functions": ["paginate_r6_ohlcv_history", "normalize_r6_bbo_event", "validate_r6_bbo_epoch", "build_r6_bbo_coverage_report", "acquire_r6_funding_history", "validate_r6_funding_settlements", "validate_r6_owner_fee_evidence", "build_r6_slippage_registry", "build_r6_market_data_gap_ledger"],
            "reason": "Strict Decimal/UTC-ms OHLCV pagination/quarantine, BBO event vs state-known coverage, funding completeness without zero-fill, owner fee/slippage honesty, and gap ledger.",
        },
        {
            "file": "aurum-desk/backend/services/kh_03_r6_custody_readiness_service.py",
            "module": "M5",
            "change_type": "NEW_R6_ADDITIVE",
            "functions": ["assign_r6_dev_partition_before_io", "enforce_r6_destination_guard", "mint_r6_raw_provenance_receipt", "R6CustodyJournal", "verify_r6_custody_readonly", "seal_r6_dev_dataset_manifest", "derive_r6_readiness_verdicts", "build_r6_owner_handover_vi"],
            "reason": "DEV_ONLY/QA_FIXTURE partition & symlink/junction/OOS deny-before-open guard, R6 provenance, chunked append-only custody + anchor, sealed DEV manifest, and 36-gate stage-scoped verdicts.",
        },
        {
            "file": "aurum-desk/backend/services/kh_03_r6_acceptance_service.py",
            "module": "M6",
            "change_type": "NEW_R6_ADDITIVE",
            "functions": ["execute_r6_fault_pair", "validate_r6_fault_pair", "derive_r6_fault_verdict", "build_r6_unsigned_owner_review_template", "run_r6_fixture_ingest_w1_w2_w4_benchmark"],
            "reason": "Paired fault truth receipts, unsigned owner review template generator, and deterministic W1/W2/W4/W1-repeat R6 fixture ingest benchmark.",
        },
        {
            "file": "aurum-desk/tools/run_kh_03_r6_acceptance.py",
            "module": "T1",
            "change_type": "NEW_R6_ADDITIVE",
            "functions": ["main"],
            "reason": "Offline Stage A acceptance orchestrator (0 external market sockets, full regression/parity/replay/benchmarks/56+ pairs/30+ verifier mutations).",
        },
        {
            "file": "aurum-desk/tools/run_kh_03_r6_capture.py",
            "module": "T2",
            "change_type": "NEW_R6_ADDITIVE",
            "functions": ["main"],
            "reason": "Explicit owner-authorized Stage B/C acquisition CLI (defaults to --dry-run, zero trading/private endpoints, requires genuine local Ed25519 grant + ledger + frozen release).",
        },
        {
            "file": "aurum-desk/tools/kh_03_r6_independent_verifier.py",
            "module": "T3",
            "change_type": "NEW_R6_ADDITIVE",
            "functions": ["verify_kh_03_r6_run_dir"],
            "reason": "Distinct OS process read-only disk verifier auditing all 00..27 artifacts & raw logs/JUnit and sealing 26_independent_OS_verifier_manifest_sha256.json.",
        },
    ]


# =====================================================================
# 3. Unsigned Owner Review Template Generator (Zero Secrets/Signatures!)
# =====================================================================
def build_r6_unsigned_owner_review_template(
    *,
    tested_executable_sha: str,
    stage_a_run_id: str,
    source_registry_digest: str,
    stage: str = "B_SMOKE",
) -> Dict[str, Any]:
    """
    Emits a strictly UNSIGNED review template for the project owner.
    Contains NO signature, NO private key, and CANNOT pass `verify_r6_owner_grant` until the
    owner independently reviews, fills local store paths/windows, and signs offline with Ed25519.
    """
    is_smoke = stage == "B_SMOKE"
    proposed_caps = (
        {
            "connections": 2,
            "requests": 30,
            "retries": 6,
            "subscriptions": 2,
            "messages": 10_000,
            "payload_bytes": 10 * 1024 * 1024,
            "decoded_bytes": 20 * 1024 * 1024,
            "pages": 5,
            "disk_bytes": 32 * 1024 * 1024,
            "records": 20_000,
        }
        if is_smoke
        else {
            "connections": 4,
            "requests": 500,
            "retries": 20,
            "subscriptions": 4,
            "messages": 200_000,
            "payload_bytes": 50 * 1024 * 1024,
            "decoded_bytes": 100 * 1024 * 1024,
            "pages": 250,
            "disk_bytes": 256 * 1024 * 1024,
            "records": 500_000,
        }
    )
    return {
        "template_status": "UNSIGNED_OWNER_REVIEW_TEMPLATE",
        "unsigned_template": True,
        "valid_authorization": False,
        "warning_vi": "DAY LA MAU PHE DUYET CHUA KY — KHONG CO HIEU LUC MO SOCKET. Chu du an phai tu kiem tra, dien duong dan local ngoai Git, va ky offline bang khoa Ed25519 rieng.",
        "stage": stage,
        "required_prerequisites": [
            f"Independently verify Stage A run {stage_a_run_id} on frozen executable SHA {tested_executable_sha}.",
            "Provision local Owner Ed25519 Trust Root JSON outside Git on fixed volume with restricted ACL.",
            "Explicitly provision local durable SQLite ledger + external anchor outside Git (never auto-created).",
            "Fill exact UTC window [window_start_ms, window_end_ms), expiry, local destination_root and store_id.",
            "Sign grant_payload offline using Ed25519 private key (private key NEVER enters repo, CLI, logs, or chat).",
        ],
        "proposed_grant_payload_template": {
            "grant_id": "<OWNER_SETS_UNIQUE_ONE_SHOT_GRANT_ID>",
            "issuer": "<OWNER_ISSUER_ID_MATCHING_TRUST_ROOT>",
            "trust_namespace": "OWNER_EXTERNAL_PRODUCTION_TRUST",
            "issued_utc_ms": "<OWNER_SETS_INTEGER_UTC_MS>",
            "expires_utc_ms": "<OWNER_SETS_FINITE_EXPIRY_UTC_MS>",
            "repo": "quocan050302/Build-app-crypto",
            "branch": MANDATORY_BRANCH_KH03_R6,
            "tested_executable_sha": tested_executable_sha,
            "release_manifest_sha256": "<OWNER_PINS_SEALED_26_MANIFEST_SHA256>",
            "purpose": "DEV_ONLY",
            "stage": stage,
            "venue": APPROVED_VENUE_R6,
            "api_family": API_FAMILY_CLASSIC_V2,
            "symbol": APPROVED_SYMBOL_R6,
            "product_type": APPROVED_PRODUCT_TYPE_V2_R6,
            "allowed_endpoints": [
                "classic_v2_contracts",
                "classic_v2_history_candles",
                "classic_v2_history_fund_rate",
                "classic_v2_ws_books1",
            ],
            "allowed_ws_channels": ["books1"],
            "window_start_ms": "<OWNER_SETS_DEV_WINDOW_START_UTC_MS>",
            "window_end_ms": "<OWNER_SETS_DEV_WINDOW_END_UTC_MS>",
            "destination_store_id": "<OWNER_PROVISIONED_LOCAL_STORE_ID>",
            "destination_root": "<LOCAL_FIXED_DRIVE_PATH_OUTSIDE_GIT>",
            "max_session_duration_sec": 300 if is_smoke else 3600,
            "max_concurrency": 2,
            "caps": proposed_caps,
            "retention_policy": {"max_retention_days": 90, "git_commit_raw_payloads": False},
        },
        "source_registry_digest": source_registry_digest,
        "exact_local_cli_commands": {
            "dry_run_command": f"python aurum-desk/tools/run_kh_03_r6_capture.py --stage {'smoke' if is_smoke else 'dev'} --dry-run --plan <local-plan.json>",
            "authorized_command": (
                f"python aurum-desk/tools/run_kh_03_r6_capture.py --stage {'smoke' if is_smoke else 'dev'} "
                "--grant <local-signed-grant.json> --owner-config <local-owner-config.json> "
                "--release-manifest <local-stageA-manifest.json>"
            ),
        },
    }


# =====================================================================
# 4. Deterministic W1/W2/W4/W1_repeat R6 Fixture Ingest Benchmark (§9.5)
# =====================================================================
def _process_r6_fixture_partition(part_idx: int, bars_per_part: int, base_ts_ms: int) -> Dict[str, Any]:
    """Deterministic pure ingest/validation slice (OHLCV + BBO + funding + gap ledger)."""
    start_ms = base_ts_ms + part_idx * bars_per_part * 60_000
    end_ms = start_ms + bars_per_part * 60_000
    server_ms = end_ms + 60_000

    # Generate deterministic OHLCV rows (60 bars/page)
    all_bars = []
    for i in range(bars_per_part):
        ts = start_ms + i * 60_000
        px = 2650 + ((part_idx * 17 + i * 3) % 40)
        all_bars.append([
            str(ts),
            f"{px}.10",
            f"{px + 1}.50",
            f"{px - 1}.00",
            f"{px}.80",
            f"{12 + (i % 7)}.25",
        ])

    def _fetcher(cur_end: Optional[int], pno: int) -> Dict[str, Any]:
        hi = int(cur_end or end_ms)
        sub = [r for r in all_bars if int(r[0]) < hi]
        page = sub[-50:]
        return {"ok": True, "rows": page}

    exp_ts = [start_ms + i * 60_000 for i in range(bars_per_part)]
    ohlcv = paginate_r6_ohlcv_history(
        symbol=APPROVED_SYMBOL_R6,
        granularity="1m",
        window_start_ms=start_ms,
        window_end_ms=end_ms,
        server_time_ms=server_ms,
        page_fetcher=_fetcher,
        max_pages=20,
        calendar_expected_open_times_ms=exp_ts,
        tick_size="0.01",
        provenance_class="QA_LOOPBACK",
    )

    # Deterministic 120-slot BBO slice
    bbo_start = start_ms
    slots = [bbo_start + s * 1000 for s in range(120)]
    evs = []
    for s in range(0, 120, 4):
        ev_ts = bbo_start + s * 1000
        n = normalize_r6_bbo_event(
            {
                "symbol": APPROVED_SYMBOL_R6,
                "bid_price": "2650.10",
                "bid_size": "5.0",
                "ask_price": "2650.20",
                "ask_size": "4.5",
                "event_ts_ms": ev_ts,
                "receive_utc_ms": ev_ts + 25,
                "epoch": 1,
                "epoch_healthy": True,
            },
            tick_size="0.01",
        )
        assert n["valid_for_streaming_bbo"]
        row = dict(n["normalized"])
        row["valid_for_streaming_bbo"] = True
        evs.append(row)
    bbo_cov = build_r6_bbo_coverage_report(
        evs,
        [{"epoch": 1, "start_ms": bbo_start, "end_ms": bbo_start + 120_000, "epoch_healthy": True, "snapshot_received": True}],
        calendar_expected_1s_slots_ms=slots,
        max_state_freshness_ms=10_000,
    )

    # Deterministic funding settlement slice aligned to 8h
    step8 = 8 * 3_600_000
    f_start = ((start_ms // step8) * step8)
    f_end = f_start + 2 * step8
    fund = validate_r6_funding_settlements(
        [
            {"symbol": APPROVED_SYMBOL_R6, "fundingTime": f_start, "fundingRate": "0.0001"},
            {"symbol": APPROVED_SYMBOL_R6, "fundingTime": f_start + step8, "fundingRate": "-0.00005"},
        ],
        symbol=APPROVED_SYMBOL_R6,
        window_start_ms=f_start,
        window_end_ms=f_end,
        observed_fund_interval_hours=8,
        provenance_class="QA_LOOPBACK",
    )
    gaps = build_r6_market_data_gap_ledger(dataset_scope="QA_FIXTURE", ohlcv_result=ohlcv, bbo_result=bbo_cov, funding_result=fund)
    out = {
        "part_idx": part_idx,
        "ohlcv_sha": ohlcv["canonical_rows_sha256"],
        "ohlcv_rows": ohlcv["canonical_valid_count"],
        "bbo_state_cov": bbo_cov["state_known_coverage_ratio"],
        "bbo_obs_cov": bbo_cov["observed_event_coverage_ratio"],
        "fund_sha": fund["realized_sha256"],
        "gap_sha": gaps["gap_ledger_sha256"],
        "all_ok": ohlcv["ok"] and bbo_cov["readiness_eligible"] and fund["ok"],
    }
    return out


def run_r6_fixture_ingest_w1_w2_w4_benchmark(
    *,
    num_partitions: int = 16,
    bars_per_partition: int = 120,
) -> Dict[str, Any]:
    """
    Executes fixed-input R6 market-data validation & ingest benchmark at W=1, W=2, W=4, W=1_repeat.
    Returns exact wall-clock timings, peak RSS, CPU time, semantic sorted SHA-256, repeat drift,
    measured fastest vs safe cap.
    """
    base_ts_ms = 1794009600000  # aligned UTC hour boundary
    input_manifest = {
        "schema": "kh_03_r6_ingest_benchmark_manifest_v1",
        "symbol": APPROVED_SYMBOL_R6,
        "num_partitions": int(num_partitions),
        "bars_per_partition": int(bars_per_partition),
        "base_ts_ms": base_ts_ms,
    }
    input_manifest_sha = _sha256_canonical(input_manifest)

    runs: List[Dict[str, Any]] = []
    proc = psutil.Process(os.getpid())

    for label, workers in (("w=1", 1), ("w=2", 2), ("w=4", 4), ("w=1_repeat", 1)):
        t_cpu0 = proc.cpu_times()
        t0 = time.perf_counter()
        if workers == 1:
            parts = [_process_r6_fixture_partition(i, bars_per_partition, base_ts_ms) for i in range(num_partitions)]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                parts = list(pool.map(lambda i: _process_r6_fixture_partition(i, bars_per_partition, base_ts_ms), range(num_partitions)))
        wall_sec = round(time.perf_counter() - t0, 5)
        t_cpu1 = proc.cpu_times()
        cpu_sec = round((t_cpu1.user - t_cpu0.user) + (t_cpu1.system - t_cpu0.system), 5)
        rss_mib = round(float(proc.memory_info().rss) / float(1024 ** 2), 3)

        parts_sorted = sorted(parts, key=lambda x: int(x["part_idx"]))
        sem_sha = _sha256_canonical(parts_sorted)
        all_ok = all(p["all_ok"] for p in parts_sorted)
        total_rows = sum(int(p["ohlcv_rows"]) for p in parts_sorted)

        runs.append({
            "mode_label": label,
            "workers": workers,
            "wall_sec": wall_sec,
            "cpu_sec": cpu_sec,
            "rss_mib": rss_mib,
            "pid": os.getpid(),
            "input_manifest_sha256": input_manifest_sha,
            "total_ohlcv_rows": total_rows,
            "all_partitions_ok": all_ok,
            "semantic_result_sha256": sem_sha,
        })

    shas = {r["semantic_result_sha256"] for r in runs}
    all_identical = len(shas) == 1 and all(r["all_partitions_ok"] for r in runs)
    w1_wall = max(1e-6, float(runs[0]["wall_sec"]))
    w2_wall = max(1e-6, float(runs[1]["wall_sec"]))
    w4_wall = max(1e-6, float(runs[2]["wall_sec"]))
    w1_rep = max(1e-6, float(runs[3]["wall_sec"]))

    repeat_drift_pct = round(abs(w1_rep - w1_wall) / w1_wall * 100.0, 2)
    best_entry = min(runs[:3], key=lambda r: float(r["wall_sec"]))
    speedup_best = round(w1_wall / max(1e-6, float(best_entry["wall_sec"])), 4)
    # Conservative safe cap: require >= 1.15x speedup above noise before picking higher concurrency
    applied_safe = int(best_entry["workers"]) if (speedup_best >= 1.15 and int(best_entry["workers"]) <= 4) else 1

    return {
        "benchmark_kind": "KH03_R6_FIXTURE_INGEST_V1",
        "input_manifest": input_manifest,
        "input_manifest_sha256": input_manifest_sha,
        "all_4_identical": all_identical,
        "canonical_semantic_sha256": next(iter(shas)) if all_identical else None,
        "w1_wall_sec": w1_wall,
        "w2_wall_sec": w2_wall,
        "w4_wall_sec": w4_wall,
        "w1_repeat_wall_sec": w1_rep,
        "repeat_drift_pct": repeat_drift_pct,
        "measured_fastest_workers": int(best_entry["workers"]),
        "measured_speedup_ratio": speedup_best,
        "applied_safe_workers_cap": applied_safe,
        "runs": runs,
        "valid": all_identical and all(_is_num(r["wall_sec"]) and r["wall_sec"] >= 0.0 for r in runs),
    }
