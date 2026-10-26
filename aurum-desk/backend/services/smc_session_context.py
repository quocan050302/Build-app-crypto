"""
backend/services/smc_session_context.py
V14.4.1 — Causal calculation of Asia / London (Europe) / NY session ranges and previous-day levels.

SESSION_CONFIGS below is the APP'S RESEARCH CONVENTION (version SESSION_CONVENTION_VERSION), expressed in
America/New_York wall time. It is not a mandatory market-wide clock. The NY ENTRY window (08:00-11:00 NY,
see trading_policy_service) is separate: analysing Asia/Europe never enables Asia/Europe entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
UTC_TZ = ZoneInfo("UTC")

SESSION_CONVENTION_VERSION = "session-convention-v1"
DAY_MS = 86_400_000


@dataclass(frozen=True)
class SessionWindowConfig:
    name: str                               # ASIA, LONDON, NY
    start_ny_hour: int
    start_ny_minute: int
    end_ny_hour: int
    end_ny_minute: int
    spans_midnight: bool = False


# Research convention (America/New_York):
#   ASIA   19:00 (prev day) -> 02:00
#   LONDON 03:00 -> 11:30   (Europe)
#   NY     08:00 -> 17:00   (analysis window; the entry window is configured separately)
SESSION_CONFIGS: Dict[str, SessionWindowConfig] = {
    "ASIA": SessionWindowConfig("ASIA", 19, 0, 2, 0, spans_midnight=True),
    "LONDON": SessionWindowConfig("LONDON", 3, 0, 11, 30, spans_midnight=False),
    "NY": SessionWindowConfig("NY", 8, 0, 17, 0, spans_midnight=False),
}


def ny_date_str(ms: int) -> str:
    """NY calendar date (YYYY-MM-DD) of an epoch-ms instant. Derived from the injected clock, never from now()."""
    return datetime.fromtimestamp(ms / 1000.0, tz=NY_TZ).strftime("%Y-%m-%d")


def scenario_history_start_ms(now_ms: int) -> int:
    """Start of the bounded M5/M15 window: 19:00 NY (Asia open) of the PREVIOUS NY calendar day. Covers the
    full Asia+Europe+NY chain of the current session (and the Asia that precedes it) without loading a year."""
    prev = datetime.fromtimestamp(now_ms / 1000.0, tz=NY_TZ) - timedelta(days=1)
    return int(datetime(prev.year, prev.month, prev.day, 19, 0, tzinfo=NY_TZ).timestamp() * 1000)


@dataclass
class SessionRange:
    session_name: str
    session_date_ny: str
    start_ms: int
    end_ms: int
    high: Optional[float] = None
    low: Optional[float] = None
    open: Optional[float] = None
    close_as_of: Optional[float] = None
    high_time_ms: Optional[int] = None
    low_time_ms: Optional[int] = None
    cutoff_ms: Optional[int] = None          # min(now, session end): range is known as of this instant
    is_complete: bool = False                # TIME has passed the session end (not a data-coverage claim)
    is_partial: bool = True                  # high/low may still change (session running)
    bars_count: int = 0                      # observed UNIQUE closed bars
    expected_bars: int = 0
    gap_bars: int = 0
    coverage_ratio: float = 0.0
    coverage_status: str = "UNKNOWN"         # VALID | LOW_COVERAGE | MISSING | NOT_STARTED | UNKNOWN
    convention_version: str = SESSION_CONVENTION_VERSION

    @property
    def is_trusted(self) -> bool:
        return self.coverage_status == "VALID"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_name": self.session_name,
            "session_date_ny": self.session_date_ny,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "cutoff_ms": self.cutoff_ms,
            "high": self.high,
            "low": self.low,
            "open": self.open,
            "close_as_of": self.close_as_of,
            "high_time_ms": self.high_time_ms,
            "low_time_ms": self.low_time_ms,
            "is_complete": self.is_complete,
            "is_partial": self.is_partial,
            "bars_count": self.bars_count,
            "expected_bars": self.expected_bars,
            "gap_bars": self.gap_bars,
            "coverage_ratio": round(self.coverage_ratio, 4),
            "coverage_status": self.coverage_status,
            "convention_version": self.convention_version,
        }


def get_session_window_bounds(session_name: str, ny_date_str_: str) -> Tuple[int, int]:
    """(start_ms, end_ms) of a named session for NY session date YYYY-MM-DD. DST-aware via zoneinfo."""
    cfg = SESSION_CONFIGS[session_name]
    y, m, d = [int(x) for x in ny_date_str_.split("-")]
    if cfg.spans_midnight:
        prev = datetime(y, m, d, tzinfo=NY_TZ) - timedelta(days=1)
        start_dt = datetime(prev.year, prev.month, prev.day, cfg.start_ny_hour, cfg.start_ny_minute, tzinfo=NY_TZ)
    else:
        start_dt = datetime(y, m, d, cfg.start_ny_hour, cfg.start_ny_minute, tzinfo=NY_TZ)
    end_dt = datetime(y, m, d, cfg.end_ny_hour, cfg.end_ny_minute, tzinfo=NY_TZ)
    return int(start_dt.timestamp() * 1000), int(end_dt.timestamp() * 1000)


def build_session_range(
    session_name: str,
    ny_date_str_: str,
    closed_candles: Sequence[Any],
    now_ms: int,
    candle_duration_ms: int = 300_000,
    min_coverage_ratio: float = 0.80,
) -> SessionRange:
    """Session extremes as known at `now_ms`, using only bars whose CLOSE is <= min(now, session end).

    A session still running is partial: its high/low/close are as-of values, never the final ones.
    Coverage is measured (expected vs observed unique bars, gaps) - a time-complete session with holes is
    LOW_COVERAGE, and a running session with enough bars is VALID-as-partial.
    """
    start_ms, end_ms = get_session_window_bounds(session_name, ny_date_str_)
    res = SessionRange(session_name=session_name, session_date_ny=ny_date_str_, start_ms=start_ms, end_ms=end_ms)
    if now_ms <= start_ms:
        res.coverage_status = "NOT_STARTED"
        return res

    cutoff = min(now_ms, end_ms)
    res.cutoff_ms = cutoff
    res.is_complete = now_ms >= end_ms
    res.is_partial = not res.is_complete
    res.expected_bars = max(0, (cutoff - start_ms) // candle_duration_ms)

    by_ts: Dict[int, Any] = {}
    for c in closed_candles:
        ts = getattr(c, "timestamp", None)
        if not isinstance(ts, int) or isinstance(ts, bool):
            continue
        if getattr(c, "is_closed", None) is not True:
            continue
        if start_ms <= ts and ts + candle_duration_ms <= cutoff:
            by_ts[ts] = c
    bars = [by_ts[t] for t in sorted(by_ts)]
    res.bars_count = len(bars)
    res.gap_bars = max(0, res.expected_bars - res.bars_count)
    res.coverage_ratio = (res.bars_count / res.expected_bars) if res.expected_bars else 0.0
    if not bars:
        res.coverage_status = "MISSING"
        return res

    high_bar = max(bars, key=lambda c: c.high)
    low_bar = min(bars, key=lambda c: c.low)
    res.open = round(float(bars[0].open), 2)
    res.close_as_of = round(float(bars[-1].close), 2)
    res.high = round(float(high_bar.high), 2)
    res.high_time_ms = high_bar.timestamp
    res.low = round(float(low_bar.low), 2)
    res.low_time_ms = low_bar.timestamp
    res.coverage_status = "VALID" if res.coverage_ratio >= min_coverage_ratio else "LOW_COVERAGE"
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
    basis: str = "UNKNOWN"                   # BITGET_D_UTC | NY_CALENDAR_DAY_FROM_M5 | UNKNOWN
    status: str = "UNKNOWN"                  # VALID | MISSING | LOW_COVERAGE | UNKNOWN
    source_open_ms: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date_str": self.date_str,
            "pdh": self.pdh,
            "pdl": self.pdl,
            "pdc": self.pdc,
            "pdo": self.pdo,
            "known_at_ms": self.known_at_ms,
            "is_valid": self.is_valid,
            "basis": self.basis,
            "status": self.status,
            "source_open_ms": self.source_open_ms,
        }


def build_previous_day_levels(
    current_ny_date_str: str,
    closed_daily_candles: Sequence[Any],
    now_ms: int,
) -> PreviousDayLevels:
    """PDH/PDL from the most recent daily candle that was FULLY closed at `now_ms`.

    The daily bar boundary is the exchange's (Bitget D = UTC day), NOT the NY trading day: this is recorded
    in `basis`. An open/partial daily bar is never used (no fallback), so `known_at_ms` can never lie in the
    future. Missing data -> status MISSING, no PDH/PDL (never 0).
    """
    y, m, d = [int(x) for x in current_ny_date_str.split("-")]
    prev_date_str = (datetime(y, m, d, tzinfo=NY_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
    res = PreviousDayLevels(date_str=prev_date_str, basis="BITGET_D_UTC", status="MISSING")
    by_ts: Dict[int, Any] = {}
    for c in closed_daily_candles or []:
        ts = getattr(c, "timestamp", None)
        if not isinstance(ts, int) or isinstance(ts, bool) or getattr(c, "is_closed", None) is not True:
            continue
        if ts + DAY_MS <= now_ms:
            by_ts[ts] = c
    if not by_ts:
        return res
    last_ts = max(by_ts)
    last_d = by_ts[last_ts]
    res.pdo = round(float(last_d.open), 2)
    res.pdh = round(float(last_d.high), 2)
    res.pdl = round(float(last_d.low), 2)
    res.pdc = round(float(last_d.close), 2)
    res.known_at_ms = last_ts + DAY_MS
    res.source_open_ms = last_ts
    res.date_str = datetime.fromtimestamp(last_ts / 1000.0, tz=UTC_TZ).strftime("%Y-%m-%d")
    res.is_valid = True
    res.status = "VALID"
    return res


def build_previous_ny_day_levels_from_m5(
    current_ny_date_str: str,
    closed_m5: Sequence[Any],
    now_ms: int,
    min_coverage_ratio: float = 0.90,
    candle_duration_ms: int = 300_000,
) -> PreviousDayLevels:
    """PDH/PDL for the NY CALENDAR day before `current_ny_date_str`, aggregated from M5 with coverage check."""
    y, m, d = [int(x) for x in current_ny_date_str.split("-")]
    cur = datetime(y, m, d, tzinfo=NY_TZ)
    prev = cur - timedelta(days=1)
    start_ms = int(datetime(prev.year, prev.month, prev.day, tzinfo=NY_TZ).timestamp() * 1000)
    end_ms = int(cur.timestamp() * 1000)
    res = PreviousDayLevels(date_str=prev.strftime("%Y-%m-%d"), basis="NY_CALENDAR_DAY_FROM_M5", status="MISSING")
    if now_ms < end_ms:
        return res
    by_ts = {c.timestamp: c for c in closed_m5
             if getattr(c, "is_closed", None) is True and start_ms <= c.timestamp and c.timestamp + candle_duration_ms <= end_ms}
    if not by_ts:
        return res
    expected = (end_ms - start_ms) // candle_duration_ms
    if len(by_ts) / expected < min_coverage_ratio:
        res.status = "LOW_COVERAGE"
        return res
    bars = [by_ts[t] for t in sorted(by_ts)]
    res.pdo = round(float(bars[0].open), 2)
    res.pdc = round(float(bars[-1].close), 2)
    res.pdh = round(max(float(b.high) for b in bars), 2)
    res.pdl = round(min(float(b.low) for b in bars), 2)
    res.known_at_ms = end_ms
    res.is_valid = True
    res.status = "VALID"
    return res
