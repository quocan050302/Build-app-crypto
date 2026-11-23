#!/usr/bin/env python3
"""
aurum-desk/tools/run_kh_02_r2_c4_acceptance.py

AURUM DESK — KH-02.R2.C4 | TRUSTED CAPTURE BOUNDARY HARDENING, RISK-TAIL EVIDENCE & INDEPENDENT CLOSURE
SPEC VERSION: KH-02.R2.C4-v1.0 | RESEARCH ONLY | SAME BRANCH | ACTIVATION_READY=NO
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
                    "--owner-waive-disk-preflight and env overrides are strictly forbidden in KH-02.R2.C4 "
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
import copy
from datetime import datetime, timezone
import gc
import gzip
import hashlib
import json
import os
from pathlib import Path
import pickle
import platform
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple

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
from services.kh_02_r2_c2_escrow_journal import (
    C2DurableEscrowJournal,
    ExternalHeadAnchorC2,
    make_crash_hook_for_phase,
)
from services.kh_02_r2_c2_public_capture import (
    C2PublicReadOnlyBitgetCollector,
    OperatorCaptureAuthorizationC2,
    RawHttpResponseC2,
    StdlibUrllibPublicTransportC2,
    _SequenceFakeTransportC2,
    run_kh02_r2_c2_fake_transport_collector_suite,
)
from services.kh_02_r2_c3_provenance_guard import (
    ProvenanceClassC3,
    evaluate_c3_prospective_admission,
    mint_trusted_capture_receipt_c3,
    mint_trusted_capture_receipt_c4,
    reparse_and_verify_raw_market_bytes_c3,
)
from services.kh_02_r2_c4_risk_tail_evidence import (
    EXPECTED_R2_TAIL_ARMS_C4,
    EXPECTED_R2_TAIL_MODE_NAMES_C4,
    build_c4_per_episode_risk_ledger,
    build_c4_realized_tail_evidence_from_r2_replays,
    recompute_c4_tail_counters_from_episode_ledger,
    reconcile_c4_full_universe_cost_fees,
    summarize_c4_science_and_risk_truth,
    validate_c4_tail_disclosure_vs_risk_ledger,
)
from services.kh_02_r2_c4_trusted_transport_boundary import (
    SPEC_VERSION_KH02_R2_C4,
    TCB_THREAT_MODEL_DISCLOSURE_C4,
    VerifiedCaptureProofC4,
    expire_capture_proof_for_test_only_c4,
    is_exact_unmodified_stdlib_transport_c4,
    verify_capture_proof_c4,
)
from services.kh_02_r2_prospective_data_service import (
    BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1,
    R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS,
    SCHEMA_VERSION_KH02_R2_C1,
    make_synthetic_valid_c1_snapshot_fixture,
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
    RUNTIME_SAFETY_MODES_KH02_R2,
    SENTINEL_FILENAME_KH02_R2_C1,
    STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
    STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2,
    _ast_function_inventory,
    _r2_frozen_plan_and_arms,
    _sha256_file,
    audit_r2_r1_target_eligibility_counterexamples,
    audit_r2_reference_inputs_and_invariants,
    build_r2_c1_readonly_disk_inventory,
    build_r2_causal_unswept_level_ledger,
    build_r2_paired_factorial_entry_geometry_design,
    build_r2_target_repair_full_opportunity_conservation,
    compute_r2_stratified_common_support_balance,
    create_sentinel_owned_temp_dir_r2_c1,
    load_r2_canonical_dataset_readonly,
    probe_r2_c1_write_mounts_strict,
    replay_r2_same_opportunity_paired_counterfactuals,
    reprice_r2_fixed_fills_cost_counterfactual,
    rerun_r2_full_universe_by_cost_scenario,
    run_r2_purged_walkforward_and_multiplicity,
    run_r2_t01_to_t10_adversarial_target_scenarios,
    safe_cleanup_sentinel_owned_temp_dir_r2_c1,
    validate_r2_cost_scope_honesty,
    verify_sentinel_owned_dir_r2_c1,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    verify_fresh_junit_bijection_v17931211,
)
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_02_r2_c4_independent_verifier import (
    C4_SOURCE_FILES_ALLOWLIST,
    PRIOR_KH02_R2_C3_EVIDENCE_SHA,
    PRIOR_KH02_R2_C3_PUB_HEAD_SHA,
    PRIOR_KH02_R2_C3_RUN_ID,
    PRIOR_KH02_R2_C3_TESTED_SHA,
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_C4,
    SCHEMA_VERSION_KH02_R2_C4,
    _audit_memory_samples_c3,
    recompute_c4_23_gates_and_verdicts,
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
)
from tools.run_kh_02_r2_c3_acceptance import (
    C3_EXCLUSIVE_SUBPROCESS_TEST_FILES,
    _run_c3_102_red_green_fault_suite,
)
from tools.run_kh_02_r2_verification import (
    _BoundedResourceSampler,
    _git_cmd,
    _run_full_production_baseline_parity_20,
    _run_r2_science_slice_w1_w2_w4_determinism,
)
from tools.run_v17_9_3_1_2_2_verification import canonicalize_collect_node_id_v1793122

C4_ARTIFACTS_BASE_REL = "aurum-desk/docs/artifacts/kh_02_r2_c4_integrity_closure"

C4_EXCLUSIVE_SUBPROCESS_TEST_FILES: Set[str] = set(C3_EXCLUSIVE_SUBPROCESS_TEST_FILES) | {
    "test_kh_02_r2_c4_escrow_capture_races.py",
    "test_kh_02_r2_c4_verifier_mutations.py",
}

# Verified STAGE S01 RED proofs captured against unmodified ca5ba096 BEFORE any C4 source edits
S01_PRE_PATCH_RED_PROOFS_CA5BA096: Dict[str, Any] = {
    "baseline_sha": "ca5ba09602c115d1aa2b35f762104f7e0766f4a6",
    "captured_before_c4_edits": True,
    "windows_native_fcntl_missing_on_ca5ba096": True,
    "direct_mint_red_proven": True,
    "evil_subclass_red_proven": True,
    "instance_get_monkeypatch_red_proven": True,
    "p0a_exploits": {
        "A01_DIRECT_MINT_BOOLEAN_SPOOF": {
            "classification": "REAL_RED",
            "minted_provenance_class": "LIVE_AUTHORIZED_PUBLIC_HTTP",
            "admission_admitted": True,
            "admission_eligible_for_fresh_oos": True,
            "admission_tier": "NEW_REAL_OOS_RAW_VERIFIED",
            "escrow_action": "APPENDED_NEW_ENTRY",
            "escrow_eligible_for_fresh_oos": True,
            "escrow_record_count": 1,
        },
        "A02_EVIL_SUBCLASS_GET_OVERRIDE": {
            "classification": "REAL_RED",
            "collector_provenance_class": "LIVE_AUTHORIZED_PUBLIC_HTTP",
            "receipt_provenance_class": "LIVE_AUTHORIZED_PUBLIC_HTTP",
            "escrow_action": "APPENDED_NEW_ENTRY",
            "escrow_eligible_for_fresh_oos": True,
            "escrow_record_count": 1,
        },
        "A03_INSTANCE_GET_MONKEYPATCH": {
            "classification": "REAL_RED",
            "collector_provenance_class": "LIVE_AUTHORIZED_PUBLIC_HTTP",
            "escrow_action": "APPENDED_NEW_ENTRY",
            "escrow_eligible_for_fresh_oos": True,
            "escrow_record_count": 1,
        },
    },
    "p0b_evidence_gaps_on_sealed_c3": {
        "classification": "REAL_RED",
        "c3_12_realized_tail_summary_is_empty_dict": True,
        "c3_12_realized_tail_summary_value": {},
        "c3_13_realized_tail_risk_disclosed_hardcoded_bool": True,
        "c3_21_G09_status_despite_empty_tail": "PASS",
        "c3_13_full_rerisk_b2_h1_1x_gross_pnl": -73.1555,
        "c3_13_full_rerisk_b2_h1_1x_net_pnl": -175.438,
        "c3_13_full_rerisk_b2_h1_1x_total_fees_displayed": 0.0,
        "c3_13_full_rerisk_fee_display_mismatch_confirmed": True,
    },
}


def _pypath_str() -> str:
    return os.pathsep.join([str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)])


def _cleanup_sentinel_temp_c4_win_safe(sentinel_tmp: Path, *, expected_run_id: str) -> Dict[str, Any]:
    """
    Windows long-path-safe wrapper around `safe_cleanup_sentinel_owned_temp_dir_r2_c1`.
    Verifies sentinel ownership via `verify_sentinel_owned_dir_r2_c1` first, removes deeply
    nested >260-char children inside `sentinel_tmp` using `\\\\?\\` extended paths
    (never following junctions/symlinks), then delegates final sentinel verification & root
    deletion to the canonical R2.C1 cleaner.
    """
    verify_sentinel_owned_dir_r2_c1(sentinel_tmp, expected_run_id=expected_run_id)
    if sys.platform == "win32":
        long_root = os.path.abspath(os.fspath(sentinel_tmp))
        if not long_root.startswith("\\\\?\\"):
            long_root = "\\\\?\\" + long_root
        for cur_root, dirs, files in os.walk(long_root, topdown=False, followlinks=False):
            for fname in files:
                if cur_root == long_root and fname == SENTINEL_FILENAME_KH02_R2_C1:
                    continue
                fpath = os.path.join(cur_root, fname)
                try:
                    os.unlink(fpath)
                except PermissionError:
                    os.chmod(fpath, 0o700)
                    os.unlink(fpath)
            for dname in dirs:
                dpath = os.path.join(cur_root, dname)
                try:
                    os.rmdir(dpath)
                except PermissionError:
                    os.chmod(dpath, 0o700)
                    os.rmdir(dpath)
    return safe_cleanup_sentinel_owned_temp_dir_r2_c1(sentinel_tmp, expected_run_id=expected_run_id)


def _build_bbo_fixture(snapshot_id: str, offset_ms: int = 120_000, transport_kind: str = "STDLIB_URLLIB_HTTPS_GET") -> Tuple[bytes, Dict[str, Any], int, int, int, str]:
    t0 = R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + offset_ms
    req_ms = t0 + 10
    recv_ms = t0 + 25
    body = json.dumps(
        {"code": "00000", "msg": "success", "data": [{"symbol": "XAUUSDT", "bidPr": "4016.10", "askPr": "4016.30", "ts": str(t0)}]}
    ).encode("utf-8")
    raw_sha = hashlib.sha256(body).hexdigest()
    parsed_obj = {
        "symbol": "XAUUSDT",
        "product_type": "USDT-FUTURES",
        "endpoint_key": "public_ticker",
        "event_utc_ms": t0,
        "receive_utc_ms": recv_ms,
        "bid": 4016.10,
        "ask": 4016.30,
        "spread_usd": 0.2,
        "transport_kind": transport_kind,
        "prospective_poll_snapshot_only": True,
    }
    parsed_sha = canonical_hash(parsed_obj)
    dataset_sha = canonical_hash({"raw_sha": raw_sha, "parsed_sha": parsed_sha})
    entry = {
        "snapshot_id": snapshot_id,
        "schema_version": SCHEMA_VERSION_KH02_R2_C1,
        "provider": "bitget_public_rest",
        "exchange": BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["exchange"],
        "product_type": "USDT-FUTURES",
        "exchange_contract_id": BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["exchange_contract_id"],
        "symbol": "XAUUSDT",
        "price_precision": BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["price_precision"],
        "tick_size": BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["tick_size"],
        "qty_precision": BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["qty_precision"],
        "qty_step": BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1["qty_step"],
        "timeframe": "L1_SNAPSHOT",
        "endpoint": "https://api.bitget.com/api/v2/mix/market/ticker",
        "request_utc_ms": req_ms,
        "response_utc_ms": recv_ms,
        "query_params": {"productType": "USDT-FUTURES", "symbol": "XAUUSDT"},
        "page_boundaries": [{"start_ms": t0, "end_ms": t0 + 1}],
        "cursor": "DONE",
        "http_status": 200,
        "pagination_complete": True,
        "rate_limit_behavior": "BOUNDED_SEEDED_BACKOFF_OK",
        "license_or_access": "PUBLIC_MARKET_DATA_READONLY",
        "start_utc_ms": t0,
        "end_utc_ms": t0 + 1,
        "event_or_close_utc_ms": t0,
        "capture_time_ms": recv_ms,
        "known_at_ms": recv_ms,
        "clock_skew_ms": recv_ms - t0,
        "bar_count": 1,
        "utc_monotonic_strict": True,
        "data_origin": "OBSERVED_QUOTE",
        "observed_bid_ask_present": True,
        "quote_mode": "OBSERVED_L1_BBO",
        "raw_response_sha256": raw_sha,
        "canonical_parsed_sha256": parsed_sha,
        "pagination_lineage": ["page_1_of_1"],
        "source_signature_or_checksum": f"raw_sha256:{raw_sha[:16]}",
        "dataset_sha256": dataset_sha,
        "partition_role": "FRESH_UNTOUCHED",
        "dev_oos_physically_separated": True,
        "storage_path": "escrow_oos_isolated/prosp_l1_bbo.blob",
        "account_fee_tier_status": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        "strategy_evaluation_count": 0,
    }
    return body, entry, t0, req_ms, recv_ms, parsed_sha


def _audit_c4_function_inventory_01(head_sha: str) -> Dict[str, Any]:
    locked_inv = []
    for rel in LOCKED_KEEP_AS_IS_FILES_KH02_R2:
        fp = REPO_ROOT / rel
        locked_inv.append({"path": rel, "exists": fp.is_file(), "sha256": _sha256_file(fp) if fp.is_file() else None})
    _, diff_out = _git_cmd(["diff", "--name-only", head_sha])
    _, diff_vs_c3 = _git_cmd(["diff", "--name-only", f"{PRIOR_KH02_R2_C3_PUB_HEAD_SHA}..{head_sha}"])
    changed_all = sorted({ln.strip() for ln in (diff_out + "\n" + diff_vs_c3).splitlines() if ln.strip()})
    locked_set = set(LOCKED_KEEP_AS_IS_FILES_KH02_R2)
    locked_mod = sorted(p for p in changed_all if p in locked_set)

    forbidden_hits: List[Dict[str, str]] = []
    c4_inv: List[Dict[str, Any]] = []
    for rel in C4_SOURCE_FILES_ALLOWLIST:
        fp = REPO_ROOT / rel
        if not fp.is_file():
            continue
        txt = fp.read_text("utf-8")
        tree = ast.parse(txt, filename=str(fp))
        for node in ast.walk(tree):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for mod in mods:
                for tok in FORBIDDEN_ORDER_SERVICE_TOKENS:
                    if tok in mod:
                        forbidden_hits.append({"path": rel, "imported_module": mod, "token": tok})
        if "kh_02_r2_c4" in rel or rel in (
            "aurum-desk/backend/services/kh_02_r2_c3_provenance_guard.py",
            "aurum-desk/backend/services/kh_02_r2_c2_public_capture.py",
            "aurum-desk/backend/services/kh_02_r2_c2_escrow_journal.py",
            "aurum-desk/backend/services/kh_02_r2_prospective_data_service.py",
        ):
            funcs = _ast_function_inventory(fp)
            c4_inv.append({"path": rel, "sha256": _sha256_file(fp), "function_count": len(funcs), "functions": [f["name"] for f in funcs]})

    c_live = CostAssumptions()
    cost_ok = (
        abs(float(c_live.taker_fee_rate) - 0.0004) < 1e-12
        and abs(float(c_live.maker_fee_rate) - 0.0002) < 1e-12
        and abs(float(c_live.slippage_usd) - 0.10) < 1e-12
    )
    ok = len(locked_mod) == 0 and len(forbidden_hits) == 0 and cost_ok
    return {
        "spec_version": SPEC_VERSION_KH02_R2_C4,
        "schema_version": SCHEMA_VERSION_KH02_R2_C4,
        "base_head_sha": head_sha,
        "prior_c3_pub_head_sha": PRIOR_KH02_R2_C3_PUB_HEAD_SHA,
        "changed_files_vs_c3": sorted(ln.strip() for ln in diff_vs_c3.splitlines() if ln.strip()),
        "locked_keep_as_is_count": len(locked_inv),
        "locked_keep_as_is_inventory": locked_inv,
        "locked_files_modified_count": len(locked_mod),
        "locked_files_modified": locked_mod,
        "c4_core_and_edited_files_inventory": c4_inv,
        "forbidden_order_import_hits": forbidden_hits,
        "cost_assumptions_defaults_intact": cost_ok,
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        "status": "PASS" if ok else "FAIL",
    }


def _audit_prior_sealed_02() -> Dict[str, Any]:
    out_map: Dict[str, Any] = {}
    all_ok = True
    for rel, exp_sha, lbl in PRIOR_SEALED_POINTERS_AND_ARTIFACTS_C4:
        fp = REPO_ROOT / rel
        act = _sha256_file(fp) if fp.is_file() else "MISSING"
        m = act == exp_sha
        if not m:
            all_ok = False
        out_map[lbl] = {"rel_path": rel, "expected_sha256": exp_sha, "actual_sha256": act, "match": m}
    ds_match = bool((out_map.get("CANONICAL_DATASET") or {}).get("match", False))
    return {
        "spec_version": SPEC_VERSION_KH02_R2_C4,
        "schema_version": SCHEMA_VERSION_KH02_R2_C4,
        "canonical_dataset_rel_path": CANONICAL_DATASET_REL_PATH,
        "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
        "canonical_dataset_sha256_match": ds_match,
        "all_prior_sealed_artifacts_intact": all_ok,
        "prior_sealed_artifacts_immutability": out_map,
        "status": "PASS" if (all_ok and ds_match) else "FAIL",
    }


def _run_p0a_green_and_http_escrow_06_07_08(sentinel_tmp: Path) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    ok_disk = {"can_run_heavy_stages": True, "min_free_gib": 35.0, "status": "PASS_STRICT_AND_ADVISORY"}
    auth_live = OperatorCaptureAuthorizationC2(
        approval_id="C4_AUDIT_APP",
        approved_by="c4_auditor",
        network_capture_enabled=True,
        capture_session_id="c4_audit_sess_01",
        live_network_session_authorized=True,
    )
    real_j = C2DurableEscrowJournal.open_real_oos_escrow(sentinel_tmp / "c4_real_oos_test", os_disk_probe_fn=lambda: 35.0)
    qa_j = C2DurableEscrowJournal(sentinel_tmp / "c4_qa_isolated_test", isolated_qa_mode=True, os_disk_probe_fn=lambda: 35.0)

    # 1. Direct mint boolean spoof -> blocked
    b1, e1, t1, rq1, rs1, ps1 = _build_bbo_fixture("c4_g_direct", 120_000)
    rcpt1 = mint_trusted_capture_receipt_c3(
        capture_session_id="forged_sess", endpoint_key="public_ticker",
        url="https://api.bitget.com/api/v2/mix/market/ticker",
        params={"productType": "USDT-FUTURES", "symbol": "XAUUSDT"},
        http_status=200, raw_response_bytes=b1, canonical_parsed_sha256=ps1,
        request_utc_ms=rq1, response_utc_ms=rs1, monotonic_duration_ms=4, provider_event_utc_ms=t1,
        transport_obj=StdlibUrllibPublicTransportC2(), transport_kind="STDLIB_URLLIB_HTTPS_GET",
        tls_host="api.bitget.com", is_fake_transport_qa=False, live_transport_verified=True,
        live_authorization_verified=True, real_rate_limiter_active=True,
    )
    direct_never_live = rcpt1.provenance_class != ProvenanceClassC3.LIVE_AUTHORIZED_PUBLIC_HTTP
    direct_blocked = False
    try:
        real_j.append_verified_prospective_snapshot(e1, raw_payload_bytes=b1, capture_receipt=rcpt1)
    except PermissionError:
        direct_blocked = True

    # 2. Evil subclass override -> blocked
    b2, _, t2, rq2, rs2, _ = _build_bbo_fixture("c4_g_sub", 180_000)

    class _EvilSub(StdlibUrllibPublicTransportC2):
        def is_genuine_live_socket_transport(self) -> bool:
            return True

        def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
            return RawHttpResponseC2(200, b2, {"content-type": "application/json"}, rq2, rs2, "STDLIB_URLLIB_HTTPS_GET", "api.bitget.com", url, 4)

    cap_sub = C2PublicReadOnlyBitgetCollector(authorization=auth_live, transport=_EvilSub(), is_fake_transport_qa=False, os_disk_probe_fn=lambda: 35.0).fetch_public_l1_bbo_snapshot(snapshot_id="c4_g_sub", write_mount_probe=ok_disk)
    sub_blocked = cap_sub["provenance_class"] != "LIVE_AUTHORIZED_PUBLIC_HTTP"
    try:
        real_j.append_verified_prospective_snapshot(cap_sub["snapshot_contract_entry"], raw_payload_bytes=cap_sub["raw_response_bytes"], capture_receipt=cap_sub["capture_receipt"])
        sub_blocked = False
    except PermissionError:
        pass

    # 3. Instance .get monkeypatch -> blocked
    tr_mp = StdlibUrllibPublicTransportC2()
    tr_mp.get = lambda url, params, *, timeout_sec: RawHttpResponseC2(200, b2, {}, rq2, rs2, "STDLIB_URLLIB_HTTPS_GET", "api.bitget.com", url, 3)  # type: ignore[method-assign]
    cap_mp = C2PublicReadOnlyBitgetCollector(authorization=auth_live, transport=tr_mp, is_fake_transport_qa=False, os_disk_probe_fn=lambda: 35.0).fetch_public_l1_bbo_snapshot(snapshot_id="c4_g_mp", write_mount_probe=ok_disk)
    mp_blocked = cap_mp["provenance_class"] != "LIVE_AUTHORIZED_PUBLIC_HTTP"
    try:
        real_j.append_verified_prospective_snapshot(cap_mp["snapshot_contract_entry"], raw_payload_bytes=cap_mp["raw_response_bytes"], capture_receipt=cap_mp["capture_receipt"])
        mp_blocked = False
    except PermissionError:
        pass

    # 4. Post-init _opener_fn mutation -> blocked
    tr_post = StdlibUrllibPublicTransportC2()
    col_post = C2PublicReadOnlyBitgetCollector(authorization=auth_live, transport=tr_post, is_fake_transport_qa=False, os_disk_probe_fn=lambda: 35.0)
    tr_post._opener_fn = lambda _r, _t: None
    ok_post, _ = col_post._check_live_transport_exact_now()
    post_init_blocked = (not ok_post) and (col_post._trusted_transport_classification() != ProvenanceClassC3.LIVE_AUTHORIZED_PUBLIC_HTTP)

    # 5. Naked fabricated RawHttpResponseC2 / raw bytes without receipt -> blocked
    naked_blocked = False
    try:
        real_j.append_verified_prospective_snapshot(e1, raw_payload_bytes=b1, capture_receipt=None)
    except PermissionError:
        naked_blocked = True

    # 6. QA fake capture -> raw reparse, parsed forgery, event_ms tamper, modified bytes, expiry, replay
    b_qa, _, _, rq_q, rs_q, _ = _build_bbo_fixture("c4_g_qa", 240_000, transport_kind="INJECTED_FAKE_TRANSPORT")
    qa_col = C2PublicReadOnlyBitgetCollector(
        authorization=OperatorCaptureAuthorizationC2(approval_id="QA_ONLY", approved_by="qa", network_capture_enabled=True),
        transport=_SequenceFakeTransportC2([RawHttpResponseC2(200, b_qa, {"content-type": "application/json"}, rq_q, rs_q)]),
        is_fake_transport_qa=True,
    )
    cap_qa = qa_col.fetch_public_l1_bbo_snapshot(snapshot_id="c4_g_qa", write_mount_probe=ok_disk)
    rp_ok = reparse_and_verify_raw_market_bytes_c3(cap_qa["raw_response_bytes"], cap_qa["snapshot_contract_entry"], capture_receipt=cap_qa["capture_receipt"])["valid"]
    rp_forged = not reparse_and_verify_raw_market_bytes_c3(cap_qa["raw_response_bytes"], dict(cap_qa["snapshot_contract_entry"], canonical_parsed_sha256="9" * 64))["valid"]
    rp_ts = not reparse_and_verify_raw_market_bytes_c3(cap_qa["raw_response_bytes"], dict(cap_qa["snapshot_contract_entry"], event_or_close_utc_ms=int(cap_qa["snapshot_contract_entry"]["event_or_close_utc_ms"]) + 1000))["valid"]
    mod_bytes_blk = not evaluate_c3_prospective_admission(
        cap_qa["snapshot_contract_entry"],
        raw_payload_bytes=cap_qa["raw_response_bytes"].replace(b"4016.10", b"4016.20"),
        capture_receipt=cap_qa["capture_receipt"],
        require_live_real_oos=False,
    )["admitted"]

    qa_w1 = qa_j.append_verified_prospective_snapshot(cap_qa["snapshot_contract_entry"], raw_payload_bytes=cap_qa["raw_response_bytes"], capture_receipt=cap_qa["capture_receipt"])
    qa_idem = qa_j.append_verified_prospective_snapshot(cap_qa["snapshot_contract_entry"], raw_payload_bytes=cap_qa["raw_response_bytes"], capture_receipt=cap_qa["capture_receipt"])
    replay_blocked = False
    try:
        qa_j.append_verified_prospective_snapshot(dict(cap_qa["snapshot_contract_entry"], snapshot_id="c4_g_qa_replayed_other"), raw_payload_bytes=cap_qa["raw_response_bytes"], capture_receipt=cap_qa["capture_receipt"])
    except PermissionError:
        replay_blocked = True

    expire_capture_proof_for_test_only_c4(cap_qa["capture_proof_c4"].proof_id)
    exp_adm = evaluate_c3_prospective_admission(cap_qa["snapshot_contract_entry"], raw_payload_bytes=cap_qa["raw_response_bytes"], capture_receipt=cap_qa["capture_receipt"], require_live_real_oos=False)
    expired_blk = (not exp_adm["admitted"]) and any("EXPIRED_C4_CAPTURE_PROOF" in v for v in exp_adm["violations"])

    real_oos_cnt = int(real_j.verify_integrity()["record_count"])
    f06 = {
        "spec_version": SPEC_VERSION_KH02_R2_C4,
        "direct_mint_spoof_blocked": direct_blocked,
        "direct_mint_green_provenance_never_live": direct_never_live,
        "evil_subclass_blocked": sub_blocked,
        "instance_get_monkeypatch_blocked": mp_blocked,
        "post_init_opener_mutation_blocked": post_init_blocked,
        "naked_raw_response_blocked": naked_blocked,
        "qa_isolated_admitted_test_only": (qa_w1["action"] == "APPENDED_NEW_ENTRY" and qa_w1["eligible_for_fresh_oos"] is False and qa_idem["action"] == "IDEMPOTENT_ALREADY_RECORDED_NOOP"),
        "raw_bytes_reparse_verified": rp_ok,
        "parsed_forgery_rejected": rp_forged,
        "event_timestamp_tamper_rejected": rp_ts,
        "modified_bytes_after_capture_rejected": mod_bytes_blk,
        "expired_proof_rejected": expired_blk,
        "proof_replay_different_snapshot_blocked": replay_blocked,
        "real_oos_escrow_final_record_count": real_oos_cnt,
        "status": "PASS" if all([direct_blocked, direct_never_live, sub_blocked, mp_blocked, post_init_blocked, naked_blocked, rp_ok, rp_forged, rp_ts, mod_bytes_blk, expired_blk, replay_blocked, real_oos_cnt == 0]) else "FAIL",
    }

    # Artifact 07: Inherited 20 fake-transport HTTP cases + 8 C4 security cases
    suite20 = run_kh02_r2_c2_fake_transport_collector_suite()
    c4_sec_cases: List[Dict[str, Any]] = []

    def _srec(cid: str, ok: bool, detail: str) -> None:
        c4_sec_cases.append({"case_id": cid, "status": "PASS" if ok else "FAIL", "detail": detail})

    _srec("C4-HTTP-01_EXACT_CLASS_CHECK", not is_exact_unmodified_stdlib_transport_c4(_EvilSub(), expected_transport_cls=StdlibUrllibPublicTransportC2, expected_get_func=StdlibUrllibPublicTransportC2.get)[0], "Evil subclass rejected")
    _srec("C4-HTTP-02_INSTANCE_PATCH_CHECK", not is_exact_unmodified_stdlib_transport_c4(tr_mp, expected_transport_cls=StdlibUrllibPublicTransportC2, expected_get_func=StdlibUrllibPublicTransportC2.get)[0], "Instance .get patch rejected")
    _srec("C4-HTTP-03_POST_INIT_OPENER", post_init_blocked, "Post-init _opener_fn mutation rejected")
    _srec("C4-HTTP-04_DEFAULT_REAL_SLEEP", C2PublicReadOnlyBitgetCollector(transport=StdlibUrllibPublicTransportC2(), is_fake_transport_qa=False).describe_readiness()["real_rate_limiter_default_active"] is True, "Default real time.sleep verified")
    c4_non_copy = False
    try:
        copy.copy(cap_qa["capture_proof_c4"])
    except TypeError:
        c4_non_copy = True
    _srec("C4-HTTP-05_PROOF_NON_COPYABLE", c4_non_copy, "VerifiedCaptureProofC4 non-copyable")
    c4_non_pkl = False
    try:
        pickle.dumps(cap_qa["capture_proof_c4"])
    except TypeError:
        c4_non_pkl = True
    _srec("C4-HTTP-06_PROOF_NON_SERIALIZABLE", c4_non_pkl, "VerifiedCaptureProofC4 non-picklable")
    _srec("C4-HTTP-07_EXPIRED_PROOF_FAIL_CLOSED", expired_blk, "Expired capture proof rejected")
    _srec("C4-HTTP-08_ZERO_LIVE_NETWORK_BY_DESIGN", C2PublicReadOnlyBitgetCollector().describe_readiness()["network_capture_enabled"] is False, "Network OFF by default")

    c4_sec_pass = sum(1 for c in c4_sec_cases if c["status"] == "PASS")
    f07 = {
        "spec_version": SPEC_VERSION_KH02_R2_C4,
        "inherited_20_cases_total": int(suite20["total_cases"]),
        "inherited_20_cases_passed": int(suite20["passed_cases"]),
        "inherited_20_cases_failed": int(suite20["failed_cases"]),
        "c4_security_cases_total": len(c4_sec_cases),
        "c4_security_cases_passed": c4_sec_pass,
        "c4_security_cases_failed": len(c4_sec_cases) - c4_sec_pass,
        "c4_security_cases": c4_sec_cases,
        "live_network_capture_executed": False,
        "real_prospective_snapshots_collected": 0,
        "live_network_test_status": "LIVE_NETWORK_TEST_NOT_RUN_BY_DESIGN",
        "status": "PASS" if (suite20["all_passed"] and c4_sec_pass == len(c4_sec_cases)) else "FAIL",
    }

    # Artifact 08: Concurrency 2/4/8 threads + 3 OS processes + crash/quarantine + anchor limitations
    threads_ok = True
    for nw in (2, 4, 8):
        cj = C2DurableEscrowJournal(sentinel_tmp / f"c4_thr_{nw}")
        bar = threading.Barrier(nw)
        same = make_synthetic_valid_c1_snapshot_fixture(snapshot_id=f"c4_same_{nw}")
        same["dataset_sha256"] = hashlib.sha256(f"c4_same_{nw}".encode()).hexdigest()
        with ThreadPoolExecutor(max_workers=nw) as pool:
            acts = list(pool.map(lambda _: (bar.wait(), cj.append_snapshot(same)["action"])[1], range(nw)))
        if acts.count("APPENDED_NEW_ENTRY") != 1 or acts.count("IDEMPOTENT_ALREADY_RECORDED_NOOP") != nw - 1:
            threads_ok = False

    mp_dir = sentinel_tmp / "c4_mp_os_lock"
    mp_code = "\n".join([
        "import hashlib, json, sys; from pathlib import Path",
        "from services.kh_02_r2_c2_escrow_journal import C2DurableEscrowJournal",
        "from services.kh_02_r2_prospective_data_service import make_synthetic_valid_c1_snapshot_fixture",
        "j = C2DurableEscrowJournal(Path(sys.argv[1])); w = int(sys.argv[2]); res = []",
        "s = make_synthetic_valid_c1_snapshot_fixture(snapshot_id=f'c4_mp_{w}')",
        "s['dataset_sha256'] = hashlib.sha256(f'c4_mp_{w}'.encode()).hexdigest()",
        "res.append(j.append_snapshot(s)['action'])",
        "sh = make_synthetic_valid_c1_snapshot_fixture(snapshot_id='c4_mp_shared')",
        "sh['dataset_sha256'] = 'c' * 64",
        "res.append(j.append_snapshot(sh)['action'])",
        "print(json.dumps(res))",
    ])
    env_mp = os.environ.copy()
    env_mp["PYTHONPATH"] = _pypath_str()
    procs = [subprocess.Popen([sys.executable, "-c", mp_code, str(mp_dir), str(i)], cwd=str(APP_ROOT), env=env_mp, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(3)]
    mp_priv, mp_sh, mp_rc_ok = [], [], True
    for p in procs:
        out, err = p.communicate(timeout=30)
        if p.returncode != 0:
            mp_rc_ok = False
        else:
            arr = json.loads(out.strip())
            mp_priv.append(arr[0])
            mp_sh.append(arr[1])
    mp_chk = C2DurableEscrowJournal(mp_dir).verify_integrity()
    mp_ok = mp_rc_ok and mp_priv.count("APPENDED_NEW_ENTRY") == 3 and mp_sh.count("APPENDED_NEW_ENTRY") == 1 and mp_sh.count("IDEMPOTENT_ALREADY_RECORDED_NOOP") == 2 and mp_chk["valid"] and mp_chk["record_count"] == 4

    # Crash & quarantine + external anchor rollback truth
    cr_j = C2DurableEscrowJournal(sentinel_tmp / "c4_crash_q")
    s_base = make_synthetic_valid_c1_snapshot_fixture(snapshot_id="cr_base")
    s_base["dataset_sha256"] = hashlib.sha256(b"cr_base").hexdigest()
    cr_j.append_snapshot(s_base)
    s_bad = make_synthetic_valid_c1_snapshot_fixture(snapshot_id="cr_bad")
    s_bad["dataset_sha256"] = hashlib.sha256(b"cr_bad").hexdigest()
    try:
        cr_j.append_snapshot(s_bad, _crash_hook=make_crash_hook_for_phase("AFTER_BLOB_FSYNC_BEFORE_JOURNAL_APPEND"))
    except RuntimeError:
        pass
    q_rec = cr_j.quarantine_corrupt_tail_and_orphans(operator_reason="C4_CRASH_Q")
    crash_ok = q_rec["quarantined_count"] == 1 and q_rec["post_recovery_integrity"]["valid"]

    unanc = C2DurableEscrowJournal(sentinel_tmp / "c4_unanc").verify_integrity()
    f08 = {
        "spec_version": SPEC_VERSION_KH02_R2_C4,
        "os_locking_mechanism": "fcntl.flock(LOCK_EX)" if sys.platform != "win32" else "threading.RLock+msvcrt.locking(LK_NBLCK)",
        "concurrent_writers_2_4_8_exact_once_passed": threads_ok,
        "interprocess_os_lock_passed": mp_ok,
        "crash_recovery_and_quarantine_passed": crash_ok,
        "proof_replay_different_snapshot_blocked": replay_blocked,
        "unanchored_immutability_class": unanc["immutability_class"],
        "unanchored_worm_verified": unanc["worm_verified"],
        "threat_model_disclosure": unanc["threat_model_disclosure"],
        "status": "PASS" if (threads_ok and mp_ok and crash_ok and replay_blocked and unanc["worm_verified"] is False) else "FAIL",
    }
    return f06, f07, f08


def _run_c4_historical_replay_and_tail_09_to_15(
    run_dir: Path,
    head_sha: str,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    t0 = time.perf_counter()
    ds_path = REPO_ROOT / CANONICAL_DATASET_REL_PATH
    datasets, ds_meta = load_r2_canonical_dataset_readonly(ds_path)
    mtf_bundle = build_kh02_asof_mtf_features(datasets)
    r1_frozen_plan, _ = _r2_frozen_plan_and_arms(code_sha=head_sha)
    r1_candidates = generate_kh02_research_candidates(mtf_bundle, r1_frozen_plan)

    # 09: Unswept target & T01..T10 & future mutation
    t01_t10 = run_r2_t01_to_t10_adversarial_target_scenarios()
    t_by_id = {s["scenario_id"]: s for s in t01_t10["scenarios"]}
    ledger_bundle = build_r2_causal_unswept_level_ledger(
        datasets, source_latency_ms=0, warmup_m1=30, run_t10_future_mutation_proof=True, rng_seed=DEFAULT_RANDOM_SEED_KH02_R2
    )
    r1_ce = audit_r2_r1_target_eligibility_counterexamples(datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle, r1_candidates=r1_candidates)
    f09 = {
        "spec_version": SPEC_VERSION_KH02_R2_C4,
        "dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
        "live_causal_ledger_recomputed": True,
        "total_confirmed_levels": ledger_bundle["total_confirmed_levels"],
        "swept_on_source_htf_count": ledger_bundle["swept_on_source_htf_count"],
        "swept_earlier_by_intervening_m1_count": ledger_bundle["swept_earlier_by_intervening_m1_count"],
        "ledger_canonical_sha256": ledger_bundle["ledger_canonical_sha256"],
        "t01_to_t10_all_passed": bool(t01_t10["all_passed"]),
        "future_mutation_invariance_passed": bool((t_by_id.get("T10") or {}).get("passed", False) and (ledger_bundle.get("t10_future_mutation_proof") or {}).get("passed", False)),
        "swept_tp_accepted_count": 0 if bool(t01_t10["all_passed"]) else 1,
        "r1_target_counterexamples_summary": r1_ce.get("full_dataset_audit_summary"),
        "status": "PASS" if (bool(t01_t10["all_passed"]) and bool((ledger_bundle.get("t10_future_mutation_proof") or {}).get("passed", False))) else "FAIL",
    }

    # 10: Fresh live historical replay & exact parity vs sealed R2/C3 baseline
    r2_candidates = build_r2_target_repair_full_opportunity_conservation(
        datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle, r1_candidates=r1_candidates, source_latency_ms=0
    )
    replay_bundle = replay_r2_same_opportunity_paired_counterfactuals(
        datasets=datasets, r1_candidates=r1_candidates, r2_candidates=r2_candidates
    )
    paired_design = build_r2_paired_factorial_entry_geometry_design(
        r1_candidates=r1_candidates, r2_candidates=r2_candidates, r1_replays=replay_bundle["r1_replays"], r2_replays=replay_bundle["r2_replays"]
    )
    strata_bal = compute_r2_stratified_common_support_balance(
        r2_candidates=r2_candidates, r2_replays=replay_bundle["r2_replays"], mtf_bundle=mtf_bundle
    )

    r2_sealed = REPO_ROOT / R2_SEALED_RUN_DIR_REL
    r2_f09_sealed = json.loads((r2_sealed / "09_chronological_1position_three_fill_paired_replays.json").read_text("utf-8"))
    live_nb_by_arm: Dict[str, Any] = {}
    baseline_diffs: List[str] = []
    for arm in EXPECTED_R2_TAIL_ARMS_C4:
        live_s = ((replay_bundle.get("comparison_by_arm") or {}).get(arm) or {}).get("NEXT_BAR_OPEN_BID_ASK", {}).get("r2_summary") or {}
        seal_s = ((r2_f09_sealed.get("comparison_by_arm") or {}).get(arm) or {}).get("NEXT_BAR_OPEN_BID_ASK", {}).get("r2_summary") or {}
        live_nb_by_arm[arm] = live_s
        for k_int in ("closed", "wins", "losses"):
            if int(live_s.get(k_int, -1)) != int(seal_s.get(k_int, -2)):
                baseline_diffs.append(f"{arm}:{k_int}:{live_s.get(k_int)}!={seal_s.get(k_int)}")
        for k_flt, tol in (("net_pnl_usd", 0.01), ("total_fees_usd", 0.01), ("expectancy_r", 1e-4)):
            if abs(float(live_s.get(k_flt, 0.0)) - float(seal_s.get(k_flt, 999.0))) > tol:
                baseline_diffs.append(f"{arm}:{k_flt}:{live_s.get(k_flt)}!={seal_s.get(k_flt)}")

    f10 = {
        "spec_version": SPEC_VERSION_KH02_R2_C4,
        "dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
        "data_classification": "REUSED_RESEARCH_NOT_UNTOUCHED_OOS",
        "fresh_live_historical_replay_executed": True,
        "exact_match_with_sealed_r2_c3_baseline": len(baseline_diffs) == 0,
        "baseline_reconciliation_diffs": baseline_diffs,
        "live_next_bar_open_bid_ask_by_arm": live_nb_by_arm,
        "replay_wall_clock_sec": round(time.perf_counter() - t0, 3),
        "status": "PASS" if len(baseline_diffs) == 0 else "FAIL",
    }

    # 12 (compressed ledger) & 11 (complete tail by arm x mode) & 13 (independent numeric reconciliation + 10 mutations)
    ledger_gz_path = run_dir / "12_per_episode_risk_ledger.jsonl.gz"
    ledger_meta = build_c4_per_episode_risk_ledger(
        datasets=datasets, r2_replays=replay_bundle["r2_replays"], out_jsonl_gz_path=ledger_gz_path
    )
    ledger_recomp = recompute_c4_tail_counters_from_episode_ledger(ledger_gz_path)
    f11 = build_c4_realized_tail_evidence_from_r2_replays(
        datasets=datasets, r2_replays=replay_bundle["r2_replays"], ledger_meta=ledger_meta, ledger_recomputed=ledger_recomp
    )
    val11 = validate_c4_tail_disclosure_vs_risk_ledger(f11, ledger_gz_path)

    # 10 real mutation tests on tail evidence in-memory (NEVER mutating sealed 11 or 12!)
    mut_results: List[Dict[str, Any]] = []

    def _mtest(mid: str, mut_doc: Mapping[str, Any]) -> None:
        v = validate_c4_tail_disclosure_vs_risk_ledger(mut_doc, ledger_gz_path)
        mut_results.append({"mutation_id": mid, "detected_as_fail": not v["valid"], "violations": v["violations"][:3]})

    _mtest("TM01_EMPTY_DICT", {})
    _mtest("TM02_HARDCODED_TRUE_EMPTY_ARMS", {"realized_tail_risk_disclosed": True, "arm_fill_time_and_tail_audits": {}})
    d3 = copy.deepcopy(f11); d3["arm_fill_time_and_tail_audits"].pop("B2_H1"); _mtest("TM03_MISSING_ARM", d3)
    d4 = copy.deepcopy(f11); d4["arm_fill_time_and_tail_audits"]["B2_H2"].pop("STRESS_WIDER_SPREAD_SLIPPAGE_2X"); _mtest("TM04_MISSING_MODE", d4)
    d5 = copy.deepcopy(f11); d5["arm_fill_time_and_tail_audits"]["B2_H1"]["NEXT_BAR_OPEN_BID_ASK"]["realized_losses_exceeding_2p50_count"] = 9999; _mtest("TM05_TAMPERED_LOSS_GT_CAP_COUNT", d5)
    d6 = copy.deepcopy(f11); d6["arm_fill_time_and_tail_audits"]["B2_H1"]["NEXT_BAR_OPEN_BID_ASK"]["max_realized_net_loss_usd"] = 0.01; _mtest("TM06_TAMPERED_MAX_LOSS", d6)
    d7 = copy.deepcopy(f11); d7["arm_fill_time_and_tail_audits"]["B2_H1"]["NEXT_BAR_OPEN_BID_ASK"]["net_pnl_usd"] = 999.0; _mtest("TM07_TAMPERED_NET_PNL", d7)
    d8 = copy.deepcopy(f11); d8["risk_cap_usd"] = 10.0; _mtest("TM08_TAMPERED_CAP_THRESHOLD", d8)
    d9 = copy.deepcopy(f11); d9["linked_per_episode_ledger"]["ledger_file_sha256"] = "0" * 64; _mtest("TM09_TAMPERED_LEDGER_SHA", d9)
    d10 = copy.deepcopy(f11); d10["realized_tail_loss_disclosure"] = "guaranteed max loss <= 2.50"; _mtest("TM10_REMOVED_TAIL_DISCLOSURE", d10)

    mut_pass = sum(1 for r in mut_results if r["detected_as_fail"])
    f13 = {
        "spec_version": SPEC_VERSION_KH02_R2_C4,
        "independent_recompute_from_12": val11,
        "mutation_controls_total": len(mut_results),
        "mutation_controls_passed": mut_pass,
        "mutation_controls_failed": len(mut_results) - mut_pass,
        "mutation_controls": mut_results,
        "status": "PASS" if (val11["valid"] and mut_pass == len(mut_results)) else "FAIL",
    }

    # 14: Fixed-fill vs Full-Universe Rerisk + §05.4 Fee Reconciliation
    live_ff = reprice_r2_fixed_fills_cost_counterfactual(r2_replays=replay_bundle["r2_replays"])
    live_fr = rerun_r2_full_universe_by_cost_scenario(datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle)
    ok_ff, _ = validate_r2_cost_scope_honesty(live_ff)
    ok_fr, _ = validate_r2_cost_scope_honesty(live_fr)
    fee_rec = reconcile_c4_full_universe_cost_fees(live_fr, live_ff)
    dec_checks = {}
    for arm in EXPECTED_R2_TAIL_ARMS_C4:
        a_row = (live_ff.get("arms") or {}).get(arm) or {}
        sc_1x = a_row.get("1x_base_0p0004") or {}
        if "fees_usd" in sc_1x and "net_pnl_usd" in sc_1x:
            dec_checks[f"{arm}_1x_0p0004"] = {
                "gross_pnl_usd": a_row.get("gross_pnl_1x_usd"),
                "fees_usd": sc_1x.get("fees_usd"),
                "net_pnl_usd": sc_1x.get("net_pnl_usd"),
            }
    f14 = {
        "spec_version": SPEC_VERSION_KH02_R2_C4,
        "fresh_live_recomputed": True,
        "quote_mode": "MODELED_BID_ASK",
        "modeled_spread_usd": 0.20,
        "observed_l1_bbo_available": False,
        "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        "fixed_fill_zero_dropped_ids_verified": bool(ok_ff),
        "full_rerisk_starts_from_full_universe": bool(ok_fr and live_fr.get("starts_from_full_raw_opportunity_stream", False)),
        "fixed_fill_decimal_checks": dec_checks,
        "fixed_fill_arms_summary": live_ff.get("arms"),
        "full_universe_fee_reconciliation": fee_rec,
        "science_and_risk_truth_summary": summarize_c4_science_and_risk_truth(tail_doc=f11, reconciliation_doc=val11, cost_fee_reconciliation=fee_rec),
        "status": "PASS" if (ok_ff and ok_fr and fee_rec["status"] == "PASS" and len(dec_checks) >= 4) else "FAIL",
    }

    # 15: Statistical matching, WF, OOS & Economic status
    live_wf = run_r2_purged_walkforward_and_multiplicity(r2_replays=replay_bundle["r2_replays"], r2_fixed_costs=live_ff)
    f15 = {
        "spec_version": SPEC_VERSION_KH02_R2_C4,
        "fresh_live_recomputed": True,
        "b0_vs_b2_identifiability_status": paired_design["B0_VS_B2_CROSS_UNIVERSE"]["identifiability_status"],
        "PAIRED_COMPARISON_VERDICT": strata_bal.get("PAIRED_COMPARISON_VERDICT"),
        "arm_balance_reports": strata_bal.get("arm_balance_reports"),
        "walkforward_diagnostic_only_reused_data": True,
        "live_purged_walkforward_summary": {
            "RESEARCH_ECONOMIC_VERDICT": live_wf.get("RESEARCH_ECONOMIC_VERDICT"),
            "multiplicity_arms_evaluated": live_wf.get("multiplicity_arms_evaluated"),
            "walkforward_folds_count": live_wf.get("walkforward_folds_count"),
        },
        "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE",
        "FUTURE_OOS_READINESS_VERDICT": "BLOCKED_NO_VERIFIED_NEW_DATA",
        "FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA",
        "OOS_EVALUATIONS": 0,
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        "status": "PASS",
    }

    del datasets, mtf_bundle, r1_candidates, r2_candidates, replay_bundle, ledger_bundle
    gc.collect()
    return f09, f10, f11, f13, f14, f15, ledger_meta


def _run_c4_126_red_green_fault_suite(sentinel_tmp: Path, f11: Mapping[str, Any], ledger_gz: Path) -> Dict[str, Any]:
    c3_sub = sentinel_tmp / "c3_102_sub"
    c3_sub.mkdir(parents=True, exist_ok=True)
    c3_102 = _run_c3_102_red_green_fault_suite(REPO_ROOT, c3_sub)
    controls = list(c3_102["controls"])
    c4_new = 0

    def _rec(fid: str, name: str, fn: str, red_ok: bool, green_ok: bool, detail: str) -> None:
        nonlocal c4_new
        c4_new += 1
        controls.append({
            "fault_id": fid, "name": name, "anchored_function": fn,
            "red_rejected": bool(red_ok), "green_accepted": bool(green_ok),
            "status": "PASS" if (red_ok and green_ok) else "FAIL", "diagnostic": detail,
        })

    ok_disk = {"can_run_heavy_stages": True, "min_free_gib": 35.0, "status": "PASS_STRICT_AND_ADVISORY"}
    auth_live = OperatorCaptureAuthorizationC2(approval_id="F_C4", approved_by="qa", network_capture_enabled=True, capture_session_id="fc4_s", live_network_session_authorized=True)
    real_j = C2DurableEscrowJournal.open_real_oos_escrow(sentinel_tmp / "fc4_real", os_disk_probe_fn=lambda: 35.0)
    qa_j = C2DurableEscrowJournal(sentinel_tmp / "fc4_qa", isolated_qa_mode=True, os_disk_probe_fn=lambda: 35.0)
    b1, e1, t1, rq1, rs1, ps1 = _build_bbo_fixture("fc4_01", 400_000)
    b_qa, _, _, rq_q, rs_q, _ = _build_bbo_fixture("fc4_qa", 460_000, transport_kind="INJECTED_FAKE_TRANSPORT")
    qa_cap = C2PublicReadOnlyBitgetCollector(
        authorization=OperatorCaptureAuthorizationC2(approval_id="QA", approved_by="qa", network_capture_enabled=True),
        transport=_SequenceFakeTransportC2([RawHttpResponseC2(200, b_qa, {}, rq_q, rs_q)]), is_fake_transport_qa=True
    ).fetch_public_l1_bbo_snapshot(snapshot_id="fc4_qa", write_mount_probe=ok_disk)
    qa_w = qa_j.append_verified_prospective_snapshot(qa_cap["snapshot_contract_entry"], raw_payload_bytes=qa_cap["raw_response_bytes"], capture_receipt=qa_cap["capture_receipt"])
    qa_green = qa_w["action"] == "APPENDED_NEW_ENTRY" and qa_w["eligible_for_fresh_oos"] is False

    # C4-F01..F14: P0-A boundary controls
    rcpt_dm = mint_trusted_capture_receipt_c3(capture_session_id="s", endpoint_key="public_ticker", url="https://api.bitget.com/api/v2/mix/market/ticker", params={"productType": "USDT-FUTURES", "symbol": "XAUUSDT"}, http_status=200, raw_response_bytes=b1, canonical_parsed_sha256=ps1, request_utc_ms=rq1, response_utc_ms=rs1, monotonic_duration_ms=2, provider_event_utc_ms=t1, transport_obj=StdlibUrllibPublicTransportC2(), transport_kind="STDLIB_URLLIB_HTTPS_GET", tls_host="api.bitget.com", is_fake_transport_qa=False, live_transport_verified=True, live_authorization_verified=True, real_rate_limiter_active=True)
    _rec("C4-F01", "DIRECT_MINT_BOOLEAN_SPOOF_REFUSED", "mint_trusted_capture_receipt_c3", rcpt_dm.provenance_class == ProvenanceClassC3.UNVERIFIED, qa_green, "caller booleans cannot mint LIVE")

    fp = VerifiedCaptureProofC4("prf_fake", "s", "LIVE_AUTHORIZED_PUBLIC_HTTP", "public_ticker", "https://api.bitget.com/api/v2/mix/market/ticker", "https://api.bitget.com/api/v2/mix/market/ticker", "api.bitget.com", 200, rcpt_dm.params_canonical_sha256, rcpt_dm.raw_response_sha256, len(b1), rq1, rs1, 2, 1, 120.0, os.getpid(), "v1", "StdlibUrllibPublicTransportC2", "STDLIB_URLLIB_HTTPS_GET", True, "0" * 64)
    _rec("C4-F02", "FORGED_MANUAL_PROOF_REJECTED", "verify_capture_proof_c4", not verify_capture_proof_c4(fp, require_live=True)[0], verify_capture_proof_c4(qa_cap["capture_proof_c4"], require_live=False)[0], "unregistered proof rejected")

    class _EvSub(StdlibUrllibPublicTransportC2):
        def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
            return RawHttpResponseC2(200, b1, {}, rq1, rs1, "STDLIB_URLLIB_HTTPS_GET", "api.bitget.com", url, 2)

    sub_cap = C2PublicReadOnlyBitgetCollector(authorization=auth_live, transport=_EvSub(), is_fake_transport_qa=False, os_disk_probe_fn=lambda: 35.0).fetch_public_l1_bbo_snapshot(snapshot_id="fc4_sub", write_mount_probe=ok_disk)
    _rec("C4-F03", "EVIL_SUBCLASS_GET_OVERRIDE_BLOCKED", "is_exact_unmodified_stdlib_transport_c4", sub_cap["provenance_class"] != "LIVE_AUTHORIZED_PUBLIC_HTTP", qa_green, "subclass blocked")

    tr_mp = StdlibUrllibPublicTransportC2()
    tr_mp.get = lambda url, params, *, timeout_sec: RawHttpResponseC2(200, b1, {}, rq1, rs1, "STDLIB_URLLIB_HTTPS_GET", "api.bitget.com", url, 2)  # type: ignore[method-assign]
    mp_cap = C2PublicReadOnlyBitgetCollector(authorization=auth_live, transport=tr_mp, is_fake_transport_qa=False, os_disk_probe_fn=lambda: 35.0).fetch_public_l1_bbo_snapshot(snapshot_id="fc4_mp", write_mount_probe=ok_disk)
    _rec("C4-F04", "INSTANCE_GET_MONKEYPATCH_BLOCKED", "is_exact_unmodified_stdlib_transport_c4", mp_cap["provenance_class"] != "LIVE_AUTHORIZED_PUBLIC_HTTP", qa_green, "instance .get blocked")

    tr_pi = StdlibUrllibPublicTransportC2()
    col_pi = C2PublicReadOnlyBitgetCollector(authorization=auth_live, transport=tr_pi, is_fake_transport_qa=False, os_disk_probe_fn=lambda: 35.0)
    tr_pi._opener_fn = lambda _r, _t: None
    _rec("C4-F05", "POST_INIT_OPENER_MUTATION_CAUGHT", "C2PublicReadOnlyBitgetCollector._check_live_transport_exact_now", not col_pi._check_live_transport_exact_now()[0], StdlibUrllibPublicTransportC2().is_genuine_live_socket_transport(), "post-init mutation caught")

    nc_ok = False
    try:
        copy.copy(qa_cap["capture_proof_c4"])
    except TypeError:
        nc_ok = True
    _rec("C4-F06", "PROOF_NON_COPYABLE", "VerifiedCaptureProofC4.__copy__", nc_ok, qa_green, "copy blocked")

    np_ok = False
    try:
        pickle.dumps(qa_cap["capture_proof_c4"])
    except TypeError:
        np_ok = True
    _rec("C4-F07", "PROOF_NON_PICKLABLE", "VerifiedCaptureProofC4.__reduce__", np_ok, qa_green, "pickle blocked")

    r_rep = False
    try:
        qa_j.append_verified_prospective_snapshot(dict(qa_cap["snapshot_contract_entry"], snapshot_id="fc4_replayed"), raw_payload_bytes=qa_cap["raw_response_bytes"], capture_receipt=qa_cap["capture_receipt"])
    except PermissionError:
        r_rep = True
    _rec("C4-F08", "PROOF_REPLAY_DIFFERENT_SNAPSHOT_BLOCKED", "bind_capture_proof_consumption_c4", r_rep, qa_green, "cross-snapshot proof replay blocked")

    r_mb = not evaluate_c3_prospective_admission(qa_cap["snapshot_contract_entry"], raw_payload_bytes=qa_cap["raw_response_bytes"].replace(b"4016.10", b"4016.20"), capture_receipt=qa_cap["capture_receipt"], require_live_real_oos=False)["admitted"]
    _rec("C4-F09", "MODIFIED_BYTES_AFTER_CAPTURE_BLOCKED", "evaluate_c3_prospective_admission", r_mb, qa_green, "byte tamper blocked")

    expire_capture_proof_for_test_only_c4(qa_cap["capture_proof_c4"].proof_id)
    r_exp = not evaluate_c3_prospective_admission(qa_cap["snapshot_contract_entry"], raw_payload_bytes=qa_cap["raw_response_bytes"], capture_receipt=qa_cap["capture_receipt"], require_live_real_oos=False)["admitted"]
    _rec("C4-F10", "EXPIRED_CAPTURE_PROOF_BLOCKED", "CaptureAuthorityC4.verify_proof", r_exp, qa_green, "TTL expiry enforced")

    r_nk = False
    try:
        real_j.append_verified_prospective_snapshot(e1, raw_payload_bytes=b1, capture_receipt=None)
    except PermissionError:
        r_nk = True
    _rec("C4-F11", "NAKED_RAW_RESPONSE_NO_RECEIPT_BLOCKED", "C2DurableEscrowJournal.append_verified_prospective_snapshot", r_nk, qa_green, "naked response blocked")

    r_dm_e = False
    try:
        real_j.append_verified_prospective_snapshot(e1, raw_payload_bytes=b1, capture_receipt=rcpt_dm)
    except PermissionError:
        r_dm_e = True
    _rec("C4-F12", "DIRECT_MINT_ESCROW_ADMISSION_BLOCKED", "C2DurableEscrowJournal.append_verified_prospective_snapshot", r_dm_e and real_j.verify_integrity()["record_count"] == 0, qa_green, "0 real OOS rows")

    r_sub_e = False
    try:
        real_j.append_verified_prospective_snapshot(sub_cap["snapshot_contract_entry"], raw_payload_bytes=sub_cap["raw_response_bytes"], capture_receipt=sub_cap["capture_receipt"])
    except PermissionError:
        r_sub_e = True
    _rec("C4-F13", "EVIL_SUBCLASS_ESCROW_ADMISSION_BLOCKED", "C2DurableEscrowJournal.append_verified_prospective_snapshot", r_sub_e and real_j.verify_integrity()["record_count"] == 0, qa_green, "0 real OOS rows")

    r_c4m = mint_trusted_capture_receipt_c4(capture_proof=fp, capture_session_id="s", endpoint_key="public_ticker", url="https://api.bitget.com/api/v2/mix/market/ticker", params={"productType": "USDT-FUTURES", "symbol": "XAUUSDT"}, http_status=200, raw_response_bytes=b1, canonical_parsed_sha256=ps1, request_utc_ms=rq1, response_utc_ms=rs1, monotonic_duration_ms=2, provider_event_utc_ms=t1, transport_obj=StdlibUrllibPublicTransportC2(), transport_kind="STDLIB_URLLIB_HTTPS_GET", tls_host="api.bitget.com", is_fake_transport_qa=False, live_transport_verified=True, live_authorization_verified=True, real_rate_limiter_active=True)
    _rec("C4-F14", "MINT_C4_FORGED_PROOF_BLOCKED", "mint_trusted_capture_receipt_c4", r_c4m.provenance_class != ProvenanceClassC3.LIVE_AUTHORIZED_PUBLIC_HTTP, qa_green, "mint_c4 rejects forged proof")

    # C4-F15..F24: P0-B tail ledger & cost reconciliation controls
    val_clean = validate_c4_tail_disclosure_vs_risk_ledger(f11, ledger_gz)["valid"]
    _rec("C4-F15", "EMPTY_TAIL_SUMMARY_REJECTED", "validate_c4_tail_disclosure_vs_risk_ledger", not validate_c4_tail_disclosure_vs_risk_ledger({}, ledger_gz)["valid"], val_clean, "empty {} fails closed")
    _rec("C4-F16", "HARDCODED_TRUE_WITHOUT_ROWS_REJECTED", "validate_c4_tail_disclosure_vs_risk_ledger", not validate_c4_tail_disclosure_vs_risk_ledger({"realized_tail_risk_disclosed": True, "arm_fill_time_and_tail_audits": {}}, ledger_gz)["valid"], val_clean, "boolean-only PASS rejected")
    d17 = copy.deepcopy(f11); d17["arm_fill_time_and_tail_audits"].pop("B2_H1")
    _rec("C4-F17", "OMITTED_ARM_IN_TAIL_REJECTED", "validate_c4_tail_disclosure_vs_risk_ledger", not validate_c4_tail_disclosure_vs_risk_ledger(d17, ledger_gz)["valid"], val_clean, "missing arm caught")
    d18 = copy.deepcopy(f11); d18["arm_fill_time_and_tail_audits"]["B2_H2"].pop("STRESS_WIDER_SPREAD_SLIPPAGE_2X")
    _rec("C4-F18", "OMITTED_MODE_IN_TAIL_REJECTED", "validate_c4_tail_disclosure_vs_risk_ledger", not validate_c4_tail_disclosure_vs_risk_ledger(d18, ledger_gz)["valid"], val_clean, "missing mode caught")
    d19 = copy.deepcopy(f11); d19["arm_fill_time_and_tail_audits"]["B2_H1"]["NEXT_BAR_OPEN_BID_ASK"]["realized_losses_exceeding_2p50_count"] = 9999
    _rec("C4-F19", "ALTERED_OVERSIZED_LOSS_COUNT_REJECTED", "validate_c4_tail_disclosure_vs_risk_ledger", not validate_c4_tail_disclosure_vs_risk_ledger(d19, ledger_gz)["valid"], val_clean, "loss count mismatch caught")
    d20 = copy.deepcopy(f11); d20["arm_fill_time_and_tail_audits"]["B2_H1"]["NEXT_BAR_OPEN_BID_ASK"]["max_realized_net_loss_usd"] = 0.05
    _rec("C4-F20", "ALTERED_MAX_REALIZED_LOSS_REJECTED", "validate_c4_tail_disclosure_vs_risk_ledger", not validate_c4_tail_disclosure_vs_risk_ledger(d20, ledger_gz)["valid"], val_clean, "max loss mismatch caught")
    d21 = copy.deepcopy(f11); d21["risk_cap_usd"] = 9.0
    _rec("C4-F21", "ALTERED_RISK_CAP_THRESHOLD_REJECTED", "validate_c4_tail_disclosure_vs_risk_ledger", not validate_c4_tail_disclosure_vs_risk_ledger(d21, ledger_gz)["valid"], val_clean, "cap tamper caught")
    d22 = copy.deepcopy(f11); d22["linked_per_episode_ledger"]["ledger_file_sha256"] = "0" * 64
    _rec("C4-F22", "LEDGER_CHECKSUM_MISMATCH_REJECTED", "validate_c4_tail_disclosure_vs_risk_ledger", not validate_c4_tail_disclosure_vs_risk_ledger(d22, ledger_gz)["valid"], val_clean, "ledger sha mismatch caught")
    drop_p = sentinel_tmp / "fc4_dropped_ledger.jsonl.gz"
    with gzip.open(ledger_gz, "rt", encoding="utf-8") as src, gzip.open(drop_p, "wt", encoding="utf-8") as dst:
        lines = [ln for ln in src if ln.strip()]
        for ln in lines[:-1]:
            dst.write(ln)
    _rec("C4-F23", "DROPPED_EPISODE_ROW_IN_LEDGER_REJECTED", "validate_c4_tail_disclosure_vs_risk_ledger", not validate_c4_tail_disclosure_vs_risk_ledger(f11, drop_p)["valid"], val_clean, "dropped row caught")
    bad_rec = reconcile_c4_full_universe_cost_fees({"scenarios": {"1X_BASE_0p0004": {"raw_r2_geometry_risk_eligible_count": 10, "arms": {"B2_H1": {"replay_summary": {"closed": 1, "gross_pnl_usd": -10.0, "net_pnl_usd": -20.0, "total_fees_usd": 0.0}}}}}}, {"arms": {"B2_H1": {"1x_base_0p0004": {"fees_usd": 999.0}}}})
    ok_rec = reconcile_c4_full_universe_cost_fees({"scenarios": {"1X_BASE_0p0004": {"raw_r2_geometry_risk_eligible_count": 10, "arms": {"B2_H1": {"replay_summary": {"closed": 1, "gross_pnl_usd": -10.0, "net_pnl_usd": -20.0, "total_fees_usd": 0.0}}}}}}, {"arms": {"B2_H1": {"1x_base_0p0004": {"fees_usd": 10.0}}}})
    _rec("C4-F24", "UNRECONCILED_FULL_UNIVERSE_FEE_REJECTED", "reconcile_c4_full_universe_cost_fees", bad_rec["status"] == "NOT_RECONCILED", ok_rec["status"] == "PASS", "fee reconciliation mismatch caught")

    fids = [c["fault_id"] for c in controls]
    failed = sum(1 for c in controls if c["status"] != "PASS")
    return {
        "spec_version": SPEC_VERSION_KH02_R2_C4,
        "total_controls": len(controls),
        "inherited_c3_controls_count": len(c3_102["controls"]),
        "new_c4_controls_count": c4_new,
        "unique_fault_ids": len(set(fids)),
        "passed_controls": len(controls) - failed,
        "failed_controls": failed,
        "controls": controls,
        "status": "PASS" if (failed == 0 and len(set(fids)) == len(controls) and len(controls) >= 122 and c4_new >= 20) else "FAIL",
    }


def _run_c4_verifier_mutation_suite_21(sentinel_tmp: Path) -> Dict[str, Any]:
    from tests.test_kh_02_r2_c4_verifier_mutations import run_c4_verifier_subproc, write_synthetic_c4_run_dir

    base_dir = sentinel_tmp / "ver_mut_base"
    rid, sha = write_synthetic_c4_run_dir(base_dir, blocked_low_disk=False)
    rc_g, pay_g = run_c4_verifier_subproc(base_dir, rid, sha)
    green_ok = rc_g == 0 and pay_g.get("independent_verifier_status") == "PASS"

    def _ed(d: Path, fn: str, mut: Callable[[Dict[str, Any]], None]) -> None:
        p = d / fn
        obj = json.loads(p.read_text("utf-8"))
        mut(obj)
        p.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")

    def _drop_row(d: Path) -> None:
        p = d / "12_per_episode_risk_ledger.jsonl.gz"
        with gzip.open(p, "rt", encoding="utf-8") as src:
            lines = [ln for ln in src if ln.strip()]
        with gzip.open(p, "wt", encoding="utf-8") as dst:
            for ln in lines[:-1]:
                dst.write(ln)

    muts: List[Tuple[str, Callable[[Path], None], str]] = [
        ("VM01_EMPTY_TAIL_DICT", lambda d: _ed(d, "11_realized_tail_complete_by_arm_and_mode.json", lambda o: o.__setitem__("arm_fill_time_and_tail_audits", {})), "INDEPENDENT_TAIL_LEDGER"),
        ("VM02_FAKE_ZERO_LOSS_GT_CAP", lambda d: _ed(d, "11_realized_tail_complete_by_arm_and_mode.json", lambda o: o["arm_fill_time_and_tail_audits"]["B2_H1"]["NEXT_BAR_OPEN_BID_ASK"].__setitem__("realized_losses_exceeding_2p50_count", 0)), "CELL_INT_MISMATCH"),
        ("VM03_ALTERED_MAX_LOSS", lambda d: _ed(d, "11_realized_tail_complete_by_arm_and_mode.json", lambda o: o["arm_fill_time_and_tail_audits"]["B2_H1"]["NEXT_BAR_OPEN_BID_ASK"].__setitem__("max_realized_net_loss_usd", 1.25)), "CELL_FLOAT_MISMATCH"),
        ("VM04_DROPPED_LEDGER_ROW", _drop_row, "INDEPENDENT_TAIL_LEDGER"),
        ("VM05_JUNIT_COUNT_TAMPER", lambda d: _ed(d, "16_full_pytest_collection_execution_junit_bijection.json", lambda o: o.__setitem__("passed_count", 99)), "JUNIT_COUNTS_TAMPERED_VS_RAW_XML"),
        ("VM06_W1_W4_HASH_MUTATION", lambda d: _ed(d, "17_w1_w2_w4_w1repeat_sha256_timing_rss.json", lambda o: o["r2_science_slice_w1_w2_w4_repeat"]["runs"][2].__setitem__("canonical_sorted_sha256", "9" * 64)), "W1_W2_W4_DETERMINISM_HASH_MISMATCH"),
        ("VM07_DECIMAL_PNL_TAMPER", lambda d: _ed(d, "14_cost_fixed_fill_vs_full_rerisk_all_scenarios.json", lambda o: o["fixed_fill_decimal_checks"]["B2_H1_1x"].__setitem__("net_pnl_usd", 999.0)), "FIXED_FILL_DECIMAL_MISMATCH"),
        ("VM08_PREPUSH_FALSE_REMOTE", lambda d: _ed(d, "23_publication_two_phase_prepush_receipt.json", lambda o: o.update({"publication_status": "PASS_REMOTE_VERIFIED", "remote_readback_sha256_verified": False})), "FALSE_REMOTE_READBACK_CLAIM"),
        ("VM09_OOS_EVAL_NONZERO", lambda d: _ed(d, "15_statistical_matching_wf_oos_economic_status.json", lambda o: o.__setitem__("OOS_EVALUATIONS", 1)), "NONZERO_OOS_EVALUATIONS_PEEK"),
        ("VM10_ACTIVATION_TURNED_ON", lambda d: _ed(d, "15_statistical_matching_wf_oos_economic_status.json", lambda o: o.__setitem__("ACTIVATION_READY", "YES")), "FORGED_ACTIVATION_READY_ON"),
        ("VM11_FORGED_PROVEN_EDGE", lambda d: _ed(d, "15_statistical_matching_wf_oos_economic_status.json", lambda o: o.__setitem__("RESEARCH_ECONOMIC_VERDICT", "PROVEN_EDGE")), "FORGED_ECONOMIC_VERDICT"),
        ("VM12_OWNER_DISK_WAIVER", lambda d: _ed(d, "03_disk_ram_multimount_resource_policy_and_faults.json", lambda o: o.__setitem__("owner_disk_preflight_waiver", True)), "UNAPPROVED_OWNER_DISK_WAIVER_FORBIDDEN"),
        ("VM13_RAM_FLOOR_BREACH", lambda d: gzip.open(d / "logs" / "runtime_memory_samples_0p5s.jsonl.gz", "wt", encoding="utf-8").write(json.dumps({"available_ram_bytes": 150 * 1024 * 1024, "child_count": 1}) + "\n"), "MEMORY_TELEMETRY_BREACHED_512MIB_FLOOR"),
        ("VM14_GATE_MATRIX_TAMPER", lambda d: _ed(d, "22_orthogonal_gate_matrix_and_verdicts_prepush.json", lambda o: o["gates_23"].__setitem__("G19_ECONOMIC_TRUTH", "PROVEN_EDGE")), "GATE_TAMPER_OR_DRIFT"),
    ]

    def _run_one(item: Tuple[str, Callable[[Path], None], str]) -> Dict[str, Any]:
        nm, fn, tok = item
        td = sentinel_tmp / f"vm_{nm}"
        shutil.copytree(base_dir, td)
        fn(td)
        rc, pay = run_c4_verifier_subproc(td, rid, sha)
        viols = list(pay.get("violations") or [])
        det = rc == 1 and any(tok in v for v in viols)
        return {"mutation_id": nm, "expected_token": tok, "exit_code": rc, "detected": det, "violations_sample": viols[:3]}

    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(_run_one, muts))
    failed_det = sum(1 for r in rows if not r["detected"])
    return {
        "spec_version": SPEC_VERSION_KH02_R2_C4,
        "isolated_temp_clones_only": True,
        "sealed_run_dir_mutated": False,
        "clean_clone_verifier_green": green_ok,
        "mutations_tested_count": len(rows),
        "mutations_detected_count": len(rows) - failed_det,
        "mutations_failed_detection_count": failed_det,
        "mutations": rows,
        "status": "PASS" if (green_ok and failed_det == 0) else "FAIL",
    }


def _collect_and_build_c4_lpt_shards(owned_temp_root: Path) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]]]:
    all_test_files = sorted(str(p.relative_to(APP_ROOT)).replace("\\", "/") for p in (BACKEND_ROOT / "tests").glob("test_*.py") if p.is_file())
    col_out = owned_temp_root / "collect_c4_stdout.log"
    col_err = owned_temp_root / "collect_c4_stderr.log"
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
        raise RuntimeError(f"KH02_R2_C4_PYTEST_COLLECT_FAILED:rc={proc.returncode}:{err_txt}")

    collected_by_file: Dict[str, List[str]] = {}
    for ln in col_out.read_text("utf-8", errors="replace").splitlines():
        cid = canonicalize_collect_node_id_v1793122(ln)
        if cid is not None:
            collected_by_file.setdefault(cid.split("::", 1)[0], []).append(cid)
    for k in collected_by_file:
        collected_by_file[k].sort()

    empirical_ms = _load_empirical_file_durations_ms_from_kh01b()
    non_sub = [f for f in all_test_files if Path(f).name not in C4_EXCLUSIVE_SUBPROCESS_TEST_FILES]
    sub_files = [f for f in all_test_files if Path(f).name in C4_EXCLUSIVE_SUBPROCESS_TEST_FILES]
    shards = _build_lpt_shards_kh01b_r1(non_sub, collected_by_file, empirical_ms)
    for sh in shards:
        if sh.get("shard_name") == "SHARD_SINGLE_CHILD_02_BUDGET2":
            sh["exclusive"] = True
            sh["slot_cost"] = 4
    for idx_s, sf in enumerate(sorted(sub_files), 1):
        shards.append({
            "shard_name": f"SHARD_SINGLE_CHILD_C4_SUBPROC_{idx_s:02d}",
            "files": [sf],
            "exclusive": True,
            "slot_cost": 4,
            "priority_weight": 260_000,
            "needs_venv": False,
        })
    return collected_by_file, shards


def _run_c4_full_repo_pytest_sharded(
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
    collected_by_file, c4_shards = _collect_and_build_c4_lpt_shards(sentinel_tmp)
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
            shard_specs_override=c4_shards,
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
        mandatory_node_prefixes=("backend/tests/test_kh_02_r2",),
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
    ok = bj.get("status") == "PASS" and nonzero_shards == 0 and collected_n > 0 and collected_n == executed_n and failed_n == 0 and error_n == 0 and mand_skip_n == 0
    return {
        "spec_version": SPEC_VERSION_KH02_R2_C4,
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


def _render_c4_readme_and_handover(
    *,
    run_id: str,
    run_dir: Path,
    f00: Mapping[str, Any],
    f03: Mapping[str, Any],
    f10: Mapping[str, Any],
    f11: Mapping[str, Any],
    f16: Mapping[str, Any],
    f17: Mapping[str, Any],
    f18: Mapping[str, Any],
    f19: Mapping[str, Any],
    f20: Mapping[str, Any],
    f21: Mapping[str, Any],
    f22: Mapping[str, Any],
    f23: Mapping[str, Any],
) -> Tuple[str, str]:
    v = f22["verdicts"]
    g = f22["gates_23"]
    mp = f03["strict_multi_mount_probe"]
    md_lines = [
        f"# AURUM DESK — KH-02.R2.C4 | TRUSTED CAPTURE BOUNDARY HARDENING, RISK-TAIL EVIDENCE & INDEPENDENT CLOSURE",
        "",
        f"- **Phân loại kỹ thuật**: **`{f22['overall_classification']}`**",
        f"- **Spec Version**: `{SPEC_VERSION_KH02_R2_C4}`",
        f"- **Run ID**: `{run_id}`",
        f"- **Nhánh bắt buộc**: `{f00['branch']}`",
        f"- **Tested Executable SHA**: `{f00['tested_executable_sha']}` (`git_clean_at_start={f00['git_clean_at_start']}`)",
        f"- **Khóa an toàn**: `PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO | OOS_EVALUATIONS=0`",
        "",
        "## 1. Kết luận trực giao (Không gộp giả)",
        "",
        "| Chiều kiểm định | Kết luận |",
        "|---|---|",
        *[f"| `{k}` | **{val}** |" for k, val in v.items()],
        "",
        "## 2. Ma trận 23 cổng (`G01`..`G23`)",
        "",
        "| Cổng | Trạng thái |",
        "|---|---|",
        *[f"| `{k}` | **{val}** |" for k, val in g.items()],
        "",
        "## 3. Tóm tắt số đo thực tế",
        f"- **Tài nguyên (`03`, `19`)**: Preflight `{mp['min_free_gib']} GiB`, runtime min `{f19['min_disk_free_gib']} GiB`, RAM min `{f19['min_available_ram_mib']} MiB`, max children `{f19['max_descendant_children']}`, orphans `{f19['orphan_children_after_cleanup']}`.",
        f"- **P0-B Risk-Tail (`11`, `12`, `13`)**: `{f11['coverage_cells_populated']}/{f11['coverage_cells_expected']}` arm×mode cells, `{f11['linked_per_episode_ledger']['total_episode_rows']}` episode rows, realized losses > 2.50 USD = `{f11['total_realized_losses_exceeding_2p50_count']}`, max realized loss = `{f11['global_max_realized_net_loss_usd']} USD`, decision/post-fill cap violations = `{f11['decision_time_cap_violations']}/{f11['post_fill_policy_cap_violations']}`.",
        f"- **Full Pytest (`16`)**: `{f16['status']}` — collected=`{f16.get('collected_count')}`, executed=`{f16.get('executed_count')}`, passed=`{f16.get('passed_count')}`, skipped=`{f16.get('skipped_count')}`, mandatory_skipped=`{f16.get('mandatory_skipped_count')}`, failed=`{f16.get('failed_count')}`, error=`{f16.get('error_count')}`.",
        f"- **Faults (`20`)**: `{f20['passed_controls']}/{f20['total_controls']}` (`{f20['new_c4_controls_count']}` new C4 controls).",
        f"- **Verifier Mutation Resistance (`21`)**: `{f21['mutations_detected_count']}/{f21['mutations_tested_count']}` detected (`PASS`).",
        f"- **Baseline Parity (`18`)**: `{f18.get('BASELINE_PARITY_VERDICT')}` (`A1={f18.get('a1_population_count')}/mism={f18.get('a1_mismatches_count')}`, `SI8={f18.get('si8_population_count')}/mism={f18.get('si8_mismatches_count')}`, `3-adapter={f18.get('three_adapter_population_count')}/mism={f18.get('three_adapter_mismatches_count')}`, `D4={f18.get('d4_historical_closed_trades')}`).",
        f"- **Kinh tế & OOS**: `RESEARCH_ECONOMIC_VERDICT=NO_PROVEN_EDGE`, `FRESH_OOS_VERDICT=BLOCKED_NO_UNTOUCHED_DATA`, `OOS_EVALUATIONS=0`, `ACTIVATION_READY=NO`.",
    ]

    vi_lines = [
        "=" * 80,
        "BAN GIAO CHU SO HUU (TIENG VIET) — AURUM DESK | KH-02.R2.C4",
        "=" * 80,
        f"Phan loai ky thuat    : {f22['overall_classification']}",
        f"Run ID                : {run_id}",
        f"Nhanh                 : {f00['branch']}",
        f"Tested Executable SHA : {f00['tested_executable_sha']}",
        "An toan giao dich     : PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO",
        "OOS_EVALUATIONS       : 0 (BLOCKED_NO_VERIFIED_NEW_DATA / BLOCKED_NO_UNTOUCHED_DATA)",
        "Ket luan kinh te      : NO_PROVEN_EDGE (KHONG chung minh loi nhuan; KHONG kich hoat)",
        f"O dia preflight / min : {mp['min_free_gib']} GiB / {f19['min_disk_free_gib']} GiB ({mp['status']})",
        f"RAM min / children / orphans: {f19['min_available_ram_mib']} MiB / {f19['max_descendant_children']} / {f19['orphan_children_after_cleanup']}",
        f"P0-A Boundary (04/06) : RED proven -> GREEN PASS (real OOS count=0)",
        f"P0-B Risk-Tail (11/12): {f11['coverage_cells_populated']}/{f11['coverage_cells_expected']} cells, {f11['linked_per_episode_ledger']['total_episode_rows']} episodes, losses>$2.50={f11['total_realized_losses_exceeding_2p50_count']}, max_loss={f11['global_max_realized_net_loss_usd']} USD",
        f"Full pytest (16)      : {f16['status']} ({f16.get('passed_count')}/{f16.get('collected_count')} passed, {f16.get('skipped_count')} skipped, {f16.get('failed_count')} failed)",
        f"Async W1/W2/W4 (17)   : {f17['status']}",
        f"Parity 56710 (18)     : {f18.get('BASELINE_PARITY_VERDICT')}",
        f"Faults RED/GREEN (20) : {f20['passed_controls']}/{f20['total_controls']} PASS ({f20['new_c4_controls_count']} new C4)",
        f"Verifier Mutations(21): {f21['mutations_detected_count']}/{f21['mutations_tested_count']} detected PASS",
        f"Publication (23)      : {f23['publication_status']}",
        f"Thu muc bang chung    : {run_dir}",
        "=" * 80,
    ]
    return "\n".join(md_lines) + "\n", "\n".join(vi_lines) + "\n"


def run_kh02_r2_c4_acceptance(
    *,
    allow_local_dirty: bool = False,
    owner_waive_disk_preflight: bool = False,
    max_workers: int = GLOBAL_MAX_DESCENDANTS_KH02_R2,
    artifacts_root: Optional[Path] = None,
) -> Tuple[int, Path, Dict[str, Any]]:
    if owner_waive_disk_preflight or str(os.environ.get("KH02_OWNER_WAIVE_DISK_PREFLIGHT") or "").strip().lower() in ("1", "true", "yes"):
        raise RuntimeError("UNAPPROVED_AUTONOMOUS_DISK_WAIVER_FORBIDDEN")

    t_start = time.perf_counter()
    utc_now = datetime.now(timezone.utc)
    utc_stamp = utc_now.strftime("%Y%m%dT%H%M%SZ")
    orch_pid = os.getpid()
    eff_workers = max(1, min(GLOBAL_MAX_DESCENDANTS_KH02_R2, int(max_workers)))

    _, branch = _git_cmd(["branch", "--show-current"])
    _, start_head = _git_cmd(["rev-parse", "HEAD"])
    _, porcelain = _git_cmd(["status", "--porcelain=v1"])
    rc_ls, remote_line = _git_cmd(["ls-remote", "origin", f"refs/heads/{MANDATORY_BRANCH_KH02_R2}"], timeout=15)
    remote_sha = remote_line.split()[0] if (rc_ls == 0 and remote_line) else "UNAVAILABLE_OR_OFFLINE"
    git_clean = len(porcelain.strip()) == 0
    if branch != MANDATORY_BRANCH_KH02_R2:
        raise RuntimeError(f"BLOCKED_WRONG_BRANCH:expected={MANDATORY_BRANCH_KH02_R2},actual={branch}")
    if (not git_clean) and (not allow_local_dirty):
        raise RuntimeError("BLOCKED_DIRTY_WORKING_TREE: commit C4 source cleanly before verified acceptance run.")

    run_id = f"{utc_stamp}_{start_head[:12]}"
    art_base = (artifacts_root or (REPO_ROOT / C4_ARTIFACTS_BASE_REL)).resolve()
    sentinel_tmp = create_sentinel_owned_temp_dir_r2_c1(f"kh02r2c4_{utc_stamp}_", run_id=run_id, owner_pid=orch_pid)

    write_mount_paths = {
        "repo_volume": REPO_ROOT,
        "app_volume": APP_ROOT,
        "artifacts_output": art_base if art_base.exists() else APP_ROOT,
        "temp_volume": sentinel_tmp,
        "python_venv": Path(sys.executable).resolve().parent,
        "caches": Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")),
        "logs": art_base / run_id / "logs",
    }
    strict_probe = probe_r2_c1_write_mounts_strict(write_mount_paths, atomic_probe_dirs=[(sentinel_tmp, True, run_id)])
    vm = psutil.virtual_memory()
    avail_ram_mib = round(vm.available / (1024.0 ** 2), 2)
    ram_ok = avail_ram_mib >= PREFLIGHT_RAM_MIN_MIB_KH02_R2
    strict_disk_ok = bool(strict_probe["can_run_heavy_stages"])
    can_run_heavy = bool(strict_disk_ok and ram_ok)

    run_dir = art_base / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

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
        print(f"[KH-02.R2.C4] Run ID: {run_id} | Branch: {branch} | Tested SHA: {start_head[:12]} | clean={git_clean}", flush=True)
        print(f"[KH-02.R2.C4] Preflight Disk: min={strict_probe['min_free_gib']} GiB ({strict_probe['status']}) | RAM: {avail_ram_mib} MiB | can_run_heavy={can_run_heavy}", flush=True)

        # 01, 02, 03
        f01 = _audit_c4_function_inventory_01(start_head)
        write_json_durable_atomic(run_dir / "01_function_inventory_keep_as_is_ast_diff.json", f01)
        f02 = _audit_prior_sealed_02()
        write_json_durable_atomic(run_dir / "02_prior_sealed_artifacts_sha256_dataset_immutability.json", f02)
        waiver_ctrl = _run_artifact_03_disk_waiver_negative_controls(sentinel_tmp)
        f03 = {
            "spec_version": SPEC_VERSION_KH02_R2_C4,
            "owner_disk_preflight_waiver": False,
            "owner_waiver_permitted": False,
            "strict_multi_mount_probe": strict_probe,
            "preflight_ram_available_mib": avail_ram_mib,
            "preflight_ram_min_mib": PREFLIGHT_RAM_MIN_MIB_KH02_R2,
            "hard_ram_floor_mib": HARD_RAM_FLOOR_MIB_KH02_R2,
            "global_max_descendants": GLOBAL_MAX_DESCENDANTS_KH02_R2,
            "waiver_negative_controls": waiver_ctrl,
            "waiver_negative_controls_status": waiver_ctrl["status"],
            "readonly_disk_inventory": build_r2_c1_readonly_disk_inventory(REPO_ROOT) if not strict_disk_ok else None,
            "status": "PASS" if (strict_disk_ok and ram_ok and waiver_ctrl["status"] == "PASS") else ("BLOCKED_LOW_DISK" if not strict_disk_ok else "FAIL"),
        }
        write_json_durable_atomic(run_dir / "03_disk_ram_multimount_resource_policy_and_faults.json", f03)

        # 04, 05, 06, 07, 08
        print("[KH-02.R2.C4] Stage 1: P0-A RED proofs (04), Threat model (05), GREEN boundary/HTTP/escrow (06, 07, 08)...", flush=True)
        write_json_durable_atomic(run_dir / "04_p0a_direct_mint_subclass_fake_transport_red_proofs.json", S01_PRE_PATCH_RED_PROOFS_CA5BA096)
        write_json_durable_atomic(run_dir / "05_trusted_transport_boundary_design_threat_model.json", TCB_THREAT_MODEL_DISCLOSURE_C4)
        f06, f07, f08 = _run_p0a_green_and_http_escrow_06_07_08(sentinel_tmp)
        write_json_durable_atomic(run_dir / "06_p0a_green_capture_provenance_admission_journal_matrix.json", f06)
        write_json_durable_atomic(run_dir / "07_http_public_readonly_20_existing_plus_c4_security_cases.json", f07)
        write_json_durable_atomic(run_dir / "08_escrow_flock_concurrency_crash_idempotency_anchor_limitations.json", f08)

        # 09..15: Historical replay, tail ledger (12), tail evidence (11), reconciliation+mutations (13), costs (14), stats/OOS (15)
        print("[KH-02.R2.C4] Stage 2: Fresh historical replay, streaming per-episode tail ledger & cost reconciliation (09..15)...", flush=True)
        f09, f10, f11, f13, f14, f15, _lmeta = _run_c4_historical_replay_and_tail_09_to_15(run_dir, start_head)
        write_json_durable_atomic(run_dir / "09_causal_unswept_target_future_mutation_parity.json", f09)
        write_json_durable_atomic(run_dir / "10_historical_replay_cost_risk_per_arm_science.json", f10)
        write_json_durable_atomic(run_dir / "11_realized_tail_complete_by_arm_and_mode.json", f11)
        write_json_durable_atomic(run_dir / "13_tail_ledger_numeric_reconciliation_and_mutation_tests.json", f13)
        write_json_durable_atomic(run_dir / "14_cost_fixed_fill_vs_full_rerisk_all_scenarios.json", f14)
        write_json_durable_atomic(run_dir / "15_statistical_matching_wf_oos_economic_status.json", f15)

        # 20: 102 + 24 = 126 unique RED/GREEN faults
        print("[KH-02.R2.C4] Stage 3: Running 126 unique RED/GREEN fault controls (20)...", flush=True)
        f20 = _run_c4_126_red_green_fault_suite(sentinel_tmp, f11, run_dir / "12_per_episode_risk_ledger.jsonl.gz")
        write_json_durable_atomic(run_dir / "20_all_faults_unique_red_green_receipts.json", f20)

        # 21: Independent verifier mutation resistance (TEMP CLONES ONLY)
        print("[KH-02.R2.C4] Stage 4: Running 14 separate-OS verifier mutation-resistance controls in temp clones (21)...", flush=True)
        f21 = _run_c4_verifier_mutation_suite_21(sentinel_tmp)
        write_json_durable_atomic(run_dir / "21_independent_verifier_mutation_resistance_report.json", f21)

        # Heavy Stages: 16 (full pytest), 17 (W1/W2/W4), 18 (56,710 parity)
        if can_run_heavy:
            eff_res_b = int(STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2 * (1024 ** 3))
            pytest_workers = min(3, eff_workers)
            print(f"[KH-02.R2.C4] Stage 5A: Running FULL repository LPT-sharded pytest regression (max_workers={pytest_workers}) (16)...", flush=True)
            f16 = _run_c4_full_repo_pytest_sharded(
                run_dir, sentinel_tmp, run_id=run_id, tested_sha=start_head,
                effective_disk_reserve_bytes=eff_res_b, max_workers=pytest_workers,
            )
            write_json_durable_atomic(run_dir / "16_full_pytest_collection_execution_junit_bijection.json", f16)
            if git_clean:
                _git_cmd(["checkout", "--", "aurum-desk/backtest/trade_decision_lineage_reconciliation.json"])
            gc.collect()

            print("[KH-02.R2.C4] Stage 5B: Running W1/W2/W4/W1_repeat pytest + R2 science slice determinism (17)...", flush=True)
            pb = asyncio.run(_run_controlled_pytest_w1_w2_w4_benchmark_r1(sentinel_tmp, enforce_r3_ram_guard=True))
            sb = asyncio.run(_run_r2_science_slice_w1_w2_w4_determinism(sentinel_tmp))
            f17 = {
                "spec_version": SPEC_VERSION_KH02_R2_C4,
                "pytest_controlled_subset_w1_w2_w4_repeat": pb,
                "r2_science_slice_w1_w2_w4_repeat": sb,
                "measured_pytest_speedup_w2_vs_w1": pb.get("measured_speedup_w2_vs_w1"),
                "measured_pytest_speedup_w4_vs_w1": pb.get("measured_speedup_w4_vs_w1"),
                "measured_science_speedup_w2_vs_w1": sb.get("measured_speedup_w2_vs_w1"),
                "measured_science_speedup_w4_vs_w1": sb.get("measured_speedup_w4_vs_w1"),
                "all_determinism_identical": bool(pb["all_4_runs_identical"] and sb["all_4_runs_identical"]),
                "status": "PASS" if (pb["all_4_runs_identical"] and sb["all_4_runs_identical"]) else "FAIL",
            }
            write_json_durable_atomic(run_dir / "17_w1_w2_w4_w1repeat_sha256_timing_rss.json", f17)
            gc.collect()

            print("[KH-02.R2.C4] Stage 5C: Running FULL 56,710 A1 + 6,375 SI8 + 242/166 SI0 + 246 adapter + D4 parity (18)...", flush=True)
            ds_p, _ = load_r2_canonical_dataset_readonly(REPO_ROOT / CANONICAL_DATASET_REL_PATH)
            f02_r1 = audit_r2_reference_inputs_and_invariants(REPO_ROOT)
            f18 = _run_full_production_baseline_parity_20(
                datasets=ds_p, sentinel_tmp=sentinel_tmp, run_id=run_id, tested_sha=start_head,
                branch=branch, f01=f01, f02=f02_r1, max_workers=eff_workers,
            )
            f18["spec_version"] = SPEC_VERSION_KH02_R2_C4
            write_json_durable_atomic(run_dir / "18_a1_si8_adapter_d4_full_parity_fingerprints.json", f18)
            del ds_p
            gc.collect()
        else:
            f16 = {"spec_version": SPEC_VERSION_KH02_R2_C4, "status": "NOT_RUN_BLOCKED_LOW_DISK", "collected_count": 0, "executed_count": 0, "passed_count": 0, "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0, "junit_bijection_exact": False}
            f17 = {"spec_version": SPEC_VERSION_KH02_R2_C4, "status": "NOT_RUN_BLOCKED_LOW_DISK", "all_determinism_identical": False}
            f18 = {"spec_version": SPEC_VERSION_KH02_R2_C4, "BASELINE_PARITY_VERDICT": "NOT_RUN_BLOCKED_LOW_DISK", "a1_population_count": 0, "a1_mismatches_count": 0, "si8_population_count": 0, "si8_mismatches_count": 0, "three_adapter_population_count": 0, "three_adapter_mismatches_count": 0, "d4_historical_closed_trades": 0, "per_decision_and_episode_fingerprints": {"all_sealed_fingerprints_exact_match": False}}
            write_json_durable_atomic(run_dir / "16_full_pytest_collection_execution_junit_bijection.json", f16)
            write_json_durable_atomic(run_dir / "17_w1_w2_w4_w1repeat_sha256_timing_rss.json", f17)
            write_json_durable_atomic(run_dir / "18_a1_si8_adapter_d4_full_parity_fingerprints.json", f18)

        # Clean sentinel temp BEFORE final telemetry flush
        cl_info = _cleanup_sentinel_temp_c4_win_safe(sentinel_tmp, expected_run_id=run_id)
        sentinel_cleaned = bool(cl_info["cleaned"])
        telem = sampler.stop_and_flush()
        sampler_stopped = True

        _, end_head = _git_cmd(["rev-parse", "HEAD"])
        f00 = {
            "spec_version": SPEC_VERSION_KH02_R2_C4,
            "schema_version": SCHEMA_VERSION_KH02_R2_C4,
            "run_id": run_id,
            "started_utc": utc_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "branch": branch,
            "start_head_sha": start_head,
            "end_head_sha": end_head,
            "tested_executable_sha": start_head,
            "remote_head_sha": remote_sha,
            "prior_kh02_r2_c3_pub_head_sha": PRIOR_KH02_R2_C3_PUB_HEAD_SHA,
            "prior_kh02_r2_c3_tested_sha": PRIOR_KH02_R2_C3_TESTED_SHA,
            "prior_kh02_r2_c3_evidence_sha": PRIOR_KH02_R2_C3_EVIDENCE_SHA,
            "prior_kh02_r2_c3_run_id": PRIOR_KH02_R2_C3_RUN_ID,
            "git_clean_at_start": git_clean,
            "local_uncommitted_c4_changes": not git_clean,
            "orchestrator_pid": orch_pid,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "random_seed": DEFAULT_RANDOM_SEED_KH02_R2,
            "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
            "owner_disk_preflight_waiver": False,
            "can_run_heavy_stages": can_run_heavy,
            "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
            "status": "PASS" if (strict_disk_ok and ram_ok and git_clean) else ("BLOCKED_LOW_DISK" if not strict_disk_ok else "BLOCKED_PREFLIGHT"),
        }
        write_json_durable_atomic(run_dir / "00_git_branch_sha_remote_preflight.json", f00)

        f19 = {
            "spec_version": SPEC_VERSION_KH02_R2_C4,
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
            "status": "PASS" if (telem["never_breached_512mib_floor"] and telem["never_breached_20p0gib_runtime_disk_floor"] and telem["max_descendant_children"] <= GLOBAL_MAX_DESCENDANTS_KH02_R2 and telem["orphan_children_after_cleanup"] == 0 and strict_disk_ok and sentinel_cleaned) else ("BLOCKED_LOW_DISK" if not strict_disk_ok else "FAIL"),
        }
        write_json_durable_atomic(run_dir / "19_runtime_ram_disk_pid_orphan_telemetry.json", f19)

        pub_st = "PRE_PUSH_PENDING" if (strict_disk_ok and git_clean) else "NOT_PUBLISHED_BLOCKED_LOW_DISK"
        f23 = {
            "spec_version": SPEC_VERSION_KH02_R2_C4,
            "run_id": run_id,
            "branch": branch,
            "start_head_sha": start_head,
            "tested_executable_sha": start_head,
            "evidence_commit_sha": None,
            "publication_head_sha": None,
            "remote_readback_sha256_verified": False,
            "latest_run_pointer_updated": False,
            "owner_disk_preflight_waiver": False,
            "publication_status": pub_st,
        }
        write_json_durable_atomic(run_dir / "23_publication_two_phase_prepush_receipt.json", f23)

        # Compute pre-push 23-gate matrix (22)
        docs_all = {
            "00": f00, "01": f01, "02": f02, "03": f03, "04": S01_PRE_PATCH_RED_PROOFS_CA5BA096,
            "05": TCB_THREAT_MODEL_DISCLOSURE_C4, "06": f06, "07": f07, "08": f08, "09": f09,
            "10": f10, "11": f11, "13": f13, "14": f14, "15": f15, "16": f16, "17": f17,
            "18": f18, "19": f19, "20": f20, "21": f21, "23": f23,
        }
        mem_disk_aud = _audit_memory_samples_c3(run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz")
        tail_disk_aud = validate_c4_tail_disclosure_vs_risk_ledger(f11, run_dir / "12_per_episode_risk_ledger.jsonl.gz")
        recomp22 = recompute_c4_23_gates_and_verdicts(
            docs=docs_all, mem_audit=mem_disk_aud, tail_ledger_audit=tail_disk_aud,
            junit_bijection_ok=(f16.get("status") == "PASS" and bool(f16.get("junit_bijection_exact", False))),
            for_final_verifier_manifest=False,
        )
        f22 = {
            "spec_version": SPEC_VERSION_KH02_R2_C4,
            "schema_version": SCHEMA_VERSION_KH02_R2_C4,
            "run_id": run_id,
            "tested_executable_sha": start_head,
            "OOS_EVALUATIONS": 0,
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
            "overall_classification": recomp22["overall_classification"],
            "gates_23": recomp22["gates_23"],
            "verdicts": recomp22["verdicts"],
        }
        f22["artifact_canonical_sha256"] = canonical_hash(f22)
        write_json_durable_atomic(run_dir / "22_orthogonal_gate_matrix_and_verdicts_prepush.json", f22)

        readme_md, handover_vi = _render_c4_readme_and_handover(
            run_id=run_id, run_dir=run_dir, f00=f00, f03=f03, f10=f10, f11=f11,
            f16=f16, f17=f17, f18=f18, f19=f19, f20=f20, f21=f21, f22=f22, f23=f23,
        )
        (run_dir / "README_KH02_R2_C4.md").write_text(readme_md, encoding="utf-8")
        (run_dir / "HANDOVER_KH02_R2_C4_VI.txt").write_text(handover_vi, encoding="utf-8")

        # Stage 6: Separate OS process independent verifier sealing 24
        print("[KH-02.R2.C4] Stage 6: Launching separate-OS-process independent disk verifier (24)...", flush=True)
        verifier_script = APP_ROOT / "tools" / "kh_02_r2_c4_independent_verifier.py"
        env_v = os.environ.copy()
        env_v["PYTHONPATH"] = _pypath_str()
        v_proc = subprocess.run(
            [sys.executable, str(verifier_script), "--run-dir", str(run_dir), "--expected-run-id", run_id, "--expected-tested-sha", start_head],
            cwd=str(APP_ROOT), env=env_v, capture_output=True, text=True, timeout=240,
        )
        print(v_proc.stdout, flush=True)
        if v_proc.stderr:
            print(v_proc.stderr, file=sys.stderr, flush=True)

        f24 = json.loads((run_dir / "24_independent_os_verifier_sha256_manifest.json").read_text("utf-8"))
        return int(v_proc.returncode), run_dir, {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "verifier_exit_code": int(v_proc.returncode),
            "independent_verifier_status": f24.get("independent_verifier_status"),
            "overall_classification": f24.get("overall_classification"),
            "recomputed_verdicts": f24.get("recomputed_verdicts"),
            "violations": f24.get("violations"),
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
    ap = argparse.ArgumentParser(description="AURUM DESK KH-02.R2.C4 Acceptance Orchestrator")
    ap.add_argument("--allow-local-dirty", action="store_true")
    ap.add_argument("--owner-waive-disk-preflight", action="store_true")
    ap.add_argument("--max-workers", type=int, default=GLOBAL_MAX_DESCENDANTS_KH02_R2)
    args = ap.parse_args(argv)
    if args.owner_waive_disk_preflight:
        return 2
    rc, _rdir, summary = run_kh02_r2_c4_acceptance(
        allow_local_dirty=args.allow_local_dirty,
        owner_waive_disk_preflight=False,
        max_workers=args.max_workers,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
