"""
aurum-desk/backend/tests/test_kh03_r6_a1_budget_transport.py

AURUM DESK — KH-03.R6.A1 | GROUP I (I01..I15) WORKER ENVELOPE, CUMULATIVE REST/WS CAPS,
SETTLEMENT CONSERVATION, CANCEL/REAP, CROSS-PROCESS RESERVATION & CRASH RECOVERY PAIRED CONTROLS
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Mapping, Tuple

import psutil
import websockets

from services.kh_03_r6_a1_acceptance_service import (
    R6A1FaultCaseSpec,
    derive_r6_a1_fault_verdict,
    execute_r6_a1_fault_pair,
)
from services.kh_03_r6_a1_trust_service import (
    authorize_r6_a1_dispatch_request,
    build_r6_a1_worker_envelope,
    complete_caps_vector_a1,
)
from services.kh_03_r6_bounded_transport_service import (
    build_r6_bound_capture_plan,
    collect_real_r6_resource_sample,
    execute_r6_bounded_rest_get,
    execute_r6_bounded_ws_session,
    supervise_r6_session,
)
from services.kh_03_r6_durable_grant_service import (
    QA_ISOLATED_TRUST_NAMESPACE_R6,
    load_r6_owner_trust_root,
    open_r6_durable_ledger,
    provision_qa_isolated_trust_root,
    provision_r6_durable_ledger,
    sign_r6_grant_payload_with_ed25519,
    verify_r6_ledger_readonly,
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

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"


def _child_env() -> Dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{BACKEND_ROOT}{os.pathsep}{APP_ROOT}{os.pathsep}{APP_ROOT.parent}"
    env["PYTHONHASHSEED"] = "0"
    return env


def _mk_grant_and_plan(
    priv_k: Any,
    tr_qa: Any,
    reg: Any,
    *,
    gid: str,
    sid: str,
    dest: Path,
    now_dt: datetime,
    sha: str,
    rel_sha: str,
    caps_override: Mapping[str, int] | None = None,
) -> Tuple[Any, Any]:
    now_ms = int(now_dt.timestamp() * 1000)
    caps = {
        "connections": 4,
        "requests": 20,
        "retries": 4,
        "subscriptions": 4,
        "messages": 100,
        "payload_bytes": 131_072,
        "decoded_bytes": 262_144,
        "pages": 4,
        "disk_bytes": 1_048_576,
        "records": 500,
    }
    if caps_override:
        caps.update(dict(caps_override))
    gp = {
        "grant_id": gid,
        "issuer": "QA_ISOLATED_HARNESS",
        "trust_namespace": QA_ISOLATED_TRUST_NAMESPACE_R6,
        "issued_utc_ms": now_ms - 1000,
        "expires_utc_ms": now_ms + 600_000,
        "repo": "quocan050302/Build-app-crypto",
        "branch": MANDATORY_BRANCH_KH03_R6,
        "tested_executable_sha": sha,
        "release_manifest_sha256": rel_sha,
        "purpose": "QA_FIXTURE",
        "stage": "QA_LOOPBACK",
        "venue": APPROVED_VENUE_R6,
        "api_family": API_FAMILY_CLASSIC_V2,
        "symbol": APPROVED_SYMBOL_R6,
        "product_type": APPROVED_PRODUCT_TYPE_V2_R6,
        "allowed_endpoints": ["classic_v2_contracts", "classic_v2_history_candles", "classic_v2_ws_books1"],
        "allowed_ws_channels": ["books1"],
        "window_start_ms": 1794000000000,
        "window_end_ms": 1794003600000,
        "destination_store_id": sid,
        "destination_root": str(dest),
        "max_session_duration_sec": 120,
        "max_concurrency": 2,
        "caps": caps,
        "retention_policy": {"max_retention_days": 7, "git_commit_raw_payloads": False},
    }
    raw = sign_r6_grant_payload_with_ed25519(gp, priv_k)
    vg = verify_r6_owner_grant(
        raw, tr_qa, now_dt,
        expected_executable_sha=sha,
        expected_release_manifest_sha256=rel_sha,
        expected_store_id=sid,
        expected_stage="QA_LOOPBACK",
    )["verified_grant"]
    pl = build_r6_bound_capture_plan(vg, reg, {}, {"ok": True}, plan_id=f"pl_{gid}")["plan"]
    return vg, pl


def _start_ws_fixture(mode_holder: Dict[str, Any]):
    ready = threading.Event()
    port_box: List[int] = []
    stop_box: List[Any] = []
    loop_box: List[Any] = []
    conn_count = [0]

    async def _handler(ws: Any) -> None:
        conn_count[0] += 1
        mode = mode_holder.get("mode", "ok")
        if mode == "disconnect_loop":
            await ws.close()
            return
        try:
            raw_sub = await asyncio.wait_for(ws.recv(), timeout=2.0)
            sub_d = json.loads(raw_sub)
            await ws.send(json.dumps({"event": "subscribe", "arg": (sub_d.get("args") or [{}])[0]}))
            if mode == "oversized_frame":
                await ws.send(json.dumps({"action": "snapshot", "data": [{"pad": "X" * 4096}]}))
                return
            ts0 = 1794000000000
            await ws.send(json.dumps({"action": "snapshot", "data": [{"bids": [["2650.10", "5"]], "asks": [["2650.20", "5"]], "ts": str(ts0)}]}))
            await ws.send(json.dumps({"action": "update", "data": [{"bids": [["2650.15", "4"]], "asks": [["2650.25", "4"]], "ts": str(ts0 + 1000)}]}))
            await ws.close()
        except Exception:
            pass

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop_box.append(loop)
        stop_ev = asyncio.Event()
        stop_box.append(stop_ev)

        async def _serve() -> None:
            async with websockets.serve(_handler, "127.0.0.1", 0, max_size=2 * 1024 * 1024) as srv:
                port_box.append(int(srv.sockets[0].getsockname()[1]))
                ready.set()
                await stop_ev.wait()

        loop.run_until_complete(_serve())
        loop.close()

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    ready.wait(timeout=5.0)
    return port_box[0], conn_count, lambda: (loop_box[0].call_soon_threadsafe(stop_box[0].set), th.join(timeout=3.0))


def run_kh03_r6_a1_controls_i01_to_i15(
    *,
    executable_sha: str,
    run_id: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    now_dt = datetime(2026, 11, 26, 2, 10, 0, tzinfo=timezone.utc)
    rel_sha = "1" * 64
    reg = build_r6_source_contract_registry()
    receipts: List[Dict[str, Any]] = []
    lifecycle_traces: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {}

    http_mode = {"mode": "ok", "hits": 0}
    body_ok = b'{"code":"00000","data":[{"symbol":"XAUUSDT"}]}'
    body_err80 = b"E" * 80
    body_gzip_bomb = gzip.compress(b"Z" * 16_384)
    body_gzip_ok = gzip.compress(body_ok)

    class _IHttp(BaseHTTPRequestHandler):
        def log_message(self, *a): return
        def do_GET(self):
            http_mode["hits"] += 1
            m = http_mode["mode"]
            if m == "ok":
                self.send_response(200)
                self.send_header("Content-Length", str(len(body_ok)))
                self.end_headers()
                self.wfile.write(body_ok)
            elif m == "retry_503_then_200":
                if http_mode["hits"] == 1:
                    self.send_response(503)
                    self.send_header("Retry-After", "0")
                    self.send_header("Content-Length", str(len(body_err80)))
                    self.end_headers()
                    self.wfile.write(body_err80)
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body_err80)))
                    self.end_headers()
                    self.wfile.write(body_err80)
            elif m == "always_503":
                self.send_response(503)
                self.send_header("Retry-After", "0")
                self.send_header("Content-Length", "10")
                self.end_headers()
                self.wfile.write(b"0123456789")
            elif m == "retry_after_30":
                self.send_response(429)
                self.send_header("Retry-After", "30")
                self.send_header("Content-Length", "4")
                self.end_headers()
                self.wfile.write(b"wait")
            elif m == "chunked_overflow":
                self.send_response(200)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                chunk = b"A" * 150
                self.wfile.write(f"{len(chunk):x}\r\n".encode("ascii") + chunk + b"\r\n0\r\n\r\n")
            elif m == "gzip_bomb":
                self.send_response(200)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body_gzip_bomb)))
                self.end_headers()
                self.wfile.write(body_gzip_bomb)
            elif m == "gzip_ok":
                self.send_response(200)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body_gzip_ok)))
                self.end_headers()
                self.wfile.write(body_gzip_ok)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _IHttp)
    hport = int(srv.server_address[1])
    hth = threading.Thread(target=srv.serve_forever, daemon=True)
    hth.start()

    ws_mode = {"mode": "ok"}
    wport, ws_conns, stop_ws = _start_ws_fixture(ws_mode)

    try:
        with tempfile.TemporaryDirectory(prefix="a1_grp_i_") as td:
            root = Path(td)
            dest = root / "dest"
            dest.mkdir()
            sid = "a1_i_store"
            tr_p, priv_k, _ = provision_qa_isolated_trust_root(root / "trust", store_id=sid)
            tr_qa = load_r6_owner_trust_root(tr_p, expected_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6)["trust_root"]
            sroot = root / "ledger"
            provision_r6_durable_ledger(sroot, store_id=sid, trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, now_utc=now_dt)
            led = open_r6_durable_ledger({"ledger_store_root": str(sroot), "store_id": sid}, sid, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_dt)["ledger"]

            def _res_sample() -> Dict[str, Any]:
                return collect_real_r6_resource_sample({"dest": dest}, active_workers=1)

            vg_i, pl_i = _mk_grant_and_plan(priv_k, tr_qa, reg, gid="G_I_MAIN", sid=sid, dest=dest, now_dt=now_dt, sha=executable_sha, rel_sha=rel_sha)
            lease_i = led.claim_r6_grant_session_atomic(vg_i, pl_i, "sess_i_main", actual_now=now_dt)["lease"]

            # I01: Child request override parent payload/decoded cap rejected (both builder & child CLI!)
            r_res_i01 = led.reserve_r6_io_budget_atomic(
                lease_i, "att_i01", "rest_get",
                complete_caps_vector_a1({"requests": 1, "payload_bytes": 256, "decoded_bytes": 512}),
                request_digest="d" * 64, actual_now=now_dt,
            )["reservation_receipt"]

            def _i01_neg():
                bad_env = build_r6_a1_worker_envelope(
                    lease=lease_i, plan=pl_i, reservation_receipt=r_res_i01,
                    request_payload={"kind": "rest_get", "url": f"http://127.0.0.1:{hport}/api/v2/mix/market/contracts", "params": {}, "max_payload_bytes": 999999},
                    request_digest="d" * 64, remaining_deadline_sec=5.0, parent_abs_deadline_monotonic=time.monotonic() + 5.0, qa_loopback_mode=True,
                )
                # Also hit --child-worker CLI directly with an external target & override
                raw_bad_cli = json.dumps({"envelope_schema": "kh_03_r6_a1_worker_envelope_v1", "request": {"kind": "rest_get", "url": "https://api.bitget.com/api/v2/mix/market/contracts", "max_payload_bytes": 999999}})
                p_ch = subprocess.run([sys.executable, "-m", "services.kh_03_r6_bounded_transport_service", "--child-worker"], input=raw_bad_cli, capture_output=True, text=True, env=_child_env(), timeout=20)
                ch_d = json.loads(p_ch.stdout) if p_ch.stdout.strip() else {"ok": False}
                return (
                    {"attack": "child_cap_override_and_direct_cli_external"},
                    {"allowed": bool(bad_env["valid"]) or bool(ch_d.get("ok")), "violations_count": len(bad_env["violations"]) + (1 if p_ch.returncode != 0 else 0)},
                )

            def _i01_pos():
                good_env = build_r6_a1_worker_envelope(
                    lease=lease_i, plan=pl_i, reservation_receipt=r_res_i01,
                    request_payload={"kind": "rest_get", "url": f"http://127.0.0.1:{hport}/api/v2/mix/market/contracts", "params": {}},
                    request_digest="d" * 64, remaining_deadline_sec=5.0, parent_abs_deadline_monotonic=time.monotonic() + 5.0, qa_loopback_mode=True,
                )
                return ({"check": "clean_parent_authoritative_envelope"}, {"allowed": bool(good_env["valid"]), "violations_count": len(good_env["violations"])})

            r_i01 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("I01", "I", "BASELINE_BUG_RED", "services.kh_03_r6_a1_trust_service.build_r6_a1_worker_envelope", "a1_rejected", "a1_accepted", "Child request cap override rejected by parent builder and child CLI"),
                negative_fn=_i01_neg, positive_fn=_i01_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_i01)
            summary["child_cap_override_rejected"] = (r_i01["status"] == "PASS")
            summary["direct_child_cli_external_blocked"] = (r_i01["status"] == "PASS")

            # I02: Child request deadline override / malformed numeric / missing parent_limits fields
            def _i02_neg():
                bad1 = build_r6_a1_worker_envelope(
                    lease=lease_i, plan=pl_i, reservation_receipt=r_res_i01,
                    request_payload={"kind": "rest_get", "url": f"http://127.0.0.1:{hport}/api/v2/mix/market/contracts", "remaining_deadline_sec": 9999.0},
                    request_digest="d" * 64, remaining_deadline_sec=5.0, parent_abs_deadline_monotonic=time.monotonic() + 5.0, qa_loopback_mode=True,
                )
                # Direct CLI with missing parent_limits keys & NaN
                raw_miss = json.dumps({"envelope_schema": "kh_03_r6_a1_worker_envelope_v1", "attempt_id": "a", "session_id": "s", "lease_id": "l", "plan_digest": "p", "request_digest": "r", "policy_namespace": "QA_LOOPBACK_ONLY", "qa_loopback_mode": True, "external_capture_permitted": False, "parent_abs_deadline_monotonic": 100.0, "parent_limits": {"remaining_deadline_sec": 5.0}, "request": {"kind": "rest_get", "url": f"http://127.0.0.1:{hport}/ok"}, "envelope_digest": "x"})
                p_ch = subprocess.run([sys.executable, "-m", "services.kh_03_r6_bounded_transport_service", "--child-worker"], input=raw_miss, capture_output=True, text=True, env=_child_env(), timeout=20)
                return ({"attack": "deadline_override_and_missing_parent_limits"}, {"allowed": bool(bad1["valid"]) or (p_ch.returncode == 0), "violations_count": len(bad1["violations"]) + (1 if p_ch.returncode != 0 else 0)})

            def _i02_pos():
                good = build_r6_a1_worker_envelope(
                    lease=lease_i, plan=pl_i, reservation_receipt=r_res_i01,
                    request_payload={"kind": "rest_get", "url": f"http://127.0.0.1:{hport}/api/v2/mix/market/contracts", "params": {}},
                    request_digest="d" * 64, remaining_deadline_sec=5.0, parent_abs_deadline_monotonic=time.monotonic() + 5.0, qa_loopback_mode=True,
                )
                return ({"check": "complete_typed_parent_limits"}, {"allowed": bool(good["valid"]), "violations_count": len(good["violations"])})

            r_i02 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("I02", "I", "NEGATIVE_CONTRACT", "services.kh_03_r6_a1_trust_service.validate_r6_a1_worker_envelope_strict", "a1_rejected", "a1_accepted", "Child deadline override and missing parent_limits rejected"),
                negative_fn=_i02_neg, positive_fn=_i02_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_i02)
            summary["child_deadline_override_and_missing_fields_rejected"] = (r_i02["status"] == "PASS")

            # I03: Zero request reservation rejected + retry over cap stopped with zero extra socket!
            def _i03_neg():
                r_zero = led.reserve_r6_io_budget_atomic(
                    lease_i, "att_i03_zero", "rest_get",
                    complete_caps_vector_a1({"requests": 0, "payload_bytes": 1024, "decoded_bytes": 2048}),
                    actual_now=now_dt,
                )
                http_mode["mode"] = "always_503"
                http_mode["hits"] = 0
                r_rest = execute_r6_bounded_rest_get(
                    f"http://127.0.0.1:{hport}/api/v2/mix/market/contracts", {},
                    remaining_deadline_sec=3.0, max_payload_bytes=512, max_decoded_bytes=512,
                    max_retries=0, max_requests=1, strict_reservation_byte_cap=512, qa_loopback_mode=True,
                )
                return (
                    {"attack": "zero_request_reserve_and_retry_over_cap"},
                    {"allowed": bool(r_zero.get("reserved")) or bool(r_rest.get("ok")) or http_mode["hits"] > 1, "violations_count": len(r_zero.get("violations") or [r_zero.get("status")]) + len(r_rest.get("violations") or []), "http_hits": http_mode["hits"]},
                )

            def _i03_pos():
                http_mode["mode"] = "ok"
                http_mode["hits"] = 0
                r_ok = execute_r6_bounded_rest_get(
                    f"http://127.0.0.1:{hport}/api/v2/mix/market/contracts", {},
                    remaining_deadline_sec=3.0, max_payload_bytes=512, max_decoded_bytes=512,
                    max_retries=0, max_requests=1, strict_reservation_byte_cap=512, qa_loopback_mode=True,
                )
                return ({"check": "bounded_1_request_ok"}, {"allowed": bool(r_ok.get("ok")), "violations_count": len(r_ok.get("violations") or [])})

            r_i03 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("I03", "I", "NEGATIVE_CONTRACT", "services.kh_03_r6_durable_grant_service.R6DurableGrantLedger.reserve_r6_io_budget_atomic", "a1_rejected", "a1_accepted", "Zero request reservation rejected and retry cap honored"),
                negative_fn=_i03_neg, positive_fn=_i03_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_i03)
            summary["zero_request_or_missing_dim_reservation_rejected"] = (r_i03["status"] == "PASS")
            summary["retry_over_cap_rejected"] = (r_i03["status"] == "PASS")
            summary["reservation_receipt_verified"] = (r_res_i01 is not None)

            # I04: HTTP error body + retry cumulative bytes + chunked sentinel cap never exceeds reservation!
            def _i04_neg():
                http_mode["mode"] = "retry_503_then_200"
                http_mode["hits"] = 0
                r_cum = execute_r6_bounded_rest_get(
                    f"http://127.0.0.1:{hport}/api/v2/mix/market/contracts", {},
                    remaining_deadline_sec=3.0, max_payload_bytes=120, max_decoded_bytes=120,
                    max_retries=2, max_requests=3, strict_reservation_byte_cap=120, qa_loopback_mode=True,
                )
                http_mode["mode"] = "chunked_overflow"
                http_mode["hits"] = 0
                r_chk = execute_r6_bounded_rest_get(
                    f"http://127.0.0.1:{hport}/api/v2/mix/market/contracts", {},
                    remaining_deadline_sec=3.0, max_payload_bytes=100, max_decoded_bytes=100,
                    max_retries=0, max_requests=1, strict_reservation_byte_cap=100, qa_loopback_mode=True,
                )
                leaked = (int(r_cum.get("payload_bytes_received", 999)) > 120) or (int(r_chk.get("payload_bytes_received", 999)) > 100)
                return (
                    {"attack": "cumulative_retry_error_bytes_and_chunked_sentinel_cap"},
                    {
                        "allowed": bool(r_cum.get("ok")) or bool(r_chk.get("ok")) or leaked,
                        "violations_count": len(r_cum.get("violations") or []) + len(r_chk.get("violations") or []),
                        "cum_bytes": r_cum.get("payload_bytes_received"),
                        "chk_bytes": r_chk.get("payload_bytes_received"),
                    },
                )

            def _i04_pos():
                http_mode["mode"] = "retry_503_then_200"
                http_mode["hits"] = 0
                r_ok = execute_r6_bounded_rest_get(
                    f"http://127.0.0.1:{hport}/api/v2/mix/market/contracts", {},
                    remaining_deadline_sec=3.0, max_payload_bytes=256, max_decoded_bytes=256,
                    max_retries=2, max_requests=3, strict_reservation_byte_cap=256, qa_loopback_mode=True,
                )
                return (
                    {"check": "cumulative_within_256_cap"},
                    {"allowed": bool(r_ok.get("ok")) and int(r_ok.get("payload_bytes_received", 0)) == 160, "violations_count": len(r_ok.get("violations") or [])},
                )

            r_i04 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("I04", "I", "INTEGRATION_FAULT", "services.kh_03_r6_bounded_transport_service.execute_r6_bounded_rest_get", "a1_rejected", "a1_accepted", "HTTP error+retry cumulative bytes and sentinel never exceed reservation"),
                negative_fn=_i04_neg, positive_fn=_i04_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_i04)
            summary["rest_error_and_retry_cumulative_bytes_bounded"] = (r_i04["status"] == "PASS")
            summary["sentinel_never_reads_beyond_reservation"] = (r_i04["status"] == "PASS")

            # I05: Retry-After beyond remaining deadline -> abort immediately without sleeping/retrying
            def _i05_neg():
                http_mode["mode"] = "retry_after_30"
                http_mode["hits"] = 0
                r_ra = execute_r6_bounded_rest_get(
                    f"http://127.0.0.1:{hport}/api/v2/mix/market/contracts", {},
                    remaining_deadline_sec=1.5, max_payload_bytes=512, max_decoded_bytes=512,
                    max_retries=2, max_requests=3, strict_reservation_byte_cap=512, qa_loopback_mode=True,
                )
                return (
                    {"attack": "retry_after_30s_exceeds_1p5s_budget"},
                    {"allowed": bool(r_ra.get("ok")) or http_mode["hits"] > 1, "violations_count": len(r_ra.get("violations") or []), "status": r_ra.get("status")},
                )

            def _i05_pos():
                http_mode["mode"] = "retry_503_then_200"
                http_mode["hits"] = 0
                r_ok = execute_r6_bounded_rest_get(
                    f"http://127.0.0.1:{hport}/api/v2/mix/market/contracts", {},
                    remaining_deadline_sec=3.0, max_payload_bytes=512, max_decoded_bytes=512,
                    max_retries=2, max_requests=3, strict_reservation_byte_cap=512, qa_loopback_mode=True,
                )
                return ({"check": "retry_after_0s_within_budget"}, {"allowed": bool(r_ok.get("ok")), "violations_count": len(r_ok.get("violations") or [])})

            r_i05 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("I05", "I", "INTEGRATION_FAULT", "services.kh_03_r6_bounded_transport_service.execute_r6_bounded_rest_get", "a1_rejected", "a1_accepted", "Retry-After exceeding remaining budget aborts immediately"),
                negative_fn=_i05_neg, positive_fn=_i05_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_i05)
            summary["retry_after_beyond_budget_aborted"] = (r_i05["status"] == "PASS")

            # I06: WS reconnect/sub/conn counts <= reservation on disconnect
            sub_pl = {"op": "subscribe", "args": [{"instType": APPROVED_PRODUCT_TYPE_V2_R6, "channel": "books1", "instId": APPROVED_SYMBOL_R6}]}

            def _i06_neg():
                ws_mode["mode"] = "disconnect_loop"
                ws_conns[0] = 0
                r_ws = execute_r6_bounded_ws_session(
                    f"ws://127.0.0.1:{wport}/v2/ws/public", sub_pl,
                    remaining_deadline_sec=3.0, max_frame_bytes=4096, max_messages=10,
                    max_payload_bytes=8192, max_queue_items=10, max_queue_bytes=8192,
                    max_reconnects=0, max_connections=1, max_subscriptions=1, max_decoded_bytes=8192, qa_loopback_mode=True,
                )
                over = int(r_ws.get("connections_opened", 99)) > 1 or int(r_ws.get("reconnects_used", 99)) > 0 or ws_conns[0] > 1
                return ({"attack": "ws_disconnect_reconnect_cap"}, {"allowed": bool(r_ws.get("ok")) or over, "violations_count": len(r_ws.get("violations") or [r_ws.get("status")]), "conns": r_ws.get("connections_opened")})

            def _i06_pos():
                ws_mode["mode"] = "ok"
                ws_conns[0] = 0
                r_ws = execute_r6_bounded_ws_session(
                    f"ws://127.0.0.1:{wport}/v2/ws/public", sub_pl,
                    remaining_deadline_sec=3.0, max_frame_bytes=4096, max_messages=10,
                    max_payload_bytes=8192, max_queue_items=10, max_queue_bytes=8192,
                    max_reconnects=1, max_connections=2, max_subscriptions=2, max_decoded_bytes=8192, qa_loopback_mode=True,
                )
                return ({"check": "ws_clean_bounded_session"}, {"allowed": bool(r_ws.get("ok")), "violations_count": len(r_ws.get("violations") or [])})

            r_i06 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("I06", "I", "INTEGRATION_FAULT", "services.kh_03_r6_bounded_transport_service.execute_r6_bounded_ws_session", "a1_rejected", "a1_accepted", "WS connections/subscriptions/reconnects strictly <= reservation"),
                negative_fn=_i06_neg, positive_fn=_i06_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_i06)
            summary["ws_reconnect_sub_conn_bounded_by_reservation"] = (r_i06["status"] == "PASS")

            # I07: WS oversized frame + gzip decompression bomb rejected fail-closed
            def _i07_neg():
                ws_mode["mode"] = "oversized_frame"
                r_ws = execute_r6_bounded_ws_session(
                    f"ws://127.0.0.1:{wport}/v2/ws/public", sub_pl,
                    remaining_deadline_sec=3.0, max_frame_bytes=512, max_messages=10,
                    max_payload_bytes=8192, max_queue_items=10, max_queue_bytes=8192,
                    max_reconnects=0, max_connections=1, max_subscriptions=1, max_decoded_bytes=8192, qa_loopback_mode=True,
                )
                http_mode["mode"] = "gzip_bomb"
                r_gz = execute_r6_bounded_rest_get(
                    f"http://127.0.0.1:{hport}/api/v2/mix/market/contracts", {},
                    remaining_deadline_sec=3.0, max_payload_bytes=4096, max_decoded_bytes=1024,
                    max_retries=0, max_requests=1, strict_reservation_byte_cap=4096, qa_loopback_mode=True,
                )
                return (
                    {"attack": "ws_oversized_frame_and_gzip_bomb"},
                    {"allowed": bool(r_ws.get("ok")) or bool(r_gz.get("ok")), "violations_count": len(r_ws.get("violations") or []) + len(r_gz.get("violations") or [])},
                )

            def _i07_pos():
                http_mode["mode"] = "gzip_ok"
                r_gz = execute_r6_bounded_rest_get(
                    f"http://127.0.0.1:{hport}/api/v2/mix/market/contracts", {},
                    remaining_deadline_sec=3.0, max_payload_bytes=4096, max_decoded_bytes=4096,
                    max_retries=0, max_requests=1, strict_reservation_byte_cap=4096, qa_loopback_mode=True,
                )
                return ({"check": "clean_gzip_within_decoded_cap"}, {"allowed": bool(r_gz.get("ok")), "violations_count": len(r_gz.get("violations") or [])})

            r_i07 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("I07", "I", "INTEGRATION_FAULT", "services.kh_03_r6_bounded_transport_service.execute_r6_bounded_ws_session", "a1_rejected", "a1_accepted", "WS frame/queue cap and gzip decompression bomb fail closed"),
                negative_fn=_i07_neg, positive_fn=_i07_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_i07)
            summary["ws_frame_queue_message_caps_enforced"] = (r_i07["status"] == "PASS")

            # I08: COMPLETED settlement missing dimensions or invalid types rejected
            led.reserve_r6_io_budget_atomic(lease_i, "att_i08", "rest_get", complete_caps_vector_a1({"requests": 1, "payload_bytes": 512, "decoded_bytes": 512}), actual_now=now_dt)
            led.mark_r6_attempt_started(lease_i, "att_i08", actual_now=now_dt)

            def _i08_neg():
                s_miss = led.settle_r6_io_budget(lease_i, "att_i08", outcome="COMPLETED", actual_known={"requests": 1}, actual_now=now_dt)
                s_bool = led.settle_r6_io_budget(lease_i, "att_i08", outcome="COMPLETED", actual_known={**complete_caps_vector_a1(), "requests": True}, actual_now=now_dt)  # type: ignore[dict-item]
                return ({"attack": "completed_missing_dims_and_bool_int"}, {"allowed": bool(s_miss.get("settled")) or bool(s_bool.get("settled")), "violations_count": len(s_miss.get("violations") or [s_miss.get("status")]) + len(s_bool.get("violations") or [s_bool.get("status")])})

            def _i08_pos():
                s_ok = led.settle_r6_io_budget(lease_i, "att_i08", outcome="COMPLETED", actual_known=complete_caps_vector_a1({"requests": 1, "payload_bytes": 100, "decoded_bytes": 100}), actual_now=now_dt)
                return ({"check": "complete_10dim_settlement"}, {"allowed": bool(s_ok.get("settled")) and s_ok.get("settlement_receipt") is not None, "violations_count": len(s_ok.get("violations") or [])})

            r_i08 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("I08", "I", "NEGATIVE_CONTRACT", "services.kh_03_r6_durable_grant_service.R6DurableGrantLedger.settle_r6_io_budget", "a1_rejected", "a1_accepted", "Completed settlement missing dimensions or bad types rejected"),
                negative_fn=_i08_neg, positive_fn=_i08_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_i08)
            summary["completed_missing_dim_rejected"] = (r_i08["status"] == "PASS")

            # I09: Measured actual > reserved -> durable quarantine WITHOUT clamping (isolated ledger so I10+ stay clean)!
            sroot09 = root / "ledger09"
            provision_r6_durable_ledger(sroot09, store_id=sid, trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, now_utc=now_dt)
            led09 = open_r6_durable_ledger({"ledger_store_root": str(sroot09), "store_id": sid}, sid, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_dt)["ledger"]
            vg_i09, pl_i09 = _mk_grant_and_plan(priv_k, tr_qa, reg, gid="G_I09", sid=sid, dest=dest, now_dt=now_dt, sha=executable_sha, rel_sha=rel_sha)
            lease_i09 = led09.claim_r6_grant_session_atomic(vg_i09, pl_i09, "sess_i09", actual_now=now_dt)["lease"]
            led09.reserve_r6_io_budget_atomic(lease_i09, "att_i09_ov", "rest_get", complete_caps_vector_a1({"requests": 1, "payload_bytes": 50, "decoded_bytes": 50}), actual_now=now_dt)
            led09.mark_r6_attempt_started(lease_i09, "att_i09_ov", actual_now=now_dt)

            def _i09_neg():
                s_ov = led09.settle_r6_io_budget(
                    lease_i09, "att_i09_ov", outcome="COMPLETED",
                    actual_known=complete_caps_vector_a1({"requests": 1, "payload_bytes": 5000, "decoded_bytes": 5000}),
                    actual_now=now_dt,
                )
                snap = led09.snapshot_grant(vg_i09.grant_identity_key)
                return (
                    {"attack": "actual_5000_exceeds_reserved_50"},
                    {
                        "settled": bool(s_ov.get("settled")),
                        "quarantined": bool(s_ov.get("quarantined")) and snap["state"] == "QUARANTINED",
                        "clamped": int(snap["known"]["payload_bytes"]) != 5000,
                        "violations_count": len(s_ov.get("violations") or []),
                        "recorded_known_bytes": int(snap["known"]["payload_bytes"]),
                    },
                )

            def _i09_pos():
                vg_p, pl_p = _mk_grant_and_plan(priv_k, tr_qa, reg, gid="G_I09_POS", sid=sid, dest=dest, now_dt=now_dt, sha=executable_sha, rel_sha=rel_sha)
                l_p = led.claim_r6_grant_session_atomic(vg_p, pl_p, "sess_i09_p", actual_now=now_dt)["lease"]
                led.reserve_r6_io_budget_atomic(l_p, "att_i09_p", "rest_get", complete_caps_vector_a1({"requests": 1, "payload_bytes": 100, "decoded_bytes": 100}), actual_now=now_dt)
                led.mark_r6_attempt_started(l_p, "att_i09_p", actual_now=now_dt)
                s_ok = led.settle_r6_io_budget(l_p, "att_i09_p", outcome="COMPLETED", actual_known=complete_caps_vector_a1({"requests": 1, "payload_bytes": 80, "decoded_bytes": 80}), actual_now=now_dt)
                return (
                    {"check": "actual_80_le_reserved_100"},
                    {"settled": bool(s_ok.get("settled")), "quarantined": bool(s_ok.get("quarantined")), "receipt_issued": s_ok.get("settlement_receipt") is not None, "violations_count": len(s_ok.get("violations") or [])},
                )

            r_i09 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("I09", "I", "BASELINE_BUG_RED", "services.kh_03_r6_durable_grant_service.R6DurableGrantLedger.settle_r6_io_budget", "a1_overrun_quarantined", "a1_settlement_conserved", "Overrun settlement quarantined durably without clamping"),
                negative_fn=_i09_neg, positive_fn=_i09_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_i09)
            summary["overrun_quarantined_without_clamping"] = (r_i09["status"] == "PASS")

            # I10: Supervisor rejects acceptance when settlement returns False or raises exception!
            http_mode["mode"] = "ok"

            class _FailSettleLedgerProxy:
                def __init__(self, real_led: Any):
                    self._real = real_led
                def __getattr__(self, name: str):
                    return getattr(self._real, name)
                def settle_r6_io_budget(self, *a, **kw):
                    return {"settled": False, "status": "FORCED_SETTLEMENT_ERROR", "settlement_receipt": None, "violations": ["INJECTED_SETTLEMENT_FALSE"]}

            def _i10_neg():
                vg_10, pl_10 = _mk_grant_and_plan(priv_k, tr_qa, reg, gid="G_I10_NEG", sid=sid, dest=dest, now_dt=now_dt, sha=executable_sha, rel_sha=rel_sha)
                l_10 = led.claim_r6_grant_session_atomic(vg_10, pl_10, "sess_i10_neg", actual_now=now_dt)["lease"]
                auth = authorize_r6_a1_dispatch_request(
                    plan=pl_10, verified_grant=vg_10, lease=l_10, registry=reg,
                    endpoint_id="classic_v2_contracts", attempt_id="att_i10_neg", partition="QA_FIXTURE",
                    requested_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 1024, "decoded_bytes": 2048}),
                    deadline_monotonic=time.monotonic() + 15.0,
                    params={"productType": APPROVED_PRODUCT_TYPE_V2_R6, "symbol": APPROVED_SYMBOL_R6},
                    qa_loopback_mode=True, qa_loopback_url=f"http://127.0.0.1:{hport}/api/v2/mix/market/contracts", actual_now=now_dt,
                )["authorized_request"]
                sup = supervise_r6_session(
                    ledger=_FailSettleLedgerProxy(led), lease=l_10, plan=pl_10, verified_grant=vg_10,  # type: ignore[arg-type]
                    validated_request={"kind": "rest_get", "url": auth.dispatch_url, "params": dict(auth.params)},
                    attempt_id="att_i10_neg", request_type="rest_get",
                    reserved_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 1024, "decoded_bytes": 2048}),
                    resource_sample_fn=_res_sample, expected_volumes={"dest": dest},
                    session_deadline_monotonic=time.monotonic() + 15.0, cancel_event=threading.Event(), utc_now_fn=lambda: now_dt,
                    qa_loopback_mode=True, allow_fixture_resource_sample=True, registry=reg, authorized_request=auth,
                    run_id=run_id, partition="QA_FIXTURE", endpoint_id="classic_v2_contracts",
                    contract_digest=reg.endpoints["classic_v2_contracts"].content_digest,
                )
                lifecycle_traces.extend(sup.get("lifecycle_trace") or ())
                reap = sup.get("reap_info") or {}
                return (
                    {"attack": "settlement_returns_false"},
                    {
                        "ok": bool(sup.get("ok")),
                        "reaped_verified": bool(reap.get("reaped_verified")),
                        "surviving_descendants": len(reap.get("surviving_descendant_pids") or ()),
                        "trusted_result_is_none": sup.get("trusted_result") is None,
                    },
                )

            i10_pos_seq = [0]

            def _i10_pos():
                i10_pos_seq[0] += 1
                idx_p = i10_pos_seq[0]
                vg_10p, pl_10p = _mk_grant_and_plan(priv_k, tr_qa, reg, gid=f"G_I10_POS_{idx_p}", sid=sid, dest=dest, now_dt=now_dt, sha=executable_sha, rel_sha=rel_sha)
                l_10p = led.claim_r6_grant_session_atomic(vg_10p, pl_10p, f"sess_i10_pos_{idx_p}", actual_now=now_dt)["lease"]
                auth = authorize_r6_a1_dispatch_request(
                    plan=pl_10p, verified_grant=vg_10p, lease=l_10p, registry=reg,
                    endpoint_id="classic_v2_contracts", attempt_id=f"att_i10_pos_{idx_p}", partition="QA_FIXTURE",
                    requested_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 1024, "decoded_bytes": 2048}),
                    deadline_monotonic=time.monotonic() + 15.0,
                    params={"productType": APPROVED_PRODUCT_TYPE_V2_R6, "symbol": APPROVED_SYMBOL_R6},
                    qa_loopback_mode=True, qa_loopback_url=f"http://127.0.0.1:{hport}/api/v2/mix/market/contracts", actual_now=now_dt,
                )["authorized_request"]
                sup = supervise_r6_session(
                    ledger=led, lease=l_10p, plan=pl_10p, verified_grant=vg_10p,
                    validated_request={"kind": "rest_get", "url": auth.dispatch_url, "params": dict(auth.params)},
                    attempt_id=f"att_i10_pos_{idx_p}", request_type="rest_get",
                    reserved_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 1024, "decoded_bytes": 2048}),
                    resource_sample_fn=_res_sample, expected_volumes={"dest": dest},
                    session_deadline_monotonic=time.monotonic() + 15.0, cancel_event=threading.Event(), utc_now_fn=lambda: now_dt,
                    qa_loopback_mode=True, allow_fixture_resource_sample=True, registry=reg, authorized_request=auth,
                    run_id=run_id, partition="QA_FIXTURE", endpoint_id="classic_v2_contracts",
                    contract_digest=reg.endpoints["classic_v2_contracts"].content_digest,
                )
                lifecycle_traces.extend(sup.get("lifecycle_trace") or ())
                reap = sup.get("reap_info") or {}
                return (
                    {"check": "clean_settlement_issues_trusted_result"},
                    {
                        "ok": bool(sup.get("ok")),
                        "reaped_verified": bool(reap.get("reaped_verified")),
                        "surviving_descendants": len(reap.get("surviving_descendant_pids") or ()),
                        "trusted_result_issued": sup.get("trusted_result") is not None,
                        "settlement_receipt_issued": sup.get("settlement_receipt") is not None,
                        "child_pid": int(reap.get("child_pid") or 0),
                    },
                )

            r_i10 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("I10", "I", "INTEGRATION_FAULT", "services.kh_03_r6_bounded_transport_service.supervise_r6_session", "a1_worker_reaped_aborted", "a1_worker_clean_trusted", "Supervisor refuses acceptance when settlement fails"),
                negative_fn=_i10_neg, positive_fn=_i10_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_i10)
            summary["supervisor_rejects_on_settlement_failure_or_exception"] = (r_i10["status"] == "PASS")

            # I11: Cancel AFTER child exits 0 and BEFORE parent acceptance (`post_exit_hook`) -> late result discarded!
            def _i11_neg():
                vg_11, pl_11 = _mk_grant_and_plan(priv_k, tr_qa, reg, gid="G_I11_NEG", sid=sid, dest=dest, now_dt=now_dt, sha=executable_sha, rel_sha=rel_sha)
                l_11 = led.claim_r6_grant_session_atomic(vg_11, pl_11, "sess_i11_neg", actual_now=now_dt)["lease"]
                ev_c = threading.Event()
                auth = authorize_r6_a1_dispatch_request(
                    plan=pl_11, verified_grant=vg_11, lease=l_11, registry=reg,
                    endpoint_id="classic_v2_contracts", attempt_id="att_i11_neg", partition="QA_FIXTURE",
                    requested_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 1024, "decoded_bytes": 2048}),
                    deadline_monotonic=time.monotonic() + 15.0,
                    params={"productType": APPROVED_PRODUCT_TYPE_V2_R6, "symbol": APPROVED_SYMBOL_R6},
                    qa_loopback_mode=True, qa_loopback_url=f"http://127.0.0.1:{hport}/api/v2/mix/market/contracts", actual_now=now_dt,
                )["authorized_request"]
                sup = supervise_r6_session(
                    ledger=led, lease=l_11, plan=pl_11, verified_grant=vg_11,
                    validated_request={"kind": "rest_get", "url": auth.dispatch_url, "params": dict(auth.params)},
                    attempt_id="att_i11_neg", request_type="rest_get",
                    reserved_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 1024, "decoded_bytes": 2048}),
                    resource_sample_fn=_res_sample, expected_volumes={"dest": dest},
                    session_deadline_monotonic=time.monotonic() + 15.0, cancel_event=ev_c, utc_now_fn=lambda: now_dt,
                    qa_loopback_mode=True, allow_fixture_resource_sample=True, registry=reg, authorized_request=auth,
                    run_id=run_id, partition="QA_FIXTURE", endpoint_id="classic_v2_contracts",
                    contract_digest=reg.endpoints["classic_v2_contracts"].content_digest,
                    post_exit_hook=lambda: ev_c.set(),
                )
                lifecycle_traces.extend(sup.get("lifecycle_trace") or ())
                reap = sup.get("reap_info") or {}
                return (
                    {"attack": "cancel_triggered_post_child_exit_before_accept"},
                    {
                        "ok": bool(sup.get("ok")),
                        "reaped_verified": bool(reap.get("reaped_verified")),
                        "surviving_descendants": len(reap.get("surviving_descendant_pids") or ()),
                        "trusted_result_is_none": sup.get("trusted_result") is None and bool(sup.get("late_result_discarded")),
                    },
                )

            r_i11 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("I11", "I", "INTEGRATION_FAULT", "services.kh_03_r6_bounded_transport_service.supervise_r6_session", "a1_worker_reaped_aborted", "a1_worker_clean_trusted", "Cancel after child exit before accept discards late result"),
                negative_fn=_i11_neg, positive_fn=_i10_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_i11)
            summary["cancel_after_child_exit_before_accept_discarded"] = (r_i11["status"] == "PASS")

            # I12: Child non-zero / malformed IPC error -> clean reap with zero orphans!
            def _i12_neg():
                vg_12, pl_12 = _mk_grant_and_plan(priv_k, tr_qa, reg, gid="G_I12_NEG", sid=sid, dest=dest, now_dt=now_dt, sha=executable_sha, rel_sha=rel_sha)
                l_12 = led.claim_r6_grant_session_atomic(vg_12, pl_12, "sess_i12_neg", actual_now=now_dt)["lease"]
                sup = supervise_r6_session(
                    ledger=led, lease=l_12, plan=pl_12, verified_grant=vg_12,
                    validated_request={"kind": "rest_get", "url": "http://127.0.0.1:1/unreachable", "params": {}},
                    attempt_id="att_i12_neg", request_type="rest_get",
                    reserved_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 1024, "decoded_bytes": 2048}),
                    resource_sample_fn=_res_sample, expected_volumes={"dest": dest},
                    session_deadline_monotonic=time.monotonic() + 5.0, cancel_event=threading.Event(), utc_now_fn=lambda: now_dt,
                    qa_loopback_mode=True, allow_fixture_resource_sample=True,
                )
                lifecycle_traces.extend(sup.get("lifecycle_trace") or ())
                reap = sup.get("reap_info") or {}
                return (
                    {"attack": "child_socket_error_and_reap"},
                    {
                        "ok": bool(sup.get("ok")),
                        "reaped_verified": bool(reap.get("reaped_verified")),
                        "surviving_descendants": len(reap.get("surviving_descendant_pids") or ()),
                        "trusted_result_is_none": sup.get("trusted_result") is None,
                    },
                )

            r_i12 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("I12", "I", "INTEGRATION_FAULT", "services.kh_03_r6_bounded_transport_service.supervise_r6_session", "a1_worker_reaped_aborted", "a1_worker_clean_trusted", "Child socket/pipe failure cleanly reaped with zero orphans"),
                negative_fn=_i12_neg, positive_fn=_i10_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_i12)
            summary["child_launch_or_pipe_error_cleaned_no_orphan"] = (r_i12["status"] == "PASS")

            # I13: Blocked non-cooperative child + grandchild -> killed and reaped via OS Job Object/PGID!
            def _i13_neg():
                vg_13, pl_13 = _mk_grant_and_plan(priv_k, tr_qa, reg, gid="G_I13_NEG", sid=sid, dest=dest, now_dt=now_dt, sha=executable_sha, rel_sha=rel_sha)
                l_13 = led.claim_r6_grant_session_atomic(vg_13, pl_13, "sess_i13_neg", actual_now=now_dt)["lease"]
                ev_c = threading.Event()
                threading.Timer(0.6, ev_c.set).start()
                sup = supervise_r6_session(
                    ledger=led, lease=l_13, plan=pl_13, verified_grant=vg_13,
                    validated_request={"kind": "qa_stall_noncooperative", "stall_sec": 30.0, "spawn_grandchild": True},
                    attempt_id="att_i13_neg", request_type="rest_get",
                    reserved_caps=complete_caps_vector_a1({"requests": 1, "payload_bytes": 1024, "decoded_bytes": 2048}),
                    resource_sample_fn=_res_sample, expected_volumes={"dest": dest},
                    session_deadline_monotonic=time.monotonic() + 10.0, cancel_event=ev_c, utc_now_fn=lambda: now_dt,
                    qa_loopback_mode=True, allow_fixture_resource_sample=True,
                )
                lifecycle_traces.extend(sup.get("lifecycle_trace") or ())
                reap = sup.get("reap_info") or {}
                cpid = int(reap.get("child_pid") or 0)
                child_gone = (cpid > 0) and (not psutil.pid_exists(cpid))
                return (
                    {"attack": "noncooperative_child_plus_grandchild_kill_and_reap"},
                    {
                        "ok": bool(sup.get("ok")),
                        "reaped_verified": bool(reap.get("reaped_verified")) and child_gone,
                        "surviving_descendants": len(reap.get("surviving_descendant_pids") or ()),
                        "trusted_result_is_none": sup.get("trusted_result") is None,
                    },
                )

            r_i13 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("I13", "I", "INTEGRATION_FAULT", "services.kh_03_r6_bounded_transport_service.supervise_r6_session", "a1_worker_reaped_aborted", "a1_worker_clean_trusted", "Noncooperative child and grandchild killed and reaped with zero survivors"),
                negative_fn=_i13_neg, positive_fn=_i10_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_i13)
            summary["noncooperative_child_and_grandchild_reaped"] = (r_i13["status"] == "PASS")

            # I14: Cross-process parallel ledger/anchor writes (3 real OS children) + concurrent cap reservation race (3 threads for requests=2)!
            vg_14, pl_14 = _mk_grant_and_plan(
                priv_k, tr_qa, reg, gid="G_I14_RACE", sid=sid, dest=dest, now_dt=now_dt, sha=executable_sha, rel_sha=rel_sha,
                caps_override={"requests": 2, "payload_bytes": 4096, "decoded_bytes": 8192},
            )
            l_14 = led.claim_r6_grant_session_atomic(vg_14, pl_14, "sess_i14_race", actual_now=now_dt)["lease"]
            # Write 3 distinct signed grants for 3 real OS child processes contending simultaneously on the same SQLite ledger + file-locked anchor!
            proc_grant_paths: List[Path] = []
            for w_i in range(3):
                gp_w = {
                    "grant_id": f"G_I14_XP_{w_i}", "issuer": "QA_ISOLATED_HARNESS", "trust_namespace": QA_ISOLATED_TRUST_NAMESPACE_R6,
                    "issued_utc_ms": int(now_dt.timestamp() * 1000) - 1000, "expires_utc_ms": int(now_dt.timestamp() * 1000) + 600_000,
                    "repo": "quocan050302/Build-app-crypto", "branch": MANDATORY_BRANCH_KH03_R6,
                    "tested_executable_sha": executable_sha, "release_manifest_sha256": rel_sha,
                    "purpose": "QA_FIXTURE", "stage": "QA_LOOPBACK", "venue": APPROVED_VENUE_R6, "api_family": API_FAMILY_CLASSIC_V2,
                    "symbol": APPROVED_SYMBOL_R6, "product_type": APPROVED_PRODUCT_TYPE_V2_R6,
                    "allowed_endpoints": ["classic_v2_contracts"], "allowed_ws_channels": [],
                    "window_start_ms": 1794000000000, "window_end_ms": 1794003600000,
                    "destination_store_id": sid, "destination_root": str(dest), "max_session_duration_sec": 120, "max_concurrency": 1,
                    "caps": {"connections": 1, "requests": 1, "retries": 0, "subscriptions": 0, "messages": 0, "payload_bytes": 1024, "decoded_bytes": 2048, "pages": 1, "disk_bytes": 65536, "records": 10},
                    "retention_policy": {"max_retention_days": 7, "git_commit_raw_payloads": False},
                }
                gp_path = root / f"i14_xp_grant_{w_i}.json"
                gp_path.write_text(sign_r6_grant_payload_with_ed25519(gp_w, priv_k), encoding="utf-8")
                proc_grant_paths.append(gp_path)

            race_script = root / "i14_race_worker.py"
            race_script.write_text(
                "import json, sys\n"
                "from datetime import datetime, timezone\n"
                "from pathlib import Path\n"
                "from services.kh_03_r6_durable_grant_service import QA_ISOLATED_TRUST_NAMESPACE_R6, load_r6_owner_trust_root, open_r6_durable_ledger, verify_r6_owner_grant\n"
                "from services.kh_03_r6_bounded_transport_service import build_r6_bound_capture_plan\n"
                "from services.kh_03_r6_source_contract_service import build_r6_source_contract_registry\n"
                "from services.kh_03_r6_a1_trust_service import complete_caps_vector_a1\n"
                "sroot, sid, trp, gp_file, sha, rel, idx = sys.argv[1:8]\n"
                "now_dt = datetime(2026, 11, 26, 2, 10, 0, tzinfo=timezone.utc)\n"
                "tr = load_r6_owner_trust_root(trp, expected_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6)['trust_root']\n"
                "led = open_r6_durable_ledger({'ledger_store_root': sroot, 'store_id': sid}, sid, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_dt)['ledger']\n"
                "vg = verify_r6_owner_grant(Path(gp_file).read_bytes(), tr, now_dt, expected_executable_sha=sha, expected_release_manifest_sha256=rel, expected_store_id=sid, expected_stage='QA_LOOPBACK')['verified_grant']\n"
                "pl = build_r6_bound_capture_plan(vg, build_r6_source_contract_registry(), {}, {'ok': True}, plan_id=f'pl_xp_{idx}')['plan']\n"
                "cl = led.claim_r6_grant_session_atomic(vg, pl, f'sess_xp_{idx}', actual_now=now_dt)\n"
                "res = led.reserve_r6_io_budget_atomic(cl['lease'], f'att_xp_{idx}', 'rest_get', complete_caps_vector_a1({'requests': 1, 'payload_bytes': 512, 'decoded_bytes': 1024}), actual_now=now_dt) if cl.get('claimed') else {'reserved': False}\n"
                "print(json.dumps({'claimed': bool(cl.get('claimed')), 'reserved': bool(res.get('reserved')), 'gen': res.get('generation')}))\n",
                encoding="utf-8",
            )

            def _i14_neg():
                # 1. 3 real OS child processes simultaneously claim + reserve against the same SQLite ledger + file-locked anchor!
                procs = [
                    subprocess.Popen(
                        [sys.executable, str(race_script), str(sroot), sid, str(tr_p), str(proc_grant_paths[w]), executable_sha, rel_sha, str(w)],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_child_env(),
                    )
                    for w in range(3)
                ]
                xp_outs = []
                for p in procs:
                    so, _ = p.communicate(timeout=25)
                    xp_outs.append(json.loads(so.strip()) if so.strip() else {"reserved": False})
                xp_all_ok = all(o.get("claimed") and o.get("reserved") for o in xp_outs)

                # 2. Simultaneously race 3 threads for 2 request slots on l_14 -> strictly 2 winners, 1 denied!
                from concurrent.futures import ThreadPoolExecutor
                def _res_t(w_idx: int) -> Dict[str, Any]:
                    return led.reserve_r6_io_budget_atomic(
                        l_14, f"att_i14_t{w_idx}", "rest_get",
                        complete_caps_vector_a1({"requests": 1, "payload_bytes": 512, "decoded_bytes": 1024}),
                        actual_now=now_dt,
                    )
                with ThreadPoolExecutor(max_workers=3) as pool:
                    t_outs = list(pool.map(_res_t, range(3)))
                winners = sum(1 for o in t_outs if o.get("reserved"))
                denied = sum(1 for o in t_outs if not o.get("reserved"))
                ro = verify_r6_ledger_readonly(sroot, expected_store_id=sid, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6)
                conserved = (winners == 2 and denied == 1 and xp_all_ok and ro["valid"])
                return (
                    {"attack": "3_contenders_for_2_request_cap_plus_3_os_processes_anchor_race"},
                    {"allowed": not conserved, "violations_count": denied if conserved else 0, "winners": winners, "denied": denied, "xp_all_ok": xp_all_ok, "ro_valid": ro["valid"]},
                )

            def _i14_pos():
                ro = verify_r6_ledger_readonly(sroot, expected_store_id=sid, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6)
                snap = led.snapshot_grant(vg_14.grant_identity_key)
                ok = ro["valid"] and int(snap["outstanding"]["requests"]) == 2
                return ({"check": "cross_process_cap_conserved_and_anchor_monotonic"}, {"allowed": ok, "violations_count": 0 if ok else 1, "outstanding_requests": snap["outstanding"]["requests"]})

            r_i14 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("I14", "I", "INTEGRATION_FAULT", "services.kh_03_r6_durable_grant_service.R6DurableGrantLedger.reserve_r6_io_budget_atomic", "a1_rejected", "a1_accepted", "Cross-process parallel reservations conserve cap and monotonic anchor"),
                negative_fn=_i14_neg, positive_fn=_i14_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_i14)
            summary["cross_process_parallel_reservations_conserved"] = (r_i14["status"] == "PASS")
            summary["anchor_monotonic_ordering_verified"] = (r_i14["status"] == "PASS")

            # I15: Crash DB commit before anchor + unknown I/O + active_session_ids preservation + no grant reuse!
            sroot15 = root / "ledger15"
            provision_r6_durable_ledger(sroot15, store_id=sid, trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, now_utc=now_dt)
            led15 = open_r6_durable_ledger({"ledger_store_root": str(sroot15), "store_id": sid}, sid, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_dt)["ledger"]
            vg_15a, pl_15a = _mk_grant_and_plan(priv_k, tr_qa, reg, gid="G_I15_CRASH", sid=sid, dest=dest, now_dt=now_dt, sha=executable_sha, rel_sha=rel_sha)
            l_15a = led15.claim_r6_grant_session_atomic(vg_15a, pl_15a, "sess_i15_crash", actual_now=now_dt)["lease"]
            led15.reserve_r6_io_budget_atomic(l_15a, "att_i15_unk", "rest_get", complete_caps_vector_a1({"requests": 1, "payload_bytes": 512, "decoded_bytes": 1024}), actual_now=now_dt)
            anc_before_start = led15.anchor_path.read_bytes()
            led15.mark_r6_attempt_started(l_15a, "att_i15_unk", actual_now=now_dt)
            # Simulate genuine crash strictly between DB commit and anchor write (restore exact previous valid anchor!)
            led15.anchor_path.write_bytes(anc_before_start)

            def _i15_neg():
                # Re-open (forward-recovers pre-anchor commit!), recover crashed session (not in active_session_ids), verify re-claim of same grant DENIED!
                led15_re = open_r6_durable_ledger({"ledger_store_root": str(sroot15), "store_id": sid}, sid, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_dt)["ledger"]
                rec_out = led15_re.recover_r6_ledger_after_restart(actual_now=now_dt, active_session_ids=())
                reclaim = led15_re.claim_r6_grant_session_atomic(vg_15a, pl_15a, "sess_i15_replay", actual_now=now_dt)
                snap = led15_re.snapshot_grant(vg_15a.grant_identity_key)
                ok_crash_handled = (not reclaim["claimed"]) and snap["state"] == "SPENT_CRASH_RECOVERED" and int(snap["uncertain"]["payload_bytes"]) >= 512 and len(rec_out.get("recovered_started_attempts") or ()) >= 1
                return (
                    {"attack": "crash_pre_anchor_unknown_io_and_reclaim_after_restart"},
                    {"allowed": bool(reclaim["claimed"]), "violations_count": len(reclaim.get("violations") or [reclaim.get("status")]) if ok_crash_handled else 0, "state": snap["state"], "uncertain_bytes": snap["uncertain"]["payload_bytes"]},
                )

            def _i15_pos():
                # Fresh grant still claims cleanly after recovery and active_session_ids prevents recovering live session!
                led15_re = open_r6_durable_ledger({"ledger_store_root": str(sroot15), "store_id": sid}, sid, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_dt)["ledger"]
                vg_live, pl_live = _mk_grant_and_plan(priv_k, tr_qa, reg, gid="G_I15_LIVE", sid=sid, dest=dest, now_dt=now_dt, sha=executable_sha, rel_sha=rel_sha)
                cl_live = led15_re.claim_r6_grant_session_atomic(vg_live, pl_live, "sess_i15_live", actual_now=now_dt)
                rec_live = led15_re.recover_r6_ledger_after_restart(actual_now=now_dt, active_session_ids=("sess_i15_live",))
                ro = verify_r6_ledger_readonly(sroot15, expected_store_id=sid, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6)
                ok = bool(cl_live["claimed"]) and len(rec_live["skipped_active_sessions"]) == 1 and ro["valid"]
                return ({"check": "active_session_preserved_and_readonly_audit_valid"}, {"allowed": ok, "violations_count": 0 if ok else 1})

            r_i15 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("I15", "I", "INTEGRATION_FAULT", "services.kh_03_r6_durable_grant_service.R6DurableGrantLedger.recover_r6_ledger_after_restart", "a1_rejected", "a1_accepted", "Pre-anchor crash and unknown I/O recovered safely without grant reuse"),
                negative_fn=_i15_neg, positive_fn=_i15_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_i15)
            summary["crash_pre_anchor_and_unknown_io_restart_safe"] = (r_i15["status"] == "PASS")
            summary["lifecycle_traces_count"] = len(lifecycle_traces)
    finally:
        srv.shutdown()
        srv.server_close()
        hth.join(timeout=2.0)
        stop_ws()

    return receipts, lifecycle_traces, summary


def test_kh03_r6_a1_budget_transport_i01_to_i15() -> None:
    recs, traces, summary = run_kh03_r6_a1_controls_i01_to_i15(executable_sha="i" * 40, run_id="pytest_a1_i")
    verd = derive_r6_a1_fault_verdict(recs, expected_executable_sha="i" * 40, expected_run_id="pytest_a1_i")
    assert verd["valid"], f"I_CONTROLS_FAILED:{verd['violations']}"
    assert verd["passed_controls"] == 15
    assert len(traces) >= 10
    assert all(v is True for k, v in summary.items() if k != "lifecycle_traces_count"), f"I_SUMMARY_FAIL:{summary}"
