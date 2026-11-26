"""
aurum-desk/backend/tests/test_kh03_r6_a1_plan_binding.py

AURUM DESK — KH-03.R6.A1 | GROUP H (H01..H10) IMMUTABLE PLAN, GRANT, REGISTRY,
LEASE & PER-ATTEMPT DISPATCH AUTHORIZATION PAIRED CONTROLS
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Dict, List, Mapping, Tuple

from services.kh_03_r6_a1_acceptance_service import (
    R6A1FaultCaseSpec,
    derive_r6_a1_fault_verdict,
    execute_r6_a1_fault_pair,
)
from services.kh_03_r6_a1_trust_service import (
    authorize_r6_a1_dispatch_request,
    canonicalize_r6_bound_plan_core,
    complete_caps_vector_a1,
    verify_authorized_dispatch_request_a1,
)
from services.kh_03_r6_bounded_transport_service import (
    build_r6_bound_capture_plan,
    collect_real_r6_resource_sample,
    supervise_r6_session,
    verify_r6_bound_plan_integrity,
)
from services.kh_03_r6_durable_grant_service import (
    QA_ISOLATED_TRUST_NAMESPACE_R6,
    R6VerifiedGrant,
    open_r6_durable_ledger,
    provision_qa_isolated_trust_root,
    provision_r6_durable_ledger,
    sign_r6_grant_payload_with_ed25519,
    verify_r6_owner_grant,
)
from services.kh_03_r6_source_contract_service import (
    API_FAMILY_CLASSIC_V2,
    APPROVED_PRODUCT_TYPE_V2_R6,
    APPROVED_SYMBOL_R6,
    APPROVED_VENUE_R6,
    MANDATORY_BRANCH_KH03_R6,
    build_r6_source_contract_registry,
)


def _mk_qa_grant(
    priv_key: Any,
    trust_root: Any,
    *,
    grant_id: str,
    store_id: str,
    dest_dir: Path,
    now_dt: datetime,
    tested_sha: str,
    rel_manifest_sha: str,
    caps_override: Mapping[str, int] | None = None,
    expires_offset_sec: int = 600,
) -> R6VerifiedGrant:
    now_ms = int(now_dt.timestamp() * 1000)
    caps = {
        "connections": 2,
        "requests": 10,
        "retries": 2,
        "subscriptions": 2,
        "messages": 50,
        "payload_bytes": 65_536,
        "decoded_bytes": 131_072,
        "pages": 4,
        "disk_bytes": 1_048_576,
        "records": 500,
    }
    if caps_override:
        caps.update(dict(caps_override))
    payload = {
        "grant_id": grant_id,
        "issuer": "QA_ISOLATED_HARNESS",
        "trust_namespace": QA_ISOLATED_TRUST_NAMESPACE_R6,
        "issued_utc_ms": now_ms - 1000,
        "expires_utc_ms": now_ms + expires_offset_sec * 1000,
        "repo": "quocan050302/Build-app-crypto",
        "branch": MANDATORY_BRANCH_KH03_R6,
        "tested_executable_sha": tested_sha,
        "release_manifest_sha256": rel_manifest_sha,
        "purpose": "QA_FIXTURE",
        "stage": "QA_LOOPBACK",
        "venue": APPROVED_VENUE_R6,
        "api_family": API_FAMILY_CLASSIC_V2,
        "symbol": APPROVED_SYMBOL_R6,
        "product_type": APPROVED_PRODUCT_TYPE_V2_R6,
        "allowed_endpoints": [
            "classic_v2_contracts",
            "classic_v2_history_candles",
            "classic_v2_history_fund_rate",
            "classic_v2_ws_books1",
        ],
        "allowed_ws_channels": ["books1"],
        "window_start_ms": 1794000000000,
        "window_end_ms": 1794003600000,
        "destination_store_id": store_id,
        "destination_root": str(dest_dir),
        "max_session_duration_sec": 120,
        "max_concurrency": 2,
        "caps": caps,
        "retention_policy": {"max_retention_days": 7, "git_commit_raw_payloads": False},
    }
    raw = sign_r6_grant_payload_with_ed25519(payload, priv_key)
    res = verify_r6_owner_grant(
        raw,
        trust_root,
        now_dt,
        expected_executable_sha=tested_sha,
        expected_release_manifest_sha256=rel_manifest_sha,
        expected_store_id=store_id,
        expected_stage="QA_LOOPBACK",
    )
    assert res["valid"] and res["verified_grant"] is not None, f"grant_verify_failed:{res}"
    return res["verified_grant"]


def run_kh03_r6_a1_controls_h01_to_h10(
    *,
    executable_sha: str,
    run_id: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    now_dt = datetime(2026, 11, 26, 2, 0, 0, tzinfo=timezone.utc)
    rel_sha = "e" * 64
    reg = build_r6_source_contract_registry()
    receipts: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {}

    with tempfile.TemporaryDirectory(prefix="a1_grp_h_") as td:
        root = Path(td)
        dest = root / "dest"
        dest.mkdir()
        sid = "a1_h_store"
        tr_path, priv_k, _ = provision_qa_isolated_trust_root(root / "trust", store_id=sid)
        from services.kh_03_r6_durable_grant_service import load_r6_owner_trust_root
        tr_qa = load_r6_owner_trust_root(tr_path, expected_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6)["trust_root"]
        sroot = root / "ledger"
        provision_r6_durable_ledger(sroot, store_id=sid, trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, now_utc=now_dt)
        led = open_r6_durable_ledger(
            {"ledger_store_root": str(sroot), "store_id": sid},
            sid,
            expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6,
            actual_now=now_dt,
        )["ledger"]

        vg_base = _mk_qa_grant(priv_k, tr_qa, grant_id="G_H_BASE", store_id=sid, dest_dir=dest, now_dt=now_dt, tested_sha=executable_sha, rel_manifest_sha=rel_sha)
        pl_clean = build_r6_bound_capture_plan(vg_base, reg, {}, {"ok": True}, plan_id="pl_h_clean")["plan"]
        assert pl_clean is not None

        # H01: Nested caps immutability (MappingProxyType) + forced tamper rejected
        def _h01_neg():
            inplace_blocked = False
            try:
                pl_clean.caps["requests"] = 999999  # type: ignore[index]
            except TypeError:
                inplace_blocked = True
            tampered = dataclasses.replace(pl_clean, caps={**dict(pl_clean.caps), "requests": 999999})
            ok, viols = verify_r6_bound_plan_integrity(tampered, vg_base, registry=reg, actual_now=now_dt)
            return (
                {"attack": "nested_caps_inplace_and_replace_tamper"},
                {"allowed": ok, "inplace_blocked": inplace_blocked, "violations_count": len(viols) + (0 if inplace_blocked else 0), "violations": viols},
            )

        def _h01_pos():
            ok, viols = verify_r6_bound_plan_integrity(pl_clean, vg_base, registry=reg, actual_now=now_dt)
            return ({"check": "clean_plan_verify"}, {"allowed": ok, "violations_count": len(viols)})

        r_h01 = execute_r6_a1_fault_pair(
            R6A1FaultCaseSpec("H01", "H", "BASELINE_BUG_RED", "services.kh_03_r6_bounded_transport_service.verify_r6_bound_plan_integrity", "a1_rejected", "a1_accepted", "Nested caps MappingProxyType immutability and tamper rejection"),
            negative_fn=_h01_neg, positive_fn=_h01_pos, executable_sha=executable_sha, run_id=run_id,
        )
        receipts.append(r_h01)
        summary["nested_caps_immutable_and_tamper_rejected"] = (r_h01["status"] == "PASS")

        # H02: dataclass.replace endpoints / ws_channels with old digest+MAC
        def _h02_neg():
            t_ep = dataclasses.replace(pl_clean, endpoints=("classic_v2_ticker",), ws_channels=("books5",))
            ok, viols = verify_r6_bound_plan_integrity(t_ep, vg_base, registry=reg, actual_now=now_dt)
            return ({"attack": "replace_endpoints_and_ws_channels"}, {"allowed": ok, "violations_count": len(viols), "violations": viols})

        def _h02_pos():
            ok, viols = verify_r6_bound_plan_integrity(pl_clean, vg_base, registry=reg, actual_now=now_dt)
            return ({"check": "clean_endpoints_channels"}, {"allowed": ok, "violations_count": len(viols)})

        r_h02 = execute_r6_a1_fault_pair(
            R6A1FaultCaseSpec("H02", "H", "BASELINE_BUG_RED", "services.kh_03_r6_bounded_transport_service.verify_r6_bound_plan_integrity", "a1_rejected", "a1_accepted", "dataclass.replace endpoints/ws_channels rejected"),
            negative_fn=_h02_neg, positive_fn=_h02_pos, executable_sha=executable_sha, run_id=run_id,
        )
        receipts.append(r_h02)

        # H03: window / duration / concurrency / destination mutation
        def _h03_neg():
            t_w = dataclasses.replace(
                pl_clean,
                window_end_ms=pl_clean.window_end_ms + 3_600_000,
                max_session_duration_sec=pl_clean.max_session_duration_sec + 999,
                max_concurrency=4,
                destination_root=str(root / "other_dest"),
            )
            ok, viols = verify_r6_bound_plan_integrity(t_w, vg_base, registry=reg, actual_now=now_dt)
            return ({"attack": "replace_window_duration_concurrency_dest"}, {"allowed": ok, "violations_count": len(viols), "violations": viols})

        def _h03_pos():
            ok, viols = verify_r6_bound_plan_integrity(pl_clean, vg_base, registry=reg, actual_now=now_dt)
            return ({"check": "clean_window_duration_concurrency"}, {"allowed": ok, "violations_count": len(viols)})

        r_h03 = execute_r6_a1_fault_pair(
            R6A1FaultCaseSpec("H03", "H", "NEGATIVE_CONTRACT", "services.kh_03_r6_bounded_transport_service.verify_r6_bound_plan_integrity", "a1_rejected", "a1_accepted", "Window, duration, concurrency, destination mutation rejected"),
            negative_fn=_h03_neg, positive_fn=_h03_pos, executable_sha=executable_sha, run_id=run_id,
        )
        receipts.append(r_h03)
        summary["dataclass_replace_endpoints_window_rejected"] = (r_h02["status"] == "PASS" and r_h03["status"] == "PASS")

        # H04: Recompute public digest for altered plan but keep old MAC -> deny before reserve/claim!
        def _h04_neg():
            new_caps = {**dict(pl_clean.caps), "requests": 9}
            _, new_dig, _ = canonicalize_r6_bound_plan_core(
                plan_id=pl_clean.plan_id,
                stage=pl_clean.stage,
                purpose=pl_clean.purpose,
                grant_identity_key=pl_clean.grant_identity_key,
                grant_payload_digest=pl_clean.grant_payload_digest,
                tested_executable_sha=pl_clean.tested_executable_sha,
                release_manifest_sha256=pl_clean.release_manifest_sha256,
                registry_digest=pl_clean.registry_digest,
                venue=pl_clean.venue,
                api_family=pl_clean.api_family,
                symbol=pl_clean.symbol,
                product_type=pl_clean.product_type,
                destination_store_id=pl_clean.destination_store_id,
                destination_root=pl_clean.destination_root,
                endpoints=pl_clean.endpoints,
                ws_channels=pl_clean.ws_channels,
                window_start_ms=pl_clean.window_start_ms,
                window_end_ms=pl_clean.window_end_ms,
                max_session_duration_sec=pl_clean.max_session_duration_sec,
                max_concurrency=pl_clean.max_concurrency,
                caps=new_caps,
                expires_utc_ms=pl_clean.expires_utc_ms,
                external_capture_permitted=pl_clean.external_capture_permitted,
                task_policy_digest=pl_clean.task_policy_digest,
            )
            forged = dataclasses.replace(pl_clean, caps=new_caps, plan_digest=str(new_dig))
            ok, viols = verify_r6_bound_plan_integrity(forged, vg_base, registry=reg, actual_now=now_dt)
            cl = led.claim_r6_grant_session_atomic(vg_base, forged, "sess_h04_forged", actual_now=now_dt)
            return (
                {"attack": "recomputed_digest_old_mac"},
                {"allowed": ok or bool(cl.get("claimed")), "violations_count": len(viols) + len(cl.get("violations") or []), "claim_status": cl.get("status")},
            )

        def _h04_pos():
            vg_p = _mk_qa_grant(priv_k, tr_qa, grant_id="G_H04_POS", store_id=sid, dest_dir=dest, now_dt=now_dt, tested_sha=executable_sha, rel_manifest_sha=rel_sha)
            pl_p = build_r6_bound_capture_plan(vg_p, reg, {}, {"ok": True}, plan_id="pl_h04_pos")["plan"]
            ok, viols = verify_r6_bound_plan_integrity(pl_p, vg_p, registry=reg, actual_now=now_dt)
            return ({"check": "authentic_digest_and_mac"}, {"allowed": ok, "violations_count": len(viols)})

        r_h04 = execute_r6_a1_fault_pair(
            R6A1FaultCaseSpec("H04", "H", "NEGATIVE_CONTRACT", "services.kh_03_r6_durable_grant_service.R6DurableGrantLedger.claim_r6_grant_session_atomic", "a1_rejected", "a1_accepted", "Recomputed public digest with old MAC rejected before claim"),
            negative_fn=_h04_neg, positive_fn=_h04_pos, executable_sha=executable_sha, run_id=run_id,
        )
        receipts.append(r_h04)
        summary["recomputed_digest_old_mac_rejected"] = (r_h04["status"] == "PASS")

        # H05: Forged R6VerifiedGrant object (unregistered token) / wrong namespace / expired / wrong release
        def _h05_neg():
            forged_vg = dataclasses.replace(vg_base, verification_token="forged_tok_123")
            r_build = build_r6_bound_capture_plan(forged_vg, reg, {}, {"ok": True}, plan_id="pl_h05_forged")
            ok_exp, viols_exp = verify_r6_bound_plan_integrity(
                pl_clean,
                vg_base,
                registry=reg,
                expected_release_manifest_sha256="9" * 64,
                actual_now=now_dt + timedelta(seconds=9999),
            )
            return (
                {"attack": "forged_grant_object_and_expired_wrong_release"},
                {"allowed": bool(r_build.get("valid")) or ok_exp, "violations_count": len(r_build.get("violations") or []) + len(viols_exp)},
            )

        def _h05_pos():
            ok, viols = verify_r6_bound_plan_integrity(pl_clean, vg_base, registry=reg, expected_executable_sha=executable_sha, expected_release_manifest_sha256=rel_sha, actual_now=now_dt)
            return ({"check": "authentic_registered_grant_and_release"}, {"allowed": ok, "violations_count": len(viols)})

        r_h05 = execute_r6_a1_fault_pair(
            R6A1FaultCaseSpec("H05", "H", "NEGATIVE_CONTRACT", "services.kh_03_r6_a1_trust_service.verify_registered_grant_authenticity_a1", "a1_rejected", "a1_accepted", "Forged verified grant object, expiry, and release drift rejected"),
            negative_fn=_h05_neg, positive_fn=_h05_pos, executable_sha=executable_sha, run_id=run_id,
        )
        receipts.append(r_h05)
        summary["forged_grant_object_rejected"] = (r_h05["status"] == "PASS")

        # H06: Registry digest / endpoint version / symbol / product drift
        def _h06_neg():
            drifted_reg = dataclasses.replace(reg, registry_digest="f" * 64)
            ok, viols = verify_r6_bound_plan_integrity(pl_clean, vg_base, registry=drifted_reg, actual_now=now_dt)
            return ({"attack": "registry_digest_drift"}, {"allowed": ok, "violations_count": len(viols), "violations": viols})

        def _h06_pos():
            ok, viols = verify_r6_bound_plan_integrity(pl_clean, vg_base, registry=reg, actual_now=now_dt)
            return ({"check": "matching_classic_v2_registry"}, {"allowed": ok, "violations_count": len(viols)})

        r_h06 = execute_r6_a1_fault_pair(
            R6A1FaultCaseSpec("H06", "H", "NEGATIVE_CONTRACT", "services.kh_03_r6_bounded_transport_service.verify_r6_bound_plan_integrity", "a1_rejected", "a1_accepted", "Registry digest drift rejected"),
            negative_fn=_h06_neg, positive_fn=_h06_pos, executable_sha=executable_sha, run_id=run_id,
        )
        receipts.append(r_h06)
        summary["registry_drift_rejected"] = (r_h06["status"] == "PASS")

        # H07: Foreign lease from another grant/plan/session -> zero dispatch!
        vg_a = _mk_qa_grant(priv_k, tr_qa, grant_id="G_H07_A", store_id=sid, dest_dir=dest, now_dt=now_dt, tested_sha=executable_sha, rel_manifest_sha=rel_sha)
        vg_b = _mk_qa_grant(priv_k, tr_qa, grant_id="G_H07_B", store_id=sid, dest_dir=dest, now_dt=now_dt, tested_sha=executable_sha, rel_manifest_sha=rel_sha)
        pl_a = build_r6_bound_capture_plan(vg_a, reg, {}, {"ok": True}, plan_id="pl_h07_a")["plan"]
        pl_b = build_r6_bound_capture_plan(vg_b, reg, {}, {"ok": True}, plan_id="pl_h07_b")["plan"]
        lease_a = led.claim_r6_grant_session_atomic(vg_a, pl_a, "sess_h07_a", actual_now=now_dt)["lease"]
        lease_b = led.claim_r6_grant_session_atomic(vg_b, pl_b, "sess_h07_b", actual_now=now_dt)["lease"]

        def _h07_neg():
            auth = authorize_r6_a1_dispatch_request(
                plan=pl_a, verified_grant=vg_a, lease=lease_b, registry=reg,
                endpoint_id="classic_v2_contracts", attempt_id="att_h07_neg", partition="QA_FIXTURE",
                requested_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 1024, "decoded_bytes": 2048}),
                deadline_monotonic=time.monotonic() + 30.0,
                params={"productType": APPROVED_PRODUCT_TYPE_V2_R6, "symbol": APPROVED_SYMBOL_R6},
                qa_loopback_mode=True, qa_loopback_url="http://127.0.0.1:18080/api/v2/mix/market/contracts", actual_now=now_dt,
            )
            return ({"attack": "foreign_lease_cross_grant"}, {"allowed": bool(auth["authorized"]), "violations_count": len(auth["violations"])})

        def _h07_pos():
            auth = authorize_r6_a1_dispatch_request(
                plan=pl_a, verified_grant=vg_a, lease=lease_a, registry=reg,
                endpoint_id="classic_v2_contracts", attempt_id="att_h07_pos", partition="QA_FIXTURE",
                requested_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 1024, "decoded_bytes": 2048}),
                deadline_monotonic=time.monotonic() + 30.0,
                params={"productType": APPROVED_PRODUCT_TYPE_V2_R6, "symbol": APPROVED_SYMBOL_R6},
                qa_loopback_mode=True, qa_loopback_url="http://127.0.0.1:18080/api/v2/mix/market/contracts", actual_now=now_dt,
            )
            return ({"check": "matching_active_lease"}, {"allowed": bool(auth["authorized"]), "violations_count": len(auth["violations"])})

        r_h07 = execute_r6_a1_fault_pair(
            R6A1FaultCaseSpec("H07", "H", "NEGATIVE_CONTRACT", "services.kh_03_r6_a1_trust_service.authorize_r6_a1_dispatch_request", "a1_rejected", "a1_accepted", "Foreign lease rejected with zero dispatch"),
            negative_fn=_h07_neg, positive_fn=_h07_pos, executable_sha=executable_sha, run_id=run_id,
        )
        receipts.append(r_h07)
        summary["foreign_lease_rejected_zero_dispatch"] = (r_h07["status"] == "PASS")

        # H08: Narrow plan cap < signed grant cap -> ledger stores and enforces smaller PLAN cap!
        vg_h08 = _mk_qa_grant(priv_k, tr_qa, grant_id="G_H08", store_id=sid, dest_dir=dest, now_dt=now_dt, tested_sha=executable_sha, rel_manifest_sha=rel_sha)
        narrow_caps = dict(vg_h08.caps)
        narrow_caps["requests"] = 1
        narrow_caps["payload_bytes"] = 2048
        narrow_caps["decoded_bytes"] = 4096
        pl_h08 = build_r6_bound_capture_plan(vg_h08, reg, {"caps": narrow_caps}, {"ok": True}, plan_id="pl_h08_narrow")["plan"]
        lease_h08 = led.claim_r6_grant_session_atomic(vg_h08, pl_h08, "sess_h08", actual_now=now_dt)["lease"]

        def _h08_neg():
            # Try reserving requests=2 (allowed by grant=10, forbidden by plan=1!)
            r_ov = led.reserve_r6_io_budget_atomic(
                lease_h08, "att_h08_ov", "rest_get",
                complete_caps_vector_a1({"requests": 2, "payload_bytes": 1024, "decoded_bytes": 2048}),
                actual_now=now_dt,
            )
            return ({"attack": "reserve_above_narrow_plan_cap_below_grant_cap"}, {"allowed": bool(r_ov.get("reserved")), "violations_count": len(r_ov.get("violations") or [r_ov.get("status")])})

        def _h08_pos():
            r_ok = led.reserve_r6_io_budget_atomic(
                lease_h08, "att_h08_ok", "rest_get",
                complete_caps_vector_a1({"requests": 1, "payload_bytes": 1024, "decoded_bytes": 2048}),
                actual_now=now_dt,
            )
            return ({"check": "reserve_within_narrow_plan_cap"}, {"allowed": bool(r_ok.get("reserved")), "violations_count": len(r_ok.get("violations") or [])})

        r_h08 = execute_r6_a1_fault_pair(
            R6A1FaultCaseSpec("H08", "H", "NEGATIVE_CONTRACT", "services.kh_03_r6_durable_grant_service.R6DurableGrantLedger.reserve_r6_io_budget_atomic", "a1_rejected", "a1_accepted", "Narrow plan cap strictly enforced by ledger below grant cap"),
            negative_fn=_h08_neg, positive_fn=_h08_pos, executable_sha=executable_sha, run_id=run_id,
        )
        receipts.append(r_h08)
        summary["narrow_plan_cap_enforced_by_ledger"] = (r_h08["status"] == "PASS")

        # H09: Allowlisted host but unauthorized path, extra query param, out-of-window startTime, or WS login op
        def _h09_neg():
            bad_path = authorize_r6_a1_dispatch_request(
                plan=pl_a, verified_grant=vg_a, lease=lease_a, registry=reg,
                endpoint_id="classic_v2_contracts", attempt_id="att_h09_p", partition="QA_FIXTURE",
                requested_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 1024, "decoded_bytes": 2048}),
                deadline_monotonic=time.monotonic() + 30.0,
                params={"productType": APPROVED_PRODUCT_TYPE_V2_R6, "symbol": APPROVED_SYMBOL_R6, "evilParam": "1"},
                qa_loopback_mode=True, qa_loopback_url="http://127.0.0.1:18080/api/v2/mix/order/place-order", actual_now=now_dt,
            )
            bad_ws = authorize_r6_a1_dispatch_request(
                plan=pl_a, verified_grant=vg_a, lease=lease_a, registry=reg,
                endpoint_id="classic_v2_ws_books1", attempt_id="att_h09_ws", partition="QA_FIXTURE",
                requested_caps=complete_caps_vector_a1({"connections": 1, "subscriptions": 1, "messages": 5, "payload_bytes": 4096, "decoded_bytes": 8192}),
                deadline_monotonic=time.monotonic() + 30.0,
                subscribe_payload={"op": "login", "args": [{"instType": APPROVED_PRODUCT_TYPE_V2_R6, "channel": "books1", "instId": APPROVED_SYMBOL_R6}]},
                qa_loopback_mode=True, qa_loopback_url="ws://127.0.0.1:18081/v2/ws/public", actual_now=now_dt,
            )
            bad_ext = authorize_r6_a1_dispatch_request(
                plan=pl_a, verified_grant=vg_a, lease=lease_a, registry=reg,
                endpoint_id="classic_v2_contracts", attempt_id="att_h09_ext", partition="DEV_ONLY",
                requested_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 1024, "decoded_bytes": 2048}),
                deadline_monotonic=time.monotonic() + 30.0,
                params={"productType": APPROVED_PRODUCT_TYPE_V2_R6, "symbol": APPROVED_SYMBOL_R6},
                qa_loopback_mode=False, actual_now=now_dt,
            )
            return (
                {"attack": "unauthorized_path_extra_param_ws_login_and_external_hard_block"},
                {
                    "allowed": bool(bad_path["authorized"]) or bool(bad_ws["authorized"]) or bool(bad_ext["authorized"]),
                    "violations_count": len(bad_path["violations"]) + len(bad_ws["violations"]) + len(bad_ext["violations"]),
                    "ext_status": bad_ext["status"],
                },
            )

        def _h09_pos():
            good_ws = authorize_r6_a1_dispatch_request(
                plan=pl_a, verified_grant=vg_a, lease=lease_a, registry=reg,
                endpoint_id="classic_v2_ws_books1", attempt_id="att_h09_ok", partition="QA_FIXTURE",
                requested_caps=complete_caps_vector_a1({"connections": 1, "subscriptions": 1, "messages": 5, "payload_bytes": 4096, "decoded_bytes": 8192}),
                deadline_monotonic=time.monotonic() + 30.0,
                subscribe_payload={"op": "subscribe", "args": [{"instType": APPROVED_PRODUCT_TYPE_V2_R6, "channel": "books1", "instId": APPROVED_SYMBOL_R6}]},
                qa_loopback_mode=True, qa_loopback_url="ws://127.0.0.1:18081/v2/ws/public", actual_now=now_dt,
            )
            return ({"check": "exact_classic_v2_ws_contract"}, {"allowed": bool(good_ws["authorized"]), "violations_count": len(good_ws["violations"])})

        r_h09 = execute_r6_a1_fault_pair(
            R6A1FaultCaseSpec("H09", "H", "NEGATIVE_CONTRACT", "services.kh_03_r6_a1_trust_service.authorize_r6_a1_dispatch_request", "a1_rejected", "a1_accepted", "Allowlisted host with unauthorized path, query param, WS op, or external blocked"),
            negative_fn=_h09_neg, positive_fn=_h09_pos, executable_sha=executable_sha, run_id=run_id,
        )
        receipts.append(r_h09)
        summary["unauthorized_path_or_query_or_ws_op_rejected"] = (r_h09["status"] == "PASS")
        summary["nonloopback_dispatch_hard_blocked"] = (r_h09["status"] == "PASS")

        # H10: Post-enqueue expiry after wait -> zero extra I/O!
        hits_h10 = [0]

        class _H10Srv(BaseHTTPRequestHandler):
            def log_message(self, *a): return
            def do_GET(self):
                hits_h10[0] += 1
                body = b'{"code":"00000","data":[]}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _H10Srv)
        port = int(srv.server_address[1])
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            auth_h10 = authorize_r6_a1_dispatch_request(
                plan=pl_a, verified_grant=vg_a, lease=lease_a, registry=reg,
                endpoint_id="classic_v2_contracts", attempt_id="att_h10_late", partition="QA_FIXTURE",
                requested_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 1024, "decoded_bytes": 2048}),
                deadline_monotonic=time.monotonic() + 30.0,
                params={"productType": APPROVED_PRODUCT_TYPE_V2_R6, "symbol": APPROVED_SYMBOL_R6},
                qa_loopback_mode=True, qa_loopback_url=f"http://127.0.0.1:{port}/api/v2/mix/market/contracts", actual_now=now_dt,
            )["authorized_request"]
            assert auth_h10 is not None

            def _h10_neg():
                # Advance clock past grant expiry after enqueue -> verify & supervise MUST reject with 0 socket hits!
                future_dt = now_dt + timedelta(seconds=9999)
                ok_v, v_viols = verify_authorized_dispatch_request_a1(auth_h10, expected_lease=lease_a, expected_plan=pl_a, actual_now=future_dt)
                sup = supervise_r6_session(
                    ledger=led, lease=lease_a, plan=pl_a, verified_grant=vg_a,
                    validated_request={"kind": "rest_get", "url": auth_h10.dispatch_url, "params": dict(auth_h10.params)},
                    attempt_id="att_h10_late", request_type="rest_get",
                    reserved_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 1024, "decoded_bytes": 2048}),
                    resource_sample_fn=lambda: collect_real_r6_resource_sample({"dest": dest}, active_workers=1),
                    expected_volumes={"dest": dest}, session_deadline_monotonic=time.monotonic() + 30.0,
                    cancel_event=threading.Event(), utc_now_fn=lambda: future_dt,
                    qa_loopback_mode=True, allow_fixture_resource_sample=True,
                    registry=reg, authorized_request=auth_h10, run_id=run_id, partition="QA_FIXTURE",
                    endpoint_id="classic_v2_contracts", contract_digest=reg.endpoints["classic_v2_contracts"].content_digest,
                )
                return (
                    {"attack": "post_enqueue_grant_expired_after_wait"},
                    {"allowed": ok_v or bool(sup.get("ok")) or hits_h10[0] > 0, "violations_count": len(v_viols) + len(sup.get("violations") or []), "socket_hits": hits_h10[0]},
                )

            def _h10_pos():
                ok_v, v_viols = verify_authorized_dispatch_request_a1(auth_h10, expected_lease=lease_a, expected_plan=pl_a, actual_now=now_dt)
                return ({"check": "unexpired_authorized_request"}, {"allowed": ok_v, "violations_count": len(v_viols)})

            r_h10 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("H10", "H", "INTEGRATION_FAULT", "services.kh_03_r6_a1_trust_service.verify_authorized_dispatch_request_a1", "a1_rejected", "a1_accepted", "Post-enqueue expiry after wait denied with zero extra socket I/O"),
                negative_fn=_h10_neg, positive_fn=_h10_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_h10)
            summary["post_enqueue_expiry_zero_io"] = (r_h10["status"] == "PASS")
        finally:
            srv.shutdown()
            srv.server_close()
            th.join(timeout=2.0)

    return receipts, summary


def test_kh03_r6_a1_plan_binding_h01_to_h10() -> None:
    recs, summary = run_kh03_r6_a1_controls_h01_to_h10(executable_sha="h" * 40, run_id="pytest_a1_h")
    verd = derive_r6_a1_fault_verdict(recs, expected_executable_sha="h" * 40, expected_run_id="pytest_a1_h")
    assert verd["valid"], f"H_CONTROLS_FAILED:{verd['violations']}"
    assert verd["passed_controls"] == 10
    assert all(summary.values()), f"H_SUMMARY_INCOMPLETE:{summary}"
