import pytest
from schemas import ReplayRunRequest
from lab.replay_engine import ReplayEngine

def test_engine_baseline_e2e():
    # PHẦN 107: Real engine run on baseline
    # 1 trade produced, valid integrity checks, no exceptions
    req = ReplayRunRequest(
        run_name="test_e2e_baseline",
        symbol="XAUUSDT",
        start_ts=1783609200000,
        end_ts=1791558000000,
        initial_equity=1000.0,
        risk_pct=0.5,
        leverage=30,
        strategy_variant="CURRENT_BASELINE",
        mode="HISTORICAL_MARKET"
    )
    res = ReplayEngine.run_replay(req)
    assert res.total_trades == 1
    assert res.wins + res.losses == 1
    assert res.total_net_pnl < 0.0
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.direction in ("LONG", "SHORT")
    assert t.entry_price > 0.0
    assert t.exit_price > 0.0
    assert t.net_pnl != 0.0

def test_engine_adaptive_scheduled_e2e():
    # PHẦN 107: Real engine run on NY_ADAPTIVE with DAILY_PAPER
    # Proves end-to-end execution, positions, accounting, integrity
    req = ReplayRunRequest(
        run_name="test_e2e_adaptive_scheduled",
        symbol="XAUUSDT",
        start_ts=1783609200000,
        end_ts=1784214000000, # 1 week window
        initial_equity=1000.0,
        risk_pct=0.5,
        leverage=30,
        strategy_variant="NY_ADAPTIVE",
        entry_cadence="DAILY_PAPER",
        ny_max_fills=3,
        include_5m=True,
        use_5m_driver=True,
        mode="HISTORICAL_MARKET"
    )
    res = ReplayEngine.run_replay(req)
    assert res.total_trades >= 1
    assert res.final_equity > 0.0
    for t in res.trades:
        assert t.direction in ("LONG", "SHORT")
        assert t.entry_price > 0.0
        assert t.stop_loss > 0.0
        assert t.take_profit > 0.0
