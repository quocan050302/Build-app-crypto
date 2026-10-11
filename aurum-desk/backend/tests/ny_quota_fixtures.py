"""Shared fixtures for V14.3 NY quota tests (not a test module).

All candles are persisted as real `models.Candle` rows with epoch-ms timestamps and EXPLICIT is_closed
flags. NY times are built with ZoneInfo (DST-safe) - never hard-coded VN strings.
"""
import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import models

NY = ZoneInfo("America/New_York")
FIVE = 300_000

# LONG story: swing high 2679 -> sell-off to a 2658 swing low -> impulse leaving a bullish FVG [2664, 2666]
# -> run to a confirmed swing high 2700 (liquidity target) -> pullback into the FVG zone.
LONG_BARS = [
    (2672, 2674, 2670, 2673), (2673, 2676, 2671, 2675), (2675, 2678, 2672, 2677), (2677, 2679, 2674, 2676),
    (2676, 2677, 2668, 2669), (2669, 2671, 2664, 2665), (2665, 2667, 2660, 2661), (2661, 2663, 2658, 2659),
    (2659, 2662, 2658.5, 2661.5), (2661.5, 2663, 2660, 2662), (2662, 2664, 2660.5, 2663),
    (2663, 2675, 2662.8, 2674), (2674, 2678, 2666, 2677), (2677, 2684, 2676, 2683), (2683, 2690, 2682, 2688),
    (2688, 2702, 2686, 2700), (2700, 2701, 2685, 2686), (2686, 2688, 2680, 2681), (2681, 2683, 2674, 2675),
    (2675, 2677, 2668, 2669), (2669, 2671, 2665.2, 2667),
]
MIRROR = 5330.0  # p' = MIRROR - p turns the LONG story into the SHORT story (high<->low)
SHORT_BARS = [(MIRROR - o, MIRROR - l, MIRROR - h, MIRROR - c) for (o, h, l, c) in LONG_BARS]

# Tick that retests the FVG zone: LONG buys the ask, SHORT sells the bid.
QUOTE = {
    "LONG": dict(bid=2665.5, ask=2665.8),
    "SHORT": dict(bid=MIRROR - 2665.8, ask=MIRROR - 2665.5),
}
BIAS = {"LONG": "BULLISH", "SHORT": "BEARISH"}


def ny_ms(y, m, d, hh, mm, ss=0):
    return int(datetime(y, m, d, hh, mm, ss, tzinfo=NY).timestamp() * 1000)


def bars_for(direction):
    return LONG_BARS if direction == "LONG" else SHORT_BARS


def seed_candles(db, direction, now_ms, *, symbol="XAUUSDT", closed_through=True, bars=None, replace=False):
    """Persist 5M candles so that the LAST bar closes exactly at `now_ms` (is_closed=True)."""
    if replace:
        db.query(models.Candle).filter(models.Candle.symbol == symbol, models.Candle.timeframe == "5M").delete()
        db.commit()
    bars = bars or bars_for(direction)
    n = len(bars)
    last_open = now_ms - FIVE
    base = last_open - (n - 1) * FIVE
    for i, (o, h, l, c) in enumerate(bars):
        db.add(models.Candle(symbol=symbol, timeframe="5M", timestamp=base + i * FIVE,
                             open=o, high=h, low=l, close=c, volume=100.0, is_closed=True))
    db.commit()
    return base


def candle_objects(direction, now_ms, bars=None, closed=True):
    bars = bars or bars_for(direction)
    n = len(bars)
    base = now_ms - FIVE - (n - 1) * FIVE
    return [SimpleNamespace(timestamp=base + i * FIVE, open=o, high=h, low=l, close=c, volume=1.0, is_closed=closed)
            for i, (o, h, l, c) in enumerate(bars)]


def ticker(direction, now_ms, age_ms=500):
    q = QUOTE[direction]
    ts = now_ms - age_ms
    return {"symbol": "XAUUSDT", "bid": q["bid"], "ask": q["ask"], "last": q["bid"],
            "timestamp": ts, "observed_at": ts}


def set_saved_risk(db, *, leverage=30, margin_mode="ISOLATED", risk_pct=0.25):
    for k, v in (("default_leverage", str(leverage)), ("default_margin_mode", margin_mode),
                 ("default_risk_pct", str(risk_pct))):
        row = db.query(models.SystemConfig).filter(models.SystemConfig.key == k).first()
        if row:
            row.value = v
        else:
            db.add(models.SystemConfig(key=k, value=v, updated_at=0))
    db.commit()


def set_policy(db, **fields):
    from services.trading_policy_service import TradingPolicyService
    p = TradingPolicyService.get_or_create_policy(db)
    for k, v in fields.items():
        setattr(p, k, v)
    db.commit()
    return p


RED_ALWAYS = json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.0})


def make_news(db, now_ms, *, title="Core CPI m/m", offset_min=0):
    row = models.EconomicNews(
        source_id=f"v143-{title}-{offset_min}-{now_ms}", title=title, country="USD", currency="USD",
        impact="High", scheduled_at=now_ms + offset_min * 60_000, received_at=now_ms - 86_400_000)
    db.add(row)
    db.commit()
    return row


def make_lesson(db, title, now_ms, *, predicate=RED_ALWAYS, severity="CRITICAL", effect="BLOCK_ENTRY"):
    from services.lesson_rule_service import LessonRuleService
    rule = models.Lesson(
        created_at=now_ms, title=title, category="PROCESS", reflection="r", action_rule="a",
        status="APPROVED", is_approved=True, enabled=True, severity=severity, effect=effect,
        validation_status="VALID", predicate=predicate, scope=None, stage="ALL", revision=1)
    db.add(rule)
    db.commit()
    LessonRuleService.invalidate_cache()
    return rule


def seed_candles_15m(db, now_ms, *, n=40, symbol="XAUUSDT", price=2650.0):
    """Quiet 15M tape so the standard SMC flow has enough data (and nothing to arm)."""
    dur = 15 * 60_000
    base = now_ms - dur - (n - 1) * dur
    for i in range(n):
        db.add(models.Candle(symbol=symbol, timeframe="15M", timestamp=base + i * dur,
                             open=price, high=price + 1.0, low=price - 1.0, close=price, volume=10.0, is_closed=True))
    db.commit()
