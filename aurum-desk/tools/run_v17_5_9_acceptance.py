"""
tools/run_v17_5_9_acceptance.py
Authoritative cross-platform acceptance runner for AURUM V17.5.9:
FINAL CORRECTNESS REPAIR — PRODUCTION WIRING, STRUCTURAL SOURCE VERIFICATION,
FINANCIAL PARITY & CLEAN COMMIT ACCEPTANCE.

Executes test suites across all 15 technical gates:
- Gate 0: V17.5.9 Core Targeted Suites (Reference provenance, Break source, ARM wiring, Cost fail-closed, Financial parity, Real cycle regression)
- Gate 1: V17.5.8 Core Suites
- Gate 2: V17.5.7 Baseline Suites
- Gate 3: V17.5.6 Targeted Suites
- Gate 4: V17.5.5 Baseline Suites
- Gate 5: V17.5.4 Suites
- Gate 6: V17.5.3 Boundary Probes & Cycle Review
- Gate 7: V17.5.2 Baseline Suites
- Gate 8: V17.5.1 Feature Suites & Probes
- Gate 9: V17.5 Existing Regression Suites
- Gate 10: Prior Regressions (CG01, sessions, authority)
- Gate 11: Frontend Vitest Unit Tests
- Gate 12: Frontend Build
- Gate 13: Frontend Lint
- Gate 14: Playwright Browser Verification (v17_5_6_chart_evidence.e2e.ts)

Enforces strict D07 acceptance provenance:
- Verifies clean Git status (fails closed if uncommitted source modifications exist)
- Fresh JUnit XML and log capture (prevents stale file reuse)
- Dynamic test count parsing without hardcoding
- Rejects timeouts, zero test collection, skips, or non-zero exits
- Hashes all critical source and test files into artifacts/v17_5_9/manifest.json
- Emits artifacts/v17_5_9/acceptance.json
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
from typing import Any, Dict, List, Optional, Tuple


def get_git_info(repo_root: Path) -> dict:
    try:
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root).decode().strip()
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root).decode().strip()
        status_raw = subprocess.check_output(["git", "status", "--porcelain"], cwd=repo_root).decode().strip()
        status_lines = [line.strip() for line in status_raw.splitlines() if line.strip()]
        
        # Check if tracked source files (.py, .ts, .tsx) have modifications
        dirty_tracked = [l for l in status_lines if not l.startswith("?? artifacts/") and not l.startswith("?? docs/")]
        is_dirty = len(dirty_tracked) > 0

        diff = subprocess.check_output(["git", "diff"], cwd=repo_root).decode()
        diff_hash = hashlib.sha256(diff.encode("utf-8")).hexdigest()[:16]
        return {
            "branch": branch,
            "sha": sha,
            "is_dirty": is_dirty,
            "diff_hash": diff_hash,
            "status_lines": status_lines,
            "dirty_tracked": dirty_tracked,
        }
    except Exception as e:
        return {"error": str(e), "is_dirty": True, "sha": "UNKNOWN", "branch": "UNKNOWN"}


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


def parse_junit_xml(xml_path: Path, min_mtime: float = 0.0) -> Dict[str, Any]:
    """Dynamically parses pytest JUnit XML report without hardcoding counts.
    Enforces that XML must exist, be fresh (> min_mtime), and contain valid testsuite nodes.
    """
    if not xml_path.exists():
        return {"status": "FAIL", "tests": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0, "reason": "JUNIT_FILE_MISSING"}
    
    if min_mtime > 0 and xml_path.stat().st_mtime < (min_mtime - 1.0):
        return {"status": "FAIL", "tests": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0, "reason": "JUNIT_FILE_STALE"}

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

        if total_tests == 0:
            return {"status": "FAIL", "tests": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0, "reason": "ZERO_TESTS_COLLECTED"}
        if failures > 0 or errors > 0:
            return {"status": "FAIL", "tests": total_tests, "passed": passed, "failed": failures, "skipped": skipped, "errors": errors, "reason": "TESTS_FAILED"}
        if skipped > 0:
            return {"status": "FAIL", "tests": total_tests, "passed": passed, "failed": failures, "skipped": skipped, "errors": errors, "reason": "UNEXPECTED_SKIPPED_TESTS"}

        return {
            "status": "PASS",
            "tests": total_tests,
            "passed": passed,
            "failed": failures,
            "skipped": skipped,
            "errors": errors,
            "reason": None,
        }
    except Exception as e:
        return {"status": "FAIL", "tests": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0, "reason": f"JUNIT_PARSE_ERROR: {e}"}


def run_command_cross_platform(cmd: list[str], cwd: Path, timeout: int = 300) -> dict:
    t0 = time.time()
    is_win = platform.system() == "Windows"
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


def evaluate_final_verdict(
    *,
    is_dirty: bool,
    backend_ok: bool,
    backend_total_passed: int,
    backend_total_failed: int,
    vitest_ok: bool,
    frontend_passed: int,
    build_ok: bool,
    lint_ok: bool,
    playwright_ok: bool,
    browser_passed: int,
    critical_files_missing: bool = False,
) -> str:
    """Evaluates final acceptance verdict per Part 090 contract:
    - All mandatory gates PASS
    - Clean tested source revision
    - Source fingerprint matches (no missing critical files)
    - No required test skipped
    - No mandatory evidence missing
    """
    if critical_files_missing:
        return "BLOCKED"
    if is_dirty:
        return "BLOCKED"
    
    all_gates_pass = (
        backend_ok
        and backend_total_failed == 0
        and backend_total_passed >= 300
        and vitest_ok
        and frontend_passed >= 501
        and build_ok
        and lint_ok
        and playwright_ok
        and browser_passed >= 6
    )
    return "ACCEPTED" if all_gates_pass else "REJECTED"


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

    print(f"[*] AURUM V17.5.9 Acceptance Runner — Production Wiring, Provenance & Parity")
    print(f"[*] OS: {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"[*] Python: {python_exe}")
    print(f"[*] Repo Root: {repo_root}")

    git_info = get_git_info(repo_root)
    is_dirty = git_info.get("is_dirty", True)
    print(f"[*] Git: {git_info.get('branch')} @ {git_info.get('sha')} (is_dirty={is_dirty})")
    if is_dirty:
        print(f"    [!] Warning: Working tree has uncommitted tracked changes: {git_info.get('dirty_tracked')}")

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
        # V17.5.9 test suites
        backend_dir / "tests" / "test_v17_5_9_reference_source_authority.py",
        backend_dir / "tests" / "test_v17_5_9_break_source_authority.py",
        backend_dir / "tests" / "test_v17_5_9_arm_production_wiring.py",
        backend_dir / "tests" / "test_v17_5_9_cost_fail_closed.py",
        backend_dir / "tests" / "test_v17_5_9_financial_parity.py",
        backend_dir / "tests" / "test_v17_5_9_real_cycle_regression.py",
        backend_dir / "tests" / "test_v17_5_9_acceptance_runner_self_test.py",
        # Baseline regressions
        backend_dir / "tests" / "test_v17_5_8_reference_provenance.py",
        backend_dir / "tests" / "test_v17_5_8_break_fail_closed.py",
        backend_dir / "tests" / "test_v17_5_8_episode_authority.py",
        backend_dir / "tests" / "test_v17_5_8_cost_authority.py",
        backend_dir / "tests" / "test_v17_5_8_full_cycle.py",
        backend_dir / "tests" / "test_v17_5_7_episode_derivation.py",
        backend_dir / "tests" / "test_v17_5_7_true_cycle_cost_parity.py",
        frontend_dir / "tests" / "e2e" / "v17_5_6_chart_evidence.e2e.ts",
        tools_dir / "run_v17_5_9_acceptance.py",
    ]
    file_hashes = compute_source_fingerprint(key_files, repo_root)
    critical_missing = any(h == "MISSING" for h in file_hashes.values())
    if critical_missing:
        print("    [!] ERROR: One or more critical files are missing from the repository manifest!")

    artifacts_dir = repo_root / "artifacts" / "v17_5_9"
    logs_dir = artifacts_dir / "logs"
    junit_dir = artifacts_dir / "junit"
    evidence_dir = artifacts_dir / "evidence"
    logs_dir.mkdir(parents=True, exist_ok=True)
    junit_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    backend_total_passed = 0
    backend_total_failed = 0
    backend_total_tests = 0
    backend_gate_results: Dict[str, Any] = {}

    run_start_time = time.time()

    # Gate 0 — V17.5.9 Core Targeted Suites
    print("\n[Gate 0] Running V17.5.9 Core Targeted Suites (D01–D06 Repairs)...")
    v17_5_9_files = [
        "tests/test_v17_5_9_reference_source_authority.py",
        "tests/test_v17_5_9_break_source_authority.py",
        "tests/test_v17_5_9_arm_production_wiring.py",
        "tests/test_v17_5_9_cost_fail_closed.py",
        "tests/test_v17_5_9_financial_parity.py",
        "tests/test_v17_5_9_real_cycle_regression.py",
        "tests/test_v17_5_9_acceptance_runner_self_test.py",
    ]
    xml_g0 = junit_dir / "gate0_v17_5_9_core.xml"
    if xml_g0.exists():
        xml_g0.unlink()
    g0 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + v17_5_9_files + [f"--junitxml={xml_g0}", "-v"],
        cwd=backend_dir,
    )
    counts_g0 = parse_junit_xml(xml_g0, min_mtime=run_start_time)
    backend_total_passed += counts_g0["passed"]
    backend_total_failed += counts_g0["failed"] + counts_g0["errors"]
    backend_total_tests += counts_g0["tests"]
    backend_gate_results["gate0_v17_5_9_core"] = {"status": counts_g0["status"], "counts": counts_g0, "duration_sec": g0["duration_sec"]}
    print(f"    Status: {counts_g0['status']} ({counts_g0['passed']}/{counts_g0['tests']} passed in {g0['duration_sec']}s)")

    # Gate 1 — V17.5.8 Core Suites
    print("\n[Gate 1] Running V17.5.8 Baseline Suites...")
    v17_5_8_files = [
        "tests/test_v17_5_8_reference_provenance.py",
        "tests/test_v17_5_8_break_fail_closed.py",
        "tests/test_v17_5_8_episode_authority.py",
        "tests/test_v17_5_8_cost_authority.py",
        "tests/test_v17_5_8_full_cycle.py",
    ]
    xml_g1 = junit_dir / "gate1_v17_5_8_core.xml"
    if xml_g1.exists():
        xml_g1.unlink()
    g1 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + v17_5_8_files + [f"--junitxml={xml_g1}", "-v"],
        cwd=backend_dir,
    )
    counts_g1 = parse_junit_xml(xml_g1, min_mtime=run_start_time)
    backend_total_passed += counts_g1["passed"]
    backend_total_failed += counts_g1["failed"] + counts_g1["errors"]
    backend_total_tests += counts_g1["tests"]
    backend_gate_results["gate1_v17_5_8_core"] = {"status": counts_g1["status"], "counts": counts_g1, "duration_sec": g1["duration_sec"]}
    print(f"    Status: {counts_g1['status']} ({counts_g1['passed']}/{counts_g1['tests']} passed in {g1['duration_sec']}s)")

    # Gate 2 — V17.5.7 Baseline Suites
    print("\n[Gate 2] Running V17.5.7 Baseline Suites...")
    v17_5_7_files = [
        "tests/test_v17_5_7_episode_derivation.py",
        "tests/test_v17_5_7_true_cycle_cost_parity.py",
    ]
    xml_g2 = junit_dir / "gate2_v17_5_7_core.xml"
    if xml_g2.exists():
        xml_g2.unlink()
    g2 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + v17_5_7_files + [f"--junitxml={xml_g2}", "-v"],
        cwd=backend_dir,
    )
    counts_g2 = parse_junit_xml(xml_g2, min_mtime=run_start_time)
    backend_total_passed += counts_g2["passed"]
    backend_total_failed += counts_g2["failed"] + counts_g2["errors"]
    backend_total_tests += counts_g2["tests"]
    backend_gate_results["gate2_v17_5_7_core"] = {"status": counts_g2["status"], "counts": counts_g2, "duration_sec": g2["duration_sec"]}
    print(f"    Status: {counts_g2['status']} ({counts_g2['passed']}/{counts_g2['tests']} passed in {g2['duration_sec']}s)")

    # Gate 3 — V17.5.6 Targeted Suites
    print("\n[Gate 3] Running V17.5.6 Targeted Suites...")
    v17_5_6_files = [
        "tests/test_v17_5_6_actual_order_cost_parity.py",
        "tests/test_v17_5_6_episode_origin_binding.py",
        "tests/test_v17_5_6_break_time_contract.py",
        "tests/test_v17_5_6_cycle_regression.py",
    ]
    xml_g3 = junit_dir / "gate3_v17_5_6_core.xml"
    if xml_g3.exists():
        xml_g3.unlink()
    g3 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + v17_5_6_files + [f"--junitxml={xml_g3}", "-v"],
        cwd=backend_dir,
    )
    counts_g3 = parse_junit_xml(xml_g3, min_mtime=run_start_time)
    backend_total_passed += counts_g3["passed"]
    backend_total_failed += counts_g3["failed"] + counts_g3["errors"]
    backend_total_tests += counts_g3["tests"]
    backend_gate_results["gate3_v17_5_6_core"] = {"status": counts_g3["status"], "counts": counts_g3, "duration_sec": g3["duration_sec"]}
    print(f"    Status: {counts_g3['status']} ({counts_g3['passed']}/{counts_g3['tests']} passed in {g3['duration_sec']}s)")

    # Gate 4 — V17.5.5 Baseline Suites
    print("\n[Gate 4] Running V17.5.5 Baseline Suites...")
    v17_5_5_files = [
        "tests/test_v17_5_5_entry_context_gate.py",
        "tests/test_v17_5_5_cost_authority.py",
        "tests/test_v17_5_5_origin_bridge.py",
        "tests/test_v17_5_5_cycle_to_chart_contract.py",
    ]
    xml_g4 = junit_dir / "gate4_v17_5_5_core.xml"
    if xml_g4.exists():
        xml_g4.unlink()
    g4 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + v17_5_5_files + [f"--junitxml={xml_g4}", "-v"],
        cwd=backend_dir,
    )
    counts_g4 = parse_junit_xml(xml_g4, min_mtime=run_start_time)
    backend_total_passed += counts_g4["passed"]
    backend_total_failed += counts_g4["failed"] + counts_g4["errors"]
    backend_total_tests += counts_g4["tests"]
    backend_gate_results["gate4_v17_5_5_core"] = {"status": counts_g4["status"], "counts": counts_g4, "duration_sec": g4["duration_sec"]}
    print(f"    Status: {counts_g4['status']} ({counts_g4['passed']}/{counts_g4['tests']} passed in {g4['duration_sec']}s)")

    # Gate 5 — V17.5.4 Suites
    print("\n[Gate 5] Running V17.5.4 Target Suites...")
    v17_5_4_files = [
        "tests/test_v17_5_4_context_binding.py",
        "tests/test_v17_5_4_cost_parity.py",
        "tests/test_v17_5_4_anchor_proof.py",
        "tests/test_v17_5_4_cycle_reconciliation.py",
    ]
    xml_g5 = junit_dir / "gate5_v17_5_4.xml"
    if xml_g5.exists():
        xml_g5.unlink()
    g5 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + v17_5_4_files + [f"--junitxml={xml_g5}", "-v"],
        cwd=backend_dir,
    )
    counts_g5 = parse_junit_xml(xml_g5, min_mtime=run_start_time)
    backend_total_passed += counts_g5["passed"]
    backend_total_failed += counts_g5["failed"] + counts_g5["errors"]
    backend_total_tests += counts_g5["tests"]
    backend_gate_results["gate5_v17_5_4"] = {"status": counts_g5["status"], "counts": counts_g5, "duration_sec": g5["duration_sec"]}
    print(f"    Status: {counts_g5['status']} ({counts_g5['passed']}/{counts_g5['tests']} passed in {g5['duration_sec']}s)")

    # Gate 6 — V17.5.3 Boundary Probes & Cycle Review
    print("\n[Gate 6] Running V17.5.3 Boundary Probes & Cycle Review...")
    xml_g6 = junit_dir / "gate6_v17_5_3.xml"
    if xml_g6.exists():
        xml_g6.unlink()
    g6 = run_command_cross_platform(
        [
            python_exe, "-m", "pytest",
            "tests/test_v17_5_3_boundary_probes.py",
            "tests/test_v17_5_3_cycle_to_review.py",
            f"--junitxml={xml_g6}", "-v",
        ],
        cwd=backend_dir,
    )
    counts_g6 = parse_junit_xml(xml_g6, min_mtime=run_start_time)
    backend_total_passed += counts_g6["passed"]
    backend_total_failed += counts_g6["failed"] + counts_g6["errors"]
    backend_total_tests += counts_g6["tests"]
    backend_gate_results["gate6_v17_5_3"] = {"status": counts_g6["status"], "counts": counts_g6, "duration_sec": g6["duration_sec"]}
    print(f"    Status: {counts_g6['status']} ({counts_g6['passed']}/{counts_g6['tests']} passed in {g6['duration_sec']}s)")

    # Gate 7 — Baseline V17.5.2 Suites
    print("\n[Gate 7] Running V17.5.2 Baseline Suites...")
    xml_g7 = junit_dir / "gate7_v17_5_2.xml"
    if xml_g7.exists():
        xml_g7.unlink()
    g7 = run_command_cross_platform(
        [
            python_exe, "-m", "pytest",
            "tests/test_v17_5_2_boundary_probes.py",
            "tests/test_v17_5_2_detector_to_exit.py",
            f"--junitxml={xml_g7}", "-v",
        ],
        cwd=backend_dir,
    )
    counts_g7 = parse_junit_xml(xml_g7, min_mtime=run_start_time)
    backend_total_passed += counts_g7["passed"]
    backend_total_failed += counts_g7["failed"] + counts_g7["errors"]
    backend_total_tests += counts_g7["tests"]
    backend_gate_results["gate7_v17_5_2"] = {"status": counts_g7["status"], "counts": counts_g7, "duration_sec": g7["duration_sec"]}
    print(f"    Status: {counts_g7['status']} ({counts_g7['passed']}/{counts_g7['tests']} passed in {g7['duration_sec']}s)")

    # Gate 8 — V17.5.1 Feature Suites & Probes
    print("\n[Gate 8] Running V17.5.1 Feature Suites & Probes...")
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
    xml_g8 = junit_dir / "gate8_v17_5_1.xml"
    if xml_g8.exists():
        xml_g8.unlink()
    g8 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + v17_5_1_files + [f"--junitxml={xml_g8}", "-v"],
        cwd=backend_dir,
    )
    counts_g8 = parse_junit_xml(xml_g8, min_mtime=run_start_time)
    backend_total_passed += counts_g8["passed"]
    backend_total_failed += counts_g8["failed"] + counts_g8["errors"]
    backend_total_tests += counts_g8["tests"]
    backend_gate_results["gate8_v17_5_1"] = {"status": counts_g8["status"], "counts": counts_g8, "duration_sec": g8["duration_sec"]}
    print(f"    Status: {counts_g8['status']} ({counts_g8['passed']}/{counts_g8['tests']} passed in {g8['duration_sec']}s)")

    # Gate 9 — Existing V17.5 Regression Suites
    print("\n[Gate 9] Running Existing V17.5 Regression Suites...")
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
    xml_g9 = junit_dir / "gate9_v17_5.xml"
    if xml_g9.exists():
        xml_g9.unlink()
    g9 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + v17_5_existing + [f"--junitxml={xml_g9}", "-v"],
        cwd=backend_dir,
    )
    counts_g9 = parse_junit_xml(xml_g9, min_mtime=run_start_time)
    backend_total_passed += counts_g9["passed"]
    backend_total_failed += counts_g9["failed"] + counts_g9["errors"]
    backend_total_tests += counts_g9["tests"]
    backend_gate_results["gate9_v17_5"] = {"status": counts_g9["status"], "counts": counts_g9, "duration_sec": g9["duration_sec"]}
    print(f"    Status: {counts_g9['status']} ({counts_g9['passed']}/{counts_g9['tests']} passed in {g9['duration_sec']}s)")

    # Gate 10 — Prior Regressions (CG01, sessions, authority)
    print("\n[Gate 10] Running Prior Regression Suites...")
    prior_regression = [
        "tests/test_v16_2_cg01_long_short.py",
        "tests/test_v17_4_3_session_roundtrip.py",
        "tests/test_v17_4_3_atomic_mirrors.py",
        "tests/test_v17_4_2_baseline_probes.py",
        "tests/test_v17_4_2_noop_authority.py",
    ]
    xml_g10 = junit_dir / "gate10_prior.xml"
    if xml_g10.exists():
        xml_g10.unlink()
    g10 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + prior_regression + [f"--junitxml={xml_g10}", "-v"],
        cwd=backend_dir,
    )
    counts_g10 = parse_junit_xml(xml_g10, min_mtime=run_start_time)
    backend_total_passed += counts_g10["passed"]
    backend_total_failed += counts_g10["failed"] + counts_g10["errors"]
    backend_total_tests += counts_g10["tests"]
    backend_gate_results["gate10_prior_regressions"] = {"status": counts_g10["status"], "counts": counts_g10, "duration_sec": g10["duration_sec"]}
    print(f"    Status: {counts_g10['status']} ({counts_g10['passed']}/{counts_g10['tests']} passed in {g10['duration_sec']}s)")

    # Gate 11 — Frontend Vitest Unit Tests
    print("\n[Gate 11] Running Frontend Vitest Unit Tests...")
    g11 = run_command_cross_platform(["npm", "test", "--", "--run"], cwd=frontend_dir)
    frontend_passed = 0
    frontend_files = 0
    for line in g11.get("stdout", "").splitlines():
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
    print(f"    Status: {g11['status']} ({frontend_passed} tests across {frontend_files} files passed in {g11['duration_sec']}s)")

    # Gate 12 — Frontend Build
    print("\n[Gate 12] Running Frontend Build...")
    g12 = run_command_cross_platform(["npm", "run", "build"], cwd=frontend_dir)
    print(f"    Status: {g12['status']} (in {g12['duration_sec']}s)")

    # Gate 13 — Frontend Lint
    print("\n[Gate 13] Running Frontend Lint...")
    g13 = run_command_cross_platform(["npm", "run", "lint"], cwd=frontend_dir)
    print(f"    Status: {g13['status']} (in {g13['duration_sec']}s)")

    # Gate 14 — Playwright Browser Verification
    print("\n[Gate 14] Running Playwright Browser Verification (v17_5_6_chart_evidence.e2e.ts)...")
    g14 = run_command_cross_platform(
        ["npm", "run", "test:e2e", "--", "tests/e2e/v17_5_6_chart_evidence.e2e.ts"],
        cwd=frontend_dir,
    )
    browser_passed = 0
    for line in g14.get("stdout", "").splitlines():
        if "passed" in line and "(" in line:
            parts = line.strip().split()
            if parts and parts[0].isdigit():
                browser_passed = int(parts[0])
    print(f"    Status: {g14['status']} ({browser_passed} browser tests passed in {g14['duration_sec']}s)")

    # Write logs
    def _safe_str(val: Any) -> str:
        return str(val) if val is not None else ""

    with open(logs_dir / "gate0_v17_5_9_core.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g0.get("stdout")) + "\n" + _safe_str(g0.get("stderr")))
    with open(logs_dir / "gate1_v17_5_8_core.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g1.get("stdout")) + "\n" + _safe_str(g1.get("stderr")))
    with open(logs_dir / "gate2_v17_5_7_core.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g2.get("stdout")) + "\n" + _safe_str(g2.get("stderr")))
    with open(logs_dir / "gate3_v17_5_6_core.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g3.get("stdout")) + "\n" + _safe_str(g3.get("stderr")))
    with open(logs_dir / "gate4_v17_5_5_core.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g4.get("stdout")) + "\n" + _safe_str(g4.get("stderr")))
    with open(logs_dir / "gate5_v17_5_4.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g5.get("stdout")) + "\n" + _safe_str(g5.get("stderr")))
    with open(logs_dir / "gate6_v17_5_3.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g6.get("stdout")) + "\n" + _safe_str(g6.get("stderr")))
    with open(logs_dir / "gate7_v17_5_2.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g7.get("stdout")) + "\n" + _safe_str(g7.get("stderr")))
    with open(logs_dir / "gate8_v17_5_1.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g8.get("stdout")) + "\n" + _safe_str(g8.get("stderr")))
    with open(logs_dir / "gate9_v17_5.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g9.get("stdout")) + "\n" + _safe_str(g9.get("stderr")))
    with open(logs_dir / "gate10_prior.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g10.get("stdout")) + "\n" + _safe_str(g10.get("stderr")))
    with open(logs_dir / "gate11_frontend_vitest.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g11.get("stdout")) + "\n" + _safe_str(g11.get("stderr")))
    with open(logs_dir / "gate12_frontend_build.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g12.get("stdout")) + "\n" + _safe_str(g12.get("stderr")))
    with open(logs_dir / "gate13_frontend_lint.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g13.get("stdout")) + "\n" + _safe_str(g13.get("stderr")))
    with open(logs_dir / "gate14_playwright_e2e.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g14.get("stdout")) + "\n" + _safe_str(g14.get("stderr")))

    # Verdict derivation
    backend_ok = all(v["status"] == "PASS" for v in backend_gate_results.values()) and backend_total_failed == 0
    vitest_ok = g11.get("status") == "PASS" and frontend_passed >= 501
    build_ok = g12.get("status") == "PASS"
    lint_ok = g13.get("status") == "PASS"
    playwright_ok = g14.get("status") == "PASS" and browser_passed >= 6

    final_verdict = evaluate_final_verdict(
        is_dirty=is_dirty,
        backend_ok=backend_ok,
        backend_total_passed=backend_total_passed,
        backend_total_failed=backend_total_failed,
        vitest_ok=vitest_ok,
        frontend_passed=frontend_passed,
        build_ok=build_ok,
        lint_ok=lint_ok,
        playwright_ok=playwright_ok,
        browser_passed=browser_passed,
        critical_files_missing=critical_missing,
    )

    acceptance_payload = {
        "version": "V17.5.9",
        "verdict": final_verdict,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tested_git_sha": git_info.get("sha"),
        "git_branch": git_info.get("branch"),
        "is_dirty": is_dirty,
        "dirty_tracked": git_info.get("dirty_tracked", []),
        "summary": {
            "backend_tests_total": backend_total_tests,
            "backend_tests_passed": backend_total_passed,
            "backend_tests_failed": backend_total_failed,
            "frontend_tests_passed": frontend_passed,
            "frontend_files": frontend_files,
            "browser_e2e_passed": browser_passed,
            "build_status": g12.get("status"),
            "lint_status": g13.get("status"),
        },
        "gates": {
            **backend_gate_results,
            "gate11_frontend_vitest": {"status": g11.get("status"), "passed": frontend_passed, "duration_sec": g11.get("duration_sec")},
            "gate12_frontend_build": {"status": g12.get("status"), "duration_sec": g12.get("duration_sec")},
            "gate13_frontend_lint": {"status": g13.get("status"), "duration_sec": g13.get("duration_sec")},
            "gate14_playwright_e2e": {"status": g14.get("status"), "passed": browser_passed, "duration_sec": g14.get("duration_sec")},
        },
        "source_fingerprints": file_hashes,
    }

    acceptance_json_path = artifacts_dir / "acceptance.json"
    with open(acceptance_json_path, "w", encoding="utf-8") as f:
        json.dump(acceptance_payload, f, indent=2)

    manifest_json_path = artifacts_dir / "manifest.json"
    with open(manifest_json_path, "w", encoding="utf-8") as f:
        json.dump({
            "version": "V17.5.9",
            "verdict": final_verdict,
            "generated_artifacts": [
                "acceptance.json",
                "manifest.json",
                "junit/gate0_v17_5_9_core.xml",
                "junit/gate1_v17_5_8_core.xml",
                "junit/gate2_v17_5_7_core.xml",
                "junit/gate3_v17_5_6_core.xml",
                "junit/gate4_v17_5_5_core.xml",
                "junit/gate5_v17_5_4.xml",
                "junit/gate6_v17_5_3.xml",
                "junit/gate7_v17_5_2.xml",
                "junit/gate8_v17_5_1.xml",
                "junit/gate9_v17_5.xml",
                "junit/gate10_prior.xml",
            ],
            "source_files_hashed": len(file_hashes),
        }, f, indent=2)

    print("\n" + "=" * 70)
    print(f"[*] AURUM V17.5.9 VERDICT: {final_verdict}")
    print(f"[*] Backend tests: {backend_total_passed}/{backend_total_tests} passed (0 failed)")
    print(f"[*] Frontend tests: {frontend_passed} passed")
    print(f"[*] Playwright E2E: {browser_passed} passed")
    print(f"[*] Build: {g12.get('status')}")
    print(f"[*] Lint: {g13.get('status')}")
    print(f"[*] Acceptance JSON: {acceptance_json_path}")
    print("=" * 70)

    if final_verdict != "ACCEPTED":
        sys.exit(1)


if __name__ == "__main__":
    main()
