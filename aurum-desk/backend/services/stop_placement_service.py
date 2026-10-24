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


TIMEFRAME_MS: Dict[str, int] = {
    "1M": 60_000,
    "5M": 300_000,
    "15M": 900_000,
    "1H": 3_600_000,
}


def compute_stop_atr(
    closed_bars: Sequence[Any],
    *,
    period: int = 14,
    timeframe: str = "1M",
    as_of_ms: Optional[int] = None,
    bar_duration_ms: Optional[int] = None,
) -> StopATRResult:
    """
    Computes strict True Range Mean (SMA_TR_V1) for stop loss buffer sizing.
    Requires at least `period + 1` continuous, finite closed bars.
    Excludes any unclosed bars or future bars after as_of_ms.
    Strictly synchronizes bar_duration_ms with timeframe.
    """
    norm_tf = timeframe.upper()
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
        )

    duration = expected_duration

    if period <= 0:
        return StopATRResult(
            status="UNAVAILABLE",
            value=0.0,
            period=period,
            timeframe=timeframe,
            method="SMA_TR_V1",
            last_closed_at_ms=0,
            bars_count=0,
            reason_code="INVALID_PERIOD",
        )

    # Filter bars strictly closed at or before as_of_ms with increasing timestamps and valid OHLC
    valid_bars: List[Any] = []
    last_ts = -1
    for b in closed_bars:
        try:
            o, h, l, c = _candle_ohlc(b)
            if not all(math.isfinite(x) and x > 0 for x in (o, h, l, c)):
                continue
            if not (l <= min(o, c) <= max(o, c) <= h and h >= l):
                continue
            t_open = _candle_open_ms(b)
            if t_open <= last_ts:
                # Disallow duplicate or out-of-order timestamps in active window
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
        )

    # Compute True Range across the slice
    tr_values: List[float] = []
    for i in range(1, len(valid_bars)):
        _, h_curr, l_curr, _ = _candle_ohlc(valid_bars[i])
        _, _, _, c_prev = _candle_ohlc(valid_bars[i - 1])
        tr = max(h_curr - l_curr, abs(h_curr - c_prev), abs(l_curr - c_prev))
        tr_values.append(tr)

    # Take the last `period` TR values
    target_tr = tr_values[-period:]
    atr_val = sum(target_tr) / float(period)

    last_bar_open = _candle_open_ms(valid_bars[-1])
    last_closed_ms = last_bar_open + duration

    return StopATRResult(
        status="AVAILABLE",
        value=float(atr_val),
        period=period,
        timeframe=timeframe,
        method="SMA_TR_V1",
        last_closed_at_ms=last_closed_ms,
        bars_count=len(valid_bars),
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
    atr_m1: StopATRResult,
    atr_m5: Optional[StopATRResult] = None,
    bid: float,
    ask: float,
) -> StopBufferResult:
    """
    Computes candidate dynamic buffer components according to StopPlacementPolicy:
    buffer = max(tick_floor, k_m1*ATR_M1, k_m5*ATR_M5 (if available), k_spread*(ask-bid))
    Records individual component values and identifies the dominant source.
    """
    tick_floor = float(policy.min_buffer_ticks) * float(tick_size)
    components: Dict[str, float] = {
        "tick_floor": tick_floor,
    }

    if atr_m1 and atr_m1.status == "AVAILABLE" and atr_m1.value > 0:
        components["atr_m1"] = float(policy.atr_m1_multiplier) * float(atr_m1.value)

    spread = max(0.0, float(ask) - float(bid))
    if spread > 0:
        components["spread"] = float(policy.spread_multiplier) * spread

    if atr_m5 and atr_m5.status == "AVAILABLE" and atr_m5.value > 0:
        components["atr_m5"] = float(policy.atr_m5_multiplier) * float(atr_m5.value)

    max_buf = max(components.values())
    dominant = max(components, key=components.get)

    return StopBufferResult(
        buffer_price=max_buf,
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
) -> StopAnchorResult:
    """
    Identifies the model-specific structural protective anchor.
    - Continuation: Protective swing high (for SHORT) or low (for LONG) originating the break leg.
    - FVG Continuation: Protected origin extreme of the leg producing the break and FVG.
    - Sweep Reclaim / Reversal / CG01: True extreme of the swept episode.
    """
    norm_dir = direction.upper()

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
            if bound_leg is None:
                return StopAnchorResult(
                    status="WAITING_EVIDENCE",
                    anchor=None,
                    reasons=("STRUCTURAL_ANCHOR_NOT_FOUND",),
                )

            anchor = StopAnchor(
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
            if not validate_anchor_not_invalidated(anchor, closed_bars, norm_dir, as_of_ms):
                return StopAnchorResult(
                    status="INVALID_DATA",
                    anchor=None,
                    reasons=("ANCHOR_ALREADY_INVALIDATED",),
                )
            return StopAnchorResult(status="AVAILABLE", anchor=anchor, bound_leg=bound_leg)

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
    anchor_result: StopAnchorResult,
    buffer_result: StopBufferResult,
    policy: StopPlacementPolicy,
    as_of_ms: int,
    tick_size: float = 0.01,
    atr_m1_val: Optional[float] = None,
) -> StopPlacementDecision:
    """
    Synthesizes the structural stop placement decision.
    Calculates Variant B (anchor + tick floor) and Variant C (anchor + dynamic buffer).
    Enforces strict price geometry and non-negative distance checks.
    """
    norm_dir = direction.upper()
    mode = policy.mode

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
                distance_atr=(dist / atr_m1_val) if atr_m1_val and atr_m1_val > 0 else None,
            ),
            reasons=(),
            policy_version=policy.version,
            mode=mode,
            decision_cutoff_ms=as_of_ms,
            applied_variant="A",
        )

    if anchor_result.status != "AVAILABLE" or anchor_result.anchor is None:
        dist = abs(planned_entry - legacy_sl)
        sel_sl = None if mode == StopPlacementMode.PAPER_STRUCTURAL.value else legacy_sl
        return StopPlacementDecision(
            status=StopStatus.WAITING_EVIDENCE.value,
            selected_sl=sel_sl,
            anchor=None,
            bound_leg=None,
            buffer=buffer_result,
            metrics=StopMetrics(
                price_distance=dist,
                distance_ticks=int(round(dist / tick_size)),
                distance_atr=(dist / atr_m1_val) if atr_m1_val and atr_m1_val > 0 else None,
            ),
            reasons=anchor_result.reasons or ("MISSING_STRUCTURAL_ANCHOR",),
            policy_version=policy.version,
            mode=mode,
            decision_cutoff_ms=as_of_ms,
            applied_variant="A",
        )

    anchor = anchor_result.anchor

    # Variant C stop: anchor outward by full dynamic buffer
    variant_c_sl = quantize_stop_outward(
        anchor_price=anchor.price,
        buffer_price=buffer_result.buffer_price,
        direction=norm_dir,
        tick_size=tick_size,
    )

    # Geometry validation for Variant C
    if norm_dir == "SHORT":
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
            buffer=buffer_result,
            metrics=StopMetrics(
                price_distance=dist,
                distance_ticks=int(round(dist / tick_size)),
                distance_atr=(dist / atr_m1_val) if atr_m1_val and atr_m1_val > 0 else None,
            ),
            reasons=("INVALID_PRICE_GEOMETRY_WITH_STRUCTURAL_SL",),
            policy_version=policy.version,
            mode=mode,
            decision_cutoff_ms=as_of_ms,
            applied_variant="A",
        )

    # Selected SL according to mode
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
        distance_atr=(dist / atr_m1_val) if atr_m1_val and atr_m1_val > 0 else None,
        delta_vs_legacy_sl=abs(selected_sl - legacy_sl),
    )

    return StopPlacementDecision(
        status=StopStatus.VALID.value,
        selected_sl=selected_sl,
        anchor=anchor,
        bound_leg=getattr(anchor_result, "bound_leg", None),
        buffer=buffer_result,
        metrics=metrics,
        reasons=(),
        policy_version=policy.version,
        mode=mode,
        decision_cutoff_ms=as_of_ms,
        applied_variant=applied_variant,
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


def validate_protected_origin_binding(
    *,
    origin: Dict[str, Any],
    direction: str,
    break_open_ms: int,
    decision_cutoff_ms: int = 0,
    closed_bars: Sequence[Any] = (),
    tick_size: float = 0.01,
    episode_start_ms: Optional[int] = None,
) -> Tuple[bool, Optional[str]]:
    """
    AURUM V17.5.6 (Appendix A4, C5):
    Pure validation helper verifying pivot source candle, extreme price,
    time boundaries, and episode membership.
    """
    if not closed_bars:
        return False, "MISSING_SOURCE_CANDLES"

    norm_dir = direction.upper()
    tol = float(tick_size) if tick_size > 0 else 0.01
    p_occ = int(origin.get("occurred_at_ms", 0) or origin.get("timestamp", 0))
    p_known = int(origin.get("known_at_ms", origin.get("confirmed_at", p_occ)))
    p_px = float(origin.get("price", 0.0))

    if p_occ >= break_open_ms:
        return False, "ORIGIN_NOT_BEFORE_BREAK"

    if decision_cutoff_ms > 0 and p_known > decision_cutoff_ms:
        return False, "ORIGIN_KNOWN_AFTER_DECISION"

    if episode_start_ms is not None and p_occ < episode_start_ms:
        return False, "ORIGIN_OUTSIDE_EPISODE"

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
    AURUM V17.5.6 (Appendix C1-C3):
    Derives the displacement break episode that created the opportunity.
    Enforces independent reference/break boundaries, source candle verification,
    and returns eligible origin IDs within the leg.
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

    # 1. Resolve break bar and timestamps
    break_bar = None
    b_open = None
    break_known_ms = None
    break_bar_id = None

    if isinstance(selected_break, dict):
        break_bar = selected_break.get("bar")
        b_open = selected_break.get("break_open_ms")
        if b_open is None and break_bar:
            b_open = _candle_open_ms(break_bar)
        if b_open is None:
            b_open = selected_break.get("broken_at_ms") or selected_break.get("break_time_ms") or selected_break.get("timestamp")

        b_known_raw = selected_break.get("known_at_ms")
        if b_known_raw is not None:
            break_known_ms = int(b_known_raw)
        elif selected_break.get("broken_at_ms") and int(selected_break["broken_at_ms"]) > int(b_open or 0):
            break_known_ms = int(selected_break["broken_at_ms"])
        else:
            break_known_ms = int(b_open or 0) + timeframe_ms

        break_bar_id = selected_break.get("id") or selected_break.get("break_bar_id")
    else:
        break_bar = selected_break
        b_open = _candle_open_ms(selected_break)
        break_known_ms = int(b_open or 0) + timeframe_ms

    if b_open is None or int(b_open) <= 0:
        return ProtectedLegEpisode(
            status="UNAVAILABLE",
            model_id=model_id,
            direction=norm_dir,
            decision_cutoff_ms=cutoff,
            reasons=("MISSING_BREAK_TIME",),
        )

    b_open_ms = int(b_open)

    if cutoff > 0:
        if b_open_ms > cutoff or break_known_ms > cutoff:
            return ProtectedLegEpisode(
                status="UNAVAILABLE",
                model_id=model_id,
                direction=norm_dir,
                break_open_ms=b_open_ms,
                break_known_at_ms=break_known_ms,
                decision_cutoff_ms=cutoff,
                reasons=("BREAK_AFTER_DECISION_CUTOFF",),
            )

    # 2. Reference validation and strict close-through
    ref_id = None
    ref_price = None
    ref_occ = None
    ref_known = None
    if selected_reference:
        ref_id = selected_reference.get("id") or selected_reference.get("swing_ref_id")
        p = selected_reference.get("price") if selected_reference.get("price") is not None else selected_reference.get("level")
        if p is not None:
            try:
                ref_price = float(p)
            except (ValueError, TypeError):
                ref_price = None
        ref_occ = selected_reference.get("occurred_at_ms") or selected_reference.get("timestamp")
        ref_known = selected_reference.get("known_at_ms") or selected_reference.get("confirmed_at")

        if ref_occ is not None and int(ref_occ) > b_open_ms:
            return ProtectedLegEpisode(
                status="UNAVAILABLE",
                model_id=model_id,
                direction=norm_dir,
                reference_id=str(ref_id) if ref_id else None,
                break_open_ms=b_open_ms,
                decision_cutoff_ms=cutoff,
                reasons=("REFERENCE_AFTER_BREAK",),
            )
        if ref_known is not None and cutoff > 0 and int(ref_known) > cutoff:
            return ProtectedLegEpisode(
                status="UNAVAILABLE",
                model_id=model_id,
                direction=norm_dir,
                reference_id=str(ref_id) if ref_id else None,
                decision_cutoff_ms=cutoff,
                reasons=("REFERENCE_KNOWN_AFTER_DECISION",),
            )

        # Strict close-through check (no equality, no wick-only)
        if break_bar and ref_price is not None:
            try:
                _, _, _, b_close = _candle_ohlc(break_bar)
                if norm_dir in ("SHORT", "SELL") and b_close >= ref_price:
                    return ProtectedLegEpisode(
                        status="UNAVAILABLE",
                        model_id=model_id,
                        direction=norm_dir,
                        reference_id=str(ref_id) if ref_id else None,
                        reference_price=ref_price,
                        break_open_ms=b_open_ms,
                        decision_cutoff_ms=cutoff,
                        reasons=("BREAK_NOT_CLOSED_THROUGH_REFERENCE",),
                    )
                elif norm_dir in ("LONG", "BUY") and b_close <= ref_price:
                    return ProtectedLegEpisode(
                        status="UNAVAILABLE",
                        model_id=model_id,
                        direction=norm_dir,
                        reference_id=str(ref_id) if ref_id else None,
                        reference_price=ref_price,
                        break_open_ms=b_open_ms,
                        decision_cutoff_ms=cutoff,
                        reasons=("BREAK_NOT_CLOSED_THROUGH_REFERENCE",),
                    )
            except Exception:
                pass

    # 3. Determine episode bounds and eligible origin candidates
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

    episode_start_boundary = None
    if isinstance(selected_break, dict) and selected_break.get("episode_start_ms"):
        episode_start_boundary = int(selected_break["episode_start_ms"])
    elif selected_reference and selected_reference.get("episode_start_ms"):
        episode_start_boundary = int(selected_reference["episode_start_ms"])

    eligible = []
    for p in pivots:
        is_valid, _ = validate_protected_origin_binding(
            origin=p,
            direction=norm_dir,
            break_open_ms=b_open_ms,
            decision_cutoff_ms=cutoff,
            closed_bars=closed_bars,
            tick_size=tol,
            episode_start_ms=episode_start_boundary,
        )
        if is_valid:
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
                reference_occurred_at_ms=int(ref_occ) if ref_occ is not None else None,
                reference_known_at_ms=int(ref_known) if ref_known is not None else None,
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
            reference_occurred_at_ms=int(ref_occ) if ref_occ is not None else None,
            reference_known_at_ms=int(ref_known) if ref_known is not None else None,
            break_bar_id=str(break_bar_id) if break_bar_id else f"break_{b_open_ms}",
            break_open_ms=b_open_ms,
            break_known_at_ms=break_known_ms,
            decision_cutoff_ms=cutoff,
            reasons=("NO_VALID_PROTECTED_ORIGIN_IN_EPISODE",),
        )

    # Sort deterministically: closest before break, tie-break known_at, then id
    eligible.sort(
        key=lambda c: (
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
        reference_occurred_at_ms=int(ref_occ) if ref_occ is not None else None,
        reference_known_at_ms=int(ref_known) if ref_known is not None else None,
        break_bar_id=str(break_bar_id) if break_bar_id else f"break_{b_open_ms}",
        break_open_ms=b_open_ms,
        break_known_at_ms=break_known_ms,
        decision_cutoff_ms=cutoff,
        episode_start_ms=episode_start_boundary if episode_start_boundary is not None else best_occ,
        episode_end_ms=break_known_ms,
        eligible_origin_ids=tuple(str(p.get("id", "")) for p in eligible),
        source_bar_ids=tuple(source_ids),
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
) -> Tuple[bool, str]:
    """
    Fail-closed barrier invoked at evaluator completion and ARM critical section.
    Rejects any trade whose stop loss does not satisfy current policy requirements.
    """
    cand_sl = getattr(candidate, "stop_loss", None)
    calc = getattr(candidate, "calculation", None)
    sp = getattr(candidate, "stop_placement", None)

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

    return True, "VALID"
