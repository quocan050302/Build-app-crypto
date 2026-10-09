import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)
from sqlalchemy.orm import Session
import models, crud
from domain_calculator import validate_price_geometry, calculate_risk_reward
from services.trading_policy_service import TradingPolicyService


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
    Authoritative, shared V7 eligibility evaluator for upcoming setups, preview cards, and arm endpoints.
    Distinguishes:
    - strategy_state: READY, WAITING_RETRACE, WAITING_PRICE, etc.
    - execution_eligibility: can_arm (bool), can_execute_now (bool), reason_codes (List[str]), block_reasons (List[str])
    - trading policy: daily fill cap (3), NY window (08:00-11:00 NY), NY slot reservation.
    """
    current_time = now_ms if now_ms is not None else int(time.time() * 1000)
    current_dt = datetime.fromtimestamp(current_time / 1000.0, tz=timezone.utc)
    reason_codes: List[str] = []
    block_reasons: List[str] = []

    symbol = getattr(setup, "instrument", None) or getattr(setup, "symbol", None) or "XAUUSDT"
    policy = TradingPolicyService.get_active_policy(db, symbol)

    # 1. Strategy State check
    # WAITING_PRICE is general/unconfirmed, cannot arm.
    # WAITING_RETRACE can be armed as pending order only if prior structural evidence is verified.
    # READY can be armed and executed.
    if setup.state in ("INVALIDATED", "CANCELLED", "EXPIRED", "CLOSED", "REJECTED"):
        reason_codes.append("SETUP_TERMINAL")
        block_reasons.append(f"Setup đã kết thúc ({setup.state})")
    elif setup.state == "WAITING_PRICE":
        reason_codes.append("WAITING_STRUCTURE")
        block_reasons.append("Setup đang chờ điều kiện giá hình thành cấu trúc/sweep, chưa đủ bằng chứng để Arm.")
    elif setup.state not in ("READY", "WAITING_RETRACE"):
        reason_codes.append("STRATEGY_NOT_READY")
        block_reasons.append(f"Setup đang ở trạng thái {setup.state}, chưa đủ bằng chứng để Arm.")

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

    # 5. Trading Policy Evaluation (Daily 3 cap, NY window, slot reservation)
    policy_eval = TradingPolicyService.evaluate_entry_policy(db, symbol, current_dt)
    if not policy_eval["allowed"]:
        p_code = policy_eval["reason_code"]
        if p_code not in reason_codes:
            reason_codes.append(p_code)
            if p_code == "MAX_DAILY_ENTRIES":
                reason_codes.append("DAILY_FILL_CAP")
            block_reasons.append(f"{p_code}: {policy_eval['reason_message']}")

    # 6. Day Audit additional checks (consecutive losses, daily loss cap, cooldown)
    day_audit = crud.get_or_create_today_audit(db)
    if day_audit:
        if day_audit.fills_count >= 3 and "DAILY_FILL_CAP" not in reason_codes:
            reason_codes.append("DAILY_FILL_CAP")
            if "MAX_DAILY_ENTRIES" not in reason_codes:
                reason_codes.append("MAX_DAILY_ENTRIES")
            block_reasons.append("DAILY_FILL_CAP: Đã đạt giới hạn tối đa 3 lệnh khớp/ngày")
        if day_audit.consecutive_losses >= 2 and "MAX_CONSECUTIVE_LOSSES" not in reason_codes:
            reason_codes.append("MAX_CONSECUTIVE_LOSSES")
            block_reasons.append("MAX_CONSECUTIVE_LOSSES: Đã dừng giao dịch sau 2 lệnh lỗ liên tiếp")
        elif day_audit.is_blocked and "DAY_BLOCKED" not in reason_codes:
            reason_codes.append("DAY_BLOCKED")
            block_reasons.append(f"DAY_BLOCKED: {day_audit.block_reason}")
        elif day_audit.cooldown_until and current_time < day_audit.cooldown_until and "COOLDOWN_ACTIVE" not in reason_codes:
            rem_sec = int((day_audit.cooldown_until - current_time) / 1000)
            reason_codes.append("COOLDOWN_ACTIVE")
            block_reasons.append(f"COOLDOWN_ACTIVE: Đang trong thời gian nghỉ cooldown ({rem_sec}s)")

    # 7. News Blackout check
    is_blackout, blackout_reason, _ = crud.check_news_blackout(db, current_time)
    if is_blackout:
        reason_codes.append("NEWS_BLACKOUT")
        block_reasons.append(f"NEWS_BLACKOUT: {blackout_reason}")

    # 7.5. Execution Feed & Overload Gap check
    try:
        from services.execution_consumer import execution_consumer
        if execution_consumer.has_execution_gap or execution_consumer.is_lagging:
            reason_codes.append("EXECUTION_FEED_DEGRADED")
            block_reasons.append("EXECUTION_FEED_DEGRADED: Hàng đợi thực thi thị trường đang bị quá tải hoặc thiếu dữ liệu, tạm dừng Arm/vào lệnh mới.")
    except Exception:
        pass

    # 8. Price Geometry & Financial snapshot validation
    entry = custom_entry if custom_entry is not None else (setup.confirmed_entry or setup.provisional_entry)
    sl = custom_sl if custom_sl is not None else (setup.confirmed_sl or setup.provisional_sl)
    tp = custom_tp if custom_tp is not None else (setup.confirmed_tp or setup.provisional_tp)

    is_geom_valid, geom_err = validate_price_geometry(setup.direction, entry, sl, tp)
    if not is_geom_valid:
        reason_codes.append("INVALID_PRICE_GEOMETRY")
        block_reasons.append(f"INVALID_PRICE_GEOMETRY: {geom_err}")

    # Sizing & risk
    equity = day_audit.current_equity if day_audit else 1000.0
    effective_risk_pct = setup.risk_pct or 0.25
    if getattr(setup, "strategy_family", "STANDARD_SMC") == "NY_QUOTA_PAPER":
        effective_risk_pct = min(effective_risk_pct, getattr(policy, "ny_fallback_risk_pct_cap", 0.10) or 0.10)

    min_rr = getattr(policy, "min_net_rr", 2.0) or 2.0
    calc_res = calculate_risk_reward(
        direction=setup.direction,
        planned_entry=entry,
        stop_loss=sl,
        take_profit=tp,
        capital_usdt=equity,
        risk_pct=effective_risk_pct,
        min_net_rr=min_rr,
        leverage=setup.leverage or 5,
        margin_mode=effective_margin_mode
    )

    if not calc_res.is_valid:
        reason_codes.append("CALCULATOR_INVALID")
        block_reasons.append(f"CALCULATOR_INVALID: {calc_res.invalid_reason}")
    elif not calc_res.meets_min_rr:
        reason_codes.append("INSUFFICIENT_RR")
        block_reasons.append(f"INSUFFICIENT_RR: Tỷ lệ Net R:R (1:{calc_res.net_rr:.2f}) không đạt ngưỡng tối thiểu {min_rr:.1f} (Yêu cầu >= {min_rr:.1f})")
    elif not calc_res.can_execute and "CROSS_MARGIN_UNSUPPORTED" not in reason_codes:
        reason_codes.append("CANNOT_EXECUTE")
        block_reasons.append(calc_res.skip_reason or "Không đủ điều kiện thực thi")

    # 9. V10.1 Governed Lesson Rules Evaluation (BEFORE_ARM)
    from services.entry_decision_service import EntryDecisionService
    ev_dict = {}
    if getattr(setup, "evidence_snapshot_id", None):
        try:
            ev_row = db.query(models.StrategyEvidence).filter(models.StrategyEvidence.id == setup.evidence_snapshot_id).first()
            if ev_row:
                ev_dict = {
                    "sweep_detected": bool(ev_row.sweep_evidence),
                    "fvg_found": bool(ev_row.fvg_evidence),
                    "structure_confirmed": (ev_row.h1_alignment == "ALIGNED")
                }
        except Exception:
            pass

    cur_ask, cur_bid = None, None
    try:
        from services.collector_service import collector_service
        ticker = collector_service.latest_ticker
        if ticker and "ask" in ticker and "bid" in ticker:
            cur_ask = float(ticker["ask"])
            cur_bid = float(ticker["bid"])
    except Exception:
        pass

    lesson_context = EntryDecisionService.build_context(
        stage="BEFORE_ARM",
        symbol=symbol,
        direction=setup.direction,
        strategy_family=getattr(setup, "strategy_family", "STANDARD_SMC") or "STANDARD_SMC",
        timeframe=getattr(setup, "timeframe", "15M"),
        execution_mode="MANUAL",
        origin="MANUAL_WEB",
        planned_entry=entry,
        stop_loss=sl,
        take_profit=tp,
        net_rr=calc_res.net_rr,
        gross_rr=calc_res.gross_rr,
        bid=cur_bid,
        ask=cur_ask,
        now_ms=current_time,
        session_instance_id=policy_eval.get("session_instance_id"),
        evidence=ev_dict,
        distance_to_entry_atr=getattr(setup, "distance_to_entry_atr", None),
        setup_id=setup.id
    )

    lesson_eval = EntryDecisionService.evaluate_entry_rules(db, lesson_context)
    if not lesson_eval["can_proceed"]:
        for b_msg in lesson_eval["blocking_reasons"]:
            if "LESSON_RULE_DATA_UNAVAILABLE" in b_msg:
                code = "LESSON_RULE_DATA_UNAVAILABLE"
            elif "LESSON_EVALUATION_FAILED" in b_msg:
                code = "LESSON_EVALUATION_FAILED"
            else:
                code = "LESSON_RULE_BLOCKED"

            if code not in reason_codes:
                reason_codes.append(code)
            block_reasons.append(b_msg)

    can_arm = len(reason_codes) == 0
    can_execute_now = can_arm and (setup.state == "READY") and calc_res.can_execute

    entry_order_type = "MARKET" if setup.state == "READY" else "LIMIT"

    return {
        "setup_id": setup.id,
        "setup_instance_id": setup.setup_instance_id,
        "revision": getattr(setup, "revision", 1),
        "version": setup.version,
        "direction": setup.direction,
        "strategy_state": setup.state,
        "strategy_family": getattr(setup, "strategy_family", "STANDARD_SMC") or "STANDARD_SMC",
        "margin_mode": effective_margin_mode,
        "can_arm": can_arm,
        "can_execute": can_execute_now,  # Backward compatibility
        "can_execute_now": can_execute_now,
        "entry_order_type": entry_order_type,
        "reason_codes": reason_codes,
        "block_reason": block_reasons[0] if block_reasons else None,
        "block_reasons": block_reasons,
        "all_block_reasons": block_reasons,
        "session_instance_id": policy_eval.get("session_instance_id"),
        "policy_config_version": getattr(policy, "version", 1),
        "risk_config_version": 1,
        "daily_fill_count": policy_eval.get("daily_fills", 0),
        "remaining_daily_slots": policy_eval.get("remaining_daily_slots", 0),
        "ny_fill_count": policy_eval.get("ny_fills", 0),
        "ny_quota_status": policy_eval.get("quota_state", "NOT_STARTED"),
        "reserved_slots": policy_eval.get("reserved_slots", 0),
        "effective_financial_snapshot": {
            "entry": round(entry, 2),
            "sl": round(sl, 2),
            "tp": round(tp, 2),
            "gross_rr": round(calc_res.gross_rr, 2),
            "net_rr": round(calc_res.net_rr, 2),
            "quantity": calc_res.quantity,
            "risk_pct": effective_risk_pct,
            "initial_margin": calc_res.initial_margin_usdt,
            "estimated_liquidation": calc_res.estimated_liquidation,
            "leverage": calc_res.leverage,
            "margin_mode": calc_res.margin_mode
        },
        "evidence_snapshot_id": getattr(setup, "evidence_snapshot_id", None),
        "evaluated_at": current_time,
        # V10 Lesson Rules Details
        "lesson_advisories": lesson_eval["advisory_notes"],
        "lesson_warnings": lesson_eval["warning_messages"],
        "lesson_blockers": lesson_eval["blocking_reasons"],
        "lesson_evaluations": lesson_eval["evaluations"],
        "lessons_retrieved_snapshot": lesson_eval["lessons_retrieved_snapshot"]
    }
