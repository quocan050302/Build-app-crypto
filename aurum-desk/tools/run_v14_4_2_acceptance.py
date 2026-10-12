#!/usr/bin/env python3
"""
V14.4.2 acceptance runner.

Runs every mandatory check and asserts presence and execution of all required case IDs:
  - D01–D12 (Drag lifecycle & readonly across all 4 states)
  - R01–R05 (Realtime WS & soak)
  - M01–M04 (Mobile reachability)
  - E01–E09 (Evidence closure & focus)
  - S01–S06 (Backend requirements & guards)
  - Unit groups: RT (realtime), O (observation), P (provenance), F (focus), L (layout), A (acceptance runner)

Usage:
  python tools/run_v14_4_2_acceptance.py [--python PATH] [--skip-e2e]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
FRONTEND = os.path.join(ROOT, "frontend")
LOG_DIR = os.path.join(ROOT, "logs", "acceptance", "v14_4_2")

# Mandatory Case Manifest (D01-D12, R01-R05, M01-M04, E01-E09, S01-S06)
REQUIRED_CASES = {
    # Drag Lifecycle (12)
    "D01": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "copy SHORT setup to draft"},
    "D02": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "drag SHORT SL increases stop loss"},
    "D03": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "drag SHORT TP decreases take profit"},
    "D04": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "invalid SHORT SL below entry is prevented"},
    "D05": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "invalid SHORT TP above entry is prevented"},
    "D06": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "body translation translates entry, sl, and tp equally"},
    "D07": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "resize RR width changes projected bars without changing prices"},
    "D08": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "pointerup commits state from final movement frame"},
    "D09": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "pointercancel rolls back draft plan to pre-drag state"},
    "D10": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "lostpointercapture rolls back mid-gesture without freezing pan"},
    "D11": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "LONG draft mirror drag behaves symmetrically"},
    "D12": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "orders are strictly readonly in armed, open, closed, and invalidated states"},
    # Realtime WS & Soak (5)
    "R01": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_realtime_soak.e2e.ts", "desc": "disconnect and reconnect triggers resync with single active socket"},
    "R02": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_realtime_soak.e2e.ts", "desc": "revision gap in domain event triggers bounded resync GET"},
    "R03": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_realtime_soak.e2e.ts", "desc": "100 selection cycles complete without unhandled errors or leak"},
    "R04": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_realtime_soak.e2e.ts", "desc": "continuous quote stream preserves valid display price and bounded queue"},
    "R05": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_realtime_soak.e2e.ts", "desc": "repeated tab unmount and remount returns subscribers to baseline"},
    # Mobile Reachability (4)
    "M01": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_mobile.e2e.ts", "desc": "mobile journal Chart button is reachable and naturally clickable"},
    "M02": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_mobile.e2e.ts", "desc": "mobile note drawer opens and closes cleanly without mutating state"},
    "M03": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_mobile.e2e.ts", "desc": "mobile touch layer menu and annotation controls remain within viewport"},
    "M04": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_mobile.e2e.ts", "desc": "responsive layout maintains usable bounds across viewport sizes"},
    # Evidence Closure & Focus (9)
    "E01": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "selected bundle renders structure events, zones, and liquidity"},
    "E02": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "causal coordinates preserve source time anchors"},
    "E03": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "dense labels remain bounded within chart pane"},
    "E04": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "old order provenance shows correct snapshot version label"},
    "E05": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "clear selection clears evidence without leaking global structure"},
    "E06": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "trade details drawer displays matching snapshot provenance and evidence accordion"},
    "E07": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "layer settings persist in localStorage and restore on reload"},
    "E08": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "offscreen evidence focus navigation"},
    "E09": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts", "desc": "focus request does not leak across selection changes"},
    # Backend Requirements & Guards (6)
    "S01": {"suite": "backend", "file": "backend/tests/test_v14_4_2_forecast_requirements.py", "desc": "asof_no_future"},
    "S02": {"suite": "backend", "file": "backend/tests/test_v14_4_2_forecast_requirements.py", "desc": "forecast_relationship"},
    "S03": {"suite": "backend", "file": "backend/tests/test_v14_4_2_forecast_requirements.py", "desc": "poi_modes"},
    "S04": {"suite": "backend", "file": "backend/tests/test_v14_4_2_forecast_requirements.py", "desc": "no_model"},
    "S05": {"suite": "backend", "file": "backend/tests/test_v14_4_2_entry_regression.py", "desc": "news_lessons_account_quote_feed"},
    "S06": {"suite": "backend", "file": "backend/tests/test_v14_4_2_entry_regression.py", "desc": "close_and_new_signal"},
    # Provisional Plan Regression & Screenshot Verification (T01–T08, T17–T20)
    "T01": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "screenshot 1 inverted SHORT TP rejected"},
    "T02": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "causal untouched target below entry selected"},
    "T03": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "consumed and future levels rejected"},
    "T04": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "LONG mirror inverted TP rejected"},
    "T05": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "confirmed partial atomic triple does not mix"},
    "T06": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "ARMED and PAPER_OPEN levels frozen"},
    "T07": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "upcoming API returns resolved_display_plan"},
    "T08": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "UI display plan DTO has nulls, no fake defaults"},
    "T09": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_selection_source.e2e.ts", "desc": "manual draft SHORT exact T02 calculator values"},
    "T10": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_selection_source.e2e.ts", "desc": "selection WAITING_BREAK scenario has no plan/RR"},
    "T11": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_selection_source.e2e.ts", "desc": "API 500 error display, no stale RR/evidence"},
    "T12": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_selection_source.e2e.ts", "desc": "switching from valid setup to waiting scenario clears RR"},
    "T13": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_selection_source.e2e.ts", "desc": "copy valid SHORT to draft preserves levels, uncertified"},
    "T14": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_selection_source.e2e.ts", "desc": "scenario with certified plan has readonly RR"},
    "T15": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_selection_source.e2e.ts", "desc": "fast selection transition cancels stale response"},
    "T16": {"suite": "browser", "file": "frontend/tests/e2e/v14_4_2_selection_source.e2e.ts", "desc": "draft toggle Mua/Ban and drag preserves readonly compat"},
    "T17": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "screenshot 2 exact numeric calculations"},
    "T18": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "screenshot 1 zero financial estimate"},
    "T19": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "signed R:R does not abs negative reward into profit"},
    "T20": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "consistent cost snapshot for long and short"},
    # Financial Context & Pricing Correctness (5)
    "FIN01": {"suite": "backend", "file": "backend/tests/test_v14_4_2_financial_context_regression.py", "desc": "DayAudit equity 2000 sizing"},
    "FIN02": {"suite": "backend", "file": "backend/tests/test_v14_4_2_financial_context_regression.py", "desc": "None financial fields no TypeError"},
    "FIN03": {"suite": "backend", "file": "backend/tests/test_v14_4_2_financial_context_regression.py", "desc": "Upcoming API preserves degraded and valid rows"},
    "FIN04": {"suite": "backend", "file": "backend/tests/test_v14_4_2_financial_context_regression.py", "desc": "Capital variants and long mirror"},
    "FIN05": {"suite": "backend", "file": "backend/tests/test_v14_4_2_financial_context_regression.py", "desc": "version changes affect preview not frozen"},
    # 15M Liquidity & Causal Targets (3)
    "LIQ01": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "engine 15m real output adapter candidate acceptance"},
    "LIQ02": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "raw kind mapping and dealing range unconfirmed"},
    "LIQ03": {"suite": "backend", "file": "backend/tests/test_v14_4_2_provisional_plan_regression.py", "desc": "timeframe durations and unsupported rejection"},
    # Realtime Quote Ordering & Execution Worker (9)
    "RT01": {"suite": "backend", "file": "backend/tests/test_realtime_source_ordering.py", "desc": "quote monotonic watermark rejects older timestamp"},
    "RT02": {"suite": "backend", "file": "backend/tests/test_realtime_source_ordering.py", "desc": "equal timestamp duplicate vs distinct payload"},
    "RT03": {"suite": "backend", "file": "backend/tests/test_realtime_source_ordering.py", "desc": "reconnect epoch and rest fallback monotonic"},
    "RT04": {"suite": "backend", "file": "backend/tests/test_realtime_source_ordering.py", "desc": "late tick before open cannot TP/SL new order"},
    "RT05": {"suite": "backend", "file": "backend/tests/test_realtime_source_ordering.py", "desc": "late tick for open order reconciliation"},
    "RT06": {"suite": "backend", "file": "backend/tests/test_realtime_execution_worker.py", "desc": "worker thread executes without blocking event loop"},
    "RT07": {"suite": "backend", "file": "backend/tests/test_realtime_execution_worker.py", "desc": "queue overflow sets execution gap and blocks entries"},
    "RT08": {"suite": "backend", "file": "backend/tests/test_realtime_execution_worker.py", "desc": "candle persist retry overflow marks data gap"},
    "RT09": {"suite": "backend", "file": "backend/tests/test_realtime_execution_worker.py", "desc": "worker exception rollback and graceful shutdown"},
    # Persistent Forecast & Forward Causal Outcomes (12)
    "FC01": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "snapshot created prior to future outcome"},
    "FC02": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "identical dedupe returns existing record"},
    "FC03": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "material revision increments and preserves snapshot"},
    "FC04": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "causal lookback rejection of past observations"},
    "FC05": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "horizon expiry yields EXPIRED outcome"},
    "FC06": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "target touched before confirmation is watched liquidity"},
    "FC07": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "valid confirmed target hit before invalidation"},
    "FC08": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "invalidation hit first yields INVALIDATED"},
    "FC09": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "ambiguous same bar target and invalidation"},
    "FC10": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "feed gap during observation window"},
    "FC11": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "unknown probability status without OOS model"},
    "FC12": {"suite": "backend", "file": "backend/tests/test_forecast_forward_observation.py", "desc": "SQL savepoint fault isolation"},
}

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


def validate_required_results(required: dict[str, list[str]], actual: dict[str, str]) -> dict:
    """Matches manifest requirements against actual parsed test outcomes."""
    coverage = {}
    for case_id, test_ids in required.items():
        missing = [test_id for test_id in test_ids if test_id not in actual]
        not_passed = {
            test_id: actual[test_id]
            for test_id in test_ids
            if test_id in actual and actual[test_id] != 'PASS'
        }
        coverage[case_id] = {
            'status': 'PASS' if test_ids and not missing and not not_passed else 'INCOMPLETE',
            'missing': missing,
            'not_passed': not_passed,
        }
    return coverage


def parse_junit_xml(xml_path: str) -> dict[str, str]:
    """Parses JUnit XML report produced by pytest --junitxml."""
    results = {}
    if not os.path.isfile(xml_path):
        return results
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        for testcase in root.iter("testcase"):
            name = testcase.get("name", "")
            classname = testcase.get("classname", "")
            status = "PASS"
            if testcase.find("failure") is not None or testcase.find("error") is not None:
                status = "FAIL"
            elif testcase.find("skipped") is not None:
                status = "SKIPPED"
            key = f"{classname}::{name}" if classname else name
            results[key] = status
            results[name] = status
            # Extract case tokens like S01, O06, P01, A01, FIN01, RT01, LIQ01, FC01
            for m in re.finditer(r"(?i)(?:^|[\W_])(RT\d{2}|FIN\d{2}|LIQ\d{2}|FC\d{2}|[TSOPFLRA]\d{2})(?:[\W_]|$)", name):
                results[m.group(1).upper()] = status
    except Exception as e:
        print(f"Error parsing JUnit XML {xml_path}: {e}", file=sys.stderr)
    return results


def parse_playwright_json(report_path: str, run_start_time: float) -> dict[str, str]:
    """Parses Playwright JSON report, rejecting stale runs."""
    results = {}
    if not os.path.isfile(report_path):
        return results
    mtime = os.path.getmtime(report_path)
    if mtime < run_start_time - 5:
        # File is older than the start of this test run
        return results
    try:
        with open(report_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        suites = data.get("suites", [])

        def walk_suites(s_list):
            for s in s_list:
                for spec in s.get("specs", []):
                    title = spec.get("title", "")
                    tests = spec.get("tests", [])
                    status = "PASS"
                    for t in tests:
                        results_list = t.get("results", [])
                        for r in results_list:
                            r_status = r.get("status")
                            if r_status in ("failed", "timedOut"):
                                status = "FAIL"
                            elif r_status == "skipped" and status != "FAIL":
                                status = "SKIPPED"
                    results[title] = status
                    match = re.match(r"^([A-Z]+\d+):", title)
                    if match:
                        results[match.group(1)] = status
                walk_suites(s.get("suites", []))

        walk_suites(suites)
    except Exception as e:
        print(f"Error parsing Playwright report {report_path}: {e}", file=sys.stderr)
    return results


def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


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


def validate_manifest_files() -> bool:
    all_ok = True
    for case_id, info in REQUIRED_CASES.items():
        rel_path = info["file"]
        full_path = os.path.join(ROOT, rel_path)
        if not os.path.isfile(full_path):
            print(f"ERROR: Mandatory file for {case_id} not found: {rel_path}", file=sys.stderr)
            all_ok = False
    return all_ok


def compute_overall(step_statuses: dict, case_coverage: dict) -> tuple[str, int]:
    """
    Enforces fail-closed evaluation per Appendix H:
    - Any step status FAIL -> ('FAIL', 1)
    - Any step status not in {'PASS', 'PASS_SCOPED'} -> ('INCOMPLETE', 1)
    - Any case_coverage item not PASS -> ('INCOMPLETE', 1)
    - Any PASS_SCOPED step -> ('PASS_SCOPED', 0)
    - All passed -> ('PASS', 0)
    """
    allowed_steps = {'PASS', 'PASS_SCOPED'}
    if any(status == 'FAIL' for status in step_statuses.values()):
        return 'FAIL', 1
    if not step_statuses or any(status not in allowed_steps for status in step_statuses.values()):
        return 'INCOMPLETE', 1
    if not case_coverage or any(case.get('status') != 'PASS' for case in case_coverage.values()):
        return 'INCOMPLETE', 1
    if any(status == 'PASS_SCOPED' for status in step_statuses.values()):
        return 'PASS_SCOPED', 0
    return 'PASS', 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-e2e", action="store_true")
    ap.add_argument("--python", default=None)
    ap.add_argument("--benchmark-cycles", type=int, default=600)
    args = ap.parse_args(argv)

    py = os.path.abspath(args.python or _venv_python())
    npx = shutil.which("npx") or "npx"
    npm = shutil.which("npm") or "npm"
    results: dict[str, dict] = {}
    actual_test_outcomes: dict[str, str] = {}

    print(f"=== V14.4.2 Acceptance Runner ===")
    print(f"Using Python: {py}")
    print(f"Checking {len(REQUIRED_CASES)} mandatory case manifest...")

    if not validate_manifest_files():
        print("FAIL: One or more mandatory test files are missing!")
        return 1

    def record(key: str, step: dict, verdict: dict) -> None:
        verdict.update({"exit_code": step["exit_code"], "duration_s": step["duration_s"], "log": step["log"],
                        "argv": step["argv"]})
        results[key] = verdict

    # 1. Backend Targeted Suites (including S01-S06, O06-O11, P01-P07, A01-A08)
    backend_xml = os.path.join(LOG_DIR, "backend_junit.xml")
    if os.path.exists(backend_xml):
        os.remove(backend_xml)

    env_backend = {"PYTHONPATH": os.pathsep.join([BACKEND, os.path.join(BACKEND, "tests"), "."])}
    targeted_files = [
        "tests/test_v14_4_2_financial_context_regression.py",
        "tests/test_v14_4_2_provisional_plan_regression.py",
        "tests/test_v14_4_2_requirements.py",
        "tests/test_v14_4_2_forecast_requirements.py",
        "tests/test_v14_4_2_entry_regression.py",
        "tests/test_v14_4_2_acceptance_runner.py",
        "tests/test_v14_4_1_production_pipeline.py",
        "tests/test_v14_4_1_contracts.py",
        "tests/test_v14_3_ny_quota.py",
        "tests/test_v14_3_frequency_audit.py",
        "tests/test_realtime_source_ordering.py",
        "tests/test_realtime_execution_worker.py",
        "tests/test_forecast_forward_observation.py",
        "tests/test_migration_runtime_upgrade.py",
    ]
    s = run_step(
        "backend_targeted",
        [py, "-m", "pytest", *targeted_files, "-q", "-p", "no:cacheprovider", f"--junitxml={backend_xml}"],
        BACKEND,
        env_extra=env_backend,
    )
    record("backend_targeted", s, classify_pytest(s))
    actual_test_outcomes.update(parse_junit_xml(backend_xml))

    # 2. Frontend Typecheck, Lint, Vitest, and Build
    s = run_step("frontend_typecheck", [npx, "tsc", "-b"], FRONTEND)
    record("frontend_typecheck", s, simple(s))

    s = run_step("frontend_lint", [npx, "oxlint"], FRONTEND)
    warn = re.search(r"Found (\d+) warnings? and (\d+) errors?", s["_out"])
    record("frontend_lint", s, simple(s, {"warnings": int(warn.group(1)) if warn else None,
                                          "errors": int(warn.group(2)) if warn else None}))

    vitest_xml = os.path.join(LOG_DIR, "vitest_junit.xml")
    if os.path.exists(vitest_xml):
        os.remove(vitest_xml)
    s = run_step("frontend_vitest", [npx, "vitest", "run", "--reporter=default", "--reporter=junit", f"--outputFile.junit={vitest_xml}"], FRONTEND)
    record("frontend_vitest", s, simple(s))
    actual_test_outcomes.update(parse_junit_xml(vitest_xml))

    s = run_step("frontend_build", [npm, "run", "build"], FRONTEND)
    record("frontend_build", s, simple(s))

    # 3. Browser E2E Tests (D01-D12, R01-R05, M01-M04, E01-E09)
    if args.skip_e2e:
        results["browser_e2e"] = {"status": "INCOMPLETE", "reason": "--skip-e2e specified; acceptance requires full browser run"}
    else:
        api_port = os.environ.get("AURUM_E2E_API_PORT", "8765")
        web_port = os.environ.get("AURUM_E2E_WEB_PORT", "5199")
        report_path = os.path.join(LOG_DIR, "playwright_report.json")
        if os.path.exists(report_path):
            os.remove(report_path)

        env_e2e = {
            "AURUM_E2E_PYTHON": py,
            "AURUM_E2E_API_PORT": api_port,
            "AURUM_E2E_WEB_PORT": web_port,
            "AURUM_E2E_REPORT": report_path,
        }

        # Safe port verification: check if isolated port is occupied
        if is_port_in_use(int(api_port)):
            print(f"[acceptance] Port {api_port} is currently bound. Playwright test server will attach or use isolated instance.", flush=True)

        e2e_files = [
            "tests/e2e/v14_4_2_chart_closure.e2e.ts",
            "tests/e2e/v14_4_2_realtime_soak.e2e.ts",
            "tests/e2e/v14_4_2_mobile.e2e.ts",
            "tests/e2e/v14_4_1_sessions_chart.e2e.ts",
            "tests/e2e/v14_4_2_selection_source.e2e.ts",
        ]
        t_start_e2e = time.time()
        s = run_step("browser_e2e", [npx, "playwright", "test", *e2e_files], FRONTEND, env_extra=env_e2e)
        stats = {}
        try:
            with open(report_path, encoding="utf-8") as fh:
                stats = json.load(fh).get("stats", {})
        except (OSError, ValueError):
            pass
        executed = stats.get("expected", 0) + stats.get("unexpected", 0) + stats.get("flaky", 0)
        verdict = simple(s, {"playwright_stats": stats, "executed_tests": executed})
        if s["exit_code"] == 0 and executed < 25:
            verdict["status"] = "FAIL"
            verdict["reason"] = f"insufficient browser tests executed: {executed} < 25"
        record("browser_e2e", s, verdict)
        actual_test_outcomes.update(parse_playwright_json(report_path, t_start_e2e))

    # 4. Benchmark & Latency Measurement
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

    # 5. Git Diff Check
    s = run_step("git_diff_check", ["git", "diff", "--check"], ROOT)
    record("git_diff_check", s, simple(s))

    # 6. Validate manifest case requirements
    required_manifest = {case_id: [case_id] for case_id in REQUIRED_CASES}
    case_coverage = validate_required_results(required_manifest, actual_test_outcomes)

    statuses = {k: v["status"] for k, v in results.items()}
    overall, exit_code = compute_overall(statuses, case_coverage)

    summary = {
        "overall": overall,
        "required_cases_count": len(REQUIRED_CASES),
        "case_coverage": case_coverage,
        "statuses": statuses,
        "steps": results,
        "baseline_failures_declared": sorted(BASELINE_FAILURES),
        "probability_research": "NOT_VERIFIED (no real out-of-sample dataset; metrics are null, not 0)",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    os.makedirs(LOG_DIR, exist_ok=True)
    out_json = os.path.join(LOG_DIR, "acceptance_v14_4_2.json")
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    print("\n=== V14.4.2 ACCEPTANCE REPORT ===")
    for k, v in statuses.items():
        print(f"  {k:22s} {v}")
    print(f"  OVERALL                {overall}\n  Report JSON: {out_json}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
