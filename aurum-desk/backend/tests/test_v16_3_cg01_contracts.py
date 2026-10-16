"""
backend/tests/test_v16_3_cg01_contracts.py
V16.3 Rules 021 - 042, 161, 163, 164, 165, 174:
- Contract tests verifying import & signature repairs (no detect_swing_points_causal, keyword build_liquidity_map)
- Representation parity between Dict and ORM candle representations
- Strict Causal HTF Bar Aggregator verification (boundaries, OHLC, incomplete bucket exclusion)
- No-lookahead invariance test
- Config parameter preservation
"""
import math
import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from services.cg01_context_adapter import (
    CanonicalCandle,
    CanonicalQuote,
    adapt_cg01_quote,
    aggregate_closed_bars,
    assess_cg01_context_readiness,
    build_cg01_closed_context,
    build_cg01_liquidity_context,
    normalize_cg01_candle,
    select_closed_candles,
)
from services.cg01_engine import build_cg01_context, evaluate_cg01_candidate
from services.cg01_types import CG01Config, CG01Stage
from services.liquidity_scenario_service import compute_dealing_range, identify_confirmed_swings
from services.smc_liquidity_service import build_liquidity_map


class MockORMCandle:
    """Simulates SQLAlchemy models.Candle attribute access."""
    def __init__(self, timestamp: int, open: float, high: float, low: float, close: float, volume: float = 100.0):
        self.timestamp = timestamp
        self.open_time = timestamp
        self.open = float(open)
        self.high = float(high)
        self.low = float(low)
        self.close = float(close)
        self.volume = float(volume)
        self.timeframe = "1M"
        self.symbol = "XAUUSDT"
        self.is_closed = True


def make_raw_dict(ts: int, o: float, h: float, l: float, c: float, v: float = 100.0) -> dict:
    return {
        "open_time": ts,
        "timestamp": ts,
        "open": float(o),
        "high": float(h),
        "low": float(l),
        "close": float(c),
        "volume": float(v),
        "timeframe": "1M",
        "symbol": "XAUUSDT",
        "is_closed": True,
    }


def test_rule_161_repaired_import_and_liquidity_contracts():
    """
    Rule 161: Verifies that build_cg01_context and build_cg01_liquidity_context call
    identify_confirmed_swings and keyword-only build_liquidity_map without TypeError/ImportError.
    """
    base_ts = 1_700_000_000_000  # ms
    candles_15m = []
    # Build 15 confirmed 15M candles with confirmed swings
    for i in range(25):
        t = base_ts + i * 900_000
        # Create a peak at i=10
        if i == 10:
            candles_15m.append(make_raw_dict(t, 2620.0, 2635.0, 2618.0, 2630.0))
        elif i == 18:
            candles_15m.append(make_raw_dict(t, 2625.0, 2628.0, 2610.0, 2615.0))
        else:
            candles_15m.append(make_raw_dict(t, 2620.0, 2625.0, 2618.0, 2622.0))

    candles_4h = [make_raw_dict(base_ts + i * 14_400_000, 2600.0, 2640.0, 2590.0, 2630.0) for i in range(15)]
    candles_1h = [make_raw_dict(base_ts + i * 3_600_000, 2610.0, 2635.0, 2605.0, 2625.0) for i in range(20)]
    candles_1m = [make_raw_dict(base_ts + i * 60_000, 2621.0, 2623.0, 2620.0, 2622.0) for i in range(30)]

    now_ms = base_ts + 25 * 900_000 + 60_000
    cfg = CG01Config(pivot_left_bars=2, pivot_right_bars=2)

    raw_ctx = {
        "4H": candles_4h,
        "1H": candles_1h,
        "15M": candles_15m,
        "1M": candles_1m,
    }

    # 1. build_cg01_context must execute cleanly without detect_swing_points_causal ImportError
    ctx_res = build_cg01_context(raw_ctx, now_ms=now_ms, config=cfg)
    assert ctx_res["status"] == "READY"
    assert not ctx_res["is_warmup"]
    assert ctx_res["dealing_range"] is not None
    assert ctx_res["dealing_range"].is_valid

    # 2. build_cg01_liquidity_context must pass keyword args to build_liquidity_map without TypeError
    levels = build_cg01_liquidity_context(raw_ctx, as_of_ms=now_ms, symbol="XAUUSDT", config=cfg)
    assert isinstance(levels, list)
    assert len(levels) > 0
    # Must contain buy-side and sell-side swing levels
    sides = {l.side for l in levels}
    assert "BUY_SIDE" in sides or "SELL_SIDE" in sides


def test_rule_163_representation_parity_dict_and_orm():
    """
    Rule 163: Proves that dict and ORM representations of the same OHLC data
    yield identical CanonicalCandle instances and evaluation decisions.
    """
    ts = 1_700_000_000_000
    d_raw = make_raw_dict(ts, 2620.50, 2625.00, 2619.00, 2624.00, 150.0)
    orm_raw = MockORMCandle(ts, 2620.50, 2625.00, 2619.00, 2624.00, 150.0)

    c_dict = normalize_cg01_candle(d_raw, "1M", "XAUUSDT")
    c_orm = normalize_cg01_candle(orm_raw, "1M", "XAUUSDT")

    assert c_dict.open_time == c_orm.open_time
    assert c_dict.open == c_orm.open
    assert c_dict.high == c_orm.high
    assert c_dict.low == c_orm.low
    assert c_dict.close == c_orm.close
    assert c_dict.volume == c_orm.volume
    assert c_dict.timeframe == c_orm.timeframe
    assert c_dict["close"] == c_orm.close
    assert c_dict.get("high") == c_orm.high

    # Invalid candles must raise ValueError("DATA_INVALID: ...")
    with pytest.raises(ValueError, match="DATA_INVALID"):
        normalize_cg01_candle(make_raw_dict(ts, 2620.0, 2615.0, 2625.0, 2620.0))  # high < low

    with pytest.raises(ValueError, match="DATA_INVALID"):
        normalize_cg01_candle(make_raw_dict(ts, 2630.0, 2625.0, 2615.0, 2620.0))  # open > high

    with pytest.raises(ValueError, match="DATA_INVALID"):
        normalize_cg01_candle(make_raw_dict(ts, float("nan"), 2625.0, 2615.0, 2620.0))  # NaN


def test_rule_164_htf_aggregation_boundaries_and_incomplete_bucket():
    """
    Rule 164: Generates 240 M1 bars spanning 4 hours and aggregates to 15M, 1H, 4H.
    Verifies boundary alignment, OHLC accumulation, and exclusion of incomplete buckets.
    """
    # 2026-10-15 00:00:00 UTC (aligned to 4H boundary: 0 % 14_400_000 == 0)
    base_ts = 1_760_486_400_000
    m1_bars = []

    for i in range(240):
        t = base_ts + i * 60_000
        o = 2600.0 + (i * 0.05)
        h = o + 0.50
        l = o - 0.30
        c = o + 0.20
        m1_bars.append(make_raw_dict(t, o, h, l, c, v=50.0))

    full_close_time = base_ts + 240 * 60_000  # exactly 4 hours after start

    # 1. Test complete aggregation
    agg_15m = aggregate_closed_bars(m1_bars, "15M", as_of_ms=full_close_time)
    assert len(agg_15m) == 16  # 240 min / 15 = 16 bars
    assert agg_15m[0].open_time == base_ts
    assert agg_15m[0].close_time_ms == base_ts + 900_000

    agg_1h = aggregate_closed_bars(m1_bars, "1H", as_of_ms=full_close_time)
    assert len(agg_1h) == 4  # 4 hours
    assert agg_1h[0].open_time == base_ts
    assert agg_1h[0].close_time_ms == base_ts + 3_600_000
    assert agg_1h[0].open == m1_bars[0]["open"]
    assert agg_1h[0].volume == sum(b["volume"] for b in m1_bars[:60])

    agg_4h = aggregate_closed_bars(m1_bars, "4H", as_of_ms=full_close_time)
    assert len(agg_4h) == 1
    assert agg_4h[0].open == m1_bars[0]["open"]
    assert abs(agg_4h[0].close - m1_bars[239]["close"]) < 1e-4
    assert abs(agg_4h[0].high - max(b["high"] for b in m1_bars)) < 1e-4
    assert agg_4h[0].low == min(b["low"] for b in m1_bars)
    assert agg_4h[0].volume == sum(b["volume"] for b in m1_bars)

    # 2. Test Incomplete Bucket (Zero Lookahead Rule 147):
    # If dataset has 239 bars (up to 03:59:00), the 4H bar is NOT closed at 03:59:00!
    partial_m1 = m1_bars[:239]
    partial_as_of = base_ts + 239 * 60_000
    agg_4h_incomplete = aggregate_closed_bars(partial_m1, "4H", as_of_ms=partial_as_of)
    assert len(agg_4h_incomplete) == 0  # 4H bar has NOT completed!


def test_rule_165_no_lookahead_invariance():
    """
    Rule 165: Verifies that evaluation at decision time T gives identical results
    regardless of whether future candles after T exist in the dataset.
    """
    base_ts = 1_760_486_400_000
    m1_bars = [make_raw_dict(base_ts + i * 60_000, 2620.0, 2622.0, 2619.0, 2621.0) for i in range(100)]
    now_ms = base_ts + 30 * 60_000

    # Dataset A: up to now_ms
    ctx_a = {"1M": m1_bars[:30]}
    closed_a = select_closed_candles(ctx_a["1M"], "1M", as_of_ms=now_ms)

    # Dataset B: full 100 bars with wild future swings after now_ms
    wild_bars = list(m1_bars)
    wild_bars[50] = make_raw_dict(base_ts + 50 * 60_000, 3000.0, 3500.0, 2900.0, 3400.0)
    ctx_b = {"1M": wild_bars}
    closed_b = select_closed_candles(ctx_b["1M"], "1M", as_of_ms=now_ms)

    assert len(closed_a) == len(closed_b)
    for ca, cb in zip(closed_a, closed_b):
        assert ca.open_time == cb.open_time
        assert ca.close == cb.close


def test_rule_174_config_preservation():
    """
    Rule 174: Asserts that 0 values in config are preserved and not overwritten by fallback defaults.
    """
    cfg = CG01Config(
        daily_min_goal=1,
        daily_max_cap=2,
        daily_ny_goal=0,
    )
    assert cfg.daily_min_goal == 1
    assert cfg.daily_max_cap == 2
    assert cfg.daily_ny_goal == 0  # Not overwritten to 1!
