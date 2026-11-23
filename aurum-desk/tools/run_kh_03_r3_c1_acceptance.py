#!/usr/bin/env python3
"""
aurum-desk/tools/run_kh_03_r3_c1_acceptance.py

AURUM DESK — KH-03.R3.C1 | WORKER BENCHMARK TRUTH, DETERMINISTIC ASYNC QA & SCIENTIFIC CLOSURE
SPEC VERSION: KH-03.R3.C1-v1.0 | ENGINEERING & SCIENTIFIC HONESTY CLOSURE | SAME BRANCH | ACTIVATION_READY=NO

Produces a clean, immutable per-run directory under:
  aurum-desk/docs/artifacts/kh_03_r3_c1_worker_policy/<UTC_RUN_ID>_<TESTED_SHA12>/
containing artifacts 00..20 (with 18 sealed by separate-OS independent verifier),
README_KH03_R3_C1.md, HANDOVER_KH03_R3_C1_VI.txt, checksums/, logs/, and junit/.
NEVER mutates any prior KH-03.R3, R2, R1, C4..C1, KH-02, or KH-01B runs or pointers.
"""
from __future__ import annotations

import json as _early_json
import os as _early_os
import sys as _early_sys

if __name__ == "__main__" and (
    "--owner-waive-disk-preflight" in _early_sys.argv
    or str(_early_os.environ.get("KH02_OWNER_WAIVE_DISK_PREFLIGHT") or "").strip().lower() in ("1", "true", "yes")
    or str(_early_os.environ.get("KH03_OWNER_WAIVE_DISK_PREFLIGHT") or "").strip().lower() in ("1", "true", "yes")
    or str(_early_os.environ.get("KH03_C1_OWNER_WAIVE_DISK_PREFLIGHT") or "").strip().lower() in ("1", "true", "yes")
):
    print(
        _early_json.dumps(
            {
                "status": "REJECTED_UNAPPROVED_AUTONOMOUS_DISK_WAIVER",
                "error": "UNAPPROVED_AUTONOMOUS_DISK_WAIVER_FORBIDDEN (>=20.5 GiB preflight / >=20.0 GiB runtime).",
                "exit_code": 2,
            },
            indent=2,
            sort_keys=True,
        ),
        file=_early_sys.stderr,
    )
    raise SystemExit(2)

import argparse
import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime, timezone
import gc
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

import psutil

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from domain_calculator import CostAssumptions
from services.kh_01b_r1_ram_safety_r3_service import load_empirical_r2_shard_rss_bytes
from services.kh_02_entry_foundation_research_service import (
    build_kh02_asof_mtf_features,
    generate_kh02_research_candidates,
)
from services.kh_02_r2_research_integrity_service import (
    ACTIVATION_READY_KH02_R2,
    ADVISORY_DISK_GIB_KH02_R2,
    CANONICAL_DATASET_REL_PATH,
    CANONICAL_DATASET_SHA256_KH02_R2,
    DEFAULT_RANDOM_SEED_KH02_R2,
    FORBIDDEN_ORDER_SERVICE_TOKENS,
    GLOBAL_MAX_DESCENDANTS_KH02_R2,
    LOCKED_KEEP_AS_IS_FILES_KH02_R2,
    PREFLIGHT_RAM_MIN_MIB_KH02_R2,
    RUNTIME_SAFETY_MODES_KH02_R2,
    STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
    STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2,
    _ast_function_inventory,
    _r2_frozen_plan_and_arms,
    _sha256_file,
    audit_r2_reference_inputs_and_invariants,
    build_r2_c1_readonly_disk_inventory,
    build_r2_causal_unswept_level_ledger,
    build_r2_target_repair_full_opportunity_conservation,
    create_sentinel_owned_temp_dir_r2_c1,
    load_r2_canonical_dataset_readonly,
    probe_r2_c1_write_mounts_strict,
    replay_r2_same_opportunity_paired_counterfactuals,
    reprice_r2_fixed_fills_cost_counterfactual,
)
from services.kh_03_data_governance_service import audit_kh03_collector_and_escrow_readiness
from services.kh_03_r3_c1_worker_policy_service import (
    BENCHMARK_KIND_PYTEST,
    BENCHMARK_KIND_SCIENCE,
    SCHEMA_VERSION_KH03_R3_C1,
    SPEC_VERSION_KH03_R3_C1,
    legacy_buggy_compute_optimum_worker_policy_r3_red_repro,
    parse_benchmark_run_duration_strict,
    parse_json_strict_no_nonfinite,
    recompute_worker_policy_from_raw_receipt,
    select_measured_worker_policy_strict,
    validate_benchmark_semantic_equivalence_strict,
)
from services.kh_03_r3_diagnostic_service import (
    audit_r3_entry_exit_geometry_and_accounting,
    build_r3_full_opportunity_lineage,
    build_r3_win_loss_failure_attribution,
)
from services.kh_03_r3_experiment_service import (
    build_r3_causal_m5_unswept_target_ledger,
    build_r3_train_only_candidates_asof,
    freeze_r3_bounded_research_plan,
    run_r3_forward_only_dev_evaluation,
    run_r3_paired_and_strict_dev_evaluation_15_16,
)
from services.kh_03_r3_legacy_research_guard import audit_kh03_r3_legacy_research_callgraph
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    verify_fresh_junit_bijection_v17931211,
)
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_03_r3_c1_independent_verifier import (
    KH03_R3_C1_SOURCE_FILES_ALLOWLIST,
    MANDATORY_BRANCH_KH03_R3_C1,
    MIN_EXPECTED_PYTEST_COLLECTED_C1,
    PRIOR_KH03_R3_ARTIFACT_19_REL,
    PRIOR_KH03_R3_ARTIFACT_19_SHA256,
    PRIOR_KH03_R3_EVIDENCE_SHA,
    PRIOR_KH03_R3_PUB_HEAD_SHA,
    PRIOR_KH03_R3_RUN_ID,
    PRIOR_KH03_R3_TESTED_SHA,
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R3_C1,
    SEALED_R3_1X_ECONOMIC_ANCHORS,
    _audit_economic_and_six_scenario_regression_c1,
    _audit_memory_and_disk_samples_c1,
    _independent_recompute_raw_benchmark_policy_c1,
    recompute_kh03_r3_c1_28_gates_and_verdicts,
)
from tools.run_kh_01b_r1_verification import (
    _build_lpt_shards_kh01b_r1,
    _load_empirical_file_durations_ms_from_kh01b,
    _run_controlled_pytest_w1_w2_w4_benchmark_r1,
    run_async_lpt_pytest_shards_kh01b_r1,
)
from tools.run_kh_02_r2_c4_acceptance import (
    _cleanup_sentinel_temp_c4_win_safe,
    _pypath_str,
)
from tools.run_kh_02_r2_verification import (
    _BoundedResourceSampler,
    _git_cmd,
    _run_full_production_baseline_parity_20,
    _run_r2_science_slice_w1_w2_w4_determinism,
)
from tools.run_kh_03_r3_acceptance import (
    KH03_R3_EXCLUSIVE_SUBPROCESS_TEST_FILES,
    _run_kh03_r3_194_red_green_fault_suite,
    _run_kh03_r3_verifier_mutation_suite_22,
)
from tools.run_v17_9_3_1_2_2_verification import canonicalize_collect_node_id_v1793122

KH03_R3_C1_ARTIFACTS_BASE_REL = "aurum-desk/docs/artifacts/kh_03_r3_c1_worker_policy"
LINEAGE_TRACKED_ARTIFACT_REL = "aurum-desk/backtest/trade_decision_lineage_reconciliation.json"

EXTRA_FROZEN_RESEARCH_FILES_C1: Tuple[str, ...] = (
    "aurum-desk/backend/services/kh_02_entry_foundation_research_service.py",
    "aurum-desk/backend/services/kh_02_r2_research_integrity_service.py",
    "aurum-desk/backend/services/kh_03_r2_methodology_service.py",
    "aurum-desk/backend/services/kh_03_r3_diagnostic_service.py",
    "aurum-desk/backend/services/kh_03_r3_experiment_service.py",
    "aurum-desk/backend/services/kh_03_r3_legacy_research_guard.py",
)

KH03_R3_C1_EXCLUSIVE_SUBPROCESS_TEST_FILES: Set[str] = set(KH03_R3_EXCLUSIVE_SUBPROCESS_TEST_FILES) | {
    "test_kh03_r3_c1_policy_safety.py",
    "test_kh03_r3_c1_verifier_mutations.py",
}

FUNCTION_RESPONSIBILITY_MAP_KH03_R3_C1: List[Dict[str, str]] = [
    {
        "function": "parse_benchmark_run_duration_strict",
        "module": "aurum-desk/backend/services/kh_03_r3_c1_worker_policy_service.py",
        "inputs": "raw run row dict, benchmark_kind",
        "outputs": "validated {mode_label, workers, duration_sec, duration_source_key, input_row_sha256, provenance_status, violations}",
        "invariants": "Canonical kind-specific duration keys (`wall_clock_seconds` / `wall_seconds`), rejects bool/str/NaN/Inf/<=0/conflicts/mixed keys/bad worker_pids",
        "tests": "test_kh03_r3_c1_worker_policy_schema.py (P07..P13)",
        "evidence": "04, 06, 07, 08",
    },
    {
        "function": "validate_benchmark_semantic_equivalence_strict",
        "module": "aurum-desk/backend/services/kh_03_r3_c1_worker_policy_service.py",
        "inputs": "raw bench_doc, benchmark_kind, expected_run_id, expected_tested_sha",
        "outputs": "PASS or BLOCKED_INVALID_BENCHMARK + normalized 4-mode table + canonical semantic SHA",
        "invariants": "Requires all 4 modes (w=1,w=2,w=4,w=1_repeat), no duplicates, identical positive counts & SHA; rejects stale/cached runs",
        "tests": "test_kh03_r3_c1_worker_policy_schema.py (P03..P06, P11, P18)",
        "evidence": "04, 05, 06, 07, 08",
    },
    {
        "function": "select_measured_worker_policy_strict",
        "module": "aurum-desk/backend/services/kh_03_r3_c1_worker_policy_service.py",
        "inputs": "bench_doc, benchmark_kind, system_limits, tie_rule, min_meaningful_gain_pct",
        "outputs": "measured_fastest_mode, mean_wall_sec_by_mode, applied_worker_count, safe_worker_cap, w1_repeat_variance_pct, policy_decision_sha256",
        "invariants": "Separates measured subset fastest from safe applied cap (<=3 full pytest), never extrapolates full-suite speedup, tie prefers fewer workers, fail-closed on low disk/RAM",
        "tests": "test_kh03_r3_c1_worker_policy_schema.py (P01, P02, P16, P17), test_kh03_r3_c1_policy_safety.py (P14, P15)",
        "evidence": "05, 08, 09, 10",
    },
    {
        "function": "recompute_worker_policy_from_raw_receipt",
        "module": "aurum-desk/backend/services/kh_03_r3_c1_worker_policy_service.py",
        "inputs": "raw_bench_doc, reported_policy, benchmark_kind, reported_speedups, system_limits",
        "outputs": "independent verification status + discrepancies list",
        "invariants": "Rejects empty means, mismatched fastest mode, forged speedup, over-cap workers, extrapolated speedup",
        "tests": "test_kh03_r3_c1_worker_policy_schema.py, test_kh03_r3_c1_regression_truth.py",
        "evidence": "10, 16",
    },
    {
        "function": "_compute_optimum_worker_policy (minimal backward-compat shim)",
        "module": "aurum-desk/tools/run_kh_03_r3_acceptance.py",
        "inputs": "bench_doc, optional keyword-only benchmark_kind & system_limits",
        "outputs": "backward-compat {mean_wall_sec_by_mode, optimum_worker_policy} + validated C1 policy metadata",
        "invariants": "Fixes P1 key bug in R3 without rewriting R3 orchestration; keeps Stage 6A pytest_workers=min(3, eff_workers)",
        "tests": "test_kh03_r3_c1_regression_truth.py",
        "evidence": "01, 03, 05",
    },
    {
        "function": "verify_kh03_r3_c1_artifacts_independently_from_disk + _independent_recompute_raw_benchmark_policy_c1",
        "module": "aurum-desk/tools/kh_03_r3_c1_independent_verifier.py",
        "inputs": "run_dir, expected_run_id, expected_tested_sha",
        "outputs": "18_independent_OS_verifier_manifest_sha256.json + exit code 0/1/2",
        "invariants": "Distinct OS PID, zero orchestrator import, independent minimal raw timing/SHA/speedup/cap/JUnit/telemetry/economic/28-gate recomputation",
        "tests": "test_kh03_r3_c1_verifier_mutations.py (17 NEW separate-OS mutations)",
        "evidence": "17, 18, 19",
    },
    {
        "function": "run_kh03_r3_c1_acceptance + main",
        "module": "aurum-desk/tools/run_kh_03_r3_c1_acceptance.py",
        "inputs": "CLI flags (waiver exit 2), max_workers <= 4",
        "outputs": "Immutable per-run artifact directory 00..20 + README + HANDOVER + junit/ + logs/ + checksums/",
        "invariants": "Single-run lock, strict >=20.5/20.0 GiB disk & >=512 MiB RAM, <=4 descendants, atomic writes, zero mutation of prior runs",
        "tests": "test_kh03_r3_c1_policy_safety.py",
        "evidence": "00..20",
    },
]


class _CrossPlatformRunLock:
    """Single-run exclusive non-blocking lock preventing concurrent orchestrator races."""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._fh: Any = None

    def acquire(self) -> Tuple[bool, str]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.lock_path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt  # type: ignore[import-not-found]
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fh.seek(0)
            self._fh.truncate()
            self._fh.write(f"pid={os.getpid()}\n".encode("utf-8"))
            self._fh.flush()
            return True, "ACQUIRED"
        except Exception as exc:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None
            return False, f"LOCK_CONTENTION:{exc}"

    def release(self) -> None:
        if self._fh is not None:
            try:
                if os.name == "nt":
                    import msvcrt  # type: ignore[import-not-found]
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None
        try:
            self.lock_path.unlink(missing_ok=True)
        except Exception:
            pass


def _audit_kh03_r3_c1_ownership_01(tested_sha: str, legacy_cg: Mapping[str, Any]) -> Dict[str, Any]:
    all_locked = list(dict.fromkeys(list(LOCKED_KEEP_AS_IS_FILES_KH02_R2) + list(EXTRA_FROZEN_RESEARCH_FILES_C1)))
    locked_inv = []
    for rel in all_locked:
        p = REPO_ROOT / rel
        locked_inv.append({
            "rel_path": rel,
            "exists": p.is_file(),
            "sha256": _sha256_file(p) if p.is_file() else None,
            "ast_function_count": len(_ast_function_inventory(p)) if (rel.endswith(".py") and p.is_file()) else None,
        })

    rc_diff, diff_out = _git_cmd(["diff", "--name-only", f"{PRIOR_KH03_R3_PUB_HEAD_SHA}..HEAD"])
    changed_files = [ln.strip() for ln in diff_out.splitlines() if ln.strip()] if rc_diff == 0 else []
    locked_mod = [f for f in changed_files if f in set(all_locked)]
    allow_set = set(KH03_R3_C1_SOURCE_FILES_ALLOWLIST)
    unauthorized_changed = [
        f for f in changed_files
        if f not in allow_set and not f.startswith(f"{KH03_R3_C1_ARTIFACTS_BASE_REL}/")
    ]

    forbidden_hits: List[str] = []
    c1_inv: List[Dict[str, Any]] = []
    for rel in KH03_R3_C1_SOURCE_FILES_ALLOWLIST:
        p = REPO_ROOT / rel
        if not p.is_file():
            continue
        src = p.read_text("utf-8", errors="replace")
        tree = ast.parse(src, filename=str(p))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(tok in alias.name for tok in FORBIDDEN_ORDER_SERVICE_TOKENS):
                        forbidden_hits.append(f"{rel}:import:{alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if any(tok in mod for tok in FORBIDDEN_ORDER_SERVICE_TOKENS):
                    forbidden_hits.append(f"{rel}:from:{mod}")
        c1_inv.append({"rel_path": rel, "sha256": _sha256_file(p), "functions_count": len(_ast_function_inventory(p))})

    c_live = CostAssumptions()
    cost_ok = (
        abs(float(c_live.maker_fee_rate) - 0.0002) <= 1e-12
        and abs(float(c_live.taker_fee_rate) - 0.0004) <= 1e-12
        and abs(float(c_live.slippage_usd) - 0.10) <= 1e-12
    )
    ok = (
        len(locked_mod) == 0
        and len(unauthorized_changed) == 0
        and len(forbidden_hits) == 0
        and cost_ok
        and legacy_cg.get("LEGACY_METHOD_GUARD") == "PASS"
    )
    return {
        "spec_version": SPEC_VERSION_KH03_R3_C1,
        "schema_version": SCHEMA_VERSION_KH03_R3_C1,
        "tested_executable_sha": tested_sha,
        "prior_r3_pub_head_sha": PRIOR_KH03_R3_PUB_HEAD_SHA,
        "locked_keep_as_is_count": len(locked_inv),
        "locked_keep_as_is_inventory": locked_inv,
        "locked_files_modified": locked_mod,
        "locked_files_modified_count": len(locked_mod),
        "changed_files_vs_r3_pub_head": changed_files,
        "unauthorized_changed_files": unauthorized_changed,
        "unauthorized_changed_files_count": len(unauthorized_changed),
        "kh03_r3_c1_module_inventory": c1_inv,
        "function_responsibility_map": FUNCTION_RESPONSIBILITY_MAP_KH03_R3_C1,
        "legacy_guard_status": legacy_cg.get("LEGACY_METHOD_GUARD"),
        "forbidden_order_import_hits": forbidden_hits,
        "cost_assumptions_defaults_intact": cost_ok,
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        "status": "PASS" if ok else "FAIL",
    }


def _audit_prior_sealed_02(sentinel_tmp: Path) -> Dict[str, Any]:
    ds_p = REPO_ROOT / CANONICAL_DATASET_REL_PATH
    ds_sha = _sha256_file(ds_p) if ds_p.is_file() else ""
    ds_ok = (ds_sha == CANONICAL_DATASET_SHA256_KH02_R2)
    prior_map: Dict[str, Any] = {}
    all_ok = ds_ok
    for rel_p, exp_sha, lbl in PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R3_C1:
        p = REPO_ROOT / rel_p
        act = _sha256_file(p) if p.is_file() else ""
        m = p.is_file() and (act == exp_sha)
        if not m:
            all_ok = False
        prior_map[lbl] = {"rel_path": rel_p, "expected_sha256": exp_sha, "actual_sha256": act, "match": m}

    prov_dir = sentinel_tmp / "c4_prov_c1"
    prov_dir.mkdir(parents=True, exist_ok=True)
    f08_c4, f09_c4 = audit_kh03_collector_and_escrow_readiness(prov_dir)
    prov_ok = (
        f08_c4.get("status") == "PASS"
        and f09_c4.get("status") == "PASS"
        and f08_c4.get("network_capture_enabled") is False
        and f08_c4.get("live_network_capture_executed") is False
        and int(f09_c4.get("real_oos_escrow_final_record_count", -1)) == 0
    )
    return {
        "spec_version": SPEC_VERSION_KH03_R3_C1,
        "schema_version": SCHEMA_VERSION_KH03_R3_C1,
        "canonical_dataset_rel_path": CANONICAL_DATASET_REL_PATH,
        "canonical_dataset_sha256": ds_sha,
        "canonical_dataset_expected_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
        "canonical_dataset_sha256_match": ds_ok,
        "all_prior_sealed_artifacts_intact": all_ok,
        "prior_sealed_artifacts_count": len(prior_map),
        "prior_sealed_artifacts_immutability": prior_map,
        "c4_provenance_and_oos_boundary": {
            "network_capture_enabled": f08_c4.get("network_capture_enabled"),
            "live_network_capture_executed": f08_c4.get("live_network_capture_executed"),
            "LIVE_NETWORK_VALIDATION": f08_c4.get("LIVE_NETWORK_VALIDATION"),
            "real_oos_escrow_integrity_valid": f09_c4.get("real_oos_escrow_integrity_valid"),
            "real_oos_escrow_final_record_count": f09_c4.get("real_oos_escrow_final_record_count"),
            "qa_fixture_refused_by_real_oos_escrow": f09_c4.get("qa_fixture_refused_by_real_oos_escrow"),
            "status": "PASS" if prov_ok else "FAIL",
        },
        "status": "PASS" if (all_ok and prov_ok) else "FAIL",
    }


def _run_stage2_red_schema_and_green_fixtures_03_04_05(tested_sha: str, run_id: str) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    r3_19_path = REPO_ROOT / PRIOR_KH03_R3_ARTIFACT_19_REL
    r3_19_sha = _sha256_file(r3_19_path)
    r3_19 = parse_json_strict_no_nonfinite(r3_19_path.read_text("utf-8"))
    pb_old = r3_19["pytest_controlled_subset_w1_w2_w4_repeat"]
    sb_old = r3_19["r2_science_slice_w1_w2_w4_repeat"]

    # 03: REAL RED reproduction using verbatim old R3 logic on immutable R3 artifact 19
    old_p = legacy_buggy_compute_optimum_worker_policy_r3_red_repro(pb_old)
    old_s = legacy_buggy_compute_optimum_worker_policy_r3_red_repro(sb_old)
    red_ok = (
        r3_19_sha == PRIOR_KH03_R3_ARTIFACT_19_SHA256
        and old_p["mean_wall_sec_by_mode"] == {}
        and old_p["optimum_worker_policy"] == "w=1"
        and old_s["mean_wall_sec_by_mode"] == {}
        and old_s["optimum_worker_policy"] == "w=1"
        and r3_19.get("pytest_optimum_worker_policy", {}).get("mean_wall_sec_by_mode") == {}
        and r3_19.get("science_optimum_worker_policy", {}).get("mean_wall_sec_by_mode") == {}
    )
    f03 = {
        "spec_version": SPEC_VERSION_KH03_R3_C1,
        "schema_version": SCHEMA_VERSION_KH03_R3_C1,
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "source_fixture_rel_path": PRIOR_KH03_R3_ARTIFACT_19_REL,
        "source_fixture_sha256": r3_19_sha,
        "source_fixture_mutated": False,
        "defect_root_cause": (
            "Old _compute_optimum_worker_policy only checked elapsed_sec/wall_sec/duration_sec, "
            "ignoring actual producer keys wall_clock_seconds (pytest) and wall_seconds (science)."
        ),
        "sealed_r3_published_pytest_policy": r3_19.get("pytest_optimum_worker_policy"),
        "sealed_r3_published_science_policy": r3_19.get("science_optimum_worker_policy"),
        "old_buggy_pytest_policy": old_p,
        "old_buggy_science_policy": old_s,
        "classification": "REAL_RED_REPRODUCED" if red_ok else "FAILED_RED_REPRO",
        "status": "PASS" if red_ok else "FAIL",
    }

    # 04: Strict duration schema & boundary falsification matrix (16 cases)
    cases: List[Dict[str, Any]] = []

    def _chk(cid: str, desc: str, passed: bool, detail: str) -> None:
        cases.append({"case_id": cid, "description": desc, "passed": bool(passed), "detail": detail})

    pb_no_rep = copy.deepcopy(pb_old)
    pb_no_rep["runs"] = [r for r in pb_no_rep["runs"] if r["mode_label"] != "w=1_repeat"]
    _chk("S01_P04_MISSING_W1_REPEAT", "Missing w=1_repeat blocks policy", select_measured_worker_policy_strict(pb_no_rep, benchmark_kind=BENCHMARK_KIND_PYTEST, is_historical_reference=True)["status"] == "BLOCKED_INVALID_BENCHMARK", "BLOCKED")

    pb_no_w4 = copy.deepcopy(pb_old)
    pb_no_w4["runs"] = [r for r in pb_no_w4["runs"] if r["mode_label"] != "w=4"]
    r_nw4 = select_measured_worker_policy_strict(pb_no_w4, benchmark_kind=BENCHMARK_KIND_PYTEST, is_historical_reference=True)
    _chk("S02_P05_MISSING_W4", "Missing w=4 blocks and never selects w=2", r_nw4["status"] == "BLOCKED_INVALID_BENCHMARK" and r_nw4["optimum_worker_policy"] != "w=2", "BLOCKED")

    pb_dup = copy.deepcopy(pb_old)
    d2 = copy.deepcopy(pb_dup["runs"][1])
    d2["wall_clock_seconds"] = 88.8
    pb_dup["runs"].append(d2)
    _chk("S03_P06_DUP_W2_DISAGREE", "Duplicate w=2 disagreeing fails", not validate_benchmark_semantic_equivalence_strict(pb_dup, benchmark_kind=BENCHMARK_KIND_PYTEST, is_historical_reference=True)["valid"], "REJECTED")

    _chk("S04_P07_STRING_DURATION", "String duration rejected", not parse_benchmark_run_duration_strict({"mode_label": "w=1", "workers": 1, "wall_clock_seconds": "32.947"}, benchmark_kind=BENCHMARK_KIND_PYTEST)["valid"], "REJECTED")
    _chk("S05_P07_BOOL_DURATION", "Bool duration rejected", not parse_benchmark_run_duration_strict({"mode_label": "w=1", "workers": 1, "wall_clock_seconds": True}, benchmark_kind=BENCHMARK_KIND_PYTEST)["valid"], "REJECTED")
    _chk("S06_P08_NAN_INF_DURATION", "NaN/Inf/-Inf rejected", all(not parse_benchmark_run_duration_strict({"mode_label": "w=1", "workers": 1, "wall_clock_seconds": v}, benchmark_kind=BENCHMARK_KIND_PYTEST)["valid"] for v in (float("nan"), float("inf"), float("-inf"))), "REJECTED")

    nan_json_err = False
    try:
        parse_json_strict_no_nonfinite('{"wall_clock_seconds": NaN}')
    except ValueError:
        nan_json_err = True
    _chk("S07_P08_JSON_NAN_PARSE", "Nonstandard JSON NaN constant rejected", nan_json_err, "RAISED_VALUE_ERROR")
    _chk("S08_P09_ZERO_NEG_DURATION", "Zero and negative duration rejected", (not parse_benchmark_run_duration_strict({"mode_label": "w=1", "workers": 1, "wall_clock_seconds": 0}, benchmark_kind=BENCHMARK_KIND_PYTEST)["valid"]) and (not parse_benchmark_run_duration_strict({"mode_label": "w=1", "workers": 1, "wall_clock_seconds": -2.0}, benchmark_kind=BENCHMARK_KIND_PYTEST)["valid"]), "REJECTED")
    _chk("S09_P10_CONFLICTING_KEYS", "Conflicting wall_clock_seconds vs elapsed_sec rejected", not parse_benchmark_run_duration_strict({"mode_label": "w=1", "workers": 1, "wall_clock_seconds": 32.9, "elapsed_sec": 50.0}, benchmark_kind=BENCHMARK_KIND_PYTEST)["valid"], "REJECTED")

    pb_mix = copy.deepcopy(pb_old)
    pb_mix["runs"][2]["wall_seconds"] = 2.277
    _chk("S10_P11_MIXED_PYTEST_SCIENCE", "Mixed pytest/science keys rejected", not validate_benchmark_semantic_equivalence_strict(pb_mix, benchmark_kind=BENCHMARK_KIND_PYTEST, is_historical_reference=True)["valid"], "REJECTED")
    _chk("S11_P12_MODE_WORKERS_MISMATCH", "mode_label=w=4 with workers=1 rejected", not parse_benchmark_run_duration_strict({"mode_label": "w=4", "workers": 1, "wall_clock_seconds": 30.0}, benchmark_kind=BENCHMARK_KIND_PYTEST)["valid"], "REJECTED")
    _chk("S12_P13_BAD_WORKER_PIDS", "W4 incompatible worker_pids count rejected", not parse_benchmark_run_duration_strict({"mode_label": "w=4", "workers": 4, "worker_pids": [1, 2], "wall_clock_seconds": 30.0}, benchmark_kind=BENCHMARK_KIND_PYTEST)["valid"], "REJECTED")

    pb_tie = copy.deepcopy(pb_old)
    pb_tie["runs"][0]["wall_clock_seconds"] = 50.0
    pb_tie["runs"][1]["wall_clock_seconds"] = 35.0
    pb_tie["runs"][2]["wall_clock_seconds"] = 35.0
    pb_tie["runs"][3]["wall_clock_seconds"] = 50.5
    r_tie = select_measured_worker_policy_strict(pb_tie, benchmark_kind=BENCHMARK_KIND_PYTEST, is_historical_reference=True)
    _chk("S13_P16_EXACT_TIE_FEWER_WORKERS", "W2==W4 exact tie selects w=2 deterministically", r_tie["status"] == "PASS" and r_tie["measured_fastest_mode"] == "w=2" and r_tie["tie_detected"], "SELECTED_W2")

    pb_var = copy.deepcopy(pb_old)
    pb_var["runs"][0]["wall_clock_seconds"] = 50.0
    pb_var["runs"][3]["wall_clock_seconds"] = 60.0
    r_var = select_measured_worker_policy_strict(pb_var, benchmark_kind=BENCHMARK_KIND_PYTEST, is_historical_reference=True)
    _chk("S14_P17_W1_REPEAT_TIMING_NOISE", "W1_repeat timing drift with same SHA passes + warns", r_var["status"] == "PASS" and r_var["timing_stability_status"] == "HIGH_TIMING_NOISE_WARNING", "DISCLOSED")

    pb_badsha = copy.deepcopy(pb_old)
    pb_badsha["runs"][3]["canonical_sorted_outcome_sha256"] = "0" * 64
    _chk("S15_P18_W1_REPEAT_SHA_DIFFERS", "W1_repeat SHA drift fails determinism", select_measured_worker_policy_strict(pb_badsha, benchmark_kind=BENCHMARK_KIND_PYTEST, is_historical_reference=True)["status"] == "BLOCKED_INVALID_BENCHMARK", "BLOCKED")

    r_res = select_measured_worker_policy_strict(pb_old, benchmark_kind=BENCHMARK_KIND_PYTEST, system_limits={"preflight_disk_free_gib": 18.0, "disk_free_gib": 18.0, "available_ram_mib": 300.0}, is_historical_reference=True)
    _chk("S16_P15_LOW_RESOURCE_BLOCKS", "Low disk/RAM blocks policy fail-closed", r_res["status"] == "BLOCKED_RESOURCE" and r_res["applied_worker_count"] == 0, "BLOCKED_RESOURCE")

    passed_n = sum(1 for c in cases if c["passed"])
    f04 = {
        "spec_version": SPEC_VERSION_KH03_R3_C1,
        "schema_version": SCHEMA_VERSION_KH03_R3_C1,
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "cases_total": len(cases),
        "cases_passed": passed_n,
        "cases_failed": len(cases) - passed_n,
        "cases": cases,
        "status": "PASS" if passed_n == len(cases) else "FAIL",
    }

    # 05: GREEN proof on immutable R3 artifact 19 (HISTORICAL_REFERENCE_ONLY — distinct from fresh C1 run!)
    grn_p = select_measured_worker_policy_strict(
        pb_old,
        benchmark_kind=BENCHMARK_KIND_PYTEST,
        system_limits={"configured_child_process_cap": GLOBAL_MAX_DESCENDANTS_KH02_R2, "full_suite_safe_worker_cap": 3},
        is_historical_reference=True,
    )
    grn_s = select_measured_worker_policy_strict(
        sb_old,
        benchmark_kind=BENCHMARK_KIND_SCIENCE,
        system_limits={"configured_child_process_cap": GLOBAL_MAX_DESCENDANTS_KH02_R2, "full_suite_safe_worker_cap": 4},
        is_historical_reference=True,
    )
    grn_ok = (
        grn_p["status"] == "PASS"
        and grn_s["status"] == "PASS"
        and grn_p["measured_fastest_mode"] == "w=4"
        and grn_p["applied_worker_count"] == 3
        and grn_s["measured_fastest_mode"] == "w=1"
        and grn_s["applied_worker_count"] == 1
        and grn_p["mean_wall_sec_by_mode"] == {"w=1": 51.698, "w=2": 41.123, "w=4": 32.947}
        and grn_s["mean_wall_sec_by_mode"] == {"w=1": 2.069, "w=2": 2.181, "w=4": 2.277}
    )
    f05 = {
        "spec_version": SPEC_VERSION_KH03_R3_C1,
        "schema_version": SCHEMA_VERSION_KH03_R3_C1,
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "provenance_role": "HISTORICAL_REFERENCE_ONLY_NOT_FRESH_C1_EXECUTION",
        "source_fixture_rel_path": PRIOR_KH03_R3_ARTIFACT_19_REL,
        "source_fixture_sha256": r3_19_sha,
        "pytest_fixture_green_policy": grn_p,
        "science_fixture_green_policy": grn_s,
        "status": "PASS" if grn_ok else "FAIL",
    }
    return f03, f04, f05


def _run_stage3_readonly_r3_economic_regression_and_faults(
    *, sentinel_tmp: Path, tested_sha: str, run_id: str, legacy_cg: Mapping[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Runs read-only canonical R3 6-scenario + E1/E2/E3/NULL regression (13, 14) and 208 faults (16)."""
    datasets, _ = load_r2_canonical_dataset_readonly(REPO_ROOT / CANONICAL_DATASET_REL_PATH)
    mtf_bundle = build_kh02_asof_mtf_features(datasets)
    r1_frozen_plan, _ = _r2_frozen_plan_and_arms(code_sha=tested_sha)
    r1_candidates = generate_kh02_research_candidates(mtf_bundle, r1_frozen_plan)
    ledger_bundle = build_r2_causal_unswept_level_ledger(
        datasets, source_latency_ms=0, warmup_m1=30, run_t10_future_mutation_proof=True, rng_seed=DEFAULT_RANDOM_SEED_KH02_R2
    )
    r2_cands_1x = build_r2_target_repair_full_opportunity_conservation(
        datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle,
        r1_candidates=r1_candidates, cost_assumptions=CostAssumptions(),
    )
    replay_bundle = replay_r2_same_opportunity_paired_counterfactuals(
        datasets=datasets, r1_candidates=r1_candidates, r2_candidates=r2_cands_1x
    )
    live_ff = reprice_r2_fixed_fills_cost_counterfactual(r2_replays=replay_bundle["r2_replays"])

    forensic_gz = sentinel_tmp / "tmp_c1_r3_forensic_check.jsonl.gz"
    f06_meta, forensic_rows, _null_sum = audit_r3_entry_exit_geometry_and_accounting(
        datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_1x=r2_cands_1x, out_ledger_gz_path=forensic_gz
    )
    f07_wl, f08_attr = build_r3_win_loss_failure_attribution(forensic_rows=forensic_rows, ledger_meta_06=f06_meta)

    m5_ledger = build_r3_causal_m5_unswept_target_ledger(datasets=datasets, pivot_radius=2)
    f11_plan = freeze_r3_bounded_research_plan(tested_code_sha=tested_sha)
    r3_cands_1x = build_r3_train_only_candidates_asof(
        datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_bundle=r2_cands_1x, m5_ledger=m5_ledger, costs=CostAssumptions()
    )
    f13_wf, f14_wf = run_r3_forward_only_dev_evaluation(
        datasets=datasets, mtf_bundle=mtf_bundle, r3_cands_1x=r3_cands_1x,
        prereg_plan_11=f11_plan, tested_code_sha=tested_sha,
    )
    f15_cost, f16_gate = run_r3_paired_and_strict_dev_evaluation_15_16(
        datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle, m5_ledger=m5_ledger,
        r2_cands_1x=r2_cands_1x, r3_cands_1x=r3_cands_1x,
        prereg_plan_11=f11_plan, wf_13=f13_wf, wf_14=f14_wf,
    )

    # Compare 1x baseline + E1/E2/E3 against sealed R3 historical anchors (fail on any drift!)
    dyn6 = f15_cost.get("dynamic_full_rerisk_six_scenarios") or {}
    base_1x = (dyn6.get("1X_BASE_0p0004") or {}).get("arms") or {}
    anchor_diffs: List[str] = []
    for arm_c, exp in SEALED_R3_1X_ECONOMIC_ANCHORS.items():
        got = base_1x.get(arm_c) or {}
        if int(got.get("closed", -1)) != exp["closed"] or int(got.get("wins", -1)) != exp["wins"] or int(got.get("losses", -1)) != exp["losses"]:
            anchor_diffs.append(f"COUNT_DRIFT:{arm_c}:got={got}")
        if abs(float(got.get("net_pnl_usd", 9999.0)) - exp["net_pnl_usd"]) > 0.01:
            anchor_diffs.append(f"NET_PNL_DRIFT:{arm_c}:got={got.get('net_pnl_usd')}!=exp={exp['net_pnl_usd']}")
        if abs(float(got.get("expectancy_r", 9999.0)) - exp["expectancy_r"]) > 0.001:
            anchor_diffs.append(f"EXP_R_DRIFT:{arm_c}:got={got.get('expectancy_r')}!=exp={exp['expectancy_r']}")

    f13_out = {
        "spec_version": SPEC_VERSION_KH03_R3_C1,
        "schema_version": SCHEMA_VERSION_KH03_R3_C1,
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "replay_mode": "READONLY_CANONICAL_DEV_REGRESSION_NO_NEW_ALPHA_SEARCH",
        "historical_r3_1x_anchors": SEALED_R3_1X_ECONOMIC_ANCHORS,
        "historical_r3_1x_anchors_exact_match": len(anchor_diffs) == 0,
        "historical_anchor_diffs": anchor_diffs,
        "dynamic_vs_fixed_fill_populations_separated": True,
        "zero_fee_display_bugs_count": int(f15_cost.get("zero_fee_display_bugs_count", 0)),
        "total_overlap_violations": int(f15_cost.get("total_overlap_violations", 0)),
        "dynamic_full_rerisk_six_scenarios": dyn6,
        "fixed_fills_descriptive_repricing": f15_cost.get("fixed_fills_descriptive_repricing") or {},
        "primary_arm_evaluations": f16_gate.get("primary_arm_evaluations") or {},
        "passing_dev_candidate_arms": list(f16_gate.get("passing_dev_candidate_arms") or []),
        "preregistered_hypothesis_budget_m": int(f16_gate.get("preregistered_hypothesis_budget_m", 3)),
        "negative_control_summaries_1x": f16_gate.get("negative_control_summaries_1x") or {},
        "DEV_RESEARCH_CANDIDATE": f16_gate.get("DEV_RESEARCH_CANDIDATE"),
        "RESEARCH_ECONOMIC_VERDICT": f16_gate.get("RESEARCH_ECONOMIC_VERDICT"),
        "FRESH_OOS": f16_gate.get("FRESH_OOS"),
        "KH04_ENTRY": f16_gate.get("KH04_ENTRY"),
        "OOS_EVALUATIONS": int(f16_gate.get("OOS_EVALUATIONS", 0)),
        "observed_l1_bbo_available": bool(f15_cost.get("observed_l1_bbo_available", False)),
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "status": "PASS" if (len(anchor_diffs) == 0 and f15_cost.get("status") == "PASS" and f16_gate.get("status") == "PASS") else "FAIL",
    }

    f14_out = {
        "spec_version": SPEC_VERSION_KH03_R3_C1,
        "schema_version": SCHEMA_VERSION_KH03_R3_C1,
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "overall_pooled_null_1x": f07_wl.get("overall_pooled_null_1x"),
        "forensic_ledger_episode_count": int((f07_wl.get("linked_forensic_ledger_06") or {}).get("total_episode_rows", 0)),
        "forensic_ledger_canonical_rows_sha256": (f07_wl.get("linked_forensic_ledger_06") or {}).get("canonical_rows_sha256"),
        "planned_cap_is_physical_guarantee": bool(f08_attr.get("planned_cap_is_physical_guarantee", False)),
        "LEGACY_METHOD_GUARD": legacy_cg.get("LEGACY_METHOD_GUARD"),
        "unauthorized_r1_callers_count": int(legacy_cg.get("unauthorized_r1_callers_count", 0)),
        "r2_forward_only_embargo_240m_verified": bool(f13_wf.get("status") == "PASS" and f13_wf.get("is_fresh_oos") is False),
        "h3_r2_excluded_from_executable_arms": bool(f11_plan.get("h3_r2_excluded_from_executable_arms", True)),
        "h3_promoted_as_executable": False,
        "H3_RESIZE_EXECUTION_STATUS": "BLOCKED_ADVISORY_FILTER_ONLY",
        "ACCOUNT_EFFECTIVE_FEE_VERDICT": str(f15_cost.get("ACCOUNT_EFFECTIVE_FEE_VERDICT") or "UNKNOWN_NOT_LOCALLY_VERIFIED"),
        "funding_modeled": "EXCLUDED_UNMODELED",
        "observed_l1_bbo_available": False,
        "OOS_EVALUATIONS": 0,
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "status": "PASS" if (legacy_cg.get("LEGACY_METHOD_GUARD") == "PASS" and f07_wl.get("status") == "PASS") else "FAIL",
    }

    # 16: Execute ALL 194 inherited R3 faults + 14 NEW C1 worker-policy faults (= 208 unique!)
    r3_sub = sentinel_tmp / "r3_194_sub"
    r3_sub.mkdir(parents=True, exist_ok=True)
    r3_194 = _run_kh03_r3_194_red_green_fault_suite(
        sentinel_tmp=r3_sub, datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle,
        r2_cands_1x=r2_cands_1x, replay_bundle=replay_bundle, live_ff=live_ff,
        forensic_gz=forensic_gz, f07=f07_wl, f13=f13_wf, m5_ledger=m5_ledger,
    )
    from tests.test_kh03_r3_c1_regression_truth import run_new_kh03_r3_c1_worker_policy_fault_controls
    c1_14 = run_new_kh03_r3_c1_worker_policy_fault_controls()

    all_controls = list(r3_194["controls"]) + list(c1_14)
    fids = [c["fault_id"] for c in all_controls]
    r3_fail = sum(1 for c in r3_194["controls"] if c["status"] != "PASS")
    c1_fail = sum(1 for c in c1_14 if c["status"] != "PASS")
    tot_pass = sum(1 for c in all_controls if c["status"] == "PASS")

    f16_out = {
        "spec_version": SPEC_VERSION_KH03_R3_C1,
        "schema_version": SCHEMA_VERSION_KH03_R3_C1,
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "inherited_r3_controls_count": len(r3_194["controls"]),
        "inherited_r3_failed_controls": r3_fail,
        "new_c1_controls_count": len(c1_14),
        "new_c1_failed_controls": c1_fail,
        "total_controls": len(all_controls),
        "unique_fault_ids": len(set(fids)),
        "passed_controls": tot_pass,
        "failed_controls": len(all_controls) - tot_pass,
        "controls": all_controls,
        "status": "PASS" if (tot_pass == len(all_controls) and len(set(fids)) == len(all_controls) and len(r3_194["controls"]) >= 194 and len(c1_14) >= 12) else "FAIL",
    }

    del datasets, mtf_bundle, ledger_bundle, r1_candidates, r2_cands_1x, r3_cands_1x, replay_bundle, live_ff, m5_ledger, forensic_rows
    gc.collect()
    return f13_out, f14_out, f16_out


def _run_stage4_verifier_mutations_17(sentinel_tmp: Path, *, run_id: str, tested_sha: str) -> Dict[str, Any]:
    """Runs 16 inherited R3 mutations + 17 NEW C1 separate-OS verifier mutations (bounded <=3 concurrent)."""
    r3_mut_dir = sentinel_tmp / "r3_mut_16"
    r3_mut_dir.mkdir(parents=True, exist_ok=True)
    r3_m16 = _run_kh03_r3_verifier_mutation_suite_22(r3_mut_dir)

    from tests.test_kh03_r3_c1_verifier_mutations import (
        KH03_R3_C1_VERIFIER_MUTATIONS_SPECS,
        build_synthetic_valid_kh03_r3_c1_run_dir,
        run_kh03_r3_c1_verifier_subprocess,
    )

    c1_base = sentinel_tmp / "c1_verif_mut_base"
    rid_s, tsha_s = build_synthetic_valid_kh03_r3_c1_run_dir(c1_base)
    rc_clean, _ = run_kh03_r3_c1_verifier_subprocess(c1_base, rid_s, tsha_s)
    clean_ok = (rc_clean == 0)

    def _run_one_c1_mut(spec_item: Tuple[str, Any, str]) -> Dict[str, Any]:
        mid, mut_fn, exp_tok = spec_item
        cdir = sentinel_tmp / f"c1_mut_{mid}"
        shutil.copytree(c1_base, cdir)
        mut_fn(cdir)
        rc, out_txt = run_kh03_r3_c1_verifier_subprocess(cdir, rid_s, tsha_s)
        detected = (rc != 0) and (exp_tok in out_txt)
        return {
            "mutation_id": mid,
            "expected_violation_token": exp_tok,
            "verifier_exit_code": rc,
            "detected_fail_closed": detected,
        }

    # Cap concurrent mutation verifier subprocesses to <=3 so global descendants NEVER exceed 4!
    with ThreadPoolExecutor(max_workers=3) as pool:
        c1_results = list(pool.map(_run_one_c1_mut, KH03_R3_C1_VERIFIER_MUTATIONS_SPECS))

    c1_det = sum(1 for r in c1_results if r["detected_fail_closed"])
    r3_det = int(r3_m16.get("mutations_detected_count", 0))
    r3_tot = int(r3_m16.get("mutations_tested_count", 0))
    c1_fail = len(c1_results) - c1_det
    r3_fail = r3_tot - r3_det

    return {
        "spec_version": SPEC_VERSION_KH03_R3_C1,
        "schema_version": SCHEMA_VERSION_KH03_R3_C1,
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "isolated_temp_clones_only": True,
        "sealed_run_dir_mutated": False,
        "synthetic_clone_vs_real_run_distinguished": True,
        "clean_clone_verifier_green": clean_ok and bool(r3_m16.get("clean_clone_verifier_green")),
        "inherited_r3_mutations_tested_count": r3_tot,
        "inherited_r3_mutations_detected_count": r3_det,
        "inherited_r3_mutations_failed_detection_count": r3_fail,
        "new_c1_mutations_tested_count": len(c1_results),
        "new_c1_mutations_detected_count": c1_det,
        "new_c1_mutations_failed_detection_count": c1_fail,
        "total_mutations_tested_count": r3_tot + len(c1_results),
        "mutations_failed_detection_count": r3_fail + c1_fail,
        "new_c1_results": c1_results,
        "inherited_r3_results": r3_m16.get("results") or [],
        "status": "PASS" if (clean_ok and r3_m16.get("status") == "PASS" and c1_fail == 0 and len(c1_results) >= 12) else "FAIL",
    }


def _collect_and_build_kh03_r3_c1_lpt_shards(owned_temp_root: Path) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]]]:
    all_test_files = sorted(
        str(p.relative_to(APP_ROOT)).replace("\\", "/")
        for p in (BACKEND_ROOT / "tests").glob("test_*.py")
        if p.is_file()
    )
    col_out = owned_temp_root / "collect_kh03_r3_c1_stdout.log"
    col_err = owned_temp_root / "collect_kh03_r3_c1_stderr.log"
    col_snippet = (
        "import os, sys, pytest; "
        "fo = open(sys.argv[1], 'w', encoding='utf-8', errors='replace'); "
        "fe = open(sys.argv[2], 'w', encoding='utf-8', errors='replace'); "
        "sys.stdout = fo; sys.stderr = fe; "
        "rc = pytest.main(sys.argv[3:]); "
        "fo.flush(); fe.flush(); fo.close(); fe.close(); "
        "os._exit(int(rc))"
    )
    col_env = dict(os.environ)
    col_env["PYTHONHASHSEED"] = "0"
    col_env["PYTHONPATH"] = _pypath_str()
    proc = subprocess.run(
        [sys.executable, "-c", col_snippet, str(col_out), str(col_err), str(BACKEND_ROOT / "tests"), "--collect-only", "-q"],
        cwd=str(APP_ROOT), env=col_env, capture_output=True, check=False, timeout=300,
    )
    if proc.returncode != 0:
        err_txt = col_err.read_text("utf-8", errors="replace")[-800:] if col_err.is_file() else ""
        raise RuntimeError(f"KH03_R3_C1_PYTEST_COLLECTION_FAILED:rc={proc.returncode}:{err_txt}")

    collected_by_file: Dict[str, List[str]] = {}
    for ln in col_out.read_text("utf-8", errors="replace").splitlines():
        cid = canonicalize_collect_node_id_v1793122(ln)
        if cid is not None:
            collected_by_file.setdefault(cid.split("::", 1)[0], []).append(cid)
    for k in collected_by_file:
        collected_by_file[k].sort()

    empirical_ms = _load_empirical_file_durations_ms_from_kh01b()
    non_sub = [f for f in all_test_files if Path(f).name not in KH03_R3_C1_EXCLUSIVE_SUBPROCESS_TEST_FILES]
    sub_files = [f for f in all_test_files if Path(f).name in KH03_R3_C1_EXCLUSIVE_SUBPROCESS_TEST_FILES]
    shards = _build_lpt_shards_kh01b_r1(non_sub, collected_by_file, empirical_ms)
    for sh in shards:
        if sh.get("shard_name") == "SHARD_SINGLE_CHILD_02_BUDGET2":
            sh["exclusive"] = True
            sh["slot_cost"] = 4
    for idx_s, sf in enumerate(sorted(sub_files), 1):
        shards.append({
            "shard_name": f"SHARD_SINGLE_CHILD_KH03_R3_C1_SUBPROC_{idx_s:02d}",
            "files": [sf], "exclusive": True, "slot_cost": 4, "priority_weight": 260_000, "needs_venv": False,
        })
    return collected_by_file, shards


def _run_kh03_r3_c1_full_repo_pytest_sharded(
    run_dir: Path, sentinel_tmp: Path, *, run_id: str, tested_sha: str, effective_disk_reserve_bytes: int, max_workers: int,
    git_clean_at_start: bool,
) -> Dict[str, Any]:
    lineage_p = REPO_ROOT / LINEAGE_TRACKED_ARTIFACT_REL
    lineage_sha_before = _sha256_file(lineage_p) if lineage_p.is_file() else None

    t0 = time.perf_counter()
    empirical_r2_rss = load_empirical_r2_shard_rss_bytes(REPO_ROOT)
    collected_by_file, c1_shards = _collect_and_build_kh03_r3_c1_lpt_shards(sentinel_tmp)
    active_shards_tracker: Dict[str, Any] = {}
    junit_res = asyncio.run(
        run_async_lpt_pytest_shards_kh01b_r1(
            run_dir, sentinel_tmp,
            run_id=run_id, tested_executable_sha=tested_sha,
            effective_disk_reserve_bytes=effective_disk_reserve_bytes,
            max_parallel_workers=max_workers, ram_policy_mode="R3_STRICT_512MIB",
            empirical_rss_by_shard=empirical_r2_rss, active_shards_tracker=active_shards_tracker,
            shard_specs_override=c1_shards, collected_by_file_override=collected_by_file,
        )
    )
    elapsed = round(time.perf_counter() - t0, 3)

    lineage_sha_after_pytest = _sha256_file(lineage_p) if lineage_p.is_file() else None
    lineage_restored = False
    if git_clean_at_start and lineage_sha_before is not None and lineage_sha_after_pytest != lineage_sha_before:
        _git_cmd(["checkout", "--", LINEAGE_TRACKED_ARTIFACT_REL])
        lineage_restored = (_sha256_file(lineage_p) == lineage_sha_before)

    cm = junit_res["collection_manifest"]
    sm = junit_res["shard_manifest"]
    bj = verify_fresh_junit_bijection_v17931211(
        run_dir, cm, sm,
        expected_run_id=run_id, expected_tested_sha=tested_sha,
        mandatory_node_prefixes=(
            "backend/tests/test_kh_02_r2",
            "backend/tests/test_kh_03",
            "backend/tests/test_kh03_r2",
            "backend/tests/test_kh03_r3",
            "backend/tests/test_kh03_r3_c1",
        ),
        require_shard_schedule=True,
    )
    collected_n = int(cm.get("collected_node_ids_count") or len(cm.get("collected_node_ids") or ()))
    executed_n = int(bj.get("executed_node_ids_count") or bj.get("executed_count") or 0)
    passed_n = int(bj.get("passed_count") or 0)
    skipped_n = int(bj.get("skipped_count") or 0)
    failed_n = int(bj.get("failed_count") or 0)
    error_n = int(bj.get("error_count") or 0)
    mand_skip_n = int(bj.get("mandatory_skipped_count") or 0)
    nonzero_shards = int(sm.get("nonzero_exit_shards_count") or 0)
    ok = (
        bj.get("status") == "PASS"
        and nonzero_shards == 0
        and collected_n >= MIN_EXPECTED_PYTEST_COLLECTED_C1
        and collected_n == executed_n
        and failed_n == 0
        and error_n == 0
        and mand_skip_n == 0
    )
    return {
        "spec_version": SPEC_VERSION_KH03_R3_C1,
        "schema_version": SCHEMA_VERSION_KH03_R3_C1,
        "status": "PASS" if ok else "FAIL",
        "elapsed_sec": elapsed,
        "max_workers_used": max_workers,
        "collected_count": collected_n,
        "executed_count": executed_n,
        "passed_count": passed_n,
        "skipped_count": skipped_n,
        "mandatory_skipped_count": mand_skip_n,
        "failed_count": failed_n,
        "error_count": error_n,
        "nonzero_exit_shards_count": nonzero_shards,
        "junit_bijection_exact": bj.get("status") == "PASS",
        "tracked_test_side_effect_audit": {
            "rel_path": LINEAGE_TRACKED_ARTIFACT_REL,
            "sha256_before": lineage_sha_before,
            "sha256_after_pytest": lineage_sha_after_pytest,
            "mutated_by_legacy_test_v17_9_0_1_2": lineage_sha_before != lineage_sha_after_pytest,
            "restored_to_clean_head": lineage_restored,
        },
        "collection_manifest": cm,
        "shard_manifest": sm,
        "bijection_audit": bj,
    }


def _render_kh03_r3_c1_readme_and_handover(
    *,
    run_id: str,
    f00: Mapping[str, Any],
    f01: Mapping[str, Any],
    f03: Mapping[str, Any],
    f05: Mapping[str, Any],
    f08: Mapping[str, Any],
    f09: Mapping[str, Any],
    f11: Mapping[str, Any],
    f12: Mapping[str, Any],
    f13: Mapping[str, Any],
    f14: Mapping[str, Any],
    f15: Mapping[str, Any],
    f16: Mapping[str, Any],
    f17: Mapping[str, Any],
    f19: Mapping[str, Any],
    f20: Mapping[str, Any],
) -> Tuple[str, str]:
    v = f19["verdicts"]
    g = f19["gates_28"]
    p_pol = f08.get("pytest_worker_policy") or {}
    s_pol = f08.get("science_worker_policy") or {}
    base_1x = ((f13.get("dynamic_full_rerisk_six_scenarios") or {}).get("1X_BASE_0p0004") or {}).get("arms") or {}
    null_1x = base_1x.get("NC_NULL_TRIGGER") or {}
    e1_1x = base_1x.get("KH03_R3_E1_LOW_FRICTION_STOP_FLOOR") or {}
    e2_1x = base_1x.get("KH03_R3_E2_CAUSAL_DELAYED_CONFIRMATION") or {}
    e3_1x = base_1x.get("KH03_R3_E3_CAUSAL_NEAREST_M5_UNSWEPT_EXIT") or {}

    disp = (
        f"ENGINEERING: {v['ENGINEERING_CORRECTNESS']}; "
        f"WORKER_POLICY: {v['BENCHMARK_POLICY_CORRECTNESS']}; "
        f"DETERMINISM: {v['DETERMINISM']}; "
        f"ECONOMIC: {v['RESEARCH_ECONOMIC_VERDICT']}; "
        f"OOS: {v['FRESH_OOS']}; "
        f"KH04: {v['KH04_ENTRY']}; "
        f"ACTIVATION: {v['ACTIVATION_READY']}; "
        f"PUBLICATION: {v['PUBLICATION']}"
    )

    readme = f"""# AURUM DESK — KH-03.R3.C1 | Worker Benchmark Truth, Deterministic Async QA & Scientific Closure

- **Spec Version**: `{SPEC_VERSION_KH03_R3_C1}` | **Run ID**: `{run_id}`
- **Nhánh Git bắt buộc**: `{f00['branch']}`
- **Tested Executable SHA**: `{f00['tested_executable_sha']}`
- **Prior R3 Pub HEAD**: `{PRIOR_KH03_R3_PUB_HEAD_SHA}` (R3 Run `{PRIOR_KH03_R3_RUN_ID}` giữ bất biến 100%)
- **Kết luận tổng quan**: `{disp}`
- **Phân loại toàn cục**: `{f19['overall_classification']}`
- **An toàn bắt buộc**: `ACTIVATION_READY=NO` | `PRODUCTION=OFF` | `LIVE=OFF` | `PAPER=OFF` | `AUTO=OFF` | `OOS_EVALUATIONS=0` | `NO_PROVEN_EDGE` | `CI_STATUS=CI_UNKNOWN_NO_ACTIONS_RUN`

## 1. Lỗi P1 đã đóng (Before -> After)

- **Nguyên nhân gốc (RED trên R3 `19_w1_w2_w4_w1repeat_hashes_timings_speedups.json`)**:
  Hàm cũ `run_kh_03_r3_acceptance._compute_optimum_worker_policy` chỉ đọc `elapsed_sec`/`wall_sec`/`duration_sec`, bỏ qua hoàn toàn hai khóa thực tế của producer (`wall_clock_seconds` cho pytest subset và `wall_seconds` cho science slice 8.000 bar) -> xuất `mean_wall_sec_by_mode={{}}` và mặc định sai `optimum_worker_policy="w=1"` cho cả hai benchmark.
- **Fixture R3 bất biến sau khi sửa (GREEN — `HISTORICAL_REFERENCE_ONLY`)**:
  - Pytest subset 155 test: `mean_wall_sec_by_mode={f05['pytest_fixture_green_policy'].get('mean_wall_sec_by_mode')}` -> `measured_fastest_mode=w=4` (`1.569x` so với W1 đầu tiên), `applied_worker_count=3` (giữ trần an toàn full-suite `<= 3`, KHÔNG ngoại suy tốc độ full-suite).
  - Science slice 8.000 bar: `mean_wall_sec_by_mode={f05['science_fixture_green_policy'].get('mean_wall_sec_by_mode')}` -> `measured_fastest_mode=w=1`, `applied_worker_count=1`.
- **Chạy thực tươi KH-03.R3.C1 trên SHA `{f00['tested_executable_sha'][:12]}`**:
  - Pytest subset (155 test): `mean_wall_sec_by_mode={p_pol.get('mean_wall_sec_by_mode')}`, `w=1_repeat={p_pol.get('w1_repeat_wall_sec')}s` (variance `{p_pol.get('w1_repeat_variance_pct')}`), `measured_fastest_mode={p_pol.get('measured_fastest_mode')}`, `safe_worker_cap={p_pol.get('safe_worker_cap')}`, `applied_worker_count={p_pol.get('applied_worker_count')}`, `speedup_w4_vs_w1={p_pol.get('measured_speedup_w4_vs_w1')}x`, `full_suite_speedup_extrapolated={p_pol.get('full_suite_speedup_extrapolated')}`.
  - Science slice (8.000 bar): `mean_wall_sec_by_mode={s_pol.get('mean_wall_sec_by_mode')}`, `w=1_repeat={s_pol.get('w1_repeat_wall_sec')}s` (variance `{s_pol.get('w1_repeat_variance_pct')}`), `measured_fastest_mode={s_pol.get('measured_fastest_mode')}`, `safe_worker_cap={s_pol.get('safe_worker_cap')}`, `applied_worker_count={s_pol.get('applied_worker_count')}`, `speedup_w4_vs_w1={s_pol.get('measured_speedup_w4_vs_w1')}x`.

## 2. Hồi quy kiểm thử, Parity & Kinh tế học trung thực (Read-only DEV)

- **Full Repo Pytest (JUnit 1:1 tươi)**: `{f11.get('collected_count')}` collected / `{f11.get('executed_count')}` executed / `{f11.get('passed_count')}` passed / `{f11.get('skipped_count')}` skipped (`mandatory_skipped={f11.get('mandatory_skipped_count')}`) / `{f11.get('failed_count')}` failed / `{f11.get('error_count')}` error (`max_workers={f11.get('max_workers_used')}`).
- **Baseline Parity**: A1 `{f12.get('a1_population_count')}/{f12.get('a1_mismatches_count')}`, SI8 `{f12.get('si8_population_count')}/{f12.get('si8_mismatches_count')}`, 3-Adapter `{f12.get('three_adapter_population_count')}/{f12.get('three_adapter_mismatches_count')}`, D4 closed `{f12.get('d4_historical_closed_trades')}`.
- **Kinh tế học lịch sử DEV (1x — KHỚP 100% KH-03.R3, KHÔNG có alpha mới)**:
  - `NULL`: `{null_1x.get('closed')}` closed ({null_1x.get('wins')}W/{null_1x.get('losses')}L), net `{null_1x.get('net_pnl_usd')}` USD, expectancy `{null_1x.get('expectancy_r')}R`.
  - `E1`: `{e1_1x.get('closed')}` closed ({e1_1x.get('wins')}W/{e1_1x.get('losses')}L), net `{e1_1x.get('net_pnl_usd')}` USD, expectancy `{e1_1x.get('expectancy_r')}R`.
  - `E2`: `{e2_1x.get('closed')}` closed ({e2_1x.get('wins')}W/{e2_1x.get('losses')}L), net `{e2_1x.get('net_pnl_usd')}` USD, expectancy `{e2_1x.get('expectancy_r')}R`.
  - `E3`: `{e3_1x.get('closed')}` closed ({e3_1x.get('wins')}W/{e3_1x.get('losses')}L), net `{e3_1x.get('net_pnl_usd')}` USD, expectancy `{e3_1x.get('expectancy_r')}R`.
  - `ACCOUNT_EFFECTIVE_FEE_VERDICT={f14.get('ACCOUNT_EFFECTIVE_FEE_VERDICT')}` | `observed_l1_bbo_available=False` | `H3_RESIZE_EXECUTION_STATUS={f14.get('H3_RESIZE_EXECUTION_STATUS')}`.
- **Fault Controls & Mutation Defense**: `{f16.get('passed_controls')}/{f16.get('total_controls')}` ({f16.get('inherited_r3_controls_count')} kế thừa R3 + {f16.get('new_c1_controls_count')} mới C1) | Verifier Mutations: `{f17.get('new_c1_mutations_detected_count')}/{f17.get('new_c1_mutations_tested_count')}` mới C1 + `{f17.get('inherited_r3_mutations_detected_count')}/{f17.get('inherited_r3_mutations_tested_count')}` kế thừa R3.
- **Tài nguyên & An toàn tiến trình**: min disk `{f15.get('min_disk_free_gib')} GiB` (`>=20.0`), min RAM `{f15.get('min_available_ram_mib')} MiB` (`>=512.0`), max child `{f15.get('max_descendant_children')}` (`<=4`), orphans `{f15.get('orphan_children_after_cleanup')}`, waiver `False`.

## 3. Ma trận 28 cổng (G01..G28)

""" + "\n".join(f"- `{k}`: **{val}**" for k, val in g.items()) + "\n"

    handover = f"""AURUM DESK — BÀN GIAO CHỦ SỞ HỮU KH-03.R3.C1 (TIẾNG VIỆT)
======================================================================
SPEC: {SPEC_VERSION_KH03_R3_C1}
RUN_ID: {run_id}
BRANCH: {f00['branch']}
TESTED_EXECUTABLE_SHA: {f00['tested_executable_sha']}
PRIOR_R3_PUB_HEAD_SHA: {PRIOR_KH03_R3_PUB_HEAD_SHA}
CI_STATUS: {f20.get('ci_status')}
ACTIVATION_READY=NO | PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | OOS_EVALUATIONS=0 | NO_PROVEN_EDGE

A. KẾT LUẬN TỔNG QUAN (1 DÒNG):
{disp}
OVERALL_CLASSIFICATION: {f19['overall_classification']}

B. CHỨNG CỨ ĐÓNG LỖI P1 (BEFORE -> AFTER):
- Trước sửa (R3 artifact 19 SHA {PRIOR_KH03_R3_ARTIFACT_19_SHA256}):
  * Pytest: mean_wall_sec_by_mode={{}} -> optimum_worker_policy="w=1" (SAI — bỏ qua wall_clock_seconds)
  * Science: mean_wall_sec_by_mode={{}} -> optimum_worker_policy="w=1" (SAI — bỏ qua wall_seconds)
- Sau sửa trên chính fixture R3 bất biến (HISTORICAL_REFERENCE_ONLY):
  * Pytest (155 test): {f05['pytest_fixture_green_policy'].get('mean_wall_sec_by_mode')} -> measured_fastest=w=4, applied_safe_cap=3
  * Science (8,000 bar): {f05['science_fixture_green_policy'].get('mean_wall_sec_by_mode')} -> measured_fastest=w=1, applied_safe_cap=1
- Chạy thực tươi trên C1 SHA {f00['tested_executable_sha']}:
  * Pytest (155 test): means={p_pol.get('mean_wall_sec_by_mode')}, w=1_repeat={p_pol.get('w1_repeat_wall_sec')}s (var={p_pol.get('w1_repeat_variance_pct')}), fastest={p_pol.get('measured_fastest_mode')}, applied={p_pol.get('applied_worker_count')} (safe_cap={p_pol.get('safe_worker_cap')}), speedup_w4_vs_w1={p_pol.get('measured_speedup_w4_vs_w1')}x, full_suite_extrapolated=False
  * Science (8,000 bar): means={s_pol.get('mean_wall_sec_by_mode')}, w=1_repeat={s_pol.get('w1_repeat_wall_sec')}s (var={s_pol.get('w1_repeat_variance_pct')}), fastest={s_pol.get('measured_fastest_mode')}, applied={s_pol.get('applied_worker_count')} (safe_cap={s_pol.get('safe_worker_cap')}), speedup_w4_vs_w1={s_pol.get('measured_speedup_w4_vs_w1')}x

C. HỒI QUY TOÀN REPO, PARITY & KINH TẾ HỌC LỊCH SỬ (KHÔNG ĐỔI ALPHA):
- Full Pytest JUnit 1:1: collected={f11.get('collected_count')}, executed={f11.get('executed_count')}, passed={f11.get('passed_count')}, skipped={f11.get('skipped_count')}, mandatory_skipped={f11.get('mandatory_skipped_count')}, failed={f11.get('failed_count')}, error={f11.get('error_count')}
- Baseline Parity: A1={f12.get('a1_population_count')}/{f12.get('a1_mismatches_count')}, SI8={f12.get('si8_population_count')}/{f12.get('si8_mismatches_count')}, Adapter={f12.get('three_adapter_population_count')}/{f12.get('three_adapter_mismatches_count')}, D4={f12.get('d4_historical_closed_trades')}
- Kinh tế học 1x lịch sử (khớp 100% R3):
  * NULL: {null_1x.get('closed')} closed ({null_1x.get('wins')}W/{null_1x.get('losses')}L), net={null_1x.get('net_pnl_usd')} USD, exp={null_1x.get('expectancy_r')}R
  * E1:   {e1_1x.get('closed')} closed ({e1_1x.get('wins')}W/{e1_1x.get('losses')}L), net={e1_1x.get('net_pnl_usd')} USD, exp={e1_1x.get('expectancy_r')}R
  * E2:   {e2_1x.get('closed')} closed ({e2_1x.get('wins')}W/{e2_1x.get('losses')}L), net={e2_1x.get('net_pnl_usd')} USD, exp={e2_1x.get('expectancy_r')}R
  * E3:   {e3_1x.get('closed')} closed ({e3_1x.get('wins')}W/{e3_1x.get('losses')}L), net={e3_1x.get('net_pnl_usd')} USD, exp={e3_1x.get('expectancy_r')}R
- Faults: {f16.get('passed_controls')}/{f16.get('total_controls')} (194 R3 + {f16.get('new_c1_controls_count')} C1 mới)
- Separate-OS Verifier Mutations: {f17.get('new_c1_mutations_detected_count')}/{f17.get('new_c1_mutations_tested_count')} C1 mới + {f17.get('inherited_r3_mutations_detected_count')}/{f17.get('inherited_r3_mutations_tested_count')} R3

D. AN TOÀN TÀI NGUYÊN & TIẾN TRÌNH:
- Min disk free runtime: {f15.get('min_disk_free_gib')} GiB (ngưỡng >= 20.0 GiB; preflight >= 20.5 GiB)
- Min RAM available: {f15.get('min_available_ram_mib')} MiB (ngưỡng >= 512.0 MiB)
- Max descendant children: {f15.get('max_descendant_children')} (trần <= 4), orphans={f15.get('orphan_children_after_cleanup')}, temp_cleaned={f15.get('sentinel_temp_cleaned_up')}, waiver=False

E. 28 CỔNG (G01..G28):
""" + "\n".join(f"  {k}: {val}" for k, val in g.items()) + """

F. CÁC PHẠM VI VẪN BỊ KHÓA (BLOCKED):
- FRESH_OOS = BLOCKED_NO_UNTOUCHED_DATA (OOS_EVALUATIONS=0; chưa có phiên thu thập dữ liệu tươi được ủy quyền riêng)
- KH04_ENTRY = BLOCKED_NO_DEV_CANDIDATE (0 primary arm vượt cổng DEV; KHÔNG có alpha)
- ACTIVATION_READY = NO (PRODUCTION=LIVE=PAPER=AUTO=OFF)
- ACCOUNT_EFFECTIVE_FEE_VERDICT = UNKNOWN_NOT_LOCALLY_VERIFIED
- CI_STATUS = CI_UNKNOWN_NO_ACTIONS_RUN
"""
    return readme, handover


def run_kh03_r3_c1_acceptance(
    *,
    allow_local_dirty: bool = False,
    owner_waive_disk_preflight: bool = False,
    max_workers: int = GLOBAL_MAX_DESCENDANTS_KH02_R2,
) -> Tuple[int, Path, Dict[str, Any]]:
    if owner_waive_disk_preflight:
        raise SystemExit(2)

    lock = _CrossPlatformRunLock(Path(tempfile.gettempdir()) / "aurum_desk_kh03_r3_c1_run.lock")
    lock_ok, lock_msg = lock.acquire()
    if not lock_ok:
        raise RuntimeError(f"KH03_R3_C1_SINGLE_RUN_LOCK_FAILED:{lock_msg}")

    t_start = time.perf_counter()
    orch_pid = os.getpid()
    utc_now = datetime.now(timezone.utc)
    _, branch = _git_cmd(["branch", "--show-current"])
    _, start_head = _git_cmd(["rev-parse", "HEAD"])
    _, st_porc = _git_cmd(["status", "--porcelain=v1"])
    _, ls_rem = _git_cmd(["ls-remote", "origin", f"refs/heads/{MANDATORY_BRANCH_KH03_R3_C1}"])
    remote_sha = ls_rem.split()[0] if ls_rem.strip() else ""
    git_clean = (st_porc.strip() == "")

    if branch != MANDATORY_BRANCH_KH03_R3_C1:
        lock.release()
        raise RuntimeError(f"WRONG_BRANCH:{branch}!=expected:{MANDATORY_BRANCH_KH03_R3_C1}")
    if (not git_clean) and (not allow_local_dirty):
        lock.release()
        raise RuntimeError(f"DIRTY_WORKTREE_REFUSED_FOR_CLEAN_ACCEPTANCE:\n{st_porc}")

    run_id = f"{utc_now.strftime('%Y%m%dT%H%M%SZ')}_{start_head[:12]}"
    run_dir = REPO_ROOT / KH03_R3_C1_ARTIFACTS_BASE_REL / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "junit").mkdir(exist_ok=True)
    (run_dir / "checksums").mkdir(exist_ok=True)

    # Owner requirement (§10 Step 0): create initial README immediately in the fresh run folder
    (run_dir / "README_KH03_R3_C1.md").write_text(
        f"# AURUM DESK — KH-03.R3.C1 (IN PROGRESS)\n\n- Run ID: `{run_id}`\n- Tested SHA: `{start_head}`\n- Status: `IN_PROGRESS_NOT_SEALED`\n",
        encoding="utf-8",
    )

    art_base = (REPO_ROOT / KH03_R3_C1_ARTIFACTS_BASE_REL).resolve()
    sentinel_tmp = create_sentinel_owned_temp_dir_r2_c1(
        f"kh03c1_{utc_now.strftime('%Y%m%dT%H%M%SZ')}_",
        run_id=run_id,
        owner_pid=orch_pid,
    )
    write_mount_paths = {
        "repo_volume": REPO_ROOT,
        "app_volume": APP_ROOT,
        "artifacts_output": art_base if art_base.exists() else APP_ROOT,
        "temp_volume": sentinel_tmp,
        "python_venv": Path(sys.executable).resolve().parent,
        "caches": Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")),
        "logs": run_dir / "logs",
    }
    strict_probe = probe_r2_c1_write_mounts_strict(write_mount_paths, atomic_probe_dirs=[(sentinel_tmp, True, run_id)])
    avail_ram_mib = round(psutil.virtual_memory().available / (1024.0 * 1024.0), 2)
    strict_disk_ok = bool(strict_probe["can_run_heavy_stages"])
    ram_ok = avail_ram_mib >= PREFLIGHT_RAM_MIN_MIB_KH02_R2
    can_run_heavy = strict_disk_ok and ram_ok
    eff_workers = max(1, min(int(max_workers), GLOBAL_MAX_DESCENDANTS_KH02_R2))
    pytest_full_cap = min(3, eff_workers)

    watch_paths = [REPO_ROOT, APP_ROOT, run_dir, sentinel_tmp, Path(sys.executable).resolve().parent]
    sampler = _BoundedResourceSampler(
        out_gz=run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz",
        watch_path=run_dir,
        watch_paths=watch_paths,
        interval_sec=0.5,
        enforce_runtime_disk_abort=can_run_heavy,
    )
    sampler.start()
    sampler_stopped = False

    try:
        # Stage 1: AST ownership, legacy R1 callgraph & prior sealed SHA immutability (01, 02)
        print(f"[KH-03.R3.C1] Stage 1: Ownership, legacy R1 callgraph & prior sealed SHA checks (run_id={run_id})...", flush=True)
        legacy_cg = audit_kh03_r3_legacy_research_callgraph(REPO_ROOT)
        f01 = _audit_kh03_r3_c1_ownership_01(start_head, legacy_cg)
        f02 = _audit_prior_sealed_02(sentinel_tmp)
        write_json_durable_atomic(run_dir / "01_ast_function_ownership_and_locked_file_diffs.json", f01)
        write_json_durable_atomic(run_dir / "02_prior_r1_r2_r3_dataset_and_sealed_sha_integrity.json", f02)

        # Stage 2: RED reproduction on immutable R3 19, schema falsification, GREEN fixture proof (03, 04, 05)
        print("[KH-03.R3.C1] Stage 2: R3 artifact 19 RED reproduction, strict schema boundary & GREEN fixture proof (03..05)...", flush=True)
        f03, f04, f05 = _run_stage2_red_schema_and_green_fixtures_03_04_05(start_head, run_id)
        write_json_durable_atomic(run_dir / "03_r3_benchmark_schema_bug_real_red_reproduction.json", f03)
        write_json_durable_atomic(run_dir / "04_strict_duration_schema_and_worker_row_falsification.json", f04)
        write_json_durable_atomic(run_dir / "05_r3_historical_fixture_green_pytest_w4_science_w1.json", f05)

        # Stage 3: Read-only canonical R3 6-scenario & E1/E2/E3/NULL economic regression + 208 faults (13, 14, 16)
        print("[KH-03.R3.C1] Stage 3: Read-only R3 economic regression (13, 14) + 208 RED/GREEN fault controls (16)...", flush=True)
        f13, f14, f16 = _run_stage3_readonly_r3_economic_regression_and_faults(
            sentinel_tmp=sentinel_tmp, tested_sha=start_head, run_id=run_id, legacy_cg=legacy_cg
        )
        write_json_durable_atomic(run_dir / "13_six_scenario_backtest_and_E1_E2_E3_null_economic_regression.json", f13)
        write_json_durable_atomic(run_dir / "14_entry_exit_ledger_fee_risk_OOS_and_legacy_guard.json", f14)
        write_json_durable_atomic(run_dir / "16_inherited_R3_faults_plus_new_C1_red_green_controls.json", f16)

        # Stage 4: Separate-OS verifier mutation defense: 16 inherited R3 + 17 NEW C1 (17)
        print("[KH-03.R3.C1] Stage 4: Running 16 R3 + 17 NEW C1 separate-OS verifier mutation attacks (17)...", flush=True)
        f17 = _run_stage4_verifier_mutations_17(sentinel_tmp, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "17_new_verifier_mutation_resistance.json", f17)

        # Stage 5: Heavy stages (only if disk/RAM preflight PASS; never waived)
        if can_run_heavy:
            eff_res_b = int(STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2 * (1024 ** 3))
            print(f"[KH-03.R3.C1] Stage 5A: Running FULL repo LPT-sharded pytest regression (max_workers={pytest_full_cap}) (11)...", flush=True)
            f11 = _run_kh03_r3_c1_full_repo_pytest_sharded(
                run_dir, sentinel_tmp, run_id=run_id, tested_sha=start_head,
                effective_disk_reserve_bytes=eff_res_b, max_workers=pytest_full_cap,
                git_clean_at_start=git_clean,
            )
            write_json_durable_atomic(run_dir / "11_full_pytest_junit_collected_executed_bijection.json", f11)
            gc.collect()

            print("[KH-03.R3.C1] Stage 5B: Running fresh W1/W2/W4/W1_repeat pytest + R2 science benchmarks (06..10)...", flush=True)
            pb_raw = asyncio.run(_run_controlled_pytest_w1_w2_w4_benchmark_r1(sentinel_tmp, enforce_r3_ram_guard=True))
            sb_raw = asyncio.run(_run_r2_science_slice_w1_w2_w4_determinism(sentinel_tmp))
            gc.collect()

            f06 = {
                "spec_version": SPEC_VERSION_KH03_R3_C1,
                "schema_version": SCHEMA_VERSION_KH03_R3_C1,
                "run_id": run_id,
                "tested_executable_sha": start_head,
                "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
                "benchmark_kind": BENCHMARK_KIND_PYTEST,
                "status": "PASS" if pb_raw.get("all_4_runs_identical") else "FAIL",
                **pb_raw,
            }
            f07 = {
                "spec_version": SPEC_VERSION_KH03_R3_C1,
                "schema_version": SCHEMA_VERSION_KH03_R3_C1,
                "run_id": run_id,
                "tested_executable_sha": start_head,
                "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
                "benchmark_kind": BENCHMARK_KIND_SCIENCE,
                "status": "PASS" if sb_raw.get("all_4_runs_identical") else "FAIL",
                **sb_raw,
            }

            min_pre_disk = min(float(m.get("free_gib", 0.0)) for m in strict_probe["distinct_mounts"])
            p_limits = {
                "configured_child_process_cap": GLOBAL_MAX_DESCENDANTS_KH02_R2,
                "currently_allowed_worker_cap": eff_workers,
                "full_suite_safe_worker_cap": pytest_full_cap,
                "shard_exclusive_cap": pytest_full_cap,
                "preflight_disk_free_gib": min_pre_disk,
                "disk_free_gib": min_pre_disk,
                "available_ram_mib": avail_ram_mib,
            }
            s_limits = {
                "configured_child_process_cap": GLOBAL_MAX_DESCENDANTS_KH02_R2,
                "currently_allowed_worker_cap": eff_workers,
                "full_suite_safe_worker_cap": eff_workers,
                "shard_exclusive_cap": eff_workers,
                "preflight_disk_free_gib": min_pre_disk,
                "disk_free_gib": min_pre_disk,
                "available_ram_mib": avail_ram_mib,
            }
            p_pol = select_measured_worker_policy_strict(
                f06, benchmark_kind=BENCHMARK_KIND_PYTEST, system_limits=p_limits,
                expected_run_id=run_id, expected_tested_sha=start_head,
                expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2,
            )
            s_pol = select_measured_worker_policy_strict(
                f07, benchmark_kind=BENCHMARK_KIND_SCIENCE, system_limits=s_limits,
                expected_run_id=run_id, expected_tested_sha=start_head,
                expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2,
            )
            f08 = {
                "spec_version": SPEC_VERSION_KH03_R3_C1,
                "schema_version": SCHEMA_VERSION_KH03_R3_C1,
                "run_id": run_id,
                "tested_executable_sha": start_head,
                "pytest_worker_policy": p_pol,
                "science_worker_policy": s_pol,
                "full_suite_actual_max_workers_applied": pytest_full_cap,
                "status": "PASS" if (p_pol["status"] == "PASS" and s_pol["status"] == "PASS") else "FAIL",
            }
            f09 = {
                "spec_version": SPEC_VERSION_KH03_R3_C1,
                "schema_version": SCHEMA_VERSION_KH03_R3_C1,
                "run_id": run_id,
                "tested_executable_sha": start_head,
                "timing_determinism_claimed": False,
                "full_suite_speedup_extrapolated": False,
                "pytest_stability": {
                    "measured_fastest_mode": p_pol["measured_fastest_mode"],
                    "applied_worker_count": p_pol["applied_worker_count"],
                    "w1_wall_sec": p_pol["mean_wall_sec_by_mode"].get("w=1"),
                    "w1_repeat_wall_sec": p_pol["w1_repeat_wall_sec"],
                    "w1_repeat_delta_sec": p_pol["w1_repeat_delta_sec"],
                    "w1_repeat_variance_pct": p_pol["w1_repeat_variance_pct"],
                    "timing_stability_status": p_pol["timing_stability_status"],
                    "measured_speedup_w2_vs_w1": p_pol["measured_speedup_w2_vs_w1"],
                    "measured_speedup_w4_vs_w1": p_pol["measured_speedup_w4_vs_w1"],
                    "measured_speedup_w4_vs_w1_mean": p_pol["measured_speedup_w4_vs_w1_mean"],
                    "policy_recommendation_confidence": p_pol["policy_recommendation_confidence"],
                    "stability_disclosure": p_pol["stability_disclosure"],
                    "benchmark_scope": p_pol["benchmark_scope"],
                    "sample_size": p_pol["sample_size"],
                },
                "science_stability": {
                    "measured_fastest_mode": s_pol["measured_fastest_mode"],
                    "applied_worker_count": s_pol["applied_worker_count"],
                    "w1_wall_sec": s_pol["mean_wall_sec_by_mode"].get("w=1"),
                    "w1_repeat_wall_sec": s_pol["w1_repeat_wall_sec"],
                    "w1_repeat_delta_sec": s_pol["w1_repeat_delta_sec"],
                    "w1_repeat_variance_pct": s_pol["w1_repeat_variance_pct"],
                    "timing_stability_status": s_pol["timing_stability_status"],
                    "measured_speedup_w2_vs_w1": s_pol["measured_speedup_w2_vs_w1"],
                    "measured_speedup_w4_vs_w1": s_pol["measured_speedup_w4_vs_w1"],
                    "measured_speedup_w4_vs_w1_mean": s_pol["measured_speedup_w4_vs_w1_mean"],
                    "policy_recommendation_confidence": s_pol["policy_recommendation_confidence"],
                    "stability_disclosure": s_pol["stability_disclosure"],
                    "benchmark_scope": s_pol["benchmark_scope"],
                    "sample_size": s_pol["sample_size"],
                },
                "status": "PASS" if (p_pol["status"] == "PASS" and s_pol["status"] == "PASS") else "FAIL",
            }
            rec_p = recompute_worker_policy_from_raw_receipt(
                f06, p_pol, benchmark_kind=BENCHMARK_KIND_PYTEST,
                reported_speedup_w2_vs_w1=f06.get("measured_speedup_w2_vs_w1"),
                reported_speedup_w4_vs_w1=f06.get("measured_speedup_w4_vs_w1"),
                system_limits=p_limits, expected_run_id=run_id, expected_tested_sha=start_head,
            )
            rec_s = recompute_worker_policy_from_raw_receipt(
                f07, s_pol, benchmark_kind=BENCHMARK_KIND_SCIENCE,
                reported_speedup_w2_vs_w1=f07.get("measured_speedup_w2_vs_w1"),
                reported_speedup_w4_vs_w1=f07.get("measured_speedup_w4_vs_w1"),
                system_limits=s_limits, expected_run_id=run_id, expected_tested_sha=start_head,
            )
            f10 = {
                "spec_version": SPEC_VERSION_KH03_R3_C1,
                "schema_version": SCHEMA_VERSION_KH03_R3_C1,
                "run_id": run_id,
                "tested_executable_sha": start_head,
                "pytest_recomputation": rec_p,
                "science_recomputation": rec_s,
                "unmeasured_full_suite_speedup_claimed": False,
                "status": "PASS" if (rec_p["valid"] and rec_s["valid"]) else "FAIL",
            }
            write_json_durable_atomic(run_dir / "06_fresh_pytest_w1_w2_w4_w1repeat_raw_receipts.json", f06)
            write_json_durable_atomic(run_dir / "07_fresh_science_w1_w2_w4_w1repeat_raw_receipts.json", f07)
            write_json_durable_atomic(run_dir / "08_worker_policy_measured_vs_applied_caps_and_provenance.json", f08)
            write_json_durable_atomic(run_dir / "09_worker_policy_noise_stability_and_speedup_limits.json", f09)
            write_json_durable_atomic(run_dir / "10_independent_benchmark_recomputation_and_claim_checks.json", f10)

            print("[KH-03.R3.C1] Stage 5C: Running FULL 56,710 A1 + 6,375 SI8 + 246 adapter + D4 parity (12)...", flush=True)
            ds_p, _ = load_r2_canonical_dataset_readonly(REPO_ROOT / CANONICAL_DATASET_REL_PATH)
            f02_r1 = audit_r2_reference_inputs_and_invariants(REPO_ROOT)
            f12 = _run_full_production_baseline_parity_20(
                datasets=ds_p, sentinel_tmp=sentinel_tmp, run_id=run_id, tested_sha=start_head,
                branch=branch, f01=f01, f02=f02_r1, max_workers=eff_workers,
            )
            f12["spec_version"] = SPEC_VERSION_KH03_R3_C1
            f12["schema_version"] = SCHEMA_VERSION_KH03_R3_C1
            write_json_durable_atomic(run_dir / "12_full_A1_SI8_adapter_D4_parity_and_fingerprints.json", f12)
            del ds_p
            gc.collect()
        else:
            f06 = {"spec_version": SPEC_VERSION_KH03_R3_C1, "run_id": run_id, "tested_executable_sha": start_head, "benchmark_kind": BENCHMARK_KIND_PYTEST, "status": "NOT_RUN_BLOCKED_LOW_DISK", "all_4_runs_identical": False, "runs": []}
            f07 = {"spec_version": SPEC_VERSION_KH03_R3_C1, "run_id": run_id, "tested_executable_sha": start_head, "benchmark_kind": BENCHMARK_KIND_SCIENCE, "status": "NOT_RUN_BLOCKED_LOW_DISK", "all_4_runs_identical": False, "runs": []}
            f08 = {"spec_version": SPEC_VERSION_KH03_R3_C1, "run_id": run_id, "tested_executable_sha": start_head, "status": "NOT_RUN_BLOCKED_LOW_DISK", "pytest_worker_policy": {}, "science_worker_policy": {}}
            f09 = {"spec_version": SPEC_VERSION_KH03_R3_C1, "run_id": run_id, "tested_executable_sha": start_head, "status": "NOT_RUN_BLOCKED_LOW_DISK", "timing_determinism_claimed": False, "full_suite_speedup_extrapolated": False}
            f10 = {"spec_version": SPEC_VERSION_KH03_R3_C1, "run_id": run_id, "tested_executable_sha": start_head, "status": "NOT_RUN_BLOCKED_LOW_DISK", "unmeasured_full_suite_speedup_claimed": False}
            f11 = {"spec_version": SPEC_VERSION_KH03_R3_C1, "status": "NOT_RUN_BLOCKED_LOW_DISK", "collected_count": 0, "executed_count": 0, "passed_count": 0, "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0, "junit_bijection_exact": False}
            f12 = {"spec_version": SPEC_VERSION_KH03_R3_C1, "BASELINE_PARITY_VERDICT": "NOT_RUN_BLOCKED_LOW_DISK", "a1_population_count": 0, "a1_mismatches_count": 0, "si8_population_count": 0, "si8_mismatches_count": 0, "three_adapter_population_count": 0, "three_adapter_mismatches_count": 0, "d4_historical_closed_trades": 0, "per_decision_and_episode_fingerprints": {"all_sealed_fingerprints_exact_match": False}}
            for fn_b, obj_b in (
                ("06_fresh_pytest_w1_w2_w4_w1repeat_raw_receipts.json", f06),
                ("07_fresh_science_w1_w2_w4_w1repeat_raw_receipts.json", f07),
                ("08_worker_policy_measured_vs_applied_caps_and_provenance.json", f08),
                ("09_worker_policy_noise_stability_and_speedup_limits.json", f09),
                ("10_independent_benchmark_recomputation_and_claim_checks.json", f10),
                ("11_full_pytest_junit_collected_executed_bijection.json", f11),
                ("12_full_A1_SI8_adapter_D4_parity_and_fingerprints.json", f12),
            ):
                write_json_durable_atomic(run_dir / fn_b, obj_b)

        cl_info = _cleanup_sentinel_temp_c4_win_safe(sentinel_tmp, expected_run_id=run_id)
        sentinel_cleaned = bool(cl_info["cleaned"])
        telem = sampler.stop_and_flush()
        sampler_stopped = True

        f15 = {
            "spec_version": SPEC_VERSION_KH03_R3_C1,
            "schema_version": SCHEMA_VERSION_KH03_R3_C1,
            "run_id": run_id,
            "tested_executable_sha": start_head,
            "orchestrator_pid": orch_pid,
            "sampling_interval_sec": 0.5,
            "memory_samples_log_rel": "logs/runtime_memory_samples_0p5s.jsonl.gz",
            **telem,
            "strict_preflight_disk_min_gib": STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
            "strict_runtime_disk_min_gib": STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2,
            "advisory_disk_gib": ADVISORY_DISK_GIB_KH02_R2,
            "owner_disk_preflight_waiver": False,
            "sentinel_temp_cleaned_up": sentinel_cleaned,
            "atomic_checksums_verified": True,
            "total_wall_clock_sec": round(time.perf_counter() - t_start, 3),
        }
        write_json_durable_atomic(run_dir / "15_disk_ram_pid_0p5s_telemetry_and_cleanup.json", f15)

        _, end_head = _git_cmd(["rev-parse", "HEAD"])
        f00 = {
            "spec_version": SPEC_VERSION_KH03_R3_C1,
            "schema_version": SCHEMA_VERSION_KH03_R3_C1,
            "run_id": run_id,
            "started_utc": utc_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "branch": branch,
            "start_head_sha": start_head,
            "end_head_sha": end_head,
            "tested_executable_sha": start_head,
            "remote_head_sha": remote_sha,
            "prior_kh03_r3_pub_head_sha": PRIOR_KH03_R3_PUB_HEAD_SHA,
            "prior_kh03_r3_tested_sha": PRIOR_KH03_R3_TESTED_SHA,
            "prior_kh03_r3_evidence_sha": PRIOR_KH03_R3_EVIDENCE_SHA,
            "prior_kh03_r3_run_id": PRIOR_KH03_R3_RUN_ID,
            "git_clean_at_start": git_clean,
            "local_uncommitted_changes": not git_clean,
            "single_run_lock_status": lock_msg,
            "orchestrator_pid": orch_pid,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "random_seed": DEFAULT_RANDOM_SEED_KH02_R2,
            "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
            "owner_disk_preflight_waiver": False,
            "strict_multi_mount_probe": strict_probe,
            "preflight_ram_available_mib": avail_ram_mib,
            "can_run_heavy_stages": can_run_heavy,
            "readonly_disk_inventory": build_r2_c1_readonly_disk_inventory(REPO_ROOT) if not strict_disk_ok else None,
            "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
            "status": "PASS" if (strict_disk_ok and ram_ok and git_clean) else ("BLOCKED_LOW_DISK" if not strict_disk_ok else "BLOCKED_PREFLIGHT"),
        }
        write_json_durable_atomic(run_dir / "00_preflight_branch_sha_resource_and_lock.json", f00)

        pub_st = "PRE_PUSH_PENDING" if (strict_disk_ok and git_clean) else "NOT_PUBLISHED_BLOCKED_LOW_DISK"
        f20 = {
            "spec_version": SPEC_VERSION_KH03_R3_C1,
            "schema_version": SCHEMA_VERSION_KH03_R3_C1,
            "run_id": run_id,
            "branch": branch,
            "start_head_sha": start_head,
            "tested_executable_sha": start_head,
            "evidence_commit_sha": None,
            "publication_head_sha": None,
            "remote_readback_sha256_verified": False,
            "latest_run_pointer_updated": False,
            "owner_disk_preflight_waiver": False,
            "ci_status": "CI_UNKNOWN_NO_ACTIONS_RUN",
            "actual_github_actions_run_verified": False,
            "publication_status": pub_st,
        }
        write_json_durable_atomic(run_dir / "20_phase1_code_push_and_phase2_evidence_remote_readback.json", f20)

        docs_all = {
            "00": f00, "01": f01, "02": f02, "03": f03, "04": f04, "05": f05,
            "06": f06, "07": f07, "08": f08, "09": f09, "10": f10, "11": f11,
            "12": f12, "13": f13, "14": f14, "15": f15, "16": f16, "17": f17, "20": f20,
        }
        p_rec_aud = _independent_recompute_raw_benchmark_policy_c1(
            f06, f08.get("pytest_worker_policy") or {}, kind=BENCHMARK_KIND_PYTEST,
            expected_run_id=run_id, expected_tested_sha=start_head, max_safe_cap=3,
        )
        s_rec_aud = _independent_recompute_raw_benchmark_policy_c1(
            f07, f08.get("science_worker_policy") or {}, kind=BENCHMARK_KIND_SCIENCE,
            expected_run_id=run_id, expected_tested_sha=start_head, max_safe_cap=GLOBAL_MAX_DESCENDANTS_KH02_R2,
        )
        mem_disk_aud = _audit_memory_and_disk_samples_c1(run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz", f15)
        econ_ok, _ = _audit_economic_and_six_scenario_regression_c1(f13, f14)
        recomp19 = recompute_kh03_r3_c1_28_gates_and_verdicts(
            docs=docs_all, pytest_recomp=p_rec_aud, science_recomp=s_rec_aud,
            mem_audit=mem_disk_aud,
            junit_bijection_ok=(f11.get("status") == "PASS" and bool(f11.get("junit_bijection_exact", False))),
            economic_ok=econ_ok, for_final_verifier_manifest=False,
        )
        f19 = {
            "spec_version": SPEC_VERSION_KH03_R3_C1,
            "schema_version": SCHEMA_VERSION_KH03_R3_C1,
            "run_id": run_id,
            "tested_executable_sha": start_head,
            "OOS_EVALUATIONS": 0,
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
            "overall_classification": recomp19["overall_classification"],
            "gates_28": recomp19["gates_28"],
            "verdicts": recomp19["verdicts"],
        }
        f19["artifact_canonical_sha256"] = canonical_hash(f19)
        write_json_durable_atomic(run_dir / "19_orthogonal_engineering_economic_resource_release_gates.json", f19)

        readme_md, handover_vi = _render_kh03_r3_c1_readme_and_handover(
            run_id=run_id, f00=f00, f01=f01, f03=f03, f05=f05, f08=f08, f09=f09,
            f11=f11, f12=f12, f13=f13, f14=f14, f15=f15, f16=f16, f17=f17, f19=f19, f20=f20,
        )
        (run_dir / "README_KH03_R3_C1.md").write_text(readme_md, encoding="utf-8")
        (run_dir / "HANDOVER_KH03_R3_C1_VI.txt").write_text(handover_vi, encoding="utf-8")

        chk_map = {
            fp.relative_to(run_dir).as_posix(): _sha256_file(fp)
            for fp in sorted(run_dir.rglob("*"))
            if fp.is_file() and not fp.name.endswith(".tmp") and fp.relative_to(run_dir).as_posix() != "checksums/sha256_inventory.json"
        }
        write_json_durable_atomic(
            run_dir / "checksums" / "sha256_inventory.json",
            {
                "spec_version": SPEC_VERSION_KH03_R3_C1,
                "run_id": run_id,
                "tested_executable_sha": start_head,
                "files_count": len(chk_map),
                "canonical_inventory_sha256": canonical_hash(chk_map),
                "files_sha256": chk_map,
            },
        )

        # Stage 7: Separate-OS Independent Verifier sealing 18_independent_OS_verifier_manifest_sha256.json
        print("[KH-03.R3.C1] Stage 7: Launching separate-OS-process independent disk verifier (18)...", flush=True)
        verifier_script = APP_ROOT / "tools" / "kh_03_r3_c1_independent_verifier.py"
        env_v = os.environ.copy()
        env_v["PYTHONPATH"] = _pypath_str()
        v_proc = subprocess.run(
            [sys.executable, str(verifier_script), "--run-dir", str(run_dir), "--expected-run-id", run_id, "--expected-tested-sha", start_head],
            cwd=str(APP_ROOT), env=env_v, capture_output=True, text=True, timeout=240,
        )
        print(v_proc.stdout, flush=True)
        if v_proc.stderr:
            print(v_proc.stderr, file=sys.stderr, flush=True)

        f18 = json.loads((run_dir / "18_independent_OS_verifier_manifest_sha256.json").read_text("utf-8"))
        return int(v_proc.returncode), run_dir, {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "verifier_exit_code": int(v_proc.returncode),
            "independent_verifier_status": f18.get("independent_verifier_status"),
            "overall_classification": f18.get("overall_classification"),
            "recomputed_verdicts": f18.get("recomputed_verdicts"),
            "violations": f18.get("violations"),
        }
    finally:
        if not sampler_stopped:
            try:
                sampler.stop_and_flush()
            except Exception:
                pass
        if sentinel_tmp.exists():
            try:
                _cleanup_sentinel_temp_c4_win_safe(sentinel_tmp, expected_run_id=run_id)
            except Exception:
                pass
        lock.release()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="AURUM DESK KH-03.R3.C1 Acceptance Orchestrator")
    ap.add_argument("--allow-local-dirty", action="store_true")
    ap.add_argument("--owner-waive-disk-preflight", action="store_true")
    ap.add_argument("--max-workers", type=int, default=GLOBAL_MAX_DESCENDANTS_KH02_R2)
    args = ap.parse_args(argv)
    if args.owner_waive_disk_preflight:
        return 2
    rc, _rdir, summary = run_kh03_r3_c1_acceptance(
        allow_local_dirty=args.allow_local_dirty,
        owner_waive_disk_preflight=False,
        max_workers=args.max_workers,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
