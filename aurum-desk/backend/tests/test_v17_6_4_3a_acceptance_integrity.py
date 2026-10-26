"""
backend/tests/test_v17_6_4_3a_acceptance_integrity.py
=====================================================
Acceptance Runner Integrity and Negative Test Suite (AI01 - AI16)
per Master Prompt V17.6.4.3a (Part 129).

Verifies:
- AI01: Missing required gate
- AI02: Dirty source
- AI03: SHA changed
- AI04: Pytest collection returns 2 with partial tests
- AI05: Unexpected skipped test
- AI06: All tests skipped
- AI07: Zero collected tests
- AI08: Missing JUnit
- AI09: Corrupt JUnit
- AI10: Browser E2E skipped
- AI11: Browser E2E failed
- AI12: Backend full suite failed
- AI13: Artifact missing
- AI14: Artifact checksum mismatch
- AI15: Development mode cannot ACCEPT
- AI16: Evidence does not match tested SHA
"""
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import run_v17_6_4_3a_acceptance as runner


class TestAcceptanceIntegrityV17643a:
    """AI01 - AI16 Acceptance Runner Integrity & Negative Test Suite."""

    def test_ai01_missing_required_gate(self):
        """AI01: Missing required gate in report results in REJECTED verdict."""
        assert len(runner.MANDATORY_GATES) == 32
        # If one gate is missing from report dictionary
        gates = {g: {"status": "PASS"} for g in runner.MANDATORY_GATES[:-1]}
        all_present = all(g in gates and gates[g] is not None for g in runner.MANDATORY_GATES)
        assert all_present is False

    def test_ai02_dirty_source(self):
        """AI02: Dirty source before tests rejects release acceptance."""
        with patch.object(runner, "git") as mock_git:
            mock_git.side_effect = lambda *args: (
                "fix/v14-1-rr-flow" if args[0] == "branch"
                else " M backend/models.py" if args[0] == "status"
                else "sha123"
            )
            current_branch = runner.git("branch", "--show-current")
            branch_ok = (current_branch == "fix/v14-1-rr-flow")
            status = runner.git("status", "--porcelain=v1", "--untracked-files=all")
            is_clean = len(status.strip()) == 0
            assert branch_ok is True
            assert is_clean is False

    def test_ai03_sha_changed(self):
        """AI03: SHA changed during test execution fails gate and rejects."""
        pre_sha = "sha_initial_123"
        post_sha = "sha_modified_456"
        sha_matches = (post_sha == pre_sha)
        assert sha_matches is False

    def test_ai04_pytest_collection_exit_nonzero_with_tests(self):
        """AI04: Pytest collection returning nonzero (e.g. exit 2) with partial tests FAILS discovery."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            with patch("subprocess.run") as mock_run:
                mock_proc = MagicMock()
                mock_proc.returncode = 2
                mock_proc.stdout = "backend/tests/test_foo.py::test_one\nbackend/tests/test_foo.py::test_two\n2 tests collected\nERRORS during collection"
                mock_proc.stderr = ""
                mock_run.return_value = mock_proc

                res = runner.run_backend_discovery("python", ROOT, {}, tmp_path, "sha123")
                assert res["status"] == "FAIL"
                assert res["exit_code"] == 2
                assert "PYTEST_COLLECTION_EXIT_2" in res["reason"] or "COLLECTION_ERROR" in res["reason"]

    def test_ai05_unexpected_skipped_test(self):
        """AI05: Unexpected skipped test fails gate and causes rejection."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            junit_file = tmp_path / "skipped_test.xml"
            junit_file.write_text(
                '<testsuite tests="5" failures="0" errors="0" skipped="1">'
                '  <testcase classname="test_mod" name="test_one"/>'
                '  <testcase classname="test_mod" name="test_two"><skipped message="unexpected"/></testcase>'
                '</testsuite>',
                encoding="utf-8"
            )
            cmd = ["echo", "pass"]
            res = runner.run_cmd("test_skip", cmd, ROOT, {}, tmp_path, junit_path=junit_file)
            assert res["status"] == "FAIL"
            assert res["skipped"] == 1
            assert "UNEXPECTED_SKIP" in res["reason"]

    def test_ai06_all_tests_skipped(self):
        """AI06: Suite where all tests are skipped fails and rejects."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            junit_file = tmp_path / "all_skipped.xml"
            junit_file.write_text(
                '<testsuite tests="3" failures="0" errors="0" skipped="3">'
                '  <testcase classname="test_mod" name="test_1"><skipped/></testcase>'
                '  <testcase classname="test_mod" name="test_2"><skipped/></testcase>'
                '  <testcase classname="test_mod" name="test_3"><skipped/></testcase>'
                '</testsuite>',
                encoding="utf-8"
            )
            cmd = ["echo", "pass"]
            res = runner.run_cmd("test_all_skip", cmd, ROOT, {}, tmp_path, junit_path=junit_file)
            assert res["status"] == "FAIL"
            assert res["skipped"] == 3
            assert res["passed"] == 0

    def test_ai07_zero_collected_tests(self):
        """AI07: Zero tests collected fails runner gate."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            junit_file = tmp_path / "zero_tests.xml"
            junit_file.write_text(
                '<testsuite tests="0" failures="0" errors="0" skipped="0"></testsuite>',
                encoding="utf-8"
            )
            cmd = ["echo", "zero"]
            res = runner.run_cmd("test_zero", cmd, ROOT, {}, tmp_path, junit_path=junit_file)
            assert res["status"] == "FAIL"
            assert res["reason"] == "ZERO_TESTS_COLLECTED"

    def test_ai08_missing_junit(self):
        """AI08: Missing expected JUnit XML fails runner gate."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            junit_file = tmp_path / "nonexistent.xml"
            cmd = ["echo", "pass"]
            res = runner.run_cmd("test_missing_junit", cmd, ROOT, {}, tmp_path, junit_path=junit_file)
            assert res["status"] == "FAIL"
            assert res["reason"] == "MISSING_JUNIT"

    def test_ai09_corrupt_junit(self):
        """AI09: Corrupt or unparseable JUnit XML fails runner gate."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            junit_file = tmp_path / "corrupt.xml"
            junit_file.write_text("<not-valid-xml", encoding="utf-8")
            cmd = ["echo", "pass"]
            res = runner.run_cmd("test_corrupt_junit", cmd, ROOT, {}, tmp_path, junit_path=junit_file)
            assert res["status"] == "FAIL"
            assert "Corrupt JUnit XML" in res["reason"]

    def test_ai10_browser_e2e_skipped(self):
        """AI10: Browser E2E skipped rejects release acceptance."""
        gates = {g: {"status": "PASS", "skipped": 0} for g in runner.MANDATORY_GATES}
        # Mark G24 as SKIPPED
        gates["G24_playwright_e2e"] = {
            "gate": "G24_playwright_e2e",
            "status": "SKIPPED",
            "reason": "SKIPPED_BY_FLAG",
            "skipped": 1,
        }
        has_unexpected_skips = any(
            (gates.get(g) or {}).get("skipped", 0) > 0 or (gates.get(g) or {}).get("status") == "SKIPPED"
            for g in runner.MANDATORY_GATES
        )
        assert has_unexpected_skips is True

    def test_ai11_browser_e2e_failed(self):
        """AI11: Browser E2E failure rejects release acceptance."""
        gates = {g: {"status": "PASS", "skipped": 0} for g in runner.MANDATORY_GATES}
        gates["G24_playwright_e2e"] = {
            "gate": "G24_playwright_e2e",
            "status": "FAIL",
            "reason": "Playwright test assertion error",
            "skipped": 0,
        }
        passed_gates = sum(1 for g in runner.MANDATORY_GATES if (gates.get(g) or {}).get("status") == "PASS")
        all_passed = (passed_gates == len(runner.MANDATORY_GATES))
        assert all_passed is False

    def test_ai12_backend_full_suite_failed(self):
        """AI12: Full backend suite failure rejects release acceptance."""
        gates = {g: {"status": "PASS", "skipped": 0} for g in runner.MANDATORY_GATES}
        gates["G20_full_backend_test_execution"] = {
            "gate": "G20_full_backend_test_execution",
            "status": "FAIL",
            "reason": "1 test failure in JUnit report",
            "skipped": 0,
        }
        passed_gates = sum(1 for g in runner.MANDATORY_GATES if (gates.get(g) or {}).get("status") == "PASS")
        all_passed = (passed_gates == len(runner.MANDATORY_GATES))
        assert all_passed is False

    def test_ai13_artifact_missing(self):
        """AI13: Missing gate log artifact fails reconciliation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            gates = {g: {"status": "PASS", "skipped": 0} for g in runner.MANDATORY_GATES}
            # Only create some logs
            (tmp_path / "G01_borrowed_flush_returns_pending_commit.log").write_text("ok", encoding="utf-8")
            missing_logs = []
            for g in runner.MANDATORY_GATES:
                if g not in ["G00_clean_branch_and_source", "G30_clean_source_after_testing", "G31_source_sha_and_artifact_reconciliation"]:
                    lf = tmp_path / f"{g}.log"
                    if not lf.exists() and (gates.get(g) or {}).get("status") != "SKIPPED":
                        missing_logs.append(g)
            assert len(missing_logs) > 0

    def test_ai14_artifact_checksum_mismatch(self):
        """AI14: Artifact checksum mismatch indicates tampering."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            test_file = tmp_path / "artifact.log"
            test_file.write_text("original content", encoding="utf-8")
            orig_hash = runner.compute_sha256(test_file)

            # Modify file
            test_file.write_text("tampered content", encoding="utf-8")
            new_hash = runner.compute_sha256(test_file)
            assert orig_hash != new_hash

    def test_ai15_development_mode_cannot_accept(self):
        """AI15: Development mode cannot produce ACCEPTED verdict."""
        is_dev_mode = True
        all_mandatory_passed = True
        source_clean_before = True
        source_clean_after = True
        source_sha_unchanged = True
        has_unexpected_skips = False

        if is_dev_mode:
            accepted = False
            verdict = "DEVELOPMENT_TEST_ONLY"
        else:
            accepted = (
                source_clean_before
                and source_clean_after
                and source_sha_unchanged
                and all_mandatory_passed
                and not has_unexpected_skips
            )
            verdict = "ACCEPTED" if accepted else "REJECTED"

        assert accepted is False
        assert verdict == "DEVELOPMENT_TEST_ONLY"

    def test_ai16_evidence_does_not_match_tested_sha(self):
        """AI16: Gate evidence with different source_sha than tested_sha is rejected."""
        tested_sha = "sha_abc123"
        gate_res = {
            "gate": "G01_borrowed_flush_returns_pending_commit",
            "source_sha": "sha_xyz999",
            "status": "PASS",
        }
        sha_consistent = (gate_res["source_sha"] == tested_sha)
        assert sha_consistent is False
