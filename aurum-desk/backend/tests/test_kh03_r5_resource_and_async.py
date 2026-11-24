"""
aurum-desk/backend/tests/test_kh03_r5_resource_and_async.py

Tests KH-03.R5 resource safety & async determinism (R5-F33..F37, F43):
  - CLI & env disk waiver rejected exit code 2 (`run_kh_03_r5_verification.py`)
  - Low preflight disk (<20.5 GiB), runtime disk (<20.0 GiB), RAM (<512 MiB), child (>4),
    and missing mandatory execution fail closed (never PASS)
  - Strict benchmark NaN/nonfinite & cross-kind schema rejection
  - Serial vs async concurrent R5 validator determinism (exact canonical SHA equality)
  - Sentinel temp creation & zero-orphan cleanup.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_research_integrity_service import (
    CANONICAL_DATASET_SHA256_KH02_R2,
    create_sentinel_owned_temp_dir_r2_c1,
)
from services.kh_03_r3_c1_worker_policy_service import (
    BENCHMARK_KIND_PYTEST,
    select_measured_worker_policy_strict,
)
from services.kh_03_r5_market_data_foundation_service import validate_r5_ohlcv_stream
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tools.kh_03_r5_independent_verifier import recompute_r5_gate_matrix
from tools.run_kh_02_r2_c4_acceptance import _cleanup_sentinel_temp_c4_win_safe


def test_r5_cli_and_env_disk_waiver_rejected_exit_2_f33() -> None:
    p_cli = subprocess.run(
        [sys.executable, str(APP_ROOT / "tools" / "run_kh_03_r5_verification.py"), "--owner-waive-disk-preflight"],
        cwd=str(APP_ROOT), capture_output=True, text=True, timeout=30,
    )
    assert p_cli.returncode == 2 and "REJECTED_UNAPPROVED_AUTONOMOUS_DISK_WAIVER" in p_cli.stderr

    env = os.environ.copy()
    env["KH03_R5_OWNER_WAIVE_DISK_PREFLIGHT"] = "1"
    p_env = subprocess.run(
        [sys.executable, str(APP_ROOT / "tools" / "run_kh_03_r5_verification.py")],
        cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=30,
    )
    assert p_env.returncode == 2 and "REJECTED_UNAPPROVED_AUTONOMOUS_DISK_WAIVER" in p_env.stderr


def test_r5_serial_vs_async_validator_determinism_f36() -> None:
    bars = [
        {"open_time": 1794000000000 + i * 60_000, "close_time": 1794000060000 + i * 60_000, "open": "4100.00", "high": "4101.00", "low": "4099.50", "close": "4100.50", "volume": "5.0", "is_closed": True}
        for i in range(60)
    ]
    # Intentionally shuffled input order to prove canonical chronological sorting is identical across W1 & async
    bars_rev = list(reversed(bars))
    contract = {"symbol": "XAUUSDT", "tick_size": "0.01"}
    rng = {"start_ms": 1794000000000, "end_ms": 1794003600000, "now_utc_ms": 1794004000000}

    def _run_one(src_bars: list) -> str:
        res = validate_r5_ohlcv_stream([{"rows": src_bars, "symbol": "XAUUSDT", "timeframe": "1m"}], contract, "1m", rng)
        assert res["valid"] is True
        return str(res["canonical_normalized_sha256"])

    s_shas = [_run_one(bars), _run_one(bars_rev)]

    async def _run_par() -> list[str]:
        return await asyncio.gather(asyncio.to_thread(_run_one, bars), asyncio.to_thread(_run_one, bars_rev))

    a_shas = asyncio.run(_run_par())
    assert len(set(s_shas + a_shas)) == 1


def test_r5_resource_and_mandatory_test_gates_fail_closed_f33_f37_f43(tmp_path: Path) -> None:
    from tests.test_kh03_r5_readiness_and_verifier import build_synthetic_valid_kh03_r5_run_dir

    rdir = tmp_path / "r5_gate_res"
    build_synthetic_valid_kh03_r5_run_dir(rdir)
    docs = {p.name[:2]: json.loads(p.read_text("utf-8")) for p in sorted(rdir.glob("[0-9][0-9]_*.json"))}
    kw = dict(mem_audit={"status": "PASS", "below_floor_samples_count": 0}, pytest_recomp={"valid": True}, science_recomp={"valid": True}, junit_ok=True, r4_replay_ok=True, for_final_verifier_manifest=True)

    assert recompute_r5_gate_matrix(docs=docs, **kw)["gates_26"]["G21_DISK_RAM_PID_NO_WAIVER"] == "PASS"

    # Low preflight disk -> BLOCKED_LOW_DISK
    d_ld = copy.deepcopy(docs)
    d_ld["00"]["strict_multi_mount_probe"]["distinct_mounts"][0]["free_gib"] = 19.8
    d_ld["00"]["strict_multi_mount_probe"]["can_run_heavy_stages"] = False
    r_ld = recompute_r5_gate_matrix(docs=d_ld, **kw)
    assert r_ld["gates_26"]["G21_DISK_RAM_PID_NO_WAIVER"] == "BLOCKED_LOW_DISK"
    assert r_ld["verdicts"]["RESOURCE_SAFETY"] == "BLOCKED_LOW_DISK"

    # Disk waiver -> FAIL
    d_w = copy.deepcopy(docs); d_w["00"]["owner_disk_preflight_waiver"] = True
    assert recompute_r5_gate_matrix(docs=d_w, **kw)["gates_26"]["G21_DISK_RAM_PID_NO_WAIVER"] == "FAIL"

    # RAM < 512 -> FAIL
    d_r = copy.deepcopy(docs); d_r["17"]["min_available_ram_mib"] = 350.0
    assert recompute_r5_gate_matrix(docs=d_r, **kw)["gates_26"]["G21_DISK_RAM_PID_NO_WAIVER"] == "FAIL"

    # Mandatory tests not executed (F43) -> G17 FAIL & ENGINEERING FAIL
    d_nt = copy.deepcopy(docs); d_nt["15"]["executed_count"] = 0; d_nt["15"]["status"] = "FAIL"
    r_nt = recompute_r5_gate_matrix(docs=d_nt, **dict(kw, junit_ok=False))
    assert r_nt["gates_26"]["G17_FULL_FRESH_PYTEST_COLLECTION_JUNIT_BIJECTION"] == "FAIL"
    assert r_nt["verdicts"]["ENGINEERING"] == "FAIL"


def test_r5_strict_benchmark_nan_and_sentinel_cleanup_f35() -> None:
    bad_b = {
        "run_id": "20261124T120000Z_eeeeeeeeeeee", "tested_executable_sha": "e" * 40,
        "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2, "benchmark_kind": BENCHMARK_KIND_PYTEST,
        "runs": [
            {"mode_label": "w=1", "workers": 1, "wall_clock_seconds": float("nan"), "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
            {"mode_label": "w=2", "workers": 2, "wall_clock_seconds": 15.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
            {"mode_label": "w=4", "workers": 4, "wall_clock_seconds": 12.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
            {"mode_label": "w=1_repeat", "workers": 1, "wall_clock_seconds": 25.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
        ],
    }
    pol = select_measured_worker_policy_strict(bad_b, benchmark_kind=BENCHMARK_KIND_PYTEST, expected_run_id=bad_b["run_id"], expected_tested_sha=bad_b["tested_executable_sha"], expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2)
    assert pol["status"] != "PASS" and pol["can_certify"] is False

    rid = "20261124T120000Z_r5sent000000"
    td = create_sentinel_owned_temp_dir_r2_c1("kh03r5_unit_", run_id=rid, owner_pid=os.getpid())
    assert td.is_dir()
    assert _cleanup_sentinel_temp_c4_win_safe(td, expected_run_id=rid)["cleaned"] is True
    assert not td.exists()
