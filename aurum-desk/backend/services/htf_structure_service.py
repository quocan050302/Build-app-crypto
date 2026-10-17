"""
backend/services/htf_structure_service.py
V16.12 — Causal Higher-Timeframe (HTF) Market Structure Engine for CG-01.

Implements Sections D - J (Rules 031 - 100):
- Pure, causal market structure evaluator on closed candles.
- Protected High / Protected Low identification and invalidation tracking.
- Strict closed-candle boundary (candle_open + duration_ms <= as_of_ms).
- Explicit HTF State Machine (UNDETERMINED -> ESTABLISHED / PULLBACK / RANGE -> TRANSITION).
- Non-rebound transition protection: rebounds after invalidation do not restore trend without new causal swings.
- Range state evaluation with breakout (RANGE_BREAK_UP / RANGE_BREAK_DOWN), buffer tolerance, and range re-entry.
- Dynamic HTF settings resolution (explicit overrides > config > defaults) with strict validation.
- Multi-timeframe relationship resolver (H4 vs H1) and CG-01 direction context.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple


TIMEFRAME_MS: Dict[str, int] = {
    "1M": 60_000,
    "5M": 300_000,
    "15M": 900_000,
    "1H": 3_600_000,
    "4H": 14_400_000,
    "D": 86_400_000,
    "1D": 86_400_000,
}


def resolve_timeframe_ms(timeframe: str) -> int:
    """Rule 037: Resolve timeframe in ms. Rejects unknown timeframes."""
    tf_upper = (timeframe or "1H").upper()
    if tf_upper not in TIMEFRAME_MS:
        raise ValueError(f"UNSUPPORTED_TIMEFRAME: {timeframe}")
    return TIMEFRAME_MS[tf_upper]


@dataclass(frozen=True)
class CanonicalBar:
    """Canonical representation of a single price candle (Rule 031)."""
    symbol: str
    timeframe: str
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    is_closed: bool = True
    close_ms: int = 0

    @property
    def timestamp(self) -> int:
        return self.open_time

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "timestamp": self.open_time,
            "open_time": self.open_time,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "is_closed": self.is_closed,
            "close_ms": self.close_ms,
        }


def validate_and_canonicalize_bar(candle: Any, timeframe: str, symbol: str = "XAUUSDT") -> Optional[CanonicalBar]:
    """Rules 031, 032: Validates price numbers and internal OHLC consistency."""
    dur = resolve_timeframe_ms(timeframe)
    if isinstance(candle, dict):
        o_raw = candle.get("open", 0.0)
        h_raw = candle.get("high", 0.0)
        l_raw = candle.get("low", 0.0)
        c_raw = candle.get("close", 0.0)
        v_raw = candle.get("volume", 0.0)
        ts_raw = candle.get("open_time") if candle.get("open_time") is not None else candle.get("timestamp", 0)
        is_closed = candle.get("is_closed", True)
        c_sym = candle.get("symbol", symbol)
    else:
        o_raw = getattr(candle, "open", 0.0)
        h_raw = getattr(candle, "high", 0.0)
        l_raw = getattr(candle, "low", 0.0)
        c_raw = getattr(candle, "close", 0.0)
        v_raw = getattr(candle, "volume", 0.0)
        ts_raw = getattr(candle, "open_time", None)
        if ts_raw is None:
            ts_raw = getattr(candle, "timestamp", 0)
        is_closed = getattr(candle, "is_closed", True)
        c_sym = getattr(candle, "symbol", symbol)

    try:
        o = float(o_raw)
        h = float(h_raw)
        l = float(l_raw)
        c = float(c_raw)
        vol = float(v_raw) if v_raw is not None else 0.0
        ts = int(ts_raw)
    except (ValueError, TypeError):
        return None

    # Finite, positive checks
    if not (math.isfinite(o) and math.isfinite(h) and math.isfinite(l) and math.isfinite(c)):
        return None
    if o <= 0 or h <= 0 or l <= 0 or c <= 0 or ts <= 0:
        return None

    # OHLC geometric contradictions
    if h < l or h < o or h < c or l > o or l > c:
        return None

    return CanonicalBar(
        symbol=str(c_sym or symbol),
        timeframe=timeframe,
        open_time=ts,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=vol,
        is_closed=bool(is_closed),
        close_ms=ts + dur,
    )


def normalize_validate_sort_dedupe(
    candles: Sequence[Any],
    timeframe: str,
    symbol: str = "XAUUSDT",
) -> List[CanonicalBar]:
    """Rules 031 - 034: Canonical normalization, sort ascending, deduplication."""
    if not candles:
        return []
    seen: Dict[int, CanonicalBar] = {}
    for c in candles:
        bar = validate_and_canonicalize_bar(c, timeframe, symbol)
        if bar is None:
            continue
        seen[bar.open_time] = bar

    valid_bars = list(seen.values())
    valid_bars.sort(key=lambda b: b.open_time)
    return valid_bars


def get_candle_time_fields(candle: Any, timeframe: str) -> Tuple[int, int]:
    """Extract (open_ms, close_ms) from candle object or dict."""
    dur = resolve_timeframe_ms(timeframe)
    if isinstance(candle, dict):
        open_ms = int(candle.get("open_time") or candle.get("timestamp") or 0)
    else:
        open_ms = int(getattr(candle, "open_time", None) or getattr(candle, "timestamp", 0))
    close_ms = open_ms + dur
    return open_ms, close_ms


def filter_closed_candles(
    candles: Sequence[Any],
    timeframe: str,
    as_of_ms: int,
    symbol: str = "XAUUSDT",
) -> List[Any]:
    """
    Rules 022, 035: Candle is ONLY eligible if open_time + timeframe_duration <= as_of_ms.
    If candle has is_closed=False, it is never treated as confirmed closed.
    Does not mutate input list or objects.
    """
    dur = resolve_timeframe_ms(timeframe)
    normalized = normalize_validate_sort_dedupe(candles, timeframe, symbol)
    closed: List[Any] = []
    for bar in normalized:
        if not bar.is_closed:
            continue
        if bar.open_time + dur <= as_of_ms:
            closed.append(bar)
    return closed


@dataclass(frozen=True)
class HTFSettings:
    """Rules 038 - 042: Validated HTF Engine Settings."""
    min_bars: int = 12
    left_bars: int = 2
    right_bars: int = 2
    tick_size: float = 0.01
    break_buffer_ticks: int = 0
    algorithm_version: str = "v16.12"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "min_bars": self.min_bars,
            "left_bars": self.left_bars,
            "right_bars": self.right_bars,
            "tick_size": self.tick_size,
            "break_buffer_ticks": self.break_buffer_ticks,
            "algorithm_version": self.algorithm_version,
        }


def resolve_htf_settings(
    config: Optional[Any] = None,
    *,
    min_bars: Optional[int] = None,
    left_bars: Optional[int] = None,
    right_bars: Optional[int] = None,
    tick_size: Optional[float] = None,
    break_buffer_ticks: Optional[int] = None,
) -> HTFSettings:
    """
    Rules 041 - 043: Resolves settings with priority:
    explicit override (if not None) > config attribute > default.
    Strictly validates values (rejects booleans as integers, NaN, negative values).
    """
    def _pick_int(override: Optional[int], cfg_attr: str, default: int, min_val: int) -> int:
        val = override
        if val is None and config is not None:
            val = getattr(config, cfg_attr, None)
        if val is None:
            val = default
        if type(val) is bool or not isinstance(val, int):
            raise ValueError(f"INVALID_SETTING: {cfg_attr} must be an integer, got {type(val).__name__}")
        if val < min_val:
            raise ValueError(f"INVALID_SETTING: {cfg_attr} must be >= {min_val}, got {val}")
        return val

    def _pick_float(override: Optional[float], cfg_attr: str, default: float) -> float:
        val = override
        if val is None and config is not None:
            val = getattr(config, cfg_attr, None)
        if val is None:
            val = default
        if type(val) is bool or not isinstance(val, (int, float)):
            raise ValueError(f"INVALID_SETTING: {cfg_attr} must be a float, got {type(val).__name__}")
        val = float(val)
        if not math.isfinite(val) or val <= 0:
            raise ValueError(f"INVALID_SETTING: {cfg_attr} must be positive finite float, got {val}")
        return val

    eff_min_bars = _pick_int(min_bars, "htf_min_bars", 12, 1)
    eff_left_bars = _pick_int(left_bars, "pivot_left_bars", 2, 1)
    eff_right_bars = _pick_int(right_bars, "pivot_right_bars", 2, 1)
    eff_tick_size = _pick_float(tick_size, "tick_size", 0.01)
    eff_buffer = _pick_int(break_buffer_ticks, "htf_break_buffer_ticks", 0, 0)

    return HTFSettings(
        min_bars=eff_min_bars,
        left_bars=eff_left_bars,
        right_bars=eff_right_bars,
        tick_size=eff_tick_size,
        break_buffer_ticks=eff_buffer,
        algorithm_version="v16.12",
    )


@dataclass
class StructuralReference:
    swing_id: str
    price: float
    occurred_at_ms: int
    known_at_ms: int
    kind: str                    # "HIGH" or "LOW"
    label: str                   # "HH", "LH", "HL", "LL", "EQH", "EQL"
    associated_event_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "swing_id": self.swing_id,
            "price": self.price,
            "occurred_at_ms": self.occurred_at_ms,
            "known_at_ms": self.known_at_ms,
            "kind": self.kind,
            "label": self.label,
            "associated_event_id": self.associated_event_id,
        }


@dataclass
class StructureEvent:
    event_id: str
    event_type: str              # "BOS", "CHOCH", "PROTECTED_LOW_BREAK", "PROTECTED_HIGH_BREAK", "SWEEP_REJECTION", "RANGE_BREAK_UP", "RANGE_BREAK_DOWN", "RANGE_REENTRY", "STRUCTURE_RECONFIRMED"
    direction: str               # "BULLISH", "BEARISH", "NEUTRAL"
    reference_swing_id: str
    reference_price: float
    breaking_bar_open_ms: int
    breaking_bar_close_ms: int
    close_price: float
    known_at_ms: int
    previous_bias: str
    new_bias: str
    previous_phase: str
    new_phase: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "direction": self.direction,
            "reference_swing_id": self.reference_swing_id,
            "reference_price": self.reference_price,
            "breaking_bar_open_ms": self.breaking_bar_open_ms,
            "breaking_bar_close_ms": self.breaking_bar_close_ms,
            "close_price": self.close_price,
            "known_at_ms": self.known_at_ms,
            "previous_bias": self.previous_bias,
            "new_bias": self.new_bias,
            "previous_phase": self.previous_phase,
            "new_phase": self.new_phase,
        }


@dataclass
class HTFStructureSnapshot:
    symbol: str
    timeframe: str
    as_of_ms: int

    bias: str                                 # "BULLISH", "BEARISH", "RANGE", "UNKNOWN"
    last_confirmed_bias: str                  # "BULLISH", "BEARISH", "UNKNOWN"
    phase: str                                # "INSUFFICIENT_DATA", "UNDETERMINED", "ESTABLISHED", "PULLBACK", "TRANSITION", "RANGE"
    bars: int = 0

    last_bar_open_ms: Optional[int] = None
    last_bar_close_ms: Optional[int] = None

    reference_high: Optional[Dict[str, Any]] = None
    reference_low: Optional[Dict[str, Any]] = None
    protected_high: Optional[Dict[str, Any]] = None
    protected_low: Optional[Dict[str, Any]] = None

    latest_structure_event: Optional[Dict[str, Any]] = None
    provenance: str = "NO_DATA"
    algorithm_version: str = "v16.12"
    data_status: str = "READY"
    indicator_bias: Optional[str] = None
    effective_settings: Optional[Dict[str, Any]] = None
    config_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "as_of_ms": self.as_of_ms,
            "bias": self.bias,
            "last_confirmed_bias": self.last_confirmed_bias,
            "phase": self.phase,
            "bars": self.bars,
            "last_closed_ms": self.last_bar_close_ms,
            "last_bar_open_ms": self.last_bar_open_ms,
            "last_bar_close_ms": self.last_bar_close_ms,
            "reference_high": self.reference_high,
            "reference_low": self.reference_low,
            "protected_high": self.protected_high,
            "protected_low": self.protected_low,
            "latest_structure_event": self.latest_structure_event,
            "provenance": self.provenance,
            "algorithm_version": self.algorithm_version,
            "data_status": self.data_status,
            "indicator_bias": self.indicator_bias,
            "effective_settings": self.effective_settings,
            "config_version": self.config_version,
        }


@dataclass
class HTFState:
    """Rule 052: Internal pure state tracking dataclass."""
    bias: str = "UNKNOWN"
    last_confirmed_bias: str = "UNKNOWN"
    phase: str = "UNDETERMINED"
    protected_high: Optional[Dict[str, Any]] = None
    protected_low: Optional[Dict[str, Any]] = None
    reference_high: Optional[Dict[str, Any]] = None
    reference_low: Optional[Dict[str, Any]] = None
    range_high: Optional[float] = None
    range_low: Optional[float] = None
    invalidation_event: Optional[StructureEvent] = None
    invalidation_known_at_ms: Optional[int] = None
    invalidation_ref_id: Optional[str] = None
    consumed_breaks: Set[Tuple[str, str]] = field(default_factory=set)
    latest_event: Optional[StructureEvent] = None
    provenance: str = "NO_DATA"


@dataclass
class HTFStepResult:
    state: HTFState
    new_event: Optional[StructureEvent] = None


def identify_causal_swings(
    closed_candles: Sequence[Any],
    timeframe: str = "1H",
    left_bars: int = 2,
    right_bars: int = 2,
    *,
    symbol: str = "XAUUSDT",
    as_of_ms: int = 0,
    tick_size: float = 0.01,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Rules 031 - 038: Causal swing high and swing low detector.
    A swing at candle i is confirmed strictly when candle i + right_bars closes.
    known_at_ms = confirm_bar_open + duration_ms.
    Only returns swings with known_at_ms <= as_of_ms.
    """
    if len(closed_candles) < left_bars + right_bars + 1:
        return [], []

    dur = resolve_timeframe_ms(timeframe)
    highs: List[Dict[str, Any]] = []
    lows: List[Dict[str, Any]] = []
    n = len(closed_candles)
    tf_lower = timeframe.lower()

    for i in range(left_bars, n - right_bars):
        mid = closed_candles[i]
        mid_h = float(getattr(mid, "high", None) if not isinstance(mid, dict) else mid.get("high", 0.0))
        mid_l = float(getattr(mid, "low", None) if not isinstance(mid, dict) else mid.get("low", 0.0))
        open_ms = int(getattr(mid, "open_time", None) or getattr(mid, "timestamp", 0) if not isinstance(mid, dict) else (mid.get("open_time") or mid.get("timestamp") or 0))

        confirm_bar = closed_candles[i + right_bars]
        confirm_open = int(getattr(confirm_bar, "open_time", None) or getattr(confirm_bar, "timestamp", 0) if not isinstance(confirm_bar, dict) else (confirm_bar.get("open_time") or confirm_bar.get("timestamp") or 0))
        known_at_ms = confirm_open + dur

        if as_of_ms and known_at_ms > as_of_ms:
            continue

        # Check Swing High
        is_sh = True
        for j in range(i - left_bars, i + right_bars + 1):
            if j == i:
                continue
            bar_h = float(getattr(closed_candles[j], "high", None) if not isinstance(closed_candles[j], dict) else closed_candles[j].get("high", 0.0))
            if j < i and bar_h >= mid_h:
                is_sh = False
                break
            elif j > i and bar_h > mid_h:
                is_sh = False
                break

        if is_sh:
            sh_id = f"{tf_lower}_sh_{open_ms}"
            highs.append({
                "id": sh_id,
                "swing_id": sh_id,
                "symbol": symbol,
                "timeframe": timeframe,
                "price": round(mid_h, 2),
                "occurred_at_ms": open_ms,
                "known_at_ms": known_at_ms,
                "index": i,
                "kind": "HIGH",
                "label": "HIGH",
            })

        # Check Swing Low
        is_sl = True
        for j in range(i - left_bars, i + right_bars + 1):
            if j == i:
                continue
            bar_l = float(getattr(closed_candles[j], "low", None) if not isinstance(closed_candles[j], dict) else closed_candles[j].get("low", 0.0))
            if j < i and bar_l <= mid_l:
                is_sl = False
                break
            elif j > i and bar_l < mid_l:
                is_sl = False
                break

        if is_sl:
            sl_id = f"{tf_lower}_sl_{open_ms}"
            lows.append({
                "id": sl_id,
                "swing_id": sl_id,
                "symbol": symbol,
                "timeframe": timeframe,
                "price": round(mid_l, 2),
                "occurred_at_ms": open_ms,
                "known_at_ms": known_at_ms,
                "index": i,
                "kind": "LOW",
                "label": "LOW",
            })

    # Sequential labeling with tick tolerance (HH, LH, EQH / HL, LL, EQL)
    for i in range(len(highs)):
        if i == 0:
            highs[i]["label"] = "HIGH"
        else:
            prev_p = highs[i - 1]["price"]
            curr_p = highs[i]["price"]
            diff = curr_p - prev_p
            if abs(diff) <= tick_size + 1e-6:
                highs[i]["label"] = "EQH"
            elif diff > 0:
                highs[i]["label"] = "HH"
            else:
                highs[i]["label"] = "LH"

    for i in range(len(lows)):
        if i == 0:
            lows[i]["label"] = "LOW"
        else:
            prev_p = lows[i - 1]["price"]
            curr_p = lows[i]["price"]
            diff = curr_p - prev_p
            if abs(diff) <= tick_size + 1e-6:
                lows[i]["label"] = "EQL"
            elif diff > 0:
                lows[i]["label"] = "HL"
            else:
                lows[i]["label"] = "LL"

    return highs, lows


def bootstrap_structure_state(
    known_h: List[Dict[str, Any]],
    known_l: List[Dict[str, Any]],
    bar: Any,
    settings: HTFSettings,
) -> Optional[HTFState]:
    """
    Rules 051, 061 - 065:
    Initial bootstrap strictly for UNDETERMINED initial state.
    Never used for recovery after invalidation.
    """
    if len(known_h) < 2 or len(known_l) < 2:
        return None

    h1, h2 = known_h[-2], known_h[-1]
    l1, l2 = known_l[-2], known_l[-1]
    bar_c = float(getattr(bar, "close", 0.0) if not isinstance(bar, dict) else bar.get("close", 0.0))
    tick_size = settings.tick_size

    is_bull_seq = (h2["price"] > h1["price"] + tick_size) and (l2["price"] > l1["price"] + tick_size)
    is_bear_seq = (h2["price"] < h1["price"] - tick_size) and (l2["price"] < l1["price"] - tick_size)
    is_range_seq = (abs(h2["price"] - h1["price"]) <= tick_size) and (abs(l2["price"] - l1["price"]) <= tick_size)

    if is_bull_seq and bar_c >= l2["price"] - tick_size:
        return HTFState(
            bias="BULLISH",
            last_confirmed_bias="BULLISH",
            phase="ESTABLISHED",
            protected_low=l2,
            protected_high=None,
            reference_high=h2,
            reference_low=l2,
            provenance="CONFIRMED_SWING_SEQUENCE",
        )
    elif is_bear_seq and bar_c <= h2["price"] + tick_size:
        return HTFState(
            bias="BEARISH",
            last_confirmed_bias="BEARISH",
            phase="ESTABLISHED",
            protected_high=h2,
            protected_low=None,
            reference_high=h2,
            reference_low=l2,
            provenance="CONFIRMED_SWING_SEQUENCE",
        )
    elif is_range_seq:
        return HTFState(
            bias="RANGE",
            last_confirmed_bias="UNKNOWN",
            phase="RANGE",
            protected_high=None,
            protected_low=None,
            range_high=max(h1["price"], h2["price"]),
            range_low=min(l1["price"], l2["price"]),
            reference_high=h2,
            reference_low=l2,
            provenance="CONFIRMED_RANGE_STRUCTURE",
        )
    return None


def evaluate_range_state(
    state: HTFState,
    bar: Any,
    known_h: List[Dict[str, Any]],
    known_l: List[Dict[str, Any]],
    settings: HTFSettings,
    timeframe: str,
    symbol: str,
) -> HTFStepResult:
    """
    Rules 081 - 090: Pure range state evaluator with breakout, buffer tolerance, and sweep detection.
    """
    bar_open_ms, bar_close_ms = get_candle_time_fields(bar, timeframe)
    bar_h = float(getattr(bar, "high", 0.0) if not isinstance(bar, dict) else bar.get("high", 0.0))
    bar_l = float(getattr(bar, "low", 0.0) if not isinstance(bar, dict) else bar.get("low", 0.0))
    bar_c = float(getattr(bar, "close", 0.0) if not isinstance(bar, dict) else bar.get("close", 0.0))

    r_high = state.range_high if state.range_high is not None else (state.reference_high["price"] if state.reference_high else 0.0)
    r_low = state.range_low if state.range_low is not None else (state.reference_low["price"] if state.reference_low else 0.0)
    buffer_amt = settings.break_buffer_ticks * settings.tick_size

    new_state = HTFState(
        bias=state.bias,
        last_confirmed_bias=state.last_confirmed_bias,
        phase=state.phase,
        protected_high=state.protected_high,
        protected_low=state.protected_low,
        reference_high=known_h[-1] if known_h else state.reference_high,
        reference_low=known_l[-1] if known_l else state.reference_low,
        range_high=r_high,
        range_low=r_low,
        invalidation_event=state.invalidation_event,
        invalidation_known_at_ms=state.invalidation_known_at_ms,
        invalidation_ref_id=state.invalidation_ref_id,
        consumed_breaks=set(state.consumed_breaks),
        latest_event=state.latest_event,
        provenance=state.provenance,
    )

    # 1. Breakout UP (Rule 083)
    if bar_c > r_high + buffer_amt:
        break_key = (f"range_up_{r_high}", "RANGE_BREAK_UP")
        if break_key not in new_state.consumed_breaks:
            new_state.consumed_breaks.add(break_key)
            evt = StructureEvent(
                event_id=f"evt_range_up_{symbol.lower()}_{timeframe.lower()}_{bar_close_ms}",
                event_type="RANGE_BREAK_UP",
                direction="BULLISH",
                reference_swing_id=state.reference_high["id"] if state.reference_high else f"range_h_{r_high}",
                reference_price=r_high,
                breaking_bar_open_ms=bar_open_ms,
                breaking_bar_close_ms=bar_close_ms,
                close_price=bar_c,
                known_at_ms=bar_close_ms,
                previous_bias="RANGE",
                new_bias="UNKNOWN",
                previous_phase="RANGE",
                new_phase="TRANSITION",
            )
            new_state.bias = "UNKNOWN"
            new_state.phase = "TRANSITION"
            new_state.provenance = "RANGE_BREAKOUT_TRANSITION"
            new_state.invalidation_event = evt
            new_state.invalidation_known_at_ms = bar_close_ms
            new_state.latest_event = evt
            return HTFStepResult(state=new_state, new_event=evt)

    # 2. Breakout DOWN (Rule 084)
    elif bar_c < r_low - buffer_amt:
        break_key = (f"range_down_{r_low}", "RANGE_BREAK_DOWN")
        if break_key not in new_state.consumed_breaks:
            new_state.consumed_breaks.add(break_key)
            evt = StructureEvent(
                event_id=f"evt_range_down_{symbol.lower()}_{timeframe.lower()}_{bar_close_ms}",
                event_type="RANGE_BREAK_DOWN",
                direction="BEARISH",
                reference_swing_id=state.reference_low["id"] if state.reference_low else f"range_l_{r_low}",
                reference_price=r_low,
                breaking_bar_open_ms=bar_open_ms,
                breaking_bar_close_ms=bar_close_ms,
                close_price=bar_c,
                known_at_ms=bar_close_ms,
                previous_bias="RANGE",
                new_bias="UNKNOWN",
                previous_phase="RANGE",
                new_phase="TRANSITION",
            )
            new_state.bias = "UNKNOWN"
            new_state.phase = "TRANSITION"
            new_state.provenance = "RANGE_BREAKOUT_TRANSITION"
            new_state.invalidation_event = evt
            new_state.invalidation_known_at_ms = bar_close_ms
            new_state.latest_event = evt
            return HTFStepResult(state=new_state, new_event=evt)

    # 3. Wick outside range: remains RANGE (Rule 086)
    elif bar_h > r_high or bar_l < r_low:
        new_state.provenance = "RANGE_SWEEP_REJECTION"

    return HTFStepResult(state=new_state, new_event=None)


def confirm_transition_structure(
    state: HTFState,
    bar: Any,
    known_h: List[Dict[str, Any]],
    known_l: List[Dict[str, Any]],
    settings: HTFSettings,
    timeframe: str,
    symbol: str,
) -> Optional[Tuple[HTFState, StructureEvent]]:
    """
    Rules 074 - 079, 088 - 090:
    Evaluates whether a TRANSITION state can be resolved into:
    1. RANGE_REENTRY (failed breakout)
    2. STRUCTURE_RECONFIRMED / BOS (new causal bullish structure post-invalidation)
    3. CHOCH / ESTABLISHED (new causal bearish structure post-invalidation)
    Returns (new_state, event) if resolved, else None (maintains TRANSITION).
    """
    bar_open_ms, bar_close_ms = get_candle_time_fields(bar, timeframe)
    bar_c = float(getattr(bar, "close", 0.0) if not isinstance(bar, dict) else bar.get("close", 0.0))
    buffer_amt = settings.break_buffer_ticks * settings.tick_size
    tick_size = settings.tick_size

    # Check 1: Range Re-entry (Rule 088)
    if state.range_high is not None and state.range_low is not None:
        r_h = state.range_high
        r_l = state.range_low
        if r_l - buffer_amt <= bar_c <= r_h + buffer_amt:
            reentry_evt = StructureEvent(
                event_id=f"evt_range_reentry_{symbol.lower()}_{timeframe.lower()}_{bar_close_ms}",
                event_type="RANGE_REENTRY",
                direction="NEUTRAL",
                reference_swing_id=state.invalidation_ref_id or "range_bounds",
                reference_price=r_h if bar_c >= (r_h + r_l)/2 else r_l,
                breaking_bar_open_ms=bar_open_ms,
                breaking_bar_close_ms=bar_close_ms,
                close_price=bar_c,
                known_at_ms=bar_close_ms,
                previous_bias="UNKNOWN",
                new_bias="RANGE",
                previous_phase="TRANSITION",
                new_phase="RANGE",
            )
            cleared_breaks = {k for k in state.consumed_breaks if not k[1].startswith("RANGE_BREAK")}
            reentry_state = HTFState(
                bias="RANGE",
                last_confirmed_bias=state.last_confirmed_bias,
                phase="RANGE",
                protected_high=None,
                protected_low=None,
                reference_high=known_h[-1] if known_h else state.reference_high,
                reference_low=known_l[-1] if known_l else state.reference_low,
                range_high=r_h,
                range_low=r_l,
                invalidation_event=None,
                invalidation_known_at_ms=None,
                invalidation_ref_id=None,
                consumed_breaks=cleared_breaks,
                latest_event=reentry_evt,
                provenance="CONFIRMED_RANGE_STRUCTURE",
            )
            return reentry_state, reentry_evt

    # Check 2: Causal structural confirmation post-invalidation (Rules 074 - 079)
    inval_cutoff = state.invalidation_known_at_ms or 0

    # Only swings confirmed AFTER invalidation cutoff can confirm a new structure!
    post_h = [h for h in known_h if h["known_at_ms"] > inval_cutoff]
    post_l = [l for l in known_l if l["known_at_ms"] > inval_cutoff]

    if len(post_h) >= 2 and len(post_l) >= 2:
        h1, h2 = post_h[-2], post_h[-1]
        l1, l2 = post_l[-2], post_l[-1]

        is_bull_seq = (h2["price"] > h1["price"] + tick_size) and (l2["price"] > l1["price"] + tick_size)
        is_bear_seq = (h2["price"] < h1["price"] - tick_size) and (l2["price"] < l1["price"] - tick_size)

        if is_bull_seq and bar_c >= l2["price"] - tick_size:
            evt_type = "STRUCTURE_RECONFIRMED" if state.last_confirmed_bias == "BULLISH" else "CHOCH"
            evt = StructureEvent(
                event_id=f"evt_confirm_bull_{symbol.lower()}_{timeframe.lower()}_{bar_close_ms}",
                event_type=evt_type,
                direction="BULLISH",
                reference_swing_id=h2["id"],
                reference_price=h2["price"],
                breaking_bar_open_ms=bar_open_ms,
                breaking_bar_close_ms=bar_close_ms,
                close_price=bar_c,
                known_at_ms=bar_close_ms,
                previous_bias="UNKNOWN",
                new_bias="BULLISH",
                previous_phase="TRANSITION",
                new_phase="ESTABLISHED",
            )
            new_state = HTFState(
                bias="BULLISH",
                last_confirmed_bias="BULLISH",
                phase="ESTABLISHED",
                protected_low=l2,
                protected_high=None,
                reference_high=h2,
                reference_low=l2,
                range_high=None,
                range_low=None,
                invalidation_event=None,
                invalidation_known_at_ms=None,
                invalidation_ref_id=None,
                consumed_breaks=set(state.consumed_breaks),
                latest_event=evt,
                provenance="CONFIRMED_SWING_SEQUENCE",
            )
            return new_state, evt

        elif is_bear_seq and bar_c <= h2["price"] + tick_size:
            evt_type = "STRUCTURE_RECONFIRMED" if state.last_confirmed_bias == "BEARISH" else "CHOCH"
            evt = StructureEvent(
                event_id=f"evt_confirm_bear_{symbol.lower()}_{timeframe.lower()}_{bar_close_ms}",
                event_type=evt_type,
                direction="BEARISH",
                reference_swing_id=l2["id"],
                reference_price=l2["price"],
                breaking_bar_open_ms=bar_open_ms,
                breaking_bar_close_ms=bar_close_ms,
                close_price=bar_c,
                known_at_ms=bar_close_ms,
                previous_bias="UNKNOWN",
                new_bias="BEARISH",
                previous_phase="TRANSITION",
                new_phase="ESTABLISHED",
            )
            new_state = HTFState(
                bias="BEARISH",
                last_confirmed_bias="BEARISH",
                phase="ESTABLISHED",
                protected_high=h2,
                protected_low=None,
                reference_high=h2,
                reference_low=l2,
                range_high=None,
                range_low=None,
                invalidation_event=None,
                invalidation_known_at_ms=None,
                invalidation_ref_id=None,
                consumed_breaks=set(state.consumed_breaks),
                latest_event=evt,
                provenance="CONFIRMED_SWING_SEQUENCE",
            )
            return new_state, evt

    return None


def step_htf_state(
    previous_state: HTFState,
    bar: Any,
    known_h: List[Dict[str, Any]],
    known_l: List[Dict[str, Any]],
    settings: HTFSettings,
    timeframe: str,
    symbol: str,
) -> HTFStepResult:
    """
    Rules 053 - 059: Pure, causal, single-step state transition on a closed candle.
    No wall clock, no DB, no singleton mutation.
    """
    bar_open_ms, bar_close_ms = get_candle_time_fields(bar, timeframe)
    bar_h = float(getattr(bar, "high", 0.0) if not isinstance(bar, dict) else bar.get("high", 0.0))
    bar_l = float(getattr(bar, "low", 0.0) if not isinstance(bar, dict) else bar.get("low", 0.0))
    bar_c = float(getattr(bar, "close", 0.0) if not isinstance(bar, dict) else bar.get("close", 0.0))
    tick_size = settings.tick_size
    buffer_amt = settings.break_buffer_ticks * tick_size

    if previous_state.phase == "UNDETERMINED":
        boot = bootstrap_structure_state(known_h, known_l, bar, settings)
        if boot is not None:
            return HTFStepResult(state=boot, new_event=None)
        st = HTFState(
            bias="UNKNOWN",
            last_confirmed_bias="UNKNOWN",
            phase="UNDETERMINED",
            reference_high=known_h[-1] if known_h else None,
            reference_low=known_l[-1] if known_l else None,
            provenance="INSUFFICIENT_CONFIRMED_SWINGS",
        )
        return HTFStepResult(state=st, new_event=None)

    elif previous_state.phase == "RANGE":
        return evaluate_range_state(previous_state, bar, known_h, known_l, settings, timeframe, symbol)

    elif previous_state.phase == "TRANSITION":
        confirmed = confirm_transition_structure(previous_state, bar, known_h, known_l, settings, timeframe, symbol)
        if confirmed is not None:
            new_st, new_evt = confirmed
            return HTFStepResult(state=new_st, new_event=new_evt)
        st = HTFState(
            bias="UNKNOWN",
            last_confirmed_bias=previous_state.last_confirmed_bias,
            phase="TRANSITION",
            protected_high=None,
            protected_low=None,
            reference_high=known_h[-1] if known_h else previous_state.reference_high,
            reference_low=known_l[-1] if known_l else previous_state.reference_low,
            range_high=previous_state.range_high,
            range_low=previous_state.range_low,
            invalidation_event=previous_state.invalidation_event,
            invalidation_known_at_ms=previous_state.invalidation_known_at_ms,
            invalidation_ref_id=previous_state.invalidation_ref_id,
            consumed_breaks=set(previous_state.consumed_breaks),
            latest_event=previous_state.latest_event,
            provenance=previous_state.provenance,
        )
        return HTFStepResult(state=st, new_event=None)

    elif previous_state.phase in ("ESTABLISHED", "PULLBACK"):
        st = HTFState(
            bias=previous_state.bias,
            last_confirmed_bias=previous_state.last_confirmed_bias,
            phase=previous_state.phase,
            protected_high=previous_state.protected_high,
            protected_low=previous_state.protected_low,
            reference_high=known_h[-1] if known_h else previous_state.reference_high,
            reference_low=known_l[-1] if known_l else previous_state.reference_low,
            range_high=None,
            range_low=None,
            invalidation_event=previous_state.invalidation_event,
            invalidation_known_at_ms=previous_state.invalidation_known_at_ms,
            invalidation_ref_id=previous_state.invalidation_ref_id,
            consumed_breaks=set(previous_state.consumed_breaks),
            latest_event=previous_state.latest_event,
            provenance=previous_state.provenance,
        )

        if st.bias == "BULLISH":
            prot_p = st.protected_low["price"] if st.protected_low else (known_l[-1]["price"] if known_l else 0.0)

            # 1. Protected low break by candle close (Rule 071, 040)
            if bar_c < prot_p - buffer_amt - (1e-6 if buffer_amt == 0 else 0):
                evt = StructureEvent(
                    event_id=f"evt_prot_low_break_{symbol.lower()}_{timeframe.lower()}_{bar_close_ms}",
                    event_type="PROTECTED_LOW_BREAK",
                    direction="BEARISH",
                    reference_swing_id=st.protected_low["id"] if st.protected_low else "prot_low",
                    reference_price=prot_p,
                    breaking_bar_open_ms=bar_open_ms,
                    breaking_bar_close_ms=bar_close_ms,
                    close_price=bar_c,
                    known_at_ms=bar_close_ms,
                    previous_bias="BULLISH",
                    new_bias="UNKNOWN",
                    previous_phase="ESTABLISHED",
                    new_phase="TRANSITION",
                )
                st.bias = "UNKNOWN"
                st.phase = "TRANSITION"
                st.last_confirmed_bias = "BULLISH"
                st.invalidation_event = evt
                st.invalidation_known_at_ms = bar_close_ms
                st.invalidation_ref_id = evt.reference_swing_id
                st.protected_low = None
                st.latest_event = evt
                st.provenance = "TRANSITION_AFTER_PROTECTED_BREAK"
                return HTFStepResult(state=st, new_event=evt)

            # 2. Sweep / Wick Rejection (Rule 073)
            elif bar_l < prot_p and bar_c >= prot_p - tick_size:
                evt = StructureEvent(
                    event_id=f"evt_sweep_{symbol.lower()}_{timeframe.lower()}_{bar_close_ms}",
                    event_type="SWEEP_REJECTION",
                    direction="BULLISH",
                    reference_swing_id=st.protected_low["id"] if st.protected_low else "prot_low",
                    reference_price=prot_p,
                    breaking_bar_open_ms=bar_open_ms,
                    breaking_bar_close_ms=bar_close_ms,
                    close_price=bar_c,
                    known_at_ms=bar_close_ms,
                    previous_bias="BULLISH",
                    new_bias="BULLISH",
                    previous_phase=st.phase,
                    new_phase="ESTABLISHED",
                )
                st.phase = "ESTABLISHED"
                st.latest_event = evt
                return HTFStepResult(state=st, new_event=evt)

            # 3. Bullish BOS (Rule 066)
            elif st.reference_high and bar_c > st.reference_high["price"] + tick_size:
                break_key = (st.reference_high["id"], "BULLISH_BOS")
                if break_key not in st.consumed_breaks:
                    st.consumed_breaks.add(break_key)
                    if st.protected_low:
                        cand_lows = [
                            l for l in known_l
                            if l["occurred_at_ms"] > st.protected_low["occurred_at_ms"] and l["price"] > st.protected_low["price"]
                        ]
                        if cand_lows:
                            st.protected_low = cand_lows[-1]
                    evt = StructureEvent(
                        event_id=f"evt_bos_{symbol.lower()}_{timeframe.lower()}_{bar_close_ms}",
                        event_type="BOS",
                        direction="BULLISH",
                        reference_swing_id=st.reference_high["id"],
                        reference_price=st.reference_high["price"],
                        breaking_bar_open_ms=bar_open_ms,
                        breaking_bar_close_ms=bar_close_ms,
                        close_price=bar_c,
                        known_at_ms=bar_close_ms,
                        previous_bias="BULLISH",
                        new_bias="BULLISH",
                        previous_phase=st.phase,
                        new_phase="ESTABLISHED",
                    )
                    st.phase = "ESTABLISHED"
                    st.latest_event = evt
                    return HTFStepResult(state=st, new_event=evt)

            # 4. Internal Pullback
            else:
                if st.protected_low and known_l:
                    higher_lows = [l for l in known_l if l["occurred_at_ms"] > st.protected_low["occurred_at_ms"] and l["price"] > st.protected_low["price"]]
                    if higher_lows and bar_c < higher_lows[-1]["price"] - tick_size and bar_c >= prot_p - tick_size:
                        st.phase = "PULLBACK"
            return HTFStepResult(state=st, new_event=None)

        elif st.bias == "BEARISH":
            prot_p = st.protected_high["price"] if st.protected_high else (known_h[-1]["price"] if known_h else 0.0)

            # 1. Protected high break by candle close (Rule 072, 040)
            if bar_c > prot_p + buffer_amt + (1e-6 if buffer_amt == 0 else 0):
                evt = StructureEvent(
                    event_id=f"evt_prot_high_break_{symbol.lower()}_{timeframe.lower()}_{bar_close_ms}",
                    event_type="PROTECTED_HIGH_BREAK",
                    direction="BULLISH",
                    reference_swing_id=st.protected_high["id"] if st.protected_high else "prot_high",
                    reference_price=prot_p,
                    breaking_bar_open_ms=bar_open_ms,
                    breaking_bar_close_ms=bar_close_ms,
                    close_price=bar_c,
                    known_at_ms=bar_close_ms,
                    previous_bias="BEARISH",
                    new_bias="UNKNOWN",
                    previous_phase="ESTABLISHED",
                    new_phase="TRANSITION",
                )
                st.bias = "UNKNOWN"
                st.phase = "TRANSITION"
                st.last_confirmed_bias = "BEARISH"
                st.invalidation_event = evt
                st.invalidation_known_at_ms = bar_close_ms
                st.invalidation_ref_id = evt.reference_swing_id
                st.protected_high = None
                st.latest_event = evt
                st.provenance = "TRANSITION_AFTER_PROTECTED_BREAK"
                return HTFStepResult(state=st, new_event=evt)

            # 2. Sweep / Wick Rejection (Rule 073)
            elif bar_h > prot_p and bar_c <= prot_p + tick_size:
                evt = StructureEvent(
                    event_id=f"evt_sweep_{symbol.lower()}_{timeframe.lower()}_{bar_close_ms}",
                    event_type="SWEEP_REJECTION",
                    direction="BEARISH",
                    reference_swing_id=st.protected_high["id"] if st.protected_high else "prot_high",
                    reference_price=prot_p,
                    breaking_bar_open_ms=bar_open_ms,
                    breaking_bar_close_ms=bar_close_ms,
                    close_price=bar_c,
                    known_at_ms=bar_close_ms,
                    previous_bias="BEARISH",
                    new_bias="BEARISH",
                    previous_phase=st.phase,
                    new_phase="ESTABLISHED",
                )
                st.phase = "ESTABLISHED"
                st.latest_event = evt
                return HTFStepResult(state=st, new_event=evt)

            # 3. Bearish BOS (Rule 067)
            elif st.reference_low and bar_c < st.reference_low["price"] - tick_size:
                break_key = (st.reference_low["id"], "BEARISH_BOS")
                if break_key not in st.consumed_breaks:
                    st.consumed_breaks.add(break_key)
                    if st.protected_high:
                        cand_highs = [
                            h for h in known_h
                            if h["occurred_at_ms"] > st.protected_high["occurred_at_ms"] and h["price"] < st.protected_high["price"]
                        ]
                        if cand_highs:
                            st.protected_high = cand_highs[-1]
                    evt = StructureEvent(
                        event_id=f"evt_bos_{symbol.lower()}_{timeframe.lower()}_{bar_close_ms}",
                        event_type="BOS",
                        direction="BEARISH",
                        reference_swing_id=st.reference_low["id"],
                        reference_price=st.reference_low["price"],
                        breaking_bar_open_ms=bar_open_ms,
                        breaking_bar_close_ms=bar_close_ms,
                        close_price=bar_c,
                        known_at_ms=bar_close_ms,
                        previous_bias="BEARISH",
                        new_bias="BEARISH",
                        previous_phase=st.phase,
                        new_phase="ESTABLISHED",
                    )
                    st.phase = "ESTABLISHED"
                    st.latest_event = evt
                    return HTFStepResult(state=st, new_event=evt)

            # 4. Internal Pullback
            else:
                if st.protected_high and known_h:
                    lower_highs = [h for h in known_h if h["occurred_at_ms"] > st.protected_high["occurred_at_ms"] and h["price"] < st.protected_high["price"]]
                    if lower_highs and bar_c > lower_highs[-1]["price"] + tick_size and bar_c <= prot_p + tick_size:
                        st.phase = "PULLBACK"
            return HTFStepResult(state=st, new_event=None)

    return HTFStepResult(state=previous_state, new_event=None)


def evaluate_htf_structure(
    candles: Sequence[Any],
    timeframe: str = "1H",
    *,
    as_of_ms: Optional[int] = None,
    symbol: str = "XAUUSDT",
    min_bars: Optional[int] = None,
    left_bars: Optional[int] = None,
    right_bars: Optional[int] = None,
    tick_size: Optional[float] = None,
    break_buffer_ticks: Optional[int] = None,
    config: Optional[Any] = None,
) -> HTFStructureSnapshot:
    """
    Pure causal market structure evaluator (Rules 041 - 080).
    Evaluates market structure strictly on closed candles up to as_of_ms.
    Never relies on SMA fallback to claim confirmed structure.
    Correctly recognizes invalidations when protected levels are broken by candle close.
    """
    dur = resolve_timeframe_ms(timeframe)

    # 1. Resolve Settings (Rule 041 - 043)
    settings = resolve_htf_settings(
        config=config,
        min_bars=min_bars,
        left_bars=left_bars,
        right_bars=right_bars,
        tick_size=tick_size,
        break_buffer_ticks=break_buffer_ticks,
    )

    # 2. Determine as_of_ms
    if as_of_ms is None:
        if candles:
            last_c = candles[-1]
            last_o = int(getattr(last_c, "open_time", None) or getattr(last_c, "timestamp", 0) if not isinstance(last_c, dict) else (last_c.get("open_time") or last_c.get("timestamp") or 0))
            as_of_ms = last_o + dur
        else:
            as_of_ms = 0

    # 3. Filter closed candles (Rule 035)
    closed_candles = filter_closed_candles(candles, timeframe, as_of_ms, symbol=symbol)
    n_bars = len(closed_candles)

    cfg_version = getattr(config, "config_version", 1) if config else 1

    if n_bars == 0:
        return HTFStructureSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            as_of_ms=as_of_ms,
            bias="UNKNOWN",
            last_confirmed_bias="UNKNOWN",
            phase="INSUFFICIENT_DATA",
            bars=0,
            provenance="NO_DATA",
            data_status="NO_DATA",
            effective_settings=settings.to_dict(),
            config_version=cfg_version,
        )

    last_bar = closed_candles[-1]
    last_open_ms, last_close_ms = get_candle_time_fields(last_bar, timeframe)

    if n_bars < settings.min_bars:
        return HTFStructureSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            as_of_ms=as_of_ms,
            bias="UNKNOWN",
            last_confirmed_bias="UNKNOWN",
            phase="INSUFFICIENT_DATA",
            bars=n_bars,
            last_bar_open_ms=last_open_ms,
            last_bar_close_ms=last_close_ms,
            provenance="INSUFFICIENT_BARS",
            data_status="INSUFFICIENT_DATA",
            effective_settings=settings.to_dict(),
            config_version=cfg_version,
        )

    # Indicator bias: 20 SMA of past closed bars for auxiliary context (never structural evidence)
    sma_bias = None
    sub_bars = closed_candles[-20:] if n_bars >= 20 else closed_candles
    if len(sub_bars) >= 5:
        closes = [float(getattr(b, "close", 0.0) if not isinstance(b, dict) else b.get("close", 0.0)) for b in sub_bars]
        ma = sum(closes) / len(closes)
        last_c_px = float(getattr(last_bar, "close", 0.0) if not isinstance(last_bar, dict) else last_bar.get("close", 0.0))
        sma_bias = "BULLISH" if last_c_px >= ma else "BEARISH"

    # 4. Detect causal swings known at or before as_of_ms
    highs, lows = identify_causal_swings(
        closed_candles,
        timeframe=timeframe,
        left_bars=settings.left_bars,
        right_bars=settings.right_bars,
        symbol=symbol,
        as_of_ms=as_of_ms,
        tick_size=settings.tick_size,
    )

    if len(highs) < 2 or len(lows) < 2:
        return HTFStructureSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            as_of_ms=as_of_ms,
            bias="UNKNOWN",
            last_confirmed_bias="UNKNOWN",
            phase="UNDETERMINED",
            bars=n_bars,
            last_bar_open_ms=last_open_ms,
            last_bar_close_ms=last_close_ms,
            reference_high=highs[-1] if highs else None,
            reference_low=lows[-1] if lows else None,
            provenance="INSUFFICIENT_CONFIRMED_SWINGS",
            data_status="READY",
            indicator_bias=sma_bias,
            effective_settings=settings.to_dict(),
            config_version=cfg_version,
        )

    # 5. Stepwise State Machine Execution (Rules 051 - 059)
    state = HTFState(
        bias="UNKNOWN",
        last_confirmed_bias="UNKNOWN",
        phase="UNDETERMINED",
        provenance="INSUFFICIENT_CONFIRMED_SWINGS",
    )

    for bar in closed_candles:
        _, bar_close_ms = get_candle_time_fields(bar, timeframe)
        known_h = [h for h in highs if h["known_at_ms"] <= bar_close_ms]
        known_l = [l for l in lows if l["known_at_ms"] <= bar_close_ms]

        step_res = step_htf_state(
            previous_state=state,
            bar=bar,
            known_h=known_h,
            known_l=known_l,
            settings=settings,
            timeframe=timeframe,
            symbol=symbol,
        )
        state = step_res.state

    return HTFStructureSnapshot(
        symbol=symbol,
        timeframe=timeframe,
        as_of_ms=as_of_ms,
        bias=state.bias,
        last_confirmed_bias=state.last_confirmed_bias,
        phase=state.phase,
        bars=n_bars,
        last_bar_open_ms=last_open_ms,
        last_bar_close_ms=last_close_ms,
        reference_high=state.reference_high or (highs[-1] if highs else None),
        reference_low=state.reference_low or (lows[-1] if lows else None),
        protected_high=state.protected_high,
        protected_low=state.protected_low,
        latest_structure_event=state.latest_event.to_dict() if state.latest_event else None,
        provenance=state.provenance,
        algorithm_version="v16.12",
        data_status="READY",
        indicator_bias=sma_bias,
        effective_settings=settings.to_dict(),
        config_version=cfg_version,
    )


def resolve_cg01_direction_context(
    h4_ctx: Optional[Any],
    h1_ctx: Optional[Any],
    range_pos: str = "EQUILIBRIUM",
    config: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Rules 029, 091 - 096: Multi-Timeframe Direction Context Resolver.
    Separates HTF structure facts, timeframe relation, and CG-01 entry hypothesis.
    """
    h4_bias = getattr(h4_ctx, "bias", "UNKNOWN") if h4_ctx else "UNKNOWN"
    h1_bias = getattr(h1_ctx, "bias", "UNKNOWN") if h1_ctx else "UNKNOWN"
    h4_phase = getattr(h4_ctx, "phase", "UNDETERMINED") if h4_ctx else "UNDETERMINED"
    h1_phase = getattr(h1_ctx, "phase", "UNDETERMINED") if h1_ctx else "UNDETERMINED"
    h4_status = getattr(h4_ctx, "data_status", "READY") if h4_ctx else "NO_DATA"
    h1_status = getattr(h1_ctx, "data_status", "READY") if h1_ctx else "NO_DATA"

    # Multi-Timeframe Relation (Rules 029, 091, 092)
    # Check TRANSITION first if both have ready data, so UNKNOWN bias does not mask transition
    if (h1_phase == "TRANSITION" or h4_phase == "TRANSITION") and h1_status == "READY" and h4_status == "READY":
        relation = "TRANSITION"
    elif h4_bias in ("UNKNOWN",) or h1_bias in ("UNKNOWN",) or h4_status != "READY" or h1_status != "READY":
        relation = "UNKNOWN"
    elif h4_bias == h1_bias:
        relation = "ALIGNED"
    elif h4_bias in ("BULLISH", "BEARISH") and h1_bias in ("BULLISH", "BEARISH"):
        relation = "DIVERGENT"
    else:
        relation = "MIXED"

    # Direction policy: Primary execution hypothesis is H1 structure in CG-01 (Rule 094)
    directions_to_evaluate: List[str] = []
    readiness = "READY"
    reason_codes: List[str] = []

    if h1_status != "READY" or h1_phase == "INSUFFICIENT_DATA":
        directions_to_evaluate = []
        readiness = "NOT_READY"
        primary_source = "INSUFFICIENT_HTF_DATA"
        reason_codes.append("HTF_DATA_INSUFFICIENT")
    elif h1_bias == "BULLISH":
        directions_to_evaluate = ["LONG"]
        primary_source = "H1_BULLISH_PRIMARY"
        reason_codes.append("H1_BULLISH")
        if relation == "DIVERGENT":
            reason_codes.append("H4_BEARISH_COUNTER_TREND")
    elif h1_bias == "BEARISH":
        directions_to_evaluate = ["SHORT"]
        primary_source = "H1_BEARISH_PRIMARY"
        reason_codes.append("H1_BEARISH")
        if relation == "DIVERGENT":
            reason_codes.append("H4_BULLISH_PULLBACK")
    elif h1_phase == "TRANSITION":
        directions_to_evaluate = ["LONG", "SHORT"]
        primary_source = "H1_TRANSITION_CONDITIONAL"
        reason_codes.append("H1_STRUCTURE_TRANSITION")
        if relation == "DIVERGENT":
            reason_codes.append("HTF_RELATION_DIVERGENT")
    elif h1_bias == "RANGE":
        directions_to_evaluate = ["LONG", "SHORT"]
        primary_source = "H1_RANGE_EVALUATE_BOUNDS"
        reason_codes.append("H1_RANGE")
    else:
        directions_to_evaluate = []
        readiness = "NOT_READY"
        primary_source = "INSUFFICIENT_HTF_DATA"
        reason_codes.append("HTF_DATA_INSUFFICIENT")

    return {
        "directions_to_evaluate": directions_to_evaluate,
        "primary_context_source": primary_source,
        "relation": relation,
        "readiness": readiness,
        "reason_codes": tuple(reason_codes),
        "policy_version": "v16.12",
        "h4_bias": h4_bias,
        "h1_bias": h1_bias,
        "h4_phase": h4_phase,
        "h1_phase": h1_phase,
    }
