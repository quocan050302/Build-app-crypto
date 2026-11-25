"""
aurum-desk/backend/tests/test_kh03_r6_source_contracts.py

KH-03.R6 Paired Controls A01..A08 (Source Contracts, Bitget Classic v2 Identity & Owner Trust Boundary).
Zero external market sockets; isolated QA Ed25519 fixtures strictly under QA_ISOLATED_TEST_TRUST.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Tuple

from services.kh_03_r6_acceptance_service import (
    R6FaultCaseSpec,
    build_r6_unsigned_owner_review_template,
    derive_r6_fault_verdict,
    execute_r6_fault_pair,
)
from services.kh_03_r6_bounded_transport_service import build_r6_bound_capture_plan
from services.kh_03_r6_durable_grant_service import (
    OWNER_EXTERNAL_TRUST_NAMESPACE_R6,
    QA_ISOLATED_TRUST_NAMESPACE_R6,
    load_r6_owner_trust_root,
    provision_qa_isolated_trust_root,
    sign_r6_grant_payload_with_ed25519,
    verify_r6_owner_grant,
)
from services.kh_03_r6_source_contract_service import (
    API_FAMILY_CLASSIC_V2,
    APPROVED_PRODUCT_TYPE_V2_R6,
    APPROVED_SYMBOL_R6,
    APPROVED_VENUE_R6,
    MANDATORY_BRANCH_KH03_R6,
    build_r6_source_capability_matrix,
    build_r6_source_contract_registry,
    validate_r6_endpoint_contract,
    validate_r6_instrument_identity,
)


def make_qa_grant_payload(
    *,
    grant_id: str,
    dest_root: Path,
    store_id: str,
    now_utc: datetime,
    executable_sha: str = "a" * 40,
    release_sha: str = "b" * 64,
    stage: str = "QA_LOOPBACK",
    purpose: str = "QA_FIXTURE",
    trust_namespace: str = QA_ISOLATED_TRUST_NAMESPACE_R6,
    issuer: str = "QA_ISOLATED_HARNESS",
    expires_delta_sec: int = 600,
    max_requests: int = 10,
    max_payload_bytes: int = 262_144,
) -> Dict[str, Any]:
    now_ms = int(now_utc.timestamp() * 1000)
    return {
        "grant_id": grant_id,
        "issuer": issuer,
        "trust_namespace": trust_namespace,
        "issued_utc_ms": now_ms - 5_000,
        "expires_utc_ms": now_ms + expires_delta_sec * 1000,
        "repo": "quocan050302/Build-app-crypto",
        "branch": MANDATORY_BRANCH_KH03_R6,
        "tested_executable_sha": executable_sha,
        "release_manifest_sha256": release_sha,
        "purpose": purpose,
        "stage": stage,
        "venue": APPROVED_VENUE_R6,
        "api_family": API_FAMILY_CLASSIC_V2,
        "symbol": APPROVED_SYMBOL_R6,
        "product_type": APPROVED_PRODUCT_TYPE_V2_R6,
        "allowed_endpoints": [
            "classic_v2_contracts",
            "classic_v2_candles",
            "classic_v2_history_candles",
            "classic_v2_history_fund_rate",
            "classic_v2_ticker",
            "classic_v2_ws_books1",
        ],
        "allowed_ws_channels": ["books1"],
        "window_start_ms": 1794000000000,
        "window_end_ms": 1794086400000,
        "destination_store_id": store_id,
        "destination_root": str(dest_root),
        "max_session_duration_sec": 120,
        "max_concurrency": 2,
        "caps": {
            "connections": 4,
            "requests": max_requests,
            "retries": 6,
            "subscriptions": 4,
            "messages": 500,
            "payload_bytes": max_payload_bytes,
            "decoded_bytes": max_payload_bytes * 2,
            "pages": 10,
            "disk_bytes": 10 * 1024 * 1024,
            "records": 5000,
        },
        "retention_policy": {"max_retention_days": 30, "git_commit_raw_payloads": False},
    }


def make_valid_instrument_raw() -> Dict[str, Any]:
    return {
        "symbol": "XAUUSDT",
        "productType": "USDT-FUTURES",
        "baseCoin": "XAU",
        "quoteCoin": "USDT",
        "symbolStatus": "normal",
        "sizeMultiplier": "0.001",
        "minTradeNum": "0.001",
        "minTradeUSDT": "5",
        "pricePlace": "2",
        "volumePlace": "3",
        "priceEndStep": "1",
        "fundInterval": "8",
        "priceBasis": "LAST_TRADE_CONTRACT",
        "calendarVerified": True,
    }


def run_kh03_r6_controls_a08(*, executable_sha: str, run_id: str) -> List[Dict[str, Any]]:
    reg = build_r6_source_contract_registry()
    now_utc = datetime(2026, 11, 25, 10, 0, 0, tzinfo=timezone.utc)
    receipts: List[Dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="r6_a08_") as td:
        tdir = Path(td)
        store_id = "qa_store_a08"
        tr_path, priv_key, _ = provision_qa_isolated_trust_root(tdir / "trust", store_id=store_id)
        tr_loaded = load_r6_owner_trust_root(tr_path, expected_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6)
        assert tr_loaded["loaded"] is True
        tr_qa = tr_loaded["trust_root"]
        dest_root = tdir / "dest"
        dest_root.mkdir()

        # A01: wrong host/path/version/parameter rejected vs documented exact contract
        def _a01_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
            r = validate_r6_endpoint_contract(
                reg,
                "classic_v2_history_candles",
                {"category": "USDT-FUTURES", "symbol": "XAUUSDT", "granularity": "1m", "extraBad": "1"},
                url_override="https://evil.example.com/api/v2/mix/market/history-candles",
            )
            return {"case": "wrong_host_mixed_v3_param"}, {"valid": r["valid"], "violations_count": len(r["violations"])}

        def _a01_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
            r = validate_r6_endpoint_contract(
                reg,
                "classic_v2_history_candles",
                {"productType": "USDT-FUTURES", "symbol": "XAUUSDT", "granularity": "1m", "limit": 100},
            )
            return {"case": "exact_classic_v2_contract"}, {"valid": r["valid"], "violations_count": len(r["violations"])}

        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-A01", "A_SOURCE_TRUST", "NEGATIVE_CONTRACT", "services.kh_03_r6_source_contract_service.validate_r6_endpoint_contract", "rejected_with_violations", "accepted_clean", "Wrong host/v3-mixed/extra param rejected vs exact Classic v2 contract"),
            negative_fn=_a01_neg, positive_fn=_a01_pos, executable_sha=executable_sha, run_id=run_id,
        ))

        # A02: wrong symbol/price basis/product vs valid exact instrument fixture
        def _a02_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
            bad = make_valid_instrument_raw()
            bad["symbol"] = "XAUTUSDT"
            bad["priceBasis"] = "MARK"
            r = validate_r6_instrument_identity(bad, reg.endpoints["classic_v2_contracts"])
            return {"symbol": "XAUTUSDT"}, {"valid": r["valid"], "violations_count": len(r["violations"])}

        def _a02_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
            r = validate_r6_instrument_identity(make_valid_instrument_raw(), reg.endpoints["classic_v2_contracts"])
            return {"symbol": "XAUUSDT"}, {"valid": r["valid"], "violations_count": len(r["violations"])}

        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-A02", "A_SOURCE_TRUST", "NEGATIVE_CONTRACT", "services.kh_03_r6_source_contract_service.validate_r6_instrument_identity", "rejected_with_violations", "accepted_clean", "Substitute symbol XAUTUSDT/MARK price basis rejected vs valid XAUUSDT"),
            negative_fn=_a02_neg, positive_fn=_a02_pos, executable_sha=executable_sha, run_id=run_id,
        ))

        # A03: missing tick/lot/multiplier vs full metadata (no default)
        def _a03_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
            bad = make_valid_instrument_raw()
            del bad["priceEndStep"]
            del bad["sizeMultiplier"]
            r = validate_r6_instrument_identity(bad, reg.endpoints["classic_v2_contracts"])
            return {"missing": ["priceEndStep", "sizeMultiplier"]}, {"valid": r["valid"], "violations_count": len(r["missing_fields"]) + len(r["violations"])}

        def _a03_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
            r = validate_r6_instrument_identity(make_valid_instrument_raw(), reg.endpoints["classic_v2_contracts"])
            return {"tick": r["normalized_metadata"]["derived_tick_size"]}, {"valid": r["valid"], "violations_count": len(r["missing_fields"]) + len(r["violations"])}

        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-A03", "A_SOURCE_TRUST", "NEGATIVE_CONTRACT", "services.kh_03_r6_source_contract_service.validate_r6_instrument_identity", "rejected_with_violations", "accepted_clean", "Missing tick/multiplier blocked without defaults vs full metadata"),
            negative_fn=_a03_neg, positive_fn=_a03_pos, executable_sha=executable_sha, run_id=run_id,
        ))

        # A04: private/login/order operation vs public GET/subscribe only
        def _a04_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
            r = validate_r6_endpoint_contract(
                reg,
                "classic_v2_ws_books1",
                {"instType": "USDT-FUTURES", "channel": "orders", "instId": "XAUUSDT"},
                method_or_op="login",
            )
            return {"op": "login", "channel": "orders"}, {"valid": r["valid"], "violations_count": len(r["violations"])}

        def _a04_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
            r = validate_r6_endpoint_contract(
                reg,
                "classic_v2_ws_books1",
                {"instType": "USDT-FUTURES", "channel": "books1", "instId": "XAUUSDT"},
                method_or_op="subscribe",
            )
            return {"op": "subscribe", "channel": "books1"}, {"valid": r["valid"], "violations_count": len(r["violations"])}

        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-A04", "A_SOURCE_TRUST", "NEGATIVE_CONTRACT", "services.kh_03_r6_source_contract_service.validate_r6_endpoint_contract", "rejected_with_violations", "accepted_clean", "Private login/orders WS op rejected vs public books1 subscribe"),
            negative_fn=_a04_neg, positive_fn=_a04_pos, executable_sha=executable_sha, run_id=run_id,
        ))

        # A05: unsigned template & QA grant trying to authorize external B_SMOKE vs clean QA signed grant
        def _a05_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
            tmpl = json.dumps(build_r6_unsigned_owner_review_template(tested_executable_sha=executable_sha, stage_a_run_id=run_id, source_registry_digest=reg.registry_digest))
            r1 = verify_r6_owner_grant(tmpl, tr_qa, now_utc, expected_executable_sha=executable_sha, expected_release_manifest_sha256="b" * 64, expected_store_id=store_id, expected_stage="B_SMOKE")
            gp_ext = make_qa_grant_payload(grant_id="G_A05_BAD", dest_root=dest_root, store_id=store_id, now_utc=now_utc, executable_sha=executable_sha, stage="B_SMOKE", purpose="DEV_ONLY")
            r2 = verify_r6_owner_grant(sign_r6_grant_payload_with_ed25519(gp_ext, priv_key), tr_qa, now_utc, expected_executable_sha=executable_sha, expected_release_manifest_sha256="b" * 64, expected_store_id=store_id, expected_stage="B_SMOKE")
            return {"unsigned_and_qa_to_ext": True}, {"valid": r1["valid"] or r2["valid"], "violations_count": len(r1["violations"]) + len(r2["violations"])}

        def _a05_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
            gp = make_qa_grant_payload(grant_id="G_A05_OK", dest_root=dest_root, store_id=store_id, now_utc=now_utc, executable_sha=executable_sha)
            r = verify_r6_owner_grant(sign_r6_grant_payload_with_ed25519(gp, priv_key), tr_qa, now_utc, expected_executable_sha=executable_sha, expected_release_manifest_sha256="b" * 64, expected_store_id=store_id, expected_stage="QA_LOOPBACK")
            return {"qa_signed_grant": True}, {"valid": r["valid"] and (not r["verified_grant"].external_capture_permitted), "violations_count": len(r["violations"])}

        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-A05", "A_SOURCE_TRUST", "NEGATIVE_CONTRACT", "services.kh_03_r6_durable_grant_service.verify_r6_owner_grant", "rejected_with_violations", "accepted_clean", "Unsigned template & QA->external grant rejected vs valid QA Ed25519 grant"),
            negative_fn=_a05_neg, positive_fn=_a05_pos, executable_sha=executable_sha, run_id=run_id,
        ))

        # A06: signature payload tamper or duplicate JSON key vs canonical valid signed payload
        def _a06_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
            gp = make_qa_grant_payload(grant_id="G_A06_T", dest_root=dest_root, store_id=store_id, now_utc=now_utc, executable_sha=executable_sha)
            signed = json.loads(sign_r6_grant_payload_with_ed25519(gp, priv_key))
            signed["grant_payload"]["caps"]["requests"] = 999  # tamper after sign!
            r1 = verify_r6_owner_grant(json.dumps(signed), tr_qa, now_utc, expected_executable_sha=executable_sha, expected_release_manifest_sha256="b" * 64, expected_store_id=store_id, expected_stage="QA_LOOPBACK")
            dup_raw = '{"grant_schema_version":"kh_03_r6_owner_grant_v1","grant_schema_version":"kh_03_r6_owner_grant_v1"}'
            r2 = verify_r6_owner_grant(dup_raw, tr_qa, now_utc, expected_executable_sha=executable_sha, expected_release_manifest_sha256="b" * 64, expected_store_id=store_id, expected_stage="QA_LOOPBACK")
            return {"tampered_and_dup_key": True}, {"valid": r1["valid"] or r2["valid"], "violations_count": len(r1["violations"]) + len(r2["violations"])}

        def _a06_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
            gp = make_qa_grant_payload(grant_id="G_A06_OK", dest_root=dest_root, store_id=store_id, now_utc=now_utc, executable_sha=executable_sha)
            r = verify_r6_owner_grant(sign_r6_grant_payload_with_ed25519(gp, priv_key), tr_qa, now_utc, expected_executable_sha=executable_sha, expected_release_manifest_sha256="b" * 64, expected_store_id=store_id, expected_stage="QA_LOOPBACK")
            return {"canonical_ed25519": True}, {"valid": r["valid"], "violations_count": len(r["violations"])}

        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-A06", "A_SOURCE_TRUST", "NEGATIVE_CONTRACT", "services.kh_03_r6_durable_grant_service.verify_r6_owner_grant", "rejected_with_violations", "accepted_clean", "Post-signature payload tamper & duplicate JSON key rejected vs valid canonical grant"),
            negative_fn=_a06_neg, positive_fn=_a06_pos, executable_sha=executable_sha, run_id=run_id,
        ))

        # A07: expired / clock rollback / revoked trust root vs in-window clean grant
        def _a07_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
            gp = make_qa_grant_payload(grant_id="G_A07_EXP", dest_root=dest_root, store_id=store_id, now_utc=now_utc - timedelta(hours=1), executable_sha=executable_sha, expires_delta_sec=60)
            r_exp = verify_r6_owner_grant(sign_r6_grant_payload_with_ed25519(gp, priv_key), tr_qa, now_utc, expected_executable_sha=executable_sha, expected_release_manifest_sha256="b" * 64, expected_store_id=store_id, expected_stage="QA_LOOPBACK")
            r_clk = verify_r6_owner_grant(sign_r6_grant_payload_with_ed25519(make_qa_grant_payload(grant_id="G_A07_CLK", dest_root=dest_root, store_id=store_id, now_utc=now_utc, executable_sha=executable_sha), priv_key), tr_qa, now_utc, expected_executable_sha=executable_sha, expected_release_manifest_sha256="b" * 64, expected_store_id=store_id, expected_stage="QA_LOOPBACK", ledger_high_water_utc_ms=int((now_utc + timedelta(minutes=10)).timestamp() * 1000))
            rev_path, _, _ = provision_qa_isolated_trust_root(tdir / "trust_rev", store_id=store_id, revoked=True)
            r_rev = load_r6_owner_trust_root(rev_path, expected_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6)
            return {"expired_rollback_revoked": True}, {"valid": r_exp["valid"] or r_clk["valid"] or r_rev["loaded"], "violations_count": len(r_exp["violations"]) + len(r_clk["violations"]) + len(r_rev["violations"])}

        def _a07_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
            gp = make_qa_grant_payload(grant_id="G_A07_OK", dest_root=dest_root, store_id=store_id, now_utc=now_utc, executable_sha=executable_sha)
            r = verify_r6_owner_grant(sign_r6_grant_payload_with_ed25519(gp, priv_key), tr_qa, now_utc, expected_executable_sha=executable_sha, expected_release_manifest_sha256="b" * 64, expected_store_id=store_id, expected_stage="QA_LOOPBACK", ledger_high_water_utc_ms=int((now_utc - timedelta(seconds=10)).timestamp() * 1000))
            return {"in_window_clean": True}, {"valid": r["valid"], "violations_count": len(r["violations"])}

        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-A07", "A_SOURCE_TRUST", "NEGATIVE_CONTRACT", "services.kh_03_r6_durable_grant_service.verify_r6_owner_grant", "rejected_with_violations", "accepted_clean", "Expired grant, clock rollback & revoked trust root rejected vs valid in-window grant"),
            negative_fn=_a07_neg, positive_fn=_a07_pos, executable_sha=executable_sha, run_id=run_id,
        ))

        # A08: budget/window/destination/SHA drift rejected (no silent clamp!) vs exact subset plan
        def _a08_neg() -> Tuple[Dict[str, Any], Dict[str, Any]]:
            gp = make_qa_grant_payload(grant_id="G_A08_P", dest_root=dest_root, store_id=store_id, now_utc=now_utc, executable_sha=executable_sha, max_requests=5)
            gv = verify_r6_owner_grant(sign_r6_grant_payload_with_ed25519(gp, priv_key), tr_qa, now_utc, expected_executable_sha=executable_sha, expected_release_manifest_sha256="b" * 64, expected_store_id=store_id, expected_stage="QA_LOOPBACK")
            p_bad = build_r6_bound_capture_plan(gv["verified_grant"], reg, {"caps": {"requests": 50}, "window_end_ms": 1794099999000}, {"ok": True}, plan_id="p_bad")
            g_sha_bad = verify_r6_owner_grant(sign_r6_grant_payload_with_ed25519(gp, priv_key), tr_qa, now_utc, expected_executable_sha="f" * 40, expected_release_manifest_sha256="b" * 64, expected_store_id=store_id, expected_stage="QA_LOOPBACK")
            return {"over_budget_and_sha_drift": True}, {"valid": p_bad["valid"] or g_sha_bad["valid"], "violations_count": len(p_bad["violations"]) + len(g_sha_bad["violations"])}

        def _a08_pos() -> Tuple[Dict[str, Any], Dict[str, Any]]:
            gp = make_qa_grant_payload(grant_id="G_A08_OK", dest_root=dest_root, store_id=store_id, now_utc=now_utc, executable_sha=executable_sha, max_requests=5)
            gv = verify_r6_owner_grant(sign_r6_grant_payload_with_ed25519(gp, priv_key), tr_qa, now_utc, expected_executable_sha=executable_sha, expected_release_manifest_sha256="b" * 64, expected_store_id=store_id, expected_stage="QA_LOOPBACK")
            p_ok = build_r6_bound_capture_plan(gv["verified_grant"], reg, {"caps": {"requests": 3}}, {"ok": True}, plan_id="p_ok")
            return {"exact_subset_plan": True}, {"valid": p_ok["valid"], "violations_count": len(p_ok["violations"])}

        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-A08", "A_SOURCE_TRUST", "NEGATIVE_CONTRACT", "services.kh_03_r6_bounded_transport_service.build_r6_bound_capture_plan", "rejected_with_violations", "accepted_clean", "Plan budget/window/SHA drift rejected without silent clamping vs valid subset plan"),
            negative_fn=_a08_neg, positive_fn=_a08_pos, executable_sha=executable_sha, run_id=run_id,
        ))

    return receipts


def test_kh03_r6_source_registry_and_capability_matrix() -> None:
    reg = build_r6_source_contract_registry()
    assert reg.status == "DOC_VERIFIED_READY_FOR_STAGE_B_REVIEW"
    assert reg.selected_api_family == API_FAMILY_CLASSIC_V2
    assert all(not ep.observed_contract_verified for ep in reg.endpoints.values())
    mat = build_r6_source_capability_matrix(reg)
    assert mat["rest_quote_snapshot_can_be_continuous_bbo"] is False
    assert mat["public_docs_prove_owner_fee_or_fills"] is False
    # Unprovisioned external owner trust root always returns BLOCKED
    tr_none = load_r6_owner_trust_root(None, expected_namespace=OWNER_EXTERNAL_TRUST_NAMESPACE_R6)
    assert tr_none["loaded"] is False
    assert tr_none["status"] == "BLOCKED_OWNER_TRUST_ROOT_NOT_PROVISIONED"


def test_kh03_r6_paired_controls_a01_to_a08() -> None:
    recs = run_kh03_r6_controls_a08(executable_sha="1" * 40, run_id="test_r6_a08")
    assert len(recs) == 8
    verd = derive_r6_fault_verdict(recs, expected_executable_sha="1" * 40, expected_run_id="test_r6_a08")
    assert verd["valid"] is True, verd["violations"]
    assert verd["passed_controls"] == 8
