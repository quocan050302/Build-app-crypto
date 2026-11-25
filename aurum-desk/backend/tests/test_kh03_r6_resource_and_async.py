"""
aurum-desk/backend/tests/test_kh03_r6_resource_and_async.py

KH-03.R6 Resource Safety, Disk Waiver Exit-2 Rejection, Deterministic W1/W2/W4/W1_repeat R6
Fixture Ingest Benchmark, AST Literal Flag Audit & Legacy R1 Callgraph Guard Verification.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from services.kh_03_r3_legacy_research_guard import audit_kh03_r3_legacy_research_callgraph
from services.kh_03_r6_acceptance_service import (
    audit_r6_fault_files_no_literal_flags,
    run_r6_fixture_ingest_w1_w2_w4_benchmark,
)
from services.kh_03_r6_bounded_transport_service import GLOBAL_MAX_WORKERS_R6

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = REPO_ROOT / "aurum-desk"


def test_kh03_r6_disk_waiver_rejected_exit2_cli_and_env() -> None:
    acc_cli = APP_ROOT / "tools" / "run_kh_03_r6_acceptance.py"
    cap_cli = APP_ROOT / "tools" / "run_kh_03_r6_capture.py"

    # CLI flag disk waiver -> exit code 2
    p1 = subprocess.run([sys.executable, str(acc_cli), "--owner-waive-disk-preflight"], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=20)
    assert p1.returncode == 2
    assert "REJECTED_UNAPPROVED_AUTONOMOUS_DISK_WAIVER" in p1.stderr

    # Env var disk waiver -> exit code 2
    env = dict(os.environ)
    env["KH03_R6_OWNER_WAIVE_DISK_PREFLIGHT"] = "1"
    p2 = subprocess.run([sys.executable, str(acc_cli), "--phase", "offline"], cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=20)
    assert p2.returncode == 2

    # Capture CLI env approval bypass -> exit code 2
    env2 = dict(os.environ)
    env2["KH03_R6_ALLOW_LIVE"] = "1"
    p3 = subprocess.run([sys.executable, str(cap_cli), "--stage", "smoke", "--dry-run"], cwd=str(REPO_ROOT), env=env2, capture_output=True, text=True, timeout=20)
    assert p3.returncode == 2


def test_kh03_r6_ingest_benchmark_w1_w2_w4_deterministic() -> None:
    bench = run_r6_fixture_ingest_w1_w2_w4_benchmark(num_partitions=8, bars_per_partition=40)
    assert bench["valid"] is True
    assert bench["all_4_identical"] is True
    assert len(bench["runs"]) == 4
    assert [r["mode_label"] for r in bench["runs"]] == ["w=1", "w=2", "w=4", "w=1_repeat"]
    assert 1 <= bench["applied_safe_workers_cap"] <= GLOBAL_MAX_WORKERS_R6


def test_kh03_r6_ast_no_literal_flags_and_no_r1_callers() -> None:
    r6_test_dir = APP_ROOT / "backend" / "tests"
    r6_files = sorted(r6_test_dir.glob("test_kh03_r6_*.py"))
    assert len(r6_files) == 7
    ast_res = audit_r6_fault_files_no_literal_flags(r6_files)
    assert ast_res["clean"] is True, ast_res["hits"]

    cg = audit_kh03_r3_legacy_research_callgraph(REPO_ROOT)
    assert int(cg.get("unauthorized_callers_count", 0)) == 0
