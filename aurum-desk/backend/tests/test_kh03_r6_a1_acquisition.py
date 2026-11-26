"""
aurum-desk/backend/tests/test_kh03_r6_a1_acquisition.py

AURUM DESK — KH-03.R6.A1 | GROUP G (G01..G10) END-TO-END ACQUISITION CLI,
STRICT DRY-RUN, REAL LOOPBACK SMOKE & DEV ACQUISITION, PARTIAL FAILURE,
CANCELLATION & RESTART NON-REUSE PAIRED CONTROLS
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

import websockets

from services.kh_03_r6_a1_acceptance_service import (
    R6A1FaultCaseSpec,
    derive_r6_a1_fault_verdict,
    execute_r6_a1_fault_pair,
)
from services.kh_03_r6_a1_acquisition_service import (
    build_r6_a1_acquisition_session,
    run_r6_a1_acquisition_session,
)
from services.kh_03_r6_bounded_transport_service import build_r6_bound_capture_plan, collect_real_r6_resource_sample
from services.kh_03_r6_custody_readiness_service import verify_r6_custody_readonly
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
from tests.test_kh03_r6_source_contracts import make_valid_instrument_raw

APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
CAPTURE_CLI_PATH = APP_ROOT / "tools" / "run_kh_03_r6_capture.py"


def _cli_env() -> Dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{BACKEND_ROOT}{os.pathsep}{APP_ROOT}{os.pathsep}{REPO_ROOT}"
    env["PYTHONHASHSEED"] = "0"
    return env


class LoopbackBitgetV2FixtureServer:
    """
    Bounded local 127.0.0.1 HTTP + WS fixture server serving exact Bitget Classic v2 paths:
      - GET /api/v2/mix/market/contracts
      - GET /api/v2/mix/market/history-candles  (supports multi-page backward endTime pagination!)
      - GET /api/v2/mix/market/candles
      - GET /api/v2/mix/market/history-fund-rate
      - WS  /v2/ws/public (books1 subscribe ack + snapshot + sequential updates)
    Supports optional fault injection (`fail_path`) and request counting.
    """

    def __init__(self, *, window_start_ms: int, window_end_ms: int, bars_per_page: int = 3) -> None:
        self.window_start_ms = int(window_start_ms)
        self.window_end_ms = int(window_end_ms)
        self.bars_per_page = int(bars_per_page)
        self.fail_path: Optional[str] = None
        self.http_hits = 0
        self.ws_hits = 0
        self._http_srv: Optional[ThreadingHTTPServer] = None
        self._http_th: Optional[threading.Thread] = None
        self._ws_th: Optional[threading.Thread] = None
        self._ws_loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws_stop: Optional[asyncio.Event] = None
        self.http_port = 0
        self.ws_port = 0

    @property
    def http_base_url(self) -> str:
        return f"http://127.0.0.1:{self.http_port}"

    @property
    def ws_base_url(self) -> str:
        return f"ws://127.0.0.1:{self.ws_port}"

    def start(self) -> "LoopbackBitgetV2FixtureServer":
        parent = self

        class _H(BaseHTTPRequestHandler):
            def log_message(self, *a): return

            def do_GET(self):
                parent.http_hits += 1
                sp = urlsplit(self.path)
                path = sp.path
                qs = {k: v[0] for k, v in parse_qs(sp.query).items()}
                if parent.fail_path and path == parent.fail_path:
                    err = b'{"code":"50000","msg":"injected_fault"}'
                    self.send_response(400)
                    self.send_header("Content-Length", str(len(err)))
                    self.end_headers()
                    self.wfile.write(err)
                    return

                if path == "/api/v2/mix/market/contracts":
                    payload = {"code": "00000", "msg": "success", "data": [make_valid_instrument_raw()]}
                elif path in ("/api/v2/mix/market/history-candles", "/api/v2/mix/market/candles"):
                    st_ms = int(qs.get("startTime", parent.window_start_ms))
                    en_ms = int(qs.get("endTime", parent.window_end_ms))
                    step = 60_000
                    all_ts = list(range(st_ms, en_ms, step))
                    # Serve last `bars_per_page` bars below cursor so multi-page backward pagination works cleanly!
                    slice_ts = all_ts[-parent.bars_per_page:] if all_ts else []
                    rows = []
                    for i, ts in enumerate(slice_ts):
                        o = 2650.00 + (i * 0.10)
                        rows.append([str(ts), f"{o:.2f}", f"{o + 0.50:.2f}", f"{o - 0.30:.2f}", f"{o + 0.20:.2f}", "12.50", "33125.00"])
                    payload = {"code": "00000", "msg": "success", "data": rows}
                elif path == "/api/v2/mix/market/history-fund-rate":
                    step_8h = 8 * 3_600_000
                    t0 = ((parent.window_start_ms // step_8h) * step_8h)
                    rows = [
                        {"symbol": APPROVED_SYMBOL_R6, "fundingRate": "0.000100", "fundingTime": str(t0)},
                        {"symbol": APPROVED_SYMBOL_R6, "fundingRate": "0.000120", "fundingTime": str(t0 + step_8h)},
                    ]
                    payload = {"code": "00000", "msg": "success", "data": rows}
                else:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self._http_srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self.http_port = int(self._http_srv.server_address[1])
        self._http_th = threading.Thread(target=self._http_srv.serve_forever, daemon=True)
        self._http_th.start()

        ws_ready = threading.Event()
        port_box: List[int] = []

        async def _ws_handler(ws: Any) -> None:
            parent.ws_hits += 1
            try:
                raw_sub = await asyncio.wait_for(ws.recv(), timeout=3.0)
                sub_d = json.loads(raw_sub)
                arg0 = (sub_d.get("args") or [{}])[0]
                await ws.send(json.dumps({"event": "subscribe", "arg": arg0}))
                now_ms = int(time.time() * 1000)
                await ws.send(json.dumps({
                    "action": "snapshot",
                    "arg": arg0,
                    "data": [{"bids": [["2650.10", "8.0"]], "asks": [["2650.20", "6.0"]], "ts": str(now_ms)}],
                }))
                for step_i in range(1, 4):
                    await ws.send(json.dumps({
                        "action": "update",
                        "arg": arg0,
                        "data": [{"bids": [[f"{2650.10 + step_i * 0.05:.2f}", "7.0"]], "asks": [[f"{2650.20 + step_i * 0.05:.2f}", "5.0"]], "ts": str(now_ms + step_i * 500)}],
                    }))
                await ws.close()
            except Exception:
                pass

        def _ws_run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            parent._ws_loop = loop
            stop_ev = asyncio.Event()
            parent._ws_stop = stop_ev

            async def _serve() -> None:
                async with websockets.serve(_ws_handler, "127.0.0.1", 0, max_size=1024 * 1024) as srv:
                    port_box.append(int(srv.sockets[0].getsockname()[1]))
                    ws_ready.set()
                    await stop_ev.wait()

            loop.run_until_complete(_serve())
            loop.close()

        self._ws_th = threading.Thread(target=_ws_run, daemon=True)
        self._ws_th.start()
        ws_ready.wait(timeout=5.0)
        self.ws_port = port_box[0]
        return self

    def stop(self) -> None:
        if self._http_srv is not None:
            self._http_srv.shutdown()
            self._http_srv.server_close()
        if self._http_th is not None:
            self._http_th.join(timeout=2.0)
        if self._ws_loop is not None and self._ws_stop is not None:
            try:
                self._ws_loop.call_soon_threadsafe(self._ws_stop.set)
            except Exception:
                pass
        if self._ws_th is not None:
            self._ws_th.join(timeout=2.0)


def _write_qa_cli_bundle(
    work_dir: Path,
    *,
    grant_id: str,
    store_id: str,
    priv_k: Any,
    tr_path: Path,
    ledger_root: Path,
    dest_dir: Path,
    http_base: str,
    ws_base: str,
    tested_sha: str,
    rel_sha: str,
    win_start: int,
    win_end: int,
    run_id: str,
    endpoints: Optional[List[str]] = None,
    ws_channels: Optional[List[str]] = None,
    caps_override: Optional[Mapping[str, int]] = None,
) -> Tuple[Path, Path, Path]:
    now_ms = int(time.time() * 1000)
    eps = endpoints or ["classic_v2_contracts", "classic_v2_history_candles", "classic_v2_history_fund_rate", "classic_v2_ws_books1"]
    wchs = ws_channels if ws_channels is not None else (["books1"] if "classic_v2_ws_books1" in eps else [])
    caps = {
        "connections": 2,
        "requests": 25,
        "retries": 4,
        "subscriptions": 2,
        "messages": 100,
        "payload_bytes": 524_288,
        "decoded_bytes": 1_048_576,
        "pages": 6,
        "disk_bytes": 4_194_304,
        "records": 2000,
    }
    if caps_override:
        caps.update(dict(caps_override))

    qa_cfg = {
        "mode": "QA_TEST_LOOPBACK_ONLY",
        "store_id": store_id,
        "ledger_store_root": str(ledger_root),
        "trust_root_path": str(tr_path),
        "loopback_http_base_url": http_base,
        "loopback_ws_base_url": ws_base,
        "expected_release_manifest_sha256": rel_sha,
        "run_id": run_id,
    }
    qa_cfg_p = work_dir / f"{grant_id}_qa_cfg.json"
    qa_cfg_p.write_text(json.dumps(qa_cfg, indent=2), encoding="utf-8")

    plan_doc = {
        "stage": "QA_LOOPBACK",
        "endpoints": eps,
        "ws_channels": wchs,
        "window_start_ms": win_start,
        "window_end_ms": win_end,
        "max_session_duration_sec": 120,
        "max_concurrency": 2,
        "destination_root": str(dest_dir),
        "caps": caps,
    }
    plan_p = work_dir / f"{grant_id}_plan.json"
    plan_p.write_text(json.dumps(plan_doc, indent=2), encoding="utf-8")

    grant_payload = {
        "grant_id": grant_id,
        "issuer": "QA_ISOLATED_HARNESS",
        "trust_namespace": QA_ISOLATED_TRUST_NAMESPACE_R6,
        "issued_utc_ms": now_ms - 5_000,
        "expires_utc_ms": now_ms + 600_000,
        "repo": "quocan050302/Build-app-crypto",
        "branch": MANDATORY_BRANCH_KH03_R6,
        "tested_executable_sha": tested_sha,
        "release_manifest_sha256": rel_sha,
        "purpose": "QA_FIXTURE",
        "stage": "QA_LOOPBACK",
        "venue": APPROVED_VENUE_R6,
        "api_family": API_FAMILY_CLASSIC_V2,
        "symbol": APPROVED_SYMBOL_R6,
        "product_type": APPROVED_PRODUCT_TYPE_V2_R6,
        "allowed_endpoints": eps,
        "allowed_ws_channels": wchs,
        "window_start_ms": win_start,
        "window_end_ms": win_end,
        "destination_store_id": store_id,
        "destination_root": str(dest_dir),
        "max_session_duration_sec": 120,
        "max_concurrency": 2,
        "caps": caps,
        "retention_policy": {"max_retention_days": 7, "git_commit_raw_payloads": False},
    }
    grant_p = work_dir / f"{grant_id}_grant.json"
    signed_str = sign_r6_grant_payload_with_ed25519(grant_payload, priv_k)
    grant_p.write_text(signed_str if isinstance(signed_str, str) else signed_str.decode("utf-8"), encoding="utf-8")
    return qa_cfg_p, plan_p, grant_p


def run_kh03_r6_a1_controls_g01_to_g10(
    *,
    executable_sha: str,
    run_id: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    # Actual repo HEAD for real CLI subprocess execution!
    head_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT), capture_output=True, text=True, check=True).stdout.strip()
    reg = build_r6_source_contract_registry()
    rel_sha = "c" * 64
    win_start = 1794000000000
    win_end_smoke = win_start + 3 * 60_000  # 3 bars -> 1 page
    win_end_dev = win_start + 6 * 60_000    # 6 bars -> 2 pages (bars_per_page=3!)

    receipts: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {}

    fx = LoopbackBitgetV2FixtureServer(window_start_ms=win_start, window_end_ms=win_end_dev, bars_per_page=3).start()
    try:
        with tempfile.TemporaryDirectory(prefix="a1_grp_g_") as td:
            root = Path(td)
            dest = root / "dest"
            dest.mkdir()
            sid = "a1_g_store"
            now_dt = datetime.now(timezone.utc)
            tr_p, priv_k, _ = provision_qa_isolated_trust_root(root / "trust", store_id=sid)
            tr_qa = load_r6_owner_trust_root(tr_p, expected_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6)["trust_root"]
            sroot = root / "ledger"
            provision_r6_durable_ledger(sroot, store_id=sid, trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, now_utc=now_dt)

            def _run_qa_cli(stage_arg: str, cfg_p: Path, gr_p: Path, pl_p: Path) -> Tuple[int, Dict[str, Any]]:
                proc = subprocess.run(
                    [sys.executable, str(CAPTURE_CLI_PATH), "--qa-loopback-e2e", "--stage", stage_arg, "--qa-config", str(cfg_p), "--grant", str(gr_p), "--plan", str(pl_p)],
                    cwd=str(REPO_ROOT), env=_cli_env(), capture_output=True, text=True, timeout=60,
                )
                doc = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {"raw_out": proc.stdout, "raw_err": proc.stderr}
                return int(proc.returncode), doc

            # G01: Handshake-only regression removed + real QA CLI E2E invokes supervisor & custody and persists!
            cli_src = CAPTURE_CLI_PATH.read_text("utf-8")
            handshake_removed = "AUTHORIZED_SESSION_CLAIMED_AND_READY" not in cli_src
            summary["handshake_only_success_removed"] = handshake_removed

            def _g01_neg():
                # Running CLI with --grant WITHOUT --qa-loopback-e2e or --execute-authorized MUST fail closed with 0 claim!
                _, pl_p, gr_p = _write_qa_cli_bundle(root, grant_id="G_G01_NEG", store_id=sid, priv_k=priv_k, tr_path=tr_p, ledger_root=sroot, dest_dir=dest, http_base=fx.http_base_url, ws_base=fx.ws_base_url, tested_sha=head_sha, rel_sha=rel_sha, win_start=win_start, win_end=win_end_smoke, run_id=f"{run_id}_g01n")
                proc = subprocess.run([sys.executable, str(CAPTURE_CLI_PATH), "--stage", "smoke", "--grant", str(gr_p), "--plan", str(pl_p)], cwd=str(REPO_ROOT), env=_cli_env(), capture_output=True, text=True, timeout=25)
                doc = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {}
                return (
                    {"attack": "missing_mode_flag_and_handshake_removed"},
                    {"exit_code": int(proc.returncode) if handshake_removed else 0, "grant_claimed": bool(doc.get("grant_claimed")), "external_requests": int(doc.get("external_market_requests_attempted", 0))},
                )

            def _g01_pos():
                cfg_p, pl_p, gr_p = _write_qa_cli_bundle(root, grant_id="G_G01_POS", store_id=sid, priv_k=priv_k, tr_path=tr_p, ledger_root=sroot, dest_dir=dest, http_base=fx.http_base_url, ws_base=fx.ws_base_url, tested_sha=head_sha, rel_sha=rel_sha, win_start=win_start, win_end=win_end_smoke, run_id=f"{run_id}_g01p")
                rc, doc = _run_qa_cli("smoke", cfg_p, gr_p, pl_p)
                summary["qa_cli_smoke_e2e_pass"] = (rc == 0 and doc.get("status") == "QA_ACQUISITION_END_TO_END_PASS")
                summary["qa_cli_smoke_pid"] = int(doc.get("cli_pid") or 0)
                summary["qa_cli_smoke_receipt"] = {k: doc.get(k) for k in ("status", "exit_code", "cli_pid", "tasks_completed_count", "committed_chunks_count", "total_committed_raw_bytes", "total_committed_normalized_rows", "custody_readonly_valid", "ledger_final_status", "receipt_digest")}
                return (
                    {"check": "real_qa_cli_smoke_e2e"},
                    {
                        "exit_code": rc, "status": doc.get("status"),
                        "committed_chunks": int(doc.get("committed_chunks_count", 0)),
                        "committed_rows": int(doc.get("total_committed_normalized_rows", 0)),
                        "custody_valid": bool(doc.get("custody_readonly_valid")),
                        "real_observed_records": int(doc.get("accepted_real_observed_records", -1)),
                        "external_requests": int(doc.get("external_market_requests_attempted", -1)),
                    },
                )

            r_g01 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("G01", "G", "BASELINE_BUG_RED", "tools.run_kh_03_r6_capture.run_capture_cli", "a1_cli_failed_no_claim", "a1_qa_e2e_pass", "Handshake-only removed and real QA CLI executes supervisor+custody E2E"),
                negative_fn=_g01_neg, positive_fn=_g01_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_g01)

            # G02: Default dry-run rejects invalid schema/caps/endpoints (nonzero exit, zero claim/socket/write); valid dry-run exits 0 with zero IO!
            def _g02_neg():
                bad_pl = root / "g02_bad_plan.json"
                bad_pl.write_text(json.dumps({"stage": "B_SMOKE", "endpoints": ["fake_private_order"], "caps": {"requests": -5}}), encoding="utf-8")
                h_before = fx.http_hits
                p_bad = subprocess.run([sys.executable, str(CAPTURE_CLI_PATH), "--stage", "smoke", "--dry-run", "--plan", str(bad_pl)], cwd=str(REPO_ROOT), env=_cli_env(), capture_output=True, text=True, timeout=25)
                doc = json.loads(p_bad.stdout) if p_bad.stdout.strip().startswith("{") else {}
                summary["dryrun_invalid_plan_nonzero_exit"] = (p_bad.returncode != 0 and doc.get("status") == "DRY_RUN_VALIDATION_FAILED")
                return (
                    {"attack": "dryrun_invalid_plan_schema"},
                    {"exit_code": int(p_bad.returncode), "grant_claimed": bool(doc.get("grant_claimed")) or (fx.http_hits != h_before), "external_requests": int(doc.get("external_market_requests_attempted", 0))},
                )

            def _g02_pos():
                good_pl = root / "g02_good_plan.json"
                good_pl.write_text(json.dumps({
                    "stage": "B_SMOKE", "endpoints": ["classic_v2_contracts", "classic_v2_history_candles"], "ws_channels": [],
                    "window_start_ms": win_start, "window_end_ms": win_end_smoke,
                    "max_session_duration_sec": 120, "max_concurrency": 1, "destination_root": str(dest),
                    "caps": {"connections": 1, "requests": 10, "retries": 2, "subscriptions": 0, "messages": 0, "payload_bytes": 65536, "decoded_bytes": 131072, "pages": 2, "disk_bytes": 1048576, "records": 100},
                }), encoding="utf-8")
                h_before = fx.http_hits
                p_ok = subprocess.run([sys.executable, str(CAPTURE_CLI_PATH), "--stage", "smoke", "--dry-run", "--plan", str(good_pl)], cwd=str(REPO_ROOT), env=_cli_env(), capture_output=True, text=True, timeout=25)
                doc = json.loads(p_ok.stdout) if p_ok.stdout.strip().startswith("{") else {}
                ok_dry = (p_ok.returncode == 0 and doc.get("status") == "DRY_RUN_VALIDATED_ZERO_IO" and doc.get("grant_claimed") is False and fx.http_hits == h_before and int(doc.get("custody_writes_attempted", -1)) == 0)
                summary["dryrun_valid_plan_zero_claim_zero_socket"] = ok_dry
                return ({"check": "dryrun_valid_plan_zero_io"}, {"allowed": ok_dry, "violations_count": len(doc.get("violations") or [])})

            r_g02 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("G02", "G", "NEGATIVE_CONTRACT", "tools.run_kh_03_r6_capture._validate_dry_run_plan_file", "a1_cli_failed_no_claim", "a1_accepted", "Dry-run rejects invalid plan and validates clean plan with zero claim/socket/write"),
                negative_fn=_g02_neg, positive_fn=_g02_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_g02)

            # G03: External flag + plausible QA/owner-like input -> HARD BLOCK in A1 with ZERO real claim!
            def _g03_neg():
                fake_owner_cfg = root / "g03_owner_cfg.json"
                fake_owner_cfg.write_text(json.dumps({"store_id": "owner_s", "ledger_store_root": str(sroot)}), encoding="utf-8")
                _, pl_p, gr_p = _write_qa_cli_bundle(root, grant_id="G_G03_EXT", store_id=sid, priv_k=priv_k, tr_path=tr_p, ledger_root=sroot, dest_dir=dest, http_base=fx.http_base_url, ws_base=fx.ws_base_url, tested_sha=head_sha, rel_sha=rel_sha, win_start=win_start, win_end=win_end_smoke, run_id=f"{run_id}_g03")
                r6_man = REPO_ROOT / "aurum-desk/docs/artifacts/kh_03_r6_observed_market_data/20261126T005509Z_529bdaf2e533/26_independent_OS_verifier_manifest_sha256.json"
                p_ext = subprocess.run(
                    [sys.executable, str(CAPTURE_CLI_PATH), "--stage", "smoke", "--execute-authorized", "--grant", str(gr_p), "--owner-config", str(fake_owner_cfg), "--release-manifest", str(r6_man), "--expected-manifest-sha256", "f7ed2c978778075d219217cd88ecdc881fde458173c6817172b289f00950320f"],
                    cwd=str(REPO_ROOT), env=_cli_env(), capture_output=True, text=True, timeout=25,
                )
                doc = json.loads(p_ext.stdout) if p_ext.stdout.strip().startswith("{") else {}
                # Also test direct build_r6_a1_acquisition_session(qa_loopback_mode=False) hard block!
                led = open_r6_durable_ledger({"ledger_store_root": str(sroot), "store_id": sid}, sid, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_dt)["ledger"]
                vg3 = verify_r6_owner_grant(gr_p.read_bytes(), tr_qa, now_dt, expected_executable_sha=head_sha, expected_release_manifest_sha256=rel_sha, expected_store_id=sid, expected_stage="QA_LOOPBACK")["verified_grant"]
                pl3 = build_r6_bound_capture_plan(vg3, reg, {}, {"ok": True}, plan_id="pl_g03")["plan"]
                b_ext = build_r6_a1_acquisition_session(
                    run_id=f"{run_id}_g03_dir", session_id="s_g03_dir", verified_grant=vg3, plan=pl3, registry=reg, ledger=led,
                    resource_sample_fn=lambda: collect_real_r6_resource_sample({"dest": dest}, active_workers=1),
                    expected_volumes={"dest": dest}, repo_root=REPO_ROOT, qa_loopback_mode=False, allow_fixture_resource_sample=True, actual_now=now_dt,
                )
                snap3 = led.snapshot_grant(vg3.grant_identity_key)
                blocked_ok = (p_ext.returncode != 0 and doc.get("grant_claimed") is False and b_ext["status"] == "BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED" and snap3 is None)
                summary["external_cli_with_plausible_input_hard_blocked"] = blocked_ok
                return (
                    {"attack": "execute_authorized_external_hard_block"},
                    {"exit_code": int(p_ext.returncode) if blocked_ok else 0, "grant_claimed": snap3 is not None, "external_requests": int(doc.get("external_market_requests_attempted", 0))},
                )

            def _g03_pos():
                cfg_p, pl_p, gr_p = _write_qa_cli_bundle(root, grant_id="G_G03_POS", store_id=sid, priv_k=priv_k, tr_path=tr_p, ledger_root=sroot, dest_dir=dest, http_base=fx.http_base_url, ws_base=fx.ws_base_url, tested_sha=head_sha, rel_sha=rel_sha, win_start=win_start, win_end=win_end_smoke, run_id=f"{run_id}_g03p")
                rc, doc = _run_qa_cli("smoke", cfg_p, gr_p, pl_p)
                return ({"check": "qa_loopback_allowed_explicitly"}, {"exit_code": rc, "status": doc.get("status"), "committed_chunks": int(doc.get("committed_chunks_count", 0)), "committed_rows": int(doc.get("total_committed_normalized_rows", 0)), "custody_valid": bool(doc.get("custody_readonly_valid")), "real_observed_records": int(doc.get("accepted_real_observed_records", -1)), "external_requests": int(doc.get("external_market_requests_attempted", -1))})

            r_g03 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("G03", "G", "NEGATIVE_CONTRACT", "services.kh_03_r6_a1_acquisition_service.build_r6_a1_acquisition_session", "a1_cli_failed_no_claim", "a1_qa_e2e_pass", "External execute-authorized hard-blocked in A1 with zero grant claim"),
                negative_fn=_g03_neg, positive_fn=_g03_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_g03)

            # G04: Empty / metadata-only task plan rejected BEFORE claiming grant!
            def _g04_neg():
                cfg_p, pl_p, gr_p = _write_qa_cli_bundle(
                    root, grant_id="G_G04_EMPTY", store_id=sid, priv_k=priv_k, tr_path=tr_p, ledger_root=sroot, dest_dir=dest,
                    http_base=fx.http_base_url, ws_base=fx.ws_base_url, tested_sha=head_sha, rel_sha=rel_sha, win_start=win_start, win_end=win_end_smoke,
                    run_id=f"{run_id}_g04n", endpoints=["classic_v2_contracts"], ws_channels=[],
                )
                rc, doc = _run_qa_cli("smoke", cfg_p, gr_p, pl_p)
                led = open_r6_durable_ledger({"ledger_store_root": str(sroot), "store_id": sid}, sid, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_dt)["ledger"]
                vg4 = verify_r6_owner_grant(gr_p.read_bytes(), tr_qa, now_dt, expected_executable_sha=head_sha, expected_release_manifest_sha256=rel_sha, expected_store_id=sid, expected_stage="QA_LOOPBACK")["verified_grant"]
                unclaimed = led.snapshot_grant(vg4.grant_identity_key) is None
                summary["empty_task_plan_blocked_no_claim"] = (rc != 0 and unclaimed)
                return ({"attack": "metadata_only_no_data_tasks"}, {"exit_code": rc, "grant_claimed": not unclaimed, "external_requests": 0})

            def _g04_pos():
                cfg_p, pl_p, gr_p = _write_qa_cli_bundle(root, grant_id="G_G04_POS", store_id=sid, priv_k=priv_k, tr_path=tr_p, ledger_root=sroot, dest_dir=dest, http_base=fx.http_base_url, ws_base=fx.ws_base_url, tested_sha=head_sha, rel_sha=rel_sha, win_start=win_start, win_end=win_end_smoke, run_id=f"{run_id}_g04p")
                rc, doc = _run_qa_cli("smoke", cfg_p, gr_p, pl_p)
                return ({"check": "nonempty_task_plan_complete"}, {"exit_code": rc, "status": doc.get("status"), "committed_chunks": int(doc.get("committed_chunks_count", 0)), "committed_rows": int(doc.get("total_committed_normalized_rows", 0)), "custody_valid": bool(doc.get("custody_readonly_valid")), "real_observed_records": int(doc.get("accepted_real_observed_records", -1)), "external_requests": int(doc.get("external_market_requests_attempted", -1))})

            r_g04 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("G04", "G", "NEGATIVE_CONTRACT", "services.kh_03_r6_a1_acquisition_service.build_r6_a1_acquisition_tasks", "a1_cli_failed_no_claim", "a1_qa_e2e_pass", "Empty/incomplete task plan blocked before grant claim"),
                negative_fn=_g04_neg, positive_fn=_g04_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_g04)

            # G05: Full loopback smoke (metadata identity + REST OHLCV + funding + WS books1 + manifest & counts) vs bad WS URL!
            def _g05_neg():
                cfg_p, pl_p, gr_p = _write_qa_cli_bundle(root, grant_id="G_G05_NEG", store_id=sid, priv_k=priv_k, tr_path=tr_p, ledger_root=sroot, dest_dir=dest, http_base="https://api.bitget.com", ws_base=fx.ws_base_url, tested_sha=head_sha, rel_sha=rel_sha, win_start=win_start, win_end=win_end_smoke, run_id=f"{run_id}_g05n")
                rc, doc = _run_qa_cli("smoke", cfg_p, gr_p, pl_p)
                return ({"attack": "nonloopback_url_in_qa_mode"}, {"exit_code": rc, "grant_claimed": bool(doc.get("grant_claimed")), "external_requests": int(doc.get("external_market_requests_attempted", 0))})

            g05_seq = [0]

            def _g05_pos():
                g05_seq[0] += 1
                idx5 = g05_seq[0]
                cfg_p, pl_p, gr_p = _write_qa_cli_bundle(root, grant_id=f"G_G05_POS_{idx5}", store_id=sid, priv_k=priv_k, tr_path=tr_p, ledger_root=sroot, dest_dir=dest, http_base=fx.http_base_url, ws_base=fx.ws_base_url, tested_sha=head_sha, rel_sha=rel_sha, win_start=win_start, win_end=win_end_smoke, run_id=f"{run_id}_g05p_{idx5}")
                rc, doc = _run_qa_cli("smoke", cfg_p, gr_p, pl_p)
                return ({"check": f"full_loopback_smoke_metadata_rest_ws_manifest_{idx5}"}, {"exit_code": rc, "status": doc.get("status"), "committed_chunks": int(doc.get("committed_chunks_count", 0)), "committed_rows": int(doc.get("total_committed_normalized_rows", 0)), "custody_valid": bool(doc.get("custody_readonly_valid")) and bool(doc.get("dataset_manifest_sha256")), "real_observed_records": int(doc.get("accepted_real_observed_records", -1)), "external_requests": int(doc.get("external_market_requests_attempted", -1))})

            r_g05 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("G05", "G", "INTEGRATION_FAULT", "services.kh_03_r6_a1_acquisition_service.run_r6_a1_acquisition_session", "a1_cli_failed_no_claim", "a1_qa_e2e_pass", "Full loopback smoke acquires metadata, REST, WS and seals QA manifest"),
                negative_fn=_g05_neg, positive_fn=_g05_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_g05)

            # G06: Full loopback DEV (multi-page OHLCV=2 pages / 6 bars + BBO epoch + funding + gaps/lineage)
            def _g06_neg():
                # Request DEV profile with non-loopback WS URL -> blocked before claim!
                cfg_p, pl_p, gr_p = _write_qa_cli_bundle(root, grant_id="G_G06_NEG", store_id=sid, priv_k=priv_k, tr_path=tr_p, ledger_root=sroot, dest_dir=dest, http_base=fx.http_base_url, ws_base="wss://ws.bitget.com", tested_sha=head_sha, rel_sha=rel_sha, win_start=win_start, win_end=win_end_dev, run_id=f"{run_id}_g06n")
                rc, doc = _run_qa_cli("dev", cfg_p, gr_p, pl_p)
                return ({"attack": "dev_nonloopback_ws_url"}, {"exit_code": rc, "grant_claimed": bool(doc.get("grant_claimed")), "external_requests": int(doc.get("external_market_requests_attempted", 0))})

            def _g06_pos():
                dev_rid = f"{run_id}_g06dev"
                cfg_p, pl_p, gr_p = _write_qa_cli_bundle(root, grant_id="G_G06_DEV", store_id=sid, priv_k=priv_k, tr_path=tr_p, ledger_root=sroot, dest_dir=dest, http_base=fx.http_base_url, ws_base=fx.ws_base_url, tested_sha=head_sha, rel_sha=rel_sha, win_start=win_start, win_end=win_end_dev, run_id=dev_rid)
                rc, doc = _run_qa_cli("dev", cfg_p, gr_p, pl_p)
                # Verify actual persisted QA custody directory read-only against SQLite ledger!
                cdir = dest / "qa_fixture" / dev_rid
                ro = verify_r6_custody_readonly(cdir, expected_partition="QA_FIXTURE", expected_run_id=dev_rid, ledger_store_root=sroot)
                summary["qa_cli_dev_e2e_pass"] = (rc == 0 and doc.get("status") == "QA_ACQUISITION_END_TO_END_PASS" and ro["valid"])
                summary["qa_cli_dev_committed_chunks"] = int(doc.get("committed_chunks_count", 0))
                summary["qa_cli_dev_committed_rows"] = int(doc.get("total_committed_normalized_rows", 0))
                summary["qa_cli_dev_committed_raw_bytes"] = int(doc.get("total_committed_raw_bytes", 0))
                summary["qa_cli_dev_receipt"] = {k: doc.get(k) for k in ("status", "exit_code", "cli_pid", "tasks_planned_count", "tasks_completed_count", "committed_chunks_count", "total_committed_raw_bytes", "total_committed_normalized_rows", "custody_readonly_valid", "custody_tip_hash", "dataset_manifest_sha256", "ledger_final_status", "receipt_digest")}
                return (
                    {"check": "full_loopback_dev_multipage_ohlcv_bbo_funding"},
                    {
                        "exit_code": rc, "status": doc.get("status"),
                        "committed_chunks": int(doc.get("committed_chunks_count", 0)),
                        "committed_rows": int(doc.get("total_committed_normalized_rows", 0)),
                        "custody_valid": bool(doc.get("custody_readonly_valid")) and bool(ro["valid"]),
                        "real_observed_records": int(doc.get("accepted_real_observed_records", -1)),
                        "external_requests": int(doc.get("external_market_requests_attempted", -1)),
                    },
                )

            r_g06 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("G06", "G", "INTEGRATION_FAULT", "services.kh_03_r6_a1_acquisition_service.run_r6_a1_acquisition_session", "a1_cli_failed_no_claim", "a1_qa_e2e_pass", "Full loopback DEV multi-page OHLCV, BBO epoch, funding and verified custody"),
                negative_fn=_g06_neg, positive_fn=_g06_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_g06)

            # G07: Second task error (OHLCV returns 400) -> truthful PARTIAL_ACQUISITION_FAILED (nonzero exit!), never false PASS!
            def _g07_neg():
                fx.fail_path = "/api/v2/mix/market/history-candles"
                try:
                    cfg_p, pl_p, gr_p = _write_qa_cli_bundle(root, grant_id="G_G07_PARTIAL", store_id=sid, priv_k=priv_k, tr_path=tr_p, ledger_root=sroot, dest_dir=dest, http_base=fx.http_base_url, ws_base=fx.ws_base_url, tested_sha=head_sha, rel_sha=rel_sha, win_start=win_start, win_end=win_end_smoke, run_id=f"{run_id}_g07n")
                    rc, doc = _run_qa_cli("smoke", cfg_p, gr_p, pl_p)
                finally:
                    fx.fail_path = None
                summary["partial_second_task_failure_truthful"] = (rc != 0 and doc.get("status") == "PARTIAL_ACQUISITION_FAILED" and int(doc.get("tasks_failed_count", 0)) >= 1)
                return (
                    {"attack": "second_task_ohlcv_http_400"},
                    {"exit_code": rc, "status": doc.get("status"), "real_observed_records": int(doc.get("accepted_real_observed_records", 0))},
                )

            r_g07 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("G07", "G", "INTEGRATION_FAULT", "services.kh_03_r6_a1_acquisition_service.build_r6_a1_session_receipt", "a1_session_aborted_or_partial", "a1_qa_e2e_pass", "Second task failure produces truthful PARTIAL_ACQUISITION_FAILED"),
                negative_fn=_g07_neg, positive_fn=_g05_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_g07)

            # G08: Cancellation between claim and first attempt -> finalize once, ZERO HTTP/WS I/O!
            led_dir = open_r6_durable_ledger({"ledger_store_root": str(sroot), "store_id": sid}, sid, expected_trust_namespace=QA_ISOLATED_TRUST_NAMESPACE_R6, actual_now=now_dt)["ledger"]

            def _g08_neg():
                _, pl_p, gr_p = _write_qa_cli_bundle(root, grant_id="G_G08_CANCEL", store_id=sid, priv_k=priv_k, tr_path=tr_p, ledger_root=sroot, dest_dir=dest, http_base=fx.http_base_url, ws_base=fx.ws_base_url, tested_sha=head_sha, rel_sha=rel_sha, win_start=win_start, win_end=win_end_smoke, run_id=f"{run_id}_g08n")
                vg8 = verify_r6_owner_grant(gr_p.read_bytes(), tr_qa, now_dt, expected_executable_sha=head_sha, expected_release_manifest_sha256=rel_sha, expected_store_id=sid, expected_stage="QA_LOOPBACK")["verified_grant"]
                pl8 = build_r6_bound_capture_plan(vg8, reg, {}, {"ok": True}, plan_id="pl_g08")["plan"]
                sb = build_r6_a1_acquisition_session(
                    run_id=f"{run_id}_g08n", session_id="s_g08n", verified_grant=vg8, plan=pl8, registry=reg, ledger=led_dir,
                    resource_sample_fn=lambda: collect_real_r6_resource_sample({"dest": dest}, active_workers=1),
                    expected_volumes={"dest": dest}, repo_root=REPO_ROOT, qa_loopback_mode=True, qa_http_base_url=fx.http_base_url, qa_ws_base_url=fx.ws_base_url, allow_fixture_resource_sample=True, actual_now=now_dt,
                )
                h0, w0 = fx.http_hits, fx.ws_hits
                rec8 = run_r6_a1_acquisition_session(
                    ctx=sb["context"], verified_grant=vg8, plan=pl8, registry=reg, ledger=led_dir,
                    resource_sample_fn=lambda: collect_real_r6_resource_sample({"dest": dest}, active_workers=1),
                    expected_volumes={"dest": dest}, repo_root=REPO_ROOT, utc_now_fn=lambda: now_dt,
                    allow_fixture_resource_sample=True, cancel_between_claim_and_first_attempt=True,
                )
                zero_io = (fx.http_hits == h0 and fx.ws_hits == w0 and rec8["committed_chunks_count"] == 0 and rec8["ledger_final_status"] == "SPENT")
                summary["cancel_between_claim_and_first_attempt_zero_io"] = zero_io
                return ({"attack": "cancel_between_claim_and_first_attempt"}, {"exit_code": int(rec8["exit_code"]) if zero_io else 0, "status": rec8["status"], "real_observed_records": 0})

            r_g08 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("G08", "G", "INTEGRATION_FAULT", "services.kh_03_r6_a1_acquisition_service.run_r6_a1_acquisition_session", "a1_session_aborted_or_partial", "a1_qa_e2e_pass", "Cancel between claim and first attempt finalizes once with zero I/O"),
                negative_fn=_g08_neg, positive_fn=_g05_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_g08)

            # G09: Cancel after child response before persist -> discard late bytes, zero writes, no false success!
            def _g09_neg():
                _, _, gr_p = _write_qa_cli_bundle(root, grant_id="G_G09_LATE", store_id=sid, priv_k=priv_k, tr_path=tr_p, ledger_root=sroot, dest_dir=dest, http_base=fx.http_base_url, ws_base=fx.ws_base_url, tested_sha=head_sha, rel_sha=rel_sha, win_start=win_start, win_end=win_end_smoke, run_id=f"{run_id}_g09n")
                vg9 = verify_r6_owner_grant(gr_p.read_bytes(), tr_qa, now_dt, expected_executable_sha=head_sha, expected_release_manifest_sha256=rel_sha, expected_store_id=sid, expected_stage="QA_LOOPBACK")["verified_grant"]
                pl9 = build_r6_bound_capture_plan(vg9, reg, {}, {"ok": True}, plan_id="pl_g09")["plan"]
                sb = build_r6_a1_acquisition_session(
                    run_id=f"{run_id}_g09n", session_id="s_g09n", verified_grant=vg9, plan=pl9, registry=reg, ledger=led_dir,
                    resource_sample_fn=lambda: collect_real_r6_resource_sample({"dest": dest}, active_workers=1),
                    expected_volumes={"dest": dest}, repo_root=REPO_ROOT, qa_loopback_mode=True, qa_http_base_url=fx.http_base_url, qa_ws_base_url=fx.ws_base_url, allow_fixture_resource_sample=True, actual_now=now_dt,
                )
                ev_c = threading.Event()
                rec9 = run_r6_a1_acquisition_session(
                    ctx=sb["context"], verified_grant=vg9, plan=pl9, registry=reg, ledger=led_dir,
                    resource_sample_fn=lambda: collect_real_r6_resource_sample({"dest": dest}, active_workers=1),
                    expected_volumes={"dest": dest}, repo_root=REPO_ROOT, cancel_event=ev_c, utc_now_fn=lambda: now_dt,
                    allow_fixture_resource_sample=True, post_exit_hook=lambda: ev_c.set(),
                )
                zero_pers = (rec9["committed_chunks_count"] == 0 and rec9["exit_code"] != 0)
                summary["cancel_before_custody_persist_zero_write"] = zero_pers
                return ({"attack": "cancel_after_response_before_persist"}, {"exit_code": int(rec9["exit_code"]) if zero_pers else 0, "status": rec9["status"], "real_observed_records": 0})

            r_g09 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("G09", "G", "INTEGRATION_FAULT", "services.kh_03_r6_a1_acquisition_service.execute_r6_a1_rest_task", "a1_session_aborted_or_partial", "a1_qa_e2e_pass", "Cancel after child response before persist discards late bytes"),
                negative_fn=_g09_neg, positive_fn=_g05_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_g09)

            # G10: Repeat same grant after successful run (new OS process = restart) -> ZERO second I/O!
            def _g10_neg():
                cfg_p, pl_p, gr_p = _write_qa_cli_bundle(root, grant_id="G_G10_ONCE", store_id=sid, priv_k=priv_k, tr_path=tr_p, ledger_root=sroot, dest_dir=dest, http_base=fx.http_base_url, ws_base=fx.ws_base_url, tested_sha=head_sha, rel_sha=rel_sha, win_start=win_start, win_end=win_end_smoke, run_id=f"{run_id}_g10_first")
                rc1, doc1 = _run_qa_cli("smoke", cfg_p, gr_p, pl_p)
                assert rc1 == 0 and doc1.get("status") == "QA_ACQUISITION_END_TO_END_PASS"
                h_after1, w_after1 = fx.http_hits, fx.ws_hits
                # Second distinct OS process tries to reuse exact same signed grant!
                rc2, doc2 = _run_qa_cli("smoke", cfg_p, gr_p, pl_p)
                no_second_io = (rc2 != 0 and fx.http_hits == h_after1 and fx.ws_hits == w_after1)
                summary["repeat_same_grant_denied_after_restart"] = no_second_io
                return ({"attack": "repeat_spent_grant_new_os_process"}, {"exit_code": rc2 if no_second_io else 0, "status": doc2.get("status"), "real_observed_records": 0})

            r_g10 = execute_r6_a1_fault_pair(
                R6A1FaultCaseSpec("G10", "G", "INTEGRATION_FAULT", "tools.run_kh_03_r6_capture.run_capture_cli", "a1_session_aborted_or_partial", "a1_qa_e2e_pass", "Replaying spent grant across OS process restart performs zero second I/O"),
                negative_fn=_g10_neg, positive_fn=_g05_pos, executable_sha=executable_sha, run_id=run_id,
            )
            receipts.append(r_g10)
    finally:
        fx.stop()

    return receipts, summary


def test_kh03_r6_a1_acquisition_g01_to_g10() -> None:
    recs, summary = run_kh03_r6_a1_controls_g01_to_g10(executable_sha="g" * 40, run_id="pytest_a1_g")
    verd = derive_r6_a1_fault_verdict(recs, expected_executable_sha="g" * 40, expected_run_id="pytest_a1_g")
    assert verd["valid"], f"G_CONTROLS_FAILED:{verd['violations']}"
    assert verd["passed_controls"] == 10
    assert summary.get("qa_cli_smoke_e2e_pass") is True
    assert summary.get("qa_cli_dev_e2e_pass") is True
    assert summary.get("qa_cli_dev_committed_chunks", 0) >= 4
