#!/usr/bin/env python3
"""
aurum-desk/tools/kh_03_r5_independent_verifier.py

AURUM DESK — KH-03.R5 | SEPARATE-OS INDEPENDENT DISK VERIFIER (G01..G26)
SPEC VERSION: KH-03.R5-v1.0 | ZERO ORCHESTRATOR IMPORT | INDEPENDENT RAW RECOMPUTATION

Runs in a distinct OS process (`verifier_pid != orchestrator_pid`), reads ALL raw R5 artifacts
(`00`..`20`, `22`, `README.md`, `HANDOVER_KH03_R5_VI.txt`, `logs/r4_replay_forensic_ledger_1x.jsonl.gz`,
`logs/runtime_memory_samples_0p5s.jsonl.gz`, `logs/pytest_collected_executed_nodeids.jsonl.gz`,
`junit/*.xml`) directly from disk using strict non-finite-rejecting JSON parsing, independently
recomputes exact Decimal R4 replay parity, JUnit 1:1 XML bijection, W1/W2/W4/W1_repeat raw timings
and worker policy, 0.5s resource telemetry, authorization/provenance/custody/fidelity truth,
prior R1..R4 + dataset SHA immutability, and ALL 26 orthogonal gates (`G01..G26`), then seals
`21_independent_OS_verifier_manifest_sha256.json` (excluding itself from its own SHA index).
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
from services.kh_03_r5_market_data_foundation_service import (
    SCHEMA_VERSION_KH03_R5,
    SPEC_VERSION_KH03_R5,
    STRICT_PREFLIGHT_DISK_MIN_GIB_R5,
    STRICT_RUNTIME_DISK_MIN_GIB_R5,
)
from services.kh_03_r5_readiness_gate_service import (
    GATE_26_KEYS_KH03_R5,
    GLOBAL_MAX_DESCENDANTS_R5,
    HARD_RAM_FLOOR_MIB_R5,
    MANDATORY_BRANCH_KH03_R5,
    MIN_EXPECTED_PYTEST_COLLECTED_R5,
    derive_r5_readiness_verdicts,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_03_r3_c1_independent_verifier import (
    PRIOR_KH03_R3_EVIDENCE_SHA,
    PRIOR_KH03_R3_PUB_HEAD_SHA,
    PRIOR_KH03_R3_RUN_ID,
    PRIOR_KH03_R3_TESTED_SHA,
    SEALED_BASELINE_PARITY_FINGERPRINTS,
)
from tools.kh_03_r3_c2_independent_verifier import (
    PRIOR_KH03_R3_C1_EVIDENCE_SHA,
    PRIOR_KH03_R3_C1_PUB_HEAD_SHA,
    PRIOR_KH03_R3_C1_RUN_ID,
    PRIOR_KH03_R3_C1_TESTED_SHA,
)
from tools.kh_03_r4_independent_verifier import (
    PRIOR_KH03_R3_C2_EVIDENCE_SHA,
    PRIOR_KH03_R3_C2_PUB_HEAD_SHA,
    PRIOR_KH03_R3_C2_RUN_ID,
    PRIOR_KH03_R3_C2_TESTED_SHA,
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R4,
)

MANIFEST_21_NAME_KH03_R5 = "21_independent_OS_verifier_manifest_sha256.json"
Q4 = Decimal("0.0001")

# Verified KH-03.R4 sealed references at HEAD 42b0573ed18f0678847c6deb25db395aad7f492e
PRIOR_KH03_R4_TESTED_SHA = "c42a14321224d3a143cb56ac15df5f5f228dbac6"
PRIOR_KH03_R4_EVIDENCE_SHA = "b67ff35099bde078c98fe68128d90701dd1d3fab"
PRIOR_KH03_R4_PUB_HEAD_SHA = "42b0573ed18f0678847c6deb25db395aad7f492e"
PRIOR_KH03_R4_RUN_ID = "20261123T162838Z_c42a14321224"

PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R5: Tuple[Tuple[str, str, str], ...] = (
    *PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R4,
    (
        "aurum-desk/docs/artifacts/kh_03_r4_economic_feasibility/latest_run.json",
        "d1f63639735638dc3bee9c47e14d3ac29c5778cfdc330a097ed2440f16173d2d",
        "KH03_R4_LATEST_RUN_POINTER",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r4_economic_feasibility/22_phase2_evidence_publish_remote_readback_{PRIOR_KH03_R4_RUN_ID}.json",
        "8b1af6e8d162bdebd1f471bd52abc4de4f5265ba658a0515e9c7b5d63d88cdc4",
        "KH03_R4_SEALED_POSTPUSH_READBACK_22",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r4_economic_feasibility/{PRIOR_KH03_R4_RUN_ID}/21_independent_OS_verifier_manifest_sha256.json",
        "67893977482c2e9028536683ebaac8267769197b5b160d238d4e9e2acf4460b1",
        "KH03_R4_SEALED_MANIFEST_21",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r4_economic_feasibility/{PRIOR_KH03_R4_RUN_ID}/04_closed_trade_decimal_gross_fee_net_bridge.json",
        "57ce82a79570301680bcf2e165a8afacc3a436dcda8e80ce7db87d4f480573cf",
        "KH03_R4_SEALED_DECIMAL_BRIDGE_04",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r4_economic_feasibility/{PRIOR_KH03_R4_RUN_ID}/11_data_readiness_register_and_missing_evidence.json",
        "9a104584362daf4acf575a7732edaea8f91470ad45293cf5e57b5210cf2f7e88",
        "KH03_R4_SEALED_DATA_READINESS_11",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r4_economic_feasibility/{PRIOR_KH03_R4_RUN_ID}/14_A1_SI8_adapter_D4_production_baseline_parity.json",
        "8dd93391fed7f9974ecdcd3d62b6bf38884b2e3b15a797bb8f435dd64ba66070",
        "KH03_R4_SEALED_PARITY_14",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r4_economic_feasibility/{PRIOR_KH03_R4_RUN_ID}/19_orthogonal_30_gates_and_verdict_matrix.json",
        "72b048a10b75f391ce3c6537609bf736d97f1ef0f67db2a092546ebe86858b93",
        "KH03_R4_SEALED_30_GATES_19",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r4_economic_feasibility/{PRIOR_KH03_R4_RUN_ID}/logs/forensic_closed_open_gap_ledger_1x.jsonl.gz",
        "11b15d2ce6385ced0ce8a3c18ff053a59a204a2da0cde77cd52b8c43f965c344",
        "KH03_R4_SEALED_FORENSIC_LEDGER_GZ",
    ),
)

KH03_R5_SOURCE_FILES_ALLOWLIST: Tuple[str, ...] = (
    "aurum-desk/backend/services/kh_03_r5_market_data_foundation_service.py",
    "aurum-desk/backend/services/kh_03_r5_capture_and_custody_service.py",
    "aurum-desk/backend/services/kh_03_r5_readiness_gate_service.py",
    "aurum-desk/tools/kh_03_r5_independent_verifier.py",
    "aurum-desk/tools/run_kh_03_r5_verification.py",
    "aurum-desk/backend/tests/test_kh03_r5_market_data_contracts.py",
    "aurum-desk/backend/tests/test_kh03_r5_capture_provenance.py",
    "aurum-desk/backend/tests/test_kh03_r5_custody_and_partitions.py",
    "aurum-desk/backend/tests/test_kh03_r5_resource_and_async.py",
    "aurum-desk/backend/tests/test_kh03_r5_readiness_and_verifier.py",
)

REQUIRED_ARTIFACTS_KH03_R5: Tuple[str, ...] = (
    "00_branch_sha_clean_preflight_resource.json",
    "01_function_ownership_keep_as_is_manifest.json",
    "02_prior_run_dataset_escrow_pointer_integrity.json",
    "03_source_capability_contract_inventory.json",
    "04_operator_scope_and_capture_plan.json",
    "05_capture_execution_and_provenance_receipts.json",
    "06_contract_metadata_validation.json",
    "07_ohlcv_lineage_gap_coverage.json",
    "08_bbo_sample_cadence_quality_coverage.json",
    "09_funding_settlement_completeness.json",
    "10_owner_fee_and_slippage_readiness.json",
    "11_dev_custody_partition_chain_and_no_oos_access.json",
    "12_observed_execution_fidelity_register.json",
    "13_r4_replay_parity_economic_bridge.json",
    "14_A1_SI8_adapter_D4_full_baseline_parity.json",
    "15_full_pytest_fresh_collection_junit_bijection.json",
    "16_W1_W2_W4_W1repeat_benchmark_worker_selection.json",
    "17_disk_ram_pid_samples_cleanup.json",
    "18_inherited_new_red_green_fault_receipts.json",
    "19_independent_verifier_mutation_attack_results.json",
    "20_orthogonal_gates_and_verdicts.json",
    "22_phase1_phase2_remote_publication_receipts.json",
    "README.md",
    "HANDOVER_KH03_R5_VI.txt",
)

STALE_PRIOR_SHAS_R5: Tuple[str, ...] = (
    PRIOR_KH03_R3_TESTED_SHA, PRIOR_KH03_R3_EVIDENCE_SHA, PRIOR_KH03_R3_PUB_HEAD_SHA,
    PRIOR_KH03_R3_C1_TESTED_SHA, PRIOR_KH03_R3_C1_EVIDENCE_SHA, PRIOR_KH03_R3_C1_PUB_HEAD_SHA,
    PRIOR_KH03_R3_C2_TESTED_SHA, PRIOR_KH03_R3_C2_EVIDENCE_SHA, PRIOR_KH03_R3_C2_PUB_HEAD_SHA,
    PRIOR_KH03_R4_TESTED_SHA, PRIOR_KH03_R4_EVIDENCE_SHA, PRIOR_KH03_R4_PUB_HEAD_SHA,
)
STALE_PRIOR_RUN_IDS_R5: Tuple[str, ...] = (
    PRIOR_KH03_R3_RUN_ID, PRIOR_KH03_R3_C1_RUN_ID, PRIOR_KH03_R3_C2_RUN_ID, PRIOR_KH03_R4_RUN_ID,
)


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _strict_loads(text: str) -> Any:
    def _rej(tok: str) -> Any:
        raise ValueError(f"NONSTANDARD_JSON_CONSTANT:{tok}")

    return json.loads(text, parse_constant=_rej)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_hex64(v: Any) -> bool:
    return isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdefABCDEF" for c in v)


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


def _audit_r4_replay_ledger_13_r5(run_dir: Path, f13: Mapping[str, Any]) -> Dict[str, Any]:
    """Independently streams `logs/r4_replay_forensic_ledger_1x.jsonl.gz` and reconciles vs `13`."""
    viols: List[str] = []
    rel_gz = str(f13.get("forensic_ledger_rel_path") or "logs/r4_replay_forensic_ledger_1x.jsonl.gz")
    gz_p = run_dir / rel_gz
    if not gz_p.is_file() or gz_p.stat().st_size == 0:
        return {"valid": False, "status": "FAIL", "violations": ["R5V_MISSING_R4_REPLAY_LEDGER_GZ"]}
    if _sha256_file(gz_p) != f13.get("forensic_ledger_file_sha256"):
        viols.append("R5V_R4_REPLAY_LEDGER_SHA_MISMATCH")

    seen: set[str] = set()
    n_cl = n_w = n_l = n_op = n_gp = over250 = 0
    g_sum = f_sum = n_sum = op_f_sum = Decimal("0")
    try:
        with gzip.open(gz_p, "rt", encoding="utf-8") as fh:
            for ln_no, raw in enumerate(fh, 1):
                s = raw.strip()
                if not s:
                    continue
                r = _strict_loads(s)
                oid = str(r.get("opportunity_id") or "")
                if not oid or oid in seen:
                    viols.append(f"R5V_R4_LEDGER_DUP_ID:line={ln_no}")
                seen.add(oid)
                if not bool(r.get("filled")):
                    continue
                lbl = str(r.get("outcome_label") or "")
                if lbl in ("WIN", "LOSS"):
                    n_cl += 1
                    g, tf, ne = _dec(r["realized_gross_pnl_usd"]), _dec(r["total_fees_usd"]), _dec(r["realized_net_pnl_usd"])
                    if abs((g - tf) - ne) > Decimal("0.001"):
                        viols.append(f"R5V_R4_LEDGER_RESIDUAL_BREACH:line={ln_no}")
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
        return {"valid": False, "status": "FAIL", "violations": [f"R5V_R4_LEDGER_PARSE_ERR:{exc}"]}

    if (n_cl, n_w, n_l, n_op, n_gp) != (203, 7, 196, 28, 2):
        viols.append(f"R5V_R4_ANCHOR_COUNT_DRIFT:{(n_cl, n_w, n_l, n_op, n_gp)}")
    if abs(g_sum - Decimal("-164.9541")) > Decimal("0.01") or abs(f_sum - Decimal("276.6577")) > Decimal("0.01") or abs(n_sum - Decimal("-441.6118")) > Decimal("0.01"):
        viols.append("R5V_R4_DECIMAL_BRIDGE_DRIFT")
    if abs(op_f_sum - Decimal("14.7151")) > Decimal("0.01"):
        viols.append("R5V_R4_OPEN_FEE_DRIFT")
    if (
        int(f13.get("null_1x_closed", -1)) != n_cl
        or not _is_num(f13.get("null_1x_net_usd"))
        or abs(_dec(f13["null_1x_net_usd"]) - n_sum) > Decimal("0.002")
        or int(f13.get("realized_losses_gt_2p50_count", -1)) != over250
    ):
        viols.append("R5V_13_TAMPERED_VS_RAW_LEDGER")
    if f13.get("planned_cap_is_physical_guarantee") is not False:
        viols.append("R5V_PLANNED_CAP_GUARANTEE_FORGED")
    if f13.get("RESEARCH_ECONOMIC_VERDICT") != "NO_PROVEN_EDGE" or f13.get("KH04_ENTRY") != "BLOCKED_NO_DEV_CANDIDATE" or int(f13.get("OOS_EVALUATIONS", -1)) != 0:
        viols.append("R5V_FORGED_ECONOMIC_OR_OOS_IN_13")

    return {"valid": len(viols) == 0, "status": "PASS" if not viols else "FAIL", "recomputed_net_usd": _q4(n_sum), "violations": viols}


def _recompute_raw_bench_r5(
    raw_doc: Mapping[str, Any],
    rep_pol: Mapping[str, Any],
    *,
    kind: str,
    expected_run_id: str,
    expected_tested_sha: str,
    max_safe_cap: int,
) -> Dict[str, Any]:
    viols: List[str] = []
    if kind == "pytest_controlled_subset":
        dk, cross, cnt_k, sha_k = "wall_clock_seconds", ("wall_seconds", "canonical_sorted_sha256", "rows_count"), "executed_tests_count", "canonical_sorted_outcome_sha256"
    elif kind == "r2_science_slice":
        dk, cross, cnt_k, sha_k = "wall_seconds", ("wall_clock_seconds", "canonical_sorted_outcome_sha256", "executed_tests_count"), "rows_count", "canonical_sorted_sha256"
    else:
        return {"valid": False, "violations": [f"R5V_UNKNOWN_BENCH_KIND:{kind}"]}

    if raw_doc.get("run_id") != expected_run_id or raw_doc.get("tested_executable_sha") != expected_tested_sha:
        viols.append(f"R5V_BENCH_PROVENANCE_MISMATCH:{kind}")
    runs = raw_doc.get("runs")
    if not isinstance(runs, list) or len(runs) != 4:
        return {"valid": False, "violations": viols + [f"R5V_BENCH_BAD_RUNS_LEN:{kind}"]}

    req_w = {"w=1": 1, "w=2": 2, "w=4": 4, "w=1_repeat": 1}
    durs: Dict[str, float] = {}
    cnts: Dict[str, int] = {}
    shas: Dict[str, str] = {}
    for r in runs:
        if not isinstance(r, Mapping):
            viols.append(f"R5V_BENCH_ROW_NOT_OBJ:{kind}")
            continue
        for ck in cross:
            if ck in r:
                viols.append(f"R5V_BENCH_MIXED_KEY:{kind}:{ck}")
        ml = r.get("mode_label")
        if ml not in req_w or ml in durs:
            viols.append(f"R5V_BENCH_BAD_MODE:{kind}:{ml}")
            continue
        if not _is_int(r.get("workers")) or int(r["workers"]) != req_w[ml]:
            viols.append(f"R5V_BENCH_BAD_WORKERS:{kind}:{ml}")
        dv = r.get(dk)
        if not _is_num(dv) or not math.isfinite(float(dv)) or float(dv) <= 0.0 or float(dv) > 86_400.0:
            viols.append(f"R5V_BENCH_BAD_DUR:{kind}:{ml}")
        else:
            durs[ml] = float(dv)
        cv = r.get(cnt_k)
        if not _is_int(cv) or int(cv) <= 0:
            viols.append(f"R5V_BENCH_BAD_CNT:{kind}:{ml}")
        else:
            cnts[ml] = int(cv)
        sv = r.get(sha_k)
        if not _is_hex64(sv):
            viols.append(f"R5V_BENCH_BAD_SHA:{kind}:{ml}")
        else:
            shas[ml] = str(sv).lower()

    if len(durs) != 4:
        return {"valid": False, "violations": viols}
    if len(cnts) != 4 or len(set(cnts.values())) != 1:
        viols.append(f"R5V_BENCH_COUNT_DRIFT:{kind}")
    if len(shas) != 4 or len(set(shas.values())) != 1:
        viols.append(f"R5V_BENCH_SEMANTIC_SHA_DRIFT:{kind}")

    means = {m: round(durs[m], 4) for m in ("w=1", "w=2", "w=4")}
    fastest = min(("w=1", "w=2", "w=4"), key=lambda m: (round(means[m], 6), req_w[m]))
    sp2 = round(durs["w=1"] / max(1e-9, durs["w=2"]), 3)
    sp4 = round(durs["w=1"] / max(1e-9, durs["w=4"]), 3)

    if not isinstance(rep_pol, Mapping) or rep_pol.get("status") != "PASS" or rep_pol.get("can_certify") is not True:
        viols.append(f"R5V_BENCH_POLICY_NOT_PASS:{kind}")
    else:
        if rep_pol.get("measured_fastest_mode") != fastest:
            viols.append(f"R5V_BENCH_FASTEST_MISMATCH:{kind}")
        if not _is_num(rep_pol.get("measured_speedup_w2_vs_w1")) or abs(float(rep_pol["measured_speedup_w2_vs_w1"]) - sp2) > 0.01:
            viols.append(f"R5V_BENCH_SPEEDUP_TAMPERED:w2:{kind}")
        if not _is_num(rep_pol.get("measured_speedup_w4_vs_w1")) or abs(float(rep_pol["measured_speedup_w4_vs_w1"]) - sp4) > 0.01:
            viols.append(f"R5V_BENCH_SPEEDUP_TAMPERED:w4:{kind}")
        aw, sc = rep_pol.get("applied_worker_count"), rep_pol.get("safe_worker_cap")
        if not _is_int(aw) or not _is_int(sc) or int(aw) < 1 or int(aw) > max_safe_cap or int(sc) > max_safe_cap or int(aw) > int(sc):
            viols.append(f"R5V_BENCH_SAFE_CAP_EXCEEDED:{kind}")
        if rep_pol.get("full_suite_speedup_extrapolated") is not False or rep_pol.get("timing_determinism_claimed") is not False:
            viols.append(f"R5V_BENCH_EXTRAPOLATION_FORBIDDEN:{kind}")

    return {"valid": len(viols) == 0, "means": means, "fastest": fastest, "sp2": sp2, "sp4": sp4, "violations": viols}


def _verify_junit_raw_xml_r5(
    run_dir: Path, f15: Mapping[str, Any], *, expected_run_id: str, expected_tested_sha: str
) -> Tuple[bool, List[str]]:
    viols: List[str] = []
    nodes_rel = str(f15.get("collected_nodeids_gz_rel") or "")
    nodes_p = (run_dir / nodes_rel) if nodes_rel else None
    c_ids: List[str] = []
    if nodes_p is not None and nodes_p.is_file():
        if _sha256_file(nodes_p) != f15.get("collected_nodeids_gz_sha256"):
            viols.append("R5V_JUNIT_NODEIDS_GZ_SHA_MISMATCH")
        with gzip.open(nodes_p, "rt", encoding="utf-8") as fh:
            for ln in fh:
                s = ln.strip()
                if s:
                    c_ids.append(s)
    else:
        cm = f15.get("collection_manifest") or {}
        if cm.get("run_id") != expected_run_id or cm.get("tested_executable_sha") != expected_tested_sha:
            viols.append("R5V_JUNIT_CM_PROVENANCE_MISMATCH")
        c_ids = list(cm.get("collected_node_ids") or ())

    sm = f15.get("shard_manifest") or {}
    if not sm:
        return False, ["R5V_JUNIT_MISSING_SHARD_MANIFEST"]
    if sm.get("run_id") != expected_run_id or sm.get("tested_executable_sha") != expected_tested_sha:
        viols.append("R5V_JUNIT_SM_PROVENANCE_MISMATCH")
    if int(sm.get("nonzero_exit_shards_count", 1)) != 0:
        viols.append("R5V_JUNIT_NONZERO_SHARDS")

    if len(c_ids) < MIN_EXPECTED_PYTEST_COLLECTED_R5:
        viols.append(f"R5V_JUNIT_COLLECTED_BELOW_R4:{len(c_ids)}<{MIN_EXPECTED_PYTEST_COLLECTED_R5}")
    if len(set(c_ids)) != len(c_ids):
        viols.append("R5V_JUNIT_COLLECTED_DUPLICATES")

    shards = list(sm.get("shards") or ())
    if not shards:
        return False, ["R5V_JUNIT_ZERO_SHARDS"]

    sched_all: List[str] = []
    tot_t = tot_f = tot_e = tot_s = mand_s = 0
    for sh in shards:
        if int(sh.get("exit_code", 1)) != 0 or bool(sh.get("timed_out", True)) or bool(sh.get("reused", True)):
            viols.append(f"R5V_JUNIT_BAD_SHARD:{sh.get('shard_id')}")
        rel_x = str(sh.get("junit_xml_path") or "")
        xp = run_dir / rel_x
        if not xp.is_file() or xp.stat().st_size == 0:
            viols.append(f"R5V_JUNIT_XML_MISSING:{rel_x}")
            continue
        if _sha256_file(xp) != str(sh.get("xml_sha256") or sh.get("junit_sha256") or ""):
            viols.append(f"R5V_JUNIT_XML_SHA_MISMATCH:{rel_x}")
        sched = list(sh.get("scheduled_node_ids") or ())
        sched_all.extend(sched)
        try:
            root = ET.fromstring(xp.read_text("utf-8"))
        except Exception as exc:
            viols.append(f"R5V_JUNIT_XML_PARSE_ERR:{rel_x}:{exc}")
            continue
        tcs = list(root.iter("testcase"))
        tot_t += len(tcs)
        if len(tcs) != len(sched):
            viols.append(f"R5V_JUNIT_SHARD_LEN_MISMATCH:{sh.get('shard_id')}")
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
        viols.append("R5V_JUNIT_BIJECTION_MISMATCH")
    tot_p = tot_t - tot_f - tot_e - tot_s
    if (
        tot_t != int(f15.get("executed_count", -1))
        or tot_p != int(f15.get("passed_count", -1))
        or tot_s != int(f15.get("skipped_count", -1))
        or tot_f != int(f15.get("failed_count", -1))
        or tot_e != int(f15.get("error_count", -1))
    ):
        viols.append("R5V_JUNIT_COUNTS_TAMPERED_VS_RAW_XML")
    if tot_f != 0 or tot_e != 0 or mand_s != 0:
        viols.append(f"R5V_JUNIT_FAIL_ERR_OR_MANDATORY_SKIP:f={tot_f},e={tot_e},ms={mand_s}")
    return len(viols) == 0, viols


def _audit_telemetry_gz_vs_17_r5(mem_gz: Path, f17: Mapping[str, Any]) -> Dict[str, Any]:
    viols: List[str] = []
    if not mem_gz.is_file() or mem_gz.stat().st_size == 0:
        return {"status": "FAIL_MISSING_LOG", "samples_count": 0, "below_floor_samples_count": 1, "max_workers_observed_in_samples": 99, "violations": ["R5V_MISSING_TELEMETRY_LOG"]}
    n, min_ram, min_dk, max_ch, below_r, below_d = 0, float("inf"), float("inf"), 0, 0, 0
    try:
        with gzip.open(mem_gz, "rt", encoding="utf-8") as fh:
            for ln in fh:
                s = ln.strip()
                if not s:
                    continue
                row = _strict_loads(s)
                n += 1
                rm = row.get("available_mib")
                if rm is None and row.get("available_ram_bytes") is not None:
                    rm = float(row["available_ram_bytes"]) / (1024.0 * 1024.0)
                if _is_num(rm):
                    rf = float(rm)
                    min_ram = min(min_ram, rf)
                    if rf < HARD_RAM_FLOOR_MIB_R5:
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
        return {"status": "FAIL_PARSE", "samples_count": n, "below_floor_samples_count": 1, "max_workers_observed_in_samples": 99, "violations": [f"R5V_TELEMETRY_PARSE_ERR:{exc}"]}

    if n == 0 or min_ram == float("inf") or min_dk == float("inf"):
        viols.append("R5V_TELEMETRY_EMPTY_OR_INCOMPLETE")
    if below_r > 0 or min_ram < HARD_RAM_FLOOR_MIB_R5:
        viols.append(f"R5V_TELEMETRY_RAM_BELOW_512MIB:{min_ram:.2f}")
    if below_d > 0 or min_dk < STRICT_RUNTIME_DISK_MIN_GIB_R5:
        viols.append(f"R5V_TELEMETRY_DISK_BELOW_20GIB:{min_dk:.4f}")
    if max_ch > GLOBAL_MAX_DESCENDANTS_R5:
        viols.append(f"R5V_TELEMETRY_CHILD_EXCEEDED_4:{max_ch}")
    rep_ram, rep_dk, rep_ch = f17.get("min_available_ram_mib"), f17.get("min_disk_free_gib"), f17.get("max_descendant_children")
    if not _is_num(rep_ram) or abs(float(rep_ram) - min_ram) > 1.0:
        viols.append("R5V_TELEMETRY_17_RAM_MUTATED_VS_GZ")
    if not _is_num(rep_dk) or abs(float(rep_dk) - min_dk) > 0.05:
        viols.append("R5V_TELEMETRY_17_DISK_MUTATED_VS_GZ")
    if not _is_int(rep_ch) or int(rep_ch) < max_ch or int(rep_ch) > GLOBAL_MAX_DESCENDANTS_R5:
        viols.append("R5V_TELEMETRY_17_CHILDREN_MUTATED_VS_GZ")
    return {
        "status": "PASS" if len(viols) == 0 else "FAIL",
        "samples_count": n,
        "min_available_ram_mib": round(min_ram if min_ram != float("inf") else 0.0, 2),
        "min_disk_free_gib": round(min_dk if min_dk != float("inf") else 0.0, 4),
        "max_workers_observed_in_samples": max_ch,
        "below_floor_samples_count": below_r + below_d,
        "violations": viols,
    }


def recompute_r5_gate_matrix(
    *,
    docs: Mapping[str, Mapping[str, Any]],
    mem_audit: Mapping[str, Any],
    pytest_recomp: Mapping[str, Any],
    science_recomp: Mapping[str, Any],
    junit_ok: bool,
    r4_replay_ok: bool,
    verifier_violations_count: int = 0,
    for_final_verifier_manifest: bool = False,
) -> Dict[str, Any]:
    """Recomputes ALL 26 orthogonal R5 gates & 19 independent verdict fields from raw inputs."""
    f15 = dict(docs["15"])
    f15["junit_bijection_exact"] = bool(junit_ok and f15.get("junit_bijection_exact"))
    f16 = dict(docs["16"])
    f16["pytest_recompute_valid"] = bool(pytest_recomp.get("valid"))
    f16["science_recompute_valid"] = bool(science_recomp.get("valid"))
    f17 = {
        **dict(docs["17"]),
        "raw_telemetry_audit_status": mem_audit.get("status"),
        "below_floor_samples_count": mem_audit.get("below_floor_samples_count", 1),
    }
    f13 = dict(docs["13"])
    if not r4_replay_ok:
        f13["status"] = "FAIL"
    mod_docs = {**docs, "13": f13, "15": f15, "16": f16}
    return derive_r5_readiness_verdicts(
        raw_evidence=mod_docs,
        resource=f17,
        regression=f15,
        parity=docs["14"],
        custody=docs["11"].get("custody_chain_verification") or {},
        observed_register=docs["12"],
        approval_state=docs["04"].get("operator_approval_state") or {},
        stale_prior_shas=STALE_PRIOR_SHAS_R5,
        stale_prior_run_ids=STALE_PRIOR_RUN_IDS_R5,
        for_final_verifier_manifest=for_final_verifier_manifest,
        verifier_violations_count=verifier_violations_count,
    )


def verify_r5_artifacts_independently_from_disk(
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

    for rel in REQUIRED_ARTIFACTS_KH03_R5:
        p = run_dir / rel
        if not p.is_file() or p.stat().st_size == 0:
            viols.append(f"R5V_MISSING_OR_EMPTY_ARTIFACT:{rel}")
            continue
        file_hashes[rel] = {"sha256": _sha256_file(p), "bytes": p.stat().st_size}
        if rel.endswith(".json"):
            try:
                docs[rel[:2]] = _strict_loads(p.read_text("utf-8"))
            except Exception as exc:
                viols.append(f"R5V_INVALID_JSON:{rel}:{exc}")

    for dk in ("00", "01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12", "13", "14", "15", "16", "17", "18", "19", "20", "22"):
        if dk not in docs:
            viols.append(f"R5V_MISSING_JSON_DOC:{dk}")

    if viols:
        return 1, {"spec_version": SPEC_VERSION_KH03_R5, "verifier_pid": verif_pid, "status": "FAIL_MISSING_OR_INVALID_ARTIFACTS", "violations": viols}

    f00, f02, f04, f05, f10, f11, f12, f13, f15, f16, f17, f20, f22 = (
        docs["00"], docs["02"], docs["04"], docs["05"], docs["10"], docs["11"],
        docs["12"], docs["13"], docs["15"], docs["16"], docs["17"], docs["20"], docs["22"],
    )
    rid = expected_run_id or str(f00.get("run_id") or "")
    tsha = expected_tested_sha or str(f00.get("tested_executable_sha") or "")

    if rid in STALE_PRIOR_RUN_IDS_R5 or tsha in STALE_PRIOR_SHAS_R5:
        viols.append("R5V_STALE_PRIOR_RUN_OR_SHA_REUSED")
    orch_pid = f00.get("orchestrator_pid")
    if _is_int(orch_pid) and int(orch_pid) == verif_pid:
        viols.append("R5V_SAME_PID_AS_ORCHESTRATOR")

    for dk, doc in sorted(docs.items()):
        if doc.get("run_id") != rid:
            viols.append(f"R5V_RUN_ID_MISMATCH:{dk}")
        if doc.get("tested_executable_sha") != tsha:
            viols.append(f"R5V_TESTED_SHA_MISMATCH:{dk}")
        if doc.get("run_id") in STALE_PRIOR_RUN_IDS_R5 or doc.get("tested_executable_sha") in STALE_PRIOR_SHAS_R5:
            viols.append(f"R5V_STALE_PRIOR_RUN_OR_SHA_REUSED:{dk}")

    # Dataset SHA & prior immutability
    if f02.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH02 or f02.get("canonical_dataset_sha256_match") is not True or f02.get("all_prior_sealed_artifacts_intact") is not True:
        viols.append("R5V_DATASET_OR_PRIOR_SHA_MISMATCH")

    # Protected source/AST check
    if int(docs["01"].get("locked_files_modified_count", 1)) != 0 or int(docs["01"].get("unauthorized_changed_files_count", 1)) != 0 or len(docs["01"].get("forbidden_order_import_hits") or []) != 0:
        viols.append("R5V_PROTECTED_SOURCE_MODIFIED_OR_ORDER_IMPORT")

    # Authorization vs capture/readiness truth (forbids fake observed data or fake readiness PASS when unauthorized!)
    ap_st = f04.get("operator_approval_state") or {}
    live_perm = bool(ap_st.get("live_network_permitted", False))
    if not live_perm:
        if int(f05.get("external_requests_attempted", 1)) != 0 or int(f05.get("accepted_observed_records_count", 1)) != 0:
            viols.append("R5V_UNAPPROVED_LIVE_CAPTURE_OR_FAKE_OBSERVED_RECORDS")
        if f12.get("execution_fidelity_status") != "INCOMPLETE":
            viols.append("R5V_FAKE_EXECUTION_FIDELITY_READY_WITHOUT_OBSERVED_DATA")
    if f10.get("credentials_requested") is not False or f10.get("observed_slippage_status") != "MISSING":
        viols.append("R5V_FORGED_FEE_CREDENTIALS_OR_SLIPPAGE")
    if int(((f11.get("custody_chain_verification") or {}).get("oos_access_guard_summary") or {}).get("oos_reads_executed", 1)) != 0:
        viols.append("R5V_FORBIDDEN_OOS_READ_EXECUTED")

    # False publication / CI claim
    if (bool(f22.get("remote_readback_sha256_verified")) and not f22.get("publication_head_sha")) or (
        f22.get("ci_status") == "CI_PASS" and not bool(f22.get("actual_github_actions_run_verified"))
    ):
        viols.append("R5V_FALSE_PUBLICATION_OR_CI_CLAIM")

    # Independent R4 replay ledger Decimal verification (13)
    r4_au = _audit_r4_replay_ledger_13_r5(run_dir, f13)
    viols.extend(r4_au.get("violations") or ())
    led_p = run_dir / str(f13.get("forensic_ledger_rel_path") or "logs/r4_replay_forensic_ledger_1x.jsonl.gz")
    if led_p.is_file():
        file_hashes[str(led_p.relative_to(run_dir)).replace("\\", "/")] = {"sha256": _sha256_file(led_p), "bytes": led_p.stat().st_size}

    # Independent 0.5s telemetry verification (17)
    mem_gz = run_dir / str(f17.get("memory_samples_log_rel") or "logs/runtime_memory_samples_0p5s.jsonl.gz")
    mem_au = _audit_telemetry_gz_vs_17_r5(mem_gz, f17)
    viols.extend(mem_au.get("violations") or ())
    if mem_gz.is_file():
        file_hashes[str(mem_gz.relative_to(run_dir)).replace("\\", "/")] = {"sha256": _sha256_file(mem_gz), "bytes": mem_gz.stat().st_size}

    # Independent W1/W2/W4/W1_repeat benchmark verification (16)
    if f16.get("status") == "NOT_RUN_BLOCKED_LOW_DISK":
        p_rec = {"valid": False, "violations": ["NOT_RUN_BLOCKED_LOW_DISK"]}
        s_rec = {"valid": False, "violations": ["NOT_RUN_BLOCKED_LOW_DISK"]}
    else:
        p_rec = _recompute_raw_bench_r5(f16.get("pytest_controlled_subset_raw") or {}, f16.get("pytest_worker_policy") or {}, kind="pytest_controlled_subset", expected_run_id=rid, expected_tested_sha=tsha, max_safe_cap=3)
        s_rec = _recompute_raw_bench_r5(f16.get("science_slice_8000_raw") or {}, f16.get("science_worker_policy") or {}, kind="r2_science_slice", expected_run_id=rid, expected_tested_sha=tsha, max_safe_cap=GLOBAL_MAX_DESCENDANTS_R5)
        viols.extend(p_rec.get("violations") or ())
        viols.extend(s_rec.get("violations") or ())

    # Independent JUnit raw XML 1:1 verification (15)
    if f15.get("status") == "NOT_RUN_BLOCKED_LOW_DISK":
        junit_ok, j_viols = False, []
    else:
        junit_ok, j_viols = _verify_junit_raw_xml_r5(run_dir, f15, expected_run_id=rid, expected_tested_sha=tsha)
        viols.extend(j_viols)
        if f15.get("collected_nodeids_gz_rel"):
            np = run_dir / str(f15["collected_nodeids_gz_rel"])
            if np.is_file():
                file_hashes[str(np.relative_to(run_dir)).replace("\\", "/")] = {"sha256": _sha256_file(np), "bytes": np.stat().st_size}

    # Recompute ALL 26 gates independently
    recomp = recompute_r5_gate_matrix(
        docs=docs, mem_audit=mem_au, pytest_recomp=p_rec, science_recomp=s_rec,
        junit_ok=junit_ok, r4_replay_ok=bool(r4_au.get("valid")),
        verifier_violations_count=len(viols), for_final_verifier_manifest=True,
    )

    # Detect forged gate/verdict inflation in 20
    rep_g = f20.get("gates_26") or {}
    for gk, gv in recomp["gates_26"].items():
        if gk == "G23_SEPARATE_OS_VERIFIER_RAW_EVIDENCE":
            continue
        if rep_g.get(gk) == "PASS" and gv != "PASS":
            viols.append(f"R5V_FORGED_GATE_INFLATION:{gk}:rep=PASS!=recomp={gv}")
    rep_v = f20.get("verdicts") or {}
    if rep_v.get("ENGINEERING") == "PASS" and recomp["verdicts"]["ENGINEERING"] != "PASS":
        viols.append("R5V_FORGED_ENGINEERING_PASS")
    if rep_v.get("DATA_READINESS") == "DEV_DATA_READY_FOR_SEPARATE_RESEARCH_REVIEW" and recomp["verdicts"]["DATA_READINESS"] != "DEV_DATA_READY_FOR_SEPARATE_RESEARCH_REVIEW":
        viols.append("R5V_FORGED_DATA_READINESS_PASS")
    if rep_v.get("RESEARCH_ECONOMIC_VERDICT") != "NO_PROVEN_EDGE" or rep_v.get("ACTIVATION_READY") != "NO" or rep_v.get("KH04_ENTRY") != "BLOCKED_NO_DEV_CANDIDATE" or int(rep_v.get("OOS_EVALUATIONS", -1)) != 0:
        viols.append("R5V_FORGED_ECONOMIC_KH04_OR_ACTIVATION_IN_20")

    if viols and recomp["gates_26"]["G23_SEPARATE_OS_VERIFIER_RAW_EVIDENCE"] == "PASS":
        recomp["gates_26"]["G23_SEPARATE_OS_VERIFIER_RAW_EVIDENCE"] = "FAIL"
        recomp["verdicts"]["ENGINEERING"] = "FAIL"
        recomp["overall_classification"] = "FAIL_CLOSED"

    all_ok = (len(viols) == 0) and (recomp["verdicts"]["ENGINEERING"] == "PASS")
    manifest: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R5,
        "schema_version": SCHEMA_VERSION_KH03_R5,
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
        "independent_recomputed_26_gates": recomp["gates_26"],
        "independent_recomputed_verdicts": recomp["verdicts"],
        "overall_classification": recomp["overall_classification"],
        "verified_artifact_sha256_manifest": dict(sorted(file_hashes.items())),
        "manifest_excludes_self_from_hash_index": True,
        "violations_count": len(viols),
        "violations": viols,
        "status": "PASS" if all_ok else "FAIL",
    }

    if write_manifest:
        write_json_durable_atomic(run_dir / MANIFEST_21_NAME_KH03_R5, manifest)

    return (0 if all_ok else 1), manifest


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="KH-03.R5 Separate-OS Independent Verifier (G01..G26)")
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--expected-run-id", default=None)
    ap.add_argument("--expected-tested-sha", default=None)
    ap.add_argument("--no-write-manifest", action="store_true")
    args = ap.parse_args(argv)
    rc, res = verify_r5_artifacts_independently_from_disk(
        args.run_dir,
        expected_run_id=args.expected_run_id,
        expected_tested_sha=args.expected_tested_sha,
        write_manifest=not args.no_write_manifest,
    )
    print(json.dumps({"status": res["status"], "verifier_pid": res["verifier_pid"], "violations_count": len(res.get("violations") or ()), "violations": res.get("violations") or ()}, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
