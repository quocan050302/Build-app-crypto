#!/usr/bin/env python3
"""
AURUM DESK V17.8 — EVIDENCE-BASED MASTER ACCEPTANCE RUNNER
============================================================
Enforces all 42 release gates (G01 to G42) for V17.8:
ICT Unicorn-Assisted Structural SL/TP & Market Noise Intelligence.
Branch: feat/v17-8-unicorn-structural-sl-tp-intelligence
"""

import glob
import hashlib
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
ARTIFACTS_DIR = ROOT / "docs" / "artifacts"

# Interpreter resolution (Windows & POSIX)
if (BACKEND / "venv" / "Scripts" / "python.exe").exists():
    PYTHON_BIN = str(BACKEND / "venv" / "Scripts" / "python.exe")
elif (BACKEND / "venv" / "bin" / "python").exists():
    PYTHON_BIN = str(BACKEND / "venv" / "bin" / "python")
else:
    PYTHON_BIN = sys.executable

NPM_BIN = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
NPX_BIN = shutil.which("npx.cmd") or shutil.which("npx") or "npx"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

RUN_ID = os.getenv("AURUM_TEST_RUN_ID") or f"run_v17_8_{int(time.time()*1000)}_{os.getpid()}"

# P0 GUARD: Set testing environment flags in runner process BEFORE any backend import
os.environ["TESTING"] = "1"
os.environ["AURUM_TEST_RUN_ID"] = RUN_ID

# Bootstrap test root in runner process and assert test DB is isolated
import database
test_root = database.get_or_create_test_root()
database.assert_test_database_isolated(database.DB_PATH)

from tools.acceptance_verdict import determine_release_verdict, MANDATORY_GATES_V17_8
from tools.artifact_verifier import (
    build_source_manifest,
    write_acceptance_artifacts,
    verify_acceptance_bundle,
)

RUN_DIR = ARTIFACTS_DIR / "v17_8" / RUN_ID
REPORTS_DIR = RUN_DIR / "reports"
LOGS_DIR = RUN_DIR / "logs"


def init_run_directories():
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def find_free_port(start_port: int = 8765) -> int:
    import socket
    for port in range(start_port, start_port + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start_port


def run_cmd(cmd_args, cwd=ROOT, env=None, log_name=None, timeout=600):
    merged_env = os.environ.copy()
    merged_env["PYTHONPATH"] = os.pathsep.join([str(BACKEND), str(BACKEND / "tests"), str(ROOT)])
    merged_env["TESTING"] = "1"
    merged_env["AURUM_TEST_RUN_ID"] = RUN_ID
    if env:
        merged_env.update(env)

    start_t = time.perf_counter()
    try:
        res = subprocess.run(
            cmd_args,
            shell=False,
            cwd=cwd,
            env=merged_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        duration_ms = int((time.perf_counter() - start_t) * 1000)
        stdout, stderr, code = res.stdout, res.stderr, res.returncode
    except subprocess.TimeoutExpired as te:
        duration_ms = int((time.perf_counter() - start_t) * 1000)
        stdout = te.stdout or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        stderr = te.stderr or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        stderr += f"\nTIMEOUT: Command exceeded {timeout}s limit"
        code = -1
    except Exception as e:
        duration_ms = int((time.perf_counter() - start_t) * 1000)
        stdout = ""
        stderr = str(e)
        code = 1

    if log_name:
        log_file = LOGS_DIR / f"{log_name}.log"
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(f"COMMAND: {' '.join(str(c) for c in cmd_args)}\n")
            f.write(f"CWD: {cwd}\n")
            f.write(f"DURATION_MS: {duration_ms}\n")
            f.write(f"EXIT_CODE: {code}\n")
            f.write("--- STDOUT ---\n")
            f.write(stdout)
            f.write("\n--- STDERR ---\n")
            f.write(stderr)

    return code, stdout, stderr, duration_ms


def parse_pytest_counts(output_text: str, report_xml: Path) -> dict:
    if report_xml.exists() and report_xml.stat().st_size > 0:
        try:
            tree = ET.parse(report_xml)
            root = tree.getroot()
            suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
            total_tests = 0
            total_failures = 0
            total_errors = 0
            total_skipped = 0
            if suites:
                for s in suites:
                    total_tests += int(s.get("tests", 0))
                    total_failures += int(s.get("failures", 0))
                    total_errors += int(s.get("errors", 0))
                    total_skipped += int(s.get("skipped", 0))
            else:
                total_tests = int(root.get("tests", 0))
                total_failures = int(root.get("failures", 0))
                total_errors = int(root.get("errors", 0))
                total_skipped = int(root.get("skipped", 0))

            direct_failures = len(root.findall(".//failure"))
            direct_errors = len(root.findall(".//error"))
            total_failures = max(total_failures, direct_failures)
            total_errors = max(total_errors, direct_errors)
            passed = max(0, total_tests - total_failures - total_errors - total_skipped)

            return {
                "collected": total_tests,
                "passed": passed,
                "failed": total_failures,
                "skipped": total_skipped,
                "errors": total_errors,
            }
        except Exception:
            pass

    passed_m = re.search(r"(\d+)\s+passed", output_text)
    failed_m = re.search(r"(\d+)\s+failed", output_text)
    skipped_m = re.search(r"(\d+)\s+skipped", output_text)
    errors_m = re.search(r"(\d+)\s+error", output_text)

    passed = int(passed_m.group(1)) if passed_m else 0
    failed = int(failed_m.group(1)) if failed_m else 0
    skipped = int(skipped_m.group(1)) if skipped_m else 0
    errors = int(errors_m.group(1)) if errors_m else 0

    return {
        "collected": passed + failed + skipped + errors,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "errors": errors,
    }


def parse_vitest_counts(output_text: str) -> dict:
    clean_text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', output_text)
    tests_m = re.search(r"Tests\s+(\d+)\s+passed\s*(?:\((\d+)\))?", clean_text)
    files_m = re.search(r"Test Files\s+(\d+)\s+passed\s*(?:\((\d+)\))?", clean_text)
    passed_tests = int(tests_m.group(1)) if tests_m else 0
    passed_files = int(files_m.group(1)) if files_m else 0
    failed_m = re.search(r"(\d+)\s+failed", clean_text)
    failed = int(failed_m.group(1)) if failed_m else 0
    return {
        "collected": passed_tests + failed,
        "passed": passed_tests,
        "failed": failed,
        "skipped": 0,
        "errors": 0,
        "test_files_passed": passed_files,
    }


def parse_playwright_counts(output_text: str) -> dict:
    clean_text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', output_text)
    passed_m = re.search(r"(\d+)\s+passed", clean_text)
    failed_m = re.search(r"(\d+)\s+failed", clean_text)
    skipped_m = re.search(r"(\d+)\s+skipped", clean_text)
    passed = int(passed_m.group(1)) if passed_m else 0
    failed = int(failed_m.group(1)) if failed_m else 0
    skipped = int(skipped_m.group(1)) if skipped_m else 0
    return {
        "collected": passed + failed + skipped,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "errors": 0,
    }


def verify_live_order_safety_boundary():
    """
    V17.8 Safety Invariant:
    - Instruments network boundaries with active spy.
    - Exercises negative control verifying forbidden sink detection.
    - Exercises legitimate production PAPER lifecycle asserting zero exchange orders.
    """
    try:
        from models import Base
        from database import SessionLocal, engine, run_schema_migrations, assert_test_database_isolated, DB_PATH
        engine_target = getattr(engine.url, "database", None) or DB_PATH
        assert_test_database_isolated(engine_target)
        Base.metadata.create_all(bind=engine)
        run_schema_migrations(engine)

        from paper_broker import PaperBroker
        from services.execution_coordinator import LIVE_EXCHANGE_ORDERS_SENT
        from tests.test_v17_7_adaptive_stop_loss import make_real_armed_candidate_order
        from services.trade_lifecycle_service import TradeLifecycleService

        pb_methods = [m[0] for m in inspect.getmembers(PaperBroker, predicate=inspect.isfunction)]
        for forbidden_method in ("place_live_order", "submit_exchange_order", "send_order", "execute_live_trade"):
            if forbidden_method in pb_methods:
                return False, f"Forbidden live method {forbidden_method} found in PaperBroker!"

        spy = Mock()
        def _forbidden_sink(*args, **kwargs):
            spy(*args, **kwargs)
            raise RuntimeError("FORBIDDEN_LIVE_ORDER_SUBMISSION: Live exchange order attempt detected!")

        with patch("httpx.Client.post", side_effect=_forbidden_sink), \
             patch("httpx.AsyncClient.post", side_effect=_forbidden_sink):

            # 1. Negative control proof
            import httpx
            client = httpx.Client()
            intercepted = False
            try:
                client.post("https://api.binance.com/fapi/v1/order", json={"symbol": "XAUUSDT"})
            except RuntimeError as exc:
                if "FORBIDDEN_LIVE_ORDER_SUBMISSION" in str(exc):
                    intercepted = True
            if not intercepted or spy.call_count != 1:
                return False, "Negative control failed: live exchange order was not intercepted by boundary spy!"

            # Reset spy for legitimate PAPER flow test
            spy.reset_mock()

            # 2. Legitimate PAPER lifecycle execution under active spy
            import models
            import crud
            db = SessionLocal()
            try:
                session_bind = db.get_bind()
                bind_target = getattr(session_bind.url, "database", None) if hasattr(session_bind, "url") else DB_PATH
                assert_test_database_isolated(bind_target)

                db.query(models.PaperOrder).delete()
                db.query(models.PaperObservation).delete()
                db.query(models.WatchSetup).delete()
                audit = crud.get_or_create_today_audit(db)
                audit.fills_count = 0
                audit.realized_pnl_today = 0.0
                audit.current_equity = 1000.0
                audit.initial_equity = 1000.0
                audit.is_blocked = False
                audit.block_reason = None
                audit.cooldown_until = None
                db.commit()

                cand, armed_order, now_ms = make_real_armed_candidate_order(
                    db, mode="PAPER_STRUCTURAL", order_id_override=f"g16_{RUN_ID[:12]}"
                )
                if not armed_order or armed_order.state != "armed":
                    return False, "Legitimate PAPER candidate failed to ARM under boundary instrumentation"
                db.commit()

                filled = TradeLifecycleService.execute_fill(
                    db, armed_order, fill_price=armed_order.planned_entry, calc_result=cand.calculation, now_ms=now_ms + 1000
                )
                if not filled or filled.state != "paper_open":
                    return False, "Legitimate PAPER order failed to transition to paper_open"
                db.commit()

                closed = TradeLifecycleService.execute_close(
                    db, order_id=armed_order.id, exit_price=armed_order.take_profit, exit_cause="TAKE_PROFIT", now_ms=now_ms + 5000
                )
                if not closed or closed.state != "closed":
                    return False, "Legitimate PAPER order failed to transition to closed"
                db.commit()
            finally:
                db.close()

            # 3. Assert zero live exchange orders sent and zero outbound calls made during paper flow
            if LIVE_EXCHANGE_ORDERS_SENT != 0:
                return False, f"LIVE_EXCHANGE_ORDERS_SENT was non-zero: {LIVE_EXCHANGE_ORDERS_SENT}"
            if spy.call_count != 0:
                return False, f"Forbidden outbound HTTP requests detected during PAPER flow: {spy.call_count}"

        return True, "Verified 0 live outbound calls with active HTTP spy, negative control exercised, and clean PAPER flow."
    except Exception as exc:
        return False, f"Exception during safety boundary verification: {exc}"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    print("=" * 80)
    print("AURUM DESK V17.8 — EVIDENCE-BASED MASTER ACCEPTANCE RUNNER")
    print(f"Run ID: {RUN_ID}")
    print("=" * 80)

    init_run_directories()

    results = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runner_version": "17.8.0",
        "run_id": RUN_ID,
        "branch": "",
        "source_sha": "",
        "gates": {},
        "verdict": "BLOCKED",
    }

    # --------------------------------------------------------------------------
    # GATE 01: Branch & Source Integrity
    # --------------------------------------------------------------------------
    print("\n[GATE 01] Verifying Working Git Branch & Source Integrity...")
    code, stdout, _, dur = run_cmd(["git", "branch", "--show-current"], log_name="g01_branch")
    branch = stdout.strip()
    results["branch"] = branch
    expected_branch = "feat/v17-8-unicorn-structural-sl-tp-intelligence"

    code_sha, sha_out, _, _ = run_cmd(["git", "rev-parse", "HEAD"], log_name="g01_sha")
    current_sha = sha_out.strip()
    results["source_sha"] = current_sha

    # Verify ancestry from master
    code_base, base_out, _, _ = run_cmd(["git", "merge-base", "HEAD", "origin/master"], log_name="g01_base")
    merge_base = base_out.strip()

    source_manifest = build_source_manifest(ROOT)

    if branch == expected_branch and len(current_sha) == 40 and source_manifest["files_count"] > 0:
        print(f"  -> PASS: Verified branch '{branch}' (HEAD: {current_sha[:8]}) | merge-base: {merge_base[:8]} | {source_manifest['files_count']} files hashed")
        results["gates"]["G01_BRANCH_INTEGRITY"] = {
            "status": "PASS", "gate_type": "command", "branch": branch, "sha": current_sha,
            "merge_base": merge_base, "composite_sha": source_manifest["composite_sha"],
            "duration_ms": dur, "exit_code": 0
        }
    else:
        print(f"  -> BLOCKER: Expected branch '{expected_branch}', got '{branch}'")
        results["gates"]["G01_BRANCH_INTEGRITY"] = {
            "status": "FAIL", "gate_type": "command", "branch": branch, "expected": expected_branch,
            "duration_ms": dur, "exit_code": 1
        }

    # --------------------------------------------------------------------------
    # Helper for running pytest gates
    # --------------------------------------------------------------------------
    def run_pytest_gate(gate_id: str, test_files: list, filter_expr: str, min_passed: int, label: str, timeout: int = 600):
        print(f"\n[{gate_id}] Verifying {label}...")
        report_xml = REPORTS_DIR / f"{gate_id.lower()}.xml"
        cmd = [PYTHON_BIN, "-m", "pytest", "-q"] + test_files + [f"--junitxml={str(report_xml)}"]
        if filter_expr:
            cmd += ["-k", filter_expr]

        code, out, err, dur = run_cmd(cmd, log_name=gate_id.lower(), timeout=timeout)
        counts = parse_pytest_counts(out, report_xml)
        passed = counts.get("passed", 0)
        failed = counts.get("failed", 0)
        errors = counts.get("errors", 0)

        if code == 0 and passed >= min_passed and failed == 0 and errors == 0:
            print(f"  -> PASS: {passed} tests passed (0 failed, 0 errors)")
            results["gates"][gate_id] = {
                "status": "PASS", "gate_type": "command", "counts": counts, "report_path": str(report_xml.relative_to(ROOT)),
                "report_status": "VALID", "duration_ms": dur, "exit_code": code
            }
        else:
            print(f"  -> FAIL: Gate failed (passed: {passed}/{min_passed}, failed: {failed}, errors: {errors}):\n{out}\n{err}")
            results["gates"][gate_id] = {
                "status": "FAIL", "gate_type": "command", "counts": counts, "duration_ms": dur, "exit_code": code or 1
            }

    # G02: Test DB Bootstrap
    run_pytest_gate(
        "G02_TEST_DB_BOOTSTRAP",
        ["backend/tests/test_v17_7_database_safety.py"],
        "test_dbs_01 or test_dbs_09",
        min_passed=2,
        label="Test-Owned Database Bootstrap"
    )

    # G03: Unsafe Path Rejection
    run_pytest_gate(
        "G03_UNSAFE_PATH_REJECTION",
        ["backend/tests/test_v17_7_database_safety.py"],
        "test_dbs_02 or test_dbs_03 or test_dbs_04 or test_dbs_05 or test_dbs_05b or test_dbs_06 or test_dbs_07 or test_dbs_08 or test_dbs_10",
        min_passed=7,
        label="Unsafe Database Path & Alias Rejection"
    )

    # G04: Destructive Fixture Isolation
    run_pytest_gate(
        "G04_DESTRUCTIVE_FIXTURE_ISOLATION",
        ["backend/tests/test_v17_7_database_safety.py"],
        "test_dbs_11 or test_dbs_12 or test_dbs_13 or test_dbs_14 or test_dbs_15",
        min_passed=5,
        label="Destructive Fixture & Mutation Isolation"
    )

    # G05: Raw ATR Provenance Authority
    run_pytest_gate(
        "G05_RAW_ATR_PROVENANCE",
        ["backend/tests/test_v17_7_atr_provenance.py"],
        "test_atr_01 or test_atr_04 or test_atr_05 or test_atr_06 or test_atr_08 or test_atr_09 or test_atr_13 or test_atr_14",
        min_passed=8,
        label="Raw ATR Provenance Authority & Causal Timestamps"
    )

    # G06: Required M1 ATR Fail-Closed
    run_pytest_gate(
        "G06_REQUIRED_M1_ATR_FAIL_CLOSED",
        ["backend/tests/test_v17_7_atr_provenance.py"],
        "test_atr_02 or test_atr_03 or test_atr_07 or test_atr_10 or test_atr_11 or test_atr_12 or test_atr_20",
        min_passed=7,
        label="Required M1 ATR Fail-Closed & WAITING_VOLATILITY_EVIDENCE"
    )

    # G07: ATR / Buffer Mathematical Correctness
    run_pytest_gate(
        "G07_ATR_BUFFER_MATH_CORRECTNESS",
        ["backend/tests/test_v17_7_atr_provenance.py"],
        "test_atr_15 or test_atr_16 or test_atr_19",
        min_passed=3,
        label="ATR / Buffer Mathematical Correctness"
    )

    # G08: Structural Anchor Causal Integrity
    run_pytest_gate(
        "G08_STRUCTURAL_ANCHOR_CAUSAL",
        ["backend/tests/test_v17_7_adaptive_stop_loss.py"],
        "test_ce_06 or test_ce_07 or test_ce_08 or test_sl_58",
        min_passed=4,
        label="Structural Anchor Causal Ordering & Invalidation"
    )

    # G09: Distant SL Financial Integrity
    run_pytest_gate(
        "G09_DISTANT_SL_FINANCIAL_INTEGRITY",
        ["backend/tests/test_v17_7_adaptive_stop_loss.py"],
        "test_dsl_01 or test_dsl_02 or test_dsl_07 or test_ce_11",
        min_passed=4,
        label="Distant SL (>4x ATR) Financial Integrity"
    )

    # G10: CG01 Real Evaluator Integration
    run_pytest_gate(
        "G10_CG01_EVALUATOR_INTEGRATION",
        ["backend/tests/test_v17_7_adaptive_stop_loss.py", "backend/tests/test_v17_7_atr_provenance.py"],
        "test_sl_36 or test_ce_10 or test_atr_17",
        min_passed=3,
        label="CG01 Real Evaluator Integration"
    )

    # G11: Scalp M1 Models Real Evaluator Integration
    run_pytest_gate(
        "G11_M1_EVALUATOR_INTEGRATION",
        ["backend/tests/test_v17_7_adaptive_stop_loss.py", "backend/tests/test_v17_7_atr_provenance.py"],
        "test_sl_37 or test_sl_38 or test_sl_39 or test_sl_40 or test_ce_12 or test_atr_18 or test_atr_21",
        min_passed=7,
        label="Scalp M1 Models Real Evaluator Integration"
    )

    # G12: Orchestrator Real ARM Boundary
    run_pytest_gate(
        "G12_ORCHESTRATOR_REAL_ARM",
        ["backend/tests/test_v17_7_adaptive_stop_loss.py"],
        "test_sl_43 or test_ce_13",
        min_passed=2,
        label="Orchestrator Real ARM Boundary"
    )

    # G13: Real ARM Concurrency & Idempotency
    run_pytest_gate(
        "G13_REAL_ARM_CONCURRENCY",
        ["backend/tests/test_v17_7_adaptive_stop_loss.py"],
        "test_ce_16 or test_sl_57 or test_dsl_18",
        min_passed=3,
        label="Multi-Threaded Real ARM Concurrency & Idempotency"
    )

    # G14: Fill-Time Financial Invariants & Sizing
    run_pytest_gate(
        "G14_FILL_TIME_FINANCIAL_INVARIANTS",
        ["backend/tests/test_v17_7_adaptive_stop_loss.py"],
        "test_sl_46 or test_ce_14",
        min_passed=2,
        label="Fill-Time Financial Invariants & Sizing Preservation"
    )

    # G15: PAPER Exit, CLOSED State & Persistence
    run_pytest_gate(
        "G15_PAPER_EXIT_AND_PERSISTENCE",
        ["backend/tests/test_v17_7_adaptive_stop_loss.py"],
        "test_sl_47 or test_sl_48 or test_sl_60 or test_ce_15",
        min_passed=4,
        label="PAPER Exit, CLOSED State & Durable Persistence"
    )

    # G16: Live Order Safety Boundary
    print("\n[G16_LIVE_ORDER_SAFETY_BOUNDARY] Verifying Live-Order Dispatch Safety & Negative Control...")
    g16_worker_script = (
        "import sys\n"
        "from tools.run_v17_8_acceptance import verify_live_order_safety_boundary\n"
        "ok, msg = verify_live_order_safety_boundary()\n"
        "print(msg)\n"
        "sys.exit(0 if ok else 1)\n"
    )
    code_spy, out_spy, err_spy, dur_spy = run_cmd([PYTHON_BIN, "-c", g16_worker_script], log_name="g16_spy_worker")
    safety_ok = (code_spy == 0)
    safety_msg = out_spy.strip() or err_spy.strip()
    report_xml_g16 = REPORTS_DIR / "g16.xml"
    cmd_g16 = [
        PYTHON_BIN, "-m", "pytest",
        "backend/tests/test_v17_7_adaptive_stop_loss.py",
        f"--junitxml={str(report_xml_g16)}",
        "-k", "test_sl_50 or test_dsl_19 or test_ce_18"
    ]
    code, out, err, dur = run_cmd(cmd_g16, log_name="g16_live_safety")
    counts = parse_pytest_counts(out, report_xml_g16)
    passed = counts.get("passed", 0)

    if safety_ok and code == 0 and passed >= 3 and counts.get("failed", 0) == 0:
        print(f"  -> PASS: {safety_msg}")
        print(f"  -> PASS: {passed} live-order safety & negative control tests passed")
        results["gates"]["G16_LIVE_ORDER_SAFETY_BOUNDARY"] = {
            "status": "PASS", "gate_type": "command", "details": safety_msg, "counts": counts,
            "report_path": str(report_xml_g16.relative_to(ROOT)), "duration_ms": dur, "exit_code": 0
        }
    else:
        print(f"  -> FAIL: Live-order safety verification failed: {safety_msg}\n{out}\n{err}")
        results["gates"]["G16_LIVE_ORDER_SAFETY_BOUNDARY"] = {
            "status": "FAIL", "gate_type": "command", "details": safety_msg, "counts": counts, "duration_ms": dur, "exit_code": 1
        }

    # G17: Dedicated V17.7 Suite (100 Tests)
    run_pytest_gate(
        "G17_EXISTING_SL_DSL_CE_REGRESSION",
        ["backend/tests/test_v17_7_adaptive_stop_loss.py"],
        filter_expr="",
        min_passed=100,
        label="Complete Dedicated V17.7 Adaptive Stop Loss Suite (100 Tests)"
    )

    # G18: V17.5 Regression Suite
    v17_5_files = sorted(glob.glob(str(BACKEND / "tests" / "test_v17_5*.py")))
    run_pytest_gate(
        "G18_V17_5_REGRESSION",
        v17_5_files,
        filter_expr="",
        min_passed=200,
        label=f"V17.5 Regression Suite ({len(v17_5_files)} test files)"
    )

    # G19: V17.6 Regression Suite
    v17_6_files = sorted(glob.glob(str(BACKEND / "tests" / "test_v17_6*.py")))
    run_pytest_gate(
        "G19_V17_6_REGRESSION",
        v17_6_files,
        filter_expr="",
        min_passed=300,
        label=f"V17.6 Regression Suite ({len(v17_6_files)} test files)",
        timeout=1800,
    )

    # G20: Full Backend Pytest Suite
    run_pytest_gate(
        "G20_FULL_BACKEND_PYTEST",
        ["backend/tests"],
        filter_expr="",
        min_passed=3000,
        label="Entire backend/tests Pytest Suite",
        timeout=2400,
    )

    # G21: Frontend Vitest Suite
    print("\n[G21_FRONTEND_VITEST] Executing Frontend Vitest Suite...")
    code, out, err, dur = run_cmd([NPM_BIN, "test", "--", "--run"], cwd=FRONTEND, log_name="g21_vitest")
    vitest_counts = parse_vitest_counts(out)
    if code == 0 and vitest_counts["passed"] > 0:
        print(f"  -> PASS: Frontend Vitest passed ({vitest_counts['passed']} tests in {vitest_counts['test_files_passed']} files) in {dur/1000:.2f}s")
        results["gates"]["G21_FRONTEND_VITEST"] = {
            "status": "PASS", "gate_type": "command", "counts": vitest_counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Frontend Vitest failed:\n{out}\n{err}")
        results["gates"]["G21_FRONTEND_VITEST"] = {
            "status": "FAIL", "gate_type": "command", "counts": vitest_counts, "duration_ms": dur, "exit_code": code
        }

    # G22: Frontend Production Build
    print("\n[G22_FRONTEND_BUILD] Executing Frontend Production Build...")
    code, out, err, dur = run_cmd([NPM_BIN, "run", "build"], cwd=FRONTEND, log_name="g22_build")
    if code == 0:
        print(f"  -> PASS: Frontend production build succeeded in {dur/1000:.2f}s")
        results["gates"]["G22_FRONTEND_BUILD"] = {
            "status": "PASS", "gate_type": "command", "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Frontend build failed:\n{out}\n{err}")
        results["gates"]["G22_FRONTEND_BUILD"] = {
            "status": "FAIL", "gate_type": "command", "duration_ms": dur, "exit_code": code
        }

    # G23: Frontend Lint (Oxlint)
    print("\n[G23_FRONTEND_LINT] Executing Frontend Oxlint...")
    code, out, err, dur = run_cmd([NPX_BIN, "oxlint"], cwd=FRONTEND, log_name="g23_lint")
    if code == 0:
        print(f"  -> PASS: Oxlint verified 0 errors in {dur/1000:.2f}s")
        results["gates"]["G23_FRONTEND_LINT"] = {
            "status": "PASS", "gate_type": "command", "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Frontend lint failed:\n{out}\n{err}")
        results["gates"]["G23_FRONTEND_LINT"] = {
            "status": "FAIL", "gate_type": "command", "duration_ms": dur, "exit_code": code
        }

    # G24: Playwright E2E Suite
    print("\n[G24_PLAYWRIGHT_BROWSER_E2E] Executing Playwright E2E Suite...")
    pw_api_port = str(find_free_port(8765))
    pw_web_port = str(find_free_port(5199))
    pw_env = {
        "AURUM_E2E_PYTHON": PYTHON_BIN,
        "AURUM_E2E_API_PORT": pw_api_port,
        "AURUM_E2E_WEB_PORT": pw_web_port,
    }
    code, out, err, dur = run_cmd(
        [NPX_BIN, "playwright", "test", "tests/e2e/v17_5_3_stop_browser.e2e.ts"],
        cwd=FRONTEND, env=pw_env, log_name="g24_playwright", timeout=120
    )
    pw_counts = parse_playwright_counts(out)
    if code == 0 and pw_counts["passed"] > 0 and pw_counts["failed"] == 0:
        print(f"  -> PASS: Playwright browser E2E verified ({pw_counts['passed']} passed) in {dur/1000:.2f}s")
        results["gates"]["G24_PLAYWRIGHT_BROWSER_E2E"] = {
            "status": "PASS", "gate_type": "command", "counts": pw_counts, "duration_ms": dur, "exit_code": code
        }
    else:
        print(f"  -> FAIL: Playwright E2E failed:\n{out}\n{err}")
        results["gates"]["G24_PLAYWRIGHT_BROWSER_E2E"] = {
            "status": "FAIL", "gate_type": "command", "counts": pw_counts, "duration_ms": dur, "exit_code": code or 1
        }

    # G25: Deterministic Replay
    run_pytest_gate(
        "G25_DETERMINISTIC_REPLAY",
        ["backend/tests/test_v17_7_adaptive_stop_loss.py"],
        "test_sl_53 or test_dsl_15",
        min_passed=2,
        label="Replay Determinism & Diagnostic Invariants"
    )

    # G26: Acceptance Runner Negative-Control Tests
    run_pytest_gate(
        "G26_ACCEPTANCE_RUNNER_NEGATIVE_TESTS",
        ["backend/tests/test_v17_7_adaptive_stop_loss.py"],
        "test_ce_19",
        min_passed=1,
        label="Acceptance Runner Negative-Control Suite"
    )

    # G27: Artifact & Source SHA Integrity
    report_xml_g27 = REPORTS_DIR / "g27.xml"
    code, out, err, dur = run_cmd(
        [PYTHON_BIN, "-m", "pytest", "backend/tests/test_v17_7_adaptive_stop_loss.py", f"--junitxml={str(report_xml_g27)}", "-k", "test_ce_20"],
        log_name="g27_ce20"
    )
    counts = parse_pytest_counts(out, report_xml_g27)
    if code == 0 and counts.get("passed", 0) >= 1:
        print(f"\n[G27_ARTIFACT_SOURCE_SHA_INTEGRITY]  -> PASS: Artifact integrity test passed")
        results["gates"]["G27_ARTIFACT_SOURCE_SHA_INTEGRITY"] = {
            "status": "PASS", "gate_type": "integrity", "sha": current_sha,
            "counts": counts, "duration_ms": dur, "exit_code": 0, "integrity_verified": True
        }
    else:
        print(f"\n[G27_ARTIFACT_SOURCE_SHA_INTEGRITY]  -> FAIL: Artifact integrity tests failed:\n{out}\n{err}")
        results["gates"]["G27_ARTIFACT_SOURCE_SHA_INTEGRITY"] = {
            "status": "FAIL", "gate_type": "integrity", "sha": current_sha,
            "duration_ms": dur, "exit_code": code or 1, "integrity_verified": False
        }

    # G28: Documentation Completeness & Consistency
    print("\n[G28_DOCS_DEFECT_REGISTER_CONSISTENCY] Verifying V17.8 Documentation Consistency...")
    req_docs = [
        "docs/V17_8_MASTER_DESIGN.md",
        "docs/V17_8_EXISTING_ARCHITECTURE_MAP.md",
        "docs/V17_8_STOP_OUT_FORENSIC_AUDIT.md",
        "docs/V17_8_UNICORN_EVIDENCE_SPEC.md",
        "docs/V17_8_STRUCTURAL_SL_DECISION_SPEC.md",
        "docs/V17_8_LIQUIDITY_TP_SPEC.md",
        "docs/V17_8_ENTRY_AND_NOISE_PROTECTION.md",
        "docs/V17_8_RISK_EXECUTION_INVARIANTS.md",
        "docs/V17_8_REPLAY_RESEARCH_PROTOCOL.md",
        "docs/V17_8_REGRESSION_MATRIX.md",
        "docs/V17_8_DEFECT_REGISTER.md",
        "docs/V17_8_EXACT_COMMIT_ACCEPTANCE.md",
        "docs/V17_8_FINAL_RELEASE_REPORT.md",
        "docs/V17_8_PAPER_PILOT_READINESS.md",
    ]
    missing_docs = []
    for doc in req_docs:
        p = ROOT / doc
        if not p.is_file() or p.stat().st_size < 200:
            missing_docs.append(doc)
            print(f"  -> FAIL: Missing or insufficient doc: {doc}")
        else:
            print(f"  -> PASS: Verified doc exists ({p.stat().st_size} bytes): {doc}")

    if not missing_docs:
        results["gates"]["G28_DOCS_DEFECT_REGISTER_CONSISTENCY"] = {
            "status": "PASS", "gate_type": "command", "verified_docs": len(req_docs), "exit_code": 0
        }
    else:
        results["gates"]["G28_DOCS_DEFECT_REGISTER_CONSISTENCY"] = {
            "status": "FAIL", "gate_type": "command", "missing_docs": missing_docs, "exit_code": 1
        }

    # ==========================================================================
    # V17.8 SPECIFIC ACCEPTANCE GATES (G29 to G42)
    # ==========================================================================

    # G29: Unicorn Breaker Detection
    run_pytest_gate(
        "G29_UNICORN_BREAKER_DETECTION",
        ["backend/tests/test_v17_8_unicorn_evidence.py"],
        "test_positive_bullish_unicorn_detection or test_positive_bearish_unicorn_detection",
        min_passed=2,
        label="ICT Unicorn Symmetrical Bullish & Bearish Breaker Detection"
    )

    # G30: Unicorn FVG Overlap Confluence
    run_pytest_gate(
        "G30_UNICORN_FVG_OVERLAP",
        ["backend/tests/test_v17_8_unicorn_evidence.py"],
        "test_negative_breaker_without_fvg or test_negative_no_geometric_overlap",
        min_passed=2,
        label="Breaker and FVG Geometric Overlap & Confluence Verification"
    )

    # G31: Cross-Timeframe Causality & Invalidation
    run_pytest_gate(
        "G31_CROSS_TIMEFRAME_CAUSALITY",
        ["backend/tests/test_v17_8_unicorn_evidence.py"],
        "test_negative_unicorn_invalidated_by_distal_breach or test_negative_waiting_retest",
        min_passed=2,
        label="Cross-Timeframe Closed Bar Causality & Distal Breach Invalidation"
    )

    # G32: Structural Invalidation Authority
    run_pytest_gate(
        "G32_STRUCTURAL_INVALIDATION_AUTHORITY",
        ["backend/tests/test_v17_8_structural_sl.py"],
        "test_unicorn_breaker_distal_invalidation_authority",
        min_passed=1,
        label="Thesis-Bound Invalidation Authority (BREAKER_DISTAL_BOUNDARY)"
    )

    # G33: Model-Specific SL Selection
    run_pytest_gate(
        "G33_MODEL_SPECIFIC_SL_SELECTION",
        ["backend/tests/test_v17_8_structural_sl.py"],
        "test_fvg_displacement_origin_fallback_when_bound_leg_invalidated",
        min_passed=1,
        label="Model-Specific SL Invalidation Selection & Displacement Origin Fallback"
    )

    # G34: ATR & Spread Noise Protection
    run_pytest_gate(
        "G34_ATR_SPREAD_NOISE_PROTECTION",
        ["backend/tests/test_v17_8_quote_noise_guard.py"],
        "test_short_stop_too_close_to_executable_ask or test_long_stop_too_close_to_executable_bid",
        min_passed=2,
        label="Dynamic Volatility Buffer & Executable Quote Distance Protection"
    )

    # G35: Liquidity TP Independence
    run_pytest_gate(
        "G35_LIQUIDITY_TP_INDEPENDENCE",
        ["backend/tests/test_v17_8_structural_sl.py"],
        "test_fail_closed_when_wide_sl_causes_insufficient_net_rr",
        min_passed=1,
        label="Independent Liquidity TP & Fail-Closed Net R:R Invariant"
    )

    # G36: Entry Quote Proximity Guard
    run_pytest_gate(
        "G36_ENTRY_QUOTE_PROXIMITY",
        ["backend/tests/test_v17_8_quote_noise_guard.py"],
        "test_short_stop_breached_by_executable_ask or test_long_stop_breached_by_executable_bid or test_safe_executable_quote_distance_passes_geometry",
        min_passed=3,
        label="Pre-ARM and Pre-FILL Executable Quote Proximity & Geometry Guard"
    )

    # G37: Risk Budget & Net R:R Integrity
    run_pytest_gate(
        "G37_RISK_BUDGET_AND_NET_RR",
        ["backend/tests/test_v17_8_structural_sl.py"],
        "test_financial_risk_conservation_and_quantity_downscaling or test_legitimate_distant_structural_sl_over_4x_and_10x_atr",
        min_passed=2,
        label="Risk Budget Conservation ($2.50 USDT) & Quantity Downscaling"
    )

    # G38: Complete ARM -> FILL -> EXIT Lifecycle
    run_pytest_gate(
        "G38_COMPLETE_ARM_FILL_EXIT",
        ["backend/tests/test_v17_7_adaptive_stop_loss.py"],
        "test_sl_43 or test_sl_46 or test_sl_47 or test_ce_13 or test_ce_14 or test_ce_15",
        min_passed=6,
        label="Complete Production Paper Lifecycle (ARM -> FILL -> CLOSED) Persistence"
    )

    # G39: Historical Replay Determinism & Matched Comparison
    run_pytest_gate(
        "G39_HISTORICAL_REPLAY_DETERMINISM",
        ["backend/tests/test_v17_8_replay_comparison.py"],
        "test_variant_a_tight_stop_early_stop_out_vs_variant_c_survival or test_matched_comparison_determinism or test_intrabar_ambiguity_conservative_sl_first",
        min_passed=3,
        label="Deterministic Replay Harness & Variants A/B/C/D Matched Comparison"
    )

    # G40: Feature Flag & Legacy Parity
    run_pytest_gate(
        "G40_FEATURE_FLAG_AND_LEGACY_PARITY",
        ["backend/tests/test_v17_7_adaptive_stop_loss.py"],
        "test_sl_01 or test_sl_02 or test_sl_03 or test_sl_04",
        min_passed=4,
        label="Feature Flag Default Opt-In & Legacy Mode Parity"
    )

    # G41: Negative Controls & Anti-Fabrication
    run_pytest_gate(
        "G41_NEGATIVE_CONTROLS_AND_ANTI_FABRICATION",
        ["backend/tests/test_v17_8_quote_noise_guard.py", "backend/tests/test_v17_8_replay_comparison.py"],
        "test_unicorn_invalidation_guard_blocks_when_distal_breached or test_stale_or_invalid_quote_fails_closed or test_unverifiable_data_rejected_or_flagged",
        min_passed=3,
        label="Fail-Closed Negative Controls & Data Anti-Fabrication"
    )

    # G42: Exact-Source & Artifact Integrity
    print("\n[G42_EXACT_SOURCE_AND_ARTIFACT_INTEGRITY] Verifying Exact Committed Source & Bundle Integrity...")
    end_source_manifest = build_source_manifest(ROOT)
    source_clean = (end_source_manifest["composite_sha"] == source_manifest["composite_sha"])

    if source_clean:
        print(f"  -> PASS: Source files unmutated during acceptance (SHA: {source_manifest['composite_sha'][:8]})")
        results["gates"]["G42_EXACT_SOURCE_AND_ARTIFACT_INTEGRITY"] = {
            "status": "PASS", "gate_type": "integrity", "sha": current_sha,
            "duration_ms": 0, "exit_code": 0, "integrity_verified": True
        }
    else:
        print("CRITICAL SAFETY BLOCKER: Source files were modified during acceptance run!")
        results["gates"]["G42_EXACT_SOURCE_AND_ARTIFACT_INTEGRITY"] = {
            "status": "FAIL", "gate_type": "integrity", "sha": current_sha,
            "duration_ms": 0, "exit_code": 1, "integrity_verified": False,
            "error": "SOURCE_MUTATED_DURING_RUN"
        }

    # --------------------------------------------------------------------------
    # FINAL VERDICT RESOLUTION & INTEGRITY VERIFICATION
    # --------------------------------------------------------------------------
    print("\n" + "=" * 80)
    final_verdict = determine_release_verdict(results["gates"], MANDATORY_GATES_V17_8)
    results["verdict"] = final_verdict
    print(f"FINAL ACCEPTANCE VERDICT: {final_verdict}")
    if final_verdict == "ACCEPTED_FOR_PAPER_OPT_IN":
        print("All 42 release gates successfully passed with verified machine evidence!")
    else:
        print("One or more mandatory release gates failed! Review gate details above.")
    print("=" * 80)

    # --------------------------------------------------------------------------
    # Run-Scoped Artifact Publication & Atomic Verification
    # --------------------------------------------------------------------------
    published_dir = write_acceptance_artifacts(
        run_id=RUN_ID,
        results_payload=results,
        source_manifest=source_manifest,
        gate_manifest=MANDATORY_GATES_V17_8,
        artifacts_base_dir=ARTIFACTS_DIR,
        version_dir="v17_8",
    )
    print(f"\nRun-scoped artifacts published to: {published_dir}")

    # Verify written bundle strictly
    ok_bundle, bundle_errors = verify_acceptance_bundle(
        published_dir,
        expected_version="17.8.0",
        expected_run_id=RUN_ID,
        expected_source_sha=current_sha,
        expected_gate_manifest=MANDATORY_GATES_V17_8,
    )
    if ok_bundle and final_verdict == "ACCEPTED_FOR_PAPER_OPT_IN":
        print("  -> PASS: Published bundle integrity strictly verified against leaf hashes and verdict.")
    else:
        print(f"  -> FAIL: Published bundle verification failed: {bundle_errors}")
        results["verdict"] = "BLOCKED"
        results["bundle_verification_errors"] = bundle_errors
        write_acceptance_artifacts(
            run_id=RUN_ID,
            results_payload=results,
            source_manifest=source_manifest,
            gate_manifest=MANDATORY_GATES_V17_8,
            artifacts_base_dir=ARTIFACTS_DIR,
            version_dir="v17_8",
        )

    # Update latest pointer without overwriting historical files
    latest_pointer = {
        "schema_version": 1,
        "version": "17.8.0",
        "latest_run_id": RUN_ID,
        "run_directory": str(published_dir.relative_to(ROOT)),
        "verdict": results["verdict"],
        "timestamp_utc": results["timestamp_utc"],
    }
    (ARTIFACTS_DIR / "v17_8" / "latest_run.json").write_text(json.dumps(latest_pointer, indent=2), encoding="utf-8")

    return 0 if results["verdict"] == "ACCEPTED_FOR_PAPER_OPT_IN" else 1


if __name__ == "__main__":
    sys.exit(main())
