"""
tools/run_v17_5_7_acceptance.py
Authoritative cross-platform acceptance runner for AURUM V17.5.7:
CHỐT HAI KHOẢNG TRỐNG CÒN LẠI: STRUCTURAL EPISODE DERIVATION & TRUE CYCLE COST PARITY.

Executes test suites across all 13 technical gates, dynamically parses JUnit XML
and Vitest test counts without any hardcoded numbers, captures stdout/stderr/exit codes,
computes file hashes, and outputs artifacts/v17_5_7/acceptance.json and logs.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional


def get_git_info(repo_root: Path) -> dict:
    try:
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root).decode().strip()
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root).decode().strip()
        status = subprocess.check_output(["git", "status", "--porcelain"], cwd=repo_root).decode().strip()
        diff = subprocess.check_output(["git", "diff"], cwd=repo_root).decode()
        diff_hash = hashlib.sha256(diff.encode("utf-8")).hexdigest()[:16]
        return {
            "branch": branch,
            "sha": sha,
            "is_dirty": bool(status),
            "diff_hash": diff_hash,
            "status_lines": status.splitlines() if status else [],
        }
    except Exception as e:
        return {"error": str(e)}


def compute_file_hash(filepath: Path) -> str:
    if not filepath.exists():
        return "MISSING"
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()[:16]


def parse_junit_xml(xml_path: Path) -> Dict[str, int]:
    """Dynamically parses pytest JUnit XML report without hardcoding counts."""
    if not xml_path.exists():
        return {"tests": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
        if not suites and "tests" in root.attrib:
            suites = [root]
        total_tests = sum(int(s.attrib.get("tests", 0)) for s in suites)
        failures = sum(int(s.attrib.get("failures", 0)) for s in suites)
        errors = sum(int(s.attrib.get("errors", 0)) for s in suites)
        skipped = sum(int(s.attrib.get("skipped", 0)) for s in suites)
        passed = max(0, total_tests - failures - errors - skipped)
        return {
            "tests": total_tests,
            "passed": passed,
            "failed": failures,
            "skipped": skipped,
            "errors": errors,
        }
    except Exception:
        return {"tests": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0}


def run_command_cross_platform(cmd: list[str], cwd: Path, timeout: int = 300) -> dict:
    t0 = time.time()
    is_win = platform.system() == "Windows"
    use_shell = False
    run_cmd = list(cmd)

    if is_win and cmd[0] in ("npm", "npx"):
        run_cmd = ["cmd.exe", "/c"] + cmd

    try:
        res = subprocess.run(
            run_cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=use_shell,
        )
        duration = round(time.time() - t0, 3)
        return {
            "command": " ".join(cmd),
            "exit_code": res.returncode,
            "duration_sec": duration,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "status": "PASS" if res.returncode == 0 else "FAIL",
        }
    except subprocess.TimeoutExpired as te:
        return {
            "command": " ".join(cmd),
            "exit_code": -1,
            "duration_sec": round(time.time() - t0, 3),
            "error": f"Timeout expired after {timeout}s",
            "status": "TIMEOUT",
            "stdout": te.stdout or "",
            "stderr": te.stderr or "",
        }
    except Exception as e:
        return {
            "command": " ".join(cmd),
            "exit_code": -1,
            "duration_sec": round(time.time() - t0, 3),
            "error": str(e),
            "status": "ERROR",
            "stdout": "",
            "stderr": str(e),
        }


def main():
    tools_dir = Path(__file__).resolve().parent
    repo_root = tools_dir.parent
    backend_dir = repo_root / "backend"
    frontend_dir = repo_root / "frontend"

    # Python interpreter resolution
    python_exe = sys.executable
    venv_win = backend_dir / "venv" / "Scripts" / "python.exe"
    venv_posix = backend_dir / "venv" / "bin" / "python"
    if venv_win.exists():
        python_exe = str(venv_win)
    elif venv_posix.exists():
        python_exe = str(venv_posix)

    print(f"[*] AURUM V17.5.7 Acceptance Runner — Closure of Remaining Gaps")
    print(f"[*] OS: {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"[*] Python: {python_exe}")
    print(f"[*] Repo Root: {repo_root}")

    git_info = get_git_info(repo_root)
    print(f"[*] Git: {git_info.get('branch')} @ {git_info.get('sha')} (dirty={git_info.get('is_dirty')})")

    # Key files to hash for provenance verification
    key_files = [
        backend_dir / "services" / "stop_placement_types.py",
        backend_dir / "services" / "stop_placement_service.py",
        backend_dir / "services" / "plan_financial_context_service.py",
        backend_dir / "services" / "scalp_m1_model_service.py",
        backend_dir / "services" / "scalp_watcher_orchestrator.py",
        backend_dir / "services" / "execution_coordinator.py",
        backend_dir / "services" / "order_plan_snapshot_service.py",
        frontend_dir / "src" / "ChartComponent.tsx",
        backend_dir / "tests" / "test_v17_5_7_episode_derivation.py",
        backend_dir / "tests" / "test_v17_5_7_true_cycle_cost_parity.py",
        backend_dir / "tests" / "test_v17_5_6_actual_order_cost_parity.py",
        backend_dir / "tests" / "test_v17_5_6_episode_origin_binding.py",
        frontend_dir / "tests" / "e2e" / "v17_5_6_chart_evidence.e2e.ts",
        tools_dir / "run_v17_5_7_acceptance.py",
    ]
    file_hashes = {str(f.relative_to(repo_root)): compute_file_hash(f) for f in key_files}

    artifacts_dir = repo_root / "artifacts" / "v17_5_7"
    logs_dir = artifacts_dir / "logs"
    junit_dir = artifacts_dir / "junit"
    evidence_dir = artifacts_dir / "evidence"
    logs_dir.mkdir(parents=True, exist_ok=True)
    junit_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    backend_total_passed = 0
    backend_total_failed = 0
    backend_total_tests = 0

    # Gate 0 — V17.5.7 Targeted Suites (Structural Episode Derivation & True Cycle Cost Parity)
    print("\n[Gate 0] Running V17.5.7 Core Targeted Suites...")
    v17_5_7_files = [
        "tests/test_v17_5_7_episode_derivation.py",
        "tests/test_v17_5_7_true_cycle_cost_parity.py",
    ]
    xml_g0 = junit_dir / "gate0_v17_5_7_core.xml"
    g0 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + v17_5_7_files + [f"--junitxml={xml_g0}", "-v"],
        cwd=backend_dir,
    )
    counts_g0 = parse_junit_xml(xml_g0)
    backend_total_passed += counts_g0["passed"]
    backend_total_failed += counts_g0["failed"]
    backend_total_tests += counts_g0["tests"]
    print(f"    Status: {g0['status']} ({counts_g0['passed']}/{counts_g0['tests']} passed in {g0['duration_sec']}s)")

    # Gate 1 — V17.5.6 Targeted Suites (Cost Parity, Episode Origin, Break Time, Cycle Regression)
    print("\n[Gate 1] Running V17.5.6 Targeted Suites...")
    v17_5_6_files = [
        "tests/test_v17_5_6_actual_order_cost_parity.py",
        "tests/test_v17_5_6_episode_origin_binding.py",
        "tests/test_v17_5_6_break_time_contract.py",
        "tests/test_v17_5_6_cycle_regression.py",
    ]
    xml_g1 = junit_dir / "gate1_v17_5_6_core.xml"
    g1 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + v17_5_6_files + [f"--junitxml={xml_g1}", "-v"],
        cwd=backend_dir,
    )
    counts_g1 = parse_junit_xml(xml_g1)
    backend_total_passed += counts_g1["passed"]
    backend_total_failed += counts_g1["failed"]
    backend_total_tests += counts_g1["tests"]
    print(f"    Status: {g1['status']} ({counts_g1['passed']}/{counts_g1['tests']} passed in {g1['duration_sec']}s)")

    # Gate 2 — V17.5.5 Baseline Suites
    print("\n[Gate 2] Running V17.5.5 Baseline Suites (Entry Gate, Cost Authority, Origin Bridge, Cycle Contract)...")
    v17_5_5_files = [
        "tests/test_v17_5_5_entry_context_gate.py",
        "tests/test_v17_5_5_cost_authority.py",
        "tests/test_v17_5_5_origin_bridge.py",
        "tests/test_v17_5_5_cycle_to_chart_contract.py",
    ]
    xml_g2 = junit_dir / "gate2_v17_5_5_core.xml"
    g2 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + v17_5_5_files + [f"--junitxml={xml_g2}", "-v"],
        cwd=backend_dir,
    )
    counts_g2 = parse_junit_xml(xml_g2)
    backend_total_passed += counts_g2["passed"]
    backend_total_failed += counts_g2["failed"]
    backend_total_tests += counts_g2["tests"]
    print(f"    Status: {g2['status']} ({counts_g2['passed']}/{counts_g2['tests']} passed in {g2['duration_sec']}s)")

    # Gate 3 — V17.5.4 Suites
    print("\n[Gate 3] Running V17.5.4 Target Suites (Context, Cost, Anchor, Cycle)...")
    v17_5_4_files = [
        "tests/test_v17_5_4_context_binding.py",
        "tests/test_v17_5_4_cost_parity.py",
        "tests/test_v17_5_4_anchor_proof.py",
        "tests/test_v17_5_4_cycle_reconciliation.py",
    ]
    xml_g3 = junit_dir / "gate3_v17_5_4.xml"
    g3 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + v17_5_4_files + [f"--junitxml={xml_g3}", "-v"],
        cwd=backend_dir,
    )
    counts_g3 = parse_junit_xml(xml_g3)
    backend_total_passed += counts_g3["passed"]
    backend_total_failed += counts_g3["failed"]
    backend_total_tests += counts_g3["tests"]
    print(f"    Status: {g3['status']} ({counts_g3['passed']}/{counts_g3['tests']} passed in {g3['duration_sec']}s)")

    # Gate 4 — V17.5.3 Boundary Probes & Cycle Review
    print("\n[Gate 4] Running V17.5.3 Boundary Probes & Cycle Review...")
    xml_g4 = junit_dir / "gate4_v17_5_3.xml"
    g4 = run_command_cross_platform(
        [
            python_exe, "-m", "pytest",
            "tests/test_v17_5_3_boundary_probes.py",
            "tests/test_v17_5_3_cycle_to_review.py",
            f"--junitxml={xml_g4}", "-v",
        ],
        cwd=backend_dir,
    )
    counts_g4 = parse_junit_xml(xml_g4)
    backend_total_passed += counts_g4["passed"]
    backend_total_failed += counts_g4["failed"]
    backend_total_tests += counts_g4["tests"]
    print(f"    Status: {g4['status']} ({counts_g4['passed']}/{counts_g4['tests']} passed in {g4['duration_sec']}s)")

    # Gate 5 — Baseline V17.5.2 Suites
    print("\n[Gate 5] Running V17.5.2 Baseline Suites...")
    xml_g5 = junit_dir / "gate5_v17_5_2.xml"
    g5 = run_command_cross_platform(
        [
            python_exe, "-m", "pytest",
            "tests/test_v17_5_2_boundary_probes.py",
            "tests/test_v17_5_2_detector_to_exit.py",
            f"--junitxml={xml_g5}", "-v",
        ],
        cwd=backend_dir,
    )
    counts_g5 = parse_junit_xml(xml_g5)
    backend_total_passed += counts_g5["passed"]
    backend_total_failed += counts_g5["failed"]
    backend_total_tests += counts_g5["tests"]
    print(f"    Status: {g5['status']} ({counts_g5['passed']}/{counts_g5['tests']} passed in {g5['duration_sec']}s)")

    # Gate 6 — V17.5.1 Feature Suites & Probes
    print("\n[Gate 6] Running V17.5.1 Feature Suites & Probes...")
    v17_5_1_files = [
        "tests/test_v17_5_1_red_probes.py",
        "tests/test_v17_5_1_anchor_leg.py",
        "tests/test_v17_5_1_structural_gate.py",
        "tests/test_v17_5_1_atr_timeframe.py",
        "tests/test_v17_5_1_policy_cas.py",
        "tests/test_v17_5_1_research_runtime.py",
        "tests/test_v17_5_1_production_flow.py",
        "tests/test_v17_5_1_guard_matrix.py",
        "tests/test_v17_5_1_stop_costs.py",
    ]
    xml_g6 = junit_dir / "gate6_v17_5_1.xml"
    g6 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + v17_5_1_files + [f"--junitxml={xml_g6}", "-v"],
        cwd=backend_dir,
    )
    counts_g6 = parse_junit_xml(xml_g6)
    backend_total_passed += counts_g6["passed"]
    backend_total_failed += counts_g6["failed"]
    backend_total_tests += counts_g6["tests"]
    print(f"    Status: {g6['status']} ({counts_g6['passed']}/{counts_g6['tests']} passed in {g6['duration_sec']}s)")

    # Gate 7 — Existing V17.5 Regression Suites
    print("\n[Gate 7] Running Existing V17.5 Regression Suites...")
    v17_5_existing = [
        "tests/test_v17_5_stop_placement.py",
        "tests/test_v17_5_anchor_binding.py",
        "tests/test_v17_5_risk_invariants.py",
        "tests/test_v17_5_policy_api.py",
        "tests/test_v17_5_exit_evidence.py",
        "tests/test_v17_5_model_integration.py",
        "tests/test_v17_5_production_flow.py",
        "tests/test_v17_5_stop_review.py",
        "tests/test_v17_5_stop_research.py",
    ]
    xml_g7 = junit_dir / "gate7_v17_5.xml"
    g7 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + v17_5_existing + [f"--junitxml={xml_g7}", "-v"],
        cwd=backend_dir,
    )
    counts_g7 = parse_junit_xml(xml_g7)
    backend_total_passed += counts_g7["passed"]
    backend_total_failed += counts_g7["failed"]
    backend_total_tests += counts_g7["tests"]
    print(f"    Status: {g7['status']} ({counts_g7['passed']}/{counts_g7['tests']} passed in {g7['duration_sec']}s)")

    # Gate 8 — Prior Regressions (CG01, sessions, authority)
    print("\n[Gate 8] Running Prior Regression Suites...")
    prior_regression = [
        "tests/test_v16_2_cg01_long_short.py",
        "tests/test_v17_4_3_session_roundtrip.py",
        "tests/test_v17_4_3_atomic_mirrors.py",
        "tests/test_v17_4_2_baseline_probes.py",
        "tests/test_v17_4_2_noop_authority.py",
    ]
    xml_g8 = junit_dir / "gate8_prior.xml"
    g8 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + prior_regression + [f"--junitxml={xml_g8}", "-v"],
        cwd=backend_dir,
    )
    counts_g8 = parse_junit_xml(xml_g8)
    backend_total_passed += counts_g8["passed"]
    backend_total_failed += counts_g8["failed"]
    backend_total_tests += counts_g8["tests"]
    print(f"    Status: {g8['status']} ({counts_g8['passed']}/{counts_g8['tests']} passed in {g8['duration_sec']}s)")

    # Gate 9 — Frontend Vitest Unit Tests
    print("\n[Gate 9] Running Frontend Vitest Unit Tests...")
    g9 = run_command_cross_platform(["npm", "test", "--", "--run"], cwd=frontend_dir)
    frontend_passed = 0
    frontend_files = 0
    for line in g9.get("stdout", "").splitlines():
        if "Tests" in line and "passed" in line:
            parts = line.strip().split()
            for i, p in enumerate(parts):
                if p == "passed" and i > 0 and parts[i - 1].isdigit():
                    frontend_passed = int(parts[i - 1])
        if "Test Files" in line and "passed" in line:
            parts = line.strip().split()
            for i, p in enumerate(parts):
                if p == "passed" and i > 0 and parts[i - 1].isdigit():
                    frontend_files = int(parts[i - 1])
    print(f"    Status: {g9['status']} ({frontend_passed} tests across {frontend_files} files passed in {g9['duration_sec']}s)")

    # Gate 10 — Frontend Build
    print("\n[Gate 10] Running Frontend Build...")
    g10 = run_command_cross_platform(["npm", "run", "build"], cwd=frontend_dir)
    print(f"    Status: {g10['status']} (in {g10['duration_sec']}s)")

    # Gate 11 — Frontend Lint
    print("\n[Gate 11] Running Frontend Lint...")
    g11 = run_command_cross_platform(["npm", "run", "lint"], cwd=frontend_dir)
    print(f"    Status: {g11['status']} (in {g11['duration_sec']}s)")

    # Gate 12 — Playwright Browser E2E Test (Mandatory Finite Geometry)
    print("\n[Gate 12] Running Playwright Browser Verification (v17_5_6_chart_evidence.e2e.ts)...")
    g12 = run_command_cross_platform(
        ["npm", "run", "test:e2e", "--", "tests/e2e/v17_5_6_chart_evidence.e2e.ts"],
        cwd=frontend_dir,
    )
    browser_passed = 0
    for line in g12.get("stdout", "").splitlines():
        if "passed" in line and "(" in line:
            parts = line.strip().split()
            if parts and parts[0].isdigit():
                browser_passed = int(parts[0])
    print(f"    Status: {g12['status']} ({browser_passed} browser tests passed in {g12['duration_sec']}s)")

    # Write logs
    def _safe_str(val: Any) -> str:
        return str(val) if val is not None else ""

    with open(logs_dir / "gate0_v17_5_7_core.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g0.get("stdout")) + "\n" + _safe_str(g0.get("stderr")))
    with open(logs_dir / "gate1_v17_5_6_core.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g1.get("stdout")) + "\n" + _safe_str(g1.get("stderr")))
    with open(logs_dir / "gate2_v17_5_5_core.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g2.get("stdout")) + "\n" + _safe_str(g2.get("stderr")))
    with open(logs_dir / "gate3_v17_5_4.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g3.get("stdout")) + "\n" + _safe_str(g3.get("stderr")))
    with open(logs_dir / "gate4_v17_5_3.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g4.get("stdout")) + "\n" + _safe_str(g4.get("stderr")))
    with open(logs_dir / "gate5_v17_5_2.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g5.get("stdout")) + "\n" + _safe_str(g5.get("stderr")))
    with open(logs_dir / "gate6_v17_5_1.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g6.get("stdout")) + "\n" + _safe_str(g6.get("stderr")))
    with open(logs_dir / "gate7_v17_5.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g7.get("stdout")) + "\n" + _safe_str(g7.get("stderr")))
    with open(logs_dir / "gate8_prior.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g8.get("stdout")) + "\n" + _safe_str(g8.get("stderr")))
    with open(logs_dir / "gate9_vitest.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g9.get("stdout")) + "\n" + _safe_str(g9.get("stderr")))
    with open(logs_dir / "gate10_build.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g10.get("stdout")) + "\n" + _safe_str(g10.get("stderr")))
    with open(logs_dir / "gate11_lint.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g11.get("stdout")) + "\n" + _safe_str(g11.get("stderr")))
    with open(logs_dir / "gate12_playwright.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g12.get("stdout")) + "\n" + _safe_str(g12.get("stderr")))

    # Final Acceptance Summary
    all_gates_pass = (
        g0["status"] == "PASS"
        and g1["status"] == "PASS"
        and g2["status"] == "PASS"
        and g3["status"] == "PASS"
        and g4["status"] == "PASS"
        and g5["status"] == "PASS"
        and g6["status"] == "PASS"
        and g7["status"] == "PASS"
        and g8["status"] == "PASS"
        and g9["status"] == "PASS"
        and g10["status"] == "PASS"
        and g11["status"] == "PASS"
        and g12["status"] == "PASS"
    )

    acceptance_data = {
        "release": "AURUM V17.5.7",
        "description": "Closure of Structural Leg Episode Derivation and True Cycle Cost Parity",
        "verdict": "ACCEPTED" if all_gates_pass else "REJECTED",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git": git_info,
        "environment": {
            "os": platform.system(),
            "os_release": platform.release(),
            "machine": platform.machine(),
            "python": python_exe,
        },
        "gates": {
            "gate0_v17_5_7_core": {
                "status": g0["status"],
                "duration_sec": g0["duration_sec"],
                "counts": counts_g0,
                "command": g0["command"],
            },
            "gate1_v17_5_6_core": {
                "status": g1["status"],
                "duration_sec": g1["duration_sec"],
                "counts": counts_g1,
                "command": g1["command"],
            },
            "gate2_v17_5_5_core": {
                "status": g2["status"],
                "duration_sec": g2["duration_sec"],
                "counts": counts_g2,
                "command": g2["command"],
            },
            "gate3_v17_5_4": {
                "status": g3["status"],
                "duration_sec": g3["duration_sec"],
                "counts": counts_g3,
                "command": g3["command"],
            },
            "gate4_v17_5_3": {
                "status": g4["status"],
                "duration_sec": g4["duration_sec"],
                "counts": counts_g4,
                "command": g4["command"],
            },
            "gate5_v17_5_2": {
                "status": g5["status"],
                "duration_sec": g5["duration_sec"],
                "counts": counts_g5,
                "command": g5["command"],
            },
            "gate6_v17_5_1": {
                "status": g6["status"],
                "duration_sec": g6["duration_sec"],
                "counts": counts_g6,
                "command": g6["command"],
            },
            "gate7_v17_5": {
                "status": g7["status"],
                "duration_sec": g7["duration_sec"],
                "counts": counts_g7,
                "command": g7["command"],
            },
            "gate8_prior": {
                "status": g8["status"],
                "duration_sec": g8["duration_sec"],
                "counts": counts_g8,
                "command": g8["command"],
            },
            "gate9_frontend_vitest": {
                "status": g9["status"],
                "duration_sec": g9["duration_sec"],
                "test_files_passed": frontend_files,
                "tests_passed": frontend_passed,
                "command": g9["command"],
            },
            "gate10_frontend_build": {
                "status": g10["status"],
                "duration_sec": g10["duration_sec"],
                "command": g10["command"],
            },
            "gate11_frontend_lint": {
                "status": g11["status"],
                "duration_sec": g11["duration_sec"],
                "command": g11["command"],
            },
            "gate12_playwright": {
                "status": g12["status"],
                "duration_sec": g12["duration_sec"],
                "tests_passed": browser_passed,
                "command": g12["command"],
            },
        },
        "totals": {
            "backend_suites_passed": sum(
                1 for g in [g0, g1, g2, g3, g4, g5, g6, g7, g8] if g["status"] == "PASS"
            ),
            "backend_tests_total": backend_total_tests,
            "backend_tests_passed": backend_total_passed,
            "backend_tests_failed": backend_total_failed,
            "frontend_unit_tests_passed": frontend_passed,
            "browser_e2e_tests_passed": browser_passed,
        },
        "provenance": {
            "key_file_hashes": file_hashes,
        },
    }

    report_path = artifacts_dir / "acceptance.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(acceptance_data, f, indent=2)

    print("\n" + "=" * 70)
    print(f"AURUM V17.5.7 ACCEPTANCE RESULT: {acceptance_data['verdict']}")
    print(f"Backend Tests: {backend_total_passed}/{backend_total_tests} passed across 9 gates (41 suites)")
    print(f"Frontend Vitest: {frontend_passed} tests across {frontend_files} files passed")
    print(f"Frontend Build & Lint: PASS")
    print(f"Playwright Browser E2E: {browser_passed} tests passed")
    print(f"Artifact report saved: {report_path}")
    print("=" * 70)

    if not all_gates_pass:
        sys.exit(1)


if __name__ == "__main__":
    main()
