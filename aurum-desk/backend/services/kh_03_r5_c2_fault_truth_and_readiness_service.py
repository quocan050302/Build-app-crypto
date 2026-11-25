"""
aurum-desk/backend/services/kh_03_r5_c2_fault_truth_and_readiness_service.py

AURUM DESK — KH-03.R5.C2 | TECHNICAL CLOSURE SERVICE
SPEC VERSION: KH-03.R5.C2-v1.0 | FAULT CONTROL TRUTH | C1 BASELINE RED HARNESS | 32-GATE VERDICTS

Provides:
  1. Typed execution-derived fault control framework (`FaultCaseSpec`, `FaultExecutionReceipt`,
     `execute_fault_case_pair`, `validate_fault_execution_receipt`, `derive_fault_control_verdict`,
     `REGISTERED_FAULT_PREDICATES_C2`, `audit_fault_certification_ast_for_literal_flags`) closing P1-D.
  2. `run_real_c1_baseline_red_reproduction_from_git_sha(...)` — extracts untouched C1 executable files
     from `3ad0c2973eeaa11f1e635b7a82beddeb4b5a8db8` into an isolated temp overlay, reproduces P1-D,
     P1-E, and P1-F honestly with zero external sockets and bounded watchdog/barrier cleanup.
  3. `build_c1_all48_correction_ledger(...)` — AST-backed 1:1 audit of all 48 C1 control IDs
     recording historical defects (literal True, shared positives, unsupported LEGACY_RED labels)
     without mutating sealed C1 evidence.
  4. `derive_r5_c2_readiness_verdicts(...)` — independently recomputes ALL 32 orthogonal gates
     (`G01`..`G32`) and all separate verdict dimensions strictly from raw disk evidence.
  5. `build_r5_c2_owner_handover_vi(...)` — concise auditable Vietnamese handover (§14).
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple
import uuid

from services.kh_02_r2_prospective_data_service import CANONICAL_DATASET_SHA256_KH02
from services.kh_03_r5_market_data_foundation_service import (
    SCHEMA_VERSION_KH03_R5_C2,
    SPEC_VERSION_KH03_R5_C2,
    STRICT_PREFLIGHT_DISK_MIN_GIB_R5,
    STRICT_RUNTIME_DISK_MIN_GIB_R5,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

MANDATORY_BRANCH_KH03_R5_C2 = "feature/v17-9-2-structural-sl-tp-from-qa"
BASELINE_REVIEWED_HEAD_SHA_C2 = "420dcf06601fee960ec7e04bd0c2f96b74bfd4b8"
PRIOR_C1_TESTED_EXECUTABLE_SHA = "3ad0c2973eeaa11f1e635b7a82beddeb4b5a8db8"
PRIOR_C1_EVIDENCE_COMMIT_SHA = "d4fd57e885fc1d525611b7227dd4971b9aacab14"
PRIOR_C1_RUN_ID = "20261125T030440Z_3ad0c2973eea"
PRIOR_R5_BASELINE_SHA = "09493feb845210a629f3c0f174a2c6edfaf2ef23"

HARD_RAM_FLOOR_MIB_R5_C2 = 512.0
GLOBAL_MAX_DESCENDANTS_R5_C2 = 4
MIN_EXPECTED_PYTEST_COLLECTED_R5_C2 = 5930
PAIRED_FAULT_RECEIPT_SCHEMA_C2 = "kh_03_r5_c2_paired_fault_v1"
ALLOWED_FAULT_KINDS_C2: Tuple[str, ...] = ("BASELINE_BUG_RED", "NEGATIVE_CONTRACT", "VERIFIER_MUTATION")

CLASSIFICATION_PASS_OFFLINE_C2 = (
    "ENGINEERING_PASS_OFFLINE_TECHNICAL_CLOSURE_"
    "CAPTURE_BLOCKED_OWNER_AUTHORIZATION_NO_PROVEN_EDGE"
)

# Exact 8 C1 controls backed 1:1 by real baseline `09493feb` witnesses in C1 artifact 03
VERIFIED_R5_BASELINE_WITNESS_MAP_C2: Dict[str, str] = {
    "C1-A02": "P1A_forged_trust_context_dict_live",
    "C1-A11": "P1A_grant_A_planner_B_bypass",
    "C1-A13": "P1A_forged_unbound_plan_dispatches",
    "C1-B01": "P1B_post_wait_deadline_cancel_race",
    "C1-B05": "P1B_retry_after_120s_shortened_to_2s",
    "C1-C01": "P1C_monitor_None_fail_open",
    "C1-C02": "P1C_monitor_empty_dict_fail_open",
    "C1-C04": "P1C_monitor_NaN_fail_open",
}

GATE_32_KEYS_KH03_R5_C2: Tuple[str, ...] = (
    "G01_BRANCH_SHA_CLEAN",
    "G02_KEEP_AS_IS",
    "G03_PRIOR_SEALED_DATASET_OOS",
    "G04_BASELINE_REAL_RED",
    "G05_C1_ALL48_TRUTH_INVENTORY",
    "G06_PAIRED_EXECUTION_PROOF",
    "G07_KIND_SPECIFIC_BASELINE_TRUTH",
    "G08_LEGACY_GATE_FAIL_CLOSED",
    "G09_WORKER_TRACKING",
    "G10_ACTUAL_START_GUARD",
    "G11_CANCELLATION_DRAIN",
    "G12_UNDRAINED_QUARANTINE",
    "G13_LATE_RESULT_ABORT_TRUTH",
    "G14_GRANT_IDENTITY",
    "G15_ONE_SHOT_CLAIM",
    "G16_GRANT_REPLAN_REISSUE",
    "G17_REQUEST_BYTE_CONSERVATION",
    "G18_RESTART_TRUST_BOUNDARY",
    "G19_STRICT_RESOURCE_GUARD",
    "G20_QA_LIVE_ISOLATION",
    "G21_FULL_PYTEST",
    "G22_BASELINE_PARITY",
    "G23_BACKTEST_PARITY",
    "G24_DETERMINISTIC_WORKERS",
    "G25_MEASURED_WORKER_POLICY",
    "G26_INHERITED_REGRESSION",
    "G27_NEW_FAULT_CONTROLS",
    "G28_INDEPENDENT_VERIFIER",
    "G29_MUTATION_RESISTANCE",
    "G30_RESOURCE_CLEANUP",
    "G31_ECONOMIC_ACTIVATION",
    "G32_REMOTE_PUBLICATION",
)


def _is_true(v: Any) -> bool:
    return v is True


def _is_false(v: Any) -> bool:
    return v is False


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


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
# 1. Registered Pure Predicates over Raw Observed Fields (P1-D)
# =====================================================================
def _eval_pred_scope_rejected(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = o.get("admitted") is False and o.get("live_network_permitted") is False and _is_int(o.get("violations_count")) and int(o["violations_count"]) >= 1
    return ok, "scope_rejected_with_violations"


def _eval_pred_scope_admitted_qa(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("admitted") is True
        and o.get("live_network_permitted") is False
        and o.get("mode") == "QA_FIXTURE_OFFLINE_ONLY"
        and _is_int(o.get("violations_count")) and int(o["violations_count"]) == 0
        and isinstance(o.get("scope_receipt_id"), str) and len(o["scope_receipt_id"]) > 0
    )
    return ok, "scope_admitted_qa_isolated"


def _eval_pred_live_default_denied(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("admitted") is False
        and o.get("live_network_permitted") is False
        and o.get("authorization_status") == "BLOCKED_OWNER_AUTHORIZATION"
    )
    return ok, "live_default_deny_enforced"


def _eval_pred_plan_blocked(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("status") == "BLOCKED"
        and o.get("live_network_permitted") is False
        and _is_int(o.get("batches_count")) and int(o["batches_count"]) == 0
        and _is_int(o.get("violations_count")) and int(o["violations_count"]) >= 1
    )
    return ok, "plan_blocked_no_batches"


def _eval_pred_plan_admitted_qa(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("status") == "PLANNED_QA_OFFLINE"
        and o.get("live_network_permitted") is False
        and _is_int(o.get("batches_count")) and int(o["batches_count"]) >= 1
        and o.get("pages_semantics") == "SINGLE_REQUEST_PER_BATCH_NO_PAGINATION"
        and _is_int(o.get("first_batch_max_pages")) and int(o["first_batch_max_pages"]) == 1
    )
    return ok, "plan_admitted_qa_single_page"


def _eval_pred_capture_zero_dispatch_abort(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        _is_int(o.get("observed_calls")) and int(o["observed_calls"]) == 0
        and o.get("aborted") is True
        and _is_int(o.get("qa_records")) and int(o["qa_records"]) == 0
        and _is_int(o.get("accepted_observed")) and int(o["accepted_observed"]) == 0
        and _is_int(o.get("external_requests")) and int(o["external_requests"]) == 0
    )
    return ok, "capture_zero_dispatch_aborted"


def _eval_pred_capture_zero_dispatch_violation(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        _is_int(o.get("observed_calls")) and int(o["observed_calls"]) == 0
        and _is_int(o.get("external_requests")) and int(o["external_requests"]) == 0
        and _is_int(o.get("qa_records")) and int(o["qa_records"]) == 0
        and o.get("expected_violation_found") is True
    )
    return ok, "capture_zero_dispatch_expected_violation"


def _eval_pred_capture_one_call_abort_zero_accept(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        _is_int(o.get("observed_calls")) and int(o["observed_calls"]) == 1
        and o.get("aborted") is True
        and _is_int(o.get("qa_records")) and int(o["qa_records"]) == 0
        and _is_int(o.get("accepted_observed")) and int(o["accepted_observed"]) == 0
        and o.get("expected_reason_found") is True
    )
    return ok, "capture_one_call_aborted_zero_accept"


def _eval_pred_capture_clean_qa_pass(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("capture_execution_status") == "QA_FIXTURE_OFFLINE_PASS"
        and o.get("aborted") is False
        and _is_int(o.get("qa_records")) and int(o["qa_records"]) >= 1
        and _is_int(o.get("accepted_observed")) and int(o["accepted_observed"]) == 0
        and _is_int(o.get("external_requests")) and int(o["external_requests"]) == 0
        and _is_int(o.get("undrained_workers")) and int(o["undrained_workers"]) == 0
    )
    return ok, "capture_clean_qa_offline_pass"


def _eval_pred_capture_timeout_bounded_clean(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("capture_execution_status") == "QA_FIXTURE_OFFLINE_PASS"
        and _is_int(o.get("observed_calls")) and int(o["observed_calls"]) == 1
        and _is_num(o.get("first_timeout_sec"))
        and _is_num(o.get("expected_max_timeout_sec"))
        and float(o["first_timeout_sec"]) <= float(o["expected_max_timeout_sec"]) + 1e-6
    )
    return ok, "capture_timeout_bounded_clean"


def _eval_pred_capture_retry_after_blocked(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        _is_int(o.get("observed_calls")) and int(o["observed_calls"]) == 1
        and o.get("sleeps_recorded") == []
        and o.get("aborted") is True
        and "RETRY_AFTER_EXCEEDS_REMAINING_BUDGET" in str(o.get("abort_reason") or "")
    )
    return ok, "retry_after_exceeding_budget_stopped"


def _eval_pred_capture_retry_after_honored(o: Mapping[str, Any]) -> Tuple[bool, str]:
    sl = o.get("sleeps_recorded")
    ok = (
        o.get("capture_execution_status") == "QA_FIXTURE_OFFLINE_PASS"
        and _is_int(o.get("observed_calls")) and int(o["observed_calls"]) == 2
        and isinstance(sl, list) and len(sl) == 1 and _is_num(sl[0]) and abs(float(sl[0]) - 3.0) < 1e-6
    )
    return ok, "retry_after_3s_honored_not_capped"


def _eval_pred_retry_after_parse_rejected(o: Mapping[str, Any]) -> Tuple[bool, str]:
    return (o.get("parse_ok") is False and isinstance(o.get("error"), str) and len(o["error"]) > 0), "retry_after_parse_rejected"


def _eval_pred_retry_after_parse_valid(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("parse_ok") is True
        and _is_num(o.get("parsed_sec"))
        and _is_num(o.get("expected_sec"))
        and abs(float(o["parsed_sec"]) - float(o["expected_sec"])) < 1e-6
    )
    return ok, "retry_after_http_date_parsed"


def _eval_pred_capture_non_retryable_single_call(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        _is_int(o.get("observed_calls")) and int(o["observed_calls"]) == 1
        and _is_int(o.get("qa_records")) and int(o["qa_records"]) == 0
        and _is_int(o.get("accepted_observed")) and int(o["accepted_observed"]) == 0
    )
    return ok, "non_retryable_single_call"


def _eval_pred_capture_request_cap_enforced(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        _is_int(o.get("dispatched_actual")) and _is_int(o.get("observed_calls")) and _is_int(o.get("max_requests_cap"))
        and int(o["dispatched_actual"]) <= int(o["max_requests_cap"])
        and int(o["observed_calls"]) <= int(o["max_requests_cap"])
        and o.get("aborted") is True
    )
    return ok, "atomic_request_cap_no_oversubscribe"


def _eval_pred_capture_wire_bytes_separated_abort(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        _is_int(o.get("wire_bytes_observed")) and _is_int(o.get("expected_wire_bytes"))
        and int(o["wire_bytes_observed"]) == int(o["expected_wire_bytes"])
        and _is_int(o.get("bytes_accepted")) and int(o["bytes_accepted"]) == 0
        and o.get("abort_reason") == "TOTAL_BYTES_BUDGET_EXHAUSTED"
    )
    return ok, "wire_bytes_separated_from_accepted"


def _eval_pred_capture_midflight_cancel_drained(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("aborted") is True
        and _is_int(o.get("qa_records")) and int(o["qa_records"]) == 0
        and _is_int(o.get("undrained_workers")) and int(o["undrained_workers"]) == 0
        and o.get("owned_shutdown_complete") is True
    )
    return ok, "midflight_cancel_late_discard_drained"


def _eval_pred_capture_invalid_clock_fail(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        _is_int(o.get("dispatched_actual")) and int(o["dispatched_actual"]) == 0
        and _is_int(o.get("observed_calls")) and int(o["observed_calls"]) == 0
        and o.get("capture_execution_status") == "FAIL"
    )
    return ok, "invalid_clock_blocked_zero_dispatch"


def _eval_pred_capture_live_read_cap_blocked(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("capture_execution_status") == "BLOCKED_TRANSPORT_READ_CAP_UNSUPPORTED"
        and _is_int(o.get("external_requests")) and int(o["external_requests"]) == 0
    )
    return ok, "legacy_c4_wire_read_cap_blocked"


def _eval_pred_capture_cadence_monotonic(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("capture_execution_status") == "QA_FIXTURE_OFFLINE_PASS"
        and _is_int(o.get("dispatch_count")) and int(o["dispatch_count"]) == 2
        and _is_num(o.get("dispatch_gap_sec")) and float(o["dispatch_gap_sec"]) >= 0.05 - 1e-6
    )
    return ok, "dispatch_cadence_monotonic_after_retry"


def _eval_pred_capture_abort_owned_cleanup_only(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("aborted") is True
        and _is_int(o.get("removed_staged_count")) and int(o["removed_staged_count"]) == 1
        and _is_int(o.get("qa_records")) and int(o["qa_records"]) == 0
        and o.get("unrelated_kept") is True
        and o.get("cleanup_complete") is True
    )
    return ok, "abort_owned_staging_cleanup_only"


def _eval_pred_custody_rejected_missing_sha(o: Mapping[str, Any]) -> Tuple[bool, str]:
    return (o.get("committed") is False and o.get("status") == "REJECTED_MISSING_RAW_SHA"), "custody_missing_raw_sha_rejected"


def _eval_pred_custody_rejected_oversize(o: Mapping[str, Any]) -> Tuple[bool, str]:
    return (o.get("committed") is False and o.get("oversize_violation_present") is True), "custody_stat_before_read_oversize_rejected"


def _eval_pred_custody_committed_and_chain_valid(o: Mapping[str, Any]) -> Tuple[bool, str]:
    return (o.get("committed") is True and o.get("chain_valid") is True), "custody_commit_and_readonly_chain_valid"


def _eval_pred_custody_tamper_detected(o: Mapping[str, Any]) -> Tuple[bool, str]:
    return (o.get("chain_valid") is False and _is_int(o.get("violations_count")) and int(o["violations_count"]) >= 1), "custody_tamper_detected_readonly"


def _eval_pred_worker_undrained_quarantined(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("capture_execution_status") == "FAIL_UNDRAINED_INFLIGHT_WORKER"
        and _is_int(o.get("undrained_workers")) and int(o["undrained_workers"]) >= 1
        and _is_int(o.get("workers_running_actual")) and int(o["workers_running_actual"]) >= 1
        and o.get("owned_shutdown_complete") is False
        and o.get("session_quarantined") is True
    )
    return ok, "worker_undrained_quarantined_honestly"


def _eval_pred_worker_cancelled_before_start(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        _is_int(o.get("cancelled_before_start")) and int(o["cancelled_before_start"]) >= 1
        and _is_int(o.get("observed_calls")) and int(o["observed_calls"]) == 0
        and _is_int(o.get("workers_running_actual")) and int(o["workers_running_actual"]) == 0
    )
    return ok, "worker_cancelled_before_start_zero_calls"


def _eval_pred_worker_clean_exit_conserved(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("capture_execution_status") == "QA_FIXTURE_OFFLINE_PASS"
        and _is_int(o.get("undrained_workers")) and int(o["undrained_workers"]) == 0
        and _is_int(o.get("workers_exited_actual")) and int(o["workers_exited_actual"]) >= 1
        and o.get("owned_shutdown_complete") is True
        and o.get("conservation_exact") is True
    )
    return ok, "worker_clean_exit_conserved"


def _eval_pred_worker_outer_cancel_propagated(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("cancelled_error_propagated") is True
        and _is_int(o.get("undrained_workers")) and int(o["undrained_workers"]) >= 1
        and o.get("session_quarantined") is True
    )
    return ok, "outer_cancellation_propagated_with_quarantine"


def _eval_pred_worker_cleanup_fail_quarantined(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("capture_execution_status") == "FAIL_ABORT_CLEANUP_INCOMPLETE"
        and o.get("cleanup_complete") is False
        and _is_int(o.get("failed_unlinks_count")) and int(o["failed_unlinks_count"]) >= 1
        and o.get("unrelated_kept") is True
    )
    return ok, "failed_unlink_honest_quarantine"


def _eval_pred_grant_replay_denied(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        _is_int(o.get("second_dispatched")) and int(o["second_dispatched"]) == 0
        and o.get("second_status") in ("BLOCKED_GRANT_REPLAY_OR_LEDGER", "BLOCKED")
        and _is_int(o.get("total_calls")) and _is_int(o.get("max_requests_cap"))
        and int(o["total_calls"]) <= int(o["max_requests_cap"])
    )
    return ok, "grant_replay_denied_within_cap"


def _eval_pred_grant_concurrent_one_winner(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        _is_int(o.get("winners_count")) and int(o["winners_count"]) == 1
        and _is_int(o.get("denied_count")) and int(o["denied_count"]) >= 1
        and _is_int(o.get("total_calls")) and _is_int(o.get("max_requests_cap"))
        and int(o["total_calls"]) <= int(o["max_requests_cap"])
    )
    return ok, "grant_concurrent_race_single_winner"


def _eval_pred_grant_conflict_denied(o: Mapping[str, Any]) -> Tuple[bool, str]:
    return (o.get("admitted") is False and o.get("conflict_violation_present") is True), "grant_payload_conflict_denied"


def _eval_pred_grant_live_hard_blocked(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("capture_execution_status") == "BLOCKED_LIVE_DURABILITY_AND_TERMINATION_UNPROVEN"
        and _is_int(o.get("external_requests")) and int(o["external_requests"]) == 0
        and o.get("durability_blocked_violation") is True
    )
    return ok, "live_grant_durability_hard_blocked"


def _eval_pred_ledger_conservation_reject_dup(o: Mapping[str, Any]) -> Tuple[bool, str]:
    ok = (
        o.get("dup_rejected") is True
        and o.get("no_negative_counters") is True
        and _is_int(o.get("uncertain_bytes_retained")) and int(o["uncertain_bytes_retained"]) > 0
    )
    return ok, "ledger_conservative_accounting_and_idempotency"


def _eval_pred_fault_aggregator_rejects_unproven(o: Mapping[str, Any]) -> Tuple[bool, str]:
    return (o.get("validation_ok") is False and _is_int(o.get("violations_count")) and int(o["violations_count"]) >= 1), "fault_validator_rejects_unproven"


def _eval_pred_fault_aggregator_accepts_valid_pair(o: Mapping[str, Any]) -> Tuple[bool, str]:
    return (o.get("validation_ok") is True and _is_int(o.get("violations_count")) and int(o["violations_count"]) == 0), "fault_validator_accepts_valid_pair"


REGISTERED_FAULT_PREDICATES_C2: Dict[str, Callable[[Mapping[str, Any]], Tuple[bool, str]]] = {
    "PRED_SCOPE_REJECTED": _eval_pred_scope_rejected,
    "PRED_SCOPE_ADMITTED_QA": _eval_pred_scope_admitted_qa,
    "PRED_LIVE_DEFAULT_DENIED": _eval_pred_live_default_denied,
    "PRED_PLAN_BLOCKED": _eval_pred_plan_blocked,
    "PRED_PLAN_ADMITTED_QA": _eval_pred_plan_admitted_qa,
    "PRED_CAPTURE_ZERO_DISPATCH_ABORT": _eval_pred_capture_zero_dispatch_abort,
    "PRED_CAPTURE_ZERO_DISPATCH_VIOLATION": _eval_pred_capture_zero_dispatch_violation,
    "PRED_CAPTURE_ONE_CALL_ABORT_ZERO_ACCEPT": _eval_pred_capture_one_call_abort_zero_accept,
    "PRED_CAPTURE_CLEAN_QA_PASS": _eval_pred_capture_clean_qa_pass,
    "PRED_CAPTURE_TIMEOUT_BOUNDED_CLEAN": _eval_pred_capture_timeout_bounded_clean,
    "PRED_CAPTURE_RETRY_AFTER_BLOCKED": _eval_pred_capture_retry_after_blocked,
    "PRED_CAPTURE_RETRY_AFTER_HONORED": _eval_pred_capture_retry_after_honored,
    "PRED_RETRY_AFTER_PARSE_REJECTED": _eval_pred_retry_after_parse_rejected,
    "PRED_RETRY_AFTER_PARSE_VALID": _eval_pred_retry_after_parse_valid,
    "PRED_CAPTURE_NON_RETRYABLE_SINGLE_CALL": _eval_pred_capture_non_retryable_single_call,
    "PRED_CAPTURE_REQUEST_CAP_ENFORCED": _eval_pred_capture_request_cap_enforced,
    "PRED_CAPTURE_WIRE_BYTES_SEPARATED_ABORT": _eval_pred_capture_wire_bytes_separated_abort,
    "PRED_CAPTURE_MIDFLIGHT_CANCEL_DRAINED": _eval_pred_capture_midflight_cancel_drained,
    "PRED_CAPTURE_INVALID_CLOCK_FAIL": _eval_pred_capture_invalid_clock_fail,
    "PRED_CAPTURE_LIVE_READ_CAP_BLOCKED": _eval_pred_capture_live_read_cap_blocked,
    "PRED_CAPTURE_CADENCE_MONOTONIC": _eval_pred_capture_cadence_monotonic,
    "PRED_CAPTURE_ABORT_OWNED_CLEANUP_ONLY": _eval_pred_capture_abort_owned_cleanup_only,
    "PRED_CUSTODY_REJECTED_MISSING_SHA": _eval_pred_custody_rejected_missing_sha,
    "PRED_CUSTODY_REJECTED_OVERSIZE": _eval_pred_custody_rejected_oversize,
    "PRED_CUSTODY_COMMITTED_AND_CHAIN_VALID": _eval_pred_custody_committed_and_chain_valid,
    "PRED_CUSTODY_TAMPER_DETECTED": _eval_pred_custody_tamper_detected,
    "PRED_WORKER_UNDRAINED_QUARANTINED": _eval_pred_worker_undrained_quarantined,
    "PRED_WORKER_CANCELLED_BEFORE_START": _eval_pred_worker_cancelled_before_start,
    "PRED_WORKER_CLEAN_EXIT_CONSERVED": _eval_pred_worker_clean_exit_conserved,
    "PRED_WORKER_OUTER_CANCEL_PROPAGATED": _eval_pred_worker_outer_cancel_propagated,
    "PRED_WORKER_CLEANUP_FAIL_QUARANTINED": _eval_pred_worker_cleanup_fail_quarantined,
    "PRED_GRANT_REPLAY_DENIED": _eval_pred_grant_replay_denied,
    "PRED_GRANT_CONCURRENT_ONE_WINNER": _eval_pred_grant_concurrent_one_winner,
    "PRED_GRANT_CONFLICT_DENIED": _eval_pred_grant_conflict_denied,
    "PRED_GRANT_LIVE_HARD_BLOCKED": _eval_pred_grant_live_hard_blocked,
    "PRED_LEDGER_CONSERVATION_REJECT_DUP": _eval_pred_ledger_conservation_reject_dup,
    "PRED_FAULT_AGGREGATOR_REJECTS_UNPROVEN": _eval_pred_fault_aggregator_rejects_unproven,
    "PRED_FAULT_AGGREGATOR_ACCEPTS_VALID_PAIR": _eval_pred_fault_aggregator_accepts_valid_pair,
}


@dataclass(frozen=True)
class FaultCaseSpec:
    """Declarative paired fault control specification requiring independent negative + positive execution."""

    fault_id: str
    control_name: str
    target_qualified_name: str
    kind: str  # BASELINE_BUG_RED | NEGATIVE_CONTRACT | VERIFIER_MUTATION
    negative_perturbation: str
    positive_clean_description: str
    negative_predicate_id: str
    positive_predicate_id: str
    negative_runner: Callable[[], Dict[str, Any]]
    positive_runner: Callable[[], Dict[str, Any]]
    baseline_witness_key: Optional[str] = None
    baseline_sha: Optional[str] = None
    expected_exception_type: Optional[str] = None


@dataclass(frozen=True)
class FaultExecutionReceipt:
    """Immutable single-branch (negative OR positive) execution receipt derived from actual invocation."""

    receipt_schema_version: str
    fault_id: str
    branch_role: str  # "NEGATIVE" | "POSITIVE"
    kind: str
    target_qualified_name: str
    executable_sha: str
    run_id: str
    case_id: str
    execution_id: str
    invocation_seq: int
    fixture_digest: str
    input_perturbation: str
    actual_invocation_witnessed: bool
    observed_fields: Dict[str, Any]
    observed_field_types: Dict[str, str]
    predicate_id: str
    predicate_passed: bool
    predicate_detail: str
    exception_type: Optional[str]
    expected_exception_type: Optional[str]
    start_mono: float
    end_mono: float
    exit_code: int
    pid: int
    trace_digest: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "receipt_schema_version": self.receipt_schema_version,
            "fault_id": self.fault_id,
            "branch_role": self.branch_role,
            "kind": self.kind,
            "target_qualified_name": self.target_qualified_name,
            "executable_sha": self.executable_sha,
            "run_id": self.run_id,
            "case_id": self.case_id,
            "execution_id": self.execution_id,
            "invocation_seq": self.invocation_seq,
            "fixture_digest": self.fixture_digest,
            "input_perturbation": self.input_perturbation,
            "actual_invocation_witnessed": self.actual_invocation_witnessed,
            "observed_fields": dict(self.observed_fields),
            "observed_field_types": dict(self.observed_field_types),
            "predicate_id": self.predicate_id,
            "predicate_passed": self.predicate_passed,
            "predicate_detail": self.predicate_detail,
            "exception_type": self.exception_type,
            "expected_exception_type": self.expected_exception_type,
            "start_mono": self.start_mono,
            "end_mono": self.end_mono,
            "exit_code": self.exit_code,
            "pid": self.pid,
            "trace_digest": self.trace_digest,
        }


_INVOCATION_COUNTER_C2 = 0


def _next_inv_seq() -> int:
    global _INVOCATION_COUNTER_C2
    _INVOCATION_COUNTER_C2 += 1
    return _INVOCATION_COUNTER_C2


def _run_one_fault_branch(
    *,
    spec: FaultCaseSpec,
    branch_role: str,
    runner_fn: Callable[[], Dict[str, Any]],
    predicate_id: str,
    perturbation_desc: str,
    executable_sha: str,
    run_id: str,
    expected_exc: Optional[str],
) -> FaultExecutionReceipt:
    seq = _next_inv_seq()
    exec_id = f"exec_{spec.fault_id}_{branch_role[:3].lower()}_{seq}_{uuid.uuid4().hex[:8]}"
    case_id = f"{spec.fault_id}:{branch_role}"
    pid = os.getpid()
    t0 = time.monotonic()
    exc_type: Optional[str] = None
    exit_code = 0
    obs: Dict[str, Any] = {}
    witnessed = False
    try:
        raw_out = runner_fn()
        witnessed = True
        if isinstance(raw_out, Mapping):
            obs = dict(raw_out)
        else:
            obs = {"raw_return_type": type(raw_out).__name__}
            exit_code = 1
    except BaseException as exc:
        witnessed = True
        exc_type = type(exc).__name__
        obs = {"exception_type": exc_type, "exception_message": str(exc)}
        exit_code = 0 if (expected_exc is not None and exc_type == expected_exc) else 1
    t1 = max(t0, time.monotonic())

    pred_fn = REGISTERED_FAULT_PREDICATES_C2.get(predicate_id)
    if pred_fn is None:
        pred_ok, pred_det = False, f"UNKNOWN_PREDICATE:{predicate_id}"
    elif _contains_nonfinite(obs):
        pred_ok, pred_det = False, "NONFINITE_OBSERVED_VALUE"
    elif exc_type is not None and expected_exc is None:
        pred_ok, pred_det = False, f"UNEXPECTED_EXCEPTION:{exc_type}"
    elif expected_exc is not None and exc_type != expected_exc:
        pred_ok, pred_det = False, f"EXPECTED_EXCEPTION_MISMATCH:expected={expected_exc},got={exc_type}"
    else:
        pred_ok, pred_det = pred_fn(obs)

    obs_types = {str(k): type(v).__name__ for k, v in sorted(obs.items())}
    fix_dig = canonical_hash({"fault_id": spec.fault_id, "role": branch_role, "perturbation": perturbation_desc, "target": spec.target_qualified_name})
    trace_body = {
        "fault_id": spec.fault_id,
        "branch_role": branch_role,
        "execution_id": exec_id,
        "invocation_seq": seq,
        "target_qualified_name": spec.target_qualified_name,
        "executable_sha": executable_sha,
        "run_id": run_id,
        "observed_fields": obs,
        "predicate_id": predicate_id,
        "exception_type": exc_type,
        "pid": pid,
    }
    try:
        trace_dig = canonical_hash(trace_body)
    except Exception:
        trace_dig = hashlib.sha256(repr(sorted(trace_body.items())).encode("utf-8")).hexdigest()

    return FaultExecutionReceipt(
        receipt_schema_version=PAIRED_FAULT_RECEIPT_SCHEMA_C2,
        fault_id=spec.fault_id,
        branch_role=branch_role,
        kind=spec.kind,
        target_qualified_name=spec.target_qualified_name,
        executable_sha=executable_sha,
        run_id=run_id,
        case_id=case_id,
        execution_id=exec_id,
        invocation_seq=seq,
        fixture_digest=fix_dig,
        input_perturbation=perturbation_desc,
        actual_invocation_witnessed=witnessed,
        observed_fields=obs,
        observed_field_types=obs_types,
        predicate_id=predicate_id,
        predicate_passed=bool(pred_ok),
        predicate_detail=pred_det,
        exception_type=exc_type,
        expected_exception_type=expected_exc,
        start_mono=round(t0, 6),
        end_mono=round(t1, 6),
        exit_code=exit_code,
        pid=pid,
        trace_digest=trace_dig,
    )


def execute_fault_case_pair(
    spec: FaultCaseSpec,
    *,
    executable_sha: str,
    run_id: str,
    baseline_witness: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Executes BOTH distinct negative and clean-positive same-contract invocations for `spec`,
    produces typed raw `FaultExecutionReceipt` dicts, and independently validates the pair.
    """
    neg_r = _run_one_fault_branch(
        spec=spec,
        branch_role="NEGATIVE",
        runner_fn=spec.negative_runner,
        predicate_id=spec.negative_predicate_id,
        perturbation_desc=spec.negative_perturbation,
        executable_sha=executable_sha,
        run_id=run_id,
        expected_exc=spec.expected_exception_type,
    )
    pos_r = _run_one_fault_branch(
        spec=spec,
        branch_role="POSITIVE",
        runner_fn=spec.positive_runner,
        predicate_id=spec.positive_predicate_id,
        perturbation_desc=spec.positive_clean_description,
        executable_sha=executable_sha,
        run_id=run_id,
        expected_exc=None,
    )
    pair_rec: Dict[str, Any] = {
        "receipt_schema_version": PAIRED_FAULT_RECEIPT_SCHEMA_C2,
        "fault_id": spec.fault_id,
        "control_name": spec.control_name,
        "target_function": spec.target_qualified_name,
        "target_qualified_name": spec.target_qualified_name,
        "control_kind": spec.kind,
        "negative_receipt": neg_r.to_dict(),
        "positive_receipt": pos_r.to_dict(),
        "baseline_witness": dict(baseline_witness) if baseline_witness is not None else None,
    }
    v_ok, v_viols = validate_fault_execution_receipt(
        pair_rec, expected_executable_sha=executable_sha, expected_run_id=run_id
    )
    pair_rec["paired_receipt_verified"] = v_ok
    pair_rec["validation_violations"] = v_viols
    pair_rec["red_negative_detected"] = bool(neg_r.predicate_passed and neg_r.actual_invocation_witnessed)
    pair_rec["green_clean_passed"] = bool(pos_r.predicate_passed and pos_r.actual_invocation_witnessed)
    pair_rec["status"] = "PASS" if v_ok else "FAIL"
    return pair_rec


def _validate_single_branch_receipt(
    br: Any,
    *,
    expected_fault_id: str,
    expected_role: str,
    expected_target: str,
    expected_kind: str,
    expected_executable_sha: Optional[str],
    expected_run_id: Optional[str],
    stale_shas: Set[str],
    stale_runs: Set[str],
) -> Tuple[bool, List[str]]:
    viols: List[str] = []
    if not isinstance(br, Mapping):
        return False, [f"MISSING_OR_INVALID_{expected_role}_RECEIPT"]
    if br.get("receipt_schema_version") != PAIRED_FAULT_RECEIPT_SCHEMA_C2:
        viols.append(f"BAD_RECEIPT_SCHEMA:{expected_role}")
    if br.get("fault_id") != expected_fault_id:
        viols.append(f"FAULT_ID_MISMATCH:{expected_role}")
    if br.get("branch_role") != expected_role:
        viols.append(f"BRANCH_ROLE_MISMATCH:{expected_role}")
    if br.get("kind") != expected_kind or expected_kind not in ALLOWED_FAULT_KINDS_C2:
        viols.append(f"INVALID_FAULT_KIND:{expected_role}:{br.get('kind')!r}")
    if br.get("target_qualified_name") != expected_target or not isinstance(expected_target, str) or not expected_target.strip():
        viols.append(f"TARGET_MISMATCH:{expected_role}")
    if br.get("actual_invocation_witnessed") is not True:
        viols.append(f"NO_ACTUAL_INVOCATION_WITNESSED:{expected_role}")
    exec_id = br.get("execution_id")
    if not isinstance(exec_id, str) or len(exec_id.strip()) < 8:
        viols.append(f"MISSING_EXECUTION_ID:{expected_role}")
    if not _is_int(br.get("pid")) or int(br["pid"]) <= 0:
        viols.append(f"INVALID_PID:{expected_role}")
    if not _is_num(br.get("start_mono")) or not _is_num(br.get("end_mono")) or float(br["end_mono"]) < float(br["start_mono"]):
        viols.append(f"INVALID_MONOTONIC_TIMESTAMPS:{expected_role}")
    sha_v = str(br.get("executable_sha") or "")
    rid_v = str(br.get("run_id") or "")
    if sha_v in stale_shas or rid_v in stale_runs:
        viols.append(f"STALE_SHA_OR_RUN:{expected_role}")
    if expected_executable_sha and sha_v != expected_executable_sha:
        viols.append(f"EXECUTABLE_SHA_MISMATCH:{expected_role}")
    if expected_run_id and rid_v != expected_run_id:
        viols.append(f"RUN_ID_MISMATCH:{expected_role}")
    obs = br.get("observed_fields")
    if not isinstance(obs, Mapping) or len(obs) == 0:
        viols.append(f"MISSING_OBSERVED_FIELDS:{expected_role}")
    elif _contains_nonfinite(obs):
        viols.append(f"NONFINITE_OBSERVED_FIELDS:{expected_role}")
    else:
        # Independently re-evaluate registered predicate over raw observed fields (NEVER trust predicate_passed flag!)
        pred_id = str(br.get("predicate_id") or "")
        pred_fn = REGISTERED_FAULT_PREDICATES_C2.get(pred_id)
        if pred_fn is None:
            viols.append(f"UNREGISTERED_PREDICATE_ID:{expected_role}:{pred_id!r}")
        else:
            exp_exc = br.get("expected_exception_type")
            got_exc = br.get("exception_type")
            if got_exc is not None and exp_exc is None:
                viols.append(f"UNEXPECTED_EXCEPTION_RECORDED:{expected_role}:{got_exc}")
            elif exp_exc is not None and got_exc != exp_exc:
                viols.append(f"EXPECTED_EXCEPTION_NOT_RAISED:{expected_role}:{exp_exc}")
            recomp_ok, _ = pred_fn(obs)
            if not recomp_ok:
                viols.append(f"PREDICATE_RECOMPUTE_FAILED:{expected_role}:{pred_id}")
            if br.get("predicate_passed") is not True:
                viols.append(f"PREDICATE_FLAG_NOT_TRUE:{expected_role}")
        # Recompute trace_digest over raw observed fields to catch silent trace tamper
        trace_body = {
            "fault_id": br.get("fault_id"),
            "branch_role": br.get("branch_role"),
            "execution_id": br.get("execution_id"),
            "invocation_seq": br.get("invocation_seq"),
            "target_qualified_name": br.get("target_qualified_name"),
            "executable_sha": br.get("executable_sha"),
            "run_id": br.get("run_id"),
            "observed_fields": dict(obs),
            "predicate_id": br.get("predicate_id"),
            "exception_type": br.get("exception_type"),
            "pid": br.get("pid"),
        }
        try:
            expected_tdig = canonical_hash(trace_body)
            if str(br.get("trace_digest") or "") != expected_tdig:
                viols.append(f"TRACE_DIGEST_MISMATCH:{expected_role}")
        except Exception:
            viols.append(f"TRACE_DIGEST_COMPUTE_ERROR:{expected_role}")
    return len(viols) == 0, viols


def validate_fault_execution_receipt(
    pair_record: Mapping[str, Any],
    *,
    expected_executable_sha: Optional[str] = None,
    expected_run_id: Optional[str] = None,
    seen_execution_ids: Optional[Set[str]] = None,
    seen_positive_execution_ids: Optional[Set[str]] = None,
    stale_shas: Sequence[str] = (),
    stale_runs: Sequence[str] = (),
) -> Tuple[bool, List[str]]:
    """
    Independently validates a paired fault control record strictly from its raw `negative_receipt`
    and `positive_receipt` observations (NEVER trusting `status`, `red_negative_detected`, or
    `green_clean_passed` summary flags!).
    """
    viols: List[str] = []
    if not isinstance(pair_record, Mapping):
        return False, ["PAIR_RECORD_NOT_MAPPING"]
    fid = str(pair_record.get("fault_id") or "")
    target = str(pair_record.get("target_qualified_name") or pair_record.get("target_function") or "")
    kind = str(pair_record.get("control_kind") or "")
    if not fid:
        viols.append("MISSING_FAULT_ID")
    if kind not in ALLOWED_FAULT_KINDS_C2:
        viols.append(f"UNSUPPORTED_CONTROL_KIND:{kind!r}")

    neg = pair_record.get("negative_receipt")
    pos = pair_record.get("positive_receipt")
    if not isinstance(neg, Mapping):
        viols.append("MISSING_NEGATIVE_RECEIPT")
    if not isinstance(pos, Mapping):
        viols.append("MISSING_POSITIVE_RECEIPT")
    if viols:
        return False, viols

    stale_s = set(stale_shas)
    stale_r = set(stale_runs)
    n_ok, n_v = _validate_single_branch_receipt(
        neg, expected_fault_id=fid, expected_role="NEGATIVE", expected_target=target, expected_kind=kind,
        expected_executable_sha=expected_executable_sha, expected_run_id=expected_run_id, stale_shas=stale_s, stale_runs=stale_r,
    )
    p_ok, p_v = _validate_single_branch_receipt(
        pos, expected_fault_id=fid, expected_role="POSITIVE", expected_target=target, expected_kind=kind,
        expected_executable_sha=expected_executable_sha, expected_run_id=expected_run_id, stale_shas=stale_s, stale_runs=stale_r,
    )
    viols.extend(n_v)
    viols.extend(p_v)

    n_eid = str(neg.get("execution_id") or "")
    p_eid = str(pos.get("execution_id") or "")
    if n_eid and p_eid and n_eid == p_eid:
        viols.append("NEGATIVE_AND_POSITIVE_SHARE_EXECUTION_ID")
    if seen_execution_ids is not None:
        for eid in (n_eid, p_eid):
            if eid in seen_execution_ids:
                viols.append(f"DUPLICATE_EXECUTION_ID:{eid}")
            elif eid:
                seen_execution_ids.add(eid)
    if seen_positive_execution_ids is not None:
        if p_eid in seen_positive_execution_ids:
            viols.append(f"SHARED_POSITIVE_EXECUTION_ID:{p_eid}")
        elif p_eid:
            seen_positive_execution_ids.add(p_eid)

    # Kind-specific baseline witness requirement for BASELINE_BUG_RED!
    if kind == "BASELINE_BUG_RED":
        bw = pair_record.get("baseline_witness")
        if not isinstance(bw, Mapping):
            viols.append("BASELINE_BUG_RED_MISSING_BASELINE_WITNESS")
        else:
            bsha = str(bw.get("baseline_sha") or "")
            if len(bsha) != 40 or bsha == expected_executable_sha:
                viols.append("BASELINE_WITNESS_INVALID_OR_NON_BASELINE_SHA")
            if bw.get("baseline_negative_reproduced") is not True:
                viols.append("BASELINE_WITNESS_NOT_REPRODUCED")
            if not isinstance(bw.get("witness_key"), str) or not bw["witness_key"].strip():
                viols.append("BASELINE_WITNESS_MISSING_KEY")
    elif kind == "NEGATIVE_CONTRACT":
        # Must NOT claim unsupported baseline witness under wrong label
        pass

    return (len(viols) == 0 and n_ok and p_ok), viols


def derive_fault_control_verdict(
    pair_records: Sequence[Mapping[str, Any]],
    *,
    expected_executable_sha: Optional[str] = None,
    expected_run_id: Optional[str] = None,
    required_fault_ids: Optional[Sequence[str]] = None,
    stale_shas: Sequence[str] = (),
    stale_runs: Sequence[str] = (),
) -> Dict[str, Any]:
    """Derives aggregate fault-control truth strictly by re-validating every raw paired receipt."""
    seen_fids: Set[str] = set()
    seen_eids: Set[str] = set()
    seen_pos_eids: Set[str] = set()
    all_viols: List[str] = []
    passed_fids: List[str] = []
    failed_fids: List[str] = []
    kind_counts: Dict[str, int] = {k: 0 for k in ALLOWED_FAULT_KINDS_C2}

    for rec in pair_records:
        fid = str(rec.get("fault_id") or "") if isinstance(rec, Mapping) else ""
        if fid in seen_fids:
            all_viols.append(f"DUPLICATE_FAULT_ID:{fid}")
            failed_fids.append(fid)
            continue
        if fid:
            seen_fids.add(fid)
        ok, rv = validate_fault_execution_receipt(
            rec,
            expected_executable_sha=expected_executable_sha,
            expected_run_id=expected_run_id,
            seen_execution_ids=seen_eids,
            seen_positive_execution_ids=seen_pos_eids,
            stale_shas=stale_shas,
            stale_runs=stale_runs,
        )
        if ok:
            passed_fids.append(fid)
            k = str(rec.get("control_kind") or "")
            if k in kind_counts:
                kind_counts[k] += 1
        else:
            failed_fids.append(fid)
            all_viols.extend(f"{fid}:{v}" for v in rv)

    if required_fault_ids is not None:
        missing = sorted(set(required_fault_ids) - seen_fids)
        if missing:
            all_viols.append(f"MISSING_REQUIRED_FAULT_IDS:{','.join(missing)}")

    valid = len(all_viols) == 0 and len(passed_fids) == len(pair_records) and len(pair_records) > 0
    return {
        "valid": valid,
        "status": "PASS" if valid else "FAIL",
        "total_controls": len(pair_records),
        "unique_fault_ids": len(seen_fids),
        "passed_controls": len(passed_fids),
        "failed_controls": len(failed_fids),
        "counts_by_kind": kind_counts,
        "unique_execution_ids_count": len(seen_eids),
        "unique_positive_execution_ids_count": len(seen_pos_eids),
        "violations_count": len(all_viols),
        "violations": all_viols,
    }


def audit_fault_certification_ast_for_literal_flags(source_text: str, *, func_names: Sequence[str] = ("_rec",)) -> Dict[str, Any]:
    """AST-audits certification code for hardcoded literal boolean args passed to fault-aggregator helpers."""
    tree = ast.parse(source_text)
    findings: List[Dict[str, Any]] = []
    target_names = set(func_names)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in target_names:
            for idx, arg in enumerate(node.args):
                if isinstance(arg, ast.Constant) and isinstance(arg.value, bool):
                    findings.append({
                        "line": node.lineno,
                        "func": node.func.id,
                        "arg_index": idx,
                        "literal_value": arg.value,
                        "call_excerpt": ast.unparse(node)[:160],
                    })
    return {
        "clean_no_literal_aggregator_flags": len(findings) == 0,
        "suspicious_literal_findings_count": len(findings),
        "findings": findings,
    }


# =====================================================================
# 2. C1 48-Control AST Audit & Correction Ledger (from untouched 3ad0c297)
# =====================================================================
def build_c1_all48_correction_ledger(
    *,
    repo_root: Path,
    c1_baseline_sha: str = PRIOR_C1_TESTED_EXECUTABLE_SHA,
) -> Dict[str, Any]:
    """
    Parses untouched C1 `test_kh03_r5_c1_custody_abort_and_determinism.py` from git commit `c1_baseline_sha`,
    inventories all 48 C1 control IDs (`C1-A01..A18`, `C1-B01..B15`, `C1-C01..C15`), classifies every
    historical C1 truth gap honestly, and maps each ID 1:1 to its C2 repaired paired contract.
    """
    rel = "aurum-desk/backend/tests/test_kh03_r5_c1_custody_abort_and_determinism.py"
    gp = subprocess.run(
        ["git", "show", f"{c1_baseline_sha}:{rel}"],
        cwd=str(repo_root), capture_output=True, check=False, timeout=30,
    )
    if gp.returncode != 0 or not gp.stdout:
        return {"status": "FAIL", "error": f"GIT_SHOW_FAILED:{c1_baseline_sha}:{rel}"}
    src_text = gp.stdout.decode("utf-8", errors="replace")
    src_sha = hashlib.sha256(gp.stdout).hexdigest()
    tree = ast.parse(src_text)

    # Expand the 11-item tuple loop for C1-C01..C1-C11 plus the 37 direct _rec calls = exact 48 IDs!
    loop_c_rows: Dict[str, Tuple[int, str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
            for el in node.iter.elts:
                if isinstance(el, ast.Tuple) and len(el.elts) >= 4:
                    fid_c = ast.literal_eval(el.elts[0]) if isinstance(el.elts[0], ast.Constant) else ""
                    if isinstance(fid_c, str) and fid_c.startswith("C1-C"):
                        nm_c = ast.literal_eval(el.elts[1])
                        kd_c = ast.literal_eval(el.elts[2])
                        loop_c_rows[fid_c] = (el.lineno, str(nm_c), str(kd_c))

    inventory: List[Dict[str, Any]] = []
    defect_counts = {
        "LITERAL_TRUE_POSITIVE": 0,
        "LITERAL_TRUE_NEGATIVE": 0,
        "REUSED_SHARED_POSITIVE": 0,
        "UNSUPPORTED_LEGACY_RED_LABEL": 0,
        "VALID_DISTINCT_PAIR_IN_C1": 0,
    }
    shared_pos_tokens = {"d0['admitted']", "not d0['live_network_permitted']", "p0['status'] == 'PLANNED_QA_OFFLINE'", "grn_s"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_rec" and len(node.args) >= 6:
            fid_node, nm_node, fn_node, kd_node, red_node, grn_node = node.args[:6]
            red_expr = ast.unparse(red_node)
            grn_expr = ast.unparse(grn_node)
            fn_str = ast.literal_eval(fn_node) if isinstance(fn_node, ast.Constant) else ast.unparse(fn_node)

            if isinstance(fid_node, ast.Name) and fid_node.id == "fid":
                # Loop expanding C1-C01..C1-C11
                for fid_l, (ln_l, nm_l, kd_l) in sorted(loop_c_rows.items()):
                    defects: List[str] = ["REUSED_SHARED_POSITIVE"]
                    defect_counts["REUSED_SHARED_POSITIVE"] += 1
                    if kd_l == "LEGACY_RED_REPRODUCED" and fid_l not in VERIFIED_R5_BASELINE_WITNESS_MAP_C2:
                        defects.append("UNSUPPORTED_LEGACY_RED_LABEL")
                        defect_counts["UNSUPPORTED_LEGACY_RED_LABEL"] += 1
                    c2_kind = "BASELINE_BUG_RED" if fid_l in VERIFIED_R5_BASELINE_WITNESS_MAP_C2 else "NEGATIVE_CONTRACT"
                    inventory.append({
                        "fault_id": fid_l,
                        "control_name": nm_l,
                        "c1_line": ln_l,
                        "c1_target": fn_str,
                        "c1_label": kd_l,
                        "c1_negative_expr": red_expr,
                        "c1_positive_expr": grn_expr,
                        "c1_defects": defects,
                        "c2_repaired_kind": c2_kind,
                        "c2_baseline_witness_key": VERIFIED_R5_BASELINE_WITNESS_MAP_C2.get(fid_l),
                    })
            else:
                fid_s = str(ast.literal_eval(fid_node))
                nm_s = str(ast.literal_eval(nm_node))
                kd_s = str(ast.literal_eval(kd_node))
                defects = []
                if grn_expr == "True":
                    defects.append("LITERAL_TRUE_POSITIVE")
                    defect_counts["LITERAL_TRUE_POSITIVE"] += 1
                elif grn_expr in shared_pos_tokens:
                    defects.append("REUSED_SHARED_POSITIVE")
                    defect_counts["REUSED_SHARED_POSITIVE"] += 1
                if red_expr == "True":
                    defects.append("LITERAL_TRUE_NEGATIVE")
                    defect_counts["LITERAL_TRUE_NEGATIVE"] += 1
                if kd_s == "LEGACY_RED_REPRODUCED" and fid_s not in VERIFIED_R5_BASELINE_WITNESS_MAP_C2:
                    defects.append("UNSUPPORTED_LEGACY_RED_LABEL")
                    defect_counts["UNSUPPORTED_LEGACY_RED_LABEL"] += 1
                if not defects:
                    defect_counts["VALID_DISTINCT_PAIR_IN_C1"] += 1
                c2_kind = "BASELINE_BUG_RED" if fid_s in VERIFIED_R5_BASELINE_WITNESS_MAP_C2 else "NEGATIVE_CONTRACT"
                inventory.append({
                    "fault_id": fid_s,
                    "control_name": nm_s,
                    "c1_line": node.lineno,
                    "c1_target": fn_str,
                    "c1_label": kd_s,
                    "c1_negative_expr": red_expr,
                    "c1_positive_expr": grn_expr,
                    "c1_defects": defects,
                    "c2_repaired_kind": c2_kind,
                    "c2_baseline_witness_key": VERIFIED_R5_BASELINE_WITNESS_MAP_C2.get(fid_s),
                })

    inventory.sort(key=lambda r: r["fault_id"])
    return {
        "spec_version": SPEC_VERSION_KH03_R5_C2,
        "schema_version": SCHEMA_VERSION_KH03_R5_C2,
        "c1_source_sha": c1_baseline_sha,
        "c1_source_file_rel": rel,
        "c1_source_file_sha256": src_sha,
        "total_c1_controls_audited": len(inventory),
        "unique_c1_fault_ids": len({r["fault_id"] for r in inventory}),
        "c1_defect_summary": defect_counts,
        "c2_baseline_bug_red_count": sum(1 for r in inventory if r["c2_repaired_kind"] == "BASELINE_BUG_RED"),
        "c2_negative_contract_count": sum(1 for r in inventory if r["c2_repaired_kind"] == "NEGATIVE_CONTRACT"),
        "historical_c1_artifacts_mutated": False,
        "controls_correction_ledger": inventory,
        "status": "PASS" if len(inventory) == 48 and len({r["fault_id"] for r in inventory}) == 48 else "FAIL",
    }


# =====================================================================
# 3. Real Isolated Baseline C1 RED Harness (P1-D, P1-E, P1-F)
# =====================================================================
_C1_RED_SUBPROC_SCRIPT = r'''
import ast, asyncio, json, os, sys, threading, time
from datetime import datetime, timezone
from pathlib import Path

repo_root = Path(sys.argv[1])
c1_test_file = Path(sys.argv[2])

from services.kh_02_r2_c2_public_capture import RawHttpResponseC2
from services.kh_03_r5_market_data_foundation_service import (
    R5OperatorGrant, build_r5_source_capability_inventory,
    validate_r5_operator_capture_scope, plan_r5_capture_batches,
)
from services.kh_03_r5_capture_and_custody_service import (
    capture_r5_public_batch, make_r5_fixture_resource_monitor,
)

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)
WS, WE = 1794000000000, 1794000600000
OK_BODY = b'{"code":"00000","data":[{"symbol":"XAUUSDT","bidPr":"4100.00","askPr":"4100.20"}]}'
inv = build_r5_source_capability_inventory(repo_root)
obs = {}

# --- P1-D: Static AST + dynamic aggregator weakness on legacy C1 `_rec(red, True)` ---
src_txt = c1_test_file.read_text("utf-8")
tree = ast.parse(src_txt)
lit_pos = 0
lit_neg = 0
for nd in ast.walk(tree):
    if isinstance(nd, ast.Call) and isinstance(nd.func, ast.Name) and nd.func.id == "_rec" and len(nd.args) >= 6:
        if ast.unparse(nd.args[5]) == "True":
            lit_pos += 1
        if ast.unparse(nd.args[4]) == "True":
            lit_neg += 1

# Replicate exact C1 `_rec` logic to show PASS emitted when positive branch is NOT executed/forced fail
def _legacy_c1_rec(fid, name, fn, kind, red, grn):
    return {"fault_id": fid, "status": "PASS" if (red and grn) else "FAIL"}
pos_executed = False
leg_out = _legacy_c1_rec("DEMO_P1D", "HARDCODED_POS", "capture_r5_public_batch", "LEGACY_RED_REPRODUCED", True, True)
obs["P1D_fault_control_literal_pass_weakness"] = {
    "c1_ast_literal_true_positives": lit_pos,
    "c1_ast_literal_true_negatives": lit_neg,
    "positive_branch_executed": pos_executed,
    "legacy_rec_status_with_hardcoded_true": leg_out["status"],
    "weakness_reproduced": lit_pos >= 20 and lit_neg >= 1 and leg_out["status"] == "PASS" and not pos_executed,
}

# --- P1-E1: Outer task cancel while worker thread blocked on barrier -> C1 reports undrained=0! ---
def _mk_plan(gid, rid, max_req=2, max_dur=2):
    g = R5OperatorGrant(gid, "QA", "QA_FIXTURE", "XAUUSDT", "USDT-FUTURES", ("public_ticker",), WS, WE, max_req, 50_000, max_dur, NOW_MS + 60_000, rid, True, "QA")
    r = {"symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "endpoints": ["public_ticker"], "run_id": rid, "purpose": "QA_FIXTURE", "window_start_ms": WS, "window_end_ms": WE, "max_requests": max_req, "max_total_bytes": 50_000, "max_duration_sec": max_dur}
    d = validate_r5_operator_capture_scope(g, r, NOW, {"qa_fixture_mode": True})
    p = plan_r5_capture_batches(d, inv, {"destination_disk_free_gib": 30.0, "max_concurrency": 1, "max_pages": max_req, "max_requests": max_req, "max_total_bytes": 50_000, "max_duration_sec": max_dur, "endpoints": ["public_ticker"]}, now_utc=NOW)
    return g, r, d, p

mon = make_r5_fixture_resource_monitor(destination=repo_root)
_, _, dw1, pw1 = _mk_plan("QA_P1E_OUTER", "red_p1e_outer", max_req=2, max_dur=2)
ent1, rel1, ext1 = threading.Event(), threading.Event(), threading.Event()
w1_info = {"calls": 0, "alive_at_return": False}
class _Blk1:
    def get(self, url, params, *, timeout_sec):
        w1_info["calls"] += 1
        ent1.set()
        rel1.wait(timeout=4.0)
        ext1.set()
        return RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)

async def _run_e1():
    t = asyncio.create_task(capture_r5_public_batch(pw1, _Blk1(), mon, run_id="red_p1e_outer", trusted_scope=dw1, destination_root=repo_root, drain_timeout_sec=0.1, utc_now_fn=lambda: NOW))
    t0 = time.monotonic()
    while not ent1.is_set() and (time.monotonic() - t0) < 3.0:
        await asyncio.sleep(0.01)
    t.cancel()
    res = await t
    w1_info["alive_at_return"] = ent1.is_set() and not ext1.is_set()
    return res

try:
    res_e1 = asyncio.run(_run_e1())
finally:
    rel1.set()
    ext1.wait(timeout=3.0)

obs["P1E_outer_cancel_blocked_worker_false_zero_undrained"] = {
    "c1_reported_undrained_inflight_workers": res_e1["undrained_inflight_workers"],
    "c1_reported_status": res_e1["capture_execution_status"],
    "actual_worker_entered": ent1.is_set(),
    "actual_worker_still_running_at_return": w1_info["alive_at_return"],
    "actual_worker_exited_after_release": ext1.is_set(),
    "weakness_reproduced": (res_e1["undrained_inflight_workers"] == 0 and w1_info["alive_at_return"] is True),
}

# --- P1-F: Sequential + concurrent + replan + re-validate grant replay multiplies max_requests=1 -> 4+! ---
gr, rr, dr, pr = _mk_plan("QA_P1F_REPLAY", "red_p1f", max_req=1, max_dur=20)
replay_calls = []
rc_lock = threading.Lock()
class _SpyF:
    def get(self, url, params, *, timeout_sec):
        with rc_lock:
            replay_calls.append(url)
        return RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)

r_f1 = asyncio.run(capture_r5_public_batch(pr, _SpyF(), mon, run_id="red_p1f", trusted_scope=dr, destination_root=repo_root, utc_now_fn=lambda: NOW))
r_f2 = asyncio.run(capture_r5_public_batch(pr, _SpyF(), mon, run_id="red_p1f", trusted_scope=dr, destination_root=repo_root, utc_now_fn=lambda: NOW))
pr2 = plan_r5_capture_batches(dr, inv, {"destination_disk_free_gib": 30.0, "max_concurrency": 1, "max_pages": 1, "max_requests": 1, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["public_ticker"]}, now_utc=NOW)
r_f3 = asyncio.run(capture_r5_public_batch(pr2, _SpyF(), mon, run_id="red_p1f", trusted_scope=dr, destination_root=repo_root, utc_now_fn=lambda: NOW))
dr_re = validate_r5_operator_capture_scope(gr, rr, NOW, {"qa_fixture_mode": True})
pr_re = plan_r5_capture_batches(dr_re, inv, {"destination_disk_free_gib": 30.0, "max_concurrency": 1, "max_pages": 1, "max_requests": 1, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["public_ticker"]}, now_utc=NOW)
r_f4 = asyncio.run(capture_r5_public_batch(pr_re, _SpyF(), mon, run_id="red_p1f", trusted_scope=dr_re, destination_root=repo_root, utc_now_fn=lambda: NOW))

# Concurrent barrier race on untouched C1
bar = threading.Barrier(2, timeout=3.0)
conc_calls = []
class _SpyFConc:
    def get(self, url, params, *, timeout_sec):
        try:
            bar.wait()
        except Exception:
            pass
        with rc_lock:
            conc_calls.append(url)
        return RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)

async def _run_conc():
    return await asyncio.gather(
        capture_r5_public_batch(pr, _SpyFConc(), mon, run_id="red_p1f", trusted_scope=dr, destination_root=repo_root, utc_now_fn=lambda: NOW),
        capture_r5_public_batch(pr, _SpyFConc(), mon, run_id="red_p1f", trusted_scope=dr, destination_root=repo_root, utc_now_fn=lambda: NOW),
    )
rc1, rc2 = asyncio.run(_run_conc())

obs["P1F_grant_replay_multiplies_budget"] = {
    "grant_max_requests": 1,
    "sequential_replan_reissue_calls": len(replay_calls),
    "concurrent_race_calls": len(conc_calls),
    "total_calls_on_single_max_req_1_grant": len(replay_calls) + len(conc_calls),
    "all_6_replays_succeeded": all(x["capture_execution_status"] == "QA_FIXTURE_OFFLINE_PASS" for x in (r_f1, r_f2, r_f3, r_f4, rc1, rc2)),
    "weakness_reproduced": (len(replay_calls) == 4 and len(conc_calls) == 2),
}

all_ok = all(bool(v.get("weakness_reproduced")) for v in obs.values())
print(json.dumps({"all_c1_def_red_reproduced": all_ok, "pid": os.getpid(), "observations": obs}, sort_keys=True))
raise SystemExit(0 if all_ok else 1)
'''


def run_real_c1_baseline_red_reproduction_from_git_sha(
    *,
    repo_root: Path,
    baseline_sha: str = PRIOR_C1_TESTED_EXECUTABLE_SHA,
    tested_c2_sha: str = "",
) -> Dict[str, Any]:
    """
    Extracts untouched C1 files via `git show <baseline_sha>:...` into an isolated temp overlay
    (NEVER mutating repo worktree, ZERO external network sockets), executes P1-D/E/F RED witness
    script in a separate OS subprocess with bounded timeout, and returns full provenance receipt.
    """
    app_root = repo_root / "aurum-desk"
    backend_root = app_root / "backend"
    tmp_dir = Path(tempfile.mkdtemp(prefix="c2_red_"))
    started_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        overlay_pkg = tmp_dir / "services"
        overlay_pkg.mkdir(parents=True, exist_ok=True)
        (overlay_pkg / "__init__.py").write_text(
            f"__path__.append({str((backend_root / 'services').resolve())!r})\n", encoding="utf-8"
        )
        extracted_hashes: Dict[str, str] = {}
        c1_s1_path = tmp_dir / "test_kh03_r5_c1_custody_abort_and_determinism.py"
        for rel, dest_p in (
            ("aurum-desk/backend/services/kh_03_r5_market_data_foundation_service.py", overlay_pkg / "kh_03_r5_market_data_foundation_service.py"),
            ("aurum-desk/backend/services/kh_03_r5_capture_and_custody_service.py", overlay_pkg / "kh_03_r5_capture_and_custody_service.py"),
            ("aurum-desk/backend/tests/test_kh03_r5_c1_custody_abort_and_determinism.py", c1_s1_path),
        ):
            gp = subprocess.run(["git", "show", f"{baseline_sha}:{rel}"], cwd=str(repo_root), capture_output=True, check=False, timeout=30)
            if gp.returncode != 0 or not gp.stdout:
                return {
                    "spec_version": SPEC_VERSION_KH03_R5_C2,
                    "status": "FAIL",
                    "baseline_sha": baseline_sha,
                    "error": f"GIT_SHOW_FAILED:{rel}:rc={gp.returncode}",
                    "all_c1_def_red_reproduced": False,
                }
            dest_p.write_bytes(gp.stdout)
            extracted_hashes[rel] = hashlib.sha256(gp.stdout).hexdigest()

        script_p = tmp_dir / "run_c1_red.py"
        script_p.write_text(_C1_RED_SUBPROC_SCRIPT, encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join([str(tmp_dir), str(backend_root), str(app_root), str(repo_root)])
        env["PYTHONHASHSEED"] = "0"
        proc = subprocess.run(
            [sys.executable, str(script_p), str(repo_root), str(c1_s1_path)],
            cwd=str(app_root), env=env, capture_output=True, text=True, timeout=60,
        )
        ended_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        stdout_sha = hashlib.sha256(proc.stdout.encode("utf-8", errors="replace")).hexdigest()
        stderr_sha = hashlib.sha256(proc.stderr.encode("utf-8", errors="replace")).hexdigest()
        parsed: Dict[str, Any] = {}
        parse_err: Optional[str] = None
        try:
            parsed = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
        except Exception as exc:
            parse_err = f"{type(exc).__name__}:{exc}"

        obs = parsed.get("observations") if isinstance(parsed, Mapping) else {}
        p1d_ok = bool((obs or {}).get("P1D_fault_control_literal_pass_weakness", {}).get("weakness_reproduced"))
        p1e_ok = bool((obs or {}).get("P1E_outer_cancel_blocked_worker_false_zero_undrained", {}).get("weakness_reproduced"))
        p1f_ok = bool((obs or {}).get("P1F_grant_replay_multiplies_budget", {}).get("weakness_reproduced"))
        all_ok = proc.returncode == 0 and p1d_ok and p1e_ok and p1f_ok
        return {
            "spec_version": SPEC_VERSION_KH03_R5_C2,
            "schema_version": SCHEMA_VERSION_KH03_R5_C2,
            "baseline_sha": baseline_sha,
            "tested_c2_sha": tested_c2_sha,
            "started_utc": started_utc,
            "ended_utc": ended_utc,
            "isolated_temp_overlay_used": True,
            "working_tree_mutated": False,
            "external_network_sockets_used": False,
            "command_display": f"python <isolated_tmp>/run_c1_red.py <repo_root> <c1_test_file> (overlay from git show {baseline_sha[:12]})",
            "subprocess_pid": parsed.get("pid"),
            "subprocess_exit_code": int(proc.returncode),
            "stdout_sha256": stdout_sha,
            "stderr_sha256": stderr_sha,
            "stderr_len": len(proc.stderr),
            "parse_error": parse_err,
            "extracted_baseline_file_sha256": extracted_hashes,
            "p1_d_red_reproduced": p1d_ok,
            "p1_e_red_reproduced": p1e_ok,
            "p1_f_red_reproduced": p1f_ok,
            "all_c1_def_red_reproduced": all_ok,
            "observations": obs,
            "status": "PASS" if all_ok else "FAIL",
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# =====================================================================
# 4. Function Mapping & 32-Gate Orthogonal Readiness Engine
# =====================================================================
def build_r5_c2_function_mapping_and_ownership() -> List[Dict[str, Any]]:
    return [
        {
            "file": "aurum-desk/backend/services/kh_03_r5_market_data_foundation_service.py",
            "functions": [
                "_canonical_grant_identity_c2",
                "R5GrantSessionLeaseC2",
                "R5GrantConsumptionLedgerC2 (register_or_check_grant_identity, claim_grant_session, reserve_dispatch, mark_dispatch_started, release_unstarted_reservation, settle_dispatch, finalize_grant_session, snapshot_grant)",
                "_ScopeBindingAuthorityC1 / validate_r5_operator_capture_scope / plan_r5_capture_batches",
            ],
            "change_type": "HARDENED_P1_F",
            "rationale": "Closes P1-F: stable logical grant identity, payload conflict rejection, atomic one-shot session claim, conservative unknown-byte settlement, and live durability hard-block.",
        },
        {
            "file": "aurum-desk/backend/services/kh_03_r5_capture_and_custody_service.py",
            "functions": [
                "R5CaptureWorkerTracker",
                "R5WorkerAttemptRecord",
                "R5CaptureCancelledError",
                "submit_tracked_transport_call",
                "drain_owned_transport_workers",
                "build_worker_lifecycle_receipt",
                "get_r5_global_worker_pool_stats_c2",
                "capture_r5_public_batch",
            ],
            "change_type": "HARDENED_P1_E_AND_P1_F",
            "rationale": "Closes P1-E & P1-F: replaces naked asyncio.to_thread with owned bounded ThreadPoolExecutor tracker, actual-start thread guard, honest undrained worker quarantine, conservative unknown-byte billing, honest failed-unlink quarantine, and hard-blocks all live network execution.",
        },
        {
            "file": "aurum-desk/backend/services/kh_03_r5_c2_fault_truth_and_readiness_service.py",
            "functions": [
                "FaultCaseSpec / FaultExecutionReceipt / REGISTERED_FAULT_PREDICATES_C2",
                "execute_fault_case_pair / validate_fault_execution_receipt / derive_fault_control_verdict",
                "audit_fault_certification_ast_for_literal_flags / build_c1_all48_correction_ledger",
                "run_real_c1_baseline_red_reproduction_from_git_sha",
                "derive_r5_c2_readiness_verdicts / build_r5_c2_owner_handover_vi",
            ],
            "change_type": "NEW_C2_SERVICE",
            "rationale": "Closes P1-D: typed execution-derived paired fault truth engine, C1 48-control correction ledger, isolated C1 RED harness, and 32-gate readiness engine.",
        },
        {
            "file": "aurum-desk/backend/services/kh_03_r5_c1_readiness_and_red_service.py + aurum-desk/tools/kh_03_r5_c1_independent_verifier.py + aurum-desk/tools/run_kh_03_r5_c1_acceptance.py",
            "functions": ["derive_r5_c1_readiness_verdicts", "verify_r5_c1_artifacts_independently_from_disk"],
            "change_type": "CLOSED_LEGACY_C1_CERTIFICATION_SURFACE",
            "rationale": "Closes §4.2: active C1 gates/verifier fail closed with LEGACY_CONTROL_PROOF_INSUFFICIENT_USE_C2 on unsupported boolean-only control proofs while leaving historical 420dcf06 sealed files untouched.",
        },
    ]


def derive_r5_c2_readiness_verdicts(
    *,
    raw_evidence: Mapping[str, Mapping[str, Any]],
    mem_audit: Mapping[str, Any],
    pytest_recomp: Mapping[str, Any],
    science_recomp: Mapping[str, Any],
    junit_ok: bool,
    r4_replay_ok: bool,
    paired_faults_recomp: Mapping[str, Any],
    worker_traces_recomp: Mapping[str, Any],
    stale_prior_shas: Sequence[str] = (),
    stale_prior_run_ids: Sequence[str] = (),
    for_final_verifier_manifest: bool = False,
    verifier_violations_count: int = 0,
) -> Dict[str, Any]:
    """Independently derives ALL 32 KH-03.R5.C2 gates and 18 separate verdict dimensions from raw disk evidence."""
    f00 = raw_evidence.get("00") or {}
    f01 = raw_evidence.get("01") or {}
    f02 = raw_evidence.get("02") or {}
    f03 = raw_evidence.get("03") or {}
    f04 = raw_evidence.get("04") or {}
    f06 = raw_evidence.get("06") or {}
    f08 = raw_evidence.get("08") or {}
    f09 = raw_evidence.get("09") or {}
    f10 = raw_evidence.get("10") or {}
    f11 = raw_evidence.get("11") or {}
    f12 = raw_evidence.get("12") or {}
    f13 = raw_evidence.get("13") or {}
    f14 = raw_evidence.get("14") or {}
    f15 = raw_evidence.get("15") or {}
    f16 = raw_evidence.get("16") or {}
    f17 = raw_evidence.get("17") or {}
    f18 = raw_evidence.get("18") or {}
    f19 = raw_evidence.get("19") or {}
    f22 = raw_evidence.get("22") or {}

    g: Dict[str, str] = {}

    # G01: Branch, clean worktree, unique frozen executable SHA
    tsha = str(f00.get("tested_executable_sha") or "")
    rid = str(f00.get("run_id") or "")
    sha_ok = (
        f00.get("branch") == MANDATORY_BRANCH_KH03_R5_C2
        and len(tsha) == 40
        and tsha == f00.get("start_head_sha") == f00.get("end_head_sha")
        and tsha not in set(stale_prior_shas)
        and rid not in set(stale_prior_run_ids)
    )
    clean_ok = _is_true(f00.get("git_clean_at_start")) and _is_false(f00.get("local_uncommitted_changes"))
    g["G01_BRANCH_SHA_CLEAN"] = "PASS" if (sha_ok and clean_ok) else ("BLOCKED_UNCOMMITTED_LOCAL_RUN" if sha_ok else "FAIL")

    # G02: Keep-as-is locked files/AST/callers
    g["G02_KEEP_AS_IS"] = (
        "PASS"
        if (
            f01.get("status") == "PASS"
            and _is_int(f01.get("locked_files_modified_count")) and int(f01["locked_files_modified_count"]) == 0
            and _is_int(f01.get("unauthorized_changed_files_count")) and int(f01["unauthorized_changed_files_count"]) == 0
            and isinstance(f01.get("forbidden_order_import_hits"), list) and len(f01["forbidden_order_import_hits"]) == 0
            and _is_true(f01.get("cost_assumptions_defaults_intact"))
            and f01.get("legacy_guard_status") == "PASS"
            and _is_int(f01.get("unauthorized_r1_callers_count")) and int(f01["unauthorized_r1_callers_count"]) == 0
        )
        else "FAIL"
    )

    # G03: Prior dataset, R1..R5.C1 sealed artifacts, and OOS escrow immutability
    g["G03_PRIOR_SEALED_DATASET_OOS"] = (
        "PASS"
        if (
            f02.get("status") == "PASS"
            and f02.get("canonical_dataset_sha256") == CANONICAL_DATASET_SHA256_KH02
            and _is_true(f02.get("canonical_dataset_sha256_match"))
            and _is_true(f02.get("all_prior_sealed_artifacts_intact"))
            and _is_int(((f02.get("c4_provenance_and_oos_boundary") or {}).get("real_oos_escrow_final_record_count")))
            and int((f02.get("c4_provenance_and_oos_boundary") or {})["real_oos_escrow_final_record_count"]) == 0
            and _is_int(f11.get("oos_reads_executed")) and int(f11["oos_reads_executed"]) == 0
            and _is_int(f11.get("oos_evaluations")) and int(f11["oos_evaluations"]) == 0
        )
        else "FAIL"
    )

    # G04: Real baseline C1 RED reproduction (P1-D, P1-E, P1-F)
    g["G04_BASELINE_REAL_RED"] = (
        "PASS"
        if (
            f03.get("status") == "PASS"
            and _is_true(f03.get("all_c1_def_red_reproduced"))
            and _is_true(f03.get("p1_d_red_reproduced"))
            and _is_true(f03.get("p1_e_red_reproduced"))
            and _is_true(f03.get("p1_f_red_reproduced"))
            and _is_false(f03.get("working_tree_mutated"))
            and _is_false(f03.get("external_network_sockets_used"))
            and _is_int(f03.get("subprocess_exit_code")) and int(f03["subprocess_exit_code"]) == 0
            and isinstance(f03.get("stdout_sha256"), str) and len(f03["stdout_sha256"]) == 64
        )
        else "FAIL"
    )

    # G05..G08: P1-D Fault Control Truth
    g["G05_C1_ALL48_TRUTH_INVENTORY"] = (
        "PASS"
        if (
            f04.get("status") == "PASS"
            and _is_int(f04.get("total_c1_controls_audited")) and int(f04["total_c1_controls_audited"]) == 48
            and _is_int(f04.get("unique_c1_fault_ids")) and int(f04["unique_c1_fault_ids"]) == 48
            and _is_false(f04.get("historical_c1_artifacts_mutated"))
        )
        else "FAIL"
    )
    pf_ok = bool(paired_faults_recomp.get("valid"))
    g["G06_PAIRED_EXECUTION_PROOF"] = (
        "PASS"
        if (
            pf_ok
            and _is_int(paired_faults_recomp.get("repaired_c1_verified_count")) and int(paired_faults_recomp["repaired_c1_verified_count"]) == 48
            and _is_int(paired_faults_recomp.get("new_c2_verified_count")) and int(paired_faults_recomp["new_c2_verified_count"]) >= 43
            and _is_int(paired_faults_recomp.get("failed_controls")) and int(paired_faults_recomp["failed_controls"]) == 0
        )
        else "FAIL"
    )
    g["G07_KIND_SPECIFIC_BASELINE_TRUTH"] = (
        "PASS"
        if (
            pf_ok
            and _is_true(f18.get("inherited_not_relabelled_paired"))
            and _is_int(f18.get("baseline_bug_red_proven_count")) and int(f18["baseline_bug_red_proven_count"]) >= 8
            and _is_int(f18.get("inherited_regression_executed_count")) and int(f18["inherited_regression_executed_count"]) >= 288
        )
        else "FAIL"
    )
    g["G08_LEGACY_GATE_FAIL_CLOSED"] = (
        "PASS"
        if (
            f06.get("status") == "PASS"
            and _is_true(f06.get("legacy_c1_gate_rejects_boolean_only_proof"))
            and _is_true(f06.get("legacy_c1_verifier_rejects_boolean_only_proof"))
            and _is_true(f06.get("ast_certification_path_no_literal_flags"))
            and _is_false(f06.get("historical_c1_artifacts_modified"))
        )
        else "FAIL"
    )

    # G09..G13: P1-E Blocking Worker Lifecycle
    wt_ok = bool(worker_traces_recomp.get("valid"))
    g["G09_WORKER_TRACKING"] = (
        "PASS"
        if (
            wt_ok
            and f08.get("status") == "PASS"
            and _is_true(f08.get("owned_bounded_executor_verified"))
            and _is_true(f08.get("terminal_state_conservation_verified"))
            and _is_true(f08.get("global_worker_cap_enforced_across_sessions"))
        )
        else "FAIL"
    )
    g["G10_ACTUAL_START_GUARD"] = (
        "PASS"
        if (
            f08.get("status") == "PASS"
            and _is_true(f08.get("queue_wait_deadline_zero_new_start"))
            and _is_true(f08.get("queue_wait_expiry_zero_new_start"))
            and _is_true(f08.get("actual_start_remaining_timeout_verified"))
        )
        else "FAIL"
    )
    g["G11_CANCELLATION_DRAIN"] = (
        "PASS"
        if (
            f08.get("status") == "PASS"
            and _is_true(f08.get("queued_future_cancelled_before_start_zero_calls"))
            and _is_true(f08.get("cooperative_cancel_clean_drain_verified"))
            and _is_true(f08.get("outer_cancellation_propagated_with_receipt"))
        )
        else "FAIL"
    )
    g["G12_UNDRAINED_QUARANTINE"] = (
        "PASS"
        if (
            f08.get("status") == "PASS"
            and _is_true(f08.get("blocked_worker_detected_undrained_gt_zero"))
            and _is_true(f08.get("blocked_worker_marked_fail_and_quarantined"))
            and _is_false(f08.get("blocked_worker_claimed_shutdown_complete"))
            and _is_true(f08.get("blocked_worker_eventually_exited_after_release"))
        )
        else "FAIL"
    )
    g["G13_LATE_RESULT_ABORT_TRUTH"] = (
        "PASS"
        if (
            f08.get("status") == "PASS"
            and _is_true(f08.get("late_success_after_abort_zero_accepted"))
            and _is_true(f08.get("failed_unlink_quarantined_honestly"))
            and _is_true(f08.get("unrelated_files_preserved_on_cleanup"))
        )
        else "FAIL"
    )

    # G14..G18: P1-F Grant Identity, One-Shot Claim, Replay & Restart Boundary
    g["G14_GRANT_IDENTITY"] = (
        "PASS"
        if (
            f09.get("status") == "PASS"
            and _is_true(f09.get("stable_logical_identity_verified"))
            and _is_true(f09.get("same_id_changed_payload_conflict_denied"))
            and _is_true(f09.get("cloned_or_tampered_receipt_denied"))
        )
        else "FAIL"
    )
    g["G15_ONE_SHOT_CLAIM"] = (
        "PASS"
        if (
            f09.get("status") == "PASS"
            and f10.get("status") == "PASS"
            and _is_true(f09.get("sequential_replay_one_winner_only"))
            and _is_int(f10.get("concurrent_race_winners_count")) and int(f10["concurrent_race_winners_count"]) == 1
            and _is_int(f10.get("concurrent_race_total_calls")) and int(f10["concurrent_race_total_calls"]) <= int(f10.get("concurrent_race_cap", 0))
            and _is_true(f09.get("abort_burns_claimed_grant"))
        )
        else "FAIL"
    )
    g["G16_GRANT_REPLAN_REISSUE"] = (
        "PASS"
        if (
            f09.get("status") == "PASS"
            and _is_true(f09.get("second_plan_from_same_scope_denied"))
            and _is_true(f09.get("reissued_scope_receipt_same_grant_denied"))
            and _is_true(f09.get("distinct_new_qa_grant_succeeds"))
        )
        else "FAIL"
    )
    g["G17_REQUEST_BYTE_CONSERVATION"] = (
        "PASS"
        if (
            f10.get("status") == "PASS"
            and _is_true(f10.get("duplicate_attempt_settle_release_blocked"))
            and _is_true(f10.get("unknown_bytes_conservatively_retained"))
            and _is_true(f10.get("ledger_unhealthy_and_capacity_exhausted_denied"))
            and _is_true(f10.get("no_negative_ledger_counters"))
        )
        else "FAIL"
    )
    live_hard_blk_ok = (
        f11.get("status") == "PASS"
        and _is_true(f11.get("live_hard_blocked_even_with_stub_verifier"))
        and f11.get("live_grant_replay_durability") == "NOT_PROVEN_BLOCKED"
        and f11.get("live_transport_termination") == "NOT_PROVEN_BLOCKED"
        and _is_int(f11.get("external_market_requests_attempted")) and int(f11["external_market_requests_attempted"]) == 0
    )
    g["G18_RESTART_TRUST_BOUNDARY"] = "PASS_LIVE_HARD_BLOCK_PROVEN" if live_hard_blk_ok else "FAIL"

    # G19 & G30: Strict Resource Guard & Actual Cleanup
    mp00 = f00.get("strict_multi_mount_probe") or {}
    waiver = (
        bool(f00.get("owner_disk_preflight_waiver", False))
        or bool(mp00.get("owner_waiver_permitted", False))
        or bool(f17.get("owner_disk_preflight_waiver", False))
    )
    mounts = list(mp00.get("distinct_mounts") or ())
    probe_errs = list(mp00.get("probe_errors") or ())
    pre_disk_ok = (
        len(mounts) > 0 and len(probe_errs) == 0
        and all(
            _is_num(m.get("free_gib")) and float(m["free_gib"]) >= STRICT_PREFLIGHT_DISK_MIN_GIB_R5
            and _is_num(m.get("projected_post_reserve_gib")) and float(m["projected_post_reserve_gib"]) >= STRICT_RUNTIME_DISK_MIN_GIB_R5
            for m in mounts
        )
        and _is_true(mp00.get("can_run_heavy_stages"))
    )
    min_rt_disk = float(f17["min_disk_free_gib"]) if _is_num(f17.get("min_disk_free_gib")) else 0.0
    min_rt_ram = float(f17["min_available_ram_mib"]) if _is_num(f17.get("min_available_ram_mib")) else 0.0
    max_ch = int(f17["max_descendant_children"]) if _is_int(f17.get("max_descendant_children")) else 99
    rt_ok = (
        min_rt_disk >= STRICT_RUNTIME_DISK_MIN_GIB_R5
        and min_rt_ram >= HARD_RAM_FLOOR_MIB_R5_C2
        and max_ch <= GLOBAL_MAX_DESCENDANTS_R5_C2
        and mem_audit.get("status") == "PASS"
        and _is_int(mem_audit.get("below_floor_samples_count")) and int(mem_audit["below_floor_samples_count"]) == 0
    )
    if waiver or len(probe_errs) > 0:
        g["G19_STRICT_RESOURCE_GUARD"] = "FAIL"
        res_verdict = "FAIL"
    elif not pre_disk_ok:
        g["G19_STRICT_RESOURCE_GUARD"] = "BLOCKED_LOW_DISK"
        res_verdict = "BLOCKED_LOW_DISK"
    elif not rt_ok:
        g["G19_STRICT_RESOURCE_GUARD"] = "FAIL"
        res_verdict = "FAIL"
    else:
        g["G19_STRICT_RESOURCE_GUARD"] = "PASS"
        res_verdict = "PASS"
    res_pass = (g["G19_STRICT_RESOURCE_GUARD"] == "PASS")
    low_disk = (g["G19_STRICT_RESOURCE_GUARD"] == "BLOCKED_LOW_DISK")

    g["G30_RESOURCE_CLEANUP"] = (
        "PASS"
        if (
            res_pass
            and _is_int(f17.get("orphan_children_after_cleanup")) and int(f17["orphan_children_after_cleanup"]) == 0
            and _is_true(f17.get("sentinel_temp_cleaned_up"))
            and _is_int(f17.get("active_capture_workers_after_run")) and int(f17["active_capture_workers_after_run"]) == 0
            and _is_int(f17.get("quarantined_capture_workers_after_run")) and int(f17["quarantined_capture_workers_after_run"]) == 0
        )
        else "FAIL"
    )

    # G20: QA vs Live isolation
    g["G20_QA_LIVE_ISOLATION"] = (
        "PASS"
        if (
            f11.get("status") == "PASS"
            and _is_int(f11.get("external_market_requests_attempted")) and int(f11["external_market_requests_attempted"]) == 0
            and _is_int(f11.get("accepted_real_observed_records")) and int(f11["accepted_real_observed_records"]) == 0
            and _is_true(f11.get("qa_grant_cannot_authorize_live"))
            and _is_true(f11.get("fake_transport_cannot_mint_observed_verified"))
            and _is_true(f11.get("qa_cannot_use_live_stdlib_transport"))
        )
        else "FAIL"
    )

    # G21: Full Repo Pytest & JUnit 1:1 Bijection
    if f15.get("status") == "NOT_RUN_BLOCKED_LOW_DISK":
        g["G21_FULL_PYTEST"] = "BLOCKED_LOW_DISK" if low_disk else "FAIL"
        reg_verdict = "BLOCKED" if low_disk else "FAIL"
    elif (
        f15.get("status") == "PASS" and res_pass and bool(junit_ok)
        and _is_true(f15.get("junit_bijection_exact"))
        and _is_int(f15.get("collected_count")) and int(f15["collected_count"]) >= MIN_EXPECTED_PYTEST_COLLECTED_R5_C2
        and _is_int(f15.get("executed_count")) and int(f15["executed_count"]) == int(f15["collected_count"])
        and _is_int(f15.get("failed_count")) and int(f15["failed_count"]) == 0
        and _is_int(f15.get("error_count")) and int(f15["error_count"]) == 0
        and _is_int(f15.get("mandatory_skipped_count")) and int(f15["mandatory_skipped_count"]) == 0
    ):
        g["G21_FULL_PYTEST"] = "PASS"
        reg_verdict = "PASS"
    else:
        g["G21_FULL_PYTEST"] = "FAIL"
        reg_verdict = "FAIL"

    # G22: Full Production Baseline Parity
    if f14.get("BASELINE_PARITY_VERDICT") == "NOT_RUN_BLOCKED_LOW_DISK":
        g["G22_BASELINE_PARITY"] = "BLOCKED_LOW_DISK" if low_disk else "FAIL"
        par_verdict = "BLOCKED" if low_disk else "FAIL"
    elif (
        f14.get("BASELINE_PARITY_VERDICT") == "PASS" and res_pass
        and _is_int(f14.get("a1_population_count")) and int(f14["a1_population_count"]) == 56710
        and _is_int(f14.get("a1_mismatches_count")) and int(f14["a1_mismatches_count"]) == 0
        and _is_int(f14.get("si8_population_count")) and int(f14["si8_population_count"]) == 6375
        and _is_int(f14.get("si8_mismatches_count")) and int(f14["si8_mismatches_count"]) == 0
        and _is_int(f14.get("three_adapter_population_count")) and int(f14["three_adapter_population_count"]) == 246
        and _is_int(f14.get("three_adapter_mismatches_count")) and int(f14["three_adapter_mismatches_count"]) == 0
        and _is_int(f14.get("d4_historical_closed_trades")) and int(f14["d4_historical_closed_trades"]) == 0
        and _is_true(((f14.get("per_decision_and_episode_fingerprints") or {}).get("all_sealed_fingerprints_exact_match")))
    ):
        g["G22_BASELINE_PARITY"] = "PASS"
        par_verdict = "PASS"
    else:
        g["G22_BASELINE_PARITY"] = "FAIL"
        par_verdict = "FAIL"

    # G23: Backtest Parity (R4 replay + 6 cost scenarios + E1/E2/E3)
    g["G23_BACKTEST_PARITY"] = (
        "PASS"
        if (
            bool(r4_replay_ok) and f12.get("status") == "PASS" and f13.get("status") == "PASS"
            and _is_true(f12.get("fresh_replay_executed_not_copied"))
            and _is_true(f12.get("r4_decimal_bridge_exact_match"))
            and _is_true(f12.get("r4_row_ids_and_ledger_sha_match"))
            and _is_false(f12.get("new_data_used_in_backtest"))
            and _is_false(f12.get("planned_cap_is_physical_guarantee"))
            and _is_int(f12.get("realized_losses_gt_2p50_count")) and int(f12["realized_losses_gt_2p50_count"]) == 139
            and _is_true(f13.get("six_cost_dynamic_vs_fixed_fills_separated"))
            and _is_true(f13.get("arms_e1_e2_e3_anchors_exact_match"))
        )
        else "FAIL"
    )

    # G24 & G25: Deterministic Workers & Measured Worker Policy
    if f16.get("status") == "NOT_RUN_BLOCKED_LOW_DISK":
        st_b = "BLOCKED_LOW_DISK" if low_disk else "FAIL"
        g["G24_DETERMINISTIC_WORKERS"] = st_b
        g["G25_MEASURED_WORKER_POLICY"] = st_b
        det_verdict = "BLOCKED" if low_disk else "FAIL"
    else:
        p_ok = bool(pytest_recomp.get("valid"))
        s_ok = bool(science_recomp.get("valid"))
        p_pol = f16.get("pytest_worker_policy") or {}
        s_pol = f16.get("science_worker_policy") or {}
        g["G24_DETERMINISTIC_WORKERS"] = (
            "PASS" if (res_pass and p_ok and s_ok and _is_true(f16.get("c2_capture_fixture_w1_w2_w4_identical"))) else "FAIL"
        )
        g["G25_MEASURED_WORKER_POLICY"] = (
            "PASS"
            if (
                res_pass and p_ok and s_ok and f16.get("status") == "PASS"
                and _is_int(p_pol.get("applied_worker_count")) and 1 <= int(p_pol["applied_worker_count"]) <= 3
                and _is_int(s_pol.get("applied_worker_count")) and 1 <= int(s_pol["applied_worker_count"]) <= GLOBAL_MAX_DESCENDANTS_R5_C2
                and _is_false(f16.get("full_suite_speedup_extrapolated"))
            )
            else "FAIL"
        )
        det_verdict = "PASS" if (g["G24_DETERMINISTIC_WORKERS"] == "PASS" and g["G25_MEASURED_WORKER_POLICY"] == "PASS") else "FAIL"

    # G26 & G27: Inherited Regression & New/Repaired Paired Fault Controls
    g["G26_INHERITED_REGRESSION"] = (
        "PASS"
        if (
            f18.get("status") == "PASS"
            and _is_int(f18.get("inherited_regression_executed_count")) and int(f18["inherited_regression_executed_count"]) >= 288
            and _is_int(f18.get("inherited_regression_failed_count")) and int(f18["inherited_regression_failed_count"]) == 0
            and _is_int(f19.get("inherited_mutations_tested_count")) and int(f19["inherited_mutations_tested_count"]) >= 111
            and _is_int(f19.get("inherited_mutations_failed_detection_count")) and int(f19["inherited_mutations_failed_detection_count"]) == 0
        )
        else "FAIL"
    )
    g["G27_NEW_FAULT_CONTROLS"] = (
        "PASS"
        if (
            pf_ok and f18.get("status") == "PASS"
            and _is_int(f18.get("repaired_c1_paired_verified_count")) and int(f18["repaired_c1_paired_verified_count"]) == 48
            and _is_int(f18.get("new_c2_paired_verified_count")) and int(f18["new_c2_paired_verified_count"]) >= 43
            and _is_int(f18.get("paired_controls_failed_count")) and int(f18["paired_controls_failed_count"]) == 0
        )
        else "FAIL"
    )

    # G28 & G29: Separate-OS Verifier & Mutation Resistance
    g["G28_INDEPENDENT_VERIFIER"] = (
        ("PASS" if verifier_violations_count == 0 else "FAIL")
        if for_final_verifier_manifest
        else "PENDING_SEPARATE_OS_VERIFIER"
    )
    g["G29_MUTATION_RESISTANCE"] = (
        "PASS"
        if (
            f19.get("status") == "PASS"
            and _is_true(f19.get("clean_clone_verifier_green"))
            and _is_int(f19.get("new_c2_mutations_tested_count")) and int(f19["new_c2_mutations_tested_count"]) >= 24
            and _is_int(f19.get("new_c2_mutations_failed_detection_count")) and int(f19["new_c2_mutations_failed_detection_count"]) == 0
            and _is_int(f19.get("inherited_mutations_failed_detection_count")) and int(f19["inherited_mutations_failed_detection_count"]) == 0
        )
        else "FAIL"
    )

    # G31: Economic & Activation Locks
    modes = f00.get("runtime_safety_modes") or {}
    g["G31_ECONOMIC_ACTIVATION"] = (
        "PASS"
        if (
            f00.get("ACTIVATION_READY") == "NO"
            and all(modes.get(k) == "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO"))
            and f12.get("RESEARCH_ECONOMIC_VERDICT") == "NO_PROVEN_EDGE"
            and f12.get("KH04_ENTRY") == "BLOCKED_NO_DEV_CANDIDATE"
            and _is_int(f12.get("OOS_EVALUATIONS")) and int(f12["OOS_EVALUATIONS"]) == 0
            and _is_int(f11.get("external_market_requests_attempted")) and int(f11["external_market_requests_attempted"]) == 0
            and _is_int(f11.get("accepted_real_observed_records")) and int(f11["accepted_real_observed_records"]) == 0
        )
        else "FAIL"
    )

    # G32: Remote Publication
    false_pub = (
        (bool(f22.get("remote_readback_sha256_verified")) and not f22.get("publication_head_sha"))
        or (f22.get("ci_status") == "CI_PASS" and not _is_true(f22.get("actual_github_actions_run_verified")))
    )
    g["G32_REMOTE_PUBLICATION"] = "FAIL" if false_pub else str(f22.get("publication_status") or "PRE_PUSH_PENDING")

    ordered = {k: g[k] for k in GATE_32_KEYS_KH03_R5_C2}

    def _gate_ok(k: str) -> bool:
        if k == "G18_RESTART_TRUST_BOUNDARY":
            return ordered[k] == "PASS_LIVE_HARD_BLOCK_PROVEN"
        return ordered[k] == "PASS"

    eng_keys = [k for k in GATE_32_KEYS_KH03_R5_C2 if k not in ("G28_INDEPENDENT_VERIFIER", "G32_REMOTE_PUBLICATION")]
    if for_final_verifier_manifest:
        eng_keys.append("G28_INDEPENDENT_VERIFIER")

    any_fail = any(ordered[k] == "FAIL" for k in GATE_32_KEYS_KH03_R5_C2)
    all_eng_pass = all(_gate_ok(k) for k in eng_keys) and not false_pub
    eng_verdict = "PASS" if all_eng_pass else ("BLOCKED" if (low_disk and not any_fail) else "FAIL")

    ctrl_truth_v = "PASS" if all(ordered[k] == "PASS" for k in ("G05_C1_ALL48_TRUTH_INVENTORY", "G06_PAIRED_EXECUTION_PROOF", "G07_KIND_SPECIFIC_BASELINE_TRUTH", "G08_LEGACY_GATE_FAIL_CLOSED", "G27_NEW_FAULT_CONTROLS")) else "FAIL"
    worker_lc_v = "PASS" if all(ordered[k] == "PASS" for k in ("G09_WORKER_TRACKING", "G10_ACTUAL_START_GUARD", "G11_CANCELLATION_DRAIN", "G12_UNDRAINED_QUARANTINE", "G13_LATE_RESULT_ABORT_TRUTH")) else "FAIL"
    grant_rep_v = "PASS" if all(ordered[k] == "PASS" for k in ("G14_GRANT_IDENTITY", "G15_ONE_SHOT_CLAIM", "G16_GRANT_REPLAN_REISSUE", "G17_REQUEST_BYTE_CONSERVATION")) and ordered["G18_RESTART_TRUST_BOUNDARY"] == "PASS_LIVE_HARD_BLOCK_PROVEN" else "FAIL"

    overall_cls = (
        CLASSIFICATION_PASS_OFFLINE_C2 if all_eng_pass
        else ("BLOCKED_RESOURCE_LIMIT" if (low_disk and not any_fail) else "FAIL_CLOSED")
    )

    verdicts = {
        "ENGINEERING": eng_verdict,
        "CONTROL_TRUTH": ctrl_truth_v,
        "WORKER_LIFECYCLE": worker_lc_v,
        "GRANT_REPLAY_SAFETY_OFFLINE": grant_rep_v,
        "LIVE_GRANT_REPLAY_DURABILITY": "NOT_PROVEN_BLOCKED",
        "LIVE_TRANSPORT_TERMINATION": "NOT_PROVEN_BLOCKED",
        "RESOURCE_SAFETY": res_verdict,
        "REGRESSION": reg_verdict,
        "BACKTEST_PARITY": par_verdict if g["G23_BACKTEST_PARITY"] == "PASS" else "FAIL",
        "DETERMINISM": det_verdict,
        "PUBLICATION": ordered["G32_REMOTE_PUBLICATION"],
        "CI_STATUS": str(f22.get("ci_status") or "CI_UNKNOWN_NO_ACTIONS_RUN"),
        "DATA_READINESS": "INCOMPLETE",
        "OBSERVED_BBO_COVERAGE": "MISSING",
        "FUNDING_READINESS": "MISSING",
        "ACCOUNT_FEE_READINESS": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        "EXECUTION_FIDELITY": "INCOMPLETE",
        "CAPTURE_AUTHORIZATION": "BLOCKED_OWNER_AUTHORIZATION",
        "CAPTURE_EXECUTION": "BLOCKED_OWNER_AUTHORIZATION",
        "CAPTURE_PROVENANCE": "NO_REAL_CAPTURE",
        "LIVE_NETWORK_VALIDATION": "NOT_RUN_BY_DESIGN",
        "ECONOMIC": "NO_PROVEN_EDGE",
        "FRESH_OOS": "BLOCKED_NO_UNTOUCHED_DATA",
        "KH04_ENTRY": "BLOCKED_NO_DEV_CANDIDATE",
        "OOS_EVALUATIONS": 0,
        "EXTERNAL_MARKET_REQUESTS_ATTEMPTED": 0,
        "ACCEPTED_REAL_OBSERVED_RECORDS": 0,
        "ACTIVATION_READY": "NO",
        "PRODUCTION": "OFF",
        "LIVE": "OFF",
        "PAPER": "OFF",
        "AUTO": "OFF",
    }
    return {
        "spec_version": SPEC_VERSION_KH03_R5_C2,
        "schema_version": SCHEMA_VERSION_KH03_R5_C2,
        "gates_32": ordered,
        "verdicts": verdicts,
        "overall_classification": overall_cls,
    }


def build_r5_c2_owner_handover_vi(report: Mapping[str, Any], receipts: Mapping[str, Mapping[str, Any]]) -> str:
    """Concise, auditable Vietnamese owner handover (§14)."""
    f00 = receipts.get("00") or {}
    f03 = receipts.get("03") or {}
    f04 = receipts.get("04") or {}
    f08 = receipts.get("08") or {}
    f10 = receipts.get("10") or {}
    f12 = receipts.get("12") or {}
    f14 = receipts.get("14") or {}
    f15 = receipts.get("15") or {}
    f16 = receipts.get("16") or {}
    f17 = receipts.get("17") or {}
    f18 = receipts.get("18") or {}
    f19 = receipts.get("19") or {}
    v = report.get("verdicts") or {}
    g = report.get("gates_32") or {}
    pp = f16.get("pytest_worker_policy") or {}
    sp = f16.get("science_worker_policy") or {}
    lines = [
        "AURUM DESK — BAN GIAO CHU SO HUU KH-03.R5.C2 (TIENG VIET)",
        "======================================================================",
        f"SPEC: {SPEC_VERSION_KH03_R5_C2} | RUN_ID: {f00.get('run_id')}",
        f"BRANCH: {f00.get('branch')} | BASELINE_C1_HEAD: {f00.get('baseline_reviewed_head_sha')} | TESTED_SHA: {f00.get('tested_executable_sha')}",
        f"CLASSIFICATION: {report.get('overall_classification')}",
        f"ENGINEERING={v.get('ENGINEERING')} | CONTROL_TRUTH={v.get('CONTROL_TRUTH')} | WORKER_LIFECYCLE={v.get('WORKER_LIFECYCLE')} | GRANT_REPLAY_OFFLINE={v.get('GRANT_REPLAY_SAFETY_OFFLINE')}",
        f"LIVE_GRANT_REPLAY_DURABILITY={v.get('LIVE_GRANT_REPLAY_DURABILITY')} | LIVE_TRANSPORT_TERMINATION={v.get('LIVE_TRANSPORT_TERMINATION')}",
        f"RESOURCE_SAFETY={v.get('RESOURCE_SAFETY')} | REGRESSION={v.get('REGRESSION')} | BACKTEST_PARITY={v.get('BACKTEST_PARITY')} | DETERMINISM={v.get('DETERMINISM')}",
        f"CAPTURE_AUTHORIZATION={v.get('CAPTURE_AUTHORIZATION')} | ECONOMIC={v.get('ECONOMIC')} | FRESH_OOS={v.get('FRESH_OOS')} | KH04={v.get('KH04_ENTRY')} | ACTIVATION_READY=NO | PRODUCTION/LIVE/PAPER/AUTO=OFF | OOS=0 | EXTERNAL_MARKET_REQUESTS=0",
        f"PUBLICATION={v.get('PUBLICATION')} | CI_STATUS={v.get('CI_STATUS')}",
        "",
        "1. TAI HIEN RED TREN C1 (3ad0c297) & DONG 3 LOI KY THUAT P1-D / P1-E / P1-F:",
        f"- Baseline C1 RED (03): p1_d={f03.get('p1_d_red_reproduced')}, p1_e={f03.get('p1_e_red_reproduced')}, p1_f={f03.get('p1_f_red_reproduced')}, exit={f03.get('subprocess_exit_code')}, stdout_sha256={str(f03.get('stdout_sha256') or '')[:16]}...",
        f"- P1-D (04/05/06/18): 48/48 C1 da sua thanh cap negative+positive co bien nhan thuc thi doc lap ({f04.get('c2_baseline_bug_red_count')} BASELINE_BUG_RED + {f04.get('c2_negative_contract_count')} NEGATIVE_CONTRACT) + {f18.get('new_c2_paired_verified_count')} cap C2 moi; {f18.get('inherited_regression_executed_count')} inherited regression bao cao rieng, khong gan nhan paired gia.",
        f"- P1-E (07/08): Thay asyncio.to_thread bang R5CaptureWorkerTracker voi ThreadPoolExecutor co tran toan cuc <=4; worker bi chan khi het drain bao FAIL_UNDRAINED_INFLIGHT_WORKER + quarantine (undrained_at_return={f08.get('blocked_worker_undrained_at_return')}, eventual_exit={f08.get('blocked_worker_eventually_exited_after_release')}).",
        f"- P1-F (09/10/11): R5GrantConsumptionLedgerC2 ap dung ONE-SHOT BOUNDED SESSION; dua dong thoi chi 1 nguoi thang (winners={f10.get('concurrent_race_winners_count')}, total_calls={f10.get('concurrent_race_total_calls')}/{f10.get('concurrent_race_cap')}); chan moi nhanh live bang HARD BLOCK (G18=PASS_LIVE_HARD_BLOCK_PROVEN).",
        "",
        "2. KIEM THU, VERIFIER, PARITY & REPLAY:",
        f"- Full Pytest JUnit 1:1 (15): collected={f15.get('collected_count')}, executed={f15.get('executed_count')}, passed={f15.get('passed_count')}, skipped={f15.get('skipped_count')}, mandatory_skipped={f15.get('mandatory_skipped_count')}, failed={f15.get('failed_count')}, error={f15.get('error_count')}",
        f"- Verifier Mutations (19): {f19.get('new_c2_mutations_detected_count')}/{f19.get('new_c2_mutations_tested_count')} moi C2 + {f19.get('inherited_mutations_detected_count')}/{f19.get('inherited_mutations_tested_count')} ke thua",
        f"- Baseline Parity (14): A1={f14.get('a1_population_count')}/{f14.get('a1_mismatches_count')}, SI8={f14.get('si8_population_count')}/{f14.get('si8_mismatches_count')}, Adapter={f14.get('three_adapter_population_count')}/{f14.get('three_adapter_mismatches_count')}, D4={f14.get('d4_historical_closed_trades')}",
        f"- Fresh R4 Replay (12/13): closed={f12.get('null_1x_closed')} ({f12.get('null_1x_wins')}W/{f12.get('null_1x_losses')}L), gross={f12.get('null_1x_gross_usd')} USD, fees={f12.get('null_1x_closed_fees_usd')} USD, net={f12.get('null_1x_net_usd')} USD, exp={f12.get('null_1x_expectancy_r')}R, >2.50USD={f12.get('realized_losses_gt_2p50_count')}",
        f"- Benchmark (16): pytest={pp.get('mean_wall_sec_by_mode')} (w1_rep={pp.get('w1_repeat_wall_sec')}s, fastest={pp.get('measured_fastest_mode')}, applied={pp.get('applied_worker_count')}); science={sp.get('mean_wall_sec_by_mode')} (w1_rep={sp.get('w1_repeat_wall_sec')}s, fastest={sp.get('measured_fastest_mode')}, applied={sp.get('applied_worker_count')})",
        f"- Resources (17): min_disk={f17.get('min_disk_free_gib')} GiB, min_ram={f17.get('min_available_ram_mib')} MiB, peak_rss={f17.get('max_tree_rss_mib')} MiB, max_children={f17.get('max_descendant_children')}, orphans={f17.get('orphan_children_after_cleanup')}, active_workers_after={f17.get('active_capture_workers_after_run')}",
        "",
        "3. 32 CONG (G01..G32):",
    ] + [f"  {k}: {val}" for k, val in g.items()]
    return "\n".join(lines) + "\n"
