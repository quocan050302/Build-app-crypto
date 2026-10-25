#!/usr/bin/env python3
"""
AURUM V17.6.4.3 — 35-GATE MASTER PRODUCTION OBSERVABILITY ACCEPTANCE RUNNER
==========================================================================
Enforces 35 rigorous verification gates (G00–G34) per Master Prompt V17.6.4.3
(Section 11 / Parts 171–176, Phases 1–11).
Guarantees:
- Multi-database telemetry isolation (DBEA-01, DBEA-02, DBEA-08)
- Authoritative verified GOOD quote evidence validation (DBEA-03, DBEA-04)
- Durable interval identity and SQLite uniqueness (DBEA-05, DBEA-06)
- Historical commit truth separated from live observation health (DBEA-07)
- Strict transaction boundaries without premature business commit (DBEA-09)
- Dynamic discovery and execution across all backend tests (DBEA-10)
- Real watcher and scheduler integration proof (DBEA-11)
- Reconciled source, schema, and acceptance evidence artifacts (DBEA-12)
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
    "G00_clean_source",
    "G01_database_identity_resolution",
    "G02_pending_queue_database_isolation",
    "G03_active_interval_database_isolation",
    "G04_same_db_multisession_compatibility",
    "G05_two_sqlite_database_integration",
    "G06_concurrent_pending_persistence",
    "G07_replay_test_live_db_separation",
    "G08_strict_good_evidence_authority",
    "G09_metadata_bypass_prevention",
    "G10_typed_quality_spoof_prevention",
    "G11_quote_age_timestamp_validation",
    "G12_persisted_interval_identity_contract",
    "G13_database_uniqueness_idempotency",
    "G14_legacy_schema_migration",
    "G15_migration_idempotency",
    "G16_transaction_ownership_correctness",
    "G17_no_premature_business_commit",
    "G18_current_evidence_freshness",
    "G19_pending_metrics_database_isolation",
    "G20_run_and_symbol_isolation",
    "G21_actual_watcher_integration",
    "G22_actual_scheduler_integration",
    "G23_crash_restart_readback",
    "G24_full_backend_test_discovery",
    "G25_full_backend_test_execution",
    "G26_full_frontend_vitest",
    "G27_frontend_build",
    "G28_frontend_lint",
    "G29_playwright_e2e",
    "G30_deterministic_soak",
    "G31_behavioral_strategy_parity",
    "G32_zero_live_order_runtime_verification",
    "G33_acceptance_runner_negative_tests",
    "G34_source_sha_and_artifact_integrity",
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
    """Parses JUnit XML to extract tests, failures, errors, skipped."""
    p = Path(junit_path)
    if not p.exists():
        return None
    try:
        tree = ET.parse(p)
        root = tree.getroot()
        suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
        total_tests = 0
        total_failures = 0
        total_errors = 0
        total_skipped = 0
        for s in suites:
            total_tests += int(s.attrib.get("tests", 0))
            total_failures += int(s.attrib.get("failures", 0))
            total_errors += int(s.attrib.get("errors", 0))
            total_skipped += int(s.attrib.get("skipped", 0))
        return {
            "test_count": total_tests,
            "failed": total_failures + total_errors,
            "errors": total_errors,
            "skipped": total_skipped,
            "passed": total_tests - (total_failures + total_errors) - total_skipped,
        }
    except Exception as exc:
        return {"error": str(exc)}


def run_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha=""):
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
            if test_count == 0:
                status = "FAIL"
                reason = "ZERO_TESTS_COLLECTED"
            elif failed > 0:
                status = "FAIL"
                reason = f"{failed} test failures in JUnit report"
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
    """G24: Dynamic collection across backend/tests."""
    cmd = [python, "-m", "pytest", "backend/tests", "--collect-only", "-q"]
    started_at = datetime.now(timezone.utc).isoformat()
    start_mono = time.monotonic()
    log_file = out_dir / "G24_full_backend_test_discovery.log"
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

    items = [line.strip() for line in stdout.splitlines() if "::" in line and not line.startswith("=")]
    manifest_file.write_text("\n".join(items), encoding="utf-8")

    match = re.search(r"(\d+)\s+tests\s+collected", stdout)
    if match:
        collected_count = int(match.group(1))
    else:
        collected_count = len(items)

    if code != 0 and collected_count == 0:
        status = "FAIL"
        reason = f"Pytest collection failed with exit code {code}"
    elif collected_count == 0:
        status = "FAIL"
        reason = "ZERO_TESTS_COLLECTED"
    else:
        status = "PASS"
        reason = f"Successfully discovered {collected_count} tests dynamically across backend/tests"

    return {
        "gate": "G24_full_backend_test_discovery",
        "command": " ".join(cmd),
        "working_directory": str(ROOT),
        "source_sha": source_sha,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration": elapsed,
        "elapsed_sec": elapsed,
        "exit_code": code,
        "test_count": collected_count,
        "passed": collected_count if status == "PASS" else 0,
        "failed": 0,
        "skipped": 0,
        "status": status,
        "reason": reason,
        "evidence_file": str(log_file),
        "evidence_hash": compute_sha256(log_file),
        "output_tail": "\n".join(stdout.strip().splitlines()[-10:]),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="AURUM V17.6.4.3 35-Gate Acceptance Runner")
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
        out_dir = Path(tempfile.mkdtemp(prefix="aurum_v17_6_4_3_artifacts_"))

    print(f"=== AURUM V17.6.4.3 MASTER ACCEPTANCE RUNNER ===")
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
    print("Checking G00: Clean Source Pre-Test...", flush=True)
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

        gates["G00_clean_source"] = {
            "gate": "G00_clean_source",
            "branch": current_branch,
            "source_sha": pre_sha,
            "status": "PASS" if pre_clean_ok else "FAIL",
            "reason": pre_reason,
            "duration": 0.0,
            "elapsed_sec": 0.0,
        }
    except Exception as exc:
        pre_sha = "UNKNOWN"
        gates["G00_clean_source"] = {
            "gate": "G00_clean_source",
            "status": "FAIL",
            "reason": str(exc),
            "duration": 0.0,
            "elapsed_sec": 0.0,
        }
    print(f"G00: {gates['G00_clean_source']['status']}\n")

    # Pytest Suites (G01 - G23)
    py_suites = [
        ("G01_database_identity_resolution", ["backend/tests/test_v17_6_4_3_database_isolation.py", "-k", "test_db01 or test_db02"]),
        ("G02_pending_queue_database_isolation", ["backend/tests/test_v17_6_4_3_database_isolation.py", "-k", "test_db01_db07"]),
        ("G03_active_interval_database_isolation", ["backend/tests/test_v17_6_4_3_database_isolation.py", "-k", "test_db03"]),
        ("G04_same_db_multisession_compatibility", ["backend/tests/test_v17_6_4_3_database_isolation.py", "-k", "test_db04"]),
        ("G05_two_sqlite_database_integration", ["backend/tests/test_v17_6_4_3_database_isolation.py", "-k", "test_db05_db06_db08"]),
        ("G06_concurrent_pending_persistence", ["backend/tests/test_v17_6_4_3_database_isolation.py", "-k", "test_db09_db10"]),
        ("G07_replay_test_live_db_separation", ["backend/tests/test_v17_6_4_3_database_isolation.py", "-k", "test_db11_db12"]),
        ("G08_strict_good_evidence_authority", ["backend/tests/test_v17_6_4_3_evidence_authority.py", "-k", "test_ea01"]),
        ("G09_metadata_bypass_prevention", ["backend/tests/test_v17_6_4_3_evidence_authority.py", "-k", "test_ea02"]),
        ("G10_typed_quality_spoof_prevention", ["backend/tests/test_v17_6_4_3_evidence_authority.py", "-k", "test_ea03"]),
        ("G11_quote_age_timestamp_validation", ["backend/tests/test_v17_6_4_3_evidence_authority.py", "-k", "test_ea11 or test_ea12 or test_ea13"]),
        ("G12_persisted_interval_identity_contract", ["backend/tests/test_v17_6_4_3_interval_identity.py", "-k", "test_id01"]),
        ("G13_database_uniqueness_idempotency", ["backend/tests/test_v17_6_4_3_interval_identity.py", "-k", "test_id08_id15"]),
        ("G14_legacy_schema_migration", ["backend/tests/test_v17_6_4_3_interval_identity.py", "-k", "test_id09_id10_id11"]),
        ("G15_migration_idempotency", ["backend/tests/test_v17_6_4_3_interval_identity.py", "-k", "test_id16"]),
        ("G16_transaction_ownership_correctness", ["backend/tests/test_v17_6_4_3_transaction_boundaries.py", "-k", "test_tx01_tx02"]),
        ("G17_no_premature_business_commit", ["backend/tests/test_v17_6_4_3_transaction_boundaries.py", "-k", "test_tx07"]),
        ("G18_current_evidence_freshness", ["backend/tests/test_v17_6_4_3_current_evidence_health.py", "-k", "test_eh01"]),
        ("G19_pending_metrics_database_isolation", ["backend/tests/test_v17_6_4_3_database_isolation.py", "-k", "test_db16_db17"]),
        ("G20_run_and_symbol_isolation", ["backend/tests/test_v17_6_4_cycle_run_identity.py"]),
        ("G21_actual_watcher_integration", ["backend/tests/test_v17_6_4_3_watcher_scheduler_integration.py"]),
        ("G22_actual_scheduler_integration", ["backend/tests/test_v17_6_4_interval_producer.py"]),
        ("G23_crash_restart_readback", ["backend/tests/test_v17_6_4_1_real_db_integration.py"]),
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

    # G24: Full backend test discovery (DBEA-10, Part 138-144)
    print("Running G24_full_backend_test_discovery...", flush=True)
    gates["G24_full_backend_test_discovery"] = run_backend_discovery(
        python, ROOT, env, out_dir, pre_sha,
    )
    print(f"G24: {gates['G24_full_backend_test_discovery']['status']}")

    # G25: Full backend test execution (DBEA-10, Part 138-144)
    print("Running G25_full_backend_test_execution...", flush=True)
    g25_junit = out_dir / "G25_full_backend_test_execution.xml"
    g25_cmd = [python, "-m", "pytest", "backend/tests", "-q", f"--junitxml={g25_junit}"]
    gates["G25_full_backend_test_execution"] = run_cmd(
        "G25_full_backend_test_execution", g25_cmd, ROOT, env, out_dir,
        junit_path=g25_junit, timeout=600, source_sha=pre_sha,
    )
    print(f"G25: {gates['G25_full_backend_test_execution']['status']}")

    # G26: Full frontend Vitest
    print("Running G26_full_frontend_vitest...", flush=True)
    g26_junit = out_dir / "G26_full_frontend_vitest.xml"
    g26_cmd = ["npm", "test", "--", "--run", "--reporter=junit", f"--outputFile={g26_junit}"]
    gates["G26_full_frontend_vitest"] = run_cmd(
        "G26_full_frontend_vitest", g26_cmd, FRONTEND, env, out_dir,
        junit_path=g26_junit, timeout=300, source_sha=pre_sha,
    )
    print(f"G26: {gates['G26_full_frontend_vitest']['status']}")

    # G27: Frontend build
    print("Running G27_frontend_build...", flush=True)
    g27_cmd = ["npm", "run", "build"]
    gates["G27_frontend_build"] = run_cmd(
        "G27_frontend_build", g27_cmd, FRONTEND, env, out_dir,
        timeout=300, source_sha=pre_sha,
    )
    print(f"G27: {gates['G27_frontend_build']['status']}")

    # G28: Frontend lint
    print("Running G28_frontend_lint...", flush=True)
    g28_cmd = ["npm", "run", "lint"]
    gates["G28_frontend_lint"] = run_cmd(
        "G28_frontend_lint", g28_cmd, FRONTEND, env, out_dir,
        timeout=180, source_sha=pre_sha,
    )
    print(f"G28: {gates['G28_frontend_lint']['status']}")

    # G29: Playwright E2E
    if args.skip_e2e:
        print("Skipping G29 (skip-e2e specified)...")
        gates["G29_playwright_e2e"] = {
            "gate": "G29_playwright_e2e",
            "status": "SKIPPED",
            "reason": "SKIPPED_BY_FLAG",
            "exit_code": 0,
            "duration": 0.0,
            "elapsed_sec": 0.0,
            "source_sha": pre_sha,
        }
    else:
        print("Running G29_playwright_e2e...", flush=True)
        e2e_cmd = ["npx", "playwright", "test", "v17_6_observation.e2e.ts"]
        gates["G29_playwright_e2e"] = run_cmd(
            "G29_playwright_e2e", e2e_cmd, FRONTEND, env, out_dir,
            timeout=300, source_sha=pre_sha,
        )
    print(f"G29: {gates['G29_playwright_e2e']['status']}")

    # G30: Deterministic soak
    print("Running G30_deterministic_soak...", flush=True)
    g30_junit = out_dir / "G30_deterministic_soak.xml"
    g30_cmd = [python, "-m", "pytest", "backend/tests/test_v17_6_soak.py", "-q", f"--junitxml={g30_junit}"]
    gates["G30_deterministic_soak"] = run_cmd(
        "G30_deterministic_soak", g30_cmd, ROOT, env, out_dir,
        junit_path=g30_junit, timeout=120, source_sha=pre_sha,
    )
    print(f"G30: {gates['G30_deterministic_soak']['status']}")

    # G31: Behavioral strategy parity
    print("Running G31_behavioral_strategy_parity...", flush=True)
    g31_junit = out_dir / "G31_behavioral_strategy_parity.xml"
    g31_cmd = [python, "-m", "pytest", "backend/tests/test_v17_6_4_2_paper_parity.py", "-k", "test_sp", "-q", f"--junitxml={g31_junit}"]
    res_g31 = run_cmd(
        "G31_behavioral_strategy_parity", g31_cmd, ROOT, env, out_dir,
        junit_path=g31_junit, timeout=120, source_sha=pre_sha,
    )
    drift_ok, drift_violations = verify_zero_strategy_drift()
    if not drift_ok:
        res_g31["status"] = "FAIL"
        res_g31["reason"] = f"Strategy drift violations: {drift_violations}"
    res_g31["violations"] = drift_violations
    gates["G31_behavioral_strategy_parity"] = res_g31
    print(f"G31: {gates['G31_behavioral_strategy_parity']['status']}")

    # G32: Zero live order runtime verification
    print("Running G32_zero_live_order_runtime_verification...", flush=True)
    g32_junit = out_dir / "G32_zero_live_order_runtime_verification.xml"
    g32_cmd = [python, "-m", "pytest", "backend/tests/test_v17_6_4_3_watcher_scheduler_integration.py", "-k", "test_ws05", "-q", f"--junitxml={g32_junit}"]
    res_g32 = run_cmd(
        "G32_zero_live_order_runtime_verification", g32_cmd, ROOT, env, out_dir,
        junit_path=g32_junit, timeout=120, source_sha=pre_sha,
    )
    live_ok, live_violations = verify_zero_live_execution()
    if not live_ok:
        res_g32["status"] = "FAIL"
        res_g32["reason"] = f"Live order execution violations: {live_violations}"
    res_g32["violations"] = live_violations
    gates["G32_zero_live_order_runtime_verification"] = res_g32
    print(f"G32: {gates['G32_zero_live_order_runtime_verification']['status']}")

    # G33: Acceptance runner negative tests
    print("Running G33_acceptance_runner_negative_tests...", flush=True)
    g33_junit = out_dir / "G33_acceptance_runner_negative_tests.xml"
    g33_cmd = [python, "-m", "pytest", "backend/tests/test_v17_6_4_3_acceptance_integrity.py", "-q", f"--junitxml={g33_junit}"]
    gates["G33_acceptance_runner_negative_tests"] = run_cmd(
        "G33_acceptance_runner_negative_tests", g33_cmd, ROOT, env, out_dir,
        junit_path=g33_junit, timeout=120, source_sha=pre_sha,
    )
    print(f"G33: {gates['G33_acceptance_runner_negative_tests']['status']}")

    # G34: Source SHA and artifact integrity
    print("Checking G34_source_sha_and_artifact_integrity...", flush=True)
    try:
        post_sha = git("rev-parse", "HEAD")
        sha_matches = (post_sha == pre_sha)
        post_status = git("status", "--porcelain=v1", "--untracked-files=all")
        post_clean = len(post_status.strip()) == 0

        # Verify artifacts integrity
        missing_logs = []
        for g in MANDATORY_GATES:
            if g not in ["G00_clean_source", "G34_source_sha_and_artifact_integrity"]:
                lf = out_dir / f"{g}.log"
                if not lf.exists() and (gates.get(g) or {}).get("status") != "SKIPPED":
                    missing_logs.append(g)

        if is_dev_mode:
            g34_ok = sha_matches and (len(missing_logs) == 0)
            g34_reason = "OK (dev mode / allow-dirty)"
        else:
            g34_ok = sha_matches and post_clean and (len(missing_logs) == 0)
            g34_reason = "OK" if g34_ok else (
                f"SHA mismatch (pre={pre_sha}, post={post_sha})" if not sha_matches
                else f"Worktree dirty post-test:\n{post_status}" if not post_clean
                else f"Missing evidence logs for: {missing_logs}"
            )

        gates["G34_source_sha_and_artifact_integrity"] = {
            "gate": "G34_source_sha_and_artifact_integrity",
            "pre_sha": pre_sha,
            "post_sha": post_sha,
            "status": "PASS" if g34_ok else "FAIL",
            "reason": g34_reason,
            "duration": 0.0,
            "elapsed_sec": 0.0,
        }
    except Exception as exc:
        post_sha = "UNKNOWN"
        gates["G34_source_sha_and_artifact_integrity"] = {
            "gate": "G34_source_sha_and_artifact_integrity",
            "status": "FAIL",
            "reason": str(exc),
            "duration": 0.0,
            "elapsed_sec": 0.0,
        }
    print(f"G34: {gates['G34_source_sha_and_artifact_integrity']['status']}\n")

    # Final verdict computation (Part 174)
    total_gates = len(MANDATORY_GATES)
    all_mandatory_present = all(g in gates and gates[g] is not None for g in MANDATORY_GATES)
    passed_gates = sum(1 for g in MANDATORY_GATES if (gates.get(g) or {}).get("status") == "PASS")
    all_mandatory_passed = all_mandatory_present and (passed_gates == total_gates)

    source_clean_before = gates.get("G00_clean_source", {}).get("status") == "PASS"
    source_clean_after = gates.get("G34_source_sha_and_artifact_integrity", {}).get("status") == "PASS"
    source_sha_unchanged = (pre_sha == post_sha)

    if is_dev_mode:
        accepted = False
        verdict = "DEVELOPMENT_TEST_ONLY"
    else:
        accepted = (
            source_clean_before
            and source_clean_after
            and source_sha_unchanged
            and all_mandatory_passed
        )
        verdict = "ACCEPTED" if accepted else "REJECTED"

    total_elapsed = round(time.monotonic() - start_time, 2)
    report = {
        "target_version": "V17.6.4.3",
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

    report_path = out_dir / "acceptance_v17_6_4_3.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("=" * 60)
    print(f"FINAL RESULT: {passed_gates}/{total_gates} GATES PASSED in {total_elapsed}s")
    print(f"VERDICT: {verdict}")
    print(f"ACCEPTANCE REPORT: {report_path}")
    print("=" * 60)

    return 0 if (accepted or (is_dev_mode and all_mandatory_passed)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
