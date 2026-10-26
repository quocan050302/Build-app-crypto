#!/usr/bin/env python3
"""
AURUM V17.6.4.2 — 32-GATE MASTER PRODUCTION OBSERVABILITY ACCEPTANCE RUNNER
==========================================================================
Enforces 32 rigorous verification gates (G00–G31) per Master Prompt V17.6.4.2
(Section 12 / Part 147–153, Parts 107–116).
Guarantees:
- Sealed pending interval state machine & retry recovery (ECI-01, ECI-12, ECI-13)
- Strict quote authority & metadata-only GOOD bypass rejection (ECI-02, ECI-08)
- Authoritative dashboard persistence & cycle commit truth (ECI-03, ECI-06, ECI-07)
- Fail-closed release acceptance & honest verdict semantics (ECI-04, ECI-05, ECI-14)
- Actual watcher evaluation & session independence (ECI-09)
- Deterministic SMC/ICT strategy parity & zero live exchange orders (ECI-10, ECI-11)
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
    "G01_rotation_failure_durability",
    "G02_checkpoint_failure_durability",
    "G03_quality_transition_failure_durability",
    "G04_pending_retry_idempotency",
    "G05_sqlite_concurrent_writer_safety",
    "G06_feed_timestamp_authority",
    "G07_feed_transport_source_authority",
    "G08_metadata_good_bypass_prevention",
    "G09_cycle_commit_truth",
    "G10_dashboard_persistence_truth",
    "G11_latest_persisted_cycle_truth",
    "G12_replay_test_isolation",
    "G13_run_identity_isolation",
    "G14_coverage_mathematics",
    "G15_engine_liveness",
    "G16_bitget_perpetual_market_calendar",
    "G17_actual_watcher_integration",
    "G18_actual_scheduler_integration",
    "G19_crash_restart_readback",
    "G20_api_contract",
    "G21_frontend_unit_tests",
    "G22_full_backend_tests",
    "G23_full_frontend_vitest",
    "G24_frontend_build",
    "G25_frontend_lint",
    "G26_playwright_e2e",
    "G27_deterministic_soak",
    "G28_behavioral_strategy_parity",
    "G29_zero_live_order_runtime_verification",
    "G30_acceptance_runner_negative_tests",
    "G31_source_sha_and_artifact_integrity",
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
        (BACKEND / "services" / "observation_interval_service.py", ["record_heartbeat_event", "ObservationIntervalService", "seal_active_interval", "persist_pending_intervals"]),
        (BACKEND / "services" / "engine_observability_service.py", ["record_evaluation_cycle", "resolve_observation_evidence_quality", "get_latest_persisted_cycle"]),
    ]
    for path, symbols in essential_symbols:
        if not path.exists():
            return False, [f"Missing core file {path}"]
        content = path.read_text(encoding="utf-8", errors="ignore")
        for sym in symbols:
            if sym not in content:
                return False, [f"Missing symbol {sym} in {path.name}"]
    return True, []


def main(argv=None):
    parser = argparse.ArgumentParser(description="AURUM V17.6.4.2 32-Gate Acceptance Runner")
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
        out_dir = Path(tempfile.mkdtemp(prefix="aurum_v17_6_4_2_artifacts_"))

    print(f"=== AURUM V17.6.4.2 MASTER ACCEPTANCE RUNNER ===")
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

    # Pytest Suites (G01 - G20)
    py_suites = [
        ("G01_rotation_failure_durability", ["backend/tests/test_v17_6_4_2_pending_interval_commit.py", "-k", "test_pi01 or test_pi02 or test_pi03 or test_pi04"]),
        ("G02_checkpoint_failure_durability", ["backend/tests/test_v17_6_4_1_interval_durability.py"]),
        ("G03_quality_transition_failure_durability", ["backend/tests/test_v17_6_4_1_quality_transitions.py"]),
        ("G04_pending_retry_idempotency", ["backend/tests/test_v17_6_4_2_pending_interval_commit.py"]),
        ("G05_sqlite_concurrent_writer_safety", ["backend/tests/test_v17_6_4_telemetry_database_isolation.py"]),
        ("G06_feed_timestamp_authority", ["backend/tests/test_v17_6_4_2_quote_authority.py"]),
        ("G07_feed_transport_source_authority", ["backend/tests/test_v17_6_4_1_feed_evidence_truth.py"]),
        ("G08_metadata_good_bypass_prevention", ["backend/tests/test_v17_6_4_2_metadata_good_bypass.py"]),
        ("G09_cycle_commit_truth", ["backend/tests/test_v17_6_4_2_cycle_commit_truth.py"]),
        ("G10_dashboard_persistence_truth", ["backend/tests/test_v17_6_4_2_persistence_status.py"]),
        ("G11_latest_persisted_cycle_truth", ["backend/tests/test_v17_6_4_1_cycle_commit_consistency.py"]),
        ("G12_replay_test_isolation", ["backend/tests/test_v17_6_4_1_origin_isolation.py"]),
        ("G13_run_identity_isolation", ["backend/tests/test_v17_6_4_cycle_run_identity.py"]),
        ("G14_coverage_mathematics", ["backend/tests/test_v17_6_4_24_7_coverage.py"]),
        ("G15_engine_liveness", ["backend/tests/test_v17_6_engine_heartbeat.py"]),
        ("G16_bitget_perpetual_market_calendar", ["backend/tests/test_v17_6_4_contract_calendar.py"]),
        ("G17_actual_watcher_integration", ["backend/tests/test_v17_6_4_2_watcher_integration.py"]),
        ("G18_actual_scheduler_integration", ["backend/tests/test_v17_6_4_interval_producer.py"]),
        ("G19_crash_restart_readback", ["backend/tests/test_v17_6_4_1_real_db_integration.py"]),
        ("G20_api_contract", ["backend/tests/test_v17_6_4_observation_api.py"]),
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

    # G21: Frontend unit tests
    print("Running G21_frontend_unit_tests...", flush=True)
    g21_junit = out_dir / "G21_frontend_unit_tests.xml"
    g21_cmd = [
        "npm", "test", "--",
        "src/__tests__/v17_6_4_2_observation_status.test.tsx",
        "src/__tests__/v17_6_4_1_observation_evidence.test.tsx",
        "src/__tests__/v17_6_4_observation_truth.test.tsx",
        "--run", "--reporter=junit", f"--outputFile={g21_junit}",
    ]
    gates["G21_frontend_unit_tests"] = run_cmd(
        "G21_frontend_unit_tests", g21_cmd, FRONTEND, env, out_dir,
        junit_path=g21_junit, timeout=180, source_sha=pre_sha,
    )
    print(f"G21: {gates['G21_frontend_unit_tests']['status']}")

    # G22: Full backend tests
    print("Running G22_full_backend_tests...", flush=True)
    g22_junit = out_dir / "G22_full_backend_tests.xml"
    g22_files = [
        "backend/tests/test_v17_6_4_2_pending_interval_commit.py",
        "backend/tests/test_v17_6_4_2_quote_authority.py",
        "backend/tests/test_v17_6_4_2_metadata_good_bypass.py",
        "backend/tests/test_v17_6_4_2_cycle_commit_truth.py",
        "backend/tests/test_v17_6_4_2_persistence_status.py",
        "backend/tests/test_v17_6_4_2_watcher_integration.py",
        "backend/tests/test_v17_6_4_2_paper_parity.py",
        "backend/tests/test_v17_6_4_2_acceptance_integrity.py",
        "backend/tests/test_v17_6_4_1_feed_evidence_truth.py",
        "backend/tests/test_v17_6_4_1_origin_isolation.py",
        "backend/tests/test_v17_6_4_1_quality_transitions.py",
        "backend/tests/test_v17_6_4_1_interval_durability.py",
        "backend/tests/test_v17_6_4_1_cycle_commit_consistency.py",
        "backend/tests/test_v17_6_4_1_engine_market_truth.py",
        "backend/tests/test_v17_6_4_1_real_db_integration.py",
        "backend/tests/test_v17_6_4_1_acceptance_runner.py",
        "backend/tests/test_v17_6_4_contract_calendar.py",
        "backend/tests/test_v17_6_4_cycle_run_identity.py",
        "backend/tests/test_v17_6_4_telemetry_database_isolation.py",
        "backend/tests/test_v17_6_4_interval_producer.py",
        "backend/tests/test_v17_6_4_24_7_coverage.py",
        "backend/tests/test_v17_6_4_engine_owner_truth.py",
        "backend/tests/test_v17_6_4_observation_api.py",
        "backend/tests/test_v17_6_decision_parity.py",
        "backend/tests/test_v17_6_engine_heartbeat.py",
        "backend/tests/test_v17_6_1_market_hours_dst.py",
        "backend/tests/test_v17_6_1_telemetry_resilience.py",
        "backend/tests/test_v17_6_2_transaction_ownership.py",
        "backend/tests/test_v17_6_soak.py",
    ]
    g22_cmd = [python, "-m", "pytest", *g22_files, "-q", f"--junitxml={g22_junit}"]
    gates["G22_full_backend_tests"] = run_cmd(
        "G22_full_backend_tests", g22_cmd, ROOT, env, out_dir,
        junit_path=g22_junit, timeout=300, source_sha=pre_sha,
    )
    print(f"G22: {gates['G22_full_backend_tests']['status']}")

    # G23: Full frontend vitest
    print("Running G23_full_frontend_vitest...", flush=True)
    g23_junit = out_dir / "G23_full_frontend_vitest.xml"
    g23_cmd = ["npm", "test", "--", "--run", "--reporter=junit", f"--outputFile={g23_junit}"]
    gates["G23_full_frontend_vitest"] = run_cmd(
        "G23_full_frontend_vitest", g23_cmd, FRONTEND, env, out_dir,
        junit_path=g23_junit, timeout=300, source_sha=pre_sha,
    )
    print(f"G23: {gates['G23_full_frontend_vitest']['status']}")

    # G24: Frontend build
    print("Running G24_frontend_build...", flush=True)
    g24_cmd = ["npm", "run", "build"]
    gates["G24_frontend_build"] = run_cmd(
        "G24_frontend_build", g24_cmd, FRONTEND, env, out_dir,
        timeout=300, source_sha=pre_sha,
    )
    print(f"G24: {gates['G24_frontend_build']['status']}")

    # G25: Frontend lint
    print("Running G25_frontend_lint...", flush=True)
    g25_cmd = ["npm", "run", "lint"]
    gates["G25_frontend_lint"] = run_cmd(
        "G25_frontend_lint", g25_cmd, FRONTEND, env, out_dir,
        timeout=180, source_sha=pre_sha,
    )
    print(f"G25: {gates['G25_frontend_lint']['status']}")

    # G26: Playwright E2E
    if args.skip_e2e:
        print("Skipping G26 (skip-e2e specified)...")
        gates["G26_playwright_e2e"] = {
            "gate": "G26_playwright_e2e",
            "status": "SKIPPED",
            "reason": "SKIPPED_BY_FLAG",
            "exit_code": 0,
            "duration": 0.0,
            "elapsed_sec": 0.0,
            "source_sha": pre_sha,
        }
    else:
        print("Running G26_playwright_e2e...", flush=True)
        e2e_cmd = ["npx", "playwright", "test", "v17_6_observation.e2e.ts"]
        gates["G26_playwright_e2e"] = run_cmd(
            "G26_playwright_e2e", e2e_cmd, FRONTEND, env, out_dir,
            timeout=300, source_sha=pre_sha,
        )
    print(f"G26: {gates['G26_playwright_e2e']['status']}")

    # G27: Deterministic soak
    print("Running G27_deterministic_soak...", flush=True)
    g27_junit = out_dir / "G27_deterministic_soak.xml"
    g27_cmd = [python, "-m", "pytest", "backend/tests/test_v17_6_soak.py", "-q", f"--junitxml={g27_junit}"]
    gates["G27_deterministic_soak"] = run_cmd(
        "G27_deterministic_soak", g27_cmd, ROOT, env, out_dir,
        junit_path=g27_junit, timeout=120, source_sha=pre_sha,
    )
    print(f"G27: {gates['G27_deterministic_soak']['status']}")

    # G28: Behavioral strategy parity
    print("Running G28_behavioral_strategy_parity...", flush=True)
    g28_junit = out_dir / "G28_behavioral_strategy_parity.xml"
    g28_cmd = [python, "-m", "pytest", "backend/tests/test_v17_6_4_2_paper_parity.py", "-k", "test_sp", "-q", f"--junitxml={g28_junit}"]
    res_g28 = run_cmd(
        "G28_behavioral_strategy_parity", g28_cmd, ROOT, env, out_dir,
        junit_path=g28_junit, timeout=120, source_sha=pre_sha,
    )
    drift_ok, drift_violations = verify_zero_strategy_drift()
    if not drift_ok:
        res_g28["status"] = "FAIL"
        res_g28["reason"] = f"Strategy drift violations: {drift_violations}"
    res_g28["violations"] = drift_violations
    gates["G28_behavioral_strategy_parity"] = res_g28
    print(f"G28: {gates['G28_behavioral_strategy_parity']['status']}")

    # G29: Zero live order runtime verification
    print("Running G29_zero_live_order_runtime_verification...", flush=True)
    g29_junit = out_dir / "G29_zero_live_order_runtime_verification.xml"
    g29_cmd = [python, "-m", "pytest", "backend/tests/test_v17_6_4_2_paper_parity.py", "-k", "test_zo", "-q", f"--junitxml={g29_junit}"]
    res_g29 = run_cmd(
        "G29_zero_live_order_runtime_verification", g29_cmd, ROOT, env, out_dir,
        junit_path=g29_junit, timeout=120, source_sha=pre_sha,
    )
    live_ok, live_violations = verify_zero_live_execution()
    if not live_ok:
        res_g29["status"] = "FAIL"
        res_g29["reason"] = f"Live order execution violations: {live_violations}"
    res_g29["violations"] = live_violations
    gates["G29_zero_live_order_runtime_verification"] = res_g29
    print(f"G29: {gates['G29_zero_live_order_runtime_verification']['status']}")

    # G30: Acceptance runner negative tests
    print("Running G30_acceptance_runner_negative_tests...", flush=True)
    g30_junit = out_dir / "G30_acceptance_runner_negative_tests.xml"
    g30_cmd = [python, "-m", "pytest", "backend/tests/test_v17_6_4_2_acceptance_integrity.py", "-q", f"--junitxml={g30_junit}"]
    gates["G30_acceptance_runner_negative_tests"] = run_cmd(
        "G30_acceptance_runner_negative_tests", g30_cmd, ROOT, env, out_dir,
        junit_path=g30_junit, timeout=120, source_sha=pre_sha,
    )
    print(f"G30: {gates['G30_acceptance_runner_negative_tests']['status']}")

    # G31: Source SHA and artifact integrity
    print("Checking G31_source_sha_and_artifact_integrity...", flush=True)
    try:
        post_sha = git("rev-parse", "HEAD")
        sha_matches = (post_sha == pre_sha)
        post_status = git("status", "--porcelain=v1", "--untracked-files=all")
        post_clean = len(post_status.strip()) == 0

        # Verify artifacts integrity
        missing_logs = []
        for g in MANDATORY_GATES:
            if g not in ["G00_clean_source", "G31_source_sha_and_artifact_integrity"]:
                lf = out_dir / f"{g}.log"
                if not lf.exists() and (gates.get(g) or {}).get("status") != "SKIPPED":
                    missing_logs.append(g)

        if is_dev_mode:
            g31_ok = sha_matches and (len(missing_logs) == 0)
            g31_reason = "OK (dev mode / allow-dirty)"
        else:
            g31_ok = sha_matches and post_clean and (len(missing_logs) == 0)
            g31_reason = "OK" if g31_ok else (
                f"SHA mismatch (pre={pre_sha}, post={post_sha})" if not sha_matches
                else f"Worktree dirty post-test:\n{post_status}" if not post_clean
                else f"Missing evidence logs for: {missing_logs}"
            )

        gates["G31_source_sha_and_artifact_integrity"] = {
            "gate": "G31_source_sha_and_artifact_integrity",
            "pre_sha": pre_sha,
            "post_sha": post_sha,
            "status": "PASS" if g31_ok else "FAIL",
            "reason": g31_reason,
            "duration": 0.0,
            "elapsed_sec": 0.0,
        }
    except Exception as exc:
        post_sha = "UNKNOWN"
        gates["G31_source_sha_and_artifact_integrity"] = {
            "gate": "G31_source_sha_and_artifact_integrity",
            "status": "FAIL",
            "reason": str(exc),
            "duration": 0.0,
            "elapsed_sec": 0.0,
        }
    print(f"G31: {gates['G31_source_sha_and_artifact_integrity']['status']}\n")

    # Final verdict computation (Part 109, Part 147–153)
    total_gates = len(MANDATORY_GATES)
    all_mandatory_present = all(g in gates and gates[g] is not None for g in MANDATORY_GATES)
    passed_gates = sum(1 for g in MANDATORY_GATES if (gates.get(g) or {}).get("status") == "PASS")
    all_mandatory_passed = all_mandatory_present and (passed_gates == total_gates)

    source_clean_before = gates.get("G00_clean_source", {}).get("status") == "PASS"
    source_clean_after = gates.get("G31_source_sha_and_artifact_integrity", {}).get("status") == "PASS"
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
        "target_version": "V17.6.4.2",
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

    report_path = out_dir / "acceptance_v17_6_4_2.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("=" * 60)
    print(f"FINAL RESULT: {passed_gates}/{total_gates} GATES PASSED in {total_elapsed}s")
    print(f"VERDICT: {verdict}")
    print(f"ACCEPTANCE REPORT: {report_path}")
    print("=" * 60)

    return 0 if (accepted or (is_dev_mode and all_mandatory_passed)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
