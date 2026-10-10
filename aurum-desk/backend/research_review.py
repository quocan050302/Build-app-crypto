"""
Aurum Desk V13 — Post-Session Outcome Review & MFE/MAE Attribution Analytics.
Solves Section 10 requirements:
- Evaluates what happened strictly AFTER the research as_of timestamp
- Computes Maximum Favorable Excursion (MFE) and Maximum Adverse Excursion (MAE)
- Separates verified FACTS from analytical HYPOTHESES
- Generates draft lessons in PENDING_REVIEW governance state (no auto-activation)
"""

from typing import Dict, Any, List, Optional


def evaluate_scenario_outcome_and_mfe_mae(
    scenario: Dict[str, Any],
    candles_after_asof: List[Dict[str, Any]],
    initial_risk_usd: Optional[float] = None
) -> Dict[str, Any]:
    """
    Evaluates scenario performance after as_of using subsequent closed candles.
    """
    direction = scenario.get("direction", "LONG")
    planned_entry = float(scenario.get("planned_entry", 0.0))
    sl = float(scenario.get("stop_loss", 0.0))
    tp = float(scenario.get("take_profit", 0.0))

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
            "outcome": "NO_EVALUATION_DATA",
            "facts": [],
            "hypotheses": []
        }

    highs = [c["high"] for c in candles_after_asof]
    lows = [c["low"] for c in candles_after_asof]

    if direction == "LONG":
        highest_favorable = max(highs)
        lowest_adverse = min(lows)
        mfe_usd = max(0.0, highest_favorable - planned_entry)
        mae_usd = max(0.0, planned_entry - lowest_adverse)

        # Check outcome sequence
        outcome = "OPEN_OR_EXPIRED"
        for c in candles_after_asof:
            hit_tp = c["high"] >= tp
            hit_sl = c["low"] <= sl
            if hit_tp and hit_sl:
                outcome = "AMBIGUOUS_SAME_BAR_EXIT"
                break
            elif hit_tp:
                outcome = "TARGET_REACHED (ĐẠT CHỐT LỜI)"
                break
            elif hit_sl:
                outcome = "STOPPED_OUT (CHẠM DỪNG LỖ)"
                break
    else: # SHORT
        lowest_favorable = min(lows)
        highest_adverse = max(highs)
        mfe_usd = max(0.0, planned_entry - lowest_favorable)
        mae_usd = max(0.0, highest_adverse - planned_entry)

        outcome = "OPEN_OR_EXPIRED"
        for c in candles_after_asof:
            hit_tp = c["low"] <= tp
            hit_sl = c["high"] >= sl
            if hit_tp and hit_sl:
                outcome = "AMBIGUOUS_SAME_BAR_EXIT"
                break
            elif hit_tp:
                outcome = "TARGET_REACHED (ĐẠT CHỐT LỜI)"
                break
            elif hit_sl:
                outcome = "STOPPED_OUT (CHẠM DỪNG LỖ)"
                break

    mfe_r = round(mfe_usd / risk, 2)
    mae_r = round(mae_usd / risk, 2)

    facts = [
        f"Kịch bản dự kiến {direction} @ ${planned_entry:.2f}, SL ${sl:.2f}, TP ${tp:.2f}",
        f"Biên độ thuận lợi cực đại (MFE): +${mfe_usd:.2f} (+{mfe_r:.2f}R)",
        f"Biên độ bất lợi cực đại (MAE): -${mae_usd:.2f} (-{mae_r:.2f}R)",
        f"Kết quả thực tế quan sát được: {outcome}"
    ]

    hypotheses = []
    if mfe_r >= 1.5 and "STOPPED_OUT" in outcome:
        hypotheses.append("Giá đã từng di chuyển thuận lợi trên +1.5R trước khi đảo chiều cán SL: Có thể cân nhắc dời SL về hòa vốn (BE) sau khi đạt +1.0R")
    elif mae_r <= 0.3 and "TARGET_REACHED" in outcome:
        hypotheses.append("Điểm vào lệnh rất chính xác, độ drawdown trước khi đạt TP cực thấp (MAE < 0.3R)")
    elif "STOPPED_OUT" in outcome and mae_r >= 1.0:
        hypotheses.append("Lực đẩy ngược chiều mạnh ngay sau thời điểm phân tích: Cần kiểm tra lại tin tức kinh tế hoặc xung đột khung thời gian lớn")

    return {
        "status": "EVALUATED",
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
        "candidate_lesson": {
            "title": f"Bài học quan sát phiên cho kịch bản {direction}",
            "status": "PENDING_REVIEW",
            "proposed_reflection": f"Kết quả kịch bản {direction}: MFE +{mfe_r}R / MAE -{mae_r}R. {outcome}.",
            "proposed_action": hypotheses[0] if hypotheses else "Tiếp tục theo dõi và kiểm tra tính nhất quán của vùng hỗ trợ/kháng cự.",
            "is_approved": False
        }
    }
