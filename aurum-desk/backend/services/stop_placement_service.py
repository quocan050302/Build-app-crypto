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
        origin_price = selected_fvg.get("origin_price") or selected_fvg.get("leg_origin_price")
        break_id = selected_break.get("id") if selected_break else None

        leg_cutoff_ms = None
        if selected_break:
            leg_cutoff_ms = (
                selected_break.get("broken_at_ms")
                or selected_break.get("break_time_ms")
                or selected_break.get("timestamp")
                or selected_break.get("break_open_ms")
            )
        if leg_cutoff_ms is None and selected_fvg:
            leg_cutoff_ms = (
                selected_fvg.get("start_ms")
                or selected_fvg.get("formation_time_ms")
                or selected_fvg.get("timestamp")
            )
        if leg_cutoff_ms is not None:
            leg_cutoff_ms = int(leg_cutoff_ms)

        if origin_price is None and confirmed_swings:
            sh_list, sl_list = confirmed_swings
            if norm_dir == "SHORT" and sh_list:
                eligible_sh = [
                    s for s in sh_list
                    if int(s.get("known_at_ms") or s.get("occurred_at_ms", 0)) <= as_of_ms
                    and (leg_cutoff_ms is None or int(s.get("occurred_at_ms", 0)) <= leg_cutoff_ms)
                ]
                if eligible_sh:
                    eligible_sh.sort(key=lambda s: (int(s.get("occurred_at_ms", 0)), int(s.get("known_at_ms", 0)), str(s.get("id", ""))))
                    chosen_sh = eligible_sh[-1]
                    origin_price = float(chosen_sh["price"])
                    anchor = StopAnchor(
                        id=f"anc_fvg_sh_{chosen_sh.get('id', as_of_ms)}",
                        kind=StopAnchorKind.PROTECTED_LEG_HIGH.value,
                        price=origin_price,
                        timeframe="1M",
                        occurred_at_ms=int(chosen_sh.get("occurred_at_ms", as_of_ms)),
                        known_at_ms=int(chosen_sh.get("known_at_ms", as_of_ms)),
                        source_leg_id=fvg_id,
                        source_break_id=break_id,
                        price_basis=PriceBasis.CANDLE_LAST.value,
                    )
                    if not validate_anchor_not_invalidated(anchor, closed_bars, norm_dir, as_of_ms):
                        return StopAnchorResult(
                            status="INVALID_DATA",
                            anchor=None,
                            reasons=("ANCHOR_ALREADY_INVALIDATED",),
                        )
                    return StopAnchorResult(status="AVAILABLE", anchor=anchor)
            elif norm_dir == "LONG" and sl_list:
                eligible_sl = [
                    s for s in sl_list
                    if int(s.get("known_at_ms") or s.get("occurred_at_ms", 0)) <= as_of_ms
                    and (leg_cutoff_ms is None or int(s.get("occurred_at_ms", 0)) <= leg_cutoff_ms)
                ]
                if eligible_sl:
                    eligible_sl.sort(key=lambda s: (int(s.get("occurred_at_ms", 0)), int(s.get("known_at_ms", 0)), str(s.get("id", ""))))
                    chosen_sl = eligible_sl[-1]
                    origin_price = float(chosen_sl["price"])
                    anchor = StopAnchor(
                        id=f"anc_fvg_sl_{chosen_sl.get('id', as_of_ms)}",
                        kind=StopAnchorKind.PROTECTED_LEG_LOW.value,
                        price=origin_price,
                        timeframe="1M",
                        occurred_at_ms=int(chosen_sl.get("occurred_at_ms", as_of_ms)),
                        known_at_ms=int(chosen_sl.get("known_at_ms", as_of_ms)),
                        source_leg_id=fvg_id,
                        source_break_id=break_id,
                        price_basis=PriceBasis.CANDLE_LAST.value,
                    )
                    if not validate_anchor_not_invalidated(anchor, closed_bars, norm_dir, as_of_ms):
                        return StopAnchorResult(
                            status="INVALID_DATA",
                            anchor=None,
                            reasons=("ANCHOR_ALREADY_INVALIDATED",),
                        )
                    return StopAnchorResult(status="AVAILABLE", anchor=anchor)

    # 3. Continuation (M1 Trend BOS)
    if confirmed_swings:
        sh_list, sl_list = confirmed_swings
        break_id = selected_break.get("id") if selected_break else None
        leg_cutoff_ms = None
        if selected_break:
            leg_cutoff_ms = (
                selected_break.get("broken_at_ms")
                or selected_break.get("break_time_ms")
                or selected_break.get("timestamp")
                or selected_break.get("break_open_ms")
            )
        if leg_cutoff_ms is not None:
            leg_cutoff_ms = int(leg_cutoff_ms)

        if norm_dir == "SHORT":
            eligible_sh = [
                s for s in sh_list
                if int(s.get("known_at_ms") or s.get("occurred_at_ms", 0)) <= as_of_ms
                and (leg_cutoff_ms is None or int(s.get("occurred_at_ms", 0)) <= leg_cutoff_ms)
            ]
            if eligible_sh:
                eligible_sh.sort(key=lambda s: (int(s.get("occurred_at_ms", 0)), int(s.get("known_at_ms", 0)), str(s.get("id", ""))))
                chosen_sh = eligible_sh[-1]
                anchor = StopAnchor(
                    id=f"anc_cont_sh_{chosen_sh.get('id', as_of_ms)}",
                    kind=StopAnchorKind.PROTECTED_LEG_HIGH.value,
                    price=float(chosen_sh["price"]),
                    timeframe="1M",
                    occurred_at_ms=int(chosen_sh.get("occurred_at_ms", as_of_ms)),
                    known_at_ms=int(chosen_sh.get("known_at_ms", as_of_ms)),
                    source_leg_id=chosen_sh.get("id", f"leg_{as_of_ms}"),
                    source_break_id=break_id,
                    price_basis=PriceBasis.CANDLE_LAST.value,
                )
                if not validate_anchor_not_invalidated(anchor, closed_bars, norm_dir, as_of_ms):
                    return StopAnchorResult(
                        status="INVALID_DATA",
                        anchor=None,
                        reasons=("ANCHOR_ALREADY_INVALIDATED",),
                    )
                return StopAnchorResult(status="AVAILABLE", anchor=anchor)
        elif norm_dir == "LONG":
            eligible_sl = [
                s for s in sl_list
                if int(s.get("known_at_ms") or s.get("occurred_at_ms", 0)) <= as_of_ms
                and (leg_cutoff_ms is None or int(s.get("occurred_at_ms", 0)) <= leg_cutoff_ms)
            ]
            if eligible_sl:
                eligible_sl.sort(key=lambda s: (int(s.get("occurred_at_ms", 0)), int(s.get("known_at_ms", 0)), str(s.get("id", ""))))
                chosen_sl = eligible_sl[-1]
                anchor = StopAnchor(
                    id=f"anc_cont_sl_{chosen_sl.get('id', as_of_ms)}",
                    kind=StopAnchorKind.PROTECTED_LEG_LOW.value,
                    price=float(chosen_sl["price"]),
                    timeframe="1M",
                    occurred_at_ms=int(chosen_sl.get("occurred_at_ms", as_of_ms)),
                    known_at_ms=int(chosen_sl.get("known_at_ms", as_of_ms)),
                    source_leg_id=chosen_sl.get("id", f"leg_{as_of_ms}"),
                    source_break_id=break_id,
                    price_basis=PriceBasis.CANDLE_LAST.value,
                )
                if not validate_anchor_not_invalidated(anchor, closed_bars, norm_dir, as_of_ms):
                    return StopAnchorResult(
                        status="INVALID_DATA",
                        anchor=None,
                        reasons=("ANCHOR_ALREADY_INVALIDATED",),
                    )
                return StopAnchorResult(status="AVAILABLE", anchor=anchor)

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
) -> Optional[Any]:
    """
    Verifies and constructs a SelectedStopLeg descriptor binding the protective pivot
    to the exact leg and break bar that created the opportunity.
    """
    from services.stop_placement_types import SelectedStopLeg

    if not selected_break:
        return None

    cutoff = decision_cutoff_ms or as_of_ms or 0
    break_time = int(
        selected_break.get("broken_at_ms")
        or selected_break.get("break_time_ms")
        or selected_break.get("timestamp")
        or selected_break.get("break_open_ms")
        or (break_bar.open_time if break_bar and hasattr(break_bar, "open_time") else cutoff)
    )

    if candidate_pivots and not candidate_pivot:
        valid_candidates = []
        for p in candidate_pivots:
            p_occ = int(p.get("occurred_at_ms", 0))
            p_known = int(p.get("known_at_ms", p_occ))
            if p_occ <= break_time and (cutoff == 0 or p_known <= cutoff):
                valid_candidates.append(p)
        if valid_candidates:
            # Deterministic selection: closest before break, tie-break known_at, then id
            valid_candidates.sort(
                key=lambda p: (int(p.get("occurred_at_ms", 0)), int(p.get("known_at_ms", 0)), str(p.get("id", ""))),
                reverse=True,
            )
            candidate_pivot = valid_candidates[0]

    if not candidate_pivot:
        return None

    pivot_occ = int(candidate_pivot.get("occurred_at_ms", 0))
    pivot_known = int(candidate_pivot.get("known_at_ms", pivot_occ))

    # Invariant: Origin pivot must occur before the break
    if pivot_occ > break_time or (cutoff > 0 and pivot_known > cutoff):
        return None

    broken_ref_price = (
        selected_break.get("broken_price")
        if selected_break.get("broken_price") is not None
        else selected_break.get("reference_price")
    )

    return SelectedStopLeg(
        model_id=model_id,
        direction=direction,
        instance_id=instance_id,
        leg_id=str(selected_break.get("id") or f"leg_{break_time}"),
        origin_pivot_id=str(candidate_pivot.get("id", "")),
        origin_price=float(candidate_pivot.get("price", 0.0)),
        origin_occurred_at_ms=pivot_occ,
        origin_known_at_ms=pivot_known,
        broken_reference_id=selected_break.get("broken_reference_id"),
        broken_reference_price=float(broken_ref_price) if broken_ref_price is not None else None,
        break_bar_id=str(selected_break.get("id", "")),
        break_open_ms=break_time,
        break_known_at_ms=int(selected_break.get("known_at_ms", break_time)),
        fvg_id=selected_fvg.get("id") if selected_fvg else None,
        fvg_middle_bar_ms=selected_fvg.get("middle_bar_ms") if selected_fvg else None,
        fvg_confirmed_at_ms=selected_fvg.get("confirmed_at") if selected_fvg else None,
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
