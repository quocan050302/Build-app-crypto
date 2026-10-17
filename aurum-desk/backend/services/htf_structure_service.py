"""
backend/services/htf_structure_service.py
V16.11 — Causal Higher-Timeframe (HTF) Market Structure Engine for CG-01.

Implements Sections 011 - 080:
- Pure, causal market structure evaluator on closed candles.
- Protected High / Protected Low identification and invalidation tracking.
- Strict closed-candle boundary (candle_open + duration_ms <= as_of_ms).
- Rejection of SMA fallback as structural evidence (returns UNKNOWN/UNDETERMINED with provenance).
- Accurate time metadata: as_of_ms, last_bar_open_ms, last_bar_close_ms, event known_at_ms.
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
    tf_upper = (timeframe or "1H").upper()
    return TIMEFRAME_MS.get(tf_upper, 3_600_000)


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
) -> List[Any]:
    """
    Rule 022: Candle is ONLY eligible if open_time + timeframe_duration <= as_of_ms.
    If candle has is_closed=False, it is never treated as confirmed closed.
    """
    dur = resolve_timeframe_ms(timeframe)
    closed: List[Any] = []
    for c in candles:
        if isinstance(c, dict):
            open_ms = int(c.get("open_time") or c.get("timestamp") or 0)
            is_closed = c.get("is_closed", True)
        else:
            open_ms = int(getattr(c, "open_time", None) or getattr(c, "timestamp", 0))
            is_closed = getattr(c, "is_closed", True)

        if is_closed is False:
            continue
        if open_ms + dur <= as_of_ms:
            closed.append(c)

    # Ensure chronological ascending sort
    def _get_ts(c: Any) -> int:
        if isinstance(c, dict):
            return int(c.get("open_time") or c.get("timestamp") or 0)
        return int(getattr(c, "open_time", None) or getattr(c, "timestamp", 0))

    closed.sort(key=_get_ts)
    return closed


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
    event_type: str              # "BOS", "CHOCH", "PROTECTED_LOW_BREAK", "PROTECTED_HIGH_BREAK", "SWEEP_REJECTION"
    direction: str               # "BULLISH", "BEARISH"
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
    algorithm_version: str = "v16.11"
    data_status: str = "READY"
    indicator_bias: Optional[str] = None

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
        }


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
    Rule 031 - 038: Causal swing high and swing low detector.
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


def evaluate_htf_structure(
    candles: Sequence[Any],
    timeframe: str = "1H",
    *,
    as_of_ms: Optional[int] = None,
    symbol: str = "XAUUSDT",
    min_bars: int = 12,
    left_bars: int = 2,
    right_bars: int = 2,
    tick_size: float = 0.01,
    config: Optional[Any] = None,
) -> HTFStructureSnapshot:
    """
    Pure causal market structure evaluator (Rules 041 - 080).
    Evaluates market structure strictly on closed candles up to as_of_ms.
    Never relies on SMA fallback to claim confirmed structure.
    Correctly recognizes invalidations when protected levels are broken by candle close.
    """
    dur = resolve_timeframe_ms(timeframe)

    # 1. Determine as_of_ms
    if as_of_ms is None:
        if candles:
            last_c = candles[-1]
            last_o = int(getattr(last_c, "open_time", None) or getattr(last_c, "timestamp", 0) if not isinstance(last_c, dict) else (last_c.get("open_time") or last_c.get("timestamp") or 0))
            as_of_ms = last_o + dur
        else:
            as_of_ms = 0

    # 2. Filter closed candles
    closed_candles = filter_closed_candles(candles, timeframe, as_of_ms)
    n_bars = len(closed_candles)

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
        )

    last_bar = closed_candles[-1]
    last_open_ms, last_close_ms = get_candle_time_fields(last_bar, timeframe)

    if n_bars < min_bars:
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
        )

    # Indicator bias: 20 SMA of past closed bars for auxiliary context (never structural evidence)
    sma_bias = None
    sub_bars = closed_candles[-20:] if n_bars >= 20 else closed_candles
    if len(sub_bars) >= 5:
        closes = [float(getattr(b, "close", 0.0) if not isinstance(b, dict) else b.get("close", 0.0)) for b in sub_bars]
        ma = sum(closes) / len(closes)
        last_c_px = float(getattr(last_bar, "close", 0.0) if not isinstance(last_bar, dict) else last_bar.get("close", 0.0))
        sma_bias = "BULLISH" if last_c_px >= ma else "BEARISH"

    # 3. Detect causal swings known at or before as_of_ms
    highs, lows = identify_causal_swings(
        closed_candles,
        timeframe=timeframe,
        left_bars=left_bars,
        right_bars=right_bars,
        symbol=symbol,
        as_of_ms=as_of_ms,
        tick_size=tick_size,
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
        )

    # 4. Chronological Structural Evaluation
    # State tracking
    bias = "UNKNOWN"
    last_confirmed_bias = "UNKNOWN"
    phase = "UNDETERMINED"
    provenance = "INSUFFICIENT_CONFIRMED_SWINGS"

    protected_low: Optional[Dict[str, Any]] = None
    protected_high: Optional[Dict[str, Any]] = None
    ref_high: Optional[Dict[str, Any]] = None
    ref_low: Optional[Dict[str, Any]] = None
    latest_event: Optional[StructureEvent] = None
    consumed_breaks: Set[Tuple[str, str]] = set()

    # Step through historical bars
    for idx, bar in enumerate(closed_candles):
        bar_open_ms, bar_close_ms = get_candle_time_fields(bar, timeframe)
        bar_h = float(getattr(bar, "high", 0.0) if not isinstance(bar, dict) else bar.get("high", 0.0))
        bar_l = float(getattr(bar, "low", 0.0) if not isinstance(bar, dict) else bar.get("low", 0.0))
        bar_c = float(getattr(bar, "close", 0.0) if not isinstance(bar, dict) else bar.get("close", 0.0))

        # Swings known at or before bar_close_ms
        known_h = [h for h in highs if h["known_at_ms"] <= bar_close_ms]
        known_l = [l for l in lows if l["known_at_ms"] <= bar_close_ms]

        if len(known_h) < 2 or len(known_l) < 2:
            continue

        h1, h2 = known_h[-2], known_h[-1]
        l1, l2 = known_l[-2], known_l[-1]

        # 4A. Initial sequence detection (when UNKNOWN or TRANSITION)
        if bias == "UNKNOWN" or phase in ("UNDETERMINED", "TRANSITION"):
            is_bull_seq = (h2["price"] > h1["price"] + tick_size) and (l2["price"] > l1["price"] + tick_size)
            is_bear_seq = (h2["price"] < h1["price"] - tick_size) and (l2["price"] < l1["price"] - tick_size)
            is_range_seq = (abs(h2["price"] - h1["price"]) <= tick_size) and (abs(l2["price"] - l1["price"]) <= tick_size)

            if is_bull_seq:
                if bar_c >= l2["price"] - tick_size:
                    bias = "BULLISH"
                    last_confirmed_bias = "BULLISH"
                    phase = "ESTABLISHED"
                    protected_low = l2
                    protected_high = None
                    ref_high = h2
                    ref_low = l2
                    provenance = "CONFIRMED_SWING_SEQUENCE"
            elif is_bear_seq:
                if bar_c <= h2["price"] + tick_size:
                    bias = "BEARISH"
                    last_confirmed_bias = "BEARISH"
                    phase = "ESTABLISHED"
                    protected_high = h2
                    protected_low = None
                    ref_high = h2
                    ref_low = l2
                    provenance = "CONFIRMED_SWING_SEQUENCE"
            elif is_range_seq:
                bias = "RANGE"
                phase = "RANGE"
                ref_high = h2
                ref_low = l2
                provenance = "CONFIRMED_RANGE_STRUCTURE"

        # 4B. Active Bullish State Evaluation
        elif bias == "BULLISH":
            # Update reference high if new swing high known
            if known_h and known_h[-1]["occurred_at_ms"] > (ref_high["occurred_at_ms"] if ref_high else 0):
                ref_high = known_h[-1]

            prot_p = protected_low["price"] if protected_low else l2["price"]

            # 1. Protected Low Broken by closed candle body
            if bar_c < prot_p - tick_size:
                bias = "UNKNOWN"
                phase = "TRANSITION"
                provenance = "TRANSITION_AFTER_PROTECTED_BREAK"
                latest_event = StructureEvent(
                    event_id=f"evt_prot_low_break_{bar_close_ms}",
                    event_type="PROTECTED_LOW_BREAK",
                    direction="BEARISH",
                    reference_swing_id=protected_low["id"] if protected_low else l2["id"],
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

            # 2. Sweep / Wick Rejection: wick penetrated, but candle closed back inside
            elif bar_l < prot_p and bar_c >= prot_p - tick_size:
                latest_event = StructureEvent(
                    event_id=f"evt_sweep_{bar_close_ms}",
                    event_type="SWEEP_REJECTION",
                    direction="BULLISH",
                    reference_swing_id=protected_low["id"] if protected_low else l2["id"],
                    reference_price=prot_p,
                    breaking_bar_open_ms=bar_open_ms,
                    breaking_bar_close_ms=bar_close_ms,
                    close_price=bar_c,
                    known_at_ms=bar_close_ms,
                    previous_bias="BULLISH",
                    new_bias="BULLISH",
                    previous_phase=phase,
                    new_phase="ESTABLISHED",
                )

            # 3. Bullish BOS (Break of structure beyond previous high)
            elif ref_high and bar_c > ref_high["price"] + tick_size:
                break_key = (ref_high["id"], "BULLISH_BOS")
                if break_key not in consumed_breaks:
                    consumed_breaks.add(break_key)
                    # Trail protected low to intermediate confirmed higher low if present
                    if protected_low:
                        cand_lows = [
                            l for l in known_l
                            if l["occurred_at_ms"] > protected_low["occurred_at_ms"] and l["price"] > protected_low["price"]
                        ]
                        if cand_lows:
                            protected_low = cand_lows[-1]
                    phase = "ESTABLISHED"
                    latest_event = StructureEvent(
                        event_id=f"evt_bos_{bar_close_ms}",
                        event_type="BOS",
                        direction="BULLISH",
                        reference_swing_id=ref_high["id"],
                        reference_price=ref_high["price"],
                        breaking_bar_open_ms=bar_open_ms,
                        breaking_bar_close_ms=bar_close_ms,
                        close_price=bar_c,
                        known_at_ms=bar_close_ms,
                        previous_bias="BULLISH",
                        new_bias="BULLISH",
                        previous_phase=phase,
                        new_phase="ESTABLISHED",
                    )

            # 4. Internal Pullback
            else:
                # If an internal swing low formed after protected low is breached by close, flag PULLBACK
                if protected_low and known_l:
                    higher_lows = [l for l in known_l if l["occurred_at_ms"] > protected_low["occurred_at_ms"] and l["price"] > protected_low["price"]]
                    if higher_lows and bar_c < higher_lows[-1]["price"] - tick_size and bar_c >= prot_p - tick_size:
                        phase = "PULLBACK"

        # 4C. Active Bearish State Evaluation
        elif bias == "BEARISH":
            # Update reference low if new swing low known
            if known_l and known_l[-1]["occurred_at_ms"] > (ref_low["occurred_at_ms"] if ref_low else 0):
                ref_low = known_l[-1]

            prot_p = protected_high["price"] if protected_high else h2["price"]

            # 1. Protected High Broken by closed candle body
            if bar_c > prot_p + tick_size:
                bias = "UNKNOWN"
                phase = "TRANSITION"
                provenance = "TRANSITION_AFTER_PROTECTED_BREAK"
                latest_event = StructureEvent(
                    event_id=f"evt_prot_high_break_{bar_close_ms}",
                    event_type="PROTECTED_HIGH_BREAK",
                    direction="BULLISH",
                    reference_swing_id=protected_high["id"] if protected_high else h2["id"],
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

            # 2. Sweep / Wick Rejection: wick penetrated, but candle closed back inside
            elif bar_h > prot_p and bar_c <= prot_p + tick_size:
                latest_event = StructureEvent(
                    event_id=f"evt_sweep_{bar_close_ms}",
                    event_type="SWEEP_REJECTION",
                    direction="BEARISH",
                    reference_swing_id=protected_high["id"] if protected_high else h2["id"],
                    reference_price=prot_p,
                    breaking_bar_open_ms=bar_open_ms,
                    breaking_bar_close_ms=bar_close_ms,
                    close_price=bar_c,
                    known_at_ms=bar_close_ms,
                    previous_bias="BEARISH",
                    new_bias="BEARISH",
                    previous_phase=phase,
                    new_phase="ESTABLISHED",
                )

            # 3. Bearish BOS (Break of structure beyond previous low)
            elif ref_low and bar_c < ref_low["price"] - tick_size:
                break_key = (ref_low["id"], "BEARISH_BOS")
                if break_key not in consumed_breaks:
                    consumed_breaks.add(break_key)
                    if protected_high:
                        cand_highs = [
                            h for h in known_h
                            if h["occurred_at_ms"] > protected_high["occurred_at_ms"] and h["price"] < protected_high["price"]
                        ]
                        if cand_highs:
                            protected_high = cand_highs[-1]
                    phase = "ESTABLISHED"
                    latest_event = StructureEvent(
                        event_id=f"evt_bos_{bar_close_ms}",
                        event_type="BOS",
                        direction="BEARISH",
                        reference_swing_id=ref_low["id"],
                        reference_price=ref_low["price"],
                        breaking_bar_open_ms=bar_open_ms,
                        breaking_bar_close_ms=bar_close_ms,
                        close_price=bar_c,
                        known_at_ms=bar_close_ms,
                        previous_bias="BEARISH",
                        new_bias="BEARISH",
                        previous_phase=phase,
                        new_phase="ESTABLISHED",
                    )

            # 4. Internal Pullback
            else:
                if protected_high and known_h:
                    lower_highs = [h for h in known_h if h["occurred_at_ms"] > protected_high["occurred_at_ms"] and h["price"] < protected_high["price"]]
                    if lower_highs and bar_c > lower_highs[-1]["price"] + tick_size and bar_c <= prot_p + tick_size:
                        phase = "PULLBACK"

    return HTFStructureSnapshot(
        symbol=symbol,
        timeframe=timeframe,
        as_of_ms=as_of_ms,
        bias=bias,
        last_confirmed_bias=last_confirmed_bias,
        phase=phase,
        bars=n_bars,
        last_bar_open_ms=last_open_ms,
        last_bar_close_ms=last_close_ms,
        reference_high=ref_high or (highs[-1] if highs else None),
        reference_low=ref_low or (lows[-1] if lows else None),
        protected_high=protected_high,
        protected_low=protected_low,
        latest_structure_event=latest_event.to_dict() if latest_event else None,
        provenance=provenance,
        algorithm_version="v16.11",
        data_status="READY",
        indicator_bias=sma_bias,
    )


def resolve_cg01_direction_context(
    h4_ctx: Optional[Any],
    h1_ctx: Optional[Any],
    range_pos: str = "EQUILIBRIUM",
    config: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Rules 046 - 050, 103 - 110 & V16.11: Multi-Timeframe Direction Context Resolver.
    Separates HTF structure facts, timeframe relation, and CG-01 entry hypothesis.
    """
    h4_bias = getattr(h4_ctx, "bias", "UNKNOWN") if h4_ctx else "UNKNOWN"
    h1_bias = getattr(h1_ctx, "bias", "UNKNOWN") if h1_ctx else "UNKNOWN"
    h4_phase = getattr(h4_ctx, "phase", "UNDETERMINED") if h4_ctx else "UNDETERMINED"
    h1_phase = getattr(h1_ctx, "phase", "UNDETERMINED") if h1_ctx else "UNDETERMINED"

    # Multi-Timeframe Relation
    if h4_bias in ("UNKNOWN",) or h1_bias in ("UNKNOWN",):
        relation = "UNKNOWN"
    elif h4_bias == h1_bias:
        relation = "ALIGNED"
    elif h4_bias in ("BULLISH", "BEARISH") and h1_bias in ("BULLISH", "BEARISH"):
        relation = "DIVERGENT"
    elif h4_phase == "TRANSITION" or h1_phase == "TRANSITION":
        relation = "TRANSITION"
    else:
        relation = "MIXED"

    # Direction policy: Primary execution hypothesis is H1 structure in CG-01
    directions_to_evaluate: List[str] = []
    readiness = "READY"
    reason_codes: List[str] = []

    if h1_bias == "BULLISH":
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
    elif h1_bias == "RANGE":
        directions_to_evaluate = ["LONG", "SHORT"]
        primary_source = "H1_RANGE_EVALUATE_BOUNDS"
        reason_codes.append("H1_RANGE")
    else:
        # UNKNOWN / INSUFFICIENT DATA: Do not blindly evaluate all directions
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
        "policy_version": "v16.11",
        "h4_bias": h4_bias,
        "h1_bias": h1_bias,
        "h4_phase": h4_phase,
        "h1_phase": h1_phase,
    }
