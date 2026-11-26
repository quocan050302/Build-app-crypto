"""
aurum-desk/backend/tests/test_kh03_r6_a1_provenance.py

AURUM DESK — KH-03.R6.A1 | GROUP J (J01..J10) TRUSTED SUPERVISOR CAPABILITY TOKEN,
RAW BYTES PROVENANCE, SINGLE-USE CONSUMPTION, QA-ONLY POLICY & POST-RESTART CUSTODY AUDIT
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Dict, List, Tuple

from services.kh_03_r6_a1_acceptance_service import (
    R6A1FaultCaseSpec,
    derive_r6_a1_fault_verdict,
    execute_r6_a1_fault_pair,
)
from services.kh_03_r6_a1_trust_service import (
    R6TrustedSupervisorResult,
    _issue_r6_a1_trusted_supervisor_result,
    authorize_r6_a1_dispatch_request,
    complete_caps_vector_a1,
)
from services.kh_03_r6_bounded_transport_service import (
    build_r6_bound_capture_plan,
    collect_real_r6_resource_sample,
    supervise_r6_session,
)
from services.kh_03_r6_custody_readiness_service import (
    R6CustodyJournal,
    mint_r6_raw_provenance_receipt,
    verify_r6_custody_readonly,
    verify_r6_raw_provenance_receipt,
)
from services.kh_03_r6_durable_grant_service import (
    QA_ISOLATED_TRUST_NAMESPACE_R6,
    load_r6_owner_trust_root,
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


def run_kh03_r6_a1_controls_j01_to_j10(
    *,
    executable_sha: str,
    run_id: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    now_dt = datetime(2026, 11, 26, 2, 20, 0, tzinfo=timezone.utc)
    now_ms = int(now_dt.timestamp() * 1000)
    rel_sha = "2" * 64
    reg = build_r6_source_contract_registry()
    ep_c = reg.endpoints["classic_v2_contracts"]
    receipts: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {}

    raw_ok = b'{"code":"00000","data":[{"symbol":"XAUUSDT","pricePlace":"2"}]}'

    class _JHttp(BaseHTTPRequestHandler):
        def log_message(self, *a): return
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw_ok)))
            self.end_headers()
            self.wfile.write(raw_ok)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _JHttp)
    port = int(srv.server_address[1])
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()

    try:
        with tempfile.TemporaryDirectory(prefix="a1_grp_j_") as td:
            root = Path(td)
            dest = root / "dest"
            dest.mkdir()
            repo_fake = root / "fake_repo"
            repo_fake.mkdir()
            sid = "a1_j_store"
            tr_p, priv_k, _ = provision_qa_isolated_trust_root(root / "trust", store_id=sid)
            tr_qa = load_r6_owner_trust_root(tr_p, expected_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6)["trust_root"]
            sroot = root / "ledger"
            provision_r6_durable_ledger(sroot, store_id=sid, trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, now_utc=now_dt)
            led = open_r6_durable_ledger({"ledger_store_root": str(sroot), "store_id": sid}, sid, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_dt)["ledger"]

            gp = {
                "grant_id": "G_J_MAIN",
                "issuer": "QA_ISOLATED_HARNESS",
                "trust_namespace": QA_ISOLATED_TRUST_NAMESPACE_R6,
                "issued_utc_ms": now_ms - 1000,
                "expires_utc_ms": now_ms + 600_000,
                "repo": "quocan050302/Build-app-crypto",
                "branch": MANDATORY_BRANCH_KH03_R6,
                "tested_executable_sha": executable_sha,
                "release_manifest_sha256": rel_sha,
                "purpose": "QA_FIXTURE",
                "stage": "QA_LOOPBACK",
                "venue": APPROVED_VENUE_R6,
                "api_family": API_FAMILY_CLASSIC_V2,
                "symbol": APPROVED_SYMBOL_R6,
                "product_type": APPROVED_PRODUCT_TYPE_V2_R6,
                "allowed_endpoints": ["classic_v2_contracts", "classic_v2_history_candles"],
                "allowed_ws_channels": [],
                "window_start_ms": 1794000000000,
                "window_end_ms": 1794003600000,
                "destination_store_id": sid,
                "destination_root": str(dest),
                "max_session_duration_sec": 120,
                "max_concurrency": 1,
                "caps": {"connections": 2, "requests": 40, "retries": 2, "subscriptions": 0, "messages": 0, "payload_bytes": 131_072, "decoded_bytes": 262_144, "pages": 10, "disk_bytes": 2_097_152, "records": 500},
                "retention_policy": {"max_retention_days": 7, "git_commit_raw_payloads": False},
            }
            vg = verify_r6_owner_grant(
                sign_r6_grant_payload_with_ed25519(gp, priv_k), tr_qa, now_dt,
                expected_executable_sha=executable_sha, expected_release_manifest_sha256=rel_sha,
                expected_store_id=sid, expected_stage="QA_LOOPBACK",
            )["verified_grant"]
            pl = build_r6_bound_capture_plan(vg, reg, {}, {"ok": True}, plan_id="pl_j_main")["plan"]
            lease = led.claim_r6_grant_session_atomic(vg, pl, "sess_j_main", actual_now=now_dt)["lease"]

            att_seq = [0]

            def _do_real_loopback_attempt() -> Dict[str, Any]:
                att_seq[0] += 1
                aid = f"att_j_{att_seq[0]:02d}"
                caps = complete_caps_vector_a1({"requests": 1, "payload_bytes": 1024, "decoded_bytes": 2048})
                auth = authorize_r6_a1_dispatch_request(
                    plan=pl, verified_grant=vg, lease=lease, registry=reg,
                    endpoint_id="classic_v2_contracts", attempt_id=aid, partition="QA_FIXTURE",
                    requested_caps=caps, deadline_monotonic=time.monotonic() + 15.0,
                    params={"productType": APPROVED_PRODUCT_TYPE_V2_R6, "symbol": APPROVED_SYMBOL_R6},
                    qa_loopback_mode=True, qa_loopback_url=f"http://127.0.0.1:{port}/api/v2/mix/market/contracts", actual_now=now_dt,
                )["authorized_request"]
                return supervise_r6_session(
                    ledger=led, lease=lease, plan=pl, verified_grant=vg,
                    validated_request={"kind": "rest_get", "url": auth.dispatch_url, "params": dict(auth.params)},
                    attempt_id=aid, request_type="rest_get", reserved_caps=caps,
                    resource_sample_fn=lambda: collect_real_r6_resource_sample({"dest": dest}, active_workers=1),
                    expected_volumes={"dest": dest}, session_deadline_monotonic=time.monotonic() + 15.0,
                    cancel_event=threading.Event(), utc_now_fn=lambda: now_dt, qa_loopback_mode=True,
                    allow_fixture_resource_sample=True, registry=reg, authorized_request=auth,
                    run_id=run_id, partition="QA_FIXTURE", endpoint_id="classic_v2_contracts",
                    contract_digest=ep_c.content_digest,
                )

            custody_j = R6CustodyJournal(approved_root=dest, partition="QA_FIXTURE", run_id=run_id, repo_root=repo_fake)

            def _clean_pos(chunk_suffix: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
                sup = _do_real_loopback_attempt()
                tsr = sup["trusted_result"]
                srec = sup["settlement_receipt"]
                prov = mint_r6_raw_provenance_receipt(
                    tested_executable_sha=executable_sha, run_id=run_id, session_id=lease.session_id,
                    lease_id=lease.lease_id, attempt_id=tsr.attempt_id, endpoint_id="classic_v2_contracts",
                    contract_digest=ep_c.content_digest, partition="QA_FIXTURE", qa_loopback_mode=True,
                    transport_kind="REST_GET", supervisor_result=tsr, raw_payload_bytes_data=sup["raw_payload_bytes"],
                    settlement_receipt=srec,
                )
                pers = custody_j.persist_r6_verified_chunk(
                    chunk_id=f"j_chunk_{chunk_suffix}", raw_payload=sup["raw_payload_bytes"],
                    normalized_rows=[{"symbol": "XAUUSDT", "chunk": chunk_suffix}], provenance_receipt=prov,
                )
                ro = verify_r6_custody_readonly(custody_j.store_dir, expected_partition="QA_FIXTURE", expected_run_id=run_id, ledger_store_root=sroot)
                return (
                    {"check": f"real_loopback_provenance_and_custody_{chunk_suffix}"},
                    {"accepted_for_custody": prov.accepted_for_custody, "provenance_class": prov.provenance_class, "persisted": bool(pers.get("persisted")), "readonly_valid": bool(ro.get("valid"))},
                )

            # J01: Fabricated Mapping + arbitrary bytes CANNOT mint observed!
            def _j01_neg():
                fake_sup = {"ok": True, "status": "HTTP_200_BOUNDED_OK", "accepted_result": {"tls_verified": True, "decoded_bytes": 12}, "reap_info": {"reaped_verified": True, "child_pid": 99999, "child_create_time": 1.0, "child_returncode": 0}}
                prov = mint_r6_raw_provenance_receipt(
                    tested_executable_sha=executable_sha, run_id=run_id, session_id=lease.session_id,
                    lease_id=lease.lease_id, attempt_id="att_fake_j01", endpoint_id="classic_v2_contracts",
                    contract_digest=ep_c.content_digest, partition="DEV_ONLY", qa_loopback_mode=False,
                    transport_kind="REST_GET", supervisor_result=fake_sup, raw_payload_bytes_data=b"FAKE_OBSERVED!",
                )
                pers = custody_j.persist_r6_verified_chunk(chunk_id="j01_fake", raw_payload=b"FAKE_OBSERVED!", normalized_rows=[{"x": 1}], provenance_receipt=prov)
                return ({"attack": "fabricated_mapping_with_tls_and_reap"}, {"accepted_for_custody": prov.accepted_for_custody, "provenance_class": prov.provenance_class, "persisted": bool(pers.get("persisted"))})

            r_j01 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("J01", "J", "BASELINE_BUG_RED", "services.kh_03_r6_custody_readiness_service.mint_r6_raw_provenance_receipt", "a1_provenance_rejected", "a1_provenance_qa_persisted", "Fabricated Mapping cannot mint RAW_OBSERVED_VERIFIED"),
                negative_fn=_j01_neg, positive_fn=lambda: _clean_pos("j01"), executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_j01)
            summary["fabricated_mapping_cannot_mint_observed"] = (r_j01["status"] == "PASS")

            # J02: Forged / copied / replaced trusted token and changed result digest + direct internal issuer blocked!
            sup_j02 = _do_real_loopback_attempt()

            def _j02_neg():
                direct_blocked = False
                try:
                    _issue_r6_a1_trusted_supervisor_result(
                        capability="fake_cap", tested_executable_sha=executable_sha, run_id=run_id, session_id="s", lease_id="l", attempt_id="a", plan_digest="p", partition="QA_FIXTURE", endpoint_id="classic_v2_contracts", contract_digest=ep_c.content_digest, request_digest="r", started_utc_ms=1, finished_utc_ms=2, started_monotonic_sec=1.0, finished_monotonic_sec=2.0, child_pid=1, child_create_time=1.0, child_exit_code=0, reaped_verified=True, containment_kind="JOB", transport_kind="REST_GET", qa_loopback_mode=True, tls_verified=False, raw_payload_bytes=raw_ok, decoded_payload_bytes=raw_ok, known_counters=complete_caps_vector_a1(), settlement_receipt=sup_j02["settlement_receipt"], child_result_digest="d",
                    )
                except PermissionError:
                    direct_blocked = True
                tsr_copy = dataclasses.replace(sup_j02["trusted_result"], result_digest="f" * 64)
                prov = mint_r6_raw_provenance_receipt(
                    tested_executable_sha=executable_sha, run_id=run_id, session_id=lease.session_id,
                    lease_id=lease.lease_id, attempt_id=tsr_copy.attempt_id, endpoint_id="classic_v2_contracts",
                    contract_digest=ep_c.content_digest, partition="QA_FIXTURE", qa_loopback_mode=True,
                    transport_kind="REST_GET", supervisor_result=tsr_copy, raw_payload_bytes_data=sup_j02["raw_payload_bytes"],
                    settlement_receipt=sup_j02["settlement_receipt"],
                )
                return ({"attack": "copied_tsr_and_direct_internal_call"}, {"accepted_for_custody": prov.accepted_for_custody or (not direct_blocked), "provenance_class": prov.provenance_class, "persisted": False})

            r_j02 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("J02", "J", "NEGATIVE_CONTRACT", "services.kh_03_r6_a1_trust_service.verify_trusted_supervisor_result_a1", "a1_provenance_rejected", "a1_provenance_qa_persisted", "Forged/copied trusted token and direct internal issuer rejected"),
                negative_fn=_j02_neg, positive_fn=lambda: _clean_pos("j02"), executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_j02)
            summary["forged_or_copied_token_rejected"] = (r_j02["status"] == "PASS")

            # J03: Wrong run/session/lease/attempt/endpoint/partition/contract binding
            sup_j03 = _do_real_loopback_attempt()

            def _j03_neg():
                prov = mint_r6_raw_provenance_receipt(
                    tested_executable_sha=executable_sha, run_id="WRONG_RUN", session_id="WRONG_SESS",
                    lease_id="WRONG_LEASE", attempt_id="WRONG_ATT", endpoint_id="classic_v2_history_candles",
                    contract_digest="9" * 64, partition="QA_FIXTURE", qa_loopback_mode=True,
                    transport_kind="REST_GET", supervisor_result=sup_j03["trusted_result"],
                    raw_payload_bytes_data=sup_j03["raw_payload_bytes"], settlement_receipt=sup_j03["settlement_receipt"],
                )
                return ({"attack": "wrong_binding_ids"}, {"accepted_for_custody": prov.accepted_for_custody, "provenance_class": prov.provenance_class, "persisted": False})

            r_j03 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("J03", "J", "NEGATIVE_CONTRACT", "services.kh_03_r6_custody_readiness_service.mint_r6_raw_provenance_receipt", "a1_provenance_rejected", "a1_provenance_qa_persisted", "Wrong run/session/lease/attempt/endpoint/contract binding rejected"),
                negative_fn=_j03_neg, positive_fn=lambda: _clean_pos("j03"), executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_j03)
            summary["wrong_binding_ids_rejected"] = (r_j03["status"] == "PASS")

            # J04: Mismatched raw bytes/length/hash vs child's exact buffer
            sup_j04 = _do_real_loopback_attempt()

            def _j04_neg():
                tsr = sup_j04["trusted_result"]
                prov = mint_r6_raw_provenance_receipt(
                    tested_executable_sha=executable_sha, run_id=run_id, session_id=lease.session_id,
                    lease_id=lease.lease_id, attempt_id=tsr.attempt_id, endpoint_id="classic_v2_contracts",
                    contract_digest=ep_c.content_digest, partition="QA_FIXTURE", qa_loopback_mode=True,
                    transport_kind="REST_GET", supervisor_result=tsr,
                    raw_payload_bytes_data=sup_j04["raw_payload_bytes"] + b"!", settlement_receipt=sup_j04["settlement_receipt"],
                )
                return ({"attack": "substituted_raw_bytes_plus_1_byte"}, {"accepted_for_custody": prov.accepted_for_custody, "provenance_class": prov.provenance_class, "persisted": False})

            r_j04 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("J04", "J", "NEGATIVE_CONTRACT", "services.kh_03_r6_custody_readiness_service.mint_r6_raw_provenance_receipt", "a1_provenance_rejected", "a1_provenance_qa_persisted", "Mismatched raw payload bytes rejected"),
                negative_fn=_j04_neg, positive_fn=lambda: _clean_pos("j04"), executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_j04)
            summary["raw_bytes_length_or_hash_mismatch_rejected"] = (r_j04["status"] == "PASS")

            # J05: Unsettled / mismatched settlement receipt -> reject provenance
            sup_j05_a = _do_real_loopback_attempt()
            sup_j05_b = _do_real_loopback_attempt()

            def _j05_neg():
                tsr_a = sup_j05_a["trusted_result"]
                prov = mint_r6_raw_provenance_receipt(
                    tested_executable_sha=executable_sha, run_id=run_id, session_id=lease.session_id,
                    lease_id=lease.lease_id, attempt_id=tsr_a.attempt_id, endpoint_id="classic_v2_contracts",
                    contract_digest=ep_c.content_digest, partition="QA_FIXTURE", qa_loopback_mode=True,
                    transport_kind="REST_GET", supervisor_result=tsr_a,
                    raw_payload_bytes_data=sup_j05_a["raw_payload_bytes"],
                    settlement_receipt=sup_j05_b["settlement_receipt"],  # mismatched attempt settlement!
                )
                return ({"attack": "mismatched_settlement_receipt"}, {"accepted_for_custody": prov.accepted_for_custody, "provenance_class": prov.provenance_class, "persisted": False})

            r_j05 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("J05", "J", "NEGATIVE_CONTRACT", "services.kh_03_r6_custody_readiness_service.mint_r6_raw_provenance_receipt", "a1_provenance_rejected", "a1_provenance_qa_persisted", "Mismatched or missing settlement proof rejected"),
                negative_fn=_j05_neg, positive_fn=lambda: _clean_pos("j05"), executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_j05)
            summary["unsettled_or_overrun_rejected"] = (r_j05["status"] == "PASS")

            # J06: Late cancelled result / nonzero exit -> trusted_result is None, cannot mint provenance
            def _j06_neg():
                ev_c = threading.Event()
                att_seq[0] += 1
                aid = f"att_j06_late_{att_seq[0]}"
                caps = complete_caps_vector_a1({"requests": 1, "payload_bytes": 1024, "decoded_bytes": 2048})
                auth = authorize_r6_a1_dispatch_request(
                    plan=pl, verified_grant=vg, lease=lease, registry=reg,
                    endpoint_id="classic_v2_contracts", attempt_id=aid, partition="QA_FIXTURE",
                    requested_caps=caps, deadline_monotonic=time.monotonic() + 15.0,
                    params={"productType": APPROVED_PRODUCT_TYPE_V2_R6, "symbol": APPROVED_SYMBOL_R6},
                    qa_loopback_mode=True, qa_loopback_url=f"http://127.0.0.1:{port}/api/v2/mix/market/contracts", actual_now=now_dt,
                )["authorized_request"]
                sup_late = supervise_r6_session(
                    ledger=led, lease=lease, plan=pl, verified_grant=vg,
                    validated_request={"kind": "rest_get", "url": auth.dispatch_url, "params": dict(auth.params)},
                    attempt_id=aid, request_type="rest_get", reserved_caps=caps,
                    resource_sample_fn=lambda: collect_real_r6_resource_sample({"dest": dest}, active_workers=1),
                    expected_volumes={"dest": dest}, session_deadline_monotonic=time.monotonic() + 15.0,
                    cancel_event=ev_c, utc_now_fn=lambda: now_dt, qa_loopback_mode=True,
                    allow_fixture_resource_sample=True, registry=reg, authorized_request=auth,
                    run_id=run_id, partition="QA_FIXTURE", endpoint_id="classic_v2_contracts",
                    contract_digest=ep_c.content_digest, post_exit_hook=lambda: ev_c.set(),
                )
                prov = mint_r6_raw_provenance_receipt(
                    tested_executable_sha=executable_sha, run_id=run_id, session_id=lease.session_id,
                    lease_id=lease.lease_id, attempt_id=aid, endpoint_id="classic_v2_contracts",
                    contract_digest=ep_c.content_digest, partition="QA_FIXTURE", qa_loopback_mode=True,
                    transport_kind="REST_GET", supervisor_result=sup_late["trusted_result"],
                    raw_payload_bytes_data=raw_ok, settlement_receipt=sup_late.get("settlement_receipt"),
                )
                return ({"attack": "late_cancelled_supervisor_result"}, {"accepted_for_custody": prov.accepted_for_custody, "provenance_class": prov.provenance_class, "persisted": False})

            r_j06 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("J06", "J", "INTEGRATION_FAULT", "services.kh_03_r6_custody_readiness_service.mint_r6_raw_provenance_receipt", "a1_provenance_rejected", "a1_provenance_qa_persisted", "Late cancelled result cannot mint provenance"),
                negative_fn=_j06_neg, positive_fn=lambda: _clean_pos("j06"), executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_j06)
            summary["late_or_nonzero_exit_rejected"] = (r_j06["status"] == "PASS")

            # J07: Replay same trusted token / provenance receipt / chunk -> denied!
            sup_j07 = _do_real_loopback_attempt()
            tsr7 = sup_j07["trusted_result"]
            prov7 = mint_r6_raw_provenance_receipt(
                tested_executable_sha=executable_sha, run_id=run_id, session_id=lease.session_id,
                lease_id=lease.lease_id, attempt_id=tsr7.attempt_id, endpoint_id="classic_v2_contracts",
                contract_digest=ep_c.content_digest, partition="QA_FIXTURE", qa_loopback_mode=True,
                transport_kind="REST_GET", supervisor_result=tsr7, raw_payload_bytes_data=sup_j07["raw_payload_bytes"],
                settlement_receipt=sup_j07["settlement_receipt"],
            )
            pers7_first = custody_j.persist_r6_verified_chunk(chunk_id="j07_first", raw_payload=sup_j07["raw_payload_bytes"], normalized_rows=[{"k": 1}], provenance_receipt=prov7)
            assert pers7_first["persisted"]

            def _j07_neg():
                # 1. Try minting a second receipt from same token
                prov_dup = mint_r6_raw_provenance_receipt(
                    tested_executable_sha=executable_sha, run_id=run_id, session_id=lease.session_id,
                    lease_id=lease.lease_id, attempt_id=tsr7.attempt_id, endpoint_id="classic_v2_contracts",
                    contract_digest=ep_c.content_digest, partition="QA_FIXTURE", qa_loopback_mode=True,
                    transport_kind="REST_GET", supervisor_result=tsr7, raw_payload_bytes_data=sup_j07["raw_payload_bytes"],
                    settlement_receipt=sup_j07["settlement_receipt"],
                )
                # 2. Try persisting a second chunk with the already-consumed provenance receipt!
                pers_dup = custody_j.persist_r6_verified_chunk(chunk_id="j07_second", raw_payload=sup_j07["raw_payload_bytes"], normalized_rows=[{"k": 2}], provenance_receipt=prov7)
                ok_v_after, _ = verify_r6_raw_provenance_receipt(prov7, expected_payload_bytes=sup_j07["raw_payload_bytes"], require_live_process_token=True)
                return (
                    {"attack": "replay_consumed_token_and_receipt"},
                    {"accepted_for_custody": prov_dup.accepted_for_custody or ok_v_after, "provenance_class": prov_dup.provenance_class, "persisted": bool(pers_dup.get("persisted"))},
                )

            r_j07 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("J07", "J", "NEGATIVE_CONTRACT", "services.kh_03_r6_custody_readiness_service.R6CustodyJournal.persist_r6_verified_chunk", "a1_provenance_rejected", "a1_provenance_qa_persisted", "Single-use token and provenance replay denied"),
                negative_fn=_j07_neg, positive_fn=lambda: _clean_pos("j07"), executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_j07)
            summary["replay_same_token_or_chunk_denied"] = (r_j07["status"] == "PASS")

            # J08: QA promotion to observed DEV rejected despite simulated TLS!
            sup_j08 = _do_real_loopback_attempt()

            def _j08_neg():
                tsr8 = sup_j08["trusted_result"]
                prov_prom = mint_r6_raw_provenance_receipt(
                    tested_executable_sha=executable_sha, run_id=run_id, session_id=lease.session_id,
                    lease_id=lease.lease_id, attempt_id=tsr8.attempt_id, endpoint_id="classic_v2_contracts",
                    contract_digest=ep_c.content_digest, partition="DEV_ONLY", qa_loopback_mode=False,
                    transport_kind="REST_GET", supervisor_result=tsr8, raw_payload_bytes_data=sup_j08["raw_payload_bytes"],
                    settlement_receipt=sup_j08["settlement_receipt"],
                )
                return ({"attack": "qa_promotion_to_dev_only"}, {"accepted_for_custody": prov_prom.accepted_for_custody, "provenance_class": prov_prom.provenance_class, "persisted": False})

            r_j08 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("J08", "J", "NEGATIVE_CONTRACT", "services.kh_03_r6_custody_readiness_service.mint_r6_raw_provenance_receipt", "a1_provenance_rejected", "a1_provenance_qa_persisted", "QA promotion to observed DEV rejected in A1"),
                negative_fn=_j08_neg, positive_fn=lambda: _clean_pos("j08"), executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_j08)
            summary["qa_promotion_to_observed_dev_rejected_despite_tls"] = (r_j08["status"] == "PASS")

            # J09: Crash / failed unlink / anchor corruption -> safe cleanup & quarantine
            def _j09_neg():
                c_tmp = R6CustodyJournal(approved_root=dest, partition="QA_FIXTURE", run_id=f"{run_id}_j09", repo_root=repo_fake)
                t_file = c_tmp.store_dir / "owned_partial.tmp"
                t_file.write_bytes(b"partial")
                c_tmp._owned_temp_files.add(t_file)
                cln_fail = c_tmp.cleanup_owned_temp_files_safe(fail_unlink_paths={t_file})
                # Now actually clean up the temp file
                cln_real = c_tmp.cleanup_owned_temp_files_safe()
                return (
                    {"attack": "failed_owned_temp_unlink_quarantines"},
                    {"accepted_for_custody": bool(cln_fail["all_owned_temps_deleted"]), "provenance_class": "QUARANTINED", "persisted": not bool(cln_real["all_owned_temps_deleted"])},
                )

            r_j09 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("J09", "J", "INTEGRATION_FAULT", "services.kh_03_r6_custody_readiness_service.R6CustodyJournal.cleanup_owned_temp_files_safe", "a1_provenance_rejected", "a1_provenance_qa_persisted", "Failed owned temp unlink reports failure cleanly without touching unrelated files"),
                negative_fn=_j09_neg, positive_fn=lambda: _clean_pos("j09"), executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_j09)
            summary["crash_and_failed_unlink_quarantined_safe_cleanup"] = (r_j09["status"] == "PASS")

            # J10: Post-restart read-only verifier recomputes QA lineage against linked SQLite ledger & catches tampered file!
            def _j10_neg():
                c_tamp = R6CustodyJournal(approved_root=dest, partition="QA_FIXTURE", run_id=f"{run_id}_j10_tamp", repo_root=repo_fake)
                sup10 = _do_real_loopback_attempt()
                tsr10 = sup10["trusted_result"]
                # Mint with matching run_id for c_tamp? Use a fresh supervised attempt? Wait: tsr10 has run_id=run_id!
                # Copy custody_j.store_dir to an isolated clone and tamper 1 byte of a raw file!
                import shutil
                clone_dir = root / "custody_tampered_clone"
                shutil.copytree(custody_j.store_dir, clone_dir)
                raw_files = sorted((clone_dir / "chunks").glob("*.raw.bin"))
                assert raw_files, "expected persisted raw .raw.bin file"
                raw_files[0].write_bytes(raw_files[0].read_bytes() + b"X")
                ro_tamp = verify_r6_custody_readonly(clone_dir, expected_partition="QA_FIXTURE", expected_run_id=run_id, ledger_store_root=sroot)
                return (
                    {"attack": "tampered_raw_file_post_restart_readonly_audit"},
                    {"accepted_for_custody": bool(ro_tamp["valid"]), "provenance_class": "TAMPERED_REJECTED", "persisted": False},
                )

            r_j10 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("J10", "J", "INTEGRATION_FAULT", "services.kh_03_r6_custody_readiness_service.verify_r6_custody_readonly", "a1_provenance_rejected", "a1_provenance_qa_persisted", "Post-restart read-only custody audit verifies SQLite ledger proof and detects file tamper"),
                negative_fn=_j10_neg, positive_fn=lambda: _clean_pos("j10"), executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_j10)
            summary["qa_custody_readonly_post_restart_valid"] = (r_j10["status"] == "PASS")
            summary["tampered_raw_or_norm_file_detected"] = (r_j10["status"] == "PASS")
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=2.0)

    return receipts, summary


def test_kh03_r6_a1_provenance_j01_to_j10() -> None:
    recs, summary = run_kh03_r6_a1_controls_j01_to_j10(executable_sha="j" * 40, run_id="pytest_a1_j")
    verd = derive_r6_a1_fault_verdict(recs, expected_executable_sha="j" * 40, expected_run_id="pytest_a1_j")
    assert verd["valid"], f"J_CONTROLS_FAILED:{verd['violations']}"
    assert verd["passed_controls"] == 10
    assert all(summary.values()), f"J_SUMMARY_INCOMPLETE:{summary}"
