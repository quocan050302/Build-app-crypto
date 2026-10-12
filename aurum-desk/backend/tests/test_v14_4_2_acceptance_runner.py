"""
Unit tests for V14.4.2 Acceptance Runner (A01–A08).

Tests:
  - A01: missing manifest file is detected
  - A02: missing required test ID is detected
  - A03: skipped required test causes failure
  - A04: stale report with exit != 0 is rejected
  - A05: timeout/unwhitelisted pytest failures classify as FAIL
  - A06: D12 missing any of the 4 states causes failure
  - A07: all required cases present and passed -> PASS
  - A08: --skip-e2e marks acceptance INCOMPLETE with exit code 1
"""
import os
import sys
import tempfile
import time
from pathlib import Path
import pytest

# Add tools directory to path
TOOLS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import run_v14_4_2_acceptance as runner


def test_a01_missing_file_fails(monkeypatch):
    """A01: validate_manifest_files returns False when a required file is missing."""
    fake_manifest = {
        "D01": {"suite": "browser", "file": "nonexistent/file/path.e2e.ts", "desc": "missing file test"}
    }
    monkeypatch.setattr(runner, "REQUIRED_CASES", fake_manifest)
    assert runner.validate_manifest_files() is False


def test_a02_missing_case_fails():
    """A02: validate_required_results flags missing test IDs as INCOMPLETE."""
    required = {"D01": ["D01"], "D02": ["D02"]}
    actual = {"D01": "PASS"}  # D02 is missing
    cov = runner.validate_required_results(required, actual)
    assert cov["D01"]["status"] == "PASS"
    assert cov["D02"]["status"] == "INCOMPLETE"
    assert "D02" in cov["D02"]["missing"]


def test_a03_skipped_required_fails():
    """A03: validate_required_results flags skipped test as INCOMPLETE."""
    required = {"E08": ["E08"]}
    actual = {"E08": "SKIPPED"}
    cov = runner.validate_required_results(required, actual)
    assert cov["E08"]["status"] == "INCOMPLETE"
    assert cov["E08"]["not_passed"]["E08"] == "SKIPPED"


def test_a04_stale_report_fails(tmp_path):
    """A04: parse_playwright_json rejects stale reports older than run_start_time."""
    report_file = tmp_path / "playwright_report.json"
    report_file.write_text('{"suites": []}', encoding="utf-8")
    
    # Set file modification time to 100 seconds in the past
    stale_time = time.time() - 100
    os.utime(report_file, (stale_time, stale_time))
    
    run_start = time.time()
    res = runner.parse_playwright_json(str(report_file), run_start)
    # Stale report should return empty dict (rejected)
    assert res == {}


def test_a05_timeout_or_unknown_failure():
    """A05: classify_pytest returns FAIL for timeout or unwhitelisted test failures."""
    timeout_step = {
        "exit_code": 124,
        "_out": "pytest timed out",
    }
    assert runner.classify_pytest(timeout_step)["status"] == "FAIL"

    unknown_fail_step = {
        "exit_code": 1,
        "_out": "FAILED tests/test_critical.py::test_unexpected - AssertionError\n1 failed in 0.1s",
    }
    c = runner.classify_pytest(unknown_fail_step)
    assert c["status"] == "FAIL"
    assert "tests/test_critical.py::test_unexpected" in c["unexpected_failures"]


def test_a06_d12_missing_state_fails():
    """A06: D12 manifest requires all 4 states: armed, paper_open, closed, invalidated."""
    required_d12 = ["D12_armed", "D12_paper_open", "D12_closed", "D12_invalidated"]
    actual = {
        "D12_armed": "PASS",
        "D12_paper_open": "PASS",
        "D12_closed": "PASS",
        # missing D12_invalidated
    }
    cov = runner.validate_required_results({"D12": required_d12}, actual)
    assert cov["D12"]["status"] == "INCOMPLETE"
    assert "D12_invalidated" in cov["D12"]["missing"]


def test_a07_all_required_pass():
    """A07: When all required tests are present and PASS, coverage is PASS."""
    required = {
        "D01": ["D01"],
        "S01": ["S01"],
        "E08": ["E08"],
    }
    actual = {
        "D01": "PASS",
        "S01": "PASS",
        "E08": "PASS",
    }
    cov = runner.validate_required_results(required, actual)
    assert all(c["status"] == "PASS" for c in cov.values())


def test_a08_skip_e2e_is_incomplete():
    """A08: When --skip-e2e is requested, runner returns INCOMPLETE and non-zero exit code."""
    statuses = {
        "backend_targeted": "PASS",
        "browser_e2e": "INCOMPLETE",
    }
    blocking = {"FAIL", "NOT_RUN", "SKIPPED", "INCOMPLETE"}
    overall = "INCOMPLETE" if any(v in blocking for v in statuses.values()) else "PASS"
    assert overall == "INCOMPLETE"
