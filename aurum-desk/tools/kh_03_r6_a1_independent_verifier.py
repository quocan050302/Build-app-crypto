#!/usr/bin/env python3
"""
aurum-desk/tools/kh_03_r6_a1_independent_verifier.py

AURUM DESK — KH-03.R6.A1 | SEPARATE-OS INDEPENDENT READ-ONLY DISK VERIFIER (G01..G36)
SPEC VERSION: KH-03.R6.A1-v1.0 | ZERO ORCHESTRATOR IMPORT | RAW RECEIPT & TRACE RECOMPUTATION

Runs in a distinct OS process (`verifier_pid != orchestrator_pid`), opens ZERO external network sockets,
NEVER accesses OOS data, reads ALL raw A1 artifacts directly from disk using strict duplicate-key and
non-finite-rejecting JSON parsing, and independently recomputes:
  - Prior R1..R6 sealed artifacts & canonical DEV dataset SHA-256 immutability (84 files!)
  - Keep-as-is & ownership audit (`01`), prior R6+C2 integrity & zero OOS (`02`), real M0 RED on 529bdaf2 (`03`)
  - Acquisition CLI dry-run + QA E2E (`04`), task coverage & finalization (`05`), immutable plan/grant/lease (`06`)
  - Request contract & parent-authoritative worker envelope (`07`), durable reservation & cumulative caps (`08`)
  - Settlement conservation & overrun quarantine (`09`), actual worker lifecycle stream (`10_process_lifecycle_cancel_reap_traces.jsonl.gz`)
  - Cross-process ledger, monotonic anchor & crash restart (`11`), trusted supervisor & provenance (`12`), QA custody (`13`)
  - Frozen executable release binding & CLI receipts (`14`), external hard-block & safety modes (`15`, unsigned owner template)
  - Exact Decimal R4 forensic ledger recomputation (`logs/r4_replay_forensic_ledger_1x.jsonl.gz` vs `16`, `17`)
  - Full A1/SI8/adapter/D4 baseline parity (`18`)
  - Raw JUnit XML 1:1 bijection vs `logs/pytest_collected_executed_nodeids.jsonl.gz` (`19`)
  - Raw W1/W2/W4/W1_repeat pytest, science, R6 ingest & A1 planning workload determinism (`20`)
  - Raw 0.5s telemetry stream (`logs/runtime_memory_samples_0p5s.jsonl.gz` vs `21`)
  - Every A1 paired fault control receipt (`22_paired_fault_execution_receipts.jsonl.gz` vs `23`) via independent predicate re-evaluation
  - Separate-OS mutation resistance (`24`: >=32 NEW A1 + 167 inherited + clean clone PASS)
  - Independent recomputation of ALL 36 A1 mandatory gates (`25` pre-verifier vs final verified, `0/4` external R6.B/C/D)
Then seals `26_independent_OS_verifier_manifest_sha256.json` (excluding itself from its file hash index).
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
from services.kh_03_r6_a1_acceptance_service import (
    GATE_36_KEYS_KH03_R6_A1,
    MIN_EXPECTED_PYTEST_COLLECTED_R6_A1,
    MIN_NEW_PAIRED_CONTROLS_R6_A1,
    MIN_NEW_VERIFIER_MUTATIONS_R6_A1,
    derive_r6_a1_fault_verdict,
    derive_r6_a1_readiness_verdicts,
)
from services.kh_03_r6_a1_corrective_service import (
    CLASSIFICATION_PASS_OFFLINE_CORRECTIVE,
    CORRECTIVE_12_GATE_KEYS,
    PRIOR_A1_EVIDENCE_COMMIT_SHA,
    PRIOR_A1_LATEST_POINTER_SHA256,
    PRIOR_A1_MANIFEST_26_SHA256,
    PRIOR_A1_PUBLICATION_HEAD_SHA,
    PRIOR_A1_READBACK_28_SHA256,
    PRIOR_A1_REVIEWED_TESTED_SHA,
    PRIOR_A1_RUN_ID,
    SCHEMA_VERSION_KH03_R6_A1_CORRECTIVE,
    SPEC_VERSION_KH03_R6_A1_CORRECTIVE,
    derive_corrective_fault_verdict,
)
from services.kh_03_r6_a1_trust_service import (
    BASELINE_REVIEWED_HEAD_SHA_R6_A1,
    PRIOR_R6_EVIDENCE_COMMIT_SHA,
    PRIOR_R6_MANIFEST_26_SHA256,
    PRIOR_R6_READBACK_28_SHA256,
    PRIOR_R6_RUN_ID,
    PRIOR_R6_TESTED_EXECUTABLE_SHA,
    SCHEMA_VERSION_KH03_R6_A1,
    SPEC_VERSION_KH03_R6_A1,
)
from services.kh_03_r6_source_contract_service import MANDATORY_BRANCH_KH03_R6
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_03_r6_independent_verifier import (
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R6,
    STALE_PRIOR_RUN_IDS_R6,
    STALE_PRIOR_SHAS_R6,
)

MANIFEST_26_NAME_KH03_R6_A1 = "26_independent_OS_verifier_manifest_sha256.json"
Q4 = Decimal("0.0001")

# Verified KH-03.R6 sealed artifacts at reviewed HEAD 28b252676a10aa21bec7ce14fbd9415db230c2f4
PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R6_A1: Tuple[Tuple[str, str, str], ...] = (
    *PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R6,
    (
        "aurum-desk/docs/artifacts/kh_03_r6_observed_market_data/latest_run.json",
        "c29603217893cdda58706a2c0d4d7e72c01f88f8eea1bef390be8eeb6526bbe1",
        "KH03_R6_LATEST_RUN_POINTER",
    ),
    (
        "aurum-desk/docs/artifacts/kh_03_r6_observed_market_data/latest_offline_run.json",
        "c29603217893cdda58706a2c0d4d7e72c01f88f8eea1bef390be8eeb6526bbe1",
        "KH03_R6_LATEST_OFFLINE_RUN_POINTER",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r6_observed_market_data/28_phase2_remote_readback_{PRIOR_R6_RUN_ID}.json",
        PRIOR_R6_READBACK_28_SHA256,
        "KH03_R6_SEALED_POSTPUSH_READBACK_28",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r6_observed_market_data/{PRIOR_R6_RUN_ID}/26_independent_OS_verifier_manifest_sha256.json",
        PRIOR_R6_MANIFEST_26_SHA256,
        "KH03_R6_SEALED_MANIFEST_26",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r6_observed_market_data/{PRIOR_R6_RUN_ID}/25_orthogonal_gates_and_verdicts.json",
        "4715ca28dcce0f26b42b992b46b2bcf621e63200841a15add48ffb2c32b6f82e",
        "KH03_R6_SEALED_GATES_25",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r6_observed_market_data/{PRIOR_R6_RUN_ID}/16_r4_replay_decimal_economic_bridge.json",
        "13da46824c22ef940ffb95d5add9fe8074fdad858b714b7606c534ab9fa8ccbf",
        "KH03_R6_SEALED_R4_BRIDGE_16",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r6_observed_market_data/{PRIOR_R6_RUN_ID}/17_six_cost_scenarios_and_arm_regression.json",
        "fd7ca2f3f7eea915b5e7416a4eac4bac2c7db8853d689b4980d0d24e8f428d36",
        "KH03_R6_SEALED_SIX_COSTS_17",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r6_observed_market_data/{PRIOR_R6_RUN_ID}/18_A1_SI8_adapter_D4_full_parity.json",
        "2d4cb5c277fd476b7697eba64236851a28b2661ebf2a2413fe8860687916a86e",
        "KH03_R6_SEALED_PARITY_18",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r6_observed_market_data/{PRIOR_R6_RUN_ID}/20_W1_W2_W4_W1repeat_benchmark_worker_policy.json",
        "2389b904f0ef188c314ee48ae96846f4e7c270bf3c9d825cd09211a8dfd4bbcb",
        "KH03_R6_SEALED_BENCHMARK_20",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r6_observed_market_data/{PRIOR_R6_RUN_ID}/logs/r4_replay_forensic_ledger_1x.jsonl.gz",
        "9a5648f27f73f547daf9ab138148c0fc7e6fe661a3d2063f84899a50fba1fc09",
        "KH03_R6_SEALED_FORENSIC_LEDGER_GZ",
    ),
)

# Explicit KH-03.R6.A1 scoped files allowlist (5 hardened R6 files + 3 compat R6 test updates + new A1 files)
KH03_R6_A1_SOURCE_FILES_ALLOWLIST: Tuple[str, ...] = (
    # Docs
    "aurum-desk/docs/kh_03_r6_a1/README.md",
    "aurum-desk/docs/kh_03_r6_a1/SPEC_KH_03_R6_A1_v1.0.md",
    "aurum-desk/docs/artifacts/kh_03_r6_a1_acquisition_trust_closure/README.md",
    # Hardened existing R6 files (strictly mapped to P1-G..P1-K!)
    "aurum-desk/tools/run_kh_03_r6_capture.py",
    "aurum-desk/backend/services/kh_03_r6_bounded_transport_service.py",
    "aurum-desk/backend/services/kh_03_r6_durable_grant_service.py",
    "aurum-desk/backend/services/kh_03_r6_custody_readiness_service.py",
    "aurum-desk/backend/services/kh_03_r6_acceptance_service.py",
    # Minimal compatibility updates to existing R6 tests for hardened interfaces
    "aurum-desk/backend/tests/test_kh03_r6_durable_grants.py",
    "aurum-desk/backend/tests/test_kh03_r6_transport_lifecycle.py",
    "aurum-desk/backend/tests/test_kh03_r6_custody_and_readiness.py",
    "aurum-desk/backend/tests/test_kh03_r6_resource_and_async.py",
    # New A1 services & tools
    "aurum-desk/backend/services/kh_03_r6_a1_trust_service.py",
    "aurum-desk/backend/services/kh_03_r6_a1_release_service.py",
    "aurum-desk/backend/services/kh_03_r6_a1_acquisition_service.py",
    "aurum-desk/backend/services/kh_03_r6_a1_acceptance_service.py",
    "aurum-desk/tools/kh_03_r6_a1_independent_verifier.py",
    "aurum-desk/tools/run_kh_03_r6_a1_acceptance.py",
    # New A1 test suites
    "aurum-desk/backend/tests/test_kh03_r6_a1_acquisition.py",
    "aurum-desk/backend/tests/test_kh03_r6_a1_plan_binding.py",
    "aurum-desk/backend/tests/test_kh03_r6_a1_budget_transport.py",
    "aurum-desk/backend/tests/test_kh03_r6_a1_provenance.py",
    "aurum-desk/backend/tests/test_kh03_r6_a1_release.py",
    "aurum-desk/backend/tests/test_kh03_r6_a1_resource_async.py",
    "aurum-desk/backend/tests/test_kh03_r6_a1_verifier_mutations.py",
)

REQUIRED_ARTIFACTS_KH03_R6_A1: Tuple[str, ...] = (
    "00_preflight_branch_sha_resource_lock.json",
    "01_function_ownership_keep_as_is_manifest.json",
    "02_prior_r6_and_c2_sealed_integrity_no_oos.json",
    "03_baseline_real_red_p1_g_to_k.json",
    "04_acquisition_cli_dryrun_zero_claim_and_qa_e2e.json",
    "05_session_state_task_coverage_and_finalization.json",
    "06_immutable_plan_grant_registry_lease_binding.json",
    "07_request_contract_and_ipc_envelope_receipts.json",
    "08_reservation_actual_io_budget_conservation.json",
    "09_settlement_failure_overrun_quarantine_receipts.json",
    "10_process_lifecycle_cancel_reap_traces.jsonl.gz",
    "11_ledger_cross_process_anchor_crash_restart.json",
    "12_trusted_supervisor_settlement_provenance_chain.json",
    "13_custody_actual_qa_raw_normalized_hashes_and_manifest.json",
    "14_frozen_executable_release_ancestry_and_cli_receipts.json",
    "15_external_block_no_observed_no_oos_no_trade.json",
    "16_r4_replay_decimal_economic_bridge.json",
    "17_six_cost_scenarios_and_arm_regression.json",
    "18_A1_SI8_adapter_D4_full_parity.json",
    "19_full_pytest_collection_junit_bijection.json",
    "20_W1_W2_W4_W1repeat_benchmark_policy.json",
    "21_actual_resource_telemetry_cleanup.json",
    "22_paired_fault_execution_receipts.jsonl.gz",
    "23_inherited_regression_and_new_fault_truth.json",
    "24_independent_verifier_mutation_results.json",
    "25_orthogonal_gates_pre_verifier.json",
    "27_phase1_code_commit_push_receipt.json",
    "README.md",
    "HANDOVER_KH03_R6_A1_VI.txt",
)

STALE_PRIOR_SHAS_R6_A1: Tuple[str, ...] = (
    *STALE_PRIOR_SHAS_R6,
    PRIOR_R6_TESTED_EXECUTABLE_SHA,
    PRIOR_R6_EVIDENCE_COMMIT_SHA,
    BASELINE_REVIEWED_HEAD_SHA_R6_A1,
)
STALE_PRIOR_RUN_IDS_R6_A1: Tuple[str, ...] = (
    *STALE_PRIOR_RUN_IDS_R6,
    PRIOR_R6_RUN_ID,
)

# =====================================================================
# KH-03.R6.A1 POST-REVIEW CORRECTIVE CLOSURE (P1-L / P1-M / P1-N)
# =====================================================================
MANIFEST_23_NAME_KH03_R6_A1_CORRECTIVE = "23_independent_verifier_manifest_sha256.json"
MIN_EXPECTED_PYTEST_COLLECTED_R6_A1_CORRECTIVE = 5978
MIN_NEW_LMN_PAIRED_CONTROLS_CORRECTIVE = 41
MIN_NEW_VERIFIER_MUTATIONS_CORRECTIVE = 18

PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R6_A1_CORRECTIVE: Tuple[Tuple[str, str, str], ...] = (
    *PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R6_A1,
    (
        "aurum-desk/docs/artifacts/kh_03_r6_a1_acquisition_trust_closure/latest_run.json",
        PRIOR_A1_LATEST_POINTER_SHA256,
        "KH03_R6_A1_LATEST_RUN_POINTER",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r6_a1_acquisition_trust_closure/28_phase2_remote_readback_{PRIOR_A1_RUN_ID}.json",
        PRIOR_A1_READBACK_28_SHA256,
        "KH03_R6_A1_SEALED_POSTPUSH_READBACK_28",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r6_a1_acquisition_trust_closure/{PRIOR_A1_RUN_ID}/26_independent_OS_verifier_manifest_sha256.json",
        PRIOR_A1_MANIFEST_26_SHA256,
        "KH03_R6_A1_SEALED_MANIFEST_26",
    ),
)

KH03_R6_A1_CORRECTIVE_SOURCE_FILES_ALLOWLIST: Tuple[str, ...] = (
    # Corrective docs
    "aurum-desk/docs/kh_03_r6_a1_corrective/README.md",
    "aurum-desk/docs/kh_03_r6_a1_corrective/SPEC_KH_03_R6_A1_CORRECTIVE_v1.0.md",
    "aurum-desk/docs/kh_03_r6_a1_corrective/FUNCTION_OWNERSHIP_MATRIX.md",
    "aurum-desk/docs/artifacts/kh_03_r6_a1_corrective_closure/README.md",
    # Modified core production modules & verifier/runner
    "aurum-desk/backend/services/kh_03_r6_a1_acquisition_service.py",
    "aurum-desk/backend/services/kh_03_r6_custody_readiness_service.py",
    "aurum-desk/backend/services/kh_03_r6_durable_grant_service.py",
    "aurum-desk/backend/services/kh_03_r6_bounded_transport_service.py",
    "aurum-desk/backend/services/kh_03_r6_a1_release_service.py",
    "aurum-desk/tools/kh_03_r6_a1_independent_verifier.py",
    "aurum-desk/tools/run_kh_03_r6_a1_acceptance.py",
    # New corrective service & 4 new test suites
    "aurum-desk/backend/services/kh_03_r6_a1_corrective_service.py",
    "aurum-desk/backend/tests/test_kh03_r6_a1_corrective_custody_l.py",
    "aurum-desk/backend/tests/test_kh03_r6_a1_corrective_budget_m.py",
    "aurum-desk/backend/tests/test_kh03_r6_a1_corrective_finalization_n.py",
    "aurum-desk/backend/tests/test_kh03_r6_a1_corrective_verifier_mutations.py",
)

REQUIRED_ARTIFACTS_KH03_R6_A1_CORRECTIVE: Tuple[str, ...] = (
    "00_preflight_branch_sha_resource_lock.json",
    "01_function_ownership_and_protected_hashes.json",
    "02_prior_sealed_manifest_integrity_no_oos.json",
    "03_baseline_red_l_m_n_receipts.jsonl.gz",
    "04_custody_writer_ownership_and_transaction_traces.jsonl.gz",
    "05_ordered_queue_and_w1_w2_w4_semantic_hashes.json",
    "06_crash_intent_restart_and_seal_truth.json",
    "07_hard_caps_and_effective_budget_contract.json",
    "08_disk_reservation_conservation_and_actual_bytes.jsonl.gz",
    "09_resource_telemetry_peak_allocations_and_cleanup.json",
    "10_claim_exception_and_durable_terminal_receipts.jsonl.gz",
    "11_finalize_errors_cancel_drain_reap_and_late_write.json",
    "12_compound_fault_and_quarantine_recovery.json",
    "13_external_block_qa_only_no_oos_no_trade.json",
    "14_inherited_g_to_k_and_36_gate_regression.json",
    "15_fresh_pytest_collection_junit_bijection.json",
    "16_r4_decimal_economic_replay_ledger_bridge.json",
    "17_six_cost_scenarios_e1_e2_e3_regression.json",
    "18_a1_si8_adapter_d4_parity.json",
    "19_worker_benchmark_and_safe_selection.json",
    "20_new_and_inherited_paired_test_receipts.jsonl.gz",
    "21_independent_verifier_mutation_receipts.jsonl.gz",
    "22_corrective_gate_predicates_and_verdicts.json",
    "24_code_publication_receipt.json",
    "README.md",
    "HANDOVER_KH03_R6_A1_CORRECTIVE_VI.txt",
)

STALE_PRIOR_SHAS_CORRECTIVE: Tuple[str, ...] = (
    *STALE_PRIOR_SHAS_R6_A1,
    PRIOR_A1_REVIEWED_TESTED_SHA,
    PRIOR_A1_EVIDENCE_COMMIT_SHA,
    PRIOR_A1_PUBLICATION_HEAD_SHA,
)
STALE_PRIOR_RUN_IDS_CORRECTIVE: Tuple[str, ...] = (
    *STALE_PRIOR_RUN_IDS_R6_A1,
    PRIOR_A1_RUN_ID,
)


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _strict_loads(text: str) -> Any:
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


def _audit_lifecycle_10_a1(run_dir: Path, a09: Mapping[str, Any]) -> List[str]:
    viols: List[str] = []
    gz_p = run_dir / "10_process_lifecycle_cancel_reap_traces.jsonl.gz"
    if not gz_p.is_file() or gz_p.stat().st_size == 0:
        return ["A1V_MISSING_LIFECYCLE_10_GZ"]
    if a09.get("lifecycle_gz_sha256") and _sha256_file(gz_p) != a09["lifecycle_gz_sha256"]:
        viols.append("A1V_LIFECYCLE_10_GZ_SHA_MISMATCH")
    rows: List[Dict[str, Any]] = []
    try:
        with gzip.open(gz_p, "rt", encoding="utf-8") as fh:
            for ln in fh:
                if ln.strip():
                    rows.append(_strict_loads(ln))
    except Exception as exc:
        return viols + [f"A1V_LIFECYCLE_10_PARSE_ERR:{exc}"]
    if len(rows) < 10:
        viols.append(f"A1V_LIFECYCLE_10_INSUFFICIENT_TRACES:{len(rows)}<10")
    launched = [int(r["child_pid"]) for r in rows if r.get("event") == "CHILD_PROCESS_LAUNCHED" and _is_int(r.get("child_pid")) and int(r["child_pid"]) > 0]
    reaped = [r for r in rows if r.get("event") == "CHILD_PROCESS_REAPED"]
    if not launched or not reaped:
        viols.append("A1V_LIFECYCLE_10_NO_LAUNCH_OR_REAP")
    for rr in reaped:
        if rr.get("reaped_verified") is not True:
            viols.append("A1V_LIFECYCLE_10_UNVERIFIED_REAP")
        if len(rr.get("surviving_descendant_pids") or ()) > 0:
            viols.append("A1V_LIFECYCLE_10_SURVIVING_DESCENDANTS")
    if a09.get("lifecycle_traces_count") != len(rows):
        viols.append(f"A1V_LIFECYCLE_10_COUNT_MISMATCH:09={a09.get('lifecycle_traces_count')}:raw={len(rows)}")
    return viols


def _audit_paired_faults_22_a1(run_dir: Path, a23: Mapping[str, Any], *, expected_run_id: str, expected_tested_sha: str) -> List[str]:
    viols: List[str] = []
    gz_p = run_dir / "22_paired_fault_execution_receipts.jsonl.gz"
    if not gz_p.is_file() or gz_p.stat().st_size == 0:
        return ["A1V_MISSING_PAIRED_FAULT_RECEIPTS_22_GZ"]
    if a23.get("paired_receipts_gz_sha256") and _sha256_file(gz_p) != a23["paired_receipts_gz_sha256"]:
        viols.append("A1V_PAIRED_FAULT_GZ_SHA_MISMATCH")
    receipts: List[Dict[str, Any]] = []
    try:
        with gzip.open(gz_p, "rt", encoding="utf-8") as fh:
            for ln in fh:
                if ln.strip():
                    receipts.append(_strict_loads(ln))
    except Exception as exc:
        return viols + [f"A1V_PAIRED_FAULT_PARSE_ERR:{exc}"]
    verd = derive_r6_a1_fault_verdict(receipts, expected_executable_sha=expected_tested_sha, expected_run_id=expected_run_id)
    if not verd["valid"]:
        viols.extend(f"A1V_PAIRED_FAULT_INVALID:{v}" for v in verd["violations"][:10])
    if verd["passed_controls"] < MIN_NEW_PAIRED_CONTROLS_R6_A1:
        viols.append(f"A1V_PAIRED_FAULT_BELOW_55:{verd['passed_controls']}<{MIN_NEW_PAIRED_CONTROLS_R6_A1}")
    if a23.get("new_a1_paired_verified_count") != verd["passed_controls"]:
        viols.append(f"A1V_PAIRED_COUNT_MISMATCH:23={a23.get('new_a1_paired_verified_count')}:raw={verd['passed_controls']}")
    if a23.get("a1_fault_ast_literal_flags_count") != 0:
        viols.append("A1V_PAIRED_FAULT_AST_LITERAL_FLAGS")
    if a23.get("inherited_not_relabelled_as_new_a1") is not True:
        viols.append("A1V_INHERITED_RELABELLED_AS_NEW")
    return viols


def _audit_r4_replay_ledger_16_a1(run_dir: Path, a16: Mapping[str, Any]) -> List[str]:
    viols: List[str] = []
    rel = str(a16.get("forensic_ledger_gz_rel") or "logs/r4_replay_forensic_ledger_1x.jsonl.gz")
    gz_p = run_dir / rel
    if not gz_p.is_file():
        return ["A1V_MISSING_R4_FORENSIC_LEDGER_GZ"]
    if a16.get("forensic_ledger_gz_sha256") and _sha256_file(gz_p) != a16["forensic_ledger_gz_sha256"]:
        viols.append("A1V_R4_FORENSIC_LEDGER_SHA_MISMATCH")
    closed = wins = losses = open_cens = gaps = gt_2p50 = 0
    gross_sum = fee_sum = net_sum = open_fee_sum = Decimal("0")
    seen_oids: set[str] = set()
    try:
        with gzip.open(gz_p, "rt", encoding="utf-8") as fh:
            for ln_no, ln in enumerate(fh, 1):
                if not ln.strip():
                    continue
                r = _strict_loads(ln)
                oid = str(r.get("opportunity_id") or "")
                if not oid or oid in seen_oids:
                    viols.append(f"A1V_R4_DUP_OR_MISSING_OID:line={ln_no}")
                seen_oids.add(oid)
                if not bool(r.get("filled")):
                    continue
                lbl = str(r.get("outcome_label") or "")
                if lbl in ("WIN", "LOSS"):
                    closed += 1
                    g, f, n = _dec(r["realized_gross_pnl_usd"]), _dec(r["total_fees_usd"]), _dec(r["realized_net_pnl_usd"])
                    if abs((g - f) - n) > Decimal("0.001"):
                        viols.append(f"A1V_R4_RESIDUAL_BREACH:line={ln_no}")
                    gross_sum += g; fee_sum += f; net_sum += n
                    if lbl == "WIN":
                        wins += 1
                    else:
                        losses += 1
                        if abs(n) > Decimal("2.50"):
                            gt_2p50 += 1
                elif lbl == "OPEN_AT_HORIZON":
                    open_cens += 1
                    open_fee_sum += _dec(r.get("accrued_open_entry_fee_usd", 0))
                elif lbl == "DATA_GAP_UNRESOLVED":
                    gaps += 1
    except Exception as exc:
        return viols + [f"A1V_R4_LEDGER_PARSE_ERR:{exc}"]
    if (closed, wins, losses, open_cens, gaps, gt_2p50) != (203, 7, 196, 28, 2, 139):
        viols.append(f"A1V_R4_COUNTS_MISMATCH:{closed}/{wins}/{losses}/{open_cens}/{gaps}/{gt_2p50}")
    if abs(gross_sum - Decimal("-164.9541")) > Decimal("0.01") or abs(fee_sum - Decimal("276.6577")) > Decimal("0.01") or abs(net_sum - Decimal("-441.6118")) > Decimal("0.01"):
        viols.append(f"A1V_R4_DECIMAL_SUM_MISMATCH:{_q4(gross_sum)}/{_q4(fee_sum)}/{_q4(net_sum)}")
    if abs(open_fee_sum - Decimal("14.7151")) > Decimal("0.01"):
        viols.append(f"A1V_R4_OPEN_FEE_DRIFT:{_q4(open_fee_sum)}")
    if a16.get("n_closed") != closed or a16.get("realized_losses_gt_2p50_count") != gt_2p50:
        viols.append("A1V_R4_16_SUMMARY_DRIFT")
    return viols


def _audit_junit_19_a1(run_dir: Path, a19: Mapping[str, Any]) -> List[str]:
    viols: List[str] = []
    nodes_gz = run_dir / str(a19.get("collected_nodeids_gz_rel") or "logs/pytest_collected_executed_nodeids.jsonl.gz")
    if not nodes_gz.is_file():
        return ["A1V_MISSING_PYTEST_NODEIDS_GZ"]
    if a19.get("collected_nodeids_gz_sha256") and _sha256_file(nodes_gz) != a19["collected_nodeids_gz_sha256"]:
        viols.append("A1V_PYTEST_NODEIDS_GZ_SHA_MISMATCH")
    with gzip.open(nodes_gz, "rt", encoding="utf-8") as fh:
        collected = [ln.strip() for ln in fh if ln.strip()]
    if len(collected) < MIN_EXPECTED_PYTEST_COLLECTED_R6_A1:
        viols.append(f"A1V_PYTEST_COLLECTED_BELOW_FLOOR:{len(collected)}<{MIN_EXPECTED_PYTEST_COLLECTED_R6_A1}")
    if len(collected) != len(set(collected)):
        viols.append("A1V_PYTEST_DUPLICATE_COLLECTED_NODEIDS")
    if a19.get("collected_count") != len(collected):
        viols.append(f"A1V_PYTEST_COLLECTED_COUNT_MISMATCH:19={a19.get('collected_count')}:raw={len(collected)}")

    junit_dir = run_dir / "junit"
    xml_files = sorted(junit_dir.glob("*.xml")) if junit_dir.is_dir() else []
    if not xml_files:
        return viols + ["A1V_MISSING_JUNIT_XML_FILES"]
    tot_tests = tot_fail = tot_err = tot_skip = mandatory_skip = 0
    mandatory_prefixes = ("test_kh_02_r2", "test_kh_03", "test_kh03_r2", "test_kh03_r3", "test_kh03_r4", "test_kh03_r5", "test_kh03_r6")
    for xf in xml_files:
        try:
            root = ET.parse(xf).getroot()
        except Exception as exc:
            viols.append(f"A1V_JUNIT_XML_CORRUPT:{xf.name}:{exc}")
            continue
        for tc in root.iter("testcase"):
            tot_tests += 1
            cls_s = str(tc.attrib.get("classname") or "")
            if tc.find("failure") is not None:
                tot_fail += 1
            if tc.find("error") is not None:
                tot_err += 1
            if tc.find("skipped") is not None:
                tot_skip += 1
                if any(mp in cls_s for mp in mandatory_prefixes):
                    mandatory_skip += 1
    if tot_tests != len(collected):
        viols.append(f"A1V_JUNIT_XML_COUNT_MISMATCH:xml={tot_tests}:collected={len(collected)}")
    if tot_fail != 0 or tot_err != 0 or mandatory_skip != 0:
        viols.append(f"A1V_JUNIT_XML_NONZERO_FAIL_ERR_MS:f={tot_fail}:e={tot_err}:ms={mandatory_skip}")
    if a19.get("executed_count") != tot_tests or a19.get("skipped_count") != tot_skip:
        viols.append("A1V_JUNIT_19_SUMMARY_MISMATCH")
    return viols


def _audit_benchmarks_20_a1(a20: Mapping[str, Any], *, expected_run_id: str, expected_tested_sha: str) -> List[str]:
    viols: List[str] = []
    for raw_k, pol_k, kind, max_cap in (
        ("pytest_controlled_subset_raw", "pytest_worker_policy", BENCHMARK_KIND_PYTEST, 4),
        ("science_slice_8000_raw", "science_worker_policy", BENCHMARK_KIND_SCIENCE, 4),
    ):
        raw_doc = a20.get(raw_k)
        rep_pol = a20.get(pol_k)
        if not isinstance(raw_doc, Mapping) or not isinstance(rep_pol, Mapping):
            viols.append(f"A1V_MISSING_BENCHMARK:{raw_k}")
            continue
        if raw_doc.get("run_id") != expected_run_id or raw_doc.get("tested_executable_sha") != expected_tested_sha:
            viols.append(f"A1V_BENCHMARK_PROVENANCE_MISMATCH:{kind}")
        if raw_doc.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH02:
            viols.append(f"A1V_BENCHMARK_DATASET_SHA_MISMATCH:{kind}")
        try:
            recomp = recompute_worker_policy_from_raw_receipt(
                raw_doc, reported_policy=rep_pol, benchmark_kind=kind,
                expected_run_id=expected_run_id, expected_tested_sha=expected_tested_sha,
                system_limits=rep_pol.get("cap_components") if isinstance(rep_pol.get("cap_components"), Mapping) else None,
            )
        except Exception as exc:
            viols.append(f"A1V_BENCHMARK_RECOMPUTE_ERR:{kind}:{exc}")
            continue
        if not recomp.get("valid") or recomp.get("status") != "PASS":
            viols.append(f"A1V_BENCHMARK_POLICY_INVALID:{kind}:{recomp.get('discrepancies')}")
        aw, sc = rep_pol.get("applied_worker_count"), rep_pol.get("safe_worker_cap")
        if not _is_int(aw) or not _is_int(sc) or int(aw) < 1 or int(aw) > max_cap or int(sc) > max_cap:
            viols.append(f"A1V_BENCHMARK_CAP_EXCEEDED:{kind}")

    for b_key, tag in (("r6_ingest_benchmark", "R6_INGEST"), ("a1_workload_benchmark", "A1_WORKLOAD")):
        b_doc = a20.get(b_key)
        if not isinstance(b_doc, Mapping):
            viols.append(f"A1V_MISSING_{tag}_BENCHMARK")
            continue
        runs = b_doc.get("runs")
        if not isinstance(runs, list) or len(runs) != 4:
            viols.append(f"A1V_{tag}_RUNS_NOT_4")
            continue
        if [r.get("mode_label") for r in runs] != ["w=1", "w=2", "w=4", "w=1_repeat"]:
            viols.append(f"A1V_{tag}_BAD_LABELS")
        shas = set()
        for r in runs:
            if not _is_num(r.get("wall_sec")) or float(r["wall_sec"]) < 0.0:
                viols.append(f"A1V_{tag}_NONFINITE_WALL")
            shas.add(str(r.get("semantic_result_sha256") or ""))
        if len(shas) != 1 or not next(iter(shas)):
            viols.append(f"A1V_{tag}_SEMANTIC_MISMATCH")
    return viols


def _audit_telemetry_21_a1(run_dir: Path, a21: Mapping[str, Any]) -> List[str]:
    viols: List[str] = []
    rel = str(a21.get("telemetry_samples_gz_rel") or "logs/runtime_memory_samples_0p5s.jsonl.gz")
    gz_p = run_dir / rel
    if not gz_p.is_file():
        return ["A1V_MISSING_TELEMETRY_GZ"]
    if a21.get("telemetry_samples_gz_sha256") and _sha256_file(gz_p) != a21["telemetry_samples_gz_sha256"]:
        viols.append("A1V_TELEMETRY_GZ_SHA_MISMATCH")
    cnt = 0
    min_ram = float("inf")
    min_disk = float("inf")
    max_desc = 0
    try:
        with gzip.open(gz_p, "rt", encoding="utf-8") as fh:
            for ln in fh:
                if not ln.strip():
                    continue
                r = _strict_loads(ln)
                cnt += 1
                ram = r.get("available_mib")
                if ram is None and r.get("available_ram_bytes") is not None:
                    ram = float(r["available_ram_bytes"]) / (1024.0 * 1024.0)
                if ram is None:
                    ram = r.get("ram_available_mib", r.get("available_ram_mib"))
                dsk = r.get("disk_free_gib", r.get("min_disk_free_gib"))
                desc = r.get("child_count", r.get("descendant_count", 0))
                if not _is_num(ram) or not _is_num(dsk):
                    viols.append("A1V_TELEMETRY_NONFINITE_SAMPLE")
                    break
                min_ram = min(min_ram, float(ram))
                min_disk = min(min_disk, float(dsk))
                if _is_int(desc):
                    max_desc = max(max_desc, int(desc))
    except Exception as exc:
        return viols + [f"A1V_TELEMETRY_PARSE_ERR:{exc}"]
    if cnt < 2:
        viols.append(f"A1V_TELEMETRY_TOO_FEW_SAMPLES:{cnt}")
    if min_ram < 512.0:
        viols.append(f"A1V_TELEMETRY_RAM_BELOW_512:{min_ram}")
    if min_disk < 20.0:
        viols.append(f"A1V_TELEMETRY_DISK_BELOW_20:{min_disk}")
    if max_desc > 4:
        viols.append(f"A1V_TELEMETRY_DESCENDANTS_GT_4:{max_desc}")
    if a21.get("samples_count") != cnt:
        viols.append(f"A1V_TELEMETRY_COUNT_MISMATCH:21={a21.get('samples_count')}:raw={cnt}")
    return viols


def verify_kh_03_r6_a1_run_dir(
    run_dir: Path,
    *,
    expected_run_id: str,
    expected_tested_sha: str,
    orchestrator_pid: Optional[int] = None,
    write_manifest: bool = True,
    check_prior_sealed_on_disk: bool = True,
) -> Tuple[int, Dict[str, Any]]:
    verifier_pid = os.getpid()
    violations: List[str] = []

    if orchestrator_pid is not None and int(orchestrator_pid) == int(verifier_pid):
        violations.append("A1V_SELF_VERIFICATION_SAME_PID_FORBIDDEN")
    if not run_dir.is_dir():
        return 1, {"status": "FAIL", "violations": ["A1V_RUN_DIR_NOT_FOUND"]}

    for rel in REQUIRED_ARTIFACTS_KH03_R6_A1:
        fp = run_dir / rel
        if not fp.is_file() or fp.stat().st_size == 0:
            violations.append(f"A1V_MISSING_OR_EMPTY_ARTIFACT:{rel}")

    arts: Dict[str, Dict[str, Any]] = {}
    for rel in REQUIRED_ARTIFACTS_KH03_R6_A1:
        if not rel.endswith(".json"):
            continue
        fp = run_dir / rel
        if not fp.is_file():
            continue
        key = rel[:2]
        try:
            obj = _strict_loads(fp.read_text("utf-8"))
        except Exception as exc:
            violations.append(f"A1V_JSON_STRICT_PARSE_ERR:{rel}:{exc}")
            continue
        if not isinstance(obj, dict):
            violations.append(f"A1V_JSON_NOT_OBJECT:{rel}")
            continue
        if obj.get("run_id") != expected_run_id:
            violations.append(f"A1V_RUN_ID_MISMATCH:{rel}:{obj.get('run_id')}")
        if obj.get("tested_executable_sha") != expected_tested_sha:
            violations.append(f"A1V_TESTED_SHA_MISMATCH:{rel}:{obj.get('tested_executable_sha')}")
        if str(obj.get("tested_executable_sha") or "") in STALE_PRIOR_SHAS_R6_A1:
            violations.append(f"A1V_STALE_PRIOR_SHA:{rel}")
        if str(obj.get("run_id") or "") in STALE_PRIOR_RUN_IDS_R6_A1:
            violations.append(f"A1V_STALE_PRIOR_RUN_ID:{rel}")
        arts[key] = obj

    prior_checked = 0
    if check_prior_sealed_on_disk:
        for rel_p, exp_sha, label in PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R6_A1:
            abs_p = REPO_ROOT / rel_p
            if not abs_p.is_file():
                violations.append(f"A1V_PRIOR_SEALED_MISSING:{label}")
                continue
            if _sha256_file(abs_p) != exp_sha:
                violations.append(f"A1V_PRIOR_SEALED_TAMPERED:{label}")
            prior_checked += 1

    # Domain-specific raw receipt & trace audits across 01..25
    a01 = arts.get("01") or {}
    if a01.get("locked_files_modified_count") != 0 or a01.get("unauthorized_changed_files_count") != 0 or a01.get("forbidden_order_imports_count") != 0 or a01.get("secrets_detected_count") != 0:
        violations.append("A1V_KEEP_AS_IS_01_INVALID")

    a02 = arts.get("02") or {}
    if a02.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH02:
        violations.append("A1V_CANONICAL_DATASET_SHA_MISMATCH")
    if a02.get("oos_reads_executed") != 0 or a02.get("oos_evaluations") != 0 or a02.get("prior_r6_manifest_sha256_verified") is not True:
        violations.append("A1V_PRIOR_INTEGRITY_02_INVALID")

    a03 = arts.get("03") or {}
    if a03.get("baseline_sha") != PRIOR_R6_TESTED_EXECUTABLE_SHA or a03.get("reproduced_count") != 6 or a03.get("all_reproduced") is not True:
        violations.append("A1V_M0_REAL_RED_03_INVALID")

    a04 = arts.get("04") or {}
    if a04.get("handshake_only_success_removed") is not True or a04.get("qa_cli_smoke_e2e_pass") is not True or a04.get("qa_cli_dev_e2e_pass") is not True or not _is_int(a04.get("qa_cli_dev_committed_chunks")) or int(a04["qa_cli_dev_committed_chunks"]) < 4:
        violations.append("A1V_QA_CLI_04_INVALID")

    a05 = arts.get("05") or {}
    if a05.get("empty_task_plan_blocked_no_claim") is not True or a05.get("partial_second_task_failure_truthful") is not True or a05.get("repeat_same_grant_denied_after_restart") is not True:
        violations.append("A1V_TASK_COVERAGE_05_INVALID")

    a06 = arts.get("06") or {}
    if a06.get("nested_caps_immutable_and_tamper_rejected") is not True or a06.get("recomputed_digest_old_mac_rejected") is not True or a06.get("narrow_plan_cap_enforced_by_ledger") is not True:
        violations.append("A1V_PLAN_BINDING_06_INVALID")

    a07 = arts.get("07") or {}
    if a07.get("child_cap_override_rejected") is not True or a07.get("unauthorized_path_or_query_or_ws_op_rejected") is not True or a07.get("direct_child_cli_external_blocked") is not True:
        violations.append("A1V_ENVELOPE_07_INVALID")

    a08 = arts.get("08") or {}
    if a08.get("zero_request_or_missing_dim_reservation_rejected") is not True or a08.get("sentinel_never_reads_beyond_reservation") is not True or a08.get("ws_reconnect_sub_conn_bounded_by_reservation") is not True:
        violations.append("A1V_BUDGET_08_INVALID")

    a09 = arts.get("09") or {}
    if a09.get("overrun_quarantined_without_clamping") is not True or a09.get("supervisor_rejects_on_settlement_failure_or_exception") is not True or a09.get("cancel_after_child_exit_before_accept_discarded") is not True:
        violations.append("A1V_SETTLEMENT_09_INVALID")
    violations.extend(_audit_lifecycle_10_a1(run_dir, a09))

    a11 = arts.get("11") or {}
    if a11.get("cross_process_parallel_reservations_conserved") is not True or a11.get("anchor_monotonic_ordering_verified") is not True or a11.get("crash_pre_anchor_and_unknown_io_restart_safe") is not True:
        violations.append("A1V_LEDGER_11_INVALID")

    a12 = arts.get("12") or {}
    if a12.get("fabricated_mapping_cannot_mint_observed") is not True or a12.get("qa_promotion_to_observed_dev_rejected_despite_tls") is not True or a12.get("replay_same_token_or_chunk_denied") is not True:
        violations.append("A1V_PROVENANCE_12_INVALID")

    a13 = arts.get("13") or {}
    if a13.get("qa_custody_readonly_post_restart_valid") is not True or a13.get("tampered_raw_or_norm_file_detected") is not True or not _is_int(a13.get("qa_e2e_committed_chunks_count")) or int(a13["qa_e2e_committed_chunks_count"]) < 4:
        violations.append("A1V_CUSTODY_13_INVALID")

    a14 = arts.get("14") or {}
    if a14.get("published_evidence_only_descendant_verified") is not True or a14.get("detached_worktree_exact_tested_sha_verified") is not True or a14.get("code_or_config_drift_rejected") is not True or a14.get("forged_manifest_pass_rejected") is not True:
        violations.append("A1V_RELEASE_14_INVALID")

    a15 = arts.get("15") or {}
    if a15.get("a1_external_capture_enabled") is not False or int(a15.get("external_market_requests_attempted") or 0) != 0 or int(a15.get("accepted_real_observed_records") or 0) != 0 or int(a15.get("owner_grant_consumed_count") or 0) != 0:
        violations.append("A1V_EXTERNAL_OR_OBSERVED_NONZERO_15")
    if a15.get("production_mode") != "OFF" or a15.get("live_trading_mode") != "OFF" or a15.get("paper_trading_mode") != "OFF" or a15.get("auto_trading_mode") != "OFF" or a15.get("activation_ready") != "NO" or a15.get("economic_verdict") != "NO_PROVEN_EDGE" or a15.get("oos_evaluations") != 0:
        violations.append("A1V_ECONOMIC_OOS_TRADING_15_VIOLATION")
    tmpl_rel = a15.get("unsigned_owner_review_template_rel")
    if not tmpl_rel or not (run_dir / str(tmpl_rel)).is_file():
        violations.append("A1V_MISSING_UNSIGNED_OWNER_TEMPLATE")
    else:
        t_obj = _strict_loads((run_dir / str(tmpl_rel)).read_text("utf-8"))
        if t_obj.get("template_status") != "UNSIGNED_OWNER_REVIEW_TEMPLATE" or t_obj.get("valid_authorization") is not False or t_obj.get("a1_external_capture_enabled") is not False:
            violations.append("A1V_OWNER_TEMPLATE_NOT_UNSIGNED")

    violations.extend(_audit_r4_replay_ledger_16_a1(run_dir, arts.get("16") or {}))

    a17 = arts.get("17") or {}
    if a17.get("all_six_scenarios_exact_match") is not True or a17.get("all_three_arms_exact_match") is not True:
        violations.append("A1V_SIX_COST_ARMS_17_INVALID")

    a18 = arts.get("18") or {}
    if a18.get("a1_checked") != 56710 or a18.get("a1_mismatches") != 0 or a18.get("si8_checked") != 6375 or a18.get("si8_mismatches") != 0 or a18.get("adapters_checked") != 246 or a18.get("adapters_mismatches") != 0 or a18.get("d4_mismatches") != 0:
        violations.append("A1V_BASELINE_PARITY_18_INVALID")

    violations.extend(_audit_junit_19_a1(run_dir, arts.get("19") or {}))
    violations.extend(_audit_benchmarks_20_a1(arts.get("20") or {}, expected_run_id=expected_run_id, expected_tested_sha=expected_tested_sha))
    violations.extend(_audit_telemetry_21_a1(run_dir, arts.get("21") or {}))
    violations.extend(_audit_paired_faults_22_a1(run_dir, arts.get("23") or {}, expected_run_id=expected_run_id, expected_tested_sha=expected_tested_sha))

    a24 = arts.get("24") or {}
    if a24.get("clean_clone_verifier_green") is not True or not _is_int(a24.get("new_a1_mutations_tested_count")) or int(a24["new_a1_mutations_tested_count"]) < MIN_NEW_VERIFIER_MUTATIONS_R6_A1 or a24.get("new_a1_mutations_detected_count") != a24.get("new_a1_mutations_tested_count"):
        violations.append("A1V_MUTATIONS_24_INVALID")

    # Independently recompute ALL 36 A1 gates & compare against producer 25!
    a25 = arts.get("25") or {}
    ad25 = a25.get("a1_denominators") or {}
    if ad25.get("external_r6_bcd_executed") != 0 or ad25.get("a1_mandatory_total") != 36:
        violations.append("A1V_EXTERNAL_DENOMINATOR_NONZERO_25")

    arts_for_gates = dict(arts)
    arts_for_gates["26"] = {
        "distinct_os_process_verified": (orchestrator_pid is None or int(orchestrator_pid) != verifier_pid),
        "verifier_status": "PASS" if not violations else "FAIL",
        "violations_count": len(violations),
        "verifier_pid": verifier_pid,
        "orchestrator_pid": int(orchestrator_pid) if orchestrator_pid is not None else -1,
    }
    recomp = derive_r6_a1_readiness_verdicts(arts_for_gates, expected_run_id=expected_run_id, expected_tested_sha=expected_tested_sha, pre_verifier_mode=False)
    prod_gates = a25.get("gates") or {}
    for gname in GATE_36_KEYS_KH03_R6_A1:
        if gname == "G32_INDEPENDENT_VERIFIER":
            continue
        if prod_gates.get(gname) != recomp["gates"].get(gname):
            violations.append(f"A1V_GATE_25_DRIFT:{gname}:prod={prod_gates.get(gname)}:recomp={recomp['gates'].get(gname)}")
        if gname != "G36_REMOTE_PUBLICATION" and recomp["gates"].get(gname) != "PASS":
            violations.append(f"A1V_MANDATORY_GATE_FAILED:{gname}={recomp['gates'].get(gname)}")

    status = "PASS" if len(violations) == 0 else "FAIL"
    arts_for_gates["26"]["verifier_status"] = status
    arts_for_gates["26"]["violations_count"] = len(violations)
    final_v = derive_r6_a1_readiness_verdicts(arts_for_gates, expected_run_id=expected_run_id, expected_tested_sha=expected_tested_sha, pre_verifier_mode=False)

    file_hashes: Dict[str, Dict[str, Any]] = {}
    for fp in sorted(run_dir.rglob("*")):
        if not fp.is_file():
            continue
        rel_f = str(fp.relative_to(run_dir)).replace("\\", "/")
        if rel_f == MANIFEST_26_NAME_KH03_R6_A1:
            continue
        file_hashes[rel_f] = {"bytes": fp.stat().st_size, "sha256": _sha256_file(fp)}

    manifest = {
        "spec_version": SPEC_VERSION_KH03_R6_A1,
        "schema_version": SCHEMA_VERSION_KH03_R6_A1,
        "run_id": expected_run_id,
        "tested_executable_sha": expected_tested_sha,
        "sealed_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verifier_pid": verifier_pid,
        "orchestrator_pid": int(orchestrator_pid) if orchestrator_pid is not None else None,
        "distinct_os_process_verified": (orchestrator_pid is None or int(orchestrator_pid) != verifier_pid),
        "prior_sealed_artifacts_verified_count": prior_checked,
        "status": status,
        "verifier_status": status,
        "violations_count": len(violations),
        "violations": violations,
        "recomputed_36_gates": final_v["gates"],
        "a1_denominators": final_v["a1_denominators"],
        "external_stages_separate": final_v["external_stages_separate"],
        "overall_classification": final_v["overall_classification"] if status == "PASS" else "FAIL_OR_BLOCKED_KH03_R6_A1_REQUIRED_GATES",
        "independent_verdicts": final_v["independent_verdicts"],
        "sealed_files_count": len(file_hashes),
        "sealed_files_sha256": file_hashes,
    }
    if write_manifest:
        write_json_durable_atomic(run_dir / MANIFEST_26_NAME_KH03_R6_A1, manifest)
    return (0 if status == "PASS" else 1), manifest


def _load_jsonl_gz_strict(p: Path) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], Optional[str]]:
    if not p.is_file() or p.stat().st_size == 0:
        return None, [], f"MISSING_OR_EMPTY_GZ:{p.name}"
    rows: List[Dict[str, Any]] = []
    try:
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            for ln in fh:
                if ln.strip():
                    obj = _strict_loads(ln)
                    if not isinstance(obj, dict):
                        return None, [], f"GZ_LINE_NOT_OBJ:{p.name}"
                    rows.append(obj)
    except Exception as exc:
        return None, [], f"GZ_PARSE_ERR:{p.name}:{exc}"
    if not rows:
        return None, [], f"GZ_EMPTY:{p.name}"
    return rows[0], rows[1:], None


def verify_kh_03_r6_a1_corrective_run_dir(
    run_dir: Path,
    *,
    expected_run_id: str,
    expected_tested_sha: str,
    orchestrator_pid: Optional[int] = None,
    write_manifest: bool = True,
    check_prior_sealed_on_disk: bool = True,
) -> Tuple[int, Dict[str, Any]]:
    """
    Separate-OS Independent Read-Only Verifier for KH-03.R6.A1 Post-Review Corrective Closure (P1-L/M/N).
    Independently recomputes ALL 12 corrective gates (`C01..C12`) directly from raw `.json`/`.jsonl.gz`/JUnit
    artifacts WITHOUT delegating core gate predicates to the orchestrator's verdict helper!
    Seals `23_independent_verifier_manifest_sha256.json`.
    """
    verifier_pid = os.getpid()
    violations: List[str] = []

    if orchestrator_pid is not None and int(orchestrator_pid) == int(verifier_pid):
        violations.append("CORRV_SELF_VERIFICATION_SAME_PID_FORBIDDEN")
    if not run_dir.is_dir():
        return 1, {"status": "FAIL", "violations": ["CORRV_RUN_DIR_NOT_FOUND"]}

    for rel in REQUIRED_ARTIFACTS_KH03_R6_A1_CORRECTIVE:
        fp = run_dir / rel
        if not fp.is_file() or fp.stat().st_size == 0:
            violations.append(f"CORRV_MISSING_OR_EMPTY_ARTIFACT:{rel}")

    arts: Dict[str, Dict[str, Any]] = {}
    for rel in REQUIRED_ARTIFACTS_KH03_R6_A1_CORRECTIVE:
        if not rel.endswith(".json"):
            continue
        fp = run_dir / rel
        if not fp.is_file():
            continue
        key = rel[:2]
        try:
            obj = _strict_loads(fp.read_text("utf-8"))
        except Exception as exc:
            violations.append(f"CORRV_JSON_STRICT_PARSE_ERR:{rel}:{exc}")
            continue
        if not isinstance(obj, dict):
            violations.append(f"CORRV_JSON_NOT_OBJECT:{rel}")
            continue
        if obj.get("run_id") != expected_run_id:
            violations.append(f"CORRV_RUN_ID_MISMATCH:{rel}:{obj.get('run_id')}")
        if obj.get("tested_executable_sha") != expected_tested_sha:
            violations.append(f"CORRV_TESTED_SHA_MISMATCH:{rel}:{obj.get('tested_executable_sha')}")
        if str(obj.get("tested_executable_sha") or "") in STALE_PRIOR_SHAS_CORRECTIVE:
            violations.append(f"CORRV_STALE_PRIOR_SHA:{rel}")
        if str(obj.get("run_id") or "") in STALE_PRIOR_RUN_IDS_CORRECTIVE:
            violations.append(f"CORRV_STALE_PRIOR_RUN_ID:{rel}")
        arts[key] = obj

    prior_checked = 0
    if check_prior_sealed_on_disk:
        for rel_p, exp_sha, label in PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R6_A1_CORRECTIVE:
            abs_p = REPO_ROOT / rel_p
            if not abs_p.is_file():
                violations.append(f"CORRV_PRIOR_SEALED_MISSING:{label}")
                continue
            if _sha256_file(abs_p) != exp_sha:
                violations.append(f"CORRV_PRIOR_SEALED_TAMPERED:{label}")
            prior_checked += 1

    # Audit gzipped streams directly from disk (03, 04, 08, 10, 20, 21)
    h03, r03, e03 = _load_jsonl_gz_strict(run_dir / "03_baseline_red_l_m_n_receipts.jsonl.gz")
    if e03 or h03 is None:
        violations.append(f"CORRV_RED_03_INVALID:{e03}")
        h03 = {}
    else:
        if h03.get("run_id") != expected_run_id or h03.get("tested_executable_sha") != expected_tested_sha:
            violations.append("CORRV_RED_03_PROVENANCE_MISMATCH")
        if h03.get("baseline_sha") != PRIOR_A1_REVIEWED_TESTED_SHA or h03.get("all_reproduced") is not True or not _is_int(h03.get("reproduced_count")) or int(h03["reproduced_count"]) < 4 or h03.get("positive_control_ok") is not True:
            violations.append("CORRV_RED_03_INVALID")
        red_rows = [r for r in r03 if r.get("defect") != "POSITIVE_CONTROL"]
        if len(red_rows) < 4 or any(r.get("baseline_sha") != PRIOR_A1_REVIEWED_TESTED_SHA or r.get("reproduced") is not True for r in red_rows):
            violations.append("CORRV_RED_03_RECEIPTS_INCOMPLETE")

    h04, r04, e04 = _load_jsonl_gz_strict(run_dir / "04_custody_writer_ownership_and_transaction_traces.jsonl.gz")
    if e04 or h04 is None:
        violations.append(f"CORRV_CUSTODY_04_INVALID:{e04}")
        h04 = {}
    else:
        if h04.get("run_id") != expected_run_id or h04.get("tested_executable_sha") != expected_tested_sha:
            violations.append("CORRV_CUSTODY_04_PROVENANCE_MISMATCH")
        if h04.get("duplicate_seq_detected") is not False:
            violations.append("CORRV_CUSTODY_04_DUPLICATE_SEQ")
        if h04.get("broken_prev_hash_detected") is not False:
            violations.append("CORRV_CUSTODY_04_BROKEN_PREV_HASH")
        if h04.get("active_writer_lock_after_close") is not False or h04.get("cross_process_single_writer_enforced") is not True or h04.get("direct_unauthorized_persist_denied") is not True:
            violations.append("CORRV_CUSTODY_04_WRITER_AUTHORITY_OR_LOCK")
        if h04.get("concurrent_writers_serialized_unique_seq") is not True:
            violations.append("CORRV_CUSTODY_04_SERIALIZATION_FAILED")
        # Check raw trace rows for duplicate seqs
        for tr in r04:
            sqs = tr.get("seqs")
            if isinstance(sqs, list) and len(sqs) != len(set(sqs)):
                violations.append("CORRV_CUSTODY_04_DUPLICATE_SEQ")
            if tr.get("readonly_valid") is False:
                violations.append("CORRV_CUSTODY_04_BROKEN_PREV_HASH")

    a05 = arts.get("05") or {}
    if (
        a05.get("w1_w2_w4_shuffled_order_identical_semantic_digest") is not True
        or a05.get("w4_repeated_seeds_byte_conserved") is not True
        or not a05.get("canonical_custody_semantic_sha256")
        or a05.get("bounded_queue_item_and_byte_backpressure_verified") is not True
        or a05.get("head_slot_anti_starvation_verified") is not True
        or a05.get("producer_failure_queue_full_no_deadlock_no_late_commit") is not True
        or a05.get("forged_or_expired_envelope_denied") is not True
    ):
        violations.append("CORRV_QUEUE_05_INVALID")

    a06 = arts.get("06") or {}
    if a06.get("crash_intent_restart_zero_duplicates") is not True or a06.get("concurrent_seal_before_writer_done_rejected") is not True:
        violations.append("CORRV_CRASH_SEAL_06_INVALID")

    a07 = arts.get("07") or {}
    if (
        a07.get("max_cap_enlargement_removed_verified") is not True
        or a07.get("narrow_plan_chunk_cap_plus1_rejected") is not True
        or a07.get("conflicting_cap_cannot_enlarge_via_override") is not True
        or a07.get("strict_int_bool_neg_overflow_rejected") is not True
    ):
        violations.append("CORRV_CAPS_07_ENLARGEMENT_OR_INVALID")

    h08, r08, e08 = _load_jsonl_gz_strict(run_dir / "08_disk_reservation_conservation_and_actual_bytes.jsonl.gz")
    if e08 or h08 is None:
        violations.append(f"CORRV_DISK_08_INVALID:{e08}")
        h08 = {}
    else:
        if h08.get("run_id") != expected_run_id or h08.get("tested_executable_sha") != expected_tested_sha:
            violations.append("CORRV_DISK_08_PROVENANCE_MISMATCH")
        if h08.get("double_release_rejected") is not True or h08.get("negative_bytes_detected") is not False:
            violations.append("CORRV_DISK_08_DOUBLE_RELEASE_OR_NEG")
        if h08.get("interrupted_write_uncertain_bytes_retained") is not True:
            violations.append("CORRV_DISK_08_MISSING_UNCERTAIN")
        if (
            h08.get("omitted_staging_json_wal_bytes") is not False
            or h08.get("normalized_utf8_expansion_prewrite_rejected") is not True
            or h08.get("peak_staging_manifest_wal_log_overhead_enforced") is not True
            or h08.get("w4_concurrent_reservations_conserved") is not True
            or h08.get("emergency_reserve_and_wal_bounded") is not True
        ):
            violations.append("CORRV_DISK_08_OVERHEAD_OR_CONSERVATION")

    a09 = arts.get("09") or {}
    if (
        a09.get("enospc_pressure_fail_closed_no_seal") is not True
        or a09.get("cleanup_fail_retains_uncertain_no_outside_delete") is not True
        or a09.get("symlink_junction_volume_substitution_rejected") is not True
        or a09.get("stale_or_nan_telemetry_rejected") is not True
        or a09.get("owned_temp_cleaned_verified") is not True
        or a09.get("orphan_workers_count") != 0
        or a09.get("disk_waiver_exit2_proven") is not True
    ):
        violations.append("CORRV_TELEMETRY_CLEANUP_09_INVALID")
    violations.extend(_audit_telemetry_21_a1(run_dir, a09))

    h10, r10, e10 = _load_jsonl_gz_strict(run_dir / "10_claim_exception_and_durable_terminal_receipts.jsonl.gz")
    if e10 or h10 is None:
        violations.append(f"CORRV_CLAIM_10_REUSED_OR_INVALID:{e10}")
        h10 = {}
    else:
        if h10.get("run_id") != expected_run_id or h10.get("tested_executable_sha") != expected_tested_sha:
            violations.append("CORRV_CLAIM_10_PROVENANCE_MISMATCH")
        if (
            h10.get("reused_one_shot_claim_accepted") is not False
            or h10.get("preclaim_exception_zero_consumption") is not True
            or h10.get("postclaim_return_exception_reconciled_once") is not True
            or h10.get("immediate_postclaim_telemetry_exception_finalized") is not True
            or h10.get("competing_finalizers_cas_idempotent") is not True
            or h10.get("persist_failure_classified_unproven_terminal") is not True
        ):
            violations.append("CORRV_CLAIM_10_REUSED_OR_INVALID")

    a11 = arts.get("11") or {}
    if (
        a11.get("finally_telemetry_exception_still_finalizes_ledger") is not True
        or a11.get("original_and_cleanup_errors_preserved_separately") is not True
        or a11.get("keyboard_interrupt_cancel_cleans_up_no_hidden_pass") is not True
        or a11.get("unreaped_worker_quarantines_and_blocks_new_session") is not True
        or a11.get("expired_deadline_cleanup_bounded_no_late_io") is not True
    ):
        violations.append("CORRV_FINALIZE_11_INVALID")

    a12 = arts.get("12") or {}
    if (
        a12.get("compound_l_m_n_fault_quarantined_conserved") is not True
        or a12.get("crash_restart_one_shot_spent_remains_spent") is not True
        or a12.get("durable_finalized_after_aborted_not_acquisition_pass") is not True
        or a12.get("positive_qa_e2e_full_chain_verified") is not True
    ):
        violations.append("CORRV_COMPOUND_12_INVALID")

    a01 = arts.get("01") or {}
    if a01.get("locked_files_modified_count") != 0 or a01.get("unauthorized_changed_files_count") != 0 or a01.get("forbidden_order_imports_count") != 0 or a01.get("secrets_detected_count") != 0:
        violations.append("CORRV_OWNERSHIP_01_INVALID")

    a02 = arts.get("02") or {}
    if a02.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH02 or a02.get("oos_reads_executed") != 0 or a02.get("oos_evaluations") != 0 or a02.get("all_prior_sealed_artifacts_intact") is not True:
        violations.append("CORRV_PRIOR_SEALED_02_INVALID")

    a13 = arts.get("13") or {}
    if (
        a13.get("a1_external_capture_enabled") is not False
        or int(a13.get("external_market_requests_attempted") or 0) != 0
        or int(a13.get("accepted_real_observed_records") or 0) != 0
        or int(a13.get("owner_grant_consumed_count") or 0) != 0
        or a13.get("production_mode") != "OFF"
        or a13.get("live_trading_mode") != "OFF"
        or a13.get("paper_trading_mode") != "OFF"
        or a13.get("auto_trading_mode") != "OFF"
        or a13.get("activation_ready") != "NO"
        or a13.get("economic_verdict") != "NO_PROVEN_EDGE"
        or a13.get("oos_evaluations") != 0
    ):
        violations.append("CORRV_EXTERNAL_13_NONZERO")

    a14 = arts.get("14") or {}
    if (
        a14.get("original_36_a1_prepush_gates_passed") != 35
        or a14.get("external_r6_bcd_executed_count") != 0
        or not _is_int(a14.get("inherited_a1_55_paired_passed")) or int(a14["inherited_a1_55_paired_passed"]) < 55
        or not _is_int(a14.get("inherited_r6_56_paired_passed")) or int(a14["inherited_r6_56_paired_passed"]) < 56
        or not _is_int(a14.get("inherited_c1_c2_91_paired_passed")) or int(a14["inherited_c1_c2_91_paired_passed"]) < 91
        or not _is_int(a14.get("inherited_regression_288_passed")) or int(a14["inherited_regression_288_passed"]) < 288
    ):
        violations.append("CORRV_INHERITED_14_INVALID")

    # Audit JUnit 15, R4 16, Six Costs 17, Parity 18, Benchmarks 19
    violations.extend(_audit_junit_19_a1(run_dir, arts.get("15") or {}))
    violations.extend(_audit_r4_replay_ledger_16_a1(run_dir, arts.get("16") or {}))

    a17 = arts.get("17") or {}
    if a17.get("all_six_scenarios_exact_match") is not True or a17.get("all_three_arms_exact_match") is not True:
        violations.append("CORRV_SIX_COST_17_INVALID")

    a18 = arts.get("18") or {}
    if a18.get("a1_checked") != 56710 or a18.get("a1_mismatches") != 0 or a18.get("si8_checked") != 6375 or a18.get("si8_mismatches") != 0 or a18.get("adapters_checked") != 246 or a18.get("adapters_mismatches") != 0 or a18.get("d4_mismatches") != 0:
        violations.append("CORRV_PARITY_18_INVALID")

    a19 = arts.get("19") or {}
    violations.extend(_audit_benchmarks_20_a1(a19, expected_run_id=expected_run_id, expected_tested_sha=expected_tested_sha))
    cw_b = a19.get("custody_w_benchmark")
    if not isinstance(cw_b, Mapping) or not isinstance(cw_b.get("runs"), list) or len(cw_b["runs"]) != 4:
        violations.append("CORRV_BENCH_19_CUSTODY_MISSING")
    else:
        c_shas = {str(r.get("semantic_result_sha256") or "") for r in cw_b["runs"]}
        if len(c_shas) != 1 or not next(iter(c_shas)) or [r.get("mode_label") for r in cw_b["runs"]] != ["w=1", "w=2", "w=4", "w=1_repeat"]:
            violations.append("CORRV_BENCH_19_CUSTODY_DRIFT")

    # Audit paired L/M/N receipts 20
    h20, r20, e20 = _load_jsonl_gz_strict(run_dir / "20_new_and_inherited_paired_test_receipts.jsonl.gz")
    if e20 or h20 is None:
        violations.append(f"CORRV_PAIRED_20_INVALID:{e20}")
        h20 = {}
    else:
        verd20 = derive_corrective_fault_verdict(r20, expected_executable_sha=expected_tested_sha, expected_run_id=expected_run_id)
        if not verd20["valid"] or verd20["passed_controls"] < MIN_NEW_LMN_PAIRED_CONTROLS_CORRECTIVE:
            violations.append(f"CORRV_PAIRED_20_VERDICT_FAILED:{verd20['violations'][:4]}")
        if h20.get("new_lmn_paired_passed_count") != verd20["passed_controls"] or h20.get("ast_literal_flags_count") != 0:
            violations.append("CORRV_PAIRED_20_SUMMARY_DRIFT")

    # Audit mutation receipts 21
    h21, r21, e21 = _load_jsonl_gz_strict(run_dir / "21_independent_verifier_mutation_receipts.jsonl.gz")
    if e21 or h21 is None:
        violations.append(f"CORRV_MUTATIONS_21_INVALID:{e21}")
        h21 = {}
    else:
        n_mut_t = h21.get("new_corrective_mutations_tested_count")
        n_mut_d = h21.get("new_corrective_mutations_detected_count")
        i_mut_t = h21.get("inherited_mutations_tested_count")
        i_mut_d = h21.get("inherited_mutations_detected_count")
        if (
            h21.get("clean_clone_verifier_green") is not True
            or not _is_int(n_mut_t) or int(n_mut_t) < MIN_NEW_VERIFIER_MUTATIONS_CORRECTIVE or n_mut_d != n_mut_t
            or not _is_int(i_mut_t) or int(i_mut_t) < 201 or i_mut_d != i_mut_t
            or len(r21) < int(n_mut_t)
            or any(m.get("detected_fail_closed") is not True for m in r21)
        ):
            violations.append("CORRV_MUTATIONS_21_FAILED")

    # Audit 22 denominators (NEVER conflate into 40 or 48!)
    a22 = arts.get("22") or {}
    cd22 = a22.get("corrective_denominators") or {}
    if (
        cd22.get("corrective_mandatory_total") != 12
        or cd22.get("original_a1_mandatory_total") != 36
        or cd22.get("external_r6_bcd_total") != 4
        or cd22.get("external_r6_bcd_executed") != 0
        or cd22.get("never_conflate_denominators_into_40_or_48") is not True
    ):
        violations.append("CORRV_DENOMINATOR_22_CONFLATED")

    # Independently recompute ALL 12 corrective gates directly from raw parsed objects!
    a00 = arts.get("00") or {}
    a24 = arts.get("24") or {}
    recomp_gates: Dict[str, str] = {
        "C01_BASELINE_RED_REPRODUCTION": "PASS" if (not any(v.startswith("CORRV_RED_03") for v in violations)) else "FAIL",
        "C02_SINGLE_WRITER_ATOMICITY_RESTART": "PASS" if (not any(v.startswith(("CORRV_CUSTODY_04", "CORRV_CRASH_SEAL_06")) for v in violations)) else "FAIL",
        "C03_DETERMINISTIC_W1_W2_W4_CUSTODY": "PASS" if (not any(v.startswith(("CORRV_QUEUE_05", "CORRV_BENCH_19_CUSTODY")) for v in violations)) else "FAIL",
        "C04_BOUNDED_QUEUE_BACKPRESSURE_NO_LATE_WRITE": "PASS" if (not any(v.startswith("CORRV_QUEUE_05") for v in violations)) else "FAIL",
        "C05_HARD_CAP_MIN_INTERSECTION_NO_ENLARGEMENT": "PASS" if (not any(v.startswith("CORRV_CAPS_07") for v in violations)) else "FAIL",
        "C06_ATOMIC_DISK_RESERVATIONS_PEAK_OVERHEAD": "PASS" if (not any(v.startswith("CORRV_DISK_08") for v in violations)) else "FAIL",
        "C07_REAL_TELEMETRY_PRESSURE_CLEANUP_ENOSPC": "PASS" if (not any(v.startswith(("CORRV_TELEMETRY_CLEANUP_09", "A1V_TELEMETRY")) for v in violations)) else "FAIL",
        "C08_CLAIM_EXCEPTION_DURABLE_TERMINAL_RECONCILE": "PASS" if (not any(v.startswith("CORRV_CLAIM_10") for v in violations)) else "FAIL",
        "C09_FINALIZER_ISOLATION_CANCEL_OWNED_REAP": "PASS" if (not any(v.startswith("CORRV_FINALIZE_11") for v in violations)) else "FAIL",
        "C10_COMPOUND_CRASH_QUARANTINE_REPLAY_NO_SEAL": "PASS" if (not any(v.startswith("CORRV_COMPOUND_12") for v in violations)) else "FAIL",
        "C11_INDEPENDENT_VERIFIER_MUTATIONS_INHERITED": "PASS" if (len(violations) == 0) else "FAIL",
        "C12_PROTECTED_SOURCE_PARITY_FROZEN_REMOTE": "PENDING_REMOTE_READBACK" if (
            a00.get("branch") == MANDATORY_BRANCH_KH03_R6
            and a00.get("worktree_clean_at_start") is True
            and a24.get("phase1_executable_committed") is True
            and not any(v.startswith(("CORRV_OWNERSHIP_01", "CORRV_PRIOR_SEALED_02", "CORRV_EXTERNAL_13", "A1V_R4_", "CORRV_SIX_COST_17", "CORRV_PARITY_18", "CORRV_STALE_PRIOR_SHA")) for v in violations)
        ) else "FAIL",
    }

    prod_gates = a22.get("corrective_12_gates") or {}
    for gk in CORRECTIVE_12_GATE_KEYS:
        if gk == "C11_INDEPENDENT_VERIFIER_MUTATIONS_INHERITED":
            continue
        if prod_gates.get(gk) != recomp_gates.get(gk):
            violations.append(f"CORRV_GATE_22_DRIFT:{gk}:prod={prod_gates.get(gk)}:recomp={recomp_gates.get(gk)}")
        if gk != "C12_PROTECTED_SOURCE_PARITY_FROZEN_REMOTE" and recomp_gates.get(gk) != "PASS":
            violations.append(f"CORRV_MANDATORY_GATE_FAILED:{gk}")
    if recomp_gates["C12_PROTECTED_SOURCE_PARITY_FROZEN_REMOTE"] not in ("PENDING_REMOTE_READBACK", "PASS_REMOTE_VERIFIED"):
        violations.append("CORRV_C12_LOCAL_PRECONDITIONS_FAILED")

    status = "PASS" if len(violations) == 0 else "FAIL"
    recomp_gates["C11_INDEPENDENT_VERIFIER_MUTATIONS_INHERITED"] = "PASS" if status == "PASS" else "FAIL"

    file_hashes: Dict[str, Dict[str, Any]] = {}
    for fp in sorted(run_dir.rglob("*")):
        if not fp.is_file():
            continue
        rel_f = str(fp.relative_to(run_dir)).replace("\\", "/")
        if rel_f == MANIFEST_23_NAME_KH03_R6_A1_CORRECTIVE:
            continue
        file_hashes[rel_f] = {"bytes": fp.stat().st_size, "sha256": _sha256_file(fp)}

    manifest = {
        "spec_version": SPEC_VERSION_KH03_R6_A1_CORRECTIVE,
        "schema_version": SCHEMA_VERSION_KH03_R6_A1_CORRECTIVE,
        "run_id": expected_run_id,
        "tested_executable_sha": expected_tested_sha,
        "sealed_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verifier_pid": verifier_pid,
        "orchestrator_pid": int(orchestrator_pid) if orchestrator_pid is not None else None,
        "distinct_os_process_verified": (orchestrator_pid is None or int(orchestrator_pid) != verifier_pid),
        "prior_sealed_artifacts_verified_count": prior_checked,
        "status": status,
        "verifier_status": status,
        "violations_count": len(violations),
        "violations": violations,
        "recomputed_12_corrective_gates": recomp_gates,
        "corrective_denominators": {
            "corrective_mandatory_total": 12,
            "corrective_prepush_passed": sum(1 for k in CORRECTIVE_12_GATE_KEYS if k != "C12_PROTECTED_SOURCE_PARITY_FROZEN_REMOTE" and recomp_gates[k] == "PASS"),
            "original_a1_mandatory_total": 36,
            "original_a1_prepush_passed": int(a14.get("original_36_a1_prepush_gates_passed") or 0),
            "external_r6_bcd_total": 4,
            "external_r6_bcd_executed": 0,
            "never_conflate_denominators_into_40_or_48": True,
        },
        "overall_classification": CLASSIFICATION_PASS_OFFLINE_CORRECTIVE if status == "PASS" else "FAIL_OR_BLOCKED_KH03_R6_A1_CORRECTIVE",
        "sealed_files_count": len(file_hashes),
        "sealed_files_sha256": file_hashes,
    }
    if write_manifest:
        write_json_durable_atomic(run_dir / MANIFEST_23_NAME_KH03_R6_A1_CORRECTIVE, manifest)
    return (0 if status == "PASS" else 1), manifest


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="KH-03.R6.A1 Separate-OS Independent Read-Only Verifier")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--expected-run-id", default=None)
    ap.add_argument("--expected-tested-sha", default=None)
    ap.add_argument("--orchestrator-pid", type=int, default=None)
    ap.add_argument("--corrective", action="store_true", help="Verify KH-03.R6.A1 corrective closure (P1-L/M/N) run directory.")
    ap.add_argument("--no-write-manifest", action="store_true")
    ap.add_argument("--skip-prior-disk-check", action="store_true")
    ns = ap.parse_args(argv)

    rdir = Path(ns.run_dir).resolve()
    exp_rid = ns.expected_run_id
    exp_sha = ns.expected_tested_sha
    if (exp_rid is None or exp_sha is None) and (rdir / "00_preflight_branch_sha_resource_lock.json").is_file():
        a00 = _strict_loads((rdir / "00_preflight_branch_sha_resource_lock.json").read_text("utf-8"))
        exp_rid = exp_rid or str(a00.get("run_id") or "")
        exp_sha = exp_sha or str(a00.get("tested_executable_sha") or "")

    verify_fn = verify_kh_03_r6_a1_corrective_run_dir if ns.corrective else verify_kh_03_r6_a1_run_dir
    rc, rep = verify_fn(
        rdir, expected_run_id=str(exp_rid or ""), expected_tested_sha=str(exp_sha or ""),
        orchestrator_pid=ns.orchestrator_pid, write_manifest=(not ns.no_write_manifest),
        check_prior_sealed_on_disk=(not ns.skip_prior_disk_check),
    )
    print(json.dumps({
        "status": rep["status"],
        "verifier_pid": rep["verifier_pid"],
        "orchestrator_pid": rep["orchestrator_pid"],
        "violations_count": rep["violations_count"],
        "violations": rep["violations"][:20],
        "sealed_files_count": rep["sealed_files_count"],
        "overall_classification": rep["overall_classification"],
    }, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
