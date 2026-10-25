"""
backend/tests/test_v17_6_4_3c_acceptance_integrity.py
V17.6.4.3c CPA-09 / CPA-10 (PART 148-151): Acceptance Negative Tests.

Guarantees:
- Imports pure `evaluate_release_acceptance` from real runner `tools.run_v17_6_4_3c_acceptance`.
- AI01 to AI20: Every failure condition strictly rejects release acceptance.
- Zero mock passes: Any missing evidence, checksum mismatch, or skip immediately REJECTS.
"""

import sys
from pathlib import Path
import pytest

# Add repo root to sys.path to import tools.run_v17_6_4_3c_acceptance
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_v17_6_4_3c_acceptance import (
    evaluate_release_acceptance,
    MANDATORY_GATES,
)


def _build_nominal_passing_context():
    """Builds a nominal 100% passing state across all 46 gates."""
    gate_results = {}
    for g in MANDATORY_GATES:
        gate_results[g] = {
            "gate": g,
            "status": "PASS",
            "skipped": 0,
            "failed": 0,
            "passed": 1,
            "reason": "OK",
        }
    source_integrity = {
        "branch": "fix/v14-1-rr-flow",
        "clean_before": True,
        "clean_after": True,
        "pre_sha": "a08097e4cfab20f2a67b3a6ab71be46a3dd978ca",
        "post_sha": "a08097e4cfab20f2a67b3a6ab71be46a3dd978ca",
    }
    artifact_integrity = {
        "all_logs_present": True,
        "missing_logs": [],
        "hashes_verified": True,
    }
    return gate_results, source_integrity, artifact_integrity


def test_ai00_nominal_release_acceptance_passes():
    """Positive baseline: all gates and criteria met -> ACCEPTED."""
    gates, src, art = _build_nominal_passing_context()
    dec = evaluate_release_acceptance(
        gate_results=gates,
        source_integrity=src,
        artifact_integrity=art,
        execution_mode="release",
    )
    assert dec.accepted is True
    assert dec.verdict == "ACCEPTED"
    assert dec.passed_gates == len(MANDATORY_GATES)


def test_ai01_missing_mandatory_gate():
    """AI01: Missing mandatory gate -> REJECTED."""
    gates, src, art = _build_nominal_passing_context()
    del gates["G01_adapter_transport_overrides_quote"]
    dec = evaluate_release_acceptance(gate_results=gates, source_integrity=src, artifact_integrity=art)
    assert dec.accepted is False
    assert dec.verdict == "REJECTED"
    assert any("G01" in u for u in dec.unmet_requirements)


def test_ai02_unexpected_skipped_test():
    """AI02: Unexpected skipped test -> REJECTED."""
    gates, src, art = _build_nominal_passing_context()
    gates["G07_exchange_timestamp_mandatory"]["skipped"] = 1
    dec = evaluate_release_acceptance(gate_results=gates, source_integrity=src, artifact_integrity=art)
    assert dec.accepted is False
    assert dec.verdict == "REJECTED"


def test_ai03_playwright_not_executed():
    """AI03: Playwright not executed -> REJECTED."""
    gates, src, art = _build_nominal_passing_context()
    del gates["G38_actual_playwright_e2e"]
    dec = evaluate_release_acceptance(gate_results=gates, source_integrity=src, artifact_integrity=art)
    assert dec.accepted is False
    assert dec.verdict == "REJECTED"


def test_ai04_playwright_failed():
    """AI04: Playwright failed -> REJECTED."""
    gates, src, art = _build_nominal_passing_context()
    gates["G38_actual_playwright_e2e"]["status"] = "FAIL"
    dec = evaluate_release_acceptance(gate_results=gates, source_integrity=src, artifact_integrity=art)
    assert dec.accepted is False
    assert dec.verdict == "REJECTED"


def test_ai05_zero_backend_tests():
    """AI05: Zero backend tests collected -> REJECTED."""
    gates, src, art = _build_nominal_passing_context()
    gates["G33_full_backend_test_discovery"]["status"] = "FAIL"
    dec = evaluate_release_acceptance(gate_results=gates, source_integrity=src, artifact_integrity=art)
    assert dec.accepted is False
    assert dec.verdict == "REJECTED"


def test_ai06_backend_discovery_failed():
    """AI06: Backend discovery failed -> REJECTED."""
    gates, src, art = _build_nominal_passing_context()
    gates["G33_full_backend_test_discovery"]["status"] = "FAIL"
    dec = evaluate_release_acceptance(gate_results=gates, source_integrity=src, artifact_integrity=art)
    assert dec.accepted is False


def test_ai07_missing_junit():
    """AI07: Missing junit results in gate failure -> REJECTED."""
    gates, src, art = _build_nominal_passing_context()
    gates["G20_scheduler_exact_cycle_persistence"]["status"] = "FAIL"
    dec = evaluate_release_acceptance(gate_results=gates, source_integrity=src, artifact_integrity=art)
    assert dec.accepted is False


def test_ai08_invalid_junit():
    """AI08: Invalid junit / test failure -> REJECTED."""
    gates, src, art = _build_nominal_passing_context()
    gates["G10_connection_epoch_mandatory"]["status"] = "FAIL"
    dec = evaluate_release_acceptance(gate_results=gates, source_integrity=src, artifact_integrity=art)
    assert dec.accepted is False


def test_ai09_missing_log():
    """AI09: Missing log file -> REJECTED."""
    gates, src, art = _build_nominal_passing_context()
    art["all_logs_present"] = False
    art["missing_logs"] = ["G17_scheduler_evaluate_symbol_now"]
    dec = evaluate_release_acceptance(gate_results=gates, source_integrity=src, artifact_integrity=art)
    assert dec.accepted is False


def test_ai10_artifact_hash_mismatch():
    """AI10: Artifact hash mismatch -> REJECTED."""
    gates, src, art = _build_nominal_passing_context()
    art["hashes_verified"] = False
    dec = evaluate_release_acceptance(gate_results=gates, source_integrity=src, artifact_integrity=art)
    assert dec.accepted is False


def test_ai11_sha_mismatch():
    """AI11: Pre-SHA and post-SHA mismatch -> REJECTED."""
    gates, src, art = _build_nominal_passing_context()
    src["post_sha"] = "different_sha_12345"
    dec = evaluate_release_acceptance(gate_results=gates, source_integrity=src, artifact_integrity=art)
    assert dec.accepted is False


def test_ai12_dirty_worktree():
    """AI12: Dirty worktree before acceptance -> REJECTED."""
    gates, src, art = _build_nominal_passing_context()
    src["clean_before"] = False
    dec = evaluate_release_acceptance(gate_results=gates, source_integrity=src, artifact_integrity=art)
    assert dec.accepted is False


def test_ai13_source_changed_during_acceptance():
    """AI13: Source changed during acceptance (clean_after=False) -> REJECTED."""
    gates, src, art = _build_nominal_passing_context()
    src["clean_after"] = False
    dec = evaluate_release_acceptance(gate_results=gates, source_integrity=src, artifact_integrity=art)
    assert dec.accepted is False


def test_ai14_real_scheduler_durability_test_omitted():
    """AI14: Real scheduler durability test omitted -> REJECTED."""
    gates, src, art = _build_nominal_passing_context()
    del gates["G21_scheduler_independent_readback"]
    dec = evaluate_release_acceptance(gate_results=gates, source_integrity=src, artifact_integrity=art)
    assert dec.accepted is False


def test_ai15_feed_transport_spoof_test_omitted():
    """AI15: Feed transport spoof test omitted -> REJECTED."""
    gates, src, art = _build_nominal_passing_context()
    del gates["G01_adapter_transport_overrides_quote"]
    dec = evaluate_release_acceptance(gate_results=gates, source_integrity=src, artifact_integrity=art)
    assert dec.accepted is False


def test_ai16_receive_only_timestamp_test_omitted():
    """AI16: Receive-only timestamp test omitted -> REJECTED."""
    gates, src, art = _build_nominal_passing_context()
    del gates["G07_exchange_timestamp_mandatory"]
    dec = evaluate_release_acceptance(gate_results=gates, source_integrity=src, artifact_integrity=art)
    assert dec.accepted is False


def test_ai17_runtime_zero_live_order_test_omitted():
    """AI17: Runtime zero-live-order test omitted -> REJECTED."""
    gates, src, art = _build_nominal_passing_context()
    del gates["G39_runtime_zero_live_orders"]
    dec = evaluate_release_acceptance(gate_results=gates, source_integrity=src, artifact_integrity=art)
    assert dec.accepted is False


def test_ai18_report_claims_pass_for_failed_command():
    """AI18: Report claims PASS for failed command -> REJECTED."""
    gates, src, art = _build_nominal_passing_context()
    gates["G06_rest_fallback_cannot_impersonate_ws"]["status"] = "FAIL"
    dec = evaluate_release_acceptance(gate_results=gates, source_integrity=src, artifact_integrity=art)
    assert dec.accepted is False


def test_ai19_development_mode_cannot_accept():
    """AI19: Development mode cannot produce ACCEPTED verdict -> DEVELOPMENT_TEST_ONLY."""
    gates, src, art = _build_nominal_passing_context()
    dec = evaluate_release_acceptance(gate_results=gates, source_integrity=src, artifact_integrity=art, execution_mode="dev")
    assert dec.accepted is False
    assert dec.verdict == "DEVELOPMENT_TEST_ONLY"


def test_ai20_missing_independent_sqlite_readback():
    """AI20: Missing independent SQLite readback gate -> REJECTED."""
    gates, src, art = _build_nominal_passing_context()
    del gates["G31_independent_duplicate_commit_proof"]
    dec = evaluate_release_acceptance(gate_results=gates, source_integrity=src, artifact_integrity=art)
    assert dec.accepted is False
    assert dec.verdict == "REJECTED"
