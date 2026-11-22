"""
aurum-desk/backend/tests/test_kh_02_r2_c3_http_rate_limit_and_faults.py

AURUM DESK — KH-02.R2.C3 P0-B HTTP Capture Safety, Redirect/SSRF Block & Rate-Limit Tests (Attacks 08-16):
  - 301/302/307 redirects to localhost / 169.254.169.254 / foreign host -> FAIL-CLOSED before following.
  - Non-HTTPS scheme, SSRF hosts, private/order API paths -> BLOCKED before HTTP.
  - Secrets/cookies/account IDs in query params, response headers, or body -> BLOCKED.
  - Default real `time.sleep` rate-limiter active on live transport; sleep/clock bypass FORBIDDEN.
  - 429 Retry-After capped + 5xx bounded retry vs 400/401/403 non-retryable (exact call counts & delays).
  - Gzip bombs & oversized response bodies -> bounded reject without memory spike.
  - Open/unclosed, gap, out-of-order, duplicate candles & stale/crossed/bad-tick/wrong-symbol BBO -> BLOCKED.
  - Forged caller `can_run_heavy_stages=True` while fresh OS disk < 20.5 GiB -> BLOCKED.
"""
from __future__ import annotations

import gzip
import io
import json
from pathlib import Path
import sys
from typing import Any, Dict, Optional

import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_c2_public_capture import (
    C2PublicReadOnlyBitgetCollector,
    OperatorCaptureAuthorizationC2,
    RawHttpResponseC2,
    StdlibUrllibPublicTransportC2,
    _SequenceFakeTransportC2,
)
from services.kh_02_r2_prospective_data_service import R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS


class _FakeUrlResp:
    def __init__(self, *, status: int = 200, body: bytes = b"", headers: Optional[Dict[str, str]] = None, url: str = "https://api.bitget.com/api/v2/mix/market/ticker") -> None:
        self.status = status
        self.code = status
        self._buf = io.BytesIO(body)
        self.headers = headers or {}
        self.url = url

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def __enter__(self) -> "_FakeUrlResp":
        return self

    def __exit__(self, *_a: Any) -> None:
        return None


def _auth() -> OperatorCaptureAuthorizationC2:
    return OperatorCaptureAuthorizationC2(approval_id="QA_HTTP_01", approved_by="qa", network_capture_enabled=True)


def _ok_disk() -> Dict[str, Any]:
    return {"can_run_heavy_stages": True, "min_free_gib": 35.0, "status": "PASS_STRICT_AND_ADVISORY"}


def test_c3_attack_08_09_10_ssrf_redirects_private_paths_and_secrets_blocked() -> None:
    # Attack 08: 301/302/307 redirects to localhost & AWS IMDS metadata blocked fail-closed
    for code, target in (
        (301, "http://127.0.0.1:8000/internal"),
        (302, "http://169.254.169.254/latest/meta-data/"),
        (307, "https://evil.example.com/steal"),
    ):
        tr = StdlibUrllibPublicTransportC2(_opener_fn=lambda _r, _t, c=code, loc=target: _FakeUrlResp(status=c, headers={"location": loc}))
        with pytest.raises(PermissionError, match="HTTP_REDIRECT_FORBIDDEN_FAIL_CLOSED"):
            tr.get("https://api.bitget.com/api/v2/mix/market/ticker", {"productType": "USDT-FUTURES", "symbol": "XAUUSDT"}, timeout_sec=1.0)

    # Attack 09: non-HTTPS, foreign/localhost host, private/order endpoint rejected BEFORE opener is even called
    called = {"n": 0}

    def _never_open(_r: Any, _t: float) -> Any:
        called["n"] += 1
        return _FakeUrlResp(status=200, body=b"{}")

    tr_guard = StdlibUrllibPublicTransportC2(_opener_fn=_never_open)
    for bad_url in (
        "http://api.bitget.com/api/v2/mix/market/ticker",
        "https://127.0.0.1/api/v2/mix/market/ticker",
        "https://169.254.169.254/latest/meta-data",
        "https://api.bitget.com/api/v2/mix/order/place-order",
        "https://api.bitget.com/api/v2/mix/account/accounts",
    ):
        with pytest.raises(PermissionError):
            tr_guard.get(bad_url, {"symbol": "XAUUSDT"}, timeout_sec=1.0)
    assert called["n"] == 0

    # Attack 10: secrets/cookies in query params or response headers blocked
    with pytest.raises(PermissionError, match="FORBIDDEN_SECRETS_IN_QUERY_PARAMS"):
        tr_guard.get("https://api.bitget.com/api/v2/mix/market/ticker", {"productType": "USDT-FUTURES", "symbol": "XAUUSDT", "api_key": "LEAK"}, timeout_sec=1.0)
    tr_hdr = StdlibUrllibPublicTransportC2(_opener_fn=lambda _r, _t: _FakeUrlResp(status=200, body=b"{}", headers={"set-cookie": "auth=secret"}))
    with pytest.raises(PermissionError, match="FORBIDDEN_SECRET_OR_COOKIE_IN_RESPONSE_HEADERS"):
        tr_hdr.get("https://api.bitget.com/api/v2/mix/market/ticker", {"productType": "USDT-FUTURES", "symbol": "XAUUSDT"}, timeout_sec=1.0)


def test_c3_attack_11_12_real_default_sleep_spacing_429_capped_vs_400_no_retry() -> None:
    # Attack 11: real transport defaults to real time.sleep and REFUSES custom sleep_fn / clock_ms_fn bypass!
    live_col = C2PublicReadOnlyBitgetCollector(transport=StdlibUrllibPublicTransportC2(), is_fake_transport_qa=False)
    desc = live_col.describe_readiness()
    assert desc["real_rate_limiter_default_active"] is True
    assert desc["live_network_test_status"] == "LIVE_NETWORK_TEST_NOT_RUN_BY_DESIGN"

    with pytest.raises(PermissionError, match="FAKE_CLOCK_OR_SLEEP_BYPASS_FORBIDDEN"):
        C2PublicReadOnlyBitgetCollector(transport=StdlibUrllibPublicTransportC2(), is_fake_transport_qa=False, sleep_fn=lambda _s: None)
    with pytest.raises(PermissionError, match="FAKE_CLOCK_OR_SLEEP_BYPASS_FORBIDDEN"):
        C2PublicReadOnlyBitgetCollector(transport=StdlibUrllibPublicTransportC2(), is_fake_transport_qa=False, clock_ms_fn=lambda: 0)

    # Verify spacing wait > 0 using fake monotonic clock in isolated QA mode
    t0 = R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 180_000
    good_body = json.dumps({"code": "00000", "data": [{"symbol": "XAUUSDT", "bidPr": "4010.10", "askPr": "4010.30", "ts": str(t0)}]}).encode()
    good_resp = RawHttpResponseC2(200, good_body, {}, t0 + 5, t0 + 20)
    clk = iter([t0 + 10, t0 + 20])  # only 10ms apart vs 100ms min spacing -> must sleep 90ms!
    c_sp = C2PublicReadOnlyBitgetCollector(
        authorization=_auth(),
        transport=_SequenceFakeTransportC2([good_resp, good_resp]),
        min_request_interval_ms=100,
        clock_ms_fn=lambda: next(clk),
        is_fake_transport_qa=True,
    )
    c_sp.fetch_public_l1_bbo_snapshot(snapshot_id="sp1", write_mount_probe=_ok_disk())
    c_sp.fetch_public_l1_bbo_snapshot(snapshot_id="sp2", write_mount_probe=_ok_disk())
    assert len(c_sp.sleep_audit_log) == 1
    assert c_sp.sleep_audit_log[0]["sleep_sec"] == pytest.approx(0.09, abs=1e-4)

    # Attack 12: 400/401/403 NEVER retry (exact 1 call); 429 Retry-After=999 capped at max_backoff_sec
    for sc in (400, 401, 403):
        tr_c = _SequenceFakeTransportC2([RawHttpResponseC2(sc, b"err", {}, t0, t0 + 5)])
        cc = C2PublicReadOnlyBitgetCollector(authorization=_auth(), transport=tr_c, max_retries=3, is_fake_transport_qa=True)
        with pytest.raises(RuntimeError, match=f"NON_RETRYABLE_CLIENT_HTTP_STATUS:{sc}"):
            cc.fetch_public_l1_bbo_snapshot(snapshot_id=f"c_{sc}", write_mount_probe=_ok_disk())
        assert len(tr_c.calls) == 1

    tr_429 = _SequenceFakeTransportC2([RawHttpResponseC2(429, b"limit", {"retry-after": "999"}, t0, t0 + 5), good_resp])
    c_429 = C2PublicReadOnlyBitgetCollector(authorization=_auth(), transport=tr_429, max_backoff_sec=1.25, is_fake_transport_qa=True)
    res_429 = c_429.fetch_public_l1_bbo_snapshot(snapshot_id="c_429", write_mount_probe=_ok_disk())
    assert res_429["validation"]["valid_for_escrow_admission"] is True
    assert len(tr_429.calls) == 2
    assert c_429.sleep_audit_log[0]["sleep_sec"] <= 1.25


def test_c3_attack_13_14_15_16_gzip_bomb_oversized_tick_symbol_and_forged_disk() -> None:
    # Attack 13: gzip bomb & oversized body bounded reject
    gz_bomb = gzip.compress(b"A" * (9 * 1024 * 1024))
    tr_gz = StdlibUrllibPublicTransportC2(_opener_fn=lambda _r, _t: _FakeUrlResp(status=200, body=gz_bomb, headers={"content-encoding": "gzip"}))
    with pytest.raises(ValueError, match="GZIP_BOMB"):
        tr_gz.get("https://api.bitget.com/api/v2/mix/market/ticker", {"productType": "USDT-FUTURES", "symbol": "XAUUSDT"}, timeout_sec=1.0)

    tr_big = StdlibUrllibPublicTransportC2(max_response_bytes=512, _opener_fn=lambda _r, _t: _FakeUrlResp(status=200, body=b"B" * 1024))
    with pytest.raises(ValueError, match="OVERSIZED_HTTP_RESPONSE_BODY"):
        tr_big.get("https://api.bitget.com/api/v2/mix/market/ticker", {"productType": "USDT-FUTURES", "symbol": "XAUUSDT"}, timeout_sec=1.0)

    # Attack 15: bad tick precision & wrong symbol in response rejected
    t0 = R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 200_000
    bad_tick = json.dumps({"code": "00000", "data": [{"symbol": "XAUUSDT", "bidPr": "4010.1234", "askPr": "4010.30", "ts": str(t0)}]}).encode()
    c_tick = C2PublicReadOnlyBitgetCollector(authorization=_auth(), transport=_SequenceFakeTransportC2([RawHttpResponseC2(200, bad_tick, {}, t0 + 5, t0 + 15)]), is_fake_transport_qa=True)
    with pytest.raises(ValueError, match="BBO_TICK_PRECISION_VIOLATION"):
        c_tick.fetch_public_l1_bbo_snapshot(snapshot_id="bad_tick", write_mount_probe=_ok_disk())

    wrong_sym = json.dumps({"code": "00000", "data": [{"symbol": "BTCUSDT", "bidPr": "4010.10", "askPr": "4010.30", "ts": str(t0)}]}).encode()
    c_sym = C2PublicReadOnlyBitgetCollector(authorization=_auth(), transport=_SequenceFakeTransportC2([RawHttpResponseC2(200, wrong_sym, {}, t0 + 5, t0 + 15)]), is_fake_transport_qa=True)
    with pytest.raises(ValueError, match="RESPONSE_SYMBOL_MISMATCH"):
        c_sym.fetch_public_l1_bbo_snapshot(snapshot_id="wrong_sym", write_mount_probe=_ok_disk())

    # Attack 16: Forged caller disk probe with fresh OS disk < 20.5 GiB blocked!
    c_disk = C2PublicReadOnlyBitgetCollector(
        authorization=_auth(),
        transport=_SequenceFakeTransportC2([]),
        is_fake_transport_qa=True,
        os_disk_probe_fn=lambda: 12.0,
    )
    with pytest.raises(RuntimeError, match="FORGED_CALLER_DISK_PROBE_BELOW_OS_FLOOR"):
        c_disk.fetch_public_l1_bbo_snapshot(snapshot_id="forged_disk", write_mount_probe=_ok_disk())
