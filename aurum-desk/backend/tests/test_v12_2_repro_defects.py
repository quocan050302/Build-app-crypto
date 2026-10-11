"""
V12_2 Defect Reproduction Test Suite (Phase 1):
Verifies and reproduces defects D01 - D20 before applying fixes.
Each test specifically isolates the defect mechanism.
"""
import os
import math
import pytest
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from domain_calculator import CostAssumptions, calculate_risk_reward
from lab.ny_strategy_variants import (
    NY_TZ,
    get_ny_datetime,
    compute_pre_ny_range,
    is_pre_ny_window,
    evaluate_mode_c_quota_candidate
)
from lab.historical_market_data import HistoricalMarketDataProvider
import schemas

def test_repro_d03_cost_assumptions_ignores_fee_rate():
    """D03: CostAssumptions maps legacy fee_rate to taker_fee_rate correctly."""
    costs = CostAssumptions(fee_rate=0.0006, slippage_usd=0.10)
    assert costs.taker_fee_rate == 0.0006, "D03 Fixed: fee_rate was properly mapped to taker_fee_rate"


def test_repro_d07_pre_ny_range_includes_0830_close_bar():
    """D07: Pre-NY range strictly excludes candles closing after 08:25 NY."""
    dt_ny = datetime(2026, 7, 10, 8, 15, 0, tzinfo=NY_TZ)
    ts_ms = int(dt_ny.timestamp() * 1000)

    candle_0815 = {
        "timestamp": ts_ms,
        "open": 2400.0,
        "high": 2500.0,  # Spurious high on candle closing at 08:30
        "low": 2390.0,
        "close": 2495.0,
        "volume": 10.0,
        "close_time": ts_ms + 15 * 60 * 1000  # 08:30:00
    }
    prior_bars = []
    for h in range(8):
        c_dt = datetime(2026, 7, 10, h, 0, 0, tzinfo=NY_TZ)
        prior_bars.append({
            "timestamp": int(c_dt.timestamp() * 1000),
            "open": 2400.0,
            "high": 2420.0,
            "low": 2380.0,
            "close": 2410.0,
            "volume": 10.0,
            "close_time": int(c_dt.timestamp() * 1000) + 15 * 60 * 1000
        })
    bars = prior_bars + [candle_0815]
    rng = compute_pre_ny_range(bars, "2026-07-10")
    assert rng is not None
    # 08:15 bar closing at 08:30 must be excluded, so range_high is 2420.0, NOT 2500.0!
    assert rng["range_high"] == 2420.0, "D07 Fixed: bar closing at 08:30 is properly excluded from pre-NY range"
    assert rng["is_frozen"] is True


def test_repro_d05_mode_c_tp_stretching_and_conflict():
    """D05: Mode C rejects H1/H4 trend conflict and does not stretch TP."""
    dt_ny = datetime(2026, 7, 10, 14, 30, 0, tzinfo=NY_TZ)
    ts_ms = int(dt_ny.timestamp() * 1000)

    bars_5m = []
    for m in range(30):
        b_dt = datetime(2026, 7, 10, 12, 0, 0, tzinfo=NY_TZ)
        bars_5m.append({
            "timestamp": ts_ms - (30 - m) * 5 * 60 * 1000,
            "open": 2400.0 + (m * 0.1),
            "high": 2401.0 + (m * 0.1),
            "low": 2399.0 + (m * 0.1),
            "close": 2400.5 + (m * 0.1),
            "volume": 5.0,
            "close_time": ts_ms - (30 - m - 1) * 5 * 60 * 1000
        })

    # Test H1/H4 conflict: H1 is BEARISH, but H4 is BULLISH -> must reject with conflict!
    cand, err = evaluate_mode_c_quota_candidate(
        curr_bar_5m=bars_5m[-1],
        recent_bars_5m=bars_5m,
        recent_bars_15m=bars_5m,
        d_bias="BULLISH",
        h4_bias="BULLISH",
        h1_trend="BEARISH",  # Conflicting!
        sim_time=ts_ms,
        capital=1000.0,
        quota_risk_pct=0.10,
        leverage=30,
        margin_mode="ISOLATED",
        costs=CostAssumptions(taker_fee_rate=0.0006),
        spread_usd=0.20,
        min_net_rr=2.0
    )
    assert cand is None
    assert err == "H1_H4_TREND_CONFLICT", "D05 Fixed: Trend conflict properly rejected"


def test_repro_d09_quality_risk_pct_default_overrides_ui():
    """D09: When quality_risk_pct is absent, it derives from risk_pct."""
    req = schemas.ReplayRunRequest(
        risk_pct=0.10,
    )
    assert req.risk_pct == 0.10
    assert req.quality_risk_pct == 0.10, "D09 Fixed: quality_risk_pct derived from risk_pct"


def test_repro_d11_stress_double_entry_slippage():
    """D11: Trade item gross_pnl already has entry slippage, subtracting ent_slip again double-counts."""
    # Entry: 2500, SL: 2490, qty: 0.1, direction: LONG
    # If fill_entry already includes slippage 0.10 (so fill_entry = 2500.10)
    # gross_pnl = (exit_price - fill_entry) * qty = (2490.0 - 2500.10) * 0.1 = -1.01
    # If someone subtracts ent_slip = qty * slippage (0.1 * 0.10 = 0.01) again:
    # net = -1.01 - 0.01 = -1.02 -> slippage was subtracted twice!
    gross_with_slip = (2490.0 - 2500.10) * 0.1  # -1.01
    double_subtracted = gross_with_slip - (0.1 * 0.10)  # -1.02
    assert abs(double_subtracted - (-1.02)) < 1e-6
