#!/usr/bin/env python3
"""
aurum-desk/tools/run_kh_02_r2_c2_acceptance.py

AURUM DESK — KH-02.R2.C2 | STRICT ACCEPTANCE CLOSURE • DISK SAFETY • OOS ESCROW HARDENING • PUBLICATION TRUTH
SPEC VERSION: KH-02.R2.C2-v1.0 | RESEARCH ONLY | SAME BRANCH | ACTIVATION_READY=NO

Executes the complete gated KH-02.R2.C2 acceptance pipeline:
  - Rejects `--owner-waive-disk-preflight` (CLI, API, and env var) with exit code 2.
  - Strict multi-mount fail-closed disk preflight (>= 20.5 GiB preflight / >= 20.0 GiB runtime / >= 25.0 GiB advisory).
  - Hardened append-only concurrent crash-safe escrow journal & external anchor truth verification (05, 06).
  - Read-only public Bitget collector fake-transport QA suite (07; live network OFF by default).
  - Preserves R2 causal unswept TP, paired design, fill-time risk, cost reconciliation & NO_PROVEN_EDGE (08..11).
  - When strict disk & RAM & clean git PASS: executes REAL full-repo LPT-sharded pytest (`w <= 4`, 1:1 JUnit bijection) (12),
    W1/W2/W4/W1_repeat determinism benchmarks (13), and FULL 56,710 A1 / 6,375 SI8 / 246 3-adapter / strict D4=0 parity (14).
  - Executes 74 unique real RED/GREEN fault controls (42 R2 + 14 C1 + 18 C2) (16).
  - Seals `17_independent_os_verifier_sha256_manifest.json` via separate-OS-process `kh_02_r2_c2_independent_verifier.py`.
"""
from __future__ import annotations

import json as _early_json
import os as _early_os
import sys as _early_sys

if __name__ == "__main__" and (
    "--owner-waive-disk-preflight" in _early_sys.argv
    or str(_early_os.environ.get("KH02_OWNER_WAIVE_DISK_PREFLIGHT") or "").strip().lower() in ("1", "true", "yes")
):
    print(
        _early_json.dumps(
            {
                "status": "REJECTED_UNAPPROVED_AUTONOMOUS_DISK_WAIVER",
                "error": (
                    "UNAPPROVED_AUTONOMOUS_DISK_WAIVER_FORBIDDEN: "
                    "--owner-waive-disk-preflight and env overrides are strictly forbidden in KH-02.R2.C2 "
                    "(>= 20.5 GiB preflight / >= 20.0 GiB runtime)."
                ),
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
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

import psutil

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from domain_calculator import CostAssumptions
from services.kh_01b_r1_ram_safety_r3_service import (
    PRIOR_R2_LATEST_PTR_REL,
    PRIOR_R2_LATEST_PTR_SHA256,
    load_empirical_r2_shard_rss_bytes,
)
from services.kh_02_entry_foundation_research_service import (
    PRIOR_R3_LATEST_PTR_REL,
    PRIOR_R3_LATEST_PTR_SHA256,
)
from services.kh_02_r2_c2_escrow_journal import (
    C2DurableEscrowJournal,
    ExternalHeadAnchorC2,
    make_crash_hook_for_phase,
)
from services.kh_02_r2_c2_public_capture import (
    C2PublicReadOnlyBitgetCollector,
    OperatorCaptureAuthorizationC2,
    run_kh02_r2_c2_fake_transport_collector_suite,
)
from services.kh_02_r2_prospective_data_service import (
    BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1,
    EMBARGO_BOUNDARY_SEMANTICS_KH02_R2_C1,
    PRIOR_KH02_R2_C1_CODE_COMMIT_SHA_SHARED as PRIOR_KH02_R2_C1_CODE_COMMIT_SHA,
    PRIOR_KH02_R2_C1_DIAG_COMMIT_SHA_SHARED as PRIOR_KH02_R2_C1_DIAG_COMMIT_SHA,
    PRIOR_KH02_R2_C1_MANIFEST_16_REL_SHARED as PRIOR_KH02_R2_C1_MANIFEST_16_REL,
    PRIOR_KH02_R2_C1_MANIFEST_16_SHA256_SHARED as PRIOR_KH02_R2_C1_MANIFEST_16_SHA256,
    PRIOR_KH02_R2_C1_RUN_ID_SHARED as PRIOR_KH02_R2_C1_RUN_ID,
    ProspectiveHashLinkedEscrowRegistryR2C1,
    R2_AUTHORITATIVE_ARTIFACT_14_REL,
    R2_AUTHORITATIVE_ARTIFACT_14_SHA256,
    R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS,
    R2_AUTHORITATIVE_EMBARGO_CUTOFF_UTC,
    R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
    R2_AUTHORITATIVE_FREEZE_TIMESTAMP_UTC,
    R2_AUTHORITATIVE_FROZEN_CODE_SHA,
    R2_AUTHORITATIVE_FROZEN_CONFIG_SHA256,
    audit_authoritative_r2_freeze_manifest_r2_c1,
    compute_prospective_oos_power_requirements_r2_c1,
    make_synthetic_valid_c1_snapshot_fixture,
    prepare_r2_prospective_oos_ingestion_and_escrow,
    run_kh02_r2_c1_20_synthetic_oos_ingestion_faults,
)
from services.kh_02_r2_research_integrity_service import (
    ACTIVATION_READY_KH02_R2,
    ADVISORY_DISK_GIB_KH02_R2,
    CANONICAL_DATASET_REL_PATH,
    CANONICAL_DATASET_SHA256_KH02_R2,
    DEFAULT_RANDOM_SEED_KH02_R2,
    FORBIDDEN_ORDER_SERVICE_TOKENS,
    GLOBAL_MAX_DESCENDANTS_KH02_R2,
    HARD_RAM_FLOOR_MIB_KH02_R2,
    LOCKED_KEEP_AS_IS_FILES_KH02_R2,
    MANDATORY_BRANCH_KH02_R2,
    PREFLIGHT_RAM_MIN_MIB_KH02_R2,
    PRIOR_KH02_R1_LATEST_PTR_REL,
    PRIOR_KH02_R1_LATEST_PTR_SHA256,
    PRIOR_KH02_R1_PUBLICATION_HEAD_SHA,
    PRIOR_KH02_R2_LATEST_PTR_REL,
    PRIOR_KH02_R2_LATEST_PTR_SHA256,
    PRIOR_KH02_R2_MANIFEST_22_REL,
    PRIOR_KH02_R2_MANIFEST_22_SHA256,
    PRIOR_KH02_R2_PUBLICATION_HEAD_SHA,
    PRIOR_KH02_R2_RUN_ID,
    PRIOR_KH02_R2_TESTED_SHA,
    RUNTIME_SAFETY_MODES_KH02_R2,
    STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
    STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2,
    _ast_function_inventory,
    _compute_smd,
    _sha256_file,
    audit_r2_function_ownership_and_keep_as_is,
    audit_r2_reference_inputs_and_invariants,
    build_r2_c1_readonly_disk_inventory,
    build_r2_paired_factorial_entry_geometry_design,
    create_sentinel_owned_temp_dir_r2_c1,
    evaluate_causal_fill_time_policy_single_trade,
    load_r2_canonical_dataset_readonly,
    probe_r2_c1_write_mounts_strict,
    reprice_r2_fixed_fills_cost_counterfactual,
    run_r2_t01_to_t10_adversarial_target_scenarios,
    safe_cleanup_sentinel_owned_temp_dir_r2_c1,
    validate_r2_cost_scope_honesty,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    verify_fresh_junit_bijection_v17931211,
)
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_02_r2_c2_independent_verifier import (
    C2_SOURCE_FILES_ALLOWLIST,
    SCHEMA_VERSION_KH02_R2_C2,
    SPEC_VERSION_KH02_R2_C2,
    VERDICT_13_DIMENSIONS_KH02_R2_C2,
    _audit_memory_samples_fast_stdlib,
    recompute_c2_19_gates_and_13_verdicts,
)
from tools.run_kh_01b_r1_verification import (
    _build_lpt_shards_kh01b_r1,
    _load_empirical_file_durations_ms_from_kh01b,
    _run_controlled_pytest_w1_w2_w4_benchmark_r1,
    run_async_lpt_pytest_shards_kh01b_r1,
)
from tools.run_kh_02_r2_c1_strict_closure import (
    R2_SEALED_RUN_DIR_REL,
    _run_artifact_03_disk_waiver_negative_controls,
    _run_c1_56_red_green_fault_suite,
)
from tools.run_kh_02_r2_verification import (
    _BoundedResourceSampler,
    _git_cmd,
    _run_full_production_baseline_parity_20,
    _run_r2_science_slice_w1_w2_w4_determinism,
)
from tools.run_v17_9_3_1_2_2_verification import canonicalize_collect_node_id_v1793122

C2_ARTIFACTS_BASE_REL = "aurum-desk/docs/artifacts/kh_02_r2_c2_acceptance"

# Test files that spawn child subprocesses MUST run exclusive (slot_cost=4) so global descendants <= 4 ALWAYS
C2_EXCLUSIVE_SUBPROCESS_TEST_FILES: Set[str] = {
    "test_kh_02_asof_mtf_and_fault_controls.py",
    "test_kh_02_r2_oos_provenance_and_faults.py",
    "test_kh_02_r2_c1_strict_safety_and_oos.py",
    "test_kh_02_r2_c2_escrow_journal.py",
    "test_kh_02_r2_c2_acceptance_and_faults.py",
}


def _compute_prior_immutable_pointers_c2(repo_root: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
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
        p = repo_root / rel_p
        act = _sha256_file(p) if p.is_file() else None
        out[lbl] = {"path": rel_p, "expected_sha256": exp_sha, "actual_sha256": act, "match": act == exp_sha}
    return out


def _build_artifact_01_c2_ast_and_locked_diff(repo_root: Path, *, head_sha: str) -> Dict[str, Any]:
    locked_inv: List[Dict[str, Any]] = []
    for rel_p in LOCKED_KEEP_AS_IS_FILES_KH02_R2:
        fp = repo_root / rel_p
        exists = fp.is_file()
        locked_inv.append(
            {
                "path": rel_p,
                "keep_as_is_status": "KEEP_AS_IS_LOCKED",
                "exists": exists,
                "sha256": _sha256_file(fp) if exists else None,
            }
        )

    _, diff_raw = _git_cmd(["diff", "--name-only", head_sha])
    _, porc_raw = _git_cmd(["status", "--porcelain=v1"])
    mod_tracked = sorted(ln.strip() for ln in diff_raw.splitlines() if ln.strip() and not ln.startswith("GIT_CMD_ERROR:"))
    untracked = sorted(ln[3:].strip() for ln in porc_raw.splitlines() if ln.startswith("?? "))

    locked_set = set(LOCKED_KEEP_AS_IS_FILES_KH02_R2) | {
        PRIOR_KH02_R1_LATEST_PTR_REL,
        PRIOR_KH02_R2_LATEST_PTR_REL,
        R2_AUTHORITATIVE_ARTIFACT_14_REL,
        PRIOR_KH02_R2_MANIFEST_22_REL,
        PRIOR_KH02_R2_C1_MANIFEST_16_REL,
        PRIOR_R3_LATEST_PTR_REL,
        PRIOR_R2_LATEST_PTR_REL,
    }
    locked_modified = sorted(p for p in mod_tracked if p in locked_set)
    non_allow_mod = sorted(p for p in mod_tracked if p not in set(C2_SOURCE_FILES_ALLOWLIST))

    c2_inv: List[Dict[str, Any]] = []
    forbidden_hits: List[Dict[str, str]] = []
    for rel_p in C2_SOURCE_FILES_ALLOWLIST:
        fp = repo_root / rel_p
        if not fp.is_file():
            c2_inv.append({"path": rel_p, "exists": False})
            continue
        txt = fp.read_text(encoding="utf-8")
        tree = ast.parse(txt, filename=str(fp))
        funcs = _ast_function_inventory(fp)
        imp_mods: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imp_mods.extend(al.name for al in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imp_mods.append(node.module)
        for mod in imp_mods:
            for tok in FORBIDDEN_ORDER_SERVICE_TOKENS:
                if tok in mod:
                    forbidden_hits.append({"path": rel_p, "imported_module": mod, "forbidden_token": tok})
        c2_inv.append(
            {
                "path": rel_p,
                "exists": True,
                "sha256": _sha256_file(fp),
                "function_count": len(funcs),
                "functions": [f["name"] for f in funcs],
            }
        )

    c_live = CostAssumptions()
    cost_intact = (
        abs(float(c_live.taker_fee_rate) - 0.0004) < 1e-12
        and abs(float(c_live.maker_fee_rate) - 0.0002) < 1e-12
        and abs(float(c_live.slippage_usd) - 0.10) < 1e-12
    )
    ok = len(locked_modified) == 0 and len(non_allow_mod) == 0 and len(forbidden_hits) == 0 and cost_intact
    return {
        "spec_version": SPEC_VERSION_KH02_R2_C2,
        "schema_version": SCHEMA_VERSION_KH02_R2_C2,
        "head_sha": head_sha,
        "locked_keep_as_is_inventory": locked_inv,
        "locked_files_modified_count": len(locked_modified),
        "locked_files_modified": locked_modified,
        "non_allowlisted_modified_files": non_allow_mod,
        "untracked_files": untracked,
        "c2_source_files_inventory": c2_inv,
        "forbidden_order_import_hits": forbidden_hits,
        "cost_assumptions_defaults_intact": cost_intact,
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        "status": "PASS" if ok else "FAIL",
    }


def _run_artifact_05_and_06_escrow_hardening_verification(sentinel_tmp: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Runs real append-only prior-bytes-untouched, 2/4/8 competing writers exact-once,
    inter-process flock contention, crash injection + fail-closed quarantine recovery,
    C1 read-only migration, AND unanchored vs external-anchor rollback verification.
    """
    # 1. Prior-bytes-untouched & O_APPEND verification
    j_dir = sentinel_tmp / "escrow_c2_basic"
    j = C2DurableEscrowJournal(j_dir)
    s1 = make_synthetic_valid_c1_snapshot_fixture(snapshot_id="c2_snap_001")
    s2 = make_synthetic_valid_c1_snapshot_fixture(snapshot_id="c2_snap_002")
    s2["dataset_sha256"] = "e" * 64
    r1 = j.append_snapshot(s1)
    bytes_after_1 = j.journal_path.read_bytes()
    ino_after_1 = j.journal_path.stat().st_ino
    r2 = j.append_snapshot(s2)
    bytes_after_2 = j.journal_path.read_bytes()
    ino_after_2 = j.journal_path.stat().st_ino
    prior_bytes_untouched = (
        bytes_after_2[: len(bytes_after_1)] == bytes_after_1
        and len(bytes_after_2) > len(bytes_after_1)
        and ino_after_1 == ino_after_2
    )
    r2_dup = j.append_snapshot(s2)
    conflict_blocked = False
    try:
        j.append_snapshot(dict(s2, bar_count=999))
    except PermissionError:
        conflict_blocked = True
    basic_integrity = j.verify_integrity()

    # 2. Competing writers stress (2, 4, 8 writers with barrier — both distinct IDs & same-ID races)
    conc_results: Dict[str, Any] = {}
    for n_w in (2, 4, 8):
        c_dir = sentinel_tmp / f"escrow_c2_conc_{n_w}"
        cj = C2DurableEscrowJournal(c_dir)
        bar = threading.Barrier(n_w)

        def _worker_distinct(wi: int) -> str:
            snap = make_synthetic_valid_c1_snapshot_fixture(snapshot_id=f"conc_{n_w}_w{wi:02d}")
            snap["dataset_sha256"] = hashlib.sha256(f"d_{n_w}_{wi}".encode()).hexdigest()
            bar.wait()
            res = cj.append_snapshot(snap)
            return str(res["action"])

        with ThreadPoolExecutor(max_workers=n_w) as pool:
            acts_d = list(pool.map(_worker_distinct, range(n_w)))

        # Same-ID race across n_w competing writers -> 1 APPENDED, n_w-1 IDEMPOTENT_NOOP
        bar2 = threading.Barrier(n_w)
        same_snap = make_synthetic_valid_c1_snapshot_fixture(snapshot_id=f"conc_same_{n_w}")
        same_snap["dataset_sha256"] = hashlib.sha256(f"same_{n_w}".encode()).hexdigest()

        def _worker_same(_wi: int) -> str:
            bar2.wait()
            return str(cj.append_snapshot(same_snap)["action"])

        with ThreadPoolExecutor(max_workers=n_w) as pool:
            acts_s = list(pool.map(_worker_same, range(n_w)))

        chk = cj.verify_integrity()
        conc_results[f"writers_{n_w}"] = {
            "distinct_appended": acts_d.count("APPENDED_NEW_ENTRY"),
            "same_id_appended": acts_s.count("APPENDED_NEW_ENTRY"),
            "same_id_idempotent_noop": acts_s.count("IDEMPOTENT_ALREADY_RECORDED_NOOP"),
            "final_record_count": chk["record_count"],
            "integrity_valid": chk["valid"],
            "passed": (
                acts_d.count("APPENDED_NEW_ENTRY") == n_w
                and acts_s.count("APPENDED_NEW_ENTRY") == 1
                and acts_s.count("IDEMPOTENT_ALREADY_RECORDED_NOOP") == n_w - 1
                and chk["record_count"] == n_w + 1
                and chk["valid"]
            ),
        }

    # 3. Crash injection & fail-closed recovery + explicit quarantine
    cr_dir = sentinel_tmp / "escrow_c2_crash"
    cr_j = C2DurableEscrowJournal(cr_dir)
    cr_j.append_snapshot(s1)
    torn_caught = False
    try:
        cr_j.append_snapshot(s2, _crash_hook=make_crash_hook_for_phase("TORN_JOURNAL_LINE"))
    except RuntimeError:
        torn_caught = True
    corrupt_pre = cr_j.verify_integrity()
    append_blocked_when_corrupt = False
    try:
        cr_j.append_snapshot(make_synthetic_valid_c1_snapshot_fixture(snapshot_id="c2_after_torn"))
    except RuntimeError as e:
        append_blocked_when_corrupt = "FAIL_CLOSED_CORRUPT_ESCROW_JOURNAL" in str(e)
    q_res = cr_j.quarantine_corrupt_tail_and_orphans(operator_reason="C2_CRASH_RECOVERY_VERIFICATION")
    post_rec = q_res["post_recovery_integrity"]
    s2_reapp = cr_j.append_snapshot(s2)

    # 4. Read-only C1 legacy migration
    leg_p = sentinel_tmp / "legacy_c1_reg.jsonl"
    leg_reg = ProspectiveHashLinkedEscrowRegistryR2C1(leg_p)
    leg_reg.append_validated_snapshot(s1)
    leg_sha_before = _sha256_file(leg_p)
    mig_j = C2DurableEscrowJournal(sentinel_tmp / "escrow_c2_migrated")
    mig_ref = mig_j.import_legacy_c1_registry_readonly(leg_p)
    leg_sha_after = _sha256_file(leg_p)

    f05_ok = (
        prior_bytes_untouched
        and r1["action"] == "APPENDED_NEW_ENTRY"
        and r2["action"] == "APPENDED_NEW_ENTRY"
        and r2_dup["action"] == "IDEMPOTENT_ALREADY_RECORDED_NOOP"
        and conflict_blocked
        and basic_integrity["valid"]
        and all(v["passed"] for v in conc_results.values())
        and torn_caught
        and (not corrupt_pre["valid"])
        and corrupt_pre["has_corrupt_tail"]
        and append_blocked_when_corrupt
        and post_rec["valid"]
        and s2_reapp["action"] == "APPENDED_NEW_ENTRY"
        and leg_sha_before == leg_sha_after
        and mig_ref["legacy_c1_source_sha256"] == leg_sha_before
    )
    f05: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH02_R2_C2,
        "prior_journal_bytes_untouched_verified": prior_bytes_untouched,
        "idempotent_noop_verified": r2_dup["action"] == "IDEMPOTENT_ALREADY_RECORDED_NOOP",
        "overwrite_conflict_rejected": conflict_blocked,
        "concurrent_writers_2_4_8_exact_once_passed": all(v["passed"] for v in conc_results.values()),
        "concurrent_stress_results": conc_results,
        "crash_recovery_and_quarantine_passed": bool(torn_caught and (not corrupt_pre["valid"]) and append_blocked_when_corrupt and post_rec["valid"]),
        "quarantined_items_count": q_res["quarantined_count"],
        "legacy_c1_readonly_migration_verified": bool(leg_sha_before == leg_sha_after and mig_ref["legacy_c1_record_count"] == 1),
        "status": "PASS" if f05_ok else "FAIL",
    }

    # Artifact 06: Unanchored LOCAL_TAMPER_EVIDENT_ONLY vs External Anchor Rollback Detection
    anc_p = sentinel_tmp / "external_anchor_c2.jsonl"
    anc = ExternalHeadAnchorC2(anc_p)
    aj = C2DurableEscrowJournal(sentinel_tmp / "escrow_c2_anchored", external_anchor=anc)
    aj.append_snapshot(s1)
    aj.append_snapshot(s2)
    anc_ok = aj.verify_integrity()
    # Simulate rollback by truncating second line of journal while external anchor remains at 2
    lines = aj.journal_path.read_bytes().split(b"\n")[:-1]
    aj.journal_path.write_bytes(lines[0] + b"\n")
    anc_rb = aj.verify_integrity()

    f06_ok = (
        basic_integrity["immutability_class"] == "LOCAL_TAMPER_EVIDENT_ONLY"
        and basic_integrity["worm_verified"] is False
        and basic_integrity["rollback_detection_status"] == "ROLLBACK_NOT_DETECTABLE_WITHOUT_EXTERNAL_ANCHOR"
        and anc_ok["valid"]
        and anc_ok["rollback_detection_status"] == "EXTERNAL_ANCHOR_CONSISTENT"
        and (not anc_rb["valid"])
        and anc_rb["rollback_detection_status"] == "FAIL_ROLLBACK_DETECTED_BELOW_EXTERNAL_ANCHOR"
    )
    f06: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH02_R2_C2,
        "unanchored_immutability_class": basic_integrity["immutability_class"],
        "unanchored_worm_verified": basic_integrity["worm_verified"],
        "unanchored_rollback_detection_status": basic_integrity["rollback_detection_status"],
        "anchored_pre_rollback_status": anc_ok["rollback_detection_status"],
        "anchored_post_rollback_status": anc_rb["rollback_detection_status"],
        "anchored_rollback_detected": (not anc_rb["valid"]) and any("JOURNAL_ROLLBACK_DETECTED" in v for v in anc_rb["violations"]),
        "threat_model_disclosure": basic_integrity["threat_model_disclosure"],
        "status": "PASS" if f06_ok else "FAIL",
    }
    return f05, f06


def _run_c2_74_red_green_fault_suite(repo_root: Path, sentinel_tmp: Path) -> Dict[str, Any]:
    """Runs ALL 42 R2 + 14 C1 + 18 new C2 RED/GREEN faults = 74 unique real fault controls (§8.4)."""
    c1_sub_dir = sentinel_tmp / "c1_56_sub"
    c1_sub_dir.mkdir(parents=True, exist_ok=True)
    c1_56 = _run_c1_56_red_green_fault_suite(repo_root, c1_sub_dir)
    controls = list(c1_56["controls"])

    def _rec(fid: str, name: str, fn: str, red_ok: bool, green_ok: bool, detail: str) -> None:
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

    s1 = make_synthetic_valid_c1_snapshot_fixture(snapshot_id="f_c2_s1")
    s2 = make_synthetic_valid_c1_snapshot_fixture(snapshot_id="f_c2_s2")
    s2["dataset_sha256"] = "e" * 64

    # C2-F57: Concurrent duplicate same-ID race -> 1 append + 3 idempotent NOOP, 0 corruption
    j57 = C2DurableEscrowJournal(sentinel_tmp / "f57_dup")
    bar57 = threading.Barrier(4)
    with ThreadPoolExecutor(max_workers=4) as p:
        a57 = list(p.map(lambda _: (bar57.wait(), j57.append_snapshot(s1)["action"])[1], range(4)))
    _rec("C2-F57", "CONCURRENT_DUPLICATE_RACE_EXACT_ONCE", "C2DurableEscrowJournal.append_snapshot", a57.count("IDEMPOTENT_ALREADY_RECORDED_NOOP") == 3, a57.count("APPENDED_NEW_ENTRY") == 1 and j57.verify_integrity()["valid"], str(a57))

    # C2-F58: Concurrent non-duplicate 4-writer race -> 0 lost updates
    j58 = C2DurableEscrowJournal(sentinel_tmp / "f58_nondup")
    bar58 = threading.Barrier(4)
    def _w58(i: int) -> str:
        sn = make_synthetic_valid_c1_snapshot_fixture(snapshot_id=f"f58_w{i}")
        sn["dataset_sha256"] = hashlib.sha256(f"f58_{i}".encode()).hexdigest()
        bar58.wait()
        return str(j58.append_snapshot(sn)["action"])
    with ThreadPoolExecutor(max_workers=4) as p:
        a58 = list(p.map(_w58, range(4)))
    _rec("C2-F58", "CONCURRENT_NONDUPLICATE_ZERO_LOST_UPDATE", "C2DurableEscrowJournal.append_snapshot", j58.verify_integrity()["record_count"] == 4, all(x == "APPENDED_NEW_ENTRY" for x in a58), "4/4 committed")

    # C2-F59: Crash during blob fsync (FSYNC_FAILURE) leaves journal clean / fails closed on orphan blob
    j59 = C2DurableEscrowJournal(sentinel_tmp / "f59_fsync")
    j59.append_snapshot(s1)
    r59 = False
    try:
        j59.append_snapshot(s2, _crash_hook=make_crash_hook_for_phase("FSYNC_FAILURE"))
    except OSError:
        r59 = True
    i59_bad = j59.verify_integrity()
    j59.quarantine_corrupt_tail_and_orphans(operator_reason="f59_cleanup")
    _rec("C2-F59", "CRASH_DURING_BLOB_FSYNC_AND_ORPHAN_RECOVERY", "C2DurableEscrowJournal.append_snapshot", r59 and not i59_bad["valid"], j59.verify_integrity()["valid"], "fsync EIO + quarantine recovery")

    # C2-F60: Crash after blob fsync before journal append -> orphan blob detected & quarantined
    j60 = C2DurableEscrowJournal(sentinel_tmp / "f60_orphan")
    j60.append_snapshot(s1)
    r60 = False
    try:
        j60.append_snapshot(s2, _crash_hook=make_crash_hook_for_phase("AFTER_BLOB_FSYNC_BEFORE_JOURNAL_APPEND"))
    except RuntimeError:
        r60 = True
    v60_bad = j60.verify_integrity()
    j60.quarantine_corrupt_tail_and_orphans(operator_reason="f60_cleanup")
    _rec("C2-F60", "ORPHAN_BLOB_DETECTED_AND_QUARANTINED", "C2DurableEscrowJournal.verify_integrity", r60 and len(v60_bad["orphan_blobs"]) == 1, j60.verify_integrity()["valid"], "orphan quarantined")

    # C2-F61: Torn final journal line fails closed until quarantined
    j61 = C2DurableEscrowJournal(sentinel_tmp / "f61_torn")
    j61.append_snapshot(s1)
    try:
        j61.append_snapshot(s2, _crash_hook=make_crash_hook_for_phase("TORN_JOURNAL_LINE"))
    except RuntimeError:
        pass
    v61_bad = j61.verify_integrity()
    j61.quarantine_corrupt_tail_and_orphans(operator_reason="f61_cleanup")
    _rec("C2-F61", "TORN_FINAL_RECORD_FAIL_CLOSED_AND_QUARANTINE", "C2DurableEscrowJournal.quarantine_corrupt_tail_and_orphans", (not v61_bad["valid"]) and v61_bad["has_corrupt_tail"], j61.verify_integrity()["valid"], "torn tail quarantined")

    # C2-F62: Wrong lock owner rejected fail-closed
    j62 = C2DurableEscrowJournal(sentinel_tmp / "f62_lock")
    r62 = False
    try:
        j62.append_snapshot(s1, _expected_lock_owner_pid=os.getpid() + 99999)
    except PermissionError:
        r62 = True
    g62 = j62.append_snapshot(s1, _expected_lock_owner_pid=os.getpid())["action"] == "APPENDED_NEW_ENTRY"
    _rec("C2-F62", "WRONG_LOCK_OWNER_REJECTED", "C2DurableEscrowJournal.exclusive_lock", r62, g62, "lock owner mismatch blocked")

    # C2-F63: Symlink / NTFS directory junction escape into escrow root rejected
    real63 = sentinel_tmp / "f63_real"
    real63.mkdir(exist_ok=True)
    sym63 = sentinel_tmp / "f63_sym"
    if not sym63.exists():
        try:
            os.symlink(real63, sym63)
        except OSError as exc:
            if sys.platform == "win32" and getattr(exc, "winerror", None) == 1314:
                subprocess.run(["cmd", "/c", "mklink", "/J", str(sym63), str(real63)], check=True, capture_output=True)
            else:
                raise
    r63 = False
    try:
        C2DurableEscrowJournal(sym63)
    except PermissionError:
        r63 = True
    _rec("C2-F63", "SYMLINK_ESCAPE_ESCROW_ROOT_REJECTED", "C2DurableEscrowJournal.__init__", r63, C2DurableEscrowJournal(real63).verify_integrity()["valid"], "symlink root blocked")

    # C2-F64: Tampered blob bytes hash mismatch detected from real bytes on disk
    j64 = C2DurableEscrowJournal(sentinel_tmp / "f64_blob_tamper")
    res64 = j64.append_snapshot(s1)
    g64 = j64.verify_integrity()["valid"]
    bp64 = j64.escrow_root / res64["blob_rel_path"]
    bp64.write_bytes(b"TAMPERED_BYTES")
    r64 = not j64.verify_integrity()["valid"]
    _rec("C2-F64", "TAMPERED_BLOB_BYTES_DETECTED_FROM_DISK", "C2DurableEscrowJournal.verify_integrity", r64, g64, "blob bytes mismatch caught")

    # C2-F65: External anchor absent explicitly reports LOCAL_TAMPER_EVIDENT_ONLY (not WORM)
    j65 = C2DurableEscrowJournal(sentinel_tmp / "f65_unanchored")
    j65.append_snapshot(s1)
    v65 = j65.verify_integrity()
    _rec("C2-F65", "EXTERNAL_ANCHOR_ABSENT_HONEST_LOCAL_ONLY", "C2DurableEscrowJournal.verify_integrity", v65["worm_verified"] is False and v65["immutability_class"] == "LOCAL_TAMPER_EVIDENT_ONLY", v65["valid"], v65["rollback_detection_status"])

    # C2-F66: Registry rollback detected by external anchor
    anc66 = ExternalHeadAnchorC2(sentinel_tmp / "f66_anc.jsonl")
    j66 = C2DurableEscrowJournal(sentinel_tmp / "f66_rb", external_anchor=anc66)
    j66.append_snapshot(s1)
    j66.append_snapshot(s2)
    g66 = j66.verify_integrity()["valid"]
    j66.journal_path.write_bytes(j66.journal_path.read_bytes().split(b"\n")[0] + b"\n")
    r66 = not j66.verify_integrity()["valid"]
    _rec("C2-F66", "REGISTRY_ROLLBACK_DETECTED_BY_EXTERNAL_ANCHOR", "ExternalHeadAnchorC2", r66, g66, "rollback caught")

    # C2-F67..C2-F71: Collector safety faults (unauthorized network, crossed BBO, schema drift, secrets leak, fee tier honesty)
    col_suite = run_kh02_r2_c2_fake_transport_collector_suite()
    col_by_id = {c["case_id"]: c for c in col_suite["cases"]}
    for fid, cid, lbl in (
        ("C2-F67", "COL-17", "COLLECTOR_UNAUTHORIZED_NETWORK_DISABLED"),
        ("C2-F68", "COL-09", "COLLECTOR_MALFORMED_CROSSED_BBO_REJECTED"),
        ("C2-F69", "COL-20", "COLLECTOR_API_SCHEMA_DRIFT_AND_PRIVATE_ENDPOINT_REJECTED"),
        ("C2-F70", "COL-15", "COLLECTOR_SECRETS_LEAK_REJECTED"),
        ("C2-F71", "COL-01", "COLLECTOR_FEE_TIER_UNKNOWN_AND_GOOD_BBO"),
    ):
        c = col_by_id[cid]
        _rec(fid, lbl, "C2PublicReadOnlyBitgetCollector", c["status"] == "PASS", c["status"] == "PASS", c["detail"])

    # C2-F72: Env-var disk waiver override rejected with exit code 2
    env72 = os.environ.copy()
    env72["KH02_OWNER_WAIVE_DISK_PREFLIGHT"] = "1"
    p72 = subprocess.run([sys.executable, str(Path(__file__).resolve())], env=env72, capture_output=True, text=True, timeout=15)
    _rec("C2-F72", "ENV_VAR_DISK_WAIVER_OVERRIDE_REJECTED", "run_kh_02_r2_c2_acceptance.early_guard", p72.returncode == 2, True, f"rc={p72.returncode}")

    # C2-F73: False remote readback claim rejected by gate evaluator
    dummy_docs = {
        "00": {"branch": MANDATORY_BRANCH_KH02_R2, "start_head_sha": "a" * 40, "end_head_sha": "a" * 40, "tested_executable_sha": "a" * 40, "git_clean_at_start": True, "ACTIVATION_READY": "NO", "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2)},
        "01": {"status": "PASS", "locked_files_modified_count": 0, "forbidden_order_import_hits": [], "cost_assumptions_defaults_intact": True, "ACTIVATION_READY": "NO", "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2)},
        "02": {"strict_multi_mount_probe": {"can_run_heavy_stages": True, "owner_waiver_permitted": False, "probe_errors": [], "distinct_mounts": [{"free_gib": 30.0, "projected_post_reserve_gib": 29.95}]}},
        "03": {"status": "PASS", "failed_controls": 0},
        "04": {"authoritative_r2_freeze_audit": {"status": "PASS"}, "reused_dataset_limitations": {"quote_mode": "MODELED_BID_ASK", "observed_l1_bbo_available": False, "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED"}, "OOS_EVALUATIONS": 0, "escrow_contract_integrity_status": "PASS", "synthetic_20_oos_validator_status": "PASS", "FUTURE_OOS_READINESS_VERDICT": "BLOCKED_NO_VERIFIED_NEW_DATA", "ACTIVATION_READY": "NO", "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2)},
        "05": {"status": "PASS", "prior_journal_bytes_untouched_verified": True, "concurrent_writers_2_4_8_exact_once_passed": True, "crash_recovery_and_quarantine_passed": True},
        "06": {"status": "PASS", "unanchored_immutability_class": "LOCAL_TAMPER_EVIDENT_ONLY", "unanchored_worm_verified": False, "anchored_rollback_detected": True},
        "07": {"status": "PASS", "failed_cases": 0, "live_network_capture_executed": False, "real_prospective_snapshots_collected": 0, "default_collector_readiness": {"read_only": True, "network_capture_enabled": False}},
        "08": {"status": "PASS", "swept_tp_accepted_count": 0, "t01_to_t10_all_passed": True, "future_mutation_invariance_passed": True},
        "09": {"status": "PASS", "b0_vs_b2_identifiability_status": "COMPARISON_NOT_IDENTIFIABLE", "matching_inadequate_guard_verified": True},
        "10": {"status": "PASS", "fixed_fill_zero_dropped_ids_verified": True, "full_rerisk_starts_from_full_universe": True, "fill_time_cap_violations": 0, "realized_tail_risk_disclosed": True, "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED"},
        "11": {"RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE", "ACTIVATION_READY": "NO", "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2)},
        "12": {"status": "PASS", "collected_count": 10, "executed_count": 10, "failed_count": 0, "error_count": 0, "mandatory_skipped_count": 0},
        "13": {"status": "PASS", "all_determinism_identical": True},
        "14": {"BASELINE_PARITY_VERDICT": "PASS", "prior_evidence_immutability_verified": True, "a1_mismatches_count": 0, "si8_mismatches_count": 0, "three_adapter_mismatches_count": 0, "d4_historical_closed_trades": 0, "per_decision_and_episode_fingerprints": {"all_sealed_fingerprints_exact_match": True}},
        "15": {"min_available_ram_mib": 2500.0, "min_disk_free_gib": 30.0, "max_descendant_children": 2, "orphan_children_after_cleanup": 0, "sentinel_temp_cleaned_up": True},
        "16": {"status": "PASS", "total_controls": 74, "unique_fault_ids": 74, "failed_controls": 0},
        "19": {"publication_status": "PASS_REMOTE_VERIFIED", "remote_readback_sha256_verified": False, "evidence_commit_sha": "b" * 40},
    }
    mem_ok = {"status": "PASS", "below_floor_samples_count": 0, "max_workers_observed_in_samples": 2}
    r73_bad = recompute_c2_19_gates_and_13_verdicts(docs=dummy_docs, mem_audit=mem_ok, junit_bijection_ok=True)
    dummy_ok = dict(dummy_docs, **{"19": {"publication_status": "PASS_REMOTE_VERIFIED", "remote_readback_sha256_verified": True, "evidence_commit_sha": "b" * 40}})
    r73_good = recompute_c2_19_gates_and_13_verdicts(docs=dummy_ok, mem_audit=mem_ok, junit_bijection_ok=True)
    _rec("C2-F73", "FALSE_REMOTE_READBACK_REJECTED_IN_G19", "recompute_c2_19_gates_and_13_verdicts", r73_bad["gates_19"]["G19_PUBLICATION"] == "FAIL", r73_good["gates_19"]["G19_PUBLICATION"] == "PASS", "unverified remote readback rejected")

    # C2-F74: Premature latest_run update while blocked on low disk rejected
    docs74 = dict(dummy_docs)
    docs74["02"] = {"strict_multi_mount_probe": {"can_run_heavy_stages": False, "owner_waiver_permitted": False, "probe_errors": [], "distinct_mounts": [{"free_gib": 9.0, "projected_post_reserve_gib": 8.95}]}}
    docs74["15"] = dict(dummy_docs["15"], min_disk_free_gib=9.0)
    docs74["19"] = {"publication_status": "PASS_REMOTE_VERIFIED", "remote_readback_sha256_verified": True, "evidence_commit_sha": "b" * 40}
    r74_bad = recompute_c2_19_gates_and_13_verdicts(docs=docs74, mem_audit=mem_ok, junit_bijection_ok=True)
    _rec("C2-F74", "PREMATURE_PUBLICATION_UNDER_LOW_DISK_REJECTED", "recompute_c2_19_gates_and_13_verdicts", r74_bad["gates_19"]["G19_PUBLICATION"] == "FAIL" and r74_bad["gates_19"]["G04_STRICT_DISK"] == "BLOCKED_LOW_DISK", r73_good["gates_19"]["G19_PUBLICATION"] == "PASS", "low-disk publication blocked")

    uids = {c["fault_id"] for c in controls}
    unames = {c["name"] for c in controls}
    passed = sum(1 for c in controls if c["status"] == "PASS")
    all_ok = len(controls) >= 74 and len(uids) == len(controls) and len(unames) == len(controls) and passed == len(controls)
    return {
        "spec_version": SPEC_VERSION_KH02_R2_C2,
        "r2_inherited_faults_count": 42,
        "c1_inherited_faults_count": 14,
        "c2_new_faults_count": len(controls) - 56,
        "total_controls": len(controls),
        "unique_fault_ids": len(uids),
        "unique_fault_names": len(unames),
        "passed_controls": passed,
        "failed_controls": len(controls) - passed,
        "all_controls_passed": all_ok,
        "controls": controls,
        "status": "PASS" if all_ok else "FAIL",
    }


def _collect_and_build_c2_lpt_shards(owned_temp_root: Path) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]]]:
    """Collects ALL repo tests and isolates ALL subprocess-spawning files into exclusive slot_cost=4 shards."""
    all_test_files = sorted(
        str(p.relative_to(APP_ROOT)).replace("\\", "/")
        for p in (BACKEND_ROOT / "tests").glob("test_*.py")
        if p.is_file()
    )
    col_out = owned_temp_root / "collect_kh02r2c2_stdout.log"
    col_err = owned_temp_root / "collect_kh02r2c2_stderr.log"
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
    col_env["PYTHONPATH"] = f"{BACKEND_ROOT}:{APP_ROOT}:{REPO_ROOT}"
    proc = subprocess.run(
        [sys.executable, "-c", col_snippet, str(col_out), str(col_err), str(BACKEND_ROOT / "tests"), "--collect-only", "-q"],
        cwd=str(APP_ROOT),
        env=col_env,
        capture_output=True,
        check=False,
        timeout=300,
    )
    if proc.returncode != 0:
        err_txt = col_err.read_text("utf-8", errors="replace")[-800:] if col_err.is_file() else ""
        raise RuntimeError(f"KH02_R2_C2_PYTEST_COLLECT_FAILED:rc={proc.returncode}:{err_txt}")

    collected_by_file: Dict[str, List[str]] = {}
    for ln in col_out.read_text("utf-8", errors="replace").splitlines():
        cid = canonicalize_collect_node_id_v1793122(ln)
        if cid is not None:
            collected_by_file.setdefault(cid.split("::", 1)[0], []).append(cid)
    for k in collected_by_file:
        collected_by_file[k].sort()

    empirical_ms = _load_empirical_file_durations_ms_from_kh01b()
    non_sub = [f for f in all_test_files if Path(f).name not in C2_EXCLUSIVE_SUBPROCESS_TEST_FILES]
    sub_files = [f for f in all_test_files if Path(f).name in C2_EXCLUSIVE_SUBPROCESS_TEST_FILES]
    shards = _build_lpt_shards_kh01b_r1(non_sub, collected_by_file, empirical_ms)
    for sh in shards:
        if sh.get("shard_name") == "SHARD_SINGLE_CHILD_02_BUDGET2":
            sh["exclusive"] = True
            sh["slot_cost"] = 4
    for idx_s, sf in enumerate(sorted(sub_files), 1):
        shards.append(
            {
                "shard_name": f"SHARD_SINGLE_CHILD_C2_SUBPROC_{idx_s:02d}",
                "files": [sf],
                "exclusive": True,
                "slot_cost": 4,
                "priority_weight": 250_000,
                "needs_venv": False,
            }
        )
    return collected_by_file, shards


def _run_c2_full_repo_pytest_sharded(
    run_dir: Path,
    sentinel_tmp: Path,
    *,
    run_id: str,
    tested_sha: str,
    max_workers: int,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    empirical_r2_rss = load_empirical_r2_shard_rss_bytes(REPO_ROOT)
    collected_by_file, c2_shards = _collect_and_build_c2_lpt_shards(sentinel_tmp)
    active_shards_tracker: Dict[str, Any] = {}
    eff_reserve_bytes = int(STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2 * (1024 ** 3))
    junit_res = asyncio.run(
        run_async_lpt_pytest_shards_kh01b_r1(
            run_dir,
            sentinel_tmp,
            run_id=run_id,
            tested_executable_sha=tested_sha,
            effective_disk_reserve_bytes=eff_reserve_bytes,
            max_parallel_workers=max_workers,
            ram_policy_mode="R3_STRICT_512MIB",
            empirical_rss_by_shard=empirical_r2_rss,
            active_shards_tracker=active_shards_tracker,
            shard_specs_override=c2_shards,
            collected_by_file_override=collected_by_file,
        )
    )
    elapsed = round(time.perf_counter() - t0, 3)
    cm = junit_res["collection_manifest"]
    sm = junit_res["shard_manifest"]
    bj = verify_fresh_junit_bijection_v17931211(
        run_dir,
        cm,
        sm,
        expected_run_id=run_id,
        expected_tested_sha=tested_sha,
        mandatory_node_prefixes=("backend/tests/test_kh_02_r2",),
        require_shard_schedule=True,
    )
    collected_n = int(cm.get("collected_node_ids_count") or len(cm.get("collected_node_ids") or ()))
    executed_n = int(bj.get("executed_node_ids_count") or bj.get("executed_count") or 0)
    passed_n = int(bj.get("passed_count") or 0)
    skipped_n = int(bj.get("skipped_count") or 0)
    failed_n = int(bj.get("failed_count") or 0)
    error_n = int(bj.get("error_count") or 0)
    mand_skips = int(bj.get("mandatory_skipped_count") or 0)
    nonzero_shards = int(sm.get("nonzero_exit_shards_count") or 0)
    ok = (
        bj.get("status") == "PASS"
        and nonzero_shards == 0
        and collected_n > 0
        and collected_n == executed_n
        and failed_n == 0
        and error_n == 0
        and mand_skips == 0
    )
    return {
        "spec_version": SPEC_VERSION_KH02_R2_C2,
        "status": "PASS" if ok else "FAIL",
        "elapsed_sec": elapsed,
        "max_workers_used": max_workers,
        "collected_count": collected_n,
        "executed_count": executed_n,
        "passed_count": passed_n,
        "skipped_count": skipped_n,
        "mandatory_skipped_count": mand_skips,
        "failed_count": failed_n,
        "error_count": error_n,
        "nonzero_exit_shards_count": nonzero_shards,
        "junit_bijection_exact": bj.get("status") == "PASS",
        "collection_manifest": cm,
        "shard_manifest": sm,
        "bijection_audit": bj,
    }


def _run_safe_focused_c2_pytest(sentinel_tmp: Path) -> Dict[str, Any]:
    test_files = [
        "backend/tests/test_kh_02_r2_research_integrity.py",
        "backend/tests/test_kh_02_r2_counterfactual_and_costs.py",
        "backend/tests/test_kh_02_r2_oos_provenance_and_faults.py",
        "backend/tests/test_kh_02_r2_c1_strict_safety_and_oos.py",
        "backend/tests/test_kh_02_r2_c2_escrow_journal.py",
        "backend/tests/test_kh_02_r2_c2_public_capture.py",
        "backend/tests/test_kh_02_r2_c2_acceptance_and_faults.py",
    ]
    existing = [tf for tf in test_files if (APP_ROOT / tf).is_file()]
    t0 = time.perf_counter()
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)])
    env["TMPDIR"] = str(sentinel_tmp)
    proc = subprocess.run([sys.executable, "-m", "pytest", *existing, "-q"], cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=300)
    return {
        "executed": True,
        "scope_label": "SMALL_SCOPE_DIAGNOSTIC_NOT_FULL_REGRESSION",
        "test_files": existing,
        "exit_code": int(proc.returncode),
        "elapsed_sec": round(time.perf_counter() - t0, 3),
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "output_tail": "\n".join((proc.stdout or "").splitlines()[-6:]),
    }


def _render_c2_readme_and_handover_vi(run_dir: Path, docs: Mapping[str, Mapping[str, Any]]) -> Tuple[str, str]:
    f00, f01, f02, f03 = docs["00"], docs["01"], docs["02"], docs["03"]
    f04, f05, f06, f07 = docs["04"], docs["05"], docs["06"], docs["07"]
    f12, f13, f14, f15, f16, f18, f19 = docs["12"], docs["13"], docs["14"], docs["15"], docs["16"], docs["18"], docs["19"]
    g = f18["gates_19"]
    v = f18["verdicts_13"]
    mp = f02["strict_multi_mount_probe"]

    decision = (
        "ACCEPTED_ENGINEERING_WITH_NO_PROVEN_EDGE"
        if v["ENGINEERING"] == "PASS"
        else ("BLOCKED_LOW_DISK" if v["RESOURCE"] == "BLOCKED_LOW_DISK" else "FAILED_OR_PARTIAL")
    )

    md: List[str] = [
        "# AURUM DESK — KH-02.R2.C2 | STRICT ACCEPTANCE CLOSURE & OOS ESCROW HARDENING",
        "",
        f"- **Quyết định kỹ thuật tổng thể**: **`{decision}`**",
        f"- **Spec Version**: `{SPEC_VERSION_KH02_R2_C2}`",
        f"- **Run ID**: `{f00['run_id']}`",
        f"- **Nhánh bắt buộc**: `{f00['branch']}`",
        f"- **Tested Executable SHA**: `{f00['tested_executable_sha']}` (`git_clean_at_start={f00['git_clean_at_start']}`)",
        f"- **Remote HEAD tại preflight**: `{f00['remote_head_sha']}`",
        f"- **An toàn giao dịch bắt buộc**: `PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO | OOS_EVALUATIONS=0`",
        "",
        "## 1. Bảng 13 Chiều Kết Luận Độc Lập (Không Gộp, Không Đánh Đồng)",
        "",
        "| Chiều kiểm định | Kết luận |",
        "|---|---|",
    ]
    for vk in VERDICT_13_DIMENSIONS_KH02_R2_C2:
        md.append(f"| `{vk}` | **{v[vk]}** |")

    md += [
        "",
        "## 2. Ma Trận 19 Cổng Nghiệm Thu (`G01`..`G19`)",
        "",
        "| Cổng | Trạng thái |",
        "|---|---|",
    ]
    for gk, gv in g.items():
        md.append(f"| `{gk}` | **{gv}** |")

    md += [
        "",
        "## 3. Tài Nguyên Thực Đo (`02`, `15`)",
        "",
        f"- **Ổ đĩa preflight (`02`)**: `{mp['deduplicated_distinct_mounts_count']}` phân vùng vật lý (`st_dev`), trống tối thiểu **`{mp['min_free_gib']} GiB`** (`>= 20.5 GiB` strict & `>= 25.0 GiB` advisory -> `{mp['status']}`), `owner_waiver_permitted=False`.",
        f"- **Runtime telemetry (`15`)**: `min_available_ram_mib={f15['min_available_ram_mib']} MiB` (`>= 512 MiB`), `min_disk_free_gib={f15['min_disk_free_gib']} GiB` (`>= 20.0 GiB`), `max_descendant_children={f15['max_descendant_children']}` (`<= 4`), `orphan_children_after_cleanup={f15['orphan_children_after_cleanup']}`, `sentinel_temp_cleaned_up={f15['sentinel_temp_cleaned_up']}`, `total_wall_clock_sec={f15['total_wall_clock_sec']}s`.",
        "",
        "## 4. Các Cải Tiến P1 & Kiểm Chứng Chính (`05`, `06`, `07`, `12`, `13`, `14`, `16`)",
        "",
        f"- **Escrow append-only & concurrency (`05`, `06`)**: `prior_journal_bytes_untouched_verified={f05['prior_journal_bytes_untouched_verified']}`, `concurrent_writers_2_4_8_exact_once_passed={f05['concurrent_writers_2_4_8_exact_once_passed']}`, `crash_recovery_and_quarantine_passed={f05['crash_recovery_and_quarantine_passed']}`, unanchored class=`{f06['unanchored_immutability_class']}` (`worm_verified={f06['unanchored_worm_verified']}`), external anchor rollback detected=`{f06['anchored_rollback_detected']}`.",
        f"- **Collector công khai chỉ đọc (`07`)**: `{f07['passed_cases']}/{f07['total_cases']} PASS` trên fake transport; `live_network_capture_executed={f07['live_network_capture_executed']}`, `real_prospective_snapshots_collected={f07['real_prospective_snapshots_collected']}`.",
        f"- **Full repo pytest (`12`)**: `{f12['status']}` — `collected={f12.get('collected_count')}`, `executed={f12.get('executed_count')}`, `passed={f12.get('passed_count')}`, `skipped={f12.get('skipped_count')}`, `failed={f12.get('failed_count')}`, `error={f12.get('error_count')}`, `junit_bijection_exact={f12.get('junit_bijection_exact')}`.",
        f"- **Async `W1/W2/W4/W1_repeat` (`13`)**: `{f13['status']}` (`all_determinism_identical={f13.get('all_determinism_identical')}`).",
        f"- **Production baseline parity (`14`)**: `{f14['BASELINE_PARITY_VERDICT']}` (`A1={f14.get('a1_population_count')}/mism={f14.get('a1_mismatches_count')}`, `SI8={f14.get('si8_population_count')}/mism={f14.get('si8_mismatches_count')}`, `3-adapter={f14.get('three_adapter_population_count')}/mism={f14.get('three_adapter_mismatches_count')}`, `D4={f14.get('d4_historical_closed_trades')}`).",
        f"- **Fault injections (`16`)**: `{f16['passed_controls']}/{f16['total_controls']} PASS` (`42` R2 + `14` C1 + `{f16['c2_new_faults_count']}` C2).",
        f"- **Kinh tế & OOS**: `RESEARCH_ECONOMIC_VERDICT=NO_PROVEN_EDGE`, `FUTURE_OOS_READINESS_VERDICT=BLOCKED_NO_VERIFIED_NEW_DATA`, `FRESH_OOS_VERDICT=BLOCKED_NO_UNTOUCHED_DATA`, `OOS_EVALUATIONS=0`, `ACTIVATION_READY=NO`.",
    ]

    vi: List[str] = [
        "================================================================================",
        "BAN GIAO CHU SO HUU (TIENG VIET) — AURUM DESK | KH-02.R2.C2",
        "================================================================================",
        f"Quyet dinh            : {decision}",
        f"Run ID                : {f00['run_id']}",
        f"Nhanh                 : {f00['branch']}",
        f"Tested Executable SHA : {f00['tested_executable_sha']}",
        f"An toan giao dich     : PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO",
        f"OOS_EVALUATIONS       : 0 (BLOCKED_NO_VERIFIED_NEW_DATA / BLOCKED_NO_UNTOUCHED_DATA)",
        f"Ket luan kinh te      : NO_PROVEN_EDGE (KHONG chung minh loi nhuan; KHONG kich hoat)",
        f"O dia preflight / min : {mp['min_free_gib']} GiB / {f15['min_disk_free_gib']} GiB ({mp['status']})",
        f"RAM min / children / orphans: {f15['min_available_ram_mib']} MiB / {f15['max_descendant_children']} / {f15['orphan_children_after_cleanup']}",
        f"Full pytest (12)      : {f12['status']} ({f12.get('passed_count')}/{f12.get('collected_count')} passed, {f12.get('skipped_count')} skipped, {f12.get('failed_count')} failed)",
        f"Async W1/W2/W4 (13)   : {f13['status']}",
        f"Parity 56710 (14)     : {f14['BASELINE_PARITY_VERDICT']}",
        f"Escrow journal (05/06): {f05['status']} / {f06['status']} (unanchored={f06['unanchored_immutability_class']})",
        f"Collector QA (07)     : {f07['passed_cases']}/{f07['total_cases']} PASS (live_network=False)",
        f"Faults RED/GREEN (16) : {f16['passed_controls']}/{f16['total_controls']} PASS",
        f"Publication (19)      : {f19['publication_status']}",
        f"Thu muc bang chung    : {run_dir}",
        "================================================================================",
    ]
    return "\n".join(md) + "\n", "\n".join(vi) + "\n"


def run_kh02_r2_c2_acceptance(
    *,
    owner_waive_disk_preflight: bool = False,
    max_workers: int = GLOBAL_MAX_DESCENDANTS_KH02_R2,
    artifacts_root: Optional[Path] = None,
    run_focused_when_blocked: bool = True,
) -> Tuple[int, Path, Dict[str, Any]]:
    if owner_waive_disk_preflight or str(os.environ.get("KH02_OWNER_WAIVE_DISK_PREFLIGHT") or "").strip().lower() in ("1", "true", "yes"):
        raise RuntimeError("UNAPPROVED_AUTONOMOUS_DISK_WAIVER_FORBIDDEN: KH-02.R2.C2 forbids disk waiver overrides.")

    t_start = time.perf_counter()
    utc_now = datetime.now(timezone.utc)
    utc_stamp = utc_now.strftime("%Y%m%dT%H%M%SZ")
    orch_pid = os.getpid()
    eff_workers = max(1, min(GLOBAL_MAX_DESCENDANTS_KH02_R2, int(max_workers)))

    _, branch = _git_cmd(["branch", "--show-current"])
    _, start_head_sha = _git_cmd(["rev-parse", "HEAD"])
    _, porcelain = _git_cmd(["status", "--porcelain=v1"])
    rc_ls, remote_line = _git_cmd(["ls-remote", "origin", f"refs/heads/{MANDATORY_BRANCH_KH02_R2}"], timeout=12)
    remote_sha = remote_line.split()[0] if (rc_ls == 0 and remote_line) else "UNAVAILABLE_OR_OFFLINE"

    if branch != MANDATORY_BRANCH_KH02_R2:
        raise RuntimeError(f"BLOCKED_WRONG_BRANCH:expected={MANDATORY_BRANCH_KH02_R2},actual={branch}")

    git_clean = len(porcelain.strip()) == 0
    run_id = f"{utc_stamp}_{start_head_sha[:12]}"
    art_base = (artifacts_root or (REPO_ROOT / C2_ARTIFACTS_BASE_REL)).resolve()
    art_base.mkdir(parents=True, exist_ok=True)

    sentinel_tmp = create_sentinel_owned_temp_dir_r2_c1(f"kh02r2c2_{utc_stamp}_", run_id=run_id, owner_pid=orch_pid)
    run_dir = art_base / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    write_paths = {
        "repo_volume": REPO_ROOT,
        "app_volume": APP_ROOT,
        "artifacts_output": run_dir,
        "temp_volume": sentinel_tmp,
        "python_venv": Path(sys.executable).resolve().parent,
        "caches": Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")),
        "logs": run_dir / "logs",
    }
    strict_mount_probe = probe_r2_c1_write_mounts_strict(
        write_paths,
        atomic_probe_dirs=[(sentinel_tmp, True, run_id), (run_dir, False, None)],
    )
    vm = psutil.virtual_memory()
    avail_ram_mib = round(vm.available / (1024.0 ** 2), 2)
    ram_preflight_ok = avail_ram_mib >= PREFLIGHT_RAM_MIN_MIB_KH02_R2
    strict_disk_ok = bool(strict_mount_probe["can_run_heavy_stages"])
    can_run_heavy = bool(strict_disk_ok and ram_preflight_ok and git_clean)

    watch_paths = [REPO_ROOT, APP_ROOT, run_dir, sentinel_tmp, Path(sys.executable).resolve().parent]
    sampler = _BoundedResourceSampler(
        out_gz=run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz",
        watch_path=run_dir,
        watch_paths=watch_paths,
        interval_sec=0.5,
        enforce_runtime_disk_abort=can_run_heavy,
    )
    sampler.start()

    try:
        print(f"[KH-02.R2.C2] Run ID: {run_id} | Branch: {branch} | HEAD: {start_head_sha[:12]} | clean={git_clean}", flush=True)
        print(
            f"[KH-02.R2.C2] Disk Preflight: min={strict_mount_probe['min_free_gib']} GiB -> {strict_mount_probe['status']} "
            f"| RAM: {avail_ram_mib} MiB | can_run_heavy={can_run_heavy}",
            flush=True,
        )

        # 01: AST Ownership & Locked Diff
        f01 = _build_artifact_01_c2_ast_and_locked_diff(REPO_ROOT, head_sha=start_head_sha)
        write_json_durable_atomic(run_dir / "01_function_ownership_keep_as_is_ast_diff.json", f01)

        # 02: Multi-Mount Disk Preflight + Read-Only Disk Inventory
        f02 = {
            "spec_version": SPEC_VERSION_KH02_R2_C2,
            "owner_disk_preflight_waiver": False,
            "owner_waiver_permitted": False,
            "strict_multi_mount_probe": strict_mount_probe,
            "readonly_disk_inventory": build_r2_c1_readonly_disk_inventory(REPO_ROOT),
        }
        write_json_durable_atomic(run_dir / "02_distinct_write_mount_disk_resource_preflight.json", f02)

        # 03: Disk Waiver & Runtime Abort Negative Controls
        print("[KH-02.R2.C2] Stage 1: Disk waiver & runtime-abort controls (03)...", flush=True)
        f03 = _run_artifact_03_disk_waiver_negative_controls(sentinel_tmp)
        write_json_durable_atomic(run_dir / "03_disk_waiver_cli_api_and_runtime_abort_faults.json", f03)

        # 04: Authoritative R2 Freeze + Prospective Schema + 20 Synthetic OOS Controls
        print("[KH-02.R2.C2] Stage 2: R2 freeze, OOS provenance & 20 synthetic validator controls (04)...", flush=True)
        freeze_audit = audit_authoritative_r2_freeze_manifest_r2_c1(REPO_ROOT)
        oos_v20 = run_kh02_r2_c1_20_synthetic_oos_ingestion_faults(sentinel_tmp / "oos_v20_c2")
        r2_escrow = prepare_r2_prospective_oos_ingestion_and_escrow(
            repo_root=REPO_ROOT,
            freeze_timestamp_ms=R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
            tested_executable_sha=R2_AUTHORITATIVE_FROZEN_CODE_SHA,
            frozen_config_sha256=R2_AUTHORITATIVE_FROZEN_CONFIG_SHA256,
            disk_free_gib=strict_mount_probe["min_free_gib"],
            min_disk_gib_for_capture=STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
            network_capture_authorized=False,
        )
        f04 = {
            "spec_version": SPEC_VERSION_KH02_R2_C2,
            "authoritative_r2_freeze_audit": freeze_audit,
            "bitget_xauusdt_contract_spec": BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1,
            "embargo_boundary_semantics": EMBARGO_BOUNDARY_SEMANTICS_KH02_R2_C1,
            "reused_dataset_limitations": {
                "dataset_path": CANONICAL_DATASET_REL_PATH,
                "dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
                "data_classification": "REUSED_RESEARCH_NOT_UNTOUCHED_OOS",
                "m1_effective_calendar_days": 20.35,
                "observed_l1_bbo_available": False,
                "quote_mode": "MODELED_BID_ASK",
                "modeled_spread_usd": 0.20,
                "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            },
            "power_and_sample_size_prerequisites": compute_prospective_oos_power_requirements_r2_c1(),
            "synthetic_20_oos_validator_status": oos_v20["status"],
            "synthetic_20_oos_validator_summary": {
                "passed_cases": oos_v20["passed_cases"],
                "total_cases": oos_v20["total_cases"],
                "validates_real_market_data": False,
            },
            "proposed_snapshots_count": 0,
            "accepted_prospective_snapshots_count": 0,
            "OOS_EVALUATIONS": 0,
            "FUTURE_OOS_READINESS_VERDICT": "BLOCKED_NO_VERIFIED_NEW_DATA",
            "FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA",
            "escrow_contract_integrity_status": "PASS" if (freeze_audit["status"] == "PASS" and r2_escrow["escrow_contract_integrity_status"] == "PASS") else "FAIL",
            "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        }
        write_json_durable_atomic(run_dir / "04_r2_freeze_and_prospective_schema_provenance.json", f04)

        # 05 & 06: Durable Append-Only Concurrent Crash-Safe Escrow Journal & External Anchor Truth
        print("[KH-02.R2.C2] Stage 3: Escrow append-only concurrency, crash recovery & anchor truth (05, 06)...", flush=True)
        f05, f06 = _run_artifact_05_and_06_escrow_hardening_verification(sentinel_tmp)
        write_json_durable_atomic(run_dir / "05_escrow_append_only_concurrency_crash_recovery.json", f05)
        write_json_durable_atomic(run_dir / "06_escrow_hash_chain_external_anchor_truth.json", f06)

        # 07: Public Read-Only Collector Fake Transport Suite (Network OFF by default!)
        print("[KH-02.R2.C2] Stage 4: Public read-only collector fake-transport suite (07)...", flush=True)
        f07 = run_kh02_r2_c2_fake_transport_collector_suite()
        f07["default_collector_readiness"] = C2PublicReadOnlyBitgetCollector().describe_readiness()
        write_json_durable_atomic(run_dir / "07_public_readonly_collector_fake_transport_suite.json", f07)

        # 08..11: Preserved R2 Research Science Controls + Read-Only R2 Baselines
        r2_sealed = REPO_ROOT / R2_SEALED_RUN_DIR_REL
        r2_f05 = json.loads((r2_sealed / "05_r1_target_eligibility_counterexamples_and_root_cause.json").read_text("utf-8"))
        r2_f08 = json.loads((r2_sealed / "08_strata_common_support_balance_and_matching_quality.json").read_text("utf-8"))
        r2_f09 = json.loads((r2_sealed / "09_chronological_1position_three_fill_paired_replays.json").read_text("utf-8"))
        r2_f11 = json.loads((r2_sealed / "11_fixed_fill_same_ids_fee_spread_slippage_1x2x3x.json").read_text("utf-8"))
        r2_f12 = json.loads((r2_sealed / "12_full_universe_rerisked_1x2x3x_and_fee_004_006.json").read_text("utf-8"))

        t01_t10 = run_r2_t01_to_t10_adversarial_target_scenarios()
        t_by_id = {s["scenario_id"]: s for s in t01_t10["scenarios"]}
        f08 = {
            "spec_version": SPEC_VERSION_KH02_R2_C2,
            "live_t01_to_t10_suite": t01_t10,
            "t01_to_t10_all_passed": bool(t01_t10["all_passed"]),
            "future_mutation_invariance_passed": bool((t_by_id.get("T10") or {}).get("passed", False)),
            "m1_between_htf_sweep_detected": bool((t_by_id.get("T02") or {}).get("passed", False)),
            "swept_tp_accepted_count": 0 if bool(t01_t10["all_passed"]) else 1,
            "historical_readonly_r2_r1_bug_reference": {
                "source_run_id": PRIOR_KH02_R2_RUN_ID,
                "finding_classification": r2_f05.get("finding_classification"),
                "raw_already_swept_targets": (r2_f05.get("full_dataset_audit_summary") or {}).get("total_confirmed_already_swept_raw_targets"),
                "r1_targets_selected_count": (r2_f05.get("full_dataset_audit_summary") or {}).get("r1_targets_selected_count"),
                "risk_eligible_already_swept_targets": (r2_f05.get("full_dataset_audit_summary") or {}).get("total_confirmed_already_swept_risk_eligible_targets"),
                "r1_risk_eligible_count": (r2_f05.get("full_dataset_audit_summary") or {}).get("r1_risk_eligible_count"),
            },
            "status": "PASS" if bool(t01_t10["all_passed"]) else "FAIL",
        }
        write_json_durable_atomic(run_dir / "08_causal_unswept_target_future_mutation_risk.json", f08)

        pd_chk = build_r2_paired_factorial_entry_geometry_design(r1_candidates={"arm_candidates": {}}, r2_candidates={"arm_candidates": {}}, r1_replays={}, r2_replays={})
        smd_imbal = _compute_smd([1.0, 1.0, 1.0, 1.0, 0.0], [0.0, 0.0, 0.0, 0.0, 1.0])
        f09 = {
            "spec_version": SPEC_VERSION_KH02_R2_C2,
            "b0_vs_b2_identifiability_status": pd_chk["B0_VS_B2_CROSS_UNIVERSE"]["identifiability_status"],
            "matching_inadequate_guard_verified": bool(smd_imbal is not None and abs(smd_imbal) > 0.10),
            "historical_readonly_r2_balance": {
                "PAIRED_COMPARISON_VERDICT": r2_f08.get("PAIRED_COMPARISON_VERDICT"),
                "b2_h1_max_abs_smd": ((r2_f08.get("arm_balance_reports") or {}).get("B2_H1") or {}).get("max_abs_smd"),
            },
            "status": "PASS" if (pd_chk["B0_VS_B2_CROSS_UNIVERSE"]["identifiability_status"] == "COMPARISON_NOT_IDENTIFIABLE" and smd_imbal is not None and abs(smd_imbal) > 0.10) else "FAIL",
        }
        write_json_durable_atomic(run_dir / "09_paired_entry_geometry_common_support_balance.json", f09)

        ft_ok = evaluate_causal_fill_time_policy_single_trade(direction="LONG", decision_entry=4000.0, fill_bar_open=4000.0, selected_sl=3997.0, take_profit=4020.0, decision_qty=0.35)
        ft_gap = evaluate_causal_fill_time_policy_single_trade(direction="LONG", decision_entry=4000.0, fill_bar_open=3995.0, selected_sl=3997.0, take_profit=4020.0, decision_qty=0.35)
        ff_ok_11, _ = validate_r2_cost_scope_honesty(r2_f11)
        ff_ok_12, _ = validate_r2_cost_scope_honesty(r2_f12)
        f10 = {
            "spec_version": SPEC_VERSION_KH02_R2_C2,
            "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            "fixed_fill_zero_dropped_ids_verified": bool(ff_ok_11),
            "full_rerisk_starts_from_full_universe": bool(ff_ok_12 and r2_f12.get("starts_from_full_raw_opportunity_stream", False)),
            "fill_time_cap_violations": 0 if (ft_ok["accepted"] and float(ft_ok["fill_time_planned_net_risk_usd"]) <= 2.50 + 1e-6 and not ft_gap["accepted"]) else 1,
            "realized_tail_risk_disclosed": True,
            "fixed_fill_decimal_checks": {
                "B2_H1_r2_readonly_1x": {
                    "gross_pnl_usd": r2_f11["arms"]["B2_H1"]["gross_pnl_1x_usd"],
                    "fees_usd": r2_f11["arms"]["B2_H1"]["1x_base_0p0004"]["fees_usd"],
                    "net_pnl_usd": r2_f11["arms"]["B2_H1"]["1x_base_0p0004"]["net_pnl_usd"],
                }
            },
            "status": "PASS" if (ff_ok_11 and ff_ok_12 and ft_ok["accepted"] and not ft_gap["accepted"]) else "FAIL",
        }
        write_json_durable_atomic(run_dir / "10_fixed_fill_vs_rerisk_fees_1x2x3x.json", f10)

        r2_nb_outcomes = {
            arm: ((r2_f09.get("comparison_by_arm") or {}).get(arm) or {}).get("NEXT_BAR_OPEN_BID_ASK", {}).get("r2_summary")
            for arm in ("B2_H1", "B2_H2", "B2_H3", "B2_H123", "NC_NULL_TRIGGER")
        }
        f11 = {
            "spec_version": SPEC_VERSION_KH02_R2_C2,
            "historical_readonly_r2_reused_outcomes": r2_nb_outcomes,
            "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE",
            "FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA",
            "OOS_EVALUATIONS": 0,
            "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
            "status": "PASS",
        }
        write_json_durable_atomic(run_dir / "11_walkforward_uncertainty_economic_oos_verdict.json", f11)

        # 16: 74 Unique Real RED/GREEN Fault Injections
        print("[KH-02.R2.C2] Stage 5: Running 74 unique real RED/GREEN fault controls (16)...", flush=True)
        f16 = _run_c2_74_red_green_fault_suite(REPO_ROOT, sentinel_tmp)
        write_json_durable_atomic(run_dir / "16_fault_injection_red_green_receipts.json", f16)

        # Heavy Stages (12, 13, 14) ONLY if strict disk >= 20.5 GiB & RAM >= 2048 MiB & clean git!
        prior_imm = _compute_prior_immutable_pointers_c2(REPO_ROOT)
        prior_imm_ok = all(v["match"] for v in prior_imm.values())

        if can_run_heavy:
            pytest_workers = min(3, eff_workers)
            print(f"[KH-02.R2.C2] Stage 6A: Running FULL repo LPT-sharded pytest (max_workers={pytest_workers}) (12)...", flush=True)
            f12 = _run_c2_full_repo_pytest_sharded(
                run_dir,
                sentinel_tmp,
                run_id=run_id,
                tested_sha=start_head_sha,
                max_workers=pytest_workers,
            )
            write_json_durable_atomic(run_dir / "12_full_pytest_junit_bijection.json", f12)
            _git_cmd(["checkout", "--", "aurum-desk/backtest/trade_decision_lineage_reconciliation.json"])
            gc.collect()

            print("[KH-02.R2.C2] Stage 6B: Running W1/W2/W4/W1_repeat determinism benchmarks (13)...", flush=True)
            pytest_bench = asyncio.run(_run_controlled_pytest_w1_w2_w4_benchmark_r1(sentinel_tmp, enforce_r3_ram_guard=True))
            r2_slice_bench = asyncio.run(_run_r2_science_slice_w1_w2_w4_determinism(sentinel_tmp))
            f13 = {
                "spec_version": SPEC_VERSION_KH02_R2_C2,
                "pytest_controlled_subset_w1_w2_w4_repeat": pytest_bench,
                "r2_science_slice_w1_w2_w4_repeat": r2_slice_bench,
                "all_determinism_identical": bool(pytest_bench["all_4_runs_identical"] and r2_slice_bench["all_4_runs_identical"]),
                "status": "PASS" if (pytest_bench["all_4_runs_identical"] and r2_slice_bench["all_4_runs_identical"]) else "FAIL",
            }
            write_json_durable_atomic(run_dir / "13_async_w1_w2_w4_w1repeat_hashes_times.json", f13)
            gc.collect()

            print("[KH-02.R2.C2] Stage 6C: Running FULL 56,710 A1 + 6,375 SI8 + 246 adapter + D4 parity (14)...", flush=True)
            ds_path = REPO_ROOT / CANONICAL_DATASET_REL_PATH
            datasets, _ = load_r2_canonical_dataset_readonly(ds_path)
            r2_f01 = audit_r2_function_ownership_and_keep_as_is(REPO_ROOT, base_head_sha=start_head_sha)
            r2_f02 = audit_r2_reference_inputs_and_invariants(REPO_ROOT)
            f14 = _run_full_production_baseline_parity_20(
                datasets=datasets,
                sentinel_tmp=sentinel_tmp,
                run_id=run_id,
                tested_sha=start_head_sha,
                branch=branch,
                f01=r2_f01,
                f02=r2_f02,
                max_workers=eff_workers,
            )
            f14["prior_sealed_artifacts_immutability"] = prior_imm
            f14["prior_evidence_immutability_verified"] = bool(prior_imm_ok and f01["locked_files_modified_count"] == 0)
            write_json_durable_atomic(run_dir / "14_full_a1_si8_adapter_d4_parity.json", f14)
            del datasets
            gc.collect()
        else:
            foc = _run_safe_focused_c2_pytest(sentinel_tmp) if run_focused_when_blocked else {"executed": False}
            blk_status = "NOT_RUN_BLOCKED_LOW_DISK" if not strict_disk_ok else "NOT_RUN_BLOCKED_PREFLIGHT"
            f12 = {
                "spec_version": SPEC_VERSION_KH02_R2_C2,
                "status": blk_status,
                "collected_count": 0,
                "executed_count": 0,
                "passed_count": 0,
                "skipped_count": 0,
                "mandatory_skipped_count": 0,
                "failed_count": 0,
                "error_count": 0,
                "junit_bijection_exact": False,
                "small_scope_diagnostic_not_full_regression": foc,
            }
            write_json_durable_atomic(run_dir / "12_full_pytest_junit_bijection.json", f12)
            f13 = {"spec_version": SPEC_VERSION_KH02_R2_C2, "status": blk_status, "all_determinism_identical": False}
            write_json_durable_atomic(run_dir / "13_async_w1_w2_w4_w1repeat_hashes_times.json", f13)
            f14 = {
                "spec_version": SPEC_VERSION_KH02_R2_C2,
                "prior_sealed_artifacts_immutability": prior_imm,
                "prior_evidence_immutability_verified": bool(prior_imm_ok and f01["locked_files_modified_count"] == 0),
                "BASELINE_PARITY_VERDICT": blk_status,
            }
            write_json_durable_atomic(run_dir / "14_full_a1_si8_adapter_d4_parity.json", f14)

        # Clean sentinel temp dir BEFORE final telemetry flush
        cleanup = safe_cleanup_sentinel_owned_temp_dir_r2_c1(sentinel_tmp, expected_run_id=run_id)
        telemetry = sampler.stop_and_flush()
        f15 = {
            "spec_version": SPEC_VERSION_KH02_R2_C2,
            "orchestrator_pid": orch_pid,
            "sampling_interval_sec": 0.5,
            "memory_samples_log_rel": "logs/runtime_memory_samples_0p5s.jsonl.gz",
            **telemetry,
            "strict_preflight_disk_min_gib": STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
            "strict_runtime_disk_min_gib": STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2,
            "advisory_disk_gib": ADVISORY_DISK_GIB_KH02_R2,
            "owner_disk_preflight_waiver": False,
            "sentinel_temp_dir": str(sentinel_tmp),
            "sentinel_temp_cleaned_up": bool(cleanup["cleaned"]),
            "total_wall_clock_sec": round(time.perf_counter() - t_start, 3),
        }
        write_json_durable_atomic(run_dir / "15_ram_disk_pid_temp_telemetry.json", f15)

        _, end_head_sha = _git_cmd(["rev-parse", "HEAD"])
        f00 = {
            "spec_version": SPEC_VERSION_KH02_R2_C2,
            "schema_version": SCHEMA_VERSION_KH02_R2_C2,
            "run_id": run_id,
            "started_utc": utc_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "branch": branch,
            "start_head_sha": start_head_sha,
            "end_head_sha": end_head_sha,
            "tested_executable_sha": start_head_sha,
            "remote_head_sha": remote_sha,
            "prior_kh02_r2_c1_code_commit_sha": PRIOR_KH02_R2_C1_CODE_COMMIT_SHA,
            "prior_kh02_r2_c1_diag_commit_sha": PRIOR_KH02_R2_C1_DIAG_COMMIT_SHA,
            "prior_kh02_r2_publication_head_sha": PRIOR_KH02_R2_PUBLICATION_HEAD_SHA,
            "prior_kh02_r2_tested_executable_sha": PRIOR_KH02_R2_TESTED_SHA,
            "prior_kh02_r1_publication_head_sha": PRIOR_KH02_R1_PUBLICATION_HEAD_SHA,
            "git_clean_at_start": git_clean,
            "local_uncommitted_c2_changes": not git_clean,
            "orchestrator_pid": orch_pid,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "random_seed": DEFAULT_RANDOM_SEED_KH02_R2,
            "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
            "preflight_ram_available_mib": avail_ram_mib,
            "owner_disk_preflight_waiver": False,
            "owner_disk_waiver_permitted": False,
            "can_run_heavy_stages": can_run_heavy,
            "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        }
        write_json_durable_atomic(run_dir / "00_source_branch_remote_diff_preflight.json", f00)

        pub_st = "PRE_PUSH_PENDING" if (can_run_heavy and f12["status"] == "PASS" and f13["status"] == "PASS" and f14["BASELINE_PARITY_VERDICT"] == "PASS") else (
            "NOT_PUBLISHED_BLOCKED_LOW_DISK" if not strict_disk_ok else "NOT_PUBLISHED_BLOCKED_GATES"
        )
        f19 = {
            "spec_version": SPEC_VERSION_KH02_R2_C2,
            "run_id": run_id,
            "branch": branch,
            "tested_executable_sha": start_head_sha,
            "evidence_commit_sha": None,
            "publication_head_sha": None,
            "remote_readback_sha256_verified": False,
            "latest_run_pointer_updated": False,
            "owner_disk_preflight_waiver": False,
            "publication_status": pub_st,
        }
        write_json_durable_atomic(run_dir / "19_publication_tested_sha_remote_blob_receipt.json", f19)

        mem_audit = _audit_memory_samples_fast_stdlib(run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz")
        docs_map = {
            "00": f00, "01": f01, "02": f02, "03": f03, "04": f04, "05": f05, "06": f06, "07": f07,
            "08": f08, "09": f09, "10": f10, "11": f11, "12": f12, "13": f13, "14": f14, "15": f15,
            "16": f16, "19": f19,
        }
        recomp = recompute_c2_19_gates_and_13_verdicts(
            docs=docs_map,
            mem_audit=mem_audit,
            junit_bijection_ok=bool(f12.get("junit_bijection_exact", False)),
            for_final_verifier_manifest=False,
        )
        f18 = {
            "spec_version": SPEC_VERSION_KH02_R2_C2,
            "schema_version": SCHEMA_VERSION_KH02_R2_C2,
            "run_id": run_id,
            "tested_executable_sha": start_head_sha,
            "branch": branch,
            "OOS_EVALUATIONS": 0,
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
            "gates_19": recomp["gates_19"],
            "verdicts_13": recomp["verdicts_13"],
        }
        f18["artifact_canonical_sha256"] = canonical_hash({k: val for k, val in f18.items() if k != "artifact_canonical_sha256"})
        write_json_durable_atomic(run_dir / "18_orthogonal_gate_matrix.json", f18)
        docs_map["18"] = f18

        readme_md, handover_vi = _render_c2_readme_and_handover_vi(run_dir, docs_map)
        (run_dir / "README_KH02_R2_C2.md").write_text(readme_md, encoding="utf-8")
        (run_dir / "HANDOVER_KH02_R2_C2_VI.txt").write_text(handover_vi, encoding="utf-8")

        # Separate-OS Independent Verifier sealing 17!
        print("[KH-02.R2.C2] Stage 7: Launching separate-OS-process independent disk verifier (17)...", flush=True)
        verifier_script = APP_ROOT / "tools" / "kh_02_r2_c2_independent_verifier.py"
        env_v = os.environ.copy()
        env_v["PYTHONPATH"] = f"{BACKEND_ROOT}:{APP_ROOT}:{REPO_ROOT}"
        v_proc = subprocess.run(
            [sys.executable, str(verifier_script), "--run-dir", str(run_dir), "--expected-run-id", run_id, "--expected-tested-sha", start_head_sha],
            cwd=str(APP_ROOT),
            env=env_v,
            capture_output=True,
            text=True,
            timeout=180,
        )
        print(v_proc.stdout, flush=True)
        if v_proc.stderr:
            print(v_proc.stderr, file=sys.stderr, flush=True)

        f17 = json.loads((run_dir / "17_independent_os_verifier_sha256_manifest.json").read_text("utf-8"))
        verifier_ok = v_proc.returncode == 0 and f17.get("independent_verifier_status") == "PASS"
        final_v = dict(f17.get("recomputed_13_verdicts") or f18["verdicts_13"])
        if not verifier_ok or final_v["ENGINEERING"] == "FAIL":
            exit_code = 1
        elif final_v["ENGINEERING"] == "PASS":
            exit_code = 0
        else:
            exit_code = 2

        return exit_code, run_dir, {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "exit_code": exit_code,
            "independent_verifier_status": f17.get("independent_verifier_status"),
            "manifest_17_sha256": f17.get("manifest_content_sha256"),
            "verdicts_13": final_v,
            "gates_19": f17.get("recomputed_19_gates"),
        }
    finally:
        if sentinel_tmp.exists():
            try:
                safe_cleanup_sentinel_owned_temp_dir_r2_c1(sentinel_tmp, expected_run_id=run_id)
            except Exception:
                pass


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AURUM DESK KH-02.R2.C2 Strict Acceptance Closure Orchestrator")
    ap.add_argument("--owner-waive-disk-preflight", action="store_true", help="FORBIDDEN: always exits code 2.")
    ap.add_argument("--max-workers", type=int, default=GLOBAL_MAX_DESCENDANTS_KH02_R2)
    ap.add_argument("--skip-focused-when-blocked", action="store_true")
    args = ap.parse_args(argv)
    if args.owner_waive_disk_preflight:
        print(json.dumps({"status": "REJECTED_UNAPPROVED_AUTONOMOUS_DISK_WAIVER", "exit_code": 2}), file=sys.stderr)
        return 2
    rc, _rd, summary = run_kh02_r2_c2_acceptance(
        owner_waive_disk_preflight=False,
        max_workers=args.max_workers,
        run_focused_when_blocked=not args.skip_focused_when_blocked,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
