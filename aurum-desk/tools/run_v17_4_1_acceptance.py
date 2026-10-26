#!/usr/bin/env python3
"""
tools/run_v17_4_1_acceptance.py
AURUM V17.4.1 Acceptance Runner & Evidence Generator (Parts 140, 176 - 180, 191, 194)

Executes all 8 acceptance gates with strict per-gate timeouts, logs capture,
tree digest tracking, and machine-readable JSON artifact generation:
- Gate 1: Backend Baseline Probes & Contract Strictness (9 tests)
- Gate 2: Database CAS & Authoritative Scope Verification (8 tests)
- Gate 3: Causal Coordinator Full-Flows & Negative Guards (20 tests)
- Gate 4: Selected Baseline & Regression Suite (28 tests)
- Gate 5: Frontend Vitest Suite (498 tests)
- Gate 6: Frontend Production Build (tsc -b && vite build)
- Gate 7: Frontend Oxlint (0 errors)
- Gate 8: Playwright Headless Browser Acceptance (8 tests)

Output:
- artifacts/v17_4_1/acceptance.json
- artifacts/v17_4_1/logs/gate_*.log
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


BASE_SHA = "b09077e6cd9f06636d82892de82dd0fd97dc28b6"
REQUIRED_BRANCH = "fix/v14-1-rr-flow"


def get_git_info(cwd: Path):
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=cwd, text=True).strip()
    except Exception:
        sha = "UNKNOWN"
    try:
        branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=cwd, text=True).strip()
    except Exception:
        branch = "UNKNOWN"
    try:
        tree = subprocess.check_output(["git", "write-tree"], cwd=cwd, text=True).strip()
    except Exception:
        tree = "UNKNOWN"
    try:
        diff_raw = subprocess.check_output(["git", "diff", BASE_SHA], cwd=cwd)
        diff_digest = hashlib.sha256(diff_raw).hexdigest()
    except Exception:
        diff_digest = "UNKNOWN"
    return sha, branch, tree, diff_digest


def run_gate_command(name: str, cmd: str, cwd: Path, env_vars: dict, timeout_sec: int, log_path: Path):
    print(f"\n=======================================================", flush=True)
    print(f"--> [GATE] {name}", flush=True)
    print(f"    Command: {cmd}", flush=True)
    print(f"    Timeout: {timeout_sec}s", flush=True)
    print(f"=======================================================", flush=True)

    env = os.environ.copy()
    if env_vars:
        env.update(env_vars)

    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout_sec,
        )
        dur = time.perf_counter() - t0
        code = proc.returncode
        output = proc.stdout
        timed_out = False
    except subprocess.TimeoutExpired as e:
        dur = time.perf_counter() - t0
        code = -1
        output = (e.stdout or "") + f"\n[GATE TIMEOUT] Process exceeded {timeout_sec}s limit and was terminated."
        timed_out = True

    status = "PASS" if code == 0 else ("TIMEOUT" if timed_out else "FAILED")
    print(f"Result: {status} (code {code}) in {dur:.2f}s", flush=True)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(output)

    summary_lines = [line.strip() for line in output.splitlines() if line.strip()][-4:]
    return {
        "name": name,
        "command": cmd,
        "exit_code": code,
        "duration_s": round(dur, 2),
        "status": status,
        "timed_out": timed_out,
        "log_file": str(log_path.relative_to(cwd.parent if cwd.name == "frontend" else cwd)),
        "summary": summary_lines,
    }


def main():
    root_dir = Path(__file__).resolve().parent.parent
    backend_dir = root_dir / "backend"
    frontend_dir = root_dir / "frontend"
    artifacts_dir = root_dir / "artifacts" / "v17_4_1"
    logs_dir = artifacts_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    python_bin = (
        root_dir / "backend" / "venv" / "Scripts" / "python.exe"
        if sys.platform == "win32"
        else root_dir / "backend" / "venv" / "bin" / "python"
    )

    current_sha, current_branch, tree_hash, diff_digest = get_git_info(root_dir)
    start_time_utc = datetime.now(timezone.utc).isoformat()

    acceptance = {
        "suite": "AURUM_V17_4_1_EXECUTION_ACCEPTANCE",
        "timestamp_utc": start_time_utc,
        "base_sha": BASE_SHA,
        "current_sha": current_sha,
        "branch": current_branch,
        "tree_hash": tree_hash,
        "diff_digest_vs_base": diff_digest,
        "environment": {
            "os": sys.platform,
            "python_executable": str(python_bin),
            "node_cwd": str(frontend_dir),
        },
        "gates": {},
    }

    env_backend = {
        "PYTHONPATH": f"{backend_dir}{os.pathsep}{root_dir}",
    }

    # Gate 1: Baseline Probes & Contracts (9 tests)
    g1 = run_gate_command(
        name="Gate 1: Baseline Probes & Contracts Strictness",
        cmd=f'"{python_bin}" -m pytest backend/tests/test_v17_4_1_baseline_probes.py backend/tests/test_v17_4_1_contracts.py -v',
        cwd=root_dir,
        env_vars=env_backend,
        timeout_sec=60,
        log_path=logs_dir / "gate_1_probes_contracts.log",
    )
    acceptance["gates"]["gate_1_probes_contracts"] = g1

    # Gate 2: Database CAS & Scope Authority (8 tests)
    g2 = run_gate_command(
        name="Gate 2: Database Atomic CAS, Scope & Quota",
        cmd=f'"{python_bin}" -m pytest backend/tests/test_v17_4_1_atomic_cas.py backend/tests/test_v17_4_1_server_scope.py backend/tests/test_v17_4_1_quota_not_required.py -v',
        cwd=root_dir,
        env_vars=env_backend,
        timeout_sec=60,
        log_path=logs_dir / "gate_2_cas_scope_quota.log",
    )
    acceptance["gates"]["gate_2_cas_scope_quota"] = g2

    # Gate 3: Causal Coordinator Full-Flows & Negative Guards (20 tests)
    g3 = run_gate_command(
        name="Gate 3: Execution Coordinator Causal Flows & Guards",
        cmd=f'"{python_bin}" -m pytest backend/tests/test_v17_4_1_coordinator_flow.py backend/tests/test_v17_4_1_coordinator_guards.py -v',
        cwd=root_dir,
        env_vars=env_backend,
        timeout_sec=120,
        log_path=logs_dir / "gate_3_coordinator_guards.log",
    )
    acceptance["gates"]["gate_3_coordinator_guards"] = g3

    # Gate 4: Selected Regression Suite (28 tests)
    g4 = run_gate_command(
        name="Gate 4: Selected Baseline Regression Suite",
        cmd=f'"{python_bin}" -m pytest backend/tests/test_v17_4_api.py backend/tests/test_v17_4_config_transaction.py backend/tests/test_v17_4_entry_session_policy.py backend/tests/test_v17_4_expiry.py backend/tests/test_v17_4_production_flow.py -v',
        cwd=root_dir,
        env_vars=env_backend,
        timeout_sec=90,
        log_path=logs_dir / "gate_4_regression_suite.log",
    )
    acceptance["gates"]["gate_4_regression_suite"] = g4

    # Gate 5: Frontend Vitest Suite (498 tests)
    g5 = run_gate_command(
        name="Gate 5: Frontend Vitest Unit & Component Suite",
        cmd="npm test",
        cwd=frontend_dir,
        env_vars=None,
        timeout_sec=90,
        log_path=logs_dir / "gate_5_frontend_vitest.log",
    )
    acceptance["gates"]["gate_5_frontend_vitest"] = g5

    # Gate 6: Frontend Build (tsc -b && vite build)
    g6 = run_gate_command(
        name="Gate 6: Frontend Production Build",
        cmd="npm run build",
        cwd=frontend_dir,
        env_vars=None,
        timeout_sec=60,
        log_path=logs_dir / "gate_6_frontend_build.log",
    )
    acceptance["gates"]["gate_6_frontend_build"] = g6

    # Gate 7: Frontend Oxlint
    g7 = run_gate_command(
        name="Gate 7: Frontend Lint Verification",
        cmd="npm run lint",
        cwd=frontend_dir,
        env_vars=None,
        timeout_sec=30,
        log_path=logs_dir / "gate_7_frontend_lint.log",
    )
    acceptance["gates"]["gate_7_frontend_lint"] = g7

    # Gate 8: Playwright Browser Acceptance (8 tests)
    g8 = run_gate_command(
        name="Gate 8: Playwright Headless Browser Acceptance",
        cmd="npx playwright test tests/e2e/v17_4_1_entry_sessions.e2e.ts tests/e2e/v17_4_entry_sessions.e2e.ts",
        cwd=frontend_dir,
        env_vars={"AURUM_E2E_API_PORT": "8795", "AURUM_E2E_WEB_PORT": "5195"},
        timeout_sec=120,
        log_path=logs_dir / "gate_8_playwright_e2e.log",
    )
    acceptance["gates"]["gate_8_playwright_e2e"] = g8

    # Overall Summary
    all_passed = all(g["status"] == "PASS" for g in acceptance["gates"].values())
    acceptance["overall_status"] = "IMPLEMENTED_AND_VERIFIED" if all_passed else "FAILED"
    acceptance["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    acceptance["runtime_observed"] = False
    acceptance["cutover_status"] = "CUTOVER_NOT_OBSERVED"

    json_path = artifacts_dir / "acceptance.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(acceptance, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 65)
    print(f"=== AURUM V17.4.1 ACCEPTANCE OVERALL: {acceptance['overall_status']} ===")
    print("=" * 65)
    for gid, ginfo in acceptance["gates"].items():
        print(f"  [{ginfo['status']}] {ginfo['name']} ({ginfo['duration_s']}s)")
    print(f"\nArtifact saved to: {json_path.resolve()}\n")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
