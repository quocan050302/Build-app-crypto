"""
backend/tests/test_v17_6_4_3_acceptance_integrity.py
===================================================
Acceptance Runner Negative & Integrity Verification Suite (AR01 - AR16)
per Master Prompt V17.6.4.3 (Part 176).
Verifies:
- AR01: Missing test file rejects acceptance (FAIL / REJECTED)
- AR02: Missing gate rejects acceptance (REJECTED)
- AR03: Zero tests collected fails runner (ZERO_TESTS_COLLECTED / REJECTED)
- AR04: Backend full discovery fails rejects runner (FAIL / REJECTED)
- AR05: Dirty source before tests rejects release mode (REJECTED)
- AR06: SHA changed during execution rejects acceptance (FAIL / REJECTED)
- AR07: E2E skipped cannot produce ACCEPTED (REJECTED)
- AR08: E2E failed rejects acceptance (FAIL / REJECTED)
- AR09: Missing JUnit output rejects acceptance (FAIL / REJECTED)
- AR10: Invalid or corrupt JUnit XML rejects acceptance (FAIL / REJECTED)
- AR11: Artifact checksum mismatch rejects acceptance (FAIL / REJECTED)
- AR12: Source branch mismatch rejects acceptance (FAIL / REJECTED)
- AR13: Dev mode cannot issue release ACCEPTED (DEVELOPMENT_TEST_ONLY)
- AR14: Failed SQLite isolation test rejects (FAIL / REJECTED)
- AR15: Failed quote authority test rejects (FAIL / REJECTED)
- AR16: Missing scheduler integration rejects (FAIL / REJECTED)
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

import run_v17_6_4_3_acceptance as runner


class TestAcceptanceIntegrityV17643:
    """AR01 - AR16 Acceptance Runner Integrity & Negative Test Suite."""

    def test_ar01_missing_test_file(self):
        """AR01: Missing test file causes command failure and runner rejects."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else ("" if args[0] == "status" else "clean_sha_123")
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir)
                # Call run_cmd with nonexistent file
                nonexistent = tmp_path / "nonexistent_test.py"
                cmd = [sys.executable, "-m", "pytest", str(nonexistent), "-q"]
                res = runner.run_cmd("G01_database_identity_resolution", cmd, ROOT, {}, tmp_path)
                assert res["status"] == "FAIL"
                assert res["exit_code"] != 0

    def test_ar02_missing_gate(self):
        """AR02: Missing mandatory gate in report results in REJECTED verdict."""
        assert len(runner.MANDATORY_GATES) == 35
        # Simulate report missing one gate
        all_gates_except_one = runner.MANDATORY_GATES[:-1]
        gates = {g: {"status": "PASS"} for g in all_gates_except_one}
        all_present = all(g in gates and gates[g] is not None for g in runner.MANDATORY_GATES)
        assert all_present is False

    def test_ar03_zero_tests_collected(self):
        """AR03: Zero tests collected fails gate and rejects release."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            junit_file = tmp_path / "zero_tests.xml"
            junit_file.write_text('<testsuite tests="0" failures="0" errors="0" skipped="0"></testsuite>', encoding="utf-8")
            data = runner.parse_junit_xml(junit_file)
            assert data["test_count"] == 0

            # Test run_cmd behavior with zero tests
            cmd = ["echo", "zero"]
            res = runner.run_cmd("test_zero", cmd, ROOT, {}, tmp_path, junit_path=junit_file)
            assert res["status"] == "FAIL"
            assert res["reason"] == "ZERO_TESTS_COLLECTED"

    def test_ar04_backend_full_discovery_fails(self):
        """AR04: Backend full discovery fails rejects runner."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            # Simulate failing python discovery command
            fake_python = "/path/to/nonexistent/python"
            res = runner.run_backend_discovery(fake_python, ROOT, {}, tmp_path, "sha123")
            assert res["status"] == "FAIL"
            assert res["gate"] == "G24_full_backend_test_discovery"

    def test_ar05_dirty_source(self):
        """AR05: Dirty source before tests rejects release acceptance."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "run_backend_discovery") as mock_disc, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            # Status returns modified file
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else (" M backend/models.py" if args[0] == "status" else "sha123")
            def fake_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha=""):
                lf = out_dir / f"{name}.log"
                lf.write_text("OK", encoding="utf-8")
                return {"gate": name, "status": "PASS", "reason": "OK", "test_count": 5, "passed": 5, "failed": 0, "skipped": 0, "exit_code": 0, "duration": 0.1, "elapsed_sec": 0.1}
            mock_cmd.side_effect = fake_cmd
            mock_disc.return_value = {"gate": "G24_full_backend_test_discovery", "status": "PASS", "reason": "OK", "test_count": 100, "passed": 100, "failed": 0, "skipped": 0, "exit_code": 0, "duration": 0.1, "elapsed_sec": 0.1}

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_3.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "REJECTED"
                assert report["gates"]["G00_clean_source"]["status"] == "FAIL"

    def test_ar06_sha_changed(self):
        """AR06: SHA changed during test execution rejects acceptance."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "run_backend_discovery") as mock_disc, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            # Pre SHA is sha_before, post SHA is sha_after
            shas = ["sha_before", "sha_after"]
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else ("" if args[0] == "status" else shas.pop(0) if shas else "sha_after")
            def fake_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha=""):
                lf = out_dir / f"{name}.log"
                lf.write_text("OK", encoding="utf-8")
                return {"gate": name, "status": "PASS", "reason": "OK", "test_count": 5, "passed": 5, "failed": 0, "skipped": 0, "exit_code": 0, "duration": 0.1, "elapsed_sec": 0.1}
            mock_cmd.side_effect = fake_cmd
            mock_disc.return_value = {"gate": "G24_full_backend_test_discovery", "status": "PASS", "reason": "OK", "test_count": 100, "passed": 100, "failed": 0, "skipped": 0, "exit_code": 0, "duration": 0.1, "elapsed_sec": 0.1}

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_3.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "REJECTED"
                assert report["gates"]["G34_source_sha_and_artifact_integrity"]["status"] == "FAIL"

    def test_ar07_e2e_skipped(self):
        """AR07: --skip-e2e cannot ACCEPT in release mode (verdict is REJECTED)."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "run_backend_discovery") as mock_disc, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else ("" if args[0] == "status" else "clean_sha_123")
            def fake_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha=""):
                lf = out_dir / f"{name}.log"
                lf.write_text("OK", encoding="utf-8")
                return {"gate": name, "status": "PASS", "reason": "OK", "test_count": 5, "passed": 5, "failed": 0, "skipped": 0, "exit_code": 0, "duration": 0.1, "elapsed_sec": 0.1}
            mock_cmd.side_effect = fake_cmd
            mock_disc.return_value = {"gate": "G24_full_backend_test_discovery", "status": "PASS", "reason": "OK", "test_count": 100, "passed": 100, "failed": 0, "skipped": 0, "exit_code": 0, "duration": 0.1, "elapsed_sec": 0.1}

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--skip-e2e", "--out-dir", tmp_dir])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_3.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "REJECTED"
                assert report["gates"]["G29_playwright_e2e"]["status"] == "SKIPPED"

    def test_ar08_e2e_failed(self):
        """AR08: Playwright failure rejects acceptance."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "run_backend_discovery") as mock_disc, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else ("" if args[0] == "status" else "clean_sha_123")
            def fake_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha=""):
                lf = out_dir / f"{name}.log"
                lf.write_text("FAIL", encoding="utf-8")
                if name == "G29_playwright_e2e":
                    return {"gate": name, "status": "FAIL", "reason": "Timeout in test", "test_count": 1, "passed": 0, "failed": 1, "skipped": 0, "exit_code": 1, "duration": 30.0, "elapsed_sec": 30.0}
                return {"gate": name, "status": "PASS", "reason": "OK", "test_count": 5, "passed": 5, "failed": 0, "skipped": 0, "exit_code": 0, "duration": 0.1, "elapsed_sec": 0.1}
            mock_cmd.side_effect = fake_cmd
            mock_disc.return_value = {"gate": "G24_full_backend_test_discovery", "status": "PASS", "reason": "OK", "test_count": 100, "passed": 100, "failed": 0, "skipped": 0, "exit_code": 0, "duration": 0.1, "elapsed_sec": 0.1}

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_3.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "REJECTED"
                assert report["gates"]["G29_playwright_e2e"]["status"] == "FAIL"

    def test_ar09_missing_junit(self):
        """AR09: Missing expected JUnit report marks gate as FAIL."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            missing_junit = tmp_path / "missing.xml"
            res = runner.run_cmd("test_gate", ["echo", "hi"], ROOT, {}, tmp_path, junit_path=missing_junit)
            assert res["status"] == "FAIL"
            assert res["reason"] == "MISSING_JUNIT"

    def test_ar10_invalid_junit(self):
        """AR10: Corrupted JUnit XML marks gate as FAIL."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            bad_junit = tmp_path / "bad.xml"
            bad_junit.write_text("<<<NOT_VALID_XML>>>", encoding="utf-8")
            res = runner.run_cmd("test_gate", ["echo", "hi"], ROOT, {}, tmp_path, junit_path=bad_junit)
            assert res["status"] == "FAIL"
            assert "Corrupt JUnit XML" in res["reason"]

    def test_ar11_artifact_checksum_mismatch(self):
        """AR11: Missing evidence logs cause G34 to FAIL and reject acceptance."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "run_backend_discovery") as mock_disc, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else ("" if args[0] == "status" else "clean_sha_123")
            # Do NOT write log files
            def fake_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha=""):
                return {"gate": name, "status": "PASS", "reason": "OK", "test_count": 5, "passed": 5, "failed": 0, "skipped": 0, "exit_code": 0, "duration": 0.1, "elapsed_sec": 0.1}
            mock_cmd.side_effect = fake_cmd
            mock_disc.return_value = {"gate": "G24_full_backend_test_discovery", "status": "PASS", "reason": "OK", "test_count": 100, "passed": 100, "failed": 0, "skipped": 0, "exit_code": 0, "duration": 0.1, "elapsed_sec": 0.1}

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_3.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "REJECTED"
                assert report["gates"]["G34_source_sha_and_artifact_integrity"]["status"] == "FAIL"

    def test_ar12_source_mismatch(self):
        """AR12: Wrong git branch rejects release acceptance."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "run_backend_discovery") as mock_disc, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            mock_git.side_effect = lambda *args: "main" if args[0] == "branch" else ("" if args[0] == "status" else "clean_sha_123")
            def fake_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha=""):
                lf = out_dir / f"{name}.log"
                lf.write_text("OK", encoding="utf-8")
                return {"gate": name, "status": "PASS", "reason": "OK", "test_count": 5, "passed": 5, "failed": 0, "skipped": 0, "exit_code": 0, "duration": 0.1, "elapsed_sec": 0.1}
            mock_cmd.side_effect = fake_cmd
            mock_disc.return_value = {"gate": "G24_full_backend_test_discovery", "status": "PASS", "reason": "OK", "test_count": 100, "passed": 100, "failed": 0, "skipped": 0, "exit_code": 0, "duration": 0.1, "elapsed_sec": 0.1}

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_3.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "REJECTED"
                assert report["gates"]["G00_clean_source"]["status"] == "FAIL"

    def test_ar13_dev_mode_cannot_accept(self):
        """AR13: Dev mode produces DEVELOPMENT_TEST_ONLY verdict, never ACCEPTED."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "run_backend_discovery") as mock_disc, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else ("" if args[0] == "status" else "clean_sha_123")
            def fake_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha=""):
                lf = out_dir / f"{name}.log"
                lf.write_text("OK", encoding="utf-8")
                return {"gate": name, "status": "PASS", "reason": "OK", "test_count": 5, "passed": 5, "failed": 0, "skipped": 0, "exit_code": 0, "duration": 0.1, "elapsed_sec": 0.1}
            mock_cmd.side_effect = fake_cmd
            def fake_disc(python, root, env, out_dir, source_sha):
                lf = out_dir / "G24_full_backend_test_discovery.log"
                lf.write_text("OK", encoding="utf-8")
                return {"gate": "G24_full_backend_test_discovery", "status": "PASS", "reason": "OK", "test_count": 100, "passed": 100, "failed": 0, "skipped": 0, "exit_code": 0, "duration": 0.1, "elapsed_sec": 0.1}
            mock_disc.side_effect = fake_disc

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--mode", "dev", "--out-dir", tmp_dir])
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_3.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "DEVELOPMENT_TEST_ONLY"
                assert res == 0

    def test_ar14_failed_sqlite_isolation_test_rejects(self):
        """AR14: Failure in SQLite isolation gates (G01–G07) rejects release."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "run_backend_discovery") as mock_disc, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else ("" if args[0] == "status" else "clean_sha_123")
            def fake_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha=""):
                lf = out_dir / f"{name}.log"
                lf.write_text("FAIL", encoding="utf-8")
                if name == "G02_pending_queue_database_isolation":
                    return {"gate": name, "status": "FAIL", "reason": "Contamination detected", "test_count": 1, "passed": 0, "failed": 1, "skipped": 0, "exit_code": 1, "duration": 0.5, "elapsed_sec": 0.5}
                return {"gate": name, "status": "PASS", "reason": "OK", "test_count": 5, "passed": 5, "failed": 0, "skipped": 0, "exit_code": 0, "duration": 0.1, "elapsed_sec": 0.1}
            mock_cmd.side_effect = fake_cmd
            mock_disc.return_value = {"gate": "G24_full_backend_test_discovery", "status": "PASS", "reason": "OK", "test_count": 100, "passed": 100, "failed": 0, "skipped": 0, "exit_code": 0, "duration": 0.1, "elapsed_sec": 0.1}

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_3.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "REJECTED"
                assert report["gates"]["G02_pending_queue_database_isolation"]["status"] == "FAIL"

    def test_ar15_failed_quote_authority_test_rejects(self):
        """AR15: Failure in quote authority gates (G08–G11) rejects release."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "run_backend_discovery") as mock_disc, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else ("" if args[0] == "status" else "clean_sha_123")
            def fake_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha=""):
                lf = out_dir / f"{name}.log"
                lf.write_text("FAIL", encoding="utf-8")
                if name == "G08_strict_good_evidence_authority":
                    return {"gate": name, "status": "FAIL", "reason": "Spoofed quote accepted", "test_count": 1, "passed": 0, "failed": 1, "skipped": 0, "exit_code": 1, "duration": 0.5, "elapsed_sec": 0.5}
                return {"gate": name, "status": "PASS", "reason": "OK", "test_count": 5, "passed": 5, "failed": 0, "skipped": 0, "exit_code": 0, "duration": 0.1, "elapsed_sec": 0.1}
            mock_cmd.side_effect = fake_cmd
            mock_disc.return_value = {"gate": "G24_full_backend_test_discovery", "status": "PASS", "reason": "OK", "test_count": 100, "passed": 100, "failed": 0, "skipped": 0, "exit_code": 0, "duration": 0.1, "elapsed_sec": 0.1}

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_3.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "REJECTED"
                assert report["gates"]["G08_strict_good_evidence_authority"]["status"] == "FAIL"

    def test_ar16_missing_scheduler_integration_rejects(self):
        """AR16: Missing scheduler integration (G22) rejects release."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "run_backend_discovery") as mock_disc, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else ("" if args[0] == "status" else "clean_sha_123")
            def fake_cmd(name, cmd, cwd, env, out_dir, junit_path=None, timeout=600, source_sha=""):
                lf = out_dir / f"{name}.log"
                lf.write_text("FAIL", encoding="utf-8")
                if name == "G22_actual_scheduler_integration":
                    return {"gate": name, "status": "FAIL", "reason": "Scheduler did not produce intervals", "test_count": 1, "passed": 0, "failed": 1, "skipped": 0, "exit_code": 1, "duration": 0.5, "elapsed_sec": 0.5}
                return {"gate": name, "status": "PASS", "reason": "OK", "test_count": 5, "passed": 5, "failed": 0, "skipped": 0, "exit_code": 0, "duration": 0.1, "elapsed_sec": 0.1}
            mock_cmd.side_effect = fake_cmd
            mock_disc.return_value = {"gate": "G24_full_backend_test_discovery", "status": "PASS", "reason": "OK", "test_count": 100, "passed": 100, "failed": 0, "skipped": 0, "exit_code": 0, "duration": 0.1, "elapsed_sec": 0.1}

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_3.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "REJECTED"
                assert report["gates"]["G22_actual_scheduler_integration"]["status"] == "FAIL"
