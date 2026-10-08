import time
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session
import models, crud
from domain_calculator import validate_price_geometry, calculate_risk_reward

def evaluate_setup_eligibility(
    db: Session,
    setup: models.WatchSetup,
    custom_entry: Optional[float] = None,
    custom_sl: Optional[float] = None,
    custom_tp: Optional[float] = None,
    custom_margin_mode: Optional[str] = None,
    now_ms: Optional[int] = None
) -> Dict[str, Any]:
    """
    Authoritative, shared eligibility evaluator for upcoming setups, preview cards, and arm endpoints.
    Distinguishes:
    - strategy_state: READY, WAITING_PRICE, WAITING_RETRACE, etc.
    - execution_eligibility: can_arm (bool), can_execute (bool), reason_codes (List[str]), block_reason (Optional[str])
    - order_lifecycle: terminal, armed, open
    """
    current_time = now_ms if now_ms is not None else int(time.time() * 1000)
    reason_codes: List[str] = []
    block_reasons: List[str] = []

    # 1. State check
    if setup.state in ("INVALIDATED", "CANCELLED", "EXPIRED", "CLOSED", "REJECTED"):
        reason_codes.append("SETUP_TERMINAL")
        block_reasons.append(f"Setup đã kết thúc ({setup.state})")
    elif setup.state not in ("READY", "WAITING_PRICE", "WAITING_RETRACE"):
        reason_codes.append("STRATEGY_NOT_READY")
        block_reasons.append(f"Setup đang ở trạng thái {setup.state}, chưa đủ điều kiện vào lệnh.")

    # 2. Margin Mode check (Cross is unsupported for paper execution)
    effective_margin_mode = custom_margin_mode or setup.margin_mode or "ISOLATED"
    if effective_margin_mode.upper() == "CROSS":
        reason_codes.append("CROSS_MARGIN_UNSUPPORTED")
        block_reasons.append("CROSS_MARGIN_UNSUPPORTED: Chế độ Cross margin chưa được hỗ trợ thực thi trên tài khoản paper (Cần Isolated)")

    # 3. Active Position check (Strict invariant: Max 1 open position)
    active_pos = crud.get_active_position(db)
    if active_pos:
        reason_codes.append("ACTIVE_POSITION_EXISTS")
        block_reasons.append("ACTIVE_POSITION_EXISTS: Đang có một vị thế mở, không thể Arm thêm lệnh mới (tối đa 1 vị thế)")

    # 4. Armed Order check (Strict invariant: Max 1 armed order)
    armed_order = db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").first()
    if armed_order:
        reason_codes.append("ARMED_ORDER_EXISTS")
        block_reasons.append(f"ARMED_ORDER_EXISTS: Đã có một lệnh đang chờ khớp ({armed_order.id})")

    # 5. Day Limits & Risk Cap (3 fills/day, 2 consecutive losses)
    day_audit = crud.get_or_create_today_audit(db)
    if day_audit:
        if day_audit.fills_count >= 3:
            reason_codes.append("MAX_DAILY_ENTRIES")
            block_reasons.append("MAX_DAILY_ENTRIES: Đạt giới hạn tối đa 3 lệnh/ngày (UTC+7)")
        elif day_audit.consecutive_losses >= 2:
            reason_codes.append("MAX_CONSECUTIVE_LOSSES")
            block_reasons.append("MAX_CONSECUTIVE_LOSSES: Đã dừng giao dịch sau 2 lệnh lỗ liên tiếp")
        elif day_audit.is_blocked:
            reason_codes.append("DAY_BLOCKED")
            block_reasons.append(f"DAY_BLOCKED: {day_audit.block_reason}")
        elif day_audit.cooldown_until and current_time < day_audit.cooldown_until:
            rem_sec = int((day_audit.cooldown_until - current_time) / 1000)
            reason_codes.append("COOLDOWN_ACTIVE")
            block_reasons.append(f"COOLDOWN_ACTIVE: Đang trong thời gian nghỉ cooldown ({rem_sec}s)")

    # 6. News Blackout check
    is_blackout, blackout_reason, _ = crud.check_news_blackout(db, current_time)
    if is_blackout:
        reason_codes.append("NEWS_BLACKOUT")
        block_reasons.append(f"NEWS_BLACKOUT: {blackout_reason}")

    # 7. Price Geometry & Financial snapshot validation
    entry = custom_entry if custom_entry is not None else (setup.confirmed_entry or setup.provisional_entry)
    sl = custom_sl if custom_sl is not None else (setup.confirmed_sl or setup.provisional_sl)
    tp = custom_tp if custom_tp is not None else (setup.confirmed_tp or setup.provisional_tp)

    is_geom_valid, geom_err = validate_price_geometry(setup.direction, entry, sl, tp)
    if not is_geom_valid:
        reason_codes.append("INVALID_PRICE_GEOMETRY")
        block_reasons.append(f"INVALID_PRICE_GEOMETRY: {geom_err}")

    equity = day_audit.current_equity if day_audit else 1000.0
    calc_res = calculate_risk_reward(
        direction=setup.direction,
        planned_entry=entry,
        stop_loss=sl,
        take_profit=tp,
        capital_usdt=equity,
        risk_pct=setup.risk_pct or 0.25,
        leverage=setup.leverage or 5,
        margin_mode=effective_margin_mode
    )

    if not calc_res.is_valid:
        reason_codes.append("CALCULATOR_INVALID")
        block_reasons.append(f"CALCULATOR_INVALID: {calc_res.invalid_reason}")
    elif not calc_res.meets_min_rr:
        reason_codes.append("INSUFFICIENT_RR")
        block_reasons.append(f"INSUFFICIENT_RR: Tỷ lệ Net R:R (1:{calc_res.net_rr:.2f}) không đạt ngưỡng tối thiểu 2.0 (Yêu cầu >= 2.0)")
    elif not calc_res.can_execute and "CROSS_MARGIN_UNSUPPORTED" not in reason_codes:
        reason_codes.append("CANNOT_EXECUTE")
        block_reasons.append(calc_res.skip_reason or "Không đủ điều kiện thực thi")

    can_arm = len(reason_codes) == 0
    can_execute = can_arm and calc_res.can_execute

    return {
        "setup_id": setup.id,
        "setup_instance_id": setup.setup_instance_id,
        "version": setup.version,
        "direction": setup.direction,
        "strategy_state": setup.state,
        "margin_mode": effective_margin_mode,
        "can_arm": can_arm,
        "can_execute": can_execute,
        "reason_codes": reason_codes,
        "block_reason": block_reasons[0] if block_reasons else None,
        "block_reasons": block_reasons,
        "all_block_reasons": block_reasons,
        "effective_financial_snapshot": {
            "entry": round(entry, 2),
            "sl": round(sl, 2),
            "tp": round(tp, 2),
            "gross_rr": round(calc_res.gross_rr, 2),
            "net_rr": round(calc_res.net_rr, 2),
            "quantity": calc_res.quantity,
            "initial_margin": calc_res.initial_margin_usdt,
            "estimated_liquidation": calc_res.estimated_liquidation,
            "leverage": calc_res.leverage,
            "margin_mode": calc_res.margin_mode
        },
        "evaluated_at": current_time
    }
