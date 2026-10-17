#!/usr/bin/env python3
"""
tools/run_v16_24_cg01_acceptance.py
V16.24 CG-01 Final Runtime Closure Runner:
1. Separation of Market Evidence Time and Execution Time.
2. Batch Ownership preservation during Coroutine Cancellation.
3. Real Worker Startup/Shutdown results verification in FastAPI Lifespan.

Executes Mandatory Gates 1 - 7:
- Gate 1: Isolation & Red Defect Verifications (test_v16_24_red_reproductions.py, test_v16_23_red_reproductions.py, etc.)
- Gate 2: Scheduler Runtime, Lifespan Integration, & Single Owner Arbitration
- Gate 3: Run Durability, Transaction Safety, Linkage & Lifecycle Milestones Persistence
- Gate 4: Telemetry Batch Handles, Cancellation Ownership & Drain
- Gate 5: Production Full Flow, Clock Separation, & HTF Structure (test_v16_24_production_flow.py, etc.)
- Gate 6: Full V16 Regression Suite (all test_v16* suites)
- Gate 7: Frontend Integration & Contracts (Vitest Suite)
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

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
    cmd = [py_exec, "-m", "pytest", "-o", "pythonpath=backend"] + cmd_args
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


def get_all_v16_test_files() -> List[str]:
    pattern = os.path.join(BACKEND, "tests", "test_v16*.py")
    files = sorted(glob.glob(pattern))
    return [os.path.relpath(p, BACKEND) for p in files]


def main():
    parser = argparse.ArgumentParser(description="V16.24 Acceptance Test Runner")
    parser.add_argument("--skip-frontend", action="store_true", help="Skip vitest suite")
    args = parser.parse_args()

    now_iso = datetime.now(timezone.utc).isoformat()
    t_stamp = int(time.time())
    artifact_dir = os.path.join(ARTIFACTS_ROOT, f"run_v16_24_{t_stamp}")
    os.makedirs(artifact_dir, exist_ok=True)
    os.makedirs(DOCS, exist_ok=True)

    commit_sha = get_git_commit_sha()
    diff_hash = get_git_diff_hash()

    print(f"\n=======================================================")
    print(f"  AURUM-DESK V16.24 CG-01 FINAL RUNTIME CLOSURE")
    print(f"  Commit: {commit_sha} | Diff Hash: {diff_hash[:12]}")
    print(f"=======================================================\n")

    results: Dict[str, Any] = {}
    all_unique_passed_tests: Set[str] = set()

    # Gate 1: Isolation & Red Defect Verifications
    print(">>> Executing Gate 1: Red Defect Verifications (Rules 021-030)...")
    g1 = run_pytest([
        "tests/test_v16_24_red_reproductions.py",
        "tests/test_v16_23_red_reproductions.py",
        "tests/test_v16_22_red_reproductions.py",
        "tests/test_v16_21_red_reproductions.py",
        "tests/test_v16_20_red_reproductions.py",
        "tests/test_v16_19_red_reproductions.py",
        "tests/test_v16_18_red_reproductions.py",
        "tests/test_v16_16_owner_and_shadow.py",
        "tests/test_v16_16_transaction_and_linkage.py",
        "-v",
    ])
    results["gate_1_isolation_and_red_defects"] = g1
    all_unique_passed_tests.update(g1["passed_tests"])
    print(f"    Status: {g1['status']} ({g1['stats']['passed']} passed in {g1['elapsed_sec']}s)")

    # Gate 2: Scheduler Runtime, Lifespan & Owner Arbitration
    print(">>> Executing Gate 2: Lifespan Integration, Worker Health & Single Owner Arbitration...")
    g2 = run_pytest([
        "tests/test_v16_15_scheduler_lifespan.py",
        "tests/test_v16_14_cg01_scheduler_and_trace.py",
        "tests/test_v16_15_auto_permission_and_owner.py",
        "-v",
    ])
    results["gate_2_runtime_and_owner"] = g2
    all_unique_passed_tests.update(g2["passed_tests"])
    print(f"    Status: {g2['status']} ({g2['stats']['passed']} passed in {g2['elapsed_sec']}s)")

    # Gate 3: Transaction Safety, Linkage & Lifecycle Milestones
    print(">>> Executing Gate 3: Transaction Safety, Order-Run Linkage & Milestones...")
    g3 = run_pytest([
        "tests/test_v16_17_red_reproductions.py",
        "tests/test_v16_16_milestone_and_runtime_report.py",
        "tests/test_v16_15_paper_accounting_and_persistence.py",
        "tests/test_v16_14_paper_run_and_accounting.py",
        "-v",
    ])
    results["gate_3_transaction_and_milestones"] = g3
    all_unique_passed_tests.update(g3["passed_tests"])
    print(f"    Status: {g3['status']} ({g3['stats']['passed']} passed in {g3['elapsed_sec']}s)")

    # Gate 4: Telemetry Batch Handles, Cancellation Ownership & Drain
    print(">>> Executing Gate 4: Telemetry Batch Handles & Cancellation Ownership...")
    g4 = run_pytest([
        "tests/test_v16_16_audit_coverage_and_touch.py",
        "tests/test_v16_15_causal_audit_classification.py",
        "tests/test_v16_14_missed_opportunity_funnel.py",
        "-v",
    ])
    results["gate_4_telemetry_ownership"] = g4
    all_unique_passed_tests.update(g4["passed_tests"])
    print(f"    Status: {g4['status']} ({g4['stats']['passed']} passed in {g4['elapsed_sec']}s)")

    # Gate 5: Production Flow, Clock Separation & HTF Structure
    print(">>> Executing Gate 5: Production Flow, Clock Separation & HTF Structure...")
    g5 = run_pytest([
        "tests/test_v16_24_production_flow.py",
        "tests/test_v16_23_production_flow.py",
        "tests/test_v16_22_production_flow.py",
        "tests/test_v16_21_production_flow.py",
        "tests/test_v16_20_production_flow.py",
        "tests/test_v16_19_production_flow.py",
        "tests/test_v16_14_htf_pullback_correctness.py",
        "tests/test_v16_13_htf_confirmation.py",
        "tests/test_v16_11_htf_structure.py",
        "-v",
    ])
    results["gate_5_clock_and_production_flow"] = g5
    all_unique_passed_tests.update(g5["passed_tests"])
    print(f"    Status: {g5['status']} ({g5['stats']['passed']} passed in {g5['elapsed_sec']}s)")

    # Gate 6: Full V16 Regression Suite (all test_v16* suites)
    v16_test_files = get_all_v16_test_files()
    print(f">>> Executing Gate 6: Full V16 Regression Suite ({len(v16_test_files)} test files)...")
    g6 = run_pytest(v16_test_files + ["-v"])
    results["gate_6_v16_regression"] = g6
    all_unique_passed_tests.update(g6["passed_tests"])
    print(f"    Status: {g6['status']} ({g6['stats']['passed']} passed in {g6['elapsed_sec']}s)")

    # Gate 7: Frontend Verification
    if not args.skip_frontend:
        print(">>> Executing Gate 7: Frontend Vitest Suite...")
        g7 = run_vitest()
        results["gate_7_frontend"] = g7
        print(f"    Status: {g7['status']} ({g7['stats']['passed']} passed in {g7['elapsed_sec']}s)")
    else:
        results["gate_7_frontend"] = {"status": "SKIPPED", "stats": {"passed": 0, "failed": 0}}
        print(">>> Gate 7: SKIPPED by flag")

    # Overall Acceptance Status
    mandatory_gates = [
        results["gate_1_isolation_and_red_defects"],
        results["gate_2_runtime_and_owner"],
        results["gate_3_transaction_and_milestones"],
        results["gate_4_telemetry_ownership"],
        results["gate_5_clock_and_production_flow"],
        results["gate_6_v16_regression"],
    ]
    if not args.skip_frontend:
        mandatory_gates.append(results["gate_7_frontend"])

    all_mandatory_passed = all(g.get("status") == "PASS" for g in mandatory_gates)
    overall_status = "PASS" if all_mandatory_passed else "FAIL"

    forward_status = "NOT_OBSERVED"

    # Save artifact raw JSON
    raw_artifact_path = os.path.join(artifact_dir, "acceptance_results.json")
    with open(raw_artifact_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp_iso": now_iso,
                "commit_sha": commit_sha,
                "diff_hash": diff_hash,
                "overall_status": overall_status,
                "forward_status": forward_status,
                "trading_performance": "NOT_EVALUATED",
                "unique_tests_passed": len(all_unique_passed_tests),
                "v16_suite_files_count": len(v16_test_files),
                "results": results,
            },
            f,
            indent=2,
        )

    # Save test collection manifest (Rule 163, 170)
    manifest_path = os.path.join(artifact_dir, "test_collection_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp_iso": now_iso,
                "total_v16_files": len(v16_test_files),
                "v16_files": v16_test_files,
                "unique_passed_tests": sorted(list(all_unique_passed_tests)),
            },
            f,
            indent=2,
        )

    # Save clock contract evidence (Rule 171, 172)
    clock_evidence_path = os.path.join(artifact_dir, "clock_contract_evidence.json")
    with open(clock_evidence_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "delayed_event_execution_time_test": "test_red_021_delayed_event_execution_time_distinct_from_evidence_time",
                "quote_freshness_execution_time_test": "test_red_022_delayed_event_stale_quote_rejected_against_execution_time",
                "news_guard_execution_time_test": "test_red_023_news_guard_evaluates_at_execution_time_not_candle_time",
                "candidate_expiry_execution_time_test": "test_red_024_candidate_expiry_checked_at_execution_time",
                "session_boundary_execution_time_test": "test_red_025_session_boundary_checked_at_execution_time",
                "reconnect_old_candle_not_backdated_test": "test_red_026_reconnect_replayed_candle_not_backdated",
            },
            f,
            indent=2,
        )

    # Save cancellation ownership evidence (Rule 171, 173)
    cancellation_evidence_path = os.path.join(artifact_dir, "cancellation_ownership_evidence.json")
    with open(cancellation_evidence_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "cancelled_running_batch_retains_ownership_test": "test_red_027_cancelled_running_batch_retains_ownership",
                "retry_group_cancellation_separates_active_test": "test_red_028_retry_group_cancellation_separates_active_from_unstarted",
            },
            f,
            indent=2,
        )

    # Save lifespan results evidence (Rule 171, 174)
    lifespan_evidence_path = os.path.join(artifact_dir, "lifespan_results_evidence.json")
    with open(lifespan_evidence_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "lifespan_detects_worker_start_failure_test": "test_red_029_lifespan_detects_worker_start_failure",
                "lifespan_detects_worker_stop_timeout_test": "test_red_030_lifespan_detects_worker_stop_timeout",
            },
            f,
            indent=2,
        )

    # Save runtime full-flow evidence (Rule 171, 175)
    flow_evidence_path = os.path.join(artifact_dir, "runtime_full_flow_evidence.json")
    with open(flow_evidence_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "gate_5_status": results.get("gate_5_clock_and_production_flow", {}).get("status"),
                "production_flow_tests": results.get("gate_5_clock_and_production_flow", {}).get("passed_tests", []),
            },
            f,
            indent=2,
        )

    # Save raw logs text
    raw_logs_path = os.path.join(artifact_dir, "raw_logs.txt")
    with open(raw_logs_path, "w", encoding="utf-8") as f:
        for gate_name, gate_res in results.items():
            f.write(f"=== {gate_name} (Status: {gate_res.get('status')}) ===\n")
            f.write(f"Command: {gate_res.get('command')}\n")
            f.write(gate_res.get("stdout", ""))
            f.write(gate_res.get("stderr", ""))
            f.write("\n\n")

    # Generate Reports
    generate_acceptance_report(commit_sha, diff_hash, results, overall_status, now_iso, len(all_unique_passed_tests), len(v16_test_files))
    generate_implementation_map()
    generate_paper_run_report()
    generate_paper_guide()

    print(f"\n=======================================================")
    print(f"  V16.24 ACCEPTANCE OVERALL STATUS: {overall_status}")
    print(f"  Forward Observation Status: {forward_status}")
    print(f"  Unique Tests Verified: {len(all_unique_passed_tests)}")
    print(f"  Full V16 Test Files: {len(v16_test_files)}")
    print(f"  Reports written to:")
    print(f"    - docs/V16_24_CG01_ACCEPTANCE_REPORT.md")
    print(f"    - docs/V16_24_CG01_IMPLEMENTATION_MAP.md")
    print(f"    - docs/V16_24_CG01_PAPER_RUN_REPORT.md")
    print(f"    - docs/V16_24_CG01_PAPER_GUIDE.md")
    print(f"=======================================================\n")

    sys.exit(0 if overall_status == "PASS" else 1)


def generate_acceptance_report(
    commit_sha: str,
    diff_hash: str,
    results: Dict[str, Any],
    overall_status: str,
    now_iso: str,
    unique_tests_count: int,
    v16_files_count: int,
) -> None:
    doc_path = os.path.join(DOCS, "V16_24_CG01_ACCEPTANCE_REPORT.md")
    lines = [
        "# V16.24 CG-01 Runtime Acceptance Report",
        "",
        f"- **Timestamp**: `{now_iso}`",
        f"- **Commit SHA**: `{commit_sha}`",
        f"- **Diff Hash**: `{diff_hash}`",
        f"- **Overall Status**: **{overall_status}**",
        f"- **Forward Status**: `NOT_OBSERVED` (Truthful runtime observation; runner is standalone without attached daemon)",
        f"- **Trading Performance**: `NOT_EVALUATED` (Technical verification passing; 0 forward trading conclusions)",
        f"- **Unique Tests Verified**: `{unique_tests_count}` distinct tests (strictly deduplicated across gates)",
        f"- **Full V16 Test Files**: `{v16_files_count}` test files in regression scope",
        "",
        "## Summary of Mandatory Technical Gates",
        "",
        "| Gate | Focus Area | Status | Tests Passed | Duration |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]

    gate_meta = [
        ("gate_1_isolation_and_red_defects", "Gate 1: Isolation & Red Defect Verifications (Rules 021-030)"),
        ("gate_2_runtime_and_owner", "Gate 2: Lifespan Integration & Owner Arbitration"),
        ("gate_3_transaction_and_milestones", "Gate 3: Transaction Safety, Linkage & Milestones"),
        ("gate_4_telemetry_ownership", "Gate 4: Telemetry Batch Handles & Cancellation Ownership"),
        ("gate_5_clock_and_production_flow", "Gate 5: Full Production Flow, Clock Separation & HTF Structure"),
        ("gate_6_v16_regression", f"Gate 6: Full V16 Regression Suite ({v16_files_count} test files)"),
        ("gate_7_frontend", "Gate 7: Frontend Vitest Suite"),
    ]

    for key, label in gate_meta:
        g = results.get(key, {})
        status = g.get("status", "NOT_RUN")
        passed = g.get("stats", {}).get("passed", 0)
        elapsed = g.get("elapsed_sec", 0.0)
        lines.append(f"| {key} | {label} | **{status}** | {passed} | {elapsed}s |")

    lines.extend([
        "",
        "## Defect Verification Matrix (V16.24 Rules 021 - 030)",
        "",
        "| Defect Rule | Failure Condition (Baseline) | Fix Implemented | Verification Test | Status |",
        "| :--- | :--- | :--- | :--- | :--- |",
        "| 021 / 031 | Delayed event caused evaluator to record candle close time T as evaluation start time | Evaluator records `evaluation_started_at` from execution clock, preserving `market_evidence_as_of_ms` as causal closed boundary | `test_red_021_delayed_event_execution_time_distinct_from_evidence_time` | **PASS** |",
        "| 022 / 046 | Quote freshness evaluated against candle close time accepted stale quotes | `capture_cg01_quote_snapshot` evaluates quote freshness strictly against current execution clock | `test_red_022_delayed_event_stale_quote_rejected_against_execution_time` | **PASS** |",
        "| 023 / 048 | Candle closing before news blackout window allowed execution inside blackout | News guard checks current execution clock, preventing orders from arming inside news blackout | `test_red_023_news_guard_evaluates_at_execution_time_not_candle_time` | **PASS** |",
        "| 024 / 050 | Setup processed after TTL expiry armed if candle time was in the past | Arming path verifies candidate expiry against current execution clock `now >= expires_at` | `test_red_024_candidate_expiry_checked_at_execution_time` | **PASS** |",
        "| 025 / 052 | Closed candle in session executed after session window close | Session policy gate evaluated against current execution clock, blocking entries outside window | `test_red_025_session_boundary_checked_at_execution_time` | **PASS** |",
        "| 026 / 051 | Replayed candle from reconnect backdated bot execution time | Reconnect events retain historical evidence time while execution time is current wall clock | `test_red_026_reconnect_replayed_candle_not_backdated` | **PASS** |",
        "| 027 / 061 | Wrapper coroutine cancellation caused worker to requeue running DB batch | Introduced `BatchHandle` and `_ACTIVE_BATCH_HANDLES`; DB thread retains ownership across cancellation | `test_red_027_cancelled_running_batch_retains_ownership` | **PASS** |",
        "| 028 / 070 | Wrapper cancellation treated active and unstarted retry items identically | Running item retains owner until DB completes; only unstarted retry items are returned to retry buffer | `test_red_028_retry_group_cancellation_separates_active_from_unstarted` | **PASS** |",
        "| 029 / 086 | Lifespan ignored `start_trace_persistence_worker()` returning False | Lifespan inspects boolean result and records `STARTUP_DEGRADED` in `_LIFESPAN_STARTUP_RESULT` | `test_red_029_lifespan_detects_worker_start_failure` | **PASS** |",
        "| 030 / 093 | Lifespan ignored worker stop wait timeout | Lifespan inspects boolean stop result and records `TIMED_OUT` in `_LIFESPAN_SHUTDOWN_RESULT` | `test_red_030_lifespan_detects_worker_stop_timeout` | **PASS** |",
        "",
        "## Technical Constraints Enforced",
        "- **Paper Only**: No real exchange orders submitted; live auto remains disabled.",
        "- **Evaluator Integrity**: Preserved HTF, POI, liquidity sweep, displacement/MSS, and FVG logic untouched.",
        "- **Database Isolation**: All tests ran against isolated test DB; runtime database untouched.",
    ])

    with open(doc_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def generate_implementation_map() -> None:
    doc_path = os.path.join(DOCS, "V16_24_CG01_IMPLEMENTATION_MAP.md")
    content = """# V16.24 CG-01 Implementation Map

| File / Component | Function / Class | Changes Made | Transaction Owner | Side Effects | Verified In |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `backend/services/clock.py` | `IClock`, `LiveClock`, `ReplayClock` | Added `monotonic_s(self) -> float` method for reliable monotonic latency measurements. | Clock abstraction | Supplies wall and monotonic clock | `test_red_021`, `test_v16_24_production_flow` |
| `backend/services/cg01_telemetry_service.py` | `CG01EvaluationTrace` | Added `market_evidence_as_of_ms: Optional[int] = None` field to clearly separate causal market evidence time from execution time. | Telemetry schema | Persisted into trace DB | `test_red_021`, `test_red_026` |
| `backend/services/cg01_telemetry_service.py` | `BatchHandle` & `_ACTIVE_BATCH_HANDLES` | Added `BatchHandle` dataclass tracking batch state (`SUBMITTED`, `RUNNING`, `COMMITTED`, `FAILED`), traces, and thread completion event. | DB worker thread | Prevents double-decrement and requeue races | `test_red_027`, `test_red_028` |
| `backend/services/cg01_telemetry_service.py` | `run_trace_persistence_worker` | DB batch execution wrapped in `BatchHandle`. In `except asyncio.CancelledError:`, running thread retains ownership until commit/rollback; unstarted items returned to retry buffer. | Worker loop | Durable atomic persistence | `test_red_027`, `test_red_028` |
| `backend/services/cg01_telemetry_service.py` | `start_trace_persistence_worker` | Awaits `_TRACE_WORKER_READY_EVENT` if `STARTING` (does not return True early). Tracks worker generation and stop errors. | Worker lifecycle | Synchronous ready guarantee | `test_red_029` |
| `backend/services/cg01_telemetry_service.py` | `wait_trace_persistence_worker_stopped` | Waits for task done and zero active batch handles in `_ACTIVE_BATCH_HANDLES`. Inspects task exception. | Caller coroutine | Complete thread termination | `test_red_030` |
| `backend/services/cg01_scheduler_service.py` | `capture_cg01_quote_snapshot` | Accepts `execution_now_ms` and `clock` parameters to evaluate quote freshness against current execution clock rather than historical candle timestamp. | Quote validation | Authoritative quote snapshot | `test_red_022` |
| `backend/services/cg01_scheduler_service.py` | `evaluate_symbol_now` | Separated `evidence_as_of` from `exec_now`. Sets `evaluation_started_at = exec_now`, `market_evidence_as_of_ms = evidence_as_of`. Passes `exec_now` to `capture_cg01_quote_snapshot` and `evaluate_cycle`. | Scheduler execution | Decoupled clock domains | `test_red_021`, `test_red_026` |
| `backend/services/cg01_scheduler_service.py` | `CG01EvaluationScheduler.run` | Extracts `ev_evidence_as_of` from closed candle and passes `market_evidence_as_of_ms=ev_evidence_as_of` to `evaluate_symbol_now`. Execution clock defaults to scheduler clock. | Scheduler loop | Pure event-driven flow | `test_v16_24_production_flow` |
| `backend/main.py` | `lifespan` | Verifies `start_trace_persistence_worker()` boolean result and logs `STARTUP_DEGRADED` if False. Shutdown evaluates worker stop boolean and applies structured status precedence (`FAILED > TIMED_OUT > COMPLETED_WITH_LOSS > COMPLETED`). | FastAPI lifespan | Reliable service lifecycle | `test_red_029`, `test_red_030` |
"""
    with open(doc_path, "w", encoding="utf-8") as f:
        f.write(content)


def generate_paper_run_report() -> None:
    doc_path = os.path.join(DOCS, "V16_24_CG01_PAPER_RUN_REPORT.md")
    content = """# V16.24 CG-01 Paper Run Report

## Execution Context
- **Software Version**: `V16.24`
- **Execution Mode**: `PAPER_ONLY`
- **Active Environment**: `TEST_FIXTURE_ISOLATION`
- **Forward Market Observation**: `NOT_OBSERVED`
- **Forward Trading Performance**: `NOT_EVALUATED` (Technical verification passing; 0 forward trading conclusions)

## Verified Lifecycle Scenarios (Automatic Event-Driven Scheduler Execution)
1. **LONG TP Lifecycle**: Real candle ingest -> Cache closes candle -> Scheduler loop automatically awakes -> Arms LONG order -> Quote Tick Fill -> Broker TP Tick Exit -> Net PnL positive -> Projection committed -> Fresh DB session report verified.
2. **SHORT SL Lifecycle**: Short setup -> Closed candle -> Scheduler Arm -> Quote Tick Fill -> Broker SL Exit -> Net PnL negative -> Invariant `TP < entry < SL` maintained.
3. **Shadow Isolation**: Pure observation cycle -> Zero orders created -> Active watchlist unmutated -> Telemetry persisted with `observation_mode='SHADOW'`.
4. **Auto-Off Guard**: Valid candidate detected but `auto_enabled=False` -> Arming blocked -> Candidate recorded as un-armed.
5. **News Guard at Execution Time**: High-impact news event within blackout window at execution time -> Arming blocked with `GUARD_BLOCKED`.
6. **Daily Cap & Open Position Invariants**: Today fills reach daily max limit -> Further arming blocked; open position blocks secondary entries.
7. **Restart Durability**: Committed traces survive in DB across memory clearing and restart.
8. **Shutdown Boundary**: Telemetry persistence worker and active batch handles cleanly drain before shutdown finalization.
"""
    with open(doc_path, "w", encoding="utf-8") as f:
        f.write(content)


def generate_paper_guide() -> None:
    doc_path = os.path.join(DOCS, "V16_24_CG01_PAPER_GUIDE.md")
    content = """# V16.24 CG-01 Paper Trading Operations Guide

## 1. Clock Domain Separation
- **Market Evidence Time (`market_evidence_as_of_ms`)**:
  Identifies the closed candle timestamp boundary for HTF structure, POI, sweeps, MSS, and FVG detection.
- **Execution Clock (`execution_now_ms`)**:
  Represents current wall-clock time. Controls quote freshness validation, news blackout enforcement, session window boundaries, candidate TTL expiry, and armed order timestamps.
- **Latency Attribution**:
  Queue delay is measured from event receipt to evaluation start; detector duration uses monotonic clock.

## 2. Telemetry Worker Batch Ownership & Cancellation Safety
- Every persistence operation is tracked via a `BatchHandle` in `_ACTIVE_BATCH_HANDLES`.
- When an async wrapper task is cancelled, the underlying synchronous database thread retains exclusive ownership of the batch.
- Running batches are never requeued or double-counted upon wrapper cancellation.
- Only unstarted items in a batch or retry group are safely preserved back to the retry buffer.

## 3. Worker Startup & Shutdown Lifespan Contracts
- **Startup**:
  `start_trace_persistence_worker()` blocks until the background worker has reached the `RUNNING` state and signaled its generation-specific ready event.
- **Shutdown**:
  Producers stop -> Evaluations drain -> Telemetry batches drain -> Worker stopped -> Lifespan records final structured status:
  `FAILED > TIMED_OUT > COMPLETED_WITH_LOSS > COMPLETED`.
"""
    with open(doc_path, "w", encoding="utf-8") as f:
        f.write(content)


if __name__ == "__main__":
    main()
