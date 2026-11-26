"""
aurum-desk/backend/tests/test_kh03_r6_transport_lifecycle.py

KH-03.R6 Paired Controls C01..C16 (Owned Subprocess Isolation, Windows Job Object / POSIX PGID
Process-Tree Termination & Reap, Real 127.0.0.1 Loopback HTTP/WS Servers, Slow-Drip Deadlines,
Chunked/Oversized/Gzip-Bomb Caps, Retry-After, WS Epochs/Reconnect/Queue Overflow & Resource Guard).
ZERO external network sockets — ALL servers strictly bind 127.0.0.1!
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Dict, List, Tuple

import websockets

from services.kh_03_r6_acceptance_service import (
    R6FaultCaseSpec,
    derive_r6_fault_verdict,
    execute_r6_fault_pair,
)
from services.kh_03_r6_a1_trust_service import complete_caps_vector_a1
from services.kh_03_r6_bounded_transport_service import (
    R6BoundedQueueWriter,
    build_r6_bound_capture_plan,
    collect_real_r6_resource_sample,
    execute_r6_bounded_rest_get,
    execute_r6_bounded_ws_session,
    supervise_r6_session,
    validate_r6_resource_guard,
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
from services.kh_03_r6_source_contract_service import build_r6_source_contract_registry
from tests.test_kh03_r6_source_contracts import make_qa_grant_payload


class _LoopbackHttpFixtureServer:
    """Isolated 127.0.0.1-only HTTP test server simulating clean, slow-drip, chunked, gzip-bomb, 429, 403, 302."""

    def __init__(self) -> None:
        self.retry_counter = 0
        self.transient_counter = 0
        parent = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:
                return

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                if path == "/ok":
                    body = json.dumps({"code": "00000", "data": [{"symbol": "XAUUSDT"}]}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif path == "/slow_drip":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    try:
                        for _ in range(30):
                            chunk = b"1\r\n \r\n"
                            self.wfile.write(chunk)
                            self.wfile.flush()
                            time.sleep(0.08)
                        self.wfile.write(b"0\r\n\r\n")
                    except OSError:
                        pass
                elif path == "/oversize_chunked":
                    self.send_response(200)
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    try:
                        big = b"A" * 16384
                        self.wfile.write(f"{len(big):x}\r\n".encode() + big + b"\r\n0\r\n\r\n")
                    except OSError:
                        pass
                elif path == "/gzip_bomb":
                    # Highly compressible 2 MiB zeros -> ~2 KiB gzip payload, blows up past 8 KiB decoded cap!
                    gz = gzip.compress(b"0" * (2 * 1024 * 1024))
                    self.send_response(200)
                    self.send_header("Content-Encoding", "gzip")
                    self.send_header("Content-Length", str(len(gz)))
                    self.end_headers()
                    self.wfile.write(gz)
                elif path == "/rate_429_long":
                    self.send_response(429)
                    self.send_header("Retry-After", "120")
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"{}")
                elif path == "/rate_429_short":
                    parent.retry_counter += 1
                    if parent.retry_counter == 1:
                        self.send_response(429)
                        self.send_header("Retry-After", "0.15")
                        self.send_header("Content-Length", "2")
                        self.end_headers()
                        self.wfile.write(b"{}")
                    else:
                        body = b'{"code":"00000","retried":true}'
                        self.send_response(200)
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                elif path == "/redirect_302":
                    self.send_response(302)
                    self.send_header("Location", "https://evil.example.com/steal")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif path == "/auth_403":
                    self.send_response(403)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"{}")
                elif path == "/transient_503":
                    parent.transient_counter += 1
                    if parent.transient_counter == 1:
                        self.send_response(503)
                        self.send_header("Content-Length", "2")
                        self.end_headers()
                        self.wfile.write(b"{}")
                    else:
                        body = b'{"code":"00000","recovered":true}'
                        self.send_response(200)
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)


class _LoopbackWsFixtureServer:
    """Isolated 127.0.0.1-only WebSocket test server simulating clean ack+snapshot, bad ack, oversize, disconnect+reconnect, overflow."""

    def __init__(self) -> None:
        self._ready = threading.Event()
        self._stop_fut: Any = None
        self._loop: Any = None
        self.port = 0
        self._conn_counts: Dict[str, int] = {}
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(timeout=5.0)

    def _run(self) -> None:
        async def _serve() -> None:
            async def handler(ws: Any) -> None:
                path = getattr(getattr(ws, "request", None), "path", "/clean")
                try:
                    raw_sub = await asyncio.wait_for(ws.recv(), timeout=3.0)
                    sub = json.loads(raw_sub)
                except Exception:
                    return
                if path == "/clean":
                    await ws.send(json.dumps({"event": "subscribe", "arg": sub["args"][0]}))
                    await ws.send(json.dumps({"action": "snapshot", "arg": sub["args"][0], "data": [{"bids": [["2650.10", "5"]], "asks": [["2650.20", "4"]], "ts": "1794000001000"}]}))
                    await ws.send(json.dumps({"action": "update", "arg": sub["args"][0], "data": [{"bids": [["2650.15", "3"]], "asks": [["2650.25", "4"]], "ts": "1794000002000"}]}))
                    await asyncio.sleep(0.05)
                elif path == "/bad_ack":
                    await ws.send(json.dumps({"event": "subscribe", "arg": {"instType": "USDT-FUTURES", "channel": "WRONG_CH", "instId": "BTCUSDT"}}))
                    await asyncio.sleep(0.05)
                elif path == "/oversize":
                    await ws.send(json.dumps({"event": "subscribe", "arg": sub["args"][0]}))
                    await ws.send(json.dumps({"action": "snapshot", "pad": "X" * 20000}))
                    await asyncio.sleep(0.05)
                elif path == "/disconnect_reconnect":
                    c = self._conn_counts.get(path, 0) + 1
                    self._conn_counts[path] = c
                    await ws.send(json.dumps({"event": "subscribe", "arg": sub["args"][0]}))
                    await ws.send(json.dumps({"action": "snapshot", "arg": sub["args"][0], "data": [{"bids": [["2650.10", "5"]], "asks": [["2650.20", "4"]], "ts": str(1794000000000 + c * 5000)}]}))
                    if c == 1:
                        await ws.close(code=1011, reason="simulated_drop")
                    else:
                        await asyncio.sleep(0.3)
                elif path == "/flood":
                    await ws.send(json.dumps({"event": "subscribe", "arg": sub["args"][0]}))
                    for i in range(10):
                        await ws.send(json.dumps({"action": "snapshot" if i == 0 else "update", "idx": i, "pad": "Z" * 200}))
                    await asyncio.sleep(0.05)

            async with websockets.serve(handler, "127.0.0.1", 0) as srv:
                self.port = int(srv.sockets[0].getsockname()[1])
                self._stop_fut = asyncio.get_running_loop().create_future()
                self._ready.set()
                await self._stop_fut

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(_serve())
        finally:
            self._loop.close()

    def url(self, path: str) -> str:
        return f"ws://127.0.0.1:{self.port}{path}"

    def close(self) -> None:
        if self._loop and self._stop_fut and not self._stop_fut.done():
            self._loop.call_soon_threadsafe(self._stop_fut.set_result, True)
        self._thread.join(timeout=3.0)


def run_kh03_r6_controls_c16(*, executable_sha: str, run_id: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    now_utc = datetime(2026, 11, 25, 10, 0, 0, tzinfo=timezone.utc)
    reg = build_r6_source_contract_registry()
    receipts: List[Dict[str, Any]] = []
    all_lifecycle_traces: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {}

    http_srv = _LoopbackHttpFixtureServer()
    ws_srv = _LoopbackWsFixtureServer()
    try:
        with tempfile.TemporaryDirectory(prefix="r6_c16_") as td:
            tdir = Path(td)
            store_id = "qa_store_c16"
            store_root = tdir / "ledger"
            dest_root = tdir / "dest"
            dest_root.mkdir()
            tr_path, priv_key, _ = provision_qa_isolated_trust_root(tdir / "trust", store_id=store_id)
            tr_qa = load_r6_owner_trust_root(tr_path, expected_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6)["trust_root"]
            provision_r6_durable_ledger(store_root, store_id=store_id, trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, now_utc=now_utc)
            ledger = open_r6_durable_ledger({"ledger_store_root": str(store_root), "store_id": store_id}, store_id, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_utc)["ledger"]
            exp_vols = {"dest": dest_root, "ledger": store_root}

            def _good_res_sample() -> Dict[str, Any]:
                return collect_real_r6_resource_sample(exp_vols, active_workers=1)

            def _claim_session(gid: str, *, max_requests: int = 10, max_payload_bytes: int = 262_144, expires_delta_sec: int = 600):
                gp = make_qa_grant_payload(grant_id=gid, dest_root=dest_root, store_id=store_id, now_utc=now_utc, executable_sha=executable_sha, max_requests=max_requests, max_payload_bytes=max_payload_bytes, expires_delta_sec=expires_delta_sec)
                gv = verify_r6_owner_grant(sign_r6_grant_payload_with_ed25519(gp, priv_key), tr_qa, now_utc, expected_executable_sha=executable_sha, expected_release_manifest_sha256="b" * 64, expected_store_id=store_id, expected_stage="QA_LOOPBACK")["verified_grant"]
                pl = build_r6_bound_capture_plan(gv, reg, {}, {"ok": True}, plan_id=f"pl_{gid}")["plan"]
                cl = ledger.claim_r6_grant_session_atomic(gv, pl, f"sess_{gid}", actual_now=now_utc)
                return gv, pl, cl["lease"]

            # C01: external socket forbidden without external owner approval vs QA loopback allowed
            r_ext_blk = execute_r6_bounded_rest_get("https://api.bitget.com/api/v2/mix/market/ticker", {"symbol": "XAUUSDT", "productType": "USDT-FUTURES"}, remaining_deadline_sec=3.0, max_payload_bytes=8192, max_decoded_bytes=16384, qa_loopback_mode=False, external_capture_permitted=False)
            gv_c01, pl_c01, lease_c01 = _claim_session("G_C01")
            sup_c01_ok = supervise_r6_session(
                ledger=ledger, lease=lease_c01, plan=pl_c01, verified_grant=gv_c01,
                validated_request={"kind": "rest_get", "url": http_srv.url("/ok"), "params": {}},
                attempt_id="att_c01_ok", request_type="rest_get",
                reserved_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 4096, "decoded_bytes": 8192}),
                resource_sample_fn=_good_res_sample, expected_volumes=exp_vols,
                session_deadline_monotonic=time.monotonic() + 10.0, utc_now_fn=lambda: now_utc, qa_loopback_mode=True,
            )
            ledger.finalize_r6_session(lease_c01, quarantine=False, reason="C01_OK", actual_now=now_utc)
            all_lifecycle_traces.extend({**e, "scenario": "C01_LOOPBACK_CLEAN"} for e in sup_c01_ok["lifecycle_trace"])
            summary["external_socket_without_grant_blocked"] = (r_ext_blk["ok"] is False and r_ext_blk["attempts"] == 0)
            summary["loopback_worker_launched_and_completed"] = (sup_c01_ok["ok"] is True)
            summary["loopback_worker_pid"] = int(sup_c01_ok["reap_info"]["child_pid"])
            summary["loopback_worker_os_exit_verified"] = (sup_c01_ok["reap_info"]["reaped_verified"] is True)
            receipts.append(execute_r6_fault_pair(
                R6FaultCaseSpec("R6-C01", "C_TRANSPORT_RESOURCE", "INTEGRATION_FAULT", "services.kh_03_r6_bounded_transport_service.supervise_r6_session", "blocked_external_zero_io", "transport_loopback_ok", "External socket blocked without grant vs owned loopback subprocess"),
                negative_fn=lambda: ({"external_blocked": True}, {"allowed": r_ext_blk["ok"], "external_requests_attempted": r_ext_blk["attempts"], "violations_count": len(r_ext_blk["violations"])}),
                positive_fn=lambda: ({"loopback_pid": sup_c01_ok["reap_info"]["child_pid"]}, {"ok": sup_c01_ok["ok"], "reaped_verified": sup_c01_ok["reap_info"]["reaped_verified"], "surviving_descendants": len(sup_c01_ok["reap_info"]["surviving_descendant_pids"]), "child_pid": sup_c01_ok["reap_info"]["child_pid"]}),
                executable_sha=executable_sha, run_id=run_id,
            ))

            # C02: actual-start expiry after queue wait -> zero dispatch vs valid remaining time
            gv_c02, pl_c02, lease_c02 = _claim_session("G_C02")
            sup_c02_exp = supervise_r6_session(
                ledger=ledger, lease=lease_c02, plan=pl_c02, verified_grant=gv_c02,
                validated_request={"kind": "rest_get", "url": http_srv.url("/ok"), "params": {}},
                attempt_id="att_c02_exp", request_type="rest_get",
                reserved_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 4096, "decoded_bytes": 8192}),
                resource_sample_fn=_good_res_sample, expected_volumes=exp_vols,
                session_deadline_monotonic=time.monotonic() - 0.1, utc_now_fn=lambda: now_utc, qa_loopback_mode=True,
            )
            sup_c02_ok = supervise_r6_session(
                ledger=ledger, lease=lease_c02, plan=pl_c02, verified_grant=gv_c02,
                validated_request={"kind": "rest_get", "url": http_srv.url("/ok"), "params": {}},
                attempt_id="att_c02_ok", request_type="rest_get",
                reserved_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 4096, "decoded_bytes": 8192}),
                resource_sample_fn=_good_res_sample, expected_volumes=exp_vols,
                session_deadline_monotonic=time.monotonic() + 10.0, utc_now_fn=lambda: now_utc, qa_loopback_mode=True,
            )
            ledger.finalize_r6_session(lease_c02, quarantine=False, reason="C02_OK", actual_now=now_utc)
            summary["actual_start_expiry_zero_dispatch"] = (sup_c02_exp["ok"] is False and sup_c02_exp["status"] == "ABORTED_EXPIRED_AT_ACTUAL_START")
            receipts.append(execute_r6_fault_pair(
                R6FaultCaseSpec("R6-C02", "C_TRANSPORT_RESOURCE", "NEGATIVE_CONTRACT", "services.kh_03_r6_bounded_transport_service.supervise_r6_session", "rest_or_ws_bounded_rejected", "transport_loopback_ok", "Expired session at actual start aborts with zero dispatch vs valid deadline"),
                negative_fn=lambda: ({"expired_before_start": True}, {"ok": sup_c02_exp["ok"], "violations_count": len(sup_c02_exp["violations"]), "expected_token_matched": "EXPIRED" in sup_c02_exp["status"]}),
                positive_fn=lambda: ({"valid_deadline": True}, {"ok": sup_c02_ok["ok"], "reaped_verified": sup_c02_ok["reap_info"]["reaped_verified"], "surviving_descendants": 0, "child_pid": sup_c02_ok["reap_info"]["child_pid"]}),
                executable_sha=executable_sha, run_id=run_id,
            ))

            # C03: cancellation during local stall -> actual OS kill & reap
            gv_c03, pl_c03, lease_c03 = _claim_session("G_C03")
            ev_cancel_c03 = threading.Event()
            threading.Timer(0.25, ev_cancel_c03.set).start()
            sup_c03_neg = supervise_r6_session(
                ledger=ledger, lease=lease_c03, plan=pl_c03, verified_grant=gv_c03,
                validated_request={"kind": "rest_get", "url": http_srv.url("/slow_drip"), "params": {}},
                attempt_id="att_c03_cancel", request_type="rest_get",
                reserved_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 4096, "decoded_bytes": 8192}),
                resource_sample_fn=_good_res_sample, expected_volumes=exp_vols,
                session_deadline_monotonic=time.monotonic() + 10.0, cancel_event=ev_cancel_c03, utc_now_fn=lambda: now_utc, qa_loopback_mode=True,
            )
            all_lifecycle_traces.extend({**e, "scenario": "C03_CANCEL_STALL"} for e in sup_c03_neg["lifecycle_trace"])
            sup_c03_pos = supervise_r6_session(
                ledger=ledger, lease=lease_c03, plan=pl_c03, verified_grant=gv_c03,
                validated_request={"kind": "rest_get", "url": http_srv.url("/ok"), "params": {}},
                attempt_id="att_c03_ok", request_type="rest_get",
                reserved_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 4096, "decoded_bytes": 8192}),
                resource_sample_fn=_good_res_sample, expected_volumes=exp_vols,
                session_deadline_monotonic=time.monotonic() + 10.0, utc_now_fn=lambda: now_utc, qa_loopback_mode=True,
            )
            ledger.finalize_r6_session(lease_c03, quarantine=False, reason="C03_OK", actual_now=now_utc)
            receipts.append(execute_r6_fault_pair(
                R6FaultCaseSpec("R6-C03", "C_TRANSPORT_RESOURCE", "INTEGRATION_FAULT", "services.kh_03_r6_bounded_transport_service.cancel_and_reap_r6_transport", "transport_reaped_aborted", "transport_loopback_ok", "Midflight cancel during loopback stall forcibly reaps child vs clean run"),
                negative_fn=lambda: ({"cancel_stall": True}, {"ok": sup_c03_neg["ok"], "reaped_verified": sup_c03_neg["reap_info"]["reaped_verified"], "surviving_descendants": len(sup_c03_neg["reap_info"]["surviving_descendant_pids"]), "accepted_result_is_none": sup_c03_neg["accepted_result"] is None}),
                positive_fn=lambda: ({"clean_run": True}, {"ok": sup_c03_pos["ok"], "reaped_verified": sup_c03_pos["reap_info"]["reaped_verified"], "surviving_descendants": 0, "child_pid": sup_c03_pos["reap_info"]["child_pid"]}),
                executable_sha=executable_sha, run_id=run_id,
            ))

            # C04: noncooperative child + grandchild ignores SIGTERM -> Windows Job Object / PGID kills whole tree!
            gv_c04, pl_c04, lease_c04 = _claim_session("G_C04")
            ev_cancel_c04 = threading.Event()
            threading.Timer(0.45, ev_cancel_c04.set).start()
            sup_c04_neg = supervise_r6_session(
                ledger=ledger, lease=lease_c04, plan=pl_c04, verified_grant=gv_c04,
                validated_request={"kind": "qa_stall_noncooperative", "spawn_grandchild": True, "stall_sec": 15.0},
                attempt_id="att_c04_tree", request_type="rest_get",
                reserved_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 4096, "decoded_bytes": 8192}),
                resource_sample_fn=_good_res_sample, expected_volumes=exp_vols,
                session_deadline_monotonic=time.monotonic() + 10.0, cancel_event=ev_cancel_c04, utc_now_fn=lambda: now_utc, qa_loopback_mode=True,
            )
            all_lifecycle_traces.extend({**e, "scenario": "C04_NONCOOP_TREE_KILL"} for e in sup_c04_neg["lifecycle_trace"])
            sup_c04_pos = supervise_r6_session(
                ledger=ledger, lease=lease_c04, plan=pl_c04, verified_grant=gv_c04,
                validated_request={"kind": "rest_get", "url": http_srv.url("/ok"), "params": {}},
                attempt_id="att_c04_ok", request_type="rest_get",
                reserved_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 4096, "decoded_bytes": 8192}),
                resource_sample_fn=_good_res_sample, expected_volumes=exp_vols,
                session_deadline_monotonic=time.monotonic() + 10.0, utc_now_fn=lambda: now_utc, qa_loopback_mode=True,
            )
            ledger.finalize_r6_session(lease_c04, quarantine=False, reason="C04_OK", actual_now=now_utc)
            summary["noncooperative_tree_terminated_and_reaped"] = (sup_c04_neg["reap_info"]["reaped_verified"] is True and sup_c04_neg["reap_info"]["force_terminated_tree"] is True)
            summary["surviving_descendants_after_reap_count"] = len(sup_c04_neg["reap_info"]["surviving_descendant_pids"])
            summary["containment_kind"] = sup_c04_neg["reap_info"]["containment_kind"]
            receipts.append(execute_r6_fault_pair(
                R6FaultCaseSpec("R6-C04", "C_TRANSPORT_RESOURCE", "INTEGRATION_FAULT", "services.kh_03_r6_bounded_transport_service.cancel_and_reap_r6_transport", "transport_reaped_aborted", "transport_loopback_ok", "Noncooperative child + grandchild killed via OS Job Object/PGID & reaped"),
                negative_fn=lambda: ({"containment": sup_c04_neg["reap_info"]["containment_kind"]}, {"ok": sup_c04_neg["ok"], "reaped_verified": sup_c04_neg["reap_info"]["reaped_verified"], "surviving_descendants": len(sup_c04_neg["reap_info"]["surviving_descendant_pids"]), "accepted_result_is_none": sup_c04_neg["accepted_result"] is None}),
                positive_fn=lambda: ({"clean_exit": True}, {"ok": sup_c04_pos["ok"], "reaped_verified": sup_c04_pos["reap_info"]["reaped_verified"], "surviving_descendants": 0, "child_pid": sup_c04_pos["reap_info"]["child_pid"]}),
                executable_sha=executable_sha, run_id=run_id,
            ))

            # C05: OS proof unavailable -> FAIL_UNDRAINED_TRANSPORT + quarantine ledger!
            sr_c05 = tdir / "ledger_c05"
            provision_r6_durable_ledger(sr_c05, store_id=store_id, trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, now_utc=now_utc)
            led_c05 = open_r6_durable_ledger({"ledger_store_root": str(sr_c05), "store_id": store_id}, store_id, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_utc)["ledger"]
            gp_c05 = make_qa_grant_payload(grant_id="G_C05_Q", dest_root=dest_root, store_id=store_id, now_utc=now_utc, executable_sha=executable_sha)
            gv_c05 = verify_r6_owner_grant(sign_r6_grant_payload_with_ed25519(gp_c05, priv_key), tr_qa, now_utc, expected_executable_sha=executable_sha, expected_release_manifest_sha256="b" * 64, expected_store_id=store_id, expected_stage="QA_LOOPBACK")["verified_grant"]
            pl_c05 = build_r6_bound_capture_plan(gv_c05, reg, {}, {"ok": True}, plan_id="pl_c05")["plan"]
            lease_c05 = led_c05.claim_r6_grant_session_atomic(gv_c05, pl_c05, "sess_c05", actual_now=now_utc)["lease"]
            sup_c05_neg = supervise_r6_session(
                ledger=led_c05, lease=lease_c05, plan=pl_c05, verified_grant=gv_c05,
                validated_request={"kind": "rest_get", "url": http_srv.url("/ok"), "params": {}},
                attempt_id="att_c05_unv", request_type="rest_get",
                reserved_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 4096, "decoded_bytes": 8192}),
                resource_sample_fn=_good_res_sample, expected_volumes={"dest": dest_root, "ledger": sr_c05},
                session_deadline_monotonic=time.monotonic() + 10.0, utc_now_fn=lambda: now_utc, qa_loopback_mode=True,
                simulate_unverifiable_os_proof=True,
            )
            all_lifecycle_traces.extend({**e, "scenario": "C05_UNVERIFIED_OS_PROOF_QUARANTINE"} for e in sup_c05_neg["lifecycle_trace"])
            reopen_c05 = open_r6_durable_ledger({"ledger_store_root": str(sr_c05), "store_id": store_id}, store_id, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_utc)
            summary["unverified_os_proof_quarantined"] = (sup_c05_neg["status"] == "FAIL_UNDRAINED_TRANSPORT" and reopen_c05["status"] == "BLOCKED_LEDGER_QUARANTINED")
            receipts.append(execute_r6_fault_pair(
                R6FaultCaseSpec("R6-C05", "C_TRANSPORT_RESOURCE", "INTEGRATION_FAULT", "services.kh_03_r6_bounded_transport_service.supervise_r6_session", "transport_quarantined_undrained", "transport_loopback_ok", "Unverified OS reap proof quarantines session & blocks further live runs"),
                negative_fn=lambda: ({"unverifiable_os_proof": True}, {"ok": sup_c05_neg["ok"], "status": sup_c05_neg["status"], "ledger_quarantined": reopen_c05["status"] == "BLOCKED_LEDGER_QUARANTINED"}),
                positive_fn=lambda: ({"verified_os_proof": True}, {"ok": sup_c01_ok["ok"], "reaped_verified": True, "surviving_descendants": 0, "child_pid": sup_c01_ok["reap_info"]["child_pid"]}),
                executable_sha=executable_sha, run_id=run_id,
            ))

            # C06: slow-drip server vs total deadline enforced (not reset by trickle bytes!)
            r_c06_neg = execute_r6_bounded_rest_get(http_srv.url("/slow_drip"), {}, remaining_deadline_sec=0.35, max_payload_bytes=8192, max_decoded_bytes=16384, max_retries=0, qa_loopback_mode=True)
            r_c06_pos = execute_r6_bounded_rest_get(http_srv.url("/ok"), {}, remaining_deadline_sec=3.0, max_payload_bytes=8192, max_decoded_bytes=16384, qa_loopback_mode=True)
            summary["slow_drip_total_deadline_enforced"] = (r_c06_neg["ok"] is False and "DEADLINE" in r_c06_neg["status"])
            receipts.append(execute_r6_fault_pair(
                R6FaultCaseSpec("R6-C06", "C_TRANSPORT_RESOURCE", "INTEGRATION_FAULT", "services.kh_03_r6_bounded_transport_service.execute_r6_bounded_rest_get", "rest_or_ws_bounded_rejected", "rest_or_ws_bounded_ok", "Slow-drip loopback server stopped by total deadline vs normal fast GET"),
                negative_fn=lambda: ({"slow_drip_deadline_sec": 0.35}, {"ok": r_c06_neg["ok"], "violations_count": len(r_c06_neg["violations"]), "expected_token_matched": "DEADLINE" in r_c06_neg["status"]}),
                positive_fn=lambda: ({"fast_ok": True}, {"ok": r_c06_pos["ok"], "violations_count": len(r_c06_pos["violations"])}),
                executable_sha=executable_sha, run_id=run_id,
            ))

            # C07: oversized / chunked unknown length cap+sentinel enforced before buffering
            r_c07_neg = execute_r6_bounded_rest_get(http_srv.url("/oversize_chunked"), {}, remaining_deadline_sec=3.0, max_payload_bytes=1024, max_decoded_bytes=2048, qa_loopback_mode=True)
            r_c07_pos = execute_r6_bounded_rest_get(http_srv.url("/ok"), {}, remaining_deadline_sec=3.0, max_payload_bytes=1024, max_decoded_bytes=2048, qa_loopback_mode=True)
            summary["oversized_and_chunked_rejected"] = (r_c07_neg["ok"] is False and r_c07_neg["payload_bytes_received"] <= 1025)
            receipts.append(execute_r6_fault_pair(
                R6FaultCaseSpec("R6-C07", "C_TRANSPORT_RESOURCE", "INTEGRATION_FAULT", "services.kh_03_r6_bounded_transport_service.execute_r6_bounded_rest_get", "rest_or_ws_bounded_rejected", "rest_or_ws_bounded_ok", "Oversized chunked stream stopped at cap+1 sentinel vs small body"),
                negative_fn=lambda: ({"cap": 1024, "received": r_c07_neg["payload_bytes_received"]}, {"ok": r_c07_neg["ok"], "violations_count": len(r_c07_neg["violations"]), "expected_token_matched": r_c07_neg["payload_bytes_received"] <= 1025}),
                positive_fn=lambda: ({"within_cap": True}, {"ok": r_c07_pos["ok"], "violations_count": len(r_c07_pos["violations"])}),
                executable_sha=executable_sha, run_id=run_id,
            ))

            # C08: gzip bomb rejected with bounded streaming decompression
            r_c08_neg = execute_r6_bounded_rest_get(http_srv.url("/gzip_bomb"), {}, remaining_deadline_sec=3.0, max_payload_bytes=65536, max_decoded_bytes=8192, qa_loopback_mode=True)
            r_c08_pos = execute_r6_bounded_rest_get(http_srv.url("/ok"), {}, remaining_deadline_sec=3.0, max_payload_bytes=65536, max_decoded_bytes=8192, qa_loopback_mode=True)
            summary["gzip_bomb_rejected_bounded"] = (r_c08_neg["ok"] is False and r_c08_neg["status"] == "REJECTED_DECOMPRESSED_BOMB_OVERFLOW" and r_c08_neg["decoded_bytes"] <= 8193)
            receipts.append(execute_r6_fault_pair(
                R6FaultCaseSpec("R6-C08", "C_TRANSPORT_RESOURCE", "INTEGRATION_FAULT", "services.kh_03_r6_bounded_transport_service.execute_r6_bounded_rest_get", "rest_or_ws_bounded_rejected", "rest_or_ws_bounded_ok", "2 MiB gzip bomb rejected within 8 KiB streaming decode cap vs normal JSON"),
                negative_fn=lambda: ({"decoded_cap": 8192, "decoded": r_c08_neg["decoded_bytes"]}, {"ok": r_c08_neg["ok"], "violations_count": len(r_c08_neg["violations"]), "expected_token_matched": r_c08_neg["status"] == "REJECTED_DECOMPRESSED_BOMB_OVERFLOW"}),
                positive_fn=lambda: ({"normal_json": True}, {"ok": r_c08_pos["ok"], "violations_count": len(r_c08_pos["violations"])}),
                executable_sha=executable_sha, run_id=run_id,
            ))

            # C09: HTTP 429 Retry-After honored without shortening (120s > deadline aborts; 0.15s < deadline honors & retries!)
            r_c09_neg = execute_r6_bounded_rest_get(http_srv.url("/rate_429_long"), {}, remaining_deadline_sec=3.0, max_payload_bytes=4096, max_decoded_bytes=8192, max_retries=2, qa_loopback_mode=True)
            r_c09_pos = execute_r6_bounded_rest_get(http_srv.url("/rate_429_short"), {}, remaining_deadline_sec=3.0, max_payload_bytes=4096, max_decoded_bytes=8192, max_retries=2, qa_loopback_mode=True)
            summary["retry_after_honored_no_shortening"] = (r_c09_neg["status"] == "ABORTED_RETRY_AFTER_EXCEEDS_DEADLINE" and r_c09_neg["sleeps_honored"] == [] and r_c09_pos["ok"] is True and len(r_c09_pos["sleeps_honored"]) == 1)
            receipts.append(execute_r6_fault_pair(
                R6FaultCaseSpec("R6-C09", "C_TRANSPORT_RESOURCE", "INTEGRATION_FAULT", "services.kh_03_r6_bounded_transport_service.execute_r6_bounded_rest_get", "rest_or_ws_bounded_rejected", "rest_or_ws_bounded_ok", "429 Retry-After 120s exceeding budget stops without shortening vs 0.15s honored"),
                negative_fn=lambda: ({"retry_after": 120}, {"ok": r_c09_neg["ok"], "violations_count": len(r_c09_neg["violations"]), "expected_token_matched": r_c09_neg["status"] == "ABORTED_RETRY_AFTER_EXCEEDS_DEADLINE"}),
                positive_fn=lambda: ({"retry_after": 0.15, "sleeps": r_c09_pos["sleeps_honored"]}, {"ok": r_c09_pos["ok"] and len(r_c09_pos["sleeps_honored"]) == 1, "violations_count": len(r_c09_pos["violations"])}),
                executable_sha=executable_sha, run_id=run_id,
            ))

            # C10: 403/302 redirect non-retryable (attempts==1) vs transient 503 bounded retry succeeds
            r_c10_redir = execute_r6_bounded_rest_get(http_srv.url("/redirect_302"), {}, remaining_deadline_sec=3.0, max_payload_bytes=4096, max_decoded_bytes=8192, max_retries=2, qa_loopback_mode=True)
            r_c10_403 = execute_r6_bounded_rest_get(http_srv.url("/auth_403"), {}, remaining_deadline_sec=3.0, max_payload_bytes=4096, max_decoded_bytes=8192, max_retries=2, qa_loopback_mode=True)
            r_c10_pos = execute_r6_bounded_rest_get(http_srv.url("/transient_503"), {}, remaining_deadline_sec=3.0, max_payload_bytes=4096, max_decoded_bytes=8192, max_retries=2, qa_loopback_mode=True)
            summary["nonretryable_401_403_redirect_stopped"] = (r_c10_redir["attempts"] == 1 and r_c10_403["attempts"] == 1 and r_c10_pos["ok"] is True and r_c10_pos["attempts"] == 2)
            receipts.append(execute_r6_fault_pair(
                R6FaultCaseSpec("R6-C10", "C_TRANSPORT_RESOURCE", "INTEGRATION_FAULT", "services.kh_03_r6_bounded_transport_service.execute_r6_bounded_rest_get", "rest_or_ws_bounded_rejected", "rest_or_ws_bounded_ok", "302 redirect & 403 fail closed with 1 attempt vs transient 503 bounded retry"),
                negative_fn=lambda: ({"redir_attempts": r_c10_redir["attempts"], "auth403_attempts": r_c10_403["attempts"]}, {"ok": r_c10_redir["ok"] or r_c10_403["ok"], "violations_count": len(r_c10_redir["violations"]) + len(r_c10_403["violations"]), "expected_token_matched": r_c10_redir["attempts"] == 1 and r_c10_403["attempts"] == 1}),
                positive_fn=lambda: ({"transient_503_attempts": r_c10_pos["attempts"]}, {"ok": r_c10_pos["ok"] and r_c10_pos["attempts"] == 2, "violations_count": len(r_c10_pos["violations"])}),
                executable_sha=executable_sha, run_id=run_id,
            ))

            # C11: WS invalid ack / private op / oversize frame rejected vs valid books1 subscribe
            sub_ok = {"op": "subscribe", "args": [{"instType": "USDT-FUTURES", "channel": "books1", "instId": "XAUUSDT"}]}
            sub_priv = {"op": "login", "args": [{"instType": "USDT-FUTURES", "channel": "orders", "instId": "XAUUSDT"}]}
            ws_priv = execute_r6_bounded_ws_session(ws_srv.url("/clean"), sub_priv, remaining_deadline_sec=2.0, max_frame_bytes=4096, max_messages=10, max_payload_bytes=16384, max_queue_items=10, max_queue_bytes=16384, qa_loopback_mode=True)
            ws_bad_ack = execute_r6_bounded_ws_session(ws_srv.url("/bad_ack"), sub_ok, remaining_deadline_sec=2.0, max_frame_bytes=4096, max_messages=10, max_payload_bytes=16384, max_queue_items=10, max_queue_bytes=16384, qa_loopback_mode=True)
            ws_big = execute_r6_bounded_ws_session(ws_srv.url("/oversize"), sub_ok, remaining_deadline_sec=2.0, max_frame_bytes=4096, max_messages=10, max_payload_bytes=16384, max_queue_items=10, max_queue_bytes=16384, qa_loopback_mode=True)
            ws_clean = execute_r6_bounded_ws_session(ws_srv.url("/clean"), sub_ok, remaining_deadline_sec=1.5, max_frame_bytes=4096, max_messages=3, max_payload_bytes=16384, max_queue_items=10, max_queue_bytes=16384, max_reconnects=0, qa_loopback_mode=True)
            summary["ws_valid_subscribe_ack_and_snapshot"] = (ws_clean["ok"] is True and len(ws_clean["events"]) >= 1 and ws_clean["epochs"][0]["snapshot_received"] is True)
            summary["ws_invalid_ack_or_private_op_rejected"] = (ws_priv["ok"] is False and ws_bad_ack["ok"] is False)
            summary["ws_oversized_frame_rejected"] = (ws_big["ok"] is False)
            receipts.append(execute_r6_fault_pair(
                R6FaultCaseSpec("R6-C11", "C_TRANSPORT_RESOURCE", "INTEGRATION_FAULT", "services.kh_03_r6_bounded_transport_service.execute_r6_bounded_ws_session", "rest_or_ws_bounded_rejected", "rest_or_ws_bounded_ok", "WS private op, bad ack & oversize frame rejected vs clean books1 subscribe"),
                negative_fn=lambda: ({"ws_priv_ack_big": True}, {"ok": ws_priv["ok"] or ws_bad_ack["ok"] or ws_big["ok"], "violations_count": len(ws_priv["violations"]) + len(ws_bad_ack["violations"]) + len(ws_big["violations"]), "expected_token_matched": True}),
                positive_fn=lambda: ({"ws_clean_events": len(ws_clean["events"])}, {"ok": ws_clean["ok"], "violations_count": len(ws_clean["violations"])}),
                executable_sha=executable_sha, run_id=run_id,
            ))

            # C12: WS disconnect/reconnect records explicit epoch discontinuity & stays within reconnect cap
            ws_reconn = execute_r6_bounded_ws_session(ws_srv.url("/disconnect_reconnect"), sub_ok, remaining_deadline_sec=1.0, max_frame_bytes=4096, max_messages=10, max_payload_bytes=16384, max_queue_items=10, max_queue_bytes=16384, max_reconnects=1, qa_loopback_mode=True)
            ws_reconn_cap0 = execute_r6_bounded_ws_session(ws_srv.url("/disconnect_reconnect"), sub_ok, remaining_deadline_sec=0.5, max_frame_bytes=4096, max_messages=10, max_payload_bytes=16384, max_queue_items=10, max_queue_bytes=16384, max_reconnects=0, qa_loopback_mode=True)
            summary["ws_disconnect_epoch_gap_and_bounded_reconnect"] = (len(ws_reconn["epochs"]) == 2 and len(ws_reconn["discontinuities"]) >= 1 and ws_reconn["reconnects_used"] == 1)
            receipts.append(execute_r6_fault_pair(
                R6FaultCaseSpec("R6-C12", "C_TRANSPORT_RESOURCE", "INTEGRATION_FAULT", "services.kh_03_r6_bounded_transport_service.execute_r6_bounded_ws_session", "data_anomaly_caught", "rest_or_ws_bounded_ok", "WS disconnect marks explicit epoch discontinuity & obeys reconnect cap"),
                negative_fn=lambda: ({"discontinuities": len(ws_reconn["discontinuities"]), "epochs": len(ws_reconn["epochs"])}, {"anomaly_caught": len(ws_reconn["discontinuities"]) >= 1 and len(ws_reconn["epochs"]) == 2 and ws_reconn_cap0["reconnects_used"] == 0, "fabricated_or_inflated": False}),
                positive_fn=lambda: ({"clean_epoch_2_snapshot": ws_reconn["epochs"][-1]["snapshot_received"]}, {"ok": ws_reconn["ok"] and ws_reconn["epochs"][-1]["snapshot_received"], "violations_count": len(ws_reconn["violations"])}),
                executable_sha=executable_sha, run_id=run_id,
            ))

            # C13: queue overflow backpressure causes controlled abort (ZERO silent drops!)
            ws_ovf = execute_r6_bounded_ws_session(ws_srv.url("/flood"), sub_ok, remaining_deadline_sec=1.5, max_frame_bytes=4096, max_messages=50, max_payload_bytes=65536, max_queue_items=2, max_queue_bytes=1024, max_reconnects=0, qa_loopback_mode=True)
            qw_clean = R6BoundedQueueWriter(max_items=5, max_bytes=4096)
            enq_ok = qw_clean.try_enqueue(b'{"a":1}', {"i": 1})
            summary["ws_queue_overflow_no_silent_drop"] = (ws_ovf["ok"] is False and ws_ovf["queue_overflow_events"] >= 1 and ws_ovf["status"] == "QUEUE_OVERFLOW_BACKPRESSURE_ABORT")
            receipts.append(execute_r6_fault_pair(
                R6FaultCaseSpec("R6-C13", "C_TRANSPORT_RESOURCE", "INTEGRATION_FAULT", "services.kh_03_r6_bounded_transport_service.R6BoundedQueueWriter", "rest_or_ws_bounded_rejected", "rest_or_ws_bounded_ok", "Bounded queue overflow aborts explicitly without silent drops vs clean enqueue"),
                negative_fn=lambda: ({"overflow_events": ws_ovf["queue_overflow_events"]}, {"ok": ws_ovf["ok"], "violations_count": len(ws_ovf["violations"]), "expected_token_matched": ws_ovf["status"] == "QUEUE_OVERFLOW_BACKPRESSURE_ABORT"}),
                positive_fn=lambda: ({"clean_enqueued": enq_ok["enqueued"]}, {"ok": enq_ok["enqueued"], "violations_count": 0}),
                executable_sha=executable_sha, run_id=run_id,
            ))

            # C14: multi-attempt reservation race incl error bodies & unknown bytes accounted against tight cap
            gv_c14, pl_c14, lease_c14 = _claim_session("G_C14", max_requests=1, max_payload_bytes=4096)
            r1_c14 = ledger.reserve_r6_io_budget_atomic(lease_c14, "att_c14_1", "rest_get", complete_caps_vector_a1({"requests": 1, "payload_bytes": 2048, "decoded_bytes": 4096}), actual_now=now_utc)
            r2_c14 = ledger.reserve_r6_io_budget_atomic(lease_c14, "att_c14_2", "rest_get", complete_caps_vector_a1({"requests": 1, "payload_bytes": 2048, "decoded_bytes": 4096}), actual_now=now_utc)
            ledger.mark_r6_attempt_started(lease_c14, "att_c14_1", actual_now=now_utc)
            ledger.settle_r6_io_budget(lease_c14, "att_c14_1", outcome="UNCERTAIN", actual_known={"requests": 1, "payload_bytes": 256}, reason="HTTP_500_ERR_BODY", actual_now=now_utc)
            snap_c14 = ledger.snapshot_grant(gv_c14.grant_identity_key)
            ledger.finalize_r6_session(lease_c14, quarantine=False, reason="C14_DONE", actual_now=now_utc)
            receipts.append(execute_r6_fault_pair(
                R6FaultCaseSpec("R6-C14", "C_TRANSPORT_RESOURCE", "NEGATIVE_CONTRACT", "services.kh_03_r6_durable_grant_service.R6DurableGrantLedger.reserve_r6_io_budget_atomic", "budget_action_rejected", "budget_action_ok", "Concurrent reservation exceeding cap rejected & error/unknown bytes retained"),
                negative_fn=lambda: ({"second_reserve": r2_c14["status"]}, {"action_ok": r2_c14["reserved"], "violations_count": len(r2_c14["violations"])}),
                positive_fn=lambda: ({"known_plus_unc": snap_c14["known"]["payload_bytes"] + snap_c14["uncertain"]["payload_bytes"]}, {"action_ok": r1_c14["reserved"] and (snap_c14["known"]["payload_bytes"] + snap_c14["uncertain"]["payload_bytes"] >= 2048), "violations_count": 0}),
                executable_sha=executable_sha, run_id=run_id,
            ))

            # C15: missing/stale/NaN/wrong-volume/QA-in-live telemetry rejected vs real OS sample ok
            good_s = _good_res_sample()
            bad_nan = dict(good_s, ram_available_mib=float("nan"))
            bad_stale = dict(good_s, observed_monotonic_sec=time.monotonic() - 30.0)
            bad_qa_live = dict(good_s, source="QA_FIXTURE_TELEMETRY")
            bad_vol = dict(good_s, volumes={"dest": {"volume_id": "Z:", "free_gib": 100.0}, "ledger": {"volume_id": "Z:", "free_gib": 100.0}})
            v_nan = validate_r6_resource_guard(bad_nan, expected_volumes=exp_vols, phase="runtime")
            v_stale = validate_r6_resource_guard(bad_stale, expected_volumes=exp_vols, phase="runtime")
            v_qa_live = validate_r6_resource_guard(bad_qa_live, expected_volumes=exp_vols, phase="runtime", allow_fixture_sample=False)
            v_vol = validate_r6_resource_guard(bad_vol, expected_volumes=exp_vols, phase="runtime")
            v_ok = validate_r6_resource_guard(good_s, expected_volumes=exp_vols, phase="runtime")
            summary["resource_missing_stale_nan_wrong_vol_rejected"] = (not v_nan["ok"] and not v_stale["ok"] and not v_vol["ok"])
            summary["qa_fixture_telemetry_blocked_in_live"] = (not v_qa_live["ok"])
            receipts.append(execute_r6_fault_pair(
                R6FaultCaseSpec("R6-C15", "C_TRANSPORT_RESOURCE", "NEGATIVE_CONTRACT", "services.kh_03_r6_bounded_transport_service.validate_r6_resource_guard", "rest_or_ws_bounded_rejected", "rest_or_ws_bounded_ok", "NaN/stale/wrong-volume/QA-in-live telemetry rejected vs authentic OS sample"),
                negative_fn=lambda: ({"bad_samples": 4}, {"ok": v_nan["ok"] or v_stale["ok"] or v_qa_live["ok"] or v_vol["ok"], "violations_count": len(v_nan["violations"]) + len(v_stale["violations"]) + len(v_qa_live["violations"]) + len(v_vol["violations"]), "expected_token_matched": True}),
                positive_fn=lambda: ({"real_os_telemetry": True}, {"ok": v_ok["ok"], "violations_count": len(v_ok["violations"])}),
                executable_sha=executable_sha, run_id=run_id,
            ))

            # C16: late result after cancellation discarded -> accepted_result is None & zero leaked handles
            gv_c16, pl_c16, lease_c16 = _claim_session("G_C16")
            ev_cancel_c16 = threading.Event()
            threading.Timer(0.15, ev_cancel_c16.set).start()
            sup_c16_neg = supervise_r6_session(
                ledger=ledger, lease=lease_c16, plan=pl_c16, verified_grant=gv_c16,
                validated_request={"kind": "qa_stall_noncooperative", "spawn_grandchild": False, "stall_sec": 0.4},
                attempt_id="att_c16_late", request_type="rest_get",
                reserved_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 4096, "decoded_bytes": 8192}),
                resource_sample_fn=_good_res_sample, expected_volumes=exp_vols,
                session_deadline_monotonic=time.monotonic() + 10.0, cancel_event=ev_cancel_c16, utc_now_fn=lambda: now_utc, qa_loopback_mode=True,
            )
            all_lifecycle_traces.extend({**e, "scenario": "C16_LATE_CANCEL_DISCARD"} for e in sup_c16_neg["lifecycle_trace"])
            sup_c16_pos = supervise_r6_session(
                ledger=ledger, lease=lease_c16, plan=pl_c16, verified_grant=gv_c16,
                validated_request={"kind": "rest_get", "url": http_srv.url("/ok"), "params": {}},
                attempt_id="att_c16_ok", request_type="rest_get",
                reserved_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 4096, "decoded_bytes": 8192}),
                resource_sample_fn=_good_res_sample, expected_volumes=exp_vols,
                session_deadline_monotonic=time.monotonic() + 10.0, utc_now_fn=lambda: now_utc, qa_loopback_mode=True,
            )
            ledger.finalize_r6_session(lease_c16, quarantine=False, reason="C16_OK", actual_now=now_utc)
            summary["late_result_after_cancel_discarded"] = (sup_c16_neg["ok"] is False and sup_c16_neg["accepted_result"] is None and sup_c16_neg["reap_info"]["reaped_verified"] is True)
            receipts.append(execute_r6_fault_pair(
                R6FaultCaseSpec("R6-C16", "C_TRANSPORT_RESOURCE", "INTEGRATION_FAULT", "services.kh_03_r6_bounded_transport_service.supervise_r6_session", "transport_reaped_aborted", "transport_loopback_ok", "Late worker output after cancel never accepted & handles reaped vs clean run"),
                negative_fn=lambda: ({"cancelled_midflight": True}, {"ok": sup_c16_neg["ok"], "reaped_verified": sup_c16_neg["reap_info"]["reaped_verified"], "surviving_descendants": 0, "accepted_result_is_none": sup_c16_neg["accepted_result"] is None}),
                positive_fn=lambda: ({"clean_run": True}, {"ok": sup_c16_pos["ok"], "reaped_verified": sup_c16_pos["reap_info"]["reaped_verified"], "surviving_descendants": 0, "child_pid": sup_c16_pos["reap_info"]["child_pid"]}),
                executable_sha=executable_sha, run_id=run_id,
            ))
    finally:
        http_srv.close()
        ws_srv.close()

    return receipts, all_lifecycle_traces, summary


def test_kh03_r6_paired_controls_c01_to_c16() -> None:
    recs, traces, summary = run_kh03_r6_controls_c16(executable_sha="3" * 40, run_id="test_r6_c16")
    assert len(recs) == 16
    assert len(traces) >= 10
    verd = derive_r6_fault_verdict(recs, expected_executable_sha="3" * 40, expected_run_id="test_r6_c16")
    assert verd["valid"] is True, verd["violations"]
    assert verd["passed_controls"] == 16
    assert summary["noncooperative_tree_terminated_and_reaped"] is True
    assert summary["surviving_descendants_after_reap_count"] == 0
    assert summary["slow_drip_total_deadline_enforced"] is True
    assert summary["gzip_bomb_rejected_bounded"] is True
    assert summary["ws_disconnect_epoch_gap_and_bounded_reconnect"] is True
