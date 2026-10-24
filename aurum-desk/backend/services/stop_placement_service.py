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


def compute_stop_atr(
    closed_bars: Sequence[Any],
    *,
    period: int = 14,
    timeframe: str = "1M",
    as_of_ms: Optional[int] = None,
    bar_duration_ms: int = 60_000,
) -> StopATRResult:
    """
    Computes strict True Range Mean (SMA_TR_V1) for stop loss buffer sizing.
    Requires at least `period + 1` continuous, finite closed bars.
    Excludes any unclosed bars or future bars after as_of_ms.
    """
    if period <= 0:
        return StopATRResult(
            status="UNAVAILABLE", value=0.0, period=period, timeframe=timeframe,
            method="SMA_TR_V1", last_closed_at_ms=0, bars_count=0
        )

    # Filter bars strictly closed at or before as_of_ms
    valid_bars: List[Any] = []
    for b in closed_bars:
        try:
            o, h, l, c = _candle_ohlc(b)
            if not all(math.isfinite(x) and x > 0 for x in (o, h, l, c)):
                continue
            t_open = _candle_open_ms(b)
            if as_of_ms is not None:
                t_close = t_open + bar_duration_ms
                if t_close > as_of_ms:
                    continue
            valid_bars.append(b)
        except Exception:
            continue

    if len(valid_bars) < period + 1:
        return StopATRResult(
            status="UNAVAILABLE",
            value=0.0,
            period=period,
            timeframe=timeframe,
            method="SMA_TR_V1",
            last_closed_at_ms=_candle_open_ms(valid_bars[-1]) + bar_duration_ms if valid_bars else 0,
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
    last_closed_ms = last_bar_open + bar_duration_ms

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

        if origin_price is None and confirmed_swings:
            sh_list, sl_list = confirmed_swings
            if norm_dir == "SHORT" and sh_list:
                eligible_sh = [s for s in sh_list if int(s.get("known_at_ms") or s.get("occurred_at_ms", 0)) <= as_of_ms]
                if eligible_sh:
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
                eligible_sl = [s for s in sl_list if int(s.get("known_at_ms") or s.get("occurred_at_ms", 0)) <= as_of_ms]
                if eligible_sl:
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
        if norm_dir == "SHORT":
            eligible_sh = [s for s in sh_list if int(s.get("known_at_ms") or s.get("occurred_at_ms", 0)) <= as_of_ms]
            if eligible_sh:
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
            eligible_sl = [s for s in sl_list if int(s.get("known_at_ms") or s.get("occurred_at_ms", 0)) <= as_of_ms]
            if eligible_sl:
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
        return StopPlacementDecision(
            status=StopStatus.WAITING_EVIDENCE.value,
            selected_sl=legacy_sl,
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
        return StopPlacementDecision(
            status=StopStatus.REJECTED_GEOMETRY.value,
            selected_sl=legacy_sl,
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
