import time
import math
import uuid
from typing import Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session
import models, schemas, crud
from domain_calculator import calculate_risk_reward, CostAssumptions

class PaperBroker:
    """
    Simulated Paper Broker for XAUUSDT with strict risk controls:
    - 1,000 USDT capital base (or real current equity)
    - Default 0.25% risk per trade (hard cap 0.5%)
    - Max 3 fills per day (UTC+7)
    - 1 active position at a time
    - 30-min cooldown after closing a trade
    - Stop new trading for the day after 2 consecutive losses
    - Daily loss cap: 1.5% starting equity
    - Conservative execution: Buy at Ask + slippage, Sell at Bid - slippage
    - Authoritative backend recalculation of all geometry, quantity, risk, Net RR, leverage, margin, and liquidation
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

        # 3. Check daily loss cap (1.5% of starting equity)
        daily_loss_budget = audit.initial_equity * 0.015
        current_realized_loss = max(0.0, -audit.realized_pnl_today)
        projected_worst_loss = current_realized_loss + risk_usdt

        if projected_worst_loss > daily_loss_budget:
            return False, f"Rủi ro lệnh mới (${risk_usdt:.2f}) cộng lỗ đã ghi nhận (${current_realized_loss:.2f}) vượt quá ngân sách lỗ ngày 1.5% (${daily_loss_budget:.2f})"

        return True, "Hợp lệ"

    @staticmethod
    def execute_market_order(
        db: Session,
        order_create: schemas.PaperOrderCreate,
        current_bid: float,
        current_ask: float
    ) -> models.PaperOrder:
        """
        Execute paper market entry with authoritative risk/reward, sizing, leverage, and liquidation verification.
        Client payload is treated as untrusted intent; backend is the authoritative decider.
        Eliminates double-counted slippage by passing entry_has_slippage=True on actual_entry.
        """
        audit = crud.get_or_create_today_audit(db)
        slippage = 0.10  # 10 cents slippage on market execution

        # 1. Calculate projected fill price
        if order_create.direction == "LONG":
            actual_entry = round(current_ask + slippage, 2)
            if actual_entry <= order_create.stop_loss:
                raise ValueError(f"Giá khớp thực tế ({actual_entry:.2f}) nằm dưới hoặc bằng SL ({order_create.stop_loss:.2f})")
            if actual_entry >= order_create.take_profit:
                raise ValueError(f"Giá khớp thực tế ({actual_entry:.2f}) nằm trên hoặc bằng TP ({order_create.take_profit:.2f})")
        elif order_create.direction == "SHORT":
            actual_entry = round(current_bid - slippage, 2)
            if actual_entry >= order_create.stop_loss:
                raise ValueError(f"Giá khớp thực tế ({actual_entry:.2f}) nằm trên hoặc bằng SL ({order_create.stop_loss:.2f})")
            if actual_entry <= order_create.take_profit:
                raise ValueError(f"Giá khớp thực tế ({actual_entry:.2f}) nằm dưới hoặc bằng TP ({order_create.take_profit:.2f})")
        else:
            raise ValueError(f"Hướng giao dịch không hợp lệ: {order_create.direction}")

        # 2. Authoritative Domain Calculation with entry_has_slippage=True to prevent double-count
        risk_pct = min(0.5, order_create.risk_pct or 0.25)
        leverage = getattr(order_create, 'leverage', 5) or 5
        margin_mode = getattr(order_create, 'margin_mode', 'ISOLATED') or 'ISOLATED'

        calc_result = calculate_risk_reward(
            direction=order_create.direction,
            entry=actual_entry,
            sl=order_create.stop_loss,
            tp=order_create.take_profit,
            capital=audit.current_equity,
            risk_pct=risk_pct,
            min_net_rr=2.0,
            quantity_override=getattr(order_create, 'quantity_override', None) if hasattr(order_create, 'quantity_override') else None,
            entry_has_slippage=True,  # Slippage is already embedded in actual_entry!
            leverage=leverage,
            margin_mode=margin_mode
        )

        if not calc_result.is_valid:
            raise ValueError(f"Lỗi cấu trúc giá: {calc_result.invalid_reason}")

        if not calc_result.can_execute:
            raise ValueError(f"Lệnh bị từ chối do không đủ điều kiện: {calc_result.skip_reason}")

        # 3. Check account risk limits using authoritative net risk
        can_open, reason = PaperBroker.can_open_position(db, calc_result.net_risk_usdt)
        if not can_open:
            raise ValueError(f"Không thể mở lệnh: {reason}")

        # 4. Populate authoritative values
        order_id = f"trade-{uuid.uuid4().hex[:8]}"
        order_create.actual_entry = actual_entry
        order_create.quantity = calc_result.quantity
        order_create.initial_risk_usdt = calc_result.net_risk_usdt
        order_create.risk_pct = calc_result.effective_risk_pct
        order_create.gross_rr = calc_result.gross_rr
        order_create.estimated_net_rr = calc_result.net_rr
        order_create.state = "candidate"

        db_order = crud.create_paper_order(db, order_create, order_id, commit=False)
        db_order.leverage = calc_result.leverage
        db_order.margin_mode = calc_result.margin_mode
        db_order.estimated_liquidation = calc_result.estimated_liquidation
        db_order.initial_margin = calc_result.initial_margin_usdt

        from services.trade_lifecycle_service import TradeLifecycleService
        return TradeLifecycleService.execute_fill(
            db=db,
            order=db_order,
            fill_price=actual_entry,
            calc_result=calc_result,
            source="MANUAL"
        )

    @staticmethod
    def amend_open_position(
        db: Session,
        order_id: str,
        new_sl: Optional[float] = None,
        new_tp: Optional[float] = None
    ) -> models.PaperOrder:
        """
        Amend SL / TP of an open position with strict risk guards:
        - Position must be open
        - Stop loss cannot be widened to increase risk
        - TP must remain on the valid side of Entry
        - Stop loss cannot breach or approach estimated liquidation price
        """
        active_pos = crud.get_paper_order(db, order_id)
        if not active_pos or active_pos.state != "paper_open":
            raise ValueError("Không tìm thấy vị thế mở để điều chỉnh")

        entry = active_pos.actual_entry or active_pos.planned_entry
        lp = active_pos.estimated_liquidation

        if new_sl is not None:
            if active_pos.direction == "LONG":
                if new_sl < active_pos.stop_loss:
                    raise ValueError(f"Không được nới rộng SL ({new_sl:.2f} < {active_pos.stop_loss:.2f}) làm tăng rủi ro")
                if new_sl >= active_pos.take_profit:
                    raise ValueError(f"SL ({new_sl:.2f}) không được vượt qua TP ({active_pos.take_profit:.2f})")
                if lp is not None and new_sl <= lp:
                    raise ValueError(f"SL ({new_sl:.2f}) không được nằm dưới hoặc bằng giá thanh lý ước tính ({lp:.2f})")
            elif active_pos.direction == "SHORT":
                if new_sl > active_pos.stop_loss:
                    raise ValueError(f"Không được nới rộng SL ({new_sl:.2f} > {active_pos.stop_loss:.2f}) làm tăng rủi ro")
                if new_sl <= active_pos.take_profit:
                    raise ValueError(f"SL ({new_sl:.2f}) không được vượt qua TP ({active_pos.take_profit:.2f})")
                if lp is not None and new_sl >= lp:
                    raise ValueError(f"SL ({new_sl:.2f}) không được nằm trên hoặc bằng giá thanh lý ước tính ({lp:.2f})")
            active_pos.stop_loss = round(new_sl, 2)

        if new_tp is not None:
            if active_pos.direction == "LONG":
                if new_tp <= entry:
                    raise ValueError(f"TP ({new_tp:.2f}) phải cao hơn Entry ({entry:.2f})")
            elif active_pos.direction == "SHORT":
                if new_tp >= entry:
                    raise ValueError(f"TP ({new_tp:.2f}) phải thấp hơn Entry ({entry:.2f})")
            active_pos.take_profit = round(new_tp, 2)

        db.commit()
        db.refresh(active_pos)
        return active_pos

    @staticmethod
    def process_price_tick(
        db: Session,
        current_bid: float,
        current_ask: float,
        candle_high: Optional[float] = None,
        candle_low: Optional[float] = None,
        candle_timestamp: Optional[int] = None
    ) -> Optional[models.PaperOrder]:
        """
        Evaluate active paper position against latest price/candle tick via unified TradeLifecycleService.
        """
        from services.trade_lifecycle_service import TradeLifecycleService
        return TradeLifecycleService.process_exit_tick(
            db=db,
            current_bid=current_bid,
            current_ask=current_ask,
            candle_high=candle_high,
            candle_low=candle_low,
            candle_timestamp=candle_timestamp
        )

    @staticmethod
    def _create_post_trade_lesson(db: Session, order: models.PaperOrder):
        """Generate structured lesson and reflection after trade closure"""
        from services.trade_lifecycle_service import TradeLifecycleService
        now_ms = int(time.time() * 1000)
        TradeLifecycleService._create_lesson(db, order, now_ms)
        db.commit()
