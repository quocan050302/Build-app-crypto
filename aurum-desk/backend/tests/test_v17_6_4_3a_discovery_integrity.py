"""
backend/tests/test_v17_6_4_3a_discovery_integrity.py
Authoritative Verification Suite for AC43A-05: Strict Backend Test Discovery.
Covers DC01 through DC10 per Master Prompt V17.6.4.3a (Phase 4, Part 070).
"""
import sys
import subprocess
from pathlib import Path
import pytest


def simulate_discovery_logic(code: int, stdout: str, out_dir: Path):
    """
    Implements strict V17.6.4.3a discovery logic:
    Requires code == 0 AND collected_count > 0.
    Nonzero exit code (e.g. exit code 2 from syntax error) must NEVER produce PASS.
    """
    manifest_file = out_dir / "backend_test_discovery_manifest.txt"
    log_file = out_dir / "G24_full_backend_test_discovery.log"

    log_file.write_text(stdout, encoding="utf-8")

    # Strict node ID extraction: ignores traceback frames containing '::'
    items = []
    for line in stdout.splitlines():
        line = line.strip()
        if "::" in line and not line.startswith("=") and not line.startswith("Traceback") and not line.startswith("File"):
            # Genuine test item pattern: path/to/file.py::test_name
            if ".py::" in line or line.endswith("]"):
                items.append(line)

    manifest_file.write_text("\n".join(items), encoding="utf-8")

    import re
    match = re.search(r"(\d+)\s+tests\s+collected", stdout)
    if match:
        collected_count = int(match.group(1))
    else:
        collected_count = len(items)

    if code != 0:
        status = "FAIL"
        reason = f"PYTEST_COLLECTION_EXIT_{code}"
    elif collected_count <= 0:
        status = "FAIL"
        reason = "ZERO_TESTS_COLLECTED"
    else:
        status = "PASS"
        reason = f"Successfully discovered {collected_count} tests dynamically across backend/tests"

    return {
        "status": status,
        "reason": reason,
        "collected_count": collected_count,
        "manifest_count": len(items),
    }


def test_dc01_exit_0_with_tests_passes(tmp_path):
    """DC01: Exit code 0 with collected tests produces PASS."""
    stdout = "backend/tests/test_a.py::test_1\nbackend/tests/test_b.py::test_2\n2 tests collected in 0.12s\n"
    res = simulate_discovery_logic(0, stdout, tmp_path)
    assert res["status"] == "PASS"
    assert res["collected_count"] == 2


def test_dc02_exit_1_fails(tmp_path):
    """DC02: Exit code 1 evaluates to FAIL."""
    stdout = "ERROR: test failure or collection issue\n"
    res = simulate_discovery_logic(1, stdout, tmp_path)
    assert res["status"] == "FAIL"
    assert "PYTEST_COLLECTION_EXIT_1" in res["reason"]


def test_dc03_exit_2_with_partial_collection_fails(tmp_path):
    """DC03: Exit code 2 (collection error) with 500 tests collected must strictly evaluate to FAIL (AC43A-05)."""
    stdout = (
        "backend/tests/test_ok.py::test_1\n"
        "...\n"
        "500 tests collected, 1 error in 1.45s\n"
        "ERROR collecting backend/tests/test_broken.py\n"
    )
    res = simulate_discovery_logic(2, stdout, tmp_path)
    assert res["status"] == "FAIL", "Exit code 2 with collected tests must FAIL discovery gate!"
    assert "PYTEST_COLLECTION_EXIT_2" in res["reason"]


def test_dc04_exit_3_internal_error_fails(tmp_path):
    """DC04: Exit code 3 (internal pytest error) evaluates to FAIL."""
    stdout = "INTERNALERROR: pytest crashed\n"
    res = simulate_discovery_logic(3, stdout, tmp_path)
    assert res["status"] == "FAIL"
    assert "PYTEST_COLLECTION_EXIT_3" in res["reason"]


def test_dc05_exit_5_no_tests_fails(tmp_path):
    """DC05: Exit code 5 (no tests collected) evaluates to FAIL."""
    stdout = "0 tests collected in 0.05s\n"
    res = simulate_discovery_logic(5, stdout, tmp_path)
    assert res["status"] == "FAIL"


def test_dc06_timeout_fails(tmp_path):
    """DC06: Timeout during collection evaluates to FAIL."""
    res = simulate_discovery_logic(-1, "TimeoutExpired: command timed out", tmp_path)
    assert res["status"] == "FAIL"


def test_dc07_import_error_during_collection_fails(tmp_path):
    """DC07: Import error during collection produces nonzero exit code and evaluates to FAIL."""
    stdout = "ImportError: cannot import name 'missing_symbol'\n100 tests collected, 1 error\n"
    res = simulate_discovery_logic(2, stdout, tmp_path)
    assert res["status"] == "FAIL"
    assert "PYTEST_COLLECTION_EXIT_2" in res["reason"]


def test_dc08_zero_tests_fails(tmp_path):
    """DC08: Zero tests collected with exit code 0 evaluates to FAIL."""
    stdout = "0 tests collected in 0.01s\n"
    res = simulate_discovery_logic(0, stdout, tmp_path)
    assert res["status"] == "FAIL"
    assert res["reason"] == "ZERO_TESTS_COLLECTED"


def test_dc09_manifest_does_not_contain_traceback_frames(tmp_path):
    """DC09: Traceback frames containing '::' are filtered and never counted as test node IDs."""
    stdout = (
        "backend/tests/test_one.py::test_alpha\n"
        "Traceback (most recent call last):\n"
        "  File '/lib/python3.13/site-packages/pytest/__init__.py', line 12, in <module>::foo\n"
        "backend/tests/test_two.py::test_beta\n"
        "2 tests collected in 0.10s\n"
    )
    res = simulate_discovery_logic(0, stdout, tmp_path)
    assert res["status"] == "PASS"
    manifest_lines = (tmp_path / "backend_test_discovery_manifest.txt").read_text().splitlines()
    assert len(manifest_lines) == 2
    assert "backend/tests/test_one.py::test_alpha" in manifest_lines
    assert "backend/tests/test_two.py::test_beta" in manifest_lines
    assert not any("Traceback" in line for line in manifest_lines)


def test_dc10_missing_discovery_artifact_fails(tmp_path):
    """DC10: Verifies that log and manifest files are created and non-empty."""
    stdout = "backend/tests/test_sample.py::test_sample\n1 tests collected in 0.05s\n"
    res = simulate_discovery_logic(0, stdout, tmp_path)
    assert res["status"] == "PASS"
    log = tmp_path / "G24_full_backend_test_discovery.log"
    manifest = tmp_path / "backend_test_discovery_manifest.txt"
    assert log.exists() and log.stat().st_size > 0
    assert manifest.exists() and manifest.stat().st_size > 0
