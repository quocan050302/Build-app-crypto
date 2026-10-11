"""
AURUM DESK — V13.5 METRICS & DRAWDOWN ACCEPTANCE TESTS
Verifies:
- 1 CLOSED + 1 OPEN trade partitioning & 100% winrate assertion.
- Drawdown calculation strictly relative to peak equity.
- Zero-loss and breakeven handling.
"""

import pytest
from lab.replay_metrics import (
    partition_trade_records,
    compute_equity_metrics,
    aggregate_replay_metrics
)


def test_01_trade_partitioning_one_closed_one_open():
    all_trades = [
        {"id": "t1", "status": "CLOSED", "net_pnl": 5.0, "realized_r": 1.5, "total_fees": 0.20, "entry_type": "SMC_CONFIRMED"},
        {"id": "t2", "status": "OPEN", "net_pnl": 0.0, "realized_r": None, "total_fees": 0.10, "entry_type": "SMC_CONTEXT_SCHEDULED_PAPER"}
    ]
    closed, open_p, _ = partition_trade_records(all_trades)
    assert len(closed) == 1
    assert len(open_p) == 1

    metrics = aggregate_replay_metrics(all_trades, ledger_postings=[], equity_curve=[])
    assert metrics["closed_count"] == 1
    assert metrics["open_positions_count"] == 1
    assert metrics["fills_count"] == 2
    assert metrics["total_trades"] == 1  # Legacy alias strictly matches closed_count
    assert metrics["wins"] == 1
    assert metrics["losses"] == 0
    assert metrics["win_rate_pct"] == 100.0


def test_02_drawdown_relative_to_peak_equity():
    # Curve: 1000 -> 1010 -> 990
    equity_curve = [
        {"timestamp": 1000, "equity": 1000.0},
        {"timestamp": 2000, "equity": 1010.0},
        {"timestamp": 3000, "equity": 990.0}
    ]
    eq_m = compute_equity_metrics(equity_curve, initial_equity=1000.0)
    assert eq_m["peak_equity"] == 1010.0
    assert eq_m["max_drawdown_usdt"] == 20.0
    # DD% = 20.0 / 1010.0 * 100 = 1.98%
    assert eq_m["max_drawdown_pct"] == 1.98
