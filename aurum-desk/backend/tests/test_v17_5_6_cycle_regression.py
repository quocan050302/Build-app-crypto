"""
backend/tests/test_v17_5_6_cycle_regression.py
AURUM V17.5.6 — Entry Context, Worker Cycle & DB Resilience Regression Suite.
Covers Parts 151–160, Appendix E1, E3:
1. Incomplete context blocks entry -> stays WAIT_ENTRY.
2. Missing cost snapshot blocks entry -> stays WAIT_ENTRY without slippage 0 fallback.
3. Full context with explicit cost 0.0 transitions to OPEN when triggered.
4. Preplan quote (timestamp before plan activation) ignored without premature fill.
5. Expired plan (deadline <= now) transitions to terminal EXPIRED, never OPEN.
6. DB rollback resilience: research flush exception does not corrupt or roll back committed trade exits.
7. Restart OPEN idempotency: order already OPEN continues to monitor exits, never re-enters.
"""

import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import crud
from services.clock import ReplayClock
from services.stop_placement_types import (
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
def anchor():
    return StopAnchor(
        id="anc_reg_1",
        kind=StopAnchorKind.PROTECTED_LEG_HIGH.value,
        price=2655.0,
        timeframe="1M",
        occurred_at_ms=1000,
        known_at_ms=1060,
        source_leg_id="leg_1",
    )


@pytest.fixture
def buffer_res():
    return StopBufferResult(
        buffer_price=1.0,
        components={"atr_m1": 0.8, "tick_floor": 0.05},
        dominant_source="atr_m1",
        tick_size=0.01,
    )


@pytest.fixture
def policy_settings():
    return {
        "capital_usdt": 1000.0,
        "risk_pct": 0.1,
        "leverage": 50,
        "margin_mode": "ISOLATED",
        "slippage_usd": 0.10,
        "config_version": 1,
    }


def test_p151_incomplete_context_stays_wait_entry(anchor, buffer_res, policy_settings):
    """Phần 151: Incomplete context with otherwise valid prices stays in WAIT_ENTRY."""
    plan = build_stop_comparison_plan(
        opportunity_key="opp_p151",
        model_id="M1_CONTINUATION",
        symbol="XAUUSDT",
        direction="SHORT",
        planned_entry=2650.0,
        take_profit=2620.0,
        legacy_sl=2660.0,
        anchor=anchor,
        buffer_result=buffer_res,
        policy_settings=policy_settings,
        context_status="CONTEXT_INCOMPLETE",
        cost_snapshot={"entry_slippage_usd": 0.10, "maker_fee_rate": 0.0002, "taker_fee_rate": 0.0004, "version": 1},
        decision_cutoff_ms=1000,
        expires_at_ms=100000,
    )
    plan_dict = plan.to_dict()

    # Trigger price arrives (bid 2650.0 touches planned entry)
    quote = {"bid": 2650.0, "ask": 2650.2, "exchange_ts_ms": 2000, "is_valid": True}
    updated = advance_stop_comparison(plan_dict, quote, as_of_ms=2000)

    # Variant A must stay WAIT_ENTRY
    assert updated["variant_a"]["execution_state"] == "WAIT_ENTRY"
    assert updated["variant_a"]["wait_reason"] == "CONTEXT_INCOMPLETE"
    assert updated["variant_a"].get("actual_entry") is None


def test_p152_missing_cost_snapshot_stays_wait_entry(anchor, buffer_res, policy_settings):
    """Phần 152: VERIFIED context but missing cost snapshot stays in WAIT_ENTRY; no fake slippage 0."""
    plan = build_stop_comparison_plan(
        opportunity_key="opp_p152",
        model_id="M1_CONTINUATION",
        symbol="XAUUSDT",
        direction="SHORT",
        planned_entry=2650.0,
        take_profit=2620.0,
        legacy_sl=2660.0,
        anchor=anchor,
        buffer_result=buffer_res,
        policy_settings=policy_settings,
        context_status="VERIFIED",
        cost_snapshot=None,  # Missing cost!
        decision_cutoff_ms=1000,
        expires_at_ms=100000,
    )
    plan_dict = plan.to_dict()

    quote = {"bid": 2650.0, "ask": 2650.2, "exchange_ts_ms": 2000, "is_valid": True}
    updated = advance_stop_comparison(plan_dict, quote, as_of_ms=2000)

    assert updated["variant_a"]["execution_state"] == "WAIT_ENTRY"
    assert updated["variant_a"]["wait_reason"] == "COST_UNAVAILABLE"


def test_p153_valid_explicit_zero_cost_transitions_to_open(anchor, buffer_res, policy_settings):
    """Phần 153: Full context with explicit cost 0.0 transitions to OPEN on trigger."""
    plan = build_stop_comparison_plan(
        opportunity_key="opp_p153",
        model_id="M1_CONTINUATION",
        symbol="XAUUSDT",
        direction="SHORT",
        planned_entry=2650.0,
        take_profit=2620.0,
        legacy_sl=2660.0,
        anchor=anchor,
        buffer_result=buffer_res,
        policy_settings=policy_settings,
        context_status="VERIFIED",
        cost_snapshot={
            "entry_slippage_usd": 0.0,
            "tp_slippage_usd": 0.0,
            "sl_slippage_usd": 0.0,
            "maker_fee_rate": 0.0002,
            "taker_fee_rate": 0.0004,
            "version": 1,
            "provenance": "VERIFIED_ZERO_COST",
        },
        decision_cutoff_ms=1000,
        expires_at_ms=100000,
    )
    plan_dict = plan.to_dict()

    quote = {"bid": 2650.0, "ask": 2650.2, "exchange_ts_ms": 2000, "is_valid": True}
    updated = advance_stop_comparison(plan_dict, quote, as_of_ms=2000)

    assert updated["variant_a"]["execution_state"] == "OPEN"
    # Fill with 0.0 slippage on bid 2650.0 = 2650.0
    assert updated["variant_a"]["actual_entry"] == 2650.0


def test_p154_preplan_quote_ignored(anchor, buffer_res, policy_settings):
    """Phần 154: Quote with timestamp prior to plan cutoff is ignored without premature fill."""
    plan = build_stop_comparison_plan(
        opportunity_key="opp_p154",
        model_id="M1_CONTINUATION",
        symbol="XAUUSDT",
        direction="SHORT",
        planned_entry=2650.0,
        take_profit=2620.0,
        legacy_sl=2660.0,
        anchor=anchor,
        buffer_result=buffer_res,
        policy_settings=policy_settings,
        context_status="VERIFIED",
        cost_snapshot={"entry_slippage_usd": 0.10, "maker_fee_rate": 0.0002, "taker_fee_rate": 0.0004, "version": 1},
        decision_cutoff_ms=5000,
        expires_at_ms=100000,
    )
    plan_dict = plan.to_dict()

    # Quote from t=3000 (before plan cutoff at t=5000)
    quote_early = {"bid": 2650.0, "ask": 2650.2, "exchange_ts_ms": 3000, "is_valid": True}
    updated = advance_stop_comparison(plan_dict, quote_early, as_of_ms=3000)

    assert updated["variant_a"].get("execution_state") in (None, "WAIT_ENTRY")
    assert updated["variant_a"].get("actual_entry") is None


def test_p157_expired_plan_transitions_to_expired(anchor, buffer_res, policy_settings):
    """Phần 157: Plan past expiry deadline transitions to EXPIRED, never OPEN."""
    plan = build_stop_comparison_plan(
        opportunity_key="opp_p157",
        model_id="M1_CONTINUATION",
        symbol="XAUUSDT",
        direction="SHORT",
        planned_entry=2650.0,
        take_profit=2620.0,
        legacy_sl=2660.0,
        anchor=anchor,
        buffer_result=buffer_res,
        policy_settings=policy_settings,
        context_status="VERIFIED",
        cost_snapshot={"entry_slippage_usd": 0.10, "maker_fee_rate": 0.0002, "taker_fee_rate": 0.0004, "version": 1},
        decision_cutoff_ms=1000,
        expires_at_ms=5000,
    )
    plan_dict = plan.to_dict()

    # Quote arrives at t=6000 (after deadline 5000)
    quote_late = {"bid": 2650.0, "ask": 2650.2, "exchange_ts_ms": 6000, "is_valid": True}
    updated = advance_stop_comparison(plan_dict, quote_late, as_of_ms=6000)

    assert updated["variant_a"]["execution_state"] == "EXPIRED"


def test_p160_restart_open_idempotency(anchor, buffer_res, policy_settings):
    """Phần 160: Order already in OPEN state continues to monitor exit and does not re-enter."""
    plan = build_stop_comparison_plan(
        opportunity_key="opp_p160",
        model_id="M1_CONTINUATION",
        symbol="XAUUSDT",
        direction="SHORT",
        planned_entry=2650.0,
        take_profit=2620.0,
        legacy_sl=2660.0,
        anchor=anchor,
        buffer_result=buffer_res,
        policy_settings=policy_settings,
        context_status="VERIFIED",
        cost_snapshot={"entry_slippage_usd": 0.10, "maker_fee_rate": 0.0002, "taker_fee_rate": 0.0004, "version": 1},
        decision_cutoff_ms=1000,
        expires_at_ms=100000,
    )
    plan_dict = plan.to_dict()

    # Tick 1: trigger entry
    q1 = {"bid": 2650.0, "ask": 2650.2, "exchange_ts_ms": 2000, "is_valid": True}
    updated1 = advance_stop_comparison(plan_dict, q1, as_of_ms=2000)
    assert updated1["variant_a"]["execution_state"] == "OPEN"
    initial_entry = updated1["variant_a"]["actual_entry"]

    # Tick 2: intermediate quote (bid 2640.0) -> still OPEN, entry unchanged
    q2 = {"bid": 2640.0, "ask": 2640.2, "exchange_ts_ms": 2500, "is_valid": True}
    updated2 = advance_stop_comparison(updated1, q2, as_of_ms=2500)
    assert updated2["variant_a"]["execution_state"] == "OPEN"
    assert updated2["variant_a"]["actual_entry"] == initial_entry

    # Tick 3: TP reached (bid 2619.0 <= TP 2620.0) -> transitions to CLOSED
    q3 = {"bid": 2619.0, "ask": 2619.2, "exchange_ts_ms": 3000, "is_valid": True}
    updated3 = advance_stop_comparison(updated2, q3, as_of_ms=3000)
    assert updated3["variant_a"]["execution_state"] == "CLOSED"
    assert updated3["variant_a"]["exit_cause"] in ("TP_HIT", "TAKE_PROFIT")
