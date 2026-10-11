"""
backend/services/smc_session_context.py
V14.4 — Causal calculation of Asian/London session ranges and previous day levels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
UTC_TZ = ZoneInfo("UTC")


@dataclass(frozen=True)
class SessionWindowConfig:
    name: str                               # ASIA, LONDON, NY
    start_ny_hour: int
    start_ny_minute: int
    end_ny_hour: int
    end_ny_minute: int
    spans_midnight: bool = False


# Standard trading session definitions relative to America/New_York
# Asia: 19:00 NY (prev day) to 02:00 NY (spans midnight)
# London / Europe: 03:00 NY to 11:30 NY
# NY trading window: 08:00 NY to 11:00 NY (or full 08:00 to 17:00 NY)
SESSION_CONFIGS: Dict[str, SessionWindowConfig] = {
    "ASIA": SessionWindowConfig("ASIA", 19, 0, 2, 0, spans_midnight=True),
    "LONDON": SessionWindowConfig("LONDON", 3, 0, 11, 30, spans_midnight=False),
    "NY": SessionWindowConfig("NY", 8, 0, 17, 0, spans_midnight=False),
}


@dataclass
class SessionRange:
    session_name: str
    session_date_ny: str
    start_ms: int
    end_ms: int
    high: Optional[float] = None
    low: Optional[float] = None
    high_time_ms: Optional[int] = None
    low_time_ms: Optional[int] = None
    is_complete: bool = False
    bars_count: int = 0
    coverage_status: str = "UNKNOWN"         # VALID, PARTIAL, MISSING, UNKNOWN

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_name": self.session_name,
            "session_date_ny": self.session_date_ny,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "high": self.high,
            "low": self.low,
            "high_time_ms": self.high_time_ms,
            "low_time_ms": self.low_time_ms,
            "is_complete": self.is_complete,
            "bars_count": self.bars_count,
            "coverage_status": self.coverage_status,
        }


def get_session_window_bounds(
    session_name: str,
    ny_date_str: str,
) -> Tuple[int, int]:
    """Returns (start_ms, end_ms) for a named session given the NY session date YYYY-MM-DD."""
    cfg = SESSION_CONFIGS[session_name]
    y, m, d = [int(x) for x in ny_date_str.split("-")]
    day_dt = datetime(y, m, d, tzinfo=NY_TZ)

    if cfg.spans_midnight:
        # e.g. Asia for date D starts on day D-1 at 19:00 NY and ends on day D at 02:00 NY
        prev_dt = day_dt - timedelta(days=1)
        start_dt = datetime(prev_dt.year, prev_dt.month, prev_dt.day, cfg.start_ny_hour, cfg.start_ny_minute, tzinfo=NY_TZ)
        end_dt = datetime(day_dt.year, day_dt.month, day_dt.day, cfg.end_ny_hour, cfg.end_ny_minute, tzinfo=NY_TZ)
    else:
        start_dt = datetime(day_dt.year, day_dt.month, day_dt.day, cfg.start_ny_hour, cfg.start_ny_minute, tzinfo=NY_TZ)
        end_dt = datetime(day_dt.year, day_dt.month, day_dt.day, cfg.end_ny_hour, cfg.end_ny_minute, tzinfo=NY_TZ)

    return int(start_dt.timestamp() * 1000), int(end_dt.timestamp() * 1000)


def build_session_range(
    session_name: str,
    ny_date_str: str,
    closed_candles: Sequence[Any],
    now_ms: int,
    candle_duration_ms: int = 300_000,
) -> SessionRange:
    """Computes session extreme levels causally using only bars closed <= now_ms."""
    start_ms, end_ms = get_session_window_bounds(session_name, ny_date_str)
    res = SessionRange(
        session_name=session_name,
        session_date_ny=ny_date_str,
        start_ms=start_ms,
        end_ms=end_ms,
    )

    # Session has not even started yet
    if now_ms < start_ms:
        res.coverage_status = "NOT_STARTED"
        return res

    # Evaluation cutoff is min(now_ms, end_ms)
    cutoff = min(now_ms, end_ms)
    res.is_complete = now_ms >= end_ms

    in_session_bars = [
        c for c in closed_candles
        if start_ms <= c.timestamp < cutoff
        and c.timestamp + candle_duration_ms <= cutoff
        and getattr(c, "is_closed", True)
    ]

    res.bars_count = len(in_session_bars)
    if not in_session_bars:
        res.coverage_status = "MISSING"
        return res

    high_bar = max(in_session_bars, key=lambda c: c.high)
    low_bar = min(in_session_bars, key=lambda c: c.low)

    res.high = round(float(high_bar.high), 2)
    res.high_time_ms = high_bar.timestamp
    res.low = round(float(low_bar.low), 2)
    res.low_time_ms = low_bar.timestamp
    res.coverage_status = "VALID" if (res.bars_count >= 10 or not res.is_complete) else "PARTIAL"
    return res


@dataclass
class PreviousDayLevels:
    date_str: str
    pdh: Optional[float] = None
    pdl: Optional[float] = None
    pdc: Optional[float] = None
    pdo: Optional[float] = None
    known_at_ms: int = 0
    is_valid: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date_str": self.date_str,
            "pdh": self.pdh,
            "pdl": self.pdl,
            "pdc": self.pdc,
            "pdo": self.pdo,
            "known_at_ms": self.known_at_ms,
            "is_valid": self.is_valid,
        }


def build_previous_day_levels(
    current_ny_date_str: str,
    closed_daily_candles: Sequence[Any],
    now_ms: int,
) -> PreviousDayLevels:
    """Finds previous completed daily candle strictly before current NY date."""
    y, m, d = [int(x) for x in current_ny_date_str.split("-")]
    curr_dt = datetime(y, m, d, tzinfo=NY_TZ)
    prev_dt = curr_dt - timedelta(days=1)
    prev_date_str = prev_dt.strftime("%Y-%m-%d")

    res = PreviousDayLevels(date_str=prev_date_str)
    if not closed_daily_candles:
        return res

    # Sort causal daily candles
    valid_daily = [
        c for c in closed_daily_candles
        if c.timestamp <= now_ms and getattr(c, "is_closed", True)
    ]
    if not valid_daily:
        return res

    # Find the most recent daily bar closed before current day start
    curr_start_ms = int(curr_dt.timestamp() * 1000)
    prior_bars = [c for c in valid_daily if c.timestamp + 86_400_000 <= curr_start_ms]
    if not prior_bars:
        # Fallback to last available strictly before curr_start_ms
        prior_bars = [c for c in valid_daily if c.timestamp < curr_start_ms]

    if prior_bars:
        last_d = prior_bars[-1]
        res.pdo = round(float(last_d.open), 2)
        res.pdh = round(float(last_d.high), 2)
        res.pdl = round(float(last_d.low), 2)
        res.pdc = round(float(last_d.close), 2)
        res.known_at_ms = last_d.timestamp + 86_400_000
        res.is_valid = True

    return res
