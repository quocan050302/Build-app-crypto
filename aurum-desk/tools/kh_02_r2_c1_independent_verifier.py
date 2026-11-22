#!/usr/bin/env python3
"""
aurum-desk/tools/kh_02_r2_c1_independent_verifier.py

AURUM DESK — KH-02.R2.C1 Separate-OS-Process Independent Disk Verifier
SPEC VERSION: KH-02.R2.C1-v1.0

Executes in a DISTINCT OS process (`verifier_pid != orchestrator_pid`), reads EVERY
closed on-disk artifact (`00`..`15`, `17`, `README_KH02_R2_C1.md`, `HANDOVER_KH02_R2_C1_VI.txt`,
`logs/runtime_memory_samples_0p5s.jsonl.gz`, and `junit/*.xml` when full regression ran),
independently recomputes all 15 orthogonal verdicts (§8.3), checks ALL §8.2 tamper controls,
and seals `16_independent_disk_verifier_content_sha256_manifest.json`.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Mapping, Tuple

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_prospective_data_service import (
    ACTIVATION_READY_KH02_R2_SHARED as ACTIVATION_READY_KH02_R2,
    CANONICAL_DATASET_REL_PATH_KH02_R2 as CANONICAL_DATASET_REL_PATH,
    CANONICAL_DATASET_SHA256_KH02 as CANONICAL_DATASET_SHA256_KH02_R2,
    GLOBAL_MAX_DESCENDANTS_SHARED as GLOBAL_MAX_DESCENDANTS_KH02_R2,
    HARD_RAM_FLOOR_MIB_SHARED as HARD_RAM_FLOOR_MIB_KH02_R2,
    KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256,
    MANDATORY_BRANCH_KH02_R2_SHARED as MANDATORY_BRANCH_KH02_R2,
    PRIOR_KH02_R1_LATEST_PTR_REL_SHARED as PRIOR_KH02_R1_LATEST_PTR_REL,
    PRIOR_KH02_R1_LATEST_PTR_SHA256_SHARED as PRIOR_KH02_R1_LATEST_PTR_SHA256,
    PRIOR_KH02_R1_RUN_ID_SHARED as PRIOR_KH02_R1_RUN_ID,
    PRIOR_KH02_R1_TESTED_SHA_SHARED as PRIOR_KH02_R1_TESTED_SHA,
    PRIOR_KH02_R2_LATEST_PTR_REL_SHARED as PRIOR_KH02_R2_LATEST_PTR_REL,
    PRIOR_KH02_R2_LATEST_PTR_SHA256_SHARED as PRIOR_KH02_R2_LATEST_PTR_SHA256,
    PRIOR_KH02_R2_MANIFEST_22_REL_SHARED as PRIOR_KH02_R2_MANIFEST_22_REL,
    PRIOR_KH02_R2_MANIFEST_22_SHA256_SHARED as PRIOR_KH02_R2_MANIFEST_22_SHA256,
    PRIOR_KH02_R2_RUN_ID_SHARED as PRIOR_KH02_R2_RUN_ID,
    PRIOR_KH02_R2_TESTED_SHA_SHARED as PRIOR_KH02_R2_TESTED_SHA,
    PRIOR_R2_LATEST_PTR_REL_SHARED as PRIOR_R2_LATEST_PTR_REL,
    PRIOR_R2_LATEST_PTR_SHA256_SHARED as PRIOR_R2_LATEST_PTR_SHA256,
    PRIOR_R3_LATEST_PTR_REL_SHARED as PRIOR_R3_LATEST_PTR_REL,
    PRIOR_R3_LATEST_PTR_SHA256_SHARED as PRIOR_R3_LATEST_PTR_SHA256,
    R2_AUTHORITATIVE_ARTIFACT_14_REL,
    R2_AUTHORITATIVE_ARTIFACT_14_SHA256,
    R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS,
    R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
    R2_AUTHORITATIVE_FROZEN_CODE_SHA,
    R2_AUTHORITATIVE_FROZEN_CONFIG_SHA256,
    R2_NEW_FILES_ALLOWLIST_SHARED as R2_NEW_FILES_ALLOWLIST,
    SCHEMA_VERSION_KH02_R2_C1_ARTIFACT as SCHEMA_VERSION_KH02_R2_C1,
    SPEC_VERSION_KH02_R2_C1,
    STRICT_PREFLIGHT_DISK_MIN_GIB_SHARED as STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
    STRICT_RUNTIME_DISK_MIN_GIB_SHARED as STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tools.fresh_junit_io import write_json_durable_atomic

HARD_RAM_FLOOR_BYTES_C1 = int(HARD_RAM_FLOOR_MIB_KH02_R2 * 1024 * 1024)


def _audit_memory_samples_fast_stdlib(path: Path) -> Dict[str, Any]:
    """Fast stdlib gzip/JSONL memory telemetry auditor enforcing >= 512 MiB floor & <= 4 workers."""
    sample_count = 0
    below_floor = 0
    max_workers = 0
    min_avail_b: int | None = None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as gz:
            for line in gz:
                if not line.strip():
                    continue
                rec = json.loads(line)
                sample_count += 1
                ab = int(rec.get("available_ram_bytes", 0))
                if min_avail_b is None or ab < min_avail_b:
                    min_avail_b = ab
                if ab < HARD_RAM_FLOOR_BYTES_C1:
                    below_floor += 1
                wp = rec.get("worker_pids") or []
                cc = int(rec.get("child_count", len(wp)))
                max_workers = max(max_workers, len(wp), cc)
    except Exception as exc:
        return {"status": f"FAIL_PARSE_ERROR:{exc}", "sample_count": sample_count, "below_floor_samples_count": 1, "max_workers_observed_in_samples": 99}
    if sample_count == 0:
        return {"status": "FAIL_EMPTY_LOG", "sample_count": 0, "below_floor_samples_count": 1, "max_workers_observed_in_samples": 0}
    status = "FAIL_BELOW_HARD_FLOOR" if below_floor > 0 else "PASS"
    return {
        "status": status,
        "sample_count": sample_count,
        "min_available_ram_bytes": min_avail_b or 0,
        "below_floor_samples_count": below_floor,
        "max_workers_observed_in_samples": max_workers,
    }

MANIFEST_16_NAME = "16_independent_disk_verifier_content_sha256_manifest.json"

REQUIRED_ARTIFACTS_KH02_R2_C1: Tuple[str, ...] = (
    "00_git_source_branch_preflight_sha.json",
    "01_function_ownership_callgraph_and_locked_diff.json",
    "02_disk_mount_preflight_and_policy.json",
    "03_disk_waiver_bypass_negative_tests.json",
    "04_prospective_oos_registry_and_source_provenance.json",
    "05_oos_ingestion_validation_fault_controls.json",
    "06_unswept_tp_and_future_mutation_regression.json",
    "07_entry_geometry_pairing_and_balance.json",
    "08_fixed_ids_vs_full_rerisk_cost_and_fill_time_risk.json",
    "09_walkforward_uncertainty_and_economic_verdict.json",
    "10_full_pytest_collection_junit_bijection_exit.json",
    "11_async_w1_w2_w4_repeat_measured_determinism.json",
    "12_ram_disk_pid_runtime_telemetry.json",
    "13_red_green_fault_injection_receipts.json",
    "14_production_baseline_immutable_parity_fingerprints.json",
    "15_orthogonal_gate_matrix.json",
    "17_publication_tested_sha_remote_readback_receipt.json",
    "README_KH02_R2_C1.md",
    "HANDOVER_KH02_R2_C1_VI.txt",
)

ORTHOGONAL_15_VERDICT_KEYS_KH02_R2_C1: Tuple[str, ...] = (
    "BRANCH_SOURCE_VERDICT",
    "STRICT_DISK_SAFETY_VERDICT",
    "RAM_PROCESS_SAFETY_VERDICT",
    "FULL_REGRESSION_VERDICT",
    "BASELINE_PARITY_VERDICT",
    "ENTRY_TARGET_INTEGRITY_VERDICT",
    "PAIRED_RESEARCH_VERDICT",
    "COST_RISK_RECONCILIATION_VERDICT",
    "DATA_PROVENANCE_VERDICT",
    "FUTURE_OOS_READINESS_VERDICT",
    "FRESH_OOS_VERDICT",
    "RESEARCH_ECONOMIC_VERDICT",
    "INDEPENDENT_VERIFIER_VERDICT",
    "PUBLICATION_VERDICT",
    "ACTIVATION_READY",
)

C1_SOURCE_FILES_ALLOWLIST: Tuple[str, ...] = (
    *R2_NEW_FILES_ALLOWLIST,
    "aurum-desk/tools/run_kh_02_r2_c1_strict_closure.py",
    "aurum-desk/tools/kh_02_r2_c1_independent_verifier.py",
    "aurum-desk/backend/tests/test_kh_02_r2_c1_strict_safety_and_oos.py",
)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def recompute_c1_15_orthogonal_verdicts_from_disk(
    run_dir: Path,
    *,
    docs: Mapping[str, Mapping[str, Any]],
    mem_audit: Mapping[str, Any],
    junit_bijection_ok: bool,
    verifier_violations_count: int = 0,
    for_final_verifier_manifest: bool = False,
) -> Dict[str, Any]:
    """
    Independently computes ALL 15 separate orthogonal verdicts (§8.3) strictly from closed
    on-disk artifacts (`00`..`14`, `17`) and audited telemetry. NEVER collapses failures or
    low-disk blocks into a fake PASS.
    """
    f00 = docs["00"]
    f01 = docs["01"]
    f02 = docs["02"]
    f03 = docs["03"]
    f04 = docs["04"]
    f05 = docs["05"]
    f06 = docs["06"]
    f07 = docs["07"]
    f08 = docs["08"]
    f09 = docs["09"]
    f10 = docs["10"]
    f11 = docs["11"]
    f12 = docs["12"]
    f13 = docs["13"]
    f14 = docs["14"]
    f17 = docs["17"]

    # 1. BRANCH_SOURCE_VERDICT
    branch_ok = f00.get("branch") == MANDATORY_BRANCH_KH02_R2
    locked_ok = (
        f01.get("status") == "PASS"
        and int(f01.get("locked_files_modified_count", 1)) == 0
        and len(f01.get("forbidden_order_import_hits") or []) == 0
        and bool(f01.get("cost_assumptions_defaults_intact", False))
    )
    sha_consistent = (
        len(str(f00.get("start_head_sha") or "")) == 40
        and str(f00.get("start_head_sha") or "") == str(f00.get("end_head_sha") or f00.get("start_head_sha") or "")
        and str(f00.get("tested_executable_sha") or "") != PRIOR_KH02_R1_TESTED_SHA
    )
    clean_git = bool(f00.get("git_clean_at_start", False)) and not bool(f00.get("local_uncommitted_c1_changes", False))
    if not (branch_ok and locked_ok and sha_consistent):
        v_branch = "FAIL"
    elif clean_git:
        v_branch = "PASS"
    else:
        v_branch = "BLOCKED_UNCOMMITTED_LOCAL_RUN"

    # 2. STRICT_DISK_SAFETY_VERDICT (NEVER PASS below 20.5 GiB preflight or 20.0 GiB runtime; FAIL on waiver!)
    mp02 = f02.get("strict_multi_mount_probe") or f02
    waiver_injected = (
        bool(f00.get("owner_disk_preflight_waiver", False))
        or bool(f02.get("owner_disk_preflight_waiver", False))
        or bool(mp02.get("owner_waiver_permitted", False))
        or bool(f12.get("owner_disk_preflight_waiver", False))
        or bool(f17.get("owner_disk_preflight_waiver", False))
    )
    distinct_mounts = list(mp02.get("distinct_mounts") or ())
    probe_errs = list(mp02.get("probe_errors") or ())
    preflight_all_mounts_ok = (
        len(distinct_mounts) > 0
        and len(probe_errs) == 0
        and all(
            float(m.get("free_gib", 0.0)) >= STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2
            and float(m.get("projected_post_reserve_gib", 0.0)) >= STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2
            for m in distinct_mounts
        )
        and bool(mp02.get("can_run_heavy_stages", False))
    )
    neg03_ok = f03.get("status") == "PASS" and int(f03.get("failed_controls", 1)) == 0
    runtime_min_disk = float(f12.get("min_disk_free_gib") or 0.0)
    runtime_disk_ok = runtime_min_disk >= STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2
    if waiver_injected or len(probe_errs) > 0 or not neg03_ok:
        v_disk = "FAIL"
    elif preflight_all_mounts_ok and runtime_disk_ok:
        v_disk = "PASS"
    elif preflight_all_mounts_ok and not runtime_disk_ok:
        v_disk = "FAIL"
    else:
        v_disk = "BLOCKED_LOW_DISK"

    # 3. RAM_PROCESS_SAFETY_VERDICT
    min_ram = float(f12.get("min_available_ram_mib") or 0.0)
    max_ch = int(f12.get("max_descendant_children", 99))
    orphans = int(f12.get("orphan_children_after_cleanup", 99))
    sentinel_cleaned = bool(f12.get("sentinel_temp_cleaned_up", False))
    mem_disk_ok = (
        mem_audit.get("status") == "PASS"
        and int(mem_audit.get("below_floor_samples_count", 1)) == 0
        and int(mem_audit.get("max_workers_observed_in_samples", 99)) <= GLOBAL_MAX_DESCENDANTS_KH02_R2
    )
    ram_ok = (
        min_ram >= HARD_RAM_FLOOR_MIB_KH02_R2
        and max_ch <= GLOBAL_MAX_DESCENDANTS_KH02_R2
        and orphans == 0
        and sentinel_cleaned
        and mem_disk_ok
    )
    v_ram = "PASS" if ram_ok else "FAIL"

    # 4. FULL_REGRESSION_VERDICT
    reg_st = str(f10.get("status") or "FAIL")
    if reg_st == "NOT_RUN_BLOCKED_LOW_DISK":
        v_reg = "NOT_RUN_BLOCKED_LOW_DISK" if v_disk == "BLOCKED_LOW_DISK" else "FAIL"
    elif (
        reg_st == "PASS"
        and v_disk == "PASS"
        and junit_bijection_ok
        and int(f10.get("collected_count", 0)) > 0
        and int(f10.get("collected_count", -1)) == int(f10.get("executed_count", -2))
        and int(f10.get("failed_count", 1)) == 0
        and int(f10.get("error_count", 1)) == 0
        and int(f10.get("mandatory_skipped_count", 0)) == 0
    ):
        v_reg = "PASS"
    else:
        v_reg = "FAIL"

    # 5. BASELINE_PARITY_VERDICT
    par_st = str(f14.get("BASELINE_PARITY_VERDICT") or "FAIL")
    imm_ok = bool(f14.get("prior_evidence_immutability_verified", False)) and bool(f14.get("canonical_dataset_sha256_match", False))
    if par_st == "NOT_RUN_BLOCKED_LOW_DISK":
        v_par = "NOT_RUN_BLOCKED_LOW_DISK" if (v_disk == "BLOCKED_LOW_DISK" and imm_ok) else "FAIL"
    elif (
        par_st == "PASS"
        and v_disk == "PASS"
        and imm_ok
        and int(f14.get("a1_mismatches_count", 1)) == 0
        and int(f14.get("si8_mismatches_count", 1)) == 0
        and int(f14.get("three_adapter_mismatches_count", 1)) == 0
        and int(f14.get("d4_historical_closed_trades", 1)) == 0
    ):
        v_par = "PASS"
    else:
        v_par = "FAIL"

    # 6. ENTRY_TARGET_INTEGRITY_VERDICT
    g06_ok = (
        f06.get("status") == "PASS"
        and int(f06.get("swept_tp_accepted_count", 1)) == 0
        and bool(f06.get("t01_to_t10_all_passed", False))
        and bool(f06.get("future_mutation_invariance_passed", False))
        and bool(f06.get("m1_between_htf_sweep_detected", False))
    )
    v_tp = "PASS" if g06_ok else "FAIL"

    # 7. PAIRED_RESEARCH_VERDICT
    g07_ok = (
        f07.get("status") == "PASS"
        and str(f07.get("b0_vs_b2_identifiability_status") or "") == "COMPARISON_NOT_IDENTIFIABLE"
        and bool(f07.get("matching_inadequate_guard_verified", False))
    )
    v_pair = str(f07.get("PAIRED_RESEARCH_VERDICT") or ("IDENTIFIABLE_WITH_PARTIAL_COMMON_SUPPORT" if g07_ok else "FAIL"))
    if not g07_ok:
        v_pair = "FAIL"

    # 8. COST_RISK_RECONCILIATION_VERDICT
    g08_ok = (
        f08.get("status") == "PASS"
        and bool(f08.get("fixed_fill_zero_dropped_ids_verified", False))
        and bool(f08.get("full_rerisk_starts_from_full_universe", False))
        and int(f08.get("fill_time_cap_violations", 1)) == 0
        and bool(f08.get("realized_tail_risk_disclosed", False))
        and str(f08.get("actual_account_effective_fee_tier") or "") == "UNKNOWN_NOT_LOCALLY_VERIFIED"
    )
    v_cost = "PASS" if g08_ok else "FAIL"

    # 9. DATA_PROVENANCE_VERDICT
    reused_lim = f04.get("reused_dataset_limitations") or {}
    freeze_aud = f04.get("authoritative_r2_freeze_audit") or {}
    g04_prov_ok = (
        freeze_aud.get("status") == "PASS"
        and reused_lim.get("quote_mode") == "MODELED_BID_ASK"
        and reused_lim.get("observed_l1_bbo_available") is False
        and reused_lim.get("actual_account_effective_fee_tier") == "UNKNOWN_NOT_LOCALLY_VERIFIED"
        and f05.get("status") == "PASS"
        and int(f05.get("failed_cases", 1)) == 0
        and f05.get("validates_real_market_data") is False
    )
    v_prov = "PASS" if g04_prov_ok else "FAIL"

    # 10 & 11. FUTURE_OOS_READINESS_VERDICT & FRESH_OOS_VERDICT
    oos_evals = int(f04.get("OOS_EVALUATIONS", -1))
    prop_cnt = int(f04.get("proposed_snapshots_count", -1))
    acc_cnt = int(f04.get("accepted_prospective_snapshots_count", -1))
    if oos_evals != 0 or freeze_aud.get("status") != "PASS" or f04.get("escrow_contract_integrity_status") != "PASS":
        v_future_oos = "FAIL"
        v_fresh_oos = "FAIL"
    elif prop_cnt == 0 and acc_cnt == 0:
        v_future_oos = "BLOCKED_NO_VERIFIED_NEW_DATA"
        v_fresh_oos = "BLOCKED_NO_UNTOUCHED_DATA"
    else:
        v_future_oos = str(f04.get("FUTURE_OOS_READINESS_VERDICT") or "FAIL")
        v_fresh_oos = str(f04.get("FRESH_OOS_VERDICT") or "FAIL")

    # 12. RESEARCH_ECONOMIC_VERDICT
    raw_econ = str(f09.get("RESEARCH_ECONOMIC_VERDICT") or "NO_PROVEN_EDGE")
    if raw_econ != "NO_PROVEN_EDGE":
        v_econ = "FAIL_FORGED_PROVEN_EDGE_ON_REUSED_DATA"
    else:
        v_econ = "NO_PROVEN_EDGE"

    # 13. INDEPENDENT_VERIFIER_VERDICT
    if for_final_verifier_manifest:
        v_verif = "PASS" if verifier_violations_count == 0 else "FAIL"
    else:
        v_verif = "PENDING_SEPARATE_OS_VERIFIER"

    # 14. PUBLICATION_VERDICT
    pub_st = str(f17.get("publication_status") or "NOT_PUBLISHED_BLOCKED_LOW_DISK")
    if waiver_injected or "WAIVER" in pub_st:
        v_pub = "FAIL"
    elif v_disk == "BLOCKED_LOW_DISK":
        v_pub = "NOT_PUBLISHED_BLOCKED_LOW_DISK" if pub_st == "NOT_PUBLISHED_BLOCKED_LOW_DISK" else "FAIL"
    elif pub_st == "PASS_REMOTE_VERIFIED" and v_disk == "PASS" and v_reg == "PASS" and v_par == "PASS" and v_branch == "PASS":
        v_pub = "PASS_REMOTE_VERIFIED"
    elif pub_st == "PRE_PUSH_PENDING" and v_disk == "PASS" and v_reg == "PASS" and v_par == "PASS" and v_branch == "PASS":
        v_pub = "PRE_PUSH_PENDING"
    else:
        v_pub = "FAIL"

    # 15. ACTIVATION_READY (ALWAYS "NO")
    act_vals = {
        str(f00.get("ACTIVATION_READY") or ""),
        str(f01.get("ACTIVATION_READY") or ""),
        str(f04.get("ACTIVATION_READY") or ""),
        str(f09.get("ACTIVATION_READY") or ""),
    }
    v_act = "NO" if act_vals == {"NO"} else "FAIL_UNSAFE_ACTIVATION_READY"

    # Fault suite check (13)
    faults_ok = (
        f13.get("status") == "PASS"
        and int(f13.get("total_controls", 0)) >= 50
        and int(f13.get("failed_controls", 1)) == 0
    )

    # Async W1/W2/W4 check (11)
    async_st = str(f11.get("status") or "FAIL")
    async_ok = (
        (async_st == "NOT_RUN_BLOCKED_LOW_DISK" and v_disk == "BLOCKED_LOW_DISK")
        or (async_st == "PASS" and v_disk == "PASS")
    )

    verdicts = {
        "BRANCH_SOURCE_VERDICT": v_branch,
        "STRICT_DISK_SAFETY_VERDICT": v_disk,
        "RAM_PROCESS_SAFETY_VERDICT": v_ram,
        "FULL_REGRESSION_VERDICT": v_reg,
        "BASELINE_PARITY_VERDICT": v_par,
        "ENTRY_TARGET_INTEGRITY_VERDICT": v_tp,
        "PAIRED_RESEARCH_VERDICT": v_pair,
        "COST_RISK_RECONCILIATION_VERDICT": v_cost,
        "DATA_PROVENANCE_VERDICT": v_prov,
        "FUTURE_OOS_READINESS_VERDICT": v_future_oos,
        "FRESH_OOS_VERDICT": v_fresh_oos,
        "RESEARCH_ECONOMIC_VERDICT": v_econ,
        "INDEPENDENT_VERIFIER_VERDICT": v_verif,
        "PUBLICATION_VERDICT": v_pub,
        "ACTIVATION_READY": v_act,
    }
    return {
        "orthogonal_15_verdicts": verdicts,
        "fault_suite_ok": faults_ok,
        "async_determinism_status_ok": async_ok,
        "OOS_EVALUATIONS": oos_evals,
    }


def verify_kh02_r2_c1_artifacts_independently_from_disk(
    run_dir: Path,
    *,
    expected_run_id: str = "",
    expected_tested_sha: str = "",
    check_live_repo_pointers: bool = True,
    enforce_distinct_pid: bool = True,
    write_manifest: bool = True,
) -> Dict[str, Any]:
    """
    Separate-OS-process independent disk verifier for KH-02.R2.C1 (§8.1, §8.2, §8.3).
    """
    run_dir = Path(run_dir).resolve()
    verifier_pid = os.getpid()
    violations: List[str] = []

    if not run_dir.is_dir():
        return {
            "spec_version": SPEC_VERSION_KH02_R2_C1,
            "schema_version": SCHEMA_VERSION_KH02_R2_C1,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": [f"RUN_DIR_MISSING:{run_dir}"],
        }

    # 1. Presence & non-emptiness of every required artifact
    for rel_req in REQUIRED_ARTIFACTS_KH02_R2_C1:
        fp = run_dir / rel_req
        if not fp.is_file() or fp.stat().st_size == 0:
            violations.append(f"MISSING_OR_EMPTY_ARTIFACT:{rel_req}")
    if violations:
        return {
            "spec_version": SPEC_VERSION_KH02_R2_C1,
            "schema_version": SCHEMA_VERSION_KH02_R2_C1,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": violations,
        }

    # 2. Load JSON & text artifacts directly from disk
    try:
        docs: Dict[str, Dict[str, Any]] = {
            "00": json.loads((run_dir / "00_git_source_branch_preflight_sha.json").read_text("utf-8")),
            "01": json.loads((run_dir / "01_function_ownership_callgraph_and_locked_diff.json").read_text("utf-8")),
            "02": json.loads((run_dir / "02_disk_mount_preflight_and_policy.json").read_text("utf-8")),
            "03": json.loads((run_dir / "03_disk_waiver_bypass_negative_tests.json").read_text("utf-8")),
            "04": json.loads((run_dir / "04_prospective_oos_registry_and_source_provenance.json").read_text("utf-8")),
            "05": json.loads((run_dir / "05_oos_ingestion_validation_fault_controls.json").read_text("utf-8")),
            "06": json.loads((run_dir / "06_unswept_tp_and_future_mutation_regression.json").read_text("utf-8")),
            "07": json.loads((run_dir / "07_entry_geometry_pairing_and_balance.json").read_text("utf-8")),
            "08": json.loads((run_dir / "08_fixed_ids_vs_full_rerisk_cost_and_fill_time_risk.json").read_text("utf-8")),
            "09": json.loads((run_dir / "09_walkforward_uncertainty_and_economic_verdict.json").read_text("utf-8")),
            "10": json.loads((run_dir / "10_full_pytest_collection_junit_bijection_exit.json").read_text("utf-8")),
            "11": json.loads((run_dir / "11_async_w1_w2_w4_repeat_measured_determinism.json").read_text("utf-8")),
            "12": json.loads((run_dir / "12_ram_disk_pid_runtime_telemetry.json").read_text("utf-8")),
            "13": json.loads((run_dir / "13_red_green_fault_injection_receipts.json").read_text("utf-8")),
            "14": json.loads((run_dir / "14_production_baseline_immutable_parity_fingerprints.json").read_text("utf-8")),
            "15": json.loads((run_dir / "15_orthogonal_gate_matrix.json").read_text("utf-8")),
            "17": json.loads((run_dir / "17_publication_tested_sha_remote_readback_receipt.json").read_text("utf-8")),
        }
        readme_txt = (run_dir / "README_KH02_R2_C1.md").read_text("utf-8")
        handover_txt = (run_dir / "HANDOVER_KH02_R2_C1_VI.txt").read_text("utf-8")
    except Exception as exc:
        return {
            "spec_version": SPEC_VERSION_KH02_R2_C1,
            "schema_version": SCHEMA_VERSION_KH02_R2_C1,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": [f"ARTIFACT_PARSE_ERROR:{exc}"],
        }

    f00, f01, f02, f03 = docs["00"], docs["01"], docs["02"], docs["03"]
    f04, f05, f06, f07 = docs["04"], docs["05"], docs["06"], docs["07"]
    f08, f09, f10, f11 = docs["08"], docs["09"], docs["10"], docs["11"]
    f12, f13, f14, f15, f17 = docs["12"], docs["13"], docs["14"], docs["15"], docs["17"]

    # 3. Distinct OS PID, branch, run_id, and tested SHA lineage (§8.1, §8.2)
    orch_pid = int(f00.get("orchestrator_pid") or -1)
    if enforce_distinct_pid and verifier_pid == orch_pid:
        violations.append(f"VERIFIER_SAME_OS_PID_AS_ORCHESTRATOR:{verifier_pid}")

    run_id = expected_run_id or str(f00.get("run_id") or "")
    tested_sha = expected_tested_sha or str(f00.get("tested_executable_sha") or "")
    if f00.get("branch") != MANDATORY_BRANCH_KH02_R2:
        violations.append(f"WRONG_BRANCH:{f00.get('branch')}")
    if tested_sha != f00.get("tested_executable_sha") or tested_sha != f15.get("tested_executable_sha") or tested_sha != f17.get("tested_executable_sha"):
        violations.append("TESTED_EXECUTABLE_SHA_MISMATCH_ACROSS_00_15_17")
    if f00.get("end_head_sha") and f00.get("start_head_sha") != f00.get("end_head_sha"):
        violations.append(f"CODE_CHANGED_DURING_TESTS:start={f00.get('start_head_sha')},end={f00.get('end_head_sha')}")
    if tested_sha in (PRIOR_KH02_R1_TESTED_SHA, PRIOR_KH02_R2_TESTED_SHA):
        violations.append(f"STALE_R1_OR_R2_TESTED_SHA_REUSED:{tested_sha}")
    if run_id in (PRIOR_KH02_R1_RUN_ID, PRIOR_KH02_R2_RUN_ID):
        violations.append(f"STALE_R1_OR_R2_RUN_ID_REUSED:{run_id}")
    if len(tested_sha) >= 12 and not run_id.endswith(f"_{tested_sha[:12]}"):
        violations.append(f"RUN_ID_MISSING_TESTED_SHA12_SUFFIX:{run_id}")

    # 4. Unapproved disk waiver & insufficient disk mount checks (§8.2)
    mp02 = f02.get("strict_multi_mount_probe") or f02
    if (
        bool(f00.get("owner_disk_preflight_waiver", False))
        or bool(f02.get("owner_disk_preflight_waiver", False))
        or bool(mp02.get("owner_waiver_permitted", False))
        or bool(f12.get("owner_disk_preflight_waiver", False))
        or bool(f17.get("owner_disk_preflight_waiver", False))
    ):
        violations.append("UNAPPROVED_OWNER_DISK_WAIVER_FORBIDDEN")

    stored_verdicts = dict(f15.get("orthogonal_15_verdicts") or {})
    if any("WAIVE" in str(v).upper() for v in stored_verdicts.values()):
        violations.append("FORBIDDEN_WAIVER_VERDICT_IN_15")

    distinct_mounts = list(mp02.get("distinct_mounts") or ())
    if not distinct_mounts:
        violations.append("MISSING_DISTINCT_DISK_MOUNT_MEASUREMENTS_IN_02")
    else:
        min_preflight_gib = min(float(m.get("free_gib", 0.0)) for m in distinct_mounts)
        if min_preflight_gib < STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2 and stored_verdicts.get("STRICT_DISK_SAFETY_VERDICT") == "PASS":
            violations.append(f"FORGED_STRICT_DISK_PASS_BELOW_20P5_GIB:min={min_preflight_gib}")
        if min_preflight_gib < STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2 and (
            f10.get("status") == "PASS" or f11.get("status") == "PASS" or f14.get("BASELINE_PARITY_VERDICT") == "PASS"
        ):
            violations.append("HEAVY_STAGE_CLAIMED_PASS_UNDER_LOW_DISK")

    if f03.get("status") != "PASS" or int(f03.get("failed_controls", 1)) != 0:
        violations.append("DISK_WAIVER_NEGATIVE_CONTROLS_03_FAILED")

    # 5. Safety modes, activation lock, locked diff, forbidden imports
    if int(f01.get("locked_files_modified_count", 1)) != 0:
        violations.append(f"LOCKED_PRODUCTION_OR_PRIOR_FILES_MODIFIED:{f01.get('locked_files_modified_count')}")
    if len(f01.get("forbidden_order_import_hits") or []) > 0:
        violations.append("FORBIDDEN_ORDER_SERVICE_IMPORT_PRESENT")
    for art_lbl, doc_x in (("00", f00), ("01", f01), ("04", f04), ("09", f09), ("15", f15)):
        if doc_x.get("ACTIVATION_READY") != ACTIVATION_READY_KH02_R2:
            violations.append(f"UNSAFE_ACTIVATION_READY_IN_{art_lbl}:{doc_x.get('ACTIVATION_READY')}")
        rsm = doc_x.get("runtime_safety_modes")
        if rsm is not None:
            for mk in ("PRODUCTION", "LIVE", "PAPER", "AUTO"):
                if rsm.get(mk) != "OFF":
                    violations.append(f"UNSAFE_RUNTIME_MODE_IN_{art_lbl}:{mk}={rsm.get(mk)}")

    # 6. Outcome ledger order & duplicate candidate IDs (§8.2)
    for art_lbl, doc_x in (("06", f06), ("07", f07), ("08", f08)):
        ledger_rows = doc_x.get("outcome_ledger_sample")
        if ledger_rows is not None:
            if not isinstance(ledger_rows, list):
                violations.append(f"MALFORMED_OUTCOME_LEDGER_IN_{art_lbl}")
            else:
                seen_cids = set()
                prev_ts = -1
                for r_i, row in enumerate(ledger_rows):
                    if not isinstance(row, Mapping) or "candidate_id" not in row or "decision_timestamp_ms" not in row:
                        violations.append(f"MALFORMED_OUTCOME_LEDGER_ROW_IN_{art_lbl}:idx={r_i}")
                        break
                    cid = str(row["candidate_id"])
                    ts_m = int(row["decision_timestamp_ms"])
                    if cid in seen_cids:
                        violations.append(f"DUPLICATE_CANDIDATE_ID_IN_{art_lbl}:{cid}")
                        break
                    seen_cids.add(cid)
                    if ts_m < prev_ts:
                        violations.append(f"REORDERED_OUTCOME_LEDGER_IN_{art_lbl}:idx={r_i}:{ts_m}<{prev_ts}")
                        break
                    prev_ts = ts_m

    # 7. Swept TP labeled unswept check (§8.2)
    if int(f06.get("swept_tp_accepted_count", 1)) != 0 or not bool(f06.get("t01_to_t10_all_passed", False)):
        violations.append("SWEPT_TP_ACCEPTED_OR_T01_T10_FAILED")

    # 8. OOS provenance, authoritative R2 freeze/embargo, synthetic quote & old data relabeling (§8.2)
    freeze_aud = f04.get("authoritative_r2_freeze_audit") or {}
    exp_fc = freeze_aud.get("observed_freeze_coordinates") or {}
    if (
        freeze_aud.get("status") != "PASS"
        or exp_fc.get("frozen_candidate_code_sha") != R2_AUTHORITATIVE_FROZEN_CODE_SHA
        or exp_fc.get("frozen_config_sha256") != R2_AUTHORITATIVE_FROZEN_CONFIG_SHA256
        or int(exp_fc.get("freeze_timestamp_ms") or 0) != R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS
        or int(exp_fc.get("embargo_cutoff_ms") or 0) != R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS
    ):
        violations.append("AUTHORITATIVE_R2_FREEZE_OR_EMBARGO_TAMPERED")

    if int(f04.get("OOS_EVALUATIONS", -1)) != 0 or int(f15.get("OOS_EVALUATIONS", -1)) != 0:
        violations.append("NONZERO_OOS_EVALUATIONS_PEEK_DETECTED")

    reused_lim = f04.get("reused_dataset_limitations") or {}
    if reused_lim.get("quote_mode") != "MODELED_BID_ASK" or reused_lim.get("observed_l1_bbo_available") is not False:
        violations.append("SYNTHETIC_OR_MODELED_QUOTE_MISLABELED_AS_OBSERVED")
    if reused_lim.get("actual_account_effective_fee_tier") != "UNKNOWN_NOT_LOCALLY_VERIFIED":
        violations.append("UNVERIFIED_FEE_TIER_MISLABELED_AS_VERIFIED_IN_04")

    dev_shas = {CANONICAL_DATASET_SHA256_KH02_R2.lower(), KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256.lower()}
    for snap in (f04.get("accepted_prospective_snapshots") or ()):
        s_sha = str(snap.get("dataset_sha256") or "").lower()
        if s_sha in dev_shas:
            violations.append(f"OLD_DEV_DATA_RELABELED_AS_FRESH_OOS:{s_sha}")
        if int(snap.get("start_utc_ms") or 0) < R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS:
            violations.append(f"PRE_FREEZE_OR_EMBARGO_OOS_SNAPSHOT_ACCEPTED:{snap.get('start_utc_ms')}")
        if bool(snap.get("is_synthetic_fixture", False)):
            violations.append("SYNTHETIC_FIXTURE_ADMITTED_AS_REAL_OOS")

    if f05.get("status") != "PASS" or int(f05.get("failed_cases", 1)) != 0 or f05.get("validates_real_market_data") is not False:
        violations.append("OOS_INGESTION_VALIDATOR_CONTROLS_05_FAILED_OR_MISLABELED")

    # 9. Cost & fill-time risk integrity (§8.2): fee 0.0006 mislabeled, dropped fixed-fill IDs, arithmetic
    if str(f08.get("actual_account_effective_fee_tier") or "") != "UNKNOWN_NOT_LOCALLY_VERIFIED":
        violations.append("FEE_0P0006_SILENTLY_SUBSTITUTED_AS_VERIFIED_ACCOUNT_TIER")
    if not bool(f08.get("fixed_fill_zero_dropped_ids_verified", False)):
        violations.append("DROPPED_IDS_IN_FIXED_FILL_COUNTERFACTUAL")
    if not bool(f08.get("full_rerisk_starts_from_full_universe", False)):
        violations.append("SUBSET_SUBSTITUTION_IN_FULL_RERISK")
    if int(f08.get("fill_time_cap_violations", 1)) != 0:
        violations.append("FILL_TIME_NET_RISK_CAP_VIOLATION_IN_08")
    for arm_k, row_c in (f08.get("fixed_fill_decimal_checks") or {}).items():
        g_d = Decimal(str(row_c.get("gross_pnl_usd", 0.0)))
        f_d = Decimal(str(row_c.get("fees_usd", 0.0)))
        n_d = Decimal(str(row_c.get("net_pnl_usd", 0.0)))
        if abs((g_d - f_d) - n_d) > Decimal("0.01"):
            violations.append(f"FIXED_FILL_DECIMAL_ARITHMETIC_MISMATCH:{arm_k}")

    # 10. Economic honesty (§8.2)
    if str(f09.get("RESEARCH_ECONOMIC_VERDICT") or "") != "NO_PROVEN_EDGE":
        violations.append(f"FORGED_ECONOMIC_VERDICT:{f09.get('RESEARCH_ECONOMIC_VERDICT')}")

    # 11. RAM / PID / orphan telemetry log on disk (§8.2)
    mem_log = run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    mem_audit: Dict[str, Any] = {}
    if not mem_log.is_file() or mem_log.stat().st_size == 0:
        violations.append("MISSING_MEMORY_TELEMETRY_LOG")
    else:
        mem_audit = _audit_memory_samples_fast_stdlib(mem_log)
        if mem_audit.get("status") != "PASS" or int(mem_audit.get("below_floor_samples_count", 1)) > 0:
            violations.append(f"MEMORY_TELEMETRY_BREACHED_512MIB_FLOOR:{mem_audit.get('status')}")
        if int(mem_audit.get("max_workers_observed_in_samples", 99)) > GLOBAL_MAX_DESCENDANTS_KH02_R2:
            violations.append(f"UNBOUNDED_CHILD_PROCESSES_DETECTED:{mem_audit.get('max_workers_observed_in_samples')}")
    if int(f12.get("orphan_children_after_cleanup", 99)) != 0:
        violations.append(f"SURVIVING_ORPHAN_PROCESSES:{f12.get('orphan_children_after_cleanup')}")
    if int(f12.get("max_descendant_children", 99)) > GLOBAL_MAX_DESCENDANTS_KH02_R2:
        violations.append(f"MAX_DESCENDANT_CHILDREN_EXCEEDED:{f12.get('max_descendant_children')}")
    if float(f12.get("min_available_ram_mib") or 0.0) < HARD_RAM_FLOOR_MIB_KH02_R2:
        violations.append(f"RAM_FLOOR_BREACHED_IN_12:{f12.get('min_available_ram_mib')}")

    # 12. JUnit XML bijection (when 10 claims PASS) OR zero placeholder JUnit XMLs (when blocked)
    junit_dir = run_dir / "junit"
    xmls = sorted(junit_dir.glob("*.xml")) if junit_dir.is_dir() else []
    junit_bijection_ok = False
    if f10.get("status") == "PASS":
        if not xmls:
            violations.append("FULL_REGRESSION_CLAIMS_PASS_WITH_ZERO_JUNIT_XML")
        else:
            cm10 = f10.get("collection_manifest") or {}
            sm10 = f10.get("shard_manifest") or {}
            if not cm10 or not sm10:
                violations.append("FULL_REGRESSION_MISSING_COLLECTION_OR_SHARD_MANIFEST")
            else:
                from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
                    verify_fresh_junit_bijection_v17931211,
                )
                bj = verify_fresh_junit_bijection_v17931211(
                    run_dir,
                    cm10,
                    sm10,
                    expected_run_id=run_id,
                    expected_tested_sha=tested_sha,
                    mandatory_node_prefixes=("backend/tests/test_kh_02_r2",),
                    require_shard_schedule=True,
                )
                if bj.get("status") != "PASS":
                    violations.append(f"INDEPENDENT_JUNIT_BIJECTION_FAILED:{bj.get('violations')}")
                else:
                    junit_bijection_ok = True
    elif f10.get("status") == "NOT_RUN_BLOCKED_LOW_DISK":
        if xmls:
            violations.append(f"PLACEHOLDER_JUNIT_XML_PRESENT_WHEN_BLOCKED:{len(xmls)}")
        if int(f10.get("collected_count", 0)) != 0 or int(f10.get("executed_count", 0)) != 0:
            violations.append("HISTORICAL_PYTEST_COUNTS_COPIED_WHILE_BLOCKED")

    # 13. Live repo immutability of prior KH-01B / KH-02.R1 / KH-02.R2 sealed artifacts & pointers (§8.2)
    prior_pointer_checks: Dict[str, Any] = {}
    if check_live_repo_pointers:
        from domain_calculator import CostAssumptions
        from services.kh_01b_r1_ram_safety_r3_service import detect_active_64mib_ram_floor_in_source
        ds_disk = REPO_ROOT / CANONICAL_DATASET_REL_PATH
        ds_sha = _file_sha256(ds_disk) if ds_disk.is_file() else ""
        if ds_sha != CANONICAL_DATASET_SHA256_KH02_R2:
            violations.append("CANONICAL_DATASET_SHA_MUTATED_ON_DISK")
        for rel_p, exp_sha, lbl in (
            (PRIOR_KH02_R1_LATEST_PTR_REL, PRIOR_KH02_R1_LATEST_PTR_SHA256, "KH02_R1_LATEST_PTR"),
            (PRIOR_KH02_R2_LATEST_PTR_REL, PRIOR_KH02_R2_LATEST_PTR_SHA256, "KH02_R2_LATEST_PTR"),
            (R2_AUTHORITATIVE_ARTIFACT_14_REL, R2_AUTHORITATIVE_ARTIFACT_14_SHA256, "KH02_R2_ARTIFACT_14"),
            (PRIOR_KH02_R2_MANIFEST_22_REL, PRIOR_KH02_R2_MANIFEST_22_SHA256, "KH02_R2_MANIFEST_22"),
            (PRIOR_R3_LATEST_PTR_REL, PRIOR_R3_LATEST_PTR_SHA256, "KH01B_R3_LATEST_PTR"),
            (PRIOR_R2_LATEST_PTR_REL, PRIOR_R2_LATEST_PTR_SHA256, "KH01B_R2_LATEST_PTR"),
        ):
            p = REPO_ROOT / rel_p
            act = _file_sha256(p) if p.is_file() else None
            ok = act == exp_sha
            prior_pointer_checks[lbl] = {"path": rel_p, "expected": exp_sha, "actual": act, "match": ok}
            if not ok:
                violations.append(f"PRIOR_ARTIFACT_OVERWRITTEN_OR_MUTATED:{lbl}")
        c_live = CostAssumptions()
        if (
            abs(float(c_live.taker_fee_rate) - 0.0004) > 1e-12
            or abs(float(c_live.maker_fee_rate) - 0.0002) > 1e-12
            or abs(float(c_live.slippage_usd) - 0.10) > 1e-12
        ):
            violations.append("LIVE_COST_ASSUMPTIONS_MUTATED")
        c1_extra_src = {
            rel: (REPO_ROOT / rel).read_text(encoding="utf-8")
            for rel in C1_SOURCE_FILES_ALLOWLIST
            if (REPO_ROOT / rel).is_file() and rel.endswith(".py") and "/tests/" not in rel
        }
        hits_64 = detect_active_64mib_ram_floor_in_source(REPO_ROOT, extra_text_by_name=c1_extra_src)
        if hits_64.get("status") != "PASS" or int(hits_64.get("violations_count", 0)) > 0:
            violations.append(f"ACTIVE_64MIB_RAM_FLOOR_FOUND:{hits_64.get('violations_count')}")
    else:
        # Even in synthetic tamper tests, check recorded immutable pointer map inside `14`
        rec_ptrs = f14.get("prior_sealed_artifacts_immutability") or {}
        for lbl, rec in rec_ptrs.items():
            if not bool(rec.get("match", False)):
                violations.append(f"PRIOR_ARTIFACT_OVERWRITTEN_OR_MUTATED:{lbl}")

    # 14. Recompute all 15 orthogonal verdicts from disk and compare against `15`
    recomp = recompute_c1_15_orthogonal_verdicts_from_disk(
        run_dir,
        docs=docs,
        mem_audit=mem_audit,
        junit_bijection_ok=junit_bijection_ok,
        for_final_verifier_manifest=False,
    )
    recomp_verdicts = recomp["orthogonal_15_verdicts"]
    if not recomp["fault_suite_ok"]:
        violations.append("RED_GREEN_FAULT_SUITE_13_FAILED_OR_INCOMPLETE")
    if not recomp["async_determinism_status_ok"]:
        violations.append("ASYNC_DETERMINISM_11_INCONSISTENT_WITH_DISK_SAFETY")
    for vk in ORTHOGONAL_15_VERDICT_KEYS_KH02_R2_C1:
        if vk == "INDEPENDENT_VERIFIER_VERDICT":
            continue
        if stored_verdicts.get(vk) != recomp_verdicts.get(vk):
            violations.append(f"VERDICT_TAMPER_OR_DRIFT:{vk}:stored={stored_verdicts.get(vk)}!=recomputed={recomp_verdicts.get(vk)}")

    # 15. Cross-view consistency in README_KH02_R2_C1.md and HANDOVER_KH02_R2_C1_VI.txt
    if run_id not in readme_txt or run_id not in handover_txt:
        violations.append("CROSS_VIEW_RUN_ID_MISSING_IN_README_OR_HANDOVER")
    if "ACTIVATION_READY=NO" not in readme_txt or "ACTIVATION_READY=NO" not in handover_txt:
        violations.append("CROSS_VIEW_ACTIVATION_LOCK_MISSING")
    disk_v = recomp_verdicts["STRICT_DISK_SAFETY_VERDICT"]
    if disk_v not in readme_txt or disk_v not in handover_txt:
        violations.append(f"CROSS_VIEW_STRICT_DISK_VERDICT_MISSING:{disk_v}")
    if "NO_PROVEN_EDGE" not in readme_txt or "NO_PROVEN_EDGE" not in handover_txt:
        violations.append("CROSS_VIEW_ECONOMIC_VERDICT_MISSING")

    # 16. Hash every closed file in run_dir recursively (non-circular: exclude manifest 16)
    artifact_sha256_map: Dict[str, Dict[str, Any]] = {}
    for fp in sorted(run_dir.rglob("*")):
        if not fp.is_file():
            continue
        rel = fp.relative_to(run_dir).as_posix()
        if rel == MANIFEST_16_NAME or rel.endswith(".tmp"):
            continue
        artifact_sha256_map[rel] = {
            "size_bytes": int(fp.stat().st_size),
            "sha256": _file_sha256(fp),
        }

    final_status = "PASS" if len(violations) == 0 else "FAIL"
    final_verdicts = dict(recomp_verdicts)
    final_verdicts["INDEPENDENT_VERIFIER_VERDICT"] = final_status

    manifest_payload: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH02_R2_C1,
        "schema_version": SCHEMA_VERSION_KH02_R2_C1,
        "verified_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "orchestrator_pid": orch_pid,
        "verifier_pid": verifier_pid,
        "distinct_os_process_verified": (verifier_pid != orch_pid),
        "independent_verifier_status": final_status,
        "recomputed_15_orthogonal_verdicts": final_verdicts,
        "memory_telemetry_disk_audit": mem_audit,
        "prior_pointer_immutability_checks": prior_pointer_checks,
        "violations": violations,
        "artifact_count_hashed": len(artifact_sha256_map),
        "artifact_sha256_map": artifact_sha256_map,
    }
    manifest_payload["manifest_content_sha256"] = canonical_hash(
        {k: v for k, v in manifest_payload.items() if k != "manifest_content_sha256"}
    )

    if write_manifest:
        write_json_durable_atomic(run_dir / MANIFEST_16_NAME, manifest_payload)

    return manifest_payload


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="KH-02.R2.C1 Independent Separate-OS Disk Verifier")
    ap.add_argument("--run-dir", required=True, help="Path to KH-02.R2.C1 run directory")
    ap.add_argument("--expected-run-id", default="", help="Expected run_id")
    ap.add_argument("--expected-tested-sha", default="", help="Expected tested executable SHA")
    ap.add_argument("--skip-live-repo-pointers", action="store_true", help="Skip live repo pointer checks (isolated synthetic tamper tests)")
    ap.add_argument("--no-write-manifest", action="store_true", help="Do not write manifest 16")
    args = ap.parse_args(argv)

    res = verify_kh02_r2_c1_artifacts_independently_from_disk(
        Path(args.run_dir),
        expected_run_id=args.expected_run_id,
        expected_tested_sha=args.expected_tested_sha,
        check_live_repo_pointers=not args.skip_live_repo_pointers,
        enforce_distinct_pid=True,
        write_manifest=not args.no_write_manifest,
    )
    print(
        json.dumps(
            {
                "independent_verifier_status": res["independent_verifier_status"],
                "verifier_pid": res["verifier_pid"],
                "distinct_os_process_verified": res.get("distinct_os_process_verified"),
                "recomputed_15_orthogonal_verdicts": res.get("recomputed_15_orthogonal_verdicts"),
                "violations": res.get("violations"),
                "artifact_count_hashed": res.get("artifact_count_hashed"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if res["independent_verifier_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
