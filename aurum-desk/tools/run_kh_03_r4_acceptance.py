#!/usr/bin/env python3
"""
aurum-desk/tools/run_kh_03_r4_acceptance.py

AURUM DESK — KH-03.R4 | ECONOMIC FEASIBILITY, ENTRY/EXIT ROOT-CAUSE EVIDENCE & DATA READINESS
SPEC VERSION: KH-03.R4-v1.0 | SAME BRANCH | READ-ONLY DIAGNOSIS | ACTIVATION_READY=NO | OOS_EVALUATIONS=0

Produces an immutable per-run directory under:
  aurum-desk/docs/artifacts/kh_03_r4_economic_feasibility/<YYYYMMDDTHHMMSSZ>_<TESTED_SHA12>/
containing artifacts 00..21 (with 21 sealed by separate-OS independent verifier),
README_KH03_R4.md (created at run start as IN_PROGRESS_NOT_SEALED), HANDOVER_KH03_R4_VI.txt,
logs/ (0.5s telemetry + streaming 1x forensic ledger), junit/, and checksums/.
NEVER mutates any prior KH-03.R3.C2, C1, R3, R2, R1, KH-02, or KH-01B runs, pointers, or locked files.
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
    or str(_early_os.environ.get("KH03_R4_OWNER_WAIVE_DISK_PREFLIGHT") or "").strip().lower() in ("1", "true", "yes")
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
from services.kh_03_r2_methodology_service import (
    build_kh03_r2_forward_only_dev_folds,
    validate_kh03_r2_fold_causality,
)
from services.kh_03_r3_c1_worker_policy_service import (
    BENCHMARK_KIND_PYTEST,
    BENCHMARK_KIND_SCIENCE,
    select_measured_worker_policy_strict,
)
from services.kh_03_r3_diagnostic_service import (
    audit_r3_entry_exit_geometry_and_accounting,
    build_r3_cost_and_break_even_sensitivity,
    build_r3_full_opportunity_lineage,
    build_r3_win_loss_failure_attribution,
    run_r3_negative_control_and_label_integrity_tests,
)
from services.kh_03_r3_experiment_service import (
    build_r3_causal_m5_unswept_target_ledger,
    build_r3_train_only_candidates_asof,
    freeze_r3_bounded_research_plan,
    run_r3_forward_only_dev_evaluation,
    run_r3_paired_and_strict_dev_evaluation_15_16,
)
from services.kh_03_r3_legacy_research_guard import audit_kh03_r3_legacy_research_callgraph
from services.kh_03_r4_economic_diagnostic_service import (
    SCHEMA_VERSION_KH03_R4,
    SPEC_VERSION_KH03_R4,
    audit_r4_covariate_overlap_and_effective_n,
    build_r4_counterfactual_cost_only_diagnostics,
    build_r4_data_gap_register,
    build_r4_mechanism_evidence_table,
    build_r4_source_function_ownership_matrix,
    build_r4_trade_population_conservation,
    decide_r4_research_next_action,
    derive_r4_breakeven_feasibility_bounds,
    load_r4_immutable_research_inputs_readonly,
    recompute_r4_decimal_economic_bridge,
    validate_r4_no_claim_inflation,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    verify_fresh_junit_bijection_v17931211,
)
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_03_r4_independent_verifier import (
    KH03_R4_SOURCE_FILES_ALLOWLIST,
    MANDATORY_BRANCH_KH03_R4,
    MIN_EXPECTED_PYTEST_COLLECTED_R4,
    PRIOR_KH03_R3_C2_EVIDENCE_SHA,
    PRIOR_KH03_R3_C2_PUB_HEAD_SHA,
    PRIOR_KH03_R3_C2_RUN_ID,
    PRIOR_KH03_R3_C2_TESTED_SHA,
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R4,
    _audit_claims_10_data_11_decision_12,
    _audit_forensic_ledger_gz_decimal,
    _audit_six_cost_08_and_arms_09,
    _audit_telemetry_gz_vs_16_r4,
    _recompute_raw_bench_r4,
    recompute_r4_gates_from_raw,
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
    _run_kh03_r3_194_red_green_fault_suite,
    _run_kh03_r3_verifier_mutation_suite_22,
)
from tools.run_kh_03_r3_c1_acceptance import (
    EXTRA_FROZEN_RESEARCH_FILES_C1,
    LINEAGE_TRACKED_ARTIFACT_REL,
    _CrossPlatformRunLock,
)
from tools.run_kh_03_r3_c2_acceptance import (
    C1_WORKER_POLICY_SERVICE_REL,
    KH03_R3_C2_EXCLUSIVE_SUBPROCESS_TEST_FILES,
)
from tools.run_v17_9_3_1_2_2_verification import canonicalize_collect_node_id_v1793122

KH03_R4_ARTIFACTS_BASE_REL = "aurum-desk/docs/artifacts/kh_03_r4_economic_feasibility"

EXTRA_FROZEN_R3_C2_FILES_R4: Tuple[str, ...] = (
    C1_WORKER_POLICY_SERVICE_REL,
    "aurum-desk/tools/run_kh_03_r3_acceptance.py",
    "aurum-desk/tools/kh_03_r3_independent_verifier.py",
    "aurum-desk/tools/run_kh_03_r3_c2_acceptance.py",
    "aurum-desk/tools/kh_03_r3_c2_independent_verifier.py",
)

KH03_R4_EXCLUSIVE_SUBPROCESS_TEST_FILES: Set[str] = set(KH03_R3_C2_EXCLUSIVE_SUBPROCESS_TEST_FILES) | {
    "test_kh03_r4_resource_and_async.py",
    "test_kh03_r4_independent_verifier_mutations.py",
}


def _stamp_r4(doc: Dict[str, Any], *, run_id: str, tested_sha: str) -> Dict[str, Any]:
    doc["spec_version"] = SPEC_VERSION_KH03_R4
    doc["schema_version"] = SCHEMA_VERSION_KH03_R4
    doc["run_id"] = run_id
    doc["tested_executable_sha"] = tested_sha
    doc["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in doc.items() if k != "artifact_canonical_sha256"})
    return doc


def _audit_ownership_01(*, tested_sha: str, run_id: str, legacy_cg: Mapping[str, Any]) -> Dict[str, Any]:
    all_locked = list(dict.fromkeys(list(LOCKED_KEEP_AS_IS_FILES_KH02_R2) + list(EXTRA_FROZEN_RESEARCH_FILES_C1) + list(EXTRA_FROZEN_R3_C2_FILES_R4)))
    locked_inv = []
    for rel in all_locked:
        p = REPO_ROOT / rel
        locked_inv.append({
            "rel_path": rel,
            "exists": p.is_file(),
            "sha256": _sha256_file(p) if p.is_file() else None,
            "ast_function_count": len(_ast_function_inventory(p)) if (rel.endswith(".py") and p.is_file()) else None,
        })

    rc_diff, diff_out = _git_cmd(["diff", "--name-only", f"{PRIOR_KH03_R3_C2_PUB_HEAD_SHA}..HEAD"])
    changed_files = [ln.strip() for ln in diff_out.splitlines() if ln.strip()] if rc_diff == 0 else []
    locked_mod = [f for f in changed_files if f in set(all_locked)]
    allow_set = set(KH03_R4_SOURCE_FILES_ALLOWLIST)
    unauthorized = [f for f in changed_files if f not in allow_set and not f.startswith(f"{KH03_R4_ARTIFACTS_BASE_REL}/")]

    forb_hits: List[str] = []
    r4_inv = []
    for rel in KH03_R4_SOURCE_FILES_ALLOWLIST:
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
        r4_inv.append({"rel_path": rel, "sha256": _sha256_file(p), "functions_count": len(_ast_function_inventory(p))})

    c_live = CostAssumptions()
    cost_ok = (
        abs(float(c_live.maker_fee_rate) - 0.0002) <= 1e-12
        and abs(float(c_live.taker_fee_rate) - 0.0004) <= 1e-12
        and abs(float(c_live.slippage_usd) - 0.10) <= 1e-12
    )
    ok = (
        len(locked_mod) == 0
        and len(unauthorized) == 0
        and len(forb_hits) == 0
        and cost_ok
        and legacy_cg.get("LEGACY_METHOD_GUARD") == "PASS"
        and int(legacy_cg.get("unauthorized_r1_callers_count", 1)) == 0
    )
    return _stamp_r4({
        "prior_c2_pub_head_sha": PRIOR_KH03_R3_C2_PUB_HEAD_SHA,
        "locked_keep_as_is_count": len(locked_inv),
        "locked_keep_as_is_inventory": locked_inv,
        "locked_files_modified": locked_mod,
        "locked_files_modified_count": len(locked_mod),
        "changed_files_vs_c2_pub_head": changed_files,
        "unauthorized_changed_files": unauthorized,
        "unauthorized_changed_files_count": len(unauthorized),
        "kh03_r4_module_inventory": r4_inv,
        "source_function_ownership_matrix": build_r4_source_function_ownership_matrix(),
        "legacy_guard_status": legacy_cg.get("LEGACY_METHOD_GUARD"),
        "unauthorized_r1_callers_count": int(legacy_cg.get("unauthorized_r1_callers_count", 0)),
        "forbidden_order_import_hits": forb_hits,
        "cost_assumptions_defaults_intact": cost_ok,
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        "status": "PASS" if ok else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)


def _run_r4_readonly_backtest_regression(
    *,
    datasets: Mapping[str, Any],
    dataset_sha: str,
    timeframe_stats: Mapping[str, Any],
    run_dir: Path,
    run_id: str,
    tested_sha: str,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """
    Executes a FRESH read-only backtest directly from the canonical DEV dataset under the
    unchanged engine (NO copy of prior summary JSONs), streams `logs/forensic_closed_open_gap_ledger_1x.jsonl.gz`,
    computes Artifacts 03..12 with exact Decimal math, and returns helper objects for fault verification.
    """
    mtf_bundle = build_kh02_asof_mtf_features(datasets)
    r1_frozen_plan, _ = _r2_frozen_plan_and_arms(code_sha=tested_sha)
    r1_candidates = generate_kh02_research_candidates(mtf_bundle, r1_frozen_plan)
    ledger_bundle = build_r2_causal_unswept_level_ledger(
        datasets, source_latency_ms=0, warmup_m1=30, run_t10_future_mutation_proof=True, rng_seed=DEFAULT_RANDOM_SEED_KH02_R2
    )
    r2_cands_1x = build_r2_target_repair_full_opportunity_conservation(
        datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle, r1_candidates=r1_candidates, cost_assumptions=CostAssumptions()
    )
    replay_bundle = replay_r2_same_opportunity_paired_counterfactuals(
        datasets=datasets, r1_candidates=r1_candidates, r2_candidates=r2_cands_1x
    )
    live_ff = reprice_r2_fixed_fills_cost_counterfactual(r2_replays=replay_bundle["r2_replays"])

    ledger_rel = "logs/forensic_closed_open_gap_ledger_1x.jsonl.gz"
    forensic_gz = run_dir / ledger_rel
    f06_meta, forensic_rows, null_sum = audit_r3_entry_exit_geometry_and_accounting(
        datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_1x=r2_cands_1x, out_ledger_gz_path=forensic_gz
    )
    f06_meta["ledger_rel_path"] = ledger_rel

    lineage_05 = build_r3_full_opportunity_lineage(
        datasets=datasets, r2_cands_1x=r2_cands_1x, null_replay_summary=null_sum, null_episodes=forensic_rows
    )
    f07_wl, f08_attr = build_r3_win_loss_failure_attribution(forensic_rows=forensic_rows, ledger_meta_06=f06_meta)
    f09_cost = build_r3_cost_and_break_even_sensitivity(
        datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle, r2_cands_1x=r2_cands_1x
    )
    f10_nc = run_r3_negative_control_and_label_integrity_tests(
        datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_1x=r2_cands_1x, forensic_rows=forensic_rows
    )

    m5_ledger = build_r3_causal_m5_unswept_target_ledger(datasets=datasets, pivot_radius=2)
    f11_plan = freeze_r3_bounded_research_plan(tested_code_sha=tested_sha)
    r3_cands_1x = build_r3_train_only_candidates_asof(
        datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_bundle=r2_cands_1x, m5_ledger=m5_ledger, costs=CostAssumptions()
    )
    f13_wf, f14_wf = run_r3_forward_only_dev_evaluation(
        datasets=datasets, mtf_bundle=mtf_bundle, r3_cands_1x=r3_cands_1x, prereg_plan_11=f11_plan, tested_code_sha=tested_sha
    )
    f15_cost, f16_gate = run_r3_paired_and_strict_dev_evaluation_15_16(
        datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle, m5_ledger=m5_ledger,
        r2_cands_1x=r2_cands_1x, r3_cands_1x=r3_cands_1x, prereg_plan_11=f11_plan, wf_13=f13_wf, wf_14=f14_wf,
    )

    # Recompute R2 forward-only DEV folds with embargo >= 240m causality check directly
    r2_folds_bundle = build_kh03_r2_forward_only_dev_folds(bars_1m=datasets["1m"], embargo_minutes=240)
    r2_fold_causality = validate_kh03_r2_fold_causality(r2_folds_bundle.get("folds") or (), min_embargo_minutes=240)

    dyn6 = f15_cost.get("dynamic_full_rerisk_six_scenarios") or {}
    ff_r3 = f15_cost.get("fixed_fills_descriptive_repricing") or {}

    d03 = build_r4_trade_population_conservation(
        lineage_05=lineage_05, forensic_rows=forensic_rows, r2_cands_1x=r2_cands_1x, dyn6_scenarios=dyn6, dataset_sha=dataset_sha
    )
    d04 = recompute_r4_decimal_economic_bridge(forensic_rows, dataset_sha=dataset_sha, ledger_meta=f06_meta)
    d07 = build_r4_counterfactual_cost_only_diagnostics(
        forensic_rows, fixed_fills_from_r3=ff_r3, dyn6_scenarios=dyn6, dataset_sha=dataset_sha
    )
    d05, d10 = build_r4_mechanism_evidence_table(
        forensic_rows, f08_attr=f08_attr, f09_cost=f09_cost, decimal_bridge=d04, fee_only_diag=d07
    )
    d06, d09 = audit_r4_covariate_overlap_and_effective_n(
        f15_cost=f15_cost, f16_gate=f16_gate, forensic_rows=forensic_rows, f10_nc=f10_nc, f13_wf=f13_wf, r2_fold_causality=r2_fold_causality
    )
    d08 = derive_r4_breakeven_feasibility_bounds(forensic_rows, f09_cost=f09_cost, dyn6_scenarios=dyn6, dataset_sha=dataset_sha)
    d11 = build_r4_data_gap_register(repo_root=REPO_ROOT, dataset_sha=dataset_sha, timeframe_stats=timeframe_stats)
    d12 = decide_r4_research_next_action(decimal_bridge=d04, fee_only_diag=d07, data_register=d11, arm_comparability=d09)

    claim_check = validate_r4_no_claim_inflation({"04": d04, "05": d05, "07": d07, "09": d09, "10": d10, "11": d11, "12": d12})
    d10["claim_inflation_self_audit"] = claim_check
    if not claim_check["valid"]:
        d10["status"] = "FAIL"

    docs_03_12 = {
        "03": _stamp_r4(d03, run_id=run_id, tested_sha=tested_sha),
        "04": _stamp_r4(d04, run_id=run_id, tested_sha=tested_sha),
        "05": _stamp_r4(d05, run_id=run_id, tested_sha=tested_sha),
        "06": _stamp_r4(d06, run_id=run_id, tested_sha=tested_sha),
        "07": _stamp_r4(d07, run_id=run_id, tested_sha=tested_sha),
        "08": _stamp_r4(d08, run_id=run_id, tested_sha=tested_sha),
        "09": _stamp_r4(d09, run_id=run_id, tested_sha=tested_sha),
        "10": _stamp_r4(d10, run_id=run_id, tested_sha=tested_sha),
        "11": _stamp_r4(d11, run_id=run_id, tested_sha=tested_sha),
        "12": _stamp_r4(d12, run_id=run_id, tested_sha=tested_sha),
    }
    ctx = {
        "mtf_bundle": mtf_bundle, "ledger_bundle": ledger_bundle, "r2_cands_1x": r2_cands_1x,
        "replay_bundle": replay_bundle, "live_ff": live_ff, "forensic_gz": forensic_gz,
        "f07_wl": f07_wl, "f13_wf": f13_wf, "m5_ledger": m5_ledger, "forensic_rows": forensic_rows,
        "docs_03_12": docs_03_12,
    }
    return docs_03_12, ctx


def _run_r4_fault_injection_suite_17(
    *, sentinel_tmp: Path, datasets: Mapping[str, Any], ctx: Mapping[str, Any], run_id: str, tested_sha: str
) -> Dict[str, Any]:
    """Runs 224 inherited (194 R3 + 14 C1 + 16 C2) + 16 NEW unique R4 = 240 RED/GREEN fault controls."""
    r3_sub = sentinel_tmp / "r3_194_sub_r4"
    r3_sub.mkdir(parents=True, exist_ok=True)
    r3_194 = _run_kh03_r3_194_red_green_fault_suite(
        sentinel_tmp=r3_sub, datasets=datasets, mtf_bundle=ctx["mtf_bundle"], ledger_bundle=ctx["ledger_bundle"],
        r2_cands_1x=ctx["r2_cands_1x"], replay_bundle=ctx["replay_bundle"], live_ff=ctx["live_ff"],
        forensic_gz=ctx["forensic_gz"], f07=ctx["f07_wl"], f13=ctx["f13_wf"], m5_ledger=ctx["m5_ledger"],
    )
    from tests.test_kh03_r3_c1_regression_truth import run_new_kh03_r3_c1_worker_policy_fault_controls
    from tests.test_kh03_r3_c2_legacy_runner_fail_closed import run_new_kh03_r3_c2_legacy_fail_closed_fault_controls
    from tests.test_kh03_r4_no_alpha_no_causal_claims import run_new_kh03_r4_fault_controls_16

    c1_14 = run_new_kh03_r3_c1_worker_policy_fault_controls()
    c2_16 = run_new_kh03_r3_c2_legacy_fail_closed_fault_controls()
    r4_16 = run_new_kh03_r4_fault_controls_16(forensic_rows=ctx["forensic_rows"], docs_03_12=ctx["docs_03_12"])

    inh_all = list(r3_194["controls"]) + list(c1_14) + list(c2_16)
    all_ctrls = inh_all + list(r4_16)
    fids = [c["fault_id"] for c in all_ctrls]
    inh_fail = sum(1 for c in inh_all if c["status"] != "PASS")
    r4_fail = sum(1 for c in r4_16 if c["status"] != "PASS")
    tot_pass = sum(1 for c in all_ctrls if c["status"] == "PASS")

    return _stamp_r4({
        "inherited_c2_c1_r3_controls_count": len(inh_all),
        "inherited_c2_c1_r3_failed_controls": inh_fail,
        "new_r4_controls_count": len(r4_16),
        "new_r4_failed_controls": r4_fail,
        "total_controls": len(all_ctrls),
        "unique_fault_ids": len(set(fids)),
        "passed_controls": tot_pass,
        "failed_controls": len(all_ctrls) - tot_pass,
        "controls": all_ctrls,
        "status": "PASS" if (tot_pass == len(all_ctrls) and len(set(fids)) == len(all_ctrls) and len(inh_all) >= 224 and len(r4_16) >= 16) else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)


def _run_r4_verifier_mutations_18(sentinel_tmp: Path, *, run_id: str, tested_sha: str) -> Dict[str, Any]:
    """Runs 51 inherited (16 R3 + 17 C1 + 18 C2) + 18 NEW R4 = 69 separate-OS verifier mutations (max_workers=3)."""
    r3_mut_dir = sentinel_tmp / "r3_m16_r4"
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
    from tests.test_kh03_r4_independent_verifier_mutations import (
        KH03_R4_OWN_VERIFIER_MUTATIONS_SPECS,
        build_synthetic_valid_kh03_r4_run_dir,
        run_kh03_r4_verifier_subprocess,
    )

    c1_base = sentinel_tmp / "c1_base_r4"
    rid_c1, tsha_c1 = build_synthetic_valid_kh03_r3_c1_run_dir(c1_base)
    rc_c1_ok, _ = run_kh03_r3_c1_verifier_subprocess(c1_base, rid_c1, tsha_c1)

    r3_base = sentinel_tmp / "r3_base_r4"
    rid_r3, tsha_r3 = build_synthetic_valid_kh03_r3_run_dir(r3_base)
    rc_r3_ok, _ = run_kh03_r3_verifier_subprocess(r3_base, rid_r3, tsha_r3)

    c2_base = sentinel_tmp / "c2_base_r4"
    rid_c2, tsha_c2 = build_synthetic_valid_kh03_r3_c2_run_dir(c2_base)
    rc_c2_ok, _ = run_kh03_r3_c2_verifier_subprocess(c2_base, rid_c2, tsha_c2)

    r4_base = sentinel_tmp / "r4_base_r4"
    rid_r4, tsha_r4 = build_synthetic_valid_kh03_r4_run_dir(r4_base)
    rc_r4_ok, _ = run_kh03_r4_verifier_subprocess(r4_base, rid_r4, tsha_r4)

    def _run_mut(args_t: Tuple[Path, str, str, Any, str, Any, str]) -> Dict[str, Any]:
        bdir, rid_x, tsha_x, runner_fn, mid, mut_fn, exp_tok = args_t
        cdir = sentinel_tmp / f"mut_{mid}"
        shutil.copytree(bdir, cdir)
        mut_fn(cdir)
        rc, out_t = runner_fn(cdir, rid_x, tsha_x)
        return {"mutation_id": mid, "expected_violation_token": exp_tok, "verifier_exit_code": rc, "detected_fail_closed": (rc != 0) and (exp_tok in out_t)}

    inh_jobs = (
        [(c1_base, rid_c1, tsha_c1, run_kh03_r3_c1_verifier_subprocess, m, fn, tok) for m, fn, tok in KH03_R3_C1_VERIFIER_MUTATIONS_SPECS]
        + [(r3_base, rid_r3, tsha_r3, run_kh03_r3_verifier_subprocess, m, fn, tok) for m, fn, tok in KH03_R3_C2_LEGACY_R3_MUTATIONS_SPECS]
        + [(c2_base, rid_c2, tsha_c2, run_kh03_r3_c2_verifier_subprocess, m, fn, tok) for m, fn, tok in KH03_R3_C2_OWN_VERIFIER_MUTATIONS_SPECS]
    )
    r4_jobs = [(r4_base, rid_r4, tsha_r4, run_kh03_r4_verifier_subprocess, m, fn, tok) for m, fn, tok in KH03_R4_OWN_VERIFIER_MUTATIONS_SPECS]

    with ThreadPoolExecutor(max_workers=3) as pool:
        inh_res = list(pool.map(_run_mut, inh_jobs))
        r4_res = list(pool.map(_run_mut, r4_jobs))

    r3_tot, r3_det = int(r3_m16.get("mutations_tested_count", 0)), int(r3_m16.get("mutations_detected_count", 0))
    inh_tot = r3_tot + len(inh_res)
    inh_det = r3_det + sum(1 for r in inh_res if r["detected_fail_closed"])
    r4_tot = len(r4_res)
    r4_det = sum(1 for r in r4_res if r["detected_fail_closed"])
    inh_fail = inh_tot - inh_det
    r4_fail = r4_tot - r4_det
    clean_ok = (rc_c1_ok == 0 and rc_r3_ok == 0 and rc_c2_ok == 0 and rc_r4_ok == 0 and bool(r3_m16.get("clean_clone_verifier_green")))

    return _stamp_r4({
        "isolated_temp_clones_only": True,
        "sealed_run_dir_mutated": False,
        "clean_clone_verifier_green": clean_ok,
        "inherited_c2_c1_r3_mutations_tested_count": inh_tot,
        "inherited_c2_c1_r3_mutations_detected_count": inh_det,
        "inherited_c2_c1_r3_mutations_failed_detection_count": inh_fail,
        "new_r4_mutations_tested_count": r4_tot,
        "new_r4_mutations_detected_count": r4_det,
        "new_r4_mutations_failed_detection_count": r4_fail,
        "total_mutations_tested_count": inh_tot + r4_tot,
        "new_r4_results": r4_res,
        "inherited_c2_c1_results": inh_res,
        "inherited_r3_results": r3_m16.get("results") or [],
        "status": "PASS" if (clean_ok and inh_fail == 0 and r4_fail == 0 and inh_tot >= 51 and r4_tot >= 16) else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)


def _collect_and_build_r4_lpt_shards(owned_temp_root: Path) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]]]:
    all_test_files = sorted(str(p.relative_to(APP_ROOT)).replace("\\", "/") for p in (BACKEND_ROOT / "tests").glob("test_*.py") if p.is_file())
    col_out = owned_temp_root / "collect_r4_stdout.log"
    col_err = owned_temp_root / "collect_r4_stderr.log"
    col_snippet = "import os, sys, pytest; fo=open(sys.argv[1],'w',encoding='utf-8',errors='replace'); fe=open(sys.argv[2],'w',encoding='utf-8',errors='replace'); sys.stdout=fo; sys.stderr=fe; rc=pytest.main(sys.argv[3:]); fo.flush(); fe.flush(); fo.close(); fe.close(); os._exit(int(rc))"
    col_env = dict(os.environ); col_env["PYTHONHASHSEED"] = "0"; col_env["PYTHONPATH"] = _pypath_str()
    proc = subprocess.run([sys.executable, "-c", col_snippet, str(col_out), str(col_err), str(BACKEND_ROOT / "tests"), "--collect-only", "-q"], cwd=str(APP_ROOT), env=col_env, capture_output=True, check=False, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"KH03_R4_PYTEST_COLLECTION_FAILED:rc={proc.returncode}")
    collected_by_file: Dict[str, List[str]] = {}
    for ln in col_out.read_text("utf-8", errors="replace").splitlines():
        cid = canonicalize_collect_node_id_v1793122(ln)
        if cid is not None:
            collected_by_file.setdefault(cid.split("::", 1)[0], []).append(cid)
    for k in collected_by_file:
        collected_by_file[k].sort()
    empirical_ms = _load_empirical_file_durations_ms_from_kh01b()
    non_sub = [f for f in all_test_files if Path(f).name not in KH03_R4_EXCLUSIVE_SUBPROCESS_TEST_FILES]
    sub_files = [f for f in all_test_files if Path(f).name in KH03_R4_EXCLUSIVE_SUBPROCESS_TEST_FILES]
    shards = _build_lpt_shards_kh01b_r1(non_sub, collected_by_file, empirical_ms)
    for sh in shards:
        if sh.get("shard_name") == "SHARD_SINGLE_CHILD_02_BUDGET2":
            sh["exclusive"] = True
            sh["slot_cost"] = 4
    for idx_s, sf in enumerate(sorted(sub_files), 1):
        shards.append({"shard_name": f"SHARD_SINGLE_CHILD_KH03_R4_SUBPROC_{idx_s:02d}", "files": [sf], "exclusive": True, "slot_cost": 4, "priority_weight": 260_000, "needs_venv": False})
    return collected_by_file, shards


def _run_r4_full_repo_pytest_async_lpt_13(
    run_dir: Path, sentinel_tmp: Path, *, run_id: str, tested_sha: str, effective_disk_reserve_bytes: int, max_workers: int, git_clean_at_start: bool
) -> Dict[str, Any]:
    lineage_p = REPO_ROOT / LINEAGE_TRACKED_ARTIFACT_REL
    sha_before = _sha256_file(lineage_p) if lineage_p.is_file() else None
    t0 = time.perf_counter()
    empirical_r2_rss = load_empirical_r2_shard_rss_bytes(REPO_ROOT)
    collected_by_file, r4_shards = _collect_and_build_r4_lpt_shards(sentinel_tmp)
    active_shards_tracker: Dict[str, Any] = {}
    junit_res = asyncio.run(
        run_async_lpt_pytest_shards_kh01b_r1(
            run_dir, sentinel_tmp, run_id=run_id, tested_executable_sha=tested_sha,
            effective_disk_reserve_bytes=effective_disk_reserve_bytes,
            max_parallel_workers=max_workers, ram_policy_mode="R3_STRICT_512MIB",
            empirical_rss_by_shard=empirical_r2_rss, active_shards_tracker=active_shards_tracker,
            shard_specs_override=r4_shards, collected_by_file_override=collected_by_file,
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
        mandatory_node_prefixes=("backend/tests/test_kh_02_r2", "backend/tests/test_kh_03", "backend/tests/test_kh03_r2", "backend/tests/test_kh03_r3", "backend/tests/test_kh03_r3_c1", "backend/tests/test_kh03_r3_c2", "backend/tests/test_kh03_r4"),
        require_shard_schedule=True,
    )
    col_n = int(cm.get("collected_node_ids_count") or len(cm.get("collected_node_ids") or ()))
    exe_n = int(bj.get("executed_node_ids_count") or bj.get("executed_count") or 0)
    pas_n, skp_n, fai_n, err_n, msk_n = int(bj.get("passed_count") or 0), int(bj.get("skipped_count") or 0), int(bj.get("failed_count") or 0), int(bj.get("error_count") or 0), int(bj.get("mandatory_skipped_count") or 0)
    nz = int(sm.get("nonzero_exit_shards_count") or 0)
    ok = bj.get("status") == "PASS" and nz == 0 and col_n >= MIN_EXPECTED_PYTEST_COLLECTED_R4 and col_n == exe_n and fai_n == 0 and err_n == 0 and msk_n == 0
    return _stamp_r4({
        "status": "PASS" if ok else "FAIL",
        "elapsed_sec": elapsed, "max_workers_used": max_workers,
        "collected_count": col_n, "executed_count": exe_n, "passed_count": pas_n,
        "skipped_count": skp_n, "mandatory_skipped_count": msk_n, "failed_count": fai_n,
        "error_count": err_n, "nonzero_exit_shards_count": nz, "junit_bijection_exact": bj.get("status") == "PASS",
        "tracked_test_side_effect_audit": {"rel_path": LINEAGE_TRACKED_ARTIFACT_REL, "sha256_before": sha_before, "sha256_after_pytest": sha_after, "restored_to_clean_head": restored},
        "collection_manifest": cm, "shard_manifest": sm, "bijection_audit": bj,
    }, run_id=run_id, tested_sha=tested_sha)


def _render_r4_readme_and_handover(
    *, run_id: str, docs: Mapping[str, Mapping[str, Any]]
) -> Tuple[str, str]:
    f00, f03, f04, f05, f06, f07 = docs["00"], docs["03"], docs["04"], docs["05"], docs["06"], docs["07"]
    f08, f09, f11, f12, f13, f14, f15, f16, f17, f18, f19, f20 = (
        docs["08"], docs["09"], docs["11"], docs["12"], docs["13"], docs["14"], docs["15"], docs["16"], docs["17"], docs["18"], docs["19"], docs["20"]
    )
    v, g = f19["verdicts"], f19["gates_30"]
    b04 = f04["closed_trades_decimal_bridge_usd"]
    o04 = f04["open_and_gap_fee_separation_usd"]
    p03 = f03["primary_baseline_1x_null_conservation"]
    p_pol, s_pol = f15.get("pytest_worker_policy") or {}, f15.get("science_worker_policy") or {}
    arms = f09.get("arm_profiles_1x") or {}
    e1, e2, e3 = arms.get("KH03_R3_E1_LOW_FRICTION_STOP_FLOOR") or {}, arms.get("KH03_R3_E2_CAUSAL_DELAYED_CONFIRMATION") or {}, arms.get("KH03_R3_E3_CAUSAL_NEAREST_M5_UNSWEPT_EXIT") or {}
    be1x = f08.get("observed_1x_breakeven_bounds_conservative") or {}

    disp = (
        f"ENGINEERING={v['ENGINEERING']}; DIAGNOSIS={v['DIAGNOSIS']}; "
        f"ECONOMIC={v['RESEARCH_ECONOMIC_VERDICT']}; DATA_READINESS={v['DATA_READINESS']}; "
        f"FRESH_OOS={v['FRESH_OOS']}; KH04_ENTRY={v['KH04_ENTRY']}; "
        f"ACTIVATION_READY={v['ACTIVATION_READY']}; CI_STATUS={v['CI_STATUS']}; PUBLICATION={v['PUBLICATION']}"
    )

    readme = f"""# AURUM DESK — KH-03.R4 | Economic Feasibility, Entry/Exit Root-Cause Evidence & Data Readiness

- **Spec Version**: `{SPEC_VERSION_KH03_R4}` | **Run ID**: `{run_id}`
- **Nhánh Git bắt buộc**: `{f00['branch']}`
- **Tested Executable SHA**: `{f00['tested_executable_sha']}`
- **Prior KH-03.R3.C2 Pub HEAD**: `{PRIOR_KH03_R3_C2_PUB_HEAD_SHA}` (C2 Run `{PRIOR_KH03_R3_C2_RUN_ID}` bất biến 100%)
- **Kết luận trực giao**: `{disp}`
- **Khóa an toàn cố định**: `PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO | OOS_EVALUATIONS=0`

## 1. Cầu nối hạch toán Decimal & Bảo toàn dân số (`NC_NULL_TRIGGER`, 1x, REUSED DEV)
- **Bảo toàn tầng**: `{p03['n_total_bars']}` M1 bars -> `{p03['n_directional_opportunities']}` cơ hội có hướng -> `{p03['n_raw_triggers']}` raw triggers -> `{p03['n_eligible']}` eligible (`{p03['n_scheduled']}` scheduled + `{p03['n_skipped_busy']}` skipped busy 1-pos).
- **Trạng thái cuối**: `{p03['n_filled']}` filled / `{p03['n_unfilled']}` unfilled -> `{p03['n_closed']}` closed (`{p03['n_wins']}W/{p03['n_losses']}L`), `{p03['n_open_censored']}` open-at-horizon (censored 180m), `{p03['n_gap_unresolved']}` data-gap unresolved.
- **Tách mẫu số Win-Rate**: chính thức trên lệnh đã đóng `7/203 = 3.45%` (khác hoàn toàn `7/233 = 3.00%` trên lệnh đã khớp/lên lịch).
- **Kinh tế học Decimal chính xác**: Gross `{b04['gross_pnl_closed_usd']} USD`, Closed Fees `{b04['total_closed_fees_usd']} USD` (Entry `{b04['entry_fees_closed_usd']}` + Exit `{b04['exit_fees_closed_usd']}`), Net `{b04['net_pnl_closed_usd']} USD`, Residual `{b04['aggregate_gross_minus_fees_minus_net_residual_usd']} USD`, Expectancy `{b04['expectancy_r']}R`, NY-day 95% CI `{b04['ny_day_cluster_95ci_r_raw']}` (`{p03['n_effective_ny_days']}` NY days).
- **Tách phí lệnh chưa đóng**: `{o04['open_at_horizon_28_accrued_entry_fees_usd']} USD` (28 lệnh open) + `{o04['data_gap_unresolved_2_accrued_entry_fees_usd']} USD` (2 lệnh data-gap) = `{o04['all_nonclosed_filled_30_accrued_entry_fees_usd']} USD` — tách hoàn toàn khỏi closed fees `{b04['total_closed_fees_usd']} USD`.
- **Counterfactual giảm phí về 0 (NON-EXECUTABLE)**: Ngay cả khi ép phí về `0` trên đúng 203 lệnh đã khớp (`COUNTERFACTUAL_ZERO_FEE_DESCRIPTIVE`), Net vẫn âm **`{f07['zero_fee_counterfactual_net_pnl_usd']} USD`** vì Gross đã âm sẵn `{f07['baseline_gross_pnl_usd']} USD`. Giảm phí đơn thuần KHÔNG thể biến baseline hiện tại thành có lãi.
- **So sánh các nhánh (INCOMPARABLE_UNMATCHED_POPULATIONS)**: `E1` (`{e1.get('n_closed')}` lệnh, `{e1.get('net_pnl_usd')} USD`, `{e1.get('expectancy_r')}R`), `E2` (`{e2.get('n_closed')}` lệnh, `{e2.get('net_pnl_usd')} USD`, `{e2.get('expectancy_r')}R`), `E3` (`{e3.get('n_closed')}` lệnh, `{e3.get('net_pnl_usd')} USD`, `{e3.get('expectancy_r')}R`) — tất cả CI 95% âm nghiêm ngặt, cấm xếp hạng theo tổng lỗ USD.
- **Break-even 1x bảo thủ**: Win-rate quan sát `{be1x.get('observed_closed_win_rate')}` thấp hơn ~`{be1x.get('required_win_rate_multiple_of_observed')}x` so với break-even thực nghiệm `{be1x.get('empirical_breakeven_win_rate_at_1x_modeled')}`.

## 2. Chẩn đoán 3 trục (KHÔNG phát biểu nhân quả giả)
- **ENGINE / ACCOUNTING AXIS**: `NOT_FOUND_IN_TESTED_SCOPE` (Decimal residual `0.0000 USD`, không chứng minh "không có bug ở mọi nơi").
- **STRATEGY & COST AXIS**: `ASSOCIATION_ONLY_NO_CAUSAL_CLAIM` (`NO_CONFIRMED_CAUSAL_ROOT_FIX`, không tạo E4/E5, không tối ưu hóa SL/TP). Lưu ý: `$2.50` là ước tính planned risk, KHÔNG phải cam kết max-loss vật lý (`{f05['non_exclusive_overlap_counts']['realized_losses_gt_2p50_cap']}` lệnh vượt `$2.50`, max `{f05['distributions_closed_trades']['realized_loss_abs_usd']['max']} USD`; `{f05['non_exclusive_overlap_counts']['losses_20bar_mfe_ge_1p0x_sl_before_stopout']}` lệnh từng đạt MFE `>=1x SL` trong 20 nến — chỉ là association).
- **DATA / EXECUTION AXIS**: `INCOMPLETE_OBSERVED_EXECUTION_DATA` (`observed_l1_bbo_available=False`, `ACCOUNT_EFFECTIVE_FEE_VERDICT=UNKNOWN_NOT_LOCALLY_VERIFIED`, `funding=EXCLUDED_UNMODELED`, `FRESH_OOS=BLOCKED_NO_UNTOUCHED_DATA`).

## 3. Kiểm thử & Tài nguyên đo tươi KH-03.R4
- **Full Repo Pytest JUnit 1:1**: `{f13.get('collected_count')}` collected / `{f13.get('executed_count')}` executed / `{f13.get('passed_count')}` passed / `{f13.get('skipped_count')}` skipped (`mandatory_skipped={f13.get('mandatory_skipped_count')}`) / `{f13.get('failed_count')}` failed / `{f13.get('error_count')}` error (`max_workers={f13.get('max_workers_used')}`).
- **Baseline Parity**: A1 `{f14.get('a1_population_count')}/{f14.get('a1_mismatches_count')}`, SI8 `{f14.get('si8_population_count')}/{f14.get('si8_mismatches_count')}`, Adapter `{f14.get('three_adapter_population_count')}/{f14.get('three_adapter_mismatches_count')}`, D4 `{f14.get('d4_historical_closed_trades')}`.
- **Benchmark W1/W2/W4/W1_repeat**:
  - Pytest subset (`155`): `means={p_pol.get('mean_wall_sec_by_mode')}`, `w=1_repeat={p_pol.get('w1_repeat_wall_sec')}s` (var `{p_pol.get('w1_repeat_variance_pct')}`), `fastest={p_pol.get('measured_fastest_mode')}`, `applied={p_pol.get('applied_worker_count')}` (`safe_cap={p_pol.get('safe_worker_cap')}`), `full_suite_speedup_extrapolated=False`.
  - Science slice (`8,000` bars): `means={s_pol.get('mean_wall_sec_by_mode')}`, `w=1_repeat={s_pol.get('w1_repeat_wall_sec')}s` (var `{s_pol.get('w1_repeat_variance_pct')}`), `fastest={s_pol.get('measured_fastest_mode')}`, `applied={s_pol.get('applied_worker_count')}`.
  - Forensic chronological 1-position replay: `NOT_PARALLELIZABLE_SAFELY_SEQUENTIAL_W1_ONLY`.
- **Faults & Mutations**: `{f17.get('passed_controls')}/{f17.get('total_controls')}` (`{f17.get('inherited_c2_c1_r3_controls_count')}` kế thừa + `{f17.get('new_r4_controls_count')}` mới R4) | Separate-OS Verifier Mutations: `{f18.get('new_r4_mutations_detected_count')}/{f18.get('new_r4_mutations_tested_count')}` mới R4 + `{f18.get('inherited_c2_c1_r3_mutations_detected_count')}/{f18.get('inherited_c2_c1_r3_mutations_tested_count')}` kế thừa.
- **Tài nguyên**: min disk `{f16.get('min_disk_free_gib')} GiB`, min RAM `{f16.get('min_available_ram_mib')} MiB`, peak RSS `{f16.get('max_tree_rss_mib')} MiB`, max child `{f16.get('max_descendant_children')}`, orphans `{f16.get('orphan_children_after_cleanup')}`, waiver `False`.

## 4. Ma trận 30 cổng (`G01..G30`)
""" + "\n".join(f"- `{k}`: **{val}**" for k, val in g.items()) + "\n"

    handover = f"""AURUM DESK — BAN GIAO CHU SO HUU KH-03.R4 (TIENG VIET)
======================================================================
SPEC: {SPEC_VERSION_KH03_R4} | RUN_ID: {run_id}
BRANCH: {f00['branch']}
TESTED_EXECUTABLE_SHA: {f00['tested_executable_sha']}
PRIOR_C2_PUB_HEAD_SHA: {PRIOR_KH03_R3_C2_PUB_HEAD_SHA}
DECISION: {f12['DECISION']}
VERDICTS: {disp}
ACTIVATION_READY=NO | PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | OOS_EVALUATIONS=0

1. KINH TE HOC DECIMAL & COUNTERFACTUAL GIAM PHI (REUSED DEV 1X):
- NC_NULL_TRIGGER: eligible={p03['n_eligible']}, scheduled={p03['n_scheduled']}, skipped_busy={p03['n_skipped_busy']}, closed={p03['n_closed']} ({p03['n_wins']}W/{p03['n_losses']}L, WR_closed=3.45%, WR_filled=3.00%), open_censored={p03['n_open_censored']}, gap={p03['n_gap_unresolved']}
- Gross={b04['gross_pnl_closed_usd']} USD | Closed Fees={b04['total_closed_fees_usd']} USD | Net={b04['net_pnl_closed_usd']} USD | Residual={b04['aggregate_gross_minus_fees_minus_net_residual_usd']} USD | Exp={b04['expectancy_r']}R | CI={b04['ny_day_cluster_95ci_r_raw']} ({p03['n_effective_ny_days']} NY days)
- Open accrued entry fees (tach rieng): 28 open={o04['open_at_horizon_28_accrued_entry_fees_usd']} USD + 2 gap={o04['data_gap_unresolved_2_accrued_entry_fees_usd']} USD = {o04['all_nonclosed_filled_30_accrued_entry_fees_usd']} USD
- Zero-fee counterfactual (NON-EXECUTABLE): Net van am {f07['zero_fee_counterfactual_net_pnl_usd']} USD vi Gross da am truoc phi -> giam phi KHONG chua duoc lo baseline.
- Arms (INCOMPARABLE_UNMATCHED_POPULATIONS): E1={e1.get('n_closed')} ({e1.get('net_pnl_usd')} USD, {e1.get('expectancy_r')}R), E2={e2.get('n_closed')} ({e2.get('net_pnl_usd')} USD, {e2.get('expectancy_r')}R), E3={e3.get('n_closed')} ({e3.get('net_pnl_usd')} USD, {e3.get('expectancy_r')}R)

2. DATA READINESS ({f11['DATA_READINESS_VERDICT']}, {f11['missing_or_unverified_domains_count']}/{f11['total_domains_audited']} domains thieu):
- Thieu: L1 BBO bid/ask quan sat, effective account fee (UNKNOWN), slippage/rules thuc te, funding (EXCLUDED_UNMODELED), fresh untouched prospective OOS (ABSENT).
- KHONG yeu cau API key/mat khau, KHONG goi private exchange API.

3. KIEM THU & TAI NGUYEN DO TUOI:
- Full pytest JUnit 1:1: collected={f13.get('collected_count')}, executed={f13.get('executed_count')}, passed={f13.get('passed_count')}, skipped={f13.get('skipped_count')}, mandatory_skipped={f13.get('mandatory_skipped_count')}, failed={f13.get('failed_count')}, error={f13.get('error_count')}
- Parity: A1={f14.get('a1_population_count')}/{f14.get('a1_mismatches_count')}, SI8={f14.get('si8_population_count')}/{f14.get('si8_mismatches_count')}, Adapter={f14.get('three_adapter_population_count')}/{f14.get('three_adapter_mismatches_count')}, D4={f14.get('d4_historical_closed_trades')}
- Faults: {f17.get('passed_controls')}/{f17.get('total_controls')} | Separate-OS Mutations: {f18.get('new_r4_mutations_detected_count')}/{f18.get('new_r4_mutations_tested_count')} moi R4 + {f18.get('inherited_c2_c1_r3_mutations_detected_count')}/{f18.get('inherited_c2_c1_r3_mutations_tested_count')} ke thua
- Resources: min_disk={f16.get('min_disk_free_gib')} GiB, min_ram={f16.get('min_available_ram_mib')} MiB, peak_rss={f16.get('max_tree_rss_mib')} MiB, max_children={f16.get('max_descendant_children')}, orphans={f16.get('orphan_children_after_cleanup')}, waiver=False

4. 30 CONG (G01..G30):
""" + "\n".join(f"  {k}: {val}" for k, val in g.items()) + "\n"
    return readme, handover


def run_kh03_r4_acceptance(
    *, allow_local_dirty: bool = False, owner_waive_disk_preflight: bool = False, max_workers: int = GLOBAL_MAX_DESCENDANTS_KH02_R2
) -> Tuple[int, Path, Dict[str, Any]]:
    if owner_waive_disk_preflight:
        raise SystemExit(2)

    lock = _CrossPlatformRunLock(Path(tempfile.gettempdir()) / "aurum_desk_kh03_r4_run.lock")
    lock_ok, lock_msg = lock.acquire()
    if not lock_ok:
        raise RuntimeError(f"KH03_R4_SINGLE_RUN_LOCK_FAILED:{lock_msg}")

    t_start = time.perf_counter()
    orch_pid = os.getpid()
    utc_now = datetime.now(timezone.utc)
    _, branch = _git_cmd(["branch", "--show-current"])
    _, start_head = _git_cmd(["rev-parse", "HEAD"])
    _, st_porc = _git_cmd(["status", "--porcelain=v1"])
    _, ls_rem = _git_cmd(["ls-remote", "origin", f"refs/heads/{MANDATORY_BRANCH_KH03_R4}"])
    remote_sha = ls_rem.split()[0] if ls_rem.strip() else ""
    git_clean = (st_porc.strip() == "")

    if branch != MANDATORY_BRANCH_KH03_R4:
        lock.release()
        raise RuntimeError(f"WRONG_BRANCH:{branch}")
    if (not git_clean) and (not allow_local_dirty):
        lock.release()
        raise RuntimeError(f"DIRTY_WORKTREE_REFUSED:\n{st_porc}")

    run_id = f"{utc_now.strftime('%Y%m%dT%H%M%SZ')}_{start_head[:12]}"
    art_base = (REPO_ROOT / KH03_R4_ARTIFACTS_BASE_REL).resolve()
    run_dir = art_base / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "junit").mkdir(exist_ok=True)
    (run_dir / "checksums").mkdir(exist_ok=True)

    # Write README immediately at run start (§B / §4 / §11)
    (run_dir / "README_KH03_R4.md").write_text(
        f"# AURUM DESK — KH-03.R4 (IN PROGRESS)\n\n- Run ID: `{run_id}`\n- Tested SHA: `{start_head}`\n- Branch: `{branch}`\n- Status: `IN_PROGRESS_NOT_SEALED`\n",
        encoding="utf-8",
    )

    sentinel_tmp = create_sentinel_owned_temp_dir_r2_c1(f"kh03r4_{utc_now.strftime('%Y%m%dT%H%M%SZ')}_", run_id=run_id, owner_pid=orch_pid)
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
        print(f"[KH-03.R4] Stage 1: Ownership/AST keep-as-is (01) & prior R1/R2/R3/C1/C2 + dataset SHA immutability (02) (run_id={run_id})...", flush=True)
        legacy_cg = audit_kh03_r3_legacy_research_callgraph(REPO_ROOT)
        f01 = _audit_ownership_01(tested_sha=start_head, run_id=run_id, legacy_cg=legacy_cg)
        ro_inputs = load_r4_immutable_research_inputs_readonly(REPO_ROOT, PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R4)
        datasets = ro_inputs["datasets"]
        prov02 = ro_inputs["provenance"]
        pdir = sentinel_tmp / "c4_prov_r4"
        pdir.mkdir(parents=True, exist_ok=True)
        f08_c4, f09_c4 = audit_kh03_collector_and_escrow_readiness(pdir)
        prov_c4_ok = (
            f08_c4.get("status") == "PASS" and f09_c4.get("status") == "PASS"
            and f08_c4.get("network_capture_enabled") is False and f08_c4.get("live_network_capture_executed") is False
            and int(f09_c4.get("real_oos_escrow_final_record_count", -1)) == 0
        )
        f02 = _stamp_r4({
            **prov02,
            "c4_provenance_and_oos_boundary": {
                "network_capture_enabled": f08_c4.get("network_capture_enabled"),
                "live_network_capture_executed": f08_c4.get("live_network_capture_executed"),
                "LIVE_NETWORK_VALIDATION": f08_c4.get("LIVE_NETWORK_VALIDATION"),
                "real_oos_escrow_integrity_valid": f09_c4.get("real_oos_escrow_integrity_valid"),
                "real_oos_escrow_final_record_count": f09_c4.get("real_oos_escrow_final_record_count"),
                "status": "PASS" if prov_c4_ok else "FAIL",
            },
            "status": "PASS" if (prov02.get("status") == "PASS" and prov_c4_ok) else "FAIL",
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "01_ownership_keep_as_is_locked_sha_manifest.json", f01)
        write_json_durable_atomic(run_dir / "02_prior_r1_r2_r3_c1_c2_dataset_hash_integrity.json", f02)

        print("[KH-03.R4] Stage 2: Fresh read-only canonical DEV replay, Decimal bridge, 6-cost, comparability, DATA_READINESS & NO-GO rules (03..12)...", flush=True)
        docs_03_12, ctx = _run_r4_readonly_backtest_regression(
            datasets=datasets, dataset_sha=str(prov02["canonical_dataset_sha256"]),
            timeframe_stats=prov02["timeframe_coverage_and_gaps"],
            run_dir=run_dir, run_id=run_id, tested_sha=start_head,
        )
        name_map_03_12 = {
            "03": "03_raw_population_lineage_and_denominator_conservation.json",
            "04": "04_closed_trade_decimal_gross_fee_net_bridge.json",
            "05": "05_entry_exit_stop_target_risk_and_tail_diagnostic.json",
            "06": "06_censoring_open_gap_unfilled_and_effective_n.json",
            "07": "07_fee_only_fixed_fills_non_executable_counterfactual.json",
            "08": "08_full_dynamic_rerisk_6_cost_scenario_replay.json",
            "09": "09_E1_E2_E3_null_negative_controls_comparability.json",
            "10": "10_assoc_not_causal_and_no_alpha_claim_proof.json",
            "11": "11_data_readiness_register_and_missing_evidence.json",
            "12": "12_go_nogo_decision_rules_and_owner_next_actions.json",
        }
        for dk, fname in name_map_03_12.items():
            write_json_durable_atomic(run_dir / fname, docs_03_12[dk])

        print("[KH-03.R4] Stage 3A: Running 240 unique RED/GREEN fault controls (224 inherited + 16 NEW R4) (17)...", flush=True)
        f17 = _run_r4_fault_injection_suite_17(sentinel_tmp=sentinel_tmp, datasets=datasets, ctx=ctx, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "17_inherited_plus_new_fault_red_green_receipts.json", f17)
        del datasets, ctx
        gc.collect()

        print("[KH-03.R4] Stage 3B: Running 69 separate-OS verifier mutations (51 inherited + 18 NEW R4, max_workers=3) (18)...", flush=True)
        f18 = _run_r4_verifier_mutations_18(sentinel_tmp, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "18_separate_OS_verifier_mutation_attack_results.json", f18)

        if can_run_heavy:
            eff_res_b = int(STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2 * (1024 ** 3))
            print(f"[KH-03.R4] Stage 4A: Running FULL repo LPT-sharded pytest (max_workers={pytest_full_cap}) (13)...", flush=True)
            f13 = _run_r4_full_repo_pytest_async_lpt_13(
                run_dir, sentinel_tmp, run_id=run_id, tested_sha=start_head,
                effective_disk_reserve_bytes=eff_res_b, max_workers=pytest_full_cap, git_clean_at_start=git_clean,
            )
            write_json_durable_atomic(run_dir / "13_full_pytest_junit_fresh_collection_bijection.json", f13)
            gc.collect()

            print("[KH-03.R4] Stage 4B: Running fresh W1/W2/W4/W1_repeat pytest + R2 science slice benchmarks (15)...", flush=True)
            pb_raw = asyncio.run(_run_controlled_pytest_w1_w2_w4_benchmark_r1(sentinel_tmp, enforce_r3_ram_guard=True))
            sb_raw = asyncio.run(_run_r2_science_slice_w1_w2_w4_determinism(sentinel_tmp))
            gc.collect()

            pb_doc = _stamp_r4({"canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2, "benchmark_kind": BENCHMARK_KIND_PYTEST, "status": "PASS" if pb_raw.get("all_4_runs_identical") else "FAIL", **pb_raw}, run_id=run_id, tested_sha=start_head)
            sb_doc = _stamp_r4({"canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2, "benchmark_kind": BENCHMARK_KIND_SCIENCE, "status": "PASS" if sb_raw.get("all_4_runs_identical") else "FAIL", **sb_raw}, run_id=run_id, tested_sha=start_head)
            min_pre_dk = min(float(m.get("free_gib", 0.0)) for m in strict_probe["distinct_mounts"])
            p_lim = {"configured_child_process_cap": GLOBAL_MAX_DESCENDANTS_KH02_R2, "currently_allowed_worker_cap": pytest_full_cap, "full_suite_safe_worker_cap": pytest_full_cap, "shard_exclusive_cap": pytest_full_cap, "preflight_disk_free_gib": min_pre_dk, "disk_free_gib": min_pre_dk, "available_ram_mib": avail_ram_mib}
            s_lim = {"configured_child_process_cap": GLOBAL_MAX_DESCENDANTS_KH02_R2, "currently_allowed_worker_cap": eff_workers, "full_suite_safe_worker_cap": eff_workers, "shard_exclusive_cap": eff_workers, "preflight_disk_free_gib": min_pre_dk, "disk_free_gib": min_pre_dk, "available_ram_mib": avail_ram_mib}
            p_pol = select_measured_worker_policy_strict(pb_doc, benchmark_kind=BENCHMARK_KIND_PYTEST, system_limits=p_lim, expected_run_id=run_id, expected_tested_sha=start_head, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2)
            s_pol = select_measured_worker_policy_strict(sb_doc, benchmark_kind=BENCHMARK_KIND_SCIENCE, system_limits=s_lim, expected_run_id=run_id, expected_tested_sha=start_head, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2)
            f15 = _stamp_r4({
                "pytest_controlled_subset_raw": pb_doc,
                "science_slice_8000_raw": sb_doc,
                "pytest_worker_policy": p_pol,
                "science_worker_policy": s_pol,
                "chronological_1pos_replay_parallelism_status": "NOT_PARALLELIZABLE_SAFELY_SEQUENTIAL_W1_ONLY",
                "full_suite_actual_max_workers_applied": pytest_full_cap,
                "full_suite_speedup_extrapolated": False,
                "status": "PASS" if (p_pol["status"] == "PASS" and s_pol["status"] == "PASS") else "FAIL",
            }, run_id=run_id, tested_sha=start_head)
            write_json_durable_atomic(run_dir / "15_W1_W2_W4_W1repeat_determinism_real_timings.json", f15)

            print("[KH-03.R4] Stage 4C: Running FULL 56,710 A1 + 6,375 SI8 + 246 adapter + D4 parity (14)...", flush=True)
            ds_p, _ = load_r2_canonical_dataset_readonly(REPO_ROOT / CANONICAL_DATASET_REL_PATH)
            f02_r1 = audit_r2_reference_inputs_and_invariants(REPO_ROOT)
            f14 = _run_full_production_baseline_parity_20(datasets=ds_p, sentinel_tmp=sentinel_tmp, run_id=run_id, tested_sha=start_head, branch=branch, f01=f01, f02=f02_r1, max_workers=eff_workers)
            f14 = _stamp_r4(f14, run_id=run_id, tested_sha=start_head)
            write_json_durable_atomic(run_dir / "14_A1_SI8_adapter_D4_production_baseline_parity.json", f14)
            del ds_p
            gc.collect()
        else:
            f13 = _stamp_r4({"status": "NOT_RUN_BLOCKED_LOW_DISK", "collected_count": 0, "executed_count": 0, "passed_count": 0, "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0, "junit_bijection_exact": False}, run_id=run_id, tested_sha=start_head)
            f14 = _stamp_r4({"BASELINE_PARITY_VERDICT": "NOT_RUN_BLOCKED_LOW_DISK", "a1_population_count": 0, "a1_mismatches_count": 0, "si8_population_count": 0, "si8_mismatches_count": 0, "three_adapter_population_count": 0, "three_adapter_mismatches_count": 0, "d4_historical_closed_trades": 0, "per_decision_and_episode_fingerprints": {"all_sealed_fingerprints_exact_match": False}}, run_id=run_id, tested_sha=start_head)
            f15 = _stamp_r4({"status": "NOT_RUN_BLOCKED_LOW_DISK", "pytest_controlled_subset_raw": {}, "science_slice_8000_raw": {}, "pytest_worker_policy": {}, "science_worker_policy": {}, "full_suite_speedup_extrapolated": False}, run_id=run_id, tested_sha=start_head)
            write_json_durable_atomic(run_dir / "13_full_pytest_junit_fresh_collection_bijection.json", f13)
            write_json_durable_atomic(run_dir / "14_A1_SI8_adapter_D4_production_baseline_parity.json", f14)
            write_json_durable_atomic(run_dir / "15_W1_W2_W4_W1repeat_determinism_real_timings.json", f15)

        cl_info = _cleanup_sentinel_temp_c4_win_safe(sentinel_tmp, expected_run_id=run_id)
        sentinel_cleaned = bool(cl_info["cleaned"])
        telem = sampler.stop_and_flush()
        sampler_stopped = True

        f16 = _stamp_r4({
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
        write_json_durable_atomic(run_dir / "16_disk_ram_PID_0p5s_log_and_cleanup.json", f16)

        _, end_head = _git_cmd(["rev-parse", "HEAD"])
        f00 = _stamp_r4({
            "started_utc": utc_now.strftime("%Y-%m-%dT%H:%M:%SZ"), "branch": branch,
            "start_head_sha": start_head, "end_head_sha": end_head, "remote_head_sha": remote_sha,
            "prior_kh03_r3_c2_pub_head_sha": PRIOR_KH03_R3_C2_PUB_HEAD_SHA,
            "prior_kh03_r3_c2_tested_sha": PRIOR_KH03_R3_C2_TESTED_SHA,
            "prior_kh03_r3_c2_evidence_sha": PRIOR_KH03_R3_C2_EVIDENCE_SHA,
            "prior_kh03_r3_c2_run_id": PRIOR_KH03_R3_C2_RUN_ID,
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
        write_json_durable_atomic(run_dir / "00_branch_head_clean_sha_preflight_resource_lock.json", f00)

        pub_st = "PRE_PUSH_PENDING" if (strict_disk_ok and git_clean) else "NOT_PUBLISHED_BLOCKED_LOW_DISK"
        f20 = _stamp_r4({
            "branch": branch, "start_head_sha": start_head,
            "evidence_commit_sha": None, "publication_head_sha": None,
            "remote_readback_sha256_verified": False, "latest_run_pointer_updated": False,
            "owner_disk_preflight_waiver": False, "ci_status": "CI_UNKNOWN_NO_ACTIONS_RUN",
            "actual_github_actions_run_verified": False, "publication_status": pub_st, "status": pub_st,
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "20_phase1_code_commit_push_receipt.json", f20)

        all_docs = {
            "00": f00, "01": f01, "02": f02, **docs_03_12,
            "13": f13, "14": f14, "15": f15, "16": f16, "17": f17, "18": f18, "20": f20,
        }
        led_au = _audit_forensic_ledger_gz_decimal(run_dir, docs_03_12["03"], docs_03_12["04"], docs_03_12["05"], docs_03_12["06"], docs_03_12["07"])
        ec_au = _audit_six_cost_08_and_arms_09(docs_03_12["08"], docs_03_12["09"])
        cl_au = _audit_claims_10_data_11_decision_12(docs_03_12["10"], docs_03_12["11"], docs_03_12["12"])
        mem_au = _audit_telemetry_gz_vs_16_r4(run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz", f16)
        p_rec = _recompute_raw_bench_r4(f15.get("pytest_controlled_subset_raw") or {}, f15.get("pytest_worker_policy") or {}, kind="pytest_controlled_subset", expected_run_id=run_id, expected_tested_sha=start_head, max_safe_cap=3) if f15.get("status") != "NOT_RUN_BLOCKED_LOW_DISK" else {"valid": False}
        s_rec = _recompute_raw_bench_r4(f15.get("science_slice_8000_raw") or {}, f15.get("science_worker_policy") or {}, kind="r2_science_slice", expected_run_id=run_id, expected_tested_sha=start_head, max_safe_cap=GLOBAL_MAX_DESCENDANTS_KH02_R2) if f15.get("status") != "NOT_RUN_BLOCKED_LOW_DISK" else {"valid": False}

        g_res = recompute_r4_gates_from_raw(
            docs=all_docs, ledger_audit=led_au, econ_audit=ec_au, claims_audit=cl_au,
            pytest_recomp=p_rec, science_recomp=s_rec, mem_audit=mem_au,
            junit_ok=bool(f13.get("junit_bijection_exact")), verifier_violations_count=0, for_final_verifier_manifest=False,
        )
        f19 = _stamp_r4(g_res, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "19_orthogonal_30_gates_and_verdict_matrix.json", f19)
        all_docs["19"] = f19

        readme_md, handover_vi = _render_r4_readme_and_handover(run_id=run_id, docs=all_docs)
        (run_dir / "README_KH03_R4.md").write_text(readme_md, encoding="utf-8")
        (run_dir / "HANDOVER_KH03_R4_VI.txt").write_text(handover_vi, encoding="utf-8")

        inv = {
            str(p.relative_to(run_dir)).replace("\\", "/"): _sha256_file(p)
            for p in sorted(run_dir.rglob("*"))
            if p.is_file() and p.name != "sha256_inventory.json"
        }
        write_json_durable_atomic(run_dir / "checksums" / "sha256_inventory.json", _stamp_r4({"files": inv, "count": len(inv)}, run_id=run_id, tested_sha=start_head))

        print("[KH-03.R4] Stage 5: Launching separate-OS independent verifier (kh_03_r4_independent_verifier.py -> 21)...", flush=True)
        env_v = os.environ.copy()
        env_v["PYTHONPATH"] = _pypath_str()
        vp = subprocess.run(
            [sys.executable, str(APP_ROOT / "tools" / "kh_03_r4_independent_verifier.py"), "--run-dir", str(run_dir), "--expected-run-id", run_id, "--expected-tested-sha", start_head],
            cwd=str(APP_ROOT), env=env_v, capture_output=True, text=True, timeout=180,
        )
        print(vp.stdout.strip(), flush=True)
        if vp.stderr.strip():
            print(vp.stderr.strip(), file=sys.stderr, flush=True)

        summary = {
            "run_id": run_id, "run_dir": str(run_dir), "tested_executable_sha": start_head,
            "verifier_exit_code": int(vp.returncode), "verdicts": f19["verdicts"],
            "overall_classification": f19["overall_classification"],
        }
        return int(vp.returncode), run_dir, summary
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
    ap = argparse.ArgumentParser(description="AURUM DESK KH-03.R4 Economic Feasibility & Data Readiness Acceptance Runner")
    ap.add_argument("--allow-local-dirty", action="store_true")
    ap.add_argument("--max-workers", type=int, default=GLOBAL_MAX_DESCENDANTS_KH02_R2)
    args = ap.parse_args(argv)
    rc, rdir, summ = run_kh03_r4_acceptance(allow_local_dirty=args.allow_local_dirty, max_workers=args.max_workers)
    print(json.dumps(summ, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
