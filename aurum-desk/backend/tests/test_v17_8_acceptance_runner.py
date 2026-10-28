"""
backend/tests/test_v17_8_acceptance_runner.py
Unit tests and negative controls for the V17.8 Acceptance Runner & Verdict Engine.
Verifies fail-closed behavior for all 42 mandatory gates.
"""

import copy
import pytest
from tools.acceptance_verdict import (
    determine_release_verdict,
    MANDATORY_GATES_V17_8,
    TEST_GATES,
    COMMAND_GATES,
    INTEGRITY_GATES,
)


def _make_passing_gate_results() -> dict:
    """Builds a completely valid, passing 42-gate results dictionary."""
    results = {}
    for gate_id in MANDATORY_GATES_V17_8:
        if gate_id in TEST_GATES:
            results[gate_id] = {
                "status": "PASS",
                "gate_type": "command",
                "counts": {"passed": 5, "failed": 0, "errors": 0, "skipped": 0, "collected": 5},
                "exit_code": 0,
            }
        elif gate_id in COMMAND_GATES:
            results[gate_id] = {
                "status": "PASS",
                "gate_type": "command",
                "exit_code": 0,
            }
        elif gate_id in INTEGRITY_GATES:
            results[gate_id] = {
                "status": "PASS",
                "gate_type": "integrity",
                "integrity_verified": True,
                "exit_code": 0,
            }
    return results


def test_verdict_all_42_gates_pass():
    """All 42 gates pass with proper counts and exit codes -> ACCEPTED_FOR_PAPER_OPT_IN."""
    gates = _make_passing_gate_results()
    assert len(gates) == 42
    verdict = determine_release_verdict(gates, MANDATORY_GATES_V17_8)
    assert verdict == "ACCEPTED_FOR_PAPER_OPT_IN"


def test_verdict_missing_gate_blocked():
    """Missing any mandatory gate fails closed with BLOCKED."""
    gates = _make_passing_gate_results()
    # Drop G29
    del gates["G29_UNICORN_BREAKER_DETECTION"]
    verdict = determine_release_verdict(gates, MANDATORY_GATES_V17_8)
    assert verdict == "BLOCKED"


def test_verdict_failed_test_gate_blocked():
    """A single failed test in a gate fails closed with BLOCKED."""
    gates = _make_passing_gate_results()
    gates["G32_STRUCTURAL_INVALIDATION_AUTHORITY"]["counts"]["failed"] = 1
    verdict = determine_release_verdict(gates, MANDATORY_GATES_V17_8)
    assert verdict == "BLOCKED"


def test_verdict_nonzero_exit_code_blocked():
    """A non-zero exit code fails closed with BLOCKED."""
    gates = _make_passing_gate_results()
    gates["G01_BRANCH_INTEGRITY"]["exit_code"] = 1
    verdict = determine_release_verdict(gates, MANDATORY_GATES_V17_8)
    assert verdict == "BLOCKED"


def test_verdict_skipped_gate_fails_closed():
    """A gate where all tests were skipped fails closed with BLOCKED."""
    gates = _make_passing_gate_results()
    gates["G30_UNICORN_FVG_OVERLAP"]["counts"] = {
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "skipped": 5,
        "collected": 5,
    }
    verdict = determine_release_verdict(gates, MANDATORY_GATES_V17_8)
    assert verdict == "BLOCKED"


def test_verdict_integrity_unverified_blocked():
    """Failed integrity verification fails closed with BLOCKED."""
    gates = _make_passing_gate_results()
    gates["G42_EXACT_SOURCE_AND_ARTIFACT_INTEGRITY"]["integrity_verified"] = False
    verdict = determine_release_verdict(gates, MANDATORY_GATES_V17_8)
    assert verdict == "BLOCKED"
