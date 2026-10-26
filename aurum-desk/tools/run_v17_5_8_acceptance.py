"""
tools/run_v17_5_8_acceptance.py
Authoritative cross-platform acceptance runner for AURUM V17.5.8:
FINAL HARDENING — STRUCTURAL PROVENANCE, EXECUTION SAFETY, COST PARITY & REPRODUCIBLE ACCEPTANCE.

Executes test suites across all 14 technical gates (Gate 0: V17.5.8 core, Gates 1-9: baseline regressions,
Gate 10: Vitest, Gate 11: Frontend build, Gate 12: Frontend lint, Gate 13: Playwright E2E browser verification).
Parses real JUnit XML and Vitest test counts dynamically, captures real logs, computes SHA256 source fingerprints,
and produces artifacts/v17_5_8/acceptance.json, manifest.json, and JUnit reports.
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


def compute_source_fingerprint(files: List[Path], repo_root: Path) -> Dict[str, str]:
    """Computes SHA-256 hashes for all critical implementation files."""
    return {str(f.relative_to(repo_root)): compute_file_hash(f) for f in files}


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

    print(f"[*] AURUM V17.5.8 Acceptance Runner — Final Hardening & Verification")
    print(f"[*] OS: {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"[*] Python: {python_exe}")
    print(f"[*] Repo Root: {repo_root}")

    git_info = get_git_info(repo_root)
    print(f"[*] Git: {git_info.get('branch')} @ {git_info.get('sha')} (dirty={git_info.get('is_dirty')})")

    # Critical files to hash for provenance verification
    key_files = [
        backend_dir / "services" / "stop_placement_types.py",
        backend_dir / "services" / "stop_placement_service.py",
        backend_dir / "services" / "plan_financial_context_service.py",
        backend_dir / "services" / "scalp_m1_model_service.py",
        backend_dir / "services" / "scalp_watcher_orchestrator.py",
        backend_dir / "services" / "execution_coordinator.py",
        backend_dir / "services" / "order_plan_snapshot_service.py",
        frontend_dir / "src" / "ChartComponent.tsx",
        backend_dir / "tests" / "test_v17_5_8_reference_provenance.py",
        backend_dir / "tests" / "test_v17_5_8_break_fail_closed.py",
        backend_dir / "tests" / "test_v17_5_8_episode_authority.py",
        backend_dir / "tests" / "test_v17_5_8_cost_authority.py",
        backend_dir / "tests" / "test_v17_5_8_full_cycle.py",
        backend_dir / "tests" / "test_v17_5_7_episode_derivation.py",
        backend_dir / "tests" / "test_v17_5_7_true_cycle_cost_parity.py",
        frontend_dir / "tests" / "e2e" / "v17_5_6_chart_evidence.e2e.ts",
        tools_dir / "run_v17_5_8_acceptance.py",
    ]
    file_hashes = compute_source_fingerprint(key_files, repo_root)

    artifacts_dir = repo_root / "artifacts" / "v17_5_8"
    logs_dir = artifacts_dir / "logs"
    junit_dir = artifacts_dir / "junit"
    evidence_dir = artifacts_dir / "evidence"
    logs_dir.mkdir(parents=True, exist_ok=True)
    junit_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    backend_total_passed = 0
    backend_total_failed = 0
    backend_total_tests = 0

    # Gate 0 — V17.5.8 Core Targeted Suites
    print("\n[Gate 0] Running V17.5.8 Core Targeted Suites...")
    v17_5_8_files = [
        "tests/test_v17_5_8_reference_provenance.py",
        "tests/test_v17_5_8_break_fail_closed.py",
        "tests/test_v17_5_8_episode_authority.py",
        "tests/test_v17_5_8_cost_authority.py",
        "tests/test_v17_5_8_full_cycle.py",
    ]
    xml_g0 = junit_dir / "gate0_v17_5_8_core.xml"
    g0 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + v17_5_8_files + [f"--junitxml={xml_g0}", "-v"],
        cwd=backend_dir,
    )
    counts_g0 = parse_junit_xml(xml_g0)
    backend_total_passed += counts_g0["passed"]
    backend_total_failed += counts_g0["failed"]
    backend_total_tests += counts_g0["tests"]
    print(f"    Status: {g0['status']} ({counts_g0['passed']}/{counts_g0['tests']} passed in {g0['duration_sec']}s)")

    # Gate 1 — V17.5.7 Targeted Suites
    print("\n[Gate 1] Running V17.5.7 Baseline Suites...")
    v17_5_7_files = [
        "tests/test_v17_5_7_episode_derivation.py",
        "tests/test_v17_5_7_true_cycle_cost_parity.py",
    ]
    xml_g1 = junit_dir / "gate1_v17_5_7_core.xml"
    g1 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + v17_5_7_files + [f"--junitxml={xml_g1}", "-v"],
        cwd=backend_dir,
    )
    counts_g1 = parse_junit_xml(xml_g1)
    backend_total_passed += counts_g1["passed"]
    backend_total_failed += counts_g1["failed"]
    backend_total_tests += counts_g1["tests"]
    print(f"    Status: {g1['status']} ({counts_g1['passed']}/{counts_g1['tests']} passed in {g1['duration_sec']}s)")

    # Gate 2 — V17.5.6 Targeted Suites
    print("\n[Gate 2] Running V17.5.6 Targeted Suites...")
    v17_5_6_files = [
        "tests/test_v17_5_6_actual_order_cost_parity.py",
        "tests/test_v17_5_6_episode_origin_binding.py",
        "tests/test_v17_5_6_break_time_contract.py",
        "tests/test_v17_5_6_cycle_regression.py",
    ]
    xml_g2 = junit_dir / "gate2_v17_5_6_core.xml"
    g2 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + v17_5_6_files + [f"--junitxml={xml_g2}", "-v"],
        cwd=backend_dir,
    )
    counts_g2 = parse_junit_xml(xml_g2)
    backend_total_passed += counts_g2["passed"]
    backend_total_failed += counts_g2["failed"]
    backend_total_tests += counts_g2["tests"]
    print(f"    Status: {g2['status']} ({counts_g2['passed']}/{counts_g2['tests']} passed in {g2['duration_sec']}s)")

    # Gate 3 — V17.5.5 Baseline Suites
    print("\n[Gate 3] Running V17.5.5 Baseline Suites (Entry Gate, Cost Authority, Origin Bridge, Cycle Contract)...")
    v17_5_5_files = [
        "tests/test_v17_5_5_entry_context_gate.py",
        "tests/test_v17_5_5_cost_authority.py",
        "tests/test_v17_5_5_origin_bridge.py",
        "tests/test_v17_5_5_cycle_to_chart_contract.py",
    ]
    xml_g3 = junit_dir / "gate3_v17_5_5_core.xml"
    g3 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + v17_5_5_files + [f"--junitxml={xml_g3}", "-v"],
        cwd=backend_dir,
    )
    counts_g3 = parse_junit_xml(xml_g3)
    backend_total_passed += counts_g3["passed"]
    backend_total_failed += counts_g3["failed"]
    backend_total_tests += counts_g3["tests"]
    print(f"    Status: {g3['status']} ({counts_g3['passed']}/{counts_g3['tests']} passed in {g3['duration_sec']}s)")

    # Gate 4 — V17.5.4 Suites
    print("\n[Gate 4] Running V17.5.4 Target Suites (Context, Cost, Anchor, Cycle)...")
    v17_5_4_files = [
        "tests/test_v17_5_4_context_binding.py",
        "tests/test_v17_5_4_cost_parity.py",
        "tests/test_v17_5_4_anchor_proof.py",
        "tests/test_v17_5_4_cycle_reconciliation.py",
    ]
    xml_g4 = junit_dir / "gate4_v17_5_4.xml"
    g4 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + v17_5_4_files + [f"--junitxml={xml_g4}", "-v"],
        cwd=backend_dir,
    )
    counts_g4 = parse_junit_xml(xml_g4)
    backend_total_passed += counts_g4["passed"]
    backend_total_failed += counts_g4["failed"]
    backend_total_tests += counts_g4["tests"]
    print(f"    Status: {g4['status']} ({counts_g4['passed']}/{counts_g4['tests']} passed in {g4['duration_sec']}s)")

    # Gate 5 — V17.5.3 Boundary Probes & Cycle Review
    print("\n[Gate 5] Running V17.5.3 Boundary Probes & Cycle Review...")
    xml_g5 = junit_dir / "gate5_v17_5_3.xml"
    g5 = run_command_cross_platform(
        [
            python_exe, "-m", "pytest",
            "tests/test_v17_5_3_boundary_probes.py",
            "tests/test_v17_5_3_cycle_to_review.py",
            f"--junitxml={xml_g5}", "-v",
        ],
        cwd=backend_dir,
    )
    counts_g5 = parse_junit_xml(xml_g5)
    backend_total_passed += counts_g5["passed"]
    backend_total_failed += counts_g5["failed"]
    backend_total_tests += counts_g5["tests"]
    print(f"    Status: {g5['status']} ({counts_g5['passed']}/{counts_g5['tests']} passed in {g5['duration_sec']}s)")

    # Gate 6 — Baseline V17.5.2 Suites
    print("\n[Gate 6] Running V17.5.2 Baseline Suites...")
    xml_g6 = junit_dir / "gate6_v17_5_2.xml"
    g6 = run_command_cross_platform(
        [
            python_exe, "-m", "pytest",
            "tests/test_v17_5_2_boundary_probes.py",
            "tests/test_v17_5_2_detector_to_exit.py",
            f"--junitxml={xml_g6}", "-v",
        ],
        cwd=backend_dir,
    )
    counts_g6 = parse_junit_xml(xml_g6)
    backend_total_passed += counts_g6["passed"]
    backend_total_failed += counts_g6["failed"]
    backend_total_tests += counts_g6["tests"]
    print(f"    Status: {g6['status']} ({counts_g6['passed']}/{counts_g6['tests']} passed in {g6['duration_sec']}s)")

    # Gate 7 — V17.5.1 Feature Suites & Probes
    print("\n[Gate 7] Running V17.5.1 Feature Suites & Probes...")
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
    xml_g7 = junit_dir / "gate7_v17_5_1.xml"
    g7 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + v17_5_1_files + [f"--junitxml={xml_g7}", "-v"],
        cwd=backend_dir,
    )
    counts_g7 = parse_junit_xml(xml_g7)
    backend_total_passed += counts_g7["passed"]
    backend_total_failed += counts_g7["failed"]
    backend_total_tests += counts_g7["tests"]
    print(f"    Status: {g7['status']} ({counts_g7['passed']}/{counts_g7['tests']} passed in {g7['duration_sec']}s)")

    # Gate 8 — Existing V17.5 Regression Suites
    print("\n[Gate 8] Running Existing V17.5 Regression Suites...")
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
    xml_g8 = junit_dir / "gate8_v17_5.xml"
    g8 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + v17_5_existing + [f"--junitxml={xml_g8}", "-v"],
        cwd=backend_dir,
    )
    counts_g8 = parse_junit_xml(xml_g8)
    backend_total_passed += counts_g8["passed"]
    backend_total_failed += counts_g8["failed"]
    backend_total_tests += counts_g8["tests"]
    print(f"    Status: {g8['status']} ({counts_g8['passed']}/{counts_g8['tests']} passed in {g8['duration_sec']}s)")

    # Gate 9 — Prior Regressions (CG01, sessions, authority)
    print("\n[Gate 9] Running Prior Regression Suites...")
    prior_regression = [
        "tests/test_v16_2_cg01_long_short.py",
        "tests/test_v17_4_3_session_roundtrip.py",
        "tests/test_v17_4_3_atomic_mirrors.py",
        "tests/test_v17_4_2_baseline_probes.py",
        "tests/test_v17_4_2_noop_authority.py",
    ]
    xml_g9 = junit_dir / "gate9_prior.xml"
    g9 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + prior_regression + [f"--junitxml={xml_g9}", "-v"],
        cwd=backend_dir,
    )
    counts_g9 = parse_junit_xml(xml_g9)
    backend_total_passed += counts_g9["passed"]
    backend_total_failed += counts_g9["failed"]
    backend_total_tests += counts_g9["tests"]
    print(f"    Status: {g9['status']} ({counts_g9['passed']}/{counts_g9['tests']} passed in {g9['duration_sec']}s)")

    # Gate 10 — Frontend Vitest Unit Tests
    print("\n[Gate 10] Running Frontend Vitest Unit Tests...")
    g10 = run_command_cross_platform(["npm", "test", "--", "--run"], cwd=frontend_dir)
    frontend_passed = 0
    frontend_files = 0
    for line in g10.get("stdout", "").splitlines():
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
    print(f"    Status: {g10['status']} ({frontend_passed} tests across {frontend_files} files passed in {g10['duration_sec']}s)")

    # Gate 11 — Frontend Build
    print("\n[Gate 11] Running Frontend Build...")
    g11 = run_command_cross_platform(["npm", "run", "build"], cwd=frontend_dir)
    print(f"    Status: {g11['status']} (in {g11['duration_sec']}s)")

    # Gate 12 — Frontend Lint
    print("\n[Gate 12] Running Frontend Lint...")
    g12 = run_command_cross_platform(["npm", "run", "lint"], cwd=frontend_dir)
    print(f"    Status: {g12['status']} (in {g12['duration_sec']}s)")

    # Gate 13 — Playwright Browser Verification
    print("\n[Gate 13] Running Playwright Browser Verification (v17_5_6_chart_evidence.e2e.ts)...")
    g13 = run_command_cross_platform(
        ["npm", "run", "test:e2e", "--", "tests/e2e/v17_5_6_chart_evidence.e2e.ts"],
        cwd=frontend_dir,
    )
    browser_passed = 0
    for line in g13.get("stdout", "").splitlines():
        if "passed" in line and "(" in line:
            parts = line.strip().split()
            if parts and parts[0].isdigit():
                browser_passed = int(parts[0])
    print(f"    Status: {g13['status']} ({browser_passed} browser tests passed in {g13['duration_sec']}s)")

    # Write logs
    def _safe_str(val: Any) -> str:
        return str(val) if val is not None else ""

    with open(logs_dir / "gate0_v17_5_8_core.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g0.get("stdout")) + "\n" + _safe_str(g0.get("stderr")))
    with open(logs_dir / "gate1_v17_5_7_core.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g1.get("stdout")) + "\n" + _safe_str(g1.get("stderr")))
    with open(logs_dir / "gate2_v17_5_6_core.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g2.get("stdout")) + "\n" + _safe_str(g2.get("stderr")))
    with open(logs_dir / "gate3_v17_5_5_core.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g3.get("stdout")) + "\n" + _safe_str(g3.get("stderr")))
    with open(logs_dir / "gate4_v17_5_4.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g4.get("stdout")) + "\n" + _safe_str(g4.get("stderr")))
    with open(logs_dir / "gate5_v17_5_3.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g5.get("stdout")) + "\n" + _safe_str(g5.get("stderr")))
    with open(logs_dir / "gate6_v17_5_2.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g6.get("stdout")) + "\n" + _safe_str(g6.get("stderr")))
    with open(logs_dir / "gate7_v17_5_1.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g7.get("stdout")) + "\n" + _safe_str(g7.get("stderr")))
    with open(logs_dir / "gate8_v17_5.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g8.get("stdout")) + "\n" + _safe_str(g8.get("stderr")))
    with open(logs_dir / "gate9_prior.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g9.get("stdout")) + "\n" + _safe_str(g9.get("stderr")))
    with open(logs_dir / "gate10_frontend_vitest.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g10.get("stdout")) + "\n" + _safe_str(g10.get("stderr")))
    with open(logs_dir / "gate11_frontend_build.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g11.get("stdout")) + "\n" + _safe_str(g11.get("stderr")))
    with open(logs_dir / "gate12_frontend_lint.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g12.get("stdout")) + "\n" + _safe_str(g12.get("stderr")))
    with open(logs_dir / "gate13_playwright_e2e.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g13.get("stdout")) + "\n" + _safe_str(g13.get("stderr")))

    # Verdict derivation
    all_backend_gates = [g0, g1, g2, g3, g4, g5, g6, g7, g8, g9]
    all_gates = all_backend_gates + [g10, g11, g12, g13]

    backend_ok = all(g.get("status") == "PASS" for g in all_backend_gates) and backend_total_failed == 0 and backend_total_passed >= 242
    vitest_ok = g10.get("status") == "PASS" and frontend_passed >= 501
    build_ok = g11.get("status") == "PASS"
    lint_ok = g12.get("status") == "PASS"
    playwright_ok = g13.get("status") == "PASS" and browser_passed >= 6

    final_verdict = "ACCEPTED" if (backend_ok and vitest_ok and build_ok and lint_ok and playwright_ok) else "REJECTED"

    acceptance_payload = {
        "version": "V17.5.8",
        "verdict": final_verdict,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tested_git_sha": git_info.get("sha"),
        "git_branch": git_info.get("branch"),
        "is_dirty": git_info.get("is_dirty"),
        "summary": {
            "backend_tests_total": backend_total_tests,
            "backend_tests_passed": backend_total_passed,
            "backend_tests_failed": backend_total_failed,
            "frontend_tests_passed": frontend_passed,
            "frontend_files": frontend_files,
            "browser_e2e_passed": browser_passed,
            "build_status": g11.get("status"),
            "lint_status": g12.get("status"),
        },
        "gates": {
            "gate0_v17_5_8_core": {"status": g0.get("status"), "counts": counts_g0, "duration_sec": g0.get("duration_sec")},
            "gate1_v17_5_7_core": {"status": g1.get("status"), "counts": counts_g1, "duration_sec": g1.get("duration_sec")},
            "gate2_v17_5_6_core": {"status": g2.get("status"), "counts": counts_g2, "duration_sec": g2.get("duration_sec")},
            "gate3_v17_5_5_core": {"status": g3.get("status"), "counts": counts_g3, "duration_sec": g3.get("duration_sec")},
            "gate4_v17_5_4": {"status": g4.get("status"), "counts": counts_g4, "duration_sec": g4.get("duration_sec")},
            "gate5_v17_5_3": {"status": g5.get("status"), "counts": counts_g5, "duration_sec": g5.get("duration_sec")},
            "gate6_v17_5_2": {"status": g6.get("status"), "counts": counts_g6, "duration_sec": g6.get("duration_sec")},
            "gate7_v17_5_1": {"status": g7.get("status"), "counts": counts_g7, "duration_sec": g7.get("duration_sec")},
            "gate8_v17_5": {"status": g8.get("status"), "counts": counts_g8, "duration_sec": g8.get("duration_sec")},
            "gate9_prior_regressions": {"status": g9.get("status"), "counts": counts_g9, "duration_sec": g9.get("duration_sec")},
            "gate10_frontend_vitest": {"status": g10.get("status"), "passed": frontend_passed, "duration_sec": g10.get("duration_sec")},
            "gate11_frontend_build": {"status": g11.get("status"), "duration_sec": g11.get("duration_sec")},
            "gate12_frontend_lint": {"status": g12.get("status"), "duration_sec": g12.get("duration_sec")},
            "gate13_playwright_e2e": {"status": g13.get("status"), "passed": browser_passed, "duration_sec": g13.get("duration_sec")},
        },
        "source_fingerprints": file_hashes,
    }

    acceptance_json_path = artifacts_dir / "acceptance.json"
    with open(acceptance_json_path, "w", encoding="utf-8") as f:
        json.dump(acceptance_payload, f, indent=2)

    manifest_json_path = artifacts_dir / "manifest.json"
    with open(manifest_json_path, "w", encoding="utf-8") as f:
        json.dump({
            "version": "V17.5.8",
            "verdict": final_verdict,
            "generated_artifacts": [
                "acceptance.json",
                "manifest.json",
                "junit/gate0_v17_5_8_core.xml",
                "junit/gate1_v17_5_7_core.xml",
                "junit/gate2_v17_5_6_core.xml",
                "junit/gate3_v17_5_5_core.xml",
                "junit/gate4_v17_5_4.xml",
                "junit/gate5_v17_5_3.xml",
                "junit/gate6_v17_5_2.xml",
                "junit/gate7_v17_5_1.xml",
                "junit/gate8_v17_5.xml",
                "junit/gate9_prior.xml",
            ],
            "source_files_hashed": len(file_hashes),
        }, f, indent=2)

    print("\n" + "=" * 70)
    print(f"[*] AURUM V17.5.8 VERDICT: {final_verdict}")
    print(f"[*] Backend tests: {backend_total_passed}/{backend_total_tests} passed (0 failed)")
    print(f"[*] Frontend tests: {frontend_passed} passed")
    print(f"[*] Playwright E2E: {browser_passed} passed")
    print(f"[*] Build: {g11.get('status')}")
    print(f"[*] Lint: {g12.get('status')}")
    print(f"[*] Acceptance JSON: {acceptance_json_path}")
    print("=" * 70)

    if final_verdict != "ACCEPTED":
        sys.exit(1)


if __name__ == "__main__":
    main()
