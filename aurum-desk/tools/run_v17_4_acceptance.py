#!/usr/bin/env python3
"""
V17.4 Full Acceptance Runner (Parts 181, 182, 196)
Executes:
1. Backend V17.4 Suite (59 tests covering timing, contract, policy, persistence, expiry, production flow)
2. Backend V14.4 Session & Lifecycle Regression Suite (493 tests)
3. Frontend Vitest Suite (488 tests)
4. Frontend Production Build (Vite + tsc)
5. Frontend Oxlint (Lint verification)
6. Playwright E2E Browser Acceptance (Chromium headless across V17.4 & V17.3)

Generates:
- artifacts/v17_4/acceptance.json (Machine-readable acceptance log)
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def get_git_info(cwd):
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=cwd, text=True).strip()
    except Exception:
        sha = "UNKNOWN"
    try:
        branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=cwd, text=True).strip()
    except Exception:
        branch = "UNKNOWN"
    return sha, branch


def run_cmd(cmd, cwd=None, env_vars=None):
    print(f"\n--> Executing: {cmd}", flush=True)
    env = os.environ.copy()
    if env_vars:
        env.update(env_vars)

    t0 = time.perf_counter()
    p = subprocess.run(
        cmd,
        shell=True,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    dur = time.perf_counter() - t0
    print(f"Exit code: {p.returncode} ({dur:.2f}s)", flush=True)
    return p.returncode, p.stdout, dur


def main():
    root_dir = Path(__file__).resolve().parent.parent
    artifacts_dir = root_dir / "artifacts" / "v17_4"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    backend_dir = root_dir / "backend"
    frontend_dir = root_dir / "frontend"

    python_bin = (
        root_dir / "backend" / "venv" / "Scripts" / "python.exe"
        if sys.platform == "win32"
        else root_dir / "backend" / "venv" / "bin" / "python"
    )

    git_sha, git_branch = get_git_info(root_dir)
    start_time_utc = datetime.now(timezone.utc).isoformat()

    results = {
        "suite": "AURUM_V17_4_ACCEPTANCE",
        "timestamp_utc": start_time_utc,
        "base_sha": "4977a21deb475bafb0cd8cac6af87a4a0191d15a",
        "current_sha": git_sha,
        "branch": git_branch,
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

    # Gate 1: Backend V17.4 Suite
    v17_4_test_files = list(backend_dir.glob("tests/test_v17_4_*.py"))
    test_files_str = " ".join(f'"{f}"' for f in v17_4_test_files)
    cmd_g1 = f'"{python_bin}" -m pytest {test_files_str} -q'
    code, out, dur = run_cmd(cmd_g1, cwd=root_dir, env_vars=env_backend)
    results["gates"]["gate_1_backend_v17_4"] = {
        "name": "Backend V17.4 Suite",
        "command": cmd_g1,
        "exit_code": code,
        "duration_s": dur,
        "status": "PASS" if code == 0 else "FAIL",
        "summary": out.splitlines()[-3:] if out else [],
    }

    # Gate 2: Backend V14.4 Regression Suite
    v14_4_test_files = list(backend_dir.glob("tests/test_v14_4_*.py"))
    v14_files_str = " ".join(f'"{f}"' for f in v14_4_test_files)
    cmd_g2 = f'"{python_bin}" -m pytest {v14_files_str} -q'
    code, out, dur = run_cmd(cmd_g2, cwd=root_dir, env_vars=env_backend)
    results["gates"]["gate_2_backend_v14_4_regression"] = {
        "name": "Backend V14.4 Regression Suite",
        "command": cmd_g2,
        "exit_code": code,
        "duration_s": dur,
        "status": "PASS" if code == 0 else "FAIL",
        "summary": out.splitlines()[-3:] if out else [],
    }

    # Gate 3: Frontend Vitest
    cmd_g3 = "npm test -- --run"
    code, out, dur = run_cmd(cmd_g3, cwd=frontend_dir)
    results["gates"]["gate_3_frontend_vitest"] = {
        "name": "Frontend Vitest Suite",
        "command": cmd_g3,
        "exit_code": code,
        "duration_s": dur,
        "status": "PASS" if code == 0 else "FAIL",
        "summary": out.splitlines()[-5:] if out else [],
    }

    # Gate 4: Frontend Build
    cmd_g4 = "npm run build"
    code, out, dur = run_cmd(cmd_g4, cwd=frontend_dir)
    results["gates"]["gate_4_frontend_build"] = {
        "name": "Frontend Production Build",
        "command": cmd_g4,
        "exit_code": code,
        "duration_s": dur,
        "status": "PASS" if code == 0 else "FAIL",
        "summary": out.splitlines()[-3:] if out else [],
    }

    # Gate 5: Frontend Lint
    cmd_g5 = "npm run lint"
    code, out, dur = run_cmd(cmd_g5, cwd=frontend_dir)
    results["gates"]["gate_5_frontend_lint"] = {
        "name": "Frontend Oxlint",
        "command": cmd_g5,
        "exit_code": code,
        "duration_s": dur,
        "status": "PASS" if code == 0 else "FAIL",
        "summary": out.splitlines()[-3:] if out else [],
    }

    # Gate 6: Playwright E2E Browser Acceptance
    env_e2e = {
        "AURUM_E2E_API_PORT": "8795",
        "AURUM_E2E_WEB_PORT": "5195",
    }
    cmd_g6 = "npx playwright test tests/e2e/v17_4_entry_sessions.e2e.ts tests/e2e/v17_3_monitor_context.e2e.ts"
    code, out, dur = run_cmd(cmd_g6, cwd=frontend_dir, env_vars=env_e2e)
    results["gates"]["gate_6_playwright_browser"] = {
        "name": "Playwright E2E Browser Acceptance",
        "command": cmd_g6,
        "exit_code": code,
        "duration_s": dur,
        "status": "PASS" if code == 0 else "FAIL",
        "summary": out.splitlines()[-5:] if out else [],
    }

    # Overall Status Evaluation
    all_passed = all(g["status"] == "PASS" for g in results["gates"].values())
    results["overall_status"] = "IMPLEMENTED_AND_VERIFIED" if all_passed else "FAILED"
    results["completed_at_utc"] = datetime.now(timezone.utc).isoformat()

    json_path = artifacts_dir / "acceptance.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("\n==========================================")
    print(f"=== AURUM V17.4 ACCEPTANCE RESULT: {results['overall_status']} ===")
    print("==========================================")
    for g_id, g_info in results["gates"].items():
        print(f"  [{g_info['status']}] {g_info['name']} ({g_info['duration_s']:.2f}s)")
    print(f"\nArtifact saved: {json_path}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
