"""
backend/tests/test_v17_5_4_cost_parity.py
AURUM V17.5.4 — Cost & Slippage Parity Acceptance Tests (Appendix B & Parts 071-100).
Verifies:
1. Frozen cost snapshot reading: distinguishes explicit 0.0 slippage from missing snapshot.
2. Appendix B3 Golden Values for MARKET, LIMIT, STOP entry triggers & directional slippage.
3. Excursion parity: WAIT_ENTRY ticks do not pollute trade MAE/MFE; OPEN variants reference actual_entry.
4. Exit evaluation parity: full cost snapshot passed to shared exit evaluator.
"""

import json
from services.stop_placement_research_service import (
    read_verified_comparison_cost_snapshot,
    evaluate_comparison_entry,
    advance_stop_comparison,
    build_stop_comparison_plan,
)


def test_read_verified_comparison_cost_snapshot():
    """B1: Distinguish explicit 0.0 slippage from missing snapshot."""
    # 1. Explicit 0.0 slippage
    plan_zero = {
        "cost_snapshot": {
            "entry_slippage_usd": 0.0,
            "tp_slippage_usd": 0.0,
            "sl_slippage_usd": 0.0,
            "maker_fee_rate": 0.0002,
            "taker_fee_rate": 0.0006,
        }
    }
    res_zero = read_verified_comparison_cost_snapshot(plan_zero)
    assert res_zero["available"] is True
    assert res_zero["entry_slippage_usd"] == 0.0

    # 2. Configured positive slippage
    plan_pos = {
        "cost_snapshot": {
            "entry_slippage_usd": 0.12,
            "tp_slippage_usd": 0.0,
            "sl_slippage_usd": 0.12,
        }
    }
    res_pos = read_verified_comparison_cost_snapshot(plan_pos)
    assert res_pos["available"] is True
    assert res_pos["entry_slippage_usd"] == 0.12

    # 3. Missing snapshot entirely
    plan_missing = {"cost_snapshot": None}
    res_missing = read_verified_comparison_cost_snapshot(plan_missing)
    assert res_missing["available"] is False
    assert res_missing["reason"] == "MISSING_COST_SNAPSHOT"


def test_evaluate_comparison_entry_golden_values():
    """B3 Golden values: Bid=2000.00, Ask=2000.20, entry_slippage=0.10."""
    bid = 2000.00
    ask = 2000.20
    slip = 0.10

    # MARKET LONG fill = ask + slip = 2000.30
    trig, supp, fill, reason = evaluate_comparison_entry(
        direction="LONG", planned_entry=2000.10, order_type="MARKET", bid=bid, ask=ask, order_slip=slip
    )
    assert supp and trig
    assert fill == 2000.30
    assert reason == "MARKET_TRIGGERED"

    # MARKET SHORT fill = bid - slip = 1999.90
    trig, supp, fill, reason = evaluate_comparison_entry(
        direction="SHORT", planned_entry=2000.10, order_type="MARKET", bid=bid, ask=ask, order_slip=slip
    )
    assert supp and trig
    assert fill == 1999.90
    assert reason == "MARKET_TRIGGERED"

    # BUY LIMIT=2000.00: Ask=2000.20 has NOT touched, even though Bid=2000.00 is at limit
    trig, supp, fill, reason = evaluate_comparison_entry(
        direction="LONG", planned_entry=2000.00, order_type="LIMIT", bid=bid, ask=ask, order_slip=slip
    )
    assert supp and not trig
    assert fill is None
    assert reason == "BUY_LIMIT_WAITING_ASK"

    # SELL LIMIT=2000.00: Bid=1999.90 has NOT touched limit
    trig, supp, fill, reason = evaluate_comparison_entry(
        direction="SHORT", planned_entry=2000.00, order_type="LIMIT", bid=1999.90, ask=2000.10, order_slip=slip
    )
    assert supp and not trig
    assert fill is None
    assert reason == "SELL_LIMIT_WAITING_BID"

    # BUY STOP=2000.00: Ask=2000.20 >= 2000.00 triggers breakout fill = Ask + slip = 2000.30
    trig, supp, fill, reason = evaluate_comparison_entry(
        direction="LONG", planned_entry=2000.00, order_type="STOP", bid=bid, ask=ask, order_slip=slip
    )
    assert supp and trig
    assert fill == 2000.30
    assert reason == "BUY_STOP_BREAKOUT"

    # SELL STOP=2000.00: Bid=1999.90 <= 2000.00 triggers breakout fill = Bid - slip = 1999.80
    trig, supp, fill, reason = evaluate_comparison_entry(
        direction="SHORT", planned_entry=2000.00, order_type="STOP", bid=1999.90, ask=2000.10, order_slip=slip
    )
    assert supp and trig
    assert fill == 1999.80
    assert reason == "SELL_STOP_BREAKOUT"

    # Explicit slippage=0.0
    trig, supp, fill, _ = evaluate_comparison_entry(
        direction="LONG", planned_entry=2000.10, order_type="MARKET", bid=bid, ask=ask, order_slip=0.0
    )
    assert fill == 2000.20

    trig, supp, fill, _ = evaluate_comparison_entry(
        direction="SHORT", planned_entry=2000.10, order_type="MARKET", bid=bid, ask=ask, order_slip=0.0
    )
    assert fill == 2000.00


def test_advance_stop_comparison_excursion_parity():
    """B6: WAIT_ENTRY ticks do not inflate trade excursions; OPEN variants measure from actual_entry."""
    plan = build_stop_comparison_plan(
        opportunity_key="opp_excursion_test",
        model_id="M1_CONTINUATION",
        symbol="XAUUSDT",
        direction="SHORT",
        planned_entry=2000.00,
        take_profit=1980.00,
        legacy_sl=2010.00,
        anchor=None,
        buffer_result=None,
        policy_settings={"capital_usdt": 1000.0},
        now_ms=100_000,
        entry_intent="LIMIT",
        order_type="LIMIT",
        decision_cutoff_ms=100_000,
        expires_at_ms=500_000,
        cost_snapshot={"entry_slippage_usd": 0.10, "tp_slippage_usd": 0.0},
    )

    state = plan.to_dict()
    # Variant A is executable
    state["variant_a"]["can_execute"] = True
    state["variant_a"]["is_valid"] = True
    state["variant_a"]["meets_min_rr"] = True

    # 1. Quote while in WAIT_ENTRY: Bid=1999.00, Ask=1999.20 (LIMIT at 2000.00 not touched yet)
    q1 = {"bid": 1999.00, "ask": 1999.20, "timestamp": 110_000, "is_valid": True, "is_stale": False}
    adv1 = advance_stop_comparison(state, q1, as_of_ms=110_000)

    va1 = adv1["variant_a"]
    assert va1["execution_state"] == "WAIT_ENTRY"
    # Trade excursions must NOT be accumulated while in WAIT_ENTRY!
    assert "excursions" not in va1 or va1["excursions"]["max_adverse"] == 0.0

    # 2. Quote that touches limit: Bid=2000.00, Ask=2000.20
    q2 = {"bid": 2000.00, "ask": 2000.20, "timestamp": 120_000, "is_valid": True, "is_stale": False}
    adv2 = advance_stop_comparison(adv1, q2, as_of_ms=120_000)

    va2 = adv2["variant_a"]
    assert va2["execution_state"] == "OPEN"
    assert va2["actual_entry"] == 2000.00
    assert "excursions" in va2
    assert va2["excursions"]["max_adverse"] == 0.20  # Ask 2000.20 - entry 2000.00 = 0.20 spread adverse

    # 3. Market moves against position: Ask rises to 2004.50 (Adverse for SHORT = 2004.50 - 2000.00 = 4.50)
    q3 = {"bid": 2004.30, "ask": 2004.50, "timestamp": 130_000, "is_valid": True, "is_stale": False}
    adv3 = advance_stop_comparison(adv2, q3, as_of_ms=130_000)

    va3 = adv3["variant_a"]
    assert va3["execution_state"] == "OPEN"
    assert va3["excursions"]["max_adverse"] == 4.50
    assert va3["excursions"]["max_favorable"] == 0.0

    # 4. Market moves into profit: Ask drops to 1990.00 (Favorable for SHORT = 2000.00 - 1990.00 = 10.00)
    q4 = {"bid": 1989.80, "ask": 1990.00, "timestamp": 140_000, "is_valid": True, "is_stale": False}
    adv4 = advance_stop_comparison(adv3, q4, as_of_ms=140_000)

    va4 = adv4["variant_a"]
    assert va4["excursions"]["max_adverse"] == 4.50
    assert va4["excursions"]["max_favorable"] == 10.00


def test_advance_stop_comparison_exit_with_costs():
    """B4: Exit trigger passes cost snapshot and finalizes CLOSED without further post-close mutations."""
    plan = build_stop_comparison_plan(
        opportunity_key="opp_exit_test",
        model_id="M1_CONTINUATION",
        symbol="XAUUSDT",
        direction="SHORT",
        planned_entry=2000.00,
        take_profit=1980.00,
        legacy_sl=2010.00,
        anchor=None,
        buffer_result=None,
        policy_settings={"capital_usdt": 1000.0},
        now_ms=100_000,
        entry_intent="MARKET",
        order_type="MARKET",
        decision_cutoff_ms=100_000,
        expires_at_ms=500_000,
        cost_snapshot={"entry_slippage_usd": 0.10, "tp_slippage_usd": 0.0, "sl_slippage_usd": 0.10},
    )
    state = plan.to_dict()
    state["variant_a"]["can_execute"] = True
    state["variant_a"]["is_valid"] = True
    state["variant_a"]["meets_min_rr"] = True

    # 1. Trigger open via MARKET: Bid=2000.00, Ask=2000.20
    q_open = {"bid": 2000.00, "ask": 2000.20, "timestamp": 105_000, "is_valid": True, "is_stale": False}
    adv_open = advance_stop_comparison(state, q_open, as_of_ms=105_000)
    assert adv_open["variant_a"]["execution_state"] == "OPEN"
    assert adv_open["variant_a"]["actual_entry"] == 1999.90  # Bid 2000.00 - slip 0.10

    # 2. Quote hits SL for SHORT (Ask >= 2010.00): Ask=2010.50
    q_sl = {"bid": 2010.30, "ask": 2010.50, "timestamp": 120_000, "is_valid": True, "is_stale": False}
    adv_closed = advance_stop_comparison(adv_open, q_sl, as_of_ms=120_000)

    va_closed = adv_closed["variant_a"]
    assert va_closed["execution_state"] == "CLOSED"
    assert va_closed["exit_cause"] == "SL_HIT"
    assert va_closed["closed_at_ms"] == 120_000
    frozen_adverse = va_closed["excursions"]["max_adverse"]

    # 3. Subsequent quote must NOT mutate closed state or trade excursions
    q_post = {"bid": 2020.00, "ask": 2020.50, "timestamp": 130_000, "is_valid": True, "is_stale": False}
    adv_post = advance_stop_comparison(adv_closed, q_post, as_of_ms=130_000)
    assert adv_post["variant_a"]["execution_state"] == "CLOSED"
    assert adv_post["variant_a"]["excursions"]["max_adverse"] == frozen_adverse
