"""
aurum-desk/backend/tests/test_kh03_r5_c2_async_resource_regression.py

KH-03.R5.C2 — Async Scheduling, Global Worker Cap, W1/W2/W4/W1-repeat Capture Determinism,
Disk Waiver Exit-2 Rejection on C2 Acceptance Runner, and Zero-Socket Isolation Regression.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import socket
import subprocess
import sys
from typing import Any, Mapping
import uuid

import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_c2_public_capture import RawHttpResponseC2
from services.kh_03_r3_legacy_research_guard import audit_kh03_r3_legacy_research_callgraph
from services.kh_03_r5_capture_and_custody_service import (
    capture_r5_public_batch,
    get_r5_global_worker_pool_stats_c2,
    make_r5_fixture_resource_monitor,
)
from services.kh_03_r5_market_data_foundation_service import (
    R5OperatorGrant,
    build_r5_source_capability_inventory,
    plan_r5_capture_batches,
    validate_r5_operator_capture_scope,
)

NOW_UTC = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
NOW_MS = int(NOW_UTC.timestamp() * 1000)
WS_MS = 1794000000000
WE_MS = 1794000600000


def test_c2_cli_and_env_disk_waiver_rejected_exit_2() -> None:
    runner_path = APP_ROOT / "tools" / "run_kh_03_r5_c2_acceptance.py"
    assert runner_path.is_file()
    p_cli = subprocess.run([sys.executable, str(runner_path), "--owner-waive-disk-preflight"], cwd=str(APP_ROOT), capture_output=True, text=True, timeout=30)
    assert p_cli.returncode == 2 and "REJECTED_UNAPPROVED_AUTONOMOUS_DISK_WAIVER" in p_cli.stderr
    env = os.environ.copy()
    env["KH03_R5_C2_OWNER_WAIVE_DISK_PREFLIGHT"] = "1"
    p_env = subprocess.run([sys.executable, str(runner_path)], cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=30)
    assert p_env.returncode == 2 and "REJECTED_UNAPPROVED_AUTONOMOUS_DISK_WAIVER" in p_env.stderr


def test_c2_w1_w2_w4_w1repeat_capture_fixture_determinism_and_zero_sockets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbid_socket(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("EXTERNAL_NETWORK_SOCKET_FORBIDDEN_IN_C2")
    monkeypatch.setattr(socket, "create_connection", _forbid_socket)

    inv = build_r5_source_capability_inventory(REPO_ROOT)
    mon = make_r5_fixture_resource_monitor(destination=tmp_path)
    tag = uuid.uuid4().hex[:8]
    shas = []
    for idx, w in enumerate((1, 2, 4, 1)):
        g = R5OperatorGrant(f"QA_C2_DET_{tag}_{idx}", "QA", "QA_FIXTURE", "XAUUSDT", "USDT-FUTURES", ("public_ticker", "contracts", "history_candles"), WS_MS, WE_MS, 6, 50_000, 20, NOW_MS + 120_000, "c2_det", True, "QA")
        d = validate_r5_operator_capture_scope(g, {"symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "endpoints": ["public_ticker", "contracts", "history_candles"], "run_id": "c2_det", "purpose": "QA_FIXTURE", "window_start_ms": WS_MS, "window_end_ms": WE_MS, "max_requests": 6, "max_total_bytes": 50_000, "max_duration_sec": 20}, NOW_UTC, {"qa_fixture_mode": True})
        p = plan_r5_capture_batches(d, inv, {"destination_disk_free_gib": 30.0, "max_concurrency": w, "max_pages": 6, "max_requests": 6, "max_total_bytes": 50_000, "max_duration_sec": 20, "endpoints": ["public_ticker", "contracts", "history_candles"]}, now_utc=NOW_UTC)
        class _DetTr:
            def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
                return RawHttpResponseC2(200, f'{{"code":"00000","u":"{url}","s":"{params.get("symbol")}"}}'.encode(), {}, NOW_MS, NOW_MS + 5)
        res = asyncio.run(capture_r5_public_batch(p, _DetTr(), mon, run_id="c2_det", trusted_scope=d, destination_root=tmp_path, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC))
        assert res["capture_execution_status"] == "QA_FIXTURE_OFFLINE_PASS"
        assert res["undrained_inflight_workers"] == 0 and res["owned_executor_shutdown_complete"] is True
        shas.append(res["semantic_result_sha256"])

    assert len(set(shas)) == 1
    st = get_r5_global_worker_pool_stats_c2()
    assert st["active_workers_count"] == 0 and st["quarantined_workers_count"] == 0


def test_c2_zero_unauthorized_r1_callers_across_repo() -> None:
    cg = audit_kh03_r3_legacy_research_callgraph(REPO_ROOT)
    assert cg["LEGACY_METHOD_GUARD"] == "PASS" and int(cg["unauthorized_r1_callers_count"]) == 0
