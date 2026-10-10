"""
Aurum Desk V13 — Causal & Validated Trade Scenario Builder.
Solves Section 8 requirements:
- Validates strictly: LONG (SL < Entry < TP), SHORT (TP < Entry < SL)
- Calculates gross RR and net RR with exact CostAssumptions
- Invalid scenarios are marked is_valid=False with clear Vietnamese reasons
- Beginner-friendly explanations without raw constant codes
"""

from typing import Dict, Any, List, Optional
from domain_calculator import CostAssumptions, validate_price_geometry, calculate_risk_reward


def build_validated_scenarios(
    current_price: float,
    atr_val: float,
    swing_high: float,
    swing_low: float,
    equilibrium: float,
    regime: str,
    costs: Optional[CostAssumptions] = None
) -> Dict[str, Any]:
    """
    Build and validate Bullish, Bearish, and No-Trade scenarios.
    Ensures mathematical and economic correctness.
    """
    if costs is None:
        costs = CostAssumptions(
            taker_fee_rate=0.0006,
            maker_fee_rate=0.0002,
            slippage_usd=0.10
        )

    # 1. Bullish Scenario (LONG)
    # Entry zone: around pullback / discount below equilibrium or breakout retest
    long_entry = round(current_price - 0.25 * atr_val if current_price > equilibrium else current_price, 2)
    # SL placed safely below swing low with 0.5 ATR buffer
    long_sl = round(min(swing_low - 0.5 * atr_val, long_entry - 1.2 * atr_val), 2)
    # TP placed at Swing High or 2.5x risk
    long_risk = long_entry - long_sl
    long_tp = round(max(swing_high, long_entry + 2.2 * long_risk), 2)

    # Validate LONG geometry
    long_is_valid = True
    long_invalidation_reasons = []

    if long_sl >= long_entry:
        long_is_valid = False
        long_invalidation_reasons.append(f"Lỗi hình học: Điểm dừng lỗ (${long_sl:.2f}) không thể lớn hơn hoặc bằng điểm vào lệnh (${long_entry:.2f})")

    if long_tp <= long_entry:
        long_is_valid = False
        long_invalidation_reasons.append(f"Lỗi hình học: Mục tiêu chốt lời (${long_tp:.2f}) không thể nhỏ hơn hoặc bằng điểm vào lệnh (${long_entry:.2f})")

    long_gross_rr = 0.0
    long_net_rr = 0.0
    if long_is_valid and long_risk > 0:
        long_reward = long_tp - long_entry
        long_gross_rr = round(long_reward / long_risk, 2)
        # Calculate net RR
        total_costs_long = 0.20 + 2 * costs.slippage_usd + long_entry * (costs.taker_fee_rate + costs.maker_fee_rate)
        long_net_rr = round((long_reward - total_costs_long) / (long_risk + total_costs_long), 2)

        if long_net_rr < 1.8:
            long_is_valid = False
            long_invalidation_reasons.append(f"Tỷ lệ Net R:R sau chi phí ({long_net_rr:.2f}R) không đạt chuẩn tối thiểu >= 1.80R")

    bullish_scenario = {
        "title": "Kịch Bản Mua Lên (Bullish Scenario)",
        "direction": "LONG",
        "is_valid": long_is_valid,
        "entry_zone": f"${long_entry:.2f} (Vùng Discount / Retest)",
        "planned_entry": long_entry,
        "stop_loss": long_sl,
        "take_profit": long_tp,
        "gross_rr": long_gross_rr,
        "net_rr": long_net_rr,
        "estimated_risk_usd": round(long_risk, 2),
        "trigger_condition": f"Giá kiểm tra vùng hỗ trợ ${long_sl + 0.5*atr_val:.2f}, xuất hiện nến 5M rút chân tăng và đóng cửa trên ${long_entry:.2f}",
        "invalidation_level": long_sl,
        "invalidation_reasons": long_invalidation_reasons,
        "action_guide": "Đợi giá hoàn thành bước kiểm định và có tín hiệu xác nhận trước khi kích hoạt lệnh; không mua đuổi đỉnh." if long_is_valid else "Kịch bản chưa đủ điều kiện giao dịch an toàn (đứng ngoài)."
    }

    # 2. Bearish Scenario (SHORT)
    short_entry = round(current_price + 0.25 * atr_val if current_price < equilibrium else current_price, 2)
    # SL placed safely above swing high with 0.5 ATR buffer
    short_sl = round(max(swing_high + 0.5 * atr_val, short_entry + 1.2 * atr_val), 2)
    short_risk = short_sl - short_entry
    short_tp = round(min(swing_low, short_entry - 2.2 * short_risk), 2)

    # Validate SHORT geometry
    short_is_valid = True
    short_invalidation_reasons = []

    if short_sl <= short_entry:
        short_is_valid = False
        short_invalidation_reasons.append(f"Lỗi hình học: Điểm dừng lỗ (${short_sl:.2f}) không thể nhỏ hơn hoặc bằng điểm vào lệnh (${short_entry:.2f})")

    if short_tp >= short_entry:
        short_is_valid = False
        short_invalidation_reasons.append(f"Lỗi hình học: Mục tiêu chốt lời (${short_tp:.2f}) không thể lớn hơn hoặc bằng điểm vào lệnh (${short_entry:.2f})")

    short_gross_rr = 0.0
    short_net_rr = 0.0
    if short_is_valid and short_risk > 0:
        short_reward = short_entry - short_tp
        short_gross_rr = round(short_reward / short_risk, 2)
        total_costs_short = 0.20 + 2 * costs.slippage_usd + short_entry * (costs.taker_fee_rate + costs.maker_fee_rate)
        short_net_rr = round((short_reward - total_costs_short) / (short_risk + total_costs_short), 2)

        if short_net_rr < 1.8:
            short_is_valid = False
            short_invalidation_reasons.append(f"Tỷ lệ Net R:R sau chi phí ({short_net_rr:.2f}R) không đạt chuẩn tối thiểu >= 1.80R")

    bearish_scenario = {
        "title": "Kịch Bản Bán Xuống (Bearish Scenario)",
        "direction": "SHORT",
        "is_valid": short_is_valid,
        "entry_zone": f"${short_entry:.2f} (Vùng Premium / Rejection)",
        "planned_entry": short_entry,
        "stop_loss": short_sl,
        "take_profit": short_tp,
        "gross_rr": short_gross_rr,
        "net_rr": short_net_rr,
        "estimated_risk_usd": round(short_risk, 2),
        "trigger_condition": f"Giá kiểm tra vùng kháng cự ${short_sl - 0.5*atr_val:.2f}, xuất hiện phản ứng từ chối giá (wick rejection) và nến 5M đóng dưới ${short_entry:.2f}",
        "invalidation_level": short_sl,
        "invalidation_reasons": short_invalidation_reasons,
        "action_guide": "Đợi giá chạm vùng cản và tạo nến xác nhận giảm trước khi vào lệnh; tuyệt đối không bán tháo khi giá chạm hỗ trợ." if short_is_valid else "Kịch bản chưa đủ điều kiện giao dịch an toàn (đứng ngoài)."
    }

    # 3. No-Trade Scenario (Always Active)
    no_trade_reasons = []
    if regime == "EVENT_VOLATILITY":
        no_trade_reasons.append("Thị trường đang có biến động mạnh bất thường hoặc tin tức lớn")
    elif regime == "RANGE":
        no_trade_reasons.append("Giá đang tích lũy đi ngang trong biên độ hẹp, chưa có xu hướng bứt phá")
    elif not long_is_valid and not short_is_valid:
        no_trade_reasons.append("Cả kịch bản Mua và Bán đều không đạt tỷ lệ Net R:R tối thiểu 1.80R sau khi trừ phí giao dịch và trượt giá")
    else:
        no_trade_reasons.append("Khi chưa xuất hiện đồng thời Liquidity Sweep và nến xác nhận trên khung 5M")

    no_trade_scenario = {
        "title": "Kịch Bản Chờ Đợi / Không Giao Dịch (No-Trade Stance)",
        "is_active": True,
        "reasons": no_trade_reasons,
        "capital_preservation_message": "Bảo vệ vốn là ưu tiên số một. Chỉ vào lệnh khi tất cả điều kiện kỹ thuật và tỷ lệ R:R được thỏa mãn đầy đủ.",
        "quota_compliance": "Tuân thủ giới hạn tối đa 3 lệnh/ngày và mục tiêu 1 cơ hội chất lượng trong phiên Mỹ."
    }

    return {
        "bullish": bullish_scenario,
        "bearish": bearish_scenario,
        "no_trade": no_trade_scenario
    }
