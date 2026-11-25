#!/usr/bin/env python3
"""
aurum-desk/tools/kh_03_r5_c2_independent_verifier.py

AURUM DESK — KH-03.R5.C2 | SEPARATE-OS INDEPENDENT DISK VERIFIER (G01..G32)
SPEC VERSION: KH-03.R5.C2-v1.0 | ZERO ORCHESTRATOR IMPORT | RAW RECEIPT & TRACE RECOMPUTATION

Runs in a distinct OS process (`verifier_pid != orchestrator_pid`), reads ALL raw C2 artifacts
(`00`..`20`, `22`, `05_paired_fault_execution_receipts.jsonl.gz`,
`07_worker_lifecycle_actual_entry_exit_traces.jsonl.gz`, `README.md`, `HANDOVER_KH03_R5_C2_VI.txt`,
`logs/r4_replay_forensic_ledger_1x.jsonl.gz`, `logs/runtime_memory_samples_0p5s.jsonl.gz`,
`logs/pytest_collected_executed_nodeids.jsonl.gz`, `junit/*.xml`) directly from disk using strict
duplicate-key & non-finite-rejecting JSON parsing, independently recomputes:
  - Prior R1..R5.C1 + canonical dataset SHA-256 immutability
  - Real C1 baseline RED reproduction (`03`) & C1 all-48 correction ledger (`04`)
  - Every paired fault control receipt (`05_paired_fault_execution_receipts.jsonl.gz`) by
    independently re-evaluating registered pure predicates over raw `observed_fields` and
    recomputing `trace_digest` (catching M01..M08!)
  - Legacy C1 certification fail-closed closure (`06`, catching M22!)
  - Every raw blocking worker lifecycle trace (`07_worker_lifecycle_actual_entry_exit_traces.jsonl.gz`)
    + cancellation/drain/quarantine receipts (`08`, catching M09..M15!)
  - One-shot grant identity/replay/conservation & restart boundary live hard-block (`09`, `10`, `11`,
    catching M16..M21!)
  - Exact Decimal R4 replay parity (`12`, `13`, catching M24!)
  - Full A1/SI8/adapter/D4 parity (`14`)
  - Raw JUnit XML 1:1 bijection (`15`, catching M23!)
  - Raw W1/W2/W4/W1_repeat timings & `recompute_worker_policy_from_raw_receipt` (`16`)
  - Raw 0.5s telemetry floor compliance (`17`)
  - Fault summary (`18`), separate-OS mutation results (`19`), 32 orthogonal gates (`20`), pub (`22`)
Then seals `21_independent_OS_verifier_manifest_sha256.json` (excluding itself from its hash index).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Mapping, Optional, Tuple
import xml.etree.ElementTree as ET

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_prospective_data_service import CANONICAL_DATASET_SHA256_KH02
from services.kh_03_r3_c1_worker_policy_service import (
    BENCHMARK_KIND_PYTEST,
    BENCHMARK_KIND_SCIENCE,
    recompute_worker_policy_from_raw_receipt,
)
from services.kh_03_r5_c2_fault_truth_and_readiness_service import (
    BASELINE_REVIEWED_HEAD_SHA_C2,
    GATE_32_KEYS_KH03_R5_C2,
    GLOBAL_MAX_DESCENDANTS_R5_C2,
    HARD_RAM_FLOOR_MIB_R5_C2,
    MANDATORY_BRANCH_KH03_R5_C2,
    MIN_EXPECTED_PYTEST_COLLECTED_R5_C2,
    PRIOR_C1_EVIDENCE_COMMIT_SHA,
    PRIOR_C1_RUN_ID,
    PRIOR_C1_TESTED_EXECUTABLE_SHA,
    derive_fault_control_verdict,
    derive_r5_c2_readiness_verdicts,
)
from services.kh_03_r5_market_data_foundation_service import (
    SCHEMA_VERSION_KH03_R5_C2,
    SPEC_VERSION_KH03_R5_C2,
    STRICT_RUNTIME_DISK_MIN_GIB_R5,
)
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_03_r5_c1_independent_verifier import (
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R5_C1,
    STALE_PRIOR_RUN_IDS_R5_C1,
    STALE_PRIOR_SHAS_R5_C1,
)

MANIFEST_21_NAME_KH03_R5_C2 = "21_independent_OS_verifier_manifest_sha256.json"
Q4 = Decimal("0.0001")

# Verified KH-03.R5.C1 sealed artifacts at reviewed HEAD 420dcf06601fee960ec7e04bd0c2f96b74bfd4b8
PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R5_C2: Tuple[Tuple[str, str, str], ...] = (
    *PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R5_C1,
    (
        "aurum-desk/docs/artifacts/kh_03_r5_c1_capture_safety/latest_run.json",
        "0b4fd0eba70dd9592b3b12031bef6f3b771037f7a69db968536c80e0cfe6d0bf",
        "KH03_R5_C1_LATEST_RUN_POINTER",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r5_c1_capture_safety/23_phase2_remote_readback_{PRIOR_C1_RUN_ID}.json",
        "0e7c1d5fb76791053561538a398dca643416ec1724713a3507dc94bf1942abf6",
        "KH03_R5_C1_SEALED_POSTPUSH_READBACK_23",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r5_c1_capture_safety/{PRIOR_C1_RUN_ID}/21_independent_OS_verifier_manifest_sha256.json",
        "cb4770de85f28a989da6defc4991401cf1b9a06c0dd72a98098a51615439c3fc",
        "KH03_R5_C1_SEALED_MANIFEST_21",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r5_c1_capture_safety/{PRIOR_C1_RUN_ID}/20_orthogonal_gates_and_verdicts.json",
        "2fe0bbab7eb3212dca975b9c7a7d13784b0caac41accd0461d384d049728998e",
        "KH03_R5_C1_SEALED_30_GATES_20",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r5_c1_capture_safety/{PRIOR_C1_RUN_ID}/12_r4_replay_decimal_economic_bridge.json",
        "6d9d0f2117261adc7a511acc1c85da35bd8973e309e4503b0b083c8aa6a904e0",
        "KH03_R5_C1_SEALED_R4_BRIDGE_12",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r5_c1_capture_safety/{PRIOR_C1_RUN_ID}/14_A1_SI8_adapter_D4_full_parity.json",
        "1aa38b30cca0a92c8e195cf800671632e6ef7a0f3d546878d93e901e2519db68",
        "KH03_R5_C1_SEALED_PARITY_14",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r5_c1_capture_safety/{PRIOR_C1_RUN_ID}/logs/r4_replay_forensic_ledger_1x.jsonl.gz",
        "95488a2bb677b55015a936c857f793274193f3af71cc8dee33d7569f2b1939a2",
        "KH03_R5_C1_SEALED_FORENSIC_LEDGER_GZ",
    ),
)

KH03_R5_C2_SOURCE_FILES_ALLOWLIST: Tuple[str, ...] = (
    "aurum-desk/docs/kh_03_r5_c2/README.md",
    "aurum-desk/docs/artifacts/kh_03_r5_c2_technical_closure/README.md",
    "aurum-desk/backend/services/kh_03_r5_market_data_foundation_service.py",
    "aurum-desk/backend/services/kh_03_r5_capture_and_custody_service.py",
    "aurum-desk/backend/services/kh_03_r5_c1_readiness_and_red_service.py",
    "aurum-desk/backend/services/kh_03_r5_c2_fault_truth_and_readiness_service.py",
    "aurum-desk/tools/kh_03_r5_c1_independent_verifier.py",
    "aurum-desk/tools/run_kh_03_r5_c1_acceptance.py",
    "aurum-desk/tools/kh_03_r5_c2_independent_verifier.py",
    "aurum-desk/tools/run_kh_03_r5_c2_acceptance.py",
    "aurum-desk/backend/tests/test_kh03_r5_capture_provenance.py",
    "aurum-desk/backend/tests/test_kh03_r5_readiness_and_verifier.py",
    "aurum-desk/backend/tests/test_kh03_r5_c1_scope_binding.py",
    "aurum-desk/backend/tests/test_kh03_r5_c1_deadline_cancel_and_budget.py",
    "aurum-desk/backend/tests/test_kh03_r5_c1_resource_fail_closed.py",
    "aurum-desk/backend/tests/test_kh03_r5_c1_custody_abort_and_determinism.py",
    "aurum-desk/backend/tests/test_kh03_r5_c1_independent_verifier_mutations.py",
    "aurum-desk/backend/tests/test_kh03_r5_c2_fault_control_truth.py",
    "aurum-desk/backend/tests/test_kh03_r5_c2_worker_lifecycle.py",
    "aurum-desk/backend/tests/test_kh03_r5_c2_grant_replay.py",
    "aurum-desk/backend/tests/test_kh03_r5_c2_verifier_mutations.py",
    "aurum-desk/backend/tests/test_kh03_r5_c2_async_resource_regression.py",
)

REQUIRED_ARTIFACTS_KH03_R5_C2: Tuple[str, ...] = (
    "00_preflight_branch_sha_resource_lock.json",
    "01_function_ownership_keep_as_is_manifest.json",
    "02_prior_dataset_sealed_integrity_no_oos.json",
    "03_baseline_c1_red_fault_truth_worker_replay.json",
    "04_c1_all48_control_inventory_and_correction_ledger.json",
    "05_paired_fault_execution_receipts.jsonl.gz",
    "06_legacy_c1_certification_gate_closure.json",
    "07_worker_lifecycle_actual_entry_exit_traces.jsonl.gz",
    "08_cancellation_deadline_drain_quarantine_receipts.json",
    "09_grant_identity_and_one_shot_ledger_receipts.json",
    "10_concurrent_replay_request_byte_conservation.json",
    "11_restart_boundary_live_block_and_provenance.json",
    "12_r4_replay_decimal_economic_bridge.json",
    "13_six_cost_scenarios_and_arm_regression.json",
    "14_A1_SI8_adapter_D4_full_parity.json",
    "15_full_pytest_fresh_collection_junit_bijection.json",
    "16_W1_W2_W4_W1repeat_benchmark_policy.json",
    "17_actual_resource_telemetry_cleanup.json",
    "18_new_and_inherited_fault_control_truth_summary.json",
    "19_separate_OS_mutation_results.json",
    "20_orthogonal_gates_and_verdicts.json",
    "22_phase1_code_commit_push_receipt.json",
    "README.md",
    "HANDOVER_KH03_R5_C2_VI.txt",
)

STALE_PRIOR_SHAS_R5_C2: Tuple[str, ...] = (
    *STALE_PRIOR_SHAS_R5_C1,
    PRIOR_C1_TESTED_EXECUTABLE_SHA,
    PRIOR_C1_EVIDENCE_COMMIT_SHA,
    BASELINE_REVIEWED_HEAD_SHA_C2,
)
STALE_PRIOR_RUN_IDS_R5_C2: Tuple[str, ...] = (
    *STALE_PRIOR_RUN_IDS_R5_C1,
    PRIOR_C1_RUN_ID,
)


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _strict_loads_no_dup_no_nonfinite(text: str) -> Any:
    def _reject_const(tok: str) -> Any:
        raise ValueError(f"NONSTANDARD_JSON_CONSTANT:{tok}")

    def _reject_dups(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k, v in pairs:
            if k in out:
                raise ValueError(f"DUPLICATE_JSON_KEY:{k}")
            out[k] = v
        return out

    return json.loads(text, parse_constant=_reject_const, object_pairs_hook=_reject_dups)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def _dec(v: Any) -> Decimal:
    if isinstance(v, bool) or v is None:
        raise ValueError(f"BAD_DEC:{v!r}")
    if isinstance(v, float) and not math.isfinite(v):
        raise ValueError(f"NONFINITE_DEC:{v!r}")
    d = Decimal(str(v))
    if not d.is_finite():
        raise ValueError(f"NONFINITE_DEC:{v!r}")
    return d


def _q4(d: Decimal) -> float:
    return float(d.quantize(Q4, rounding=ROUND_HALF_UP))


def _audit_paired_fault_receipts_05_c2(
    run_dir: Path,
    f18: Mapping[str, Any],
    *,
    expected_run_id: str,
    expected_tested_sha: str,
) -> Dict[str, Any]:
    """Independently streams 05_paired_fault_execution_receipts.jsonl.gz and re-evaluates every predicate."""
    gz_p = run_dir / "05_paired_fault_execution_receipts.jsonl.gz"
    if not gz_p.is_file() or gz_p.stat().st_size == 0:
        return {"valid": False, "status": "FAIL", "repaired_c1_verified_count": 0, "new_c2_verified_count": 0, "failed_controls": 1, "violations": ["C2V_MISSING_PAIRED_FAULT_RECEIPTS_GZ"]}
    viols: List[str] = []
    if f18.get("paired_receipts_gz_sha256") and _sha256_file(gz_p) != f18["paired_receipts_gz_sha256"]:
        viols.append("C2V_PAIRED_FAULT_RECEIPTS_GZ_SHA_MISMATCH")

    records: List[Dict[str, Any]] = []
    try:
        with gzip.open(gz_p, "rt", encoding="utf-8") as fh:
            for ln_no, raw in enumerate(fh, 1):
                s = raw.strip()
                if not s:
                    continue
                obj = _strict_loads_no_dup_no_nonfinite(s)
                if not isinstance(obj, Mapping):
                    viols.append(f"C2V_PAIRED_RECEIPT_NOT_OBJ:line={ln_no}")
                    continue
                records.append(dict(obj))
    except Exception as exc:
        return {"valid": False, "status": "FAIL", "repaired_c1_verified_count": 0, "new_c2_verified_count": 0, "failed_controls": 1, "violations": [f"C2V_PAIRED_RECEIPTS_PARSE_ERR:{exc}"]}

    if not records:
        return {"valid": False, "status": "FAIL", "repaired_c1_verified_count": 0, "new_c2_verified_count": 0, "failed_controls": 1, "violations": ["C2V_EMPTY_PAIRED_FAULT_RECEIPTS"]}

    verd = derive_fault_control_verdict(
        records,
        expected_executable_sha=expected_tested_sha,
        expected_run_id=expected_run_id,
        stale_shas=STALE_PRIOR_SHAS_R5_C2,
        stale_runs=STALE_PRIOR_RUN_IDS_R5_C2,
    )
    viols.extend(f"C2V_FAULT_PAIR:{v}" for v in (verd.get("violations") or ()))

    c1_ids = [r["fault_id"] for r in records if str(r.get("fault_id") or "").startswith("C1-")]
    c2_ids = [r["fault_id"] for r in records if str(r.get("fault_id") or "").startswith(("C2-D", "C2-E", "C2-F"))]
    c1_ok = len(set(c1_ids)) if verd["valid"] else 0
    c2_ok = len(set(c2_ids)) if verd["valid"] else 0
    if len(set(c1_ids)) != 48:
        viols.append(f"C2V_REPAIRED_C1_COUNT_NE_48:{len(set(c1_ids))}")
    if len(set(c2_ids)) < 43:
        viols.append(f"C2V_NEW_C2_COUNT_LT_43:{len(set(c2_ids))}")
    if verd["counts_by_kind"].get("BASELINE_BUG_RED", 0) < 8:
        viols.append(f"C2V_BASELINE_BUG_RED_COUNT_LT_8:{verd['counts_by_kind'].get('BASELINE_BUG_RED', 0)}")

    return {
        "valid": len(viols) == 0 and bool(verd["valid"]),
        "status": "PASS" if (len(viols) == 0 and verd["valid"]) else "FAIL",
        "total_paired_records": len(records),
        "repaired_c1_verified_count": c1_ok,
        "new_c2_verified_count": c2_ok,
        "counts_by_kind": verd["counts_by_kind"],
        "unique_execution_ids_count": verd["unique_execution_ids_count"],
        "unique_positive_execution_ids_count": verd["unique_positive_execution_ids_count"],
        "failed_controls": int(verd["failed_controls"]),
        "violations": viols,
    }


def _audit_worker_lifecycle_traces_07_c2(run_dir: Path, f08: Mapping[str, Any]) -> Dict[str, Any]:
    """Independently streams 07_worker_lifecycle_actual_entry_exit_traces.jsonl.gz and verifies invariants (M09..M15)."""
    gz_p = run_dir / "07_worker_lifecycle_actual_entry_exit_traces.jsonl.gz"
    if not gz_p.is_file() or gz_p.stat().st_size == 0:
        return {"valid": False, "status": "FAIL", "traces_count": 0, "violations": ["C2V_MISSING_WORKER_LIFECYCLE_TRACES_GZ"]}
    viols: List[str] = []
    if f08.get("worker_traces_gz_sha256") and _sha256_file(gz_p) != f08["worker_traces_gz_sha256"]:
        viols.append("C2V_WORKER_TRACES_GZ_SHA_MISMATCH")

    traces: List[Dict[str, Any]] = []
    try:
        with gzip.open(gz_p, "rt", encoding="utf-8") as fh:
            for ln_no, raw in enumerate(fh, 1):
                s = raw.strip()
                if not s:
                    continue
                row = _strict_loads_no_dup_no_nonfinite(s)
                if not isinstance(row, Mapping):
                    viols.append(f"C2V_WORKER_TRACE_NOT_OBJ:line={ln_no}")
                    continue
                traces.append(dict(row))
    except Exception as exc:
        return {"valid": False, "status": "FAIL", "traces_count": 0, "violations": [f"C2V_WORKER_TRACES_PARSE_ERR:{exc}"]}

    if not traces:
        return {"valid": False, "status": "FAIL", "traces_count": 0, "violations": ["C2V_EMPTY_WORKER_LIFECYCLE_TRACES"]}

    seen_blocked_quarantined = False
    for tr in traces:
        sc = str(tr.get("scenario_id") or "UNKNOWN")
        lc = tr.get("worker_lifecycle_receipt")
        if not isinstance(lc, Mapping):
            viols.append(f"C2V_MISSING_WORKER_LIFECYCLE_RECEIPT:{sc}")
            continue
        atts = lc.get("attempts")
        if not isinstance(atts, list):
            viols.append(f"C2V_MISSING_WORKER_ATTEMPTS:{sc}")
            continue

        # Recompute terminal state conservation from raw attempt records!
        c_run = c_q = c_ex = c_can = 0
        for att in atts:
            st = str(att.get("state") or "")
            sm = att.get("started_mono")
            em = att.get("exited_mono")
            to_s = att.get("effective_timeout_sec_at_start")
            if st == "CANCELLED_BEFORE_START":
                c_can += 1
                if sm is not None:
                    viols.append(f"C2V_CANCELLED_BEFORE_START_HAS_START_TIME:{sc}:{att.get('attempt_id')}")
            elif st in ("EXITED_OK", "EXITED_ERROR"):
                c_ex += 1
                if not _is_num(sm) or not _is_num(em):
                    viols.append(f"C2V_EXITED_WORKER_MISSING_TIMESTAMPS:{sc}:{att.get('attempt_id')}")
                elif float(em) < float(sm):
                    viols.append(f"C2V_WORKER_EXIT_BEFORE_ENTRY:{sc}:{att.get('attempt_id')}")
                if to_s is not None and (not _is_num(to_s) or float(to_s) <= 0.0):
                    viols.append(f"C2V_INVALID_ACTUAL_START_TIMEOUT:{sc}:{att.get('attempt_id')}")
            elif st in ("STARTED", "STILL_RUNNING_AT_RETURN"):
                c_run += 1
                if to_s is not None and (not _is_num(to_s) or float(to_s) <= 0.0):
                    viols.append(f"C2V_INVALID_ACTUAL_START_TIMEOUT:{sc}:{att.get('attempt_id')}")
            elif st in ("QUEUED", "QUARANTINED"):
                c_q += 1
            else:
                viols.append(f"C2V_UNKNOWN_WORKER_STATE:{sc}:{st!r}")

        rep_undrained = lc.get("undrained_inflight_workers")
        rep_run = lc.get("workers_running_actual")
        rep_sub = lc.get("total_workers_submitted")
        if not _is_int(rep_sub) or int(rep_sub) != len(atts) or len(atts) != (c_run + c_q + c_ex + c_can):
            viols.append(f"C2V_WORKER_STATE_CONSERVATION_MISMATCH:{sc}")
        if c_run > 0 and (not _is_int(rep_undrained) or int(rep_undrained) < c_run or not _is_int(rep_run) or int(rep_run) < c_run):
            viols.append(f"C2V_WORKER_RUNNING_CLAIMED_ZERO_UNDRAINED:{sc}")
        if (c_run + c_q) > 0:
            if lc.get("owned_executor_shutdown_complete") is not False or lc.get("session_quarantined") is not True:
                viols.append(f"C2V_ORPHAN_OR_QUARANTINE_HIDDEN:{sc}")
            if tr.get("capture_execution_status") == "QA_FIXTURE_OFFLINE_PASS":
                viols.append(f"C2V_UNDRAINED_SESSION_CLAIMED_PASS:{sc}")
            seen_blocked_quarantined = True

        # M13: aborted trace MUST NEVER have accepted items!
        if tr.get("aborted") is True:
            acc_n = tr.get("accepted_records_count", 0)
            if not _is_int(acc_n) or int(acc_n) != 0:
                viols.append(f"C2V_LATE_ACCEPTED_ITEM_AFTER_ABORT:{sc}")

    if not seen_blocked_quarantined:
        viols.append("C2V_NO_BLOCKED_WORKER_QUARANTINE_TRACE_WITNESSED")

    return {
        "valid": len(viols) == 0,
        "status": "PASS" if not viols else "FAIL",
        "traces_count": len(traces),
        "violations": viols,
    }


def _audit_r4_replay_ledger_12_c2(run_dir: Path, f12: Mapping[str, Any]) -> Dict[str, Any]:
    viols: List[str] = []
    rel_gz = str(f12.get("forensic_ledger_rel_path") or "logs/r4_replay_forensic_ledger_1x.jsonl.gz")
    gz_p = run_dir / rel_gz
    if not gz_p.is_file() or gz_p.stat().st_size == 0:
        return {"valid": False, "status": "FAIL", "violations": ["C2V_MISSING_R4_REPLAY_LEDGER_GZ"]}
    if _sha256_file(gz_p) != f12.get("forensic_ledger_file_sha256"):
        viols.append("C2V_R4_REPLAY_LEDGER_SHA_MISMATCH")

    seen: set[str] = set()
    n_cl = n_w = n_l = n_op = n_gp = over250 = 0
    g_sum = f_sum = n_sum = op_f_sum = Decimal("0")
    try:
        with gzip.open(gz_p, "rt", encoding="utf-8") as fh:
            for ln_no, raw in enumerate(fh, 1):
                s = raw.strip()
                if not s:
                    continue
                r = _strict_loads_no_dup_no_nonfinite(s)
                oid = str(r.get("opportunity_id") or "")
                if not oid or oid in seen:
                    viols.append(f"C2V_R4_LEDGER_DUP_ID:line={ln_no}")
                seen.add(oid)
                if not bool(r.get("filled")):
                    continue
                lbl = str(r.get("outcome_label") or "")
                if lbl in ("WIN", "LOSS"):
                    n_cl += 1
                    g, tf, ne = _dec(r["realized_gross_pnl_usd"]), _dec(r["total_fees_usd"]), _dec(r["realized_net_pnl_usd"])
                    if abs((g - tf) - ne) > Decimal("0.001"):
                        viols.append(f"C2V_R4_LEDGER_RESIDUAL_BREACH:line={ln_no}")
                    g_sum += g; f_sum += tf; n_sum += ne
                    if lbl == "WIN":
                        n_w += 1
                    else:
                        n_l += 1
                        if abs(ne) > Decimal("2.50"):
                            over250 += 1
                elif lbl == "OPEN_AT_HORIZON":
                    n_op += 1
                    op_f_sum += _dec(r.get("accrued_open_entry_fee_usd", 0))
                elif lbl == "DATA_GAP_UNRESOLVED":
                    n_gp += 1
    except Exception as exc:
        return {"valid": False, "status": "FAIL", "violations": [f"C2V_R4_LEDGER_PARSE_ERR:{exc}"]}

    if (n_cl, n_w, n_l, n_op, n_gp) != (203, 7, 196, 28, 2):
        viols.append(f"C2V_R4_ANCHOR_COUNT_DRIFT:{(n_cl, n_w, n_l, n_op, n_gp)}")
    if abs(g_sum - Decimal("-164.9541")) > Decimal("0.01") or abs(f_sum - Decimal("276.6577")) > Decimal("0.01") or abs(n_sum - Decimal("-441.6118")) > Decimal("0.01"):
        viols.append("C2V_R4_DECIMAL_BRIDGE_DRIFT")
    if abs(op_f_sum - Decimal("14.7151")) > Decimal("0.01"):
        viols.append("C2V_R4_OPEN_FEE_DRIFT")
    if (
        not _is_int(f12.get("null_1x_closed")) or int(f12["null_1x_closed"]) != n_cl
        or not _is_num(f12.get("null_1x_net_usd")) or abs(_dec(f12["null_1x_net_usd"]) - n_sum) > Decimal("0.002")
        or not _is_int(f12.get("realized_losses_gt_2p50_count")) or int(f12["realized_losses_gt_2p50_count"]) != over250
    ):
        viols.append("C2V_12_TAMPERED_VS_RAW_LEDGER")
    if f12.get("planned_cap_is_physical_guarantee") is not False:
        viols.append("C2V_PLANNED_CAP_GUARANTEE_FORGED")
    if f12.get("RESEARCH_ECONOMIC_VERDICT") != "NO_PROVEN_EDGE" or f12.get("KH04_ENTRY") != "BLOCKED_NO_DEV_CANDIDATE" or not _is_int(f12.get("OOS_EVALUATIONS")) or int(f12["OOS_EVALUATIONS"]) != 0:
        viols.append("C2V_FORGED_ECONOMIC_OR_OOS_IN_12")
    return {"valid": len(viols) == 0, "status": "PASS" if not viols else "FAIL", "recomputed_net_usd": _q4(n_sum), "violations": viols}


def _verify_benchmark_16_c2(
    raw_doc: Mapping[str, Any],
    rep_pol: Mapping[str, Any],
    *,
    kind: str,
    expected_run_id: str,
    expected_tested_sha: str,
    max_safe_cap: int,
) -> Dict[str, Any]:
    viols: List[str] = []
    if not isinstance(raw_doc, Mapping) or not isinstance(rep_pol, Mapping):
        return {"valid": False, "violations": [f"C2V_BENCH_MISSING:{kind}"]}
    if raw_doc.get("run_id") != expected_run_id or raw_doc.get("tested_executable_sha") != expected_tested_sha:
        viols.append(f"C2V_BENCH_PROVENANCE_MISMATCH:{kind}")
    if raw_doc.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH02:
        viols.append(f"C2V_BENCH_DATASET_SHA_MISMATCH:{kind}")
    try:
        recomp = recompute_worker_policy_from_raw_receipt(
            raw_doc,
            reported_policy=rep_pol,
            benchmark_kind=kind,
            expected_run_id=expected_run_id,
            expected_tested_sha=expected_tested_sha,
            system_limits=rep_pol.get("cap_components") if isinstance(rep_pol.get("cap_components"), Mapping) else None,
        )
    except Exception as exc:
        return {"valid": False, "violations": [f"C2V_BENCH_RECOMPUTE_ERR:{kind}:{exc}"]}
    if not recomp.get("valid") or recomp.get("status") != "PASS":
        viols.extend(f"C2V_BENCH:{kind}:{v}" for v in (recomp.get("discrepancies") or ["RECOMPUTE_FAILED"]))
    aw = rep_pol.get("applied_worker_count")
    sc = rep_pol.get("safe_worker_cap")
    if not _is_int(aw) or not _is_int(sc) or int(aw) < 1 or int(aw) > max_safe_cap or int(sc) > max_safe_cap or int(aw) > int(sc):
        viols.append(f"C2V_BENCH_SAFE_CAP_EXCEEDED:{kind}")
    if rep_pol.get("full_suite_speedup_extrapolated") is not False or rep_pol.get("timing_determinism_claimed") is not False:
        viols.append(f"C2V_BENCH_EXTRAPOLATION_FORBIDDEN:{kind}")
    return {"valid": len(viols) == 0, "recomputed_policy_summary": recomp.get("recomputed_policy"), "violations": viols}


def _verify_junit_raw_xml_15_c2(
    run_dir: Path, f15: Mapping[str, Any], *, expected_run_id: str, expected_tested_sha: str
) -> Tuple[bool, List[str]]:
    viols: List[str] = []
    nodes_rel = str(f15.get("collected_nodeids_gz_rel") or "")
    nodes_p = (run_dir / nodes_rel) if nodes_rel else None
    c_ids: List[str] = []
    if nodes_p is not None and nodes_p.is_file():
        if _sha256_file(nodes_p) != f15.get("collected_nodeids_gz_sha256"):
            viols.append("C2V_JUNIT_NODEIDS_GZ_SHA_MISMATCH")
        with gzip.open(nodes_p, "rt", encoding="utf-8") as fh:
            for ln in fh:
                s = ln.strip()
                if s:
                    c_ids.append(s)
    else:
        viols.append("C2V_JUNIT_MISSING_COLLECTED_NODEIDS_GZ")

    sm = f15.get("shard_manifest") or {}
    if not sm:
        return False, viols + ["C2V_JUNIT_MISSING_SHARD_MANIFEST"]
    if sm.get("run_id") != expected_run_id or sm.get("tested_executable_sha") != expected_tested_sha:
        viols.append("C2V_JUNIT_SM_PROVENANCE_MISMATCH")
    if not _is_int(sm.get("nonzero_exit_shards_count")) or int(sm["nonzero_exit_shards_count"]) != 0:
        viols.append("C2V_JUNIT_NONZERO_SHARDS")

    if len(c_ids) < MIN_EXPECTED_PYTEST_COLLECTED_R5_C2:
        viols.append(f"C2V_JUNIT_COLLECTED_BELOW_MIN:{len(c_ids)}<{MIN_EXPECTED_PYTEST_COLLECTED_R5_C2}")
    if len(set(c_ids)) != len(c_ids):
        viols.append("C2V_JUNIT_COLLECTED_DUPLICATES")

    shards = list(sm.get("shards") or ())
    if not shards:
        return False, viols + ["C2V_JUNIT_ZERO_SHARDS"]

    sched_all: List[str] = []
    tot_t = tot_f = tot_e = tot_s = mand_s = 0
    for sh in shards:
        if int(sh.get("exit_code", 1)) != 0 or bool(sh.get("timed_out", True)) or bool(sh.get("reused", True)):
            viols.append(f"C2V_JUNIT_BAD_SHARD:{sh.get('shard_id')}")
        rel_x = str(sh.get("junit_xml_path") or "")
        xp = run_dir / rel_x
        if not xp.is_file() or xp.stat().st_size == 0:
            viols.append(f"C2V_JUNIT_XML_MISSING:{rel_x}")
            continue
        if _sha256_file(xp) != str(sh.get("xml_sha256") or sh.get("junit_sha256") or ""):
            viols.append(f"C2V_JUNIT_XML_SHA_MISMATCH:{rel_x}")
        sched = list(sh.get("scheduled_node_ids") or ())
        sched_all.extend(sched)
        try:
            root = ET.fromstring(xp.read_text("utf-8"))
        except Exception as exc:
            viols.append(f"C2V_JUNIT_XML_PARSE_ERR:{rel_x}:{exc}")
            continue
        tcs = list(root.iter("testcase"))
        tot_t += len(tcs)
        if len(tcs) != len(sched):
            viols.append(f"C2V_JUNIT_SHARD_LEN_MISMATCH:{sh.get('shard_id')}")
        for tc in tcs:
            cn, nm = str(tc.attrib.get("classname") or ""), str(tc.attrib.get("name") or "")
            if tc.find("failure") is not None:
                tot_f += 1
            if tc.find("error") is not None:
                tot_e += 1
            if tc.find("skipped") is not None:
                tot_s += 1
                if any(tok in cn or tok in nm for tok in ("test_kh_02_r2", "test_kh03_r3", "test_kh03_r4", "test_kh03_r5")):
                    mand_s += 1

    if sorted(sched_all) != sorted(c_ids):
        viols.append("C2V_JUNIT_BIJECTION_MISMATCH")
    tot_p = tot_t - tot_f - tot_e - tot_s
    if (
        not _is_int(f15.get("executed_count")) or tot_t != int(f15["executed_count"])
        or not _is_int(f15.get("passed_count")) or tot_p != int(f15["passed_count"])
        or not _is_int(f15.get("skipped_count")) or tot_s != int(f15["skipped_count"])
        or not _is_int(f15.get("failed_count")) or tot_f != int(f15["failed_count"])
        or not _is_int(f15.get("error_count")) or tot_e != int(f15["error_count"])
    ):
        viols.append("C2V_JUNIT_COUNTS_TAMPERED_VS_RAW_XML")
    if tot_f != 0 or tot_e != 0 or mand_s != 0:
        viols.append(f"C2V_JUNIT_FAIL_ERR_OR_MANDATORY_SKIP:f={tot_f},e={tot_e},ms={mand_s}")
    return len(viols) == 0, viols


def _audit_telemetry_gz_vs_17_c2(mem_gz: Path, f17: Mapping[str, Any]) -> Dict[str, Any]:
    viols: List[str] = []
    if not mem_gz.is_file() or mem_gz.stat().st_size == 0:
        return {"status": "FAIL_MISSING_LOG", "samples_count": 0, "below_floor_samples_count": 1, "max_workers_observed_in_samples": 99, "violations": ["C2V_MISSING_TELEMETRY_LOG"]}
    n, min_ram, min_dk, max_ch, below_r, below_d = 0, float("inf"), float("inf"), 0, 0, 0
    try:
        with gzip.open(mem_gz, "rt", encoding="utf-8") as fh:
            for ln in fh:
                s = ln.strip()
                if not s:
                    continue
                row = _strict_loads_no_dup_no_nonfinite(s)
                n += 1
                rm = row.get("available_mib")
                if rm is None and row.get("available_ram_bytes") is not None:
                    rm = float(row["available_ram_bytes"]) / (1024.0 * 1024.0)
                if _is_num(rm):
                    rf = float(rm)
                    min_ram = min(min_ram, rf)
                    if rf < HARD_RAM_FLOOR_MIB_R5_C2:
                        below_r += 1
                dk = row.get("disk_free_gib")
                if _is_num(dk):
                    df = float(dk)
                    min_dk = min(min_dk, df)
                    if df < STRICT_RUNTIME_DISK_MIN_GIB_R5:
                        below_d += 1
                ch = row.get("child_count")
                if _is_int(ch):
                    max_ch = max(max_ch, int(ch))
    except Exception as exc:
        return {"status": "FAIL_PARSE", "samples_count": n, "below_floor_samples_count": 1, "max_workers_observed_in_samples": 99, "violations": [f"C2V_TELEMETRY_PARSE_ERR:{exc}"]}

    if n == 0 or min_ram == float("inf") or min_dk == float("inf"):
        viols.append("C2V_TELEMETRY_EMPTY_OR_INCOMPLETE")
    if below_r > 0 or min_ram < HARD_RAM_FLOOR_MIB_R5_C2:
        viols.append(f"C2V_TELEMETRY_RAM_BELOW_512MIB:{min_ram:.2f}")
    if below_d > 0 or min_dk < STRICT_RUNTIME_DISK_MIN_GIB_R5:
        viols.append(f"C2V_TELEMETRY_DISK_BELOW_20GIB:{min_dk:.4f}")
    if max_ch > GLOBAL_MAX_DESCENDANTS_R5_C2:
        viols.append(f"C2V_TELEMETRY_CHILD_EXCEEDED_4:{max_ch}")
    rep_ram, rep_dk, rep_ch = f17.get("min_available_ram_mib"), f17.get("min_disk_free_gib"), f17.get("max_descendant_children")
    if not _is_num(rep_ram) or abs(float(rep_ram) - min_ram) > 1.0:
        viols.append("C2V_TELEMETRY_17_RAM_MUTATED_VS_GZ")
    if not _is_num(rep_dk) or abs(float(rep_dk) - min_dk) > 0.05:
        viols.append("C2V_TELEMETRY_17_DISK_MUTATED_VS_GZ")
    if not _is_int(rep_ch) or int(rep_ch) < max_ch or int(rep_ch) > GLOBAL_MAX_DESCENDANTS_R5_C2:
        viols.append("C2V_TELEMETRY_17_CHILDREN_MUTATED_VS_GZ")
    return {
        "status": "PASS" if len(viols) == 0 else "FAIL",
        "samples_count": n,
        "min_available_ram_mib": round(min_ram if min_ram != float("inf") else 0.0, 2),
        "min_disk_free_gib": round(min_dk if min_dk != float("inf") else 0.0, 4),
        "max_workers_observed_in_samples": max_ch,
        "below_floor_samples_count": below_r + below_d,
        "violations": viols,
    }


def verify_r5_c2_artifacts_independently_from_disk(
    run_dir: Path,
    *,
    expected_run_id: Optional[str] = None,
    expected_tested_sha: Optional[str] = None,
    write_manifest: bool = True,
) -> Tuple[int, Dict[str, Any]]:
    run_dir = Path(run_dir).resolve()
    verif_pid = os.getpid()
    viols: List[str] = []
    docs: Dict[str, Dict[str, Any]] = {}
    file_hashes: Dict[str, Dict[str, Any]] = {}

    for rel in REQUIRED_ARTIFACTS_KH03_R5_C2:
        p = run_dir / rel
        if not p.is_file() or p.stat().st_size == 0:
            if rel == "05_paired_fault_execution_receipts.jsonl.gz":
                viols.append("C2V_MISSING_PAIRED_FAULT_RECEIPTS_GZ")
            elif rel == "07_worker_lifecycle_actual_entry_exit_traces.jsonl.gz":
                viols.append("C2V_MISSING_WORKER_LIFECYCLE_TRACES_GZ")
            else:
                viols.append(f"C2V_MISSING_OR_EMPTY_ARTIFACT:{rel}")
            continue
        file_hashes[rel] = {"sha256": _sha256_file(p), "bytes": p.stat().st_size}
        if rel.endswith(".json"):
            try:
                docs[rel[:2]] = _strict_loads_no_dup_no_nonfinite(p.read_text("utf-8"))
            except Exception as exc:
                viols.append(f"C2V_INVALID_JSON:{rel}:{exc}")

    for dk in ("00", "01", "02", "03", "04", "06", "08", "09", "10", "11", "12", "13", "14", "15", "16", "17", "18", "19", "20", "22"):
        if dk not in docs:
            viols.append(f"C2V_MISSING_JSON_DOC:{dk}")

    if viols:
        return 1, {"spec_version": SPEC_VERSION_KH03_R5_C2, "verifier_pid": verif_pid, "status": "FAIL_MISSING_OR_INVALID_ARTIFACTS", "violations": viols}

    f00, f01, f02, f03, f04, f06, f08, f09, f10, f11, f12, f13, f14, f15, f16, f17, f18, f19, f20, f22 = (
        docs["00"], docs["01"], docs["02"], docs["03"], docs["04"], docs["06"], docs["08"], docs["09"],
        docs["10"], docs["11"], docs["12"], docs["13"], docs["14"], docs["15"], docs["16"], docs["17"],
        docs["18"], docs["19"], docs["20"], docs["22"],
    )
    rid = expected_run_id or str(f00.get("run_id") or "")
    tsha = expected_tested_sha or str(f00.get("tested_executable_sha") or "")

    if rid in STALE_PRIOR_RUN_IDS_R5_C2 or tsha in STALE_PRIOR_SHAS_R5_C2:
        viols.append("C2V_STALE_PRIOR_RUN_OR_SHA_REUSED")
    orch_pid = f00.get("orchestrator_pid")
    if _is_int(orch_pid) and int(orch_pid) == verif_pid:
        viols.append("C2V_SAME_PID_AS_ORCHESTRATOR")

    for dk, doc in sorted(docs.items()):
        if doc.get("run_id") != rid:
            viols.append(f"C2V_RUN_ID_MISMATCH:{dk}")
        if doc.get("tested_executable_sha") != tsha:
            viols.append(f"C2V_TESTED_SHA_MISMATCH:{dk}")
        if doc.get("run_id") in STALE_PRIOR_RUN_IDS_R5_C2 or doc.get("tested_executable_sha") in STALE_PRIOR_SHAS_R5_C2:
            viols.append(f"C2V_STALE_PRIOR_RUN_OR_SHA_REUSED:{dk}")

    # Keep-as-is & prior sealed immutability
    if f02.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH02 or f02.get("canonical_dataset_sha256_match") is not True or f02.get("all_prior_sealed_artifacts_intact") is not True:
        viols.append("C2V_DATASET_OR_PRIOR_SHA_MISMATCH")
    if not _is_int(f01.get("locked_files_modified_count")) or int(f01["locked_files_modified_count"]) != 0 or not _is_int(f01.get("unauthorized_changed_files_count")) or int(f01["unauthorized_changed_files_count"]) != 0 or len(f01.get("forbidden_order_import_hits") or []) != 0:
        viols.append("C2V_PROTECTED_SOURCE_MODIFIED_OR_ORDER_IMPORT")

    # Baseline C1 RED (03) & C1 48 correction ledger (04)
    if f03.get("all_c1_def_red_reproduced") is not True or f03.get("p1_d_red_reproduced") is not True or f03.get("p1_e_red_reproduced") is not True or f03.get("p1_f_red_reproduced") is not True:
        viols.append("C2V_MISSING_OR_FORGED_C1_RED_PROOF")
    if not _is_int(f04.get("total_c1_controls_audited")) or int(f04["total_c1_controls_audited"]) != 48 or f04.get("historical_c1_artifacts_mutated") is not False:
        viols.append("C2V_C1_ALL48_CORRECTION_LEDGER_INVALID")

    # Independent paired fault execution receipts (05, catching M01..M08!)
    pf_recomp = _audit_paired_fault_receipts_05_c2(run_dir, f18, expected_run_id=rid, expected_tested_sha=tsha)
    viols.extend(pf_recomp.get("violations") or ())

    # M22: Legacy C1 certification gate closure (06)
    if (
        f06.get("legacy_c1_gate_rejects_boolean_only_proof") is not True
        or f06.get("legacy_c1_verifier_rejects_boolean_only_proof") is not True
        or f06.get("ast_certification_path_no_literal_flags") is not True
        or f06.get("historical_c1_artifacts_modified") is not False
    ):
        viols.append("C2V_LEGACY_C1_CERTIFICATION_NOT_CLOSED")

    # Independent worker lifecycle traces (07 + 08, catching M09..M15!)
    wt_recomp = _audit_worker_lifecycle_traces_07_c2(run_dir, f08)
    viols.extend(wt_recomp.get("violations") or ())
    if f08.get("actual_start_remaining_timeout_verified") is not True:
        viols.append("C2V_INVALID_ACTUAL_START_TIMEOUT")
    if f08.get("blocked_worker_claimed_shutdown_complete") is not False or f08.get("blocked_worker_marked_fail_and_quarantined") is not True:
        viols.append("C2V_ORPHAN_OR_QUARANTINE_HIDDEN")
    if f08.get("late_success_after_abort_zero_accepted") is not True or f08.get("failed_unlink_quarantined_honestly") is not True:
        viols.append("C2V_LATE_ACCEPTED_ITEM_AFTER_ABORT")

    # P1-F Grant identity, replay, conservation & live hard-block (09, 10, 11, catching M16..M21!)
    if f09.get("same_id_changed_payload_conflict_denied") is not True or f09.get("stable_logical_identity_verified") is not True:
        viols.append("C2V_GRANT_PAYLOAD_CONFLICT_ACCEPTED")
    if f09.get("sequential_replay_one_winner_only") is not True or f09.get("second_plan_from_same_scope_denied") is not True or f09.get("reissued_scope_receipt_same_grant_denied") is not True:
        viols.append("C2V_GRANT_REPLAY_OR_REISSUE_ALLOWED")
    if not _is_int(f10.get("concurrent_race_winners_count")) or int(f10["concurrent_race_winners_count"]) != 1:
        viols.append("C2V_MULTIPLE_SAME_GRANT_SESSION_WINNERS")
    if (
        not _is_int(f10.get("concurrent_race_total_calls")) or not _is_int(f10.get("concurrent_race_cap"))
        or int(f10["concurrent_race_total_calls"]) > int(f10["concurrent_race_cap"])
        or f10.get("duplicate_attempt_settle_release_blocked") is not True
    ):
        viols.append("C2V_REPLAY_CAP_RESET_OR_FREE_RETRY")
    if (
        f10.get("unknown_bytes_conservatively_retained") is not True
        or not _is_int(f10.get("uncertain_bytes_retained_observed")) or int(f10["uncertain_bytes_retained_observed"]) <= 0
    ):
        viols.append("C2V_UNKNOWN_BYTES_SETTLED_AS_ZERO")
    if f10.get("no_negative_ledger_counters") is not True or any(
        _is_int(f10.get(ck)) and int(f10[ck]) < 0
        for ck in ("concurrent_race_total_calls", "uncertain_bytes_retained_observed", "ledger_min_counter_observed")
    ):
        viols.append("C2V_LEDGER_OVER_RELEASE_OR_NEGATIVE_COUNTER")

    # M21: Live hard-block & offline trust boundary (11 + 20)
    if (
        f11.get("live_hard_blocked_even_with_stub_verifier") is not True
        or f11.get("live_grant_replay_durability") != "NOT_PROVEN_BLOCKED"
        or f11.get("live_transport_termination") != "NOT_PROVEN_BLOCKED"
        or not _is_int(f11.get("external_market_requests_attempted")) or int(f11["external_market_requests_attempted"]) != 0
        or not _is_int(f11.get("accepted_real_observed_records")) or int(f11["accepted_real_observed_records"]) != 0
        or not _is_int(f11.get("oos_reads_executed")) or int(f11["oos_reads_executed"]) != 0
        or not _is_int(f11.get("oos_evaluations")) or int(f11["oos_evaluations"]) != 0
    ):
        viols.append("C2V_LIVE_PROMOTED_FROM_OFFLINE_OR_NONDURABLE")

    # Resource/cleanup & remote pub honesty
    if (
        f00.get("owner_disk_preflight_waiver") is True
        or f17.get("owner_disk_preflight_waiver") is True
        or not _is_int(f17.get("orphan_children_after_cleanup")) or int(f17["orphan_children_after_cleanup"]) != 0
        or not _is_int(f17.get("active_capture_workers_after_run")) or int(f17["active_capture_workers_after_run"]) != 0
        or not _is_int(f17.get("quarantined_capture_workers_after_run")) or int(f17["quarantined_capture_workers_after_run"]) != 0
    ):
        viols.append("C2V_ORPHAN_OR_QUARANTINE_HIDDEN")
    if (
        (bool(f22.get("remote_readback_sha256_verified")) and not f22.get("publication_head_sha"))
        or (f22.get("ci_status") == "CI_PASS" and f22.get("actual_github_actions_run_verified") is not True)
        or (f22.get("tested_executable_sha") != tsha)
    ):
        viols.append("C2V_FALSE_REMOTE_PUBLICATION_OR_WRONG_SHA")

    # Independent R4 replay ledger Decimal verification (12 + 13, M24!)
    r4_au = _audit_r4_replay_ledger_12_c2(run_dir, f12)
    viols.extend(r4_au.get("violations") or ())
    led_p = run_dir / str(f12.get("forensic_ledger_rel_path") or "logs/r4_replay_forensic_ledger_1x.jsonl.gz")
    if led_p.is_file():
        file_hashes[str(led_p.relative_to(run_dir)).replace("\\", "/")] = {"sha256": _sha256_file(led_p), "bytes": led_p.stat().st_size}

    # Independent 0.5s telemetry verification (17)
    mem_gz = run_dir / str(f17.get("memory_samples_log_rel") or "logs/runtime_memory_samples_0p5s.jsonl.gz")
    mem_au = _audit_telemetry_gz_vs_17_c2(mem_gz, f17)
    viols.extend(mem_au.get("violations") or ())
    if mem_gz.is_file():
        file_hashes[str(mem_gz.relative_to(run_dir)).replace("\\", "/")] = {"sha256": _sha256_file(mem_gz), "bytes": mem_gz.stat().st_size}

    # Independent W1/W2/W4/W1_repeat benchmark verification (16)
    if f16.get("status") == "NOT_RUN_BLOCKED_LOW_DISK":
        p_rec = {"valid": False, "violations": ["NOT_RUN_BLOCKED_LOW_DISK"]}
        s_rec = {"valid": False, "violations": ["NOT_RUN_BLOCKED_LOW_DISK"]}
    else:
        p_rec = _verify_benchmark_16_c2(f16.get("pytest_controlled_subset_raw") or {}, f16.get("pytest_worker_policy") or {}, kind=BENCHMARK_KIND_PYTEST, expected_run_id=rid, expected_tested_sha=tsha, max_safe_cap=3)
        s_rec = _verify_benchmark_16_c2(f16.get("science_slice_8000_raw") or {}, f16.get("science_worker_policy") or {}, kind=BENCHMARK_KIND_SCIENCE, expected_run_id=rid, expected_tested_sha=tsha, max_safe_cap=GLOBAL_MAX_DESCENDANTS_R5_C2)
        viols.extend(p_rec.get("violations") or ())
        viols.extend(s_rec.get("violations") or ())

    # Independent JUnit raw XML 1:1 verification (15, M23!)
    if f15.get("status") == "NOT_RUN_BLOCKED_LOW_DISK":
        junit_ok, j_viols = False, []
    else:
        junit_ok, j_viols = _verify_junit_raw_xml_15_c2(run_dir, f15, expected_run_id=rid, expected_tested_sha=tsha)
        viols.extend(j_viols)
        if f15.get("collected_nodeids_gz_rel"):
            np = run_dir / str(f15["collected_nodeids_gz_rel"])
            if np.is_file():
                file_hashes[str(np.relative_to(run_dir)).replace("\\", "/")] = {"sha256": _sha256_file(np), "bytes": np.stat().st_size}

    # Parity anchor check (14)
    if (
        f14.get("BASELINE_PARITY_VERDICT") != "PASS"
        or not _is_int(f14.get("a1_population_count")) or int(f14["a1_population_count"]) != 56710
        or not _is_int(f14.get("a1_mismatches_count")) or int(f14["a1_mismatches_count"]) != 0
        or not _is_int(f14.get("si8_population_count")) or int(f14["si8_population_count"]) != 6375
        or not _is_int(f14.get("si8_mismatches_count")) or int(f14["si8_mismatches_count"]) != 0
        or not _is_int(f14.get("three_adapter_population_count")) or int(f14["three_adapter_population_count"]) != 246
        or not _is_int(f14.get("three_adapter_mismatches_count")) or int(f14["three_adapter_mismatches_count"]) != 0
    ):
        viols.append("C2V_PARITY_ANCHOR_MUTATED")

    # Recompute ALL 32 gates independently!
    recomp = derive_r5_c2_readiness_verdicts(
        raw_evidence=docs,
        mem_audit=mem_au,
        pytest_recomp=p_rec,
        science_recomp=s_rec,
        junit_ok=junit_ok,
        r4_replay_ok=bool(r4_au.get("valid")),
        paired_faults_recomp=pf_recomp,
        worker_traces_recomp=wt_recomp,
        stale_prior_shas=STALE_PRIOR_SHAS_R5_C2,
        stale_prior_run_ids=STALE_PRIOR_RUN_IDS_R5_C2,
        for_final_verifier_manifest=True,
        verifier_violations_count=len(viols),
    )

    rep_g = f20.get("gates_32") or {}
    for gk, gv in recomp["gates_32"].items():
        if gk == "G28_INDEPENDENT_VERIFIER":
            continue
        if gk == "G18_RESTART_TRUST_BOUNDARY":
            if rep_g.get(gk) == "PASS" and gv != "PASS":
                viols.append("C2V_LIVE_PROMOTED_FROM_OFFLINE_OR_NONDURABLE")
            continue
        if rep_g.get(gk) == "PASS" and gv != "PASS":
            viols.append(f"C2V_FORGED_GATE_INFLATION:{gk}:rep=PASS!=recomp={gv}")
    rep_v = f20.get("verdicts") or {}
    if rep_v.get("ENGINEERING") == "PASS" and recomp["verdicts"]["ENGINEERING"] != "PASS":
        viols.append("C2V_FORGED_ENGINEERING_PASS")
    if (
        rep_v.get("LIVE_GRANT_REPLAY_DURABILITY") != "NOT_PROVEN_BLOCKED"
        or rep_v.get("LIVE_TRANSPORT_TERMINATION") != "NOT_PROVEN_BLOCKED"
        or rep_v.get("ECONOMIC") != "NO_PROVEN_EDGE"
        or rep_v.get("ACTIVATION_READY") != "NO"
        or rep_v.get("KH04_ENTRY") != "BLOCKED_NO_DEV_CANDIDATE"
        or not _is_int(rep_v.get("OOS_EVALUATIONS")) or int(rep_v["OOS_EVALUATIONS"]) != 0
        or rep_v.get("CAPTURE_AUTHORIZATION") != "BLOCKED_OWNER_AUTHORIZATION"
    ):
        viols.append("C2V_LIVE_PROMOTED_FROM_OFFLINE_OR_NONDURABLE")

    if viols and recomp["gates_32"]["G28_INDEPENDENT_VERIFIER"] == "PASS":
        recomp["gates_32"]["G28_INDEPENDENT_VERIFIER"] = "FAIL"
        recomp["verdicts"]["ENGINEERING"] = "FAIL"
        recomp["overall_classification"] = "FAIL_CLOSED"

    all_ok = (len(viols) == 0) and (recomp["verdicts"]["ENGINEERING"] == "PASS")
    manifest: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R5_C2,
        "schema_version": SCHEMA_VERSION_KH03_R5_C2,
        "run_id": rid,
        "tested_executable_sha": tsha,
        "verified_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "orchestrator_pid": orch_pid,
        "verifier_pid": verif_pid,
        "distinct_os_process_verified": (_is_int(orch_pid) and int(orch_pid) != verif_pid),
        "independent_paired_faults_audit": pf_recomp,
        "independent_worker_traces_audit": wt_recomp,
        "independent_r4_replay_audit": r4_au,
        "independent_pytest_benchmark_recompute": p_rec,
        "independent_science_benchmark_recompute": s_rec,
        "independent_telemetry_audit": mem_au,
        "independent_junit_bijection_ok": junit_ok,
        "independent_recomputed_32_gates": recomp["gates_32"],
        "independent_recomputed_verdicts": recomp["verdicts"],
        "overall_classification": recomp["overall_classification"],
        "verified_artifact_sha256_manifest": dict(sorted(file_hashes.items())),
        "manifest_excludes_self_from_hash_index": True,
        "violations_count": len(viols),
        "violations": viols,
        "status": "PASS" if all_ok else "FAIL",
    }

    if write_manifest:
        write_json_durable_atomic(run_dir / MANIFEST_21_NAME_KH03_R5_C2, manifest)
    return (0 if all_ok else 1), manifest


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="KH-03.R5.C2 Separate-OS Independent Verifier (G01..G32)")
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--expected-run-id", default=None)
    ap.add_argument("--expected-tested-sha", default=None)
    ap.add_argument("--no-write-manifest", action="store_true")
    args = ap.parse_args(argv)
    rc, res = verify_r5_c2_artifacts_independently_from_disk(
        args.run_dir,
        expected_run_id=args.expected_run_id,
        expected_tested_sha=args.expected_tested_sha,
        write_manifest=not args.no_write_manifest,
    )
    print(json.dumps({
        "status": res["status"],
        "verifier_pid": res["verifier_pid"],
        "violations_count": len(res.get("violations") or ()),
        "violations": res.get("violations") or (),
    }, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
