"""
Deterministic synthetic market fixtures for V14.4.1 tests (NOT market data; correctness fixtures only).

LONG: Asia/London decline from a 2675 swing peak, a sell-side sweep (wick 2644.8 under the 2646.0 swing
low), a displacement break above the pre-sweep swing high, a real FVG created by that leg, then a closed
retest. SHORT is the exact price mirror (p' = 5300 - p, high/low swapped).
Tail layout (t0 = 08:00 NY): sweep t13, displacement t15 (FVG middle), FVG third t16, break-through t17,
retest t18 = last closed bar at now (09:35 NY).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
M5 = 300_000
MIRROR_K = 5300.0
SYMBOL = "XAUUSDT"


@dataclass
class C:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 1.0
    is_closed: bool = True


def ny_ms(y, mo, d, h, mi=0) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=NY).timestamp() * 1000)


NOW_MS = ny_ms(2026, 4, 14, 9, 35)         # NY entry window (08:00-11:00 NY)
T0_MS = ny_ms(2026, 4, 14, 8, 0)
HISTORY_START_MS = ny_ms(2026, 4, 13, 19, 0)  # Asia start (previous NY day 19:00)

TAIL = [  # (open, high, low, close) for t0..t18
    (2650.0, 2650.6, 2649.6, 2650.2), (2650.2, 2651.0, 2650.0, 2650.4), (2650.4, 2650.7, 2649.5, 2649.8),
    (2649.8, 2650.1, 2649.0, 2649.2), (2649.2, 2649.6, 2648.4, 2648.6), (2648.6, 2649.0, 2647.8, 2648.0),
    (2648.0, 2648.4, 2647.0, 2647.2), (2647.2, 2647.9, 2646.6, 2647.0), (2647.0, 2647.5, 2646.2, 2646.4),
    (2646.4, 2647.0, 2646.0, 2646.8), (2646.8, 2647.6, 2646.5, 2647.4), (2647.4, 2647.5, 2646.7, 2646.9),
    (2646.9, 2647.2, 2646.3, 2646.5),
    (2646.5, 2646.9, 2644.8, 2646.4),     # t13 sweep: wick 2644.8 under swing low 2646.0, closes back above
    (2646.4, 2647.0, 2646.2, 2646.7),     # t14 FVG first candle (high 2647.0)
    (2646.7, 2650.0, 2646.6, 2649.8),     # t15 displacement (closes above 2647.6) = FVG middle
    (2649.8, 2651.5, 2648.2, 2651.0),     # t16 FVG third (low 2648.2) -> bullish FVG [2647.0, 2648.2]
    (2651.0, 2652.6, 2650.9, 2652.5),     # t17 closes through the t1 swing high 2651.0
    (2652.5, 2652.8, 2648.0, 2648.6),     # t18 retest: low 2648.0 <= 2648.2, close >= 2647.0
]


def _history_long() -> List[tuple]:
    """Asia/London: rise to a 2675 peak, then a strictly monotonic decline to 2650.0 (no spurious pivots)."""
    n = (T0_MS - HISTORY_START_MS) // M5
    up_bars, down_bars = 60, n - 60
    rows: List[tuple] = []
    px = 2655.0
    step_up = (2675.0 - 2655.0) / up_bars
    for i in range(up_bars):
        o, c = px, px + step_up
        rows.append((o, c + 0.12 + i * 0.0005, o - 0.12 + i * 0.0005, c)); px = c
    # peak candle: unique max
    rows[-1] = (rows[-1][0], 2675.0, rows[-1][2], rows[-1][3])
    step_dn = (px - 2650.0) / down_bars
    for i in range(down_bars):
        o, c = px, px - step_dn
        rows.append((o, o + 0.8, c - 0.8, c)); px = c
    return rows


def mirror(row: tuple) -> tuple:
    o, h, l, c = row
    return (MIRROR_K - o, MIRROR_K - l, MIRROR_K - h, MIRROR_K - c)


def build_m5(direction: str = "LONG", now_ms: int = NOW_MS) -> List[C]:
    rows = _history_long() + TAIL
    if direction == "SHORT":
        rows = [mirror(r) for r in rows]
    bars = [C(HISTORY_START_MS + i * M5, *[round(v, 2) for v in r]) for i, r in enumerate(rows)]
    return [b for b in bars if b.timestamp + M5 <= now_ms]


def aggregate(m5: List[C], minutes: int, now_ms: int) -> List[C]:
    period = minutes * 60_000
    buckets: Dict[int, List[C]] = {}
    for c in m5:
        buckets.setdefault(c.timestamp - c.timestamp % period, []).append(c)
    out = []
    for start in sorted(buckets):
        bars = buckets[start]
        if start + period <= now_ms and len(bars) == minutes // 5:
            out.append(C(start, bars[0].open, max(b.high for b in bars), min(b.low for b in bars), bars[-1].close))
    return out


def daily_before(now_ms: int, direction: str = "LONG") -> List[C]:
    """One fully closed Bitget D bar (UTC day) ending before now: PDH 2690 / PDL 2630 (mirrored for SHORT)."""
    day = 86_400_000
    start = (now_ms // day - 1) * day
    row = (2640.0, 2690.0, 2630.0, 2655.0)
    if direction == "SHORT":
        row = mirror(row)
    return [C(start, *row)]


def make_candles_by_tf(direction: str = "LONG", now_ms: int = NOW_MS) -> Dict[str, List[C]]:
    m5 = build_m5(direction, now_ms)
    return {"5M": m5, "15M": aggregate(m5, 15, now_ms), "1H": aggregate(m5, 60, now_ms),
            "4H": aggregate(m5, 240, now_ms), "D": daily_before(now_ms, direction)}


def default_settings():
    from domain_calculator import CostAssumptions
    from services.smc_scenario_plan import FinancialSettings
    return FinancialSettings(capital_usdt=1000.0, risk_pct=0.25, leverage=5, margin_mode="ISOLATED",
                             costs=CostAssumptions(), instrument_version="test-fixture")
