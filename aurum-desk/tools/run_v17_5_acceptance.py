"""
tools/run_v17_5_acceptance.py
Comprehensive acceptance harness for AURUM V17.5: Structural SL, Execution Evidence & Paired Paper Research.
Executes test suites, captures stdout/stderr/exit codes, measures execution metrics,
and writes artifacts/v17_5/acceptance.json.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def get_git_info(repo_root: Path) -> dict:
    try:
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root).decode().strip()
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root).decode().strip()
        status = subprocess.check_output(["git", "status", "--porcelain"], cwd=repo_root).decode().strip()
        return {"branch": branch, "sha": sha, "is_dirty": bool(status)}
    except Exception as e:
        return {"error": str(e)}


def compute_file_hash(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()[:16]


def run_test_suite(python_exe: str, test_files: list, cwd: Path) -> dict:
    cmd = [python_exe, "-m", "pytest"] + test_files + ["-v"]
    t0 = time.time()
    try:
        res = subprocess.run(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,
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
    except Exception as e:
        return {
            "command": " ".join(cmd),
            "exit_code": -1,
            "duration_sec": round(time.time() - t0, 3),
            "error": str(e),
            "status": "ERROR",
        }


def main():
    backend_dir = Path(__file__).resolve().parent.parent / "backend"
    repo_root = backend_dir.parent
    python_exe = str(backend_dir / "venv" / "Scripts" / "python.exe")
    if not os.path.exists(python_exe):
        python_exe = sys.executable

    print(f"[*] Running AURUM V17.5 Acceptance Test Harness...")
    print(f"[*] Backend Dir: {backend_dir}")
    print(f"[*] Python: {python_exe}")

    git_info = get_git_info(repo_root)
    print(f"[*] Git Branch: {git_info.get('branch')}, SHA: {git_info.get('sha')}")

    # V17.5 Specific Suites
    v17_5_tests = [
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

    # Baseline & Regression Suites
    regression_tests = [
        "tests/test_v15_0_m1_scalp_models.py",
        "tests/test_v17_4_3_session_roundtrip.py",
        "tests/test_v17_4_3_atomic_mirrors.py",
        "tests/test_v17_4_2_baseline_probes.py",
        "tests/test_v17_4_2_noop_authority.py",
        "tests/test_v17_4_2_quote_execution.py",
        "tests/test_v17_4_2_quote_revalidation.py",
        "tests/test_v17_4_2_writer_contracts.py",
        "tests/test_v17_4_1_atomic_cas.py",
        "tests/test_v17_4_1_coordinator_flow.py",
        "tests/test_v17_4_1_coordinator_guards.py",
        "tests/test_v17_4_1_quota_not_required.py",
    ]

    print("[*] Running V17.5 Unit and Flow Suites...")
    v17_5_result = run_test_suite(python_exe, v17_5_tests, backend_dir)
    print(f"    V17.5 Tests: {v17_5_result['status']} (in {v17_5_result['duration_sec']}s)")

    print("[*] Running Baseline Regression Suites...")
    regression_result = run_test_suite(python_exe, regression_tests, backend_dir)
    print(f"    Regression Tests: {regression_result['status']} (in {regression_result['duration_sec']}s)")

    # Frontend build verification
    frontend_dir = repo_root / "frontend"
    print("[*] Verifying Frontend TypeScript & Vite Build...")
    t0 = time.time()
    try:
        build_res = subprocess.run(
            ["cmd.exe", "/c", "npm run build"],
            cwd=frontend_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        )
        fe_build_status = "PASS" if build_res.returncode == 0 else "FAIL"
        fe_build_time = round(time.time() - t0, 3)
    except Exception as e:
        fe_build_status = f"ERROR: {e}"
        fe_build_time = round(time.time() - t0, 3)
    print(f"    Frontend Build: {fe_build_status} (in {fe_build_time}s)")

    all_pass = (
        v17_5_result["status"] == "PASS"
        and regression_result["status"] == "PASS"
        and fe_build_status == "PASS"
    )

    manifest = {
        "timestamp_ms": int(time.time() * 1000),
        "git": git_info,
        "technical_status": "IMPLEMENTATION_VERIFIED" if all_pass else "FAILED",
        "empirical_status": "EMPIRICAL_UNPROVEN",
        "v17_5_suite": {
            "status": v17_5_result["status"],
            "exit_code": v17_5_result["exit_code"],
            "duration_sec": v17_5_result["duration_sec"],
        },
        "regression_suite": {
            "status": regression_result["status"],
            "exit_code": regression_result["exit_code"],
            "duration_sec": regression_result["duration_sec"],
        },
        "frontend_build": {
            "status": fe_build_status,
            "duration_sec": fe_build_time,
        },
    }

    # Save artifact
    artifacts_dir = repo_root / "artifacts" / "v17_5"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    out_file = artifacts_dir / "acceptance.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"[*] Acceptance summary saved to: {out_file}")
    print(f"[*] Overall Status: {manifest['technical_status']} | Empirical: {manifest['empirical_status']}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
