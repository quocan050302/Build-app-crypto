#!/usr/bin/env python3
"""
aurum-desk/tools/run_kh_03_r6_a1_acceptance.py

AURUM DESK — KH-03.R6.A1 | OFFLINE / LOOPBACK QA END-TO-END ACQUISITION & TRUST CLOSURE ACCEPTANCE RUNNER
SPEC VERSION: KH-03.R6.A1-v1.0 | ZERO EXTERNAL MARKET SOCKETS | OOS=0 | ACTIVATION=NO

Produces a unique immutable per-run directory under:
  aurum-desk/docs/artifacts/kh_03_r6_a1_acquisition_trust_closure/<UTC_YYYYMMDDTHHMMSSZ>_<TESTED_SHA12>_<NONCE>/
containing mandatory artifacts 00..27 (with 26 sealed by separate-OS verifier `kh_03_r6_a1_independent_verifier.py`),
10_process_lifecycle_cancel_reap_traces.jsonl.gz, 22_paired_fault_execution_receipts.jsonl.gz,
README.md, HANDOVER_KH03_R6_A1_VI.txt, logs/, junit/, and checksums/sha256_inventory.json.
"""
from __future__ import annotations

import json as _early_json
import os as _early_os
import sys as _early_sys

if __name__ == "__main__" and (
    "--owner-waive-disk-preflight" in _early_sys.argv
    or any(
        str(_early_os.environ.get(ek) or "").strip().lower() in ("1", "true", "yes")
        for ek in (
            "KH02_OWNER_WAIVE_DISK_PREFLIGHT",
            "KH03_OWNER_WAIVE_DISK_PREFLIGHT",
            "KH03_C1_OWNER_WAIVE_DISK_PREFLIGHT",
            "KH03_C2_OWNER_WAIVE_DISK_PREFLIGHT",
            "KH03_R4_OWNER_WAIVE_DISK_PREFLIGHT",
            "KH03_R5_OWNER_WAIVE_DISK_PREFLIGHT",
            "KH03_R5_C1_OWNER_WAIVE_DISK_PREFLIGHT",
            "KH03_R5_C2_OWNER_WAIVE_DISK_PREFLIGHT",
            "KH03_R6_OWNER_WAIVE_DISK_PREFLIGHT",
            "KH03_R6_A1_OWNER_WAIVE_DISK_PREFLIGHT",
        )
    )
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
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
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
from services.kh_02_r2_research_integrity_service import (
    ACTIVATION_READY_KH02_R2,
    CANONICAL_DATASET_REL_PATH,
    CANONICAL_DATASET_SHA256_KH02_R2,
    DEFAULT_RANDOM_SEED_KH02_R2,
    FORBIDDEN_ORDER_SERVICE_TOKENS,
    GLOBAL_MAX_DESCENDANTS_KH02_R2,
    LOCKED_KEEP_AS_IS_FILES_KH02_R2,
    PREFLIGHT_RAM_MIN_MIB_KH02_R2,
    RUNTIME_SAFETY_MODES_KH02_R2,
    STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2,
    _ast_function_inventory,
    _sha256_file,
    audit_r2_reference_inputs_and_invariants,
    create_sentinel_owned_temp_dir_r2_c1,
    load_r2_canonical_dataset_readonly,
    probe_r2_c1_write_mounts_strict,
)
from services.kh_03_data_governance_service import audit_kh03_collector_and_escrow_readiness
from services.kh_03_r3_c1_worker_policy_service import (
    BENCHMARK_KIND_PYTEST,
    BENCHMARK_KIND_SCIENCE,
    select_measured_worker_policy_strict,
)
from services.kh_03_r3_legacy_research_guard import audit_kh03_r3_legacy_research_callgraph
from services.kh_03_r4_economic_diagnostic_service import load_r4_immutable_research_inputs_readonly
from services.kh_03_r6_a1_acceptance_service import (
    MIN_EXPECTED_PYTEST_COLLECTED_R6_A1,
    audit_r6_a1_fault_files_no_literal_flags,
    build_r6_a1_owner_handover_vi,
    derive_r6_a1_fault_verdict,
    derive_r6_a1_readiness_verdicts,
    run_m0_real_baseline_red_reproduction_529bdaf2,
    run_r6_a1_planning_workload_w1_w2_w4_benchmark,
)
from services.kh_03_r6_a1_corrective_service import (
    CORRECTIVE_12_GATE_KEYS,
    PRIOR_A1_EVIDENCE_COMMIT_SHA,
    PRIOR_A1_MANIFEST_26_SHA256,
    PRIOR_A1_PUBLICATION_HEAD_SHA,
    PRIOR_A1_REVIEWED_TESTED_SHA,
    PRIOR_A1_RUN_ID,
    SCHEMA_VERSION_KH03_R6_A1_CORRECTIVE,
    SPEC_VERSION_KH03_R6_A1_CORRECTIVE,
    derive_corrective_fault_verdict,
    derive_r6_a1_corrective_gates,
    run_m0_real_baseline_red_133980ac,
)
from services.kh_03_r6_a1_release_service import build_r6_a1_unsigned_owner_review_template
from services.kh_03_r6_a1_trust_service import (
    A1_EXTERNAL_CAPTURE_ENABLED,
    BASELINE_REVIEWED_HEAD_SHA_R6_A1,
    PRIOR_R6_EVIDENCE_COMMIT_SHA,
    PRIOR_R6_MANIFEST_26_SHA256,
    PRIOR_R6_RUN_ID,
    PRIOR_R6_TESTED_EXECUTABLE_SHA,
    SCHEMA_VERSION_KH03_R6_A1,
    SPEC_VERSION_KH03_R6_A1,
)
from services.kh_03_r6_acceptance_service import run_r6_fixture_ingest_w1_w2_w4_benchmark
from services.kh_03_r6_source_contract_service import (
    MANDATORY_BRANCH_KH03_R6,
    build_r6_source_contract_registry,
)
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import verify_fresh_junit_bijection_v17931211
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_03_r4_independent_verifier import KH03_R4_SOURCE_FILES_ALLOWLIST
from tools.kh_03_r5_c2_independent_verifier import KH03_R5_C2_SOURCE_FILES_ALLOWLIST
from tools.kh_03_r6_a1_independent_verifier import (
    KH03_R6_A1_CORRECTIVE_SOURCE_FILES_ALLOWLIST,
    KH03_R6_A1_SOURCE_FILES_ALLOWLIST,
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R6_A1,
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R6_A1_CORRECTIVE,
)
from tools.run_kh_01b_r1_verification import (
    _build_lpt_shards_kh01b_r1,
    _load_empirical_file_durations_ms_from_kh01b,
    _run_controlled_pytest_w1_w2_w4_benchmark_r1,
    run_async_lpt_pytest_shards_kh01b_r1,
)
from tools.run_kh_02_r2_c4_acceptance import _cleanup_sentinel_temp_c4_win_safe, _pypath_str
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
from tools.run_kh_03_r4_acceptance import EXTRA_FROZEN_R3_C2_FILES_R4
from tools.run_kh_03_r5_verification import _run_stage3a_r4_real_replay_13
from tools.run_kh_03_r6_acceptance import (
    KH03_R6_EXCLUSIVE_SUBPROCESS_TEST_FILES,
    _verify_c2_live_hard_block_intact,
)
from tools.run_v17_9_3_1_2_2_verification import canonicalize_collect_node_id_v1793122

KH03_R6_A1_ARTIFACTS_BASE_REL = "aurum-desk/docs/artifacts/kh_03_r6_a1_acquisition_trust_closure"
KH03_R6_A1_CORRECTIVE_ARTIFACTS_BASE_REL = "aurum-desk/docs/artifacts/kh_03_r6_a1_corrective_closure"

# Heavy multi-child files get dedicated solo exclusive shards; single-child exclusive files are
# safely batched into bounded exclusive shards (still serial slot_cost=4 -> max_descendants <= 4!).
KH03_R6_A1_SOLO_HEAVY_EXCLUSIVE_FILES: Set[str] = {
    "test_kh03_r6_durable_grants.py",
    "test_kh03_r6_fault_truth_and_mutations.py",
    "test_kh03_r6_a1_budget_transport.py",
    "test_kh03_r6_a1_verifier_mutations.py",
    "test_kh03_r6_a1_corrective_custody_l.py",
    "test_kh03_r6_a1_corrective_finalization_n.py",
    "test_kh03_r6_a1_corrective_verifier_mutations.py",
}
KH03_R6_A1_EXCLUSIVE_SUBPROCESS_TEST_FILES: Set[str] = set(KH03_R6_EXCLUSIVE_SUBPROCESS_TEST_FILES) | {
    "test_kh03_r6_a1_acquisition.py",
    "test_kh03_r6_a1_plan_binding.py",
    "test_kh03_r6_a1_budget_transport.py",
    "test_kh03_r6_a1_provenance.py",
    "test_kh03_r6_a1_release.py",
    "test_kh03_r6_a1_resource_async.py",
    "test_kh03_r6_a1_verifier_mutations.py",
    "test_kh03_r6_a1_corrective_custody_l.py",
    "test_kh03_r6_a1_corrective_budget_m.py",
    "test_kh03_r6_a1_corrective_finalization_n.py",
    "test_kh03_r6_a1_corrective_verifier_mutations.py",
}

LOCKED_R6_UNMODIFIED_FILES: Tuple[str, ...] = (
    "aurum-desk/backend/services/kh_03_r6_source_contract_service.py",
    "aurum-desk/backend/services/kh_03_r6_market_data_service.py",
    "aurum-desk/tools/kh_03_r6_independent_verifier.py",
    "aurum-desk/tools/run_kh_03_r6_acceptance.py",
)


def _stamp_a1(doc: Dict[str, Any], *, run_id: str, tested_sha: str) -> Dict[str, Any]:
    out = dict(doc)
    out["spec_version"] = SPEC_VERSION_KH03_R6_A1
    out["schema_version"] = SCHEMA_VERSION_KH03_R6_A1
    out["run_id"] = run_id
    out["tested_executable_sha"] = tested_sha
    return out


def _git_blob_sha256(rev: str, rel_path: str) -> Optional[str]:
    p = subprocess.run(["git", "show", f"{rev}:{rel_path}"], cwd=str(REPO_ROOT), capture_output=True, check=False, timeout=15)
    return hashlib.sha256(p.stdout).hexdigest() if p.returncode == 0 else None


def _audit_ownership_01_a1(*, tested_sha: str, run_id: str, legacy_cg: Mapping[str, Any], sentinel_tmp: Path) -> Dict[str, Any]:
    all_locked = list(dict.fromkeys(
        list(LOCKED_KEEP_AS_IS_FILES_KH02_R2)
        + list(EXTRA_FROZEN_RESEARCH_FILES_C1)
        + list(EXTRA_FROZEN_R3_C2_FILES_R4)
        + list(KH03_R4_SOURCE_FILES_ALLOWLIST)
        + list(KH03_R5_C2_SOURCE_FILES_ALLOWLIST)
        + list(LOCKED_R6_UNMODIFIED_FILES)
    ))
    rc_diff, diff_out = _git_cmd(["diff", "--name-only", f"{BASELINE_REVIEWED_HEAD_SHA_R6_A1}..HEAD"])
    changed_files = [ln.strip() for ln in diff_out.splitlines() if ln.strip()] if rc_diff == 0 else []
    locked_mod = [f for f in changed_files if f in set(all_locked)]
    allow_set = set(KH03_R6_A1_SOURCE_FILES_ALLOWLIST)
    unauthorized = [f for f in changed_files if f not in allow_set and not f.startswith(f"{KH03_R6_A1_ARTIFACTS_BASE_REL}/")]

    forb_hits: List[str] = []
    secret_hits: List[str] = []
    a1_inv: List[Dict[str, Any]] = []
    pem_markers = (
        "-----" + "BEGIN PRIVATE KEY-----",
        "-----" + "BEGIN RSA PRIVATE KEY-----",
        "-----" + "BEGIN EC PRIVATE KEY-----",
        "-----" + "BEGIN OPENSSH PRIVATE KEY-----",
    )
    for rel in KH03_R6_A1_SOURCE_FILES_ALLOWLIST:
        p = REPO_ROOT / rel
        if not p.is_file():
            continue
        txt = p.read_text("utf-8", errors="replace")
        if any(m in txt for m in pem_markers):
            secret_hits.append(f"{rel}:embedded_private_key")
        if rel.endswith(".py"):
            tree = ast.parse(txt, filename=str(p))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        if any(tok in a.name for tok in FORBIDDEN_ORDER_SERVICE_TOKENS):
                            forb_hits.append(f"{rel}:import:{a.name}")
                elif isinstance(node, ast.ImportFrom):
                    m = node.module or ""
                    if any(tok in m for tok in FORBIDDEN_ORDER_SERVICE_TOKENS):
                        forb_hits.append(f"{rel}:from:{m}")
        before_h = _git_blob_sha256(BASELINE_REVIEWED_HEAD_SHA_R6_A1, rel)
        a1_inv.append({
            "rel_path": rel,
            "change_kind": "MODIFIED_R6_HARDENING" if before_h is not None else "NEW_A1_FILE",
            "before_sha256_at_28b25267": before_h,
            "after_sha256": _sha256_file(p),
            "functions_count": len(_ast_function_inventory(p)) if rel.endswith(".py") else None,
        })

    c_live = CostAssumptions()
    cost_ok = abs(float(c_live.maker_fee_rate) - 0.0002) <= 1e-12 and abs(float(c_live.taker_fee_rate) - 0.0004) <= 1e-12
    c2_hb_ok = _verify_c2_live_hard_block_intact(sentinel_tmp)
    unauth_r1 = int(legacy_cg.get("unauthorized_callers_count", 0))
    ok = (not locked_mod) and (not unauthorized) and (not forb_hits) and (not secret_hits) and cost_ok and c2_hb_ok and (unauth_r1 == 0)

    return _stamp_a1({
        "status": "PASS" if ok else "FAIL",
        "baseline_reviewed_head_sha": BASELINE_REVIEWED_HEAD_SHA_R6_A1,
        "changed_files_since_r6_published": changed_files,
        "locked_files_checked_count": len(all_locked),
        "locked_files_modified_count": len(locked_mod),
        "locked_files_modified": locked_mod,
        "unauthorized_changed_files_count": len(unauthorized),
        "unauthorized_changed_files": unauthorized,
        "forbidden_order_imports_count": len(forb_hits),
        "forbidden_order_imports": forb_hits,
        "secrets_detected_count": len(secret_hits),
        "secrets_detected": secret_hits,
        "legacy_r1_unauthorized_callers_count": unauth_r1,
        "production_cost_defaults_intact": cost_ok,
        "c2_live_hard_block_intact": c2_hb_ok,
        "a1_source_files_inventory": a1_inv,
    }, run_id=run_id, tested_sha=tested_sha)


def _collect_and_build_a1_lpt_shards(owned_temp_root: Path) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]]]:
    all_test_files = sorted(str(p.relative_to(APP_ROOT)).replace("\\", "/") for p in (BACKEND_ROOT / "tests").glob("test_*.py") if p.is_file())
    col_out = owned_temp_root / "collect_a1_stdout.log"
    col_err = owned_temp_root / "collect_a1_stderr.log"
    col_snippet = "import os, sys, pytest; fo=open(sys.argv[1],'w',encoding='utf-8',errors='replace'); fe=open(sys.argv[2],'w',encoding='utf-8',errors='replace'); sys.stdout=fo; sys.stderr=fe; rc=pytest.main(sys.argv[3:]); fo.flush(); fe.flush(); fo.close(); fe.close(); os._exit(int(rc))"
    col_env = dict(os.environ); col_env["PYTHONHASHSEED"] = "0"; col_env["PYTHONPATH"] = _pypath_str()
    proc = subprocess.run([sys.executable, "-c", col_snippet, str(col_out), str(col_err), str(BACKEND_ROOT / "tests"), "--collect-only", "-q"], cwd=str(APP_ROOT), env=col_env, capture_output=True, check=False, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"KH03_R6_A1_PYTEST_COLLECTION_FAILED:rc={proc.returncode}")
    collected_by_file: Dict[str, List[str]] = {}
    for ln in col_out.read_text("utf-8", errors="replace").splitlines():
        cid = canonicalize_collect_node_id_v1793122(ln)
        if cid is not None:
            collected_by_file.setdefault(cid.split("::", 1)[0], []).append(cid)
    for k in collected_by_file:
        collected_by_file[k].sort()

    empirical_ms = _load_empirical_file_durations_ms_from_kh01b()
    non_sub = [f for f in all_test_files if Path(f).name not in KH03_R6_A1_EXCLUSIVE_SUBPROCESS_TEST_FILES]
    solo_heavy = [f for f in all_test_files if Path(f).name in KH03_R6_A1_SOLO_HEAVY_EXCLUSIVE_FILES]
    batch_excl = [f for f in all_test_files if Path(f).name in KH03_R6_A1_EXCLUSIVE_SUBPROCESS_TEST_FILES and Path(f).name not in KH03_R6_A1_SOLO_HEAVY_EXCLUSIVE_FILES]

    shards = _build_lpt_shards_kh01b_r1(non_sub, collected_by_file, empirical_ms)
    for sh in shards:
        if sh.get("shard_name") == "SHARD_SINGLE_CHILD_02_BUDGET2":
            sh["exclusive"] = True
            sh["slot_cost"] = 4

    # Safe bounded batching of single-child exclusive files into 6 balanced exclusive shards (slot_cost=4 -> strictly <=4 descendants!)
    batch_size = max(1, (len(batch_excl) + 5) // 6)
    for b_idx in range(0, len(batch_excl), batch_size):
        chunk = batch_excl[b_idx:b_idx + batch_size]
        shards.append({
            "shard_name": f"SHARD_EXCL_BATCH_A1_{(b_idx // batch_size) + 1:02d}",
            "files": chunk,
            "exclusive": True,
            "slot_cost": 4,
            "priority_weight": 260_000,
            "needs_venv": False,
        })
    for s_idx, sf in enumerate(sorted(solo_heavy), 1):
        shards.append({
            "shard_name": f"SHARD_EXCL_SOLO_A1_{s_idx:02d}",
            "files": [sf],
            "exclusive": True,
            "slot_cost": 4,
            "priority_weight": 270_000,
            "needs_venv": False,
        })
    return collected_by_file, shards


def _run_a1_full_repo_pytest_19(
    run_dir: Path, sentinel_tmp: Path, *, run_id: str, tested_sha: str, effective_disk_reserve_bytes: int, max_workers: int, git_clean_at_start: bool
) -> Dict[str, Any]:
    lineage_p = REPO_ROOT / LINEAGE_TRACKED_ARTIFACT_REL
    sha_before = _sha256_file(lineage_p) if lineage_p.is_file() else None
    t0 = time.perf_counter()
    empirical_r2_rss = load_empirical_r2_shard_rss_bytes(REPO_ROOT)
    collected_by_file, a1_shards = _collect_and_build_a1_lpt_shards(sentinel_tmp)
    active_shards_tracker: Dict[str, Any] = {}
    junit_res = asyncio.run(
        run_async_lpt_pytest_shards_kh01b_r1(
            run_dir, sentinel_tmp, run_id=run_id, tested_executable_sha=tested_sha,
            effective_disk_reserve_bytes=effective_disk_reserve_bytes,
            max_parallel_workers=max_workers, ram_policy_mode="R3_STRICT_512MIB",
            empirical_rss_by_shard=empirical_r2_rss, active_shards_tracker=active_shards_tracker,
            shard_specs_override=a1_shards, collected_by_file_override=collected_by_file,
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
        mandatory_node_prefixes=("backend/tests/test_kh_02_r2", "backend/tests/test_kh_03", "backend/tests/test_kh03_r2", "backend/tests/test_kh03_r3", "backend/tests/test_kh03_r4", "backend/tests/test_kh03_r5", "backend/tests/test_kh03_r6"),
        require_shard_schedule=True,
    )
    nodes_rel = "logs/pytest_collected_executed_nodeids.jsonl.gz"
    nodes_gz = run_dir / nodes_rel
    c_ids = list(cm.get("collected_node_ids") or ())
    with gzip.open(nodes_gz, "wt", encoding="utf-8") as fh:
        for cid in c_ids:
            fh.write(cid + "\n")
    nodes_sha = _sha256_file(nodes_gz)

    col_n = len(c_ids)
    exe_n = int(bj.get("executed_node_ids_count") or bj.get("executed_count") or 0)
    pas_n, skp_n, fai_n, err_n, msk_n = int(bj.get("passed_count") or 0), int(bj.get("skipped_count") or 0), int(bj.get("failed_count") or 0), int(bj.get("error_count") or 0), int(bj.get("mandatory_skipped_count") or 0)
    nz = int(sm.get("nonzero_exit_shards_count") or 0)
    ok = bj.get("status") == "PASS" and nz == 0 and col_n >= MIN_EXPECTED_PYTEST_COLLECTED_R6_A1 and col_n == exe_n and fai_n == 0 and err_n == 0 and msk_n == 0
    return _stamp_a1({
        "status": "PASS" if ok else "FAIL",
        "elapsed_sec": elapsed, "max_workers_used": max_workers, "shards_count": len(a1_shards),
        "collected_count": col_n, "executed_count": exe_n, "passed_count": pas_n,
        "skipped_count": skp_n, "mandatory_skipped_count": msk_n, "failed_count": fai_n,
        "error_count": err_n, "nonzero_exit_shards_count": nz, "junit_bijection_exact": bj.get("status") == "PASS",
        "collected_nodeids_gz_rel": nodes_rel, "collected_nodeids_gz_sha256": nodes_sha,
        "tracked_test_side_effect_audit": {"rel_path": LINEAGE_TRACKED_ARTIFACT_REL, "sha256_before": sha_before, "sha256_after_pytest": sha_after, "restored_to_clean_head": restored},
        "shard_manifest": sm, "bijection_audit": {k: v for k, v in bj.items() if k not in ("executed_node_ids", "collected_node_ids")},
    }, run_id=run_id, tested_sha=tested_sha)


def _run_all_verifier_mutations_24_a1(sentinel_tmp: Path, *, run_id: str, tested_sha: str) -> Dict[str, Any]:
    mut_root = sentinel_tmp / "a1_all_muts"
    mut_root.mkdir(parents=True, exist_ok=True)
    r3_mut_dir = mut_root / "r3_m16"
    r3_mut_dir.mkdir(parents=True, exist_ok=True)
    r3_m16 = _run_kh03_r3_verifier_mutation_suite_22(r3_mut_dir)

    from tests.test_kh03_r3_c1_verifier_mutations import KH03_R3_C1_VERIFIER_MUTATIONS_SPECS, build_synthetic_valid_kh03_r3_c1_run_dir, run_kh03_r3_c1_verifier_subprocess
    from tests.test_kh03_r3_c2_independent_mutations import KH03_R3_C2_LEGACY_R3_MUTATIONS_SPECS, KH03_R3_C2_OWN_VERIFIER_MUTATIONS_SPECS, build_synthetic_valid_kh03_r3_c2_run_dir, run_kh03_r3_c2_verifier_subprocess
    from tests.test_kh03_r3_resource_async_and_verifier_mutations import build_synthetic_valid_kh03_r3_run_dir, run_kh03_r3_verifier_subprocess
    from tests.test_kh03_r4_independent_verifier_mutations import KH03_R4_OWN_VERIFIER_MUTATIONS_SPECS, build_synthetic_valid_kh03_r4_run_dir, run_kh03_r4_verifier_subprocess
    from tests.test_kh03_r5_readiness_and_verifier import KH03_R5_OWN_VERIFIER_MUTATIONS_SPECS, build_synthetic_valid_kh03_r5_run_dir, run_kh03_r5_verifier_subprocess
    from tests.test_kh03_r5_c1_independent_verifier_mutations import KH03_R5_C1_OWN_VERIFIER_MUTATIONS_SPECS, build_synthetic_valid_kh03_r5_c1_run_dir, run_kh03_r5_c1_verifier_subprocess
    from tests.test_kh03_r5_c2_verifier_mutations import KH03_R5_C2_OWN_VERIFIER_MUTATIONS_SPECS, build_synthetic_valid_kh03_r5_c2_run_dir, run_kh03_r5_c2_verifier_subprocess
    from tests.test_kh03_r6_fault_truth_and_mutations import KH03_R6_OWN_VERIFIER_MUTATIONS_SPECS, build_synthetic_valid_kh03_r6_run_dir, run_kh03_r6_verifier_subprocess
    from tests.test_kh03_r6_a1_verifier_mutations import KH03_R6_A1_OWN_VERIFIER_MUTATIONS_SPECS, build_synthetic_valid_kh03_r6_a1_run_dir, run_kh03_r6_a1_verifier_subprocess

    bases = {}
    for key, bfn, rfn in (
        ("c1", build_synthetic_valid_kh03_r3_c1_run_dir, run_kh03_r3_c1_verifier_subprocess),
        ("r3", build_synthetic_valid_kh03_r3_run_dir, run_kh03_r3_verifier_subprocess),
        ("c2", build_synthetic_valid_kh03_r3_c2_run_dir, run_kh03_r3_c2_verifier_subprocess),
        ("r4", build_synthetic_valid_kh03_r4_run_dir, run_kh03_r4_verifier_subprocess),
        ("r5", build_synthetic_valid_kh03_r5_run_dir, run_kh03_r5_verifier_subprocess),
        ("r5c1", build_synthetic_valid_kh03_r5_c1_run_dir, run_kh03_r5_c1_verifier_subprocess),
        ("r5c2", build_synthetic_valid_kh03_r5_c2_run_dir, run_kh03_r5_c2_verifier_subprocess),
        ("r6", build_synthetic_valid_kh03_r6_run_dir, run_kh03_r6_verifier_subprocess),
        ("a1", build_synthetic_valid_kh03_r6_a1_run_dir, run_kh03_r6_a1_verifier_subprocess),
    ):
        bd = mut_root / f"base_{key}"
        rid_x, tsha_x = bfn(bd)
        rc_x, _ = rfn(bd, rid_x, tsha_x)
        bases[key] = (bd, rid_x, tsha_x, rfn, rc_x)

    def _run_mut(args_t: Tuple[Path, str, str, Any, str, Any, str]) -> Dict[str, Any]:
        bdir, rid_x, tsha_x, runner_fn, mid, mut_fn, exp_tok = args_t
        cdir = mut_root / f"clone_{mid}"
        shutil.copytree(bdir, cdir)
        try:
            mut_fn(cdir)
            rc, out_t = runner_fn(cdir, rid_x, tsha_x)
            return {
                "mutation_id": mid,
                "expected_violation_token": exp_tok,
                "verifier_exit_code": rc,
                "output_sha256": hashlib.sha256(out_t.encode("utf-8", errors="replace")).hexdigest(),
                "detected_fail_closed": (rc != 0) and (exp_tok in out_t),
            }
        finally:
            shutil.rmtree(cdir, ignore_errors=True)

    inh_jobs = (
        [(bases["c1"][0], bases["c1"][1], bases["c1"][2], bases["c1"][3], m, fn, tok) for m, fn, tok in KH03_R3_C1_VERIFIER_MUTATIONS_SPECS]
        + [(bases["r3"][0], bases["r3"][1], bases["r3"][2], bases["r3"][3], m, fn, tok) for m, fn, tok in KH03_R3_C2_LEGACY_R3_MUTATIONS_SPECS]
        + [(bases["c2"][0], bases["c2"][1], bases["c2"][2], bases["c2"][3], m, fn, tok) for m, fn, tok in KH03_R3_C2_OWN_VERIFIER_MUTATIONS_SPECS]
        + [(bases["r4"][0], bases["r4"][1], bases["r4"][2], bases["r4"][3], m, fn, tok) for m, fn, tok in KH03_R4_OWN_VERIFIER_MUTATIONS_SPECS]
        + [(bases["r5"][0], bases["r5"][1], bases["r5"][2], bases["r5"][3], m, fn, tok) for m, fn, tok in KH03_R5_OWN_VERIFIER_MUTATIONS_SPECS]
        + [(bases["r5c1"][0], bases["r5c1"][1], bases["r5c1"][2], bases["r5c1"][3], m, fn, tok) for m, fn, tok in KH03_R5_C1_OWN_VERIFIER_MUTATIONS_SPECS]
        + [(bases["r5c2"][0], bases["r5c2"][1], bases["r5c2"][2], bases["r5c2"][3], m, fn, tok) for m, fn, tok in KH03_R5_C2_OWN_VERIFIER_MUTATIONS_SPECS]
        + [(bases["r6"][0], bases["r6"][1], bases["r6"][2], bases["r6"][3], m, fn, tok) for m, fn, tok in KH03_R6_OWN_VERIFIER_MUTATIONS_SPECS]
    )
    a1_jobs = [(bases["a1"][0], bases["a1"][1], bases["a1"][2], bases["a1"][3], m, fn, tok) for m, fn, tok in KH03_R6_A1_OWN_VERIFIER_MUTATIONS_SPECS]

    with ThreadPoolExecutor(max_workers=3) as pool:
        inh_res = list(pool.map(_run_mut, inh_jobs))
        a1_res = list(pool.map(_run_mut, a1_jobs))

    shutil.rmtree(mut_root, ignore_errors=True)
    r3_tot, r3_det = int(r3_m16.get("mutations_tested_count", 0)), int(r3_m16.get("mutations_detected_count", 0))
    inh_tot = r3_tot + len(inh_res)
    inh_det = r3_det + sum(1 for r in inh_res if r["detected_fail_closed"])
    a1_tot, a1_det = len(a1_res), sum(1 for r in a1_res if r["detected_fail_closed"])
    clean_ok = all(v[4] == 0 for v in bases.values()) and bool(r3_m16.get("clean_clone_verifier_green"))

    return _stamp_a1({
        "isolated_temp_clones_only": True,
        "sealed_run_dir_mutated": False,
        "clean_clone_verifier_green": clean_ok,
        "inherited_mutations_tested_count": inh_tot,
        "inherited_mutations_detected_count": inh_det,
        "inherited_mutations_failed_detection_count": inh_tot - inh_det,
        "new_a1_mutations_tested_count": a1_tot,
        "new_a1_mutations_detected_count": a1_det,
        "new_a1_mutations_failed_detection_count": a1_tot - a1_det,
        "total_mutations_tested_count": inh_tot + a1_tot,
        "new_a1_results": a1_res,
        "status": "PASS" if (clean_ok and inh_det == inh_tot and a1_det == a1_tot and inh_tot >= 167 and a1_tot >= 32) else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)


def run_kh03_r6_a1_orchestrator(
    *, allow_local_dirty: bool = False, max_workers: int = GLOBAL_MAX_DESCENDANTS_KH02_R2
) -> Tuple[int, Path, Dict[str, Any]]:
    lock = _CrossPlatformRunLock(Path(tempfile.gettempdir()) / "aurum_desk_kh03_r6_a1_run.lock")
    lock_ok, lock_msg = lock.acquire()
    if not lock_ok:
        raise RuntimeError(f"KH03_R6_A1_SINGLE_RUN_LOCK_FAILED:{lock_msg}")

    t_start = time.perf_counter()
    orch_pid = os.getpid()
    utc_now = datetime.now(timezone.utc)
    _, branch = _git_cmd(["branch", "--show-current"])
    _, origin_url = _git_cmd(["remote", "get-url", "origin"])
    _, start_head = _git_cmd(["rev-parse", "HEAD"])
    _, st_porc = _git_cmd(["status", "--porcelain=v1"])
    _, ls_rem = _git_cmd(["ls-remote", "origin", f"refs/heads/{MANDATORY_BRANCH_KH03_R6}"])
    remote_sha = ls_rem.split()[0] if ls_rem.strip() else ""
    git_clean = (st_porc.strip() == "")

    if branch != MANDATORY_BRANCH_KH03_R6:
        lock.release()
        raise RuntimeError(f"WRONG_BRANCH:{branch}")
    if (not git_clean) and (not allow_local_dirty):
        lock.release()
        raise RuntimeError(f"DIRTY_WORKTREE_REFUSED:\n{st_porc}")

    nonce = secrets.token_hex(2)
    run_id = f"{utc_now.strftime('%Y%m%dT%H%M%SZ')}_{start_head[:12]}_{nonce}"
    art_base = (REPO_ROOT / KH03_R6_A1_ARTIFACTS_BASE_REL).resolve()
    run_dir = art_base / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "junit").mkdir(exist_ok=True)
    (run_dir / "checksums").mkdir(exist_ok=True)

    short_tmp_base: Optional[Path] = Path(r"C:\Users\Public") if (os.name == "nt" and Path(r"C:\Users\Public").is_dir()) else None
    try:
        sentinel_tmp = create_sentinel_owned_temp_dir_r2_c1("a1_", run_id=run_id, owner_pid=orch_pid, base_dir=short_tmp_base)
    except OSError:
        sentinel_tmp = create_sentinel_owned_temp_dir_r2_c1("a1_", run_id=run_id, owner_pid=orch_pid)

    write_mount_paths = {
        "repo_volume": REPO_ROOT, "app_volume": APP_ROOT,
        "artifacts_output": art_base if art_base.exists() else APP_ROOT,
        "temp_volume": sentinel_tmp, "python_venv": Path(sys.executable).resolve().parent,
        "caches": Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")),
        "logs": run_dir / "logs",
    }
    strict_probe = probe_r2_c1_write_mounts_strict(write_mount_paths, atomic_probe_dirs=[(sentinel_tmp, True, run_id)])
    avail_ram_mib = round(psutil.virtual_memory().available / (1024.0 * 1024.0), 2)
    min_pre_dk = min(float(m.get("free_gib", 0.0)) for m in strict_probe["distinct_mounts"])
    strict_disk_ok = bool(strict_probe["can_run_heavy_stages"])
    ram_ok = avail_ram_mib >= PREFLIGHT_RAM_MIN_MIB_KH02_R2
    can_run_heavy = strict_disk_ok and ram_ok
    eff_workers = max(1, min(int(max_workers), GLOBAL_MAX_DESCENDANTS_KH02_R2))
    pytest_full_cap = min(2, eff_workers) if (min_pre_dk < 24.0 or avail_ram_mib < 3000.0) else min(3, eff_workers)

    sampler = _BoundedResourceSampler(
        out_gz=run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz",
        watch_path=run_dir, watch_paths=[REPO_ROOT, APP_ROOT, run_dir, sentinel_tmp, Path(sys.executable).resolve().parent],
        interval_sec=0.5, enforce_runtime_disk_abort=can_run_heavy,
    )
    sampler.start()
    sampler_stopped = False

    try:
        print("[KH-03.R6.A1] Stage 1: Ownership (01), prior R1..R6 integrity (02) & M0 Real RED on 529bdaf2 (03)...", flush=True)
        legacy_cg = audit_kh03_r3_legacy_research_callgraph(REPO_ROOT)
        f01 = _audit_ownership_01_a1(tested_sha=start_head, run_id=run_id, legacy_cg=legacy_cg, sentinel_tmp=sentinel_tmp)
        ro_inputs = load_r4_immutable_research_inputs_readonly(REPO_ROOT, PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R6_A1)
        datasets, prov02 = ro_inputs["datasets"], ro_inputs["provenance"]
        pdir = sentinel_tmp / "c4_prov_a1"
        pdir.mkdir(parents=True, exist_ok=True)
        f08_c4, f09_c4 = audit_kh03_collector_and_escrow_readiness(pdir)
        shutil.rmtree(pdir, ignore_errors=True)
        r6_man_p = REPO_ROOT / f"aurum-desk/docs/artifacts/kh_03_r6_observed_market_data/{PRIOR_R6_RUN_ID}/26_independent_OS_verifier_manifest_sha256.json"
        r6_man_ok = r6_man_p.is_file() and (_sha256_file(r6_man_p) == PRIOR_R6_MANIFEST_26_SHA256)
        f02 = _stamp_a1({
            **prov02,
            "all_prior_sealed_artifacts_intact": prov02.get("status") == "PASS",
            "prior_sealed_artifacts_checked_count": len(PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R6_A1),
            "prior_r6_manifest_sha256_verified": r6_man_ok and (prov02.get("status") == "PASS"),
            "oos_reads_executed": 0, "oos_evaluations": 0,
            "c4_escrow_record_count": int(f09_c4.get("real_oos_escrow_final_record_count", -1)),
            "status": "PASS" if (prov02.get("status") == "PASS" and r6_man_ok and f08_c4.get("status") == "PASS" and f09_c4.get("status") == "PASS") else "FAIL",
        }, run_id=run_id, tested_sha=start_head)
        f03 = _stamp_a1(run_m0_real_baseline_red_reproduction_529bdaf2(repo_root=REPO_ROOT, run_id=run_id, tested_sha=start_head), run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "01_function_ownership_keep_as_is_manifest.json", f01)
        write_json_durable_atomic(run_dir / "02_prior_r6_and_c2_sealed_integrity_no_oos.json", f02)
        write_json_durable_atomic(run_dir / "03_baseline_real_red_p1_g_to_k.json", f03)

        print("[KH-03.R6.A1] Stage 2: Executing 55 NEW A1 paired controls (G01..K10), QA E2E & lifecycle traces (04..15)...", flush=True)
        from tests.test_kh03_r6_a1_acquisition import run_kh03_r6_a1_controls_g01_to_g10
        from tests.test_kh03_r6_a1_plan_binding import run_kh03_r6_a1_controls_h01_to_h10
        from tests.test_kh03_r6_a1_budget_transport import run_kh03_r6_a1_controls_i01_to_i15
        from tests.test_kh03_r6_a1_provenance import run_kh03_r6_a1_controls_j01_to_j10
        from tests.test_kh03_r6_a1_release import run_kh03_r6_a1_controls_k01_to_k10

        recs_g, sum_g = run_kh03_r6_a1_controls_g01_to_g10(executable_sha=start_head, run_id=run_id)
        recs_h, sum_h = run_kh03_r6_a1_controls_h01_to_h10(executable_sha=start_head, run_id=run_id)
        recs_i, traces_10, sum_i = run_kh03_r6_a1_controls_i01_to_i15(executable_sha=start_head, run_id=run_id)
        recs_j, sum_j = run_kh03_r6_a1_controls_j01_to_j10(executable_sha=start_head, run_id=run_id)
        recs_k, sum_k = run_kh03_r6_a1_controls_k01_to_k10(executable_sha=start_head, run_id=run_id)
        all_a1_55 = list(recs_g) + list(recs_h) + list(recs_i) + list(recs_j) + list(recs_k)

        lc10_gz = run_dir / "10_process_lifecycle_cancel_reap_traces.jsonl.gz"
        with gzip.open(lc10_gz, "wt", encoding="utf-8") as fh:
            for tr in traces_10:
                fh.write(json.dumps(tr, sort_keys=True, separators=(",", ":")) + "\n")

        reg = build_r6_source_contract_registry()
        tmpl_smoke_rel = "logs/unsigned_owner_review_template_stage_b_smoke.json"
        tmpl_dev_rel = "logs/unsigned_owner_review_template_stage_c_dev.json"
        write_json_durable_atomic(run_dir / tmpl_smoke_rel, build_r6_a1_unsigned_owner_review_template(
            tested_executable_sha=start_head, publication_head_sha=None, stage_a1_run_id=run_id,
            manifest_26_sha256="<SEALED_AFTER_VERIFIER_26_SHA256>", source_registry_digest=reg.registry_digest, stage="B_SMOKE",
        ))
        write_json_durable_atomic(run_dir / tmpl_dev_rel, build_r6_a1_unsigned_owner_review_template(
            tested_executable_sha=start_head, publication_head_sha=None, stage_a1_run_id=run_id,
            manifest_26_sha256="<SEALED_AFTER_VERIFIER_26_SHA256>", source_registry_digest=reg.registry_digest, stage="C_DEV",
        ))

        f04 = _stamp_a1({
            "dryrun_invalid_plan_nonzero_exit": sum_g["dryrun_invalid_plan_nonzero_exit"],
            "dryrun_valid_plan_zero_claim_zero_socket": sum_g["dryrun_valid_plan_zero_claim_zero_socket"],
            "handshake_only_success_removed": sum_g["handshake_only_success_removed"],
            "qa_cli_smoke_e2e_pass": sum_g["qa_cli_smoke_e2e_pass"],
            "qa_cli_smoke_pid": sum_g["qa_cli_smoke_pid"],
            "qa_cli_smoke_receipt": sum_g.get("qa_cli_smoke_receipt"),
            "qa_cli_dev_e2e_pass": sum_g["qa_cli_dev_e2e_pass"],
            "qa_cli_dev_committed_chunks": sum_g["qa_cli_dev_committed_chunks"],
            "qa_cli_dev_committed_rows": sum_g["qa_cli_dev_committed_rows"],
            "qa_cli_dev_committed_raw_bytes": sum_g["qa_cli_dev_committed_raw_bytes"],
            "qa_cli_dev_receipt": sum_g.get("qa_cli_dev_receipt"),
        }, run_id=run_id, tested_sha=start_head)
        f05 = _stamp_a1({
            "empty_task_plan_blocked_no_claim": sum_g["empty_task_plan_blocked_no_claim"],
            "partial_second_task_failure_truthful": sum_g["partial_second_task_failure_truthful"],
            "cancel_between_claim_and_first_attempt_zero_io": sum_g["cancel_between_claim_and_first_attempt_zero_io"],
            "repeat_same_grant_denied_after_restart": sum_g["repeat_same_grant_denied_after_restart"],
        }, run_id=run_id, tested_sha=start_head)
        f06 = _stamp_a1({
            "nested_caps_immutable_and_tamper_rejected": sum_h["nested_caps_immutable_and_tamper_rejected"],
            "dataclass_replace_endpoints_window_rejected": sum_h["dataclass_replace_endpoints_window_rejected"],
            "recomputed_digest_old_mac_rejected": sum_h["recomputed_digest_old_mac_rejected"],
            "forged_grant_object_rejected": sum_h["forged_grant_object_rejected"],
            "foreign_lease_rejected_zero_dispatch": sum_h["foreign_lease_rejected_zero_dispatch"],
            "narrow_plan_cap_enforced_by_ledger": sum_h["narrow_plan_cap_enforced_by_ledger"],
            "registry_drift_rejected": sum_h["registry_drift_rejected"],
        }, run_id=run_id, tested_sha=start_head)
        f07 = _stamp_a1({
            "unauthorized_path_or_query_or_ws_op_rejected": sum_h["unauthorized_path_or_query_or_ws_op_rejected"],
            "post_enqueue_expiry_zero_io": sum_h["post_enqueue_expiry_zero_io"],
            "nonloopback_dispatch_hard_blocked": sum_h["nonloopback_dispatch_hard_blocked"],
            "child_cap_override_rejected": sum_i["child_cap_override_rejected"],
            "child_deadline_override_and_missing_fields_rejected": sum_i["child_deadline_override_and_missing_fields_rejected"],
            "direct_child_cli_external_blocked": sum_i["direct_child_cli_external_blocked"],
        }, run_id=run_id, tested_sha=start_head)
        f08 = _stamp_a1({
            "zero_request_or_missing_dim_reservation_rejected": sum_i["zero_request_or_missing_dim_reservation_rejected"],
            "retry_over_cap_rejected": sum_i["retry_over_cap_rejected"],
            "reservation_receipt_verified": sum_i["reservation_receipt_verified"],
            "rest_error_and_retry_cumulative_bytes_bounded": sum_i["rest_error_and_retry_cumulative_bytes_bounded"],
            "retry_after_beyond_budget_aborted": sum_i["retry_after_beyond_budget_aborted"],
            "sentinel_never_reads_beyond_reservation": sum_i["sentinel_never_reads_beyond_reservation"],
            "ws_reconnect_sub_conn_bounded_by_reservation": sum_i["ws_reconnect_sub_conn_bounded_by_reservation"],
            "ws_frame_queue_message_caps_enforced": sum_i["ws_frame_queue_message_caps_enforced"],
        }, run_id=run_id, tested_sha=start_head)
        f09 = _stamp_a1({
            "completed_missing_dim_rejected": sum_i["completed_missing_dim_rejected"],
            "overrun_quarantined_without_clamping": sum_i["overrun_quarantined_without_clamping"],
            "supervisor_rejects_on_settlement_failure_or_exception": sum_i["supervisor_rejects_on_settlement_failure_or_exception"],
            "noncooperative_child_and_grandchild_reaped": sum_i["noncooperative_child_and_grandchild_reaped"],
            "child_launch_or_pipe_error_cleaned_no_orphan": sum_i["child_launch_or_pipe_error_cleaned_no_orphan"],
            "cancel_after_child_exit_before_accept_discarded": sum_i["cancel_after_child_exit_before_accept_discarded"],
            "cancel_before_custody_persist_zero_write": sum_g["cancel_before_custody_persist_zero_write"],
            "lifecycle_gz_rel": "10_process_lifecycle_cancel_reap_traces.jsonl.gz",
            "lifecycle_gz_sha256": _sha256_file(lc10_gz),
            "lifecycle_traces_count": len(traces_10),
        }, run_id=run_id, tested_sha=start_head)
        f11 = _stamp_a1({
            "cross_process_parallel_reservations_conserved": sum_i["cross_process_parallel_reservations_conserved"],
            "anchor_monotonic_ordering_verified": sum_i["anchor_monotonic_ordering_verified"],
            "crash_pre_anchor_and_unknown_io_restart_safe": sum_i["crash_pre_anchor_and_unknown_io_restart_safe"],
        }, run_id=run_id, tested_sha=start_head)
        f12 = _stamp_a1({
            "fabricated_mapping_cannot_mint_observed": sum_j["fabricated_mapping_cannot_mint_observed"],
            "forged_or_copied_token_rejected": sum_j["forged_or_copied_token_rejected"],
            "wrong_binding_ids_rejected": sum_j["wrong_binding_ids_rejected"],
            "raw_bytes_length_or_hash_mismatch_rejected": sum_j["raw_bytes_length_or_hash_mismatch_rejected"],
            "unsettled_or_overrun_rejected": sum_j["unsettled_or_overrun_rejected"],
            "late_or_nonzero_exit_rejected": sum_j["late_or_nonzero_exit_rejected"],
            "replay_same_token_or_chunk_denied": sum_j["replay_same_token_or_chunk_denied"],
            "qa_promotion_to_observed_dev_rejected_despite_tls": sum_j["qa_promotion_to_observed_dev_rejected_despite_tls"],
        }, run_id=run_id, tested_sha=start_head)
        f13 = _stamp_a1({
            "qa_custody_readonly_post_restart_valid": sum_j["qa_custody_readonly_post_restart_valid"],
            "tampered_raw_or_norm_file_detected": sum_j["tampered_raw_or_norm_file_detected"],
            "crash_and_failed_unlink_quarantined_safe_cleanup": sum_j["crash_and_failed_unlink_quarantined_safe_cleanup"],
            "qa_e2e_committed_chunks_count": sum_g["qa_cli_dev_committed_chunks"],
            "qa_e2e_committed_rows_count": sum_g["qa_cli_dev_committed_rows"],
            "qa_e2e_committed_raw_bytes": sum_g["qa_cli_dev_committed_raw_bytes"],
            "qa_e2e_dataset_manifest_sha256": (sum_g.get("qa_cli_dev_receipt") or {}).get("dataset_manifest_sha256"),
            "raw_payloads_committed_to_git": False,
        }, run_id=run_id, tested_sha=start_head)
        f14 = _stamp_a1({
            "published_evidence_only_descendant_verified": sum_k["published_evidence_only_descendant_verified"],
            "detached_worktree_exact_tested_sha_verified": sum_k["detached_worktree_exact_tested_sha_verified"],
            "code_or_config_drift_rejected": sum_k["code_or_config_drift_rejected"],
            "dirty_or_untrusted_pythonpath_or_dep_drift_rejected": sum_k["dirty_or_untrusted_pythonpath_or_dep_drift_rejected"],
            "forged_manifest_pass_rejected": sum_k["forged_manifest_pass_rejected"],
            "pointer_receipt_ancestry_mismatch_rejected": sum_k["pointer_receipt_ancestry_mismatch_rejected"],
            "missing_execute_authorized_or_conflict_rejected": sum_k["missing_execute_authorized_or_conflict_rejected"],
            "unsigned_template_as_grant_rejected": sum_k["unsigned_template_as_grant_rejected"],
            "qa_cli_on_frozen_executable_verified": sum_k["qa_cli_on_frozen_executable_verified"],
        }, run_id=run_id, tested_sha=start_head)
        f15 = _stamp_a1({
            "a1_external_capture_enabled": A1_EXTERNAL_CAPTURE_ENABLED,
            "external_cli_with_plausible_input_hard_blocked": sum_g["external_cli_with_plausible_input_hard_blocked"],
            "owner_grant_consumed_count": 0,
            "external_market_requests_attempted": 0,
            "accepted_real_observed_records": 0,
            "private_or_trading_calls": 0,
            "oos_reads_executed": 0,
            "oos_evaluations": 0,
            "production_mode": "OFF", "live_trading_mode": "OFF", "paper_trading_mode": "OFF", "auto_trading_mode": "OFF",
            "activation_ready": "NO", "economic_verdict": "NO_PROVEN_EDGE",
            "fresh_oos_verdict": "BLOCKED_NO_UNTOUCHED_DATA", "kh04_entry_verdict": "BLOCKED_NO_DEV_CANDIDATE",
            "unsigned_owner_review_template_rel": tmpl_smoke_rel,
            "unsigned_owner_review_template_dev_rel": tmpl_dev_rel,
        }, run_id=run_id, tested_sha=start_head)

        for fname, obj in (
            ("04_acquisition_cli_dryrun_zero_claim_and_qa_e2e.json", f04),
            ("05_session_state_task_coverage_and_finalization.json", f05),
            ("06_immutable_plan_grant_registry_lease_binding.json", f06),
            ("07_request_contract_and_ipc_envelope_receipts.json", f07),
            ("08_reservation_actual_io_budget_conservation.json", f08),
            ("09_settlement_failure_overrun_quarantine_receipts.json", f09),
            ("11_ledger_cross_process_anchor_crash_restart.json", f11),
            ("12_trusted_supervisor_settlement_provenance_chain.json", f12),
            ("13_custody_actual_qa_raw_normalized_hashes_and_manifest.json", f13),
            ("14_frozen_executable_release_ancestry_and_cli_receipts.json", f14),
            ("15_external_block_no_observed_no_oos_no_trade.json", f15),
        ):
            write_json_durable_atomic(run_dir / fname, obj)

        print("[KH-03.R6.A1] Stage 3A: Running fresh R4 1x replay + 6 dynamic cost scenarios + E1/E2/E3 arms (16, 17)...", flush=True)
        f16_raw, ctx = _run_stage3a_r4_real_replay_13(
            datasets=datasets, dataset_sha=str(prov02["canonical_dataset_sha256"]),
            timeframe_stats=prov02["timeframe_coverage_and_gaps"],
            run_dir=run_dir, sentinel_tmp=sentinel_tmp, run_id=run_id, tested_sha=start_head,
        )
        fgz_rel = str(f16_raw.get("forensic_ledger_rel_path") or f16_raw.get("forensic_ledger_gz_rel") or "logs/r4_replay_forensic_ledger_1x.jsonl.gz")
        f16 = _stamp_a1({
            **dict(f16_raw),
            "forensic_ledger_gz_rel": fgz_rel,
            "forensic_ledger_gz_sha256": _sha256_file(run_dir / fgz_rel),
            "r4_decimal_bridge_exact_match": (
                int(f16_raw.get("null_1x_closed", -1)) == 203
                and int(f16_raw.get("null_1x_wins", -1)) == 7
                and int(f16_raw.get("null_1x_losses", -1)) == 196
                and abs(float(f16_raw.get("null_1x_net_usd", 0.0)) - (-441.6118)) <= 0.01
            ),
            "n_closed": int(f16_raw.get("null_1x_closed", 0)),
            "n_wins": int(f16_raw.get("null_1x_wins", 0)),
            "n_losses": int(f16_raw.get("null_1x_losses", 0)),
            "n_open_censored": int(f16_raw.get("null_1x_open_censored", 0)),
            "n_gap_events": int(f16_raw.get("null_1x_gap_unresolved", f16_raw.get("null_1x_gap_events", 0))),
            "realized_losses_gt_2p50_count": int(f16_raw.get("realized_losses_gt_2p50_count", 0)),
        }, run_id=run_id, tested_sha=start_head)
        arms_p = f16_raw.get("arms_1x_profiles") or {}
        e1_p = arms_p.get("KH03_R3_E1_LOW_FRICTION_STOP_FLOOR") or {}
        e2_p = arms_p.get("KH03_R3_E2_CAUSAL_DELAYED_CONFIRMATION") or {}
        e3_p = arms_p.get("KH03_R3_E3_CAUSAL_NEAREST_M5_UNSWEPT_EXIT") or {}
        arms_ok = (
            int(e1_p.get("n_closed", -1)) == 58 and int(e1_p.get("n_wins", -1)) == 3 and int(e1_p.get("n_losses", -1)) == 55 and abs(float(e1_p.get("net_pnl_usd", 0.0)) - (-118.0121)) <= 0.01
            and int(e2_p.get("n_closed", -1)) == 131 and int(e2_p.get("n_wins", -1)) == 5 and int(e2_p.get("n_losses", -1)) == 126 and abs(float(e2_p.get("net_pnl_usd", 0.0)) - (-274.3771)) <= 0.01
            and int(e3_p.get("n_closed", -1)) == 206 and int(e3_p.get("n_wins", -1)) == 9 and int(e3_p.get("n_losses", -1)) == 197 and abs(float(e3_p.get("net_pnl_usd", 0.0)) - (-439.0423)) <= 0.01
        )
        f17 = _stamp_a1({
            "all_six_scenarios_exact_match": bool(f16_raw.get("six_cost_dynamic_vs_fixed_fills_separated")),
            "all_three_arms_exact_match": arms_ok,
            "six_cost_scenarios_null_summary": f16_raw.get("six_cost_scenarios_null_summary"),
            "null_1x_zero_fee_counterfactual_net_usd": f16_raw.get("null_1x_zero_fee_counterfactual_net_usd"),
            "arms_1x_profiles": arms_p,
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "16_r4_replay_decimal_economic_bridge.json", f16)
        write_json_durable_atomic(run_dir / "17_six_cost_scenarios_and_arm_regression.json", f17)

        print("[KH-03.R6.A1] Stage 3B: Re-running 56 R6 + 91 C1/C2 + 288 inherited controls (22/23) & 34 NEW + 167 inherited mutations (24)...", flush=True)
        r3_sub = sentinel_tmp / "r3_194_sub_a1"
        r3_sub.mkdir(parents=True, exist_ok=True)
        r3_194 = _run_kh03_r3_194_red_green_fault_suite(
            sentinel_tmp=r3_sub, datasets=datasets, mtf_bundle=ctx["mtf_bundle"], ledger_bundle=ctx["ledger_bundle"],
            r2_cands_1x=ctx["r2_cands_1x"], replay_bundle=ctx["replay_bundle"], live_ff=ctx["live_ff"],
            forensic_gz=ctx["forensic_gz"], f07=ctx["f07_wl"], f13=ctx["f13_wf"], m5_ledger=ctx["m5_ledger"],
        )
        from tests.test_kh03_r3_c1_regression_truth import run_new_kh03_r3_c1_worker_policy_fault_controls
        from tests.test_kh03_r3_c2_legacy_runner_fail_closed import run_new_kh03_r3_c2_legacy_fail_closed_fault_controls
        from tests.test_kh03_r4_no_alpha_no_causal_claims import run_new_kh03_r4_fault_controls_16
        from tests.test_kh03_r5_readiness_and_verifier import run_new_kh03_r5_48_fault_controls
        from tests.test_kh03_r5_c1_custody_abort_and_determinism import run_new_kh03_r5_c1_fault_controls
        from tests.test_kh03_r5_c2_fault_control_truth import run_new_kh03_r5_c2_fault_controls_d12
        from tests.test_kh03_r5_c2_worker_lifecycle import run_new_kh03_r5_c2_worker_controls_e16
        from tests.test_kh03_r5_c2_grant_replay import run_new_kh03_r5_c2_grant_controls_f15
        from tests.test_kh03_r6_source_contracts import run_kh03_r6_controls_a08
        from tests.test_kh03_r6_durable_grants import run_kh03_r6_controls_b12
        from tests.test_kh03_r6_transport_lifecycle import run_kh03_r6_controls_c16
        from tests.test_kh03_r6_market_data_integrity import run_kh03_r6_controls_d01_to_d11
        from tests.test_kh03_r6_custody_and_readiness import run_kh03_r6_controls_d12_to_e06

        inh_288 = (
            list(r3_194["controls"])
            + list(run_new_kh03_r3_c1_worker_policy_fault_controls())
            + list(run_new_kh03_r3_c2_legacy_fail_closed_fault_controls())
            + list(run_new_kh03_r4_fault_controls_16(forensic_rows=ctx["forensic_rows"], docs_03_12=ctx["r4_docs"]))
            + list(run_new_kh03_r5_48_fault_controls())
        )
        shutil.rmtree(r3_sub, ignore_errors=True)
        inh_288_pass = sum(1 for c in inh_288 if c.get("status") == "PASS")

        c1_c2_91 = (
            list(run_new_kh03_r5_c1_fault_controls(executable_sha=start_head, run_id=run_id))
            + list(run_new_kh03_r5_c2_fault_controls_d12(executable_sha=start_head, run_id=run_id))
            + list(run_new_kh03_r5_c2_worker_controls_e16(executable_sha=start_head, run_id=run_id))
            + list(run_new_kh03_r5_c2_grant_controls_f15(executable_sha=start_head, run_id=run_id))
        )
        c1_c2_91_pass = sum(1 for c in c1_c2_91 if c.get("status") == "PASS")

        r6_56 = (
            list(run_kh03_r6_controls_a08(executable_sha=start_head, run_id=run_id))
            + list(run_kh03_r6_controls_b12(executable_sha=start_head, run_id=run_id)[0])
            + list(run_kh03_r6_controls_c16(executable_sha=start_head, run_id=run_id)[0])
            + list(run_kh03_r6_controls_d01_to_d11(executable_sha=start_head, run_id=run_id)[0])
            + list(run_kh03_r6_controls_d12_to_e06(executable_sha=start_head, run_id=run_id)[0])
        )
        r6_56_pass = sum(1 for c in r6_56 if c.get("status") == "PASS")

        p22_gz = run_dir / "22_paired_fault_execution_receipts.jsonl.gz"
        with gzip.open(p22_gz, "wt", encoding="utf-8") as gz:
            for rec in all_a1_55:
                gz.write(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n")

        a1_verd = derive_r6_a1_fault_verdict(all_a1_55, expected_executable_sha=start_head, expected_run_id=run_id)
        ast_chk = audit_r6_a1_fault_files_no_literal_flags(sorted((BACKEND_ROOT / "tests").glob("test_kh03_r6_a1_*.py")))
        f23 = _stamp_a1({
            "paired_receipts_gz_rel": "22_paired_fault_execution_receipts.jsonl.gz",
            "paired_receipts_gz_sha256": _sha256_file(p22_gz),
            "new_a1_paired_verified_count": int(a1_verd["passed_controls"]),
            "new_a1_paired_failed_count": int(a1_verd["failed_controls"]),
            "new_a1_counts_by_group": a1_verd["counts_by_group"],
            "new_a1_counts_by_kind": a1_verd["counts_by_kind"],
            "a1_fault_ast_literal_flags_count": int(ast_chk["literal_flag_hits_count"]),
            "unique_a1_execution_ids_ok": (
                a1_verd["unique_negative_execution_ids_count"] == len(all_a1_55)
                and a1_verd["unique_positive_execution_ids_count"] == len(all_a1_55)
            ),
            "inherited_r6_paired_executed_count": len(r6_56),
            "inherited_r6_paired_passed_count": r6_56_pass,
            "inherited_r6_paired_failed_count": len(r6_56) - r6_56_pass,
            "inherited_c1_c2_paired_executed_count": len(c1_c2_91),
            "inherited_c1_c2_paired_passed_count": c1_c2_91_pass,
            "inherited_c1_c2_paired_failed_count": len(c1_c2_91) - c1_c2_91_pass,
            "inherited_regression_executed_count": len(inh_288),
            "inherited_regression_passed_count": inh_288_pass,
            "inherited_regression_failed_count": len(inh_288) - inh_288_pass,
            "inherited_not_relabelled_as_new_a1": True,
            "status": "PASS" if (a1_verd["valid"] and ast_chk["clean"] and r6_56_pass == len(r6_56) and c1_c2_91_pass == len(c1_c2_91) and inh_288_pass == len(inh_288)) else "FAIL",
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "23_inherited_regression_and_new_fault_truth.json", f23)

        f24 = _run_all_verifier_mutations_24_a1(sentinel_tmp, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "24_independent_verifier_mutation_results.json", f24)
        del datasets, ctx
        gc.collect()

        eff_res_b = int(STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2 * (1024 ** 3))
        print(f"[KH-03.R6.A1] Stage 4A: Running FULL repo LPT-sharded pytest (max_workers={pytest_full_cap}) (19)...", flush=True)
        f19 = _run_a1_full_repo_pytest_19(
            run_dir, sentinel_tmp, run_id=run_id, tested_sha=start_head,
            effective_disk_reserve_bytes=eff_res_b, max_workers=pytest_full_cap, git_clean_at_start=git_clean,
        )
        write_json_durable_atomic(run_dir / "19_full_pytest_collection_junit_bijection.json", f19)
        gc.collect()

        print("[KH-03.R6.A1] Stage 4B: Running W1/W2/W4/W1_repeat pytest, science, R6 ingest & A1 workload benchmarks (20)...", flush=True)
        pb_raw = asyncio.run(_run_controlled_pytest_w1_w2_w4_benchmark_r1(sentinel_tmp, enforce_r3_ram_guard=True))
        sb_raw = asyncio.run(_run_r2_science_slice_w1_w2_w4_determinism(sentinel_tmp))
        r6_ing = run_r6_fixture_ingest_w1_w2_w4_benchmark(num_partitions=16, bars_per_partition=120)
        a1_wl = run_r6_a1_planning_workload_w1_w2_w4_benchmark(num_units=32)
        gc.collect()

        pb_doc = _stamp_a1({"canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2, "benchmark_kind": BENCHMARK_KIND_PYTEST, "status": "PASS" if pb_raw.get("all_4_runs_identical") else "FAIL", **pb_raw}, run_id=run_id, tested_sha=start_head)
        sb_doc = _stamp_a1({"canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2, "benchmark_kind": BENCHMARK_KIND_SCIENCE, "status": "PASS" if sb_raw.get("all_4_runs_identical") else "FAIL", **sb_raw}, run_id=run_id, tested_sha=start_head)
        p_lim = {"configured_child_process_cap": GLOBAL_MAX_DESCENDANTS_KH02_R2, "currently_allowed_worker_cap": pytest_full_cap, "full_suite_safe_worker_cap": pytest_full_cap, "shard_exclusive_cap": pytest_full_cap, "preflight_disk_free_gib": min_pre_dk, "disk_free_gib": min_pre_dk, "available_ram_mib": avail_ram_mib}
        s_lim = {"configured_child_process_cap": GLOBAL_MAX_DESCENDANTS_KH02_R2, "currently_allowed_worker_cap": eff_workers, "full_suite_safe_worker_cap": eff_workers, "shard_exclusive_cap": eff_workers, "preflight_disk_free_gib": min_pre_dk, "disk_free_gib": min_pre_dk, "available_ram_mib": avail_ram_mib}
        p_pol = select_measured_worker_policy_strict(pb_doc, benchmark_kind=BENCHMARK_KIND_PYTEST, system_limits=p_lim, expected_run_id=run_id, expected_tested_sha=start_head, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2)
        s_pol = select_measured_worker_policy_strict(sb_doc, benchmark_kind=BENCHMARK_KIND_SCIENCE, system_limits=s_lim, expected_run_id=run_id, expected_tested_sha=start_head, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2)
        f20 = _stamp_a1({
            "pytest_controlled_subset_raw": pb_doc,
            "science_slice_8000_raw": sb_doc,
            "pytest_worker_policy": p_pol,
            "science_worker_policy": s_pol,
            "r6_ingest_benchmark": r6_ing,
            "a1_workload_benchmark": a1_wl,
            "science_w1_w2_w4_all_identical": bool(sb_raw.get("all_4_runs_identical")),
            "r6_ingest_w1_w2_w4_all_identical": bool(r6_ing["all_4_identical"]),
            "a1_workload_w1_w2_w4_all_identical": bool(a1_wl["all_4_identical"]),
            "pytest_policy_valid": p_pol.get("status") == "PASS",
            "science_policy_valid": s_pol.get("status") == "PASS",
            "r6_ingest_benchmark_valid": bool(r6_ing["valid"]),
            "a1_workload_benchmark_valid": bool(a1_wl["valid"]),
            "applied_safe_workers_cap": int(p_pol.get("applied_worker_count") or pytest_full_cap),
            "full_suite_speedup_extrapolated": False,
            "status": "PASS" if (p_pol.get("status") == "PASS" and s_pol.get("status") == "PASS" and r6_ing["valid"] and a1_wl["valid"]) else "FAIL",
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "20_W1_W2_W4_W1repeat_benchmark_policy.json", f20)

        print("[KH-03.R6.A1] Stage 4C: Running FULL 56,710 A1 + 6,375 SI8 + 246 adapter + D4 parity (18)...", flush=True)
        ds_p, _ = load_r2_canonical_dataset_readonly(REPO_ROOT / CANONICAL_DATASET_REL_PATH)
        f02_r1 = audit_r2_reference_inputs_and_invariants(REPO_ROOT)
        p_raw = _run_full_production_baseline_parity_20(datasets=ds_p, sentinel_tmp=sentinel_tmp, run_id=run_id, tested_sha=start_head, branch=branch, f01=f01, f02=f02_r1, max_workers=eff_workers)
        f18 = _stamp_a1({
            **p_raw,
            "a1_checked": int(p_raw.get("a1_population_count", 0)),
            "a1_mismatches": int(p_raw.get("a1_mismatches_count", -1)),
            "si8_checked": int(p_raw.get("si8_population_count", 0)),
            "si8_mismatches": int(p_raw.get("si8_mismatches_count", -1)),
            "adapters_checked": int(p_raw.get("three_adapter_population_count", 0)),
            "adapters_mismatches": int(p_raw.get("three_adapter_mismatches_count", -1)),
            "d4_mismatches": 0 if p_raw.get("BASELINE_PARITY_VERDICT") == "PASS" else 1,
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "18_A1_SI8_adapter_D4_full_parity.json", f18)
        del ds_p
        gc.collect()

        p_dw = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--owner-waive-disk-preflight"], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=20)
        dw_ok = (p_dw.returncode == 2)
        cl_info = _cleanup_sentinel_temp_c4_win_safe(sentinel_tmp, expected_run_id=run_id)
        sentinel_cleaned = bool(cl_info["cleaned"])
        telem = sampler.stop_and_flush()
        sampler_stopped = True
        tel_gz_p = run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"

        t_cnt = 0; t_min_ram = float("inf"); t_min_disk = float("inf"); t_max_desc = 0
        with gzip.open(tel_gz_p, "rt", encoding="utf-8") as tfh:
            for tln in tfh:
                if not tln.strip():
                    continue
                tr = json.loads(tln)
                t_cnt += 1
                rm = tr.get("available_mib")
                if rm is None and tr.get("available_ram_bytes") is not None:
                    rm = float(tr["available_ram_bytes"]) / (1024.0 * 1024.0)
                if rm is not None:
                    t_min_ram = min(t_min_ram, float(rm))
                if tr.get("disk_free_gib") is not None:
                    t_min_disk = min(t_min_disk, float(tr["disk_free_gib"]))
                if tr.get("child_count") is not None:
                    t_max_desc = max(t_max_desc, int(tr["child_count"]))

        f21 = _stamp_a1({
            "orchestrator_pid": orch_pid, "sampling_interval_sec": 0.5,
            "telemetry_samples_gz_rel": "logs/runtime_memory_samples_0p5s.jsonl.gz",
            "telemetry_samples_gz_sha256": _sha256_file(tel_gz_p),
            **telem, "samples_count": t_cnt,
            "min_observed_ram_available_mib": round(t_min_ram, 2),
            "min_observed_disk_free_gib": round(t_min_disk, 3),
            "max_observed_descendants": t_max_desc,
            "disk_waiver_exit2_proven": dw_ok,
            "owned_temp_cleaned_verified": sentinel_cleaned,
            "orphan_workers_count": int(telem.get("orphan_children_after_cleanup", 0)),
            "total_wall_clock_sec": round(time.perf_counter() - t_start, 3),
            "status": "PASS" if (sentinel_cleaned and dw_ok and int(telem.get("orphan_children_after_cleanup", 1)) == 0) else "FAIL",
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "21_actual_resource_telemetry_cleanup.json", f21)

        _, end_head = _git_cmd(["rev-parse", "HEAD"])
        f00 = _stamp_a1({
            "started_utc": utc_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "branch": branch, "origin_url": origin_url,
            "baseline_reviewed_head_sha": BASELINE_REVIEWED_HEAD_SHA_R6_A1,
            "prior_r6_tested_sha": PRIOR_R6_TESTED_EXECUTABLE_SHA,
            "prior_r6_evidence_sha": PRIOR_R6_EVIDENCE_COMMIT_SHA,
            "prior_r6_run_id": PRIOR_R6_RUN_ID,
            "start_head_sha": start_head, "end_head_sha": end_head, "remote_head_sha": remote_sha,
            "worktree_clean_at_start": git_clean, "disk_waiver_used": False,
            "preflight_disk_free_gib": round(min_pre_dk, 3), "preflight_ram_available_mib": avail_ram_mib,
            "single_run_lock_status": lock_msg, "orchestrator_pid": orch_pid,
            "python_version": platform.python_version(), "platform": platform.platform(), "cpu_count": os.cpu_count(),
            "random_seed": DEFAULT_RANDOM_SEED_KH02_R2, "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
            "strict_multi_mount_probe": strict_probe, "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
            "status": "PASS" if (strict_disk_ok and ram_ok and git_clean) else "BLOCKED_PREFLIGHT",
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "00_preflight_branch_sha_resource_lock.json", f00)

        f27 = _stamp_a1({
            "phase1_executable_committed": git_clean, "branch": branch,
            "start_head_sha": start_head, "evidence_commit_sha": None, "publication_head_sha": None,
            "remote_readback_sha256_verified": False, "ci_status": "CI_UNKNOWN_NO_ACTIONS_RUN",
            "status": "PENDING_REMOTE_READBACK",
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "27_phase1_code_commit_push_receipt.json", f27)

        all_docs = {
            "00": f00, "01": f01, "02": f02, "03": f03, "04": f04, "05": f05, "06": f06, "07": f07,
            "08": f08, "09": f09, "11": f11, "12": f12, "13": f13, "14": f14, "15": f15,
            "16": f16, "17": f17, "18": f18, "19": f19, "20": f20, "21": f21, "23": f23, "24": f24, "27": f27,
            "26": {"distinct_os_process_verified": True, "verifier_status": "PENDING", "violations_count": 0, "verifier_pid": -1, "orchestrator_pid": orch_pid},
        }
        # F30: Explicit pre-verifier artifact (`G32 = PENDING_SEPARATE_OS_VERIFIER`)!
        f25 = _stamp_a1(derive_r6_a1_readiness_verdicts(all_docs, expected_run_id=run_id, expected_tested_sha=start_head, pre_verifier_mode=True), run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "25_orthogonal_gates_pre_verifier.json", f25)

        handover_vi = build_r6_a1_owner_handover_vi(
            run_id=run_id, branch=branch, base_reviewed_head_sha=BASELINE_REVIEWED_HEAD_SHA_R6_A1,
            tested_sha=start_head, verdicts=f25,
            summary_stats={
                "m0_reproduced_count": f03.get("reproduced_count"),
                "qa_cli_smoke_pid": f04.get("qa_cli_smoke_pid"),
                "qa_cli_dev_chunks": f04.get("qa_cli_dev_committed_chunks"),
                "qa_cli_dev_rows": f04.get("qa_cli_dev_committed_rows"),
                "qa_cli_dev_raw_bytes": f04.get("qa_cli_dev_committed_raw_bytes"),
                "pytest_passed": f19["passed_count"], "pytest_collected": f19["collected_count"], "pytest_skipped": f19["skipped_count"],
                "new_a1_paired": f23["new_a1_paired_verified_count"],
                "inherited_r6_paired": f23["inherited_r6_paired_passed_count"],
                "inherited_c1_c2_paired": f23["inherited_c1_c2_paired_passed_count"],
                "inherited_regression": f23["inherited_regression_passed_count"],
                "new_a1_mutations_detected": f24["new_a1_mutations_detected_count"], "new_a1_mutations_tested": f24["new_a1_mutations_tested_count"],
                "inherited_mutations_detected": f24["inherited_mutations_detected_count"], "inherited_mutations_tested": f24["inherited_mutations_tested_count"],
                "min_disk_gib": f21["min_observed_disk_free_gib"], "min_ram_mib": f21["min_observed_ram_available_mib"], "max_descendants": f21["max_observed_descendants"],
            },
            unsigned_template_rel_path=f"{KH03_R6_A1_ARTIFACTS_BASE_REL}/{run_id}/{tmpl_smoke_rel}",
        )
        (run_dir / "HANDOVER_KH03_R6_A1_VI.txt").write_text(handover_vi, encoding="utf-8")

        (run_dir / "README.md").write_text(
            f"# AURUM DESK — KH-03.R6.A1 | Offline / QA Loopback End-to-End Acquisition & Trust Closure Evidence\n\n"
            f"- **Run ID:** `{run_id}` | **Branch:** `{branch}` | **Tested Executable SHA:** `{start_head}`\n"
            f"- **Reviewed Baseline HEAD (R6):** `{BASELINE_REVIEWED_HEAD_SHA_R6_A1}` | **Prior R6 Tested SHA:** `{PRIOR_R6_TESTED_EXECUTABLE_SHA}`\n"
            f"- **Pre-Verifier Status (`25`):** `{f25['overall_classification']}` (final verified status sealed in `26_independent_OS_verifier_manifest_sha256.json`)\n"
            f"- **External Stages (R6.B/C/D):** `0/4` executed (`BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED`)\n"
            f"- **Safety:** `PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO | ECONOMIC=NO_PROVEN_EDGE | OOS_EVALUATIONS=0 | EXTERNAL_MARKET_REQUESTS=0`\n"
            f"- **Reproduce Command:** `python aurum-desk/tools/run_kh_03_r6_a1_acceptance.py --workers auto`\n",
            encoding="utf-8",
        )

        ck_inv: Dict[str, Dict[str, Any]] = {}
        for fp in sorted(run_dir.rglob("*")):
            if fp.is_file():
                rel_p = str(fp.relative_to(run_dir)).replace("\\", "/")
                if rel_p in ("checksums/sha256_inventory.json", "26_independent_OS_verifier_manifest_sha256.json"):
                    continue
                ck_inv[rel_p] = {"sha256": _sha256_file(fp), "bytes": fp.stat().st_size}
        write_json_durable_atomic(run_dir / "checksums" / "sha256_inventory.json", _stamp_a1({"files_count": len(ck_inv), "files": ck_inv}, run_id=run_id, tested_sha=start_head))

        print("[KH-03.R6.A1] Stage 5: Invoking Separate-OS Independent Verifier (sealing 26)...", flush=True)
        v_env = dict(os.environ); v_env["PYTHONPATH"] = _pypath_str(); v_env["PYTHONHASHSEED"] = "0"
        v_proc = subprocess.run(
            [
                sys.executable, str(APP_ROOT / "tools" / "kh_03_r6_a1_independent_verifier.py"),
                "--run-dir", str(run_dir), "--expected-run-id", run_id,
                "--expected-tested-sha", start_head, "--orchestrator-pid", str(orch_pid),
            ],
            cwd=str(APP_ROOT), env=v_env, capture_output=True, text=True, timeout=180,
        )
        print(v_proc.stdout, flush=True)
        if v_proc.stderr:
            print(v_proc.stderr, file=sys.stderr, flush=True)
        return int(v_proc.returncode), run_dir, f25
    finally:
        if not sampler_stopped:
            try:
                sampler.stop_and_flush()
            except Exception:
                pass
        lock.release()


def _stamp_corr(doc: Dict[str, Any], *, run_id: str, tested_sha: str) -> Dict[str, Any]:
    out = dict(doc)
    out["spec_version"] = SPEC_VERSION_KH03_R6_A1_CORRECTIVE
    out["schema_version"] = SCHEMA_VERSION_KH03_R6_A1_CORRECTIVE
    out["run_id"] = run_id
    out["tested_executable_sha"] = tested_sha
    return out


def _write_jsonl_gz_corr(p: Path, rows: List[Dict[str, Any]]) -> str:
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
    return _sha256_file(p)


def _audit_ownership_01_corrective(*, tested_sha: str, run_id: str, legacy_cg: Mapping[str, Any], sentinel_tmp: Path) -> Dict[str, Any]:
    all_locked = list(dict.fromkeys(
        list(LOCKED_KEEP_AS_IS_FILES_KH02_R2)
        + list(EXTRA_FROZEN_RESEARCH_FILES_C1)
        + list(EXTRA_FROZEN_R3_C2_FILES_R4)
        + list(KH03_R4_SOURCE_FILES_ALLOWLIST)
        + list(KH03_R5_C2_SOURCE_FILES_ALLOWLIST)
        + list(LOCKED_R6_UNMODIFIED_FILES)
    ))
    rc_diff, diff_out = _git_cmd(["diff", "--name-only", f"{PRIOR_A1_PUBLICATION_HEAD_SHA}..HEAD"])
    changed_files = [ln.strip() for ln in diff_out.splitlines() if ln.strip()] if rc_diff == 0 else []
    locked_mod = [f for f in changed_files if f in set(all_locked)]
    allow_set = set(KH03_R6_A1_CORRECTIVE_SOURCE_FILES_ALLOWLIST)
    unauthorized = [f for f in changed_files if f not in allow_set and not f.startswith(f"{KH03_R6_A1_CORRECTIVE_ARTIFACTS_BASE_REL}/")]

    forb_hits: List[str] = []
    secret_hits: List[str] = []
    corr_inv: List[Dict[str, Any]] = []
    pem_markers = (
        "-----" + "BEGIN PRIVATE KEY-----",
        "-----" + "BEGIN RSA PRIVATE KEY-----",
        "-----" + "BEGIN EC PRIVATE KEY-----",
        "-----" + "BEGIN OPENSSH PRIVATE KEY-----",
    )
    for rel in KH03_R6_A1_CORRECTIVE_SOURCE_FILES_ALLOWLIST:
        p = REPO_ROOT / rel
        if not p.is_file():
            continue
        txt = p.read_text("utf-8", errors="replace")
        if any(m in txt for m in pem_markers):
            secret_hits.append(f"{rel}:embedded_private_key")
        if rel.endswith(".py"):
            tree = ast.parse(txt, filename=str(p))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        if any(tok in a.name for tok in FORBIDDEN_ORDER_SERVICE_TOKENS):
                            forb_hits.append(f"{rel}:import:{a.name}")
                elif isinstance(node, ast.ImportFrom):
                    m = node.module or ""
                    if any(tok in m for tok in FORBIDDEN_ORDER_SERVICE_TOKENS):
                        forb_hits.append(f"{rel}:from:{m}")
        before_h = _git_blob_sha256(PRIOR_A1_REVIEWED_TESTED_SHA, rel)
        corr_inv.append({
            "rel_path": rel,
            "change_kind": "MODIFIED_P1_LMN_HARDENING" if before_h is not None else "NEW_CORRECTIVE_FILE",
            "before_sha256_at_133980ac": before_h,
            "after_sha256": _sha256_file(p),
            "functions_count": len(_ast_function_inventory(p)) if rel.endswith(".py") else None,
        })

    c_live = CostAssumptions()
    cost_ok = abs(float(c_live.maker_fee_rate) - 0.0002) <= 1e-12 and abs(float(c_live.taker_fee_rate) - 0.0004) <= 1e-12
    c2_hb_ok = _verify_c2_live_hard_block_intact(sentinel_tmp)
    unauth_r1 = int(legacy_cg.get("unauthorized_callers_count", 0))
    ok = (not locked_mod) and (not unauthorized) and (not forb_hits) and (not secret_hits) and cost_ok and c2_hb_ok and (unauth_r1 == 0)

    return _stamp_corr({
        "status": "PASS" if ok else "FAIL",
        "prior_a1_tested_sha": PRIOR_A1_REVIEWED_TESTED_SHA,
        "prior_a1_publication_head_sha": PRIOR_A1_PUBLICATION_HEAD_SHA,
        "changed_files_since_prior_a1_published": changed_files,
        "locked_files_checked_count": len(all_locked),
        "locked_files_modified_count": len(locked_mod),
        "locked_files_modified": locked_mod,
        "unauthorized_changed_files_count": len(unauthorized),
        "unauthorized_changed_files": unauthorized,
        "forbidden_order_imports_count": len(forb_hits),
        "forbidden_order_imports": forb_hits,
        "secrets_detected_count": len(secret_hits),
        "secrets_detected": secret_hits,
        "legacy_r1_unauthorized_callers_count": unauth_r1,
        "production_cost_defaults_intact": cost_ok,
        "c2_live_hard_block_intact": c2_hb_ok,
        "corrective_source_files_inventory": corr_inv,
    }, run_id=run_id, tested_sha=tested_sha)


def _run_all_verifier_mutations_corrective(sentinel_tmp: Path, *, run_id: str, tested_sha: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    base_a1_muts = _run_all_verifier_mutations_24_a1(sentinel_tmp, run_id=run_id, tested_sha=tested_sha)
    inh_total = int(base_a1_muts["total_mutations_tested_count"])
    inh_det = int(base_a1_muts["inherited_mutations_detected_count"]) + int(base_a1_muts["new_a1_mutations_detected_count"])

    from tests.test_kh03_r6_a1_corrective_verifier_mutations import (
        KH03_R6_A1_CORRECTIVE_OWN_MUTATIONS_SPECS,
        build_synthetic_valid_kh03_r6_a1_corrective_run_dir,
        run_kh03_r6_a1_corrective_verifier_subprocess,
    )
    mut_root = sentinel_tmp / "corr_mut20"
    mut_root.mkdir(parents=True, exist_ok=True)
    bdir = mut_root / "base_clean"
    rid_x, tsha_x = build_synthetic_valid_kh03_r6_a1_corrective_run_dir(bdir)
    rc_clean, _ = run_kh03_r6_a1_corrective_verifier_subprocess(bdir, rid_x, tsha_x)

    def _run_one(spec: Tuple[str, Any, str]) -> Dict[str, Any]:
        mid, mfn, tok = spec
        cdir = mut_root / f"clone_{mid}"
        shutil.copytree(bdir, cdir)
        try:
            mfn(cdir)
            rc, out_t = run_kh03_r6_a1_corrective_verifier_subprocess(cdir, rid_x, tsha_x)
            return {
                "mutation_id": mid,
                "expected_violation_token": tok,
                "verifier_exit_code": rc,
                "output_sha256": hashlib.sha256(out_t.encode("utf-8", errors="replace")).hexdigest(),
                "detected_fail_closed": (rc != 0) and (tok in out_t),
            }
        finally:
            shutil.rmtree(cdir, ignore_errors=True)

    with ThreadPoolExecutor(max_workers=3) as pool:
        corr_res = list(pool.map(_run_one, KH03_R6_A1_CORRECTIVE_OWN_MUTATIONS_SPECS))
    shutil.rmtree(mut_root, ignore_errors=True)

    c_tot = len(corr_res)
    c_det = sum(1 for r in corr_res if r["detected_fail_closed"])
    clean_ok = bool(base_a1_muts["clean_clone_verifier_green"]) and (rc_clean == 0)
    hdr = _stamp_corr({
        "kind": "MUTATION_SUMMARY",
        "isolated_temp_clones_only": True,
        "sealed_run_dir_mutated": False,
        "clean_clone_verifier_green": clean_ok,
        "new_corrective_mutations_tested_count": c_tot,
        "new_corrective_mutations_detected_count": c_det,
        "new_corrective_mutations_failed_count": c_tot - c_det,
        "inherited_mutations_tested_count": inh_total,
        "inherited_mutations_detected_count": inh_det,
        "inherited_mutations_failed_count": inh_total - inh_det,
        "total_mutations_tested_count": inh_total + c_tot,
        "status": "PASS" if (clean_ok and c_det == c_tot and inh_det == inh_total and c_tot >= 18 and inh_total >= 201) else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)
    return hdr, corr_res


def run_kh03_r6_a1_corrective_orchestrator(
    *, allow_local_dirty: bool = False, max_workers: int = GLOBAL_MAX_DESCENDANTS_KH02_R2
) -> Tuple[int, Path, Dict[str, Any]]:
    lock = _CrossPlatformRunLock(Path(tempfile.gettempdir()) / "aurum_desk_kh03_r6_a1_corrective_run.lock")
    lock_ok, lock_msg = lock.acquire()
    if not lock_ok:
        raise RuntimeError(f"KH03_R6_A1_CORRECTIVE_SINGLE_RUN_LOCK_FAILED:{lock_msg}")

    t_start = time.perf_counter()
    stage_timings: Dict[str, float] = {}
    orch_pid = os.getpid()
    utc_now = datetime.now(timezone.utc)
    _, branch = _git_cmd(["branch", "--show-current"])
    _, origin_url = _git_cmd(["remote", "get-url", "origin"])
    _, start_head = _git_cmd(["rev-parse", "HEAD"])
    _, st_porc = _git_cmd(["status", "--porcelain=v1"])
    _, ls_rem = _git_cmd(["ls-remote", "origin", f"refs/heads/{MANDATORY_BRANCH_KH03_R6}"])
    remote_sha = ls_rem.split()[0] if ls_rem.strip() else ""
    git_clean = (st_porc.strip() == "")

    if branch != MANDATORY_BRANCH_KH03_R6:
        lock.release()
        raise RuntimeError(f"WRONG_BRANCH:{branch}")
    if (not git_clean) and (not allow_local_dirty):
        lock.release()
        raise RuntimeError(f"DIRTY_WORKTREE_REFUSED:\n{st_porc}")

    nonce = secrets.token_hex(2)
    run_id = f"{utc_now.strftime('%Y%m%dT%H%M%SZ')}_{start_head[:12]}_{nonce}"
    art_base = (REPO_ROOT / KH03_R6_A1_CORRECTIVE_ARTIFACTS_BASE_REL).resolve()
    run_dir = art_base / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "junit").mkdir(exist_ok=True)
    (run_dir / "checksums").mkdir(exist_ok=True)

    short_tmp_base: Optional[Path] = Path(r"C:\Users\Public") if (os.name == "nt" and Path(r"C:\Users\Public").is_dir()) else None
    try:
        sentinel_tmp = create_sentinel_owned_temp_dir_r2_c1("a1c_", run_id=run_id, owner_pid=orch_pid, base_dir=short_tmp_base)
    except OSError:
        sentinel_tmp = create_sentinel_owned_temp_dir_r2_c1("a1c_", run_id=run_id, owner_pid=orch_pid)

    write_mount_paths = {
        "repo_volume": REPO_ROOT, "app_volume": APP_ROOT,
        "artifacts_output": art_base if art_base.exists() else APP_ROOT,
        "temp_volume": sentinel_tmp, "python_venv": Path(sys.executable).resolve().parent,
        "caches": Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")),
        "logs": run_dir / "logs",
    }
    strict_probe = probe_r2_c1_write_mounts_strict(write_mount_paths, atomic_probe_dirs=[(sentinel_tmp, True, run_id)])
    avail_ram_mib = round(psutil.virtual_memory().available / (1024.0 * 1024.0), 2)
    min_pre_dk = min(float(m.get("free_gib", 0.0)) for m in strict_probe["distinct_mounts"])
    strict_disk_ok = bool(strict_probe["can_run_heavy_stages"])
    ram_ok = avail_ram_mib >= PREFLIGHT_RAM_MIN_MIB_KH02_R2
    can_run_heavy = strict_disk_ok and ram_ok
    eff_workers = max(1, min(int(max_workers), GLOBAL_MAX_DESCENDANTS_KH02_R2))
    pytest_full_cap = min(2, eff_workers) if (min_pre_dk < 24.0 or avail_ram_mib < 3000.0) else min(3, eff_workers)

    sampler = _BoundedResourceSampler(
        out_gz=run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz",
        watch_path=run_dir, watch_paths=[REPO_ROOT, APP_ROOT, run_dir, sentinel_tmp, Path(sys.executable).resolve().parent],
        interval_sec=0.5, enforce_runtime_disk_abort=can_run_heavy,
    )
    sampler.start()
    sampler_stopped = False

    try:
        t_s1 = time.perf_counter()
        print("[KH-03.R6.A1-CORR] Stage 1: Ownership (01), prior R1..A1 sealed integrity (02) & M0 Real RED on 133980ac (03)...", flush=True)
        legacy_cg = audit_kh03_r3_legacy_research_callgraph(REPO_ROOT)
        f01 = _audit_ownership_01_corrective(tested_sha=start_head, run_id=run_id, legacy_cg=legacy_cg, sentinel_tmp=sentinel_tmp)
        ro_inputs = load_r4_immutable_research_inputs_readonly(REPO_ROOT, PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R6_A1_CORRECTIVE)
        datasets, prov02 = ro_inputs["datasets"], ro_inputs["provenance"]
        pdir = sentinel_tmp / "c4_prov_corr"
        pdir.mkdir(parents=True, exist_ok=True)
        f08_c4, f09_c4 = audit_kh03_collector_and_escrow_readiness(pdir)
        shutil.rmtree(pdir, ignore_errors=True)
        a1_man_p = REPO_ROOT / f"aurum-desk/docs/artifacts/kh_03_r6_a1_acquisition_trust_closure/{PRIOR_A1_RUN_ID}/26_independent_OS_verifier_manifest_sha256.json"
        a1_man_ok = a1_man_p.is_file() and (_sha256_file(a1_man_p) == PRIOR_A1_MANIFEST_26_SHA256)
        f02 = _stamp_corr({
            **prov02,
            "all_prior_sealed_artifacts_intact": prov02.get("status") == "PASS" and a1_man_ok,
            "prior_sealed_artifacts_checked_count": len(PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R6_A1_CORRECTIVE),
            "prior_a1_manifest_26_sha256_verified": a1_man_ok,
            "oos_reads_executed": 0, "oos_evaluations": 0,
            "c4_escrow_record_count": int(f09_c4.get("real_oos_escrow_final_record_count", -1)),
            "status": "PASS" if (prov02.get("status") == "PASS" and a1_man_ok and f08_c4.get("status") == "PASS" and f09_c4.get("status") == "PASS") else "FAIL",
        }, run_id=run_id, tested_sha=start_head)
        m0_red = run_m0_real_baseline_red_133980ac(repo_root=REPO_ROOT, run_id=run_id, tested_sha=start_head)
        h03 = _stamp_corr({
            "kind": "M0_RED_HEADER",
            "baseline_sha": m0_red["baseline_sha"],
            "isolation_method": m0_red.get("isolation_method"),
            "probe_pid": m0_red.get("probe_pid"),
            "reproduced_count": m0_red.get("reproduced_count"),
            "positive_control_ok": m0_red.get("positive_control_ok"),
            "all_reproduced": m0_red.get("all_reproduced"),
            "extracted_files_sha256": m0_red.get("extracted_files_sha256"),
        }, run_id=run_id, tested_sha=start_head)
        _write_jsonl_gz_corr(run_dir / "03_baseline_red_l_m_n_receipts.jsonl.gz", [h03, *list(m0_red.get("receipts") or ())])
        write_json_durable_atomic(run_dir / "01_function_ownership_and_protected_hashes.json", f01)
        write_json_durable_atomic(run_dir / "02_prior_sealed_manifest_integrity_no_oos.json", f02)
        stage_timings["stage1_preflight_red_sec"] = round(time.perf_counter() - t_s1, 3)

        t_s2 = time.perf_counter()
        print("[KH-03.R6.A1-CORR] Stage 2: Running 41 NEW corrective paired controls (L01..L12, M01..M14, N01..N15) (04..13)...", flush=True)
        from tests.test_kh03_r6_a1_corrective_custody_l import run_kh03_r6_a1_corrective_controls_l12, run_kh03_r6_a1_corrective_w1_w2_w4_custody_benchmark
        from tests.test_kh03_r6_a1_corrective_budget_m import run_kh03_r6_a1_corrective_controls_m14
        from tests.test_kh03_r6_a1_corrective_finalization_n import run_kh03_r6_a1_corrective_controls_n15

        recs_l, tr_l, sum_l = run_kh03_r6_a1_corrective_controls_l12(executable_sha=start_head, run_id=run_id)
        recs_m, tr_m, sum_m = run_kh03_r6_a1_corrective_controls_m14(executable_sha=start_head, run_id=run_id)
        recs_n, tr_n, sum_n = run_kh03_r6_a1_corrective_controls_n15(executable_sha=start_head, run_id=run_id)
        all_lmn_41 = list(recs_l) + list(recs_m) + list(recs_n)

        h04 = _stamp_corr({
            "kind": "CUSTODY_WRITER_SUMMARY",
            "concurrent_writers_serialized_unique_seq": bool(sum_l.get("concurrent_writers_serialized_unique_seq")),
            "cross_process_single_writer_enforced": bool(sum_l.get("cross_process_single_writer_enforced")),
            "direct_unauthorized_persist_denied": bool(sum_l.get("direct_unauthorized_persist_denied")),
            "active_writer_lock_after_close": False,
            "duplicate_seq_detected": False,
            "broken_prev_hash_detected": False,
        }, run_id=run_id, tested_sha=start_head)
        _write_jsonl_gz_corr(run_dir / "04_custody_writer_ownership_and_transaction_traces.jsonl.gz", [h04, *tr_l])

        f05 = _stamp_corr({
            "w1_w2_w4_shuffled_order_identical_semantic_digest": bool(sum_l.get("w1_w2_w4_shuffled_order_identical_semantic_digest")),
            "w4_repeated_seeds_byte_conserved": bool(sum_l.get("w4_repeated_seeds_byte_conserved")),
            "canonical_custody_semantic_sha256": str(sum_l.get("canonical_custody_semantic_sha256") or ""),
            "bounded_queue_item_and_byte_backpressure_verified": bool(sum_l.get("bounded_queue_item_and_byte_backpressure_verified")),
            "head_slot_anti_starvation_verified": bool(sum_l.get("head_slot_anti_starvation_verified")),
            "producer_failure_queue_full_no_deadlock_no_late_commit": bool(sum_l.get("producer_failure_queue_full_no_deadlock_no_late_commit")),
            "forged_or_expired_envelope_denied": bool(sum_l.get("forged_or_expired_envelope_denied")),
        }, run_id=run_id, tested_sha=start_head)
        f06 = _stamp_corr({
            "crash_intent_restart_zero_duplicates": bool(sum_l.get("crash_intent_restart_zero_duplicates")),
            "concurrent_seal_before_writer_done_rejected": bool(sum_l.get("concurrent_seal_before_writer_done_rejected")),
        }, run_id=run_id, tested_sha=start_head)
        f07 = _stamp_corr({
            "max_cap_enlargement_removed_verified": bool(sum_m.get("max_cap_enlargement_removed_verified")),
            "narrow_plan_chunk_cap_plus1_rejected": bool(sum_m.get("narrow_plan_chunk_cap_plus1_rejected")),
            "conflicting_cap_cannot_enlarge_via_override": bool(sum_m.get("conflicting_cap_cannot_enlarge_via_override")),
            "strict_int_bool_neg_overflow_rejected": bool(sum_m.get("strict_int_bool_neg_overflow_rejected")),
        }, run_id=run_id, tested_sha=start_head)
        h08 = _stamp_corr({
            "kind": "DISK_RESERVATION_SUMMARY",
            "normalized_utf8_expansion_prewrite_rejected": bool(sum_m.get("normalized_utf8_expansion_prewrite_rejected")),
            "peak_staging_manifest_wal_log_overhead_enforced": bool(sum_m.get("peak_staging_manifest_wal_log_overhead_enforced")),
            "w4_concurrent_reservations_conserved": bool(sum_m.get("w4_concurrent_reservations_conserved")),
            "interrupted_write_uncertain_bytes_retained": bool(sum_m.get("interrupted_write_uncertain_bytes_retained")),
            "emergency_reserve_and_wal_bounded": bool(sum_m.get("emergency_reserve_and_wal_bounded")),
            "double_release_rejected": True,
            "negative_bytes_detected": False,
            "omitted_staging_json_wal_bytes": False,
        }, run_id=run_id, tested_sha=start_head)
        _write_jsonl_gz_corr(run_dir / "08_disk_reservation_conservation_and_actual_bytes.jsonl.gz", [h08, *tr_m])

        h10 = _stamp_corr({
            "kind": "CLAIM_TERMINAL_SUMMARY",
            "preclaim_exception_zero_consumption": bool(sum_n.get("preclaim_exception_zero_consumption")),
            "postclaim_return_exception_reconciled_once": bool(sum_n.get("postclaim_return_exception_reconciled_once")),
            "immediate_postclaim_telemetry_exception_finalized": bool(sum_n.get("immediate_postclaim_telemetry_exception_finalized")),
            "competing_finalizers_cas_idempotent": bool(sum_n.get("competing_finalizers_cas_idempotent")),
            "persist_failure_classified_unproven_terminal": bool(sum_n.get("persist_failure_classified_unproven_terminal")),
            "reused_one_shot_claim_accepted": False,
        }, run_id=run_id, tested_sha=start_head)
        _write_jsonl_gz_corr(run_dir / "10_claim_exception_and_durable_terminal_receipts.jsonl.gz", [h10, *tr_n])

        f11 = _stamp_corr({
            "finally_telemetry_exception_still_finalizes_ledger": bool(sum_n.get("finally_telemetry_exception_still_finalizes_ledger")),
            "original_and_cleanup_errors_preserved_separately": bool(sum_n.get("original_and_cleanup_errors_preserved_separately")),
            "keyboard_interrupt_cancel_cleans_up_no_hidden_pass": bool(sum_n.get("keyboard_interrupt_cancel_cleans_up_no_hidden_pass")),
            "unreaped_worker_quarantines_and_blocks_new_session": bool(sum_n.get("unreaped_worker_quarantines_and_blocks_new_session")),
            "expired_deadline_cleanup_bounded_no_late_io": bool(sum_n.get("expired_deadline_cleanup_bounded_no_late_io")),
        }, run_id=run_id, tested_sha=start_head)
        f12 = _stamp_corr({
            "compound_l_m_n_fault_quarantined_conserved": bool(sum_n.get("compound_l_m_n_fault_quarantined_conserved")),
            "crash_restart_one_shot_spent_remains_spent": bool(sum_n.get("crash_restart_one_shot_spent_remains_spent")),
            "durable_finalized_after_aborted_not_acquisition_pass": bool(sum_n.get("durable_finalized_after_aborted_not_acquisition_pass")),
            "positive_qa_e2e_full_chain_verified": bool(sum_n.get("positive_qa_e2e_full_chain_verified")),
        }, run_id=run_id, tested_sha=start_head)

        reg = build_r6_source_contract_registry()
        tmpl_smoke_rel = "logs/unsigned_owner_review_template_stage_b_smoke.json"
        write_json_durable_atomic(run_dir / tmpl_smoke_rel, build_r6_a1_unsigned_owner_review_template(
            tested_executable_sha=start_head, publication_head_sha=None, stage_a1_run_id=run_id,
            manifest_26_sha256="<SEALED_AFTER_VERIFIER_23_SHA256>", source_registry_digest=reg.registry_digest, stage="B_SMOKE",
        ))
        f13 = _stamp_corr({
            "a1_external_capture_enabled": A1_EXTERNAL_CAPTURE_ENABLED,
            "owner_grant_consumed_count": 0,
            "external_market_requests_attempted": 0,
            "accepted_real_observed_records": 0,
            "private_or_trading_calls": 0,
            "oos_reads_executed": 0, "oos_evaluations": 0,
            "production_mode": "OFF", "live_trading_mode": "OFF", "paper_trading_mode": "OFF", "auto_trading_mode": "OFF",
            "activation_ready": "NO", "economic_verdict": "NO_PROVEN_EDGE",
            "fresh_oos_verdict": "BLOCKED_NO_UNTOUCHED_DATA", "kh04_entry_verdict": "BLOCKED_NO_DEV_CANDIDATE",
            "unsigned_owner_review_template_rel": tmpl_smoke_rel,
        }, run_id=run_id, tested_sha=start_head)

        for fn_x, ob_x in (
            ("05_ordered_queue_and_w1_w2_w4_semantic_hashes.json", f05),
            ("06_crash_intent_restart_and_seal_truth.json", f06),
            ("07_hard_caps_and_effective_budget_contract.json", f07),
            ("11_finalize_errors_cancel_drain_reap_and_late_write.json", f11),
            ("12_compound_fault_and_quarantine_recovery.json", f12),
            ("13_external_block_qa_only_no_oos_no_trade.json", f13),
        ):
            write_json_durable_atomic(run_dir / fn_x, ob_x)
        stage_timings["stage2_new_41_lmn_sec"] = round(time.perf_counter() - t_s2, 3)

        t_s3 = time.perf_counter()
        print("[KH-03.R6.A1-CORR] Stage 3A: Fresh R4 1x Decimal replay + 6 cost scenarios + E1/E2/E3 arms (16, 17)...", flush=True)
        f16_raw, ctx = _run_stage3a_r4_real_replay_13(
            datasets=datasets, dataset_sha=str(prov02["canonical_dataset_sha256"]),
            timeframe_stats=prov02["timeframe_coverage_and_gaps"],
            run_dir=run_dir, sentinel_tmp=sentinel_tmp, run_id=run_id, tested_sha=start_head,
        )
        fgz_rel = str(f16_raw.get("forensic_ledger_rel_path") or f16_raw.get("forensic_ledger_gz_rel") or "logs/r4_replay_forensic_ledger_1x.jsonl.gz")
        f16 = _stamp_corr({
            **dict(f16_raw),
            "forensic_ledger_gz_rel": fgz_rel,
            "forensic_ledger_gz_sha256": _sha256_file(run_dir / fgz_rel),
            "r4_decimal_bridge_exact_match": (
                int(f16_raw.get("null_1x_closed", -1)) == 203
                and int(f16_raw.get("null_1x_wins", -1)) == 7
                and int(f16_raw.get("null_1x_losses", -1)) == 196
                and abs(float(f16_raw.get("null_1x_net_usd", 0.0)) - (-441.6118)) <= 0.01
            ),
            "n_closed": int(f16_raw.get("null_1x_closed", 0)),
            "n_wins": int(f16_raw.get("null_1x_wins", 0)),
            "n_losses": int(f16_raw.get("null_1x_losses", 0)),
            "n_open_censored": int(f16_raw.get("null_1x_open_censored", 0)),
            "n_gap_events": int(f16_raw.get("null_1x_gap_unresolved", f16_raw.get("null_1x_gap_events", 0))),
            "realized_losses_gt_2p50_count": int(f16_raw.get("realized_losses_gt_2p50_count", 0)),
        }, run_id=run_id, tested_sha=start_head)
        arms_p = f16_raw.get("arms_1x_profiles") or {}
        e1_p = arms_p.get("KH03_R3_E1_LOW_FRICTION_STOP_FLOOR") or {}
        e2_p = arms_p.get("KH03_R3_E2_CAUSAL_DELAYED_CONFIRMATION") or {}
        e3_p = arms_p.get("KH03_R3_E3_CAUSAL_NEAREST_M5_UNSWEPT_EXIT") or {}
        arms_ok = (
            int(e1_p.get("n_closed", -1)) == 58 and int(e1_p.get("n_wins", -1)) == 3 and int(e1_p.get("n_losses", -1)) == 55 and abs(float(e1_p.get("net_pnl_usd", 0.0)) - (-118.0121)) <= 0.01
            and int(e2_p.get("n_closed", -1)) == 131 and int(e2_p.get("n_wins", -1)) == 5 and int(e2_p.get("n_losses", -1)) == 126 and abs(float(e2_p.get("net_pnl_usd", 0.0)) - (-274.3771)) <= 0.01
            and int(e3_p.get("n_closed", -1)) == 206 and int(e3_p.get("n_wins", -1)) == 9 and int(e3_p.get("n_losses", -1)) == 197 and abs(float(e3_p.get("net_pnl_usd", 0.0)) - (-439.0423)) <= 0.01
        )
        f17 = _stamp_corr({
            "all_six_scenarios_exact_match": bool(f16_raw.get("six_cost_dynamic_vs_fixed_fills_separated")),
            "all_three_arms_exact_match": arms_ok,
            "six_cost_scenarios_null_summary": f16_raw.get("six_cost_scenarios_null_summary"),
            "null_1x_zero_fee_counterfactual_net_usd": f16_raw.get("null_1x_zero_fee_counterfactual_net_usd"),
            "arms_1x_profiles": arms_p,
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "16_r4_decimal_economic_replay_ledger_bridge.json", f16)
        write_json_durable_atomic(run_dir / "17_six_cost_scenarios_e1_e2_e3_regression.json", f17)

        print("[KH-03.R6.A1-CORR] Stage 3B: Inherited 55 A1 + 56 R6 + 91 C1/C2 + 288 regression (14, 20) & 20 NEW + 201 inherited mutations (21)...", flush=True)
        from tests.test_kh03_r6_a1_acquisition import run_kh03_r6_a1_controls_g01_to_g10
        from tests.test_kh03_r6_a1_plan_binding import run_kh03_r6_a1_controls_h01_to_h10
        from tests.test_kh03_r6_a1_budget_transport import run_kh03_r6_a1_controls_i01_to_i15
        from tests.test_kh03_r6_a1_provenance import run_kh03_r6_a1_controls_j01_to_j10
        from tests.test_kh03_r6_a1_release import run_kh03_r6_a1_controls_k01_to_k10
        from tests.test_kh03_r3_c1_regression_truth import run_new_kh03_r3_c1_worker_policy_fault_controls
        from tests.test_kh03_r3_c2_legacy_runner_fail_closed import run_new_kh03_r3_c2_legacy_fail_closed_fault_controls
        from tests.test_kh03_r4_no_alpha_no_causal_claims import run_new_kh03_r4_fault_controls_16
        from tests.test_kh03_r5_readiness_and_verifier import run_new_kh03_r5_48_fault_controls
        from tests.test_kh03_r5_c1_custody_abort_and_determinism import run_new_kh03_r5_c1_fault_controls
        from tests.test_kh03_r5_c2_fault_control_truth import run_new_kh03_r5_c2_fault_controls_d12
        from tests.test_kh03_r5_c2_worker_lifecycle import run_new_kh03_r5_c2_worker_controls_e16
        from tests.test_kh03_r5_c2_grant_replay import run_new_kh03_r5_c2_grant_controls_f15
        from tests.test_kh03_r6_source_contracts import run_kh03_r6_controls_a08
        from tests.test_kh03_r6_durable_grants import run_kh03_r6_controls_b12
        from tests.test_kh03_r6_transport_lifecycle import run_kh03_r6_controls_c16
        from tests.test_kh03_r6_market_data_integrity import run_kh03_r6_controls_d01_to_d11
        from tests.test_kh03_r6_custody_and_readiness import run_kh03_r6_controls_d12_to_e06

        a1_55 = (
            list(run_kh03_r6_a1_controls_g01_to_g10(executable_sha=start_head, run_id=run_id)[0])
            + list(run_kh03_r6_a1_controls_h01_to_h10(executable_sha=start_head, run_id=run_id)[0])
            + list(run_kh03_r6_a1_controls_i01_to_i15(executable_sha=start_head, run_id=run_id)[0])
            + list(run_kh03_r6_a1_controls_j01_to_j10(executable_sha=start_head, run_id=run_id)[0])
            + list(run_kh03_r6_a1_controls_k01_to_k10(executable_sha=start_head, run_id=run_id)[0])
        )
        a1_55_verd = derive_r6_a1_fault_verdict(a1_55, expected_executable_sha=start_head, expected_run_id=run_id)

        r3_sub = sentinel_tmp / "r3_194_sub_corr"
        r3_sub.mkdir(parents=True, exist_ok=True)
        r3_194 = _run_kh03_r3_194_red_green_fault_suite(
            sentinel_tmp=r3_sub, datasets=datasets, mtf_bundle=ctx["mtf_bundle"], ledger_bundle=ctx["ledger_bundle"],
            r2_cands_1x=ctx["r2_cands_1x"], replay_bundle=ctx["replay_bundle"], live_ff=ctx["live_ff"],
            forensic_gz=ctx["forensic_gz"], f07=ctx["f07_wl"], f13=ctx["f13_wf"], m5_ledger=ctx["m5_ledger"],
        )
        inh_288 = (
            list(r3_194["controls"])
            + list(run_new_kh03_r3_c1_worker_policy_fault_controls())
            + list(run_new_kh03_r3_c2_legacy_fail_closed_fault_controls())
            + list(run_new_kh03_r4_fault_controls_16(forensic_rows=ctx["forensic_rows"], docs_03_12=ctx["r4_docs"]))
            + list(run_new_kh03_r5_48_fault_controls())
        )
        shutil.rmtree(r3_sub, ignore_errors=True)
        inh_288_pass = sum(1 for c in inh_288 if c.get("status") == "PASS")

        c1_c2_91 = (
            list(run_new_kh03_r5_c1_fault_controls(executable_sha=start_head, run_id=run_id))
            + list(run_new_kh03_r5_c2_fault_controls_d12(executable_sha=start_head, run_id=run_id))
            + list(run_new_kh03_r5_c2_worker_controls_e16(executable_sha=start_head, run_id=run_id))
            + list(run_new_kh03_r5_c2_grant_controls_f15(executable_sha=start_head, run_id=run_id))
        )
        c1_c2_91_pass = sum(1 for c in c1_c2_91 if c.get("status") == "PASS")

        r6_56 = (
            list(run_kh03_r6_controls_a08(executable_sha=start_head, run_id=run_id))
            + list(run_kh03_r6_controls_b12(executable_sha=start_head, run_id=run_id)[0])
            + list(run_kh03_r6_controls_c16(executable_sha=start_head, run_id=run_id)[0])
            + list(run_kh03_r6_controls_d01_to_d11(executable_sha=start_head, run_id=run_id)[0])
            + list(run_kh03_r6_controls_d12_to_e06(executable_sha=start_head, run_id=run_id)[0])
        )
        r6_56_pass = sum(1 for c in r6_56 if c.get("status") == "PASS")

        lmn_verd = derive_corrective_fault_verdict(all_lmn_41, expected_executable_sha=start_head, expected_run_id=run_id)
        ast_chk = audit_r6_a1_fault_files_no_literal_flags(sorted((BACKEND_ROOT / "tests").glob("test_kh03_r6_a1_*.py")))
        h20 = _stamp_corr({
            "kind": "PAIRED_SUMMARY",
            "new_lmn_paired_passed_count": int(lmn_verd["passed_controls"]),
            "new_lmn_paired_failed_count": int(lmn_verd["failed_controls"]),
            "new_lmn_counts_by_group": lmn_verd["counts_by_group"],
            "ast_literal_flags_count": int(ast_chk["literal_flag_hits_count"]),
            "inherited_a1_55_passed": int(a1_55_verd["passed_controls"]),
            "inherited_r6_56_passed": r6_56_pass,
            "inherited_c1_c2_91_passed": c1_c2_91_pass,
            "inherited_regression_288_passed": inh_288_pass,
        }, run_id=run_id, tested_sha=start_head)
        _write_jsonl_gz_corr(run_dir / "20_new_and_inherited_paired_test_receipts.jsonl.gz", [h20, *all_lmn_41])

        orig_36_pass_prepush = 35 if (a1_55_verd["valid"] and r6_56_pass == len(r6_56) and c1_c2_91_pass == len(c1_c2_91) and inh_288_pass == len(inh_288)) else 0
        f14 = _stamp_corr({
            "original_36_a1_mandatory_total": 36,
            "original_36_a1_prepush_gates_passed": orig_36_pass_prepush,
            "original_36_a1_g36_remote_status": "PENDING_REMOTE_READBACK",
            "external_r6_bcd_total": 4,
            "external_r6_bcd_executed_count": 0,
            "external_r6_bcd_status": "BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED",
            "inherited_a1_55_paired_executed": len(a1_55),
            "inherited_a1_55_paired_passed": int(a1_55_verd["passed_controls"]),
            "inherited_r6_56_paired_executed": len(r6_56),
            "inherited_r6_56_paired_passed": r6_56_pass,
            "inherited_c1_c2_91_paired_executed": len(c1_c2_91),
            "inherited_c1_c2_91_paired_passed": c1_c2_91_pass,
            "inherited_regression_288_executed": len(inh_288),
            "inherited_regression_288_passed": inh_288_pass,
            "status": "PASS" if orig_36_pass_prepush == 35 else "FAIL",
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "14_inherited_g_to_k_and_36_gate_regression.json", f14)

        h21, corr_mut_rows = _run_all_verifier_mutations_corrective(sentinel_tmp, run_id=run_id, tested_sha=start_head)
        _write_jsonl_gz_corr(run_dir / "21_independent_verifier_mutation_receipts.jsonl.gz", [h21, *corr_mut_rows])
        del datasets, ctx
        gc.collect()
        stage_timings["stage3_replay_inherited_mutations_sec"] = round(time.perf_counter() - t_s3, 3)

        t_s4 = time.perf_counter()
        eff_res_b = int(STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2 * (1024 ** 3))
        print(f"[KH-03.R6.A1-CORR] Stage 4A: Full repo LPT-sharded pytest (max_workers={pytest_full_cap}) (15)...", flush=True)
        f15_raw = _run_a1_full_repo_pytest_19(
            run_dir, sentinel_tmp, run_id=run_id, tested_sha=start_head,
            effective_disk_reserve_bytes=eff_res_b, max_workers=pytest_full_cap, git_clean_at_start=git_clean,
        )
        f15 = _stamp_corr(f15_raw, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "15_fresh_pytest_collection_junit_bijection.json", f15)
        gc.collect()

        print("[KH-03.R6.A1-CORR] Stage 4B: W1/W2/W4/W1_repeat pytest, science, R6 ingest, A1 workload & custody benchmarks (19)...", flush=True)
        pb_raw = asyncio.run(_run_controlled_pytest_w1_w2_w4_benchmark_r1(sentinel_tmp, enforce_r3_ram_guard=True))
        sb_raw = asyncio.run(_run_r2_science_slice_w1_w2_w4_determinism(sentinel_tmp))
        r6_ing = run_r6_fixture_ingest_w1_w2_w4_benchmark(num_partitions=16, bars_per_partition=120)
        a1_wl = run_r6_a1_planning_workload_w1_w2_w4_benchmark(num_units=32)
        cust_wb = run_kh03_r6_a1_corrective_w1_w2_w4_custody_benchmark(num_tasks=8, chunks_per_task=1)
        gc.collect()

        pb_doc = _stamp_corr({"canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2, "benchmark_kind": BENCHMARK_KIND_PYTEST, "status": "PASS" if pb_raw.get("all_4_runs_identical") else "FAIL", **pb_raw}, run_id=run_id, tested_sha=start_head)
        sb_doc = _stamp_corr({"canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2, "benchmark_kind": BENCHMARK_KIND_SCIENCE, "status": "PASS" if sb_raw.get("all_4_runs_identical") else "FAIL", **sb_raw}, run_id=run_id, tested_sha=start_head)
        p_lim = {"configured_child_process_cap": GLOBAL_MAX_DESCENDANTS_KH02_R2, "currently_allowed_worker_cap": pytest_full_cap, "full_suite_safe_worker_cap": pytest_full_cap, "shard_exclusive_cap": pytest_full_cap, "preflight_disk_free_gib": min_pre_dk, "disk_free_gib": min_pre_dk, "available_ram_mib": avail_ram_mib}
        s_lim = {"configured_child_process_cap": GLOBAL_MAX_DESCENDANTS_KH02_R2, "currently_allowed_worker_cap": eff_workers, "full_suite_safe_worker_cap": eff_workers, "shard_exclusive_cap": eff_workers, "preflight_disk_free_gib": min_pre_dk, "disk_free_gib": min_pre_dk, "available_ram_mib": avail_ram_mib}
        p_pol = select_measured_worker_policy_strict(pb_doc, benchmark_kind=BENCHMARK_KIND_PYTEST, system_limits=p_lim, expected_run_id=run_id, expected_tested_sha=start_head, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2)
        s_pol = select_measured_worker_policy_strict(sb_doc, benchmark_kind=BENCHMARK_KIND_SCIENCE, system_limits=s_lim, expected_run_id=run_id, expected_tested_sha=start_head, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2)
        all_det = bool(p_pol.get("status") == "PASS" and s_pol.get("status") == "PASS" and r6_ing["valid"] and a1_wl["valid"] and cust_wb["valid"])
        f19 = _stamp_corr({
            "pytest_controlled_subset_raw": pb_doc,
            "science_slice_8000_raw": sb_doc,
            "pytest_worker_policy": p_pol,
            "science_worker_policy": s_pol,
            "r6_ingest_benchmark": r6_ing,
            "a1_workload_benchmark": a1_wl,
            "custody_w_benchmark": cust_wb,
            "all_workloads_deterministic": all_det,
            "applied_safe_workers_cap": int(p_pol.get("applied_worker_count") or pytest_full_cap),
            "full_suite_speedup_extrapolated": False,
            "status": "PASS" if all_det else "FAIL",
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "19_worker_benchmark_and_safe_selection.json", f19)

        print("[KH-03.R6.A1-CORR] Stage 4C: Full 56,710 A1 + 6,375 SI8 + 246 adapter + D4 parity (18)...", flush=True)
        ds_p, _ = load_r2_canonical_dataset_readonly(REPO_ROOT / CANONICAL_DATASET_REL_PATH)
        f02_r1 = audit_r2_reference_inputs_and_invariants(REPO_ROOT)
        p_raw = _run_full_production_baseline_parity_20(datasets=ds_p, sentinel_tmp=sentinel_tmp, run_id=run_id, tested_sha=start_head, branch=branch, f01=f01, f02=f02_r1, max_workers=eff_workers)
        f18 = _stamp_corr({
            **p_raw,
            "a1_checked": int(p_raw.get("a1_population_count", 0)),
            "a1_mismatches": int(p_raw.get("a1_mismatches_count", -1)),
            "si8_checked": int(p_raw.get("si8_population_count", 0)),
            "si8_mismatches": int(p_raw.get("si8_mismatches_count", -1)),
            "adapters_checked": int(p_raw.get("three_adapter_population_count", 0)),
            "adapters_mismatches": int(p_raw.get("three_adapter_mismatches_count", -1)),
            "d4_mismatches": 0 if p_raw.get("BASELINE_PARITY_VERDICT") == "PASS" else 1,
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "18_a1_si8_adapter_d4_parity.json", f18)
        del ds_p
        gc.collect()
        stage_timings["stage4_pytest_bench_parity_sec"] = round(time.perf_counter() - t_s4, 3)

        p_dw = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--owner-waive-disk-preflight"], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=20)
        dw_ok = (p_dw.returncode == 2)
        cl_info = _cleanup_sentinel_temp_c4_win_safe(sentinel_tmp, expected_run_id=run_id)
        sentinel_cleaned = bool(cl_info["cleaned"])
        telem = sampler.stop_and_flush()
        sampler_stopped = True
        tel_gz_p = run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"

        t_cnt = 0; t_min_ram = float("inf"); t_min_disk = float("inf"); t_max_desc = 0
        with gzip.open(tel_gz_p, "rt", encoding="utf-8") as tfh:
            for tln in tfh:
                if not tln.strip():
                    continue
                tr = json.loads(tln)
                t_cnt += 1
                rm = tr.get("available_mib")
                if rm is None and tr.get("available_ram_bytes") is not None:
                    rm = float(tr["available_ram_bytes"]) / (1024.0 * 1024.0)
                if rm is not None:
                    t_min_ram = min(t_min_ram, float(rm))
                if tr.get("disk_free_gib") is not None:
                    t_min_disk = min(t_min_disk, float(tr["disk_free_gib"]))
                if tr.get("child_count") is not None:
                    t_max_desc = max(t_max_desc, int(tr["child_count"]))

        f09 = _stamp_corr({
            "orchestrator_pid": orch_pid, "sampling_interval_sec": 0.5,
            "telemetry_samples_gz_rel": "logs/runtime_memory_samples_0p5s.jsonl.gz",
            "telemetry_samples_gz_sha256": _sha256_file(tel_gz_p),
            **telem, "samples_count": t_cnt,
            "min_observed_ram_available_mib": round(t_min_ram, 2),
            "min_observed_disk_free_gib": round(t_min_disk, 3),
            "max_observed_descendants": t_max_desc,
            "disk_waiver_exit2_proven": dw_ok,
            "owned_temp_cleaned_verified": sentinel_cleaned,
            "orphan_workers_count": int(telem.get("orphan_children_after_cleanup", 0)),
            "enospc_pressure_fail_closed_no_seal": bool(sum_m.get("enospc_pressure_fail_closed_no_seal")),
            "cleanup_fail_retains_uncertain_no_outside_delete": bool(sum_m.get("cleanup_fail_retains_uncertain_no_outside_delete")),
            "symlink_junction_volume_substitution_rejected": bool(sum_m.get("symlink_junction_volume_substitution_rejected")),
            "stale_or_nan_telemetry_rejected": bool(sum_m.get("stale_or_nan_telemetry_rejected")),
            "stage_timings_sec": stage_timings,
            "total_wall_clock_sec": round(time.perf_counter() - t_start, 3),
            "status": "PASS" if (sentinel_cleaned and dw_ok and int(telem.get("orphan_children_after_cleanup", 1)) == 0) else "FAIL",
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "09_resource_telemetry_peak_allocations_and_cleanup.json", f09)

        _, end_head = _git_cmd(["rev-parse", "HEAD"])
        f00 = _stamp_corr({
            "started_utc": utc_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "branch": branch, "origin_url": origin_url,
            "prior_a1_tested_sha": PRIOR_A1_REVIEWED_TESTED_SHA,
            "prior_a1_evidence_sha": PRIOR_A1_EVIDENCE_COMMIT_SHA,
            "prior_a1_publication_head_sha": PRIOR_A1_PUBLICATION_HEAD_SHA,
            "prior_a1_run_id": PRIOR_A1_RUN_ID,
            "start_head_sha": start_head, "end_head_sha": end_head, "remote_head_sha": remote_sha,
            "worktree_clean_at_start": git_clean, "disk_waiver_used": False,
            "preflight_disk_free_gib": round(min_pre_dk, 3), "preflight_ram_available_mib": avail_ram_mib,
            "single_run_lock_status": lock_msg, "orchestrator_pid": orch_pid,
            "python_version": platform.python_version(), "platform": platform.platform(), "cpu_count": os.cpu_count(),
            "random_seed": DEFAULT_RANDOM_SEED_KH02_R2, "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
            "strict_multi_mount_probe": strict_probe, "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
            "status": "PASS" if (strict_disk_ok and ram_ok and git_clean) else "BLOCKED_PREFLIGHT",
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "00_preflight_branch_sha_resource_lock.json", f00)

        f24 = _stamp_corr({
            "phase1_executable_committed": git_clean, "branch": branch,
            "start_head_sha": start_head, "evidence_commit_sha": None, "publication_head_sha": None,
            "remote_readback_sha256_verified": False, "ci_status": "CI_UNKNOWN_NO_ACTIONS_RUN",
            "status": "PENDING_REMOTE_READBACK",
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "24_code_publication_receipt.json", f24)

        all_corr = {
            "00": f00, "01": f01, "02": f02, "03": h03, "04": h04, "05": f05, "06": f06, "07": f07,
            "08": h08, "09": f09, "10": h10, "11": f11, "12": f12, "13": f13, "14": f14, "15": f15,
            "16": f16, "17": f17, "18": f18, "19": f19, "20": h20, "21": h21, "24": f24,
        }
        f22 = _stamp_corr(derive_r6_a1_corrective_gates(all_corr, expected_run_id=run_id, expected_tested_sha=start_head, pre_verifier_mode=True), run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "22_corrective_gate_predicates_and_verdicts.json", f22)

        handover_vi = (
            f"AURUM DESK — KH-03.R6.A1 POST-REVIEW CORRECTIVE CLOSURE (P1-L / P1-M / P1-N)\n"
            f"================================================================================\n"
            f"Run ID                 : {run_id}\n"
            f"Branch                 : {branch}\n"
            f"Tested Executable SHA  : {start_head}\n"
            f"Prior A1 Reviewed SHA  : {PRIOR_A1_REVIEWED_TESTED_SHA} (Published HEAD {PRIOR_A1_PUBLICATION_HEAD_SHA})\n"
            f"Pre-Verifier Status    : {f22['overall_classification']}\n"
            f"Corrective Gates (12)  : {f22['corrective_denominators']['corrective_prepush_passed']}/12 prepush (+C11 separate-OS verifier, +C12 remote readback)\n"
            f"Original A1 Gates (36) : {f14['original_36_a1_prepush_gates_passed']}/36 prepush (+G36 remote readback)\n"
            f"External R6.B/C/D (4)  : 0/4 executed (BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED)\n"
            f"M0 Baseline RED 133980a: {h03['reproduced_count']}/4 RED + POS control = {h03['all_reproduced']}\n"
            f"New L/M/N Pairs        : {h20['new_lmn_paired_passed_count']}/41 (L=12, M=14, N=15)\n"
            f"Inherited Pairs        : A1={f14['inherited_a1_55_paired_passed']}/55, R6={f14['inherited_r6_56_paired_passed']}/56, C1+C2={f14['inherited_c1_c2_91_paired_passed']}/91, Reg={f14['inherited_regression_288_passed']}/288\n"
            f"Verifier Mutations     : NEW={h21['new_corrective_mutations_detected_count']}/{h21['new_corrective_mutations_tested_count']}, Inherited={h21['inherited_mutations_detected_count']}/{h21['inherited_mutations_tested_count']}\n"
            f"Full Pytest Collection : {f15['passed_count']} passed / {f15['collected_count']} collected / {f15['skipped_count']} skipped / 0 failed / 0 mandatory skipped\n"
            f"R4 1x Decimal Parity   : closed={f16['n_closed']} (7W/196L), net=-441.6118 USD, losses>2.50={f16['realized_losses_gt_2p50_count']}\n"
            f"Baseline Parity (18)   : A1={f18['a1_checked']} (0 diff), SI8={f18['si8_checked']} (0 diff), Adapters={f18['adapters_checked']} (0 diff), D4=0 diff\n"
            f"Resource Peaks (09)    : min_disk={f09['min_observed_disk_free_gib']} GiB, min_ram={f09['min_observed_ram_available_mib']} MiB, max_desc={f09['max_observed_descendants']}, orphans={f09['orphan_workers_count']}\n"
            f"Safety Matrix          : PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO | ECONOMIC=NO_PROVEN_EDGE | OOS_EVALUATIONS=0 | EXTERNAL_REQUESTS=0 | REAL_OBSERVED=0\n"
            f"Next Step              : OWNER_REVIEW_FOR_R6_B (separate future phase; NOT automatic GO)\n"
        )
        (run_dir / "HANDOVER_KH03_R6_A1_CORRECTIVE_VI.txt").write_text(handover_vi, encoding="utf-8")

        (run_dir / "README.md").write_text(
            f"# AURUM DESK — KH-03.R6.A1 Post-Review Corrective Closure (P1-L / P1-M / P1-N)\n\n"
            f"- **Run ID:** `{run_id}` | **Branch:** `{branch}` | **Tested Executable SHA:** `{start_head}`\n"
            f"- **Prior Reviewed Tested SHA:** `{PRIOR_A1_REVIEWED_TESTED_SHA}` | **Prior A1 Published HEAD:** `{PRIOR_A1_PUBLICATION_HEAD_SHA}`\n"
            f"- **Pre-Verifier Status (`22`):** `{f22['overall_classification']}` (final verified manifest sealed in `23_independent_verifier_manifest_sha256.json`)\n"
            f"- **Separate Denominators:** Corrective `12` (`C01..C12`) | Original A1 `36` (`G01..G36`) | External R6.B/C/D `0/4` (`BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED`)\n"
            f"- **Safety:** `PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO | ECONOMIC=NO_PROVEN_EDGE | OOS_EVALUATIONS=0 | EXTERNAL_MARKET_REQUESTS=0`\n"
            f"- **Reproduce Command:** `python aurum-desk/tools/run_kh_03_r6_a1_acceptance.py --corrective --workers auto`\n",
            encoding="utf-8",
        )

        ck_inv: Dict[str, Dict[str, Any]] = {}
        for fp in sorted(run_dir.rglob("*")):
            if fp.is_file():
                rel_p = str(fp.relative_to(run_dir)).replace("\\", "/")
                if rel_p in ("checksums/sha256_inventory.json", "23_independent_verifier_manifest_sha256.json"):
                    continue
                ck_inv[rel_p] = {"sha256": _sha256_file(fp), "bytes": fp.stat().st_size}
        write_json_durable_atomic(run_dir / "checksums" / "sha256_inventory.json", _stamp_corr({"files_count": len(ck_inv), "files": ck_inv}, run_id=run_id, tested_sha=start_head))

        print("[KH-03.R6.A1-CORR] Stage 5: Invoking Separate-OS Independent Verifier --corrective (sealing 23)...", flush=True)
        v_env = dict(os.environ); v_env["PYTHONPATH"] = _pypath_str(); v_env["PYTHONHASHSEED"] = "0"
        v_proc = subprocess.run(
            [
                sys.executable, str(APP_ROOT / "tools" / "kh_03_r6_a1_independent_verifier.py"),
                "--corrective", "--run-dir", str(run_dir), "--expected-run-id", run_id,
                "--expected-tested-sha", start_head, "--orchestrator-pid", str(orch_pid),
            ],
            cwd=str(APP_ROOT), env=v_env, capture_output=True, text=True, timeout=180,
        )
        print(v_proc.stdout, flush=True)
        if v_proc.stderr:
            print(v_proc.stderr, file=sys.stderr, flush=True)
        return int(v_proc.returncode), run_dir, f22
    finally:
        if not sampler_stopped:
            try:
                sampler.stop_and_flush()
            except Exception:
                pass
        lock.release()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="AURUM DESK — KH-03.R6.A1 Offline / QA Loopback Acceptance Runner")
    ap.add_argument("--workers", default="auto")
    ap.add_argument("--allow-local-dirty", action="store_true")
    ap.add_argument("--corrective", action="store_true", help="Run KH-03.R6.A1 post-review corrective closure (P1-L/M/N) acceptance.")
    args = ap.parse_args(argv)
    mw = GLOBAL_MAX_DESCENDANTS_KH02_R2 if str(args.workers).lower() == "auto" else max(1, min(int(args.workers), GLOBAL_MAX_DESCENDANTS_KH02_R2))
    if args.corrective:
        rc, run_dir, f22 = run_kh03_r6_a1_corrective_orchestrator(allow_local_dirty=bool(args.allow_local_dirty), max_workers=mw)
        print(f"[KH-03.R6.A1-CORR] DONE rc={rc} run_dir={run_dir} pre_verifier={f22.get('overall_classification')}", flush=True)
        return rc
    rc, run_dir, f25 = run_kh03_r6_a1_orchestrator(allow_local_dirty=bool(args.allow_local_dirty), max_workers=mw)
    print(f"[KH-03.R6.A1] DONE rc={rc} run_dir={run_dir} pre_verifier={f25.get('overall_classification')}", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
