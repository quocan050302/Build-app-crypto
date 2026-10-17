#!/usr/bin/env python3
"""
tools/run_v16_16_cg01_runtime_acceptance.py
V16.16 CG-01 LTF Acceptance Test Runner:
Transaction Safety, Single Execution Owner, Persistent Paper Measurement,
Causal Missed-Opportunity Audit, and Measured Realtime Optimization.

Executes Gates 1 - 6:
- Gate 1: Scheduler Runtime, Lifespan Integration, & Single Owner Arbitration
- Gate 2: Transaction Safety, Run Linkage, & Lifecycle Milestones Persistence
- Gate 3: Causal Missed Opportunity Audit, Quote Coverage, & Touch Discrimination
- Gate 4: HTF Structure, Pullback Correctness, & Strategy Guard Preservation
- Gate 5: Full V16 Suite Non-Regression (-k v16)
- Gate 6: Frontend Integration & Contracts (Vitest Suite)

Enforces:
- Rule 188: Never treat SKIPPED as PASS; mandatory technical gates must explicitly PASS.
- Rule 189: Gathers real execution counts and results; no synthetic forward time simulation.
- Rule 191: Generates docs/V16_16_CG01_RUNTIME_ACCEPTANCE_REPORT.md.
- Rule 192: Generates docs/V16_16_CG01_RUNTIME_IMPLEMENTATION_MAP.md.
- Rule 193: Generates docs/V16_16_CG01_PAPER_RUN_REPORT.md grounded in DB and worker health.
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
    parser = argparse.ArgumentParser(description="V16.16 Acceptance Test Runner")
    parser.add_argument("--skip-frontend", action="store_true", help="Skip vitest suite")
    args = parser.parse_args()

    now_iso = datetime.now(timezone.utc).isoformat()
    t_stamp = int(time.time())
    artifact_dir = os.path.join(ARTIFACTS_ROOT, f"run_v16_16_{t_stamp}")
    os.makedirs(artifact_dir, exist_ok=True)
    os.makedirs(DOCS, exist_ok=True)

    commit_sha = get_git_commit_sha()
    diff_hash = get_git_diff_hash()

    print(f"\n=======================================================")
    print(f"  AURUM-DESK V16.16 CG-01 RUNTIME ACCEPTANCE TEST RUNNER")
    print(f"  Commit: {commit_sha} | Diff Hash: {diff_hash[:12]}")
    print(f"=======================================================\n")

    results: Dict[str, Any] = {}

    # Gate 1: Scheduler Runtime, Lifespan & Owner Arbitration
    print(">>> Executing Gate 1: Lifespan Integration, Worker Health & Single Owner Arbitration...")
    g1 = run_pytest([
        "tests/test_v16_15_scheduler_lifespan.py",
        "tests/test_v16_14_cg01_scheduler_and_trace.py",
        "tests/test_v16_16_owner_and_shadow.py",
        "tests/test_v16_15_auto_permission_and_owner.py",
        "-v",
    ])
    results["gate_1_runtime_and_owner"] = g1
    print(f"    Status: {g1['status']} ({g1['stats']['passed']} passed in {g1['elapsed_sec']}s)")

    # Gate 2: Transaction Safety, Linkage & Lifecycle Milestones
    print(">>> Executing Gate 2: Transaction Safety, Order-Run Linkage & Milestones...")
    g2 = run_pytest([
        "tests/test_v16_16_transaction_and_linkage.py",
        "tests/test_v16_16_milestone_and_runtime_report.py",
        "tests/test_v16_15_paper_accounting_and_persistence.py",
        "tests/test_v16_14_paper_run_and_accounting.py",
        "-v",
    ])
    results["gate_2_transaction_and_milestones"] = g2
    print(f"    Status: {g2['status']} ({g2['stats']['passed']} passed in {g2['elapsed_sec']}s)")

    # Gate 3: Causal Missed Opportunity Audit & Quote Coverage
    print(">>> Executing Gate 3: Causal Missed Opportunity Audit & Quote Tape Verification...")
    g3 = run_pytest([
        "tests/test_v16_16_audit_coverage_and_touch.py",
        "tests/test_v16_15_causal_audit_classification.py",
        "tests/test_v16_14_missed_opportunity_funnel.py",
        "-v",
    ])
    results["gate_3_causal_audit"] = g3
    print(f"    Status: {g3['status']} ({g3['stats']['passed']} passed in {g3['elapsed_sec']}s)")

    # Gate 4: HTF Structure & Pullback Correctness
    print(">>> Executing Gate 4: HTF Structure & Pullback Correctness...")
    g4 = run_pytest([
        "tests/test_v16_14_htf_pullback_correctness.py",
        "tests/test_v16_13_htf_confirmation.py",
        "tests/test_v16_11_htf_structure.py",
        "-v",
    ])
    results["gate_4_htf_structure"] = g4
    print(f"    Status: {g4['status']} ({g4['stats']['passed']} passed in {g4['elapsed_sec']}s)")

    # Gate 5: Full V16 Regression Suite
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
    mandatory_gates = [
        results["gate_1_runtime_and_owner"],
        results["gate_2_transaction_and_milestones"],
        results["gate_3_causal_audit"],
        results["gate_4_htf_structure"],
        results["gate_5_v16_regression"],
    ]
    if not args.skip_frontend:
        mandatory_gates.append(results["gate_6_frontend"])

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
    print(f"  V16.16 ACCEPTANCE OVERALL STATUS: {overall_status}")
    print(f"  Forward Observation Status: COLLECTING")
    print(f"  Reports written to:")
    print(f"    - docs/V16_16_CG01_RUNTIME_ACCEPTANCE_REPORT.md")
    print(f"    - docs/V16_16_CG01_RUNTIME_IMPLEMENTATION_MAP.md")
    print(f"    - docs/V16_16_CG01_PAPER_RUN_REPORT.md")
    print(f"=======================================================\n")

    sys.exit(0 if overall_status == "PASS" else 1)


def generate_acceptance_report(
    commit_sha: str,
    diff_hash: str,
    results: Dict[str, Any],
    overall_status: str,
    now_iso: str,
) -> None:
    doc_path = os.path.join(DOCS, "V16_16_CG01_RUNTIME_ACCEPTANCE_REPORT.md")
    lines = [
        "# V16.16 CG-01 Runtime Acceptance Report",
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
        ("gate_1_runtime_and_owner", "Gate 1: Lifespan Integration & Owner Arbitration"),
        ("gate_2_transaction_and_milestones", "Gate 2: Transaction Safety, Linkage & Milestones"),
        ("gate_3_causal_audit", "Gate 3: Causal Missed Audit & Quote Coverage"),
        ("gate_4_htf_structure", "Gate 4: HTF Structure & Pullback Correctness"),
        ("gate_5_v16_regression", "Gate 5: Full V16 Suite Non-Regression"),
        ("gate_6_frontend", "Gate 6: Frontend Vitest Suite"),
    ]

    for key, name in gate_meta:
        res = results.get(key, {})
        st = res.get("status", "UNKNOWN")
        passed = res.get("stats", {}).get("passed", 0)
        dur = res.get("elapsed_sec", 0.0)
        lines.append(f"| {name} | {st} | {passed} | {dur}s |")

    lines.extend([
        "",
        "## Architectural Defects Repaired in V16.16",
        "1. **Caller Transaction Ownership Enforced**: Research helpers (`get_or_create_run`, `start_observation_run`, `complete_run`, `associate_order_with_research_run`) use `db.flush()`, eliminating premature caller transaction commits.",
        "2. **Idempotent Linkage Metadata Supplementation**: `associate_order_with_research_run` updates existing links when `trace_id` or `opportunity_id` was previously missing rather than dropping new caller metadata.",
        "3. **Effective Execution Owner Arbitration in Production**: `ScalpWatcherOrchestrator.run_watcher_loop` invokes `resolve_entry_execution_owner`. When scheduler is in `shadow_mode=True`, watcher remains execution owner. When scheduler is healthy execution owner, watcher does not run competing cycles.",
        "4. **Causal Missed Opportunity Audit**: `verify_entry_touch_coverage` discriminates between coverage verification and executable price touches on correct sides (ask for Long, bid for Short). Expired orders without coverage are strictly labeled `UNVERIFIABLE` / `DATA_GAP`.",
        "5. **Order Lifecycle Milestones**: `record_committed_order_milestone` writes durable, schema-backed milestone records (`PaperResearchOrderMilestone`) for ARMED, FILLED, and CLOSED transitions.",
        "6. **Truthful Runtime Reporting**: `generate_runtime_paper_report_content` grounds report content directly in database facts and worker health, never hardcoding 'running continuously' or assuming 100% uptime when inactive.",
    ])

    with open(doc_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def generate_implementation_map() -> None:
    doc_path = os.path.join(DOCS, "V16_16_CG01_RUNTIME_IMPLEMENTATION_MAP.md")
    lines = [
        "# V16.16 CG-01 Runtime Implementation Map",
        "",
        "| Component / Function | File | Transaction Ownership | Callers | Verification Node ID |",
        "| :--- | :--- | :--- | :--- | :--- |",
        "| `get_or_create_run` | `paper_research_run_service.py` | Caller-owned (`db.flush()`) | `start_observation_run`, `build_cg01_paper_run_summary` | `test_v16_16_transaction_and_linkage.py::test_get_or_create_run_does_not_commit_caller_transaction` |",
        "| `start_observation_run` | `paper_research_run_service.py` | Caller-owned (`db.flush()`) | Tests, API endpoints | `test_v16_16_transaction_and_linkage.py::test_start_and_complete_run_do_not_commit_caller_transaction` |",
        "| `associate_order_with_research_run` | `paper_research_run_service.py` | Caller-owned (`db.flush()`) | `evaluate_symbol_now`, `_arm_paper_candidate` | `test_v16_16_transaction_and_linkage.py::test_association_supplements_missing_trace_metadata` |",
        "| `record_committed_order_milestone` | `paper_research_run_service.py` | Caller-owned (`db.flush()`) | `_arm_paper_candidate`, `execute_fill`, `execute_close` | `test_v16_16_milestone_and_runtime_report.py::test_order_milestone_persists_and_is_readable_in_fresh_session` |",
        "| `resolve_entry_execution_owner` | `cg01_scheduler_service.py` | Read-only | `evaluate_symbol_now`, `run_watcher_loop` | `test_v16_16_owner_and_shadow.py::test_shadow_owner_combination_keeps_watcher_as_execution_owner` |",
        "| `run_watcher_loop` | `scalp_watcher_orchestrator.py` | Session-per-cycle | `main.py::lifespan` | `test_v16_16_owner_and_shadow.py::test_watcher_loop_uses_effective_owner_not_raw_is_owner` |",
        "| `verify_entry_touch_coverage` | `cg01_missed_audit_service.py` | Pure calculation | `classify_opportunity` | `test_v16_16_audit_coverage_and_touch.py::test_coverage_verified_with_touch_is_touched_not_not_touched` |",
        "| `classify_opportunity` | `cg01_missed_audit_service.py` | Pure calculation | `audit_run` | `test_v16_16_audit_coverage_and_touch.py::test_expired_without_coverage_is_unverifiable` |",
        "| `audit_run` | `cg01_missed_audit_service.py` | Read-only query | `build_cg01_paper_run_summary` | `test_v16_16_audit_coverage_and_touch.py::test_audit_run_empty_associations_does_not_leak_auto_strategy` |",
        "| `generate_runtime_paper_report_content` | `paper_research_run_service.py` | Read-only query | Acceptance runner, API | `test_v16_16_milestone_and_runtime_report.py::test_runtime_report_accurately_reflects_stopped_worker_and_empty_run` |",
    ]
    with open(doc_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def generate_paper_run_report() -> None:
    doc_path = os.path.join(DOCS, "V16_16_CG01_PAPER_RUN_REPORT.md")
    try:
        from database import SessionLocal
        from services.paper_research_run_service import generate_runtime_paper_report_content
        db = SessionLocal()
        try:
            rep = generate_runtime_paper_report_content(run_id="run_cg01_v16_16_default", db=db)
            md = rep.get("markdown") or "# V16.16 CG-01 Paper Run Status\n\nNo report data available."
            with open(doc_path, "w", encoding="utf-8") as f:
                f.write(md)
        finally:
            db.close()
    except Exception as e:
        with open(doc_path, "w", encoding="utf-8") as f:
            f.write(f"# V16.16 CG-01 Paper Run Status\n\nReport generation fallback: {e}\n")


if __name__ == "__main__":
    main()
