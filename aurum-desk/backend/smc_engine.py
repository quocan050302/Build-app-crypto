import time
import math
from typing import List, Dict, Any, Optional, Tuple
import schemas
from domain_calculator import calculate_risk_reward, CalculationResult, CostAssumptions

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


def identify_pivots(candles: list) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Detect confirmed Pivot Highs and Pivot Lows using 2 candles left + 2 candles right.
    CRITICAL (Issue 8): A pivot at candle i is confirmed ONLY when candle i+2 is closed (is_closed == True).
    If candle i+2 is still forming/open, the pivot is NOT confirmed.
    """
    n = len(candles)
    swing_highs = []
    swing_lows = []

    # Requires 2 bars to the left and 2 closed bars to the right
    for i in range(2, n - 2):
        c = candles[i]
        c_right2 = candles[i + 2]

        # Candle i+2 MUST be closed for the pivot to be confirmed
        if not getattr(c_right2, 'is_closed', True):
            continue

        # Pivot High
        if (c.high > candles[i - 1].high and c.high > candles[i - 2].high and
            c.high > candles[i + 1].high and c.high > c_right2.high):
            swing_highs.append({
                "id": f"sh-{c.timestamp}",
                "index": i,
                "timestamp": c.timestamp,
                "pivot_at": c.timestamp,
                "confirmed_at": c_right2.timestamp,
                "price": round(c.high, 2),
                "kind": "HIGH",
                "is_confirmed": True
            })

        # Pivot Low
        if (c.low < candles[i - 1].low and c.low < candles[i - 2].low and
            c.low < candles[i + 1].low and c.low < c_right2.low):
            swing_lows.append({
                "id": f"sl-{c.timestamp}",
                "index": i,
                "timestamp": c.timestamp,
                "pivot_at": c.timestamp,
                "confirmed_at": c_right2.timestamp,
                "price": round(c.low, 2),
                "kind": "LOW",
                "is_confirmed": True
            })

    # Label swings: HH, HL, LH, LL
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


def determine_trend(candles: list, swing_highs: list, swing_lows: list) -> str:
    """
    Determine market structure trend strictly based on swing sequence:
    - HH + HL => BULLISH
    - LH + LL => BEARISH
    - Otherwise RANGING or UNKNOWN
    """
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        sh1, sh2 = swing_highs[-2]["price"], swing_highs[-1]["price"]
        sl1, sl2 = swing_lows[-2]["price"], swing_lows[-1]["price"]

        if sh2 > sh1 and sl2 > sl1:
            return "BULLISH"
        elif sh2 < sh1 and sl2 < sl1:
            return "BEARISH"
        elif candles[-1].close > sh2:
            return "BULLISH"
        elif candles[-1].close < sl2:
            return "BEARISH"
        else:
            return "RANGING"

    if len(candles) >= 20:
        ma = sum(c.close for c in candles[-20:]) / min(20, len(candles))
        return "BULLISH" if candles[-1].close >= ma else "BEARISH"
    return "UNKNOWN"


def detect_fvgs(candles: list) -> List[Dict[str, Any]]:
    """
    Detect 3-candle Fair Value Gaps (FVG) and track their mitigation state:
    created -> confirmed -> partially_mitigated -> fully_mitigated
    """
    n = len(candles)
    fvgs = []

    for i in range(2, n):
        # Bullish FVG: Low of candle[i] > High of candle[i-2]
        if candles[i].low > candles[i - 2].high:
            top = candles[i].low
            bottom = candles[i - 2].high
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

            fvgs.append({
                "id": f"fvg-bull-{candles[i-1].timestamp}",
                "type": "BULLISH_FVG",
                "top": round(top, 2),
                "bottom": round(bottom, 2),
                "timestamp": candles[i - 1].timestamp,
                "state": state,
                "mitigated": mitigated
            })

        # Bearish FVG: High of candle[i] < Low of candle[i-2]
        if candles[i].high < candles[i - 2].low:
            top = candles[i - 2].low
            bottom = candles[i].high
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

            fvgs.append({
                "id": f"fvg-bear-{candles[i-1].timestamp}",
                "type": "BEARISH_FVG",
                "top": round(top, 2),
                "bottom": round(bottom, 2),
                "timestamp": candles[i - 1].timestamp,
                "state": state,
                "mitigated": mitigated
            })

    return fvgs


def detect_sweeps_and_breaks(
    candles: list,
    swing_highs: list,
    swing_lows: list,
    trend: str
) -> Tuple[List[Dict[str, Any]], str, Optional[Dict[str, Any]]]:
    """
    Causal Sweep & Break detection (Issue 8):
    At each bar t, only use swings that were ALREADY CONFIRMED at or before bar t's timestamp.
    Does NOT use future swings or the last swing of the entire dataset.
    Sweep requires closed bar (is_closed == True).
    """
    n = len(candles)
    structure_events: List[Dict[str, Any]] = []
    last_event_summary = "Cấu trúc ổn định"
    latest_sweep = None

    if n < 5:
        return structure_events, last_event_summary, latest_sweep

    # Process candles chronologically
    for i in range(max(2, n - 30), n):
        c = candles[i]
        c_time = c.timestamp

        # Only swings confirmed BEFORE or AT this candle can be observed
        available_sh = [s for s in swing_highs if s.get("confirmed_at", s.get("timestamp", 0)) <= c_time]
        available_sl = [s for s in swing_lows if s.get("confirmed_at", s.get("timestamp", 0)) <= c_time]

        if not available_sh and not available_sl:
            continue

        active_sh = available_sh[-1]["price"] if available_sh else None
        active_sl = available_sl[-1]["price"] if available_sl else None

        # Sweep of High: Wick pierced above confirmed swing high, but bar closed strictly below it
        if active_sh and c.is_closed and c.high > active_sh and c.close < active_sh:
            sweep_ev = {
                "id": f"sweep-high-{c_time}",
                "event_type": "SWEEP",
                "type": "SWEEP_HIGH",
                "kind": "SWEEP_HIGH",
                "level": active_sh,
                "wick_extreme": c.high,
                "timestamp": c_time,
                "confirmed_at": c_time,
                "detail": f"Sweep đỉnh {active_sh:.2f} (Râu {c.high:.2f}, Đóng {c.close:.2f})"
            }
            structure_events.append(sweep_ev)
            last_event_summary = sweep_ev["detail"]
            latest_sweep = sweep_ev

        # Sweep of Low: Wick pierced below confirmed swing low, but bar closed strictly above it
        if active_sl and c.is_closed and c.low < active_sl and c.close > active_sl:
            sweep_ev = {
                "id": f"sweep-low-{c_time}",
                "event_type": "SWEEP",
                "type": "SWEEP_LOW",
                "kind": "SWEEP_LOW",
                "level": active_sl,
                "wick_extreme": c.low,
                "timestamp": c_time,
                "confirmed_at": c_time,
                "detail": f"Sweep đáy {active_sl:.2f} (Râu {c.low:.2f}, Đóng {c.close:.2f})"
            }
            structure_events.append(sweep_ev)
            last_event_summary = sweep_ev["detail"]
            latest_sweep = sweep_ev

        # BOS / CHoCH: Candle close beyond confirmed swing
        if c.is_closed:
            if active_sh and c.close > active_sh:
                b_type = "BOS" if trend == "BULLISH" else "CHOCH"
                desc = f"{b_type} Tăng tại {active_sh:.2f}"
                structure_events.append({
                    "id": f"{b_type.lower()}-bull-{c_time}",
                    "event_type": b_type,
                    "direction": "BULLISH",
                    "level": active_sh,
                    "timestamp": c_time,
                    "confirmed_at": c_time,
                    "detail": desc
                })
                last_event_summary = desc

            elif active_sl and c.close < active_sl:
                b_type = "BOS" if trend == "BEARISH" else "CHOCH"
                desc = f"{b_type} Giảm tại {active_sl:.2f}"
                structure_events.append({
                    "id": f"{b_type.lower()}-bear-{c_time}",
                    "event_type": b_type,
                    "direction": "BEARISH",
                    "level": active_sl,
                    "timestamp": c_time,
                    "confirmed_at": c_time,
                    "detail": desc
                })
                last_event_summary = desc

    return structure_events, last_event_summary, latest_sweep


def evaluate_smc_setup(
    candles: list,
    symbol: str = "XAUUSDT",
    timeframe: str = "15M",
    ticker_data: Optional[Dict[str, Any]] = None,
    day_audit: Optional[Any] = None,
    is_news_blackout: bool = False,
    news_blackout_reason: Optional[str] = None,
    htf_bias: Optional[str] = None,        # Real HTF bias from D/4H
    h1_alignment: Optional[str] = None    # Real H1 context
) -> Dict[str, Any]:
    """
    SMC/ICT Strategy Engine v1.0.0 (Prompt V3 requirements):
    - Real multi-timeframe bias (D/4H/1H)
    - Anchored SL to actual sweep extreme / POI boundary + small buffer
    - Target TP from authentic opposing confirmed liquidity (NO artificial 2.5x forcing!)
    - Authoritative calculate_risk_reward domain call
    - Specific reason codes
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
            "htf_bias": htf_bias or "NEUTRAL",
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
            "missing_conditions": ["Cần tối thiểu 20 nến để phân tích"]
        }

    current_candle = candles[-1]
    current_price = current_candle.close
    last_data_at = current_candle.timestamp
    bid = ticker_data.get("bid", current_price) if ticker_data else current_price
    ask = ticker_data.get("ask", current_price) if ticker_data else current_price
    atr = compute_atr(candles, 14)

    # 1. Swing Pivots (no lookahead, confirmed at i+2)
    swing_highs, swing_lows = identify_pivots(candles)
    recent_high = max(c.high for c in candles[-20:])
    recent_low = min(c.low for c in candles[-20:])
    last_sh = swing_highs[-1]["price"] if swing_highs else recent_high
    last_sl = swing_lows[-1]["price"] if swing_lows else recent_low

    all_swings = sorted(swing_highs + swing_lows, key=lambda s: s["timestamp"])

    # 2. Trend & Dealing Range
    trend = determine_trend(candles, swing_highs, swing_lows)
    dealing_high = max([sh["price"] for sh in swing_highs[-3:]] or [recent_high])
    dealing_low = min([sl["price"] for sl in swing_lows[-3:]] or [recent_low])
    if dealing_high <= dealing_low:
        dealing_high = current_price + 10.0
        dealing_low = current_price - 10.0

    eq = (dealing_high + dealing_low) / 2.0
    zone = "DISCOUNT" if current_price < eq else "PREMIUM"

    # Multi-timeframe synthesis
    final_htf_bias = htf_bias or trend
    final_h1_align = h1_alignment or ("CONSENSUS" if final_htf_bias == trend else "NEUTRAL")

    # 3. FVGs & Causal Sweeps
    all_fvgs = detect_fvgs(candles)
    active_fvgs = [f for f in all_fvgs if not f["mitigated"]][-4:]
    events, last_event, sweep_info = detect_sweeps_and_breaks(candles, swing_highs, swing_lows, trend)

    liquidity_levels = [
        {"price": last_sh, "type": "SWING_HIGH", "timestamp": swing_highs[-1]["timestamp"] if swing_highs else now_ms, "status": "active"},
        {"price": last_sl, "type": "SWING_LOW", "timestamp": swing_lows[-1]["timestamp"] if swing_lows else now_ms, "status": "active"},
        {"price": dealing_high, "type": "DEALING_HIGH", "timestamp": now_ms, "status": "active"},
        {"price": dealing_low, "type": "DEALING_LOW", "timestamp": now_ms, "status": "active"}
    ]

    # 4. Strategy Rules Formulation (Issue 5: NO artificial forcing!)
    setup_direction = None
    sl = current_price
    tp = current_price
    missing_conditions = []
    reason_code = "WAITING_SETUP"

    has_sweep_low = any(e.get("kind") == "SWEEP_LOW" for e in events)
    has_sweep_high = any(e.get("kind") == "SWEEP_HIGH" for e in events)

    # Long Setup:
    # 1. Trend Bullish AND in Discount AND Sweep of Low confirmed
    if (trend == "BULLISH" and zone == "DISCOUNT" and has_sweep_low) or (final_htf_bias == "BULLISH" and zone == "DISCOUNT" and has_sweep_low):
        setup_direction = "LONG"
        # SL anchored strictly to the lowest sweep wick extreme + 0.3 ATR buffer
        sweep_extreme = sweep_info["wick_extreme"] if (sweep_info and sweep_info["kind"] == "SWEEP_LOW") else last_sl
        sl = round(sweep_extreme - 0.3 * atr, 2)
        # TP anchored strictly to authentic opposing confirmed liquidity (Swing High), NOT forced 2.5x!
        tp = round(last_sh, 2)

    # Short Setup:
    elif (trend == "BEARISH" and zone == "PREMIUM" and has_sweep_high) or (final_htf_bias == "BEARISH" and zone == "PREMIUM" and has_sweep_high):
        setup_direction = "SHORT"
        sweep_extreme = sweep_info["wick_extreme"] if (sweep_info and sweep_info["kind"] == "SWEEP_HIGH") else last_sh
        sl = round(sweep_extreme + 0.3 * atr, 2)
        # TP anchored strictly to authentic opposing confirmed liquidity (Swing Low), NOT forced 2.5x!
        tp = round(last_sl, 2)

    else:
        if not (has_sweep_low or has_sweep_high):
            missing_conditions.append("Chưa có xác nhận Liquidity Sweep đỉnh/đáy của nến đã đóng")
            reason_code = "SWEEP_NOT_CONFIRMED"
        elif (trend == "BULLISH" and zone == "PREMIUM") or (trend == "BEARISH" and zone == "DISCOUNT"):
            missing_conditions.append(f"Giá nằm sai vị trí Dealing Range (Xu hướng {trend} nhưng giá ở {zone})")
            reason_code = "WRONG_DEALING_ZONE"

    # 5. Position Sizing & Real Domain Calculation
    capital = day_audit.current_equity if day_audit else 1000.0
    risk_pct = 0.25 # Default 0.25% equity (conservative paper default)

    calc_res: Optional[CalculationResult] = None
    if setup_direction:
        calc_res = calculate_risk_reward(
            direction=setup_direction,
            entry=current_price,
            sl=sl,
            tp=tp,
            capital=capital,
            risk_pct=risk_pct,
            min_net_rr=2.0
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
    sweep_pass = (has_sweep_low if setup_direction == "LONG" else (has_sweep_high if setup_direction == "SHORT" else False))
    checklist.append({
        "id": "LIQUIDITY_SWEEP",
        "label": "Xác Nhận Liquidity Sweep (Nến Đóng)",
        "status": "PASS" if sweep_pass else ("WAITING" if setup_direction else "FAIL"),
        "detail": "Đã quét thanh khoản và đóng nến rút chân hợp lệ" if sweep_pass else "Chưa có nến đóng quét thanh khoản",
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
    elif setup_direction and rr_pass and sweep_pass and calc_res and calc_res.can_execute:
        engine_state = "candidate"
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
        "reason_code": reason_code,
        "last_analyzed_at": now_ms,
        "last_data_at": last_data_at,
        "missing_conditions": missing_conditions
    }
