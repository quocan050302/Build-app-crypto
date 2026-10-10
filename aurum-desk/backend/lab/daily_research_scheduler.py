"""
backend/lab/daily_research_scheduler.py
V13.3 Causal Daily NY Session Paper Research Scheduler

Orchestrates daily NY session research entry targets:
- Evaluates session eligibility (market open, historical data completeness, warmup).
- Monitors confirmed setups (B1 continuation, B2 range break-retest).
- At scheduled deadline (e.g. 14:30 NY), if 0 fills have occurred today and session is eligible,
  schedules a paper entry (SMC_CONTEXT_SCHEDULED_PAPER) using HTF alignment and 15M structure.
- Explicitly tags missing confirmations and heuristic confidence kind.
- Enforces hard guards (max 3 fills/session, 2 consecutive SL stop, daily loss budget -1.5%, cooldown).
- Reconciles session outcomes and produces transparent cadence summaries.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from zoneinfo import ZoneInfo

from domain_calculator import calculate_risk_reward, validate_price_geometry, CostAssumptions
from lab.ny_strategy_variants import (
    is_ny_session_window,
    is_ny_deadline_reached,
    get_ny_datetime,
    compute_atr_bars,
    BarProxy,
    ensure_proxies,
    NY_TZ
)
import smc_engine

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


@dataclass
class SessionEligibility:
    session_id: str
    ny_date: str
    market_open: bool
    data_complete: bool
    warmup_complete: bool
    execution_data_valid: bool
    eligible: bool
    reasons: List[str] = field(default_factory=list)
    assessed_at_ms: int = 0


@dataclass
class SessionResearchState:
    session_id: str
    date_ny: str
    trade_day_vn: str
    status: str = "PREPARING"  # PREPARING, SEEKING_CONFIRMED, SCHEDULED_DUE, SCHEDULED_PENDING_FILL, TARGET_FILLED, RISK_BLOCKED, DATA_BLOCKED, UNFULFILLED, SESSION_COMPLETE
    fills: int = 0
    confirmed_fill_count: int = 0
    scheduled_fill_count: int = 0
    attempts_count: int = 0
    scheduled_attempt_id: Optional[str] = None
    last_decision_ms: Optional[int] = None
    block_reason: Optional[str] = None
    open_trade_id: Optional[str] = None
    missing_confirmations: List[str] = field(default_factory=list)
    unmet_reason: Optional[str] = None
    is_eligible: bool = True
    cooldown_until_ms: int = 0


@dataclass
class ScheduledDecision:
    direction: Optional[str]
    decision_ms: int
    entry_model: str
    structural_references: Dict[str, Any]
    missing_confirmations: List[str]
    confidence_kind: str = "HEURISTIC"
    reason: str = ""
    config_version: str = "v13.3"


def make_session_id(symbol: str, ny_date: str, policy_version: str = "v13.3") -> str:
    return f"NY-{ny_date}"


def resolve_research_range(
    request: Any,
    now_ms: Optional[int] = None
) -> Tuple[int, int, Dict[str, Any]]:
    """
    PHẦN 13: Helper resolve range mới.
    Canonical start_ts and end_ts (end-exclusive).
    Display metadata with dates in VN_TZ and NY_TZ.
    """
    now_ts = now_ms if now_ms is not None else int(datetime.now(tz=VN_TZ).timestamp() * 1000)

    start_date_str = getattr(request, "start_date", None)
    end_date_str = getattr(request, "end_date", None)
    req_start_ts = getattr(request, "start_ts", None)
    req_end_ts = getattr(request, "end_ts", None)

    # Resolve start
    if req_start_ts is not None:
        start_ts = int(req_start_ts)
    elif start_date_str:
        dt_start = datetime.strptime(start_date_str, "%Y-%m-%d").replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=VN_TZ)
        start_ts = int(dt_start.timestamp() * 1000)
    else:
        # Default to 90 days before now
        dt_start = datetime.now(tz=VN_TZ).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=90)
        start_ts = int(dt_start.timestamp() * 1000)

    # Resolve end (end-exclusive: end_date inclusive -> start of next day in VN_TZ)
    if req_end_ts is not None:
        end_ts = int(req_end_ts)
    elif end_date_str:
        dt_end = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=VN_TZ) + timedelta(days=1)
        end_ts = int(dt_end.timestamp() * 1000)
    else:
        end_ts = now_ts

    # Clamp if future relative to now_ts
    is_partial_day = False
    if end_ts > now_ts:
        end_ts = now_ts
        is_partial_day = True

    if start_ts >= end_ts:
        raise ValueError(f"Khoảng thời gian nghiên cứu không hợp lệ: start_ts ({start_ts}) >= end_ts ({end_ts})")

    dt_s = datetime.fromtimestamp(start_ts / 1000.0, tz=VN_TZ)
    dt_e = datetime.fromtimestamp(end_ts / 1000.0, tz=VN_TZ)
    metadata = {
        "canonical_start_ts": start_ts,
        "canonical_end_ts": end_ts,
        "start_date_vn": dt_s.strftime("%Y-%m-%d"),
        "end_date_vn": (dt_e - timedelta(milliseconds=1)).strftime("%Y-%m-%d"),
        "start_date_ny": dt_s.astimezone(NY_TZ).strftime("%Y-%m-%d"),
        "end_date_ny": (dt_e - timedelta(milliseconds=1)).astimezone(NY_TZ).strftime("%Y-%m-%d"),
        "is_partial_day": is_partial_day,
        "eval_days_count": round((end_ts - start_ts) / (86400 * 1000), 2)
    }
    return start_ts, end_ts, metadata


def evaluate_session_eligibility(
    session_id: str,
    ny_date: str,
    has_data: bool = True,
    warmup_complete: bool = True,
    is_weekend: bool = False,
    now_ms: int = 0
) -> SessionEligibility:
    """
    PHẦN 24: Helper session eligibility mới.
    Assesses market open, data completeness, and warmup completeness.
    Execution blockers (risk stops, rules) are dynamic and recorded separately.
    """
    reasons = []
    market_open = not is_weekend
    if is_weekend:
        reasons.append("WEEKEND_MARKET_CLOSED")

    data_complete = has_data
    if not has_data:
        reasons.append("DATA_MISSING")

    if not warmup_complete:
        reasons.append("WARMUP_INCOMPLETE")

    execution_data_valid = data_complete and warmup_complete
    eligible = market_open and data_complete and warmup_complete

    return SessionEligibility(
        session_id=session_id,
        ny_date=ny_date,
        market_open=market_open,
        data_complete=data_complete,
        warmup_complete=warmup_complete,
        execution_data_valid=execution_data_valid,
        eligible=eligible,
        reasons=reasons,
        assessed_at_ms=now_ms
    )


def should_schedule_daily_entry(
    state: SessionResearchState,
    now_ms: int,
    config: Any,
    eligibility: SessionEligibility
) -> Tuple[bool, str]:
    """
    PHẦN 41: should_schedule_daily_entry.
    Returns (True, reason) if daily scheduled paper entry is due at deadline.
    Condition: DAILY_PAPER cadence, in session window, deadline reached, 0 fills so far,
    session eligible, not in cooldown, and no active/terminal state.
    """
    cadence = getattr(config, "entry_cadence", "CONFIRMED_ONLY")
    variant = getattr(config, "strategy_variant", "CURRENT_BASELINE")
    is_daily_mode = (cadence == "DAILY_PAPER") or (variant == "NY_DAILY_PAPER_RESEARCH")

    if not is_daily_mode:
        return False, "NOT_DAILY_PAPER_MODE"

    if not eligibility.eligible:
        return False, f"SESSION_INELIGIBLE: {', '.join(eligibility.reasons)}"

    if state.fills >= 1:
        return False, "TARGET_ALREADY_FILLED"

    if state.status in ("TARGET_FILLED", "SESSION_COMPLETE", "DATA_BLOCKED"):
        return False, f"SESSION_TERMINAL_STATE: {state.status}"

    dt_ny = get_ny_datetime(now_ms)
    if not is_ny_session_window(dt_ny):
        return False, "OUTSIDE_NY_SESSION_WINDOW"

    deadline_h = getattr(config, "scheduled_deadline_hour", None) or getattr(config, "ny_deadline_hour", 14)
    deadline_m = getattr(config, "scheduled_deadline_minute", None) or getattr(config, "ny_deadline_minute", 30)

    if not is_ny_deadline_reached(dt_ny, deadline_hour=deadline_h, deadline_min=deadline_m):
        return False, "DEADLINE_NOT_YET_REACHED"

    if now_ms < state.cooldown_until_ms:
        return False, "COOLDOWN_ACTIVE"

    return True, "DEADLINE_REACHED_ZERO_FILLS"


def choose_scheduled_direction(
    context: Dict[str, Any],
    config: Any
) -> Tuple[Optional[str], str, Dict[str, Any], List[str]]:
    """
    PHẦN 42: choose_scheduled_direction.
    Selects scheduled direction deterministically:
    1. H1/H4 confirmed alignment.
    2. Conflict resolved via 15M structure / swing flow.
    3. Fallback to session momentum (relative to session open / pre-NY midpoint).
    Deterministic tie-break without random coin flips or future bias.
    """
    h1_trend = context.get("h1_trend", "UNKNOWN")
    h4_bias = context.get("h4_bias", "UNKNOWN")
    d_bias = context.get("d_bias", "UNKNOWN")
    recent_bars_15m = context.get("recent_bars_15m", [])
    recent_bars_5m = context.get("recent_bars_5m", [])

    missing_confirmations = ["NO_CONFIRMED_CHOCH", "NO_5M_DISPLACEMENT", "SCHEDULED_ENTRY_AT_DEADLINE"]
    structural_refs = {
        "h1_trend": h1_trend,
        "h4_bias": h4_bias,
        "d_bias": d_bias
    }

    # 1. Aligned HTF
    if h1_trend == "BULLISH" and h4_bias in ("BULLISH", "UNKNOWN"):
        return "LONG", "HTF_ALIGN_BULLISH", structural_refs, missing_confirmations
    if h1_trend == "BEARISH" and h4_bias in ("BEARISH", "UNKNOWN"):
        return "SHORT", "HTF_ALIGN_BEARISH", structural_refs, missing_confirmations

    if h4_bias == "BULLISH" and h1_trend in ("BULLISH", "UNKNOWN"):
        return "LONG", "H4_BIAS_BULLISH", structural_refs, missing_confirmations
    if h4_bias == "BEARISH" and h1_trend in ("BEARISH", "UNKNOWN"):
        return "SHORT", "H4_BIAS_BEARISH", structural_refs, missing_confirmations

    # 2. HTF Conflict: resolve via 15M structure & moving average
    if len(recent_bars_15m) >= 10:
        c_15m = [b["close"] for b in recent_bars_15m[-10:]]
        ma_15m = sum(c_15m) / len(c_15m)
        curr_c = recent_bars_15m[-1]["close"]
        structural_refs["15m_ma10"] = round(ma_15m, 2)
        structural_refs["curr_15m_close"] = round(curr_c, 2)

        if curr_c > ma_15m:
            missing_confirmations.append("HTF_CONFLICT_RESOLVED_15M_MOMENTUM")
            return "LONG", "15M_STRUCTURE_OVERRIDE_BULLISH", structural_refs, missing_confirmations
        elif curr_c < ma_15m:
            missing_confirmations.append("HTF_CONFLICT_RESOLVED_15M_MOMENTUM")
            return "SHORT", "15M_STRUCTURE_OVERRIDE_BEARISH", structural_refs, missing_confirmations

    # 3. Fallback: Daily bias or session direction
    if d_bias == "BULLISH":
        return "LONG", "DAILY_BIAS_FALLBACK", structural_refs, missing_confirmations
    elif d_bias == "BEARISH":
        return "SHORT", "DAILY_BIAS_FALLBACK", structural_refs, missing_confirmations

    # Deterministic tie-break based on last bar close vs open
    if recent_bars_15m:
        last_b = recent_bars_15m[-1]
        if last_b["close"] >= last_b["open"]:
            return "LONG", "BAR_MOMENTUM_TIEBREAK", structural_refs, missing_confirmations
        else:
            return "SHORT", "BAR_MOMENTUM_TIEBREAK", structural_refs, missing_confirmations

    return None, "NO_DIRECTIONAL_BIAS", structural_refs, missing_confirmations


def build_scheduled_price_plan(
    direction: str,
    context: Dict[str, Any],
    curr_quote: Dict[str, Any],
    config: Any
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    PHẦN 43: build_scheduled_price_plan.
    Builds price geometry (entry, structural SL, structural/extension TP) and calculates Net R:R.
    Requires Net R:R >= 2.0R under realistic costs.
    """
    recent_bars_5m = context.get("recent_bars_5m", [])
    recent_bars_15m = context.get("recent_bars_15m", [])
    sim_time = curr_quote.get("sim_time", 0)
    curr_close = curr_quote.get("close", 0.0)
    spread_usd = curr_quote.get("spread_usd", 0.35)
    costs: CostAssumptions = curr_quote.get("costs", CostAssumptions())
    capital = curr_quote.get("capital", 1000.0)
    leverage = getattr(config, "leverage", 30)
    margin_mode = getattr(config, "margin_mode", "ISOLATED")

    quota_risk_pct = getattr(config, "quota_risk_pct", None) or 0.10

    # Entry with taker fee + half spread + slippage
    if direction == "LONG":
        fill_entry = round(curr_close + (0.5 * spread_usd) + costs.slippage_usd, 2)
    else:
        fill_entry = round(curr_close - (0.5 * spread_usd) - costs.slippage_usd, 2)

    # Volatility buffer from ATR
    atr_source = recent_bars_5m if len(recent_bars_5m) >= 15 else recent_bars_15m
    raw_atr = compute_atr_bars(atr_source, 14) if atr_source else 3.0
    atr = max(raw_atr, 2.0)

    # Swing levels for invalidation & liquidity targets
    sh_5m, sl_5m = smc_engine.identify_pivots(ensure_proxies(recent_bars_5m[-30:]), "5M") if len(recent_bars_5m) >= 15 else ([], [])

    if direction == "LONG":
        if sl_5m:
            sl_cand = round(sl_5m[-1]["price"] - max(0.2 * atr, 0.50), 2)
            stop_model = "STRUCTURAL_5M_SWING_LOW"
        else:
            sl_cand = round(fill_entry - max(1.2 * atr, 3.0), 2)
            stop_model = "ATR_VOLATILITY_STOP"

        # Ensure valid SL distance
        sl_dist = fill_entry - sl_cand
        if sl_dist < 1.5:
            sl_cand = round(fill_entry - max(1.0 * atr, 2.5), 2)
            sl_dist = fill_entry - sl_cand
            stop_model = "ATR_CLAMPED_STOP"
        elif sl_dist > 4.0 * atr:
            sl_cand = round(fill_entry - 2.5 * atr, 2)
            sl_dist = fill_entry - sl_cand
            stop_model = "ATR_CEILING_STOP"

        # Target: External Liquidity / Measured Move Extension
        target_model = "MEASURED_RANGE_EXTENSION_2R"
        if sh_5m and sh_5m[-1]["price"] > fill_entry + (2.0 * sl_dist):
            tp_cand = round(sh_5m[-1]["price"], 2)
            target_model = "STRUCTURAL_5M_SWING_HIGH"
        else:
            tp_cand = round(fill_entry + (sl_dist * 2.5), 2)

    else:  # SHORT
        if sh_5m:
            sl_cand = round(sh_5m[-1]["price"] + max(0.2 * atr, 0.50), 2)
            stop_model = "STRUCTURAL_5M_SWING_HIGH"
        else:
            sl_cand = round(fill_entry + max(1.2 * atr, 3.0), 2)
            stop_model = "ATR_VOLATILITY_STOP"

        sl_dist = sl_cand - fill_entry
        if sl_dist < 1.5:
            sl_cand = round(fill_entry + max(1.0 * atr, 2.5), 2)
            sl_dist = sl_cand - fill_entry
            stop_model = "ATR_CLAMPED_STOP"
        elif sl_dist > 4.0 * atr:
            sl_cand = round(fill_entry + 2.5 * atr, 2)
            sl_dist = sl_cand - fill_entry
            stop_model = "ATR_CEILING_STOP"

        target_model = "MEASURED_RANGE_EXTENSION_2R"
        if sl_5m and sl_5m[-1]["price"] < fill_entry - (2.0 * sl_dist):
            tp_cand = round(sl_5m[-1]["price"], 2)
            target_model = "STRUCTURAL_5M_SWING_LOW"
        else:
            tp_cand = round(fill_entry - (sl_dist * 2.5), 2)

    is_geom_valid, geom_err = validate_price_geometry(direction, fill_entry, sl_cand, tp_cand)
    if not is_geom_valid:
        return None, f"INVALID_GEOMETRY: {geom_err}"

    # Solve target price that satisfies Net RR >= 2.0R under realistic costs
    valid_plan_found = False
    final_calc = None
    final_tp = tp_cand

    for mult in [2.2, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0]:
        test_tp = round(fill_entry + (sl_dist * mult) if direction == "LONG" else fill_entry - (sl_dist * mult), 2)
        test_calc = calculate_risk_reward(
            direction=direction,
            entry=fill_entry,
            sl=sl_cand,
            tp=test_tp,
            capital=capital,
            risk_pct=quota_risk_pct,
            costs=costs,
            entry_has_slippage=True,
            leverage=leverage,
            margin_mode=margin_mode,
            min_net_rr=2.0
        )
        if test_calc.can_execute and test_calc.net_rr >= 2.0:
            final_tp = test_tp
            final_calc = test_calc
            valid_plan_found = True
            break

    if not valid_plan_found or final_calc is None:
        reason = getattr(test_calc, 'skip_reason', None) or getattr(test_calc, 'invalid_reason', None) or 'NET_RR_TOO_LOW'
        return None, f"NET_RR_TOO_LOW: {reason}"

    calc = final_calc
    tp_cand = final_tp

    plan = {
        "direction": direction,
        "entry_price": fill_entry,
        "stop_loss": sl_cand,
        "take_profit": tp_cand,
        "calc": calc,
        "stop_model": stop_model,
        "target_model": target_model,
        "atr": round(atr, 2)
    }
    return plan, None


def evaluate_scheduled_entry(
    context: Dict[str, Any],
    state: SessionResearchState,
    config: Any,
    curr_quote: Dict[str, Any]
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """
    PHẦN 45: evaluate_scheduled_entry.
    Composes direction selection, price planning, and candidate generation.
    Labels candidate with SMC_CONTEXT_SCHEDULED_PAPER and captures missing confirmations.
    """
    rejections = []
    direction, dir_model, structural_refs, missing_confirmations = choose_scheduled_direction(context, config)
    if not direction:
        rejections.append("NO_DIRECTIONAL_BIAS")
        return None, rejections

    plan, plan_err = build_scheduled_price_plan(direction, context, curr_quote, config)
    if not plan:
        rejections.append(plan_err or "PRICE_PLAN_UNAVAILABLE")
        return None, rejections

    sim_time = curr_quote.get("sim_time", 0)
    setup_id = f"sched-{state.session_id}-{int(sim_time)}"

    candidate = {
        "setup_id": setup_id,
        "strategy_family": "SMC_CONTEXT_SCHEDULED",
        "entry_type": "SMC_CONTEXT_SCHEDULED_PAPER",
        "confidence_kind": "HEURISTIC",
        "missing_confirmations": missing_confirmations,
        "direction": direction,
        "entry_price": plan["entry_price"],
        "stop_loss": plan["stop_loss"],
        "take_profit": plan["take_profit"],
        "calc": plan["calc"],
        "entry_model": dir_model,
        "stop_model": plan["stop_model"],
        "target_model": plan["target_model"],
        "decision_ms": sim_time,
        "structural_references": structural_refs,
        "notes": f"Scheduled NY paper entry at deadline ({dir_model})"
    }
    return candidate, rejections


def summarize_cadence(session_outcomes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    PHẦN 61: summarize_cadence.
    Aggregates session cadence statistics across the entire evaluation horizon.
    """
    calendar_days = len(session_outcomes)
    ny_sessions_total = calendar_days
    market_open_sessions = sum(1 for s in session_outcomes if s.get("market_open", False))
    data_complete_sessions = sum(1 for s in session_outcomes if s.get("data_complete", False))
    executable_sessions = sum(1 for s in session_outcomes if s.get("eligible", False))

    sessions_with_fills = sum(1 for s in session_outcomes if s.get("fills_count", 0) >= 1)
    confirmed_fill_sessions = sum(1 for s in session_outcomes if s.get("confirmed_fills", 0) >= 1)
    scheduled_fill_sessions = sum(1 for s in session_outcomes if s.get("scheduled_fills", 0) >= 1)

    blocked_sessions = sum(1 for s in session_outcomes if s.get("outcome_category") in ("RISK_STOP", "POLICY_BLOCKED"))
    unmet_sessions = sum(1 for s in session_outcomes if s.get("outcome_category") in ("UNFULFILLED", "NO_VALID_PRICE_PLAN"))

    coverage_pct = round((sessions_with_fills / executable_sessions * 100.0), 1) if executable_sessions > 0 else 0.0

    return {
        "calendar_days": calendar_days,
        "ny_sessions_total": ny_sessions_total,
        "market_open_sessions": market_open_sessions,
        "data_complete_sessions": data_complete_sessions,
        "executable_sessions": executable_sessions,
        "sessions_with_fills": sessions_with_fills,
        "confirmed_fill_sessions": confirmed_fill_sessions,
        "scheduled_fill_sessions": scheduled_fill_sessions,
        "blocked_sessions": blocked_sessions,
        "unmet_sessions": unmet_sessions,
        "coverage_pct": coverage_pct
    }


def finalize_session_outcome(
    state: SessionResearchState,
    eligibility: SessionEligibility,
    decision_events: Optional[List[Dict[str, Any]]] = None,
    boundary: str = "SESSION_END"
) -> Dict[str, Any]:
    """
    PHẦN 63: finalize_session_outcome.
    Finalizes single session outcome for reporting and audit tables.
    """
    if not eligibility.market_open:
        outcome_cat = "MARKET_CLOSED"
        primary_reason = "WEEKEND_MARKET_CLOSED"
    elif not eligibility.data_complete:
        outcome_cat = "DATA_MISSING"
        primary_reason = "HISTORICAL_DATA_UNAVAILABLE"
    elif not eligibility.warmup_complete:
        outcome_cat = "WARMUP_INCOMPLETE"
        primary_reason = "WARMUP_CANDLES_INSUFFICIENT"
    elif state.fills >= 1:
        outcome_cat = "TARGET_ACHIEVED"
        primary_reason = f"FILLED_{state.fills}_TRADES"
    elif state.block_reason and "LOSS" in state.block_reason:
        outcome_cat = "RISK_STOP"
        primary_reason = state.block_reason
    elif state.block_reason and ("POLICY" in state.block_reason or "RULE" in state.block_reason or "NEWS" in state.block_reason):
        outcome_cat = "POLICY_BLOCKED"
        primary_reason = state.block_reason
    elif state.unmet_reason:
        outcome_cat = "NO_VALID_PRICE_PLAN" if "RR" in state.unmet_reason or "GEOMETRY" in state.unmet_reason else "UNFULFILLED"
        primary_reason = state.unmet_reason
    else:
        outcome_cat = "UNFULFILLED"
        primary_reason = "NO_QUALIFIED_SETUP_OR_WINDOW_EXPIRED"

    return {
        "session_id": state.session_id,
        "date_ny": state.date_ny,
        "trade_day_vn": state.trade_day_vn,
        "market_open": eligibility.market_open,
        "data_complete": eligibility.data_complete,
        "eligible": eligibility.eligible,
        "status": state.status,
        "outcome_category": outcome_cat,
        "primary_reason": primary_reason,
        "fills_count": state.fills,
        "confirmed_fills": state.confirmed_fill_count,
        "scheduled_fills": state.scheduled_fill_count,
        "attempts_count": state.attempts_count,
        "missing_confirmations": state.missing_confirmations,
        "last_decision_ms": state.last_decision_ms
    }
