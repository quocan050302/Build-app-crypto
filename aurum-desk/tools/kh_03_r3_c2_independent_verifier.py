#!/usr/bin/env python3
"""
aurum-desk/tools/kh_03_r3_c2_independent_verifier.py

AURUM DESK — KH-03.R3.C2 | SEPARATE-OS INDEPENDENT DISK VERIFIER (G01..G26)
SPEC VERSION: KH-03.R3.C2-v1.0 | ZERO ORCHESTRATOR IMPORT | INDEPENDENT RAW RECOMPUTATION

Executes in a distinct OS process (`verifier_pid != orchestrator_pid`), reads all raw C2
artifacts (`00`..`15`, `17`, `18`, `README_KH03_R3_C2.md`, `HANDOVER_KH03_R3_C2_VI.txt`,
`junit/*.xml`, `logs/runtime_memory_samples_0p5s.jsonl.gz`) directly from disk using strict
non-finite-rejecting JSON parsing, independently recomputes:
  - Prior R1/R2/R3/C1 + canonical dataset SHA-256 immutability
  - Locked KEEP-AS-IS production/research files + C1 worker-policy service immutability
  - Pre-fix RED01..RED06 reproduction proof & strict C2T01..C2T40 boundary proof
  - Hardened R3 G22 verifier proof & archived R3 `LEGACY_DESCRIPTIVE_ONLY` separation
  - Independent raw timing/SHA/speedup/cap/noise recomputation directly from `06` & `07` vs `08`
  - Raw JUnit XML 1:1 bijection (`09`), full A1/SI8/adapter/D4 parity (`10`)
  - 6-scenario fee math & exact `NULL/E1/E2/E3` 1x historical anchors (`11`)
  - Direct 0.5s `.jsonl.gz` telemetry reconciliation (`13`)
  - `>=224` RED/GREEN fault controls (`14`) & `>=16` new C2 + inherited verifier mutations (`15`)
  - ALL 26 orthogonal release gates (`G01..G26`) & verdicts
and seals `16_independent_OS_verifier_manifest_sha256.json`.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
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
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_03_r3_c1_independent_verifier import (
    PRIOR_KH03_R3_EVIDENCE_SHA,
    PRIOR_KH03_R3_PUB_HEAD_SHA,
    PRIOR_KH03_R3_RUN_ID,
    PRIOR_KH03_R3_TESTED_SHA,
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R3_C1,
    SEALED_BASELINE_PARITY_FINGERPRINTS,
    SEALED_R3_1X_ECONOMIC_ANCHORS,
)

SPEC_VERSION_KH03_R3_C2 = "KH-03.R3.C2-v1.0"
SCHEMA_VERSION_KH03_R3_C2 = "kh_03_r3_c2_legacy_fail_closed_v1"
MANDATORY_BRANCH_KH03_R3_C2 = "feature/v17-9-2-structural-sl-tp-from-qa"
CANONICAL_DATASET_SHA256_KH03_R3_C2 = CANONICAL_DATASET_SHA256_KH02
STRICT_PREFLIGHT_DISK_MIN_GIB_C2 = 20.5
STRICT_RUNTIME_DISK_MIN_GIB_C2 = 20.0
HARD_RAM_FLOOR_MIB_C2 = 512.0
GLOBAL_MAX_DESCENDANTS_C2 = 4
MIN_EXPECTED_PYTEST_COLLECTED_C2 = 5743
MANIFEST_16_NAME_KH03_R3_C2 = "16_independent_OS_verifier_manifest_sha256.json"

PRIOR_KH03_R3_C1_TESTED_SHA = "0d81a6e4b4f2de6c0770506308f4a280ff7f186d"
PRIOR_KH03_R3_C1_EVIDENCE_SHA = "cc4cda79019c4ee8394e4e545bc7561e44efe220"
PRIOR_KH03_R3_C1_PUB_HEAD_SHA = "dfcc99c94e47afca76bc33ad1a1ca6ce3b19f10e"
PRIOR_KH03_R3_C1_RUN_ID = "20261123T123153Z_0d81a6e4b4f2"

PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R3_C2: Tuple[Tuple[str, str, str], ...] = (
    *PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R3_C1,
    (
        "aurum-desk/docs/artifacts/kh_03_r3_c1_worker_policy/latest_run.json",
        "18faf344c8a51073016367458fa66cc08ef3294b48a7680297c954975069c4d0",
        "KH03_R3_C1_LATEST_RUN_POINTER",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r3_c1_worker_policy/{PRIOR_KH03_R3_C1_RUN_ID}/18_independent_OS_verifier_manifest_sha256.json",
        "2c625eedcd3dcf636b32306b63d9534d37aa618805b12eb21f8f0ac8f5dd25f4",
        "KH03_R3_C1_SEALED_MANIFEST_18",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r3_c1_worker_policy/21_phase2_evidence_publish_remote_readback_{PRIOR_KH03_R3_C1_RUN_ID}.json",
        "f67a89efdf52eaf56aec296770b8d995e07e43572dd133ea4e4957b63ad94c16",
        "KH03_R3_C1_SEALED_POSTPUSH_READBACK_21",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r3_c1_worker_policy/{PRIOR_KH03_R3_C1_RUN_ID}/06_fresh_pytest_w1_w2_w4_w1repeat_raw_receipts.json",
        "46c464f570ff9e4365b33aa9409565c64b4ae9afbae175e091c359ee1d638330",
        "KH03_R3_C1_SEALED_PYTEST_BENCH_06",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r3_c1_worker_policy/{PRIOR_KH03_R3_C1_RUN_ID}/07_fresh_science_w1_w2_w4_w1repeat_raw_receipts.json",
        "8eb4edc404d09362ea4df6dd37ccc966cba73e1b836513379612ff0a4e5e13dc",
        "KH03_R3_C1_SEALED_SCIENCE_BENCH_07",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r3_c1_worker_policy/{PRIOR_KH03_R3_C1_RUN_ID}/08_worker_policy_measured_vs_applied_caps_and_provenance.json",
        "8f31be369c73b999c32d8a648ba156487e62edfb49ecd043d0bfa01341cc0826",
        "KH03_R3_C1_SEALED_POLICY_08",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r3_c1_worker_policy/{PRIOR_KH03_R3_C1_RUN_ID}/11_full_pytest_junit_collected_executed_bijection.json",
        "c85f5df06a7b45e4c8feaac9a923630e942ca39072a26fb02b29c286e032e08f",
        "KH03_R3_C1_SEALED_JUNIT_11",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r3_c1_worker_policy/{PRIOR_KH03_R3_C1_RUN_ID}/12_full_A1_SI8_adapter_D4_parity_and_fingerprints.json",
        "4ada96a0e09de0ec256a178810e891a2b131a5ea2da624c8f5cdc0478d131aa5",
        "KH03_R3_C1_SEALED_PARITY_12",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r3_c1_worker_policy/{PRIOR_KH03_R3_C1_RUN_ID}/13_six_scenario_backtest_and_E1_E2_E3_null_economic_regression.json",
        "79d5bf9737cf8fb04e91b6bb8e4ef738b09be6330c7d813715fdeee47b3e6de7",
        "KH03_R3_C1_SEALED_ECONOMIC_13",
    ),
)

KH03_R3_C2_SOURCE_FILES_ALLOWLIST: Tuple[str, ...] = (
    "aurum-desk/tools/run_kh_03_r3_acceptance.py",
    "aurum-desk/tools/kh_03_r3_independent_verifier.py",
    "aurum-desk/backend/tests/test_kh03_r3_resource_async_and_verifier_mutations.py",
    "aurum-desk/backend/tests/test_kh03_r3_c2_legacy_runner_fail_closed.py",
    "aurum-desk/backend/tests/test_kh03_r3_c2_independent_mutations.py",
    "aurum-desk/tools/run_kh_03_r3_c2_acceptance.py",
    "aurum-desk/tools/kh_03_r3_c2_independent_verifier.py",
)

REQUIRED_ARTIFACTS_KH03_R3_C2: Tuple[str, ...] = (
    "00_branch_remote_sha_dirty_preflight.json",
    "01_locked_code_ownership_and_ast_parity.json",
    "02_prior_r1_r2_r3_c1_artifact_sha_immutability.json",
    "03_legacy_r3_false_pass_red_proof.json",
    "04_r3_f19_policy_condition_green_boundary_suite.json",
    "05_r3_independent_g22_raw_benchmark_recompute.json",
    "06_fresh_pytest_w1_w2_w4_w1repeat.json",
    "07_fresh_science_w1_w2_w4_w1repeat.json",
    "08_measured_fastest_vs_safe_applied_worker_caps.json",
    "09_full_repo_pytest_junit_1to1.json",
    "10_full_A1_SI8_adapter_D4_parity.json",
    "11_six_cost_e1_e2_e3_null_regression.json",
    "12_legacy_and_c1_old_source_do_not_change.json",
    "13_memory_disk_pid_telemetry.json",
    "14_inherited_plus_new_red_green_fault_controls.json",
    "15_independent_verifier_mutation_test_results.json",
    "17_orthogonal_gate_matrix_and_verdicts.json",
    "18_phase1_code_commit_push_receipt.json",
    "README_KH03_R3_C2.md",
    "HANDOVER_KH03_R3_C2_VI.txt",
)

GATE_26_KEYS_KH03_R3_C2: Tuple[str, ...] = (
    "G01_BRANCH_CLEAN_UNIQUE_SHA_ORIGIN",
    "G02_DATASET_AND_R1_R2_R3_C1_SEALED_IMMUTABLE",
    "G03_PROD_ENTRY_SL_TP_FEES_ONEPOS_AST_PARITY",
    "G04_GENUINE_OLD_R3_FALSE_PASS_RED_REPRODUCED",
    "G05_STRICT_F19_CHECKS_POLICY_PASS_AND_CAN_CERTIFY",
    "G06_RAW_PYTEST_4MODE_VALIDATES_INDEPENDENTLY",
    "G07_RAW_SCIENCE_4MODE_VALIDATES_INDEPENDENTLY",
    "G08_NO_NONFINITE_MIXED_MISSING_FORGED_SCHEMA",
    "G09_MEASURED_WINNER_AND_SAFE_APPLIED_CAP_SEPARATED",
    "G10_SEMANTIC_DETERMINISTIC_W1_W2_W4_W1REPEAT",
    "G11_TIMING_NOISE_AND_SCOPE_NO_EXTRAPOLATION",
    "G12_INDEPENDENT_R3_VERIFIER_G22_CATCHES_FALSE_PASS",
    "G13_HISTORICAL_R3_SEALED_DESCRIPTIVE_ONLY_UNCHANGED",
    "G14_FULL_PYTEST_FRESH_JUNIT_1_TO_1_ZERO_MANDATORY_SKIP",
    "G15_FULL_A1_SI8_ADAPTER_D4_PARITY_ZERO_MISMATCH",
    "G16_1X_NULL_E1_E2_E3_ANCHORS_UNCHANGED",
    "G17_SIX_DYNAMIC_COST_AND_FIXED_FILL_SEPARATED",
    "G18_LEGACY_R1_GUARD_H3_ADVISORY_R2_FWD_ONLY_INTACT",
    "G19_DISK_RAM_CHILD_CEILINGS_CLEANUP_ZERO_ORPHANS",
    "G20_INHERITED_RED_GREEN_FAULTS_FULLY_EXECUTED",
    "G21_NEW_UNIQUE_C2_RED_GREEN_FAULTS_FULLY_EXECUTED",
    "G22_NEW_VERIFIER_MUTATION_TESTS_ALL_DETECTED",
    "G23_DISTINCT_PID_INDEPENDENT_VERIFIER_OWN_MANIFEST",
    "G24_NO_FAKE_ECONOMIC_OOS_LIVE_PAPER_AUTO_OFF",
    "G25_RELEASE_GATE_FAIL_WHEN_POLICY_INVALID",
    "G26_TWO_PHASE_COMMIT_PUSH_REMOTE_READBACK",
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


def _independent_recompute_raw_bench_c2(
    raw_doc: Mapping[str, Any],
    rep_pol: Mapping[str, Any],
    *,
    kind: str,
    expected_run_id: str,
    expected_tested_sha: str,
    max_safe_cap: int,
) -> Dict[str, Any]:
    """Standalone verifier-owned raw benchmark & policy recomputer (zero producer/service import)."""
    viols: List[str] = []
    if kind == "pytest_controlled_subset":
        dk_canon = "wall_clock_seconds"
        cross = ("wall_seconds", "canonical_sorted_sha256", "rows_count")
        cnt_k = "executed_tests_count"
        sha_k = "canonical_sorted_outcome_sha256"
    elif kind == "r2_science_slice":
        dk_canon = "wall_seconds"
        cross = ("wall_clock_seconds", "canonical_sorted_outcome_sha256", "executed_tests_count")
        cnt_k = "rows_count"
        sha_k = "canonical_sorted_sha256"
    else:
        return {"valid": False, "violations": [f"C2V_UNKNOWN_KIND:{kind}"]}

    if raw_doc.get("run_id") != expected_run_id:
        viols.append(f"C2V_RUN_ID_MISMATCH:{kind}")
    if raw_doc.get("tested_executable_sha") != expected_tested_sha:
        viols.append(f"C2V_TESTED_SHA_MISMATCH:{kind}")
    if raw_doc.get("run_id") in (PRIOR_KH03_R3_RUN_ID, PRIOR_KH03_R3_C1_RUN_ID) or raw_doc.get("tested_executable_sha") in (PRIOR_KH03_R3_TESTED_SHA, PRIOR_KH03_R3_C1_TESTED_SHA):
        viols.append(f"C2V_STALE_PRIOR_BENCH_REUSED:{kind}")

    runs = raw_doc.get("runs")
    if not isinstance(runs, list) or len(runs) == 0:
        viols.append(f"C2V_EMPTY_RUNS:{kind}")
        return {"valid": False, "violations": viols}

    req_w = {"w=1": 1, "w=2": 2, "w=4": 4, "w=1_repeat": 1}
    durs: Dict[str, float] = {}
    cnts: Dict[str, int] = {}
    shas: Dict[str, str] = {}

    for i, r in enumerate(runs):
        if not isinstance(r, Mapping):
            viols.append(f"C2V_ROW_NOT_OBJ:{kind}:{i}")
            continue
        for ck in cross:
            if ck in r:
                viols.append(f"C2V_MIXED_SCHEMA_KEY:{kind}:{ck}")
        ml = r.get("mode_label")
        if ml not in req_w:
            viols.append(f"C2V_BAD_MODE:{kind}:{ml!r}")
            continue
        if ml in durs:
            viols.append(f"C2V_DUP_MODE:{kind}:{ml}")
            continue
        wk = r.get("workers")
        if not _is_int(wk) or int(wk) != req_w[ml]:
            viols.append(f"C2V_BAD_WORKERS:{kind}:{ml}:{wk!r}")
        if kind == "pytest_controlled_subset" and "worker_pids" in r:
            pids = r.get("worker_pids")
            if not isinstance(pids, list) or len(pids) != req_w[ml] or len(set(pids)) != req_w[ml] or any((not _is_int(p) or p <= 0) for p in pids):
                viols.append(f"C2V_BAD_PIDS:{kind}:{ml}")
        if kind == "r2_science_slice" and "chunks_count" in r:
            cc = r.get("chunks_count")
            if not _is_int(cc) or int(cc) != req_w[ml]:
                viols.append(f"C2V_BAD_CHUNKS:{kind}:{ml}")

        if dk_canon not in r:
            viols.append(f"C2V_MISSING_DUR:{kind}:{ml}")
        else:
            all_dk = [k for k in (dk_canon, "elapsed_sec", "wall_sec", "duration_sec") if k in r]
            vals: List[float] = []
            bad = False
            for dk in all_dk:
                dv = r.get(dk)
                if not _is_num(dv):
                    viols.append(f"C2V_NON_NUM_DUR:{kind}:{ml}:{dk}")
                    bad = True
                    continue
                fv = float(dv)
                if not math.isfinite(fv) or fv <= 0.0 or fv > 86_400.0:
                    viols.append(f"C2V_BAD_DUR_RANGE:{kind}:{ml}:{dk}")
                    bad = True
                    continue
                vals.append(fv)
            if not bad and len(vals) > 1 and any(abs(x - vals[0]) > 1e-9 for x in vals[1:]):
                viols.append(f"C2V_CONFLICT_DUR:{kind}:{ml}")
            if not bad and _is_num(r.get(dk_canon)):
                durs[ml] = float(r[dk_canon])

        cv = r.get(cnt_k)
        if not _is_int(cv) or int(cv) <= 0:
            viols.append(f"C2V_BAD_COUNT:{kind}:{ml}")
        else:
            cnts[ml] = int(cv)
        sv = r.get(sha_k)
        if not _is_hex64(sv):
            viols.append(f"C2V_BAD_SHA:{kind}:{ml}")
        else:
            shas[ml] = str(sv).lower()

    for m in ("w=1", "w=2", "w=4", "w=1_repeat"):
        if m not in durs:
            viols.append(f"C2V_MISSING_MODE:{kind}:{m}")
    if len(cnts) == 4 and len(set(cnts.values())) != 1:
        viols.append(f"C2V_COUNT_DRIFT:{kind}")
    if "w=1" in shas and "w=1_repeat" in shas and shas["w=1"] != shas["w=1_repeat"]:
        viols.append(f"C2V_W1_REPEAT_SHA_DRIFT:{kind}")
    if len(shas) == 4 and len(set(shas.values())) != 1:
        viols.append(f"C2V_SEMANTIC_SHA_DRIFT:{kind}")

    if any(m not in durs for m in ("w=1", "w=2", "w=4", "w=1_repeat")):
        return {"valid": False, "violations": viols}

    means = {m: round(durs[m], 4) for m in ("w=1", "w=2", "w=4")}
    fastest = min(("w=1", "w=2", "w=4"), key=lambda m: (round(means[m], 6), req_w[m]))
    sp2 = round(durs["w=1"] / max(1e-9, durs["w=2"]), 3)
    sp4 = round(durs["w=1"] / max(1e-9, durs["w=4"]), 3)
    w1r = round(durs["w=1_repeat"], 4)
    w1v = round(abs(durs["w=1_repeat"] - durs["w=1"]) / max(1e-9, min(durs["w=1"], durs["w=1_repeat"])), 4)

    if not isinstance(rep_pol, Mapping):
        viols.append(f"C2V_POLICY_MISSING:{kind}")
        return {"valid": False, "violations": viols}
    if rep_pol.get("status") != "PASS" or rep_pol.get("can_certify") is not True:
        viols.append(f"C2V_POLICY_NOT_CERTIFIABLE:{kind}")
    if rep_pol.get("policy_status") != "PASS_MEASURED_AND_SAFE_CAP_APPLIED":
        viols.append(f"C2V_POLICY_ENUM_INVALID:{kind}")
    rm = rep_pol.get("mean_wall_sec_by_mode")
    if not isinstance(rm, Mapping) or len(rm) == 0:
        viols.append(f"C2V_EMPTY_MEANS:{kind}")
    else:
        for m in ("w=1", "w=2", "w=4"):
            gv = rm.get(m)
            if not _is_num(gv) or abs(float(gv) - means[m]) > 1e-3:
                viols.append(f"C2V_MEANS_MISMATCH:{kind}:{m}")
    if rep_pol.get("measured_fastest_mode") != fastest or rep_pol.get("optimum_worker_policy") != fastest:
        viols.append(f"C2V_FASTEST_MISMATCH:{kind}:rep={rep_pol.get('measured_fastest_mode')}!=recomp={fastest}")
    if not _is_num(rep_pol.get("measured_speedup_w2_vs_w1")) or abs(float(rep_pol["measured_speedup_w2_vs_w1"]) - sp2) > 0.01:
        viols.append(f"C2V_SPEEDUP_W2_MISMATCH:{kind}")
    if not _is_num(rep_pol.get("measured_speedup_w4_vs_w1")) or abs(float(rep_pol["measured_speedup_w4_vs_w1"]) - sp4) > 0.01:
        viols.append(f"C2V_SPEEDUP_W4_MISMATCH:{kind}")
    if not _is_num(rep_pol.get("w1_repeat_wall_sec")) or abs(float(rep_pol["w1_repeat_wall_sec"]) - w1r) > 1e-3:
        viols.append(f"C2V_W1_REPEAT_WALL_MISMATCH:{kind}")
    if not _is_num(rep_pol.get("w1_repeat_variance_pct")) or abs(float(rep_pol["w1_repeat_variance_pct"]) - w1v) > 1e-3:
        viols.append(f"C2V_W1_REPEAT_VAR_MISMATCH:{kind}")
    sc = rep_pol.get("safe_worker_cap")
    aw = rep_pol.get("applied_worker_count")
    if not _is_int(sc) or int(sc) < 1 or int(sc) > max_safe_cap or int(sc) > GLOBAL_MAX_DESCENDANTS_C2:
        viols.append(f"C2V_BAD_SAFE_CAP:{kind}:{sc!r}")
    if not _is_int(aw) or int(aw) < 1 or int(aw) > max_safe_cap or int(aw) > GLOBAL_MAX_DESCENDANTS_C2 or (_is_int(sc) and int(aw) > int(sc)):
        viols.append(f"C2V_APPLIED_CAP_EXCEEDED:{kind}:{aw!r}")
    if rep_pol.get("full_suite_speedup_extrapolated") is not False:
        viols.append(f"C2V_EXTRAPOLATION_FORBIDDEN:{kind}")
    if rep_pol.get("timing_determinism_claimed") is not False:
        viols.append(f"C2V_TIMING_DET_FORBIDDEN:{kind}")

    return {
        "valid": len(viols) == 0,
        "means": means,
        "fastest": fastest,
        "speedup_w2_vs_w1": sp2,
        "speedup_w4_vs_w1": sp4,
        "w1_repeat_wall_sec": w1r,
        "w1_repeat_variance_pct": w1v,
        "semantic_sha256": next(iter(shas.values())) if len(set(shas.values())) == 1 else None,
        "semantic_count": next(iter(cnts.values())) if len(set(cnts.values())) == 1 else 0,
        "violations": viols,
    }


def _verify_junit_raw_xml_bijection_c2(
    run_dir: Path, f09: Mapping[str, Any], *, expected_run_id: str, expected_tested_sha: str
) -> Tuple[bool, List[str]]:
    viols: List[str] = []
    cm = f09.get("collection_manifest") or {}
    sm = f09.get("shard_manifest") or {}
    if not cm or not sm:
        return False, ["JUNIT_MISSING_MANIFESTS"]
    if cm.get("run_id") != expected_run_id or sm.get("run_id") != expected_run_id:
        viols.append("JUNIT_RUN_ID_MISMATCH")
    if cm.get("tested_executable_sha") != expected_tested_sha or sm.get("tested_executable_sha") != expected_tested_sha:
        viols.append("JUNIT_TESTED_SHA_MISMATCH")
    if cm.get("run_id") in (PRIOR_KH03_R3_RUN_ID, PRIOR_KH03_R3_C1_RUN_ID):
        viols.append("JUNIT_STALE_PRIOR_RUN_REUSED")
    if int(sm.get("nonzero_exit_shards_count", 1)) != 0:
        viols.append(f"JUNIT_NONZERO_SHARDS:{sm.get('nonzero_exit_shards_count')}")

    c_ids = list(cm.get("collected_node_ids") or ())
    if len(c_ids) < MIN_EXPECTED_PYTEST_COLLECTED_C2:
        viols.append(f"JUNIT_COLLECTED_BELOW_C1_BASELINE:{len(c_ids)}<{MIN_EXPECTED_PYTEST_COLLECTED_C2}")
    if len(c_ids) != int(cm.get("collected_node_ids_count", -1)) or len(set(c_ids)) != len(c_ids):
        viols.append("JUNIT_COLLECTED_COUNT_OR_DUP_ERROR")

    shards = list(sm.get("shards") or ())
    if not shards:
        return False, ["JUNIT_ZERO_SHARDS"]

    sched_all: List[str] = []
    tot_t, tot_f, tot_e, tot_s, mand_s = 0, 0, 0, 0, 0
    for sh in shards:
        if int(sh.get("exit_code", 1)) != 0 or bool(sh.get("timed_out", True)) or bool(sh.get("reused", True)):
            viols.append(f"JUNIT_BAD_SHARD:{sh.get('shard_id')}")
        rel_x = str(sh.get("junit_xml_path") or "")
        xp = run_dir / rel_x
        if not xp.is_file() or xp.stat().st_size == 0:
            viols.append(f"JUNIT_XML_MISSING:{rel_x}")
            continue
        if _sha256_file(xp) != str(sh.get("xml_sha256") or sh.get("junit_sha256") or ""):
            viols.append(f"JUNIT_XML_SHA_MISMATCH:{rel_x}")
        sched = list(sh.get("scheduled_node_ids") or ())
        sched_all.extend(sched)
        try:
            root = ET.fromstring(xp.read_text("utf-8"))
        except Exception as exc:
            viols.append(f"JUNIT_XML_PARSE_ERR:{rel_x}:{exc}")
            continue
        tcs = list(root.iter("testcase"))
        tot_t += len(tcs)
        if len(tcs) != len(sched):
            viols.append(f"JUNIT_SHARD_LEN_MISMATCH:{sh.get('shard_id')}")
        for tc in tcs:
            cn = str(tc.attrib.get("classname") or "")
            nm = str(tc.attrib.get("name") or "")
            if tc.find("failure") is not None:
                tot_f += 1
            if tc.find("error") is not None:
                tot_e += 1
            if tc.find("skipped") is not None:
                tot_s += 1
                if any(tok in cn or tok in nm for tok in ("test_kh_02_r2", "test_kh03_r3", "test_kh03_r3_c1", "test_kh03_r3_c2")):
                    mand_s += 1

    if sorted(sched_all) != sorted(c_ids):
        viols.append("JUNIT_BIJECTION_MISMATCH")
    tot_p = tot_t - tot_f - tot_e - tot_s
    if (
        tot_t != int(f09.get("executed_count", -1))
        or tot_p != int(f09.get("passed_count", -1))
        or tot_s != int(f09.get("skipped_count", -1))
        or tot_f != int(f09.get("failed_count", -1))
        or tot_e != int(f09.get("error_count", -1))
    ):
        viols.append("JUNIT_COUNTS_TAMPERED_VS_RAW_XML")
    if tot_f != 0 or tot_e != 0 or mand_s != 0:
        viols.append(f"JUNIT_FAIL_ERR_OR_MANDATORY_SKIP:f={tot_f},e={tot_e},ms={mand_s}")
    return len(viols) == 0, viols


def _audit_telemetry_gz_vs_13_c2(mem_gz: Path, f13: Mapping[str, Any]) -> Dict[str, Any]:
    viols: List[str] = []
    if not mem_gz.is_file() or mem_gz.stat().st_size == 0:
        return {"status": "FAIL_MISSING_LOG", "samples_count": 0, "below_floor_samples_count": 1, "max_workers_observed_in_samples": 99, "violations": ["MISSING_TELEMETRY_LOG"]}
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
                    if rf < HARD_RAM_FLOOR_MIB_C2:
                        below_r += 1
                dk = row.get("disk_free_gib")
                if _is_num(dk):
                    df = float(dk)
                    min_dk = min(min_dk, df)
                    if df < STRICT_RUNTIME_DISK_MIN_GIB_C2:
                        below_d += 1
                ch = row.get("child_count")
                if _is_int(ch):
                    max_ch = max(max_ch, int(ch))
    except Exception as exc:
        return {"status": "FAIL_PARSE", "samples_count": n, "below_floor_samples_count": 1, "max_workers_observed_in_samples": 99, "violations": [f"TELEMETRY_PARSE_ERR:{exc}"]}

    if n == 0 or min_ram == float("inf") or min_dk == float("inf"):
        viols.append("TELEMETRY_EMPTY_OR_INCOMPLETE")
    if below_r > 0 or min_ram < HARD_RAM_FLOOR_MIB_C2:
        viols.append(f"TELEMETRY_RAM_BELOW_512MIB:{min_ram:.2f}")
    if below_d > 0 or min_dk < STRICT_RUNTIME_DISK_MIN_GIB_C2:
        viols.append(f"TELEMETRY_DISK_BELOW_20GIB:{min_dk:.4f}")
    if max_ch > GLOBAL_MAX_DESCENDANTS_C2:
        viols.append(f"TELEMETRY_CHILD_EXCEEDED_4:{max_ch}")
    rep_ram, rep_dk, rep_ch = f13.get("min_available_ram_mib"), f13.get("min_disk_free_gib"), f13.get("max_descendant_children")
    if not _is_num(rep_ram) or abs(float(rep_ram) - min_ram) > 1.0:
        viols.append("TELEMETRY_13_RAM_MUTATED_VS_GZ")
    if not _is_num(rep_dk) or abs(float(rep_dk) - min_dk) > 0.05:
        viols.append("TELEMETRY_13_DISK_MUTATED_VS_GZ")
    if not _is_int(rep_ch) or int(rep_ch) < max_ch or int(rep_ch) > GLOBAL_MAX_DESCENDANTS_C2:
        viols.append("TELEMETRY_13_CHILDREN_MUTATED_VS_GZ")
    return {
        "status": "PASS" if len(viols) == 0 else "FAIL",
        "samples_count": n,
        "min_available_ram_mib": round(min_ram if min_ram != float("inf") else 0.0, 2),
        "min_disk_free_gib": round(min_dk if min_dk != float("inf") else 0.0, 4),
        "max_workers_observed_in_samples": max_ch,
        "below_floor_samples_count": below_r + below_d,
        "violations": viols,
    }


def _audit_economic_regression_11_c2(f11: Mapping[str, Any]) -> Tuple[bool, List[str]]:
    viols: List[str] = []
    dyn = f11.get("dynamic_full_rerisk_six_scenarios") or {}
    fix = f11.get("fixed_fills_descriptive_repricing") or {}
    if len(dyn) != 6 or len(fix) < 1 or f11.get("dynamic_vs_fixed_fill_populations_separated") is not True:
        viols.append("ECONOMIC_SIX_SCENARIO_OR_FIXED_SCOPE_INVALID")
    for sc_id, sc_obj in dyn.items():
        for ac, rs in ((sc_obj or {}).get("arms") or {}).items():
            if int((rs or {}).get("closed") or 0) > 0:
                g, fe, ne = float(rs.get("gross_pnl_usd") or 0.0), float(rs.get("total_fees_usd") or 0.0), float(rs.get("net_pnl_usd") or 0.0)
                if fe <= 0.0 and (g - ne) > 0.01:
                    viols.append(f"ECONOMIC_ZERO_FEE_BUG:{sc_id}:{ac}")
                if abs((g - fe) - ne) > 0.01:
                    viols.append(f"ECONOMIC_FEE_RECONCILIATION_DRIFT:{sc_id}:{ac}")
    base_1x = (dyn.get("1X_BASE_0p0004") or {}).get("arms") or {}
    for ac, anc in SEALED_R3_1X_ECONOMIC_ANCHORS.items():
        got = base_1x.get(ac) or {}
        if int(got.get("closed", -1)) != anc["closed"] or int(got.get("wins", -1)) != anc["wins"] or int(got.get("losses", -1)) != anc["losses"]:
            viols.append(f"ECONOMIC_ANCHOR_COUNT_DRIFT:{ac}")
        if abs(float(got.get("net_pnl_usd", 9999.0)) - anc["net_pnl_usd"]) > 0.01:
            viols.append(f"ECONOMIC_ANCHOR_NET_DRIFT:{ac}")
        if abs(float(got.get("expectancy_r", 9999.0)) - anc["expectancy_r"]) > 0.001:
            viols.append(f"ECONOMIC_ANCHOR_EXPR_DRIFT:{ac}")
    if f11.get("RESEARCH_ECONOMIC_VERDICT") != "NO_PROVEN_EDGE":
        viols.append(f"FORGED_RESEARCH_ECONOMIC_VERDICT:{f11.get('RESEARCH_ECONOMIC_VERDICT')}")
    if f11.get("DEV_RESEARCH_CANDIDATE") != "NO_ECONOMIC_ADVANCEMENT_CANDIDATE":
        viols.append("FORGED_DEV_RESEARCH_CANDIDATE")
    if list(f11.get("passing_dev_candidate_arms") or []) != []:
        viols.append("FORGED_PASSING_DEV_ARMS")
    if int(f11.get("OOS_EVALUATIONS", -1)) != 0:
        viols.append("FORGED_NONZERO_OOS_EVALUATIONS")
    if f11.get("observed_l1_bbo_available") is not False:
        viols.append("FORGED_OBSERVED_L1_BBO_AVAILABLE")
    if str(f11.get("H3_RESIZE_EXECUTION_STATUS") or "") == "PASS" or bool(f11.get("h3_promoted_as_executable", True)):
        viols.append("FORGED_H3_EXECUTABLE_PASS")
    if f11.get("ACCOUNT_EFFECTIVE_FEE_VERDICT") != "UNKNOWN_NOT_LOCALLY_VERIFIED":
        viols.append("FORGED_ACCOUNT_FEE_VERDICT")
    if f11.get("LEGACY_METHOD_GUARD") != "PASS" or int(f11.get("unauthorized_r1_callers_count", 1)) != 0:
        viols.append("LEGACY_R1_GUARD_FAILED")
    return len(viols) == 0, viols


def recompute_kh03_r3_c2_26_gates_and_verdicts(
    *,
    docs: Mapping[str, Mapping[str, Any]],
    pytest_recomp: Mapping[str, Any],
    science_recomp: Mapping[str, Any],
    mem_audit: Mapping[str, Any],
    junit_bijection_ok: bool,
    economic_ok: bool,
    verifier_violations_count: int = 0,
    for_final_verifier_manifest: bool = False,
) -> Dict[str, Any]:
    """Independently recomputes ALL 26 orthogonal KH-03.R3.C2 release gates (G01..G26) & verdicts."""
    f00, f01, f02, f03, f04, f05 = docs["00"], docs["01"], docs["02"], docs["03"], docs["04"], docs["05"]
    f06, f07, f08, f09, f10, f11 = docs["06"], docs["07"], docs["08"], docs["09"], docs["10"], docs["11"]
    f12, f13, f14, f15, f18 = docs["12"], docs["13"], docs["14"], docs["15"], docs["18"]

    gates: Dict[str, str] = {}

    # G01
    tsha = str(f00.get("tested_executable_sha") or "")
    stale = {
        PRIOR_KH03_R3_TESTED_SHA, PRIOR_KH03_R3_EVIDENCE_SHA, PRIOR_KH03_R3_PUB_HEAD_SHA,
        PRIOR_KH03_R3_C1_TESTED_SHA, PRIOR_KH03_R3_C1_EVIDENCE_SHA, PRIOR_KH03_R3_C1_PUB_HEAD_SHA,
    }
    sha_ok = (
        f00.get("branch") == MANDATORY_BRANCH_KH03_R3_C2
        and len(tsha) == 40
        and tsha == f00.get("start_head_sha") == f00.get("end_head_sha")
        and tsha not in stale
        and str(f00.get("run_id") or "") not in (PRIOR_KH03_R3_RUN_ID, PRIOR_KH03_R3_C1_RUN_ID)
    )
    clean_ok = bool(f00.get("git_clean_at_start", False)) and not bool(f00.get("local_uncommitted_changes", False))
    gates["G01_BRANCH_CLEAN_UNIQUE_SHA_ORIGIN"] = "PASS" if (sha_ok and clean_ok) else ("BLOCKED_UNCOMMITTED_LOCAL_RUN" if sha_ok else "FAIL")

    # G02 & G03
    gates["G02_DATASET_AND_R1_R2_R3_C1_SEALED_IMMUTABLE"] = (
        "PASS" if (f02.get("status") == "PASS" and bool(f02.get("canonical_dataset_sha256_match")) and bool(f02.get("all_prior_sealed_artifacts_intact"))) else "FAIL"
    )
    gates["G03_PROD_ENTRY_SL_TP_FEES_ONEPOS_AST_PARITY"] = (
        "PASS"
        if (
            f01.get("status") == "PASS"
            and int(f01.get("locked_files_modified_count", 1)) == 0
            and int(f01.get("unauthorized_changed_files_count", 1)) == 0
            and len(f01.get("forbidden_order_import_hits") or []) == 0
            and bool(f01.get("cost_assumptions_defaults_intact"))
            and f12.get("status") == "PASS"
            and bool(f12.get("c1_worker_policy_service_untouched"))
        )
        else "FAIL"
    )

    # G04 & G05 & G08 & G12 & G13 & G25 (Legacy R3 fail-closed & G22 verifier)
    gates["G04_GENUINE_OLD_R3_FALSE_PASS_RED_REPRODUCED"] = (
        "PASS" if (f03.get("status") == "PASS" and int(f03.get("red_cases_reproduced_count", 0)) >= 6 and bool(f03.get("pre_fix_verifier_swallowed_forged_f19_proved"))) else "FAIL"
    )
    gates["G05_STRICT_F19_CHECKS_POLICY_PASS_AND_CAN_CERTIFY"] = (
        "PASS" if (f04.get("status") == "PASS" and int(f04.get("cases_passed", 0)) >= 40 and int(f04.get("cases_failed", 1)) == 0) else "FAIL"
    )
    gates["G08_NO_NONFINITE_MIXED_MISSING_FORGED_SCHEMA"] = (
        "PASS" if (f04.get("status") == "PASS" and bool(f04.get("nonfinite_mixed_missing_forged_all_rejected"))) else "FAIL"
    )
    gates["G12_INDEPENDENT_R3_VERIFIER_G22_CATCHES_FALSE_PASS"] = (
        "PASS" if (f05.get("status") == "PASS" and bool(f05.get("clean_c2_r3_run_passes")) and bool(f05.get("forged_f19_rejected_fail_closed"))) else "FAIL"
    )
    gates["G13_HISTORICAL_R3_SEALED_DESCRIPTIVE_ONLY_UNCHANGED"] = (
        "PASS"
        if (
            f05.get("status") == "PASS"
            and f05.get("archived_r3_descriptive_audit_classification") == "LEGACY_DESCRIPTIVE_ONLY"
            and f05.get("archived_r3_admitted_for_new_c2_certification") is False
            and f05.get("archived_r3_strict_mode_rejected") is True
            and bool(f02.get("all_prior_sealed_artifacts_intact"))
        )
        else "FAIL"
    )
    gates["G25_RELEASE_GATE_FAIL_WHEN_POLICY_INVALID"] = (
        "PASS" if (f04.get("status") == "PASS" and bool(f04.get("g22_invalid_blocks_overall_engineering_pass"))) else "FAIL"
    )

    # G19: Resource safety (evaluate before heavy gates)
    mp00 = f00.get("strict_multi_mount_probe") or {}
    waiver = bool(f00.get("owner_disk_preflight_waiver", False)) or bool(mp00.get("owner_waiver_permitted", False)) or bool(f13.get("owner_disk_preflight_waiver", False)) or bool(f18.get("owner_disk_preflight_waiver", False))
    mounts = list(mp00.get("distinct_mounts") or ())
    probe_errs = list(mp00.get("probe_errors") or ())
    pre_disk_ok = len(mounts) > 0 and len(probe_errs) == 0 and all(float(m.get("free_gib", 0.0)) >= STRICT_PREFLIGHT_DISK_MIN_GIB_C2 and float(m.get("projected_post_reserve_gib", 0.0)) >= STRICT_RUNTIME_DISK_MIN_GIB_C2 for m in mounts) and bool(mp00.get("can_run_heavy_stages", False))
    rt_disk_ok = float(f13.get("min_disk_free_gib") or 0.0) >= STRICT_RUNTIME_DISK_MIN_GIB_C2
    ram_pid_ok = (
        float(f13.get("min_available_ram_mib") or 0.0) >= HARD_RAM_FLOOR_MIB_C2
        and int(f13.get("max_descendant_children", 99)) <= GLOBAL_MAX_DESCENDANTS_C2
        and int(f13.get("orphan_children_after_cleanup", 99)) == 0
        and bool(f13.get("sentinel_temp_cleaned_up", False))
        and mem_audit.get("status") == "PASS"
        and int(mem_audit.get("below_floor_samples_count", 1)) == 0
        and int(mem_audit.get("max_workers_observed_in_samples", 99)) <= GLOBAL_MAX_DESCENDANTS_C2
    )
    if waiver or len(probe_errs) > 0 or not ram_pid_ok:
        gates["G19_DISK_RAM_CHILD_CEILINGS_CLEANUP_ZERO_ORPHANS"] = "FAIL"
    elif pre_disk_ok and rt_disk_ok:
        gates["G19_DISK_RAM_CHILD_CEILINGS_CLEANUP_ZERO_ORPHANS"] = "PASS"
    elif pre_disk_ok and not rt_disk_ok:
        gates["G19_DISK_RAM_CHILD_CEILINGS_CLEANUP_ZERO_ORPHANS"] = "FAIL"
    else:
        gates["G19_DISK_RAM_CHILD_CEILINGS_CLEANUP_ZERO_ORPHANS"] = "BLOCKED_LOW_DISK"

    res_pass = gates["G19_DISK_RAM_CHILD_CEILINGS_CLEANUP_ZERO_ORPHANS"] == "PASS"
    low_disk = gates["G19_DISK_RAM_CHILD_CEILINGS_CLEANUP_ZERO_ORPHANS"] == "BLOCKED_LOW_DISK"

    # G06, G07, G09, G10, G11: Fresh W1/W2/W4/W1_repeat benchmarks
    if f06.get("status") == "NOT_RUN_BLOCKED_LOW_DISK" or f07.get("status") == "NOT_RUN_BLOCKED_LOW_DISK":
        st_b = "NOT_RUN_BLOCKED_LOW_DISK" if low_disk else "FAIL"
        for gk in (
            "G06_RAW_PYTEST_4MODE_VALIDATES_INDEPENDENTLY",
            "G07_RAW_SCIENCE_4MODE_VALIDATES_INDEPENDENTLY",
            "G09_MEASURED_WINNER_AND_SAFE_APPLIED_CAP_SEPARATED",
            "G10_SEMANTIC_DETERMINISTIC_W1_W2_W4_W1REPEAT",
            "G11_TIMING_NOISE_AND_SCOPE_NO_EXTRAPOLATION",
        ):
            gates[gk] = st_b
    else:
        p_pol = f08.get("pytest_worker_policy") or {}
        s_pol = f08.get("science_worker_policy") or {}
        gates["G06_RAW_PYTEST_4MODE_VALIDATES_INDEPENDENTLY"] = "PASS" if (res_pass and bool(pytest_recomp.get("valid"))) else "FAIL"
        gates["G07_RAW_SCIENCE_4MODE_VALIDATES_INDEPENDENTLY"] = "PASS" if (res_pass and bool(science_recomp.get("valid"))) else "FAIL"
        gates["G09_MEASURED_WINNER_AND_SAFE_APPLIED_CAP_SEPARATED"] = (
            "PASS"
            if (
                res_pass and bool(pytest_recomp.get("valid")) and bool(science_recomp.get("valid"))
                and f08.get("status") == "PASS"
                and int(p_pol.get("applied_worker_count", 99)) <= 3
                and int(s_pol.get("applied_worker_count", 99)) <= GLOBAL_MAX_DESCENDANTS_C2
                and p_pol.get("full_suite_speedup_extrapolated") is False
                and s_pol.get("full_suite_speedup_extrapolated") is False
            )
            else "FAIL"
        )
        gates["G10_SEMANTIC_DETERMINISTIC_W1_W2_W4_W1REPEAT"] = (
            "PASS"
            if (
                res_pass and bool(pytest_recomp.get("valid")) and bool(science_recomp.get("valid"))
                and bool(f06.get("all_4_runs_identical")) and bool(f07.get("all_4_runs_identical"))
            )
            else "FAIL"
        )
        gates["G11_TIMING_NOISE_AND_SCOPE_NO_EXTRAPOLATION"] = (
            "PASS"
            if (
                res_pass and bool(pytest_recomp.get("valid")) and bool(science_recomp.get("valid"))
                and p_pol.get("timing_determinism_claimed") is False
                and s_pol.get("timing_determinism_claimed") is False
                and p_pol.get("full_suite_speedup_extrapolated") is False
                and s_pol.get("full_suite_speedup_extrapolated") is False
                and f08.get("full_suite_speedup_extrapolated") is False
            )
            else "FAIL"
        )

    # G14: Full pytest JUnit 1:1
    if f09.get("status") == "NOT_RUN_BLOCKED_LOW_DISK":
        gates["G14_FULL_PYTEST_FRESH_JUNIT_1_TO_1_ZERO_MANDATORY_SKIP"] = "NOT_RUN_BLOCKED_LOW_DISK" if low_disk else "FAIL"
    elif (
        f09.get("status") == "PASS" and res_pass and junit_bijection_ok
        and int(f09.get("collected_count", 0)) >= MIN_EXPECTED_PYTEST_COLLECTED_C2
        and int(f09.get("collected_count", -1)) == int(f09.get("executed_count", -2))
        and int(f09.get("failed_count", 1)) == 0 and int(f09.get("error_count", 1)) == 0
        and int(f09.get("mandatory_skipped_count", 1)) == 0
    ):
        gates["G14_FULL_PYTEST_FRESH_JUNIT_1_TO_1_ZERO_MANDATORY_SKIP"] = "PASS"
    else:
        gates["G14_FULL_PYTEST_FRESH_JUNIT_1_TO_1_ZERO_MANDATORY_SKIP"] = "FAIL"

    # G15: Full A1/SI8/Adapter/D4 parity
    fp10 = f10.get("per_decision_and_episode_fingerprints") or {}
    fp_ok = all(fp10.get(k) == v for k, v in SEALED_BASELINE_PARITY_FINGERPRINTS.items())
    if f10.get("BASELINE_PARITY_VERDICT") == "NOT_RUN_BLOCKED_LOW_DISK":
        gates["G15_FULL_A1_SI8_ADAPTER_D4_PARITY_ZERO_MISMATCH"] = "NOT_RUN_BLOCKED_LOW_DISK" if low_disk else "FAIL"
    elif (
        f10.get("BASELINE_PARITY_VERDICT") == "PASS" and res_pass and fp_ok
        and int(f10.get("a1_population_count", 0)) == 56710 and int(f10.get("a1_mismatches_count", 1)) == 0
        and int(f10.get("si8_population_count", 0)) == 6375 and int(f10.get("si8_mismatches_count", 1)) == 0
        and int(f10.get("three_adapter_population_count", 0)) == 246 and int(f10.get("three_adapter_mismatches_count", 1)) == 0
        and int(f10.get("d4_historical_closed_trades", 1)) == 0
    ):
        gates["G15_FULL_A1_SI8_ADAPTER_D4_PARITY_ZERO_MISMATCH"] = "PASS"
    else:
        gates["G15_FULL_A1_SI8_ADAPTER_D4_PARITY_ZERO_MISMATCH"] = "FAIL"

    # G16, G17, G18, G24
    gates["G16_1X_NULL_E1_E2_E3_ANCHORS_UNCHANGED"] = "PASS" if (economic_ok and f11.get("status") == "PASS" and bool(f11.get("historical_r3_1x_anchors_exact_match"))) else "FAIL"
    gates["G17_SIX_DYNAMIC_COST_AND_FIXED_FILL_SEPARATED"] = "PASS" if (economic_ok and len(f11.get("dynamic_full_rerisk_six_scenarios") or {}) == 6 and len(f11.get("fixed_fills_descriptive_repricing") or {}) >= 1 and bool(f11.get("dynamic_vs_fixed_fill_populations_separated")) and int(f11.get("zero_fee_display_bugs_count", 1)) == 0) else "FAIL"
    gates["G18_LEGACY_R1_GUARD_H3_ADVISORY_R2_FWD_ONLY_INTACT"] = "PASS" if (economic_ok and f11.get("LEGACY_METHOD_GUARD") == "PASS" and int(f11.get("unauthorized_r1_callers_count", 1)) == 0 and bool(f11.get("r2_forward_only_embargo_240m_verified")) and bool(f11.get("h3_r2_excluded_from_executable_arms")) and str(f11.get("H3_RESIZE_EXECUTION_STATUS") or "") != "PASS") else "FAIL"

    prov = f02.get("c4_provenance_and_oos_boundary") or {}
    modes_ok = all(d.get("ACTIVATION_READY") == "NO" and all((d.get("runtime_safety_modes") or {}).get(m) == "OFF" for m in ("PRODUCTION", "LIVE", "PAPER", "AUTO")) for d in (f00, f01, f11))
    oos_ok = int(f11.get("OOS_EVALUATIONS", -1)) == 0 and f11.get("FRESH_OOS") == "BLOCKED_NO_UNTOUCHED_DATA" and prov.get("network_capture_enabled") is False and prov.get("live_network_capture_executed") is False and int(prov.get("real_oos_escrow_final_record_count", -1)) == 0
    gates["G24_NO_FAKE_ECONOMIC_OOS_LIVE_PAPER_AUTO_OFF"] = "PASS" if (economic_ok and modes_ok and oos_ok) else "FAIL"

    # G20, G21, G22, G23
    gates["G20_INHERITED_RED_GREEN_FAULTS_FULLY_EXECUTED"] = "PASS" if (f14.get("status") == "PASS" and int(f14.get("inherited_c1_r3_controls_count", 0)) >= 208 and int(f14.get("inherited_c1_r3_failed_controls", 1)) == 0) else "FAIL"
    gates["G21_NEW_UNIQUE_C2_RED_GREEN_FAULTS_FULLY_EXECUTED"] = "PASS" if (f14.get("status") == "PASS" and int(f14.get("new_c2_controls_count", 0)) >= 16 and int(f14.get("new_c2_failed_controls", 1)) == 0 and int(f14.get("unique_fault_ids", 0)) == int(f14.get("total_controls", -1))) else "FAIL"
    g22_ok = f15.get("status") == "PASS" and bool(f15.get("clean_clone_verifier_green")) and int(f15.get("new_c2_mutations_tested_count", 0)) >= 16 and int(f15.get("new_c2_mutations_failed_detection_count", 1)) == 0 and int(f15.get("inherited_r3_c1_mutations_failed_detection_count", 1)) == 0
    gates["G22_NEW_VERIFIER_MUTATION_TESTS_ALL_DETECTED"] = "PASS" if g22_ok else "FAIL"
    gates["G23_DISTINCT_PID_INDEPENDENT_VERIFIER_OWN_MANIFEST"] = ("PASS" if (verifier_violations_count == 0 and g22_ok) else "FAIL") if for_final_verifier_manifest else "PENDING_SEPARATE_OS_VERIFIER"

    # G26: Publication
    pub_st = str(f18.get("publication_status") or "NOT_PUBLISHED_BLOCKED_LOW_DISK")
    if waiver or "WAIVER" in pub_st:
        gates["G26_TWO_PHASE_COMMIT_PUSH_REMOTE_READBACK"] = "FAIL"
    elif pub_st == "PASS_REMOTE_VERIFIED" and bool(f18.get("remote_readback_sha256_verified")) and len(str(f18.get("evidence_commit_sha") or "")) == 40:
        gates["G26_TWO_PHASE_COMMIT_PUSH_REMOTE_READBACK"] = "PASS_REMOTE_VERIFIED"
    elif pub_st == "PRE_PUSH_PENDING":
        gates["G26_TWO_PHASE_COMMIT_PUSH_REMOTE_READBACK"] = "PRE_PUSH_PENDING"
    elif "BLOCKED" in pub_st:
        gates["G26_TWO_PHASE_COMMIT_PUSH_REMOTE_READBACK"] = pub_st
    else:
        gates["G26_TWO_PHASE_COMMIT_PUSH_REMOTE_READBACK"] = "FAIL"

    eng_keys = [k for k in GATE_26_KEYS_KH03_R3_C2 if not k.startswith(("G23_", "G26_"))]
    any_fail = any(gates[k] == "FAIL" for k in gates) or (for_final_verifier_manifest and verifier_violations_count > 0)
    any_low_disk = any("LOW_DISK" in gates[k] for k in eng_keys)
    eng_pass = all(gates[k] == "PASS" for k in eng_keys) and ((not for_final_verifier_manifest) or gates["G23_DISTINCT_PID_INDEPENDENT_VERIFIER_OWN_MANIFEST"] == "PASS")

    if any_fail:
        eng_v = "FAIL"
    elif any_low_disk:
        eng_v = "BLOCKED_RESOURCE"
    elif eng_pass:
        eng_v = "PASS"
    else:
        eng_v = "BLOCKED_INTEGRITY"

    pol_ok = all(gates[k] == "PASS" for k in ("G04_GENUINE_OLD_R3_FALSE_PASS_RED_REPRODUCED", "G05_STRICT_F19_CHECKS_POLICY_PASS_AND_CAN_CERTIFY", "G06_RAW_PYTEST_4MODE_VALIDATES_INDEPENDENTLY", "G07_RAW_SCIENCE_4MODE_VALIDATES_INDEPENDENTLY", "G08_NO_NONFINITE_MIXED_MISSING_FORGED_SCHEMA", "G09_MEASURED_WINNER_AND_SAFE_APPLIED_CAP_SEPARATED", "G11_TIMING_NOISE_AND_SCOPE_NO_EXTRAPOLATION", "G12_INDEPENDENT_R3_VERIFIER_G22_CATCHES_FALSE_PASS", "G25_RELEASE_GATE_FAIL_WHEN_POLICY_INVALID"))
    det_ok = gates["G10_SEMANTIC_DETERMINISTIC_W1_W2_W4_W1REPEAT"] == "PASS"

    if eng_v == "PASS" and economic_ok and oos_ok and modes_ok:
        overall = "PASS_KH03_R3_C2_LEGACY_FAIL_CLOSED_TECHNICAL_CLOSURE_NO_PROVEN_EDGE_OOS_BLOCKED"
    elif eng_v == "BLOCKED_RESOURCE":
        overall = "BLOCKED_LOW_DISK"
    else:
        overall = f"FAIL_OR_BLOCKED_{eng_v}"

    verdicts = {
        "ENGINEERING": eng_v,
        "WORKER_POLICY": "PASS" if pol_ok else ("BLOCKED_RESOURCE" if low_disk else "FAIL"),
        "DETERMINISM": "PASS" if det_ok else ("BLOCKED_RESOURCE" if low_disk else "FAIL"),
        "RESOURCE": "PASS" if res_pass else ("BLOCKED_LOW_DISK" if low_disk else "FAIL"),
        "ECONOMIC_DEV": "NO_ECONOMIC_ADVANCEMENT_CANDIDATE" if economic_ok else "FAIL",
        "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE" if economic_ok else "FAIL",
        "FRESH_OOS": "BLOCKED_NO_UNTOUCHED_DATA" if oos_ok else "FAIL",
        "KH04_ENTRY": "BLOCKED_NO_DEV_CANDIDATE" if (modes_ok and oos_ok and economic_ok) else "FAIL",
        "ACCOUNT_EFFECTIVE_FEE_VERDICT": str(f11.get("ACCOUNT_EFFECTIVE_FEE_VERDICT") or "FAIL"),
        "LIVE_NETWORK_VALIDATION": str(prov.get("LIVE_NETWORK_VALIDATION") or "FAIL"),
        "CI_STATUS": str(f18.get("ci_status") or "CI_UNKNOWN_NO_ACTIONS_RUN"),
        "PUBLICATION": gates["G26_TWO_PHASE_COMMIT_PUSH_REMOTE_READBACK"],
        "ACTIVATION_READY": "NO" if modes_ok else "FAIL",
        "OOS_EVALUATIONS": 0 if oos_ok else -1,
    }
    return {"overall_classification": overall, "gates_26": gates, "verdicts": verdicts}


def verify_kh03_r3_c2_artifacts_independently_from_disk(
    run_dir: Path,
    *,
    expected_run_id: str = "",
    expected_tested_sha: str = "",
    check_live_repo_pointers: bool = True,
    enforce_distinct_pid: bool = True,
    write_manifest: bool = True,
) -> Dict[str, Any]:
    """Separate-OS-process disk verifier recomputing all KH-03.R3.C2 invariants & G01..G26."""
    run_dir = Path(run_dir).resolve()
    verifier_pid = os.getpid()
    violations: List[str] = []

    if not run_dir.is_dir():
        return {"spec_version": SPEC_VERSION_KH03_R3_C2, "independent_verifier_status": "FAIL", "verifier_pid": verifier_pid, "exit_code_recommended": 1, "violations": [f"RUN_DIR_MISSING:{run_dir}"]}
    for rel in REQUIRED_ARTIFACTS_KH03_R3_C2:
        fp = run_dir / rel
        if not fp.is_file() or fp.stat().st_size == 0:
            violations.append(f"MISSING_OR_EMPTY_ARTIFACT:{rel}")
    if violations:
        return {"spec_version": SPEC_VERSION_KH03_R3_C2, "independent_verifier_status": "FAIL", "verifier_pid": verifier_pid, "exit_code_recommended": 1, "violations": violations}

    def _rd(fn: str) -> Dict[str, Any]:
        return _strict_loads((run_dir / fn).read_text("utf-8"))

    try:
        docs: Dict[str, Dict[str, Any]] = {
            "00": _rd("00_branch_remote_sha_dirty_preflight.json"),
            "01": _rd("01_locked_code_ownership_and_ast_parity.json"),
            "02": _rd("02_prior_r1_r2_r3_c1_artifact_sha_immutability.json"),
            "03": _rd("03_legacy_r3_false_pass_red_proof.json"),
            "04": _rd("04_r3_f19_policy_condition_green_boundary_suite.json"),
            "05": _rd("05_r3_independent_g22_raw_benchmark_recompute.json"),
            "06": _rd("06_fresh_pytest_w1_w2_w4_w1repeat.json"),
            "07": _rd("07_fresh_science_w1_w2_w4_w1repeat.json"),
            "08": _rd("08_measured_fastest_vs_safe_applied_worker_caps.json"),
            "09": _rd("09_full_repo_pytest_junit_1to1.json"),
            "10": _rd("10_full_A1_SI8_adapter_D4_parity.json"),
            "11": _rd("11_six_cost_e1_e2_e3_null_regression.json"),
            "12": _rd("12_legacy_and_c1_old_source_do_not_change.json"),
            "13": _rd("13_memory_disk_pid_telemetry.json"),
            "14": _rd("14_inherited_plus_new_red_green_fault_controls.json"),
            "15": _rd("15_independent_verifier_mutation_test_results.json"),
            "17": _rd("17_orthogonal_gate_matrix_and_verdicts.json"),
            "18": _rd("18_phase1_code_commit_push_receipt.json"),
        }
        readme_txt = (run_dir / "README_KH03_R3_C2.md").read_text("utf-8")
        handover_txt = (run_dir / "HANDOVER_KH03_R3_C2_VI.txt").read_text("utf-8")
    except Exception as exc:
        return {"spec_version": SPEC_VERSION_KH03_R3_C2, "independent_verifier_status": "FAIL", "verifier_pid": verifier_pid, "exit_code_recommended": 1, "violations": [f"ARTIFACT_STRICT_JSON_PARSE_ERROR:{exc}"]}

    f00, f01, f02, f06, f07, f08, f09, f11, f13, f17, f18 = (
        docs["00"], docs["01"], docs["02"], docs["06"], docs["07"], docs["08"],
        docs["09"], docs["11"], docs["13"], docs["17"], docs["18"],
    )
    orch_pid = int(f00.get("orchestrator_pid") or -1)
    if enforce_distinct_pid and verifier_pid == orch_pid:
        violations.append(f"VERIFIER_SAME_OS_PID_AS_ORCHESTRATOR:{verifier_pid}")

    run_id = expected_run_id or str(f00.get("run_id") or "")
    tested_sha = expected_tested_sha or str(f00.get("tested_executable_sha") or "")
    if f00.get("branch") != MANDATORY_BRANCH_KH03_R3_C2:
        violations.append(f"WRONG_BRANCH:{f00.get('branch')}")
    if run_id in (PRIOR_KH03_R3_RUN_ID, PRIOR_KH03_R3_C1_RUN_ID) or tested_sha in (PRIOR_KH03_R3_TESTED_SHA, PRIOR_KH03_R3_C1_TESTED_SHA):
        violations.append("STALE_PRIOR_RUN_OR_SHA_REUSED")
    if tested_sha != f00.get("tested_executable_sha") or tested_sha != f17.get("tested_executable_sha") or tested_sha != f18.get("tested_executable_sha"):
        violations.append("TESTED_SHA_MISMATCH_ACROSS_00_17_18")
    if len(tested_sha) >= 12 and not run_id.endswith(f"_{tested_sha[:12]}"):
        violations.append("RUN_ID_MISSING_SHA12_SUFFIX")

    if check_live_repo_pointers:
        for rel_p, exp_sha, lbl in PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R3_C2:
            p = REPO_ROOT / rel_p
            if not p.is_file() or _sha256_file(p) != exp_sha:
                violations.append(f"PRIOR_SEALED_ARTIFACT_MUTATED:{lbl}")
    else:
        pm = f02.get("prior_sealed_artifacts_immutability") or {}
        if any(not bool(v.get("match", False)) for v in pm.values() if isinstance(v, Mapping)):
            violations.append("PRIOR_SEALED_ARTIFACT_MUTATED")
    if f02.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH03_R3_C2 or not bool(f02.get("canonical_dataset_sha256_match", False)):
        violations.append("CANONICAL_DATASET_SHA_TAMPERED")
    if int(f01.get("locked_files_modified_count", 1)) != 0 or int(f01.get("unauthorized_changed_files_count", 1)) != 0:
        violations.append("LOCKED_OR_UNAUTHORIZED_FILE_MODIFIED")

    pytest_recomp = _independent_recompute_raw_bench_c2(
        f06, f08.get("pytest_worker_policy") or {}, kind="pytest_controlled_subset",
        expected_run_id=run_id, expected_tested_sha=tested_sha, max_safe_cap=3,
    )
    science_recomp = _independent_recompute_raw_bench_c2(
        f07, f08.get("science_worker_policy") or {}, kind="r2_science_slice",
        expected_run_id=run_id, expected_tested_sha=tested_sha, max_safe_cap=GLOBAL_MAX_DESCENDANTS_C2,
    )
    if f06.get("status") != "NOT_RUN_BLOCKED_LOW_DISK" and not pytest_recomp["valid"]:
        violations.extend(pytest_recomp["violations"])
    if f07.get("status") != "NOT_RUN_BLOCKED_LOW_DISK" and not science_recomp["valid"]:
        violations.extend(science_recomp["violations"])

    junit_ok = False
    if f09.get("status") == "PASS":
        junit_ok, j_errs = _verify_junit_raw_xml_bijection_c2(run_dir, f09, expected_run_id=run_id, expected_tested_sha=tested_sha)
        if not junit_ok:
            violations.extend(j_errs)

    econ_ok, econ_viols = _audit_economic_regression_11_c2(f11)
    if not econ_ok:
        violations.extend(econ_viols)
    for k_s in ("00", "01", "11"):
        ds = docs[k_s]
        if ds.get("ACTIVATION_READY") != "NO" or any((ds.get("runtime_safety_modes") or {}).get(m) != "OFF" for m in ("PRODUCTION", "LIVE", "PAPER", "AUTO")):
            violations.append(f"SAFETY_MODE_OR_ACTIVATION_VIOLATION_IN_{k_s}")

    mem_audit = _audit_telemetry_gz_vs_13_c2(run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz", f13)
    if mem_audit.get("status") != "PASS":
        violations.extend(mem_audit.get("violations") or ["TELEMETRY_FAILED"])

    if bool(f00.get("owner_disk_preflight_waiver", False)) or bool(f13.get("owner_disk_preflight_waiver", False)):
        violations.append("UNAPPROVED_OWNER_DISK_WAIVER_FORBIDDEN")
    if f18.get("publication_status") == "PASS_REMOTE_VERIFIED" and (not bool(f18.get("remote_readback_sha256_verified")) or len(str(f18.get("evidence_commit_sha") or "")) != 40):
        violations.append("FALSE_REMOTE_READBACK_CLAIM")
    if f18.get("ci_status") == "PASS" and not bool(f18.get("actual_github_actions_run_verified", False)):
        violations.append("FABRICATED_CI_PASS_WITHOUT_ACTIONS_RUN")

    recomp_pre = recompute_kh03_r3_c2_26_gates_and_verdicts(
        docs=docs, pytest_recomp=pytest_recomp, science_recomp=science_recomp, mem_audit=mem_audit,
        junit_bijection_ok=junit_ok, economic_ok=econ_ok, verifier_violations_count=len(violations), for_final_verifier_manifest=False,
    )
    stored_gates = f17.get("gates_26") or {}
    for gk, gv in recomp_pre["gates_26"].items():
        if stored_gates.get(gk) != gv:
            violations.append(f"GATE_TAMPER_OR_DRIFT:{gk}:stored={stored_gates.get(gk)}!=recomputed={gv}")

    for tok, err_code in (
        (run_id, "CROSS_VIEW_RUN_ID_MISSING"),
        ("ACTIVATION_READY=NO", "CROSS_VIEW_ACTIVATION_LOCK_MISSING"),
        ("NO_PROVEN_EDGE", "CROSS_VIEW_NO_PROVEN_EDGE_MISSING"),
        ("OOS_EVALUATIONS=0", "CROSS_VIEW_OOS_ZERO_MISSING"),
        ("CI_UNKNOWN_NO_ACTIONS_RUN", "CROSS_VIEW_CI_HONESTY_MISSING"),
    ):
        if tok not in readme_txt or tok not in handover_txt:
            violations.append(err_code)

    recomp_final = recompute_kh03_r3_c2_26_gates_and_verdicts(
        docs=docs, pytest_recomp=pytest_recomp, science_recomp=science_recomp, mem_audit=mem_audit,
        junit_bijection_ok=junit_ok, economic_ok=econ_ok, verifier_violations_count=len(violations), for_final_verifier_manifest=True,
    )

    art_map: Dict[str, Dict[str, Any]] = {}
    for fp in sorted(run_dir.rglob("*")):
        if not fp.is_file():
            continue
        rel = fp.relative_to(run_dir).as_posix()
        if rel == MANIFEST_16_NAME_KH03_R3_C2 or rel.endswith(".tmp"):
            continue
        art_map[rel] = {"size_bytes": int(fp.stat().st_size), "sha256": _sha256_file(fp)}

    eng_final = recomp_final["verdicts"]["ENGINEERING"]
    if len(violations) > 0 or eng_final == "FAIL":
        v_st, ec = "FAIL", 1
    elif eng_final == "PASS":
        v_st, ec = "PASS", 0
    else:
        v_st, ec = "PASS", 2

    payload: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R3_C2,
        "schema_version": SCHEMA_VERSION_KH03_R3_C2,
        "verified_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "orchestrator_pid": orch_pid,
        "verifier_pid": verifier_pid,
        "distinct_os_process_verified": (verifier_pid != orch_pid),
        "independent_verifier_status": v_st,
        "exit_code_recommended": ec,
        "overall_classification": recomp_final["overall_classification"],
        "recomputed_26_gates": recomp_final["gates_26"],
        "recomputed_verdicts": recomp_final["verdicts"],
        "independent_pytest_recomputation": pytest_recomp,
        "independent_science_recomputation": science_recomp,
        "independent_memory_telemetry_audit": mem_audit,
        "independent_junit_bijection_ok": junit_ok,
        "independent_economic_regression_ok": econ_ok,
        "violations_count": len(violations),
        "violations": violations,
        "artifact_count_hashed": len(art_map),
        "manifest_content_sha256": canonical_hash(art_map),
        "artifact_sha256_manifest": art_map,
    }
    if write_manifest:
        write_json_durable_atomic(run_dir / MANIFEST_16_NAME_KH03_R3_C2, payload)
    return payload


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="AURUM DESK KH-03.R3.C2 Separate-OS Independent Verifier")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--expected-run-id", default="")
    ap.add_argument("--expected-tested-sha", default="")
    ap.add_argument("--skip-live-repo-pointers", action="store_true")
    ap.add_argument("--allow-same-pid", action="store_true")
    ap.add_argument("--no-write-manifest", action="store_true")
    args = ap.parse_args(argv)
    res = verify_kh03_r3_c2_artifacts_independently_from_disk(
        Path(args.run_dir),
        expected_run_id=args.expected_run_id,
        expected_tested_sha=args.expected_tested_sha,
        check_live_repo_pointers=(not args.skip_live_repo_pointers),
        enforce_distinct_pid=(not args.allow_same_pid),
        write_manifest=(not args.no_write_manifest),
    )
    print(
        json.dumps(
            {
                "independent_verifier_status": res["independent_verifier_status"],
                "verifier_pid": res["verifier_pid"],
                "distinct_os_process_verified": res.get("distinct_os_process_verified"),
                "overall_classification": res.get("overall_classification"),
                "recomputed_verdicts": res.get("recomputed_verdicts"),
                "recomputed_26_gates": res.get("recomputed_26_gates"),
                "artifact_count_hashed": res.get("artifact_count_hashed"),
                "violations": res.get("violations"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return int(res.get("exit_code_recommended", 1))


if __name__ == "__main__":
    raise SystemExit(main())
