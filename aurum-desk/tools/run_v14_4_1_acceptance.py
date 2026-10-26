#!/usr/bin/env python3
"""
V14.4.1 acceptance runner.

Runs every mandatory check as a structured-argv subprocess (no shell wildcards, no hard-coded PASS) and writes
per-command logs + a JSON summary under logs/acceptance/v14_4_1/. Each step is exactly one of:
  PASS | FAIL | PASS_SCOPED (only the pre-declared baseline failures) | NOT_RUN | SKIPPED
and the overall verdict can only be PASS when no mandatory step is FAIL / NOT_RUN / SKIPPED.

Usage: python tools/run_v14_4_1_acceptance.py [--skip-e2e] [--python PATH]
(--skip-e2e records the e2e step as NOT_RUN, which prevents an overall PASS.)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
FRONTEND = os.path.join(ROOT, "frontend")
LOG_DIR = os.path.join(ROOT, "logs", "acceptance", "v14_4_1")

# Failures that exist on the pre-V14.4.1 baseline (Windows `resource` module, replay ledger/integrity fixtures).
# Reported as PASS_SCOPED - never as "all clean".
BASELINE_FAILURES = {
    "tests/test_v11_historical_replay.py::test_s01_and_s02_soak_burst_and_fault_injection",
    "tests/test_v12_4_acceptance.py::test_v124_01_canonical_ledger_schema_and_export",
    "tests/test_v13_5_integrity_acceptance.py::test_01_integrity_pass_on_valid_inputs",
}


def _venv_python() -> str:
    for rel in (("venv", "Scripts", "python.exe"), ("venv", "bin", "python3"), ("venv", "bin", "python")):
        cand = os.path.join(BACKEND, *rel)
        if os.path.exists(cand):
            return cand
    return sys.executable


def run_step(name: str, argv: list[str], cwd: str, env_extra: dict | None = None, timeout: int = 3600) -> dict:
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, f"{name}.log")
    env = dict(os.environ)
    env.update(env_extra or {})
    print(f"--> {name}: {' '.join(argv)}", flush=True)
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)
        code, out = proc.returncode, proc.stdout or ""
    except subprocess.TimeoutExpired as exc:
        code, out = 124, (exc.stdout or "") if isinstance(exc.stdout, str) else ""
    except FileNotFoundError as exc:
        code, out = 127, f"command not found: {exc}"
    dur = time.perf_counter() - t0
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write(out)
    print(f"    exit={code} ({dur:.1f}s) log={log_path}", flush=True)
    return {"name": name, "argv": argv, "exit_code": code, "duration_s": round(dur, 2), "log": log_path, "_out": out}


def classify_pytest(step: dict) -> dict:
    out = step["_out"]
    failed = set(re.findall(r"^FAILED\s+(\S+?)(?:\s+-|$)", out, flags=re.M))
    failed = {f.replace("\\", "/") for f in failed}
    summary = next((ln for ln in reversed(out.splitlines()) if re.search(r"\d+ (passed|failed)", ln)), "")
    if step["exit_code"] == 0:
        status = "PASS"
    elif failed and failed <= BASELINE_FAILURES:
        status = "PASS_SCOPED"
    else:
        status = "FAIL"
    return {"status": status, "summary": summary.strip(), "failed": sorted(failed),
            "unexpected_failures": sorted(failed - BASELINE_FAILURES)}


def simple(step: dict, extra: dict | None = None) -> dict:
    res = {"status": "PASS" if step["exit_code"] == 0 else "FAIL"}
    res.update(extra or {})
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-e2e", action="store_true")
    ap.add_argument("--python", default=None)
    ap.add_argument("--benchmark-cycles", type=int, default=600)
    args = ap.parse_args(argv)

    py = args.python or _venv_python()
    npx = shutil.which("npx") or "npx"
    npm = shutil.which("npm") or "npm"
    results: dict[str, dict] = {}

    def record(key: str, step: dict, verdict: dict) -> None:
        verdict.update({"exit_code": step["exit_code"], "duration_s": step["duration_s"], "log": step["log"],
                        "argv": step["argv"]})
        results[key] = verdict

    s = run_step("backend_full", [py, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"], BACKEND)
    record("backend_full", s, classify_pytest(s))

    targeted = [
        "tests/test_v14_4_1_production_pipeline.py", "tests/test_v14_4_1_contracts.py",
        "tests/test_v14_4_1_entry_guards.py", "tests/test_v14_4_1_session_forecasts.py",
        "tests/test_v14_4_1_scenario_fail_closed.py", "tests/test_v14_4_1_probability.py",
        "tests/test_v14_3_ny_quota.py", "tests/test_v14_3_frequency_audit.py",
    ]
    existing = [t for t in targeted if os.path.exists(os.path.join(BACKEND, *t.split("/")))]
    s = run_step("backend_targeted", [py, "-m", "pytest", *existing, "-q", "-p", "no:cacheprovider"], BACKEND)
    record("backend_targeted", s, classify_pytest(s))

    s = run_step("frontend_typecheck", [npx, "tsc", "-b"], FRONTEND)
    record("frontend_typecheck", s, simple(s))
    s = run_step("frontend_lint", [npx, "oxlint"], FRONTEND)
    warn = re.search(r"Found (\d+) warnings? and (\d+) errors?", s["_out"])
    record("frontend_lint", s, simple(s, {"warnings": int(warn.group(1)) if warn else None,
                                          "errors": int(warn.group(2)) if warn else None}))
    s = run_step("frontend_vitest", [npx, "vitest", "run"], FRONTEND)
    record("frontend_vitest", s, simple(s))
    s = run_step("frontend_build", [npm, "run", "build"], FRONTEND)
    record("frontend_build", s, simple(s))

    if args.skip_e2e:
        results["browser_e2e"] = {"status": "NOT_RUN", "reason": "--skip-e2e"}
    else:
        env = {
            "AURUM_E2E_PYTHON": py,
            "AURUM_E2E_API_PORT": os.environ.get("AURUM_E2E_API_PORT", "18765"),
            "AURUM_E2E_WEB_PORT": os.environ.get("AURUM_E2E_WEB_PORT", "15199"),
            "AURUM_E2E_REPORT": os.path.join(LOG_DIR, "playwright_report.json"),
        }
        s = run_step("browser_e2e", [npx, "playwright", "test", "tests/e2e/v14_4_1_sessions_chart.e2e.ts"],
                     FRONTEND, env_extra=env)
        stats = {}
        try:
            with open(env["AURUM_E2E_REPORT"], encoding="utf-8") as fh:
                stats = json.load(fh).get("stats", {})
        except (OSError, ValueError):
            pass
        # zero executed tests is NOT a pass
        executed = (stats.get("expected", 0) + stats.get("unexpected", 0) + stats.get("flaky", 0))
        verdict = simple(s, {"playwright_stats": stats})
        if s["exit_code"] == 0 and executed == 0:
            verdict["status"] = "FAIL"
            verdict["reason"] = "no browser tests were executed"
        record("browser_e2e", s, verdict)

    bench_out = os.path.join(LOG_DIR, "benchmark.json")
    s = run_step("benchmark", [py, os.path.join(ROOT, "tools", "run_v14_4_chart_acceptance.py"),
                               "--cycles", str(args.benchmark_cycles), "--out", bench_out], ROOT)
    bench = {}
    try:
        with open(bench_out, encoding="utf-8") as fh:
            bench = json.load(fh)
    except (OSError, ValueError):
        pass
    record("benchmark", s, simple(s, {"latency_ms": bench.get("latency_ms"), "not_measured": bench.get("NOT_MEASURED")}))

    s = run_step("git_diff_check", ["git", "diff", "--check"], ROOT)
    record("git_diff_check", s, simple(s))

    blocking = {"FAIL", "NOT_RUN", "SKIPPED"}
    statuses = {k: v["status"] for k, v in results.items()}
    if any(v in blocking for v in statuses.values()):
        overall = "FAIL" if "FAIL" in statuses.values() else "INCOMPLETE"
    elif any(v == "PASS_SCOPED" for v in statuses.values()):
        overall = "PASS_SCOPED"
    else:
        overall = "PASS"
    summary = {"overall": overall, "statuses": statuses, "steps": results,
               "baseline_failures_declared": sorted(BASELINE_FAILURES),
               "probability_research": "NOT_VERIFIED (no real out-of-sample dataset; metrics are null, not 0)",
               "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}
    os.makedirs(LOG_DIR, exist_ok=True)
    out_json = os.path.join(LOG_DIR, "acceptance_v14_4_1.json")
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print("\n=== V14.4.1 ACCEPTANCE ===")
    for k, v in statuses.items():
        print(f"  {k:20s} {v}")
    print(f"  OVERALL              {overall}\n  artifact: {out_json}")
    return 0 if overall in {"PASS", "PASS_SCOPED"} else 1


if __name__ == "__main__":
    sys.exit(main())
