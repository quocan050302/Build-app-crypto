"""
Tests for Entry Intent Closure (REPRO05, PHẦN 165, 166)
Validates that unrecognized execution modes fail-closed and setup dict direction is preserved.
"""
import pytest
from unittest.mock import MagicMock
from services.entry_execution_intent_service import (
    resolve_entry_execution_intent,
    EntryIntentDecision,
)


def test_repro05_unknown_mode_blocks():
    """REPRO05 (PHẦN 165):
    Unrecognized or unsupported execution_mode must fail-closed with allowed=False
    and entry_mode='ENTRY_MODE_UNAVAILABLE'.
    """
    setup = {
        "direction": "LONG",
        "entry": 4180.0,
        "stop_loss": 4170.0,
        "take_profit": 4200.0,
        "strategy_family": "STANDARD_SMC",
    }
    descriptor = {"execution_mode": "UNRECOGNIZED_MODEL"}
    decision = resolve_entry_execution_intent(setup, model_descriptor=descriptor)

    assert not decision.allowed, (
        f"Expected allowed=False for unrecognized execution_mode, got allowed={decision.allowed}"
    )
    assert decision.entry_mode == "ENTRY_MODE_UNAVAILABLE"


def test_int01_dict_short_preserves_short_direction():
    """INT01 (PHẦN 165, 166):
    When setup is passed as a dict with direction='SHORT',
    it must NOT fallback to 'LONG'.
    For SHORT entry=4180, bid=4179.0, ask=4179.2:
    - bid < 4180 -> limit is not yet marketable (bid has not reached entry).
    - If wrongly treated as LONG: ask (4179.2) <= entry (4180.0) -> wrongly blocked as ENTRY_ZONE_ALREADY_REACHED!
    """
    setup = {
        "direction": "SHORT",
        "entry": 4180.0,
        "stop_loss": 4185.0,
        "take_profit": 4160.0,
        "strategy_family": "STANDARD_SMC",
    }
    quote = MagicMock(bid=4179.0, ask=4179.2)
    descriptor = {"execution_mode": "RETEST_LIMIT"}

    decision = resolve_entry_execution_intent(setup, model_descriptor=descriptor, quote=quote)

    assert decision.allowed, (
        f"Expected allowed=True for valid non-marketable SHORT limit, but got reason: {decision.reason_code}"
    )
    assert decision.order_type == "LIMIT"
    assert decision.entry_mode == "RETEST_LIMIT"
