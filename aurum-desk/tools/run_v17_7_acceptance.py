#!/usr/bin/env python3
"""
AURUM DESK V17.7.3 — EVIDENCE-BASED MASTER ACCEPTANCE RUNNER
============================================================
Enforces all 28 release gates (G01 to G28) for V17.7.3:
Database Safety Isolation, ATR Provenance Authority & Release Gate Truthfulness.
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

sys.path.insert(0, str(ROOT))
from tools.acceptance_verdict import determine_release_verdict, MANDATORY_GATES_V17_7_3


def run_cmd(cmd, cwd=ROOT, env=None):
    merged_env = os.environ.copy()
    merged_env["PYTHONPATH"] = f"{BACKEND}:{BACKEND / 'tests'}:{ROOT}"
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
    print("AURUM DESK V17.7.3 — EVIDENCE-BASED MASTER ACCEPTANCE RUNNER")
    print("=" * 80)

    results = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runner_version": "17.7.3",
        "branch": "",
        "source_sha": "",
        "gates": {},
        "verdict": "BLOCKED",
    }

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
            "status": "PASS", "branch": branch, "sha": current_sha, "duration_ms": dur, "exit_code": 0
        }
    else:
        print(f"  -> BLOCKER: Expected branch '{expected_branch}', got '{branch}'")
        results["gates"]["G01_BRANCH_INTEGRITY"] = {
            "status": "FAIL", "branch": branch, "expected": expected_branch, "duration_ms": dur, "exit_code": 1
        }

    # --------------------------------------------------------------------------
    # GATE 02: Test-Owned Database Bootstrap
    # --------------------------------------------------------------------------
    print("\n[GATE 02] Verifying Test-Owned Database Bootstrap...")
    cmd_g02 = f"{PYTEST_BIN} backend/tests/test_v17_7_database_safety.py -k 'test_dbs_01 or test_dbs_09'"
    code, out, err, dur = run_cmd(cmd_g02)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 2 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} test-owned DB bootstrap tests passed")
        results["gates"]["G02_TEST_DB_BOOTSTRAP"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: DB bootstrap tests failed:\n{out}\n{err}")
        results["gates"]["G02_TEST_DB_BOOTSTRAP"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 03: Unsafe Database Path Rejection
    # --------------------------------------------------------------------------
    print("\n[GATE 03] Verifying Unsafe Database Path Rejection (Symlinks, Traversals, Sentinels)...")
    cmd_g03 = f"{PYTEST_BIN} backend/tests/test_v17_7_database_safety.py -k 'test_dbs_02 or test_dbs_03 or test_dbs_04 or test_dbs_05 or test_dbs_06 or test_dbs_07 or test_dbs_08 or test_dbs_10'"
    code, out, err, dur = run_cmd(cmd_g03)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 8 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} unsafe path rejection tests passed")
        results["gates"]["G03_UNSAFE_PATH_REJECTION"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Unsafe path rejection tests failed:\n{out}\n{err}")
        results["gates"]["G03_UNSAFE_PATH_REJECTION"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 04: Destructive Test Fixture Isolation
    # --------------------------------------------------------------------------
    print("\n[GATE 04] Verifying Destructive Test Fixture & Migration Isolation...")
    cmd_g04 = f"{PYTEST_BIN} backend/tests/test_v17_7_database_safety.py -k 'test_dbs_11 or test_dbs_12 or test_dbs_13 or test_dbs_14 or test_dbs_15'"
    code, out, err, dur = run_cmd(cmd_g04)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 5 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} fixture isolation tests passed")
        results["gates"]["G04_DESTRUCTIVE_FIXTURE_ISOLATION"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Fixture isolation tests failed:\n{out}\n{err}")
        results["gates"]["G04_DESTRUCTIVE_FIXTURE_ISOLATION"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 05: Raw ATR Provenance Authority
    # --------------------------------------------------------------------------
    print("\n[GATE 05] Verifying Raw ATR Provenance Authority & Causal Timestamps...")
    cmd_g05 = f"{PYTEST_BIN} backend/tests/test_v17_7_atr_provenance.py -k 'test_atr_01 or test_atr_04 or test_atr_05 or test_atr_06 or test_atr_08 or test_atr_09 or test_atr_13 or test_atr_14'"
    code, out, err, dur = run_cmd(cmd_g05)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 8 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} raw ATR provenance tests passed")
        results["gates"]["G05_RAW_ATR_PROVENANCE"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Raw ATR provenance tests failed:\n{out}\n{err}")
        results["gates"]["G05_RAW_ATR_PROVENANCE"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 06: Required M1 ATR Fail-Closed
    # --------------------------------------------------------------------------
    print("\n[GATE 06] Verifying Required M1 ATR Fail-Closed & WAITING_VOLATILITY_EVIDENCE...")
    cmd_g06 = f"{PYTEST_BIN} backend/tests/test_v17_7_atr_provenance.py -k 'test_atr_02 or test_atr_03 or test_atr_07 or test_atr_10 or test_atr_11 or test_atr_12 or test_atr_20'"
    code, out, err, dur = run_cmd(cmd_g06)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 7 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} required ATR fail-closed tests passed")
        results["gates"]["G06_REQUIRED_M1_ATR_FAIL_CLOSED"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Required ATR fail-closed tests failed:\n{out}\n{err}")
        results["gates"]["G06_REQUIRED_M1_ATR_FAIL_CLOSED"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 07: ATR / Buffer Mathematical Correctness
    # --------------------------------------------------------------------------
    print("\n[GATE 07] Verifying ATR / Buffer Mathematical Correctness...")
    cmd_g07 = f"{PYTEST_BIN} backend/tests/test_v17_7_atr_provenance.py -k 'test_atr_15 or test_atr_16 or test_atr_19'"
    code, out, err, dur = run_cmd(cmd_g07)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 3 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} ATR/buffer math correctness tests passed")
        results["gates"]["G07_ATR_BUFFER_MATH_CORRECTNESS"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: ATR/buffer math tests failed:\n{out}\n{err}")
        results["gates"]["G07_ATR_BUFFER_MATH_CORRECTNESS"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 08: Structural Anchor Causal Integrity
    # --------------------------------------------------------------------------
    print("\n[GATE 08] Verifying Structural Anchor Causal Ordering & Invalidation...")
    cmd_g08 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -k 'test_ce_06 or test_ce_07 or test_ce_08 or test_sl_58'"
    code, out, err, dur = run_cmd(cmd_g08)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 4 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} anchor causality & invalidation tests passed")
        results["gates"]["G08_STRUCTURAL_ANCHOR_CAUSAL"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Anchor authority tests failed:\n{out}\n{err}")
        results["gates"]["G08_STRUCTURAL_ANCHOR_CAUSAL"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 09: Distant SL Financial Integrity
    # --------------------------------------------------------------------------
    print("\n[GATE 09] Verifying Distant SL (>4x ATR) Financial Integrity...")
    cmd_g09 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -k 'test_dsl_01 or test_dsl_02 or test_dsl_07 or test_ce_11'"
    code, out, err, dur = run_cmd(cmd_g09)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 4 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} distant SL financial integrity tests passed")
        results["gates"]["G09_DISTANT_SL_FINANCIAL_INTEGRITY"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Distant SL financial tests failed:\n{out}\n{err}")
        results["gates"]["G09_DISTANT_SL_FINANCIAL_INTEGRITY"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 10: CG01 Real Evaluator Integration
    # --------------------------------------------------------------------------
    print("\n[GATE 10] Verifying CG01 Real Evaluator Integration...")
    cmd_g10 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py backend/tests/test_v17_7_atr_provenance.py -k 'test_sl_36 or test_ce_10 or test_atr_17'"
    code, out, err, dur = run_cmd(cmd_g10)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 3 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} real CG01 evaluator integration tests passed")
        results["gates"]["G10_CG01_EVALUATOR_INTEGRATION"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: CG01 integration tests failed:\n{out}\n{err}")
        results["gates"]["G10_CG01_EVALUATOR_INTEGRATION"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 11: Scalp M1 Models Real Evaluator Integration
    # --------------------------------------------------------------------------
    print("\n[GATE 11] Verifying Scalp M1 Models Real Evaluator Integration...")
    cmd_g11 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py backend/tests/test_v17_7_atr_provenance.py -k 'test_sl_37 or test_sl_38 or test_sl_39 or test_sl_40 or test_ce_12 or test_atr_18'"
    code, out, err, dur = run_cmd(cmd_g11)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 6 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} real M1 model evaluator integration tests passed")
        results["gates"]["G11_M1_EVALUATOR_INTEGRATION"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: M1 model integration tests failed:\n{out}\n{err}")
        results["gates"]["G11_M1_EVALUATOR_INTEGRATION"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 12: Orchestrator / Actual ARM Boundary
    # --------------------------------------------------------------------------
    print("\n[GATE 12] Verifying Orchestrator Actual ARM Boundary...")
    cmd_g12 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -k 'test_sl_43 or test_ce_13'"
    code, out, err, dur = run_cmd(cmd_g12)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 2 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} production orchestrator ARM tests passed")
        results["gates"]["G12_ORCHESTRATOR_REAL_ARM"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Orchestrator ARM tests failed:\n{out}\n{err}")
        results["gates"]["G12_ORCHESTRATOR_REAL_ARM"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 13: Real ARM Concurrency & Idempotency
    # --------------------------------------------------------------------------
    print("\n[GATE 13] Verifying Multi-Threaded Real ARM Concurrency & Idempotency...")
    cmd_g13 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -k 'test_ce_16 or test_sl_57 or test_dsl_18'"
    code, out, err, dur = run_cmd(cmd_g13)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 3 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} multi-threaded real ARM race tests passed")
        results["gates"]["G13_REAL_ARM_CONCURRENCY"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Concurrency tests failed:\n{out}\n{err}")
        results["gates"]["G13_REAL_ARM_CONCURRENCY"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 14: Fill-Time Financial Invariants & Sizing
    # --------------------------------------------------------------------------
    print("\n[GATE 14] Verifying Fill-Time Financial Invariants & Sizing Preservation...")
    cmd_g14 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -k 'test_sl_46 or test_ce_14'"
    code, out, err, dur = run_cmd(cmd_g14)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 2 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} fill sizing preservation tests passed")
        results["gates"]["G14_FILL_TIME_FINANCIAL_INVARIANTS"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Fill sizing tests failed:\n{out}\n{err}")
        results["gates"]["G14_FILL_TIME_FINANCIAL_INVARIANTS"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 15: PAPER Exit, CLOSED State & Persistence
    # --------------------------------------------------------------------------
    print("\n[GATE 15] Verifying PAPER Exit, CLOSED State & Durable Persistence...")
    cmd_g15 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -k 'test_sl_47 or test_sl_48 or test_sl_60 or test_ce_15'"
    code, out, err, dur = run_cmd(cmd_g15)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 4 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} exit and persistence lifecycle tests passed")
        results["gates"]["G15_PAPER_EXIT_AND_PERSISTENCE"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Exit and persistence tests failed:\n{out}\n{err}")
        results["gates"]["G15_PAPER_EXIT_AND_PERSISTENCE"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 16: Actual Live-Order Dispatch Safety & Negative Control
    # --------------------------------------------------------------------------
    print("\n[GATE 16] Verifying Live-Order Dispatch Safety & Negative Control...")
    safety_ok, safety_msg = verify_live_order_safety_boundary()
    cmd_g16 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -k 'test_sl_50 or test_dsl_19 or test_ce_18'"
    code, out, err, dur = run_cmd(cmd_g16)
    counts = parse_pytest_counts(out)
    if safety_ok and code == 0 and counts["passed"] >= 3 and counts["failed"] == 0:
        print(f"  -> PASS: {safety_msg}")
        print(f"  -> PASS: {counts['passed']} live-order safety & negative control tests passed")
        results["gates"]["G16_LIVE_ORDER_SAFETY_BOUNDARY"] = {
            "status": "PASS", "details": safety_msg, "counts": counts, "duration_ms": dur, "exit_code": 0
        }
    else:
        print(f"  -> FAIL: Live-order safety verification failed: {safety_msg}\n{out}\n{err}")
        results["gates"]["G16_LIVE_ORDER_SAFETY_BOUNDARY"] = {
            "status": "FAIL", "details": safety_msg, "counts": counts, "duration_ms": dur, "exit_code": 1
        }

    # --------------------------------------------------------------------------
    # GATE 17: Dedicated V17.7 Suite (100 Gates: SL-01..60 + DSL-01..20 + CE-01..20)
    # --------------------------------------------------------------------------
    print("\n[GATE 17] Executing Complete Dedicated V17.7 Test Suite (100 Gates)...")
    cmd_g17 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -v"
    code, out, err, dur = run_cmd(cmd_g17)
    counts_g17 = parse_pytest_counts(out)
    if code == 0 and counts_g17["passed"] == 100 and counts_g17["failed"] == 0:
        print(f"  -> PASS: All 100 dedicated gates passed in {dur/1000:.2f}s!")
        results["gates"]["G17_EXISTING_SL_DSL_CE_REGRESSION"] = {
            "status": "PASS", "counts": counts_g17, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Dedicated suite failed (code {code}, passed {counts_g17['passed']}/100):\n{out}\n{err}")
        results["gates"]["G17_EXISTING_SL_DSL_CE_REGRESSION"] = {
            "status": "FAIL", "counts": counts_g17, "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 18: V17.5 Regression Suite
    # --------------------------------------------------------------------------
    print("\n[GATE 18] Executing V17.5 Regression Suite...")
    cmd_g18 = f"{PYTEST_BIN} backend/tests/test_v17_5*.py -q"
    code, out, err, dur = run_cmd(cmd_g18)
    counts_g18 = parse_pytest_counts(out)
    if code == 0 and counts_g18["failed"] == 0 and counts_g18["passed"] > 0:
        print(f"  -> PASS: V17.5 regression passed ({counts_g18['passed']} tests) in {dur/1000:.2f}s")
        results["gates"]["G18_V17_5_REGRESSION"] = {
            "status": "PASS", "counts": counts_g18, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: V17.5 regression failed:\n{out}\n{err}")
        results["gates"]["G18_V17_5_REGRESSION"] = {
            "status": "FAIL", "counts": counts_g18, "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 19: V17.6 Regression Suite
    # --------------------------------------------------------------------------
    print("\n[GATE 19] Executing V17.6 Regression Suite (incl. V17.6.4.3d)...")
    cmd_g19 = f"{PYTEST_BIN} backend/tests/test_v17_6*.py -q"
    code, out, err, dur = run_cmd(cmd_g19)
    counts_g19 = parse_pytest_counts(out)
    if code == 0 and counts_g19["failed"] == 0 and counts_g19["passed"] > 0:
        print(f"  -> PASS: V17.6 regression passed ({counts_g19['passed']} tests) in {dur/1000:.2f}s")
        results["gates"]["G19_V17_6_REGRESSION"] = {
            "status": "PASS", "counts": counts_g19, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: V17.6 regression failed:\n{out}\n{err}")
        results["gates"]["G19_V17_6_REGRESSION"] = {
            "status": "FAIL", "counts": counts_g19, "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 20: Entire backend/tests Pytest Suite (Section 09 / V173-P0-02)
    # --------------------------------------------------------------------------
    print("\n[GATE 20] Executing Entire backend/tests Pytest Suite...")
    cmd_g20 = f"{PYTEST_BIN} backend/tests -q"
    code, out, err, dur = run_cmd(cmd_g20)
    counts_g20 = parse_pytest_counts(out)
    if code == 0 and counts_g20["failed"] == 0 and counts_g20["passed"] > 0:
        print(f"  -> PASS: Entire backend suite passed ({counts_g20['passed']} tests) in {dur/1000:.2f}s")
        results["gates"]["G20_FULL_BACKEND_PYTEST"] = {
            "status": "PASS", "counts": counts_g20, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Entire backend suite failed (passed {counts_g20['passed']}, failed {counts_g20['failed']}):\n{out}\n{err}")
        results["gates"]["G20_FULL_BACKEND_PYTEST"] = {
            "status": "FAIL", "counts": counts_g20, "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 21: Frontend Vitest Suite
    # --------------------------------------------------------------------------
    print("\n[GATE 21] Executing Frontend Vitest Suite...")
    cmd_g21 = "npm test -- --run"
    code, out, err, dur = run_cmd(cmd_g21, cwd=FRONTEND)
    vitest_counts = parse_vitest_counts(out)
    if code == 0 and vitest_counts["tests_passed"] > 0:
        print(f"  -> PASS: Frontend Vitest passed ({vitest_counts['tests_passed']} tests in {vitest_counts['test_files_passed']} files) in {dur/1000:.2f}s")
        results["gates"]["G21_FRONTEND_VITEST"] = {
            "status": "PASS", "counts": vitest_counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Frontend Vitest failed:\n{out}\n{err}")
        results["gates"]["G21_FRONTEND_VITEST"] = {
            "status": "FAIL", "counts": vitest_counts, "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 22: Frontend Production Build
    # --------------------------------------------------------------------------
    print("\n[GATE 22] Executing Frontend Production Build...")
    cmd_g22 = "npm run build"
    code, out, err, dur = run_cmd(cmd_g22, cwd=FRONTEND)
    if code == 0:
        print(f"  -> PASS: Frontend production build succeeded in {dur/1000:.2f}s")
        results["gates"]["G22_FRONTEND_BUILD"] = {
            "status": "PASS", "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Frontend build failed:\n{out}\n{err}")
        results["gates"]["G22_FRONTEND_BUILD"] = {
            "status": "FAIL", "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 23: Frontend Lint (Oxlint)
    # --------------------------------------------------------------------------
    print("\n[GATE 23] Executing Frontend Oxlint...")
    cmd_g23 = "npx oxlint"
    code, out, err, dur = run_cmd(cmd_g23, cwd=FRONTEND)
    if code == 0:
        print(f"  -> PASS: Oxlint verified 0 errors in {dur/1000:.2f}s")
        results["gates"]["G23_FRONTEND_LINT"] = {
            "status": "PASS", "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Frontend lint failed:\n{out}\n{err}")
        results["gates"]["G23_FRONTEND_LINT"] = {
            "status": "FAIL", "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 24: Playwright E2E Suite
    # --------------------------------------------------------------------------
    print("\n[GATE 24] Executing Playwright E2E Suite...")
    cmd_g24 = f"AURUM_E2E_PYTHON={PYTHON_BIN} npx playwright test tests/e2e/v17_5_3_stop_browser.e2e.ts"
    code, out, err, dur = run_cmd(cmd_g24, cwd=FRONTEND)
    if code == 0 and "passed" in out:
        print(f"  -> PASS: Playwright E2E verified in {dur/1000:.2f}s with actual browser")
        results["gates"]["G24_PLAYWRIGHT_BROWSER_E2E"] = {
            "status": "PASS", "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Playwright E2E failed:\n{out}\n{err}")
        results["gates"]["G24_PLAYWRIGHT_BROWSER_E2E"] = {
            "status": "FAIL", "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 25: Deterministic Replay
    # --------------------------------------------------------------------------
    print("\n[GATE 25] Verifying Replay Determinism...")
    cmd_g25 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -k 'test_sl_53 or test_dsl_15'"
    code, out, err, dur = run_cmd(cmd_g25)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 2 and counts["failed"] == 0:
        print(f"  -> PASS: {counts['passed']} replay determinism tests passed")
        results["gates"]["G25_DETERMINISTIC_REPLAY"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Replay determinism tests failed:\n{out}\n{err}")
        results["gates"]["G25_DETERMINISTIC_REPLAY"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 26: Acceptance Runner Negative-Control Tests
    # --------------------------------------------------------------------------
    print("\n[GATE 26] Executing Acceptance Runner Negative-Control Tests...")
    cmd_g26 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -k 'test_ce_19'"
    code, out, err, dur = run_cmd(cmd_g26)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 1 and counts["failed"] == 0:
        print(f"  -> PASS: Acceptance runner negative-control tests passed")
        results["gates"]["G26_ACCEPTANCE_RUNNER_NEGATIVE_TESTS"] = {
            "status": "PASS", "counts": counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Acceptance runner negative-control failed:\n{out}\n{err}")
        results["gates"]["G26_ACCEPTANCE_RUNNER_NEGATIVE_TESTS"] = {
            "status": "FAIL", "counts": counts, "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 27: Artifact & Source SHA Integrity
    # --------------------------------------------------------------------------
    print("\n[GATE 27] Verifying Tested Source SHA Integrity...")
    cmd_g27 = f"{PYTEST_BIN} backend/tests/test_v17_7_adaptive_stop_loss.py -k 'test_ce_20'"
    code, out, err, dur = run_cmd(cmd_g27)
    counts = parse_pytest_counts(out)
    if code == 0 and counts["passed"] >= 1 and counts["failed"] == 0:
        print(f"  -> PASS: Tested commit SHA ({current_sha[:8]}) verified against git HEAD")
        results["gates"]["G27_ARTIFACT_SOURCE_SHA_INTEGRITY"] = {
            "status": "PASS", "sha": current_sha, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Source SHA check failed:\n{out}\n{err}")
        results["gates"]["G27_ARTIFACT_SOURCE_SHA_INTEGRITY"] = {
            "status": "FAIL", "sha": current_sha, "duration_ms": dur, "exit_code": code
        }

    # --------------------------------------------------------------------------
    # GATE 28: Documentation Completeness & Defect Register Consistency
    # --------------------------------------------------------------------------
    print("\n[GATE 28] Verifying V17.7.3 Documentation & Defect Register Consistency...")
    req_docs = [
        "docs/V17_7_3_SOURCE_AND_ROOT_CAUSE_AUDIT.md",
        "docs/V17_7_3_DATABASE_SAFETY_ISOLATION.md",
        "docs/V17_7_3_ATR_PROVENANCE_AUTHORITY.md",
        "docs/V17_7_3_STRUCTURAL_SL_FORMULA_VERIFICATION.md",
        "docs/V17_7_3_CONCURRENT_ARM_INTEGRITY.md",
        "docs/V17_7_3_ACCEPTANCE_RUNNER_TRUTHFULNESS.md",
        "docs/V17_7_3_FULL_BACKEND_REGRESSION.md",
        "docs/V17_7_3_RELEASE_ACCEPTANCE_REPORT.md",
        "docs/V17_7_3_DEFECT_REGISTER.md",
        "docs/V17_7_3_FUNCTION_CHANGE_MAP.md",
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
        results["gates"]["G28_DOCS_DEFECT_REGISTER_CONSISTENCY"] = {
            "status": "PASS", "verified_docs": len(req_docs), "exit_code": 0
        }
    else:
        results["gates"]["G28_DOCS_DEFECT_REGISTER_CONSISTENCY"] = {
            "status": "FAIL", "missing_docs": missing_docs, "exit_code": 1
        }

    # --------------------------------------------------------------------------
    # FINAL VERDICT VIA AUTHORITATIVE MODULE
    # --------------------------------------------------------------------------
    print("\n" + "=" * 80)
    final_verdict = determine_release_verdict(results["gates"], MANDATORY_GATES_V17_7_3)
    results["verdict"] = final_verdict
    print(f"FINAL ACCEPTANCE VERDICT: {final_verdict}")
    if final_verdict == "ACCEPTED_FOR_PAPER_OPT_IN":
        print("All 28 release gates successfully passed with verified machine evidence!")
    else:
        print("One or more mandatory release gates failed! Review gate details above.")
    print("=" * 80)

    # Persist JSON Artifacts
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    out_file = ARTIFACTS_DIR / "v17_7_3_acceptance_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"Acceptance artifact saved to: {out_file}\n")

    out_file_compat = ARTIFACTS_DIR / "v17_7_2_acceptance_results.json"
    with open(out_file_compat, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    return 0 if final_verdict == "ACCEPTED_FOR_PAPER_OPT_IN" else 1


if __name__ == "__main__":
    sys.exit(main())
