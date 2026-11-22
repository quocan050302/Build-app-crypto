#!/usr/bin/env python3
"""
aurum-desk/tools/kh_02_r2_c2_independent_verifier.py

AURUM DESK — KH-02.R2.C2 Separate-OS-Process Independent Disk Verifier
SPEC VERSION: KH-02.R2.C2-v1.0

Executes in a DISTINCT OS process (`verifier_pid != orchestrator_pid`), reads EVERY
closed on-disk artifact (`00`..`16`, `18`, `19`, `README_KH02_R2_C2.md`,
`HANDOVER_KH02_R2_C2_VI.txt`, `logs/runtime_memory_samples_0p5s.jsonl.gz`, and `junit/*.xml`
when full regression ran), independently recomputes ALL 19 gates (`G01`..`G19`) and ALL 13
verdict dimensions (§9), verifies immutability of KH-01B/R1/R2/C1 sealed artifacts, and
seals `17_independent_os_verifier_sha256_manifest.json`.
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
    PRIOR_KH02_R2_C1_CODE_COMMIT_SHA_SHARED as PRIOR_KH02_R2_C1_CODE_COMMIT_SHA,
    PRIOR_KH02_R2_C1_MANIFEST_16_REL_SHARED as PRIOR_KH02_R2_C1_MANIFEST_16_REL,
    PRIOR_KH02_R2_C1_MANIFEST_16_SHA256_SHARED as PRIOR_KH02_R2_C1_MANIFEST_16_SHA256,
    PRIOR_KH02_R2_C1_RUN_ID_SHARED as PRIOR_KH02_R2_C1_RUN_ID,
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
    STRICT_PREFLIGHT_DISK_MIN_GIB_SHARED as STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
    STRICT_RUNTIME_DISK_MIN_GIB_SHARED as STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tools.fresh_junit_io import write_json_durable_atomic

SPEC_VERSION_KH02_R2_C2 = "KH-02.R2.C2-v1.0"
SCHEMA_VERSION_KH02_R2_C2 = "kh_02_r2_c2_acceptance_v1"
MANIFEST_17_NAME = "17_independent_os_verifier_sha256_manifest.json"
HARD_RAM_FLOOR_BYTES_C2 = int(HARD_RAM_FLOOR_MIB_KH02_R2 * 1024 * 1024)

REQUIRED_ARTIFACTS_KH02_R2_C2: Tuple[str, ...] = (
    "00_source_branch_remote_diff_preflight.json",
    "01_function_ownership_keep_as_is_ast_diff.json",
    "02_distinct_write_mount_disk_resource_preflight.json",
    "03_disk_waiver_cli_api_and_runtime_abort_faults.json",
    "04_r2_freeze_and_prospective_schema_provenance.json",
    "05_escrow_append_only_concurrency_crash_recovery.json",
    "06_escrow_hash_chain_external_anchor_truth.json",
    "07_public_readonly_collector_fake_transport_suite.json",
    "08_causal_unswept_target_future_mutation_risk.json",
    "09_paired_entry_geometry_common_support_balance.json",
    "10_fixed_fill_vs_rerisk_fees_1x2x3x.json",
    "11_walkforward_uncertainty_economic_oos_verdict.json",
    "12_full_pytest_junit_bijection.json",
    "13_async_w1_w2_w4_w1repeat_hashes_times.json",
    "14_full_a1_si8_adapter_d4_parity.json",
    "15_ram_disk_pid_temp_telemetry.json",
    "16_fault_injection_red_green_receipts.json",
    "18_orthogonal_gate_matrix.json",
    "19_publication_tested_sha_remote_blob_receipt.json",
    "README_KH02_R2_C2.md",
    "HANDOVER_KH02_R2_C2_VI.txt",
)

VERDICT_13_DIMENSIONS_KH02_R2_C2: Tuple[str, ...] = (
    "ENGINEERING",
    "RESOURCE",
    "REGRESSION",
    "PARITY",
    "DATA_PROVENANCE",
    "OOS_READINESS",
    "ENTRY_TARGET",
    "COST",
    "CONCURRENCY",
    "INDEPENDENT_VERIFIER",
    "PUBLICATION",
    "ECONOMIC",
    "ACTIVATION",
)

C2_SOURCE_FILES_ALLOWLIST: Tuple[str, ...] = (
    *R2_NEW_FILES_ALLOWLIST,
    "aurum-desk/tools/run_kh_02_r2_c1_strict_closure.py",
    "aurum-desk/tools/kh_02_r2_c1_independent_verifier.py",
    "aurum-desk/backend/tests/test_kh_02_r2_c1_strict_safety_and_oos.py",
    "aurum-desk/backend/services/kh_02_r2_c2_escrow_journal.py",
    "aurum-desk/backend/services/kh_02_r2_c2_public_capture.py",
    "aurum-desk/tools/run_kh_02_r2_c2_acceptance.py",
    "aurum-desk/tools/kh_02_r2_c2_independent_verifier.py",
    "aurum-desk/backend/tests/test_kh_02_r2_c2_escrow_journal.py",
    "aurum-desk/backend/tests/test_kh_02_r2_c2_public_capture.py",
    "aurum-desk/backend/tests/test_kh_02_r2_c2_acceptance_and_faults.py",
)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _audit_memory_samples_fast_stdlib(path: Path) -> Dict[str, Any]:
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
                if ab < HARD_RAM_FLOOR_BYTES_C2:
                    below_floor += 1
                wp = rec.get("worker_pids") or []
                cc = int(rec.get("child_count", len(wp)))
                max_workers = max(max_workers, len(wp), cc)
    except Exception as exc:
        return {"status": f"FAIL_PARSE_ERROR:{exc}", "sample_count": sample_count, "below_floor_samples_count": 1, "max_workers_observed_in_samples": 99}
    if sample_count == 0:
        return {"status": "FAIL_EMPTY_LOG", "sample_count": 0, "below_floor_samples_count": 1, "max_workers_observed_in_samples": 0}
    return {
        "status": "FAIL_BELOW_HARD_FLOOR" if below_floor > 0 else "PASS",
        "sample_count": sample_count,
        "min_available_ram_bytes": min_avail_b or 0,
        "below_floor_samples_count": below_floor,
        "max_workers_observed_in_samples": max_workers,
    }


def recompute_c2_19_gates_and_13_verdicts(
    *,
    docs: Mapping[str, Mapping[str, Any]],
    mem_audit: Mapping[str, Any],
    junit_bijection_ok: bool,
    verifier_violations_count: int = 0,
    for_final_verifier_manifest: bool = False,
) -> Dict[str, Any]:
    """Independently recomputes ALL 19 gates (G01..G19) and ALL 13 verdict dimensions (§9)."""
    f00, f01, f02, f03 = docs["00"], docs["01"], docs["02"], docs["03"]
    f04, f05, f06, f07 = docs["04"], docs["05"], docs["06"], docs["07"]
    f08, f09, f10, f11 = docs["08"], docs["09"], docs["10"], docs["11"]
    f12, f13, f14, f15 = docs["12"], docs["13"], docs["14"], docs["15"]
    f16, f19 = docs["16"], docs["19"]

    gates: Dict[str, str] = {}

    # G01 BRANCH_SOURCE
    branch_ok = f00.get("branch") == MANDATORY_BRANCH_KH02_R2
    sha_ok = (
        len(str(f00.get("start_head_sha") or "")) == 40
        and f00.get("start_head_sha") == f00.get("end_head_sha")
        and f00.get("tested_executable_sha") not in (PRIOR_KH02_R1_TESTED_SHA, PRIOR_KH02_R2_TESTED_SHA, PRIOR_KH02_R2_C1_CODE_COMMIT_SHA)
    )
    clean_ok = bool(f00.get("git_clean_at_start", False)) and not bool(f00.get("local_uncommitted_c2_changes", False))
    gates["G01_BRANCH_SOURCE"] = "PASS" if (branch_ok and sha_ok and clean_ok) else ("BLOCKED_UNCOMMITTED_LOCAL_RUN" if (branch_ok and sha_ok) else "FAIL")

    # G02 PRODUCTION_FREEZE
    g02_ok = (
        f01.get("status") == "PASS"
        and int(f01.get("locked_files_modified_count", 1)) == 0
        and len(f01.get("forbidden_order_import_hits") or []) == 0
        and bool(f01.get("cost_assumptions_defaults_intact", False))
        and bool(f14.get("prior_evidence_immutability_verified", False))
    )
    gates["G02_PRODUCTION_FREEZE"] = "PASS" if g02_ok else "FAIL"

    # G03 LIVE_MODES
    modes_ok = True
    for d in (f00, f01, f04, f11):
        if d.get("ACTIVATION_READY") != "NO":
            modes_ok = False
        rsm = d.get("runtime_safety_modes") or {}
        if any(rsm.get(k) != "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO")):
            modes_ok = False
    gates["G03_LIVE_MODES"] = "PASS" if modes_ok else "FAIL"

    # G04 STRICT_DISK
    mp02 = f02.get("strict_multi_mount_probe") or f02
    waiver_flag = (
        bool(f00.get("owner_disk_preflight_waiver", False))
        or bool(f02.get("owner_disk_preflight_waiver", False))
        or bool(mp02.get("owner_waiver_permitted", False))
        or bool(f15.get("owner_disk_preflight_waiver", False))
        or bool(f19.get("owner_disk_preflight_waiver", False))
    )
    mounts = list(mp02.get("distinct_mounts") or ())
    probe_errs = list(mp02.get("probe_errors") or ())
    preflight_ok = (
        len(mounts) > 0
        and len(probe_errs) == 0
        and all(
            float(m.get("free_gib", 0.0)) >= STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2
            and float(m.get("projected_post_reserve_gib", 0.0)) >= STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2
            for m in mounts
        )
        and bool(mp02.get("can_run_heavy_stages", False))
    )
    rt_disk = float(f15.get("min_disk_free_gib") or 0.0)
    rt_disk_ok = rt_disk >= STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2
    f03_ok = f03.get("status") == "PASS" and int(f03.get("failed_controls", 1)) == 0
    if waiver_flag or len(probe_errs) > 0 or not f03_ok:
        gates["G04_STRICT_DISK"] = "FAIL"
    elif preflight_ok and rt_disk_ok:
        gates["G04_STRICT_DISK"] = "PASS"
    elif preflight_ok and not rt_disk_ok:
        gates["G04_STRICT_DISK"] = "FAIL"
    else:
        gates["G04_STRICT_DISK"] = "BLOCKED_LOW_DISK"

    # G05 RAM_PID
    ram_ok = (
        float(f15.get("min_available_ram_mib") or 0.0) >= HARD_RAM_FLOOR_MIB_KH02_R2
        and int(f15.get("max_descendant_children", 99)) <= GLOBAL_MAX_DESCENDANTS_KH02_R2
        and int(f15.get("orphan_children_after_cleanup", 99)) == 0
        and bool(f15.get("sentinel_temp_cleaned_up", False))
        and mem_audit.get("status") == "PASS"
        and int(mem_audit.get("below_floor_samples_count", 1)) == 0
        and int(mem_audit.get("max_workers_observed_in_samples", 99)) <= GLOBAL_MAX_DESCENDANTS_KH02_R2
    )
    gates["G05_RAM_PID"] = "PASS" if ram_ok else "FAIL"

    # G06 JOURNAL (append-only, concurrency, crash recovery, anchor truth)
    g06_ok = (
        f05.get("status") == "PASS"
        and bool(f05.get("prior_journal_bytes_untouched_verified", False))
        and bool(f05.get("concurrent_writers_2_4_8_exact_once_passed", False))
        and bool(f05.get("crash_recovery_and_quarantine_passed", False))
        and f06.get("status") == "PASS"
        and f06.get("unanchored_immutability_class") == "LOCAL_TAMPER_EVIDENT_ONLY"
        and f06.get("unanchored_worm_verified") is False
        and bool(f06.get("anchored_rollback_detected", False))
    )
    gates["G06_JOURNAL"] = "PASS" if g06_ok else "FAIL"

    # G07 OOS_PROVENANCE
    frz = f04.get("authoritative_r2_freeze_audit") or {}
    lim = f04.get("reused_dataset_limitations") or {}
    g07_ok = (
        frz.get("status") == "PASS"
        and lim.get("quote_mode") == "MODELED_BID_ASK"
        and lim.get("observed_l1_bbo_available") is False
        and lim.get("actual_account_effective_fee_tier") == "UNKNOWN_NOT_LOCALLY_VERIFIED"
        and int(f04.get("OOS_EVALUATIONS", -1)) == 0
        and f04.get("escrow_contract_integrity_status") == "PASS"
        and f04.get("synthetic_20_oos_validator_status") == "PASS"
    )
    gates["G07_OOS_PROVENANCE"] = "PASS" if g07_ok else "FAIL"

    # G08 COLLECTOR_SAFETY
    g08_ok = (
        f07.get("status") == "PASS"
        and int(f07.get("failed_cases", 1)) == 0
        and f07.get("live_network_capture_executed") is False
        and int(f07.get("real_prospective_snapshots_collected", -1)) == 0
        and bool((f07.get("default_collector_readiness") or {}).get("read_only", False))
        and (f07.get("default_collector_readiness") or {}).get("network_capture_enabled") is False
    )
    gates["G08_COLLECTOR_SAFETY"] = "PASS" if g08_ok else "FAIL"

    # G09 ENTRY_TARGET_INTEGRITY
    g09_ok = (
        f08.get("status") == "PASS"
        and int(f08.get("swept_tp_accepted_count", 1)) == 0
        and bool(f08.get("t01_to_t10_all_passed", False))
        and bool(f08.get("future_mutation_invariance_passed", False))
    )
    gates["G09_ENTRY_TARGET_INTEGRITY"] = "PASS" if g09_ok else "FAIL"

    # G10 PAIRED_RESEARCH
    g10_ok = (
        f09.get("status") == "PASS"
        and f09.get("b0_vs_b2_identifiability_status") == "COMPARISON_NOT_IDENTIFIABLE"
        and bool(f09.get("matching_inadequate_guard_verified", False))
    )
    gates["G10_PAIRED_RESEARCH"] = "PASS" if g10_ok else "FAIL"

    # G11 COST_RISK
    g11_ok = (
        f10.get("status") == "PASS"
        and bool(f10.get("fixed_fill_zero_dropped_ids_verified", False))
        and bool(f10.get("full_rerisk_starts_from_full_universe", False))
        and int(f10.get("fill_time_cap_violations", 1)) == 0
        and bool(f10.get("realized_tail_risk_disclosed", False))
        and f10.get("actual_account_effective_fee_tier") == "UNKNOWN_NOT_LOCALLY_VERIFIED"
    )
    gates["G11_COST_RISK"] = "PASS" if g11_ok else "FAIL"

    # G12 FULL_REGRESSION
    r12 = str(f12.get("status") or "FAIL")
    if r12 == "NOT_RUN_BLOCKED_LOW_DISK":
        gates["G12_FULL_REGRESSION"] = "NOT_RUN_BLOCKED_LOW_DISK" if gates["G04_STRICT_DISK"] == "BLOCKED_LOW_DISK" else "FAIL"
    elif (
        r12 == "PASS"
        and gates["G04_STRICT_DISK"] == "PASS"
        and junit_bijection_ok
        and int(f12.get("collected_count", 0)) > 0
        and int(f12.get("collected_count", -1)) == int(f12.get("executed_count", -2))
        and int(f12.get("failed_count", 1)) == 0
        and int(f12.get("error_count", 1)) == 0
        and int(f12.get("mandatory_skipped_count", 0)) == 0
    ):
        gates["G12_FULL_REGRESSION"] = "PASS"
    else:
        gates["G12_FULL_REGRESSION"] = "FAIL"

    # G13 ASYNC_DETERMINISM
    r13 = str(f13.get("status") or "FAIL")
    if r13 == "NOT_RUN_BLOCKED_LOW_DISK":
        gates["G13_ASYNC_DETERMINISM"] = "NOT_RUN_BLOCKED_LOW_DISK" if gates["G04_STRICT_DISK"] == "BLOCKED_LOW_DISK" else "FAIL"
    elif r13 == "PASS" and gates["G04_STRICT_DISK"] == "PASS" and bool(f13.get("all_determinism_identical", False)):
        gates["G13_ASYNC_DETERMINISM"] = "PASS"
    else:
        gates["G13_ASYNC_DETERMINISM"] = "FAIL"

    # G14 BASELINE_PARITY
    r14 = str(f14.get("BASELINE_PARITY_VERDICT") or "FAIL")
    if r14 == "NOT_RUN_BLOCKED_LOW_DISK":
        gates["G14_BASELINE_PARITY"] = "NOT_RUN_BLOCKED_LOW_DISK" if gates["G04_STRICT_DISK"] == "BLOCKED_LOW_DISK" else "FAIL"
    elif (
        r14 == "PASS"
        and gates["G04_STRICT_DISK"] == "PASS"
        and int(f14.get("a1_mismatches_count", 1)) == 0
        and int(f14.get("si8_mismatches_count", 1)) == 0
        and int(f14.get("three_adapter_mismatches_count", 1)) == 0
        and int(f14.get("d4_historical_closed_trades", 1)) == 0
        and bool((f14.get("per_decision_and_episode_fingerprints") or {}).get("all_sealed_fingerprints_exact_match", False))
    ):
        gates["G14_BASELINE_PARITY"] = "PASS"
    else:
        gates["G14_BASELINE_PARITY"] = "FAIL"

    # G15 FAULT_INJECTIONS
    g15_ok = (
        f16.get("status") == "PASS"
        and int(f16.get("total_controls", 0)) >= 70
        and int(f16.get("failed_controls", 1)) == 0
        and int(f16.get("unique_fault_ids", 0)) == int(f16.get("total_controls", -1))
    )
    gates["G15_FAULT_INJECTIONS"] = "PASS" if g15_ok else "FAIL"

    # G16 INDEPENDENT_DISK_VERIFIER
    if for_final_verifier_manifest:
        gates["G16_INDEPENDENT_DISK_VERIFIER"] = "PASS" if verifier_violations_count == 0 else "FAIL"
    else:
        gates["G16_INDEPENDENT_DISK_VERIFIER"] = "PENDING_SEPARATE_OS_VERIFIER"

    # G17 FUTURE_OOS_STATUS
    if int(f04.get("OOS_EVALUATIONS", -1)) != 0 or gates["G07_OOS_PROVENANCE"] != "PASS":
        gates["G17_FUTURE_OOS_STATUS"] = "FAIL"
    else:
        gates["G17_FUTURE_OOS_STATUS"] = str(f04.get("FUTURE_OOS_READINESS_VERDICT") or "BLOCKED_NO_VERIFIED_NEW_DATA")

    # G18 RESEARCH_ECONOMICS
    raw_ec = str(f11.get("RESEARCH_ECONOMIC_VERDICT") or "NO_PROVEN_EDGE")
    gates["G18_RESEARCH_ECONOMICS"] = "NO_PROVEN_EDGE" if raw_ec == "NO_PROVEN_EDGE" else "FAIL_FORGED_PROVEN_EDGE"

    # G19 PUBLICATION
    pub_st = str(f19.get("publication_status") or "NOT_PUBLISHED_BLOCKED_LOW_DISK")
    if waiver_flag or "WAIVER" in pub_st:
        gates["G19_PUBLICATION"] = "FAIL"
    elif pub_st == "PASS_REMOTE_VERIFIED":
        remote_ok = (
            bool(f19.get("remote_readback_sha256_verified", False))
            and len(str(f19.get("evidence_commit_sha") or "")) == 40
            and gates["G04_STRICT_DISK"] == "PASS"
            and gates["G12_FULL_REGRESSION"] == "PASS"
            and gates["G14_BASELINE_PARITY"] == "PASS"
        )
        gates["G19_PUBLICATION"] = "PASS" if remote_ok else "FAIL"
    elif pub_st == "PRE_PUSH_PENDING":
        gates["G19_PUBLICATION"] = (
            "PRE_PUSH_PENDING"
            if (gates["G01_BRANCH_SOURCE"] == "PASS" and gates["G04_STRICT_DISK"] == "PASS" and gates["G12_FULL_REGRESSION"] == "PASS" and gates["G14_BASELINE_PARITY"] == "PASS")
            else "FAIL"
        )
    elif pub_st == "NOT_PUBLISHED_BLOCKED_LOW_DISK" and gates["G04_STRICT_DISK"] == "BLOCKED_LOW_DISK":
        gates["G19_PUBLICATION"] = "NOT_PUBLISHED_BLOCKED_LOW_DISK"
    else:
        gates["G19_PUBLICATION"] = "FAIL"

    # Compute 13 separate orthogonal verdict dimensions (§9)
    tech_gates = (
        "G01_BRANCH_SOURCE", "G02_PRODUCTION_FREEZE", "G03_LIVE_MODES", "G04_STRICT_DISK",
        "G05_RAM_PID", "G06_JOURNAL", "G07_OOS_PROVENANCE", "G08_COLLECTOR_SAFETY",
        "G09_ENTRY_TARGET_INTEGRITY", "G10_PAIRED_RESEARCH", "G11_COST_RISK",
        "G12_FULL_REGRESSION", "G13_ASYNC_DETERMINISM", "G14_BASELINE_PARITY", "G15_FAULT_INJECTIONS",
    )
    if any(gates[k] == "FAIL" for k in tech_gates) or gates["G18_RESEARCH_ECONOMICS"] != "NO_PROVEN_EDGE":
        v_eng = "FAIL"
    elif gates["G04_STRICT_DISK"] == "BLOCKED_LOW_DISK":
        v_eng = "BLOCKED_LOW_DISK"
    elif all(gates[k] == "PASS" for k in tech_gates):
        v_eng = "PASS"
    else:
        v_eng = "BLOCKED_PARTIAL"

    verdicts_13: Dict[str, str] = {
        "ENGINEERING": v_eng,
        "RESOURCE": "PASS" if (gates["G04_STRICT_DISK"] == "PASS" and gates["G05_RAM_PID"] == "PASS") else (
            "FAIL" if (gates["G04_STRICT_DISK"] == "FAIL" or gates["G05_RAM_PID"] == "FAIL") else "BLOCKED_LOW_DISK"
        ),
        "REGRESSION": gates["G12_FULL_REGRESSION"],
        "PARITY": gates["G14_BASELINE_PARITY"],
        "DATA_PROVENANCE": gates["G07_OOS_PROVENANCE"],
        "OOS_READINESS": gates["G17_FUTURE_OOS_STATUS"],
        "ENTRY_TARGET": gates["G09_ENTRY_TARGET_INTEGRITY"],
        "COST": gates["G11_COST_RISK"],
        "CONCURRENCY": gates["G06_JOURNAL"],
        "INDEPENDENT_VERIFIER": gates["G16_INDEPENDENT_DISK_VERIFIER"],
        "PUBLICATION": gates["G19_PUBLICATION"],
        "ECONOMIC": gates["G18_RESEARCH_ECONOMICS"],
        "ACTIVATION": "NO" if gates["G03_LIVE_MODES"] == "PASS" else "FAIL_UNSAFE_ACTIVATION",
    }

    return {
        "gates_19": gates,
        "verdicts_13": verdicts_13,
        "OOS_EVALUATIONS": int(f04.get("OOS_EVALUATIONS", -1)),
    }


def verify_kh02_r2_c2_artifacts_independently_from_disk(
    run_dir: Path,
    *,
    expected_run_id: str = "",
    expected_tested_sha: str = "",
    check_live_repo_pointers: bool = True,
    enforce_distinct_pid: bool = True,
    write_manifest: bool = True,
) -> Dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    verifier_pid = os.getpid()
    violations: List[str] = []

    if not run_dir.is_dir():
        return {
            "spec_version": SPEC_VERSION_KH02_R2_C2,
            "schema_version": SCHEMA_VERSION_KH02_R2_C2,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": [f"RUN_DIR_MISSING:{run_dir}"],
        }

    for rel_req in REQUIRED_ARTIFACTS_KH02_R2_C2:
        fp = run_dir / rel_req
        if not fp.is_file() or fp.stat().st_size == 0:
            violations.append(f"MISSING_OR_EMPTY_ARTIFACT:{rel_req}")
    if violations:
        return {
            "spec_version": SPEC_VERSION_KH02_R2_C2,
            "schema_version": SCHEMA_VERSION_KH02_R2_C2,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": violations,
        }

    try:
        docs: Dict[str, Dict[str, Any]] = {
            "00": json.loads((run_dir / "00_source_branch_remote_diff_preflight.json").read_text("utf-8")),
            "01": json.loads((run_dir / "01_function_ownership_keep_as_is_ast_diff.json").read_text("utf-8")),
            "02": json.loads((run_dir / "02_distinct_write_mount_disk_resource_preflight.json").read_text("utf-8")),
            "03": json.loads((run_dir / "03_disk_waiver_cli_api_and_runtime_abort_faults.json").read_text("utf-8")),
            "04": json.loads((run_dir / "04_r2_freeze_and_prospective_schema_provenance.json").read_text("utf-8")),
            "05": json.loads((run_dir / "05_escrow_append_only_concurrency_crash_recovery.json").read_text("utf-8")),
            "06": json.loads((run_dir / "06_escrow_hash_chain_external_anchor_truth.json").read_text("utf-8")),
            "07": json.loads((run_dir / "07_public_readonly_collector_fake_transport_suite.json").read_text("utf-8")),
            "08": json.loads((run_dir / "08_causal_unswept_target_future_mutation_risk.json").read_text("utf-8")),
            "09": json.loads((run_dir / "09_paired_entry_geometry_common_support_balance.json").read_text("utf-8")),
            "10": json.loads((run_dir / "10_fixed_fill_vs_rerisk_fees_1x2x3x.json").read_text("utf-8")),
            "11": json.loads((run_dir / "11_walkforward_uncertainty_economic_oos_verdict.json").read_text("utf-8")),
            "12": json.loads((run_dir / "12_full_pytest_junit_bijection.json").read_text("utf-8")),
            "13": json.loads((run_dir / "13_async_w1_w2_w4_w1repeat_hashes_times.json").read_text("utf-8")),
            "14": json.loads((run_dir / "14_full_a1_si8_adapter_d4_parity.json").read_text("utf-8")),
            "15": json.loads((run_dir / "15_ram_disk_pid_temp_telemetry.json").read_text("utf-8")),
            "16": json.loads((run_dir / "16_fault_injection_red_green_receipts.json").read_text("utf-8")),
            "18": json.loads((run_dir / "18_orthogonal_gate_matrix.json").read_text("utf-8")),
            "19": json.loads((run_dir / "19_publication_tested_sha_remote_blob_receipt.json").read_text("utf-8")),
        }
        readme_txt = (run_dir / "README_KH02_R2_C2.md").read_text("utf-8")
        handover_txt = (run_dir / "HANDOVER_KH02_R2_C2_VI.txt").read_text("utf-8")
    except Exception as exc:
        return {
            "spec_version": SPEC_VERSION_KH02_R2_C2,
            "schema_version": SCHEMA_VERSION_KH02_R2_C2,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": [f"ARTIFACT_PARSE_ERROR:{exc}"],
        }

    f00, f01, f02, f03 = docs["00"], docs["01"], docs["02"], docs["03"]
    f04, f05, f06, f07 = docs["04"], docs["05"], docs["06"], docs["07"]
    f08, f10, f11, f12 = docs["08"], docs["10"], docs["11"], docs["12"]
    f14, f15, f18, f19 = docs["14"], docs["15"], docs["18"], docs["19"]

    # 1. Distinct PID, branch, run_id, tested SHA lineage
    orch_pid = int(f00.get("orchestrator_pid") or -1)
    if enforce_distinct_pid and verifier_pid == orch_pid:
        violations.append(f"VERIFIER_SAME_OS_PID_AS_ORCHESTRATOR:{verifier_pid}")

    run_id = expected_run_id or str(f00.get("run_id") or "")
    tested_sha = expected_tested_sha or str(f00.get("tested_executable_sha") or "")
    if f00.get("branch") != MANDATORY_BRANCH_KH02_R2:
        violations.append(f"WRONG_BRANCH:{f00.get('branch')}")
    if tested_sha != f00.get("tested_executable_sha") or tested_sha != f18.get("tested_executable_sha") or tested_sha != f19.get("tested_executable_sha"):
        violations.append("TESTED_EXECUTABLE_SHA_MISMATCH_ACROSS_00_18_19")
    if f00.get("start_head_sha") != f00.get("end_head_sha"):
        violations.append("CODE_CHANGED_MID_RUN")
    if tested_sha in (PRIOR_KH02_R1_TESTED_SHA, PRIOR_KH02_R2_TESTED_SHA, PRIOR_KH02_R2_C1_CODE_COMMIT_SHA):
        violations.append(f"STALE_PRIOR_TESTED_SHA_REUSED:{tested_sha}")
    if run_id in (PRIOR_KH02_R1_RUN_ID, PRIOR_KH02_R2_RUN_ID, PRIOR_KH02_R2_C1_RUN_ID):
        violations.append(f"STALE_PRIOR_RUN_ID_REUSED:{run_id}")
    if len(tested_sha) >= 12 and not run_id.endswith(f"_{tested_sha[:12]}"):
        violations.append(f"RUN_ID_MISSING_TESTED_SHA12_SUFFIX:{run_id}")

    # 2. Disk waiver & premature acceptance pointer checks
    if any(bool(d.get("owner_disk_preflight_waiver", False)) for d in (f00, f02, f15, f19)):
        violations.append("UNAPPROVED_OWNER_DISK_WAIVER_FORBIDDEN")
    if bool(f19.get("latest_run_pointer_updated", False)) and f19.get("publication_status") != "PASS_REMOTE_VERIFIED":
        violations.append("PREMATURE_LATEST_RUN_POINTER_UPDATE_BEFORE_REMOTE_VERIFICATION")
    if f19.get("publication_status") == "PASS_REMOTE_VERIFIED" and not bool(f19.get("remote_readback_sha256_verified", False)):
        violations.append("FALSE_REMOTE_READBACK_CLAIM_WITHOUT_SHA256_VERIFICATION")

    # 3. Escrow WORM honesty & anchor truth (05, 06)
    if f06.get("unanchored_worm_verified") is not False:
        violations.append("FALSE_WORM_CLAIM_WITHOUT_EXTERNAL_WORM_ANCHOR")
    if f06.get("unanchored_immutability_class") != "LOCAL_TAMPER_EVIDENT_ONLY":
        violations.append(f"INVALID_UNANCHORED_IMMUTABILITY_CLASS:{f06.get('unanchored_immutability_class')}")

    # 4. Freeze, OOS non-peek, quote/fee/cost Decimal & economic honesty
    frz = f04.get("authoritative_r2_freeze_audit") or {}
    obs_fc = frz.get("observed_freeze_coordinates") or {}
    if (
        frz.get("status") != "PASS"
        or obs_fc.get("frozen_candidate_code_sha") != R2_AUTHORITATIVE_FROZEN_CODE_SHA
        or obs_fc.get("frozen_config_sha256") != R2_AUTHORITATIVE_FROZEN_CONFIG_SHA256
        or int(obs_fc.get("freeze_timestamp_ms") or 0) != R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS
        or int(obs_fc.get("embargo_cutoff_ms") or 0) != R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS
    ):
        violations.append("AUTHORITATIVE_R2_FREEZE_OR_EMBARGO_TAMPERED")
    if int(f04.get("OOS_EVALUATIONS", -1)) != 0 or int(f18.get("OOS_EVALUATIONS", -1)) != 0:
        violations.append("NONZERO_OOS_EVALUATIONS_PEEK")
    if f10.get("actual_account_effective_fee_tier") != "UNKNOWN_NOT_LOCALLY_VERIFIED":
        violations.append("UNVERIFIED_FEE_TIER_MISLABELED")
    for arm_k, row_c in (f10.get("fixed_fill_decimal_checks") or {}).items():
        g_d = Decimal(str(row_c.get("gross_pnl_usd", 0.0)))
        f_d = Decimal(str(row_c.get("fees_usd", 0.0)))
        n_d = Decimal(str(row_c.get("net_pnl_usd", 0.0)))
        if abs((g_d - f_d) - n_d) > Decimal("0.01"):
            violations.append(f"FIXED_FILL_DECIMAL_MISMATCH:{arm_k}")
    if f11.get("RESEARCH_ECONOMIC_VERDICT") != "NO_PROVEN_EDGE":
        violations.append(f"FORGED_ECONOMIC_VERDICT:{f11.get('RESEARCH_ECONOMIC_VERDICT')}")

    # 5. RAM/PID telemetry from disk
    mem_log = run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    mem_audit: Dict[str, Any] = {}
    if not mem_log.is_file() or mem_log.stat().st_size == 0:
        violations.append("MISSING_MEMORY_TELEMETRY_LOG")
    else:
        mem_audit = _audit_memory_samples_fast_stdlib(mem_log)
        if mem_audit.get("status") != "PASS" or int(mem_audit.get("below_floor_samples_count", 1)) > 0:
            violations.append(f"MEMORY_TELEMETRY_BREACHED_512MIB_FLOOR:{mem_audit.get('status')}")
        if int(mem_audit.get("max_workers_observed_in_samples", 99)) > GLOBAL_MAX_DESCENDANTS_KH02_R2:
            violations.append(f"EXCEEDED_GLOBAL_MAX_4_DESCENDANTS:{mem_audit.get('max_workers_observed_in_samples')}")

    # 6. JUnit 1:1 bijection when 12 claims PASS, or zero placeholders when blocked
    junit_dir = run_dir / "junit"
    xmls = sorted(junit_dir.glob("*.xml")) if junit_dir.is_dir() else []
    junit_bijection_ok = False
    if f12.get("status") == "PASS":
        if not xmls:
            violations.append("FULL_REGRESSION_PASS_WITH_ZERO_JUNIT_XML")
        else:
            cm12 = f12.get("collection_manifest") or {}
            sm12 = f12.get("shard_manifest") or {}
            if not cm12 or not sm12:
                violations.append("FULL_REGRESSION_MISSING_COLLECTION_OR_SHARD_MANIFEST")
            else:
                from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
                    verify_fresh_junit_bijection_v17931211,
                )
                bj = verify_fresh_junit_bijection_v17931211(
                    run_dir,
                    cm12,
                    sm12,
                    expected_run_id=run_id,
                    expected_tested_sha=tested_sha,
                    mandatory_node_prefixes=("backend/tests/test_kh_02_r2",),
                    require_shard_schedule=True,
                )
                if bj.get("status") != "PASS":
                    violations.append(f"INDEPENDENT_JUNIT_BIJECTION_FAILED:{bj.get('violations')}")
                else:
                    junit_bijection_ok = True
    elif f12.get("status") == "NOT_RUN_BLOCKED_LOW_DISK":
        if xmls:
            violations.append(f"PLACEHOLDER_JUNIT_XML_PRESENT_WHEN_BLOCKED:{len(xmls)}")
        if int(f12.get("collected_count", 0)) != 0 or int(f12.get("executed_count", 0)) != 0:
            violations.append("COPIED_PYTEST_COUNTS_WHILE_BLOCKED")

    # 7. Prior sealed artifacts & live repo pointers immutability (KH-01B, R1, R2, C1, dataset, CostAssumptions)
    prior_checks: Dict[str, Any] = {}
    if check_live_repo_pointers:
        from domain_calculator import CostAssumptions
        from services.kh_01b_r1_ram_safety_r3_service import detect_active_64mib_ram_floor_in_source
        for rel_p, exp_sha, lbl in (
            (CANONICAL_DATASET_REL_PATH, CANONICAL_DATASET_SHA256_KH02_R2, "CANONICAL_DATASET"),
            (PRIOR_KH02_R1_LATEST_PTR_REL, PRIOR_KH02_R1_LATEST_PTR_SHA256, "KH02_R1_LATEST_PTR"),
            (PRIOR_KH02_R2_LATEST_PTR_REL, PRIOR_KH02_R2_LATEST_PTR_SHA256, "KH02_R2_LATEST_PTR"),
            (R2_AUTHORITATIVE_ARTIFACT_14_REL, R2_AUTHORITATIVE_ARTIFACT_14_SHA256, "KH02_R2_ARTIFACT_14"),
            (PRIOR_KH02_R2_MANIFEST_22_REL, PRIOR_KH02_R2_MANIFEST_22_SHA256, "KH02_R2_MANIFEST_22"),
            (PRIOR_KH02_R2_C1_MANIFEST_16_REL, PRIOR_KH02_R2_C1_MANIFEST_16_SHA256, "KH02_R2_C1_MANIFEST_16"),
            (PRIOR_R3_LATEST_PTR_REL, PRIOR_R3_LATEST_PTR_SHA256, "KH01B_R3_LATEST_PTR"),
            (PRIOR_R2_LATEST_PTR_REL, PRIOR_R2_LATEST_PTR_SHA256, "KH01B_R2_LATEST_PTR"),
        ):
            p = REPO_ROOT / rel_p
            act = _file_sha256(p) if p.is_file() else None
            ok = act == exp_sha
            prior_checks[lbl] = {"path": rel_p, "expected": exp_sha, "actual": act, "match": ok}
            if not ok:
                violations.append(f"PRIOR_SEALED_ARTIFACT_MUTATED:{lbl}")
        c_live = CostAssumptions()
        if (
            abs(float(c_live.taker_fee_rate) - 0.0004) > 1e-12
            or abs(float(c_live.maker_fee_rate) - 0.0002) > 1e-12
            or abs(float(c_live.slippage_usd) - 0.10) > 1e-12
        ):
            violations.append("LIVE_COST_ASSUMPTIONS_MUTATED")
        c2_extra = {
            rel: (REPO_ROOT / rel).read_text(encoding="utf-8")
            for rel in C2_SOURCE_FILES_ALLOWLIST
            if (REPO_ROOT / rel).is_file() and rel.endswith(".py") and "/tests/" not in rel
        }
        hits_64 = detect_active_64mib_ram_floor_in_source(REPO_ROOT, extra_text_by_name=c2_extra)
        if hits_64.get("status") != "PASS" or int(hits_64.get("violations_count", 0)) > 0:
            violations.append(f"ACTIVE_64MIB_RAM_FLOOR_FOUND:{hits_64.get('violations_count')}")
    else:
        for lbl, rec in (f14.get("prior_sealed_artifacts_immutability") or {}).items():
            if not bool(rec.get("match", False)):
                violations.append(f"PRIOR_SEALED_ARTIFACT_MUTATED:{lbl}")

    # 8. Recompute ALL 19 gates & 13 verdicts and assert exact equality with 18
    recomp = recompute_c2_19_gates_and_13_verdicts(
        docs=docs,
        mem_audit=mem_audit,
        junit_bijection_ok=junit_bijection_ok,
        for_final_verifier_manifest=False,
    )
    stored_g = dict(f18.get("gates_19") or {})
    stored_v = dict(f18.get("verdicts_13") or {})
    for gk, gv in recomp["gates_19"].items():
        if gk == "G16_INDEPENDENT_DISK_VERIFIER":
            continue
        if stored_g.get(gk) != gv:
            violations.append(f"GATE_TAMPER_OR_DRIFT:{gk}:stored={stored_g.get(gk)}!=recomputed={gv}")
    for vk in VERDICT_13_DIMENSIONS_KH02_R2_C2:
        if vk == "INDEPENDENT_VERIFIER":
            continue
        if stored_v.get(vk) != recomp["verdicts_13"].get(vk):
            violations.append(f"VERDICT_TAMPER_OR_DRIFT:{vk}:stored={stored_v.get(vk)}!=recomputed={recomp['verdicts_13'].get(vk)}")

    # 9. Cross-view README & Vietnamese handover consistency
    if run_id not in readme_txt or run_id not in handover_txt:
        violations.append("CROSS_VIEW_RUN_ID_MISSING")
    if "ACTIVATION_READY=NO" not in readme_txt or "ACTIVATION_READY=NO" not in handover_txt:
        violations.append("CROSS_VIEW_ACTIVATION_LOCK_MISSING")
    if "NO_PROVEN_EDGE" not in readme_txt or "NO_PROVEN_EDGE" not in handover_txt:
        violations.append("CROSS_VIEW_ECONOMIC_VERDICT_MISSING")

    # 10. Hash EVERY closed file in run_dir recursively (excluding manifest 17)
    sha_map: Dict[str, Dict[str, Any]] = {}
    for fp in sorted(run_dir.rglob("*")):
        if not fp.is_file():
            continue
        rel = fp.relative_to(run_dir).as_posix()
        if rel == MANIFEST_17_NAME or rel.endswith(".tmp"):
            continue
        sha_map[rel] = {"size_bytes": int(fp.stat().st_size), "sha256": _file_sha256(fp)}

    final_status = "PASS" if len(violations) == 0 else "FAIL"
    final_g = dict(recomp["gates_19"])
    final_g["G16_INDEPENDENT_DISK_VERIFIER"] = final_status
    final_v = dict(recomp["verdicts_13"])
    final_v["INDEPENDENT_VERIFIER"] = final_status

    manifest: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH02_R2_C2,
        "schema_version": SCHEMA_VERSION_KH02_R2_C2,
        "verified_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "orchestrator_pid": orch_pid,
        "verifier_pid": verifier_pid,
        "distinct_os_process_verified": (verifier_pid != orch_pid),
        "independent_verifier_status": final_status,
        "recomputed_19_gates": final_g,
        "recomputed_13_verdicts": final_v,
        "memory_telemetry_disk_audit": mem_audit,
        "prior_sealed_artifacts_checks": prior_checks,
        "violations": violations,
        "artifact_count_hashed": len(sha_map),
        "artifact_sha256_map": sha_map,
    }
    manifest["manifest_content_sha256"] = canonical_hash(
        {k: v for k, v in manifest.items() if k != "manifest_content_sha256"}
    )
    if write_manifest:
        write_json_durable_atomic(run_dir / MANIFEST_17_NAME, manifest)
    return manifest


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="KH-02.R2.C2 Separate-OS Independent Disk Verifier")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--expected-run-id", default="")
    ap.add_argument("--expected-tested-sha", default="")
    ap.add_argument("--skip-live-repo-pointers", action="store_true")
    ap.add_argument("--no-write-manifest", action="store_true")
    args = ap.parse_args(argv)

    res = verify_kh02_r2_c2_artifacts_independently_from_disk(
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
                "recomputed_13_verdicts": res.get("recomputed_13_verdicts"),
                "recomputed_19_gates": res.get("recomputed_19_gates"),
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
