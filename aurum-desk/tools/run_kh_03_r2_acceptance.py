#!/usr/bin/env python3
"""
aurum-desk/tools/run_kh_03_r2_acceptance.py

AURUM DESK — KH-03.R2 | RESEARCH METHODOLOGY CLOSURE ACCEPTANCE ORCHESTRATOR
SPEC VERSION: KH-03.R2-v1.0 | RESEARCH ONLY | SAME BRANCH | ACTIVATION_READY=NO | OOS_EVALUATIONS=0

Produces a clean, immutable per-run directory under:
  aurum-desk/docs/artifacts/kh_03_r2_methodology_closure/<UTC_RUN_ID>_<TESTED_SHA12>/
containing artifacts 00..26 (with 25 sealed by separate-OS independent verifier),
README_KH03_R2.md, HANDOVER_KH03_R2_VI.txt, checksums/, logs/, and junit/.
NEVER mutates any prior KH-03.R1, C4, C3, C2, C1, KH-02, or KH-01B sealed runs or pointers.
"""
from __future__ import annotations

import json as _early_json
import os as _early_os
import sys as _early_sys

if __name__ == "__main__" and (
    "--owner-waive-disk-preflight" in _early_sys.argv
    or str(_early_os.environ.get("KH02_OWNER_WAIVE_DISK_PREFLIGHT") or "").strip().lower() in ("1", "true", "yes")
    or str(_early_os.environ.get("KH03_OWNER_WAIVE_DISK_PREFLIGHT") or "").strip().lower() in ("1", "true", "yes")
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
import time
from typing import Any, Dict, List, Mapping, Set, Tuple

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
from services.kh_02_r2_c4_risk_tail_evidence import (
    build_c4_per_episode_risk_ledger,
    build_c4_realized_tail_evidence_from_r2_replays,
    recompute_c4_tail_counters_from_episode_ledger,
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
    MANDATORY_BRANCH_KH02_R2,
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
    evaluate_causal_fill_time_policy_single_trade,
    load_r2_canonical_dataset_readonly,
    probe_r2_c1_write_mounts_strict,
    replay_r2_same_opportunity_paired_counterfactuals,
    reprice_r2_fixed_fills_cost_counterfactual,
)
from services.kh_03_data_governance_service import audit_kh03_collector_and_escrow_readiness
from services.kh_03_fee_integrity_service import build_kh03_six_scenario_fee_ledger_and_reconciliation
from services.kh_03_r2_methodology_service import (
    MIN_EMBARGO_MINUTES_KH03_R2,
    SCHEMA_VERSION_KH03_R2,
    SPEC_VERSION_KH03_R2,
    assert_no_instantaneous_open_to_open_fill,
    build_kh03_r2_ci_red_green_and_boundary_artifacts,
    build_kh03_r2_forward_only_dev_folds,
    build_kh03_r2_h3_advisory_ledger_and_disclosure,
    build_kh03_r2_h3_red_proof_and_policy_choice,
    build_kh03_r2_wf_red_proofs,
    evaluate_kh03_dev_ci_and_advancement,
    parse_kh03_cluster_ci_strict,
    run_kh03_r2_forward_only_walkforward,
    run_kh03_r2_recomputed_science_14_16_23,
    select_past_only_arm_for_fold,
    validate_kh03_r2_fold_causality,
    validate_kh03_r2_h3_ledger_independently,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    verify_fresh_junit_bijection_v17931211,
)
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_02_r2_c3_independent_verifier import _audit_memory_samples_c3
from tools.kh_03_r2_independent_verifier import (
    KH03_R2_SOURCE_FILES_ALLOWLIST,
    PRIOR_KH03_R1_EVIDENCE_SHA,
    PRIOR_KH03_R1_PUB_HEAD_SHA,
    PRIOR_KH03_R1_RUN_ID,
    PRIOR_KH03_R1_TESTED_SHA,
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R2,
    recompute_kh03_r2_28_gates_and_verdicts,
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
from tools.run_kh_03_acceptance import (
    KH03_EXCLUSIVE_SUBPROCESS_TEST_FILES,
    KH03_PRE_PATCH_FEE_RED_PROOF_9200C802,
    _run_kh03_150_red_green_fault_suite,
)
from tools.run_v17_9_3_1_2_2_verification import canonicalize_collect_node_id_v1793122

KH03_R2_ARTIFACTS_BASE_REL = "aurum-desk/docs/artifacts/kh_03_r2_methodology_closure"
PRIOR_KH03_R1_DIR_REL = f"aurum-desk/docs/artifacts/kh_03_research_data/{PRIOR_KH03_R1_RUN_ID}"

KH03_R2_EXCLUSIVE_SUBPROCESS_TEST_FILES: Set[str] = set(KH03_EXCLUSIVE_SUBPROCESS_TEST_FILES) | {
    "test_kh03_r2_verifier_mutations.py",
}

FUNCTION_RESPONSIBILITY_MAP_KH03_R2: List[Dict[str, str]] = [
    {
        "function": "parse_kh03_cluster_ci_strict",
        "module": "services/kh_03_r2_methodology_service.py",
        "inputs": "value (list|tuple|strict dict|None), label",
        "outputs": "{available, lower, upper, status, strictly_positive, schema_valid, diagnostic}",
        "invariants": "Rejects swapped/NaN/Inf/bool/str/arbitrary dict; strictly negative -> CI_VALID_NEGATIVE (never CROSSES_ZERO); exact zero -> CI_TOUCHES_ZERO",
        "tests": "test_kh03_r2_ci_schema_and_gate.py",
        "evidence": "03, 04, 05, 14",
    },
    {
        "function": "evaluate_kh03_dev_ci_and_advancement",
        "module": "services/kh_03_r2_methodology_service.py",
        "inputs": "raw arm_stats, raw CI, holm p, matching, controls, forward-only WF stats, executable_edge_eligible",
        "outputs": "{ci_parsed, subgates, failed_advancement_criteria, passes_preregistered_dev_advancement, arm_dev_verdict}",
        "invariants": "Never trusts self-reported boolean; disqualifies non-executable PATH B H3; requires all independent gates for DEV_CANDIDATE_UNPROVEN_OOS",
        "tests": "test_kh03_r2_ci_schema_and_gate.py, test_kh03_r2_verifier_mutations.py",
        "evidence": "05, 14, 23, 25",
    },
    {
        "function": "build_kh03_r2_h3_advisory_ledger_and_disclosure",
        "module": "services/kh_03_r2_methodology_service.py",
        "inputs": "datasets, mtf_bundle, r2_cands_1x, frozen h3_policy_07, out_h3_ledger_gz_path",
        "outputs": "08_h3_per_episode_policy_qty_time_ledger.jsonl.gz + 09 disclosure JSON",
        "invariants": "PATH B enforces size_applied_to_execution=False, assumed_tradeable_at_ms=None, Decimal fee reconciliation residual <= 0.001, H3 disqualified from executable candidate",
        "tests": "test_kh03_r2_h3_policy_truth.py, test_kh03_r2_verifier_mutations.py",
        "evidence": "06, 07, 08, 09",
    },
    {
        "function": "build_kh03_r2_forward_only_dev_folds + validate_kh03_r2_fold_causality",
        "module": "services/kh_03_r2_methodology_service.py",
        "inputs": "bars_1m, mtf_records_by_idx, embargo_minutes>=240, max_label_horizon_minutes=180",
        "outputs": "expanding past-only folds with train_end < val_start - embargo & causality audit",
        "invariants": "Zero future train bars; gap >= 240m; label exit <= embargo_start; is_fresh_oos=False; returns WALKFORWARD_INSUFFICIENT_HISTORY_OR_POWER when short",
        "tests": "test_kh03_r2_forward_only_temporal_folds.py",
        "evidence": "10, 11, 12",
    },
    {
        "function": "run_kh03_r2_forward_only_walkforward",
        "module": "services/kh_03_r2_methodology_service.py",
        "inputs": "datasets, mtf_bundle, r2_cands_1x, tested_code_sha",
        "outputs": "11, 12, 13 (past-only train purge, frozen fold-local selection SHA, independent fold-local 1-position validation replays)",
        "invariants": "Fresh bounded replay per fold truncated at val_end; future +100R injection invariant; zero duplicate cross-fold episodes",
        "tests": "test_kh03_r2_forward_only_temporal_folds.py, test_kh03_r2_verifier_mutations.py",
        "evidence": "11, 12, 13, 14",
    },
    {
        "function": "verify_kh03_r2_artifacts_independently_from_disk",
        "module": "tools/kh_03_r2_independent_verifier.py",
        "inputs": "run_dir, expected_run_id, expected_tested_sha",
        "outputs": "25_independent_OS_verifier_manifest_sha256.json + exit code 0/1/2",
        "invariants": "Distinct OS PID; recomputes CI, H3 ledger, WF causality, 6-cost fees, JUnit bijection, W1-W4, RAM/disk, G01..G28 directly from raw disk files",
        "tests": "test_kh03_r2_verifier_mutations.py (16 new mutations)",
        "evidence": "22, 24, 25",
    },
]


def _audit_kh03_r2_function_ownership_01(tested_sha: str) -> Dict[str, Any]:
    locked_inv: List[Dict[str, Any]] = []
    for rel in LOCKED_KEEP_AS_IS_FILES_KH02_R2:
        p = REPO_ROOT / rel
        is_py = rel.endswith(".py")
        locked_inv.append(
            {
                "rel_path": rel,
                "exists": p.is_file(),
                "sha256": _sha256_file(p) if p.is_file() else None,
                "ast_function_count": len(_ast_function_inventory(p)) if (is_py and p.is_file()) else None,
            }
        )

    rc_diff, diff_out = _git_cmd(["diff", "--name-only", f"{PRIOR_KH03_R1_PUB_HEAD_SHA}..HEAD"])
    changed_files = [ln.strip() for ln in diff_out.splitlines() if ln.strip()] if rc_diff == 0 else []
    locked_set = set(LOCKED_KEEP_AS_IS_FILES_KH02_R2)
    locked_mod = [f for f in changed_files if f in locked_set]

    forbidden_hits: List[str] = []
    r2_inv: List[Dict[str, Any]] = []
    for rel in KH03_R2_SOURCE_FILES_ALLOWLIST:
        p = REPO_ROOT / rel
        if not p.is_file():
            continue
        if rel.endswith(".py") and ("kh_03_r2" in rel or "kh03_r2" in rel):
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
            r2_inv.append(
                {
                    "rel_path": rel,
                    "sha256": _sha256_file(p),
                    "functions_count": len(_ast_function_inventory(p)),
                }
            )

    c_live = CostAssumptions()
    cost_ok = (
        abs(float(c_live.maker_fee_rate) - 0.0002) <= 1e-12
        and abs(float(c_live.taker_fee_rate) - 0.0004) <= 1e-12
        and abs(float(c_live.slippage_usd) - 0.10) <= 1e-12
    )
    ok = len(locked_mod) == 0 and len(forbidden_hits) == 0 and cost_ok
    return {
        "spec_version": SPEC_VERSION_KH03_R2,
        "schema_version": SCHEMA_VERSION_KH03_R2,
        "tested_executable_sha": tested_sha,
        "prior_r1_pub_head_sha": PRIOR_KH03_R1_PUB_HEAD_SHA,
        "locked_keep_as_is_count": len(locked_inv),
        "locked_keep_as_is_inventory": locked_inv,
        "locked_files_modified": locked_mod,
        "locked_files_modified_count": len(locked_mod),
        "changed_files_vs_r1": changed_files,
        "kh03_r2_module_inventory": r2_inv,
        "function_responsibility_map": FUNCTION_RESPONSIBILITY_MAP_KH03_R2,
        "forbidden_order_import_hits": forbidden_hits,
        "cost_assumptions_defaults_intact": cost_ok,
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        "status": "PASS" if ok else "FAIL",
    }


def _audit_prior_r1_c4_and_dataset_02(sentinel_tmp: Path) -> Dict[str, Any]:
    ds_p = REPO_ROOT / CANONICAL_DATASET_REL_PATH
    ds_sha = _sha256_file(ds_p) if ds_p.is_file() else ""
    ds_ok = ds_sha == CANONICAL_DATASET_SHA256_KH02_R2

    prior_map: Dict[str, Any] = {}
    all_ok = ds_ok
    for rel_p, exp_sha, lbl in PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R2:
        p = REPO_ROOT / rel_p
        act = _sha256_file(p) if p.is_file() else ""
        m = p.is_file() and (act == exp_sha)
        if not m:
            all_ok = False
        prior_map[lbl] = {"rel_path": rel_p, "expected_sha256": exp_sha, "actual_sha256": act, "match": m}

    f08_c4, f09_c4 = audit_kh03_collector_and_escrow_readiness(sentinel_tmp / "c4_prov_check")
    prov_ok = (
        f08_c4.get("status") == "PASS"
        and f09_c4.get("status") == "PASS"
        and f08_c4.get("network_capture_enabled") is False
        and f08_c4.get("live_network_capture_executed") is False
        and int(f09_c4.get("real_oos_escrow_final_record_count", -1)) == 0
    )

    return {
        "spec_version": SPEC_VERSION_KH03_R2,
        "schema_version": SCHEMA_VERSION_KH03_R2,
        "canonical_dataset_rel_path": CANONICAL_DATASET_REL_PATH,
        "canonical_dataset_sha256": ds_sha,
        "canonical_dataset_expected_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
        "canonical_dataset_sha256_match": ds_ok,
        "all_prior_sealed_artifacts_intact": all_ok,
        "prior_sealed_artifacts_immutability": prior_map,
        "c4_provenance_and_oos_boundary": {
            "network_capture_enabled": f08_c4.get("network_capture_enabled"),
            "live_network_capture_executed": f08_c4.get("live_network_capture_executed"),
            "LIVE_NETWORK_VALIDATION": f08_c4.get("LIVE_NETWORK_VALIDATION"),
            "c4_boundary_direct_mint_blocked": f08_c4.get("c4_boundary_direct_mint_blocked"),
            "c4_boundary_evil_subclass_blocked": f08_c4.get("c4_boundary_evil_subclass_blocked"),
            "c4_boundary_monkeypatch_blocked": f08_c4.get("c4_boundary_monkeypatch_blocked"),
            "real_oos_escrow_integrity_valid": f09_c4.get("real_oos_escrow_integrity_valid"),
            "real_oos_escrow_final_record_count": f09_c4.get("real_oos_escrow_final_record_count"),
            "qa_fixture_refused_by_real_oos_escrow": f09_c4.get("qa_fixture_refused_by_real_oos_escrow"),
            "status": "PASS" if prov_ok else "FAIL",
        },
        "r1_reference_baseline_metrics_not_reused_as_pass": {
            "r1_run_id": PRIOR_KH03_R1_RUN_ID,
            "r1_pytest_collected": 5587,
            "r1_faults": 150,
            "r1_verifier_mutations": 14,
            "r1_min_disk_gib": 44.5873,
            "r1_min_ram_mib": 2051.31,
            "re_measured_fresh_in_r2": True,
        },
        "status": "PASS" if (all_ok and prov_ok) else "FAIL",
    }


def _run_kh03_r2_174_red_green_fault_suite(
    *,
    sentinel_tmp: Path,
    datasets: Mapping[str, Any],
    mtf_bundle: Mapping[str, Any],
    ledger_bundle: Mapping[str, Any],
    replay_bundle: Mapping[str, Any],
    live_ff: Mapping[str, Any],
    h3_ledger_gz: Path,
    f09: Mapping[str, Any],
    f11: Mapping[str, Any],
) -> Dict[str, Any]:
    # Inherit ALL 150 R1 + C4 controls first!
    tmp_fee_gz = sentinel_tmp / "tmp_r1_fee_for_faults.jsonl.gz"
    _, tmp_f05 = build_kh03_six_scenario_fee_ledger_and_reconciliation(
        datasets=datasets,
        mtf_bundle=mtf_bundle,
        ledger_bundle=ledger_bundle,
        fixed_fill_doc=live_ff,
        out_episode_ledger_gz_path=tmp_fee_gz,
        prior_c4_red_proof=KH03_PRE_PATCH_FEE_RED_PROOF_9200C802,
    )
    tmp_c4_gz = sentinel_tmp / "tmp_c4_tail_for_faults.jsonl.gz"
    tmp_c4_meta = build_c4_per_episode_risk_ledger(
        datasets=datasets, r2_replays=replay_bundle["r2_replays"], out_jsonl_gz_path=tmp_c4_gz
    )
    tmp_c4_rec = recompute_c4_tail_counters_from_episode_ledger(tmp_c4_gz)
    tmp_c4_f11 = build_c4_realized_tail_evidence_from_r2_replays(
        datasets=datasets, r2_replays=replay_bundle["r2_replays"], ledger_meta=tmp_c4_meta, ledger_recomputed=tmp_c4_rec
    )
    r1_150 = _run_kh03_150_red_green_fault_suite(
        sentinel_tmp / "r1_150_sub", tmp_f05, tmp_fee_gz, tmp_c4_f11, tmp_c4_gz
    )
    controls = list(r1_150["controls"])
    r2_new = 0

    def _rec(fid: str, name: str, fn: str, red_ok: bool, green_ok: bool, detail: str) -> None:
        nonlocal r2_new
        r2_new += 1
        controls.append(
            {
                "fault_id": fid,
                "name": name,
                "anchored_function": fn,
                "red_rejected": bool(red_ok),
                "green_accepted": bool(green_ok),
                "status": "PASS" if (red_ok and green_ok) else "FAIL",
                "diagnostic": detail,
            }
        )

    # KH03R2-F01..F08: P0-SCI-01 Strict CI & DEV gate controls
    g_ci_pos = parse_kh03_cluster_ci_strict([0.10, 0.40])["status"] == "CI_VALID_POSITIVE"
    _rec("KH03R2-F01", "CI_NEGATIVE_NOT_CALLED_CROSSES_ZERO", "parse_kh03_cluster_ci_strict", parse_kh03_cluster_ci_strict([-1.0, -0.8])["status"] == "CI_VALID_NEGATIVE", g_ci_pos, "strictly negative CI classified CI_VALID_NEGATIVE")
    _rec("KH03R2-F02", "CI_STRADDLES_ZERO_DETECTED", "parse_kh03_cluster_ci_strict", parse_kh03_cluster_ci_strict([-0.1, 0.2])["status"] == "CI_STRADDLES_ZERO", g_ci_pos, "straddling CI rejected from positive")
    _rec("KH03R2-F03", "CI_TOUCHES_ZERO_NOT_STRICTLY_POSITIVE", "parse_kh03_cluster_ci_strict", parse_kh03_cluster_ci_strict([0.0, 0.3])["strictly_positive"] is False, g_ci_pos, "exact 0 boundary not strictly positive")
    _rec("KH03R2-F04", "CI_SWAPPED_BOUNDS_INVALID_SCHEMA", "parse_kh03_cluster_ci_strict", parse_kh03_cluster_ci_strict([0.5, 0.1])["status"] == "CI_INVALID_SCHEMA", g_ci_pos, "swapped bounds rejected")
    _rec("KH03R2-F05", "CI_NAN_INF_INVALID_SCHEMA", "parse_kh03_cluster_ci_strict", parse_kh03_cluster_ci_strict([float("nan"), 0.2])["status"] == "CI_INVALID_SCHEMA" and parse_kh03_cluster_ci_strict([0.1, float("inf")])["status"] == "CI_INVALID_SCHEMA", g_ci_pos, "NaN/Inf rejected")
    _rec("KH03R2-F06", "CI_BOOL_STR_EMPTY_DICT_REJECTED", "parse_kh03_cluster_ci_strict", all(parse_kh03_cluster_ci_strict(x)["status"] == "CI_INVALID_SCHEMA" for x in ([True, True], ["0.1", "0.2"], {})), g_ci_pos, "bool/str/empty dict rejected")
    _rec("KH03R2-F07", "CI_NONE_UNAVAILABLE_NOT_PASS", "parse_kh03_cluster_ci_strict", parse_kh03_cluster_ci_strict(None)["status"] == "CI_UNAVAILABLE_INSUFFICIENT_SAMPLE" and not parse_kh03_cluster_ci_strict(None)["strictly_positive"], g_ci_pos, "None CI inconclusive")
    ev_pos_only = evaluate_kh03_dev_ci_and_advancement(
        arm_code="S", closed_trades_1x=10, effective_ny_days_1x=2, expectancy_r_1x=0.3, net_pnl_usd_1x=5.0,
        expectancy_r_fee_only_0p0006=0.2, expectancy_r_2x=-0.1, raw_ny_day_95ci_r=[0.1, 0.5],
        holm_bonferroni_adjusted_pvalue=0.2, matching_quality_status="MATCHING_INADEQUATE",
        matched_mean_delta_r_vs_null=-0.1, expectancy_r_vs_placebo_shuffled=-0.1, expectancy_r_vs_time_offset=-0.1,
        wf_median_fold_expectancy_r=-0.1, wf_positive_fold_ratio=0.0, wf_valid_forward_only_folds_count=3,
    )
    _rec("KH03R2-F08", "POSITIVE_CI_ALONE_CANNOT_PASS_DEV_GATE", "evaluate_kh03_dev_ci_and_advancement", ev_pos_only["passes_preregistered_dev_advancement"] is False, ev_pos_only["subgates"]["ci_strictly_positive_ok"] is True, "positive CI alone blocked by other gates")

    # KH03R2-F09..F16: P0-SCI-02 H3 policy truth & causality controls
    _rec("KH03R2-F09", "H3_OPEN_TO_OPEN_WITHOUT_L1_REJECTED", "assert_no_instantaneous_open_to_open_fill", not assert_no_instantaneous_open_to_open_fill(observed_at_ms=1000, assumed_tradeable_at_ms=1000, observed_l1_bbo_available=False)["executable_fill_verifiable"], assert_no_instantaneous_open_to_open_fill(observed_at_ms=1000, assumed_tradeable_at_ms=1500, observed_l1_bbo_available=True)["executable_fill_verifiable"], "open-to-open without L1 blocked")
    _rec("KH03R2-F10", "H3_SAME_TS_WITH_L1_STILL_REJECTED", "assert_no_instantaneous_open_to_open_fill", not assert_no_instantaneous_open_to_open_fill(observed_at_ms=1000, assumed_tradeable_at_ms=1000, observed_l1_bbo_available=True)["executable_fill_verifiable"], True, "zero-latency same-ts execution rejected")
    fhl_raised = False
    try:
        evaluate_causal_fill_time_policy_single_trade(direction="LONG", decision_entry=4100.0, fill_bar_open=4100.0, selected_sl=4098.0, take_profit=4110.0, decision_qty=0.3, forbidden_future_fields_present=True)
    except ValueError:
        fhl_raised = True
    _rec("KH03R2-F11", "H3_FUTURE_HIGH_LOW_FIELDS_RAISE", "evaluate_causal_fill_time_policy_single_trade", fhl_raised, True, "future bar H/L raises ValueError")
    _rec("KH03R2-F12", "H3_GAP_PAST_SL_REJECTED", "evaluate_causal_fill_time_policy_single_trade", not evaluate_causal_fill_time_policy_single_trade(direction="LONG", decision_entry=4100.0, fill_bar_open=4097.0, selected_sl=4098.0, take_profit=4110.0, decision_qty=0.3)["accepted"], True, "gap past SL rejected")
    _rec("KH03R2-F13", "H3_GAP_PAST_TP_REJECTED", "evaluate_causal_fill_time_policy_single_trade", not evaluate_causal_fill_time_policy_single_trade(direction="SHORT", decision_entry=4100.0, fill_bar_open=4089.0, selected_sl=4102.0, take_profit=4090.0, decision_qty=0.3)["accepted"], True, "gap past TP rejected")
    f09_tamp = copy.deepcopy(dict(f09))
    f09_tamp["legacy_r1_filter_only_compatibility_summary"]["total_fees_usd"] = 0.0
    _rec("KH03R2-F14", "H3_LEDGER_TAMPERED_FEE_SUMMARY_CAUGHT", "validate_kh03_r2_h3_ledger_independently", not validate_kh03_r2_h3_ledger_independently(h3_ledger_gz, f09_tamp)["valid"], validate_kh03_r2_h3_ledger_independently(h3_ledger_gz, f09)["valid"], "tampered H3 fee caught")
    ev_h3_disq = evaluate_kh03_dev_ci_and_advancement(
        arm_code="KH03_H3_FILL_TIME_CAUSAL_RR_GATE", closed_trades_1x=100, effective_ny_days_1x=15,
        expectancy_r_1x=0.5, net_pnl_usd_1x=100.0, expectancy_r_fee_only_0p0006=0.4, expectancy_r_2x=0.3,
        raw_ny_day_95ci_r=[0.2, 0.8], holm_bonferroni_adjusted_pvalue=0.005,
        matching_quality_status="BALANCED_COMMON_SUPPORT", matched_mean_delta_r_vs_null=0.3,
        expectancy_r_vs_placebo_shuffled=0.4, expectancy_r_vs_time_offset=0.4,
        wf_median_fold_expectancy_r=0.3, wf_positive_fold_ratio=1.0, wf_valid_forward_only_folds_count=3,
        executable_edge_eligible=False, disqualification_reason="H3_DISQUALIFIED_PATH_B_FILTER_ONLY_NOT_EXECUTABLE_WITH_M1",
    )
    _rec("KH03R2-F15", "H3_PATH_B_DISQUALIFIED_EVEN_ON_SYNTH_POSITIVE", "evaluate_kh03_dev_ci_and_advancement", ev_h3_disq["passes_preregistered_dev_advancement"] is False and ev_h3_disq["arm_dev_verdict"] == "DISQUALIFIED_FILTER_ONLY_RESEARCH_ADVISORY", True, "PATH B H3 cannot pass executable candidate gate")
    _rec("KH03R2-F16", "H3_LONG_SHORT_RESIZE_TRIGGERED_PROPERLY", "evaluate_causal_fill_time_policy_single_trade", evaluate_causal_fill_time_policy_single_trade(direction="LONG", decision_entry=4125.0, fill_bar_open=4125.5, selected_sl=4122.0, take_profit=4150.0, decision_qty=0.45)["fill_time_action"] == "RESIZED_CAUSALLY_AT_FILL_TIME", evaluate_causal_fill_time_policy_single_trade(direction="SHORT", decision_entry=4125.0, fill_bar_open=4124.5, selected_sl=4128.0, take_profit=4100.0, decision_qty=0.45)["fill_time_action"] == "RESIZED_CAUSALLY_AT_FILL_TIME", "LONG/SHORT fill-time resize repro")

    # KH03R2-F17..F24: P0-SCI-03 True forward-only WF causality controls
    good_folds = list(f11["folds"])
    g_wf = validate_kh03_r2_fold_causality(good_folds)["valid"]
    bad_f_post = copy.deepcopy(good_folds)
    bad_f_post[0]["train_max_known_at_ts_ms"] = bad_f_post[0]["val_min_ts_ms"]
    _rec("KH03R2-F17", "WF_TRAIN_GE_VAL_REJECTED", "validate_kh03_r2_fold_causality", not validate_kh03_r2_fold_causality(bad_f_post)["valid"], g_wf, "train >= val blocked")
    bad_f_emb = copy.deepcopy(good_folds)
    bad_f_emb[0]["train_max_known_at_ts_ms"] = bad_f_emb[0]["val_min_ts_ms"] - (239 * 60_000 + 59_000)
    _rec("KH03R2-F18", "WF_EMBARGO_239M59S_REJECTED", "validate_kh03_r2_fold_causality", not validate_kh03_r2_fold_causality(bad_f_emb)["valid"], g_wf, "239m59s embargo rejected")
    bad_f_fut = copy.deepcopy(good_folds)
    bad_f_fut[0]["future_train_bars_count"] = 1
    _rec("KH03R2-F19", "WF_FUTURE_TRAIN_BAR_REJECTED", "validate_kh03_r2_fold_causality", not validate_kh03_r2_fold_causality(bad_f_fut)["valid"], g_wf, "future train bar rejected")
    bad_f_lbl = copy.deepcopy(good_folds)
    bad_f_lbl[0]["train_last_label_exit_ts_ms"] = bad_f_lbl[0]["embargo_start_ms"] + 60_000
    _rec("KH03R2-F20", "WF_LABEL_EXIT_CROSSING_EMBARGO_REJECTED", "validate_kh03_r2_fold_causality", not validate_kh03_r2_fold_causality(bad_f_lbl)["valid"], g_wf, "train label crossing embargo rejected")
    bad_f_oos = copy.deepcopy(good_folds)
    bad_f_oos[0]["is_fresh_oos"] = True
    _rec("KH03R2-F21", "WF_FOLD_CLAIMED_FRESH_OOS_REJECTED", "validate_kh03_r2_fold_causality", not validate_kh03_r2_fold_causality(bad_f_oos)["valid"], g_wf, "reused DEV fold cannot be fresh OOS")
    bad_f_dup = copy.deepcopy(good_folds)
    bad_f_dup[0]["val_episode_ids"] = ["ep_shared"]
    bad_f_dup[1]["val_episode_ids"] = ["ep_shared"]
    _rec("KH03R2-F22", "WF_DUPLICATE_EPISODE_ACROSS_FOLDS_REJECTED", "validate_kh03_r2_fold_causality", not validate_kh03_r2_fold_causality(bad_f_dup)["valid"], g_wf, "duplicate val episode rejected")
    short_wf = build_kh03_r2_forward_only_dev_folds(bars_1m=list(datasets["1m"])[:100])
    _rec("KH03R2-F23", "WF_SHORT_HISTORY_TRUTHFUL_INSUFFICIENT", "build_kh03_r2_forward_only_dev_folds", short_wf["status"] == "WALKFORWARD_INSUFFICIENT_HISTORY_OR_POWER", g_wf, "short history never fabricates folds")
    env_w = os.environ.copy()
    env_w["KH02_OWNER_WAIVE_DISK_PREFLIGHT"] = "1"
    p_w = subprocess.run([sys.executable, str(APP_ROOT / "tools" / "run_kh_03_r2_acceptance.py"), "--help"], cwd=str(APP_ROOT), env=env_w, capture_output=True, text=True, timeout=20)
    _rec("KH03R2-F24", "KH03_R2_DISK_WAIVER_ENV_REFUSED", "run_kh_03_r2_acceptance.main", p_w.returncode == 2, True, "R2 runner refuses disk waiver env with exit 2")

    passed_n = sum(1 for c in controls if c["status"] == "PASS")
    fids = [c["fault_id"] for c in controls]
    return {
        "spec_version": SPEC_VERSION_KH03_R2,
        "schema_version": SCHEMA_VERSION_KH03_R2,
        "inherited_r1_controls_count": len(r1_150["controls"]),
        "new_kh03_r2_controls_count": r2_new,
        "total_controls": len(controls),
        "unique_fault_ids": len(set(fids)),
        "passed_controls": passed_n,
        "failed_controls": len(controls) - passed_n,
        "controls": controls,
        "status": "PASS" if (passed_n == len(controls) and len(set(fids)) == len(controls) and len(controls) >= 174) else "FAIL",
    }


def _run_kh03_r2_verifier_mutation_suite_22(sentinel_tmp: Path) -> Dict[str, Any]:
    from tests.test_kh03_r2_verifier_mutations import (
        KH03_R2_VERIFIER_MUTATIONS_SPECS,
        build_synthetic_valid_kh03_r2_run_dir,
        run_kh03_r2_verifier_subprocess,
    )

    base_clone = sentinel_tmp / "kh03_r2_verif_mut_base"
    rid, tsha = build_synthetic_valid_kh03_r2_run_dir(base_clone)
    rc_clean, _clean_out = run_kh03_r2_verifier_subprocess(base_clone, rid, tsha)
    clean_ok = (rc_clean == 0)

    results: List[Dict[str, Any]] = []
    for mid, mut_fn, expected_tok in KH03_R2_VERIFIER_MUTATIONS_SPECS:
        cdir = sentinel_tmp / f"kh03_r2_mut_{mid}"
        shutil.copytree(base_clone, cdir)
        mut_fn(cdir)
        rc, out_txt = run_kh03_r2_verifier_subprocess(cdir, rid, tsha)
        detected = (rc != 0) and (expected_tok in out_txt)
        results.append(
            {
                "mutation_id": mid,
                "expected_violation_token": expected_tok,
                "verifier_exit_code": rc,
                "detected_fail_closed": detected,
            }
        )

    det_n = sum(1 for r in results if r["detected_fail_closed"])
    return {
        "spec_version": SPEC_VERSION_KH03_R2,
        "schema_version": SCHEMA_VERSION_KH03_R2,
        "isolated_temp_clones_only": True,
        "sealed_run_dir_mutated": False,
        "clean_clone_verifier_green": clean_ok,
        "mutations_tested_count": len(results),
        "mutations_detected_count": det_n,
        "mutations_failed_detection_count": len(results) - det_n,
        "results": results,
        "status": "PASS" if (clean_ok and det_n == len(results) and len(results) >= 16) else "FAIL",
    }


def _collect_and_build_kh03_r2_lpt_shards(owned_temp_root: Path) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]]]:
    all_test_files = sorted(
        str(p.relative_to(APP_ROOT)).replace("\\", "/")
        for p in (BACKEND_ROOT / "tests").glob("test_*.py")
        if p.is_file()
    )
    col_out = owned_temp_root / "collect_kh03_r2_stdout.log"
    col_err = owned_temp_root / "collect_kh03_r2_stderr.log"
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
        raise RuntimeError(f"KH03_R2_PYTEST_COLLECTION_FAILED:rc={proc.returncode}:{err_txt}")

    collected_by_file: Dict[str, List[str]] = {}
    for ln in col_out.read_text("utf-8", errors="replace").splitlines():
        cid = canonicalize_collect_node_id_v1793122(ln)
        if cid is not None:
            collected_by_file.setdefault(cid.split("::", 1)[0], []).append(cid)
    for k in collected_by_file:
        collected_by_file[k].sort()

    empirical_ms = _load_empirical_file_durations_ms_from_kh01b()
    non_sub = [f for f in all_test_files if Path(f).name not in KH03_R2_EXCLUSIVE_SUBPROCESS_TEST_FILES]
    sub_files = [f for f in all_test_files if Path(f).name in KH03_R2_EXCLUSIVE_SUBPROCESS_TEST_FILES]
    shards = _build_lpt_shards_kh01b_r1(non_sub, collected_by_file, empirical_ms)
    for sh in shards:
        if sh.get("shard_name") == "SHARD_SINGLE_CHILD_02_BUDGET2":
            sh["exclusive"] = True
            sh["slot_cost"] = 4
    for idx_s, sf in enumerate(sorted(sub_files), 1):
        shards.append(
            {
                "shard_name": f"SHARD_SINGLE_CHILD_KH03_R2_SUBPROC_{idx_s:02d}",
                "files": [sf],
                "exclusive": True,
                "slot_cost": 4,
                "priority_weight": 260_000,
                "needs_venv": False,
            }
        )
    return collected_by_file, shards


def _run_kh03_r2_full_repo_pytest_sharded(
    run_dir: Path,
    sentinel_tmp: Path,
    *,
    run_id: str,
    tested_sha: str,
    effective_disk_reserve_bytes: int,
    max_workers: int,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    empirical_r2_rss = load_empirical_r2_shard_rss_bytes(REPO_ROOT)
    collected_by_file, r2_shards = _collect_and_build_kh03_r2_lpt_shards(sentinel_tmp)
    active_shards_tracker: Dict[str, Any] = {}
    junit_res = asyncio.run(
        run_async_lpt_pytest_shards_kh01b_r1(
            run_dir,
            sentinel_tmp,
            run_id=run_id,
            tested_executable_sha=tested_sha,
            effective_disk_reserve_bytes=effective_disk_reserve_bytes,
            max_parallel_workers=max_workers,
            ram_policy_mode="R3_STRICT_512MIB",
            empirical_rss_by_shard=empirical_r2_rss,
            active_shards_tracker=active_shards_tracker,
            shard_specs_override=r2_shards,
            collected_by_file_override=collected_by_file,
        )
    )
    elapsed = round(time.perf_counter() - t0, 3)
    cm = junit_res["collection_manifest"]
    sm = junit_res["shard_manifest"]
    bj = verify_fresh_junit_bijection_v17931211(
        run_dir, cm, sm,
        expected_run_id=run_id,
        expected_tested_sha=tested_sha,
        mandatory_node_prefixes=("backend/tests/test_kh_02_r2", "backend/tests/test_kh_03", "backend/tests/test_kh03_r2"),
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
    ok = bj.get("status") == "PASS" and nonzero_shards == 0 and collected_n >= 5587 and collected_n == executed_n and failed_n == 0 and error_n == 0 and mand_skip_n == 0
    return {
        "spec_version": SPEC_VERSION_KH03_R2,
        "schema_version": SCHEMA_VERSION_KH03_R2,
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
        "collection_manifest": cm,
        "shard_manifest": sm,
        "bijection_audit": bj,
    }


def _render_kh03_r2_readme_and_handover(
    *,
    run_id: str,
    f00: Mapping[str, Any],
    f03: Mapping[str, Any],
    f04: Mapping[str, Any],
    f06: Mapping[str, Any],
    f07: Mapping[str, Any],
    f09: Mapping[str, Any],
    f11: Mapping[str, Any],
    f13: Mapping[str, Any],
    f17: Mapping[str, Any],
    f18: Mapping[str, Any],
    f19: Mapping[str, Any],
    f20: Mapping[str, Any],
    f21: Mapping[str, Any],
    f22: Mapping[str, Any],
    f23: Mapping[str, Any],
    f24: Mapping[str, Any],
) -> Tuple[str, str]:
    v = f24["verdicts"]
    g = f24["gates_28"]
    mp = f00["strict_multi_mount_probe"]

    md_lines = [
        "# AURUM DESK — KH-03.R2 | RESEARCH METHODOLOGY CLOSURE",
        "",
        f"- **Phân loại kỹ thuật**: **`{f24['overall_classification']}`**",
        f"- **Spec Version**: `{SPEC_VERSION_KH03_R2}` | **Run ID**: `{run_id}`",
        f"- **Nhánh bắt buộc**: `{f00['branch']}`",
        f"- **Tested Executable SHA**: `{f00['tested_executable_sha']}` (`git_clean_at_start={f00['git_clean_at_start']}`)",
        "- **Khóa an toàn**: `PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO | OOS_EVALUATIONS=0`",
        "- **Chế độ H3**: `PATH_B_FILTER_ONLY_RESEARCH_ADVISORY` (`H3_RESIZE_EXECUTION_STATUS=NOT_VERIFIABLE_WITH_M1`, `H3_FILTER_ONLY_RESEARCH=PASS`, `size_applied_to_execution=False`)",
        "",
        "## 1. Kết luận trực giao",
        "",
        "| Chiều kiểm định | Kết luận |",
        "|---|---|",
        *[f"| `{k}` | **{val}** |" for k, val in v.items()],
        "",
        "## 2. Ma trận 28 cổng (`G01`..`G28`)",
        "",
        "| Cổng | Trạng thái |",
        "|---|---|",
        *[f"| `{k}` | **{val}** |" for k, val in g.items()],
        "",
        "## 3. Tóm tắt 3 đóng lỗi phương pháp & kiểm định thực tế",
        f"- **P0-SCI-01 (CI Gate)**: R1 bug tái lập 3/3 arms (`{f03['status']}`), strict boundary matrix `{f04['passed_boundary_cases']}/{f04['total_boundary_cases']}`, strictly negative CI phân loại `CI_VALID_NEGATIVE` (không còn `CROSSES_ZERO:ci_low=None`).",
        f"- **P0-SCI-02 (H3 Policy Truth)**: R1 bỏ `final_qty` trên `{f06['real_dev_replayed_episodes_where_r1_discarded_final_qty_count']}` lệnh replayed (`{f06['real_dev_resized_opportunities_count']}` đề xuất resize / 1,323 cơ hội). Chọn trung thực **`{f07['chosen_path']}`**: `H3_RESIZE_EXECUTION_STATUS={f07['H3_RESIZE_EXECUTION_STATUS']}`, `size_applied_to_execution_any_row={f09['size_applied_to_execution_any_row']}`, H3 bị loại khỏi `EXECUTABLE_EDGE_CANDIDATE`.",
        f"- **P0-SCI-03 (True Forward-Only DEV WF)**: `{f11['valid_folds_count']}` expanding folds (`train_end < val_start`, embargo `>= {f11['embargo_minutes']} min`, `future_train_bars = 0`, `is_fresh_oos = False`), future +100R injection invariance = `{f13['future_win_injection_invariance_passed']}`.",
        f"- **Baseline Parity (`17`)**: `{f17.get('BASELINE_PARITY_VERDICT')}` (`A1={f17.get('a1_population_count')}/0`, `SI8={f17.get('si8_population_count')}/0`, `3-adapter={f17.get('three_adapter_population_count')}/0`, `D4={f17.get('d4_historical_closed_trades')}`).",
        f"- **Full Pytest (`18`)**: `{f18['status']}` — collected=`{f18.get('collected_count')}`, executed=`{f18.get('executed_count')}`, passed=`{f18.get('passed_count')}`, skipped=`{f18.get('skipped_count')}`, mandatory_skipped=`{f18.get('mandatory_skipped_count')}`, failed=`{f18.get('failed_count')}`.",
        f"- **Faults (`21`)**: `{f21['passed_controls']}/{f21['total_controls']}` (`{f21['inherited_r1_controls_count']}` inherited + `{f21['new_kh03_r2_controls_count']}` new R2). **Mutations (`22`)**: `{f22['mutations_detected_count']}/{f22['mutations_tested_count']}`.",
        f"- **Tài nguyên (`00`, `20`)**: Preflight `{mp['min_free_gib']} GiB`, runtime min `{f20['min_disk_free_gib']} GiB`, RAM min `{f20['min_available_ram_mib']} MiB`, max children `{f20['max_descendant_children']}`, orphans `{f20['orphan_children_after_cleanup']}`.",
        f"- **Kinh tế & KH-04 (`23`)**: `KH03_R2_DEV_CANDIDATE_VERDICT={f23['KH03_R2_DEV_CANDIDATE_VERDICT']}`, `RESEARCH_ECONOMIC_VERDICT={f23['RESEARCH_ECONOMIC_VERDICT']}`, `KH04_ENTRY={f23['KH04_ENTRY']}`, `ACTIVATION_READY=NO`.",
        "",
        "## 4. Bản đồ trách nhiệm hàm (Function Responsibility Map)",
        "",
        "| Hàm | Module | Bất biến cốt lõi | Tests | Bằng chứng |",
        "|---|---|---|---|---|",
        *[
            f"| `{r['function']}` | `{r['module']}` | {r['invariants']} | `{r['tests']}` | `{r['evidence']}` |"
            for r in FUNCTION_RESPONSIBILITY_MAP_KH03_R2
        ],
        "",
        "## 5. Lệnh tái chạy kiểm chứng độc lập",
        "```bash",
        "python aurum-desk/tools/run_kh_03_r2_acceptance.py",
        f"python aurum-desk/tools/kh_03_r2_independent_verifier.py --run-dir aurum-desk/docs/artifacts/kh_03_r2_methodology_closure/{run_id} --expected-run-id {run_id} --expected-tested-sha {f00['tested_executable_sha']} --no-write-manifest",
        "```",
    ]
    md = "\n".join(md_lines) + "\n"

    vi_lines = [
        f"AURUM DESK — KH-03.R2 RESEARCH METHODOLOGY CLOSURE | RUN_ID={run_id}",
        f"PHAN LOAI: {f24['overall_classification']}",
        f"BRANCH={f00['branch']} | TESTED_SHA={f00['tested_executable_sha']}",
        f"ENGINEERING={v['ENGINEERING']} | RESEARCH_METHOD_VALIDITY={v['RESEARCH_METHOD_VALIDITY']} | CI_GATE={v['CI_GATE_CORRECTNESS']}",
        f"H3_POLICY_TRUTH={v['H3_POLICY_TRUTH']} | H3_RESIZE_EXECUTION_STATUS=NOT_VERIFIABLE_WITH_M1 | WALKFORWARD={v['WALKFORWARD_VALIDITY']}",
        f"ECONOMIC_DEV={v['ECONOMIC_DEV']} | RESEARCH_ECONOMIC_VERDICT=NO_PROVEN_EDGE | FRESH_OOS=BLOCKED_NO_UNTOUCHED_DATA",
        f"KH04_ENTRY={v['KH04_ENTRY']} | OOS_EVALUATIONS=0 | ACTIVATION_READY=NO | LIVE_NETWORK_VALIDATION=NOT_RUN_BY_DESIGN",
        f"PYTEST={f18['status']} ({f18.get('passed_count')}/{f18.get('collected_count')}, failed={f18.get('failed_count')}) | FAULTS={f21['passed_controls']}/{f21['total_controls']} | MUTATIONS={f22['mutations_detected_count']}/{f22['mutations_tested_count']}",
        f"DISK_MIN={f20['min_disk_free_gib']}GiB | RAM_MIN={f20['min_available_ram_mib']}MiB | MAX_CHILDREN={f20['max_descendant_children']} | ORPHANS={f20['orphan_children_after_cleanup']}",
    ]
    return md, "\n".join(vi_lines) + "\n"


def run_kh03_r2_acceptance(
    *,
    allow_local_dirty: bool = False,
    owner_waive_disk_preflight: bool = False,
    max_workers: int = GLOBAL_MAX_DESCENDANTS_KH02_R2,
) -> Tuple[int, Path, Dict[str, Any]]:
    if owner_waive_disk_preflight:
        raise SystemExit(2)

    t_start = time.perf_counter()
    orch_pid = os.getpid()
    utc_now = datetime.now(timezone.utc)

    _, branch = _git_cmd(["branch", "--show-current"])
    if branch != MANDATORY_BRANCH_KH02_R2:
        raise RuntimeError(f"WRONG_BRANCH:{branch}!=expected:{MANDATORY_BRANCH_KH02_R2}")
    _, start_head = _git_cmd(["rev-parse", "HEAD"])
    _, porcelain = _git_cmd(["status", "--porcelain=v1"])
    git_clean = (len(porcelain.strip()) == 0)
    if (not git_clean) and (not allow_local_dirty):
        raise RuntimeError("DIRTY_WORKING_TREE_REFUSED_FOR_SEALED_RUN")
    _, remote_out = _git_cmd(["ls-remote", "origin", f"refs/heads/{MANDATORY_BRANCH_KH02_R2}"])
    remote_sha = remote_out.split()[0] if remote_out.strip() else ""

    run_id = f"{utc_now.strftime('%Y%m%dT%H%M%SZ')}_{start_head[:12]}"
    run_dir = REPO_ROOT / KH03_R2_ARTIFACTS_BASE_REL / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "junit").mkdir(exist_ok=True)
    (run_dir / "checksums").mkdir(exist_ok=True)

    sentinel_tmp = create_sentinel_owned_temp_dir_r2_c1(run_id)
    write_mount_paths = {
        "repo_root": REPO_ROOT,
        "artifacts": REPO_ROOT / KH03_R2_ARTIFACTS_BASE_REL,
        "temp_volume": sentinel_tmp,
        "python_venv": Path(sys.executable).resolve().parent,
        "caches": Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")),
        "logs": run_dir / "logs",
    }
    strict_probe = probe_r2_c1_write_mounts_strict(
        write_mount_paths, atomic_probe_dirs=[(sentinel_tmp, True, run_id)]
    )
    avail_ram_mib = round(psutil.virtual_memory().available / (1024.0 * 1024.0), 2)
    strict_disk_ok = bool(strict_probe["can_run_heavy_stages"])
    ram_ok = avail_ram_mib >= PREFLIGHT_RAM_MIN_MIB_KH02_R2
    can_run_heavy = strict_disk_ok and ram_ok
    eff_workers = max(1, min(int(max_workers), GLOBAL_MAX_DESCENDANTS_KH02_R2))

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
        print(f"[KH-03.R2] Start run_id={run_id} tested_sha={start_head[:12]} disk_min={strict_probe['min_free_gib']}GiB ram={avail_ram_mib}MiB", flush=True)

        # Stage 0: Ownership (01) & Prior R1/C4/Dataset immutability + C4 provenance (02)
        f01 = _audit_kh03_r2_function_ownership_01(start_head)
        write_json_durable_atomic(run_dir / "01_function_ast_ownership_and_immutable_diff.json", f01)
        f02 = _audit_prior_r1_c4_and_dataset_02(sentinel_tmp)
        write_json_durable_atomic(run_dir / "02_prior_R1_C4_and_canonical_dataset_immutability.json", f02)

        # Stage 1: P0-SCI-01 CI Red Proof (03), Strict Boundary Matrix (04), Independent Verdicts (05)
        print("[KH-03.R2] Stage 1: P0-SCI-01 CI RED proof, 14-case strict boundary matrix & independent verdicts (03..05)...", flush=True)
        r1_dir = REPO_ROOT / PRIOR_KH03_R1_DIR_REL
        r1_07_path = r1_dir / "07_partition_freeze_pre_registration_and_noncontamination.json"
        r1_15_path = r1_dir / "15_purged_walkforward_bootstrap_multiplicity_power.json"
        r1_15_doc = json.loads(r1_15_path.read_text("utf-8"))

        f03, f04, f05 = build_kh03_r2_ci_red_green_and_boundary_artifacts(prior_r1_15_path=r1_15_path)
        write_json_durable_atomic(run_dir / "03_ci_old_bug_red_proof.json", f03)
        write_json_durable_atomic(run_dir / "04_ci_strict_adapter_green_boundary_matrix.json", f04)
        write_json_durable_atomic(run_dir / "05_ci_independent_positive_negative_verdicts.json", f05)

        # Load canonical DEV dataset strictly read-only
        ds_path = REPO_ROOT / CANONICAL_DATASET_REL_PATH
        datasets, _ = load_r2_canonical_dataset_readonly(ds_path)
        mtf_bundle = build_kh02_asof_mtf_features(datasets)
        r1_frozen_plan, _ = _r2_frozen_plan_and_arms(code_sha=start_head)
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

        # Stage 2: P0-SCI-02 H3 RED proof (06), Frozen PATH B Choice (07), Streaming Advisory Ledger (08) & Disclosure (09)
        print("[KH-03.R2] Stage 2: P0-SCI-02 H3 RED proof, frozen PATH B policy choice & advisory ledger (06..09)...", flush=True)
        f06, f07 = build_kh03_r2_h3_red_proof_and_policy_choice(
            datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_1x=r2_cands_1x,
            tested_code_sha=start_head, observed_l1_bbo_available=False,
        )
        write_json_durable_atomic(run_dir / "06_h3_old_filter_only_vs_resize_red_proof.json", f06)
        write_json_durable_atomic(run_dir / "07_h3_policy_choice_and_execution_feasibility.json", f07)

        h3_ledger_gz = run_dir / "08_h3_per_episode_policy_qty_time_ledger.jsonl.gz"
        _f08_meta, f09 = build_kh03_r2_h3_advisory_ledger_and_disclosure(
            datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_1x=r2_cands_1x,
            h3_policy_choice_07=f07, out_h3_ledger_gz_path=h3_ledger_gz,
        )
        write_json_durable_atomic(run_dir / "09_h3_reconciliation_research_only_or_filter_only_disclosure.json", f09)

        # Stage 3: P0-SCI-03 WF RED proofs (10), Expanding Forward-Only Folds (11), Purge Audit (12), Selection Freeze & Replay (13)
        print("[KH-03.R2] Stage 3: P0-SCI-03 True expanding forward-only DEV walk-forward & fold-local replays (10..13)...", flush=True)
        f10 = build_kh03_r2_wf_red_proofs(prior_r1_07_path=r1_07_path, prior_r1_15_path=r1_15_path, bars_1m=datasets["1m"])
        write_json_durable_atomic(run_dir / "10_wf_old_posthoc_and_future_train_red_proofs.json", f10)

        f11, f12, f13 = run_kh03_r2_forward_only_walkforward(
            datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_1x=r2_cands_1x,
            tested_code_sha=start_head, prior_r1_15_doc=r1_15_doc, seed=DEFAULT_RANDOM_SEED_KH02_R2,
        )
        write_json_durable_atomic(run_dir / "11_wf_forward_only_folds_train_val_timestamps.json", f11)
        write_json_durable_atomic(run_dir / "12_wf_purged_labels_embargo_episode_nonoverlap.json", f12)
        write_json_durable_atomic(run_dir / "13_wf_fold_local_selection_freeze_and_replay.json", f13)

        # Stage 4: Recomputed Statistics (14), 6-Cost Full Re-Risk & Fees (15), Risk Tail (16), Honest Economic & KH-04 Gate (23)
        print("[KH-03.R2] Stage 4: Recomputing CI/Holm/Matching (14), 6-scenario costs (15), risk tail (16) & economic gate (23)...", flush=True)
        f14, f15, f16, f23 = run_kh03_r2_recomputed_science_14_16_23(
            datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle, r2_cands_1x=r2_cands_1x,
            h3_policy_07=f07, h3_disclosure_09=f09, wf_11=f11, wf_13=f13,
            tested_code_sha=start_head, seed=DEFAULT_RANDOM_SEED_KH02_R2,
        )
        write_json_durable_atomic(run_dir / "14_statistical_ci_holm_matching_controls_recomputed.json", f14)
        write_json_durable_atomic(run_dir / "15_six_cost_full_rerisk_and_fee_integrity.json", f15)
        write_json_durable_atomic(run_dir / "16_realized_tail_loss_and_1position_portfolio.json", f16)
        write_json_durable_atomic(run_dir / "23_economic_win_loss_dev_eligibility_and_KH04_gate.json", f23)

        # Stage 5: 174 Unique RED/GREEN Fault Suite (150 inherited R1+C4 + 24 new KH-03.R2) (21)
        print("[KH-03.R2] Stage 5: Running 174 unique RED/GREEN fault controls (150 inherited + 24 new R2) (21)...", flush=True)
        f21 = _run_kh03_r2_174_red_green_fault_suite(
            sentinel_tmp=sentinel_tmp, datasets=datasets, mtf_bundle=mtf_bundle,
            ledger_bundle=ledger_bundle, replay_bundle=replay_bundle, live_ff=live_ff,
            h3_ledger_gz=h3_ledger_gz, f09=f09, f11=f11,
        )
        write_json_durable_atomic(run_dir / "21_inherited_plus_R2_new_unique_red_green_faults.json", f21)

        del datasets, mtf_bundle, ledger_bundle, r1_candidates, r2_cands_1x, replay_bundle, live_ff
        gc.collect()

        # Stage 6: 16 NEW Separate-OS R2 Verifier Mutation Resistance Attacks in Temp Clones (22)
        print("[KH-03.R2] Stage 6: Running 16 NEW separate-OS R2 verifier mutation-resistance controls (22)...", flush=True)
        f22 = _run_kh03_r2_verifier_mutation_suite_22(sentinel_tmp)
        write_json_durable_atomic(run_dir / "22_independent_verifier_mutation_tests.json", f22)

        # Stage 7: Heavy Stages (18 full pytest, 19 W1/W2/W4/W1_repeat, 17 full 56,710 baseline parity)
        if can_run_heavy:
            eff_res_b = int(STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2 * (1024 ** 3))
            pytest_workers = min(3, eff_workers)
            print(f"[KH-03.R2] Stage 7A: Running FULL repo LPT-sharded pytest regression (max_workers={pytest_workers}) (18)...", flush=True)
            f18 = _run_kh03_r2_full_repo_pytest_sharded(
                run_dir, sentinel_tmp, run_id=run_id, tested_sha=start_head,
                effective_disk_reserve_bytes=eff_res_b, max_workers=pytest_workers,
            )
            write_json_durable_atomic(run_dir / "18_full_pytest_collected_executed_junit_bijection.json", f18)
            if git_clean:
                _git_cmd(["checkout", "--", "aurum-desk/backtest/trade_decision_lineage_reconciliation.json"])
            gc.collect()

            print("[KH-03.R2] Stage 7B: Running W1/W2/W4/W1_repeat pytest + R2 science slice determinism (19)...", flush=True)
            pb = asyncio.run(_run_controlled_pytest_w1_w2_w4_benchmark_r1(sentinel_tmp, enforce_r3_ram_guard=True))
            sb = asyncio.run(_run_r2_science_slice_w1_w2_w4_determinism(sentinel_tmp))
            gc.collect()

            print("[KH-03.R2] Stage 7C: Running FULL 56,710 A1 + 6,375 SI8 + 242/166 SI0 + 246 adapter + D4 parity (17)...", flush=True)
            ds_p, _ = load_r2_canonical_dataset_readonly(REPO_ROOT / CANONICAL_DATASET_REL_PATH)
            f02_r1 = audit_r2_reference_inputs_and_invariants(REPO_ROOT)
            f17 = _run_full_production_baseline_parity_20(
                datasets=ds_p, sentinel_tmp=sentinel_tmp, run_id=run_id, tested_sha=start_head,
                branch=branch, f01=f01, f02=f02_r1, max_workers=eff_workers,
            )
            f17["spec_version"] = SPEC_VERSION_KH03_R2
            write_json_durable_atomic(run_dir / "17_historical_baseline_parity_and_prior_hashes.json", f17)
            del ds_p
            gc.collect()
        else:
            f18 = {"spec_version": SPEC_VERSION_KH03_R2, "status": "NOT_RUN_BLOCKED_LOW_DISK", "collected_count": 0, "executed_count": 0, "passed_count": 0, "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0, "junit_bijection_exact": False}
            pb = {"all_4_runs_identical": False, "runs": []}
            sb = {"all_4_runs_identical": False, "runs": []}
            f17 = {"spec_version": SPEC_VERSION_KH03_R2, "BASELINE_PARITY_VERDICT": "NOT_RUN_BLOCKED_LOW_DISK", "a1_population_count": 0, "a1_mismatches_count": 0, "si8_population_count": 0, "si8_mismatches_count": 0, "three_adapter_population_count": 0, "three_adapter_mismatches_count": 0, "d4_historical_closed_trades": 0, "per_decision_and_episode_fingerprints": {"all_sealed_fingerprints_exact_match": False}}
            write_json_durable_atomic(run_dir / "18_full_pytest_collected_executed_junit_bijection.json", f18)
            write_json_durable_atomic(run_dir / "17_historical_baseline_parity_and_prior_hashes.json", f17)

        # Cleanup sentinel temp (Windows long-path safe!) BEFORE telemetry flush
        cl_info = _cleanup_sentinel_temp_c4_win_safe(sentinel_tmp, expected_run_id=run_id)
        sentinel_cleaned = bool(cl_info["cleaned"])
        telem = sampler.stop_and_flush()
        sampler_stopped = True

        f19 = {
            "spec_version": SPEC_VERSION_KH03_R2,
            "schema_version": SCHEMA_VERSION_KH03_R2,
            "pytest_controlled_subset_w1_w2_w4_repeat": pb,
            "r2_science_slice_w1_w2_w4_repeat": sb,
            "measured_pytest_speedup_w2_vs_w1": pb.get("measured_speedup_w2_vs_w1"),
            "measured_pytest_speedup_w4_vs_w1": pb.get("measured_speedup_w4_vs_w1"),
            "measured_science_speedup_w2_vs_w1": sb.get("measured_speedup_w2_vs_w1"),
            "measured_science_speedup_w4_vs_w1": sb.get("measured_speedup_w4_vs_w1"),
            "all_determinism_identical": bool(pb.get("all_4_runs_identical") and sb.get("all_4_runs_identical")),
            "status": (
                "NOT_RUN_BLOCKED_LOW_DISK"
                if not can_run_heavy
                else ("PASS" if (pb.get("all_4_runs_identical") and sb.get("all_4_runs_identical")) else "FAIL")
            ),
        }
        write_json_durable_atomic(run_dir / "19_w1_w2_w4_w1repeat_timing_hashes.json", f19)

        f20 = {
            "spec_version": SPEC_VERSION_KH03_R2,
            "schema_version": SCHEMA_VERSION_KH03_R2,
            "orchestrator_pid": orch_pid,
            "sampling_interval_sec": 0.5,
            "memory_samples_log_rel": "logs/runtime_memory_samples_0p5s.jsonl.gz",
            **telem,
            "strict_preflight_disk_min_gib": STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
            "strict_runtime_disk_min_gib": STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2,
            "advisory_disk_gib": ADVISORY_DISK_GIB_KH02_R2,
            "owner_disk_preflight_waiver": False,
            "sentinel_temp_cleaned_up": sentinel_cleaned,
            "total_wall_clock_sec": round(time.perf_counter() - t_start, 3),
        }
        write_json_durable_atomic(run_dir / "20_disk_ram_pid_temp_telemetry.json", f20)

        _, end_head = _git_cmd(["rev-parse", "HEAD"])
        f00 = {
            "spec_version": SPEC_VERSION_KH03_R2,
            "schema_version": SCHEMA_VERSION_KH03_R2,
            "run_id": run_id,
            "started_utc": utc_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "branch": branch,
            "start_head_sha": start_head,
            "end_head_sha": end_head,
            "tested_executable_sha": start_head,
            "remote_head_sha": remote_sha,
            "prior_kh03_r1_pub_head_sha": PRIOR_KH03_R1_PUB_HEAD_SHA,
            "prior_kh03_r1_tested_sha": PRIOR_KH03_R1_TESTED_SHA,
            "prior_kh03_r1_evidence_sha": PRIOR_KH03_R1_EVIDENCE_SHA,
            "prior_kh03_r1_run_id": PRIOR_KH03_R1_RUN_ID,
            "git_clean_at_start": git_clean,
            "local_uncommitted_changes": not git_clean,
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
        write_json_durable_atomic(run_dir / "00_branch_head_preflight_resource_and_sha.json", f00)

        pub_st = "PRE_PUSH_PENDING" if (strict_disk_ok and git_clean) else "NOT_PUBLISHED_BLOCKED_LOW_DISK"
        f26 = {
            "spec_version": SPEC_VERSION_KH03_R2,
            "schema_version": SCHEMA_VERSION_KH03_R2,
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
            "publication_status": pub_st,
        }
        write_json_durable_atomic(run_dir / "26_phase1_code_commit_push_receipt.json", f26)

        docs_all = {
            "00": f00, "01": f01, "02": f02, "03": f03, "04": f04, "05": f05,
            "06": f06, "07": f07, "09": f09, "10": f10, "11": f11, "12": f12,
            "13": f13, "14": f14, "15": f15, "16": f16, "17": f17, "18": f18,
            "19": f19, "20": f20, "21": f21, "22": f22, "23": f23, "26": f26,
        }
        mem_disk_aud = _audit_memory_samples_c3(run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz")
        h3_disk_aud = validate_kh03_r2_h3_ledger_independently(h3_ledger_gz, f09)
        recomp24 = recompute_kh03_r2_28_gates_and_verdicts(
            docs=docs_all,
            mem_audit=mem_disk_aud,
            h3_ledger_audit=h3_disk_aud,
            junit_bijection_ok=(f18.get("status") == "PASS" and bool(f18.get("junit_bijection_exact", False))),
            for_final_verifier_manifest=False,
        )
        f24 = {
            "spec_version": SPEC_VERSION_KH03_R2,
            "schema_version": SCHEMA_VERSION_KH03_R2,
            "run_id": run_id,
            "tested_executable_sha": start_head,
            "OOS_EVALUATIONS": 0,
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
            "overall_classification": recomp24["overall_classification"],
            "gates_28": recomp24["gates_28"],
            "verdicts": recomp24["verdicts"],
        }
        f24["artifact_canonical_sha256"] = canonical_hash(f24)
        write_json_durable_atomic(run_dir / "24_prepublication_28_gate_matrix.json", f24)

        readme_md, handover_vi = _render_kh03_r2_readme_and_handover(
            run_id=run_id, f00=f00, f03=f03, f04=f04, f06=f06, f07=f07, f09=f09,
            f11=f11, f13=f13, f17=f17, f18=f18, f19=f19, f20=f20, f21=f21,
            f22=f22, f23=f23, f24=f24,
        )
        (run_dir / "README_KH03_R2.md").write_text(readme_md, encoding="utf-8")
        (run_dir / "HANDOVER_KH03_R2_VI.txt").write_text(handover_vi, encoding="utf-8")

        # Write pre-verifier checksums inventory
        chk_map: Dict[str, str] = {}
        for fp in sorted(run_dir.rglob("*")):
            if fp.is_file() and not fp.name.endswith(".tmp") and fp.relative_to(run_dir).as_posix() != "checksums/sha256_inventory.json":
                chk_map[fp.relative_to(run_dir).as_posix()] = _sha256_file(fp)
        write_json_durable_atomic(run_dir / "checksums" / "sha256_inventory.json", {"run_id": run_id, "files_sha256": chk_map})

        # Stage 8: Separate-OS Independent Verifier sealing 25_independent_OS_verifier_manifest_sha256.json
        print("[KH-03.R2] Stage 8: Launching separate-OS-process independent disk verifier (25)...", flush=True)
        verifier_script = APP_ROOT / "tools" / "kh_03_r2_independent_verifier.py"
        env_v = os.environ.copy()
        env_v["PYTHONPATH"] = _pypath_str()
        v_proc = subprocess.run(
            [sys.executable, str(verifier_script), "--run-dir", str(run_dir), "--expected-run-id", run_id, "--expected-tested-sha", start_head],
            cwd=str(APP_ROOT), env=env_v, capture_output=True, text=True, timeout=240,
        )
        print(v_proc.stdout, flush=True)
        if v_proc.stderr:
            print(v_proc.stderr, file=sys.stderr, flush=True)

        f25 = json.loads((run_dir / "25_independent_OS_verifier_manifest_sha256.json").read_text("utf-8"))
        return int(v_proc.returncode), run_dir, {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "verifier_exit_code": int(v_proc.returncode),
            "independent_verifier_status": f25.get("independent_verifier_status"),
            "overall_classification": f25.get("overall_classification"),
            "recomputed_verdicts": f25.get("recomputed_verdicts"),
            "violations": f25.get("violations"),
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


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AURUM DESK KH-03.R2 Methodology Closure Acceptance Orchestrator")
    ap.add_argument("--allow-local-dirty", action="store_true")
    ap.add_argument("--owner-waive-disk-preflight", action="store_true")
    ap.add_argument("--max-workers", type=int, default=GLOBAL_MAX_DESCENDANTS_KH02_R2)
    args = ap.parse_args(argv)
    if args.owner_waive_disk_preflight:
        return 2
    rc, _rdir, summary = run_kh03_r2_acceptance(
        allow_local_dirty=args.allow_local_dirty,
        owner_waive_disk_preflight=False,
        max_workers=args.max_workers,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
