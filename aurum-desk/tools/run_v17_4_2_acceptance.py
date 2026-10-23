#!/usr/bin/env python3
"""
tools/run_v17_4_2_acceptance.py
AURUM V17.4.2 Acceptance Runner & Evidence Generator (Parts 169 - 180, 183 - 187)

Executes all 10 acceptance gates with strict per-gate timeouts, logs capture,
tree digest tracking, and machine-readable JSON artifact generation:
- Gate 1: Baseline Adversarial Probes (Q, N, L, C - 4 tests)
- Gate 2: Quote Revalidation & Execution Parity (Q01-Q09, QE01-QE08 - 17 tests)
- Gate 3: No-op Database Authority & Transaction Concurrency (N01-N08 - 8 tests)
- Gate 4: Session Writer Contracts & Scope Authority (W01-W12 - 12 tests)
- Gate 5: V17.4.1 Regression Suite (37 tests)
- Gate 6: Selected Session/Startup Regressions (32 tests)
- Gate 7: Frontend Vitest Suite (498 tests)
- Gate 8: Frontend Production Build (tsc -b && vite build)
- Gate 9: Frontend Lint Verification (0 errors)
- Gate 10: Playwright Headless Browser Acceptance (8 tests)

Output:
- artifacts/v17_4_2/acceptance.json
- artifacts/v17_4_2/logs/gate_*.log
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


BASE_SHA = "c112a4aa1c365ad1e78a047f8fca61517c4d96c5"
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
        out_raw = e.stdout
        if isinstance(out_raw, bytes):
            out_raw = out_raw.decode("utf-8", errors="replace")
        output = (out_raw or "") + f"\n[GATE TIMEOUT] Process exceeded {timeout_sec}s limit and was terminated."
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
    artifacts_dir = root_dir / "artifacts" / "v17_4_2"
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
        "suite": "AURUM_V17_4_2_EXECUTION_ACCEPTANCE",
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

    # Gate 1: Baseline Adversarial Probes (Q, N, L, C - 4 tests)
    g1 = run_gate_command(
        name="Gate 1: Baseline Adversarial Probes (Q, N, L, C)",
        cmd=f'"{python_bin}" -m pytest backend/tests/test_v17_4_2_baseline_probes.py -v',
        cwd=root_dir,
        env_vars=env_backend,
        timeout_sec=60,
        log_path=logs_dir / "gate_1_baseline_probes.log",
    )
    acceptance["gates"]["gate_1_baseline_probes"] = g1

    # Gate 2: Quote Revalidation & Execution Parity (17 tests)
    g2 = run_gate_command(
        name="Gate 2: Quote Revalidation & Execution Parity (Q01-Q09, QE01-QE08)",
        cmd=f'"{python_bin}" -m pytest backend/tests/test_v17_4_2_quote_revalidation.py backend/tests/test_v17_4_2_quote_execution.py -v',
        cwd=root_dir,
        env_vars=env_backend,
        timeout_sec=90,
        log_path=logs_dir / "gate_2_quote_revalidation_execution.log",
    )
    acceptance["gates"]["gate_2_quote_revalidation_execution"] = g2

    # Gate 3: No-op Database Authority & Concurrency (8 tests)
    g3 = run_gate_command(
        name="Gate 3: No-op Database Authority & Transaction Concurrency (N01-N08)",
        cmd=f'"{python_bin}" -m pytest backend/tests/test_v17_4_2_noop_authority.py -v',
        cwd=root_dir,
        env_vars=env_backend,
        timeout_sec=90,
        log_path=logs_dir / "gate_3_noop_authority.log",
    )
    acceptance["gates"]["gate_3_noop_authority"] = g3

    # Gate 4: Session Writer Contracts & Scope Authority (12 tests)
    g4 = run_gate_command(
        name="Gate 4: Session Writer Contracts & Scope Authority (W01-W12)",
        cmd=f'"{python_bin}" -m pytest backend/tests/test_v17_4_2_writer_contracts.py -v',
        cwd=root_dir,
        env_vars=env_backend,
        timeout_sec=90,
        log_path=logs_dir / "gate_4_writer_contracts.log",
    )
    acceptance["gates"]["gate_4_writer_contracts"] = g4

    # Gate 5: V17.4.1 Regression Suite (37 tests)
    g5 = run_gate_command(
        name="Gate 5: V17.4.1 Regression Suite (Atomic CAS, Coordinator Flow & Guards, Scope, Quota)",
        cmd=f'"{python_bin}" -m pytest backend/tests/test_v17_4_1_atomic_cas.py backend/tests/test_v17_4_1_baseline_probes.py backend/tests/test_v17_4_1_contracts.py backend/tests/test_v17_4_1_coordinator_flow.py backend/tests/test_v17_4_1_coordinator_guards.py backend/tests/test_v17_4_1_quota_not_required.py backend/tests/test_v17_4_1_server_scope.py -v',
        cwd=root_dir,
        env_vars=env_backend,
        timeout_sec=180,
        log_path=logs_dir / "gate_5_v17_4_1_regression.log",
    )
    acceptance["gates"]["gate_5_v17_4_1_regression"] = g5

    # Gate 6: Selected Session/Startup Regressions (32 tests)
    g6 = run_gate_command(
        name="Gate 6: Selected Session & Startup Regressions (V14.4 Suite)",
        cmd=f'"{python_bin}" -m pytest backend/tests/test_v14_4_5_policy_startup.py backend/tests/test_v14_4_10_three_session_entry.py backend/tests/test_v14_4_11_three_session_execution.py backend/tests/test_v14_4_12_forward_paper_session.py -v',
        cwd=root_dir,
        env_vars=env_backend,
        timeout_sec=60,
        log_path=logs_dir / "gate_6_session_startup_regression.log",
    )
    acceptance["gates"]["gate_6_session_startup_regression"] = g6

    # Gate 7: Frontend Vitest Suite (498 tests)
    g7 = run_gate_command(
        name="Gate 7: Frontend Vitest Unit & Component Suite",
        cmd="npm test -- --run",
        cwd=frontend_dir,
        env_vars=None,
        timeout_sec=120,
        log_path=logs_dir / "gate_7_frontend_vitest.log",
    )
    acceptance["gates"]["gate_7_frontend_vitest"] = g7

    # Gate 8: Frontend Build (tsc -b && vite build)
    g8 = run_gate_command(
        name="Gate 8: Frontend Production Build",
        cmd="npm run build",
        cwd=frontend_dir,
        env_vars=None,
        timeout_sec=60,
        log_path=logs_dir / "gate_8_frontend_build.log",
    )
    acceptance["gates"]["gate_8_frontend_build"] = g8

    # Gate 9: Frontend Oxlint
    g9 = run_gate_command(
        name="Gate 9: Frontend Lint Verification",
        cmd="npm run lint",
        cwd=frontend_dir,
        env_vars=None,
        timeout_sec=30,
        log_path=logs_dir / "gate_9_frontend_lint.log",
    )
    acceptance["gates"]["gate_9_frontend_lint"] = g9

    # Gate 10: Playwright Browser Acceptance (8 tests)
    g10 = run_gate_command(
        name="Gate 10: Playwright Headless Browser Acceptance",
        cmd="npx playwright test tests/e2e/v17_4_1_entry_sessions.e2e.ts tests/e2e/v17_4_entry_sessions.e2e.ts",
        cwd=frontend_dir,
        env_vars={"AURUM_E2E_API_PORT": "8768", "AURUM_E2E_WEB_PORT": "5201"},
        timeout_sec=120,
        log_path=logs_dir / "gate_10_playwright_e2e.log",
    )
    acceptance["gates"]["gate_10_playwright_e2e"] = g10

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
    print(f"=== AURUM V17.4.2 ACCEPTANCE OVERALL: {acceptance['overall_status']} ===")
    print("=" * 65)
    for gid, ginfo in acceptance["gates"].items():
        print(f"  [{ginfo['status']}] {ginfo['name']} ({ginfo['duration_s']}s)")
    print(f"\nArtifact saved to: {json_path.resolve()}\n")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
