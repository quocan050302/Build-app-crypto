"""
aurum-desk/backend/tests/test_kh03_r4_resource_and_async.py

Tests KH-03.R4 resource safety (no disk waiver exit code 2, disk/RAM/child-cap fail-closed,
sentinel temp creation & zero-orphan cleanup), serial vs asyncio concurrent semantic determinism,
and C2/C1 regression lock (legacy R3 fail-closed policy & strict worker selection).
"""
from __future__ import annotations

import asyncio
import copy
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

from services.kh_02_r2_research_integrity_service import (
    CANONICAL_DATASET_SHA256_KH02_R2,
    create_sentinel_owned_temp_dir_r2_c1,
)
from services.kh_03_r3_c1_worker_policy_service import (
    BENCHMARK_KIND_PYTEST,
    select_measured_worker_policy_strict,
)
from services.kh_03_r4_economic_diagnostic_service import (
    recompute_r4_decimal_economic_bridge,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tests.test_kh03_r4_economic_accounting import _make_synthetic_anchor_forensic_rows
from tools.kh_03_r4_independent_verifier import recompute_r4_gates_from_raw
from tools.run_kh_02_r2_c4_acceptance import _cleanup_sentinel_temp_c4_win_safe


def test_r4_cli_and_env_disk_waiver_rejected_exit_2() -> None:
    p_cli = subprocess.run(
        [sys.executable, str(APP_ROOT / "tools" / "run_kh_03_r4_acceptance.py"), "--owner-waive-disk-preflight"],
        cwd=str(APP_ROOT), capture_output=True, text=True, timeout=30,
    )
    assert p_cli.returncode == 2
    assert "REJECTED_UNAPPROVED_AUTONOMOUS_DISK_WAIVER" in p_cli.stderr

    env = os.environ.copy()
    env["KH03_R4_OWNER_WAIVE_DISK_PREFLIGHT"] = "true"
    p_env = subprocess.run(
        [sys.executable, str(APP_ROOT / "tools" / "run_kh_03_r4_acceptance.py")],
        cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=30,
    )
    assert p_env.returncode == 2
    assert "REJECTED_UNAPPROVED_AUTONOMOUS_DISK_WAIVER" in p_env.stderr


def test_r4_sentinel_temp_dir_owned_cleanup_and_mismatch_refusal() -> None:
    rid = "20261123T235900Z_r4senttest00"
    td = create_sentinel_owned_temp_dir_r2_c1("kh03r4_test_", run_id=rid, owner_pid=os.getpid())
    assert td.is_dir()
    # Mismatched run_id MUST refuse deletion (PermissionError)
    with pytest.raises(PermissionError):
        _cleanup_sentinel_temp_c4_win_safe(td, expected_run_id="WRONG_RUN_ID")
    assert td.is_dir()
    # Matching run_id cleanly deletes sentinel temp dir with zero orphans
    res = _cleanup_sentinel_temp_c4_win_safe(td, expected_run_id=rid)
    assert res["cleaned"] is True
    assert not td.exists()


def test_r4_serial_vs_async_diagnostic_semantic_sha_identical() -> None:
    rows = _make_synthetic_anchor_forensic_rows()

    def _compute_once(tag: int) -> str:
        out = recompute_r4_decimal_economic_bridge(
            rows, dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2, ledger_meta={"tag": tag}
        )
        return canonical_hash({k: v for k, v in out.items() if k != "linked_forensic_ledger"})

    serial_shas = [_compute_once(i) for i in range(4)]

    async def _run_async() -> list[str]:
        return await asyncio.gather(*(asyncio.to_thread(_compute_once, i) for i in range(4)))

    async_shas = asyncio.run(_run_async())
    assert len(set(serial_shas)) == 1
    assert async_shas == serial_shas


def test_r4_resource_gate_g25_blocks_low_disk_ram_child_and_waiver(tmp_path: Path) -> None:
    from tests.test_kh03_r4_independent_verifier_mutations import build_synthetic_valid_kh03_r4_run_dir
    import json

    rdir = tmp_path / "r4_res_gate_test"
    build_synthetic_valid_kh03_r4_run_dir(rdir)
    docs = {p.name[:2]: json.loads(p.read_text("utf-8")) for p in sorted(rdir.glob("[0-9][0-9]_*.json"))}
    base_kwargs = dict(
        ledger_audit={"valid": True, "violations": []},
        econ_audit={"valid": True, "violations": []},
        claims_audit={"valid": True, "violations": []},
        pytest_recomp={"valid": True, "violations": []},
        science_recomp={"valid": True, "violations": []},
        mem_audit={"status": "PASS", "below_floor_samples_count": 0, "max_workers_observed_in_samples": 3},
        junit_ok=True,
        for_final_verifier_manifest=True,
    )

    # Clean PASS
    g_ok = recompute_r4_gates_from_raw(docs=docs, **base_kwargs)
    assert g_ok["gates_30"]["G25_DISK_RAM_TEMP_ORPHAN_LOCK"] == "PASS"

    # Low preflight disk -> BLOCKED_LOW_DISK (never PASS, no waiver)
    d_ld = copy.deepcopy(docs)
    d_ld["00"]["strict_multi_mount_probe"]["distinct_mounts"][0]["free_gib"] = 19.2
    d_ld["00"]["strict_multi_mount_probe"]["distinct_mounts"][0]["projected_post_reserve_gib"] = 18.7
    d_ld["00"]["strict_multi_mount_probe"]["can_run_heavy_stages"] = False
    g_ld = recompute_r4_gates_from_raw(docs=d_ld, **base_kwargs)
    assert g_ld["gates_30"]["G25_DISK_RAM_TEMP_ORPHAN_LOCK"] == "BLOCKED_LOW_DISK"

    # Waiver -> FAIL
    d_w = copy.deepcopy(docs)
    d_w["00"]["owner_disk_preflight_waiver"] = True
    assert recompute_r4_gates_from_raw(docs=d_w, **base_kwargs)["gates_30"]["G25_DISK_RAM_TEMP_ORPHAN_LOCK"] == "FAIL"

    # RAM < 512 MiB -> FAIL
    d_ram = copy.deepcopy(docs)
    d_ram["16"]["min_available_ram_mib"] = 420.0
    assert recompute_r4_gates_from_raw(docs=d_ram, **base_kwargs)["gates_30"]["G25_DISK_RAM_TEMP_ORPHAN_LOCK"] == "FAIL"

    # Children > 4 -> FAIL
    d_ch = copy.deepcopy(docs)
    d_ch["16"]["max_descendant_children"] = 5
    assert recompute_r4_gates_from_raw(docs=d_ch, **base_kwargs)["gates_30"]["G25_DISK_RAM_TEMP_ORPHAN_LOCK"] == "FAIL"


def test_r4_preserves_c1_c2_strict_worker_policy_guard() -> None:
    bad_pb = {
        "run_id": "20261123T235000Z_cccccccccccc",
        "tested_executable_sha": "c" * 40,
        "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
        "benchmark_kind": BENCHMARK_KIND_PYTEST,
        "runs": [
            {"mode_label": "w=1", "workers": 1, "wall_clock_seconds": 25.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
            {"mode_label": "w=2", "workers": 2, "wall_clock_seconds": -1.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
            {"mode_label": "w=4", "workers": 4, "wall_clock_seconds": 12.0, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
            {"mode_label": "w=1_repeat", "workers": 1, "wall_clock_seconds": 24.5, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "1" * 64},
        ],
    }
    pol = select_measured_worker_policy_strict(
        bad_pb, benchmark_kind=BENCHMARK_KIND_PYTEST,
        expected_run_id=bad_pb["run_id"], expected_tested_sha=bad_pb["tested_executable_sha"],
        expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2,
    )
    assert pol["status"] != "PASS"
    assert pol["can_certify"] is False
