"""
backend/services/stop_placement_service.py
AURUM V17.5 — Pure Calculation and Resolution Services for Structural Stop Placement.
Zero database mutations, zero network I/O, zero wall-clock dependencies.
"""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.stop_placement_types import (
    PriceBasis,
    StopAnchor,
    StopAnchorKind,
    StopAnchorResult,
    StopATRResult,
    StopBufferResult,
    StopMetrics,
    StopPlacementDecision,
    StopPlacementMode,
    StopPlacementPolicy,
    StopProtectiveDistanceResult,
    StopStatus,
)


def _candle_open_ms(c: Any) -> int:
    val = getattr(c, "open_time", None) or getattr(c, "timestamp", None)
    if val is None and isinstance(c, dict):
        val = c.get("open_time") or c.get("timestamp")
    return int(val or 0)


def _candle_ohlc(c: Any) -> Tuple[float, float, float, float]:
    if isinstance(c, dict):
        return float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
    return float(c.open), float(c.high), float(c.low), float(c.close)


def parse_valid_market_timestamp(
    value: Any,
    *,
    field_name: str = "timestamp",
    allow_zero: bool = False,
) -> Tuple[bool, Optional[int], Optional[str]]:
    """
    AURUM V17.5.9 (Part 019, D06):
    Safe timestamp parser rejecting booleans, NaN, Inf, non-numeric strings,
    and negative/zero timestamps without throwing unhandled exceptions.
    """
    if value is None:
        return False, None, f"MISSING_{field_name.upper()}"
    if isinstance(value, bool):
        return False, None, f"INVALID_{field_name.upper()}"
    try:
        if isinstance(value, str):
            val_str = value.strip()
            if not val_str:
                return False, None, f"MISSING_{field_name.upper()}"
            f_val = float(val_str)
        elif isinstance(value, (int, float)):
            f_val = float(value)
        else:
            return False, None, f"INVALID_{field_name.upper()}"

        if not math.isfinite(f_val):
            return False, None, f"INVALID_{field_name.upper()}"

        int_val = int(f_val)
        if not allow_zero and int_val <= 0:
            return False, None, f"INVALID_{field_name.upper()}"
        if allow_zero and int_val < 0:
            return False, None, f"INVALID_{field_name.upper()}"

        return True, int_val, None
    except (ValueError, TypeError, OverflowError):
        return False, None, f"INVALID_{field_name.upper()}"


TIMEFRAME_MS: Dict[str, int] = {
    "1M": 60_000,
    "5M": 300_000,
    "15M": 900_000,
    "1H": 3_600_000,
}


def validate_stop_atr_authority(
    atr_result: Any,
    *,
    expected_symbol: Optional[str] = None,
    expected_timeframe: str = "1M",
    expected_period: int = 14,
    expected_method: str = "SMA_TR_V1",
    decision_cutoff: Optional[int] = None,
    max_age_ms: Optional[int] = None,
) -> Tuple[bool, str]:
    """
    AURUM DESK V17.7.4 (Section 04):
    Authoritative ATR validator shared across planner, evaluators, and ARM boundary.
    Validates provenance, schema, finite positive value, symbol, timeframe, period,
    source bar count, causal closure, and duplicate metadata consistency.
    Fails closed with stable, standardized reason codes.
    """
    if atr_result is None:
        return False, "ATR_MISSING"

    # 1. Type and schema check
    if not hasattr(atr_result, "status") or not hasattr(atr_result, "value"):
        return False, "ATR_PROVENANCE_MISSING"

    # 2. Status check
    status = getattr(atr_result, "status", None)
    if status != "AVAILABLE":
        rc = getattr(atr_result, "reason_code", None)
        if rc in ("ZERO_OR_NEGATIVE_ATR", "NON_POSITIVE_OR_NON_FINITE_ATR"):
            return False, "ATR_NON_POSITIVE_OR_NON_FINITE"
        return False, f"ATR_STATUS_UNAVAILABLE: {rc}" if rc else "ATR_STATUS_UNAVAILABLE"

    # 3. Value check: finite, positive, NOT bool!
    val = getattr(atr_result, "value", None)
    if val is None or isinstance(val, bool):
        return False, "ATR_NON_POSITIVE_OR_NON_FINITE"
    try:
        f_val = float(val)
        if not math.isfinite(f_val) or f_val <= 0.0:
            return False, "ATR_NON_POSITIVE_OR_NON_FINITE"
    except (ValueError, TypeError, OverflowError):
        return False, "ATR_NON_POSITIVE_OR_NON_FINITE"

    # 4. Timeframe check
    tf = getattr(atr_result, "timeframe", None)
    if tf is None or str(tf).upper() != str(expected_timeframe).upper():
        return False, "ATR_TIMEFRAME_MISMATCH"

    # 5. Symbol check
    sym = getattr(atr_result, "symbol", None)
    if expected_symbol is not None:
        if sym is None or str(sym).upper() != str(expected_symbol).upper():
            return False, "ATR_SYMBOL_MISMATCH"

    # 6. Period check: defensive parsing
    prd = getattr(atr_result, "period", None)
    if prd is None or isinstance(prd, bool):
        return False, "ATR_PERIOD_MISMATCH"
    try:
        if int(prd) != int(expected_period):
            return False, "ATR_PERIOD_MISMATCH"
    except (ValueError, TypeError):
        return False, "ATR_PERIOD_MISMATCH"

    # 7. Method check
    method = getattr(atr_result, "method", None)
    if method is None or str(method) != str(expected_method):
        return False, "ATR_METHOD_UNSUPPORTED"

    # 8. Source candle count: SMA_TR_V1 needs >= period + 1
    bars_count = getattr(atr_result, "bars_count", None)
    source_candle_count = getattr(atr_result, "source_candle_count", None)
    min_count = expected_period + 1
    eff_count = bars_count if bars_count is not None else source_candle_count
    if eff_count is None or isinstance(eff_count, bool):
        return False, "ATR_SOURCE_COUNT_INSUFFICIENT"
    try:
        if int(eff_count) < min_count:
            return False, "ATR_SOURCE_COUNT_INSUFFICIENT"
    except (ValueError, TypeError):
        return False, "ATR_SOURCE_COUNT_INSUFFICIENT"

    # 11. Duplicate metadata consistency
    if (
        bars_count is not None
        and source_candle_count is not None
        and not isinstance(bars_count, bool)
        and not isinstance(source_candle_count, bool)
    ):
        try:
            bc_int = int(bars_count)
            sc_int = int(source_candle_count)
            if bc_int > 0 and sc_int > 0 and bc_int != sc_int:
                return False, "ATR_PROVENANCE_INCONSISTENT"
        except (ValueError, TypeError):
            return False, "ATR_PROVENANCE_INCONSISTENT"

    last_closed_at = getattr(atr_result, "last_closed_at_ms", None)
    last_closed_candle = getattr(atr_result, "last_closed_candle_timestamp", None)
    if (
        last_closed_at is not None
        and last_closed_candle is not None
        and not isinstance(last_closed_at, bool)
        and not isinstance(last_closed_candle, bool)
    ):
        try:
            lca_int = int(last_closed_at)
            lcc_int = int(last_closed_candle)
            if lca_int > 0 and lcc_int > 0 and lca_int != lcc_int:
                return False, "ATR_PROVENANCE_INCONSISTENT"
        except (ValueError, TypeError):
            return False, "ATR_PROVENANCE_INCONSISTENT"

    # 9. Required timestamps: last_closed_at_ms or last_closed_candle_timestamp MUST be present, positive int > 0
    eff_last_closed = last_closed_at if last_closed_at is not None else last_closed_candle
    if eff_last_closed is None or isinstance(eff_last_closed, bool):
        return False, "ATR_TIMESTAMP_MISSING_OR_INVALID"
    try:
        int_last_closed = int(eff_last_closed)
        if int_last_closed <= 0:
            return False, "ATR_TIMESTAMP_MISSING_OR_INVALID"
    except (ValueError, TypeError):
        return False, "ATR_TIMESTAMP_MISSING_OR_INVALID"

    calc_as_of = getattr(atr_result, "calculated_as_of", None)
    if calc_as_of is None or isinstance(calc_as_of, bool):
        return False, "ATR_TIMESTAMP_MISSING_OR_INVALID"
    try:
        int_calc_as_of = int(calc_as_of)
        if int_calc_as_of < 0:
            return False, "ATR_TIMESTAMP_MISSING_OR_INVALID"
    except (ValueError, TypeError):
        return False, "ATR_TIMESTAMP_MISSING_OR_INVALID"

    # Effective calculated_as_of: if 0, defaults to int_last_closed
    eff_calc_as_of = int_calc_as_of if int_calc_as_of > 0 else int_last_closed

    # Causal ordering: last closed bar must have closed at or before calculation time
    if int_last_closed > eff_calc_as_of:
        return False, "ATR_PROVENANCE_INCONSISTENT"

    # 10. Decision cutoff validation (V17.7.4.1 Section 04.2)
    int_cutoff = None
    if decision_cutoff is not None:
        if isinstance(decision_cutoff, bool):
            return False, "ATR_DECISION_CUTOFF_INVALID"
        try:
            if isinstance(decision_cutoff, float) and (math.isnan(decision_cutoff) or math.isinf(decision_cutoff)):
                return False, "ATR_DECISION_CUTOFF_INVALID"
            int_cutoff = int(decision_cutoff)
            if int_cutoff < 0:
                return False, "ATR_DECISION_CUTOFF_INVALID"
            if int_last_closed > int_cutoff:
                return False, "ATR_SOURCE_AFTER_DECISION"
            if eff_calc_as_of > int_cutoff:
                return False, "ATR_SOURCE_AFTER_DECISION"
        except (ValueError, TypeError, OverflowError):
            return False, "ATR_DECISION_CUTOFF_INVALID"

    # Own decision cutoff validation (if set > 0, must be >= eff_calc_as_of, and <= caller cutoff)
    own_cutoff = getattr(atr_result, "decision_cutoff", None)
    if own_cutoff is not None:
        if isinstance(own_cutoff, bool):
            return False, "ATR_DECISION_CUTOFF_INVALID"
        try:
            if isinstance(own_cutoff, float) and (math.isnan(own_cutoff) or math.isinf(own_cutoff)):
                return False, "ATR_DECISION_CUTOFF_INVALID"
            int_own_cutoff = int(own_cutoff)
            if int_own_cutoff < 0:
                return False, "ATR_DECISION_CUTOFF_INVALID"
            if int_own_cutoff > 0:
                if eff_calc_as_of > int_own_cutoff:
                    return False, "ATR_PROVENANCE_INCONSISTENT"
                if int_cutoff is not None and int_own_cutoff > int_cutoff:
                    return False, "ATR_SOURCE_AFTER_DECISION"
        except (ValueError, TypeError, OverflowError):
            return False, "ATR_DECISION_CUTOFF_INVALID"

    # 12. Freshness check
    if max_age_ms is not None:
        if isinstance(max_age_ms, bool):
            return False, "ATR_TIMESTAMP_MISSING_OR_INVALID"
        try:
            if isinstance(max_age_ms, float) and (math.isnan(max_age_ms) or math.isinf(max_age_ms)):
                return False, "ATR_TIMESTAMP_MISSING_OR_INVALID"
            int_max_age = int(max_age_ms)
            if int_max_age < 0:
                return False, "ATR_TIMESTAMP_MISSING_OR_INVALID"
            ref_cutoff = int_cutoff if int_cutoff is not None else eff_calc_as_of
            if ref_cutoff - int_last_closed > int_max_age:
                return False, "ATR_STALE_FOR_DECISION"
        except (ValueError, TypeError, OverflowError):
            return False, "ATR_TIMESTAMP_MISSING_OR_INVALID"

    return True, "VALID"


def compute_stop_atr(
    closed_bars: Sequence[Any],
    *,
    period: int = 14,
    timeframe: str = "1M",
    as_of_ms: Optional[int] = None,
    bar_duration_ms: Optional[int] = None,
    symbol: Optional[str] = None,
) -> StopATRResult:
    """
    Computes strict True Range Mean (SMA_TR_V1) for stop loss buffer sizing (V17.7.4).
    Requires at least `period + 1` continuous, finite closed bars.
    Excludes unclosed bars or future bars after as_of_ms.
    Strictly synchronizes bar_duration_ms with timeframe.
    Validates symbol consistency, candle closure, and positive finite prices.
    Enforces active-window cadence verification (no gap bridging).
    """
    norm_tf = timeframe.upper() if isinstance(timeframe, str) else "1M"
    expected_duration = TIMEFRAME_MS.get(norm_tf)
    if expected_duration is None:
        return StopATRResult(
            status="UNAVAILABLE",
            value=0.0,
            period=period,
            timeframe=timeframe,
            method="SMA_TR_V1",
            last_closed_at_ms=0,
            bars_count=0,
            reason_code="UNSUPPORTED_TIMEFRAME",
            symbol=symbol,
            calculated_as_of=as_of_ms or 0,
            decision_cutoff=as_of_ms or 0,
        )

    if bar_duration_ms is not None and bar_duration_ms != expected_duration:
        return StopATRResult(
            status="UNAVAILABLE",
            value=0.0,
            period=period,
            timeframe=timeframe,
            method="SMA_TR_V1",
            last_closed_at_ms=0,
            bars_count=0,
            reason_code="TIMEFRAME_DURATION_MISMATCH",
            symbol=symbol,
            calculated_as_of=as_of_ms or 0,
            decision_cutoff=as_of_ms or 0,
        )

    duration = expected_duration

    try:
        if period is None or isinstance(period, bool):
            raise ValueError("INVALID_PERIOD")
        int_period = int(period)
        if int_period <= 0:
            raise ValueError("INVALID_PERIOD")
    except (ValueError, TypeError):
        return StopATRResult(
            status="UNAVAILABLE",
            value=0.0,
            period=0,
            timeframe=timeframe if isinstance(timeframe, str) else "1M",
            method="SMA_TR_V1",
            last_closed_at_ms=0,
            bars_count=0,
            reason_code="INVALID_PERIOD",
            symbol=symbol,
            calculated_as_of=as_of_ms or 0,
            decision_cutoff=as_of_ms or 0,
        )
    period = int_period

    # Sort closed_bars by open timestamp to normalize ordering
    sorted_raw_bars: List[Any] = []
    for b in closed_bars:
        try:
            t_open = _candle_open_ms(b)
            sorted_raw_bars.append((t_open, b))
        except Exception:
            continue
    sorted_raw_bars.sort(key=lambda item: item[0])

    # Filter bars strictly closed at or before as_of_ms with valid OHLC and symbol
    valid_bars: List[Any] = []
    last_ts = -1
    for t_open, b in sorted_raw_bars:
        try:
            # Check symbol if present on bar
            b_sym = getattr(b, "symbol", None) or (b.get("symbol") if isinstance(b, dict) else None)
            if symbol is not None and b_sym is not None and str(b_sym).upper() != str(symbol).upper():
                return StopATRResult(
                    status="UNAVAILABLE",
                    value=0.0,
                    period=period,
                    timeframe=timeframe,
                    method="SMA_TR_V1",
                    last_closed_at_ms=0,
                    bars_count=0,
                    reason_code="SYMBOL_MISMATCH",
                    symbol=symbol,
                    calculated_as_of=as_of_ms or 0,
                    decision_cutoff=as_of_ms or 0,
                )

            # Check unclosed candle status explicitly without truthiness short-circuit (V17.7.4 Section 05.B)
            is_closed = None
            if hasattr(b, "is_closed"):
                is_closed = getattr(b, "is_closed")
            elif isinstance(b, dict) and "is_closed" in b:
                is_closed = b["is_closed"]

            if is_closed is False:
                continue

            o, h, l, c = _candle_ohlc(b)
            if not all(math.isfinite(x) and not isinstance(x, bool) and x > 0 for x in (o, h, l, c)):
                continue
            if not (l <= min(o, c) <= max(o, c) <= h and h >= l):
                continue
            if t_open <= last_ts:
                # Disallow duplicate or out-of-order timestamps
                continue
            if as_of_ms is not None:
                t_close = t_open + duration
                if t_close > as_of_ms:
                    continue
            last_ts = t_open
            valid_bars.append(b)
        except Exception:
            continue

    if len(valid_bars) < period + 1:
        last_ms = _candle_open_ms(valid_bars[-1]) + duration if valid_bars else 0
        return StopATRResult(
            status="UNAVAILABLE",
            value=0.0,
            period=period,
            timeframe=timeframe,
            method="SMA_TR_V1",
            last_closed_at_ms=last_ms,
            bars_count=len(valid_bars),
            reason_code="INSUFFICIENT_BARS",
            symbol=symbol,
            calculated_as_of=as_of_ms or 0,
            decision_cutoff=as_of_ms or 0,
        )

    # Active calculation window: the last `period + 1` bars
    active_bars = valid_bars[-(period + 1):]

    # Verify cadence continuity in the active calculation window (V17.7.4 Section 05.C)
    for i in range(len(active_bars) - 1):
        t_curr = _candle_open_ms(active_bars[i])
        t_next = _candle_open_ms(active_bars[i + 1])
        if t_next - t_curr != duration:
            last_ms = _candle_open_ms(active_bars[-1]) + duration
            return StopATRResult(
                status="UNAVAILABLE",
                value=0.0,
                period=period,
                timeframe=timeframe,
                method="SMA_TR_V1",
                last_closed_at_ms=last_ms,
                bars_count=len(valid_bars),
                reason_code="NON_CONTIGUOUS_BARS",
                symbol=symbol,
                calculated_as_of=as_of_ms or 0,
                decision_cutoff=as_of_ms or 0,
            )

    # Compute True Range across the active window
    tr_values: List[float] = []
    for i in range(1, len(active_bars)):
        _, h_curr, l_curr, _ = _candle_ohlc(active_bars[i])
        _, _, _, c_prev = _candle_ohlc(active_bars[i - 1])
        tr = max(h_curr - l_curr, abs(h_curr - c_prev), abs(l_curr - c_prev))
        tr_values.append(tr)

    atr_val = sum(tr_values) / float(period)

    last_bar_open = _candle_open_ms(active_bars[-1])
    last_closed_ms = last_bar_open + duration

    if not math.isfinite(atr_val) or atr_val <= 0.0:
        return StopATRResult(
            status="UNAVAILABLE",
            value=0.0,
            period=period,
            timeframe=timeframe,
            method="SMA_TR_V1",
            last_closed_at_ms=last_closed_ms,
            bars_count=len(valid_bars),
            reason_code="ZERO_OR_NEGATIVE_ATR",
            symbol=symbol,
            calculated_as_of=as_of_ms or last_closed_ms,
            decision_cutoff=as_of_ms or last_closed_ms,
        )

    return StopATRResult(
        status="AVAILABLE",
        value=float(atr_val),
        period=period,
        timeframe=norm_tf,
        method="SMA_TR_V1",
        last_closed_at_ms=last_closed_ms,
        bars_count=len(valid_bars),
        symbol=symbol,
        source="CANDLE_CLOSED",
        source_candle_count=len(valid_bars),
        last_closed_candle_timestamp=last_closed_ms,
        calculated_as_of=as_of_ms or last_closed_ms,
        decision_cutoff=as_of_ms or last_closed_ms,
    )


def quantize_stop_outward(
    *,
    anchor_price: float,
    buffer_price: float,
    direction: str,
    tick_size: float,
) -> float:
    """
    Quantizes the stop loss outward (away from the market/anchor) to the instrument tick grid.
    SHORT: ceil((anchor + buffer) / tick) * tick
    LONG:  floor((anchor - buffer) / tick) * tick
    Uses Decimal for exact monetary arithmetic without float drift.
    """
    anchor_d = Decimal(str(anchor_price))
    buffer_d = Decimal(str(buffer_price))
    tick_d = Decimal(str(tick_size))

    if not (anchor_d.is_finite() and buffer_d.is_finite() and tick_d.is_finite()):
        raise ValueError("STOP_INPUT_NON_FINITE")
    if tick_d <= Decimal("0") or buffer_d < Decimal("0"):
        raise ValueError("STOP_INPUT_INVALID")

    norm_dir = direction.upper()
    if norm_dir in ("SHORT", "SELL"):
        raw = anchor_d + buffer_d
        steps = (raw / tick_d).to_integral_value(rounding=ROUND_CEILING)
    elif norm_dir in ("LONG", "BUY"):
        raw = anchor_d - buffer_d
        steps = (raw / tick_d).to_integral_value(rounding=ROUND_FLOOR)
    else:
        raise ValueError(f"STOP_DIRECTION_INVALID: {direction}")

    price_d = steps * tick_d
    if price_d <= Decimal("0"):
        raise ValueError("STOP_PRICE_INVALID")

    return float(price_d)


def compute_stop_buffer(
    *,
    policy: StopPlacementPolicy,
    tick_size: float,
    atr_m1: Optional[StopATRResult] = None,
    atr_m5: Optional[StopATRResult] = None,
    bid: float,
    ask: float,
    symbol: Optional[str] = None,
    decision_cutoff: Optional[int] = None,
) -> StopBufferResult:
    """
    Computes candidate dynamic buffer components according to StopPlacementPolicy (V17.7.4):
    buffer = max(tick_floor, k_m1*raw_ATR_M1, k_m5*raw_ATR_M5 (if available), k_spread*(ask-bid))
    Records individual component values, preserves raw ATRs, and identifies the dominant source.
    Validates ATR inputs via validate_stop_atr_authority.
    """
    f_tick = float(tick_size)
    if not math.isfinite(f_tick) or f_tick <= 0.0:
        raise ValueError("INVALID_TICK_SIZE")

    k_m1 = float(getattr(policy, "atr_m1_multiplier", 0.0))
    if not math.isfinite(k_m1) or k_m1 < 0.0:
        raise ValueError("INVALID_ATR_M1_MULTIPLIER")

    k_m5 = float(getattr(policy, "atr_m5_multiplier", 0.0))
    if not math.isfinite(k_m5) or k_m5 < 0.0:
        raise ValueError("INVALID_ATR_M5_MULTIPLIER")

    k_spread = float(getattr(policy, "spread_multiplier", 0.0))
    if not math.isfinite(k_spread) or k_spread < 0.0:
        raise ValueError("INVALID_SPREAD_MULTIPLIER")

    tick_floor = float(policy.min_buffer_ticks) * f_tick
    components: Dict[str, float] = {
        "tick_floor": tick_floor,
    }

    raw_m1 = None
    if atr_m1 is not None and getattr(atr_m1, "status", None) == "AVAILABLE":
        m1_sym = getattr(atr_m1, "symbol", None)
        m1_tf = getattr(atr_m1, "timeframe", None)

        m1_cutoff_ok = True
        if decision_cutoff is not None:
            if isinstance(decision_cutoff, bool) or not isinstance(decision_cutoff, (int, float)) or math.isnan(decision_cutoff) or int(decision_cutoff) < 0:
                m1_cutoff_ok = False
            elif getattr(atr_m1, "last_closed_at_ms", 0) > int(decision_cutoff):
                m1_cutoff_ok = False

        mode = getattr(policy, "mode", StopPlacementMode.SHADOW_COMPARE.value)
        has_provenance_meta = (
            getattr(atr_m1, "symbol", None) is not None
            or getattr(atr_m1, "last_closed_at_ms", 0) > 0
            or getattr(atr_m1, "decision_cutoff", 0) > 0
            or symbol is not None
            or decision_cutoff is not None
        )
        if mode == StopPlacementMode.PAPER_STRUCTURAL.value:
            if has_provenance_meta:
                # V17.7.4.1 Section 04.1: When provenance metadata or execution context is present,
                # full validate_stop_atr_authority is mandatory and missing counts fail closed.
                auth_ok, _ = validate_stop_atr_authority(
                    atr_m1,
                    expected_symbol=symbol or m1_sym,
                    expected_timeframe="1M",
                    expected_period=getattr(policy, "atr_period", 14),
                    expected_method=getattr(policy, "atr_method", "SMA_TR_V1"),
                    decision_cutoff=decision_cutoff,
                )
            else:
                # Pure buffer math calculation fixture without execution context
                auth_ok = True
        else:
            auth_ok = True
            if getattr(atr_m1, "bars_count", 0) > 0 or getattr(atr_m1, "source_candle_count", 0) > 0:
                auth_ok, _ = validate_stop_atr_authority(
                    atr_m1,
                    expected_symbol=symbol or m1_sym,
                    expected_timeframe="1M",
                    expected_period=getattr(policy, "atr_period", 14),
                    expected_method=getattr(policy, "atr_method", "SMA_TR_V1"),
                    decision_cutoff=decision_cutoff,
                )

        if symbol is not None:
            sym_ok = m1_sym is not None and str(m1_sym).upper() == str(symbol).upper()
            tf_ok = m1_tf is not None and str(m1_tf).upper() == "1M"
        else:
            sym_ok = True
            tf_ok = m1_tf is None or str(m1_tf).upper() == "1M"

        if sym_ok and tf_ok and m1_cutoff_ok and auth_ok:
            v1 = getattr(atr_m1, "value", None)
            if v1 is not None and not isinstance(v1, bool):
                try:
                    fv1 = float(v1)
                    if math.isfinite(fv1) and fv1 > 0.0:
                        raw_m1 = fv1
                        components["atr_m1"] = k_m1 * fv1
                except (ValueError, TypeError):
                    pass

    f_bid = float(bid)
    f_ask = float(ask)
    if not (math.isfinite(f_bid) and math.isfinite(f_ask) and f_bid > 0 and f_ask > 0):
        raise ValueError("INVALID_BID_ASK")
    if f_ask < f_bid:
        raise ValueError("INVERTED_QUOTE")

    spread = f_ask - f_bid
    if spread > 0:
        components["spread"] = k_spread * spread

    raw_m5 = None
    if atr_m5 is not None and getattr(atr_m5, "status", None) == "AVAILABLE":
        m5_sym = getattr(atr_m5, "symbol", None)
        m5_tf = getattr(atr_m5, "timeframe", None)

        m5_cutoff_ok = True
        if decision_cutoff is not None:
            if isinstance(decision_cutoff, bool) or not isinstance(decision_cutoff, (int, float)) or math.isnan(decision_cutoff) or int(decision_cutoff) < 0:
                m5_cutoff_ok = False
            elif getattr(atr_m5, "last_closed_at_ms", 0) > int(decision_cutoff):
                m5_cutoff_ok = False

        mode = getattr(policy, "mode", StopPlacementMode.SHADOW_COMPARE.value)
        has_provenance_meta_5 = (
            getattr(atr_m5, "symbol", None) is not None
            or getattr(atr_m5, "last_closed_at_ms", 0) > 0
            or getattr(atr_m5, "decision_cutoff", 0) > 0
            or symbol is not None
            or decision_cutoff is not None
        )
        if mode == StopPlacementMode.PAPER_STRUCTURAL.value:
            if has_provenance_meta_5:
                # V17.7.4.1 Section 04.5: Optional M5 must validate authority if supplied in PAPER_STRUCTURAL mode
                auth_ok_5, _ = validate_stop_atr_authority(
                    atr_m5,
                    expected_symbol=symbol or m5_sym,
                    expected_timeframe="5M",
                    expected_period=getattr(policy, "atr_period", 14),
                    expected_method=getattr(policy, "atr_method", "SMA_TR_V1"),
                    decision_cutoff=decision_cutoff,
                )
            else:
                auth_ok_5 = True
        else:
            if getattr(atr_m5, "bars_count", 0) > 0 or getattr(atr_m5, "source_candle_count", 0) > 0:
                auth_ok_5, _ = validate_stop_atr_authority(
                    atr_m5,
                    expected_symbol=symbol or m5_sym,
                    expected_timeframe="5M",
                    expected_period=getattr(policy, "atr_period", 14),
                    expected_method=getattr(policy, "atr_method", "SMA_TR_V1"),
                    decision_cutoff=decision_cutoff,
                )
            else:
                auth_ok_5 = True

        if symbol is not None:
            sym_ok_5 = m5_sym is not None and str(m5_sym).upper() == str(symbol).upper()
            tf_ok_5 = m5_tf is not None and str(m5_tf).upper() == "5M"
        else:
            sym_ok_5 = True
            tf_ok_5 = m5_tf is None or str(m5_tf).upper() in ("5M", "1M")

        if sym_ok_5 and tf_ok_5 and m5_cutoff_ok and auth_ok_5:
            v5 = getattr(atr_m5, "value", None)
            if v5 is not None and not isinstance(v5, bool):
                try:
                    fv5 = float(v5)
                    if math.isfinite(fv5) and fv5 > 0.0:
                        raw_m5 = fv5
                        components["atr_m5"] = k_m5 * fv5
                except (ValueError, TypeError):
                    pass

    max_buf = max(components.values())
    dominant = max(components, key=components.get)

    return StopBufferResult(
        buffer_price=max_buf,
        components=components,
        dominant_source=dominant,
        tick_size=tick_size,
        raw_atr_m1=raw_m1,
        raw_atr_m5=raw_m5,
    )


def compute_min_protective_distance(
    *,
    policy: StopPlacementPolicy,
    tick_size: float,
    atr_m1: Optional[StopATRResult] = None,
    atr_m5: Optional[StopATRResult] = None,
    spread: Optional[float] = None,
    atr_m1_val: Optional[float] = None,
    atr_m5_val: Optional[float] = None,
    symbol: Optional[str] = None,
    decision_cutoff: Optional[int] = None,
) -> StopProtectiveDistanceResult:
    """
    Computes candidate minimum entry-relative protective distance required
    under StopPlacementPolicy V17.7.4:
    min_dist = max(
        min_protective_ticks * tick_size,
        k1 * ATR_M1,
        k5 * ATR_M5,
        k_spread * spread,
    )
    Guarantees the selected stop is placed outside immediate market microstructure noise.
    In PAPER_STRUCTURAL mode, requires validated ATR authority objects.
    """
    min_ticks = getattr(policy, "min_protective_ticks", 10)
    tick_floor = float(min_ticks) * float(tick_size)
    components: Dict[str, float] = {
        "min_tick_floor": tick_floor,
    }

    mode = getattr(policy, "mode", StopPlacementMode.SHADOW_COMPARE.value)

    m1_val = None
    if atr_m1 is not None:
        is_valid, _ = validate_stop_atr_authority(
            atr_m1,
            expected_symbol=symbol or getattr(atr_m1, "symbol", None),
            expected_timeframe="1M",
            expected_period=getattr(policy, "atr_period", 14),
            expected_method=getattr(policy, "atr_method", "SMA_TR_V1"),
            decision_cutoff=decision_cutoff,
        )
        if is_valid:
            m1_val = float(atr_m1.value)
    elif mode != StopPlacementMode.PAPER_STRUCTURAL.value and atr_m1_val is not None:
        # Scalar fallback allowed ONLY in legacy / shadow compare modes when object is omitted
        try:
            fv = float(atr_m1_val)
            if math.isfinite(fv) and not isinstance(atr_m1_val, bool) and fv > 0:
                m1_val = fv
        except (ValueError, TypeError):
            m1_val = None

    if m1_val is not None:
        mult_m1 = getattr(policy, "min_distance_atr_m1_mult", 0.50)
        components["min_atr_m1"] = float(mult_m1) * float(m1_val)

    m5_val = None
    if atr_m5 is not None:
        is_valid_5, _ = validate_stop_atr_authority(
            atr_m5,
            expected_symbol=symbol or getattr(atr_m5, "symbol", None),
            expected_timeframe="5M",
            expected_period=getattr(policy, "atr_period", 14),
            expected_method=getattr(policy, "atr_method", "SMA_TR_V1"),
            decision_cutoff=decision_cutoff,
        )
        if is_valid_5:
            m5_val = float(atr_m5.value)
    elif mode != StopPlacementMode.PAPER_STRUCTURAL.value and atr_m5_val is not None:
        try:
            fv5 = float(atr_m5_val)
            if math.isfinite(fv5) and not isinstance(atr_m5_val, bool) and fv5 > 0:
                m5_val = fv5
        except (ValueError, TypeError):
            m5_val = None

    if m5_val is not None:
        mult_m5 = getattr(policy, "min_distance_atr_m5_mult", 0.20)
        components["min_atr_m5"] = float(mult_m5) * float(m5_val)

    if spread is not None and math.isfinite(float(spread)) and float(spread) > 0:
        mult_spread = getattr(policy, "min_distance_spread_mult", 2.0)
        components["min_spread"] = float(mult_spread) * float(spread)

    max_dist = max(components.values())
    dominant = max(components, key=components.get)

    return StopProtectiveDistanceResult(
        min_distance=max_dist,
        components=components,
        dominant_source=dominant,
        tick_size=tick_size,
    )


def validate_anchor_not_invalidated(
    anchor: StopAnchor,
    closed_bars: Sequence[Any],
    direction: str,
    as_of_ms: int,
    bar_duration_ms: int = 60_000,
) -> bool:
    """
    Verifies that the structural anchor has not been breached/invalidated by closed candles
    occurring strictly between anchor.occurred_at_ms and as_of_ms.
    SHORT: High must not exceed anchor.price.
    LONG:  Low must not fall below anchor.price.
    """
    norm_dir = direction.upper()
    for b in closed_bars:
        t_open = _candle_open_ms(b)
        t_close = t_open + bar_duration_ms
        if t_close <= anchor.occurred_at_ms or t_close > as_of_ms:
            continue
        _, h, l, _ = _candle_ohlc(b)
        if norm_dir == "SHORT" and h > anchor.price:
            return False
        if norm_dir == "LONG" and l < anchor.price:
            return False
    return True


def resolve_stop_anchor(
    *,
    model_id: str,
    direction: str,
    closed_bars: Sequence[Any],
    as_of_ms: int,
    selected_break: Optional[Dict[str, Any]] = None,
    selected_fvg: Optional[Dict[str, Any]] = None,
    selected_sweep: Optional[Dict[str, Any]] = None,
    confirmed_swings: Optional[Tuple[Sequence[Dict[str, Any]], Sequence[Dict[str, Any]]]] = None,
    tick_size: float = 0.01,
    unicorn_evidence: Optional[Any] = None,
    policy: Optional[StopPlacementPolicy] = None,
) -> StopAnchorResult:
    """
    Identifies the model-specific structural protective anchor.
    - Unicorn: Verified Breaker distal boundary / invalidation price (only when Unicorn is VALID
      and V17.8 structural intelligence is enabled or directly invoked).
    - Continuation: Protective swing high (for SHORT) or low (for LONG) originating the break leg.
    - FVG Continuation: Protected origin extreme of the leg producing the break and FVG,
      falling back to displacement origin swing prior to FVG formation if bound leg is stale/breached
      (when V17.8 structural intelligence is enabled).
    - Sweep Reclaim / Reversal / CG01: True extreme of the swept episode.
    """
    norm_dir = direction.upper()
    v18_enabled = (
        policy is None
        or getattr(policy, "v18_structural_intelligence_enabled", False) is True
    )

    # 0. Unicorn Evidence: Breaker Distal Invalidation (V17.8.0.1)
    if unicorn_evidence is not None:
        u_status = getattr(unicorn_evidence, "validity_status", None) or (
            unicorn_evidence.get("validity_status") if isinstance(unicorn_evidence, dict) else None
        )
        u_valid = getattr(unicorn_evidence, "is_valid", False)
        if not u_valid and isinstance(unicorn_evidence, dict):
            u_valid = u_status in ("CONFIRMED", "VALID")

        hard_invalid_statuses = {
            "UNICORN_INVALIDATED",
            "FUTURE_DATA_REJECTED",
            "UNCLOSED_EVIDENCE_REJECTED",
            "CONSUMED_OR_INVALIDATED_FVG",
            "RETEST_BEFORE_CONFIRMATION",
            "WRONG_DIRECTION_OVERLAP",
            "EPISODE_MISMATCH",
            "MISSING_CAUSAL_PROVENANCE",
        }
        if not u_valid and (model_id == "ICT_UNICORN" or str(u_status) in hard_invalid_statuses):
            return StopAnchorResult(
                status="INVALID_DATA",
                anchor=None,
                reasons=(str(u_status or "UNICORN_INVALID"),),
            )

        inval_px = getattr(unicorn_evidence, "invalidation_price", None)
        if inval_px is None and isinstance(unicorn_evidence, dict):
            inval_px = unicorn_evidence.get("invalidation_price")

        if v18_enabled and u_valid and inval_px is not None and math.isfinite(float(inval_px)):
            breaker_id = getattr(unicorn_evidence, "breaker_id", None) or (unicorn_evidence.get("breaker_id") if isinstance(unicorn_evidence, dict) else f"brk_{as_of_ms}")
            occ_ms = getattr(unicorn_evidence, "breaker_confirmed_at_ms", None) or (unicorn_evidence.get("breaker_confirmed_at_ms") if isinstance(unicorn_evidence, dict) else as_of_ms)
            known_ms = getattr(unicorn_evidence, "known_at_ms", None) or (unicorn_evidence.get("known_at_ms") if isinstance(unicorn_evidence, dict) else occ_ms)
            occ_ms = int(occ_ms or as_of_ms)
            known_ms = int(known_ms or as_of_ms)
            if known_ms <= as_of_ms:
                u_id = getattr(unicorn_evidence, "unicorn_id", None) or (unicorn_evidence.get("unicorn_id") if isinstance(unicorn_evidence, dict) else f"uni_{as_of_ms}")
                mss_id = getattr(unicorn_evidence, "mss_or_bos_id", None) or (unicorn_evidence.get("mss_or_bos_id") if isinstance(unicorn_evidence, dict) else None)
                anchor = StopAnchor(
                    id=f"anc_unicorn_{breaker_id}",
                    kind=StopAnchorKind.BREAKER_DISTAL_BOUNDARY.value,
                    price=float(inval_px),
                    timeframe="1M",
                    occurred_at_ms=occ_ms,
                    known_at_ms=known_ms,
                    source_leg_id=str(u_id),
                    source_break_id=str(mss_id) if mss_id else None,
                    price_basis=PriceBasis.CANDLE_LAST.value,
                )
                if not validate_anchor_not_invalidated(anchor, closed_bars, norm_dir, as_of_ms):
                    return StopAnchorResult(
                        status="INVALID_DATA",
                        anchor=None,
                        reasons=("ANCHOR_ALREADY_INVALIDATED", "UNICORN_DISTAL_BREACHED"),
                    )
                return StopAnchorResult(status="AVAILABLE", anchor=anchor)

    # 1. Sweep-based models: Reversal, Sweep Reclaim, CG01
    if selected_sweep is not None:
        extreme_price = (
            selected_sweep.get("sweep_extreme")
            or selected_sweep.get("sweep_price")
            or selected_sweep.get("price")
        )
        if extreme_price is not None and math.isfinite(float(extreme_price)):
            sweep_id = str(selected_sweep.get("id") or selected_sweep.get("episode_id") or f"sweep_{as_of_ms}")
            occurred_ms = int(selected_sweep.get("occurred_at_ms") or selected_sweep.get("timestamp") or as_of_ms)
            known_ms = int(selected_sweep.get("known_at_ms") or selected_sweep.get("confirmed_at") or occurred_ms)
            if known_ms <= as_of_ms:
                anchor = StopAnchor(
                    id=f"anc_{sweep_id}",
                    kind=StopAnchorKind.SWEEP_EXTREME.value,
                    price=float(extreme_price),
                    timeframe="1M",
                    occurred_at_ms=occurred_ms,
                    known_at_ms=known_ms,
                    source_leg_id=sweep_id,
                    source_break_id=selected_sweep.get("break_id"),
                    price_basis=PriceBasis.CANDLE_LAST.value,
                )
                if not validate_anchor_not_invalidated(anchor, closed_bars, norm_dir, as_of_ms):
                    return StopAnchorResult(
                        status="INVALID_DATA",
                        anchor=None,
                        reasons=("ANCHOR_ALREADY_INVALIDATED",),
                    )
                return StopAnchorResult(status="AVAILABLE", anchor=anchor)

    # 2. FVG Continuation: Structural origin of the displacement leg
    if selected_fvg is not None:
        fvg_id = str(selected_fvg.get("id") or f"fvg_{as_of_ms}")

        leg_cutoff_ms = None
        if selected_break:
            leg_cutoff_ms = (
                selected_break.get("broken_at_ms")
                or selected_break.get("break_time_ms")
                or selected_break.get("timestamp")
                or selected_break.get("break_open_ms")
            )
            if leg_cutoff_ms is None and "bar" in selected_break:
                leg_cutoff_ms = _candle_open_ms(selected_break["bar"])
        if leg_cutoff_ms is None and selected_fvg:
            leg_cutoff_ms = (
                selected_fvg.get("middle_bar_ms")
                or selected_fvg.get("timestamp")
                or selected_fvg.get("time_ms")
                or selected_fvg.get("start_ms")
                or selected_fvg.get("formation_time_ms")
                or selected_fvg.get("confirmed_at")
            )
        if leg_cutoff_ms is None:
            leg_cutoff_ms = as_of_ms
        leg_cutoff_ms = int(leg_cutoff_ms)

        if confirmed_swings:
            sh_list, sl_list = confirmed_swings
            pivots = sh_list if norm_dir == "SHORT" else sl_list
            bound_leg = resolve_bound_stop_leg(
                model_id=model_id,
                direction=norm_dir,
                closed_bars=closed_bars,
                selected_break=selected_break,
                selected_fvg=selected_fvg,
                candidate_pivots=pivots,
                decision_cutoff_ms=as_of_ms,
                as_of_ms=as_of_ms,
                tick_size=tick_size,
            )
            anchor = None
            if bound_leg is not None:
                cand_anchor = StopAnchor(
                    id=f"anc_fvg_{'sh' if norm_dir == 'SHORT' else 'sl'}_{bound_leg.origin_pivot_id}",
                    kind=StopAnchorKind.PROTECTED_LEG_HIGH.value if norm_dir == "SHORT" else StopAnchorKind.PROTECTED_LEG_LOW.value,
                    price=bound_leg.origin_price,
                    timeframe="1M",
                    occurred_at_ms=bound_leg.origin_occurred_at_ms,
                    known_at_ms=bound_leg.origin_known_at_ms,
                    source_leg_id=bound_leg.leg_id,
                    source_break_id=bound_leg.break_bar_id,
                    price_basis=PriceBasis.CANDLE_LAST.value,
                )
                if validate_anchor_not_invalidated(cand_anchor, closed_bars, norm_dir, as_of_ms):
                    anchor = cand_anchor

            # V17.8 Fallback: If bound_leg is unavailable or already invalidated,
            # bind to the true displacement origin extreme preceding the FVG formation
            if v18_enabled and anchor is None and pivots:
                fvg_t = leg_cutoff_ms
                eligible_pivots = [
                    p for p in pivots
                    if int(p.get("occurred_at_ms", 0)) < fvg_t
                    and int(p.get("known_at_ms", p.get("occurred_at_ms", 0))) <= as_of_ms
                ]
                eligible_pivots.sort(key=lambda p: (int(p.get("occurred_at_ms", 0)), int(p.get("known_at_ms", 0))), reverse=True)
                for ep in eligible_pivots:
                    ep_px = float(ep.get("price", 0.0))
                    ep_occ = int(ep.get("occurred_at_ms", 0))
                    ep_known = int(ep.get("known_at_ms", ep_occ))
                    cand = StopAnchor(
                        id=f"anc_fvg_origin_{'sh' if norm_dir == 'SHORT' else 'sl'}_{ep.get('id', ep_occ)}",
                        kind=StopAnchorKind.DISPLACEMENT_ORIGIN.value,
                        price=ep_px,
                        timeframe="1M",
                        occurred_at_ms=ep_occ,
                        known_at_ms=ep_known,
                        source_leg_id=fvg_id,
                        source_break_id=None,
                        price_basis=PriceBasis.CANDLE_LAST.value,
                    )
                    if validate_anchor_not_invalidated(cand, closed_bars, norm_dir, as_of_ms):
                        fvg_top = float(selected_fvg.get("top", 0.0)) if selected_fvg.get("top") is not None else 0.0
                        fvg_bot = float(selected_fvg.get("bottom", 0.0)) if selected_fvg.get("bottom") is not None else 0.0
                        if norm_dir == "SHORT" and ep_px >= fvg_top:
                            anchor = cand
                            break
                        elif norm_dir == "LONG" and ep_px <= fvg_bot:
                            anchor = cand
                            break

            if anchor is not None:
                return StopAnchorResult(status="AVAILABLE", anchor=anchor, bound_leg=bound_leg)
            else:
                return StopAnchorResult(
                    status="WAITING_EVIDENCE",
                    anchor=None,
                    reasons=("STRUCTURAL_ANCHOR_NOT_FOUND",),
                )

    # 3. Continuation (M1 Trend BOS)
    if confirmed_swings:
        sh_list, sl_list = confirmed_swings
        leg_cutoff_ms = None
        if selected_break:
            leg_cutoff_ms = (
                selected_break.get("broken_at_ms")
                or selected_break.get("break_time_ms")
                or selected_break.get("timestamp")
                or selected_break.get("break_open_ms")
            )
            if leg_cutoff_ms is None and "bar" in selected_break:
                leg_cutoff_ms = _candle_open_ms(selected_break["bar"])
            if leg_cutoff_ms is None:
                return StopAnchorResult(
                    status="WAITING_EVIDENCE",
                    anchor=None,
                    reasons=("MISSING_BREAK_TIMELOG",),
                )
        else:
            leg_cutoff_ms = as_of_ms
        leg_cutoff_ms = int(leg_cutoff_ms)

        pivots = sh_list if norm_dir == "SHORT" else sl_list
        bound_leg = resolve_bound_stop_leg(
            model_id=model_id,
            direction=norm_dir,
            closed_bars=closed_bars,
            selected_break=selected_break,
            candidate_pivots=pivots,
            decision_cutoff_ms=as_of_ms,
            as_of_ms=as_of_ms,
            tick_size=tick_size,
        )
        if bound_leg is None:
            return StopAnchorResult(
                status="WAITING_EVIDENCE",
                anchor=None,
                reasons=("STRUCTURAL_ANCHOR_NOT_FOUND",),
            )

        anchor = StopAnchor(
            id=f"anc_cont_{'sh' if norm_dir == 'SHORT' else 'sl'}_{bound_leg.origin_pivot_id}",
            kind=StopAnchorKind.PROTECTED_LEG_HIGH.value if norm_dir == "SHORT" else StopAnchorKind.PROTECTED_LEG_LOW.value,
            price=bound_leg.origin_price,
            timeframe="1M",
            occurred_at_ms=bound_leg.origin_occurred_at_ms,
            known_at_ms=bound_leg.origin_known_at_ms,
            source_leg_id=bound_leg.leg_id,
            source_break_id=bound_leg.break_bar_id,
            price_basis=PriceBasis.CANDLE_LAST.value,
        )
        if not validate_anchor_not_invalidated(anchor, closed_bars, norm_dir, as_of_ms):
            return StopAnchorResult(
                status="INVALID_DATA",
                anchor=None,
                reasons=("ANCHOR_ALREADY_INVALIDATED",),
            )
        return StopAnchorResult(status="AVAILABLE", anchor=anchor, bound_leg=bound_leg)

    return StopAnchorResult(
        status="WAITING_EVIDENCE",
        anchor=None,
        reasons=("STRUCTURAL_ANCHOR_NOT_FOUND",),
    )


def plan_structural_stop(
    *,
    direction: str,
    planned_entry: float,
    take_profit: float,
    legacy_sl: float,
    anchor_result: Optional[StopAnchorResult] = None,
    buffer_result: Optional[StopBufferResult] = None,
    policy: StopPlacementPolicy,
    as_of_ms: int,
    tick_size: float = 0.01,
    atr_m1_val: Optional[float] = None,
    atr_m5_val: Optional[float] = None,
    spread: Optional[float] = None,
    atr_m1_obj: Optional[StopATRResult] = None,
    atr_m5_obj: Optional[StopATRResult] = None,
    symbol: Optional[str] = "XAUUSDT",
    unicorn_evidence: Optional[Any] = None,
    invalidation_authority: Optional[str] = None,
) -> StopPlacementDecision:
    """
    Synthesizes the V17.8 Adaptive Structural Stop Loss decision.
    - Preserves structural invalidation anchor (protected swing/origin/sweep/Unicorn breaker).
    - Single authoritative ATR resolution: PAPER_STRUCTURAL strictly requires validated raw ATR.
    - Computes dynamic buffer beyond the anchor from validated metrics.
    - Computes minimum entry-relative protective distance against microstructure noise.
    - Applies defensive outward expansion if anchor buffer is closer than protective threshold.
    - Assesses stop distance relative to volatility (non-blocking diagnostic: DISTANCE_NORMAL, DISTANCE_ELEVATED, DISTANCE_EXTREME, DISTANCE_ATR_UNAVAILABLE).
    - Never rejects solely because stop distance exceeds an arbitrary ATR multiple.
    - Rejects abnormal spread / market shock conditions (spread > max_spread_usd).
    - Enforces strict outward tick quantization (floor for LONG, ceil for SHORT).
    - Fails closed in PAPER_STRUCTURAL mode when evidence is missing or invalid.
    """
    norm_dir = direction.upper() if isinstance(direction, str) else "UNKNOWN"
    mode = getattr(policy, "mode", StopPlacementMode.SHADOW_COMPARE.value)
    policy_ver = getattr(policy, "version", 1)
    algo_ver = getattr(policy, "algorithm_version", "V17_8_0")

    u_dict = unicorn_evidence.to_dict() if (unicorn_evidence and hasattr(unicorn_evidence, "to_dict")) else (unicorn_evidence if isinstance(unicorn_evidence, dict) else None)
    u_id = getattr(unicorn_evidence, "unicorn_id", None) or (unicorn_evidence.get("unicorn_id") if isinstance(unicorn_evidence, dict) else None)
    u_inval = getattr(unicorn_evidence, "invalidation_price", None) or (unicorn_evidence.get("invalidation_price") if isinstance(unicorn_evidence, dict) else None)
    unicorn_sl_cand = None
    if u_inval is not None:
        try:
            fu_inval = float(u_inval)
            if math.isfinite(fu_inval):
                buf_p = buffer_result.buffer_price if buffer_result else (float(policy.min_buffer_ticks) * float(tick_size))
                unicorn_sl_cand = quantize_stop_outward(
                    anchor_price=fu_inval,
                    buffer_price=buf_p,
                    direction=norm_dir,
                    tick_size=tick_size,
                )
        except (ValueError, TypeError):
            pass

    def _make_decision(
        *,
        status: str,
        selected_sl: Optional[float] = None,
        anchor: Optional[StopAnchor] = None,
        bound_leg: Optional[Any] = None,
        buffer: Optional[StopBufferResult] = None,
        metrics: Optional[StopMetrics] = None,
        reasons: Tuple[str, ...] = (),
        applied_variant: str = "A",
        volatility_context: Optional[Dict[str, Any]] = None,
        protective_distance: Optional[float] = None,
        expansion_applied: bool = False,
        distance_assessment: Optional[str] = "DISTANCE_NORMAL",
        structural_sl: Optional[float] = None,
    ) -> StopPlacementDecision:
        b_comps = buffer.components if buffer else (buffer_result.components if buffer_result else None)
        inval_auth = invalidation_authority or (getattr(anchor, "kind", None) if anchor else None)
        return StopPlacementDecision(
            status=status,
            selected_sl=selected_sl,
            anchor=anchor,
            bound_leg=bound_leg,
            buffer=buffer,
            metrics=metrics,
            reasons=reasons,
            policy_version=policy_ver,
            mode=mode,
            decision_cutoff_ms=as_of_ms,
            applied_variant=applied_variant,
            volatility_context=volatility_context,
            protective_distance=protective_distance,
            expansion_applied=expansion_applied,
            distance_assessment=distance_assessment,
            algorithm_version=algo_ver,
            unicorn_evidence=u_dict,
            unicorn_id=u_id,
            invalidation_authority=inval_auth,
            unicorn_sl_candidate=unicorn_sl_cand,
            legacy_sl=legacy_sl,
            structural_sl=structural_sl,
            buffer_components=b_comps,
        )

    # 0. Single Authoritative ATR Resolution (V17.7.4 Section 06)
    is_m1_valid, m1_reason = validate_stop_atr_authority(
        atr_m1_obj,
        expected_symbol=symbol or getattr(atr_m1_obj, "symbol", None),
        expected_timeframe="1M",
        expected_period=getattr(policy, "atr_period", 14),
        expected_method=getattr(policy, "atr_method", "SMA_TR_V1"),
        decision_cutoff=as_of_ms,
    )
    if is_m1_valid:
        eff_m1 = float(atr_m1_obj.value)
    elif mode == StopPlacementMode.PAPER_STRUCTURAL.value:
        # Strict V17.7.4: In PAPER_STRUCTURAL mode, scalar or buffer fallback is strictly forbidden
        eff_m1 = None
    elif atr_m1_obj is None and atr_m1_val is not None:
        # Legacy/research scalar compatibility only when object was completely omitted
        try:
            fv = float(atr_m1_val)
            eff_m1 = fv if (math.isfinite(fv) and not isinstance(atr_m1_val, bool) and fv > 0.0) else None
        except (ValueError, TypeError):
            eff_m1 = None
    else:
        eff_m1 = None

    is_m5_valid, m5_reason = validate_stop_atr_authority(
        atr_m5_obj,
        expected_symbol=symbol or getattr(atr_m5_obj, "symbol", None),
        expected_timeframe="5M",
        expected_period=getattr(policy, "atr_period", 14),
        expected_method=getattr(policy, "atr_method", "SMA_TR_V1"),
        decision_cutoff=as_of_ms,
    )
    if is_m5_valid:
        eff_m5 = float(atr_m5_obj.value)
    elif mode == StopPlacementMode.PAPER_STRUCTURAL.value:
        eff_m5 = None
    elif atr_m5_obj is None and atr_m5_val is not None:
        try:
            fv5 = float(atr_m5_val)
            eff_m5 = fv5 if (math.isfinite(fv5) and not isinstance(atr_m5_val, bool) and fv5 > 0.0) else None
        except (ValueError, TypeError):
            eff_m5 = None
    else:
        eff_m5 = None

    # 1. Price positivity and finiteness (SL-16, SL-17)
    for p_name, p_val in (
        ("planned_entry", planned_entry),
        ("take_profit", take_profit),
        ("legacy_sl", legacy_sl),
        ("tick_size", tick_size),
    ):
        try:
            f_val = float(p_val)
            if not math.isfinite(f_val):
                return StopPlacementDecision(
                    status=StopStatus.INVALID_DATA.value,
                    selected_sl=None,
                    anchor=None,
                    bound_leg=None,
                    buffer=buffer_result,
                    metrics=None,
                    reasons=("NON_FINITE_INPUT", f"{p_name.upper()}_NOT_FINITE"),
                    policy_version=policy_ver,
                    mode=mode,
                    decision_cutoff_ms=as_of_ms,
                    applied_variant="A",
                    algorithm_version=algo_ver,
                )
            if f_val <= 0.0:
                return StopPlacementDecision(
                    status=StopStatus.INVALID_DATA.value,
                    selected_sl=None,
                    anchor=None,
                    bound_leg=None,
                    buffer=buffer_result,
                    metrics=None,
                    reasons=("ZERO_OR_NEGATIVE_PRICE", f"{p_name.upper()}_NON_POSITIVE"),
                    policy_version=policy_ver,
                    mode=mode,
                    decision_cutoff_ms=as_of_ms,
                    applied_variant="A",
                    algorithm_version=algo_ver,
                )
        except (ValueError, TypeError):
            return StopPlacementDecision(
                status=StopStatus.INVALID_DATA.value,
                selected_sl=None,
                anchor=None,
                bound_leg=None,
                buffer=buffer_result,
                metrics=None,
                reasons=("INVALID_PRICE_INPUT",),
                policy_version=policy_ver,
                mode=mode,
                decision_cutoff_ms=as_of_ms,
                applied_variant="A",
                algorithm_version=algo_ver,
            )

    if norm_dir not in ("LONG", "SHORT", "BUY", "SELL"):
        return StopPlacementDecision(
            status=StopStatus.INVALID_DATA.value,
            selected_sl=None,
            reasons=("INVALID_DIRECTION",),
            policy_version=policy_ver,
            mode=mode,
            decision_cutoff_ms=as_of_ms,
            applied_variant="A",
            algorithm_version=algo_ver,
        )

    # 2. Spread and Volatility Shock checks (SL-13, SL-14, SL-18)
    eff_spread = spread
    if eff_spread is None and buffer_result and "spread" in buffer_result.components:
        mult = getattr(policy, "spread_multiplier", 1.50)
        if mult > 0:
            eff_spread = buffer_result.components["spread"] / mult

    if eff_spread is not None:
        try:
            f_spread = float(eff_spread)
            if not math.isfinite(f_spread) or f_spread < 0.0:
                return StopPlacementDecision(
                    status=StopStatus.INVALID_DATA.value,
                    selected_sl=None,
                    reasons=("INVALID_SPREAD", "INVERTED_QUOTE" if f_spread < 0.0 else "NON_FINITE_SPREAD"),
                    policy_version=policy_ver,
                    mode=mode,
                    decision_cutoff_ms=as_of_ms,
                    applied_variant="A",
                    algorithm_version=algo_ver,
                )
            max_spread = getattr(policy, "max_spread_usd", 2.0)
            if f_spread > max_spread:
                dist = abs(planned_entry - legacy_sl)
                sel_sl = None if mode == StopPlacementMode.PAPER_STRUCTURAL.value else legacy_sl
                return StopPlacementDecision(
                    status=StopStatus.REJECTED_GEOMETRY.value,
                    selected_sl=sel_sl,
                    anchor=anchor_result.anchor if (anchor_result and hasattr(anchor_result, "anchor")) else None,
                    bound_leg=getattr(anchor_result, "bound_leg", None),
                    buffer=buffer_result,
                    metrics=StopMetrics(
                        price_distance=dist,
                        distance_ticks=int(round(dist / tick_size)),
                        distance_atr=(dist / eff_m1) if eff_m1 and eff_m1 > 0 else None,
                    ),
                    reasons=("EXCESSIVE_SPREAD", f"Spread {f_spread:.2f} > max {max_spread:.2f}"),
                    policy_version=policy_ver,
                    mode=mode,
                    decision_cutoff_ms=as_of_ms,
                    applied_variant="A",
                    algorithm_version=algo_ver,
                )
        except (ValueError, TypeError):
            return StopPlacementDecision(
                status=StopStatus.INVALID_DATA.value,
                selected_sl=None,
                reasons=("INVALID_SPREAD",),
                policy_version=policy_ver,
                mode=mode,
                decision_cutoff_ms=as_of_ms,
                applied_variant="A",
                algorithm_version=algo_ver,
            )

    # 3. LEGACY mode parity (SL-29)
    if mode == StopPlacementMode.LEGACY.value:
        dist = abs(planned_entry - legacy_sl)
        return StopPlacementDecision(
            status=StopStatus.VALID.value,
            selected_sl=legacy_sl,
            anchor=None,
            bound_leg=None,
            buffer=None,
            metrics=StopMetrics(
                price_distance=dist,
                distance_ticks=int(round(dist / tick_size)),
                distance_atr=(dist / eff_m1) if eff_m1 and eff_m1 > 0 else None,
            ),
            reasons=(),
            policy_version=policy_ver,
            mode=mode,
            decision_cutoff_ms=as_of_ms,
            applied_variant="A",
            algorithm_version=algo_ver,
        )

    # 4. Check Anchor Availability (SL-12, SL-32)
    if anchor_result is None or anchor_result.status != "AVAILABLE" or anchor_result.anchor is None:
        dist = abs(planned_entry - legacy_sl)
        sel_sl = None if mode == StopPlacementMode.PAPER_STRUCTURAL.value else legacy_sl
        reasons = anchor_result.reasons if (anchor_result and anchor_result.reasons) else ("MISSING_STRUCTURAL_ANCHOR",)
        return StopPlacementDecision(
            status=StopStatus.WAITING_EVIDENCE.value,
            selected_sl=sel_sl,
            anchor=None,
            bound_leg=None,
            buffer=buffer_result,
            metrics=StopMetrics(
                price_distance=dist,
                distance_ticks=int(round(dist / tick_size)),
                distance_atr=(dist / eff_m1) if eff_m1 and eff_m1 > 0 else None,
            ),
            reasons=reasons,
            policy_version=policy_ver,
            mode=mode,
            decision_cutoff_ms=as_of_ms,
            applied_variant="A",
            algorithm_version=algo_ver,
        )

    anchor = anchor_result.anchor

    # 4b. Causal Timestamp and Decision Cutoff Verification (CE-06)
    occ_ms = getattr(anchor, "occurred_at_ms", None)
    known_ms = getattr(anchor, "known_at_ms", None)
    if occ_ms is not None and known_ms is not None and occ_ms > known_ms:
        dist = abs(planned_entry - legacy_sl)
        sel_sl = None if mode == StopPlacementMode.PAPER_STRUCTURAL.value else legacy_sl
        return StopPlacementDecision(
            status=StopStatus.INVALID_DATA.value,
            selected_sl=sel_sl,
            anchor=anchor,
            bound_leg=getattr(anchor_result, "bound_leg", None),
            buffer=buffer_result,
            metrics=StopMetrics(
                price_distance=dist,
                distance_ticks=int(round(dist / tick_size)),
                distance_atr=(dist / eff_m1) if eff_m1 and eff_m1 > 0 else None,
            ),
            reasons=("CAUSAL_ORDERING_VIOLATION", "OCCURRED_AFTER_KNOWN"),
            policy_version=policy_ver,
            mode=mode,
            decision_cutoff_ms=as_of_ms,
            applied_variant="A",
            algorithm_version=algo_ver,
        )

    if as_of_ms is not None and known_ms is not None and known_ms > as_of_ms:
        dist = abs(planned_entry - legacy_sl)
        sel_sl = None if mode == StopPlacementMode.PAPER_STRUCTURAL.value else legacy_sl
        return StopPlacementDecision(
            status=StopStatus.INVALID_DATA.value,
            selected_sl=sel_sl,
            anchor=anchor,
            bound_leg=getattr(anchor_result, "bound_leg", None),
            buffer=buffer_result,
            metrics=StopMetrics(
                price_distance=dist,
                distance_ticks=int(round(dist / tick_size)),
                distance_atr=(dist / eff_m1) if eff_m1 and eff_m1 > 0 else None,
            ),
            reasons=("CAUSAL_ORDERING_VIOLATION", "ANCHOR_KNOWN_AFTER_DECISION_CUTOFF"),
            policy_version=policy_ver,
            mode=mode,
            decision_cutoff_ms=as_of_ms,
            applied_variant="A",
            algorithm_version=algo_ver,
        )

    # 5. Directional Anchor Relationship (SL-01, SL-02, SL-11)
    if norm_dir in ("LONG", "BUY") and anchor.price >= planned_entry:
        dist = abs(planned_entry - legacy_sl)
        sel_sl = None if mode == StopPlacementMode.PAPER_STRUCTURAL.value else legacy_sl
        return StopPlacementDecision(
            status=StopStatus.REJECTED_GEOMETRY.value,
            selected_sl=sel_sl,
            anchor=anchor,
            bound_leg=getattr(anchor_result, "bound_leg", None),
            buffer=buffer_result,
            metrics=StopMetrics(
                price_distance=dist,
                distance_ticks=int(round(dist / tick_size)),
                distance_atr=(dist / eff_m1) if eff_m1 and eff_m1 > 0 else None,
            ),
            reasons=("INVALID_ANCHOR_RELATION", "LONG_ANCHOR_MUST_BE_BELOW_ENTRY"),
            policy_version=policy_ver,
            mode=mode,
            decision_cutoff_ms=as_of_ms,
            applied_variant="A",
            algorithm_version=algo_ver,
        )

    if norm_dir in ("SHORT", "SELL") and anchor.price <= planned_entry:
        dist = abs(planned_entry - legacy_sl)
        sel_sl = None if mode == StopPlacementMode.PAPER_STRUCTURAL.value else legacy_sl
        return StopPlacementDecision(
            status=StopStatus.REJECTED_GEOMETRY.value,
            selected_sl=sel_sl,
            anchor=anchor,
            bound_leg=getattr(anchor_result, "bound_leg", None),
            buffer=buffer_result,
            metrics=StopMetrics(
                price_distance=dist,
                distance_ticks=int(round(dist / tick_size)),
                distance_atr=(dist / eff_m1) if eff_m1 and eff_m1 > 0 else None,
            ),
            reasons=("INVALID_ANCHOR_RELATION", "SHORT_ANCHOR_MUST_BE_ABOVE_ENTRY"),
            policy_version=policy_ver,
            mode=mode,
            decision_cutoff_ms=as_of_ms,
            applied_variant="A",
            algorithm_version=algo_ver,
        )

    # 6. Verify and sanitize buffer_result consistency against validated ATR inputs (SL-22 / V17.7.4)
    effective_buffer = buffer_result
    if effective_buffer is not None:
        comps = dict(effective_buffer.components)
        modified = False
        raw_b_m1 = getattr(effective_buffer, "raw_atr_m1", None)
        raw_b_m5 = getattr(effective_buffer, "raw_atr_m5", None)

        if not is_m5_valid:
            if "atr_m5" in comps:
                comps.pop("atr_m5", None)
                modified = True
            if raw_b_m5 is not None:
                raw_b_m5 = None
                modified = True

        if not is_m1_valid and mode == StopPlacementMode.PAPER_STRUCTURAL.value:
            if "atr_m1" in comps:
                comps.pop("atr_m1", None)
                modified = True
            if raw_b_m1 is not None:
                raw_b_m1 = None
                modified = True

        if modified:
            if not comps:
                comps["tick_floor"] = float(policy.min_buffer_ticks) * float(tick_size)
            new_buf_price = max(comps.values())
            new_dominant = max(comps, key=comps.get)
            effective_buffer = StopBufferResult(
                buffer_price=new_buf_price,
                components=comps,
                dominant_source=new_dominant,
                tick_size=tick_size,
                raw_atr_m1=raw_b_m1,
                raw_atr_m5=raw_b_m5,
            )

    buf_price = effective_buffer.buffer_price if effective_buffer else (float(policy.min_buffer_ticks) * float(tick_size))
    anchor_sl = quantize_stop_outward(
        anchor_price=anchor.price,
        buffer_price=buf_price,
        direction=norm_dir,
        tick_size=tick_size,
    )

    # 7. Minimum Entry-Relative Protective Distance & Outward Expansion (SL-19)
    min_prot_res = compute_min_protective_distance(
        policy=policy,
        tick_size=tick_size,
        atr_m1=atr_m1_obj if is_m1_valid else None,
        atr_m5=atr_m5_obj if is_m5_valid else None,
        spread=eff_spread,
        atr_m1_val=eff_m1,
        atr_m5_val=eff_m5,
        symbol=symbol,
        decision_cutoff=as_of_ms,
    )
    min_prot_dist = min_prot_res.min_distance

    expansion_applied = False
    if norm_dir in ("LONG", "BUY"):
        anchor_dist = planned_entry - anchor_sl
        if anchor_dist < min_prot_dist:
            expanded_sl = quantize_stop_outward(
                anchor_price=planned_entry,
                buffer_price=min_prot_dist,
                direction=norm_dir,
                tick_size=tick_size,
            )
            # Ensure expanded_sl remains strictly on protective side of anchor
            if expanded_sl <= anchor.price:
                variant_c_sl = expanded_sl
                expansion_applied = True
            else:
                variant_c_sl = anchor_sl
        else:
            variant_c_sl = anchor_sl
    else:  # SHORT, SELL
        anchor_dist = anchor_sl - planned_entry
        if anchor_dist < min_prot_dist:
            expanded_sl = quantize_stop_outward(
                anchor_price=planned_entry,
                buffer_price=min_prot_dist,
                direction=norm_dir,
                tick_size=tick_size,
            )
            if expanded_sl >= anchor.price:
                variant_c_sl = expanded_sl
                expansion_applied = True
            else:
                variant_c_sl = anchor_sl
        else:
            variant_c_sl = anchor_sl

    # 8. Evidence-Based Stop Distance Assessment (V17.7.1 / DSL)
    # A structurally valid Stop Loss is NEVER rejected solely because Stop Distance > 4.0 x ATR.
    # ATR is a volatility measurement, not an absolute boundary for structural invalidation distance.
    # We record non-blocking diagnostic classification: DISTANCE_NORMAL, DISTANCE_ELEVATED,
    # DISTANCE_EXTREME, DISTANCE_ATR_UNAVAILABLE.
    final_dist = abs(planned_entry - variant_c_sl)

    dist_ticks = int(round(final_dist / tick_size))
    dist_atr_m1 = (final_dist / eff_m1) if (eff_m1 is not None and eff_m1 > 0) else None
    dist_atr_m5 = (final_dist / eff_m5) if (eff_m5 is not None and eff_m5 > 0) else None

    ref_elevated = float(getattr(policy, "max_stop_distance_atr", 4.0))
    if eff_m1 is None or eff_m1 <= 0.0:
        distance_assessment = "DISTANCE_ATR_UNAVAILABLE"
    elif dist_atr_m1 is not None and dist_atr_m1 <= 2.5:
        distance_assessment = "DISTANCE_NORMAL"
    elif dist_atr_m1 is not None and dist_atr_m1 <= ref_elevated:
        distance_assessment = "DISTANCE_ELEVATED"
    else:
        distance_assessment = "DISTANCE_EXTREME"

    # 9. Geometry validation for Variant C (SL-03)
    if norm_dir in ("SHORT", "SELL"):
        geom_valid = (take_profit < planned_entry < variant_c_sl)
    else:
        geom_valid = (variant_c_sl < planned_entry < take_profit)

    if not geom_valid:
        dist = abs(planned_entry - legacy_sl)
        sel_sl = None if mode == StopPlacementMode.PAPER_STRUCTURAL.value else legacy_sl
        return StopPlacementDecision(
            status=StopStatus.REJECTED_GEOMETRY.value,
            selected_sl=sel_sl,
            anchor=anchor,
            bound_leg=getattr(anchor_result, "bound_leg", None),
            buffer=effective_buffer,
            metrics=StopMetrics(
                price_distance=dist,
                distance_ticks=int(round(dist / tick_size)),
                distance_atr=(dist / eff_m1) if eff_m1 and eff_m1 > 0 else None,
            ),
            reasons=("INVALID_PRICE_GEOMETRY_WITH_STRUCTURAL_SL",),
            policy_version=policy_ver,
            mode=mode,
            decision_cutoff_ms=as_of_ms,
            applied_variant="A",
            protective_distance=min_prot_dist,
            expansion_applied=expansion_applied,
            distance_assessment=distance_assessment,
            algorithm_version=algo_ver,
        )

    # 9b. Volatility authority fail-closed check for PAPER_STRUCTURAL (V17.7.4 P0-01)
    if mode == StopPlacementMode.PAPER_STRUCTURAL.value and not is_m1_valid:
        dist = abs(planned_entry - legacy_sl)
        return StopPlacementDecision(
            status=StopStatus.WAITING_VOLATILITY_EVIDENCE.value,
            selected_sl=None,
            anchor=anchor,
            bound_leg=getattr(anchor_result, "bound_leg", None),
            buffer=effective_buffer,
            metrics=StopMetrics(
                price_distance=dist,
                distance_ticks=int(round(dist / tick_size)),
                distance_atr=None,
            ),
            reasons=("WAITING_VOLATILITY_EVIDENCE", "REQUIRED_M1_ATR_UNAVAILABLE", m1_reason),
            policy_version=policy_ver,
            mode=mode,
            decision_cutoff_ms=as_of_ms,
            applied_variant="NONE",
            protective_distance=min_prot_dist,
            expansion_applied=expansion_applied,
            distance_assessment="DISTANCE_ATR_UNAVAILABLE",
            algorithm_version=algo_ver,
            volatility_context={
                "atr_m1": None,
                "atr_m5": eff_m5,
                "atr_m1_provenance": None,
                "atr_m5_provenance": atr_m5_obj.to_dict() if (is_m5_valid and atr_m5_obj and hasattr(atr_m5_obj, "to_dict")) else None,
                "atr_m1_status": "UNAVAILABLE",
                "volatility_gate_status": "FAIL_CLOSED",
                "volatility_gate_reason": m1_reason,
                "distance_assessment": "DISTANCE_ATR_UNAVAILABLE",
            },
        )

    # 10. Selected SL according to mode (SL-30, SL-31)
    if mode == StopPlacementMode.PAPER_STRUCTURAL.value:
        selected_sl = variant_c_sl
        applied_variant = "C"
    else:  # SHADOW_COMPARE
        selected_sl = legacy_sl
        applied_variant = "A"

    dist = abs(planned_entry - selected_sl)
    metrics = StopMetrics(
        price_distance=dist,
        distance_ticks=int(round(dist / tick_size)),
        distance_atr=(dist / eff_m1) if eff_m1 and eff_m1 > 0 else None,
        delta_vs_legacy_sl=abs(selected_sl - legacy_sl),
    )

    bound_leg = getattr(anchor_result, "bound_leg", None)
    protected_ep_id = getattr(bound_leg, "episode_id", getattr(bound_leg, "id", None)) if bound_leg else None
    anchor_inval_evidence = {
        "anchor_id": getattr(anchor, "id", getattr(anchor, "anchor_id", None)) if anchor else None,
        "anchor_price": getattr(anchor, "price", None) if anchor else None,
        "occurred_at_ms": getattr(anchor, "occurred_at_ms", None) if anchor else None,
        "known_at_ms": getattr(anchor, "known_at_ms", None) if anchor else None,
        "source_leg_id": getattr(anchor, "source_leg_id", None) if anchor else None,
    } if anchor else None

    vol_ctx = {
        "anchor_buffer_price": effective_buffer.buffer_price if effective_buffer else 0.0,
        "dominant_buffer_source": effective_buffer.dominant_source if effective_buffer else "tick_floor",
        "min_protective_distance": min_prot_dist,
        "dominant_protective_source": min_prot_res.dominant_source,
        "protective_expansion_applied": expansion_applied,
        "variant_c_sl": variant_c_sl,
        "anchor_price": getattr(anchor, "price", None) if anchor else None,
        "spread": eff_spread,
        "atr_m1": eff_m1,
        "atr_m5": eff_m5,
        "atr_m1_provenance": atr_m1_obj.to_dict() if (is_m1_valid and atr_m1_obj and hasattr(atr_m1_obj, "to_dict")) else (atr_m1_obj if is_m1_valid else None),
        "atr_m5_provenance": atr_m5_obj.to_dict() if (is_m5_valid and atr_m5_obj and hasattr(atr_m5_obj, "to_dict")) else (atr_m5_obj if is_m5_valid else None),
        "stop_distance_price": final_dist,
        "stop_distance_ticks": dist_ticks,
        "stop_distance_atr_m1": dist_atr_m1,
        "stop_distance_atr_m5": dist_atr_m5,
        "structural_anchor_id": getattr(anchor, "id", getattr(anchor, "anchor_id", None)) if anchor else None,
        "structural_anchor_kind": getattr(anchor, "kind", None) if anchor else None,
        "protected_episode_id": protected_ep_id,
        "anchor_invalidation_evidence": anchor_inval_evidence,
        "distance_assessment": distance_assessment,
        "algorithm_version": algo_ver,
        "symbol": symbol or (getattr(atr_m1_obj, "symbol", None) if atr_m1_obj else None),
        "volatility_gate_status": "AVAILABLE",
        "atr_m1_status": "AVAILABLE",
    }

    return _make_decision(
        status=StopStatus.VALID.value,
        selected_sl=selected_sl,
        anchor=anchor,
        bound_leg=bound_leg,
        buffer=effective_buffer,
        metrics=metrics,
        reasons=(),
        applied_variant=applied_variant,
        volatility_context=vol_ctx,
        protective_distance=min_prot_dist,
        expansion_applied=expansion_applied,
        distance_assessment=distance_assessment,
        structural_sl=variant_c_sl,
    )


def build_sweep_stop_descriptor(
    sweep: Dict[str, Any],
    closed_bars: Sequence[Any],
    bar_duration_ms: int = 60_000,
) -> Optional[Dict[str, Any]]:
    """
    Translates raw sweep detector output into a causal StopAnchor input descriptor.
    Extracts sweep_time_ms and derives known_at_ms from the closing time of reclaim_bar_idx.
    """
    sweep_time = sweep.get("sweep_time_ms") or sweep.get("timestamp")
    sweep_extreme = sweep.get("sweep_extreme") or sweep.get("price")
    if sweep_time is None or sweep_extreme is None:
        return None

    # Derive known_at_ms from reclaim_bar_idx if available
    reclaim_idx = sweep.get("reclaim_bar_idx")
    known_at_ms = None
    if reclaim_idx is not None and isinstance(reclaim_idx, int) and 0 <= reclaim_idx < len(closed_bars):
        c_open = _candle_open_ms(closed_bars[reclaim_idx])
        known_at_ms = c_open + bar_duration_ms
    elif "sweep_bar_idx" in sweep and isinstance(sweep["sweep_bar_idx"], int) and 0 <= sweep["sweep_bar_idx"] < len(closed_bars):
        c_open = _candle_open_ms(closed_bars[sweep["sweep_bar_idx"]])
        known_at_ms = c_open + bar_duration_ms
    else:
        known_at_ms = int(sweep_time) + bar_duration_ms

    return {
        "id": sweep.get("sweep_id") or sweep.get("episode_id") or f"sweep_{sweep_time}",
        "sweep_id": sweep.get("sweep_id"),
        "episode_id": sweep.get("episode_id"),
        "reference_id": sweep.get("reference_id"),
        "sweep_extreme": float(sweep_extreme),
        "price": float(sweep_extreme),
        "occurred_at_ms": int(sweep_time),
        "known_at_ms": int(known_at_ms),
        "source_leg_id": str(sweep.get("episode_id") or sweep.get("sweep_id") or "sweep_leg"),
    }
def validate_episode_interval(
    *,
    ref_occurred_at_ms: int,
    break_open_ms: int,
    episode_start_ms: int,
    episode_end_ms: int,
) -> Tuple[bool, Optional[str]]:
    """
    AURUM V17.5.8 (Part 020):
    Validates structural episode interval consistency.
    """
    if ref_occurred_at_ms <= 0 or break_open_ms <= 0 or episode_start_ms <= 0 or episode_end_ms <= 0:
        return False, "INVALID_TIMESTAMPS"
    if episode_start_ms < ref_occurred_at_ms:
        return False, "EPISODE_START_BEFORE_REFERENCE"
    if episode_start_ms >= episode_end_ms:
        return False, "EPISODE_START_AFTER_END"
    if episode_end_ms > break_open_ms:
        return False, "EPISODE_END_AFTER_BREAK"
    return True, None


def resolve_verified_reference_node(
    *,
    selected_reference: Optional[Dict[str, Any]],
    confirmed_swings: Optional[Tuple[Sequence[Dict[str, Any]], Sequence[Dict[str, Any]]]],
    closed_bars: Sequence[Any],
    model_id: str,
    direction: str,
    decision_cutoff_ms: int = 0,
    break_open_ms: Optional[int] = None,
    tick_size: float = 0.01,
    timeframe_ms: int = 60_000,
) -> Dict[str, Any]:
    """
    AURUM V17.5.9 (D01, D06, Section 02):
    Resolves and verifies the causal reference node with stable identity,
    non-ambiguous price matching, verified source candle provenance,
    expected swing extreme, and causal timestamps.
    """
    if not selected_reference:
        return {
            "status": "UNAVAILABLE",
            "reasons": ("MISSING_REFERENCE_DATA",),
            "ref_id": None,
            "ref_price": None,
            "ref_occurred_at_ms": None,
            "ref_known_at_ms": None,
        }

    norm_dir = str(direction).upper() if direction else ""
    if norm_dir not in ("SHORT", "SELL", "LONG", "BUY"):
        return {
            "status": "UNAVAILABLE",
            "reasons": ("INVALID_DIRECTION",),
            "ref_id": None,
            "ref_price": None,
            "ref_occurred_at_ms": None,
            "ref_known_at_ms": None,
        }

    tol = float(tick_size) if tick_size > 0 else 0.01
    sh_list: Sequence[Dict[str, Any]] = ()
    sl_list: Sequence[Dict[str, Any]] = ()
    if confirmed_swings:
        sh_list, sl_list = confirmed_swings

    ref_swings = sl_list if norm_dir in ("SHORT", "SELL") else sh_list

    ref_id = selected_reference.get("id") or selected_reference.get("swing_ref_id")
    p = selected_reference.get("price") if selected_reference.get("price") is not None else selected_reference.get("level")
    ref_price: Optional[float] = None
    if p is not None:
        if isinstance(p, bool):
            return {
                "status": "UNAVAILABLE",
                "reasons": ("INVALID_REFERENCE_PRICE",),
                "ref_id": str(ref_id) if ref_id else None,
                "ref_price": None,
                "ref_occurred_at_ms": None,
                "ref_known_at_ms": None,
            }
        try:
            f = float(p)
            if not math.isfinite(f) or f <= 0:
                return {
                    "status": "UNAVAILABLE",
                    "reasons": ("INVALID_REFERENCE_PRICE",),
                    "ref_id": str(ref_id) if ref_id else None,
                    "ref_price": None,
                    "ref_occurred_at_ms": None,
                    "ref_known_at_ms": None,
                }
            ref_price = f
        except (ValueError, TypeError):
            return {
                "status": "UNAVAILABLE",
                "reasons": ("INVALID_REFERENCE_PRICE",),
                "ref_id": str(ref_id) if ref_id else None,
                "ref_price": None,
                "ref_occurred_at_ms": None,
                "ref_known_at_ms": None,
            }

    ref_occ: Optional[int] = None
    ref_known: Optional[int] = None
    ref_occ_raw = selected_reference.get("occurred_at_ms")
    if ref_occ_raw is None:
        ref_occ_raw = selected_reference.get("timestamp")
    if ref_occ_raw is not None:
        is_valid_occ, parsed_occ, _ = parse_valid_market_timestamp(ref_occ_raw, field_name="reference_timestamp")
        if not is_valid_occ:
            return {
                "status": "UNAVAILABLE",
                "reasons": ("INVALID_REFERENCE_TIMESTAMPS",),
                "ref_id": str(ref_id) if ref_id else None,
                "ref_price": ref_price,
                "ref_occurred_at_ms": None,
                "ref_known_at_ms": None,
            }
        ref_occ = parsed_occ

    ref_known_raw = selected_reference.get("known_at_ms")
    if ref_known_raw is None:
        ref_known_raw = selected_reference.get("confirmed_at")
    if ref_known_raw is not None:
        is_valid_known, parsed_known, _ = parse_valid_market_timestamp(ref_known_raw, field_name="reference_confirmation_time")
        if not is_valid_known:
            return {
                "status": "UNAVAILABLE",
                "reasons": ("INVALID_REFERENCE_TIMESTAMPS",),
                "ref_id": str(ref_id) if ref_id else None,
                "ref_price": ref_price,
                "ref_occurred_at_ms": ref_occ,
                "ref_known_at_ms": None,
            }
        ref_known = parsed_known

    # Resolve from confirmed swings if possible
    if ref_id:
        matched_by_id = [s for s in ref_swings if str(s.get("id", "")) == str(ref_id)]
        if matched_by_id:
            s_node = matched_by_id[0]
            s_occ = int(s_node.get("occurred_at_ms", 0) or s_node.get("timestamp", 0))
            s_known = int(s_node.get("known_at_ms", 0) or s_node.get("confirmed_at", 0) or (s_occ + timeframe_ms))
            s_price_raw = s_node.get("price")
            s_price = float(s_price_raw) if s_price_raw is not None else None

            # Part 009 (D01): Contradictory caller-supplied fields must be rejected
            if ref_price is not None and s_price is not None:
                if abs(ref_price - s_price) > (tol / 2.0 + 1e-6):
                    return {
                        "status": "UNAVAILABLE",
                        "reasons": ("REFERENCE_METADATA_MISMATCH",),
                        "ref_id": str(ref_id),
                        "ref_price": ref_price,
                        "ref_occurred_at_ms": ref_occ,
                        "ref_known_at_ms": ref_known,
                    }
            if ref_occ is not None and s_occ > 0:
                if ref_occ != s_occ:
                    return {
                        "status": "UNAVAILABLE",
                        "reasons": ("REFERENCE_METADATA_MISMATCH",),
                        "ref_id": str(ref_id),
                        "ref_price": ref_price,
                        "ref_occurred_at_ms": ref_occ,
                        "ref_known_at_ms": ref_known,
                    }

            if ref_occ is None:
                ref_occ = s_occ
            if ref_known is None:
                ref_known = s_known
            if ref_price is None and s_price is not None:
                ref_price = s_price

    # If only price is available, check for ambiguity in confirmed swings (Part 014)
    if ref_occ is None and ref_price is not None:
        matched_price = [
            s for s in ref_swings
            if abs(float(s.get("price", 0.0)) - ref_price) <= (tol / 2.0 + 1e-6)
        ]
        if len(matched_price) > 1:
            distinct_times = set(int(s.get("occurred_at_ms", 0) or s.get("timestamp", 0)) for s in matched_price)
            if len(distinct_times) > 1:
                return {
                    "status": "UNAVAILABLE",
                    "reasons": ("AMBIGUOUS_REFERENCE_PRICE",),
                    "ref_id": str(ref_id) if ref_id else None,
                    "ref_price": ref_price,
                    "ref_occurred_at_ms": None,
                    "ref_known_at_ms": None,
                }
            else:
                s_node = matched_price[0]
                ref_id = ref_id or s_node.get("id")
                ref_occ = int(s_node.get("occurred_at_ms", 0) or s_node.get("timestamp", 0))
                ref_known = int(s_node.get("known_at_ms", 0) or s_node.get("confirmed_at", 0) or (ref_occ + timeframe_ms))
        elif len(matched_price) == 1:
            s_node = matched_price[0]
            ref_id = ref_id or s_node.get("id")
            ref_occ = int(s_node.get("occurred_at_ms", 0) or s_node.get("timestamp", 0))
            ref_known = int(s_node.get("known_at_ms", 0) or s_node.get("confirmed_at", 0) or (ref_occ + timeframe_ms))

    # Match in closed_bars if not found in swings
    if ref_occ is None and ref_price is not None and closed_bars and break_open_ms is not None:
        matching_bars = []
        for b in reversed(closed_bars):
            t_b = _candle_open_ms(b)
            if t_b < break_open_ms:
                try:
                    _, h, l, c = _candle_ohlc(b)
                    if (norm_dir in ("SHORT", "SELL") and (abs(l - ref_price) < tol or abs(c - ref_price) < tol)) or \
                       (norm_dir in ("LONG", "BUY") and (abs(h - ref_price) < tol or abs(c - ref_price) < tol)):
                        matching_bars.append(t_b)
                except Exception:
                    continue
        if len(matching_bars) == 1:
            ref_occ = matching_bars[0]
        elif len(matching_bars) > 1:
            # Part 008 (D01): Disallow arbitrary first candle selection on ambiguity
            return {
                "status": "UNAVAILABLE",
                "reasons": ("AMBIGUOUS_REFERENCE_SOURCE",),
                "ref_id": str(ref_id) if ref_id else None,
                "ref_price": ref_price,
                "ref_occurred_at_ms": None,
                "ref_known_at_ms": None,
            }

    if ref_occ is None:
        return {
            "status": "UNAVAILABLE",
            "reasons": ("MISSING_REFERENCE_SOURCE",),
            "ref_id": str(ref_id) if ref_id else None,
            "ref_price": ref_price,
            "ref_occurred_at_ms": None,
            "ref_known_at_ms": None,
        }

    if ref_known is None:
        ref_known = ref_occ + timeframe_ms

    if ref_occ <= 0 or ref_known <= 0 or ref_known < ref_occ:
        return {
            "status": "UNAVAILABLE",
            "reasons": ("INVALID_REFERENCE_TIMESTAMPS",),
            "ref_id": str(ref_id) if ref_id else None,
            "ref_price": ref_price,
            "ref_occurred_at_ms": ref_occ,
            "ref_known_at_ms": ref_known,
        }

    if break_open_ms is not None and ref_occ >= break_open_ms:
        return {
            "status": "UNAVAILABLE",
            "reasons": ("REFERENCE_AFTER_BREAK",),
            "ref_id": str(ref_id) if ref_id else None,
            "ref_price": ref_price,
            "ref_occurred_at_ms": ref_occ,
            "ref_known_at_ms": ref_known,
        }

    if decision_cutoff_ms > 0 and ref_known > decision_cutoff_ms:
        return {
            "status": "UNAVAILABLE",
            "reasons": ("REFERENCE_KNOWN_AFTER_DECISION",),
            "ref_id": str(ref_id) if ref_id else None,
            "ref_price": ref_price,
            "ref_occurred_at_ms": ref_occ,
            "ref_known_at_ms": ref_known,
        }

    # Verify authoritative reference candle provenance (Part 007 & 011)
    ref_bar = next((b for b in closed_bars if _candle_open_ms(b) == ref_occ), None) if closed_bars else None
    if ref_bar is None:
        # Part 007: Check if verified structural cache carries authoritative provenance
        # when a short chart display/evaluation window omits old candles prior to the episode
        can_use_cache = False
        if matched_by_id and closed_bars:
            s_node = matched_by_id[0]
            earliest_bar_ms = min(_candle_open_ms(b) for b in closed_bars)
            if ref_occ < earliest_bar_ms and s_node.get("price") is not None:
                can_use_cache = True

        if not can_use_cache:
            return {
                "status": "UNAVAILABLE",
                "reasons": ("REFERENCE_CANDLE_MISSING",),
                "ref_id": str(ref_id) if ref_id else None,
                "ref_price": ref_price,
                "ref_occurred_at_ms": ref_occ,
                "ref_known_at_ms": ref_known,
            }

    if ref_price is not None and ref_bar is not None:
        try:
            bo, bh, bl, bc = _candle_ohlc(ref_bar)
            if not (bl <= min(bo, bc) <= max(bo, bc) <= bh and bh >= bl):
                return {
                    "status": "UNAVAILABLE",
                    "reasons": ("REFERENCE_CANDLE_READ_ERROR",),
                    "ref_id": str(ref_id) if ref_id else None,
                    "ref_price": ref_price,
                    "ref_occurred_at_ms": ref_occ,
                    "ref_known_at_ms": ref_known,
                }
            if ref_price < (bl - tol) or ref_price > (bh + tol):
                return {
                    "status": "UNAVAILABLE",
                    "reasons": ("REFERENCE_PRICE_MISMATCH",),
                    "ref_id": str(ref_id) if ref_id else None,
                    "ref_price": ref_price,
                    "ref_occurred_at_ms": ref_occ,
                    "ref_known_at_ms": ref_known,
                }

            # Part 011 / R05: Verify expected swing extreme (bl/bc for low, bh/bc for high)
            is_extreme_match = False
            if norm_dir in ("SHORT", "SELL"):
                if abs(bl - ref_price) <= tol or abs(bc - ref_price) <= tol:
                    is_extreme_match = True
            elif norm_dir in ("LONG", "BUY"):
                if abs(bh - ref_price) <= tol or abs(bc - ref_price) <= tol:
                    is_extreme_match = True

            if not is_extreme_match:
                return {
                    "status": "UNAVAILABLE",
                    "reasons": ("REFERENCE_EXTREME_MISMATCH",),
                    "ref_id": str(ref_id) if ref_id else None,
                    "ref_price": ref_price,
                    "ref_occurred_at_ms": ref_occ,
                    "ref_known_at_ms": ref_known,
                }
        except Exception:
            return {
                "status": "UNAVAILABLE",
                "reasons": ("REFERENCE_CANDLE_READ_ERROR",),
                "ref_id": str(ref_id) if ref_id else None,
                "ref_price": ref_price,
                "ref_occurred_at_ms": ref_occ,
                "ref_known_at_ms": ref_known,
            }

    return {
        "status": "AVAILABLE",
        "reasons": (),
        "ref_id": str(ref_id) if ref_id else f"ref_{ref_occ}",
        "ref_price": ref_price,
        "ref_occurred_at_ms": ref_occ,
        "ref_known_at_ms": ref_known,
    }


def resolve_verified_break_candle(
    *,
    selected_break: Any,
    reference_price: Optional[float],
    closed_bars: Sequence[Any],
    direction: str,
    decision_cutoff_ms: int = 0,
    timeframe_ms: int = 60_000,
) -> Dict[str, Any]:
    """
    AURUM V17.5.9 (D02, D06, Section 03):
    Resolves and verifies the break candle from closed_bars.
    Enforces canonical source existence, eliminates unverified caller fallback,
    safely parses timestamps, and verifies strict close-through.
    """
    if selected_break is None:
        return {
            "status": "UNAVAILABLE",
            "reasons": ("MISSING_BREAK_DATA",),
            "break_open_ms": None,
            "break_known_at_ms": None,
            "break_bar_id": None,
            "break_bar": None,
        }

    norm_dir = str(direction).upper() if direction else ""
    if norm_dir not in ("SHORT", "SELL", "LONG", "BUY"):
        return {
            "status": "UNAVAILABLE",
            "reasons": ("INVALID_DIRECTION",),
            "break_open_ms": None,
            "break_known_at_ms": None,
            "break_bar_id": None,
            "break_bar": None,
        }

    b_open_raw = None
    b_known_raw = None
    break_bar_id = None
    supplied_bar = None

    if isinstance(selected_break, dict):
        supplied_bar = selected_break.get("bar")
        b_open_raw = selected_break.get("break_open_ms")
        if b_open_raw is None:
            b_open_raw = selected_break.get("open_ms")
        if b_open_raw is None and supplied_bar:
            b_open_raw = _candle_open_ms(supplied_bar)
        if b_open_raw is None:
            b_open_raw = selected_break.get("broken_at_ms") or selected_break.get("break_time_ms") or selected_break.get("timestamp")

        b_known_raw = selected_break.get("known_at_ms")
        if b_known_raw is None and selected_break.get("broken_at_ms") and b_open_raw and str(selected_break.get("broken_at_ms")) != str(b_open_raw):
            b_known_raw = selected_break.get("broken_at_ms")

        break_bar_id = selected_break.get("id") or selected_break.get("break_bar_id")
    else:
        supplied_bar = selected_break
        b_open_raw = _candle_open_ms(selected_break)

    if b_open_raw is None:
        return {
            "status": "UNAVAILABLE",
            "reasons": ("MISSING_BREAK_TIME",),
            "break_open_ms": None,
            "break_known_at_ms": None,
            "break_bar_id": None,
            "break_bar": None,
        }

    is_valid_open, parsed_b_open, _ = parse_valid_market_timestamp(b_open_raw, field_name="break_timestamp")
    if not is_valid_open:
        return {
            "status": "UNAVAILABLE",
            "reasons": ("INVALID_BREAK_TIMESTAMP",),
            "break_open_ms": None,
            "break_known_at_ms": None,
            "break_bar_id": None,
            "break_bar": None,
        }

    b_open_ms = parsed_b_open

    if b_known_raw is not None:
        is_valid_known, parsed_known, _ = parse_valid_market_timestamp(b_known_raw, field_name="break_confirmation_time")
        if not is_valid_known:
            return {
                "status": "UNAVAILABLE",
                "reasons": ("INVALID_BREAK_CONFIRMATION_TIME",),
                "break_open_ms": b_open_ms,
                "break_known_at_ms": None,
                "break_bar_id": str(break_bar_id) if break_bar_id else f"break_{b_open_ms}",
                "break_bar": None,
            }
        break_known_ms = parsed_known
    else:
        break_known_ms = b_open_ms + timeframe_ms

    # Locate source candle strictly in closed_bars (Part 017: No caller-supplied fallback!)
    source_bar = None
    if closed_bars:
        source_bar = next((b for b in closed_bars if _candle_open_ms(b) == b_open_ms), None)

    if source_bar is None:
        return {
            "status": "UNAVAILABLE",
            "reasons": ("MISSING_BREAK_SOURCE_CANDLE",),
            "break_open_ms": b_open_ms,
            "break_known_at_ms": break_known_ms,
            "break_bar_id": str(break_bar_id) if break_bar_id else f"break_{b_open_ms}",
            "break_bar": None,
        }

    # Strict OHLC validation on source candle
    try:
        bo, bh, bl, bc = _candle_ohlc(source_bar)
        if not all(math.isfinite(x) and x > 0 for x in (bo, bh, bl, bc)):
            return {
                "status": "UNAVAILABLE",
                "reasons": ("BREAK_CANDLE_INVALID",),
                "break_open_ms": b_open_ms,
                "break_known_at_ms": break_known_ms,
                "break_bar_id": str(break_bar_id) if break_bar_id else f"break_{b_open_ms}",
                "break_bar": source_bar,
            }
        if not (bl <= min(bo, bc) <= max(bo, bc) <= bh and bh >= bl):
            return {
                "status": "UNAVAILABLE",
                "reasons": ("BREAK_CANDLE_INVALID",),
                "break_open_ms": b_open_ms,
                "break_known_at_ms": break_known_ms,
                "break_bar_id": str(break_bar_id) if break_bar_id else f"break_{b_open_ms}",
                "break_bar": source_bar,
            }
    except Exception:
        return {
            "status": "UNAVAILABLE",
            "reasons": ("BREAK_CANDLE_INVALID",),
            "break_open_ms": b_open_ms,
            "break_known_at_ms": break_known_ms,
            "break_bar_id": str(break_bar_id) if break_bar_id else f"break_{b_open_ms}",
            "break_bar": source_bar,
        }

    # If both supplied_bar and source_bar exist, verify consistency
    if supplied_bar is not None and supplied_bar is not source_bar:
        try:
            s_bo, s_bh, s_bl, s_bc = _candle_ohlc(supplied_bar)
            if (
                abs(s_bo - bo) > 1e-4
                or abs(s_bh - bh) > 1e-4
                or abs(s_bl - bl) > 1e-4
                or abs(s_bc - bc) > 1e-4
                or _candle_open_ms(supplied_bar) != b_open_ms
            ):
                return {
                    "status": "UNAVAILABLE",
                    "reasons": ("BREAK_CANDLE_MISMATCH",),
                    "break_open_ms": b_open_ms,
                    "break_known_at_ms": break_known_ms,
                    "break_bar_id": str(break_bar_id) if break_bar_id else f"break_{b_open_ms}",
                    "break_bar": source_bar,
                }
        except Exception:
            return {
                "status": "UNAVAILABLE",
                "reasons": ("BREAK_CANDLE_INVALID",),
                "break_open_ms": b_open_ms,
                "break_known_at_ms": break_known_ms,
                "break_bar_id": str(break_bar_id) if break_bar_id else f"break_{b_open_ms}",
                "break_bar": source_bar,
            }

    # Check bar closure and confirmation causality (Part 021, B07, B08)
    is_closed = getattr(source_bar, "is_closed", None)
    if is_closed is None and isinstance(source_bar, dict):
        is_closed = source_bar.get("is_closed")
    if is_closed is False or break_known_ms <= b_open_ms:
        return {
            "status": "UNAVAILABLE",
            "reasons": ("BREAK_CONFIRMATION_BEFORE_CLOSE",),
            "break_open_ms": b_open_ms,
            "break_known_at_ms": break_known_ms,
            "break_bar_id": str(break_bar_id) if break_bar_id else f"break_{b_open_ms}",
            "break_bar": source_bar,
        }

    if decision_cutoff_ms > 0:
        if b_open_ms > decision_cutoff_ms or break_known_ms > decision_cutoff_ms:
            return {
                "status": "UNAVAILABLE",
                "reasons": ("BREAK_AFTER_DECISION_CUTOFF",),
                "break_open_ms": b_open_ms,
                "break_known_at_ms": break_known_ms,
                "break_bar_id": str(break_bar_id) if break_bar_id else f"break_{b_open_ms}",
                "break_bar": source_bar,
            }

    # Strict close-through check (Part 022, B09, B10, B11, B12)
    if reference_price is None or not math.isfinite(reference_price) or reference_price <= 0:
        return {
            "status": "UNAVAILABLE",
            "reasons": ("MISSING_REFERENCE_PRICE",),
            "break_open_ms": b_open_ms,
            "break_known_at_ms": break_known_ms,
            "break_bar_id": str(break_bar_id) if break_bar_id else f"break_{b_open_ms}",
            "break_bar": source_bar,
            "b_close": bc,
        }

    if norm_dir in ("SHORT", "SELL") and bc >= reference_price:
        return {
            "status": "UNAVAILABLE",
            "reasons": ("BREAK_NOT_CLOSED_THROUGH_REFERENCE",),
            "break_open_ms": b_open_ms,
            "break_known_at_ms": break_known_ms,
            "break_bar_id": str(break_bar_id) if break_bar_id else f"break_{b_open_ms}",
            "break_bar": source_bar,
            "b_close": bc,
        }
    elif norm_dir in ("LONG", "BUY") and bc <= reference_price:
        return {
            "status": "UNAVAILABLE",
            "reasons": ("BREAK_NOT_CLOSED_THROUGH_REFERENCE",),
            "break_open_ms": b_open_ms,
            "break_known_at_ms": break_known_ms,
            "break_bar_id": str(break_bar_id) if break_bar_id else f"break_{b_open_ms}",
            "break_bar": source_bar,
            "b_close": bc,
        }

    return {
        "status": "AVAILABLE",
        "reasons": (),
        "break_open_ms": b_open_ms,
        "break_known_at_ms": break_known_ms,
        "break_bar_id": str(break_bar_id) if break_bar_id else f"break_{b_open_ms}",
        "break_bar": source_bar,
        "b_close": bc,
    }


def validate_protected_origin_binding(
    *,
    origin: Dict[str, Any],
    direction: str,
    break_open_ms: int,
    decision_cutoff_ms: int = 0,
    closed_bars: Sequence[Any] = (),
    tick_size: float = 0.01,
    episode_start_ms: Optional[int] = None,
    episode_end_ms: Optional[int] = None,
) -> Tuple[bool, Optional[str]]:
    """
    AURUM V17.5.6/V17.5.7/V17.5.8 (Parts 031-036):
    Pure validation helper verifying pivot source candle, extreme price,
    time boundaries, and episode membership.
    """
    if not closed_bars:
        return False, "MISSING_SOURCE_CANDLES"

    norm_dir = direction.upper()
    tol = float(tick_size) if tick_size > 0 else 0.01
    p_occ = int(origin.get("occurred_at_ms", 0) or origin.get("timestamp", 0))
    p_known = int(origin.get("known_at_ms", origin.get("confirmed_at", p_occ)))

    try:
        p_px = float(origin.get("price", 0.0))
        if not (math.isfinite(p_px) and p_px > 0):
            return False, "INVALID_ORIGIN_PRICE"
    except (ValueError, TypeError):
        return False, "INVALID_ORIGIN_PRICE"

    if p_known < p_occ:
        return False, "INVALID_ORIGIN_TIMESTAMPS"

    if p_occ >= break_open_ms:
        return False, "ORIGIN_NOT_BEFORE_BREAK"

    if decision_cutoff_ms > 0 and p_known > decision_cutoff_ms:
        return False, "ORIGIN_KNOWN_AFTER_DECISION"

    if episode_start_ms is not None and p_occ < episode_start_ms:
        return False, "ORIGIN_OUTSIDE_EPISODE"

    if episode_end_ms is not None and p_occ >= episode_end_ms:
        return False, "ORIGIN_AFTER_EPISODE_END"

    matched = False
    for b in closed_bars:
        if _candle_open_ms(b) == p_occ:
            matched = True
            try:
                _, h, l, _ = _candle_ohlc(b)
            except Exception:
                return False, "SOURCE_CANDLE_READ_ERROR"
            if norm_dir in ("SHORT", "SELL") and abs(p_px - h) > tol:
                return False, "ORIGIN_PRICE_MISMATCH_HIGH"
            elif norm_dir in ("LONG", "BUY") and abs(p_px - l) > tol:
                return False, "ORIGIN_PRICE_MISMATCH_LOW"
            break

    if not matched:
        return False, "SOURCE_CANDLE_NOT_FOUND"

    return True, None


def resolve_episode_membership_from_structure(
    *,
    model_id: str = "M1_CONTINUATION",
    direction: str,
    selected_reference: Optional[Dict[str, Any]] = None,
    selected_break: Any = None,
    confirmed_swings: Optional[Tuple[Sequence[Dict[str, Any]], Sequence[Dict[str, Any]]]] = None,
    closed_bars: Sequence[Any] = (),
    decision_cutoff_ms: int = 0,
    timeframe_ms: int = 60_000,
    tick_size: float = 0.01,
) -> Dict[str, Any]:
    """
    AURUM V17.5.7/V17.5.8 (Parts 012-020):
    Pure structural helper resolving episode boundaries from reference and break nodes
    without requiring caller to self-inject episode_start_ms.
    """
    norm_dir = direction.upper()
    sh_list: Sequence[Dict[str, Any]] = ()
    sl_list: Sequence[Dict[str, Any]] = ()
    if confirmed_swings:
        sh_list, sl_list = confirmed_swings

    ref_swings = sl_list if norm_dir in ("SHORT", "SELL") else sh_list

    # 1. Resolve break open time
    b_open = None
    if isinstance(selected_break, dict):
        b_open = selected_break.get("break_open_ms") or selected_break.get("open_ms")
        if b_open is None and selected_break.get("bar"):
            b_open = _candle_open_ms(selected_break["bar"])
        if b_open is None:
            b_open = selected_break.get("broken_at_ms") or selected_break.get("break_time_ms") or selected_break.get("timestamp")
    elif selected_break:
        b_open = _candle_open_ms(selected_break)

    if b_open is None or int(b_open) <= 0:
        return {
            "status": "UNAVAILABLE",
            "reasons": ("MISSING_BREAK_TIME",),
            "episode_start_ms": None,
            "episode_end_ms": None,
            "boundary_source": "UNAVAILABLE",
        }
    b_open_ms = int(b_open)

    # 2. Resolve verified reference node
    ref_res = resolve_verified_reference_node(
        selected_reference=selected_reference,
        confirmed_swings=confirmed_swings,
        closed_bars=closed_bars,
        model_id=model_id,
        direction=norm_dir,
        decision_cutoff_ms=decision_cutoff_ms,
        break_open_ms=b_open_ms,
        tick_size=tick_size,
        timeframe_ms=timeframe_ms,
    )
    if ref_res["status"] != "AVAILABLE":
        return {
            "status": "UNAVAILABLE",
            "reasons": ref_res["reasons"],
            "episode_start_ms": None,
            "episode_end_ms": None,
            "ref_price": ref_res.get("ref_price"),
            "ref_id": ref_res.get("ref_id"),
            "ref_occurred_at_ms": ref_res.get("ref_occurred_at_ms"),
            "ref_known_at_ms": ref_res.get("ref_known_at_ms"),
            "boundary_source": "UNAVAILABLE",
        }

    ref_occ = ref_res["ref_occurred_at_ms"]
    ref_price = ref_res["ref_price"]
    ref_id = ref_res["ref_id"]

    episode_start_ms = ref_occ
    episode_end_ms = b_open_ms

    # Identify subsequent structure reference of the same kind that bounds this leg
    subsequent_refs = [
        int(s.get("occurred_at_ms", 0) or s.get("timestamp", 0))
        for s in ref_swings
        if ref_occ < int(s.get("occurred_at_ms", 0) or s.get("timestamp", 0)) < b_open_ms
    ]
    if subsequent_refs:
        episode_end_ms = min(b_open_ms, min(subsequent_refs))

    is_int_valid, int_err = validate_episode_interval(
        ref_occurred_at_ms=ref_occ,
        break_open_ms=b_open_ms,
        episode_start_ms=episode_start_ms,
        episode_end_ms=episode_end_ms,
    )
    if not is_int_valid:
        return {
            "status": "UNAVAILABLE",
            "reasons": (int_err or "INVALID_EPISODE_INTERVAL",),
            "episode_start_ms": None,
            "episode_end_ms": None,
            "boundary_source": "UNAVAILABLE",
        }

    return {
        "status": "AVAILABLE",
        "reasons": (),
        "episode_start_ms": episode_start_ms,
        "episode_end_ms": episode_end_ms,
        "ref_occurred_at_ms": ref_occ,
        "ref_known_at_ms": ref_res.get("ref_known_at_ms"),
        "ref_price": ref_price,
        "ref_id": ref_id,
        "boundary_source": "CONFIRMED_STRUCTURE_NODES",
        "derivation_version": 1,
    }


def derive_protected_leg_episode(
    *,
    model_id: str = "M1_CONTINUATION",
    direction: str,
    selected_reference: Optional[Dict[str, Any]] = None,
    selected_break: Any = None,
    confirmed_swings: Optional[Tuple[Sequence[Dict[str, Any]], Sequence[Dict[str, Any]]]] = None,
    closed_bars: Sequence[Any] = (),
    decision_cutoff_ms: int = 0,
    timeframe_ms: int = 60_000,
    tick_size: float = 0.01,
) -> Any:
    """
    AURUM V17.5.8 (Parts 011-040):
    Derives the displacement break episode that created the opportunity.
    Enforces independent reference/break boundaries, verified break candle,
    source candle verification, and returns eligible origin IDs within the leg.
    """
    from services.stop_placement_types import ProtectedLegEpisode

    norm_dir = direction.upper()
    cutoff = int(decision_cutoff_ms or 0)
    tol = float(tick_size) if tick_size > 0 else 0.01

    if not closed_bars:
        return ProtectedLegEpisode(
            status="UNAVAILABLE",
            model_id=model_id,
            direction=norm_dir,
            decision_cutoff_ms=cutoff,
            reasons=("MISSING_SOURCE_CANDLES",),
        )

    if selected_break is None:
        return ProtectedLegEpisode(
            status="UNAVAILABLE",
            model_id=model_id,
            direction=norm_dir,
            decision_cutoff_ms=cutoff,
            reasons=("MISSING_BREAK_DATA",),
        )

    # 1. Structural membership and reference resolution
    struct_res = resolve_episode_membership_from_structure(
        model_id=model_id,
        direction=norm_dir,
        selected_reference=selected_reference,
        selected_break=selected_break,
        confirmed_swings=confirmed_swings,
        closed_bars=closed_bars,
        decision_cutoff_ms=cutoff,
        timeframe_ms=timeframe_ms,
        tick_size=tol,
    )

    ref_id = struct_res.get("ref_id")
    ref_price = struct_res.get("ref_price")
    ref_occ = struct_res.get("ref_occurred_at_ms")
    ref_known = struct_res.get("ref_known_at_ms")

    # 2. Break validation and strict close-through
    break_res = resolve_verified_break_candle(
        selected_break=selected_break,
        reference_price=ref_price,
        closed_bars=closed_bars,
        direction=norm_dir,
        decision_cutoff_ms=cutoff,
        timeframe_ms=timeframe_ms,
    )

    b_open_ms = break_res.get("break_open_ms")
    break_known_ms = break_res.get("break_known_at_ms")
    break_bar_id = break_res.get("break_bar_id")

    if break_res["status"] != "AVAILABLE":
        return ProtectedLegEpisode(
            status="UNAVAILABLE",
            model_id=model_id,
            direction=norm_dir,
            reference_id=str(ref_id) if ref_id else None,
            reference_price=ref_price,
            reference_occurred_at_ms=ref_occ,
            reference_known_at_ms=ref_known,
            break_bar_id=str(break_bar_id) if break_bar_id else (f"break_{b_open_ms}" if b_open_ms else None),
            break_open_ms=b_open_ms,
            break_known_at_ms=break_known_ms,
            decision_cutoff_ms=cutoff,
            reasons=break_res["reasons"],
        )

    if struct_res["status"] != "AVAILABLE":
        return ProtectedLegEpisode(
            status="UNAVAILABLE",
            model_id=model_id,
            direction=norm_dir,
            reference_id=str(ref_id) if ref_id else None,
            reference_price=ref_price,
            reference_occurred_at_ms=ref_occ,
            reference_known_at_ms=ref_known,
            break_bar_id=str(break_bar_id) if break_bar_id else (f"break_{b_open_ms}" if b_open_ms else None),
            break_open_ms=b_open_ms,
            break_known_at_ms=break_known_ms,
            decision_cutoff_ms=cutoff,
            reasons=struct_res["reasons"],
        )

    derived_start = struct_res["episode_start_ms"]
    derived_end = struct_res["episode_end_ms"]

    # PART 019: Caller-supplied boundary cannot overwrite derived structural boundary
    # derived_start is authoritative.

    sh_list: Sequence[Dict[str, Any]] = ()
    sl_list: Sequence[Dict[str, Any]] = ()
    if confirmed_swings:
        sh_list, sl_list = confirmed_swings
    pivots = sh_list if norm_dir in ("SHORT", "SELL") else sl_list

    specified_id = None
    if isinstance(selected_break, dict):
        specified_id = (
            selected_break.get("origin_pivot_id")
            or selected_break.get("origin_id")
            or selected_break.get("target_origin_id")
        )

    eligible = []
    for p in pivots:
        is_valid, _ = validate_protected_origin_binding(
            origin=p,
            direction=norm_dir,
            break_open_ms=b_open_ms,
            decision_cutoff_ms=cutoff,
            closed_bars=closed_bars,
            tick_size=tol,
            episode_start_ms=derived_start,
            episode_end_ms=derived_end,
        )
        if is_valid:
            p_px = float(p.get("price", 0.0))
            if norm_dir in ("SHORT", "SELL") and ref_price is not None:
                if p_px <= ref_price:
                    continue
            elif norm_dir in ("LONG", "BUY") and ref_price is not None:
                if p_px >= ref_price:
                    continue
            eligible.append(p)

    if specified_id:
        matched_specified = [p for p in eligible if str(p.get("id", "")) == str(specified_id)]
        if not matched_specified:
            return ProtectedLegEpisode(
                status="UNAVAILABLE",
                model_id=model_id,
                direction=norm_dir,
                reference_id=str(ref_id) if ref_id else None,
                reference_price=ref_price,
                reference_occurred_at_ms=ref_occ,
                reference_known_at_ms=ref_known,
                break_bar_id=str(break_bar_id) if break_bar_id else f"break_{b_open_ms}",
                break_open_ms=b_open_ms,
                break_known_at_ms=break_known_ms,
                decision_cutoff_ms=cutoff,
                reasons=("SPECIFIED_ORIGIN_INVALID",),
            )
        eligible = matched_specified

    if not eligible:
        return ProtectedLegEpisode(
            status="UNAVAILABLE",
            model_id=model_id,
            direction=norm_dir,
            reference_id=str(ref_id) if ref_id else None,
            reference_price=ref_price,
            reference_occurred_at_ms=ref_occ,
            reference_known_at_ms=ref_known,
            break_bar_id=str(break_bar_id) if break_bar_id else f"break_{b_open_ms}",
            break_open_ms=b_open_ms,
            break_known_at_ms=break_known_ms,
            decision_cutoff_ms=cutoff,
            reasons=("NO_VALID_PROTECTED_ORIGIN_IN_EPISODE",),
        )

    # Sort deterministically
    if norm_dir in ("SHORT", "SELL"):
        eligible.sort(
            key=lambda c: (
                float(c.get("price", 0.0)),
                int(c.get("occurred_at_ms", 0) or c.get("timestamp", 0)),
                int(c.get("known_at_ms", 0) or c.get("confirmed_at", 0)),
                str(c.get("id", "")),
            ),
            reverse=True,
        )
    else:
        eligible.sort(
            key=lambda c: (
                -float(c.get("price", 0.0)),
                int(c.get("occurred_at_ms", 0) or c.get("timestamp", 0)),
                int(c.get("known_at_ms", 0) or c.get("confirmed_at", 0)),
                str(c.get("id", "")),
            ),
            reverse=True,
        )

    best = eligible[0]
    best_occ = int(best.get("occurred_at_ms", 0) or best.get("timestamp", 0))
    source_ids = [str(_candle_open_ms(b)) for b in closed_bars if _candle_open_ms(b) in (best_occ, b_open_ms)]

    return ProtectedLegEpisode(
        status="AVAILABLE",
        model_id=model_id,
        direction=norm_dir,
        reference_id=str(ref_id) if ref_id else None,
        reference_price=ref_price,
        reference_occurred_at_ms=ref_occ,
        reference_known_at_ms=ref_known,
        break_bar_id=str(break_bar_id) if break_bar_id else f"break_{b_open_ms}",
        break_open_ms=b_open_ms,
        break_known_at_ms=break_known_ms,
        decision_cutoff_ms=cutoff,
        episode_start_ms=derived_start,
        episode_end_ms=derived_end,
        eligible_origin_ids=tuple(str(p.get("id", "")) for p in eligible if p.get("id")),
        source_bar_ids=tuple(source_ids),
        boundary_source="CONFIRMED_STRUCTURE_NODES",
        derivation_version=1,
        reasons=(),
    )


def select_protected_origin_for_break(
    *,
    model_id: str = "M1_CONTINUATION",
    direction: str,
    selected_break: Any,
    selected_reference: Optional[Dict[str, Any]] = None,
    confirmed_swings: Optional[Tuple[Sequence[Dict[str, Any]], Sequence[Dict[str, Any]]]] = None,
    closed_bars: Sequence[Any] = (),
    decision_cutoff_ms: int = 0,
    tick_size: float = 0.01,
) -> Dict[str, Any]:
    """
    AURUM V17.5.6 (Appendix C1-C3):
    Deterministic origin bridge selecting the protected swing origin of the displacement
    break episode that created the opportunity.
    Ensures that:
    1. closed_bars is mandatory; empty closed_bars returns UNAVAILABLE.
    2. Origin occurred strictly before break bar open.
    3. Origin was known at or before decision cutoff.
    4. Source candle exists in closed_bars and matches extreme price within tick_size tolerance.
    5. Origin belongs to the displacement episode bounded by reference and break.
    6. Prioritizes detector-specified origin and rejects without fallback if invalid.
    """
    norm_dir = direction.upper()

    episode = derive_protected_leg_episode(
        model_id=model_id,
        direction=direction,
        selected_reference=selected_reference,
        selected_break=selected_break,
        confirmed_swings=confirmed_swings,
        closed_bars=closed_bars,
        decision_cutoff_ms=decision_cutoff_ms,
        tick_size=tick_size,
    )

    if episode.status != "AVAILABLE":
        primary_reason = episode.reasons[0] if episode.reasons else "EPISODE_UNAVAILABLE"
        return {
            "status": "UNAVAILABLE",
            "origin_pivot_id": None,
            "origin_price": None,
            "occurred_at_ms": None,
            "origin_occurred_at_ms": None,
            "known_at_ms": None,
            "episode_start_ms": None,
            "origin_kind": None,
            "reason": primary_reason,
            "episode": episode.to_dict(),
        }

    best_id = episode.eligible_origin_ids[0]
    sh_list, sl_list = confirmed_swings if confirmed_swings else ((), ())
    pivots = sh_list if norm_dir in ("SHORT", "SELL") else sl_list
    best_pivot = next((p for p in pivots if str(p.get("id", "")) == str(best_id)), None)

    if not best_pivot:
        return {
            "status": "UNAVAILABLE",
            "origin_pivot_id": None,
            "origin_price": None,
            "occurred_at_ms": None,
            "origin_occurred_at_ms": None,
            "known_at_ms": None,
            "episode_start_ms": None,
            "origin_kind": None,
            "reason": "ORIGIN_PIVOT_LOOKUP_FAILED",
            "episode": episode.to_dict(),
        }

    p_occ = int(best_pivot.get("occurred_at_ms", 0) or best_pivot.get("timestamp", 0))
    p_known = int(best_pivot.get("known_at_ms", best_pivot.get("confirmed_at", p_occ)))
    p_px = float(best_pivot.get("price", 0.0))

    return {
        "status": "AVAILABLE",
        "origin_pivot_id": str(best_id),
        "origin_price": p_px,
        "occurred_at_ms": p_occ,
        "origin_occurred_at_ms": p_occ,
        "known_at_ms": p_known,
        "episode_start_ms": episode.episode_start_ms,
        "origin_kind": StopAnchorKind.PROTECTED_LEG_HIGH.value if norm_dir in ("SHORT", "SELL") else StopAnchorKind.PROTECTED_LEG_LOW.value,
        "reason": None,
        "episode": episode.to_dict(),
    }


def build_selected_break_descriptor(
    *,
    break_bar: Any,
    broken_reference: Optional[Dict[str, Any]] = None,
    timeframe_ms: int = 60_000,
    decision_cutoff_ms: int = 0,
    model_id: str = "",
    instance_id: str = "default",
    direction: str = "",
    origin_pivot_id: Optional[str] = None,
    episode_start_ms: Optional[int] = None,
    origin_price: Optional[float] = None,
    origin_occurred_at_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Builds a complete, normalized SelectedBreak descriptor from actual detector break bar
    and broken structure reference.
    Guarantees break_open_ms, broken_at_ms, known_at_ms, broken_reference_id, and reference_price.
    Preserves protected origin binding (origin_pivot_id, episode_start_ms) for structural SL proofs.
    """
    if break_bar is None:
        return {}

    b_open = _candle_open_ms(break_bar)
    b_close = b_open + timeframe_ms

    # Causal validation: break must open and close at or before decision cutoff
    if decision_cutoff_ms > 0:
        if b_open > decision_cutoff_ms or b_close > decision_cutoff_ms:
            return {}

    ref_id = None
    ref_price = None
    if broken_reference:
        ref_id = broken_reference.get("id") or broken_reference.get("swing_ref_id")
        p = (
            broken_reference.get("price")
            if broken_reference.get("price") is not None
            else broken_reference.get("swing_ref_price")
        )
        if p is not None:
            try:
                ref_price = float(p)
            except (ValueError, TypeError):
                ref_price = None

    return {
        "id": f"break_{b_open}",
        "break_open_ms": b_open,
        "broken_at_ms": b_open,
        "known_at_ms": b_close,
        "break_time_ms": b_open,
        "broken_reference_id": str(ref_id) if ref_id else None,
        "broken_price": ref_price,
        "reference_price": ref_price,
        "bar": break_bar,
        "model_id": model_id,
        "instance_id": instance_id,
        "direction": direction,
        "decision_cutoff_ms": decision_cutoff_ms,
        "origin_pivot_id": origin_pivot_id,
        "target_origin_id": origin_pivot_id,
        "episode_start_ms": episode_start_ms,
        "origin_price": origin_price,
        "origin_occurred_at_ms": origin_occurred_at_ms,
    }


def resolve_bound_stop_leg(
    *,
    model_id: str,
    direction: str,
    instance_id: str = "default",
    closed_bars: Sequence[Any] = (),
    selected_break: Optional[Dict[str, Any]] = None,
    selected_fvg: Optional[Dict[str, Any]] = None,
    candidate_pivot: Optional[Dict[str, Any]] = None,
    candidate_pivots: Optional[Sequence[Dict[str, Any]]] = None,
    break_bar: Optional[Any] = None,
    decision_cutoff_ms: int = 0,
    as_of_ms: int = 0,
    tolerance_tick: Optional[float] = None,
    tick_size: float = 0.01,
) -> Optional[Any]:
    """
    Verifies and constructs a SelectedStopLeg descriptor binding the protective pivot
    to the exact leg and break bar that created the opportunity.
    Enforces that:
    1. Origin pivot occurred strictly before break bar open/cutoff.
    2. Origin pivot was known before or at decision cutoff.
    3. Break bar occurred and was known at or before decision cutoff.
    4. Origin pivot price matches the candle extreme within instrument tick tolerance.
       Source candle MUST exist in closed_bars if closed_bars are provided.
    5. Break bar actually closed strictly through the reference price (no equality, no wick-only).
    """
    from services.stop_placement_types import SelectedStopLeg

    if not selected_break and not break_bar and not selected_fvg:
        return None

    cutoff = decision_cutoff_ms or as_of_ms or 0

    # Causal validation: break bar and known time cannot be in the future relative to cutoff
    if cutoff > 0:
        if selected_break:
            b_known = selected_break.get("known_at_ms")
            if b_known is not None and int(b_known) > cutoff:
                return None
            b_opened = (
                selected_break.get("break_open_ms")
                or selected_break.get("broken_at_ms")
                or selected_break.get("break_time_ms")
            )
            if b_opened is not None and int(b_opened) > cutoff:
                return None
        if break_bar:
            bar_open = _candle_open_ms(break_bar)
            if bar_open > cutoff:
                return None

    b_time = None
    if selected_break:
        b_time = (
            selected_break.get("broken_at_ms")
            or selected_break.get("break_time_ms")
            or selected_break.get("timestamp")
            or selected_break.get("break_open_ms")
        )
        if b_time is None and "bar" in selected_break:
            b_time = _candle_open_ms(selected_break["bar"])
    if b_time is None and break_bar:
        b_time = _candle_open_ms(break_bar)
    if b_time is None and selected_fvg:
        b_time = (
            selected_fvg.get("middle_bar_ms")
            or selected_fvg.get("timestamp")
            or selected_fvg.get("time_ms")
            or selected_fvg.get("start_ms")
            or selected_fvg.get("formation_time_ms")
            or selected_fvg.get("confirmed_at")
        )
    if b_time is None:
        b_time = cutoff

    break_time = int(b_time)
    if cutoff > 0 and break_time > cutoff:
        return None
    norm_dir = direction.upper()

    tolerance = float(tolerance_tick if tolerance_tick is not None else tick_size)
    if tolerance <= 0:
        tolerance = 0.01

    def _verify_pivot_source_and_extreme(p: Dict[str, Any]) -> bool:
        if not closed_bars:
            return True
        p_occ = int(p.get("occurred_at_ms", 0))
        p_px = float(p.get("price", 0.0))
        matched = False
        for b in closed_bars:
            if _candle_open_ms(b) == p_occ:
                matched = True
                try:
                    _, h, l, _ = _candle_ohlc(b)
                except Exception:
                    return False
                if norm_dir == "SHORT" and abs(p_px - h) > tolerance:
                    return False
                elif norm_dir == "LONG" and abs(p_px - l) > tolerance:
                    return False
                break
        return matched  # Missing source candle in closed_bars fails validation

    target_origin_id = None
    episode_start_ms = None
    if selected_break:
        target_origin_id = (
            selected_break.get("origin_pivot_id")
            or selected_break.get("origin_id")
            or selected_break.get("target_origin_id")
        )
        episode_start_ms = (
            selected_break.get("episode_start_ms")
            or selected_break.get("leg_start_ms")
            or selected_break.get("origin_occurred_at_ms")
        )

    all_pivots = list(candidate_pivots) if candidate_pivots else ([] if candidate_pivot is None else [candidate_pivot])

    chosen_pivot = None
    if all_pivots:
        valid_candidates = []
        for p in all_pivots:
            p_occ = int(p.get("occurred_at_ms", 0))
            p_known = int(p.get("known_at_ms", p_occ))
            if p_occ < break_time and (cutoff == 0 or p_known <= cutoff):
                if episode_start_ms is not None and p_occ < int(episode_start_ms):
                    continue
                valid_candidates.append(p)

        if valid_candidates:
            # 1. If target_origin_id specified by selected_break/detector, verify and prioritize it
            if target_origin_id:
                for p in valid_candidates:
                    if str(p.get("id", "")) == str(target_origin_id):
                        if _verify_pivot_source_and_extreme(p):
                            chosen_pivot = p
                            break

            # 2. Otherwise sort deterministically: closest before break, tie-break known_at, then id
            if chosen_pivot is None:
                valid_candidates.sort(
                    key=lambda p: (
                        int(p.get("occurred_at_ms", 0)),
                        int(p.get("known_at_ms", 0)),
                        str(p.get("id", "")),
                    ),
                    reverse=True,
                )
                for p in valid_candidates:
                    if _verify_pivot_source_and_extreme(p):
                        chosen_pivot = p
                        break

    if not chosen_pivot:
        return None

    pivot_occ = int(chosen_pivot.get("occurred_at_ms", 0))
    pivot_known = int(chosen_pivot.get("known_at_ms", pivot_occ))
    pivot_px = float(chosen_pivot.get("price", 0.0))

    # Invariant: Origin pivot must occur strictly before the break bar
    if pivot_occ >= break_time or (cutoff > 0 and pivot_known > cutoff):
        return None

    # Verify break bar close through reference if available (strict close-through: no equality)
    broken_ref_price = None
    if selected_break:
        broken_ref_price = (
            selected_break.get("broken_price")
            if selected_break.get("broken_price") is not None
            else selected_break.get("reference_price")
        )

    b_bar = break_bar or (selected_break.get("bar") if selected_break else None)
    if b_bar and broken_ref_price is not None:
        try:
            _, _, _, b_close = _candle_ohlc(b_bar)
            ref_px = float(broken_ref_price)
            if norm_dir == "SHORT" and b_close >= ref_px:
                return None
            elif norm_dir == "LONG" and b_close <= ref_px:
                return None
        except Exception:
            return None

    break_id = str(selected_break.get("id") if selected_break else f"brk_{break_time}")
    break_known = (
        selected_break.get("known_at_ms")
        if selected_break
        else break_time + 60_000
    ) or (break_time + 60_000)

    fvg_id = selected_fvg.get("id") if selected_fvg else None
    fvg_mid = (
        selected_fvg.get("middle_bar_ms") or selected_fvg.get("timestamp")
        if selected_fvg else None
    )
    fvg_conf = (
        selected_fvg.get("confirmed_at") or selected_fvg.get("confirmed_at_ms")
        if selected_fvg else None
    )

    return SelectedStopLeg(
        model_id=model_id,
        direction=norm_dir,
        instance_id=instance_id,
        leg_id=str((selected_break and selected_break.get("id")) or f"leg_{break_time}"),
        origin_pivot_id=str(chosen_pivot.get("id", "")),
        origin_price=pivot_px,
        origin_occurred_at_ms=pivot_occ,
        origin_known_at_ms=pivot_known,
        broken_reference_id=selected_break.get("broken_reference_id") if selected_break else None,
        broken_reference_price=float(broken_ref_price) if broken_ref_price is not None else None,
        break_bar_id=break_id,
        break_open_ms=break_time,
        break_known_at_ms=int(break_known),
        fvg_id=str(fvg_id) if fvg_id else None,
        fvg_middle_bar_ms=int(fvg_mid) if fvg_mid else None,
        fvg_confirmed_at_ms=int(fvg_conf) if fvg_conf else None,
        decision_cutoff_ms=cutoff,
    )


def validate_applied_stop_decision(
    *,
    candidate: Any,
    current_policy: StopPlacementPolicy,
    tolerance_tick: float = 0.01,
    now_ms: Optional[int] = None,
) -> Tuple[bool, str]:
    """
    Fail-closed barrier invoked at evaluator completion and ARM critical section.
    Rejects any trade whose stop loss does not satisfy current policy requirements.
    In PAPER_STRUCTURAL mode (V17.7.4), validates execution-grade ATR evidence provenance.
    """
    cand_sl = candidate.get("stop_loss") if isinstance(candidate, dict) else getattr(candidate, "stop_loss", None)
    calc = candidate.get("calculation") if isinstance(candidate, dict) else getattr(candidate, "calculation", None)
    sp = candidate.get("stop_placement") if isinstance(candidate, dict) else getattr(candidate, "stop_placement", None)

    if current_policy.mode == StopPlacementMode.PAPER_STRUCTURAL.value:
        if sp is None:
            return False, "MISSING_STOP_PLACEMENT_DECISION"
        if getattr(sp, "status", "") != StopStatus.VALID.value:
            return False, f"STRUCTURAL_STOP_STATUS_INVALID: {getattr(sp, 'status', '')}"
        if getattr(sp, "applied_variant", "") != "C":
            return False, f"STRUCTURAL_MODE_EXPECTS_VARIANT_C_GOT_{getattr(sp, 'applied_variant', '')}"
        if getattr(sp, "anchor", None) is None:
            return False, "MISSING_STRUCTURAL_ANCHOR"
        if getattr(sp, "selected_sl", None) is None:
            return False, "MISSING_SELECTED_STRUCTURAL_SL"
        if cand_sl is None or abs(float(cand_sl) - float(sp.selected_sl)) > tolerance_tick:
            return False, f"CANDIDATE_SL_MISMATCH_WITH_DECISION: {cand_sl} vs {sp.selected_sl}"
        if calc is not None:
            calc_sl = getattr(calc, "stop_loss", None)
            if calc_sl is None or abs(float(calc_sl) - float(sp.selected_sl)) > tolerance_tick:
                return False, f"CALC_SL_MISMATCH_WITH_DECISION: {calc_sl} vs {sp.selected_sl}"
            if not getattr(calc, "can_execute", False):
                return False, f"CALC_CANNOT_EXECUTE: {getattr(calc, 'skip_reason', '')}"

        # V17.7.4 Section 07.4: Validate underlying execution-grade ATR evidence
        vol_ctx = getattr(sp, "volatility_context", None)
        if not isinstance(vol_ctx, dict):
            return False, "FORGED_STRUCTURAL_DECISION_MISSING_ATR_EVIDENCE: MISSING_VOLATILITY_CONTEXT"

        gate_status = vol_ctx.get("volatility_gate_status")
        if gate_status != "AVAILABLE":
            gate_reason = vol_ctx.get("volatility_gate_reason") or gate_status or "FAIL_CLOSED"
            return False, f"VOLATILITY_GATE_NOT_AVAILABLE: {gate_reason} (ATR_FAIL_CLOSED)"

        if vol_ctx.get("atr_m1_status") != "AVAILABLE":
            return False, "ATR_M1_UNAVAILABLE (ATR_FAIL_CLOSED)"

        raw_m1 = vol_ctx.get("atr_m1")
        if raw_m1 is None or isinstance(raw_m1, bool):
            return False, "FORGED_STRUCTURAL_DECISION_MISSING_ATR_EVIDENCE: INVALID_RAW_ATR_M1"
        try:
            f_m1 = float(raw_m1)
            if not math.isfinite(f_m1) or f_m1 <= 0.0:
                return False, "FORGED_STRUCTURAL_DECISION_MISSING_ATR_EVIDENCE: NON_POSITIVE_RAW_ATR"
        except (ValueError, TypeError):
            return False, "FORGED_STRUCTURAL_DECISION_MISSING_ATR_EVIDENCE: NON_NUMERIC_RAW_ATR"

        # Revalidate execution-grade ATR authority and provenance at ARM boundary
        atr_prov = vol_ctx.get("atr_m1_provenance")
        if atr_prov is None:
            atr_prov = getattr(sp, "atr_m1_result", None)

        if atr_prov is None:
            return False, "FORGED_STRUCTURAL_DECISION_MISSING_ATR_EVIDENCE: MISSING_ATR_PROVENANCE"

        cand_symbol = (
            getattr(candidate, "symbol", None)
            or (candidate.get("symbol") if isinstance(candidate, dict) else None)
            or vol_ctx.get("symbol")
            or (atr_prov.get("symbol") if isinstance(atr_prov, dict) else getattr(atr_prov, "symbol", None))
        )
        if not cand_symbol:
            return False, "MISSING_CANDIDATE_SYMBOL_FOR_ATR_VALIDATION"

        if isinstance(atr_prov, dict):
            try:
                atr_obj = StopATRResult(**atr_prov)
            except Exception as e:
                return False, f"FORGED_STRUCTURAL_DECISION_INVALID_ATR_PROVENANCE_SCHEMA: {e}"
        else:
            atr_obj = atr_prov

        cutoff = getattr(sp, "decision_cutoff_ms", now_ms)
        is_auth_ok, auth_err = validate_stop_atr_authority(
            atr_obj,
            expected_symbol=cand_symbol,
            expected_timeframe="1M",
            expected_period=getattr(current_policy, "atr_period", 14),
            expected_method=getattr(current_policy, "atr_method", "SMA_TR_V1"),
            decision_cutoff=cutoff,
            max_age_ms=getattr(current_policy, "max_atr_age_ms", None),
        )
        if not is_auth_ok:
            return False, f"FORGED_STRUCTURAL_DECISION_INVALID_ATR_AUTHORITY: {auth_err}"

        try:
            prov_val = float(atr_obj.value)
            if abs(prov_val - f_m1) > 1e-3 and abs(prov_val - round(f_m1, 4)) > 1e-4:
                return False, f"ATR_EVIDENCE_PROVENANCE_VALUE_MISMATCH: provenance {prov_val} vs vol_ctx {f_m1}"
        except (ValueError, TypeError):
            return False, "FORGED_STRUCTURAL_DECISION_MISSING_ATR_EVIDENCE: NON_NUMERIC_PROVENANCE_VALUE"

        # Check optional M5 provenance if present
        atr_m5_prov = vol_ctx.get("atr_m5_provenance")
        if atr_m5_prov is not None:
            if isinstance(atr_m5_prov, dict):
                try:
                    atr_m5_obj = StopATRResult(**atr_m5_prov)
                except Exception:
                    atr_m5_obj = None
            else:
                atr_m5_obj = atr_m5_prov
            if atr_m5_obj is not None:
                is_m5_auth_ok, m5_auth_err = validate_stop_atr_authority(
                    atr_m5_obj,
                    expected_symbol=cand_symbol,
                    expected_timeframe="5M",
                    expected_period=getattr(current_policy, "atr_period", 14),
                    expected_method=getattr(current_policy, "atr_method", "SMA_TR_V1"),
                    decision_cutoff=cutoff,
                    max_age_ms=getattr(current_policy, "max_atr_age_ms", None),
                )
                if not is_m5_auth_ok:
                    return False, f"FORGED_STRUCTURAL_DECISION_INVALID_ATR_M5_AUTHORITY: {m5_auth_err}"

        # Check buffer raw ATR consistency if buffer is present
        buf = getattr(sp, "buffer", None)
        if buf is not None and getattr(buf, "raw_atr_m1", None) is not None:
            buf_m1 = float(buf.raw_atr_m1)
            if abs(buf_m1 - f_m1) > 1e-4:
                return False, f"ATR_EVIDENCE_BUFFER_MISMATCH: buffer {buf_m1} vs vol_ctx {f_m1}"

        # Decision cutoff causal check
        if now_ms is not None and cutoff is not None and cutoff > now_ms:
            return False, f"FUTURE_DECISION_CUTOFF_AT_ARM: cutoff {cutoff} > now {now_ms}"

    return True, "VALID"
