"""
backend/tests/test_v17_8_acceptance_runner.py
Unit tests and negative controls for the V17.8 / V17.8.0.1 Acceptance Runner & Verdict Engine.
Verifies fail-closed behavior for all 52 mandatory gates (G01-G52).
"""

import pytest
from tools.acceptance_verdict import (
    COMMAND_GATES,
    INTEGRITY_GATES,
    MANDATORY_GATES_V17_8,
    MANDATORY_GATES_V17_8_0,
    MANDATORY_GATES_V17_8_0_1,
    TEST_GATES,
    determine_release_verdict,
)


def _make_passing_gate_results() -> dict:
    """Builds a completely valid, passing 52-gate results dictionary."""
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


def test_verdict_all_52_gates_pass():
    """All 52 gates pass with proper counts and exit codes -> ACCEPTED_FOR_PAPER_OPT_IN."""
    gates = _make_passing_gate_results()
    assert len(MANDATORY_GATES_V17_8_0) == 42
    assert len(MANDATORY_GATES_V17_8_0_1) == 52
    assert len(gates) == 52
    verdict = determine_release_verdict(gates, MANDATORY_GATES_V17_8)
    assert verdict == "ACCEPTED_FOR_PAPER_OPT_IN"


def test_verdict_missing_gate_blocked():
    """Missing any mandatory gate (including V17.8.0.1 G43-G52) fails closed with BLOCKED."""
    gates = _make_passing_gate_results()
    del gates["G29_UNICORN_BREAKER_DETECTION"]
    verdict = determine_release_verdict(gates, MANDATORY_GATES_V17_8)
    assert verdict == "BLOCKED"

    gates_v17_8_0_1 = _make_passing_gate_results()
    del gates_v17_8_0_1["G49_END_TO_END_RUNTIME_INTEGRATION"]
    assert determine_release_verdict(gates_v17_8_0_1, MANDATORY_GATES_V17_8) == "BLOCKED"


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
