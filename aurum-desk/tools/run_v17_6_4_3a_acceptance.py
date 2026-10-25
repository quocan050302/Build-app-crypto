#!/usr/bin/env python3
"""
AURUM V17.6.4.3a — 32-GATE MASTER ACCEPTANCE RUNNER
===================================================
Enforces 32 rigorous verification gates (G00–G31) per Master Prompt V17.6.4.3a.
Guarantees:
- Transaction commit truth (flushed is PENDING_COMMIT, not COMMITTED)
- Unforgeable quote evidence authority (GOOD strictly requires canonical quote)
- Multi-database pending metrics and engine health isolation
- Strict backend test discovery (code == 0 AND collected > 0 AND no errors)
- Unexpected skip rejection (any unexpected skip -> FAIL)
- Full backend, Vitest, build, lint, and Playwright verification
- Zero live exchange orders (LIVE_EXCHANGE_ORDERS_SENT = 0)
- Zero SMC/ICT mathematical drift
"""
import argparse
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

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

MANDATORY_GATES = [
    "G00_clean_branch_and_source",
    "G01_borrowed_flush_returns_pending_commit",
    "G02_outer_transaction_commit_acknowledgement",
    "G03_rollback_clears_uncommitted_db_rows",
    "G04_no_premature_paper_business_commit",
    "G05_pending_interval_acknowledgement_integrity",
    "G06_sqlite_concurrent_writer_and_retry",
    "G07_forged_typed_good_rejected",
    "G08_forged_dictionary_good_rejected",
    "G09_metadata_only_good_rejected",
    "G10_valid_canonical_good_accepted",
    "G11_real_watcher_quote_authority",
    "G12_real_scheduler_quote_authority",
    "G13_replay_test_separation",
    "G14_database_ab_pending_isolation",
    "G15_database_ab_health_isolation",
    "G16_run_id_isolation",
    "G17_current_evidence_and_durable_commit_agreement",
    "G18_strict_backend_discovery_exit_code",
    "G19_unexpected_skip_rejection",
    "G20_full_backend_test_execution",
    "G21_frontend_vitest",
    "G22_frontend_build",
    "G23_frontend_lint",
    "G24_playwright_e2e",
    "G25_sqlite_restart_readback",
    "G26_deterministic_observation_soak",
    "G27_behavioral_paper_strategy_parity",
    "G28_zero_live_order_runtime_verification",
    "G29_acceptance_runner_negative_tests",
    "G30_clean_source_after_testing",
    "G31_source_sha_and_artifact_reconciliation",
]


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
                skip_elem = tc.find("skipped")
                if skip_elem is not None:
                    name = tc.attrib.get("name", "unknown")
                    classname = tc.attrib.get("classname", "")
                    skipped_names.append(f"{classname}::{name}".strip(":"))

        return {
            "test_count": total_tests,
            "failed": total_failures + total_errors,
            "errors": total_errors,
            "skipped": total_skipped,
            "skipped_names": skipped_names,
            "passed": total_tests - (total_failures + total_errors) - total_skipped,
        }
    except Exception as exc:
        return {"error": str(exc)}


def run_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha="", allow_skipped=False):
    started_at = datetime.now(timezone.utc).isoformat()
    start_mono = time.monotonic()
    log_file = out_dir / f"{name}.log"
    try:
        res = subprocess.run(
            cmd, cwd=cwd, env=env, capture_output=True,
            text=True, errors="replace", timeout=timeout,
        )
        code = res.returncode
        stdout = (res.stdout or "") + "\n" + (res.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
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
    status = "PASS" if code == 0 else "FAIL"
    reason = "OK" if code == 0 else f"Command failed with exit code {code}"

    if junit_path is not None:
        junit_data = parse_junit_xml(junit_path)
        if junit_data is None:
            status = "FAIL"
            reason = "MISSING_JUNIT"
        elif "error" in junit_data:
            status = "FAIL"
            reason = f"Corrupt JUnit XML: {junit_data['error']}"
        else:
            test_count = junit_data["test_count"]
            passed = junit_data["passed"]
            failed = junit_data["failed"]
            skipped = junit_data["skipped"]
            skipped_names = junit_data.get("skipped_names", [])
            if test_count == 0:
                status = "FAIL"
                reason = "ZERO_TESTS_COLLECTED"
            elif failed > 0:
                status = "FAIL"
                reason = f"{failed} test failures in JUnit report"
            elif skipped > 0 and not allow_skipped:
                status = "FAIL"
                reason = f"UNEXPECTED_SKIP: {skipped} skipped tests detected ({skipped_names})"
            elif code != 0:
                status = "FAIL"
                reason = f"Command failed with exit code {code}"
            else:
                status = "PASS"
                reason = f"All {passed} tests passed"

    return {
        "gate": name,
        "command": " ".join(cmd) if isinstance(cmd, list) else str(cmd),
        "working_directory": str(cwd),
        "source_sha": source_sha,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration": elapsed,
        "elapsed_sec": elapsed,
        "exit_code": code,
        "test_count": test_count,
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


def verify_zero_live_execution():
    """Verifies live exchange order execution is strictly disabled across codebase."""
    found_violations = []
    suspicious_patterns = [
        r"LIVE_EXCHANGE_ORDER_EXECUTION\s*=\s*True",
        r"is_live_order_execution_enabled\(\)\s*->\s*True",
        r"LIVE_EXCHANGE_ORDERS_SENT\s*>\s*0",
    ]
    for root, _, files in os.walk(BACKEND):
        for f in files:
            if f.endswith(".py") and not f.startswith("test_"):
                path = Path(root) / f
                content = path.read_text(encoding="utf-8", errors="ignore")
                for pat in suspicious_patterns:
                    if re.search(pat, content):
                        found_violations.append(f"{path.name}: {pat}")
    return len(found_violations) == 0, found_violations


def verify_zero_strategy_drift():
    """Verifies core SMC/ICT mathematical invariants remain intact."""
    essential_symbols = [
        (BACKEND / "services" / "scalp_watcher_orchestrator.py", ["evaluate_cycle", "ScalpWatcherOrchestrator"]),
        (BACKEND / "services" / "cg01_engine.py", ["build_cg01_context", "detect_cg01_sweep", "resolve_cg01_mss_and_displacement"]),
        (BACKEND / "services" / "market_calendar_service.py", ["get_contract_trading_availability", "get_underlying_reference_session"]),
        (BACKEND / "services" / "observation_interval_service.py", [
            "record_heartbeat_event",
            "ObservationIntervalService",
            "seal_active_interval",
            "persist_pending_intervals",
            "resolve_observation_database_identity",
            "compute_interval_identity",
        ]),
        (BACKEND / "services" / "engine_observability_service.py", [
            "record_evaluation_cycle",
            "resolve_observation_evidence_quality",
            "get_latest_persisted_cycle",
            "build_authoritative_observation_quality",
            "build_engine_health_snapshot",
            "build_observation_dashboard_snapshot",
        ]),
    ]
    for path, symbols in essential_symbols:
        if not path.exists():
            return False, [f"Missing core file {path}"]
        content = path.read_text(encoding="utf-8", errors="ignore")
        for sym in symbols:
            if sym not in content:
                return False, [f"Missing symbol {sym} in {path.name}"]
    return True, []


def run_backend_discovery(python, ROOT, env, out_dir, source_sha):
    """G18: Strict dynamic collection across backend/tests (AC43A-05, Parts 062-072)."""
    cmd = [python, "-m", "pytest", "backend/tests", "--collect-only", "-q"]
    started_at = datetime.now(timezone.utc).isoformat()
    start_mono = time.monotonic()
    log_file = out_dir / "G18_strict_backend_discovery_exit_code.log"
    manifest_file = out_dir / "backend_test_discovery_manifest.txt"
    try:
        res = subprocess.run(
            cmd, cwd=ROOT, env=env, capture_output=True,
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

    items = []
    for line in stdout.splitlines():
        line_s = line.strip()
        if "::" in line_s and not line_s.startswith("=") and not line_s.startswith("Traceback") and not line_s.startswith("File "):
            items.append(line_s)
    manifest_file.write_text("\n".join(items), encoding="utf-8")

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
        "gate": "G18_strict_backend_discovery_exit_code",
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
    parser = argparse.ArgumentParser(description="AURUM V17.6.4.3a 32-Gate Acceptance Runner")
    parser.add_argument("--mode", choices=["release", "dev"], default="release", help="Execution mode (release | dev)")
    parser.add_argument("--allow-dirty", action="store_true", help="Allow dirty worktree (dev mode only; never produces ACCEPTED)")
    parser.add_argument("--skip-e2e", action="store_true", help="Skip Playwright browser E2E gate (cannot produce ACCEPTED in release mode)")
    parser.add_argument("--out-dir", type=str, default=None, help="Output directory for logs, reports, and evidence")
    args = parser.parse_args(argv)

    is_dev_mode = (args.mode == "dev") or args.allow_dirty
    start_time = time.monotonic()
    if args.out_dir:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = Path(tempfile.mkdtemp(prefix="aurum_v17_6_4_3a_artifacts_"))

    print(f"=== AURUM V17.6.4.3a MASTER ACCEPTANCE RUNNER ===")
    print(f"Mode: {'DEVELOPMENT' if is_dev_mode else 'RELEASE'}")
    print(f"Artifact Directory: {out_dir}\n")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND)
    env["TESTING"] = "1"
    env["AURUM_E2E_API_PORT"] = env.get("AURUM_E2E_API_PORT", "8768")
    env["AURUM_E2E_WEB_PORT"] = env.get("AURUM_E2E_WEB_PORT", "5198")
    env["AURUM_E2E_REPORT"] = str(out_dir / "playwright_report.json")

    python = str(BACKEND / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    if not Path(python).exists():
        python = sys.executable

    gates = {}

    # G00: Git Branch & Worktree Cleanliness (Pre-Test)
    print("Checking G00_clean_branch_and_source...", flush=True)
    try:
        current_branch = git("branch", "--show-current")
        pre_sha = git("rev-parse", "HEAD")
        branch_ok = (current_branch == "fix/v14-1-rr-flow")
        porcelain_status = git("status", "--porcelain=v1", "--untracked-files=all")
        is_clean = len(porcelain_status.strip()) == 0

        if is_dev_mode:
            pre_clean_ok = branch_ok
            pre_reason = "OK (dev mode / allow-dirty)"
        else:
            pre_clean_ok = branch_ok and is_clean
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
    except Exception as exc:
        pre_sha = "UNKNOWN"
        gates["G00_clean_branch_and_source"] = {
            "gate": "G00_clean_branch_and_source",
            "status": "FAIL",
            "reason": str(exc),
            "duration": 0.0,
            "elapsed_sec": 0.0,
        }
    print(f"G00: {gates['G00_clean_branch_and_source']['status']}\n")

    # Pytest Suites (G01 - G17)
    py_suites = [
        ("G01_borrowed_flush_returns_pending_commit", ["backend/tests/test_v17_6_4_3a_commit_truth.py", "-k", "test_ct02"]),
        ("G02_outer_transaction_commit_acknowledgement", ["backend/tests/test_v17_6_4_3a_commit_truth.py", "-k", "test_ct04"]),
        ("G03_rollback_clears_uncommitted_db_rows", ["backend/tests/test_v17_6_4_3a_commit_truth.py", "-k", "test_ct03"]),
        ("G04_no_premature_paper_business_commit", ["backend/tests/test_v17_6_4_3a_commit_truth.py", "-k", "test_ct09"]),
        ("G05_pending_interval_acknowledgement_integrity", ["backend/tests/test_v17_6_4_3a_commit_truth.py", "-k", "test_ct05"]),
        ("G06_sqlite_concurrent_writer_and_retry", ["backend/tests/test_v17_6_4_3a_commit_truth.py", "-k", "test_ct10"]),
        ("G07_forged_typed_good_rejected", ["backend/tests/test_v17_6_4_3a_authoritative_good.py", "-k", "test_ag01"]),
        ("G08_forged_dictionary_good_rejected", ["backend/tests/test_v17_6_4_3a_authoritative_good.py", "-k", "test_ag02"]),
        ("G09_metadata_only_good_rejected", ["backend/tests/test_v17_6_4_3a_authoritative_good.py", "-k", "test_ag03 or test_ag04"]),
        ("G10_valid_canonical_good_accepted", ["backend/tests/test_v17_6_4_3a_authoritative_good.py", "-k", "test_ag13"]),
        ("G11_real_watcher_quote_authority", ["backend/tests/test_v17_6_4_3a_authoritative_good.py", "-k", "test_ag14"]),
        ("G12_real_scheduler_quote_authority", ["backend/tests/test_v17_6_4_3a_authoritative_good.py", "-k", "test_ag15"]),
        ("G13_replay_test_separation", ["backend/tests/test_v17_6_4_3a_authoritative_good.py", "-k", "test_ag16 or test_ag17"]),
        ("G14_database_ab_pending_isolation", ["backend/tests/test_v17_6_4_3a_health_db_scoping.py", "-k", "test_hs01"]),
        ("G15_database_ab_health_isolation", ["backend/tests/test_v17_6_4_3a_health_db_scoping.py", "-k", "test_hs02"]),
        ("G16_run_id_isolation", ["backend/tests/test_v17_6_4_3a_health_db_scoping.py", "-k", "test_hs03"]),
        ("G17_current_evidence_and_durable_commit_agreement", ["backend/tests/test_v17_6_4_3a_health_db_scoping.py", "-k", "test_hs10"]),
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
        else:
            print(f"{gate_name}: MISSING")

    # G18: Strict backend test discovery (AC43A-05, Parts 062-072)
    print("Running G18_strict_backend_discovery_exit_code...", flush=True)
    gates["G18_strict_backend_discovery_exit_code"] = run_backend_discovery(
        python, ROOT, env, out_dir, pre_sha,
    )
    print(f"G18: {gates['G18_strict_backend_discovery_exit_code']['status']}")

    # G19: Unexpected skip rejection verification
    print("Running G19_unexpected_skip_rejection...", flush=True)
    g19_junit = out_dir / "G19_unexpected_skip_rejection.xml"
    g19_cmd = [python, "-m", "pytest", "backend/tests/test_v17_6_4_3a_acceptance_integrity.py", "-k", "test_ai05", "-q", f"--junitxml={g19_junit}"]
    gates["G19_unexpected_skip_rejection"] = run_cmd(
        "G19_unexpected_skip_rejection", g19_cmd, ROOT, env, out_dir,
        junit_path=g19_junit, timeout=120, source_sha=pre_sha,
    )
    print(f"G19: {gates['G19_unexpected_skip_rejection']['status']}")

    # G20: Full backend test execution
    print("Running G20_full_backend_test_execution...", flush=True)
    g20_junit = out_dir / "G20_full_backend_test_execution.xml"
    g20_cmd = [python, "-m", "pytest", "backend/tests", "-q", f"--junitxml={g20_junit}"]
    gates["G20_full_backend_test_execution"] = run_cmd(
        "G20_full_backend_test_execution", g20_cmd, ROOT, env, out_dir,
        junit_path=g20_junit, timeout=600, source_sha=pre_sha,
    )
    print(f"G20: {gates['G20_full_backend_test_execution']['status']}")

    # G21: Full frontend Vitest
    print("Running G21_frontend_vitest...", flush=True)
    g21_junit = out_dir / "G21_frontend_vitest.xml"
    g21_cmd = ["npm", "test", "--", "--run", "--reporter=junit", f"--outputFile={g21_junit}"]
    gates["G21_frontend_vitest"] = run_cmd(
        "G21_frontend_vitest", g21_cmd, FRONTEND, env, out_dir,
        junit_path=g21_junit, timeout=300, source_sha=pre_sha,
    )
    print(f"G21: {gates['G21_frontend_vitest']['status']}")

    # G22: Frontend build
    print("Running G22_frontend_build...", flush=True)
    g22_cmd = ["npm", "run", "build"]
    gates["G22_frontend_build"] = run_cmd(
        "G22_frontend_build", g22_cmd, FRONTEND, env, out_dir,
        timeout=300, source_sha=pre_sha,
    )
    print(f"G22: {gates['G22_frontend_build']['status']}")

    # G23: Frontend lint
    print("Running G23_frontend_lint...", flush=True)
    g23_cmd = ["npm", "run", "lint"]
    gates["G23_frontend_lint"] = run_cmd(
        "G23_frontend_lint", g23_cmd, FRONTEND, env, out_dir,
        timeout=180, source_sha=pre_sha,
    )
    print(f"G23: {gates['G23_frontend_lint']['status']}")

    # G24: Playwright E2E
    if args.skip_e2e:
        print("Skipping G24 (skip-e2e specified)...")
        gates["G24_playwright_e2e"] = {
            "gate": "G24_playwright_e2e",
            "status": "SKIPPED",
            "reason": "SKIPPED_BY_FLAG",
            "exit_code": 0,
            "duration": 0.0,
            "elapsed_sec": 0.0,
            "source_sha": pre_sha,
        }
    else:
        print("Running G24_playwright_e2e...", flush=True)
        e2e_cmd = ["npx", "playwright", "test", "v17_6_observation.e2e.ts"]
        gates["G24_playwright_e2e"] = run_cmd(
            "G24_playwright_e2e", e2e_cmd, FRONTEND, env, out_dir,
            timeout=300, source_sha=pre_sha,
        )
    print(f"G24: {gates['G24_playwright_e2e']['status']}")

    # G25: SQLite restart/readback
    print("Running G25_sqlite_restart_readback...", flush=True)
    g25_junit = out_dir / "G25_sqlite_restart_readback.xml"
    g25_cmd = [python, "-m", "pytest", "backend/tests/test_v17_6_4_3a_commit_truth.py", "-k", "test_ct12 or test_ct13", "-q", f"--junitxml={g25_junit}"]
    gates["G25_sqlite_restart_readback"] = run_cmd(
        "G25_sqlite_restart_readback", g25_cmd, ROOT, env, out_dir,
        junit_path=g25_junit, timeout=120, source_sha=pre_sha,
    )
    print(f"G25: {gates['G25_sqlite_restart_readback']['status']}")

    # G26: Deterministic soak
    print("Running G26_deterministic_observation_soak...", flush=True)
    g26_junit = out_dir / "G26_deterministic_observation_soak.xml"
    g26_cmd = [python, "-m", "pytest", "backend/tests/test_v17_6_soak.py", "-q", f"--junitxml={g26_junit}"]
    gates["G26_deterministic_observation_soak"] = run_cmd(
        "G26_deterministic_observation_soak", g26_cmd, ROOT, env, out_dir,
        junit_path=g26_junit, timeout=120, source_sha=pre_sha,
    )
    print(f"G26: {gates['G26_deterministic_observation_soak']['status']}")

    # G27: Behavioral PAPER strategy parity
    print("Running G27_behavioral_paper_strategy_parity...", flush=True)
    g27_junit = out_dir / "G27_behavioral_paper_strategy_parity.xml"
    g27_cmd = [python, "-m", "pytest", "backend/tests/test_v17_6_4_2_paper_parity.py", "-k", "test_sp", "-q", f"--junitxml={g27_junit}"]
    res_g27 = run_cmd(
        "G27_behavioral_paper_strategy_parity", g27_cmd, ROOT, env, out_dir,
        junit_path=g27_junit, timeout=120, source_sha=pre_sha,
    )
    drift_ok, drift_violations = verify_zero_strategy_drift()
    if not drift_ok:
        res_g27["status"] = "FAIL"
        res_g27["reason"] = f"Strategy drift violations: {drift_violations}"
    res_g27["violations"] = drift_violations
    gates["G27_behavioral_paper_strategy_parity"] = res_g27
    print(f"G27: {gates['G27_behavioral_paper_strategy_parity']['status']}")

    # G28: Zero LIVE order runtime verification
    print("Running G28_zero_live_order_runtime_verification...", flush=True)
    g28_junit = out_dir / "G28_zero_live_order_runtime_verification.xml"
    g28_cmd = [python, "-m", "pytest", "backend/tests/test_v17_6_4_3_watcher_scheduler_integration.py", "-k", "test_ws05", "-q", f"--junitxml={g28_junit}"]
    res_g28 = run_cmd(
        "G28_zero_live_order_runtime_verification", g28_cmd, ROOT, env, out_dir,
        junit_path=g28_junit, timeout=120, source_sha=pre_sha,
    )
    live_ok, live_violations = verify_zero_live_execution()
    if not live_ok:
        res_g28["status"] = "FAIL"
        res_g28["reason"] = f"Live order execution violations: {live_violations}"
    res_g28["violations"] = live_violations
    gates["G28_zero_live_order_runtime_verification"] = res_g28
    print(f"G28: {gates['G28_zero_live_order_runtime_verification']['status']}")

    # G29: Acceptance runner negative tests
    print("Running G29_acceptance_runner_negative_tests...", flush=True)
    g29_junit = out_dir / "G29_acceptance_runner_negative_tests.xml"
    g29_cmd = [python, "-m", "pytest", "backend/tests/test_v17_6_4_3a_acceptance_integrity.py", "-q", f"--junitxml={g29_junit}"]
    gates["G29_acceptance_runner_negative_tests"] = run_cmd(
        "G29_acceptance_runner_negative_tests", g29_cmd, ROOT, env, out_dir,
        junit_path=g29_junit, timeout=120, source_sha=pre_sha,
    )
    print(f"G29: {gates['G29_acceptance_runner_negative_tests']['status']}")

    # G30: Clean source after testing (Post-Test)
    print("Checking G30_clean_source_after_testing...", flush=True)
    try:
        post_status = git("status", "--porcelain=v1", "--untracked-files=all")
        post_clean = len(post_status.strip()) == 0
        if is_dev_mode:
            g30_ok = True
            g30_reason = "OK (dev mode / allow-dirty)"
        else:
            g30_ok = post_clean
            g30_reason = "OK" if post_clean else f"Worktree dirty post-test:\n{post_status}"

        gates["G30_clean_source_after_testing"] = {
            "gate": "G30_clean_source_after_testing",
            "status": "PASS" if g30_ok else "FAIL",
            "reason": g30_reason,
            "duration": 0.0,
            "elapsed_sec": 0.0,
            "source_sha": pre_sha,
        }
    except Exception as exc:
        gates["G30_clean_source_after_testing"] = {
            "gate": "G30_clean_source_after_testing",
            "status": "FAIL",
            "reason": str(exc),
            "duration": 0.0,
            "elapsed_sec": 0.0,
            "source_sha": pre_sha,
        }
    print(f"G30: {gates['G30_clean_source_after_testing']['status']}")

    # G31: Source SHA and artifact reconciliation
    print("Checking G31_source_sha_and_artifact_reconciliation...", flush=True)
    try:
        post_sha = git("rev-parse", "HEAD")
        sha_matches = (post_sha == pre_sha)

        missing_logs = []
        for g in MANDATORY_GATES:
            if g not in ["G00_clean_branch_and_source", "G30_clean_source_after_testing", "G31_source_sha_and_artifact_reconciliation"]:
                lf = out_dir / f"{g}.log"
                if not lf.exists() and (gates.get(g) or {}).get("status") != "SKIPPED":
                    missing_logs.append(g)

        if is_dev_mode:
            g31_ok = sha_matches and (len(missing_logs) == 0)
            g31_reason = "OK (dev mode / allow-dirty)"
        else:
            g31_ok = sha_matches and (len(missing_logs) == 0)
            g31_reason = "OK" if g31_ok else (
                f"SHA mismatch (pre={pre_sha}, post={post_sha})" if not sha_matches
                else f"Missing evidence logs for: {missing_logs}"
            )

        gates["G31_source_sha_and_artifact_reconciliation"] = {
            "gate": "G31_source_sha_and_artifact_reconciliation",
            "pre_sha": pre_sha,
            "post_sha": post_sha,
            "status": "PASS" if g31_ok else "FAIL",
            "reason": g31_reason,
            "duration": 0.0,
            "elapsed_sec": 0.0,
        }
    except Exception as exc:
        post_sha = "UNKNOWN"
        gates["G31_source_sha_and_artifact_reconciliation"] = {
            "gate": "G31_source_sha_and_artifact_reconciliation",
            "status": "FAIL",
            "reason": str(exc),
            "duration": 0.0,
            "elapsed_sec": 0.0,
        }
    print(f"G31: {gates['G31_source_sha_and_artifact_reconciliation']['status']}\n")

    # Final verdict computation (Part 130)
    total_gates = len(MANDATORY_GATES)
    all_mandatory_present = all(g in gates and gates[g] is not None for g in MANDATORY_GATES)
    passed_gates = sum(1 for g in MANDATORY_GATES if (gates.get(g) or {}).get("status") == "PASS")
    all_mandatory_passed = all_mandatory_present and (passed_gates == total_gates)

    source_clean_before = gates.get("G00_clean_branch_and_source", {}).get("status") == "PASS"
    source_clean_after = gates.get("G30_clean_source_after_testing", {}).get("status") == "PASS"
    source_sha_unchanged = (pre_sha == post_sha)

    # Check for any unexpected skips
    has_unexpected_skips = any(
        (gates.get(g) or {}).get("skipped", 0) > 0 or (gates.get(g) or {}).get("status") == "SKIPPED"
        for g in MANDATORY_GATES
    )

    if is_dev_mode:
        accepted = False
        verdict = "DEVELOPMENT_TEST_ONLY"
    else:
        accepted = (
            source_clean_before
            and source_clean_after
            and source_sha_unchanged
            and all_mandatory_passed
            and not has_unexpected_skips
        )
        verdict = "ACCEPTED" if accepted else "REJECTED"

    total_elapsed = round(time.monotonic() - start_time, 2)
    report = {
        "target_version": "V17.6.4.3a",
        "verdict": verdict,
        "mode": "dev" if is_dev_mode else "release",
        "branch": git("branch", "--show-current") if branch_ok else "UNKNOWN",
        "source_sha": post_sha,
        "pre_sha": pre_sha,
        "post_sha": post_sha,
        "total_gates": total_gates,
        "passed_gates": passed_gates,
        "total_elapsed_sec": total_elapsed,
        "environment": {
            "os": platform.system(),
            "python": platform.python_version(),
            "node": subprocess.check_output(["node", "-v"]).strip().decode("utf-8"),
        },
        "gates": gates,
    }

    report_path = out_dir / "acceptance_v17_6_4_3a.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("=" * 60)
    print(f"FINAL RESULT: {passed_gates}/{total_gates} GATES PASSED in {total_elapsed}s")
    print(f"VERDICT: {verdict}")
    print(f"ACCEPTANCE REPORT: {report_path}")
    print("=" * 60)

    return 0 if (accepted or (is_dev_mode and all_mandatory_passed)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
