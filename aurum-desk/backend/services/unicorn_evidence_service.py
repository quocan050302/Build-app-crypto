"""
backend/services/unicorn_evidence_service.py
AURUM V17.8.0.1 — ICT Unicorn Evidence Engine.
Pure calculation, zero database mutations, zero network I/O, zero wall-clock dependencies.
Detects Breaker Block + FVG Confluence with strict causal episode binding, provenance validation,
and retest tracking.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

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


def _swing_occurred_ms(s: Dict[str, Any]) -> int:
    return int(s.get("occurred_at_ms") or s.get("timestamp") or 0)


def _swing_confirmed_ms(s: Dict[str, Any]) -> int:
    return int(s.get("confirmed_at") or s.get("known_at_ms") or _swing_occurred_ms(s))


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
    Detects causal ICT Breaker Blocks with strict anti-lookahead, displacement strength,
    and deduplication of broken swings and source order blocks.
    """
    duration_ms = _tf_duration_ms(timeframe)
    norm_dir = direction.upper()

    valid_bars: List[Any] = []
    for b in closed_bars:
        t_open = _candle_open_ms(b)
        t_close = t_open + duration_ms
        if t_close <= as_of_ms:
            valid_bars.append(b)

    if len(valid_bars) < 4:
        return []

    if confirmed_swings is not None:
        sh_list, sl_list = confirmed_swings
    else:
        sh_list, sl_list = identify_confirmed_swings(
            valid_bars, left_bars=2, right_bars=2, as_of_ms=as_of_ms
        )

    breakers: List[BreakerBlock] = []
    seen_broken_swing_ids: Set[str] = set()
    seen_source_ob_ids: Set[str] = set()
    n = len(valid_bars)

    for i in range(2, n):
        curr_bar = valid_bars[i]
        curr_open, curr_high, curr_low, curr_close = _candle_ohlc(curr_bar)
        curr_open_ms = _candle_open_ms(curr_bar)
        curr_close_ms = curr_open_ms + duration_ms

        if curr_close_ms > as_of_ms:
            continue

        # Compute local prior average range (ATR proxy) over up to 14 prior closed bars
        prior_slice = valid_bars[max(0, i - 14):i]
        prior_ranges = [max(0.01, _candle_ohlc(pb)[1] - _candle_ohlc(pb)[2]) for pb in prior_slice]
        avg_prior_range = sum(prior_ranges) / len(prior_ranges) if prior_ranges else 1.0

        if norm_dir == "LONG":
            # Break candle must be bullish with sufficient displacement body
            body = curr_close - curr_open
            if body <= 0 or body < (min_displacement_body_atr * avg_prior_range - 1e-9):
                continue

            eligible_sh = [
                sh for sh in sh_list
                if _swing_confirmed_ms(sh) <= curr_open_ms
                and _swing_occurred_ms(sh) < curr_open_ms
                and float(sh.get("price", 0.0)) < curr_close
            ]
            if not eligible_sh:
                continue

            broken_sh = max(eligible_sh, key=_swing_occurred_ms)
            broken_price = float(broken_sh["price"])
            sh_ts = _swing_occurred_ms(broken_sh)
            broken_swing_id = str(broken_sh.get("id") or f"sh_{sh_ts}")

            if broken_swing_id in seen_broken_swing_ids:
                continue

            # Only the initial break candle converts the OB into a Breaker
            if any(
                _candle_ohlc(valid_bars[prev_i])[3] > broken_price
                for prev_i in range(2, i)
                if _candle_open_ms(valid_bars[prev_i]) > sh_ts
            ):
                continue

            lowest_idx = i
            lowest_val = curr_low
            for k in range(max(0, i - 10), i):
                if _candle_open_ms(valid_bars[k]) <= sh_ts:
                    continue
                _, _, k_l, _ = _candle_ohlc(valid_bars[k])
                if k_l < lowest_val:
                    lowest_val = k_l
                    lowest_idx = k

            ob_idx = None
            for k in range(lowest_idx, max(-1, lowest_idx - 5), -1):
                if _candle_open_ms(valid_bars[k]) <= sh_ts:
                    continue
                k_o, _, _, k_c = _candle_ohlc(valid_bars[k])
                if k_c <= k_o:
                    ob_idx = k
                    break

            if ob_idx is None:
                continue

            ob_bar = valid_bars[ob_idx]
            ob_o, ob_h, ob_l, ob_c = _candle_ohlc(ob_bar)
            ob_open_ms = _candle_open_ms(ob_bar)
            ob_close_ms = ob_open_ms + duration_ms
            source_ob_id = f"ob_down_{ob_open_ms}"

            if source_ob_id in seen_source_ob_ids:
                continue

            seen_broken_swing_ids.add(broken_swing_id)
            seen_source_ob_ids.add(source_ob_id)

            b_low = ob_l if bounds_policy == "FULL_CANDLE" else min(ob_o, ob_c)
            b_high = ob_h if bounds_policy == "FULL_CANDLE" else max(ob_o, ob_c)

            b_id = f"brk_bull_{timeframe}_{ob_open_ms}_{curr_open_ms}"
            mss_id = f"mss_bull_{broken_swing_id}_{curr_open_ms}"
            disp_id = f"disp_bull_{curr_open_ms}"
            ep_id = f"ep_{sh_ts}_{curr_open_ms}"

            breakers.append(
                BreakerBlock(
                    id=b_id,
                    symbol=symbol,
                    breaker_type=BreakerType.BULLISH_BREAKER,
                    low=round(b_low, 2),
                    high=round(b_high, 2),
                    distal_price=round(b_low, 2),
                    proximal_price=round(b_high, 2),
                    source_ob_id=source_ob_id,
                    source_ob_low=round(ob_l, 2),
                    source_ob_high=round(ob_h, 2),
                    source_ob_open_ms=ob_open_ms,
                    source_ob_close_ms=ob_close_ms,
                    break_bar_open_ms=curr_open_ms,
                    break_bar_close_ms=curr_close_ms,
                    confirmed_at_ms=curr_close_ms,
                    broken_swing_id=broken_swing_id,
                    broken_swing_price=broken_price,
                    episode_id=ep_id,
                    displacement_ratio=round((curr_close - curr_open) / max(0.01, curr_high - curr_low), 2),
                    mss_or_bos_id=mss_id,
                    displacement_id=disp_id,
                    known_at_ms=curr_close_ms,
                )
            )

        else:
            # Break candle must be bearish with sufficient displacement body
            body = curr_open - curr_close
            if body <= 0 or body < (min_displacement_body_atr * avg_prior_range - 1e-9):
                continue

            eligible_sl = [
                sl for sl in sl_list
                if _swing_confirmed_ms(sl) <= curr_open_ms
                and _swing_occurred_ms(sl) < curr_open_ms
                and float(sl.get("price", 0.0)) > curr_close
            ]
            if not eligible_sl:
                continue

            broken_sl = max(eligible_sl, key=_swing_occurred_ms)
            broken_price = float(broken_sl["price"])
            sl_ts = _swing_occurred_ms(broken_sl)
            broken_swing_id = str(broken_sl.get("id") or f"sl_{sl_ts}")

            if broken_swing_id in seen_broken_swing_ids:
                continue

            if any(
                _candle_ohlc(valid_bars[prev_i])[3] < broken_price
                for prev_i in range(2, i)
                if _candle_open_ms(valid_bars[prev_i]) > sl_ts
            ):
                continue

            highest_idx = i
            highest_val = curr_high
            for k in range(max(0, i - 10), i):
                if _candle_open_ms(valid_bars[k]) <= sl_ts:
                    continue
                _, k_h, _, _ = _candle_ohlc(valid_bars[k])
                if k_h > highest_val:
                    highest_val = k_h
                    highest_idx = k

            ob_idx = None
            for k in range(highest_idx, max(-1, lowest_idx if False else highest_idx - 5), -1):
                if _candle_open_ms(valid_bars[k]) <= sl_ts:
                    continue
                k_o, _, _, k_c = _candle_ohlc(valid_bars[k])
                if k_c >= k_o:
                    ob_idx = k
                    break

            if ob_idx is None:
                continue

            ob_bar = valid_bars[ob_idx]
            ob_o, ob_h, ob_l, ob_c = _candle_ohlc(ob_bar)
            ob_open_ms = _candle_open_ms(ob_bar)
            ob_close_ms = ob_open_ms + duration_ms
            source_ob_id = f"ob_up_{ob_open_ms}"

            if source_ob_id in seen_source_ob_ids:
                continue

            seen_broken_swing_ids.add(broken_swing_id)
            seen_source_ob_ids.add(source_ob_id)

            b_low = ob_l if bounds_policy == "FULL_CANDLE" else min(ob_o, ob_c)
            b_high = ob_h if bounds_policy == "FULL_CANDLE" else max(ob_o, ob_c)

            b_id = f"brk_bear_{timeframe}_{ob_open_ms}_{curr_open_ms}"
            mss_id = f"mss_bear_{broken_swing_id}_{curr_open_ms}"
            disp_id = f"disp_bear_{curr_open_ms}"
            ep_id = f"ep_{sl_ts}_{curr_open_ms}"

            breakers.append(
                BreakerBlock(
                    id=b_id,
                    symbol=symbol,
                    breaker_type=BreakerType.BEARISH_BREAKER,
                    low=round(b_low, 2),
                    high=round(b_high, 2),
                    distal_price=round(b_high, 2),
                    proximal_price=round(b_low, 2),
                    source_ob_id=source_ob_id,
                    source_ob_low=round(ob_l, 2),
                    source_ob_high=round(ob_h, 2),
                    source_ob_open_ms=ob_open_ms,
                    source_ob_close_ms=ob_close_ms,
                    break_bar_open_ms=curr_open_ms,
                    break_bar_close_ms=curr_close_ms,
                    confirmed_at_ms=curr_close_ms,
                    broken_swing_id=broken_swing_id,
                    broken_swing_price=broken_price,
                    episode_id=ep_id,
                    displacement_ratio=round((curr_open - curr_close) / max(0.01, curr_high - curr_low), 2),
                    mss_or_bos_id=mss_id,
                    displacement_id=disp_id,
                    known_at_ms=curr_close_ms,
                )
            )

    return breakers


def _is_same_causal_episode(
    brk: BreakerBlock,
    fvg: Dict[str, Any],
    valid_bars: Sequence[Any],
    norm_dir: str,
    duration_ms: int,
    selected_break: Optional[Dict[str, Any]] = None,
    selected_reference: Optional[Dict[str, Any]] = None,
    strict_causal_binding: bool = False,
) -> Tuple[bool, str]:
    """
    Verifies that the Breaker Block and FVG belong to the exact same structural
    displacement episode (MSS/BOS + displacement impulse), not merely a 15-bar window.
    """
    def _norm_swing_id(sid: Any) -> str:
        return str(sid or "").strip().lower().replace("m1_sh_", "1m_sh_").replace("m1_sl_", "1m_sl_")

    # 1. Explicit provenance matching if provided on FVG or selected_break
    fvg_ep = fvg.get("setup_episode_id") or fvg.get("episode_id")
    if fvg_ep and brk.episode_id and str(fvg_ep) != str(brk.episode_id):
        return False, "EPISODE_ID_MISMATCH"

    fvg_swing_id = fvg.get("broken_swing_id")
    if fvg_swing_id and brk.broken_swing_id and _norm_swing_id(fvg_swing_id) != _norm_swing_id(brk.broken_swing_id):
        return False, "BROKEN_SWING_ID_MISMATCH"

    if selected_reference and selected_reference.get("id"):
        ref_id = _norm_swing_id(selected_reference["id"])
        if brk.broken_swing_id and ref_id != _norm_swing_id(brk.broken_swing_id):
            return False, "REFERENCE_SWING_ID_MISMATCH"

    fvg_disp_id = fvg.get("displacement_id")
    if fvg_disp_id and str(fvg_disp_id) not in (str(brk.displacement_id), str(brk.id)):
        return False, "DISPLACEMENT_ID_MISMATCH"

    if selected_break:
        sb_open = selected_break.get("break_open_ms") or selected_break.get("broken_at_ms") or selected_break.get("timestamp")
        if sb_open is not None and abs(int(sb_open) - brk.break_bar_open_ms) > duration_ms:
            return False, "SELECTED_BREAK_BAR_MISMATCH"
        sb_ep = selected_break.get("setup_episode_id")
        if sb_ep and brk.episode_id and str(sb_ep) != str(brk.episode_id):
            return False, "SELECTED_BREAK_EPISODE_MISMATCH"

    fvg_break_ms = fvg.get("break_open_ms")
    if fvg_break_ms is not None and abs(int(fvg_break_ms) - brk.break_bar_open_ms) > duration_ms:
        return False, "FVG_BREAK_BAR_MISMATCH"

    if strict_causal_binding:
        if not fvg.get("id") or not brk.broken_swing_id or not brk.source_ob_id:
            return False, "MISSING_CAUSAL_PROVENANCE"

    # 2. Structural impulse contiguity: FVG middle candle must form as part of the
    # same displacement leg that broke structure (within 3 bars of break_bar_open_ms,
    # after source_ob_open_ms, and with no counter-trend break in between).
    fvg_conf = int(fvg.get("confirmed_at") or fvg.get("timestamp") or 0)
    fvg_mid_ms = int(
        fvg.get("middle_bar_ms")
        or fvg.get("timestamp")
        or fvg.get("open_time")
        or max(0, fvg_conf - 2 * duration_ms)
    )

    if fvg_mid_ms < brk.source_ob_open_ms:
        return False, "FVG_PRECEDES_SOURCE_OB"

    if abs(fvg_mid_ms - brk.break_bar_open_ms) > 3 * duration_ms:
        return False, "DISJOINT_DISPLACEMENT_IMPULSE"

    if abs(fvg_conf - brk.confirmed_at_ms) > 3 * duration_ms:
        return False, "DISJOINT_CONFIRMATION_WINDOW"

    # Verify all candles between break_bar_open_ms and fvg_mid_ms maintain impulse direction
    leg_start_ms = min(brk.break_bar_open_ms, fvg_mid_ms)
    leg_end_ms = max(brk.break_bar_open_ms, fvg_mid_ms)
    for b in valid_bars:
        t_open = _candle_open_ms(b)
        if leg_start_ms <= t_open <= leg_end_ms:
            b_o, _, _, b_c = _candle_ohlc(b)
            if norm_dir == "LONG" and b_c < b_o:
                return False, "IMPULSE_INTERRUPTED_BY_BEARISH_BAR"
            if norm_dir == "SHORT" and b_c > b_o:
                return False, "IMPULSE_INTERRUPTED_BY_BULLISH_BAR"

    return True, "OK"


def _build_rejected_evidence(
    *,
    fallback_id: str,
    symbol: str,
    norm_dir: str,
    as_of_ms: int,
    status: UnicornSetupStatus,
    reason_code: str,
    brk: Optional[BreakerBlock] = None,
    fvg: Optional[Dict[str, Any]] = None,
    overlap_low: Optional[float] = None,
    overlap_high: Optional[float] = None,
    known_at_ms: Optional[int] = None,
) -> UnicornEvidence:
    """
    Constructs a fail-closed UnicornEvidence object.
    Crucial safety invariant: invalid UnicornEvidence NEVER exposes a non-None
    invalidation_price for executable stop placement.
    """
    fvg_id = str(fvg.get("id")) if fvg and fvg.get("id") is not None else None
    fvg_conf = int(fvg.get("confirmed_at") or fvg.get("timestamp") or 0) if fvg else None
    fvg_low = float(fvg["bottom"]) if fvg and "bottom" in fvg and fvg["bottom"] is not None else None
    fvg_high = float(fvg["top"]) if fvg and "top" in fvg and fvg["top"] is not None else None

    resolved_known_ms = known_at_ms if known_at_ms is not None else (
        brk.confirmed_at_ms if brk else as_of_ms
    )

    return UnicornEvidence(
        unicorn_id=fallback_id,
        symbol=symbol,
        direction=norm_dir,
        setup_episode_id=(brk.episode_id if brk and brk.episode_id else "none"),
        breaker_id=brk.id if brk else None,
        breaker_type=brk.breaker_type.value if brk else None,
        breaker_low=brk.low if brk else None,
        breaker_high=brk.high if brk else None,
        breaker_distal_price=brk.distal_price if brk else None,
        breaker_confirmed_at_ms=brk.confirmed_at_ms if brk else None,
        breaker_source_ob_id=brk.source_ob_id if brk else None,
        broken_swing_id=brk.broken_swing_id if brk else None,
        fvg_id=fvg_id,
        selected_fvg_id=fvg_id,
        fvg_low=fvg_low,
        fvg_high=fvg_high,
        fvg_confirmed_at_ms=fvg_conf,
        overlap_low=round(overlap_low, 2) if overlap_low is not None else None,
        overlap_high=round(overlap_high, 2) if overlap_high is not None else None,
        mss_or_bos_id=(brk.mss_or_bos_id or brk.broken_swing_id) if brk else None,
        displacement_id=(brk.displacement_id or brk.id) if brk else None,
        sweep_id=None,
        invalidation_source_id=brk.id if brk else None,
        invalidation_price=None,  # Fail closed: never expose executable stop anchor when invalid
        retest_timestamp_ms=None,
        confirmed_at_ms=resolved_known_ms,
        known_at_ms=resolved_known_ms,
        decision_cutoff_ms=as_of_ms,
        validity_status=status.value,
        reason_codes=(reason_code,),
    )


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
    selected_fvg: Optional[Dict[str, Any]] = None,
    candidate_fvgs: Optional[Sequence[Dict[str, Any]]] = None,
    selected_break: Optional[Dict[str, Any]] = None,
    selected_reference: Optional[Dict[str, Any]] = None,
    htf_evidence: Optional[Sequence[Dict[str, Any]]] = None,
    min_displacement_body_atr: float = 0.40,
    strict_causal_binding: bool = False,
) -> UnicornEvidence:
    """
    Synthesizes ICT Unicorn Evidence (V17.8.0.1).
    Canonical contract supports both `selected_fvg` (for active candidate evaluation,
    never substituting an unrelated FVG) and `candidate_fvgs` (for batch/research scanning).
    """
    norm_dir = str(direction or "LONG").upper()
    duration_ms = _tf_duration_ms(timeframe)
    fallback_id = f"unicorn_{symbol}_{norm_dir}_{as_of_ms}"

    if norm_dir not in ("LONG", "SHORT"):
        return _build_rejected_evidence(
            fallback_id=fallback_id,
            symbol=symbol,
            norm_dir=norm_dir,
            as_of_ms=as_of_ms,
            status=UnicornSetupStatus.INVALID_DATA,
            reason_code="INVALID_DIRECTION",
        )

    # 0. Strict anti-lookahead & unclosed HTF evidence validation
    if confirmed_swings is not None:
        sh_in, sl_in = confirmed_swings
        for s in list(sh_in) + list(sl_in):
            if _swing_confirmed_ms(s) > as_of_ms or _swing_occurred_ms(s) > as_of_ms:
                return _build_rejected_evidence(
                    fallback_id=fallback_id,
                    symbol=symbol,
                    norm_dir=norm_dir,
                    as_of_ms=as_of_ms,
                    status=UnicornSetupStatus.FUTURE_DATA_REJECTED,
                    reason_code="FUTURE_CONFIRMED_SWING_REJECTED",
                )

    if htf_evidence is not None:
        for item in htf_evidence:
            if not isinstance(item, dict):
                continue
            item_tf = str(item.get("timeframe") or "5M")
            item_dur = _tf_duration_ms(item_tf)
            item_open = int(item.get("open_time") or item.get("timestamp") or 0)
            item_close = int(item.get("close_time_ms") or item.get("confirmed_at") or (item_open + item_dur))
            if item.get("is_closed") is False or item_close > as_of_ms:
                return _build_rejected_evidence(
                    fallback_id=fallback_id,
                    symbol=symbol,
                    norm_dir=norm_dir,
                    as_of_ms=as_of_ms,
                    status=UnicornSetupStatus.UNCLOSED_EVIDENCE_REJECTED,
                    reason_code="UNCLOSED_HTF_EVIDENCE_REJECTED",
                )

    # 1. Filter closed bars strictly <= as_of_ms
    valid_bars: List[Any] = []
    for b in closed_bars:
        t_open = _candle_open_ms(b)
        t_close = t_open + duration_ms
        if t_close <= as_of_ms:
            valid_bars.append(b)

    if len(valid_bars) < 4:
        return _build_rejected_evidence(
            fallback_id=fallback_id,
            symbol=symbol,
            norm_dir=norm_dir,
            as_of_ms=as_of_ms,
            status=UnicornSetupStatus.INVALID_DATA,
            reason_code="DATA_INSUFFICIENT",
        )

    # 2. Validate selected_fvg upfront if explicitly passed (cannot substitute another FVG)
    target_fvg_type = "BULLISH_FVG" if norm_dir == "LONG" else "BEARISH_FVG"
    if selected_fvg is not None:
        if not isinstance(selected_fvg, dict) or not selected_fvg:
            return _build_rejected_evidence(
                fallback_id=fallback_id,
                symbol=symbol,
                norm_dir=norm_dir,
                as_of_ms=as_of_ms,
                status=UnicornSetupStatus.MISSING_FVG,
                reason_code="FVG_NOT_FOUND",
            )
        if selected_fvg.get("is_closed") is False:
            return _build_rejected_evidence(
                fallback_id=fallback_id,
                symbol=symbol,
                norm_dir=norm_dir,
                as_of_ms=as_of_ms,
                status=UnicornSetupStatus.UNCLOSED_EVIDENCE_REJECTED,
                reason_code="UNCLOSED_FVG_EVIDENCE_REJECTED",
                fvg=selected_fvg,
            )
        fvg_tf = str(selected_fvg.get("timeframe") or timeframe)
        fvg_tf_dur = _tf_duration_ms(fvg_tf)
        fvg_conf_raw = int(selected_fvg.get("confirmed_at") or selected_fvg.get("timestamp") or 0)
        fvg_ts_raw = int(selected_fvg.get("timestamp") or selected_fvg.get("middle_bar_ms") or 0)
        if fvg_conf_raw > as_of_ms or fvg_ts_raw > as_of_ms:
            return _build_rejected_evidence(
                fallback_id=fallback_id,
                symbol=symbol,
                norm_dir=norm_dir,
                as_of_ms=as_of_ms,
                status=UnicornSetupStatus.FUTURE_DATA_REJECTED,
                reason_code="FUTURE_FVG_REJECTED",
                fvg=selected_fvg,
            )
        if fvg_tf_dur > duration_ms and fvg_ts_raw > 0 and (fvg_ts_raw + fvg_tf_dur) > as_of_ms:
            return _build_rejected_evidence(
                fallback_id=fallback_id,
                symbol=symbol,
                norm_dir=norm_dir,
                as_of_ms=as_of_ms,
                status=UnicornSetupStatus.UNCLOSED_EVIDENCE_REJECTED,
                reason_code="UNCLOSED_HTF_FVG_REJECTED",
                fvg=selected_fvg,
            )
        if not selected_fvg.get("id"):
            return _build_rejected_evidence(
                fallback_id=fallback_id,
                symbol=symbol,
                norm_dir=norm_dir,
                as_of_ms=as_of_ms,
                status=UnicornSetupStatus.MISSING_CAUSAL_PROVENANCE,
                reason_code="MISSING_FVG_ID",
                fvg=selected_fvg,
            )
        fvg_type_raw = selected_fvg.get("type")
        fvg_dir_raw = selected_fvg.get("direction")
        if (fvg_type_raw and str(fvg_type_raw).upper() != target_fvg_type) or (
            fvg_dir_raw and str(fvg_dir_raw).upper() != norm_dir
        ):
            return _build_rejected_evidence(
                fallback_id=fallback_id,
                symbol=symbol,
                norm_dir=norm_dir,
                as_of_ms=as_of_ms,
                status=UnicornSetupStatus.WRONG_DIRECTION_OVERLAP,
                reason_code="WRONG_DIRECTION_FVG_OVERLAP",
                fvg=selected_fvg,
            )
        try:
            s_bot = float(selected_fvg.get("bottom", 0.0))
            s_top = float(selected_fvg.get("top", 0.0))
        except (TypeError, ValueError):
            s_bot, s_top = 0.0, 0.0
        if s_bot <= 0.0 or s_top <= s_bot:
            return _build_rejected_evidence(
                fallback_id=fallback_id,
                symbol=symbol,
                norm_dir=norm_dir,
                as_of_ms=as_of_ms,
                status=UnicornSetupStatus.INVALID_DATA,
                reason_code="INVALID_FVG_GEOMETRY",
                fvg=selected_fvg,
            )
        fvg_state = str(selected_fvg.get("state") or "confirmed").lower()
        if (
            fvg_state in ("mitigated", "invalidated", "consumed", "filled", "expired")
            or bool(selected_fvg.get("is_consumed"))
            or bool(selected_fvg.get("is_invalidated"))
        ):
            return _build_rejected_evidence(
                fallback_id=fallback_id,
                symbol=symbol,
                norm_dir=norm_dir,
                as_of_ms=as_of_ms,
                status=UnicornSetupStatus.CONSUMED_OR_INVALIDATED_FVG,
                reason_code="FVG_CONSUMED_OR_INVALIDATED",
                fvg=selected_fvg,
            )

    # 3. Detect Breaker Blocks
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
        return _build_rejected_evidence(
            fallback_id=fallback_id,
            symbol=symbol,
            norm_dir=norm_dir,
            as_of_ms=as_of_ms,
            status=UnicornSetupStatus.MISSING_BREAKER,
            reason_code="BREAKER_NOT_FOUND",
            fvg=selected_fvg,
        )

    # 4. Resolve FVG list (NEVER substitute another FVG when selected_fvg is provided)
    if selected_fvg is not None:
        normalized_selected = dict(selected_fvg)
        if not normalized_selected.get("type"):
            normalized_selected["type"] = target_fvg_type
        fvgs = [normalized_selected]
    elif candidate_fvgs is not None:
        fvgs = list(candidate_fvgs)
    else:
        fvgs = smc_engine.detect_fvgs(valid_bars, timeframe=timeframe)

    if not fvgs:
        return _build_rejected_evidence(
            fallback_id=fallback_id,
            symbol=symbol,
            norm_dir=norm_dir,
            as_of_ms=as_of_ms,
            status=UnicornSetupStatus.MISSING_FVG,
            reason_code="FVG_NOT_FOUND",
            brk=breakers[-1],
        )

    # Check for future/consumed/wrong-direction FVGs in candidate_fvgs
    has_wrong_dir = False
    has_consumed = False
    has_future = False
    valid_fvgs: List[Dict[str, Any]] = []

    for f in fvgs:
        if not isinstance(f, dict):
            continue
        f_conf = int(f.get("confirmed_at") or f.get("timestamp") or 0)
        f_ts = int(f.get("timestamp") or f.get("middle_bar_ms") or 0)
        if f_conf > as_of_ms or f_ts > as_of_ms:
            has_future = True
            continue
        f_type = str(f.get("type") or "").upper()
        f_dir = str(f.get("direction") or "").upper()
        if (f_type and f_type != target_fvg_type) or (f_dir and f_dir != norm_dir):
            has_wrong_dir = True
            continue
        f_state = str(f.get("state") or "confirmed").lower()
        if (
            f_state in ("mitigated", "invalidated", "consumed", "filled", "expired")
            or bool(f.get("is_consumed"))
            or bool(f.get("is_invalidated"))
        ):
            has_consumed = True
            continue
        if float(f.get("bottom", 0.0)) < float(f.get("top", 0.0)):
            valid_fvgs.append(f)

    if not valid_fvgs:
        best_breaker = breakers[-1]
        if has_future:
            return _build_rejected_evidence(
                fallback_id=fallback_id,
                symbol=symbol,
                norm_dir=norm_dir,
                as_of_ms=as_of_ms,
                status=UnicornSetupStatus.FUTURE_DATA_REJECTED,
                reason_code="FUTURE_FVG_REJECTED",
                brk=best_breaker,
            )
        if has_consumed:
            return _build_rejected_evidence(
                fallback_id=fallback_id,
                symbol=symbol,
                norm_dir=norm_dir,
                as_of_ms=as_of_ms,
                status=UnicornSetupStatus.CONSUMED_OR_INVALIDATED_FVG,
                reason_code="FVG_CONSUMED_OR_INVALIDATED",
                brk=best_breaker,
            )
        if has_wrong_dir:
            return _build_rejected_evidence(
                fallback_id=fallback_id,
                symbol=symbol,
                norm_dir=norm_dir,
                as_of_ms=as_of_ms,
                status=UnicornSetupStatus.WRONG_DIRECTION_OVERLAP,
                reason_code="WRONG_DIRECTION_FVG_OVERLAP",
                brk=best_breaker,
            )
        return _build_rejected_evidence(
            fallback_id=fallback_id,
            symbol=symbol,
            norm_dir=norm_dir,
            as_of_ms=as_of_ms,
            status=UnicornSetupStatus.MISSING_FVG,
            reason_code="FVG_NOT_FOUND",
            brk=best_breaker,
        )

    # 5. Search for Breaker + FVG pairs with valid geometric overlap AND causal episode binding
    best_candidate: Optional[Tuple[BreakerBlock, Dict[str, Any], float, float]] = None
    episode_mismatch_candidate: Optional[Tuple[BreakerBlock, Dict[str, Any], float, float, str]] = None
    min_overlap_price = min_overlap_ticks * tick_size

    for brk in reversed(breakers):
        for fvg in reversed(valid_fvgs):
            fvg_top = float(fvg["top"])
            fvg_bot = float(fvg["bottom"])

            o_low = max(brk.low, fvg_bot)
            o_high = min(brk.high, fvg_top)

            if (o_high - o_low) >= (min_overlap_price - 1e-6):
                same_ep, ep_reason = _is_same_causal_episode(
                    brk=brk,
                    fvg=fvg,
                    valid_bars=valid_bars,
                    norm_dir=norm_dir,
                    duration_ms=duration_ms,
                    selected_break=selected_break,
                    selected_reference=selected_reference,
                    strict_causal_binding=strict_causal_binding,
                )
                if same_ep:
                    best_candidate = (brk, fvg, o_low, o_high)
                    break
                elif episode_mismatch_candidate is None:
                    episode_mismatch_candidate = (brk, fvg, o_low, o_high, ep_reason)
        if best_candidate:
            break

    if not best_candidate:
        if episode_mismatch_candidate is not None:
            m_brk, m_fvg, m_low, m_high, m_reason = episode_mismatch_candidate
            status = (
                UnicornSetupStatus.MISSING_CAUSAL_PROVENANCE
                if m_reason == "MISSING_CAUSAL_PROVENANCE"
                else UnicornSetupStatus.EPISODE_MISMATCH
            )
            return _build_rejected_evidence(
                fallback_id=fallback_id,
                symbol=symbol,
                norm_dir=norm_dir,
                as_of_ms=as_of_ms,
                status=status,
                reason_code=m_reason if status == UnicornSetupStatus.MISSING_CAUSAL_PROVENANCE else "CAUSAL_EPISODE_MISMATCH",
                brk=m_brk,
                fvg=m_fvg,
                overlap_low=m_low,
                overlap_high=m_high,
                known_at_ms=max(m_brk.confirmed_at_ms, int(m_fvg.get("confirmed_at") or 0)),
            )

        best_brk = breakers[-1]
        best_fvg = valid_fvgs[-1]
        return _build_rejected_evidence(
            fallback_id=fallback_id,
            symbol=symbol,
            norm_dir=norm_dir,
            as_of_ms=as_of_ms,
            status=UnicornSetupStatus.NO_GEOMETRIC_OVERLAP,
            reason_code="BREAKER_FVG_NO_OVERLAP",
            brk=best_brk,
            fvg=best_fvg,
            known_at_ms=max(best_brk.confirmed_at_ms, int(best_fvg.get("confirmed_at") or 0)),
        )

    brk, fvg, overlap_low, overlap_high = best_candidate
    if strict_causal_binding and not fvg.get("id"):
        return _build_rejected_evidence(
            fallback_id=fallback_id,
            symbol=symbol,
            norm_dir=norm_dir,
            as_of_ms=as_of_ms,
            status=UnicornSetupStatus.MISSING_CAUSAL_PROVENANCE,
            reason_code="MISSING_FVG_ID",
            brk=brk,
            fvg=fvg,
        )

    fvg_id = str(fvg.get("id") or f"fvg_{norm_dir}_{fvg['bottom']}_{fvg['top']}")
    fvg_conf = int(fvg.get("confirmed_at") or fvg.get("timestamp") or 0)
    fvg_bot = float(fvg["bottom"])
    fvg_top = float(fvg["top"])
    setup_known_ms = max(brk.confirmed_at_ms, fvg_conf)
    invalidation_price = brk.distal_price

    # 6. Invalidation Check: Did any closed bar breach distal price between setup_known_ms and as_of_ms?
    is_distal_invalidated = False
    is_fvg_consumed = False

    for b in valid_bars:
        t_open = _candle_open_ms(b)
        t_close = t_open + duration_ms
        if t_close <= setup_known_ms or t_close > as_of_ms:
            continue
        _, _, _, b_c = _candle_ohlc(b)
        if norm_dir == "LONG" and b_c < invalidation_price:
            is_distal_invalidated = True
            break
        if norm_dir == "SHORT" and b_c > invalidation_price:
            is_distal_invalidated = True
            break
        if norm_dir == "LONG" and b_c < fvg_bot:
            is_fvg_consumed = True
        if norm_dir == "SHORT" and b_c > fvg_top:
            is_fvg_consumed = True

    if is_distal_invalidated:
        return _build_rejected_evidence(
            fallback_id=f"unicorn_{symbol}_{norm_dir}_{brk.id}_{fvg_id}",
            symbol=symbol,
            norm_dir=norm_dir,
            as_of_ms=as_of_ms,
            status=UnicornSetupStatus.UNICORN_INVALIDATED,
            reason_code="DISTAL_LEVEL_BREACHED",
            brk=brk,
            fvg=fvg,
            overlap_low=overlap_low,
            overlap_high=overlap_high,
            known_at_ms=setup_known_ms,
        )

    if is_fvg_consumed:
        return _build_rejected_evidence(
            fallback_id=f"unicorn_{symbol}_{norm_dir}_{brk.id}_{fvg_id}",
            symbol=symbol,
            norm_dir=norm_dir,
            as_of_ms=as_of_ms,
            status=UnicornSetupStatus.CONSUMED_OR_INVALIDATED_FVG,
            reason_code="FVG_CONSUMED_BY_CLOSE_BEYOND_ZONE",
            brk=brk,
            fvg=fvg,
            overlap_low=overlap_low,
            overlap_high=overlap_high,
            known_at_ms=setup_known_ms,
        )

    # 7. Retest Check: Must occur strictly AFTER setup_known_ms
    explicit_retest_ms = fvg.get("retest_timestamp_ms")
    if explicit_retest_ms is not None and (int(explicit_retest_ms) + duration_ms) <= setup_known_ms:
        return _build_rejected_evidence(
            fallback_id=f"unicorn_{symbol}_{norm_dir}_{brk.id}_{fvg_id}",
            symbol=symbol,
            norm_dir=norm_dir,
            as_of_ms=as_of_ms,
            status=UnicornSetupStatus.RETEST_BEFORE_CONFIRMATION,
            reason_code="RETEST_BEFORE_CONFIRMATION",
            brk=brk,
            fvg=fvg,
            overlap_low=overlap_low,
            overlap_high=overlap_high,
            known_at_ms=setup_known_ms,
        )

    retest_bar_open_ms = None
    for b in valid_bars:
        t_open = _candle_open_ms(b)
        t_close = t_open + duration_ms
        if t_close <= setup_known_ms or t_close > as_of_ms:
            continue
        _, b_h, b_l, _ = _candle_ohlc(b)
        if b_h >= overlap_low and b_l <= overlap_high:
            retest_bar_open_ms = t_open
            break

    if retest_bar_open_ms is None:
        fvg_origin_open_ms = int(fvg.get("timestamp") or fvg.get("middle_bar_ms") or 0)
        for b in valid_bars:
            t_open = _candle_open_ms(b)
            t_close = t_open + duration_ms
            if t_open > fvg_origin_open_ms and t_close <= setup_known_ms:
                _, b_h, b_l, _ = _candle_ohlc(b)
                if b_h >= overlap_low and b_l <= overlap_high:
                    return _build_rejected_evidence(
                        fallback_id=f"unicorn_{symbol}_{norm_dir}_{brk.id}_{fvg_id}",
                        symbol=symbol,
                        norm_dir=norm_dir,
                        as_of_ms=as_of_ms,
                        status=UnicornSetupStatus.RETEST_BEFORE_CONFIRMATION,
                        reason_code="RETEST_BEFORE_CONFIRMATION",
                        brk=brk,
                        fvg=fvg,
                        overlap_low=overlap_low,
                        overlap_high=overlap_high,
                        known_at_ms=setup_known_ms,
                    )

        return _build_rejected_evidence(
            fallback_id=f"unicorn_{symbol}_{norm_dir}_{brk.id}_{fvg_id}",
            symbol=symbol,
            norm_dir=norm_dir,
            as_of_ms=as_of_ms,
            status=UnicornSetupStatus.WAITING_RETEST,
            reason_code="WAITING_CONFLUENCE_RETEST",
            brk=brk,
            fvg=fvg,
            overlap_low=overlap_low,
            overlap_high=overlap_high,
            known_at_ms=setup_known_ms,
        )

    # 8. Retested & Valid!
    unicorn_id = f"unicorn_{symbol}_{norm_dir}_{brk.id}_{fvg_id}_{retest_bar_open_ms}"
    final_known_ms = max(setup_known_ms, retest_bar_open_ms + duration_ms)
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
        breaker_source_ob_id=brk.source_ob_id,
        broken_swing_id=brk.broken_swing_id,
        fvg_id=fvg_id,
        selected_fvg_id=fvg_id,
        fvg_low=fvg_bot,
        fvg_high=fvg_top,
        fvg_confirmed_at_ms=fvg_conf,
        overlap_low=round(overlap_low, 2),
        overlap_high=round(overlap_high, 2),
        mss_or_bos_id=brk.mss_or_bos_id or brk.broken_swing_id,
        displacement_id=brk.displacement_id or brk.id,
        sweep_id=None,
        invalidation_source_id=brk.id,
        invalidation_price=invalidation_price,
        retest_timestamp_ms=retest_bar_open_ms,
        confirmed_at_ms=setup_known_ms,
        known_at_ms=final_known_ms,
        decision_cutoff_ms=as_of_ms,
        validity_status=UnicornSetupStatus.VALID.value,
        reason_codes=(),
    )
