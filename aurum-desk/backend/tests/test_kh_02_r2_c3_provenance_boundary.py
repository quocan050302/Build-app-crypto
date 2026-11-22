"""
aurum-desk/backend/tests/test_kh_02_r2_c3_provenance_boundary.py

AURUM DESK — KH-02.R2.C3 P0-A Adversarial Provenance Firewall Tests (Attacks 01-07, 17):
  - Fake transport masquerading with is_fake_transport_qa=False & live_session=True -> BLOCKED from real OOS.
  - Synthetic fixture with is_synthetic_fixture stripped/omitted -> BLOCKED from real OOS.
  - raw_payload_bytes=None for OBSERVED_QUOTE & OHLCV_SNAPSHOT -> BLOCKED (zero metadata-only fallback).
  - Valid raw_response_sha256 with forged canonical_parsed_sha256 -> BLOCKED by raw-byte reparse.
  - Valid raw body with overwritten event_or_close_utc_ms -> BLOCKED by raw-byte reparse.
  - Forged TrustedCaptureReceiptC3 MAC -> BLOCKED.
  - DEV canonical SHA / pre-embargo window replay -> BLOCKED.
  - Counterfeit approval_id without capture_session_id for live capture -> BLOCKED.
  - Isolated QA mode tags fake transport strictly as TEST_ONLY_NOT_ELIGIBLE_OOS (eligible_for_fresh_oos=False).
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

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
    _SequenceFakeTransportC2,
)
from services.kh_02_r2_c3_provenance_guard import (
    ProvenanceClassC3,
    TrustedCaptureReceiptC3,
    evaluate_c3_prospective_admission,
    reparse_and_verify_raw_market_bytes_c3,
    verify_trusted_capture_receipt_mac_c3,
)
from services.kh_02_r2_prospective_data_service import (
    CANONICAL_DATASET_SHA256_KH02,
    R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS,
    make_synthetic_valid_c1_snapshot_fixture,
    validate_prospective_snapshot_contract_c3,
)


def _capture_bbo(*, snapshot_id: str, is_fake: bool = True, live_session: bool = False) -> dict:
    t0 = R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 120_000
    body = json.dumps(
        {
            "code": "00000",
            "msg": "success",
            "data": [{"symbol": "XAUUSDT", "bidPr": "4012.10", "askPr": "4012.30", "ts": str(t0)}],
        }
    ).encode("utf-8")
    resp = RawHttpResponseC2(
        status_code=200,
        body_bytes=body,
        headers={"content-type": "application/json"},
        request_utc_ms=t0 + 10,
        response_utc_ms=t0 + 25,
        transport_kind="STDLIB_URLLIB_HTTPS_GET" if not is_fake else "INJECTED_FAKE_TRANSPORT",
        tls_verified_host="api.bitget.com" if not is_fake else "",
    )
    auth = OperatorCaptureAuthorizationC2(
        approval_id="QA_BOUNDARY_APPROVAL",
        approved_by="qa_harness",
        network_capture_enabled=True,
        capture_session_id="qa_sess_01",
        live_network_session_authorized=live_session,
    )
    col = C2PublicReadOnlyBitgetCollector(
        authorization=auth,
        transport=_SequenceFakeTransportC2([resp]),
        is_fake_transport_qa=is_fake,
        os_disk_probe_fn=lambda: 35.0,
    )
    return col.fetch_public_l1_bbo_snapshot(
        snapshot_id=snapshot_id,
        write_mount_probe={"can_run_heavy_stages": True, "min_free_gib": 35.0, "status": "PASS_STRICT_AND_ADVISORY"},
    )


def _capture_ohlcv(snapshot_id: str) -> dict:
    t0 = R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 240_000
    body = json.dumps(
        {
            "code": "00000",
            "msg": "success",
            "data": [
                [str(t0), "4000.00", "4001.00", "3999.50", "4000.50", "10.0", "40005.0"],
                [str(t0 + 60_000), "4000.50", "4002.00", "4000.00", "4001.50", "12.0", "48018.0"],
            ],
        }
    ).encode("utf-8")
    resp = RawHttpResponseC2(200, body, {"content-type": "application/json"}, t0 + 180_000, t0 + 180_030)
    auth = OperatorCaptureAuthorizationC2(approval_id="QA_OHLCV", approved_by="qa", network_capture_enabled=True)
    col = C2PublicReadOnlyBitgetCollector(authorization=auth, transport=_SequenceFakeTransportC2([resp]), is_fake_transport_qa=True)
    return col.fetch_public_ohlcv_candles_snapshot(
        snapshot_id=snapshot_id,
        write_mount_probe={"can_run_heavy_stages": True, "min_free_gib": 35.0},
        start_time_ms=t0,
        end_time_ms=t0 + 120_000,
    )


def test_c3_attack_01_fake_transport_spoofing_live_flags_blocked_from_real_oos(tmp_path: Path) -> None:
    cap = _capture_bbo(snapshot_id="atk01_spoof", is_fake=False, live_session=True)
    # Even though caller passed is_fake=False, live_session=True, transport_kind=STDLIB_URLLIB_HTTPS_GET,
    # the trusted capture boundary inspects the actual transport object and refuses LIVE classification!
    assert cap["provenance_class"] != ProvenanceClassC3.LIVE_AUTHORIZED_PUBLIC_HTTP.value
    assert cap["capture_receipt"].provenance_class != ProvenanceClassC3.LIVE_AUTHORIZED_PUBLIC_HTTP

    real_escrow = C2DurableEscrowJournal.open_real_oos_escrow(tmp_path / "real_oos", os_disk_probe_fn=lambda: 35.0)
    with pytest.raises(PermissionError, match="UNTRUSTED_OR_SYNTHETIC_PROVENANCE_FORBIDDEN"):
        real_escrow.append_verified_prospective_snapshot(
            cap["snapshot_contract_entry"],
            raw_payload_bytes=cap["raw_response_bytes"],
            capture_receipt=cap["capture_receipt"],
        )
    assert real_escrow.verify_integrity()["record_count"] == 0


def test_c3_attack_02_03_04_synthetic_omitted_and_metadata_only_blocked_from_real_oos(tmp_path: Path) -> None:
    real_escrow = C2DurableEscrowJournal.open_real_oos_escrow(tmp_path / "real_oos_meta", os_disk_probe_fn=lambda: 35.0)
    bbo = _capture_bbo(snapshot_id="atk03_bbo")
    ohlcv = _capture_ohlcv("atk04_ohlcv")

    # Attack 02: synthetic fixture with is_synthetic_fixture omitted
    synth = make_synthetic_valid_c1_snapshot_fixture(snapshot_id="atk02_synth_stripped")
    synth.pop("is_synthetic_fixture", None)
    with pytest.raises((PermissionError, ValueError)):
        real_escrow.append_verified_prospective_snapshot(
            synth, raw_payload_bytes=bbo["raw_response_bytes"], capture_receipt=None
        )

    # Attack 03: OBSERVED_QUOTE with raw_payload_bytes=None
    with pytest.raises(ValueError, match="RAW_PAYLOAD_BYTES_REQUIRED_FOR_REAL_OOS"):
        real_escrow.append_verified_prospective_snapshot(
            bbo["snapshot_contract_entry"], raw_payload_bytes=None, capture_receipt=bbo["capture_receipt"]
        )

    # Attack 04: OHLCV_SNAPSHOT with raw_payload_bytes=None
    with pytest.raises(ValueError, match="RAW_PAYLOAD_BYTES_REQUIRED_FOR_REAL_OOS"):
        real_escrow.append_verified_prospective_snapshot(
            ohlcv["snapshot_contract_entry"], raw_payload_bytes=None, capture_receipt=ohlcv["capture_receipt"]
        )
    assert real_escrow.verify_integrity()["record_count"] == 0


def test_c3_attack_05_06_07_raw_byte_reparse_catches_parsed_forgery_ts_overwrite_and_dev_replay() -> None:
    bbo = _capture_bbo(snapshot_id="atk05_bbo")
    ohlcv = _capture_ohlcv("atk05_ohlcv")

    # GREEN reference on both BBO and OHLCV
    assert reparse_and_verify_raw_market_bytes_c3(bbo["raw_response_bytes"], bbo["snapshot_contract_entry"], capture_receipt=bbo["capture_receipt"])["valid"] is True
    assert reparse_and_verify_raw_market_bytes_c3(ohlcv["raw_response_bytes"], ohlcv["snapshot_contract_entry"], capture_receipt=ohlcv["capture_receipt"])["valid"] is True

    # Attack 05: raw_response_sha256 intact, canonical_parsed_sha256 forged
    bad_parsed = dict(bbo["snapshot_contract_entry"], canonical_parsed_sha256="9" * 64)
    rp_bad = reparse_and_verify_raw_market_bytes_c3(bbo["raw_response_bytes"], bad_parsed)
    assert rp_bad["valid"] is False
    assert any("CANONICAL_PARSED_SHA256_TAMPERED" in v for v in rp_bad["violations"])

    # Attack 06: raw bytes intact, event_or_close_utc_ms overwritten
    bad_ts = dict(bbo["snapshot_contract_entry"], event_or_close_utc_ms=int(bbo["snapshot_contract_entry"]["event_or_close_utc_ms"]) + 1000)
    rp_ts = reparse_and_verify_raw_market_bytes_c3(bbo["raw_response_bytes"], bad_ts)
    assert rp_ts["valid"] is False
    assert any("EVENT_TIMESTAMP_TAMPERED" in v for v in rp_ts["violations"])

    # Attack 07: DEV canonical SHA replay blocked
    bad_dev = dict(bbo["snapshot_contract_entry"], dataset_sha256=CANONICAL_DATASET_SHA256_KH02)
    adm_dev = validate_prospective_snapshot_contract_c3(
        bad_dev,
        raw_payload_bytes=bbo["raw_response_bytes"],
        capture_receipt=bbo["capture_receipt"],
        require_live_real_oos=False,
    )
    assert adm_dev["admitted"] is False
    assert adm_dev["eligible_for_fresh_oos"] is False


def test_c3_attack_17_forged_receipt_mac_counterfeit_approval_and_isolated_qa_tag(tmp_path: Path) -> None:
    bbo = _capture_bbo(snapshot_id="atk17_qa")
    orig = bbo["capture_receipt"]
    assert verify_trusted_capture_receipt_mac_c3(orig)[0] is True

    # Tampered receipt MAC claiming LIVE_AUTHORIZED_PUBLIC_HTTP must fail
    forged = TrustedCaptureReceiptC3(
        receipt_id=orig.receipt_id,
        capture_session_id=orig.capture_session_id,
        provenance_class=ProvenanceClassC3.LIVE_AUTHORIZED_PUBLIC_HTTP,
        endpoint_key=orig.endpoint_key,
        url=orig.url,
        tls_host="api.bitget.com",
        http_status=200,
        params_canonical_sha256=orig.params_canonical_sha256,
        raw_response_sha256=orig.raw_response_sha256,
        raw_response_size_bytes=orig.raw_response_size_bytes,
        canonical_parsed_sha256=orig.canonical_parsed_sha256,
        request_utc_ms=orig.request_utc_ms,
        response_utc_ms=orig.response_utc_ms,
        monotonic_duration_ms=orig.monotonic_duration_ms,
        provider_event_utc_ms=orig.provider_event_utc_ms,
        transport_class_name="StdlibUrllibPublicTransportC2",
        transport_kind="STDLIB_URLLIB_HTTPS_GET",
        real_rate_limiter_active=True,
        receipt_mac_hex="0" * 64,
    )
    assert verify_trusted_capture_receipt_mac_c3(forged)[0] is False
    adm_f = evaluate_c3_prospective_admission(
        bbo["snapshot_contract_entry"],
        raw_payload_bytes=bbo["raw_response_bytes"],
        capture_receipt=forged,
        require_live_real_oos=True,
    )
    assert adm_f["admitted"] is False

    # Counterfeit live authorization missing capture_session_id rejected
    with pytest.raises(PermissionError, match="LIVE_NETWORK_AUTHORIZATION_REQUIRES"):
        OperatorCaptureAuthorizationC2(
            approval_id="FAKE_APP",
            approved_by="attacker",
            network_capture_enabled=True,
            live_network_session_authorized=True,
            capture_session_id="",
        )

    # Isolated QA escrow admits fake transport ONLY as TEST_ONLY_NOT_ELIGIBLE_OOS
    qa_j = C2DurableEscrowJournal(tmp_path / "qa_isolated", isolated_qa_mode=True, os_disk_probe_fn=lambda: 35.0)
    res = qa_j.append_verified_prospective_snapshot(
        bbo["snapshot_contract_entry"],
        raw_payload_bytes=bbo["raw_response_bytes"],
        capture_receipt=orig,
    )
    assert res["action"] == "APPENDED_NEW_ENTRY"
    assert res["admission_tier"] == "TEST_ONLY_NOT_ELIGIBLE_OOS"
    assert res["eligible_for_fresh_oos"] is False
