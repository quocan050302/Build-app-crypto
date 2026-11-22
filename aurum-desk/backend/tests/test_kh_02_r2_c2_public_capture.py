"""
aurum-desk/backend/tests/test_kh_02_r2_c2_public_capture.py

AURUM DESK — KH-02.R2.C2 Unit & Integration Tests:
  T08: Read-only public Bitget collector fake-transport QA suite (20/20 scenarios),
       default network OFF, allowlist enforcement, contracts / merge_depth / orderbook_v3
       parsing, rate-limit backoff, schema drift, and C1 collector backward-compat delegation.
  T09: End-to-end fake-transport snapshot -> C2DurableEscrowJournal in isolated tmp_path,
       embargo enforcement, quote/fee disclosure, secrets redaction, OOS_EVALUATIONS == 0.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_c2_escrow_journal import C2DurableEscrowJournal
from services.kh_02_r2_c2_public_capture import (
    C2PublicReadOnlyBitgetCollector,
    OFFICIAL_BITGET_PUBLIC_ALLOWLIST_C2,
    OperatorCaptureAuthorizationC2,
    RawHttpResponseC2,
    run_kh02_r2_c2_fake_transport_collector_suite,
)
from services.kh_02_r2_prospective_data_service import (
    R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS,
    ReadOnlyBitgetPublicProspectiveCollectorR2C1,
)


class _SingleFakeTransport:
    def __init__(self, responses: Sequence[RawHttpResponseC2]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
        self.calls.append({"url": url, "params": dict(params), "timeout_sec": timeout_sec})
        return self._responses.pop(0)


def _ok_disk_probe(free_gib: float = 35.0) -> dict[str, Any]:
    return {
        "can_run_heavy_stages": free_gib >= 20.5,
        "min_free_gib": free_gib,
        "status": "PASS_STRICT_AND_ADVISORY" if free_gib >= 25.0 else "BLOCKED_LOW_DISK",
    }


def _auth() -> OperatorCaptureAuthorizationC2:
    return OperatorCaptureAuthorizationC2(
        approval_id="QA_OFFLINE_FAKE_TRANSPORT_APPROVAL_01",
        approved_by="qa_offline_test",
        network_capture_enabled=True,
        persistent_capture_active=False,
    )


def test_t08_full_20_scenario_fake_transport_collector_suite() -> None:
    res = run_kh02_r2_c2_fake_transport_collector_suite()
    assert res["status"] == "PASS", [c for c in res["cases"] if c["status"] != "PASS"]
    assert res["total_cases"] == 20
    assert res["passed_cases"] == 20
    assert res["failed_cases"] == 0
    assert res["live_network_capture_executed"] is False
    assert res["real_prospective_snapshots_collected"] == 0


def test_t08_default_collector_is_read_only_and_network_disabled() -> None:
    col = C2PublicReadOnlyBitgetCollector()
    desc = col.describe_readiness()
    assert desc["network_capture_enabled"] is False
    assert desc["persistent_capture_active"] is False
    assert desc["read_only"] is True
    assert desc["order_or_private_endpoints_present"] is False
    assert desc["api_key_or_secret_reads_present"] is False
    assert desc["historical_l1_bbo_available_from_rest"] is False
    assert desc["actual_account_effective_fee_tier"] == "UNKNOWN_NOT_LOCALLY_VERIFIED"
    assert set(OFFICIAL_BITGET_PUBLIC_ALLOWLIST_C2.keys()) == {
        "contracts",
        "public_ticker",
        "merge_depth",
        "orderbook_v3",
        "candles",
        "history_candles",
    }
    with pytest.raises(PermissionError, match="PROSPECTIVE_NETWORK_CAPTURE_DISABLED_BY_DEFAULT"):
        col.fetch_public_l1_bbo_snapshot(snapshot_id="default_off", write_mount_probe=_ok_disk_probe())


def test_t08_merge_depth_and_orderbook_v3_and_candles_parsing() -> None:
    t0 = R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 3_600_000
    resp_depth = RawHttpResponseC2(
        status_code=200,
        body_bytes=json.dumps({
            "code": "00000",
            "msg": "success",
            "data": {
                "bids": [["4010.20", "1.500"]],
                "asks": [["4010.45", "2.100"]],
                "ts": str(t0 + 10),
            },
        }).encode("utf-8"),
        headers={"content-type": "application/json"},
        request_utc_ms=t0 + 100,
        response_utc_ms=t0 + 130,
    )
    resp_ob_v3 = RawHttpResponseC2(
        status_code=200,
        body_bytes=json.dumps({
            "code": "00000",
            "msg": "success",
            "data": {
                "b": [["4010.30", "1.250"]],
                "a": [["4010.50", "1.750"]],
                "ts": str(t0 + 200),
            },
        }).encode("utf-8"),
        headers={"content-type": "application/json"},
        request_utc_ms=t0 + 210,
        response_utc_ms=t0 + 235,
    )
    resp_candles = RawHttpResponseC2(
        status_code=200,
        body_bytes=json.dumps({
            "code": "00000",
            "msg": "success",
            "data": [
                [str(t0), "4010.00", "4011.00", "4009.50", "4010.60", "12.5", "50125.0"],
                [str(t0 + 60_000), "4010.60", "4012.00", "4010.20", "4011.40", "10.0", "40114.0"],
            ],
        }).encode("utf-8"),
        headers={"content-type": "application/json"},
        request_utc_ms=t0 + 180_000,
        response_utc_ms=t0 + 180_050,
    )
    tr = _SingleFakeTransport([resp_depth, resp_ob_v3, resp_candles])
    col = C2PublicReadOnlyBitgetCollector(authorization=_auth(), transport=tr, is_fake_transport_qa=True)

    d_res = col.fetch_public_l1_bbo_snapshot(snapshot_id="qa_depth_01", write_mount_probe=_ok_disk_probe(), endpoint_key="merge_depth")
    assert d_res["validation"]["valid_for_escrow_admission"] is True
    assert d_res["parsed_quote"]["bid"] == pytest.approx(4010.20)
    assert d_res["parsed_quote"]["ask"] == pytest.approx(4010.45)

    v3_res = col.fetch_public_l1_bbo_snapshot(snapshot_id="qa_obv3_01", write_mount_probe=_ok_disk_probe(), endpoint_key="orderbook_v3")
    assert v3_res["validation"]["valid_for_escrow_admission"] is True
    assert v3_res["parsed_quote"]["spread_usd"] == pytest.approx(0.20)
    assert tr.calls[1]["params"]["category"] == "USDT-FUTURES"

    k_res = col.fetch_public_ohlcv_candles_snapshot(
        snapshot_id="qa_candles_01",
        write_mount_probe=_ok_disk_probe(),
        start_time_ms=t0,
        end_time_ms=t0 + 120_000,
    )
    assert k_res["validation"]["valid_for_escrow_admission"] is True
    assert len(k_res["parsed_bars"]) == 2


def test_t09_end_to_end_fake_transport_to_c2_escrow_journal_and_c1_compat(tmp_path: Path) -> None:
    t0 = R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 7_200_000
    ticker_resp = RawHttpResponseC2(
        status_code=200,
        body_bytes=json.dumps({
            "code": "00000",
            "msg": "success",
            "data": [{
                "symbol": "XAUUSDT",
                "bidPr": "4015.10",
                "bidSz": "2.000",
                "askPr": "4015.30",
                "askSz": "2.500",
                "ts": str(t0 + 10),
            }],
        }).encode("utf-8"),
        headers={"content-type": "application/json"},
        request_utc_ms=t0,
        response_utc_ms=t0 + 25,
    )
    # 1. C2 collector -> C2DurableEscrowJournal (isolated synthetic tmp_path; OOS_EVALUATIONS=0)
    col = C2PublicReadOnlyBitgetCollector(
        authorization=_auth(),
        transport=_SingleFakeTransport([ticker_resp]),
        is_fake_transport_qa=True,
    )
    snap_res = col.fetch_public_l1_bbo_snapshot(snapshot_id="e2e_fake_bbo_01", write_mount_probe=_ok_disk_probe())
    assert snap_res["is_fake_transport_qa"] is True
    assert "FAKE_TRANSPORT_QA_ONLY" in snap_res["provenance_disclosure"]

    escrow_entry = dict(snap_res["snapshot_contract_entry"])
    j = C2DurableEscrowJournal(tmp_path / "e2e_escrow")
    app = j.append_snapshot(escrow_entry)
    assert app["action"] == "APPENDED_NEW_ENTRY"
    chk = j.verify_integrity()
    assert chk["valid"] is True
    assert chk["record_count"] == 1
    assert chk["immutability_class"] == "LOCAL_TAMPER_EVIDENT_ONLY"

    # 2. Backward-compatible C1 collector wrapper delegates cleanly when fake transport injected
    c1_wrap = ReadOnlyBitgetPublicProspectiveCollectorR2C1(
        network_capture_enabled=True,
        explicit_operator_approval_id="UNIT_TEST_C1_WRAPPER_FAKE_TRANSPORT",
    )
    w_res = c1_wrap.fetch_public_snapshot(
        endpoint_key="public_ticker",
        write_mount_probe=_ok_disk_probe(),
        transport=_SingleFakeTransport([ticker_resp]),
        snapshot_id="c1_wrap_bbo_01",
    )
    assert w_res["validation"]["valid_for_escrow_admission"] is True
    assert w_res["snapshot_contract_entry"]["strategy_evaluation_count"] == 0

    # 3. C1 wrapper without transport still refuses live network cleanly
    c1_no_tr = ReadOnlyBitgetPublicProspectiveCollectorR2C1(
        network_capture_enabled=True,
        explicit_operator_approval_id="NO_TRANSPORT_TEST",
    )
    with pytest.raises(PermissionError, match="NO_HTTP_TRANSPORT_CONFIGURED_LIVE_NETWORK_DISABLED"):
        c1_no_tr.fetch_public_snapshot(endpoint_key="public_ticker", write_mount_probe=_ok_disk_probe())
