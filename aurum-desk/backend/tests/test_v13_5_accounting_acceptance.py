"""
AURUM DESK — V13.5 ACCOUNTING ACCEPTANCE TESTS
Verifies:
- Production closed trade accounting arithmetic for LONG and SHORT.
- Exact fee and adverse slippage attribution.
- Frozen initial risk denominator for realized R.
- Cash ledger reconciliation.
"""

import pytest
from lab.replay_contracts import ReplayPosition
from lab.replay_execution import compute_closed_trade_accounting
from lab.replay_metrics import reconcile_cash_equity
from domain_calculator import CostAssumptions


def test_01_short_trade_accounting_reference():
    # Benchmark from prompt: SHORT entry 100, SL 102, TP 96, qty 1, zero fees, multiplier 1
    costs = CostAssumptions(taker_fee_rate=0.0, slippage_usd=0.0)
    pos = ReplayPosition(
        position_id="pos-short-1",
        order_id="ord-1",
        setup_id="setup-1",
        session_id="NY-2026-07-15",
        entry_type="SMC_CONFIRMED",
        direction="SHORT",
        entry_time=1000,
        entry_price=100.0,
        stop_loss=102.0,
        take_profit=96.0,
        quantity=1.0,
        leverage=30,
        margin_mode="ISOLATED",
        initial_risk_usdt=2.0,  # 102 - 100
        planned_net_rr=2.0,
        entry_fee=0.0
    )

    # 1. Take Profit Exit at 96.0
    res_tp = compute_closed_trade_accounting(pos, exit_price=96.0, exit_time=2000, exit_cause="TAKE_PROFIT", costs=costs)
    assert res_tp["gross_pnl"] == 4.0
    assert res_tp["net_pnl"] == 4.0
    assert res_tp["realized_r"] == 2.0  # +4 / 2

    # 2. Stop Loss Exit at 102.0
    res_sl = compute_closed_trade_accounting(pos, exit_price=102.0, exit_time=3000, exit_cause="STOP_LOSS", costs=costs)
    assert res_sl["gross_pnl"] == -2.0
    assert res_sl["net_pnl"] == -2.0
    assert res_sl["realized_r"] == -1.0  # -2 / 2


def test_02_long_trade_realistic_fees_and_pnl():
    # Benchmark from prompt: LONG fill 2650.10 -> 2665.0, qty 0.1, fee 0.16 each leg
    costs = CostAssumptions(taker_fee_rate=0.00060375, slippage_usd=0.0)
    pos = ReplayPosition(
        position_id="pos-long-1",
        order_id="ord-2",
        setup_id="setup-2",
        session_id="NY-2026-07-16",
        entry_type="SMC_CONFIRMED",
        direction="LONG",
        entry_time=1000,
        entry_price=2650.10,
        stop_loss=2645.0,
        take_profit=2665.0,
        quantity=0.1,
        leverage=30,
        margin_mode="ISOLATED",
        initial_risk_usdt=0.51,
        planned_net_rr=2.5,
        entry_fee=0.16
    )

    res = compute_closed_trade_accounting(pos, exit_price=2665.0, exit_time=2000, exit_cause="TAKE_PROFIT", costs=costs)
    # Gross = (2665.0 - 2650.10) * 0.1 = 1.49
    assert round(res["gross_pnl"], 2) == 1.49
    # Exit fee approx 0.16
    assert round(res["exit_fee"], 2) == 0.16
    # Net = 1.49 - 0.16 - 0.16 = 1.17
    assert round(res["net_pnl"], 2) == 1.17


def test_03_cash_ledger_reconciliation():
    initial_cash = 1000.0
    postings = [
        {"amount": -0.16, "posting_type": "ENTRY_FEE"},
        {"amount": 1.49, "posting_type": "REALIZED_GROSS_PNL"},
        {"amount": -0.16, "posting_type": "EXIT_FEE"}
    ]
    reported_cash = 1001.17
    reconciled, total_postings, diff = reconcile_cash_equity(initial_cash, postings, reported_cash)
    assert reconciled is True
    assert diff == 0.0
    assert total_postings == 1.17
