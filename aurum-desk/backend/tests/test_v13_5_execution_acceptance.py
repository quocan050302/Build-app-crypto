"""
AURUM DESK — V13.5 EXECUTION SIMULATOR ACCEPTANCE TESTS
Verifies:
- Order submission vs execution event separation.
- Adverse fill price execution with spread and slippage.
- Post-fill geometry & Net R:R re-validation.
- Conservative exit resolution when candle touches both SL and TP.
"""

import pytest
from lab.replay_contracts import ReplayPendingOrder, ReplayMarketEvent, ReplayPosition
from lab.replay_execution import submit_replay_order, try_fill_pending_order, evaluate_position_exit
from domain_calculator import CostAssumptions


def test_01_submit_pending_order_does_not_fill():
    candidate = {
        "direction": "LONG",
        "entry_price": 2650.0,
        "stop_loss": 2645.0,
        "take_profit": 2670.0,
        "quantity": 0.1,
        "planned_net_rr": 2.5
    }
    order = submit_replay_order(
        candidate_plan=candidate,
        session_id="NY-2026-07-20",
        decision_ms=1000,
        earliest_execution_ms=1050,
        expiry_ms=5000
    )
    assert order.status == "SUBMITTED"
    assert order.executed_at_ms is None
    assert order.fill_price is None


def test_02_fill_at_next_event_with_adverse_execution():
    candidate = {
        "direction": "LONG",
        "entry_price": 2650.0,
        "stop_loss": 2645.0,
        "take_profit": 2670.0,
        "quantity": 0.1,
        "planned_net_rr": 2.5
    }
    order = submit_replay_order(
        candidate_plan=candidate,
        session_id="NY-2026-07-20",
        decision_ms=1000,
        earliest_execution_ms=1050,
        expiry_ms=5000
    )
    costs = CostAssumptions(taker_fee_rate=0.0004, slippage_usd=0.10)

    # 1. Event prior to earliest_execution_ms -> Not filled
    early_evt = ReplayMarketEvent(kind="OPEN", timestamp=1020, timeframe="5M", open_price=2650.0, high_price=2652.0, low_price=2649.0, close_price=2651.0)
    pos_early, posting_early, status_early = try_fill_pending_order(order, early_evt, costs, capital=1000.0, risk_pct=0.10, spread_usd=0.20)
    assert pos_early is None
    assert status_early == "WAITING_EARLIEST_EXECUTION"

    # 2. Event at or after earliest_execution_ms -> Filled
    valid_evt = ReplayMarketEvent(kind="OPEN", timestamp=1100, timeframe="5M", open_price=2650.0, high_price=2652.0, low_price=2649.0, close_price=2651.0)
    pos, posting, status = try_fill_pending_order(order, valid_evt, costs, capital=1000.0, risk_pct=0.10, spread_usd=0.20)
    assert pos is not None
    assert status == "FILLED"
    # LONG fill = open 2650.0 + 0.10 (half spread) + 0.10 (slippage) = 2650.20
    assert pos.entry_price == 2650.20
    assert posting["posting_type"] == "ENTRY_FEE"
    assert posting["amount"] < 0.0


def test_03_conservative_ambiguous_exit_resolution():
    costs = CostAssumptions(taker_fee_rate=0.0004, slippage_usd=0.10)
    pos = ReplayPosition(
        position_id="pos-test",
        order_id="ord-test",
        setup_id="setup-test",
        session_id="NY-2026-07-20",
        entry_type="SMC_CONFIRMED",
        direction="LONG",
        entry_time=1000,
        entry_price=2650.0,
        stop_loss=2645.0,
        take_profit=2665.0,
        quantity=0.1,
        leverage=30,
        margin_mode="ISOLATED",
        initial_risk_usdt=0.50,
        planned_net_rr=2.5,
        entry_fee=0.10
    )

    # Bar with High=2670 (touches TP 2665) AND Low=2640 (touches SL 2645)
    ambiguous_bar = ReplayMarketEvent(
        kind="CLOSE",
        timestamp=2000,
        timeframe="15M",
        open_price=2650.0,
        high_price=2670.0,
        low_price=2640.0,
        close_price=2655.0
    )

    accounting, exit_cause = evaluate_position_exit(pos, ambiguous_bar, costs)
    assert exit_cause == "STOP_LOSS"
    assert accounting["is_ambiguous"] is True
    assert accounting["exit_cause"] == "STOP_LOSS"
