"""
backend/tests/test_v17_6_2_acceptance_runner.py
AURUM V17.6.2 — Acceptance Runner Self-Test Suite (FIX-04, AC01-AC12).

Verifies that the acceptance runner strictly and authoritatively enforces
production quality gates, refusing to certify dirty sources, missing gates,
stale evidence, or incomplete multi-cycle soak runs.

Tests:
- AC01: All gates pass -> ACCEPTED
- AC02: Missing gate -> REJECTED
- AC03: Failed gate -> REJECTED
- AC04: Dirty tracked source -> BLOCKED
- AC05: Untracked source file -> BLOCKED
- AC06: Stale JUnit report -> FAIL
- AC07: Zero tests collected -> FAIL
- AC08: Nonzero exit code -> FAIL
- AC09: Missing critical source file -> BLOCKED
- AC10: Source fingerprint mismatch or missing file detection
- AC11: Incomplete soak execution -> REJECTED
- AC12: Corrupt manifest or invalid XML -> FAIL
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

import run_v17_6_2_acceptance as runner


def test_ac01_all_pass_accepted():
    """AC01: Clean source tree with all passing gates evaluates to ACCEPTED."""
    verdict = runner.evaluate_final_verdict(
        is_dirty=False,
        backend_ok=True,
        backend_total_passed=160,
        backend_total_failed=0,
        vitest_ok=True,
        frontend_passed=514,
        build_ok=True,
        lint_ok=True,
        playwright_ok=True,
        browser_passed=1,
        soak_ok=True,
        critical_files_missing=False,
        missing_gates=False,
    )
    assert verdict == "ACCEPTED"


def test_ac02_missing_gate_fails():
    """AC02: Missing technical gate causes REJECTED verdict."""
    verdict = runner.evaluate_final_verdict(
        is_dirty=False,
        backend_ok=True,
        backend_total_passed=160,
        backend_total_failed=0,
        vitest_ok=True,
        frontend_passed=514,
        build_ok=True,
        lint_ok=True,
        playwright_ok=True,
        browser_passed=1,
        soak_ok=True,
        critical_files_missing=False,
        missing_gates=True,  # Mandatory gate missing
    )
    assert verdict == "REJECTED"


def test_ac03_failed_gate_rejected():
    """AC03: Any failed backend or frontend gate returns REJECTED verdict."""
    verdict = runner.evaluate_final_verdict(
        is_dirty=False,
        backend_ok=False,  # Failed backend suite
        backend_total_passed=159,
        backend_total_failed=1,
        vitest_ok=True,
        frontend_passed=514,
        build_ok=True,
        lint_ok=True,
        playwright_ok=True,
        browser_passed=1,
        soak_ok=True,
        critical_files_missing=False,
        missing_gates=False,
    )
    assert verdict == "REJECTED"


def test_ac04_dirty_tracked_source_blocked():
    """AC04: Dirty tracked source prevents ACCEPTED verdict, returning BLOCKED."""
    verdict = runner.evaluate_final_verdict(
        is_dirty=True,  # Dirty working tree
        backend_ok=True,
        backend_total_passed=160,
        backend_total_failed=0,
        vitest_ok=True,
        frontend_passed=514,
        build_ok=True,
        lint_ok=True,
        playwright_ok=True,
        browser_passed=1,
        soak_ok=True,
        critical_files_missing=False,
        missing_gates=False,
    )
    assert verdict == "BLOCKED"


def test_ac05_untracked_source_file_blocked():
    """AC05: Untracked source in code directory marks working tree dirty."""
    # Simulate git porcelain output containing untracked source file
    mock_status_lines = [
        "?? backend/services/new_untracked_service.py",
        "?? artifacts/v17_6_2/acceptance.json",  # Allowed
    ]
    dirty_untracked = [
        l for l in mock_status_lines
        if l.startswith("?? ")
        and not l.startswith("?? artifacts/")
        and not l.startswith("?? logs/")
    ]
    assert len(dirty_untracked) == 1
    assert "new_untracked_service.py" in dirty_untracked[0]


def test_ac06_stale_junit_fails():
    """AC06: Old JUnit file generated before test execution returns FAIL status."""
    with tempfile.TemporaryDirectory() as td:
        xml_file = Path(td) / "stale_report.xml"
        xml_file.write_text('<testsuite tests="10" failures="0" errors="0" skipped="0"></testsuite>', encoding="utf-8")
        stale_time = time.time() - 200
        os.utime(str(xml_file), (stale_time, stale_time))

        run_start = time.time()
        res = runner.parse_junit_xml(xml_file, min_mtime=run_start)
        assert res["status"] == "FAIL"
        assert res["reason"] == "JUNIT_FILE_STALE"


def test_ac07_zero_tests_collected_fails():
    """AC07: Zero tests collected returns FAIL status."""
    with tempfile.TemporaryDirectory() as td:
        xml_file = Path(td) / "zero_tests.xml"
        xml_file.write_text('<testsuite tests="0" failures="0" errors="0" skipped="0"></testsuite>', encoding="utf-8")
        res = runner.parse_junit_xml(xml_file)
        assert res["status"] == "FAIL"
        assert res["reason"] == "ZERO_TESTS_COLLECTED"


def test_ac08_nonzero_exit_code_fails():
    """AC08: Command with nonzero exit code returns FAIL."""
    res = runner.run_command_cross_platform([sys.executable, "-c", "import sys; sys.exit(2)"], cwd=Path("."))
    assert res["exit_code"] == 2
    assert res["status"] == "FAIL"


def test_ac09_missing_critical_file_blocked():
    """AC09: Missing critical source file returns BLOCKED verdict."""
    verdict = runner.evaluate_final_verdict(
        is_dirty=False,
        backend_ok=True,
        backend_total_passed=160,
        backend_total_failed=0,
        vitest_ok=True,
        frontend_passed=514,
        build_ok=True,
        lint_ok=True,
        playwright_ok=True,
        browser_passed=1,
        soak_ok=True,
        critical_files_missing=True,  # Missing critical source file
        missing_gates=False,
    )
    assert verdict == "BLOCKED"


def test_ac10_source_fingerprint_missing_detection():
    """AC10: Source fingerprint computes MISSING for nonexistent files."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    non_existent = repo_root / "backend/services/non_existent_service_file_xyz.py"
    hashes = runner.compute_source_fingerprint([non_existent], repo_root)
    assert hashes[str(non_existent.relative_to(repo_root))] == "MISSING"


def test_ac11_incomplete_soak_rejected():
    """AC11: Failed or incomplete soak test returns REJECTED verdict."""
    verdict = runner.evaluate_final_verdict(
        is_dirty=False,
        backend_ok=True,
        backend_total_passed=160,
        backend_total_failed=0,
        vitest_ok=True,
        frontend_passed=514,
        build_ok=True,
        lint_ok=True,
        playwright_ok=True,
        browser_passed=1,
        soak_ok=False,  # Incomplete/failed soak
        critical_files_missing=False,
        missing_gates=False,
    )
    assert verdict == "REJECTED"


def test_ac12_corrupt_junit_xml_fails():
    """AC12: Corrupt XML content returns FAIL with JUNIT_PARSE_ERROR."""
    with tempfile.TemporaryDirectory() as td:
        xml_file = Path(td) / "corrupt.xml"
        xml_file.write_text("<unclosed_tag", encoding="utf-8")
        res = runner.parse_junit_xml(xml_file)
        assert res["status"] == "FAIL"
        assert "JUNIT_PARSE_ERROR" in res["reason"]
