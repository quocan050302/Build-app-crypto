#!/usr/bin/env python3
"""
tools/run_v16_18_cg01_runtime_acceptance.py
V16.18 CG-01 Paper Acceptance Test Runner:
Transaction Isolation, Shadow Isolation, Causal Recovery, Durable Telemetry and Production Acceptance.

Executes Gates 1 - 7:
- Gate 1: Isolation & Red Defect Verifications (test_v16_18_red_reproductions.py)
- Gate 2: Scheduler Runtime, Lifespan Integration, & Single Owner Arbitration
- Gate 3: Transaction Safety, Run Linkage, & Lifecycle Milestones Persistence
- Gate 4: Causal Missed Opportunity Audit, Quote Coverage, & Touch Discrimination
- Gate 5: HTF Structure, Pullback Correctness, & End-to-End Production Flow
- Gate 6: Full V16 Suite Non-Regression (-k v16)
- Gate 7: Frontend Integration & Contracts (Vitest Suite)

Enforces:
- Rule 185: Tools/run_v16_18_cg01_runtime_acceptance.py runs real commands, collects real counts and durations.
- Rule 186: Mandatory gates must all PASS.
- Rule 188: Never count duplicate tests as unique.
- Rule 191: Generates artifacts/run_v16_18_<timestamp>/acceptance_results.json.
- Rule 192: Generates docs/V16_18_CG01_IMPLEMENTATION_MAP.md.
- Rule 193: Generates docs/V16_18_CG01_ACCEPTANCE_REPORT.md.
- Rule 194: Generates docs/V16_18_CG01_PAPER_RUN_REPORT.md.
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


def main():
    parser = argparse.ArgumentParser(description="V16.18 Acceptance Test Runner")
    parser.add_argument("--skip-frontend", action="store_true", help="Skip vitest suite")
    args = parser.parse_args()

    now_iso = datetime.now(timezone.utc).isoformat()
    t_stamp = int(time.time())
    artifact_dir = os.path.join(ARTIFACTS_ROOT, f"run_v16_18_{t_stamp}")
    os.makedirs(artifact_dir, exist_ok=True)
    os.makedirs(DOCS, exist_ok=True)

    commit_sha = get_git_commit_sha()
    diff_hash = get_git_diff_hash()

    print(f"\n=======================================================")
    print(f"  AURUM-DESK V16.18 CG-01 RUNTIME ACCEPTANCE TEST RUNNER")
    print(f"  Commit: {commit_sha} | Diff Hash: {diff_hash[:12]}")
    print(f"=======================================================\n")

    results: Dict[str, Any] = {}

    # Gate 1: Isolation & Red Defect Verifications (V16.18 Red Suite)
    print(">>> Executing Gate 1: Isolation & Red Defect Verifications...")
    g1 = run_pytest([
        "tests/test_v16_18_red_reproductions.py",
        "tests/test_v16_16_owner_and_shadow.py",
        "tests/test_v16_16_transaction_and_linkage.py",
        "-v",
    ])
    results["gate_1_isolation_and_red_defects"] = g1
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
    print(f"    Status: {g3['status']} ({g3['stats']['passed']} passed in {g3['elapsed_sec']}s)")

    # Gate 4: Causal Missed Opportunity Audit & Quote Coverage
    print(">>> Executing Gate 4: Causal Missed Opportunity Audit & Quote Tape Verification...")
    g4 = run_pytest([
        "tests/test_v16_16_audit_coverage_and_touch.py",
        "tests/test_v16_15_causal_audit_classification.py",
        "tests/test_v16_14_missed_opportunity_funnel.py",
        "-v",
    ])
    results["gate_4_causal_audit"] = g4
    print(f"    Status: {g4['status']} ({g4['stats']['passed']} passed in {g4['elapsed_sec']}s)")

    # Gate 5: HTF Structure, Pullback & End-to-End Production Flow
    print(">>> Executing Gate 5: HTF Structure, Pullback & End-to-End Production Flow...")
    g5 = run_pytest([
        "tests/test_v16_17_production_flow.py",
        "tests/test_v16_14_htf_pullback_correctness.py",
        "tests/test_v16_13_htf_confirmation.py",
        "tests/test_v16_11_htf_structure.py",
        "-v",
    ])
    results["gate_5_htf_and_production_flow"] = g5
    print(f"    Status: {g5['status']} ({g5['stats']['passed']} passed in {g5['elapsed_sec']}s)")

    # Gate 6: Full V16 Regression Suite
    print(">>> Executing Gate 6: Full V16 Regression Suite (-k v16)...")
    g6 = run_pytest(["-k", "v16", "-q"])
    results["gate_6_v16_regression"] = g6
    print(f"    Status: {g6['status']} ({g6['stats']['passed']} passed, {g6['stats']['deselected']} deselected in {g6['elapsed_sec']}s)")

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
        results["gate_4_causal_audit"],
        results["gate_5_htf_and_production_flow"],
        results["gate_6_v16_regression"],
    ]
    if not args.skip_frontend:
        mandatory_gates.append(results["gate_7_frontend"])

    all_mandatory_passed = all(g.get("status") == "PASS" for g in mandatory_gates)
    overall_status = "PASS" if all_mandatory_passed else "FAIL"

    # Save artifact raw JSON
    raw_artifact_path = os.path.join(artifact_dir, "acceptance_results.json")
    with open(raw_artifact_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp_iso": now_iso,
                "commit_sha": commit_sha,
                "diff_hash": diff_hash,
                "overall_status": overall_status,
                "forward_status": "COLLECTING",
                "trading_performance": "NOT_EVALUATED",
                "results": results,
            },
            f,
            indent=2,
        )

    # Generate Reports
    generate_acceptance_report(commit_sha, diff_hash, results, overall_status, now_iso)
    generate_implementation_map()
    generate_paper_run_report()

    print(f"\n=======================================================")
    print(f"  V16.18 ACCEPTANCE OVERALL STATUS: {overall_status}")
    print(f"  Forward Observation Status: COLLECTING")
    print(f"  Reports written to:")
    print(f"    - docs/V16_18_CG01_ACCEPTANCE_REPORT.md")
    print(f"    - docs/V16_18_CG01_IMPLEMENTATION_MAP.md")
    print(f"    - docs/V16_18_CG01_PAPER_RUN_REPORT.md")
    print(f"=======================================================\n")

    sys.exit(0 if overall_status == "PASS" else 1)


def generate_acceptance_report(
    commit_sha: str,
    diff_hash: str,
    results: Dict[str, Any],
    overall_status: str,
    now_iso: str,
) -> None:
    doc_path = os.path.join(DOCS, "V16_18_CG01_ACCEPTANCE_REPORT.md")
    lines = [
        "# V16.18 CG-01 Runtime Acceptance Report",
        "",
        f"- **Timestamp**: `{now_iso}`",
        f"- **Commit SHA**: `{commit_sha}`",
        f"- **Diff Hash**: `{diff_hash}`",
        f"- **Overall Status**: **{overall_status}**",
        f"- **Forward Status**: `COLLECTING` (Truthful runtime observation; 0 synthetic forward samples)",
        "",
        "## Summary of Mandatory Technical Gates",
        "",
        "| Gate | Focus Area | Status | Tests Passed | Duration |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]

    gate_meta = [
        ("gate_1_isolation_and_red_defects", "Gate 1: Isolation & Red Defect Verifications"),
        ("gate_2_runtime_and_owner", "Gate 2: Lifespan Integration & Owner Arbitration"),
        ("gate_3_transaction_and_milestones", "Gate 3: Transaction Safety, Linkage & Milestones"),
        ("gate_4_causal_audit", "Gate 4: Causal Missed Audit & Quote Coverage"),
        ("gate_5_htf_and_production_flow", "Gate 5: HTF Structure & Production Flow"),
        ("gate_6_v16_regression", "Gate 6: Full V16 Suite Non-Regression"),
        ("gate_7_frontend", "Gate 7: Frontend Vitest Suite"),
    ]

    for key, name in gate_meta:
        res = results.get(key, {})
        st = res.get("status", "UNKNOWN")
        passed = res.get("stats", {}).get("passed", 0)
        dur = res.get("elapsed_sec", 0.0)
        lines.append(f"| {name} | {st} | {passed} | {dur}s |")

    lines.extend([
        "",
        "## Architectural Defects Repaired in V16.18",
        "1. **Defect 1 - Telemetry Commits Caller Session (Rule 022, 031, 032)**: `CG01TracePersistenceWriter.persist_trace` no longer prematurely executes `target_db.commit()` on the caller session. Dedicated asynchronous worker sessions handle commits, while caller sessions are strictly flushed.",
        "2. **Defect 2 - Shadow Isolation Violates Active State (Rules 021, 041-045)**: Scheduler shadow evaluation decouples completely from singleton `scalp_watcher_orchestrator.evaluate_cycle()`. Added pure adapter `evaluate_cg01_shadow_snapshot()` reusing the canonical CG-01 evaluator without mutating active watchlist, hypotheses, rejections, or business tables.",
        "3. **Defect 3 - Run Resolver Ignores Scope (Rules 023, 051-053)**: `resolve_active_observation_run` now enforces strict scope match in SQL query and does not select runs based solely on symbol.",
        "4. **Defect 4 - Cross-Run Event Contamination in Projection (Rules 024, 075)**: `CG01ResearchProjectionWorker._handle_armed_event` removes blind fallback to currently active run for older orders, ensuring historical orders are not improperly linked to new runs.",
        "5. **Defect 5 - Checkpoint Skips Unlinked Domain Events (Rules 025, 074-080)**: Introduced explicit `ProjectionOutcome` (`PROJECTED`, `ALREADY_PROJECTED`, `RETRY_PENDING`, `UNVERIFIABLE`, `UNSUPPORTED`) and durable table `paper_research_pending_projections`. Unlinked events are persisted and retried with backoff instead of being permanently dropped.",
        "6. **Defect 6 - Coverage Failure After Restart (Rules 026, 094, 095)**: `build_cg01_paper_run_summary` queries `cg01_trace_recorder.get_traces(symbol, run_id=run.run_id)` with SQLite fallback and merged ring buffer, ensuring historical run traces remain queryable after memory clear.",
        "7. **Defect 7 - Distortion of Restored Timestamps and Latencies (Rules 027, 091-093)**: `load_traces` restores truthful timestamps (`evaluation_started_at`, `evaluation_completed_at`, `exchange_event_time_ms`, etc.) directly from JSON payload rather than overriding them with `created_at_ms`.",
        "8. **Defect 8 - Conflation of Trace Health with Uptime (Rules 028, 111-115)**: `build_run_coverage_summary` introduces `trace_health_pct` for valid trace ratios, and requires `paper_observation_intervals` to report wall-clock `uptime_pct`, preventing 100% uptime claims during multi-hour downtime.",
        "9. **Defect 9 - Cross-Mode Trace Pollution (Rules 029, 094, 095)**: `CG01TraceRecorder` and SQLite tables support `observation_mode` (`SHADOW` vs `ACTIVE`), enabling precise filtered queries by mode and run.",
    ])

    with open(doc_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def generate_implementation_map() -> None:
    doc_path = os.path.join(DOCS, "V16_18_CG01_IMPLEMENTATION_MAP.md")
    lines = [
        "# V16.18 CG-01 Implementation Map",
        "",
        "| Component / Function | File | Transaction Ownership | Callers | Verification Node ID |",
        "| :--- | :--- | :--- | :--- | :--- |",
        "| `persist_trace` | `cg01_telemetry_service.py` | Dedicated worker commits; caller session only flushes | `record_trace`, `run_trace_persistence_worker` | `test_v16_18_red_reproductions.py::test_red_022_telemetry_commits_caller_uncommitted_transaction` |",
        "| `evaluate_cg01_shadow_snapshot` | `cg01_scheduler_service.py` | Pure evaluation / zero DB side-effects | `evaluate_symbol_now` (shadow mode) | `test_v16_18_red_reproductions.py::test_red_021_shadow_isolation_violates_active_state` |",
        "| `resolve_active_observation_run` | `paper_research_run_service.py` | Pure DB query with scope & mode filter | `evaluate_symbol_now`, `cg01_projection_worker` | `test_v16_18_red_reproductions.py::test_red_023_resolve_active_run_ignores_scope` |",
        "| `_handle_armed_event` | `cg01_projection_worker.py` | Projection worker transaction | `process_pending_events` | `test_v16_18_red_reproductions.py::test_red_024_projection_attaches_old_event_to_new_run` |",
        "| `record_pending_projection` | `cg01_projection_worker.py` | Worker transaction alongside checkpoint | `process_pending_events`, opened/closed handlers | `test_v16_18_red_reproductions.py::test_red_025_projection_checkpoint_skips_unlinked_events` |",
        "| `build_cg01_paper_run_summary` | `paper_research_run_service.py` | Read-only query (DB authority) | Reports, dashboard API | `test_v16_18_red_reproductions.py::test_red_026_coverage_fails_after_restart_without_buffer` |",
        "| `load_traces` | `cg01_telemetry_service.py` | Read-only query with true timestamp restoration | `get_traces`, `build_cg01_paper_run_summary` | `test_v16_18_red_reproductions.py::test_red_027_trace_timestamps_and_latencies_restoration` |",
        "| `build_run_coverage_summary` | `paper_research_run_service.py` | Pure calculation with interval support | `build_cg01_paper_run_summary` | `test_v16_18_red_reproductions.py::test_red_028_uptime_vs_trace_health_with_gap` |",
        "| `get_traces_by_mode` | `cg01_telemetry_service.py` | Memory + SQLite query by mode | Reports, diagnostic API | `test_v16_18_red_reproductions.py::test_red_029_trace_query_mixes_shadow_and_active` |",
    ]
    with open(doc_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def generate_paper_run_report() -> None:
    doc_path = os.path.join(DOCS, "V16_18_CG01_PAPER_RUN_REPORT.md")
    try:
        from database import SessionLocal
        from services.paper_research_run_service import (
            generate_runtime_paper_report_content,
            resolve_active_observation_run,
        )
        db = SessionLocal()
        try:
            active_run = resolve_active_observation_run(db, scope="CG01", symbol="XAUUSDT")
            run_id = active_run.run_id if active_run else "run_cg01_v16_18_default"
            rep = generate_runtime_paper_report_content(run_id=run_id, db=db)
            md = rep.get("markdown") or "# V16.18 CG-01 Paper Run Status\n\nNo report data available."
            with open(doc_path, "w", encoding="utf-8") as f:
                f.write(md)
        finally:
            db.close()
    except Exception as e:
        with open(doc_path, "w", encoding="utf-8") as f:
            f.write(f"# V16.18 CG-01 Paper Run Status\n\nReport generation fallback: {e}\n")


if __name__ == "__main__":
    main()
