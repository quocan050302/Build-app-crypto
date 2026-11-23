"""
aurum-desk/backend/tests/test_kh03_r3_c1_policy_safety.py

KH-03.R3.C1 Resource Safety, Cap Separation, Locked Surface & Cross-Platform Temp Tests (P14, P15, P27, P30):
Verifies measured-fastest W4 vs safe full-suite cap <=3 without speedup extrapolation, fail-closed
disk (<20.5 / <20.0 GiB) & RAM (<512 MiB) & waiver rejection, CLI waiver refusal (exit 2),
locked Entry/SL/TP/dataset immutability, and adversarial temp/sentinel/atomic-write safety.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict
import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from domain_calculator import CostAssumptions
from services.kh_02_r2_research_integrity_service import (
    CANONICAL_DATASET_REL_PATH,
    CANONICAL_DATASET_SHA256_KH02_R2,
    LOCKED_KEEP_AS_IS_FILES_KH02_R2,
    create_sentinel_owned_temp_dir_r2_c1,
)
from services.kh_03_r3_c1_worker_policy_service import (
    BENCHMARK_KIND_PYTEST,
    BENCHMARK_KIND_SCIENCE,
    select_measured_worker_policy_strict,
)
from tools.fresh_junit_io import write_json_durable_atomic
from tools.run_kh_02_r2_c4_acceptance import _cleanup_sentinel_temp_c4_win_safe


def _valid_pytest_w4_fastest() -> Dict[str, Any]:
    sha = "c" * 64
    return {
        "all_4_runs_identical": True,
        "runs": [
            {"mode_label": "w=1", "workers": 1, "worker_pids": [11], "wall_clock_seconds": 52.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": sha},
            {"mode_label": "w=2", "workers": 2, "worker_pids": [21, 22], "wall_clock_seconds": 40.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": sha},
            {"mode_label": "w=4", "workers": 4, "worker_pids": [41, 42, 43, 44], "wall_clock_seconds": 32.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": sha},
            {"mode_label": "w=1_repeat", "workers": 1, "worker_pids": [51], "wall_clock_seconds": 53.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": sha},
        ],
    }


def test_p14_w4_fastest_subset_capped_at_3_full_suite_no_extrapolation():
    """P14: Subset measures W4 fastest, full-suite cap=3 -> measured W4, applied<=3, NO full-suite speedup extrapolation."""
    pol = select_measured_worker_policy_strict(
        _valid_pytest_w4_fastest(),
        benchmark_kind=BENCHMARK_KIND_PYTEST,
        system_limits={
            "configured_child_process_cap": 4,
            "currently_allowed_worker_cap": 4,
            "full_suite_safe_worker_cap": 3,
            "preflight_disk_free_gib": 30.0,
            "disk_free_gib": 29.5,
            "available_ram_mib": 4096.0,
        },
    )
    assert pol["status"] == "PASS"
    assert pol["measured_fastest_mode"] == "w=4"
    assert pol["safe_worker_cap"] == 3
    assert pol["applied_worker_count"] == 3
    assert pol["applied_worker_count_is_benchmark_measured"] is False
    assert pol["full_suite_speedup_extrapolated"] is False

    # Tight RAM-derived cap = 2 forces applied <= 2 even while faithfully reporting measured W4 (§11G)
    pol_ram = select_measured_worker_policy_strict(
        _valid_pytest_w4_fastest(),
        benchmark_kind=BENCHMARK_KIND_PYTEST,
        system_limits={
            "configured_child_process_cap": 4,
            "ram_derived_worker_cap": 2,
            "full_suite_safe_worker_cap": 3,
            "preflight_disk_free_gib": 30.0,
            "disk_free_gib": 29.5,
            "available_ram_mib": 900.0,
        },
    )
    assert pol_ram["status"] == "PASS"
    assert pol_ram["measured_fastest_mode"] == "w=4"
    assert pol_ram["applied_worker_count"] == 2


@pytest.mark.parametrize(
    "bad_limits,expected_tok",
    [
        ({"preflight_disk_free_gib": 19.8, "disk_free_gib": 19.8, "available_ram_mib": 4096.0}, "PREFLIGHT_DISK_FLOOR_BREACHED"),
        ({"preflight_disk_free_gib": 22.0, "disk_free_gib": 19.5, "available_ram_mib": 4096.0}, "RUNTIME_DISK_FLOOR_BREACHED"),
        ({"preflight_disk_free_gib": 30.0, "disk_free_gib": 29.0, "available_ram_mib": 400.0}, "RAM_FLOOR_BREACHED"),
        ({"owner_disk_preflight_waiver": True, "preflight_disk_free_gib": 30.0, "disk_free_gib": 29.0, "available_ram_mib": 4096.0}, "UNAPPROVED_OWNER_DISK_WAIVER_FORBIDDEN"),
        ({"configured_child_process_cap": 8, "preflight_disk_free_gib": 30.0, "disk_free_gib": 29.0, "available_ram_mib": 4096.0}, "WORKER_CAP_EXCEEDS_GLOBAL_MAX_4"),
        ({"disk_floor_gib": 5.0}, "DISK_FLOOR_BELOW_STRICT_MINIMUM"),
        ({"ram_floor_mib": 128.0}, "RAM_FLOOR_BELOW_STRICT_MINIMUM"),
    ],
)
def test_p15_resource_floor_or_waiver_breach_blocks_fail_closed(bad_limits, expected_tok):
    """P15: Disk/RAM floor breach, waiver, or cap>4 MUST block with BLOCKED_RESOURCE and applied=0 (no override)."""
    pol = select_measured_worker_policy_strict(
        _valid_pytest_w4_fastest(),
        benchmark_kind=BENCHMARK_KIND_PYTEST,
        system_limits=bad_limits,
    )
    assert pol["status"] == "BLOCKED_RESOURCE"
    assert pol["can_certify"] is False
    assert pol["applied_worker_count"] == 0
    assert any(expected_tok in v for v in pol["violations"])


def test_p15_c1_cli_refuses_disk_waiver_flag_and_env():
    """C1 orchestrator CLI MUST exit 2 immediately on --owner-waive-disk-preflight or waiver env vars."""
    script = APP_ROOT / "tools" / "run_kh_03_r3_c1_acceptance.py"
    assert script.is_file()

    p_flag = subprocess.run(
        [sys.executable, str(script), "--owner-waive-disk-preflight"],
        cwd=str(APP_ROOT), capture_output=True, text=True, timeout=20,
    )
    assert p_flag.returncode == 2

    for env_k in ("KH02_OWNER_WAIVE_DISK_PREFLIGHT", "KH03_OWNER_WAIVE_DISK_PREFLIGHT", "KH03_C1_OWNER_WAIVE_DISK_PREFLIGHT"):
        env = os.environ.copy()
        env[env_k] = "1"
        p_env = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=20,
        )
        assert p_env.returncode == 2, f"Env {env_k}=1 MUST exit 2"


def test_p27_locked_entry_sl_tp_and_canonical_dataset_sha_intact():
    """P27: Locked production/Entry/SL/TP files, CostAssumptions defaults, and canonical dataset SHA stay untouched."""
    ds_p = REPO_ROOT / CANONICAL_DATASET_REL_PATH
    assert ds_p.is_file()
    actual_ds_sha = hashlib.sha256(ds_p.read_bytes()).hexdigest()
    assert actual_ds_sha == CANONICAL_DATASET_SHA256_KH02_R2

    for rel in LOCKED_KEEP_AS_IS_FILES_KH02_R2:
        assert (REPO_ROOT / rel).is_file(), f"Locked file missing: {rel}"

    c = CostAssumptions()
    assert abs(float(c.maker_fee_rate) - 0.0002) <= 1e-12
    assert abs(float(c.taker_fee_rate) - 0.0004) <= 1e-12
    assert abs(float(c.slippage_usd) - 0.10) <= 1e-12


def test_p30_cross_platform_sentinel_ownership_long_path_and_atomic_write_safety(tmp_path):
    """P30: Sentinel mismatch raises PermissionError & preserves data; owned long-path dir cleans up; atomic write leaves no .tmp."""
    # 1. Missing sentinel MUST raise PermissionError and NEVER delete unrelated path!
    foreign_dir = tmp_path / "foreign_unrelated_dir"
    foreign_dir.mkdir()
    (foreign_dir / "keep_me.txt").write_text("user_data", encoding="utf-8")
    with pytest.raises(PermissionError, match="UNOWNED_TEMP_DIR_MISSING_SENTINEL"):
        _cleanup_sentinel_temp_c4_win_safe(foreign_dir, expected_run_id="RUN_ABC_123")
    assert (foreign_dir / "keep_me.txt").is_file()

    # 2. Owned sentinel with mismatched run_id MUST raise PermissionError and NEVER delete!
    owned = create_sentinel_owned_temp_dir_r2_c1("kh03c1_p30_", run_id="RUN_C1_TEST_P30", base_dir=tmp_path)
    nested = owned / ("a" * 40) / ("b" * 40) / ("c" * 40)
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "payload.json").write_text('{"ok": true}', encoding="utf-8")
    with pytest.raises(PermissionError):
        _cleanup_sentinel_temp_c4_win_safe(owned, expected_run_id="DIFFERENT_RUN_ID")
    assert (nested / "payload.json").is_file()

    # 3. Matching sentinel run_id cleans up nested long-path directory cleanly
    cl_owned = _cleanup_sentinel_temp_c4_win_safe(owned, expected_run_id="RUN_C1_TEST_P30")
    assert cl_owned["cleaned"] is True
    assert not owned.exists()

    # 4. Atomic durable JSON write creates exact file and leaves no stray .tmp file
    out_json = tmp_path / "atomic_test.json"
    write_json_durable_atomic(out_json, {"spec_version": "KH-03.R3.C1-v1.0", "ok": True})
    assert out_json.is_file()
    assert json.loads(out_json.read_text("utf-8"))["ok"] is True
    assert not list(tmp_path.glob("*.tmp"))
