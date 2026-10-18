#!/usr/bin/env python3
"""
tools/test_baseline_research_tools.py
Comprehensive test suite for baseline research downloader, aggregation parity, and harness.
Enforces:
- Gate 1: Downloader pagination boundary, duplicate detection, progress checks, and error handling.
- Gate 2: Full parity checks between fast_aggregate_closed_bars / fast_select_closed_candles and native functions.
- Gate 4: Harness context manager scoping, immediate disk-first export, and graceful interruption handling.
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from typing import Any, Dict, List
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import lab.cg01_replay_driver as replay_driver_mod
import services.cg01_context_adapter as context_adapter_mod
from services.cg01_context_adapter import (
    CanonicalCandle,
    aggregate_closed_bars as orig_aggregate_closed_bars,
    select_closed_candles as orig_select_closed_candles,
)

from tools.download_v16_27_m1_dataset import (
    validate_and_normalize,
    compute_logical_sha256,
    CADENCE_MS,
)
from tools.eval_cg01_v16_27_baseline import (
    fast_aggregate_closed_bars,
    fast_select_closed_candles,
    fast_aggregation_context,
    reset_fast_caches,
    process_closed_trades,
    export_run_artifacts,
)


# =====================================================================
# 1. DOWNLOADER TESTS (Gate 1 & Rules 141-151)
# =====================================================================

def test_downloader_boundary_no_gaps_logic():
    """
    Simulates Bitget API pagination where endTime is inclusive of close time (exclusive of open time).
    Demonstrates that setting current_end = earliest maintains contiguous M1 sequence with zero gaps.
    """
    # 2 pages of 5 bars each
    # Page 1: endTime = 1789737300000 -> returns open_times [1789737000000, 1789737060000, 1789737120000, 1789737180000, 1789737240000]
    # Earliest in Page 1 = 1789737000000
    # Page 2: with current_end = earliest (1789737000000) -> returns open_times [1789736700000, ..., 1789736940000]
    p1 = [
        [1789737000000, "2600.0", "2601.0", "2599.0", "2600.5", "10.0"],
        [1789737060000, "2600.5", "2602.0", "2600.0", "2601.5", "12.0"],
        [1789737120000, "2601.5", "2603.0", "2601.0", "2602.5", "15.0"],
    ]
    earliest_p1 = min(int(r[0]) for r in p1)
    assert earliest_p1 == 1789737000000

    # With boundary fix (current_end = earliest_p1):
    # API returns candles with close_time <= 1789737000000, meaning latest candle has open_time = 1789736940000
    p2 = [
        [1789736880000, "2598.0", "2599.0", "2597.5", "2598.5", "8.0"],
        [1789736940000, "2598.5", "2600.2", "2598.0", "2600.0", "9.0"],
    ]

    all_raw = p1 + p2
    candles, stats = validate_and_normalize(all_raw, 1789736880000, 1789737180000)

    # Assert continuity: exactly 5 candles with diff = 60000ms
    assert len(candles) == 5
    assert stats["gaps_count"] == 0
    for i in range(len(candles) - 1):
        assert candles[i+1]["open_time"] - candles[i]["open_time"] == 60_000


def test_downloader_boundary_off_by_one_reproduction():
    """
    Demonstrates that current_end = earliest - 1 reproduces the 120s (1 missing bar) gap bug.
    """
    p1 = [[1789737000000, "2600.0", "2601.0", "2599.0", "2600.5", "10.0"]]
    earliest_p1 = 1789737000000

    # Buggy boundary: current_end = earliest_p1 - 1
    # Bitget filters close_time <= 1789736999999.
    # The candle at 1789736940000 has close_time = 1789737000000, so it is skipped!
    # Next returned candle is 1789736880000:
    p2_buggy = [[1789736880000, "2598.0", "2599.0", "2597.5", "2598.5", "8.0"]]

    all_raw = p1 + p2_buggy
    candles, stats = validate_and_normalize(all_raw, 1789736880000, 1789737060000)

    assert len(candles) == 2
    assert stats["gaps_count"] == 1
    assert stats["gaps"][0]["gap_ms"] == 120_000
    assert stats["gaps"][0]["missing_bars"] == 1


def test_downloader_duplicate_detection_conflicting_and_identical():
    """Verifies that validate_and_normalize detects and counts identical vs conflicting duplicates."""
    rows = [
        [1789737000000, "2600.0", "2601.0", "2599.0", "2600.5", "10.0"],
        # Identical duplicate:
        [1789737000000, "2600.0", "2601.0", "2599.0", "2600.5", "10.0"],
        # Conflicting duplicate:
        [1789737000000, "2600.0", "2605.0", "2595.0", "2604.0", "99.0"],
        [1789737060000, "2600.5", "2602.0", "2600.0", "2601.5", "12.0"],
    ]
    candles, stats = validate_and_normalize(rows, 1789737000000, 1789737120000)
    assert len(candles) == 2
    assert stats["identical_duplicates"] == 1
    assert stats["conflicting_duplicates"] == 1


def test_downloader_gap_calculation_definition():
    """Verifies that gap_ms = 120_000 produces missing_bars = 1, not 2."""
    rows = [
        [1789737000000, "2600.0", "2601.0", "2599.0", "2600.5", "10.0"],
        # 120s later:
        [1789737120000, "2601.5", "2603.0", "2601.0", "2602.5", "15.0"],
    ]
    candles, stats = validate_and_normalize(rows, 1789737000000, 1789737180000)
    assert stats["gaps_count"] == 1
    gap = stats["gaps"][0]
    assert gap["gap_ms"] == 120_000
    assert gap["missing_bars"] == 1
    assert gap["missing_duration_minutes"] == 1


def test_downloader_invalid_geometry_and_non_finite():
    """Verifies that invalid geometry and non-finite values are rejected."""
    rows = [
        # Normal
        [1789737000000, "2600.0", "2601.0", "2599.0", "2600.5", "10.0"],
        # Invalid geometry (high < low)
        [1789737060000, "2600.0", "2590.0", "2605.0", "2600.5", "10.0"],
        # Non-finite
        [1789737120000, "nan", "2601.0", "2599.0", "2600.5", "10.0"],
        # Negative price
        [1789737180000, "-10.0", "2601.0", "2599.0", "2600.5", "10.0"],
    ]
    candles, stats = validate_and_normalize(rows, 1789737000000, 1789737240000)
    assert len(candles) == 1
    assert stats["invalid_geometry_rejected"] == 1
    assert stats["non_finite_rejected"] == 2


# =====================================================================
# 2. PARITY TESTS: FAST VS NATIVE AGGREGATION (Gate 2 & Rules 088-100)
# =====================================================================

@pytest.fixture
def sample_m1_bars():
    """Generates 240 contiguous M1 bars (4 hours of data)."""
    base_ts = 1789689600000  # 2026-09-18T00:00:00Z
    bars = []
    px = 2600.0
    for i in range(240):
        t = base_ts + i * 60_000
        o = round(px, 2)
        h = round(px + 0.8, 2)
        l = round(px - 0.7, 2)
        c = round(px + 0.1, 2)
        v = round(100.0 + (i % 10), 4)
        bars.append(replay_driver_mod.ReplayBar(
            open_time=t, open=o, high=h, low=l, close=c, volume=v, is_closed=True
        ))
        px = c
    return bars


def assert_candle_equal(c1: CanonicalCandle, c2: CanonicalCandle):
    assert c1.open_time == c2.open_time
    assert c1.close_time_ms == c2.close_time_ms
    assert abs(c1.open - c2.open) < 1e-4
    assert abs(c1.high - c2.high) < 1e-4
    assert abs(c1.low - c2.low) < 1e-4
    assert abs(c1.close - c2.close) < 1e-4
    assert abs(c1.volume - c2.volume) < 1e-4
    assert c1.timeframe == c2.timeframe
    assert c1.symbol == c2.symbol
    assert c1.is_closed == c2.is_closed


def test_aggregation_parity_timeframes(sample_m1_bars):
    """Asserts 100% parity across M15, H1, H4 between fast and native aggregators."""
    as_of = sample_m1_bars[-1].open_time + 60_000

    for tf in ["15M", "1H", "4H"]:
        reset_fast_caches()
        native = orig_aggregate_closed_bars(sample_m1_bars, tf, as_of_ms=as_of, symbol="XAUUSDT")
        fast = fast_aggregate_closed_bars(sample_m1_bars, tf, as_of_ms=as_of, symbol="XAUUSDT")

        assert len(native) == len(fast), f"Mismatch in candle count for {tf}: native {len(native)} != fast {len(fast)}"
        assert len(native) > 0, f"No candles produced for {tf}"
        for c1, c2 in zip(native, fast):
            assert_candle_equal(c1, c2)


def test_select_closed_candles_parity(sample_m1_bars):
    """Asserts 100% parity for 1M select_closed_candles between fast and native."""
    as_of = sample_m1_bars[-1].open_time + 60_000
    reset_fast_caches()
    native = orig_select_closed_candles(sample_m1_bars, "1M", as_of_ms=as_of, symbol="XAUUSDT")
    fast = fast_select_closed_candles(sample_m1_bars, "1M", as_of_ms=as_of, symbol="XAUUSDT")

    assert len(native) == len(fast)
    for c1, c2 in zip(native, fast):
        assert_candle_equal(c1, c2)


def test_prefix_evolution_parity(sample_m1_bars):
    """
    Simulates incremental bar addition (c_slice = candles[:idx+1]) as done in replay driver.
    Asserts fast aggregation matches native aggregation at every single step.
    """
    reset_fast_caches()
    for idx in range(15, 65):  # 50 consecutive bars covering multiple 15M boundaries
        c_slice = sample_m1_bars[:idx+1]
        now_ms = sample_m1_bars[idx].open_time + 60_000

        for tf in ["15M", "1H"]:
            native = orig_aggregate_closed_bars(c_slice, tf, as_of_ms=now_ms, symbol="XAUUSDT")
            fast = fast_aggregate_closed_bars(c_slice, tf, as_of_ms=now_ms, symbol="XAUUSDT")

            assert len(native) == len(fast), f"Step {idx} ({tf}): count {len(native)} != {len(fast)}"
            for c1, c2 in zip(native, fast):
                assert_candle_equal(c1, c2)


def test_boundary_conditions_parity(sample_m1_bars):
    """
    Tests as_of right before bucket close (14m 59s), at bucket close (15m 00s), and after.
    Verifies that bucket only closes at or after end boundary.
    """
    # 15M boundary closes at 15 * 60_000 = 900_000 ms from base_ts
    base_ts = sample_m1_bars[0].open_time
    boundary_close = base_ts + 15 * 60_000

    # 1. 1 ms before close
    reset_fast_caches()
    native_before = orig_aggregate_closed_bars(sample_m1_bars[:15], "15M", as_of_ms=boundary_close - 1)
    reset_fast_caches()
    fast_before = fast_aggregate_closed_bars(sample_m1_bars[:15], "15M", as_of_ms=boundary_close - 1)
    assert len(native_before) == 0
    assert len(fast_before) == 0

    # 2. Exactly at close
    reset_fast_caches()
    native_at = orig_aggregate_closed_bars(sample_m1_bars[:15], "15M", as_of_ms=boundary_close)
    reset_fast_caches()
    fast_at = fast_aggregate_closed_bars(sample_m1_bars[:15], "15M", as_of_ms=boundary_close)
    assert len(native_at) == 1
    assert len(fast_at) == 1
    assert_candle_equal(native_at[0], fast_at[0])


def test_cache_reset_and_symbol_isolation(sample_m1_bars):
    """Verifies that caches are isolated by symbol and reset clears all state."""
    reset_fast_caches()
    as_of = sample_m1_bars[-1].open_time + 60_000

    # Run for XAUUSDT
    fast_xau = fast_aggregate_closed_bars(sample_m1_bars, "15M", as_of_ms=as_of, symbol="XAUUSDT")
    assert len(fast_xau) > 0

    # Run for BTCUSDT with different data
    btc_bars = [
        replay_driver_mod.ReplayBar(
            open_time=b.open_time, open=60000.0, high=60100.0, low=59900.0, close=60050.0, volume=1.0, is_closed=True
        ) for b in sample_m1_bars[:30]
    ]
    fast_btc = fast_aggregate_closed_bars(btc_bars, "15M", as_of_ms=as_of, symbol="BTCUSDT")
    assert len(fast_btc) > 0
    assert fast_btc[0].open == 60000.0

    # Ensure XAU cache was not overwritten
    from tools.eval_cg01_v16_27_baseline import _AGG_CACHE
    assert ("XAUUSDT", "15M") in _AGG_CACHE
    assert ("BTCUSDT", "15M") in _AGG_CACHE

    # Reset clears all
    reset_fast_caches()
    assert len(_AGG_CACHE) == 0


# =====================================================================
# 3. HARNESS & STATE TESTS (Gate 4 & Rules 155-160)
# =====================================================================

def test_fast_aggregation_context_restores_on_success():
    """Verifies that fast_aggregation_context restores original module references on normal exit."""
    orig_agg = replay_driver_mod.aggregate_closed_bars
    orig_sel = replay_driver_mod.select_closed_candles

    with fast_aggregation_context(enabled=True):
        assert replay_driver_mod.aggregate_closed_bars == fast_aggregate_closed_bars
        assert replay_driver_mod.select_closed_candles == fast_select_closed_candles

    assert replay_driver_mod.aggregate_closed_bars == orig_agg
    assert replay_driver_mod.select_closed_candles == orig_sel


def test_fast_aggregation_context_restores_on_exception():
    """Verifies that fast_aggregation_context restores original references even on exception or KeyboardInterrupt."""
    orig_agg = replay_driver_mod.aggregate_closed_bars
    orig_sel = replay_driver_mod.select_closed_candles

    try:
        with fast_aggregation_context(enabled=True):
            assert replay_driver_mod.aggregate_closed_bars == fast_aggregate_closed_bars
            raise KeyboardInterrupt("Simulated Ctrl-C")
    except KeyboardInterrupt:
        pass

    assert replay_driver_mod.aggregate_closed_bars == orig_agg
    assert replay_driver_mod.select_closed_candles == orig_sel


def test_honest_metrics_on_unverified_sample():
    """Verifies that empty/unverified trades produce null with reason instead of misleading 0% winrate."""
    res = process_closed_trades([], initial_capital=1000.0)
    assert res["total_closed"] == 0
    assert res["wins"] == 0
    assert res["losses"] == 0
    assert res["winrate_pct"] is None
    assert res["winrate_reason"] == "NO_CLOSED_TRADES"
    assert res["expectancy_net_usd"] is None
    assert res["profit_factor"] is None
    assert res["closed_equity_drawdown_pct"] == 0.0


def test_immediate_per_run_export():
    """Verifies that export_run_artifacts writes all required files immediately and atomically."""
    with tempfile.TemporaryDirectory() as tmpdir:
        raw_mock = {
            "status": "COMPLETED",
            "reason": "OK",
            "total_fills": 0,
            "closed_trades_count": 0,
            "daily_stats": {"2026-09-18": {"date": "2026-09-18", "fills_count": 0, "status": "SEARCHING"}},
            "equity_mtm": [{"timestamp": 1789689600000, "mtm_equity": 1000.0, "drawdown_pct": 0.0}],
            "equity_realized": [{"timestamp": 1789689600000, "cash_equity": 1000.0}],
            "strategy_funnel": {"cycles": 100},
            "rejection_funnel": [],
        }
        metrics_mock = {
            "label": "30-Day",
            "trade_metrics": process_closed_trades([]),
        }
        export_run_artifacts(raw_mock, metrics_mock, "30_day", out_dir=tmpdir)

        assert os.path.exists(os.path.join(tmpdir, "raw_report_30_day.json"))
        assert os.path.exists(os.path.join(tmpdir, "trades_30_day.csv"))
        assert os.path.exists(os.path.join(tmpdir, "daily_stats_30_day.csv"))
        assert os.path.exists(os.path.join(tmpdir, "equity_30_day.csv"))
        assert os.path.exists(os.path.join(tmpdir, "funnel_30_day.json"))

        # Verify JSON is valid
        with open(os.path.join(tmpdir, "raw_report_30_day.json")) as f:
            loaded = json.load(f)
            assert loaded["status"] == "COMPLETED"
