"""
backend/tests/test_v17_5_9_acceptance_runner_self_test.py
AURUM V17.5.9 — Acceptance Runner Self-Test Suite (Section 10 Part 089).

Tests:
1. Missing JUnit -> FAIL
2. Old/stale JUnit -> FAIL
3. Zero tests collected -> FAIL
4. Nonzero exit -> FAIL
5. Command timeout -> TIMEOUT status & FAIL
6. Dirty source -> not clean ACCEPTED (returns BLOCKED)
7. Missing critical file -> FAIL / BLOCKED
8. Invalid manifest / corrupt XML -> FAIL
9. Valid clean run -> eligible for ACCEPTED
"""

import os
import sys
import tempfile
import time
from pathlib import Path
import pytest

# Add tools directory to path
TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import run_v17_5_9_acceptance as runner


def test_t01_missing_junit_fails():
    """Part 089: Missing JUnit file returns FAIL status."""
    non_existent = Path("/tmp/nonexistent_junit_report_12345.xml")
    res = runner.parse_junit_xml(non_existent)
    assert res["status"] == "FAIL"
    assert res["reason"] == "JUNIT_FILE_MISSING"
    assert res["tests"] == 0


def test_t02_stale_junit_fails():
    """Part 089: Old JUnit file generated before test run start returns FAIL status."""
    with tempfile.TemporaryDirectory() as td:
        xml_file = Path(td) / "stale_report.xml"
        xml_file.write_text('<testsuite tests="10" failures="0" errors="0" skipped="0"></testsuite>', encoding="utf-8")
        stale_time = time.time() - 100
        os.utime(str(xml_file), (stale_time, stale_time))

        run_start = time.time()
        res = runner.parse_junit_xml(xml_file, min_mtime=run_start)
        assert res["status"] == "FAIL"
        assert res["reason"] == "JUNIT_FILE_STALE"


def test_t03_zero_tests_fails():
    """Part 089: Zero tests collected returns FAIL status."""
    with tempfile.TemporaryDirectory() as td:
        xml_file = Path(td) / "zero_tests.xml"
        xml_file.write_text('<testsuite tests="0" failures="0" errors="0" skipped="0"></testsuite>', encoding="utf-8")
        res = runner.parse_junit_xml(xml_file)
        assert res["status"] == "FAIL"
        assert res["reason"] == "ZERO_TESTS_COLLECTED"


def test_t04_nonzero_exit_fails():
    """Part 089: Command with nonzero exit code returns FAIL."""
    res = runner.run_command_cross_platform([sys.executable, "-c", "import sys; sys.exit(2)"], cwd=Path("."))
    assert res["exit_code"] == 2
    assert res["status"] == "FAIL"


def test_t05_timeout_fails():
    """Part 089: Subprocess timeout returns TIMEOUT status."""
    res = runner.run_command_cross_platform([sys.executable, "-c", "import time; time.sleep(5)"], cwd=Path("."), timeout=1)
    assert res["status"] == "TIMEOUT"
    assert res["exit_code"] == -1


def test_t06_dirty_source_not_accepted():
    """Part 089: Dirty working tree prevents ACCEPTED verdict, returning BLOCKED."""
    verdict = runner.evaluate_final_verdict(
        is_dirty=True,  # Dirty working tree
        backend_ok=True,
        backend_total_passed=304,
        backend_total_failed=0,
        vitest_ok=True,
        frontend_passed=501,
        build_ok=True,
        lint_ok=True,
        playwright_ok=True,
        browser_passed=6,
        critical_files_missing=False,
    )
    assert verdict == "BLOCKED"


def test_t07_missing_critical_file_fails():
    """Part 089: Missing critical source file returns BLOCKED verdict."""
    verdict = runner.evaluate_final_verdict(
        is_dirty=False,
        backend_ok=True,
        backend_total_passed=304,
        backend_total_failed=0,
        vitest_ok=True,
        frontend_passed=501,
        build_ok=True,
        lint_ok=True,
        playwright_ok=True,
        browser_passed=6,
        critical_files_missing=True,  # Missing critical file
    )
    assert verdict == "BLOCKED"


def test_t08_invalid_manifest_or_corrupt_xml_fails():
    """Part 089: Corrupt XML content returns FAIL with JUNIT_PARSE_ERROR."""
    with tempfile.TemporaryDirectory() as td:
        xml_file = Path(td) / "corrupt.xml"
        xml_file.write_text("<unclosed_tag", encoding="utf-8")
        res = runner.parse_junit_xml(xml_file)
        assert res["status"] == "FAIL"
        assert "JUNIT_PARSE_ERROR" in res["reason"]


def test_t09_valid_clean_run_eligible_for_accepted():
    """Part 089: Clean source tree with all passing gates evaluates to ACCEPTED."""
    verdict = runner.evaluate_final_verdict(
        is_dirty=False,
        backend_ok=True,
        backend_total_passed=304,
        backend_total_failed=0,
        vitest_ok=True,
        frontend_passed=501,
        build_ok=True,
        lint_ok=True,
        playwright_ok=True,
        browser_passed=6,
        critical_files_missing=False,
    )
    assert verdict == "ACCEPTED"
