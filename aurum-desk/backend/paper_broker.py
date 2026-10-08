import time
import math
import uuid
from typing import Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session
import models, schemas, crud

class PaperBroker:
    """
    Simulated Paper Broker for XAUUSDT with strict risk controls:
    - 1,000 USDT capital base
    - 0.5% risk per trade (max 1.0%)
    - Max 3 fills per day (UTC+7)
    - 1 active position at a time
    - 30-min cooldown after closing a trade
    - Stop new trading for the day after 2 consecutive losses
    - Daily loss cap: 1.5% equity
    - Conservative execution: Buy at Ask + slippage, Sell at Bid - slippage
    """

    @staticmethod
    def can_open_position(db: Session, risk_usdt: float) -> Tuple[bool, str]:
        # 1. Check if there is already an open position
        active_pos = crud.get_active_position(db)
        if active_pos:
            return False, f"Đang có vị thế mở ({active_pos.direction} tại {active_pos.actual_entry:.2f}). Mặc định chỉ duy trì 1 vị thế."

        # 2. Check today's audit limits
        audit = crud.get_or_create_today_audit(db)
        now_ms = int(time.time() * 1000)

        if audit.is_blocked:
            return False, f"Tạm khóa giao dịch ngày: {audit.block_reason}"

        if audit.fills_count >= 3:
            return False, "Đã đạt giới hạn tối đa 3 lệnh/ngày (UTC+7)"

        if audit.consecutive_losses >= 2:
            return False, "Đã chạm ngưỡng dừng sau 2 lệnh lỗ liên tiếp trong ngày"

        if audit.cooldown_until and now_ms < audit.cooldown_until:
            remaining_sec = int((audit.cooldown_until - now_ms) / 1000)
            return False, f"Đang trong thời gian cooldown sau lệnh trước: còn {remaining_sec}s"

        # 3. Check daily loss cap (1.5%)
        daily_loss_budget = audit.initial_equity * 0.015
        projected_worst_loss = abs(audit.realized_pnl_today) + risk_usdt
        if audit.realized_pnl_today < 0 and projected_worst_loss > daily_loss_budget:
            return False, f"Rủi ro lệnh mới (${risk_usdt:.2f}) cộng lỗ đã ghi nhận (${abs(audit.realized_pnl_today):.2f}) vượt quá ngân sách lỗ ngày 1.5% (${daily_loss_budget:.2f})"

        return True, "Hợp lệ"

    @staticmethod
    def execute_market_order(
        db: Session,
        order_create: schemas.PaperOrderCreate,
        current_bid: float,
        current_ask: float
    ) -> models.PaperOrder:
        """
        Execute paper market entry with realistic bid/ask and slippage.
        Buy at Ask + slippage, Sell at Bid - slippage.
        """
        can_open, reason = PaperBroker.can_open_position(db, order_create.initial_risk_usdt)
        if not can_open:
            raise ValueError(f"Không thể mở lệnh: {reason}")

        slippage = 0.10  # 10 cents slippage on market execution
        if order_create.direction == "LONG":
            actual_entry = round(current_ask + slippage, 2)
        else:
            actual_entry = round(current_bid - slippage, 2)

        order_id = f"trade-{uuid.uuid4().hex[:8]}"
        order_create.actual_entry = actual_entry
        order_create.state = "paper_open"

        # Re-verify stop loss & gross RR based on actual entry
        if order_create.direction == "LONG":
            actual_risk = abs(actual_entry - order_create.stop_loss)
            actual_reward = abs(order_create.take_profit - actual_entry)
        else:
            actual_risk = abs(order_create.stop_loss - actual_entry)
            actual_reward = abs(actual_entry - order_create.take_profit)

        order_create.gross_rr = round(actual_reward / actual_risk, 2) if actual_risk > 0 else 2.0

        db_order = crud.create_paper_order(db, order_create, order_id)
        crud.record_trade_fill_audit(db)
        return db_order

    @staticmethod
    def process_price_tick(
        db: Session,
        current_bid: float,
        current_ask: float,
        candle_high: Optional[float] = None,
        candle_low: Optional[float] = None
    ) -> Optional[models.PaperOrder]:
        """
        Evaluate active paper position against latest price/candle tick:
        - Check TP and SL triggers
        - Handle conservative SL-first if ambiguous candle touches both TP and SL
        """
        active_pos = crud.get_active_position(db)
        if not active_pos:
            return None

        exit_triggered = False
        exit_price = 0.0
        exit_cause = ""

        # High & Low of the current bar if provided
        high_val = candle_high if candle_high is not None else max(current_bid, current_ask)
        low_val = candle_low if candle_low is not None else min(current_bid, current_ask)

        if active_pos.direction == "LONG":
            hit_sl = (low_val <= active_pos.stop_loss)
            hit_tp = (high_val >= active_pos.take_profit)

            if hit_sl and hit_tp:
                # Ambiguous bar: conservative rule assumes SL hit first!
                exit_triggered = True
                exit_price = active_pos.stop_loss
                exit_cause = "AMBIGUOUS_BAR_SL_FIRST"
            elif hit_sl:
                exit_triggered = True
                # Stop gap: exit at lowest observable price
                exit_price = min(active_pos.stop_loss, low_val)
                exit_cause = "SL_HIT"
            elif hit_tp:
                exit_triggered = True
                exit_price = active_pos.take_profit
                exit_cause = "TP_HIT"

        elif active_pos.direction == "SHORT":
            hit_sl = (high_val >= active_pos.stop_loss)
            hit_tp = (low_val <= active_pos.take_profit)

            if hit_sl and hit_tp:
                exit_triggered = True
                exit_price = active_pos.stop_loss
                exit_cause = "AMBIGUOUS_BAR_SL_FIRST"
            elif hit_sl:
                exit_triggered = True
                exit_price = max(active_pos.stop_loss, high_val)
                exit_cause = "SL_HIT"
            elif hit_tp:
                exit_triggered = True
                exit_price = active_pos.take_profit
                exit_cause = "TP_HIT"

        if exit_triggered:
            # Calculate Realized PnL Net and Realized R
            entry_p = active_pos.actual_entry or active_pos.planned_entry
            qty = active_pos.quantity
            direction_mult = 1.0 if active_pos.direction == "LONG" else -1.0
            gross_pnl = (exit_price - entry_p) * qty * direction_mult

            # Deduct fees (round-trip 0.04% maker/taker)
            fee_cost = (entry_p + exit_price) * qty * 0.0004
            net_pnl = round(gross_pnl - fee_cost, 2)
            realized_r = round(net_pnl / active_pos.initial_risk_usdt, 2) if active_pos.initial_risk_usdt > 0 else 0.0

            # Update Order in DB
            closed_order = crud.update_paper_order_state(
                db,
                order_id=active_pos.id,
                new_state="closed",
                actual_exit=round(exit_price, 2),
                realized_pnl=net_pnl,
                realized_r=realized_r,
                exit_cause=exit_cause
            )

            # Update Daily Audit
            crud.record_trade_close_audit(db, net_pnl)

            # Automatically record structured Lesson learned for the journal
            PaperBroker._create_post_trade_lesson(db, closed_order)
            return closed_order

        return None

    @staticmethod
    def _create_post_trade_lesson(db: Session, order: models.PaperOrder):
        """Generate structured lesson and reflection after trade closure"""
        now_ms = int(time.time() * 1000)
        pnl = order.realized_pnl_net or 0.0
        r_mult = order.realized_r or 0.0

        if pnl >= 0:
            title = f"Thắng {order.direction} +{r_mult}R (+${pnl:.2f}) theo cấu trúc SMC"
            reflection = f"Lệnh {order.direction} tuân thủ đúng quy tắc Sweep và FVG. TP tại {order.actual_exit:.2f} hoàn thành kỳ vọng."
            action_rule = "Tiếp tục duy trì tính kỷ luật chỉ mở lệnh khi có Liquidity Sweep rõ ràng."
        else:
            title = f"Dừng lỗ {order.direction} {r_mult}R (-${abs(pnl):.2f}) tại {order.actual_exit:.2f}"
            reflection = f"Lệnh chạm SL do {order.exit_cause}. Thị trường biến động mạnh hơn dự kiến."
            action_rule = "Kiểm tra lại biên độ buffer ATR và tránh vào lệnh gần vùng biến động mở phiên."

        db_lesson = models.Lesson(
            created_at=now_ms,
            title=title,
            category="EXECUTION",
            related_trade_id=order.id,
            setup_type=order.setup_id or "SMC_V1",
            session="ALL",
            reflection=reflection,
            action_rule=action_rule,
            is_hard_filter=False,
            is_approved=True
        )
        db.add(db_lesson)
        db.commit()
