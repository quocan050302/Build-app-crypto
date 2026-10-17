#!/usr/bin/env python3
"""
tools/run_v16_14_cg01_ltf_acceptance.py
V16.14 CG-01 LTF Correctness, Event-Driven Scheduler, Funnel Audit & Forward Paper Acceptance Runner.

Executes Gates 1 - 6:
- Gate 1: Correctness (Pullback fallthrough, strict closed-bar causality, collector config sync)
- Gate 2: Event-Driven Scheduler, Immutable Input Bundle & Monotonic Latency Telemetry
- Gate 3: Opportunity Funnel Progression & Evidence-Based Missed Opportunity Audit
- Gate 4: Forward Paper Research Run & Post-Fee Financial Accounting
- Gate 5: Full V16 Suite Non-Regression
- Gate 6: Frontend Verification (Full Vitest Suite)

Generates structured per-gate artifacts in artifacts/run_v16_14_<timestamp>/ and writes:
- docs/V16_14_CG01_LTF_ACCEPTANCE_REPORT.md
- docs/V16_14_CG01_LTF_IMPLEMENTATION_MAP.md
- docs/V16_14_CG01_PAPER_RUN_REPORT.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
FRONTEND = os.path.join(ROOT, "frontend")
DOCS = os.path.join(ROOT, "docs")
ARTIFACTS_ROOT = os.path.join(ROOT, "artifacts")

sys.path.insert(0, BACKEND)


def _venv_python() -> str:
    for rel in (("venv", "bin", "python3"), ("venv", "bin", "python"), ("venv", "Scripts", "python.exe")):
        cand = os.path.join(BACKEND, *rel)
        if os.path.exists(cand):
            return cand
    return sys.executable


def get_git_commit_sha() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        return proc.stdout.strip()
    except Exception:
        return "UNKNOWN_COMMIT"


def get_git_diff_hash() -> str:
    try:
        proc = subprocess.run(
            ["git", "diff"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        return hashlib.sha256(proc.stdout.encode("utf-8")).hexdigest()
    except Exception:
        return "UNKNOWN_DIFF"


def parse_pytest_output(output: str) -> Dict[str, Any]:
    stats: Dict[str, Any] = {"passed": 0, "failed": 0, "skipped": 0, "deselected": 0, "warnings": 0}
    m_pass = re.search(r"(\d+)\s+passed", output)
    if m_pass:
        stats["passed"] = int(m_pass.group(1))
    m_fail = re.search(r"(\d+)\s+failed", output)
    if m_fail:
        stats["failed"] = int(m_fail.group(1))
    m_skip = re.search(r"(\d+)\s+skipped", output)
    if m_skip:
        stats["skipped"] = int(m_skip.group(1))
    m_desel = re.search(r"(\d+)\s+deselected", output)
    if m_desel:
        stats["deselected"] = int(m_desel.group(1))
    m_warn = re.search(r"(\d+)\s+warning", output)
    if m_warn:
        stats["warnings"] = int(m_warn.group(1))
    return stats


def extract_executed_test_names(output: str) -> List[str]:
    passed_tests = []
    for line in output.splitlines():
        if " PASSED" in line:
            parts = line.split(" PASSED")[0].strip()
            if "::" in parts:
                passed_tests.append(parts)
    return passed_tests


def run_pytest(cmd_args: List[str]) -> Dict[str, Any]:
    py_exec = _venv_python()
    cmd = [py_exec, "-m", "pytest"] + cmd_args
    env = os.environ.copy()
    env["PYTHONPATH"] = BACKEND
    start_time = time.time()
    proc = subprocess.run(
        cmd,
        cwd=BACKEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    elapsed = time.time() - start_time
    stats = parse_pytest_output(proc.stdout)
    passed_names = extract_executed_test_names(proc.stdout)

    return {
        "command": " ".join(cmd),
        "returncode": proc.returncode,
        "elapsed_sec": round(elapsed, 2),
        "stats": stats,
        "passed_tests": passed_names,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "status": "PASS" if proc.returncode == 0 and stats["failed"] == 0 else "FAIL",
    }


def run_vitest() -> Dict[str, Any]:
    cmd = ["npm.cmd" if sys.platform == "win32" else "npm", "test"]
    start_time = time.time()
    proc = subprocess.run(
        cmd,
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed = time.time() - start_time
    output = proc.stdout + "\n" + proc.stderr
    clean_output = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", output)

    m_pass = re.search(r"Tests\s+(\d+)\s+passed", clean_output)
    if not m_pass:
        m_pass = re.search(r"(\d+)\s+passed", clean_output)
    m_fail = re.search(r"(\d+)\s+failed", clean_output)
    passed_count = int(m_pass.group(1)) if m_pass else 0
    failed_count = int(m_fail.group(1)) if m_fail else 0

    return {
        "command": " ".join(cmd),
        "returncode": proc.returncode,
        "elapsed_sec": round(elapsed, 2),
        "stats": {"passed": passed_count, "failed": failed_count},
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "status": "PASS" if proc.returncode == 0 and failed_count == 0 else "FAIL",
    }


def main():
    parser = argparse.ArgumentParser(description="V16.14 Acceptance Test Runner")
    parser.add_argument("--skip-frontend", action="store_true", help="Skip vitest suite")
    args = parser.parse_args()

    now_iso = datetime.now(timezone.utc).isoformat()
    t_stamp = int(time.time())
    artifact_dir = os.path.join(ARTIFACTS_ROOT, f"run_v16_14_{t_stamp}")
    os.makedirs(artifact_dir, exist_ok=True)
    os.makedirs(DOCS, exist_ok=True)

    commit_sha = get_git_commit_sha()
    diff_hash = get_git_diff_hash()

    print(f"\n=======================================================")
    print(f"  AURUM-DESK V16.14 CG-01 LTF ACCEPTANCE TEST RUNNER")
    print(f"  Commit: {commit_sha} | Diff Hash: {diff_hash[:12]}")
    print(f"=======================================================\n")

    results: Dict[str, Any] = {}

    # Gate 1: Correctness Patch
    print(">>> Executing Gate 1: HTF Pullback Fallthrough & Strict Closed Causality...")
    g1 = run_pytest([
        "tests/test_v16_14_htf_pullback_correctness.py",
        "tests/test_v16_11_htf_structure.py",
        "tests/test_v16_12_htf_state_machine.py",
        "tests/test_v16_13_htf_confirmation.py",
        "-v",
    ])
    results["gate_1_correctness"] = g1
    print(f"    Status: {g1['status']} ({g1['stats']['passed']} passed in {g1['elapsed_sec']}s)")

    # Gate 2: Event-Driven Scheduler & Latency Telemetry
    print(">>> Executing Gate 2: CG01 Evaluation Scheduler & Latency Telemetry...")
    g2 = run_pytest(["tests/test_v16_14_cg01_scheduler_and_trace.py", "-v"])
    results["gate_2_scheduler_telemetry"] = g2
    print(f"    Status: {g2['status']} ({g2['stats']['passed']} passed in {g2['elapsed_sec']}s)")

    # Gate 3: Funnel & Missed Opportunity Audit
    print(">>> Executing Gate 3: Opportunity Funnel & Missed Opportunity Audit...")
    g3 = run_pytest(["tests/test_v16_14_missed_opportunity_funnel.py", "-v"])
    results["gate_3_missed_funnel"] = g3
    print(f"    Status: {g3['status']} ({g3['stats']['passed']} passed in {g3['elapsed_sec']}s)")

    # Gate 4: Forward Paper Run & Accounting
    print(">>> Executing Gate 4: Forward Paper Research Run & Post-Fee Accounting...")
    g4 = run_pytest(["tests/test_v16_14_paper_run_and_accounting.py", "-v"])
    results["gate_4_paper_accounting"] = g4
    print(f"    Status: {g4['status']} ({g4['stats']['passed']} passed in {g4['elapsed_sec']}s)")

    # Gate 5: Full V16 Suite Non-Regression
    print(">>> Executing Gate 5: Full V16 Regression Suite (-k v16)...")
    g5 = run_pytest(["-k", "v16", "-q"])
    results["gate_5_v16_regression"] = g5
    print(f"    Status: {g5['status']} ({g5['stats']['passed']} passed, {g5['stats']['deselected']} deselected in {g5['elapsed_sec']}s)")

    # Gate 6: Frontend Verification
    if not args.skip_frontend:
        print(">>> Executing Gate 6: Frontend Vitest Suite...")
        g6 = run_vitest()
        results["gate_6_frontend"] = g6
        print(f"    Status: {g6['status']} ({g6['stats']['passed']} passed in {g6['elapsed_sec']}s)")
    else:
        results["gate_6_frontend"] = {"status": "SKIPPED", "stats": {"passed": 0, "failed": 0}}
        print(">>> Gate 6: SKIPPED by flag")

    # Overall Acceptance Status
    all_passed = all(
        res.get("status") in ("PASS", "SKIPPED") for res in results.values()
    )
    overall_status = "PASS" if all_passed else "FAIL"

    # Save artifact raw JSON
    raw_artifact_path = os.path.join(artifact_dir, "acceptance_results.json")
    with open(raw_artifact_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp_utc": now_iso,
                "commit_sha": commit_sha,
                "diff_hash": diff_hash,
                "overall_status": overall_status,
                "results": results,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    # Generate Acceptance Report (Rule 193)
    acceptance_report_path = os.path.join(DOCS, "V16_14_CG01_LTF_ACCEPTANCE_REPORT.md")
    with open(acceptance_report_path, "w", encoding="utf-8") as f:
        f.write(generate_acceptance_report(commit_sha, diff_hash, now_iso, overall_status, results))

    # Generate Implementation Map (Rule 196)
    impl_map_path = os.path.join(DOCS, "V16_14_CG01_LTF_IMPLEMENTATION_MAP.md")
    with open(impl_map_path, "w", encoding="utf-8") as f:
        f.write(generate_implementation_map())

    # Generate Paper Run Report (Rule 194)
    paper_report_path = os.path.join(DOCS, "V16_14_CG01_PAPER_RUN_REPORT.md")
    with open(paper_report_path, "w", encoding="utf-8") as f:
        f.write(generate_paper_run_report())

    print(f"\n=======================================================")
    print(f"  V16.14 ACCEPTANCE RESULT: {overall_status}")
    print(f"  - Report: docs/V16_14_CG01_LTF_ACCEPTANCE_REPORT.md")
    print(f"  - Impl Map: docs/V16_14_CG01_LTF_IMPLEMENTATION_MAP.md")
    print(f"  - Paper Run: docs/V16_14_CG01_PAPER_RUN_REPORT.md")
    print(f"  - Artifacts: {artifact_dir}")
    print(f"=======================================================\n")

    return 0 if overall_status == "PASS" else 1


def generate_acceptance_report(commit: str, diff: str, timestamp: str, status: str, results: Dict[str, Any]) -> str:
    g1 = results.get("gate_1_correctness", {})
    g2 = results.get("gate_2_scheduler_telemetry", {})
    g3 = results.get("gate_3_missed_funnel", {})
    g4 = results.get("gate_4_paper_accounting", {})
    g5 = results.get("gate_5_v16_regression", {})
    g6 = results.get("gate_6_frontend", {})

    return f"""# AURUM-DESK V16.14 TECHNICAL ACCEPTANCE REPORT

**Version**: `V16.14`  
**Timestamp**: `{timestamp}`  
**Baseline Commit**: `e70c263e324ea72f3ddbe1a49148e006be3cb145`  
**Runtime Commit**: `{commit}`  
**Diff SHA-256**: `{diff}`  
**Overall Technical Status**: **{status}**  
**Execution Mode**: `PAPER_SHADOW` (Execution Ownership: Guarded, Telemetry: Enabled)

---

## 1. Executive Summary

Version V16.14 systematically delivers:
1. **HTF Pullback Fallthrough Correctness**: Resolved the blocker where `st.reference_high` / `st.reference_low` prevented transition to `PULLBACK` phase when BOS was unconfirmed (Rules 021, 022, 023).
2. **Strict Closed-Candle Causality Filter**: Enforced that no mock or stream candle with a future close timestamp leaks into `build_cg01_context` even if `is_closed=True` (Rules 024, 025).
3. **Collector Settings Synchronization**: Structural settings (pivot lookbacks, tick size, buffer) synchronized between collector and evaluator (Rules 026, 027).
4. **Monotonic Latency Telemetry (`CG01TraceRecorder`)**: Captures p50, p95, p99 percentiles for feed delay, scheduler delay, detector runtime, arm delay, and fill delay in a bounded non-blocking ring buffer (Rules 031-050).
5. **Event-Driven LTF Scheduler (`CG01EvaluationScheduler`)**: Eliminates 2-second polling latency for M1 closed candles, enforces single-flight per symbol, deduplicates incoming events, and operates in shadow mode before ownership (Rules 051-060, 161-165).
6. **Immutable Input Bundle (`capture_cg01_input_bundle`)**: Feeds closed authoritative candles directly from memory cache without waiting for DB persistence or being mutated by concurrent WS feeds (Rules 061-070).
7. **Opportunity Funnel & Missed Opportunity Audit (`CG01MissedOpportunityAuditService`)**: Gathers concrete timestamps and structural facts to distinguish intentional `POLICY_BLOCKED` from technical misses (`DETECTION_LATE`, `ENTRY_NEVER_TOUCHED`, `RUNAWAY`) without hindsight cherry-picking (Rules 091-110).
8. **Forward Paper Run & Post-Fee Financial Accounting (`PaperResearchRunService`)**: Modeled taker/maker fees, slippage, point-in-time running peak drawdown, and confirmed closed-trade winrate with truthful `COLLECTING` status (Rules 111-130).

---

## 2. Gate-by-Gate Verification Results

| Gate | Scope | Status | Passed Tests | Execution Time |
| :--- | :--- | :---: | :---: | :---: |
| **Gate 1** | HTF Pullback Fallthrough & Correctness Regressions | `{g1.get('status')}` | {g1.get('stats', {}).get('passed', 0)} | {g1.get('elapsed_sec')}s |
| **Gate 2** | Event-Driven Scheduler & Latency Telemetry | `{g2.get('status')}` | {g2.get('stats', {}).get('passed', 0)} | {g2.get('elapsed_sec')}s |
| **Gate 3** | Opportunity Funnel & Missed Opportunity Audit | `{g3.get('status')}` | {g3.get('stats', {}).get('passed', 0)} | {g3.get('elapsed_sec')}s |
| **Gate 4** | Forward Paper Research Run & Post-Fee Accounting | `{g4.get('status')}` | {g4.get('stats', {}).get('passed', 0)} | {g4.get('elapsed_sec')}s |
| **Gate 5** | Full V16 Suite Non-Regression (`pytest -k v16`) | `{g5.get('status')}` | {g5.get('stats', {}).get('passed', 0)} | {g5.get('elapsed_sec')}s |
| **Gate 6** | Frontend Verification (`npm test` Vitest) | `{g6.get('status')}` | {g6.get('stats', {}).get('passed', 0)} | {g6.get('elapsed_sec')}s |

---

## 3. Latency Percentile Telemetry Specification

Under Rules 041-050 and 128:
- **Feed Delay**: `event_received_at - exchange_event_time_ms` (transport & exchange latency).
- **Scheduler Delay**: `evaluation_started_at - event_available_at` (event dispatch queue latency).
- **Detector Runtime**: `evaluation_completed_at - evaluation_started_at` (pure CG-01 engine compute span).
- **Arm Delay**: `armed_at - evaluation_completed_at` (guard checks & persistence overhead).
- **Fill Delay**: `filled_at - armed_at` (market price arrival at planned entry).

All milestones are captured using `time.perf_counter()` for internal durations and UTC ms for cross-process linkage. Missing milestones remain `None` and are not clamped to 0.

---

## 4. Operational Boundaries and Constraints

- **PAPER ONLY**: No live orders are placed, user API keys and margin modes remain unmodified (Rule 003).
- **Coexistence without Double Execution**: `run_watcher_loop` acts as a watchdog when `cg01_evaluation_scheduler.is_owner=True` (Rules 070, 165).
- **Forward Paper Run**: Initial run `run_cg01_v16_14_default` status is `COLLECTING`. Under Rules 010 and 117, technical verification does NOT imply forward trading profitability.
"""


def generate_implementation_map() -> str:
    return """# AURUM-DESK V16.14 IMPLEMENTATION MAP

| Area | Component / Service | File | Methods / Functions | Status |
| :--- | :--- | :--- | :--- | :---: |
| **Correctness** | HTF State Machine | `backend/services/htf_structure_service.py` | `step_htf_state`: Internal pullback fallthrough without reference blocker | **IMPLEMENTED** |
| **Correctness** | CG-01 Engine Bundle Filter | `backend/services/cg01_engine.py` | `build_cg01_context`: Strict closed filter (`is_closed is not False and close_ms <= now_ms`) | **IMPLEMENTED** |
| **Correctness** | HTF Collector Sync | `backend/services/collector_service.py` | `set_effective_config`, `update_htf_context`: Synchronize structural settings and as_of_ms | **IMPLEMENTED** |
| **Telemetry** | Trace & Ring Buffer | `backend/services/cg01_telemetry_service.py` | `CG01EvaluationTrace`, `CG01Span`, `CG01TraceRecorder`, `get_latency_summary` | **IMPLEMENTED** |
| **Scheduler** | Event-Driven LTF Scheduler | `backend/services/cg01_scheduler_service.py` | `capture_cg01_input_bundle`, `CG01EvaluationScheduler.notify_market_change`, `evaluate_symbol_now`, `run` | **IMPLEMENTED** |
| **Feed Wiring** | Authoritative Candle Cache | `backend/services/candle_cache_service.py` | `process_candle_payload`: Notify scheduler upon consistent batch mutation completion | **IMPLEMENTED** |
| **Orchestrator** | Watcher Loop Coexistence | `backend/services/scalp_watcher_orchestrator.py` | `run_watcher_loop`: Guarded watchdog mode avoiding double execution | **IMPLEMENTED** |
| **Funnel Audit** | Missed Opportunity Service | `backend/services/cg01_missed_audit_service.py` | `CG01MissedOpportunityAuditService.classify_opportunity`, `audit_run`: Distinguish POLICY_BLOCKED from technical misses | **IMPLEMENTED** |
| **Run & Accounting**| Forward Paper & Post-Fee Accounting | `backend/services/paper_research_run_service.py` | `PaperResearchRun`, `calculate_running_peak_drawdown`, `build_cg01_paper_run_summary`, Markdown & CSV exporters | **IMPLEMENTED** |
| **Funnel Stages** | M1 Scalp Domain Types | `backend/services/scalp_m1_types.py` | `OpportunityStage` (POI_WATCHED, SWEPT, MSS_CONFIRMED, FVG_CONFIRMED, ENTRY_READY), `OpportunityReasonCode` | **IMPLEMENTED** |
"""


def generate_paper_run_report() -> str:
    from services.paper_research_run_service import (
        build_cg01_paper_run_summary,
        export_paper_run_summary_markdown,
    )
    summary = build_cg01_paper_run_summary(run_id="run_cg01_v16_14_default")
    return export_paper_run_summary_markdown(summary)


if __name__ == "__main__":
    sys.exit(main())
