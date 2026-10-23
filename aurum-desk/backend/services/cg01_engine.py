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
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
import zoneinfo

from domain_calculator import CalculationResult, CostAssumptions, calculate_risk_reward, validate_price_geometry
from services.cg01_context_adapter import (
    CanonicalCandle,
    CanonicalQuote,
    adapt_cg01_quote,
    assess_cg01_context_readiness,
    build_cg01_closed_context,
    build_cg01_liquidity_context,
    normalize_cg01_candle,
    resolve_cg01_retest,
    select_closed_candles,
)
from services.cg01_evidence_adapter import (
    build_cg01_evidence_nodes,
    build_cg01_rejection_summary,
    build_cg01_snapshot,
    build_cg01_trigger_chain,
)
from services.cg01_types import (
    CG01CandidateDiagnostic,
    CG01Config,
    CG01Decision,
    CG01EntryIntent,
    CG01EntryMode,
    CG01EvidenceRefs,
    CG01ProfileMode,
    CG01Stage,
    CG01TimeContext,
    ExecutionDeadline,
    resolve_cg01_execution_deadline,
    resolve_cg01_order_type,
    resolve_slippage_usd,
)
from services.liquidity_scenario_service import (
    assess_range_position,
    compute_dealing_range,
    identify_confirmed_swings,
)
from services.scalp_m1_model_service import ScalpCandidate, candle_open_ms, resolve_executable_target
from services.scalp_m1_types import ModelIdentifier, OpportunityStage, WatchedZone, ZoneState
from services.htf_structure_service import filter_closed_candles, resolve_cg01_direction_context
from services.cg01_daily_bias_service import resolve_cg01_entry_direction_policy
from services.cg01_types import CG01DirectionPolicy
from services.smc_liquidity_service import build_liquidity_map, update_liquidity_states
from services.smc_regime_service import build_htf_context
from services.zone_watchlist_service import ZoneWatchlistService


def resolve_cg01_windows_for_profile(
    dt_ny: datetime.datetime,
    profile: str = "SILVER_BULLET_LONDON_NY",
    execution_scope: str = "PAPER",
) -> Tuple[Optional[str], Optional[int], Optional[int], str]:
    """
    Rules 037 - 043: Resolve active window, start/end timestamps, and effective profile.
    - SILVER_BULLET_LONDON_NY: London (03:00 - 04:00 NY), NY AM (10:00 - 11:00 NY).
    - CG01_NY_PAPER_EXPANDED (PAPER only): London (03:00 - 04:00 NY), NY Expanded (08:00 - 11:00 NY).
      Scope check: if execution_scope is not PAPER, falls back to SILVER_BULLET_LONDON_NY.
    - Start is inclusive, end is exclusive.
    """
    cur_hm = (dt_ny.hour, dt_ny.minute)

    # Scope verification: CG01_NY_PAPER_EXPANDED and CG01_ALL_SESSIONS_PAPER are strictly PAPER-only (Rules 041, V17.4 Parts 041-044)
    effective_profile = profile
    is_paper_scope = execution_scope.upper() in (
        "PAPER", "PAPER_EXECUTION", "PAPER_SHADOW", "SHADOW", "SIMULATION"
    )

    if profile == "CG01_ALL_SESSIONS_PAPER":
        if not is_paper_scope:
            # LIVE / REAL / LIVE_EXECUTION cannot receive ALL_SESSIONS_PAPER permission (Parts 016, 042, 153)
            return None, None, None, "SILVER_BULLET_LONDON_NY"
        return "ALL_SESSIONS_PAPER", None, None, profile

    if profile == "CG01_NY_PAPER_EXPANDED" and not is_paper_scope:
        effective_profile = "SILVER_BULLET_LONDON_NY"

    active_window_id: Optional[str] = None
    window_start_ms: Optional[int] = None
    window_end_ms: Optional[int] = None

    if effective_profile == "CG01_NY_PAPER_EXPANDED":
        # London Silver Bullet: 03:00 - 04:00
        # NY PAPER Expanded: 08:00 - 11:00 (start inclusive, end exclusive)
        if (3, 0) <= cur_hm < (4, 0):
            active_window_id = "LONDON_SILVER_BULLET"
            w_start = dt_ny.replace(hour=3, minute=0, second=0, microsecond=0)
            w_end = dt_ny.replace(hour=4, minute=0, second=0, microsecond=0)
            window_start_ms = int(w_start.timestamp() * 1000)
            window_end_ms = int(w_end.timestamp() * 1000)
        elif (8, 0) <= cur_hm < (11, 0):
            active_window_id = "NY_PAPER_EXPANDED"
            w_start = dt_ny.replace(hour=8, minute=0, second=0, microsecond=0)
            w_end = dt_ny.replace(hour=11, minute=0, second=0, microsecond=0)
            window_start_ms = int(w_start.timestamp() * 1000)
            window_end_ms = int(w_end.timestamp() * 1000)
    else:
        # Default SILVER_BULLET_LONDON_NY
        if (3, 0) <= cur_hm < (4, 0):
            active_window_id = "LONDON_SILVER_BULLET"
            w_start = dt_ny.replace(hour=3, minute=0, second=0, microsecond=0)
            w_end = dt_ny.replace(hour=4, minute=0, second=0, microsecond=0)
            window_start_ms = int(w_start.timestamp() * 1000)
            window_end_ms = int(w_end.timestamp() * 1000)
        elif (10, 0) <= cur_hm < (11, 0):
            active_window_id = "NY_AM_SILVER_BULLET"
            w_start = dt_ny.replace(hour=10, minute=0, second=0, microsecond=0)
            w_end = dt_ny.replace(hour=11, minute=0, second=0, microsecond=0)
            window_start_ms = int(w_start.timestamp() * 1000)
            window_end_ms = int(w_end.timestamp() * 1000)

    return active_window_id, window_start_ms, window_end_ms, effective_profile


def resolve_cg01_time_context(
    now_ms: int,
    config: Optional[CG01Config] = None,
) -> CG01TimeContext:
    """
    Rules 051 - 060, V17.4 Parts 041-045: Deterministic time context resolver in America/New_York.
    Supports London Silver Bullet (03:00 - 04:00 NY), NY AM (10:00 - 11:00 NY),
    CG01_NY_PAPER_EXPANDED (08:00 - 11:00 NY, PAPER only), and
    CG01_ALL_SESSIONS_PAPER (all sessions setup-first, PAPER only).
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

    req_profile = getattr(config, "timing_profile", "SILVER_BULLET_LONDON_NY") or "SILVER_BULLET_LONDON_NY"
    exec_scope = "PAPER"
    if config:
        pm = getattr(config, "profile_mode", "PAPER_EXECUTION")
        if pm in ("LIVE", "LIVE_EXECUTION", "REAL"):
            exec_scope = "LIVE"

    active_window_id, window_start_ms, window_end_ms, eff_profile = resolve_cg01_windows_for_profile(
        dt_ny,
        profile=req_profile,
        execution_scope=exec_scope,
    )

    is_in_window = (active_window_id is not None)
    if active_window_id == "ALL_SESSIONS_PAPER":
        permission_reason = "Cho phép xét setup PAPER ở mọi phiên; vẫn kiểm tra cấu trúc và bảo vệ rủi ro"
    elif is_in_window:
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
        timing_profile=eff_profile,
        execution_scope=exec_scope,
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
    c_4h = [normalize_cg01_candle(b, "4H") if not isinstance(b, CanonicalCandle) else b for b in (closed_context.get("4H") or [])]
    c_1h = [normalize_cg01_candle(b, "1H") if not isinstance(b, CanonicalCandle) else b for b in (closed_context.get("1H") or [])]
    c_15m = [normalize_cg01_candle(b, "15M") if not isinstance(b, CanonicalCandle) else b for b in (closed_context.get("15M") or [])]
    c_1m = [normalize_cg01_candle(b, "1M") if not isinstance(b, CanonicalCandle) else b for b in (closed_context.get("1M") or [])]

    # Rule 024 (V16.14): Single closed candle filter: strictly not unclosed AND close_ms <= now_ms
    from services.htf_structure_service import resolve_timeframe_ms

    def _is_valid_closed_bar(b: Any, tf: str) -> bool:
        if isinstance(b, dict):
            if b.get("is_closed") is False:
                return False
            o_time = b.get("open_time", b.get("timestamp", 0))
            c_time = b.get("close_time")
        else:
            if getattr(b, "is_closed", True) is False:
                return False
            o_time = getattr(b, "open_time", getattr(b, "timestamp", 0))
            c_time = getattr(b, "close_time", None)

        dur = resolve_timeframe_ms(tf)
        close_ms = c_time if c_time is not None else (o_time + dur)
        return close_ms <= now_ms

    c_4h_closed = [b for b in c_4h if _is_valid_closed_bar(b, "4H")]
    c_1h_closed = [b for b in c_1h if _is_valid_closed_bar(b, "1H")]
    c_15m_closed = [b for b in c_15m if _is_valid_closed_bar(b, "15M")]
    c_1m_closed = [b for b in c_1m if _is_valid_closed_bar(b, "1M")]

    # Rule 045: Warmup validation with dynamic config min_bars
    min_b = getattr(config, "htf_min_bars", 12) if config else 12
    if len(c_4h_closed) < min_b or len(c_1h_closed) < min_b or len(c_15m_closed) < min_b:
        return {
            "status": "CONTEXT_WARMUP",
            "is_warmup": True,
            "h4_bias": "NEUTRAL",
            "h1_bias": "NEUTRAL",
            "dealing_range": None,
            "range_position": "EQUILIBRIUM",
            "evidence_id": "ev_cg01_warmup",
            "reason": "Chưa đủ số nến đóng tối thiểu cho khung H4/H1/15M.",
            "context_source": closed_context.get("context_source", "DECISION_CONTEXT"),
        }

    h4_ctx = build_htf_context(c_4h_closed, "4H", as_of_ms=now_ms, config=config, min_bars=min_b)
    h1_ctx = build_htf_context(c_1h_closed, "1H", as_of_ms=now_ms, config=config, min_bars=min_b)

    # Dealing range from 15M swings (Rules 036 - 038)
    left_b = config.pivot_left_bars if config else 2
    right_b = config.pivot_right_bars if config else 2
    shs, sls = identify_confirmed_swings(
        c_15m_closed,
        timeframe="15M",
        left_bars=left_b,
        right_bars=right_b,
        symbol="XAUUSDT",
        as_of_ms=now_ms,
    )
    dealing_range = compute_dealing_range([*shs, *sls], now_ms=now_ms, symbol="XAUUSDT")

    # Rule 098, 113, 114: Causal price for range position: M1 closed first, then M15 closed. NO FUTURE FALLBACK!
    last_close = 0.0
    if c_1m_closed:
        last_bar = c_1m_closed[-1]
        last_close = float(last_bar.get("close", 0.0) if isinstance(last_bar, dict) else getattr(last_bar, "close", 0.0))
    elif c_15m_closed:
        last_bar = c_15m_closed[-1]
        last_close = float(last_bar.get("close", 0.0) if isinstance(last_bar, dict) else getattr(last_bar, "close", 0.0))

    range_position = "EQUILIBRIUM"
    if dealing_range and getattr(dealing_range, "is_valid", False) and last_close > 0:
        range_position = assess_range_position(last_close, dealing_range)

    # Context Bias & Direction Context determination (V16.11, V16.30 Daily Bias)
    policy_res = resolve_cg01_entry_direction_policy(
        closed_context=closed_context,
        config=config,
        as_of_ms=now_ms,
        symbol="XAUUSDT",
        h4_ctx=h4_ctx,
        h1_ctx=h1_ctx,
        range_pos=range_position,
    )
    dir_ctx = policy_res.get("direction_context") or {}
    daily_bias = policy_res.get("daily_bias")
    direction_policy = policy_res.get("direction_policy", "LEGACY_H1")

    h4_bias = getattr(h4_ctx, "bias", "UNKNOWN")
    h1_bias = getattr(h1_ctx, "bias", "UNKNOWN")

    return {
        "status": "READY",
        "is_warmup": False,
        "h4_bias": h4_bias,
        "h1_bias": h1_bias,
        "h4_snapshot": h4_ctx.to_dict(),
        "h1_snapshot": h1_ctx.to_dict(),
        "direction_context": dir_ctx,
        "relation": dir_ctx.get("relation", "UNKNOWN"),
        "directions_to_evaluate": policy_res.get("directions_to_evaluate", []),
        "dealing_range": dealing_range,
        "range_position": range_position,
        "daily_bias": daily_bias,
        "direction_policy": direction_policy,
        "evidence_id": f"ev_cg01_ctx_{now_ms}",
        "reason": "Bối cảnh HTF đã sẵn sàng.",
        "context_source": closed_context.get("context_source", "DECISION_CONTEXT"),
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
        lvl_side = getattr(lvl, "side", "")
        if lvl_price <= 0:
            continue

        # Rule 061, 062, 082:
        # LONG sweeps sell-side liquidity (lows/EQL) near/in POI
        # SHORT sweeps buy-side liquidity (highs/EQH) near/in POI
        buffer = poi.height * 0.50
        if direction == "LONG":
            if lvl_side and lvl_side != "SELL_SIDE":
                continue
            if (poi.bottom - buffer) <= lvl_price <= (poi.top + buffer):
                valid_refs.append(lvl)
        else:
            if lvl_side and lvl_side != "BUY_SIDE":
                continue
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


def build_cg01_displacement_atr_series(
    closed_m1: Sequence[Any],
    period: int = 14,
    as_of_ms: Optional[int] = None,
) -> Dict[int, float]:
    """
    V16.28 Section B & Items 136-146: Causal per-bar M1 ATR series for displacement confirmation.
    Computes Simple Moving Average (SMA / arithmetic rolling mean) of True Range over `period` completed
    bars strictly preceding each candle timestamp (pre-break volatility baseline), with prefix invariance
    and zero lookahead. Note: This uses rolling arithmetic mean over preceding True Ranges, NOT recursive Wilder RMA.

    Formula:
      For candle at index i (open timestamp t_i):
      TR_j = max(high_j - low_j, abs(high_j - close_{j-1}), abs(low_j - close_{j-1}))
      for completed bars strictly prior to candle i (indices 1 .. i-1).
      If at least `period` completed TRs exist prior to candle i (i >= period + 1):
        atr_val = sum(TR_{i-1-period : i-1}) / period
      Mapping: t_i -> atr_val
    """
    if not closed_m1 or period <= 0:
        return {}

    candles = []
    for c in closed_m1:
        c_open = candle_open_ms(c)
        c_close = c_open + 60_000
        if as_of_ms is not None and c_close > as_of_ms:
            continue
        candles.append(c)

    n = len(candles)
    if n < period + 1:
        return {}

    tr_list: List[float] = []
    for j in range(1, n):
        curr = candles[j]
        prev = candles[j - 1]
        c_high = float(curr.get("high") if isinstance(curr, dict) else getattr(curr, "high", 0.0))
        c_low = float(curr.get("low") if isinstance(curr, dict) else getattr(curr, "low", 0.0))
        prev_close = float(prev.get("close") if isinstance(prev, dict) else getattr(prev, "close", 0.0))

        if not (math.isfinite(c_high) and math.isfinite(c_low) and math.isfinite(prev_close)):
            tr_list.append(float("nan"))
            continue
        if c_high < c_low:
            tr_list.append(float("nan"))
            continue

        tr = max(
            c_high - c_low,
            abs(c_high - prev_close),
            abs(c_low - prev_close),
        )
        tr_list.append(tr)

    atr_series: Dict[int, float] = {}
    for i in range(period + 1, n + 1):
        recent_trs = tr_list[i - 1 - period : i - 1]
        if any(not math.isfinite(val) for val in recent_trs):
            continue
        raw_atr = sum(recent_trs) / float(period)
        if math.isfinite(raw_atr) and raw_atr >= 0.0:
            if i < n:
                bar_ts = candle_open_ms(candles[i])
                atr_series[bar_ts] = raw_atr

    return atr_series


def resolve_cg01_mss_and_displacement(
    m1_candles: Sequence[Any],
    sweep_info: Dict[str, Any],
    direction: str,
    now_ms: int,
    config: Optional[CG01Config] = None,
    atr_1m: Optional[Union[float, Dict[int, float]]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Rules 086 - 096: MSS & Displacement Detector.
    Finds structural swing break confirmed with closed candle and displacement >= 0.8 ATR.
    Strictly forbids fake micro-breaks (Rules 088, 094, 095).
    V16.28 Section A: Uses actual causal per-break M1 ATR series or caller-provided ATR.
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

    # Resolve ATR authority: caller scalar, precomputed series, or build causal series
    atr_lookup: Union[float, Dict[int, float]]
    if atr_1m is not None:
        if isinstance(atr_1m, dict):
            atr_lookup = atr_1m
        else:
            atr_lookup = float(atr_1m)
    else:
        atr_period = config.displacement_atr_period if config else 14
        atr_lookup = build_cg01_displacement_atr_series(m1_candles, period=atr_period, as_of_ms=now_ms)

    # 2. Look for closed break candle after the reference bar
    min_disp_ratio = config.displacement_body_atr_min if config else 0.80

    for i in range(reclaim_bar_idx, len(m1_candles)):
        bar = m1_candles[i]
        b_open = float(bar.get("open") if isinstance(bar, dict) else getattr(bar, "open", 0.0))
        b_close = float(bar.get("close") if isinstance(bar, dict) else getattr(bar, "close", 0.0))
        b_time_ms = candle_open_ms(bar)

        if isinstance(atr_lookup, dict):
            if b_time_ms not in atr_lookup:
                # No warmed-up ATR available for this historical break candle
                continue
            effective_atr = atr_lookup[b_time_ms]
        else:
            effective_atr = atr_lookup

        if not math.isfinite(effective_atr) or effective_atr < 0:
            continue

        safe_atr = max(effective_atr, 0.20)
        req_disp = min_disp_ratio * safe_atr

        if direction == "LONG":
            # Rule 089: Close MUST exceed swing high reference
            if b_close > (ref_price + min_excess):
                body = b_close - b_open
                # Rule 091 - 093: Displacement direction and magnitude
                if body > 0 and body >= req_disp:
                    return {
                        "mss_id": f"mss_{structure_ref['ref_id']}_{b_time_ms}",
                        "structure_ref_id": structure_ref["ref_id"],
                        "structure_ref_price": ref_price,
                        "break_bar_idx": i,
                        "break_candle_time_ms": b_time_ms,
                        "displacement_body": body,
                        "displacement_atr": safe_atr,
                        "displacement_ratio": round(body / safe_atr, 4) if safe_atr > 0 else 0.0,
                        "break_close": b_close,
                    }
        else:
            # Rule 089: Close MUST break below swing low reference
            if b_close < (ref_price - min_excess):
                body = b_open - b_close
                if body > 0 and body >= req_disp:
                    return {
                        "mss_id": f"mss_{structure_ref['ref_id']}_{b_time_ms}",
                        "structure_ref_id": structure_ref["ref_id"],
                        "structure_ref_price": ref_price,
                        "break_bar_idx": i,
                        "break_candle_time_ms": b_time_ms,
                        "displacement_body": body,
                        "displacement_atr": safe_atr,
                        "displacement_ratio": round(body / safe_atr, 4) if safe_atr > 0 else 0.0,
                        "break_close": b_close,
                    }

    return None


def assess_cg01_fvg_lifecycle(
    fvg: Dict[str, Any],
    closed_m1: Sequence[Any],
    direction: str,
    as_of_ms: int,
    config: Optional[CG01Config] = None,
    entry_mode: Optional[str] = None,
    structural_sl: Optional[float] = None,
) -> Dict[str, Any]:
    """
    V16.28 Section C: Evaluates FVG lifecycle, penetration, CE touch, freshness, and invalidation.
    Returns structured facts with zero lookahead.
    """
    lower = float(fvg["lower"])
    upper = float(fvg["upper"])
    ce_price = float(fvg.get("ce_price", 0.0))
    form_time_ms = int(fvg.get("formation_time_ms") or 0)
    known_at_ms = int(fvg.get("known_at_ms") or (form_time_ms + 60_000))
    k_idx = int(fvg.get("formation_bar_idx", 0))

    height = upper - lower
    tick_size = config.tick_size if config else 0.01
    max_mitigation_ratio = config.max_fvg_mitigation_ratio if config else 0.50
    mode = entry_mode or (config.entry_mode if config else CG01EntryMode.LIMIT_AT_CE_AFTER_CONFIRMATION.value)

    if height <= 0:
        return {
            "known_at_ms": known_at_ms,
            "first_touch_ms": None,
            "first_ce_touch_ms": None,
            "max_mitigation_ratio": 1.0,
            "is_invalidated": True,
            "invalidation_reason": "INVALID_FVG_HEIGHT",
            "invalidation_time_ms": known_at_ms,
            "is_fresh": False,
            "reason": "INVALID_GEOMETRY",
        }

    first_touch_ms: Optional[int] = None
    first_ce_touch_ms: Optional[int] = None
    observed_max_mitigation: float = 0.0
    is_invalidated = False
    invalidation_reason: Optional[str] = None
    invalidation_time_ms: Optional[int] = None

    for j in range(k_idx + 1, len(closed_m1)):
        b = closed_m1[j]
        b_open = candle_open_ms(b)
        b_close_time = b_open + 60_000

        if b_close_time > as_of_ms:
            break
        if b_open < known_at_ms:
            continue

        b_low = float(b.get("low") if isinstance(b, dict) else getattr(b, "low", 0.0))
        b_high = float(b.get("high") if isinstance(b, dict) else getattr(b, "high", 0.0))

        if direction == "LONG":
            if b_low < upper:
                if first_touch_ms is None:
                    first_touch_ms = b_open
                depth = upper - b_low
                ratio = depth / height
                if ratio > observed_max_mitigation:
                    observed_max_mitigation = ratio

            if b_low <= (ce_price + tick_size * 0.5):
                if first_ce_touch_ms is None:
                    first_ce_touch_ms = b_open

            if b_low <= lower:
                is_invalidated = True
                invalidation_reason = "FVG_BOUNDARY_VIOLATED"
                invalidation_time_ms = b_open
                break
            if structural_sl is not None and b_low <= structural_sl:
                is_invalidated = True
                invalidation_reason = "STRUCTURAL_SL_BREACHED"
                invalidation_time_ms = b_open
                break

        else:
            if b_high > lower:
                if first_touch_ms is None:
                    first_touch_ms = b_open
                depth = b_high - lower
                ratio = depth / height
                if ratio > observed_max_mitigation:
                    observed_max_mitigation = ratio

            if b_high >= (ce_price - tick_size * 0.5):
                if first_ce_touch_ms is None:
                    first_ce_touch_ms = b_open

            if b_high >= upper:
                is_invalidated = True
                invalidation_reason = "FVG_BOUNDARY_VIOLATED"
                invalidation_time_ms = b_open
                break
            if structural_sl is not None and b_high >= structural_sl:
                is_invalidated = True
                invalidation_reason = "STRUCTURAL_SL_BREACHED"
                invalidation_time_ms = b_open
                break

    is_fresh = True
    reason = "FRESH"

    if is_invalidated:
        is_fresh = False
        reason = invalidation_reason or "INVALIDATED"
    elif mode in (CG01EntryMode.LIMIT_AT_CE_AFTER_CONFIRMATION.value, "LIMIT_AT_CE_AFTER_CONFIRMATION", "LIMIT"):
        if first_ce_touch_ms is not None:
            is_fresh = False
            reason = "FVG_USED_BEFORE_ARM"
        elif observed_max_mitigation > max_mitigation_ratio:
            is_fresh = False
            reason = "FVG_OVER_MITIGATED"
        else:
            is_fresh = True
            reason = "FRESH"
    else:
        if observed_max_mitigation > 1.0:
            is_fresh = False
            reason = "FVG_OVER_MITIGATED"
        else:
            is_fresh = True
            reason = "ACTIVE_FOR_RETEST"

    return {
        "known_at_ms": known_at_ms,
        "first_touch_ms": first_touch_ms,
        "first_ce_touch_ms": first_ce_touch_ms,
        "max_mitigation_ratio": round(observed_max_mitigation, 4),
        "is_invalidated": is_invalidated,
        "invalidation_reason": invalidation_reason,
        "invalidation_time_ms": invalidation_time_ms,
        "is_fresh": is_fresh,
        "reason": reason,
    }


def select_cg01_progress_diagnostic(
    diagnostics: Sequence[CG01CandidateDiagnostic],
) -> Optional[CG01CandidateDiagnostic]:
    """
    V16.28 Item 075-076: Selects the diagnostic representing the furthest observed milestone.
    Uses progress_rank descending, preserving stable POI tie-break.
    """
    if not diagnostics:
        return None
    return max(diagnostics, key=lambda d: d.progress_rank)


def select_cg01_leg_fvg_with_diagnostics(
    m1_candles: Sequence[Any],
    mss_info: Dict[str, Any],
    direction: str,
    now_ms: int,
    config: Optional[CG01Config] = None,
    entry_mode: Optional[str] = None,
    structural_sl: Optional[float] = None,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    V16.28 Item 082-084: FVG selection with rejection diagnostics.
    Returns (selected_fvg, list_of_rejected_candidate_facts).
    """
    break_idx = mss_info["break_bar_idx"]
    if break_idx < 2 or break_idx >= len(m1_candles):
        return None, [{"reason": "INVALID_BREAK_INDEX"}]

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
        return None, [{"reason": "NO_FVG_ON_LEG"}]

    rejection_facts: List[Dict[str, Any]] = []
    for cand in candidates:
        k_idx, lower, upper, ce, form_time_ms = cand
        fvg_dict = {
            "fvg_id": f"fvg_leg_{k_idx}_{form_time_ms}",
            "lower": lower,
            "upper": upper,
            "ce_price": ce,
            "formation_time_ms": form_time_ms,
            "known_at_ms": form_time_ms + 60_000,
            "formation_bar_idx": k_idx,
        }
        lifecycle = assess_cg01_fvg_lifecycle(
            fvg=fvg_dict,
            closed_m1=m1_candles,
            direction=direction,
            as_of_ms=now_ms,
            config=config,
            entry_mode=entry_mode,
            structural_sl=structural_sl,
        )
        if lifecycle["is_fresh"]:
            fvg_dict["lifecycle"] = lifecycle
            fvg_dict["mitigation_ratio"] = lifecycle["max_mitigation_ratio"]
            fvg_dict["is_fresh"] = True
            return fvg_dict, rejection_facts
        else:
            rejection_facts.append({
                "fvg_id": fvg_dict["fvg_id"],
                "reason": lifecycle.get("reason", "FVG_NOT_FRESH"),
                "lifecycle": lifecycle,
            })

    return None, rejection_facts


def select_cg01_leg_fvg(
    m1_candles: Sequence[Any],
    mss_info: Dict[str, Any],
    direction: str,
    now_ms: int,
    config: Optional[CG01Config] = None,
    entry_mode: Optional[str] = None,
    structural_sl: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Rules 097 - 105: FVG belonging to the MSS Displacement Leg.
    Computes 50% CE level and validates freshness across primary and secondary candidates.
    V16.28 Section C: Preserves compatibility via select_cg01_leg_fvg_with_diagnostics.
    """
    fvg, _ = select_cg01_leg_fvg_with_diagnostics(
        m1_candles=m1_candles,
        mss_info=mss_info,
        direction=direction,
        now_ms=now_ms,
        config=config,
        entry_mode=entry_mode,
        structural_sl=structural_sl,
    )
    return fvg


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

    q_adapted = adapt_cg01_quote(current_quote)
    bid = q_adapted.bid
    ask = q_adapted.ask
    current_price = q_adapted.mid

    # 1. Step 1: Timing Window Check (Rules 051 - 060)
    time_ctx = resolve_cg01_time_context(t_now, cfg)

    # RULE 021: Closed boundary at evaluator — build closed bundle at start (Rules 021 - 032)
    closed_bundle = build_cg01_closed_context(context, as_of_ms=t_now, symbol="XAUUSDT")
    if isinstance(context, dict) and "context_source" in context:
        closed_bundle["context_source"] = context["context_source"]
    ready, ready_reason, missing_conds = assess_cg01_context_readiness(closed_bundle, cfg)
    if not ready:
        return CG01Decision(
            stage=CG01Stage.OBSERVING,
            is_eligible=False,
            model_version=cfg.model_version,
            config_version=cfg.config_version,
            reason_codes=(ready_reason,),
            missing_conditions=tuple(missing_conds),
            as_of_ms=t_now,
            time_context=time_ctx,
            ui_summary_vi={"status": f"Đang khởi động dữ liệu bối cảnh HTF: {ready_reason}."},
            direction_policy=getattr(cfg, "direction_policy", "LEGACY_H1"),
        )

    # 2. Step 2: HTF Context & Dealing Range (Rules 046 - 050)
    htf_data = build_cg01_context(closed_bundle, t_now, cfg)
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
            direction_policy=getattr(cfg, "direction_policy", "LEGACY_H1"),
        )

    # Context Bias determines primary hypothesis direction (V16.11, V16.30 Daily Bias)
    h1_bias = htf_data.get("h1_bias", "UNKNOWN")
    range_pos = htf_data.get("range_position", "EQUILIBRIUM")
    dir_ctx = htf_data.get("direction_context") or {}
    daily_bias = htf_data.get("daily_bias")
    direction_policy = htf_data.get("direction_policy", getattr(cfg, "direction_policy", "LEGACY_H1"))

    directions_to_evaluate = list(dir_ctx.get("directions_to_evaluate", []))
    if not directions_to_evaluate:
        # Rules 055, 056, 123, 126, 127: In DAILY_BIAS_PRIMARY, NO FALLBACK TO H1!
        if direction_policy in ("DAILY_BIAS_PRIMARY", CG01DirectionPolicy.DAILY_BIAS_PRIMARY.value):
            b_status = getattr(daily_bias, "status", "UNAVAILABLE") if daily_bias else "UNAVAILABLE"
            if b_status == "INVALIDATED":
                rcode = "DAILY_BIAS_INVALIDATED"
                mcond = "DAILY_BIAS_INVALIDATED"
            elif b_status == "UNAVAILABLE":
                rcode = "DAILY_BIAS_UNAVAILABLE"
                mcond = "DAILY_BIAS_UNAVAILABLE"
            else:
                rcode = "DAILY_BIAS_NEUTRAL"
                mcond = "DAILY_BIAS_NEUTRAL"

            summ_vi = {"status": getattr(daily_bias, "summary_vi", f"Chưa có hướng giao dịch theo Daily Bias ({b_status}).")} if daily_bias else {"status": "Chưa có dữ liệu Daily Bias."}

            return CG01Decision(
                stage=CG01Stage.OBSERVING,
                is_eligible=False,
                model_version=cfg.model_version,
                config_version=cfg.config_version,
                reason_codes=(rcode,),
                missing_conditions=(mcond,),
                as_of_ms=t_now,
                time_context=time_ctx,
                ui_summary_vi=summ_vi,
                daily_bias=daily_bias,
                direction_policy=direction_policy,
            )

        # If HTF structure is UNKNOWN or insufficient, do not blindly open Long and Short (Phần 108)
        if h1_bias in ("UNKNOWN", "NEUTRAL") or dir_ctx.get("readiness") == "NOT_READY":
            return CG01Decision(
                stage=CG01Stage.OBSERVING,
                is_eligible=False,
                model_version=cfg.model_version,
                config_version=cfg.config_version,
                reason_codes=dir_ctx.get("reason_codes", ("HTF_DATA_INSUFFICIENT",)),
                missing_conditions=("HTF_STRUCTURE_UNKNOWN",),
                as_of_ms=t_now,
                time_context=time_ctx,
                ui_summary_vi={"status": "Chưa đủ bằng chứng cấu trúc HTF để xác định hướng giao dịch."},
                daily_bias=daily_bias,
                direction_policy=direction_policy,
            )
        elif h1_bias == "BULLISH":
            directions_to_evaluate = ["LONG"]
        elif h1_bias == "BEARISH":
            directions_to_evaluate = ["SHORT"]
        else:
            directions_to_evaluate = ["LONG", "SHORT"]

    c_15m = closed_bundle["15M"]
    c_1m = closed_bundle["1M"]

    # Rule 039: Strict M1 timeframe verification
    if not c_1m:
        return CG01Decision(
            stage=CG01Stage.OBSERVING,
            is_eligible=False,
            reason_codes=("DATA_INCOMPLETE",),
            missing_conditions=("MISSING_1M_CANDLES",),
            as_of_ms=t_now,
            time_context=time_ctx,
            daily_bias=daily_bias,
            direction_policy=direction_policy,
        )

    # V16.28 Section A: Verify M1 ATR Warmup
    if len(c_1m) < (cfg.displacement_atr_period + 1):
        return CG01Decision(
            stage=CG01Stage.OBSERVING,
            is_eligible=False,
            model_version=cfg.model_version,
            config_version=cfg.config_version,
            reason_codes=("ATR_WARMUP",),
            missing_conditions=(f"INSUFFICIENT_1M_BARS_FOR_ATR (have {len(c_1m)}, need {cfg.displacement_atr_period + 1})",),
            as_of_ms=t_now,
            time_context=time_ctx,
            ui_summary_vi={"status": "Đang tích luỹ nến M1 để tính độ biến động ATR."},
            daily_bias=daily_bias,
            direction_policy=direction_policy,
        )

    # Precompute causal per-bar ATR series across closed M1 candles
    atr_series = build_cg01_displacement_atr_series(
        c_1m,
        period=cfg.displacement_atr_period,
        as_of_ms=t_now,
    )

    # 3. Extract HTF POIs from 15M (Rules 071 - 080)
    zones_15m = ZoneWatchlistService.extract_zones_from_candles(
        c_15m, timeframe="15M", symbol="XAUUSDT", as_of_ms=t_now
    )

    # Liquidity map for sweep & targets (Rules 041, 042, 061 - 070)
    liquidity_levels = build_cg01_liquidity_context(
        closed_bundle, as_of_ms=t_now, symbol="XAUUSDT", config=cfg
    )

    executable_decisions: List[CG01Decision] = []
    waiting_decisions: List[CG01Decision] = []
    rejection_diagnostics: List[Tuple[CG01Stage, str]] = []
    progress_diagnostics: List[CG01CandidateDiagnostic] = []

    for direction in directions_to_evaluate:
        # Rule 050: Filter POIs
        eligible_pois = filter_cg01_pois(zones_15m, direction, current_price, t_now, cfg)
        if not eligible_pois:
            continue

        for poi in eligible_pois:
            # 4. Sweep Detection
            sweep = detect_cg01_sweep(c_1m, liquidity_levels, poi, direction, t_now, cfg)
            if not sweep:
                progress_diagnostics.append(
                    CG01CandidateDiagnostic(
                        poi_id=poi.zone_id,
                        direction=direction,
                        stage=CG01Stage.WAIT_SWEEP,
                        reason="WAITING_SWEEP_CONFIRMATION",
                        evaluated_at_ms=t_now,
                        progress_rank=2,
                        missing_condition="SWEEP_NOT_CONFIRMED",
                        ui_summary_vi=f"POI {poi.zone_id}: Đang theo dõi vùng cấu trúc giá và chờ tín hiệu sweep hợp lệ.",
                    )
                )
                continue

            # 5. MSS & Displacement with dynamic M1 ATR series
            mss = resolve_cg01_mss_and_displacement(c_1m, sweep, direction, t_now, cfg, atr_1m=atr_series)
            if not mss:
                ref_price = float(sweep.get("reference_price", 0.0))
                progress_diagnostics.append(
                    CG01CandidateDiagnostic(
                        poi_id=poi.zone_id,
                        direction=direction,
                        stage=CG01Stage.WAIT_MSS,
                        reason="WAITING_MSS_CONFIRMATION",
                        evaluated_at_ms=t_now,
                        progress_rank=3,
                        missing_condition="MSS_DISPLACEMENT_NOT_CONFIRMED",
                        ui_summary_vi=f"POI {poi.zone_id}: Đã có quét thanh khoản ({ref_price:.2f}); đang chờ nến đóng xác nhận phá cấu trúc và lực đẩy.",
                    )
                )
                continue

            sweep_extreme = sweep["sweep_extreme"]
            buffer_ticks = cfg.sl_buffer_ticks
            tick_size = cfg.tick_size

            if direction == "LONG":
                sl = round(sweep_extreme - (buffer_ticks * tick_size), 2)
            else:
                sl = round(sweep_extreme + (buffer_ticks * tick_size), 2)

            # 6. Leg FVG & 50% CE with lifecycle validation and rejection diagnostics
            fvg, fvg_rejections = select_cg01_leg_fvg_with_diagnostics(
                c_1m,
                mss,
                direction,
                t_now,
                cfg,
                entry_mode=cfg.entry_mode,
                structural_sl=sl,
            )
            if not fvg:
                fvg_rej_reason = fvg_rejections[0]["reason"] if fvg_rejections else "WAITING_FVG_FORMATION"
                fvg_ui_desc = (
                    f"POI {poi.zone_id}: Đã có xác nhận cấu trúc ({mss.get('structure_ref_price', 0.0):.2f}); {fvg_rej_reason}."
                    if fvg_rejections
                    else f"POI {poi.zone_id}: Đã có xác nhận cấu trúc ({mss.get('structure_ref_price', 0.0):.2f}); chưa tìm thấy FVG hợp lệ trên nhịp đẩy."
                )
                progress_diagnostics.append(
                    CG01CandidateDiagnostic(
                        poi_id=poi.zone_id,
                        direction=direction,
                        stage=CG01Stage.WAIT_FVG,
                        reason=fvg_rej_reason,
                        evaluated_at_ms=t_now,
                        progress_rank=4,
                        missing_condition="FVG_NOT_AVAILABLE",
                        ui_summary_vi=fvg_ui_desc,
                    )
                )
                continue

            ce_price = fvg["ce_price"]

            # Rule 078, 081, 082: Entry mode validation & resolution
            is_retest_market = (cfg.entry_mode == CG01EntryMode.CONFIRMED_RETEST_MARKET.value)
            if is_retest_market:
                retest_res = resolve_cg01_retest(
                    closed_m1=c_1m,
                    fvg=fvg,
                    direction=direction,
                    as_of_ms=t_now,
                    config=cfg,
                    structural_sl=sl,
                )
                if not retest_res.confirmed:
                    wait_dec = CG01Decision(
                        stage=CG01Stage.WAIT_ENTRY,
                        is_eligible=False,
                        model_id=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
                        model_version=cfg.model_version,
                        config_version=cfg.config_version,
                        direction=direction,
                        poi_id=poi.zone_id,
                        reason_codes=(retest_res.reason,),
                        missing_conditions=("FVG_RETEST_NOT_CONFIRMED",),
                        as_of_ms=t_now,
                        time_context=time_ctx,
                        ui_summary_vi={"status": f"POI {poi.zone_id}: Đang chờ giá hồi (retest) vào vùng FVG."},
                    )
                    waiting_decisions.append(wait_dec)
                    progress_diagnostics.append(
                        CG01CandidateDiagnostic(
                            poi_id=poi.zone_id,
                            direction=direction,
                            stage=CG01Stage.WAIT_ENTRY,
                            reason=retest_res.reason,
                            evaluated_at_ms=t_now,
                            progress_rank=5,
                            missing_condition="FVG_RETEST_NOT_CONFIRMED",
                            ui_summary_vi=f"POI {poi.zone_id}: Đang chờ giá hồi (retest) vào vùng FVG.",
                        )
                    )
                    continue

                # Rules 008, 045, 046, 052: Market entry uses executable quote estimate + slippage
                costs_obj = costs or CostAssumptions()
                slippage = resolve_slippage_usd(getattr(costs_obj, "slippage_usd", None), default=0.10)
                if direction == "LONG":
                    planned_entry = round(ask + slippage, 2)
                    price_basis = "MARKET_QUOTE_ASK"
                else:
                    planned_entry = round(bid - slippage, 2)
                    price_basis = "MARKET_QUOTE_BID"
                order_type = "MARKET"
                entry_has_slippage = True
                trigger_known_at = retest_res.known_at or t_now
            else:
                # Rule 044: Limit at CE
                planned_entry = ce_price
                price_basis = "LIMIT_CE"
                order_type = "LIMIT"
                entry_has_slippage = False
                trigger_known_at = int(fvg.get("known_at_ms") or fvg.get("formation_time_ms") or t_now)

            target_dec = resolve_executable_target(
                liquidity_levels, direction, planned_entry, as_of_ms=t_now, model_id=ModelIdentifier.CG01_SWEEP_MSS_FVG.value
            )
            tp = target_dec.price if (target_dec and target_dec.status == "CONFIRMED") else None

            if not tp:
                rejection_diagnostics.append((CG01Stage.WAIT_ENTRY, "TARGET_UNAVAILABLE"))
                continue

            # Rule 118: Validate price geometry (LONG: SL < entry < TP; SHORT: TP < entry < SL)
            geom_ok, geom_err = validate_price_geometry(direction, planned_entry, sl, tp)
            if not geom_ok:
                rejection_diagnostics.append((CG01Stage.WAIT_ENTRY, f"GEOMETRY_INVALID: {geom_err}"))
                continue

            # Sizing via calculate_risk_reward (Rules 116, 117, 049)
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
                entry_has_slippage=entry_has_slippage,
                now_ms=t_now,
            )

            if not calc.is_valid or not calc.can_execute or calc.net_rr < cfg.min_net_rr:
                reason = "RR_BELOW_MINIMUM" if calc.net_rr < cfg.min_net_rr else (calc.invalid_reason or "CALCULATION_INVALID")
                rejection_diagnostics.append((CG01Stage.WAIT_ENTRY, reason))
                continue

            # Construct CG01EntryIntent (Rule 024, 025, 028, 041)
            exec_deadline = resolve_cg01_execution_deadline(
                trigger_known_at_ms=int(trigger_known_at),
                candidate_ttl_ms=int(cfg.candidate_ttl_ms),
                max_trigger_age_ms=int(getattr(cfg, "max_trigger_age_ms", cfg.candidate_ttl_ms * 2)),
                entry_window_end_ms=getattr(time_ctx, "window_end_ms", None),
            )
            valid_until = exec_deadline.expires_at_ms

            # Rule 028: Evaluator returns EXPIRED if t_now >= valid_until
            if t_now >= valid_until:
                rejection_diagnostics.append((CG01Stage.EXPIRED, "TRIGGER_EXPIRED"))
                continue

            intent = CG01EntryIntent(
                entry_mode=cfg.entry_mode,
                order_type=order_type,
                reference_ce=ce_price,
                planned_entry=planned_entry,
                price_basis=price_basis,
                trigger_known_at=trigger_known_at,
                valid_until=valid_until,
                quote_timestamp=q_adapted.timestamp,
            )

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
                entry_trigger_id=f"trig_{order_type.lower()}_{fvg['fvg_id']}",
                invalidation_id=f"sl_sweep_{sweep['sweep_id']}",
                target_id=target_dec.source_level_id or f"tp_{tp}",
            )

            # Rule 127, 128: Stable Candidate Key & Episode Identity
            episode_id = sweep["episode_id"]
            cand_key = f"cg01_{direction}_{poi.zone_id}_{sweep['reference_id']}_{fvg['fvg_id']}"

            # Build full EvidenceNodes, TriggerChain, and immutable ScenarioSnapshot (Rules 096, 121 - 127, PHẦN 030)
            ev_nodes = build_cg01_evidence_nodes(
                symbol="XAUUSDT",
                direction=direction,
                htf_data=htf_data,
                poi=poi,
                sweep=sweep,
                mss=mss,
                fvg=fvg,
                planned_entry=planned_entry,
                sl=sl,
                tp=tp,
                target_dec=target_dec,
                now_ms=t_now,
                entry_mode=cfg.entry_mode,
                retest_res=retest_res if is_retest_market else None,
            )
            trig_chain = build_cg01_trigger_chain(ev_nodes, t_now)
            snap = build_cg01_snapshot(
                symbol="XAUUSDT",
                direction=direction,
                candidate_key=cand_key,
                planned_entry=planned_entry,
                sl=sl,
                tp=tp,
                calc=calc,
                poi=poi,
                sweep=sweep,
                mss=mss,
                fvg=fvg,
                target_dec=target_dec,
                ev_nodes=ev_nodes,
                trigger_chain=trig_chain,
                config=cfg,
                now_ms=t_now,
                trigger_known_at=trigger_known_at,
                execution_valid_until=valid_until,
                deadline_constraints=exec_deadline.constraints,
                htf_data=htf_data,
            )

            # Rule 153 & V16.11: Six short sentences for newbie drawer
            h4_bias_label = htf_data.get("h4_bias", "UNKNOWN")
            rel_label = htf_data.get("relation", "ALIGNED")
            ui_summary = {
                "context": f"Bối cảnh: H4 {h4_bias_label}, H1 {h1_bias} ({rel_label}), Dealing Range {range_pos}.",
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
                evidence=ev_nodes,
                trigger_chain=trig_chain,
                snapshot=snap,
                evidence_ids=ev_refs.to_tuple(),
                valid_until_ms=valid_until,
                as_of_ms=t_now,
                model_version=cfg.model_version,
                order_type=order_type,
                entry_intent=intent.to_dict(),
                reference_ce=ce_price,
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
                daily_bias=daily_bias,
                direction_policy=direction_policy,
            )
            executable_decisions.append(decision)

    # 7. Candidate Selection Policy (Items 128-130, 134)
    if executable_decisions:
        # Precedence to first executable according to stable natural POI ranking
        return executable_decisions[0]

    if waiting_decisions:
        # Return deterministic representative waiting state
        return waiting_decisions[0]

    if rejection_diagnostics:
        # Select most specific actionable diagnostic
        priority_order = ["TRIGGER_EXPIRED", "RR_BELOW_MINIMUM", "TARGET_UNAVAILABLE", "GEOMETRY_INVALID"]
        chosen_diag: Optional[Tuple[CG01Stage, str]] = None
        for prio in priority_order:
            for diag in rejection_diagnostics:
                if prio in diag[1]:
                    chosen_diag = diag
                    break
            if chosen_diag:
                break
        if not chosen_diag:
            chosen_diag = rejection_diagnostics[-1]

        stage_rej, reason_rej = chosen_diag
        missing_cond = (
            ("TRIGGER_EXPIRED",)
            if "TRIGGER_EXPIRED" in reason_rej
            else (("NET_RR_BELOW_MINIMUM",) if "RR_BELOW_MINIMUM" in reason_rej else ("CALCULATION_REJECTED",))
        )
        return CG01Decision(
            stage=stage_rej,
            is_eligible=False,
            model_id=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
            model_version=cfg.model_version,
            config_version=cfg.config_version,
            reason_codes=(reason_rej,),
            missing_conditions=missing_cond,
            as_of_ms=t_now,
            time_context=time_ctx,
            ui_summary_vi={"status": f"Kịch bản không khả thi: {reason_rej}."},
            daily_bias=daily_bias,
            direction_policy=direction_policy,
        )

    # 8. Highest Progress Diagnostic Policy (Items 041, 063, 066, 075, 076)
    if progress_diagnostics:
        best_diag = select_cg01_progress_diagnostic(progress_diagnostics)
        if best_diag and best_diag.progress_rank >= 3:
            # Progress reached beyond initial sweep waiting (e.g. WAIT_MSS or WAIT_FVG)
            return CG01Decision(
                stage=best_diag.stage,
                is_eligible=False,
                model_id=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
                model_version=cfg.model_version,
                config_version=cfg.config_version,
                direction=best_diag.direction,
                poi_id=best_diag.poi_id,
                reason_codes=(best_diag.reason,),
                missing_conditions=(best_diag.missing_condition or "PROGRESS_INCOMPLETE",),
                as_of_ms=t_now,
                time_context=time_ctx,
                ui_summary_vi={"status": best_diag.ui_summary_vi or f"Tiến trình đang ở bước {best_diag.stage.value}."},
                daily_bias=daily_bias,
                direction_policy=direction_policy,
            )

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
        daily_bias=daily_bias,
        direction_policy=direction_policy,
    )
