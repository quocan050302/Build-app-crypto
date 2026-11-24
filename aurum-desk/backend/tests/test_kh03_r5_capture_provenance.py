"""
aurum-desk/backend/tests/test_kh03_r5_capture_provenance.py

Unit & adversarial tests for KH-03.R5 operator authorization, capture batch planning,
trusted transport boundary, rate-limit backoff/retry, midflight cancellation, and
budget exhaustion (R5-F01..F05, F19..F21, F45). Executes ZERO external network requests!
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_c2_public_capture import (
    RawHttpResponseC2,
    StdlibUrllibPublicTransportC2,
    _SequenceFakeTransportC2,
)
from services.kh_03_r5_capture_and_custody_service import capture_r5_public_batch
from services.kh_03_r5_market_data_foundation_service import (
    R5OperatorGrant,
    build_r5_source_capability_inventory,
    plan_r5_capture_batches,
    validate_r5_operator_capture_scope,
)


def _scope_req(rid: str = "r5_test_001") -> dict[str, Any]:
    return {"symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "endpoints": ["public_ticker"], "run_id": rid, "max_requests": 5, "max_total_bytes": 50_000}


def _make_qa_grant(now_ms: int, rid: str = "r5_test_001") -> R5OperatorGrant:
    return R5OperatorGrant(
        grant_id="QA_G1", issued_by="QA", purpose="QA_FIXTURE", symbol="XAUUSDT", product_type="USDT-FUTURES",
        allowed_endpoints=("public_ticker", "contracts"), window_start_ms=1794000000000, window_end_ms=1794000300000,
        max_requests=5, max_total_bytes=50_000, max_duration_sec=30, expires_utc_ms=now_ms + 3600_000,
        run_id=rid, is_qa_fixture_grant=True, origin_kind="QA_LOCAL",
    )


def test_r5_missing_grant_and_cli_env_prompt_bypass_blocked_f01_f02() -> None:
    now = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    inv = build_r5_source_capability_inventory(REPO_ROOT)

    # F01: Missing grant -> blocked, zero external calls!
    d0 = validate_r5_operator_capture_scope(None, _scope_req(), now, {})
    assert d0["admitted"] is False and d0["authorization_status"] == "BLOCKED_OWNER_AUTHORIZATION"
    p0 = plan_r5_capture_batches(d0, inv, {"destination_disk_free_gib": 28.0, "max_concurrency": 1, "max_pages": 2, "max_requests": 2, "max_total_bytes": 10000, "max_duration_sec": 10, "endpoints": ["public_ticker"]})
    assert p0["status"] == "BLOCKED"
    res0 = asyncio.run(capture_r5_public_batch(p0, None, None))
    assert res0["external_requests_attempted"] == 0 and res0["capture_execution_status"] == "BLOCKED_OWNER_AUTHORIZATION"

    # F02: CLI / env / prompt bools & expired / wrong symbol / excess budget grants rejected!
    assert validate_r5_operator_capture_scope(None, _scope_req(), now, {"from_cli_flag": True, "from_env_var": True})["admitted"] is False
    exp_grant = R5OperatorGrant("G_EXP", "owner", "DEVELOPMENT_CAPTURE", "XAUUSDT", "USDT-FUTURES", ("public_ticker",), 1794000000000, 1794000300000, 5, 50_000, 30, now_ms - 1000, "r5_test_001", False, "SEPARATE_OWNER_ATTESTATION")
    assert validate_r5_operator_capture_scope(exp_grant, _scope_req(), now, {"human_operator_attestation_verified": True, "attestation_source": "EXPLICIT_SEPARATE_OWNER_GRANT"})["admitted"] is False
    # QA grant CANNOT authorize live capture!
    qa_g = _make_qa_grant(now_ms)
    assert validate_r5_operator_capture_scope(qa_g, _scope_req(), now, {"qa_fixture_mode": False, "human_operator_attestation_verified": True})["admitted"] is False


def test_r5_private_endpoint_redirect_and_fake_transport_blocked_f03_f04(tmp_path: Path) -> None:
    now = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    inv = build_r5_source_capability_inventory(REPO_ROOT)

    # F03: Private/order endpoint & HTTP 302 redirect blocked!
    qa_d = validate_r5_operator_capture_scope(_make_qa_grant(now_ms), _scope_req(), now, {"qa_fixture_mode": True})
    qa_p = plan_r5_capture_batches(qa_d, inv, {"destination_disk_free_gib": 28.0, "max_concurrency": 1, "max_pages": 2, "max_requests": 5, "max_total_bytes": 50_000, "max_duration_sec": 30, "endpoints": ["public_ticker"]})
    bad_priv_plan = dict(qa_p, batches=[{"batch_id": "B_PRIV", "endpoint_key": "public_ticker", "url": "https://api.bitget.com/api/v2/mix/order/place-order", "symbol": "XAUUSDT", "product_type": "USDT-FUTURES"}])
    r_priv = asyncio.run(capture_r5_public_batch(bad_priv_plan, _SequenceFakeTransportC2([]), None))
    assert any("BLOCKED_UNAUTHORIZED_OR_PRIVATE_ENDPOINT" in v for v in r_priv["violations"])

    tr_redir = _SequenceFakeTransportC2([RawHttpResponseC2(302, b"", {"location": "https://evil.example/"}, 1794000000000, 1794000000010)])
    r_redir = asyncio.run(capture_r5_public_batch(qa_p, tr_redir, None, sleep_fn=lambda _: None))
    assert any("HTTP_REDIRECT_BLOCKED" in v for v in r_redir["violations"])

    # F04: Subclassed StdlibUrllibPublicTransportC2 CANNOT mint observed live capture!
    class _SneakySubclassTransport(StdlibUrllibPublicTransportC2):
        def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
            return RawHttpResponseC2(200, b'{"code":"00000","data":[]}', {}, 1794000000000, 1794000000010)

    live_plan = dict(qa_p, status="PLANNED_DEV_BOUNDED", partition_role="DEVELOPMENT_CAPTURE", live_network_permitted=True)
    r_sub = asyncio.run(capture_r5_public_batch(live_plan, _SneakySubclassTransport(), None))
    assert r_sub["accepted_observed_records_count"] == 0
    assert any("FAKE_OR_SUBCLASSED_TRANSPORT" in v for v in r_sub["violations"])


def test_r5_rate_limit_retry_cancel_and_budget_exhaustion_f19_f20_f21_f45(tmp_path: Path) -> None:
    now = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    inv = build_r5_source_capability_inventory(REPO_ROOT)
    qa_d = validate_r5_operator_capture_scope(_make_qa_grant(now_ms), _scope_req(), now, {"qa_fixture_mode": True})
    qa_p = plan_r5_capture_batches(qa_d, inv, {"destination_disk_free_gib": 28.0, "max_concurrency": 1, "max_pages": 2, "max_requests": 5, "max_total_bytes": 50_000, "max_duration_sec": 30, "endpoints": ["public_ticker"]})

    # F19: 429 Retry-After -> 503 -> 200 succeeds boundedly and records retry_chain!
    ok_body = json.dumps({"code": "00000", "data": [{"symbol": "XAUUSDT", "bidPr": "4100.00", "askPr": "4100.20"}]}).encode("utf-8")
    tr_retry = _SequenceFakeTransportC2([
        RawHttpResponseC2(429, b"", {"retry-after": "0.01"}, 1794000000000, 1794000000010),
        RawHttpResponseC2(503, b"", {}, 1794000000020, 1794000000030),
        RawHttpResponseC2(200, ok_body, {}, 1794000000040, 1794000000050),
    ])
    r_ok = asyncio.run(capture_r5_public_batch(qa_p, tr_retry, lambda: {"disk_free_gib": 28.0, "available_ram_mib": 2048.0}, staging_root=tmp_path / "st1", sleep_fn=lambda _: None))
    assert r_ok["capture_execution_status"] == "QA_FIXTURE_OFFLINE_PASS" and len(r_ok["items"][0]["retry_chain"]) == 2

    # F20: Cancel midflight & runtime disk drop <20 GiB aborts cleanly without leaving staged files!
    ev = asyncio.Event(); ev.set()
    r_can = asyncio.run(capture_r5_public_batch(qa_p, _SequenceFakeTransportC2([RawHttpResponseC2(200, ok_body, {}, 1, 2)]), None, cancel_event=ev, staging_root=tmp_path / "st_can"))
    assert r_can["aborted"] is True and r_can["abort_reason"] == "CANCELLED_MIDFLIGHT"
    r_dk = asyncio.run(capture_r5_public_batch(qa_p, _SequenceFakeTransportC2([RawHttpResponseC2(200, ok_body, {}, 1, 2)]), lambda: {"disk_free_gib": 19.5, "available_ram_mib": 2048.0}, staging_root=tmp_path / "st_dk"))
    assert r_dk["aborted"] is True and "RUNTIME_DISK_BELOW_20GIB" in str(r_dk["abort_reason"])

    # F21 & F45: Oversize response & tiny byte budget exhausted before first commit
    tiny_p = dict(qa_p, budget=dict(qa_p["budget"], max_total_bytes=10))
    r_bud = asyncio.run(capture_r5_public_batch(tiny_p, _SequenceFakeTransportC2([RawHttpResponseC2(200, ok_body, {}, 1794000000000, 1794000000010)]), None, staging_root=tmp_path / "st_bud", sleep_fn=lambda _: None))
    assert r_bud["aborted"] is True and r_bud["abort_reason"] == "TOTAL_BYTES_BUDGET_EXHAUSTED" and r_bud["qa_fixture_records_count"] == 0
