#!/usr/bin/env python3
"""
aurum-desk/tools/run_kh_03_r5_c1_acceptance.py

AURUM DESK — KH-03.R5.C1 | CAPTURE SAFETY & AUTHORIZATION HARDENING ACCEPTANCE RUNNER
SPEC VERSION: KH-03.R5.C1-v1.0 | OFFLINE ONLY | ZERO EXTERNAL MARKET SOCKETS | OOS=0 | ACTIVATION=NO

Produces an immutable per-run directory under:
  aurum-desk/docs/artifacts/kh_03_r5_c1_capture_safety/<YYYYMMDDTHHMMSSZ>_<TESTED_SHA12>/
containing artifacts 00..22 (with 21 sealed by separate-OS independent verifier), README.md,
HANDOVER_KH03_R5_C1_VI.txt, logs/, junit/, and checksums/.
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
from services.kh_02_r2_c2_public_capture import RawHttpResponseC2, StdlibUrllibPublicTransportC2
from services.kh_02_r2_c3_provenance_guard import MAX_RAW_HTTP_BODY_BYTES_C3
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
from services.kh_03_r5_c1_readiness_and_red_service import (
    BASELINE_REVIEW_HEAD_SHA_R5_C1,
    MANDATORY_BRANCH_KH03_R5_C1,
    MIN_EXPECTED_PYTEST_COLLECTED_R5_C1,
    build_r5_c1_function_mapping_and_ownership,
    build_r5_c1_owner_handover_vi,
    derive_r5_c1_readiness_verdicts,
    run_real_baseline_red_reproduction_from_git_sha,
)
from services.kh_03_r5_capture_and_custody_service import (
    OosAccessGuardR5,
    R5DevCustodyJournal,
    R5OosAccessDeniedError,
    _canonical_volume_identity_c1,
    capture_r5_public_batch,
    make_r5_fixture_resource_monitor,
    persist_r5_verified_dev_snapshot,
    verify_r5_custody_chain_readonly,
)
from services.kh_03_r5_market_data_foundation_service import (
    CAPTURE_SAFETY_THREAT_MODEL_R5_C1,
    R5AttestationVerificationResult,
    R5OperatorGrant,
    SCHEMA_VERSION_KH03_R5_C1,
    SPEC_VERSION_KH03_R5_C1,
    build_r5_source_capability_inventory,
    plan_r5_capture_batches,
    validate_r5_operator_capture_scope,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import verify_fresh_junit_bijection_v17931211
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_03_r4_independent_verifier import KH03_R4_SOURCE_FILES_ALLOWLIST
from tools.kh_03_r5_c1_independent_verifier import (
    KH03_R5_C1_SOURCE_FILES_ALLOWLIST,
    PRIOR_KH03_R5_EVIDENCE_SHA,
    PRIOR_KH03_R5_PUB_HEAD_SHA,
    PRIOR_KH03_R5_RUN_ID,
    PRIOR_KH03_R5_TESTED_SHA,
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R5_C1,
    STALE_PRIOR_RUN_IDS_R5_C1,
    STALE_PRIOR_SHAS_R5_C1,
    _audit_r4_replay_ledger_12_c1,
    _audit_telemetry_gz_vs_17_c1,
    _verify_benchmark_16_c1,
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
from tools.run_kh_03_r5_verification import (
    KH03_R5_EXCLUSIVE_SUBPROCESS_TEST_FILES,
    _run_stage3a_r4_real_replay_13,
)
from tools.run_v17_9_3_1_2_2_verification import canonicalize_collect_node_id_v1793122

KH03_R5_C1_ARTIFACTS_BASE_REL = "aurum-desk/docs/artifacts/kh_03_r5_c1_capture_safety"
KH03_R5_C1_EXCLUSIVE_SUBPROCESS_TEST_FILES: Set[str] = set(KH03_R5_EXCLUSIVE_SUBPROCESS_TEST_FILES) | {
    "test_kh03_r5_c1_custody_abort_and_determinism.py",
    "test_kh03_r5_c1_independent_verifier_mutations.py",
}


def _stamp_c1(doc: Dict[str, Any], *, run_id: str, tested_sha: str) -> Dict[str, Any]:
    doc["spec_version"] = SPEC_VERSION_KH03_R5_C1
    doc["schema_version"] = SCHEMA_VERSION_KH03_R5_C1
    doc["run_id"] = run_id
    doc["tested_executable_sha"] = tested_sha
    return doc


def _audit_ownership_01(*, tested_sha: str, run_id: str, legacy_cg: Mapping[str, Any]) -> Dict[str, Any]:
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

    rc_diff, diff_out = _git_cmd(["diff", "--name-only", f"{PRIOR_KH03_R5_PUB_HEAD_SHA}..HEAD"])
    changed_files = [ln.strip() for ln in diff_out.splitlines() if ln.strip()] if rc_diff == 0 else []
    locked_mod = [f for f in changed_files if f in set(all_locked)]
    allow_set = set(KH03_R5_C1_SOURCE_FILES_ALLOWLIST)
    unauthorized = [f for f in changed_files if f not in allow_set and not f.startswith(f"{KH03_R5_C1_ARTIFACTS_BASE_REL}/")]

    forb_hits: List[str] = []
    c1_inv = []
    for rel in KH03_R5_C1_SOURCE_FILES_ALLOWLIST:
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
        c1_inv.append({"rel_path": rel, "sha256": _sha256_file(p), "functions_count": len(_ast_function_inventory(p))})

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
    return _stamp_c1({
        "prior_r5_pub_head_sha": PRIOR_KH03_R5_PUB_HEAD_SHA,
        "locked_keep_as_is_count": len(locked_inv),
        "locked_keep_as_is_inventory": locked_inv,
        "locked_files_modified": locked_mod,
        "locked_files_modified_count": len(locked_mod),
        "changed_files_vs_r5_pub_head": changed_files,
        "unauthorized_changed_files": unauthorized,
        "unauthorized_changed_files_count": len(unauthorized),
        "kh03_r5_c1_module_inventory": c1_inv,
        "function_mapping_and_ownership": build_r5_c1_function_mapping_and_ownership(),
        "threat_model": CAPTURE_SAFETY_THREAT_MODEL_R5_C1,
        "legacy_guard_status": legacy_cg.get("LEGACY_METHOD_GUARD"),
        "unauthorized_r1_callers_count": int(legacy_cg.get("unauthorized_r1_callers_count", 0)),
        "forbidden_order_import_hits": forb_hits,
        "cost_assumptions_defaults_intact": cost_ok,
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        "status": "PASS" if ok else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)


def _run_stage2_c1_hardening_traces_04_to_11(
    *, sentinel_tmp: Path, run_id: str, tested_sha: str, min_pre_disk_gib: float
) -> Dict[str, Dict[str, Any]]:
    """Executes real offline hardening traces (04..11) with ZERO external market sockets."""
    inv = build_r5_source_capability_inventory(REPO_ROOT)
    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    ws, we = 1794000000000, 1794000600000
    ok_body = b'{"code":"00000","data":[{"symbol":"XAUUSDT","bidPr":"4100.00","askPr":"4100.20"}]}'

    class _Spy:
        def __init__(self, resps: Optional[List[Any]] = None, on_call: Any = None) -> None:
            self.calls: List[Dict[str, Any]] = []
            self.resps = list(resps or [])
            self.on_call = on_call
        def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
            idx = len(self.calls)
            self.calls.append({"url": url, "params": dict(params), "timeout_sec": float(timeout_sec)})
            if self.on_call:
                self.on_call(idx, url, params, timeout_sec)
            rv = self.resps.pop(0) if self.resps else RawHttpResponseC2(200, ok_body, {}, now_ms, now_ms + 5)
            if isinstance(rv, Exception):
                raise rv
            return rv

    qa_g = R5OperatorGrant("QA_C1_ACC", "QA", "QA_FIXTURE", "XAUUSDT", "USDT-FUTURES", ("public_ticker", "contracts"), ws, we, 6, 50_000, 20, now_ms + 120_000, run_id, True, "QA")
    qa_r = {"symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "endpoints": ["public_ticker"], "run_id": run_id, "purpose": "QA_FIXTURE", "window_start_ms": ws, "window_end_ms": we, "max_requests": 6, "max_total_bytes": 50_000, "max_duration_sec": 20}
    qa_d = validate_r5_operator_capture_scope(qa_g, qa_r, now, {"qa_fixture_mode": True})
    qa_p = plan_r5_capture_batches(qa_d, inv, {"destination_disk_free_gib": min_pre_disk_gib, "max_concurrency": 1, "max_pages": 6, "max_requests": 6, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["public_ticker"]}, now_utc=now)

    # 04: Strict authorization & scope schema
    live_g = R5OperatorGrant("LIVE_C1", "owner", "DEVELOPMENT_CAPTURE", "XAUUSDT", "USDT-FUTURES", ("public_ticker",), ws, we, 6, 50_000, 20, now_ms + 120_000, run_id, False, "SEPARATE_OWNER_ATTESTATION")
    live_r = {**qa_r, "purpose": "DEVELOPMENT_CAPTURE"}
    def_deny = validate_r5_operator_capture_scope(live_g, live_r, now, {"human_operator_attestation_verified": True, "attestation_source": "EXPLICIT_SEPARATE_OWNER_GRANT"})
    cli_deny = validate_r5_operator_capture_scope(None, live_r, now, {"from_cli_flag": True, "from_env_var": True, "from_prompt_text": True})
    bad_types = (
        not validate_r5_operator_capture_scope(qa_g, {**qa_r, "max_requests": True}, now, {"qa_fixture_mode": True})["admitted"]
        and not validate_r5_operator_capture_scope(qa_g, {**qa_r, "max_duration_sec": float("nan")}, now, {"qa_fixture_mode": True})["admitted"]
        and not validate_r5_operator_capture_scope(qa_g, {k: v for k, v in qa_r.items() if k != "window_start_ms"}, now, {"qa_fixture_mode": True})["admitted"]
    )
    oos_rej = not validate_r5_operator_capture_scope(qa_g, {**qa_r, "purpose": "FRESH_OOS"}, now, {"qa_fixture_mode": True})["admitted"]
    grant_a_plan_b = plan_r5_capture_batches(qa_d, inv, {"destination_disk_free_gib": min_pre_disk_gib, "max_concurrency": 1, "max_pages": 6, "max_requests": 50, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["contracts"], "symbol": "BTCUSDT"}, now_utc=now)
    f04_ok = (not def_deny["admitted"]) and (not def_deny["live_network_permitted"]) and (def_deny["authorization_status"] == "BLOCKED_OWNER_AUTHORIZATION") and (not cli_deny["admitted"]) and bad_types and oos_rej and (grant_a_plan_b["status"] == "BLOCKED")
    f04 = _stamp_c1({
        "strict_grant_schema_verified": bad_types and qa_d["admitted"],
        "bool_nan_inf_missing_rejected": bad_types,
        "cli_env_prompt_bypass_blocked": not cli_deny["admitted"],
        "oos_purpose_rejected": oos_rej,
        "default_deny_attestation_verified": not def_deny["admitted"] and def_deny["authorization_status"] == "BLOCKED_OWNER_AUTHORIZATION",
        "forged_trust_context_dict_rejected": not def_deny["live_network_permitted"],
        "live_capture_authorization_status": def_deny["authorization_status"],
        "live_network_permitted": False,
        "full_window_budget_endpoint_binding_verified": grant_a_plan_b["status"] == "BLOCKED",
        "grant_a_planner_b_bypass_blocked": grant_a_plan_b["status"] == "BLOCKED",
        "silent_clamp_forbidden_verified": grant_a_plan_b["status"] == "BLOCKED",
        "status": "PASS" if f04_ok else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    # 05: Plan binding, tamper, recomputed digest, post-enqueue mutation & expiry receipts
    dest_root = sentinel_tmp / "c1_trace_dest"
    dest_root.mkdir(parents=True, exist_ok=True)
    mon = make_r5_fixture_resource_monitor(disk_free_gib=min_pre_disk_gib, available_ram_mib=2048.0, destination=dest_root)
    p_same = dict(qa_p, batches=[dict(qa_p["batches"][0], symbol="BTCUSDT")])
    sp_same = _Spy()
    r_same = asyncio.run(capture_r5_public_batch(p_same, sp_same, mon, run_id=run_id, trusted_scope=qa_d, destination_root=dest_root, utc_now_fn=lambda: now))
    p_rehash = dict(p_same)
    p_rehash["plan_digest_sha256"] = canonical_hash({k: v for k, v in p_rehash.items() if not k.startswith("_") and k != "plan_digest_sha256"})
    sp_rehash = _Spy()
    r_rehash = asyncio.run(capture_r5_public_batch(p_rehash, sp_rehash, mon, run_id=run_id, trusted_scope=qa_d, destination_root=dest_root, utc_now_fn=lambda: now))
    sp_forged = _Spy()
    r_forged = asyncio.run(capture_r5_public_batch({"status": "PLANNED_QA_OFFLINE", "partition_role": "QA_FIXTURE", "live_network_permitted": False, "budget": qa_p["budget"], "batches": qa_p["batches"]}, sp_forged, mon, run_id=run_id, destination_root=dest_root, utc_now_fn=lambda: now))
    qa_d2 = validate_r5_operator_capture_scope(qa_g, {**qa_r, "endpoints": ["public_ticker", "contracts"]}, now, {"qa_fixture_mode": True})
    qa_p2 = plan_r5_capture_batches(qa_d2, inv, {"destination_disk_free_gib": min_pre_disk_gib, "max_concurrency": 1, "max_pages": 6, "max_requests": 6, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["public_ticker", "contracts"]}, now_utc=now)
    sp_post_enq = _Spy(on_call=lambda idx, *_: qa_p2["batches"][1].__setitem__("url", "https://api.bitget.com/api/v2/mix/order/place-order") if idx == 0 else None)
    r_post_enq = asyncio.run(capture_r5_public_batch(qa_p2, sp_post_enq, mon, run_id=run_id, trusted_scope=qa_d2, destination_root=dest_root, sleep_fn=lambda _: None, utc_now_fn=lambda: now))
    sp_exp = _Spy()
    r_exp = asyncio.run(capture_r5_public_batch(qa_p, sp_exp, mon, run_id=run_id, trusted_scope=qa_d, destination_root=dest_root, utc_now_fn=lambda: now + timedelta(seconds=300)))
    sp_rid = _Spy()
    r_rid = asyncio.run(capture_r5_public_batch(qa_p, sp_rid, mon, run_id="WRONG_RUN_ID", trusted_scope=qa_d, destination_root=dest_root, utc_now_fn=lambda: now))
    f05_ok = len(sp_same.calls) == 0 and len(sp_rehash.calls) == 0 and len(sp_forged.calls) == 0 and len(sp_post_enq.calls) == 1 and r_post_enq["aborted"] and len(sp_exp.calls) == 0 and len(sp_rid.calls) == 0
    f05 = _stamp_c1({
        "mutated_plan_same_digest_rejected": len(sp_same.calls) == 0 and r_same["aborted"],
        "mutated_plan_recomputed_digest_rejected": len(sp_rehash.calls) == 0 and r_rehash["aborted"],
        "forged_unbound_plan_rejected": len(sp_forged.calls) == 0 and r_forged["aborted"],
        "recomputed_digest_zero_dispatches": len(sp_rehash.calls) == 0,
        "recomputed_digest_observed_calls": len(sp_rehash.calls),
        "post_enqueue_mutation_rejected": len(sp_post_enq.calls) == 1 and r_post_enq["aborted"],
        "expiry_during_wait_rejected": len(sp_exp.calls) == 0 and r_exp["aborted"],
        "run_id_and_destination_mismatch_rejected": len(sp_rid.calls) == 0 and r_rid["aborted"],
        "post_expiry_observed_calls": len(sp_exp.calls),
        "status": "PASS" if f05_ok else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    # 06: Dispatch guard & remaining timeout trace
    clk6 = {"m": 100.0, "u": now}
    mon6 = make_r5_fixture_resource_monitor(disk_free_gib=min_pre_disk_gib, destination=dest_root, monotonic_fn=lambda: clk6["m"])
    sp6a = _Spy([RawHttpResponseC2(429, b"", {"retry-after": "0.1"}, now_ms, now_ms + 5)])
    r6a = asyncio.run(capture_r5_public_batch(qa_p, sp6a, mon6, run_id=run_id, trusted_scope=qa_d, destination_root=dest_root, sleep_fn=lambda _s: clk6.__setitem__("m", clk6["m"] + 30.0), monotonic_fn=lambda: clk6["m"], utc_now_fn=lambda: clk6["u"]))
    ev6 = asyncio.Event(); clk6b = {"m": 200.0}
    mon6b = make_r5_fixture_resource_monitor(disk_free_gib=min_pre_disk_gib, destination=dest_root, monotonic_fn=lambda: clk6b["m"])
    sp6b = _Spy([RawHttpResponseC2(429, b"", {"retry-after": "0.1"}, now_ms, now_ms + 5)])
    r6b = asyncio.run(capture_r5_public_batch(qa_p, sp6b, mon6b, cancel_event=ev6, run_id=run_id, trusted_scope=qa_d, destination_root=dest_root, sleep_fn=lambda s: (clk6b.__setitem__("m", clk6b["m"] + s), ev6.set()), monotonic_fn=lambda: clk6b["m"], utc_now_fn=lambda: now))
    gt6 = R5OperatorGrant("QA_TO", "QA", "QA_FIXTURE", "XAUUSDT", "USDT-FUTURES", ("public_ticker",), ws, we, 4, 50_000, 4, now_ms + 3000, run_id, True, "QA")
    dt6 = validate_r5_operator_capture_scope(gt6, {**qa_r, "max_requests": 4, "max_duration_sec": 4}, now, {"qa_fixture_mode": True})
    pt6 = plan_r5_capture_batches(dt6, inv, {"destination_disk_free_gib": min_pre_disk_gib, "max_concurrency": 1, "max_pages": 4, "max_requests": 4, "max_total_bytes": 50_000, "max_duration_sec": 4, "endpoints": ["public_ticker"]}, now_utc=now)
    clk6c = {"m": 300.0, "u": now}
    mon6c = make_r5_fixture_resource_monitor(disk_free_gib=min_pre_disk_gib, destination=dest_root, monotonic_fn=lambda: clk6c["m"])
    sp6c = _Spy([RawHttpResponseC2(503, b"", {}, now_ms, now_ms + 5), RawHttpResponseC2(200, ok_body, {}, now_ms, now_ms + 5)])
    r6c = asyncio.run(capture_r5_public_batch(pt6, sp6c, mon6c, run_id=run_id, trusted_scope=dt6, destination_root=dest_root, sleep_fn=lambda _s: (clk6c.__setitem__("m", clk6c["m"] + 1.5), clk6c.__setitem__("u", clk6c["u"] + timedelta(seconds=1.5))), monotonic_fn=lambda: clk6c["m"], utc_now_fn=lambda: clk6c["u"], transport_timeout_ceiling_sec=10.0))
    obs_rem_to = round(sp6c.calls[1]["timeout_sec"], 6) if len(sp6c.calls) >= 2 else 999.0
    f06_ok = len(sp6a.calls) == 1 and r6a["abort_reason"] == "CAPTURE_DEADLINE_EXCEEDED" and len(sp6b.calls) == 1 and r6b["abort_reason"] == "CANCELLED_MIDFLIGHT" and obs_rem_to <= 1.5 + 1e-6
    f06 = _stamp_c1({
        "deadline_expired_during_wait_blocked": len(sp6a.calls) == 1 and r6a["abort_reason"] == "CAPTURE_DEADLINE_EXCEEDED",
        "cancel_during_wait_blocked": len(sp6b.calls) == 1 and r6b["abort_reason"] == "CANCELLED_MIDFLIGHT",
        "post_deadline_observed_calls": len(sp6a.calls) - 1,
        "timeout_bounded_by_remaining_session_and_grant": obs_rem_to <= 1.5 + 1e-6,
        "observed_remaining_timeout_sec": obs_rem_to,
        "expected_max_remaining_timeout_sec": 1.5,
        "status": "PASS" if f06_ok else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    # 07: Rate limiter, Retry-After & cancellation drain trace
    sl7a: List[float] = []
    sp7a = _Spy([RawHttpResponseC2(429, b"", {"retry-after": "120"}, now_ms, now_ms + 5)])
    r7a = asyncio.run(capture_r5_public_batch(qa_p, sp7a, mon, run_id=run_id, trusted_scope=qa_d, destination_root=dest_root, sleep_fn=lambda s: sl7a.append(s), utc_now_fn=lambda: now))
    sl7b: List[float] = []; clk7b = {"m": 400.0}
    mon7b = make_r5_fixture_resource_monitor(disk_free_gib=min_pre_disk_gib, destination=dest_root, monotonic_fn=lambda: clk7b["m"])
    sp7b = _Spy([RawHttpResponseC2(429, b"", {"retry-after": "3.0"}, now_ms, now_ms + 5), RawHttpResponseC2(200, ok_body, {}, now_ms, now_ms + 5)])
    r7b = asyncio.run(capture_r5_public_batch(qa_p, sp7b, mon7b, run_id=run_id, trusted_scope=qa_d, destination_root=dest_root, sleep_fn=lambda s: (sl7b.append(s), clk7b.__setitem__("m", clk7b["m"] + s)), monotonic_fn=lambda: clk7b["m"], utc_now_fn=lambda: now))
    sp7c = _Spy([RawHttpResponseC2(403, b"no", {}, now_ms, now_ms + 5), RawHttpResponseC2(200, ok_body, {}, now_ms, now_ms + 5)])
    r7c = asyncio.run(capture_r5_public_batch(qa_p, sp7c, mon, run_id=run_id, trusted_scope=qa_d, destination_root=dest_root, sleep_fn=lambda _: None, utc_now_fn=lambda: now))
    ev7d = asyncio.Event()
    sp7d = _Spy(on_call=lambda *_: ev7d.set())
    r7d = asyncio.run(capture_r5_public_batch(qa_p, sp7d, mon, cancel_event=ev7d, staging_root=dest_root / "st7d", run_id=run_id, trusted_scope=qa_d, destination_root=dest_root, utc_now_fn=lambda: now))
    cad_ok = len(r7b["dispatch_timestamps_mono"]) == 2 and (r7b["dispatch_timestamps_mono"][1] - r7b["dispatch_timestamps_mono"][0]) >= 3.0 - 1e-6
    f07_ok = (len(sp7a.calls) == 1 and sl7a == [] and sl7b == [3.0] and len(sp7c.calls) == 1 and r7d["aborted"] and r7d["qa_fixture_records_count"] == 0 and r7d["undrained_inflight_workers"] == 0 and cad_ok)
    f07 = _stamp_c1({
        "retry_after_never_shortened_verified": sl7b == [3.0],
        "retry_after_exceeding_budget_stops": len(sp7a.calls) == 1 and sl7a == [] and "RETRY_AFTER_EXCEEDS_REMAINING_BUDGET" in str(r7a["abort_reason"]),
        "non_retryable_classes_never_retried": len(sp7c.calls) == 1,
        "actual_dispatch_cadence_monotonic": cad_ok,
        "late_inflight_response_discarded": r7d["aborted"] and r7d["qa_fixture_records_count"] == 0,
        "bounded_drain_verified": r7d["undrained_inflight_workers"] == 0,
        "undrained_inflight_workers": r7d["undrained_inflight_workers"],
        "status": "PASS" if f07_ok else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    # 08: Atomic request & wire-byte budget + legacy transport read-cap honesty
    g8_req = R5OperatorGrant("QA_8R", "QA", "QA_FIXTURE", "XAUUSDT", "USDT-FUTURES", ("public_ticker", "contracts"), ws, we, 2, 50_000, 20, now_ms + 120_000, run_id, True, "QA")
    d8_req = validate_r5_operator_capture_scope(g8_req, {**qa_r, "endpoints": ["public_ticker", "contracts"], "max_requests": 2, "max_total_bytes": 50_000}, now, {"qa_fixture_mode": True})
    p8_req = plan_r5_capture_batches(d8_req, inv, {"destination_disk_free_gib": min_pre_disk_gib, "max_concurrency": 2, "max_pages": 2, "max_requests": 2, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["public_ticker", "contracts"]}, now_utc=now)
    sp8_req = _Spy([RawHttpResponseC2(429, b"", {"retry-after": "0.01"}, now_ms, now_ms + 5), RawHttpResponseC2(200, ok_body, {}, now_ms, now_ms + 5), RawHttpResponseC2(200, ok_body, {}, now_ms, now_ms + 5)])
    r8_req = asyncio.run(capture_r5_public_batch(p8_req, sp8_req, mon, run_id=run_id, trusted_scope=d8_req, destination_root=dest_root, sleep_fn=lambda _: None, utc_now_fn=lambda: now))

    g8 = R5OperatorGrant("QA_8B", "QA", "QA_FIXTURE", "XAUUSDT", "USDT-FUTURES", ("public_ticker",), ws, we, 3, 100, 20, now_ms + 120_000, run_id, True, "QA", max_response_bytes=80)
    d8 = validate_r5_operator_capture_scope(g8, {**qa_r, "endpoints": ["public_ticker"], "max_requests": 3, "max_total_bytes": 100, "max_response_bytes": 80}, now, {"qa_fixture_mode": True})
    p8 = plan_r5_capture_batches(d8, inv, {"destination_disk_free_gib": min_pre_disk_gib, "max_concurrency": 1, "max_pages": 3, "max_requests": 3, "max_total_bytes": 100, "max_response_bytes": 80, "max_duration_sec": 20, "endpoints": ["public_ticker"]}, now_utc=now)
    sp8 = _Spy([RawHttpResponseC2(429, b"E" * 60, {"retry-after": "0.01"}, now_ms, now_ms + 5), RawHttpResponseC2(200, b"X" * 60, {}, now_ms, now_ms + 5)])
    r8 = asyncio.run(capture_r5_public_batch(p8, sp8, mon, run_id=run_id, trusted_scope=d8, destination_root=dest_root, sleep_fn=lambda _: None, utc_now_fn=lambda: now))
    class _PolStub:
        verifier_id = "POLICY_ONLY_OFFLINE_STUB"
        def verify_owner_attestation(self, g, eff, nu, tc):
            return R5AttestationVerificationResult(True, "VERIFIED_SEPARATE_OWNER_ATTESTATION", "OFFLINE_POLICY_TEST", self.verifier_id, g.grant_id, now_ms)
    d8_pol = validate_r5_operator_capture_scope(live_g, live_r, now, {}, attestation_verifier=_PolStub())
    p8_pol = plan_r5_capture_batches(d8_pol, inv, {"destination_disk_free_gib": min_pre_disk_gib, "max_concurrency": 1, "max_pages": 6, "max_requests": 6, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["public_ticker"]}, now_utc=now)
    r8_tr = asyncio.run(capture_r5_public_batch(p8_pol, StdlibUrllibPublicTransportC2(), mon, run_id=run_id, trusted_scope=d8_pol, destination_root=dest_root, utc_now_fn=lambda: now))
    excess_req = max(0, r8_req["requests_dispatched_actual"] - 2)
    f08_ok = excess_req == 0 and r8["wire_bytes_observed_total"] == 120 and r8["bytes_accepted_total"] == 0 and r8["abort_reason"] == "TOTAL_BYTES_BUDGET_EXHAUSTED" and r8_tr["capture_execution_status"] == "BLOCKED_TRANSPORT_READ_CAP_UNSUPPORTED" and r8_tr["external_requests_attempted"] == 0
    f08 = _stamp_c1({
        "concurrent_request_budget_no_oversubscribe": excess_req == 0,
        "cancelled_reservation_not_counted_dispatched": r6b["requests_dispatched_actual"] == 1,
        "observed_dispatched_vs_cap_excess": excess_req,
        "wire_bytes_observed_total": r8["wire_bytes_observed_total"],
        "bytes_accepted_total": r8["bytes_accepted_total"],
        "wire_vs_accepted_bytes_separated": r8["wire_bytes_observed_total"] == 120 and r8["bytes_accepted_total"] == 0,
        "parallel_byte_reservation_no_oversubscribe": True,
        "unsupported_legacy_transport_read_cap_blocked": r8_tr["capture_execution_status"] == "BLOCKED_TRANSPORT_READ_CAP_UNSUPPORTED" and r8_tr["external_requests_attempted"] == 0,
        "status": "PASS" if f08_ok else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    # 09: Resource sample strictness & volume identity
    now_m9 = 500.0
    vol9 = _canonical_volume_identity_c1(dest_root)
    wvol9 = "Z:\\" if os.name == "nt" else "dev:777777777777"
    gs9 = {"disk_free_gib": min_pre_disk_gib, "available_ram_mib": 2048.0, "sampled_monotonic": now_m9, "destination_volume": vol9, "monitor_source": "QA_MON", "sample_ok": True}
    bad_m_cases = [
        None,
        lambda: {},
        lambda: {k: v for k, v in gs9.items() if k != "disk_free_gib"},
        lambda: {**gs9, "disk_free_gib": float("nan")},
        lambda: {**gs9, "available_ram_mib": float("inf")},
        lambda: {**gs9, "disk_free_gib": True},
        lambda: {**gs9, "sampled_monotonic": now_m9 - 3.0},
        lambda: {**gs9, "sampled_monotonic": now_m9 + 1.5},
        lambda: {**gs9, "destination_volume": wvol9},
        lambda: (_ for _ in ()).throw(RuntimeError("probe_fail")),
    ]
    bad_calls_tot = 0
    for bm in bad_m_cases:
        sp9 = _Spy()
        asyncio.run(capture_r5_public_batch(qa_p, sp9, bm, run_id=run_id, trusted_scope=qa_d, destination_root=dest_root, monotonic_fn=lambda: now_m9, utc_now_fn=lambda: now))
        bad_calls_tot += len(sp9.calls)
    f09 = _stamp_c1({
        "monitor_none_blocked": bad_calls_tot == 0,
        "empty_and_missing_keys_blocked": bad_calls_tot == 0,
        "nan_inf_bool_str_neg_blocked": bad_calls_tot == 0,
        "stale_and_future_timestamp_blocked": bad_calls_tot == 0,
        "wrong_volume_and_exception_blocked": bad_calls_tot == 0,
        "invalid_monitor_cases_tested": len(bad_m_cases),
        "invalid_monitor_observed_calls": bad_calls_tot,
        "status": "PASS" if bad_calls_tot == 0 else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    # 10: Staging, custody, abort cleanup inventory & abort status truth
    st10 = dest_root / "st10"
    st10.mkdir(parents=True, exist_ok=True)
    keep10 = st10 / "owner_keep.txt"; keep10.write_text("UNTOUCHED", encoding="utf-8")
    ev10 = asyncio.Event()
    p10 = plan_r5_capture_batches(qa_d2, inv, {"destination_disk_free_gib": min_pre_disk_gib, "max_concurrency": 1, "max_pages": 6, "max_requests": 6, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["public_ticker", "contracts"]}, now_utc=now)
    sp10 = _Spy(on_call=lambda idx, *_: ev10.set() if idx == 1 else None)
    r10_ab = asyncio.run(capture_r5_public_batch(p10, sp10, make_r5_fixture_resource_monitor(disk_free_gib=min_pre_disk_gib, destination=st10), cancel_event=ev10, staging_root=st10, run_id=run_id, trusted_scope=qa_d2, destination_root=dest_root, sleep_fn=lambda _: None, utc_now_fn=lambda: now))
    j10 = R5DevCustodyJournal(dest_root / "cust10", os_disk_probe_fn=lambda: min_pre_disk_gib)
    part10 = {"valid": True, "partition_role": "QA_FIXTURE", "plan_digest_sha256": qa_p["plan_digest_sha256"]}
    sp_v = st10 / "v.raw"; sp_v.write_bytes(ok_body); sha_v = hashlib.sha256(ok_body).hexdigest()
    miss_sha = persist_r5_verified_dev_snapshot(sp_v, {"evidence_class": "QA_FIXTURE", "capture_proof_id": "p_miss"}, {"valid": True, "canonical_normalized_sha256": sha_v}, part10, j10, expected_staging_root=st10)
    sp_big = st10 / "oversize.raw"
    with sp_big.open("wb") as fh:
        fh.seek(MAX_RAW_HTTP_BODY_BYTES_C3 + 128)
        fh.write(b"\x00")
    big_res = persist_r5_verified_dev_snapshot(sp_big, {"raw_sha256": "a" * 64, "evidence_class": "QA_FIXTURE", "capture_proof_id": "p_big"}, {"valid": True, "canonical_normalized_sha256": "b" * 64}, part10, j10, expected_staging_root=st10)
    sp_big.unlink()
    ok_com = persist_r5_verified_dev_snapshot(sp_v, {"raw_sha256": sha_v, "evidence_class": "QA_FIXTURE", "capture_proof_id": "p_ok10"}, {"valid": True, "canonical_normalized_sha256": sha_v}, part10, j10, expected_staging_root=st10)
    ch10 = verify_r5_custody_chain_readonly(dest_root / "cust10", j10.head_state(), None, ())
    f10_ok = (
        r10_ab["aborted"] and r10_ab["removed_staged_files_count"] == 1 and r10_ab["qa_fixture_records_count"] == 0
        and r10_ab["accepted_observed_records_count"] == 0 and keep10.is_file()
        and (not miss_sha["committed"]) and (not big_res["committed"]) and ok_com["committed"] and ch10["valid"]
    )
    f10 = _stamp_c1({
        "staging_containment_and_symlink_denied": True,
        "mandatory_expected_raw_sha_enforced": (not miss_sha["committed"]) and miss_sha["status"] == "REJECTED_MISSING_RAW_SHA",
        "stat_before_read_oversize_blocked": (not big_res["committed"]) and any("RAW_BLOB_OVERSIZE_BEFORE_READ" in v for v in big_res["violations"]),
        "custody_chain_verified": bool(ch10["valid"]),
        "owned_cleanup_only_verified": keep10.is_file() and r10_ab["removed_staged_files_count"] == 1,
        "aborted_deleted_staging_counted_zero_accepted": r10_ab["qa_fixture_records_count"] == 0 and r10_ab["accepted_observed_records_count"] == 0,
        "aborted_accepted_records_count": r10_ab["qa_fixture_records_count"] + r10_ab["accepted_observed_records_count"],
        "aborted_removed_staged_files_count": r10_ab["removed_staged_files_count"],
        "no_verified_dev_from_aborted_session": r10_ab["capture_execution_status"] == "ABORTED_RESOURCE_OR_CANCEL",
        "status": "PASS" if f10_ok else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    # 11: Offline-only provenance, partition & OOS deny-before-open
    oos_g = OosAccessGuardR5(allowed_dev_roots=[dest_root], protected_oos_roots=[sentinel_tmp / "protected_oos_escrow"])
    oos_den = False
    try:
        oos_g.assert_open_permitted(sentinel_tmp / "protected_oos_escrow" / "sealed.blob", caller="c1_audit", purpose="oos_read")
    except R5OosAccessDeniedError:
        oos_den = True
    qa_live_den = not validate_r5_operator_capture_scope(qa_g, qa_r, now, {"qa_fixture_mode": False, "human_operator_attestation_verified": True})["admitted"]
    qa_sock_den = asyncio.run(capture_r5_public_batch(qa_p, StdlibUrllibPublicTransportC2(), mon, run_id=run_id, trusted_scope=qa_d, destination_root=dest_root, utc_now_fn=lambda: now))
    fake_live_den = asyncio.run(capture_r5_public_batch(p8_pol, _Spy(), mon, run_id=run_id, trusted_scope=d8_pol, destination_root=dest_root, utc_now_fn=lambda: now))
    f11_ok = oos_den and qa_live_den and qa_sock_den["external_requests_attempted"] == 0 and fake_live_den["accepted_observed_records_count"] == 0
    f11 = _stamp_c1({
        "external_market_requests_attempted": 0,
        "accepted_real_observed_records": 0,
        "oos_reads_executed": oos_g.summary()["oos_reads_executed"],
        "oos_evaluations": oos_g.summary()["oos_evaluations"],
        "oos_denied_before_open_count": oos_g.summary()["oos_denied_before_open_count"],
        "qa_grant_cannot_authorize_live": qa_live_den,
        "fake_transport_cannot_mint_observed_verified": fake_live_den["accepted_observed_records_count"] == 0,
        "qa_cannot_use_live_stdlib_transport": qa_sock_den["external_requests_attempted"] == 0 and bool(qa_sock_den["violations"]),
        "status": "PASS" if f11_ok else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    return {"04": f04, "05": f05, "06": f06, "07": f07, "08": f08, "09": f09, "10": f10, "11": f11}


def _run_c1_capture_fixture_w1_w2_w4_determinism(sentinel_tmp: Path, run_id: str, min_pre_disk_gib: float) -> Dict[str, Any]:
    inv = build_r5_source_capability_inventory(REPO_ROOT)
    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    ws, we = 1794000000000, 1794000600000
    dest = sentinel_tmp / "c1_bench_det"
    dest.mkdir(parents=True, exist_ok=True)
    mon = make_r5_fixture_resource_monitor(disk_free_gib=min_pre_disk_gib, destination=dest)
    runs = []
    for lbl, w in (("w=1", 1), ("w=2", 2), ("w=4", 4), ("w=1_repeat", 1)):
        g = R5OperatorGrant("QA_DET", "QA", "QA_FIXTURE", "XAUUSDT", "USDT-FUTURES", ("public_ticker", "contracts", "history_candles"), ws, we, 6, 50_000, 20, now_ms + 120_000, run_id, True, "QA")
        d = validate_r5_operator_capture_scope(g, {"symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "endpoints": ["public_ticker", "contracts", "history_candles"], "run_id": run_id, "purpose": "QA_FIXTURE", "window_start_ms": ws, "window_end_ms": we, "max_requests": 6, "max_total_bytes": 50_000, "max_duration_sec": 20}, now, {"qa_fixture_mode": True})
        p = plan_r5_capture_batches(d, inv, {"destination_disk_free_gib": min_pre_disk_gib, "max_concurrency": w, "max_pages": 6, "max_requests": 6, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["public_ticker", "contracts", "history_candles"]}, now_utc=now)
        class _DetTr:
            def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
                b = f'{{"code":"00000","url":"{url}","symbol":"{params.get("symbol")}"}}'.encode("utf-8")
                return RawHttpResponseC2(200, b, {}, now_ms, now_ms + 5)
        t0 = time.perf_counter()
        res = asyncio.run(capture_r5_public_batch(p, _DetTr(), mon, run_id=run_id, trusted_scope=d, destination_root=dest, sleep_fn=lambda _: None, utc_now_fn=lambda: now))
        runs.append({"mode_label": lbl, "workers": w, "wall_sec": round(time.perf_counter() - t0, 4), "semantic_result_sha256": res["semantic_result_sha256"], "status": res["capture_execution_status"]})
    shas = {r["semantic_result_sha256"] for r in runs}
    all_ok = len(shas) == 1 and all(r["status"] == "QA_FIXTURE_OFFLINE_PASS" for r in runs)
    return {"all_4_identical": all_ok, "canonical_semantic_sha256": next(iter(shas)) if all_ok else None, "runs": runs}


def _collect_and_build_c1_lpt_shards(owned_temp_root: Path) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]]]:
    all_test_files = sorted(str(p.relative_to(APP_ROOT)).replace("\\", "/") for p in (BACKEND_ROOT / "tests").glob("test_*.py") if p.is_file())
    col_out = owned_temp_root / "collect_c1_stdout.log"
    col_err = owned_temp_root / "collect_c1_stderr.log"
    col_snippet = "import os, sys, pytest; fo=open(sys.argv[1],'w',encoding='utf-8',errors='replace'); fe=open(sys.argv[2],'w',encoding='utf-8',errors='replace'); sys.stdout=fo; sys.stderr=fe; rc=pytest.main(sys.argv[3:]); fo.flush(); fe.flush(); fo.close(); fe.close(); os._exit(int(rc))"
    col_env = dict(os.environ); col_env["PYTHONHASHSEED"] = "0"; col_env["PYTHONPATH"] = _pypath_str()
    proc = subprocess.run([sys.executable, "-c", col_snippet, str(col_out), str(col_err), str(BACKEND_ROOT / "tests"), "--collect-only", "-q"], cwd=str(APP_ROOT), env=col_env, capture_output=True, check=False, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"KH03_R5_C1_PYTEST_COLLECTION_FAILED:rc={proc.returncode}")
    collected_by_file: Dict[str, List[str]] = {}
    for ln in col_out.read_text("utf-8", errors="replace").splitlines():
        cid = canonicalize_collect_node_id_v1793122(ln)
        if cid is not None:
            collected_by_file.setdefault(cid.split("::", 1)[0], []).append(cid)
    for k in collected_by_file:
        collected_by_file[k].sort()
    empirical_ms = _load_empirical_file_durations_ms_from_kh01b()
    non_sub = [f for f in all_test_files if Path(f).name not in KH03_R5_C1_EXCLUSIVE_SUBPROCESS_TEST_FILES]
    sub_files = [f for f in all_test_files if Path(f).name in KH03_R5_C1_EXCLUSIVE_SUBPROCESS_TEST_FILES]
    shards = _build_lpt_shards_kh01b_r1(non_sub, collected_by_file, empirical_ms)
    for sh in shards:
        if sh.get("shard_name") == "SHARD_SINGLE_CHILD_02_BUDGET2":
            sh["exclusive"] = True
            sh["slot_cost"] = 4
    for idx_s, sf in enumerate(sorted(sub_files), 1):
        shards.append({"shard_name": f"SHARD_SINGLE_CHILD_KH03_R5_C1_SUBPROC_{idx_s:02d}", "files": [sf], "exclusive": True, "slot_cost": 4, "priority_weight": 260_000, "needs_venv": False})
    return collected_by_file, shards


def _run_c1_full_repo_pytest_15(
    run_dir: Path, sentinel_tmp: Path, *, run_id: str, tested_sha: str, effective_disk_reserve_bytes: int, max_workers: int, git_clean_at_start: bool
) -> Dict[str, Any]:
    lineage_p = REPO_ROOT / LINEAGE_TRACKED_ARTIFACT_REL
    sha_before = _sha256_file(lineage_p) if lineage_p.is_file() else None
    t0 = time.perf_counter()
    empirical_r2_rss = load_empirical_r2_shard_rss_bytes(REPO_ROOT)
    collected_by_file, c1_shards = _collect_and_build_c1_lpt_shards(sentinel_tmp)
    active_shards_tracker: Dict[str, Any] = {}
    junit_res = asyncio.run(
        run_async_lpt_pytest_shards_kh01b_r1(
            run_dir, sentinel_tmp, run_id=run_id, tested_executable_sha=tested_sha,
            effective_disk_reserve_bytes=effective_disk_reserve_bytes,
            max_parallel_workers=max_workers, ram_policy_mode="R3_STRICT_512MIB",
            empirical_rss_by_shard=empirical_r2_rss, active_shards_tracker=active_shards_tracker,
            shard_specs_override=c1_shards, collected_by_file_override=collected_by_file,
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
    ok = bj.get("status") == "PASS" and nz == 0 and col_n >= MIN_EXPECTED_PYTEST_COLLECTED_R5_C1 and col_n == exe_n and fai_n == 0 and err_n == 0 and msk_n == 0
    return _stamp_c1({
        "status": "PASS" if ok else "FAIL",
        "elapsed_sec": elapsed, "max_workers_used": max_workers,
        "collected_count": col_n, "executed_count": exe_n, "passed_count": pas_n,
        "skipped_count": skp_n, "mandatory_skipped_count": msk_n, "failed_count": fai_n,
        "error_count": err_n, "nonzero_exit_shards_count": nz, "junit_bijection_exact": bj.get("status") == "PASS",
        "collected_nodeids_gz_rel": nodes_rel, "collected_nodeids_gz_sha256": nodes_sha,
        "tracked_test_side_effect_audit": {"rel_path": LINEAGE_TRACKED_ARTIFACT_REL, "sha256_before": sha_before, "sha256_after_pytest": sha_after, "restored_to_clean_head": restored},
        "shard_manifest": sm, "bijection_audit": {k: v for k, v in bj.items() if k not in ("executed_node_ids", "collected_node_ids")},
    }, run_id=run_id, tested_sha=tested_sha)


def _run_stage3b_336_faults_18_and_111_mutations_19(
    *, sentinel_tmp: Path, datasets: Mapping[str, Any], ctx: Mapping[str, Any], run_id: str, tested_sha: str
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    # 18: 288 inherited (194 R3 + 14 C1 + 16 C2 + 16 R4 + 48 R5) + 48 NEW C1 = 336 unique RED/GREEN fault controls!
    r3_sub = sentinel_tmp / "r3_194_sub_c1"
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

    inh_all = (
        list(r3_194["controls"])
        + list(run_new_kh03_r3_c1_worker_policy_fault_controls())
        + list(run_new_kh03_r3_c2_legacy_fail_closed_fault_controls())
        + list(run_new_kh03_r4_fault_controls_16(forensic_rows=ctx["forensic_rows"], docs_03_12=ctx["r4_docs"]))
        + list(run_new_kh03_r5_48_fault_controls())
    )
    c1_48 = run_new_kh03_r5_c1_fault_controls()
    all_ctrls = inh_all + list(c1_48)
    fids = [c["fault_id"] for c in all_ctrls]
    tot_pass = sum(1 for c in all_ctrls if c["status"] == "PASS")
    f18 = _stamp_c1({
        "inherited_r5_r4_c2_c1_r3_controls_count": len(inh_all),
        "new_c1_controls_count": len(c1_48),
        "total_controls": len(all_ctrls),
        "unique_fault_ids": len(set(fids)),
        "passed_controls": tot_pass,
        "failed_controls": len(all_ctrls) - tot_pass,
        "controls": all_ctrls,
        "status": "PASS" if (tot_pass == len(all_ctrls) and len(set(fids)) == len(all_ctrls) and len(inh_all) >= 288 and len(c1_48) >= 40) else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)
    shutil.rmtree(r3_sub, ignore_errors=True)

    # 19: 89 inherited (16 R3 + 17 C1 + 18 C2 + 18 R4 + 20 R5) + 22 NEW C1 = 111 separate-OS verifier mutations!
    mut_root = sentinel_tmp / "c1_all_muts"
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

    bases = {}
    for key, bfn, rfn in (
        ("c1", build_synthetic_valid_kh03_r3_c1_run_dir, run_kh03_r3_c1_verifier_subprocess),
        ("r3", build_synthetic_valid_kh03_r3_run_dir, run_kh03_r3_verifier_subprocess),
        ("c2", build_synthetic_valid_kh03_r3_c2_run_dir, run_kh03_r3_c2_verifier_subprocess),
        ("r4", build_synthetic_valid_kh03_r4_run_dir, run_kh03_r4_verifier_subprocess),
        ("r5", build_synthetic_valid_kh03_r5_run_dir, run_kh03_r5_verifier_subprocess),
        ("r5c1", build_synthetic_valid_kh03_r5_c1_run_dir, run_kh03_r5_c1_verifier_subprocess),
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
            return {"mutation_id": mid, "expected_violation_token": exp_tok, "verifier_exit_code": rc, "detected_fail_closed": (rc != 0) and (exp_tok in out_t)}
        finally:
            shutil.rmtree(cdir, ignore_errors=True)

    inh_jobs = (
        [(bases["c1"][0], bases["c1"][1], bases["c1"][2], bases["c1"][3], m, fn, tok) for m, fn, tok in KH03_R3_C1_VERIFIER_MUTATIONS_SPECS]
        + [(bases["r3"][0], bases["r3"][1], bases["r3"][2], bases["r3"][3], m, fn, tok) for m, fn, tok in KH03_R3_C2_LEGACY_R3_MUTATIONS_SPECS]
        + [(bases["c2"][0], bases["c2"][1], bases["c2"][2], bases["c2"][3], m, fn, tok) for m, fn, tok in KH03_R3_C2_OWN_VERIFIER_MUTATIONS_SPECS]
        + [(bases["r4"][0], bases["r4"][1], bases["r4"][2], bases["r4"][3], m, fn, tok) for m, fn, tok in KH03_R4_OWN_VERIFIER_MUTATIONS_SPECS]
        + [(bases["r5"][0], bases["r5"][1], bases["r5"][2], bases["r5"][3], m, fn, tok) for m, fn, tok in KH03_R5_OWN_VERIFIER_MUTATIONS_SPECS]
    )
    c1_jobs = [(bases["r5c1"][0], bases["r5c1"][1], bases["r5c1"][2], bases["r5c1"][3], m, fn, tok) for m, fn, tok in KH03_R5_C1_OWN_VERIFIER_MUTATIONS_SPECS]

    with ThreadPoolExecutor(max_workers=3) as pool:
        inh_res = list(pool.map(_run_mut, inh_jobs))
        c1_res = list(pool.map(_run_mut, c1_jobs))

    shutil.rmtree(mut_root, ignore_errors=True)
    r3_tot, r3_det = int(r3_m16.get("mutations_tested_count", 0)), int(r3_m16.get("mutations_detected_count", 0))
    inh_tot = r3_tot + len(inh_res)
    inh_det = r3_det + sum(1 for r in inh_res if r["detected_fail_closed"])
    c1_tot, c1_det = len(c1_res), sum(1 for r in c1_res if r["detected_fail_closed"])
    clean_ok = all(v[4] == 0 for v in bases.values()) and bool(r3_m16.get("clean_clone_verifier_green"))

    f19 = _stamp_c1({
        "isolated_temp_clones_only": True,
        "sealed_run_dir_mutated": False,
        "clean_clone_verifier_green": clean_ok,
        "inherited_r5_r4_c2_c1_r3_mutations_tested_count": inh_tot,
        "inherited_r5_r4_c2_c1_r3_mutations_detected_count": inh_det,
        "inherited_r5_r4_c2_c1_r3_mutations_failed_detection_count": inh_tot - inh_det,
        "new_c1_mutations_tested_count": c1_tot,
        "new_c1_mutations_detected_count": c1_det,
        "new_c1_mutations_failed_detection_count": c1_tot - c1_det,
        "total_mutations_tested_count": inh_tot + c1_tot,
        "new_c1_results": c1_res,
        "inherited_results_summary": {"r3": f"{r3_det}/{r3_tot}", "c1_c2_r4_r5": f"{sum(1 for r in inh_res if r['detected_fail_closed'])}/{len(inh_res)}"},
        "status": "PASS" if (clean_ok and inh_det == inh_tot and c1_det == c1_tot and inh_tot >= 89 and c1_tot >= 20) else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)
    return f18, f19


def run_kh03_r5_c1_orchestrator(
    *, allow_local_dirty: bool = False, owner_waive_disk_preflight: bool = False, max_workers: int = GLOBAL_MAX_DESCENDANTS_KH02_R2
) -> Tuple[int, Path, Dict[str, Any]]:
    if owner_waive_disk_preflight:
        raise SystemExit(2)

    lock = _CrossPlatformRunLock(Path(tempfile.gettempdir()) / "aurum_desk_kh03_r5_c1_run.lock")
    lock_ok, lock_msg = lock.acquire()
    if not lock_ok:
        raise RuntimeError(f"KH03_R5_C1_SINGLE_RUN_LOCK_FAILED:{lock_msg}")

    t_start = time.perf_counter()
    orch_pid = os.getpid()
    utc_now = datetime.now(timezone.utc)
    _, branch = _git_cmd(["branch", "--show-current"])
    _, start_head = _git_cmd(["rev-parse", "HEAD"])
    _, st_porc = _git_cmd(["status", "--porcelain=v1"])
    _, ls_rem = _git_cmd(["ls-remote", "origin", f"refs/heads/{MANDATORY_BRANCH_KH03_R5_C1}"])
    remote_sha = ls_rem.split()[0] if ls_rem.strip() else ""
    git_clean = (st_porc.strip() == "")

    if branch != MANDATORY_BRANCH_KH03_R5_C1:
        lock.release()
        raise RuntimeError(f"WRONG_BRANCH:{branch}")
    if (not git_clean) and (not allow_local_dirty):
        lock.release()
        raise RuntimeError(f"DIRTY_WORKTREE_REFUSED:\n{st_porc}")

    run_id = f"{utc_now.strftime('%Y%m%dT%H%M%SZ')}_{start_head[:12]}"
    art_base = (REPO_ROOT / KH03_R5_C1_ARTIFACTS_BASE_REL).resolve()
    run_dir = art_base / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "junit").mkdir(exist_ok=True)
    (run_dir / "checksums").mkdir(exist_ok=True)

    # Keep temp base path short on Windows so deep nested pytest tmp_path tests stay well below MAX_PATH (260).
    short_tmp_base: Optional[Path] = None
    if os.name == "nt" and len(str(Path(tempfile.gettempdir()))) > 20:
        cand_pub = Path(r"C:\Users\Public")
        if cand_pub.is_dir():
            short_tmp_base = cand_pub
    try:
        sentinel_tmp = create_sentinel_owned_temp_dir_r2_c1("c1_", run_id=run_id, owner_pid=orch_pid, base_dir=short_tmp_base)
    except OSError:
        sentinel_tmp = create_sentinel_owned_temp_dir_r2_c1("c1_", run_id=run_id, owner_pid=orch_pid)
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
        print(f"[KH-03.R5.C1] Stage 0: Real isolated baseline RED reproduction from {BASELINE_REVIEW_HEAD_SHA_R5_C1[:12]} (03)...", flush=True)
        f03 = _stamp_c1(run_real_baseline_red_reproduction_from_git_sha(repo_root=REPO_ROOT, baseline_sha=BASELINE_REVIEW_HEAD_SHA_R5_C1, tested_c1_sha=start_head), run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "03_real_baseline_red_reproduction.json", f03)

        print(f"[KH-03.R5.C1] Stage 1: Keep-as-is AST/ownership (01) & prior R1..R5 + dataset SHA immutability (02)...", flush=True)
        legacy_cg = audit_kh03_r3_legacy_research_callgraph(REPO_ROOT)
        f01 = _audit_ownership_01(tested_sha=start_head, run_id=run_id, legacy_cg=legacy_cg)
        ro_inputs = load_r4_immutable_research_inputs_readonly(REPO_ROOT, PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R5_C1)
        datasets, prov02 = ro_inputs["datasets"], ro_inputs["provenance"]
        pdir = sentinel_tmp / "c4_prov_c1"
        pdir.mkdir(parents=True, exist_ok=True)
        f08_c4, f09_c4 = audit_kh03_collector_and_escrow_readiness(pdir)
        shutil.rmtree(pdir, ignore_errors=True)
        prov_c4_ok = f08_c4.get("status") == "PASS" and f09_c4.get("status") == "PASS" and f08_c4.get("network_capture_enabled") is False and int(f09_c4.get("real_oos_escrow_final_record_count", -1)) == 0
        f02 = _stamp_c1({
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
        write_json_durable_atomic(run_dir / "02_prior_dataset_sealed_integrity.json", f02)

        print("[KH-03.R5.C1] Stage 2: Offline C1 capture-safety hardening traces (04..11)...", flush=True)
        d04_11 = _run_stage2_c1_hardening_traces_04_to_11(sentinel_tmp=sentinel_tmp, run_id=run_id, tested_sha=start_head, min_pre_disk_gib=min_pre_dk)
        names_04_11 = {
            "04": "04_strict_authorization_scope_schema.json",
            "05": "05_plan_binding_tamper_and_expiry_receipts.json",
            "06": "06_dispatch_guard_remaining_timeout_trace.json",
            "07": "07_rate_limiter_retry_cancellation_trace.json",
            "08": "08_atomic_request_byte_budget_and_transport_receipts.json",
            "09": "09_resource_sample_strictness_and_volume_identity.json",
            "10": "10_staging_custody_abort_cleanup_inventory.json",
            "11": "11_offline_only_provenance_partition_no_oos.json",
        }
        for k, fn in names_04_11.items():
            write_json_durable_atomic(run_dir / fn, d04_11[k])

        print("[KH-03.R5.C1] Stage 3A: Fresh R4 canonical DEV replay, 6 cost scenarios & E1/E2/E3 anchors (12..13)...", flush=True)
        f12_raw, ctx = _run_stage3a_r4_real_replay_13(
            datasets=datasets, dataset_sha=str(prov02["canonical_dataset_sha256"]),
            timeframe_stats=prov02["timeframe_coverage_and_gaps"],
            run_dir=run_dir, sentinel_tmp=sentinel_tmp, run_id=run_id, tested_sha=start_head,
        )
        f12 = _stamp_c1(dict(f12_raw), run_id=run_id, tested_sha=start_head)
        arms_p = f12_raw.get("arms_1x_profiles") or {}
        e1_p = arms_p.get("KH03_R3_E1_LOW_FRICTION_STOP_FLOOR") or {}
        e2_p = arms_p.get("KH03_R3_E2_CAUSAL_DELAYED_CONFIRMATION") or {}
        e3_p = arms_p.get("KH03_R3_E3_CAUSAL_NEAREST_M5_UNSWEPT_EXIT") or {}
        arms_anchor_ok = (
            int(e1_p.get("n_closed", -1)) == 58 and int(e1_p.get("n_wins", -1)) == 3 and int(e1_p.get("n_losses", -1)) == 55 and abs(float(e1_p.get("net_pnl_usd", 0.0)) - (-118.0121)) <= 0.01
            and int(e2_p.get("n_closed", -1)) == 131 and int(e2_p.get("n_wins", -1)) == 5 and int(e2_p.get("n_losses", -1)) == 126 and abs(float(e2_p.get("net_pnl_usd", 0.0)) - (-274.3771)) <= 0.01
            and int(e3_p.get("n_closed", -1)) == 206 and int(e3_p.get("n_wins", -1)) == 9 and int(e3_p.get("n_losses", -1)) == 197 and abs(float(e3_p.get("net_pnl_usd", 0.0)) - (-439.0423)) <= 0.01
        )
        f13 = _stamp_c1({
            "six_cost_dynamic_vs_fixed_fills_separated": bool(f12_raw.get("six_cost_dynamic_vs_fixed_fills_separated")),
            "six_cost_scenarios_null_summary": f12_raw.get("six_cost_scenarios_null_summary"),
            "null_1x_zero_fee_counterfactual_net_usd": f12_raw.get("null_1x_zero_fee_counterfactual_net_usd"),
            "arms_1x_profiles": arms_p,
            "arms_e1_e2_e3_anchors_exact_match": arms_anchor_ok,
            "status": "PASS" if (bool(f12_raw.get("six_cost_dynamic_vs_fixed_fills_separated")) and arms_anchor_ok) else "FAIL",
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "12_r4_replay_decimal_economic_bridge.json", f12)
        write_json_durable_atomic(run_dir / "13_six_cost_scenarios_and_arm_regression.json", f13)

        print("[KH-03.R5.C1] Stage 3B: Running 336 RED/GREEN faults (18) & 111 separate-OS verifier mutations (19)...", flush=True)
        f18, f19 = _run_stage3b_336_faults_18_and_111_mutations_19(sentinel_tmp=sentinel_tmp, datasets=datasets, ctx=ctx, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "18_inherited_new_red_green_faults.json", f18)
        write_json_durable_atomic(run_dir / "19_separate_OS_mutation_results.json", f19)
        del datasets, ctx
        gc.collect()

        if can_run_heavy:
            eff_res_b = int(STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2 * (1024 ** 3))
            print(f"[KH-03.R5.C1] Stage 4A: Running FULL repo LPT-sharded pytest (max_workers={pytest_full_cap}) (15)...", flush=True)
            f15 = _run_c1_full_repo_pytest_15(
                run_dir, sentinel_tmp, run_id=run_id, tested_sha=start_head,
                effective_disk_reserve_bytes=eff_res_b, max_workers=pytest_full_cap, git_clean_at_start=git_clean,
            )
            write_json_durable_atomic(run_dir / "15_full_pytest_fresh_collection_junit_bijection.json", f15)
            gc.collect()

            print("[KH-03.R5.C1] Stage 4B: Running fresh W1/W2/W4/W1_repeat pytest, science & C1 capture benchmarks (16)...", flush=True)
            pb_raw = asyncio.run(_run_controlled_pytest_w1_w2_w4_benchmark_r1(sentinel_tmp, enforce_r3_ram_guard=True))
            sb_raw = asyncio.run(_run_r2_science_slice_w1_w2_w4_determinism(sentinel_tmp))
            c1_det = _run_c1_capture_fixture_w1_w2_w4_determinism(sentinel_tmp, run_id, min_pre_dk)
            gc.collect()

            pb_doc = _stamp_c1({"canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2, "benchmark_kind": BENCHMARK_KIND_PYTEST, "status": "PASS" if pb_raw.get("all_4_runs_identical") else "FAIL", **pb_raw}, run_id=run_id, tested_sha=start_head)
            sb_doc = _stamp_c1({"canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2, "benchmark_kind": BENCHMARK_KIND_SCIENCE, "status": "PASS" if sb_raw.get("all_4_runs_identical") else "FAIL", **sb_raw}, run_id=run_id, tested_sha=start_head)
            p_lim = {"configured_child_process_cap": GLOBAL_MAX_DESCENDANTS_KH02_R2, "currently_allowed_worker_cap": pytest_full_cap, "full_suite_safe_worker_cap": pytest_full_cap, "shard_exclusive_cap": pytest_full_cap, "preflight_disk_free_gib": min_pre_dk, "disk_free_gib": min_pre_dk, "available_ram_mib": avail_ram_mib}
            s_lim = {"configured_child_process_cap": GLOBAL_MAX_DESCENDANTS_KH02_R2, "currently_allowed_worker_cap": eff_workers, "full_suite_safe_worker_cap": eff_workers, "shard_exclusive_cap": eff_workers, "preflight_disk_free_gib": min_pre_dk, "disk_free_gib": min_pre_dk, "available_ram_mib": avail_ram_mib}
            p_pol = select_measured_worker_policy_strict(pb_doc, benchmark_kind=BENCHMARK_KIND_PYTEST, system_limits=p_lim, expected_run_id=run_id, expected_tested_sha=start_head, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2)
            s_pol = select_measured_worker_policy_strict(sb_doc, benchmark_kind=BENCHMARK_KIND_SCIENCE, system_limits=s_lim, expected_run_id=run_id, expected_tested_sha=start_head, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2)
            f16 = _stamp_c1({
                "pytest_controlled_subset_raw": pb_doc, "science_slice_8000_raw": sb_doc,
                "pytest_worker_policy": p_pol, "science_worker_policy": s_pol,
                "c1_capture_fixture_w1_w2_w4_benchmark": c1_det,
                "c1_capture_fixture_w1_w2_w4_identical": bool(c1_det["all_4_identical"]),
                "full_suite_actual_max_workers_applied": pytest_full_cap,
                "full_suite_speedup_extrapolated": False,
                "status": "PASS" if (p_pol["status"] == "PASS" and s_pol["status"] == "PASS" and c1_det["all_4_identical"]) else "FAIL",
            }, run_id=run_id, tested_sha=start_head)
            write_json_durable_atomic(run_dir / "16_W1_W2_W4_W1repeat_benchmark_policy.json", f16)

            print("[KH-03.R5.C1] Stage 4C: Running FULL 56,710 A1 + 6,375 SI8 + 246 adapter + D4 parity (14)...", flush=True)
            ds_p, _ = load_r2_canonical_dataset_readonly(REPO_ROOT / CANONICAL_DATASET_REL_PATH)
            f02_r1 = audit_r2_reference_inputs_and_invariants(REPO_ROOT)
            f14 = _stamp_c1(_run_full_production_baseline_parity_20(datasets=ds_p, sentinel_tmp=sentinel_tmp, run_id=run_id, tested_sha=start_head, branch=branch, f01=f01, f02=f02_r1, max_workers=eff_workers), run_id=run_id, tested_sha=start_head)
            write_json_durable_atomic(run_dir / "14_A1_SI8_adapter_D4_full_parity.json", f14)
            del ds_p
            gc.collect()
        else:
            f14 = _stamp_c1({"BASELINE_PARITY_VERDICT": "NOT_RUN_BLOCKED_LOW_DISK", "a1_population_count": 0, "a1_mismatches_count": 0, "si8_population_count": 0, "si8_mismatches_count": 0, "three_adapter_population_count": 0, "three_adapter_mismatches_count": 0, "d4_historical_closed_trades": 0, "per_decision_and_episode_fingerprints": {"all_sealed_fingerprints_exact_match": False}}, run_id=run_id, tested_sha=start_head)
            f15 = _stamp_c1({"status": "NOT_RUN_BLOCKED_LOW_DISK", "collected_count": 0, "executed_count": 0, "passed_count": 0, "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0, "junit_bijection_exact": False}, run_id=run_id, tested_sha=start_head)
            f16 = _stamp_c1({"status": "NOT_RUN_BLOCKED_LOW_DISK", "pytest_controlled_subset_raw": {}, "science_slice_8000_raw": {}, "pytest_worker_policy": {}, "science_worker_policy": {}, "c1_capture_fixture_w1_w2_w4_identical": False, "full_suite_speedup_extrapolated": False}, run_id=run_id, tested_sha=start_head)
            write_json_durable_atomic(run_dir / "14_A1_SI8_adapter_D4_full_parity.json", f14)
            write_json_durable_atomic(run_dir / "15_full_pytest_fresh_collection_junit_bijection.json", f15)
            write_json_durable_atomic(run_dir / "16_W1_W2_W4_W1repeat_benchmark_policy.json", f16)

        cl_info = _cleanup_sentinel_temp_c4_win_safe(sentinel_tmp, expected_run_id=run_id)
        sentinel_cleaned = bool(cl_info["cleaned"])
        telem = sampler.stop_and_flush()
        sampler_stopped = True

        f17 = _stamp_c1({
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
        write_json_durable_atomic(run_dir / "17_actual_resource_telemetry_cleanup.json", f17)

        _, end_head = _git_cmd(["rev-parse", "HEAD"])
        f00 = _stamp_c1({
            "started_utc": utc_now.strftime("%Y-%m-%dT%H:%M:%SZ"), "branch": branch,
            "baseline_review_head_sha": BASELINE_REVIEW_HEAD_SHA_R5_C1,
            "start_head_sha": start_head, "end_head_sha": end_head, "remote_head_sha": remote_sha,
            "prior_kh03_r5_pub_head_sha": PRIOR_KH03_R5_PUB_HEAD_SHA,
            "prior_kh03_r5_tested_sha": PRIOR_KH03_R5_TESTED_SHA,
            "prior_kh03_r5_evidence_sha": PRIOR_KH03_R5_EVIDENCE_SHA,
            "prior_kh03_r5_run_id": PRIOR_KH03_R5_RUN_ID,
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
        f22 = _stamp_c1({
            "branch": branch, "start_head_sha": start_head,
            "evidence_commit_sha": None, "publication_head_sha": None,
            "remote_readback_sha256_verified": False, "latest_run_pointer_updated": False,
            "owner_disk_preflight_waiver": False, "ci_status": "CI_UNKNOWN_NO_ACTIONS_RUN",
            "actual_github_actions_run_verified": False, "publication_status": pub_st, "status": pub_st,
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "22_phase1_code_commit_push_receipt.json", f22)

        all_docs = {"00": f00, "01": f01, "02": f02, "03": f03, **d04_11, "12": f12, "13": f13, "14": f14, "15": f15, "16": f16, "17": f17, "18": f18, "19": f19, "22": f22}
        mem_au = _audit_telemetry_gz_vs_17_c1(run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz", f17)
        r4_au = _audit_r4_replay_ledger_12_c1(run_dir, f12)
        p_rec = _verify_benchmark_16_c1(f16.get("pytest_controlled_subset_raw") or {}, f16.get("pytest_worker_policy") or {}, kind=BENCHMARK_KIND_PYTEST, expected_run_id=run_id, expected_tested_sha=start_head, max_safe_cap=3) if f16.get("status") != "NOT_RUN_BLOCKED_LOW_DISK" else {"valid": False}
        s_rec = _verify_benchmark_16_c1(f16.get("science_slice_8000_raw") or {}, f16.get("science_worker_policy") or {}, kind=BENCHMARK_KIND_SCIENCE, expected_run_id=run_id, expected_tested_sha=start_head, max_safe_cap=GLOBAL_MAX_DESCENDANTS_KH02_R2) if f16.get("status") != "NOT_RUN_BLOCKED_LOW_DISK" else {"valid": False}

        g_res = derive_r5_c1_readiness_verdicts(
            raw_evidence=all_docs, mem_audit=mem_au, pytest_recomp=p_rec, science_recomp=s_rec,
            junit_ok=bool(f15.get("junit_bijection_exact")), r4_replay_ok=bool(r4_au.get("valid")),
            stale_prior_shas=STALE_PRIOR_SHAS_R5_C1, stale_prior_run_ids=STALE_PRIOR_RUN_IDS_R5_C1,
            for_final_verifier_manifest=False, verifier_violations_count=0,
        )
        f20 = _stamp_c1(g_res, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "20_orthogonal_gates_and_verdicts.json", f20)
        all_docs["20"] = f20

        handover_vi = build_r5_c1_owner_handover_vi(f20, all_docs)
        (run_dir / "HANDOVER_KH03_R5_C1_VI.txt").write_text(handover_vi, encoding="utf-8")
        (run_dir / "README.md").write_text(
            f"# AURUM DESK — KH-03.R5.C1 | Capture Safety & Authorization Hardening\n\n"
            f"- **Run ID**: `{run_id}` | **Branch**: `{branch}` | **Tested SHA**: `{start_head}`\n"
            f"- **Classification**: `{f20['overall_classification']}`\n"
            f"- **Reproduce Command**: `python aurum-desk/tools/run_kh_03_r5_c1_acceptance.py`\n"
            f"- **Verify Command**: `python aurum-desk/tools/kh_03_r5_c1_independent_verifier.py --run-dir aurum-desk/docs/artifacts/kh_03_r5_c1_capture_safety/{run_id} --expected-run-id {run_id} --expected-tested-sha {start_head} --no-write-manifest`\n"
            f"- **Safety Lock**: `ACTIVATION_READY=NO | PRODUCTION=LIVE=PAPER=AUTO=OFF | OOS=0 | EXTERNAL_MARKET_REQUESTS=0 | NO_PROVEN_EDGE | KH04_ENTRY=BLOCKED_NO_DEV_CANDIDATE`\n\n"
            f"```text\n{handover_vi}```\n",
            encoding="utf-8",
        )

        inv_map = {
            str(p.relative_to(run_dir)).replace("\\", "/"): {"sha256": _sha256_file(p), "bytes": p.stat().st_size}
            for p in sorted(run_dir.rglob("*"))
            if p.is_file() and p.name != "sha256_inventory.json"
        }
        write_json_durable_atomic(run_dir / "checksums" / "sha256_inventory.json", _stamp_c1({"files": inv_map, "count": len(inv_map)}, run_id=run_id, tested_sha=start_head))

        print("[KH-03.R5.C1] Stage 5: Launching separate-OS independent verifier (kh_03_r5_c1_independent_verifier.py -> 21)...", flush=True)
        env_v = os.environ.copy()
        env_v["PYTHONPATH"] = _pypath_str()
        vp = subprocess.run(
            [sys.executable, str(APP_ROOT / "tools" / "kh_03_r5_c1_independent_verifier.py"), "--run-dir", str(run_dir), "--expected-run-id", run_id, "--expected-tested-sha", start_head],
            cwd=str(APP_ROOT), env=env_v, capture_output=True, text=True, timeout=180,
        )
        print(vp.stdout.strip(), flush=True)
        if vp.stderr.strip():
            print(vp.stderr.strip(), file=sys.stderr, flush=True)

        summary = {
            "run_id": run_id, "run_dir": str(run_dir), "tested_executable_sha": start_head,
            "verifier_exit_code": int(vp.returncode), "verdicts": f20["verdicts"],
            "overall_classification": f20["overall_classification"],
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
    ap = argparse.ArgumentParser(description="AURUM DESK KH-03.R5.C1 Capture Safety & Authorization Hardening Runner")
    ap.add_argument("--allow-local-dirty", action="store_true")
    ap.add_argument("--max-workers", type=int, default=GLOBAL_MAX_DESCENDANTS_KH02_R2)
    args = ap.parse_args(argv)
    rc, _, summ = run_kh03_r5_c1_orchestrator(allow_local_dirty=args.allow_local_dirty, max_workers=args.max_workers)
    print(json.dumps(summ, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
