"""
backend/services/smc_liquidity_service.py
V14.4 — Causal liquidity inventory, clustering, state tracking, and target selection.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple
from services.smc_scenario_types import LiquidityLevel, make_evidence_id


def cluster_equal_levels(
    swings: Sequence[Dict[str, Any]],
    side: str,                               # BUY_SIDE (highs) or SELL_SIDE (lows)
    tolerance_price: float,
    timeframe: str = "15M",
    symbol: str = "XAUUSDT",
) -> List[LiquidityLevel]:
    """Clusters confirmed swing levels within tolerance into EQH (equal highs) or EQL (equal lows).
    Only confirmed swings are used. known_at = max(source confirmed_at).
    """
    if len(swings) < 2 or tolerance_price <= 0:
        return []

    # Sort by price
    sorted_swings = sorted(swings, key=lambda s: s["price"])
    clusters: List[List[Dict[str, Any]]] = []
    current_cluster: List[Dict[str, Any]] = [sorted_swings[0]]

    for s in sorted_swings[1:]:
        if abs(s["price"] - current_cluster[-1]["price"]) <= tolerance_price:
            current_cluster.append(s)
        else:
            if len(current_cluster) >= 2:
                clusters.append(current_cluster)
            current_cluster = [s]
    if len(current_cluster) >= 2:
        clusters.append(current_cluster)

    eq_levels: List[LiquidityLevel] = []
    kind = "EQH" if side == "BUY_SIDE" else "EQL"

    for idx, cl in enumerate(clusters):
        avg_p = round(sum(s["price"] for s in cl) / len(cl), 2)
        source_ids = [s.get("id", f"sw-{s['price']}") for s in cl]
        max_known = max(s.get("confirmed_at", s.get("timestamp", 0)) for s in cl)
        min_occ = min(s.get("timestamp", 0) for s in cl)
        label = f"Đỉnh đôi ({len(cl)} đỉnh)" if kind == "EQH" else f"Đáy đôi ({len(cl)} đáy)"
        eq_id = make_evidence_id(symbol, timeframe, kind, f"{avg_p}-{idx}")

        eq_levels.append(
            LiquidityLevel(
                id=eq_id,
                price=avg_p,
                side=side,
                source_kind=kind,
                occurred_at_ms=min_occ,
                known_at_ms=max_known,
                source_ids=source_ids,
                state="UNTOUCHED",
                label_vi=label,
                tolerance_price=tolerance_price,
            )
        )

    return eq_levels


def build_liquidity_map(
    *,
    symbol: str = "XAUUSDT",
    timeframe: str = "15M",
    swing_highs: Sequence[Dict[str, Any]],
    swing_lows: Sequence[Dict[str, Any]],
    session_ranges: Optional[Dict[str, Any]] = None,
    previous_day: Optional[Any] = None,
    tolerance_atr: float = 0.25,
    atr: float = 2.0,
    now_ms: int,
) -> List[LiquidityLevel]:
    """Builds comprehensive causal liquidity map from swings, equal levels, session extremes, and PDH/PDL."""
    levels: List[LiquidityLevel] = []
    tol = max(0.20, tolerance_atr * atr)

    # 1. Confirmed Swing Highs (Buy-side)
    for sh in swing_highs:
        if sh.get("confirmed_at", sh.get("timestamp", 0)) <= now_ms:
            levels.append(
                LiquidityLevel(
                    id=sh.get("id", f"sh-{sh['price']}"),
                    price=float(sh["price"]),
                    side="BUY_SIDE",
                    source_kind="SWING",
                    occurred_at_ms=sh.get("timestamp", 0),
                    known_at_ms=sh.get("confirmed_at", sh.get("timestamp", 0)),
                    source_ids=[sh.get("id", f"sh-{sh['price']}")],
                    label_vi=f"Đỉnh swing {sh['price']:.2f}",
                )
            )

    # 2. Confirmed Swing Lows (Sell-side)
    for sl in swing_lows:
        if sl.get("confirmed_at", sl.get("timestamp", 0)) <= now_ms:
            levels.append(
                LiquidityLevel(
                    id=sl.get("id", f"sl-{sl['price']}"),
                    price=float(sl["price"]),
                    side="SELL_SIDE",
                    source_kind="SWING",
                    occurred_at_ms=sl.get("timestamp", 0),
                    known_at_ms=sl.get("confirmed_at", sl.get("timestamp", 0)),
                    source_ids=[sl.get("id", f"sl-{sl['price']}")],
                    label_vi=f"Đáy swing {sl['price']:.2f}",
                )
            )

    # 3. Equal Highs and Lows (Clustered)
    eq_highs = cluster_equal_levels(swing_highs, "BUY_SIDE", tol, timeframe, symbol)
    levels.extend(eq_highs)
    eq_lows = cluster_equal_levels(swing_lows, "SELL_SIDE", tol, timeframe, symbol)
    levels.extend(eq_lows)

    # 4. Session Levels (Asia, London)
    if session_ranges:
        for s_name, s_range in session_ranges.items():
            if s_range.high is not None and s_range.start_ms <= now_ms:
                levels.append(
                    LiquidityLevel(
                        id=f"liq-{s_name.lower()}-high-{s_range.high}",
                        price=s_range.high,
                        side="BUY_SIDE",
                        source_kind=f"SESSION_{s_name.upper()}",
                        occurred_at_ms=s_range.high_time_ms or s_range.start_ms,
                        known_at_ms=s_range.end_ms if s_range.is_complete else now_ms,
                        label_vi=f"Đỉnh phiên {s_name} {s_range.high:.2f}",
                    )
                )
            if s_range.low is not None and s_range.start_ms <= now_ms:
                levels.append(
                    LiquidityLevel(
                        id=f"liq-{s_name.lower()}-low-{s_range.low}",
                        price=s_range.low,
                        side="SELL_SIDE",
                        source_kind=f"SESSION_{s_name.upper()}",
                        occurred_at_ms=s_range.low_time_ms or s_range.start_ms,
                        known_at_ms=s_range.end_ms if s_range.is_complete else now_ms,
                        label_vi=f"Đáy phiên {s_name} {s_range.low:.2f}",
                    )
                )

    # 5. Previous Day Levels (PDH, PDL)
    if previous_day and getattr(previous_day, "is_valid", False):
        if previous_day.pdh is not None and previous_day.known_at_ms <= now_ms:
            levels.append(
                LiquidityLevel(
                    id=f"liq-pdh-{previous_day.pdh}",
                    price=previous_day.pdh,
                    side="BUY_SIDE",
                    source_kind="PDH",
                    occurred_at_ms=previous_day.known_at_ms - 86_400_000,
                    known_at_ms=previous_day.known_at_ms,
                    label_vi=f"Đỉnh ngày trước (PDH) {previous_day.pdh:.2f}",
                )
            )
        if previous_day.pdl is not None and previous_day.known_at_ms <= now_ms:
            levels.append(
                LiquidityLevel(
                    id=f"liq-pdl-{previous_day.pdl}",
                    price=previous_day.pdl,
                    side="SELL_SIDE",
                    source_kind="PDL",
                    occurred_at_ms=previous_day.known_at_ms - 86_400_000,
                    known_at_ms=previous_day.known_at_ms,
                    label_vi=f"Đáy ngày trước (PDL) {previous_day.pdl:.2f}",
                )
            )

    return levels


def update_liquidity_state(
    level: LiquidityLevel,
    causal_candles: Sequence[Any],
    now_ms: int,
    candle_duration_ms: int = 300_000,
) -> str:
    """Updates level state by scanning causal closed candles between level.known_at_ms and now_ms.
    State transitions: UNTOUCHED -> APPROACHED / SWEPT / BROKEN -> CONSUMED.
    Does NOT revert once swept or broken.
    """
    if level.state in ("BROKEN", "CONSUMED"):
        return level.state

    subsequent_bars = [
        c for c in causal_candles
        if c.timestamp >= level.known_at_ms
        and c.timestamp + candle_duration_ms <= now_ms
        and getattr(c, "is_closed", True)
    ]

    for c in subsequent_bars:
        if level.side == "BUY_SIDE":
            if c.close > level.price:
                level.state = "BROKEN"
                return level.state
            if c.high > level.price and c.close <= level.price:
                level.state = "SWEPT"
        elif level.side == "SELL_SIDE":
            if c.close < level.price:
                level.state = "BROKEN"
                return level.state
            if c.low < level.price and c.close >= level.price:
                level.state = "SWEPT"

    return level.state


def select_observation_anchor(
    levels: Sequence[LiquidityLevel],
    direction: Optional[str],
    current_price: Optional[float],
) -> Optional[LiquidityLevel]:
    """Selects primary liquidity anchor being watched.
    - For LONG: watches SELL_SIDE liquidity below current price (candidates to be swept).
    - For SHORT: watches BUY_SIDE liquidity above current price (candidates to be swept).
    - If direction is None, selects nearest un-broken liquidity on either side.
    """
    if not levels or current_price is None or current_price <= 0:
        return None

    if direction == "LONG":
        candidates = [l for l in levels if l.side == "SELL_SIDE" and l.price <= current_price and l.state != "BROKEN"]
        if candidates:
            # Nearest below current price
            return max(candidates, key=lambda l: l.price)
    elif direction == "SHORT":
        candidates = [l for l in levels if l.side == "BUY_SIDE" and l.price >= current_price and l.state != "BROKEN"]
        if candidates:
            # Nearest above current price
            return min(candidates, key=lambda l: l.price)
    else:
        candidates = [l for l in levels if l.state != "BROKEN"]
        if candidates:
            return min(candidates, key=lambda l: abs(l.price - current_price))

    return None


def select_unconsumed_target(
    levels: Sequence[LiquidityLevel],
    direction: str,
    entry_price: float,
) -> Optional[LiquidityLevel]:
    """Selects valid unconsumed target liquidity level on the correct side of entry.
    - For LONG: BUY_SIDE liquidity with price > entry_price.
    - For SHORT: SELL_SIDE liquidity with price < entry_price.
    - Target must NOT be BROKEN or CONSUMED.
    """
    if not levels or entry_price <= 0:
        return None

    if direction == "LONG":
        candidates = [
            l for l in levels
            if l.side == "BUY_SIDE"
            and l.price > entry_price
            and l.state in ("UNTOUCHED", "APPROACHED")
        ]
        if candidates:
            # Closest target above entry (nearest structural objective)
            return min(candidates, key=lambda l: l.price)
    elif direction == "SHORT":
        candidates = [
            l for l in levels
            if l.side == "SELL_SIDE"
            and l.price < entry_price
            and l.state in ("UNTOUCHED", "APPROACHED")
        ]
        if candidates:
            # Closest target below entry
            return max(candidates, key=lambda l: l.price)

    return None


def rank_secondary_targets(
    levels: Sequence[LiquidityLevel],
    direction: str,
    primary_target: Optional[LiquidityLevel],
    entry_price: float,
) -> List[LiquidityLevel]:
    """Returns ranked secondary liquidity targets on the same side beyond the primary target."""
    if not levels or primary_target is None or entry_price <= 0:
        return []

    if direction == "LONG":
        return sorted(
            [l for l in levels if l.side == "BUY_SIDE" and l.price > primary_target.price and l.id != primary_target.id and l.state in ("UNTOUCHED", "APPROACHED")],
            key=lambda l: l.price
        )[:3]
    elif direction == "SHORT":
        return sorted(
            [l for l in levels if l.side == "SELL_SIDE" and l.price < primary_target.price and l.id != primary_target.id and l.state in ("UNTOUCHED", "APPROACHED")],
            key=lambda l: -l.price
        )[:3]

    return []
