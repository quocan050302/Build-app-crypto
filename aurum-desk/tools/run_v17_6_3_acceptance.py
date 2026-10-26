#!/usr/bin/env python3
"""AURUM V17.6.3 reproducible full regression runner.

Execute from a clean checkout with application dependencies installed.
Writes results to an external temporary directory so evidence does not make
the tested Git revision dirty. No simulated soak is called real observation.
"""
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"


def git(*arguments):
    return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()


def run_gate(name, command, cwd, env, out_dir, timeout=480):
    start = time.monotonic()
    try:
        result = subprocess.run(
            command, cwd=cwd, env=env, capture_output=True,
            text=True, errors="replace", timeout=timeout,
        )
        code = result.returncode
        stdout = (result.stdout or "") + "\n" + (result.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        code = -1
        stdout = repr(exc)
    (out_dir / f"{name}.log").write_text(stdout, encoding="utf-8")
    gate = {
        "name": name, "command": command, "exit_code": code,
        "elapsed_sec": round(time.monotonic() - start, 2),
        "status": "PASS" if code == 0 else "FAIL",
        "log": str(out_dir / f"{name}.log"),
    }
    if name == "backend_full":
        path = out_dir / "backend_full.xml"
        if path.exists():
            root = ET.parse(path).getroot()
            suites = [root] if root.tag == "testsuite" else list(root.findall(".//testsuite"))
            # Avoid double counting aggregate nested suites.
            if root.tag != "testsuite":
                suites = [s for s in suites if not list(s.findall("testsuite"))]
            test_count = sum(int(s.get("tests", "0")) for s in suites)
            failures = sum(int(s.get("failures", "0")) + int(s.get("errors", "0")) for s in suites)
            gate.update({"tests": test_count, "failed": failures})
            if test_count < 1 or failures:
                gate["status"] = "FAIL"
        else:
            gate["status"] = "FAIL"
            gate["reason"] = "MISSING_JUNIT"
    elif name == "frontend_vitest":
        clean_stdout = re.sub(r"\x1b\[[0-9;]*m", "", stdout)
        match = re.search(r"Tests\s+(\d+)\s+passed", clean_stdout)
        gate["passed"] = int(match.group(1)) if match else None
        if gate["passed"] is None or gate["passed"] < 1:
            gate["status"] = "FAIL"
            gate["reason"] = "UNVERIFIABLE_VITEST_TEST_COUNT"
    elif name == "browser_e2e":
        clean_stdout = re.sub(r"\x1b\[[0-9;]*m", "", stdout)
        match = re.search(r"(\d+)\s+passed", clean_stdout)
        gate["passed"] = int(match.group(1)) if match else None
        if gate["passed"] is None or gate["passed"] < 1:
            gate["status"] = "FAIL"
            gate["reason"] = "UNVERIFIABLE_PLAYWRIGHT_TEST_COUNT"
    return gate


def main():
    out_dir = Path(tempfile.mkdtemp(prefix="aurum_v17_6_3_acceptance_"))
    try:
        source_sha = git("rev-parse", "HEAD")
        status_before = git("status", "--porcelain", "--untracked-files=all")
    except (OSError, subprocess.CalledProcessError) as exc:
        print("BLOCKED: Cannot verify Git source:", exc)
        return 2

    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND)
    env["TESTING"] = "1"
    python = str(BACKEND / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    if not Path(python).exists():
        python = sys.executable

    tasks = [
        ("backend_full", [python, "-m", "pytest", "tests", "-q",
            f"--junitxml={out_dir / 'backend_full.xml'}"], BACKEND, 1200),
        ("frontend_vitest", ["npm", "test", "--", "--run"], FRONTEND, 600),
        ("frontend_build", ["npm", "run", "build"], FRONTEND, 600),
        ("frontend_lint", ["npm", "run", "lint"], FRONTEND, 300),
        ("browser_e2e", ["npm", "run", "test:e2e", "--",
            "tests/e2e/v17_6_observation.e2e.ts"], FRONTEND, 600),
    ]
    gates = {}
    for name, cmd, cwd, timeout in tasks:
        print(f"Running {name}...", flush=True)
        gates[name] = run_gate(name, cmd, cwd, env, out_dir, timeout=timeout)
        print(name, gates[name]["status"], flush=True)

    source_unchanged = git("rev-parse", "HEAD") == source_sha
    status_after = git("status", "--porcelain", "--untracked-files=all")
    # Only an unchanged, clean source revision can be accepted.
    clean = not status_before and not status_after and source_unchanged
    accepted = clean and len(gates) == len(tasks) and all(
        g["status"] == "PASS" for g in gates.values()
    )
    report = {
        "version": "V17.6.3",
        "verdict": "ACCEPTED" if accepted else "REJECTED",
        "source_sha": source_sha,
        "source_clean_before": not bool(status_before),
        "source_clean_after": not bool(status_after),
        "source_unchanged": source_unchanged,
        "environment": {"os": platform.system(), "python": platform.python_version()},
        "gates": gates,
        "evidence_type": "LOCAL_REGRESSION_NOT_LIVE_FORWARD_SOAK",
    }
    report_path = out_dir / "acceptance.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Report:", report_path)
    print("Verdict:", report["verdict"])
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
