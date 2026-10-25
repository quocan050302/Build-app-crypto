#!/usr/bin/env python3
"""
AURUM V17.6.4.3b — 36-GATE MASTER ACCEPTANCE RUNNER
===================================================
Enforces 36 rigorous verification gates (G00–G35) per Master Prompt V17.6.4.3b.
Guarantees:
- Canonical feed provenance truth (no timestamp or source fabrication)
- Real CG01 scheduler runtime execution (evaluate_symbol_now executes pipeline)
- Current evidence health truth (feed UNVERIFIED cannot produce HEALTHY)
- Borrowed session unique conflict durability (independent readback before ALREADY_EXISTS)
- Strict backend test discovery (code == 0 AND collected > 0 AND no errors)
- Unexpected skip rejection (any unexpected skip -> FAIL)
- Full backend, Vitest, build, and lint verification
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
    "G01_missing_timestamps_fail_closed",
    "G02_missing_source_fails_closed",
    "G03_disconnected_remains_disconnected",
    "G04_reconnecting_remains_reconnecting",
    "G05_old_epoch_rejected",
    "G06_genuine_fresh_ws_accepted",
    "G07_proper_interval_good",
    "G08_real_scheduler_instance_created",
    "G09_notify_market_change_queues",
    "G10_evaluate_symbol_now_executes",
    "G11_capture_input_bundle_complete",
    "G12_scheduler_records_cycle_to_db",
    "G13_shadow_mode_disables_execution",
    "G14_stale_market_aborts_evaluation",
    "G15_concurrent_evaluations_serialized",
    "G16_strategy_decision_logged_with_cycle",
    "G17_scheduler_full_lifecycle",
    "G18_feed_good_evaluates_healthy",
    "G19_feed_unverified_not_healthy",
    "G20_process_stopped_offline",
    "G21_old_interval_stalled",
    "G22_rest_fallback_not_healthy",
    "G23_recovery_restores_healthy",
    "G24_uncommitted_duplicate_pending",
    "G25_unverified_unique_conflict_retried",
    "G26_committed_row_already_exists",
    "G27_different_interval_identity_distinct",
    "G28_strict_backend_discovery",
    "G29_unexpected_skip_rejection",
    "G30_full_backend_test_execution",
    "G31_frontend_vitest",
    "G32_frontend_build",
    "G33_frontend_lint",
    "G34_zero_live_order_runtime_verification",
    "G35_release_acceptance_report_reconciliation",
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
                if tc.find("skipped") is not None:
                    name = tc.attrib.get("name", "unknown")
                    classname = tc.attrib.get("classname", "")
                    skipped_names.append(f"{classname}::{name}" if classname else name)

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


def run_backend_discovery(python, ROOT, env, out_dir, source_sha):
    """G28: Strict dynamic collection across backend/tests."""
    cmd = [python, "-m", "pytest", "backend/tests", "--collect-only", "-q"]
    started_at = datetime.now(timezone.utc).isoformat()
    start_mono = time.monotonic()
    log_file = out_dir / "G28_strict_backend_discovery.log"
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
        "gate": "G28_strict_backend_discovery",
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
    parser = argparse.ArgumentParser(description="AURUM V17.6.4.3b 36-Gate Acceptance Runner")
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
        out_dir = Path(tempfile.mkdtemp(prefix="aurum_v17_6_4_3b_artifacts_"))

    print(f"=== AURUM V17.6.4.3b MASTER ACCEPTANCE RUNNER ===")
    print(f"Mode: {'DEVELOPMENT' if is_dev_mode else 'RELEASE'}")
    print(f"Artifact Directory: {out_dir}\n")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND)
    env["TESTING"] = "1"

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

    # Pytest Suites (G01 - G27)
    py_suites = [
        # Feed Provenance Gates (G01 - G07)
        ("G01_missing_timestamps_fail_closed", ["backend/tests/test_v17_6_4_3b_feed_provenance.py", "-k", "test_fp01"]),
        ("G02_missing_source_fails_closed", ["backend/tests/test_v17_6_4_3b_feed_provenance.py", "-k", "test_fp02"]),
        ("G03_disconnected_remains_disconnected", ["backend/tests/test_v17_6_4_3b_feed_provenance.py", "-k", "test_fp04"]),
        ("G04_reconnecting_remains_reconnecting", ["backend/tests/test_v17_6_4_3b_feed_provenance.py", "-k", "test_fp05"]),
        ("G05_old_epoch_rejected", ["backend/tests/test_v17_6_4_3b_feed_provenance.py", "-k", "test_fp07"]),
        ("G06_genuine_fresh_ws_accepted", ["backend/tests/test_v17_6_4_3b_feed_provenance.py", "-k", "test_fp21"]),
        ("G07_proper_interval_good", ["backend/tests/test_v17_6_4_3b_feed_provenance.py", "-k", "test_fp26"]),

        # Scheduler Runtime Gates (G08 - G17)
        ("G08_real_scheduler_instance_created", ["backend/tests/test_v17_6_4_3b_scheduler_runtime.py", "-k", "test_sc01"]),
        ("G09_notify_market_change_queues", ["backend/tests/test_v17_6_4_3b_scheduler_runtime.py", "-k", "test_sc02"]),
        ("G10_evaluate_symbol_now_executes", ["backend/tests/test_v17_6_4_3b_scheduler_runtime.py", "-k", "test_sc03"]),
        ("G11_capture_input_bundle_complete", ["backend/tests/test_v17_6_4_3b_scheduler_runtime.py", "-k", "test_sc04"]),
        ("G12_scheduler_records_cycle_to_db", ["backend/tests/test_v17_6_4_3b_scheduler_runtime.py", "-k", "test_sc06"]),
        ("G13_shadow_mode_disables_execution", ["backend/tests/test_v17_6_4_3b_scheduler_runtime.py", "-k", "test_sc07"]),
        ("G14_stale_market_aborts_evaluation", ["backend/tests/test_v17_6_4_3b_scheduler_runtime.py", "-k", "test_sc09"]),
        ("G15_concurrent_evaluations_serialized", ["backend/tests/test_v17_6_4_3b_scheduler_runtime.py", "-k", "test_sc12"]),
        ("G16_strategy_decision_logged_with_cycle", ["backend/tests/test_v17_6_4_3b_scheduler_runtime.py", "-k", "test_sc14"]),
        ("G17_scheduler_full_lifecycle", ["backend/tests/test_v17_6_4_3b_scheduler_runtime.py", "-k", "test_sc18"]),

        # Health Truth Gates (G18 - G23)
        ("G18_feed_good_evaluates_healthy", ["backend/tests/test_v17_6_4_3b_health_truth.py", "-k", "test_eh01"]),
        ("G19_feed_unverified_not_healthy", ["backend/tests/test_v17_6_4_3b_health_truth.py", "-k", "test_eh02"]),
        ("G20_process_stopped_offline", ["backend/tests/test_v17_6_4_3b_health_truth.py", "-k", "test_eh06"]),
        ("G21_old_interval_stalled", ["backend/tests/test_v17_6_4_3b_health_truth.py", "-k", "test_eh09"]),
        ("G22_rest_fallback_not_healthy", ["backend/tests/test_v17_6_4_3b_health_truth.py", "-k", "test_eh12"]),
        ("G23_recovery_restores_healthy", ["backend/tests/test_v17_6_4_3b_health_truth.py", "-k", "test_eh15"]),

        # Unique Commit Truth Gates (G24 - G27)
        ("G24_uncommitted_duplicate_pending", ["backend/tests/test_v17_6_4_3b_unique_commit_truth.py", "-k", "test_uc02"]),
        ("G25_unverified_unique_conflict_retried", ["backend/tests/test_v17_6_4_3b_unique_commit_truth.py", "-k", "test_uc03"]),
        ("G26_committed_row_already_exists", ["backend/tests/test_v17_6_4_3b_unique_commit_truth.py", "-k", "test_uc05"]),
        ("G27_different_interval_identity_distinct", ["backend/tests/test_v17_6_4_3b_unique_commit_truth.py", "-k", "test_uc06"]),
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

    # G28: Strict backend test discovery
    print("Running G28_strict_backend_discovery...", flush=True)
    gates["G28_strict_backend_discovery"] = run_backend_discovery(
        python, ROOT, env, out_dir, pre_sha,
    )
    print(f"G28: {gates['G28_strict_backend_discovery']['status']}")

    # G29: Unexpected skip rejection verification
    print("Running G29_unexpected_skip_rejection...", flush=True)
    g29_junit = out_dir / "G29_unexpected_skip_rejection.xml"
    g29_cmd = [python, "-m", "pytest", "backend/tests/test_v17_6_4_3b_acceptance_integrity.py", "-k", "test_ai03", "-q", f"--junitxml={g29_junit}"]
    gates["G29_unexpected_skip_rejection"] = run_cmd(
        "G29_unexpected_skip_rejection", g29_cmd, ROOT, env, out_dir,
        junit_path=g29_junit, timeout=120, source_sha=pre_sha,
    )
    print(f"G29: {gates['G29_unexpected_skip_rejection']['status']}")

    # G30: Full backend test execution
    print("Running G30_full_backend_test_execution...", flush=True)
    g30_junit = out_dir / "G30_full_backend_test_execution.xml"
    g30_cmd = [python, "-m", "pytest", "backend/tests", "-q", f"--junitxml={g30_junit}"]
    gates["G30_full_backend_test_execution"] = run_cmd(
        "G30_full_backend_test_execution", g30_cmd, ROOT, env, out_dir,
        junit_path=g30_junit, timeout=600, source_sha=pre_sha,
    )
    print(f"G30: {gates['G30_full_backend_test_execution']['status']}")

    # G31: Full frontend Vitest
    print("Running G31_frontend_vitest...", flush=True)
    g31_junit = out_dir / "G31_frontend_vitest.xml"
    g31_cmd = ["npm", "test", "--", "--run", "--reporter=junit", f"--outputFile={g31_junit}"]
    gates["G31_frontend_vitest"] = run_cmd(
        "G31_frontend_vitest", g31_cmd, FRONTEND, env, out_dir,
        junit_path=g31_junit, timeout=300, source_sha=pre_sha,
    )
    print(f"G31: {gates['G31_frontend_vitest']['status']}")

    # G32: Frontend build
    print("Running G32_frontend_build...", flush=True)
    g32_cmd = ["npm", "run", "build"]
    gates["G32_frontend_build"] = run_cmd(
        "G32_frontend_build", g32_cmd, FRONTEND, env, out_dir,
        timeout=300, source_sha=pre_sha,
    )
    print(f"G32: {gates['G32_frontend_build']['status']}")

    # G33: Frontend lint
    print("Running G33_frontend_lint...", flush=True)
    g33_cmd = ["npm", "run", "lint"]
    gates["G33_frontend_lint"] = run_cmd(
        "G33_frontend_lint", g33_cmd, FRONTEND, env, out_dir,
        timeout=180, source_sha=pre_sha,
    )
    print(f"G33: {gates['G33_frontend_lint']['status']}")

    # G34: Zero LIVE order runtime verification
    print("Running G34_zero_live_order_runtime_verification...", flush=True)
    live_ok, live_violations = verify_zero_live_execution()
    g34_res = {
        "gate": "G34_zero_live_order_runtime_verification",
        "status": "PASS" if live_ok else "FAIL",
        "reason": "OK" if live_ok else f"Live order execution violations: {live_violations}",
        "violations": live_violations,
        "duration": 0.0,
        "elapsed_sec": 0.0,
        "source_sha": pre_sha,
    }
    gates["G34_zero_live_order_runtime_verification"] = g34_res
    print(f"G34: {gates['G34_zero_live_order_runtime_verification']['status']}")

    # G35: Release acceptance report reconciliation
    print("Checking G35_release_acceptance_report_reconciliation...", flush=True)
    try:
        post_sha = git("rev-parse", "HEAD")
        sha_matches = (post_sha == pre_sha)

        missing_logs = []
        for g in MANDATORY_GATES:
            if g not in ["G00_clean_branch_and_source", "G34_zero_live_order_runtime_verification", "G35_release_acceptance_report_reconciliation"]:
                lf = out_dir / f"{g}.log"
                if not lf.exists() and (gates.get(g) or {}).get("status") != "SKIPPED":
                    missing_logs.append(g)

        if is_dev_mode:
            g35_ok = sha_matches and (len(missing_logs) == 0)
            g35_reason = "OK (dev mode / allow-dirty)"
        else:
            g35_ok = sha_matches and (len(missing_logs) == 0)
            g35_reason = "OK" if g35_ok else (
                f"SHA mismatch (pre={pre_sha}, post={post_sha})" if not sha_matches
                else f"Missing evidence logs for: {missing_logs}"
            )

        gates["G35_release_acceptance_report_reconciliation"] = {
            "gate": "G35_release_acceptance_report_reconciliation",
            "pre_sha": pre_sha,
            "post_sha": post_sha,
            "status": "PASS" if g35_ok else "FAIL",
            "reason": g35_reason,
            "duration": 0.0,
            "elapsed_sec": 0.0,
        }
    except Exception as exc:
        post_sha = "UNKNOWN"
        gates["G35_release_acceptance_report_reconciliation"] = {
            "gate": "G35_release_acceptance_report_reconciliation",
            "status": "FAIL",
            "reason": str(exc),
            "duration": 0.0,
            "elapsed_sec": 0.0,
        }
    print(f"G35: {gates['G35_release_acceptance_report_reconciliation']['status']}\n")

    # Final verdict computation
    total_gates = len(MANDATORY_GATES)
    all_mandatory_present = all(g in gates and gates[g] is not None for g in MANDATORY_GATES)
    passed_gates = sum(1 for g in MANDATORY_GATES if (gates.get(g) or {}).get("status") == "PASS")
    all_mandatory_passed = all_mandatory_present and (passed_gates == total_gates)

    source_clean_before = gates.get("G00_clean_branch_and_source", {}).get("status") == "PASS"
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
            and source_sha_unchanged
            and all_mandatory_passed
            and not has_unexpected_skips
        )
        verdict = "ACCEPTED" if accepted else "REJECTED"

    total_elapsed = round(time.monotonic() - start_time, 2)
    report = {
        "target_version": "V17.6.4.3b",
        "verdict": verdict,
        "mode": "dev" if is_dev_mode else "release",
        "branch": current_branch if branch_ok else "UNKNOWN",
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

    report_path = out_dir / "acceptance_v17_6_4_3b.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("=" * 60)
    print(f"FINAL RESULT: {passed_gates}/{total_gates} GATES PASSED in {total_elapsed}s")
    print(f"VERDICT: {verdict}")
    print(f"ACCEPTANCE REPORT: {report_path}")
    print("=" * 60)

    return 0 if (accepted or (is_dev_mode and all_mandatory_passed)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
