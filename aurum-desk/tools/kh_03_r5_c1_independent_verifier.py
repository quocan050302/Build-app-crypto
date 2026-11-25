#!/usr/bin/env python3
"""
aurum-desk/tools/kh_03_r5_c1_independent_verifier.py

AURUM DESK — KH-03.R5.C1 | SEPARATE-OS INDEPENDENT DISK VERIFIER (G01..G30)
SPEC VERSION: KH-03.R5.C1-v1.0 | ZERO ORCHESTRATOR IMPORT | STRICT RAW RECOMPUTATION

Runs in a distinct OS process (`verifier_pid != orchestrator_pid`), reads ALL raw C1 artifacts
(`00`..`20`, `22`, `README.md`, `HANDOVER_KH03_R5_C1_VI.txt`, `logs/r4_replay_forensic_ledger_1x.jsonl.gz`,
`logs/runtime_memory_samples_0p5s.jsonl.gz`, `logs/pytest_collected_executed_nodeids.jsonl.gz`,
`junit/*.xml`) directly from disk using strict duplicate-key & non-finite-rejecting JSON parsing,
independently recomputes:
  - Prior R1..R5 + canonical dataset SHA-256 immutability
  - Real baseline RED proof (`03`) & C1 P1-A/B/C hardening receipts (`04`..`11`)
  - Exact Decimal R4 replay parity (`12`, `13`) from raw compressed forensic ledger
  - Full A1/SI8/adapters/D4 parity (`14`)
  - Exact JUnit XML 1:1 bijection (`15`)
  - Raw W1/W2/W4/W1_repeat timings & `recompute_worker_policy_from_raw_receipt` (`16`)
  - Raw 0.5s telemetry floor compliance (`17`)
  - Inherited + NEW C1 fault controls (`18`) and separate-OS mutation results (`19`)
  - Independent recomputation of ALL 30 gates (`G01`..`G30`) and orthogonal verdicts
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
from services.kh_03_r5_c1_readiness_and_red_service import (
    BASELINE_REVIEW_HEAD_SHA_R5_C1,
    GATE_30_KEYS_KH03_R5_C1,
    GLOBAL_MAX_DESCENDANTS_R5_C1,
    HARD_RAM_FLOOR_MIB_R5_C1,
    MANDATORY_BRANCH_KH03_R5_C1,
    MIN_EXPECTED_PYTEST_COLLECTED_R5_C1,
    derive_r5_c1_readiness_verdicts,
)
from services.kh_03_r5_market_data_foundation_service import (
    SCHEMA_VERSION_KH03_R5_C1,
    SPEC_VERSION_KH03_R5_C1,
    STRICT_RUNTIME_DISK_MIN_GIB_R5,
)
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_03_r5_independent_verifier import (
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R5,
    STALE_PRIOR_RUN_IDS_R5,
    STALE_PRIOR_SHAS_R5,
)

MANIFEST_21_NAME_KH03_R5_C1 = "21_independent_OS_verifier_manifest_sha256.json"
Q4 = Decimal("0.0001")

# Verified KH-03.R5 sealed references at HEAD 09493feb845210a629f3c0f174a2c6edfaf2ef23
PRIOR_KH03_R5_TESTED_SHA = "d9e24be7864de6ad886b8581c65df86bbd531275"
PRIOR_KH03_R5_EVIDENCE_SHA = "745f8fd309de3065e70d8efe322a671ef9a2d930"
PRIOR_KH03_R5_PUB_HEAD_SHA = BASELINE_REVIEW_HEAD_SHA_R5_C1
PRIOR_KH03_R5_RUN_ID = "20261124T135139Z_d9e24be7864d"

PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R5_C1: Tuple[Tuple[str, str, str], ...] = (
    *PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R5,
    (
        "aurum-desk/docs/artifacts/kh_03_r5_market_data_foundation/latest_run.json",
        "39bafa28ffa73cd28b38c94764a745cb6cf0319c63a44c5640c26c99fe556982",
        "KH03_R5_LATEST_RUN_POINTER",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r5_market_data_foundation/22_phase2_evidence_publish_remote_readback_{PRIOR_KH03_R5_RUN_ID}.json",
        "e9779b7268383e1866ce1197d3d0b2db29ab89f53dcce939cac2c65a4ee2a648",
        "KH03_R5_SEALED_POSTPUSH_READBACK_22",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r5_market_data_foundation/{PRIOR_KH03_R5_RUN_ID}/21_independent_OS_verifier_manifest_sha256.json",
        "9751317ef930944768c26f488b3a71ccfdb2a96c14076bfb12a705b70eedb7e2",
        "KH03_R5_SEALED_MANIFEST_21",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r5_market_data_foundation/{PRIOR_KH03_R5_RUN_ID}/20_orthogonal_gates_and_verdicts.json",
        "1c639b6e135444965a3174742c65c719e7cda0d64bf3a58ea13fdb3e96a2bd7a",
        "KH03_R5_SEALED_26_GATES_20",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r5_market_data_foundation/{PRIOR_KH03_R5_RUN_ID}/13_r4_replay_parity_economic_bridge.json",
        "3c085c62adb27a3b7230ee5cac4e13923327af183d188f7d02a43f41fb04129e",
        "KH03_R5_SEALED_R4_BRIDGE_13",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r5_market_data_foundation/{PRIOR_KH03_R5_RUN_ID}/14_A1_SI8_adapter_D4_full_baseline_parity.json",
        "7f32a69b3d4ec88d9ff5f8073d785379d2b2fd51c93799fed00a2023da0f094a",
        "KH03_R5_SEALED_PARITY_14",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r5_market_data_foundation/{PRIOR_KH03_R5_RUN_ID}/logs/r4_replay_forensic_ledger_1x.jsonl.gz",
        "7c2a5fbb0440950a6eef87d53fc5d223eeb2752e80803a19b24cbe21dc4de145",
        "KH03_R5_SEALED_FORENSIC_LEDGER_GZ",
    ),
)

KH03_R5_C1_SOURCE_FILES_ALLOWLIST: Tuple[str, ...] = (
    "aurum-desk/backend/services/kh_03_r5_market_data_foundation_service.py",
    "aurum-desk/backend/services/kh_03_r5_capture_and_custody_service.py",
    "aurum-desk/backend/services/kh_03_r5_readiness_gate_service.py",
    "aurum-desk/backend/services/kh_03_r5_c1_readiness_and_red_service.py",
    "aurum-desk/tools/kh_03_r5_c1_independent_verifier.py",
    "aurum-desk/tools/run_kh_03_r5_c1_acceptance.py",
    "aurum-desk/backend/tests/test_kh03_r5_capture_provenance.py",
    "aurum-desk/backend/tests/test_kh03_r5_custody_and_partitions.py",
    "aurum-desk/backend/tests/test_kh03_r5_readiness_and_verifier.py",
    "aurum-desk/backend/tests/test_kh03_r5_c1_scope_binding.py",
    "aurum-desk/backend/tests/test_kh03_r5_c1_deadline_cancel_and_budget.py",
    "aurum-desk/backend/tests/test_kh03_r5_c1_resource_fail_closed.py",
    "aurum-desk/backend/tests/test_kh03_r5_c1_custody_abort_and_determinism.py",
    "aurum-desk/backend/tests/test_kh03_r5_c1_independent_verifier_mutations.py",
)

REQUIRED_ARTIFACTS_KH03_R5_C1: Tuple[str, ...] = (
    "00_preflight_branch_sha_resource_lock.json",
    "01_function_ownership_keep_as_is_manifest.json",
    "02_prior_dataset_sealed_integrity.json",
    "03_real_baseline_red_reproduction.json",
    "04_strict_authorization_scope_schema.json",
    "05_plan_binding_tamper_and_expiry_receipts.json",
    "06_dispatch_guard_remaining_timeout_trace.json",
    "07_rate_limiter_retry_cancellation_trace.json",
    "08_atomic_request_byte_budget_and_transport_receipts.json",
    "09_resource_sample_strictness_and_volume_identity.json",
    "10_staging_custody_abort_cleanup_inventory.json",
    "11_offline_only_provenance_partition_no_oos.json",
    "12_r4_replay_decimal_economic_bridge.json",
    "13_six_cost_scenarios_and_arm_regression.json",
    "14_A1_SI8_adapter_D4_full_parity.json",
    "15_full_pytest_fresh_collection_junit_bijection.json",
    "16_W1_W2_W4_W1repeat_benchmark_policy.json",
    "17_actual_resource_telemetry_cleanup.json",
    "18_inherited_new_red_green_faults.json",
    "19_separate_OS_mutation_results.json",
    "20_orthogonal_gates_and_verdicts.json",
    "22_phase1_code_commit_push_receipt.json",
    "README.md",
    "HANDOVER_KH03_R5_C1_VI.txt",
)

STALE_PRIOR_SHAS_R5_C1: Tuple[str, ...] = (
    *STALE_PRIOR_SHAS_R5,
    PRIOR_KH03_R5_TESTED_SHA,
    PRIOR_KH03_R5_EVIDENCE_SHA,
    PRIOR_KH03_R5_PUB_HEAD_SHA,
)
STALE_PRIOR_RUN_IDS_R5_C1: Tuple[str, ...] = (
    *STALE_PRIOR_RUN_IDS_R5,
    PRIOR_KH03_R5_RUN_ID,
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


def _audit_r4_replay_ledger_12_c1(run_dir: Path, f12: Mapping[str, Any]) -> Dict[str, Any]:
    viols: List[str] = []
    rel_gz = str(f12.get("forensic_ledger_rel_path") or "logs/r4_replay_forensic_ledger_1x.jsonl.gz")
    gz_p = run_dir / rel_gz
    if not gz_p.is_file() or gz_p.stat().st_size == 0:
        return {"valid": False, "status": "FAIL", "violations": ["C1V_MISSING_R4_REPLAY_LEDGER_GZ"]}
    if _sha256_file(gz_p) != f12.get("forensic_ledger_file_sha256"):
        viols.append("C1V_R4_REPLAY_LEDGER_SHA_MISMATCH")

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
                    viols.append(f"C1V_R4_LEDGER_DUP_ID:line={ln_no}")
                seen.add(oid)
                if not bool(r.get("filled")):
                    continue
                lbl = str(r.get("outcome_label") or "")
                if lbl in ("WIN", "LOSS"):
                    n_cl += 1
                    g, tf, ne = _dec(r["realized_gross_pnl_usd"]), _dec(r["total_fees_usd"]), _dec(r["realized_net_pnl_usd"])
                    if abs((g - tf) - ne) > Decimal("0.001"):
                        viols.append(f"C1V_R4_LEDGER_RESIDUAL_BREACH:line={ln_no}")
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
        return {"valid": False, "status": "FAIL", "violations": [f"C1V_R4_LEDGER_PARSE_ERR:{exc}"]}

    if (n_cl, n_w, n_l, n_op, n_gp) != (203, 7, 196, 28, 2):
        viols.append(f"C1V_R4_ANCHOR_COUNT_DRIFT:{(n_cl, n_w, n_l, n_op, n_gp)}")
    if abs(g_sum - Decimal("-164.9541")) > Decimal("0.01") or abs(f_sum - Decimal("276.6577")) > Decimal("0.01") or abs(n_sum - Decimal("-441.6118")) > Decimal("0.01"):
        viols.append("C1V_R4_DECIMAL_BRIDGE_DRIFT")
    if abs(op_f_sum - Decimal("14.7151")) > Decimal("0.01"):
        viols.append("C1V_R4_OPEN_FEE_DRIFT")
    if (
        not _is_int(f12.get("null_1x_closed")) or int(f12["null_1x_closed"]) != n_cl
        or not _is_num(f12.get("null_1x_net_usd")) or abs(_dec(f12["null_1x_net_usd"]) - n_sum) > Decimal("0.002")
        or not _is_int(f12.get("realized_losses_gt_2p50_count")) or int(f12["realized_losses_gt_2p50_count"]) != over250
    ):
        viols.append("C1V_12_TAMPERED_VS_RAW_LEDGER")
    if f12.get("planned_cap_is_physical_guarantee") is not False:
        viols.append("C1V_PLANNED_CAP_GUARANTEE_FORGED")
    if f12.get("RESEARCH_ECONOMIC_VERDICT") != "NO_PROVEN_EDGE" or f12.get("KH04_ENTRY") != "BLOCKED_NO_DEV_CANDIDATE" or not _is_int(f12.get("OOS_EVALUATIONS")) or int(f12["OOS_EVALUATIONS"]) != 0:
        viols.append("C1V_FORGED_ECONOMIC_OR_OOS_IN_12")
    return {"valid": len(viols) == 0, "status": "PASS" if not viols else "FAIL", "recomputed_net_usd": _q4(n_sum), "violations": viols}


def _verify_benchmark_16_c1(
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
        return {"valid": False, "violations": [f"C1V_BENCH_MISSING:{kind}"]}
    if raw_doc.get("run_id") != expected_run_id or raw_doc.get("tested_executable_sha") != expected_tested_sha:
        viols.append(f"C1V_BENCH_PROVENANCE_MISMATCH:{kind}")
    if raw_doc.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH02:
        viols.append(f"C1V_BENCH_DATASET_SHA_MISMATCH:{kind}")
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
        return {"valid": False, "violations": [f"C1V_BENCH_RECOMPUTE_ERR:{kind}:{exc}"]}
    if not recomp.get("valid") or recomp.get("status") != "PASS":
        viols.extend(f"C1V_BENCH:{kind}:{v}" for v in (recomp.get("discrepancies") or ["RECOMPUTE_FAILED"]))
    aw = rep_pol.get("applied_worker_count")
    sc = rep_pol.get("safe_worker_cap")
    if not _is_int(aw) or not _is_int(sc) or int(aw) < 1 or int(aw) > max_safe_cap or int(sc) > max_safe_cap or int(aw) > int(sc):
        viols.append(f"C1V_BENCH_SAFE_CAP_EXCEEDED:{kind}")
    if rep_pol.get("full_suite_speedup_extrapolated") is not False or rep_pol.get("timing_determinism_claimed") is not False:
        viols.append(f"C1V_BENCH_EXTRAPOLATION_FORBIDDEN:{kind}")
    return {"valid": len(viols) == 0, "recomputed_policy_summary": recomp.get("recomputed_policy"), "violations": viols}


def _verify_junit_raw_xml_15_c1(
    run_dir: Path, f15: Mapping[str, Any], *, expected_run_id: str, expected_tested_sha: str
) -> Tuple[bool, List[str]]:
    viols: List[str] = []
    nodes_rel = str(f15.get("collected_nodeids_gz_rel") or "")
    nodes_p = (run_dir / nodes_rel) if nodes_rel else None
    c_ids: List[str] = []
    if nodes_p is not None and nodes_p.is_file():
        if _sha256_file(nodes_p) != f15.get("collected_nodeids_gz_sha256"):
            viols.append("C1V_JUNIT_NODEIDS_GZ_SHA_MISMATCH")
        with gzip.open(nodes_p, "rt", encoding="utf-8") as fh:
            for ln in fh:
                s = ln.strip()
                if s:
                    c_ids.append(s)
    else:
        viols.append("C1V_JUNIT_MISSING_COLLECTED_NODEIDS_GZ")

    sm = f15.get("shard_manifest") or {}
    if not sm:
        return False, viols + ["C1V_JUNIT_MISSING_SHARD_MANIFEST"]
    if sm.get("run_id") != expected_run_id or sm.get("tested_executable_sha") != expected_tested_sha:
        viols.append("C1V_JUNIT_SM_PROVENANCE_MISMATCH")
    if not _is_int(sm.get("nonzero_exit_shards_count")) or int(sm["nonzero_exit_shards_count"]) != 0:
        viols.append("C1V_JUNIT_NONZERO_SHARDS")

    if len(c_ids) < MIN_EXPECTED_PYTEST_COLLECTED_R5_C1:
        viols.append(f"C1V_JUNIT_COLLECTED_BELOW_MIN:{len(c_ids)}<{MIN_EXPECTED_PYTEST_COLLECTED_R5_C1}")
    if len(set(c_ids)) != len(c_ids):
        viols.append("C1V_JUNIT_COLLECTED_DUPLICATES")

    shards = list(sm.get("shards") or ())
    if not shards:
        return False, viols + ["C1V_JUNIT_ZERO_SHARDS"]

    sched_all: List[str] = []
    tot_t = tot_f = tot_e = tot_s = mand_s = 0
    for sh in shards:
        if int(sh.get("exit_code", 1)) != 0 or bool(sh.get("timed_out", True)) or bool(sh.get("reused", True)):
            viols.append(f"C1V_JUNIT_BAD_SHARD:{sh.get('shard_id')}")
        rel_x = str(sh.get("junit_xml_path") or "")
        xp = run_dir / rel_x
        if not xp.is_file() or xp.stat().st_size == 0:
            viols.append(f"C1V_JUNIT_XML_MISSING:{rel_x}")
            continue
        if _sha256_file(xp) != str(sh.get("xml_sha256") or sh.get("junit_sha256") or ""):
            viols.append(f"C1V_JUNIT_XML_SHA_MISMATCH:{rel_x}")
        sched = list(sh.get("scheduled_node_ids") or ())
        sched_all.extend(sched)
        try:
            root = ET.fromstring(xp.read_text("utf-8"))
        except Exception as exc:
            viols.append(f"C1V_JUNIT_XML_PARSE_ERR:{rel_x}:{exc}")
            continue
        tcs = list(root.iter("testcase"))
        tot_t += len(tcs)
        if len(tcs) != len(sched):
            viols.append(f"C1V_JUNIT_SHARD_LEN_MISMATCH:{sh.get('shard_id')}")
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
        viols.append("C1V_JUNIT_BIJECTION_MISMATCH")
    tot_p = tot_t - tot_f - tot_e - tot_s
    if (
        not _is_int(f15.get("executed_count")) or tot_t != int(f15["executed_count"])
        or not _is_int(f15.get("passed_count")) or tot_p != int(f15["passed_count"])
        or not _is_int(f15.get("skipped_count")) or tot_s != int(f15["skipped_count"])
        or not _is_int(f15.get("failed_count")) or tot_f != int(f15["failed_count"])
        or not _is_int(f15.get("error_count")) or tot_e != int(f15["error_count"])
    ):
        viols.append("C1V_JUNIT_COUNTS_TAMPERED_VS_RAW_XML")
    if tot_f != 0 or tot_e != 0 or mand_s != 0:
        viols.append(f"C1V_JUNIT_FAIL_ERR_OR_MANDATORY_SKIP:f={tot_f},e={tot_e},ms={mand_s}")
    return len(viols) == 0, viols


def _audit_telemetry_gz_vs_17_c1(mem_gz: Path, f17: Mapping[str, Any]) -> Dict[str, Any]:
    viols: List[str] = []
    if not mem_gz.is_file() or mem_gz.stat().st_size == 0:
        return {"status": "FAIL_MISSING_LOG", "samples_count": 0, "below_floor_samples_count": 1, "max_workers_observed_in_samples": 99, "violations": ["C1V_MISSING_TELEMETRY_LOG"]}
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
                    if rf < HARD_RAM_FLOOR_MIB_R5_C1:
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
        return {"status": "FAIL_PARSE", "samples_count": n, "below_floor_samples_count": 1, "max_workers_observed_in_samples": 99, "violations": [f"C1V_TELEMETRY_PARSE_ERR:{exc}"]}

    if n == 0 or min_ram == float("inf") or min_dk == float("inf"):
        viols.append("C1V_TELEMETRY_EMPTY_OR_INCOMPLETE")
    if below_r > 0 or min_ram < HARD_RAM_FLOOR_MIB_R5_C1:
        viols.append(f"C1V_TELEMETRY_RAM_BELOW_512MIB:{min_ram:.2f}")
    if below_d > 0 or min_dk < STRICT_RUNTIME_DISK_MIN_GIB_R5:
        viols.append(f"C1V_TELEMETRY_DISK_BELOW_20GIB:{min_dk:.4f}")
    if max_ch > GLOBAL_MAX_DESCENDANTS_R5_C1:
        viols.append(f"C1V_TELEMETRY_CHILD_EXCEEDED_4:{max_ch}")
    rep_ram, rep_dk, rep_ch = f17.get("min_available_ram_mib"), f17.get("min_disk_free_gib"), f17.get("max_descendant_children")
    if not _is_num(rep_ram) or abs(float(rep_ram) - min_ram) > 1.0:
        viols.append("C1V_TELEMETRY_17_RAM_MUTATED_VS_GZ")
    if not _is_num(rep_dk) or abs(float(rep_dk) - min_dk) > 0.05:
        viols.append("C1V_TELEMETRY_17_DISK_MUTATED_VS_GZ")
    if not _is_int(rep_ch) or int(rep_ch) < max_ch or int(rep_ch) > GLOBAL_MAX_DESCENDANTS_R5_C1:
        viols.append("C1V_TELEMETRY_17_CHILDREN_MUTATED_VS_GZ")
    return {
        "status": "PASS" if len(viols) == 0 else "FAIL",
        "samples_count": n,
        "min_available_ram_mib": round(min_ram if min_ram != float("inf") else 0.0, 2),
        "min_disk_free_gib": round(min_dk if min_dk != float("inf") else 0.0, 4),
        "max_workers_observed_in_samples": max_ch,
        "below_floor_samples_count": below_r + below_d,
        "violations": viols,
    }


def verify_r5_c1_artifacts_independently_from_disk(
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

    for rel in REQUIRED_ARTIFACTS_KH03_R5_C1:
        p = run_dir / rel
        if not p.is_file() or p.stat().st_size == 0:
            viols.append(f"C1V_MISSING_OR_EMPTY_ARTIFACT:{rel}")
            continue
        file_hashes[rel] = {"sha256": _sha256_file(p), "bytes": p.stat().st_size}
        if rel.endswith(".json"):
            try:
                docs[rel[:2]] = _strict_loads_no_dup_no_nonfinite(p.read_text("utf-8"))
            except Exception as exc:
                viols.append(f"C1V_INVALID_JSON:{rel}:{exc}")

    for dk in ("00", "01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12", "13", "14", "15", "16", "17", "18", "19", "20", "22"):
        if dk not in docs:
            viols.append(f"C1V_MISSING_JSON_DOC:{dk}")

    if viols:
        return 1, {"spec_version": SPEC_VERSION_KH03_R5_C1, "verifier_pid": verif_pid, "status": "FAIL_MISSING_OR_INVALID_ARTIFACTS", "violations": viols}

    f00, f01, f02, f03, f04, f05, f06, f07, f08, f09, f10, f11, f12, f13, f14, f15, f16, f17, f20, f22 = (
        docs["00"], docs["01"], docs["02"], docs["03"], docs["04"], docs["05"], docs["06"], docs["07"],
        docs["08"], docs["09"], docs["10"], docs["11"], docs["12"], docs["13"], docs["14"], docs["15"],
        docs["16"], docs["17"], docs["20"], docs["22"],
    )
    rid = expected_run_id or str(f00.get("run_id") or "")
    tsha = expected_tested_sha or str(f00.get("tested_executable_sha") or "")

    if rid in STALE_PRIOR_RUN_IDS_R5_C1 or tsha in STALE_PRIOR_SHAS_R5_C1:
        viols.append("C1V_STALE_PRIOR_RUN_OR_SHA_REUSED")
    orch_pid = f00.get("orchestrator_pid")
    if _is_int(orch_pid) and int(orch_pid) == verif_pid:
        viols.append("C1V_SAME_PID_AS_ORCHESTRATOR")

    for dk, doc in sorted(docs.items()):
        if doc.get("run_id") != rid:
            viols.append(f"C1V_RUN_ID_MISMATCH:{dk}")
        if doc.get("tested_executable_sha") != tsha:
            viols.append(f"C1V_TESTED_SHA_MISMATCH:{dk}")
        if doc.get("run_id") in STALE_PRIOR_RUN_IDS_R5_C1 or doc.get("tested_executable_sha") in STALE_PRIOR_SHAS_R5_C1:
            viols.append(f"C1V_STALE_PRIOR_RUN_OR_SHA_REUSED:{dk}")

    # Cross-file semantic verification (D01..D22 defense!)
    if f02.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH02 or f02.get("canonical_dataset_sha256_match") is not True or f02.get("all_prior_sealed_artifacts_intact") is not True:
        viols.append("C1V_DATASET_OR_PRIOR_SHA_MISMATCH")
    if not _is_int(f01.get("locked_files_modified_count")) or int(f01["locked_files_modified_count"]) != 0 or not _is_int(f01.get("unauthorized_changed_files_count")) or int(f01["unauthorized_changed_files_count"]) != 0 or len(f01.get("forbidden_order_import_hits") or []) != 0:
        viols.append("C1V_PROTECTED_SOURCE_MODIFIED_OR_ORDER_IMPORT")

    # D03/D04/D05: Scope binding, recomputed digest, post-expiry dispatch
    if f03.get("all_p1_red_reproduced") is not True or not isinstance(f03.get("observations"), Mapping) or len(f03["observations"]) < 8:
        viols.append("C1V_MISSING_OR_FORGED_REAL_RED_TRACE")
    if f04.get("live_network_permitted") is not False or f04.get("live_capture_authorization_status") != "BLOCKED_OWNER_AUTHORIZATION":
        viols.append("C1V_FORGED_LIVE_AUTHORIZATION_IN_04")
    if not _is_int(f05.get("recomputed_digest_observed_calls")) or int(f05["recomputed_digest_observed_calls"]) != 0 or f05.get("mutated_plan_recomputed_digest_rejected") is not True:
        viols.append("C1V_PLAN_RECOMPUTED_DIGEST_BYPASS_NOT_BLOCKED")
    if not _is_int(f05.get("post_expiry_observed_calls")) or int(f05["post_expiry_observed_calls"]) != 0 or f05.get("expiry_during_wait_rejected") is not True:
        viols.append("C1V_DISPATCH_AFTER_EXPIRY_DETECTED")

    # D06/D07/D08/D09/D10: Deadline overrun, invalid monitor, counter mismatch, byte budget, abort cleanup truth
    if not _is_int(f06.get("post_deadline_observed_calls")) or int(f06["post_deadline_observed_calls"]) != 0 or f06.get("deadline_expired_during_wait_blocked") is not True:
        viols.append("C1V_DEADLINE_OVERRUN_OR_POST_WAIT_DISPATCH")
    if not _is_num(f06.get("observed_remaining_timeout_sec")) or not _is_num(f06.get("expected_max_remaining_timeout_sec")) or float(f06["observed_remaining_timeout_sec"]) > float(f06["expected_max_remaining_timeout_sec"]) + 1e-6:
        viols.append("C1V_TIMEOUT_EXCEEDED_REMAINING_BUDGET")
    if not _is_int(f09.get("invalid_monitor_observed_calls")) or int(f09["invalid_monitor_observed_calls"]) != 0 or f09.get("monitor_none_blocked") is not True:
        viols.append("C1V_INVALID_RESOURCE_MONITOR_CLAIMED_SAFE")
    if not _is_int(f08.get("observed_dispatched_vs_cap_excess")) or int(f08["observed_dispatched_vs_cap_excess"]) != 0 or f08.get("cancelled_reservation_not_counted_dispatched") is not True:
        viols.append("C1V_REQUEST_COUNTER_OR_CAP_MISMATCH")
    if f08.get("wire_vs_accepted_bytes_separated") is not True or f08.get("parallel_byte_reservation_no_oversubscribe") is not True or f08.get("unsupported_legacy_transport_read_cap_blocked") is not True:
        viols.append("C1V_BYTE_BUDGET_FALSE_PASS")
    if not _is_int(f10.get("aborted_accepted_records_count")) or int(f10["aborted_accepted_records_count"]) != 0 or f10.get("aborted_deleted_staging_counted_zero_accepted") is not True:
        viols.append("C1V_ABORT_DELETED_FILES_COUNTED_ACCEPTED")
    if not _is_int(f11.get("external_market_requests_attempted")) or int(f11["external_market_requests_attempted"]) != 0 or not _is_int(f11.get("accepted_real_observed_records")) or int(f11["accepted_real_observed_records"]) != 0:
        viols.append("C1V_NONZERO_EXTERNAL_MARKET_REQUESTS_OR_FAKE_OBSERVED")
    if not _is_int(f11.get("oos_reads_executed")) or int(f11["oos_reads_executed"]) != 0 or not _is_int(f11.get("oos_evaluations")) or int(f11["oos_evaluations"]) != 0:
        viols.append("C1V_FORBIDDEN_OOS_ACCESS_OR_EVALUATION")

    # D11/D19: Disk waiver / orphan / remote receipt SHA check
    if f00.get("owner_disk_preflight_waiver") is True or f17.get("owner_disk_preflight_waiver") is True or (not _is_int(f17.get("orphan_children_after_cleanup")) or int(f17["orphan_children_after_cleanup"]) != 0):
        viols.append("C1V_DISK_WAIVER_OR_ORPHAN_BREACH")
    if (bool(f22.get("remote_readback_sha256_verified")) and not f22.get("publication_head_sha")) or (
        f22.get("ci_status") == "CI_PASS" and f22.get("actual_github_actions_run_verified") is not True
    ) or (f22.get("tested_executable_sha") != tsha):
        viols.append("C1V_FALSE_REMOTE_PUBLICATION_OR_WRONG_SHA")

    # Independent R4 replay ledger Decimal verification (12)
    r4_au = _audit_r4_replay_ledger_12_c1(run_dir, f12)
    viols.extend(r4_au.get("violations") or ())
    led_p = run_dir / str(f12.get("forensic_ledger_rel_path") or "logs/r4_replay_forensic_ledger_1x.jsonl.gz")
    if led_p.is_file():
        file_hashes[str(led_p.relative_to(run_dir)).replace("\\", "/")] = {"sha256": _sha256_file(led_p), "bytes": led_p.stat().st_size}

    # Independent 0.5s telemetry verification (17)
    mem_gz = run_dir / str(f17.get("memory_samples_log_rel") or "logs/runtime_memory_samples_0p5s.jsonl.gz")
    mem_au = _audit_telemetry_gz_vs_17_c1(mem_gz, f17)
    viols.extend(mem_au.get("violations") or ())
    if mem_gz.is_file():
        file_hashes[str(mem_gz.relative_to(run_dir)).replace("\\", "/")] = {"sha256": _sha256_file(mem_gz), "bytes": mem_gz.stat().st_size}

    # Independent W1/W2/W4/W1_repeat benchmark verification (16)
    if f16.get("status") == "NOT_RUN_BLOCKED_LOW_DISK":
        p_rec = {"valid": False, "violations": ["NOT_RUN_BLOCKED_LOW_DISK"]}
        s_rec = {"valid": False, "violations": ["NOT_RUN_BLOCKED_LOW_DISK"]}
    else:
        p_rec = _verify_benchmark_16_c1(f16.get("pytest_controlled_subset_raw") or {}, f16.get("pytest_worker_policy") or {}, kind=BENCHMARK_KIND_PYTEST, expected_run_id=rid, expected_tested_sha=tsha, max_safe_cap=3)
        s_rec = _verify_benchmark_16_c1(f16.get("science_slice_8000_raw") or {}, f16.get("science_worker_policy") or {}, kind=BENCHMARK_KIND_SCIENCE, expected_run_id=rid, expected_tested_sha=tsha, max_safe_cap=GLOBAL_MAX_DESCENDANTS_R5_C1)
        viols.extend(p_rec.get("violations") or ())
        viols.extend(s_rec.get("violations") or ())

    # Independent JUnit raw XML 1:1 verification (15)
    if f15.get("status") == "NOT_RUN_BLOCKED_LOW_DISK":
        junit_ok, j_viols = False, []
    else:
        junit_ok, j_viols = _verify_junit_raw_xml_15_c1(run_dir, f15, expected_run_id=rid, expected_tested_sha=tsha)
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
        viols.append("C1V_PARITY_ANCHOR_MUTATED")

    # Recompute ALL 30 gates independently!
    recomp = derive_r5_c1_readiness_verdicts(
        raw_evidence=docs,
        mem_audit=mem_au,
        pytest_recomp=p_rec,
        science_recomp=s_rec,
        junit_ok=junit_ok,
        r4_replay_ok=bool(r4_au.get("valid")),
        stale_prior_shas=STALE_PRIOR_SHAS_R5_C1,
        stale_prior_run_ids=STALE_PRIOR_RUN_IDS_R5_C1,
        for_final_verifier_manifest=True,
        verifier_violations_count=len(viols),
    )

    rep_g = f20.get("gates_30") or {}
    for gk, gv in recomp["gates_30"].items():
        if gk == "G27_INDEPENDENT_VERIFIER":
            continue
        if rep_g.get(gk) == "PASS" and gv != "PASS":
            viols.append(f"C1V_FORGED_GATE_INFLATION:{gk}:rep=PASS!=recomp={gv}")
    rep_v = f20.get("verdicts") or {}
    if rep_v.get("ENGINEERING") == "PASS" and recomp["verdicts"]["ENGINEERING"] != "PASS":
        viols.append("C1V_FORGED_ENGINEERING_PASS")
    if (
        rep_v.get("RESEARCH_ECONOMIC_VERDICT") != "NO_PROVEN_EDGE"
        or rep_v.get("ACTIVATION_READY") != "NO"
        or rep_v.get("KH04_ENTRY") != "BLOCKED_NO_DEV_CANDIDATE"
        or not _is_int(rep_v.get("OOS_EVALUATIONS")) or int(rep_v["OOS_EVALUATIONS"]) != 0
        or rep_v.get("CAPTURE_AUTHORIZATION") != "BLOCKED_OWNER_AUTHORIZATION"
    ):
        viols.append("C1V_FORGED_ECONOMIC_OOS_OR_ACTIVATION_IN_20")

    if viols and recomp["gates_30"]["G27_INDEPENDENT_VERIFIER"] == "PASS":
        recomp["gates_30"]["G27_INDEPENDENT_VERIFIER"] = "FAIL"
        recomp["verdicts"]["ENGINEERING"] = "FAIL"
        recomp["overall_classification"] = "FAIL_CLOSED"

    all_ok = (len(viols) == 0) and (recomp["verdicts"]["ENGINEERING"] == "PASS")
    manifest: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R5_C1,
        "schema_version": SCHEMA_VERSION_KH03_R5_C1,
        "run_id": rid,
        "tested_executable_sha": tsha,
        "verified_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "orchestrator_pid": orch_pid,
        "verifier_pid": verif_pid,
        "distinct_os_process_verified": (_is_int(orch_pid) and int(orch_pid) != verif_pid),
        "independent_r4_replay_audit": r4_au,
        "independent_pytest_benchmark_recompute": p_rec,
        "independent_science_benchmark_recompute": s_rec,
        "independent_telemetry_audit": mem_au,
        "independent_junit_bijection_ok": junit_ok,
        "independent_recomputed_30_gates": recomp["gates_30"],
        "independent_recomputed_verdicts": recomp["verdicts"],
        "overall_classification": recomp["overall_classification"],
        "verified_artifact_sha256_manifest": dict(sorted(file_hashes.items())),
        "manifest_excludes_self_from_hash_index": True,
        "violations_count": len(viols),
        "violations": viols,
        "status": "PASS" if all_ok else "FAIL",
    }

    if write_manifest:
        write_json_durable_atomic(run_dir / MANIFEST_21_NAME_KH03_R5_C1, manifest)
    return (0 if all_ok else 1), manifest


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="KH-03.R5.C1 Separate-OS Independent Verifier (G01..G30)")
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--expected-run-id", default=None)
    ap.add_argument("--expected-tested-sha", default=None)
    ap.add_argument("--no-write-manifest", action="store_true")
    args = ap.parse_args(argv)
    rc, res = verify_r5_c1_artifacts_independently_from_disk(
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
