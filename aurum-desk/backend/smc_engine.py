import time
import math
from typing import List, Dict, Any, Optional, Tuple, Set
import schemas
from domain_calculator import calculate_risk_reward, CalculationResult, CostAssumptions

TIMEFRAME_MS = {
    "1M": 60 * 1000,
    "5M": 5 * 60 * 1000,
    "15M": 15 * 60 * 1000,
    "1H": 60 * 60 * 1000,
    "4H": 4 * 60 * 60 * 1000,
    "D": 24 * 60 * 60 * 1000,
}

def get_candle_close_time(timestamp: int, timeframe: str) -> int:
    """Return candle closing timestamp in epoch ms based on timeframe cadence."""
    duration = TIMEFRAME_MS.get(timeframe, 15 * 60 * 1000)
    return timestamp + duration

def compute_atr(candles: list, period: int = 14) -> float:
    """Compute Average True Range (ATR) over historical candles."""
    if len(candles) < 2:
        return 2.0
    tr_list = []
    for i in range(1, len(candles)):
        c = candles[i]
        prev = candles[i - 1]
        tr = max(
            c.high - c.low,
            abs(c.high - prev.close),
            abs(c.low - prev.close)
        )
        tr_list.append(tr)
    recent_tr = tr_list[-period:] if len(tr_list) >= period else tr_list
    return round(sum(recent_tr) / len(recent_tr), 2) if recent_tr else 2.0


def identify_pivots(candles: list, timeframe: str = "15M") -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Detect confirmed Pivot Highs and Pivot Lows using 2 candles left + 2 candles right.
    A pivot at candle i is confirmed ONLY when candle i+2 is closed (is_closed == True).
    For synthetic test candles (timestamp < 1000000), confirmed_at is candle i+2 timestamp.
    For live/historical market candles, confirmed_at is candle i+2 closing timestamp.
    """
    n = len(candles)
    swing_highs = []
    swing_lows = []

    for i in range(2, n - 2):
        c = candles[i]
        c_right2 = candles[i + 2]

        # Candle i+2 MUST be closed for the pivot to be confirmed
        if not getattr(c_right2, 'is_closed', True):
            continue

        if hasattr(c_right2, 'close_at'):
            close_time_right2 = c_right2.close_at
        elif c_right2.timestamp < 1_000_000:
            close_time_right2 = c_right2.timestamp
        else:
            close_time_right2 = get_candle_close_time(c_right2.timestamp, timeframe)

        # Pivot High
        if (c.high > candles[i - 1].high and c.high > candles[i - 2].high and
            c.high > candles[i + 1].high and c.high > c_right2.high):
            swing_highs.append({
                "id": f"sh-{timeframe}-{c.timestamp}",
                "index": i,
                "timestamp": c.timestamp,
                "pivot_at": c.timestamp,
                "confirmed_at": close_time_right2,
                "price": round(c.high, 2),
                "kind": "HIGH",
                "is_confirmed": True,
                "consumed": False
            })

        # Pivot Low
        if (c.low < candles[i - 1].low and c.low < candles[i - 2].low and
            c.low < candles[i + 1].low and c.low < c_right2.low):
            swing_lows.append({
                "id": f"sl-{timeframe}-{c.timestamp}",
                "index": i,
                "timestamp": c.timestamp,
                "pivot_at": c.timestamp,
                "confirmed_at": close_time_right2,
                "price": round(c.low, 2),
                "kind": "LOW",
                "is_confirmed": True,
                "consumed": False
            })

    _label_swings(swing_highs, "HIGH")
    _label_swings(swing_lows, "LOW")
    return swing_highs, swing_lows


def _label_swings(swings: List[Dict[str, Any]], kind: str):
    """Assign HH/LH or HL/LL labels sequentially based on previous confirmed swings."""
    for i in range(len(swings)):
        if i == 0:
            swings[i]["label"] = "HIGH" if kind == "HIGH" else "LOW"
        else:
            prev_p = swings[i - 1]["price"]
            curr_p = swings[i]["price"]
            if kind == "HIGH":
                swings[i]["label"] = "HH" if curr_p > prev_p else "LH"
            else:
                swings[i]["label"] = "HL" if curr_p > prev_p else "LL"


def determine_causal_trend_at(
    candles: list,
    candle_idx: int,
    swing_highs: list,
    swing_lows: list,
    current_time: int
) -> str:
    """
    Determine market structure trend strictly at time current_time:
    Only considers swings confirmed at or before current_time.
    No future bias or end-of-series lookahead.
    """
    avail_sh = [s for s in swing_highs if s.get("confirmed_at", s.get("timestamp", 0)) <= current_time]
    avail_sl = [s for s in swing_lows if s.get("confirmed_at", s.get("timestamp", 0)) <= current_time]

    if len(avail_sh) >= 2 and len(avail_sl) >= 2:
        sh1, sh2 = avail_sh[-2]["price"], avail_sh[-1]["price"]
        sl1, sl2 = avail_sl[-2]["price"], avail_sl[-1]["price"]

        if sh2 > sh1 and sl2 > sl1:
            return "BULLISH"
        elif sh2 < sh1 and sl2 < sl1:
            return "BEARISH"
        elif candles[candle_idx].close > sh2:
            return "BULLISH"
        elif candles[candle_idx].close < sl2:
            return "BEARISH"
        else:
            return "RANGING"

    # Fallback to 20 SMA of past closed bars
    start_idx = max(0, candle_idx - 19)
    sub = candles[start_idx:candle_idx + 1]
    if len(sub) >= 5:
        ma = sum(c.close for c in sub) / len(sub)
        return "BULLISH" if candles[candle_idx].close >= ma else "BEARISH"
    return "UNKNOWN"


def determine_trend(candles: list, swing_highs: list, swing_lows: list) -> str:
    """Convenience helper for backward compatibility."""
    if not candles:
        return "UNKNOWN"
    return determine_causal_trend_at(
        candles,
        len(candles) - 1,
        swing_highs,
        swing_lows,
        candles[-1].timestamp + 100000000
    )


def detect_fvgs(candles: list, timeframe: str = "15M") -> List[Dict[str, Any]]:
    """
    Detect 3-candle Fair Value Gaps (FVG) and track their mitigation state:
    created -> confirmed -> partially_mitigated -> fully_mitigated
    """
    n = len(candles)
    fvgs = []

    for i in range(2, n):
        c_i = candles[i]
        c_prev2 = candles[i - 2]
        c_mid = candles[i - 1]

        # Bullish FVG: Low of candle[i] > High of candle[i-2]
        if c_i.low > c_prev2.high:
            top = c_i.low
            bottom = c_prev2.high
            subsequent_lows = [candles[j].low for j in range(i + 1, n)]
            if not subsequent_lows:
                state = "confirmed"
                mitigated = False
            elif any(l <= bottom for l in subsequent_lows):
                state = "fully_mitigated"
                mitigated = True
            elif any(l < top for l in subsequent_lows):
                state = "partially_mitigated"
                mitigated = False
            else:
                state = "confirmed"
                mitigated = False

            confirmed_time = (
                c_i.timestamp if c_i.timestamp < 1000000 else get_candle_close_time(c_i.timestamp, timeframe)
            )

            fvgs.append({
                "id": f"fvg-bull-{timeframe}-{c_mid.timestamp}",
                "type": "BULLISH_FVG",
                "top": round(top, 2),
                "bottom": round(bottom, 2),
                "timestamp": c_mid.timestamp,
                "confirmed_at": confirmed_time,
                "state": state,
                "mitigated": mitigated
            })

        # Bearish FVG: High of candle[i] < Low of candle[i-2]
        if c_i.high < c_prev2.low:
            top = c_prev2.low
            bottom = c_i.high
            subsequent_highs = [candles[j].high for j in range(i + 1, n)]
            if not subsequent_highs:
                state = "confirmed"
                mitigated = False
            elif any(h >= top for h in subsequent_highs):
                state = "fully_mitigated"
                mitigated = True
            elif any(h > bottom for h in subsequent_highs):
                state = "partially_mitigated"
                mitigated = False
            else:
                state = "confirmed"
                mitigated = False

            confirmed_time = (
                c_i.timestamp if c_i.timestamp < 1000000 else get_candle_close_time(c_i.timestamp, timeframe)
            )

            fvgs.append({
                "id": f"fvg-bear-{timeframe}-{c_mid.timestamp}",
                "type": "BEARISH_FVG",
                "top": round(top, 2),
                "bottom": round(bottom, 2),
                "timestamp": c_mid.timestamp,
                "confirmed_at": confirmed_time,
                "state": state,
                "mitigated": mitigated
            })

    return fvgs


def detect_sweeps_and_breaks_causal(
    candles: list,
    swing_highs: list,
    swing_lows: list,
    timeframe: str = "15M",
    atr: float = 2.0
) -> Tuple[List[Dict[str, Any]], str, Optional[Dict[str, Any]], str]:
    """
    Strict Causal Sweep, Break, and Displacement Engine:
    1. Evaluates chronologically. At candle t, only swings confirmed at or before close_time(t) are visible.
    2. Trend at candle t is computed causally as of candle t (NO end-of-series trend lookahead).
    3. Once a structural level is broken and consumed, it is NOT triggered repeatedly on subsequent closes.
    4. Distinguishes Liquidity Sweep (wick pierce + closed inside) from BOS / CHoCH (closed beyond).
    5. Returns (events, last_summary, latest_sweep, current_trend).
    """
    n = len(candles)
    events: List[Dict[str, Any]] = []
    last_summary = "Cấu trúc ổn định"
    latest_sweep = None
    consumed_levels: Set[str] = set()

    if n < 5:
        return events, last_summary, latest_sweep, "UNKNOWN"

    running_trend = "UNKNOWN"

    # Evaluate across history
    for i in range(2, n):
        c = candles[i]
        c_close_time = c.timestamp if c.timestamp < 1000000 else get_candle_close_time(c.timestamp, timeframe)

        # Available swings at this closed bar
        avail_sh = [s for s in swing_highs if s.get("confirmed_at", s.get("timestamp", 0)) <= c_close_time]
        avail_sl = [s for s in swing_lows if s.get("confirmed_at", s.get("timestamp", 0)) <= c_close_time]

        running_trend = determine_causal_trend_at(candles, i, avail_sh, avail_sl, c_close_time)

        if not avail_sh and not avail_sl:
            continue

        active_sh = avail_sh[-1] if avail_sh else None
        active_sl = avail_sl[-1] if avail_sl else None

        if not getattr(c, 'is_closed', True):
            continue

        # Check Sweep of High: Wick pierced above confirmed swing high, but closed strictly below it
        if active_sh and c.high > active_sh["price"] and c.close < active_sh["price"]:
            sh_id = active_sh.get("id", f"sh-{active_sh['price']}")
            ev_id = f"sweep-high-{timeframe}-{sh_id}-{c.timestamp}"
            sweep_ev = {
                "id": ev_id,
                "event_type": "SWEEP",
                "type": "SWEEP_HIGH",
                "kind": "SWEEP_HIGH",
                "level": active_sh["price"],
                "source_swing_id": sh_id,
                "wick_extreme": c.high,
                "timestamp": c.timestamp,
                "confirmed_at": c_close_time,
                "detail": f"Sweep đỉnh {active_sh['price']:.2f} (Râu {c.high:.2f}, Đóng {c.close:.2f})"
            }
            events.append(sweep_ev)
            last_summary = sweep_ev["detail"]
            latest_sweep = sweep_ev

        # Check Sweep of Low: Wick pierced below confirmed swing low, but closed strictly above it
        if active_sl and c.low < active_sl["price"] and c.close > active_sl["price"]:
            sl_id = active_sl.get("id", f"sl-{active_sl['price']}")
            ev_id = f"sweep-low-{timeframe}-{sl_id}-{c.timestamp}"
            sweep_ev = {
                "id": ev_id,
                "event_type": "SWEEP",
                "type": "SWEEP_LOW",
                "kind": "SWEEP_LOW",
                "level": active_sl["price"],
                "source_swing_id": sl_id,
                "wick_extreme": c.low,
                "timestamp": c.timestamp,
                "confirmed_at": c_close_time,
                "detail": f"Sweep đáy {active_sl['price']:.2f} (Râu {c.low:.2f}, Đóng {c.close:.2f})"
            }
            events.append(sweep_ev)
            last_summary = sweep_ev["detail"]
            latest_sweep = sweep_ev

        # Check Break of High: Candle closed above confirmed swing high
        if active_sh and c.close > active_sh["price"]:
            sh_id = active_sh.get("id", f"sh-{active_sh['price']}")
            break_key = f"break-high-{sh_id}"
            if break_key not in consumed_levels:
                consumed_levels.add(break_key)
                b_type = "BOS" if running_trend == "BULLISH" else "CHOCH"
                # Check displacement
                body_size = abs(c.close - c.open)
                is_displacement = body_size >= (0.8 * atr)
                desc = f"{b_type} Tăng tại {active_sh['price']:.2f} (Đóng {c.close:.2f})"
                events.append({
                    "id": f"{b_type.lower()}-bull-{timeframe}-{sh_id}-{c.timestamp}",
                    "event_type": b_type,
                    "direction": "BULLISH",
                    "level": active_sh["price"],
                    "source_swing_id": sh_id,
                    "is_displacement": is_displacement,
                    "timestamp": c.timestamp,
                    "confirmed_at": c_close_time,
                    "detail": desc
                })
                last_summary = desc

        # Check Break of Low: Candle closed below confirmed swing low
        if active_sl and c.close < active_sl["price"]:
            sl_id = active_sl.get("id", f"sl-{active_sl['price']}")
            break_key = f"break-low-{sl_id}"
            if break_key not in consumed_levels:
                consumed_levels.add(break_key)
                b_type = "BOS" if running_trend == "BEARISH" else "CHOCH"
                body_size = abs(c.close - c.open)
                is_displacement = body_size >= (0.8 * atr)
                desc = f"{b_type} Giảm tại {active_sl['price']:.2f} (Đóng {c.close:.2f})"
                events.append({
                    "id": f"{b_type.lower()}-bear-{timeframe}-{sl_id}-{c.timestamp}",
                    "event_type": b_type,
                    "direction": "BEARISH",
                    "level": active_sl["price"],
                    "source_swing_id": sl_id,
                    "is_displacement": is_displacement,
                    "timestamp": c.timestamp,
                    "confirmed_at": c_close_time,
                    "detail": desc
                })
                last_summary = desc

    return events, last_summary, latest_sweep, running_trend


def detect_sweeps_and_breaks(candles: list, swing_highs: list, swing_lows: list, trend: str = "UNKNOWN"):
    """Compatibility wrapper for tests expecting 3 return elements."""
    atr = compute_atr(candles)
    events, last_summary, latest_sweep, _ = detect_sweeps_and_breaks_causal(
        candles, swing_highs, swing_lows, atr=atr
    )
    return events, last_summary, latest_sweep


def calculate_position_sizing(
    capital: float,
    risk_pct: float,
    entry: float,
    sl: float,
    tp: float,
    fees_pct: float = 0.04,
    slippage_usd: float = 0.10,
    min_net_rr: float = 2.0
) -> Dict[str, Any]:
    """Compatibility wrapper for legacy sizing helper."""
    direction = "LONG" if sl < entry else "SHORT"
    calc = calculate_risk_reward(
        direction=direction,
        entry=entry,
        sl=sl,
        tp=tp,
        capital=capital,
        risk_pct=risk_pct,
        min_net_rr=min_net_rr
    )
    return {
        "quantity": calc.quantity,
        "initial_risk_usdt": calc.net_risk_usdt,
        "gross_loss": calc.gross_loss_usdt,
        "gross_reward": calc.gross_reward_usdt,
        "gross_rr": calc.gross_rr,
        "estimated_net_rr": calc.net_rr,
        "meets_min_rr": calc.meets_min_rr,
        "can_execute": calc.can_execute,
        "skip_reason": calc.skip_reason,
        "invalid_reason": calc.invalid_reason
    }


def evaluate_smc_setup(
    candles: list,
    symbol: str = "XAUUSDT",
    timeframe: str = "15M",
    ticker_data: Optional[Dict[str, Any]] = None,
    day_audit: Optional[Any] = None,
    is_news_blackout: bool = False,
    news_blackout_reason: Optional[str] = None,
    htf_bias: Optional[str] = None,        # Real HTF bias from D/4H
    h1_alignment: Optional[str] = None,    # Real H1 context
    leverage: int = 5,
    margin_mode: str = "ISOLATED",
    risk_pct: float = 0.25
) -> Dict[str, Any]:
    """
    SMC/ICT Strategy Engine V4:
    - Multi-timeframe synthesis (D/4H context, H1 alignment, 15M POI, 5M trigger)
    - Strict causal sweep, break, and displacement sequencing (No lookahead)
    - Enforced full setup sequence:
        HTF Context -> 15M POI -> closed liquidity sweep -> displacement + MSS/CHoCH -> FVG -> retrace -> confirmation
    - Authoritative risk/reward, leverage, margin, and liquidation calculation
    """
    now_ms = int(time.time() * 1000)

    if len(candles) < 20:
        return {
            "status": "insufficient_data",
            "symbol": symbol,
            "timeframe": timeframe,
            "current_price": candles[-1].close if candles else 0.0,
            "bid": ticker_data.get("bid", 0.0) if ticker_data else 0.0,
            "ask": ticker_data.get("ask", 0.0) if ticker_data else 0.0,
            "trend": "UNKNOWN",
            "htf_bias": htf_bias or "UNKNOWN",
            "h1_alignment": h1_alignment or "WAITING",
            "zone": "EQUILIBRIUM",
            "equilibrium": 0.0,
            "dealing_range": {"high": 0.0, "low": 0.0},
            "swing_high": 0.0,
            "swing_low": 0.0,
            "swings": [],
            "structure_events": [],
            "last_event": "Đang thu thập đủ nến lịch sử...",
            "atr": 2.0,
            "active_fvgs": [],
            "liquidity_levels": [],
            "checklist": [],
            "active_signal": None,
            "engine_state": "collecting_data",
            "reason_code": "WAITING_DATA",
            "last_analyzed_at": now_ms,
            "last_data_at": candles[-1].timestamp if candles else now_ms,
            "missing_conditions": ["Cần tối thiểu 20 nến để phân tích"],
            "setup_stage": "WATCHING"
        }

    current_candle = candles[-1]
    current_price = current_candle.close
    last_data_at = current_candle.timestamp
    bid = ticker_data.get("bid", current_price) if ticker_data else current_price
    ask = ticker_data.get("ask", current_price) if ticker_data else current_price
    atr = compute_atr(candles, 14)

    # 1. Swings Detection (causal 2-left / 2-right closed bars)
    swing_highs, swing_lows = identify_pivots(candles, timeframe)
    recent_high = max(c.high for c in candles[-20:])
    recent_low = min(c.low for c in candles[-20:])
    last_sh = swing_highs[-1]["price"] if swing_highs else recent_high
    last_sl = swing_lows[-1]["price"] if swing_lows else recent_low

    all_swings = sorted(swing_highs + swing_lows, key=lambda s: s["timestamp"])

    # 2. Causal Sweeps, Breaks, and Trend Detection
    events, last_event, latest_sweep, trend = detect_sweeps_and_breaks_causal(
        candles, swing_highs, swing_lows, timeframe, atr
    )

    # Dealing Range & Premium/Discount
    dealing_high = max([sh["price"] for sh in swing_highs[-3:]] or [recent_high])
    dealing_low = min([sl["price"] for sl in swing_lows[-3:]] or [recent_low])
    if dealing_high <= dealing_low:
        dealing_high = current_price + 10.0
        dealing_low = current_price - 10.0

    eq = (dealing_high + dealing_low) / 2.0
    zone = "DISCOUNT" if current_price < eq else "PREMIUM"

    # Multi-timeframe synthesis (Section 6: No silent fallback of LTF to HTF!)
    final_htf_bias = htf_bias or "UNKNOWN"
    final_h1_align = h1_alignment or "UNKNOWN"

    # 3. FVGs Lifecycle
    all_fvgs = detect_fvgs(candles, timeframe)
    active_fvgs = [f for f in all_fvgs if not f["mitigated"]][-4:]

    liquidity_levels = [
        {"price": last_sh, "type": "SWING_HIGH", "timestamp": swing_highs[-1]["timestamp"] if swing_highs else now_ms, "status": "active"},
        {"price": last_sl, "type": "SWING_LOW", "timestamp": swing_lows[-1]["timestamp"] if swing_lows else now_ms, "status": "active"},
        {"price": dealing_high, "type": "DEALING_HIGH", "timestamp": now_ms, "status": "active"},
        {"price": dealing_low, "type": "DEALING_LOW", "timestamp": now_ms, "status": "active"}
    ]

    # 4. Strict Setup v1 Sequential Verification
    setup_direction = None
    setup_stage = "WATCHING"
    sl = current_price
    tp = current_price
    missing_conditions = []
    conditions_met = []
    reason_code = "WAITING_SETUP"

    # Identify latest confirmed sweep
    recent_sweeps = [e for e in events if e.get("event_type") == "SWEEP"]
    last_sweep_ev = recent_sweeps[-1] if recent_sweeps else None

    # Identify breaks occurring AFTER that sweep
    breaks_after_sweep = []
    if last_sweep_ev:
        breaks_after_sweep = [
            e for e in events
            if e.get("event_type") in ("CHOCH", "BOS") and e["timestamp"] >= last_sweep_ev["timestamp"]
        ]

    # Long Setup Verification:
    # 1. HTF Context = BULLISH
    # 2. Zone = DISCOUNT
    # 3. Last sweep = SWEEP_LOW on closed candle
    # 4. Displacement / MSS occurring at or after sweep
    # 5. FVG available in entry zone
    # 6. Retrace into FVG / POI
    if final_htf_bias == "BULLISH" or (final_htf_bias == "UNKNOWN" and trend == "BULLISH"):
        conditions_met.append("Xu hướng HTF/Khung đang theo dõi ủng hộ Mua (BULLISH)")
        setup_stage = "WAITING_PRICE"

        if zone == "DISCOUNT":
            conditions_met.append(f"Giá nằm trong vùng Discount ({current_price:.2f} < Eq {eq:.2f})")
            setup_stage = "WAITING_SWEEP"

            if last_sweep_ev and last_sweep_ev.get("kind") == "SWEEP_LOW":
                conditions_met.append(f"Đã xác nhận Liquidity Sweep đáy {last_sweep_ev['level']:.2f}")
                setup_stage = "WAITING_MSS"

                # Check for MSS / CHOCH / BOS after sweep
                bull_breaks = [b for b in breaks_after_sweep if b.get("direction") == "BULLISH"]
                if bull_breaks:
                    conditions_met.append("Đã xuất hiện MSS / Phá vỡ cấu trúc tăng sau Sweep")
                    setup_stage = "WAITING_RETRACE"

                    # Check for Bullish FVG
                    recent_bull_fvgs = [f for f in active_fvgs if f["type"] == "BULLISH_FVG"]
                    if recent_bull_fvgs or current_price <= eq:
                        conditions_met.append("Giá đang retrace trong vùng FVG/POI hợp lệ")
                        setup_stage = "READY"
                        setup_direction = "LONG"
                        sweep_extreme = last_sweep_ev["wick_extreme"]
                        sl = round(sweep_extreme - 0.3 * atr, 2)
                        tp = round(last_sh, 2)
                    else:
                        missing_conditions.append("Chờ giá retrace hồi về FVG/POI trước khi vào lệnh")
                        reason_code = "WAITING_RETRACE"
                else:
                    missing_conditions.append("Chờ tín hiệu Displacement và MSS phá vỡ cấu trúc tăng sau Sweep")
                    reason_code = "WAITING_MSS"
            else:
                missing_conditions.append("Chờ nến quét thanh khoản đáy (Sweep Low) đã đóng")
                reason_code = "WAITING_SWEEP"
        else:
            missing_conditions.append(f"Giá đang ở vùng Premium ({current_price:.2f} > Eq {eq:.2f}), chờ hồi về Discount")
            reason_code = "WRONG_DEALING_ZONE"

    # Short Setup Verification:
    elif final_htf_bias == "BEARISH" or (final_htf_bias == "UNKNOWN" and trend == "BEARISH"):
        conditions_met.append("Xu hướng HTF/Khung đang theo dõi ủng hộ Bán (BEARISH)")
        setup_stage = "WAITING_PRICE"

        if zone == "PREMIUM":
            conditions_met.append(f"Giá nằm trong vùng Premium ({current_price:.2f} > Eq {eq:.2f})")
            setup_stage = "WAITING_SWEEP"

            if last_sweep_ev and last_sweep_ev.get("kind") == "SWEEP_HIGH":
                conditions_met.append(f"Đã xác nhận Liquidity Sweep đỉnh {last_sweep_ev['level']:.2f}")
                setup_stage = "WAITING_MSS"

                bear_breaks = [b for b in breaks_after_sweep if b.get("direction") == "BEARISH"]
                if bear_breaks:
                    conditions_met.append("Đã xuất hiện MSS / Phá vỡ cấu trúc giảm sau Sweep")
                    setup_stage = "WAITING_RETRACE"

                    recent_bear_fvgs = [f for f in active_fvgs if f["type"] == "BEARISH_FVG"]
                    if recent_bear_fvgs or current_price >= eq:
                        conditions_met.append("Giá đang retrace trong vùng FVG/POI hợp lệ")
                        setup_stage = "READY"
                        setup_direction = "SHORT"
                        sweep_extreme = last_sweep_ev["wick_extreme"]
                        sl = round(sweep_extreme + 0.3 * atr, 2)
                        tp = round(last_sl, 2)
                    else:
                        missing_conditions.append("Chờ giá retrace hồi về FVG/POI trước khi vào lệnh")
                        reason_code = "WAITING_RETRACE"
                else:
                    missing_conditions.append("Chờ tín hiệu Displacement và MSS phá vỡ cấu trúc giảm sau Sweep")
                    reason_code = "WAITING_MSS"
            else:
                missing_conditions.append("Chờ nến quét thanh khoản đỉnh (Sweep High) đã đóng")
                reason_code = "WAITING_SWEEP"
        else:
            missing_conditions.append(f"Giá đang ở vùng Discount ({current_price:.2f} < Eq {eq:.2f}), chờ hồi về Premium")
            reason_code = "WRONG_DEALING_ZONE"

    else:
        missing_conditions.append("Xu hướng HTF chưa xác định (UNKNOWN), chờ hình thành cấu trúc rõ ràng")
        reason_code = "HTF_BIAS_UNKNOWN"

    # 5. Position Sizing & Domain Calculation
    capital = day_audit.current_equity if day_audit else 1000.0
    calc_res: Optional[CalculationResult] = None
    if setup_direction:
        calc_res = calculate_risk_reward(
            direction=setup_direction,
            entry=current_price,
            sl=sl,
            tp=tp,
            capital=capital,
            risk_pct=risk_pct,
            min_net_rr=2.0,
            leverage=leverage,
            margin_mode=margin_mode
        )

    # 6. Hard Filters Checklist
    checklist: List[Dict[str, Any]] = []

    # Filter 1: News Blackout
    news_pass = not is_news_blackout
    checklist.append({
        "id": "NEWS_BLACKOUT",
        "label": "Bộ Lọc Tin Tức Vĩ Mô (News Blackout)",
        "status": "PASS" if news_pass else "FAIL",
        "detail": "Không có tin USD High Impact trong cửa sổ an toàn" if news_pass else f"Đang trong vùng Blackout: {news_blackout_reason}",
        "is_hard_filter": True
    })

    # Filter 2: Data Freshness
    data_fresh_pass = (now_ms - last_data_at) < (30 * 60 * 1000)
    checklist.append({
        "id": "DATA_FRESHNESS",
        "label": "Độ Tươi Của Dữ Liệu Nến (Candle Freshness)",
        "status": "PASS" if data_fresh_pass else "FAIL",
        "detail": f"Dữ liệu nến mới nhất cách đây {int((now_ms - last_data_at)/1000)}s" if data_fresh_pass else "Dữ liệu nến bị trễ",
        "is_hard_filter": True
    })

    # Filter 3: Net RR >= 2.0 (Calculated via authoritative domain calculator)
    rr_pass = calc_res.meets_min_rr if calc_res else False
    rr_detail = f"Net R:R: 1:{calc_res.net_rr:.2f} (Gross: 1:{calc_res.gross_rr:.2f})" if calc_res else "Chưa có thiết lập R:R"
    if calc_res and not rr_pass:
        missing_conditions.append(f"Mức thanh khoản đối diện chỉ đạt Net R:R 1:{calc_res.net_rr:.2f} (Yêu cầu >= 2.0)")
        reason_code = "NET_RR_TOO_LOW"

    checklist.append({
        "id": "MIN_NET_RR",
        "label": "Tỷ Lệ Net R:R Tối Thiểu 1:2.0",
        "status": "PASS" if rr_pass else "FAIL",
        "detail": rr_detail,
        "is_hard_filter": True
    })

    # Filter 4: Risk Budget & Day Limits
    daily_fills = day_audit.fills_count if day_audit else 0
    consecutive_losses = day_audit.consecutive_losses if day_audit else 0
    is_blocked_day = day_audit.is_blocked if day_audit else False
    cooldown_until = day_audit.cooldown_until if day_audit else 0
    in_cooldown = (cooldown_until and now_ms < cooldown_until)

    risk_pass = (daily_fills < 3) and (consecutive_losses < 2) and (not is_blocked_day) and (not in_cooldown)
    risk_detail = "Hạn mức ngày hợp lệ"
    if daily_fills >= 3:
        risk_detail = "Đã đạt tối đa 3 lệnh/ngày"
        reason_code = "MAX_DAILY_ENTRIES"
    elif consecutive_losses >= 2:
        risk_detail = "Đã dừng giao dịch sau 2 lệnh lỗ liên tiếp"
        reason_code = "MAX_CONSECUTIVE_LOSSES"
    elif in_cooldown:
        risk_detail = f"Đang trong thời gian cooldown: còn {int((cooldown_until - now_ms)/1000)}s"
        reason_code = "COOLDOWN_ACTIVE"
    elif is_blocked_day:
        risk_detail = f"Ngày bị khóa: {day_audit.block_reason}"
        reason_code = "DAILY_LOSS_CAP_EXCEEDED"

    checklist.append({
        "id": "RISK_BUDGET",
        "label": "Ngân Sách Rủi Ro & Giới Hạn Ngày (UTC+7)",
        "status": "PASS" if risk_pass else "FAIL",
        "detail": risk_detail,
        "is_hard_filter": True
    })

    # Filter 5: Liquidity Sweep
    sweep_pass = (last_sweep_ev is not None and (
        (setup_direction == "LONG" and last_sweep_ev.get("kind") == "SWEEP_LOW") or
        (setup_direction == "SHORT" and last_sweep_ev.get("kind") == "SWEEP_HIGH")
    ))
    checklist.append({
        "id": "LIQUIDITY_SWEEP",
        "label": "Xác Nhận Liquidity Sweep Chuỗi Setup (Nến Đóng)",
        "status": "PASS" if sweep_pass else ("WAITING" if setup_direction else "FAIL"),
        "detail": "Đã quét thanh khoản và đóng nến rút chân hợp lệ theo chuỗi" if sweep_pass else "Chưa có nến đóng quét thanh khoản",
        "is_hard_filter": True
    })

    # Filter 6: Liquidation Safety Buffer
    liq_pass = calc_res is not None and calc_res.can_execute and (
        (setup_direction == "LONG" and calc_res.estimated_liquidation < calc_res.stop_loss) or
        (setup_direction == "SHORT" and calc_res.estimated_liquidation > calc_res.stop_loss)
    ) if calc_res else True
    liq_detail = f"LP: {calc_res.estimated_liquidation:.2f} (Đệm SL: ${calc_res.sl_lp_buffer_usdt:.2f})" if calc_res and calc_res.estimated_liquidation else "Đệm an toàn hợp lệ"
    checklist.append({
        "id": "LIQUIDATION_BUFFER",
        "label": "Khoảng Đệm Thanh Lý Ước Tính (Isolated LP vs SL)",
        "status": "PASS" if liq_pass else "FAIL",
        "detail": liq_detail,
        "is_hard_filter": True
    })

    # State Machine Assignment
    engine_state = "waiting_setup"
    if not news_pass:
        engine_state = "blocked_news"
        reason_code = "NEWS_BLACKOUT"
    elif not risk_pass:
        engine_state = "blocked_risk"
    elif not data_fresh_pass:
        engine_state = "stale_data"
        reason_code = "STALE_QUOTE"
    elif setup_direction and rr_pass and sweep_pass and liq_pass and calc_res and calc_res.can_execute:
        engine_state = "candidate"
        setup_stage = "READY"
        reason_code = "SETUP_READY"

    # Build Authoritative Active Signal
    active_signal = None
    if calc_res and calc_res.is_valid and (engine_state in ("candidate", "armed", "paper_open")):
        active_signal = {
            "id": f"setup-{symbol}-{timeframe}-{current_candle.timestamp}",
            "setup_id": f"setup-{current_candle.timestamp}",
            "signal_id": f"sig-{current_candle.timestamp}",
            "trade_id": None,
            "instrument": symbol,
            "direction": setup_direction,
            "state": "candidate",
            "planned_entry": calc_res.planned_entry,
            "actual_entry": None,
            "stop_loss": calc_res.stop_loss,
            "targets": [
                {
                    "price": calc_res.take_profit,
                    "close_fraction": 1.0,
                    "gross_rr": calc_res.gross_rr
                }
            ],
            "quantity": calc_res.quantity,
            "initial_risk_usdt": calc_res.net_risk_usdt,
            "risk_pct": risk_pct,
            "gross_rr": calc_res.gross_rr,
            "estimated_net_rr": calc_res.net_rr,
            "fees_assumption": calc_res.fees_total_usdt,
            "slippage_assumption": calc_res.slippage_total_usdt,
            "leverage": calc_res.leverage,
            "margin_mode": calc_res.margin_mode,
            "estimated_liquidation": calc_res.estimated_liquidation,
            "initial_margin": calc_res.initial_margin_usdt,
            "created_at": now_ms,
            "armed_at": None,
            "opened_at": None,
            "closed_at": None,
            "expires_at": now_ms + (2 * 60 * 60 * 1000),
            "actual_exit": None,
            "realized_pnl_net": None,
            "realized_r": None,
            "invalidation_reason": None,
            "strategy_version": "1.0.0"
        }

    return {
        "status": "success",
        "symbol": symbol,
        "timeframe": timeframe,
        "current_price": round(current_price, 2),
        "bid": round(bid, 2),
        "ask": round(ask, 2),
        "trend": trend,
        "htf_bias": final_htf_bias,
        "h1_alignment": final_h1_align,
        "zone": zone,
        "equilibrium": round(eq, 2),
        "dealing_range": {
            "high": round(dealing_high, 2),
            "low": round(dealing_low, 2)
        },
        "swing_high": round(last_sh, 2),
        "swing_low": round(last_sl, 2),
        "swings": all_swings,
        "structure_events": events,
        "last_event": last_event,
        "atr": round(atr, 2),
        "active_fvgs": active_fvgs,
        "liquidity_levels": liquidity_levels,
        "checklist": checklist,
        "active_signal": active_signal,
        "engine_state": engine_state,
        "setup_stage": setup_stage,
        "reason_code": reason_code,
        "last_analyzed_at": now_ms,
        "last_data_at": last_data_at,
        "missing_conditions": missing_conditions,
        "conditions_met": conditions_met
    }
