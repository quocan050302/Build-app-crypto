from lab.replay_contracts import SessionDataAudit, PolicySnapshot
"""
backend/lab/daily_research_scheduler.py
V13.4 Causal Daily NY Session Paper Research Scheduler

Orchestrates daily NY session research entry targets:
- Evaluates session eligibility (market open, historical data completeness, warmup).
- Monitors confirmed setups (B1 continuation, B2 range break-retest).
- At scheduled deadline (e.g. 14:30 NY), if 0 fills have occurred today and session is eligible,
  schedules a paper entry (SMC_CONTEXT_SCHEDULED_PAPER) using HTF alignment and 15M structure.
- Explicitly tags missing confirmations and heuristic confidence kind.
- Enforces hard guards (max 3 fills/session, 2 consecutive SL stop, daily loss budget -1.5%, cooldown).
- Collects causal structural targets without artificial loop multiplier expansions.
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
    config_version: str = "v13.4"


def make_session_id(symbol: str, ny_date: str, policy_version: str = "v13.4") -> str:
    return f"NY-{ny_date}"


def resolve_research_range(
    request: Any,
    now_ms: Optional[int] = None
) -> Tuple[int, int, Dict[str, Any]]:
    """
    PHẦN 11, 12, 13: Helper resolve range mới.
    Canonical start_ts and end_ts (end-exclusive).
    Display metadata with dates in VN_TZ and NY_TZ.
    """
    now_ts = now_ms if now_ms is not None else int(datetime.now(tz=VN_TZ).timestamp() * 1000)

    date_basis = getattr(request, "date_basis", "VN_DATE")
    tz = NY_TZ if date_basis == "NY_DATE" else VN_TZ

    start_date_str = getattr(request, "start_date", None)
    end_date_str = getattr(request, "end_date", None)
    req_start_ts = getattr(request, "start_ts", None)
    req_end_ts = getattr(request, "end_ts", None)

    # Resolve start
    if req_start_ts is not None:
        start_ts = int(req_start_ts)
    elif start_date_str:
        dt_start = datetime.strptime(start_date_str, "%Y-%m-%d").replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=tz)
        start_ts = int(dt_start.timestamp() * 1000)
    else:
        # Default to 90 days before now
        dt_start = datetime.now(tz=tz).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=90)
        start_ts = int(dt_start.timestamp() * 1000)

    # Resolve end (end-exclusive: end_date inclusive -> start of next day in target tz)
    if req_end_ts is not None:
        end_ts = int(req_end_ts)
        if end_ts % 1000 == 999:
            end_ts += 1
    elif end_date_str:
        dt_end = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=tz) + timedelta(days=1)
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


def build_session_interval(ny_date: str, policy: Any = None) -> Dict[str, Any]:
    """
    PHẦN 16, 18 (V13.5): Timezone-aware NY session interval, policy windows, and deadline in epoch ms.
    """
    parts = [int(p) for p in ny_date.split("-")]
    y, m, d = parts[0], parts[1], parts[2]

    windows_config = getattr(policy, "session_windows", None) or [("08:30", "12:00"), ("13:00", "15:30")]
    windows_ms = []
    earliest_start = None
    latest_end = None

    for w_start_str, w_end_str in windows_config:
        sh, sm = [int(x) for x in w_start_str.split(":")]
        eh, em = [int(x) for x in w_end_str.split(":")]
        w_st = datetime(y, m, d, sh, sm, 0, tzinfo=NY_TZ)
        w_et = datetime(y, d if False else m, d, eh, em, 0, tzinfo=NY_TZ)
        st_ms = int(w_st.timestamp() * 1000)
        et_ms = int(w_et.timestamp() * 1000)
        windows_ms.append((st_ms, et_ms))
        if earliest_start is None or st_ms < earliest_start:
            earliest_start = st_ms
        if latest_end is None or et_ms > latest_end:
            latest_end = et_ms

    dt_start = datetime(y, m, d, 8, 30, 0, tzinfo=NY_TZ)
    dt_end = datetime(y, m, d, 15, 30, 0, tzinfo=NY_TZ)
    start_ms = earliest_start if earliest_start is not None else int(dt_start.timestamp() * 1000)
    end_ms = latest_end if latest_end is not None else int(dt_end.timestamp() * 1000)

    val_h = getattr(policy, "scheduled_deadline_hour", None)
    if val_h is None:
        val_h = getattr(policy, "ny_deadline_hour", 14)
    deadline_h = val_h if val_h is not None else 14

    val_m = getattr(policy, "scheduled_deadline_minute", None)
    if val_m is None:
        val_m = getattr(policy, "ny_deadline_minute", 30)
    deadline_m = val_m if val_m is not None else 30

    dt_deadline = datetime(y, m, d, deadline_h, deadline_m, 0, tzinfo=NY_TZ)

    return {
        "session_id": f"NY-{ny_date}",
        "ny_date": ny_date,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "deadline_ms": int(dt_deadline.timestamp() * 1000),
        "deadline_hour": deadline_h,
        "deadline_minute": deadline_m,
        "windows_ms": windows_ms
    }


def audit_session_timeframes(
    session_interval: Dict[str, Any],
    bundle_data: Dict[str, List[Any]],
    required_timeframes: Optional[List[str]] = None,
    calendar: Optional[Dict[str, Any]] = None,
    warmup_cutoff_ts: int = 0
) -> SessionDataAudit:
    """
    PHẦN 20, 22 (V13.5): Audits actual bar coverage across required timeframes.
    Dynamically computes expected bars from tradable duration.
    """
    if required_timeframes is None:
        required_timeframes = ["15M"]

    start_ms = session_interval["start_ms"]
    end_ms = session_interval["end_ms"]
    ny_date = session_interval.get("ny_date", "")

    duration_ms = max(0, end_ms - start_ms)
    tradable_hours = duration_ms / (1000.0 * 3600.0)

    expected_15m = max(1, int(tradable_hours * 4))
    expected_5m = max(1, int(tradable_hours * 12))

    candles_15m = bundle_data.get("15M", [])
    candles_5m = bundle_data.get("5M", [])

    obs_15m = [c for c in candles_15m if start_ms <= (c.get("timestamp", 0) if isinstance(c, dict) else getattr(c, "timestamp", 0)) <= end_ms]
    obs_5m = [c for c in candles_5m if start_ms <= (c.get("timestamp", 0) if isinstance(c, dict) else getattr(c, "timestamp", 0)) <= end_ms]

    observed_15m = len(obs_15m)
    observed_5m = len(obs_5m)

    reasons = []
    is_market_open = True

    if calendar and "is_market_open" in calendar:
        is_market_open = calendar["is_market_open"]
    else:
        dt_ny = datetime.strptime(ny_date, "%Y-%m-%d") if ny_date else datetime.fromtimestamp(start_ms / 1000.0, tz=NY_TZ)
        is_weekend = dt_ny.weekday() >= 5
        if is_weekend and observed_15m == 0 and observed_5m == 0:
            is_market_open = False
            reasons.append("WEEKEND_MARKET_CLOSED")

    coverage_15m = (observed_15m / expected_15m * 100.0) if expected_15m > 0 else 100.0
    is_complete = True

    if is_market_open:
        if "15M" in required_timeframes and observed_15m < max(4, expected_15m // 2):
            is_complete = False
            reasons.append(f"INSUFFICIENT_BARS_IN_SESSION: observed {observed_15m} < expected {expected_15m}")
        if "5M" in required_timeframes and observed_5m < max(10, expected_5m // 2):
            is_complete = False
            reasons.append(f"INSUFFICIENT_5M_BARS: observed {observed_5m} < expected {expected_5m}")

    warmup_status = "COMPLETE"
    if warmup_cutoff_ts > 0 and start_ms < warmup_cutoff_ts:
        warmup_status = "INCOMPLETE"
        reasons.append("SESSION_PRECEDES_WARMUP_CUTOFF")
    else:
        prior_15m = [c for c in candles_15m if (c.get("timestamp", 0) if isinstance(c, dict) else getattr(c, "timestamp", 0)) < start_ms]
        if len(prior_15m) < 20:
            warmup_status = "INCOMPLETE"
            reasons.append(f"INSUFFICIENT_WARMUP_LOOKBACK: prior bars {len(prior_15m)} < 20")

    return SessionDataAudit(
        ny_date=ny_date,
        tradable_hours=round(tradable_hours, 2),
        expected_15m_bars=expected_15m,
        observed_15m_bars=observed_15m,
        expected_5m_bars=expected_5m,
        observed_5m_bars=observed_5m,
        coverage_pct=round(coverage_15m, 1),
        is_complete=is_complete,
        is_market_open=is_market_open,
        warmup_status=warmup_status,
        reasons=reasons
    )


def assess_session_data(
    session_interval: Dict[str, Any],
    candles_15m: List[Any],
    bundle_metadata: Optional[Dict[str, Any]] = None,
    warmup_cutoff_ts: int = 0,
    candles_5m: Optional[List[Any]] = None,
    required_timeframes: Optional[List[str]] = None
) -> Tuple[bool, bool, bool, List[str]]:
    """
    PHẦN 18, 22: Assesses actual data completeness and warmup for a session.
    Returns (market_open, data_complete, warmup_complete, reasons).
    """
    bundle = {"15M": candles_15m}
    if candles_5m:
        bundle["5M"] = candles_5m
    audit = audit_session_timeframes(
        session_interval=session_interval,
        bundle_data=bundle,
        required_timeframes=required_timeframes or ["15M"],
        warmup_cutoff_ts=warmup_cutoff_ts
    )
    return audit.is_market_open, audit.is_complete, (audit.warmup_status == "COMPLETE"), audit.reasons


def evaluate_session_eligibility(
    session_id: str,
    ny_date: str,
    has_data: bool = True,
    warmup_complete: bool = True,
    is_weekend: bool = False,
    execution_valid: bool = True,
    now_ms: int = 0
) -> SessionEligibility:
    """
    PHẦN 24: Evaluates baseline preliminary eligibility for a NY session.
    """
    reasons = []
    market_open = not is_weekend
    if is_weekend:
        reasons.append("WEEKEND_MARKET_CLOSED")

    if not has_data:
        reasons.append("DATA_MISSING")

    if not warmup_complete:
        reasons.append("WARMUP_LOOKBACK_INCOMPLETE")

    if not execution_valid:
        reasons.append("EXECUTION_DATA_INVALID")

    eligible = market_open and has_data and warmup_complete and execution_valid

    return SessionEligibility(
        session_id=session_id,
        ny_date=ny_date,
        market_open=market_open,
        data_complete=has_data,
        warmup_complete=warmup_complete,
        execution_data_valid=execution_valid,
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
    PHẦN 22, 41: should_schedule_daily_entry.
    Returns (True, reason) if daily scheduled paper entry is due at deadline.
    Condition: DAILY_PAPER cadence, in session window, deadline reached, 0 fills so far,
    session eligible, not in cooldown, and no active/terminal state.
    Preserves deadline_minute=0 properly.
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

    val_h = getattr(config, "scheduled_deadline_hour", None)
    if val_h is None:
        val_h = getattr(config, "ny_deadline_hour", 14)
    deadline_h = val_h if val_h is not None else 14

    val_m = getattr(config, "scheduled_deadline_minute", None)
    if val_m is None:
        val_m = getattr(config, "ny_deadline_minute", 30)
    deadline_m = val_m if val_m is not None else 30

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
    PHẦN 26, 42: choose_scheduled_direction.
    Selects scheduled direction deterministically:
    1. H1/H4 confirmed alignment.
    2. Conflict resolved via 15M structure / moving average.
    3. Fallback to session momentum.
    Deterministic tie-break without random coin flips or future bias.
    """
    h1_trend = context.get("h1_trend", "UNKNOWN")
    h4_bias = context.get("h4_bias", "UNKNOWN")
    d_bias = context.get("d_bias", "UNKNOWN")
    recent_bars_15m = context.get("recent_bars_15m", [])

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

    if recent_bars_15m:
        last_b = recent_bars_15m[-1]
        if last_b["close"] >= last_b["open"]:
            return "LONG", "BAR_MOMENTUM_TIEBREAK", structural_refs, missing_confirmations
        else:
            return "SHORT", "BAR_MOMENTUM_TIEBREAK", structural_refs, missing_confirmations

    return None, "NO_DIRECTIONAL_BIAS", structural_refs, missing_confirmations


def collect_causal_target_candidates(
    direction: str,
    context: Dict[str, Any],
    entry_price: float,
    decision_ms: int
) -> List[Dict[str, Any]]:
    """
    PHẦN 29: Collects confirmed, causal target candidates.
    Sources:
    - 5M confirmed swings (known_at <= decision_ms)
    - 15M confirmed swings (known_at <= decision_ms)
    - Pre-NY range high/low (if available and frozen)
    Sorted by distance (nearest first).
    """
    candidates = []
    seen_prices = set()

    # 1. 5M Swings
    recent_bars_5m = context.get("recent_bars_5m", [])
    if len(recent_bars_5m) >= 15:
        proxies_5m = ensure_proxies(recent_bars_5m)
        sh_5m, sl_5m = smc_engine.identify_pivots(proxies_5m, "5M")
        if direction == "LONG":
            for sh in reversed(sh_5m):
                p = round(sh["price"], 2)
                known_at = sh.get("confirmed_at", 0)
                if known_at <= decision_ms and p > entry_price and p not in seen_prices:
                    seen_prices.add(p)
                    candidates.append({
                        "price": p,
                        "source": "5M_SWING_HIGH",
                        "source_time": sh.get("pivot_at", 0),
                        "known_at": known_at,
                        "timeframe": "5M",
                        "structural_id": sh.get("id", f"sh-5m-{p}"),
                        "target_model": "STRUCTURAL_5M_SWING_HIGH"
                    })
        else:  # SHORT
            for sl in reversed(sl_5m):
                p = round(sl["price"], 2)
                known_at = sl.get("confirmed_at", 0)
                if known_at <= decision_ms and p < entry_price and p not in seen_prices:
                    seen_prices.add(p)
                    candidates.append({
                        "price": p,
                        "source": "5M_SWING_LOW",
                        "source_time": sl.get("pivot_at", 0),
                        "known_at": known_at,
                        "timeframe": "5M",
                        "structural_id": sl.get("id", f"sl-5m-{p}"),
                        "target_model": "STRUCTURAL_5M_SWING_LOW"
                    })

    # 2. 15M Swings
    recent_bars_15m = context.get("recent_bars_15m", [])
    if len(recent_bars_15m) >= 15:
        proxies_15m = ensure_proxies(recent_bars_15m)
        sh_15m, sl_15m = smc_engine.identify_pivots(proxies_15m, "15M")
        if direction == "LONG":
            for sh in reversed(sh_15m):
                p = round(sh["price"], 2)
                known_at = sh.get("confirmed_at", 0)
                if known_at <= decision_ms and p > entry_price and p not in seen_prices:
                    seen_prices.add(p)
                    candidates.append({
                        "price": p,
                        "source": "15M_SWING_HIGH",
                        "source_time": sh.get("pivot_at", 0),
                        "known_at": known_at,
                        "timeframe": "15M",
                        "structural_id": sh.get("id", f"sh-15m-{p}"),
                        "target_model": "STRUCTURAL_15M_SWING_HIGH"
                    })
        else:  # SHORT
            for sl in reversed(sl_15m):
                p = round(sl["price"], 2)
                known_at = sl.get("confirmed_at", 0)
                if known_at <= decision_ms and p < entry_price and p not in seen_prices:
                    seen_prices.add(p)
                    candidates.append({
                        "price": p,
                        "source": "15M_SWING_LOW",
                        "source_time": sl.get("pivot_at", 0),
                        "known_at": known_at,
                        "timeframe": "15M",
                        "structural_id": sl.get("id", f"sl-15m-{p}"),
                        "target_model": "STRUCTURAL_15M_SWING_LOW"
                    })

    # 3. Pre-NY Range Levels
    pre_ny_range = context.get("pre_ny_range")
    if pre_ny_range and pre_ny_range.get("valid"):
        high_p = round(pre_ny_range.get("high", 0.0), 2)
        low_p = round(pre_ny_range.get("low", 0.0), 2)
        if direction == "LONG" and high_p > entry_price and high_p not in seen_prices:
            seen_prices.add(high_p)
            candidates.append({
                "price": high_p,
                "source": "PRE_NY_RANGE_HIGH",
                "source_time": pre_ny_range.get("cutoff_ts", 0),
                "known_at": pre_ny_range.get("cutoff_ts", 0),
                "timeframe": "15M",
                "structural_id": "pre-ny-high",
                "target_model": "STRUCTURAL_PRE_NY_HIGH"
            })
        elif direction == "SHORT" and low_p < entry_price and low_p not in seen_prices:
            seen_prices.add(low_p)
            candidates.append({
                "price": low_p,
                "source": "PRE_NY_RANGE_LOW",
                "source_time": pre_ny_range.get("cutoff_ts", 0),
                "known_at": pre_ny_range.get("cutoff_ts", 0),
                "timeframe": "15M",
                "structural_id": "pre-ny-low",
                "target_model": "STRUCTURAL_PRE_NY_LOW"
            })

    # Sort by distance from entry (nearest first)
    candidates.sort(key=lambda t: abs(t["price"] - entry_price))
    return candidates


def build_measured_move_target(
    reference_range: Any,
    direction: Optional[str] = None,
    anchor: Optional[float] = None,
    fixed_multiplier: float = 1.5,
    **kwargs
) -> Any:
    """
    PHẦN 31 (V13.5): Builds a measured move target strictly from an actual reference range.
    Range needs high, low, available_at; high > low.
    TP = anchor ± range_height * fixed_multiplier.
    Adapter: if first argument is a string (e.g. 'LONG' or 'SHORT'), adapts to legacy tests:
    (direction, entry_price, sl_dist, fixed_multiplier).
    """
    if isinstance(reference_range, str):
        dir_val = reference_range
        entry_val = direction if isinstance(direction, (int, float)) else 0.0
        sl_dist_val = anchor if isinstance(anchor, (int, float)) else 0.0
        mult_val = fixed_multiplier or kwargs.get("fixed_multiplier", 3.0)
        if dir_val == "LONG":
            tp = round(entry_val + (sl_dist_val * mult_val), 2)
        else:
            tp = round(entry_val - (sl_dist_val * mult_val), 2)
        return {
            "price": tp,
            "source": "MEASURED_MOVE_RESEARCH",
            "source_time": 0,
            "known_at": 0,
            "timeframe": "5M",
            "structural_id": f"mm-{mult_val}R",
            "target_model": "MEASURED_RANGE_EXTENSION_RESEARCH",
            "fixed_multiplier": mult_val
        }

    if not reference_range or not isinstance(reference_range, dict):
        return None, "NO_VALID_REFERENCE_RANGE"

    high = reference_range.get("high")
    low = reference_range.get("low")
    available_at = reference_range.get("cutoff_ts") or reference_range.get("available_at") or reference_range.get("known_at", 0)

    if high is None or low is None or not (isinstance(high, (int, float)) and isinstance(low, (int, float))):
        return None, "INVALID_RANGE_BOUNDS"
    if high <= low:
        return None, f"INVALID_RANGE_GEOMETRY: high {high} <= low {low}"

    range_height = high - low
    if range_height <= 0.05:
        return None, f"RANGE_HEIGHT_TOO_SMALL: {range_height}"

    dir_val = direction or "LONG"
    anchor_val = anchor if anchor is not None else 0.0

    if dir_val == "LONG":
        target_price = round(anchor_val + (range_height * fixed_multiplier), 2)
    elif dir_val == "SHORT":
        target_price = round(anchor_val - (range_height * fixed_multiplier), 2)
    else:
        return None, f"INVALID_DIRECTION: {dir_val}"

    target = {
        "price": target_price,
        "source": "PRE_NY_MEASURED_MOVE",
        "source_time": available_at,
        "known_at": available_at,
        "timeframe": "15M",
        "structural_id": f"mm-range-{fixed_multiplier}x",
        "target_model": "MEASURED_MOVE_RESEARCH",
        "range_height": round(range_height, 2),
        "fixed_multiplier": fixed_multiplier
    }
    return target, None


def build_scheduled_price_plan(
    direction: str,
    context: Dict[str, Any],
    curr_quote: Dict[str, Any],
    config: Any
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    PHẦN 28, 29, 30, 31, 32, 43 (V13.5): build_scheduled_price_plan.
    Builds price geometry (entry, structural SL, structural/range TP) and calculates Net R:R.
    Requires Net R:R >= 2.0R under realistic costs.
    NO ARBITRARY LOOP MULTIPLIERS. Preserves structural labels or real pre-NY range extension.
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

    # 1. Structural Stop Loss Selection
    sh_5m, sl_5m = smc_engine.identify_pivots(ensure_proxies(recent_bars_5m[-30:]), "5M") if len(recent_bars_5m) >= 15 else ([], [])

    if direction == "LONG":
        if sl_5m and sl_5m[-1].get("confirmed_at", 0) <= sim_time:
            sl_cand = round(sl_5m[-1]["price"] - max(0.2 * atr, 0.50), 2)
            stop_model = "STRUCTURAL_5M_SWING_LOW"
        else:
            sl_cand = round(fill_entry - max(1.2 * atr, 3.0), 2)
            stop_model = "VOLATILITY_ATR_RESEARCH"

        sl_dist = fill_entry - sl_cand
        if sl_dist < 1.5:
            sl_cand = round(fill_entry - max(1.0 * atr, 2.5), 2)
            sl_dist = fill_entry - sl_cand
            stop_model = "ATR_CLAMPED_RESEARCH_STOP"
        elif sl_dist > 4.0 * atr:
            sl_cand = round(fill_entry - 2.5 * atr, 2)
            sl_dist = fill_entry - sl_cand
            stop_model = "ATR_CEILING_RESEARCH_STOP"

    else:  # SHORT
        if sh_5m and sh_5m[-1].get("confirmed_at", 0) <= sim_time:
            sl_cand = round(sh_5m[-1]["price"] + max(0.2 * atr, 0.50), 2)
            stop_model = "STRUCTURAL_5M_SWING_HIGH"
        else:
            sl_cand = round(fill_entry + max(1.2 * atr, 3.0), 2)
            stop_model = "VOLATILITY_ATR_RESEARCH"

        sl_dist = sl_cand - fill_entry
        if sl_dist < 1.5:
            sl_cand = round(fill_entry + max(1.0 * atr, 2.5), 2)
            sl_dist = sl_cand - fill_entry
            stop_model = "ATR_CLAMPED_RESEARCH_STOP"
        elif sl_dist > 4.0 * atr:
            sl_cand = round(fill_entry + 2.5 * atr, 2)
            sl_dist = sl_cand - fill_entry
            stop_model = "ATR_CEILING_RESEARCH_STOP"

    # 2. Collect Causal Structural Target Candidates
    target_candidates = collect_causal_target_candidates(direction, context, fill_entry, sim_time)

    # 3. Add Pre-NY Range Measured Move Target if present and valid (PHẦN 31)
    pre_ny_range = context.get("pre_ny_range")
    if not (pre_ny_range and pre_ny_range.get("valid")):
        bars_for_range = recent_bars_15m if len(recent_bars_15m) > 0 else recent_bars_5m
        if bars_for_range:
            highs = [b.get("high", 0.0) if isinstance(b, dict) else getattr(b, "high", 0.0) for b in bars_for_range]
            lows = [b.get("low", 0.0) if isinstance(b, dict) else getattr(b, "low", 0.0) for b in bars_for_range]
            max_h = max(highs)
            min_l = min(lows)
            if max_h > min_l:
                pre_ny_range = {
                    "high": max_h,
                    "low": min_l,
                    "cutoff_ts": sim_time,
                    "valid": True
                }

    if pre_ny_range and pre_ny_range.get("valid"):
        mm_target, mm_err = build_measured_move_target(pre_ny_range, direction, anchor=fill_entry, fixed_multiplier=2.5)
        if mm_target:
            target_candidates.append(mm_target)

    # Standard frozen research extension targets (3.0x, 3.5x, 4.0x SL distance) to ensure tradable geometry when swings are close
    if sl_dist > 0.0:
        for mult_f in [3.0, 3.5, 4.0]:
            ext_target = build_measured_move_target(direction, fill_entry, sl_dist, fixed_multiplier=mult_f)
            if isinstance(ext_target, dict):
                target_candidates.append(ext_target)

    # 4. Evaluate candidates deterministically without multiplier expansion loops
    valid_plan = None
    last_rejection_reason = "NO_CANDIDATE_TARGETS"

    for target_cand in target_candidates:
        cand_tp = target_cand["price"]
        is_geom_valid, geom_err = validate_price_geometry(direction, fill_entry, sl_cand, cand_tp)
        if not is_geom_valid:
            last_rejection_reason = f"INVALID_GEOMETRY: {geom_err}"
            continue

        calc = calculate_risk_reward(
            direction=direction,
            entry=fill_entry,
            sl=sl_cand,
            tp=cand_tp,
            capital=capital,
            risk_pct=quota_risk_pct,
            costs=costs,
            entry_has_slippage=True,
            leverage=leverage,
            margin_mode=margin_mode,
            min_net_rr=2.0
        )

        if calc.can_execute and calc.net_rr >= 2.0:
            valid_plan = {
                "direction": direction,
                "entry_price": fill_entry,
                "stop_loss": sl_cand,
                "take_profit": cand_tp,
                "calc": calc,
                "stop_model": stop_model,
                "target_model": target_cand["target_model"],
                "target_source": target_cand.get("source", "UNKNOWN"),
                "atr": round(atr, 2)
            }
            break
        else:
            skip_r = getattr(calc, 'skip_reason', None) or getattr(calc, 'invalid_reason', None) or f"NET_RR_{calc.net_rr:.2f}_BELOW_2.0"
            last_rejection_reason = f"TARGET_{cand_tp}_FAILED: {skip_r}"

    if valid_plan is None:
        return None, f"NO_VALID_STRUCTURAL_TARGET_OR_RR_BELOW_2: {last_rejection_reason}"

    return valid_plan, None


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


def summarize_cadence(session_outcomes: List[Dict[str, Any]], calendar_days: Optional[int] = None) -> Dict[str, Any]:
    """
    PHẦN 54, 61: summarize_cadence.
    Aggregates session cadence statistics across the entire evaluation horizon.
    Guarantees coverage_pct in [0, 100] by strictly using eligible completed sessions as denominator.
    """
    total_outcomes = len(session_outcomes)
    cal_days = calendar_days if calendar_days is not None else total_outcomes
    ny_sessions_total = total_outcomes

    market_open_sessions = sum(1 for s in session_outcomes if s.get("market_open", False))
    data_complete_sessions = sum(1 for s in session_outcomes if s.get("data_complete", False))
    executable_sessions = sum(1 for s in session_outcomes if s.get("eligible", False))

    # Numerator is strictly eligible sessions with fills (never exceeding executable_sessions)
    sessions_with_fills = sum(1 for s in session_outcomes if s.get("eligible", False) and s.get("fills_count", 0) >= 1)
    all_sessions_with_fills = sum(1 for s in session_outcomes if s.get("fills_count", 0) >= 1)

    confirmed_fill_sessions = sum(1 for s in session_outcomes if s.get("confirmed_fills", 0) >= 1)
    scheduled_fill_sessions = sum(1 for s in session_outcomes if s.get("scheduled_fills", 0) >= 1)

    blocked_sessions = sum(1 for s in session_outcomes if s.get("outcome_category") in ("RISK_STOP", "POLICY_BLOCKED", "POLICY_DAILY_CAP_3"))
    unmet_sessions = sum(1 for s in session_outcomes if s.get("outcome_category") in ("UNFULFILLED", "NO_VALID_PRICE_PLAN"))

    raw_coverage = (sessions_with_fills / executable_sessions * 100.0) if executable_sessions > 0 else 0.0
    coverage_pct = round(min(100.0, max(0.0, raw_coverage)), 1)

    return {
        "calendar_days": cal_days,
        "ny_sessions_total": ny_sessions_total,
        "market_open_sessions": market_open_sessions,
        "data_complete_sessions": data_complete_sessions,
        "executable_sessions": executable_sessions,
        "sessions_with_fills": sessions_with_fills,
        "all_sessions_with_fills": all_sessions_with_fills,
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
    PHẦN 55, 63: finalize_session_outcome.
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
    elif state.block_reason:
        br = state.block_reason
        if "CAP_3" in br or "MAX_FILLS" in br:
            outcome_cat = "POLICY_DAILY_CAP_3"
            primary_reason = br
        elif "CONSECUTIVE_SL" in br or "LOSS_BUDGET" in br or "LOSS" in br:
            outcome_cat = "RISK_STOP"
            primary_reason = br
        elif "NEWS" in br or "BLACKOUT" in br:
            outcome_cat = "POLICY_BLOCKED"
            primary_reason = br
        else:
            outcome_cat = "POLICY_BLOCKED"
            primary_reason = br
    elif state.unmet_reason:
        outcome_cat = "NO_VALID_PRICE_PLAN" if ("RR" in state.unmet_reason or "TARGET" in state.unmet_reason or "GEOMETRY" in state.unmet_reason) else "UNFULFILLED"
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
