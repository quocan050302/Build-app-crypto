import pytest
from lab.replay_contracts import ReplayPosition
from lab.replay_execution import compute_closed_trade_accounting
from domain_calculator import CostAssumptions

def test_long_hand_calculated_accounting_taker_and_maker():
    # PHẦN 83, 99: LONG entry 100, exit 110, qty 2, multiplier 1, entry fee rate 0.001
    pos = ReplayPosition(
        position_id="pos-long-1",
        order_id="ord-1",
        setup_id="set-1",
        session_id="NY-2026-07-15",
        entry_type="QUALITY_ENTRY",
        direction="LONG",
        entry_time=1000,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        quantity=2.0,
        initial_risk_usdt=10.0,
        planned_net_rr=2.0,
        leverage=30,
        margin_mode="ISOLATED",
        entry_fee=0.20,  # 100 * 2 * 0.001 = 0.20
        status="OPEN"
    )
    # Taker exit
    costs_taker = CostAssumptions(taker_fee_rate=0.001, maker_fee_rate=0.0002, tp_is_maker=False)
    acct_taker = compute_closed_trade_accounting(
        position=pos,
        exit_price=110.0,
        exit_time=2000,
        exit_cause="TAKE_PROFIT",
        costs=costs_taker,
        multiplier=1.0
    )
    assert acct_taker["gross_pnl"] == 20.0
    assert acct_taker["entry_fee"] == 0.20
    assert acct_taker["exit_fee"] == 0.22
    assert acct_taker["net_pnl"] == 19.58
    assert acct_taker["realized_r"] == 1.96

    # Maker exit
    pos.status = "OPEN"
    costs_maker = CostAssumptions(taker_fee_rate=0.001, maker_fee_rate=0.0002, tp_is_maker=True)
    acct_maker = compute_closed_trade_accounting(
        position=pos,
        exit_price=110.0,
        exit_time=2000,
        exit_cause="TAKE_PROFIT",
        costs=costs_maker,
        multiplier=1.0
    )
    assert acct_maker["gross_pnl"] == 20.0
    assert acct_maker["entry_fee"] == 0.20
    assert acct_maker["exit_fee"] == 0.044
    assert acct_maker["net_pnl"] == 19.756
    assert acct_maker["realized_r"] == 1.98

def test_short_hand_calculated_accounting_taker_and_maker():
    # PHẦN 83, 99: SHORT entry 100, exit 90, qty 2, multiplier 1
    pos = ReplayPosition(
        position_id="pos-short-1",
        order_id="ord-2",
        setup_id="set-2",
        session_id="NY-2026-07-15",
        entry_type="QUALITY_ENTRY",
        direction="SHORT",
        entry_time=1000,
        entry_price=100.0,
        stop_loss=105.0,
        take_profit=90.0,
        quantity=2.0,
        initial_risk_usdt=10.0,
        planned_net_rr=2.0,
        leverage=30,
        margin_mode="ISOLATED",
        entry_fee=0.20,
        status="OPEN"
    )
    # Taker exit
    costs_taker = CostAssumptions(taker_fee_rate=0.001, maker_fee_rate=0.0002, tp_is_maker=False)
    acct_taker = compute_closed_trade_accounting(
        position=pos,
        exit_price=90.0,
        exit_time=2000,
        exit_cause="TAKE_PROFIT",
        costs=costs_taker,
        multiplier=1.0
    )
    assert acct_taker["gross_pnl"] == 20.0
    assert acct_taker["entry_fee"] == 0.20
    assert acct_taker["exit_fee"] == 0.18
    assert acct_taker["net_pnl"] == 19.62
    assert acct_taker["realized_r"] == 1.96

    # Maker exit
    pos.status = "OPEN"
    costs_maker = CostAssumptions(taker_fee_rate=0.001, maker_fee_rate=0.0002, tp_is_maker=True)
    acct_maker = compute_closed_trade_accounting(
        position=pos,
        exit_price=90.0,
        exit_time=2000,
        exit_cause="TAKE_PROFIT",
        costs=costs_maker,
        multiplier=1.0
    )
    assert acct_maker["gross_pnl"] == 20.0
    assert acct_maker["entry_fee"] == 0.20
    assert acct_maker["exit_fee"] == 0.036
    assert acct_maker["net_pnl"] == 19.764
    assert acct_maker["realized_r"] == 1.98

def test_contract_multiplier_ten():
    # PHẦN 83, 99: Multiplier = 10
    pos = ReplayPosition(
        position_id="pos-long-mult-10",
        order_id="ord-3",
        setup_id="set-3",
        session_id="NY-2026-07-15",
        entry_type="QUALITY_ENTRY",
        direction="LONG",
        entry_time=1000,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        quantity=2.0,
        initial_risk_usdt=100.0,
        planned_net_rr=2.0,
        leverage=30,
        margin_mode="ISOLATED",
        entry_fee=2.0,  # 100 * 2 * 0.001 * 10 = 2.0
        status="OPEN"
    )
    costs_taker = CostAssumptions(taker_fee_rate=0.001, maker_fee_rate=0.0002, tp_is_maker=False)
    acct = compute_closed_trade_accounting(
        position=pos,
        exit_price=110.0,
        exit_time=2000,
        exit_cause="TAKE_PROFIT",
        costs=costs_taker,
        multiplier=10.0
    )
    assert acct["gross_pnl"] == 200.0
    assert acct["entry_fee"] == 2.0
    assert acct["exit_fee"] == 2.20
    assert acct["net_pnl"] == 195.80
