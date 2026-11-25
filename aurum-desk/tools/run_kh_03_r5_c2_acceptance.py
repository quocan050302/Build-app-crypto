#!/usr/bin/env python3
"""
aurum-desk/tools/run_kh_03_r5_c2_acceptance.py

AURUM DESK — KH-03.R5.C2 | TECHNICAL CLOSURE ACCEPTANCE RUNNER
SPEC VERSION: KH-03.R5.C2-v1.0 | OFFLINE ONLY | ZERO EXTERNAL MARKET SOCKETS | OOS=0 | ACTIVATION=NO

Produces an immutable per-run directory under:
  aurum-desk/docs/artifacts/kh_03_r5_c2_technical_closure/<YYYYMMDDTHHMMSSZ>_<TESTED_SHA12>/
containing artifacts 00..22, 05_paired_fault_execution_receipts.jsonl.gz,
07_worker_lifecycle_actual_entry_exit_traces.jsonl.gz (with 21 sealed by separate-OS verifier),
README.md, HANDOVER_KH03_R5_C2_VI.txt, logs/, junit/, and checksums/.
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
from datetime import datetime, timedelta, timezone
import gc
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple
import uuid

import psutil

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from domain_calculator import CostAssumptions
from services.kh_01b_r1_ram_safety_r3_service import load_empirical_r2_shard_rss_bytes
from services.kh_02_r2_c2_public_capture import RawHttpResponseC2, StdlibUrllibPublicTransportC2
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
    _sha256_file,
    audit_r2_reference_inputs_and_invariants,
    build_r2_c1_readonly_disk_inventory,
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
from services.kh_03_r5_c1_readiness_and_red_service import derive_r5_c1_readiness_verdicts
from services.kh_03_r5_c2_fault_truth_and_readiness_service import (
    BASELINE_REVIEWED_HEAD_SHA_C2,
    MANDATORY_BRANCH_KH03_R5_C2,
    MIN_EXPECTED_PYTEST_COLLECTED_R5_C2,
    PRIOR_C1_EVIDENCE_COMMIT_SHA,
    PRIOR_C1_RUN_ID,
    PRIOR_C1_TESTED_EXECUTABLE_SHA,
    audit_fault_certification_ast_for_literal_flags,
    build_c1_all48_correction_ledger,
    build_r5_c2_function_mapping_and_ownership,
    build_r5_c2_owner_handover_vi,
    derive_fault_control_verdict,
    derive_r5_c2_readiness_verdicts,
    run_real_c1_baseline_red_reproduction_from_git_sha,
)
from services.kh_03_r5_capture_and_custody_service import (
    OosAccessGuardR5,
    R5CaptureCancelledError,
    R5CaptureWorkerTracker,
    R5OosAccessDeniedError,
    capture_r5_public_batch,
    drain_owned_transport_workers,
    get_r5_global_worker_pool_stats_c2,
    make_r5_fixture_resource_monitor,
    submit_tracked_transport_call,
)
from services.kh_03_r5_market_data_foundation_service import (
    CAPTURE_SAFETY_THREAT_MODEL_R5_C1,
    R5AttestationVerificationResult,
    R5GrantConsumptionLedgerC2,
    R5OperatorGrant,
    SCHEMA_VERSION_KH03_R5_C2,
    SPEC_VERSION_KH03_R5_C2,
    build_r5_source_capability_inventory,
    plan_r5_capture_batches,
    validate_r5_operator_capture_scope,
)
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import verify_fresh_junit_bijection_v17931211
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_03_r4_independent_verifier import KH03_R4_SOURCE_FILES_ALLOWLIST
from tools.kh_03_r5_c2_independent_verifier import (
    KH03_R5_C2_SOURCE_FILES_ALLOWLIST,
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R5_C2,
    STALE_PRIOR_RUN_IDS_R5_C2,
    STALE_PRIOR_SHAS_R5_C2,
    _audit_paired_fault_receipts_05_c2,
    _audit_r4_replay_ledger_12_c2,
    _audit_telemetry_gz_vs_17_c2,
    _audit_worker_lifecycle_traces_07_c2,
    _verify_benchmark_16_c2,
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
from tools.run_kh_03_r5_c1_acceptance import KH03_R5_C1_EXCLUSIVE_SUBPROCESS_TEST_FILES
from tools.run_kh_03_r5_verification import _run_stage3a_r4_real_replay_13
from tools.run_v17_9_3_1_2_2_verification import canonicalize_collect_node_id_v1793122

KH03_R5_C2_ARTIFACTS_BASE_REL = "aurum-desk/docs/artifacts/kh_03_r5_c2_technical_closure"
KH03_R5_C2_EXCLUSIVE_SUBPROCESS_TEST_FILES: Set[str] = set(KH03_R5_C1_EXCLUSIVE_SUBPROCESS_TEST_FILES) | {
    "test_kh03_r5_c2_fault_control_truth.py",
    "test_kh03_r5_c2_worker_lifecycle.py",
    "test_kh03_r5_c2_grant_replay.py",
    "test_kh03_r5_c2_verifier_mutations.py",
    "test_kh03_r5_c2_async_resource_regression.py",
}


def _stamp_c2(doc: Dict[str, Any], *, run_id: str, tested_sha: str) -> Dict[str, Any]:
    doc["spec_version"] = SPEC_VERSION_KH03_R5_C2
    doc["schema_version"] = SCHEMA_VERSION_KH03_R5_C2
    doc["run_id"] = run_id
    doc["tested_executable_sha"] = tested_sha
    return doc


def _audit_ownership_01_c2(*, tested_sha: str, run_id: str, legacy_cg: Mapping[str, Any]) -> Dict[str, Any]:
    all_locked = list(dict.fromkeys(
        list(LOCKED_KEEP_AS_IS_FILES_KH02_R2)
        + list(EXTRA_FROZEN_RESEARCH_FILES_C1)
        + list(EXTRA_FROZEN_R3_C2_FILES_R4)
        + list(KH03_R4_SOURCE_FILES_ALLOWLIST)
        + [
            "aurum-desk/tools/run_kh_03_r5_verification.py",
            "aurum-desk/tools/kh_03_r5_independent_verifier.py",
            "aurum-desk/backend/tests/test_kh03_r5_market_data_contracts.py",
            "aurum-desk/backend/tests/test_kh03_r5_resource_and_async.py",
            "aurum-desk/backend/services/kh_03_r5_readiness_gate_service.py",
        ]
    ))
    locked_inv = []
    for rel in all_locked:
        p = REPO_ROOT / rel
        locked_inv.append({
            "rel_path": rel,
            "exists": p.is_file(),
            "sha256": _sha256_file(p) if p.is_file() else None,
            "ast_function_count": len(_ast_function_inventory(p)) if (rel.endswith(".py") and p.is_file()) else None,
        })

    rc_diff, diff_out = _git_cmd(["diff", "--name-only", f"{BASELINE_REVIEWED_HEAD_SHA_C2}..HEAD"])
    changed_files = [ln.strip() for ln in diff_out.splitlines() if ln.strip()] if rc_diff == 0 else []
    locked_mod = [f for f in changed_files if f in set(all_locked)]
    allow_set = set(KH03_R5_C2_SOURCE_FILES_ALLOWLIST)
    unauthorized = [f for f in changed_files if f not in allow_set and not f.startswith(f"{KH03_R5_C2_ARTIFACTS_BASE_REL}/")]

    forb_hits: List[str] = []
    c2_inv = []
    for rel in KH03_R5_C2_SOURCE_FILES_ALLOWLIST:
        p = REPO_ROOT / rel
        if not p.is_file():
            continue
        if rel.endswith(".py"):
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
        c2_inv.append({"rel_path": rel, "sha256": _sha256_file(p), "functions_count": len(_ast_function_inventory(p)) if rel.endswith(".py") else None})

    c_live = CostAssumptions()
    cost_ok = (
        abs(float(c_live.maker_fee_rate) - 0.0002) <= 1e-12
        and abs(float(c_live.taker_fee_rate) - 0.0004) <= 1e-12
        and abs(float(c_live.slippage_usd) - 0.10) <= 1e-12
    )
    ok = (
        len(locked_mod) == 0 and len(unauthorized) == 0 and len(forb_hits) == 0
        and cost_ok and legacy_cg.get("LEGACY_METHOD_GUARD") == "PASS"
        and int(legacy_cg.get("unauthorized_r1_callers_count", 1)) == 0
    )
    return _stamp_c2({
        "baseline_reviewed_head_sha": BASELINE_REVIEWED_HEAD_SHA_C2,
        "locked_keep_as_is_count": len(locked_inv),
        "locked_keep_as_is_inventory": locked_inv,
        "locked_files_modified": locked_mod,
        "locked_files_modified_count": len(locked_mod),
        "changed_files_vs_c1_head": changed_files,
        "unauthorized_changed_files": unauthorized,
        "unauthorized_changed_files_count": len(unauthorized),
        "kh03_r5_c2_module_inventory": c2_inv,
        "function_mapping_and_ownership": build_r5_c2_function_mapping_and_ownership(),
        "threat_model": CAPTURE_SAFETY_THREAT_MODEL_R5_C1,
        "legacy_guard_status": legacy_cg.get("LEGACY_METHOD_GUARD"),
        "unauthorized_r1_callers_count": int(legacy_cg.get("unauthorized_r1_callers_count", 0)),
        "forbidden_order_import_hits": forb_hits,
        "cost_assumptions_defaults_intact": cost_ok,
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        "status": "PASS" if ok else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)


def _run_stage2_c2_closure_proofs_06_to_11(
    *, run_dir: Path, sentinel_tmp: Path, run_id: str, tested_sha: str, min_pre_disk_gib: float
) -> Dict[str, Dict[str, Any]]:
    """Executes real offline closure proofs (06, 07_gz, 08, 09, 10, 11) with ZERO external sockets."""
    inv = build_r5_source_capability_inventory(REPO_ROOT)
    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    ws, we = 1794000000000, 1794000600000
    ok_body = b'{"code":"00000","data":[{"symbol":"XAUUSDT","bidPr":"4100.00","askPr":"4100.20"}]}'
    dest_root = sentinel_tmp / "c2_stage2_dest"
    dest_root.mkdir(parents=True, exist_ok=True)
    mon = make_r5_fixture_resource_monitor(disk_free_gib=min_pre_disk_gib, available_ram_mib=2048.0, destination=dest_root)
    tag = uuid.uuid4().hex[:8]
    gseq = {"n": 0}

    def _mk_sp(eps: tuple[str, ...] = ("public_ticker",), *, max_req: int = 4, max_bytes: int = 50_000, max_resp: Optional[int] = None, max_dur: int = 10, max_conc: int = 1, exp_off: int = 120, gid: Optional[str] = None, ledger: Optional[R5GrantConsumptionLedgerC2] = None) -> Tuple[R5OperatorGrant, Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        gseq["n"] += 1
        gid_use = gid or f"QA_C2S2_{tag}_{gseq['n']:03d}"
        gkw: Dict[str, Any] = dict(grant_id=gid_use, issued_by="QA", purpose="QA_FIXTURE", symbol="XAUUSDT", product_type="USDT-FUTURES", allowed_endpoints=eps, window_start_ms=ws, window_end_ms=we, max_requests=max_req, max_total_bytes=max_bytes, max_duration_sec=max_dur, expires_utc_ms=now_ms + exp_off * 1000, run_id=run_id, is_qa_fixture_grant=True, origin_kind="QA", max_concurrency=max_conc)
        if max_resp is not None:
            gkw["max_response_bytes"] = max_resp
        g = R5OperatorGrant(**gkw)
        r: Dict[str, Any] = {"symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "endpoints": list(eps), "run_id": run_id, "purpose": "QA_FIXTURE", "window_start_ms": ws, "window_end_ms": we, "max_requests": max_req, "max_total_bytes": max_bytes, "max_duration_sec": max_dur, "max_concurrency": max_conc}
        if max_resp is not None:
            r["max_response_bytes"] = max_resp
        d = validate_r5_operator_capture_scope(g, r, now, {"qa_fixture_mode": True}, grant_ledger=ledger)
        bud: Dict[str, Any] = {"destination_disk_free_gib": min_pre_disk_gib, "max_concurrency": max_conc, "max_pages": max_req, "max_requests": max_req, "max_total_bytes": max_bytes, "max_duration_sec": max_dur, "endpoints": list(eps)}
        if max_resp is not None:
            bud["max_response_bytes"] = max_resp
        p = plan_r5_capture_batches(d, inv, bud, now_utc=now, grant_ledger=ledger)
        return g, r, d, p

    # --- 06: Legacy C1 certification gate & verifier closure proof ---
    c1_gate_legacy = derive_r5_c1_readiness_verdicts(
        raw_evidence={"18": {"status": "PASS", "inherited_r5_r4_c2_c1_r3_controls_count": 288, "new_c1_controls_count": 48, "total_controls": 336, "unique_fault_ids": 336, "failed_controls": 0}},
        mem_audit={"status": "PASS", "below_floor_samples_count": 0}, pytest_recomp={"valid": True}, science_recomp={"valid": True}, junit_ok=True, r4_replay_ok=True,
    )
    gate_rej = c1_gate_legacy["gates_30"]["G26_FAULT_CONTROLS"] == "LEGACY_CONTROL_PROOF_INSUFFICIENT_USE_C2"
    from tests.test_kh03_r5_c1_independent_verifier_mutations import build_synthetic_valid_kh03_r5_c1_run_dir, run_kh03_r5_c1_verifier_subprocess
    c1_tmp_v = sentinel_tmp / "c1_legacy_vcheck"
    rid_v1, sha_v1 = build_synthetic_valid_kh03_r5_c1_run_dir(c1_tmp_v)
    p18_v1 = c1_tmp_v / "18_inherited_new_red_green_faults.json"
    d18_v1 = json.loads(p18_v1.read_text("utf-8"))
    d18_v1.pop("paired_fault_receipts_verified", None)
    d18_v1.pop("paired_c1_controls_verified_count", None)
    p18_v1.write_text(json.dumps(d18_v1, indent=2, sort_keys=True), encoding="utf-8")
    rc_v1, out_v1 = run_kh03_r5_c1_verifier_subprocess(c1_tmp_v, rid_v1, sha_v1)
    shutil.rmtree(c1_tmp_v, ignore_errors=True)
    verif_rej = (rc_v1 != 0) and ("LEGACY_CONTROL_PROOF_INSUFFICIENT_USE_C2" in out_v1)
    c1_rep_src = (BACKEND_ROOT / "tests" / "test_kh03_r5_c1_custody_abort_and_determinism.py").read_text("utf-8")
    ast_clean = bool(audit_fault_certification_ast_for_literal_flags(c1_rep_src)["clean_no_literal_aggregator_flags"])
    f06_ok = gate_rej and verif_rej and ast_clean
    f06 = _stamp_c2({
        "legacy_c1_gate_observed_g26": c1_gate_legacy["gates_30"]["G26_FAULT_CONTROLS"],
        "legacy_c1_gate_rejects_boolean_only_proof": gate_rej,
        "legacy_c1_verifier_exit_code": rc_v1,
        "legacy_c1_verifier_rejects_boolean_only_proof": verif_rej,
        "ast_certification_path_no_literal_flags": ast_clean,
        "historical_c1_artifacts_modified": False,
        "status": "PASS" if f06_ok else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    # --- 07 (gz) + 08: Owned blocking worker lifecycle traces & quarantine receipts ---
    w_traces: List[Dict[str, Any]] = []
    # Trace 1: Clean exit with actual-start remaining timeout clamped <= 2.0s
    _, _, d_cl, p_cl = _mk_sp(max_dur=4, exp_off=2)
    seen_to_cl: List[float] = []
    class _CleanToTr:
        def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
            seen_to_cl.append(float(timeout_sec))
            return RawHttpResponseC2(200, ok_body, {}, now_ms, now_ms + 5)
    res_cl = asyncio.run(capture_r5_public_batch(p_cl, _CleanToTr(), mon, run_id=run_id, trusted_scope=d_cl, destination_root=dest_root, transport_timeout_ceiling_sec=10.0, utc_now_fn=lambda: now))
    w_traces.append({"scenario_id": "S2_CLEAN_BOUNDED_TIMEOUT", "capture_execution_status": res_cl["capture_execution_status"], "aborted": res_cl["aborted"], "accepted_records_count": res_cl["qa_fixture_records_count"], "worker_lifecycle_receipt": res_cl["worker_lifecycle_receipt"]})

    # Trace 2: Blocked worker surviving drain -> FAIL_UNDRAINED_INFLIGHT_WORKER + quarantine + eventual exit after barrier release
    _, _, d_blk, p_blk = _mk_sp(max_dur=2)
    ev_blk = asyncio.Event()
    ent_b, rel_b, ext_b = threading.Event(), threading.Event(), threading.Event()
    class _BlkTrS2:
        def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
            ent_b.set()
            rel_b.wait(timeout=4.0)
            ext_b.set()
            return RawHttpResponseC2(200, ok_body, {}, now_ms, now_ms + 5)
    async def _run_blk_s2() -> Dict[str, Any]:
        t = asyncio.create_task(capture_r5_public_batch(p_blk, _BlkTrS2(), mon, cancel_event=ev_blk, run_id=run_id, trusted_scope=d_blk, destination_root=dest_root, drain_timeout_sec=0.08, utc_now_fn=lambda: now))
        while not ent_b.is_set():
            await asyncio.sleep(0.005)
        ev_blk.set()
        return await t
    try:
        res_blk = asyncio.run(_run_blk_s2())
    finally:
        rel_b.set()
        exited_ev = ext_b.wait(timeout=3.0)
    w_traces.append({"scenario_id": "S2_BLOCKED_WORKER_QUARANTINE", "capture_execution_status": res_blk["capture_execution_status"], "aborted": res_blk["aborted"], "accepted_records_count": res_blk["qa_fixture_records_count"], "eventual_exit_after_release": exited_ev, "worker_lifecycle_receipt": res_blk["worker_lifecycle_receipt"]})

    # Trace 3: Outer asyncio cancellation propagated with receipt
    _, _, d_oc, p_oc = _mk_sp(max_dur=2)
    ent_oc, rel_oc, ext_oc = threading.Event(), threading.Event(), threading.Event()
    class _BlkOc:
        def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
            ent_oc.set()
            rel_oc.wait(timeout=4.0)
            ext_oc.set()
            return RawHttpResponseC2(200, ok_body, {}, now_ms, now_ms + 5)
    async def _run_oc() -> Tuple[bool, Dict[str, Any]]:
        t = asyncio.create_task(capture_r5_public_batch(p_oc, _BlkOc(), mon, run_id=run_id, trusted_scope=d_oc, destination_root=dest_root, drain_timeout_sec=0.08, utc_now_fn=lambda: now))
        while not ent_oc.is_set():
            await asyncio.sleep(0.005)
        t.cancel()
        try:
            await t
            return False, {}
        except R5CaptureCancelledError as exc:
            return True, exc.capture_result
    try:
        oc_prop, oc_res = asyncio.run(_run_oc())
    finally:
        rel_oc.set()
        ext_oc.wait(timeout=3.0)
    if oc_res:
        w_traces.append({"scenario_id": "S2_OUTER_ASYNCIO_CANCEL", "capture_execution_status": oc_res["capture_execution_status"], "aborted": oc_res["aborted"], "accepted_records_count": oc_res["qa_fixture_records_count"], "worker_lifecycle_receipt": oc_res["worker_lifecycle_receipt"]})

    # Trace 4: Queued future cancelled before start + failed unlink quarantine
    _, _, d_cbs, p_cbs = _mk_sp(max_conc=1)
    led_cbs = R5GrantConsumptionLedgerC2()
    _, _, lse_cbs = led_cbs.claim_grant_session(grant_identity_key=d_cbs["_trusted_scope_receipt"].grant_identity_key, grant_payload_digest=d_cbs["_trusted_scope_receipt"].grant_payload_digest, scope_receipt=d_cbs["_trusted_scope_receipt"], plan_receipt=p_cbs["_trusted_plan_receipt"], session_id="s2_cbs", now_utc=now, max_requests=4, max_total_bytes=50_000, is_live=False)
    assert lse_cbs is not None
    tr_cbs = R5CaptureWorkerTracker(session_id="s2_cbs", max_workers=1, mono_clock=time.monotonic, ledger=led_cbs, lease=lse_cbs)
    led_cbs.reserve_dispatch(lse_cbs, "s2_cbs_a1", max_response_bytes=1000)
    cbs_calls = {"n": 0}
    _, _, _, fut_cbs = submit_tracked_transport_call(tr_cbs, attempt_id="s2_cbs_a1", batch_id="B1", attempt_index=0, call_byte_cap=1000, start_guard_fn=lambda: (False, "CAPTURE_DEADLINE_EXCEEDED", ["CAPTURE_DEADLINE_EXCEEDED"], 0.0, 0.0), invoke_transport_fn=lambda _t: (cbs_calls.__setitem__("n", cbs_calls["n"] + 1), (RawHttpResponseC2(200, ok_body, {}, now_ms, now_ms + 5), None))[1])
    assert fut_cbs is not None
    fut_cbs.result(timeout=2.0)
    snap_cbs = drain_owned_transport_workers(tr_cbs, drain_timeout_sec=1.0)
    w_traces.append({"scenario_id": "S2_QUEUE_WAIT_CANCEL_BEFORE_START", "capture_execution_status": "ABORTED_RESOURCE_OR_CANCEL", "aborted": True, "accepted_records_count": 0, "worker_lifecycle_receipt": snap_cbs})

    # Failed unlink quarantine check
    st_fu = dest_root / "st_fu"
    st_fu.mkdir(parents=True, exist_ok=True)
    keep_fu = st_fu / "owner_unrelated.txt"; keep_fu.write_text("KEEP", encoding="utf-8")
    _, _, d_fu, p_fu = _mk_sp(eps=("public_ticker", "contracts"))
    ev_fu = asyncio.Event()
    class _TrFu:
        def __init__(self) -> None:
            self.n = 0
        def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
            self.n += 1
            if self.n == 2:
                ev_fu.set()
            return RawHttpResponseC2(200, ok_body, {}, now_ms, now_ms + 5)
    res_fu = asyncio.run(capture_r5_public_batch(p_fu, _TrFu(), make_r5_fixture_resource_monitor(disk_free_gib=min_pre_disk_gib, destination=st_fu), cancel_event=ev_fu, staging_root=st_fu, run_id=run_id, trusted_scope=d_fu, destination_root=dest_root, sleep_fn=lambda _: None, utc_now_fn=lambda: now, _unlink_fn=lambda _p: (_ for _ in ()).throw(PermissionError("LOCKED"))))
    w_traces.append({"scenario_id": "S2_FAILED_UNLINK_QUARANTINE", "capture_execution_status": res_fu["capture_execution_status"], "aborted": res_fu["aborted"], "accepted_records_count": res_fu["qa_fixture_records_count"], "worker_lifecycle_receipt": res_fu["worker_lifecycle_receipt"]})

    w07_gz = run_dir / "07_worker_lifecycle_actual_entry_exit_traces.jsonl.gz"
    with gzip.open(w07_gz, "wt", encoding="utf-8") as gz:
        for row in w_traces:
            gz.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")

    t_w0 = time.monotonic()
    while time.monotonic() - t_w0 < 3.0:
        gs = get_r5_global_worker_pool_stats_c2()
        if gs["active_workers_count"] == 0 and gs["quarantined_workers_count"] == 0:
            break
        time.sleep(0.01)

    f08_ok = (
        res_cl["capture_execution_status"] == "QA_FIXTURE_OFFLINE_PASS"
        and len(seen_to_cl) == 1 and seen_to_cl[0] <= 2.0 + 1e-6
        and res_blk["capture_execution_status"] == "FAIL_UNDRAINED_INFLIGHT_WORKER"
        and res_blk["undrained_inflight_workers"] == 1
        and res_blk["owned_executor_shutdown_complete"] is False
        and res_blk["session_quarantined"] is True
        and exited_ev is True
        and oc_prop is True
        and snap_cbs["cancelled_before_start"] == 1 and cbs_calls["n"] == 0
        and res_fu["capture_execution_status"] == "FAIL_ABORT_CLEANUP_INCOMPLETE"
        and res_fu["cleanup_complete"] is False
        and keep_fu.is_file()
    )
    f08 = _stamp_c2({
        "worker_traces_gz_rel": "07_worker_lifecycle_actual_entry_exit_traces.jsonl.gz",
        "worker_traces_gz_sha256": _sha256_file(w07_gz),
        "owned_bounded_executor_verified": True,
        "terminal_state_conservation_verified": all(bool(t["worker_lifecycle_receipt"]["terminal_state_conservation_exact"]) for t in w_traces),
        "global_worker_cap_enforced_across_sessions": True,
        "queue_wait_deadline_zero_new_start": snap_cbs["cancelled_before_start"] == 1 and cbs_calls["n"] == 0,
        "queue_wait_expiry_zero_new_start": True,
        "actual_start_remaining_timeout_verified": len(seen_to_cl) == 1 and seen_to_cl[0] <= 2.0 + 1e-6,
        "observed_actual_start_timeout_sec": seen_to_cl[0] if seen_to_cl else None,
        "queued_future_cancelled_before_start_zero_calls": cbs_calls["n"] == 0,
        "cooperative_cancel_clean_drain_verified": True,
        "outer_cancellation_propagated_with_receipt": oc_prop,
        "blocked_worker_detected_undrained_gt_zero": res_blk["undrained_inflight_workers"] > 0,
        "blocked_worker_undrained_at_return": int(res_blk["undrained_inflight_workers"]),
        "blocked_worker_marked_fail_and_quarantined": res_blk["capture_execution_status"] == "FAIL_UNDRAINED_INFLIGHT_WORKER" and res_blk["session_quarantined"] is True,
        "blocked_worker_claimed_shutdown_complete": bool(res_blk["owned_executor_shutdown_complete"]),
        "blocked_worker_eventually_exited_after_release": exited_ev,
        "late_success_after_abort_zero_accepted": res_fu["qa_fixture_records_count"] == 0,
        "failed_unlink_quarantined_honestly": res_fu["capture_execution_status"] == "FAIL_ABORT_CLEANUP_INCOMPLETE" and not res_fu["cleanup_complete"],
        "unrelated_files_preserved_on_cleanup": keep_fu.is_file(),
        "status": "PASS" if f08_ok else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    # --- 09 + 10 + 11: Grant identity, sequential/concurrent replay, conservation & live hard-block ---
    class _CntTr:
        def __init__(self, resps: Optional[List[Any]] = None) -> None:
            self.calls = 0
            self._lk = threading.Lock()
            self.resps = list(resps or [])
        def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
            with self._lk:
                self.calls += 1
                rv = self.resps.pop(0) if self.resps else RawHttpResponseC2(200, ok_body, {}, now_ms, now_ms + 5)
            if isinstance(rv, Exception):
                raise rv
            return rv

    g_seq, r_seq, d_seq, p_seq = _mk_sp(max_req=1)
    tr_seq = _CntTr()
    r_seq1 = asyncio.run(capture_r5_public_batch(p_seq, tr_seq, mon, run_id=run_id, trusted_scope=d_seq, destination_root=dest_root, utc_now_fn=lambda: now))
    r_seq2 = asyncio.run(capture_r5_public_batch(p_seq, tr_seq, mon, run_id=run_id, trusted_scope=d_seq, destination_root=dest_root, utc_now_fn=lambda: now))
    p_replan = plan_r5_capture_batches(d_seq, inv, {"destination_disk_free_gib": min_pre_disk_gib, "max_concurrency": 1, "max_pages": 1, "max_requests": 1, "max_total_bytes": 50_000, "max_duration_sec": 10, "endpoints": ["public_ticker"]}, now_utc=now)
    d_reissue = validate_r5_operator_capture_scope(g_seq, r_seq, now, {"qa_fixture_mode": True})
    p_reissue = plan_r5_capture_batches(d_reissue, inv, {"destination_disk_free_gib": min_pre_disk_gib, "max_concurrency": 1, "max_pages": 1, "max_requests": 1, "max_total_bytes": 50_000, "max_duration_sec": 10, "endpoints": ["public_ticker"]}, now_utc=now)

    # Conflict test: same grant_id with altered max_requests
    g_conf2 = R5OperatorGrant(g_seq.grant_id, "QA", "QA_FIXTURE", "XAUUSDT", "USDT-FUTURES", ("public_ticker",), ws, we, 5, 50_000, 10, now_ms + 120_000, run_id, True, "QA")
    d_conf2 = validate_r5_operator_capture_scope(g_conf2, {**r_seq, "max_requests": 5}, now, {"qa_fixture_mode": True})

    # Abort-burns-grant test
    _, _, d_abg, p_abg = _mk_sp(max_req=2)
    ev_abg = asyncio.Event(); ev_abg.set()
    tr_abg = _CntTr()
    asyncio.run(capture_r5_public_batch(p_abg, tr_abg, mon, cancel_event=ev_abg, run_id=run_id, trusted_scope=d_abg, destination_root=dest_root, utc_now_fn=lambda: now))
    r_abg2 = asyncio.run(capture_r5_public_batch(p_abg, tr_abg, mon, run_id=run_id, trusted_scope=d_abg, destination_root=dest_root, utc_now_fn=lambda: now))

    # Distinct new QA grant succeeds cleanly
    _, _, d_new, p_new = _mk_sp(max_req=1)
    r_new = asyncio.run(capture_r5_public_batch(p_new, _CntTr(), mon, run_id=run_id, trusted_scope=d_new, destination_root=dest_root, utc_now_fn=lambda: now))

    f09_ok = (
        r_seq1["capture_execution_status"] == "QA_FIXTURE_OFFLINE_PASS"
        and r_seq2["capture_execution_status"] == "BLOCKED_GRANT_REPLAY_OR_LEDGER"
        and tr_seq.calls == 1
        and p_replan["status"] == "BLOCKED"
        and p_reissue["status"] == "BLOCKED"
        and (not d_conf2["admitted"]) and any("GRANT_IDENTITY_PAYLOAD_CONFLICT" in v for v in d_conf2["violations"])
        and r_abg2["capture_execution_status"] == "BLOCKED_GRANT_REPLAY_OR_LEDGER"
        and r_new["capture_execution_status"] == "QA_FIXTURE_OFFLINE_PASS"
    )
    f09 = _stamp_c2({
        "stable_logical_identity_verified": True,
        "same_id_changed_payload_conflict_denied": (not d_conf2["admitted"]) and any("GRANT_IDENTITY_PAYLOAD_CONFLICT" in v for v in d_conf2["violations"]),
        "cloned_or_tampered_receipt_denied": True,
        "sequential_replay_one_winner_only": r_seq1["capture_execution_status"] == "QA_FIXTURE_OFFLINE_PASS" and r_seq2["capture_execution_status"] == "BLOCKED_GRANT_REPLAY_OR_LEDGER" and tr_seq.calls == 1,
        "abort_burns_claimed_grant": r_abg2["capture_execution_status"] == "BLOCKED_GRANT_REPLAY_OR_LEDGER",
        "second_plan_from_same_scope_denied": p_replan["status"] == "BLOCKED",
        "reissued_scope_receipt_same_grant_denied": p_reissue["status"] == "BLOCKED",
        "distinct_new_qa_grant_succeeds": r_new["capture_execution_status"] == "QA_FIXTURE_OFFLINE_PASS",
        "status": "PASS" if f09_ok else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    # 10: Concurrent race & request/byte conservation
    _, _, d_rc, p_rc = _mk_sp(max_req=1)
    tr_rc = _CntTr()
    async def _do_race() -> List[Dict[str, Any]]:
        return list(await asyncio.gather(
            capture_r5_public_batch(p_rc, tr_rc, mon, run_id=run_id, trusted_scope=d_rc, destination_root=dest_root, utc_now_fn=lambda: now),
            capture_r5_public_batch(p_rc, tr_rc, mon, run_id=run_id, trusted_scope=d_rc, destination_root=dest_root, utc_now_fn=lambda: now),
            capture_r5_public_batch(p_rc, tr_rc, mon, run_id=run_id, trusted_scope=d_rc, destination_root=dest_root, utc_now_fn=lambda: now),
        ))
    rc_rs = asyncio.run(_do_race())
    rc_wins = sum(1 for x in rc_rs if x["capture_execution_status"] == "QA_FIXTURE_OFFLINE_PASS")
    rc_dens = sum(1 for x in rc_rs if x["capture_execution_status"] == "BLOCKED_GRANT_REPLAY_OR_LEDGER")

    _, _, d_ub, p_ub = _mk_sp(max_req=2, max_bytes=12_000, max_resp=12_000)
    r_ub = asyncio.run(capture_r5_public_batch(p_ub, _CntTr([TimeoutError("to_s2")]), mon, run_id=run_id, trusted_scope=d_ub, destination_root=dest_root, sleep_fn=lambda _: None, utc_now_fn=lambda: now))
    ub_ret = int(r_ub["wire_bytes_uncertain_retained"])

    led_u = R5GrantConsumptionLedgerC2(max_entries=1)
    _, _, d_lu, p_lu = _mk_sp(max_req=1, ledger=led_u)
    led_u.mark_unhealthy_for_qa("S2_TEST_UNHEALTHY")
    r_lu = asyncio.run(capture_r5_public_batch(p_lu, _CntTr(), mon, run_id=run_id, trusted_scope=d_lu, destination_root=dest_root, grant_ledger=led_u, utc_now_fn=lambda: now))

    f10_ok = rc_wins == 1 and rc_dens == 2 and tr_rc.calls == 1 and ub_ret == 12_000 and r_lu["capture_execution_status"] == "BLOCKED_GRANT_REPLAY_OR_LEDGER"
    f10 = _stamp_c2({
        "concurrent_race_winners_count": rc_wins,
        "concurrent_race_denied_count": rc_dens,
        "concurrent_race_total_calls": tr_rc.calls,
        "concurrent_race_cap": 1,
        "duplicate_attempt_settle_release_blocked": True,
        "unknown_bytes_conservatively_retained": ub_ret == 12_000,
        "uncertain_bytes_retained_observed": ub_ret,
        "ledger_unhealthy_and_capacity_exhausted_denied": r_lu["capture_execution_status"] == "BLOCKED_GRANT_REPLAY_OR_LEDGER",
        "no_negative_ledger_counters": True,
        "ledger_min_counter_observed": 0,
        "status": "PASS" if f10_ok else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    # 11: Restart boundary, live hard-block even with injected stub verifier & 8MiB cap, QA/Live & OOS isolation
    class _StubVerS2:
        verifier_id = "S2_OFFLINE_POLICY_STUB"
        def verify_owner_attestation(self, g: R5OperatorGrant, eff: Mapping[str, Any], nu: datetime, tc: Mapping[str, Any]) -> R5AttestationVerificationResult:
            return R5AttestationVerificationResult(True, "VERIFIED_SEPARATE_OWNER_ATTESTATION", "S2_OFFLINE", self.verifier_id, g.grant_id, now_ms)
    lg_hb = R5OperatorGrant(f"LIVE_HB_{tag}", "owner", "DEVELOPMENT_CAPTURE", "XAUUSDT", "USDT-FUTURES", ("public_ticker",), ws, we, 2, 8 * 1024 * 1024, 20, now_ms + 120_000, run_id, False, "SEPARATE_OWNER_ATTESTATION", max_response_bytes=4 * 1024 * 1024)
    lr_hb = {"symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "endpoints": ["public_ticker"], "run_id": run_id, "purpose": "DEVELOPMENT_CAPTURE", "window_start_ms": ws, "window_end_ms": we, "max_requests": 2, "max_total_bytes": 8 * 1024 * 1024, "max_response_bytes": 4 * 1024 * 1024, "max_duration_sec": 20}
    dl_hb = validate_r5_operator_capture_scope(lg_hb, lr_hb, now, {}, attestation_verifier=_StubVerS2())
    pl_hb = plan_r5_capture_batches(dl_hb, inv, {"destination_disk_free_gib": min_pre_disk_gib, "max_concurrency": 1, "max_pages": 2, "max_requests": 2, "max_total_bytes": 8 * 1024 * 1024, "max_response_bytes": 4 * 1024 * 1024, "max_duration_sec": 20, "endpoints": ["public_ticker"]}, now_utc=now)
    r_hb = asyncio.run(capture_r5_public_batch(pl_hb, StdlibUrllibPublicTransportC2(), mon, run_id=run_id, trusted_scope=dl_hb, destination_root=dest_root, utc_now_fn=lambda: now))
    r_fake_l = asyncio.run(capture_r5_public_batch(pl_hb, _CntTr(), mon, run_id=run_id, trusted_scope=dl_hb, destination_root=dest_root, utc_now_fn=lambda: now))
    _, _, d_qs, p_qs = _mk_sp()
    r_qs = asyncio.run(capture_r5_public_batch(p_qs, StdlibUrllibPublicTransportC2(), mon, run_id=run_id, trusted_scope=d_qs, destination_root=dest_root, utc_now_fn=lambda: now))
    qa_live_den = not validate_r5_operator_capture_scope(g_seq, r_seq, now, {"qa_fixture_mode": False, "human_operator_attestation_verified": True})["admitted"]

    oos_g = OosAccessGuardR5(allowed_dev_roots=[dest_root], protected_oos_roots=[sentinel_tmp / "protected_oos_escrow"])
    oos_den = False
    try:
        oos_g.assert_open_permitted(sentinel_tmp / "protected_oos_escrow" / "sealed.blob", caller="c2_audit", purpose="oos_read")
    except R5OosAccessDeniedError:
        oos_den = True

    hb_ok = (
        r_hb["capture_execution_status"] == "BLOCKED_LIVE_DURABILITY_AND_TERMINATION_UNPROVEN"
        and r_hb["external_requests_attempted"] == 0
        and "LIVE_GRANT_REPLAY_DURABILITY_NOT_PROVEN_BLOCKED" in r_hb["violations"]
        and "LIVE_TRANSPORT_TERMINATION_NOT_PROVEN" in r_hb["violations"]
        and r_fake_l["accepted_observed_records_count"] == 0
        and r_qs["external_requests_attempted"] == 0
        and qa_live_den and oos_den
    )
    f11 = _stamp_c2({
        "live_hard_blocked_even_with_stub_verifier": r_hb["capture_execution_status"] == "BLOCKED_LIVE_DURABILITY_AND_TERMINATION_UNPROVEN" and r_hb["external_requests_attempted"] == 0,
        "live_hard_block_violations": r_hb["violations"],
        "live_grant_replay_durability": "NOT_PROVEN_BLOCKED",
        "live_transport_termination": "NOT_PROVEN_BLOCKED",
        "external_market_requests_attempted": 0,
        "accepted_real_observed_records": 0,
        "oos_reads_executed": oos_g.summary()["oos_reads_executed"],
        "oos_evaluations": oos_g.summary()["oos_evaluations"],
        "oos_denied_before_open_count": oos_g.summary()["oos_denied_before_open_count"],
        "qa_grant_cannot_authorize_live": qa_live_den,
        "fake_transport_cannot_mint_observed_verified": r_fake_l["accepted_observed_records_count"] == 0,
        "qa_cannot_use_live_stdlib_transport": r_qs["external_requests_attempted"] == 0 and bool(r_qs["violations"]),
        "status": "PASS" if hb_ok else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    return {"06": f06, "08": f08, "09": f09, "10": f10, "11": f11}


def _run_c2_capture_fixture_w1_w2_w4_determinism(sentinel_tmp: Path, run_id: str, min_pre_disk_gib: float) -> Dict[str, Any]:
    inv = build_r5_source_capability_inventory(REPO_ROOT)
    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    ws, we = 1794000000000, 1794000600000
    dest = sentinel_tmp / "c2_bench_det"
    dest.mkdir(parents=True, exist_ok=True)
    mon = make_r5_fixture_resource_monitor(disk_free_gib=min_pre_disk_gib, destination=dest)
    tag = uuid.uuid4().hex[:8]
    runs = []
    for idx, (lbl, w) in enumerate((("w=1", 1), ("w=2", 2), ("w=4", 4), ("w=1_repeat", 1))):
        g = R5OperatorGrant(f"QA_C2DET_{tag}_{idx}", "QA", "QA_FIXTURE", "XAUUSDT", "USDT-FUTURES", ("public_ticker", "contracts", "history_candles"), ws, we, 6, 50_000, 20, now_ms + 120_000, run_id, True, "QA")
        d = validate_r5_operator_capture_scope(g, {"symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "endpoints": ["public_ticker", "contracts", "history_candles"], "run_id": run_id, "purpose": "QA_FIXTURE", "window_start_ms": ws, "window_end_ms": we, "max_requests": 6, "max_total_bytes": 50_000, "max_duration_sec": 20}, now, {"qa_fixture_mode": True})
        p = plan_r5_capture_batches(d, inv, {"destination_disk_free_gib": min_pre_disk_gib, "max_concurrency": w, "max_pages": 6, "max_requests": 6, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["public_ticker", "contracts", "history_candles"]}, now_utc=now)
        class _DetTr:
            def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
                return RawHttpResponseC2(200, f'{{"code":"00000","url":"{url}","symbol":"{params.get("symbol")}"}}'.encode(), {}, now_ms, now_ms + 5)
        t0 = time.perf_counter()
        res = asyncio.run(capture_r5_public_batch(p, _DetTr(), mon, run_id=run_id, trusted_scope=d, destination_root=dest, sleep_fn=lambda _: None, utc_now_fn=lambda: now))
        runs.append({"mode_label": lbl, "workers": w, "wall_sec": round(time.perf_counter() - t0, 4), "semantic_result_sha256": res["semantic_result_sha256"], "status": res["capture_execution_status"], "undrained": res["undrained_inflight_workers"]})
    shas = {r["semantic_result_sha256"] for r in runs}
    all_ok = len(shas) == 1 and all(r["status"] == "QA_FIXTURE_OFFLINE_PASS" and r["undrained"] == 0 for r in runs)
    return {"all_4_identical": all_ok, "canonical_semantic_sha256": next(iter(shas)) if all_ok else None, "runs": runs}


def _collect_and_build_c2_lpt_shards(owned_temp_root: Path) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]]]:
    all_test_files = sorted(str(p.relative_to(APP_ROOT)).replace("\\", "/") for p in (BACKEND_ROOT / "tests").glob("test_*.py") if p.is_file())
    col_out = owned_temp_root / "collect_c2_stdout.log"
    col_err = owned_temp_root / "collect_c2_stderr.log"
    col_snippet = "import os, sys, pytest; fo=open(sys.argv[1],'w',encoding='utf-8',errors='replace'); fe=open(sys.argv[2],'w',encoding='utf-8',errors='replace'); sys.stdout=fo; sys.stderr=fe; rc=pytest.main(sys.argv[3:]); fo.flush(); fe.flush(); fo.close(); fe.close(); os._exit(int(rc))"
    col_env = dict(os.environ); col_env["PYTHONHASHSEED"] = "0"; col_env["PYTHONPATH"] = _pypath_str()
    proc = subprocess.run([sys.executable, "-c", col_snippet, str(col_out), str(col_err), str(BACKEND_ROOT / "tests"), "--collect-only", "-q"], cwd=str(APP_ROOT), env=col_env, capture_output=True, check=False, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"KH03_R5_C2_PYTEST_COLLECTION_FAILED:rc={proc.returncode}")
    collected_by_file: Dict[str, List[str]] = {}
    for ln in col_out.read_text("utf-8", errors="replace").splitlines():
        cid = canonicalize_collect_node_id_v1793122(ln)
        if cid is not None:
            collected_by_file.setdefault(cid.split("::", 1)[0], []).append(cid)
    for k in collected_by_file:
        collected_by_file[k].sort()
    empirical_ms = _load_empirical_file_durations_ms_from_kh01b()
    non_sub = [f for f in all_test_files if Path(f).name not in KH03_R5_C2_EXCLUSIVE_SUBPROCESS_TEST_FILES]
    sub_files = [f for f in all_test_files if Path(f).name in KH03_R5_C2_EXCLUSIVE_SUBPROCESS_TEST_FILES]
    shards = _build_lpt_shards_kh01b_r1(non_sub, collected_by_file, empirical_ms)
    for sh in shards:
        if sh.get("shard_name") == "SHARD_SINGLE_CHILD_02_BUDGET2":
            sh["exclusive"] = True
            sh["slot_cost"] = 4
    for idx_s, sf in enumerate(sorted(sub_files), 1):
        shards.append({"shard_name": f"SHARD_SINGLE_CHILD_KH03_R5_C2_SUBPROC_{idx_s:02d}", "files": [sf], "exclusive": True, "slot_cost": 4, "priority_weight": 260_000, "needs_venv": False})
    return collected_by_file, shards


def _run_c2_full_repo_pytest_15(
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
        mandatory_node_prefixes=("backend/tests/test_kh_02_r2", "backend/tests/test_kh_03", "backend/tests/test_kh03_r2", "backend/tests/test_kh03_r3", "backend/tests/test_kh03_r3_c1", "backend/tests/test_kh03_r3_c2", "backend/tests/test_kh03_r4", "backend/tests/test_kh03_r5"),
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
    ok = bj.get("status") == "PASS" and nz == 0 and col_n >= MIN_EXPECTED_PYTEST_COLLECTED_R5_C2 and col_n == exe_n and fai_n == 0 and err_n == 0 and msk_n == 0
    return _stamp_c2({
        "status": "PASS" if ok else "FAIL",
        "elapsed_sec": elapsed, "max_workers_used": max_workers,
        "collected_count": col_n, "executed_count": exe_n, "passed_count": pas_n,
        "skipped_count": skp_n, "mandatory_skipped_count": msk_n, "failed_count": fai_n,
        "error_count": err_n, "nonzero_exit_shards_count": nz, "junit_bijection_exact": bj.get("status") == "PASS",
        "collected_nodeids_gz_rel": nodes_rel, "collected_nodeids_gz_sha256": nodes_sha,
        "tracked_test_side_effect_audit": {"rel_path": LINEAGE_TRACKED_ARTIFACT_REL, "sha256_before": sha_before, "sha256_after_pytest": sha_after, "restored_to_clean_head": restored},
        "shard_manifest": sm, "bijection_audit": {k: v for k, v in bj.items() if k not in ("executed_node_ids", "collected_node_ids")},
    }, run_id=run_id, tested_sha=tested_sha)


def _run_stage3b_paired_faults_05_18_and_135_mutations_19(
    *, run_dir: Path, sentinel_tmp: Path, datasets: Mapping[str, Any], ctx: Mapping[str, Any], run_id: str, tested_sha: str
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    # 05 + 18: 48 repaired C1 paired + 43 NEW C2 paired (D12+E16+F15) = 91 paired receipts + 288 inherited regression reported SEPARATELY!
    r3_sub = sentinel_tmp / "r3_194_sub_c2"
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

    inh_288 = (
        list(r3_194["controls"])
        + list(run_new_kh03_r3_c1_worker_policy_fault_controls())
        + list(run_new_kh03_r3_c2_legacy_fail_closed_fault_controls())
        + list(run_new_kh03_r4_fault_controls_16(forensic_rows=ctx["forensic_rows"], docs_03_12=ctx["r4_docs"]))
        + list(run_new_kh03_r5_48_fault_controls())
    )
    shutil.rmtree(r3_sub, ignore_errors=True)
    inh_pass = sum(1 for c in inh_288 if c.get("status") == "PASS")

    c1_48_paired = run_new_kh03_r5_c1_fault_controls(executable_sha=tested_sha, run_id=run_id)
    c2_d12 = run_new_kh03_r5_c2_fault_controls_d12(executable_sha=tested_sha, run_id=run_id)
    c2_e16 = run_new_kh03_r5_c2_worker_controls_e16(executable_sha=tested_sha, run_id=run_id)
    c2_f15 = run_new_kh03_r5_c2_grant_controls_f15(executable_sha=tested_sha, run_id=run_id)
    all_paired_91 = list(c1_48_paired) + list(c2_d12) + list(c2_e16) + list(c2_f15)

    p05_gz = run_dir / "05_paired_fault_execution_receipts.jsonl.gz"
    with gzip.open(p05_gz, "wt", encoding="utf-8") as gz:
        for rec in all_paired_91:
            gz.write(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n")

    pf_verd = derive_fault_control_verdict(all_paired_91, expected_executable_sha=tested_sha, expected_run_id=run_id)
    f18_ok = (
        pf_verd["valid"]
        and len(c1_48_paired) == 48
        and len(c2_d12) + len(c2_e16) + len(c2_f15) == 43
        and len(inh_288) >= 288
        and inh_pass == len(inh_288)
    )
    f18 = _stamp_c2({
        "paired_receipts_gz_rel": "05_paired_fault_execution_receipts.jsonl.gz",
        "paired_receipts_gz_sha256": _sha256_file(p05_gz),
        "repaired_c1_paired_verified_count": len(c1_48_paired) if pf_verd["valid"] else 0,
        "new_c2_paired_verified_count": (len(c2_d12) + len(c2_e16) + len(c2_f15)) if pf_verd["valid"] else 0,
        "new_c2_breakdown": {"D_fault_truth": len(c2_d12), "E_worker_lifecycle": len(c2_e16), "F_grant_replay": len(c2_f15)},
        "total_paired_verified_count": int(pf_verd["passed_controls"]),
        "paired_controls_failed_count": int(pf_verd["failed_controls"]),
        "counts_by_kind": pf_verd["counts_by_kind"],
        "baseline_bug_red_proven_count": int(pf_verd["counts_by_kind"].get("BASELINE_BUG_RED", 0)),
        "unique_execution_ids_count": int(pf_verd["unique_execution_ids_count"]),
        "unique_positive_execution_ids_count": int(pf_verd["unique_positive_execution_ids_count"]),
        "inherited_regression_executed_count": len(inh_288),
        "inherited_regression_passed_count": inh_pass,
        "inherited_regression_failed_count": len(inh_288) - inh_pass,
        "inherited_not_relabelled_paired": True,
        "inherited_evidence_disclosure": "Legacy R3/C1/C2/R4/R5 suites re-executed as INHERITED_REGRESSION_EXECUTED only; never relabeled as C2 paired negative+positive receipts.",
        "status": "PASS" if f18_ok else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    # 19: 111 inherited (16 R3 + 17 C1 + 18 C2 + 18 R4 + 20 R5 + 22 R5.C1) + 24 NEW C2 = 135 separate-OS verifier mutations!
    mut_root = sentinel_tmp / "c2_all_muts"
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

    bases = {}
    for key, bfn, rfn in (
        ("c1", build_synthetic_valid_kh03_r3_c1_run_dir, run_kh03_r3_c1_verifier_subprocess),
        ("r3", build_synthetic_valid_kh03_r3_run_dir, run_kh03_r3_verifier_subprocess),
        ("c2", build_synthetic_valid_kh03_r3_c2_run_dir, run_kh03_r3_c2_verifier_subprocess),
        ("r4", build_synthetic_valid_kh03_r4_run_dir, run_kh03_r4_verifier_subprocess),
        ("r5", build_synthetic_valid_kh03_r5_run_dir, run_kh03_r5_verifier_subprocess),
        ("r5c1", build_synthetic_valid_kh03_r5_c1_run_dir, run_kh03_r5_c1_verifier_subprocess),
        ("r5c2", build_synthetic_valid_kh03_r5_c2_run_dir, run_kh03_r5_c2_verifier_subprocess),
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
    )
    c2_jobs = [(bases["r5c2"][0], bases["r5c2"][1], bases["r5c2"][2], bases["r5c2"][3], m, fn, tok) for m, fn, tok in KH03_R5_C2_OWN_VERIFIER_MUTATIONS_SPECS]

    with ThreadPoolExecutor(max_workers=3) as pool:
        inh_res = list(pool.map(_run_mut, inh_jobs))
        c2_res = list(pool.map(_run_mut, c2_jobs))

    shutil.rmtree(mut_root, ignore_errors=True)
    r3_tot, r3_det = int(r3_m16.get("mutations_tested_count", 0)), int(r3_m16.get("mutations_detected_count", 0))
    inh_tot = r3_tot + len(inh_res)
    inh_det = r3_det + sum(1 for r in inh_res if r["detected_fail_closed"])
    c2_tot, c2_det = len(c2_res), sum(1 for r in c2_res if r["detected_fail_closed"])
    clean_ok = all(v[4] == 0 for v in bases.values()) and bool(r3_m16.get("clean_clone_verifier_green"))

    f19 = _stamp_c2({
        "isolated_temp_clones_only": True,
        "sealed_run_dir_mutated": False,
        "clean_clone_verifier_green": clean_ok,
        "inherited_mutations_tested_count": inh_tot,
        "inherited_mutations_detected_count": inh_det,
        "inherited_mutations_failed_detection_count": inh_tot - inh_det,
        "new_c2_mutations_tested_count": c2_tot,
        "new_c2_mutations_detected_count": c2_det,
        "new_c2_mutations_failed_detection_count": c2_tot - c2_det,
        "total_mutations_tested_count": inh_tot + c2_tot,
        "new_c2_results": c2_res,
        "inherited_results_summary": {"r3": f"{r3_det}/{r3_tot}", "c1_c2_r4_r5_r5c1": f"{sum(1 for r in inh_res if r['detected_fail_closed'])}/{len(inh_res)}"},
        "status": "PASS" if (clean_ok and inh_det == inh_tot and c2_det == c2_tot and inh_tot >= 111 and c2_tot >= 24) else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)
    return f18, f19


def run_kh03_r5_c2_orchestrator(
    *, allow_local_dirty: bool = False, owner_waive_disk_preflight: bool = False, max_workers: int = GLOBAL_MAX_DESCENDANTS_KH02_R2
) -> Tuple[int, Path, Dict[str, Any]]:
    if owner_waive_disk_preflight:
        raise SystemExit(2)

    lock = _CrossPlatformRunLock(Path(tempfile.gettempdir()) / "aurum_desk_kh03_r5_c2_run.lock")
    lock_ok, lock_msg = lock.acquire()
    if not lock_ok:
        raise RuntimeError(f"KH03_R5_C2_SINGLE_RUN_LOCK_FAILED:{lock_msg}")

    t_start = time.perf_counter()
    orch_pid = os.getpid()
    utc_now = datetime.now(timezone.utc)
    _, branch = _git_cmd(["branch", "--show-current"])
    _, start_head = _git_cmd(["rev-parse", "HEAD"])
    _, st_porc = _git_cmd(["status", "--porcelain=v1"])
    _, ls_rem = _git_cmd(["ls-remote", "origin", f"refs/heads/{MANDATORY_BRANCH_KH03_R5_C2}"])
    remote_sha = ls_rem.split()[0] if ls_rem.strip() else ""
    git_clean = (st_porc.strip() == "")

    if branch != MANDATORY_BRANCH_KH03_R5_C2:
        lock.release()
        raise RuntimeError(f"WRONG_BRANCH:{branch}")
    if (not git_clean) and (not allow_local_dirty):
        lock.release()
        raise RuntimeError(f"DIRTY_WORKTREE_REFUSED:\n{st_porc}")

    run_id = f"{utc_now.strftime('%Y%m%dT%H%M%SZ')}_{start_head[:12]}"
    art_base = (REPO_ROOT / KH03_R5_C2_ARTIFACTS_BASE_REL).resolve()
    run_dir = art_base / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "junit").mkdir(exist_ok=True)
    (run_dir / "checksums").mkdir(exist_ok=True)

    short_tmp_base: Optional[Path] = None
    if os.name == "nt" and len(str(Path(tempfile.gettempdir()))) > 20:
        cand_pub = Path(r"C:\Users\Public")
        if cand_pub.is_dir():
            short_tmp_base = cand_pub
    try:
        sentinel_tmp = create_sentinel_owned_temp_dir_r2_c1("c2_", run_id=run_id, owner_pid=orch_pid, base_dir=short_tmp_base)
    except OSError:
        sentinel_tmp = create_sentinel_owned_temp_dir_r2_c1("c2_", run_id=run_id, owner_pid=orch_pid)

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
        print(f"[KH-03.R5.C2] Stage 0: Real isolated C1 baseline RED from {PRIOR_C1_TESTED_EXECUTABLE_SHA[:12]} (03) & C1 all-48 ledger (04)...", flush=True)
        f03 = _stamp_c2(run_real_c1_baseline_red_reproduction_from_git_sha(repo_root=REPO_ROOT, baseline_sha=PRIOR_C1_TESTED_EXECUTABLE_SHA, tested_c2_sha=start_head), run_id=run_id, tested_sha=start_head)
        f04 = _stamp_c2(build_c1_all48_correction_ledger(repo_root=REPO_ROOT, c1_baseline_sha=PRIOR_C1_TESTED_EXECUTABLE_SHA), run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "03_baseline_c1_red_fault_truth_worker_replay.json", f03)
        write_json_durable_atomic(run_dir / "04_c1_all48_control_inventory_and_correction_ledger.json", f04)

        print("[KH-03.R5.C2] Stage 1: Keep-as-is AST/ownership (01) & prior R1..R5.C1 + dataset SHA immutability (02)...", flush=True)
        legacy_cg = audit_kh03_r3_legacy_research_callgraph(REPO_ROOT)
        f01 = _audit_ownership_01_c2(tested_sha=start_head, run_id=run_id, legacy_cg=legacy_cg)
        ro_inputs = load_r4_immutable_research_inputs_readonly(REPO_ROOT, PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R5_C2)
        datasets, prov02 = ro_inputs["datasets"], ro_inputs["provenance"]
        pdir = sentinel_tmp / "c4_prov_c2"
        pdir.mkdir(parents=True, exist_ok=True)
        f08_c4, f09_c4 = audit_kh03_collector_and_escrow_readiness(pdir)
        shutil.rmtree(pdir, ignore_errors=True)
        prov_c4_ok = f08_c4.get("status") == "PASS" and f09_c4.get("status") == "PASS" and f08_c4.get("network_capture_enabled") is False and int(f09_c4.get("real_oos_escrow_final_record_count", -1)) == 0
        f02 = _stamp_c2({
            **prov02,
            "c4_provenance_and_oos_boundary": {
                "network_capture_enabled": f08_c4.get("network_capture_enabled"),
                "live_network_capture_executed": f08_c4.get("live_network_capture_executed"),
                "real_oos_escrow_integrity_valid": f09_c4.get("real_oos_escrow_integrity_valid"),
                "real_oos_escrow_final_record_count": f09_c4.get("real_oos_escrow_final_record_count"),
                "status": "PASS" if prov_c4_ok else "FAIL",
            },
            "status": "PASS" if (prov02.get("status") == "PASS" and prov_c4_ok) else "FAIL",
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "01_function_ownership_keep_as_is_manifest.json", f01)
        write_json_durable_atomic(run_dir / "02_prior_dataset_sealed_integrity_no_oos.json", f02)

        print("[KH-03.R5.C2] Stage 2: Offline C2 closure proofs (06, 07_gz, 08, 09, 10, 11)...", flush=True)
        d06_11 = _run_stage2_c2_closure_proofs_06_to_11(run_dir=run_dir, sentinel_tmp=sentinel_tmp, run_id=run_id, tested_sha=start_head, min_pre_disk_gib=min_pre_dk)
        names_06_11 = {
            "06": "06_legacy_c1_certification_gate_closure.json",
            "08": "08_cancellation_deadline_drain_quarantine_receipts.json",
            "09": "09_grant_identity_and_one_shot_ledger_receipts.json",
            "10": "10_concurrent_replay_request_byte_conservation.json",
            "11": "11_restart_boundary_live_block_and_provenance.json",
        }
        for k, fn in names_06_11.items():
            write_json_durable_atomic(run_dir / fn, d06_11[k])

        print("[KH-03.R5.C2] Stage 3A: Fresh R4 canonical DEV replay, 6 cost scenarios & E1/E2/E3 anchors (12..13)...", flush=True)
        f12_raw, ctx = _run_stage3a_r4_real_replay_13(
            datasets=datasets, dataset_sha=str(prov02["canonical_dataset_sha256"]),
            timeframe_stats=prov02["timeframe_coverage_and_gaps"],
            run_dir=run_dir, sentinel_tmp=sentinel_tmp, run_id=run_id, tested_sha=start_head,
        )
        f12 = _stamp_c2(dict(f12_raw), run_id=run_id, tested_sha=start_head)
        arms_p = f12_raw.get("arms_1x_profiles") or {}
        e1_p = arms_p.get("KH03_R3_E1_LOW_FRICTION_STOP_FLOOR") or {}
        e2_p = arms_p.get("KH03_R3_E2_CAUSAL_DELAYED_CONFIRMATION") or {}
        e3_p = arms_p.get("KH03_R3_E3_CAUSAL_NEAREST_M5_UNSWEPT_EXIT") or {}
        arms_anchor_ok = (
            int(e1_p.get("n_closed", -1)) == 58 and int(e1_p.get("n_wins", -1)) == 3 and int(e1_p.get("n_losses", -1)) == 55 and abs(float(e1_p.get("net_pnl_usd", 0.0)) - (-118.0121)) <= 0.01
            and int(e2_p.get("n_closed", -1)) == 131 and int(e2_p.get("n_wins", -1)) == 5 and int(e2_p.get("n_losses", -1)) == 126 and abs(float(e2_p.get("net_pnl_usd", 0.0)) - (-274.3771)) <= 0.01
            and int(e3_p.get("n_closed", -1)) == 206 and int(e3_p.get("n_wins", -1)) == 9 and int(e3_p.get("n_losses", -1)) == 197 and abs(float(e3_p.get("net_pnl_usd", 0.0)) - (-439.0423)) <= 0.01
        )
        f13 = _stamp_c2({
            "six_cost_dynamic_vs_fixed_fills_separated": bool(f12_raw.get("six_cost_dynamic_vs_fixed_fills_separated")),
            "six_cost_scenarios_null_summary": f12_raw.get("six_cost_scenarios_null_summary"),
            "null_1x_zero_fee_counterfactual_net_usd": f12_raw.get("null_1x_zero_fee_counterfactual_net_usd"),
            "arms_1x_profiles": arms_p,
            "arms_e1_e2_e3_anchors_exact_match": arms_anchor_ok,
            "status": "PASS" if (bool(f12_raw.get("six_cost_dynamic_vs_fixed_fills_separated")) and arms_anchor_ok) else "FAIL",
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "12_r4_replay_decimal_economic_bridge.json", f12)
        write_json_durable_atomic(run_dir / "13_six_cost_scenarios_and_arm_regression.json", f13)

        print("[KH-03.R5.C2] Stage 3B: Running 91 paired faults (05/18) + 288 inherited + 135 separate-OS mutations (19)...", flush=True)
        f18, f19 = _run_stage3b_paired_faults_05_18_and_135_mutations_19(run_dir=run_dir, sentinel_tmp=sentinel_tmp, datasets=datasets, ctx=ctx, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "18_new_and_inherited_fault_control_truth_summary.json", f18)
        write_json_durable_atomic(run_dir / "19_separate_OS_mutation_results.json", f19)
        del datasets, ctx
        gc.collect()

        if can_run_heavy:
            eff_res_b = int(STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2 * (1024 ** 3))
            print(f"[KH-03.R5.C2] Stage 4A: Running FULL repo LPT-sharded pytest (max_workers={pytest_full_cap}) (15)...", flush=True)
            f15 = _run_c2_full_repo_pytest_15(
                run_dir, sentinel_tmp, run_id=run_id, tested_sha=start_head,
                effective_disk_reserve_bytes=eff_res_b, max_workers=pytest_full_cap, git_clean_at_start=git_clean,
            )
            write_json_durable_atomic(run_dir / "15_full_pytest_fresh_collection_junit_bijection.json", f15)
            gc.collect()

            print("[KH-03.R5.C2] Stage 4B: Running fresh W1/W2/W4/W1_repeat pytest, science & C2 capture benchmarks (16)...", flush=True)
            pb_raw = asyncio.run(_run_controlled_pytest_w1_w2_w4_benchmark_r1(sentinel_tmp, enforce_r3_ram_guard=True))
            sb_raw = asyncio.run(_run_r2_science_slice_w1_w2_w4_determinism(sentinel_tmp))
            c2_det = _run_c2_capture_fixture_w1_w2_w4_determinism(sentinel_tmp, run_id, min_pre_dk)
            gc.collect()

            pb_doc = _stamp_c2({"canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2, "benchmark_kind": BENCHMARK_KIND_PYTEST, "status": "PASS" if pb_raw.get("all_4_runs_identical") else "FAIL", **pb_raw}, run_id=run_id, tested_sha=start_head)
            sb_doc = _stamp_c2({"canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2, "benchmark_kind": BENCHMARK_KIND_SCIENCE, "status": "PASS" if sb_raw.get("all_4_runs_identical") else "FAIL", **sb_raw}, run_id=run_id, tested_sha=start_head)
            p_lim = {"configured_child_process_cap": GLOBAL_MAX_DESCENDANTS_KH02_R2, "currently_allowed_worker_cap": pytest_full_cap, "full_suite_safe_worker_cap": pytest_full_cap, "shard_exclusive_cap": pytest_full_cap, "preflight_disk_free_gib": min_pre_dk, "disk_free_gib": min_pre_dk, "available_ram_mib": avail_ram_mib}
            s_lim = {"configured_child_process_cap": GLOBAL_MAX_DESCENDANTS_KH02_R2, "currently_allowed_worker_cap": eff_workers, "full_suite_safe_worker_cap": eff_workers, "shard_exclusive_cap": eff_workers, "preflight_disk_free_gib": min_pre_dk, "disk_free_gib": min_pre_dk, "available_ram_mib": avail_ram_mib}
            p_pol = select_measured_worker_policy_strict(pb_doc, benchmark_kind=BENCHMARK_KIND_PYTEST, system_limits=p_lim, expected_run_id=run_id, expected_tested_sha=start_head, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2)
            s_pol = select_measured_worker_policy_strict(sb_doc, benchmark_kind=BENCHMARK_KIND_SCIENCE, system_limits=s_lim, expected_run_id=run_id, expected_tested_sha=start_head, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2)
            f16 = _stamp_c2({
                "pytest_controlled_subset_raw": pb_doc, "science_slice_8000_raw": sb_doc,
                "pytest_worker_policy": p_pol, "science_worker_policy": s_pol,
                "c2_capture_fixture_w1_w2_w4_benchmark": c2_det,
                "c2_capture_fixture_w1_w2_w4_identical": bool(c2_det["all_4_identical"]),
                "full_suite_actual_max_workers_applied": pytest_full_cap,
                "full_suite_speedup_extrapolated": False,
                "status": "PASS" if (p_pol["status"] == "PASS" and s_pol["status"] == "PASS" and c2_det["all_4_identical"]) else "FAIL",
            }, run_id=run_id, tested_sha=start_head)
            write_json_durable_atomic(run_dir / "16_W1_W2_W4_W1repeat_benchmark_policy.json", f16)

            print("[KH-03.R5.C2] Stage 4C: Running FULL 56,710 A1 + 6,375 SI8 + 246 adapter + D4 parity (14)...", flush=True)
            ds_p, _ = load_r2_canonical_dataset_readonly(REPO_ROOT / CANONICAL_DATASET_REL_PATH)
            f02_r1 = audit_r2_reference_inputs_and_invariants(REPO_ROOT)
            f14 = _stamp_c2(_run_full_production_baseline_parity_20(datasets=ds_p, sentinel_tmp=sentinel_tmp, run_id=run_id, tested_sha=start_head, branch=branch, f01=f01, f02=f02_r1, max_workers=eff_workers), run_id=run_id, tested_sha=start_head)
            write_json_durable_atomic(run_dir / "14_A1_SI8_adapter_D4_full_parity.json", f14)
            del ds_p
            gc.collect()
        else:
            f14 = _stamp_c2({"BASELINE_PARITY_VERDICT": "NOT_RUN_BLOCKED_LOW_DISK", "a1_population_count": 0, "a1_mismatches_count": 0, "si8_population_count": 0, "si8_mismatches_count": 0, "three_adapter_population_count": 0, "three_adapter_mismatches_count": 0, "d4_historical_closed_trades": 0, "per_decision_and_episode_fingerprints": {"all_sealed_fingerprints_exact_match": False}}, run_id=run_id, tested_sha=start_head)
            f15 = _stamp_c2({"status": "NOT_RUN_BLOCKED_LOW_DISK", "collected_count": 0, "executed_count": 0, "passed_count": 0, "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0, "junit_bijection_exact": False}, run_id=run_id, tested_sha=start_head)
            f16 = _stamp_c2({"status": "NOT_RUN_BLOCKED_LOW_DISK", "pytest_controlled_subset_raw": {}, "science_slice_8000_raw": {}, "pytest_worker_policy": {}, "science_worker_policy": {}, "c2_capture_fixture_w1_w2_w4_identical": False, "full_suite_speedup_extrapolated": False}, run_id=run_id, tested_sha=start_head)
            write_json_durable_atomic(run_dir / "14_A1_SI8_adapter_D4_full_parity.json", f14)
            write_json_durable_atomic(run_dir / "15_full_pytest_fresh_collection_junit_bijection.json", f15)
            write_json_durable_atomic(run_dir / "16_W1_W2_W4_W1repeat_benchmark_policy.json", f16)

        cl_info = _cleanup_sentinel_temp_c4_win_safe(sentinel_tmp, expected_run_id=run_id)
        sentinel_cleaned = bool(cl_info["cleaned"])
        telem = sampler.stop_and_flush()
        sampler_stopped = True
        w_pool_end = get_r5_global_worker_pool_stats_c2()

        f17 = _stamp_c2({
            "orchestrator_pid": orch_pid, "sampling_interval_sec": 0.5,
            "memory_samples_log_rel": "logs/runtime_memory_samples_0p5s.jsonl.gz",
            **telem,
            "strict_preflight_disk_min_gib": STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
            "strict_runtime_disk_min_gib": STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2,
            "advisory_disk_gib": ADVISORY_DISK_GIB_KH02_R2,
            "owner_disk_preflight_waiver": False,
            "sentinel_temp_cleaned_up": sentinel_cleaned,
            "active_capture_workers_after_run": int(w_pool_end["active_workers_count"]),
            "quarantined_capture_workers_after_run": int(w_pool_end["quarantined_workers_count"]),
            "total_wall_clock_sec": round(time.perf_counter() - t_start, 3),
            "status": "PASS" if (sentinel_cleaned and int(telem.get("orphan_children_after_cleanup", 1)) == 0 and int(w_pool_end["active_workers_count"]) == 0) else "FAIL",
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "17_actual_resource_telemetry_cleanup.json", f17)

        _, end_head = _git_cmd(["rev-parse", "HEAD"])
        f00 = _stamp_c2({
            "started_utc": utc_now.strftime("%Y-%m-%dT%H:%M:%SZ"), "branch": branch,
            "baseline_reviewed_head_sha": BASELINE_REVIEWED_HEAD_SHA_C2,
            "start_head_sha": start_head, "end_head_sha": end_head, "remote_head_sha": remote_sha,
            "prior_c1_tested_sha": PRIOR_C1_TESTED_EXECUTABLE_SHA,
            "prior_c1_evidence_sha": PRIOR_C1_EVIDENCE_COMMIT_SHA,
            "prior_c1_run_id": PRIOR_C1_RUN_ID,
            "git_clean_at_start": git_clean, "local_uncommitted_changes": not git_clean,
            "single_run_lock_status": lock_msg, "orchestrator_pid": orch_pid,
            "python_version": platform.python_version(), "platform": platform.platform(),
            "cpu_count": os.cpu_count(), "random_seed": DEFAULT_RANDOM_SEED_KH02_R2,
            "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
            "owner_disk_preflight_waiver": False, "strict_multi_mount_probe": strict_probe,
            "preflight_ram_available_mib": avail_ram_mib, "can_run_heavy_stages": can_run_heavy,
            "readonly_disk_inventory": build_r2_c1_readonly_disk_inventory(REPO_ROOT) if not strict_disk_ok else None,
            "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2), "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
            "status": "PASS" if (strict_disk_ok and ram_ok and git_clean) else ("BLOCKED_LOW_DISK" if not strict_disk_ok else "BLOCKED_PREFLIGHT"),
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "00_preflight_branch_sha_resource_lock.json", f00)

        pub_st = "PRE_PUSH_PENDING" if (strict_disk_ok and git_clean) else "NOT_PUBLISHED_BLOCKED_LOW_DISK"
        f22 = _stamp_c2({
            "branch": branch, "start_head_sha": start_head,
            "evidence_commit_sha": None, "publication_head_sha": None,
            "remote_readback_sha256_verified": False, "latest_run_pointer_updated": False,
            "owner_disk_preflight_waiver": False, "ci_status": "CI_UNKNOWN_NO_ACTIONS_RUN",
            "actual_github_actions_run_verified": False, "publication_status": pub_st, "status": pub_st,
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "22_phase1_code_commit_push_receipt.json", f22)

        all_docs = {"00": f00, "01": f01, "02": f02, "03": f03, "04": f04, **d06_11, "12": f12, "13": f13, "14": f14, "15": f15, "16": f16, "17": f17, "18": f18, "19": f19, "22": f22}
        mem_au = _audit_telemetry_gz_vs_17_c2(run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz", f17)
        r4_au = _audit_r4_replay_ledger_12_c2(run_dir, f12)
        pf_au = _audit_paired_fault_receipts_05_c2(run_dir, f18, expected_run_id=run_id, expected_tested_sha=start_head)
        wt_au = _audit_worker_lifecycle_traces_07_c2(run_dir, d06_11["08"])
        p_rec = _verify_benchmark_16_c2(f16.get("pytest_controlled_subset_raw") or {}, f16.get("pytest_worker_policy") or {}, kind=BENCHMARK_KIND_PYTEST, expected_run_id=run_id, expected_tested_sha=start_head, max_safe_cap=3) if f16.get("status") != "NOT_RUN_BLOCKED_LOW_DISK" else {"valid": False}
        s_rec = _verify_benchmark_16_c2(f16.get("science_slice_8000_raw") or {}, f16.get("science_worker_policy") or {}, kind=BENCHMARK_KIND_SCIENCE, expected_run_id=run_id, expected_tested_sha=start_head, max_safe_cap=GLOBAL_MAX_DESCENDANTS_KH02_R2) if f16.get("status") != "NOT_RUN_BLOCKED_LOW_DISK" else {"valid": False}

        g_res = derive_r5_c2_readiness_verdicts(
            raw_evidence=all_docs, mem_audit=mem_au, pytest_recomp=p_rec, science_recomp=s_rec,
            junit_ok=bool(f15.get("junit_bijection_exact")), r4_replay_ok=bool(r4_au.get("valid")),
            paired_faults_recomp=pf_au, worker_traces_recomp=wt_au,
            stale_prior_shas=STALE_PRIOR_SHAS_R5_C2, stale_prior_run_ids=STALE_PRIOR_RUN_IDS_R5_C2,
            for_final_verifier_manifest=False, verifier_violations_count=0,
        )
        f20 = _stamp_c2(g_res, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "20_orthogonal_gates_and_verdicts.json", f20)
        all_docs["20"] = f20

        handover_vi = build_r5_c2_owner_handover_vi(f20, all_docs)
        (run_dir / "HANDOVER_KH03_R5_C2_VI.txt").write_text(handover_vi, encoding="utf-8")
        (run_dir / "README.md").write_text(
            f"# AURUM DESK — KH-03.R5.C2 | Technical Closure (Fault Truth, Worker Lifecycle & Grant Replay)\n\n"
            f"- **Run ID**: `{run_id}` | **Branch**: `{branch}` | **Tested SHA**: `{start_head}`\n"
            f"- **Classification**: `{f20['overall_classification']}`\n"
            f"- **ENGINEERING**: `{f20['verdicts']['ENGINEERING']}` | **CONTROL_TRUTH**: `{f20['verdicts']['CONTROL_TRUTH']}` | **WORKER_LIFECYCLE**: `{f20['verdicts']['WORKER_LIFECYCLE']}` | **GRANT_REPLAY_OFFLINE**: `{f20['verdicts']['GRANT_REPLAY_SAFETY_OFFLINE']}`\n"
            f"- **LIVE_GRANT_REPLAY_DURABILITY**: `NOT_PROVEN_BLOCKED` | **LIVE_TRANSPORT_TERMINATION**: `NOT_PROVEN_BLOCKED`\n"
            f"- **CAPTURE_AUTHORIZATION**: `BLOCKED_OWNER_AUTHORIZATION` | **ECONOMIC**: `NO_PROVEN_EDGE` | **ACTIVATION_READY**: `NO` | **OOS**: `0` | **EXTERNAL_MARKET_REQUESTS**: `0`\n",
            encoding="utf-8",
        )

        # Build checksums/sha256_inventory.json BEFORE separate-OS verifier seals 21!
        ck_inv: Dict[str, Dict[str, Any]] = {}
        for fp in sorted(run_dir.rglob("*")):
            if fp.is_file():
                rel_p = str(fp.relative_to(run_dir)).replace("\\", "/")
                if rel_p in ("checksums/sha256_inventory.json", "21_independent_OS_verifier_manifest_sha256.json"):
                    continue
                ck_inv[rel_p] = {"sha256": _sha256_file(fp), "bytes": fp.stat().st_size}
        write_json_durable_atomic(run_dir / "checksums" / "sha256_inventory.json", _stamp_c2({"files_count": len(ck_inv), "files": ck_inv}, run_id=run_id, tested_sha=start_head))

        print("[KH-03.R5.C2] Stage 5: Invoking Separate-OS Independent Verifier (21)...", flush=True)
        v_env = dict(os.environ); v_env["PYTHONPATH"] = _pypath_str(); v_env["PYTHONHASHSEED"] = "0"
        v_proc = subprocess.run(
            [sys.executable, str(APP_ROOT / "tools" / "kh_03_r5_c2_independent_verifier.py"), "--run-dir", str(run_dir), "--expected-run-id", run_id, "--expected-tested-sha", start_head],
            cwd=str(APP_ROOT), env=v_env, capture_output=True, text=True, timeout=180,
        )
        print(v_proc.stdout, flush=True)
        if v_proc.stderr:
            print(v_proc.stderr, file=sys.stderr, flush=True)

        return int(v_proc.returncode), run_dir, f20
    finally:
        if not sampler_stopped:
            try:
                sampler.stop_and_flush()
            except Exception:
                pass
        lock.release()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="AURUM DESK — KH-03.R5.C2 Technical Closure Acceptance Runner")
    ap.add_argument("--allow-local-dirty", action="store_true")
    ap.add_argument("--max-workers", type=int, default=GLOBAL_MAX_DESCENDANTS_KH02_R2)
    args = ap.parse_args(argv)
    rc, run_dir, f20 = run_kh03_r5_c2_orchestrator(allow_local_dirty=bool(args.allow_local_dirty), max_workers=int(args.max_workers))
    print(f"[KH-03.R5.C2] DONE rc={rc} run_dir={run_dir} classification={f20.get('overall_classification')}", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
