"""
backend/services/cg01_context_adapter.py
V16.3 CG-01 Canonical Data Contracts and Context Adapter.
Implements Rules 021 - 035, 041 - 045, 146 - 148.

Provides:
- CanonicalCandle: immutable candle representation with attribute & dict access.
- normalize_cg01_candle: validates OHLC geometry, timestamp sanity, and converts dict/ORM.
- select_closed_candles: causal closed candle filter based on as_of_ms and timeframe duration.
- adapt_cg01_quote: boundary quote adapter preserving bid, ask, freshness, spread.
- build_cg01_closed_context: multi-timeframe closed candle bundle.
- assess_cg01_context_readiness: checks warmup requirements across frames.
- aggregate_closed_bars: causal aggregation of M1 into 15M, 1H, 4H without lookahead.
- build_cg01_liquidity_context: causal SMC liquidity levels using confirmed swings and state updates.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from services.cg01_types import CG01Config
from services.liquidity_scenario_service import identify_confirmed_swings, compute_dealing_range
from services.smc_liquidity_service import build_liquidity_map, update_liquidity_states, LiquidityLevel


TIMEFRAME_DURATIONS_MS: Dict[str, int] = {
    "1M": 60_000,
    "3M": 180_000,
    "5M": 300_000,
    "15M": 900_000,
    "30M": 1_800_000,
    "1H": 3_600_000,
    "2H": 7_200_000,
    "4H": 14_400_000,
    "6H": 21_600_000,
    "12H": 43_200_000,
    "D": 86_400_000,
    "1D": 86_400_000,
}


def resolve_tf_duration_ms(tf: str) -> int:
    return TIMEFRAME_DURATIONS_MS.get(tf.upper(), 60_000)


@dataclass(frozen=True)
class CanonicalCandle:
    """
    Rule 021, 022: Canonical immutable candle representation.
    Provides both attribute access (candle.close) and dict-like access (candle['close'], candle.get('close')).
    """
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 100.0
    timeframe: str = "1M"
    symbol: str = "XAUUSDT"
    is_closed: bool = True
    close_time_ms: int = 0

    @property
    def timestamp(self) -> int:
        return self.open_time

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "open_time": self.open_time,
            "timestamp": self.open_time,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "timeframe": self.timeframe,
            "symbol": self.symbol,
            "is_closed": self.is_closed,
            "close_time_ms": self.close_time_ms,
        }


@dataclass(frozen=True)
class CanonicalQuote:
    """
    Rule 027, 028: Canonical execution quote adapter.
    Preserves bid, ask, spread, validity, staleness, and timestamp.
    """
    bid: float
    ask: float
    is_valid: bool
    is_stale: bool
    timestamp: int
    spread: float
    mid: float

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bid": self.bid,
            "ask": self.ask,
            "is_valid": self.is_valid,
            "is_stale": self.is_stale,
            "timestamp": self.timestamp,
            "spread": self.spread,
            "mid": self.mid,
        }


def normalize_cg01_candle(raw: Any, timeframe: str = "1M", symbol: str = "XAUUSDT") -> CanonicalCandle:
    """
    Rule 021 - 024: Validates OHLC geometry and converts raw dict or ORM object into CanonicalCandle.
    Rejects NaN/inf, high < low, open/close outside [low, high], and non-positive timestamps.
    """
    if raw is None:
        raise ValueError("DATA_INVALID: candle cannot be None")

    if isinstance(raw, CanonicalCandle):
        return raw

    # Timestamp extraction
    ts = None
    if isinstance(raw, dict):
        ts = raw.get("open_time") or raw.get("timestamp") or raw.get("time")
    else:
        ts = getattr(raw, "open_time", None) or getattr(raw, "timestamp", None) or getattr(raw, "time", None)

    if ts is None:
        raise ValueError("DATA_INVALID: candle missing open_time/timestamp")

    try:
        ts_int = int(ts)
    except (ValueError, TypeError):
        raise ValueError(f"DATA_INVALID: invalid timestamp value {ts}")

    # Convert seconds to milliseconds only if in Unix seconds range (Rule 024)
    if 1_000_000_000 <= ts_int <= 2_500_000_000:
        ts_int = ts_int * 1000

    if ts_int <= 0:
        raise ValueError(f"DATA_INVALID: non-positive timestamp {ts_int}")

    # Price extraction
    def _extract_float(field_name: str) -> float:
        val = raw.get(field_name) if isinstance(raw, dict) else getattr(raw, field_name, None)
        if val is None:
            raise ValueError(f"DATA_INVALID: missing required field '{field_name}'")
        try:
            f_val = float(val)
        except (ValueError, TypeError):
            raise ValueError(f"DATA_INVALID: field '{field_name}' is not float: {val}")
        if not math.isfinite(f_val):
            raise ValueError(f"DATA_INVALID: field '{field_name}' is NaN or Inf")
        return f_val

    o = _extract_float("open")
    h = _extract_float("high")
    l = _extract_float("low")
    c = _extract_float("close")

    # Volume extraction
    vol = 100.0
    raw_vol = raw.get("volume") if isinstance(raw, dict) else getattr(raw, "volume", 100.0)
    if raw_vol is not None:
        try:
            f_vol = float(raw_vol)
            if math.isfinite(f_vol) and f_vol >= 0:
                vol = f_vol
        except (ValueError, TypeError):
            pass

    # Geometry validation (Rule 023)
    if h < l:
        raise ValueError(f"DATA_INVALID: high ({h}) < low ({l}) at timestamp {ts_int}")

    # Tolerance for small floating point rounding: allow 1e-4 epsilon
    eps = 1e-4
    if o < (l - eps) or o > (h + eps):
        raise ValueError(f"DATA_INVALID: open ({o}) outside [low ({l}), high ({h})] at timestamp {ts_int}")
    if c < (l - eps) or c > (h + eps):
        raise ValueError(f"DATA_INVALID: close ({c}) outside [low ({l}), high ({h})] at timestamp {ts_int}")

    duration_ms = resolve_tf_duration_ms(timeframe)
    close_time_ms = ts_int + duration_ms

    raw_tf = raw.get("timeframe") if isinstance(raw, dict) else getattr(raw, "timeframe", timeframe)
    raw_sym = raw.get("symbol") if isinstance(raw, dict) else getattr(raw, "symbol", symbol)
    raw_closed = raw.get("is_closed") if isinstance(raw, dict) else getattr(raw, "is_closed", True)

    return CanonicalCandle(
        open_time=ts_int,
        open=round(o, 4),
        high=round(h, 4),
        low=round(l, 4),
        close=round(c, 4),
        volume=round(vol, 4),
        timeframe=str(raw_tf or timeframe),
        symbol=str(raw_sym or symbol),
        is_closed=bool(raw_closed),
        close_time_ms=close_time_ms,
    )


def select_closed_candles(
    candles: Sequence[Any],
    timeframe: str,
    as_of_ms: int,
    symbol: str = "XAUUSDT",
) -> List[CanonicalCandle]:
    """
    Rule 025, 026: Filters strictly closed candles up to as_of_ms.
    A candle is closed only when open_time + duration_ms <= as_of_ms.
    Sorts ascending by open_time and deduplicates identical timestamps.
    """
    if not candles:
        return []

    duration_ms = resolve_tf_duration_ms(timeframe)
    normalized: List[CanonicalCandle] = []

    for raw in candles:
        try:
            c = normalize_cg01_candle(raw, timeframe=timeframe, symbol=symbol)
            if c.open_time + duration_ms <= as_of_ms:
                normalized.append(c)
        except Exception:
            continue

    # Sort ascending by open_time
    normalized.sort(key=lambda x: x.open_time)

    # Deduplicate by timestamp (Rule 026)
    deduped: List[CanonicalCandle] = []
    seen: Dict[int, CanonicalCandle] = {}
    for c in normalized:
        if c.open_time not in seen:
            seen[c.open_time] = c
            deduped.append(c)
        else:
            prev = seen[c.open_time]
            # Verify no conflicting data
            if abs(prev.close - c.close) > 0.01:
                # Conflicting revision: preserve later one
                seen[c.open_time] = c
                # Update in deduped list
                for idx, item in enumerate(deduped):
                    if item.open_time == c.open_time:
                        deduped[idx] = c
                        break

    return deduped


def adapt_cg01_quote(q: Any) -> CanonicalQuote:
    """
    Rule 027, 028: Adapts raw quote or quote object to CanonicalQuote.
    Preserves bid, ask, spread, mid, validity and timestamp.
    """
    if q is None:
        return CanonicalQuote(
            bid=0.0, ask=0.0, is_valid=False, is_stale=True, timestamp=0, spread=0.0, mid=0.0
        )

    if isinstance(q, CanonicalQuote):
        return q

    bid = float(getattr(q, "bid", 0.0) if hasattr(q, "bid") else (q.get("bid", 0.0) if isinstance(q, dict) else 0.0))
    ask = float(getattr(q, "ask", 0.0) if hasattr(q, "ask") else (q.get("ask", 0.0) if isinstance(q, dict) else 0.0))
    is_valid = bool(getattr(q, "is_valid", False) if hasattr(q, "is_valid") else (q.get("is_valid", False) if isinstance(q, dict) else False))
    is_stale = bool(getattr(q, "is_stale", False) if hasattr(q, "is_stale") else (q.get("is_stale", False) if isinstance(q, dict) else False))
    ts = int(getattr(q, "timestamp", 0) if hasattr(q, "timestamp") else (q.get("timestamp", 0) if isinstance(q, dict) else 0))

    if bid > 0 and ask <= 0:
        ask = bid
    elif ask > 0 and bid <= 0:
        bid = ask

    if is_valid and (bid <= 0 or ask < bid or not math.isfinite(bid) or not math.isfinite(ask)):
        is_valid = False

    spread = round(ask - bid, 4) if (bid > 0 and ask >= bid) else 0.0
    mid = round((bid + ask) / 2.0, 4) if (bid > 0 and ask > 0) else bid

    return CanonicalQuote(
        bid=bid,
        ask=ask,
        is_valid=is_valid,
        is_stale=is_stale,
        timestamp=ts,
        spread=spread,
        mid=mid,
    )


def build_cg01_closed_context(
    raw_by_tf: Dict[str, Sequence[Any]],
    as_of_ms: int,
    symbol: str = "XAUUSDT",
) -> Dict[str, Any]:
    """
    Rule 029: Assembles canonical closed context bundle across 4H, 1H, 15M, and 1M frames.
    """
    c_4h = select_closed_candles(raw_by_tf.get("4H") or [], "4H", as_of_ms, symbol)
    c_1h = select_closed_candles(raw_by_tf.get("1H") or [], "1H", as_of_ms, symbol)
    c_15m = select_closed_candles(raw_by_tf.get("15M") or [], "15M", as_of_ms, symbol)
    c_1m = select_closed_candles(raw_by_tf.get("1M") or [], "1M", as_of_ms, symbol)

    counts = {
        "4H": len(c_4h),
        "1H": len(c_1h),
        "15M": len(c_15m),
        "1M": len(c_1m),
    }

    last_close_times = {
        "4H": c_4h[-1].close_time_ms if c_4h else None,
        "1H": c_1h[-1].close_time_ms if c_1h else None,
        "15M": c_15m[-1].close_time_ms if c_15m else None,
        "1M": c_1m[-1].close_time_ms if c_1m else None,
    }

    missing_frames = [tf for tf, cnt in counts.items() if cnt == 0]

    return {
        "4H": c_4h,
        "1H": c_1h,
        "15M": c_15m,
        "1M": c_1m,
        "counts": counts,
        "last_close_times": last_close_times,
        "missing_frames": missing_frames,
        "as_of_ms": as_of_ms,
        "symbol": symbol,
    }


def assess_cg01_context_readiness(
    bundle: Dict[str, Any],
    config: Optional[CG01Config] = None,
) -> Tuple[bool, str, List[str]]:
    """
    Rule 030: Evaluates readiness of closed candle bundle for CG-01 consumers.
    Requires at least 12 bars for 4H, 1H, 15M, and at least 3 bars for 1M.
    """
    counts = bundle.get("counts", {})
    missing_conds = []

    c_4h_count = counts.get("4H", 0)
    c_1h_count = counts.get("1H", 0)
    c_15m_count = counts.get("15M", 0)
    c_1m_count = counts.get("1M", 0)

    if c_4h_count < 12:
        missing_conds.append(f"INSUFFICIENT_4H_BARS (have {c_4h_count}, need 12)")
    if c_1h_count < 12:
        missing_conds.append(f"INSUFFICIENT_1H_BARS (have {c_1h_count}, need 12)")
    if c_15m_count < 12:
        missing_conds.append(f"INSUFFICIENT_15M_BARS (have {c_15m_count}, need 12)")
    if c_1m_count < 3:
        missing_conds.append(f"INSUFFICIENT_1M_BARS (have {c_1m_count}, need 3)")

    if missing_conds:
        return False, "CONTEXT_WARMUP", missing_conds

    return True, "READY", []


def build_cg01_liquidity_context(
    bundle: Dict[str, Any],
    as_of_ms: int,
    symbol: str = "XAUUSDT",
    config: Optional[CG01Config] = None,
) -> List[LiquidityLevel]:
    """
    Rule 041, 042: Builds causal liquidity map from confirmed 15M swings and updates states with M1 candles.
    """
    c_15m = [normalize_cg01_candle(b, "15M") if not isinstance(b, CanonicalCandle) else b for b in (bundle.get("15M") or [])]
    c_1m = [normalize_cg01_candle(b, "1M") if not isinstance(b, CanonicalCandle) else b for b in (bundle.get("1M") or [])]

    left_bars = config.pivot_left_bars if config else 2
    right_bars = config.pivot_right_bars if config else 2

    highs, lows = identify_confirmed_swings(
        c_15m,
        timeframe="15M",
        left_bars=left_bars,
        right_bars=right_bars,
        symbol=symbol,
        as_of_ms=as_of_ms,
    )

    levels_15m = build_liquidity_map(
        symbol=symbol,
        timeframe="15M",
        swing_highs=highs,
        swing_lows=lows,
        now_ms=as_of_ms,
    )

    all_levels: List[LiquidityLevel] = list(levels_15m)
    seen_ids = {lvl.id for lvl in all_levels}

    if c_1m:
        highs_m1, lows_m1 = identify_confirmed_swings(
            c_1m,
            timeframe="1M",
            left_bars=left_bars,
            right_bars=right_bars,
            symbol=symbol,
            as_of_ms=as_of_ms,
        )
        levels_m1 = build_liquidity_map(
            symbol=symbol,
            timeframe="1M",
            swing_highs=highs_m1,
            swing_lows=lows_m1,
            now_ms=as_of_ms,
        )
        for lvl in levels_m1:
            if lvl.id not in seen_ids:
                all_levels.append(lvl)
                seen_ids.add(lvl.id)

        update_liquidity_states(all_levels, c_1m, as_of_ms, candle_duration_ms=60_000)

    return all_levels


def aggregate_closed_bars(
    m1_bars: Sequence[Any],
    target_tf: str,
    as_of_ms: int,
    symbol: str = "XAUUSDT",
) -> List[CanonicalCandle]:
    """
    Rule 146, 147: Causal aggregation of closed M1 bars into completed HTF bars (15M, 1H, 4H).
    Guarantees zero lookahead: a bucket is included ONLY if bucket_start + duration <= as_of_ms.
    """
    duration_ms = resolve_tf_duration_ms(target_tf)
    if duration_ms <= 60_000:
        return select_closed_candles(m1_bars, "1M", as_of_ms, symbol)

    # Filter and sort closed M1 bars
    closed_m1 = select_closed_candles(m1_bars, "1M", as_of_ms, symbol)
    if not closed_m1:
        return []

    # Group M1 bars into target bucket
    buckets: Dict[int, List[CanonicalCandle]] = {}
    for bar in closed_m1:
        bucket_start = (bar.open_time // duration_ms) * duration_ms
        if bucket_start not in buckets:
            buckets[bucket_start] = []
        buckets[bucket_start].append(bar)

    aggregated: List[CanonicalCandle] = []
    for b_start in sorted(buckets.keys()):
        b_end = b_start + duration_ms
        # Zero lookahead check (Rule 147): bucket must be completely closed at as_of_ms
        if b_end > as_of_ms:
            continue

        bars = buckets[b_start]
        bars.sort(key=lambda b: b.open_time)

        o = bars[0].open
        h = max(b.high for b in bars)
        l = min(b.low for b in bars)
        c = bars[-1].close
        v = sum(b.volume for b in bars)

        agg_candle = CanonicalCandle(
            open_time=b_start,
            open=round(o, 4),
            high=round(h, 4),
            low=round(l, 4),
            close=round(c, 4),
            volume=round(v, 4),
            timeframe=target_tf,
            symbol=symbol,
            is_closed=True,
            close_time_ms=b_end,
        )
        aggregated.append(agg_candle)

    return aggregated
