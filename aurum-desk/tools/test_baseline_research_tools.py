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

import csv
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
    download_m1_candles,
    validate_and_normalize,
    compute_logical_sha256,
    CADENCE_MS,
)
from tools.eval_cg01_v16_27_baseline import (
    fast_aggregate_closed_bars,
    fast_select_closed_candles,
    fast_aggregation_context,
    reset_fast_caches,
    normalize_replay_closed_trade,
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
# 1B. GENUINE DOWNLOADER LOOP & PAGINATION TESTS (Part B & Gate 2)
# Directly invokes download_m1_candles() with injected FakeBitgetClient.
# =====================================================================

class FakeBitgetResponse:
    def __init__(self, status_code: int = 200, payload: Dict[str, Any] = None):
        self.status_code = status_code
        self._payload = payload or {"code": "00000", "msg": "success", "data": []}

    def json(self):
        return self._payload


class FakeBitgetClient:
    def __init__(self, handler=None):
        self.handler = handler
        self.calls: List[Dict[str, Any]] = []
        self.is_closed = False

    def get(self, url: str, params: Dict[str, Any] = None):
        self.calls.append({"url": url, "params": dict(params or {})})
        if self.handler:
            return self.handler(url, params)
        return FakeBitgetResponse(200, {"code": "00000", "msg": "success", "data": []})

    def close(self):
        self.is_closed = True


def test_downloader_genuine_pagination_three_pages_traversal(monkeypatch):
    """
    Directly invokes download_m1_candles() with an injected FakeBitgetClient across 3 pages.
    Simulates Bitget close-time semantics (close_time <= endTime).
    Verifies that endTime decrements correctly on each iteration and reaches COMPLETE status.
    """
    monkeypatch.setattr("tools.download_v16_27_m1_dataset.time.sleep", lambda s: None)

    start_ms = 1789736700000  # Target start (t=0)
    end_ms = 1789737000000    # Target end_exclusive (t=300000, 5 mins)

    # 3 pages:
    # Page 1: endTime = 1789737000000 -> returns candles with open_time [1789736880000, 1789736940000]
    # Page 2: endTime = 1789736880000 -> returns candles with open_time [1789736760000, 1789736820000]
    # Page 3: endTime = 1789736760000 -> returns candle with open_time [1789736700000] (earliest <= start_ms)
    def mock_get(url: str, params: Dict[str, Any]):
        end_time = params["endTime"]
        if end_time == 1789737000000:
            return FakeBitgetResponse(200, {
                "code": "00000",
                "data": [
                    [1789736880000, "2600.0", "2601.0", "2599.0", "2600.5", "10.0"],
                    [1789736940000, "2600.5", "2602.0", "2600.0", "2601.5", "12.0"],
                ]
            })
        elif end_time == 1789736880000:
            return FakeBitgetResponse(200, {
                "code": "00000",
                "data": [
                    [1789736760000, "2598.0", "2599.0", "2597.5", "2598.5", "8.0"],
                    [1789736820000, "2598.5", "2600.0", "2598.0", "2600.0", "9.0"],
                ]
            })
        elif end_time == 1789736760000:
            return FakeBitgetResponse(200, {
                "code": "00000",
                "data": [
                    [1789736700000, "2597.0", "2598.0", "2596.5", "2597.5", "7.0"],
                ]
            })
        return FakeBitgetResponse(200, {"code": "00000", "data": []})

    client = FakeBitgetClient(handler=mock_get)
    raw_rows, status = download_m1_candles(start_ms, end_ms, client=client)

    assert status == "COMPLETE"
    assert len(client.calls) == 3
    assert client.calls[0]["params"]["endTime"] == 1789737000000
    assert client.calls[1]["params"]["endTime"] == 1789736880000
    assert client.calls[2]["params"]["endTime"] == 1789736760000
    assert len(raw_rows) == 5

    # Validate that raw_rows produce contiguous sequence
    candles, stats = validate_and_normalize(raw_rows, start_ms, end_ms)
    assert len(candles) == 5
    assert stats["gaps_count"] == 0
    assert client.is_closed is False  # Injected client should not be closed


def test_downloader_genuine_no_progress_termination(monkeypatch):
    """
    Verifies that if API returns earliest >= current_end, download_m1_candles terminates immediately
    with PARTIAL status rather than entering an infinite loop.
    """
    monkeypatch.setattr("tools.download_v16_27_m1_dataset.time.sleep", lambda s: None)

    def stuck_get(url: str, params: Dict[str, Any]):
        return FakeBitgetResponse(200, {
            "code": "00000",
            # Earliest returned is 1789737000000 which is >= current_end
            "data": [[1789737000000, "2600.0", "2601.0", "2599.0", "2600.5", "10.0"]]
        })

    client = FakeBitgetClient(handler=stuck_get)
    raw_rows, status = download_m1_candles(1789736000000, 1789737000000, client=client)

    assert status == "PARTIAL"
    assert len(client.calls) == 1
    assert len(raw_rows) == 1


def test_downloader_genuine_http_failure_first_page(monkeypatch):
    """Verifies that HTTP 500 across all 4 retries on first page results in FAILED status."""
    monkeypatch.setattr("tools.download_v16_27_m1_dataset.time.sleep", lambda s: None)

    client = FakeBitgetClient(handler=lambda u, p: FakeBitgetResponse(500, {}))
    raw_rows, status = download_m1_candles(1789736000000, 1789737000000, client=client)

    assert status == "FAILED"
    assert len(raw_rows) == 0
    assert len(client.calls) == 4  # 4 retry attempts


def test_downloader_genuine_http_failure_subsequent_page(monkeypatch):
    """Verifies that failure on page 2 after successful page 1 results in PARTIAL status with data preserved."""
    monkeypatch.setattr("tools.download_v16_27_m1_dataset.time.sleep", lambda s: None)

    call_count = 0
    def mock_get(url: str, params: Dict[str, Any]):
        nonlocal call_count
        call_count += 1
        if params["endTime"] == 1789737000000:
            return FakeBitgetResponse(200, {
                "code": "00000",
                "data": [[1789736940000, "2600.0", "2601.0", "2599.0", "2600.5", "10.0"]]
            })
        return FakeBitgetResponse(500, {})

    client = FakeBitgetClient(handler=mock_get)
    raw_rows, status = download_m1_candles(1789736000000, 1789737000000, client=client)

    assert status == "PARTIAL"
    assert len(raw_rows) == 1
    assert len(client.calls) == 5  # 1 successful + 4 failed retries on page 2


def test_downloader_genuine_api_code_error(monkeypatch):
    """Verifies that HTTP 200 with code != '00000' is treated as request failure."""
    monkeypatch.setattr("tools.download_v16_27_m1_dataset.time.sleep", lambda s: None)

    client = FakeBitgetClient(handler=lambda u, p: FakeBitgetResponse(200, {"code": "40001", "msg": "Rate limit"}))
    raw_rows, status = download_m1_candles(1789736000000, 1789737000000, client=client)

    assert status == "FAILED"
    assert len(client.calls) == 4


def test_downloader_genuine_empty_data_early_termination(monkeypatch):
    """Verifies that empty data returned before reaching start boundary produces PARTIAL status."""
    monkeypatch.setattr("tools.download_v16_27_m1_dataset.time.sleep", lambda s: None)

    client = FakeBitgetClient(handler=lambda u, p: FakeBitgetResponse(200, {"code": "00000", "data": []}))
    raw_rows, status = download_m1_candles(1789736000000, 1789737000000, client=client)

    assert status == "PARTIAL"
    assert len(raw_rows) == 0


def test_downloader_genuine_max_pages_limit(monkeypatch):
    """Verifies that setting max_pages halts pagination early with PARTIAL status."""
    monkeypatch.setattr("tools.download_v16_27_m1_dataset.time.sleep", lambda s: None)

    def endless_pages(url: str, params: Dict[str, Any]):
        end = params["endTime"]
        return FakeBitgetResponse(200, {
            "code": "00000",
            "data": [[end - 60_000, "2600.0", "2601.0", "2599.0", "2600.5", "10.0"]]
        })

    client = FakeBitgetClient(handler=endless_pages)
    raw_rows, status = download_m1_candles(1789730000000, 1789737000000, client=client, max_pages=2)

    assert status == "PARTIAL"
    assert len(client.calls) == 2
    assert len(raw_rows) == 2


def test_downloader_genuine_duplicate_and_conflict_preservation(monkeypatch):
    """
    Verifies that download_m1_candles retains duplicate rows from overlapping pages
    so that validate_and_normalize can detect conflicting duplicates.
    """
    monkeypatch.setattr("tools.download_v16_27_m1_dataset.time.sleep", lambda s: None)

    def overlapping_get(url: str, params: Dict[str, Any]):
        end = params["endTime"]
        if end == 1789737000000:
            return FakeBitgetResponse(200, {
                "code": "00000",
                "data": [[1789736880000, "2600.0", "2601.0", "2599.0", "2600.5", "10.0"]]
            })
        elif end == 1789736880000:
            return FakeBitgetResponse(200, {
                "code": "00000",
                # Same timestamp 1789736880000 but conflicting OHLC values
                "data": [
                    [1789736880000, "2600.0", "2610.0", "2590.0", "2608.0", "50.0"],
                    [1789736700000, "2595.0", "2596.0", "2594.0", "2595.5", "8.0"],
                ]
            })
        return FakeBitgetResponse(200, {"code": "00000", "data": []})

    client = FakeBitgetClient(handler=overlapping_get)
    raw_rows, status = download_m1_candles(1789736700000, 1789737000000, client=client)

    assert status == "COMPLETE"
    assert len(raw_rows) == 3
    candles, stats = validate_and_normalize(raw_rows, 1789736700000, 1789737000000)
    assert stats["conflicting_duplicates"] == 1


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


# =====================================================================
# 5. CANONICAL REPLAY TRADE MAPPING & REPORTING TESTS (Part A & Gate 1)
# =====================================================================

def test_mapping_canonical_adapter_schema():
    """
    Verifies that normalize_replay_closed_trade and process_closed_trades consume
    the exact canonical schema produced by CG01ReplayExecutionAdapter.collect_state().
    Asserts:
    - trade_id is preserved intact
    - entry_time and exit_time map to ms and formatted UTC/VN/NY strings
    - duration_minutes is correctly calculated
    - net_pnl_usd is preserved without double-deduction of fees
    - initial_risk_usdt, realized_r, and intrabar_ambiguous are preserved
    - gross_pnl_usd and total_fee_usd remain None when authority is absent
    """
    adapter_fixture = [{
        "trade_id": "ORD_TEST_CANONICAL_01",
        "direction": "LONG",
        "entry_time": 1789737000000,
        "entry_price": 2650.50,
        "exit_time": 1789737300000,
        "exit_price": 2655.75,
        "outcome": "TP",
        "exit_reason": "TP_HIT",
        "quantity": 0.5,
        "net_pnl_usd": 2.625,
        "realized_r": 2.1,
        "initial_risk_usdt": 1.25,
        "intrabar_ambiguous": False,
    }]

    res = process_closed_trades(adapter_fixture, initial_capital=1000.0)

    assert res["total_closed"] == 1
    assert res["wins"] == 1
    assert res["losses"] == 0
    assert res["breakevens"] == 0
    assert res["winrate_pct"] == 100.0
    assert res["net_pnl_usd"] == 2.625
    assert res["expectancy_net_usd"] == 2.625
    assert res["expectancy_r"] == 2.1
    assert res["gross_pnl_usd"] is None  # Adapter provides net only; must remain None
    assert res["total_fees_usd"] is None # Authority absent; must remain None

    t0 = res["processed_trades"][0]
    assert t0["trade_id"] == "ORD_TEST_CANONICAL_01"
    assert t0["direction"] == "LONG"
    assert t0["entry_time_ms"] == 1789737000000
    assert t0["exit_time_ms"] == 1789737300000
    assert t0["duration_minutes"] == 5.0
    assert t0["timing_status"] == "VALID"
    assert t0["entry_price"] == 2650.50
    assert t0["exit_price"] == 2655.75
    assert t0["net_pnl_usd"] == 2.625
    assert t0["realized_r"] == 2.1
    assert t0["initial_risk_usdt"] == 1.25
    assert t0["exit_reason"] == "TP_HIT"
    assert t0["intrabar_ambiguous"] is False
    assert "2026" in t0["entry_time_utc"]


def test_mapping_long_win_short_loss_and_breakeven():
    """
    Verifies accounting across LONG win, SHORT loss, and zero-PnL breakeven.
    Ensures zero PnL is preserved (not falsy fallback) and loss sign is not inverted.
    """
    trades = [
        # Win
        {
            "trade_id": "T_WIN",
            "direction": "LONG",
            "entry_time": 1789737000000,
            "exit_time": 1789737180000,
            "entry_price": 2650.0,
            "exit_price": 2655.0,
            "net_pnl_usd": 15.0,
            "realized_r": 2.0,
            "initial_risk_usdt": 7.5,
        },
        # Loss
        {
            "trade_id": "T_LOSS",
            "direction": "SHORT",
            "entry_time": 1789737200000,
            "exit_time": 1789737300000,
            "entry_price": 2660.0,
            "exit_price": 2662.0,
            "net_pnl_usd": -10.0,
            "realized_r": -1.0,
            "initial_risk_usdt": 10.0,
        },
        # Breakeven
        {
            "trade_id": "T_BE",
            "direction": "LONG",
            "entry_time": 1789737400000,
            "exit_time": 1789737500000,
            "entry_price": 2650.0,
            "exit_price": 2650.0,
            "net_pnl_usd": 0.0,
            "realized_r": 0.0,
            "initial_risk_usdt": 5.0,
        },
    ]

    res = process_closed_trades(trades, initial_capital=1000.0)

    assert res["total_closed"] == 3
    assert res["wins"] == 1
    assert res["losses"] == 1
    assert res["breakevens"] == 1
    assert res["winrate_pct"] == 33.33
    assert res["net_pnl_usd"] == 5.0  # 15.0 - 10.0 + 0.0
    assert res["expectancy_net_usd"] == round(5.0 / 3, 4)
    assert res["long_trades"]["count"] == 2
    assert res["long_trades"]["wins"] == 1
    assert res["short_trades"]["count"] == 1
    assert res["short_trades"]["losses"] == 1
    assert res["short_trades"]["net_pnl_usd"] == -10.0


def test_mapping_canonical_precedence_over_legacy():
    """Verifies that canonical fields strictly take precedence over legacy fields when both are present."""
    mixed_trade = {
        "trade_id": "CANONICAL_ID",
        "order_id": "LEGACY_ORDER_ID",
        "id": "LEGACY_ID",
        "entry_time": 1789737000000,
        "opened_at": 1789736000000,
        "exit_time": 1789737300000,
        "closed_at": 1789738000000,
        "net_pnl_usd": 50.0,
        "realized_pnl_net": 999.0,
        "gross_pnl_usd": 55.0,
        "realized_pnl_gross": 999.0,
        "exit_reason": "CANONICAL_EXIT",
        "close_reason": "LEGACY_CLOSE",
    }

    norm = normalize_replay_closed_trade(mixed_trade)
    assert norm["trade_id"] == "CANONICAL_ID"
    assert norm["entry_time_ms"] == 1789737000000
    assert norm["exit_time_ms"] == 1789737300000
    assert norm["net_pnl_usd"] == 50.0
    assert norm["gross_pnl_usd"] == 55.0
    assert norm["exit_reason"] == "CANONICAL_EXIT"


def test_mapping_legacy_field_compatibility():
    """Verifies that legacy records without canonical keys still parse correctly."""
    legacy_trade = {
        "order_id": "LEGACY_001",
        "direction": "long",
        "opened_at": 1789737000000,
        "closed_at": 1789737180000,
        "entry_price": 2600.0,
        "exit_price": 2605.0,
        "realized_pnl_net": 12.5,
        "close_reason": "MANUAL",
    }

    norm = normalize_replay_closed_trade(legacy_trade)
    assert norm["trade_id"] == "LEGACY_001"
    assert norm["direction"] == "LONG"
    assert norm["entry_time_ms"] == 1789737000000
    assert norm["exit_time_ms"] == 1789737180000
    assert norm["duration_minutes"] == 3.0
    assert norm["net_pnl_usd"] == 12.5
    assert norm["exit_reason"] == "MANUAL"


def test_mapping_missing_pnl_authority_handling():
    """
    Verifies that trades lacking PnL authority are flagged as MISSING_PNL_AUTHORITY,
    recorded in invalid_trades_count, and NOT counted as breakevens.
    """
    invalid_trade = {
        "trade_id": "NO_PNL",
        "entry_time": 1789737000000,
        "exit_time": 1789737100000,
        # net_pnl_usd and realized_pnl_net are absent
    }

    norm = normalize_replay_closed_trade(invalid_trade)
    assert norm["pnl_status"] == "MISSING_PNL_AUTHORITY"
    assert norm["net_pnl_usd"] is None

    res = process_closed_trades([invalid_trade])
    assert res["invalid_trades_count"] == 1
    assert res["total_closed"] == 0
    assert res["wins"] == 0
    assert res["losses"] == 0
    assert res["breakevens"] == 0
    assert res["winrate_reason"] == "NO_VALID_TRADES_WITH_PNL_AUTHORITY"


def test_mapping_invalid_and_missing_timestamps():
    """Verifies that invalid timestamp ordering and missing timestamps are flagged without using abs()."""
    inverted_trade = {
        "trade_id": "INVERTED",
        "entry_time": 1789737300000,
        "exit_time": 1789737000000,  # Exit before entry!
        "net_pnl_usd": 5.0,
    }
    norm_inv = normalize_replay_closed_trade(inverted_trade)
    assert norm_inv["timing_status"] == "INVALID_TIMESTAMP_ORDERING"
    assert norm_inv["duration_minutes"] is None  # Must NOT be abs()

    missing_ts_trade = {
        "trade_id": "MISSING_TS",
        "entry_time": None,
        "exit_time": 1789737000000,
        "net_pnl_usd": 5.0,
    }
    norm_miss = normalize_replay_closed_trade(missing_ts_trade)
    assert norm_miss["timing_status"] == "MISSING_TIMESTAMPS"
    assert norm_miss["duration_minutes"] is None


def test_mapping_duplicate_trade_ids_detection():
    """Verifies that duplicate trade IDs in reporting input are recorded in duplicate_trade_ids."""
    trades = [
        {"trade_id": "DUP_A", "entry_time": 1000, "exit_time": 2000, "net_pnl_usd": 1.0},
        {"trade_id": "DUP_A", "entry_time": 3000, "exit_time": 4000, "net_pnl_usd": 2.0},
        {"trade_id": "UNIQUE_B", "entry_time": 5000, "exit_time": 6000, "net_pnl_usd": 3.0},
    ]
    res = process_closed_trades(trades)
    assert "DUP_A" in res["duplicate_trade_ids"]
    assert "UNIQUE_B" not in res["duplicate_trade_ids"]


def test_mapping_closed_equity_drawdown_chronology_sorting():
    """
    Verifies that closed equity drawdown sorts trades by exit_time_ms rather than
    relying on input list ordering.
    """
    # Out of order trades:
    # Trade 1: exit at t=3, loss -50
    # Trade 2: exit at t=1, win +100 (peak = 1100)
    # Trade 3: exit at t=2, loss -20 (equity = 1080)
    # Chronological equity curve from $1000:
    # t=1: 1100 (peak 1100, DD = 0%)
    # t=2: 1080 (peak 1100, DD = (20/1100)*100 = 1.82%)
    # t=3: 1030 (peak 1100, DD = (70/1100)*100 = 6.36%) -> max DD = 6.36%
    unordered_trades = [
        {"trade_id": "T3", "entry_time": 100, "exit_time": 3000, "net_pnl_usd": -50.0},
        {"trade_id": "T1", "entry_time": 100, "exit_time": 1000, "net_pnl_usd": 100.0},
        {"trade_id": "T2", "entry_time": 100, "exit_time": 2000, "net_pnl_usd": -20.0},
    ]

    res = process_closed_trades(unordered_trades, initial_capital=1000.0)
    assert res["closed_equity_drawdown_pct"] == 6.36
    assert res["drawdown_chronology_valid"] is True


def test_mapping_csv_export_content_and_null_formatting():
    """
    Verifies that export_run_artifacts writes trades.csv containing canonical trade fields,
    and formats missing fields as empty CSV cells rather than misleading '0' values.
    """
    canonical_trade = {
        "trade_id": "ORD_CANON_CSV_99",
        "direction": "SHORT",
        "entry_time": 1789737000000,
        "entry_price": 2680.25,
        "exit_time": 1789737240000,
        "exit_price": 2675.50,
        "exit_reason": "TP_HIT",
        "quantity": 1.0,
        "net_pnl_usd": 4.75,
        "realized_r": 1.9,
        "initial_risk_usdt": 2.50,
        # Notice: gross_pnl_usd and total_fee_usd are intentionally absent!
    }

    metrics = {
        "label": "Test",
        "trade_metrics": process_closed_trades([canonical_trade]),
    }
    raw_mock = {"status": "COMPLETED", "daily_stats": {}, "equity_mtm": [], "equity_realized": []}

    with tempfile.TemporaryDirectory() as tmpdir:
        export_run_artifacts(raw_mock, metrics, "test_csv", out_dir=tmpdir)
        csv_path = os.path.join(tmpdir, "trades_test_csv.csv")
        assert os.path.exists(csv_path)

        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))

        assert len(reader) == 1
        row = reader[0]
        assert row["trade_id"] == "ORD_CANON_CSV_99"
        assert row["direction"] == "SHORT"
        assert row["entry_time_ms"] == "1789737000000"
        assert row["exit_time_ms"] == "1789737240000"
        assert row["duration_minutes"] == "4.0"
        assert row["net_pnl_usd"] == "4.75"
        assert row["realized_r"] == "1.9"
        assert row["initial_risk_usdt"] == "2.5"
        assert row["timing_status"] == "VALID"
        # Crucial: missing gross_pnl and total_fee must be empty string "", NOT "0" or "0.0"!
        assert row["gross_pnl_usd"] == ""
        assert row["total_fee_usd"] == ""
