"""
aurum-desk/backend/tests/test_kh03_r6_a1_resource_async.py

AURUM DESK — KH-03.R6.A1 | M0 REAL BASELINE RED (529bdaf2), A1 PLANNING WORKLOAD
W1/W2/W4/REPEAT DETERMINISM, AST ZERO LITERAL FLAGS AUDIT & DISK WAIVER EXIT-2 GUARD
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from services.kh_03_r6_a1_acceptance_service import (
    audit_r6_a1_fault_files_no_literal_flags,
    run_m0_real_baseline_red_reproduction_529bdaf2,
    run_r6_a1_planning_workload_w1_w2_w4_benchmark,
)

APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parent
TESTS_DIR = Path(__file__).resolve().parent


def test_kh03_r6_a1_m0_baseline_red_reproduction_529bdaf2() -> None:
    res = run_m0_real_baseline_red_reproduction_529bdaf2(
        repo_root=REPO_ROOT,
        run_id="pytest_m0_red",
        tested_sha="0" * 40,
    )
    assert res["all_reproduced"] is True, f"M0_RED_INCOMPLETE:{res}"
    assert res["reproduced_count"] == 6
    assert sorted(res["cases"].keys()) == ["RED-G", "RED-H", "RED-I1", "RED-I2", "RED-J", "RED-K"]


def test_kh03_r6_a1_planning_workload_w1_w2_w4_determinism() -> None:
    bench = run_r6_a1_planning_workload_w1_w2_w4_benchmark(num_units=16)
    assert bench["valid"] is True
    assert bench["all_4_identical"] is True
    assert [r["mode_label"] for r in bench["runs"]] == ["w=1", "w=2", "w=4", "w=1_repeat"]
    assert len({r["semantic_result_sha256"] for r in bench["runs"]}) == 1


def test_kh03_r6_a1_ast_zero_literal_flags_and_cli_disk_waiver_exit2() -> None:
    a1_files = sorted(TESTS_DIR.glob("test_kh03_r6_a1_*.py"))
    assert len(a1_files) >= 5
    ast_res = audit_r6_a1_fault_files_no_literal_flags(a1_files)
    assert ast_res["clean"] is True, f"LITERAL_FLAGS_FOUND:{ast_res['hits']}"

    # CLI must refuse --owner-waive-disk-preflight with exit code 2
    p_cli = subprocess.run(
        [sys.executable, str(APP_ROOT / "tools" / "run_kh_03_r6_capture.py"), "--stage", "smoke", "--dry-run", "--owner-waive-disk-preflight"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=20,
    )
    assert p_cli.returncode == 2
