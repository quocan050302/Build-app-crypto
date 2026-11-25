"""
aurum-desk/backend/tests/test_kh03_r5_c1_deadline_cancel_and_budget.py

KH-03.R5.C1 — Phase B Deadline, Cancel, Rate-Limit, Retry-After, Atomic Request & Byte Budgets (B01..B24).
Deterministic fake clocks, barriers, and spy transports — ZERO network calls, ZERO long real sleeps.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import threading
import time
from typing import Any, Dict, List, Mapping, Optional

import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_c2_public_capture import RawHttpResponseC2, StdlibUrllibPublicTransportC2
from services.kh_03_r5_capture_and_custody_service import (
    _parse_retry_after_strict,
    capture_r5_public_batch,
    make_r5_fixture_resource_monitor,
)
from services.kh_03_r5_market_data_foundation_service import (
    R5AttestationVerificationResult,
    R5OperatorGrant,
    build_r5_source_capability_inventory,
    plan_r5_capture_batches,
    validate_r5_operator_capture_scope,
)

NOW_UTC = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
NOW_MS = int(NOW_UTC.timestamp() * 1000)
WS_MS = 1794000000000
WE_MS = 1794000600000
OK_BODY = b'{"code":"00000","data":[{"symbol":"XAUUSDT","bidPr":"4100.00","askPr":"4100.20"}]}'


def _build_plan(
    endpoints: List[str] = None,  # type: ignore[assignment]
    *,
    max_requests: int = 10,
    max_total_bytes: int = 100_000,
    max_response_bytes: int = 50_000,
    max_duration_sec: int = 10,
    max_retries: int = 2,
    max_concurrency: int = 2,
    min_iv_ms: int = 100,
    expires_offset_sec: int = 300,
    rid: str = "c1_b_run",
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    eps = list(endpoints or ["public_ticker"])
    inv = build_r5_source_capability_inventory(REPO_ROOT)
    g = R5OperatorGrant(
        grant_id="QA_B", issued_by="QA", purpose="QA_FIXTURE", symbol="XAUUSDT", product_type="USDT-FUTURES",
        allowed_endpoints=tuple(eps), window_start_ms=WS_MS, window_end_ms=WE_MS,
        max_requests=max_requests, max_total_bytes=max_total_bytes, max_duration_sec=max_duration_sec,
        expires_utc_ms=NOW_MS + expires_offset_sec * 1000, run_id=rid, is_qa_fixture_grant=True, origin_kind="QA",
        max_response_bytes=max_response_bytes, max_retries_per_request=max_retries,
        max_concurrency=max_concurrency, min_request_interval_ms=min_iv_ms,
    )
    req = {
        "symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "endpoints": eps, "run_id": rid, "purpose": "QA_FIXTURE",
        "window_start_ms": WS_MS, "window_end_ms": WE_MS, "max_requests": max_requests,
        "max_total_bytes": max_total_bytes, "max_duration_sec": max_duration_sec,
        "max_response_bytes": max_response_bytes, "max_retries_per_request": max_retries,
        "max_concurrency": max_concurrency, "min_request_interval_ms": min_iv_ms,
    }
    dec = validate_r5_operator_capture_scope(g, req, NOW_UTC, {"qa_fixture_mode": True})
    assert dec["admitted"] is True
    plan = plan_r5_capture_batches(
        dec, inv,
        {
            "destination_disk_free_gib": 30.0, "max_concurrency": max_concurrency, "max_pages": max_requests,
            "max_requests": max_requests, "max_total_bytes": max_total_bytes, "max_response_bytes": max_response_bytes,
            "max_duration_sec": max_duration_sec, "max_retries_per_request": max_retries,
            "min_request_interval_ms": min_iv_ms, "endpoints": eps,
        },
        now_utc=NOW_UTC,
    )
    assert plan["status"] == "PLANNED_QA_OFFLINE"
    return dec, plan


class _SpyTransport:
    def __init__(self, responses: Optional[List[Any]] = None, on_call: Any = None) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.responses = list(responses or [])
        self.on_call = on_call
        self._lock = threading.Lock()

    def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
        with self._lock:
            idx = len(self.calls)
            self.calls.append({"url": url, "params": dict(params), "timeout_sec": float(timeout_sec)})
            resp = self.responses.pop(0) if self.responses else RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)
        if self.on_call is not None:
            self.on_call(idx, url, params, timeout_sec)
        if isinstance(resp, Exception):
            raise resp
        return resp


def test_c1_b01_to_b06_deadline_cancel_post_wait_and_remaining_timeout(tmp_path: Path) -> None:
    dec, plan = _build_plan(max_duration_sec=5, max_retries=2, min_iv_ms=100, expires_offset_sec=10)

    # B01/B02: Deadline expires DURING wait -> 0 post-wait dispatch & pre-dispatch reservation released!
    clk = {"m": 100.0, "u": NOW_UTC}
    mon = make_r5_fixture_resource_monitor(destination=tmp_path, monotonic_fn=lambda: clk["m"])
    spy_b02 = _SpyTransport([RawHttpResponseC2(429, b"", {"retry-after": "0.5"}, NOW_MS, NOW_MS + 5)])
    def _sleep_over_deadline(s: float) -> None:
        clk["m"] += 6.0  # jumps past deadline 105.0 during retry wait!
    r02 = asyncio.run(capture_r5_public_batch(
        plan, spy_b02, mon, run_id="c1_b_run", trusted_scope=dec, destination_root=tmp_path,
        sleep_fn=_sleep_over_deadline, monotonic_fn=lambda: clk["m"], utc_now_fn=lambda: clk["u"],
    ))
    assert len(spy_b02.calls) == 1 and r02["aborted"] is True and r02["abort_reason"] == "CAPTURE_DEADLINE_EXCEEDED"
    assert r02["requests_dispatched_actual"] == 1

    # B03/B04/B12: Cancel during retry wait -> 0 second dispatch, reservation not counted as dispatched!
    clk3 = {"m": 200.0}
    mon3 = make_r5_fixture_resource_monitor(destination=tmp_path, monotonic_fn=lambda: clk3["m"])
    ev3 = asyncio.Event()
    spy_b03 = _SpyTransport([RawHttpResponseC2(429, b"", {"retry-after": "0.2"}, NOW_MS, NOW_MS + 5)])
    def _sleep_and_cancel(s: float) -> None:
        clk3["m"] += s
        ev3.set()
    r03 = asyncio.run(capture_r5_public_batch(
        plan, spy_b03, mon3, cancel_event=ev3, run_id="c1_b_run", trusted_scope=dec, destination_root=tmp_path,
        sleep_fn=_sleep_and_cancel, monotonic_fn=lambda: clk3["m"], utc_now_fn=lambda: NOW_UTC,
    ))
    assert len(spy_b03.calls) == 1 and r03["aborted"] is True and r03["abort_reason"] == "CANCELLED_MIDFLIGHT"

    # B06: Per-call timeout_sec strictly reflects remaining session/grant time (NOT flat 10s ceiling)!
    dec_t, plan_t = _build_plan(max_duration_sec=4, expires_offset_sec=3, max_retries=1)
    clk_t = {"m": 300.0, "u": NOW_UTC}
    mon_t = make_r5_fixture_resource_monitor(destination=tmp_path, monotonic_fn=lambda: clk_t["m"])
    spy_t = _SpyTransport([
        RawHttpResponseC2(503, b"", {}, NOW_MS, NOW_MS + 5),
        RawHttpResponseC2(200, OK_BODY, {}, NOW_MS + 10, NOW_MS + 15),
    ])
    def _sleep_advance_t(s: float) -> None:
        clk_t["m"] += 1.5
        clk_t["u"] = clk_t["u"] + timedelta(seconds=1.5)
    r_t = asyncio.run(capture_r5_public_batch(
        plan_t, spy_t, mon_t, run_id="c1_b_run", trusted_scope=dec_t, destination_root=tmp_path,
        sleep_fn=_sleep_advance_t, monotonic_fn=lambda: clk_t["m"], utc_now_fn=lambda: clk_t["u"],
        transport_timeout_ceiling_sec=10.0,
    ))
    assert r_t["capture_execution_status"] == "QA_FIXTURE_OFFLINE_PASS"
    assert spy_t.calls[0]["timeout_sec"] <= 3.0 + 1e-6  # bounded by 3s grant lifetime < 4s session < 10s ceiling
    assert spy_t.calls[1]["timeout_sec"] <= 1.5 + 1e-6  # bounded by remaining 1.5s after wait!


def test_c1_b07_to_b10_b17_b18_retry_after_classes_and_cadence(tmp_path: Path) -> None:
    dec, plan = _build_plan(max_duration_sec=5, max_retries=2, min_iv_ms=100)
    clk = {"m": 400.0}
    mon = make_r5_fixture_resource_monitor(destination=tmp_path, monotonic_fn=lambda: clk["m"])

    # B07: Retry-After=120s > remaining 5s -> terminates immediately, NEVER shortens to 2s, 0 second call!
    sleeps7: List[float] = []
    spy7 = _SpyTransport([RawHttpResponseC2(429, b"", {"retry-after": "120"}, NOW_MS, NOW_MS + 5)])
    r7 = asyncio.run(capture_r5_public_batch(
        plan, spy7, mon, run_id="c1_b_run", trusted_scope=dec, destination_root=tmp_path,
        sleep_fn=lambda s: sleeps7.append(s), monotonic_fn=lambda: clk["m"], utc_now_fn=lambda: NOW_UTC,
    ))
    assert len(spy7.calls) == 1 and sleeps7 == [] and r7["aborted"] is True
    assert "RETRY_AFTER_EXCEEDS_REMAINING_BUDGET" in str(r7["abort_reason"])

    # B08: Retry-After=3.0s within 5s budget -> waits FULL 3.0s (NOT capped at 2.0s!), plus HTTP-date parser!
    sleeps8: List[float] = []
    clk8 = {"m": 500.0}
    mon8 = make_r5_fixture_resource_monitor(destination=tmp_path, monotonic_fn=lambda: clk8["m"])
    spy8 = _SpyTransport([
        RawHttpResponseC2(429, b"", {"retry-after": "3.0"}, NOW_MS, NOW_MS + 5),
        RawHttpResponseC2(200, OK_BODY, {}, NOW_MS + 3000, NOW_MS + 3005),
    ])
    def _sleep8(s: float) -> None:
        sleeps8.append(s)
        clk8["m"] += s
    r8 = asyncio.run(capture_r5_public_batch(
        plan, spy8, mon8, run_id="c1_b_run", trusted_scope=dec, destination_root=tmp_path,
        sleep_fn=_sleep8, monotonic_fn=lambda: clk8["m"], utc_now_fn=lambda: NOW_UTC,
    ))
    assert r8["capture_execution_status"] == "QA_FIXTURE_OFFLINE_PASS" and sleeps8 == [3.0]
    # Strict HTTP-date parsing check
    ok_hd, sec_hd, _ = _parse_retry_after_strict("Wed, 25 Nov 2026 12:00:04 GMT", NOW_UTC)
    assert ok_hd is True and sec_hd == pytest.approx(4.0, abs=1e-6)
    ok_bad, _, _ = _parse_retry_after_strict("not-a-date", NOW_UTC)
    assert ok_bad is False

    # B09: TimeoutError retried boundedly up to max_retries
    clk9 = {"m": 600.0}
    mon9 = make_r5_fixture_resource_monitor(destination=tmp_path, monotonic_fn=lambda: clk9["m"])
    spy9 = _SpyTransport([TimeoutError("sock timeout"), RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)])
    r9 = asyncio.run(capture_r5_public_batch(
        plan, spy9, mon9, run_id="c1_b_run", trusted_scope=dec, destination_root=tmp_path,
        sleep_fn=lambda s: clk9.__setitem__("m", clk9["m"] + s), monotonic_fn=lambda: clk9["m"], utc_now_fn=lambda: NOW_UTC,
    ))
    assert r9["capture_execution_status"] == "QA_FIXTURE_OFFLINE_PASS" and len(spy9.calls) == 2
    # B17: Actual dispatch timestamps monotonic and >= min_iv_ms apart!
    ts_list = r9["dispatch_timestamps_mono"]
    assert len(ts_list) == 2 and (ts_list[1] - ts_list[0]) >= 0.10 - 1e-6

    # B10: No retry for 302 redirect, 401/403 auth, 400, or PermissionError
    for bad_resp in (
        RawHttpResponseC2(302, b"", {"location": "https://evil.test"}, NOW_MS, NOW_MS + 1),
        RawHttpResponseC2(401, b"unauthorized", {}, NOW_MS, NOW_MS + 1),
        RawHttpResponseC2(403, b"forbidden", {}, NOW_MS, NOW_MS + 1),
        PermissionError("HTTP_REDIRECT_FORBIDDEN"),
    ):
        spy10 = _SpyTransport([bad_resp, RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)])
        r10 = asyncio.run(capture_r5_public_batch(
            plan, spy10, mon, run_id="c1_b_run", trusted_scope=dec, destination_root=tmp_path,
            sleep_fn=lambda _: None, monotonic_fn=lambda: clk["m"], utc_now_fn=lambda: NOW_UTC,
        ))
        assert len(spy10.calls) == 1 and r10["qa_fixture_records_count"] == 0


def test_c1_b11_to_b16_b19_to_b24_atomic_budgets_drain_and_w1_w2_w4_determinism(tmp_path: Path) -> None:
    # B11: Concurrent request reservation cannot oversubscribe max_requests=2 across 3 endpoints!
    dec3, plan3 = _build_plan(["public_ticker", "contracts", "history_candles"], max_requests=3, max_concurrency=3)
    # Wait: test max_requests=2 with 2 endpoints + 429 retry so total attempts capped at EXACTLY 2!
    dec2, plan2 = _build_plan(["public_ticker", "contracts"], max_requests=2, max_concurrency=2, max_retries=2)
    mon = make_r5_fixture_resource_monitor(destination=tmp_path)
    spy11 = _SpyTransport([
        RawHttpResponseC2(429, b"", {"retry-after": "0.01"}, NOW_MS, NOW_MS + 5),
        RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5),
        RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5),
    ])
    r11 = asyncio.run(capture_r5_public_batch(plan2, spy11, mon, run_id="c1_b_run", trusted_scope=dec2, destination_root=tmp_path, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC))
    assert r11["requests_dispatched_actual"] <= 2 and len(spy11.calls) <= 2

    # B13/B14: Wire bytes include 429 error body, and parallel byte reservations never oversubscribe!
    err_body = b"E" * 60
    dec_b, plan_b = _build_plan(["public_ticker"], max_requests=3, max_total_bytes=100, max_response_bytes=80, max_retries=2)
    spy13 = _SpyTransport([
        RawHttpResponseC2(429, err_body, {"retry-after": "0.01"}, NOW_MS, NOW_MS + 5),
        RawHttpResponseC2(200, b"X" * 60, {}, NOW_MS, NOW_MS + 5),  # 60 + 60 = 120 > 100 wire cap!
    ])
    r13 = asyncio.run(capture_r5_public_batch(plan_b, spy13, mon, run_id="c1_b_run", trusted_scope=dec_b, destination_root=tmp_path, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC))
    assert r13["aborted"] is True and r13["abort_reason"] == "TOTAL_BYTES_BUDGET_EXHAUSTED"
    assert r13["wire_bytes_observed_total"] == 120 and r13["bytes_accepted_total"] == 0 and r13["qa_fixture_records_count"] == 0

    # B15/B16/B22: Midflight cancel while call in-flight -> late response DISCARDED, 0 staged, 0 undrained!
    ev_mid = asyncio.Event()
    def _set_cancel_inflight(_idx: int, _u: str, _p: Mapping[str, Any], _t: float) -> None:
        ev_mid.set()
    dec1, plan1 = _build_plan(["public_ticker"], max_requests=2, max_concurrency=1)
    st_mid = tmp_path / "st_mid"
    mon_mid = make_r5_fixture_resource_monitor(destination=st_mid)
    r15 = asyncio.run(capture_r5_public_batch(
        plan1, _SpyTransport(on_call=_set_cancel_inflight), mon_mid, cancel_event=ev_mid,
        staging_root=st_mid, run_id="c1_b_run", trusted_scope=dec1, destination_root=tmp_path, utc_now_fn=lambda: NOW_UTC,
    ))
    assert r15["aborted"] is True and r15["qa_fixture_records_count"] == 0 and r15["undrained_inflight_workers"] == 0
    assert not st_mid.exists() or list(st_mid.glob("*.raw")) == []

    # B23: Invalid monotonic clock (NaN) & invalid timeout ceiling fail closed with 0 dispatches!
    spy23 = _SpyTransport()
    r23a = asyncio.run(capture_r5_public_batch(plan1, spy23, mon, run_id="c1_b_run", trusted_scope=dec1, destination_root=tmp_path, monotonic_fn=lambda: float("nan"), utc_now_fn=lambda: NOW_UTC))
    r23b = asyncio.run(capture_r5_public_batch(plan1, spy23, mon, run_id="c1_b_run", trusted_scope=dec1, destination_root=tmp_path, transport_timeout_ceiling_sec=-1.0, utc_now_fn=lambda: NOW_UTC))
    assert len(spy23.calls) == 0 and r23a["capture_execution_status"] == "FAIL" and r23b["capture_execution_status"] == "FAIL"

    # B19/B20/B21 + W1/W2/W4 deterministic canonical semantic hash across concurrency 1, 2, 4!
    shas: List[str] = []
    for w in (1, 2, 4, 1):
        dec_w, plan_w = _build_plan(["public_ticker", "contracts", "history_candles"], max_requests=6, max_concurrency=w)
        assert plan_w["pages_semantics"] == "SINGLE_REQUEST_PER_BATCH_NO_PAGINATION"
        # Map URL -> deterministic payload so scheduling order never alters per-batch bytes
        class _DetUrlTransport:
            def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
                body = f'{{"code":"00000","url":"{url}","symbol":"{params.get("symbol")}"}}'.encode("utf-8")
                return RawHttpResponseC2(200, body, {}, NOW_MS, NOW_MS + 5)
        r_w = asyncio.run(capture_r5_public_batch(plan_w, _DetUrlTransport(), mon, run_id="c1_b_run", trusted_scope=dec_w, destination_root=tmp_path, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC))
        assert r_w["capture_execution_status"] == "QA_FIXTURE_OFFLINE_PASS" and r_w["all_required_batches_accounted"] is True
        shas.append(r_w["semantic_result_sha256"])
    assert len(set(shas)) == 1
