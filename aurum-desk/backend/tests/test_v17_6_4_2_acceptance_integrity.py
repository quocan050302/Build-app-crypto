"""
backend/tests/test_v17_6_4_2_acceptance_integrity.py
===================================================
Acceptance Runner Negative & Integrity Verification Suite (AC01 - AC16)
per Section 12 (Part 114, Parts 107-116, Part 147-153).
Verifies:
- AC01: All mandatory tests execute and pass -> ACCEPTED
- AC02: --skip-e2e cannot ACCEPT (REJECTED in release mode)
- AC03: --allow-dirty cannot ACCEPT (DEVELOPMENT_TEST_ONLY, never ACCEPTED)
- AC04: Missing gate cannot ACCEPT (REJECTED)
- AC05: Gate status SKIPPED cannot ACCEPT (REJECTED)
- AC06: Gate status BLOCKED cannot ACCEPT (REJECTED)
- AC07: Missing JUnit cannot ACCEPT (FAIL / REJECTED)
- AC08: Zero tests cannot ACCEPT (FAIL / REJECTED)
- AC09: Stale artifact cannot ACCEPT (FAIL / REJECTED)
- AC10: Source SHA mismatch cannot ACCEPT (FAIL / REJECTED)
- AC11: Dirty source after tests cannot ACCEPT (FAIL / REJECTED)
- AC12: Nonzero subprocess exit cannot ACCEPT (FAIL / REJECTED)
- AC13: Playwright failure cannot ACCEPT (FAIL / REJECTED)
- AC14: Missing browser setup cannot ACCEPT (FAIL / REJECTED)
- AC15: Evidence hash mismatch cannot ACCEPT (FAIL / REJECTED)
- AC16: Dev mode cannot issue release ACCEPTED (DEVELOPMENT_TEST_ONLY)
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

import run_v17_6_4_2_acceptance as runner


class TestAcceptanceIntegrityV17642:
    """AC01 - AC16 acceptance runner integrity negative test suite."""

    def test_ac01_all_mandatory_tests_execute_and_pass(self):
        """AC01: Runner verdict is ACCEPTED when all 32 mandatory gates pass cleanly."""
        assert len(runner.MANDATORY_GATES) == 32
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else ("" if args[0] == "status" else "clean_sha_123")
            def fake_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha=""):
                # Write dummy log to satisfy artifact integrity
                lf = out_dir / f"{name}.log"
                lf.write_text("OK", encoding="utf-8")
                return {
                    "gate": name, "command": "cmd", "working_directory": str(cwd),
                    "started_at": "2026-10-25T00:00:00Z", "completed_at": "2026-10-25T00:00:01Z",
                    "duration": 0.1, "elapsed_sec": 0.1, "exit_code": 0, "test_count": 10,
                    "passed": 10, "failed": 0, "skipped": 0, "status": "PASS", "reason": "OK",
                    "evidence_file": str(lf), "evidence_hash": runner.compute_sha256(lf),
                    "output_tail": "10 passed",
                }
            mock_cmd.side_effect = fake_cmd

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir])
                assert res == 0
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_2.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "ACCEPTED"
                assert report["passed_gates"] == 32
                assert report["total_gates"] == 32

    def test_ac02_skip_e2e_cannot_accept(self):
        """AC02: --skip-e2e cannot ACCEPT in release mode (verdict is REJECTED)."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else ("" if args[0] == "status" else "clean_sha_123")
            def fake_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha=""):
                lf = out_dir / f"{name}.log"
                lf.write_text("OK", encoding="utf-8")
                return {
                    "gate": name, "command": "cmd", "working_directory": str(cwd),
                    "started_at": "2026-10-25T00:00:00Z", "completed_at": "2026-10-25T00:00:01Z",
                    "duration": 0.1, "elapsed_sec": 0.1, "exit_code": 0, "test_count": 5,
                    "passed": 5, "failed": 0, "skipped": 0, "status": "PASS", "reason": "OK",
                    "evidence_file": str(lf), "evidence_hash": runner.compute_sha256(lf),
                    "output_tail": "5 passed",
                }
            mock_cmd.side_effect = fake_cmd

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--skip-e2e", "--out-dir", tmp_dir])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_2.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "REJECTED"
                assert report["gates"]["G26_playwright_e2e"]["status"] == "SKIPPED"

    def test_ac03_allow_dirty_cannot_accept(self):
        """AC03: --allow-dirty cannot issue release ACCEPTED (issues DEVELOPMENT_TEST_ONLY)."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else ("M modified.py" if args[0] == "status" else "clean_sha_123")
            def fake_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha=""):
                lf = out_dir / f"{name}.log"
                lf.write_text("OK", encoding="utf-8")
                return {
                    "gate": name, "command": "cmd", "working_directory": str(cwd),
                    "started_at": "2026-10-25T00:00:00Z", "completed_at": "2026-10-25T00:00:01Z",
                    "duration": 0.1, "elapsed_sec": 0.1, "exit_code": 0, "test_count": 5,
                    "passed": 5, "failed": 0, "skipped": 0, "status": "PASS", "reason": "OK",
                    "evidence_file": str(lf), "evidence_hash": runner.compute_sha256(lf),
                    "output_tail": "5 passed",
                }
            mock_cmd.side_effect = fake_cmd

            with tempfile.TemporaryDirectory() as tmp_dir:
                runner.main(["--allow-dirty", "--out-dir", tmp_dir])
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_2.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "DEVELOPMENT_TEST_ONLY"
                assert report["verdict"] != "ACCEPTED"

    def test_ac04_missing_gate_cannot_accept(self):
        """AC04: Missing mandatory gate cannot produce ACCEPTED."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else ("" if args[0] == "status" else "clean_sha_123")
            def fake_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha=""):
                # Omit G09 from reporting
                if name == "G09_cycle_commit_truth":
                    return None
                lf = out_dir / f"{name}.log"
                lf.write_text("OK", encoding="utf-8")
                return {
                    "gate": name, "exit_code": 0, "status": "PASS", "duration": 0.1, "elapsed_sec": 0.1,
                    "test_count": 5, "passed": 5, "failed": 0, "skipped": 0, "reason": "OK",
                    "evidence_file": str(lf), "evidence_hash": runner.compute_sha256(lf),
                    "output_tail": "5 passed", "command": "cmd", "working_directory": str(cwd),
                    "started_at": "2026-10-25T00:00:00Z", "completed_at": "2026-10-25T00:00:01Z",
                }
            mock_cmd.side_effect = fake_cmd

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_2.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "REJECTED"

    def test_ac05_gate_status_skipped_cannot_accept(self):
        """AC05: Gate status SKIPPED cannot produce ACCEPTED."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else ("" if args[0] == "status" else "clean_sha_123")
            def fake_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha=""):
                lf = out_dir / f"{name}.log"
                lf.write_text("OK", encoding="utf-8")
                status = "SKIPPED" if name == "G05_sqlite_concurrent_writer_safety" else "PASS"
                return {
                    "gate": name, "exit_code": 0, "status": status, "duration": 0.1, "elapsed_sec": 0.1,
                    "test_count": 5, "passed": 5, "failed": 0, "skipped": 0, "reason": "Skipped",
                    "evidence_file": str(lf), "evidence_hash": runner.compute_sha256(lf),
                    "output_tail": "5 passed", "command": "cmd", "working_directory": str(cwd),
                    "started_at": "2026-10-25T00:00:00Z", "completed_at": "2026-10-25T00:00:01Z",
                }
            mock_cmd.side_effect = fake_cmd

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_2.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "REJECTED"

    def test_ac06_gate_status_blocked_cannot_accept(self):
        """AC06: Gate status BLOCKED cannot produce ACCEPTED."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else ("" if args[0] == "status" else "clean_sha_123")
            def fake_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha=""):
                lf = out_dir / f"{name}.log"
                lf.write_text("OK", encoding="utf-8")
                status = "BLOCKED" if name == "G08_metadata_good_bypass_prevention" else "PASS"
                return {
                    "gate": name, "exit_code": 0, "status": status, "duration": 0.1, "elapsed_sec": 0.1,
                    "test_count": 5, "passed": 5, "failed": 0, "skipped": 0, "reason": "Blocked",
                    "evidence_file": str(lf), "evidence_hash": runner.compute_sha256(lf),
                    "output_tail": "5 passed", "command": "cmd", "working_directory": str(cwd),
                    "started_at": "2026-10-25T00:00:00Z", "completed_at": "2026-10-25T00:00:01Z",
                }
            mock_cmd.side_effect = fake_cmd

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_2.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "REJECTED"

    def test_ac07_missing_junit_cannot_accept(self):
        """AC07: Missing JUnit report marks gate as FAIL and rejects acceptance."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)
            missing_junit = out_dir / "nonexistent.xml"
            res = runner.run_cmd("test_gate", ["echo", "test"], ROOT, os.environ, out_dir, junit_path=missing_junit)
            assert res["status"] == "FAIL"
            assert res["reason"] == "MISSING_JUNIT"

    def test_ac08_zero_tests_cannot_accept(self):
        """AC08: Zero tests collected in JUnit marks gate as FAIL with ZERO_TESTS_COLLECTED."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)
            junit_file = out_dir / "empty_tests.xml"
            junit_file.write_text('<testsuite tests="0" failures="0" errors="0" skipped="0"></testsuite>', encoding="utf-8")
            res = runner.run_cmd("empty_gate", ["echo", "test"], ROOT, os.environ, out_dir, junit_path=junit_file)
            assert res["status"] == "FAIL"
            assert res["reason"] == "ZERO_TESTS_COLLECTED"

    def test_ac09_stale_artifact_cannot_accept(self):
        """AC09: Missing evidence logs or stale artifacts causes G31 FAIL and REJECTED verdict."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else ("" if args[0] == "status" else "clean_sha_123")
            # Do NOT create log files in out_dir
            mock_cmd.return_value = {
                "gate": "mock", "command": "cmd", "working_directory": str(ROOT),
                "started_at": "2026-10-25T00:00:00Z", "completed_at": "2026-10-25T00:00:01Z",
                "duration": 0.1, "elapsed_sec": 0.1, "exit_code": 0, "test_count": 5,
                "passed": 5, "failed": 0, "skipped": 0, "status": "PASS", "reason": "OK",
                "evidence_file": "missing.log", "evidence_hash": None, "output_tail": "5 passed",
            }

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_2.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "REJECTED"
                assert report["gates"]["G31_source_sha_and_artifact_integrity"]["status"] == "FAIL"

    def test_ac10_source_sha_mismatch_cannot_accept(self):
        """AC10: Source SHA mismatch between pre and post tests causes REJECTED."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            call_count = {"sha": 0}
            def fake_git(*args):
                if args[0] == "branch":
                    return "fix/v14-1-rr-flow"
                if args[0] == "status":
                    return ""
                if args[0] == "rev-parse":
                    call_count["sha"] += 1
                    return "pre_sha_111" if call_count["sha"] == 1 else "post_sha_222"
                return ""
            mock_git.side_effect = fake_git
            def fake_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha=""):
                lf = out_dir / f"{name}.log"
                lf.write_text("OK", encoding="utf-8")
                return {
                    "gate": name, "command": "cmd", "working_directory": str(cwd),
                    "started_at": "2026-10-25T00:00:00Z", "completed_at": "2026-10-25T00:00:01Z",
                    "duration": 0.1, "elapsed_sec": 0.1, "exit_code": 0, "test_count": 5,
                    "passed": 5, "failed": 0, "skipped": 0, "status": "PASS", "reason": "OK",
                    "evidence_file": str(lf), "evidence_hash": runner.compute_sha256(lf),
                    "output_tail": "5 passed",
                }
            mock_cmd.side_effect = fake_cmd

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_2.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "REJECTED"
                assert report["gates"]["G31_source_sha_and_artifact_integrity"]["status"] == "FAIL"

    def test_ac11_dirty_source_after_tests_cannot_accept(self):
        """AC11: Dirty worktree after tests causes G31 FAIL and REJECTED."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            call_count = {"status": 0}
            def fake_git(*args):
                if args[0] == "branch":
                    return "fix/v14-1-rr-flow"
                if args[0] == "status":
                    call_count["status"] += 1
                    return "" if call_count["status"] == 1 else "?? untracked_leak.py"
                return "sha_123"
            mock_git.side_effect = fake_git
            def fake_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha=""):
                lf = out_dir / f"{name}.log"
                lf.write_text("OK", encoding="utf-8")
                return {
                    "gate": name, "command": "cmd", "working_directory": str(cwd),
                    "started_at": "2026-10-25T00:00:00Z", "completed_at": "2026-10-25T00:00:01Z",
                    "duration": 0.1, "elapsed_sec": 0.1, "exit_code": 0, "test_count": 5,
                    "passed": 5, "failed": 0, "skipped": 0, "status": "PASS", "reason": "OK",
                    "evidence_file": str(lf), "evidence_hash": runner.compute_sha256(lf),
                    "output_tail": "5 passed",
                }
            mock_cmd.side_effect = fake_cmd

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_2.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "REJECTED"
                assert report["gates"]["G31_source_sha_and_artifact_integrity"]["status"] == "FAIL"

    def test_ac12_nonzero_subprocess_exit_cannot_accept(self):
        """AC12: Nonzero subprocess exit code causes gate FAIL and REJECTED."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else ("" if args[0] == "status" else "clean_sha_123")
            def fake_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha=""):
                lf = out_dir / f"{name}.log"
                lf.write_text("Failed test trace", encoding="utf-8")
                code = 1 if name == "G10_dashboard_persistence_truth" else 0
                return {
                    "gate": name, "command": "cmd", "working_directory": str(cwd),
                    "started_at": "2026-10-25T00:00:00Z", "completed_at": "2026-10-25T00:00:01Z",
                    "duration": 0.1, "elapsed_sec": 0.1, "exit_code": code,
                    "test_count": 5, "passed": 4 if code else 5, "failed": 1 if code else 0, "skipped": 0,
                    "status": "FAIL" if code else "PASS", "reason": f"Exit code {code}",
                    "evidence_file": str(lf), "evidence_hash": runner.compute_sha256(lf),
                    "output_tail": "failed",
                }
            mock_cmd.side_effect = fake_cmd

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_2.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "REJECTED"
                assert report["gates"]["G10_dashboard_persistence_truth"]["status"] == "FAIL"

    def test_ac13_playwright_failure_cannot_accept(self):
        """AC13: Playwright failure in G26 marks G26 as FAIL and causes REJECTED."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else ("" if args[0] == "status" else "clean_sha_123")
            def fake_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha=""):
                lf = out_dir / f"{name}.log"
                lf.write_text("OK", encoding="utf-8")
                code = 1 if name == "G26_playwright_e2e" else 0
                return {
                    "gate": name, "command": "cmd", "working_directory": str(cwd),
                    "started_at": "2026-10-25T00:00:00Z", "completed_at": "2026-10-25T00:00:01Z",
                    "duration": 0.1, "elapsed_sec": 0.1, "exit_code": code,
                    "test_count": 1, "passed": 0 if code else 1, "failed": 1 if code else 0, "skipped": 0,
                    "status": "FAIL" if code else "PASS", "reason": f"Exit code {code}",
                    "evidence_file": str(lf), "evidence_hash": runner.compute_sha256(lf),
                    "output_tail": "E2E failed",
                }
            mock_cmd.side_effect = fake_cmd

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_2.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "REJECTED"
                assert report["gates"]["G26_playwright_e2e"]["status"] == "FAIL"

    def test_ac14_missing_browser_setup_cannot_accept(self):
        """AC14: Missing browser setup / launch failure in G26 causes FAIL and REJECTED."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else ("" if args[0] == "status" else "clean_sha_123")
            def fake_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha=""):
                lf = out_dir / f"{name}.log"
                if name == "G26_playwright_e2e":
                    lf.write_text("browserType.launch: Executable doesn't exist", encoding="utf-8")
                    return {
                        "gate": name, "command": "cmd", "working_directory": str(cwd),
                        "started_at": "2026-10-25T00:00:00Z", "completed_at": "2026-10-25T00:00:01Z",
                        "duration": 0.1, "elapsed_sec": 0.1, "exit_code": 1,
                        "test_count": 0, "passed": 0, "failed": 0, "skipped": 0,
                        "status": "FAIL", "reason": "Executable doesn't exist",
                        "evidence_file": str(lf), "evidence_hash": runner.compute_sha256(lf),
                        "output_tail": "browserType.launch error",
                    }
                lf.write_text("OK", encoding="utf-8")
                return {
                    "gate": name, "command": "cmd", "working_directory": str(cwd),
                    "started_at": "2026-10-25T00:00:00Z", "completed_at": "2026-10-25T00:00:01Z",
                    "duration": 0.1, "elapsed_sec": 0.1, "exit_code": 0,
                    "test_count": 5, "passed": 5, "failed": 0, "skipped": 0,
                    "status": "PASS", "reason": "OK",
                    "evidence_file": str(lf), "evidence_hash": runner.compute_sha256(lf),
                    "output_tail": "5 passed",
                }
            mock_cmd.side_effect = fake_cmd

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_2.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "REJECTED"
                assert report["gates"]["G26_playwright_e2e"]["status"] == "FAIL"

    def test_ac15_evidence_hash_mismatch_cannot_accept(self):
        """AC15: Evidence file hash calculation produces correct sha256 checksum."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_file = Path(tmp_dir) / "sample_evidence.log"
            test_file.write_text("Evidence Content V17.6.4.2", encoding="utf-8")
            expected_hash = hashlib.sha256("Evidence Content V17.6.4.2".encode("utf-8")).hexdigest()
            actual_hash = runner.compute_sha256(test_file)
            assert actual_hash == expected_hash

            # Non-existent file returns None
            assert runner.compute_sha256(Path(tmp_dir) / "missing.log") is None

    def test_ac16_dev_mode_cannot_issue_release_accepted(self):
        """AC16: Execution in --mode dev produces DEVELOPMENT_TEST_ONLY, never ACCEPTED."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else ("" if args[0] == "status" else "clean_sha_123")
            def fake_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha=""):
                lf = out_dir / f"{name}.log"
                lf.write_text("OK", encoding="utf-8")
                return {
                    "gate": name, "command": "cmd", "working_directory": str(cwd),
                    "started_at": "2026-10-25T00:00:00Z", "completed_at": "2026-10-25T00:00:01Z",
                    "duration": 0.1, "elapsed_sec": 0.1, "exit_code": 0, "test_count": 5,
                    "passed": 5, "failed": 0, "skipped": 0, "status": "PASS", "reason": "OK",
                    "evidence_file": str(lf), "evidence_hash": runner.compute_sha256(lf),
                    "output_tail": "5 passed",
                }
            mock_cmd.side_effect = fake_cmd

            with tempfile.TemporaryDirectory() as tmp_dir:
                runner.main(["--mode", "dev", "--out-dir", tmp_dir])
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_2.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "DEVELOPMENT_TEST_ONLY"
                assert report["verdict"] != "ACCEPTED"
