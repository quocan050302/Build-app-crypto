#!/usr/bin/env python3
"""
aurum-desk/tools/run_kh_03_r3_acceptance.py

AURUM DESK — KH-03.R3 | ENTRY/EXIT FAILURE DIAGNOSIS, LEGACY METHOD GUARD & BOUNDED DEV RESEARCH
SPEC VERSION: KH-03.R3-v1.0 | RESEARCH ONLY | SAME BRANCH | ACTIVATION_READY=NO | OOS_EVALUATIONS=0

Produces a clean, immutable per-run directory under:
  aurum-desk/docs/artifacts/kh_03_r3_entry_exit_diagnosis/<UTC_RUN_ID>_<TESTED_SHA12>/
containing artifacts 00..25 (with 24 sealed by separate-OS independent verifier),
README_KH03_R3.md, HANDOVER_KH03_R3_VI.txt, checksums/, logs/, and junit/.
NEVER mutates any prior KH-03.R2, KH-03.R1, C4..C1, KH-02, or KH-01B runs or pointers.
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
    load_r2_canonical_dataset_readonly,
    probe_r2_c1_write_mounts_strict,
    replay_r2_same_opportunity_paired_counterfactuals,
    reprice_r2_fixed_fills_cost_counterfactual,
)
from services.kh_03_data_governance_service import audit_kh03_collector_and_escrow_readiness
from services.kh_03_r2_methodology_service import (
    build_kh03_r2_h3_advisory_ledger_and_disclosure,
    build_kh03_r2_h3_red_proof_and_policy_choice,
    run_kh03_r2_forward_only_walkforward,
    validate_kh03_r2_fold_causality,
)
from services.kh_03_r3_diagnostic_service import (
    audit_r3_entry_exit_geometry_and_accounting,
    build_r3_cost_and_break_even_sensitivity,
    build_r3_full_opportunity_lineage,
    build_r3_win_loss_failure_attribution,
    run_r3_negative_control_and_label_integrity_tests,
    validate_r3_forensic_ledger_independently,
)
from services.kh_03_r3_experiment_service import (
    KH03_R3_PRIMARY_ARMS,
    build_r3_causal_m5_unswept_target_ledger,
    build_r3_train_only_candidates_asof,
    freeze_r3_bounded_research_plan,
    query_nearest_causal_unswept_m5_target,
    run_r3_forward_only_dev_evaluation,
    run_r3_paired_and_strict_dev_evaluation_15_16,
)
from services.kh_03_r3_legacy_research_guard import (
    SCHEMA_VERSION_KH03_R3,
    SPEC_VERSION_KH03_R3,
    audit_kh03_r3_legacy_research_callgraph,
    build_r3_legacy_guard_artifacts_03_04,
    require_kh03_r2_or_newer_research_protocol,
    scan_python_source_for_forbidden_r1_usage,
    validate_r3_research_output_provenance,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    verify_fresh_junit_bijection_v17931211,
)
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_02_r2_c3_independent_verifier import _audit_memory_samples_c3
from tools.kh_03_r2_independent_verifier import PRIOR_KH03_R1_RUN_ID
from tools.kh_03_r3_independent_verifier import (
    KH03_R3_SOURCE_FILES_ALLOWLIST,
    PRIOR_KH03_R2_EVIDENCE_SHA,
    PRIOR_KH03_R2_PUB_HEAD_SHA,
    PRIOR_KH03_R2_RUN_ID,
    PRIOR_KH03_R2_TESTED_SHA,
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R3,
    recompute_kh03_r3_30_gates_and_verdicts,
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
from tools.run_kh_03_r2_acceptance import (
    KH03_R2_EXCLUSIVE_SUBPROCESS_TEST_FILES,
    _run_kh03_r2_174_red_green_fault_suite,
)
from tools.run_v17_9_3_1_2_2_verification import canonicalize_collect_node_id_v1793122

KH03_R3_ARTIFACTS_BASE_REL = "aurum-desk/docs/artifacts/kh_03_r3_entry_exit_diagnosis"
PRIOR_KH03_R1_DIR_REL = f"aurum-desk/docs/artifacts/kh_03_research_data/{PRIOR_KH03_R1_RUN_ID}"

KH03_R3_EXCLUSIVE_SUBPROCESS_TEST_FILES: Set[str] = set(KH03_R2_EXCLUSIVE_SUBPROCESS_TEST_FILES) | {
    "test_kh03_r3_resource_async_and_verifier_mutations.py",
}

FUNCTION_RESPONSIBILITY_MAP_KH03_R3: List[Dict[str, str]] = [
    {
        "function": "audit_kh03_r3_legacy_research_callgraph + require_kh03_r2_or_newer_research_protocol",
        "module": "services/kh_03_r3_legacy_research_guard.py",
        "inputs": "repo_root, allowed_legacy_compat_paths | protocol_id, evidence",
        "outputs": "AST caller classification + protocol certification/denial decision",
        "invariants": "Blocks direct/alias/re-export/eval R1 promotion in new modules; discloses LEGACY_DIRECT_CALL_REMAINS_AVAILABLE_FOR_HISTORICAL_ONLY",
        "tests": "test_kh03_r3_legacy_guard.py",
        "evidence": "01, 03, 04",
    },
    {
        "function": "build_r3_full_opportunity_lineage + audit_r3_entry_exit_geometry_and_accounting",
        "module": "services/kh_03_r3_diagnostic_service.py",
        "inputs": "datasets, mtf_bundle, r2_cands_1x, out_ledger_gz_path",
        "outputs": "05 lineage manifest + 06 streaming forensic Decimal ledger.jsonl.gz",
        "invariants": "Exact eligible=scheduled+busy_skipped conservation; gross-fees=net <= 0.001 USD; 0 overlaps",
        "tests": "test_kh03_r3_entry_exit_diagnosis.py",
        "evidence": "05, 06, 07, 08",
    },
    {
        "function": "build_r3_win_loss_failure_attribution + build_r3_cost_and_break_even_sensitivity + run_r3_negative_control_and_label_integrity_tests",
        "module": "services/kh_03_r3_diagnostic_service.py",
        "inputs": "forensic_rows, datasets, mtf_bundle, ledger_bundle, r2_cands_1x",
        "outputs": "07 denominator breakdown, 08 3-axis failure attribution, 09 6-cost breakeven, 10 direction-invert/placebo/future-mutation",
        "invariants": "Separates closed/filled/scheduled/censored; labels association vs causality; confirms accounting is NOT engine bug",
        "tests": "test_kh03_r3_entry_exit_diagnosis.py, test_kh03_r3_research_falsification.py",
        "evidence": "07, 08, 09, 10",
    },
    {
        "function": "freeze_r3_bounded_research_plan + build_r3_train_only_candidates_asof + run_r3_forward_only_dev_evaluation + run_r3_paired_and_strict_dev_evaluation_15_16",
        "module": "services/kh_03_r3_experiment_service.py",
        "inputs": "datasets, mtf_bundle, ledger_bundle, m5_ledger, tested_code_sha",
        "outputs": "11 frozen plan SHA, 12 conservation, 13/14 R2 forward-only 3-fold replays, 15 6-cost dynamic vs fixed-fill, 16 strict CI/Holm/DEV gate",
        "invariants": "Max 3 distinct arms (E1/E2/E3), H3 excluded, no OPEN_TO_OPEN, embargo>=240m, future_train=0, strict R2 CI gate, NO_PROVEN_EDGE on DEV",
        "tests": "test_kh03_r3_research_falsification.py, test_kh03_r3_forward_only_economics.py",
        "evidence": "11, 12, 13, 14, 15, 16",
    },
    {
        "function": "verify_kh03_r3_artifacts_independently_from_disk",
        "module": "tools/kh_03_r3_independent_verifier.py",
        "inputs": "run_dir, expected_run_id, expected_tested_sha",
        "outputs": "24_independent_OS_verifier_manifest_sha256.json + exit code 0/1/2",
        "invariants": "Distinct OS PID; zero import from orchestrator; independently recomputes 06 ledger, 11 SHA, 13/14 WF, 16 CI/DEV, 18 JUnit, 19 W1-W4, 20 RAM/disk, G01..G30",
        "tests": "test_kh03_r3_resource_async_and_verifier_mutations.py (16 new mutations)",
        "evidence": "22, 23, 24",
    },
]


def _audit_kh03_r3_ownership_01(tested_sha: str, f04_cg: Mapping[str, Any]) -> Dict[str, Any]:
    locked_inv = []
    for rel in LOCKED_KEEP_AS_IS_FILES_KH02_R2:
        p = REPO_ROOT / rel
        locked_inv.append(
            {
                "rel_path": rel,
                "exists": p.is_file(),
                "sha256": _sha256_file(p) if p.is_file() else None,
                "ast_function_count": len(_ast_function_inventory(p)) if (rel.endswith(".py") and p.is_file()) else None,
            }
        )
    rc_diff, diff_out = _git_cmd(["diff", "--name-only", f"{PRIOR_KH03_R2_PUB_HEAD_SHA}..HEAD"])
    changed_files = [ln.strip() for ln in diff_out.splitlines() if ln.strip()] if rc_diff == 0 else []
    locked_mod = [f for f in changed_files if f in set(LOCKED_KEEP_AS_IS_FILES_KH02_R2)]

    forbidden_hits: List[str] = []
    r3_inv: List[Dict[str, Any]] = []
    for rel in KH03_R3_SOURCE_FILES_ALLOWLIST:
        p = REPO_ROOT / rel
        if not p.is_file():
            continue
        if rel.endswith(".py") and ("kh_03_r3" in rel or "kh03_r3" in rel):
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
            r3_inv.append({"rel_path": rel, "sha256": _sha256_file(p), "functions_count": len(_ast_function_inventory(p))})

    c_live = CostAssumptions()
    cost_ok = abs(float(c_live.maker_fee_rate) - 0.0002) <= 1e-12 and abs(float(c_live.taker_fee_rate) - 0.0004) <= 1e-12 and abs(float(c_live.slippage_usd) - 0.10) <= 1e-12
    ok = len(locked_mod) == 0 and len(forbidden_hits) == 0 and cost_ok and f04_cg.get("LEGACY_METHOD_GUARD") == "PASS"
    return {
        "spec_version": SPEC_VERSION_KH03_R3,
        "schema_version": SCHEMA_VERSION_KH03_R3,
        "tested_executable_sha": tested_sha,
        "prior_r2_pub_head_sha": PRIOR_KH03_R2_PUB_HEAD_SHA,
        "locked_keep_as_is_count": len(locked_inv),
        "locked_keep_as_is_inventory": locked_inv,
        "locked_files_modified": locked_mod,
        "locked_files_modified_count": len(locked_mod),
        "changed_files_vs_r2": changed_files,
        "kh03_r3_module_inventory": r3_inv,
        "function_responsibility_map": FUNCTION_RESPONSIBILITY_MAP_KH03_R3,
        "legacy_guard_status": f04_cg.get("LEGACY_METHOD_GUARD"),
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
    for rel_p, exp_sha, lbl in PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R3:
        p = REPO_ROOT / rel_p
        act = _sha256_file(p) if p.is_file() else ""
        m = p.is_file() and (act == exp_sha)
        if not m:
            all_ok = False
        prior_map[lbl] = {"rel_path": rel_p, "expected_sha256": exp_sha, "actual_sha256": act, "match": m}

    f08_c4, f09_c4 = audit_kh03_collector_and_escrow_readiness(sentinel_tmp / "c4_prov_r3")
    prov_ok = (
        f08_c4.get("status") == "PASS"
        and f09_c4.get("status") == "PASS"
        and f08_c4.get("network_capture_enabled") is False
        and f08_c4.get("live_network_capture_executed") is False
        and int(f09_c4.get("real_oos_escrow_final_record_count", -1)) == 0
    )
    return {
        "spec_version": SPEC_VERSION_KH03_R3,
        "schema_version": SCHEMA_VERSION_KH03_R3,
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
            "real_oos_escrow_integrity_valid": f09_c4.get("real_oos_escrow_integrity_valid"),
            "real_oos_escrow_final_record_count": f09_c4.get("real_oos_escrow_final_record_count"),
            "qa_fixture_refused_by_real_oos_escrow": f09_c4.get("qa_fixture_refused_by_real_oos_escrow"),
            "status": "PASS" if prov_ok else "FAIL",
        },
        "status": "PASS" if (all_ok and prov_ok) else "FAIL",
    }


def _run_kh03_r3_194_red_green_fault_suite(
    *,
    sentinel_tmp: Path,
    datasets: Mapping[str, Any],
    mtf_bundle: Mapping[str, Any],
    ledger_bundle: Mapping[str, Any],
    r2_cands_1x: Mapping[str, Any],
    replay_bundle: Mapping[str, Any],
    live_ff: Mapping[str, Any],
    forensic_gz: Path,
    f07: Mapping[str, Any],
    f13: Mapping[str, Any],
    m5_ledger: Mapping[str, Any],
) -> Dict[str, Any]:
    # Actually execute ALL 174 inherited R2+R1+C4 controls!
    tmp_h3_gz = sentinel_tmp / "tmp_r2_h3_for_faults.jsonl.gz"
    _, tmp_f07_h3 = build_kh03_r2_h3_red_proof_and_policy_choice(
        datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_1x=r2_cands_1x, tested_code_sha="c" * 40
    )
    _, tmp_f09_h3 = build_kh03_r2_h3_advisory_ledger_and_disclosure(
        datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_1x=r2_cands_1x,
        h3_policy_choice_07=tmp_f07_h3, out_h3_ledger_gz_path=tmp_h3_gz,
    )
    tmp_f11_wf, _, _ = run_kh03_r2_forward_only_walkforward(
        datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_1x=r2_cands_1x, tested_code_sha="c" * 40
    )
    r2_174 = _run_kh03_r2_174_red_green_fault_suite(
        sentinel_tmp=sentinel_tmp / "r2_174_sub", datasets=datasets, mtf_bundle=mtf_bundle,
        ledger_bundle=ledger_bundle, replay_bundle=replay_bundle, live_ff=live_ff,
        h3_ledger_gz=tmp_h3_gz, f09=tmp_f09_h3, f11=tmp_f11_wf,
    )
    controls = list(r2_174["controls"])
    r3_new = 0

    def _rec(fid: str, name: str, fn: str, red_ok: bool, green_ok: bool, detail: str) -> None:
        nonlocal r3_new
        r3_new += 1
        controls.append(
            {
                "fault_id": fid, "name": name, "anchored_function": fn,
                "red_rejected": bool(red_ok), "green_accepted": bool(green_ok),
                "status": "PASS" if (red_ok and green_ok) else "FAIL", "diagnostic": detail,
            }
        )

    valid_ev = {
        "ACTIVATION_READY": "NO", "OOS_EVALUATIONS": 0, "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE",
        "h3_promoted_as_executable": False, "temporal_validation_kind": "FORWARD_ONLY_DEV_VALIDATION",
        "embargo_minutes": 240, "future_train_bars_count": 0,
        "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
    }
    g_proto = require_kh03_r2_or_newer_research_protocol("KH-03.R3-v1.0", valid_ev)["admitted_for_certification"]

    # KH03R3-F01..F10: Legacy method firewall & provenance guard
    _rec("KH03R3-F01", "R1_PROTOCOL_CERTIFICATION_DENIED", "require_kh03_r2_or_newer_research_protocol", not require_kh03_r2_or_newer_research_protocol("KH-03.R1-v1.0", valid_ev)["admitted_for_certification"], g_proto, "R1 protocol cannot certify")
    _rec("KH03R3-F02", "UNKNOWN_PROTOCOL_DENIED", "require_kh03_r2_or_newer_research_protocol", not require_kh03_r2_or_newer_research_protocol("UNKNOWN-v9", valid_ev)["admitted_for_certification"], g_proto, "unknown protocol denied")
    _rec("KH03R3-F03", "R1_DIRECT_IMPORT_DETECTED", "scan_python_source_for_forbidden_r1_usage", len(scan_python_source_for_forbidden_r1_usage("from services.kh_03_research_service import run_kh03_full_research_study_10_to_16")) > 0, len(scan_python_source_for_forbidden_r1_usage("x = 1")) == 0, "direct R1 import caught")
    _rec("KH03R3-F04", "R1_ALIAS_IMPORT_DETECTED", "scan_python_source_for_forbidden_r1_usage", len(scan_python_source_for_forbidden_r1_usage("from services.kh_03_research_service import preregister_kh03_research_plan as p")) > 0, True, "alias R1 import caught")
    _rec("KH03R3-F05", "R1_REEXPORT_ALL_DETECTED", "scan_python_source_for_forbidden_r1_usage", len(scan_python_source_for_forbidden_r1_usage("__all__ = ['build_kh03_partition_manifest_and_noncontamination']")) > 0, True, "re-export caught")
    _rec("KH03R3-F06", "R1_GETATTR_EVAL_WRAPPER_DETECTED", "scan_python_source_for_forbidden_r1_usage", len(scan_python_source_for_forbidden_r1_usage("getattr(m, 'run_kh03_full_research_study_10_to_16')()")) > 0 and len(scan_python_source_for_forbidden_r1_usage("eval('run_kh03_full_research_study_10_to_16()')")) > 0, True, "dynamic wrapper caught")
    _rec("KH03R3-F07", "PROVENANCE_FORGED_PROVEN_EDGE_DENIED", "validate_r3_research_output_provenance", not validate_r3_research_output_provenance({"spec_version": SPEC_VERSION_KH03_R3, "RESEARCH_ECONOMIC_VERDICT": "PROVEN_EDGE"})["valid"], validate_r3_research_output_provenance({"spec_version": SPEC_VERSION_KH03_R3, "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE", "ACTIVATION_READY": "NO", "OOS_EVALUATIONS": 0})["valid"], "forged PROVEN_EDGE denied")
    _rec("KH03R3-F08", "PROVENANCE_REUSED_DEV_FRESH_OOS_DENIED", "validate_r3_research_output_provenance", not validate_r3_research_output_provenance({"spec_version": SPEC_VERSION_KH03_R3, "is_fresh_oos": True})["valid"], True, "reused DEV as fresh OOS denied")
    _rec("KH03R3-F09", "PROVENANCE_H3_EXECUTABLE_PROMOTION_DENIED", "validate_r3_research_output_provenance", not validate_r3_research_output_provenance({"spec_version": SPEC_VERSION_KH03_R3, "H3_RESIZE_EXECUTION_STATUS": "PASS"})["valid"], True, "H3 execution pass denied")
    _rec("KH03R3-F10", "PROVENANCE_FAKE_L1_FROM_M1_DENIED", "validate_r3_research_output_provenance", not validate_r3_research_output_provenance({"spec_version": SPEC_VERSION_KH03_R3, "observed_l1_bbo_available": True})["valid"], True, "fake observed L1 denied")

    # KH03R3-F11..F20: Forensic ledger, denominator, M5 target causality, WF & CLI safety
    f07_tamp = copy.deepcopy(dict(f07))
    f07_tamp["overall_pooled_null_1x"]["losses"] = 0
    _rec("KH03R3-F11", "FORENSIC_07_DROPPED_LOSSES_CAUGHT", "validate_r3_forensic_ledger_independently", not validate_r3_forensic_ledger_independently(forensic_gz, f07_tamp)["valid"], validate_r3_forensic_ledger_independently(forensic_gz, f07)["valid"], "excluded losses caught vs 06")
    f07_fee_t = copy.deepcopy(dict(f07))
    f07_fee_t["overall_pooled_null_1x"]["total_fees_usd"] = 0.0
    _rec("KH03R3-F12", "FORENSIC_07_ZERO_FEE_TAMPER_CAUGHT", "validate_r3_forensic_ledger_independently", not validate_r3_forensic_ledger_independently(forensic_gz, f07_fee_t)["valid"], True, "zeroed fee in 07 caught")
    # Causal M5 target must NEVER read a level confirmed AFTER decision_ts_ms!
    future_only_ledger = {"swings_high": [(9_999_999_999_999, 4120.0, 999999)], "swings_low": []}
    q_fut = query_nearest_causal_unswept_m5_target(
        m5_ledger=future_only_ledger, direction="LONG", decision_idx_1m=100, decision_ts_ms=1791500000000,
        entry_price=4100.0, selected_sl=4095.0, baseline_tp=4140.0, costs=CostAssumptions(),
    )
    _rec("KH03R3-F13", "M5_FUTURE_SWING_IGNORED_STRICTLY", "query_nearest_causal_unswept_m5_target", q_fut["replaced_baseline_tp"] is False and q_fut["selected_tp"] == 4140.0, True, "future M5 target never visible")
    swept_ledger = {"swings_high": [(1791400000000, 4120.0, 50)], "swings_low": []}
    q_sw = query_nearest_causal_unswept_m5_target(
        m5_ledger=swept_ledger, direction="LONG", decision_idx_1m=100, decision_ts_ms=1791500000000,
        entry_price=4100.0, selected_sl=4095.0, baseline_tp=4140.0, costs=CostAssumptions(),
    )
    _rec("KH03R3-F14", "M5_ALREADY_SWEPT_SWING_IGNORED", "query_nearest_causal_unswept_m5_target", q_sw["replaced_baseline_tp"] is False, True, "already-swept M5 target skipped")
    good_m5 = {"swings_high": [(1791400000000, 4125.0, 999999)], "swings_low": []}
    q_ok = query_nearest_causal_unswept_m5_target(
        m5_ledger=good_m5, direction="LONG", decision_idx_1m=100, decision_ts_ms=1791500000000,
        entry_price=4100.0, selected_sl=4095.0, baseline_tp=4140.0, costs=CostAssumptions(),
    )
    _rec("KH03R3-F15", "M5_VALID_ASOF_UNSWEPT_NEARER_TARGET_ADMITTED", "query_nearest_causal_unswept_m5_target", True, q_ok["replaced_baseline_tp"] is True and q_ok["selected_tp"] == 4125.0, "valid as-of M5 swing selected")
    _rec("KH03R3-F16", "PROVENANCE_OLD_CI_CROSSES_ZERO_ON_NEG_DENIED", "validate_r3_research_output_provenance", not validate_r3_research_output_provenance({"spec_version": SPEC_VERSION_KH03_R3, "primary_arm_evaluations": {"E1": {"ny_day_cluster_95ci_r": [-1.0, -0.5], "failed_advancement_criteria": ["NY_DAY_95CI_CROSSES_ZERO:ci_low=None"]}}})["valid"], True, "old R1 CI bug rejected")
    _rec("KH03R3-F17", "PROVENANCE_SAFETY_MODE_LIVE_ON_DENIED", "validate_r3_research_output_provenance", not validate_r3_research_output_provenance({"spec_version": SPEC_VERSION_KH03_R3, "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "ON", "PAPER": "OFF", "AUTO": "OFF"}})["valid"], True, "LIVE=ON rejected")
    _rec("KH03R3-F18", "PROTOCOL_FUTURE_TRAIN_BARS_NONZERO_DENIED", "require_kh03_r2_or_newer_research_protocol", not require_kh03_r2_or_newer_research_protocol("KH-03.R3-v1.0", dict(valid_ev, future_train_bars_count=5))["admitted_for_certification"], g_proto, "future train bars blocked")
    _rec("KH03R3-F19", "PROTOCOL_EMBARGO_LT_240M_DENIED", "require_kh03_r2_or_newer_research_protocol", not require_kh03_r2_or_newer_research_protocol("KH-03.R3-v1.0", dict(valid_ev, embargo_minutes=120))["admitted_for_certification"], g_proto, "embargo < 240m blocked")
    env_w = os.environ.copy()
    env_w["KH03_OWNER_WAIVE_DISK_PREFLIGHT"] = "1"
    p_w = subprocess.run([sys.executable, str(APP_ROOT / "tools" / "run_kh_03_r3_acceptance.py"), "--help"], cwd=str(APP_ROOT), env=env_w, capture_output=True, text=True, timeout=20)
    _rec("KH03R3-F20", "KH03_R3_DISK_WAIVER_ENV_REFUSED", "run_kh_03_r3_acceptance.main", p_w.returncode == 2, True, "R3 runner refuses disk waiver with exit 2")

    passed_n = sum(1 for c in controls if c["status"] == "PASS")
    fids = [c["fault_id"] for c in controls]
    return {
        "spec_version": SPEC_VERSION_KH03_R3,
        "schema_version": SCHEMA_VERSION_KH03_R3,
        "inherited_r2_controls_count": len(r2_174["controls"]),
        "new_kh03_r3_controls_count": r3_new,
        "total_controls": len(controls),
        "unique_fault_ids": len(set(fids)),
        "passed_controls": passed_n,
        "failed_controls": len(controls) - passed_n,
        "controls": controls,
        "status": "PASS" if (passed_n == len(controls) and len(set(fids)) == len(controls) and len(controls) >= 194) else "FAIL",
    }


def _run_kh03_r3_verifier_mutation_suite_22(sentinel_tmp: Path) -> Dict[str, Any]:
    from tests.test_kh03_r3_resource_async_and_verifier_mutations import (
        KH03_R3_VERIFIER_MUTATIONS_SPECS,
        build_synthetic_valid_kh03_r3_run_dir,
        run_kh03_r3_verifier_subprocess,
    )

    base_clone = sentinel_tmp / "kh03_r3_verif_mut_base"
    rid, tsha = build_synthetic_valid_kh03_r3_run_dir(base_clone)
    rc_clean, _ = run_kh03_r3_verifier_subprocess(base_clone, rid, tsha)
    clean_ok = (rc_clean == 0)

    results: List[Dict[str, Any]] = []
    for mid, mut_fn, expected_tok in KH03_R3_VERIFIER_MUTATIONS_SPECS:
        cdir = sentinel_tmp / f"kh03_r3_mut_{mid}"
        shutil.copytree(base_clone, cdir)
        mut_fn(cdir)
        rc, out_txt = run_kh03_r3_verifier_subprocess(cdir, rid, tsha)
        detected = (rc != 0) and (expected_tok in out_txt)
        results.append(
            {
                "mutation_id": mid, "expected_violation_token": expected_tok,
                "verifier_exit_code": rc, "detected_fail_closed": detected,
            }
        )

    det_n = sum(1 for r in results if r["detected_fail_closed"])
    return {
        "spec_version": SPEC_VERSION_KH03_R3,
        "schema_version": SCHEMA_VERSION_KH03_R3,
        "isolated_temp_clones_only": True,
        "sealed_run_dir_mutated": False,
        "clean_clone_verifier_green": clean_ok,
        "mutations_tested_count": len(results),
        "mutations_detected_count": det_n,
        "mutations_failed_detection_count": len(results) - det_n,
        "results": results,
        "status": "PASS" if (clean_ok and det_n == len(results) and len(results) >= 16) else "FAIL",
    }


def _collect_and_build_kh03_r3_lpt_shards(owned_temp_root: Path) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]]]:
    all_test_files = sorted(
        str(p.relative_to(APP_ROOT)).replace("\\", "/")
        for p in (BACKEND_ROOT / "tests").glob("test_*.py")
        if p.is_file()
    )
    col_out = owned_temp_root / "collect_kh03_r3_stdout.log"
    col_err = owned_temp_root / "collect_kh03_r3_stderr.log"
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
        raise RuntimeError(f"KH03_R3_PYTEST_COLLECTION_FAILED:rc={proc.returncode}:{err_txt}")

    collected_by_file: Dict[str, List[str]] = {}
    for ln in col_out.read_text("utf-8", errors="replace").splitlines():
        cid = canonicalize_collect_node_id_v1793122(ln)
        if cid is not None:
            collected_by_file.setdefault(cid.split("::", 1)[0], []).append(cid)
    for k in collected_by_file:
        collected_by_file[k].sort()

    empirical_ms = _load_empirical_file_durations_ms_from_kh01b()
    non_sub = [f for f in all_test_files if Path(f).name not in KH03_R3_EXCLUSIVE_SUBPROCESS_TEST_FILES]
    sub_files = [f for f in all_test_files if Path(f).name in KH03_R3_EXCLUSIVE_SUBPROCESS_TEST_FILES]
    shards = _build_lpt_shards_kh01b_r1(non_sub, collected_by_file, empirical_ms)
    for sh in shards:
        if sh.get("shard_name") == "SHARD_SINGLE_CHILD_02_BUDGET2":
            sh["exclusive"] = True
            sh["slot_cost"] = 4
    for idx_s, sf in enumerate(sorted(sub_files), 1):
        shards.append(
            {
                "shard_name": f"SHARD_SINGLE_CHILD_KH03_R3_SUBPROC_{idx_s:02d}",
                "files": [sf], "exclusive": True, "slot_cost": 4, "priority_weight": 260_000, "needs_venv": False,
            }
        )
    return collected_by_file, shards


def _run_kh03_r3_full_repo_pytest_sharded(
    run_dir: Path, sentinel_tmp: Path, *, run_id: str, tested_sha: str, effective_disk_reserve_bytes: int, max_workers: int,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    empirical_r2_rss = load_empirical_r2_shard_rss_bytes(REPO_ROOT)
    collected_by_file, r3_shards = _collect_and_build_kh03_r3_lpt_shards(sentinel_tmp)
    active_shards_tracker: Dict[str, Any] = {}
    junit_res = asyncio.run(
        run_async_lpt_pytest_shards_kh01b_r1(
            run_dir, sentinel_tmp,
            run_id=run_id, tested_executable_sha=tested_sha,
            effective_disk_reserve_bytes=effective_disk_reserve_bytes,
            max_parallel_workers=max_workers, ram_policy_mode="R3_STRICT_512MIB",
            empirical_rss_by_shard=empirical_r2_rss, active_shards_tracker=active_shards_tracker,
            shard_specs_override=r3_shards, collected_by_file_override=collected_by_file,
        )
    )
    elapsed = round(time.perf_counter() - t0, 3)
    cm, sm = junit_res["collection_manifest"], junit_res["shard_manifest"]
    bj = verify_fresh_junit_bijection_v17931211(
        run_dir, cm, sm, expected_run_id=run_id, expected_tested_sha=tested_sha,
        mandatory_node_prefixes=("backend/tests/test_kh_02_r2", "backend/tests/test_kh_03", "backend/tests/test_kh03_r2", "backend/tests/test_kh03_r3"),
        require_shard_schedule=True,
    )
    collected_n = int(cm.get("collected_node_ids_count") or len(cm.get("collected_node_ids") or ()))
    executed_n = int(bj.get("executed_node_ids_count") or bj.get("executed_count") or 0)
    passed_n, skipped_n, failed_n, error_n, mand_skip_n = (
        int(bj.get("passed_count") or 0), int(bj.get("skipped_count") or 0),
        int(bj.get("failed_count") or 0), int(bj.get("error_count") or 0), int(bj.get("mandatory_skipped_count") or 0),
    )
    nonzero_shards = int(sm.get("nonzero_exit_shards_count") or 0)
    ok = bj.get("status") == "PASS" and nonzero_shards == 0 and collected_n >= 5646 and collected_n == executed_n and failed_n == 0 and error_n == 0 and mand_skip_n == 0
    return {
        "spec_version": SPEC_VERSION_KH03_R3, "schema_version": SCHEMA_VERSION_KH03_R3,
        "status": "PASS" if ok else "FAIL", "elapsed_sec": elapsed, "max_workers_used": max_workers,
        "collected_count": collected_n, "executed_count": executed_n, "passed_count": passed_n,
        "skipped_count": skipped_n, "mandatory_skipped_count": mand_skip_n, "failed_count": failed_n,
        "error_count": error_n, "nonzero_exit_shards_count": nonzero_shards, "junit_bijection_exact": bj.get("status") == "PASS",
        "collection_manifest": cm, "shard_manifest": sm, "bijection_audit": bj,
    }


def _compute_optimum_worker_policy(bench_doc: Mapping[str, Any]) -> Dict[str, Any]:
    runs = list(bench_doc.get("runs") or ())
    by_mode: Dict[str, List[float]] = {}
    for r in runs:
        ml = str(r.get("mode_label") or "")
        t = r.get("elapsed_sec") if r.get("elapsed_sec") is not None else (r.get("wall_sec") if r.get("wall_sec") is not None else r.get("duration_sec"))
        if t is not None and ml in ("w=1", "w=2", "w=4"):
            by_mode.setdefault(ml, []).append(float(t))
    means = {m: round(sum(v) / len(v), 4) for m, v in by_mode.items() if v}
    opt = min(means.items(), key=lambda kv: kv[1])[0] if means else "w=1"
    return {"mean_wall_sec_by_mode": means, "optimum_worker_policy": opt}


def _render_kh03_r3_readme_and_handover(
    *, run_id: str, f00: Mapping[str, Any], f04: Mapping[str, Any], f07: Mapping[str, Any],
    f08: Mapping[str, Any], f16: Mapping[str, Any], f17: Mapping[str, Any], f18: Mapping[str, Any],
    f19: Mapping[str, Any], f20: Mapping[str, Any], f21: Mapping[str, Any], f22: Mapping[str, Any], f23: Mapping[str, Any],
) -> Tuple[str, str]:
    v, g = f23["verdicts"], f23["gates_30"]
    ov = f07["overall_pooled_null_1x"]
    mp = f00["strict_multi_mount_probe"]

    md_lines = [
        "# AURUM DESK — KH-03.R3 | ENTRY/EXIT FAILURE DIAGNOSIS, LEGACY GUARD & BOUNDED DEV RESEARCH",
        "",
        f"- **Phân loại kỹ thuật**: **`{f23['overall_classification']}`**",
        f"- **Spec Version**: `{SPEC_VERSION_KH03_R3}` | **Run ID**: `{run_id}`",
        f"- **Nhánh bắt buộc**: `{f00['branch']}` | **Tested SHA**: `{f00['tested_executable_sha']}`",
        "- **Khóa an toàn**: `PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO | OOS_EVALUATIONS=0`",
        "",
        "## 1. Kết luận trực giao",
        "",
        "| Chiều kiểm định | Kết luận |",
        "|---|---|",
        *[f"| `{k}` | **{val}** |" for k, val in v.items()],
        "",
        "## 2. Ma trận 30 cổng (`G01`..`G30`)",
        "",
        "| Cổng | Trạng thái |",
        "|---|---|",
        *[f"| `{k}` | **{val}** |" for k, val in g.items()],
        "",
        "## 3. Tóm tắt số đo thực tế",
        f"- **P0-A Legacy Guard (`03`, `04`)**: `LEGACY_METHOD_GUARD={f04['callgraph_audit']['LEGACY_METHOD_GUARD']}`, unauthorized callers=`{f04['callgraph_audit']['unauthorized_r1_callers_count']}`, `LEGACY_DIRECT_CALL_REMAINS_AVAILABLE_FOR_HISTORICAL_ONLY=True`.",
        f"- **P0-B Forensic Diagnosis (`05`..`10`)**: NULL 1X closed=`{ov['closed_count']}` (`{ov['wins']}W/{ov['losses']}L`, win_rate=`{ov['win_rate_over_closed']}`, net=`{ov['net_pnl_usd']} USD`, fees=`{ov['total_fees_usd']} USD`, expR=`{ov['expectancy_r']}`); engine bug=`{f08['three_axis_diagnosis']['ENGINE_AXIS']['accounting_or_simulator_bug_detected']}`; attribution=`{f08['attribution_epistemic_label']}`.",
        f"- **P1-C Bounded DEV Research (`11`..`16`)**: `DEV_RESEARCH_CANDIDATE={f16['DEV_RESEARCH_CANDIDATE']}`, `RESEARCH_ECONOMIC_VERDICT={f16['RESEARCH_ECONOMIC_VERDICT']}`, `KH04_ENTRY={f16['KH04_ENTRY']}`, `OOS_EVALUATIONS=0`.",
        f"- **Baseline Parity (`17`)**: `{f17.get('BASELINE_PARITY_VERDICT')}` (`A1={f17.get('a1_population_count')}/0`, `SI8={f17.get('si8_population_count')}/0`, `3-adapter={f17.get('three_adapter_population_count')}/0`, `D4={f17.get('d4_historical_closed_trades')}`).",
        f"- **Full Pytest (`18`)**: `{f18['status']}` — collected=`{f18.get('collected_count')}`, executed=`{f18.get('executed_count')}`, passed=`{f18.get('passed_count')}`, skipped=`{f18.get('skipped_count')}`, failed=`{f18.get('failed_count')}`.",
        f"- **Faults (`21`)**: `{f21['passed_controls']}/{f21['total_controls']}` (`{f21['inherited_r2_controls_count']}` inherited + `{f21['new_kh03_r3_controls_count']}` new R3). **Mutations (`22`)**: `{f22['mutations_detected_count']}/{f22['mutations_tested_count']}`.",
        f"- **Tài nguyên (`00`, `20`)**: Preflight `{mp['min_free_gib']} GiB`, runtime min `{f20['min_disk_free_gib']} GiB`, RAM min `{f20['min_available_ram_mib']} MiB`, max children `{f20['max_descendant_children']}`, orphans `{f20['orphan_children_after_cleanup']}`.",
        f"- **Optimum Worker Policy (`19`)**: pytest=`{f19['pytest_optimum_worker_policy']['optimum_worker_policy']}`, science=`{f19['science_optimum_worker_policy']['optimum_worker_policy']}`.",
        "",
        "## 4. Bản đồ trách nhiệm hàm (Function Responsibility Map)",
        "",
        "| Hàm | Module | Bất biến cốt lõi | Tests | Bằng chứng |",
        "|---|---|---|---|---|",
        *[f"| `{r['function']}` | `{r['module']}` | {r['invariants']} | `{r['tests']}` | `{r['evidence']}` |" for r in FUNCTION_RESPONSIBILITY_MAP_KH03_R3],
    ]
    md = "\n".join(md_lines) + "\n"

    vi_lines = [
        f"AURUM DESK — KH-03.R3 ENTRY/EXIT DIAGNOSIS & BOUNDED DEV RESEARCH | RUN_ID={run_id}",
        f"PHAN LOAI: {f23['overall_classification']}",
        f"BRANCH={f00['branch']} | TESTED_SHA={f00['tested_executable_sha']}",
        f"KH03_R3_ENGINEERING={v['KH03_R3_ENGINEERING']} | RESEARCH_METHOD_VALIDITY={v['RESEARCH_METHOD_VALIDITY']} | DIAGNOSTIC={v['ENTRY_EXIT_DIAGNOSTIC_VALIDITY']}",
        f"DEV_RESEARCH_CANDIDATE={v['DEV_RESEARCH_CANDIDATE']} | RESEARCH_ECONOMIC_VERDICT=NO_PROVEN_EDGE | FRESH_OOS=BLOCKED_NO_UNTOUCHED_DATA",
        f"KH04_ENTRY={v['KH04_ENTRY']} | OOS_EVALUATIONS=0 | ACTIVATION_READY=NO | PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF",
        f"PYTEST={f18['status']} ({f18.get('passed_count')}/{f18.get('collected_count')}, failed={f18.get('failed_count')}) | FAULTS={f21['passed_controls']}/{f21['total_controls']} | MUTATIONS={f22['mutations_detected_count']}/{f22['mutations_tested_count']}",
    ]
    return md, "\n".join(vi_lines) + "\n"


def run_kh03_r3_acceptance(
    *, allow_local_dirty: bool = False, owner_waive_disk_preflight: bool = False, max_workers: int = GLOBAL_MAX_DESCENDANTS_KH02_R2,
) -> Tuple[int, Path, Dict[str, Any]]:
    if owner_waive_disk_preflight:
        raise SystemExit(2)

    t_start = time.perf_counter()
    orch_pid = os.getpid()
    utc_now = datetime.now(timezone.utc)
    utc_stamp = utc_now.strftime("%Y%m%dT%H%M%SZ")

    _, branch = _git_cmd(["branch", "--show-current"])
    if branch != MANDATORY_BRANCH_KH02_R2:
        raise RuntimeError(f"WRONG_BRANCH:{branch}")
    _, start_head = _git_cmd(["rev-parse", "HEAD"])
    _, porcelain = _git_cmd(["status", "--porcelain=v1"])
    git_clean = (len(porcelain.strip()) == 0)
    if (not git_clean) and (not allow_local_dirty):
        raise RuntimeError("DIRTY_WORKING_TREE_REFUSED_FOR_SEALED_RUN")
    _, remote_out = _git_cmd(["ls-remote", "origin", f"refs/heads/{MANDATORY_BRANCH_KH02_R2}"])
    remote_sha = remote_out.split()[0] if remote_out.strip() else ""

    run_id = f"{utc_stamp}_{start_head[:12]}"
    art_base = (REPO_ROOT / KH03_R3_ARTIFACTS_BASE_REL).resolve()
    sentinel_tmp = create_sentinel_owned_temp_dir_r2_c1(f"kh03r3_{utc_stamp}_", run_id=run_id, owner_pid=orch_pid)
    write_mount_paths = {
        "repo_volume": REPO_ROOT, "app_volume": APP_ROOT,
        "artifacts_output": art_base if art_base.exists() else APP_ROOT,
        "temp_volume": sentinel_tmp, "python_venv": Path(sys.executable).resolve().parent,
        "caches": Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")),
        "logs": art_base / run_id / "logs",
    }
    strict_probe = probe_r2_c1_write_mounts_strict(write_mount_paths, atomic_probe_dirs=[(sentinel_tmp, True, run_id)])
    avail_ram_mib = round(psutil.virtual_memory().available / (1024.0 * 1024.0), 2)
    strict_disk_ok = bool(strict_probe["can_run_heavy_stages"])
    ram_ok = avail_ram_mib >= PREFLIGHT_RAM_MIN_MIB_KH02_R2
    can_run_heavy = strict_disk_ok and ram_ok
    eff_workers = max(1, min(int(max_workers), GLOBAL_MAX_DESCENDANTS_KH02_R2))

    run_dir = art_base / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "junit").mkdir(exist_ok=True)
    (run_dir / "checksums").mkdir(exist_ok=True)

    watch_paths = [REPO_ROOT, APP_ROOT, run_dir, sentinel_tmp, Path(sys.executable).resolve().parent]
    sampler = _BoundedResourceSampler(
        out_gz=run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz",
        watch_path=run_dir, watch_paths=watch_paths, interval_sec=0.5, enforce_runtime_disk_abort=can_run_heavy,
    )
    sampler.start()
    sampler_stopped = False

    try:
        print(f"[KH-03.R3] Start run_id={run_id} tested_sha={start_head[:12]} disk_min={strict_probe['min_free_gib']}GiB ram={avail_ram_mib}MiB", flush=True)

        # Stage 1: Legacy Guard (03, 04), Ownership (01), Prior Immutability (02)
        print("[KH-03.R3] Stage 1: P0-A Legacy R1 Firewall, AST Ownership & Prior Sealed Immutability (01..04)...", flush=True)
        f03, f04 = build_r3_legacy_guard_artifacts_03_04(repo_root=REPO_ROOT, prior_r1_dir=REPO_ROOT / PRIOR_KH03_R1_DIR_REL)
        f01 = _audit_kh03_r3_ownership_01(start_head, f04["callgraph_audit"])
        f02 = _audit_prior_sealed_02(sentinel_tmp)
        write_json_durable_atomic(run_dir / "01_ast_ownership_legacy_guard_and_keep_as_is_delta.json", f01)
        write_json_durable_atomic(run_dir / "02_prior_sealed_sha_and_canonical_dataset_integrity.json", f02)
        write_json_durable_atomic(run_dir / "03_r1_legacy_false_pass_red_reproductions.json", f03)
        write_json_durable_atomic(run_dir / "04_r2_method_guard_green_and_entrypoint_scans.json", f04)

        # Load read-only canonical DEV dataset
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

        # Stage 2: P0-B Forensic Entry/Exit Failure Diagnosis (05..10)
        print("[KH-03.R3] Stage 2: P0-B Forensic Entry/Exit Failure Diagnosis (05..10)...", flush=True)
        forensic_gz = run_dir / "06_per_trade_entry_exit_fee_forensic_ledger.jsonl.gz"
        f06_meta, forensic_rows, null_sum = audit_r3_entry_exit_geometry_and_accounting(
            datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_1x=r2_cands_1x, out_ledger_gz_path=forensic_gz
        )
        f05 = build_r3_full_opportunity_lineage(
            datasets=datasets, r2_cands_1x=r2_cands_1x, null_replay_summary=null_sum, null_episodes=forensic_rows
        )
        f07, f08 = build_r3_win_loss_failure_attribution(forensic_rows=forensic_rows, ledger_meta_06=f06_meta)
        f09 = build_r3_cost_and_break_even_sensitivity(
            datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle, r2_cands_1x=r2_cands_1x
        )
        f10 = run_r3_negative_control_and_label_integrity_tests(
            datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_1x=r2_cands_1x, forensic_rows=forensic_rows
        )
        write_json_durable_atomic(run_dir / "05_full_opportunity_lineage_and_hash_manifest.json", f05)
        write_json_durable_atomic(run_dir / "07_win_loss_breakdown_denominator_ci_coverage.json", f07)
        write_json_durable_atomic(run_dir / "08_stop_target_gap_tie_holding_risk_tail_attribution.json", f08)
        write_json_durable_atomic(run_dir / "09_six_cost_scenarios_and_breakeven_accounting.json", f09)
        write_json_durable_atomic(run_dir / "10_negative_controls_matching_and_causal_disclaimers.json", f10)

        # Stage 3: P1-C Pre-Registered Bounded DEV Research E1/E2/E3 (11..16)
        print("[KH-03.R3] Stage 3: P1-C Frozen Pre-Reg (11), Conservation (12), R2 Forward-Only WF (13..14) & Strict DEV Gate (15..16)...", flush=True)
        f11 = freeze_r3_bounded_research_plan(tested_code_sha=start_head)
        write_json_durable_atomic(run_dir / "11_training_only_bounded_prereg_plan.json", f11)

        m5_ledger = build_r3_causal_m5_unswept_target_ledger(datasets=datasets, pivot_radius=2)
        r3_cands_1x = build_r3_train_only_candidates_asof(
            datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_bundle=r2_cands_1x, m5_ledger=m5_ledger, costs=CostAssumptions()
        )
        f12 = {
            "spec_version": SPEC_VERSION_KH03_R3, "schema_version": SCHEMA_VERSION_KH03_R3,
            "preregistered_plan_sha256": f11["plan_frozen_sha256"],
            "base_eligible_count": r3_cands_1x["base_eligible_count"],
            "m5_swings_tracked_asof": m5_ledger["total_m5_swings_tracked"],
            "e3_replaced_baseline_tp_with_nearer_m5_count": r3_cands_1x["e3_replaced_baseline_tp_with_nearer_m5_count"],
            "all_arms_conserved_exact": r3_cands_1x["all_arms_conserved_exact"],
            "arm_conservation": r3_cands_1x["arm_conservation"],
            "status": "PASS" if r3_cands_1x["all_arms_conserved_exact"] else "FAIL",
        }
        f12["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f12.items() if k != "artifact_canonical_sha256"})
        write_json_durable_atomic(run_dir / "12_candidate_asof_features_and_exact_conservation.json", f12)

        f13, f14 = run_r3_forward_only_dev_evaluation(
            datasets=datasets, mtf_bundle=mtf_bundle, r3_cands_1x=r3_cands_1x, prereg_plan_11=f11, tested_code_sha=start_head
        )
        f15, f16 = run_r3_paired_and_strict_dev_evaluation_15_16(
            datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle, m5_ledger=m5_ledger,
            r2_cands_1x=r2_cands_1x, r3_cands_1x=r3_cands_1x, prereg_plan_11=f11, wf_13=f13, wf_14=f14
        )
        write_json_durable_atomic(run_dir / "13_r2_forward_only_fold_boundaries_freeze_no_peek.json", f13)
        write_json_durable_atomic(run_dir / "14_validation_fold_independent_results_by_arm.json", f14)
        write_json_durable_atomic(run_dir / "15_fixed_fill_vs_dynamic_rerisk_and_expense_truth.json", f15)
        write_json_durable_atomic(run_dir / "16_strict_dev_gate_power_holm_cluster_bootstrap.json", f16)

        # Stage 4: 194 Unique RED/GREEN Fault Suite (174 inherited + 20 new R3) (21)
        print("[KH-03.R3] Stage 4: Running 194 unique RED/GREEN fault controls (174 inherited + 20 new R3) (21)...", flush=True)
        f21 = _run_kh03_r3_194_red_green_fault_suite(
            sentinel_tmp=sentinel_tmp, datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle,
            r2_cands_1x=r2_cands_1x, replay_bundle=replay_bundle, live_ff=live_ff,
            forensic_gz=forensic_gz, f07=f07, f13=f13, m5_ledger=m5_ledger,
        )
        write_json_durable_atomic(run_dir / "21_inherited_plus_new_unique_red_green_fault_receipts.json", f21)

        del datasets, mtf_bundle, ledger_bundle, r1_candidates, r2_cands_1x, r3_cands_1x, replay_bundle, live_ff, m5_ledger, forensic_rows
        gc.collect()

        # Stage 5: 16 NEW Separate-OS R3 Verifier Mutation Resistance Attacks (22)
        print("[KH-03.R3] Stage 5: Running 16 NEW separate-OS R3 verifier mutation attacks in temp clones (22)...", flush=True)
        f22 = _run_kh03_r3_verifier_mutation_suite_22(sentinel_tmp)
        write_json_durable_atomic(run_dir / "22_independent_verifier_new_mutation_resistance.json", f22)

        # Stage 6: Heavy Stages (18 full pytest, 19 W1/W2/W4/W1_repeat, 17 full 56,710 baseline parity)
        if can_run_heavy:
            eff_res_b = int(STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2 * (1024 ** 3))
            pytest_workers = min(3, eff_workers)
            print(f"[KH-03.R3] Stage 6A: Running FULL repo LPT-sharded pytest regression (max_workers={pytest_workers}) (18)...", flush=True)
            f18 = _run_kh03_r3_full_repo_pytest_sharded(
                run_dir, sentinel_tmp, run_id=run_id, tested_sha=start_head,
                effective_disk_reserve_bytes=eff_res_b, max_workers=pytest_workers,
            )
            write_json_durable_atomic(run_dir / "18_full_pytest_junit_collected_executed_bijection.json", f18)
            if git_clean:
                _git_cmd(["checkout", "--", "aurum-desk/backtest/trade_decision_lineage_reconciliation.json"])
            gc.collect()

            print("[KH-03.R3] Stage 6B: Running W1/W2/W4/W1_repeat pytest + R2 science determinism (19)...", flush=True)
            pb = asyncio.run(_run_controlled_pytest_w1_w2_w4_benchmark_r1(sentinel_tmp, enforce_r3_ram_guard=True))
            sb = asyncio.run(_run_r2_science_slice_w1_w2_w4_determinism(sentinel_tmp))
            gc.collect()

            print("[KH-03.R3] Stage 6C: Running FULL 56,710 A1 + 6,375 SI8 + 246 adapter + D4 parity (17)...", flush=True)
            ds_p, _ = load_r2_canonical_dataset_readonly(REPO_ROOT / CANONICAL_DATASET_REL_PATH)
            f02_r1 = audit_r2_reference_inputs_and_invariants(REPO_ROOT)
            f17 = _run_full_production_baseline_parity_20(
                datasets=ds_p, sentinel_tmp=sentinel_tmp, run_id=run_id, tested_sha=start_head,
                branch=branch, f01=f01, f02=f02_r1, max_workers=eff_workers,
            )
            f17["spec_version"] = SPEC_VERSION_KH03_R3
            write_json_durable_atomic(run_dir / "17_baseline_parity_A1_SI8_adapter_D4.json", f17)
            del ds_p
            gc.collect()
        else:
            f18 = {"spec_version": SPEC_VERSION_KH03_R3, "status": "NOT_RUN_BLOCKED_LOW_DISK", "collected_count": 0, "executed_count": 0, "passed_count": 0, "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0, "junit_bijection_exact": False}
            pb, sb = {"all_4_runs_identical": False, "runs": []}, {"all_4_runs_identical": False, "runs": []}
            f17 = {"spec_version": SPEC_VERSION_KH03_R3, "BASELINE_PARITY_VERDICT": "NOT_RUN_BLOCKED_LOW_DISK", "a1_population_count": 0, "a1_mismatches_count": 0, "si8_population_count": 0, "si8_mismatches_count": 0, "three_adapter_population_count": 0, "three_adapter_mismatches_count": 0, "d4_historical_closed_trades": 0, "per_decision_and_episode_fingerprints": {"all_sealed_fingerprints_exact_match": False}}
            write_json_durable_atomic(run_dir / "18_full_pytest_junit_collected_executed_bijection.json", f18)
            write_json_durable_atomic(run_dir / "17_baseline_parity_A1_SI8_adapter_D4.json", f17)

        cl_info = _cleanup_sentinel_temp_c4_win_safe(sentinel_tmp, expected_run_id=run_id)
        sentinel_cleaned = bool(cl_info["cleaned"])
        telem = sampler.stop_and_flush()
        sampler_stopped = True

        f19 = {
            "spec_version": SPEC_VERSION_KH03_R3, "schema_version": SCHEMA_VERSION_KH03_R3,
            "pytest_controlled_subset_w1_w2_w4_repeat": pb,
            "r2_science_slice_w1_w2_w4_repeat": sb,
            "measured_pytest_speedup_w2_vs_w1": pb.get("measured_speedup_w2_vs_w1"),
            "measured_pytest_speedup_w4_vs_w1": pb.get("measured_speedup_w4_vs_w1"),
            "measured_science_speedup_w2_vs_w1": sb.get("measured_speedup_w2_vs_w1"),
            "measured_science_speedup_w4_vs_w1": sb.get("measured_speedup_w4_vs_w1"),
            "pytest_optimum_worker_policy": _compute_optimum_worker_policy(pb),
            "science_optimum_worker_policy": _compute_optimum_worker_policy(sb),
            "all_determinism_identical": bool(pb.get("all_4_runs_identical") and sb.get("all_4_runs_identical")),
            "status": "NOT_RUN_BLOCKED_LOW_DISK" if not can_run_heavy else ("PASS" if (pb.get("all_4_runs_identical") and sb.get("all_4_runs_identical")) else "FAIL"),
        }
        write_json_durable_atomic(run_dir / "19_w1_w2_w4_w1repeat_hashes_timings_speedups.json", f19)

        f20 = {
            "spec_version": SPEC_VERSION_KH03_R3, "schema_version": SCHEMA_VERSION_KH03_R3,
            "orchestrator_pid": orch_pid, "sampling_interval_sec": 0.5,
            "memory_samples_log_rel": "logs/runtime_memory_samples_0p5s.jsonl.gz",
            **telem,
            "strict_preflight_disk_min_gib": STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
            "strict_runtime_disk_min_gib": STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2,
            "advisory_disk_gib": ADVISORY_DISK_GIB_KH02_R2,
            "owner_disk_preflight_waiver": False,
            "sentinel_temp_cleaned_up": sentinel_cleaned,
            "total_wall_clock_sec": round(time.perf_counter() - t_start, 3),
        }
        write_json_durable_atomic(run_dir / "20_disk_ram_pid_samples_and_cleanup.json", f20)

        _, end_head = _git_cmd(["rev-parse", "HEAD"])
        f00 = {
            "spec_version": SPEC_VERSION_KH03_R3, "schema_version": SCHEMA_VERSION_KH03_R3,
            "run_id": run_id, "started_utc": utc_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "branch": branch, "start_head_sha": start_head, "end_head_sha": end_head,
            "tested_executable_sha": start_head, "remote_head_sha": remote_sha,
            "prior_kh03_r2_pub_head_sha": PRIOR_KH03_R2_PUB_HEAD_SHA,
            "prior_kh03_r2_tested_sha": PRIOR_KH03_R2_TESTED_SHA,
            "prior_kh03_r2_evidence_sha": PRIOR_KH03_R2_EVIDENCE_SHA,
            "prior_kh03_r2_run_id": PRIOR_KH03_R2_RUN_ID,
            "git_clean_at_start": git_clean, "local_uncommitted_changes": not git_clean,
            "orchestrator_pid": orch_pid, "python_version": platform.python_version(),
            "platform": platform.platform(), "random_seed": DEFAULT_RANDOM_SEED_KH02_R2,
            "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
            "owner_disk_preflight_waiver": False, "strict_multi_mount_probe": strict_probe,
            "preflight_ram_available_mib": avail_ram_mib, "can_run_heavy_stages": can_run_heavy,
            "readonly_disk_inventory": build_r2_c1_readonly_disk_inventory(REPO_ROOT) if not strict_disk_ok else None,
            "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2), "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
            "status": "PASS" if (strict_disk_ok and ram_ok and git_clean) else ("BLOCKED_LOW_DISK" if not strict_disk_ok else "BLOCKED_PREFLIGHT"),
        }
        write_json_durable_atomic(run_dir / "00_source_branch_clean_sha_disk_ram_preflight.json", f00)

        pub_st = "PRE_PUSH_PENDING" if (strict_disk_ok and git_clean) else "NOT_PUBLISHED_BLOCKED_LOW_DISK"
        f25 = {
            "spec_version": SPEC_VERSION_KH03_R3, "schema_version": SCHEMA_VERSION_KH03_R3,
            "run_id": run_id, "branch": branch, "start_head_sha": start_head, "tested_executable_sha": start_head,
            "evidence_commit_sha": None, "publication_head_sha": None, "remote_readback_sha256_verified": False,
            "latest_run_pointer_updated": False, "owner_disk_preflight_waiver": False,
            "ci_status": "CI_UNKNOWN_NO_ACTIONS_RUN", "publication_status": pub_st,
        }
        write_json_durable_atomic(run_dir / "25_phase1_code_commit_push_readback.json", f25)

        docs_all = {
            "00": f00, "01": f01, "02": f02, "03": f03, "04": f04, "05": f05,
            "07": f07, "08": f08, "09": f09, "10": f10, "11": f11, "12": f12,
            "13": f13, "14": f14, "15": f15, "16": f16, "17": f17, "18": f18,
            "19": f19, "20": f20, "21": f21, "22": f22, "25": f25,
        }
        mem_disk_aud = _audit_memory_samples_c3(run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz")
        forensic_disk_aud = validate_r3_forensic_ledger_independently(forensic_gz, f07)
        recomp23 = recompute_kh03_r3_30_gates_and_verdicts(
            docs=docs_all, mem_audit=mem_disk_aud, forensic_ledger_audit=forensic_disk_aud,
            junit_bijection_ok=(f18.get("status") == "PASS" and bool(f18.get("junit_bijection_exact", False))),
            for_final_verifier_manifest=False,
        )
        f23 = {
            "spec_version": SPEC_VERSION_KH03_R3, "schema_version": SCHEMA_VERSION_KH03_R3,
            "run_id": run_id, "tested_executable_sha": start_head,
            "OOS_EVALUATIONS": 0, "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
            "overall_classification": recomp23["overall_classification"],
            "gates_30": recomp23["gates_30"], "verdicts": recomp23["verdicts"],
        }
        f23["artifact_canonical_sha256"] = canonical_hash(f23)
        write_json_durable_atomic(run_dir / "23_orthogonal_gate_matrix_economic_kh04_oos_activation.json", f23)

        readme_md, handover_vi = _render_kh03_r3_readme_and_handover(
            run_id=run_id, f00=f00, f04=f04, f07=f07, f08=f08, f16=f16, f17=f17,
            f18=f18, f19=f19, f20=f20, f21=f21, f22=f22, f23=f23,
        )
        (run_dir / "README_KH03_R3.md").write_text(readme_md, encoding="utf-8")
        (run_dir / "HANDOVER_KH03_R3_VI.txt").write_text(handover_vi, encoding="utf-8")

        chk_map = {
            fp.relative_to(run_dir).as_posix(): _sha256_file(fp)
            for fp in sorted(run_dir.rglob("*"))
            if fp.is_file() and not fp.name.endswith(".tmp") and fp.relative_to(run_dir).as_posix() != "checksums/sha256_inventory.json"
        }
        write_json_durable_atomic(run_dir / "checksums" / "sha256_inventory.json", {"run_id": run_id, "files_sha256": chk_map})

        # Stage 7: Separate-OS Independent Verifier sealing 24_independent_OS_verifier_manifest_sha256.json
        print("[KH-03.R3] Stage 7: Launching separate-OS-process independent disk verifier (24)...", flush=True)
        verifier_script = APP_ROOT / "tools" / "kh_03_r3_independent_verifier.py"
        env_v = os.environ.copy()
        env_v["PYTHONPATH"] = _pypath_str()
        v_proc = subprocess.run(
            [sys.executable, str(verifier_script), "--run-dir", str(run_dir), "--expected-run-id", run_id, "--expected-tested-sha", start_head],
            cwd=str(APP_ROOT), env=env_v, capture_output=True, text=True, timeout=240,
        )
        print(v_proc.stdout, flush=True)
        if v_proc.stderr:
            print(v_proc.stderr, file=sys.stderr, flush=True)

        f24 = json.loads((run_dir / "24_independent_OS_verifier_manifest_sha256.json").read_text("utf-8"))
        return int(v_proc.returncode), run_dir, {
            "run_id": run_id, "run_dir": str(run_dir), "verifier_exit_code": int(v_proc.returncode),
            "independent_verifier_status": f24.get("independent_verifier_status"),
            "overall_classification": f24.get("overall_classification"),
            "recomputed_verdicts": f24.get("recomputed_verdicts"), "violations": f24.get("violations"),
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
    ap = argparse.ArgumentParser(description="AURUM DESK KH-03.R3 Acceptance Orchestrator")
    ap.add_argument("--allow-local-dirty", action="store_true")
    ap.add_argument("--owner-waive-disk-preflight", action="store_true")
    ap.add_argument("--max-workers", type=int, default=GLOBAL_MAX_DESCENDANTS_KH02_R2)
    args = ap.parse_args(argv)
    if args.owner_waive_disk_preflight:
        return 2
    rc, _rdir, summary = run_kh03_r3_acceptance(
        allow_local_dirty=args.allow_local_dirty, owner_waive_disk_preflight=False, max_workers=args.max_workers
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
