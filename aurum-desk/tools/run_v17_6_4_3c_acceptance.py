#!/usr/bin/env python3
"""
AURUM V17.6.4.3c — 46-GATE MASTER ACCEPTANCE RUNNER
===================================================
Enforces 46 rigorous verification gates (G00–G45) per Master Prompt V17.6.4.3c.
Guarantees:
- Trusted transport authority overrides raw quote metadata (CPA-01)
- Quote source cannot self-authorize WS without adapter verification (CPA-02)
- Exchange timestamp mandatory for WS GOOD (CPA-03)
- Connection epoch mandatory and verified against adapter (CPA-04)
- Strict scheduler durability with independent DB probe readback (CPA-05, CPA-06)
- Mandatory Playwright E2E browser acceptance (CPA-07)
- Runtime zero-live-orders verification (CPA-08)
- Acceptance artifact and source SHA integrity (CPA-09, CPA-10)
- Pure acceptance decision evaluation logic reusable in negative tests
"""
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
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
from typing import Dict, Any, List, Optional

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

MANDATORY_GATES = [
    "G00_clean_branch_and_source",
    "G01_adapter_transport_overrides_quote",
    "G02_disconnected_cannot_produce_good",
    "G03_reconnecting_cannot_produce_good",
    "G04_unknown_adapter_cannot_produce_good",
    "G05_trusted_quote_source_integrity",
    "G06_rest_fallback_cannot_impersonate_ws",
    "G07_exchange_timestamp_mandatory",
    "G08_receive_timestamp_mandatory",
    "G09_timestamp_consistency",
    "G10_connection_epoch_mandatory",
    "G11_old_epoch_rejected",
    "G12_fresh_current_epoch_positive_control",
    "G13_watcher_disconnected_integration",
    "G14_watcher_good_recovery",
    "G15_correct_interval_quality_after_commit",
    "G16_no_fabricated_coverage_during_disconnect",
    "G17_scheduler_evaluate_symbol_now",
    "G18_scheduler_event_admission",
    "G19_scheduler_single_flight",
    "G20_scheduler_exact_cycle_persistence",
    "G21_scheduler_independent_readback",
    "G22_scheduler_reconnect_recovery",
    "G23_scheduler_run_loop_dispatch",
    "G24_scheduler_failure_restart_handling",
    "G25_dashboard_unverified_health_truth",
    "G26_dashboard_degraded_down_health_truth",
    "G27_persistence_pending_truth",
    "G28_api_frontend_status_reconciliation",
    "G29_database_identity_isolation",
    "G30_sqlite_transaction_rollback",
    "G31_independent_duplicate_commit_proof",
    "G32_replay_test_live_separation",
    "G33_full_backend_test_discovery",
    "G34_full_backend_test_execution",
    "G35_full_frontend_vitest",
    "G36_frontend_production_build",
    "G37_frontend_lint",
    "G38_actual_playwright_e2e",
    "G39_runtime_zero_live_orders",
    "G40_behavioral_paper_strategy_parity",
    "G41_deterministic_observation_soak",
    "G42_acceptance_negative_tests",
    "G43_artifact_checksum_and_sha_integrity",
    "G44_clean_source_after_acceptance",
    "G45_controlled_paper_operational_smoke",
]


@dataclass
class AcceptanceDecision:
    verdict: str  # "ACCEPTED", "REJECTED", "DEVELOPMENT_TEST_ONLY"
    accepted: bool
    reason: str
    passed_gates: int
    total_gates: int
    unmet_requirements: List[str]


def evaluate_release_acceptance(
    *,
    gate_results: Dict[str, Any],
    source_integrity: Dict[str, Any],
    artifact_integrity: Dict[str, Any],
    execution_mode: str = "release",
    mandatory_gates: Optional[List[str]] = None,
) -> AcceptanceDecision:
    """
    Pure verdict evaluation function (PART 149).
    Used both by this runner and by the acceptance negative test suite (AI01-AI20).
    """
    gates_list = mandatory_gates if mandatory_gates is not None else MANDATORY_GATES
    unmet = []

    # Check source cleanliness
    if not source_integrity.get("clean_before", False):
        unmet.append("DIRTY_WORKTREE_BEFORE_ACCEPTANCE")
    if not source_integrity.get("clean_after", False):
        unmet.append("DIRTY_WORKTREE_AFTER_ACCEPTANCE")
    if source_integrity.get("branch") != "fix/v14-1-rr-flow":
        unmet.append(f"INVALID_BRANCH_{source_integrity.get('branch')}")
    if source_integrity.get("pre_sha") != source_integrity.get("post_sha"):
        unmet.append("SOURCE_SHA_CHANGED_DURING_ACCEPTANCE")

    # Check artifact integrity
    if not artifact_integrity.get("all_logs_present", False):
        unmet.append(f"MISSING_EVIDENCE_LOGS: {artifact_integrity.get('missing_logs', [])}")
    if not artifact_integrity.get("hashes_verified", False):
        unmet.append("ARTIFACT_CHECKSUM_VERIFICATION_FAILED")

    # Check mandatory gates
    passed_count = 0
    for g in gates_list:
        g_data = gate_results.get(g)
        if not g_data:
            unmet.append(f"GATE_NOT_PRESENT_{g}")
            continue
        status = g_data.get("status")
        skipped = g_data.get("skipped", 0)
        if status == "PASS" and skipped == 0:
            passed_count += 1
        elif status == "SKIPPED" or skipped > 0:
            unmet.append(f"UNEXPECTED_SKIP_IN_{g}")
        else:
            unmet.append(f"GATE_FAILED_{g}:{g_data.get('reason', 'UNKNOWN')}")

    total_gates = len(gates_list)
    all_passed = (passed_count == total_gates) and (len(unmet) == 0)

    if execution_mode != "release":
        verdict = "DEVELOPMENT_TEST_ONLY"
        accepted = False
        reason = "Non-release execution mode" if all_passed else f"Dev failures: {unmet}"
    else:
        accepted = all_passed
        verdict = "ACCEPTED" if accepted else "REJECTED"
        reason = "All mandatory requirements verified" if accepted else f"Unmet requirements: {unmet}"

    return AcceptanceDecision(
        verdict=verdict,
        accepted=accepted,
        reason=reason,
        passed_gates=passed_count,
        total_gates=total_gates,
        unmet_requirements=unmet,
    )


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def compute_sha256(file_path):
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def parse_junit_xml(junit_path):
    """Parses JUnit XML to extract tests, failures, errors, skipped, and skipped test names."""
    p = Path(junit_path)
    if not p.exists():
        return None
    try:
        tree = ET.parse(p)
        root = tree.getroot()
        suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
        if not suites and root.tag == "testsuites":
            suites = root.findall("testsuite")
        
        total_tests = 0
        total_failures = 0
        total_errors = 0
        total_skipped = 0
        skipped_names = []

        for s in suites:
            total_tests += int(s.attrib.get("tests", 0))
            total_failures += int(s.attrib.get("failures", 0))
            total_errors += int(s.attrib.get("errors", 0))
            total_skipped += int(s.attrib.get("skipped", 0))
            for tc in s.findall("testcase"):
                skip = tc.find("skipped")
                if skip is not None:
                    name = f"{tc.attrib.get('classname', '')}::{tc.attrib.get('name', '')}"
                    skipped_names.append(name)

        return {
            "tests": total_tests,
            "failures": total_failures,
            "errors": total_errors,
            "skipped": total_skipped,
            "skipped_names": skipped_names,
        }
    except Exception as exc:
        return {"error": str(exc)}


def run_cmd(gate_name, cmd, cwd, env, out_dir, junit_path=None, timeout=300, source_sha="UNKNOWN"):
    """Executes a command subprocess with detailed evidence recording."""
    log_file = out_dir / f"{gate_name}.log"
    started_at = datetime.now(timezone.utc).isoformat()
    start_mono = time.monotonic()
    try:
        res = subprocess.run(
            cmd, cwd=cwd, env=env, capture_output=True,
            text=True, errors="replace", timeout=timeout,
        )
        code = res.returncode
        stdout = (res.stdout or "") + "\n" + (res.stderr or "")
    except subprocess.TimeoutExpired as exc:
        code = 124
        stdout = (exc.stdout or "") + "\n" + (exc.stderr or "") + f"\nTIMEOUT after {timeout}s"
    except Exception as exc:
        code = -1
        stdout = repr(exc)

    elapsed = round(time.monotonic() - start_mono, 2)
    completed_at = datetime.now(timezone.utc).isoformat()
    log_file.write_text(stdout, encoding="utf-8")
    evidence_hash = compute_sha256(log_file)

    test_count = 0
    passed = 0
    failed = 0
    skipped = 0
    skipped_names = []

    if junit_path and Path(junit_path).exists():
        parsed = parse_junit_xml(junit_path)
        if parsed and "error" not in parsed:
            test_count = parsed["tests"]
            failed = parsed["failures"] + parsed["errors"]
            skipped = parsed["skipped"]
            passed = test_count - failed - skipped
            skipped_names = parsed["skipped_names"]

    # Determine status
    if code == 0:
        if skipped > 0:
            status = "FAIL"
            reason = f"UNEXPECTED_SKIPS: {skipped_names}"
        else:
            status = "PASS"
            reason = "OK"
    else:
        status = "FAIL"
        reason = f"EXIT_CODE_{code}"

    return {
        "gate": gate_name,
        "command": " ".join(cmd) if isinstance(cmd, list) else cmd,
        "working_directory": str(cwd),
        "source_sha": source_sha,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration": elapsed,
        "elapsed_sec": elapsed,
        "exit_code": code,
        "test_count": test_count,
        "executed": test_count,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "skipped_names": skipped_names,
        "status": status,
        "reason": reason,
        "evidence_file": str(log_file),
        "evidence_hash": evidence_hash,
        "output_tail": "\n".join(stdout.strip().splitlines()[-10:]),
    }


def run_backend_discovery(python, cwd, env, out_dir, source_sha):
    """Executes pytest collection-only to discover all backend tests."""
    log_file = out_dir / "G33_full_backend_test_discovery.log"
    started_at = datetime.now(timezone.utc).isoformat()
    start_mono = time.monotonic()

    cmd = [python, "-m", "pytest", "backend/tests", "--collect-only", "-q"]
    try:
        res = subprocess.run(
            cmd, cwd=cwd, env=env, capture_output=True,
            text=True, errors="replace", timeout=180,
        )
        code = res.returncode
        stdout = (res.stdout or "") + "\n" + (res.stderr or "")
    except Exception as exc:
        code = -1
        stdout = repr(exc)

    elapsed = round(time.monotonic() - start_mono, 2)
    completed_at = datetime.now(timezone.utc).isoformat()
    log_file.write_text(stdout, encoding="utf-8")

    items = [line for line in stdout.splitlines() if "::" in line]
    match = re.search(r"(\d+)\s+tests\s+collected", stdout)
    if match:
        collected_count = int(match.group(1))
    else:
        collected_count = len(items)

    has_collection_error = (code != 0) or ("error during collection" in stdout.lower()) or ("errors during collection" in stdout.lower())

    if code != 0:
        status = "FAIL"
        reason = f"PYTEST_COLLECTION_EXIT_{code}"
    elif has_collection_error:
        status = "FAIL"
        reason = "COLLECTION_ERROR_DETECTED"
    elif collected_count <= 0:
        status = "FAIL"
        reason = "ZERO_TESTS_COLLECTED"
    else:
        status = "PASS"
        reason = f"Successfully discovered {collected_count} tests dynamically across backend/tests"

    return {
        "gate": "G33_full_backend_test_discovery",
        "command": " ".join(cmd),
        "working_directory": str(ROOT),
        "source_sha": source_sha,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration": elapsed,
        "elapsed_sec": elapsed,
        "exit_code": code,
        "test_count": collected_count,
        "executed": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "status": status,
        "reason": reason,
        "evidence_file": str(log_file),
        "evidence_hash": compute_sha256(log_file),
        "output_tail": "\n".join(stdout.strip().splitlines()[-10:]),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="AURUM V17.6.4.3c 46-Gate Acceptance Runner")
    parser.add_argument("--mode", choices=["release", "dev"], default="release", help="Execution mode (release | dev)")
    parser.add_argument("--allow-dirty", action="store_true", help="Allow dirty worktree (dev mode only; never produces ACCEPTED)")
    parser.add_argument("--out-dir", type=str, default=None, help="Output directory for logs, reports, and evidence")
    args = parser.parse_args(argv)

    is_dev_mode = (args.mode == "dev") or args.allow_dirty
    start_time = time.monotonic()
    if args.out_dir:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = Path(tempfile.mkdtemp(prefix="aurum_v17_6_4_3c_artifacts_"))

    print(f"=== AURUM V17.6.4.3c MASTER ACCEPTANCE RUNNER ===")
    print(f"Mode: {'DEVELOPMENT' if is_dev_mode else 'RELEASE'}")
    print(f"Artifact Directory: {out_dir}\n")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND)
    env["TESTING"] = "1"
    env["AURUM_E2E_REPORT"] = str(out_dir / "playwright_report.json")

    python = str(BACKEND / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    if not Path(python).exists():
        python = sys.executable
    env["AURUM_E2E_PYTHON"] = python

    gates = {}

    # G00: Git Branch & Worktree Cleanliness (Pre-Test)
    print("Checking G00_clean_branch_and_source...", flush=True)
    current_branch = git("branch", "--show-current")
    pre_sha = git("rev-parse", "HEAD")
    branch_ok = (current_branch == "fix/v14-1-rr-flow")
    porcelain_status = git("status", "--porcelain=v1", "--untracked-files=all")
    clean_before = len(porcelain_status.strip()) == 0

    if is_dev_mode:
        pre_clean_ok = branch_ok
        pre_reason = "OK (dev mode / allow-dirty)"
    else:
        pre_clean_ok = branch_ok and clean_before
        pre_reason = "OK" if pre_clean_ok else (
            "Branch must be fix/v14-1-rr-flow" if not branch_ok else f"Worktree must be clean:\n{porcelain_status}"
        )

    gates["G00_clean_branch_and_source"] = {
        "gate": "G00_clean_branch_and_source",
        "branch": current_branch,
        "source_sha": pre_sha,
        "status": "PASS" if pre_clean_ok else "FAIL",
        "reason": pre_reason,
        "duration": 0.0,
        "elapsed_sec": 0.0,
    }
    print(f"G00: {gates['G00_clean_branch_and_source']['status']}\n")

    # Pytest Suites (G01 - G32)
    py_suites = [
        # Provenance Authority Gates (G01 - G12)
        ("G01_adapter_transport_overrides_quote", ["backend/tests/test_v17_6_4_3c_transport_authority.py", "-k", "test_ta01"]),
        ("G02_disconnected_cannot_produce_good", ["backend/tests/test_v17_6_4_3c_transport_authority.py", "-k", "test_ta04"]),
        ("G03_reconnecting_cannot_produce_good", ["backend/tests/test_v17_6_4_3c_transport_authority.py", "-k", "test_ta02"]),
        ("G04_unknown_adapter_cannot_produce_good", ["backend/tests/test_v17_6_4_3c_transport_authority.py", "-k", "test_ta03"]),
        ("G05_trusted_quote_source_integrity", ["backend/tests/test_v17_6_4_3c_source_authority.py", "-k", "test_sa05"]),
        ("G06_rest_fallback_cannot_impersonate_ws", ["backend/tests/test_v17_6_4_3c_source_authority.py", "-k", "test_sa01"]),
        ("G07_exchange_timestamp_mandatory", ["backend/tests/test_v17_6_4_3c_timestamp_authority.py", "-k", "test_ts01"]),
        ("G08_receive_timestamp_mandatory", ["backend/tests/test_v17_6_4_3c_timestamp_authority.py", "-k", "test_ts02"]),
        ("G09_timestamp_consistency", ["backend/tests/test_v17_6_4_3c_timestamp_authority.py", "-k", "test_ts08"]),
        ("G10_connection_epoch_mandatory", ["backend/tests/test_v17_6_4_3c_epoch_authority.py", "-k", "test_ep01"]),
        ("G11_old_epoch_rejected", ["backend/tests/test_v17_6_4_3c_epoch_authority.py", "-k", "test_ep02"]),
        ("G12_fresh_current_epoch_positive_control", ["backend/tests/test_v17_6_4_3c_epoch_authority.py", "-k", "test_ep07"]),

        # Watcher Integration Gates (G13 - G16)
        ("G13_watcher_disconnected_integration", ["backend/tests/test_v17_6_4_3c_transport_authority.py", "-k", "test_ta12"]),
        ("G14_watcher_good_recovery", ["backend/tests/test_v17_6_4_3b_feed_provenance.py", "-k", "test_fp25"]),
        ("G15_correct_interval_quality_after_commit", ["backend/tests/test_v17_6_4_3c_transport_authority.py", "-k", "test_ta05"]),
        ("G16_no_fabricated_coverage_during_disconnect", ["backend/tests/test_v17_6_4_3c_transport_authority.py", "-k", "test_ta11"]),

        # Scheduler Runtime Gates (G17 - G24)
        ("G17_scheduler_evaluate_symbol_now", ["backend/tests/test_v17_6_4_3c_scheduler_durability.py", "-k", "test_sd01"]),
        ("G18_scheduler_event_admission", ["backend/tests/test_v17_6_4_3b_scheduler_runtime.py", "-k", "test_sc02"]),
        ("G19_scheduler_single_flight", ["backend/tests/test_v17_6_4_3b_scheduler_runtime.py", "-k", "test_sc12"]),
        ("G20_scheduler_exact_cycle_persistence", ["backend/tests/test_v17_6_4_3c_scheduler_durability.py", "-k", "test_sd02"]),
        ("G21_scheduler_independent_readback", ["backend/tests/test_v17_6_4_3c_scheduler_durability.py", "-k", "test_sd04"]),
        ("G22_scheduler_reconnect_recovery", ["backend/tests/test_v17_6_4_3b_scheduler_runtime.py", "-k", "test_sc17"]),
        ("G23_scheduler_run_loop_dispatch", ["backend/tests/test_v17_6_4_3c_scheduler_durability.py", "-k", "test_sd06"]),
        ("G24_scheduler_failure_restart_handling", ["backend/tests/test_v17_6_4_3b_scheduler_runtime.py", "-k", "test_sc16"]),

        # Health & Telemetry Gates (G25 - G28)
        ("G25_dashboard_unverified_health_truth", ["backend/tests/test_v17_6_4_3b_health_truth.py", "-k", "test_eh02"]),
        ("G26_dashboard_degraded_down_health_truth", ["backend/tests/test_v17_6_4_3b_health_truth.py", "-k", "test_eh06"]),
        ("G27_persistence_pending_truth", ["backend/tests/test_v17_6_4_3b_health_truth.py", "-k", "test_eh11"]),
        ("G28_api_frontend_status_reconciliation", ["backend/tests/test_v17_6_4_3b_health_truth.py", "-k", "test_eh16"]),

        # Database Isolation Gates (G29 - G32)
        ("G29_database_identity_isolation", ["backend/tests/test_v17_6_4_3b_unique_commit_truth.py", "-k", "test_uc06"]),
        ("G30_sqlite_transaction_rollback", ["backend/tests/test_v17_6_4_3b_unique_commit_truth.py", "-k", "test_uc03"]),
        ("G31_independent_duplicate_commit_proof", ["backend/tests/test_v17_6_4_3c_scheduler_durability.py", "-k", "test_sd03"]),
        ("G32_replay_test_live_separation", ["backend/tests/test_v17_6_4_3b_unique_commit_truth.py", "-k", "test_uc07"]),
    ]

    for gate_name, pytest_args in py_suites:
        print(f"Running {gate_name}...", flush=True)
        junit_file = out_dir / f"{gate_name}.xml"
        cmd = [python, "-m", "pytest", *pytest_args, "-q", f"--junitxml={junit_file}"]
        gate_res = run_cmd(
            gate_name, cmd, ROOT, env, out_dir,
            junit_path=junit_file, timeout=120, source_sha=pre_sha,
        )
        if gate_res is not None:
            gates[gate_name] = gate_res
            print(f"{gate_name}: {gate_res.get('status', 'FAIL')}")

    # G33: Full backend test discovery
    print("Running G33_full_backend_test_discovery...", flush=True)
    gates["G33_full_backend_test_discovery"] = run_backend_discovery(
        python, ROOT, env, out_dir, pre_sha,
    )
    print(f"G33: {gates['G33_full_backend_test_discovery']['status']}")

    # G34: Full backend test execution
    print("Running G34_full_backend_test_execution...", flush=True)
    g34_junit = out_dir / "G34_full_backend_test_execution.xml"
    g34_cmd = [python, "-m", "pytest", "backend/tests", "-q", f"--junitxml={g34_junit}"]
    gates["G34_full_backend_test_execution"] = run_cmd(
        "G34_full_backend_test_execution", g34_cmd, ROOT, env, out_dir,
        junit_path=g34_junit, timeout=600, source_sha=pre_sha,
    )
    print(f"G34: {gates['G34_full_backend_test_execution']['status']}")

    # G35: Full frontend Vitest
    print("Running G35_full_frontend_vitest...", flush=True)
    g35_junit = out_dir / "G35_full_frontend_vitest.xml"
    g35_cmd = ["npm", "test", "--", "--run", "--reporter=junit", f"--outputFile={g35_junit}"]
    gates["G35_full_frontend_vitest"] = run_cmd(
        "G35_full_frontend_vitest", g35_cmd, FRONTEND, env, out_dir,
        junit_path=g35_junit, timeout=300, source_sha=pre_sha,
    )
    print(f"G35: {gates['G35_full_frontend_vitest']['status']}")

    # G36: Frontend production build
    print("Running G36_frontend_production_build...", flush=True)
    g36_cmd = ["npm", "run", "build"]
    gates["G36_frontend_production_build"] = run_cmd(
        "G36_frontend_production_build", g36_cmd, FRONTEND, env, out_dir,
        timeout=300, source_sha=pre_sha,
    )
    print(f"G36: {gates['G36_frontend_production_build']['status']}")

    # G37: Frontend lint
    print("Running G37_frontend_lint...", flush=True)
    g37_cmd = ["npm", "run", "lint"]
    gates["G37_frontend_lint"] = run_cmd(
        "G37_frontend_lint", g37_cmd, FRONTEND, env, out_dir,
        timeout=180, source_sha=pre_sha,
    )
    print(f"G37: {gates['G37_frontend_lint']['status']}")

    # G38: Actual Playwright E2E browser acceptance
    print("Running G38_actual_playwright_e2e...", flush=True)
    g38_cmd = ["npx", "playwright", "test", "tests/e2e/v17_6_observation.e2e.ts"]
    gates["G38_actual_playwright_e2e"] = run_cmd(
        "G38_actual_playwright_e2e", g38_cmd, FRONTEND, env, out_dir,
        timeout=300, source_sha=pre_sha,
    )
    print(f"G38: {gates['G38_actual_playwright_e2e']['status']}")

    # G39: Runtime zero live orders verification
    print("Running G39_runtime_zero_live_orders...", flush=True)
    g39_junit = out_dir / "G39_runtime_zero_live_orders.xml"
    g39_cmd = [python, "-m", "pytest", "backend/tests/test_v17_6_4_3c_zero_live_orders_runtime.py", "-q", f"--junitxml={g39_junit}"]
    gates["G39_runtime_zero_live_orders"] = run_cmd(
        "G39_runtime_zero_live_orders", g39_cmd, ROOT, env, out_dir,
        junit_path=g39_junit, timeout=120, source_sha=pre_sha,
    )
    print(f"G39: {gates['G39_runtime_zero_live_orders']['status']}")

    # G40: Behavioral PAPER strategy parity
    print("Running G40_behavioral_paper_strategy_parity...", flush=True)
    g40_junit = out_dir / "G40_behavioral_paper_strategy_parity.xml"
    g40_cmd = [python, "-m", "pytest", "backend/tests/test_v17_6_4_2_paper_parity.py", "-q", f"--junitxml={g40_junit}"]
    gates["G40_behavioral_paper_strategy_parity"] = run_cmd(
        "G40_behavioral_paper_strategy_parity", g40_cmd, ROOT, env, out_dir,
        junit_path=g40_junit, timeout=120, source_sha=pre_sha,
    )
    print(f"G40: {gates['G40_behavioral_paper_strategy_parity']['status']}")

    # G41: Deterministic observation soak
    print("Running G41_deterministic_observation_soak...", flush=True)
    g41_junit = out_dir / "G41_deterministic_observation_soak.xml"
    g41_cmd = [python, "-m", "pytest", "backend/tests/test_v17_6_4_3b_health_truth.py", "-k", "test_eh01 or test_eh15", "-q", f"--junitxml={g41_junit}"]
    gates["G41_deterministic_observation_soak"] = run_cmd(
        "G41_deterministic_observation_soak", g41_cmd, ROOT, env, out_dir,
        junit_path=g41_junit, timeout=120, source_sha=pre_sha,
    )
    print(f"G41: {gates['G41_deterministic_observation_soak']['status']}")

    # G42: Acceptance negative tests
    print("Running G42_acceptance_negative_tests...", flush=True)
    g42_junit = out_dir / "G42_acceptance_negative_tests.xml"
    g42_cmd = [python, "-m", "pytest", "backend/tests/test_v17_6_4_3c_acceptance_integrity.py", "-q", f"--junitxml={g42_junit}"]
    gates["G42_acceptance_negative_tests"] = run_cmd(
        "G42_acceptance_negative_tests", g42_cmd, ROOT, env, out_dir,
        junit_path=g42_junit, timeout=120, source_sha=pre_sha,
    )
    print(f"G42: {gates['G42_acceptance_negative_tests']['status']}")

    # G43: Artifact checksum and SHA integrity
    print("Running G43_artifact_checksum_and_sha_integrity...", flush=True)
    missing_logs = []
    hashes_verified = True
    for g in MANDATORY_GATES:
        if g not in ["G00_clean_branch_and_source", "G43_artifact_checksum_and_sha_integrity", "G44_clean_source_after_acceptance", "G45_controlled_paper_operational_smoke"]:
            lf = out_dir / f"{g}.log"
            if not lf.exists():
                missing_logs.append(g)
            else:
                computed_hash = compute_sha256(lf)
                recorded_hash = gates.get(g, {}).get("evidence_hash")
                if recorded_hash and computed_hash != recorded_hash:
                    hashes_verified = False

    artifact_ok = (len(missing_logs) == 0) and hashes_verified
    gates["G43_artifact_checksum_and_sha_integrity"] = {
        "gate": "G43_artifact_checksum_and_sha_integrity",
        "status": "PASS" if artifact_ok else "FAIL",
        "reason": "OK" if artifact_ok else f"Missing logs: {missing_logs}, Hashes verified: {hashes_verified}",
        "missing_logs": missing_logs,
        "hashes_verified": hashes_verified,
        "duration": 0.0,
        "elapsed_sec": 0.0,
    }
    print(f"G43: {gates['G43_artifact_checksum_and_sha_integrity']['status']}")

    # G44: Clean source after acceptance
    print("Running G44_clean_source_after_acceptance...", flush=True)
    post_sha = git("rev-parse", "HEAD")
    post_porcelain = git("status", "--porcelain=v1", "--untracked-files=all")
    clean_after = len(post_porcelain.strip()) == 0
    sha_unchanged = (pre_sha == post_sha)

    g44_ok = (clean_after and sha_unchanged) if not is_dev_mode else sha_unchanged
    gates["G44_clean_source_after_acceptance"] = {
        "gate": "G44_clean_source_after_acceptance",
        "status": "PASS" if g44_ok else "FAIL",
        "reason": "OK" if g44_ok else f"Clean: {clean_after}, SHA match: {sha_unchanged}",
        "pre_sha": pre_sha,
        "post_sha": post_sha,
        "duration": 0.0,
        "elapsed_sec": 0.0,
    }
    print(f"G44: {gates['G44_clean_source_after_acceptance']['status']}")

    # G45: Controlled PAPER operational smoke verification
    print("Running G45_controlled_paper_operational_smoke...", flush=True)
    g45_junit = out_dir / "G45_controlled_paper_operational_smoke.xml"
    g45_cmd = [python, "-m", "pytest", "backend/tests/test_v17_6_4_3c_zero_live_orders_runtime.py", "-k", "test_lo01 or test_lo10", "-q", f"--junitxml={g45_junit}"]
    gates["G45_controlled_paper_operational_smoke"] = run_cmd(
        "G45_controlled_paper_operational_smoke", g45_cmd, ROOT, env, out_dir,
        junit_path=g45_junit, timeout=60, source_sha=pre_sha,
    )
    print(f"G45: {gates['G45_controlled_paper_operational_smoke']['status']}\n")

    # Final Decision via pure evaluation function
    source_integrity = {
        "branch": current_branch,
        "clean_before": clean_before,
        "clean_after": clean_after,
        "pre_sha": pre_sha,
        "post_sha": post_sha,
    }
    artifact_integrity = {
        "all_logs_present": (len(missing_logs) == 0),
        "missing_logs": missing_logs,
        "hashes_verified": hashes_verified,
    }

    decision = evaluate_release_acceptance(
        gate_results=gates,
        source_integrity=source_integrity,
        artifact_integrity=artifact_integrity,
        execution_mode="dev" if is_dev_mode else "release",
    )

    total_elapsed = round(time.monotonic() - start_time, 2)
    report = {
        "target_version": "V17.6.4.3c",
        "verdict": decision.verdict,
        "accepted": decision.accepted,
        "mode": "dev" if is_dev_mode else "release",
        "branch": current_branch,
        "source_sha": post_sha,
        "pre_sha": pre_sha,
        "post_sha": post_sha,
        "total_gates": decision.total_gates,
        "passed_gates": decision.passed_gates,
        "total_elapsed_sec": total_elapsed,
        "unmet_requirements": decision.unmet_requirements,
        "environment": {
            "os": platform.system(),
            "python": platform.python_version(),
            "node": subprocess.check_output(["node", "-v"]).strip().decode("utf-8"),
        },
        "gates": gates,
    }

    report_path = out_dir / "acceptance_v17_6_4_3c.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("=" * 60)
    print(f"FINAL RESULT: {decision.passed_gates}/{decision.total_gates} GATES PASSED in {total_elapsed}s")
    print(f"VERDICT: {decision.verdict}")
    print(f"ACCEPTANCE REPORT: {report_path}")
    print("=" * 60)

    return 0 if (decision.accepted or (is_dev_mode and decision.passed_gates == decision.total_gates)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
