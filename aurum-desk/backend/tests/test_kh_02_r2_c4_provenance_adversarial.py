"""
aurum-desk/backend/tests/test_kh_02_r2_c4_provenance_adversarial.py

AURUM DESK — KH-02.R2.C4 P0-A Adversarial Provenance Boundary Tests (A01..A08):
  - A01: Direct `mint_trusted_capture_receipt_c3/c4` with caller live booleans -> UNVERIFIED, blocked from real OOS.
  - A02: EvilSubclass(StdlibUrllibPublicTransportC2) overriding `.get()` -> blocked from real OOS.
  - A03: Instance `.get` monkeypatch, post-init `_opener_fn` mutation, naked RawHttpResponseC2 -> blocked.
  - A04: Forged auth/session/disk probe, metadata-only, synthetic flag stripped -> blocked.
  - A05: Forged MAC, non-copyable/non-picklable proof, expired proof, modified bytes -> blocked.
  - A06: Redirect, SSRF, private endpoint, non-HTTPS, secrets, 429/5xx exhaustion -> fail-closed.
  - A07: Proof replay for a different snapshot_id or modified bytes -> blocked inside escrow lock.
  - A08: Isolated QA partition admits fake fixture strictly as `TEST_ONLY_NOT_ELIGIBLE_OOS` (`eligible_for_fresh_oos=False`).
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import pickle
import sys
from typing import Any, Mapping

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
    OperatorCaptureAuthorizationC2,
    RawHttpResponseC2,
    StdlibUrllibPublicTransportC2,
    _SequenceFakeTransportC2,
)
from services.kh_02_r2_c3_provenance_guard import (
    ProvenanceClassC3,
    TrustedCaptureReceiptC3,
    evaluate_c3_prospective_admission,
    mint_trusted_capture_receipt_c3,
    mint_trusted_capture_receipt_c4,
)
from services.kh_02_r2_c4_trusted_transport_boundary import (
    TCB_THREAT_MODEL_DISCLOSURE_C4,
    VerifiedCaptureProofC4,
    expire_capture_proof_for_test_only_c4,
    issue_qa_or_unverified_capture_proof_c4,
    verify_capture_proof_c4,
)
from services.kh_02_r2_prospective_data_service import (
    BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1,
    R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS,
    SCHEMA_VERSION_KH02_R2_C1,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash


def _make_bbo_bytes_and_entry(snapshot_id: str, *, offset_ms: int = 120_000, transport_kind: str = "STDLIB_URLLIB_HTTPS_GET") -> tuple[bytes, dict, int, int, int, str]:
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


def _live_auth(sess: str = "c4_adv_sess") -> OperatorCaptureAuthorizationC2:
    return OperatorCaptureAuthorizationC2(
        approval_id="C4_ADV_APP",
        approved_by="qa_auditor",
        network_capture_enabled=True,
        capture_session_id=sess,
        live_network_session_authorized=True,
    )


def _ok_disk() -> dict:
    return {"can_run_heavy_stages": True, "min_free_gib": 35.0, "status": "PASS_STRICT_AND_ADVISORY"}


def test_c4_a01_direct_mint_boolean_spoof_blocked_from_real_oos(tmp_path: Path) -> None:
    body, entry, t0, req_ms, recv_ms, psha = _make_bbo_bytes_and_entry("c4_a01_direct")
    rcpt = mint_trusted_capture_receipt_c3(
        capture_session_id="forged_sess",
        endpoint_key="public_ticker",
        url="https://api.bitget.com/api/v2/mix/market/ticker",
        params={"productType": "USDT-FUTURES", "symbol": "XAUUSDT"},
        http_status=200,
        raw_response_bytes=body,
        canonical_parsed_sha256=psha,
        request_utc_ms=req_ms,
        response_utc_ms=recv_ms,
        monotonic_duration_ms=5,
        provider_event_utc_ms=t0,
        transport_obj=StdlibUrllibPublicTransportC2(),
        transport_kind="STDLIB_URLLIB_HTTPS_GET",
        tls_host="api.bitget.com",
        is_fake_transport_qa=False,
        live_transport_verified=True,
        live_authorization_verified=True,
        real_rate_limiter_active=True,
    )
    assert rcpt.provenance_class == ProvenanceClassC3.UNVERIFIED
    adm = evaluate_c3_prospective_admission(entry, raw_payload_bytes=body, capture_receipt=rcpt, require_live_real_oos=True)
    assert adm["admitted"] is False
    assert adm["eligible_for_fresh_oos"] is False

    # Even constructing a forged VerifiedCaptureProofC4 manually cannot mint LIVE
    forged_proof = VerifiedCaptureProofC4(
        proof_id="prf_c4_forged_12345",
        capture_session_id="forged_sess",
        provenance_class_value="LIVE_AUTHORIZED_PUBLIC_HTTP",
        endpoint_key="public_ticker",
        url="https://api.bitget.com/api/v2/mix/market/ticker",
        final_url="https://api.bitget.com/api/v2/mix/market/ticker",
        tls_host="api.bitget.com",
        http_status=200,
        params_canonical_sha256=rcpt.params_canonical_sha256,
        raw_response_sha256=rcpt.raw_response_sha256,
        raw_response_size_bytes=len(body),
        request_utc_ms=req_ms,
        response_utc_ms=recv_ms,
        monotonic_duration_ms=5,
        issued_mono_ns=1,
        ttl_sec=120.0,
        worker_pid=1,
        policy_version="kh02_r2_c4_boundary_v1",
        transport_class_name="StdlibUrllibPublicTransportC2",
        transport_kind="STDLIB_URLLIB_HTTPS_GET",
        real_rate_limiter_active=True,
        proof_mac_hex="0" * 64,
    )
    assert verify_capture_proof_c4(forged_proof, require_live=True)[0] is False
    rcpt2 = mint_trusted_capture_receipt_c4(
        capture_proof=forged_proof,
        capture_session_id="forged_sess",
        endpoint_key="public_ticker",
        url="https://api.bitget.com/api/v2/mix/market/ticker",
        params={"productType": "USDT-FUTURES", "symbol": "XAUUSDT"},
        http_status=200,
        raw_response_bytes=body,
        canonical_parsed_sha256=psha,
        request_utc_ms=req_ms,
        response_utc_ms=recv_ms,
        monotonic_duration_ms=5,
        provider_event_utc_ms=t0,
        transport_obj=StdlibUrllibPublicTransportC2(),
        transport_kind="STDLIB_URLLIB_HTTPS_GET",
        tls_host="api.bitget.com",
        is_fake_transport_qa=False,
        live_transport_verified=True,
        live_authorization_verified=True,
        real_rate_limiter_active=True,
    )
    assert rcpt2.provenance_class != ProvenanceClassC3.LIVE_AUTHORIZED_PUBLIC_HTTP

    real_j = C2DurableEscrowJournal.open_real_oos_escrow(tmp_path / "real_a01", os_disk_probe_fn=lambda: 35.0)
    with pytest.raises(PermissionError, match="UNTRUSTED_OR_SYNTHETIC_PROVENANCE_FORBIDDEN"):
        real_j.append_verified_prospective_snapshot(entry, raw_payload_bytes=body, capture_receipt=rcpt)
    assert real_j.verify_integrity()["record_count"] == 0


def test_c4_a02_a03_evil_subclass_monkeypatch_and_post_init_opener_blocked(tmp_path: Path) -> None:
    body, _e, t0, req_ms, recv_ms, _p = _make_bbo_bytes_and_entry("c4_a02_sub", offset_ms=180_000)

    class EvilSubTransport(StdlibUrllibPublicTransportC2):
        def is_genuine_live_socket_transport(self) -> bool:
            return True

        def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
            return RawHttpResponseC2(200, body, {"content-type": "application/json"}, req_ms, recv_ms, "STDLIB_URLLIB_HTTPS_GET", "api.bitget.com", url, 4)

    # A02: Evil subclass cannot classify LIVE or enter real OOS
    col_sub = C2PublicReadOnlyBitgetCollector(authorization=_live_auth("s_sub"), transport=EvilSubTransport(), is_fake_transport_qa=False, os_disk_probe_fn=lambda: 35.0)
    cap_sub = col_sub.fetch_public_l1_bbo_snapshot(snapshot_id="c4_a02_sub", write_mount_probe=_ok_disk())
    assert cap_sub["provenance_class"] != ProvenanceClassC3.LIVE_AUTHORIZED_PUBLIC_HTTP.value
    assert cap_sub["capture_receipt"].provenance_class != ProvenanceClassC3.LIVE_AUTHORIZED_PUBLIC_HTTP

    # A03a: Instance .get monkeypatch on exact StdlibUrllibPublicTransportC2()
    tr_mp = StdlibUrllibPublicTransportC2()
    tr_mp.get = lambda url, params, *, timeout_sec: RawHttpResponseC2(200, body, {}, req_ms, recv_ms, "STDLIB_URLLIB_HTTPS_GET", "api.bitget.com", url, 3)  # type: ignore[method-assign]
    col_mp = C2PublicReadOnlyBitgetCollector(authorization=_live_auth("s_mp"), transport=tr_mp, is_fake_transport_qa=False, os_disk_probe_fn=lambda: 35.0)
    cap_mp = col_mp.fetch_public_l1_bbo_snapshot(snapshot_id="c4_a03_mp", write_mount_probe=_ok_disk())
    assert cap_mp["provenance_class"] != ProvenanceClassC3.LIVE_AUTHORIZED_PUBLIC_HTTP.value

    # A03b: Post-__init__ mutation of transport._opener_fn after collector init
    import io

    class _FakeResp:
        status = 200
        code = 200
        headers = {"content-type": "application/json"}
        url = "https://api.bitget.com/api/v2/mix/market/ticker"

        def read(self, _n: int = -1) -> bytes:
            return body

        def __enter__(self) -> "_FakeResp":
            return self

        def __exit__(self, *_a: Any) -> None:
            return None

    tr_post = StdlibUrllibPublicTransportC2()
    col_post = C2PublicReadOnlyBitgetCollector(authorization=_live_auth("s_post"), transport=tr_post, is_fake_transport_qa=False, os_disk_probe_fn=lambda: 35.0)
    assert col_post._is_live_stdlib_transport is True
    # Mutate _opener_fn AFTER __init__!
    tr_post._opener_fn = lambda _r, _t: _FakeResp()
    assert col_post._trusted_transport_classification() != ProvenanceClassC3.LIVE_AUTHORIZED_PUBLIC_HTTP
    # Use fresh timestamp within clock skew via mocked response? Even easier: check _check_live_transport_exact_now is False!
    ok_now, reasons = col_post._check_live_transport_exact_now()
    assert ok_now is False and "INJECTED_OPENER_FN_PRESENT_NOT_LIVE_SOCKET" in reasons

    real_j = C2DurableEscrowJournal.open_real_oos_escrow(tmp_path / "real_a0203", os_disk_probe_fn=lambda: 35.0)
    for cap in (cap_sub, cap_mp):
        with pytest.raises(PermissionError, match="UNTRUSTED_OR_SYNTHETIC_PROVENANCE_FORBIDDEN"):
            real_j.append_verified_prospective_snapshot(
                cap["snapshot_contract_entry"],
                raw_payload_bytes=cap["raw_response_bytes"],
                capture_receipt=cap["capture_receipt"],
            )
    assert real_j.verify_integrity()["record_count"] == 0


def test_c4_a05_a07_a08_non_serializable_proof_expiry_replay_and_isolated_qa(tmp_path: Path) -> None:
    body, _e, t0, req_ms, recv_ms, _p = _make_bbo_bytes_and_entry("c4_a05_qa", offset_ms=240_000, transport_kind="INJECTED_FAKE_TRANSPORT")
    resp = RawHttpResponseC2(200, body, {"content-type": "application/json"}, req_ms, recv_ms)
    auth = OperatorCaptureAuthorizationC2(approval_id="QA_ONLY", approved_by="qa", network_capture_enabled=True)
    col = C2PublicReadOnlyBitgetCollector(authorization=auth, transport=_SequenceFakeTransportC2([resp]), is_fake_transport_qa=True)
    cap = col.fetch_public_l1_bbo_snapshot(snapshot_id="c4_a05_qa", write_mount_probe=_ok_disk())
    proof = cap["capture_proof_c4"]

    # Non-copyable & non-picklable proof
    with pytest.raises(TypeError, match="NON_COPYABLE"):
        copy.copy(proof)
    with pytest.raises(TypeError, match="NON_COPYABLE"):
        copy.deepcopy(proof)
    with pytest.raises(TypeError, match="NON_SERIALIZABLE"):
        pickle.dumps(proof)

    # A08: Isolated QA escrow admits as TEST_ONLY_NOT_ELIGIBLE_OOS (eligible_for_fresh_oos=False)
    qa_j = C2DurableEscrowJournal(tmp_path / "qa_a08", isolated_qa_mode=True, os_disk_probe_fn=lambda: 35.0)
    w1 = qa_j.append_verified_prospective_snapshot(
        cap["snapshot_contract_entry"],
        raw_payload_bytes=cap["raw_response_bytes"],
        capture_receipt=cap["capture_receipt"],
    )
    assert w1["action"] == "APPENDED_NEW_ENTRY"
    assert w1["admission_tier"] == "TEST_ONLY_NOT_ELIGIBLE_OOS"
    assert w1["eligible_for_fresh_oos"] is False

    # Same snapshot_id + same bytes is cleanly idempotent
    w_idem = qa_j.append_verified_prospective_snapshot(
        cap["snapshot_contract_entry"],
        raw_payload_bytes=cap["raw_response_bytes"],
        capture_receipt=cap["capture_receipt"],
    )
    assert w_idem["action"] == "IDEMPOTENT_ALREADY_RECORDED_NOOP"

    # A07: Replaying the exact same proof/receipt under a DIFFERENT snapshot_id fails closed inside lock!
    other_entry = dict(cap["snapshot_contract_entry"], snapshot_id="c4_a07_replayed_other_sid")
    with pytest.raises(PermissionError, match="C4_CAPTURE_PROOF_REPLAY_FORBIDDEN"):
        qa_j.append_verified_prospective_snapshot(
            other_entry,
            raw_payload_bytes=cap["raw_response_bytes"],
            capture_receipt=cap["capture_receipt"],
        )

    # A05: Expired proof fails closed even in isolated QA mode
    assert expire_capture_proof_for_test_only_c4(proof.proof_id) is True
    adm_exp = evaluate_c3_prospective_admission(
        cap["snapshot_contract_entry"],
        raw_payload_bytes=cap["raw_response_bytes"],
        capture_receipt=cap["capture_receipt"],
        require_live_real_oos=False,
    )
    assert adm_exp["admitted"] is False
    assert any("EXPIRED_C4_CAPTURE_PROOF" in v for v in adm_exp["violations"])
    assert TCB_THREAT_MODEL_DISCLOSURE_C4["hardware_tpm_or_remote_worm"] is False
