"""
aurum-desk/backend/tests/test_kh03_r5_c1_resource_fail_closed.py

KH-03.R5.C1 — Phase C Resource Monitor Fail-Closed Negative & Boundary Controls (C01..C22).
Deterministic fake clocks and fixture monitors — ZERO real disk depletion or network sockets.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List, Mapping

import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_c2_public_capture import RawHttpResponseC2
from services.kh_02_r2_c3_provenance_guard import MAX_RAW_HTTP_BODY_BYTES_C3
from services.kh_03_r5_capture_and_custody_service import (
    R5DevCustodyJournal,
    _canonical_volume_identity_c1,
    capture_r5_public_batch,
    make_r5_fixture_resource_monitor,
    persist_r5_verified_dev_snapshot,
    validate_r5_resource_sample_strict,
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
OK_BODY = b'{"code":"00000","data":[{"symbol":"XAUUSDT"}]}'


def _valid_sample(dest: Path, *, now_m: float = 100.0, **over: Any) -> Dict[str, Any]:
    s: Dict[str, Any] = {
        "disk_free_gib": 25.0,
        "available_ram_mib": 1024.0,
        "sampled_monotonic": now_m,
        "destination_volume": _canonical_volume_identity_c1(dest),
        "monitor_source": "QA_DETERMINISTIC_FIXTURE_MONITOR",
        "sample_ok": True,
    }
    s.update(over)
    return s


_QA_C_SEQ_C1 = 0


def _plan_and_scope(rid: str = "c1_res_run") -> tuple[Dict[str, Any], Dict[str, Any]]:
    global _QA_C_SEQ_C1
    _QA_C_SEQ_C1 += 1
    inv = build_r5_source_capability_inventory(REPO_ROOT)
    g = R5OperatorGrant(
        grant_id=f"QA_C_{_QA_C_SEQ_C1}", issued_by="QA", purpose="QA_FIXTURE", symbol="XAUUSDT", product_type="USDT-FUTURES",
        allowed_endpoints=("public_ticker",), window_start_ms=WS_MS, window_end_ms=WE_MS,
        max_requests=4, max_total_bytes=50_000, max_duration_sec=30, expires_utc_ms=NOW_MS + 300_000,
        run_id=rid, is_qa_fixture_grant=True, origin_kind="QA",
    )
    req = {
        "symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "endpoints": ["public_ticker"], "run_id": rid,
        "purpose": "QA_FIXTURE", "window_start_ms": WS_MS, "window_end_ms": WE_MS,
        "max_requests": 4, "max_total_bytes": 50_000, "max_duration_sec": 30,
    }
    dec = validate_r5_operator_capture_scope(g, req, NOW_UTC, {"qa_fixture_mode": True})
    plan = plan_r5_capture_batches(dec, inv, {"destination_disk_free_gib": 28.0, "max_concurrency": 1, "max_pages": 4, "max_requests": 4, "max_total_bytes": 50_000, "max_duration_sec": 30, "endpoints": ["public_ticker"]}, now_utc=NOW_UTC)
    return dec, plan


class _CountingTransport:
    def __init__(self, responses: List[RawHttpResponseC2] = None) -> None:  # type: ignore[assignment]
        self.calls = 0
        self.responses = list(responses or [])

    def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
        self.calls += 1
        if self.responses:
            return self.responses.pop(0)
        return RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)


def test_c1_c01_to_c12_c16_invalid_monitors_block_with_zero_dispatches(tmp_path: Path) -> None:
    dec, plan = _plan_and_scope()
    now_m = 500.0
    wrong_vol = "Z:\\" if os.name == "nt" else "dev:999999999999"

    def _raising_mon() -> Dict[str, Any]:
        raise RuntimeError("psutil_disk_probe_io_error")

    bad_monitors: List[tuple[str, Any]] = [
        ("C01_monitor_none", None),
        ("C02_empty_dict", lambda: {}),
        ("C03_missing_disk", lambda: {k: v for k, v in _valid_sample(tmp_path, now_m=now_m).items() if k != "disk_free_gib"}),
        ("C04_missing_ram", lambda: {k: v for k, v in _valid_sample(tmp_path, now_m=now_m).items() if k != "available_ram_mib"}),
        ("C05_nan_disk", lambda: _valid_sample(tmp_path, now_m=now_m, disk_free_gib=float("nan"))),
        ("C06_inf_ram", lambda: _valid_sample(tmp_path, now_m=now_m, available_ram_mib=float("inf"))),
        ("C07_bool_disk", lambda: _valid_sample(tmp_path, now_m=now_m, disk_free_gib=True)),
        ("C07_str_ram", lambda: _valid_sample(tmp_path, now_m=now_m, available_ram_mib="2048")),
        ("C08_negative_disk", lambda: _valid_sample(tmp_path, now_m=now_m, disk_free_gib=-1.0)),
        ("C09_stale_sample", lambda: _valid_sample(tmp_path, now_m=now_m - 2.5)),
        ("C10_future_timestamp", lambda: _valid_sample(tmp_path, now_m=now_m + 1.0)),
        ("C11_wrong_volume", lambda: _valid_sample(tmp_path, now_m=now_m, destination_volume=wrong_vol)),
        ("C12_monitor_raises", _raising_mon),
        ("C16_sample_ok_false", lambda: _valid_sample(tmp_path, now_m=now_m, sample_ok=False)),
        ("C16_sample_ok_str", lambda: _valid_sample(tmp_path, now_m=now_m, sample_ok="true")),
        ("C16_missing_source", lambda: _valid_sample(tmp_path, now_m=now_m, monitor_source="")),
    ]

    for cid, mon in bad_monitors:
        tr = _CountingTransport()
        res = asyncio.run(capture_r5_public_batch(
            plan, tr, mon, run_id="c1_res_run", trusted_scope=dec, destination_root=tmp_path,
            monotonic_fn=lambda: now_m, utc_now_fn=lambda: NOW_UTC,
        ))
        assert tr.calls == 0, f"{cid} unexpectedly dispatched!"
        assert res["aborted"] is True and res["qa_fixture_records_count"] == 0, f"{cid} did not fail closed!"


def test_c1_boundary_exact_limits_and_midflight_resource_transitions_c13_c14_c15_c18(tmp_path: Path) -> None:
    # Exact boundary at 20.0 GiB / 512.0 MiB passes; just below fails!
    ok_at, _, _ = validate_r5_resource_sample_strict(_valid_sample(tmp_path, now_m=100.0, disk_free_gib=20.0, available_ram_mib=512.0), tmp_path, now_monotonic=100.0)
    assert ok_at is True
    bad_dk, v_dk, _ = validate_r5_resource_sample_strict(_valid_sample(tmp_path, now_m=100.0, disk_free_gib=19.9999, available_ram_mib=512.0), tmp_path, now_monotonic=100.0)
    assert bad_dk is False and any("RUNTIME_DISK_BELOW_20GIB" in v for v in v_dk)
    bad_rm, v_rm, _ = validate_r5_resource_sample_strict(_valid_sample(tmp_path, now_m=100.0, disk_free_gib=20.0, available_ram_mib=511.99), tmp_path, now_monotonic=100.0)
    assert bad_rm is False and any("RUNTIME_RAM_BELOW_512MIB" in v for v in v_rm)

    # C13: Low preflight disk (<20.5 GiB) blocked by planner!
    inv = build_r5_source_capability_inventory(REPO_ROOT)
    dec, _ = _plan_and_scope()
    p_low = plan_r5_capture_batches(dec, inv, {"destination_disk_free_gib": 20.49, "max_concurrency": 1, "max_pages": 2, "max_requests": 2, "max_total_bytes": 10_000, "max_duration_sec": 10, "endpoints": ["public_ticker"]}, now_utc=NOW_UTC)
    assert p_low["status"] == "BLOCKED" and any("BLOCKED_LOW_DISK_PREFLIGHT" in v for v in p_low["violations"])

    # C14: Disk drops <20 GiB AFTER retry sleep -> second attempt NEVER dispatches!
    dec, plan = _plan_and_scope()
    state = {"disk": 28.0, "ram": 2048.0, "m": 200.0}
    mon_dyn = lambda: _valid_sample(tmp_path, now_m=state["m"], disk_free_gib=state["disk"], available_ram_mib=state["ram"])
    tr14 = _CountingTransport([RawHttpResponseC2(429, b"", {"retry-after": "0.1"}, NOW_MS, NOW_MS + 5)])
    def _drop_disk_on_wait(s: float) -> None:
        state["m"] += s
        state["disk"] = 19.5
    r14 = asyncio.run(capture_r5_public_batch(
        plan, tr14, mon_dyn, run_id="c1_res_run", trusted_scope=dec, destination_root=tmp_path,
        sleep_fn=_drop_disk_on_wait, monotonic_fn=lambda: state["m"], utc_now_fn=lambda: NOW_UTC,
    ))
    assert tr14.calls == 1 and r14["aborted"] is True and "RUNTIME_DISK_BELOW_20GIB" in str(r14["abort_reason"])

    # C15: RAM drops <512 MiB AFTER transport returns, BEFORE staging write -> 0 staged files!
    dec15, plan15 = _plan_and_scope()
    state15 = {"ram": 2048.0, "m": 300.0}
    st15 = tmp_path / "st15"
    mon15 = lambda: _valid_sample(st15, now_m=state15["m"], disk_free_gib=28.0, available_ram_mib=state15["ram"])
    class _DropRamInFlight:
        def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
            state15["ram"] = 400.0
            return RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)
    r15 = asyncio.run(capture_r5_public_batch(
        plan15, _DropRamInFlight(), mon15, staging_root=st15, run_id="c1_res_run", trusted_scope=dec15, destination_root=tmp_path,
        monotonic_fn=lambda: state15["m"], utc_now_fn=lambda: NOW_UTC,
    ))
    assert r15["aborted"] is True and "RUNTIME_RAM_BELOW_512MIB" in str(r15["abort_reason"])
    assert r15["qa_fixture_records_count"] == 0 and (not st15.exists() or list(st15.glob("*.raw")) == [])


def test_c1_c19_to_c22_owned_cleanup_oversize_symlink_and_mandatory_raw_sha(tmp_path: Path) -> None:
    # C19: Abort cleanup deletes ONLY owned staged files, NEVER touches pre-existing unrelated files!
    dec, plan = _plan_and_scope()
    st_dir = tmp_path / "staging_shared"
    st_dir.mkdir()
    unrelated = st_dir / "owner_unrelated.txt"
    unrelated.write_text("KEEP_ME", encoding="utf-8")
    inv = build_r5_source_capability_inventory(REPO_ROOT)
    g2 = R5OperatorGrant("QA_C19", "QA", "QA_FIXTURE", "XAUUSDT", "USDT-FUTURES", ("public_ticker", "contracts"), WS_MS, WE_MS, 4, 50_000, 30, NOW_MS + 300_000, "c1_c19", True, "QA")
    d2 = validate_r5_operator_capture_scope(g2, {"symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "endpoints": ["public_ticker", "contracts"], "run_id": "c1_c19", "purpose": "QA_FIXTURE", "window_start_ms": WS_MS, "window_end_ms": WE_MS, "max_requests": 4, "max_total_bytes": 50_000, "max_duration_sec": 30}, NOW_UTC, {"qa_fixture_mode": True})
    p2 = plan_r5_capture_batches(d2, inv, {"destination_disk_free_gib": 28.0, "max_concurrency": 1, "max_pages": 4, "max_requests": 4, "max_total_bytes": 50_000, "max_duration_sec": 30, "endpoints": ["public_ticker", "contracts"]}, now_utc=NOW_UTC)
    ev19 = asyncio.Event()
    class _CancelAfterFirst:
        def __init__(self) -> None:
            self.n = 0
        def get(self, url: str, params: Mapping[str, Any], *, timeout_sec: float) -> RawHttpResponseC2:
            self.n += 1
            if self.n == 1:
                return RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)
            ev19.set()
            return RawHttpResponseC2(200, OK_BODY, {}, NOW_MS, NOW_MS + 5)
    mon19 = make_r5_fixture_resource_monitor(destination=st_dir)
    r19 = asyncio.run(capture_r5_public_batch(p2, _CancelAfterFirst(), mon19, cancel_event=ev19, staging_root=st_dir, run_id="c1_c19", trusted_scope=d2, destination_root=tmp_path, sleep_fn=lambda _: None, utc_now_fn=lambda: NOW_UTC))
    assert r19["aborted"] is True and r19["removed_staged_files_count"] == 1
    assert r19["qa_fixture_records_count"] == 0 and r19["items"] == []
    assert unrelated.is_file() and unrelated.read_text("utf-8") == "KEEP_ME"

    # C20/C21/C22: Custody journal rejects oversized staged file BEFORE read, symlink escape, and missing raw_sha256!
    croot = tmp_path / "custody_c1"
    j = R5DevCustodyJournal(croot, os_disk_probe_fn=lambda: 28.0)
    part = {"valid": True, "partition_role": "QA_FIXTURE", "plan_digest_sha256": "a" * 64}
    val_ok = {"valid": True, "canonical_normalized_sha256": "b" * 64}

    # C22: missing expected raw_sha256 -> REJECTED_MISSING_RAW_SHA
    sp_ok = st_dir / "valid.raw"
    sp_ok.write_bytes(OK_BODY)
    r22 = persist_r5_verified_dev_snapshot(sp_ok, {"evidence_class": "QA_FIXTURE", "capture_proof_id": "p22"}, val_ok, part, j, expected_staging_root=st_dir)
    assert r22["committed"] is False and r22["status"] == "REJECTED_MISSING_RAW_SHA"

    # C20: oversized staged file (> 4 MiB) rejected via stat BEFORE reading into RAM!
    sp_big = st_dir / "oversize.raw"
    with sp_big.open("wb") as fh:
        fh.seek(MAX_RAW_HTTP_BODY_BYTES_C3 + 1024)
        fh.write(b"\x00")
    r20 = persist_r5_verified_dev_snapshot(sp_big, {"raw_sha256": "c" * 64, "evidence_class": "QA_FIXTURE", "capture_proof_id": "p20"}, val_ok, part, j, expected_staging_root=st_dir)
    assert r20["committed"] is False and any("RAW_BLOB_OVERSIZE_BEFORE_READ" in v for v in r20["violations"])
    sp_big.unlink()

    # C21: symlink / Windows NTFS junction escape denied!
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    outside_file = outside_dir / "outside.raw"
    outside_file.write_bytes(OK_BODY)
    sym_sp = st_dir / "sym_escape.raw"
    junc_dir = st_dir / "junc_escape"
    created_junction = False
    try:
        try:
            os.symlink(outside_file, sym_sp)
            target_link = sym_sp
        except OSError:
            jproc = subprocess.run(["cmd", "/c", "mklink", "/J", str(junc_dir), str(outside_dir)], capture_output=True, text=True, check=False)
            assert jproc.returncode == 0, f"Windows junction creation failed: {jproc.stdout} {jproc.stderr}"
            created_junction = True
            target_link = junc_dir / "outside.raw"
        r21 = persist_r5_verified_dev_snapshot(target_link, {"raw_sha256": hashlib.sha256(OK_BODY).hexdigest(), "evidence_class": "QA_FIXTURE", "capture_proof_id": "p21"}, val_ok, part, j, expected_staging_root=st_dir)
        assert r21["committed"] is False and r21["status"] in ("REJECTED_SYMLINK", "REJECTED_STAGING_ESCAPE")
    finally:
        if created_junction and junc_dir.exists():
            os.rmdir(junc_dir)
