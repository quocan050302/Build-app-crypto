"""
backend/tests/test_v17_6_4_1_acceptance_runner.py
=================================================
Acceptance Runner Self-Verification Test Suite (AC01 - AC12).
Verifies the integrity, fail-closed enforcement, clean worktree checking,
and audit trail generation of tools/run_v17_6_4_1_acceptance.py.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

# Add tools directory to path to import runner
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import run_v17_6_4_1_acceptance as runner


class TestAcceptanceRunnerSelfVerification:
    """Test suite AC01 - AC12 verifying the acceptance runner itself."""

    def test_ac01_exactly_30_mandatory_gates(self):
        """AC01: Runner specifies exactly 30 mandatory gates (G00 to G29)."""
        assert len(runner.MANDATORY_GATES) == 30
        assert runner.MANDATORY_GATES[0] == "G00_clean_worktree_pre"
        assert runner.MANDATORY_GATES[-1] == "G29_clean_worktree_post"
        # Verify continuous numbering
        for idx in range(30):
            gate_prefix = f"G{idx:02d}_"
            assert any(g.startswith(gate_prefix) for g in runner.MANDATORY_GATES), f"Missing gate {gate_prefix}"

    def test_ac02_branch_enforcement(self):
        """AC02: Runner verifies target branch is fix/v14-1-rr-flow."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            mock_git.side_effect = lambda *args: "wrong-branch" if args[0] == "branch" else ("" if args[0] == "status" else "abc123")
            mock_cmd.return_value = {"gate": "mock", "exit_code": 0, "status": "PASS", "elapsed_sec": 0.01, "log": "log", "output_tail": "ok"}
            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--allow-dirty", "--out-dir", tmp_dir, "--skip-e2e"])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_1.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "REJECTED"
                assert report["gates"]["G00_clean_worktree_pre"]["status"] == "FAIL"

    def test_ac03_g00_fails_on_dirty_worktree(self):
        """AC03: G00 fails when git worktree is dirty without --allow-dirty."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            def fake_git(*args):
                if args[0] == "branch":
                    return "fix/v14-1-rr-flow"
                if args[0] == "status":
                    return " M modified_file.py"
                return "sha123"
            mock_git.side_effect = fake_git
            mock_cmd.return_value = {"gate": "mock", "exit_code": 0, "status": "PASS", "elapsed_sec": 0.01, "log": "log", "output_tail": "ok"}
            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir, "--skip-e2e"])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_1.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["gates"]["G00_clean_worktree_pre"]["status"] == "FAIL"

    def test_ac04_g29_fails_on_post_dirty_worktree(self):
        """AC04: G29 fails when post-test worktree is dirty or modified."""
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
                    # Clean on pre-check (1st call), dirty on post-check (2nd call)
                    return "" if call_count["status"] == 1 else "?? untracked_leak.py"
                return "sha123"
            mock_git.side_effect = fake_git
            mock_cmd.return_value = {"gate": "mock", "exit_code": 0, "status": "PASS", "elapsed_sec": 0.01, "log": "log", "output_tail": "ok"}
            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir, "--skip-e2e"])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_1.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["gates"]["G29_clean_worktree_post"]["status"] == "FAIL"

    def test_ac05_g29_verifies_sha_match(self):
        """AC05: G29 verifies post-test git SHA matches pre-test source SHA."""
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
                    return "pre_sha_123" if call_count["sha"] == 1 else "post_sha_456"
                return ""
            mock_git.side_effect = fake_git
            mock_cmd.return_value = {"gate": "mock", "exit_code": 0, "status": "PASS", "elapsed_sec": 0.01, "log": "log", "output_tail": "ok"}
            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir, "--skip-e2e"])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_1.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["gates"]["G29_clean_worktree_post"]["status"] == "FAIL"
                assert "mismatch" in report["gates"]["G29_clean_worktree_post"]["reason"].lower()

    def test_ac06_g27_detects_live_execution_violations(self):
        """AC06: G27 correctly detects live execution violations if LIVE_EXCHANGE_ORDER_EXECUTION = True."""
        with patch.object(runner.os, "walk") as mock_walk, \
             patch.object(Path, "read_text") as mock_read:
            mock_walk.return_value = [("/path/backend/services", [], ["forbidden_service.py"])]
            mock_read.return_value = "LIVE_EXCHANGE_ORDER_EXECUTION = True"
            ok, violations = runner.verify_zero_live_execution()
            assert ok is False
            assert len(violations) > 0

    def test_ac07_g28_detects_strategy_drift(self):
        """AC07: G28 detects missing essential SMC/ICT symbol or file."""
        with patch.object(Path, "exists") as mock_exists:
            mock_exists.return_value = False
            ok, violations = runner.verify_zero_strategy_drift()
            assert ok is False
            assert any("Missing core file" in v for v in violations)

    def test_ac08_runner_writes_external_artifacts(self):
        """AC08: Runner writes test logs and final JSON report to the external output directory."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)
            cmd_res = runner.run_cmd("test_gate", ["echo", "hello"], ROOT, os.environ, out_dir)
            assert cmd_res["status"] == "PASS"
            log_path = Path(cmd_res["log"])
            assert log_path.exists()
            assert "hello" in log_path.read_text(encoding="utf-8")

    def test_ac09_accepted_verdict_when_all_gates_pass(self):
        """AC09: Runner verdict is ACCEPTED when all 30 gates pass."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "verify_zero_live_execution") as mock_live, \
             patch.object(runner, "verify_zero_strategy_drift") as mock_drift:
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else ("" if args[0] == "status" else "sha123")
            mock_cmd.side_effect = lambda name, cmd, cwd, env, out_dir, timeout=600: {
                "gate": name, "exit_code": 0, "status": "PASS", "elapsed_sec": 0.1, "log": "log", "output_tail": "ok"
            }
            mock_live.return_value = (True, [])
            mock_drift.return_value = (True, [])

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir])
                assert res == 0
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_1.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "ACCEPTED"
                assert report["passed_gates"] == 30
                assert report["total_gates"] == 30

    def test_ac13_skip_e2e_cannot_accept(self):
        """AC13: --skip-e2e sets G26 to SKIPPED and causes REJECTED verdict."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "verify_zero_live_execution") as mock_live, \
             patch.object(runner, "verify_zero_strategy_drift") as mock_drift:
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else ("" if args[0] == "status" else "sha123")
            mock_cmd.side_effect = lambda name, cmd, cwd, env, out_dir, timeout=600: {
                "gate": name, "exit_code": 0, "status": "PASS", "elapsed_sec": 0.1, "log": "log", "output_tail": "ok"
            }
            mock_live.return_value = (True, [])
            mock_drift.return_value = (True, [])

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir, "--skip-e2e"])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_1.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "REJECTED"
                assert report["gates"]["G26_playwright_e2e"]["status"] == "SKIPPED"

    def test_ac10_rejected_verdict_when_any_gate_fails(self):
        """AC10: Runner verdict is REJECTED when any gate fails."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "verify_zero_live_execution") as mock_live, \
             patch.object(runner, "verify_zero_strategy_drift") as mock_drift:
            mock_git.side_effect = lambda *args: "fix/v14-1-rr-flow" if args[0] == "branch" else ("" if args[0] == "status" else "sha123")
            # Make G01 fail
            def fake_cmd(name, cmd, cwd, env, out_dir, timeout=600):
                code = 1 if name == "G01_feed_evidence_truth" else 0
                return {
                    "gate": name, "exit_code": code, "status": "FAIL" if code != 0 else "PASS",
                    "elapsed_sec": 0.1, "log": "log", "output_tail": "failed"
                }
            mock_cmd.side_effect = fake_cmd
            mock_live.return_value = (True, [])
            mock_drift.return_value = (True, [])

            with tempfile.TemporaryDirectory() as tmp_dir:
                res = runner.main(["--out-dir", tmp_dir, "--skip-e2e"])
                assert res == 1
                report_file = Path(tmp_dir) / "acceptance_v17_6_4_1.json"
                report = json.loads(report_file.read_text(encoding="utf-8"))
                assert report["verdict"] == "REJECTED"
                assert report["gates"]["G01_feed_evidence_truth"]["status"] == "FAIL"

    def test_ac11_command_execution_helper_captures_details(self):
        """AC11: Command execution helper records exit code, execution time, and stdout/stderr output tail."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)
            cmd_res = runner.run_cmd("tail_test", [sys.executable, "-c", "import sys; print('Line 1'); sys.stderr.write('Line 2\\n')"], ROOT, os.environ, out_dir)
            assert cmd_res["exit_code"] == 0
            assert cmd_res["elapsed_sec"] >= 0.0
            assert "Line 1" in cmd_res["output_tail"] or "Line 2" in cmd_res["output_tail"]

    def test_ac12_runner_exit_code_matches_verdict(self):
        """AC12: Runner exits with status code 0 on ACCEPTED and non-zero on REJECTED."""
        with patch.object(runner, "git") as mock_git, \
             patch.object(runner, "run_cmd") as mock_cmd, \
             patch.object(runner, "verify_zero_live_execution", return_value=(True, [])), \
             patch.object(runner, "verify_zero_strategy_drift", return_value=(True, [])):
            mock_git.side_effect = lambda *args: "wrong_branch" if args[0] == "branch" else ("" if args[0] == "status" else "sha123")
            mock_cmd.return_value = {"gate": "mock", "exit_code": 0, "status": "PASS", "elapsed_sec": 0.01, "log": "log", "output_tail": "ok"}
            with tempfile.TemporaryDirectory() as tmp_dir:
                exit_code = runner.main(["--out-dir", tmp_dir, "--skip-e2e"])
                assert exit_code != 0
