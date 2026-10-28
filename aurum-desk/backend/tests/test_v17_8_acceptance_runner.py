"""
backend/tests/test_v17_8_acceptance_runner.py
Unit tests and negative controls for the V17.8 / V17.8.0.1 / V17.8.0.2 Acceptance Runner & Verdict Engine.
Verifies fail-closed behavior for all 52 core gates (G01-G52) and all 58 V17.8.0.2 gates (G01-G58).
"""

import pytest
from tools.acceptance_verdict import (
    COMMAND_GATES,
    INTEGRITY_GATES,
    MANDATORY_GATES_V17_8,
    MANDATORY_GATES_V17_8_0,
    MANDATORY_GATES_V17_8_0_1,
    MANDATORY_GATES_V17_8_0_2,
    MANDATORY_GATES_V17_8_0_3,
    TEST_GATES,
    determine_release_verdict,
    determine_v17_8_0_2_release_verdict,
    determine_v17_8_0_3_release_verdict,
)


def _make_passing_gate_results(manifest=None) -> dict:
    """Builds a completely valid, passing gate results dictionary for the target manifest."""
    target_manifest = manifest if manifest is not None else MANDATORY_GATES_V17_8
    results = {}
    for gate_id in target_manifest:
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


def test_verdict_all_52_and_58_gates_pass():
    """All 52 (V17.8.0.1), 58 (V17.8.0.2), and 75 (V17.8.0.3) gates pass."""
    assert len(MANDATORY_GATES_V17_8_0) == 42
    assert len(MANDATORY_GATES_V17_8_0_1) == 52
    assert len(MANDATORY_GATES_V17_8_0_2) == 58
    assert len(MANDATORY_GATES_V17_8_0_3) == 75
    assert len(MANDATORY_GATES_V17_8) == 75

    gates_52 = _make_passing_gate_results(MANDATORY_GATES_V17_8_0_1)
    assert len(gates_52) == 52
    assert determine_release_verdict(gates_52, MANDATORY_GATES_V17_8_0_1) == "ACCEPTED_FOR_PAPER_OPT_IN"

    gates_58 = _make_passing_gate_results(MANDATORY_GATES_V17_8_0_2)
    assert len(gates_58) == 58
    assert determine_release_verdict(gates_58, MANDATORY_GATES_V17_8_0_2) == "ACCEPTED_FOR_PAPER_OPT_IN"

    gates_75 = _make_passing_gate_results(MANDATORY_GATES_V17_8_0_3)
    assert len(gates_75) == 75
    assert determine_release_verdict(gates_75, MANDATORY_GATES_V17_8) == "ACCEPTED_FOR_PAPER_OPT_IN"

    valid_preflight = {
        "status": "PASS",
        "db_sha256_before": "abc12345",
        "db_sha256_after": "abc12345",
        "counters": {
            "PAPER_ORDERS_CREATED": 0,
            "PAPER_ORDERS_FILLED": 0,
            "PAPER_POSITIONS_CLOSED": 0,
            "LIVE_EXCHANGE_ORDERS_SENT": 0,
            "UNAUTHORIZED_DB_WRITES": 0,
            "BACKGROUND_TRADING_WORKERS_STARTED": 0,
            "UNEXPECTED_RUNTIME_ERRORS": 0,
        },
    }
    assert (
        determine_v17_8_0_2_release_verdict(gates_58, MANDATORY_GATES_V17_8_0_2, valid_preflight)
        == "V17_8_0_2_ACCEPTED_FOR_SAFE_OBSERVATION"
    )

    valid_shadow_preflight = {
        "status": "PASS",
        "runtime_profile": "MARKET_SHADOW",
        "operational_db_sha256_before": "op12345",
        "operational_db_sha256_after": "op12345",
        "operational_db_immutable": True,
        "counters": {
            "PAPER_ORDERS_CREATED": 0,
            "PAPER_ORDERS_FILLED": 0,
            "PAPER_POSITIONS_CLOSED": 0,
            "LIVE_EXCHANGE_ORDERS_SENT": 0,
            "UNAUTHORIZED_DB_WRITES": 0,
            "BACKGROUND_TRADING_WORKERS_STARTED": 0,
            "UNEXPECTED_RUNTIME_ERRORS": 0,
        },
    }
    valid_backtest = {
        "status": "PASS",
        "fabricated_candles_count": 0,
    }
    assert (
        determine_v17_8_0_3_release_verdict(
            gates_75,
            MANDATORY_GATES_V17_8_0_3,
            valid_preflight,
            valid_shadow_preflight,
            valid_backtest,
        )
        == "V17_8_0_3_ACCEPTED_FOR_SAFE_MARKET_SHADOW_AND_BACKTEST"
    )
    bad_backtest = {"status": "PASS", "fabricated_candles_count": 1}
    assert (
        determine_v17_8_0_3_release_verdict(
            gates_75,
            MANDATORY_GATES_V17_8_0_3,
            valid_preflight,
            valid_shadow_preflight,
            bad_backtest,
        )
        == "V17_8_0_3_BLOCKED"
    )


def test_verdict_missing_gate_blocked():
    """Missing any mandatory gate (including V17.8.0.1 G43-G52 and V17.8.0.2 G53-G58) fails closed with BLOCKED."""
    gates = _make_passing_gate_results()
    del gates["G29_UNICORN_BREAKER_DETECTION"]
    verdict = determine_release_verdict(gates, MANDATORY_GATES_V17_8)
    assert verdict == "BLOCKED"

    gates_v17_8_0_1 = _make_passing_gate_results()
    del gates_v17_8_0_1["G49_END_TO_END_RUNTIME_INTEGRATION"]
    assert determine_release_verdict(gates_v17_8_0_1, MANDATORY_GATES_V17_8) == "BLOCKED"

    gates_v17_8_0_2 = _make_passing_gate_results()
    del gates_v17_8_0_2["G53_OBSERVE_ONLY_STARTUP_ISOLATION"]
    assert determine_release_verdict(gates_v17_8_0_2, MANDATORY_GATES_V17_8) == "BLOCKED"
    assert determine_v17_8_0_2_release_verdict(gates_v17_8_0_2, MANDATORY_GATES_V17_8_0_2) == "V17_8_0_2_BLOCKED"


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

    gates_g58 = _make_passing_gate_results()
    gates_g58["G58_RELEASE_ARTIFACT_TRUTH_INTEGRITY"]["integrity_verified"] = False
    assert determine_release_verdict(gates_g58, MANDATORY_GATES_V17_8) == "BLOCKED"


def test_verdict_v17_8_0_2_preflight_violation_blocked():
    """Non-zero preflight counters or DB hash mismatch fail closed in V17.8.0.2 verdict resolution."""
    gates_58 = _make_passing_gate_results(MANDATORY_GATES_V17_8_0_2)
    bad_preflight_counter = {
        "status": "PASS",
        "db_sha256_before": "abc",
        "db_sha256_after": "abc",
        "counters": {
            "PAPER_ORDERS_CREATED": 1,
            "PAPER_ORDERS_FILLED": 0,
            "PAPER_POSITIONS_CLOSED": 0,
            "LIVE_EXCHANGE_ORDERS_SENT": 0,
            "UNAUTHORIZED_DB_WRITES": 0,
            "BACKGROUND_TRADING_WORKERS_STARTED": 0,
            "UNEXPECTED_RUNTIME_ERRORS": 0,
        },
    }
    assert (
        determine_v17_8_0_2_release_verdict(gates_58, MANDATORY_GATES_V17_8_0_2, bad_preflight_counter)
        == "V17_8_0_2_BLOCKED"
    )

    bad_preflight_hash = {
        "status": "PASS",
        "db_sha256_before": "abc",
        "db_sha256_after": "def",
        "counters": {
            "PAPER_ORDERS_CREATED": 0,
            "PAPER_ORDERS_FILLED": 0,
            "PAPER_POSITIONS_CLOSED": 0,
            "LIVE_EXCHANGE_ORDERS_SENT": 0,
            "UNAUTHORIZED_DB_WRITES": 0,
            "BACKGROUND_TRADING_WORKERS_STARTED": 0,
            "UNEXPECTED_RUNTIME_ERRORS": 0,
        },
    }
    assert (
        determine_v17_8_0_2_release_verdict(gates_58, MANDATORY_GATES_V17_8_0_2, bad_preflight_hash)
        == "V17_8_0_2_BLOCKED"
    )

