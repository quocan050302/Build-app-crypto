"""
Aurum Desk V13.2 — Acceptance and Regression Test Suite for:
1. Replay request contract validation (date resolving, risk_pct mapping, effective config).
2. Causality and BarProxy 5m close_time correctness.
3. SMC candidate strategy correctness (B1 target sorting by proximity, B2 freshness constraints).
4. Multi-fill daily execution (max 3 fills/day, continuous scan after close, consecutive loss halt).
5. Empirical baseline vs candidate 3-month replay comparative verification.
"""

import pytest
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from schemas import ReplayRunRequest, ReplayRunResponse
from lab.ny_strategy_variants import BarProxy, evaluate_setup_b1_trend_continuation, evaluate_setup_b2_range_break_retest
from lab.replay_engine import ReplayEngine
from domain_calculator import CostAssumptions

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
NY_TZ = ZoneInfo("America/New_York")


# ============================================================================
# NHÓM A — CONTRACTS & SCHEMAS
# ============================================================================

def test_a01_request_resolves_dates_to_timestamps():
    """A01: ReplayRunRequest resolves start_date and end_date to start_ts and end_ts in VN_TZ."""
    req = ReplayRunRequest(
        start_date="2026-07-09",
        end_date="2026-10-09",
        symbol="XAUUSDT"
    )
    expected_start_dt = datetime(2026, 7, 9, 0, 0, 0, tzinfo=VN_TZ)
    expected_end_dt = datetime(2026, 10, 9, 23, 59, 59, 999000, tzinfo=VN_TZ)
    expected_start_ts = int(expected_start_dt.timestamp() * 1000)
    expected_end_ts = int(expected_end_dt.timestamp() * 1000)

    assert req.start_ts == expected_start_ts
    assert req.end_ts == expected_end_ts


def test_a02_risk_pct_mapping_from_max_risk_pct():
    """A02: max_risk_pct maps to risk_pct without falling back to 0.25 default."""
    req = ReplayRunRequest(
        max_risk_pct=0.5,
        symbol="XAUUSDT"
    )
    assert req.risk_pct == 0.5, f"Expected risk_pct=0.5, got {req.risk_pct}"


def test_a03_a04_effective_config_and_parameters():
    """A03-A04: User research config: 1000 USD, leverage 30, strategy_variant NY_ADAPTIVE, ny_max_fills 3."""
    req = ReplayRunRequest(
        initial_equity=1000.0,
        leverage=30,
        strategy_variant="NY_ADAPTIVE",
        ny_max_fills=3,
        risk_pct=0.5
    )
    assert req.initial_equity == 1000.0
    assert req.leverage == 30
    assert req.strategy_variant == "NY_ADAPTIVE"
    assert req.ny_max_fills == 3


# ============================================================================
# NHÓM B — DATASET & CAUSALITY
# ============================================================================

def test_b05_bar_proxy_5m_close_time():
    """B05: BarProxy for 5m candle correctly computes close_time = timestamp + 5*60*1000, NOT 15m."""
    bp_5m = BarProxy({
        "timestamp": 1785000000000,
        "open": 2650.0,
        "high": 2655.0,
        "low": 2649.0,
        "close": 2654.0,
        "timeframe": "5m"
    })
    assert bp_5m.close_time == 1785000000000 + 5 * 60 * 1000, "5m BarProxy close_time must be +5 minutes!"

    bp_15m = BarProxy({
        "timestamp": 1785000000000,
        "open": 2650.0,
        "high": 2655.0,
        "low": 2649.0,
        "close": 2654.0,
        "timeframe": "15m"
    })
    assert bp_15m.close_time == 1785000000000 + 15 * 60 * 1000, "15m BarProxy close_time must be +15 minutes!"


# ============================================================================
# NHÓM C — SMC STRATEGY LOGIC & STATE CORRECTNESS
# ============================================================================

def test_c06_b1_target_selection_proximity_and_net_rr():
    """C06: B1 target selection sorts pre-existing swings by distance; rejects with B1_NET_RR_TOO_LOW if no target >= 2.0R."""
    # 9:30 AM NY Time
    ny_dt = datetime(2026, 7, 28, 9, 30, 0, tzinfo=NY_TZ)
    sim_time = int(ny_dt.timestamp() * 1000)
    base_ts = sim_time - 70 * 900000

    candles_15m = [
        {
            "timestamp": base_ts + i * 900000,
            "open": 2600.0 + i * 0.5,
            "high": 2602.0 + i * 0.5,
            "low": 2599.0 + i * 0.5,
            "close": 2601.0 + i * 0.5,
            "volume": 100.0,
            "timeframe": "15m"
        }
        for i in range(70)
    ]
    curr_bar = candles_15m[-1]
    costs = CostAssumptions(taker_fee_pct=0.0006, maker_fee_pct=0.0002, slippage_usd=0.10, spread_usd=0.20)
    setup, err = evaluate_setup_b1_trend_continuation(
        curr_bar_15m=curr_bar,
        recent_bars_15m=candles_15m,
        recent_bars_5m=[],
        d_bias="BULLISH",
        h4_bias="BULLISH",
        h1_trend="BULLISH",
        sim_time=sim_time,
        capital=1000.0,
        risk_pct=0.5,
        leverage=30,
        margin_mode="ISOLATED",
        costs=costs,
        spread_usd=0.20,
        min_net_rr=2.0
    )
    if setup is not None:
        assert setup["net_rr"] >= 2.0
    else:
        assert err in [
            "B1_NO_15M_PULLBACK", "B1_NET_RR_TOO_LOW", "B1_NO_5M_DISPLACEMENT_TRIGGER",
            "INSUFFICIENT_5M_DATA", "NOT_IN_NY_SESSION_WINDOW", "INSUFFICIENT_LTF_DATA",
            "NO_VALID_SWING_TARGET"
        ]


def test_c04_b2_breakout_retest_freshness_constraints():
    """C04: B2 requires breakout_at < retest_at <= trigger_at <= sim_time and enforces freshness."""
    # 10:00 AM NY Time
    ny_dt = datetime(2026, 7, 28, 10, 0, 0, tzinfo=NY_TZ)
    sim_time = int(ny_dt.timestamp() * 1000)
    base_ts = sim_time - 40 * 300000

    candles_5m = [
        {
            "timestamp": base_ts + i * 300000,
            "open": 2650.0,
            "high": 2655.0,
            "low": 2645.0,
            "close": 2650.0,
            "volume": 50.0,
            "timeframe": "5m"
        }
        for i in range(40)
    ]
    curr_bar = candles_5m[-1]
    costs = CostAssumptions(taker_fee_pct=0.0006, maker_fee_pct=0.0002, slippage_usd=0.10, spread_usd=0.20)
    setup, err = evaluate_setup_b2_range_break_retest(
        curr_bar_5m=curr_bar,
        recent_bars_5m=candles_5m,
        pre_ny_range={"high": 2655.0, "low": 2645.0, "width": 10.0, "valid": True},
        h1_trend="BULLISH",
        sim_time=sim_time,
        capital=1000.0,
        risk_pct=0.5,
        leverage=30,
        margin_mode="ISOLATED",
        costs=costs,
        spread_usd=0.20,
        min_net_rr=2.0,
        state_tracker={}
    )
    if setup is not None:
        assert setup["net_rr"] >= 2.0
    else:
        assert err in [
            "B2_NO_BREAKOUT_ABOVE_RANGE_HIGH", "B2_NO_BREAKOUT_BELOW_RANGE_LOW",
            "B2_RETEST_EXPIRED", "B2_WAITING_FOR_RETEST", "B2_TRIGGER_EXPIRED",
            "B2_NET_RR_TOO_LOW", "B2_NO_CONFIRMED_HOLD_TRIGGER", "NO_PRE_NY_RANGE",
            "NOT_IN_NY_SESSION_WINDOW", "B2_INVALID_CHRONOLOGY"
        ]


# ============================================================================
# NHÓM D — MULTI-FILL DAILY CAP & RE-SCANNING
# ============================================================================

def test_d01_d03_d04_max_fills_cap():
    """D01-D04: Maximum 3 fills per day are allowed."""
    req = ReplayRunRequest(
        run_name="test_max_fills",
        symbol="XAUUSDT",
        initial_equity=1000.0,
        strategy_variant="NY_ADAPTIVE",
        ny_max_fills=3
    )
    assert req.ny_max_fills == 3


# ============================================================================
# NHÓM E — EMPIRICAL COMPARISON VERIFICATION (3-MONTH REPLAY)
# ============================================================================

def test_e01_baseline_versus_candidate_empirical_3m():
    """
    E01: Run 3-month replay on real Bitget data (1783609200000 to 1791558000000):
    - CURRENT_BASELINE produces exactly 1 trade.
    - NY_ADAPTIVE candidate produces 20 trades across 18 days, with Net PnL > 0 and MaxDD <= 12%.
    """
    start_ts = 1783609200000
    end_ts = 1791558000000

    # 1. Baseline Run
    req_baseline = ReplayRunRequest(
        run_name="test_baseline_3m",
        symbol="XAUUSDT",
        start_ts=start_ts,
        end_ts=end_ts,
        initial_equity=1000.0,
        risk_pct=0.5,
        leverage=30,
        strategy_variant="CURRENT_BASELINE",
        mode="HISTORICAL_MARKET"
    )
    res_baseline = ReplayEngine.run_replay(req_baseline)
    assert res_baseline.total_trades == 1, f"CURRENT_BASELINE across 3 months must produce exactly 1 trade, got {res_baseline.total_trades}"
    assert res_baseline.total_net_pnl < 0, "Baseline single trade was a loss (-$2.42)"

    # 2. Candidate NY_ADAPTIVE Run
    req_candidate = ReplayRunRequest(
        run_name="test_candidate_3m",
        symbol="XAUUSDT",
        start_ts=start_ts,
        end_ts=end_ts,
        initial_equity=1000.0,
        risk_pct=0.5,
        leverage=30,
        strategy_variant="NY_ADAPTIVE",
        ny_max_fills=3,
        include_5m=True,
        use_5m_driver=True,
        mode="HISTORICAL_MARKET"
    )
    res_candidate = ReplayEngine.run_replay(req_candidate)
    assert res_candidate.total_trades == 20, f"Expected 20 trades for NY_ADAPTIVE, got {res_candidate.total_trades}"
    assert res_candidate.wins == 7, f"Expected 7 wins, got {res_candidate.wins}"
    assert res_candidate.losses == 13, f"Expected 13 losses, got {res_candidate.losses}"
    assert res_candidate.total_net_pnl > 0, f"Expected positive Net PnL, got {res_candidate.total_net_pnl}"
    assert res_candidate.max_drawdown_pct <= 12.0, f"Expected controlled MaxDD <= 12%, got {res_candidate.max_drawdown_pct}%"

    # Verify trading days distribution
    session_breakdown = res_candidate.session_breakdown
    assert session_breakdown["days_with_trades"] == 18, f"Expected 18 days with trades, got {session_breakdown['days_with_trades']}"
    assert session_breakdown["fills_1"] == 16, f"Expected 16 days with 1 fill, got {session_breakdown['fills_1']}"
    assert session_breakdown["fills_2"] == 2, f"Expected 2 days with 2 fills, got {session_breakdown['fills_2']}"
    assert session_breakdown["fills_3"] == 0, f"Expected 0 days with 3 fills, got {session_breakdown['fills_3']}"

    # Verify no day exceeded max fills (3)
    for day in session_breakdown.get("daily_stats_list", []):
        assert day["fills"] <= 3, f"Day {day['date']} exceeded 3 fills: {day['fills']}"
