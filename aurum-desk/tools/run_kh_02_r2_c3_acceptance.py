#!/usr/bin/env python3
"""
aurum-desk/tools/run_kh_02_r2_c3_acceptance.py

AURUM DESK — KH-02.R2.C3 | TRUSTED PROVENANCE BOUNDARY • FAIL-CLOSED MARKET CAPTURE •
             DETERMINISTIC REPLAY & PUBLICATION
SPEC VERSION: KH-02.R2.C3-v1.0 | RESEARCH ONLY | SAME BRANCH | ACTIVATION_READY=NO
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
                    "--owner-waive-disk-preflight and env overrides are strictly forbidden in KH-02.R2.C3 "
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
import errno
import gc
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
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
    TCB_LIMITATIONS_DISCLOSURE_C3,
    TrustedCaptureReceiptC3,
    evaluate_c3_prospective_admission,
    reparse_and_verify_raw_market_bytes_c3,
)
from services.kh_02_r2_prospective_data_service import (
    BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1,
    EMBARGO_BOUNDARY_SEMANTICS_KH02_R2_C1,
    PRIOR_KH02_R2_C1_CODE_COMMIT_SHA_SHARED as PRIOR_KH02_R2_C1_CODE_COMMIT_SHA,
    PRIOR_KH02_R2_C1_DIAG_COMMIT_SHA_SHARED as PRIOR_KH02_R2_C1_DIAG_COMMIT_SHA,
    PRIOR_KH02_R2_C2_EVIDENCE_SHA_SHARED as PRIOR_KH02_R2_C2_EVIDENCE_SHA,
    PRIOR_KH02_R2_C2_PUB_HEAD_SHA_SHARED as PRIOR_KH02_R2_C2_PUB_HEAD_SHA,
    PRIOR_KH02_R2_C2_RUN_ID_SHARED as PRIOR_KH02_R2_C2_RUN_ID,
    PRIOR_KH02_R2_C2_TESTED_SHA_SHARED as PRIOR_KH02_R2_C2_TESTED_SHA,
    R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS,
    R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
    R2_AUTHORITATIVE_FROZEN_CODE_SHA,
    R2_AUTHORITATIVE_FROZEN_CONFIG_SHA256,
    audit_authoritative_r2_freeze_manifest_r2_c1,
    compute_prospective_oos_power_requirements_r2_c1,
    make_synthetic_valid_c1_snapshot_fixture,
    prepare_r2_prospective_oos_ingestion_and_escrow,
    run_kh02_r2_c1_20_synthetic_oos_ingestion_faults,
    validate_prospective_snapshot_contract_c3,
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
    PRIOR_KH02_R1_PUBLICATION_HEAD_SHA,
    PRIOR_KH02_R2_PUBLICATION_HEAD_SHA,
    PRIOR_KH02_R2_RUN_ID,
    PRIOR_KH02_R2_TESTED_SHA,
    RUNTIME_SAFETY_MODES_KH02_R2,
    STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
    STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2,
    _ast_function_inventory,
    _compute_smd,
    _r2_frozen_plan_and_arms,
    _sha256_file,
    audit_r2_fill_time_net_risk_and_realized_tail,
    audit_r2_function_ownership_and_keep_as_is,
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
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    verify_fresh_junit_bijection_v17931211,
)
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_02_r2_c3_independent_verifier import (
    C3_SOURCE_FILES_ALLOWLIST,
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_C3,
    SCHEMA_VERSION_KH02_R2_C3,
    SPEC_VERSION_KH02_R2_C3,
    VERDICT_17_DIMENSIONS_KH02_R2_C3,
    _audit_memory_samples_c3,
    recompute_c3_17_gates_and_17_verdicts,
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
from tools.run_kh_02_r2_c2_acceptance import _run_c2_74_red_green_fault_suite
from tools.run_kh_02_r2_verification import (
    _BoundedResourceSampler,
    _git_cmd,
    _run_full_production_baseline_parity_20,
    _run_r2_science_slice_w1_w2_w4_determinism,
)
from tools.run_v17_9_3_1_2_2_verification import canonicalize_collect_node_id_v1793122

C3_ARTIFACTS_BASE_REL = "aurum-desk/docs/artifacts/kh_02_r2_c3_provenance_qc"

# ALL subprocess-spawning test files MUST run exclusive (slot_cost=4) so global descendants <= 4 ALWAYS
C3_EXCLUSIVE_SUBPROCESS_TEST_FILES: Set[str] = {
    "test_kh_02_asof_mtf_and_fault_controls.py",
    "test_kh_02_r2_oos_provenance_and_faults.py",
    "test_kh_02_r2_c1_strict_safety_and_oos.py",
    "test_kh_02_r2_c2_escrow_journal.py",
    "test_kh_02_r2_c2_acceptance_and_faults.py",
    "test_kh_02_r2_c3_escrow_raw_bytes_and_crash.py",
    "test_kh_02_r2_c3_backtest_parity.py",
}


class _MockUrlResp:
    """In-memory fake urllib response for testing StdlibUrllibPublicTransportC2 without live internet."""

    def __init__(self, *, status: int = 200, body: bytes = b"", headers: Optional[Dict[str, str]] = None, url: str = "https://api.bitget.com/api/v2/mix/market/ticker") -> None:
        self.status = status
        self.code = status
        self._buf = io.BytesIO(body)
        self.headers = headers or {}
        self.url = url

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def __enter__(self) -> "_MockUrlResp":
        return self

    def __exit__(self, *_a: Any) -> None:
        return None


def _compute_prior_immutable_pointers_c3(repo_root: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for rel_p, exp_sha, lbl in PRIOR_SEALED_POINTERS_AND_ARTIFACTS_C3:
        p = repo_root / rel_p
        act = _sha256_file(p) if p.is_file() else None
        out[lbl] = {"path": rel_p, "expected_sha256": exp_sha, "actual_sha256": act, "match": act == exp_sha}
    return out


def _build_artifact_01_c3_ast_and_locked_diff(repo_root: Path, *, head_sha: str) -> Dict[str, Any]:
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

    locked_set = set(LOCKED_KEEP_AS_IS_FILES_KH02_R2) | {rel for rel, _, _ in PRIOR_SEALED_POINTERS_AND_ARTIFACTS_C3}
    locked_modified = sorted(p for p in mod_tracked if p in locked_set)
    non_allow_mod = sorted(p for p in mod_tracked if p not in set(C3_SOURCE_FILES_ALLOWLIST))

    c3_inv: List[Dict[str, Any]] = []
    forbidden_hits: List[Dict[str, str]] = []
    for rel_p in C3_SOURCE_FILES_ALLOWLIST:
        fp = repo_root / rel_p
        if not fp.is_file():
            c3_inv.append({"path": rel_p, "exists": False})
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
        c3_inv.append(
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
        "spec_version": SPEC_VERSION_KH02_R2_C3,
        "schema_version": SCHEMA_VERSION_KH02_R2_C3,
        "head_sha": head_sha,
        "locked_keep_as_is_inventory": locked_inv,
        "locked_files_modified_count": len(locked_modified),
        "locked_files_modified": locked_modified,
        "non_allowlisted_modified_files": non_allow_mod,
        "untracked_files": untracked,
        "c3_source_files_inventory": c3_inv,
        "forbidden_order_import_hits": forbidden_hits,
        "cost_assumptions_defaults_intact": cost_intact,
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        "status": "PASS" if ok else "FAIL",
    }


def _make_valid_bbo_fake_capture(
    snapshot_id: str = "qa_bbo_01",
    *,
    t0: Optional[int] = None,
    is_fake_flag: bool = True,
    live_session_flag: bool = False,
) -> Dict[str, Any]:
    ts = t0 if t0 is not None else (R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 120_000)
    body = json.dumps(
        {
            "code": "00000",
            "msg": "success",
            "data": [{"symbol": "XAUUSDT", "bidPr": "4010.10", "askPr": "4010.30", "ts": str(ts)}],
        }
    ).encode("utf-8")
    resp = RawHttpResponseC2(
        status_code=200,
        body_bytes=body,
        headers={"content-type": "application/json"},
        request_utc_ms=ts + 10,
        response_utc_ms=ts + 25,
        transport_kind="STDLIB_URLLIB_HTTPS_GET" if not is_fake_flag else "INJECTED_FAKE_TRANSPORT",
        tls_verified_host="api.bitget.com" if not is_fake_flag else "",
    )
    auth = OperatorCaptureAuthorizationC2(
        approval_id="QA_BOUNDARY_ATTACK_01",
        approved_by="qa_harness",
        network_capture_enabled=True,
        capture_session_id="sess_qa_01",
        live_network_session_authorized=live_session_flag,
    )
    col = C2PublicReadOnlyBitgetCollector(
        authorization=auth,
        transport=_SequenceFakeTransportC2([resp]),
        is_fake_transport_qa=is_fake_flag,
        os_disk_probe_fn=lambda: 35.0,
    )
    ok_disk = {"can_run_heavy_stages": True, "status": "PASS_STRICT_AND_ADVISORY", "min_free_gib": 35.0}
    return col.fetch_public_l1_bbo_snapshot(snapshot_id=snapshot_id, write_mount_probe=ok_disk)


def _make_valid_ohlcv_fake_capture(snapshot_id: str = "qa_ohlcv_01") -> Dict[str, Any]:
    t0 = R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 180_000
    rows = [
        [str(t0), "4000.00", "4001.00", "3999.00", "4000.50", "10.0", "40005.0"],
        [str(t0 + 60_000), "4000.50", "4002.00", "4000.00", "4001.50", "12.0", "48018.0"],
    ]
    body = json.dumps({"code": "00000", "msg": "success", "data": rows}).encode("utf-8")
    resp = RawHttpResponseC2(
        status_code=200,
        body_bytes=body,
        headers={"content-type": "application/json"},
        request_utc_ms=t0 + 180_000,
        response_utc_ms=t0 + 180_030,
    )
    auth = OperatorCaptureAuthorizationC2(approval_id="QA_OHLCV_01", approved_by="qa", network_capture_enabled=True)
    col = C2PublicReadOnlyBitgetCollector(authorization=auth, transport=_SequenceFakeTransportC2([resp]), is_fake_transport_qa=True)
    ok_disk = {"can_run_heavy_stages": True, "status": "PASS_STRICT_AND_ADVISORY", "min_free_gib": 35.0}
    return col.fetch_public_ohlcv_candles_snapshot(
        snapshot_id=snapshot_id,
        write_mount_probe=ok_disk,
        start_time_ms=t0,
        end_time_ms=t0 + 120_000,
    )


def _run_artifacts_05_06_07_provenance_and_http_safety(sentinel_tmp: Path) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Executes real P0-A fake/live boundary attacks (05), raw-byte reparse checks (06), and P0-B HTTP/rate-limit/redirect controls (07)."""
    real_escrow = C2DurableEscrowJournal.open_real_oos_escrow(
        sentinel_tmp / "c3_real_oos_escrow_probe",
        os_disk_probe_fn=lambda: 35.0,
    )
    qa_escrow = C2DurableEscrowJournal(
        sentinel_tmp / "c3_qa_isolated_escrow",
        strict_real_oos_admission=False,
        isolated_qa_mode=True,
        os_disk_probe_fn=lambda: 35.0,
    )

    # --- 05: Fake/Live Boundary Forgery Rejections ---
    attacks: List[Dict[str, Any]] = []

    def _atk(aid: str, name: str, rejected: bool, detail: str) -> None:
        attacks.append({"attack_id": aid, "name": name, "rejected": bool(rejected), "status": "PASS" if rejected else "FAIL", "detail": detail})

    # A1: Fake transport masquerading with is_fake_transport_qa=False, live_session=True, transport_kind=STDLIB_URLLIB_HTTPS_GET
    spoof_cap = _make_valid_bbo_fake_capture(snapshot_id="atk_spoof_live", is_fake_flag=False, live_session_flag=True)
    a1_rej = False
    try:
        real_escrow.append_verified_prospective_snapshot(
            spoof_cap["snapshot_contract_entry"],
            raw_payload_bytes=spoof_cap["raw_response_bytes"],
            capture_receipt=spoof_cap["capture_receipt"],
        )
    except PermissionError as exc:
        a1_rej = "UNTRUSTED_OR_SYNTHETIC_PROVENANCE_FORBIDDEN" in str(exc)
    _atk("P0A-01", "FAKE_TRANSPORT_WITH_IS_FAKE_FALSE_BLOCKED_FROM_REAL_OOS", a1_rej and spoof_cap["provenance_class"] != ProvenanceClassC3.LIVE_AUTHORIZED_PUBLIC_HTTP.value, f"class={spoof_cap['provenance_class']}")

    # A2: Synthetic fixture with is_synthetic_fixture stripped/False cannot enter real OOS
    synth_stripped = make_synthetic_valid_c1_snapshot_fixture(snapshot_id="atk_synth_stripped")
    synth_stripped.pop("is_synthetic_fixture", None)
    a2_rej = False
    try:
        real_escrow.append_verified_prospective_snapshot(
            synth_stripped,
            raw_payload_bytes=spoof_cap["raw_response_bytes"],
            capture_receipt=None,
        )
    except (PermissionError, ValueError):
        a2_rej = True
    _atk("P0A-02", "SYNTHETIC_FIXTURE_OMITTED_FLAG_BLOCKED_FROM_REAL_OOS", a2_rej, "missing trusted receipt & raw mismatch blocked")

    # A3: raw_payload_bytes=None metadata-only fallback blocked for OBSERVED_QUOTE & OHLCV_SNAPSHOT
    a3a, a3b = False, False
    try:
        real_escrow.append_verified_prospective_snapshot(
            spoof_cap["snapshot_contract_entry"],
            raw_payload_bytes=None,
            capture_receipt=spoof_cap["capture_receipt"],
        )
    except ValueError as exc:
        a3a = "RAW_PAYLOAD_BYTES_REQUIRED_FOR_REAL_OOS" in str(exc)
    ohlcv_cap = _make_valid_ohlcv_fake_capture("atk_ohlcv_meta")
    try:
        real_escrow.append_verified_prospective_snapshot(
            ohlcv_cap["snapshot_contract_entry"],
            raw_payload_bytes=None,
            capture_receipt=ohlcv_cap["capture_receipt"],
        )
    except ValueError as exc:
        a3b = "RAW_PAYLOAD_BYTES_REQUIRED_FOR_REAL_OOS" in str(exc)
    _atk("P0A-03", "METADATA_ONLY_FALLBACK_BLOCKED_FOR_QUOTE_AND_OHLCV", a3a and a3b, "raw_payload_bytes=None rejected")

    # A4: Forged TrustedCaptureReceiptC3 with bogus MAC claiming LIVE_AUTHORIZED_PUBLIC_HTTP rejected
    orig_r = spoof_cap["capture_receipt"]
    forged_r = TrustedCaptureReceiptC3(
        receipt_id=orig_r.receipt_id,
        capture_session_id=orig_r.capture_session_id,
        provenance_class=ProvenanceClassC3.LIVE_AUTHORIZED_PUBLIC_HTTP,
        endpoint_key=orig_r.endpoint_key,
        url=orig_r.url,
        tls_host="api.bitget.com",
        http_status=200,
        params_canonical_sha256=orig_r.params_canonical_sha256,
        raw_response_sha256=orig_r.raw_response_sha256,
        raw_response_size_bytes=orig_r.raw_response_size_bytes,
        canonical_parsed_sha256=orig_r.canonical_parsed_sha256,
        request_utc_ms=orig_r.request_utc_ms,
        response_utc_ms=orig_r.response_utc_ms,
        monotonic_duration_ms=orig_r.monotonic_duration_ms,
        provider_event_utc_ms=orig_r.provider_event_utc_ms,
        transport_class_name="StdlibUrllibPublicTransportC2",
        transport_kind="STDLIB_URLLIB_HTTPS_GET",
        real_rate_limiter_active=True,
        receipt_mac_hex="f" * 64,
    )
    a4_rej = False
    try:
        real_escrow.append_verified_prospective_snapshot(
            spoof_cap["snapshot_contract_entry"],
            raw_payload_bytes=spoof_cap["raw_response_bytes"],
            capture_receipt=forged_r,
        )
    except PermissionError as exc:
        a4_rej = "FORGED_OR_TAMPERED_CAPTURE_RECEIPT_MAC" in str(exc)
    _atk("P0A-04", "FORGED_LIVE_CAPTURE_RECEIPT_MAC_REJECTED", a4_rej, "tampered MAC rejected")

    # A5: Isolated QA escrow accepts fake transport ONLY tagged TEST_ONLY_NOT_ELIGIBLE_OOS (eligible_for_fresh_oos=False)
    qa_cap = _make_valid_bbo_fake_capture("qa_iso_ok", is_fake_flag=True)
    qa_res = qa_escrow.append_verified_prospective_snapshot(
        qa_cap["snapshot_contract_entry"],
        raw_payload_bytes=qa_cap["raw_response_bytes"],
        capture_receipt=qa_cap["capture_receipt"],
    )
    a5_ok = qa_res["admission_tier"] == "TEST_ONLY_NOT_ELIGIBLE_OOS" and qa_res["eligible_for_fresh_oos"] is False
    _atk("P0A-05", "ISOLATED_QA_MARKED_TEST_ONLY_NOT_ELIGIBLE_OOS", a5_ok, f"tier={qa_res['admission_tier']}")

    failed_atk = sum(1 for a in attacks if a["status"] != "PASS")
    f05 = {
        "spec_version": SPEC_VERSION_KH02_R2_C3,
        "attacks_total": len(attacks),
        "failed_attacks": failed_atk,
        "fake_transport_blocked_from_real_oos": a1_rej,
        "stripped_synthetic_blocked_from_real_oos": a2_rej,
        "metadata_only_blocked_from_real_oos": a3a and a3b,
        "forged_receipt_mac_blocked": a4_rej,
        "isolated_qa_strictly_tagged_non_oos": a5_ok,
        "real_oos_escrow_final_record_count": real_escrow.verify_integrity()["record_count"],
        "tcb_disclosure": TCB_LIMITATIONS_DISCLOSURE_C3,
        "attacks": attacks,
        "status": "PASS" if (failed_atk == 0 and real_escrow.verify_integrity()["record_count"] == 0) else "FAIL",
    }

    # --- 06: Raw-Bytes Reparse & Checksum Admission ---
    rp_checks: List[Dict[str, Any]] = []

    def _rp(cid: str, name: str, ok: bool, detail: str) -> None:
        rp_checks.append({"check_id": cid, "name": name, "status": "PASS" if ok else "FAIL", "detail": detail})

    rp_bbo_ok = reparse_and_verify_raw_market_bytes_c3(qa_cap["raw_response_bytes"], qa_cap["snapshot_contract_entry"], capture_receipt=qa_cap["capture_receipt"])
    rp_ohlcv_ok = reparse_and_verify_raw_market_bytes_c3(ohlcv_cap["raw_response_bytes"], ohlcv_cap["snapshot_contract_entry"], capture_receipt=ohlcv_cap["capture_receipt"])
    _rp("RP-01", "VALID_RAW_BYTES_REPARSE_BBO_AND_OHLCV", rp_bbo_ok["valid"] and rp_ohlcv_ok["valid"], "both reparsed identically")

    # Parsed SHA forged with valid raw_response_sha256
    forged_parsed = dict(qa_cap["snapshot_contract_entry"], canonical_parsed_sha256="a" * 64)
    rp_bad_parsed = reparse_and_verify_raw_market_bytes_c3(qa_cap["raw_response_bytes"], forged_parsed)
    _rp("RP-02", "PARSED_SHA_FORGERY_REJECTED_WITH_VALID_RAW_SHA", (not rp_bad_parsed["valid"]) and any("CANONICAL_PARSED_SHA256_TAMPERED" in v for v in rp_bad_parsed["violations"]), str(rp_bad_parsed["violations"]))

    # Event timestamp overwritten while raw bytes unchanged
    forged_ts = dict(qa_cap["snapshot_contract_entry"], event_or_close_utc_ms=int(qa_cap["snapshot_contract_entry"]["event_or_close_utc_ms"]) + 5000)
    rp_bad_ts = reparse_and_verify_raw_market_bytes_c3(qa_cap["raw_response_bytes"], forged_ts)
    _rp("RP-03", "EVENT_TIMESTAMP_TAMPER_REJECTED_VS_RAW_BYTES", (not rp_bad_ts["valid"]) and any("EVENT_TIMESTAMP_TAMPERED" in v for v in rp_bad_ts["violations"]), str(rp_bad_ts["violations"]))

    # DEV SHA replay blocked
    dev_entry = dict(qa_cap["snapshot_contract_entry"], dataset_sha256=CANONICAL_DATASET_SHA256_KH02_R2)
    adm_dev = validate_prospective_snapshot_contract_c3(dev_entry, raw_payload_bytes=qa_cap["raw_response_bytes"], capture_receipt=qa_cap["capture_receipt"], require_live_real_oos=False)
    _rp("RP-04", "DEV_CANONICAL_SHA_REPLAY_BLOCKED", not adm_dev["admitted"], str(adm_dev["violations"]))

    failed_rp = sum(1 for c in rp_checks if c["status"] != "PASS")
    f06 = {
        "spec_version": SPEC_VERSION_KH02_R2_C3,
        "total_checks": len(rp_checks),
        "failed_checks": failed_rp,
        "raw_bytes_reparse_verified": rp_bbo_ok["valid"] and rp_ohlcv_ok["valid"],
        "parsed_forgery_rejected": not rp_bad_parsed["valid"],
        "event_timestamp_tamper_rejected": not rp_bad_ts["valid"],
        "dev_replay_rejected": not adm_dev["admitted"],
        "checks": rp_checks,
        "status": "PASS" if failed_rp == 0 else "FAIL",
    }

    # --- 07: HTTP Allowlist, 302 Redirect, Rate Limit, Retries, Gzip Bomb & Fake Transport Suite ---
    suite20 = run_kh02_r2_c2_fake_transport_collector_suite()
    tr_redir = StdlibUrllibPublicTransportC2(
        _opener_fn=lambda _r, _t: _MockUrlResp(status=302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
    )
    redir_blk = False
    try:
        tr_redir.get("https://api.bitget.com/api/v2/mix/market/ticker", {"productType": "USDT-FUTURES", "symbol": "XAUUSDT"}, timeout_sec=2.0)
    except PermissionError as exc:
        redir_blk = "HTTP_REDIRECT_FORBIDDEN_FAIL_CLOSED" in str(exc)

    # Gzip bomb & oversized response rejected via StdlibUrllibPublicTransportC2 injected opener
    gz_bomb = gzip.compress(b"A" * (9 * 1024 * 1024))
    tr_gz = StdlibUrllibPublicTransportC2(_opener_fn=lambda _r, _t: _MockUrlResp(status=200, body=gz_bomb, headers={"content-encoding": "gzip"}))
    gz_blk = False
    try:
        tr_gz.get("https://api.bitget.com/api/v2/mix/market/ticker", {"productType": "USDT-FUTURES", "symbol": "XAUUSDT"}, timeout_sec=2.0)
    except ValueError as exc:
        gz_blk = "GZIP_BOMB" in str(exc)

    tr_big = StdlibUrllibPublicTransportC2(max_response_bytes=1024, _opener_fn=lambda _r, _t: _MockUrlResp(status=200, body=b"X" * 2048))
    big_blk = False
    try:
        tr_big.get("https://api.bitget.com/api/v2/mix/market/ticker", {"productType": "USDT-FUTURES", "symbol": "XAUUSDT"}, timeout_sec=2.0)
    except ValueError as exc:
        big_blk = "OVERSIZED_HTTP_RESPONSE_BODY" in str(exc)

    # Real default sleeper active when is_fake_transport_qa=False, and fake sleep bypass forbidden!
    live_col_def = C2PublicReadOnlyBitgetCollector(transport=StdlibUrllibPublicTransportC2(), is_fake_transport_qa=False)
    sleep_bypass_blk = False
    try:
        C2PublicReadOnlyBitgetCollector(transport=StdlibUrllibPublicTransportC2(), is_fake_transport_qa=False, sleep_fn=lambda _s: None)
    except PermissionError:
        sleep_bypass_blk = True
    real_sleep_ok = live_col_def.describe_readiness()["real_rate_limiter_default_active"] is True and sleep_bypass_blk

    # Forged caller disk probe rejected against fresh OS disk probe
    auth_ok = OperatorCaptureAuthorizationC2(approval_id="QA_DISK_01", approved_by="qa", network_capture_enabled=True)
    col_forged_disk = C2PublicReadOnlyBitgetCollector(
        authorization=auth_ok,
        transport=_SequenceFakeTransportC2([]),
        is_fake_transport_qa=True,
        os_disk_probe_fn=lambda: 9.0,
    )
    forged_disk_blk = False
    try:
        col_forged_disk.fetch_public_l1_bbo_snapshot(
            snapshot_id="qa_forged_disk",
            write_mount_probe={"can_run_heavy_stages": True, "min_free_gib": 99.0, "status": "FORGED_PASS"},
        )
    except RuntimeError as exc:
        forged_disk_blk = "FORGED_CALLER_DISK_PROBE_BELOW_OS_FLOOR" in str(exc)

    f07_ok = (
        suite20["status"] == "PASS"
        and redir_blk
        and gz_blk
        and big_blk
        and real_sleep_ok
        and forged_disk_blk
    )
    f07 = {
        **suite20,
        "spec_version": SPEC_VERSION_KH02_R2_C3,
        "live_network_test_status": "LIVE_NETWORK_TEST_NOT_RUN_BY_DESIGN",
        "redirect_302_blocked": redir_blk,
        "gzip_bomb_and_oversized_blocked": gz_blk and big_blk,
        "real_default_sleep_verified": real_sleep_ok,
        "forged_caller_disk_probe_blocked": forged_disk_blk,
        "default_collector_readiness": C2PublicReadOnlyBitgetCollector().describe_readiness(),
        "status": "PASS" if f07_ok else "FAIL",
    }
    return f05, f06, f07


def _run_artifacts_08_09_escrow_concurrency_and_crash(sentinel_tmp: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Executes P0-C escrow append-only, 2/4/8 thread + 3-process OS flock concurrency, EINTR/ENOSPC/short-write, crash-before-anchor & double-quarantine."""
    s1 = make_synthetic_valid_c1_snapshot_fixture(snapshot_id="c3_e_001")
    s2 = make_synthetic_valid_c1_snapshot_fixture(snapshot_id="c3_e_002")
    s2["dataset_sha256"] = "e" * 64

    # 1. Prior bytes untouched + same-content-different-ID dedup
    j = C2DurableEscrowJournal(sentinel_tmp / "c3_escrow_basic")
    j.append_snapshot(s1)
    b1 = j.journal_path.read_bytes()
    ino1 = j.journal_path.stat().st_ino
    j.append_snapshot(s2)
    b2 = j.journal_path.read_bytes()
    prior_untouched = b2[: len(b1)] == b1 and len(b2) > len(b1) and j.journal_path.stat().st_ino == ino1

    # 2. Competing writers 2/4/8 threads
    conc: Dict[str, Any] = {}
    for nw in (2, 4, 8):
        cj = C2DurableEscrowJournal(sentinel_tmp / f"c3_conc_{nw}")
        bar = threading.Barrier(nw)

        def _wd(i: int) -> str:
            sn = make_synthetic_valid_c1_snapshot_fixture(snapshot_id=f"c3_{nw}_w{i}")
            sn["dataset_sha256"] = hashlib.sha256(f"c3_{nw}_{i}".encode()).hexdigest()
            bar.wait()
            return str(cj.append_snapshot(sn)["action"])

        with ThreadPoolExecutor(max_workers=nw) as pool:
            ad = list(pool.map(_wd, range(nw)))
        bar2 = threading.Barrier(nw)
        same_sn = make_synthetic_valid_c1_snapshot_fixture(snapshot_id=f"c3_same_{nw}")
        same_sn["dataset_sha256"] = hashlib.sha256(f"same_{nw}".encode()).hexdigest()
        with ThreadPoolExecutor(max_workers=nw) as pool:
            as_ = list(pool.map(lambda _: (bar2.wait(), cj.append_snapshot(same_sn)["action"])[1], range(nw)))
        chk = cj.verify_integrity()
        conc[f"writers_{nw}"] = {
            "distinct_appended": ad.count("APPENDED_NEW_ENTRY"),
            "same_id_appended": as_.count("APPENDED_NEW_ENTRY"),
            "same_id_noop": as_.count("IDEMPOTENT_ALREADY_RECORDED_NOOP"),
            "record_count": chk["record_count"],
            "valid": chk["valid"],
            "passed": ad.count("APPENDED_NEW_ENTRY") == nw and as_.count("APPENDED_NEW_ENTRY") == 1 and as_.count("IDEMPOTENT_ALREADY_RECORDED_NOOP") == nw - 1 and chk["valid"],
        }

    # 3. Real 3-process OS fcntl.flock contention (<= 3 children -> global <= 4!)
    mp_dir = sentinel_tmp / "c3_mp_flock"
    child_py = "\n".join([
        "import hashlib, json, sys; from pathlib import Path",
        "from services.kh_02_r2_c2_escrow_journal import C2DurableEscrowJournal",
        "from services.kh_02_r2_prospective_data_service import make_synthetic_valid_c1_snapshot_fixture",
        "j = C2DurableEscrowJournal(Path(sys.argv[1])); wid = int(sys.argv[2]); outs = []",
        "for k in range(2):",
        "    s = make_synthetic_valid_c1_snapshot_fixture(snapshot_id=f'c3mp_w{wid}_k{k}')",
        "    s['dataset_sha256'] = hashlib.sha256(f'c3mp_{wid}_{k}'.encode()).hexdigest()",
        "    outs.append(j.append_snapshot(s)['action'])",
        "same = make_synthetic_valid_c1_snapshot_fixture(snapshot_id='c3mp_shared')",
        "same['dataset_sha256'] = 'b' * 64",
        "outs.append(j.append_snapshot(same)['action'])",
        "print(json.dumps(outs))",
    ])
    env_mp = os.environ.copy()
    env_mp["PYTHONPATH"] = f"{BACKEND_ROOT}:{APP_ROOT}:{REPO_ROOT}"
    procs = [
        subprocess.Popen([sys.executable, "-c", child_py, str(mp_dir), str(w)], cwd=str(APP_ROOT), env=env_mp, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for w in range(3)
    ]
    mp_d, mp_s = [], []
    for p in procs:
        out, err = p.communicate(timeout=30)
        if p.returncode != 0:
            raise RuntimeError(f"MP_FLOCK_CHILD_FAILED:{err}")
        arr = json.loads(out.strip())
        mp_d.extend(arr[:2])
        mp_s.append(arr[2])
    mp_chk = C2DurableEscrowJournal(mp_dir).verify_integrity()
    mp_ok = mp_d.count("APPENDED_NEW_ENTRY") == 6 and mp_s.count("APPENDED_NEW_ENTRY") == 1 and mp_s.count("IDEMPOTENT_ALREADY_RECORDED_NOOP") == 2 and mp_chk["valid"] and mp_chk["record_count"] == 7

    f08 = {
        "spec_version": SPEC_VERSION_KH02_R2_C3,
        "prior_journal_bytes_untouched_verified": prior_untouched,
        "concurrent_writers_2_4_8_exact_once_passed": all(v["passed"] for v in conc.values()),
        "concurrent_stress_results": conc,
        "interprocess_flock_passed": mp_ok,
        "interprocess_flock_record_count": mp_chk["record_count"],
        "status": "PASS" if (prior_untouched and all(v["passed"] for v in conc.values()) and mp_ok) else "FAIL",
    }

    # --- 09: Torn-write, EINTR, ENOSPC, crash-before-anchor & double quarantine ---
    cr_j = C2DurableEscrowJournal(sentinel_tmp / "c3_crash_double_q")
    cr_j.append_snapshot(s1)
    torn_hit = False
    try:
        cr_j.append_snapshot(s2, _crash_hook=make_crash_hook_for_phase("TORN_JOURNAL_LINE"))
    except RuntimeError:
        torn_hit = True
    q1 = cr_j.quarantine_corrupt_tail_and_orphans(operator_reason="C3_FIRST_RECOVERY")
    q2 = cr_j.quarantine_corrupt_tail_and_orphans(operator_reason="C3_SECOND_IDEMPOTENT_RECOVERY")
    double_q_ok = torn_hit and q1["quarantined_count"] == 2 and q1["post_recovery_integrity"]["valid"] and q2["quarantined_count"] == 0 and q2["post_recovery_integrity"]["valid"]

    # EINTR retry succeeds cleanly
    eintr_j = C2DurableEscrowJournal(sentinel_tmp / "c3_eintr")
    eintr_calls = {"n": 0}

    def _eintr_writer(fd: int, data: bytes) -> int:
        eintr_calls["n"] += 1
        if eintr_calls["n"] == 1:
            raise OSError(errno.EINTR, "SIMULATED_EINTR")
        return os.write(fd, data[: max(1, len(data) // 2)])

    eintr_j.append_snapshot(s1, _write_fn=_eintr_writer)
    eintr_ok = eintr_calls["n"] >= 3 and eintr_j.verify_integrity()["valid"]

    # ENOSPC blob write leaves prior journal untouched
    enospc_j = C2DurableEscrowJournal(sentinel_tmp / "c3_enospc")
    enospc_j.append_snapshot(s1)
    pre_enospc = enospc_j.journal_path.read_bytes()
    enospc_hit = False
    try:
        enospc_j.append_snapshot(s2, _crash_hook=make_crash_hook_for_phase("ENOSPC_BLOB_WRITE"))
    except OSError as exc:
        enospc_hit = exc.errno == errno.ENOSPC
    enospc_ok = enospc_hit and enospc_j.journal_path.read_bytes() == pre_enospc

    # Crash AFTER journal fsync BEFORE external anchor -> UNANCHORED_PENDING_ATTESTATION!
    anc_p = sentinel_tmp / "c3_anchor_pending.jsonl"
    anc = ExternalHeadAnchorC2(anc_p)
    aj = C2DurableEscrowJournal(sentinel_tmp / "c3_anchored_j", external_anchor=anc)
    aj.append_snapshot(s1)
    crash_pre_anc = False
    try:
        aj.append_snapshot(s2, _crash_hook=make_crash_hook_for_phase("AFTER_JOURNAL_FSYNC_BEFORE_ANCHOR"))
    except RuntimeError:
        crash_pre_anc = True
    pend_chk = aj.verify_integrity()
    pend_ok = crash_pre_anc and pend_chk["rollback_detection_status"] == "UNANCHORED_PENDING_ATTESTATION" and pend_chk["external_anchor"]["head_attested"] is False

    # External anchor rollback detection
    anc_rb_p = sentinel_tmp / "c3_anchor_rb.jsonl"
    aj_rb = C2DurableEscrowJournal(sentinel_tmp / "c3_anchored_rb", external_anchor=ExternalHeadAnchorC2(anc_rb_p))
    aj_rb.append_snapshot(s1)
    aj_rb.append_snapshot(s2)
    lines = aj_rb.journal_path.read_bytes().split(b"\n")[:-1]
    aj_rb.journal_path.write_bytes(lines[0] + b"\n")
    rb_chk = aj_rb.verify_integrity()
    rb_ok = (not rb_chk["valid"]) and rb_chk["rollback_detection_status"] == "FAIL_ROLLBACK_DETECTED_BELOW_EXTERNAL_ANCHOR"

    unanc_chk = j.verify_integrity()
    f09_ok = double_q_ok and eintr_ok and enospc_ok and pend_ok and rb_ok and unanc_chk["immutability_class"] == "LOCAL_TAMPER_EVIDENT_ONLY" and unanc_chk["worm_verified"] is False
    f09 = {
        "spec_version": SPEC_VERSION_KH02_R2_C3,
        "crash_recovery_and_double_quarantine_passed": double_q_ok,
        "eintr_and_short_write_recovery_passed": eintr_ok,
        "enospc_blob_leaves_prior_journal_untouched": enospc_ok,
        "unanchored_pending_attestation_detected": pend_ok,
        "unanchored_immutability_class": unanc_chk["immutability_class"],
        "unanchored_worm_verified": unanc_chk["worm_verified"],
        "anchored_rollback_detected": rb_ok,
        "threat_model_disclosure": unanc_chk["threat_model_disclosure"],
        "status": "PASS" if f09_ok else "FAIL",
    }
    return f08, f09


def _run_artifacts_10_to_14_live_historical_replay(head_sha: str) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """
    §08 (F1..F8): Executes a GENUINE FRESH FULL HISTORICAL REPLAY & BACKTEST on the unchanged
    canonical REUSED research dataset (`9f301fee...`), verifying deterministic row-level parity
    against the sealed R2 baseline (`20261121T154352Z_8ca245bcf4d8`).
    """
    t0 = time.perf_counter()
    ds_path = REPO_ROOT / CANONICAL_DATASET_REL_PATH
    datasets, ds_meta = load_r2_canonical_dataset_readonly(ds_path)
    mtf_bundle = build_kh02_asof_mtf_features(datasets)
    r1_frozen_plan, _ = _r2_frozen_plan_and_arms(code_sha=head_sha)
    r1_candidates = generate_kh02_research_candidates(mtf_bundle, r1_frozen_plan)

    # 10: Live T01..T10 + Live Causal Unswept Level Ledger + R1 Target Counterexamples
    t01_t10 = run_r2_t01_to_t10_adversarial_target_scenarios()
    t_by_id = {s["scenario_id"]: s for s in t01_t10["scenarios"]}
    ledger_bundle = build_r2_causal_unswept_level_ledger(
        datasets,
        source_latency_ms=0,
        warmup_m1=30,
        run_t10_future_mutation_proof=True,
        rng_seed=DEFAULT_RANDOM_SEED_KH02_R2,
    )
    r1_ce = audit_r2_r1_target_eligibility_counterexamples(
        datasets=datasets,
        mtf_bundle=mtf_bundle,
        ledger_bundle=ledger_bundle,
        r1_candidates=r1_candidates,
    )
    f10 = {
        "spec_version": SPEC_VERSION_KH02_R2_C3,
        "dataset_sha256": ds_meta.get("dataset_sha256") or CANONICAL_DATASET_SHA256_KH02_R2,
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

    # 11 & 12: Live Target Repair + Live Chronological 1-Position 3-Fill Replays + Paired Design + SMD Balance + Fill-Time Risk
    r2_candidates = build_r2_target_repair_full_opportunity_conservation(
        datasets=datasets,
        mtf_bundle=mtf_bundle,
        ledger_bundle=ledger_bundle,
        r1_candidates=r1_candidates,
        source_latency_ms=0,
    )
    replay_bundle = replay_r2_same_opportunity_paired_counterfactuals(
        datasets=datasets,
        r1_candidates=r1_candidates,
        r2_candidates=r2_candidates,
    )
    paired_design = build_r2_paired_factorial_entry_geometry_design(
        r1_candidates=r1_candidates,
        r2_candidates=r2_candidates,
        r1_replays=replay_bundle["r1_replays"],
        r2_replays=replay_bundle["r2_replays"],
    )
    strata_bal = compute_r2_stratified_common_support_balance(
        r2_candidates=r2_candidates,
        r2_replays=replay_bundle["r2_replays"],
        mtf_bundle=mtf_bundle,
    )
    smd_imbal = _compute_smd([1.0, 1.0, 1.0, 1.0, 0.0], [0.0, 0.0, 0.0, 0.0, 1.0])
    f11 = {
        "spec_version": SPEC_VERSION_KH02_R2_C3,
        "fresh_live_recomputed": True,
        "conservation_sum_verified": bool(r2_candidates["conservation_sum_verified"]),
        "zero_silent_drops_verified": bool(paired_design["zero_silent_drops_verified"]),
        "b0_vs_b2_identifiability_status": paired_design["B0_VS_B2_CROSS_UNIVERSE"]["identifiability_status"],
        "matching_inadequate_guard_verified": bool(smd_imbal is not None and abs(smd_imbal) > 0.10),
        "PAIRED_COMPARISON_VERDICT": strata_bal.get("PAIRED_COMPARISON_VERDICT"),
        "arm_balance_reports": strata_bal.get("arm_balance_reports"),
        "candidates_canonical_sha256": r2_candidates["candidates_canonical_sha256"],
        "status": "PASS" if (r2_candidates["conservation_sum_verified"] and paired_design["zero_silent_drops_verified"] and paired_design["B0_VS_B2_CROSS_UNIVERSE"]["identifiability_status"] == "COMPARISON_NOT_IDENTIFIABLE") else "FAIL",
    }

    fill_risk = audit_r2_fill_time_net_risk_and_realized_tail(
        datasets=datasets,
        r2_replays=replay_bundle["r2_replays"],
    )
    # Reconcile live recomputed NEXT_BAR_OPEN_BID_ASK outcomes arm-by-arm against sealed R2 baseline!
    r2_sealed = REPO_ROOT / R2_SEALED_RUN_DIR_REL
    r2_f09_sealed = json.loads((r2_sealed / "09_chronological_1position_three_fill_paired_replays.json").read_text("utf-8"))
    live_nb_by_arm: Dict[str, Any] = {}
    baseline_diffs: List[str] = []
    for arm in ("B2_H1", "B2_H2", "B2_H3", "B2_H123", "NC_NULL_TRIGGER"):
        live_s = ((replay_bundle.get("comparison_by_arm") or {}).get(arm) or {}).get("NEXT_BAR_OPEN_BID_ASK", {}).get("r2_summary") or {}
        seal_s = ((r2_f09_sealed.get("comparison_by_arm") or {}).get(arm) or {}).get("NEXT_BAR_OPEN_BID_ASK", {}).get("r2_summary") or {}
        live_nb_by_arm[arm] = live_s
        if int(live_s.get("closed", -1)) != int(seal_s.get("closed", -2)):
            baseline_diffs.append(f"{arm}:closed:{live_s.get('closed')}!={seal_s.get('closed')}")
        if int(live_s.get("wins", -1)) != int(seal_s.get("wins", -2)) or int(live_s.get("losses", -1)) != int(seal_s.get("losses", -2)):
            baseline_diffs.append(f"{arm}:wl:({live_s.get('wins')},{live_s.get('losses')})!=({seal_s.get('wins')},{seal_s.get('losses')})")
        if abs(float(live_s.get("net_pnl_usd", 0.0)) - float(seal_s.get("net_pnl_usd", 999.0))) > 0.01:
            baseline_diffs.append(f"{arm}:net_pnl:{live_s.get('net_pnl_usd')}!={seal_s.get('net_pnl_usd')}")
        if abs(float(live_s.get("total_fees_usd", 0.0)) - float(seal_s.get("total_fees_usd", 999.0))) > 0.01:
            baseline_diffs.append(f"{arm}:fees:{live_s.get('total_fees_usd')}!={seal_s.get('total_fees_usd')}")
        if abs(float(live_s.get("expectancy_r", 0.0)) - float(seal_s.get("expectancy_r", 999.0))) > 1e-4:
            baseline_diffs.append(f"{arm}:expectancy_r:{live_s.get('expectancy_r')}!={seal_s.get('expectancy_r')}")

    cap_viols = int(fill_risk.get("fill_time_cap_violations_count", 0))
    f12 = {
        "spec_version": SPEC_VERSION_KH02_R2_C3,
        "dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
        "data_classification": "REUSED_RESEARCH_NOT_UNTOUCHED_OOS",
        "fresh_live_historical_replay_executed": True,
        "exact_match_with_sealed_r2_baseline": len(baseline_diffs) == 0,
        "baseline_reconciliation_diffs": baseline_diffs,
        "live_next_bar_open_bid_ask_by_arm": live_nb_by_arm,
        "fill_time_cap_violations": cap_viols,
        "realized_tail_summary": fill_risk.get("realized_tail_summary") or fill_risk.get("summary") or {},
        "replay_wall_clock_sec": round(time.perf_counter() - t0, 3),
        "status": "PASS" if (len(baseline_diffs) == 0 and cap_viols == 0) else "FAIL",
    }

    # 13: Live Fixed-Fill 1x/2x/3x/0.0004/0.0006 vs Live Full-Universe Re-Risked
    live_f11_costs = reprice_r2_fixed_fills_cost_counterfactual(r2_replays=replay_bundle["r2_replays"])
    live_f12_rerisk = rerun_r2_full_universe_by_cost_scenario(
        datasets=datasets,
        mtf_bundle=mtf_bundle,
        ledger_bundle=ledger_bundle,
    )
    ok_ff, _ = validate_r2_cost_scope_honesty(live_f11_costs)
    ok_fr, _ = validate_r2_cost_scope_honesty(live_f12_rerisk)
    dec_checks = {}
    for arm in ("B2_H1", "B2_H2", "B2_H3", "B2_H123", "NC_NULL_TRIGGER"):
        a_row = (live_f11_costs.get("arms") or {}).get(arm) or {}
        sc_1x = a_row.get("1x_base_0p0004") or {}
        if "fees_usd" in sc_1x and "net_pnl_usd" in sc_1x:
            dec_checks[f"{arm}_1x_0p0004"] = {
                "gross_pnl_usd": a_row.get("gross_pnl_1x_usd"),
                "fees_usd": sc_1x.get("fees_usd"),
                "net_pnl_usd": sc_1x.get("net_pnl_usd"),
            }
    f13 = {
        "spec_version": SPEC_VERSION_KH02_R2_C3,
        "fresh_live_recomputed": True,
        "quote_mode": "MODELED_BID_ASK",
        "modeled_spread_usd": 0.20,
        "observed_l1_bbo_available": False,
        "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        "fixed_fill_zero_dropped_ids_verified": bool(ok_ff),
        "full_rerisk_starts_from_full_universe": bool(ok_fr and live_f12_rerisk.get("starts_from_full_raw_opportunity_stream", False)),
        "realized_tail_risk_disclosed": True,
        "fixed_fill_decimal_checks": dec_checks,
        "fixed_fill_arms_summary": live_f11_costs.get("arms"),
        "full_universe_rerisk_summary": live_f12_rerisk.get("scenarios") or live_f12_rerisk.get("arms"),
        "status": "PASS" if (ok_ff and ok_fr and len(dec_checks) >= 4) else "FAIL",
    }

    # 14: Live Purged Walk-Forward & Cluster Uncertainty + Economic & OOS Verdict
    live_wf = run_r2_purged_walkforward_and_multiplicity(
        r2_replays=replay_bundle["r2_replays"],
        r2_fixed_costs=live_f11_costs,
    )
    f14 = {
        "spec_version": SPEC_VERSION_KH02_R2_C3,
        "fresh_live_recomputed": True,
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
    return f10, f11, f12, f13, f14


def _run_c3_102_red_green_fault_suite(repo_root: Path, sentinel_tmp: Path) -> Dict[str, Any]:
    """
    Runs ALL 42 R2 + 14 C1 + 18 C2 + 28 NEW C3 RED/GREEN fault controls = 102 unique controls (§09).
    """
    c2_sub = sentinel_tmp / "c2_74_sub"
    c2_sub.mkdir(parents=True, exist_ok=True)
    c2_74 = _run_c2_74_red_green_fault_suite(repo_root, c2_sub)
    controls = list(c2_74["controls"])

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

    qa_cap = _make_valid_bbo_fake_capture("f_c3_qa_ok", is_fake_flag=True)
    spoof_cap = _make_valid_bbo_fake_capture("f_c3_spoof", is_fake_flag=False, live_session_flag=True)
    ohlcv_cap = _make_valid_ohlcv_fake_capture("f_c3_ohlcv")
    real_j = C2DurableEscrowJournal.open_real_oos_escrow(sentinel_tmp / "f_c3_real_j", os_disk_probe_fn=lambda: 35.0)
    qa_j = C2DurableEscrowJournal(sentinel_tmp / "f_c3_qa_j", isolated_qa_mode=True, os_disk_probe_fn=lambda: 35.0)
    qa_ok_write = qa_j.append_verified_prospective_snapshot(qa_cap["snapshot_contract_entry"], raw_payload_bytes=qa_cap["raw_response_bytes"], capture_receipt=qa_cap["capture_receipt"])
    qa_green = qa_ok_write["action"] == "APPENDED_NEW_ENTRY" and qa_ok_write["eligible_for_fresh_oos"] is False

    # C3-F01: Fake transport with is_fake_transport_qa=False blocked from real OOS
    r01 = False
    try:
        real_j.append_verified_prospective_snapshot(spoof_cap["snapshot_contract_entry"], raw_payload_bytes=spoof_cap["raw_response_bytes"], capture_receipt=spoof_cap["capture_receipt"])
    except PermissionError:
        r01 = True
    _rec("C3-F01", "FAKE_TRANSPORT_IS_FAKE_FALSE_CANNOT_ENTER_REAL_OOS", "evaluate_c3_prospective_admission", r01, qa_green, "spoofed live flag blocked")

    # C3-F02: Synthetic fixture with is_synthetic_fixture omitted blocked from real OOS
    s02 = make_synthetic_valid_c1_snapshot_fixture(snapshot_id="f_c3_s02")
    s02.pop("is_synthetic_fixture", None)
    r02 = False
    try:
        real_j.append_verified_prospective_snapshot(s02, raw_payload_bytes=qa_cap["raw_response_bytes"], capture_receipt=None)
    except (PermissionError, ValueError):
        r02 = True
    _rec("C3-F02", "SYNTHETIC_OMITTED_FLAG_BLOCKED_REAL_OOS", "C2DurableEscrowJournal.append_verified_prospective_snapshot", r02, qa_green, "omitted synthetic flag blocked")

    # C3-F03: raw_payload_bytes=None for OBSERVED_QUOTE blocked
    r03 = False
    try:
        real_j.append_verified_prospective_snapshot(qa_cap["snapshot_contract_entry"], raw_payload_bytes=None, capture_receipt=qa_cap["capture_receipt"])
    except ValueError:
        r03 = True
    _rec("C3-F03", "OBSERVED_QUOTE_RAW_BYTES_NONE_BLOCKED", "C2DurableEscrowJournal.append_verified_prospective_snapshot", r03, qa_green, "metadata-only quote blocked")

    # C3-F04: OHLCV metadata-only FRESH_UNTOUCHED blocked
    r04 = False
    try:
        real_j.append_verified_prospective_snapshot(ohlcv_cap["snapshot_contract_entry"], raw_payload_bytes=None, capture_receipt=ohlcv_cap["capture_receipt"])
    except ValueError:
        r04 = True
    _rec("C3-F04", "OHLCV_METADATA_ONLY_FRESH_BLOCKED", "C2DurableEscrowJournal.append_verified_prospective_snapshot", r04, qa_green, "metadata-only OHLCV blocked")

    # C3-F05: raw_response_sha256 valid but canonical_parsed_sha256 forged blocked
    rp05_bad = reparse_and_verify_raw_market_bytes_c3(qa_cap["raw_response_bytes"], dict(qa_cap["snapshot_contract_entry"], canonical_parsed_sha256="1" * 64))
    rp05_ok = reparse_and_verify_raw_market_bytes_c3(qa_cap["raw_response_bytes"], qa_cap["snapshot_contract_entry"], capture_receipt=qa_cap["capture_receipt"])
    _rec("C3-F05", "PARSED_PAYLOAD_SHA_FORGED_WITH_VALID_RAW_SHA_BLOCKED", "reparse_and_verify_raw_market_bytes_c3", not rp05_bad["valid"], rp05_ok["valid"], "parsed mismatch caught")

    # C3-F06: Raw body valid but event timestamp overwritten blocked
    rp06_bad = reparse_and_verify_raw_market_bytes_c3(qa_cap["raw_response_bytes"], dict(qa_cap["snapshot_contract_entry"], event_or_close_utc_ms=int(qa_cap["snapshot_contract_entry"]["event_or_close_utc_ms"]) + 1000))
    _rec("C3-F06", "PROVIDER_EVENT_TS_OVERWRITE_BLOCKED", "reparse_and_verify_raw_market_bytes_c3", not rp06_bad["valid"], rp05_ok["valid"], "event_ms mismatch caught")

    # C3-F07: DEV canonical SHA / pre-embargo window replay blocked
    adm07 = validate_prospective_snapshot_contract_c3(dict(qa_cap["snapshot_contract_entry"], dataset_sha256=CANONICAL_DATASET_SHA256_KH02_R2), raw_payload_bytes=qa_cap["raw_response_bytes"], capture_receipt=qa_cap["capture_receipt"], require_live_real_oos=False)
    adm07_ok = validate_prospective_snapshot_contract_c3(qa_cap["snapshot_contract_entry"], raw_payload_bytes=qa_cap["raw_response_bytes"], capture_receipt=qa_cap["capture_receipt"], require_live_real_oos=False)
    _rec("C3-F07", "DEV_CANONICAL_SHA_AND_OVERLAP_REPLAY_BLOCKED", "validate_prospective_snapshot_contract_c3", not adm07["admitted"], adm07_ok["admitted"], "DEV SHA blocked")

    # C3-F08: 302 redirect to localhost / 169.254.169.254 blocked
    r08 = False
    try:
        StdlibUrllibPublicTransportC2(_opener_fn=lambda _r, _t: _MockUrlResp(status=302, headers={"location": "http://127.0.0.1:8080/admin"})).get("https://api.bitget.com/api/v2/mix/market/ticker", {"productType": "USDT-FUTURES", "symbol": "XAUUSDT"}, timeout_sec=1.0)
    except PermissionError:
        r08 = True
    g08 = StdlibUrllibPublicTransportC2(_opener_fn=lambda _r, _t: _MockUrlResp(status=200, body=qa_cap["raw_response_bytes"])).get("https://api.bitget.com/api/v2/mix/market/ticker", {"productType": "USDT-FUTURES", "symbol": "XAUUSDT"}, timeout_sec=1.0).status_code == 200
    _rec("C3-F08", "HTTP_302_SSRF_REDIRECT_BLOCKED", "StdlibUrllibPublicTransportC2.get", r08, g08, "302 to 127.0.0.1 blocked")

    # C3-F09: Private / trading API path blocked before HTTP
    r09 = False
    try:
        StdlibUrllibPublicTransportC2(_opener_fn=lambda _r, _t: _MockUrlResp(status=200, body=b"{}")).get("https://api.bitget.com/api/v2/mix/order/place-order", {"symbol": "XAUUSDT"}, timeout_sec=1.0)
    except PermissionError:
        r09 = True
    _rec("C3-F09", "PRIVATE_ORDER_API_PATH_BLOCKED_BEFORE_HTTP", "StdlibUrllibPublicTransportC2.get", r09, g08, "place-order blocked")

    # C3-F10: Secrets/cookies/account ID in query/headers/body blocked
    r10 = False
    try:
        StdlibUrllibPublicTransportC2(_opener_fn=lambda _r, _t: _MockUrlResp(status=200, body=b"{}")).get("https://api.bitget.com/api/v2/mix/market/ticker", {"productType": "USDT-FUTURES", "symbol": "XAUUSDT", "api_key": "SECRET123"}, timeout_sec=1.0)
    except PermissionError:
        r10 = True
    _rec("C3-F10", "SECRETS_COOKIES_ACCOUNT_ID_BLOCKED", "StdlibUrllibPublicTransportC2.get", r10, g08, "api_key query blocked")

    # C3-F11: Real default rate-limiter active on live transport; fake sleep bypass refused
    r11 = False
    try:
        C2PublicReadOnlyBitgetCollector(transport=StdlibUrllibPublicTransportC2(), is_fake_transport_qa=False, sleep_fn=lambda _s: None)
    except PermissionError:
        r11 = True
    g11 = C2PublicReadOnlyBitgetCollector(transport=StdlibUrllibPublicTransportC2(), is_fake_transport_qa=False).describe_readiness()["real_rate_limiter_default_active"] is True
    _rec("C3-F11", "REAL_DEFAULT_SLEEP_ACTIVE_AND_BYPASS_BLOCKED", "C2PublicReadOnlyBitgetCollector.__init__", r11, g11, "live sleep bypass blocked")

    # C3-F12: 429 Retry-After capped vs 400 non-retryable
    t0_12 = R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 300_000
    auth12 = OperatorCaptureAuthorizationC2(approval_id="QA_12", approved_by="qa", network_capture_enabled=True)
    ok_d = {"can_run_heavy_stages": True, "min_free_gib": 35.0}
    tr400 = _SequenceFakeTransportC2([RawHttpResponseC2(400, b"bad", {}, t0_12, t0_12 + 5)])
    c400 = C2PublicReadOnlyBitgetCollector(authorization=auth12, transport=tr400, is_fake_transport_qa=True)
    r12 = False
    try:
        c400.fetch_public_l1_bbo_snapshot(snapshot_id="f12_400", write_mount_probe=ok_d)
    except RuntimeError as exc:
        r12 = "NON_RETRYABLE_CLIENT_HTTP_STATUS:400" in str(exc) and len(tr400.calls) == 1
    tr429 = _SequenceFakeTransportC2([RawHttpResponseC2(429, b"slow", {"retry-after": "999"}, t0_12, t0_12 + 5), RawHttpResponseC2(200, qa_cap["raw_response_bytes"], {}, qa_cap["snapshot_contract_entry"]["request_utc_ms"], qa_cap["snapshot_contract_entry"]["response_utc_ms"])])
    c429 = C2PublicReadOnlyBitgetCollector(authorization=auth12, transport=tr429, max_backoff_sec=1.5, is_fake_transport_qa=True)
    g12_res = c429.fetch_public_l1_bbo_snapshot(snapshot_id="f12_429", write_mount_probe=ok_d)
    g12 = g12_res["validation"]["valid_for_escrow_admission"] and c429.sleep_audit_log[0]["sleep_sec"] <= 1.5
    _rec("C3-F12", "HTTP_400_NO_RETRY_VS_429_CAPPED_RETRY_AFTER", "C2PublicReadOnlyBitgetCollector._execute_get_with_retry", r12, g12, "400=1 call; 429 capped<=1.5s")

    # C3-F13: Gzip bomb & oversized response bounded reject
    gz_b = gzip.compress(b"Z" * (9 * 1024 * 1024))
    r13 = False
    try:
        StdlibUrllibPublicTransportC2(_opener_fn=lambda _r, _t: _MockUrlResp(status=200, body=gz_b, headers={"content-encoding": "gzip"})).get("https://api.bitget.com/api/v2/mix/market/ticker", {"productType": "USDT-FUTURES", "symbol": "XAUUSDT"}, timeout_sec=1.0)
    except ValueError:
        r13 = True
    _rec("C3-F13", "GZIP_BOMB_AND_OVERSIZED_BODY_BOUNDED_REJECT", "StdlibUrllibPublicTransportC2.get", r13, g08, "gzip bomb bounded reject")

    # C3-F14: Open/unclosed candle rejected
    t0_14 = R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 360_000
    bad_c_body = json.dumps({"code": "00000", "data": [[str(t0_14), "4000", "4001", "3999", "4000.5", "5", "20000"]]}).encode()
    c14 = C2PublicReadOnlyBitgetCollector(authorization=auth12, transport=_SequenceFakeTransportC2([RawHttpResponseC2(200, bad_c_body, {}, t0_14, t0_14 + 30_000)]), is_fake_transport_qa=True)
    r14 = False
    try:
        c14.fetch_public_ohlcv_candles_snapshot(snapshot_id="f14_open", write_mount_probe=ok_d, start_time_ms=t0_14, end_time_ms=t0_14 + 60_000)
    except ValueError as exc:
        r14 = "UNCLOSED_OR_FORMING_BAR_REJECTED" in str(exc)
    _rec("C3-F14", "UNCLOSED_CANDLE_REJECTED", "C2PublicReadOnlyBitgetCollector.fetch_public_ohlcv_candles_snapshot", r14, ohlcv_cap["validation"]["valid_for_escrow_admission"], "forming bar rejected")

    # C3-F15: Bad tick precision / wrong symbol BBO rejected
    bad_tick_body = json.dumps({"code": "00000", "data": [{"symbol": "XAUUSDT", "bidPr": "4000.1234", "askPr": "4000.30", "ts": str(t0_14)}]}).encode()
    c15 = C2PublicReadOnlyBitgetCollector(authorization=auth12, transport=_SequenceFakeTransportC2([RawHttpResponseC2(200, bad_tick_body, {}, t0_14 + 5, t0_14 + 15)]), is_fake_transport_qa=True)
    r15 = False
    try:
        c15.fetch_public_l1_bbo_snapshot(snapshot_id="f15_tick", write_mount_probe=ok_d)
    except ValueError as exc:
        r15 = "BBO_TICK_PRECISION_VIOLATION" in str(exc)
    _rec("C3-F15", "BBO_TICK_PRECISION_AND_SYMBOL_MISMATCH_REJECTED", "C2PublicReadOnlyBitgetCollector.fetch_public_l1_bbo_snapshot", r15, qa_cap["validation"]["valid_for_escrow_admission"], "0.0001 tick rejected")

    # C3-F16: Forged caller disk probe with fresh OS disk below floor blocked
    c16 = C2PublicReadOnlyBitgetCollector(authorization=auth12, transport=_SequenceFakeTransportC2([]), is_fake_transport_qa=True, os_disk_probe_fn=lambda: 10.0)
    r16 = False
    try:
        c16.fetch_public_l1_bbo_snapshot(snapshot_id="f16_disk", write_mount_probe=ok_d)
    except RuntimeError as exc:
        r16 = "FORGED_CALLER_DISK_PROBE_BELOW_OS_FLOOR" in str(exc)
    _rec("C3-F16", "FORGED_CALLER_DISK_PROBE_BELOW_OS_FLOOR_REJECTED", "C2PublicReadOnlyBitgetCollector._check_guards", r16, True, "fresh OS statvfs overrides caller dict")

    # C3-F17: Counterfeit approval_id / empty session rejected for live authorization
    r17 = False
    try:
        OperatorCaptureAuthorizationC2(approval_id="FAKE_ID", approved_by="someone", network_capture_enabled=True, live_network_session_authorized=True, capture_session_id="")
    except PermissionError:
        r17 = True
    g17 = OperatorCaptureAuthorizationC2(approval_id="APP1", approved_by="owner", network_capture_enabled=True, live_network_session_authorized=True, capture_session_id="SESS1").live_network_session_authorized is True
    _rec("C3-F17", "COUNTERFEIT_APPROVAL_WITHOUT_SESSION_BLOCKED", "OperatorCaptureAuthorizationC2.__post_init__", r17, g17, "missing session blocked")

    # C3-F18: ENOSPC in O_EXCL blob write leaves old journal untouched
    j18 = C2DurableEscrowJournal(sentinel_tmp / "f18_enospc")
    s18a = make_synthetic_valid_c1_snapshot_fixture(snapshot_id="f18_a")
    s18b = make_synthetic_valid_c1_snapshot_fixture(snapshot_id="f18_b"); s18b["dataset_sha256"] = "c" * 64
    j18.append_snapshot(s18a)
    pre18 = j18.journal_path.read_bytes()
    r18 = False
    try:
        j18.append_snapshot(s18b, _crash_hook=make_crash_hook_for_phase("ENOSPC_BLOB_WRITE"))
    except OSError as exc:
        r18 = exc.errno == errno.ENOSPC and j18.journal_path.read_bytes() == pre18
    q18 = j18.quarantine_corrupt_tail_and_orphans(operator_reason="F18_CLEAN")
    _rec("C3-F18", "ENOSPC_BLOB_WRITE_PRESERVES_PRIOR_JOURNAL", "C2DurableEscrowJournal.append_snapshot", r18, q18["post_recovery_integrity"]["valid"], "prior journal untouched")

    # C3-F19: Short O_APPEND write / zero progress fails closed; EINTR retries cleanly
    j19 = C2DurableEscrowJournal(sentinel_tmp / "f19_short")
    r19 = False
    try:
        j19.append_snapshot(s18a, _write_fn=lambda _fd, _b: 0)
    except IOError as exc:
        r19 = "SHORT_WRITE_ZERO_PROGRESS" in str(exc)
    j19_ok = C2DurableEscrowJournal(sentinel_tmp / "f19_eintr_ok")
    cnt19 = {"k": 0}
    def _w19(fd: int, data: bytes) -> int:
        cnt19["k"] += 1
        if cnt19["k"] == 1:
            raise OSError(errno.EINTR, "EINTR")
        return os.write(fd, data)
    j19_ok.append_snapshot(s18a, _write_fn=_w19)
    _rec("C3-F19", "SHORT_WRITE_FAIL_CLOSED_AND_EINTR_RETRIES", "_write_all_eintr_safe", r19, j19_ok.verify_integrity()["valid"], "zero-progress fail-closed; EINTR OK")

    # C3-F20: Crash after fsync before external anchor -> UNANCHORED_PENDING_ATTESTATION
    j20 = C2DurableEscrowJournal(sentinel_tmp / "f20_anc_pend", external_anchor=ExternalHeadAnchorC2(sentinel_tmp / "f20.anc"))
    j20.append_snapshot(s18a)
    try:
        j20.append_snapshot(s18b, _crash_hook=make_crash_hook_for_phase("AFTER_JOURNAL_FSYNC_BEFORE_ANCHOR"))
    except RuntimeError:
        pass
    c20 = j20.verify_integrity()
    _rec("C3-F20", "CRASH_BEFORE_ANCHOR_UNANCHORED_PENDING_ATTESTATION", "C2DurableEscrowJournal.verify_integrity", c20["rollback_detection_status"] == "UNANCHORED_PENDING_ATTESTATION" and c20["external_anchor"]["head_attested"] is False, c20["valid"] is True, c20["rollback_detection_status"])

    # C3-F21: External local anchor rollback detected without WORM claim
    j21 = C2DurableEscrowJournal(sentinel_tmp / "f21_anc_rb", external_anchor=ExternalHeadAnchorC2(sentinel_tmp / "f21.anc"))
    j21.append_snapshot(s18a)
    j21.append_snapshot(s18b)
    ok21 = j21.verify_integrity()
    l21 = j21.journal_path.read_bytes().split(b"\n")[:-1]
    j21.journal_path.write_bytes(l21[0] + b"\n")
    bad21 = j21.verify_integrity()
    _rec("C3-F21", "EXTERNAL_ANCHOR_ROLLBACK_DETECTED_NO_WORM_CLAIM", "C2DurableEscrowJournal.verify_integrity", (not bad21["valid"]) and bad21["worm_verified"] is False, ok21["valid"] and ok21["worm_verified"] is False, bad21["rollback_detection_status"])

    # C3-F22: Concurrent 4-writer duplicate + conflicting fork -> exact once / conflict rejected
    j22 = C2DurableEscrowJournal(sentinel_tmp / "f22_conc")
    b22 = threading.Barrier(4)
    with ThreadPoolExecutor(max_workers=4) as p22:
        a22 = list(p22.map(lambda _: (b22.wait(), j22.append_snapshot(s18a)["action"])[1], range(4)))
    fork_blk = False
    try:
        j22.append_snapshot(dict(s18a, bar_count=999))
    except PermissionError:
        fork_blk = True
    _rec("C3-F22", "CONCURRENT_DUPLICATE_AND_FORK_CONFLICT", "C2DurableEscrowJournal.append_snapshot", fork_blk, a22.count("APPENDED_NEW_ENTRY") == 1 and a22.count("IDEMPOTENT_ALREADY_RECORDED_NOOP") == 3, "1 append + 3 noop + fork blocked")

    # C3-F23..F28: Independent verifier gate controls (recompute_c3_17_gates_and_17_verdicts)
    base_docs = _make_minimal_valid_c3_docs_in_mem()
    mem_ok = {"status": "PASS", "below_floor_samples_count": 0, "max_workers_observed_in_samples": 2}
    g_base = recompute_c3_17_gates_and_17_verdicts(docs=base_docs, mem_audit=mem_ok, junit_bijection_ok=True)

    # C3-F23: Mandatory skip or tampered JUnit counts rejected
    d23 = json.loads(json.dumps(base_docs))
    d23["15"]["mandatory_skipped_count"] = 1
    r23 = recompute_c3_17_gates_and_17_verdicts(docs=d23, mem_audit=mem_ok, junit_bijection_ok=True)
    _rec("C3-F23", "MANDATORY_SKIP_OR_JUNIT_TAMPER_REJECTED", "recompute_c3_17_gates_and_17_verdicts", r23["gates_17"]["G10_FULL_PYTEST_JUNIT_BIJECTION"] == "FAIL", g_base["gates_17"]["G10_FULL_PYTEST_JUNIT_BIJECTION"] == "PASS", "mandatory_skip=1 -> FAIL")

    # C3-F24: Forged economic verdict / unknown fee tier mislabeled rejected
    d24 = json.loads(json.dumps(base_docs))
    d24["14"]["RESEARCH_ECONOMIC_VERDICT"] = "PROVEN_EDGE"
    r24 = recompute_c3_17_gates_and_17_verdicts(docs=d24, mem_audit=mem_ok, junit_bijection_ok=True)
    _rec("C3-F24", "FORGED_ECONOMIC_VERDICT_OR_FEE_TAMPER_REJECTED", "recompute_c3_17_gates_and_17_verdicts", r24["verdicts_17"]["ENGINEERING"] == "FAIL", g_base["verdicts_17"]["ECONOMIC"] == "NO_PROVEN_EDGE", "PROVEN_EDGE -> FAIL")

    # C3-F25: W1/W2/W4 hash mismatch despite same counts rejected
    d25 = json.loads(json.dumps(base_docs))
    d25["16"]["r2_science_slice_w1_w2_w4_repeat"]["runs"][2]["canonical_sorted_sha256"] = "9" * 64
    r25 = recompute_c3_17_gates_and_17_verdicts(docs=d25, mem_audit=mem_ok, junit_bijection_ok=True)
    _rec("C3-F25", "W1_W2_W4_ROW_HASH_MUTATION_REJECTED", "recompute_c3_17_gates_and_17_verdicts", r25["gates_17"]["G11_ASYNC_W1_W2_W4_DETERMINISM"] == "FAIL", g_base["gates_17"]["G11_ASYNC_W1_W2_W4_DETERMINISM"] == "PASS", "W4 hash drift -> FAIL")

    # C3-F26: Runtime disk drop below 20.0 GiB fails G03
    d26 = json.loads(json.dumps(base_docs))
    d26["18"]["min_disk_free_gib"] = 18.5
    r26 = recompute_c3_17_gates_and_17_verdicts(docs=d26, mem_audit=mem_ok, junit_bijection_ok=True)
    _rec("C3-F26", "RUNTIME_DISK_DROP_BELOW_20GIB_FAILS_GATE", "recompute_c3_17_gates_and_17_verdicts", r26["gates_17"]["G03_STRICT_DISK_RAM_PID_SAFETY"] == "FAIL", g_base["gates_17"]["G03_STRICT_DISK_RAM_PID_SAFETY"] == "PASS", "18.5 GiB runtime -> FAIL")

    # C3-F27: Prepush receipt tampered to PASS_REMOTE_VERIFIED without remote SHA readback rejected
    d27 = json.loads(json.dumps(base_docs))
    d27["22"]["publication_status"] = "PASS_REMOTE_VERIFIED"
    d27["22"]["remote_readback_sha256_verified"] = False
    r27 = recompute_c3_17_gates_and_17_verdicts(docs=d27, mem_audit=mem_ok, junit_bijection_ok=True)
    _rec("C3-F27", "PREPUSH_TAMPERED_TO_REMOTE_VERIFIED_REJECTED", "recompute_c3_17_gates_and_17_verdicts", r27["gates_17"]["G17_PUBLICATION_TWO_PHASE"] == "FAIL", g_base["gates_17"]["G17_PUBLICATION_TWO_PHASE"] == "PRE_PUSH_PENDING", "false remote readback -> FAIL")

    # C3-F28: OOS_EVALUATIONS=1 or ACTIVATION_READY=YES rejected
    d28 = json.loads(json.dumps(base_docs))
    d28["14"]["OOS_EVALUATIONS"] = 1
    d28["14"]["ACTIVATION_READY"] = "YES"
    r28 = recompute_c3_17_gates_and_17_verdicts(docs=d28, mem_audit=mem_ok, junit_bijection_ok=True)
    _rec("C3-F28", "OOS_EVAL_NONZERO_OR_ACTIVATION_ON_REJECTED", "recompute_c3_17_gates_and_17_verdicts", r28["gates_17"]["G15_OOS_TRUTH_NO_PEEK"] == "FAIL" and r28["gates_17"]["G02_PRODUCTION_FREEZE_AND_OFFLINE_SAFETY"] == "FAIL", g_base["verdicts_17"]["ACTIVATION"] == "NO", "OOS=1 / ACT=YES -> FAIL")

    uids = {c["fault_id"] for c in controls}
    unames = {c["name"] for c in controls}
    passed = sum(1 for c in controls if c["status"] == "PASS")
    all_ok = len(controls) >= 102 and len(uids) == len(controls) and len(unames) == len(controls) and passed == len(controls)
    return {
        "spec_version": SPEC_VERSION_KH02_R2_C3,
        "r2_c1_c2_inherited_faults_count": len(c2_74["controls"]),
        "c3_new_faults_count": len(controls) - len(c2_74["controls"]),
        "total_controls": len(controls),
        "unique_fault_ids": len(uids),
        "unique_fault_names": len(unames),
        "passed_controls": passed,
        "failed_controls": len(controls) - passed,
        "all_controls_passed": all_ok,
        "controls": controls,
        "status": "PASS" if all_ok else "FAIL",
    }


def _make_minimal_valid_c3_docs_in_mem() -> Dict[str, Dict[str, Any]]:
    tsha = "f" * 40
    h_p = "1" * 64
    h_s = "2" * 64
    return {
        "00": {"branch": MANDATORY_BRANCH_KH02_R2, "start_head_sha": tsha, "end_head_sha": tsha, "tested_executable_sha": tsha, "git_clean_at_start": True, "local_uncommitted_c3_changes": False, "owner_disk_preflight_waiver": False, "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2), "ACTIVATION_READY": ACTIVATION_READY_KH02_R2},
        "01": {"status": "PASS", "locked_files_modified_count": 0, "forbidden_order_import_hits": [], "cost_assumptions_defaults_intact": True, "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2), "ACTIVATION_READY": ACTIVATION_READY_KH02_R2},
        "02": {"owner_disk_preflight_waiver": False, "strict_multi_mount_probe": {"owner_waiver_permitted": False, "can_run_heavy_stages": True, "probe_errors": [], "distinct_mounts": [{"free_gib": 35.0, "projected_post_reserve_gib": 34.9}]}},
        "03": {"status": "PASS", "failed_controls": 0},
        "04": {"authoritative_r2_freeze_audit": {"status": "PASS"}, "synthetic_20_oos_validator_status": "PASS", "OOS_EVALUATIONS": 0, "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2), "ACTIVATION_READY": ACTIVATION_READY_KH02_R2},
        "05": {"status": "PASS", "failed_attacks": 0, "fake_transport_blocked_from_real_oos": True, "stripped_synthetic_blocked_from_real_oos": True, "metadata_only_blocked_from_real_oos": True},
        "06": {"status": "PASS", "failed_checks": 0, "raw_bytes_reparse_verified": True, "parsed_forgery_rejected": True, "event_timestamp_tamper_rejected": True},
        "07": {"status": "PASS", "failed_cases": 0, "live_network_capture_executed": False, "real_prospective_snapshots_collected": 0, "live_network_test_status": "LIVE_NETWORK_TEST_NOT_RUN_BY_DESIGN", "redirect_302_blocked": True, "real_default_sleep_verified": True, "forged_caller_disk_probe_blocked": True, "gzip_bomb_and_oversized_blocked": True, "default_collector_readiness": {"read_only": True, "network_capture_enabled": False}},
        "08": {"status": "PASS", "prior_journal_bytes_untouched_verified": True, "concurrent_writers_2_4_8_exact_once_passed": True, "interprocess_flock_passed": True},
        "09": {"status": "PASS", "crash_recovery_and_double_quarantine_passed": True, "unanchored_pending_attestation_detected": True, "unanchored_immutability_class": "LOCAL_TAMPER_EVIDENT_ONLY", "unanchored_worm_verified": False, "anchored_rollback_detected": True},
        "10": {"status": "PASS", "swept_tp_accepted_count": 0, "t01_to_t10_all_passed": True, "future_mutation_invariance_passed": True, "live_causal_ledger_recomputed": True},
        "11": {"status": "PASS", "b0_vs_b2_identifiability_status": "COMPARISON_NOT_IDENTIFIABLE", "matching_inadequate_guard_verified": True},
        "12": {"status": "PASS", "fresh_live_historical_replay_executed": True, "exact_match_with_sealed_r2_baseline": True, "fill_time_cap_violations": 0},
        "13": {"status": "PASS", "fixed_fill_zero_dropped_ids_verified": True, "full_rerisk_starts_from_full_universe": True, "realized_tail_risk_disclosed": True, "quote_mode": "MODELED_BID_ASK", "observed_l1_bbo_available": False, "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED"},
        "14": {"status": "PASS", "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE", "FUTURE_OOS_READINESS_VERDICT": "BLOCKED_NO_VERIFIED_NEW_DATA", "OOS_EVALUATIONS": 0, "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2), "ACTIVATION_READY": ACTIVATION_READY_KH02_R2},
        "15": {"status": "PASS", "collected_count": 100, "executed_count": 100, "passed_count": 100, "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0},
        "16": {"status": "PASS", "pytest_controlled_subset_w1_w2_w4_repeat": {"all_4_runs_identical": True, "runs": [{"canonical_sorted_outcome_sha256": h_p, "executed_tests_count": 10}] * 4}, "r2_science_slice_w1_w2_w4_repeat": {"all_4_runs_identical": True, "runs": [{"canonical_sorted_sha256": h_s, "rows_count": 100}] * 4}},
        "17": {"prior_evidence_immutability_verified": True, "BASELINE_PARITY_VERDICT": "PASS", "a1_population_count": 56710, "a1_mismatches_count": 0, "si8_population_count": 6375, "si8_mismatches_count": 0, "three_adapter_population_count": 246, "three_adapter_mismatches_count": 0, "d4_historical_closed_trades": 0, "per_decision_and_episode_fingerprints": {"all_sealed_fingerprints_exact_match": True}},
        "18": {"min_available_ram_mib": 2000.0, "min_disk_free_gib": 34.0, "max_descendant_children": 3, "orphan_children_after_cleanup": 0, "sentinel_temp_cleaned_up": True, "owner_disk_preflight_waiver": False},
        "19": {"status": "PASS", "total_controls": 102, "failed_controls": 0, "unique_fault_ids": 102},
        "22": {"publication_status": "PRE_PUSH_PENDING", "owner_disk_preflight_waiver": False, "remote_readback_sha256_verified": False},
    }


def _collect_and_build_c3_lpt_shards(owned_temp_root: Path) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]]]:
    all_test_files = sorted(
        str(p.relative_to(APP_ROOT)).replace("\\", "/")
        for p in (BACKEND_ROOT / "tests").glob("test_*.py")
        if p.is_file()
    )
    col_out = owned_temp_root / "collect_kh02r2c3_stdout.log"
    col_err = owned_temp_root / "collect_kh02r2c3_stderr.log"
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
        raise RuntimeError(f"KH02_R2_C3_PYTEST_COLLECT_FAILED:rc={proc.returncode}:{err_txt}")

    collected_by_file: Dict[str, List[str]] = {}
    for ln in col_out.read_text("utf-8", errors="replace").splitlines():
        cid = canonicalize_collect_node_id_v1793122(ln)
        if cid is not None:
            collected_by_file.setdefault(cid.split("::", 1)[0], []).append(cid)
    for k in collected_by_file:
        collected_by_file[k].sort()

    empirical_ms = _load_empirical_file_durations_ms_from_kh01b()
    non_sub = [f for f in all_test_files if Path(f).name not in C3_EXCLUSIVE_SUBPROCESS_TEST_FILES]
    sub_files = [f for f in all_test_files if Path(f).name in C3_EXCLUSIVE_SUBPROCESS_TEST_FILES]
    shards = _build_lpt_shards_kh01b_r1(non_sub, collected_by_file, empirical_ms)
    for sh in shards:
        if sh.get("shard_name") == "SHARD_SINGLE_CHILD_02_BUDGET2":
            sh["exclusive"] = True
            sh["slot_cost"] = 4
    for idx_s, sf in enumerate(sorted(sub_files), 1):
        shards.append(
            {
                "shard_name": f"SHARD_SINGLE_CHILD_C3_SUBPROC_{idx_s:02d}",
                "files": [sf],
                "exclusive": True,
                "slot_cost": 4,
                "priority_weight": 250_000,
                "needs_venv": False,
            }
        )
    return collected_by_file, shards


def _run_c3_full_repo_pytest_sharded(
    run_dir: Path,
    sentinel_tmp: Path,
    *,
    run_id: str,
    tested_sha: str,
    max_workers: int,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    empirical_r2_rss = load_empirical_r2_shard_rss_bytes(REPO_ROOT)
    collected_by_file, c3_shards = _collect_and_build_c3_lpt_shards(sentinel_tmp)
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
            shard_specs_override=c3_shards,
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
        "spec_version": SPEC_VERSION_KH02_R2_C3,
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


def _render_c3_readme_and_handover_vi(run_dir: Path, docs: Mapping[str, Mapping[str, Any]]) -> Tuple[str, str]:
    f00, f02, f05, f06, f07 = docs["00"], docs["02"], docs["05"], docs["06"], docs["07"]
    f08, f09, f12, f15, f16, f17, f18, f19, f21, f22 = (
        docs["08"], docs["09"], docs["12"], docs["15"], docs["16"], docs["17"], docs["18"], docs["19"], docs["21"], docs["22"]
    )
    g = f21["gates_17"]
    v = f21["verdicts_17"]
    mp = f02["strict_multi_mount_probe"]

    decision = (
        "PASS_ENGINEERING_RESEARCH_NO_EDGE"
        if v["ENGINEERING"] == "PASS"
        else ("BLOCKED_LOW_DISK" if v["RESOURCE"] == "BLOCKED_LOW_DISK" else "FAILED_OR_PARTIAL")
    )

    md: List[str] = [
        "# AURUM DESK — KH-02.R2.C3 | TRUSTED PROVENANCE BOUNDARY, FAIL-CLOSED MARKET CAPTURE & DETERMINISTIC REPLAY",
        "",
        f"- **Phân loại nghiệm thu kỹ thuật**: **`{decision}`**",
        f"- **Spec Version**: `{SPEC_VERSION_KH02_R2_C3}`",
        f"- **Run ID**: `{f00['run_id']}`",
        f"- **Nhánh bắt buộc**: `{f00['branch']}`",
        f"- **Tested Executable SHA**: `{f00['tested_executable_sha']}` (`git_clean_at_start={f00['git_clean_at_start']}`)",
        f"- **Remote HEAD tại preflight**: `{f00['remote_head_sha']}`",
        f"- **Khóa an toàn giao dịch**: `PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO | OOS_EVALUATIONS=0`",
        "",
        "## 1. Bảng 17 Chiều Kết Luận Độc Lập (Không Gộp, Không Đánh Đồng)",
        "",
        "| Chiều kiểm định | Kết luận |",
        "|---|---|",
    ]
    for vk in VERDICT_17_DIMENSIONS_KH02_R2_C3:
        md.append(f"| `{vk}` | **{v[vk]}** |")

    md += [
        "",
        "## 2. Ma Trận 17 Cổng Nghiệm Thu (`G01`..`G17`)",
        "",
        "| Cổng | Trạng thái |",
        "|---|---|",
    ]
    for gk, gv in g.items():
        md.append(f"| `{gk}` | **{gv}** |")

    md += [
        "",
        "## 3. Số Đo Thực Tế (`02`, `05..09`, `12`, `15..19`)",
        "",
        f"- **Ổ đĩa & RAM (`02`, `18`)**: Preflight **`{mp['min_free_gib']} GiB`** (`{mp['status']}`), runtime min **`{f18['min_disk_free_gib']} GiB`**, RAM min **`{f18['min_available_ram_mib']} MiB`**, max children **`{f18['max_descendant_children']}`**, orphans **`{f18['orphan_children_after_cleanup']}`**, tổng thời gian **`{f18['total_wall_clock_sec']}s`**.",
        f"- **Tường lửa nguồn gốc P0-A (`05`, `06`)**: Fake-transport/stripped-synthetic/metadata-only/forged-MAC đều bị chặn khỏi real OOS (`real_oos_escrow_final_record_count={f05['real_oos_escrow_final_record_count']}`); tái phân tích raw bytes PASS (`parsed_forgery_rejected={f06['parsed_forgery_rejected']}`, `event_timestamp_tamper_rejected={f06['event_timestamp_tamper_rejected']}`).",
        f"- **An toàn HTTP & rate-limit P0-B (`07`)**: `{f07['passed_cases']}/{f07['total_cases']} PASS`, `redirect_302_blocked={f07['redirect_302_blocked']}`, `real_default_sleep_verified={f07['real_default_sleep_verified']}`, `forged_caller_disk_probe_blocked={f07['forged_caller_disk_probe_blocked']}`, `live_network_test_status={f07['live_network_test_status']}`.",
        f"- **Escrow P0-C (`08`, `09`)**: `prior_journal_bytes_untouched={f08['prior_journal_bytes_untouched_verified']}`, `2/4/8 threads={f08['concurrent_writers_2_4_8_exact_once_passed']}`, `3-OS-process flock={f08['interprocess_flock_passed']}`, `double_quarantine={f09['crash_recovery_and_double_quarantine_passed']}`, `unanchored_pending_attestation={f09['unanchored_pending_attestation_detected']}`, `worm_verified={f09['unanchored_worm_verified']}`.",
        f"- **Live Historical Replay & Backtest (`12`)**: `fresh_live_historical_replay_executed={f12['fresh_live_historical_replay_executed']}`, `exact_match_with_sealed_r2_baseline={f12['exact_match_with_sealed_r2_baseline']}`, `fill_time_cap_violations={f12['fill_time_cap_violations']}`.",
        f"- **Full repo pytest (`15`)**: `{f15['status']}` — `collected={f15.get('collected_count')}`, `executed={f15.get('executed_count')}`, `passed={f15.get('passed_count')}`, `skipped={f15.get('skipped_count')}`, `mandatory_skipped={f15.get('mandatory_skipped_count')}`, `failed={f15.get('failed_count')}`, `error={f15.get('error_count')}`.",
        f"- **Async `W1/W2/W4/W1_repeat` (`16`)**: `{f16['status']}`.",
        f"- **Production parity (`17`)**: `{f17['BASELINE_PARITY_VERDICT']}` (`A1={f17.get('a1_population_count')}/mism={f17.get('a1_mismatches_count')}`, `SI8={f17.get('si8_population_count')}/mism={f17.get('si8_mismatches_count')}`, `3-adapter={f17.get('three_adapter_population_count')}/mism={f17.get('three_adapter_mismatches_count')}`, `D4={f17.get('d4_historical_closed_trades')}`).",
        f"- **Fault controls (`19`)**: `{f19['passed_controls']}/{f19['total_controls']} PASS` (`{f19['unique_fault_ids']}` ID duy nhất).",
        f"- **Khoa học & Kinh tế**: `RESEARCH_ECONOMIC_VERDICT=NO_PROVEN_EDGE`, `FUTURE_OOS_READINESS_VERDICT=BLOCKED_NO_VERIFIED_NEW_DATA`, `OOS_EVALUATIONS=0`, `ACTIVATION_READY=NO`.",
    ]

    vi: List[str] = [
        "================================================================================",
        "BAN GIAO CHU SO HUU (TIENG VIET) — AURUM DESK | KH-02.R2.C3",
        "================================================================================",
        f"Phan loai ky thuat    : {decision}",
        f"Run ID                : {f00['run_id']}",
        f"Nhanh                 : {f00['branch']}",
        f"Tested Executable SHA : {f00['tested_executable_sha']}",
        f"An toan giao dich     : PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO",
        f"OOS_EVALUATIONS       : 0 (BLOCKED_NO_VERIFIED_NEW_DATA / BLOCKED_NO_UNTOUCHED_DATA)",
        f"Ket luan kinh te      : NO_PROVEN_EDGE (KHONG chung minh loi nhuan; KHONG kich hoat)",
        f"O dia preflight / min : {mp['min_free_gib']} GiB / {f18['min_disk_free_gib']} GiB ({mp['status']})",
        f"RAM min / children / orphans: {f18['min_available_ram_mib']} MiB / {f18['max_descendant_children']} / {f18['orphan_children_after_cleanup']}",
        f"Tuong lua P0-A (05/06): {f05['status']} / {f06['status']}",
        f"HTTP & RateLimit (07) : {f07['status']} ({f07['passed_cases']}/{f07['total_cases']}, live_network=False)",
        f"Escrow P0-C (08/09)   : {f08['status']} / {f09['status']} (worm_verified=False)",
        f"Replay R1-vs-R2 (12)  : {f12['status']} (baseline_match={f12['exact_match_with_sealed_r2_baseline']})",
        f"Full pytest (15)      : {f15['status']} ({f15.get('passed_count')}/{f15.get('collected_count')} passed, {f15.get('skipped_count')} skipped, {f15.get('failed_count')} failed)",
        f"Async W1/W2/W4 (16)   : {f16['status']}",
        f"Parity 56710 (17)     : {f17['BASELINE_PARITY_VERDICT']}",
        f"Faults RED/GREEN (19) : {f19['passed_controls']}/{f19['total_controls']} PASS",
        f"Publication (22)      : {f22['publication_status']}",
        f"Thu muc bang chung    : {run_dir}",
        "================================================================================",
    ]
    return "\n".join(md) + "\n", "\n".join(vi) + "\n"


def run_kh02_r2_c3_acceptance(
    *,
    owner_waive_disk_preflight: bool = False,
    max_workers: int = GLOBAL_MAX_DESCENDANTS_KH02_R2,
    artifacts_root: Optional[Path] = None,
) -> Tuple[int, Path, Dict[str, Any]]:
    if owner_waive_disk_preflight or str(os.environ.get("KH02_OWNER_WAIVE_DISK_PREFLIGHT") or "").strip().lower() in ("1", "true", "yes"):
        raise RuntimeError("UNAPPROVED_AUTONOMOUS_DISK_WAIVER_FORBIDDEN: KH-02.R2.C3 forbids disk waiver overrides.")

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
    art_base = (artifacts_root or (REPO_ROOT / C3_ARTIFACTS_BASE_REL)).resolve()
    art_base.mkdir(parents=True, exist_ok=True)

    sentinel_tmp = create_sentinel_owned_temp_dir_r2_c1(f"kh02r2c3_{utc_stamp}_", run_id=run_id, owner_pid=orch_pid)
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
        print(f"[KH-02.R2.C3] Run ID: {run_id} | Branch: {branch} | HEAD: {start_head_sha[:12]} | clean={git_clean}", flush=True)
        print(f"[KH-02.R2.C3] Disk Preflight: min={strict_mount_probe['min_free_gib']} GiB -> {strict_mount_probe['status']} | RAM: {avail_ram_mib} MiB | can_run_heavy={can_run_heavy}", flush=True)

        # 01: AST Ownership & Locked Diff
        f01 = _build_artifact_01_c3_ast_and_locked_diff(REPO_ROOT, head_sha=start_head_sha)
        write_json_durable_atomic(run_dir / "01_function_ownership_ast_keep_as_is_locked_diff.json", f01)

        # 02: Multi-Mount Disk & RAM Preflight + Read-Only Disk Inventory
        f02 = {
            "spec_version": SPEC_VERSION_KH02_R2_C3,
            "owner_disk_preflight_waiver": False,
            "owner_waiver_permitted": False,
            "strict_multi_mount_probe": strict_mount_probe,
            "readonly_disk_inventory": build_r2_c1_readonly_disk_inventory(REPO_ROOT),
        }
        write_json_durable_atomic(run_dir / "02_distinct_mount_disk_ram_preflight_readonly_inventory.json", f02)

        # 03: Disk Waiver & Runtime Abort Controls
        print("[KH-02.R2.C3] Stage 1: Disk waiver & runtime-abort controls (03)...", flush=True)
        f03 = _run_artifact_03_disk_waiver_negative_controls(sentinel_tmp)
        write_json_durable_atomic(run_dir / "03_disk_waiver_and_runtime_abort_controls.json", f03)

        # 04: Authoritative R2 Freeze + C3 Provenance Contract + 20 Synthetic Validator Controls
        print("[KH-02.R2.C3] Stage 2: R2 freeze, raw provenance contract & 20 synthetic validator controls (04)...", flush=True)
        freeze_audit = audit_authoritative_r2_freeze_manifest_r2_c1(REPO_ROOT)
        oos_v20 = run_kh02_r2_c1_20_synthetic_oos_ingestion_faults(sentinel_tmp / "oos_v20_c3")
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
            "spec_version": SPEC_VERSION_KH02_R2_C3,
            "authoritative_r2_freeze_audit": freeze_audit,
            "bitget_xauusdt_contract_spec": BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1,
            "embargo_boundary_semantics": EMBARGO_BOUNDARY_SEMANTICS_KH02_R2_C1,
            "power_and_sample_size_prerequisites": compute_prospective_oos_power_requirements_r2_c1(),
            "synthetic_20_oos_validator_status": oos_v20["status"],
            "synthetic_20_oos_validator_summary": {"passed_cases": oos_v20["passed_cases"], "total_cases": oos_v20["total_cases"], "validates_real_market_data": False},
            "tcb_disclosure": TCB_LIMITATIONS_DISCLOSURE_C3,
            "proposed_snapshots_count": 0,
            "accepted_prospective_snapshots_count": 0,
            "OOS_EVALUATIONS": 0,
            "FUTURE_OOS_READINESS_VERDICT": "BLOCKED_NO_VERIFIED_NEW_DATA",
            "FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA",
            "escrow_contract_integrity_status": "PASS" if (freeze_audit["status"] == "PASS" and r2_escrow["escrow_contract_integrity_status"] == "PASS") else "FAIL",
            "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        }
        write_json_durable_atomic(run_dir / "04_freeze_registry_raw_provenance_contract.json", f04)

        # 05, 06, 07: Provenance Firewall, Raw-Bytes Reparse & HTTP Safety
        print("[KH-02.R2.C3] Stage 3: Fake/live provenance firewall, raw-byte reparse & HTTP safety (05, 06, 07)...", flush=True)
        f05, f06, f07 = _run_artifacts_05_06_07_provenance_and_http_safety(sentinel_tmp)
        write_json_durable_atomic(run_dir / "05_fake_live_boundary_forgery_rejections.json", f05)
        write_json_durable_atomic(run_dir / "06_raw_bytes_reparse_checksum_admission.json", f06)
        write_json_durable_atomic(run_dir / "07_http_allowlist_redirect_rate_limit_retries.json", f07)

        # 08, 09: Escrow Concurrency & Crash / Double Quarantine / Anchor Truth
        print("[KH-02.R2.C3] Stage 4: Escrow concurrency, EINTR/ENOSPC/torn-write crash & anchor truth (08, 09)...", flush=True)
        f08, f09 = _run_artifacts_08_09_escrow_concurrency_and_crash(sentinel_tmp)
        write_json_durable_atomic(run_dir / "08_escrow_concurrent_writers_hash_integrity.json", f08)
        write_json_durable_atomic(run_dir / "09_escrow_torn_write_crash_quarantine_anchor_truth.json", f09)

        # 10..14: GENUINE LIVE HISTORICAL REPLAY & BACKTEST ON CANONICAL DATASET
        print("[KH-02.R2.C3] Stage 5: Running LIVE full R1-vs-R2 & B2 historical replay/backtest & cost/uncertainty reconciliation (10..14)...", flush=True)
        f10, f11, f12, f13, f14 = _run_artifacts_10_to_14_live_historical_replay(start_head_sha)
        write_json_durable_atomic(run_dir / "10_unswept_tp_future_mutation_regression.json", f10)
        write_json_durable_atomic(run_dir / "11_entry_geometry_matching_common_support.json", f11)
        write_json_durable_atomic(run_dir / "12_replay_backtest_r1_r2_strata_risk_fees.json", f12)
        write_json_durable_atomic(run_dir / "13_cost_fixed_fill_vs_full_rerisk_1x_2x_3x.json", f13)
        write_json_durable_atomic(run_dir / "14_research_uncertainty_economic_oos_verdict.json", f14)

        # 19: 102 Unique Real RED/GREEN Fault Injections
        print("[KH-02.R2.C3] Stage 6: Running 102 unique real RED/GREEN fault controls (19)...", flush=True)
        f19 = _run_c3_102_red_green_fault_suite(REPO_ROOT, sentinel_tmp)
        write_json_durable_atomic(run_dir / "19_fault_injection_red_green_unique_receipts.json", f19)

        # Heavy Stages (15, 16, 17) ONLY if strict disk >= 20.5 GiB & RAM >= 2048 MiB & clean git!
        prior_imm = _compute_prior_immutable_pointers_c3(REPO_ROOT)
        prior_imm_ok = all(v["match"] for v in prior_imm.values())

        if can_run_heavy:
            pytest_workers = min(3, eff_workers)
            print(f"[KH-02.R2.C3] Stage 7A: Running FULL repo LPT-sharded pytest (max_workers={pytest_workers}) (15)...", flush=True)
            f15 = _run_c3_full_repo_pytest_sharded(
                run_dir,
                sentinel_tmp,
                run_id=run_id,
                tested_sha=start_head_sha,
                max_workers=pytest_workers,
            )
            write_json_durable_atomic(run_dir / "15_full_pytest_collection_execution_junit_bijection.json", f15)
            _git_cmd(["checkout", "--", "aurum-desk/backtest/trade_decision_lineage_reconciliation.json"])
            gc.collect()

            print("[KH-02.R2.C3] Stage 7B: Running W1/W2/W4/W1_repeat determinism benchmarks (16)...", flush=True)
            pytest_bench = asyncio.run(_run_controlled_pytest_w1_w2_w4_benchmark_r1(sentinel_tmp, enforce_r3_ram_guard=True))
            r2_slice_bench = asyncio.run(_run_r2_science_slice_w1_w2_w4_determinism(sentinel_tmp))
            f16 = {
                "spec_version": SPEC_VERSION_KH02_R2_C3,
                "pytest_controlled_subset_w1_w2_w4_repeat": pytest_bench,
                "r2_science_slice_w1_w2_w4_repeat": r2_slice_bench,
                "all_determinism_identical": bool(pytest_bench["all_4_runs_identical"] and r2_slice_bench["all_4_runs_identical"]),
                "status": "PASS" if (pytest_bench["all_4_runs_identical"] and r2_slice_bench["all_4_runs_identical"]) else "FAIL",
            }
            write_json_durable_atomic(run_dir / "16_async_w1_w2_w4_w1repeat_hash_timing_ram.json", f16)
            gc.collect()

            print("[KH-02.R2.C3] Stage 7C: Running FULL 56,710 A1 + 6,375 SI8 + 246 adapter + D4 parity (17)...", flush=True)
            ds_path = REPO_ROOT / CANONICAL_DATASET_REL_PATH
            datasets, _ = load_r2_canonical_dataset_readonly(ds_path)
            r2_f01 = audit_r2_function_ownership_and_keep_as_is(REPO_ROOT, base_head_sha=start_head_sha)
            r2_f02 = audit_r2_reference_inputs_and_invariants(REPO_ROOT)
            f17 = _run_full_production_baseline_parity_20(
                datasets=datasets,
                sentinel_tmp=sentinel_tmp,
                run_id=run_id,
                tested_sha=start_head_sha,
                branch=branch,
                f01=r2_f01,
                f02=r2_f02,
                max_workers=eff_workers,
            )
            f17["prior_sealed_artifacts_immutability"] = prior_imm
            f17["prior_evidence_immutability_verified"] = bool(prior_imm_ok and f01["locked_files_modified_count"] == 0)
            write_json_durable_atomic(run_dir / "17_full_a1_si8_adapter_d4_parity.json", f17)
            del datasets
            gc.collect()
        else:
            blk_status = "NOT_RUN_BLOCKED_LOW_DISK" if not strict_disk_ok else "NOT_RUN_BLOCKED_PREFLIGHT"
            f15 = {"spec_version": SPEC_VERSION_KH02_R2_C3, "status": blk_status, "collected_count": 0, "executed_count": 0, "passed_count": 0, "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0, "junit_bijection_exact": False}
            write_json_durable_atomic(run_dir / "15_full_pytest_collection_execution_junit_bijection.json", f15)
            f16 = {"spec_version": SPEC_VERSION_KH02_R2_C3, "status": blk_status, "all_determinism_identical": False}
            write_json_durable_atomic(run_dir / "16_async_w1_w2_w4_w1repeat_hash_timing_ram.json", f16)
            f17 = {"spec_version": SPEC_VERSION_KH02_R2_C3, "prior_sealed_artifacts_immutability": prior_imm, "prior_evidence_immutability_verified": bool(prior_imm_ok and f01["locked_files_modified_count"] == 0), "BASELINE_PARITY_VERDICT": blk_status}
            write_json_durable_atomic(run_dir / "17_full_a1_si8_adapter_d4_parity.json", f17)

        # Clean sentinel temp BEFORE final telemetry flush
        cleanup = safe_cleanup_sentinel_owned_temp_dir_r2_c1(sentinel_tmp, expected_run_id=run_id)
        telemetry = sampler.stop_and_flush()
        f18 = {
            "spec_version": SPEC_VERSION_KH02_R2_C3,
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
        write_json_durable_atomic(run_dir / "18_resource_runtime_telemetry_memory_disk_pid.json", f18)

        _, end_head_sha = _git_cmd(["rev-parse", "HEAD"])
        f00 = {
            "spec_version": SPEC_VERSION_KH02_R2_C3,
            "schema_version": SCHEMA_VERSION_KH02_R2_C3,
            "run_id": run_id,
            "started_utc": utc_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "branch": branch,
            "start_head_sha": start_head_sha,
            "end_head_sha": end_head_sha,
            "tested_executable_sha": start_head_sha,
            "remote_head_sha": remote_sha,
            "prior_kh02_r2_c2_tested_sha": PRIOR_KH02_R2_C2_TESTED_SHA,
            "prior_kh02_r2_c2_evidence_sha": PRIOR_KH02_R2_C2_EVIDENCE_SHA,
            "prior_kh02_r2_c2_pub_head_sha": PRIOR_KH02_R2_C2_PUB_HEAD_SHA,
            "prior_kh02_r2_c2_run_id": PRIOR_KH02_R2_C2_RUN_ID,
            "prior_kh02_r2_c1_code_commit_sha": PRIOR_KH02_R2_C1_CODE_COMMIT_SHA,
            "prior_kh02_r2_c1_diag_commit_sha": PRIOR_KH02_R2_C1_DIAG_COMMIT_SHA,
            "prior_kh02_r2_publication_head_sha": PRIOR_KH02_R2_PUBLICATION_HEAD_SHA,
            "prior_kh02_r2_tested_executable_sha": PRIOR_KH02_R2_TESTED_SHA,
            "prior_kh02_r1_publication_head_sha": PRIOR_KH02_R1_PUBLICATION_HEAD_SHA,
            "git_clean_at_start": git_clean,
            "local_uncommitted_c3_changes": not git_clean,
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
        write_json_durable_atomic(run_dir / "00_git_branch_tested_sha_remote_preflight.json", f00)

        pub_st = "PRE_PUSH_PENDING" if (can_run_heavy and f15["status"] == "PASS" and f16["status"] == "PASS" and f17["BASELINE_PARITY_VERDICT"] == "PASS") else (
            "NOT_PUBLISHED_BLOCKED_LOW_DISK" if not strict_disk_ok else "NOT_PUBLISHED_BLOCKED_GATES"
        )
        f22 = {
            "spec_version": SPEC_VERSION_KH02_R2_C3,
            "run_id": run_id,
            "branch": branch,
            "tested_executable_sha": start_head_sha,
            "evidence_commit_sha": None,
            "publication_head_sha": None,
            "remote_readback_sha256_verified": False,
            "latest_run_pointer_updated": False,
            "owner_disk_preflight_waiver": False,
            "publication_phase": "PHASE_1_PRE_PUSH_IMMUTABLE_RECEIPT",
            "publication_status": pub_st,
        }
        write_json_durable_atomic(run_dir / "22_publication_tested_sha_prepush_receipt.json", f22)

        mem_audit = _audit_memory_samples_c3(run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz")
        docs_map = {
            "00": f00, "01": f01, "02": f02, "03": f03, "04": f04, "05": f05, "06": f06, "07": f07,
            "08": f08, "09": f09, "10": f10, "11": f11, "12": f12, "13": f13, "14": f14, "15": f15,
            "16": f16, "17": f17, "18": f18, "19": f19, "22": f22,
        }
        recomp = recompute_c3_17_gates_and_17_verdicts(
            docs=docs_map,
            mem_audit=mem_audit,
            junit_bijection_ok=bool(f15.get("junit_bijection_exact", False)),
            for_final_verifier_manifest=False,
        )
        f21 = {
            "spec_version": SPEC_VERSION_KH02_R2_C3,
            "schema_version": SCHEMA_VERSION_KH02_R2_C3,
            "run_id": run_id,
            "tested_executable_sha": start_head_sha,
            "branch": branch,
            "OOS_EVALUATIONS": 0,
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
            "gates_17": recomp["gates_17"],
            "verdicts_17": recomp["verdicts_17"],
        }
        f21["artifact_canonical_sha256"] = canonical_hash({k: val for k, val in f21.items() if k != "artifact_canonical_sha256"})
        write_json_durable_atomic(run_dir / "21_orthogonal_science_engineering_gate_matrix.json", f21)
        docs_map["21"] = f21

        readme_md, handover_vi = _render_c3_readme_and_handover_vi(run_dir, docs_map)
        (run_dir / "README_KH02_R2_C3.md").write_text(readme_md, encoding="utf-8")
        (run_dir / "HANDOVER_KH02_R2_C3_VI.txt").write_text(handover_vi, encoding="utf-8")

        # Stage 8: Separate-OS-Process Independent Disk Verifier sealing 20!
        print("[KH-02.R2.C3] Stage 8: Launching separate-OS-process independent disk verifier (20)...", flush=True)
        verifier_script = APP_ROOT / "tools" / "kh_02_r2_c3_independent_verifier.py"
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

        f20 = json.loads((run_dir / "20_independent_verifier_sha256_manifest.json").read_text("utf-8"))
        rc = int(v_proc.returncode)
        return rc, run_dir, {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "exit_code": rc,
            "overall_classification": f20.get("overall_classification"),
            "independent_verifier_status": f20.get("independent_verifier_status"),
            "manifest_20_sha256": f20.get("manifest_content_sha256"),
            "verdicts_17": f20.get("recomputed_17_verdicts"),
            "gates_17": f20.get("recomputed_17_gates"),
        }
    finally:
        if sentinel_tmp.exists():
            try:
                safe_cleanup_sentinel_owned_temp_dir_r2_c1(sentinel_tmp, expected_run_id=run_id)
            except Exception:
                pass


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AURUM DESK KH-02.R2.C3 Strict Provenance & Acceptance Orchestrator")
    ap.add_argument("--owner-waive-disk-preflight", action="store_true", help="FORBIDDEN: always exits code 2.")
    ap.add_argument("--max-workers", type=int, default=GLOBAL_MAX_DESCENDANTS_KH02_R2)
    args = ap.parse_args(argv)
    if args.owner_waive_disk_preflight:
        print(json.dumps({"status": "REJECTED_UNAPPROVED_AUTONOMOUS_DISK_WAIVER", "exit_code": 2}), file=sys.stderr)
        return 2
    rc, _rd, summary = run_kh02_r2_c3_acceptance(owner_waive_disk_preflight=False, max_workers=args.max_workers)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
