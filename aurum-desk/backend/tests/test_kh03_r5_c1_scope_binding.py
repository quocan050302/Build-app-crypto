"""
aurum-desk/backend/tests/test_kh03_r5_c1_scope_binding.py

KH-03.R5.C1 — Phase A Strict Authorization & Scope Binding Negative/Positive Controls (A01..A28).
Executes ZERO external network sockets; uses deterministic offline spy transports only.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
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
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

NOW_UTC = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
NOW_MS = int(NOW_UTC.timestamp() * 1000)
WS_MS = 1794000000000
WE_MS = 1794000600000


class _SpyOfflineTransport:
    def __init__(self, responses: Optional[List[RawHttpResponseC2]] = None, on_call: Any = None) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.responses = list(responses or [])
        self.on_call = on_call

    def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
        idx = len(self.calls)
        self.calls.append({"url": url, "params": dict(params), "timeout_sec": float(timeout_sec)})
        if self.on_call is not None:
            self.on_call(idx, url, params)
        if self.responses:
            return self.responses.pop(0)
        return RawHttpResponseC2(200, b'{"code":"00000","data":[{"symbol":"XAUUSDT"}]}', {}, NOW_MS, NOW_MS + 5)


def _qa_grant(rid: str = "c1_run_01", **over: Any) -> R5OperatorGrant:
    kw: Dict[str, Any] = dict(
        grant_id="QA_C1_G01",
        issued_by="QA_HARNESS",
        purpose="QA_FIXTURE",
        symbol="XAUUSDT",
        product_type="USDT-FUTURES",
        allowed_endpoints=("public_ticker", "contracts"),
        window_start_ms=WS_MS,
        window_end_ms=WE_MS,
        max_requests=10,
        max_total_bytes=100_000,
        max_duration_sec=30,
        expires_utc_ms=NOW_MS + 60_000,
        run_id=rid,
        is_qa_fixture_grant=True,
        origin_kind="QA_LOCAL",
    )
    kw.update(over)
    return R5OperatorGrant(**kw)


def _req(rid: str = "c1_run_01", **over: Any) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "symbol": "XAUUSDT",
        "product_type": "USDT-FUTURES",
        "endpoints": ["public_ticker"],
        "run_id": rid,
        "purpose": "QA_FIXTURE",
        "window_start_ms": WS_MS,
        "window_end_ms": WE_MS,
        "max_requests": 5,
        "max_total_bytes": 50_000,
        "max_duration_sec": 30,
    }
    d.update(over)
    return d


def _bud(**over: Any) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "destination_disk_free_gib": 30.0,
        "max_concurrency": 1,
        "max_pages": 5,
        "max_requests": 5,
        "max_total_bytes": 50_000,
        "max_duration_sec": 30,
        "endpoints": ["public_ticker"],
    }
    d.update(over)
    return d


def test_c1_a01_to_a19_strict_grant_schema_and_trust_boundary() -> None:
    # A01 missing grant -> BLOCKED_OWNER_AUTHORIZATION
    r01 = validate_r5_operator_capture_scope(None, _req(purpose="DEVELOPMENT_CAPTURE"), NOW_UTC, {})
    assert r01["admitted"] is False and r01["authorization_status"] == "BLOCKED_OWNER_AUTHORIZATION"

    # A02 bare dataclass for live capture without genuine external verifier -> BLOCKED_OWNER_AUTHORIZATION
    live_g = _qa_grant(purpose="DEVELOPMENT_CAPTURE", is_qa_fixture_grant=False, origin_kind="SEPARATE_OWNER_ATTESTATION")
    r02 = validate_r5_operator_capture_scope(live_g, _req(purpose="DEVELOPMENT_CAPTURE"), NOW_UTC, {})
    assert r02["admitted"] is False and r02["live_network_permitted"] is False and r02["authorization_status"] == "BLOCKED_OWNER_AUTHORIZATION"

    # A03 forged trust_context dict claiming human_operator_attestation_verified=True -> REJECTED by default-deny verifier!
    r03 = validate_r5_operator_capture_scope(
        live_g, _req(purpose="DEVELOPMENT_CAPTURE"), NOW_UTC,
        {"human_operator_attestation_verified": True, "attestation_source": "EXPLICIT_SEPARATE_OWNER_GRANT", "qa_fixture_mode": False},
    )
    assert r03["admitted"] is False and r03["live_network_permitted"] is False and r03["authorization_status"] == "BLOCKED_OWNER_AUTHORIZATION"

    # A04 CLI / env / prompt flags rejected
    for flag in ("from_cli_flag", "from_env_var", "from_prompt_text"):
        r04 = validate_r5_operator_capture_scope(_qa_grant(), _req(), NOW_UTC, {"qa_fixture_mode": True, flag: True})
        assert r04["admitted"] is False

    # A05 QA grant promoted to live -> rejected
    r05 = validate_r5_operator_capture_scope(_qa_grant(), _req(), NOW_UTC, {"qa_fixture_mode": False, "human_operator_attestation_verified": True})
    assert r05["admitted"] is False and r05["live_network_permitted"] is False

    # A06 expired grant
    r06 = validate_r5_operator_capture_scope(_qa_grant(expires_utc_ms=NOW_MS - 1), _req(), NOW_UTC, {"qa_fixture_mode": True})
    assert r06["admitted"] is False and any("OPERATOR_GRANT_EXPIRED" in v for v in r06["violations"])

    # A07 wrong run_id
    r07 = validate_r5_operator_capture_scope(_qa_grant(run_id="run_A"), _req(rid="run_B"), NOW_UTC, {"qa_fixture_mode": True})
    assert r07["admitted"] is False and any("RUN_ID_SCOPE_MISMATCH" in v for v in r07["violations"])

    # A08 wrong symbol & A09 wrong productType
    r08 = validate_r5_operator_capture_scope(_qa_grant(), _req(symbol="BTCUSDT"), NOW_UTC, {"qa_fixture_mode": True})
    assert r08["admitted"] is False and any("SYMBOL_OR_PRODUCT_TYPE_SCOPE_MISMATCH" in v for v in r08["violations"])
    r09 = validate_r5_operator_capture_scope(_qa_grant(), _req(product_type="SPOT"), NOW_UTC, {"qa_fixture_mode": True})
    assert r09["admitted"] is False

    # A10 endpoint outside grant but inside allowlist
    r10 = validate_r5_operator_capture_scope(_qa_grant(allowed_endpoints=("public_ticker",)), _req(endpoints=["contracts"]), NOW_UTC, {"qa_fixture_mode": True})
    assert r10["admitted"] is False and any("REQUESTED_ENDPOINT_OUTSIDE_GRANT" in v for v in r10["violations"])

    # A12 forbidden private/unknown endpoint
    r12 = validate_r5_operator_capture_scope(_qa_grant(), _req(endpoints=["place_order"]), NOW_UTC, {"qa_fixture_mode": True})
    assert r12["admitted"] is False

    # A13 start before grant, A14 end after grant, A15 inverted window
    r13 = validate_r5_operator_capture_scope(_qa_grant(), _req(window_start_ms=WS_MS - 60_000), NOW_UTC, {"qa_fixture_mode": True})
    assert r13["admitted"] is False and any("REQUESTED_WINDOW_START_BEFORE_GRANT" in v for v in r13["violations"])
    r14 = validate_r5_operator_capture_scope(_qa_grant(), _req(window_end_ms=WE_MS + 60_000), NOW_UTC, {"qa_fixture_mode": True})
    assert r14["admitted"] is False and any("REQUESTED_WINDOW_END_AFTER_GRANT" in v for v in r14["violations"])
    r15 = validate_r5_operator_capture_scope(_qa_grant(), _req(window_start_ms=WE_MS, window_end_ms=WS_MS), NOW_UTC, {"qa_fixture_mode": True})
    assert r15["admitted"] is False and any("INVALID_CAPTURE_TIME_WINDOW" in v for v in r15["violations"])

    # A16/A17/A18 budgets larger than grant
    assert validate_r5_operator_capture_scope(_qa_grant(max_requests=3), _req(max_requests=4), NOW_UTC, {"qa_fixture_mode": True})["admitted"] is False
    assert validate_r5_operator_capture_scope(_qa_grant(max_total_bytes=1000), _req(max_total_bytes=1001), NOW_UTC, {"qa_fixture_mode": True})["admitted"] is False
    assert validate_r5_operator_capture_scope(_qa_grant(max_duration_sec=10), _req(max_duration_sec=11), NOW_UTC, {"qa_fixture_mode": True})["admitted"] is False

    # A19 zero / bool / NaN / Inf / missing budgets rejected fail-closed!
    for bad_over in (
        {"max_requests": 0},
        {"max_requests": True},
        {"max_total_bytes": False},
        {"max_duration_sec": float("nan")},
        {"max_duration_sec": float("inf")},
        {"max_duration_sec": None},
        {"window_start_ms": None},
    ):
        r19 = validate_r5_operator_capture_scope(_qa_grant(), _req(**bad_over), NOW_UTC, {"qa_fixture_mode": True})
        assert r19["admitted"] is False, f"Bad scope accepted: {bad_over}"

    # A26 duplicate endpoints & A27 OOS purpose / destination
    assert validate_r5_operator_capture_scope(_qa_grant(), _req(endpoints=["public_ticker", "public_ticker"]), NOW_UTC, {"qa_fixture_mode": True})["admitted"] is False
    assert validate_r5_operator_capture_scope(_qa_grant(purpose="FRESH_OOS"), _req(purpose="FRESH_OOS"), NOW_UTC, {"qa_fixture_mode": True})["admitted"] is False
    assert validate_r5_operator_capture_scope(_qa_grant(), _req(destination_scope="/tmp/real_oos_escrow"), NOW_UTC, {"qa_fixture_mode": True})["admitted"] is False

    # A28 invalid attestation verifier interface fails closed
    r28 = validate_r5_operator_capture_scope(live_g, _req(purpose="DEVELOPMENT_CAPTURE"), NOW_UTC, {}, attestation_verifier=object())  # type: ignore[arg-type]
    assert r28["admitted"] is False and r28["live_network_permitted"] is False


def test_c1_a11_a20_to_a25_plan_binding_tamper_rehash_post_enqueue_and_expiry(tmp_path: Path) -> None:
    inv = build_r5_source_capability_inventory(REPO_ROOT)
    qa_d = validate_r5_operator_capture_scope(_qa_grant(destination_scope=str(tmp_path / "ok_dest")), _req(endpoints=["public_ticker", "contracts"], destination_scope=str(tmp_path / "ok_dest")), NOW_UTC, {"qa_fixture_mode": True})
    assert qa_d["admitted"] is True and qa_d["live_network_permitted"] is False

    # Planner rejects broader budget/endpoint/symbol (NO silent clamp!)
    b_bad_ep = plan_r5_capture_batches(qa_d, inv, _bud(endpoints=["history_candles"]), now_utc=NOW_UTC)
    assert b_bad_ep["status"] == "BLOCKED"
    b_bad_sym = plan_r5_capture_batches(qa_d, inv, _bud(symbol="BTCUSDT"), now_utc=NOW_UTC)
    assert b_bad_sym["status"] == "BLOCKED"
    b_bad_req = plan_r5_capture_batches(qa_d, inv, _bud(max_requests=50), now_utc=NOW_UTC)
    assert b_bad_req["status"] == "BLOCKED"
    # A22 forged admitted scope_decision dict without process-local receipt rejected by planner!
    forged_scope = {k: v for k, v in qa_d.items() if not k.startswith("_")}
    assert plan_r5_capture_batches(forged_scope, inv, _bud(), now_utc=NOW_UTC)["status"] == "BLOCKED"

    # Build legitimate 2-batch plan
    dest_ok = tmp_path / "ok_dest"
    dest_ok.mkdir()
    qa_p = plan_r5_capture_batches(qa_d, inv, _bud(endpoints=["public_ticker", "contracts"]), now_utc=NOW_UTC)
    assert qa_p["status"] == "PLANNED_QA_OFFLINE" and qa_p["pages_semantics"] == "SINGLE_REQUEST_PER_BATCH_NO_PAGINATION"
    mon_ok = make_r5_fixture_resource_monitor(disk_free_gib=28.0, available_ram_mib=2048.0, destination=dest_ok)

    # A11 / A20: Mutate URL keeping original digest -> 0 dispatches!
    p_mut20 = dict(qa_p, batches=[dict(qa_p["batches"][0], url="https://api.bitget.com/api/v2/mix/market/contracts")])
    spy20 = _SpyOfflineTransport()
    r20 = asyncio.run(capture_r5_public_batch(p_mut20, spy20, mon_ok, run_id="c1_run_01", trusted_scope=qa_d, destination_root=dest_ok, utc_now_fn=lambda: NOW_UTC))
    assert len(spy20.calls) == 0 and r20["aborted"] is True

    # A21: Mutate plan AND recompute public plan_digest_sha256 -> STILL REJECTED with 0 dispatches!
    p_mut21 = dict(qa_p, batches=[dict(qa_p["batches"][0], symbol="BTCUSDT")])
    pub21 = {k: v for k, v in p_mut21.items() if not k.startswith("_") and k != "plan_digest_sha256"}
    p_mut21["plan_digest_sha256"] = canonical_hash(pub21)
    spy21 = _SpyOfflineTransport()
    r21 = asyncio.run(capture_r5_public_batch(p_mut21, spy21, mon_ok, run_id="c1_run_01", trusted_scope=qa_d, destination_root=dest_ok, utc_now_fn=lambda: NOW_UTC))
    assert len(spy21.calls) == 0 and any("PLAN_DIGEST_DRIFT_VS_TRUSTED_PLAN_RECEIPT" in v or "PLAN_SNAPSHOT_MUTATED" in v for v in r21["violations"])

    # A25: Destination outside approved destination_scope & wrong run_id -> 0 dispatches!
    wrong_dest = tmp_path / "other_dir"
    wrong_dest.mkdir()
    mon_w = make_r5_fixture_resource_monitor(disk_free_gib=28.0, available_ram_mib=2048.0, destination=wrong_dest)
    spy25 = _SpyOfflineTransport()
    r25 = asyncio.run(capture_r5_public_batch(qa_p, spy25, mon_w, run_id="c1_run_01", trusted_scope=qa_d, destination_root=wrong_dest, utc_now_fn=lambda: NOW_UTC))
    assert len(spy25.calls) == 0 and any("DESTINATION_OUTSIDE_APPROVED_DESTINATION_SCOPE" in v for v in r25["violations"])
    r_rid = asyncio.run(capture_r5_public_batch(qa_p, spy25, mon_ok, run_id="WRONG_RUN", trusted_scope=qa_d, destination_root=dest_ok, utc_now_fn=lambda: NOW_UTC))
    assert len(spy25.calls) == 0 and any("RUN_ID" in v for v in r_rid["violations"])

    # A23: Mutate plan AFTER enqueue (during batch 1 transport call) -> batch 2 blocked with 0 extra dispatch!
    qa_p_mut_live = plan_r5_capture_batches(qa_d, inv, _bud(endpoints=["public_ticker", "contracts"], max_concurrency=1), now_utc=NOW_UTC)
    def _tamper_on_first_call(idx: int, _u: str, _p: Mapping[str, Any]) -> None:
        if idx == 0:
            qa_p_mut_live["batches"][1]["url"] = "https://api.bitget.com/api/v2/mix/order/place-order"
    spy23 = _SpyOfflineTransport(on_call=_tamper_on_first_call)
    r23 = asyncio.run(capture_r5_public_batch(qa_p_mut_live, spy23, mon_ok, run_id="c1_run_01", trusted_scope=qa_d, destination_root=dest_ok, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC))
    assert len(spy23.calls) == 1 and r23["aborted"] is True and r23["qa_fixture_records_count"] == 0

    # A24: Grant expires DURING retry wait -> second attempt NEVER dispatches!
    clock_state = {"utc": NOW_UTC, "mono": 100.0}
    mon_clk = make_r5_fixture_resource_monitor(disk_free_gib=28.0, available_ram_mib=2048.0, destination=dest_ok, monotonic_fn=lambda: clock_state["mono"])
    qa_p_exp = plan_r5_capture_batches(qa_d, inv, _bud(endpoints=["public_ticker"]), now_utc=NOW_UTC)
    spy24 = _SpyOfflineTransport([RawHttpResponseC2(429, b"", {"retry-after": "0.1"}, NOW_MS, NOW_MS + 5)])
    def _advance_past_expiry(sec: float) -> None:
        clock_state["mono"] += sec
        clock_state["utc"] = NOW_UTC + timedelta(seconds=120)  # past 60s expiry!
    r24 = asyncio.run(capture_r5_public_batch(
        qa_p_exp, spy24, mon_clk, run_id="c1_run_01", trusted_scope=qa_d, destination_root=dest_ok,
        sleep_fn=_advance_past_expiry, monotonic_fn=lambda: clock_state["mono"], utc_now_fn=lambda: clock_state["utc"],
    ))
    assert len(spy24.calls) == 1 and r24["aborted"] is True and "OPERATOR_GRANT_EXPIRED" in str(r24["abort_reason"])


def test_c1_positive_strict_qa_and_pure_policy_fixture_no_socket(tmp_path: Path) -> None:
    inv = build_r5_source_capability_inventory(REPO_ROOT)
    # 1. Valid strict QA offline capture succeeds cleanly with deterministic fixture monitor
    qa_d = validate_r5_operator_capture_scope(_qa_grant(), _req(), NOW_UTC, {"qa_fixture_mode": True})
    qa_p = plan_r5_capture_batches(qa_d, inv, _bud(), now_utc=NOW_UTC)
    mon = make_r5_fixture_resource_monitor(disk_free_gib=28.0, available_ram_mib=2048.0, destination=tmp_path)
    spy = _SpyOfflineTransport()
    res = asyncio.run(capture_r5_public_batch(qa_p, spy, mon, staging_root=tmp_path / "st", run_id="c1_run_01", trusted_scope=qa_d, destination_root=tmp_path, utc_now_fn=lambda: NOW_UTC))
    assert res["capture_execution_status"] == "QA_FIXTURE_OFFLINE_PASS"
    assert res["external_requests_attempted"] == 0 and res["accepted_observed_records_count"] == 0
    assert res["qa_fixture_records_count"] == 1 and len(spy.calls) == 1

    # 2. QA plan MUST NOT use live StdlibUrllibPublicTransportC2!
    r_live_sock = asyncio.run(capture_r5_public_batch(qa_p, StdlibUrllibPublicTransportC2(), mon, run_id="c1_run_01", trusted_scope=qa_d, destination_root=tmp_path, utc_now_fn=lambda: NOW_UTC))
    assert r_live_sock["external_requests_attempted"] == 0 and any("QA_FIXTURE_MODE_MUST_NOT_USE_LIVE_SOCKET_TRANSPORT" in v for v in r_live_sock["violations"])

    # 3. Pure policy-only custom verifier fixture tests interface contract (still ZERO live socket calls!)
    class _PolicyOnlyTestVerifier:
        verifier_id = "UNIT_TEST_PURE_POLICY_STUB_NO_NETWORK"
        def verify_owner_attestation(self, grant: R5OperatorGrant, effective_scope: Mapping[str, Any], now_utc: datetime, tc: Mapping[str, Any]) -> R5AttestationVerificationResult:
            return R5AttestationVerificationResult(True, "VERIFIED_SEPARATE_OWNER_ATTESTATION", "TEST_ONLY", self.verifier_id, grant.grant_id, NOW_MS)

    live_g = _qa_grant(purpose="DEVELOPMENT_CAPTURE", is_qa_fixture_grant=False, origin_kind="SEPARATE_OWNER_ATTESTATION")
    dec_pol = validate_r5_operator_capture_scope(live_g, _req(purpose="DEVELOPMENT_CAPTURE"), NOW_UTC, {}, attestation_verifier=_PolicyOnlyTestVerifier())
    assert dec_pol["admitted"] is True and dec_pol["live_network_permitted"] is True
    # Even with pure-policy live plan, fake transport is rejected and legacy C4 transport with 4MiB cap > 50KB budget fails closed!
    plan_pol = plan_r5_capture_batches(dec_pol, inv, _bud(), now_utc=NOW_UTC)
    assert plan_pol["status"] == "PLANNED_DEV_BOUNDED"
    r_fake = asyncio.run(capture_r5_public_batch(plan_pol, _SpyOfflineTransport(), mon, run_id="c1_run_01", trusted_scope=dec_pol, destination_root=tmp_path, utc_now_fn=lambda: NOW_UTC))
    assert r_fake["accepted_observed_records_count"] == 0 and any("FAKE_OR_SUBCLASSED_TRANSPORT" in v for v in r_fake["violations"])
    r_cap = asyncio.run(capture_r5_public_batch(plan_pol, StdlibUrllibPublicTransportC2(), mon, run_id="c1_run_01", trusted_scope=dec_pol, destination_root=tmp_path, utc_now_fn=lambda: NOW_UTC))
    assert r_cap["capture_execution_status"] == "BLOCKED_TRANSPORT_READ_CAP_UNSUPPORTED" and r_cap["external_requests_attempted"] == 0
