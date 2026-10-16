"""
backend/tests/test_v16_2_cg01_long_short.py
V16.2 Rules 120, 184, 185:
- Positive LONG flow with full CG-01 evidence chain
- Positive SHORT flow with full CG-01 evidence chain (asserting TP < entry < SL)
- Numeric Risk Regression test (ensuring estimated SL loss respects budget after fees)
"""
import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from domain_calculator import CostAssumptions, calculate_risk_reward, validate_price_geometry
from services.cg01_engine import (
    compute_zone_ce,
    detect_cg01_sweep,
    evaluate_cg01_candidate,
    resolve_cg01_mss_and_displacement,
    resolve_cg01_time_context,
    select_cg01_leg_fvg,
)
from services.cg01_types import CG01Config, CG01Stage
from services.scalp_m1_types import WatchedZone


def make_candle(open_time, o, h, l, c, v=100.0):
    return {
        "open_time": open_time,
        "timestamp": open_time,
        "open": float(o),
        "high": float(h),
        "low": float(l),
        "close": float(c),
        "volume": float(v),
        "is_closed": True,
    }


class LiquidityStub:
    def __init__(self, id, price, known_at_ms, side="SELL_SIDE", status="CONFIRMED"):
        self.id = id
        self.level_id = id
        self.price = float(price)
        self.known_at_ms = known_at_ms
        self.side = side
        self.status = status
        self.source_level_id = id
        self.label_vi = f"Mục tiêu {price}"


def test_rule_120_risk_regression_numeric():
    """
    Rule 016, 120: Budget $2.50 USD, stop distance ~0.35 near price 4166.
    Ensures estimated SL loss is bounded by the budget and does not explode to $27 USD.
    """
    costs = CostAssumptions(
        maker_fee=0.0002,
        taker_fee=0.0005,
        slippage_ticks=1,
        multiplier=1.0,
    )
    # Runner old bug did: qty = risk_amt / stop_dist without lot/step or fee checks
    # Here calculate_risk_reward authoritative calculation:
    calc = calculate_risk_reward(
        direction="LONG",
        entry=4166.35,
        sl=4166.00, # dist = 0.35
        tp=4167.50, # dist = 1.15
        capital=1000.0,
        risk_pct=0.25, # 0.25% of 1000 = $2.50 budget
        costs=costs,
        min_net_rr=2.0,
    )

    assert calc.is_valid
    assert calc.budget_usdt == 2.50
    # Net risk must be close to or strictly bounded by budget, NOT $27!
    assert calc.net_risk_usdt <= 2.51
    assert calc.net_risk_usdt > 0.0
    assert calc.quantity > 0.0
    print(f"Calculated qty={calc.quantity}, net_risk={calc.net_risk_usdt}")


def test_rule_184_cg01_positive_long_flow():
    """
    Rule 184: Positive LONG scenario:
    1. London Silver Bullet window (03:30 NY)
    2. Bullish POI on 15M: [2620, 2625]
    3. Sell-side liquidity sweep at 2622.0 -> low reaches 2619.5, closes back at 2622.5
    4. Confirmed Swing High formed at 2623.5 (pivot left=2, right=2)
    5. MSS Break candle closes at 2625.5 (> 2623.5) with strong displacement
    6. Leg FVG created: [2623.0, 2624.5], CE = 2623.75
    7. Structural SL at sweep extreme 2619.5 - buffer (2619.45)
    8. Valid target at 2635.0
    """
    # 2026-10-15 03:30 NY time
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)

    cfg = CG01Config(
        enabled=True,
        profile_mode="PAPER_EXECUTION",
        displacement_body_atr_min=0.8,
        min_net_rr=2.0,
        sl_buffer_ticks=5,
        tick_size=0.01,
    )

    # 1. Verify Timing Window
    time_ctx = resolve_cg01_time_context(t_start, cfg)
    assert time_ctx.is_in_window
    assert time_ctx.active_window_id == "LONDON_SILVER_BULLET"

    # 2. Build 1M candles for the sequence
    candles_1m = []
    # Pre-sweep: bars 0..4 (establish swing high at bar 2: 2623.5)
    candles_1m.append(make_candle(t_start, 2621.0, 2622.0, 2620.8, 2621.5))       # 0
    candles_1m.append(make_candle(t_start + 60_000, 2621.5, 2622.8, 2621.0, 2622.5))# 1
    candles_1m.append(make_candle(t_start + 120_000, 2622.5, 2623.5, 2622.0, 2623.0))# 2: SH High 2623.5
    candles_1m.append(make_candle(t_start + 180_000, 2623.0, 2623.0, 2621.5, 2622.2))# 3
    candles_1m.append(make_candle(t_start + 240_000, 2622.2, 2622.5, 2621.0, 2621.8))# 4: confirms SH (left=2, right=2)

    # Sweep bar (bar 5): sweeps reference 2621.0 down to 2619.5, closes at 2621.5 (reclaim)
    candles_1m.append(make_candle(t_start + 300_000, 2621.8, 2622.0, 2619.5, 2621.5))# 5: sweep & reclaim

    # Break leg candles: bars 6, 7, 8
    # bar 6: c1 of FVG
    candles_1m.append(make_candle(t_start + 360_000, 2621.5, 2623.0, 2621.2, 2622.8))# 6: high 2623.0
    # bar 7: big bullish displacement candle breaking 2623.5
    candles_1m.append(make_candle(t_start + 420_000, 2622.8, 2625.5, 2622.5, 2625.2))# 7: open 2622.8, close 2625.2 (body 2.4 > 0.8*1.0)
    # bar 8: c3 of FVG
    candles_1m.append(make_candle(t_start + 480_000, 2625.2, 2626.0, 2624.5, 2625.8))# 8: low 2624.5

    now_ms = t_start + 540_000

    poi = WatchedZone(
        zone_id="poi_15m_bullish",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BULLISH",
        top=2625.0,
        bottom=2620.0,
        created_at=t_start - 3600_000,
        known_at=t_start - 1800_000,
        touch_count=0,
    )

    sell_side_ref = LiquidityStub(id="lvl_ssl", price=2621.0, known_at_ms=t_start - 1800_000, side="SELL_SIDE")

    # 3. Detect Sweep
    sweep = detect_cg01_sweep(candles_1m, [sell_side_ref], poi, direction="LONG", now_ms=now_ms, config=cfg)
    assert sweep is not None
    assert sweep["sweep_extreme"] == 2619.5
    assert sweep["reference_price"] == 2621.0

    # 4. Detect MSS & Displacement
    mss = resolve_cg01_mss_and_displacement(candles_1m, sweep, direction="LONG", now_ms=now_ms, config=cfg, atr_1m=1.0)
    assert mss is not None
    assert mss["structure_ref_price"] == 2623.5
    assert mss["break_close"] >= 2625.0
    assert mss["displacement_body"] >= 0.8

    # 5. Detect Leg FVG
    fvg = select_cg01_leg_fvg(candles_1m, mss, direction="LONG", now_ms=now_ms, config=cfg)
    assert fvg is not None
    assert fvg["lower"] == 2623.0
    assert fvg["upper"] == 2624.5
    expected_ce = compute_zone_ce(2623.0, 2624.5, tick_size=0.01)
    assert fvg["ce_price"] == expected_ce
    assert fvg["ce_price"] == 2623.75

    # 6. Sizing & Geometry
    target_p = 2640.0
    sl_p = round(2619.5 - (cfg.sl_buffer_ticks * cfg.tick_size), 2)
    assert sl_p == 2619.45
    assert sl_p < expected_ce < target_p

    geom_ok, _ = validate_price_geometry("LONG", expected_ce, sl_p, target_p)
    assert geom_ok

    calc = calculate_risk_reward(
        direction="LONG",
        entry=expected_ce,
        sl=sl_p,
        tp=target_p,
        capital=1000.0,
        risk_pct=0.25,
        min_net_rr=2.0,
    )
    assert calc.is_valid
    assert calc.net_rr >= 2.0


def test_rule_185_cg01_positive_short_flow():
    """
    Rule 185: Positive SHORT scenario:
    1. NY AM Silver Bullet window (10:15 NY)
    2. Bearish POI on 15M: [2650, 2655]
    3. Buy-side liquidity sweep at 2654.0 -> high reaches 2656.0, closes back at 2653.5
    4. Confirmed Swing Low formed at 2652.0 (left=2, right=2)
    5. MSS Break candle closes at 2649.5 (< 2652.0) with strong bearish displacement
    6. Leg FVG created: [2650.5, 2652.0], CE = 2651.25
    7. Structural SL at sweep extreme 2656.0 + buffer (2656.05)
    8. Valid target at 2632.0
    9. Assert strict geometry: TP < entry < SL
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 10, 15, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)

    cfg = CG01Config(
        enabled=True,
        profile_mode="PAPER_EXECUTION",
        displacement_body_atr_min=0.8,
        min_net_rr=2.0,
        sl_buffer_ticks=5,
        tick_size=0.01,
    )

    # 1. Verify Timing Window
    time_ctx = resolve_cg01_time_context(t_start, cfg)
    assert time_ctx.is_in_window
    assert time_ctx.active_window_id == "NY_AM_SILVER_BULLET"

    # 2. Build 1M candles for SHORT sequence
    candles_1m = []
    # Pre-sweep: bars 0..4 (establish swing low at bar 2: 2652.0)
    candles_1m.append(make_candle(t_start, 2653.5, 2654.0, 2653.0, 2653.2))        # 0
    candles_1m.append(make_candle(t_start + 60_000, 2653.2, 2653.5, 2652.5, 2652.8)) # 1
    candles_1m.append(make_candle(t_start + 120_000, 2652.8, 2653.0, 2652.0, 2652.5))# 2: SL Low 2652.0
    candles_1m.append(make_candle(t_start + 180_000, 2652.5, 2653.5, 2652.4, 2653.2))# 3
    candles_1m.append(make_candle(t_start + 240_000, 2653.2, 2654.0, 2652.8, 2653.8))# 4: confirms SL (left=2, right=2)

    # Sweep bar (bar 5): sweeps reference 2654.0 up to 2656.0, closes at 2653.5 (reclaim)
    candles_1m.append(make_candle(t_start + 300_000, 2653.8, 2656.0, 2653.2, 2653.5))# 5: sweep & reclaim

    # Break leg candles: bars 6, 7, 8
    # bar 6: c1 of FVG
    candles_1m.append(make_candle(t_start + 360_000, 2653.5, 2653.8, 2652.0, 2652.2))# 6: low 2652.0
    # bar 7: big bearish displacement candle breaking 2652.0
    candles_1m.append(make_candle(t_start + 420_000, 2652.2, 2652.5, 2649.2, 2649.5))# 7: open 2652.2, close 2649.5 (body 2.7 > 0.8*1.0)
    # bar 8: c3 of FVG
    candles_1m.append(make_candle(t_start + 480_000, 2649.5, 2650.5, 2649.0, 2649.8))# 8: high 2650.5

    now_ms = t_start + 540_000

    poi = WatchedZone(
        zone_id="poi_15m_bearish",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BEARISH",
        top=2655.0,
        bottom=2650.0,
        created_at=t_start - 3600_000,
        known_at=t_start - 1800_000,
        touch_count=0,
    )

    buy_side_ref = LiquidityStub(id="lvl_bsl", price=2654.0, known_at_ms=t_start - 1800_000, side="BUY_SIDE")

    # 3. Detect Sweep
    sweep = detect_cg01_sweep(candles_1m, [buy_side_ref], poi, direction="SHORT", now_ms=now_ms, config=cfg)
    assert sweep is not None
    assert sweep["sweep_extreme"] == 2656.0
    assert sweep["reference_price"] == 2654.0

    # 4. Detect MSS & Displacement
    mss = resolve_cg01_mss_and_displacement(candles_1m, sweep, direction="SHORT", now_ms=now_ms, config=cfg, atr_1m=1.0)
    assert mss is not None
    assert mss["structure_ref_price"] == 2652.0
    assert mss["break_close"] <= 2650.0
    assert mss["displacement_body"] >= 0.8

    # 5. Detect Leg FVG
    fvg = select_cg01_leg_fvg(candles_1m, mss, direction="SHORT", now_ms=now_ms, config=cfg)
    assert fvg is not None
    assert fvg["lower"] == 2650.5
    assert fvg["upper"] == 2652.0
    expected_ce = compute_zone_ce(2650.5, 2652.0, tick_size=0.01)
    assert fvg["ce_price"] == expected_ce
    assert fvg["ce_price"] == 2651.25

    # 6. Structural SL & Target
    target_p = 2632.0
    sl_p = round(2656.0 + (cfg.sl_buffer_ticks * cfg.tick_size), 2)
    assert sl_p == 2656.05

    # Rule 185: Assert TP < entry < SL
    assert target_p < expected_ce < sl_p, f"SHORT geometry violated: TP={target_p} entry={expected_ce} SL={sl_p}"

    geom_ok, _ = validate_price_geometry("SHORT", expected_ce, sl_p, target_p)
    assert geom_ok

    calc = calculate_risk_reward(
        direction="SHORT",
        entry=expected_ce,
        sl=sl_p,
        tp=target_p,
        capital=1000.0,
        risk_pct=0.25,
        min_net_rr=2.0,
    )
    assert calc.is_valid
    assert calc.net_rr >= 2.0
