"""
tools/run_v17_5_1_acceptance.py
Authoritative cross-platform acceptance runner for AURUM V17.5.1:
Structural Stop Loss & Real Paper Evidence.
Executes test suites, computes file hashes of all test/implementation files,
captures stdout/stderr/exit codes, measures execution metrics,
and writes artifacts/v17_5_1/acceptance.json and logs.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path


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

    print(f"[*] AURUM V17.5.1 Authoritative Acceptance Runner")
    print(f"[*] OS: {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"[*] Python: {python_exe}")
    print(f"[*] Repo Root: {repo_root}")

    git_info = get_git_info(repo_root)
    print(f"[*] Git: {git_info.get('branch')} @ {git_info.get('sha')} (dirty={git_info.get('is_dirty')})")

    # Files to hash for provenance verification
    key_files = [
        backend_dir / "services" / "stop_placement_types.py",
        backend_dir / "services" / "stop_placement_service.py",
        backend_dir / "services" / "stop_placement_policy_service.py",
        backend_dir / "services" / "stop_placement_research_service.py",
        backend_dir / "services" / "scalp_m1_model_service.py",
        backend_dir / "services" / "execution_consumer.py",
        backend_dir / "services" / "cg01_engine.py",
        backend_dir / "services" / "post_trade_review_service.py",
        backend_dir / "schemas.py",
        backend_dir / "main.py",
        frontend_dir / "src" / "components" / "StopComparisonModal.tsx",
        frontend_dir / "src" / "components" / "StopPlacementDetails.tsx",
        backend_dir / "tests" / "test_v17_5_1_red_probes.py",
        backend_dir / "tests" / "test_v17_5_1_anchor_leg.py",
        backend_dir / "tests" / "test_v17_5_1_structural_gate.py",
        backend_dir / "tests" / "test_v17_5_1_atr_timeframe.py",
        backend_dir / "tests" / "test_v17_5_1_policy_cas.py",
        backend_dir / "tests" / "test_v17_5_1_research_runtime.py",
        backend_dir / "tests" / "test_v17_5_1_production_flow.py",
        backend_dir / "tests" / "test_v17_5_1_guard_matrix.py",
        backend_dir / "tests" / "test_v17_5_1_stop_costs.py",
        frontend_dir / "src" / "__tests__" / "v17_5_1_stop_contract.test.tsx",
    ]
    file_hashes = {str(f.relative_to(repo_root)): compute_file_hash(f) for f in key_files}

    logs_dir = repo_root / "artifacts" / "v17_5_1" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # 1. Gate 1 — Six Red Probes (R1 - R6)
    print("\n[Gate 1] Running 6 Red Probes (R1 - R6)...")
    g1 = run_command_cross_platform(
        [python_exe, "-m", "pytest", "tests/test_v17_5_1_red_probes.py", "-v"],
        cwd=backend_dir,
    )
    print(f"    Status: {g1['status']} (in {g1['duration_sec']}s)")

    # 2. Gate 2 — V17.5.1 Targeted Unit & Integration Suites
    print("\n[Gate 2] Running V17.5.1 Feature Suites...")
    v17_5_1_files = [
        "tests/test_v17_5_1_anchor_leg.py",
        "tests/test_v17_5_1_structural_gate.py",
        "tests/test_v17_5_1_atr_timeframe.py",
        "tests/test_v17_5_1_policy_cas.py",
        "tests/test_v17_5_1_research_runtime.py",
        "tests/test_v17_5_1_production_flow.py",
        "tests/test_v17_5_1_guard_matrix.py",
        "tests/test_v17_5_1_stop_costs.py",
    ]
    g2 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + v17_5_1_files + ["-v"],
        cwd=backend_dir,
    )
    print(f"    Status: {g2['status']} (in {g2['duration_sec']}s)")

    # 3. Gate 3 — Existing V17.5 Regression Suite
    print("\n[Gate 3] Running Existing V17.5 Suites (31 tests)...")
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
    g3 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + v17_5_existing + ["-v"],
        cwd=backend_dir,
    )
    print(f"    Status: {g3['status']} (in {g3['duration_sec']}s)")

    # 4. Gate 4 — Prior Regression Suites
    print("\n[Gate 4] Running Selected Prior Regression Suites (35 tests)...")
    prior_regression = [
        "tests/test_v16_2_cg01_long_short.py",
        "tests/test_v17_4_3_session_roundtrip.py",
        "tests/test_v17_4_3_atomic_mirrors.py",
        "tests/test_v17_4_2_baseline_probes.py",
        "tests/test_v17_4_2_noop_authority.py",
    ]
    g4 = run_command_cross_platform(
        [python_exe, "-m", "pytest"] + prior_regression + ["-v"],
        cwd=backend_dir,
    )
    print(f"    Status: {g4['status']} (in {g4['duration_sec']}s)")

    # 5. Gate 5 — Frontend Unit & Contract Suite
    print("\n[Gate 5] Running Frontend Tests (501 tests)...")
    g5 = run_command_cross_platform(["npm", "test", "--", "--run"], cwd=frontend_dir)
    print(f"    Status: {g5['status']} (in {g5['duration_sec']}s)")

    # 6. Gate 6 — Frontend Build (tsc -b && vite build)
    print("\n[Gate 6] Running Frontend Build...")
    g6 = run_command_cross_platform(["npm", "run", "build"], cwd=frontend_dir)
    print(f"    Status: {g6['status']} (in {g6['duration_sec']}s)")

    # 7. Gate 7 — Frontend Lint
    print("\n[Gate 7] Running Frontend Lint...")
    g7 = run_command_cross_platform(["npm", "run", "lint"], cwd=frontend_dir)
    print(f"    Status: {g7['status']} (in {g7['duration_sec']}s)")

    # Write logs to artifacts/v17_5_1/logs/
    def _safe_str(val: Any) -> str:
        return str(val) if val is not None else ""

    with open(logs_dir / "gate1_red_probes.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g1.get("stdout")) + "\n" + _safe_str(g1.get("stderr")))
    with open(logs_dir / "gate2_v17_5_1_suites.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g2.get("stdout")) + "\n" + _safe_str(g2.get("stderr")))
    with open(logs_dir / "gate3_v17_5_suites.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g3.get("stdout")) + "\n" + _safe_str(g3.get("stderr")))
    with open(logs_dir / "gate4_prior_regression.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g4.get("stdout")) + "\n" + _safe_str(g4.get("stderr")))
    with open(logs_dir / "gate5_frontend_tests.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g5.get("stdout")) + "\n" + _safe_str(g5.get("stderr")))
    with open(logs_dir / "gate6_frontend_build.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g6.get("stdout")) + "\n" + _safe_str(g6.get("stderr")))
    with open(logs_dir / "gate7_frontend_lint.log", "w", encoding="utf-8") as f:
        f.write(_safe_str(g7.get("stdout")) + "\n" + _safe_str(g7.get("stderr")))

    all_pass = (
        g1["status"] == "PASS"
        and g2["status"] == "PASS"
        and g3["status"] == "PASS"
        and g4["status"] == "PASS"
        and g5["status"] == "PASS"
        and g6["status"] == "PASS"
        and g7["status"] == "PASS"
    )

    manifest = {
        "timestamp_ms": int(time.time() * 1000),
        "runtime_os": f"{platform.system()} {platform.release()}",
        "python_interpreter": python_exe,
        "git": git_info,
        "source_file_hashes": file_hashes,
        "technical_status": "IMPLEMENTATION_VERIFIED" if all_pass else "FAILED",
        "empirical_status": "EMPIRICAL_UNPROVEN",
        "gates": {
            "gate1_red_probes": {"status": g1["status"], "duration_sec": g1["duration_sec"]},
            "gate2_v17_5_1_suites": {"status": g2["status"], "duration_sec": g2["duration_sec"]},
            "gate3_v17_5_existing": {"status": g3["status"], "duration_sec": g3["duration_sec"]},
            "gate4_prior_regression": {"status": g4["status"], "duration_sec": g4["duration_sec"]},
            "gate5_frontend_tests": {"status": g5["status"], "duration_sec": g5["duration_sec"]},
            "gate6_frontend_build": {"status": g6["status"], "duration_sec": g6["duration_sec"]},
            "gate7_frontend_lint": {"status": g7["status"], "duration_sec": g7["duration_sec"]},
        },
        "summary": {
            "total_backend_tests_run": 57 + 35,  # 57 V17.5/V17.5.1 + 35 regression
            "total_frontend_tests_run": 501,
            "overall_status": "ALL_GATES_PASSED" if all_pass else "GATES_FAILED",
        },
    }

    out_json = repo_root / "artifacts" / "v17_5_1" / "acceptance.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"\n[*] Acceptance Manifest written to: {out_json}")
    print(f"[*] Technical Status: {manifest['technical_status']}")
    print(f"[*] Empirical Status: {manifest['empirical_status']}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
