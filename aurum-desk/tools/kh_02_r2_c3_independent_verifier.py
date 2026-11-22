#!/usr/bin/env python3
"""
aurum-desk/tools/kh_02_r2_c3_independent_verifier.py

AURUM DESK — KH-02.R2.C3 Separate-OS-Process Independent Disk Verifier
SPEC VERSION: KH-02.R2.C3-v1.0

Executes in a DISTINCT OS process (`verifier_pid != orchestrator_pid`), reads EVERY closed
on-disk artifact (`00`..`19`, `21`, `22`, `README_KH02_R2_C3.md`, `HANDOVER_KH02_R2_C3_VI.txt`,
`logs/runtime_memory_samples_0p5s.jsonl.gz`, and `junit/*.xml` when full regression ran),
independently recomputes ALL 17 gates (`G01`..`G17`) and 17 orthogonal verdicts without
trusting self-reported status labels, verifies byte-for-byte immutability of ALL prior
sealed milestones (KH-01B R2/R3, KH-02.R1, KH-02.R2, KH-02.R2.C1, KH-02.R2.C2, canonical
dataset, CostAssumptions), and seals `20_independent_verifier_sha256_manifest.json`.

Exit codes:
  0 — ALL mandatory engineering gates PASS (OOS=BLOCKED & ECONOMIC=NO_PROVEN_EDGE truthful)
  1 — ANY failure, tamper, SHA mismatch, or invariant violation
  2 — BLOCKED resource / incomplete heavy stages (fail-closed, never promoted to 0)
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
    MANDATORY_BRANCH_KH02_R2_SHARED as MANDATORY_BRANCH_KH02_R2,
    PRIOR_KH02_R1_LATEST_PTR_REL_SHARED as PRIOR_KH02_R1_LATEST_PTR_REL,
    PRIOR_KH02_R1_LATEST_PTR_SHA256_SHARED as PRIOR_KH02_R1_LATEST_PTR_SHA256,
    PRIOR_KH02_R1_RUN_ID_SHARED as PRIOR_KH02_R1_RUN_ID,
    PRIOR_KH02_R1_TESTED_SHA_SHARED as PRIOR_KH02_R1_TESTED_SHA,
    PRIOR_KH02_R2_C1_CODE_COMMIT_SHA_SHARED as PRIOR_KH02_R2_C1_CODE_COMMIT_SHA,
    PRIOR_KH02_R2_C1_MANIFEST_16_REL_SHARED as PRIOR_KH02_R2_C1_MANIFEST_16_REL,
    PRIOR_KH02_R2_C1_MANIFEST_16_SHA256_SHARED as PRIOR_KH02_R2_C1_MANIFEST_16_SHA256,
    PRIOR_KH02_R2_C1_RUN_ID_SHARED as PRIOR_KH02_R2_C1_RUN_ID,
    PRIOR_KH02_R2_C2_LATEST_PTR_REL_SHARED as PRIOR_KH02_R2_C2_LATEST_PTR_REL,
    PRIOR_KH02_R2_C2_LATEST_PTR_SHA256_SHARED as PRIOR_KH02_R2_C2_LATEST_PTR_SHA256,
    PRIOR_KH02_R2_C2_MANIFEST_17_REL_SHARED as PRIOR_KH02_R2_C2_MANIFEST_17_REL,
    PRIOR_KH02_R2_C2_MANIFEST_17_SHA256_SHARED as PRIOR_KH02_R2_C2_MANIFEST_17_SHA256,
    PRIOR_KH02_R2_C2_RUN_ID_SHARED as PRIOR_KH02_R2_C2_RUN_ID,
    PRIOR_KH02_R2_C2_TESTED_SHA_SHARED as PRIOR_KH02_R2_C2_TESTED_SHA,
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
    STRICT_PREFLIGHT_DISK_MIN_GIB_SHARED as STRICT_PREFLIGHT_DISK_MIN_GIB,
    STRICT_RUNTIME_DISK_MIN_GIB_SHARED as STRICT_RUNTIME_DISK_MIN_GIB,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tools.fresh_junit_io import write_json_durable_atomic

SPEC_VERSION_KH02_R2_C3 = "KH-02.R2.C3-v1.0"
SCHEMA_VERSION_KH02_R2_C3 = "kh_02_r2_c3_provenance_qc_v1"
MANIFEST_20_NAME = "20_independent_verifier_sha256_manifest.json"
HARD_RAM_FLOOR_BYTES_C3 = int(HARD_RAM_FLOOR_MIB_KH02_R2 * 1024 * 1024)

REQUIRED_ARTIFACTS_KH02_R2_C3: Tuple[str, ...] = (
    "00_git_branch_tested_sha_remote_preflight.json",
    "01_function_ownership_ast_keep_as_is_locked_diff.json",
    "02_distinct_mount_disk_ram_preflight_readonly_inventory.json",
    "03_disk_waiver_and_runtime_abort_controls.json",
    "04_freeze_registry_raw_provenance_contract.json",
    "05_fake_live_boundary_forgery_rejections.json",
    "06_raw_bytes_reparse_checksum_admission.json",
    "07_http_allowlist_redirect_rate_limit_retries.json",
    "08_escrow_concurrent_writers_hash_integrity.json",
    "09_escrow_torn_write_crash_quarantine_anchor_truth.json",
    "10_unswept_tp_future_mutation_regression.json",
    "11_entry_geometry_matching_common_support.json",
    "12_replay_backtest_r1_r2_strata_risk_fees.json",
    "13_cost_fixed_fill_vs_full_rerisk_1x_2x_3x.json",
    "14_research_uncertainty_economic_oos_verdict.json",
    "15_full_pytest_collection_execution_junit_bijection.json",
    "16_async_w1_w2_w4_w1repeat_hash_timing_ram.json",
    "17_full_a1_si8_adapter_d4_parity.json",
    "18_resource_runtime_telemetry_memory_disk_pid.json",
    "19_fault_injection_red_green_unique_receipts.json",
    "21_orthogonal_science_engineering_gate_matrix.json",
    "22_publication_tested_sha_prepush_receipt.json",
    "README_KH02_R2_C3.md",
    "HANDOVER_KH02_R2_C3_VI.txt",
)

VERDICT_17_DIMENSIONS_KH02_R2_C3: Tuple[str, ...] = (
    "ENGINEERING",
    "RESOURCE",
    "PROVENANCE_FIREWALL",
    "HTTP_CAPTURE_SAFETY",
    "ESCROW_DURABILITY",
    "ENTRY_TARGET",
    "HISTORICAL_REPLAY",
    "COST",
    "REGRESSION",
    "ASYNC_DETERMINISM",
    "PARITY",
    "FAULTS",
    "INDEPENDENT_VERIFIER",
    "PUBLICATION",
    "OOS_READINESS",
    "ECONOMIC",
    "ACTIVATION",
)

C3_SOURCE_FILES_ALLOWLIST: Tuple[str, ...] = (
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
    "aurum-desk/backend/services/kh_02_r2_c3_provenance_guard.py",
    "aurum-desk/tools/run_kh_02_r2_c3_acceptance.py",
    "aurum-desk/tools/kh_02_r2_c3_independent_verifier.py",
    "aurum-desk/backend/tests/test_kh_02_r2_c3_provenance_boundary.py",
    "aurum-desk/backend/tests/test_kh_02_r2_c3_http_rate_limit_and_faults.py",
    "aurum-desk/backend/tests/test_kh_02_r2_c3_escrow_raw_bytes_and_crash.py",
    "aurum-desk/backend/tests/test_kh_02_r2_c3_backtest_parity.py",
)

PRIOR_SEALED_POINTERS_AND_ARTIFACTS_C3: Tuple[Tuple[str, str, str], ...] = (
    (CANONICAL_DATASET_REL_PATH, CANONICAL_DATASET_SHA256_KH02_R2, "CANONICAL_DATASET"),
    (PRIOR_R2_LATEST_PTR_REL, PRIOR_R2_LATEST_PTR_SHA256, "KH01B_R2_LATEST_PTR"),
    (PRIOR_R3_LATEST_PTR_REL, PRIOR_R3_LATEST_PTR_SHA256, "KH01B_R3_LATEST_PTR"),
    (PRIOR_KH02_R1_LATEST_PTR_REL, PRIOR_KH02_R1_LATEST_PTR_SHA256, "KH02_R1_LATEST_PTR"),
    (PRIOR_KH02_R2_LATEST_PTR_REL, PRIOR_KH02_R2_LATEST_PTR_SHA256, "KH02_R2_LATEST_PTR"),
    (R2_AUTHORITATIVE_ARTIFACT_14_REL, R2_AUTHORITATIVE_ARTIFACT_14_SHA256, "KH02_R2_ARTIFACT_14"),
    (PRIOR_KH02_R2_MANIFEST_22_REL, PRIOR_KH02_R2_MANIFEST_22_SHA256, "KH02_R2_MANIFEST_22"),
    (PRIOR_KH02_R2_C1_MANIFEST_16_REL, PRIOR_KH02_R2_C1_MANIFEST_16_SHA256, "KH02_R2_C1_MANIFEST_16"),
    (PRIOR_KH02_R2_C2_LATEST_PTR_REL, PRIOR_KH02_R2_C2_LATEST_PTR_SHA256, "KH02_R2_C2_LATEST_PTR"),
    (PRIOR_KH02_R2_C2_MANIFEST_17_REL, PRIOR_KH02_R2_C2_MANIFEST_17_SHA256, "KH02_R2_C2_MANIFEST_17"),
)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _audit_memory_samples_c3(path: Path) -> Dict[str, Any]:
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
                if ab < HARD_RAM_FLOOR_BYTES_C3:
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


def _recompute_w1_w2_w4_hash_consistency(f16: Mapping[str, Any]) -> bool:
    """Independently checks that all 4 runs in BOTH pytest subset and science slice share exact SHA256."""
    pb = f16.get("pytest_controlled_subset_w1_w2_w4_repeat") or {}
    sb = f16.get("r2_science_slice_w1_w2_w4_repeat") or {}
    p_runs = list(pb.get("runs") or ())
    s_runs = list(sb.get("runs") or ())
    if len(p_runs) < 4 or len(s_runs) < 4:
        return False
    p_hashes = {str(r.get("canonical_sorted_outcome_sha256") or "") for r in p_runs}
    s_hashes = {str(r.get("canonical_sorted_sha256") or "") for r in s_runs}
    if len(p_hashes) != 1 or "" in p_hashes or len(s_hashes) != 1 or "" in s_hashes:
        return False
    if any(int(r.get("executed_tests_count", 0)) <= 0 for r in p_runs):
        return False
    if any(int(r.get("rows_count", 0)) <= 0 for r in s_runs):
        return False
    return bool(pb.get("all_4_runs_identical", False) and sb.get("all_4_runs_identical", False))


def recompute_c3_17_gates_and_17_verdicts(
    *,
    docs: Mapping[str, Mapping[str, Any]],
    mem_audit: Mapping[str, Any],
    junit_bijection_ok: bool,
    verifier_violations_count: int = 0,
    for_final_verifier_manifest: bool = False,
) -> Dict[str, Any]:
    """Independently recomputes ALL 17 gates (G01..G17 per §12) & 17 orthogonal verdicts."""
    f00, f01, f02, f03 = docs["00"], docs["01"], docs["02"], docs["03"]
    f04, f05, f06, f07 = docs["04"], docs["05"], docs["06"], docs["07"]
    f08, f09, f10, f11 = docs["08"], docs["09"], docs["10"], docs["11"]
    f12, f13, f14, f15 = docs["12"], docs["13"], docs["14"], docs["15"]
    f16, f17, f18, f19, f22 = docs["16"], docs["17"], docs["18"], docs["19"], docs["22"]

    gates: Dict[str, str] = {}

    # G01: Branch + SHA + source + clean local + locked prior intact
    branch_ok = f00.get("branch") == MANDATORY_BRANCH_KH02_R2
    sha_ok = (
        len(str(f00.get("start_head_sha") or "")) == 40
        and f00.get("start_head_sha") == f00.get("end_head_sha")
        and f00.get("tested_executable_sha")
        not in (
            PRIOR_KH02_R1_TESTED_SHA,
            PRIOR_KH02_R2_TESTED_SHA,
            PRIOR_KH02_R2_C1_CODE_COMMIT_SHA,
            PRIOR_KH02_R2_C2_TESTED_SHA,
        )
    )
    clean_ok = bool(f00.get("git_clean_at_start", False)) and not bool(f00.get("local_uncommitted_c3_changes", False))
    prior_ok = bool(f17.get("prior_evidence_immutability_verified", False))
    if branch_ok and sha_ok and clean_ok and prior_ok:
        gates["G01_BRANCH_SOURCE_AND_LOCKED_PRIOR"] = "PASS"
    elif branch_ok and sha_ok and not clean_ok:
        gates["G01_BRANCH_SOURCE_AND_LOCKED_PRIOR"] = "BLOCKED_UNCOMMITTED_LOCAL_RUN"
    else:
        gates["G01_BRANCH_SOURCE_AND_LOCKED_PRIOR"] = "FAIL"

    # G02: Production unchanged + ALL trading modes OFF + NO real network capture
    modes_ok = True
    for d in (f00, f01, f04, f14):
        if d.get("ACTIVATION_READY") != "NO":
            modes_ok = False
        rsm = d.get("runtime_safety_modes") or {}
        if any(rsm.get(k) != "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO")):
            modes_ok = False
    g02_ok = (
        modes_ok
        and f01.get("status") == "PASS"
        and int(f01.get("locked_files_modified_count", 1)) == 0
        and len(f01.get("forbidden_order_import_hits") or []) == 0
        and bool(f01.get("cost_assumptions_defaults_intact", False))
        and f07.get("live_network_capture_executed") is False
        and int(f07.get("real_prospective_snapshots_collected", -1)) == 0
    )
    gates["G02_PRODUCTION_FREEZE_AND_OFFLINE_SAFETY"] = "PASS" if g02_ok else "FAIL"

    # G03: Strict disk + RAM preflight & runtime telemetry; zero waiver
    mp02 = f02.get("strict_multi_mount_probe") or f02
    waiver_flag = (
        bool(f00.get("owner_disk_preflight_waiver", False))
        or bool(f02.get("owner_disk_preflight_waiver", False))
        or bool(mp02.get("owner_waiver_permitted", False))
        or bool(f18.get("owner_disk_preflight_waiver", False))
        or bool(f22.get("owner_disk_preflight_waiver", False))
    )
    mounts = list(mp02.get("distinct_mounts") or ())
    probe_errs = list(mp02.get("probe_errors") or ())
    preflight_disk_ok = (
        len(mounts) > 0
        and len(probe_errs) == 0
        and all(
            float(m.get("free_gib", 0.0)) >= STRICT_PREFLIGHT_DISK_MIN_GIB
            and float(m.get("projected_post_reserve_gib", 0.0)) >= STRICT_RUNTIME_DISK_MIN_GIB
            for m in mounts
        )
        and bool(mp02.get("can_run_heavy_stages", False))
    )
    rt_disk_ok = float(f18.get("min_disk_free_gib") or 0.0) >= STRICT_RUNTIME_DISK_MIN_GIB
    f03_ok = f03.get("status") == "PASS" and int(f03.get("failed_controls", 1)) == 0
    ram_pid_ok = (
        float(f18.get("min_available_ram_mib") or 0.0) >= HARD_RAM_FLOOR_MIB_KH02_R2
        and int(f18.get("max_descendant_children", 99)) <= GLOBAL_MAX_DESCENDANTS_KH02_R2
        and int(f18.get("orphan_children_after_cleanup", 99)) == 0
        and bool(f18.get("sentinel_temp_cleaned_up", False))
        and mem_audit.get("status") == "PASS"
        and int(mem_audit.get("below_floor_samples_count", 1)) == 0
        and int(mem_audit.get("max_workers_observed_in_samples", 99)) <= GLOBAL_MAX_DESCENDANTS_KH02_R2
    )
    if waiver_flag or len(probe_errs) > 0 or not f03_ok or not ram_pid_ok:
        gates["G03_STRICT_DISK_RAM_PID_SAFETY"] = "FAIL"
    elif preflight_disk_ok and rt_disk_ok:
        gates["G03_STRICT_DISK_RAM_PID_SAFETY"] = "PASS"
    elif preflight_disk_ok and not rt_disk_ok:
        gates["G03_STRICT_DISK_RAM_PID_SAFETY"] = "FAIL"
    else:
        gates["G03_STRICT_DISK_RAM_PID_SAFETY"] = "BLOCKED_LOW_DISK"

    # G04: Fake/live provenance firewall & raw bytes bound to parsed data
    frz = f04.get("authoritative_r2_freeze_audit") or {}
    g04_ok = (
        frz.get("status") == "PASS"
        and f04.get("synthetic_20_oos_validator_status") == "PASS"
        and f05.get("status") == "PASS"
        and int(f05.get("failed_attacks", 1)) == 0
        and bool(f05.get("fake_transport_blocked_from_real_oos", False))
        and bool(f05.get("stripped_synthetic_blocked_from_real_oos", False))
        and bool(f05.get("metadata_only_blocked_from_real_oos", False))
        and f06.get("status") == "PASS"
        and int(f06.get("failed_checks", 1)) == 0
        and bool(f06.get("raw_bytes_reparse_verified", False))
        and bool(f06.get("parsed_forgery_rejected", False))
        and bool(f06.get("event_timestamp_tamper_rejected", False))
    )
    gates["G04_FAKE_LIVE_PROVENANCE_FIREWALL"] = "PASS" if g04_ok else "FAIL"

    # G05: Public GET safety, real rate limiting & fake transport tests
    g05_ok = (
        f07.get("status") == "PASS"
        and int(f07.get("failed_cases", 1)) == 0
        and f07.get("live_network_capture_executed") is False
        and int(f07.get("real_prospective_snapshots_collected", -1)) == 0
        and f07.get("live_network_test_status") == "LIVE_NETWORK_TEST_NOT_RUN_BY_DESIGN"
        and bool(f07.get("redirect_302_blocked", False))
        and bool(f07.get("real_default_sleep_verified", False))
        and bool(f07.get("forged_caller_disk_probe_blocked", False))
        and bool(f07.get("gzip_bomb_and_oversized_blocked", False))
        and bool((f07.get("default_collector_readiness") or {}).get("read_only", False))
        and (f07.get("default_collector_readiness") or {}).get("network_capture_enabled") is False
    )
    gates["G05_PUBLIC_HTTP_RATE_LIMIT_SAFETY"] = "PASS" if g05_ok else "FAIL"

    # G06: Escrow append-only, concurrency, crash, quarantine & honest anchor
    g06_ok = (
        f08.get("status") == "PASS"
        and bool(f08.get("prior_journal_bytes_untouched_verified", False))
        and bool(f08.get("concurrent_writers_2_4_8_exact_once_passed", False))
        and bool(f08.get("interprocess_flock_passed", False))
        and f09.get("status") == "PASS"
        and bool(f09.get("crash_recovery_and_double_quarantine_passed", False))
        and bool(f09.get("unanchored_pending_attestation_detected", False))
        and f09.get("unanchored_immutability_class") == "LOCAL_TAMPER_EVIDENT_ONLY"
        and f09.get("unanchored_worm_verified") is False
        and bool(f09.get("anchored_rollback_detected", False))
    )
    gates["G06_ESCROW_DURABILITY_AND_ANCHOR_TRUTH"] = "PASS" if g06_ok else "FAIL"

    # G07: R2 target unswept as-of invariants & gap/leakage checks
    g07_ok = (
        f10.get("status") == "PASS"
        and int(f10.get("swept_tp_accepted_count", 1)) == 0
        and bool(f10.get("t01_to_t10_all_passed", False))
        and bool(f10.get("future_mutation_invariance_passed", False))
        and bool(f10.get("live_causal_ledger_recomputed", False))
    )
    gates["G07_CAUSAL_UNSWEPT_TARGET_INTEGRITY"] = "PASS" if g07_ok else "FAIL"

    # G08: Live historical replay backtest & reconciliation with row-level proof
    g08_ok = (
        f11.get("status") == "PASS"
        and f11.get("b0_vs_b2_identifiability_status") == "COMPARISON_NOT_IDENTIFIABLE"
        and bool(f11.get("matching_inadequate_guard_verified", False))
        and f12.get("status") == "PASS"
        and bool(f12.get("fresh_live_historical_replay_executed", False))
        and bool(f12.get("exact_match_with_sealed_r2_baseline", False))
        and int(f12.get("fill_time_cap_violations", 1)) == 0
    )
    gates["G08_LIVE_HISTORICAL_REPLAY_BACKTEST"] = "PASS" if g08_ok else "FAIL"

    # G09: Cost/risk fixed-fill vs full-universe, unknown live fees disclosed
    g09_ok = (
        f13.get("status") == "PASS"
        and bool(f13.get("fixed_fill_zero_dropped_ids_verified", False))
        and bool(f13.get("full_rerisk_starts_from_full_universe", False))
        and bool(f13.get("realized_tail_risk_disclosed", False))
        and f13.get("quote_mode") == "MODELED_BID_ASK"
        and f13.get("observed_l1_bbo_available") is False
        and f13.get("actual_account_effective_fee_tier") == "UNKNOWN_NOT_LOCALLY_VERIFIED"
    )
    gates["G09_COST_RISK_SCOPE_AND_FEE_TRUTH"] = "PASS" if g09_ok else "FAIL"

    # G10: Full pytest JUnit 1:1 bijection
    r15 = str(f15.get("status") or "FAIL")
    if r15 == "NOT_RUN_BLOCKED_LOW_DISK":
        gates["G10_FULL_PYTEST_JUNIT_BIJECTION"] = (
            "NOT_RUN_BLOCKED_LOW_DISK" if gates["G03_STRICT_DISK_RAM_PID_SAFETY"] == "BLOCKED_LOW_DISK" else "FAIL"
        )
    elif (
        r15 == "PASS"
        and gates["G03_STRICT_DISK_RAM_PID_SAFETY"] == "PASS"
        and junit_bijection_ok
        and int(f15.get("collected_count", 0)) > 0
        and int(f15.get("collected_count", -1)) == int(f15.get("executed_count", -2))
        and int(f15.get("failed_count", 1)) == 0
        and int(f15.get("error_count", 1)) == 0
        and int(f15.get("mandatory_skipped_count", 1)) == 0
    ):
        gates["G10_FULL_PYTEST_JUNIT_BIJECTION"] = "PASS"
    else:
        gates["G10_FULL_PYTEST_JUNIT_BIJECTION"] = "FAIL"

    # G11: W1/W2/W4/W1_repeat exact deterministic canonical hashes
    r16 = str(f16.get("status") or "FAIL")
    if r16 == "NOT_RUN_BLOCKED_LOW_DISK":
        gates["G11_ASYNC_W1_W2_W4_DETERMINISM"] = (
            "NOT_RUN_BLOCKED_LOW_DISK" if gates["G03_STRICT_DISK_RAM_PID_SAFETY"] == "BLOCKED_LOW_DISK" else "FAIL"
        )
    elif r16 == "PASS" and gates["G03_STRICT_DISK_RAM_PID_SAFETY"] == "PASS" and _recompute_w1_w2_w4_hash_consistency(f16):
        gates["G11_ASYNC_W1_W2_W4_DETERMINISM"] = "PASS"
    else:
        gates["G11_ASYNC_W1_W2_W4_DETERMINISM"] = "FAIL"

    # G12: Baseline 56,710 A1 / 6,375 SI8 / 246 adapters / strict D4 unchanged
    r17 = str(f17.get("BASELINE_PARITY_VERDICT") or "FAIL")
    if r17 == "NOT_RUN_BLOCKED_LOW_DISK":
        gates["G12_FULL_BASELINE_PARITY"] = (
            "NOT_RUN_BLOCKED_LOW_DISK" if gates["G03_STRICT_DISK_RAM_PID_SAFETY"] == "BLOCKED_LOW_DISK" else "FAIL"
        )
    elif (
        r17 == "PASS"
        and gates["G03_STRICT_DISK_RAM_PID_SAFETY"] == "PASS"
        and int(f17.get("a1_population_count", 0)) == 56710
        and int(f17.get("a1_mismatches_count", 1)) == 0
        and int(f17.get("si8_population_count", 0)) == 6375
        and int(f17.get("si8_mismatches_count", 1)) == 0
        and int(f17.get("three_adapter_population_count", 0)) == 246
        and int(f17.get("three_adapter_mismatches_count", 1)) == 0
        and int(f17.get("d4_historical_closed_trades", 1)) == 0
        and bool((f17.get("per_decision_and_episode_fingerprints") or {}).get("all_sealed_fingerprints_exact_match", False))
    ):
        gates["G12_FULL_BASELINE_PARITY"] = "PASS"
    else:
        gates["G12_FULL_BASELINE_PARITY"] = "FAIL"

    # G13: All unique RED/GREEN fault injections (>= 102 unique real controls)
    g13_ok = (
        f19.get("status") == "PASS"
        and int(f19.get("total_controls", 0)) >= 102
        and int(f19.get("failed_controls", 1)) == 0
        and int(f19.get("unique_fault_ids", 0)) == int(f19.get("total_controls", -1))
    )
    gates["G13_ADVERSARIAL_FAULT_INJECTIONS"] = "PASS" if g13_ok else "FAIL"

    # G14: Independent separate-process verifier
    if for_final_verifier_manifest:
        gates["G14_INDEPENDENT_OS_VERIFIER"] = "PASS" if verifier_violations_count == 0 else "FAIL"
    else:
        gates["G14_INDEPENDENT_OS_VERIFIER"] = "PENDING_SEPARATE_OS_VERIFIER"

    # G15: OOS truth BLOCKED_NO_VERIFIED_NEW_DATA, OOS_EVALUATIONS=0
    if int(f04.get("OOS_EVALUATIONS", -1)) != 0 or int(f14.get("OOS_EVALUATIONS", -1)) != 0:
        gates["G15_OOS_TRUTH_NO_PEEK"] = "FAIL"
    else:
        gates["G15_OOS_TRUTH_NO_PEEK"] = str(f14.get("FUTURE_OOS_READINESS_VERDICT") or "BLOCKED_NO_VERIFIED_NEW_DATA")

    # G16: Economic truth NO_PROVEN_EDGE
    raw_ec = str(f14.get("RESEARCH_ECONOMIC_VERDICT") or "NO_PROVEN_EDGE")
    gates["G16_ECONOMIC_TRUTH_NO_EDGE"] = "NO_PROVEN_EDGE" if raw_ec == "NO_PROVEN_EDGE" else "FAIL_FORGED_PROVEN_EDGE"

    # G17: Publication two-phase verification (NEVER promote PRE_PUSH_PENDING to PASS inside sealed dir!)
    pub_st = str(f22.get("publication_status") or "NOT_PUBLISHED_BLOCKED_LOW_DISK")
    if waiver_flag or "WAIVER" in pub_st:
        gates["G17_PUBLICATION_TWO_PHASE"] = "FAIL"
    elif pub_st == "PASS_REMOTE_VERIFIED":
        rem_ok = (
            bool(f22.get("remote_readback_sha256_verified", False))
            and len(str(f22.get("evidence_commit_sha") or "")) == 40
            and gates["G03_STRICT_DISK_RAM_PID_SAFETY"] == "PASS"
            and gates["G10_FULL_PYTEST_JUNIT_BIJECTION"] == "PASS"
            and gates["G12_FULL_BASELINE_PARITY"] == "PASS"
        )
        gates["G17_PUBLICATION_TWO_PHASE"] = "PASS" if rem_ok else "FAIL"
    elif pub_st == "PRE_PUSH_PENDING":
        ready = (
            gates["G01_BRANCH_SOURCE_AND_LOCKED_PRIOR"] == "PASS"
            and gates["G03_STRICT_DISK_RAM_PID_SAFETY"] == "PASS"
            and gates["G10_FULL_PYTEST_JUNIT_BIJECTION"] == "PASS"
            and gates["G12_FULL_BASELINE_PARITY"] == "PASS"
        )
        gates["G17_PUBLICATION_TWO_PHASE"] = "PRE_PUSH_PENDING" if ready else "FAIL"
    elif pub_st == "NOT_PUBLISHED_BLOCKED_LOW_DISK" and gates["G03_STRICT_DISK_RAM_PID_SAFETY"] == "BLOCKED_LOW_DISK":
        gates["G17_PUBLICATION_TWO_PHASE"] = "NOT_PUBLISHED_BLOCKED_LOW_DISK"
    else:
        gates["G17_PUBLICATION_TWO_PHASE"] = "FAIL"

    # Compute 17 orthogonal non-blended verdicts
    eng_gates = (
        "G01_BRANCH_SOURCE_AND_LOCKED_PRIOR",
        "G02_PRODUCTION_FREEZE_AND_OFFLINE_SAFETY",
        "G03_STRICT_DISK_RAM_PID_SAFETY",
        "G04_FAKE_LIVE_PROVENANCE_FIREWALL",
        "G05_PUBLIC_HTTP_RATE_LIMIT_SAFETY",
        "G06_ESCROW_DURABILITY_AND_ANCHOR_TRUTH",
        "G07_CAUSAL_UNSWEPT_TARGET_INTEGRITY",
        "G08_LIVE_HISTORICAL_REPLAY_BACKTEST",
        "G09_COST_RISK_SCOPE_AND_FEE_TRUTH",
        "G10_FULL_PYTEST_JUNIT_BIJECTION",
        "G11_ASYNC_W1_W2_W4_DETERMINISM",
        "G12_FULL_BASELINE_PARITY",
        "G13_ADVERSARIAL_FAULT_INJECTIONS",
    )
    if any(gates[k] == "FAIL" for k in eng_gates) or gates["G16_ECONOMIC_TRUTH_NO_EDGE"] != "NO_PROVEN_EDGE" or gates["G15_OOS_TRUTH_NO_PEEK"] == "FAIL":
        v_eng = "FAIL"
    elif gates["G03_STRICT_DISK_RAM_PID_SAFETY"] == "BLOCKED_LOW_DISK":
        v_eng = "BLOCKED_LOW_DISK"
    elif all(gates[k] == "PASS" for k in eng_gates):
        v_eng = "PASS"
    else:
        v_eng = "BLOCKED_PARTIAL"

    verdicts_17: Dict[str, str] = {
        "ENGINEERING": v_eng,
        "RESOURCE": gates["G03_STRICT_DISK_RAM_PID_SAFETY"],
        "PROVENANCE_FIREWALL": gates["G04_FAKE_LIVE_PROVENANCE_FIREWALL"],
        "HTTP_CAPTURE_SAFETY": gates["G05_PUBLIC_HTTP_RATE_LIMIT_SAFETY"],
        "ESCROW_DURABILITY": gates["G06_ESCROW_DURABILITY_AND_ANCHOR_TRUTH"],
        "ENTRY_TARGET": gates["G07_CAUSAL_UNSWEPT_TARGET_INTEGRITY"],
        "HISTORICAL_REPLAY": gates["G08_LIVE_HISTORICAL_REPLAY_BACKTEST"],
        "COST": gates["G09_COST_RISK_SCOPE_AND_FEE_TRUTH"],
        "REGRESSION": gates["G10_FULL_PYTEST_JUNIT_BIJECTION"],
        "ASYNC_DETERMINISM": gates["G11_ASYNC_W1_W2_W4_DETERMINISM"],
        "PARITY": gates["G12_FULL_BASELINE_PARITY"],
        "FAULTS": gates["G13_ADVERSARIAL_FAULT_INJECTIONS"],
        "INDEPENDENT_VERIFIER": gates["G14_INDEPENDENT_OS_VERIFIER"],
        "PUBLICATION": gates["G17_PUBLICATION_TWO_PHASE"],
        "OOS_READINESS": gates["G15_OOS_TRUTH_NO_PEEK"],
        "ECONOMIC": gates["G16_ECONOMIC_TRUTH_NO_EDGE"],
        "ACTIVATION": "NO" if gates["G02_PRODUCTION_FREEZE_AND_OFFLINE_SAFETY"] == "PASS" else "FAIL_UNSAFE_ACTIVATION",
    }

    return {
        "gates_17": gates,
        "verdicts_17": verdicts_17,
        "OOS_EVALUATIONS": int(f14.get("OOS_EVALUATIONS", -1)),
    }


def verify_kh02_r2_c3_artifacts_independently_from_disk(
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
            "spec_version": SPEC_VERSION_KH02_R2_C3,
            "schema_version": SCHEMA_VERSION_KH02_R2_C3,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": [f"RUN_DIR_MISSING:{run_dir}"],
        }

    for rel_req in REQUIRED_ARTIFACTS_KH02_R2_C3:
        fp = run_dir / rel_req
        if not fp.is_file() or fp.stat().st_size == 0:
            violations.append(f"MISSING_OR_EMPTY_ARTIFACT:{rel_req}")
    if violations:
        return {
            "spec_version": SPEC_VERSION_KH02_R2_C3,
            "schema_version": SCHEMA_VERSION_KH02_R2_C3,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": violations,
        }

    try:
        docs: Dict[str, Dict[str, Any]] = {}
        for rel_req in REQUIRED_ARTIFACTS_KH02_R2_C3:
            if rel_req.endswith(".json"):
                docs[rel_req[:2]] = json.loads((run_dir / rel_req).read_text("utf-8"))
        readme_txt = (run_dir / "README_KH02_R2_C3.md").read_text("utf-8")
        handover_txt = (run_dir / "HANDOVER_KH02_R2_C3_VI.txt").read_text("utf-8")
    except Exception as exc:
        return {
            "spec_version": SPEC_VERSION_KH02_R2_C3,
            "schema_version": SCHEMA_VERSION_KH02_R2_C3,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": [f"ARTIFACT_PARSE_ERROR:{exc}"],
        }

    f00, f02, f04 = docs["00"], docs["02"], docs["04"]
    f09, f12, f13, f14 = docs["09"], docs["12"], docs["13"], docs["14"]
    f15, f16, f17, f18 = docs["15"], docs["16"], docs["17"], docs["18"]
    f21, f22 = docs["21"], docs["22"]

    # 1. Distinct OS PID, branch, run_id, tested SHA lineage
    orch_pid = int(f00.get("orchestrator_pid") or -1)
    if enforce_distinct_pid and verifier_pid == orch_pid:
        violations.append(f"VERIFIER_SAME_OS_PID_AS_ORCHESTRATOR:{verifier_pid}")

    run_id = expected_run_id or str(f00.get("run_id") or "")
    tested_sha = expected_tested_sha or str(f00.get("tested_executable_sha") or "")
    if f00.get("branch") != MANDATORY_BRANCH_KH02_R2:
        violations.append(f"WRONG_BRANCH:{f00.get('branch')}")
    if tested_sha != f00.get("tested_executable_sha") or tested_sha != f21.get("tested_executable_sha") or tested_sha != f22.get("tested_executable_sha"):
        violations.append("TESTED_EXECUTABLE_SHA_MISMATCH_ACROSS_00_21_22")
    if f00.get("start_head_sha") != f00.get("end_head_sha"):
        violations.append("CODE_CHANGED_MID_RUN")
    if tested_sha in (
        PRIOR_KH02_R1_TESTED_SHA,
        PRIOR_KH02_R2_TESTED_SHA,
        PRIOR_KH02_R2_C1_CODE_COMMIT_SHA,
        PRIOR_KH02_R2_C2_TESTED_SHA,
    ):
        violations.append(f"STALE_PRIOR_TESTED_SHA_REUSED:{tested_sha}")
    if run_id in (PRIOR_KH02_R1_RUN_ID, PRIOR_KH02_R2_RUN_ID, PRIOR_KH02_R2_C1_RUN_ID, PRIOR_KH02_R2_C2_RUN_ID):
        violations.append(f"STALE_PRIOR_RUN_ID_REUSED:{run_id}")
    if len(tested_sha) >= 12 and not run_id.endswith(f"_{tested_sha[:12]}"):
        violations.append(f"RUN_ID_MISSING_TESTED_SHA12_SUFFIX:{run_id}")

    # 2. Disk waiver & premature/false publication checks
    if any(bool(d.get("owner_disk_preflight_waiver", False)) for d in (f00, f02, f18, f22)):
        violations.append("UNAPPROVED_OWNER_DISK_WAIVER_FORBIDDEN")
    if bool(f22.get("latest_run_pointer_updated", False)) and f22.get("publication_status") != "PASS_REMOTE_VERIFIED":
        violations.append("PREMATURE_LATEST_RUN_POINTER_UPDATE_BEFORE_REMOTE_VERIFICATION")
    if f22.get("publication_status") == "PASS_REMOTE_VERIFIED" and not bool(f22.get("remote_readback_sha256_verified", False)):
        violations.append("FALSE_REMOTE_READBACK_CLAIM_WITHOUT_SHA256_VERIFICATION")

    # 3. Escrow WORM honesty & anchor truth (09)
    if f09.get("unanchored_worm_verified") is not False:
        violations.append("FALSE_WORM_CLAIM_WITHOUT_EXTERNAL_WORM_ANCHOR")
    if f09.get("unanchored_immutability_class") != "LOCAL_TAMPER_EVIDENT_ONLY":
        violations.append(f"INVALID_UNANCHORED_IMMUTABILITY_CLASS:{f09.get('unanchored_immutability_class')}")

    # 4. Authoritative freeze, OOS non-peek, Decimal PnL/fees, economic & activation lock
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
    if int(f04.get("OOS_EVALUATIONS", -1)) != 0 or int(f14.get("OOS_EVALUATIONS", -1)) != 0 or int(f21.get("OOS_EVALUATIONS", -1)) != 0:
        violations.append("NONZERO_OOS_EVALUATIONS_PEEK")
    if f13.get("actual_account_effective_fee_tier") != "UNKNOWN_NOT_LOCALLY_VERIFIED":
        violations.append("UNVERIFIED_FEE_TIER_MISLABELED")
    dec_rows = f13.get("fixed_fill_decimal_checks") or {}
    if not dec_rows:
        violations.append("MISSING_FIXED_FILL_DECIMAL_CHECKS")
    for arm_k, row_c in dec_rows.items():
        g_d = Decimal(str(row_c.get("gross_pnl_usd", 0.0)))
        f_d = Decimal(str(row_c.get("fees_usd", 0.0)))
        n_d = Decimal(str(row_c.get("net_pnl_usd", 0.0)))
        if abs((g_d - f_d) - n_d) > Decimal("0.01"):
            violations.append(f"FIXED_FILL_DECIMAL_MISMATCH:{arm_k}")
    if f14.get("RESEARCH_ECONOMIC_VERDICT") != "NO_PROVEN_EDGE":
        violations.append(f"FORGED_ECONOMIC_VERDICT:{f14.get('RESEARCH_ECONOMIC_VERDICT')}")
    if f14.get("ACTIVATION_READY") != "NO" or f21.get("ACTIVATION_READY") != "NO":
        violations.append("FORGED_ACTIVATION_READY_ON")
    if not bool(f12.get("fresh_live_historical_replay_executed", False)):
        violations.append("HISTORICAL_REPLAY_NOT_EXECUTED")

    # 5. RAM/PID telemetry from disk
    mem_log = run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    mem_audit: Dict[str, Any] = {}
    if not mem_log.is_file() or mem_log.stat().st_size == 0:
        violations.append("MISSING_MEMORY_TELEMETRY_LOG")
    else:
        mem_audit = _audit_memory_samples_c3(mem_log)
        if mem_audit.get("status") != "PASS" or int(mem_audit.get("below_floor_samples_count", 1)) > 0:
            violations.append(f"MEMORY_TELEMETRY_BREACHED_512MIB_FLOOR:{mem_audit.get('status')}")
        if int(mem_audit.get("max_workers_observed_in_samples", 99)) > GLOBAL_MAX_DESCENDANTS_KH02_R2:
            violations.append(f"EXCEEDED_GLOBAL_MAX_4_DESCENDANTS:{mem_audit.get('max_workers_observed_in_samples')}")

    # 6. JUnit 1:1 bijection from raw XML bytes
    junit_dir = run_dir / "junit"
    xmls = sorted(junit_dir.glob("*.xml")) if junit_dir.is_dir() else []
    junit_bijection_ok = False
    if f15.get("status") == "PASS":
        if not xmls:
            violations.append("FULL_REGRESSION_PASS_WITH_ZERO_JUNIT_XML")
        else:
            cm15 = f15.get("collection_manifest") or {}
            sm15 = f15.get("shard_manifest") or {}
            if not cm15 or not sm15:
                violations.append("FULL_REGRESSION_MISSING_COLLECTION_OR_SHARD_MANIFEST")
            else:
                from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
                    verify_fresh_junit_bijection_v17931211,
                )
                bj = verify_fresh_junit_bijection_v17931211(
                    run_dir,
                    cm15,
                    sm15,
                    expected_run_id=run_id,
                    expected_tested_sha=tested_sha,
                    mandatory_node_prefixes=("backend/tests/test_kh_02_r2",),
                    require_shard_schedule=True,
                )
                if bj.get("status") != "PASS":
                    violations.append(f"INDEPENDENT_JUNIT_BIJECTION_FAILED:{bj.get('violations')}")
                else:
                    # Also verify stored counts match independently parsed XML counts!
                    bj_exec = int(bj.get("executed_node_ids_count") if bj.get("executed_node_ids_count") is not None else bj.get("executed_count", -1))
                    bj_mand_skip = int(
                        (bj.get("counters") or {}).get(
                            "mandatory_skipped",
                            len(bj.get("mandatory_skipped_nodes") or ()),
                        )
                    )
                    if (
                        bj_exec != int(f15.get("executed_count", -2))
                        or int(bj.get("passed_count", -1)) != int(f15.get("passed_count", -2))
                        or bj_mand_skip != 0
                    ):
                        violations.append("JUNIT_COUNTS_TAMPERED_VS_RAW_XML")
                    else:
                        junit_bijection_ok = True
    elif f15.get("status") == "NOT_RUN_BLOCKED_LOW_DISK":
        if xmls:
            violations.append(f"PLACEHOLDER_JUNIT_XML_PRESENT_WHEN_BLOCKED:{len(xmls)}")
        if int(f15.get("collected_count", 0)) != 0 or int(f15.get("executed_count", 0)) != 0:
            violations.append("COPIED_PYTEST_COUNTS_WHILE_BLOCKED")

    # 7. W1/W2/W4/W1_repeat independent hash check when executed
    if f16.get("status") == "PASS" and not _recompute_w1_w2_w4_hash_consistency(f16):
        violations.append("W1_W2_W4_DETERMINISM_HASH_MISMATCH")

    # 8. Prior sealed artifacts & live repo pointers immutability (KH-01B, R1, R2, C1, C2, dataset, CostAssumptions)
    prior_checks: Dict[str, Any] = {}
    if check_live_repo_pointers:
        from domain_calculator import CostAssumptions
        from services.kh_01b_r1_ram_safety_r3_service import detect_active_64mib_ram_floor_in_source

        for rel_p, exp_sha, lbl in PRIOR_SEALED_POINTERS_AND_ARTIFACTS_C3:
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
        c3_extra = {
            rel: (REPO_ROOT / rel).read_text(encoding="utf-8")
            for rel in C3_SOURCE_FILES_ALLOWLIST
            if (REPO_ROOT / rel).is_file() and rel.endswith(".py") and "/tests/" not in rel
        }
        hits_64 = detect_active_64mib_ram_floor_in_source(REPO_ROOT, extra_text_by_name=c3_extra)
        if hits_64.get("status") != "PASS" or int(hits_64.get("violations_count", 0)) > 0:
            violations.append(f"ACTIVE_64MIB_RAM_FLOOR_FOUND:{hits_64.get('violations_count')}")
    else:
        for lbl, rec in (f17.get("prior_sealed_artifacts_immutability") or {}).items():
            if not bool(rec.get("match", False)):
                violations.append(f"PRIOR_SEALED_ARTIFACT_MUTATED:{lbl}")

    # 9. Recompute ALL 17 gates & 17 verdicts and assert exact equality with 21
    recomp = recompute_c3_17_gates_and_17_verdicts(
        docs=docs,
        mem_audit=mem_audit,
        junit_bijection_ok=junit_bijection_ok,
        for_final_verifier_manifest=False,
    )
    stored_g = dict(f21.get("gates_17") or {})
    stored_v = dict(f21.get("verdicts_17") or {})
    for gk, gv in recomp["gates_17"].items():
        if gk == "G14_INDEPENDENT_OS_VERIFIER":
            continue
        if stored_g.get(gk) != gv:
            violations.append(f"GATE_TAMPER_OR_DRIFT:{gk}:stored={stored_g.get(gk)}!=recomputed={gv}")
    for vk in VERDICT_17_DIMENSIONS_KH02_R2_C3:
        if vk == "INDEPENDENT_VERIFIER":
            continue
        if stored_v.get(vk) != recomp["verdicts_17"].get(vk):
            violations.append(f"VERDICT_TAMPER_OR_DRIFT:{vk}:stored={stored_v.get(vk)}!=recomputed={recomp['verdicts_17'].get(vk)}")

    # 10. Cross-view README & Vietnamese handover consistency
    if run_id not in readme_txt or run_id not in handover_txt:
        violations.append("CROSS_VIEW_RUN_ID_MISSING")
    if "ACTIVATION_READY=NO" not in readme_txt or "ACTIVATION_READY=NO" not in handover_txt:
        violations.append("CROSS_VIEW_ACTIVATION_LOCK_MISSING")
    if "NO_PROVEN_EDGE" not in readme_txt or "NO_PROVEN_EDGE" not in handover_txt:
        violations.append("CROSS_VIEW_ECONOMIC_VERDICT_MISSING")

    # 11. Hash EVERY closed file in run_dir recursively (excluding manifest 20)
    sha_map: Dict[str, Dict[str, Any]] = {}
    for fp in sorted(run_dir.rglob("*")):
        if not fp.is_file():
            continue
        rel = fp.relative_to(run_dir).as_posix()
        if rel == MANIFEST_20_NAME or rel.endswith(".tmp"):
            continue
        sha_map[rel] = {"size_bytes": int(fp.stat().st_size), "sha256": _file_sha256(fp)}

    final_status = "PASS" if len(violations) == 0 else "FAIL"
    final_g = dict(recomp["gates_17"])
    final_g["G14_INDEPENDENT_OS_VERIFIER"] = final_status
    final_v = dict(recomp["verdicts_17"])
    final_v["INDEPENDENT_VERIFIER"] = final_status

    if final_status == "FAIL" or final_v["ENGINEERING"] == "FAIL":
        overall_label = "FAIL_INTEGRITY_OR_ENGINEERING"
        rec_exit_code = 1
    elif final_v["ENGINEERING"] == "PASS":
        overall_label = "PASS_ENGINEERING_RESEARCH_NO_EDGE"
        rec_exit_code = 0
    else:
        overall_label = f"BLOCKED_{final_v['ENGINEERING']}"
        rec_exit_code = 2

    manifest: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH02_R2_C3,
        "schema_version": SCHEMA_VERSION_KH02_R2_C3,
        "verified_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "orchestrator_pid": orch_pid,
        "verifier_pid": verifier_pid,
        "distinct_os_process_verified": (verifier_pid != orch_pid),
        "independent_verifier_status": final_status,
        "overall_classification": overall_label,
        "recommended_exit_code": rec_exit_code,
        "recomputed_17_gates": final_g,
        "recomputed_17_verdicts": final_v,
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
        write_json_durable_atomic(run_dir / MANIFEST_20_NAME, manifest)
    return manifest


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="KH-02.R2.C3 Separate-OS Independent Disk Verifier")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--expected-run-id", default="")
    ap.add_argument("--expected-tested-sha", default="")
    ap.add_argument("--skip-live-repo-pointers", action="store_true")
    ap.add_argument("--no-write-manifest", action="store_true")
    args = ap.parse_args(argv)

    res = verify_kh02_r2_c3_artifacts_independently_from_disk(
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
                "overall_classification": res.get("overall_classification"),
                "verifier_pid": res["verifier_pid"],
                "distinct_os_process_verified": res.get("distinct_os_process_verified"),
                "recomputed_17_verdicts": res.get("recomputed_17_verdicts"),
                "recomputed_17_gates": res.get("recomputed_17_gates"),
                "violations": res.get("violations"),
                "artifact_count_hashed": res.get("artifact_count_hashed"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return int(res.get("recommended_exit_code", 1))


if __name__ == "__main__":
    raise SystemExit(main())
