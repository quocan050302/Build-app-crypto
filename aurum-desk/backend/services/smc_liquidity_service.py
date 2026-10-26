"""
backend/services/smc_liquidity_service.py
V14.4 — Causal liquidity inventory, clustering, state tracking, and target selection.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple
from services.smc_scenario_types import LiquidityLevel, LiquidityCluster, make_evidence_id


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
        max_known = max(s.get("known_at_ms", s.get("confirmed_at", s.get("timestamp", 0))) for s in cl)
        min_occ = min(s.get("occurred_at_ms", s.get("timestamp", 0)) for s in cl)
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
    include_round_numbers: bool = False,
    now_ms: int,
) -> List[LiquidityLevel]:
    """Builds comprehensive causal liquidity map from swings, equal levels, session extremes, and PDH/PDL."""
    levels: List[LiquidityLevel] = []
    tol = max(0.20, tolerance_atr * atr)

    # 1. Confirmed Swing Highs (Buy-side)
    for sh in swing_highs:
        sh_known = sh.get("known_at_ms", sh.get("confirmed_at", sh.get("timestamp", 0)))
        sh_occ = sh.get("occurred_at_ms", sh.get("timestamp", 0))
        if sh_known <= now_ms:
            levels.append(
                LiquidityLevel(
                    id=sh.get("id", f"sh-{sh['price']}"),
                    price=float(sh["price"]),
                    side="BUY_SIDE",
                    source_kind="SWING",
                    occurred_at_ms=sh_occ,
                    known_at_ms=sh_known,
                    source_ids=[sh.get("id", f"sh-{sh['price']}")],
                    label_vi=f"Đỉnh swing {sh['price']:.2f}",
                )
            )

    # 2. Confirmed Swing Lows (Sell-side)
    for sl in swing_lows:
        sl_known = sl.get("known_at_ms", sl.get("confirmed_at", sl.get("timestamp", 0)))
        sl_occ = sl.get("occurred_at_ms", sl.get("timestamp", 0))
        if sl_known <= now_ms:
            levels.append(
                LiquidityLevel(
                    id=sl.get("id", f"sl-{sl['price']}"),
                    price=float(sl["price"]),
                    side="SELL_SIDE",
                    source_kind="SWING",
                    occurred_at_ms=sl_occ,
                    known_at_ms=sl_known,
                    source_ids=[sl.get("id", f"sl-{sl['price']}")],
                    label_vi=f"Đáy swing {sl['price']:.2f}",
                )
            )

    # 3. Equal Highs and Lows (Clustered)
    eq_highs = cluster_equal_levels(swing_highs, "BUY_SIDE", tol, timeframe, symbol)
    levels.extend(eq_highs)
    eq_lows = cluster_equal_levels(swing_lows, "SELL_SIDE", tol, timeframe, symbol)
    levels.extend(eq_lows)

    # 4. Session levels (Asia / London / NY). Only sessions with trusted coverage become levels; a session
    #    still running contributes an AS-OF extreme flagged partial (never presented as the final high/low).
    if session_ranges:
        for s_name, s_range in session_ranges.items():
            if getattr(s_range, "coverage_status", "UNKNOWN") != "VALID" or s_range.start_ms > now_ms:
                continue
            known = (s_range.end_ms if s_range.is_complete else (s_range.cutoff_ms or now_ms))
            if known > now_ms:
                continue
            partial = not s_range.is_complete
            suffix = " (đang hình thành)" if partial else ""
            cov = {"ratio": round(s_range.coverage_ratio, 4), "bars": s_range.bars_count,
                   "expected": s_range.expected_bars, "date": s_range.session_date_ny}
            if s_range.high is not None:
                levels.append(LiquidityLevel(
                    id=f"liq-{s_name.lower()}-{s_range.session_date_ny}-high",
                    price=s_range.high, side="BUY_SIDE", source_kind=f"SESSION_{s_name.upper()}",
                    occurred_at_ms=s_range.high_time_ms or s_range.start_ms, known_at_ms=known,
                    label_vi=f"Đỉnh phiên {s_name} {s_range.high:.2f}{suffix}",
                    is_partial=partial, coverage=cov))
            if s_range.low is not None:
                levels.append(LiquidityLevel(
                    id=f"liq-{s_name.lower()}-{s_range.session_date_ny}-low",
                    price=s_range.low, side="SELL_SIDE", source_kind=f"SESSION_{s_name.upper()}",
                    occurred_at_ms=s_range.low_time_ms or s_range.start_ms, known_at_ms=known,
                    label_vi=f"Đáy phiên {s_name} {s_range.low:.2f}{suffix}",
                    is_partial=partial, coverage=cov))

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

    # 6. Round Number Levels (Rule 055: Secondary context with clear configuration)
    if include_round_numbers:
        min_p = min((l.price for l in levels), default=0.0)
        max_p = max((l.price for l in levels), default=0.0)
        if min_p > 0 and max_p > min_p:
            step = 50.0 if (max_p - min_p) >= 100.0 else 10.0
            start_round = math.ceil(min_p / step) * step
            while start_round <= max_p:
                r_price = round(start_round, 2)
                # Avoid duplicating an existing level at the exact same price
                if not any(abs(l.price - r_price) < 0.05 for l in levels):
                    mid_p = (min_p + max_p) / 2.0
                    side = "BUY_SIDE" if r_price >= mid_p else "SELL_SIDE"
                    levels.append(
                        LiquidityLevel(
                            id=f"liq-round-{int(r_price)}",
                            price=r_price,
                            side=side,
                            source_kind="ROUND_NUMBER",
                            occurred_at_ms=now_ms,
                            known_at_ms=now_ms,
                            label_vi=f"Mức số tròn {r_price:.2f}",
                        )
                    )
                start_round += step

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


def update_liquidity_states(
    levels: Sequence[LiquidityLevel],
    causal_candles: Sequence[Any],
    now_ms: int,
    candle_duration_ms: int = 300_000,
) -> None:
    """Applies update_liquidity_state to every level (mutates level.state). Must run BEFORE anchor/target
    selection so that a level already taken by price is never offered as an untouched target again."""
    for lv in levels:
        update_liquidity_state(lv, causal_candles, now_ms, candle_duration_ms)


def select_unconsumed_target(
    levels: Sequence[LiquidityLevel],
    direction: str,
    entry_price: float,
    as_of_ms: Optional[int] = None,
) -> Optional[LiquidityLevel]:
    """Selects a valid unconsumed target liquidity level on the correct side of entry.
    - LONG: BUY_SIDE liquidity with price > entry_price.
    - SHORT: SELL_SIDE liquidity with price < entry_price.
    - Target must be UNTOUCHED/APPROACHED (not SWEPT/BROKEN/CONSUMED) and, when `as_of_ms` is given, known
      at or before that instant (a level learned later is not a target at decision time).
    """
    if not levels or entry_price is None or entry_price <= 0:
        return None

    def _live(l: LiquidityLevel) -> bool:
        if getattr(l, "source_kind", "") == "ROUND_NUMBER":
            return False
        return l.state in ("UNTOUCHED", "APPROACHED") and (as_of_ms is None or l.known_at_ms <= as_of_ms)

    if direction == "LONG":
        candidates = [l for l in levels if l.side == "BUY_SIDE" and l.price > entry_price and _live(l)]
        if candidates:
            return min(candidates, key=lambda l: l.price)
    elif direction == "SHORT":
        candidates = [l for l in levels if l.side == "SELL_SIDE" and l.price < entry_price and _live(l)]
        if candidates:
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


def _create_cluster_from_group(
    group: List[LiquidityLevel],
    side: str,
    symbol: str,
    timeframe: str,
    now_ms: int,
) -> LiquidityCluster:
    prices = [l.price for l in group]
    bot = min(prices)
    top = max(prices)
    mid = round((bot + top) / 2.0, 2)
    source_ids = [l.id for l in group]
    max_known = max(l.known_at_ms for l in group)

    inferred_kinds = {"EQH", "EQL", "ROUND_NUMBER"}
    is_inferred = any(l.source_kind in inferred_kinds for l in group)
    c_type = "INFERRED_LIQUIDITY" if is_inferred else "OBSERVED_STRUCTURE"

    # Score calculation (Rule 058): base on number of distinct sources, recency, state
    base_score = min(5.0, len(group) * 1.5)
    if any(l.source_kind.startswith("SESSION") for l in group):
        base_score += 1.0
    if any(l.source_kind in ("PDH", "PDL") for l in group):
        base_score += 1.5
    cluster_score = round(min(10.0, base_score), 2)

    if any(l.state == "CONSUMED" for l in group):
        c_state = "INVALIDATED"
    elif any(l.state in ("SWEPT", "BROKEN") for l in group):
        c_state = "SWEPT"
    else:
        c_state = "UNSWEPT"

    c_id = f"cl-{side[:3].lower()}-{symbol}-{timeframe}-{mid:.2f}-{len(group)}"
    side_vi = "Mua (Buy-side)" if side == "BUY_SIDE" else "Bán (Sell-side)"
    label = f"Cụm thanh khoản {side_vi} [{bot:.2f} - {top:.2f}] ({len(group)} nguồn)"

    return LiquidityCluster(
        id=c_id,
        side=side,
        bottom=bot,
        top=top,
        price_mid=mid,
        source_level_ids=source_ids,
        known_at_ms=max_known,
        state=c_state,
        cluster_type=c_type,
        score=cluster_score,
        label_vi=label,
        evidence_references=source_ids,
    )


def build_liquidity_clusters(
    levels: Sequence[LiquidityLevel],
    tolerance_price: float = 0.50,
    now_ms: Optional[int] = None,
    timeframe: str = "15M",
    symbol: str = "XAUUSDT",
    tolerance: Optional[float] = None,
    as_of_ms: Optional[int] = None,
) -> List[LiquidityCluster]:
    """
    V16 Liquidity Cluster grouping (Rules 054-060).
    Clusters nearby liquidity levels of the same side (BUY_SIDE / SELL_SIDE) within tolerance_price.
    Distinguishes OBSERVED_STRUCTURE vs INFERRED_LIQUIDITY.
    Calculates cluster score based on number of distinct sources, timeframes, and confirmation state.
    """
    tol = tolerance if tolerance is not None else tolerance_price
    t_now = now_ms if now_ms is not None else (as_of_ms if as_of_ms is not None else int(time.time() * 1000))
    if not levels or tol <= 0:
        return []

    clusters: List[LiquidityCluster] = []
    for side in ("BUY_SIDE", "SELL_SIDE"):
        side_lvls = [l for l in levels if l.side == side and l.known_at_ms <= t_now]
        if not side_lvls:
            continue
        sorted_lvls = sorted(side_lvls, key=lambda l: l.price)

        current_group: List[LiquidityLevel] = [sorted_lvls[0]]
        for lvl in sorted_lvls[1:]:
            if abs(lvl.price - current_group[-1].price) <= tol:
                current_group.append(lvl)
            else:
                clusters.append(_create_cluster_from_group(current_group, side, symbol, timeframe, t_now))
                current_group = [lvl]
        if current_group:
            clusters.append(_create_cluster_from_group(current_group, side, symbol, timeframe, t_now))

    return clusters


def update_cluster_state(
    cluster: LiquidityCluster,
    causal_candles: Sequence[Any],
    now_ms: int,
) -> str:
    """
    V16 Causal state transition for a liquidity cluster (Rules 060-063).
    UNSWEPT -> SWEPT -> RECLAIMED or ACCEPTED_BEYOND.
    """
    if cluster.state in ("INVALIDATED", "ACCEPTED_BEYOND", "RECLAIMED"):
        return cluster.state

    subsequent_bars = [
        c for c in causal_candles
        if getattr(c, "timestamp", getattr(c, "open_time", 0)) >= cluster.known_at_ms
    ]
    if not subsequent_bars:
        return cluster.state

    for bar in subsequent_bars:
        high = float(bar.high)
        low = float(bar.low)
        close = float(bar.close)

        b_time = getattr(bar, "timestamp", getattr(bar, "open_time", now_ms))
        if cluster.side == "BUY_SIDE":
            if high > cluster.top:
                cluster.swept_extreme = high if cluster.swept_extreme is None else max(cluster.swept_extreme, high)
                cluster.swept_at_ms = b_time
                cluster.state = "SWEPT"
                if close <= cluster.top:
                    cluster.state = "RECLAIMED"
                    cluster.reclaimed_at_ms = b_time
                else:
                    cluster.state = "ACCEPTED_BEYOND"
        elif cluster.side == "SELL_SIDE":
            if low < cluster.bottom:
                cluster.swept_extreme = low if cluster.swept_extreme is None else min(cluster.swept_extreme, low)
                cluster.swept_at_ms = b_time
                cluster.state = "SWEPT"
                if close >= cluster.bottom:
                    cluster.state = "RECLAIMED"
                    cluster.reclaimed_at_ms = b_time
                else:
                    cluster.state = "ACCEPTED_BEYOND"

    return cluster.state
