#!/usr/bin/env python3
"""
aurum-desk/tools/kh_03_r3_c1_independent_verifier.py

AURUM DESK — KH-03.R3.C1 | SEPARATE-OS INDEPENDENT DISK VERIFIER
SPEC VERSION: KH-03.R3.C1-v1.0 | ZERO IMPORT FROM ORCHESTRATOR | INDEPENDENT RAW RECOMPUTATION

Independently reads raw artifacts from disk in a distinct OS process, implements its OWN
minimal raw-JSON timing parser, semantic hash validator, speedup/cap recomputer, JUnit raw XML
1:1 bijection verifier, 0.5s gzipped telemetry auditor, economic/fee/OOS/activation honesty
auditor, and recomputes ALL 28 orthogonal gates (G01..G28) without trusting orchestrator PASS flags.

Exit codes:
  0 — All mandatory engineering & benchmark-policy gates PASS (G01..G25), honest G26/G27, 0 violations
  1 — Any gate FAIL or integrity/tamper violation detected
  2 — Blocked by disk/RAM resource floor or dirty preflight (not FAIL, not PASS)
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
from services.kh_03_r3_c1_worker_policy_service import (
    SCHEMA_VERSION_KH03_R3_C1,
    SPEC_VERSION_KH03_R3_C1,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_03_r3_independent_verifier import (
    PRIOR_KH02_R1_TESTED_SHA_SHARED,
    PRIOR_KH02_R2_C1_CODE_COMMIT_SHA_SHARED,
    PRIOR_KH02_R2_C2_TESTED_SHA_SHARED,
    PRIOR_KH02_R2_C3_TESTED_SHA,
    PRIOR_KH02_R2_C4_PUB_HEAD_SHA,
    PRIOR_KH02_R2_C4_TESTED_SHA,
    PRIOR_KH02_R2_TESTED_SHA_SHARED,
    PRIOR_KH03_R1_PUB_HEAD_SHA,
    PRIOR_KH03_R1_TESTED_SHA,
    PRIOR_KH03_R2_PUB_HEAD_SHA,
    PRIOR_KH03_R2_TESTED_SHA,
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R3,
)

MANDATORY_BRANCH_KH03_R3_C1 = "feature/v17-9-2-structural-sl-tp-from-qa"
CANONICAL_DATASET_SHA256_KH03_R3_C1 = CANONICAL_DATASET_SHA256_KH02
STRICT_PREFLIGHT_DISK_MIN_GIB_C1 = 20.5
STRICT_RUNTIME_DISK_MIN_GIB_C1 = 20.0
HARD_RAM_FLOOR_MIB_C1 = 512.0
GLOBAL_MAX_DESCENDANTS_C1 = 4
MIN_EXPECTED_PYTEST_COLLECTED_C1 = 5683
MANIFEST_18_NAME_KH03_R3_C1 = "18_independent_OS_verifier_manifest_sha256.json"

PRIOR_KH03_R3_TESTED_SHA = "2301d9c648984f6b06ccb4e22f47174c3990d7af"
PRIOR_KH03_R3_EVIDENCE_SHA = "8697af2679091a7254510452d7645e22a2af5d49"
PRIOR_KH03_R3_PUB_HEAD_SHA = "b03db8642cdd9a1dede38ca5c9adad47cd1ced71"
PRIOR_KH03_R3_RUN_ID = "20261123T085134Z_2301d9c64898"
PRIOR_KH03_R3_ARTIFACT_19_REL = (
    f"aurum-desk/docs/artifacts/kh_03_r3_entry_exit_diagnosis/{PRIOR_KH03_R3_RUN_ID}/"
    "19_w1_w2_w4_w1repeat_hashes_timings_speedups.json"
)
PRIOR_KH03_R3_ARTIFACT_19_SHA256 = "85d7e5f7ed41192688c062e27114cec05e54b611f39822dd4c37496fd4471356"

PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R3_C1: Tuple[Tuple[str, str, str], ...] = (
    *PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R3,
    (
        "aurum-desk/docs/artifacts/kh_03_r3_entry_exit_diagnosis/latest_run.json",
        "ddd52e1d75469e47b14cc36a76e836f020eb05fa0e77a1958b2b4f9920737aec",
        "KH03_R3_LATEST_RUN_POINTER",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r3_entry_exit_diagnosis/{PRIOR_KH03_R3_RUN_ID}/24_independent_OS_verifier_manifest_sha256.json",
        "0edda8c11834bea2158e20dd93fcbd28a70170d36de59b6dbbcbaef8969cae97",
        "KH03_R3_SEALED_MANIFEST_24",
    ),
    (
        PRIOR_KH03_R3_ARTIFACT_19_REL,
        PRIOR_KH03_R3_ARTIFACT_19_SHA256,
        "KH03_R3_SEALED_ARTIFACT_19_BENCHMARK_RED_FIXTURE",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r3_entry_exit_diagnosis/26_phase2_evidence_publish_remote_readback_{PRIOR_KH03_R3_RUN_ID}.json",
        "6ec5c19f048165c92c1810e434689cebeda08cc5c655a3e53344a39995c4a9ff",
        "KH03_R3_SEALED_POSTPUSH_READBACK_26",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r3_entry_exit_diagnosis/{PRIOR_KH03_R3_RUN_ID}/15_fixed_fill_vs_dynamic_rerisk_and_expense_truth.json",
        "c5fef84826a52e11c695fb9f8703d264423cb7f450af903a750e55fdda2e90e4",
        "KH03_R3_SEALED_SIX_SCENARIO_15",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r3_entry_exit_diagnosis/{PRIOR_KH03_R3_RUN_ID}/16_strict_dev_gate_power_holm_cluster_bootstrap.json",
        "8bcb247342bca84d830534d1748d9946bf0c9ae94394ff8331d7b228ab17682e",
        "KH03_R3_SEALED_STRICT_DEV_GATE_16",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r3_entry_exit_diagnosis/{PRIOR_KH03_R3_RUN_ID}/17_baseline_parity_A1_SI8_adapter_D4.json",
        "cc46449889f0cc09cf33cb6cd45062c84aeff84a4f36cff5fcfcd0be3d461fca",
        "KH03_R3_SEALED_BASELINE_PARITY_17",
    ),
)

KH03_R3_C1_SOURCE_FILES_ALLOWLIST: Tuple[str, ...] = (
    "aurum-desk/backend/services/kh_03_r3_c1_worker_policy_service.py",
    "aurum-desk/tools/run_kh_03_r3_acceptance.py",
    "aurum-desk/tools/run_kh_03_r3_c1_acceptance.py",
    "aurum-desk/tools/kh_03_r3_c1_independent_verifier.py",
    "aurum-desk/backend/tests/test_kh03_r3_c1_worker_policy_schema.py",
    "aurum-desk/backend/tests/test_kh03_r3_c1_policy_safety.py",
    "aurum-desk/backend/tests/test_kh03_r3_c1_verifier_mutations.py",
    "aurum-desk/backend/tests/test_kh03_r3_c1_regression_truth.py",
)

# Sealed KH-03.R3 1x reference anchors (historical DEV truth; must not drift)
SEALED_R3_1X_ECONOMIC_ANCHORS: Dict[str, Dict[str, Any]] = {
    "NC_NULL_TRIGGER": {"closed": 203, "wins": 7, "losses": 196, "net_pnl_usd": -441.6118, "expectancy_r": -0.8806},
    "KH03_R3_E1_LOW_FRICTION_STOP_FLOOR": {"closed": 58, "wins": 3, "losses": 55, "net_pnl_usd": -118.0121, "expectancy_r": -0.8286},
    "KH03_R3_E2_CAUSAL_DELAYED_CONFIRMATION": {"closed": 131, "wins": 5, "losses": 126, "net_pnl_usd": -274.3771, "expectancy_r": -0.8498},
    "KH03_R3_E3_CAUSAL_NEAREST_M5_UNSWEPT_EXIT": {"closed": 206, "wins": 9, "losses": 197, "net_pnl_usd": -439.0423, "expectancy_r": -0.8628},
}

SEALED_BASELINE_PARITY_FINGERPRINTS: Dict[str, str] = {
    "a1_56710_ids_sha256": "dd8282ca484c5d38b2d94b6a0dd65669ad01fb1acbda2874b6ed808457da08f3",
    "si8_6375_ids_sha256": "8d3bc50b44f7da510c166ba468b6de03402739eaa2e4bcebac6b939770a0141b",
    "si0_242_episode_matched_tuples_sha256": "644addeb27aae5215079363564d6ba89a3a7d90c47a26499c595cbc501be964e",
    "e00_bundle_sha256": "30f90659430d0607180e9834d11554d664f89438389d793e4f6384f0dde12b83",
}

REQUIRED_ARTIFACTS_KH03_R3_C1: Tuple[str, ...] = (
    "00_preflight_branch_sha_resource_and_lock.json",
    "01_ast_function_ownership_and_locked_file_diffs.json",
    "02_prior_r1_r2_r3_dataset_and_sealed_sha_integrity.json",
    "03_r3_benchmark_schema_bug_real_red_reproduction.json",
    "04_strict_duration_schema_and_worker_row_falsification.json",
    "05_r3_historical_fixture_green_pytest_w4_science_w1.json",
    "06_fresh_pytest_w1_w2_w4_w1repeat_raw_receipts.json",
    "07_fresh_science_w1_w2_w4_w1repeat_raw_receipts.json",
    "08_worker_policy_measured_vs_applied_caps_and_provenance.json",
    "09_worker_policy_noise_stability_and_speedup_limits.json",
    "10_independent_benchmark_recomputation_and_claim_checks.json",
    "11_full_pytest_junit_collected_executed_bijection.json",
    "12_full_A1_SI8_adapter_D4_parity_and_fingerprints.json",
    "13_six_scenario_backtest_and_E1_E2_E3_null_economic_regression.json",
    "14_entry_exit_ledger_fee_risk_OOS_and_legacy_guard.json",
    "15_disk_ram_pid_0p5s_telemetry_and_cleanup.json",
    "16_inherited_R3_faults_plus_new_C1_red_green_controls.json",
    "17_new_verifier_mutation_resistance.json",
    "19_orthogonal_engineering_economic_resource_release_gates.json",
    "20_phase1_code_push_and_phase2_evidence_remote_readback.json",
    "README_KH03_R3_C1.md",
    "HANDOVER_KH03_R3_C1_VI.txt",
)

GATE_28_KEYS_KH03_R3_C1: Tuple[str, ...] = (
    "G01_BRANCH_CLEAN_EXECUTABLE_SHA",
    "G02_PRIOR_R1_R2_R3_AND_DATASET_IMMUTABLE",
    "G03_LOCKED_PROD_ENTRY_SL_TP_AST_PARITY",
    "G04_R3_EMPTY_MEAN_MAP_RED_REPRODUCED",
    "G05_STRICT_SCHEMA_PARSER_BOUNDARY_GREEN",
    "G06_R3_PYTEST_FIXTURE_SELECTS_W4",
    "G07_R3_SCIENCE_FIXTURE_SELECTS_W1",
    "G08_EXPLICIT_BENCHMARK_IDENTITY_FOUR_RAW_RUNS",
    "G09_FRESH_W1_W2_W4_W1REPEAT_SEMANTIC_IDENTICAL",
    "G10_W1_REPEAT_TIMING_VARIANCE_DISCLOSED",
    "G11_MEASURED_FASTEST_VS_APPLIED_SAFE_CAP_SEPARATED",
    "G12_POLICY_INDEPENDENTLY_RECOMPUTED_NONEMPTY_MEANS",
    "G13_RESOURCE_CEILINGS_DISK_RAM_WORKERS_FAIL_CLOSED",
    "G14_FULL_PYTEST_FRESH_JUNIT_1_TO_1_ZERO_MANDATORY_SKIP",
    "G15_FULL_A1_SI8_ADAPTER_D4_PARITY_ZERO_MISMATCH",
    "G16_BASELINE_1X_AND_E1_E2_E3_ECONOMIC_REGRESSION",
    "G17_SIX_COST_DYNAMIC_VS_FIXED_FILL_SCOPED",
    "G18_LEGACY_R1_GUARD_R2_FWD_ONLY_H3_PATH_B_INTACT",
    "G19_FRESH_OOS_ZERO_NO_L1_FORGERY_ACTIVATION_OFF",
    "G20_INHERITED_R3_FAULTS_RED_GREEN_VALID",
    "G21_NEW_C1_WORKER_POLICY_FAULTS_12_PLUS_RED_GREEN",
    "G22_NEW_C1_INDEPENDENT_VERIFIER_MUTATIONS_12_PLUS",
    "G23_INDEPENDENT_VERIFIER_DISTINCT_OS_PID_NO_TRUST",
    "G24_ZERO_ORPHANS_CLEAN_TEMP_ATOMIC_CHECKSUMS",
    "G25_PERFORMANCE_CLAIMS_MEASURED_SCOPE_NO_EXTRAPOLATION",
    "G26_RESEARCH_ECONOMIC_NO_PROVEN_EDGE_HONESTY",
    "G27_KH04_BLOCKED_AND_ACTIVATION_READY_NO",
    "G28_TWO_PHASE_COMMIT_PUSH_REMOTE_READBACK",
)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _strict_json_loads(raw_text: str) -> Any:
    def _reject_const(tok: str) -> Any:
        raise ValueError(f"NONSTANDARD_JSON_CONSTANT:{tok}")

    return json.loads(raw_text, parse_constant=_reject_const)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_hex64(v: Any) -> bool:
    return isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdefABCDEF" for c in v)


def _independent_recompute_raw_benchmark_policy_c1(
    raw_doc: Mapping[str, Any],
    reported_policy: Mapping[str, Any],
    *,
    kind: str,
    expected_run_id: str,
    expected_tested_sha: str,
    max_safe_cap: int,
) -> Dict[str, Any]:
    """
    COMPLETELY INDEPENDENT minimal raw-JSON benchmark parser & policy recomputer implemented
    directly inside the verifier (no delegation to orchestrator or worker_policy_service).
    Prevents common-mode failure!
    """
    viols: List[str] = []
    if kind == "pytest_controlled_subset":
        canon_dur_key = "wall_clock_seconds"
        forbidden_other = ("wall_seconds", "canonical_sorted_sha256", "rows_count")
        cnt_key = "executed_tests_count"
        sha_key = "canonical_sorted_outcome_sha256"
    elif kind == "r2_science_slice":
        canon_dur_key = "wall_seconds"
        forbidden_other = ("wall_clock_seconds", "canonical_sorted_outcome_sha256", "executed_tests_count")
        cnt_key = "rows_count"
        sha_key = "canonical_sorted_sha256"
    else:
        return {"valid": False, "violations": [f"VERIFIER_UNKNOWN_KIND:{kind}"], "means": {}, "fastest": None}

    if raw_doc.get("run_id") != expected_run_id:
        viols.append(f"BENCH_RUN_ID_MISMATCH:{kind}:got={raw_doc.get('run_id')}!=expected={expected_run_id}")
    if raw_doc.get("tested_executable_sha") != expected_tested_sha:
        viols.append(f"BENCH_TESTED_SHA_MISMATCH:{kind}:got={raw_doc.get('tested_executable_sha')}!=expected={expected_tested_sha}")
    if raw_doc.get("run_id") == PRIOR_KH03_R3_RUN_ID or raw_doc.get("tested_executable_sha") == PRIOR_KH03_R3_TESTED_SHA:
        viols.append(f"BENCH_STALE_R3_REUSED:{kind}")

    raw_runs = raw_doc.get("runs")
    if not isinstance(raw_runs, list) or len(raw_runs) == 0:
        viols.append(f"BENCH_EMPTY_RUNS:{kind}")
        return {"valid": False, "violations": viols, "means": {}, "fastest": None}

    req_workers = {"w=1": 1, "w=2": 2, "w=4": 4, "w=1_repeat": 1}
    durs: Dict[str, float] = {}
    counts: Dict[str, int] = {}
    shas: Dict[str, str] = {}

    for i, r in enumerate(raw_runs):
        if not isinstance(r, Mapping):
            viols.append(f"BENCH_ROW_NOT_OBJ:{kind}:{i}")
            continue
        for fk in forbidden_other:
            if fk in r:
                viols.append(f"BENCH_MIXED_SCHEMA_KEY:{kind}:{fk}")
        ml = r.get("mode_label")
        if ml not in req_workers:
            viols.append(f"BENCH_BAD_MODE_LABEL:{kind}:{ml!r}")
            continue
        if ml in durs:
            viols.append(f"BENCH_DUPLICATE_MODE:{kind}:{ml}")
            continue
        w = r.get("workers")
        if not _is_int(w) or int(w) != req_workers[ml]:
            viols.append(f"BENCH_WORKERS_MISMATCH:{kind}:{ml}:got={w!r}")
        if kind == "pytest_controlled_subset" and "worker_pids" in r:
            pids = r.get("worker_pids")
            if not isinstance(pids, list) or len(pids) != req_workers[ml] or len(set(pids)) != req_workers[ml] or any((not _is_int(p) or p <= 0) for p in pids):
                viols.append(f"BENCH_BAD_WORKER_PIDS:{kind}:{ml}")

        # Strict duration check + conflict check
        all_dk = [k for k in (canon_dur_key, "elapsed_sec", "wall_sec", "duration_sec") if k in r]
        if canon_dur_key not in r:
            viols.append(f"BENCH_MISSING_CANONICAL_DURATION:{kind}:{ml}:{canon_dur_key}")
            continue
        vals_present: List[float] = []
        bad_dur = False
        for dk in all_dk:
            dv = r.get(dk)
            if not _is_num(dv):
                viols.append(f"BENCH_NON_NUMERIC_DURATION:{kind}:{ml}:{dk}:{dv!r}")
                bad_dur = True
                continue
            fv = float(dv)
            if not math.isfinite(fv) or fv <= 0.0 or fv > 86_400.0:
                viols.append(f"BENCH_INVALID_DURATION_RANGE:{kind}:{ml}:{dk}:{dv!r}")
                bad_dur = True
                continue
            vals_present.append(fv)
        if not bad_dur and len(vals_present) > 1:
            if any(abs(x - vals_present[0]) > 1e-9 for x in vals_present[1:]):
                viols.append(f"BENCH_CONFLICTING_DURATION_KEYS:{kind}:{ml}")
        if not bad_dur and _is_num(r.get(canon_dur_key)):
            durs[ml] = float(r[canon_dur_key])

        cnt = r.get(cnt_key)
        if not _is_int(cnt) or int(cnt) <= 0:
            viols.append(f"BENCH_INVALID_COUNT:{kind}:{ml}:{cnt!r}")
        else:
            counts[ml] = int(cnt)
        sh = r.get(sha_key)
        if not _is_hex64(sh):
            viols.append(f"BENCH_INVALID_SHA:{kind}:{ml}:{sh!r}")
        else:
            shas[ml] = str(sh).lower()

    for m in ("w=1", "w=2", "w=4", "w=1_repeat"):
        if m not in durs:
            viols.append(f"BENCH_MISSING_MODE:{kind}:{m}")

    if len(counts) == 4 and len(set(counts.values())) != 1:
        viols.append(f"BENCH_COUNT_DRIFT:{kind}:{counts}")
    if "w=1" in shas and "w=1_repeat" in shas and shas["w=1"] != shas["w=1_repeat"]:
        viols.append(f"BENCH_W1_REPEAT_SHA_MISMATCH:{kind}")
    if len(shas) == 4 and len(set(shas.values())) != 1:
        viols.append(f"BENCH_SEMANTIC_SHA_MISMATCH:{kind}")

    if any(m not in durs for m in ("w=1", "w=2", "w=4", "w=1_repeat")):
        return {"valid": False, "violations": viols, "means": {}, "fastest": None}

    means = {m: round(durs[m], 4) for m in ("w=1", "w=2", "w=4")}
    fastest = min(("w=1", "w=2", "w=4"), key=lambda m: (round(means[m], 6), req_workers[m]))
    sp2 = round(durs["w=1"] / max(1e-9, durs["w=2"]), 3)
    sp4 = round(durs["w=1"] / max(1e-9, durs["w=4"]), 3)
    w1_rep = round(durs["w=1_repeat"], 4)
    w1_var = round(abs(durs["w=1_repeat"] - durs["w=1"]) / max(1e-9, min(durs["w=1"], durs["w=1_repeat"])), 4)

    # Compare independently recomputed policy against reported_policy
    rep_means = reported_policy.get("mean_wall_sec_by_mode")
    if not isinstance(rep_means, Mapping) or len(rep_means) == 0:
        viols.append(f"VERIFIER_EMPTY_MEAN_WALL_MAP:{kind}")
    else:
        for m in ("w=1", "w=2", "w=4"):
            gv = rep_means.get(m)
            if not _is_num(gv) or abs(float(gv) - means[m]) > 1e-3:
                viols.append(f"VERIFIER_MEAN_WALL_DRIFT:{kind}:{m}:rep={gv}!=recomp={means[m]}")

    if reported_policy.get("measured_fastest_mode") != fastest or reported_policy.get("optimum_worker_policy") != fastest:
        viols.append(
            f"VERIFIER_FASTEST_MODE_MISMATCH:{kind}:rep_fast={reported_policy.get('measured_fastest_mode')},"
            f"rep_opt={reported_policy.get('optimum_worker_policy')}!=recomp={fastest}"
        )

    rep_sp2 = reported_policy.get("measured_speedup_w2_vs_w1")
    rep_sp4 = reported_policy.get("measured_speedup_w4_vs_w1")
    if not _is_num(rep_sp2) or abs(float(rep_sp2) - sp2) > 0.01:
        viols.append(f"VERIFIER_SPEEDUP_W2_MISMATCH:{kind}:rep={rep_sp2}!=recomp={sp2}")
    if not _is_num(rep_sp4) or abs(float(rep_sp4) - sp4) > 0.01:
        viols.append(f"VERIFIER_SPEEDUP_W4_MISMATCH:{kind}:rep={rep_sp4}!=recomp={sp4}")

    raw_sp2 = raw_doc.get("measured_speedup_w2_vs_w1")
    raw_sp4 = raw_doc.get("measured_speedup_w4_vs_w1")
    if raw_sp2 is not None and (not _is_num(raw_sp2) or abs(float(raw_sp2) - sp2) > 0.01):
        viols.append(f"VERIFIER_RAW_SPEEDUP_W2_FORGED:{kind}:raw={raw_sp2}!=recomp={sp2}")
    if raw_sp4 is not None and (not _is_num(raw_sp4) or abs(float(raw_sp4) - sp4) > 0.01):
        viols.append(f"VERIFIER_RAW_SPEEDUP_W4_FORGED:{kind}:raw={raw_sp4}!=recomp={sp4}")

    rep_w1r = reported_policy.get("w1_repeat_wall_sec")
    if not _is_num(rep_w1r) or abs(float(rep_w1r) - w1_rep) > 1e-3:
        viols.append(f"VERIFIER_W1_REPEAT_WALL_MISMATCH:{kind}:rep={rep_w1r}!=recomp={w1_rep}")
    rep_var = reported_policy.get("w1_repeat_variance_pct")
    if not _is_num(rep_var) or abs(float(rep_var) - w1_var) > 1e-3:
        viols.append(f"VERIFIER_W1_REPEAT_VARIANCE_MISMATCH:{kind}:rep={rep_var}!=recomp={w1_var}")
    if bool(reported_policy.get("timing_determinism_claimed", True)):
        viols.append(f"VERIFIER_FALSE_TIMING_DETERMINISM_CLAIM:{kind}")
    if bool(reported_policy.get("full_suite_speedup_extrapolated", False)):
        viols.append(f"VERIFIER_FORBIDDEN_FULL_SUITE_EXTRAPOLATION:{kind}")

    safe_cap_rep = reported_policy.get("safe_worker_cap")
    if not _is_int(safe_cap_rep) or int(safe_cap_rep) < 1 or int(safe_cap_rep) > GLOBAL_MAX_DESCENDANTS_C1 or int(safe_cap_rep) > max_safe_cap:
        viols.append(f"VERIFIER_INVALID_OR_ABSENT_SAFE_WORKER_CAP:{kind}:cap={safe_cap_rep!r}:max_safe={max_safe_cap}")

    app_w = reported_policy.get("applied_worker_count")
    if not _is_int(app_w) or int(app_w) < 1 or int(app_w) > GLOBAL_MAX_DESCENDANTS_C1 or int(app_w) > max_safe_cap or (_is_int(safe_cap_rep) and int(app_w) > int(safe_cap_rep)):
        viols.append(f"VERIFIER_APPLIED_WORKER_CAP_EXCEEDED:{kind}:applied={app_w!r}:safe_cap={safe_cap_rep!r}:max_safe={max_safe_cap}")

    return {
        "valid": len(viols) == 0,
        "kind": kind,
        "means": means,
        "fastest": fastest,
        "speedup_w2_vs_w1": sp2,
        "speedup_w4_vs_w1": sp4,
        "w1_repeat_wall_sec": w1_rep,
        "w1_repeat_variance_pct": w1_var,
        "semantic_sha256": next(iter(shas.values())) if len(set(shas.values())) == 1 else None,
        "semantic_count": next(iter(counts.values())) if len(set(counts.values())) == 1 else 0,
        "violations": viols,
    }


def _verify_junit_raw_xml_bijection_c1(
    run_dir: Path,
    f11: Mapping[str, Any],
    *,
    expected_run_id: str,
    expected_tested_sha: str,
) -> Tuple[bool, List[str]]:
    """Independently parses every raw JUnit XML file from disk and verifies 1:1 bijection."""
    violations: List[str] = []
    cm = f11.get("collection_manifest") or {}
    sm = f11.get("shard_manifest") or {}
    if not cm or not sm:
        return False, ["JUNIT_MISSING_COLLECTION_OR_SHARD_MANIFEST"]
    if cm.get("run_id") != expected_run_id or sm.get("run_id") != expected_run_id:
        violations.append("JUNIT_MANIFEST_RUN_ID_MISMATCH")
    if cm.get("tested_executable_sha") != expected_tested_sha or sm.get("tested_executable_sha") != expected_tested_sha:
        violations.append("JUNIT_MANIFEST_TESTED_SHA_MISMATCH")
    if cm.get("run_id") == PRIOR_KH03_R3_RUN_ID or cm.get("tested_executable_sha") == PRIOR_KH03_R3_TESTED_SHA:
        violations.append("JUNIT_STALE_R3_REUSED")
    if int(sm.get("nonzero_exit_shards_count", 1)) != 0:
        violations.append(f"JUNIT_NONZERO_EXIT_SHARDS:{sm.get('nonzero_exit_shards_count')}")

    collected_ids = list(cm.get("collected_node_ids") or ())
    if len(collected_ids) < MIN_EXPECTED_PYTEST_COLLECTED_C1:
        violations.append(f"JUNIT_COLLECTED_BELOW_R3_BASELINE:{len(collected_ids)}<{MIN_EXPECTED_PYTEST_COLLECTED_C1}")
    if len(collected_ids) != int(cm.get("collected_node_ids_count", -1)):
        violations.append("JUNIT_COLLECTED_COUNT_MISMATCH")
    if len(set(collected_ids)) != len(collected_ids):
        violations.append("JUNIT_DUPLICATE_COLLECTED_NODE_IDS")

    shards = list(sm.get("shards") or ())
    if not shards:
        return False, ["JUNIT_ZERO_SHARDS"]

    scheduled_all: List[str] = []
    xml_tests_total = 0
    xml_fail_total = 0
    xml_err_total = 0
    xml_skip_total = 0
    mandatory_skipped = 0

    for sh in shards:
        if int(sh.get("exit_code", 1)) != 0 or bool(sh.get("timed_out", True)) or bool(sh.get("reused", True)):
            violations.append(f"JUNIT_SHARD_BAD_STATUS:{sh.get('shard_id')}")
        rel_xml = str(sh.get("junit_xml_path") or "")
        xp = run_dir / rel_xml
        if not xp.is_file() or xp.stat().st_size == 0:
            violations.append(f"JUNIT_XML_MISSING:{rel_xml}")
            continue
        actual_sha = _file_sha256(xp)
        exp_sha = str(sh.get("xml_sha256") or sh.get("junit_sha256") or "")
        if actual_sha != exp_sha:
            violations.append(f"JUNIT_XML_SHA256_MISMATCH:{rel_xml}")
        sched = list(sh.get("scheduled_node_ids") or ())
        scheduled_all.extend(sched)
        try:
            root = ET.fromstring(xp.read_text("utf-8"))
        except Exception as exc:
            violations.append(f"JUNIT_XML_PARSE_ERROR:{rel_xml}:{exc}")
            continue
        tcs = list(root.iter("testcase"))
        xml_tests_total += len(tcs)
        if len(tcs) != len(sched):
            violations.append(
                f"JUNIT_SHARD_TESTCASE_COUNT_MISMATCH:{sh.get('shard_id')}:xml={len(tcs)}!=sched={len(sched)}"
            )
        for tc in tcs:
            cn = str(tc.attrib.get("classname") or "")
            nm = str(tc.attrib.get("name") or "")
            if tc.find("failure") is not None:
                xml_fail_total += 1
            if tc.find("error") is not None:
                xml_err_total += 1
            if tc.find("skipped") is not None:
                xml_skip_total += 1
                if any(tok in cn or tok in nm for tok in ("test_kh_02_r2", "test_kh03_r3", "test_kh03_r3_c1")):
                    mandatory_skipped += 1

    if sorted(scheduled_all) != sorted(collected_ids):
        violations.append(f"JUNIT_BIJECTION_MISMATCH:collected={len(collected_ids)},scheduled={len(scheduled_all)}")
    xml_pass_total = xml_tests_total - xml_fail_total - xml_err_total - xml_skip_total
    if (
        xml_tests_total != int(f11.get("executed_count", -1))
        or xml_pass_total != int(f11.get("passed_count", -1))
        or xml_skip_total != int(f11.get("skipped_count", -1))
        or xml_fail_total != int(f11.get("failed_count", -1))
        or xml_err_total != int(f11.get("error_count", -1))
    ):
        violations.append(
            f"JUNIT_COUNTS_TAMPERED_VS_RAW_XML:xml=({xml_tests_total},{xml_pass_total},{xml_skip_total},{xml_fail_total},{xml_err_total})"
        )
    if xml_fail_total != 0 or xml_err_total != 0 or mandatory_skipped != 0:
        violations.append(
            f"JUNIT_FAILURES_OR_MANDATORY_SKIPS:fail={xml_fail_total},err={xml_err_total},mandatory_skip={mandatory_skipped}"
        )
    return len(violations) == 0, violations


def _audit_memory_and_disk_samples_c1(mem_gz_path: Path, f15: Mapping[str, Any]) -> Dict[str, Any]:
    """Independently parses logs/runtime_memory_samples_0p5s.jsonl.gz and reconciles against artifact 15."""
    viols: List[str] = []
    if not mem_gz_path.is_file() or mem_gz_path.stat().st_size == 0:
        return {
            "status": "FAIL_MISSING_LOG",
            "samples_count": 0,
            "below_floor_samples_count": 1,
            "max_workers_observed_in_samples": 99,
            "min_available_ram_mib": 0.0,
            "min_disk_free_gib": 0.0,
            "violations": ["MISSING_MEMORY_TELEMETRY_LOG"],
        }

    samples_n = 0
    min_ram = float("inf")
    min_disk = float("inf")
    max_children = 0
    below_ram = 0
    below_disk = 0

    try:
        with gzip.open(mem_gz_path, "rt", encoding="utf-8") as fh:
            for ln in fh:
                ln_s = ln.strip()
                if not ln_s:
                    continue
                row = _strict_json_loads(ln_s)
                samples_n += 1
                ram_mib = row.get("available_mib")
                if ram_mib is None and row.get("available_ram_bytes") is not None:
                    ram_mib = float(row["available_ram_bytes"]) / (1024.0 * 1024.0)
                if _is_num(ram_mib):
                    rf = float(ram_mib)
                    min_ram = min(min_ram, rf)
                    if rf < HARD_RAM_FLOOR_MIB_C1:
                        below_ram += 1
                dk_gib = row.get("disk_free_gib")
                if _is_num(dk_gib):
                    df = float(dk_gib)
                    min_disk = min(min_disk, df)
                    if df < STRICT_RUNTIME_DISK_MIN_GIB_C1:
                        below_disk += 1
                ch = row.get("child_count")
                if ch is None and isinstance(row.get("worker_pids"), list):
                    ch = len(row["worker_pids"])
                if _is_int(ch):
                    max_children = max(max_children, int(ch))
    except Exception as exc:
        return {
            "status": "FAIL_PARSE_ERROR",
            "samples_count": samples_n,
            "below_floor_samples_count": 1,
            "max_workers_observed_in_samples": 99,
            "min_available_ram_mib": 0.0,
            "min_disk_free_gib": 0.0,
            "violations": [f"TELEMETRY_GZ_PARSE_ERROR:{exc}"],
        }

    if samples_n == 0:
        viols.append("TELEMETRY_GZ_ZERO_SAMPLES")
    if min_ram == float("inf"):
        min_ram = 0.0
        viols.append("TELEMETRY_MISSING_RAM_SAMPLES")
    if min_disk == float("inf"):
        min_disk = 0.0
        viols.append("TELEMETRY_MISSING_DISK_SAMPLES")
    if below_ram > 0 or min_ram < HARD_RAM_FLOOR_MIB_C1:
        viols.append(f"TELEMETRY_RAM_BELOW_512MIB:min={min_ram:.2f},below_n={below_ram}")
    if below_disk > 0 or min_disk < STRICT_RUNTIME_DISK_MIN_GIB_C1:
        viols.append(f"TELEMETRY_DISK_BELOW_20GIB:min={min_disk:.4f},below_n={below_disk}")
    if max_children > GLOBAL_MAX_DESCENDANTS_C1:
        viols.append(f"TELEMETRY_CHILD_COUNT_EXCEEDED_4:max={max_children}")

    # Reconcile artifact 15 against raw .jsonl.gz samples (catches P23!)
    rep_ram = f15.get("min_available_ram_mib")
    rep_disk = f15.get("min_disk_free_gib")
    rep_ch = f15.get("max_descendant_children")
    if not _is_num(rep_ram) or abs(float(rep_ram) - min_ram) > 1.0:
        viols.append(f"TELEMETRY_15_RAM_MUTATED_VS_GZ:rep={rep_ram}!=gz={min_ram:.2f}")
    if not _is_num(rep_disk) or abs(float(rep_disk) - min_disk) > 0.05:
        viols.append(f"TELEMETRY_15_DISK_MUTATED_VS_GZ:rep={rep_disk}!=gz={min_disk:.4f}")
    if not _is_int(rep_ch) or int(rep_ch) < max_children or int(rep_ch) > GLOBAL_MAX_DESCENDANTS_C1:
        viols.append(f"TELEMETRY_15_CHILD_COUNT_MUTATED_VS_GZ:rep={rep_ch},gz_max={max_children}")

    return {
        "status": "PASS" if len(viols) == 0 else "FAIL",
        "samples_count": samples_n,
        "min_available_ram_mib": round(min_ram, 2),
        "min_disk_free_gib": round(min_disk, 4),
        "max_workers_observed_in_samples": max_children,
        "below_floor_samples_count": below_ram + below_disk,
        "violations": viols,
    }


def _audit_economic_and_six_scenario_regression_c1(
    f13: Mapping[str, Any], f14: Mapping[str, Any]
) -> Tuple[bool, List[str]]:
    """Independently audits 6-scenario fee math, R3 historical anchors, OOS=0, H3 advisory, and safety modes."""
    viols: List[str] = []

    dyn = f13.get("dynamic_full_rerisk_six_scenarios") or {}
    fix = f13.get("fixed_fills_descriptive_repricing") or {}
    if len(dyn) != 6:
        viols.append(f"ECONOMIC_SIX_SCENARIOS_COUNT_INVALID:{len(dyn)}")
    if len(fix) < 1:
        viols.append("ECONOMIC_FIXED_FILLS_DIAGNOSTIC_MISSING")
    if bool(f13.get("dynamic_vs_fixed_fill_populations_separated", False)) is not True:
        viols.append("ECONOMIC_DYNAMIC_VS_FIXED_SCOPE_NOT_SEPARATED")

    for sc_id, sc_obj in dyn.items():
        arms_m = (sc_obj or {}).get("arms") or {}
        for arm_c, rs in arms_m.items():
            closed_n = int((rs or {}).get("closed") or 0)
            if closed_n > 0:
                g = float(rs.get("gross_pnl_usd") or 0.0)
                fe = float(rs.get("total_fees_usd") or 0.0)
                ne = float(rs.get("net_pnl_usd") or 0.0)
                if fe <= 0.0 and (g - ne) > 0.01:
                    viols.append(f"ECONOMIC_ZERO_FEE_BUG:{sc_id}:{arm_c}")
                if abs((g - fe) - ne) > 0.01:
                    viols.append(f"ECONOMIC_GROSS_FEE_NET_DRIFT:{sc_id}:{arm_c}")

    # Verify exact match to sealed KH-03.R3 1x reference numbers (scientific non-drift check!)
    base_1x = (dyn.get("1X_BASE_0p0004") or {}).get("arms") or {}
    for arm_c, exp in SEALED_R3_1X_ECONOMIC_ANCHORS.items():
        got = base_1x.get(arm_c) or {}
        if int(got.get("closed", -1)) != exp["closed"] or int(got.get("wins", -1)) != exp["wins"] or int(got.get("losses", -1)) != exp["losses"]:
            viols.append(f"ECONOMIC_1X_COUNT_DRIFT_VS_R3:{arm_c}:got={got}")
        if abs(float(got.get("net_pnl_usd", 9999.0)) - exp["net_pnl_usd"]) > 0.01:
            viols.append(f"ECONOMIC_1X_NET_PNL_DRIFT_VS_R3:{arm_c}:got={got.get('net_pnl_usd')}!=exp={exp['net_pnl_usd']}")
        if abs(float(got.get("expectancy_r", 9999.0)) - exp["expectancy_r"]) > 0.001:
            viols.append(f"ECONOMIC_1X_EXPECTANCY_DRIFT_VS_R3:{arm_c}:got={got.get('expectancy_r')}!=exp={exp['expectancy_r']}")

    if f13.get("RESEARCH_ECONOMIC_VERDICT") != "NO_PROVEN_EDGE":
        viols.append(f"FORGED_RESEARCH_ECONOMIC_VERDICT:{f13.get('RESEARCH_ECONOMIC_VERDICT')}")
    if f13.get("DEV_RESEARCH_CANDIDATE") != "NO_ECONOMIC_ADVANCEMENT_CANDIDATE":
        viols.append(f"FORGED_DEV_RESEARCH_CANDIDATE:{f13.get('DEV_RESEARCH_CANDIDATE')}")
    if list(f13.get("passing_dev_candidate_arms") or []) != []:
        viols.append(f"FORGED_PASSING_DEV_ARMS:{f13.get('passing_dev_candidate_arms')}")
    if int(f13.get("OOS_EVALUATIONS", -1)) != 0 or int(f14.get("OOS_EVALUATIONS", -1)) != 0:
        viols.append("FORGED_NONZERO_OOS_EVALUATIONS")
    if f13.get("observed_l1_bbo_available") is not False or f14.get("observed_l1_bbo_available") is not False:
        viols.append("FORGED_OBSERVED_L1_BBO_AVAILABLE")
    if str(f14.get("H3_RESIZE_EXECUTION_STATUS") or "") == "PASS" or bool(f14.get("h3_promoted_as_executable", True)):
        viols.append("FORGED_H3_RESIZE_EXECUTION_PASS")
    if f14.get("ACCOUNT_EFFECTIVE_FEE_VERDICT") != "UNKNOWN_NOT_LOCALLY_VERIFIED":
        viols.append(f"FORGED_ACCOUNT_FEE_VERDICT:{f14.get('ACCOUNT_EFFECTIVE_FEE_VERDICT')}")
    if f14.get("planned_cap_is_physical_guarantee") is not False:
        viols.append("FORGED_PLANNED_CAP_PHYSICAL_GUARANTEE")
    if f14.get("LEGACY_METHOD_GUARD") != "PASS" or int(f14.get("unauthorized_r1_callers_count", 1)) != 0:
        viols.append("LEGACY_R1_METHOD_GUARD_FAILED")

    return len(viols) == 0, viols


def recompute_kh03_r3_c1_28_gates_and_verdicts(
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
    """Independently recomputes ALL 28 orthogonal KH-03.R3.C1 gates (G01..G28) and verdicts."""
    f00, f01, f02, f03, f04, f05 = docs["00"], docs["01"], docs["02"], docs["03"], docs["04"], docs["05"]
    f06, f07, f08, f09, f10, f11 = docs["06"], docs["07"], docs["08"], docs["09"], docs["10"], docs["11"]
    f12, f13, f14, f15, f16, f17, f20 = docs["12"], docs["13"], docs["14"], docs["15"], docs["16"], docs["17"], docs["20"]

    gates: Dict[str, str] = {}

    # G01: Branch / clean tree / fresh unique tested SHA
    tsha = str(f00.get("tested_executable_sha") or "")
    stale_shas = {
        PRIOR_KH02_R1_TESTED_SHA_SHARED, PRIOR_KH02_R2_TESTED_SHA_SHARED,
        PRIOR_KH02_R2_C1_CODE_COMMIT_SHA_SHARED, PRIOR_KH02_R2_C2_TESTED_SHA_SHARED,
        PRIOR_KH02_R2_C3_TESTED_SHA, PRIOR_KH02_R2_C4_TESTED_SHA, PRIOR_KH02_R2_C4_PUB_HEAD_SHA,
        PRIOR_KH03_R1_TESTED_SHA, PRIOR_KH03_R1_PUB_HEAD_SHA,
        PRIOR_KH03_R2_TESTED_SHA, PRIOR_KH03_R2_PUB_HEAD_SHA,
        PRIOR_KH03_R3_TESTED_SHA, PRIOR_KH03_R3_EVIDENCE_SHA, PRIOR_KH03_R3_PUB_HEAD_SHA,
    }
    sha_ok = (
        f00.get("branch") == MANDATORY_BRANCH_KH03_R3_C1
        and len(tsha) == 40
        and tsha == f00.get("start_head_sha")
        and tsha == f00.get("end_head_sha")
        and tsha not in stale_shas
        and str(f00.get("run_id") or "") != PRIOR_KH03_R3_RUN_ID
    )
    clean_ok = bool(f00.get("git_clean_at_start", False)) and not bool(f00.get("local_uncommitted_changes", False))
    gates["G01_BRANCH_CLEAN_EXECUTABLE_SHA"] = (
        "PASS" if (sha_ok and clean_ok) else ("BLOCKED_UNCOMMITTED_LOCAL_RUN" if (sha_ok and not clean_ok) else "FAIL")
    )

    # G02: Old R1/R2/R3 sealed artifacts + canonical dataset unchanged
    gates["G02_PRIOR_R1_R2_R3_AND_DATASET_IMMUTABLE"] = (
        "PASS"
        if (
            f02.get("status") == "PASS"
            and bool(f02.get("canonical_dataset_sha256_match"))
            and bool(f02.get("all_prior_sealed_artifacts_intact"))
        )
        else "FAIL"
    )

    # G03: Locked production / Entry / SL / TP / R2 / R3 research AST parity
    gates["G03_LOCKED_PROD_ENTRY_SL_TP_AST_PARITY"] = (
        "PASS"
        if (
            f01.get("status") == "PASS"
            and int(f01.get("locked_files_modified_count", 1)) == 0
            and int(f01.get("unauthorized_changed_files_count", 1)) == 0
            and len(f01.get("forbidden_order_import_hits") or []) == 0
            and bool(f01.get("cost_assumptions_defaults_intact"))
        )
        else "FAIL"
    )

    # G04: Historical mean_wall_sec_by_mode={} false-default RED reproduced on immutable R3 artifact 19
    p_red = f03.get("old_buggy_pytest_policy") or {}
    s_red = f03.get("old_buggy_science_policy") or {}
    gates["G04_R3_EMPTY_MEAN_MAP_RED_REPRODUCED"] = (
        "PASS"
        if (
            f03.get("status") == "PASS"
            and f03.get("classification") == "REAL_RED_REPRODUCED"
            and f03.get("source_fixture_sha256") == PRIOR_KH03_R3_ARTIFACT_19_SHA256
            and p_red.get("mean_wall_sec_by_mode") == {}
            and p_red.get("optimum_worker_policy") == "w=1"
            and s_red.get("mean_wall_sec_by_mode") == {}
            and s_red.get("optimum_worker_policy") == "w=1"
        )
        else "FAIL"
    )

    # G05: Strict source schema parser boundary tests GREEN
    gates["G05_STRICT_SCHEMA_PARSER_BOUNDARY_GREEN"] = (
        "PASS"
        if (
            f04.get("status") == "PASS"
            and int(f04.get("cases_passed", 0)) >= 14
            and int(f04.get("cases_failed", 1)) == 0
        )
        else "FAIL"
    )

    # G06 & G07: Old immutable R3 fixture GREEN -> pytest selects W4, science selects W1
    p_grn = f05.get("pytest_fixture_green_policy") or {}
    s_grn = f05.get("science_fixture_green_policy") or {}
    p_grn_m = p_grn.get("mean_wall_sec_by_mode") or {}
    s_grn_m = s_grn.get("mean_wall_sec_by_mode") or {}
    gates["G06_R3_PYTEST_FIXTURE_SELECTS_W4"] = (
        "PASS"
        if (
            f05.get("status") == "PASS"
            and f05.get("source_fixture_sha256") == PRIOR_KH03_R3_ARTIFACT_19_SHA256
            and p_grn.get("provenance_role") == "HISTORICAL_REFERENCE_ONLY"
            and p_grn.get("measured_fastest_mode") == "w=4"
            and abs(float(p_grn_m.get("w=1", 0.0)) - 51.698) <= 0.001
            and abs(float(p_grn_m.get("w=2", 0.0)) - 41.123) <= 0.001
            and abs(float(p_grn_m.get("w=4", 0.0)) - 32.947) <= 0.001
        )
        else "FAIL"
    )
    gates["G07_R3_SCIENCE_FIXTURE_SELECTS_W1"] = (
        "PASS"
        if (
            f05.get("status") == "PASS"
            and f05.get("source_fixture_sha256") == PRIOR_KH03_R3_ARTIFACT_19_SHA256
            and s_grn.get("provenance_role") == "HISTORICAL_REFERENCE_ONLY"
            and s_grn.get("measured_fastest_mode") == "w=1"
            and abs(float(s_grn_m.get("w=1", 0.0)) - 2.069) <= 0.001
            and abs(float(s_grn_m.get("w=2", 0.0)) - 2.181) <= 0.001
            and abs(float(s_grn_m.get("w=4", 0.0)) - 2.277) <= 0.001
        )
        else "FAIL"
    )

    # G13: Disk / RAM / Worker count ceilings fail closed (evaluate before heavy gates)
    mp00 = f00.get("strict_multi_mount_probe") or {}
    waiver = (
        bool(f00.get("owner_disk_preflight_waiver", False))
        or bool(mp00.get("owner_waiver_permitted", False))
        or bool(f15.get("owner_disk_preflight_waiver", False))
        or bool(f20.get("owner_disk_preflight_waiver", False))
    )
    mounts = list(mp00.get("distinct_mounts") or ())
    probe_errs = list(mp00.get("probe_errors") or ())
    pre_disk_ok = (
        len(mounts) > 0
        and len(probe_errs) == 0
        and all(
            float(m.get("free_gib", 0.0)) >= STRICT_PREFLIGHT_DISK_MIN_GIB_C1
            and float(m.get("projected_post_reserve_gib", 0.0)) >= STRICT_RUNTIME_DISK_MIN_GIB_C1
            for m in mounts
        )
        and bool(mp00.get("can_run_heavy_stages", False))
    )
    rt_disk_ok = float(f15.get("min_disk_free_gib") or 0.0) >= STRICT_RUNTIME_DISK_MIN_GIB_C1
    ram_pid_ok = (
        float(f15.get("min_available_ram_mib") or 0.0) >= HARD_RAM_FLOOR_MIB_C1
        and int(f15.get("max_descendant_children", 99)) <= GLOBAL_MAX_DESCENDANTS_C1
        and mem_audit.get("status") == "PASS"
        and int(mem_audit.get("below_floor_samples_count", 1)) == 0
        and int(mem_audit.get("max_workers_observed_in_samples", 99)) <= GLOBAL_MAX_DESCENDANTS_C1
    )
    if waiver or len(probe_errs) > 0 or not ram_pid_ok:
        gates["G13_RESOURCE_CEILINGS_DISK_RAM_WORKERS_FAIL_CLOSED"] = "FAIL"
    elif pre_disk_ok and rt_disk_ok:
        gates["G13_RESOURCE_CEILINGS_DISK_RAM_WORKERS_FAIL_CLOSED"] = "PASS"
    elif pre_disk_ok and not rt_disk_ok:
        gates["G13_RESOURCE_CEILINGS_DISK_RAM_WORKERS_FAIL_CLOSED"] = "FAIL"
    else:
        gates["G13_RESOURCE_CEILINGS_DISK_RAM_WORKERS_FAIL_CLOSED"] = "BLOCKED_LOW_DISK"

    res_pass = gates["G13_RESOURCE_CEILINGS_DISK_RAM_WORKERS_FAIL_CLOSED"] == "PASS"
    low_disk = gates["G13_RESOURCE_CEILINGS_DISK_RAM_WORKERS_FAIL_CLOSED"] == "BLOCKED_LOW_DISK"

    # G08..G12: Fresh W1/W2/W4/W1_repeat benchmarks, noise disclosure, cap separation, independent recompute
    if f06.get("status") == "NOT_RUN_BLOCKED_LOW_DISK" or f07.get("status") == "NOT_RUN_BLOCKED_LOW_DISK":
        st_b = "NOT_RUN_BLOCKED_LOW_DISK" if low_disk else "FAIL"
        for gk in (
            "G08_EXPLICIT_BENCHMARK_IDENTITY_FOUR_RAW_RUNS",
            "G09_FRESH_W1_W2_W4_W1REPEAT_SEMANTIC_IDENTICAL",
            "G10_W1_REPEAT_TIMING_VARIANCE_DISCLOSED",
            "G11_MEASURED_FASTEST_VS_APPLIED_SAFE_CAP_SEPARATED",
            "G12_POLICY_INDEPENDENTLY_RECOMPUTED_NONEMPTY_MEANS",
        ):
            gates[gk] = st_b
    else:
        both_recomp_ok = bool(pytest_recomp.get("valid")) and bool(science_recomp.get("valid"))
        p_pol = f08.get("pytest_worker_policy") or {}
        s_pol = f08.get("science_worker_policy") or {}
        gates["G08_EXPLICIT_BENCHMARK_IDENTITY_FOUR_RAW_RUNS"] = (
            "PASS"
            if (
                res_pass
                and both_recomp_ok
                and f06.get("benchmark_kind") == "pytest_controlled_subset"
                and f07.get("benchmark_kind") == "r2_science_slice"
                and len(f06.get("runs") or []) == 4
                and len(f07.get("runs") or []) == 4
            )
            else "FAIL"
        )
        gates["G09_FRESH_W1_W2_W4_W1REPEAT_SEMANTIC_IDENTICAL"] = (
            "PASS"
            if (
                res_pass
                and both_recomp_ok
                and bool(f06.get("all_4_runs_identical"))
                and bool(f07.get("all_4_runs_identical"))
                and bool(pytest_recomp.get("semantic_sha256"))
                and bool(science_recomp.get("semantic_sha256"))
            )
            else "FAIL"
        )
        gates["G10_W1_REPEAT_TIMING_VARIANCE_DISCLOSED"] = (
            "PASS"
            if (
                res_pass
                and both_recomp_ok
                and f09.get("status") == "PASS"
                and f09.get("timing_determinism_claimed") is False
                and _is_num((f09.get("pytest_stability") or {}).get("w1_repeat_variance_pct"))
                and _is_num((f09.get("science_stability") or {}).get("w1_repeat_variance_pct"))
            )
            else "FAIL"
        )
        gates["G11_MEASURED_FASTEST_VS_APPLIED_SAFE_CAP_SEPARATED"] = (
            "PASS"
            if (
                res_pass
                and both_recomp_ok
                and f08.get("status") == "PASS"
                and int(p_pol.get("applied_worker_count", 99)) <= int(p_pol.get("safe_worker_cap", 0))
                and int(p_pol.get("applied_worker_count", 99)) <= 3
                and int(s_pol.get("applied_worker_count", 99)) <= int(s_pol.get("safe_worker_cap", 0))
                and int(s_pol.get("applied_worker_count", 99)) <= GLOBAL_MAX_DESCENDANTS_C1
                and p_pol.get("full_suite_speedup_extrapolated") is False
                and s_pol.get("full_suite_speedup_extrapolated") is False
            )
            else "FAIL"
        )
        gates["G12_POLICY_INDEPENDENTLY_RECOMPUTED_NONEMPTY_MEANS"] = (
            "PASS"
            if (
                res_pass
                and both_recomp_ok
                and f10.get("status") == "PASS"
                and len(p_pol.get("mean_wall_sec_by_mode") or {}) == 3
                and len(s_pol.get("mean_wall_sec_by_mode") or {}) == 3
            )
            else "FAIL"
        )

    # G14: Full pytest fresh JUnit 1:1 bijection
    r11 = str(f11.get("status") or "FAIL")
    if r11 == "NOT_RUN_BLOCKED_LOW_DISK":
        gates["G14_FULL_PYTEST_FRESH_JUNIT_1_TO_1_ZERO_MANDATORY_SKIP"] = "NOT_RUN_BLOCKED_LOW_DISK" if low_disk else "FAIL"
    elif (
        r11 == "PASS"
        and res_pass
        and junit_bijection_ok
        and int(f11.get("collected_count", 0)) >= MIN_EXPECTED_PYTEST_COLLECTED_C1
        and int(f11.get("collected_count", -1)) == int(f11.get("executed_count", -2))
        and int(f11.get("failed_count", 1)) == 0
        and int(f11.get("error_count", 1)) == 0
        and int(f11.get("mandatory_skipped_count", 1)) == 0
    ):
        gates["G14_FULL_PYTEST_FRESH_JUNIT_1_TO_1_ZERO_MANDATORY_SKIP"] = "PASS"
    else:
        gates["G14_FULL_PYTEST_FRESH_JUNIT_1_TO_1_ZERO_MANDATORY_SKIP"] = "FAIL"

    # G15: Full A1/SI8/adapter/D4 parity
    p_st = str(f12.get("BASELINE_PARITY_VERDICT") or "FAIL")
    fp12 = f12.get("per_decision_and_episode_fingerprints") or {}
    fp_exact = all(fp12.get(k) == v for k, v in SEALED_BASELINE_PARITY_FINGERPRINTS.items())
    if p_st == "NOT_RUN_BLOCKED_LOW_DISK":
        gates["G15_FULL_A1_SI8_ADAPTER_D4_PARITY_ZERO_MISMATCH"] = "NOT_RUN_BLOCKED_LOW_DISK" if low_disk else "FAIL"
    elif (
        p_st == "PASS"
        and res_pass
        and bool(fp12.get("all_sealed_fingerprints_exact_match"))
        and fp_exact
        and int(f12.get("a1_population_count", 0)) == 56710
        and int(f12.get("a1_mismatches_count", 1)) == 0
        and int(f12.get("si8_population_count", 0)) == 6375
        and int(f12.get("si8_mismatches_count", 1)) == 0
        and int(f12.get("three_adapter_population_count", 0)) == 246
        and int(f12.get("three_adapter_mismatches_count", 1)) == 0
        and int(f12.get("d4_historical_closed_trades", 1)) == 0
    ):
        gates["G15_FULL_A1_SI8_ADAPTER_D4_PARITY_ZERO_MISMATCH"] = "PASS"
    else:
        gates["G15_FULL_A1_SI8_ADAPTER_D4_PARITY_ZERO_MISMATCH"] = "FAIL"

    # G16..G19: Economic regression, 6-cost scope, legacy guard & H3 PATH B, OOS=0 & activation OFF
    gates["G16_BASELINE_1X_AND_E1_E2_E3_ECONOMIC_REGRESSION"] = (
        "PASS" if (economic_ok and f13.get("status") == "PASS" and bool(f13.get("historical_r3_1x_anchors_exact_match"))) else "FAIL"
    )
    gates["G17_SIX_COST_DYNAMIC_VS_FIXED_FILL_SCOPED"] = (
        "PASS"
        if (
            economic_ok
            and len(f13.get("dynamic_full_rerisk_six_scenarios") or {}) == 6
            and len(f13.get("fixed_fills_descriptive_repricing") or {}) >= 1
            and bool(f13.get("dynamic_vs_fixed_fill_populations_separated"))
            and int(f13.get("zero_fee_display_bugs_count", 1)) == 0
        )
        else "FAIL"
    )
    gates["G18_LEGACY_R1_GUARD_R2_FWD_ONLY_H3_PATH_B_INTACT"] = (
        "PASS"
        if (
            economic_ok
            and f14.get("status") == "PASS"
            and f14.get("LEGACY_METHOD_GUARD") == "PASS"
            and int(f14.get("unauthorized_r1_callers_count", 1)) == 0
            and bool(f14.get("r2_forward_only_embargo_240m_verified"))
            and bool(f14.get("h3_r2_excluded_from_executable_arms"))
            and str(f14.get("H3_RESIZE_EXECUTION_STATUS") or "") != "PASS"
        )
        else "FAIL"
    )

    prov = f02.get("c4_provenance_and_oos_boundary") or {}
    modes_ok = all(
        d.get("ACTIVATION_READY") == "NO"
        and all((d.get("runtime_safety_modes") or {}).get(k) == "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO"))
        for d in (f00, f01, f13, f14)
    )
    oos_ok = (
        int(f13.get("OOS_EVALUATIONS", -1)) == 0
        and int(f14.get("OOS_EVALUATIONS", -1)) == 0
        and f13.get("FRESH_OOS") == "BLOCKED_NO_UNTOUCHED_DATA"
        and f13.get("observed_l1_bbo_available") is False
        and f14.get("observed_l1_bbo_available") is False
        and prov.get("network_capture_enabled") is False
        and prov.get("live_network_capture_executed") is False
        and int(prov.get("real_oos_escrow_final_record_count", -1)) == 0
    )
    gates["G19_FRESH_OOS_ZERO_NO_L1_FORGERY_ACTIVATION_OFF"] = "PASS" if (modes_ok and oos_ok) else "FAIL"

    # G20 & G21: Faults (194 inherited R3 + >= 12 NEW C1)
    gates["G20_INHERITED_R3_FAULTS_RED_GREEN_VALID"] = (
        "PASS"
        if (
            f16.get("status") == "PASS"
            and int(f16.get("inherited_r3_controls_count", 0)) >= 194
            and int(f16.get("inherited_r3_failed_controls", 1)) == 0
        )
        else "FAIL"
    )
    gates["G21_NEW_C1_WORKER_POLICY_FAULTS_12_PLUS_RED_GREEN"] = (
        "PASS"
        if (
            f16.get("status") == "PASS"
            and int(f16.get("new_c1_controls_count", 0)) >= 12
            and int(f16.get("new_c1_failed_controls", 1)) == 0
            and int(f16.get("unique_fault_ids", 0)) == int(f16.get("total_controls", -1))
            and int(f16.get("failed_controls", 1)) == 0
        )
        else "FAIL"
    )

    # G22 & G23: Mutations (>= 12 NEW C1 + 16 inherited R3) & Separate-OS verifier
    g22_ok = (
        f17.get("status") == "PASS"
        and bool(f17.get("clean_clone_verifier_green"))
        and int(f17.get("new_c1_mutations_tested_count", 0)) >= 12
        and int(f17.get("new_c1_mutations_failed_detection_count", 1)) == 0
        and int(f17.get("inherited_r3_mutations_tested_count", 0)) >= 16
        and int(f17.get("inherited_r3_mutations_failed_detection_count", 1)) == 0
    )
    gates["G22_NEW_C1_INDEPENDENT_VERIFIER_MUTATIONS_12_PLUS"] = "PASS" if g22_ok else "FAIL"
    gates["G23_INDEPENDENT_VERIFIER_DISTINCT_OS_PID_NO_TRUST"] = (
        ("PASS" if (verifier_violations_count == 0 and g22_ok) else "FAIL")
        if for_final_verifier_manifest
        else "PENDING_SEPARATE_OS_VERIFIER"
    )

    # G24: Zero orphans, clean owned temp, atomic checksums
    gates["G24_ZERO_ORPHANS_CLEAN_TEMP_ATOMIC_CHECKSUMS"] = (
        "PASS"
        if (
            int(f15.get("orphan_children_after_cleanup", 99)) == 0
            and bool(f15.get("sentinel_temp_cleaned_up", False))
            and bool(f15.get("atomic_checksums_verified", False))
            and res_pass
        )
        else "FAIL"
    )

    # G25: Performance claims strictly within measured scope, zero full-suite extrapolation
    gates["G25_PERFORMANCE_CLAIMS_MEASURED_SCOPE_NO_EXTRAPOLATION"] = (
        "PASS"
        if (
            f09.get("status") == "PASS"
            and f10.get("status") == "PASS"
            and f09.get("full_suite_speedup_extrapolated") is False
            and f10.get("unmeasured_full_suite_speedup_claimed") is False
            and gates["G11_MEASURED_FASTEST_VS_APPLIED_SAFE_CAP_SEPARATED"] == "PASS"
            and gates["G12_POLICY_INDEPENDENTLY_RECOMPUTED_NONEMPTY_MEANS"] == "PASS"
        )
        else ("NOT_RUN_BLOCKED_LOW_DISK" if low_disk else "FAIL")
    )

    # G26 & G27: Honest orthogonal non-PASS economic/KH04/activation states
    gates["G26_RESEARCH_ECONOMIC_NO_PROVEN_EDGE_HONESTY"] = (
        "NO_PROVEN_EDGE_NO_DEV_CANDIDATE" if economic_ok else "FAIL"
    )
    gates["G27_KH04_BLOCKED_AND_ACTIVATION_READY_NO"] = (
        "BLOCKED_NO_DEV_CANDIDATE" if (modes_ok and oos_ok and economic_ok) else "FAIL"
    )

    # G28: Two-phase git commit/push + remote SHA-256 readback
    pub_st = str(f20.get("publication_status") or "NOT_PUBLISHED_BLOCKED_LOW_DISK")
    if waiver or "WAIVER" in pub_st:
        gates["G28_TWO_PHASE_COMMIT_PUSH_REMOTE_READBACK"] = "FAIL"
    elif pub_st == "PASS_REMOTE_VERIFIED" and bool(f20.get("remote_readback_sha256_verified")) and len(str(f20.get("evidence_commit_sha") or "")) == 40:
        gates["G28_TWO_PHASE_COMMIT_PUSH_REMOTE_READBACK"] = "PASS_REMOTE_VERIFIED"
    elif pub_st == "PRE_PUSH_PENDING":
        gates["G28_TWO_PHASE_COMMIT_PUSH_REMOTE_READBACK"] = "PRE_PUSH_PENDING"
    elif "BLOCKED" in pub_st:
        gates["G28_TWO_PHASE_COMMIT_PUSH_REMOTE_READBACK"] = pub_st
    else:
        gates["G28_TWO_PHASE_COMMIT_PUSH_REMOTE_READBACK"] = "FAIL"

    eng_prefixes = [f"G{i:02d}_" for i in range(1, 26) if i != 23]
    eng_keys = [k for k in GATE_28_KEYS_KH03_R3_C1 if any(k.startswith(p) for p in eng_prefixes)]
    any_fail = any(gates[k] == "FAIL" for k in gates) or (for_final_verifier_manifest and verifier_violations_count > 0)
    any_low_disk = any("LOW_DISK" in gates[k] for k in eng_keys)
    eng_pass = all(gates[k] == "PASS" for k in eng_keys) and (
        (not for_final_verifier_manifest) or gates["G23_INDEPENDENT_VERIFIER_DISTINCT_OS_PID_NO_TRUST"] == "PASS"
    )

    if any_fail:
        eng_v = "FAIL"
    elif any_low_disk:
        eng_v = "BLOCKED_RESOURCE"
    elif eng_pass:
        eng_v = "PASS"
    else:
        eng_v = "BLOCKED_INTEGRITY"

    pol_ok = all(
        gates[k] == "PASS"
        for k in (
            "G04_R3_EMPTY_MEAN_MAP_RED_REPRODUCED",
            "G05_STRICT_SCHEMA_PARSER_BOUNDARY_GREEN",
            "G06_R3_PYTEST_FIXTURE_SELECTS_W4",
            "G07_R3_SCIENCE_FIXTURE_SELECTS_W1",
            "G08_EXPLICIT_BENCHMARK_IDENTITY_FOUR_RAW_RUNS",
            "G10_W1_REPEAT_TIMING_VARIANCE_DISCLOSED",
            "G11_MEASURED_FASTEST_VS_APPLIED_SAFE_CAP_SEPARATED",
            "G12_POLICY_INDEPENDENTLY_RECOMPUTED_NONEMPTY_MEANS",
            "G25_PERFORMANCE_CLAIMS_MEASURED_SCOPE_NO_EXTRAPOLATION",
        )
    )
    det_ok = gates["G09_FRESH_W1_W2_W4_W1REPEAT_SEMANTIC_IDENTICAL"] == "PASS"
    res_v = "PASS" if (gates["G13_RESOURCE_CEILINGS_DISK_RAM_WORKERS_FAIL_CLOSED"] == "PASS" and gates["G24_ZERO_ORPHANS_CLEAN_TEMP_ATOMIC_CHECKSUMS"] == "PASS") else ("BLOCKED_LOW_DISK" if low_disk else "FAIL")

    if eng_v == "PASS" and economic_ok and oos_ok and modes_ok:
        overall = "PASS_KH03_R3_C1_ENGINEERING_POLICY_CORRECT_NO_PROVEN_EDGE_OOS_BLOCKED"
    elif eng_v == "BLOCKED_RESOURCE":
        overall = "BLOCKED_LOW_DISK"
    else:
        overall = f"FAIL_OR_BLOCKED_{eng_v}"

    verdicts = {
        "ENGINEERING_CORRECTNESS": eng_v,
        "BENCHMARK_POLICY_CORRECTNESS": "PASS" if pol_ok else ("BLOCKED_RESOURCE" if low_disk else "FAIL"),
        "DETERMINISM": "PASS" if det_ok else ("BLOCKED_RESOURCE" if low_disk else "FAIL"),
        "RESOURCE_SAFETY": res_v,
        "ECONOMIC_DEV": "NO_ECONOMIC_ADVANCEMENT_CANDIDATE" if economic_ok else "FAIL",
        "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE" if economic_ok else "FAIL",
        "FRESH_OOS": "BLOCKED_NO_UNTOUCHED_DATA" if oos_ok else "FAIL",
        "KH04_ENTRY": "BLOCKED_NO_DEV_CANDIDATE" if (modes_ok and oos_ok and economic_ok) else "FAIL",
        "ACCOUNT_EFFECTIVE_FEE_VERDICT": str(f14.get("ACCOUNT_EFFECTIVE_FEE_VERDICT") or "FAIL"),
        "LIVE_NETWORK_VALIDATION": str(prov.get("LIVE_NETWORK_VALIDATION") or "FAIL"),
        "CI_STATUS": str(f20.get("ci_status") or "CI_UNKNOWN_NO_ACTIONS_RUN"),
        "PUBLICATION": gates["G28_TWO_PHASE_COMMIT_PUSH_REMOTE_READBACK"],
        "ACTIVATION_READY": "NO" if modes_ok else "FAIL",
        "OOS_EVALUATIONS": 0 if oos_ok else -1,
    }
    return {"overall_classification": overall, "gates_28": gates, "verdicts": verdicts}


def verify_kh03_r3_c1_artifacts_independently_from_disk(
    run_dir: Path,
    *,
    expected_run_id: str = "",
    expected_tested_sha: str = "",
    check_live_repo_pointers: bool = True,
    enforce_distinct_pid: bool = True,
    write_manifest: bool = True,
) -> Dict[str, Any]:
    """Separate-OS-process disk verifier recomputing all KH-03.R3.C1 invariants & G01..G28."""
    run_dir = Path(run_dir).resolve()
    verifier_pid = os.getpid()
    violations: List[str] = []

    if not run_dir.is_dir():
        return {
            "spec_version": SPEC_VERSION_KH03_R3_C1,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "exit_code_recommended": 1,
            "violations": [f"RUN_DIR_MISSING:{run_dir}"],
        }

    for rel in REQUIRED_ARTIFACTS_KH03_R3_C1:
        fp = run_dir / rel
        if not fp.is_file() or fp.stat().st_size == 0:
            violations.append(f"MISSING_OR_EMPTY_ARTIFACT:{rel}")
    if violations:
        return {
            "spec_version": SPEC_VERSION_KH03_R3_C1,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "exit_code_recommended": 1,
            "violations": violations,
        }

    def _rd(fn: str) -> Dict[str, Any]:
        return _strict_json_loads((run_dir / fn).read_text("utf-8"))

    try:
        docs: Dict[str, Dict[str, Any]] = {
            "00": _rd("00_preflight_branch_sha_resource_and_lock.json"),
            "01": _rd("01_ast_function_ownership_and_locked_file_diffs.json"),
            "02": _rd("02_prior_r1_r2_r3_dataset_and_sealed_sha_integrity.json"),
            "03": _rd("03_r3_benchmark_schema_bug_real_red_reproduction.json"),
            "04": _rd("04_strict_duration_schema_and_worker_row_falsification.json"),
            "05": _rd("05_r3_historical_fixture_green_pytest_w4_science_w1.json"),
            "06": _rd("06_fresh_pytest_w1_w2_w4_w1repeat_raw_receipts.json"),
            "07": _rd("07_fresh_science_w1_w2_w4_w1repeat_raw_receipts.json"),
            "08": _rd("08_worker_policy_measured_vs_applied_caps_and_provenance.json"),
            "09": _rd("09_worker_policy_noise_stability_and_speedup_limits.json"),
            "10": _rd("10_independent_benchmark_recomputation_and_claim_checks.json"),
            "11": _rd("11_full_pytest_junit_collected_executed_bijection.json"),
            "12": _rd("12_full_A1_SI8_adapter_D4_parity_and_fingerprints.json"),
            "13": _rd("13_six_scenario_backtest_and_E1_E2_E3_null_economic_regression.json"),
            "14": _rd("14_entry_exit_ledger_fee_risk_OOS_and_legacy_guard.json"),
            "15": _rd("15_disk_ram_pid_0p5s_telemetry_and_cleanup.json"),
            "16": _rd("16_inherited_R3_faults_plus_new_C1_red_green_controls.json"),
            "17": _rd("17_new_verifier_mutation_resistance.json"),
            "19": _rd("19_orthogonal_engineering_economic_resource_release_gates.json"),
            "20": _rd("20_phase1_code_push_and_phase2_evidence_remote_readback.json"),
        }
        readme_txt = (run_dir / "README_KH03_R3_C1.md").read_text("utf-8")
        handover_txt = (run_dir / "HANDOVER_KH03_R3_C1_VI.txt").read_text("utf-8")
    except Exception as exc:
        return {
            "spec_version": SPEC_VERSION_KH03_R3_C1,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "exit_code_recommended": 1,
            "violations": [f"ARTIFACT_STRICT_JSON_PARSE_ERROR:{exc}"],
        }

    f00, f01, f02, f06, f07, f08, f09, f10, f11, f13, f14, f15, f19, f20 = (
        docs["00"], docs["01"], docs["02"], docs["06"], docs["07"], docs["08"],
        docs["09"], docs["10"], docs["11"], docs["13"], docs["14"], docs["15"], docs["19"], docs["20"],
    )

    orch_pid = int(f00.get("orchestrator_pid") or -1)
    if enforce_distinct_pid and verifier_pid == orch_pid:
        violations.append(f"VERIFIER_SAME_OS_PID_AS_ORCHESTRATOR:{verifier_pid}")

    # 1. Branch, SHA, run_id binding & anti-stale R3 reuse check (P28)
    run_id = expected_run_id or str(f00.get("run_id") or "")
    tested_sha = expected_tested_sha or str(f00.get("tested_executable_sha") or "")
    if f00.get("branch") != MANDATORY_BRANCH_KH03_R3_C1:
        violations.append(f"WRONG_BRANCH:{f00.get('branch')}")
    if run_id == PRIOR_KH03_R3_RUN_ID or tested_sha == PRIOR_KH03_R3_TESTED_SHA:
        violations.append("STALE_R3_RUN_ID_OR_SHA_REUSED_AS_C1")
    if tested_sha != f00.get("tested_executable_sha") or tested_sha != f19.get("tested_executable_sha") or tested_sha != f20.get("tested_executable_sha"):
        violations.append("TESTED_SHA_MISMATCH_ACROSS_00_19_20")
    if len(tested_sha) >= 12 and not run_id.endswith(f"_{tested_sha[:12]}"):
        violations.append(f"RUN_ID_MISSING_TESTED_SHA12_SUFFIX:{run_id}")

    # 2. Prior R1/R2/R3 + canonical dataset immutability
    if check_live_repo_pointers:
        for rel_p, exp_sha, lbl in PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R3_C1:
            p = REPO_ROOT / rel_p
            if not p.is_file() or _file_sha256(p) != exp_sha:
                violations.append(f"PRIOR_SEALED_ARTIFACT_MUTATED:{lbl}")
    else:
        pm = f02.get("prior_sealed_artifacts_immutability") or {}
        if any(not bool(v.get("match", False)) for v in pm.values() if isinstance(v, Mapping)):
            violations.append("PRIOR_SEALED_ARTIFACT_MUTATED")
    if f02.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH03_R3_C1 or not bool(f02.get("canonical_dataset_sha256_match", False)):
        violations.append("CANONICAL_DATASET_SHA256_TAMPERED")

    # 3. Locked production/research files check
    if int(f01.get("locked_files_modified_count", 1)) != 0 or int(f01.get("unauthorized_changed_files_count", 1)) != 0:
        violations.append("LOCKED_OR_UNAUTHORIZED_FILE_MODIFIED")

    # 4. Independent RAW benchmark policy recomputation from 06 & 07 vs 08/09/10 (NO trust of orchestrator!)
    p_pol_rep = f08.get("pytest_worker_policy") or {}
    s_pol_rep = f08.get("science_worker_policy") or {}
    pytest_recomp = _independent_recompute_raw_benchmark_policy_c1(
        f06,
        p_pol_rep,
        kind="pytest_controlled_subset",
        expected_run_id=run_id,
        expected_tested_sha=tested_sha,
        max_safe_cap=3,
    )
    science_recomp = _independent_recompute_raw_benchmark_policy_c1(
        f07,
        s_pol_rep,
        kind="r2_science_slice",
        expected_run_id=run_id,
        expected_tested_sha=tested_sha,
        max_safe_cap=GLOBAL_MAX_DESCENDANTS_C1,
    )
    if f06.get("status") != "NOT_RUN_BLOCKED_LOW_DISK" and not pytest_recomp["valid"]:
        violations.extend(pytest_recomp["violations"])
    if f07.get("status") != "NOT_RUN_BLOCKED_LOW_DISK" and not science_recomp["valid"]:
        violations.extend(science_recomp["violations"])

    # Reject forbidden unmeasured full-suite speedup claims in artifacts & docs
    for bad_tok in ("FULL_SUITE_SPEEDUP_1.569X", "FULL_SUITE_SPEEDUP_EXTRAPOLATED=TRUE"):
        if bad_tok in readme_txt.upper() or bad_tok in handover_txt.upper():
            violations.append(f"FORBIDDEN_FULL_SUITE_SPEEDUP_CLAIM_IN_DOCS:{bad_tok}")
    if bool(f09.get("full_suite_speedup_extrapolated", False)) or bool(f10.get("unmeasured_full_suite_speedup_claimed", False)):
        violations.append("FORBIDDEN_FULL_SUITE_SPEEDUP_EXTRAPOLATION_IN_09_10")

    # 5. Raw JUnit XML 1:1 bijection
    junit_ok = False
    if f11.get("status") == "PASS":
        junit_ok, j_errs = _verify_junit_raw_xml_bijection_c1(
            run_dir, f11, expected_run_id=run_id, expected_tested_sha=tested_sha
        )
        if not junit_ok:
            violations.extend(j_errs)

    # 6. Economic 6-scenario & E1/E2/E3/NULL regression + OOS=0 + H3 advisory + activation OFF
    economic_ok, econ_viols = _audit_economic_and_six_scenario_regression_c1(f13, f14)
    if not economic_ok:
        violations.extend(econ_viols)
    for idx_k in ("00", "01", "13", "14"):
        d_s = docs[idx_k]
        if d_s.get("ACTIVATION_READY") != "NO" or any((d_s.get("runtime_safety_modes") or {}).get(m) != "OFF" for m in ("PRODUCTION", "LIVE", "PAPER", "AUTO")):
            violations.append(f"SAFETY_MODE_OR_ACTIVATION_VIOLATION_IN_{idx_k}")

    # 7. Direct 0.5s gzipped telemetry audit reconciled vs 15
    mem_log = run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    mem_audit = _audit_memory_and_disk_samples_c1(mem_log, f15)
    if mem_audit.get("status") != "PASS":
        violations.extend(mem_audit.get("violations") or ["TELEMETRY_AUDIT_FAILED"])

    # 8. Zero disk waiver, remote readback honesty & CI honesty (P29)
    if bool(f00.get("owner_disk_preflight_waiver", False)) or bool(f15.get("owner_disk_preflight_waiver", False)):
        violations.append("UNAPPROVED_OWNER_DISK_WAIVER_FORBIDDEN")
    if f20.get("publication_status") == "PASS_REMOTE_VERIFIED" and (
        not bool(f20.get("remote_readback_sha256_verified"))
        or len(str(f20.get("evidence_commit_sha") or "")) != 40
    ):
        violations.append("FALSE_REMOTE_READBACK_CLAIM")
    if f20.get("ci_status") == "PASS" and not bool(f20.get("actual_github_actions_run_verified", False)):
        violations.append("FABRICATED_CI_PASS_WITHOUT_ACTIONS_RUN")

    # 9. Recompute all 28 gates & verify against stored 19
    recomp_pre = recompute_kh03_r3_c1_28_gates_and_verdicts(
        docs=docs,
        pytest_recomp=pytest_recomp,
        science_recomp=science_recomp,
        mem_audit=mem_audit,
        junit_bijection_ok=junit_ok,
        economic_ok=economic_ok,
        verifier_violations_count=len(violations),
        for_final_verifier_manifest=False,
    )
    stored_gates = f19.get("gates_28") or {}
    for gk, gv in recomp_pre["gates_28"].items():
        if stored_gates.get(gk) != gv:
            violations.append(f"GATE_TAMPER_OR_DRIFT:{gk}:stored={stored_gates.get(gk)}!=recomputed={gv}")

    # 10. Cross-view consistency on README & HANDOVER
    for tok, err_code in (
        (run_id, "CROSS_VIEW_RUN_ID_MISSING"),
        ("ACTIVATION_READY=NO", "CROSS_VIEW_ACTIVATION_LOCK_MISSING"),
        ("NO_PROVEN_EDGE", "CROSS_VIEW_NO_PROVEN_EDGE_MISSING"),
        ("OOS_EVALUATIONS=0", "CROSS_VIEW_OOS_ZERO_MISSING"),
        ("CI_UNKNOWN_NO_ACTIONS_RUN", "CROSS_VIEW_CI_HONESTY_MISSING"),
    ):
        if tok not in readme_txt or tok not in handover_txt:
            violations.append(err_code)

    recomp_final = recompute_kh03_r3_c1_28_gates_and_verdicts(
        docs=docs,
        pytest_recomp=pytest_recomp,
        science_recomp=science_recomp,
        mem_audit=mem_audit,
        junit_bijection_ok=junit_ok,
        economic_ok=economic_ok,
        verifier_violations_count=len(violations),
        for_final_verifier_manifest=True,
    )

    artifact_sha256_map: Dict[str, Dict[str, Any]] = {}
    for fp in sorted(run_dir.rglob("*")):
        if not fp.is_file():
            continue
        rel = fp.relative_to(run_dir).as_posix()
        if rel == MANIFEST_18_NAME_KH03_R3_C1 or rel.endswith(".tmp"):
            continue
        artifact_sha256_map[rel] = {"size_bytes": int(fp.stat().st_size), "sha256": _file_sha256(fp)}

    eng_final = recomp_final["verdicts"]["ENGINEERING_CORRECTNESS"]
    if len(violations) > 0 or eng_final == "FAIL":
        verifier_status, exit_code = "FAIL", 1
    elif eng_final == "PASS":
        verifier_status, exit_code = "PASS", 0
    else:
        verifier_status, exit_code = "PASS", 2

    payload: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R3_C1,
        "schema_version": SCHEMA_VERSION_KH03_R3_C1,
        "verified_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "orchestrator_pid": orch_pid,
        "verifier_pid": verifier_pid,
        "distinct_os_process_verified": (verifier_pid != orch_pid),
        "independent_verifier_status": verifier_status,
        "exit_code_recommended": exit_code,
        "overall_classification": recomp_final["overall_classification"],
        "recomputed_28_gates": recomp_final["gates_28"],
        "recomputed_verdicts": recomp_final["verdicts"],
        "independent_pytest_policy_recomputation": pytest_recomp,
        "independent_science_policy_recomputation": science_recomp,
        "independent_memory_telemetry_audit": mem_audit,
        "independent_junit_bijection_ok": junit_ok,
        "independent_economic_regression_ok": economic_ok,
        "violations_count": len(violations),
        "violations": violations,
        "artifact_count_hashed": len(artifact_sha256_map),
        "manifest_content_sha256": canonical_hash(artifact_sha256_map),
        "artifact_sha256_manifest": artifact_sha256_map,
    }
    if write_manifest:
        write_json_durable_atomic(run_dir / MANIFEST_18_NAME_KH03_R3_C1, payload)
    return payload


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="AURUM DESK KH-03.R3.C1 Separate-OS Independent Verifier")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--expected-run-id", default="")
    ap.add_argument("--expected-tested-sha", default="")
    ap.add_argument("--skip-live-repo-pointers", action="store_true")
    ap.add_argument("--allow-same-pid", action="store_true")
    ap.add_argument("--no-write-manifest", action="store_true")
    args = ap.parse_args(argv)

    res = verify_kh03_r3_c1_artifacts_independently_from_disk(
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
                "recomputed_28_gates": res.get("recomputed_28_gates"),
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
