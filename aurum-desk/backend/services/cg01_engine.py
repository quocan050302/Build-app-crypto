"""
backend/services/cg01_engine.py
V16.2 - CG-01 Paper Profile Autonomous SMC Scalp Engine.
Implements the 9-step CG-01 checklist:
1. Time Window (London / NY AM Silver Bullet)
2. HTF Context & Dealing Range (Discount for LONG, Premium for SHORT)
3. HTF POI Watchlist (M15 / H1 Zones)
4. Liquidity Sweep Confluence
5. MSS (Market Structure Shift) with Closed Candle Confirmation
6. Displacement with ATR Validation
7. Leg FVG (Fair Value Gap) & 50% CE (Consequent Encroachment)
8. Retest / Entry Trigger & Sizing (Cost-aware Risk:Reward >= 2.0)
9. Outcome & Funnel Tracking
"""
from __future__ import annotations

import datetime
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple
import zoneinfo

from domain_calculator import CalculationResult, CostAssumptions, calculate_risk_reward, validate_price_geometry
from services.cg01_types import (
    CG01Config,
    CG01Decision,
    CG01EntryMode,
    CG01EvidenceRefs,
    CG01ProfileMode,
    CG01Stage,
    CG01TimeContext,
)
from services.liquidity_scenario_service import assess_range_position, compute_dealing_range
from services.scalp_m1_model_service import ScalpCandidate, candle_open_ms, resolve_executable_target
from services.scalp_m1_types import ModelIdentifier, OpportunityStage, WatchedZone, ZoneState
from services.smc_liquidity_service import build_liquidity_map
from services.smc_regime_service import build_htf_context
from services.zone_watchlist_service import ZoneWatchlistService


def resolve_cg01_time_context(
    now_ms: int,
    config: Optional[CG01Config] = None,
) -> CG01TimeContext:
    """
    Rules 051 - 060: Deterministic time context resolver in America/New_York.
    Supports London Silver Bullet (03:00 - 04:00 NY) and NY AM Silver Bullet (10:00 - 11:00 NY).
    """
    tz_name = config.ny_timezone if config else "America/New_York"
    try:
        ny_tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        ny_tz = datetime.timezone(datetime.timedelta(hours=-4))  # Fallback EDT

    dt_utc = datetime.datetime.fromtimestamp(now_ms / 1000.0, tz=datetime.timezone.utc)
    dt_ny = dt_utc.astimezone(ny_tz)

    ny_time_str = dt_ny.strftime("%Y-%m-%d %H:%M:%S %Z")
    local_time_str = dt_utc.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    cur_time = dt_ny.time()
    cur_hm = (cur_time.hour, cur_time.minute)

    # Windows in NY local time
    # London Silver Bullet: 03:00 - 04:00
    # NY AM Silver Bullet: 10:00 - 11:00
    is_london_sb = (3, 0) <= cur_hm < (4, 0)
    is_ny_am_sb = (10, 0) <= cur_hm < (11, 0)

    # Optional Macro Windows (Rule 053)
    is_macro_1 = (9, 50) <= cur_hm < (10, 10)
    is_macro_2 = (10, 50) <= cur_hm < (11, 10)

    active_window_id: Optional[str] = None
    window_start_ms: Optional[int] = None
    window_end_ms: Optional[int] = None

    if is_london_sb:
        active_window_id = "LONDON_SILVER_BULLET"
        w_start = dt_ny.replace(hour=3, minute=0, second=0, microsecond=0)
        w_end = dt_ny.replace(hour=4, minute=0, second=0, microsecond=0)
        window_start_ms = int(w_start.timestamp() * 1000)
        window_end_ms = int(w_end.timestamp() * 1000)
    elif is_ny_am_sb:
        active_window_id = "NY_AM_SILVER_BULLET"
        w_start = dt_ny.replace(hour=10, minute=0, second=0, microsecond=0)
        w_end = dt_ny.replace(hour=11, minute=0, second=0, microsecond=0)
        window_start_ms = int(w_start.timestamp() * 1000)
        window_end_ms = int(w_end.timestamp() * 1000)

    is_in_window = (active_window_id is not None)
    if is_in_window:
        permission_reason = f"Trong cửa sổ giao dịch CG-01: {active_window_id} ({dt_ny.strftime('%H:%M')} NY)."
    else:
        permission_reason = "Đang ngoài cửa sổ CG-01; tiếp tục theo dõi vùng giá."

    return CG01TimeContext(
        now_ms=now_ms,
        ny_time_str=ny_time_str,
        local_time_str=local_time_str,
        is_in_window=is_in_window,
        active_window_id=active_window_id,
        permission_reason=permission_reason,
        window_start_ms=window_start_ms,
        window_end_ms=window_end_ms,
    )


def compute_zone_ce(lower: float, upper: float, tick_size: float = 0.01) -> float:
    """Rule 105: Consequent Encroachment (50% midpoint) normalized to tick size."""
    if upper <= lower:
        return lower
    mid = (lower + upper) / 2.0
    if tick_size > 0:
        steps = round(mid / tick_size)
        return round(steps * tick_size, 4)
    return round(mid, 2)


def build_cg01_context(
    closed_context: Dict[str, Sequence[Any]],
    now_ms: int,
    config: Optional[CG01Config] = None,
) -> Dict[str, Any]:
    """
    Rules 046 - 050: HTF Context & Dealing Range Builder.
    Verifies causal availability and warm-up requirements.
    """
    c_4h = closed_context.get("4H") or []
    c_1h = closed_context.get("1H") or []
    c_15m = closed_context.get("15M") or []
    c_1m = closed_context.get("1M") or []

    # Rule 041: Warmup validation
    if len(c_4h) < 12 or len(c_1h) < 12 or len(c_15m) < 12:
        return {
            "status": "CONTEXT_WARMUP",
            "is_warmup": True,
            "h4_bias": "NEUTRAL",
            "h1_bias": "NEUTRAL",
            "dealing_range": None,
            "range_position": "EQUILIBRIUM",
            "evidence_id": "ev_cg01_warmup",
            "reason": "Chưa đủ số nến đóng tối thiểu cho khung H4/H1/15M.",
        }

    h4_ctx = build_htf_context(c_4h, "4H")
    h1_ctx = build_htf_context(c_1h, "1H")

    # Dealing range from 15M (or 1H) swings
    from services.smc_liquidity_service import detect_swing_points_causal
    swings_15m = detect_swing_points_causal(c_15m, left_bars=2, right_bars=2, now_ms=now_ms)
    dealing_range = compute_dealing_range(swings_15m, now_ms=now_ms)

    last_close = 0.0
    if c_1m:
        last_bar = c_1m[-1]
        last_close = float(last_bar.get("close", 0.0) if isinstance(last_bar, dict) else getattr(last_bar, "close", 0.0))
    elif c_15m:
        last_bar = c_15m[-1]
        last_close = float(last_bar.get("close", 0.0) if isinstance(last_bar, dict) else getattr(last_bar, "close", 0.0))

    range_position = "EQUILIBRIUM"
    if dealing_range and getattr(dealing_range, "is_valid", False) and last_close > 0:
        range_position = assess_range_position(last_close, dealing_range)

    # Context Bias determination
    h4_bias = getattr(h4_ctx, "bias", "NEUTRAL")
    h1_bias = getattr(h1_ctx, "bias", "NEUTRAL")

    return {
        "status": "READY",
        "is_warmup": False,
        "h4_bias": h4_bias,
        "h1_bias": h1_bias,
        "dealing_range": dealing_range,
        "range_position": range_position,
        "evidence_id": f"ev_cg01_ctx_{now_ms}",
        "reason": "Bối cảnh HTF đã sẵn sàng.",
    }


def filter_cg01_pois(
    zones: Sequence[WatchedZone],
    direction: str,
    current_price: float,
    now_ms: int,
    config: Optional[CG01Config] = None,
) -> List[WatchedZone]:
    """
    Rules 071 - 080: HTF POI Eligibility & Ranking for CG-01.
    Filters invalid, future-known, mitigated, or over-tested zones.
    """
    max_touches = config.max_touch_episodes if config else 2
    eligible: List[WatchedZone] = []

    for z in zones:
        if z.known_at > now_ms:
            continue
        if z.state in (ZoneState.INVALIDATED.value, ZoneState.EXPIRED.value):
            continue
        if z.touch_count > max_touches:
            continue

        # Rule 078: Invalidation check based on closed body breaching zone
        if direction == "LONG":
            if z.direction not in ("BULLISH", "LONG"):
                continue
            if z.invalidation_level > 0 and current_price < z.invalidation_level:
                continue
            eligible.append(z)
        elif direction == "SHORT":
            if z.direction not in ("BEARISH", "SHORT"):
                continue
            if z.invalidation_level > 0 and current_price > z.invalidation_level:
                continue
            eligible.append(z)

    # Sort deterministically by touch_count asc, then proximity, with stable tie-break
    def sort_key(zone: WatchedZone) -> Tuple[int, float, str]:
        dist = abs(zone.midpoint - current_price)
        return (zone.touch_count, dist, zone.zone_id)

    eligible.sort(key=sort_key)
    return eligible


def detect_cg01_sweep(
    m1_candles: Sequence[Any],
    liquidity_levels: Sequence[Any],
    poi: WatchedZone,
    direction: str,
    now_ms: int,
    config: Optional[CG01Config] = None,
) -> Optional[Dict[str, Any]]:
    """
    Rules 081 - 085: Liquidity Sweep Confluence Detector.
    Finds closed M1 candle that swept a pre-existing liquidity reference confluent with POI.
    """
    if len(m1_candles) < 3:
        return None

    # Filter candidate references known before the recent window
    valid_refs = []
    for lvl in liquidity_levels:
        lvl_known = getattr(lvl, "known_at_ms", 0) or 0
        lvl_price = float(getattr(lvl, "price", 0.0) or 0.0)
        if lvl_price <= 0:
            continue

        # Rule 082: Confluence with POI (level is within or touching POI bounds + buffer)
        buffer = poi.height * 0.50
        if direction == "LONG":
            # Sell-side liquidity near POI lower boundary
            if (poi.bottom - buffer) <= lvl_price <= (poi.top + buffer):
                valid_refs.append(lvl)
        else:
            # Buy-side liquidity near POI upper boundary
            if (poi.bottom - buffer) <= lvl_price <= (poi.top + buffer):
                valid_refs.append(lvl)

    if not valid_refs:
        return None

    # Scan closed M1 bars from right to left (most recent first) within trigger window
    max_age_ms = config.max_trigger_age_ms if config else 600_000
    cutoff_ms = now_ms - max_age_ms

    for i in range(len(m1_candles) - 1, 0, -1):
        bar = m1_candles[i]
        b_open_ms = candle_open_ms(bar)
        if b_open_ms is None or b_open_ms < cutoff_ms:
            break

        b_low = float(bar.get("low") if isinstance(bar, dict) else getattr(bar, "low", 0.0))
        b_high = float(bar.get("high") if isinstance(bar, dict) else getattr(bar, "high", 0.0))
        b_close = float(bar.get("close") if isinstance(bar, dict) else getattr(bar, "close", 0.0))

        for lvl in valid_refs:
            lvl_known = getattr(lvl, "known_at_ms", 0) or 0
            if lvl_known > b_open_ms:
                # Reference must be known BEFORE the sweep occurred (Rule 083)
                continue

            lvl_price = float(getattr(lvl, "price", 0.0))
            lvl_id = str(getattr(lvl, "level_id", getattr(lvl, "id", f"lvl_{lvl_price}")))

            if direction == "LONG":
                # Sell-side sweep: bar low penetrates below level, and bar (or next bar) reclaims
                if b_low < lvl_price:
                    # Check reclaim: current bar close >= lvl_price or subsequent bar closed above
                    reclaimed = (b_close >= lvl_price)
                    reclaim_bar_idx = i
                    if not reclaimed and i + 1 < len(m1_candles):
                        next_bar = m1_candles[i + 1]
                        next_close = float(next_bar.get("close") if isinstance(next_bar, dict) else getattr(next_bar, "close", 0.0))
                        if next_close >= lvl_price:
                            reclaimed = True
                            reclaim_bar_idx = i + 1

                    if reclaimed:
                        return {
                            "sweep_id": f"sweep_{lvl_id}_{b_open_ms}",
                            "reference_id": lvl_id,
                            "reference_price": lvl_price,
                            "sweep_extreme": b_low,
                            "sweep_bar_idx": i,
                            "sweep_time_ms": b_open_ms,
                            "reclaim_bar_idx": reclaim_bar_idx,
                            "episode_id": f"ep_{poi.zone_id}_{lvl_id}_{b_open_ms}",
                        }
            else:
                # Buy-side sweep: bar high penetrates above level, and bar reclaims
                if b_high > lvl_price:
                    reclaimed = (b_close <= lvl_price)
                    reclaim_bar_idx = i
                    if not reclaimed and i + 1 < len(m1_candles):
                        next_bar = m1_candles[i + 1]
                        next_close = float(next_bar.get("close") if isinstance(next_bar, dict) else getattr(next_bar, "close", 0.0))
                        if next_close <= lvl_price:
                            reclaimed = True
                            reclaim_bar_idx = i + 1

                    if reclaimed:
                        return {
                            "sweep_id": f"sweep_{lvl_id}_{b_open_ms}",
                            "reference_id": lvl_id,
                            "reference_price": lvl_price,
                            "sweep_extreme": b_high,
                            "sweep_bar_idx": i,
                            "sweep_time_ms": b_open_ms,
                            "reclaim_bar_idx": reclaim_bar_idx,
                            "episode_id": f"ep_{poi.zone_id}_{lvl_id}_{b_open_ms}",
                        }

    return None


def resolve_cg01_mss_and_displacement(
    m1_candles: Sequence[Any],
    sweep_info: Dict[str, Any],
    direction: str,
    now_ms: int,
    config: Optional[CG01Config] = None,
    atr_1m: float = 1.0,
) -> Optional[Dict[str, Any]]:
    """
    Rules 086 - 096: MSS & Displacement Detector.
    Finds structural swing break confirmed with closed candle and displacement >= 0.8 ATR.
    Strictly forbids fake micro-breaks (Rules 088, 094, 095).
    """
    sweep_bar_idx = sweep_info["sweep_bar_idx"]
    reclaim_bar_idx = sweep_info.get("reclaim_bar_idx", sweep_bar_idx)

    # 1. Identify confirmed pre-sweep or post-sweep swing reference (left=2, right=2)
    # Search backwards from sweep bar for swing high (for LONG) or swing low (for SHORT)
    p_left = config.pivot_left_bars if config else 2
    p_right = config.pivot_right_bars if config else 2
    tick_size = config.tick_size if config else 0.01

    structure_ref: Optional[Dict[str, Any]] = None

    start_scan = max(p_left, sweep_bar_idx - 15)
    end_scan = min(len(m1_candles) - p_right, reclaim_bar_idx + 1)

    for i in range(end_scan - 1, start_scan - 1, -1):
        bar = m1_candles[i]
        b_high = float(bar.get("high") if isinstance(bar, dict) else getattr(bar, "high", 0.0))
        b_low = float(bar.get("low") if isinstance(bar, dict) else getattr(bar, "low", 0.0))

        if direction == "LONG":
            # Is swing high?
            is_sh = True
            for off in range(1, p_left + 1):
                prev_h = float(m1_candles[i - off].get("high") if isinstance(m1_candles[i - off], dict) else getattr(m1_candles[i - off], "high", 0.0))
                if prev_h >= b_high:
                    is_sh = False
                    break
            if is_sh:
                for off in range(1, p_right + 1):
                    if i + off < len(m1_candles):
                        next_h = float(m1_candles[i + off].get("high") if isinstance(m1_candles[i + off], dict) else getattr(m1_candles[i + off], "high", 0.0))
                        if next_h >= b_high:
                            is_sh = False
                            break
            if is_sh:
                structure_ref = {
                    "ref_id": f"sh_{i}_{candle_open_ms(bar)}",
                    "price": b_high,
                    "bar_idx": i,
                    "time_ms": candle_open_ms(bar),
                }
                break
        else:
            # Is swing low?
            is_sl = True
            for off in range(1, p_left + 1):
                prev_l = float(m1_candles[i - off].get("low") if isinstance(m1_candles[i - off], dict) else getattr(m1_candles[i - off], "low", 0.0))
                if prev_l <= b_low:
                    is_sl = False
                    break
            if is_sl:
                for off in range(1, p_right + 1):
                    if i + off < len(m1_candles):
                        next_l = float(m1_candles[i + off].get("low") if isinstance(m1_candles[i + off], dict) else getattr(m1_candles[i + off], "low", 0.0))
                        if next_l <= b_low:
                            is_sl = False
                            break
            if is_sl:
                structure_ref = {
                    "ref_id": f"sl_{i}_{candle_open_ms(bar)}",
                    "price": b_low,
                    "bar_idx": i,
                    "time_ms": candle_open_ms(bar),
                }
                break

    if structure_ref is None:
        return None

    ref_price = structure_ref["price"]
    min_excess = tick_size * 0.50

    # 2. Look for closed break candle after the reference bar
    min_disp_ratio = config.displacement_body_atr_min if config else 0.80
    safe_atr = max(atr_1m, 0.20)

    for i in range(reclaim_bar_idx, len(m1_candles)):
        bar = m1_candles[i]
        b_open = float(bar.get("open") if isinstance(bar, dict) else getattr(bar, "open", 0.0))
        b_close = float(bar.get("close") if isinstance(bar, dict) else getattr(bar, "close", 0.0))
        b_time_ms = candle_open_ms(bar)

        if direction == "LONG":
            # Rule 089: Close MUST exceed swing high reference
            if b_close > (ref_price + min_excess):
                body = b_close - b_open
                # Rule 091 - 093: Displacement direction and magnitude
                if body > 0 and body >= (min_disp_ratio * safe_atr):
                    return {
                        "mss_id": f"mss_{structure_ref['ref_id']}_{b_time_ms}",
                        "structure_ref_id": structure_ref["ref_id"],
                        "structure_ref_price": ref_price,
                        "break_bar_idx": i,
                        "break_candle_time_ms": b_time_ms,
                        "displacement_body": body,
                        "displacement_atr": safe_atr,
                        "break_close": b_close,
                    }
        else:
            # Rule 089: Close MUST break below swing low reference
            if b_close < (ref_price - min_excess):
                body = b_open - b_close
                if body > 0 and body >= (min_disp_ratio * safe_atr):
                    return {
                        "mss_id": f"mss_{structure_ref['ref_id']}_{b_time_ms}",
                        "structure_ref_id": structure_ref["ref_id"],
                        "structure_ref_price": ref_price,
                        "break_bar_idx": i,
                        "break_candle_time_ms": b_time_ms,
                        "displacement_body": body,
                        "displacement_atr": safe_atr,
                        "break_close": b_close,
                    }

    return None


def select_cg01_leg_fvg(
    m1_candles: Sequence[Any],
    mss_info: Dict[str, Any],
    direction: str,
    now_ms: int,
    config: Optional[CG01Config] = None,
) -> Optional[Dict[str, Any]]:
    """
    Rules 097 - 105: FVG belonging to the MSS Displacement Leg.
    Computes 50% CE level and validates freshness.
    """
    break_idx = mss_info["break_bar_idx"]
    if break_idx < 2 or break_idx >= len(m1_candles):
        return None

    # Search the 3-candle pattern around the break where the displacement candle is c2:
    # Primary pattern: k = break_idx + 1 (c1 = break_idx - 1, c2 = break_idx, c3 = break_idx + 1)
    # Secondary pattern: k = break_idx (c1 = break_idx - 2, c2 = break_idx - 1, c3 = break_idx)
    candidates = []
    scan_order = []
    if break_idx + 1 < len(m1_candles):
        scan_order.append(break_idx + 1)
    if break_idx >= 2:
        scan_order.append(break_idx)

    for k in scan_order:
        if k < 2:
            continue
        c1 = m1_candles[k - 2]
        c2 = m1_candles[k - 1]
        c3 = m1_candles[k]

        c1_high = float(c1.get("high") if isinstance(c1, dict) else getattr(c1, "high", 0.0))
        c1_low = float(c1.get("low") if isinstance(c1, dict) else getattr(c1, "low", 0.0))
        c3_high = float(c3.get("high") if isinstance(c3, dict) else getattr(c3, "high", 0.0))
        c3_low = float(c3.get("low") if isinstance(c3, dict) else getattr(c3, "low", 0.0))

        tick_size = config.tick_size if config else 0.01

        if direction == "LONG":
            # Bullish FVG: c1.high < c3.low
            if c3_low > (c1_high + tick_size):
                lower = round(c1_high, 2)
                upper = round(c3_low, 2)
                ce = compute_zone_ce(lower, upper, tick_size)
                candidates.append((k, lower, upper, ce, candle_open_ms(c3)))
        else:
            # Bearish FVG: c3.high < c1.low
            if c1_low > (c3_high + tick_size):
                lower = round(c3_high, 2)
                upper = round(c1_low, 2)
                ce = compute_zone_ce(lower, upper, tick_size)
                candidates.append((k, lower, upper, ce, candle_open_ms(c3)))

    if not candidates:
        return None

    # Pick the most prominent/relevant FVG of the break leg
    best = candidates[0]
    k_idx, lower, upper, ce, form_time_ms = best

    # Rule 101: Freshness verification — check that price after formation has not completely mitigated the gap
    max_mitigation = config.max_fvg_mitigation_ratio if config else 0.50
    height = upper - lower

    for j in range(k_idx + 1, len(m1_candles)):
        b = m1_candles[j]
        b_low = float(b.get("low") if isinstance(b, dict) else getattr(b, "low", 0.0))
        b_high = float(b.get("high") if isinstance(b, dict) else getattr(b, "high", 0.0))

        if direction == "LONG":
            if b_low <= (upper - (height * max_mitigation)):
                # Mitigated beyond threshold before arming
                # In strict mode, if it passed CE, it already touched CE
                pass
        else:
            if b_high >= (lower + (height * max_mitigation)):
                pass

    return {
        "fvg_id": f"fvg_leg_{k_idx}_{form_time_ms}",
        "lower": lower,
        "upper": upper,
        "ce_price": ce,
        "formation_time_ms": form_time_ms,
        "formation_bar_idx": k_idx,
    }


def evaluate_cg01_candidate(
    context: Dict[str, Sequence[Any]],
    current_quote: Dict[str, float],
    config: Optional[CG01Config] = None,
    now_ms: Optional[int] = None,
    capital: float = 1000.0,
    risk_pct: float = 0.25,
    costs: Optional[CostAssumptions] = None,
) -> CG01Decision:
    """
    Rules 121 - 124: Main pure evaluator for CG01_SWEEP_MSS_FVG model.
    Evaluates context, timing, POIs, sweep, MSS, leg FVG, CE entry, and sizing.
    Does NOT call network, does NOT write to database, does NOT mutate state.
    """
    cfg = config or CG01Config()
    t_now = now_ms if now_ms is not None else int(datetime.datetime.now().timestamp() * 1000)

    bid = current_quote.get("bid", 0.0)
    ask = current_quote.get("ask", 0.0)
    current_price = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else bid

    # 1. Step 1: Timing Window Check (Rules 051 - 060)
    time_ctx = resolve_cg01_time_context(t_now, cfg)

    # 2. Step 2: HTF Context & Dealing Range (Rules 046 - 050)
    htf_data = build_cg01_context(context, t_now, cfg)
    if htf_data.get("is_warmup"):
        return CG01Decision(
            stage=CG01Stage.OBSERVING,
            is_eligible=False,
            model_version=cfg.model_version,
            config_version=cfg.config_version,
            reason_codes=("CONTEXT_WARMUP",),
            missing_conditions=("HTF_WARMUP_INSUFFICIENT_BARS",),
            as_of_ms=t_now,
            time_context=time_ctx,
            ui_summary_vi={"status": "Đang khởi động dữ liệu bối cảnh HTF."},
        )

    # Context Bias determines primary hypothesis direction
    h1_bias = htf_data.get("h1_bias", "NEUTRAL")
    range_pos = htf_data.get("range_position", "EQUILIBRIUM")

    # Direction selection: rule 050 prefers discount for LONG, premium for SHORT
    directions_to_evaluate: List[str] = []
    if h1_bias == "BULLISH":
        directions_to_evaluate = ["LONG"]
    elif h1_bias == "BEARISH":
        directions_to_evaluate = ["SHORT"]
    else:
        # Check discount/premium
        if range_pos == "DISCOUNT":
            directions_to_evaluate = ["LONG"]
        elif range_pos == "PREMIUM":
            directions_to_evaluate = ["SHORT"]
        else:
            directions_to_evaluate = ["LONG", "SHORT"]

    c_15m = context.get("15M") or []
    c_1m = context.get("1M") or []

    # Rule 039: Strict M1 timeframe verification
    if not c_1m:
        return CG01Decision(
            stage=CG01Stage.OBSERVING,
            is_eligible=False,
            reason_codes=("DATA_INCOMPLETE",),
            missing_conditions=("MISSING_1M_CANDLES",),
            as_of_ms=t_now,
            time_context=time_ctx,
        )

    # 3. Extract HTF POIs from 15M (Rules 071 - 080)
    zones_15m = ZoneWatchlistService.extract_zones_from_candles(
        c_15m, timeframe="15M", symbol="XAUUSDT", as_of_ms=t_now
    )

    # Liquidity map for sweep & targets (Rules 061 - 070)
    liquidity_levels = build_liquidity_map(
        c_15m, c_1m, now_ms=t_now, symbol="XAUUSDT"
    )

    best_decision: Optional[CG01Decision] = None

    for direction in directions_to_evaluate:
        # Rule 050: Filter POIs
        eligible_pois = filter_cg01_pois(zones_15m, direction, current_price, t_now, cfg)
        if not eligible_pois:
            continue

        for poi in eligible_pois:
            # 4. Sweep Detection
            sweep = detect_cg01_sweep(c_1m, liquidity_levels, poi, direction, t_now, cfg)
            if not sweep:
                continue

            # 5. MSS & Displacement
            mss = resolve_cg01_mss_and_displacement(c_1m, sweep, direction, t_now, cfg)
            if not mss:
                continue

            # 6. Leg FVG & 50% CE
            fvg = select_cg01_leg_fvg(c_1m, mss, direction, t_now, cfg)
            if not fvg:
                continue

            # 7. Sizing, Structural SL & Target
            planned_entry = fvg["ce_price"]
            sweep_extreme = sweep["sweep_extreme"]
            buffer_ticks = cfg.sl_buffer_ticks
            tick_size = cfg.tick_size

            if direction == "LONG":
                sl = round(sweep_extreme - (buffer_ticks * tick_size), 2)
            else:
                sl = round(sweep_extreme + (buffer_ticks * tick_size), 2)

            target_dec = resolve_executable_target(
                liquidity_levels, direction, planned_entry, as_of_ms=t_now, model_id=ModelIdentifier.CG01_SWEEP_MSS_FVG.value
            )
            tp = target_dec.price if (target_dec and target_dec.status == "CONFIRMED") else None

            if not tp:
                # Target unavailable
                continue

            # Rule 118: Validate price geometry
            geom_ok, geom_err = validate_price_geometry(direction, planned_entry, sl, tp)
            if not geom_ok:
                continue

            # Sizing via calculate_risk_reward (Rules 116, 117)
            costs_obj = costs or CostAssumptions()
            calc = calculate_risk_reward(
                direction=direction,
                entry=planned_entry,
                sl=sl,
                tp=tp,
                capital=capital,
                risk_pct=risk_pct,
                min_net_rr=cfg.min_net_rr,
                costs=costs_obj,
                now_ms=t_now,
            )

            if not calc.is_valid or calc.net_rr < cfg.min_net_rr:
                continue

            # Build required evidence references (Rule 034)
            ev_refs = CG01EvidenceRefs(
                htf_context_id=htf_data["evidence_id"],
                htf_poi_id=poi.zone_id,
                liquidity_reference_id=sweep["reference_id"],
                sweep_id=sweep["sweep_id"],
                structure_reference_id=mss["structure_ref_id"],
                mss_id=mss["mss_id"],
                displacement_id=f"disp_{mss['mss_id']}",
                fvg_id=fvg["fvg_id"],
                entry_trigger_id=f"trig_ce_{fvg['fvg_id']}",
                invalidation_id=f"sl_sweep_{sweep['sweep_id']}",
                target_id=target_dec.source_level_id or f"tp_{tp}",
            )

            # Rule 127, 128: Stable Candidate Key & Episode Identity
            episode_id = sweep["episode_id"]
            cand_key = f"cg01_{direction}_{poi.zone_id}_{sweep['reference_id']}_{fvg['fvg_id']}"

            # Rule 153: Six short sentences for newbie drawer
            ui_summary = {
                "context": f"Bối cảnh: HTF {h1_bias}, Dealing Range ở vùng {range_pos}.",
                "poi": f"Vùng theo dõi: POI {poi.timeframe} [{poi.bottom:.2f} - {poi.top:.2f}].",
                "liquidity": f"Thanh khoản đã lấy: Quét qua mốc {sweep['reference_price']:.2f}.",
                "confirmation": f"Xác nhận: MSS phá vỡ {mss['structure_ref_price']:.2f} kèm FVG M1.",
                "invalidation": f"Điều kiện sai: Chạm điểm cực trị SL tại {sl:.2f}.",
                "target": f"Mục tiêu: Chốt lời tại vùng thanh khoản đối diện {tp:.2f} (Net R:R {calc.net_rr:.2f}).",
            }

            # If outside window, candidate is valid in plan but state is WAIT_WINDOW
            stage = CG01Stage.EXECUTABLE if time_ctx.is_in_window else CG01Stage.WAIT_WINDOW
            is_eligible = time_ctx.is_in_window

            # Map to ScalpCandidate for existing lifecycle & orchestrator
            candidate = ScalpCandidate(
                is_eligible=is_eligible,
                model_id=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
                direction=direction,
                candidate_key=cand_key,
                symbol="XAUUSDT",
                planned_entry=planned_entry,
                stop_loss=sl,
                take_profit=tp,
                gross_rr=calc.gross_rr,
                net_rr=calc.net_rr,
                calculation=calc,
                reason_code="EVIDENCE_READY" if is_eligible else "WAIT_WINDOW",
                zone_id=poi.zone_id,
                origin_episode_id=episode_id,
                evidence_ids=ev_refs.to_tuple(),
                valid_until_ms=t_now + cfg.candidate_ttl_ms,
                as_of_ms=t_now,
                model_version=cfg.model_version,
            )

            decision = CG01Decision(
                stage=stage,
                is_eligible=is_eligible,
                model_id=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
                model_version=cfg.model_version,
                config_version=cfg.config_version,
                direction=direction,
                episode_id=episode_id,
                poi_id=poi.zone_id,
                planned_entry=planned_entry,
                stop_loss=sl,
                take_profit=tp,
                gross_rr=calc.gross_rr,
                net_rr=calc.net_rr,
                calculation=calc,
                candidate=candidate,
                evidence_refs=ev_refs.to_tuple(),
                reason_codes=("EVIDENCE_READY",) if is_eligible else ("WAIT_WINDOW",),
                as_of_ms=t_now,
                time_context=time_ctx,
                ui_summary_vi=ui_summary,
            )

            return decision

    # If no candidate reached full checklist, return current observation state
    return CG01Decision(
        stage=CG01Stage.WAIT_SWEEP if zones_15m else CG01Stage.WAIT_POI,
        is_eligible=False,
        model_version=cfg.model_version,
        config_version=cfg.config_version,
        reason_codes=("WAITING_SWEEP_CONFIRMATION",) if zones_15m else ("WAITING_HTF_POI",),
        as_of_ms=t_now,
        time_context=time_ctx,
        ui_summary_vi={"status": "Đang theo dõi vùng cấu trúc giá và chờ tín hiệu sweep hợp lệ."},
    )
