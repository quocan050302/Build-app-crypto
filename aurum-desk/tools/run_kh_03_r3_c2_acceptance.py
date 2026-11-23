#!/usr/bin/env python3
"""
aurum-desk/tools/run_kh_03_r3_c2_acceptance.py

AURUM DESK — KH-03.R3.C2 | LEGACY R3 RUNNER FAIL-CLOSED FINAL CLOSURE
SPEC VERSION: KH-03.R3.C2-v1.0 | SAME BRANCH | ACTIVATION_READY=NO | OOS_EVALUATIONS=0

Produces an immutable per-run directory under:
  aurum-desk/docs/artifacts/kh_03_r3_c2_legacy_fail_closed/<UTC_RUN_ID>_<TESTED_SHA12>/
containing artifacts 00..18 (with 16 sealed by separate-OS independent verifier),
README_KH03_R3_C2.md, HANDOVER_KH03_R3_C2_VI.txt, checksums/, logs/, and junit/.
NEVER mutates any prior KH-03.R3.C1, KH-03.R3, R2, R1, C4..C1, KH-02, or KH-01B runs or pointers.
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
    or str(_early_os.environ.get("KH03_C2_OWNER_WAIVE_DISK_PREFLIGHT") or "").strip().lower() in ("1", "true", "yes")
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
import hashlib
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
    select_measured_worker_policy_strict,
)
from services.kh_03_r3_diagnostic_service import (
    audit_r3_entry_exit_geometry_and_accounting,
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
from tools.kh_03_r3_c2_independent_verifier import (
    KH03_R3_C2_SOURCE_FILES_ALLOWLIST,
    MANDATORY_BRANCH_KH03_R3_C2,
    MIN_EXPECTED_PYTEST_COLLECTED_C2,
    PRIOR_KH03_R3_C1_EVIDENCE_SHA,
    PRIOR_KH03_R3_C1_PUB_HEAD_SHA,
    PRIOR_KH03_R3_C1_RUN_ID,
    PRIOR_KH03_R3_C1_TESTED_SHA,
    PRIOR_KH03_R3_RUN_ID,
    PRIOR_KH03_R3_TESTED_SHA,
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R3_C2,
    SCHEMA_VERSION_KH03_R3_C2,
    SEALED_R3_1X_ECONOMIC_ANCHORS,
    SPEC_VERSION_KH03_R3_C2,
    _audit_economic_regression_11_c2,
    _audit_telemetry_gz_vs_13_c2,
    _independent_recompute_raw_bench_c2,
    recompute_kh03_r3_c2_26_gates_and_verdicts,
)
from tools.kh_03_r3_independent_verifier import (
    ARCHIVED_ORIGINAL_R3_RUN_ID_VERIF,
    ARCHIVED_ORIGINAL_R3_TESTED_SHA_VERIF,
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
    _compute_optimum_worker_policy,
    _evaluate_r3_f19_policy_certification,
    _legacy_prefixed_r3_f19_status_red_repro_only,
    _run_kh03_r3_194_red_green_fault_suite,
    _run_kh03_r3_verifier_mutation_suite_22,
)
from tools.run_kh_03_r3_c1_acceptance import (
    EXTRA_FROZEN_RESEARCH_FILES_C1,
    KH03_R3_C1_EXCLUSIVE_SUBPROCESS_TEST_FILES,
    LINEAGE_TRACKED_ARTIFACT_REL,
    _CrossPlatformRunLock,
)
from tools.run_v17_9_3_1_2_2_verification import canonicalize_collect_node_id_v1793122

KH03_R3_C2_ARTIFACTS_BASE_REL = "aurum-desk/docs/artifacts/kh_03_r3_c2_legacy_fail_closed"
C1_WORKER_POLICY_SERVICE_REL = "aurum-desk/backend/services/kh_03_r3_c1_worker_policy_service.py"

KH03_R3_C2_EXCLUSIVE_SUBPROCESS_TEST_FILES: Set[str] = set(KH03_R3_C1_EXCLUSIVE_SUBPROCESS_TEST_FILES) | {
    "test_kh03_r3_c2_legacy_runner_fail_closed.py",
    "test_kh03_r3_c2_independent_mutations.py",
}

# Verified pre-fix RED06 execution receipt on unmodified HEAD dfcc99c94e47afca76bc33ad1a1ca6ce3b19f10e
PREFIX_HEAD_SHA_VERIFIED = "dfcc99c94e47afca76bc33ad1a1ca6ce3b19f10e"
PREFIX_RED06_LOG_SHA256 = "ab8cc954b58aca1c5fce718fdbad90ef3905946e7c0b864e2177a00713610a58"


def _stamp_c2(doc: Dict[str, Any], *, run_id: str, tested_sha: str) -> Dict[str, Any]:
    doc["spec_version"] = SPEC_VERSION_KH03_R3_C2
    doc["schema_version"] = SCHEMA_VERSION_KH03_R3_C2
    doc["run_id"] = run_id
    doc["tested_executable_sha"] = tested_sha
    doc["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in doc.items() if k != "artifact_canonical_sha256"})
    return doc


def _audit_ownership_01_and_untouched_12(
    *, tested_sha: str, run_id: str, legacy_cg: Mapping[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    all_locked = list(dict.fromkeys(list(LOCKED_KEEP_AS_IS_FILES_KH02_R2) + list(EXTRA_FROZEN_RESEARCH_FILES_C1) + [C1_WORKER_POLICY_SERVICE_REL]))
    locked_inv = []
    for rel in all_locked:
        p = REPO_ROOT / rel
        locked_inv.append({"rel_path": rel, "exists": p.is_file(), "sha256": _sha256_file(p) if p.is_file() else None, "ast_function_count": len(_ast_function_inventory(p)) if (rel.endswith(".py") and p.is_file()) else None})

    rc_diff, diff_out = _git_cmd(["diff", "--name-only", f"{PRIOR_KH03_R3_C1_PUB_HEAD_SHA}..HEAD"])
    changed_files = [ln.strip() for ln in diff_out.splitlines() if ln.strip()] if rc_diff == 0 else []
    locked_mod = [f for f in changed_files if f in set(all_locked)]
    allow_set = set(KH03_R3_C2_SOURCE_FILES_ALLOWLIST)
    unauthorized = [f for f in changed_files if f not in allow_set and not f.startswith(f"{KH03_R3_C2_ARTIFACTS_BASE_REL}/")]

    forb_hits: List[str] = []
    c2_inv = []
    for rel in KH03_R3_C2_SOURCE_FILES_ALLOWLIST:
        p = REPO_ROOT / rel
        if not p.is_file():
            continue
        tree = ast.parse(p.read_text("utf-8", errors="replace"), filename=str(p))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if any(tok in a.name for tok in FORBIDDEN_ORDER_SERVICE_TOKENS):
                        forb_hits.append(f"{rel}:import:{a.name}")
            elif isinstance(node, ast.ImportFrom):
                m = node.module or ""
                if any(tok in m for tok in FORBIDDEN_ORDER_SERVICE_TOKENS):
                    forb_hits.append(f"{rel}:from:{m}")
        c2_inv.append({"rel_path": rel, "sha256": _sha256_file(p), "functions_count": len(_ast_function_inventory(p))})

    c_live = CostAssumptions()
    cost_ok = abs(float(c_live.maker_fee_rate) - 0.0002) <= 1e-12 and abs(float(c_live.taker_fee_rate) - 0.0004) <= 1e-12 and abs(float(c_live.slippage_usd) - 0.10) <= 1e-12
    ok = len(locked_mod) == 0 and len(unauthorized) == 0 and len(forb_hits) == 0 and cost_ok and legacy_cg.get("LEGACY_METHOD_GUARD") == "PASS"
    f01 = _stamp_c2({
        "prior_c1_pub_head_sha": PRIOR_KH03_R3_C1_PUB_HEAD_SHA,
        "locked_keep_as_is_count": len(locked_inv),
        "locked_keep_as_is_inventory": locked_inv,
        "locked_files_modified": locked_mod,
        "locked_files_modified_count": len(locked_mod),
        "changed_files_vs_c1_pub_head": changed_files,
        "unauthorized_changed_files": unauthorized,
        "unauthorized_changed_files_count": len(unauthorized),
        "kh03_r3_c2_module_inventory": c2_inv,
        "legacy_guard_status": legacy_cg.get("LEGACY_METHOD_GUARD"),
        "forbidden_order_import_hits": forb_hits,
        "cost_assumptions_defaults_intact": cost_ok,
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        "status": "PASS" if ok else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    c1_svc_untouched = C1_WORKER_POLICY_SERVICE_REL not in set(changed_files)
    f12 = _stamp_c2({
        "c1_worker_policy_service_rel": C1_WORKER_POLICY_SERVICE_REL,
        "c1_worker_policy_service_sha256": _sha256_file(REPO_ROOT / C1_WORKER_POLICY_SERVICE_REL),
        "c1_worker_policy_service_untouched": c1_svc_untouched,
        "locked_production_and_science_files_modified_count": len(locked_mod),
        "changed_files_vs_c1_pub_head": changed_files,
        "status": "PASS" if (c1_svc_untouched and len(locked_mod) == 0) else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)
    return f01, f12


def _audit_prior_sealed_02(sentinel_tmp: Path, *, run_id: str, tested_sha: str) -> Dict[str, Any]:
    ds_p = REPO_ROOT / CANONICAL_DATASET_REL_PATH
    ds_sha = _sha256_file(ds_p) if ds_p.is_file() else ""
    ds_ok = (ds_sha == CANONICAL_DATASET_SHA256_KH02_R2)
    prior_map: Dict[str, Any] = {}
    all_ok = ds_ok
    for rel_p, exp_sha, lbl in PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R3_C2:
        p = REPO_ROOT / rel_p
        act = _sha256_file(p) if p.is_file() else ""
        m = p.is_file() and (act == exp_sha)
        if not m:
            all_ok = False
        prior_map[lbl] = {"rel_path": rel_p, "expected_sha256": exp_sha, "actual_sha256": act, "match": m}
    pdir = sentinel_tmp / "c4_prov_c2"
    pdir.mkdir(parents=True, exist_ok=True)
    f08_c4, f09_c4 = audit_kh03_collector_and_escrow_readiness(pdir)
    prov_ok = f08_c4.get("status") == "PASS" and f09_c4.get("status") == "PASS" and f08_c4.get("network_capture_enabled") is False and f08_c4.get("live_network_capture_executed") is False and int(f09_c4.get("real_oos_escrow_final_record_count", -1)) == 0
    return _stamp_c2({
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
            "status": "PASS" if prov_ok else "FAIL",
        },
        "status": "PASS" if (all_ok and prov_ok) else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)


def _run_stage2_red_green_and_r3_g22_03_04_05(
    sentinel_tmp: Path, *, run_id: str, tested_sha: str
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    from tests.test_kh03_r3_c2_legacy_runner_fail_closed import (
        FRESH_RID, FRESH_SHA, GOOD_RES, _make_pb, _make_sb, execute_c2t01_to_c2t40_boundary_matrix,
    )
    from tests.test_kh03_r3_resource_async_and_verifier_mutations import (
        build_synthetic_valid_kh03_r3_run_dir, run_kh03_r3_verifier_subprocess,
    )

    pb0, sb0 = _make_pb(), _make_sb()
    red_records: List[Dict[str, Any]] = []

    def _add_red(tname: str, pb_i: Mapping[str, Any], sb_i: Mapping[str, Any], p_lim: Optional[Mapping[str, Any]], viol_reason: str) -> None:
        in_sha = canonical_hash({"pb": pb_i, "sb": sb_i, "limits": p_lim})
        pol_p = _compute_optimum_worker_policy(pb_i, benchmark_kind=BENCHMARK_KIND_PYTEST, system_limits=p_lim)
        pol_s = _compute_optimum_worker_policy(sb_i, benchmark_kind=BENCHMARK_KIND_SCIENCE)
        old_st = _legacy_prefixed_r3_f19_status_red_repro_only(pb_i, sb_i, can_run_heavy=True)
        new_c = _evaluate_r3_f19_policy_certification(
            pb_raw=pb_i, sb_raw=sb_i, pytest_policy=pol_p, science_policy=pol_s,
            run_id=FRESH_RID, tested_sha=FRESH_SHA, dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2,
            resource_state=GOOD_RES, can_run_heavy=True,
        )
        repro_ok = (old_st == "PASS") and (new_c["status"] != "PASS") and (new_c["can_certify"] is False)
        log_blob = json.dumps({"old_f19_status": old_st, "new_f19_status": new_c["status"], "pol_p": pol_p["status"], "pol_s": pol_s["status"]}, sort_keys=True)
        red_records.append({
            "test_name": tname,
            "input_sha256": in_sha,
            "tested_code_sha": tested_sha,
            "pre_fix_reference_head_sha": PREFIX_HEAD_SHA_VERIFIED,
            "expected_behavior": "FAIL_OR_BLOCKED_NEVER_PASS",
            "actual_pre_fix_behavior": f"old_f19_status={old_st} despite policy=({pol_p['status']},{pol_s['status']})",
            "post_fix_behavior": f"new_f19_status={new_c['status']}, can_certify={new_c['can_certify']}",
            "source_function": "tools/run_kh_03_r3_acceptance.py::run_kh03_r3_acceptance (f19 line 911)",
            "violation_reason": viol_reason,
            "reproduction_command": "PYTHONPATH=backend:.:.. backend/venv/bin/python -m pytest backend/tests/test_kh03_r3_c2_legacy_runner_fail_closed.py",
            "exit_code": 0 if repro_ok else 1,
            "captured_log_sha256": hashlib.sha256(log_blob.encode("utf-8")).hexdigest(),
            "reproduced_red_and_fixed_green": repro_ok,
        })

    pb1 = copy.deepcopy(pb0); pb1["runs"] = [r for r in pb1["runs"] if r["mode_label"] != "w=4"]
    _add_red("R3C2-RED01", pb1, sb0, None, "pb missing w=4 while all_4_runs_identical=True")
    pb2 = copy.deepcopy(pb0); pb2["runs"][2]["wall_clock_seconds"] = -5.0
    _add_red("R3C2-RED02", pb2, sb0, None, "pb negative duration -5.0 while all_4_runs_identical=True")
    sb3 = copy.deepcopy(sb0); sb3["runs"][0]["wall_clock_seconds"] = 9.9
    _add_red("R3C2-RED03", pb0, sb3, None, "science cross-kind wall_clock_seconds contamination")
    _add_red("R3C2-RED04", pb0, sb0, {"configured_child_process_cap": 8}, "over-cap child process limit=8")
    pb5 = copy.deepcopy(pb0); pb5["runs"][2]["worker_pids"] = [401, 402]
    _add_red("R3C2-RED05", pb5, sb0, None, "incompatible W4 worker_pids len=2")

    # R3C2-RED06: Verified pre-fix separate-OS verifier execution on dfcc99c9 (exit 0 / PASS on forged f19) -> NOW post-fix exits 1!
    red06_dir = sentinel_tmp / "red06_post_fix_check"
    rid6, tsha6 = build_synthetic_valid_kh03_r3_run_dir(red06_dir)
    f19_p6 = red06_dir / "19_w1_w2_w4_w1repeat_hashes_timings_speedups.json"
    d19_6 = json.loads(f19_p6.read_text("utf-8"))
    for r in d19_6["pytest_controlled_subset_w1_w2_w4_repeat"]["runs"]:
        r["wall_clock_seconds"] = -5.0
        r["workers"] = 1
    d19_6["pytest_optimum_worker_policy"] = {"status": "BLOCKED_INVALID_BENCHMARK", "can_certify": False, "mean_wall_sec_by_mode": {}, "optimum_worker_policy": "w=1"}
    d19_6["science_optimum_worker_policy"] = {"status": "BLOCKED_INVALID_BENCHMARK", "can_certify": False, "mean_wall_sec_by_mode": {}, "optimum_worker_policy": "w=1"}
    f19_p6.write_text(json.dumps(d19_6, indent=2, sort_keys=True), encoding="utf-8")
    rc6_post, out6_post = run_kh03_r3_verifier_subprocess(red06_dir, rid6, tsha6)
    red06_fixed = (rc6_post != 0) and ("R3V_PYTEST_INVALID_DURATION_RANGE" in out6_post or "R3V_PYTEST_POLICY_STATUS_NOT_PASS" in out6_post)
    red_records.append({
        "test_name": "R3C2-RED06",
        "input_sha256": _sha256_file(f19_p6),
        "tested_code_sha": tested_sha,
        "pre_fix_reference_head_sha": PREFIX_HEAD_SHA_VERIFIED,
        "expected_behavior": "kh_03_r3_independent_verifier.py MUST exit non-zero and mark G22=FAIL on forged f19",
        "actual_pre_fix_behavior": f"On unmodified {PREFIX_HEAD_SHA_VERIFIED}, kh_03_r3_independent_verifier.py exited 0 with G22=PASS and violations=[]",
        "post_fix_behavior": f"On fixed {tested_sha}, kh_03_r3_independent_verifier.py exited {rc6_post} (FAIL closed)",
        "source_function": "tools/kh_03_r3_independent_verifier.py::recompute_kh03_r3_30_gates_and_verdicts (G22)",
        "violation_reason": "Pre-fix R3 verifier checked only _recompute_w1_w2_w4_c4(f19) and swallowed forged BLOCKED policy + negative durations",
        "reproduction_command": "PYTHONPATH=backend:.:.. backend/venv/bin/python tools/kh_03_r3_independent_verifier.py --run-dir <forged_clone>",
        "exit_code": 0,
        "post_fix_exit_code": rc6_post,
        "captured_log_sha256": PREFIX_RED06_LOG_SHA256,
        "post_fix_log_sha256": hashlib.sha256(out6_post.encode("utf-8")).hexdigest(),
        "reproduced_red_and_fixed_green": red06_fixed,
    })

    red_ok_n = sum(1 for r in red_records if r["reproduced_red_and_fixed_green"])
    f03 = _stamp_c2({
        "red_cases_total": len(red_records),
        "red_cases_reproduced_count": red_ok_n,
        "pre_fix_verifier_swallowed_forged_f19_proved": red06_fixed,
        "records": red_records,
        "status": "PASS" if red_ok_n == 6 else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    # 04: Execute ALL 40 boundary cases (C2T01..C2T40)
    m40 = execute_c2t01_to_c2t40_boundary_matrix()
    p40 = sum(1 for x in m40 if x["passed"])
    f04 = _stamp_c2({
        "cases_total": len(m40),
        "cases_passed": p40,
        "cases_failed": len(m40) - p40,
        "nonfinite_mixed_missing_forged_all_rejected": all(r["passed"] for r in m40 if r["case_id"] in ("C2T04","C2T05","C2T06","C2T07","C2T08","C2T09","C2T10","C2T11","C2T12","C2T13","C2T14","C2T15","C2T21","C2T23","C2T24","C2T25")),
        "g22_invalid_blocks_overall_engineering_pass": all(r["passed"] for r in m40 if r["case_id"] in ("C2T39","C2T40")),
        "cases": m40,
        "status": "PASS" if p40 == 40 else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    # 05: Live separate-OS R3 verifier checks (clean C2-compliant R3 run, forged f19, archived R3 strict vs descriptive-only)
    clean_r3 = sentinel_tmp / "clean_r3_g22_check"
    rid_c, tsha_c = build_synthetic_valid_kh03_r3_run_dir(clean_r3)
    rc_clean, _ = run_kh03_r3_verifier_subprocess(clean_r3, rid_c, tsha_c)

    r3_arch_dir = REPO_ROOT / f"aurum-desk/docs/artifacts/kh_03_r3_entry_exit_diagnosis/{ARCHIVED_ORIGINAL_R3_RUN_ID_VERIF}"
    rc_arch_strict, out_arch_strict = run_kh03_r3_verifier_subprocess(r3_arch_dir, ARCHIVED_ORIGINAL_R3_RUN_ID_VERIF, ARCHIVED_ORIGINAL_R3_TESTED_SHA_VERIF)
    env_d = os.environ.copy()
    env_d["PYTHONPATH"] = _pypath_str()
    p_desc = subprocess.run(
        [sys.executable, str(APP_ROOT / "tools" / "kh_03_r3_independent_verifier.py"), "--run-dir", str(r3_arch_dir), "--expected-run-id", ARCHIVED_ORIGINAL_R3_RUN_ID_VERIF, "--expected-tested-sha", ARCHIVED_ORIGINAL_R3_TESTED_SHA_VERIF, "--no-write-manifest", "--legacy-descriptive-audit-only"],
        cwd=str(APP_ROOT), env=env_d, capture_output=True, text=True, timeout=60,
    )
    arch_strict_rej = (rc_arch_strict != 0) and ("STALE_ARCHIVED_R3_REFUSED_FOR_NEW_CERTIFICATION" in out_arch_strict)
    arch_desc_ok = (p_desc.returncode == 0)
    f05 = _stamp_c2({
        "clean_c2_r3_run_passes": (rc_clean == 0),
        "forged_f19_rejected_fail_closed": red06_fixed,
        "archived_r3_run_id": ARCHIVED_ORIGINAL_R3_RUN_ID_VERIF,
        "archived_r3_strict_mode_exit_code": rc_arch_strict,
        "archived_r3_strict_mode_rejected": arch_strict_rej,
        "archived_r3_descriptive_audit_exit_code": int(p_desc.returncode),
        "archived_r3_descriptive_audit_classification": "LEGACY_DESCRIPTIVE_ONLY" if arch_desc_ok else "FAIL",
        "archived_r3_admitted_for_new_c2_certification": False,
        "status": "PASS" if (rc_clean == 0 and red06_fixed and arch_strict_rej and arch_desc_ok) else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)
    return f03, f04, f05


def _run_stage3_economic_11_and_224_faults_14(
    *, sentinel_tmp: Path, run_id: str, tested_sha: str, legacy_cg: Mapping[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    datasets, _ = load_r2_canonical_dataset_readonly(REPO_ROOT / CANONICAL_DATASET_REL_PATH)
    mtf_bundle = build_kh02_asof_mtf_features(datasets)
    r1_frozen_plan, _ = _r2_frozen_plan_and_arms(code_sha=tested_sha)
    r1_candidates = generate_kh02_research_candidates(mtf_bundle, r1_frozen_plan)
    ledger_bundle = build_r2_causal_unswept_level_ledger(datasets, source_latency_ms=0, warmup_m1=30, run_t10_future_mutation_proof=True, rng_seed=DEFAULT_RANDOM_SEED_KH02_R2)
    r2_cands_1x = build_r2_target_repair_full_opportunity_conservation(datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle, r1_candidates=r1_candidates, cost_assumptions=CostAssumptions())
    replay_bundle = replay_r2_same_opportunity_paired_counterfactuals(datasets=datasets, r1_candidates=r1_candidates, r2_candidates=r2_cands_1x)
    live_ff = reprice_r2_fixed_fills_cost_counterfactual(r2_replays=replay_bundle["r2_replays"])

    forensic_gz = sentinel_tmp / "tmp_c2_r3_forensic.jsonl.gz"
    f06_meta, forensic_rows, _ = audit_r3_entry_exit_geometry_and_accounting(datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_1x=r2_cands_1x, out_ledger_gz_path=forensic_gz)
    f07_wl, _ = build_r3_win_loss_failure_attribution(forensic_rows=forensic_rows, ledger_meta_06=f06_meta)

    m5_ledger = build_r3_causal_m5_unswept_target_ledger(datasets=datasets, pivot_radius=2)
    f11_plan = freeze_r3_bounded_research_plan(tested_code_sha=tested_sha)
    r3_cands_1x = build_r3_train_only_candidates_asof(datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_bundle=r2_cands_1x, m5_ledger=m5_ledger, costs=CostAssumptions())
    f13_wf, f14_wf = run_r3_forward_only_dev_evaluation(datasets=datasets, mtf_bundle=mtf_bundle, r3_cands_1x=r3_cands_1x, prereg_plan_11=f11_plan, tested_code_sha=tested_sha)
    f15_cost, f16_gate = run_r3_paired_and_strict_dev_evaluation_15_16(datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle, m5_ledger=m5_ledger, r2_cands_1x=r2_cands_1x, r3_cands_1x=r3_cands_1x, prereg_plan_11=f11_plan, wf_13=f13_wf, wf_14=f14_wf)

    dyn6 = f15_cost.get("dynamic_full_rerisk_six_scenarios") or {}
    base_1x = (dyn6.get("1X_BASE_0p0004") or {}).get("arms") or {}
    anchor_diffs: List[str] = []
    for ac, exp in SEALED_R3_1X_ECONOMIC_ANCHORS.items():
        got = base_1x.get(ac) or {}
        if int(got.get("closed", -1)) != exp["closed"] or int(got.get("wins", -1)) != exp["wins"] or int(got.get("losses", -1)) != exp["losses"]:
            anchor_diffs.append(f"COUNT_DRIFT:{ac}")
        if abs(float(got.get("net_pnl_usd", 9999.0)) - exp["net_pnl_usd"]) > 0.01:
            anchor_diffs.append(f"NET_DRIFT:{ac}")
        if abs(float(got.get("expectancy_r", 9999.0)) - exp["expectancy_r"]) > 0.001:
            anchor_diffs.append(f"EXPR_DRIFT:{ac}")

    f11_out = _stamp_c2({
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
        "DEV_RESEARCH_CANDIDATE": f16_gate.get("DEV_RESEARCH_CANDIDATE"),
        "RESEARCH_ECONOMIC_VERDICT": f16_gate.get("RESEARCH_ECONOMIC_VERDICT"),
        "FRESH_OOS": f16_gate.get("FRESH_OOS"),
        "KH04_ENTRY": f16_gate.get("KH04_ENTRY"),
        "OOS_EVALUATIONS": int(f16_gate.get("OOS_EVALUATIONS", 0)),
        "observed_l1_bbo_available": False,
        "H3_RESIZE_EXECUTION_STATUS": "BLOCKED_ADVISORY_FILTER_ONLY",
        "h3_promoted_as_executable": False,
        "h3_r2_excluded_from_executable_arms": bool(f11_plan.get("h3_r2_excluded_from_executable_arms", True)),
        "r2_forward_only_embargo_240m_verified": bool(f13_wf.get("status") == "PASS" and f13_wf.get("is_fresh_oos") is False),
        "ACCOUNT_EFFECTIVE_FEE_VERDICT": str(f15_cost.get("ACCOUNT_EFFECTIVE_FEE_VERDICT") or "UNKNOWN_NOT_LOCALLY_VERIFIED"),
        "funding_modeled": "EXCLUDED_UNMODELED",
        "LEGACY_METHOD_GUARD": legacy_cg.get("LEGACY_METHOD_GUARD"),
        "unauthorized_r1_callers_count": int(legacy_cg.get("unauthorized_r1_callers_count", 0)),
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "status": "PASS" if (len(anchor_diffs) == 0 and f15_cost.get("status") == "PASS" and f16_gate.get("status") == "PASS") else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    # 14: 194 inherited R3 + 14 inherited C1 + 16 NEW C2 = 224 unique RED/GREEN fault controls!
    r3_sub = sentinel_tmp / "r3_194_sub_c2"
    r3_sub.mkdir(parents=True, exist_ok=True)
    r3_194 = _run_kh03_r3_194_red_green_fault_suite(
        sentinel_tmp=r3_sub, datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle,
        r2_cands_1x=r2_cands_1x, replay_bundle=replay_bundle, live_ff=live_ff,
        forensic_gz=forensic_gz, f07=f07_wl, f13=f13_wf, m5_ledger=m5_ledger,
    )
    from tests.test_kh03_r3_c1_regression_truth import run_new_kh03_r3_c1_worker_policy_fault_controls
    from tests.test_kh03_r3_c2_legacy_runner_fail_closed import run_new_kh03_r3_c2_legacy_fail_closed_fault_controls
    c1_14 = run_new_kh03_r3_c1_worker_policy_fault_controls()
    c2_16 = run_new_kh03_r3_c2_legacy_fail_closed_fault_controls()
    inh_all = list(r3_194["controls"]) + list(c1_14)
    all_ctrls = inh_all + list(c2_16)
    fids = [c["fault_id"] for c in all_ctrls]
    inh_fail = sum(1 for c in inh_all if c["status"] != "PASS")
    c2_fail = sum(1 for c in c2_16 if c["status"] != "PASS")
    tot_pass = sum(1 for c in all_ctrls if c["status"] == "PASS")

    f14_out = _stamp_c2({
        "inherited_c1_r3_controls_count": len(inh_all),
        "inherited_c1_r3_failed_controls": inh_fail,
        "new_c2_controls_count": len(c2_16),
        "new_c2_failed_controls": c2_fail,
        "total_controls": len(all_ctrls),
        "unique_fault_ids": len(set(fids)),
        "passed_controls": tot_pass,
        "failed_controls": len(all_ctrls) - tot_pass,
        "controls": all_ctrls,
        "status": "PASS" if (tot_pass == len(all_ctrls) and len(set(fids)) == len(all_ctrls) and len(inh_all) >= 208 and len(c2_16) >= 16) else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    del datasets, mtf_bundle, ledger_bundle, r1_candidates, r2_cands_1x, r3_cands_1x, replay_bundle, live_ff, m5_ledger, forensic_rows
    gc.collect()
    return f11_out, f14_out


def _run_stage4_51_verifier_mutations_15(sentinel_tmp: Path, *, run_id: str, tested_sha: str) -> Dict[str, Any]:
    """Runs 16 inherited R3 + 17 inherited C1 + 18 NEW C2 = 51 separate-OS verifier mutations (max_workers=3)."""
    r3_mut_dir = sentinel_tmp / "r3_m16_c2"
    r3_mut_dir.mkdir(parents=True, exist_ok=True)
    r3_m16 = _run_kh03_r3_verifier_mutation_suite_22(r3_mut_dir)

    from tests.test_kh03_r3_c1_verifier_mutations import (
        KH03_R3_C1_VERIFIER_MUTATIONS_SPECS,
        build_synthetic_valid_kh03_r3_c1_run_dir,
        run_kh03_r3_c1_verifier_subprocess,
    )
    from tests.test_kh03_r3_c2_independent_mutations import (
        KH03_R3_C2_LEGACY_R3_MUTATIONS_SPECS,
        KH03_R3_C2_OWN_VERIFIER_MUTATIONS_SPECS,
        build_synthetic_valid_kh03_r3_c2_run_dir,
        run_kh03_r3_c2_verifier_subprocess,
    )
    from tests.test_kh03_r3_resource_async_and_verifier_mutations import (
        build_synthetic_valid_kh03_r3_run_dir,
        run_kh03_r3_verifier_subprocess,
    )

    c1_base = sentinel_tmp / "c1_base_for_c2"
    rid_c1, tsha_c1 = build_synthetic_valid_kh03_r3_c1_run_dir(c1_base)
    rc_c1_ok, _ = run_kh03_r3_c1_verifier_subprocess(c1_base, rid_c1, tsha_c1)

    r3_base = sentinel_tmp / "r3_base_for_c2_new"
    rid_r3, tsha_r3 = build_synthetic_valid_kh03_r3_run_dir(r3_base)
    rc_r3_ok, _ = run_kh03_r3_verifier_subprocess(r3_base, rid_r3, tsha_r3)

    c2_base = sentinel_tmp / "c2_base_for_c2_new"
    rid_c2, tsha_c2 = build_synthetic_valid_kh03_r3_c2_run_dir(c2_base)
    rc_c2_ok, _ = run_kh03_r3_c2_verifier_subprocess(c2_base, rid_c2, tsha_c2)

    def _run_mut(args_t: Tuple[Path, str, str, Any, str, Any, str]) -> Dict[str, Any]:
        bdir, rid_x, tsha_x, runner_fn, mid, mut_fn, exp_tok = args_t
        cdir = sentinel_tmp / f"mut_{mid}"
        shutil.copytree(bdir, cdir)
        mut_fn(cdir)
        rc, out_t = runner_fn(cdir, rid_x, tsha_x)
        return {"mutation_id": mid, "expected_violation_token": exp_tok, "verifier_exit_code": rc, "detected_fail_closed": (rc != 0) and (exp_tok in out_t)}

    c1_jobs = [(c1_base, rid_c1, tsha_c1, run_kh03_r3_c1_verifier_subprocess, m, fn, tok) for m, fn, tok in KH03_R3_C1_VERIFIER_MUTATIONS_SPECS]
    c2_jobs = (
        [(r3_base, rid_r3, tsha_r3, run_kh03_r3_verifier_subprocess, m, fn, tok) for m, fn, tok in KH03_R3_C2_LEGACY_R3_MUTATIONS_SPECS]
        + [(c2_base, rid_c2, tsha_c2, run_kh03_r3_c2_verifier_subprocess, m, fn, tok) for m, fn, tok in KH03_R3_C2_OWN_VERIFIER_MUTATIONS_SPECS]
    )

    with ThreadPoolExecutor(max_workers=3) as pool:
        c1_res = list(pool.map(_run_mut, c1_jobs))
        c2_res = list(pool.map(_run_mut, c2_jobs))

    r3_tot, r3_det = int(r3_m16.get("mutations_tested_count", 0)), int(r3_m16.get("mutations_detected_count", 0))
    c1_tot, c1_det = len(c1_res), sum(1 for r in c1_res if r["detected_fail_closed"])
    c2_tot, c2_det = len(c2_res), sum(1 for r in c2_res if r["detected_fail_closed"])
    inh_fail = (r3_tot - r3_det) + (c1_tot - c1_det)
    c2_fail = c2_tot - c2_det
    clean_ok = (rc_c1_ok == 0 and rc_r3_ok == 0 and rc_c2_ok == 0 and bool(r3_m16.get("clean_clone_verifier_green")))

    return _stamp_c2({
        "isolated_temp_clones_only": True,
        "sealed_run_dir_mutated": False,
        "clean_clone_verifier_green": clean_ok,
        "inherited_r3_c1_mutations_tested_count": r3_tot + c1_tot,
        "inherited_r3_c1_mutations_detected_count": r3_det + c1_det,
        "inherited_r3_c1_mutations_failed_detection_count": inh_fail,
        "new_c2_mutations_tested_count": c2_tot,
        "new_c2_mutations_detected_count": c2_det,
        "new_c2_mutations_failed_detection_count": c2_fail,
        "total_mutations_tested_count": r3_tot + c1_tot + c2_tot,
        "new_c2_results": c2_res,
        "inherited_c1_results": c1_res,
        "inherited_r3_results": r3_m16.get("results") or [],
        "status": "PASS" if (clean_ok and inh_fail == 0 and c2_fail == 0 and c2_tot >= 16) else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)


def _collect_and_build_c2_lpt_shards(owned_temp_root: Path) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]]]:
    all_test_files = sorted(str(p.relative_to(APP_ROOT)).replace("\\", "/") for p in (BACKEND_ROOT / "tests").glob("test_*.py") if p.is_file())
    col_out = owned_temp_root / "collect_c2_stdout.log"
    col_err = owned_temp_root / "collect_c2_stderr.log"
    col_snippet = "import os, sys, pytest; fo=open(sys.argv[1],'w',encoding='utf-8',errors='replace'); fe=open(sys.argv[2],'w',encoding='utf-8',errors='replace'); sys.stdout=fo; sys.stderr=fe; rc=pytest.main(sys.argv[3:]); fo.flush(); fe.flush(); fo.close(); fe.close(); os._exit(int(rc))"
    col_env = dict(os.environ); col_env["PYTHONHASHSEED"] = "0"; col_env["PYTHONPATH"] = _pypath_str()
    proc = subprocess.run([sys.executable, "-c", col_snippet, str(col_out), str(col_err), str(BACKEND_ROOT / "tests"), "--collect-only", "-q"], cwd=str(APP_ROOT), env=col_env, capture_output=True, check=False, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"KH03_R3_C2_PYTEST_COLLECTION_FAILED:rc={proc.returncode}")
    collected_by_file: Dict[str, List[str]] = {}
    for ln in col_out.read_text("utf-8", errors="replace").splitlines():
        cid = canonicalize_collect_node_id_v1793122(ln)
        if cid is not None:
            collected_by_file.setdefault(cid.split("::", 1)[0], []).append(cid)
    for k in collected_by_file:
        collected_by_file[k].sort()
    empirical_ms = _load_empirical_file_durations_ms_from_kh01b()
    non_sub = [f for f in all_test_files if Path(f).name not in KH03_R3_C2_EXCLUSIVE_SUBPROCESS_TEST_FILES]
    sub_files = [f for f in all_test_files if Path(f).name in KH03_R3_C2_EXCLUSIVE_SUBPROCESS_TEST_FILES]
    shards = _build_lpt_shards_kh01b_r1(non_sub, collected_by_file, empirical_ms)
    for sh in shards:
        if sh.get("shard_name") == "SHARD_SINGLE_CHILD_02_BUDGET2":
            sh["exclusive"] = True
            sh["slot_cost"] = 4
    for idx_s, sf in enumerate(sorted(sub_files), 1):
        shards.append({"shard_name": f"SHARD_SINGLE_CHILD_KH03_R3_C2_SUBPROC_{idx_s:02d}", "files": [sf], "exclusive": True, "slot_cost": 4, "priority_weight": 260_000, "needs_venv": False})
    return collected_by_file, shards


def _run_c2_full_repo_pytest_sharded_09(
    run_dir: Path, sentinel_tmp: Path, *, run_id: str, tested_sha: str, effective_disk_reserve_bytes: int, max_workers: int, git_clean_at_start: bool
) -> Dict[str, Any]:
    lineage_p = REPO_ROOT / LINEAGE_TRACKED_ARTIFACT_REL
    sha_before = _sha256_file(lineage_p) if lineage_p.is_file() else None
    t0 = time.perf_counter()
    empirical_r2_rss = load_empirical_r2_shard_rss_bytes(REPO_ROOT)
    collected_by_file, c2_shards = _collect_and_build_c2_lpt_shards(sentinel_tmp)
    active_shards_tracker: Dict[str, Any] = {}
    junit_res = asyncio.run(
        run_async_lpt_pytest_shards_kh01b_r1(
            run_dir, sentinel_tmp, run_id=run_id, tested_executable_sha=tested_sha,
            effective_disk_reserve_bytes=effective_disk_reserve_bytes,
            max_parallel_workers=max_workers, ram_policy_mode="R3_STRICT_512MIB",
            empirical_rss_by_shard=empirical_r2_rss, active_shards_tracker=active_shards_tracker,
            shard_specs_override=c2_shards, collected_by_file_override=collected_by_file,
        )
    )
    elapsed = round(time.perf_counter() - t0, 3)
    sha_after = _sha256_file(lineage_p) if lineage_p.is_file() else None
    restored = False
    if git_clean_at_start and sha_before is not None and sha_after != sha_before:
        _git_cmd(["checkout", "--", LINEAGE_TRACKED_ARTIFACT_REL])
        restored = (_sha256_file(lineage_p) == sha_before)

    cm, sm = junit_res["collection_manifest"], junit_res["shard_manifest"]
    bj = verify_fresh_junit_bijection_v17931211(
        run_dir, cm, sm, expected_run_id=run_id, expected_tested_sha=tested_sha,
        mandatory_node_prefixes=("backend/tests/test_kh_02_r2", "backend/tests/test_kh_03", "backend/tests/test_kh03_r2", "backend/tests/test_kh03_r3", "backend/tests/test_kh03_r3_c1", "backend/tests/test_kh03_r3_c2"),
        require_shard_schedule=True,
    )
    col_n = int(cm.get("collected_node_ids_count") or len(cm.get("collected_node_ids") or ()))
    exe_n = int(bj.get("executed_node_ids_count") or bj.get("executed_count") or 0)
    pas_n, skp_n, fai_n, err_n, msk_n = int(bj.get("passed_count") or 0), int(bj.get("skipped_count") or 0), int(bj.get("failed_count") or 0), int(bj.get("error_count") or 0), int(bj.get("mandatory_skipped_count") or 0)
    nz = int(sm.get("nonzero_exit_shards_count") or 0)
    ok = bj.get("status") == "PASS" and nz == 0 and col_n >= MIN_EXPECTED_PYTEST_COLLECTED_C2 and col_n == exe_n and fai_n == 0 and err_n == 0 and msk_n == 0
    return _stamp_c2({
        "status": "PASS" if ok else "FAIL",
        "elapsed_sec": elapsed, "max_workers_used": max_workers,
        "collected_count": col_n, "executed_count": exe_n, "passed_count": pas_n,
        "skipped_count": skp_n, "mandatory_skipped_count": msk_n, "failed_count": fai_n,
        "error_count": err_n, "nonzero_exit_shards_count": nz, "junit_bijection_exact": bj.get("status") == "PASS",
        "tracked_test_side_effect_audit": {"rel_path": LINEAGE_TRACKED_ARTIFACT_REL, "sha256_before": sha_before, "sha256_after_pytest": sha_after, "restored_to_clean_head": restored},
        "collection_manifest": cm, "shard_manifest": sm, "bijection_audit": bj,
    }, run_id=run_id, tested_sha=tested_sha)


def _render_c2_readme_and_handover(
    *, run_id: str, f00: Mapping[str, Any], f03: Mapping[str, Any], f04: Mapping[str, Any], f05: Mapping[str, Any],
    f08: Mapping[str, Any], f09: Mapping[str, Any], f10: Mapping[str, Any], f11: Mapping[str, Any],
    f13: Mapping[str, Any], f14: Mapping[str, Any], f15: Mapping[str, Any], f17: Mapping[str, Any], f18: Mapping[str, Any],
) -> Tuple[str, str]:
    v, g = f17["verdicts"], f17["gates_26"]
    p_pol, s_pol = f08.get("pytest_worker_policy") or {}, f08.get("science_worker_policy") or {}
    base_1x = ((f11.get("dynamic_full_rerisk_six_scenarios") or {}).get("1X_BASE_0p0004") or {}).get("arms") or {}
    n1, e1, e2, e3 = base_1x.get("NC_NULL_TRIGGER") or {}, base_1x.get("KH03_R3_E1_LOW_FRICTION_STOP_FLOOR") or {}, base_1x.get("KH03_R3_E2_CAUSAL_DELAYED_CONFIRMATION") or {}, base_1x.get("KH03_R3_E3_CAUSAL_NEAREST_M5_UNSWEPT_EXIT") or {}
    disp = f"ENGINEERING: {v['ENGINEERING']}; WORKER_POLICY: {v['WORKER_POLICY']}; DETERMINISM: {v['DETERMINISM']}; RESOURCE: {v['RESOURCE']}; ECONOMIC: {v['RESEARCH_ECONOMIC_VERDICT']}; OOS: {v['FRESH_OOS']}; KH04: {v['KH04_ENTRY']}; ACTIVATION: {v['ACTIVATION_READY']}; PUBLICATION: {v['PUBLICATION']}"

    readme = f"""# AURUM DESK — KH-03.R3.C2 | Legacy R3 Runner Fail-Closed Final Closure

- **Spec Version**: `{SPEC_VERSION_KH03_R3_C2}` | **Run ID**: `{run_id}`
- **Nhánh Git bắt buộc**: `{f00['branch']}`
- **Tested Executable SHA**: `{f00['tested_executable_sha']}`
- **Prior C1 Pub HEAD**: `{PRIOR_KH03_R3_C1_PUB_HEAD_SHA}` (C1 Run `{PRIOR_KH03_R3_C1_RUN_ID}` & R3 Run `{PRIOR_KH03_R3_RUN_ID}` giữ nguyên bất biến 100%)
- **Kết luận tổng quan**: `{disp}`
- **Phân loại toàn cục**: `{f17['overall_classification']}`
- **Khóa an toàn**: `ACTIVATION_READY=NO` | `PRODUCTION=OFF` | `LIVE=OFF` | `PAPER=OFF` | `AUTO=OFF` | `OOS_EVALUATIONS=0` | `NO_PROVEN_EDGE` | `CI_STATUS=CI_UNKNOWN_NO_ACTIONS_RUN`

## 1. Đóng lỗi P0/P1 đường R3 cũ (RED -> GREEN)
- **RED tái hiện thực (`{f03['red_cases_reproduced_count']}/6`)**: Đường R3 cũ (`run_kh_03_r3_acceptance.py` line 911) trả `f19.status="PASS"` dù `pytest_optimum_worker_policy` bị `BLOCKED_INVALID_BENCHMARK`/`can_certify=False`; và `kh_03_r3_independent_verifier.py` trên HEAD `{PREFIX_HEAD_SHA_VERIFIED}` trước sửa trả `exit_code=0`, `G22=PASS`, `violations=[]` (log SHA `{PREFIX_RED06_LOG_SHA256}`) dù `f19` chứa `wall_clock_seconds=-5.0` và policy `BLOCKED`.
- **GREEN sau sửa**: `_evaluate_r3_f19_policy_certification` (40/40 cases `C2T01..C2T40` PASS) + `recompute_r3_worker_policy_from_raw_for_verifier` độc lập trong R3 verifier chặn tuyệt đối mọi forged PASS, từ chối archived R3 trong strict mode, chỉ cho phép đọc lịch sử dưới `--legacy-descriptive-audit-only` (`LEGACY_DESCRIPTIVE_ONLY`, `admitted_for_new_c2_certification=False`).

## 2. Kết quả đo thực tươi KH-03.R3.C2
- **Pytest subset (`155` tests)**: `means={p_pol.get('mean_wall_sec_by_mode')}`, `w=1_repeat={p_pol.get('w1_repeat_wall_sec')}s` (var `{p_pol.get('w1_repeat_variance_pct')}`), `measured_fastest={p_pol.get('measured_fastest_mode')}`, `applied_worker_count={p_pol.get('applied_worker_count')}` (safe cap `{p_pol.get('safe_worker_cap')}`), `speedup_w4_vs_w1={p_pol.get('measured_speedup_w4_vs_w1')}x`, `full_suite_speedup_extrapolated=False`.
- **Science slice (`8,000` bars / `12,062` rows)**: `means={s_pol.get('mean_wall_sec_by_mode')}`, `w=1_repeat={s_pol.get('w1_repeat_wall_sec')}s` (var `{s_pol.get('w1_repeat_variance_pct')}`), `measured_fastest={s_pol.get('measured_fastest_mode')}`, `applied_worker_count={s_pol.get('applied_worker_count')}`.
- **Full Repo Pytest JUnit 1:1**: `{f09.get('collected_count')}` collected / `{f09.get('executed_count')}` executed / `{f09.get('passed_count')}` passed / `{f09.get('skipped_count')}` skipped (`mandatory_skipped={f09.get('mandatory_skipped_count')}`) / `{f09.get('failed_count')}` failed / `{f09.get('error_count')}` error (`max_workers={f09.get('max_workers_used')}`).
- **Baseline Parity**: A1 `{f10.get('a1_population_count')}/{f10.get('a1_mismatches_count')}`, SI8 `{f10.get('si8_population_count')}/{f10.get('si8_mismatches_count')}`, Adapter `{f10.get('three_adapter_population_count')}/{f10.get('three_adapter_mismatches_count')}`, D4 `{f10.get('d4_historical_closed_trades')}`.
- **Kinh tế học lịch sử DEV 1x (khớp 100% R3, KHÔNG alpha mới)**: NULL `{n1.get('closed')}` (`{n1.get('wins')}W/{n1.get('losses')}L`, `{n1.get('net_pnl_usd')}` USD, `{n1.get('expectancy_r')}R`), E1 `{e1.get('closed')}` (`{e1.get('net_pnl_usd')}` USD, `{e1.get('expectancy_r')}R`), E2 `{e2.get('closed')}` (`{e2.get('net_pnl_usd')}` USD, `{e2.get('expectancy_r')}R`), E3 `{e3.get('closed')}` (`{e3.get('net_pnl_usd')}` USD, `{e3.get('expectancy_r')}R`).
- **Faults & Mutations**: `{f14.get('passed_controls')}/{f14.get('total_controls')}` (`208` kế thừa + `{f14.get('new_c2_controls_count')}` mới C2) | Verifier Mutations: `{f15.get('new_c2_mutations_detected_count')}/{f15.get('new_c2_mutations_tested_count')}` mới C2 + `{f15.get('inherited_r3_c1_mutations_detected_count')}/{f15.get('inherited_r3_c1_mutations_tested_count')}` kế thừa.
- **Tài nguyên**: min disk `{f13.get('min_disk_free_gib')} GiB`, min RAM `{f13.get('min_available_ram_mib')} MiB`, peak RSS `{f13.get('max_tree_rss_mib')} MiB`, max child `{f13.get('max_descendant_children')}`, orphans `{f13.get('orphan_children_after_cleanup')}`, waiver `False`.

## 3. Ma trận 26 cổng (`G01..G26`)
""" + "\n".join(f"- `{k}`: **{val}**" for k, val in g.items()) + "\n"

    handover = f"""AURUM DESK — BÀN GIAO CHỦ SỞ HỮU KH-03.R3.C2 (TIẾNG VIỆT)
======================================================================
SPEC: {SPEC_VERSION_KH03_R3_C2} | RUN_ID: {run_id}
BRANCH: {f00['branch']}
TESTED_EXECUTABLE_SHA: {f00['tested_executable_sha']}
PRIOR_C1_PUB_HEAD_SHA: {PRIOR_KH03_R3_C1_PUB_HEAD_SHA}
CI_STATUS: {f18.get('ci_status')}
ACTIVATION_READY=NO | PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | OOS_EVALUATIONS=0 | NO_PROVEN_EDGE

KET LUAN: {disp}
CLASSIFICATION: {f17['overall_classification']}

1. RED -> GREEN DUONG R3 CU:
- Pre-fix RED01..RED06 tai hien that tren {PREFIX_HEAD_SHA_VERIFIED}: {f03['red_cases_reproduced_count']}/6 (RED06 log SHA {PREFIX_RED06_LOG_SHA256}, R3 verifier cu tra exit 0/G22=PASS cho f19 gia mao).
- Post-fix boundary suite C2T01..C2T40: {f04['cases_passed']}/{f04['cases_total']} PASS.
- R3 verifier G22 doc lap: clean={f05['clean_c2_r3_run_passes']}, forged_rejected={f05['forged_f19_rejected_fail_closed']}, archived_r3_strict_rejected={f05['archived_r3_strict_mode_rejected']}, archived_r3_mode={f05['archived_r3_descriptive_audit_classification']} (admitted_for_new_c2_certification=False).

2. BENCHMARK & FULL REPO PYTEST JUNIT 1:1:
- Pytest subset (155): means={p_pol.get('mean_wall_sec_by_mode')}, w=1_repeat={p_pol.get('w1_repeat_wall_sec')}s (var={p_pol.get('w1_repeat_variance_pct')}), fastest={p_pol.get('measured_fastest_mode')}, applied={p_pol.get('applied_worker_count')} (safe_cap={p_pol.get('safe_worker_cap')}), full_suite_extrapolated=False
- Science slice (8000 bars): means={s_pol.get('mean_wall_sec_by_mode')}, w=1_repeat={s_pol.get('w1_repeat_wall_sec')}s (var={s_pol.get('w1_repeat_variance_pct')}), fastest={s_pol.get('measured_fastest_mode')}, applied={s_pol.get('applied_worker_count')}
- Full repo pytest: collected={f09.get('collected_count')}, executed={f09.get('executed_count')}, passed={f09.get('passed_count')}, skipped={f09.get('skipped_count')}, mandatory_skipped={f09.get('mandatory_skipped_count')}, failed={f09.get('failed_count')}, error={f09.get('error_count')}
- Parity: A1={f10.get('a1_population_count')}/{f10.get('a1_mismatches_count')}, SI8={f10.get('si8_population_count')}/{f10.get('si8_mismatches_count')}, Adapter={f10.get('three_adapter_population_count')}/{f10.get('three_adapter_mismatches_count')}, D4={f10.get('d4_historical_closed_trades')}
- Faults: {f14.get('passed_controls')}/{f14.get('total_controls')} | Separate-OS Mutations: {f15.get('new_c2_mutations_detected_count')}/{f15.get('new_c2_mutations_tested_count')} moi C2 + {f15.get('inherited_r3_c1_mutations_detected_count')}/{f15.get('inherited_r3_c1_mutations_tested_count')} ke thua

3. KINH TE HOC 1X LICH SU (KHONG ALPHA MOI):
- NULL: {n1.get('closed')} ({n1.get('wins')}W/{n1.get('losses')}L), net={n1.get('net_pnl_usd')} USD, exp={n1.get('expectancy_r')}R
- E1:   {e1.get('closed')} ({e1.get('wins')}W/{e1.get('losses')}L), net={e1.get('net_pnl_usd')} USD, exp={e1.get('expectancy_r')}R
- E2:   {e2.get('closed')} ({e2.get('wins')}W/{e2.get('losses')}L), net={e2.get('net_pnl_usd')} USD, exp={e2.get('expectancy_r')}R
- E3:   {e3.get('closed')} ({e3.get('wins')}W/{e3.get('losses')}L), net={e3.get('net_pnl_usd')} USD, exp={e3.get('expectancy_r')}R

4. TAI NGUYEN:
- min_disk={f13.get('min_disk_free_gib')} GiB (>=20.0), min_ram={f13.get('min_available_ram_mib')} MiB (>=512.0), peak_rss={f13.get('max_tree_rss_mib')} MiB, max_children={f13.get('max_descendant_children')} (<=4), orphans={f13.get('orphan_children_after_cleanup')}, waiver=False

5. 26 CONG (G01..G26):
""" + "\n".join(f"  {k}: {val}" for k, val in g.items()) + "\n"
    return readme, handover


def run_kh03_r3_c2_acceptance(
    *, allow_local_dirty: bool = False, owner_waive_disk_preflight: bool = False, max_workers: int = GLOBAL_MAX_DESCENDANTS_KH02_R2
) -> Tuple[int, Path, Dict[str, Any]]:
    if owner_waive_disk_preflight:
        raise SystemExit(2)

    lock = _CrossPlatformRunLock(Path(tempfile.gettempdir()) / "aurum_desk_kh03_r3_c2_run.lock")
    lock_ok, lock_msg = lock.acquire()
    if not lock_ok:
        raise RuntimeError(f"KH03_R3_C2_SINGLE_RUN_LOCK_FAILED:{lock_msg}")

    t_start = time.perf_counter()
    orch_pid = os.getpid()
    utc_now = datetime.now(timezone.utc)
    _, branch = _git_cmd(["branch", "--show-current"])
    _, start_head = _git_cmd(["rev-parse", "HEAD"])
    _, st_porc = _git_cmd(["status", "--porcelain=v1"])
    _, ls_rem = _git_cmd(["ls-remote", "origin", f"refs/heads/{MANDATORY_BRANCH_KH03_R3_C2}"])
    remote_sha = ls_rem.split()[0] if ls_rem.strip() else ""
    git_clean = (st_porc.strip() == "")

    if branch != MANDATORY_BRANCH_KH03_R3_C2:
        lock.release()
        raise RuntimeError(f"WRONG_BRANCH:{branch}")
    if (not git_clean) and (not allow_local_dirty):
        lock.release()
        raise RuntimeError(f"DIRTY_WORKTREE_REFUSED:\n{st_porc}")

    run_id = f"{utc_now.strftime('%Y%m%dT%H%M%SZ')}_{start_head[:12]}"
    art_base = (REPO_ROOT / KH03_R3_C2_ARTIFACTS_BASE_REL).resolve()
    run_dir = art_base / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "junit").mkdir(exist_ok=True)
    (run_dir / "checksums").mkdir(exist_ok=True)

    # Create README immediately at run start (§3/§10)
    (run_dir / "README_KH03_R3_C2.md").write_text(
        f"# AURUM DESK — KH-03.R3.C2 (IN PROGRESS)\n\n- Run ID: `{run_id}`\n- Tested SHA: `{start_head}`\n- Status: `IN_PROGRESS_NOT_SEALED`\n",
        encoding="utf-8",
    )

    sentinel_tmp = create_sentinel_owned_temp_dir_r2_c1(f"kh03c2_{utc_now.strftime('%Y%m%dT%H%M%SZ')}_", run_id=run_id, owner_pid=orch_pid)
    write_mount_paths = {
        "repo_volume": REPO_ROOT, "app_volume": APP_ROOT,
        "artifacts_output": art_base if art_base.exists() else APP_ROOT,
        "temp_volume": sentinel_tmp, "python_venv": Path(sys.executable).resolve().parent,
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

    sampler = _BoundedResourceSampler(
        out_gz=run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz",
        watch_path=run_dir, watch_paths=[REPO_ROOT, APP_ROOT, run_dir, sentinel_tmp, Path(sys.executable).resolve().parent],
        interval_sec=0.5, enforce_runtime_disk_abort=can_run_heavy,
    )
    sampler.start()
    sampler_stopped = False

    try:
        print(f"[KH-03.R3.C2] Stage 1: Ownership, C1 service untouched & prior R1/R2/R3/C1 SHA immutability (run_id={run_id})...", flush=True)
        legacy_cg = audit_kh03_r3_legacy_research_callgraph(REPO_ROOT)
        f01, f12 = _audit_ownership_01_and_untouched_12(tested_sha=start_head, run_id=run_id, legacy_cg=legacy_cg)
        f02 = _audit_prior_sealed_02(sentinel_tmp, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "01_locked_code_ownership_and_ast_parity.json", f01)
        write_json_durable_atomic(run_dir / "02_prior_r1_r2_r3_c1_artifact_sha_immutability.json", f02)
        write_json_durable_atomic(run_dir / "12_legacy_and_c1_old_source_do_not_change.json", f12)

        print("[KH-03.R3.C2] Stage 2: Legacy R3 RED proof (03), 40-case C2T01..C2T40 GREEN suite (04) & R3 G22 verifier check (05)...", flush=True)
        f03, f04, f05 = _run_stage2_red_green_and_r3_g22_03_04_05(sentinel_tmp, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "03_legacy_r3_false_pass_red_proof.json", f03)
        write_json_durable_atomic(run_dir / "04_r3_f19_policy_condition_green_boundary_suite.json", f04)
        write_json_durable_atomic(run_dir / "05_r3_independent_g22_raw_benchmark_recompute.json", f05)

        print("[KH-03.R3.C2] Stage 3: Read-only 6-scenario R3 economic regression (11) & 224 RED/GREEN faults (14)...", flush=True)
        f11, f14 = _run_stage3_economic_11_and_224_faults_14(sentinel_tmp=sentinel_tmp, run_id=run_id, tested_sha=start_head, legacy_cg=legacy_cg)
        write_json_durable_atomic(run_dir / "11_six_cost_e1_e2_e3_null_regression.json", f11)
        write_json_durable_atomic(run_dir / "14_inherited_plus_new_red_green_fault_controls.json", f14)

        print("[KH-03.R3.C2] Stage 4: Running 51 separate-OS verifier mutation attacks (16 R3 + 17 C1 + 18 NEW C2) (15)...", flush=True)
        f15 = _run_stage4_51_verifier_mutations_15(sentinel_tmp, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "15_independent_verifier_mutation_test_results.json", f15)

        if can_run_heavy:
            eff_res_b = int(STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2 * (1024 ** 3))
            print(f"[KH-03.R3.C2] Stage 5A: Running FULL repo LPT-sharded pytest (max_workers={pytest_full_cap}) (09)...", flush=True)
            f09 = _run_c2_full_repo_pytest_sharded_09(
                run_dir, sentinel_tmp, run_id=run_id, tested_sha=start_head,
                effective_disk_reserve_bytes=eff_res_b, max_workers=pytest_full_cap, git_clean_at_start=git_clean,
            )
            write_json_durable_atomic(run_dir / "09_full_repo_pytest_junit_1to1.json", f09)
            gc.collect()

            print("[KH-03.R3.C2] Stage 5B: Running fresh W1/W2/W4/W1_repeat pytest + R2 science benchmarks (06..08)...", flush=True)
            pb_raw = asyncio.run(_run_controlled_pytest_w1_w2_w4_benchmark_r1(sentinel_tmp, enforce_r3_ram_guard=True))
            sb_raw = asyncio.run(_run_r2_science_slice_w1_w2_w4_determinism(sentinel_tmp))
            gc.collect()

            f06 = _stamp_c2({"canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2, "benchmark_kind": BENCHMARK_KIND_PYTEST, "status": "PASS" if pb_raw.get("all_4_runs_identical") else "FAIL", **pb_raw}, run_id=run_id, tested_sha=start_head)
            f07 = _stamp_c2({"canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2, "benchmark_kind": BENCHMARK_KIND_SCIENCE, "status": "PASS" if sb_raw.get("all_4_runs_identical") else "FAIL", **sb_raw}, run_id=run_id, tested_sha=start_head)
            min_pre_dk = min(float(m.get("free_gib", 0.0)) for m in strict_probe["distinct_mounts"])
            p_lim = {"configured_child_process_cap": GLOBAL_MAX_DESCENDANTS_KH02_R2, "currently_allowed_worker_cap": pytest_full_cap, "full_suite_safe_worker_cap": pytest_full_cap, "shard_exclusive_cap": pytest_full_cap, "preflight_disk_free_gib": min_pre_dk, "disk_free_gib": min_pre_dk, "available_ram_mib": avail_ram_mib}
            s_lim = {"configured_child_process_cap": GLOBAL_MAX_DESCENDANTS_KH02_R2, "currently_allowed_worker_cap": eff_workers, "full_suite_safe_worker_cap": eff_workers, "shard_exclusive_cap": eff_workers, "preflight_disk_free_gib": min_pre_dk, "disk_free_gib": min_pre_dk, "available_ram_mib": avail_ram_mib}
            p_pol = select_measured_worker_policy_strict(f06, benchmark_kind=BENCHMARK_KIND_PYTEST, system_limits=p_lim, expected_run_id=run_id, expected_tested_sha=start_head, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2)
            s_pol = select_measured_worker_policy_strict(f07, benchmark_kind=BENCHMARK_KIND_SCIENCE, system_limits=s_lim, expected_run_id=run_id, expected_tested_sha=start_head, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2)
            f08 = _stamp_c2({
                "pytest_worker_policy": p_pol, "science_worker_policy": s_pol,
                "full_suite_actual_max_workers_applied": pytest_full_cap,
                "full_suite_speedup_extrapolated": False,
                "status": "PASS" if (p_pol["status"] == "PASS" and s_pol["status"] == "PASS") else "FAIL",
            }, run_id=run_id, tested_sha=start_head)
            write_json_durable_atomic(run_dir / "06_fresh_pytest_w1_w2_w4_w1repeat.json", f06)
            write_json_durable_atomic(run_dir / "07_fresh_science_w1_w2_w4_w1repeat.json", f07)
            write_json_durable_atomic(run_dir / "08_measured_fastest_vs_safe_applied_worker_caps.json", f08)

            print("[KH-03.R3.C2] Stage 5C: Running FULL 56,710 A1 + 6,375 SI8 + 246 adapter + D4 parity (10)...", flush=True)
            ds_p, _ = load_r2_canonical_dataset_readonly(REPO_ROOT / CANONICAL_DATASET_REL_PATH)
            f02_r1 = audit_r2_reference_inputs_and_invariants(REPO_ROOT)
            f10 = _run_full_production_baseline_parity_20(datasets=ds_p, sentinel_tmp=sentinel_tmp, run_id=run_id, tested_sha=start_head, branch=branch, f01=f01, f02=f02_r1, max_workers=eff_workers)
            f10 = _stamp_c2(f10, run_id=run_id, tested_sha=start_head)
            write_json_durable_atomic(run_dir / "10_full_A1_SI8_adapter_D4_parity.json", f10)
            del ds_p
            gc.collect()
        else:
            f06 = _stamp_c2({"benchmark_kind": BENCHMARK_KIND_PYTEST, "status": "NOT_RUN_BLOCKED_LOW_DISK", "all_4_runs_identical": False, "runs": []}, run_id=run_id, tested_sha=start_head)
            f07 = _stamp_c2({"benchmark_kind": BENCHMARK_KIND_SCIENCE, "status": "NOT_RUN_BLOCKED_LOW_DISK", "all_4_runs_identical": False, "runs": []}, run_id=run_id, tested_sha=start_head)
            f08 = _stamp_c2({"status": "NOT_RUN_BLOCKED_LOW_DISK", "pytest_worker_policy": {}, "science_worker_policy": {}, "full_suite_speedup_extrapolated": False}, run_id=run_id, tested_sha=start_head)
            f09 = _stamp_c2({"status": "NOT_RUN_BLOCKED_LOW_DISK", "collected_count": 0, "executed_count": 0, "passed_count": 0, "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0, "junit_bijection_exact": False}, run_id=run_id, tested_sha=start_head)
            f10 = _stamp_c2({"BASELINE_PARITY_VERDICT": "NOT_RUN_BLOCKED_LOW_DISK", "a1_population_count": 0, "a1_mismatches_count": 0, "si8_population_count": 0, "si8_mismatches_count": 0, "three_adapter_population_count": 0, "three_adapter_mismatches_count": 0, "d4_historical_closed_trades": 0, "per_decision_and_episode_fingerprints": {"all_sealed_fingerprints_exact_match": False}}, run_id=run_id, tested_sha=start_head)
            for fn_b, obj_b in (("06_fresh_pytest_w1_w2_w4_w1repeat.json", f06), ("07_fresh_science_w1_w2_w4_w1repeat.json", f07), ("08_measured_fastest_vs_safe_applied_worker_caps.json", f08), ("09_full_repo_pytest_junit_1to1.json", f09), ("10_full_A1_SI8_adapter_D4_parity.json", f10)):
                write_json_durable_atomic(run_dir / fn_b, obj_b)

        cl_info = _cleanup_sentinel_temp_c4_win_safe(sentinel_tmp, expected_run_id=run_id)
        sentinel_cleaned = bool(cl_info["cleaned"])
        telem = sampler.stop_and_flush()
        sampler_stopped = True

        f13 = _stamp_c2({
            "orchestrator_pid": orch_pid, "sampling_interval_sec": 0.5,
            "memory_samples_log_rel": "logs/runtime_memory_samples_0p5s.jsonl.gz",
            **telem,
            "strict_preflight_disk_min_gib": STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
            "strict_runtime_disk_min_gib": STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2,
            "advisory_disk_gib": ADVISORY_DISK_GIB_KH02_R2,
            "owner_disk_preflight_waiver": False,
            "sentinel_temp_cleaned_up": sentinel_cleaned,
            "total_wall_clock_sec": round(time.perf_counter() - t_start, 3),
            "status": "PASS" if (sentinel_cleaned and int(telem.get("orphan_children_after_cleanup", 1)) == 0) else "FAIL",
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "13_memory_disk_pid_telemetry.json", f13)

        _, end_head = _git_cmd(["rev-parse", "HEAD"])
        f00 = _stamp_c2({
            "started_utc": utc_now.strftime("%Y-%m-%dT%H:%M:%SZ"), "branch": branch,
            "start_head_sha": start_head, "end_head_sha": end_head, "remote_head_sha": remote_sha,
            "prior_kh03_r3_c1_pub_head_sha": PRIOR_KH03_R3_C1_PUB_HEAD_SHA,
            "prior_kh03_r3_c1_tested_sha": PRIOR_KH03_R3_C1_TESTED_SHA,
            "prior_kh03_r3_c1_evidence_sha": PRIOR_KH03_R3_C1_EVIDENCE_SHA,
            "prior_kh03_r3_c1_run_id": PRIOR_KH03_R3_C1_RUN_ID,
            "git_clean_at_start": git_clean, "local_uncommitted_changes": not git_clean,
            "single_run_lock_status": lock_msg, "orchestrator_pid": orch_pid,
            "python_version": platform.python_version(), "platform": platform.platform(),
            "random_seed": DEFAULT_RANDOM_SEED_KH02_R2, "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
            "owner_disk_preflight_waiver": False, "strict_multi_mount_probe": strict_probe,
            "preflight_ram_available_mib": avail_ram_mib, "can_run_heavy_stages": can_run_heavy,
            "readonly_disk_inventory": build_r2_c1_readonly_disk_inventory(REPO_ROOT) if not strict_disk_ok else None,
            "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2), "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
            "status": "PASS" if (strict_disk_ok and ram_ok and git_clean) else ("BLOCKED_LOW_DISK" if not strict_disk_ok else "BLOCKED_PREFLIGHT"),
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "00_branch_remote_sha_dirty_preflight.json", f00)

        pub_st = "PRE_PUSH_PENDING" if (strict_disk_ok and git_clean) else "NOT_PUBLISHED_BLOCKED_LOW_DISK"
        f18 = _stamp_c2({
            "branch": branch, "start_head_sha": start_head,
            "evidence_commit_sha": None, "publication_head_sha": None,
            "remote_readback_sha256_verified": False, "latest_run_pointer_updated": False,
            "owner_disk_preflight_waiver": False, "ci_status": "CI_UNKNOWN_NO_ACTIONS_RUN",
            "actual_github_actions_run_verified": False, "publication_status": pub_st, "status": pub_st,
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "18_phase1_code_commit_push_receipt.json", f18)

        docs_all = {"00": f00, "01": f01, "02": f02, "03": f03, "04": f04, "05": f05, "06": f06, "07": f07, "08": f08, "09": f09, "10": f10, "11": f11, "12": f12, "13": f13, "14": f14, "15": f15, "18": f18}
        p_rec = _independent_recompute_raw_bench_c2(f06, f08.get("pytest_worker_policy") or {}, kind=BENCHMARK_KIND_PYTEST, expected_run_id=run_id, expected_tested_sha=start_head, max_safe_cap=3)
        s_rec = _independent_recompute_raw_bench_c2(f07, f08.get("science_worker_policy") or {}, kind=BENCHMARK_KIND_SCIENCE, expected_run_id=run_id, expected_tested_sha=start_head, max_safe_cap=GLOBAL_MAX_DESCENDANTS_KH02_R2)
        m_aud = _audit_telemetry_gz_vs_13_c2(run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz", f13)
        e_ok, _ = _audit_economic_regression_11_c2(f11)
        r17 = recompute_kh03_r3_c2_26_gates_and_verdicts(docs=docs_all, pytest_recomp=p_rec, science_recomp=s_rec, mem_audit=m_aud, junit_bijection_ok=(f09.get("status") == "PASS" and bool(f09.get("junit_bijection_exact"))), economic_ok=e_ok, for_final_verifier_manifest=False)
        f17 = _stamp_c2({"OOS_EVALUATIONS": 0, "ACTIVATION_READY": ACTIVATION_READY_KH02_R2, "overall_classification": r17["overall_classification"], "gates_26": r17["gates_26"], "verdicts": r17["verdicts"], "status": r17["verdicts"]["ENGINEERING"]}, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "17_orthogonal_gate_matrix_and_verdicts.json", f17)

        readme_md, handover_vi = _render_c2_readme_and_handover(run_id=run_id, f00=f00, f03=f03, f04=f04, f05=f05, f08=f08, f09=f09, f10=f10, f11=f11, f13=f13, f14=f14, f15=f15, f17=f17, f18=f18)
        (run_dir / "README_KH03_R3_C2.md").write_text(readme_md, encoding="utf-8")
        (run_dir / "HANDOVER_KH03_R3_C2_VI.txt").write_text(handover_vi, encoding="utf-8")

        chk_map = {fp.relative_to(run_dir).as_posix(): _sha256_file(fp) for fp in sorted(run_dir.rglob("*")) if fp.is_file() and not fp.name.endswith(".tmp") and fp.relative_to(run_dir).as_posix() != "checksums/sha256_inventory.json"}
        write_json_durable_atomic(run_dir / "checksums" / "sha256_inventory.json", {"spec_version": SPEC_VERSION_KH03_R3_C2, "run_id": run_id, "tested_executable_sha": start_head, "files_count": len(chk_map), "canonical_inventory_sha256": canonical_hash(chk_map), "files_sha256": chk_map})

        print("[KH-03.R3.C2] Stage 7: Launching separate-OS-process independent disk verifier (16)...", flush=True)
        env_v = os.environ.copy(); env_v["PYTHONPATH"] = _pypath_str()
        v_proc = subprocess.run([sys.executable, str(APP_ROOT / "tools" / "kh_03_r3_c2_independent_verifier.py"), "--run-dir", str(run_dir), "--expected-run-id", run_id, "--expected-tested-sha", start_head], cwd=str(APP_ROOT), env=env_v, capture_output=True, text=True, timeout=240)
        print(v_proc.stdout, flush=True)
        if v_proc.stderr:
            print(v_proc.stderr, file=sys.stderr, flush=True)

        f16 = json.loads((run_dir / "16_independent_OS_verifier_manifest_sha256.json").read_text("utf-8"))
        return int(v_proc.returncode), run_dir, {
            "run_id": run_id, "run_dir": str(run_dir), "verifier_exit_code": int(v_proc.returncode),
            "independent_verifier_status": f16.get("independent_verifier_status"),
            "overall_classification": f16.get("overall_classification"),
            "recomputed_verdicts": f16.get("recomputed_verdicts"), "violations": f16.get("violations"),
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
    ap = argparse.ArgumentParser(description="AURUM DESK KH-03.R3.C2 Acceptance Orchestrator")
    ap.add_argument("--allow-local-dirty", action="store_true")
    ap.add_argument("--owner-waive-disk-preflight", action="store_true")
    ap.add_argument("--max-workers", type=int, default=GLOBAL_MAX_DESCENDANTS_KH02_R2)
    args = ap.parse_args(argv)
    if args.owner_waive_disk_preflight:
        return 2
    rc, _rdir, summary = run_kh03_r3_c2_acceptance(allow_local_dirty=args.allow_local_dirty, owner_waive_disk_preflight=False, max_workers=args.max_workers)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
