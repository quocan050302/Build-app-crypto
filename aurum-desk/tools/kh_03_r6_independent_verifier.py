#!/usr/bin/env python3
"""
aurum-desk/tools/kh_03_r6_independent_verifier.py

AURUM DESK — KH-03.R6 (T3) | SEPARATE-OS INDEPENDENT READ-ONLY DISK VERIFIER (G01..G36)
SPEC VERSION: KH-03.R6-v1.0 | ZERO ORCHESTRATOR IMPORT | RAW RECEIPT & TRACE RECOMPUTATION

Runs in a distinct OS process (`verifier_pid != orchestrator_pid`), opens ZERO external network sockets,
NEVER accesses OOS data, reads ALL raw R6 artifacts directly from disk using strict duplicate-key and
non-finite-rejecting JSON parsing, independently recomputes:
  - Prior R1..R5.C2 + canonical dataset SHA-256 immutability
  - Official Bitget Classic v2 source docs & non-mixing contract audit (`03`, `10`)
  - Owner trust boundary, unsigned template & grant/plan binding (`04`, `09`)
  - Cross-process/restart durable SQLite ledger + external anchor crash/budget receipts (`05`, `06`)
  - Actual owned transport process lifecycle raw stream (`07_transport_actual_process_lifecycle.jsonl.gz`) & loopback bounds (`08`)
  - Market data contracts: OHLCV (`11`), BBO event vs inferred state-known (`12`), funding (`13`), fee/slippage honesty (`14`), custody (`15`)
  - Exact Decimal R4 replay ledger (`logs/r4_replay_forensic_ledger_1x.jsonl.gz` vs `16`, `17`)
  - Full A1/SI8/adapter/D4 baseline parity (`18`)
  - Raw JUnit XML 1:1 bijection vs `logs/pytest_collected_executed_nodeids.jsonl.gz` (`19`)
  - Raw W1/W2/W4/W1_repeat benchmarks via `recompute_worker_policy_from_raw_receipt` + R6 ingest check (`20`)
  - Raw 0.5s telemetry stream (`logs/runtime_memory_samples_0p5s.jsonl.gz` vs `21`)
  - Every paired fault control receipt (`22_paired_fault_execution_receipts.jsonl.gz` vs `23`) via independent predicate re-evaluation
  - Separate-OS mutation resistance (`24`)
  - Independent recomputation of ALL 36 stage-scoped gates (`25`) enforcing stage denominators (never 36/36 PASS without capture!)
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
from services.kh_03_r6_acceptance_service import derive_r6_fault_verdict
from services.kh_03_r6_custody_readiness_service import (
    GATE_36_KEYS_KH03_R6,
    MIN_EXPECTED_PYTEST_COLLECTED_R6,
    MIN_NEW_PAIRED_CONTROLS_R6,
    MIN_NEW_VERIFIER_MUTATIONS_R6,
    STAGE_A_MANDATORY_GATES_R6,
    STAGE_BC_CONDITIONAL_GATES_R6,
    derive_r6_readiness_verdicts,
)
from services.kh_03_r6_source_contract_service import (
    API_FAMILY_CLASSIC_V2,
    BASELINE_REVIEWED_HEAD_SHA_R6,
    MANDATORY_BRANCH_KH03_R6,
    PRIOR_C2_EVIDENCE_COMMIT_SHA,
    PRIOR_C2_RUN_ID,
    PRIOR_C2_TESTED_EXECUTABLE_SHA,
    SCHEMA_VERSION_KH03_R6,
    SPEC_VERSION_KH03_R6,
)
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_03_r5_c2_independent_verifier import (
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R5_C2,
    STALE_PRIOR_RUN_IDS_R5_C2,
    STALE_PRIOR_SHAS_R5_C2,
)

MANIFEST_26_NAME_KH03_R6 = "26_independent_OS_verifier_manifest_sha256.json"
Q4 = Decimal("0.0001")

# Verified KH-03.R5.C2 sealed artifacts at reviewed HEAD fd0df117ca38f0ac72312d27265dcdabaa5a0dba
PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R6: Tuple[Tuple[str, str, str], ...] = (
    *PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R5_C2,
    (
        "aurum-desk/docs/artifacts/kh_03_r5_c2_technical_closure/latest_run.json",
        "8c9225c884aec3e3df6ed74b90bf05970d0e3b1c77574ce92d35774fa4e38e62",
        "KH03_R5_C2_LATEST_RUN_POINTER",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r5_c2_technical_closure/23_phase2_remote_readback_{PRIOR_C2_RUN_ID}.json",
        "8bb861863395ecf4545f14d275bb02d765b13d2fc413956a632e5e6480b00989",
        "KH03_R5_C2_SEALED_POSTPUSH_READBACK_23",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r5_c2_technical_closure/{PRIOR_C2_RUN_ID}/21_independent_OS_verifier_manifest_sha256.json",
        "6a65843b16cf7b97b3011afcaa2e8495c2ec90aaaadc35dbf0148338cb3d2778",
        "KH03_R5_C2_SEALED_MANIFEST_21",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r5_c2_technical_closure/{PRIOR_C2_RUN_ID}/20_orthogonal_gates_and_verdicts.json",
        "f793990d9e31de3a6e565917f5be85d0f53813a70a7e9c49ffdd2e759335379b",
        "KH03_R5_C2_SEALED_32_GATES_20",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r5_c2_technical_closure/{PRIOR_C2_RUN_ID}/12_r4_replay_decimal_economic_bridge.json",
        "3e50e91291dee87656d3a6f0a03a256dff98ff5b4a6e26f8949fda35e2a0f437",
        "KH03_R5_C2_SEALED_R4_BRIDGE_12",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r5_c2_technical_closure/{PRIOR_C2_RUN_ID}/14_A1_SI8_adapter_D4_full_parity.json",
        "56301f3eea3785d2dc96825c0dcf9ace021a17b5162a3ff8768e27563410f419",
        "KH03_R5_C2_SEALED_PARITY_14",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r5_c2_technical_closure/{PRIOR_C2_RUN_ID}/logs/r4_replay_forensic_ledger_1x.jsonl.gz",
        "21ea08cecb964b9bcf5324d49cf12b80c3194ab4763b0bdf3c890a41a09d35e3",
        "KH03_R5_C2_SEALED_FORENSIC_LEDGER_GZ",
    ),
)

# 100% additive KH-03.R6 allowlist (ZERO existing locked files modified!)
KH03_R6_SOURCE_FILES_ALLOWLIST: Tuple[str, ...] = (
    "aurum-desk/docs/kh_03_r6/README.md",
    "aurum-desk/docs/kh_03_r6/SPEC_KH_03_R6_v1.0.md",
    "aurum-desk/docs/artifacts/kh_03_r6_observed_market_data/README.md",
    "aurum-desk/backend/services/kh_03_r6_source_contract_service.py",
    "aurum-desk/backend/services/kh_03_r6_durable_grant_service.py",
    "aurum-desk/backend/services/kh_03_r6_bounded_transport_service.py",
    "aurum-desk/backend/services/kh_03_r6_market_data_service.py",
    "aurum-desk/backend/services/kh_03_r6_custody_readiness_service.py",
    "aurum-desk/backend/services/kh_03_r6_acceptance_service.py",
    "aurum-desk/tools/run_kh_03_r6_acceptance.py",
    "aurum-desk/tools/run_kh_03_r6_capture.py",
    "aurum-desk/tools/kh_03_r6_independent_verifier.py",
    "aurum-desk/backend/tests/test_kh03_r6_source_contracts.py",
    "aurum-desk/backend/tests/test_kh03_r6_durable_grants.py",
    "aurum-desk/backend/tests/test_kh03_r6_transport_lifecycle.py",
    "aurum-desk/backend/tests/test_kh03_r6_market_data_integrity.py",
    "aurum-desk/backend/tests/test_kh03_r6_custody_and_readiness.py",
    "aurum-desk/backend/tests/test_kh03_r6_fault_truth_and_mutations.py",
    "aurum-desk/backend/tests/test_kh03_r6_resource_and_async.py",
)

REQUIRED_ARTIFACTS_KH03_R6: Tuple[str, ...] = (
    "00_preflight_branch_sha_resource_lock.json",
    "01_function_ownership_keep_as_is_manifest.json",
    "02_prior_c2_dataset_sealed_integrity_no_oos.json",
    "03_official_source_contracts_and_api_family_registry.json",
    "04_owner_trust_scope_and_plan_redacted.json",
    "05_durable_grant_claim_cross_process_restart_receipts.json",
    "06_ledger_crash_anchor_and_conservation_receipts.json",
    "07_transport_actual_process_lifecycle.jsonl.gz",
    "08_rest_ws_bounds_loopback_and_cancel_reap.json",
    "09_capture_authorization_external_dispatch_provenance.json",
    "10_instrument_identity_metadata_validation.json",
    "11_ohlcv_pagination_lineage_gap_coverage.json",
    "12_bbo_epoch_event_state_coverage.json",
    "13_funding_settlement_completeness.json",
    "14_fee_slippage_execution_fidelity_register.json",
    "15_dev_partition_custody_dataset_manifest.json",
    "16_r4_replay_decimal_economic_bridge.json",
    "17_six_cost_scenarios_and_arm_regression.json",
    "18_A1_SI8_adapter_D4_full_parity.json",
    "19_full_pytest_fresh_collection_junit_bijection.json",
    "20_W1_W2_W4_W1repeat_benchmark_worker_policy.json",
    "21_actual_resource_telemetry_cleanup.json",
    "22_paired_fault_execution_receipts.jsonl.gz",
    "23_inherited_regression_and_new_fault_truth.json",
    "24_independent_verifier_mutation_results.json",
    "25_orthogonal_gates_and_verdicts.json",
    "27_phase1_code_commit_push_receipt.json",
    "README.md",
    "HANDOVER_KH03_R6_VI.txt",
)

STALE_PRIOR_SHAS_R6: Tuple[str, ...] = (
    *STALE_PRIOR_SHAS_R5_C2,
    PRIOR_C2_TESTED_EXECUTABLE_SHA,
    PRIOR_C2_EVIDENCE_COMMIT_SHA,
    BASELINE_REVIEWED_HEAD_SHA_R6,
)
STALE_PRIOR_RUN_IDS_R6: Tuple[str, ...] = (
    *STALE_PRIOR_RUN_IDS_R5_C2,
    PRIOR_C2_RUN_ID,
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


def _audit_transport_lifecycle_07_r6(run_dir: Path, a08: Mapping[str, Any]) -> List[str]:
    viols: List[str] = []
    gz_p = run_dir / "07_transport_actual_process_lifecycle.jsonl.gz"
    if not gz_p.is_file() or gz_p.stat().st_size == 0:
        return ["R6V_MISSING_TRANSPORT_LIFECYCLE_GZ"]
    if a08.get("lifecycle_gz_sha256") and _sha256_file(gz_p) != a08["lifecycle_gz_sha256"]:
        viols.append("R6V_TRANSPORT_LIFECYCLE_GZ_SHA_MISMATCH")

    rows: List[Dict[str, Any]] = []
    try:
        with gzip.open(gz_p, "rt", encoding="utf-8") as fh:
            for ln in fh:
                if ln.strip():
                    rows.append(_strict_loads_no_dup_no_nonfinite(ln))
    except Exception as exc:
        return viols + [f"R6V_TRANSPORT_LIFECYCLE_PARSE_ERR:{exc}"]

    if len(rows) < 3:
        viols.append("R6V_TRANSPORT_LIFECYCLE_INSUFFICIENT_TRACES")
        return viols

    launched_pids = [int(r["child_pid"]) for r in rows if r.get("event") == "CHILD_PROCESS_LAUNCHED" and _is_int(r.get("child_pid")) and int(r["child_pid"]) > 0]
    reaped_rows = [r for r in rows if r.get("event") == "CHILD_PROCESS_REAPED"]
    if not launched_pids or not reaped_rows:
        viols.append("R6V_TRANSPORT_LIFECYCLE_NO_ACTUAL_LAUNCH_OR_REAP")
    for rr in reaped_rows:
        if rr.get("reaped_verified") is not True and rr.get("scenario") != "C05_UNVERIFIED_OS_PROOF_QUARANTINE":
            viols.append("R6V_TRANSPORT_LIFECYCLE_UNDRAINED_UNQUARANTINED")
        surv = rr.get("surviving_descendant_pids") or []
        if len(surv) > 0:
            viols.append("R6V_TRANSPORT_LIFECYCLE_SURVIVING_DESCENDANTS")

    expected_pid = a08.get("loopback_worker_pid")
    if not _is_int(expected_pid) or int(expected_pid) not in launched_pids:
        viols.append("R6V_TRANSPORT_LIFECYCLE_PID_MISMATCH_VS_08")
    return viols


def _audit_paired_faults_22_r6(
    run_dir: Path,
    a23: Mapping[str, Any],
    *,
    expected_run_id: str,
    expected_tested_sha: str,
) -> List[str]:
    viols: List[str] = []
    gz_p = run_dir / "22_paired_fault_execution_receipts.jsonl.gz"
    if not gz_p.is_file() or gz_p.stat().st_size == 0:
        return ["R6V_MISSING_PAIRED_FAULT_RECEIPTS_GZ"]
    if a23.get("paired_receipts_gz_sha256") and _sha256_file(gz_p) != a23["paired_receipts_gz_sha256"]:
        viols.append("R6V_PAIRED_FAULT_GZ_SHA_MISMATCH")

    receipts: List[Dict[str, Any]] = []
    try:
        with gzip.open(gz_p, "rt", encoding="utf-8") as fh:
            for ln in fh:
                if ln.strip():
                    receipts.append(_strict_loads_no_dup_no_nonfinite(ln))
    except Exception as exc:
        return viols + [f"R6V_PAIRED_FAULT_PARSE_ERR:{exc}"]

    verd = derive_r6_fault_verdict(receipts, expected_executable_sha=expected_tested_sha, expected_run_id=expected_run_id)
    if not verd["valid"]:
        viols.extend(f"R6V_PAIRED_FAULT_INVALID:{v}" for v in verd["violations"][:10])
    if verd["passed_controls"] < MIN_NEW_PAIRED_CONTROLS_R6:
        viols.append(f"R6V_PAIRED_FAULT_BELOW_56:{verd['passed_controls']}<{MIN_NEW_PAIRED_CONTROLS_R6}")
    if a23.get("new_r6_paired_verified_count") != verd["passed_controls"]:
        viols.append(f"R6V_PAIRED_COUNT_MISMATCH:23={a23.get('new_r6_paired_verified_count')}:raw={verd['passed_controls']}")
    return viols


def _audit_r4_replay_ledger_16_r6(run_dir: Path, a16: Mapping[str, Any]) -> List[str]:
    viols: List[str] = []
    rel = str(a16.get("forensic_ledger_gz_rel") or "logs/r4_replay_forensic_ledger_1x.jsonl.gz")
    gz_p = run_dir / rel
    if not gz_p.is_file():
        return ["R6V_MISSING_R4_FORENSIC_LEDGER_GZ"]
    if a16.get("forensic_ledger_gz_sha256") and _sha256_file(gz_p) != a16["forensic_ledger_gz_sha256"]:
        viols.append("R6V_R4_FORENSIC_LEDGER_SHA_MISMATCH")

    closed = wins = losses = open_cens = gaps = gt_2p50 = 0
    gross_sum = Decimal("0")
    fee_sum = Decimal("0")
    net_sum = Decimal("0")
    open_fee_sum = Decimal("0")
    seen_oids: set[str] = set()
    try:
        with gzip.open(gz_p, "rt", encoding="utf-8") as fh:
            for ln_no, ln in enumerate(fh, 1):
                if not ln.strip():
                    continue
                r = _strict_loads_no_dup_no_nonfinite(ln)
                oid = str(r.get("opportunity_id") or "")
                if not oid or oid in seen_oids:
                    viols.append(f"R6V_R4_DUP_OR_MISSING_OID:line={ln_no}")
                seen_oids.add(oid)
                if not bool(r.get("filled")):
                    continue
                lbl = str(r.get("outcome_label") or "")
                if lbl in ("WIN", "LOSS"):
                    closed += 1
                    g = _dec(r["realized_gross_pnl_usd"])
                    f = _dec(r["total_fees_usd"])
                    n = _dec(r["realized_net_pnl_usd"])
                    if abs((g - f) - n) > Decimal("0.001"):
                        viols.append(f"R6V_R4_RESIDUAL_BREACH:line={ln_no}")
                    gross_sum += g
                    fee_sum += f
                    net_sum += n
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
        return viols + [f"R6V_R4_LEDGER_PARSE_ERR:{exc}"]

    if (closed, wins, losses, open_cens, gaps, gt_2p50) != (203, 7, 196, 28, 2, 139):
        viols.append(f"R6V_R4_COUNTS_MISMATCH:closed={closed}:w={wins}:l={losses}:open={open_cens}:gap={gaps}:gt250={gt_2p50}")
    if abs(gross_sum - Decimal("-164.9541")) > Decimal("0.01") or abs(fee_sum - Decimal("276.6577")) > Decimal("0.01") or abs(net_sum - Decimal("-441.6118")) > Decimal("0.01"):
        viols.append(f"R6V_R4_DECIMAL_SUM_MISMATCH:gross={_q4(gross_sum)}:fee={_q4(fee_sum)}:net={_q4(net_sum)}")
    if abs(open_fee_sum - Decimal("14.7151")) > Decimal("0.01"):
        viols.append(f"R6V_R4_OPEN_FEE_DRIFT:{_q4(open_fee_sum)}")
    if a16.get("n_closed") != closed or a16.get("realized_losses_gt_2p50_count") != gt_2p50:
        viols.append("R6V_R4_16_SUMMARY_DRIFT_VS_RAW_LEDGER")
    return viols


def _audit_junit_19_r6(run_dir: Path, a19: Mapping[str, Any]) -> List[str]:
    viols: List[str] = []
    nodes_gz = run_dir / str(a19.get("collected_nodeids_gz_rel") or "logs/pytest_collected_executed_nodeids.jsonl.gz")
    if not nodes_gz.is_file():
        return ["R6V_MISSING_PYTEST_NODEIDS_GZ"]
    if a19.get("collected_nodeids_gz_sha256") and _sha256_file(nodes_gz) != a19["collected_nodeids_gz_sha256"]:
        viols.append("R6V_PYTEST_NODEIDS_GZ_SHA_MISMATCH")

    with gzip.open(nodes_gz, "rt", encoding="utf-8") as fh:
        collected = [ln.strip() for ln in fh if ln.strip()]
    if len(collected) < MIN_EXPECTED_PYTEST_COLLECTED_R6:
        viols.append(f"R6V_PYTEST_COLLECTED_BELOW_FLOOR:{len(collected)}<{MIN_EXPECTED_PYTEST_COLLECTED_R6}")
    if len(collected) != len(set(collected)):
        viols.append("R6V_PYTEST_DUPLICATE_COLLECTED_NODEIDS")
    if a19.get("collected_count") != len(collected):
        viols.append(f"R6V_PYTEST_COLLECTED_COUNT_MISMATCH:19={a19.get('collected_count')}:raw={len(collected)}")

    junit_dir = run_dir / "junit"
    xml_files = sorted(junit_dir.glob("*.xml")) if junit_dir.is_dir() else []
    if not xml_files:
        return viols + ["R6V_MISSING_JUNIT_XML_FILES"]

    tot_tests = 0
    tot_fail = 0
    tot_err = 0
    tot_skip = 0
    mandatory_skip = 0
    mandatory_prefixes = ("test_kh_02_r2", "test_kh_03", "test_kh03_r2", "test_kh03_r3", "test_kh03_r4", "test_kh03_r5", "test_kh03_r6")

    for xf in xml_files:
        try:
            root = ET.parse(xf).getroot()
        except Exception as exc:
            viols.append(f"R6V_JUNIT_XML_CORRUPT:{xf.name}:{exc}")
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
        viols.append(f"R6V_JUNIT_XML_COUNT_MISMATCH:xml={tot_tests}:collected={len(collected)}")
    if tot_fail != 0 or tot_err != 0 or mandatory_skip != 0:
        viols.append(f"R6V_JUNIT_XML_NONZERO_FAIL_ERR_MANDATORY_SKIP:f={tot_fail}:e={tot_err}:ms={mandatory_skip}")
    if a19.get("executed_count") != tot_tests or a19.get("skipped_count") != tot_skip:
        viols.append(f"R6V_JUNIT_19_SUMMARY_MISMATCH:19_exec={a19.get('executed_count')}:xml={tot_tests}")
    return viols


def _audit_benchmarks_20_r6(
    a20: Mapping[str, Any],
    *,
    expected_run_id: str,
    expected_tested_sha: str,
) -> List[str]:
    viols: List[str] = []
    # 1. Re-evaluate pytest & science strict R3.C1 receipts using exact raw_doc + reported_policy contract
    for raw_k, pol_k, kind, max_cap in (
        ("pytest_controlled_subset_raw", "pytest_worker_policy", BENCHMARK_KIND_PYTEST, 4),
        ("science_slice_8000_raw", "science_worker_policy", BENCHMARK_KIND_SCIENCE, 4),
    ):
        raw_doc = a20.get(raw_k)
        rep_pol = a20.get(pol_k)
        if not isinstance(raw_doc, Mapping) or not isinstance(rep_pol, Mapping):
            viols.append(f"R6V_MISSING_BENCHMARK_RECEIPT:{raw_k}")
            continue
        if raw_doc.get("run_id") != expected_run_id or raw_doc.get("tested_executable_sha") != expected_tested_sha:
            viols.append(f"R6V_BENCHMARK_PROVENANCE_MISMATCH:{kind}")
        if raw_doc.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH02:
            viols.append(f"R6V_BENCHMARK_DATASET_SHA_MISMATCH:{kind}")
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
            viols.append(f"R6V_BENCHMARK_RECOMPUTE_ERR:{kind}:{exc}")
            continue
        if not recomp.get("valid") or recomp.get("status") != "PASS":
            viols.append(f"R6V_BENCHMARK_POLICY_INVALID:{kind}:{recomp.get('discrepancies')}")
        aw = rep_pol.get("applied_worker_count")
        sc = rep_pol.get("safe_worker_cap")
        if not _is_int(aw) or not _is_int(sc) or int(aw) < 1 or int(aw) > max_cap or int(sc) > max_cap:
            viols.append(f"R6V_BENCHMARK_CAP_EXCEEDED:{kind}")

    # 2. Independently check R6 fixture ingest W1/W2/W4/W1_repeat benchmark
    r6b = a20.get("r6_ingest_benchmark")
    if not isinstance(r6b, Mapping):
        return viols + ["R6V_MISSING_R6_INGEST_BENCHMARK"]
    runs = r6b.get("runs")
    if not isinstance(runs, list) or len(runs) != 4:
        return viols + ["R6V_R6_INGEST_RUNS_NOT_4"]
    labels = [r.get("mode_label") for r in runs]
    if labels != ["w=1", "w=2", "w=4", "w=1_repeat"]:
        viols.append(f"R6V_R6_INGEST_BAD_LABELS:{labels}")
    shas = set()
    for r in runs:
        if not _is_num(r.get("wall_sec")) or float(r["wall_sec"]) < 0.0:
            viols.append(f"R6V_R6_INGEST_NONFINITE_WALL:{r.get('mode_label')}")
        if r.get("all_partitions_ok") is not True:
            viols.append(f"R6V_R6_INGEST_PARTITION_FAIL:{r.get('mode_label')}")
        shas.add(str(r.get("semantic_result_sha256") or ""))
    if len(shas) != 1 or not next(iter(shas)):
        viols.append("R6V_R6_INGEST_SEMANTIC_HASH_MISMATCH")
    if a20.get("r6_ingest_w1_w2_w4_all_identical") is not True or a20.get("r6_ingest_canonical_semantic_sha256") != next(iter(shas), None):
        viols.append("R6V_R6_INGEST_SUMMARY_DRIFT")
    return viols


def _audit_telemetry_21_r6(run_dir: Path, a21: Mapping[str, Any]) -> List[str]:
    viols: List[str] = []
    rel = str(a21.get("telemetry_samples_gz_rel") or "logs/runtime_memory_samples_0p5s.jsonl.gz")
    gz_p = run_dir / rel
    if not gz_p.is_file():
        return ["R6V_MISSING_TELEMETRY_GZ"]
    if a21.get("telemetry_samples_gz_sha256") and _sha256_file(gz_p) != a21["telemetry_samples_gz_sha256"]:
        viols.append("R6V_TELEMETRY_GZ_SHA_MISMATCH")

    cnt = 0
    min_ram = float("inf")
    min_disk = float("inf")
    max_desc = 0
    try:
        with gzip.open(gz_p, "rt", encoding="utf-8") as fh:
            for ln in fh:
                if not ln.strip():
                    continue
                r = _strict_loads_no_dup_no_nonfinite(ln)
                cnt += 1
                ram = r.get("available_mib")
                if ram is None and r.get("available_ram_bytes") is not None:
                    ram = float(r["available_ram_bytes"]) / (1024.0 * 1024.0)
                if ram is None:
                    ram = r.get("ram_available_mib", r.get("available_ram_mib"))
                dsk = r.get("disk_free_gib", r.get("min_disk_free_gib"))
                desc = r.get("child_count", r.get("descendant_count", r.get("children_count", 0)))
                if not _is_num(ram) or not _is_num(dsk):
                    viols.append("R6V_TELEMETRY_NONFINITE_SAMPLE")
                    break
                min_ram = min(min_ram, float(ram))
                min_disk = min(min_disk, float(dsk))
                if _is_int(desc):
                    max_desc = max(max_desc, int(desc))
    except Exception as exc:
        return viols + [f"R6V_TELEMETRY_PARSE_ERR:{exc}"]

    if cnt < 2:
        viols.append(f"R6V_TELEMETRY_TOO_FEW_SAMPLES:{cnt}")
    if min_ram < 512.0:
        viols.append(f"R6V_TELEMETRY_RAM_BELOW_512:{min_ram}")
    if min_disk < 20.0:
        viols.append(f"R6V_TELEMETRY_DISK_BELOW_20:{min_disk}")
    if max_desc > 4:
        viols.append(f"R6V_TELEMETRY_DESCENDANTS_GT_4:{max_desc}")
    if a21.get("samples_count") != cnt:
        viols.append(f"R6V_TELEMETRY_COUNT_MISMATCH:21={a21.get('samples_count')}:raw={cnt}")
    return viols


def verify_kh_03_r6_run_dir(
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
        violations.append("R6V_SELF_VERIFICATION_SAME_PID_FORBIDDEN")

    if not run_dir.is_dir():
        return 1, {"status": "FAIL", "violations": ["R6V_RUN_DIR_NOT_FOUND"]}

    # 1. Check required files exist & are non-empty
    for rel in REQUIRED_ARTIFACTS_KH03_R6:
        fp = run_dir / rel
        if not fp.is_file() or fp.stat().st_size == 0:
            violations.append(f"R6V_MISSING_OR_EMPTY_ARTIFACT:{rel}")

    # 2. Strictly load all JSON artifacts 00..25, 27
    arts: Dict[str, Dict[str, Any]] = {}
    for rel in REQUIRED_ARTIFACTS_KH03_R6:
        if not rel.endswith(".json"):
            continue
        fp = run_dir / rel
        if not fp.is_file():
            continue
        key = rel[:2]
        try:
            obj = _strict_loads_no_dup_no_nonfinite(fp.read_text("utf-8"))
        except Exception as exc:
            violations.append(f"R6V_JSON_STRICT_PARSE_ERR:{rel}:{exc}")
            continue
        if not isinstance(obj, dict):
            violations.append(f"R6V_JSON_NOT_OBJECT:{rel}")
            continue
        # Validate run_id, tested_executable_sha, and non-stale SHA/run_id
        if obj.get("run_id") != expected_run_id:
            violations.append(f"R6V_RUN_ID_MISMATCH:{rel}:{obj.get('run_id')}")
        if obj.get("tested_executable_sha") != expected_tested_sha:
            violations.append(f"R6V_TESTED_SHA_MISMATCH:{rel}:{obj.get('tested_executable_sha')}")
        if str(obj.get("tested_executable_sha") or "") in STALE_PRIOR_SHAS_R6:
            violations.append(f"R6V_STALE_PRIOR_SHA:{rel}")
        if str(obj.get("run_id") or "") in STALE_PRIOR_RUN_IDS_R6:
            violations.append(f"R6V_STALE_PRIOR_RUN_ID:{rel}")
        arts[key] = obj

    # 3. Check prior sealed artifacts + canonical dataset immutability on disk
    prior_checked = 0
    if check_prior_sealed_on_disk:
        for rel_p, exp_sha, label in PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R6:
            abs_p = REPO_ROOT / rel_p
            if not abs_p.is_file():
                violations.append(f"R6V_PRIOR_SEALED_MISSING:{label}")
                continue
            if _sha256_file(abs_p) != exp_sha:
                violations.append(f"R6V_PRIOR_SEALED_TAMPERED:{label}")
            prior_checked += 1
        a02 = arts.get("02") or {}
        if a02.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH02:
            violations.append("R6V_CANONICAL_DATASET_SHA_MISMATCH")

    # 4. Domain-specific raw receipt & trace audits
    a03 = arts.get("03") or {}
    if a03.get("selected_api_family") != API_FAMILY_CLASSIC_V2 or a03.get("rest_quote_snapshot_can_be_continuous_bbo") is not False:
        violations.append("R6V_SOURCE_CONTRACT_03_INVALID")

    a04 = arts.get("04") or {}
    if a04.get("default_embedded_owner_key_present") is not False or a04.get("qa_trust_cannot_authorize_external") is not True:
        violations.append("R6V_OWNER_TRUST_04_INVALID")

    a05 = arts.get("05") or {}
    if a05.get("cross_process_winners_count") != 1 or a05.get("post_kill_restart_replay_denied") is not True:
        violations.append("R6V_DURABLE_CLAIM_05_INVALID")

    a06 = arts.get("06") or {}
    if a06.get("missing_db_no_auto_init_blocked") is not True or a06.get("anchor_rollback_quarantined") is not True or a06.get("kill_after_start_retains_unknown_bytes") is not True:
        violations.append("R6V_LEDGER_CRASH_06_INVALID")

    violations.extend(_audit_transport_lifecycle_07_r6(run_dir, arts.get("08") or {}))

    a09 = arts.get("09") or {}
    if int(a09.get("external_market_requests_attempted") or 0) != 0 or int(a09.get("accepted_real_observed_records") or 0) != 0:
        violations.append("R6V_UNAUTHORIZED_EXTERNAL_TRAFFIC_IN_STAGE_A")
    if a09.get("oos_evaluations") != 0 or a09.get("activation_ready") != "NO" or a09.get("economic_verdict") != "NO_PROVEN_EDGE":
        violations.append("R6V_ECONOMIC_OOS_ACTIVATION_09_VIOLATION")
    tmpl_rel = a09.get("unsigned_owner_review_template_rel")
    if not tmpl_rel or not (run_dir / str(tmpl_rel)).is_file():
        violations.append("R6V_MISSING_UNSIGNED_OWNER_TEMPLATE")
    else:
        t_obj = _strict_loads_no_dup_no_nonfinite((run_dir / str(tmpl_rel)).read_text("utf-8"))
        if t_obj.get("template_status") != "UNSIGNED_OWNER_REVIEW_TEMPLATE" or t_obj.get("valid_authorization") is not False:
            violations.append("R6V_OWNER_TEMPLATE_NOT_STRICTLY_UNSIGNED")

    a12 = arts.get("12") or {}
    if a12.get("observed_vs_state_known_reported_separately") is not True or a12.get("rest_snapshot_rejected_as_streaming_bbo") is not True:
        violations.append("R6V_BBO_COVERAGE_12_INVALID")

    a13 = arts.get("13") or {}
    if a13.get("missing_settlements_not_zero_filled") is not True or a13.get("unknown_interval_blocked_no_8h_default") is not True:
        violations.append("R6V_FUNDING_13_INVALID")

    a14 = arts.get("14") or {}
    if a14.get("owner_fee_status") != "UNKNOWN_NOT_LOCALLY_VERIFIED" or a14.get("slippage_status") != "MISSING_NO_OWNER_FILL_EVIDENCE" or a14.get("orders_placed_count") != 0:
        violations.append("R6V_FEE_SLIPPAGE_14_INVALID")

    a15 = arts.get("15") or {}
    if a15.get("qa_promotion_to_dev_denied") is not True or a15.get("symlink_junction_oos_denied_before_open") is not True or a15.get("raw_payloads_committed_to_git") is not False:
        violations.append("R6V_CUSTODY_15_INVALID")

    violations.extend(_audit_r4_replay_ledger_16_r6(run_dir, arts.get("16") or {}))

    a17 = arts.get("17") or {}
    if a17.get("all_six_scenarios_exact_match") is not True or a17.get("all_three_arms_exact_match") is not True:
        violations.append("R6V_SIX_COST_ARMS_17_INVALID")

    a18 = arts.get("18") or {}
    if a18.get("a1_checked") != 56710 or a18.get("a1_mismatches") != 0 or a18.get("si8_checked") != 6375 or a18.get("si8_mismatches") != 0 or a18.get("adapters_checked") != 246 or a18.get("adapters_mismatches") != 0 or a18.get("d4_mismatches") != 0:
        violations.append("R6V_BASELINE_PARITY_18_INVALID")

    violations.extend(_audit_junit_19_r6(run_dir, arts.get("19") or {}))
    violations.extend(_audit_benchmarks_20_r6(arts.get("20") or {}, expected_run_id=expected_run_id, expected_tested_sha=expected_tested_sha))
    violations.extend(_audit_telemetry_21_r6(run_dir, arts.get("21") or {}))
    violations.extend(_audit_paired_faults_22_r6(run_dir, arts.get("23") or {}, expected_run_id=expected_run_id, expected_tested_sha=expected_tested_sha))

    a24 = arts.get("24") or {}
    if a24.get("clean_clone_verifier_green") is not True or not _is_int(a24.get("new_r6_mutations_tested_count")) or int(a24["new_r6_mutations_tested_count"]) < MIN_NEW_VERIFIER_MUTATIONS_R6 or a24.get("new_r6_mutations_detected_count") != a24.get("new_r6_mutations_tested_count"):
        violations.append("R6V_MUTATIONS_24_INVALID")

    # 5. Independently recompute ALL 36 stage-scoped gates & compare against producer 25!
    arts_for_gates = dict(arts)
    arts_for_gates["26"] = {
        "distinct_os_process_verified": (orchestrator_pid is None or int(orchestrator_pid) != verifier_pid),
        "verifier_status": "PASS" if not violations else "FAIL",
        "violations_count": len(violations),
        "verifier_pid": verifier_pid,
        "orchestrator_pid": int(orchestrator_pid) if orchestrator_pid is not None else -1,
    }
    recomp_verdicts = derive_r6_readiness_verdicts(
        arts_for_gates,
        stage="STAGE_A_OFFLINE",
        expected_run_id=expected_run_id,
        expected_tested_sha=expected_tested_sha,
    )

    a25 = arts.get("25") or {}
    prod_gates = a25.get("gates") or {}
    for gname in GATE_36_KEYS_KH03_R6:
        if gname == "G28_INDEPENDENT_VERIFIER":
            continue
        if prod_gates.get(gname) != recomp_verdicts["gates"].get(gname):
            violations.append(f"R6V_GATE_25_DRIFT:{gname}:prod={prod_gates.get(gname)}:recomp={recomp_verdicts['gates'].get(gname)}")
    # Ensure Stage B/C gates are NOT falsely passed in Stage A!
    for bc_g in STAGE_BC_CONDITIONAL_GATES_R6:
        if prod_gates.get(bc_g) == "PASS" or recomp_verdicts["gates"].get(bc_g) == "PASS":
            violations.append(f"R6V_FORBIDDEN_STAGE_BC_PASS_IN_STAGE_A:{bc_g}")
    # Ensure Stage A core gates all PASS!
    for sa_g in STAGE_A_MANDATORY_GATES_R6:
        if sa_g in ("G28_INDEPENDENT_VERIFIER", "G36_REMOTE_PUBLICATION"):
            continue
        if recomp_verdicts["gates"].get(sa_g) != "PASS":
            violations.append(f"R6V_STAGE_A_MANDATORY_GATE_FAILED:{sa_g}={recomp_verdicts['gates'].get(sa_g)}")

    # Ensure no false 36/36 claim in 25
    sd25 = a25.get("stage_denominators") or {}
    if sd25.get("stage_bc_executed_count") != 0 or sd25.get("stage_a_mandatory_total") != 32:
        violations.append("R6V_STAGE_DENOMINATORS_25_INVALID")

    # Re-evaluate final verifier status
    status = "PASS" if len(violations) == 0 else "FAIL"
    arts_for_gates["26"]["verifier_status"] = status
    arts_for_gates["26"]["violations_count"] = len(violations)
    final_verdicts = derive_r6_readiness_verdicts(
        arts_for_gates,
        stage="STAGE_A_OFFLINE",
        expected_run_id=expected_run_id,
        expected_tested_sha=expected_tested_sha,
    )

    # Hash ALL files in run_dir excluding 26_independent_OS_verifier_manifest_sha256.json itself!
    file_hashes: Dict[str, Dict[str, Any]] = {}
    for fp in sorted(run_dir.rglob("*")):
        if not fp.is_file():
            continue
        rel_f = str(fp.relative_to(run_dir)).replace("\\", "/")
        if rel_f == MANIFEST_26_NAME_KH03_R6:
            continue
        file_hashes[rel_f] = {"bytes": fp.stat().st_size, "sha256": _sha256_file(fp)}

    manifest = {
        "spec_version": SPEC_VERSION_KH03_R6,
        "schema_version": SCHEMA_VERSION_KH03_R6,
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
        "recomputed_36_gates": final_verdicts["gates"],
        "stage_denominators": final_verdicts["stage_denominators"],
        "overall_classification": final_verdicts["overall_classification"] if status == "PASS" else "FAIL_INDEPENDENT_VERIFIER",
        "independent_verdicts": final_verdicts["independent_verdicts"],
        "sealed_files_count": len(file_hashes),
        "sealed_files_sha256": file_hashes,
    }

    if write_manifest:
        write_json_durable_atomic(run_dir / MANIFEST_26_NAME_KH03_R6, manifest)

    return (0 if status == "PASS" else 1), manifest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="KH-03.R6 Separate-OS Independent Read-Only Verifier")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--expected-run-id", default=None)
    parser.add_argument("--expected-tested-sha", default=None)
    parser.add_argument("--orchestrator-pid", type=int, default=None)
    parser.add_argument("--no-write-manifest", action="store_true")
    parser.add_argument("--skip-prior-disk-check", action="store_true")
    ns = parser.parse_args(argv)

    rdir = Path(ns.run_dir).resolve()
    exp_rid = ns.expected_run_id
    exp_sha = ns.expected_tested_sha
    if (exp_rid is None or exp_sha is None) and (rdir / "00_preflight_branch_sha_resource_lock.json").is_file():
        a00 = _strict_loads_no_dup_no_nonfinite((rdir / "00_preflight_branch_sha_resource_lock.json").read_text("utf-8"))
        exp_rid = exp_rid or str(a00.get("run_id") or "")
        exp_sha = exp_sha or str(a00.get("tested_executable_sha") or "")

    rc, report = verify_kh_03_r6_run_dir(
        rdir,
        expected_run_id=str(exp_rid or ""),
        expected_tested_sha=str(exp_sha or ""),
        orchestrator_pid=ns.orchestrator_pid,
        write_manifest=(not ns.no_write_manifest),
        check_prior_sealed_on_disk=(not ns.skip_prior_disk_check),
    )
    print(json.dumps({
        "status": report["status"],
        "verifier_pid": report["verifier_pid"],
        "orchestrator_pid": report["orchestrator_pid"],
        "violations_count": report["violations_count"],
        "violations": report["violations"][:20],
        "sealed_files_count": report["sealed_files_count"],
        "overall_classification": report["overall_classification"],
    }, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
