"""
Aurum Desk V12_1: New York Session Strategy Variants & Quota Models.
Defines:
1. Setup B1: NY_TREND_CONTINUATION (HTF trend alignment, 15M pullback to POI/FVG without mandatory sweep, 5M displacement trigger, Net RR >= 2.0)
2. Setup B2: NY_RANGE_BREAK_RETEST (Pre-NY 00:00-08:25 range, close breakout in H1 direction, confirmed retest & hold, Net RR >= 2.0)
3. Mode C Quota Candidate: Triggered at 14:30 NY deadline if 0 NY fills today, deterministic weighted ranking, risk 0.10% equity, strict hard guards.
"""
import math
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Optional, Tuple

import smc_engine
from domain_calculator import calculate_risk_reward, validate_price_geometry, CostAssumptions

NY_TZ = ZoneInfo("America/New_York")
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

# NY Session Boundaries
# Standard research windows: 08:30 - 12:00 and 13:00 - 15:30
NY_MORNING_START = dtime(8, 30)
NY_MORNING_END = dtime(12, 0)
NY_AFTERNOON_START = dtime(13, 0)
NY_AFTERNOON_END = dtime(15, 30)

# Pre-NY Range Window: 00:00 - 08:25
PRE_NY_START = dtime(0, 0)
PRE_NY_END = dtime(8, 25)

# Quota Deadline: 14:30
QUOTA_DEADLINE = dtime(14, 30)


class BarProxy:
    """Provides dual dict-indexing and attribute access for candle structures."""
    def __init__(self, d: Any):
        if isinstance(d, dict):
            self._d = d
            self.timestamp = int(d.get("timestamp", 0))
            self.open = float(d.get("open", 0.0))
            self.high = float(d.get("high", 0.0))
            self.low = float(d.get("low", 0.0))
            self.close = float(d.get("close", 0.0))
            self.volume = float(d.get("volume", 0.0))
            self.is_closed = bool(d.get("is_closed", True))
            self.close_time = int(d.get("close_time", self.timestamp + 15 * 60 * 1000))
        else:
            self._d = getattr(d, "_d", {})
            self.timestamp = int(getattr(d, "timestamp", 0))
            self.open = float(getattr(d, "open", 0.0))
            self.high = float(getattr(d, "high", 0.0))
            self.low = float(getattr(d, "low", 0.0))
            self.close = float(getattr(d, "close", 0.0))
            self.volume = float(getattr(d, "volume", 0.0))
            self.is_closed = bool(getattr(d, "is_closed", True))
            self.close_time = int(getattr(d, "close_time", self.timestamp + 15 * 60 * 1000))

    def __getitem__(self, item):
        if self._d and item in self._d:
            return self._d[item]
        return getattr(self, item)

    def get(self, item, default=None):
        if self._d and item in self._d:
            return self._d[item]
        return getattr(self, item, default)


def ensure_proxies(bars: List[Any]) -> List[BarProxy]:
    return [b if isinstance(b, BarProxy) else BarProxy(b) for b in bars]


def compute_atr_bars(bars: List[Any], period: int = 14) -> float:
    if len(bars) < 2:
        return 2.0
    p_bars = ensure_proxies(bars)
    tr_list = []
    for i in range(1, len(p_bars)):
        c = p_bars[i]
        prev = p_bars[i - 1]
        tr = max(
            c.high - c.low,
            abs(c.high - prev.close),
            abs(c.low - prev.close)
        )
        tr_list.append(tr)
    recent_tr = tr_list[-period:] if len(tr_list) >= period else tr_list
    return round(sum(recent_tr) / len(recent_tr), 2) if recent_tr else 2.0


def get_ny_datetime(ts_ms: int) -> datetime:
    """Converts UTC epoch ms to timezone-aware America/New_York datetime."""
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=NY_TZ)


def is_ny_session_window(dt_ny: datetime) -> bool:
    """Checks whether datetime is inside active NY research windows (08:30-12:00 or 13:00-15:30)."""
    t = dt_ny.time()
    in_morning = (NY_MORNING_START <= t <= NY_MORNING_END)
    in_afternoon = (NY_AFTERNOON_START <= t <= NY_AFTERNOON_END)
    return in_morning or in_afternoon


def is_pre_ny_window(dt_ny: datetime) -> bool:
    """Checks whether datetime is within pre-NY range window (00:00 - 08:25 NY)."""
    t = dt_ny.time()
    return PRE_NY_START <= t <= PRE_NY_END


def is_ny_deadline_reached(dt_ny: datetime, deadline_hour: int = 14, deadline_min: int = 30) -> bool:
    """Checks if NY deadline has been reached (default >= 14:30 NY and < 15:30 NY)."""
    t = dt_ny.time()
    deadline = dtime(deadline_hour, deadline_min)
    return (deadline <= t <= NY_AFTERNOON_END)


def compute_pre_ny_range(
    candles_15m: List[Dict[str, Any]],
    session_ny_date: str,
    frozen_cache: Optional[Dict[str, Any]] = None,
    sim_time: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """
    Computes pre-NY high/low range using only closed candles strictly within 00:00 to 08:25 NY time.
    Strictly excludes any candle closing after 08:25 NY time (e.g. 08:15 15M bar closing at 08:30 is excluded).
    Freezes the computed range once 08:25 passes so it never drifts from rolling window truncation.
    """
    if frozen_cache is not None and session_ny_date in frozen_cache:
        return frozen_cache[session_ny_date]

    range_candles = []
    for c in candles_15m:
        ts = int(c["timestamp"])
        close_ts = int(c.get("close_time", ts + 15 * 60 * 1000))
        open_dt = get_ny_datetime(ts)
        close_dt = get_ny_datetime(close_ts)

        # Candle must belong to session_ny_date, open >= 00:00, and close <= 08:25:00 NY time
        if (
            open_dt.strftime("%Y-%m-%d") == session_ny_date
            and open_dt.time() >= PRE_NY_START
            and close_dt.time() <= PRE_NY_END
        ):
            range_candles.append(c)

    if len(range_candles) < 2:  # At least 30 minutes of data
        return None

    range_high = max(float(c["high"]) for c in range_candles)
    range_low = min(float(c["low"]) for c in range_candles)
    range_size = round(range_high - range_low, 2)
    start_ms = int(range_candles[0]["timestamp"])
    last_c = range_candles[-1]
    end_ms = int(last_c.get("close_time", int(last_c["timestamp"]) + 15 * 60 * 1000))

    result = {
        "session_ny_date": session_ny_date,
        "range_high": range_high,
        "range_low": range_low,
        "range_size": range_size,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "actual_end_time_ny": get_ny_datetime(end_ms).strftime("%H:%M:%S"),
        "candle_count": len(range_candles),
        "is_frozen": True
    }

    if frozen_cache is not None:
        if sim_time is not None:
            if get_ny_datetime(sim_time).time() >= PRE_NY_END:
                frozen_cache[session_ny_date] = result
        else:
            frozen_cache[session_ny_date] = result

    return result


def evaluate_setup_b1_trend_continuation(
    curr_bar_15m: Dict[str, Any],
    recent_bars_15m: List[Dict[str, Any]],
    recent_bars_5m: List[Dict[str, Any]],
    d_bias: str,
    h4_bias: str,
    h1_trend: str,
    sim_time: int,
    capital: float,
    risk_pct: float,
    leverage: int,
    margin_mode: str,
    costs: CostAssumptions,
    spread_usd: float,
    min_net_rr: float = 2.0
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Setup B1: NY_TREND_CONTINUATION
    - D1 is background context; H4 is main structure; H1 determines trade direction.
    - H1 and H4 agree, or H4 transition is confirmed.
    - 15M pullback into valid POI/FVG/OB without requiring liquidity sweep.
    - 5M confirmation: displacement in direction of trend.
    - Stop Loss at structural invalidation + buffer.
    - Take Profit to pre-existing swing liquidity target.
    - Net RR >= 2.0 after full costs.
    """
    dt_ny = get_ny_datetime(sim_time)
    if not is_ny_session_window(dt_ny):
        return None, "NOT_IN_NY_SESSION_WINDOW"

    curr_bar_15m = BarProxy(curr_bar_15m)
    recent_bars_15m = ensure_proxies(recent_bars_15m)
    recent_bars_5m = ensure_proxies(recent_bars_5m)

    # 1. HTF Trend & Alignment
    # H1 and H4 must support same direction; D1 must not be explicitly opposing
    direction = None
    if h4_bias == "BULLISH" and h1_trend == "BULLISH":
        if d_bias != "BEARISH":
            direction = "LONG"
    elif h4_bias == "BEARISH" and h1_trend == "BEARISH":
        if d_bias != "BULLISH":
            direction = "SHORT"

    if not direction:
        return None, "HTF_STRUCTURE_CONFLICT"

    if len(recent_bars_15m) < 25 or len(recent_bars_5m) < 15:
        return None, "INSUFFICIENT_LTF_DATA"

    # 2. 15M Pullback Detection (No Liquidity Sweep Required!)
    atr_15m = compute_atr_bars(recent_bars_15m, 14)
    curr_close = curr_bar_15m["close"]

    # Detect active FVGs or POIs on 15M
    fvgs_15m = smc_engine.detect_fvgs(ensure_proxies(recent_bars_15m[-30:]), "15M")
    desired_fvg_type = "BULLISH_FVG" if direction == "LONG" else "BEARISH_FVG"
    active_fvgs = [f for f in fvgs_15m if f["type"] == desired_fvg_type and not f.get("mitigated", False)]

    # Swings on 15M for structural reference
    sh_15m, sl_15m = smc_engine.identify_pivots(ensure_proxies(recent_bars_15m[-40:]), "15M")
    if not sh_15m or not sl_15m:
        return None, "NO_VALID_SWINGS"

    pullback_confirmed = False
    invalidation_level = 0.0
    target_level = 0.0

    if direction == "LONG":
        recent_highest = max(b["high"] for b in recent_bars_15m[-12:])
        # Pullback: current low has dipped below recent high by at least 0.5 ATR
        if (recent_highest - curr_bar_15m["low"]) >= (0.5 * atr_15m):
            pullback_confirmed = True
        # Or touched an active FVG zone
        if active_fvgs:
            fvg = active_fvgs[-1]
            if curr_bar_15m["low"] <= fvg["top"] and curr_close >= fvg["bottom"]:
                pullback_confirmed = True

        if not pullback_confirmed:
            return None, "B1_NO_15M_PULLBACK"

        # 3. 5M Displacement Trigger
        # Check last 3 5M bars for bullish displacement candle
        last_3_5m = recent_bars_5m[-3:]
        bullish_displacement = any(
            (b["close"] > b["open"]) and ((b["close"] - b["open"]) >= 0.4 * atr_15m)
            for b in last_3_5m
        )
        if not bullish_displacement:
            return None, "B1_NO_5M_DISPLACEMENT_TRIGGER"

        # Structural SL: lowest low of recent pullback - 0.2 ATR buffer
        pullback_low = min(b["low"] for b in recent_bars_15m[-8:])
        invalidation_level = round(pullback_low - max(0.2 * atr_15m, 0.50), 2)

        # Target: nearest pre-existing swing high
        valid_targets = [s["price"] for s in sh_15m if s["price"] > curr_close + 1.0]
        if not valid_targets:
            # Fallback to recent highest
            target_level = round(recent_highest, 2)
        else:
            target_level = round(valid_targets[-1], 2)

        fill_entry = round(curr_close + (0.5 * spread_usd) + costs.slippage_usd, 2)

    else:  # SHORT
        recent_lowest = min(b["low"] for b in recent_bars_15m[-12:])
        if (curr_bar_15m["high"] - recent_lowest) >= (0.5 * atr_15m):
            pullback_confirmed = True
        if active_fvgs:
            fvg = active_fvgs[-1]
            if curr_bar_15m["high"] >= fvg["bottom"] and curr_close <= fvg["top"]:
                pullback_confirmed = True

        if not pullback_confirmed:
            return None, "B1_NO_15M_PULLBACK"

        last_3_5m = recent_bars_5m[-3:]
        bearish_displacement = any(
            (b["open"] > b["close"]) and ((b["open"] - b["close"]) >= 0.4 * atr_15m)
            for b in last_3_5m
        )
        if not bearish_displacement:
            return None, "B1_NO_5M_DISPLACEMENT_TRIGGER"

        pullback_high = max(b["high"] for b in recent_bars_15m[-8:])
        invalidation_level = round(pullback_high + max(0.2 * atr_15m, 0.50), 2)

        valid_targets = [s["price"] for s in sl_15m if s["price"] < curr_close - 1.0]
        if not valid_targets:
            target_level = round(recent_lowest, 2)
        else:
            target_level = round(valid_targets[-1], 2)

        fill_entry = round(curr_close - (0.5 * spread_usd) - costs.slippage_usd, 2)

    # 4. Price Geometry & Net RR check
    is_geom_valid, geom_err = validate_price_geometry(direction, fill_entry, invalidation_level, target_level)
    if not is_geom_valid:
        return None, f"INVALID_GEOMETRY: {geom_err}"

    calc = calculate_risk_reward(
        direction=direction,
        entry=fill_entry,
        sl=invalidation_level,
        tp=target_level,
        capital=capital,
        risk_pct=risk_pct,
        costs=costs,
        entry_has_slippage=True,
        leverage=leverage,
        margin_mode=margin_mode,
        min_net_rr=min_net_rr
    )

    if not calc.can_execute or calc.net_rr < min_net_rr:
        return None, f"NET_RR_TOO_LOW: {calc.net_rr:.2f}R < {min_net_rr:.1f}R"

    setup = {
        "setup_id": f"b1-{sim_time}",
        "strategy_family": "NY_TREND_CONTINUATION",
        "target_model": "TARGET_MODEL_SWING_LIQUIDITY",
        "direction": direction,
        "entry_price": fill_entry,
        "stop_loss": invalidation_level,
        "take_profit": target_level,
        "calc": calc,
        "sim_time": sim_time,
        "dt_ny": dt_ny,
        "entry_type": "QUALITY_ENTRY",
        "rationale": f"NY Continuation: H4={h4_bias}, H1={h1_trend}, 15M pullback confirmed, 5M displacement trigger, Net RR={calc.net_rr:.2f}R"
    }
    return setup, None


def evaluate_setup_b2_range_break_retest(
    curr_bar_5m: Dict[str, Any],
    recent_bars_5m: List[Dict[str, Any]],
    pre_ny_range: Optional[Dict[str, Any]],
    h1_trend: str,
    sim_time: int,
    capital: float,
    risk_pct: float,
    leverage: int,
    margin_mode: str,
    costs: CostAssumptions,
    spread_usd: float,
    min_net_rr: float = 2.0,
    state_tracker: Optional[Dict[str, Any]] = None
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Setup B2: NY_RANGE_BREAK_RETEST
    - Pre-NY range (00:00 - 08:25 NY).
    - Bar closes breaking range in direction supported by H1 trend.
    - Confirmed retest at broken boundary holding structure strictly AFTER breakout bar.
    - Chronology: breakout_at < retest_at <= trigger_at.
    - Disallows breakout and retest from the same bar.
    - Target model: explicit TARGET_MODEL_RANGE_EXTENSION.
    - Net RR >= 2.0.
    """
    dt_ny = get_ny_datetime(sim_time)
    if not is_ny_session_window(dt_ny):
        return None, "NOT_IN_NY_SESSION_WINDOW"

    curr_bar_5m = BarProxy(curr_bar_5m)
    recent_bars_5m = ensure_proxies(recent_bars_5m)

    if not pre_ny_range:
        return None, "NO_PRE_NY_RANGE"

    range_high = pre_ny_range.get("range_high", pre_ny_range.get("high"))
    range_low = pre_ny_range.get("range_low", pre_ny_range.get("low"))
    range_size = pre_ny_range.get("range_size", round(range_high - range_low, 2) if (range_high and range_low) else 0.0)

    if range_size < 3.0 or range_size > 35.0:
        return None, f"PRE_NY_RANGE_SIZE_INVALID: {range_size:.2f} USD"

    # Only consider breakout in direction of H1 trend
    if h1_trend not in ("BULLISH", "BEARISH"):
        return None, "H1_TREND_NOT_CONFIRMED"

    direction = "LONG" if h1_trend == "BULLISH" else "SHORT"
    curr_close = curr_bar_5m["close"]

    # Filter bars since 08:30 NY today
    session_ny_date = dt_ny.strftime("%Y-%m-%d")
    ny_bars = [
        b for b in recent_bars_5m
        if get_ny_datetime(b["timestamp"]).strftime("%Y-%m-%d") == session_ny_date
        and get_ny_datetime(b["timestamp"]).time() >= NY_MORNING_START
    ]

    if len(ny_bars) < 3:
        return None, "TOO_FEW_BARS_SINCE_NY_OPEN"

    atr = compute_atr_bars(recent_bars_5m, 14)

    # State Machine chronology:
    if direction == "LONG":
        breakout_bar = None
        for b in ny_bars:
            if b["close"] > range_high:
                breakout_bar = b
                break

        if not breakout_bar:
            return None, "B2_NO_BREAKOUT_ABOVE_RANGE_HIGH"

        breakout_bar_ts = breakout_bar["timestamp"]
        breakout_at = breakout_bar.close_time

        # Retest bars must be strictly AFTER breakout bar (same bar cannot be both breakout and retest)
        subsequent_bars = [b for b in ny_bars if b["timestamp"] > breakout_bar_ts]
        if not subsequent_bars:
            return None, "B2_WAITING_FOR_RETEST"

        retest_bar = None
        for b in subsequent_bars:
            if abs(b["low"] - range_high) <= (0.6 * atr) and b["close"] >= range_high - (0.2 * atr):
                retest_bar = b
                break

        if not retest_bar:
            return None, "B2_NO_CONFIRMED_RETEST"

        retest_at = retest_bar.close_time

        # Trigger confirmation on current bar: holds boundary with bullish close
        holds_level = (curr_close >= range_high - (0.2 * atr)) and (curr_bar_5m["close"] > curr_bar_5m["open"])
        if not holds_level:
            return None, "B2_NO_CONFIRMED_HOLD_TRIGGER"

        if not (breakout_bar_ts < retest_bar["timestamp"]):
            return None, "B2_INVALID_CHRONOLOGY"

        invalidation_level = round(range_high - max(0.5 * atr, 1.0), 2)
        target_level = round(range_high + max(range_size, 1.5 * atr), 2)
        fill_entry = round(curr_close + (0.5 * spread_usd) + costs.slippage_usd, 2)

    else:  # SHORT
        breakout_bar = None
        for b in ny_bars:
            if b["close"] < range_low:
                breakout_bar = b
                break

        if not breakout_bar:
            return None, "B2_NO_BREAKOUT_BELOW_RANGE_LOW"

        breakout_bar_ts = breakout_bar["timestamp"]
        breakout_at = breakout_bar.close_time

        subsequent_bars = [b for b in ny_bars if b["timestamp"] > breakout_bar_ts]
        if not subsequent_bars:
            return None, "B2_WAITING_FOR_RETEST"

        retest_bar = None
        for b in subsequent_bars:
            if abs(b["high"] - range_low) <= (0.6 * atr) and b["close"] <= range_low + (0.2 * atr):
                retest_bar = b
                break

        if not retest_bar:
            return None, "B2_NO_CONFIRMED_RETEST"

        retest_at = retest_bar.close_time

        holds_level = (curr_close <= range_low + (0.2 * atr)) and (curr_bar_5m["close"] < curr_bar_5m["open"])
        if not holds_level:
            return None, "B2_NO_CONFIRMED_HOLD_TRIGGER"

        if not (breakout_bar_ts < retest_bar["timestamp"]):
            return None, "B2_INVALID_CHRONOLOGY"

        invalidation_level = round(range_low + max(0.5 * atr, 1.0), 2)
        target_level = round(range_low - max(range_size, 1.5 * atr), 2)
        fill_entry = round(curr_close - (0.5 * spread_usd) - costs.slippage_usd, 2)

    is_geom_valid, geom_err = validate_price_geometry(direction, fill_entry, invalidation_level, target_level)
    if not is_geom_valid:
        return None, f"INVALID_GEOMETRY: {geom_err}"

    calc = calculate_risk_reward(
        direction=direction,
        entry=fill_entry,
        sl=invalidation_level,
        tp=target_level,
        capital=capital,
        risk_pct=risk_pct,
        costs=costs,
        entry_has_slippage=True,
        leverage=leverage,
        margin_mode=margin_mode,
        min_net_rr=min_net_rr
    )

    if not calc.can_execute or calc.net_rr < min_net_rr:
        return None, f"NET_RR_TOO_LOW: {calc.net_rr:.2f}R < {min_net_rr:.1f}R"

    setup = {
        "setup_id": f"b2-{sim_time}",
        "strategy_family": "NY_RANGE_BREAK_RETEST",
        "target_model": "TARGET_MODEL_RANGE_EXTENSION",
        "direction": direction,
        "entry_price": fill_entry,
        "stop_loss": invalidation_level,
        "take_profit": target_level,
        "breakout_at": breakout_at,
        "retest_at": retest_at,
        "trigger_at": sim_time,
        "calc": calc,
        "sim_time": sim_time,
        "dt_ny": dt_ny,
        "entry_type": "QUALITY_ENTRY",
        "rationale": f"NY Break-Retest: Range [{range_low:.2f} - {range_high:.2f}], H1={h1_trend}, Breakout@{breakout_at}, Retest@{retest_at}, Net RR={calc.net_rr:.2f}R"
    }
    return setup, None


def evaluate_mode_c_quota_candidate(
    curr_bar_5m: Dict[str, Any],
    recent_bars_5m: List[Dict[str, Any]],
    recent_bars_15m: List[Dict[str, Any]],
    d_bias: str,
    h4_bias: str,
    h1_trend: str,
    sim_time: int,
    capital: float,
    quota_risk_pct: float,
    leverage: int,
    margin_mode: str,
    costs: CostAssumptions,
    spread_usd: float,
    min_net_rr: float = 2.0,
    deadline_hour: int = 14,
    deadline_min: int = 30
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Mode C Quota Candidate:
    - Activated at NY deadline if 0 NY fills today.
    - Uses confirmed aligned H1/H4 directional trend (blocks on trend conflict!).
    - Selects best 5M structural trigger candidate with existing swing target (no artificial TP expansion!).
    - Sizing: quota entry risk = 0.10% equity (strict).
    - Enforces ALL hard guards (Net RR >= 2.0, geometry valid, stop/target evidence).
    - Returns candidate labeled 'QUOTA_ENTRY'.
    """
    dt_ny = get_ny_datetime(sim_time)
    if not is_ny_deadline_reached(dt_ny, deadline_hour=deadline_hour, deadline_min=deadline_min):
        return None, "NOT_AT_NY_DEADLINE"

    curr_bar_5m = BarProxy(curr_bar_5m)
    recent_bars_5m = ensure_proxies(recent_bars_5m)
    recent_bars_15m = ensure_proxies(recent_bars_15m)

    # Confirmed trend direction: H1 and H4 must NOT conflict!
    direction = None
    if h1_trend == "BULLISH" and h4_bias in ("BULLISH", "UNKNOWN"):
        direction = "LONG"
    elif h1_trend == "BEARISH" and h4_bias in ("BEARISH", "UNKNOWN"):
        direction = "SHORT"
    elif h1_trend in ("BULLISH", "BEARISH") and h4_bias in ("BULLISH", "BEARISH") and h1_trend != h4_bias:
        return None, "H1_H4_TREND_CONFLICT"
    else:
        return None, "TREND_UNKNOWN_CONFLICT"

    if len(recent_bars_5m) < 15:
        return None, "INSUFFICIENT_5M_DATA"

    atr = compute_atr_bars(recent_bars_5m, 14)
    curr_close = curr_bar_5m["close"]

    # Recent swings on 5M
    sh_5m, sl_5m = smc_engine.identify_pivots(ensure_proxies(recent_bars_5m[-30:]), "5M")
    if not sh_5m or not sl_5m:
        return None, "NO_VALID_SWINGS"

    if direction == "LONG":
        fill_entry = round(curr_close + (0.5 * spread_usd) + costs.slippage_usd, 2)
        # Structural SL at recent 5M swing low - 0.2 ATR
        sl_cand = round(sl_5m[-1]["price"] - max(0.2 * atr, 0.50), 2)
        # Target at recent 5M swing high
        tp_cand = round(sh_5m[-1]["price"], 2)
        # Strictly require valid existing target (never artificially stretch TP to force RR!)
        if tp_cand <= fill_entry + 0.5:
            return None, "NO_VALID_SWING_TARGET"
    else:
        fill_entry = round(curr_close - (0.5 * spread_usd) - costs.slippage_usd, 2)
        sl_cand = round(sh_5m[-1]["price"] + max(0.2 * atr, 0.50), 2)
        tp_cand = round(sl_5m[-1]["price"], 2)
        if tp_cand >= fill_entry - 0.5:
            return None, "NO_VALID_SWING_TARGET"

    is_geom_valid, geom_err = validate_price_geometry(direction, fill_entry, sl_cand, tp_cand)
    if not is_geom_valid:
        return None, f"INVALID_GEOMETRY: {geom_err}"

    calc = calculate_risk_reward(
        direction=direction,
        entry=fill_entry,
        sl=sl_cand,
        tp=tp_cand,
        capital=capital,
        risk_pct=quota_risk_pct,
        costs=costs,
        entry_has_slippage=True,
        leverage=leverage,
        margin_mode=margin_mode,
        min_net_rr=min_net_rr
    )

    if not calc.can_execute or calc.net_rr < min_net_rr:
        return None, f"NET_RR_TOO_LOW: {calc.net_rr:.2f}R < {min_net_rr:.1f}R"

    # Deterministic weighted ranking score:
    # HTF align (30), structure (25), target distance (20), spread (15), volatility (10)
    score = 30.0 if (h4_bias == h1_trend) else 20.0
    score += 25.0 if calc.net_rr >= 2.5 else 15.0
    score += min(20.0, 5.0 * calc.net_rr)
    score += 15.0 if spread_usd <= 0.25 else 5.0
    score += 10.0 if atr >= 1.0 else 5.0

    candidate = {
        "setup_id": f"quota-{sim_time}",
        "strategy_family": "NY_QUOTA_CANDIDATE",
        "target_model": "TARGET_MODEL_SWING_LIQUIDITY",
        "direction": direction,
        "entry_price": fill_entry,
        "stop_loss": sl_cand,
        "take_profit": tp_cand,
        "calc": calc,
        "sim_time": sim_time,
        "dt_ny": dt_ny,
        "entry_type": "QUOTA_ENTRY",
        "ranking_score": round(score, 1),
        "rationale": f"NY Quota Candidate @ {dt_ny.strftime('%H:%M')} deadline: Score={score:.1f}, Risk={quota_risk_pct:.2f}%, Net RR={calc.net_rr:.2f}R, H1={h1_trend}"
    }
    return candidate, None
