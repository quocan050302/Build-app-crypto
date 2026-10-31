"""
AURUM Desk V17.9.1 — Causal Multi-Timeframe Structure & Liquidity Engine (H4/H1/M15)
====================================================================================
Provides unified, strictly causal, non-repainting market structure (Confirmed Swings,
HH/HL/LH/LL, Protected High/Low, Closed-Candle BOS/CHoCH) and liquidity mapping
(PDH/PDL from completed sessions, Equal High/Low bounded-diameter pools, Closed-Candle
Sweep & Reclaim, Lifecycle State Machine) for Bitget XAUUSDT across:
  - Production (`ScalpWatcherOrchestrator`)
  - MARKET_SHADOW (`MarketShadowService`)
  - Chronological Backtest (`V17_8_BacktestRunner`)

All functions enforce `available_at <= decision_time` and reuse existing repository
primitives (`htf_structure_service`, `smc_liquidity_service`, `market_calendar_service`,
`cg01_daily_bias_service`) without creating a parallel trading engine.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from backend.services.market_calendar_service import get_contract_trading_availability

MTF_ALGORITHM_VERSION = "17.9.1"
SUPPORTED_SYMBOL = "XAUUSDT"
SUPPORTED_VENUE = "BITGET"
DEFAULT_TICK_SIZE = 0.01

TIMEFRAME_SECONDS: Dict[str, int] = {
    "M1": 60,
    "1m": 60,
    "M5": 300,
    "5m": 300,
    "M15": 900,
    "15m": 900,
    "H1": 3600,
    "1h": 3600,
    "H4": 14400,
    "4h": 14400,
    "D1": 86400,
    "1d": 86400,
}

CANONICAL_TF_MAP: Dict[str, str] = {
    "1m": "M1",
    "m1": "M1",
    "M1": "M1",
    "5m": "M5",
    "m5": "M5",
    "M5": "M5",
    "15m": "M15",
    "m15": "M15",
    "M15": "M15",
    "1h": "H1",
    "h1": "H1",
    "H1": "H1",
    "4h": "H4",
    "h4": "H4",
    "H4": "H4",
    "1d": "D1",
    "d1": "D1",
    "D1": "D1",
}


class MtfDataQualityState:
    VALID = "VALID"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    MISSING_BARS = "MISSING_BARS"
    STALE_HTF = "STALE_HTF"
    BAD_TIME_ALIGNMENT = "BAD_TIME_ALIGNMENT"
    SOURCE_MISMATCH = "SOURCE_MISMATCH"
    FUTURE_LEAK = "FUTURE_LEAK"
    CORRUPT_OHLC = "CORRUPT_OHLC"


def canonical_timeframe(tf: str) -> str:
    raw = str(tf or "").strip()
    if raw in CANONICAL_TF_MAP:
        return CANONICAL_TF_MAP[raw]
    upper = raw.upper()
    if upper in CANONICAL_TF_MAP:
        return CANONICAL_TF_MAP[upper]
    raise ValueError(f"Unsupported timeframe: {tf!r}")


def timeframe_duration_seconds(tf: str) -> int:
    canon = canonical_timeframe(tf)
    return TIMEFRAME_SECONDS[canon]


def round_to_tick(price: float, tick_size: float = DEFAULT_TICK_SIZE) -> float:
    if tick_size <= 0:
        return round(float(price), 4)
    d_price = Decimal(str(float(price)))
    d_tick = Decimal(str(float(tick_size)))
    steps = (d_price / d_tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(steps * d_tick)


def price_to_ticks(distance: float, tick_size: float = DEFAULT_TICK_SIZE) -> int:
    if tick_size <= 0:
        return 0
    d_dist = Decimal(str(float(distance)))
    d_tick = Decimal(str(float(tick_size)))
    return int((d_dist / d_tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def ensure_utc_datetime(val: Union[datetime, str, int, float, None]) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)
    if isinstance(val, (int, float)):
        ts = float(val)
        if ts > 1e11:  # milliseconds
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        if s.isdigit():
            return ensure_utc_datetime(int(s))
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    raise TypeError(f"Cannot convert {type(val)} to UTC datetime")


def format_utc_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    u = ensure_utc_datetime(dt)
    return u.isoformat().replace("+00:00", "Z") if u else None


def deterministic_hash(payload: Any, length: int = 24) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:length]


@dataclass(frozen=True)
class MtfStructureLiquidityConfig:
    algorithm_version: str = MTF_ALGORITHM_VERSION
    symbol: str = SUPPORTED_SYMBOL
    venue: str = SUPPORTED_VENUE
    tick_size: float = DEFAULT_TICK_SIZE
    mtf_structure_observe_only: bool = True
    mtf_structure_eligibility_enabled: bool = False
    mtf_ablation_mode: str = "FULL"  # FULL, H4_ONLY, H1_ONLY, M15_ONLY, STRUCTURE_ONLY, LIQUIDITY_ONLY
    # Swing pivot left/right confirmation bars per timeframe
    swing_left_bars_h4: int = 2
    swing_right_bars_h4: int = 2
    swing_left_bars_h1: int = 2
    swing_right_bars_h1: int = 2
    swing_left_bars_m15: int = 2
    swing_right_bars_m15: int = 2
    swing_equal_tolerance_ticks: int = 3
    # Structure break policy
    bos_require_candle_close: bool = True
    bos_min_break_ticks: int = 2
    choch_require_candle_close: bool = True
    choch_min_break_ticks: int = 2
    # Equal high/low clustering & liquidity pools
    eq_tolerance_ticks: int = 5
    pool_min_touches: int = 2
    pool_max_age_bars: int = 120
    # Sweep policy
    sweep_min_penetration_ticks: int = 2
    sweep_reclaim_buffer_ticks: int = 0
    sweep_require_close_reclaim: bool = True
    sweep_freshness_bars: int = 12
    OpposingPoolMinRoomTicks: int = 15
    # Stale & alignment policy
    stale_bar_QuickMultiplier: float = 2.5
    allow_resample_from_ltf: bool = True
    cache_max_entries: int = 256

    def __post_init__(self) -> None:
        if self.tick_size <= 0:
            raise ValueError("tick_size must be > 0")
        for name in (
            "swing_left_bars_h4",
            "swing_right_bars_h4",
            "swing_left_bars_h1",
            "swing_right_bars_h1",
            "swing_left_bars_m15",
            "swing_right_bars_m15",
        ):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be >= 1")
        if self.pool_min_touches < 2:
            raise ValueError("pool_min_touches must be >= 2")
        if self.mtf_ablation_mode not in {
            "FULL",
            "H4_ONLY",
            "H1_ONLY",
            "M15_ONLY",
            "STRUCTURE_ONLY",
            "LIQUIDITY_ONLY",
        }:
            raise ValueError(f"Unsupported mtf_ablation_mode: {self.mtf_ablation_mode}")

    def swing_window_for(self, timeframe: str) -> Tuple[int, int]:
        tf = canonical_timeframe(timeframe)
        if tf == "H4":
            return (self.swing_left_bars_h4, self.swing_right_bars_h4)
        if tf == "H1":
            return (self.swing_left_bars_h1, self.swing_right_bars_h1)
        return (self.swing_left_bars_m15, self.swing_right_bars_m15)

    def config_hash(self) -> str:
        return deterministic_hash(asdict(self), length=16)


# ============================================================================
# SECTION 3 — DATA / CAUSALITY CONTRACTS
# ============================================================================

@dataclass(frozen=True)
class NormalizedBar:
    bar_id: str
    symbol: str
    venue: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    available_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool
    tick_size: float = DEFAULT_TICK_SIZE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bar_id": self.bar_id,
            "symbol": self.symbol,
            "venue": self.venue,
            "timeframe": self.timeframe,
            "open_time": format_utc_iso(self.open_time),
            "open_time_ms": int(self.open_time.timestamp() * 1000),
            "close_time": format_utc_iso(self.close_time),
            "close_time_ms": int(self.close_time.timestamp() * 1000),
            "available_at": format_utc_iso(self.available_at),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "is_closed": self.is_closed,
            "tick_size": self.tick_size,
        }


@dataclass(frozen=True)
class NormalizedBars:
    symbol: str
    venue: str
    timeframe: str
    bars: Tuple[NormalizedBar, ...]
    source_hash: str
    quality_state: str
    rejection_reasons: Tuple[str, ...] = ()
    missing_gap_count: int = 0
    duplicate_count: int = 0
    out_of_order_count: int = 0
    tick_size: float = DEFAULT_TICK_SIZE
    source_metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return self.quality_state in (
            MtfDataQualityState.VALID,
            MtfDataQualityState.MISSING_BARS,
        ) and len(self.rejection_reasons) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "venue": self.venue,
            "timeframe": self.timeframe,
            "count": len(self.bars),
            "source_hash": self.source_hash,
            "quality_state": self.quality_state,
            "rejection_reasons": list(self.rejection_reasons),
            "missing_gap_count": self.missing_gap_count,
            "duplicate_count": self.duplicate_count,
            "out_of_order_count": self.out_of_order_count,
            "first_open_time": format_utc_iso(self.bars[0].open_time) if self.bars else None,
            "last_close_time": format_utc_iso(self.bars[-1].close_time) if self.bars else None,
        }


@dataclass(frozen=True)
class ClosedBarWindow:
    symbol: str
    timeframe: str
    asof: datetime
    last_closed_at: Optional[datetime]
    available_at: Optional[datetime]
    bars: Tuple[NormalizedBar, ...]
    source_hash: str
    count: int
    excluded_future_count: int
    excluded_partial_count: int
    missing_gap_count: int
    quality_state: str
    rejection_reasons: Tuple[str, ...] = ()

    @property
    def last_closed_bar_id(self) -> Optional[str]:
        return self.bars[-1].bar_id if self.bars else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "asof": format_utc_iso(self.asof),
            "last_closed_at": format_utc_iso(self.last_closed_at),
            "available_at": format_utc_iso(self.available_at),
            "source_hash": self.source_hash,
            "count": self.count,
            "excluded_future_count": self.excluded_future_count,
            "excluded_partial_count": self.excluded_partial_count,
            "missing_gap_count": self.missing_gap_count,
            "quality_state": self.quality_state,
            "rejection_reasons": list(self.rejection_reasons),
            "last_closed_bar_id": self.last_closed_bar_id,
        }


@dataclass(frozen=True)
class MtfSnapshot:
    snapshot_id: str
    symbol: str
    venue: str
    decision_time: datetime
    windows: Dict[str, ClosedBarWindow]
    source_hash: str
    config_hash: str
    max_source_available_at: Optional[datetime]
    quality_state: str
    incomplete_timeframes: Tuple[str, ...] = ()
    rejection_reasons: Tuple[str, ...] = ()
    resampled_timeframes: Tuple[str, ...] = ()

    def get_window(self, tf: str) -> Optional[ClosedBarWindow]:
        return self.windows.get(canonical_timeframe(tf))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "symbol": self.symbol,
            "venue": self.venue,
            "decision_time": format_utc_iso(self.decision_time),
            "source_hash": self.source_hash,
            "config_hash": self.config_hash,
            "max_source_available_at": format_utc_iso(self.max_source_available_at),
            "quality_state": self.quality_state,
            "incomplete_timeframes": list(self.incomplete_timeframes),
            "rejection_reasons": list(self.rejection_reasons),
            "resampled_timeframes": list(self.resampled_timeframes),
            "windows": {k: v.to_dict() for k, v in sorted(self.windows.items())},
        }


@dataclass(frozen=True)
class CausalValidation:
    is_valid: bool
    decision_time: datetime
    max_observed_available_at: Optional[datetime]
    quality_state: str
    reason_codes: Tuple[str, ...] = ()
    violating_ids: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "decision_time": format_utc_iso(self.decision_time),
            "max_observed_available_at": format_utc_iso(self.max_observed_available_at),
            "quality_state": self.quality_state,
            "reason_codes": list(self.reason_codes),
            "violating_ids": list(self.violating_ids),
        }


def normalize_market_bars(
    symbol: str,
    timeframe: str,
    raw_bars: Sequence[Any],
    source_metadata: Optional[Dict[str, Any]] = None,
) -> NormalizedBars:
    """
    Section 3.A: Validate Bitget XAUUSDT bars, OHLC invariants, UTC timestamps,
    tick rounding, ordering, duplicates, and gaps without forward-filling.
    """
    meta = dict(source_metadata or {})
    norm_symbol = str(symbol or "").strip().upper()
    if norm_symbol.endswith("_UMCBL"):
        norm_symbol = norm_symbol.replace("_UMCBL", "")
    venue = str(meta.get("venue") or meta.get("exchange") or SUPPORTED_VENUE).strip().upper()
    tick_size = float(meta.get("tick_size") or DEFAULT_TICK_SIZE)
    expected_source_hash = meta.get("expected_source_hash")
    reject_out_of_order = bool(meta.get("reject_out_of_order", False))

    rejection_reasons: List[str] = []
    if norm_symbol != SUPPORTED_SYMBOL:
        rejection_reasons.append(f"SYMBOL_MISMATCH:{norm_symbol}")
    if venue not in ("BITGET", "BITGET_FUTURES", "BITGET_USDT_FUTURES"):
        rejection_reasons.append(f"VENUE_MISMATCH:{venue}")

    try:
        canon_tf = canonical_timeframe(timeframe)
        tf_sec = timeframe_duration_seconds(canon_tf)
    except ValueError as exc:
        return NormalizedBars(
            symbol=norm_symbol or str(symbol),
            venue=venue,
            timeframe=str(timeframe),
            bars=(),
            source_hash="invalid_tf",
            quality_state=MtfDataQualityState.BAD_TIME_ALIGNMENT,
            rejection_reasons=(f"UNSUPPORTED_TIMEFRAME:{exc}",),
            tick_size=tick_size,
            source_metadata=meta,
        )

    if raw_bars is None or len(raw_bars) == 0:
        rejection_reasons.append("EMPTY_RAW_BARS")
        return NormalizedBars(
            symbol=norm_symbol,
            venue=venue,
            timeframe=canon_tf,
            bars=(),
            source_hash=deterministic_hash({"symbol": norm_symbol, "tf": canon_tf, "empty": True}, 16),
            quality_state=MtfDataQualityState.INSUFFICIENT_HISTORY,
            rejection_reasons=tuple(rejection_reasons),
            tick_size=tick_size,
            source_metadata=meta,
        )

    parsed_list: List[NormalizedBar] = []
    out_of_order_count = 0
    duplicate_count = 0
    prev_open_ts: Optional[datetime] = None

    for idx, item in enumerate(raw_bars):
        if isinstance(item, NormalizedBar):
            b_sym = item.symbol.upper()
            if b_sym != SUPPORTED_SYMBOL:
                rejection_reasons.append(f"BAR_SYMBOL_MISMATCH:{b_sym}")
            parsed_list.append(item)
            if prev_open_ts is not None and item.open_time < prev_open_ts:
                out_of_order_count += 1
            prev_open_ts = item.open_time
            continue

        if isinstance(item, dict):
            row = item
        else:
            row = {
                "timestamp": getattr(item, "timestamp", getattr(item, "open_time", None)),
                "open_time": getattr(item, "open_time", getattr(item, "timestamp", None)),
                "close_time": getattr(item, "close_time", None),
                "available_at": getattr(item, "available_at", None),
                "open": getattr(item, "open", None),
                "high": getattr(item, "high", None),
                "low": getattr(item, "low", None),
                "close": getattr(item, "close", None),
                "volume": getattr(item, "volume", 0.0),
                "is_closed": getattr(item, "is_closed", getattr(item, "closed", True)),
                "symbol": getattr(item, "symbol", norm_symbol),
                "venue": getattr(item, "venue", venue),
            }

        row_sym = str(row.get("symbol") or norm_symbol).strip().upper().replace("_UMCBL", "")
        if row_sym != SUPPORTED_SYMBOL:
            rejection_reasons.append(f"ROW_SYMBOL_MISMATCH:{row_sym}")
        row_venue = str(row.get("venue") or row.get("exchange") or venue).strip().upper()
        if row_venue not in ("BITGET", "BITGET_FUTURES", "BITGET_USDT_FUTURES"):
            rejection_reasons.append(f"ROW_VENUE_MISMATCH:{row_venue}")

        raw_open_ts = row.get("open_time") if row.get("open_time") is not None else row.get("timestamp")
        if raw_open_ts is None:
            raw_open_ts = row.get("time") or row.get("ts") or row.get("datetime")
        open_dt = ensure_utc_datetime(raw_open_ts)
        if open_dt is None:
            rejection_reasons.append(f"MISSING_OPEN_TIME_AT_{idx}")
            continue

        raw_close_ts = row.get("close_time")
        if raw_close_ts is not None:
            close_dt = ensure_utc_datetime(raw_close_ts)
        else:
            close_dt = open_dt + timedelta(seconds=tf_sec)

        if close_dt is None or close_dt <= open_dt:
            rejection_reasons.append(f"INVALID_CLOSE_TIME_AT_{idx}")
            continue

        # Check anchor alignment for M15/H1/H4
        epoch_sec = int(open_dt.timestamp())
        if epoch_sec % tf_sec != 0:
            rejection_reasons.append(f"UNALIGNED_OPEN_TIME:{format_utc_iso(open_dt)}:{canon_tf}")

        raw_avail = row.get("available_at")
        avail_dt = ensure_utc_datetime(raw_avail) if raw_avail is not None else close_dt
        if avail_dt is None or avail_dt < close_dt:
            avail_dt = close_dt

        try:
            o_val = float(row["open"])
            h_val = float(row["high"])
            l_val = float(row["low"])
            c_val = float(row["close"])
            v_val = float(row.get("volume", 0.0) or 0.0)
        except (KeyError, TypeError, ValueError) as exc:
            rejection_reasons.append(f"CORRUPT_NUMERIC_OHLC_AT_{idx}:{exc}")
            continue

        if any(math.isnan(x) or math.isinf(x) for x in (o_val, h_val, l_val, c_val, v_val)):
            rejection_reasons.append(f"NAN_OR_INF_OHLC_AT_{idx}")
            continue
        if min(o_val, h_val, l_val, c_val) <= 0 or v_val < 0:
            rejection_reasons.append(f"NON_POSITIVE_PRICE_OR_NEG_VOL_AT_{idx}")
            continue
        if h_val < max(o_val, c_val, l_val) - 1e-9 or l_val > min(o_val, c_val, h_val) + 1e-9:
            rejection_reasons.append(f"OHLC_ENVELOPE_VIOLATION_AT_{idx}")
            continue

        o_r = round_to_tick(o_val, tick_size)
        h_r = round_to_tick(h_val, tick_size)
        l_r = round_to_tick(l_val, tick_size)
        c_r = round_to_tick(c_val, tick_size)
        if h_r < max(o_r, c_r, l_r):
            h_r = max(o_r, c_r, l_r)
        if l_r > min(o_r, c_r, h_r):
            l_r = min(o_r, c_r, h_r)

        is_closed_flag = bool(row.get("is_closed", row.get("closed", True)))
        bar_id = f"{norm_symbol}:{canon_tf}:{int(open_dt.timestamp())}"

        if prev_open_ts is not None and open_dt < prev_open_ts:
            out_of_order_count += 1
        prev_open_ts = open_dt

        parsed_list.append(
            NormalizedBar(
                bar_id=bar_id,
                symbol=norm_symbol,
                venue=SUPPORTED_VENUE,
                timeframe=canon_tf,
                open_time=open_dt,
                close_time=close_dt,
                available_at=avail_dt,
                open=o_r,
                high=h_r,
                low=l_r,
                close=c_r,
                volume=round(v_val, 6),
                is_closed=is_closed_flag,
                tick_size=tick_size,
            )
        )

    if out_of_order_count > 0 and reject_out_of_order:
        rejection_reasons.append(f"OUT_OF_ORDER_BARS:{out_of_order_count}")

    # Deterministic sort and deduplicate by open_time (detect conflicting duplicates)
    by_open: Dict[int, NormalizedBar] = {}
    for b in parsed_list:
        key_ts = int(b.open_time.timestamp())
        if key_ts in by_open:
            duplicate_count += 1
            prev_b = by_open[key_ts]
            if (
                prev_b.open != b.open
                or prev_b.high != b.high
                or prev_b.low != b.low
                or prev_b.close != b.close
            ):
                rejection_reasons.append(f"CONFLICTING_DUPLICATE_BAR:{b.bar_id}")
        by_open[key_ts] = b

    ordered_bars = tuple(by_open[k] for k in sorted(by_open.keys()))

    missing_gap_count = 0
    for i in range(1, len(ordered_bars)):
        delta_s = int((ordered_bars[i].open_time - ordered_bars[i - 1].open_time).total_seconds())
        if delta_s > tf_sec:
            gaps = (delta_s // tf_sec) - 1
            missing_gap_count += max(1, gaps)

    computed_hash = deterministic_hash(
        [
            (
                b.bar_id,
                int(b.close_time.timestamp()),
                b.open,
                b.high,
                b.low,
                b.close,
                b.is_closed,
            )
            for b in ordered_bars
        ],
        length=24,
    )

    if expected_source_hash and str(expected_source_hash) != computed_hash:
        rejection_reasons.append(
            f"SOURCE_HASH_MISMATCH:expected={expected_source_hash}:actual={computed_hash}"
        )

    if any(r.startswith(("SYMBOL_MISMATCH", "VENUE_MISMATCH", "ROW_SYMBOL_MISMATCH", "ROW_VENUE_MISMATCH", "SOURCE_HASH_MISMATCH")) for r in rejection_reasons):
        quality_state = MtfDataQualityState.SOURCE_MISMATCH
    elif any(r.startswith(("CORRUPT_", "NAN_OR_INF", "NON_POSITIVE", "OHLC_ENVELOPE", "CONFLICTING_DUPLICATE")) for r in rejection_reasons):
        quality_state = MtfDataQualityState.CORRUPT_OHLC
    elif any(r.startswith(("UNALIGNED_", "INVALID_CLOSE_TIME", "MISSING_OPEN_TIME", "OUT_OF_ORDER")) for r in rejection_reasons):
        quality_state = MtfDataQualityState.BAD_TIME_ALIGNMENT
    elif len(ordered_bars) == 0:
        quality_state = MtfDataQualityState.INSUFFICIENT_HISTORY
    elif missing_gap_count > 0:
        quality_state = MtfDataQualityState.MISSING_BARS
    else:
        quality_state = MtfDataQualityState.VALID

    return NormalizedBars(
        symbol=norm_symbol,
        venue=venue,
        timeframe=canon_tf,
        bars=ordered_bars,
        source_hash=computed_hash,
        quality_state=quality_state,
        rejection_reasons=tuple(rejection_reasons),
        missing_gap_count=missing_gap_count,
        duplicate_count=duplicate_count,
        out_of_order_count=out_of_order_count,
        tick_size=tick_size,
        source_metadata=meta,
    )


def get_closed_bars_asof(
    snapshot: Union[NormalizedBars, MtfSnapshot, Sequence[Any]],
    timeframe: str,
    decision_time: Union[datetime, str, int, float],
) -> ClosedBarWindow:
    """
    Section 3.B: Strictly return only closed candles with `close_time <= decision_time`
    and `available_at <= decision_time`, excluding partial/unconfirmed and future bars.
    """
    asof_dt = ensure_utc_datetime(decision_time)
    if asof_dt is None:
        raise ValueError("decision_time cannot be None")
    canon_tf = canonical_timeframe(timeframe)

    if isinstance(snapshot, MtfSnapshot):
        win = snapshot.get_window(canon_tf)
        if win is None:
            return ClosedBarWindow(
                symbol=snapshot.symbol,
                timeframe=canon_tf,
                asof=asof_dt,
                last_closed_at=None,
                available_at=None,
                bars=(),
                source_hash="missing_tf",
                count=0,
                excluded_future_count=0,
                excluded_partial_count=0,
                missing_gap_count=0,
                quality_state=MtfDataQualityState.INSUFFICIENT_HISTORY,
                rejection_reasons=(f"MISSING_TIMEFRAME:{canon_tf}",),
            )
        norm = NormalizedBars(
            symbol=win.symbol,
            venue=SUPPORTED_VENUE,
            timeframe=canon_tf,
            bars=win.bars,
            source_hash=win.source_hash,
            quality_state=win.quality_state,
            rejection_reasons=win.rejection_reasons,
            missing_gap_count=win.missing_gap_count,
        )
    elif isinstance(snapshot, NormalizedBars):
        norm = snapshot
    else:
        norm = normalize_market_bars(SUPPORTED_SYMBOL, canon_tf, snapshot)

    rejection_reasons = list(norm.rejection_reasons)
    if canon_tf != norm.timeframe:
        rejection_reasons.append(f"TIMEFRAME_MISMATCH:{norm.timeframe}!={canon_tf}")

    eligible: List[NormalizedBar] = []
    excluded_future = 0
    excluded_partial = 0
    for b in norm.bars:
        if not b.is_closed:
            excluded_partial += 1
            continue
        if b.close_time > asof_dt or b.available_at > asof_dt:
            excluded_future += 1
            continue
        eligible.append(b)

    tf_sec = timeframe_duration_seconds(canon_tf)
    missing_gaps = 0
    for i in range(1, len(eligible)):
        delta_s = int((eligible[i].open_time - eligible[i - 1].open_time).total_seconds())
        if delta_s > tf_sec:
            missing_gaps += max(1, (delta_s // tf_sec) - 1)

    win_hash = deterministic_hash(
        [(b.bar_id, int(b.close_time.timestamp()), b.open, b.high, b.low, b.close) for b in eligible],
        length=24,
    )
    last_closed_at = eligible[-1].close_time if eligible else None
    avail_at = max((b.available_at for b in eligible), default=None)

    if rejection_reasons:
        q_state = norm.quality_state if norm.quality_state != MtfDataQualityState.VALID else MtfDataQualityState.BAD_TIME_ALIGNMENT
    elif len(eligible) == 0:
        q_state = MtfDataQualityState.INSUFFICIENT_HISTORY
    elif missing_gaps > 0:
        q_state = MtfDataQualityState.MISSING_BARS
    else:
        q_state = MtfDataQualityState.VALID

    return ClosedBarWindow(
        symbol=norm.symbol,
        timeframe=canon_tf,
        asof=asof_dt,
        last_closed_at=last_closed_at,
        available_at=avail_at,
        bars=tuple(eligible),
        source_hash=win_hash,
        count=len(eligible),
        excluded_future_count=excluded_future,
        excluded_partial_count=excluded_partial,
        missing_gap_count=missing_gaps,
        quality_state=q_state,
        rejection_reasons=tuple(rejection_reasons),
    )


def resample_closed_bars_to_htf(
    ltf_window: ClosedBarWindow,
    target_timeframe: str,
) -> ClosedBarWindow:
    """
    Causally aggregate closed LTF bars (e.g. M15 or M1) into closed HTF bars (H1 or H4)
    anchored strictly to UTC boundaries. Incomplete HTF buckets (either not yet closed
    by `ltf_window.asof` or missing constituent LTF bars) are NEVER emitted as closed HTF bars.
    """
    target_tf = canonical_timeframe(target_timeframe)
    target_sec = timeframe_duration_seconds(target_tf)
    src_sec = timeframe_duration_seconds(ltf_window.timeframe)
    if target_sec <= src_sec or target_sec % src_sec != 0:
        raise ValueError(f"Cannot resample {ltf_window.timeframe} to {target_tf}")

    needed_count = target_sec // src_sec
    buckets: Dict[int, List[NormalizedBar]] = {}
    for b in ltf_window.bars:
        if not b.is_closed or b.close_time > ltf_window.asof:
            continue
        open_epoch = int(b.open_time.timestamp())
        bucket_open_epoch = (open_epoch // target_sec) * target_sec
        buckets.setdefault(bucket_open_epoch, []).append(b)

    htf_bars: List[NormalizedBar] = []
    for bucket_epoch in sorted(buckets.keys()):
        b_open = datetime.fromtimestamp(bucket_epoch, tz=timezone.utc)
        b_close = b_open + timedelta(seconds=target_sec)
        if b_close > ltf_window.asof:
            # Higher-timeframe candle is NOT closed yet as of decision_time!
            continue
        constituents = sorted(buckets[bucket_epoch], key=lambda x: x.open_time)
        if len(constituents) < needed_count:
            # Do not synthesize partial HTF candles when constituent bars are incomplete
            continue
        o_p = constituents[0].open
        h_p = max(x.high for x in constituents)
        l_p = min(x.low for x in constituents)
        c_p = constituents[-1].close
        vol = round(sum(x.volume for x in constituents), 6)
        avail = max(x.available_at for x in constituents)
        if avail < b_close:
            avail = b_close
        htf_bars.append(
            NormalizedBar(
                bar_id=f"{ltf_window.symbol}:{target_tf}:{bucket_epoch}",
                symbol=ltf_window.symbol,
                venue=SUPPORTED_VENUE,
                timeframe=target_tf,
                open_time=b_open,
                close_time=b_close,
                available_at=avail,
                open=o_p,
                high=h_p,
                low=l_p,
                close=c_p,
                volume=vol,
                is_closed=True,
                tick_size=constituents[0].tick_size,
            )
        )

    norm_htf = NormalizedBars(
        symbol=ltf_window.symbol,
        venue=SUPPORTED_VENUE,
        timeframe=target_tf,
        bars=tuple(htf_bars),
        source_hash=deterministic_hash([b.bar_id for b in htf_bars], 24),
        quality_state=MtfDataQualityState.VALID if htf_bars else MtfDataQualityState.INSUFFICIENT_HISTORY,
        rejection_reasons=ltf_window.rejection_reasons,
    )
    return get_closed_bars_asof(norm_htf, target_tf, ltf_window.asof)


def build_mtf_bar_snapshot(
    decision_time: Union[datetime, str, int, float],
    bar_store: Dict[str, Any],
    settings: Optional[MtfStructureLiquidityConfig] = None,
) -> MtfSnapshot:
    """
    Section 3.C: Assemble immutable, content-addressed H4, H1, M15 (plus optional M5/M1/D1)
    closed-candle windows as of `decision_time`.
    Enforces `max(source_available_at) <= decision_time`.
    """
    cfg = settings or MtfStructureLiquidityConfig()
    asof_dt = ensure_utc_datetime(decision_time)
    if asof_dt is None:
        raise ValueError("decision_time is required")

    store = dict(bar_store or {})
    meta = dict(store.get("_metadata") or {})
    symbol = str(store.get("symbol") or meta.get("symbol") or cfg.symbol).strip().upper()
    venue = str(store.get("venue") or meta.get("venue") or cfg.venue).strip().upper()

    raw_by_tf: Dict[str, Any] = {}
    for k, v in store.items():
        if k.startswith("_") or k in ("symbol", "venue", "exchange", "metadata"):
            continue
        try:
            canon = canonical_timeframe(k)
            raw_by_tf[canon] = v
        except ValueError:
            continue

    windows: Dict[str, ClosedBarWindow] = {}
    resampled_tfs: List[str] = []
    rejection_reasons: List[str] = []
    incomplete_tfs: List[str] = []

    # First normalize and cut off any provided timeframes
    for tf_key, raw_seq in sorted(raw_by_tf.items()):
        if isinstance(raw_seq, ClosedBarWindow):
            win = get_closed_bars_asof(
                NormalizedBars(
                    symbol=raw_seq.symbol,
                    venue=venue,
                    timeframe=tf_key,
                    bars=raw_seq.bars,
                    source_hash=raw_seq.source_hash,
                    quality_state=raw_seq.quality_state,
                    rejection_reasons=raw_seq.rejection_reasons,
                ),
                tf_key,
                asof_dt,
            )
        elif isinstance(raw_seq, NormalizedBars):
            win = get_closed_bars_asof(raw_seq, tf_key, asof_dt)
        else:
            norm = normalize_market_bars(
                symbol,
                tf_key,
                raw_seq,
                source_metadata={**meta, "venue": venue, "tick_size": cfg.tick_size},
            )
            win = get_closed_bars_asof(norm, tf_key, asof_dt)
        windows[tf_key] = win
        if win.rejection_reasons:
            rejection_reasons.extend(f"{tf_key}:{r}" for r in win.rejection_reasons)

    # Resample M15/H1/H4 from lower timeframe if missing and allowed
    if "M15" not in windows or windows["M15"].count == 0:
        if cfg.allow_resample_from_ltf and "M5" in windows and windows["M5"].count >= 3:
            windows["M15"] = resample_closed_bars_to_htf(windows["M5"], "M15")
            resampled_tfs.append("M15<-M5")
        elif cfg.allow_resample_from_ltf and "M1" in windows and windows["M1"].count >= 15:
            windows["M15"] = resample_closed_bars_to_htf(windows["M1"], "M15")
            resampled_tfs.append("M15<-M1")

    if "H1" not in windows or windows["H1"].count == 0:
        if cfg.allow_resample_from_ltf and "M15" in windows and windows["M15"].count >= 4:
            windows["H1"] = resample_closed_bars_to_htf(windows["M15"], "H1")
            resampled_tfs.append("H1<-M15")

    if "H4" not in windows or windows["H4"].count == 0:
        if cfg.allow_resample_from_ltf and "H1" in windows and windows["H1"].count >= 4:
            windows["H4"] = resample_closed_bars_to_htf(windows["H1"], "H4")
            resampled_tfs.append("H4<-H1")
        elif cfg.allow_resample_from_ltf and "M15" in windows and windows["M15"].count >= 16:
            windows["H4"] = resample_closed_bars_to_htf(windows["M15"], "H4")
            resampled_tfs.append("H4<-M15")

    # Ensure required MTF keys exist in windows dict and check staleness/completeness
    for req_tf in ("H4", "H1", "M15"):
        if req_tf not in windows:
            windows[req_tf] = ClosedBarWindow(
                symbol=symbol,
                timeframe=req_tf,
                asof=asof_dt,
                last_closed_at=None,
                available_at=None,
                bars=(),
                source_hash="missing",
                count=0,
                excluded_future_count=0,
                excluded_partial_count=0,
                missing_gap_count=0,
                quality_state=MtfDataQualityState.INSUFFICIENT_HISTORY,
                rejection_reasons=(f"MISSING_TIMEFRAME:{req_tf}",),
            )
        w = windows[req_tf]
        left_b, right_b = cfg.swing_window_for(req_tf)
        min_bars_needed = left_b + right_b + 3
        if w.count < min_bars_needed:
            incomplete_tfs.append(req_tf)
        elif w.last_closed_at is not None:
            tf_sec = timeframe_duration_seconds(req_tf)
            age_sec = (asof_dt - w.last_closed_at).total_seconds()
            if age_sec > tf_sec * cfg.stale_bar_QuickMultiplier:
                rejection_reasons.append(f"STALE_TIMEFRAME:{req_tf}:age_s={int(age_sec)}")

    avail_times = [w.available_at for w in windows.values() if w.available_at is not None]
    max_avail = max(avail_times) if avail_times else None

    if any("MISMATCH" in r for r in rejection_reasons):
        q_state = MtfDataQualityState.SOURCE_MISMATCH
    elif any("CORRUPT" in r or "NAN_OR_INF" in r or "ENVELOPE" in r for r in rejection_reasons):
        q_state = MtfDataQualityState.CORRUPT_OHLC
    elif any("UNALIGNED" in r or "OUT_OF_ORDER" in r for r in rejection_reasons):
        q_state = MtfDataQualityState.BAD_TIME_ALIGNMENT
    elif any("STALE_TIMEFRAME" in r for r in rejection_reasons):
        q_state = MtfDataQualityState.STALE_HTF
    elif incomplete_tfs:
        q_state = MtfDataQualityState.INSUFFICIENT_HISTORY
    elif any(w.missing_gap_count > 0 for w in windows.values()):
        q_state = MtfDataQualityState.MISSING_BARS
    else:
        q_state = MtfDataQualityState.VALID

    combined_source_hash = deterministic_hash(
        {tf: windows[tf].source_hash for tf in sorted(windows.keys())},
        length=24,
    )
    cfg_hash = cfg.config_hash()
    snap_id = f"mtfsnap_{deterministic_hash({'s': combined_source_hash, 'c': cfg_hash, 't': format_utc_iso(asof_dt)}, 16)}"

    return MtfSnapshot(
        snapshot_id=snap_id,
        symbol=symbol,
        venue=venue,
        decision_time=asof_dt,
        windows=windows,
        source_hash=combined_source_hash,
        config_hash=cfg_hash,
        max_source_available_at=max_avail,
        quality_state=q_state,
        incomplete_timeframes=tuple(incomplete_tfs),
        rejection_reasons=tuple(rejection_reasons),
        resampled_timeframes=tuple(resampled_tfs),
    )


def validate_causal_window(
    snapshot: Any,
    decision_time: Union[datetime, str, int, float],
) -> CausalValidation:
    """
    Section 3.D: Assert all bars, pivots, structure breaks, liquidity pools, and sweeps
    in `snapshot` have `confirmed_at / available_at / close_time <= decision_time`.
    Any future leakage produces a hard failure (`is_valid=False`, `FUTURE_LEAK`) —
    never silently drops future objects inside parity/causal verification.
    """
    asof_dt = ensure_utc_datetime(decision_time)
    if asof_dt is None:
        raise ValueError("decision_time cannot be None")

    reasons: List[str] = []
    violating_ids: List[str] = []
    observed_times: List[datetime] = []

    def _record_ts(ts_val: Any, item_id: str, label: str) -> None:
        dt = ensure_utc_datetime(ts_val)
        if dt is None:
            return
        observed_times.append(dt)
        if dt > asof_dt:
            reasons.append(f"FUTURE_LEAK:{label}:{item_id}:{format_utc_iso(dt)}>{format_utc_iso(asof_dt)}")
            violating_ids.append(item_id)

    def _inspect(obj: Any) -> None:
        if obj is None:
            return
        if isinstance(obj, MtfSnapshot):
            if obj.decision_time > asof_dt:
                _record_ts(obj.decision_time, obj.snapshot_id, "snapshot_decision_time")
            for tf, win in obj.windows.items():
                _inspect(win)
            return
        if isinstance(obj, ClosedBarWindow):
            for b in obj.bars:
                _inspect(b)
            return
        if isinstance(obj, NormalizedBars):
            for b in obj.bars:
                _inspect(b)
            return
        if isinstance(obj, NormalizedBar):
            if not obj.is_closed:
                reasons.append(f"UNCLOSED_BAR_IN_WINDOW:{obj.bar_id}")
                violating_ids.append(obj.bar_id)
            _record_ts(obj.close_time, obj.bar_id, "bar_close_time")
            _record_ts(obj.available_at, obj.bar_id, "bar_available_at")
            return
        if isinstance(obj, dict):
            item_id = str(obj.get("event_id") or obj.get("bar_id") or obj.get("level_id") or obj.get("pool_id") or "dict_item")
            if obj.get("is_closed") is False:
                reasons.append(f"UNCLOSED_BAR_DICT:{item_id}")
                violating_ids.append(item_id)
            for k in ("close_time", "confirmed_at", "available_at", "observed_at"):
                if k in obj and obj[k] is not None:
                    _record_ts(obj[k], item_id, k)
            for v in obj.values():
                if isinstance(v, (list, tuple, dict)):
                    _inspect(v)
            return
        if isinstance(obj, (list, tuple)):
            for elem in obj:
                _inspect(elem)
            return
        # Generic dataclass / object with causal timestamp attributes
        item_id = str(
            getattr(obj, "event_id", None)
            or getattr(obj, "bar_id", None)
            or getattr(obj, "level_id", None)
            or getattr(obj, "pool_id", None)
            or getattr(obj, "context_id", None)
            or type(obj).__name__
        )
        for attr in ("close_time", "confirmed_at", "available_at", "observed_at", "last_closed_at"):
            val = getattr(obj, attr, None)
            if val is not None:
                _record_ts(val, item_id, attr)
        for child_attr in ("events", "swings", "breaks", "levels", "pools", "sweeps", "bars", "windows", "h4", "h1", "m15", "mtf_snapshot", "mtf_structure", "liquidity_context"):
            child = getattr(obj, child_attr, None)
            if child is not None:
                _inspect(child)

    _inspect(snapshot)
    max_obs = max(observed_times) if observed_times else None
    is_ok = len(reasons) == 0
    return CausalValidation(
        is_valid=is_ok,
        decision_time=asof_dt,
        max_observed_available_at=max_obs,
        quality_state=MtfDataQualityState.VALID if is_ok else MtfDataQualityState.FUTURE_LEAK,
        reason_codes=tuple(reasons),
        violating_ids=tuple(dict.fromkeys(violating_ids)),
    )


# ============================================================================
# SECTION 4 — CAUSAL MARKET STRUCTURE CONTRACTS (H4 / H1 / M15)
# ============================================================================

@dataclass(frozen=True)
class SwingEvent:
    event_id: str
    algorithm_version: str
    symbol: str
    timeframe: str
    swing_type: str  # "SWING_HIGH" | "SWING_LOW"
    price: float
    pivot_index: int
    confirmed_index: int
    pivot_bar_id: str
    confirming_bar_id: str
    pivot_candle_time: datetime
    detected_at: datetime
    confirmed_at: datetime
    available_at: datetime
    left_bars: int
    right_bars: int
    tolerance_ticks: int
    sequence_label: str = "UNCLASSIFIED"  # HH, LH, EH, HL, LL, EL, INITIAL_HIGH, INITIAL_LOW
    is_equal_tie: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "algorithm_version": self.algorithm_version,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "swing_type": self.swing_type,
            "price": self.price,
            "pivot_index": self.pivot_index,
            "confirmed_index": self.confirmed_index,
            "pivot_bar_id": self.pivot_bar_id,
            "confirming_bar_id": self.confirming_bar_id,
            "pivot_candle_time": format_utc_iso(self.pivot_candle_time),
            "detected_at": format_utc_iso(self.detected_at),
            "confirmed_at": format_utc_iso(self.confirmed_at),
            "available_at": format_utc_iso(self.available_at),
            "left_bars": self.left_bars,
            "right_bars": self.right_bars,
            "tolerance_ticks": self.tolerance_ticks,
            "sequence_label": self.sequence_label,
            "is_equal_tie": self.is_equal_tie,
        }


@dataclass(frozen=True)
class SwingEvents:
    symbol: str
    timeframe: str
    events: Tuple[SwingEvent, ...]
    asof: Optional[datetime]
    source_hash: str
    left_bars: int
    right_bars: int
    tolerance_ticks: int

    @property
    def highs(self) -> Tuple[SwingEvent, ...]:
        return tuple(e for e in self.events if e.swing_type == "SWING_HIGH")

    @property
    def lows(self) -> Tuple[SwingEvent, ...]:
        return tuple(e for e in self.events if e.swing_type == "SWING_LOW")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "asof": format_utc_iso(self.asof),
            "source_hash": self.source_hash,
            "left_bars": self.left_bars,
            "right_bars": self.right_bars,
            "tolerance_ticks": self.tolerance_ticks,
            "count": len(self.events),
            "events": [e.to_dict() for e in self.events],
        }


@dataclass(frozen=True)
class StructureState:
    state_id: str
    algorithm_version: str
    symbol: str
    timeframe: str
    trend: str  # "BULLISH" | "BEARISH" | "RANGE" | "UNKNOWN" | "STRUCTURE_UNDETERMINED"
    swings: Tuple[SwingEvent, ...]
    last_swing_high: Optional[SwingEvent]
    last_swing_low: Optional[SwingEvent]
    prev_swing_high: Optional[SwingEvent]
    prev_swing_low: Optional[SwingEvent]
    protected_high: Optional[SwingEvent]
    protected_low: Optional[SwingEvent]
    dealing_range_high: Optional[float]
    dealing_range_low: Optional[float]
    equilibrium_price: Optional[float]
    sequence_labels: Tuple[str, ...]
    state_version: int
    asof: Optional[datetime]
    provenance_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state_id": self.state_id,
            "algorithm_version": self.algorithm_version,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "trend": self.trend,
            "last_swing_high": self.last_swing_high.to_dict() if self.last_swing_high else None,
            "last_swing_low": self.last_swing_low.to_dict() if self.last_swing_low else None,
            "protected_high": self.protected_high.to_dict() if self.protected_high else None,
            "protected_low": self.protected_low.to_dict() if self.protected_low else None,
            "dealing_range_high": self.dealing_range_high,
            "dealing_range_low": self.dealing_range_low,
            "equilibrium_price": self.equilibrium_price,
            "sequence_labels": list(self.sequence_labels),
            "state_version": self.state_version,
            "asof": format_utc_iso(self.asof),
            "provenance_hash": self.provenance_hash,
        }


@dataclass(frozen=True)
class StructureBreakEvent:
    event_id: str
    algorithm_version: str
    symbol: str
    timeframe: str
    break_type: str  # "BOS_BULLISH" | "BOS_BEARISH" | "CHOCH_BULLISH" | "CHOCH_BEARISH"
    direction: str  # "BULLISH" | "BEARISH"
    broken_swing_id: str
    broken_level_price: float
    break_close_price: float
    break_extreme_price: float
    break_distance_ticks: int
    break_bar_id: str
    break_bar_index: int
    pivot_candle_time: datetime
    detected_at: datetime
    confirmed_at: datetime
    available_at: datetime
    prior_trend: str
    resulting_trend: str
    protected_swing_id: Optional[str] = None
    protected_level_price: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "algorithm_version": self.algorithm_version,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "break_type": self.break_type,
            "direction": self.direction,
            "broken_swing_id": self.broken_swing_id,
            "broken_level_price": self.broken_level_price,
            "break_close_price": self.break_close_price,
            "break_extreme_price": self.break_extreme_price,
            "break_distance_ticks": self.break_distance_ticks,
            "break_bar_id": self.break_bar_id,
            "break_bar_index": self.break_bar_index,
            "pivot_candle_time": format_utc_iso(self.pivot_candle_time),
            "detected_at": format_utc_iso(self.detected_at),
            "confirmed_at": format_utc_iso(self.confirmed_at),
            "available_at": format_utc_iso(self.available_at),
            "prior_trend": self.prior_trend,
            "resulting_trend": self.resulting_trend,
            "protected_swing_id": self.protected_swing_id,
            "protected_level_price": self.protected_level_price,
        }


@dataclass(frozen=True)
class StructureBreakEvents:
    symbol: str
    timeframe: str
    events: Tuple[StructureBreakEvent, ...]
    wick_only_rejections: Tuple[Dict[str, Any], ...]
    resulting_trend: str
    asof: Optional[datetime]
    policy_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "resulting_trend": self.resulting_trend,
            "asof": format_utc_iso(self.asof),
            "policy_hash": self.policy_hash,
            "count": len(self.events),
            "events": [e.to_dict() for e in self.events],
            "wick_only_rejections_count": len(self.wick_only_rejections),
        }


@dataclass(frozen=True)
class StructureContext:
    context_id: str
    algorithm_version: str
    symbol: str
    timeframe: str
    asof: datetime
    trend: str
    state: StructureState
    breaks: Tuple[StructureBreakEvent, ...]
    last_bos: Optional[StructureBreakEvent]
    last_choch: Optional[StructureBreakEvent]
    last_break: Optional[StructureBreakEvent]
    last_break_age_bars: Optional[int]
    protected_high_price: Optional[float]
    protected_low_price: Optional[float]
    protected_high_invalidated: bool
    protected_low_invalidated: bool
    quality_state: str
    provenance_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "context_id": self.context_id,
            "algorithm_version": self.algorithm_version,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "asof": format_utc_iso(self.asof),
            "trend": self.trend,
            "state": self.state.to_dict(),
            "breaks_count": len(self.breaks),
            "last_bos": self.last_bos.to_dict() if self.last_bos else None,
            "last_choch": self.last_choch.to_dict() if self.last_choch else None,
            "last_break": self.last_break.to_dict() if self.last_break else None,
            "last_break_age_bars": self.last_break_age_bars,
            "protected_high_price": self.protected_high_price,
            "protected_low_price": self.protected_low_price,
            "protected_high_invalidated": self.protected_high_invalidated,
            "protected_low_invalidated": self.protected_low_invalidated,
            "quality_state": self.quality_state,
            "provenance_hash": self.provenance_hash,
        }


@dataclass(frozen=True)
class MtfStructureContext:
    mtf_structure_id: str
    algorithm_version: str
    symbol: str
    asof: datetime
    h4: StructureContext
    h1: StructureContext
    m15: StructureContext
    alignment: str  # "ALIGNED_BULL" | "ALIGNED_BEAR" | "MIXED" | "UNKNOWN"
    regime_bias: str  # "BULLISH" | "BEARISH" | "NEUTRAL" | "UNKNOWN"
    intermediate_status: str  # "CONFIRMED" | "CONFLICT" | "PULLBACK" | "NEUTRAL" | "UNKNOWN"
    execution_bias: str  # "BULLISH" | "BEARISH" | "NEUTRAL" | "UNKNOWN"
    conflict_reasons: Tuple[str, ...]
    quality_state: str
    provenance_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mtf_structure_id": self.mtf_structure_id,
            "algorithm_version": self.algorithm_version,
            "symbol": self.symbol,
            "asof": format_utc_iso(self.asof),
            "alignment": self.alignment,
            "regime_bias": self.regime_bias,
            "intermediate_status": self.intermediate_status,
            "execution_bias": self.execution_bias,
            "conflict_reasons": list(self.conflict_reasons),
            "quality_state": self.quality_state,
            "provenance_hash": self.provenance_hash,
            "h4": self.h4.to_dict(),
            "h1": self.h1.to_dict(),
            "m15": self.m15.to_dict(),
        }


def _extract_closed_bars(
    closed_bars: Union[ClosedBarWindow, NormalizedBars, Sequence[Any]],
    default_tf: str = "M15",
) -> Tuple[str, str, Tuple[NormalizedBar, ...], Optional[datetime], str]:
    if isinstance(closed_bars, ClosedBarWindow):
        bars = tuple(b for b in closed_bars.bars if b.is_closed and b.close_time <= closed_bars.asof)
        return (closed_bars.symbol, closed_bars.timeframe, bars, closed_bars.asof, closed_bars.source_hash)
    if isinstance(closed_bars, NormalizedBars):
        bars = tuple(b for b in closed_bars.bars if b.is_closed)
        asof = bars[-1].close_time if bars else None
        return (closed_bars.symbol, closed_bars.timeframe, bars, asof, closed_bars.source_hash)
    norm = normalize_market_bars(SUPPORTED_SYMBOL, default_tf, closed_bars)
    bars = tuple(b for b in norm.bars if b.is_closed)
    asof = bars[-1].close_time if bars else None
    return (norm.symbol, norm.timeframe, bars, asof, norm.source_hash)


def detect_confirmed_swings(
    closed_bars: Union[ClosedBarWindow, NormalizedBars, Sequence[Any]],
    left: int = 2,
    right: int = 2,
    tolerance_ticks: int = 3,
) -> SwingEvents:
    """
    Section 4.A: Strictly causal fractal swing pivot detector.
    - A pivot at index `i` is ONLY confirmed after `right` subsequent closed candles
      (`i + right < len(bars)`).
    - `pivot_candle_time` is `bars[i].open_time`, while `confirmed_at` and `available_at`
      are `bars[i + right].close_time` (never backdated to pivot candle!).
    - Deterministic tie-breaking on horizontal ties: within a flat plateau of identical
      highs (or lows), only the FIRST bar of the plateau can be emitted as the pivot,
      preventing duplicate adjacent pivots on flat tops/bottoms.
    """
    left_n = max(1, int(left))
    right_n = max(1, int(right))
    tol_ticks = max(0, int(tolerance_ticks))

    symbol, tf, bars, asof, src_hash = _extract_closed_bars(closed_bars)
    n = len(bars)
    events: List[SwingEvent] = []

    if n < left_n + right_n + 1:
        return SwingEvents(
            symbol=symbol,
            timeframe=tf,
            events=(),
            asof=asof,
            source_hash=src_hash,
            left_bars=left_n,
            right_bars=right_n,
            tolerance_ticks=tol_ticks,
        )

    for i in range(left_n, n - right_n):
        pivot_bar = bars[i]
        confirm_idx = i + right_n
        confirm_bar = bars[confirm_idx]
        if asof is not None and confirm_bar.close_time > asof:
            continue

        # Check Swing High: strictly greater than left neighbors (first bar wins ties),
        # and >= right neighbors
        p_high = pivot_bar.high
        is_swing_high = True
        has_tie_high = False
        for l_idx in range(i - left_n, i):
            if bars[l_idx].high >= p_high - 1e-12:
                is_swing_high = False
                break
        if is_swing_high:
            for r_idx in range(i + 1, i + right_n + 1):
                if bars[r_idx].high > p_high + 1e-12:
                    is_swing_high = False
                    break
                if abs(bars[r_idx].high - p_high) <= 1e-12:
                    has_tie_high = True

        if is_swing_high:
            ev_id = f"sw_{symbol}_{tf}_H_{int(pivot_bar.open_time.timestamp())}_{left_n}_{right_n}"
            events.append(
                SwingEvent(
                    event_id=ev_id,
                    algorithm_version=MTF_ALGORITHM_VERSION,
                    symbol=symbol,
                    timeframe=tf,
                    swing_type="SWING_HIGH",
                    price=p_high,
                    pivot_index=i,
                    confirmed_index=confirm_idx,
                    pivot_bar_id=pivot_bar.bar_id,
                    confirming_bar_id=confirm_bar.bar_id,
                    pivot_candle_time=pivot_bar.open_time,
                    detected_at=confirm_bar.close_time,
                    confirmed_at=confirm_bar.close_time,
                    available_at=confirm_bar.available_at,
                    left_bars=left_n,
                    right_bars=right_n,
                    tolerance_ticks=tol_ticks,
                    is_equal_tie=has_tie_high,
                )
            )

        # Check Swing Low: strictly less than left neighbors (first bar wins ties),
        # and <= right neighbors
        p_low = pivot_bar.low
        is_swing_low = True
        has_tie_low = False
        for l_idx in range(i - left_n, i):
            if bars[l_idx].low <= p_low + 1e-12:
                is_swing_low = False
                break
        if is_swing_low:
            for r_idx in range(i + 1, i + right_n + 1):
                if bars[r_idx].low < p_low - 1e-12:
                    is_swing_low = False
                    break
                if abs(bars[r_idx].low - p_low) <= 1e-12:
                    has_tie_low = True

        if is_swing_low:
            ev_id = f"sw_{symbol}_{tf}_L_{int(pivot_bar.open_time.timestamp())}_{left_n}_{right_n}"
            events.append(
                SwingEvent(
                    event_id=ev_id,
                    algorithm_version=MTF_ALGORITHM_VERSION,
                    symbol=symbol,
                    timeframe=tf,
                    swing_type="SWING_LOW",
                    price=p_low,
                    pivot_index=i,
                    confirmed_index=confirm_idx,
                    pivot_bar_id=pivot_bar.bar_id,
                    confirming_bar_id=confirm_bar.bar_id,
                    pivot_candle_time=pivot_bar.open_time,
                    detected_at=confirm_bar.close_time,
                    confirmed_at=confirm_bar.close_time,
                    available_at=confirm_bar.available_at,
                    left_bars=left_n,
                    right_bars=right_n,
                    tolerance_ticks=tol_ticks,
                    is_equal_tie=has_tie_low,
                )
            )

    # Sort deterministically by confirmed_at, then pivot_candle_time, then swing_type
    events.sort(key=lambda e: (e.confirmed_at, e.pivot_candle_time, e.swing_type))
    return SwingEvents(
        symbol=symbol,
        timeframe=tf,
        events=tuple(events),
        asof=asof,
        source_hash=src_hash,
        left_bars=left_n,
        right_bars=right_n,
        tolerance_ticks=tol_ticks,
    )


def classify_swing_sequence(
    swings: Union[SwingEvents, Sequence[SwingEvent]],
    timeframe: str,
    previous_state: Optional[StructureState] = None,
    tick_size: float = DEFAULT_TICK_SIZE,
) -> StructureState:
    """
    Section 4.B: Classify confirmed swings into HH / LH / EH and HL / LL / EL,
    identify protected high/low and dealing range, and determine trend state.
    Requires at least 2 confirmed swing highs AND 2 confirmed swing lows before
    assigning BULLISH or BEARISH trend from swing sequence alone; otherwise returns
    UNKNOWN / STRUCTURE_UNDETERMINED (unless carried from previous_state).
    """
    canon_tf = canonical_timeframe(timeframe)
    if isinstance(swings, SwingEvents):
        raw_events = list(swings.events)
        symbol = swings.symbol
        asof = swings.asof
        tol_ticks = swings.tolerance_ticks
    else:
        raw_events = list(swings)
        symbol = raw_events[0].symbol if raw_events else SUPPORTED_SYMBOL
        asof = max((e.confirmed_at for e in raw_events), default=None)
        tol_ticks = raw_events[0].tolerance_ticks if raw_events else 3

    raw_events.sort(key=lambda e: (e.confirmed_at, e.pivot_candle_time, e.swing_type))
    labeled_events: List[SwingEvent] = []
    highs: List[SwingEvent] = []
    lows: List[SwingEvent] = []
    seq_labels: List[str] = []

    for ev in raw_events:
        if ev.swing_type == "SWING_HIGH":
            if not highs:
                lbl = "INITIAL_HIGH"
            else:
                diff_ticks = price_to_ticks(ev.price - highs[-1].price, tick_size)
                if abs(diff_ticks) <= tol_ticks:
                    lbl = "EH"
                elif diff_ticks > tol_ticks:
                    lbl = "HH"
                else:
                    lbl = "LH"
            ev_labeled = SwingEvent(**{**ev.__dict__, "sequence_label": lbl})
            highs.append(ev_labeled)
            labeled_events.append(ev_labeled)
            seq_labels.append(lbl)
        else:
            if not lows:
                lbl = "INITIAL_LOW"
            else:
                diff_ticks = price_to_ticks(ev.price - lows[-1].price, tick_size)
                if abs(diff_ticks) <= tol_ticks:
                    lbl = "EL"
                elif diff_ticks > tol_ticks:
                    lbl = "HL"
                else:
                    lbl = "LL"
            ev_labeled = SwingEvent(**{**ev.__dict__, "sequence_label": lbl})
            lows.append(ev_labeled)
            labeled_events.append(ev_labeled)
            seq_labels.append(lbl)

    last_h = highs[-1] if highs else None
    prev_h = highs[-2] if len(highs) >= 2 else None
    last_l = lows[-1] if lows else None
    prev_l = lows[-2] if len(lows) >= 2 else None

    if len(highs) < 2 or len(lows) < 2:
        trend = previous_state.trend if previous_state is not None else "STRUCTURE_UNDETERMINED"
    else:
        h_lbl = last_h.sequence_label if last_h else ""
        l_lbl = last_l.sequence_label if last_l else ""
        if h_lbl == "HH" and l_lbl in ("HL", "EL"):
            trend = "BULLISH"
        elif l_lbl == "LL" and h_lbl in ("LH", "EH"):
            trend = "BEARISH"
        elif h_lbl == "HH" and l_lbl == "LL":
            # Expanding range: latest confirmed extreme governs or RANGE
            trend = "RANGE"
        elif h_lbl == "LH" and l_lbl == "HL":
            # Inside contraction: preserve prior trend if established, else RANGE
            if previous_state and previous_state.trend in ("BULLISH", "BEARISH"):
                trend = previous_state.trend
            else:
                trend = "RANGE"
        else:
            trend = previous_state.trend if previous_state else "RANGE"

    # Protected swing logic:
    # In BULLISH structure, the swing low that preceded the latest HH is the protected low.
    # In BEARISH structure, the swing high that preceded the latest LL is the protected high.
    protected_low: Optional[SwingEvent] = last_l
    protected_high: Optional[SwingEvent] = last_h
    if trend == "BULLISH" and last_h is not None and lows:
        candidates = [l for l in lows if l.pivot_candle_time <= last_h.pivot_candle_time]
        protected_low = candidates[-1] if candidates else last_l
    elif trend == "BEARISH" and last_l is not None and highs:
        candidates = [h for h in highs if h.pivot_candle_time <= last_l.pivot_candle_time]
        protected_high = candidates[-1] if candidates else last_h

    dr_high = last_h.price if last_h else None
    dr_low = last_l.price if last_l else None
    eq_price = round_to_tick((dr_high + dr_low) / 2.0, tick_size) if (dr_high is not None and dr_low is not None) else None

    prev_ver = previous_state.state_version if previous_state else 0
    prov_hash = deterministic_hash(
        {
            "tf": canon_tf,
            "trend": trend,
            "swings": [e.event_id for e in labeled_events],
            "prot_h": protected_high.event_id if protected_high else None,
            "prot_l": protected_low.event_id if protected_low else None,
        },
        length=20,
    )
    return StructureState(
        state_id=f"st_{symbol}_{canon_tf}_{prov_hash[:12]}",
        algorithm_version=MTF_ALGORITHM_VERSION,
        symbol=symbol,
        timeframe=canon_tf,
        trend=trend,
        swings=tuple(labeled_events),
        last_swing_high=last_h,
        last_swing_low=last_l,
        prev_swing_high=prev_h,
        prev_swing_low=prev_l,
        protected_high=protected_high,
        protected_low=protected_low,
        dealing_range_high=dr_high,
        dealing_range_low=dr_low,
        equilibrium_price=eq_price,
        sequence_labels=tuple(seq_labels),
        state_version=prev_ver + 1,
        asof=asof,
        provenance_hash=prov_hash,
    )


def detect_structure_break(
    closed_bars: Union[ClosedBarWindow, NormalizedBars, Sequence[Any]],
    state: StructureState,
    break_policy: Optional[Union[MtfStructureLiquidityConfig, Dict[str, Any]]] = None,
) -> StructureBreakEvents:
    """
    Section 4.C: Causal BOS / CHoCH detection on closed candles.
    - A swing level can only be broken by a candle AFTER that swing is confirmed
      (`bar.close_time > swing.confirmed_at`).
    - Default requires CLOSED-CANDLE body close beyond level by >= `min_break_ticks`.
      A wick beyond the level whose close stays inside is recorded in `wick_only_rejections`
      (candidate liquidity sweep) and NEVER emitted as BOS/CHoCH.
    - Each swing level can be broken at most ONCE (idempotent break emission; no repeated
      break emitted on every subsequent candle above/below the same swing).
    - BOS = break in the direction of active structure trend (`BULLISH` breaking swing high,
      `BEARISH` breaking swing low).
    - CHoCH = break against active structure trend (breaking protected opposite swing, or
      initial regime break from RANGE/UNDETERMINED once both swing high and low exist).
    """
    if isinstance(break_policy, MtfStructureLiquidityConfig):
        req_close_bos = break_policy.bos_require_candle_close
        min_ticks_bos = break_policy.bos_min_break_ticks
        req_close_choch = break_policy.choch_require_candle_close
        min_ticks_choch = break_policy.choch_min_break_ticks
        tick_size = break_policy.tick_size
    elif isinstance(break_policy, dict):
        req_close_bos = bool(break_policy.get("bos_require_candle_close", True))
        min_ticks_bos = int(break_policy.get("bos_min_break_ticks", 2))
        req_close_choch = bool(break_policy.get("choch_require_candle_close", True))
        min_ticks_choch = int(break_policy.get("choch_min_break_ticks", 2))
        tick_size = float(break_policy.get("tick_size", DEFAULT_TICK_SIZE))
    else:
        req_close_bos = True
        min_ticks_bos = 2
        req_close_choch = True
        min_ticks_choch = 2
        tick_size = DEFAULT_TICK_SIZE

    symbol, tf, bars, asof, _ = _extract_closed_bars(closed_bars, default_tf=state.timeframe)
    pol_hash = deterministic_hash(
        {
            "cbos": req_close_bos,
            "tbos": min_ticks_bos,
            "cchoch": req_close_choch,
            "tchoch": min_ticks_choch,
            "ts": tick_size,
        },
        length=16,
    )

    if not bars or not state.swings:
        return StructureBreakEvents(
            symbol=symbol,
            timeframe=tf,
            events=(),
            wick_only_rejections=(),
            resulting_trend=state.trend,
            asof=asof,
            policy_hash=pol_hash,
        )

    broken_swing_ids: set[str] = set()
    break_events: List[StructureBreakEvent] = []
    wick_rejections: List[Dict[str, Any]] = []

    # Step through bars chronologically, activating swings only when swing.confirmed_at <= bar.close_time
    all_swings = sorted(state.swings, key=lambda s: (s.confirmed_at, s.pivot_candle_time))
    sw_ptr = 0
    active_high: Optional[SwingEvent] = None
    active_low: Optional[SwingEvent] = None
    running_trend: str = "STRUCTURE_UNDETERMINED"
    high_count = 0
    low_count = 0

    for idx, bar in enumerate(bars):
        if asof is not None and bar.close_time > asof:
            break

        # Check breaks against currently active confirmed swing high and low BEFORE registering
        # swings confirmed on this exact bar (a bar cannot both confirm a swing and break it).
        if active_high is not None and active_high.event_id not in broken_swing_ids:
            if bar.open_time > active_high.pivot_candle_time and bar.close_time > active_high.confirmed_at:
                is_bos_dir = running_trend == "BULLISH"
                min_t = min_ticks_bos if is_bos_dir else min_ticks_choch
                req_close = req_close_bos if is_bos_dir else req_close_choch
                wick_ticks = price_to_ticks(bar.high - active_high.price, tick_size)
                close_ticks = price_to_ticks(bar.close - active_high.price, tick_size)

                if wick_ticks >= min_t:
                    if req_close and close_ticks < min_t:
                        wick_rejections.append(
                            {
                                "bar_id": bar.bar_id,
                                "swing_id": active_high.event_id,
                                "direction": "BULLISH",
                                "reason": "WICK_ONLY_NOT_CLOSED_BEYOND_LEVEL",
                                "wick_ticks": wick_ticks,
                                "close_ticks": close_ticks,
                            }
                        )
                    elif (req_close and close_ticks >= min_t) or (not req_close and wick_ticks >= min_t):
                        # Only emit break if structure is determined or both sides exist
                        if running_trend in ("BULLISH", "BEARISH", "RANGE") or (high_count >= 1 and low_count >= 1):
                            b_type = "BOS_BULLISH" if running_trend == "BULLISH" else "CHOCH_BULLISH"
                            prior_t = running_trend
                            running_trend = "BULLISH"
                            broken_swing_ids.add(active_high.event_id)
                            ev_id = f"brk_{symbol}_{tf}_{b_type}_{int(bar.open_time.timestamp())}_{active_high.event_id[-8:]}"
                            break_events.append(
                                StructureBreakEvent(
                                    event_id=ev_id,
                                    algorithm_version=MTF_ALGORITHM_VERSION,
                                    symbol=symbol,
                                    timeframe=tf,
                                    break_type=b_type,
                                    direction="BULLISH",
                                    broken_swing_id=active_high.event_id,
                                    broken_level_price=active_high.price,
                                    break_close_price=bar.close,
                                    break_extreme_price=bar.high,
                                    break_distance_ticks=close_ticks if req_close else wick_ticks,
                                    break_bar_id=bar.bar_id,
                                    break_bar_index=idx,
                                    pivot_candle_time=active_high.pivot_candle_time,
                                    detected_at=bar.close_time,
                                    confirmed_at=bar.close_time,
                                    available_at=bar.available_at,
                                    prior_trend=prior_t,
                                    resulting_trend=running_trend,
                                    protected_swing_id=active_low.event_id if active_low else None,
                                    protected_level_price=active_low.price if active_low else None,
                                )
                            )

        if active_low is not None and active_low.event_id not in broken_swing_ids:
            if bar.open_time > active_low.pivot_candle_time and bar.close_time > active_low.confirmed_at:
                is_bos_dir = running_trend == "BEARISH"
                min_t = min_ticks_bos if is_bos_dir else min_ticks_choch
                req_close = req_close_bos if is_bos_dir else req_close_choch
                wick_ticks = price_to_ticks(active_low.price - bar.low, tick_size)
                close_ticks = price_to_ticks(active_low.price - bar.close, tick_size)

                if wick_ticks >= min_t:
                    if req_close and close_ticks < min_t:
                        wick_rejections.append(
                            {
                                "bar_id": bar.bar_id,
                                "swing_id": active_low.event_id,
                                "direction": "BEARISH",
                                "reason": "WICK_ONLY_NOT_CLOSED_BEYOND_LEVEL",
                                "wick_ticks": wick_ticks,
                                "close_ticks": close_ticks,
                            }
                        )
                    elif (req_close and close_ticks >= min_t) or (not req_close and wick_ticks >= min_t):
                        if running_trend in ("BULLISH", "BEARISH", "RANGE") or (high_count >= 1 and low_count >= 1):
                            b_type = "BOS_BEARISH" if running_trend == "BEARISH" else "CHOCH_BEARISH"
                            prior_t = running_trend
                            running_trend = "BEARISH"
                            broken_swing_ids.add(active_low.event_id)
                            ev_id = f"brk_{symbol}_{tf}_{b_type}_{int(bar.open_time.timestamp())}_{active_low.event_id[-8:]}"
                            break_events.append(
                                StructureBreakEvent(
                                    event_id=ev_id,
                                    algorithm_version=MTF_ALGORITHM_VERSION,
                                    symbol=symbol,
                                    timeframe=tf,
                                    break_type=b_type,
                                    direction="BEARISH",
                                    broken_swing_id=active_low.event_id,
                                    broken_level_price=active_low.price,
                                    break_close_price=bar.close,
                                    break_extreme_price=bar.low,
                                    break_distance_ticks=close_ticks if req_close else wick_ticks,
                                    break_bar_id=bar.bar_id,
                                    break_bar_index=idx,
                                    pivot_candle_time=active_low.pivot_candle_time,
                                    detected_at=bar.close_time,
                                    confirmed_at=bar.close_time,
                                    available_at=bar.available_at,
                                    prior_trend=prior_t,
                                    resulting_trend=running_trend,
                                    protected_swing_id=active_high.event_id if active_high else None,
                                    protected_level_price=active_high.price if active_high else None,
                                )
                            )

        # Now activate any swings confirmed at or before this bar's close_time
        while sw_ptr < len(all_swings) and all_swings[sw_ptr].confirmed_at <= bar.close_time:
            sw = all_swings[sw_ptr]
            sw_ptr += 1
            if sw.swing_type == "SWING_HIGH":
                active_high = sw
                high_count += 1
            else:
                active_low = sw
                low_count += 1
            if running_trend == "STRUCTURE_UNDETERMINED" and high_count >= 2 and low_count >= 2:
                if active_high and active_low:
                    if active_high.sequence_label == "HH" and active_low.sequence_label in ("HL", "EL"):
                        running_trend = "BULLISH"
                    elif active_low.sequence_label == "LL" and active_high.sequence_label in ("LH", "EH"):
                        running_trend = "BEARISH"
                    else:
                        running_trend = "RANGE"

    final_trend = break_events[-1].resulting_trend if break_events else state.trend
    return StructureBreakEvents(
        symbol=symbol,
        timeframe=tf,
        events=tuple(break_events),
        wick_only_rejections=tuple(wick_rejections),
        resulting_trend=final_trend,
        asof=asof,
        policy_hash=pol_hash,
    )


def update_structure_state(
    previous_state: Optional[StructureState],
    swings: Union[SwingEvents, Sequence[SwingEvent]],
    breaks: Union[StructureBreakEvents, Sequence[StructureBreakEvent]],
    asof: Union[datetime, str, int, float],
    total_bars_count: Optional[int] = None,
) -> StructureContext:
    """
    Section 4.D: Pure, deterministic reducer producing an immutable `StructureContext` as of `asof`.
    Filters out any event with `available_at > asof` and computes consistent trend,
    last BOS/CHoCH and age, protected high/low, and invalidation status.
    """
    asof_dt = ensure_utc_datetime(asof)
    if asof_dt is None:
        raise ValueError("asof is required")

    raw_swings = list(swings.events if isinstance(swings, SwingEvents) else swings)
    causal_swings = [s for s in raw_swings if s.available_at <= asof_dt and s.confirmed_at <= asof_dt]
    tf = (
        swings.timeframe
        if isinstance(swings, SwingEvents)
        else (previous_state.timeframe if previous_state else (causal_swings[0].timeframe if causal_swings else "M15"))
    )
    symbol = (
        swings.symbol
        if isinstance(swings, SwingEvents)
        else (previous_state.symbol if previous_state else SUPPORTED_SYMBOL)
    )

    state = classify_swing_sequence(causal_swings, tf, previous_state=previous_state)

    raw_breaks = list(breaks.events if isinstance(breaks, StructureBreakEvents) else breaks)
    causal_breaks = [b for b in raw_breaks if b.available_at <= asof_dt and b.confirmed_at <= asof_dt]
    causal_breaks.sort(key=lambda b: (b.confirmed_at, b.break_bar_index))

    bos_list = [b for b in causal_breaks if b.break_type.startswith("BOS_")]
    choch_list = [b for b in causal_breaks if b.break_type.startswith("CHOCH_")]
    last_bos = bos_list[-1] if bos_list else None
    last_choch = choch_list[-1] if choch_list else None
    last_break = causal_breaks[-1] if causal_breaks else None

    if last_break is not None:
        effective_trend = last_break.resulting_trend
    else:
        effective_trend = state.trend

    # Check if protected high/low was invalidated by a subsequent break
    prot_high_inv = False
    prot_low_inv = False
    if state.protected_high is not None and last_break is not None:
        if last_break.direction == "BULLISH" and last_break.broken_swing_id == state.protected_high.event_id:
            prot_high_inv = True
    if state.protected_low is not None and last_break is not None:
        if last_break.direction == "BEARISH" and last_break.broken_swing_id == state.protected_low.event_id:
            prot_low_inv = True

    if last_break is not None and total_bars_count is not None and total_bars_count > last_break.break_bar_index:
        break_age = (total_bars_count - 1) - last_break.break_bar_index
    elif last_break is not None:
        tf_sec = timeframe_duration_seconds(tf)
        break_age = max(0, int((asof_dt - last_break.confirmed_at).total_seconds() // tf_sec))
    else:
        break_age = None

    q_state = (
        MtfDataQualityState.VALID
        if len(causal_swings) >= 2
        else MtfDataQualityState.INSUFFICIENT_HISTORY
    )

    prov_hash = deterministic_hash(
        {
            "symbol": symbol,
            "tf": tf,
            "asof": format_utc_iso(asof_dt),
            "state": state.provenance_hash,
            "breaks": [b.event_id for b in causal_breaks],
            "trend": effective_trend,
        },
        length=24,
    )

    return StructureContext(
        context_id=f"sctx_{symbol}_{tf}_{prov_hash[:12]}",
        algorithm_version=MTF_ALGORITHM_VERSION,
        symbol=symbol,
        timeframe=tf,
        asof=asof_dt,
        trend=effective_trend,
        state=state,
        breaks=tuple(causal_breaks),
        last_bos=last_bos,
        last_choch=last_choch,
        last_break=last_break,
        last_break_age_bars=break_age,
        protected_high_price=state.protected_high.price if state.protected_high else None,
        protected_low_price=state.protected_low.price if state.protected_low else None,
        protected_high_invalidated=prot_high_inv,
        protected_low_invalidated=prot_low_inv,
        quality_state=q_state,
        provenance_hash=prov_hash,
    )


def build_multi_timeframe_structure(
    h4: StructureContext,
    h1: StructureContext,
    m15: StructureContext,
    policy: Optional[MtfStructureLiquidityConfig] = None,
) -> MtfStructureContext:
    """
    Section 4.E: Combine H4 (regime/major swing bias), H1 (intermediate confirmation/conflict),
    and M15 (execution-scale structure context) into an immutable `MtfStructureContext`.
    Enforces that no HTF event is newer than `asof = max(h4.asof, h1.asof, m15.asof)`.
    """
    asof_dt = max(h4.asof, h1.asof, m15.asof)
    symbol = m15.symbol or h1.symbol or h4.symbol

    def _norm_dir(t: str) -> str:
        if t == "BULLISH":
            return "BULLISH"
        if t == "BEARISH":
            return "BEARISH"
        if t in ("RANGE", "NEUTRAL"):
            return "NEUTRAL"
        return "UNKNOWN"

    h4_dir = _norm_dir(h4.trend)
    h1_dir = _norm_dir(h1.trend)
    m15_dir = _norm_dir(m15.trend)

    conflicts: List[str] = []
    known_dirs = [d for d in (h4_dir, h1_dir, m15_dir) if d in ("BULLISH", "BEARISH")]

    if len(known_dirs) == 0:
        alignment = "UNKNOWN"
    elif all(d == "BULLISH" for d in (h4_dir, h1_dir, m15_dir)):
        alignment = "ALIGNED_BULL"
    elif all(d == "BEARISH" for d in (h4_dir, h1_dir, m15_dir)):
        alignment = "ALIGNED_BEAR"
    elif h4_dir in ("UNKNOWN", "NEUTRAL") and h1_dir == "BULLISH" and m15_dir == "BULLISH":
        alignment = "ALIGNED_BULL"
    elif h4_dir in ("UNKNOWN", "NEUTRAL") and h1_dir == "BEARISH" and m15_dir == "BEARISH":
        alignment = "ALIGNED_BEAR"
    elif "BULLISH" in known_dirs and "BEARISH" in known_dirs:
        alignment = "MIXED"
    else:
        alignment = "MIXED"

    # Intermediate status (H1 relative to H4)
    if h4_dir in ("BULLISH", "BEARISH") and h1_dir == h4_dir:
        intermediate_status = "CONFIRMED"
    elif h4_dir in ("BULLISH", "BEARISH") and h1_dir in ("BULLISH", "BEARISH") and h1_dir != h4_dir:
        intermediate_status = "CONFLICT"
        conflicts.append(f"H4_{h4_dir}_VS_H1_{h1_dir}")
    elif h4_dir in ("BULLISH", "BEARISH") and h1_dir == "NEUTRAL":
        intermediate_status = "PULLBACK"
    elif h1_dir in ("BULLISH", "BEARISH"):
        intermediate_status = "CONFIRMED"
    else:
        intermediate_status = "UNKNOWN"

    if h1_dir in ("BULLISH", "BEARISH") and m15_dir in ("BULLISH", "BEARISH") and h1_dir != m15_dir:
        conflicts.append(f"H1_{h1_dir}_VS_M15_{m15_dir}")
    if h4_dir in ("BULLISH", "BEARISH") and m15_dir in ("BULLISH", "BEARISH") and h4_dir != m15_dir:
        conflicts.append(f"H4_{h4_dir}_VS_M15_{m15_dir}")

    regime_bias = h4_dir if h4_dir in ("BULLISH", "BEARISH") else (h1_dir if h1_dir in ("BULLISH", "BEARISH") else "NEUTRAL")
    exec_bias = m15_dir if m15_dir in ("BULLISH", "BEARISH") else h1_dir

    if all(c.quality_state == MtfDataQualityState.VALID for c in (h4, h1, m15)):
        q_state = MtfDataQualityState.VALID
    else:
        q_state = MtfDataQualityState.INSUFFICIENT_HISTORY

    prov_hash = deterministic_hash(
        {
            "h4": h4.provenance_hash,
            "h1": h1.provenance_hash,
            "m15": m15.provenance_hash,
            "alignment": alignment,
        },
        length=24,
    )

    return MtfStructureContext(
        mtf_structure_id=f"mtfstr_{symbol}_{prov_hash[:12]}",
        algorithm_version=MTF_ALGORITHM_VERSION,
        symbol=symbol,
        asof=asof_dt,
        h4=h4,
        h1=h1,
        m15=m15,
        alignment=alignment,
        regime_bias=regime_bias,
        intermediate_status=intermediate_status,
        execution_bias=exec_bias,
        conflict_reasons=tuple(conflicts),
        quality_state=q_state,
        provenance_hash=prov_hash,
    )


# ============================================================================
# SECTION 5 — CAUSAL LIQUIDITY & SWEEP ENGINE
# ============================================================================

@dataclass(frozen=True)
class LiquidityPoolCluster:
    pool_id: str
    algorithm_version: str
    symbol: str
    timeframe: str
    pool_type: str  # "EQH" | "EQL"
    side: str  # "BUY_SIDE" (above price / highs) | "SELL_SIDE" (below price / lows)
    representative_price: float
    min_price: float
    max_price: float
    diameter_ticks: int
    tolerance_ticks: int
    touch_count: int
    member_swing_ids: Tuple[str, ...]
    first_pivot_time: datetime
    last_pivot_time: datetime
    confirmed_at: datetime
    available_at: datetime
    state: str = "ACTIVE"  # ACTIVE, TOUCHED, SWEPT, CONSUMED, INVALIDATED, EXPIRED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pool_id": self.pool_id,
            "algorithm_version": self.algorithm_version,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "pool_type": self.pool_type,
            "side": self.side,
            "representative_price": self.representative_price,
            "min_price": self.min_price,
            "max_price": self.max_price,
            "diameter_ticks": self.diameter_ticks,
            "tolerance_ticks": self.tolerance_ticks,
            "touch_count": self.touch_count,
            "member_swing_ids": list(self.member_swing_ids),
            "first_pivot_time": format_utc_iso(self.first_pivot_time),
            "last_pivot_time": format_utc_iso(self.last_pivot_time),
            "confirmed_at": format_utc_iso(self.confirmed_at),
            "available_at": format_utc_iso(self.available_at),
            "state": self.state,
        }


@dataclass(frozen=True)
class LiquidityPools:
    symbol: str
    timeframe: str
    pools: Tuple[LiquidityPoolCluster, ...]
    asof: Optional[datetime]
    tolerance_ticks: int
    min_touches: int
    provenance_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "asof": format_utc_iso(self.asof),
            "tolerance_ticks": self.tolerance_ticks,
            "min_touches": self.min_touches,
            "count": len(self.pools),
            "provenance_hash": self.provenance_hash,
            "pools": [p.to_dict() for p in self.pools],
        }


@dataclass(frozen=True)
class LiquidityLevelRecord:
    level_id: str
    algorithm_version: str
    symbol: str
    originating_timeframe: str
    level_type: str  # "SWING_HIGH" | "SWING_LOW" | "EQH" | "EQL" | "PDH" | "PDL"
    side: str  # "BUY_SIDE" | "SELL_SIDE"
    price: float
    tick_size: float
    source_event_ids: Tuple[str, ...]
    valid_from: datetime
    available_at: datetime
    expires_at: Optional[datetime]
    touch_count: int = 1
    state: str = "ACTIVE"  # ACTIVE, TOUCHED, SWEPT, CONSUMED, INVALIDATED, EXPIRED
    last_transition_at: Optional[datetime] = None
    last_transition_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level_id": self.level_id,
            "algorithm_version": self.algorithm_version,
            "symbol": self.symbol,
            "originating_timeframe": self.originating_timeframe,
            "level_type": self.level_type,
            "side": self.side,
            "price": self.price,
            "tick_size": self.tick_size,
            "source_event_ids": list(self.source_event_ids),
            "valid_from": format_utc_iso(self.valid_from),
            "available_at": format_utc_iso(self.available_at),
            "expires_at": format_utc_iso(self.expires_at),
            "touch_count": self.touch_count,
            "state": self.state,
            "last_transition_at": format_utc_iso(self.last_transition_at),
            "last_transition_reason": self.last_transition_reason,
        }


@dataclass(frozen=True)
class LiquidityLevels:
    symbol: str
    asof: datetime
    levels: Tuple[LiquidityLevelRecord, ...]
    pdh_pdl_status: str  # "AVAILABLE" | "NOT_AVAILABLE"
    prior_session_date: Optional[str]
    pdh_price: Optional[float]
    pdl_price: Optional[float]
    provenance_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "asof": format_utc_iso(self.asof),
            "pdh_pdl_status": self.pdh_pdl_status,
            "prior_session_date": self.prior_session_date,
            "pdh_price": self.pdh_price,
            "pdl_price": self.pdl_price,
            "count": len(self.levels),
            "provenance_hash": self.provenance_hash,
            "levels": [lvl.to_dict() for lvl in self.levels],
        }


@dataclass(frozen=True)
class SweepEvent:
    event_id: str
    algorithm_version: str
    symbol: str
    timeframe: str
    swept_level_id: str
    swept_level_type: str
    swept_level_price: float
    side: str  # "BUY_SIDE_SWEEP" (bearish rejection of highs) | "SELL_SIDE_SWEEP" (bullish rejection of lows)
    implied_reversal_direction: str  # "BEARISH" (after buy-side sweep) | "BULLISH" (after sell-side sweep)
    sweep_bar_id: str
    sweep_bar_index: int
    extreme_price: float
    reclaim_close_price: float
    swept_ticks: int
    reclaim_ticks_inside: int
    event_time: datetime
    confirmed_at: datetime
    available_at: datetime
    policy_version: str = MTF_ALGORITHM_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "algorithm_version": self.algorithm_version,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "swept_level_id": self.swept_level_id,
            "swept_level_type": self.swept_level_type,
            "swept_level_price": self.swept_level_price,
            "side": self.side,
            "implied_reversal_direction": self.implied_reversal_direction,
            "sweep_bar_id": self.sweep_bar_id,
            "sweep_bar_index": self.sweep_bar_index,
            "extreme_price": self.extreme_price,
            "reclaim_close_price": self.reclaim_close_price,
            "swept_ticks": self.swept_ticks,
            "reclaim_ticks_inside": self.reclaim_ticks_inside,
            "event_time": format_utc_iso(self.event_time),
            "confirmed_at": format_utc_iso(self.confirmed_at),
            "available_at": format_utc_iso(self.available_at),
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True)
class SweepEvents:
    symbol: str
    timeframe: str
    events: Tuple[SweepEvent, ...]
    touches: Tuple[Dict[str, Any], ...]
    non_reclaimed_breakouts: Tuple[Dict[str, Any], ...]
    asof: Optional[datetime]
    policy_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "asof": format_utc_iso(self.asof),
            "policy_hash": self.policy_hash,
            "count": len(self.events),
            "touches_count": len(self.touches),
            "non_reclaimed_breakouts_count": len(self.non_reclaimed_breakouts),
            "events": [e.to_dict() for e in self.events],
        }


@dataclass(frozen=True)
class LiquidityContext:
    context_id: str
    algorithm_version: str
    symbol: str
    asof: datetime
    levels: Tuple[LiquidityLevelRecord, ...]
    active_levels: Tuple[LiquidityLevelRecord, ...]
    sweeps: Tuple[SweepEvent, ...]
    pdh_pdl_status: str
    pdh_price: Optional[float]
    pdl_price: Optional[float]
    state_transitions: Tuple[Dict[str, Any], ...]
    provenance_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "context_id": self.context_id,
            "algorithm_version": self.algorithm_version,
            "symbol": self.symbol,
            "asof": format_utc_iso(self.asof),
            "total_levels": len(self.levels),
            "active_levels_count": len(self.active_levels),
            "sweeps_count": len(self.sweeps),
            "pdh_pdl_status": self.pdh_pdl_status,
            "pdh_price": self.pdh_price,
            "pdl_price": self.pdl_price,
            "provenance_hash": self.provenance_hash,
            "active_levels": [l.to_dict() for l in self.active_levels],
            "sweeps": [s.to_dict() for s in self.sweeps],
            "state_transitions_count": len(self.state_transitions),
        }


@dataclass(frozen=True)
class EvaluatorLiquidityContext:
    summary_id: str
    algorithm_version: str
    symbol: str
    asof: datetime
    reference_price: Optional[float]
    nearest_buy_side_level: Optional[LiquidityLevelRecord]
    nearest_sell_side_level: Optional[LiquidityLevelRecord]
    distance_to_buy_side_ticks: Optional[int]
    distance_to_sell_side_ticks: Optional[int]
    draw_on_liquidity_side: str  # "BUY_SIDE" | "SELL_SIDE" | "BALANCED" | "NONE"
    has_fresh_sweep: bool
    last_sweep: Optional[SweepEvent]
    seconds_since_last_sweep: Optional[int]
    structure_alignment_with_sweep: str  # "ALIGNED" | "CONFLICT" | "NO_SWEEP"
    pdh_pdl_status: str
    quality_state: str
    provenance_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary_id": self.summary_id,
            "algorithm_version": self.algorithm_version,
            "symbol": self.symbol,
            "asof": format_utc_iso(self.asof),
            "reference_price": self.reference_price,
            "nearest_buy_side_level": self.nearest_buy_side_level.to_dict() if self.nearest_buy_side_level else None,
            "nearest_sell_side_level": self.nearest_sell_side_level.to_dict() if self.nearest_sell_side_level else None,
            "distance_to_buy_side_ticks": self.distance_to_buy_side_ticks,
            "distance_to_sell_side_ticks": self.distance_to_sell_side_ticks,
            "draw_on_liquidity_side": self.draw_on_liquidity_side,
            "has_fresh_sweep": self.has_fresh_sweep,
            "last_sweep": self.last_sweep.to_dict() if self.last_sweep else None,
            "seconds_since_last_sweep": self.seconds_since_last_sweep,
            "structure_alignment_with_sweep": self.structure_alignment_with_sweep,
            "pdh_pdl_status": self.pdh_pdl_status,
            "quality_state": self.quality_state,
            "provenance_hash": self.provenance_hash,
        }


def cluster_equal_high_low(
    swings: Union[SwingEvents, Sequence[SwingEvent]],
    tolerance_ticks: int = 5,
    min_touches: int = 2,
    policy: Optional[Union[MtfStructureLiquidityConfig, Dict[str, Any]]] = None,
) -> LiquidityPools:
    """
    Section 5.B: Deterministic bounded-diameter Equal High (EQH) / Equal Low (EQL) clustering
    on CONFIRMED swings only.
    - Prevents transitive chain expansion: every member in a cluster must be within
      `tolerance_ticks` of the cluster anchor AND the total cluster diameter
      `max_price - min_price` must not exceed `tolerance_ticks`.
    - Requires at least `min_touches` distinct confirmed swing events.
    - `confirmed_at` and `available_at` of the pool equal the latest `confirmed_at`
      among the constituent swings (never visible before the 2nd+ touch is confirmed!).
    """
    tol_ticks = max(0, int(tolerance_ticks))
    min_t = max(2, int(min_touches))
    tick_size = (
        policy.tick_size
        if isinstance(policy, MtfStructureLiquidityConfig)
        else float((policy or {}).get("tick_size", DEFAULT_TICK_SIZE))
    )

    if isinstance(swings, SwingEvents):
        raw_events = list(swings.events)
        symbol = swings.symbol
        tf = swings.timeframe
        asof = swings.asof
    else:
        raw_events = list(swings)
        symbol = raw_events[0].symbol if raw_events else SUPPORTED_SYMBOL
        tf = raw_events[0].timeframe if raw_events else "M15"
        asof = max((e.confirmed_at for e in raw_events), default=None)

    causal_events = [
        e for e in raw_events if asof is None or (e.confirmed_at <= asof and e.available_at <= asof)
    ]
    causal_events.sort(key=lambda e: (e.confirmed_at, e.pivot_candle_time, e.event_id))

    pools: List[LiquidityPoolCluster] = []

    for sw_type, p_type, side in (
        ("SWING_HIGH", "EQH", "BUY_SIDE"),
        ("SWING_LOW", "EQL", "SELL_SIDE"),
    ):
        candidates = [e for e in causal_events if e.swing_type == sw_type]
        used_ids: set[str] = set()
        for i, anchor in enumerate(candidates):
            if anchor.event_id in used_ids:
                continue
            members: List[SwingEvent] = [anchor]
            min_p = anchor.price
            max_p = anchor.price
            for j in range(i + 1, len(candidates)):
                cand = candidates[j]
                if cand.event_id in used_ids or cand.pivot_bar_id == anchor.pivot_bar_id:
                    continue
                new_min = min(min_p, cand.price)
                new_max = max(max_p, cand.price)
                diam_ticks = price_to_ticks(new_max - new_min, tick_size)
                anchor_dist_ticks = price_to_ticks(abs(cand.price - anchor.price), tick_size)
                if anchor_dist_ticks <= tol_ticks and diam_ticks <= tol_ticks:
                    members.append(cand)
                    min_p = new_min
                    max_p = new_max

            if len(members) >= min_t:
                for m in members:
                    used_ids.add(m.event_id)
                rep_price = round_to_tick(sum(m.price for m in members) / len(members), tick_size)
                diam = price_to_ticks(max_p - min_p, tick_size)
                first_t = min(m.pivot_candle_time for m in members)
                last_t = max(m.pivot_candle_time for m in members)
                conf_t = max(m.confirmed_at for m in members)
                avail_t = max(m.available_at for m in members)
                m_ids = tuple(m.event_id for m in members)
                p_id = f"pool_{symbol}_{tf}_{p_type}_{deterministic_hash(m_ids, 10)}"
                pools.append(
                    LiquidityPoolCluster(
                        pool_id=p_id,
                        algorithm_version=MTF_ALGORITHM_VERSION,
                        symbol=symbol,
                        timeframe=tf,
                        pool_type=p_type,
                        side=side,
                        representative_price=rep_price,
                        min_price=min_p,
                        max_price=max_p,
                        diameter_ticks=diam,
                        tolerance_ticks=tol_ticks,
                        touch_count=len(members),
                        member_swing_ids=m_ids,
                        first_pivot_time=first_t,
                        last_pivot_time=last_t,
                        confirmed_at=conf_t,
                        available_at=avail_t,
                        state="ACTIVE",
                    )
                )

    pools.sort(key=lambda p: (p.confirmed_at, p.pool_id))
    prov_hash = deterministic_hash([p.pool_id for p in pools], length=20)
    return LiquidityPools(
        symbol=symbol,
        timeframe=tf,
        pools=tuple(pools),
        asof=asof,
        tolerance_ticks=tol_ticks,
        min_touches=min_t,
        provenance_hash=prov_hash,
    )


def _derive_prior_day_high_low(
    mtf_snapshot: MtfSnapshot,
    tick_size: float = DEFAULT_TICK_SIZE,
) -> Tuple[str, Optional[str], Optional[float], Optional[float], Optional[datetime], Optional[datetime], Tuple[str, ...]]:
    """
    Strictly derive Prior Day High (PDH) and Prior Day Low (PDL) from the COMPLETED
    previous UTC trading day (`open_time.date() == prior_day`), never using any bar
    from the current in-progress day (`decision_time.date()`).
    Validates against `MarketCalendarService.is_gold_market_open` and requires coverage
    across the prior session; otherwise returns `NOT_AVAILABLE`.
    """
    asof_dt = mtf_snapshot.decision_time
    current_date = asof_dt.date()

    # Collect all closed bars strictly before current_date 00:00 UTC
    current_day_start = datetime(current_date.year, current_date.month, current_date.day, tzinfo=timezone.utc)
    best_win: Optional[ClosedBarWindow] = None
    for tf_pref in ("M15", "H1", "H4", "M5", "M1", "D1"):
        w = mtf_snapshot.windows.get(tf_pref)
        if w is not None and w.count > 0:
            best_win = w
            break

    if best_win is None:
        return ("NOT_AVAILABLE", None, None, None, None, None, ())

    raw_by_date: Dict[str, List[NormalizedBar]] = {}
    for b in best_win.bars:
        if not b.is_closed or b.close_time > current_day_start or b.open_time >= current_day_start:
            continue
        d_key = b.open_time.strftime("%Y-%m-%d")
        raw_by_date.setdefault(d_key, []).append(b)

    if not raw_by_date:
        return ("NOT_AVAILABLE", None, None, None, None, None, ())

    prior_date_str: Optional[str] = None
    prior_bars: List[NormalizedBar] = []
    for cand_date in sorted(raw_by_date.keys(), reverse=True):
        cand_bars = [
            b
            for b in raw_by_date[cand_date]
            if bool(
                get_contract_trading_availability(
                    symbol="XAUUSDT",
                    venue="BITGET",
                    now_ms=int(b.open_time.timestamp() * 1000),
                ).get("scheduled_open", True)
            )
        ]
        if cand_bars:
            prior_date_str = cand_date
            prior_bars = cand_bars
            break

    if prior_date_str is None or not prior_bars:
        return ("NOT_AVAILABLE", None, None, None, None, None, ())

    # Require sufficient coverage of the prior trading day so partial 1-bar history is not treated as full PDH/PDL
    tf_sec = timeframe_duration_seconds(best_win.timeframe)
    min_bars_for_session = max(1, (8 * 3600) // tf_sec)  # At least 8 hours of session coverage
    if best_win.timeframe == "D1":
        min_bars_for_session = 1
    if len(prior_bars) < min_bars_for_session:
        return ("NOT_AVAILABLE", prior_date_str, None, None, None, None, ())

    pdh = round_to_tick(max(b.high for b in prior_bars), tick_size)
    pdl = round_to_tick(min(b.low for b in prior_bars), tick_size)
    session_close_dt = max(b.close_time for b in prior_bars)
    avail_dt = max(session_close_dt, datetime.strptime(prior_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1))
    if avail_dt > current_day_start:
        avail_dt = current_day_start

    src_ids = (prior_bars[0].bar_id, prior_bars[-1].bar_id)
    return ("AVAILABLE", prior_date_str, pdh, pdl, session_close_dt, avail_dt, src_ids)


def derive_reference_liquidity_levels(
    mtf_snapshot: MtfSnapshot,
    structure: Union[MtfStructureContext, StructureContext],
    policy: Optional[MtfStructureLiquidityConfig] = None,
) -> LiquidityLevels:
    """
    Section 5.A: Derive causal reference liquidity levels across H4/H1/M15:
    - Confirmed swing highs (`BUY_SIDE`) & swing lows (`SELL_SIDE`)
    - Equal Highs (`EQH`) & Equal Lows (`EQL`) clusters
    - Prior Day High (`PDH`) & Prior Day Low (`PDL`) strictly from the completed prior session
      (or `NOT_AVAILABLE` when session coverage is absent/incomplete).
    """
    cfg = policy or MtfStructureLiquidityConfig()
    asof_dt = mtf_snapshot.decision_time
    symbol = mtf_snapshot.symbol

    if isinstance(structure, MtfStructureContext):
        ctx_list = [structure.h4, structure.h1, structure.m15]
    else:
        ctx_list = [structure]

    levels: List[LiquidityLevelRecord] = []

    for s_ctx in ctx_list:
        tf = s_ctx.timeframe
        tf_sec = timeframe_duration_seconds(tf)
        max_age_sec = cfg.pool_max_age_bars * tf_sec

        # 1. Equal High/Low pools on this timeframe
        pools = cluster_equal_high_low(
            s_ctx.state.swings,
            tolerance_ticks=cfg.eq_tolerance_ticks,
            min_touches=cfg.pool_min_touches,
            policy=cfg,
        )
        pooled_swing_ids: set[str] = set()
        for p in pools.pools:
            if p.available_at > asof_dt:
                continue
            pooled_swing_ids.update(p.member_swing_ids)
            exp_dt = p.confirmed_at + timedelta(seconds=max_age_sec)
            lvl_state = "EXPIRED" if asof_dt > exp_dt else "ACTIVE"
            levels.append(
                LiquidityLevelRecord(
                    level_id=p.pool_id,
                    algorithm_version=MTF_ALGORITHM_VERSION,
                    symbol=symbol,
                    originating_timeframe=tf,
                    level_type=p.pool_type,
                    side=p.side,
                    price=p.representative_price,
                    tick_size=cfg.tick_size,
                    source_event_ids=p.member_swing_ids,
                    valid_from=p.confirmed_at,
                    available_at=p.available_at,
                    expires_at=exp_dt,
                    touch_count=p.touch_count,
                    state=lvl_state,
                )
            )

        # 2. Individual confirmed swing highs/lows
        for sw in s_ctx.state.swings:
            if sw.available_at > asof_dt or sw.event_id in pooled_swing_ids:
                continue
            exp_dt = sw.confirmed_at + timedelta(seconds=max_age_sec)
            lvl_state = "EXPIRED" if asof_dt > exp_dt else "ACTIVE"
            side = "BUY_SIDE" if sw.swing_type == "SWING_HIGH" else "SELL_SIDE"
            levels.append(
                LiquidityLevelRecord(
                    level_id=f"lvl_{sw.event_id}",
                    algorithm_version=MTF_ALGORITHM_VERSION,
                    symbol=symbol,
                    originating_timeframe=tf,
                    level_type=sw.swing_type,
                    side=side,
                    price=sw.price,
                    tick_size=cfg.tick_size,
                    source_event_ids=(sw.event_id,),
                    valid_from=sw.confirmed_at,
                    available_at=sw.available_at,
                    expires_at=exp_dt,
                    touch_count=1,
                    state=lvl_state,
                )
            )

    # 3. Prior Day High / Prior Day Low (PDH / PDL)
    pdh_status, prior_date, pdh_p, pdl_p, valid_from_dt, avail_dt, src_ids = _derive_prior_day_high_low(
        mtf_snapshot, tick_size=cfg.tick_size
    )
    if pdh_status == "AVAILABLE" and pdh_p is not None and pdl_p is not None and avail_dt is not None and avail_dt <= asof_dt:
        exp_dt = avail_dt + timedelta(hours=48)
        levels.append(
            LiquidityLevelRecord(
                level_id=f"lvl_{symbol}_D1_PDH_{prior_date}",
                algorithm_version=MTF_ALGORITHM_VERSION,
                symbol=symbol,
                originating_timeframe="D1",
                level_type="PDH",
                side="BUY_SIDE",
                price=pdh_p,
                tick_size=cfg.tick_size,
                source_event_ids=src_ids,
                valid_from=valid_from_dt or avail_dt,
                available_at=avail_dt,
                expires_at=exp_dt,
                touch_count=1,
                state="ACTIVE",
            )
        )
        levels.append(
            LiquidityLevelRecord(
                level_id=f"lvl_{symbol}_D1_PDL_{prior_date}",
                algorithm_version=MTF_ALGORITHM_VERSION,
                symbol=symbol,
                originating_timeframe="D1",
                level_type="PDL",
                side="SELL_SIDE",
                price=pdl_p,
                tick_size=cfg.tick_size,
                source_event_ids=src_ids,
                valid_from=valid_from_dt or avail_dt,
                available_at=avail_dt,
                expires_at=exp_dt,
                touch_count=1,
                state="ACTIVE",
            )
        )

    levels.sort(key=lambda x: (x.available_at, x.level_id))
    prov_hash = deterministic_hash([l.level_id for l in levels], length=20)
    return LiquidityLevels(
        symbol=symbol,
        asof=asof_dt,
        levels=tuple(levels),
        pdh_pdl_status=pdh_status,
        prior_session_date=prior_date,
        pdh_price=pdh_p,
        pdl_price=pdl_p,
        provenance_hash=prov_hash,
    )


def detect_liquidity_sweep(
    closed_bars: Union[ClosedBarWindow, NormalizedBars, Sequence[Any]],
    levels: Union[LiquidityLevels, LiquidityPools, Sequence[Any]],
    sweep_policy: Optional[Union[MtfStructureLiquidityConfig, Dict[str, Any]]] = None,
) -> SweepEvents:
    """
    Section 5.C: Strictly causal closed-candle Liquidity Sweep detector.
    - Only evaluates CLOSED candles (`bar.is_closed == True` and `bar.close_time <= asof`).
    - Level must already be available BEFORE the sweep candle closes (`level.available_at < bar.close_time`)
      and not yet expired (`bar.close_time <= level.expires_at`) and not already swept/consumed.
    - BUY_SIDE sweep (bearish rejection): `bar.high - level.price >= min_penetration_ticks`
      AND `level.price - bar.close >= reclaim_buffer_ticks` (candle closes back at/below level).
    - SELL_SIDE sweep (bullish rejection): `level.price - bar.low >= min_penetration_ticks`
      AND `bar.close - level.price >= reclaim_buffer_ticks` (candle closes back at/above level).
    - Touch-only (`0 <= wick_penetration_ticks < min_penetration_ticks`) is recorded in `touches`,
      NEVER as a sweep.
    - Breakout where close stays beyond the level is recorded in `non_reclaimed_breakouts`
      (BOS/breakout candidate), NEVER as a sweep.
    """
    if isinstance(sweep_policy, MtfStructureLiquidityConfig):
        min_pen_ticks = sweep_policy.sweep_min_penetration_ticks
        reclaim_buf_ticks = sweep_policy.sweep_reclaim_buffer_ticks
        tick_size = sweep_policy.tick_size
    elif isinstance(sweep_policy, dict):
        min_pen_ticks = int(sweep_policy.get("sweep_min_penetration_ticks", 2))
        reclaim_buf_ticks = int(sweep_policy.get("sweep_reclaim_buffer_ticks", 0))
        tick_size = float(sweep_policy.get("tick_size", DEFAULT_TICK_SIZE))
    else:
        min_pen_ticks = 2
        reclaim_buf_ticks = 0
        tick_size = DEFAULT_TICK_SIZE

    symbol, tf, bars, asof, _ = _extract_closed_bars(closed_bars)

    raw_levels: List[LiquidityLevelRecord] = []
    if isinstance(levels, LiquidityLevels):
        raw_levels = list(levels.levels)
    elif isinstance(levels, LiquidityPools):
        for p in levels.pools:
            raw_levels.append(
                LiquidityLevelRecord(
                    level_id=p.pool_id,
                    algorithm_version=p.algorithm_version,
                    symbol=p.symbol,
                    originating_timeframe=p.timeframe,
                    level_type=p.pool_type,
                    side=p.side,
                    price=p.representative_price,
                    tick_size=tick_size,
                    source_event_ids=p.member_swing_ids,
                    valid_from=p.confirmed_at,
                    available_at=p.available_at,
                    expires_at=None,
                    touch_count=p.touch_count,
                    state=p.state,
                )
            )
    else:
        for item in levels:
            if isinstance(item, LiquidityLevelRecord):
                raw_levels.append(item)
            elif isinstance(item, LiquidityPoolCluster):
                raw_levels.append(
                    LiquidityLevelRecord(
                        level_id=item.pool_id,
                        algorithm_version=item.algorithm_version,
                        symbol=item.symbol,
                        originating_timeframe=item.timeframe,
                        level_type=item.pool_type,
                        side=item.side,
                        price=item.representative_price,
                        tick_size=tick_size,
                        source_event_ids=item.member_swing_ids,
                        valid_from=item.confirmed_at,
                        available_at=item.available_at,
                        expires_at=None,
                        touch_count=item.touch_count,
                        state=item.state,
                    )
                )

    pol_hash = deterministic_hash({"pen": min_pen_ticks, "rec": reclaim_buf_ticks, "ts": tick_size}, 16)
    sweeps: List[SweepEvent] = []
    touches: List[Dict[str, Any]] = []
    non_reclaimed: List[Dict[str, Any]] = []
    consumed_level_ids: set[str] = {
        lvl.level_id for lvl in raw_levels if lvl.state in ("SWEPT", "CONSUMED", "INVALIDATED", "EXPIRED")
    }

    for idx, bar in enumerate(bars):
        if not bar.is_closed:
            continue
        if asof is not None and bar.close_time > asof:
            break

        for lvl in raw_levels:
            if lvl.level_id in consumed_level_ids:
                continue
            # Level must be causally available BEFORE this bar closes
            if lvl.available_at >= bar.close_time:
                continue
            if lvl.expires_at is not None and bar.close_time > lvl.expires_at:
                consumed_level_ids.add(lvl.level_id)
                continue

            if lvl.side == "BUY_SIDE":
                pen_ticks = price_to_ticks(bar.high - lvl.price, tick_size)
                if pen_ticks < 0:
                    continue
                if pen_ticks < min_pen_ticks:
                    touches.append(
                        {
                            "level_id": lvl.level_id,
                            "bar_id": bar.bar_id,
                            "bar_index": idx,
                            "side": "BUY_SIDE",
                            "penetration_ticks": pen_ticks,
                            "event_time": bar.close_time,
                        }
                    )
                    continue
                reclaim_inside_ticks = price_to_ticks(lvl.price - bar.close, tick_size)
                if reclaim_inside_ticks >= reclaim_buf_ticks:
                    ev_id = f"swp_{symbol}_{tf}_{lvl.level_id}_{int(bar.open_time.timestamp())}"
                    sweeps.append(
                        SweepEvent(
                            event_id=ev_id,
                            algorithm_version=MTF_ALGORITHM_VERSION,
                            symbol=symbol,
                            timeframe=tf,
                            swept_level_id=lvl.level_id,
                            swept_level_type=lvl.level_type,
                            swept_level_price=lvl.price,
                            side="BUY_SIDE_SWEEP",
                            implied_reversal_direction="BEARISH",
                            sweep_bar_id=bar.bar_id,
                            sweep_bar_index=idx,
                            extreme_price=bar.high,
                            reclaim_close_price=bar.close,
                            swept_ticks=pen_ticks,
                            reclaim_ticks_inside=reclaim_inside_ticks,
                            event_time=bar.open_time,
                            confirmed_at=bar.close_time,
                            available_at=bar.available_at,
                        )
                    )
                    consumed_level_ids.add(lvl.level_id)
                else:
                    non_reclaimed.append(
                        {
                            "level_id": lvl.level_id,
                            "bar_id": bar.bar_id,
                            "bar_index": idx,
                            "side": "BUY_SIDE",
                            "penetration_ticks": pen_ticks,
                            "close_price": bar.close,
                            "event_time": bar.close_time,
                        }
                    )
                    consumed_level_ids.add(lvl.level_id)

            elif lvl.side == "SELL_SIDE":
                pen_ticks = price_to_ticks(lvl.price - bar.low, tick_size)
                if pen_ticks < 0:
                    continue
                if pen_ticks < min_pen_ticks:
                    touches.append(
                        {
                            "level_id": lvl.level_id,
                            "bar_id": bar.bar_id,
                            "bar_index": idx,
                            "side": "SELL_SIDE",
                            "penetration_ticks": pen_ticks,
                            "event_time": bar.close_time,
                        }
                    )
                    continue
                reclaim_inside_ticks = price_to_ticks(bar.close - lvl.price, tick_size)
                if reclaim_inside_ticks >= reclaim_buf_ticks:
                    ev_id = f"swp_{symbol}_{tf}_{lvl.level_id}_{int(bar.open_time.timestamp())}"
                    sweeps.append(
                        SweepEvent(
                            event_id=ev_id,
                            algorithm_version=MTF_ALGORITHM_VERSION,
                            symbol=symbol,
                            timeframe=tf,
                            swept_level_id=lvl.level_id,
                            swept_level_type=lvl.level_type,
                            swept_level_price=lvl.price,
                            side="SELL_SIDE_SWEEP",
                            implied_reversal_direction="BULLISH",
                            sweep_bar_id=bar.bar_id,
                            sweep_bar_index=idx,
                            extreme_price=bar.low,
                            reclaim_close_price=bar.close,
                            swept_ticks=pen_ticks,
                            reclaim_ticks_inside=reclaim_inside_ticks,
                            event_time=bar.open_time,
                            confirmed_at=bar.close_time,
                            available_at=bar.available_at,
                        )
                    )
                    consumed_level_ids.add(lvl.level_id)
                else:
                    non_reclaimed.append(
                        {
                            "level_id": lvl.level_id,
                            "bar_id": bar.bar_id,
                            "bar_index": idx,
                            "side": "SELL_SIDE",
                            "penetration_ticks": pen_ticks,
                            "close_price": bar.close,
                            "event_time": bar.close_time,
                        }
                    )
                    consumed_level_ids.add(lvl.level_id)

    return SweepEvents(
        symbol=symbol,
        timeframe=tf,
        events=tuple(sweeps),
        touches=tuple(touches),
        non_reclaimed_breakouts=tuple(non_reclaimed),
        asof=asof,
        policy_hash=pol_hash,
    )


def update_liquidity_state(
    previous: Union[LiquidityLevels, LiquidityContext],
    touches: Sequence[Dict[str, Any]],
    sweeps: Union[SweepEvents, Sequence[SweepEvent]],
    structure_breaks: Union[StructureBreakEvents, Sequence[StructureBreakEvent]],
    asof: Union[datetime, str, int, float],
) -> LiquidityContext:
    """
    Section 5.D: Deterministic liquidity lifecycle state machine:
      `ACTIVE -> TOUCHED -> SWEPT / CONSUMED / INVALIDATED / EXPIRED`
    - Terminal states (`SWEPT`, `CONSUMED`, `INVALIDATED`, `EXPIRED`) are absorbing:
      once a level enters a terminal state, it cannot transition back to `ACTIVE` or `TOUCHED`
      or be swept a second time.
    """
    asof_dt = ensure_utc_datetime(asof)
    if asof_dt is None:
        raise ValueError("asof is required")

    if isinstance(previous, LiquidityLevels):
        base_levels = list(previous.levels)
        symbol = previous.symbol
        pdh_status = previous.pdh_pdl_status
        pdh_p = previous.pdh_price
        pdl_p = previous.pdl_price
    else:
        base_levels = list(previous.levels)
        symbol = previous.symbol
        pdh_status = previous.pdh_pdl_status
        pdh_p = previous.pdh_price
        pdl_p = previous.pdl_price

    raw_sweeps = list(sweeps.events if isinstance(sweeps, SweepEvents) else sweeps)
    non_reclaimed = list(sweeps.non_reclaimed_breakouts if isinstance(sweeps, SweepEvents) else ())
    raw_breaks = list(structure_breaks.events if isinstance(structure_breaks, StructureBreakEvents) else structure_breaks)

    causal_sweeps = [s for s in raw_sweeps if s.available_at <= asof_dt and s.confirmed_at <= asof_dt]
    causal_breaks = [b for b in raw_breaks if b.available_at <= asof_dt and b.confirmed_at <= asof_dt]
    causal_touches = [
        t for t in touches if ensure_utc_datetime(t.get("event_time")) is not None and ensure_utc_datetime(t.get("event_time")) <= asof_dt
    ]
    causal_non_reclaimed = [
        nr for nr in non_reclaimed if ensure_utc_datetime(nr.get("event_time")) is not None and ensure_utc_datetime(nr.get("event_time")) <= asof_dt
    ]

    sweep_by_lvl: Dict[str, SweepEvent] = {}
    for s in causal_sweeps:
        if s.swept_level_id not in sweep_by_lvl:
            sweep_by_lvl[s.swept_level_id] = s

    touch_by_lvl: Dict[str, Dict[str, Any]] = {}
    for t in causal_touches:
        lid = str(t.get("level_id"))
        touch_by_lvl[lid] = t

    nr_by_lvl: Dict[str, Dict[str, Any]] = {}
    for nr in causal_non_reclaimed:
        lid = str(nr.get("level_id"))
        if lid not in nr_by_lvl:
            nr_by_lvl[lid] = nr

    broken_swing_to_break: Dict[str, StructureBreakEvent] = {b.broken_swing_id: b for b in causal_breaks}

    updated_levels: List[LiquidityLevelRecord] = []
    transitions: List[Dict[str, Any]] = []
    terminal_states = {"SWEPT", "CONSUMED", "INVALIDATED", "EXPIRED"}

    for lvl in base_levels:
        if lvl.available_at > asof_dt:
            continue
        old_state = lvl.state
        new_state = old_state
        trans_at = lvl.last_transition_at
        trans_reason = lvl.last_transition_reason
        touch_cnt = lvl.touch_count

        if old_state not in terminal_states:
            # Check expiration first if it expired before any sweep/break
            if lvl.expires_at is not None and asof_dt > lvl.expires_at:
                new_state = "EXPIRED"
                trans_at = lvl.expires_at
                trans_reason = "POOL_MAX_AGE_EXCEEDED"

            # Check sweep (highest priority event on the level before expiration)
            if lvl.level_id in sweep_by_lvl:
                sw_ev = sweep_by_lvl[lvl.level_id]
                if lvl.expires_at is None or sw_ev.confirmed_at <= lvl.expires_at:
                    new_state = "SWEPT"
                    trans_at = sw_ev.confirmed_at
                    trans_reason = f"SWEPT_BY_{sw_ev.event_id}"
                    touch_cnt += 1

            # Check structure break / non-reclaimed close-through invalidation
            if new_state not in ("SWEPT",):
                matched_break = None
                for src_id in lvl.source_event_ids:
                    if src_id in broken_swing_to_break:
                        matched_break = broken_swing_to_break[src_id]
                        break
                if matched_break is not None and (lvl.expires_at is None or matched_break.confirmed_at <= lvl.expires_at):
                    new_state = "INVALIDATED"
                    trans_at = matched_break.confirmed_at
                    trans_reason = f"BROKEN_BY_{matched_break.break_type}_{matched_break.event_id}"
                elif lvl.level_id in nr_by_lvl:
                    nr_ev = nr_by_lvl[lvl.level_id]
                    nr_dt = ensure_utc_datetime(nr_ev.get("event_time"))
                    if lvl.expires_at is None or (nr_dt and nr_dt <= lvl.expires_at):
                        new_state = "CONSUMED"
                        trans_at = nr_dt
                        trans_reason = f"CLOSED_BEYOND_LEVEL_BAR_{nr_ev.get('bar_id')}"

            # Check touch if still ACTIVE
            if new_state == "ACTIVE" and lvl.level_id in touch_by_lvl:
                t_ev = touch_by_lvl[lvl.level_id]
                new_state = "TOUCHED"
                trans_at = ensure_utc_datetime(t_ev.get("event_time"))
                trans_reason = f"TOUCHED_BY_{t_ev.get('bar_id')}"
                touch_cnt += 1

        if new_state != old_state:
            transitions.append(
                {
                    "level_id": lvl.level_id,
                    "from_state": old_state,
                    "to_state": new_state,
                    "at": format_utc_iso(trans_at),
                    "reason": trans_reason,
                }
            )

        updated_levels.append(
            LiquidityLevelRecord(
                level_id=lvl.level_id,
                algorithm_version=lvl.algorithm_version,
                symbol=lvl.symbol,
                originating_timeframe=lvl.originating_timeframe,
                level_type=lvl.level_type,
                side=lvl.side,
                price=lvl.price,
                tick_size=lvl.tick_size,
                source_event_ids=lvl.source_event_ids,
                valid_from=lvl.valid_from,
                available_at=lvl.available_at,
                expires_at=lvl.expires_at,
                touch_count=touch_cnt,
                state=new_state,
                last_transition_at=trans_at,
                last_transition_reason=trans_reason,
            )
        )

    active_lvls = tuple(l for l in updated_levels if l.state in ("ACTIVE", "TOUCHED"))
    prov_hash = deterministic_hash(
        {
            "asof": format_utc_iso(asof_dt),
            "levels": [(l.level_id, l.state) for l in updated_levels],
            "sweeps": [s.event_id for s in causal_sweeps],
        },
        length=24,
    )

    return LiquidityContext(
        context_id=f"lctx_{symbol}_{prov_hash[:12]}",
        algorithm_version=MTF_ALGORITHM_VERSION,
        symbol=symbol,
        asof=asof_dt,
        levels=tuple(updated_levels),
        active_levels=active_lvls,
        sweeps=tuple(causal_sweeps),
        pdh_pdl_status=pdh_status,
        pdh_price=pdh_p,
        pdl_price=pdl_p,
        state_transitions=tuple(transitions),
        provenance_hash=prov_hash,
    )


def summarize_liquidity_for_evaluator(
    mtf_structure: MtfStructureContext,
    liquidity: LiquidityContext,
    asof: Union[datetime, str, int, float],
    reference_price: Optional[float] = None,
    freshness_seconds: int = 10800,
    tick_size: float = DEFAULT_TICK_SIZE,
) -> EvaluatorLiquidityContext:
    """
    Section 5.E: Summarize causal MTF liquidity & sweep context for the shared evaluator
    without placing orders or mutating SL/TP.
    """
    asof_dt = ensure_utc_datetime(asof)
    if asof_dt is None:
        raise ValueError("asof is required")

    ref_p = reference_price
    if ref_p is None:
        if mtf_structure.m15.state.equilibrium_price is not None:
            ref_p = mtf_structure.m15.state.equilibrium_price
        elif mtf_structure.h1.state.equilibrium_price is not None:
            ref_p = mtf_structure.h1.state.equilibrium_price

    buy_candidates = [
        l for l in liquidity.active_levels if l.side == "BUY_SIDE" and (ref_p is None or l.price >= ref_p)
    ]
    sell_candidates = [
        l for l in liquidity.active_levels if l.side == "SELL_SIDE" and (ref_p is None or l.price <= ref_p)
    ]
    buy_candidates.sort(key=lambda l: abs(l.price - (ref_p or l.price)))
    sell_candidates.sort(key=lambda l: abs(l.price - (ref_p or l.price)))

    nearest_buy = buy_candidates[0] if buy_candidates else None
    nearest_sell = sell_candidates[0] if sell_candidates else None

    dist_buy = price_to_ticks(nearest_buy.price - ref_p, tick_size) if (nearest_buy and ref_p is not None) else None
    dist_sell = price_to_ticks(ref_p - nearest_sell.price, tick_size) if (nearest_sell and ref_p is not None) else None

    causal_sweeps = [s for s in liquidity.sweeps if s.available_at <= asof_dt]
    causal_sweeps.sort(key=lambda s: s.confirmed_at)
    last_sweep = causal_sweeps[-1] if causal_sweeps else None

    if last_sweep is not None:
        sec_since = max(0, int((asof_dt - last_sweep.confirmed_at).total_seconds()))
        is_fresh = sec_since <= freshness_seconds
    else:
        sec_since = None
        is_fresh = False

    if last_sweep is None:
        sweep_align = "NO_SWEEP"
    else:
        rev_dir = last_sweep.implied_reversal_direction
        if mtf_structure.regime_bias == rev_dir or mtf_structure.execution_bias == rev_dir:
            sweep_align = "ALIGNED"
        elif mtf_structure.regime_bias in ("BULLISH", "BEARISH") and mtf_structure.regime_bias != rev_dir:
            sweep_align = "CONFLICT"
        else:
            sweep_align = "ALIGNED"

    # Determine primary draw on liquidity
    if is_fresh and last_sweep is not None:
        draw_side = "BUY_SIDE" if last_sweep.implied_reversal_direction == "BULLISH" else "SELL_SIDE"
    elif mtf_structure.regime_bias == "BULLISH" and nearest_buy is not None:
        draw_side = "BUY_SIDE"
    elif mtf_structure.regime_bias == "BEARISH" and nearest_sell is not None:
        draw_side = "SELL_SIDE"
    elif dist_buy is not None and dist_sell is not None:
        draw_side = "BUY_SIDE" if dist_buy <= dist_sell else "SELL_SIDE"
    elif nearest_buy is not None:
        draw_side = "BUY_SIDE"
    elif nearest_sell is not None:
        draw_side = "SELL_SIDE"
    else:
        draw_side = "NONE"

    prov_hash = deterministic_hash(
        {
            "mtf_structure": mtf_structure.provenance_hash,
            "liquidity": liquidity.provenance_hash,
            "ref_p": ref_p,
            "draw": draw_side,
        },
        length=24,
    )

    return EvaluatorLiquidityContext(
        summary_id=f"eval_liq_{mtf_structure.symbol}_{prov_hash[:12]}",
        algorithm_version=MTF_ALGORITHM_VERSION,
        symbol=mtf_structure.symbol,
        asof=asof_dt,
        reference_price=ref_p,
        nearest_buy_side_level=nearest_buy,
        nearest_sell_side_level=nearest_sell,
        distance_to_buy_side_ticks=dist_buy,
        distance_to_sell_side_ticks=dist_sell,
        draw_on_liquidity_side=draw_side,
        has_fresh_sweep=is_fresh,
        last_sweep=last_sweep,
        seconds_since_last_sweep=sec_since,
        structure_alignment_with_sweep=sweep_align,
        pdh_pdl_status=liquidity.pdh_pdl_status,
        quality_state=mtf_structure.quality_state,
        provenance_hash=prov_hash,
    )


# ============================================================================
# SECTION 6 & 7 — BOUNDED LRU CONTEXT CACHE & SHARED EVALUATOR INTEGRATION
# ============================================================================

@dataclass(frozen=True)
class MtfEvaluationContext:
    mtf_context_id: str
    mtf_context_hash: str
    mtf_algorithm_version: str
    mtf_quality_state: str
    symbol: str
    decision_time: datetime
    config: MtfStructureLiquidityConfig
    snapshot: MtfSnapshot
    causal_validation: CausalValidation
    mtf_structure: MtfStructureContext
    liquidity_context: LiquidityContext
    evaluator_liquidity: EvaluatorLiquidityContext
    cache_hit: bool = False

    def to_summary_dict(self) -> Dict[str, Any]:
        return {
            "mtf_context_id": self.mtf_context_id,
            "mtf_context_hash": self.mtf_context_hash,
            "mtf_algorithm_version": self.mtf_algorithm_version,
            "mtf_quality_state": self.mtf_quality_state,
            "symbol": self.symbol,
            "decision_time": format_utc_iso(self.decision_time),
            "alignment": self.mtf_structure.alignment,
            "regime_bias": self.mtf_structure.regime_bias,
            "intermediate_status": self.mtf_structure.intermediate_status,
            "execution_bias": self.mtf_structure.execution_bias,
            "h4_trend": self.mtf_structure.h4.trend,
            "h1_trend": self.mtf_structure.h1.trend,
            "m15_trend": self.mtf_structure.m15.trend,
            "draw_on_liquidity_side": self.evaluator_liquidity.draw_on_liquidity_side,
            "has_fresh_sweep": self.evaluator_liquidity.has_fresh_sweep,
            "pdh_pdl_status": self.evaluator_liquidity.pdh_pdl_status,
            "pdh_price": self.liquidity_context.pdh_price,
            "pdl_price": self.liquidity_context.pdl_price,
            "active_liquidity_pools": len(self.liquidity_context.active_levels),
            "sweeps_count": len(self.liquidity_context.sweeps),
            "causal_valid": self.causal_validation.is_valid,
        }


class MtfContextCache:
    """
    Section 7: Bounded immutable LRU cache keyed by:
      `(symbol, timeframe, source_hash, config_hash, last_closed_bar_id, algorithm_version)`
    Instance-scoped (no shared mutable global state across worker processes).
    """

    def __init__(self, max_entries: int = 256) -> None:
        self.max_entries = max(8, int(max_entries))
        self._tf_store: OrderedDict[Tuple[str, str, str, str, str, str], StructureContext] = OrderedDict()
        self._mtf_store: OrderedDict[Tuple[str, str, str, str, str], MtfEvaluationContext] = OrderedDict()
        self.hits: int = 0
        self.misses: int = 0

    def get_tf_structure(
        self,
        symbol: str,
        timeframe: str,
        source_hash: str,
        config_hash: str,
        last_closed_bar_id: Optional[str],
        algorithm_version: str = MTF_ALGORITHM_VERSION,
    ) -> Optional[StructureContext]:
        key = (
            symbol.upper(),
            canonical_timeframe(timeframe),
            source_hash,
            config_hash,
            str(last_closed_bar_id or "none"),
            algorithm_version,
        )
        val = self._tf_store.get(key)
        if val is not None:
            self._tf_store.move_to_end(key)
            self.hits += 1
            return val
        self.misses += 1
        return None

    def put_tf_structure(
        self,
        symbol: str,
        timeframe: str,
        source_hash: str,
        config_hash: str,
        last_closed_bar_id: Optional[str],
        ctx: StructureContext,
        algorithm_version: str = MTF_ALGORITHM_VERSION,
    ) -> None:
        key = (
            symbol.upper(),
            canonical_timeframe(timeframe),
            source_hash,
            config_hash,
            str(last_closed_bar_id or "none"),
            algorithm_version,
        )
        self._tf_store[key] = ctx
        self._tf_store.move_to_end(key)
        while len(self._tf_store) > self.max_entries:
            self._tf_store.popitem(last=False)

    def clear(self) -> None:
        self._tf_store.clear()
        self._mtf_store.clear()


DEFAULT_MTF_CONTEXT_CACHE = MtfContextCache(max_entries=512)



def _coerce_mtf_config(config: Optional[Any]) -> MtfStructureLiquidityConfig:
    if isinstance(config, MtfStructureLiquidityConfig):
        return config
    if isinstance(config, dict):
        allowed = {f.name for f in MtfStructureLiquidityConfig.__dataclass_fields__.values()}
        filtered = {k: v for k, v in config.items() if k in allowed and v is not None}
        return MtfStructureLiquidityConfig(**filtered)
    if config is not None:
        allowed = {f.name for f in MtfStructureLiquidityConfig.__dataclass_fields__.values()}
        filtered = {k: getattr(config, k) for k in allowed if hasattr(config, k) and getattr(config, k) is not None}
        return MtfStructureLiquidityConfig(**filtered)
    return MtfStructureLiquidityConfig()


def build_mtf_context(
    decision_time: Union[datetime, str, int, float],
    market_data_source: Union[MtfSnapshot, Dict[str, Any]],
    config: Optional[Any] = None,
    cache: Optional[MtfContextCache] = None,
    reference_price: Optional[float] = None,
) -> MtfEvaluationContext:
    """
    Section 6: Single unified MTF Structure & Liquidity context builder consumed identically
    by Production, MARKET_SHADOW, and Chronological Backtest.
    """
    cfg = _coerce_mtf_config(config)
    asof_dt = ensure_utc_datetime(decision_time)
    if asof_dt is None:
        raise ValueError("decision_time is required")

    if isinstance(market_data_source, MtfSnapshot):
        snap = market_data_source
    else:
        snap = build_mtf_bar_snapshot(asof_dt, market_data_source, settings=cfg)

    cfg_hash = cfg.config_hash()
    tf_contexts: Dict[str, StructureContext] = {}
    all_sweeps: List[SweepEvent] = []
    all_touches: List[Dict[str, Any]] = []
    all_non_reclaimed: List[Dict[str, Any]] = []
    all_breaks: List[StructureBreakEvent] = []
    was_cache_hit = False

    for tf in ("H4", "H1", "M15"):
        win = snap.get_window(tf)
        if win is None:
            win = ClosedBarWindow(
                symbol=snap.symbol,
                timeframe=tf,
                asof=asof_dt,
                last_closed_at=None,
                available_at=None,
                bars=(),
                source_hash="missing",
                count=0,
                excluded_future_count=0,
                excluded_partial_count=0,
                missing_gap_count=0,
                quality_state=MtfDataQualityState.INSUFFICIENT_HISTORY,
            )

        cached_ctx: Optional[StructureContext] = None
        if cache is not None and win.quality_state == MtfDataQualityState.VALID and win.last_closed_bar_id is not None:
            cached_ctx = cache.get_tf_structure(
                snap.symbol,
                tf,
                win.source_hash,
                cfg_hash,
                win.last_closed_bar_id,
                cfg.algorithm_version,
            )

        if cached_ctx is not None:
            was_cache_hit = True
            # Update asof to current decision_time while preserving immutable events
            tf_ctx = StructureContext(
                context_id=cached_ctx.context_id,
                algorithm_version=cached_ctx.algorithm_version,
                symbol=cached_ctx.symbol,
                timeframe=cached_ctx.timeframe,
                asof=asof_dt,
                trend=cached_ctx.trend,
                state=cached_ctx.state,
                breaks=cached_ctx.breaks,
                last_bos=cached_ctx.last_bos,
                last_choch=cached_ctx.last_choch,
                last_break=cached_ctx.last_break,
                last_break_age_bars=cached_ctx.last_break_age_bars,
                protected_high_price=cached_ctx.protected_high_price,
                protected_low_price=cached_ctx.protected_low_price,
                protected_high_invalidated=cached_ctx.protected_high_invalidated,
                protected_low_invalidated=cached_ctx.protected_low_invalidated,
                quality_state=cached_ctx.quality_state,
                provenance_hash=cached_ctx.provenance_hash,
            )
        else:
            left_b, right_b = cfg.swing_window_for(tf)
            swings = detect_confirmed_swings(
                win,
                left=left_b,
                right=right_b,
                tolerance_ticks=cfg.swing_equal_tolerance_ticks,
            )
            st_state = classify_swing_sequence(swings, tf, tick_size=cfg.tick_size)
            breaks = detect_structure_break(win, st_state, break_policy=cfg)
            tf_ctx = update_structure_state(
                None,
                swings,
                breaks,
                asof=asof_dt,
                total_bars_count=win.count,
            )
            if cache is not None and win.quality_state == MtfDataQualityState.VALID and win.last_closed_bar_id is not None:
                cache.put_tf_structure(
                    snap.symbol,
                    tf,
                    win.source_hash,
                    cfg_hash,
                    win.last_closed_bar_id,
                    tf_ctx,
                    cfg.algorithm_version,
                )

        tf_contexts[tf] = tf_ctx
        all_breaks.extend(tf_ctx.breaks)

    mtf_structure = build_multi_timeframe_structure(
        tf_contexts["H4"],
        tf_contexts["H1"],
        tf_contexts["M15"],
        policy=cfg,
    )

    ref_levels = derive_reference_liquidity_levels(snap, mtf_structure, policy=cfg)

    # Detect sweeps on M15 (and H1/H4) closed bars against derived levels
    for tf in ("H4", "H1", "M15"):
        win = snap.get_window(tf)
        if win is not None and win.count > 0:
            tf_levels = LiquidityLevels(
                symbol=ref_levels.symbol,
                asof=asof_dt,
                levels=tuple(l for l in ref_levels.levels if l.originating_timeframe == tf or (tf == "M15" and l.originating_timeframe == "D1")),
                pdh_pdl_status=ref_levels.pdh_pdl_status,
                prior_session_date=ref_levels.prior_session_date,
                pdh_price=ref_levels.pdh_price,
                pdl_price=ref_levels.pdl_price,
                provenance_hash=ref_levels.provenance_hash,
            )
            swp_res = detect_liquidity_sweep(win, tf_levels, sweep_policy=cfg)
            all_sweeps.extend(swp_res.events)
            all_touches.extend(swp_res.touches)
            all_non_reclaimed.extend(swp_res.non_reclaimed_breakouts)

    combined_sweeps = SweepEvents(
        symbol=snap.symbol,
        timeframe="MTF",
        events=tuple(sorted(all_sweeps, key=lambda s: (s.confirmed_at, s.event_id))),
        touches=tuple(all_touches),
        non_reclaimed_breakouts=tuple(all_non_reclaimed),
        asof=asof_dt,
        policy_hash=cfg_hash,
    )

    liq_ctx = update_liquidity_state(
        ref_levels,
        all_touches,
        combined_sweeps,
        all_breaks,
        asof=asof_dt,
    )

    if reference_price is None:
        m15_w = snap.get_window("M15")
        if m15_w and m15_w.bars:
            reference_price = m15_w.bars[-1].close

    eval_liq = summarize_liquidity_for_evaluator(
        mtf_structure,
        liq_ctx,
        asof=asof_dt,
        reference_price=reference_price,
        freshness_seconds=cfg.sweep_freshness_bars * 900,
        tick_size=cfg.tick_size,
    )

    causal_val = validate_causal_window(
        {
            "snapshot": snap,
            "mtf_structure": mtf_structure,
            "liquidity_context": liq_ctx,
        },
        asof_dt,
    )

    if not causal_val.is_valid:
        overall_q = MtfDataQualityState.FUTURE_LEAK
    elif snap.quality_state != MtfDataQualityState.VALID:
        overall_q = snap.quality_state
    else:
        overall_q = mtf_structure.quality_state

    ctx_hash = deterministic_hash(
        {
            "alg": cfg.algorithm_version,
            "snap": snap.source_hash,
            "cfg": cfg_hash,
            "str": mtf_structure.provenance_hash,
            "liq": liq_ctx.provenance_hash,
            "eval_liq": eval_liq.provenance_hash,
            "q": overall_q,
        },
        length=24,
    )
    ctx_id = f"mtfctx_{snap.symbol}_{ctx_hash[:12]}"

    return MtfEvaluationContext(
        mtf_context_id=ctx_id,
        mtf_context_hash=ctx_hash,
        mtf_algorithm_version=cfg.algorithm_version,
        mtf_quality_state=overall_q,
        symbol=snap.symbol,
        decision_time=asof_dt,
        config=cfg,
        snapshot=snap,
        causal_validation=causal_val,
        mtf_structure=mtf_structure,
        liquidity_context=liq_ctx,
        evaluator_liquidity=eval_liq,
        cache_hit=was_cache_hit,
    )


def evaluate_mtf_eligibility_for_direction(
    direction: str,
    mtf_context: Optional[MtfEvaluationContext],
    ablation_mode: str = "FULL",
) -> Tuple[bool, Optional[str], str]:
    """
    Evaluate whether trade `direction` ("LONG" | "SHORT") is eligible under the
    causal MTF Structure & Liquidity filter when `mtf_structure_eligibility_enabled=True`.
    Supports ablation modes:
      - `FULL`: H4/H1/M15 structure alignment + liquidity sweep/room check
      - `H4_ONLY`: H4 structure trend filter only
      - `H1_ONLY`: H1 structure trend filter only
      - `M15_ONLY`: M15 structure trend filter only
      - `STRUCTURE_ONLY`: H4/H1/M15 structure alignment without liquidity gating
      - `LIQUIDITY_ONLY`: Liquidity room & opposing sweep rejection without structure gating
    Returns `(is_eligible, rejection_code, detail_message)`.
    """
    norm_dir = str(direction or "").strip().upper()
    if norm_dir in ("BUY", "BULLISH"):
        norm_dir = "LONG"
    elif norm_dir in ("SELL", "BEARISH"):
        norm_dir = "SHORT"

    if mtf_context is None:
        return (False, "MTF_MISSING_DATA", "MTF context is missing while mtf_structure_eligibility_enabled=True")

    if not mtf_context.causal_validation.is_valid:
        return (False, "MTF_FUTURE_LEAK_BLOCKED", ";".join(mtf_context.causal_validation.reason_codes))

    expected_trend = "BULLISH" if norm_dir == "LONG" else "BEARISH"
    opposing_trend = "BEARISH" if norm_dir == "LONG" else "BULLISH"
    s = mtf_context.mtf_structure
    liq = mtf_context.evaluator_liquidity
    mode = str(ablation_mode or mtf_context.config.mtf_ablation_mode or "FULL").upper()

    # Check hard data quality errors first
    if mtf_context.mtf_quality_state in (
        MtfDataQualityState.SOURCE_MISMATCH,
        MtfDataQualityState.CORRUPT_OHLC,
        MtfDataQualityState.BAD_TIME_ALIGNMENT,
        MtfDataQualityState.STALE_HTF,
    ):
        return (
            False,
            "MTF_MISSING_DATA",
            f"MTF quality_state={mtf_context.mtf_quality_state} insufficient for eligibility gating",
        )

    # 1. Single-timeframe ablations check their own timeframe quality state
    if mode == "H4_ONLY":
        if s.h4.quality_state != MtfDataQualityState.VALID:
            return (False, "MTF_MISSING_DATA", f"H4_ONLY: H4 quality_state={s.h4.quality_state}")
        if s.h4.trend == opposing_trend:
            return (False, "MTF_STRUCTURE_FILTER_REJECT", f"H4_ONLY: H4 trend {s.h4.trend} opposes {norm_dir}")
        return (True, None, "H4_ONLY_PASS")

    if mode == "H1_ONLY":
        if s.h1.quality_state != MtfDataQualityState.VALID:
            return (False, "MTF_MISSING_DATA", f"H1_ONLY: H1 quality_state={s.h1.quality_state}")
        if s.h1.trend == opposing_trend:
            return (False, "MTF_STRUCTURE_FILTER_REJECT", f"H1_ONLY: H1 trend {s.h1.trend} opposes {norm_dir}")
        return (True, None, "H1_ONLY_PASS")

    if mode == "M15_ONLY":
        if s.m15.quality_state != MtfDataQualityState.VALID:
            return (False, "MTF_MISSING_DATA", f"M15_ONLY: M15 quality_state={s.m15.quality_state}")
        if s.m15.trend == opposing_trend:
            return (False, "MTF_STRUCTURE_FILTER_REJECT", f"M15_ONLY: M15 trend {s.m15.trend} opposes {norm_dir}")
        return (True, None, "M15_ONLY_PASS")

    if s.m15.quality_state != MtfDataQualityState.VALID and s.h1.quality_state != MtfDataQualityState.VALID:
        return (
            False,
            "MTF_MISSING_DATA",
            f"MTF quality_state={mtf_context.mtf_quality_state} insufficient for eligibility gating",
        )

    # 2. Structure check (active in FULL and STRUCTURE_ONLY)
    if mode in ("FULL", "STRUCTURE_ONLY"):
        if norm_dir == "LONG" and s.alignment == "ALIGNED_BEAR":
            return (False, "MTF_CONFLICT", "All MTF timeframes are ALIGNED_BEAR against LONG")
        if norm_dir == "SHORT" and s.alignment == "ALIGNED_BULL":
            return (False, "MTF_CONFLICT", "All MTF timeframes are ALIGNED_BULL against SHORT")

        # If H4 and H1 both oppose trade direction without an M15 CHoCH in trade direction
        if s.h4.trend == opposing_trend and s.h1.trend == opposing_trend:
            has_m15_choch = (
                s.m15.last_choch is not None
                and s.m15.last_choch.direction == expected_trend
                and (s.m15.last_break_age_bars is not None and s.m15.last_break_age_bars <= 4)
            )
            if not has_m15_choch:
                return (
                    False,
                    "MTF_CONFLICT",
                    f"H4 ({s.h4.trend}) and H1 ({s.h1.trend}) oppose {norm_dir} without fresh M15 CHoCH",
                )

        # If H1 and M15 both oppose trade direction
        if s.h1.trend == opposing_trend and s.m15.trend == opposing_trend:
            return (
                False,
                "MTF_STRUCTURE_FILTER_REJECT",
                f"H1 ({s.h1.trend}) and M15 ({s.m15.trend}) structure oppose {norm_dir}",
            )

    # 3. Liquidity check (active in FULL and LIQUIDITY_ONLY)
    if mode in ("FULL", "LIQUIDITY_ONLY"):
        # Reject if a fresh liquidity sweep just occurred in the OPPOSING reversal direction
        if liq.has_fresh_sweep and liq.last_sweep is not None:
            if liq.last_sweep.implied_reversal_direction == opposing_trend:
                return (
                    False,
                    "MTF_LIQUIDITY_FILTER_REJECT",
                    f"Fresh {liq.last_sweep.side} implies {opposing_trend} reversal against {norm_dir}",
                )

        # Reject if trading directly into an adjacent opposing pool with less than minimum room
        min_room = mtf_context.config.OpposingPoolMinRoomTicks
        if norm_dir == "LONG" and liq.distance_to_buy_side_ticks is not None:
            if 0 < liq.distance_to_buy_side_ticks < min_room and liq.nearest_buy_side_level is not None:
                if liq.nearest_buy_side_level.level_type in ("EQH", "PDH"):
                    return (
                        False,
                        "MTF_LIQUIDITY_FILTER_REJECT",
                        f"Insufficient room ({liq.distance_to_buy_side_ticks} ticks) below major {liq.nearest_buy_side_level.level_type}",
                    )
        elif norm_dir == "SHORT" and liq.distance_to_sell_side_ticks is not None:
            if 0 < liq.distance_to_sell_side_ticks < min_room and liq.nearest_sell_side_level is not None:
                if liq.nearest_sell_side_level.level_type in ("EQL", "PDL"):
                    return (
                        False,
                        "MTF_LIQUIDITY_FILTER_REJECT",
                        f"Insufficient room ({liq.distance_to_sell_side_ticks} ticks) above major {liq.nearest_sell_side_level.level_type}",
                    )

    return (True, None, f"{mode}_PASS")


def attach_context_to_evaluation(
    evaluation_input: Any,
    mtf_context: Optional[MtfEvaluationContext],
) -> Any:
    """
    Section 6: Attach immutable `MtfEvaluationContext` diagnostics to `MarketDecisionContext`
    or a raw evaluation dictionary without mutating baseline market_input_hash when flag=OFF.
    """
    if mtf_context is None:
        return evaluation_input

    summary = mtf_context.to_summary_dict()
    if isinstance(evaluation_input, dict):
        enriched = dict(evaluation_input)
        enriched["mtf_context"] = mtf_context
        enriched["mtf_context_id"] = mtf_context.mtf_context_id
        enriched["mtf_context_hash"] = mtf_context.mtf_context_hash
        enriched["mtf_algorithm_version"] = mtf_context.mtf_algorithm_version
        enriched["mtf_quality_state"] = mtf_context.mtf_quality_state
        enriched["mtf_structure_bias_summary"] = {
            "alignment": mtf_context.mtf_structure.alignment,
            "regime_bias": mtf_context.mtf_structure.regime_bias,
            "intermediate_status": mtf_context.mtf_structure.intermediate_status,
            "execution_bias": mtf_context.mtf_structure.execution_bias,
            "h4_trend": mtf_context.mtf_structure.h4.trend,
            "h1_trend": mtf_context.mtf_structure.h1.trend,
            "m15_trend": mtf_context.mtf_structure.m15.trend,
        }
        enriched["mtf_liquidity_summary"] = mtf_context.evaluator_liquidity.to_dict()
        return enriched

    # Attach to MarketDecisionContext instance
    for attr_name, attr_val in (
        ("mtf_context", mtf_context),
        ("mtf_context_id", mtf_context.mtf_context_id),
        ("mtf_context_hash", mtf_context.mtf_context_hash),
        ("mtf_algorithm_version", mtf_context.mtf_algorithm_version),
        ("mtf_quality_state", mtf_context.mtf_quality_state),
        ("mtf_structure_bias_summary", summary),
        ("mtf_liquidity_summary", mtf_context.evaluator_liquidity.to_dict()),
    ):
        try:
            object.__setattr__(evaluation_input, attr_name, attr_val)
        except Exception:
            pass
    return evaluation_input


def serialize_decision_evidence(
    decision: Any,
    mtf_context: Optional[MtfEvaluationContext] = None,
) -> Dict[str, Any]:
    """
    Section 6: Deterministic evidence payload combining the consumed `StrategyDecisionSnapshot`
    with the exact `MtfEvaluationContext` provenance.
    """
    if hasattr(decision, "to_parity_dict"):
        base = dict(decision.to_parity_dict())
    elif isinstance(decision, dict):
        base = dict(decision)
    else:
        base = {"decision": str(decision)}

    ctx = mtf_context or getattr(decision, "mtf_context", None)
    if ctx is not None and isinstance(ctx, MtfEvaluationContext):
        base["mtf_evidence"] = {
            "mtf_context_id": ctx.mtf_context_id,
            "mtf_context_hash": ctx.mtf_context_hash,
            "mtf_algorithm_version": ctx.mtf_algorithm_version,
            "mtf_quality_state": ctx.mtf_quality_state,
            "observe_only": ctx.config.mtf_structure_observe_only,
            "eligibility_enabled": ctx.config.mtf_structure_eligibility_enabled,
            "ablation_mode": ctx.config.mtf_ablation_mode,
            "snapshot_source_hash": ctx.snapshot.source_hash,
            "config_hash": ctx.snapshot.config_hash,
            "structure_summary": ctx.to_summary_dict(),
            "liquidity_summary": ctx.evaluator_liquidity.to_dict(),
        }
    else:
        base["mtf_evidence"] = {
            "mtf_context_id": getattr(decision, "mtf_context_id", base.get("mtf_context_id")),
            "mtf_context_hash": getattr(decision, "mtf_context_hash", base.get("mtf_context_hash")),
            "mtf_algorithm_version": getattr(decision, "mtf_algorithm_version", base.get("mtf_algorithm_version", MTF_ALGORITHM_VERSION)),
            "mtf_quality_state": getattr(decision, "mtf_quality_state", base.get("mtf_quality_state")),
        }
    base["evidence_payload_hash"] = deterministic_hash(base, length=24)
    return base


def compare_actual_consumed_decisions(
    prod_out: Any,
    shadow_out: Any,
    backtest_out: Any,
    independent_oracle: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Section 6: Compare actual consumed decisions across Production, MARKET_SHADOW,
    and Chronological Backtest (plus optional independent oracle), verifying both
    the 20 foundation parity fields and V17.9.1 MTF context hashes.
    Fails closed (`PARITY_NOT_ESTABLISHED`) if any mode is missing, self-extracted
    from the same object reference, or diverges on any consumed field.
    """
    def _extract_dict(obj: Any) -> Dict[str, Any]:
        if hasattr(obj, "to_parity_dict"):
            d = dict(obj.to_parity_dict())
            for k in ("mtf_context_id", "mtf_context_hash", "mtf_algorithm_version", "mtf_quality_state"):
                if k not in d and hasattr(obj, k):
                    d[k] = getattr(obj, k)
            return d
        if isinstance(obj, dict):
            if "consumed_decision" in obj and hasattr(obj["consumed_decision"], "to_parity_dict"):
                return _extract_dict(obj["consumed_decision"])
            return dict(obj)
        raise TypeError(f"Unsupported decision output type: {type(obj)}")

    if prod_out is None or shadow_out is None or backtest_out is None:
        return {
            "status": "PARITY_NOT_ESTABLISHED",
            "is_parity": False,
            "reason": "MISSING_MODE_OUTPUT",
            "mismatches": ["One or more of prod_out, shadow_out, backtest_out is None"],
        }

    # Guard against circular self-extraction (passing the exact same object instance)
    if prod_out is shadow_out or prod_out is backtest_out or shadow_out is backtest_out:
        return {
            "status": "PARITY_NOT_ESTABLISHED",
            "is_parity": False,
            "reason": "SELF_EXTRACTED_IDENTICAL_OBJECT_INSTANCE",
            "mismatches": ["Modes must be independently produced instances, not the same object reference"],
        }
    if independent_oracle is not None and (
        independent_oracle is prod_out or independent_oracle is shadow_out or independent_oracle is backtest_out
    ):
        return {
            "status": "PARITY_NOT_ESTABLISHED",
            "is_parity": False,
            "reason": "ORACLE_SELF_EXTRACTION_DETECTED",
            "mismatches": ["Independent oracle cannot be the same object reference as runtime output"],
        }

    p_dict = _extract_dict(prod_out)
    s_dict = _extract_dict(shadow_out)
    b_dict = _extract_dict(backtest_out)
    o_dict = _extract_dict(independent_oracle) if independent_oracle is not None else None

    compared_fields = [
        "symbol",
        "decision_timestamp",
        "source_fingerprint",
        "selected_model",
        "direction",
        "setup_validity",
        "entry_Status",
        "entry_price",
        "stop_loss",
        "take_profit",
        "rr_ratio",
        "sizing_tier",
        "final_action",
        "rejection_code",
        "blocked_by",
        "safety_gate",
        "evidence_fingerprint",
        "policy_fingerprint",
        "decision_fingerprint",
        "mtf_context_id",
        "mtf_context_hash",
        "mtf_algorithm_version",
        "mtf_quality_state",
    ]
    # Normalize key casing for entry_status
    for d in (p_dict, s_dict, b_dict, o_dict):
        if d is not None and "entry_status" in d and "entry_Status" not in d:
            d["entry_Status"] = d["entry_status"]

    mismatches: List[str] = []
    for f_name in compared_fields:
        pv = p_dict.get(f_name)
        sv = s_dict.get(f_name)
        bv = b_dict.get(f_name)
        if pv != sv or pv != bv:
            mismatches.append(f"{f_name}: prod={pv!r} vs shadow={sv!r} vs backtest={bv!r}")
        if o_dict is not None and f_name in o_dict:
            ov = o_dict.get(f_name)
            if pv != ov:
                mismatches.append(f"{f_name}: prod={pv!r} vs oracle={ov!r}")

    is_ok = len(mismatches) == 0
    return {
        "status": "PASS" if is_ok else "PARITY_NOT_ESTABLISHED",
        "is_parity": is_ok,
        "compared_fields_count": len(compared_fields),
        "mismatches": mismatches,
        "decision_fingerprint": p_dict.get("decision_fingerprint"),
        "mtf_context_hash": p_dict.get("mtf_context_hash"),
    }


def evaluate_common_decision(
    evaluation_input: Any,
    strategy_config: Optional[Any] = None,
    mtf_context: Optional[MtfEvaluationContext] = None,
    mode: str = "SHARED_STRATEGY_EVALUATOR",
) -> Any:
    """
    Section 6: Unified entry point that attaches `MtfEvaluationContext` (building it
    once if not yet attached and MTF bars are present) and delegates to
    `SharedStrategyEvaluator.evaluate(...)`.
    Never duplicates strategy logic; preserves 100% baseline behavior when
    `mtf_structure_eligibility_enabled=False`.
    """
    from backend.services.shared_strategy_evaluator import (
        MarketDecisionContext,
        SharedStrategyEvaluator,
        build_market_decision_context,
    )

    if isinstance(evaluation_input, MarketDecisionContext):
        ctx_obj = evaluation_input
    elif isinstance(evaluation_input, dict):
        ctx_obj = build_market_decision_context(**evaluation_input)
    else:
        raise TypeError(f"Unsupported evaluation_input type: {type(evaluation_input)}")

    if mtf_context is None and getattr(ctx_obj, "mtf_context", None) is not None:
        mtf_context = getattr(ctx_obj, "mtf_context")

    if mtf_context is not None:
        attach_context_to_evaluation(ctx_obj, mtf_context)

    s_cfg_dict: Dict[str, Any] = {"adapter_origin": mode}
    if strategy_config is not None:
        cfg_obj = _coerce_mtf_config(strategy_config)
        merged_pol = dict(ctx_obj.get_policy_dict() if hasattr(ctx_obj, "get_policy_dict") else ())
        merged_pol["mtf_structure_observe_only"] = cfg_obj.mtf_structure_observe_only
        merged_pol["mtf_structure_eligibility_enabled"] = cfg_obj.mtf_structure_eligibility_enabled
        merged_pol["mtf_ablation_mode"] = cfg_obj.mtf_ablation_mode
        object.__setattr__(ctx_obj, "policy_config_snapshot", tuple(sorted(merged_pol.items())))
        if isinstance(strategy_config, dict):
            s_cfg_dict.update(strategy_config)

    return SharedStrategyEvaluator.evaluate_decision(
        market_snapshot=ctx_obj,
        strategy_config=s_cfg_dict,
    )



def _simulate_forward_trade_outcome(
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    forward_m1_bars: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Deterministically evaluate a trade candidate against subsequent real closed M1 bars
    after the decision timestamp. Conservative tie-breaking: if both SL and TP are
    touched inside the same M1 candle, SL is assumed hit first (LOSS).
    """
    risk_dist = abs(entry_price - stop_loss)
    reward_dist = abs(take_profit - entry_price)
    if risk_dist <= 1e-9:
        return {"outcome": "INVALID_RISK", "realized_r": 0.0, "bars_held": 0}

    for idx, bar in enumerate(forward_m1_bars, start=1):
        bh = float(bar.get("high", 0.0))
        bl = float(bar.get("low", 0.0))
        if direction == "LONG":
            hit_sl = bl <= stop_loss
            hit_tp = bh >= take_profit
        else:
            hit_sl = bh >= stop_loss
            hit_tp = bl <= take_profit

        if hit_sl and hit_tp:
            return {"outcome": "LOSS_TIE_CONSERVATIVE", "realized_r": -1.0, "bars_held": idx}
        if hit_sl:
            return {"outcome": "LOSS", "realized_r": -1.0, "bars_held": idx}
        if hit_tp:
            r_mult = round(reward_dist / risk_dist, 4)
            return {"outcome": "WIN", "realized_r": r_mult, "bars_held": idx}

    # Mark-to-market at end of available forward window if neither SL nor TP was hit
    if forward_m1_bars:
        last_c = float(forward_m1_bars[-1].get("close", entry_price))
        pnl = (last_c - entry_price) if direction == "LONG" else (entry_price - last_c)
        r_open = round(pnl / risk_dist, 4)
        return {"outcome": "OPEN_AT_HORIZON", "realized_r": r_open, "bars_held": len(forward_m1_bars)}
    return {"outcome": "NO_FORWARD_BARS", "realized_r": 0.0, "bars_held": 0}


def run_v17_9_1_historical_ab_backtest(
    max_evaluation_points: int = 120,
) -> Dict[str, Any]:
    """
    Section 11: Execute real historical Bitget XAUUSDT A/B/C/D backtest & 3-mode + independent
    oracle parity verification over `backtest/historical_dataset_XAUUSDT.json.gz`.
    """
    import bisect
    from pathlib import Path
    from backend.services.market_shadow_service import MarketShadowService
    from backend.services.scalp_m1_types import ModelIdentifier
    from backend.services.shared_strategy_evaluator import (
        DecisionStatus,
        EVALUATOR_VERSION,
        SharedStrategyEvaluator,
        build_market_decision_context,
        characterize_production_baseline_decision,
        compare_decision_parity,
    )
    from backend.services.v17_8_backtest_runner import (
        SAVED_DATASET_GZ_PATH,
        collect_or_load_verified_historical_dataset,
        evaluate_production_parity_model_snapshot,
    )

    if not SAVED_DATASET_GZ_PATH.exists():
        return {
            "status": "BACKTEST_BLOCKED_INSUFFICIENT_REAL_DATA",
            "reason": f"Missing {SAVED_DATASET_GZ_PATH}",
        }

    dataset_sha256 = hashlib.sha256(SAVED_DATASET_GZ_PATH.read_bytes()).hexdigest()
    datasets = collect_or_load_verified_historical_dataset(symbol="XAUUSDT", allow_network_refresh=False)

    n_bars_by_tf: Dict[str, int] = {}
    time_ranges_by_tf: Dict[str, Dict[str, Optional[str]]] = {}
    missing_gaps_by_tf: Dict[str, int] = {}

    for gran, canon in (("1m", "M1"), ("5m", "M5"), ("15m", "M15"), ("1H", "H1"), ("4H", "H4"), ("1D", "D1")):
        rows = datasets.get(gran, [])
        n_bars_by_tf[canon] = len(rows)
        if rows:
            t0 = format_utc_iso(ensure_utc_datetime(rows[0]["open_time"]))
            t1 = format_utc_iso(ensure_utc_datetime(rows[-1]["close_time"]))
            time_ranges_by_tf[canon] = {"start_open_utc": t0, "end_close_utc": t1}
            norm_tf = normalize_market_bars("XAUUSDT", canon, rows)
            missing_gaps_by_tf[canon] = norm_tf.missing_gap_count
        else:
            time_ranges_by_tf[canon] = {"start_open_utc": None, "end_close_utc": None}
            missing_gaps_by_tf[canon] = 0

    bars_1m = datasets.get("1m", [])
    bars_5m = datasets.get("5m", [])
    bars_15m = datasets.get("15m", [])
    bars_1h = datasets.get("1H", [])
    bars_4h = datasets.get("4H", [])
    bars_1d = datasets.get("1D", [])

    if len(bars_1m) < 60 or len(bars_15m) < 20:
        return {
            "status": "BACKTEST_BLOCKED_INSUFFICIENT_REAL_DATA",
            "reason": "Insufficient real historical 1m/15m bars",
            "n_bars_by_tf": n_bars_by_tf,
        }

    close_ts_1m = [int(b["close_time"]) for b in bars_1m]
    close_ts_5m = [int(b["close_time"]) for b in bars_5m]
    close_ts_15m = [int(b["close_time"]) for b in bars_15m]
    close_ts_1h = [int(b["close_time"]) for b in bars_1h]
    close_ts_4h = [int(b["close_time"]) for b in bars_4h]
    close_ts_1d = [int(b["close_time"]) for b in bars_1d]

    # Select chronological evaluation checkpoints across the real 1m bar window
    start_idx = 30
    end_idx = len(bars_1m) - 1
    total_avail_steps = max(1, end_idx - start_idx)
    stride = max(1, total_avail_steps // max(1, int(max_evaluation_points)))
    eval_indices = list(range(start_idx, end_idx + 1, stride))

    models_to_eval = (
        ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
        ModelIdentifier.SMC_M1_CONTINUATION.value,
        ModelIdentifier.SMC_M1_REVERSAL.value,
        ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
        ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
    )

    variants = OrderedDict(
        [
            (
                "A_BASELINE_OFF",
                {
                    "mtf_structure_observe_only": False,
                    "mtf_structure_eligibility_enabled": False,
                    "mtf_ablation_mode": "FULL",
                },
            ),
            (
                "B_OBSERVE_ONLY_DEFAULT",
                {
                    "mtf_structure_observe_only": True,
                    "mtf_structure_eligibility_enabled": False,
                    "mtf_ablation_mode": "FULL",
                },
            ),
            (
                "C_MTF_ELIGIBILITY_ON_FULL",
                {
                    "mtf_structure_observe_only": False,
                    "mtf_structure_eligibility_enabled": True,
                    "mtf_ablation_mode": "FULL",
                },
            ),
            (
                "D1_ABLATION_H4_ONLY",
                {
                    "mtf_structure_observe_only": False,
                    "mtf_structure_eligibility_enabled": True,
                    "mtf_ablation_mode": "H4_ONLY",
                },
            ),
            (
                "D2_ABLATION_H1_ONLY",
                {
                    "mtf_structure_observe_only": False,
                    "mtf_structure_eligibility_enabled": True,
                    "mtf_ablation_mode": "H1_ONLY",
                },
            ),
            (
                "D3_ABLATION_M15_ONLY",
                {
                    "mtf_structure_observe_only": False,
                    "mtf_structure_eligibility_enabled": True,
                    "mtf_ablation_mode": "M15_ONLY",
                },
            ),
            (
                "D4_ABLATION_STRUCTURE_ONLY",
                {
                    "mtf_structure_observe_only": False,
                    "mtf_structure_eligibility_enabled": True,
                    "mtf_ablation_mode": "STRUCTURE_ONLY",
                },
            ),
            (
                "D5_ABLATION_LIQUIDITY_ONLY",
                {
                    "mtf_structure_observe_only": False,
                    "mtf_structure_eligibility_enabled": True,
                    "mtf_ablation_mode": "LIQUIDITY_ONLY",
                },
            ),
        ]
    )

    variant_stats: Dict[str, Dict[str, Any]] = {}
    for v_name in variants:
        variant_stats[v_name] = {
            "n_decisions_evaluated": 0,
            "n_valid_setups": 0,
            "n_eligible_trades": 0,
            "n_rejected": 0,
            "n_mtf_filtered": 0,
            "rejection_reasons": {},
            "mtf_filter_reasons": {},
            "outcomes": {"WIN": 0, "LOSS": 0, "LOSS_TIE_CONSERVATIVE": 0, "OPEN_AT_HORIZON": 0},
            "realized_r_list": [],
            "net_rr_list": [],
            "partition_counts": {"TRAIN": 0, "VALIDATION": 0, "OUT_OF_SAMPLE": 0},
            "decision_fingerprints": [],
        }

    shadow_svc = MarketShadowService(symbol="XAUUSDT")
    parity_checks_total = 0
    parity_checks_passed = 0
    parity_mismatches: List[Dict[str, Any]] = []
    a_vs_b_decision_invariance_checks = 0
    a_vs_b_decision_invariance_mismatches = 0
    oracle_vs_a_checks = 0
    oracle_vs_a_mismatches = 0
    consumed_samples: List[Dict[str, Any]] = []

    n_pts = len(eval_indices)
    for pos_idx, i_1m in enumerate(eval_indices):
        asof_ms = int(bars_1m[i_1m]["close_time"])
        if pos_idx < int(n_pts * 0.5):
            part_label = "TRAIN"
        elif pos_idx < int(n_pts * 0.75):
            part_label = "VALIDATION"
        else:
            part_label = "OUT_OF_SAMPLE"

        idx_5m = bisect.bisect_right(close_ts_5m, asof_ms)
        idx_15m = bisect.bisect_right(close_ts_15m, asof_ms)
        idx_1h = bisect.bisect_right(close_ts_1h, asof_ms)
        idx_4h = bisect.bisect_right(close_ts_4h, asof_ms)
        idx_1d = bisect.bisect_right(close_ts_1d, asof_ms)

        win_1m = bars_1m[max(0, i_1m + 1 - 120) : i_1m + 1]
        win_5m = bars_5m[max(0, idx_5m - 96) : idx_5m]
        win_15m = bars_15m[max(0, idx_15m - 96) : idx_15m]
        win_1h = bars_1h[max(0, idx_1h - 96) : idx_1h]
        win_4h = bars_4h[max(0, idx_4h - 64) : idx_4h]
        win_1d = bars_1d[max(0, idx_1d - 30) : idx_1d]
        forward_1m = bars_1m[i_1m + 1 : min(len(bars_1m), i_1m + 1 + 180)]

        last_c = float(win_1m[-1]["close"])
        bid_px = round(last_c - 0.10, 2)
        ask_px = round(last_c + 0.10, 2)

        # Build contexts for baseline (A/B) and each eligibility variant
        ctx_base = build_market_decision_context(
            symbol="XAUUSDT",
            decision_timestamp_ms=asof_ms,
            m1_candles=win_1m,
            m5_candles=win_5m,
            m15_candles=win_15m,
            h1_candles=win_1h,
            h4_candles=win_4h,
            d1_candles=win_1d,
            bid=bid_px,
            ask=ask_px,
        )
        pref_dir = SharedStrategyEvaluator.resolve_canonical_preferred_direction(ctx_base)

        for model_id in models_to_eval:
            # 1. Evaluate A (flag=OFF) and B (observe-only default) and Independent Oracle
            snap_a = SharedStrategyEvaluator.evaluate_model_direction(
                ctx_base, model_id, pref_dir, adapter_origin="PRODUCTION_ORCHESTRATOR"
            )
            snap_b_prod = SharedStrategyEvaluator.evaluate_model_direction(
                ctx_base, model_id, pref_dir, adapter_origin="PRODUCTION_ORCHESTRATOR"
            )
            snap_b_shadow = shadow_svc.evaluate_shared_decision_snapshot(
                context=ctx_base, model_id=model_id, direction=pref_dir
            )
            bt_res = evaluate_production_parity_model_snapshot(
                model_id=model_id, context=ctx_base, direction=pref_dir
            )
            snap_b_bt = bt_res["shared_decision_snapshot"]
            oracle_snap = characterize_production_baseline_decision(ctx_base, model_id, pref_dir)

            # Verify Oracle vs A
            oracle_vs_a_checks += 1
            p_ora = compare_decision_parity(oracle_snap, snap_a)
            if not p_ora["is_match"]:
                oracle_vs_a_mismatches += 1

            # Verify A vs B decision invariance
            a_vs_b_decision_invariance_checks += 1
            if snap_a.decision_fingerprint != snap_b_prod.decision_fingerprint:
                a_vs_b_decision_invariance_mismatches += 1

            # Verify 3-mode + independent oracle parity on B
            parity_checks_total += 1
            p_3way = compare_actual_consumed_decisions(
                snap_b_prod, snap_b_shadow, snap_b_bt, independent_oracle=oracle_snap
            )
            if p_3way["is_parity"]:
                parity_checks_passed += 1
            else:
                parity_mismatches.append(
                    {
                        "decision_timestamp_ms": asof_ms,
                        "model_id": model_id,
                        "mismatches": p_3way["mismatches"],
                    }
                )

            if len(consumed_samples) < 5 and snap_b_prod.setup_validity == "VALID":
                consumed_samples.append(serialize_decision_evidence(snap_b_prod, ctx_base.mtf_context))

            # Record stats for A and B
            for v_key, s_obj in (("A_BASELINE_OFF", snap_a), ("B_OBSERVE_ONLY_DEFAULT", snap_b_prod)):
                st = variant_stats[v_key]
                st["n_decisions_evaluated"] += 1
                st["partition_counts"][part_label] += 1
                st["decision_fingerprints"].append(s_obj.decision_fingerprint)
                if s_obj.setup_validity == "VALID":
                    st["n_valid_setups"] += 1
                if s_obj.decision_status == DecisionStatus.ELIGIBLE.value:
                    st["n_eligible_trades"] += 1
                    if s_obj.net_rr is not None:
                        st["net_rr_list"].append(float(s_obj.net_rr))
                    sim = _simulate_forward_trade_outcome(
                        s_obj.direction,
                        float(s_obj.entry_reference),
                        float(s_obj.selected_sl),
                        float(s_obj.take_profit),
                        forward_1m,
                    )
                    out_lbl = sim["outcome"]
                    if out_lbl in st["outcomes"]:
                        st["outcomes"][out_lbl] += 1
                    st["realized_r_list"].append(float(sim["realized_r"]))
                else:
                    st["n_rejected"] += 1
                    for r_code in s_obj.rejection_reason_codes:
                        st["rejection_reasons"][r_code] = st["rejection_reasons"].get(r_code, 0) + 1

            # 2. Evaluate C and D1..D5 (eligibility ON variants)
            for v_key in (
                "C_MTF_ELIGIBILITY_ON_FULL",
                "D1_ABLATION_H4_ONLY",
                "D2_ABLATION_H1_ONLY",
                "D3_ABLATION_M15_ONLY",
                "D4_ABLATION_STRUCTURE_ONLY",
                "D5_ABLATION_LIQUIDITY_ONLY",
            ):
                v_pol = dict(ctx_base.get_policy_dict())
                v_pol.update(variants[v_key])
                ctx_v = build_market_decision_context(
                    symbol="XAUUSDT",
                    decision_timestamp_ms=asof_ms,
                    m1_candles=win_1m,
                    m5_candles=win_5m,
                    m15_candles=win_15m,
                    h1_candles=win_1h,
                    h4_candles=win_4h,
                    d1_candles=win_1d,
                    bid=bid_px,
                    ask=ask_px,
                    policy_config=v_pol,
                )
                snap_v = SharedStrategyEvaluator.evaluate_model_direction(
                    ctx_v, model_id, pref_dir, adapter_origin="PRODUCTION_ORCHESTRATOR"
                )
                st = variant_stats[v_key]
                st["n_decisions_evaluated"] += 1
                st["partition_counts"][part_label] += 1
                st["decision_fingerprints"].append(snap_v.decision_fingerprint)
                if snap_v.setup_validity == "VALID":
                    st["n_valid_setups"] += 1
                if snap_v.decision_status == DecisionStatus.ELIGIBLE.value:
                    st["n_eligible_trades"] += 1
                    if snap_v.net_rr is not None:
                        st["net_rr_list"].append(float(snap_v.net_rr))
                    sim = _simulate_forward_trade_outcome(
                        snap_v.direction,
                        float(snap_v.entry_reference),
                        float(snap_v.selected_sl),
                        float(snap_v.take_profit),
                        forward_1m,
                    )
                    out_lbl = sim["outcome"]
                    if out_lbl in st["outcomes"]:
                        st["outcomes"][out_lbl] += 1
                    st["realized_r_list"].append(float(sim["realized_r"]))
                else:
                    st["n_rejected"] += 1
                    for r_code in snap_v.rejection_reason_codes:
                        st["rejection_reasons"][r_code] = st["rejection_reasons"].get(r_code, 0) + 1
                        if r_code.startswith("MTF_"):
                            st["n_mtf_filtered"] += 1
                            st["mtf_filter_reasons"][r_code] = st["mtf_filter_reasons"].get(r_code, 0) + 1

    # Summarize metrics per variant honestly
    summarized_variants: Dict[str, Any] = {}
    for v_key, st in variant_stats.items():
        r_list = st["realized_r_list"]
        wins = st["outcomes"]["WIN"]
        losses = st["outcomes"]["LOSS"] + st["outcomes"]["LOSS_TIE_CONSERVATIVE"]
        closed_trades = wins + losses
        win_rate = round(wins / closed_trades, 4) if closed_trades > 0 else None
        gross_win_r = sum(x for x in r_list if x > 0)
        gross_loss_r = abs(sum(x for x in r_list if x < 0))
        profit_factor = round(gross_win_r / gross_loss_r, 4) if gross_loss_r > 0 else (None if gross_win_r == 0 else 999.0)
        expectancy_r = round(sum(r_list) / len(r_list), 4) if r_list else None

        # Compute max drawdown in R
        cum_r = 0.0
        peak_r = 0.0
        max_dd_r = 0.0
        for r_val in r_list:
            cum_r += r_val
            if cum_r > peak_r:
                peak_r = cum_r
            dd = peak_r - cum_r
            if dd > max_dd_r:
                max_dd_r = dd

        summarized_variants[v_key] = {
            "policy": variants[v_key],
            "n_decisions_evaluated": st["n_decisions_evaluated"],
            "n_valid_setups": st["n_valid_setups"],
            "n_eligible_trades": st["n_eligible_trades"],
            "n_closed_resolved_trades": closed_trades,
            "n_rejected": st["n_rejected"],
            "n_mtf_filtered": st["n_mtf_filtered"],
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "expectancy_r": expectancy_r,
            "max_drawdown_r": round(max_dd_r, 4),
            "mean_planned_net_rr": round(sum(st["net_rr_list"]) / len(st["net_rr_list"]), 4) if st["net_rr_list"] else None,
            "outcomes": st["outcomes"],
            "rejection_reasons": st["rejection_reasons"],
            "mtf_filter_reasons": st["mtf_filter_reasons"],
            "partition_counts": st["partition_counts"],
            "decisions_Set_hash": deterministic_hash(st["decision_fingerprints"], 24),
        }

    is_parity_ok = (
        parity_checks_total > 0
        and parity_checks_passed == parity_checks_total
        and a_vs_b_decision_invariance_mismatches == 0
        and oracle_vs_a_mismatches == 0
    )

    return {
        "status": "PASS" if is_parity_ok else "PARITY_NOT_ESTABLISHED",
        "evaluator_version": EVALUATOR_VERSION,
        "mtf_algorithm_version": MTF_ALGORITHM_VERSION,
        "dataset": {
            "venue": "BITGET",
            "symbol": "XAUUSDT",
            "product_type": "USDT-FUTURES",
            "dataset_path": str(SAVED_DATASET_GZ_PATH),
            "dataset_sha256": dataset_sha256,
            "n_bars_by_tf": n_bars_by_tf,
            "time_ranges_by_tf": time_ranges_by_tf,
            "missing_gaps_by_tf": missing_gaps_by_tf,
            "evaluation_checkpoints": len(eval_indices),
            "models_evaluated": list(models_to_eval),
        },
        "parity": {
            "status": "PASS" if is_parity_ok else "PARITY_NOT_ESTABLISHED",
            "parity_checks_total": parity_checks_total,
            "parity_checks_passed": parity_checks_passed,
            "parity_mismatch_count": len(parity_mismatches),
            "a_vs_b_decision_invariance_checks": a_vs_b_decision_invariance_checks,
            "a_vs_b_decision_invariance_mismatches": a_vs_b_decision_invariance_mismatches,
            "oracle_vs_a_checks": oracle_vs_a_checks,
            "oracle_vs_a_mismatches": oracle_vs_a_mismatches,
            "mismatches": parity_mismatches[:10],
            "consumed_decision_samples": consumed_samples,
        },
        "variants": summarized_variants,
        "coverage_limitations": (
            "Bitget XAUUSDT 1m historical window covers ~24h (1,440 bars) while 15m/1H/4H/1D extend back further; "
            "sample size of eligible M1 setups in a 24h M1 window is bounded, so statistical confidence intervals "
            "on win_rate are wide and reported for engineering verification only without winrate marketing claims."
        ),
    }
