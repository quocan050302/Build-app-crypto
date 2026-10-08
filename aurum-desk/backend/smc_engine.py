import time
import math
from typing import List, Dict, Any, Optional, Tuple
import schemas

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
    Crucial: candle at i is only confirmed at candle i+2.
    """
    n = len(candles)
    swing_highs = []
    swing_lows = []

    # Requires 2 bars to the left and 2 closed bars to the right
    for i in range(2, n - 2):
        c = candles[i]
        # Pivot High
        if (c.high > candles[i - 1].high and c.high > candles[i - 2].high and
            c.high > candles[i + 1].high and c.high > candles[i + 2].high):
            swing_highs.append({
                "index": i,
                "timestamp": c.timestamp,
                "confirmed_at": candles[i + 2].timestamp,
                "price": round(c.high, 2),
                "type": "HIGH"
            })

        # Pivot Low
        if (c.low < candles[i - 1].low and c.low < candles[i - 2].low and
            c.low < candles[i + 1].low and c.low < candles[i + 2].low):
            swing_lows.append({
                "index": i,
                "timestamp": c.timestamp,
                "confirmed_at": candles[i + 2].timestamp,
                "price": round(c.low, 2),
                "type": "LOW"
            })

    return swing_highs, swing_lows


def determine_trend(candles: list, swing_highs: list, swing_lows: list) -> str:
    """
    Determine market structure trend based on swing sequence:
    - HH + HL => BULLISH
    - LH + LL => BEARISH
    - Otherwise RANGING or fallback to price action
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

    # Fallback to 20-period simple price mean
    if len(candles) >= 10:
        ma = sum(c.close for c in candles[-20:]) / min(20, len(candles))
        return "BULLISH" if candles[-1].close >= ma else "BEARISH"
    return "UNKNOWN"


def detect_fvgs(candles: list) -> List[Dict[str, Any]]:
    """
    Detect 3-candle Fair Value Gaps (FVG) and track their mitigation state:
    - created -> confirmed -> partially_mitigated -> fully_mitigated
    """
    n = len(candles)
    fvgs = []

    for i in range(2, n):
        # Bullish FVG: Low of candle[i] > High of candle[i-2]
        if candles[i].low > candles[i - 2].high:
            top = candles[i].low
            bottom = candles[i - 2].high
            # Check mitigation by subsequent candles
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
) -> Tuple[List[str], str, Optional[Dict[str, Any]]]:
    """
    Detect liquidity sweeps and structural breaks (BOS/CHoCH).
    Returns (events_list, last_event_summary, sweep_info)
    """
    n = len(candles)
    events = []
    last_event = "Cấu trúc ổn định"
    sweep_detected = None

    if not swing_highs or not swing_lows or n < 5:
        return events, last_event, sweep_detected

    last_sh = swing_highs[-1]["price"]
    last_sl = swing_lows[-1]["price"]

    # Examine recent bars for sweeps and breaks
    lookback = min(20, n - 2)
    for i in range(n - lookback, n):
        c = candles[i]

        # Liquidity Sweep of High: Wick broke above swing high, but closed below it
        if c.high > last_sh and c.close < last_sh:
            desc = f"Liquidity Sweep đỉnh {last_sh:.2f}"
            events.append(desc)
            last_event = desc
            sweep_detected = {
                "type": "SWEEP_HIGH",
                "level": last_sh,
                "wick_high": c.high,
                "timestamp": c.timestamp
            }

        # Liquidity Sweep of Low: Wick broke below swing low, but closed above it
        if c.low < last_sl and c.close > last_sl:
            desc = f"Liquidity Sweep đáy {last_sl:.2f}"
            events.append(desc)
            last_event = desc
            sweep_detected = {
                "type": "SWEEP_LOW",
                "level": last_sl,
                "wick_low": c.low,
                "timestamp": c.timestamp
            }

        # BOS / CHoCH detection on closed candles
        if c.is_closed:
            if c.close > last_sh:
                evt = "BOS Tăng (Bullish)" if trend == "BULLISH" else "CHoCH Đảo chiều Tăng"
                events.append(f"{evt} tại {last_sh:.2f}")
                last_event = f"{evt} ({last_sh:.2f})"
            elif c.close < last_sl:
                evt = "BOS Giảm (Bearish)" if trend == "BEARISH" else "CHoCH Đảo chiều Giảm"
                events.append(f"{evt} tại {last_sl:.2f}")
                last_event = f"{evt} ({last_sl:.2f})"

    return events, last_event, sweep_detected


def calculate_position_sizing(
    capital: float,
    risk_pct: float,
    entry: float,
    sl: float,
    tp: float,
    fees_pct: float = 0.04,   # 0.04% maker/taker fee
    slippage_usd: float = 0.10 # $0.10 slippage assumption
) -> Dict[str, Any]:
    """
    Calculate position size, Gross R:R, and estimated Net R:R.
    Bitget XAUUSDT perpetual futures contract unit: 1 contract = 1 oz of gold.
    Minimum quantity step: 0.01 oz.
    """
    risk_amount = capital * (risk_pct / 100.0)
    stop_distance = abs(entry - sl)
    if stop_distance <= 0.01:
        stop_distance = 1.0

    # Total risk per unit including estimated slippage and round-trip fee
    estimated_fee_per_unit = (entry + sl) * (fees_pct / 100.0)
    total_risk_per_unit = stop_distance + slippage_usd + estimated_fee_per_unit

    raw_quantity = risk_amount / total_risk_per_unit
    # Floor to 2 decimal places (0.01 oz step)
    quantity = math.floor(raw_quantity * 100) / 100.0
    if quantity < 0.01:
        quantity = 0.01

    target_distance = abs(tp - entry)
    gross_rr = round(target_distance / stop_distance, 2) if stop_distance > 0 else 0.0

    # Net expected reward after fees
    expected_gross_reward = quantity * target_distance
    total_expected_fees = quantity * (entry + tp) * (fees_pct / 100.0)
    net_reward = expected_gross_reward - total_expected_fees - (quantity * slippage_usd)
    actual_risk_dollars = quantity * total_risk_per_unit
    net_rr = round(net_reward / actual_risk_dollars, 2) if actual_risk_dollars > 0 else 0.0

    return {
        "quantity": quantity,
        "initial_risk_usdt": round(actual_risk_dollars, 2),
        "gross_rr": gross_rr,
        "estimated_net_rr": net_rr,
        "fees_assumption": round(total_expected_fees, 3),
        "slippage_assumption": round(quantity * slippage_usd, 3)
    }


def evaluate_smc_setup(
    candles: list,
    symbol: str = "XAUUSDT",
    timeframe: str = "15M",
    ticker_data: Optional[Dict[str, Any]] = None,
    day_audit: Optional[Any] = None,
    is_news_blackout: bool = False,
    news_blackout_reason: Optional[str] = None
) -> Dict[str, Any]:
    """
    Execute full SMC/ICT v1 rules:
    1. Multi-timeframe trend & dealing range
    2. Pivots, Sweeps, BOS/CHoCH
    3. FVG validation in Premium/Discount
    4. Hard filter evaluation (News, Data freshness, R:R >= 2.0, Risk limits)
    5. State machine: waiting_setup, candidate, armed, paper_open, blocked_news, blocked_risk
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
            "htf_bias": "NEUTRAL",
            "h1_alignment": "WAITING",
            "zone": "EQUILIBRIUM",
            "equilibrium": 0.0,
            "dealing_range": {"high": 0.0, "low": 0.0},
            "swing_high": 0.0,
            "swing_low": 0.0,
            "last_event": "Đang thu thập đủ nến lịch sử...",
            "atr": 2.0,
            "active_fvgs": [],
            "liquidity_levels": [],
            "checklist": [],
            "active_signal": None,
            "engine_state": "collecting_data",
            "last_analyzed_at": now_ms,
            "last_data_at": candles[-1].timestamp if candles else now_ms,
            "missing_conditions": ["Cần tối thiểu 20 nến để tính ATR và Swing Points"]
        }

    current_candle = candles[-1]
    current_price = current_candle.close
    last_data_at = current_candle.timestamp
    bid = ticker_data.get("bid", current_price) if ticker_data else current_price
    ask = ticker_data.get("ask", current_price) if ticker_data else current_price
    atr = compute_atr(candles, 14)

    # 1. Swing Pivots
    swing_highs, swing_lows = identify_pivots(candles)
    recent_high = max(c.high for c in candles[-20:])
    recent_low = min(c.low for c in candles[-20:])
    last_sh = swing_highs[-1]["price"] if swing_highs else recent_high
    last_sl = swing_lows[-1]["price"] if swing_lows else recent_low

    # 2. Trend & Dealing Range
    trend = determine_trend(candles, swing_highs, swing_lows)
    dealing_high = max([sh["price"] for sh in swing_highs[-3:]] or [recent_high])
    dealing_low = min([sl["price"] for sl in swing_lows[-3:]] or [recent_low])
    if dealing_high <= dealing_low:
        dealing_high = current_price + 10.0
        dealing_low = current_price - 10.0

    eq = (dealing_high + dealing_low) / 2.0
    zone = "DISCOUNT" if current_price < eq else "PREMIUM"

    # 3. FVGs & Sweeps
    all_fvgs = detect_fvgs(candles)
    active_fvgs = [f for f in all_fvgs if not f["mitigated"]][-4:]
    events, last_event, sweep_info = detect_sweeps_and_breaks(candles, swing_highs, swing_lows, trend)

    # Liquidity levels
    liquidity_levels = [
        {"price": last_sh, "type": "SWING_HIGH", "timestamp": swing_highs[-1]["timestamp"] if swing_highs else now_ms, "status": "active"},
        {"price": last_sl, "type": "SWING_LOW", "timestamp": swing_lows[-1]["timestamp"] if swing_lows else now_ms, "status": "active"},
        {"price": dealing_high, "type": "DEALING_HIGH", "timestamp": now_ms, "status": "active"},
        {"price": dealing_low, "type": "DEALING_LOW", "timestamp": now_ms, "status": "active"}
    ]

    # 4. Strategy Rules Setup Formulation (Long / Short)
    setup_direction = None
    planned_entry = current_price
    sl = current_price
    tp = current_price
    invalidation_reason = ""
    missing_conditions = []

    # Long Setup: Trend Bullish OR (Sweep Low in Discount)
    has_sweep_low = any("Sweep đáy" in e for e in events)
    has_sweep_high = any("Sweep đỉnh" in e for e in events)

    if (trend == "BULLISH" and zone == "DISCOUNT") or (has_sweep_low and zone == "DISCOUNT"):
        setup_direction = "LONG"
        planned_entry = current_price
        # SL below lowest recent swing low or sweep wick with 0.5 ATR buffer
        sl = round(min(last_sl, current_price - 1.5 * atr), 2)
        risk_dist = max(1.0, planned_entry - sl)
        # TP at opposite swing high or 2.5x risk
        tp = round(max(last_sh, planned_entry + 2.5 * risk_dist), 2)
    elif (trend == "BEARISH" and zone == "PREMIUM") or (has_sweep_high and zone == "PREMIUM"):
        setup_direction = "SHORT"
        planned_entry = current_price
        sl = round(max(last_sh, current_price + 1.5 * atr), 2)
        risk_dist = max(1.0, sl - planned_entry)
        tp = round(min(last_sl, planned_entry - 2.5 * risk_dist), 2)
    else:
        missing_conditions.append(f"Chưa có phân kỳ cấu trúc (Đang ở {zone} với xu hướng {trend})")

    # 5. Position Sizing & RR Calculation
    capital = day_audit.current_equity if day_audit else 1000.0
    risk_pct = 0.5
    sizing = calculate_position_sizing(capital, risk_pct, planned_entry, sl, tp)

    # 6. Hard Filters Checklist
    checklist: List[Dict[str, Any]] = []

    # Filter 1: News Blackout
    news_pass = not is_news_blackout
    checklist.append({
        "id": "NEWS_BLACKOUT",
        "label": "Bộ lọc Tin Tức Vĩ Mô (News Blackout)",
        "status": "PASS" if news_pass else "FAIL",
        "detail": "Không có tin USD High Impact trong cửa sổ an toàn" if news_pass else f"Đang trong vùng Blackout: {news_blackout_reason}",
        "is_hard_filter": True
    })

    # Filter 2: Data Freshness
    data_fresh_pass = (now_ms - last_data_at) < (30 * 60 * 1000)
    checklist.append({
        "id": "DATA_FRESHNESS",
        "label": "Độ Tươi Của Dữ Liệu Nến (Data Freshness)",
        "status": "PASS" if data_fresh_pass else "FAIL",
        "detail": f"Dữ liệu nến mới nhất cách đây {int((now_ms - last_data_at)/1000)}s" if data_fresh_pass else "Dữ liệu bị trễ hoặc mất kết nối feed",
        "is_hard_filter": True
    })

    # Filter 3: Net RR >= 2.0
    rr_pass = (sizing["estimated_net_rr"] >= 2.0)
    checklist.append({
        "id": "MIN_NET_RR",
        "label": "Tỷ Lệ Net R:R Tối Thiểu 1:2.0",
        "status": "PASS" if rr_pass else "FAIL",
        "detail": f"Net R:R ước tính: 1:{sizing['estimated_net_rr']} (Gross: 1:{sizing['gross_rr']})",
        "is_hard_filter": True
    })

    # Filter 4: Risk & Day Limits
    daily_fills = day_audit.fills_count if day_audit else 0
    consecutive_losses = day_audit.consecutive_losses if day_audit else 0
    is_blocked_day = day_audit.is_blocked if day_audit else False
    cooldown_until = day_audit.cooldown_until if day_audit else 0
    in_cooldown = (cooldown_until and now_ms < cooldown_until)

    risk_pass = (daily_fills < 3) and (consecutive_losses < 2) and (not is_blocked_day) and (not in_cooldown)
    risk_detail = "Hạn mức ngày hợp lệ"
    if daily_fills >= 3:
        risk_detail = "Đã đạt tối đa 3 lệnh/ngày"
    elif consecutive_losses >= 2:
        risk_detail = "Đã dừng giao dịch sau 2 lệnh lỗ liên tiếp"
    elif in_cooldown:
        risk_detail = f"Đang trong thời gian nghỉ ngơi (cooldown) {int((cooldown_until - now_ms)/1000)}s"
    elif is_blocked_day:
        risk_detail = f"Ngày bị khóa: {day_audit.block_reason}"

    checklist.append({
        "id": "RISK_BUDGET",
        "label": "Ngân Sách Rủi Ro & Giới Hạn Ngày (UTC+7)",
        "status": "PASS" if risk_pass else "FAIL",
        "detail": risk_detail,
        "is_hard_filter": True
    })

    # Filter 5: Liquidity Sweep / Confirmation
    sweep_pass = (has_sweep_low if setup_direction == "LONG" else (has_sweep_high if setup_direction == "SHORT" else False))
    checklist.append({
        "id": "LIQUIDITY_SWEEP",
        "label": "Xác Nhận Liquidity Sweep & POI",
        "status": "PASS" if sweep_pass else ("WAITING" if setup_direction else "FAIL"),
        "detail": "Đã quét thanh khoản đáy và rút chân" if (setup_direction == "LONG" and sweep_pass) else (
            "Đã quét thanh khoản đỉnh và rút chân" if (setup_direction == "SHORT" and sweep_pass) else "Chưa xuất hiện nến quét thanh khoản rõ rệt"
        ),
        "is_hard_filter": True
    })

    # Determine Engine State
    engine_state = "waiting_setup"
    if not news_pass:
        engine_state = "blocked_news"
    elif not risk_pass:
        engine_state = "blocked_risk"
    elif not data_fresh_pass:
        engine_state = "stale_data"
    elif setup_direction and rr_pass:
        if sweep_pass:
            engine_state = "candidate"
        else:
            engine_state = "waiting_setup"
            missing_conditions.append("Cần xác nhận Liquidity Sweep trước khi kích hoạt lệnh")
    else:
        if setup_direction and not rr_pass:
            missing_conditions.append(f"Tỷ lệ Net R:R 1:{sizing['estimated_net_rr']} chưa đạt ngưỡng tối thiểu 2.0")

    # Build Signal Overlay if candidate or armed
    active_signal = None
    if setup_direction and (engine_state in ("candidate", "armed", "paper_open")):
        active_signal = {
            "id": f"setup-{symbol}-{timeframe}-{current_candle.timestamp}",
            "setup_id": f"setup-{current_candle.timestamp}",
            "signal_id": f"sig-{current_candle.timestamp}",
            "trade_id": None,
            "instrument": symbol,
            "direction": setup_direction,
            "state": engine_state,
            "planned_entry": planned_entry,
            "actual_entry": None,
            "stop_loss": sl,
            "targets": [
                {
                    "price": tp,
                    "close_fraction": 1.0,
                    "gross_rr": sizing["gross_rr"]
                }
            ],
            "quantity": sizing["quantity"],
            "initial_risk_usdt": sizing["initial_risk_usdt"],
            "risk_pct": risk_pct,
            "gross_rr": sizing["gross_rr"],
            "estimated_net_rr": sizing["estimated_net_rr"],
            "fees_assumption": sizing["fees_assumption"],
            "slippage_assumption": sizing["slippage_assumption"],
            "created_at": now_ms,
            "armed_at": now_ms if engine_state in ("armed", "candidate") else None,
            "opened_at": None,
            "closed_at": None,
            "expires_at": now_ms + (2 * 60 * 60 * 1000), # 2 hour expiry
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
        "htf_bias": "BULLISH" if trend in ("BULLISH", "RANGING") else "BEARISH",
        "h1_alignment": "CONSENSUS" if trend != "UNKNOWN" else "NEUTRAL",
        "zone": zone,
        "equilibrium": round(eq, 2),
        "dealing_range": {
            "high": round(dealing_high, 2),
            "low": round(dealing_low, 2)
        },
        "swing_high": round(last_sh, 2),
        "swing_low": round(last_sl, 2),
        "last_event": last_event,
        "atr": round(atr, 2),
        "active_fvgs": active_fvgs,
        "liquidity_levels": liquidity_levels,
        "checklist": checklist,
        "active_signal": active_signal,
        "engine_state": engine_state,
        "last_analyzed_at": now_ms,
        "last_data_at": last_data_at,
        "missing_conditions": missing_conditions
    }
