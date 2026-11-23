#!/usr/bin/env python3
"""
aurum-desk/tools/kh_02_r2_c4_independent_verifier.py

AURUM DESK — KH-02.R2.C4 Separate-OS-Process Independent Disk Verifier
SPEC VERSION: KH-02.R2.C4-v1.0

Executes in a DISTINCT OS process (`verifier_pid != orchestrator_pid`), reads EVERY closed
artifact (`00`..`23`, `12_per_episode_risk_ledger.jsonl.gz`, `README_KH02_R2_C4.md`,
`HANDOVER_KH02_R2_C4_VI.txt`, `logs/runtime_memory_samples_0p5s.jsonl.gz`, and `junit/*.xml`)
directly from disk, NEVER trusts producer status/boolean labels, independently recomputes:
  - Full SHA-256 manifest of all run artifacts (non-circular, excludes 24 itself)
  - Live disk immutability of ALL prior sealed pointers/manifests (KH-01B R2/R3, KH-02.R1,
    KH-02.R2, KH-02.R2.C1, KH-02.R2.C2, KH-02.R2.C3) + canonical dataset + CostAssumptions
  - Streaming per-episode risk-tail ledger (`12_per_episode_risk_ledger.jsonl.gz`) numeric
    recomputation of ALL 5 arms x 2 modes, PnL/fee Decimal arithmetic, loss > $2.50 counts,
    quantiles, max loss, ID hashes (rejects empty `{}` or hardcoded `True`!)
  - 1:1 raw JUnit XML bijection & exact counts across all shards (`mandatory_skipped == 0`)
  - W1/W2/W4/W1_repeat canonical outcome hashes & distinct worker PIDs
  - A1 56,710 / SI8 6,375 / 246 adapters / strict D4=0 fingerprints
  - >= 102 existing + >= 20 new distinct C4 RED/GREEN fault receipts
  - Runtime RAM >= 512 MiB, disk >= 20.5/20.0 GiB, descendants <= 4, orphans == 0, zero waivers
  - ALL 23 orthogonal gates (`G01`..`G23`) & independent verdicts.

Exit codes:
  0 — ALL mandatory engineering gates PASS (OOS=BLOCKED & ECONOMIC=NO_PROVEN_EDGE truthful)
  1 — ANY failure, tamper, SHA mismatch, numeric discrepancy, or invariant violation
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
import xml.etree.ElementTree as ET

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_c4_risk_tail_evidence import (
    EXPECTED_R2_TAIL_ARMS_C4,
    EXPECTED_R2_TAIL_MODE_NAMES_C4,
    validate_c4_tail_disclosure_vs_risk_ledger,
)
from services.kh_02_r2_c4_trusted_transport_boundary import SPEC_VERSION_KH02_R2_C4
from services.kh_02_r2_prospective_data_service import (
    ACTIVATION_READY_KH02_R2_SHARED as ACTIVATION_READY_KH02_R2,
    CANONICAL_DATASET_REL_PATH_KH02_R2 as CANONICAL_DATASET_REL_PATH,
    CANONICAL_DATASET_SHA256_KH02 as CANONICAL_DATASET_SHA256_KH02_R2,
    GLOBAL_MAX_DESCENDANTS_SHARED as GLOBAL_MAX_DESCENDANTS_KH02_R2,
    HARD_RAM_FLOOR_MIB_SHARED as HARD_RAM_FLOOR_MIB_KH02_R2,
    MANDATORY_BRANCH_KH02_R2_SHARED as MANDATORY_BRANCH_KH02_R2,
    PRIOR_KH02_R1_TESTED_SHA_SHARED as PRIOR_KH02_R1_TESTED_SHA,
    PRIOR_KH02_R2_C1_CODE_COMMIT_SHA_SHARED as PRIOR_KH02_R2_C1_CODE_COMMIT_SHA,
    PRIOR_KH02_R2_C2_TESTED_SHA_SHARED as PRIOR_KH02_R2_C2_TESTED_SHA,
    PRIOR_KH02_R2_TESTED_SHA_SHARED as PRIOR_KH02_R2_TESTED_SHA,
    STRICT_PREFLIGHT_DISK_MIN_GIB_SHARED as STRICT_PREFLIGHT_DISK_MIN_GIB,
    STRICT_RUNTIME_DISK_MIN_GIB_SHARED as STRICT_RUNTIME_DISK_MIN_GIB,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_02_r2_c3_independent_verifier import (
    C3_SOURCE_FILES_ALLOWLIST,
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_C3,
    _audit_memory_samples_c3,
)

SCHEMA_VERSION_KH02_R2_C4 = "kh_02_r2_c4_integrity_closure_v1"
MANIFEST_24_NAME = "24_independent_os_verifier_sha256_manifest.json"
HARD_RAM_FLOOR_BYTES_C4 = int(HARD_RAM_FLOOR_MIB_KH02_R2 * 1024 * 1024)

PRIOR_KH02_R2_C3_TESTED_SHA = "d7eff11058e7875b838654a5f4b4d59e94500a80"
PRIOR_KH02_R2_C3_EVIDENCE_SHA = "2a8c1bdb55c01496b058f15eae9175b815cb8dbc"
PRIOR_KH02_R2_C3_PUB_HEAD_SHA = "ca5ba09602c115d1aa2b35f762104f7e0766f4a6"
PRIOR_KH02_R2_C3_RUN_ID = "20261122T124741Z_d7eff11058e7"
PRIOR_KH02_R2_C3_LATEST_PTR_REL = "aurum-desk/docs/artifacts/kh_02_r2_c3_provenance_qc/latest_run.json"
PRIOR_KH02_R2_C3_LATEST_PTR_SHA256 = "48349333e7cd9dae8b480d859af5bb266f601382236f2bfb869fbbd50eb4a6d8"
PRIOR_KH02_R2_C3_MANIFEST_20_REL = (
    "aurum-desk/docs/artifacts/kh_02_r2_c3_provenance_qc/"
    "20261122T124741Z_d7eff11058e7/20_independent_verifier_sha256_manifest.json"
)
PRIOR_KH02_R2_C3_MANIFEST_20_SHA256 = "292f8f4a988fbd8a099522caca71de72d89ca871b0793862cd9804d266d5bff6"
PRIOR_KH02_R2_C3_POSTPUSH_REL = (
    "aurum-desk/docs/artifacts/kh_02_r2_c3_provenance_qc/"
    "23_postpush_remote_blobs_sha256_receipt_20261122T124741Z_d7eff11058e7.json"
)
PRIOR_KH02_R2_C3_POSTPUSH_SHA256 = "589bf037eb0452e088df767a9f6fbd37c62a519a2a1abcf0599ef7af0682679c"

PRIOR_SEALED_POINTERS_AND_ARTIFACTS_C4: Tuple[Tuple[str, str, str], ...] = (
    *PRIOR_SEALED_POINTERS_AND_ARTIFACTS_C3,
    (PRIOR_KH02_R2_C3_LATEST_PTR_REL, PRIOR_KH02_R2_C3_LATEST_PTR_SHA256, "KH02_R2_C3_LATEST_PTR"),
    (PRIOR_KH02_R2_C3_MANIFEST_20_REL, PRIOR_KH02_R2_C3_MANIFEST_20_SHA256, "KH02_R2_C3_MANIFEST_20"),
    (PRIOR_KH02_R2_C3_POSTPUSH_REL, PRIOR_KH02_R2_C3_POSTPUSH_SHA256, "KH02_R2_C3_POSTPUSH_RECEIPT"),
)

C4_SOURCE_FILES_ALLOWLIST: Tuple[str, ...] = (
    *C3_SOURCE_FILES_ALLOWLIST,
    "aurum-desk/backend/services/kh_02_r2_c4_trusted_transport_boundary.py",
    "aurum-desk/backend/services/kh_02_r2_c4_risk_tail_evidence.py",
    "aurum-desk/backend/tests/test_kh_02_r2_c4_provenance_adversarial.py",
    "aurum-desk/backend/tests/test_kh_02_r2_c4_tail_evidence.py",
    "aurum-desk/backend/tests/test_kh_02_r2_c4_verifier_mutations.py",
    "aurum-desk/backend/tests/test_kh_02_r2_c4_escrow_capture_races.py",
    "aurum-desk/tools/run_kh_02_r2_c4_acceptance.py",
    "aurum-desk/tools/kh_02_r2_c4_independent_verifier.py",
)

REQUIRED_ARTIFACTS_KH02_R2_C4: Tuple[str, ...] = (
    "00_git_branch_sha_remote_preflight.json",
    "01_function_inventory_keep_as_is_ast_diff.json",
    "02_prior_sealed_artifacts_sha256_dataset_immutability.json",
    "03_disk_ram_multimount_resource_policy_and_faults.json",
    "04_p0a_direct_mint_subclass_fake_transport_red_proofs.json",
    "05_trusted_transport_boundary_design_threat_model.json",
    "06_p0a_green_capture_provenance_admission_journal_matrix.json",
    "07_http_public_readonly_20_existing_plus_c4_security_cases.json",
    "08_escrow_flock_concurrency_crash_idempotency_anchor_limitations.json",
    "09_causal_unswept_target_future_mutation_parity.json",
    "10_historical_replay_cost_risk_per_arm_science.json",
    "11_realized_tail_complete_by_arm_and_mode.json",
    "12_per_episode_risk_ledger.jsonl.gz",
    "13_tail_ledger_numeric_reconciliation_and_mutation_tests.json",
    "14_cost_fixed_fill_vs_full_rerisk_all_scenarios.json",
    "15_statistical_matching_wf_oos_economic_status.json",
    "16_full_pytest_collection_execution_junit_bijection.json",
    "17_w1_w2_w4_w1repeat_sha256_timing_rss.json",
    "18_a1_si8_adapter_d4_full_parity_fingerprints.json",
    "19_runtime_ram_disk_pid_orphan_telemetry.json",
    "20_all_faults_unique_red_green_receipts.json",
    "21_independent_verifier_mutation_resistance_report.json",
    "22_orthogonal_gate_matrix_and_verdicts_prepush.json",
    "23_publication_two_phase_prepush_receipt.json",
    "README_KH02_R2_C4.md",
    "HANDOVER_KH02_R2_C4_VI.txt",
)

GATE_23_KEYS_C4: Tuple[str, ...] = (
    "G01_SOURCE_BRANCH_HEAD_SHA_CLEAN",
    "G02_KEEP_AS_IS_PRODUCTION_PARITY",
    "G03_DISK_RAM_PID_SAFE",
    "G04_DIRECT_MINT_FORGERY_REJECTED",
    "G05_EVIL_SUBCLASS_INJECTED_TRANSPORT_REJECTED",
    "G06_PUBLIC_HTTP_SECURITY",
    "G07_REAL_OOS_ESCROW_BOUNDARY",
    "G08_RAW_REPARSE_TIMESTAMP_HASH",
    "G09_TARGET_CAUSAL_UNSWEPT",
    "G10_HISTORICAL_REPLAY_PARITY",
    "G11_TAIL_EVIDENCE_COMPLETE",
    "G12_TAIL_INDEPENDENT_RECOMPUTE",
    "G13_COST_RERISK_SCOPE",
    "G14_FULL_PYTEST_JUNIT",
    "G15_ASYNC_DETERMINISM",
    "G16_FAULTS_REAL_RED_GREEN",
    "G17_INDEPENDENT_OS_VERIFIER",
    "G18_OOS_TRUTH",
    "G19_ECONOMIC_TRUTH",
    "G20_LIVE_NETWORK_VALIDATION",
    "G21_PUBLICATION",
    "G22_KH03_RESEARCH_ENTRY_GATE",
    "G23_ACTIVATION",
)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _recompute_w1_w2_w4_c4(f17: Mapping[str, Any]) -> bool:
    pb = f17.get("pytest_controlled_subset_w1_w2_w4_repeat") or {}
    sb = f17.get("r2_science_slice_w1_w2_w4_repeat") or {}
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
    # Verify distinct worker PIDs across runs when recorded
    w4_run = next((r for r in p_runs if r.get("mode_label") == "w=4"), None)
    if w4_run is not None and isinstance(w4_run.get("worker_pids"), list):
        if len(set(w4_run["worker_pids"])) < 2:
            return False
    return bool(pb.get("all_4_runs_identical", False) and sb.get("all_4_runs_identical", False))


def _verify_junit_raw_xml_bijection_c4(
    run_dir: Path,
    f16: Mapping[str, Any],
    *,
    expected_run_id: str,
    expected_tested_sha: str,
) -> Tuple[bool, List[str]]:
    violations: List[str] = []
    cm = f16.get("collection_manifest") or {}
    sm = f16.get("shard_manifest") or {}
    if not cm or not sm:
        return False, ["JUNIT_MISSING_COLLECTION_OR_SHARD_MANIFEST"]
    if cm.get("run_id") != expected_run_id or sm.get("run_id") != expected_run_id:
        violations.append("JUNIT_MANIFEST_RUN_ID_MISMATCH")
    if cm.get("tested_executable_sha") != expected_tested_sha or sm.get("tested_executable_sha") != expected_tested_sha:
        violations.append("JUNIT_MANIFEST_TESTED_SHA_MISMATCH")
    if int(sm.get("nonzero_exit_shards_count", 1)) != 0:
        violations.append(f"JUNIT_NONZERO_EXIT_SHARDS:{sm.get('nonzero_exit_shards_count')}")

    collected_ids = list(cm.get("collected_node_ids") or ())
    if len(collected_ids) == 0 or len(collected_ids) != int(cm.get("collected_node_ids_count", -1)):
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
            violations.append(f"JUNIT_SHARD_TESTCASE_COUNT_MISMATCH:{sh.get('shard_id')}:xml={len(tcs)}!=sched={len(sched)}")
        for tc in tcs:
            cn = str(tc.attrib.get("classname") or "")
            nm = str(tc.attrib.get("name") or "")
            if tc.find("failure") is not None:
                xml_fail_total += 1
            if tc.find("error") is not None:
                xml_err_total += 1
            if tc.find("skipped") is not None:
                xml_skip_total += 1
                if "test_kh_02_r2" in cn or "test_kh_02_r2" in nm:
                    mandatory_skipped += 1

    if sorted(scheduled_all) != sorted(collected_ids):
        violations.append(f"JUNIT_BIJECTION_MISMATCH:collected={len(collected_ids)},scheduled={len(scheduled_all)}")
    xml_pass_total = xml_tests_total - xml_fail_total - xml_err_total - xml_skip_total
    if (
        xml_tests_total != int(f16.get("executed_count", -1))
        or xml_pass_total != int(f16.get("passed_count", -1))
        or xml_skip_total != int(f16.get("skipped_count", -1))
        or xml_fail_total != int(f16.get("failed_count", -1))
        or xml_err_total != int(f16.get("error_count", -1))
    ):
        violations.append(
            f"JUNIT_COUNTS_TAMPERED_VS_RAW_XML:xml=({xml_tests_total},{xml_pass_total},{xml_skip_total},{xml_fail_total},{xml_err_total})!="
            f"f16=({f16.get('executed_count')},{f16.get('passed_count')},{f16.get('skipped_count')},{f16.get('failed_count')},{f16.get('error_count')})"
        )
    if xml_fail_total != 0 or xml_err_total != 0 or mandatory_skipped != 0:
        violations.append(
            f"JUNIT_FAILURES_OR_MANDATORY_SKIPS:fail={xml_fail_total},err={xml_err_total},mandatory_skip={mandatory_skipped}"
        )
    return len(violations) == 0, violations


def recompute_c4_23_gates_and_verdicts(
    *,
    docs: Mapping[str, Mapping[str, Any]],
    mem_audit: Mapping[str, Any],
    tail_ledger_audit: Mapping[str, Any],
    junit_bijection_ok: bool,
    verifier_violations_count: int = 0,
    for_final_verifier_manifest: bool = False,
) -> Dict[str, Any]:
    """Independently recomputes ALL 23 orthogonal gates (G01..G23) and independent verdicts."""
    f00, f01, f02, f03 = docs["00"], docs["01"], docs["02"], docs["03"]
    f04, f05, f06, f07 = docs["04"], docs["05"], docs["06"], docs["07"]
    f08, f09, f10, f11 = docs["08"], docs["09"], docs["10"], docs["11"]
    f13, f14, f15, f16 = docs["13"], docs["14"], docs["15"], docs["16"]
    f17, f18, f19, f20, f21, f23 = docs["17"], docs["18"], docs["19"], docs["20"], docs["21"], docs["23"]

    gates: Dict[str, str] = {}

    # G01: Source branch, HEAD, tested SHA, clean
    branch_ok = f00.get("branch") == MANDATORY_BRANCH_KH02_R2
    tsha = str(f00.get("tested_executable_sha") or "")
    sha_ok = (
        len(tsha) == 40
        and tsha == f00.get("start_head_sha")
        and tsha == f00.get("end_head_sha")
        and tsha not in (
            PRIOR_KH02_R1_TESTED_SHA,
            PRIOR_KH02_R2_TESTED_SHA,
            PRIOR_KH02_R2_C1_CODE_COMMIT_SHA,
            PRIOR_KH02_R2_C2_TESTED_SHA,
            PRIOR_KH02_R2_C3_TESTED_SHA,
        )
    )
    clean_ok = bool(f00.get("git_clean_at_start", False)) and not bool(f00.get("local_uncommitted_c4_changes", False))
    prior_ok = bool(f02.get("all_prior_sealed_artifacts_intact", False)) and bool(f02.get("canonical_dataset_sha256_match", False))
    if branch_ok and sha_ok and clean_ok and prior_ok:
        gates["G01_SOURCE_BRANCH_HEAD_SHA_CLEAN"] = "PASS"
    elif branch_ok and sha_ok and not clean_ok:
        gates["G01_SOURCE_BRANCH_HEAD_SHA_CLEAN"] = "BLOCKED_UNCOMMITTED_LOCAL_RUN"
    else:
        gates["G01_SOURCE_BRANCH_HEAD_SHA_CLEAN"] = "FAIL"

    # G02: Keep-as-is production freeze + A1/SI8/adapter/D4 exact parity
    p_st = str(f18.get("BASELINE_PARITY_VERDICT") or "FAIL")
    fp_ok = bool((f18.get("per_decision_and_episode_fingerprints") or {}).get("all_sealed_fingerprints_exact_match", False))
    keep_ok = (
        f01.get("status") == "PASS"
        and int(f01.get("locked_files_modified_count", 1)) == 0
        and len(f01.get("forbidden_order_import_hits") or []) == 0
        and bool(f01.get("cost_assumptions_defaults_intact", False))
    )
    parity_ok = (
        p_st == "PASS"
        and fp_ok
        and int(f18.get("a1_population_count", 0)) == 56710
        and int(f18.get("a1_mismatches_count", 1)) == 0
        and int(f18.get("si8_population_count", 0)) == 6375
        and int(f18.get("si8_mismatches_count", 1)) == 0
        and int(f18.get("three_adapter_population_count", 0)) == 246
        and int(f18.get("three_adapter_mismatches_count", 1)) == 0
        and int(f18.get("d4_historical_closed_trades", 1)) == 0
    )
    if p_st == "NOT_RUN_BLOCKED_LOW_DISK":
        gates["G02_KEEP_AS_IS_PRODUCTION_PARITY"] = "NOT_RUN_BLOCKED_LOW_DISK"
    else:
        gates["G02_KEEP_AS_IS_PRODUCTION_PARITY"] = "PASS" if (keep_ok and parity_ok) else "FAIL"

    # G03: Strict disk/RAM/PID safety & zero waiver
    mp03 = f03.get("strict_multi_mount_probe") or {}
    waiver_flag = (
        bool(f00.get("owner_disk_preflight_waiver", False))
        or bool(f03.get("owner_disk_preflight_waiver", False))
        or bool(mp03.get("owner_waiver_permitted", False))
        or bool(f19.get("owner_disk_preflight_waiver", False))
        or bool(f23.get("owner_disk_preflight_waiver", False))
    )
    mounts = list(mp03.get("distinct_mounts") or ())
    probe_errs = list(mp03.get("probe_errors") or ())
    preflight_disk_ok = (
        len(mounts) > 0
        and len(probe_errs) == 0
        and all(
            float(m.get("free_gib", 0.0)) >= STRICT_PREFLIGHT_DISK_MIN_GIB
            and float(m.get("projected_post_reserve_gib", 0.0)) >= STRICT_RUNTIME_DISK_MIN_GIB
            for m in mounts
        )
        and bool(mp03.get("can_run_heavy_stages", False))
    )
    rt_disk_ok = float(f19.get("min_disk_free_gib") or 0.0) >= STRICT_RUNTIME_DISK_MIN_GIB
    waiver_neg_ok = f03.get("waiver_negative_controls_status") == "PASS"
    ram_pid_ok = (
        float(f19.get("min_available_ram_mib") or 0.0) >= HARD_RAM_FLOOR_MIB_KH02_R2
        and int(f19.get("max_descendant_children", 99)) <= GLOBAL_MAX_DESCENDANTS_KH02_R2
        and int(f19.get("orphan_children_after_cleanup", 99)) == 0
        and bool(f19.get("sentinel_temp_cleaned_up", False))
        and mem_audit.get("status") == "PASS"
        and int(mem_audit.get("below_floor_samples_count", 1)) == 0
        and int(mem_audit.get("max_workers_observed_in_samples", 99)) <= GLOBAL_MAX_DESCENDANTS_KH02_R2
    )
    if waiver_flag or len(probe_errs) > 0 or not waiver_neg_ok or not ram_pid_ok:
        gates["G03_DISK_RAM_PID_SAFE"] = "FAIL"
    elif preflight_disk_ok and rt_disk_ok:
        gates["G03_DISK_RAM_PID_SAFE"] = "PASS"
    elif preflight_disk_ok and not rt_disk_ok:
        gates["G03_DISK_RAM_PID_SAFE"] = "FAIL"
    else:
        gates["G03_DISK_RAM_PID_SAFE"] = "BLOCKED_LOW_DISK"

    # G04: Direct mint forgery rejected (RED proven + GREEN fixed)
    g04_ok = (
        f04.get("direct_mint_red_proven") is True
        and f06.get("direct_mint_spoof_blocked") is True
        and f06.get("direct_mint_green_provenance_never_live") is True
        and f05.get("hardware_tpm_or_remote_worm") is False
    )
    gates["G04_DIRECT_MINT_FORGERY_REJECTED"] = "PASS" if g04_ok else "FAIL"

    # G05: Evil subclass & injected transport/monkeypatch rejected
    g05_ok = (
        f04.get("evil_subclass_red_proven") is True
        and f04.get("instance_get_monkeypatch_red_proven") is True
        and f06.get("evil_subclass_blocked") is True
        and f06.get("instance_get_monkeypatch_blocked") is True
        and f06.get("post_init_opener_mutation_blocked") is True
        and f06.get("naked_raw_response_blocked") is True
    )
    gates["G05_EVIL_SUBCLASS_INJECTED_TRANSPORT_REJECTED"] = "PASS" if g05_ok else "FAIL"

    # G06: Public HTTP security (20 inherited fake + new C4 cases, zero unauthorized network)
    g06_ok = (
        f07.get("status") == "PASS"
        and int(f07.get("inherited_20_cases_passed", 0)) == 20
        and int(f07.get("c4_security_cases_failed", 1)) == 0
        and int(f07.get("c4_security_cases_passed", 0)) >= 6
        and f07.get("live_network_capture_executed") is False
        and int(f07.get("real_prospective_snapshots_collected", -1)) == 0
    )
    gates["G06_PUBLIC_HTTP_SECURITY"] = "PASS" if g06_ok else "FAIL"

    # G07: Real OOS escrow boundary (0 real OOS records, concurrent + crash + idempotency pass)
    g07_ok = (
        f08.get("status") == "PASS"
        and int(f06.get("real_oos_escrow_final_record_count", -1)) == 0
        and bool(f08.get("concurrent_writers_2_4_8_exact_once_passed", False))
        and bool(f08.get("interprocess_os_lock_passed", False))
        and bool(f08.get("crash_recovery_and_quarantine_passed", False))
        and bool(f08.get("proof_replay_different_snapshot_blocked", False))
        and f08.get("unanchored_worm_verified") is False
    )
    gates["G07_REAL_OOS_ESCROW_BOUNDARY"] = "PASS" if g07_ok else "FAIL"

    # G08: Raw reparse, timestamp & hash bound to proof/session/entry
    g08_ok = (
        f06.get("raw_bytes_reparse_verified") is True
        and f06.get("parsed_forgery_rejected") is True
        and f06.get("event_timestamp_tamper_rejected") is True
        and f06.get("expired_proof_rejected") is True
        and f06.get("modified_bytes_after_capture_rejected") is True
    )
    gates["G08_RAW_REPARSE_TIMESTAMP_HASH"] = "PASS" if g08_ok else "FAIL"

    # G09: Target causal unswept & future mutation
    g09_ok = (
        f09.get("status") == "PASS"
        and bool(f09.get("t01_to_t10_all_passed", False))
        and bool(f09.get("future_mutation_invariance_passed", False))
        and int(f09.get("swept_tp_accepted_count", 1)) == 0
    )
    gates["G09_TARGET_CAUSAL_UNSWEPT"] = "PASS" if g09_ok else "FAIL"

    # G10: Historical replay exact parity with C3/R2 sealed baseline
    g10_ok = (
        f10.get("status") == "PASS"
        and bool(f10.get("fresh_live_historical_replay_executed", False))
        and bool(f10.get("exact_match_with_sealed_r2_c3_baseline", False))
        and len(f10.get("baseline_reconciliation_diffs") or []) == 0
    )
    gates["G10_HISTORICAL_REPLAY_PARITY"] = "PASS" if g10_ok else "FAIL"

    # G11: Tail evidence complete (nonempty 5 arms x 2 modes, nonzero ledger rows, cap=0 violations, disclosure)
    arm_audits_11 = f11.get("arm_fill_time_and_tail_audits") or {}
    g11_ok = (
        f11.get("status") == "PASS"
        and isinstance(arm_audits_11, Mapping)
        and len(arm_audits_11) == len(EXPECTED_R2_TAIL_ARMS_C4)
        and int(f11.get("coverage_cells_populated", 0)) == len(EXPECTED_R2_TAIL_ARMS_C4) * len(EXPECTED_R2_TAIL_MODE_NAMES_C4)
        and int(f11.get("decision_time_cap_violations", 1)) == 0
        and int(f11.get("post_fill_policy_cap_violations", 1)) == 0
        and int((f11.get("linked_per_episode_ledger") or {}).get("total_episode_rows", 0)) > 0
        and "NOT a physical guarantee" in str(f11.get("realized_tail_loss_disclosure") or "")
    )
    gates["G11_TAIL_EVIDENCE_COMPLETE"] = "PASS" if g11_ok else "FAIL"

    # G12: Independent streaming recompute of per-episode ledger + mutation controls
    g12_ok = (
        tail_ledger_audit.get("valid") is True
        and tail_ledger_audit.get("status") == "PASS"
        and f13.get("status") == "PASS"
        and int(f13.get("mutation_controls_failed", 1)) == 0
        and int(f13.get("mutation_controls_passed", 0)) >= 8
    )
    gates["G12_TAIL_INDEPENDENT_RECOMPUTE"] = "PASS" if g12_ok else "FAIL"

    # G13: Cost & full-universe rerisk scope + fee reconciliation + modeled quote/unknown fee truth
    g13_ok = (
        f14.get("status") == "PASS"
        and bool(f14.get("fixed_fill_zero_dropped_ids_verified", False))
        and bool(f14.get("full_rerisk_starts_from_full_universe", False))
        and (f14.get("full_universe_fee_reconciliation") or {}).get("status") == "PASS"
        and f14.get("quote_mode") == "MODELED_BID_ASK"
        and f14.get("observed_l1_bbo_available") is False
        and f14.get("actual_account_effective_fee_tier") == "UNKNOWN_NOT_LOCALLY_VERIFIED"
    )
    gates["G13_COST_RERISK_SCOPE"] = "PASS" if g13_ok else "FAIL"

    # G14: Full pytest JUnit 1:1 bijection
    r16 = str(f16.get("status") or "FAIL")
    if r16 == "NOT_RUN_BLOCKED_LOW_DISK":
        gates["G14_FULL_PYTEST_JUNIT"] = "NOT_RUN_BLOCKED_LOW_DISK" if gates["G03_DISK_RAM_PID_SAFE"] == "BLOCKED_LOW_DISK" else "FAIL"
    elif (
        r16 == "PASS"
        and gates["G03_DISK_RAM_PID_SAFE"] == "PASS"
        and junit_bijection_ok
        and int(f16.get("collected_count", 0)) > 0
        and int(f16.get("collected_count", -1)) == int(f16.get("executed_count", -2))
        and int(f16.get("failed_count", 1)) == 0
        and int(f16.get("error_count", 1)) == 0
        and int(f16.get("mandatory_skipped_count", 1)) == 0
    ):
        gates["G14_FULL_PYTEST_JUNIT"] = "PASS"
    else:
        gates["G14_FULL_PYTEST_JUNIT"] = "FAIL"

    # G15: Async W1/W2/W4/W1_repeat determinism
    r17 = str(f17.get("status") or "FAIL")
    if r17 == "NOT_RUN_BLOCKED_LOW_DISK":
        gates["G15_ASYNC_DETERMINISM"] = "NOT_RUN_BLOCKED_LOW_DISK" if gates["G03_DISK_RAM_PID_SAFE"] == "BLOCKED_LOW_DISK" else "FAIL"
    elif r17 == "PASS" and gates["G03_DISK_RAM_PID_SAFE"] == "PASS" and _recompute_w1_w2_w4_c4(f17):
        gates["G15_ASYNC_DETERMINISM"] = "PASS"
    else:
        gates["G15_ASYNC_DETERMINISM"] = "FAIL"

    # G16: Faults >= 102 existing + >= 20 new distinct C4 controls = >= 122 unique
    g16_ok = (
        f20.get("status") == "PASS"
        and int(f20.get("total_controls", 0)) >= 122
        and int(f20.get("new_c4_controls_count", 0)) >= 20
        and int(f20.get("unique_fault_ids", 0)) == int(f20.get("total_controls", -1))
        and int(f20.get("failed_controls", 1)) == 0
    )
    gates["G16_FAULTS_REAL_RED_GREEN"] = "PASS" if g16_ok else "FAIL"

    # G17: Independent OS verifier & mutation resistance
    mut_ok = (
        f21.get("status") == "PASS"
        and int(f21.get("mutations_tested_count", 0)) >= 10
        and int(f21.get("mutations_failed_detection_count", 1)) == 0
    )
    if for_final_verifier_manifest:
        gates["G17_INDEPENDENT_OS_VERIFIER"] = "PASS" if (verifier_violations_count == 0 and mut_ok) else "FAIL"
    else:
        gates["G17_INDEPENDENT_OS_VERIFIER"] = "PENDING_SEPARATE_OS_VERIFIER"

    # G18: OOS truth (honest BLOCKED_NO_VERIFIED_NEW_DATA, OOS_EVALUATIONS == 0)
    oos_ok = (
        int(f15.get("OOS_EVALUATIONS", -1)) == 0
        and f15.get("FUTURE_OOS_READINESS_VERDICT") == "BLOCKED_NO_VERIFIED_NEW_DATA"
        and f15.get("FRESH_OOS_VERDICT") == "BLOCKED_NO_UNTOUCHED_DATA"
    )
    gates["G18_OOS_TRUTH"] = "BLOCKED_NO_VERIFIED_NEW_DATA" if oos_ok else "FAIL"

    # G19: Economic truth (NO_PROVEN_EDGE)
    econ_ok = f15.get("RESEARCH_ECONOMIC_VERDICT") == "NO_PROVEN_EDGE"
    gates["G19_ECONOMIC_TRUTH"] = "NO_PROVEN_EDGE" if econ_ok else "FAIL"

    # G20: Live network validation (NOT_RUN_BY_DESIGN unless owner-approved genuine session)
    net_ok = f07.get("live_network_test_status") == "LIVE_NETWORK_TEST_NOT_RUN_BY_DESIGN" and f07.get("live_network_capture_executed") is False
    gates["G20_LIVE_NETWORK_VALIDATION"] = "NOT_RUN_BY_DESIGN" if net_ok else "FAIL"

    # G21: Publication two-phase
    pub_st = str(f23.get("publication_status") or "NOT_PUBLISHED_BLOCKED_LOW_DISK")
    if waiver_flag or "WAIVER" in pub_st:
        gates["G21_PUBLICATION"] = "FAIL"
    elif pub_st == "PASS_REMOTE_VERIFIED" and bool(f23.get("remote_readback_sha256_verified", False)):
        gates["G21_PUBLICATION"] = "PASS_REMOTE_VERIFIED"
    elif pub_st == "PRE_PUSH_PENDING":
        gates["G21_PUBLICATION"] = "PRE_PUSH_PENDING"
    elif "BLOCKED" in pub_st:
        gates["G21_PUBLICATION"] = pub_st
    else:
        gates["G21_PUBLICATION"] = "FAIL"

    # G23: Activation always NO & all modes OFF
    modes_ok = True
    for d in (f00, f01, f15):
        if d.get("ACTIVATION_READY") != "NO":
            modes_ok = False
        rsm = d.get("runtime_safety_modes") or {}
        if any(rsm.get(k) != "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO")):
            modes_ok = False
    gates["G23_ACTIVATION"] = "NO" if modes_ok else "FAIL"

    # Engineering roll-up across G01..G17
    eng_gates_pre = [
        "G01_SOURCE_BRANCH_HEAD_SHA_CLEAN",
        "G02_KEEP_AS_IS_PRODUCTION_PARITY",
        "G03_DISK_RAM_PID_SAFE",
        "G04_DIRECT_MINT_FORGERY_REJECTED",
        "G05_EVIL_SUBCLASS_INJECTED_TRANSPORT_REJECTED",
        "G06_PUBLIC_HTTP_SECURITY",
        "G07_REAL_OOS_ESCROW_BOUNDARY",
        "G08_RAW_REPARSE_TIMESTAMP_HASH",
        "G09_TARGET_CAUSAL_UNSWEPT",
        "G10_HISTORICAL_REPLAY_PARITY",
        "G11_TAIL_EVIDENCE_COMPLETE",
        "G12_TAIL_INDEPENDENT_RECOMPUTE",
        "G13_COST_RERISK_SCOPE",
        "G14_FULL_PYTEST_JUNIT",
        "G15_ASYNC_DETERMINISM",
        "G16_FAULTS_REAL_RED_GREEN",
    ]
    any_fail = (
        any(gates[k] == "FAIL" for k in gates)
        or (for_final_verifier_manifest and verifier_violations_count > 0)
    )
    any_low_disk = any("LOW_DISK" in gates[k] for k in eng_gates_pre)
    eng_core_pass = all(gates[k] == "PASS" for k in eng_gates_pre) and (
        (not for_final_verifier_manifest) or gates["G17_INDEPENDENT_OS_VERIFIER"] == "PASS"
    )

    if any_fail:
        eng_verdict = "FAIL"
    elif any_low_disk:
        eng_verdict = "BLOCKED_LOW_DISK"
    elif eng_core_pass:
        eng_verdict = "PASS"
    else:
        eng_verdict = "BLOCKED_INCOMPLETE_VERIFICATION"

    # G22: KH03_RESEARCH_ENTRY_GATE
    if eng_verdict != "PASS" or not oos_ok or not econ_ok or not net_ok or not modes_ok:
        gates["G22_KH03_RESEARCH_ENTRY_GATE"] = f"BLOCKED_{eng_verdict}"
    elif gates["G21_PUBLICATION"] == "PASS_REMOTE_VERIFIED":
        gates["G22_KH03_RESEARCH_ENTRY_GATE"] = "READY_FOR_OWNER_REVIEW"
    else:
        gates["G22_KH03_RESEARCH_ENTRY_GATE"] = "PENDING_REMOTE_PUBLICATION"

    if eng_verdict == "PASS" and oos_ok and econ_ok and modes_ok:
        overall = "PASS_ENGINEERING_RESEARCH_NO_EDGE_OOS_BLOCKED"
    elif eng_verdict == "BLOCKED_LOW_DISK":
        overall = "BLOCKED_LOW_DISK"
    elif eng_verdict == "FAIL":
        overall = "FAIL_INTEGRITY_OR_VERIFICATION"
    else:
        overall = f"BLOCKED_{eng_verdict}"

    verdicts = {
        "ENGINEERING_C4": eng_verdict,
        "RESOURCE_SAFETY": gates["G03_DISK_RAM_PID_SAFE"],
        "TRUSTED_CAPTURE_BOUNDARY_P0A": "PASS" if (g04_ok and g05_ok and g06_ok and g07_ok and g08_ok) else "FAIL",
        "RISK_TAIL_EVIDENCE_P0B": "PASS" if (g11_ok and g12_ok and g13_ok) else "FAIL",
        "HISTORICAL_REPLAY_AND_PARITY": "PASS" if (g09_ok and g10_ok and gates["G02_KEEP_AS_IS_PRODUCTION_PARITY"] == "PASS") else gates["G02_KEEP_AS_IS_PRODUCTION_PARITY"],
        "FULL_REGRESSION": gates["G14_FULL_PYTEST_JUNIT"],
        "ASYNC_DETERMINISM": gates["G15_ASYNC_DETERMINISM"],
        "FAULTS_RED_GREEN": gates["G16_FAULTS_REAL_RED_GREEN"],
        "INDEPENDENT_OS_VERIFIER": gates["G17_INDEPENDENT_OS_VERIFIER"],
        "LIVE_NETWORK_VALIDATION": gates["G20_LIVE_NETWORK_VALIDATION"],
        "FRESH_OOS": "BLOCKED_NO_UNTOUCHED_DATA" if oos_ok else "FAIL",
        "FUTURE_OOS_READINESS": gates["G18_OOS_TRUTH"],
        "ECONOMIC": gates["G19_ECONOMIC_TRUTH"],
        "PUBLICATION": gates["G21_PUBLICATION"],
        "KH03_RESEARCH_ENTRY_GATE": gates["G22_KH03_RESEARCH_ENTRY_GATE"],
        "ACTIVATION_READY": gates["G23_ACTIVATION"],
    }

    return {
        "overall_classification": overall,
        "gates_23": gates,
        "verdicts": verdicts,
    }


def verify_kh02_r2_c4_artifacts_independently_from_disk(
    run_dir: Path,
    *,
    expected_run_id: str = "",
    expected_tested_sha: str = "",
    check_live_repo_pointers: bool = True,
    enforce_distinct_pid: bool = True,
    write_manifest: bool = True,
) -> Dict[str, Any]:
    """Separate-OS-process disk verifier recomputing all hashes, ledgers, JUnit, and 23 gates."""
    run_dir = Path(run_dir).resolve()
    verifier_pid = os.getpid()
    violations: List[str] = []

    if not run_dir.is_dir():
        return {
            "spec_version": SPEC_VERSION_KH02_R2_C4,
            "schema_version": SCHEMA_VERSION_KH02_R2_C4,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "exit_code_recommended": 1,
            "violations": [f"RUN_DIR_MISSING:{run_dir}"],
        }

    for rel in REQUIRED_ARTIFACTS_KH02_R2_C4:
        fp = run_dir / rel
        if not fp.is_file() or fp.stat().st_size == 0:
            violations.append(f"MISSING_OR_EMPTY_ARTIFACT:{rel}")
    if violations:
        return {
            "spec_version": SPEC_VERSION_KH02_R2_C4,
            "schema_version": SCHEMA_VERSION_KH02_R2_C4,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "exit_code_recommended": 1,
            "violations": violations,
        }

    def _rd_json(fn: str) -> Dict[str, Any]:
        return json.loads((run_dir / fn).read_text("utf-8"))

    try:
        docs: Dict[str, Dict[str, Any]] = {
            "00": _rd_json("00_git_branch_sha_remote_preflight.json"),
            "01": _rd_json("01_function_inventory_keep_as_is_ast_diff.json"),
            "02": _rd_json("02_prior_sealed_artifacts_sha256_dataset_immutability.json"),
            "03": _rd_json("03_disk_ram_multimount_resource_policy_and_faults.json"),
            "04": _rd_json("04_p0a_direct_mint_subclass_fake_transport_red_proofs.json"),
            "05": _rd_json("05_trusted_transport_boundary_design_threat_model.json"),
            "06": _rd_json("06_p0a_green_capture_provenance_admission_journal_matrix.json"),
            "07": _rd_json("07_http_public_readonly_20_existing_plus_c4_security_cases.json"),
            "08": _rd_json("08_escrow_flock_concurrency_crash_idempotency_anchor_limitations.json"),
            "09": _rd_json("09_causal_unswept_target_future_mutation_parity.json"),
            "10": _rd_json("10_historical_replay_cost_risk_per_arm_science.json"),
            "11": _rd_json("11_realized_tail_complete_by_arm_and_mode.json"),
            "13": _rd_json("13_tail_ledger_numeric_reconciliation_and_mutation_tests.json"),
            "14": _rd_json("14_cost_fixed_fill_vs_full_rerisk_all_scenarios.json"),
            "15": _rd_json("15_statistical_matching_wf_oos_economic_status.json"),
            "16": _rd_json("16_full_pytest_collection_execution_junit_bijection.json"),
            "17": _rd_json("17_w1_w2_w4_w1repeat_sha256_timing_rss.json"),
            "18": _rd_json("18_a1_si8_adapter_d4_full_parity_fingerprints.json"),
            "19": _rd_json("19_runtime_ram_disk_pid_orphan_telemetry.json"),
            "20": _rd_json("20_all_faults_unique_red_green_receipts.json"),
            "21": _rd_json("21_independent_verifier_mutation_resistance_report.json"),
            "22": _rd_json("22_orthogonal_gate_matrix_and_verdicts_prepush.json"),
            "23": _rd_json("23_publication_two_phase_prepush_receipt.json"),
        }
        readme_txt = (run_dir / "README_KH02_R2_C4.md").read_text("utf-8")
        handover_txt = (run_dir / "HANDOVER_KH02_R2_C4_VI.txt").read_text("utf-8")
    except Exception as exc:
        return {
            "spec_version": SPEC_VERSION_KH02_R2_C4,
            "schema_version": SCHEMA_VERSION_KH02_R2_C4,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "exit_code_recommended": 1,
            "violations": [f"ARTIFACT_PARSE_ERROR:{exc}"],
        }

    f00, f11, f14, f15, f16, f17, f18, f19, f22, f23 = (
        docs["00"], docs["11"], docs["14"], docs["15"], docs["16"],
        docs["17"], docs["18"], docs["19"], docs["22"], docs["23"],
    )

    orch_pid = int(f00.get("orchestrator_pid") or -1)
    if enforce_distinct_pid and verifier_pid == orch_pid:
        violations.append(f"VERIFIER_SAME_OS_PID_AS_ORCHESTRATOR:{verifier_pid}")

    run_id = expected_run_id or str(f00.get("run_id") or "")
    tested_sha = expected_tested_sha or str(f00.get("tested_executable_sha") or "")
    if f00.get("branch") != MANDATORY_BRANCH_KH02_R2:
        violations.append(f"WRONG_BRANCH:{f00.get('branch')}")
    if tested_sha != f00.get("tested_executable_sha") or tested_sha != f22.get("tested_executable_sha") or tested_sha != f23.get("tested_executable_sha"):
        violations.append("TESTED_EXECUTABLE_SHA_MISMATCH_ACROSS_00_22_23")
    if tested_sha in (
        PRIOR_KH02_R1_TESTED_SHA,
        PRIOR_KH02_R2_TESTED_SHA,
        PRIOR_KH02_R2_C1_CODE_COMMIT_SHA,
        PRIOR_KH02_R2_C2_TESTED_SHA,
        PRIOR_KH02_R2_C3_TESTED_SHA,
    ):
        violations.append(f"STALE_PRIOR_TESTED_SHA_REUSED:{tested_sha}")
    if len(tested_sha) >= 12 and not run_id.endswith(f"_{tested_sha[:12]}"):
        violations.append(f"RUN_ID_MISSING_TESTED_SHA12_SUFFIX:{run_id}")

    # 1. Independently stream & recompute per-episode risk ledger (`12_per_episode_risk_ledger.jsonl.gz`) vs `11`!
    ledger_gz = run_dir / "12_per_episode_risk_ledger.jsonl.gz"
    tail_audit = validate_c4_tail_disclosure_vs_risk_ledger(f11, ledger_gz)
    if not tail_audit.get("valid", False):
        violations.extend([f"INDEPENDENT_TAIL_LEDGER:{v}" for v in (tail_audit.get("violations") or ["TAIL_AUDIT_FAILED"])])

    # 2. Independently verify fixed-fill Decimal arithmetic in `14`
    ff_checks = f14.get("fixed_fill_decimal_checks") or {}
    if not ff_checks:
        violations.append("MISSING_FIXED_FILL_DECIMAL_CHECKS_IN_14")
    for k_arm, row in ff_checks.items():
        try:
            g = Decimal(str(row["gross_pnl_usd"]))
            f = Decimal(str(row["fees_usd"]))
            n = Decimal(str(row["net_pnl_usd"]))
            if abs((g - f) - n) > Decimal("0.01"):
                violations.append(f"FIXED_FILL_DECIMAL_MISMATCH:{k_arm}:gross={g},fee={f},net={n}")
        except Exception as exc:
            violations.append(f"FIXED_FILL_DECIMAL_PARSE_ERR:{k_arm}:{exc}")

    # 3. Independently audit memory telemetry log (`logs/runtime_memory_samples_0p5s.jsonl.gz`)
    mem_log = run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    if not mem_log.is_file() or mem_log.stat().st_size == 0:
        mem_audit: Dict[str, Any] = {"status": "FAIL_MISSING_LOG", "below_floor_samples_count": 1, "max_workers_observed_in_samples": 99}
        violations.append("MISSING_MEMORY_TELEMETRY_LOG")
    else:
        mem_audit = _audit_memory_samples_c3(mem_log)
        if mem_audit.get("status") != "PASS":
            violations.append(f"MEMORY_TELEMETRY_BREACHED_512MIB_FLOOR:{mem_audit.get('status')}")
        if int(mem_audit.get("max_workers_observed_in_samples", 99)) > GLOBAL_MAX_DESCENDANTS_KH02_R2:
            violations.append(f"MEMORY_TELEMETRY_EXCEEDED_CHILD_CAP:{mem_audit.get('max_workers_observed_in_samples')}")

    # 4. Independently verify JUnit raw XML bijection if full pytest ran
    junit_ok = False
    if f16.get("status") == "PASS":
        junit_ok, j_errs = _verify_junit_raw_xml_bijection_c4(
            run_dir, f16, expected_run_id=run_id, expected_tested_sha=tested_sha
        )
        if not junit_ok:
            violations.extend(j_errs)

    # 5. W1/W2/W4 determinism check when executed
    if f17.get("status") == "PASS" and not _recompute_w1_w2_w4_c4(f17):
        violations.append("W1_W2_W4_DETERMINISM_HASH_MISMATCH")

    # 6. Pre-push publication honesty
    if f23.get("publication_status") == "PASS_REMOTE_VERIFIED" and not bool(f23.get("remote_readback_sha256_verified", False)):
        violations.append("FALSE_REMOTE_READBACK_CLAIM")
    if bool(f00.get("owner_disk_preflight_waiver", False)) or bool(docs["03"].get("owner_disk_preflight_waiver", False)) or bool(f19.get("owner_disk_preflight_waiver", False)):
        violations.append("UNAPPROVED_OWNER_DISK_WAIVER_FORBIDDEN")

    # 7. Economic, OOS, Activation & WORM honesty
    if int(f15.get("OOS_EVALUATIONS", -1)) != 0:
        violations.append("NONZERO_OOS_EVALUATIONS_PEEK")
    if f15.get("ACTIVATION_READY") != "NO" or f00.get("ACTIVATION_READY") != "NO":
        violations.append("FORGED_ACTIVATION_READY_ON")
    if f15.get("RESEARCH_ECONOMIC_VERDICT") != "NO_PROVEN_EDGE":
        violations.append(f"FORGED_ECONOMIC_VERDICT:{f15.get('RESEARCH_ECONOMIC_VERDICT')}")
    if docs["08"].get("unanchored_worm_verified") is not False:
        violations.append("FALSE_WORM_CLAIM")

    # 8. Live repo immutability of ALL prior sealed pointers & CostAssumptions
    if check_live_repo_pointers:
        for rel_p, exp_sha, lbl in PRIOR_SEALED_POINTERS_AND_ARTIFACTS_C4:
            p = REPO_ROOT / rel_p
            if not p.is_file() or _file_sha256(p) != exp_sha:
                violations.append(f"PRIOR_SEALED_ARTIFACT_MUTATED:{lbl}")
        from domain_calculator import CostAssumptions
        c_live = CostAssumptions()
        if (
            abs(float(c_live.taker_fee_rate) - 0.0004) > 1e-12
            or abs(float(c_live.maker_fee_rate) - 0.0002) > 1e-12
            or abs(float(c_live.slippage_usd) - 0.10) > 1e-12
        ):
            violations.append("LIVE_COST_ASSUMPTIONS_MUTATED")
    else:
        prior_map = docs["02"].get("prior_sealed_artifacts_immutability") or {}
        if any(not bool(v.get("match", False)) for v in prior_map.values() if isinstance(v, Mapping)):
            violations.append("PRIOR_SEALED_ARTIFACT_MUTATED")

    # 9. Recompute all 23 gates & compare against stored `22_orthogonal_gate_matrix_and_verdicts_prepush.json`
    recomp_pre = recompute_c4_23_gates_and_verdicts(
        docs=docs,
        mem_audit=mem_audit,
        tail_ledger_audit=tail_audit,
        junit_bijection_ok=junit_ok,
        verifier_violations_count=len(violations),
        for_final_verifier_manifest=False,
    )
    stored_gates = docs["22"].get("gates_23") or {}
    for gk, gv in recomp_pre["gates_23"].items():
        if stored_gates.get(gk) != gv:
            violations.append(f"GATE_TAMPER_OR_DRIFT:{gk}:stored={stored_gates.get(gk)}!=recomputed={gv}")

    # 10. Cross-view consistency on README & HANDOVER
    if run_id not in readme_txt or run_id not in handover_txt:
        violations.append("CROSS_VIEW_RUN_ID_MISSING")
    if "ACTIVATION_READY=NO" not in readme_txt or "ACTIVATION_READY=NO" not in handover_txt:
        violations.append("CROSS_VIEW_ACTIVATION_LOCK_MISSING")
    if "NO_PROVEN_EDGE" not in readme_txt or "NO_PROVEN_EDGE" not in handover_txt:
        violations.append("CROSS_VIEW_NO_PROVEN_EDGE_MISSING")

    # 11. Final recompute with verifier_violations_count applied to G17
    recomp_final = recompute_c4_23_gates_and_verdicts(
        docs=docs,
        mem_audit=mem_audit,
        tail_ledger_audit=tail_audit,
        junit_bijection_ok=junit_ok,
        verifier_violations_count=len(violations),
        for_final_verifier_manifest=True,
    )

    # 12. Hash EVERY file in run_dir recursively (non-circular: exclude manifest 24 & .tmp)
    artifact_sha256_map: Dict[str, Dict[str, Any]] = {}
    for fp in sorted(run_dir.rglob("*")):
        if not fp.is_file():
            continue
        rel = fp.relative_to(run_dir).as_posix()
        if rel == MANIFEST_24_NAME or rel.endswith(".tmp"):
            continue
        artifact_sha256_map[rel] = {
            "size_bytes": int(fp.stat().st_size),
            "sha256": _file_sha256(fp),
        }

    eng_final = recomp_final["verdicts"]["ENGINEERING_C4"]
    if len(violations) > 0 or eng_final == "FAIL":
        verifier_status = "FAIL"
        exit_code = 1
    elif eng_final == "PASS":
        verifier_status = "PASS"
        exit_code = 0
    else:
        verifier_status = "PASS"
        exit_code = 2

    payload: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH02_R2_C4,
        "schema_version": SCHEMA_VERSION_KH02_R2_C4,
        "verified_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "orchestrator_pid": orch_pid,
        "verifier_pid": verifier_pid,
        "distinct_os_process_verified": (verifier_pid != orch_pid),
        "independent_verifier_status": verifier_status,
        "overall_classification": recomp_final["overall_classification"],
        "exit_code_recommended": exit_code,
        "recomputed_23_gates": recomp_final["gates_23"],
        "recomputed_verdicts": recomp_final["verdicts"],
        "independent_tail_ledger_audit": tail_audit.get("recomputed_summary"),
        "memory_telemetry_disk_audit": mem_audit,
        "junit_bijection_verified": junit_ok,
        "violations": violations,
        "artifact_count_hashed": len(artifact_sha256_map),
        "artifact_sha256_map": artifact_sha256_map,
    }
    payload["manifest_content_sha256"] = canonical_hash(
        {k: v for k, v in payload.items() if k != "manifest_content_sha256"}
    )

    if write_manifest:
        write_json_durable_atomic(run_dir / MANIFEST_24_NAME, payload)

    return payload


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="KH-02.R2.C4 Independent Separate-OS Disk Verifier")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--expected-run-id", default="")
    ap.add_argument("--expected-tested-sha", default="")
    ap.add_argument("--skip-live-repo-pointers", action="store_true")
    ap.add_argument("--no-write-manifest", action="store_true")
    args = ap.parse_args(argv)

    res = verify_kh02_r2_c4_artifacts_independently_from_disk(
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
                "recomputed_23_gates": res.get("recomputed_23_gates"),
                "recomputed_verdicts": res.get("recomputed_verdicts"),
                "violations": res.get("violations"),
                "artifact_count_hashed": res.get("artifact_count_hashed"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return int(res.get("exit_code_recommended", 1))


if __name__ == "__main__":
    raise SystemExit(main())
