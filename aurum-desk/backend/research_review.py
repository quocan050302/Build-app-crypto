"""
Aurum Desk V13.1 — Post-Session Outcome Review & Causal MFE/MAE Attribution Analytics.
Solves Section 9 requirements:
- Strictly simulates price path through closed candles after as_of
- Case A: If price moves directly to TP without touching Entry -> NOT_TRIGGERED (not a winning trade)
- Case B: If price touches Entry and later hits SL -> CLOSED_SL. MFE is terminated upon SL hit and does NOT include future candles!
- States: WAITING_ENTRY, OPEN, CLOSED_TP, CLOSED_SL, EXPIRED, NOT_TRIGGERED, AMBIGUOUS, DATA_UNAVAILABLE
- Separates verified FACTS from analytical HYPOTHESES
- Candidate lessons only generated for executed trades in PENDING_REVIEW governance state
"""

from typing import Dict, Any, List, Optional


def evaluate_scenario_outcome_and_mfe_mae(
    scenario: Dict[str, Any],
    candles_after_asof: List[Dict[str, Any]],
    initial_risk_usd: Optional[float] = None
) -> Dict[str, Any]:
    """
    Evaluates scenario performance after as_of using subsequent closed candles.
    Simulates causal fill and exit chronologically.
    """
    direction = scenario.get("direction", "LONG")
    try:
        planned_entry = float(scenario.get("planned_entry", 0.0))
        sl = float(scenario.get("stop_loss", 0.0))
        tp = float(scenario.get("take_profit", 0.0))
    except (ValueError, TypeError):
        return {
            "status": "DATA_UNAVAILABLE",
            "message": "Thông số kịch bản không hợp lệ để đánh giá",
            "mfe_usd": 0.0,
            "mfe_r": 0.0,
            "mae_usd": 0.0,
            "mae_r": 0.0,
            "outcome": "DỮ LIỆU KỊCH BẢN THIẾU",
            "facts": [],
            "hypotheses": []
        }

    risk = initial_risk_usd or abs(planned_entry - sl)
    if risk <= 0:
        risk = 1.0

    if not candles_after_asof:
        return {
            "status": "DATA_UNAVAILABLE",
            "message": "Không có dữ liệu nến sau thời điểm phân tích để đánh giá kết quả",
            "mfe_usd": 0.0,
            "mfe_r": 0.0,
            "mae_usd": 0.0,
            "mae_r": 0.0,
            "outcome": "CHƯA CÓ DỮ LIỆU ĐÁNH GIÁ (NO EVALUATION DATA)",
            "facts": [],
            "hypotheses": []
        }

    # Simulation State
    is_filled = False
    fill_timestamp = None
    fill_index = -1
    exit_status = None
    exit_timestamp = None
    exit_price = None

    mfe_usd = 0.0
    mae_usd = 0.0

    for idx, c in enumerate(candles_after_asof):
        c_high = float(c["high"])
        c_low = float(c["low"])
        c_open = float(c.get("open", c_high))
        c_close = float(c.get("close", c_low))
        c_time = c.get("timestamp", idx)

        if not is_filled:
            # Check if entry is touched
            if direction == "LONG":
                if c_low <= planned_entry:
                    is_filled = True
                    fill_timestamp = c_time
                    fill_index = idx

                    hit_sl = (c_low <= sl)
                    hit_tp = (c_high >= tp)
                    if hit_sl and hit_tp:
                        exit_status = "AMBIGUOUS"
                        exit_timestamp = c_time
                        exit_price = sl
                        mae_usd = max(mae_usd, planned_entry - c_low)
                        mfe_usd = max(mfe_usd, tp - planned_entry)
                        break
                    elif hit_sl:
                        exit_status = "CLOSED_SL"
                        exit_timestamp = c_time
                        exit_price = sl
                        mae_usd = max(mae_usd, planned_entry - c_low)
                        if c_high > planned_entry:
                            mfe_usd = max(mfe_usd, c_high - planned_entry)
                        break
                    elif hit_tp:
                        exit_status = "CLOSED_TP"
                        exit_timestamp = c_time
                        exit_price = tp
                        mfe_usd = max(mfe_usd, tp - planned_entry)
                        if c_low < planned_entry:
                            mae_usd = max(mae_usd, planned_entry - c_low)
                        break
                    else:
                        mfe_usd = max(mfe_usd, max(0.0, c_high - planned_entry))
                        mae_usd = max(mae_usd, max(0.0, planned_entry - c_low))
            else:  # SHORT
                if c_high >= planned_entry:
                    is_filled = True
                    fill_timestamp = c_time
                    fill_index = idx

                    hit_sl = (c_high >= sl)
                    hit_tp = (c_low <= tp)
                    if hit_sl and hit_tp:
                        exit_status = "AMBIGUOUS"
                        exit_timestamp = c_time
                        exit_price = sl
                        mae_usd = max(mae_usd, c_high - planned_entry)
                        mfe_usd = max(mfe_usd, planned_entry - tp)
                        break
                    elif hit_sl:
                        exit_status = "CLOSED_SL"
                        exit_timestamp = c_time
                        exit_price = sl
                        mae_usd = max(mae_usd, c_high - planned_entry)
                        if c_low < planned_entry:
                            mfe_usd = max(mfe_usd, planned_entry - c_low)
                        break
                    elif hit_tp:
                        exit_status = "CLOSED_TP"
                        exit_timestamp = c_time
                        exit_price = tp
                        mfe_usd = max(mfe_usd, planned_entry - tp)
                        if c_high > planned_entry:
                            mae_usd = max(mae_usd, c_high - planned_entry)
                        break
                    else:
                        mfe_usd = max(mfe_usd, max(0.0, planned_entry - c_low))
                        mae_usd = max(mae_usd, max(0.0, c_high - planned_entry))
        else:
            # Trade is OPEN
            if direction == "LONG":
                hit_sl = (c_low <= sl)
                hit_tp = (c_high >= tp)
                if hit_sl and hit_tp:
                    exit_status = "AMBIGUOUS"
                    exit_timestamp = c_time
                    exit_price = sl
                    mae_usd = max(mae_usd, planned_entry - c_low)
                    mfe_usd = max(mfe_usd, tp - planned_entry)
                    break
                elif hit_sl:
                    exit_status = "CLOSED_SL"
                    exit_timestamp = c_time
                    exit_price = sl
                    mae_usd = max(mae_usd, planned_entry - c_low)
                    if c_high > planned_entry:
                        mfe_usd = max(mfe_usd, c_high - planned_entry)
                    break  # CRITICAL: Stop upon SL, do not count future candle surges!
                elif hit_tp:
                    exit_status = "CLOSED_TP"
                    exit_timestamp = c_time
                    exit_price = tp
                    mfe_usd = max(mfe_usd, tp - planned_entry)
                    if c_low < planned_entry:
                        mae_usd = max(mae_usd, planned_entry - c_low)
                    break
                else:
                    mfe_usd = max(mfe_usd, max(0.0, c_high - planned_entry))
                    mae_usd = max(mae_usd, max(0.0, planned_entry - c_low))
            else:  # SHORT
                hit_sl = (c_high >= sl)
                hit_tp = (c_low <= tp)
                if hit_sl and hit_tp:
                    exit_status = "AMBIGUOUS"
                    exit_timestamp = c_time
                    exit_price = sl
                    mae_usd = max(mae_usd, c_high - planned_entry)
                    mfe_usd = max(mfe_usd, planned_entry - tp)
                    break
                elif hit_sl:
                    exit_status = "CLOSED_SL"
                    exit_timestamp = c_time
                    exit_price = sl
                    mae_usd = max(mae_usd, c_high - planned_entry)
                    if c_low < planned_entry:
                        mfe_usd = max(mfe_usd, planned_entry - c_low)
                    break  # CRITICAL: Stop upon SL
                elif hit_tp:
                    exit_status = "CLOSED_TP"
                    exit_timestamp = c_time
                    exit_price = tp
                    mfe_usd = max(mfe_usd, planned_entry - tp)
                    if c_high > planned_entry:
                        mae_usd = max(mae_usd, c_high - planned_entry)
                    break
                else:
                    mfe_usd = max(mfe_usd, max(0.0, planned_entry - c_low))
                    mae_usd = max(mae_usd, max(0.0, c_high - planned_entry))

    # Resolve Final Outcome and R-multiples
    if not is_filled:
        status = "NOT_TRIGGERED"
        outcome = "CHƯA KHỚP VÙNG VÀO (Giá không chạm điểm Entry trong phiên)"
        mfe_usd = 0.0
        mfe_r = 0.0
        mae_usd = 0.0
        mae_r = 0.0
        facts = [
            f"Kịch bản dự kiến {direction} @ ${planned_entry:.2f}, SL ${sl:.2f}, TP ${tp:.2f}",
            "Giá không chạm vùng điểm vào (Entry) trong các nến đã xét.",
            "Không phát sinh lệnh giao dịch thật, không có lãi/lỗ hay MFE/MAE."
        ]
        hypotheses = [
            "Giá có thể đã di chuyển thẳng tới mục tiêu mà không hồi về vùng Entry; có thể cân nhắc điểm vào linh hoạt hơn hoặc chờ setup tiếp theo."
        ]
        candidate_lesson = None
    else:
        mfe_r = round(mfe_usd / risk, 2)
        mae_r = round(mae_usd / risk, 2)

        if exit_status == "CLOSED_TP":
            status = "CLOSED_TP"
            outcome = "TARGET_REACHED (ĐẠT CHỐT LỜI TP)"
        elif exit_status == "CLOSED_SL":
            status = "CLOSED_SL"
            outcome = "STOPPED_OUT (CHẠM CẮT LỖ SL)"
        elif exit_status == "AMBIGUOUS":
            status = "AMBIGUOUS"
            outcome = "AMBIGUOUS (Biến động cùng nến chạm cả TP và SL)"
        else:
            status = "OPEN"
            outcome = "VỊ THẾ ĐANG MỞ (Chưa chạm TP hay SL khi hết phiên)"

        facts = [
            f"Kịch bản {direction} @ ${planned_entry:.2f}, SL ${sl:.2f}, TP ${tp:.2f}",
            f"Khớp lệnh tại nến thứ {fill_index + 1} sau thời điểm As-Of",
            f"Biên độ thuận lợi tối đa trong lệnh (MFE): +${mfe_usd:.2f} (+{mfe_r:.2f}R)",
            f"Biên độ bất lợi tối đa phải chịu (MAE): -${mae_usd:.2f} (-{mae_r:.2f}R)",
            f"Kết quả thực tế quan sát: {outcome}"
        ]

        hypotheses = []
        if status == "CLOSED_SL" and mfe_r >= 1.0:
            hypotheses.append(f"Lệnh từng đạt +{mfe_r}R trước khi quay đầu dính SL: Có thể thử nghiệm quy tắc dời SL về hòa vốn (Breakeven) khi đạt +1.0R.")
        elif status == "CLOSED_TP" and mae_r <= 0.3:
            hypotheses.append("Điểm vào lệnh rất chính xác, độ sụt giảm trước khi chạm TP rất nhỏ (MAE <= 0.3R).")
        elif status == "CLOSED_SL" and mae_r >= 1.0 and mfe_r < 0.3:
            hypotheses.append("Giá đi thẳng ngược chiều ngay sau khi khớp lệnh: Cần kiểm tra lại tin tức vĩ mô hoặc xu hướng khung thời gian lớn hơn.")

        candidate_lesson = {
            "title": f"Bài học quan sát phiên cho kịch bản {direction}",
            "status": "PENDING_REVIEW",
            "is_saved": False,
            "proposed_reflection": f"Kịch bản {direction} ({outcome}): MFE +{mfe_r}R / MAE -{mae_r}R.",
            "proposed_action": hypotheses[0] if hypotheses else "Duy trì theo dõi tính nhất quán của cấu trúc Swing.",
            "is_approved": False
        }

    return {
        "status": status,
        "is_filled": is_filled,
        "direction": direction,
        "planned_entry": planned_entry,
        "stop_loss": sl,
        "take_profit": tp,
        "mfe_usd": round(mfe_usd, 2),
        "mfe_r": mfe_r,
        "mae_usd": round(mae_usd, 2),
        "mae_r": mae_r,
        "outcome": outcome,
        "facts": facts,
        "hypotheses": hypotheses,
        "candidate_lesson": candidate_lesson
    }
