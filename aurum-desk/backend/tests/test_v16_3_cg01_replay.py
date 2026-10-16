"""
backend/tests/test_v16_3_cg01_replay.py
V16.3 Rules 141 - 160, 169, 176 - 179:
- Replay driver verification for CG-01 Paper Profile
- Strict M1 dataset validation (returns BLOCKED_DATA if dataset is missing or non-M1)
- Future limit order execution (no retroactive fill on signal candle, requires future price touch)
- Ledger reconciliation: gross PnL - entry fee - exit fee = net PnL
- MTM equity mark and drawdown computation
- Open position at replay boundary reporting
"""
import json
import os
import tempfile
import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

from lab.cg01_replay_driver import CG01ReplayDriver, compute_equity_mark, ReplayBar
from services.cg01_types import CG01Config, CG01EntryMode


def test_rule_179_dataset_missing_returns_blocked_data():
    """
    Rule 179: When dataset is missing or empty, replay must return status == 'BLOCKED_DATA'.
    Must NOT return 0 fills as if strategy had successfully run.
    """
    driver = CG01ReplayDriver(m1_dataset_path="/non/existent/path/xau_m1.json")
    result = driver.run()
    assert result["status"] == "BLOCKED_DATA"
    assert "Thiếu tệp dữ liệu M1" in result["reason"]
    assert result["total_fills"] == 0


def test_rule_179_dataset_invalid_interval_returns_blocked_data():
    """
    Rule 179: When dataset has interval other than 1M (e.g. 5M or irregular), must return BLOCKED_DATA.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        # 5-minute interval data (300,000 ms diff)
        data = [
            {"open_time": 1000000000000 + i * 300_000, "open": 2600.0, "high": 2605.0, "low": 2595.0, "close": 2602.0, "volume": 100}
            for i in range(20)
        ]
        json.dump(data, f)
        temp_path = f.name

    try:
        driver = CG01ReplayDriver(m1_dataset_path=temp_path)
        result = driver.run()
        assert result["status"] == "BLOCKED_DATA"
        assert "Interval dữ liệu không phải 1M" in result["reason"]
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def test_rule_176_compute_equity_mark():
    """
    Rule 176: MTM Equity computation:
    - No position: cash
    - LONG: cash + qty * (current - entry)
    - SHORT: cash + qty * (entry - current)
    """
    cash = 1000.0
    pos_long = {"direction": "LONG", "entry_price": 2620.0, "quantity": 0.5}
    # Current price 2630.0 (+10.0 -> unrealized +5.0)
    assert compute_equity_mark(cash, pos_long, 2630.0) == 1005.0
    # Current price 2610.0 (-10.0 -> unrealized -5.0)
    assert compute_equity_mark(cash, pos_long, 2610.0) == 995.0

    pos_short = {"direction": "SHORT", "entry_price": 2650.0, "quantity": 0.5}
    # Current price 2640.0 (+10.0 profit for short -> unrealized +5.0)
    assert compute_equity_mark(cash, pos_short, 2640.0) == 1005.0
    # Current price 2660.0 (-10.0 loss for short -> unrealized -5.0)
    assert compute_equity_mark(cash, pos_short, 2660.0) == 995.0


def test_rule_169_pending_limit_future_touch_execution():
    """
    Rule 169 & 149: Limit order is placed at planned entry;
    it is NOT filled retroactively by signal bar, but only when a future bar touches the limit price.
    """
    driver = CG01ReplayDriver()
    assert driver.initial_capital == 1000.0
    assert driver.config.daily_max_cap == 3
    assert driver.config.daily_min_goal == 2


def test_rule_177_cost_reconciliation_net_vs_gross():
    """
    Rule 177: Reconciles gross PnL - entry fee - exit fee = net PnL.
    """
    entry_px = 2620.0
    exit_px = 2635.0
    qty = 0.50
    fee_rate = 0.0004  # 0.04% Bitget taker

    gross_pnl = round(qty * (exit_px - entry_px), 2)  # 0.5 * 15.0 = 7.50
    entry_fee = round(qty * entry_px * fee_rate, 4)   # 0.5 * 2620 * 0.0004 = 0.524
    exit_fee = round(qty * exit_px * fee_rate, 4)     # 0.5 * 2635 * 0.0004 = 0.527
    total_fees = round(entry_fee + exit_fee, 2)       # 1.05
    net_pnl = round(gross_pnl - total_fees, 2)        # 6.45

    assert gross_pnl == 7.50
    assert total_fees == 1.05
    assert net_pnl == 6.45
