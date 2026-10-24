"""
backend/tests/test_v17_5_5_entry_context_gate.py
AURUM V17.5.5 — Unit and Integration Suite for Entry Gate and Context Enforcement.
Covers Parts 011–040, Appendix A, E1, E3:
1. Negative probe: CONTEXT_INCOMPLETE blocks entry with wait_reason="CONTEXT_INCOMPLETE".
2. Negative probe: cost_snapshot=None / unavailable blocks entry with wait_reason="COST_UNAVAILABLE".
3. Negative probe: Invalid/negative/infinite cost values block entry with wait_reason="INVALID_COST".
4. Negative probe: Expired plans block entry with wait_reason="EXPIRED".
5. Ineligible variants (can_execute=False) blocked with their rejection_reason.
6. Positive twin: VERIFIED context + complete cost snapshot transitions to OPEN on trigger.
7. Explicit 0.0 slippage distinguished from missing snapshot.
8. Variants already in OPEN state never have hard exits or stop loss blocked by entry gates.
"""

import math
import pytest
from services.stop_placement_types import (
    ComparisonEntryGateResult,
    StopAnchor,
    StopAnchorKind,
    StopBufferResult,
)
from services.stop_placement_research_service import (
    build_stop_comparison_plan,
    advance_stop_comparison,
    evaluate_stop_comparison_entry_gate,
    read_verified_comparison_cost_snapshot,
)


@pytest.fixture
def base_anchor():
    return StopAnchor(
        id="anc_test_1m",
        kind=StopAnchorKind.PROTECTED_LEG_HIGH.value,
        price=2655.0,
        timeframe="1M",
        occurred_at_ms=1000,
        known_at_ms=1060,
        source_leg_id="leg_1",
    )


@pytest.fixture
def base_buffer():
    return StopBufferResult(
        buffer_price=1.0,
        components={"atr_m1": 0.8, "tick_floor": 0.05},
        dominant_source="atr_m1",
        tick_size=0.01,
    )


@pytest.fixture
def valid_policy_settings():
    return {
        "capital_usdt": 1000.0,
        "risk_pct": 0.1,
        "leverage": 50,
        "margin_mode": "ISOLATED",
        "slippage_usd": 0.10,
        "config_version": 1,
    }


def test_evaluate_stop_comparison_entry_gate_context_incomplete(base_anchor, base_buffer, valid_policy_settings):
    """Phần 022, Appendix A2: CONTEXT_INCOMPLETE blocks entry gate."""
    plan = build_stop_comparison_plan(
        opportunity_key="opp_probe_neg_context",
        model_id="M1_CONTINUATION",
        symbol="XAUUSDT",
        direction="SHORT",
        planned_entry=2650.0,
        take_profit=2620.0,
        legacy_sl=2660.0,
        anchor=base_anchor,
        buffer_result=base_buffer,
        policy_settings=valid_policy_settings,
        context_status="CONTEXT_INCOMPLETE",
        context_reason="MISSING_EXPIRY_OR_INTENT",
        cost_snapshot={"entry_slippage_usd": 0.10, "maker_fee_rate": 0.0002, "taker_fee_rate": 0.0004, "version": 1},
    )
    plan_dict = plan.to_dict()
    costs = read_verified_comparison_cost_snapshot(plan_dict)

    gate = evaluate_stop_comparison_entry_gate(
        plan=plan_dict,
        variant=plan_dict["variant_a"],
        quote={"bid": 2650.0, "ask": 2650.2, "exchange_ts_ms": 2000},
        as_of_ms=2000,
        cost_result=costs,
    )

    assert isinstance(gate, ComparisonEntryGateResult)
    assert gate.allowed is False
    assert gate.reason_code == "CONTEXT_INCOMPLETE"
    assert "expected VERIFIED" in gate.explanation


def test_evaluate_stop_comparison_entry_gate_missing_cost_snapshot(base_anchor, base_buffer, valid_policy_settings):
    """Phần 024, Appendix A2: Missing cost snapshot blocks entry gate."""
    plan = build_stop_comparison_plan(
        opportunity_key="opp_probe_neg_cost",
        model_id="M1_CONTINUATION",
        symbol="XAUUSDT",
        direction="SHORT",
        planned_entry=2650.0,
        take_profit=2620.0,
        legacy_sl=2660.0,
        anchor=base_anchor,
        buffer_result=base_buffer,
        policy_settings=valid_policy_settings,
        context_status="VERIFIED",
        cost_snapshot=None,
    )
    plan_dict = plan.to_dict()
    costs = read_verified_comparison_cost_snapshot(plan_dict)

    gate = evaluate_stop_comparison_entry_gate(
        plan=plan_dict,
        variant=plan_dict["variant_a"],
        quote={"bid": 2650.0, "ask": 2650.2, "exchange_ts_ms": 2000},
        as_of_ms=2000,
        cost_result=costs,
    )

    assert gate.allowed is False
    assert gate.reason_code == "COST_UNAVAILABLE"


def test_evaluate_stop_comparison_entry_gate_invalid_cost_values(base_anchor, base_buffer, valid_policy_settings):
    """Phần 042, 049: NaN or negative slippage blocks gate with INVALID_COST."""
    plan_dict = {
        "context_status": "VERIFIED",
        "expires_at_ms": 50000,
        "variant_a": {"is_valid": True, "can_execute": True, "meets_min_rr": True},
    }
    # Case NaN
    nan_costs = {"available": True, "entry_slippage_usd": float("nan"), "maker_fee_rate": 0.0002, "taker_fee_rate": 0.0004}
    gate_nan = evaluate_stop_comparison_entry_gate(
        plan=plan_dict,
        variant=plan_dict["variant_a"],
        quote={},
        as_of_ms=2000,
        cost_result=nan_costs,
    )
    assert gate_nan.allowed is False
    assert gate_nan.reason_code == "INVALID_COST"

    # Case Negative
    neg_costs = {"available": True, "entry_slippage_usd": -0.10, "maker_fee_rate": 0.0002, "taker_fee_rate": 0.0004}
    gate_neg = evaluate_stop_comparison_entry_gate(
        plan=plan_dict,
        variant=plan_dict["variant_a"],
        quote={},
        as_of_ms=2000,
        cost_result=neg_costs,
    )
    assert gate_neg.allowed is False
    assert gate_neg.reason_code == "INVALID_COST"


def test_evaluate_stop_comparison_entry_gate_expired_plan():
    """Phần 025: Expired plan blocks entry gate."""
    plan_dict = {
        "context_status": "VERIFIED",
        "expires_at_ms": 2000,
        "variant_a": {"is_valid": True, "can_execute": True, "meets_min_rr": True},
    }
    costs = {"available": True, "entry_slippage_usd": 0.10, "maker_fee_rate": 0.0002, "taker_fee_rate": 0.0004}
    gate = evaluate_stop_comparison_entry_gate(
        plan=plan_dict,
        variant=plan_dict["variant_a"],
        quote={},
        as_of_ms=2500,  # after expiry
        cost_result=costs,
    )
    assert gate.allowed is False
    assert gate.reason_code == "EXPIRED"


def test_evaluate_stop_comparison_entry_gate_ineligible_variant():
    """Phần 025: Ineligible variant blocks entry gate with rejection reason."""
    plan_dict = {
        "context_status": "VERIFIED",
        "expires_at_ms": 50000,
        "variant_b": {
            "is_valid": False,
            "can_execute": False,
            "meets_min_rr": False,
            "rejection_reason": "MISSING_STRUCTURAL_ANCHOR",
        },
    }
    costs = {"available": True, "entry_slippage_usd": 0.10, "maker_fee_rate": 0.0002, "taker_fee_rate": 0.0004}
    gate = evaluate_stop_comparison_entry_gate(
        plan=plan_dict,
        variant=plan_dict["variant_b"],
        quote={},
        as_of_ms=2000,
        cost_result=costs,
    )
    assert gate.allowed is False
    assert gate.reason_code == "MISSING_STRUCTURAL_ANCHOR"


def test_evaluate_stop_comparison_entry_gate_positive_twin():
    """Phần 029, Appendix A5: Positive twin with VERIFIED context & explicit 0.0 slip passes gate."""
    plan_dict = {
        "context_status": "VERIFIED",
        "expires_at_ms": 50000,
        "variant_a": {"is_valid": True, "can_execute": True, "meets_min_rr": True},
    }
    costs = {"available": True, "entry_slippage_usd": 0.0, "maker_fee_rate": 0.0002, "taker_fee_rate": 0.0004}
    gate = evaluate_stop_comparison_entry_gate(
        plan=plan_dict,
        variant=plan_dict["variant_a"],
        quote={},
        as_of_ms=2000,
        cost_result=costs,
    )
    assert gate.allowed is True
    assert gate.reason_code == "READY"


def test_advance_stop_comparison_blocks_context_incomplete(base_anchor, base_buffer, valid_policy_settings):
    """Phần 031, 039: advance_stop_comparison strictly blocks transition to OPEN when context is incomplete."""
    plan = build_stop_comparison_plan(
        opportunity_key="opp_advance_context_inc",
        model_id="M1_CONTINUATION",
        symbol="XAUUSDT",
        direction="SHORT",
        planned_entry=2650.0,
        take_profit=2620.0,
        legacy_sl=2660.0,
        anchor=base_anchor,
        buffer_result=base_buffer,
        policy_settings=valid_policy_settings,
        context_status="CONTEXT_INCOMPLETE",
        context_reason="MISSING_EXPIRY_OR_INTENT",
        cost_snapshot={"entry_slippage_usd": 0.10, "maker_fee_rate": 0.0002, "taker_fee_rate": 0.0004, "version": 1},
        decision_cutoff_ms=1000,
        expires_at_ms=100000,
    )
    quote = {
        "bid": 2650.0,
        "ask": 2650.2,
        "exchange_ts_ms": 2000,
    }

    advanced = advance_stop_comparison(plan.to_dict(), quote, as_of_ms=2000)

    # All variants must remain WAIT_ENTRY, never OPEN
    for v_key in ("variant_a", "variant_b", "variant_c"):
        v = advanced[v_key]
        assert v.get("execution_state") == "WAIT_ENTRY"
        assert v.get("actual_entry") is None
        assert v.get("opened_at_ms") is None
        if v.get("can_execute", False):
            assert v.get("wait_reason") == "CONTEXT_INCOMPLETE"


def test_advance_stop_comparison_blocks_missing_cost(base_anchor, base_buffer, valid_policy_settings):
    """Phần 032, 039: advance_stop_comparison strictly blocks transition to OPEN when cost_snapshot is None."""
    plan = build_stop_comparison_plan(
        opportunity_key="opp_advance_missing_cost",
        model_id="M1_CONTINUATION",
        symbol="XAUUSDT",
        direction="SHORT",
        planned_entry=2650.0,
        take_profit=2620.0,
        legacy_sl=2660.0,
        anchor=base_anchor,
        buffer_result=base_buffer,
        policy_settings=valid_policy_settings,
        context_status="VERIFIED",
        cost_snapshot=None,  # No cost snapshot!
        decision_cutoff_ms=1000,
        expires_at_ms=100000,
    )
    quote = {
        "bid": 2650.0,
        "ask": 2650.2,
        "exchange_ts_ms": 2000,
    }

    advanced = advance_stop_comparison(plan.to_dict(), quote, as_of_ms=2000)

    for v_key in ("variant_a", "variant_b", "variant_c"):
        v = advanced[v_key]
        assert v.get("execution_state") == "WAIT_ENTRY"
        assert v.get("actual_entry") is None
        assert v.get("opened_at_ms") is None
        if v.get("can_execute", False):
            assert v.get("wait_reason") == "COST_UNAVAILABLE"


def test_advance_stop_comparison_positive_twin_opens_position(base_anchor, base_buffer, valid_policy_settings):
    """Phần 040: Positive twin with VERIFIED context and cost transitions to OPEN and records actual fill."""
    plan = build_stop_comparison_plan(
        opportunity_key="opp_advance_positive",
        model_id="M1_CONTINUATION",
        symbol="XAUUSDT",
        direction="SHORT",
        planned_entry=2650.0,
        take_profit=2620.0,
        legacy_sl=2660.0,
        anchor=base_anchor,
        buffer_result=base_buffer,
        policy_settings=valid_policy_settings,
        context_status="VERIFIED",
        cost_snapshot={"entry_slippage_usd": 0.10, "maker_fee_rate": 0.0002, "taker_fee_rate": 0.0004, "version": 1},
        decision_cutoff_ms=1000,
        expires_at_ms=100000,
    )
    quote = {
        "bid": 2650.0,
        "ask": 2650.2,
        "exchange_ts_ms": 2000,
    }

    advanced = advance_stop_comparison(plan.to_dict(), quote, as_of_ms=2000)

    # Variant A (executable) transitions to OPEN
    va = advanced["variant_a"]
    assert va.get("execution_state") == "OPEN"
    assert va.get("opened_at_ms") == 2000
    # MARKET SHORT fill = bid - slippage = 2650.0 - 0.10 = 2649.90
    assert abs(va.get("actual_entry") - 2649.90) < 1e-4
    assert va.get("wait_reason") is None


def test_advance_stop_comparison_open_variant_not_blocked_on_exit(base_anchor, base_buffer, valid_policy_settings):
    """Phần 027: Variants already in OPEN state execute TP/SL exits normally; gate does not block exit."""
    plan = build_stop_comparison_plan(
        opportunity_key="opp_advance_exit_test",
        model_id="M1_CONTINUATION",
        symbol="XAUUSDT",
        direction="SHORT",
        planned_entry=2650.0,
        take_profit=2620.0,
        legacy_sl=2660.0,
        anchor=base_anchor,
        buffer_result=base_buffer,
        policy_settings=valid_policy_settings,
        context_status="CONTEXT_INCOMPLETE",  # Notice incomplete context at resume
        cost_snapshot={"entry_slippage_usd": 0.10, "maker_fee_rate": 0.0002, "taker_fee_rate": 0.0004, "version": 1},
        decision_cutoff_ms=1000,
        expires_at_ms=100000,
    )
    plan_dict = plan.to_dict()
    # Force variant A into OPEN state as if previously filled
    plan_dict["variant_a"]["execution_state"] = "OPEN"
    plan_dict["variant_a"]["actual_entry"] = 2650.0
    plan_dict["variant_a"]["opened_at_ms"] = 1500

    # Market hits Stop Loss at 2662.0 (above SL 2660.0)
    sl_quote = {
        "bid": 2662.0,
        "ask": 2662.2,
        "exchange_ts_ms": 3000,
    }
    advanced = advance_stop_comparison(plan_dict, sl_quote, as_of_ms=3000)

    va = advanced["variant_a"]
    assert va.get("execution_state") == "CLOSED"
    assert va.get("exit_cause") in ("SL_HIT", "STOP_LOSS")
    assert va.get("closed_at_ms") == 3000
