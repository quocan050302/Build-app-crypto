"""
backend/services/unicorn_evidence_service.py
AURUM V17.8 — ICT Unicorn Evidence Engine.
Pure calculation, zero database mutations, zero network I/O, zero wall-clock dependencies.
Detects Breaker Block + FVG Confluence with causal episode binding and retest tracking.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.unicorn_evidence_types import (
    BreakerBlock,
    BreakerType,
    UnicornEvidence,
    UnicornSetupStatus,
)
from services.liquidity_scenario_service import identify_confirmed_swings
import smc_engine


def _candle_open_ms(c: Any) -> int:
    val = getattr(c, "open_time", None) or getattr(c, "timestamp", None)
    if val is None and isinstance(c, dict):
        val = c.get("open_time") or c.get("timestamp")
    return int(val or 0)


def _candle_ohlc(c: Any) -> Tuple[float, float, float, float]:
    if isinstance(c, dict):
        return float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
    return float(c.open), float(c.high), float(c.low), float(c.close)


def _tf_duration_ms(timeframe: str) -> int:
    tf = str(timeframe or "1M").upper()
    mapping = {
        "1M": 60_000,
        "M1": 60_000,
        "3M": 180_000,
        "5M": 300_000,
        "M5": 300_000,
        "15M": 900_000,
        "M15": 900_000,
        "1H": 3_600_000,
        "H1": 3_600_000,
    }
    return mapping.get(tf, 60_000)


def detect_breaker_blocks(
    closed_bars: Sequence[Any],
    direction: str,
    as_of_ms: int,
    *,
    symbol: str = "XAUUSDT",
    timeframe: str = "1M",
    confirmed_swings: Optional[Tuple[Sequence[Dict[str, Any]], Sequence[Dict[str, Any]]]] = None,
    min_displacement_body_atr: float = 0.40,
    bounds_policy: str = "FULL_CANDLE",
) -> List[BreakerBlock]:
    """
    Detects causal ICT Breaker Blocks.
    - Symmetrical for Bullish and Bearish.
    - Bullish Breaker (for LONG):
      1. Prior swing low was formed or tested.
      2. Bearish Order Block (down-close candle: close < open) preceding the lowest point.
      3. Aggressive displacement upward closes strictly above a confirmed prior swing high (MSS/BOS).
      4. Down-close candle converted into Bullish Breaker.
      5. Distal level = breaker low. Proximal level = breaker high.
    - Bearish Breaker (for SHORT):
      1. Prior swing high was formed or swept.
      2. Bullish Order Block (up-close candle: close > open) preceding the highest point.
      3. Aggressive displacement downward closes strictly below a confirmed prior swing low (MSS/BOS).
      4. Up-close candle converted into Bearish Breaker.
      5. Distal level = breaker high. Proximal level = breaker low.
    """
    duration_ms = _tf_duration_ms(timeframe)
    norm_dir = direction.upper()
    target_type = BreakerType.BULLISH_BREAKER if norm_dir == "LONG" else BreakerType.BEARISH_BREAKER

    # Filter candles strictly <= as_of_ms
    valid_bars: List[Any] = []
    for b in closed_bars:
        t_open = _candle_open_ms(b)
        t_close = t_open + duration_ms
        if t_close <= as_of_ms:
            valid_bars.append(b)

    if len(valid_bars) < 4:
        return []

    # Get confirmed swings
    if confirmed_swings is not None:
        sh_list, sl_list = confirmed_swings
    else:
        sh_list, sl_list = identify_confirmed_swings(valid_bars, left_bars=2, right_bars=2, as_of_ms=as_of_ms)

    breakers: List[BreakerBlock] = []
    n = len(valid_bars)

    for i in range(2, n):
        curr_bar = valid_bars[i]
        curr_open, curr_high, curr_low, curr_close = _candle_ohlc(curr_bar)
        curr_open_ms = _candle_open_ms(curr_bar)
        curr_close_ms = curr_open_ms + duration_ms

        if curr_close_ms > as_of_ms:
            continue

        if norm_dir == "LONG":
            # Looking for Bullish Breaker: current bar broke above a confirmed swing high
            # Eligible swing highs must be confirmed prior to current bar's close
            eligible_sh = [
                sh for sh in sh_list
                if int(sh.get("confirmed_at") or sh.get("known_at_ms") or sh.get("timestamp", 0)) <= curr_open_ms
                and int(sh.get("timestamp", 0)) < curr_open_ms
                and float(sh.get("price", 0.0)) < curr_close
            ]
            if not eligible_sh:
                continue

            # Prior swing high broken
            broken_sh = max(eligible_sh, key=lambda s: int(s.get("timestamp", 0)))
            broken_price = float(broken_sh["price"])
            sh_ts = int(broken_sh.get("timestamp", 0))

            # Only the initial break candle converts the OB into a Breaker
            if any(_candle_ohlc(valid_bars[prev_i])[3] > broken_price
                   for prev_i in range(2, i)
                   if _candle_open_ms(valid_bars[prev_i]) > sh_ts):
                continue

            # Find the bearish candle(s) prior to the impulsive upward rally
            # Search backward from i for a down-close candle that formed before or at the lowest point
            lowest_idx = i
            lowest_val = curr_low
            for k in range(max(0, i - 10), i):
                _, _, k_l, _ = _candle_ohlc(valid_bars[k])
                if k_l < lowest_val:
                    lowest_val = k_l
                    lowest_idx = k

            # Find down candle before or at lowest_idx
            ob_idx = None
            for k in range(lowest_idx, max(-1, lowest_idx - 5), -1):
                k_o, _, _, k_c = _candle_ohlc(valid_bars[k])
                if k_c <= k_o:  # Down-close or doji
                    ob_idx = k
                    break

            if ob_idx is None:
                continue

            ob_bar = valid_bars[ob_idx]
            ob_o, ob_h, ob_l, ob_c = _candle_ohlc(ob_bar)
            ob_open_ms = _candle_open_ms(ob_bar)
            ob_close_ms = ob_open_ms + duration_ms

            b_low = ob_l if bounds_policy == "FULL_CANDLE" else min(ob_o, ob_c)
            b_high = ob_h if bounds_policy == "FULL_CANDLE" else max(ob_o, ob_c)

            b_id = f"brk_bull_{timeframe}_{ob_open_ms}_{curr_open_ms}"
            breakers.append(
                BreakerBlock(
                    id=b_id,
                    symbol=symbol,
                    breaker_type=BreakerType.BULLISH_BREAKER,
                    low=round(b_low, 2),
                    high=round(b_high, 2),
                    distal_price=round(b_low, 2),
                    proximal_price=round(b_high, 2),
                    source_ob_id=f"ob_down_{ob_open_ms}",
                    source_ob_low=round(ob_l, 2),
                    source_ob_high=round(ob_h, 2),
                    source_ob_open_ms=ob_open_ms,
                    source_ob_close_ms=ob_close_ms,
                    break_bar_open_ms=curr_open_ms,
                    break_bar_close_ms=curr_close_ms,
                    confirmed_at_ms=curr_close_ms,
                    broken_swing_id=str(broken_sh.get("id", f"sh_{broken_sh.get('timestamp')}")),
                    broken_swing_price=broken_price,
                    episode_id=f"ep_{broken_sh.get('timestamp')}_{curr_open_ms}",
                    displacement_ratio=round((curr_close - curr_open) / max(0.01, curr_high - curr_low), 2),
                )
            )

        else:
            # Looking for Bearish Breaker: current bar broke below a confirmed swing low
            eligible_sl = [
                sl for sl in sl_list
                if int(sl.get("confirmed_at") or sl.get("known_at_ms") or sl.get("timestamp", 0)) <= curr_open_ms
                and int(sl.get("timestamp", 0)) < curr_open_ms
                and float(sl.get("price", 0.0)) > curr_close
            ]
            if not eligible_sl:
                continue

            # Prior swing low broken
            broken_sl = max(eligible_sl, key=lambda s: int(s.get("timestamp", 0)))
            broken_price = float(broken_sl["price"])
            sl_ts = int(broken_sl.get("timestamp", 0))

            # Only the initial break candle converts the OB into a Breaker
            if any(_candle_ohlc(valid_bars[prev_i])[3] < broken_price
                   for prev_i in range(2, i)
                   if _candle_open_ms(valid_bars[prev_i]) > sl_ts):
                continue

            # Find the bullish candle(s) prior to the impulsive downward drop
            highest_idx = i
            highest_val = curr_high
            for k in range(max(0, i - 10), i):
                _, k_h, _, _ = _candle_ohlc(valid_bars[k])
                if k_h > highest_val:
                    highest_val = k_h
                    highest_idx = k

            ob_idx = None
            for k in range(highest_idx, max(-1, highest_idx - 5), -1):
                k_o, _, _, k_c = _candle_ohlc(valid_bars[k])
                if k_c >= k_o:  # Up-close or doji
                    ob_idx = k
                    break

            if ob_idx is None:
                continue

            ob_bar = valid_bars[ob_idx]
            ob_o, ob_h, ob_l, ob_c = _candle_ohlc(ob_bar)
            ob_open_ms = _candle_open_ms(ob_bar)
            ob_close_ms = ob_open_ms + duration_ms

            b_low = ob_l if bounds_policy == "FULL_CANDLE" else min(ob_o, ob_c)
            b_high = ob_h if bounds_policy == "FULL_CANDLE" else max(ob_o, ob_c)

            b_id = f"brk_bear_{timeframe}_{ob_open_ms}_{curr_open_ms}"
            breakers.append(
                BreakerBlock(
                    id=b_id,
                    symbol=symbol,
                    breaker_type=BreakerType.BEARISH_BREAKER,
                    low=round(b_low, 2),
                    high=round(b_high, 2),
                    distal_price=round(b_high, 2),
                    proximal_price=round(b_low, 2),
                    source_ob_id=f"ob_up_{ob_open_ms}",
                    source_ob_low=round(ob_l, 2),
                    source_ob_high=round(ob_h, 2),
                    source_ob_open_ms=ob_open_ms,
                    source_ob_close_ms=ob_close_ms,
                    break_bar_open_ms=curr_open_ms,
                    break_bar_close_ms=curr_close_ms,
                    confirmed_at_ms=curr_close_ms,
                    broken_swing_id=str(broken_sl.get("id", f"sl_{broken_sl.get('timestamp')}")),
                    broken_swing_price=broken_price,
                    episode_id=f"ep_{broken_sl.get('timestamp')}_{curr_open_ms}",
                    displacement_ratio=round((curr_open - curr_close) / max(0.01, curr_high - curr_low), 2),
                )
            )

    return breakers


def detect_unicorn_evidence(
    closed_bars: Sequence[Any],
    direction: str,
    as_of_ms: int,
    *,
    symbol: str = "XAUUSDT",
    timeframe: str = "1M",
    min_overlap_ticks: float = 1.0,
    tick_size: float = 0.01,
    confirmed_swings: Optional[Tuple[Sequence[Dict[str, Any]], Sequence[Dict[str, Any]]]] = None,
    candidate_fvgs: Optional[Sequence[Dict[str, Any]]] = None,
    min_displacement_body_atr: float = 0.40,
) -> UnicornEvidence:
    """
    Synthesizes ICT Unicorn Evidence (V17.8 Phase 1).
    Requirements:
    1. Valid Breaker Block in trade direction.
    2. Valid Fair Value Gap (FVG) in trade direction.
    3. Price overlap between Breaker Block and FVG:
       overlap_high > overlap_low by at least min_overlap_ticks * tick_size.
    4. Causal episode binding (Breaker and FVG formed during the same structural displacement).
    5. Invalidation check: Breaker distal price must NOT be breached prior to as_of_ms.
    6. Retest check: Retest into overlap / breaker zone must occur AFTER confirmation.
    """
    norm_dir = direction.upper()
    duration_ms = _tf_duration_ms(timeframe)

    # 1. Filter closed bars strictly <= as_of_ms
    valid_bars: List[Any] = []
    for b in closed_bars:
        t_open = _candle_open_ms(b)
        t_close = t_open + duration_ms
        if t_close <= as_of_ms:
            valid_bars.append(b)

    fallback_id = f"unicorn_{symbol}_{norm_dir}_{as_of_ms}"

    if len(valid_bars) < 4:
        return UnicornEvidence(
            unicorn_id=fallback_id,
            symbol=symbol,
            direction=norm_dir,
            setup_episode_id="none",
            breaker_id=None,
            breaker_type=None,
            breaker_low=None,
            breaker_high=None,
            breaker_distal_price=None,
            breaker_confirmed_at_ms=None,
            fvg_id=None,
            fvg_low=None,
            fvg_high=None,
            fvg_confirmed_at_ms=None,
            overlap_low=None,
            overlap_high=None,
            mss_or_bos_id=None,
            displacement_id=None,
            sweep_id=None,
            invalidation_source_id=None,
            invalidation_price=None,
            retest_timestamp_ms=None,
            known_at_ms=as_of_ms,
            decision_cutoff_ms=as_of_ms,
            validity_status=UnicornSetupStatus.INVALID_DATA.value,
            reason_codes=("DATA_INSUFFICIENT",),
        )

    # 2. Detect Breaker Blocks
    breakers = detect_breaker_blocks(
        valid_bars,
        norm_dir,
        as_of_ms,
        symbol=symbol,
        timeframe=timeframe,
        confirmed_swings=confirmed_swings,
        min_displacement_body_atr=min_displacement_body_atr,
    )

    if not breakers:
        return UnicornEvidence(
            unicorn_id=fallback_id,
            symbol=symbol,
            direction=norm_dir,
            setup_episode_id="none",
            breaker_id=None,
            breaker_type=None,
            breaker_low=None,
            breaker_high=None,
            breaker_distal_price=None,
            breaker_confirmed_at_ms=None,
            fvg_id=None,
            fvg_low=None,
            fvg_high=None,
            fvg_confirmed_at_ms=None,
            overlap_low=None,
            overlap_high=None,
            mss_or_bos_id=None,
            displacement_id=None,
            sweep_id=None,
            invalidation_source_id=None,
            invalidation_price=None,
            retest_timestamp_ms=None,
            known_at_ms=as_of_ms,
            decision_cutoff_ms=as_of_ms,
            validity_status=UnicornSetupStatus.MISSING_BREAKER.value,
            reason_codes=("BREAKER_NOT_FOUND",),
        )

    # 3. Detect / Resolve FVGs
    if candidate_fvgs is not None:
        fvgs = list(candidate_fvgs)
    else:
        fvgs = smc_engine.detect_fvgs(valid_bars, timeframe=timeframe)

    target_fvg_type = "BULLISH_FVG" if norm_dir == "LONG" else "BEARISH_FVG"
    valid_fvgs = [
        f for f in fvgs
        if f.get("type") == target_fvg_type
        and float(f.get("bottom", 0.0)) < float(f.get("top", 0.0))
        and int(f.get("confirmed_at") or f.get("timestamp") or 0) <= as_of_ms
    ]

    if not valid_fvgs:
        best_breaker = breakers[-1]
        return UnicornEvidence(
            unicorn_id=fallback_id,
            symbol=symbol,
            direction=norm_dir,
            setup_episode_id=best_breaker.episode_id or "ep_unknown",
            breaker_id=best_breaker.id,
            breaker_type=best_breaker.breaker_type.value,
            breaker_low=best_breaker.low,
            breaker_high=best_breaker.high,
            breaker_distal_price=best_breaker.distal_price,
            breaker_confirmed_at_ms=best_breaker.confirmed_at_ms,
            fvg_id=None,
            fvg_low=None,
            fvg_high=None,
            fvg_confirmed_at_ms=None,
            overlap_low=None,
            overlap_high=None,
            mss_or_bos_id=best_breaker.broken_swing_id,
            displacement_id=best_breaker.id,
            sweep_id=None,
            invalidation_source_id=best_breaker.id,
            invalidation_price=best_breaker.distal_price,
            retest_timestamp_ms=None,
            known_at_ms=best_breaker.confirmed_at_ms,
            decision_cutoff_ms=as_of_ms,
            validity_status=UnicornSetupStatus.MISSING_FVG.value,
            reason_codes=("FVG_NOT_FOUND",),
        )

    # 4. Search for Breaker + FVG pairs with valid geometric overlap
    best_candidate: Optional[Tuple[BreakerBlock, Dict[str, Any], float, float]] = None
    min_overlap_price = min_overlap_ticks * tick_size

    # Iterate backwards (most recent breakers and FVGs first)
    for brk in reversed(breakers):
        for fvg in reversed(valid_fvgs):
            fvg_top = float(fvg["top"])
            fvg_bot = float(fvg["bottom"])
            fvg_conf = int(fvg.get("confirmed_at") or fvg.get("timestamp") or 0)

            # Causal episode check: must be formed within 15 bars of each other
            time_delta = abs(brk.confirmed_at_ms - fvg_conf)
            if time_delta > 15 * duration_ms:
                continue

            o_low = max(brk.low, fvg_bot)
            o_high = min(brk.high, fvg_top)

            if (o_high - o_low) >= (min_overlap_price - 1e-6):
                best_candidate = (brk, fvg, o_low, o_high)
                break
        if best_candidate:
            break

    if not best_candidate:
        # Breaker exists and FVG exists, but no geometric overlap
        best_brk = breakers[-1]
        best_fvg = valid_fvgs[-1]
        return UnicornEvidence(
            unicorn_id=fallback_id,
            symbol=symbol,
            direction=norm_dir,
            setup_episode_id=best_brk.episode_id or "ep_unknown",
            breaker_id=best_brk.id,
            breaker_type=best_brk.breaker_type.value,
            breaker_low=best_brk.low,
            breaker_high=best_brk.high,
            breaker_distal_price=best_brk.distal_price,
            breaker_confirmed_at_ms=best_brk.confirmed_at_ms,
            fvg_id=str(best_fvg.get("id")),
            fvg_low=float(best_fvg["bottom"]),
            fvg_high=float(best_fvg["top"]),
            fvg_confirmed_at_ms=int(best_fvg.get("confirmed_at") or 0),
            overlap_low=None,
            overlap_high=None,
            mss_or_bos_id=best_brk.broken_swing_id,
            displacement_id=best_brk.id,
            sweep_id=None,
            invalidation_source_id=best_brk.id,
            invalidation_price=best_brk.distal_price,
            retest_timestamp_ms=None,
            known_at_ms=max(best_brk.confirmed_at_ms, int(best_fvg.get("confirmed_at") or 0)),
            decision_cutoff_ms=as_of_ms,
            validity_status=UnicornSetupStatus.NO_GEOMETRIC_OVERLAP.value,
            reason_codes=("BREAKER_FVG_NO_OVERLAP",),
        )

    brk, fvg, overlap_low, overlap_high = best_candidate
    fvg_id = str(fvg.get("id", f"fvg_{norm_dir}_{fvg['bottom']}_{fvg['top']}"))
    fvg_conf = int(fvg.get("confirmed_at") or fvg.get("timestamp") or 0)
    setup_known_ms = max(brk.confirmed_at_ms, fvg_conf)

    # 5. Invalidation Check: Did any closed bar breach distal price between setup_known_ms and as_of_ms?
    is_invalidated = False
    invalidation_price = brk.distal_price

    for b in valid_bars:
        t_open = _candle_open_ms(b)
        t_close = t_open + duration_ms
        if t_close <= setup_known_ms or t_close > as_of_ms:
            continue
        _, b_h, b_l, b_c = _candle_ohlc(b)
        if norm_dir == "LONG" and b_c < invalidation_price:
            is_invalidated = True
            break
        if norm_dir == "SHORT" and b_c > invalidation_price:
            is_invalidated = True
            break

    if is_invalidated:
        return UnicornEvidence(
            unicorn_id=f"unicorn_{symbol}_{norm_dir}_{brk.id}_{fvg_id}",
            symbol=symbol,
            direction=norm_dir,
            setup_episode_id=brk.episode_id or f"ep_{setup_known_ms}",
            breaker_id=brk.id,
            breaker_type=brk.breaker_type.value,
            breaker_low=brk.low,
            breaker_high=brk.high,
            breaker_distal_price=invalidation_price,
            breaker_confirmed_at_ms=brk.confirmed_at_ms,
            fvg_id=fvg_id,
            fvg_low=float(fvg["bottom"]),
            fvg_high=float(fvg["top"]),
            fvg_confirmed_at_ms=fvg_conf,
            overlap_low=round(overlap_low, 2),
            overlap_high=round(overlap_high, 2),
            mss_or_bos_id=brk.broken_swing_id,
            displacement_id=brk.id,
            sweep_id=None,
            invalidation_source_id=brk.id,
            invalidation_price=invalidation_price,
            retest_timestamp_ms=None,
            known_at_ms=setup_known_ms,
            decision_cutoff_ms=as_of_ms,
            validity_status=UnicornSetupStatus.UNICORN_INVALIDATED.value,
            reason_codes=("DISTAL_LEVEL_BREACHED",),
        )

    # 6. Retest Check: Did any bar after setup_known_ms trade into overlap zone?
    retest_bar_open_ms = None
    for b in valid_bars:
        t_open = _candle_open_ms(b)
        t_close = t_open + duration_ms
        if t_close <= setup_known_ms or t_close > as_of_ms:
            continue
        _, b_h, b_l, _ = _candle_ohlc(b)
        # Check intersection with overlap zone [overlap_low, overlap_high]
        if b_h >= overlap_low and b_l <= overlap_high:
            retest_bar_open_ms = t_open
            break

    if retest_bar_open_ms is None:
        return UnicornEvidence(
            unicorn_id=f"unicorn_{symbol}_{norm_dir}_{brk.id}_{fvg_id}",
            symbol=symbol,
            direction=norm_dir,
            setup_episode_id=brk.episode_id or f"ep_{setup_known_ms}",
            breaker_id=brk.id,
            breaker_type=brk.breaker_type.value,
            breaker_low=brk.low,
            breaker_high=brk.high,
            breaker_distal_price=invalidation_price,
            breaker_confirmed_at_ms=brk.confirmed_at_ms,
            fvg_id=fvg_id,
            fvg_low=float(fvg["bottom"]),
            fvg_high=float(fvg["top"]),
            fvg_confirmed_at_ms=fvg_conf,
            overlap_low=round(overlap_low, 2),
            overlap_high=round(overlap_high, 2),
            mss_or_bos_id=brk.broken_swing_id,
            displacement_id=brk.id,
            sweep_id=None,
            invalidation_source_id=brk.id,
            invalidation_price=invalidation_price,
            retest_timestamp_ms=None,
            known_at_ms=setup_known_ms,
            decision_cutoff_ms=as_of_ms,
            validity_status=UnicornSetupStatus.WAITING_RETEST.value,
            reason_codes=("WAITING_CONFLUENCE_RETEST",),
        )

    # Retested & Valid!
    unicorn_id = f"unicorn_{symbol}_{norm_dir}_{brk.id}_{fvg_id}_{retest_bar_open_ms}"
    return UnicornEvidence(
        unicorn_id=unicorn_id,
        symbol=symbol,
        direction=norm_dir,
        setup_episode_id=brk.episode_id or f"ep_{setup_known_ms}",
        breaker_id=brk.id,
        breaker_type=brk.breaker_type.value,
        breaker_low=brk.low,
        breaker_high=brk.high,
        breaker_distal_price=invalidation_price,
        breaker_confirmed_at_ms=brk.confirmed_at_ms,
        fvg_id=fvg_id,
        fvg_low=float(fvg["bottom"]),
        fvg_high=float(fvg["top"]),
        fvg_confirmed_at_ms=fvg_conf,
        overlap_low=round(overlap_low, 2),
        overlap_high=round(overlap_high, 2),
        mss_or_bos_id=brk.broken_swing_id,
        displacement_id=brk.id,
        sweep_id=None,
        invalidation_source_id=brk.id,
        invalidation_price=invalidation_price,
        retest_timestamp_ms=retest_bar_open_ms,
        known_at_ms=max(setup_known_ms, retest_bar_open_ms + duration_ms),
        decision_cutoff_ms=as_of_ms,
        validity_status=UnicornSetupStatus.VALID.value,
        reason_codes=(),
    )
