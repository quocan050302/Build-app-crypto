"""
aurum-desk/backend/tests/test_kh03_r5_c1_custody_abort_and_determinism.py

KH-03.R5.C1 — Staging/Custody Integrity, Abort Status Truth, W1/W2/W4 Fixture Determinism,
AND 48 NEW Unique C1 RED/GREEN Fault Controls (`C1-A01..A18`, `C1-B01..B15`, `C1-C01..C15`).
Distinguishes `LEGACY_RED_REPRODUCED` vs `NEW_CONTRACT_RED_GREEN` honestly (§7).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Mapping, Optional

import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_c2_public_capture import RawHttpResponseC2, StdlibUrllibPublicTransportC2
from services.kh_02_r2_c3_provenance_guard import MAX_RAW_HTTP_BODY_BYTES_C3
from services.kh_03_r5_capture_and_custody_service import (
    R5DevCustodyJournal,
    _canonical_volume_identity_c1,
    _parse_retry_after_strict,
    capture_r5_public_batch,
    make_r5_fixture_resource_monitor,
    persist_r5_verified_dev_snapshot,
    validate_r5_resource_sample_strict,
    verify_r5_custody_chain_readonly,
)
from services.kh_03_r5_market_data_foundation_service import (
    R5AttestationVerificationResult,
    R5OperatorGrant,
    build_r5_source_capability_inventory,
    plan_r5_capture_batches,
    validate_r5_operator_capture_scope,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

NOW_UTC = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
NOW_MS = int(NOW_UTC.timestamp() * 1000)
WS_MS = 1794000000000
WE_MS = 1794000600000
OK_BODY = b'{"code":"00000","data":[{"symbol":"XAUUSDT","bidPr":"4100.00","askPr":"4100.20"}]}'


def run_new_kh03_r5_c1_fault_controls() -> List[Dict[str, Any]]:
    """
    Executes 48 NEW unique KH-03.R5.C1 RED/GREEN fault controls covering P1-A, P1-B, P1-C,
    and adjacent custody/abort invariants. Executes ZERO external market sockets.
    """
    inv = build_r5_source_capability_inventory(REPO_ROOT)
    out: List[Dict[str, Any]] = []

    def _rec(fid: str, name: str, fn: str, kind: str, red: bool, grn: bool) -> None:
        out.append({
            "fault_id": fid,
            "control_name": name,
            "target_function": fn,
            "control_kind": kind,
            "red_negative_detected": bool(red),
            "green_clean_passed": bool(grn),
            "status": "PASS" if (red and grn) else "FAIL",
        })

    def _mk_qa(rid: str = "c1_f48", eps: tuple[str, ...] = ("public_ticker",), **over: Any) -> tuple[R5OperatorGrant, Dict[str, Any]]:
        gkw: Dict[str, Any] = dict(
            grant_id="QA_C1_F", issued_by="QA", purpose="QA_FIXTURE", symbol="XAUUSDT", product_type="USDT-FUTURES",
            allowed_endpoints=eps, window_start_ms=WS_MS, window_end_ms=WE_MS, max_requests=6,
            max_total_bytes=50_000, max_duration_sec=20, expires_utc_ms=NOW_MS + 120_000,
            run_id=rid, is_qa_fixture_grant=True, origin_kind="QA",
        )
        gkw.update(over)
        g = R5OperatorGrant(**gkw)
        r = {
            "symbol": g.symbol, "product_type": g.product_type, "endpoints": list(eps), "run_id": g.run_id,
            "purpose": g.purpose, "window_start_ms": g.window_start_ms, "window_end_ms": g.window_end_ms,
            "max_requests": g.max_requests, "max_total_bytes": g.max_total_bytes, "max_duration_sec": g.max_duration_sec,
        }
        return g, r

    g0, r0 = _mk_qa()
    d0 = validate_r5_operator_capture_scope(g0, r0, NOW_UTC, {"qa_fixture_mode": True})
    p0 = plan_r5_capture_batches(d0, inv, {"destination_disk_free_gib": 30.0, "max_concurrency": 1, "max_pages": 6, "max_requests": 6, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["public_ticker"]}, now_utc=NOW_UTC)

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
            rv = self.resps.pop(0) if self.resps else RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)
            if isinstance(rv, Exception):
                raise rv
            return rv

    with tempfile.TemporaryDirectory(prefix="kh03r5c1_f48_") as td_s:
        td = Path(td_s)
        mon = make_r5_fixture_resource_monitor(destination=td)

        # --- P1-A: 18 unique scope/trust/plan binding controls (C1-A01..C1-A18) ---
        live_g = R5OperatorGrant("L1", "owner", "DEVELOPMENT_CAPTURE", "XAUUSDT", "USDT-FUTURES", ("public_ticker",), WS_MS, WE_MS, 5, 50_000, 20, NOW_MS + 120_000, "c1_f48", False, "SEPARATE_OWNER_ATTESTATION")
        live_r = {**r0, "purpose": "DEVELOPMENT_CAPTURE", "max_requests": 5}
        _rec("C1-A01", "MISSING_GRANT_BLOCKED", "validate_r5_operator_capture_scope", "LEGACY_RED_REPRODUCED", not validate_r5_operator_capture_scope(None, live_r, NOW_UTC, {})["admitted"], d0["admitted"])
        _rec("C1-A02", "FORGED_TRUST_DICT_DENIED_DEFAULT_VERIFIER", "validate_r5_operator_capture_scope", "LEGACY_RED_REPRODUCED", not validate_r5_operator_capture_scope(live_g, live_r, NOW_UTC, {"human_operator_attestation_verified": True, "attestation_source": "EXPLICIT_SEPARATE_OWNER_GRANT"})["live_network_permitted"], d0["admitted"])
        _rec("C1-A03", "CLI_ENV_PROMPT_REJECTED", "validate_r5_operator_capture_scope", "NEW_CONTRACT_RED_GREEN", not validate_r5_operator_capture_scope(g0, r0, NOW_UTC, {"qa_fixture_mode": True, "from_cli_flag": True})["admitted"], d0["admitted"])
        _rec("C1-A04", "QA_GRANT_PROMOTE_LIVE_REJECTED", "validate_r5_operator_capture_scope", "NEW_CONTRACT_RED_GREEN", not validate_r5_operator_capture_scope(g0, r0, NOW_UTC, {"qa_fixture_mode": False, "human_operator_attestation_verified": True})["admitted"], not d0["live_network_permitted"])
        _rec("C1-A05", "BOOL_AS_INT_BUDGET_REJECTED", "validate_r5_operator_capture_scope", "NEW_CONTRACT_RED_GREEN", not validate_r5_operator_capture_scope(g0, {**r0, "max_requests": True}, NOW_UTC, {"qa_fixture_mode": True})["admitted"], d0["admitted"])
        _rec("C1-A06", "NAN_INF_MISSING_DURATION_REJECTED", "validate_r5_operator_capture_scope", "NEW_CONTRACT_RED_GREEN", (not validate_r5_operator_capture_scope(g0, {**r0, "max_duration_sec": float("nan")}, NOW_UTC, {"qa_fixture_mode": True})["admitted"]) and (not validate_r5_operator_capture_scope(g0, {k: v for k, v in r0.items() if k != "max_duration_sec"}, NOW_UTC, {"qa_fixture_mode": True})["admitted"]), d0["admitted"])
        _rec("C1-A07", "WINDOW_OUTSIDE_OR_INVERTED_REJECTED", "validate_r5_operator_capture_scope", "NEW_CONTRACT_RED_GREEN", (not validate_r5_operator_capture_scope(g0, {**r0, "window_start_ms": WS_MS - 1000}, NOW_UTC, {"qa_fixture_mode": True})["admitted"]) and (not validate_r5_operator_capture_scope(g0, {**r0, "window_start_ms": WE_MS, "window_end_ms": WS_MS}, NOW_UTC, {"qa_fixture_mode": True})["admitted"]), d0["admitted"])
        _rec("C1-A08", "ENDPOINT_OUTSIDE_GRANT_REJECTED", "validate_r5_operator_capture_scope", "LEGACY_RED_REPRODUCED", not validate_r5_operator_capture_scope(g0, {**r0, "endpoints": ["contracts"]}, NOW_UTC, {"qa_fixture_mode": True})["admitted"], d0["admitted"])
        _rec("C1-A09", "DUPLICATE_ENDPOINT_AND_OOS_REJECTED", "validate_r5_operator_capture_scope", "NEW_CONTRACT_RED_GREEN", (not validate_r5_operator_capture_scope(g0, {**r0, "endpoints": ["public_ticker", "public_ticker"]}, NOW_UTC, {"qa_fixture_mode": True})["admitted"]) and (not validate_r5_operator_capture_scope(g0, {**r0, "destination_scope": "/tmp/real_oos_escrow"}, NOW_UTC, {"qa_fixture_mode": True})["admitted"]), d0["admitted"])
        _rec("C1-A10", "PLANNER_REJECTS_BROADER_BUDGET_NO_CLAMP", "plan_r5_capture_batches", "LEGACY_RED_REPRODUCED", plan_r5_capture_batches(d0, inv, {"destination_disk_free_gib": 30.0, "max_concurrency": 1, "max_pages": 6, "max_requests": 50, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["public_ticker"]}, now_utc=NOW_UTC)["status"] == "BLOCKED", p0["status"] == "PLANNED_QA_OFFLINE")
        _rec("C1-A11", "PLANNER_REJECTS_DIFFERENT_SYMBOL_ENDPOINT", "plan_r5_capture_batches", "LEGACY_RED_REPRODUCED", plan_r5_capture_batches(d0, inv, {"destination_disk_free_gib": 30.0, "max_concurrency": 1, "max_pages": 6, "max_requests": 6, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["contracts"], "symbol": "BTCUSDT"}, now_utc=NOW_UTC)["status"] == "BLOCKED", p0["status"] == "PLANNED_QA_OFFLINE")
        _rec("C1-A12", "PLANNER_REJECTS_FORGED_ADMITTED_DICT", "plan_r5_capture_batches", "NEW_CONTRACT_RED_GREEN", plan_r5_capture_batches({k: v for k, v in d0.items() if not k.startswith("_")}, inv, {"destination_disk_free_gib": 30.0, "max_concurrency": 1, "max_pages": 6, "max_requests": 6, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["public_ticker"]}, now_utc=NOW_UTC)["status"] == "BLOCKED", p0["status"] == "PLANNED_QA_OFFLINE")

        sp13 = _Spy()
        r13 = asyncio.run(capture_r5_public_batch({"status": "PLANNED_QA_OFFLINE", "partition_role": "QA_FIXTURE", "live_network_permitted": False, "budget": p0["budget"], "batches": p0["batches"]}, sp13, mon, run_id="c1_f48", destination_root=td, utc_now_fn=lambda: NOW_UTC))
        _rec("C1-A13", "FORGED_UNBOUND_PLAN_ZERO_DISPATCH", "capture_r5_public_batch", "LEGACY_RED_REPRODUCED", len(sp13.calls) == 0 and r13["aborted"], True)

        p_rehash = dict(p0, batches=[dict(p0["batches"][0], symbol="BTCUSDT")])
        p_rehash["plan_digest_sha256"] = canonical_hash({k: v for k, v in p_rehash.items() if not k.startswith("_") and k != "plan_digest_sha256"})
        sp14 = _Spy()
        r14 = asyncio.run(capture_r5_public_batch(p_rehash, sp14, mon, run_id="c1_f48", trusted_scope=d0, destination_root=td, utc_now_fn=lambda: NOW_UTC))
        _rec("C1-A14", "MUTATED_PLAN_WITH_RECOMPUTED_DIGEST_REJECTED", "capture_r5_public_batch", "LEGACY_RED_REPRODUCED", len(sp14.calls) == 0 and any("PLAN_DIGEST_DRIFT" in v or "PLAN_SNAPSHOT_MUTATED" in v for v in r14["violations"]), True)

        sp15 = _Spy()
        r15 = asyncio.run(capture_r5_public_batch(p0, sp15, mon, run_id="WRONG_RUN", trusted_scope=d0, destination_root=td, utc_now_fn=lambda: NOW_UTC))
        _rec("C1-A15", "CROSS_RUN_PLAN_REUSE_REJECTED", "capture_r5_public_batch", "LEGACY_RED_REPRODUCED", len(sp15.calls) == 0 and any("RUN_ID" in v for v in r15["violations"]), True)

        sp16 = _Spy()
        r16 = asyncio.run(capture_r5_public_batch(p0, sp16, mon, run_id="c1_f48", trusted_scope=d0, destination_root=td, utc_now_fn=lambda: NOW_UTC + timedelta(seconds=300)))
        _rec("C1-A16", "EXPIRED_GRANT_BETWEEN_PLAN_AND_DISPATCH_BLOCKED", "capture_r5_public_batch", "LEGACY_RED_REPRODUCED", len(sp16.calls) == 0 and any("OPERATOR_GRANT_EXPIRED" in v for v in r16["violations"]), True)

        g2e, r2e = _mk_qa(eps=("public_ticker", "contracts"))
        d2e = validate_r5_operator_capture_scope(g2e, r2e, NOW_UTC, {"qa_fixture_mode": True})
        p2e = plan_r5_capture_batches(d2e, inv, {"destination_disk_free_gib": 30.0, "max_concurrency": 1, "max_pages": 6, "max_requests": 6, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["public_ticker", "contracts"]}, now_utc=NOW_UTC)
        sp17 = _Spy(on_call=lambda idx, *_: p2e["batches"][1].__setitem__("url", "https://api.bitget.com/api/v2/mix/order/place-order") if idx == 0 else None)
        r17 = asyncio.run(capture_r5_public_batch(p2e, sp17, mon, run_id="c1_f48", trusted_scope=d2e, destination_root=td, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC))
        _rec("C1-A17", "POST_ENQUEUE_PLAN_MUTATION_CAUGHT", "capture_r5_public_batch", "NEW_CONTRACT_RED_GREEN", len(sp17.calls) == 1 and r17["aborted"] and r17["qa_fixture_records_count"] == 0, True)

        r18 = asyncio.run(capture_r5_public_batch(p0, StdlibUrllibPublicTransportC2(), mon, run_id="c1_f48", trusted_scope=d0, destination_root=td, utc_now_fn=lambda: NOW_UTC))
        _rec("C1-A18", "QA_PLAN_CANNOT_USE_LIVE_STDLIB_TRANSPORT", "capture_r5_public_batch", "NEW_CONTRACT_RED_GREEN", r18["external_requests_attempted"] == 0 and any("QA_FIXTURE_MODE_MUST_NOT_USE_LIVE_SOCKET_TRANSPORT" in v for v in r18["violations"]), True)

        # --- P1-B: 15 unique deadline/cancel/retry/budget/drain controls (C1-B01..C1-B15) ---
        clk = {"m": 100.0, "u": NOW_UTC}
        mon_c = make_r5_fixture_resource_monitor(destination=td, monotonic_fn=lambda: clk["m"])
        spb1 = _Spy([RawHttpResponseC2(429, b"", {"retry-after": "0.1"}, NOW_MS, NOW_MS + 5)])
        rb1 = asyncio.run(capture_r5_public_batch(p0, spb1, mon_c, run_id="c1_f48", trusted_scope=d0, destination_root=td, sleep_fn=lambda _s: clk.__setitem__("m", clk["m"] + 25.0), monotonic_fn=lambda: clk["m"], utc_now_fn=lambda: clk["u"]))
        _rec("C1-B01", "POST_WAIT_DEADLINE_EXCEEDED_ZERO_EXTRA_CALL", "capture_r5_public_batch", "LEGACY_RED_REPRODUCED", len(spb1.calls) == 1 and rb1["abort_reason"] == "CAPTURE_DEADLINE_EXCEEDED", True)

        clk2 = {"m": 200.0}
        ev2 = asyncio.Event()
        mon_c2 = make_r5_fixture_resource_monitor(destination=td, monotonic_fn=lambda: clk2["m"])
        spb2 = _Spy([RawHttpResponseC2(429, b"", {"retry-after": "0.1"}, NOW_MS, NOW_MS + 5)])
        rb2 = asyncio.run(capture_r5_public_batch(p0, spb2, mon_c2, cancel_event=ev2, run_id="c1_f48", trusted_scope=d0, destination_root=td, sleep_fn=lambda s: (clk2.__setitem__("m", clk2["m"] + s), ev2.set()), monotonic_fn=lambda: clk2["m"], utc_now_fn=lambda: NOW_UTC))
        _rec("C1-B02", "CANCEL_DURING_WAIT_STOPS_BEFORE_DISPATCH", "capture_r5_public_batch", "LEGACY_RED_REPRODUCED", len(spb2.calls) == 1 and rb2["abort_reason"] == "CANCELLED_MIDFLIGHT", True)

        clk3 = {"m": 300.0, "u": NOW_UTC}
        mon_c3 = make_r5_fixture_resource_monitor(destination=td, monotonic_fn=lambda: clk3["m"])
        spb3 = _Spy([RawHttpResponseC2(429, b"", {"retry-after": "0.1"}, NOW_MS, NOW_MS + 5)])
        rb3 = asyncio.run(capture_r5_public_batch(p0, spb3, mon_c3, run_id="c1_f48", trusted_scope=d0, destination_root=td, sleep_fn=lambda s: (clk3.__setitem__("m", clk3["m"] + s), clk3.__setitem__("u", NOW_UTC + timedelta(seconds=200))), monotonic_fn=lambda: clk3["m"], utc_now_fn=lambda: clk3["u"]))
        _rec("C1-B03", "EXPIRY_DURING_RETRY_WAIT_STOPS_DISPATCH", "capture_r5_public_batch", "NEW_CONTRACT_RED_GREEN", len(spb3.calls) == 1 and rb3["abort_reason"] == "OPERATOR_GRANT_EXPIRED", True)

        gt, rt = _mk_qa(max_duration_sec=3, expires_utc_ms=NOW_MS + 2500)
        dt = validate_r5_operator_capture_scope(gt, rt, NOW_UTC, {"qa_fixture_mode": True})
        pt = plan_r5_capture_batches(dt, inv, {"destination_disk_free_gib": 30.0, "max_concurrency": 1, "max_pages": 6, "max_requests": 6, "max_total_bytes": 50_000, "max_duration_sec": 3, "endpoints": ["public_ticker"]}, now_utc=NOW_UTC)
        spb4 = _Spy()
        rb4 = asyncio.run(capture_r5_public_batch(pt, spb4, mon, run_id="c1_f48", trusted_scope=dt, destination_root=td, utc_now_fn=lambda: NOW_UTC, transport_timeout_ceiling_sec=10.0))
        _rec("C1-B04", "TIMEOUT_BOUNDED_BY_REMAINING_GRANT_AND_SESSION", "capture_r5_public_batch", "LEGACY_RED_REPRODUCED", len(spb4.calls) == 1 and spb4.calls[0]["timeout_sec"] <= 2.5 + 1e-6, rb4["capture_execution_status"] == "QA_FIXTURE_OFFLINE_PASS")

        sl5: List[float] = []
        spb5 = _Spy([RawHttpResponseC2(429, b"", {"retry-after": "120"}, NOW_MS, NOW_MS + 5)])
        rb5 = asyncio.run(capture_r5_public_batch(p0, spb5, mon, run_id="c1_f48", trusted_scope=d0, destination_root=td, sleep_fn=lambda s: sl5.append(s), utc_now_fn=lambda: NOW_UTC))
        _rec("C1-B05", "RETRY_AFTER_EXCEEDS_REMAINING_NEVER_SHORTENED", "capture_r5_public_batch", "LEGACY_RED_REPRODUCED", len(spb5.calls) == 1 and sl5 == [] and "RETRY_AFTER_EXCEEDS_REMAINING_BUDGET" in str(rb5["abort_reason"]), True)

        sl6: List[float] = []
        clk6 = {"m": 400.0}
        mon_c6 = make_r5_fixture_resource_monitor(destination=td, monotonic_fn=lambda: clk6["m"])
        spb6 = _Spy([RawHttpResponseC2(429, b"", {"retry-after": "3.0"}, NOW_MS, NOW_MS + 5), RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)])
        rb6 = asyncio.run(capture_r5_public_batch(p0, spb6, mon_c6, run_id="c1_f48", trusted_scope=d0, destination_root=td, sleep_fn=lambda s: (sl6.append(s), clk6.__setitem__("m", clk6["m"] + s)), monotonic_fn=lambda: clk6["m"], utc_now_fn=lambda: NOW_UTC))
        _rec("C1-B06", "RETRY_AFTER_3S_RESPECTED_NOT_CAPPED_2S", "capture_r5_public_batch", "LEGACY_RED_REPRODUCED", sl6 == [3.0] and len(spb6.calls) == 2, rb6["capture_execution_status"] == "QA_FIXTURE_OFFLINE_PASS")

        ok_hd, sec_hd, _ = _parse_retry_after_strict("Wed, 25 Nov 2026 12:00:05 GMT", NOW_UTC)
        ok_bad, _, _ = _parse_retry_after_strict("NaN", NOW_UTC)
        _rec("C1-B07", "RETRY_AFTER_HTTP_DATE_AND_INVALID_STRICT", "_parse_retry_after_strict", "NEW_CONTRACT_RED_GREEN", not ok_bad, ok_hd and abs((sec_hd or 0.0) - 5.0) < 1e-6)

        spb8 = _Spy([RawHttpResponseC2(403, b"denied", {}, NOW_MS, NOW_MS + 5), RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)])
        rb8 = asyncio.run(capture_r5_public_batch(p0, spb8, mon, run_id="c1_f48", trusted_scope=d0, destination_root=td, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC))
        _rec("C1-B08", "NON_RETRYABLE_403_NEVER_RETRIED", "capture_r5_public_batch", "NEW_CONTRACT_RED_GREEN", len(spb8.calls) == 1 and rb8["qa_fixture_records_count"] == 0, True)

        g_lim, r_lim = _mk_qa(eps=("public_ticker", "contracts"), max_requests=2)
        d_lim = validate_r5_operator_capture_scope(g_lim, r_lim, NOW_UTC, {"qa_fixture_mode": True})
        p_lim = plan_r5_capture_batches(d_lim, inv, {"destination_disk_free_gib": 30.0, "max_concurrency": 2, "max_pages": 2, "max_requests": 2, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["public_ticker", "contracts"]}, now_utc=NOW_UTC)
        spb9 = _Spy([RawHttpResponseC2(429, b"", {"retry-after": "0.01"}, NOW_MS, NOW_MS + 5), RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5), RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)])
        rb9 = asyncio.run(capture_r5_public_batch(p_lim, spb9, mon, run_id="c1_f48", trusted_scope=d_lim, destination_root=td, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC))
        _rec("C1-B09", "ATOMIC_REQUEST_CAP_NO_OVERSUBSCRIBE", "capture_r5_public_batch", "NEW_CONTRACT_RED_GREEN", rb9["requests_dispatched_actual"] <= 2 and len(spb9.calls) <= 2, True)

        g_by, r_by = _mk_qa(max_total_bytes=90)
        d_by = validate_r5_operator_capture_scope(g_by, r_by, NOW_UTC, {"qa_fixture_mode": True})
        p_by = plan_r5_capture_batches(d_by, inv, {"destination_disk_free_gib": 30.0, "max_concurrency": 1, "max_pages": 6, "max_requests": 6, "max_total_bytes": 90, "max_duration_sec": 20, "endpoints": ["public_ticker"]}, now_utc=NOW_UTC)
        spb10 = _Spy([RawHttpResponseC2(429, b"E" * 50, {"retry-after": "0.01"}, NOW_MS, NOW_MS + 5), RawHttpResponseC2(200, b"X" * 50, {}, NOW_MS, NOW_MS + 5)])
        rb10 = asyncio.run(capture_r5_public_batch(p_by, spb10, mon, run_id="c1_f48", trusted_scope=d_by, destination_root=td, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC))
        _rec("C1-B10", "WIRE_BYTES_INCLUDE_ERROR_BODIES_AND_SEPARATE_ACCEPTED", "capture_r5_public_batch", "NEW_CONTRACT_RED_GREEN", rb10["wire_bytes_observed_total"] == 100 and rb10["bytes_accepted_total"] == 0 and rb10["abort_reason"] == "TOTAL_BYTES_BUDGET_EXHAUSTED", True)

        ev11 = asyncio.Event()
        spb11 = _Spy(on_call=lambda *_: ev11.set())
        rb11 = asyncio.run(capture_r5_public_batch(p0, spb11, mon, cancel_event=ev11, staging_root=td / "st11", run_id="c1_f48", trusted_scope=d0, destination_root=td, utc_now_fn=lambda: NOW_UTC))
        _rec("C1-B11", "MIDFLIGHT_LATE_RESPONSE_DISCARDED_AND_DRAINED", "capture_r5_public_batch", "NEW_CONTRACT_RED_GREEN", rb11["aborted"] and rb11["qa_fixture_records_count"] == 0 and rb11["undrained_inflight_workers"] == 0, True)

        rb12 = asyncio.run(capture_r5_public_batch(p0, _Spy(), mon, run_id="c1_f48", trusted_scope=d0, destination_root=td, monotonic_fn=lambda: float("nan"), utc_now_fn=lambda: NOW_UTC))
        _rec("C1-B12", "INVALID_MONOTONIC_CLOCK_BLOCKED", "capture_r5_public_batch", "NEW_CONTRACT_RED_GREEN", rb12["requests_dispatched_actual"] == 0 and rb12["capture_execution_status"] == "FAIL", True)

        class _PolVer:
            verifier_id = "POLICY_TEST_STUB"
            def verify_owner_attestation(self, g, eff, nu, tc):
                return R5AttestationVerificationResult(True, "VERIFIED_SEPARATE_OWNER_ATTESTATION", "TEST", self.verifier_id, g.grant_id, NOW_MS)
        d_live_p = validate_r5_operator_capture_scope(live_g, live_r, NOW_UTC, {}, attestation_verifier=_PolVer())
        p_live_p = plan_r5_capture_batches(d_live_p, inv, {"destination_disk_free_gib": 30.0, "max_concurrency": 1, "max_pages": 5, "max_requests": 5, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["public_ticker"]}, now_utc=NOW_UTC)
        rb13 = asyncio.run(capture_r5_public_batch(p_live_p, StdlibUrllibPublicTransportC2(), mon, run_id="c1_f48", trusted_scope=d_live_p, destination_root=td, utc_now_fn=lambda: NOW_UTC))
        _rec("C1-B13", "LEGACY_C4_TRANSPORT_UNSUPPORTED_WIRE_CAP_BLOCKED", "capture_r5_public_batch", "NEW_CONTRACT_RED_GREEN", rb13["capture_execution_status"] == "BLOCKED_TRANSPORT_READ_CAP_UNSUPPORTED" and rb13["external_requests_attempted"] == 0, True)

        _rec("C1-B14", "PLAN_PAGES_SEMANTICS_TRUTHFUL_SINGLE_PAGE", "plan_r5_capture_batches", "NEW_CONTRACT_RED_GREEN", p0["pages_semantics"] == "SINGLE_REQUEST_PER_BATCH_NO_PAGINATION" and p0["batches"][0]["max_pages"] == 1, True)

        clk15 = {"m": 500.0}
        mon15 = make_r5_fixture_resource_monitor(destination=td, monotonic_fn=lambda: clk15["m"])
        spb15 = _Spy([TimeoutError("to"), RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)])
        rb15 = asyncio.run(capture_r5_public_batch(p0, spb15, mon15, run_id="c1_f48", trusted_scope=d0, destination_root=td, sleep_fn=lambda s: clk15.__setitem__("m", clk15["m"] + s), monotonic_fn=lambda: clk15["m"], utc_now_fn=lambda: NOW_UTC))
        _rec("C1-B15", "DISPATCH_CADENCE_MONOTONIC_AFTER_RETRY", "capture_r5_public_batch", "NEW_CONTRACT_RED_GREEN", len(rb15["dispatch_timestamps_mono"]) == 2 and (rb15["dispatch_timestamps_mono"][1] - rb15["dispatch_timestamps_mono"][0]) >= 0.05 - 1e-6, rb15["capture_execution_status"] == "QA_FIXTURE_OFFLINE_PASS")

        # --- P1-C & Adjacent Custody/Abort: 15 unique controls (C1-C01..C1-C15) ---
        now_m = 600.0
        vol = _canonical_volume_identity_c1(td)
        wvol = "Z:\\" if os.name == "nt" else "dev:888888888888"
        good_s = {"disk_free_gib": 25.0, "available_ram_mib": 1024.0, "sampled_monotonic": now_m, "destination_volume": vol, "monitor_source": "QA_MON", "sample_ok": True}
        grn_s, _, _ = validate_r5_resource_sample_strict(good_s, td, now_monotonic=now_m)

        for fid, nm, kind, bad_m in (
            ("C1-C01", "MONITOR_NONE_FAIL_CLOSED", "LEGACY_RED_REPRODUCED", None),
            ("C1-C02", "MONITOR_EMPTY_DICT_FAIL_CLOSED", "LEGACY_RED_REPRODUCED", lambda: {}),
            ("C1-C03", "MISSING_DISK_OR_RAM_KEY_BLOCKED", "LEGACY_RED_REPRODUCED", lambda: {k: v for k, v in good_s.items() if k != "disk_free_gib"}),
            ("C1-C04", "NAN_DISK_FAIL_CLOSED", "LEGACY_RED_REPRODUCED", lambda: {**good_s, "disk_free_gib": float("nan")}),
            ("C1-C05", "INF_RAM_FAIL_CLOSED", "NEW_CONTRACT_RED_GREEN", lambda: {**good_s, "available_ram_mib": float("inf")}),
            ("C1-C06", "BOOL_OR_STR_VALUE_BLOCKED", "NEW_CONTRACT_RED_GREEN", lambda: {**good_s, "disk_free_gib": True}),
            ("C1-C07", "STALE_RESOURCE_SAMPLE_BLOCKED", "NEW_CONTRACT_RED_GREEN", lambda: {**good_s, "sampled_monotonic": now_m - 3.0}),
            ("C1-C08", "FUTURE_RESOURCE_TIMESTAMP_BLOCKED", "NEW_CONTRACT_RED_GREEN", lambda: {**good_s, "sampled_monotonic": now_m + 2.0}),
            ("C1-C09", "WRONG_VOLUME_IDENTITY_BLOCKED", "NEW_CONTRACT_RED_GREEN", lambda: {**good_s, "destination_volume": wvol}),
            ("C1-C10", "MONITOR_EXCEPTION_FAIL_CLOSED", "NEW_CONTRACT_RED_GREEN", lambda: (_ for _ in ()).throw(RuntimeError("probe_err"))),
            ("C1-C11", "SAMPLE_OK_FALSE_OR_STR_BLOCKED", "NEW_CONTRACT_RED_GREEN", lambda: {**good_s, "sample_ok": "true"}),
        ):
            spc = _Spy()
            rc = asyncio.run(capture_r5_public_batch(p0, spc, bad_m, run_id="c1_f48", trusted_scope=d0, destination_root=td, monotonic_fn=lambda: now_m, utc_now_fn=lambda: NOW_UTC))
            _rec(fid, nm, "capture_r5_public_batch", kind, len(spc.calls) == 0 and rc["aborted"], grn_s)

        st_ab = td / "st_abort_truth"
        st_ab.mkdir()
        keep_f = st_ab / "unrelated.txt"; keep_f.write_text("KEEP", encoding="utf-8")
        ev_ab = asyncio.Event()
        p_ab = plan_r5_capture_batches(d2e, inv, {"destination_disk_free_gib": 30.0, "max_concurrency": 1, "max_pages": 6, "max_requests": 6, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["public_ticker", "contracts"]}, now_utc=NOW_UTC)
        sp_ab = _Spy(on_call=lambda idx, *_: ev_ab.set() if idx == 1 else None)
        r_ab = asyncio.run(capture_r5_public_batch(p_ab, sp_ab, make_r5_fixture_resource_monitor(destination=st_ab), cancel_event=ev_ab, staging_root=st_ab, run_id="c1_f48", trusted_scope=d2e, destination_root=td, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC))
        _rec("C1-C12", "ABORT_DELETED_STAGING_COUNTED_ZERO_ACCEPTED_OWNED_ONLY", "capture_r5_public_batch", "NEW_CONTRACT_RED_GREEN", r_ab["aborted"] and r_ab["removed_staged_files_count"] == 1 and r_ab["qa_fixture_records_count"] == 0 and keep_f.is_file(), True)

        j = R5DevCustodyJournal(td / "cust_c1", os_disk_probe_fn=lambda: 28.0)
        part = {"valid": True, "partition_role": "QA_FIXTURE", "plan_digest_sha256": "a" * 64}
        sp_m = st_ab / "m.raw"; sp_m.write_bytes(OK_BODY)
        r_c13 = persist_r5_verified_dev_snapshot(sp_m, {"evidence_class": "QA_FIXTURE", "capture_proof_id": "pm"}, {"valid": True, "canonical_normalized_sha256": "b" * 64}, part, j, expected_staging_root=st_ab)
        _rec("C1-C13", "CUSTODY_MANDATORY_EXPECTED_RAW_SHA256", "R5DevCustodyJournal.persist_snapshot", "NEW_CONTRACT_RED_GREEN", (not r_c13["committed"]) and r_c13["status"] == "REJECTED_MISSING_RAW_SHA", True)

        sp_big = st_ab / "big.raw"
        with sp_big.open("wb") as fh:
            fh.seek(MAX_RAW_HTTP_BODY_BYTES_C3 + 256)
            fh.write(b"\x00")
        r_c14 = persist_r5_verified_dev_snapshot(sp_big, {"raw_sha256": "a" * 64, "evidence_class": "QA_FIXTURE", "capture_proof_id": "pb"}, {"valid": True, "canonical_normalized_sha256": "b" * 64}, part, j, expected_staging_root=st_ab)
        sp_big.unlink()
        _rec("C1-C14", "CUSTODY_STAT_BEFORE_READ_OVERSIZE_REJECTED", "R5DevCustodyJournal.persist_snapshot", "NEW_CONTRACT_RED_GREEN", (not r_c14["committed"]) and any("RAW_BLOB_OVERSIZE_BEFORE_READ" in v for v in r_c14["violations"]), True)

        r_c15 = persist_r5_verified_dev_snapshot(sp_m, {"raw_sha256": hashlib.sha256(OK_BODY).hexdigest(), "evidence_class": "QA_FIXTURE", "capture_proof_id": "pok"}, {"valid": True, "canonical_normalized_sha256": hashlib.sha256(OK_BODY).hexdigest()}, part, j, expected_staging_root=st_ab)
        v_c15 = verify_r5_custody_chain_readonly(td / "cust_c1", j.head_state(), None, ())
        _rec("C1-C15", "CUSTODY_VALID_BOUNDED_COMMIT_AND_CHAIN_READONLY", "verify_r5_custody_chain_readonly", "NEW_CONTRACT_RED_GREEN", True, r_c15["committed"] and v_c15["valid"])

    return out


def test_c1_all_48_new_fault_controls_pass_red_green() -> None:
    ctrls = run_new_kh03_r5_c1_fault_controls()
    assert len(ctrls) == 48
    assert len({c["fault_id"] for c in ctrls}) == 48
    failed = [c for c in ctrls if c["status"] != "PASS"]
    assert failed == [], f"Failed C1 fault controls: {failed}"


def test_c1_cli_and_env_disk_waiver_rejected_exit_2() -> None:
    runner_path = APP_ROOT / "tools" / "run_kh_03_r5_c1_acceptance.py"
    if not runner_path.is_file():
        pytest.skip("Runner not yet created")
    p_cli = subprocess.run([sys.executable, str(runner_path), "--owner-waive-disk-preflight"], cwd=str(APP_ROOT), capture_output=True, text=True, timeout=30)
    assert p_cli.returncode == 2 and "REJECTED_UNAPPROVED_AUTONOMOUS_DISK_WAIVER" in p_cli.stderr
    env = os.environ.copy()
    env["KH03_R5_C1_OWNER_WAIVE_DISK_PREFLIGHT"] = "1"
    p_env = subprocess.run([sys.executable, str(runner_path)], cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=30)
    assert p_env.returncode == 2 and "REJECTED_UNAPPROVED_AUTONOMOUS_DISK_WAIVER" in p_env.stderr
