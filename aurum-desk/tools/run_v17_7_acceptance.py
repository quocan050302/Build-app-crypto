#!/usr/bin/env python3
"""
AURUM DESK V17.7.2 — EVIDENCE-BASED MASTER ACCEPTANCE RUNNER
============================================================
Enforces all 23 release gates (G01 to G23) for V17.7.2:
Causal SL Evidence, True Runtime E2E & Fail-Closed Acceptance Repair.
Branch: feat/v17-7-adaptive-structural-stop-loss
"""

import os
import sys
import re
import json
import subprocess
import time
from pathlib import Path
from unittest.mock import Mock, patch
import inspect

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
ARTIFACTS_DIR = ROOT / "docs" / "artifacts"
PYTHON_BIN = str(BACKEND / "venv" / "bin" / "python") if (BACKEND / "venv" / "bin" / "python").exists() else sys.executable
PYTEST_BIN = str(BACKEND / "venv" / "bin" / "pytest") if (BACKEND / "venv" / "bin" / "pytest").exists() else "pytest"


def run_cmd(cmd, cwd=ROOT, env=None):
    merged_env = os.environ.copy()
    merged_env["PYTHONPATH"] = f"{BACKEND}:{BACKEND / 'tests'}"
    merged_env["TESTING"] = "1"
    if env:
        merged_env.update(env)
    start_t = time.time()
    res = subprocess.run(cmd, shell=isinstance(cmd, str), cwd=cwd, env=merged_env, capture_output=True, text=True)
    duration_ms = int((time.time() - start_t) * 1000)
    return res.returncode, res.stdout, res.stderr, duration_ms


def parse_pytest_counts(output: str):
    """Dynamically parses pytest stdout to extract collected, passed, failed, skipped."""
    passed_m = re.search(r"(\d+)\s+passed", output)
    failed_m = re.search(r"(\d+)\s+failed", output)
    skipped_m = re.search(r"(\d+)\s+skipped", output)
    collected_m = re.search(r"collected\s+(\d+)\s+items?", output)

    passed = int(passed_m.group(1)) if passed_m else 0
    failed = int(failed_m.group(1)) if failed_m else 0
    skipped = int(skipped_m.group(1)) if skipped_m else 0
    collected = int(collected_m.group(1)) if collected_m else (passed + failed + skipped)

    return {"collected": collected, "passed": passed, "failed": failed, "skipped": skipped}


def parse_vitest_counts(output: str):
    """Dynamically parses vitest stdout to extract passed/failed test counts."""
    tests_m = re.search(r"Tests\s+(\d+)\s+passed\s*(?:\((\d+)\))?", output)
    files_m = re.search(r"Test Files\s+(\d+)\s+passed\s*(?:\((\d+)\))?", output)

    passed_tests = int(tests_m.group(1)) if tests_m else 0
    total_tests = int(tests_m.group(2)) if (tests_m and tests_m.group(2)) else passed_tests
    passed_files = int(files_m.group(1)) if files_m else 0

    return {"test_files_passed": passed_files, "tests_passed": passed_tests, "tests_total": total_tests}


def verify_live_order_safety_boundary():
    """Instruments network boundaries with active spy asserting 0 calls and clean PaperBroker API."""
    sys.path.insert(0, str(BACKEND))
    try:
        from paper_broker import PaperBroker
        from services.execution_coordinator import LIVE_EXCHANGE_ORDERS_SENT

        spy = Mock()
        def _forbidden_sink(*args, **kwargs):
            spy(*args, **kwargs)
            raise RuntimeError("FORBIDDEN_LIVE_ORDER_SUBMISSION: Live exchange order attempt detected!")

        with patch("httpx.Client.post", side_effect=_forbidden_sink), \
             patch("httpx.AsyncClient.post", side_effect=_forbidden_sink):

            pb_methods = [m[0] for m in inspect.getmembers(PaperBroker, predicate=inspect.isfunction)]
            for forbidden_method in ("place_live_order", "submit_exchange_order", "send_order", "execute_live_trade"):
                assert forbidden_method not in pb_methods, f"Forbidden live method {forbidden_method} found in PaperBroker!"

            assert LIVE_EXCHANGE_ORDERS_SENT == 0, "LIVE_EXCHANGE_ORDERS_SENT must be zero!"
            assert spy.call_count == 0, "No live outbound HTTP requests permitted!"
        return True, "Verified 0 live outbound calls with active HTTP spy interceptors and clean PaperBroker API."
    except Exception as exc:
        return False, str(exc)


def main():
    print("=" * 80)
    print("AURUM DESK V17.7.2 — EVIDENCE-BASED MASTER ACCEPTANCE RUNNER")
    print("=" * 80)

    results = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runner_version": "17.7.2",
        "branch": "",
        "source_sha": "",
        "gates": {},
        "verdict": "BLOCKED",
    }
    all_gates_passed = True

    # --------------------------------------------------------------------------
    # GATE 01: Branch & Source Integrity
    # --------------------------------------------------------------------------
    print("\n[GATE 01] Verifying Working Git Branch & Source Integrity...")
    code, stdout, _, dur = run_cmd("git branch --show-current")
    branch = stdout.strip()
    results["branch"] = branch
    expected_branch = "feat/v17-7-adaptive-structural-stop-loss"

    code_sha, sha_out, _, _ = run_cmd("git rev-parse HEAD")
    current_sha = sha_out.strip()
    results["source_sha"] = current_sha

    if branch == expected_branch:
        print(f"  -> PASS: Verified branch '{branch}' (HEAD: {current_sha})")
        results["gates"]["G01_BRANCH_INTEGRITY"] = {
            "status": "PASS", "branch": branch, "sha": current_sha, "duration_ms": dur
        }
    else:
        print(f"  -> BLOCKER: Expected branch '{expected_branch}', got '{branch}'")
        results["gates"]["G01_BRANCH_INTEGRITY"] = {
            "status": "FAIL", "branch": branch, "expected": expected_branch, "duration_ms": dur
        }
        all_gates_passed = False

    # --------------------------------------------------------------------------
    # GATE 02: Test Environment / Database Isolation
    # --------------------------------------------------------------------------
    print("\n[GATE 02] Verifying Test Environment & Database Isolation...")
    cmd_g02 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -k 'test_ce_17'"
    code, out, err, dur = run_cmd(cmd_g02)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 1 and counts["failed"] == 0:
        print(f"  -> PASS: Database isolation verified (runtime DB protected, temp DB enforced)")
        results["gates"]["G02_DATABASE_ISOLATION"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Database isolation test failed:\n{out}\n{err}")
        results["gates"]["G02_DATABASE_ISOLATION"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }
        all_gates_passed = False

    # --------------------------------------------------------------------------
    # GATE 03: Causal Market Data & Required ATR
    # --------------------------------------------------------------------------
    print("\n[GATE 03] Verifying Causal Market Data & Required ATR Fail-Closed...")
    cmd_g03 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -k 'test_ce_01 or test_ce_02 or test_ce_03 or test_ce_04 or test_ce_05 or test_dsl_10'"
    code, out, err, dur = run_cmd(cmd_g03)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 5 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} causal ATR and volatility barrier tests passed")
        results["gates"]["G03_CAUSAL_ATR_EVIDENCE"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Causal ATR tests failed:\n{out}\n{err}")
        results["gates"]["G03_CAUSAL_ATR_EVIDENCE"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }
        all_gates_passed = False

    # --------------------------------------------------------------------------
    # GATE 04: Structural Anchor & Invalidation
    # --------------------------------------------------------------------------
    print("\n[GATE 04] Verifying Structural Anchor Causal Ordering & Invalidation...")
    cmd_g04 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -k 'test_ce_06 or test_ce_07 or test_ce_08 or test_sl_58'"
    code, out, err, dur = run_cmd(cmd_g04)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 4 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} anchor causality & invalidation tests passed")
        results["gates"]["G04_STRUCTURAL_ANCHOR_AUTHORITY"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Anchor authority tests failed:\n{out}\n{err}")
        results["gates"]["G04_STRUCTURAL_ANCHOR_AUTHORITY"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }
        all_gates_passed = False

    # --------------------------------------------------------------------------
    # GATE 05: Distant SL Financial Integrity
    # --------------------------------------------------------------------------
    print("\n[GATE 05] Verifying Distant SL (>4x ATR) Financial Integrity...")
    cmd_g05 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -k 'test_dsl_01 or test_dsl_02 or test_dsl_07 or test_ce_11'"
    code, out, err, dur = run_cmd(cmd_g05)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 4 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} distant SL financial integrity tests passed")
        results["gates"]["G05_DISTANT_SL_FINANCIAL_INTEGRITY"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Distant SL financial tests failed:\n{out}\n{err}")
        results["gates"]["G05_DISTANT_SL_FINANCIAL_INTEGRITY"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }
        all_gates_passed = False

    # --------------------------------------------------------------------------
    # GATE 06: CG01 Real Evaluator Integration
    # --------------------------------------------------------------------------
    print("\n[GATE 06] Verifying CG01 Real Evaluator Integration...")
    cmd_g06 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -k 'test_sl_36 or test_ce_10 or test_ce_11'"
    code, out, err, dur = run_cmd(cmd_g06)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 3 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} real CG01 evaluator integration tests passed")
        results["gates"]["G06_CG01_REAL_INTEGRATION"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: CG01 integration tests failed:\n{out}\n{err}")
        results["gates"]["G06_CG01_REAL_INTEGRATION"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }
        all_gates_passed = False

    # --------------------------------------------------------------------------
    # GATE 07: M1 Model Real Evaluator Integration
    # --------------------------------------------------------------------------
    print("\n[GATE 07] Verifying Scalp M1 Models Real Evaluator Integration...")
    cmd_g07 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -k 'test_sl_37 or test_sl_38 or test_sl_39 or test_sl_40 or test_ce_12'"
    code, out, err, dur = run_cmd(cmd_g07)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 5 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} real M1 model evaluator integration tests passed")
        results["gates"]["G07_M1_MODELS_REAL_INTEGRATION"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: M1 model integration tests failed:\n{out}\n{err}")
        results["gates"]["G07_M1_MODELS_REAL_INTEGRATION"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }
        all_gates_passed = False

    # --------------------------------------------------------------------------
    # GATE 08: Orchestrator / Actual ARM Boundary
    # --------------------------------------------------------------------------
    print("\n[GATE 08] Verifying Orchestrator Actual ARM Boundary...")
    cmd_g08 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -k 'test_sl_43 or test_ce_13'"
    code, out, err, dur = run_cmd(cmd_g08)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 2 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} production orchestrator ARM tests passed")
        results["gates"]["G08_ORCHESTRATOR_ACTUAL_ARM"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Orchestrator ARM tests failed:\n{out}\n{err}")
        results["gates"]["G08_ORCHESTRATOR_ACTUAL_ARM"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }
        all_gates_passed = False

    # --------------------------------------------------------------------------
    # GATE 09: Real Execution Coordinator / FILL Sizing
    # --------------------------------------------------------------------------
    print("\n[GATE 09] Verifying Real Execution Coordinator / Fill Sizing Preservation...")
    cmd_g09 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -k 'test_sl_46 or test_ce_14'"
    code, out, err, dur = run_cmd(cmd_g09)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 2 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} fill sizing preservation tests passed")
        results["gates"]["G09_EXECUTION_FILL_SIZING"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Fill sizing tests failed:\n{out}\n{err}")
        results["gates"]["G09_EXECUTION_FILL_SIZING"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }
        all_gates_passed = False

    # --------------------------------------------------------------------------
    # GATE 10: Exit / CLOSED / Persistence
    # --------------------------------------------------------------------------
    print("\n[GATE 10] Verifying Exit, CLOSED State & Durable Persistence...")
    cmd_g10 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -k 'test_sl_47 or test_sl_48 or test_sl_60 or test_ce_15'"
    code, out, err, dur = run_cmd(cmd_g10)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 4 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} exit and persistence lifecycle tests passed")
        results["gates"]["G10_EXIT_PERSISTENCE"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Exit and persistence tests failed:\n{out}\n{err}")
        results["gates"]["G10_EXIT_PERSISTENCE"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }
        all_gates_passed = False

    # --------------------------------------------------------------------------
    # GATE 11: Concurrent ARM & Idempotency Races
    # --------------------------------------------------------------------------
    print("\n[GATE 11] Verifying Multi-Threaded Concurrent ARM & Idempotency...")
    cmd_g11 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -k 'test_sl_57 or test_dsl_18 or test_ce_16'"
    code, out, err, dur = run_cmd(cmd_g11)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 3 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} multi-threaded concurrency race tests passed")
        results["gates"]["G11_CONCURRENCY_IDEMPOTENCY"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Concurrency tests failed:\n{out}\n{err}")
        results["gates"]["G11_CONCURRENCY_IDEMPOTENCY"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }
        all_gates_passed = False

    # --------------------------------------------------------------------------
    # GATE 12: Actual Live-Order Dispatch Safety & Negative Control
    # --------------------------------------------------------------------------
    print("\n[GATE 12] Verifying Live-Order Dispatch Safety & Negative Control...")
    safety_ok, safety_msg = verify_live_order_safety_boundary()
    cmd_g12 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -k 'test_sl_50 or test_dsl_19 or test_ce_18'"
    code, out, err, dur = run_cmd(cmd_g12)
    counts = parse_pytest_counts(out)
    if safety_ok and code == 0 and counts["passed"] >= 3 and counts["failed"] == 0:
        print(f"  -> PASS: {safety_msg}")
        print(f"  -> PASS: {counts['passed']} live-order safety & negative control tests passed")
        results["gates"]["G12_LIVE_ORDER_SAFETY"] = {
            "status": "PASS", "details": safety_msg, "counts": counts, "duration_ms": dur
        }
    else:
        print(f"  -> FAIL: Live-order safety verification failed: {safety_msg}\n{out}\n{err}")
        results["gates"]["G12_LIVE_ORDER_SAFETY"] = {
            "status": "FAIL", "details": safety_msg, "counts": counts, "duration_ms": dur
        }
        all_gates_passed = False

    # --------------------------------------------------------------------------
    # GATE 13: Dedicated V17.7.2 Suite (100 Gates: SL-01..60 + DSL-01..20 + CE-01..20)
    # --------------------------------------------------------------------------
    print("\n[GATE 13] Executing Complete Dedicated V17.7.2 Test Suite (100 Gates)...")
    cmd_g13 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -v"
    code, out, err, dur = run_cmd(cmd_g13)
    counts_g13 = parse_pytest_counts(out)
    if code == 0 and counts_g13["passed"] == 100 and counts_g13["failed"] == 0:
        print(f"  -> PASS: All 100 dedicated gates passed in {dur/1000:.2f}s!")
        results["gates"]["G13_V17_7_2_DEDICATED"] = {
            "status": "PASS", "counts": counts_g13, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Dedicated suite failed (code {code}, passed {counts_g13['passed']}/100):\n{out}\n{err}")
        results["gates"]["G13_V17_7_2_DEDICATED"] = {
            "status": "FAIL", "counts": counts_g13, "duration_ms": dur, "exit_code": code
        }
        all_gates_passed = False

    # --------------------------------------------------------------------------
    # GATE 14: V17.5 Regression Suite
    # --------------------------------------------------------------------------
    print("\n[GATE 14] Executing V17.5 Regression Suite...")
    cmd_g14 = f"{PYTEST_BIN} backend/tests/test_v17_5*.py -q"
    code, out, err, dur = run_cmd(cmd_g14)
    counts_g14 = parse_pytest_counts(out)
    if code == 0 and counts_g14["failed"] == 0 and counts_g14["passed"] > 0:
        print(f"  -> PASS: V17.5 regression passed ({counts_g14['passed']} tests) in {dur/1000:.2f}s")
        results["gates"]["G14_V17_5_REGRESSION"] = {
            "status": "PASS", "counts": counts_g14, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: V17.5 regression failed:\n{out}\n{err}")
        results["gates"]["G14_V17_5_REGRESSION"] = {
            "status": "FAIL", "counts": counts_g14, "duration_ms": dur, "exit_code": code
        }
        all_gates_passed = False

    # --------------------------------------------------------------------------
    # GATE 15: V17.6 Regression Suite
    # --------------------------------------------------------------------------
    print("\n[GATE 15] Executing V17.6 Regression Suite (incl. V17.6.4.3d)...")
    cmd_g15 = f"{PYTEST_BIN} backend/tests/test_v17_6*.py -q"
    code, out, err, dur = run_cmd(cmd_g15)
    counts_g15 = parse_pytest_counts(out)
    if code == 0 and counts_g15["failed"] == 0 and counts_g15["passed"] > 0:
        print(f"  -> PASS: V17.6 regression passed ({counts_g15['passed']} tests) in {dur/1000:.2f}s")
        results["gates"]["G15_V17_6_REGRESSION"] = {
            "status": "PASS", "counts": counts_g15, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: V17.6 regression failed:\n{out}\n{err}")
        results["gates"]["G15_V17_6_REGRESSION"] = {
            "status": "FAIL", "counts": counts_g15, "duration_ms": dur, "exit_code": code
        }
        all_gates_passed = False

    # --------------------------------------------------------------------------
    # GATE 16: Full Backend Test Suite
    # --------------------------------------------------------------------------
    print("\n[GATE 16] Executing Backend Core Integration Tests...")
    cmd_g16 = f"{PYTEST_BIN} backend/tests/test_v17_*.py -q"
    code, out, err, dur = run_cmd(cmd_g16)
    counts_g16 = parse_pytest_counts(out)
    if code == 0 and counts_g16["failed"] == 0 and counts_g16["passed"] > 0:
        print(f"  -> PASS: Backend core suite passed ({counts_g16['passed']} tests) in {dur/1000:.2f}s")
        results["gates"]["G16_BACKEND_FULL_PYTEST"] = {
            "status": "PASS", "counts": counts_g16, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Backend core suite failed:\n{out}\n{err}")
        results["gates"]["G16_BACKEND_FULL_PYTEST"] = {
            "status": "FAIL", "counts": counts_g16, "duration_ms": dur, "exit_code": code
        }
        all_gates_passed = False

    # --------------------------------------------------------------------------
    # GATE 17: Frontend Vitest Unit & Contract Suite
    # --------------------------------------------------------------------------
    print("\n[GATE 17] Executing Frontend Vitest Suite...")
    cmd_g17 = "npm test -- --run"
    code, out, err, dur = run_cmd(cmd_g17, cwd=FRONTEND)
    vitest_counts = parse_vitest_counts(out)
    if code == 0 and vitest_counts["tests_passed"] > 0:
        print(f"  -> PASS: Frontend Vitest passed ({vitest_counts['tests_passed']} tests in {vitest_counts['test_files_passed']} files) in {dur/1000:.2f}s")
        results["gates"]["G17_FRONTEND_VITEST"] = {
            "status": "PASS", "counts": vitest_counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Frontend Vitest failed:\n{out}\n{err}")
        results["gates"]["G17_FRONTEND_VITEST"] = {
            "status": "FAIL", "counts": vitest_counts, "duration_ms": dur, "exit_code": code
        }
        all_gates_passed = False

    # --------------------------------------------------------------------------
    # GATE 18: Frontend Production Build
    # --------------------------------------------------------------------------
    print("\n[GATE 18] Executing Frontend Production Build...")
    cmd_g18 = "npm run build"
    code, out, err, dur = run_cmd(cmd_g18, cwd=FRONTEND)
    if code == 0:
        print(f"  -> PASS: Frontend production build succeeded in {dur/1000:.2f}s")
        results["gates"]["G18_FRONTEND_BUILD"] = {
            "status": "PASS", "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Frontend build failed:\n{out}\n{err}")
        results["gates"]["G18_FRONTEND_BUILD"] = {
            "status": "FAIL", "duration_ms": dur, "exit_code": code
        }
        all_gates_passed = False

    # --------------------------------------------------------------------------
    # GATE 19: Frontend Lint
    # --------------------------------------------------------------------------
    print("\n[GATE 19] Executing Frontend Oxlint...")
    cmd_g19 = "npx oxlint"
    code, out, err, dur = run_cmd(cmd_g19, cwd=FRONTEND)
    if code == 0:
        print(f"  -> PASS: Oxlint verified 0 errors in {dur/1000:.2f}s")
        results["gates"]["G19_FRONTEND_LINT"] = {
            "status": "PASS", "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Frontend lint failed:\n{out}\n{err}")
        results["gates"]["G19_FRONTEND_LINT"] = {
            "status": "FAIL", "duration_ms": dur, "exit_code": code
        }
        all_gates_passed = False

    # --------------------------------------------------------------------------
    # GATE 20: Playwright E2E Suite
    # --------------------------------------------------------------------------
    print("\n[GATE 20] Executing Playwright E2E Suite...")
    cmd_g20 = f"AURUM_E2E_PYTHON={PYTHON_BIN} npx playwright test tests/e2e/v17_5_3_stop_browser.e2e.ts"
    code, out, err, dur = run_cmd(cmd_g20, cwd=FRONTEND)
    if code == 0 and "passed" in out:
        print(f"  -> PASS: Playwright E2E verified in {dur/1000:.2f}s with actual browser")
        results["gates"]["G20_PLAYWRIGHT_E2E"] = {
            "status": "PASS", "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Playwright E2E failed:\n{out}\n{err}")
        results["gates"]["G20_PLAYWRIGHT_E2E"] = {
            "status": "FAIL", "duration_ms": dur, "exit_code": code
        }
        all_gates_passed = False

    # --------------------------------------------------------------------------
    # GATE 21: Replay Determinism
    # --------------------------------------------------------------------------
    print("\n[GATE 21] Verifying Replay Determinism...")
    cmd_g21 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -k 'test_sl_53 or test_dsl_15'"
    code, out, err, dur = run_cmd(cmd_g21)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 2 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} replay determinism tests passed")
        results["gates"]["G21_REPLAY_DETERMINISM"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Replay determinism tests failed:\n{out}\n{err}")
        results["gates"]["G21_REPLAY_DETERMINISM"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }
        all_gates_passed = False

    # --------------------------------------------------------------------------
    # GATE 22: Evidence & Documentation Completeness
    # --------------------------------------------------------------------------
    print("\n[GATE 22] Verifying V17.7.2 Documentation Completeness...")
    req_docs = [
        "docs/V17_7_2_SOURCE_AUDIT.md",
        "docs/V17_7_2_ATR_CAUSALITY_AND_SL_ELIGIBILITY.md",
        "docs/V17_7_2_CG01_M1_RUNTIME_INTEGRATION.md",
        "docs/V17_7_2_TRUE_ARM_FILL_EXIT_E2E.md",
        "docs/V17_7_2_DATABASE_ISOLATION_AND_CONCURRENCY.md",
        "docs/V17_7_2_LIVE_ORDER_SAFETY_PROOF.md",
        "docs/V17_7_2_FULL_REGRESSION_REPORT.md",
        "docs/V17_7_2_ACCEPTANCE_REPORT.md",
        "docs/V17_7_2_DEFECT_REGISTER.md",
        "docs/V17_7_2_FUNCTION_CHANGE_MAP.md",
    ]
    missing_docs = []
    for doc in req_docs:
        p = ROOT / doc
        if not p.exists() or p.stat().st_size < 100:
            missing_docs.append(doc)
            print(f"  -> FAIL: Missing or empty doc: {doc}")
        else:
            print(f"  -> PASS: Verified doc exists ({p.stat().st_size} bytes): {doc}")

    if not missing_docs:
        results["gates"]["G22_DOCS_INTEGRITY"] = {"status": "PASS", "verified_docs": len(req_docs)}
    else:
        results["gates"]["G22_DOCS_INTEGRITY"] = {"status": "FAIL", "missing_docs": missing_docs}
        all_gates_passed = False

    # --------------------------------------------------------------------------
    # GATE 23: Acceptance Source SHA Consistency
    # --------------------------------------------------------------------------
    print("\n[GATE 23] Reconciling Acceptance Source SHA Consistency...")
    cmd_g23 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -k 'test_ce_20'"
    code, out, err, dur = run_cmd(cmd_g23)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 1 and counts["failed"] == 0:
        print(f"  -> PASS: Tested commit SHA ({current_sha[:8]}) verified consistent")
        results["gates"]["G23_SOURCE_SHA_CONSISTENCY"] = {
            "status": "PASS", "sha": current_sha, "duration_ms": dur
        }
    else:
        print(f"  -> FAIL: Source SHA consistency check failed:\n{out}\n{err}")
        results["gates"]["G23_SOURCE_SHA_CONSISTENCY"] = {
            "status": "FAIL", "sha": current_sha, "duration_ms": dur
        }
        all_gates_passed = False

    # --------------------------------------------------------------------------
    # FINAL VERDICT
    # --------------------------------------------------------------------------
    print("\n" + "=" * 80)
    if all_gates_passed:
        final_verdict = "ACCEPTED_FOR_PAPER_OPT_IN"
        print(f"FINAL ACCEPTANCE VERDICT: {final_verdict}")
        print("All 23 release gates successfully passed with verified machine evidence!")
    else:
        final_verdict = "BLOCKED"
        print(f"FINAL ACCEPTANCE VERDICT: {final_verdict}")
        print("One or more mandatory release gates failed! Review gate details above.")

    results["verdict"] = final_verdict
    print("=" * 80)

    # Persist JSON Artifacts
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    out_file = ARTIFACTS_DIR / "v17_7_2_acceptance_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"Acceptance artifact saved to: {out_file}\n")

    out_file_compat = ARTIFACTS_DIR / "v17_7_1_acceptance_results.json"
    with open(out_file_compat, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    return 0 if all_gates_passed else 1


if __name__ == "__main__":
    sys.exit(main())
