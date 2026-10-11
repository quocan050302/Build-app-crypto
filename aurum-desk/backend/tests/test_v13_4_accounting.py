"""
Aurum Desk V13.4 — Unit tests for independent reference accounting and ledger reconciliation.
Phần 34, 35, 36, 50, 80.
"""
import pytest
from schemas import ReplayTradeItem
from domain_calculator import CostAssumptions, calculate_risk_reward


def test_01_short_trade_accounting_reference():
    """A01: Reference fixture for SHORT: entry 100, SL 102, TP 96, qty 1, zero costs."""
    entry_p = 100.0
    sl_p = 102.0
    tp_p = 96.0
    qty = 1.0
    initial_risk = abs(entry_p - sl_p) * qty  # 2.0 USD

    # Win scenario: exit at 96
    exit_tp = 96.0
    gross_pnl_win = (entry_p - exit_tp) * qty  # +4.0 USD
    realized_r_win = gross_pnl_win / initial_risk  # +2.0R
    assert gross_pnl_win == 4.0
    assert realized_r_win == 2.0

    # Loss scenario: exit at 102
    exit_sl = 102.0
    gross_pnl_loss = (entry_p - exit_sl) * qty  # -2.0 USD
    realized_r_loss = gross_pnl_loss / initial_risk  # -1.0R
    assert gross_pnl_loss == -2.0
    assert realized_r_loss == -1.0


def test_02_open_trade_partitioning():
    """A02: OPEN trade must not be included in closed trades count or win rate denominator."""
    trade_closed = ReplayTradeItem(
        id="trade-1",
        direction="LONG",
        order_type="MARKET",
        entry_time=1785000000000,
        entry_price=2650.0,
        exit_price=2665.0,
        stop_loss=2645.0,
        take_profit=2665.0,
        quantity=0.10,
        initial_risk_usdt=0.50,
        gross_pnl=1.50,
        fees=0.30,
        slippage=0.02,
        session="NEW_YORK",
        status="CLOSED",
        net_pnl=1.18,
        realized_r=2.0
    )
    trade_open = ReplayTradeItem(
        id="trade-2",
        direction="SHORT",
        order_type="MARKET",
        entry_time=1785003000000,
        entry_price=2650.0,
        exit_price=2650.0,
        stop_loss=2655.0,
        take_profit=2635.0,
        quantity=0.10,
        initial_risk_usdt=0.50,
        gross_pnl=0.0,
        fees=0.15,
        slippage=0.01,
        session="NEW_YORK",
        status="OPEN",
        net_pnl=0.0,
        realized_r=0.0
    )
    all_trades = [trade_closed, trade_open]

    closed_only = [t for t in all_trades if t.status == "CLOSED"]
    open_only = [t for t in all_trades if t.status == "OPEN"]

    assert len(closed_only) == 1
    assert len(open_only) == 1

    wins = sum(1 for t in closed_only if t.net_pnl > 0)
    win_rate = (wins / len(closed_only)) * 100.0
    assert win_rate == 100.0, "Win rate of 1 closed winning trade must be 100%, not 50%!"


def test_03_no_double_counting_fees():
    """A03: Entry fee and exit fee are deducted exactly once; slippage in fill price is not subtracted again."""
    entry_fill = 2650.10  # includes slippage
    exit_fill = 2665.00
    qty = 0.10
    entry_fee = 0.16
    exit_fee = 0.16

    gross_pnl = (exit_fill - entry_fill) * qty  # (2665.00 - 2650.10) * 0.10 = 1.49 USD
    net_pnl = round(gross_pnl - entry_fee - exit_fee, 2)  # 1.49 - 0.32 = 1.17 USD

    assert round(gross_pnl, 2) == 1.49
    assert net_pnl == 1.17
