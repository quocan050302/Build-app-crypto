import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import text, desc
import models, schemas

from services.clock import live_clock, IClock

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

def get_today_str_vn(clock: Optional[IClock] = None) -> str:
    """Returns today's date string in Asia/Ho_Chi_Minh (UTC+7) timezone: YYYY-MM-DD"""
    c = clock or live_clock
    return c.get_today_str_vn()

# ==================== CANDLES CRUD ====================

def get_candles(db: Session, symbol: str, timeframe: str, limit: int = 200, ascending: bool = True) -> List[models.Candle]:
    """Retrieve candles from SQLite ordered chronologically if ascending=True"""
    order = models.Candle.timestamp.asc() if ascending else models.Candle.timestamp.desc()
    candles = (
        db.query(models.Candle)
        .filter(models.Candle.symbol == symbol, models.Candle.timeframe == timeframe)
        .order_by(models.Candle.timestamp.desc())
        .limit(limit)
        .all()
    )
    if ascending:
        candles.reverse()
    return candles

def bulk_upsert_candles(db: Session, candles: List[schemas.CandleCreate]) -> int:
    """
    Atomic SQLite upsert using ON CONFLICT(symbol, timeframe, timestamp) DO UPDATE.
    High performance, no duplicates, safe under concurrent calls.
    """
    if not candles:
        return 0

    stmt = text("""
        INSERT INTO candles (symbol, timeframe, timestamp, open, high, low, close, volume, is_closed)
        VALUES (:symbol, :timeframe, :timestamp, :open, :high, :low, :close, :volume, :is_closed)
        ON CONFLICT(symbol, timeframe, timestamp) DO UPDATE SET
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            volume = excluded.volume,
            is_closed = excluded.is_closed;
    """)

    params = [c.model_dump() for c in candles]
    db.execute(stmt, params)
    db.commit()
    return len(candles)

# ==================== PAPER ORDERS CRUD ====================

def create_paper_order(db: Session, order: schemas.PaperOrderCreate, order_id: str, commit: bool = True) -> models.PaperOrder:
    now_ms = int(time.time() * 1000)
    db_order = models.PaperOrder(
        id=order_id,
        setup_id=order.setup_id,
        signal_id=order.signal_id,
        instrument=order.instrument,
        direction=order.direction,
        state=order.state,
        order_type=order.order_type,
        timeframe=order.timeframe,
        planned_entry=order.planned_entry,
        actual_entry=order.actual_entry,
        stop_loss=order.stop_loss,
        take_profit=order.take_profit,
        quantity=order.quantity,
        initial_risk_usdt=order.initial_risk_usdt,
        risk_pct=order.risk_pct,
        gross_rr=order.gross_rr,
        estimated_net_rr=order.estimated_net_rr,
        fees_assumption=order.fees_assumption,
        slippage_assumption=order.slippage_assumption,
        strategy_version=order.strategy_version,
        created_at=now_ms,
        armed_at=now_ms if order.state in ("armed", "paper_open") else None,
        opened_at=now_ms if order.state == "paper_open" else None,
        checklist_snapshot=order.checklist_snapshot,
        lessons_retrieved=order.lessons_retrieved,
        idempotency_key=getattr(order, "idempotency_key", None),
        setup_instance_id=getattr(order, "setup_instance_id", None)
    )
    db.add(db_order)
    if commit:
        db.commit()
        db.refresh(db_order)
    else:
        db.flush()
    return db_order

def get_paper_order(db: Session, order_id: str) -> Optional[models.PaperOrder]:
    return db.query(models.PaperOrder).filter(models.PaperOrder.id == order_id).first()

def get_active_position(db: Session) -> Optional[models.PaperOrder]:
    """Return the currently open paper position (max 1 position enforced)"""
    return db.query(models.PaperOrder).filter(models.PaperOrder.state == "paper_open").first()

def get_latest_signal(db: Session) -> Optional[models.PaperOrder]:
    """Return the latest candidate, armed, or open paper order"""
    return (
        db.query(models.PaperOrder)
        .filter(models.PaperOrder.state.in_(["candidate", "armed", "paper_open"]))
        .order_by(models.PaperOrder.created_at.desc())
        .first()
    )

def list_paper_orders(db: Session, limit: int = 50) -> List[models.PaperOrder]:
    return (
        db.query(models.PaperOrder)
        .order_by(models.PaperOrder.created_at.desc())
        .limit(limit)
        .all()
    )

def update_paper_order_state(
    db: Session,
    order_id: str,
    new_state: str,
    actual_exit: Optional[float] = None,
    realized_pnl: Optional[float] = None,
    realized_r: Optional[float] = None,
    exit_cause: Optional[str] = None,
    invalidation_reason: Optional[str] = None,
    commit: bool = True
) -> Optional[models.PaperOrder]:
    order = get_paper_order(db, order_id)
    if not order:
        return None

    now_ms = int(time.time() * 1000)
    order.state = new_state
    if new_state == "paper_open" and not order.opened_at:
        order.opened_at = now_ms
    elif new_state in ("closed", "expired", "invalidated", "cancelled"):
        order.closed_at = now_ms
        if actual_exit is not None:
            order.actual_exit = actual_exit
        if realized_pnl is not None:
            order.realized_pnl_net = realized_pnl
        if realized_r is not None:
            order.realized_r = realized_r
        if exit_cause:
            order.exit_cause = exit_cause
        if invalidation_reason:
            order.invalidation_reason = invalidation_reason

    if commit:
        db.commit()
        db.refresh(order)
    else:
        db.flush()
    return order

# ==================== DAY AUDIT CRUD ====================

def get_or_create_today_audit(
    db: Session,
    commit: bool = True,
    date_str: Optional[str] = None,
    clock: Optional[IClock] = None
) -> models.DayAudit:
    ds = date_str or get_today_str_vn(clock)
    audit = db.query(models.DayAudit).filter(models.DayAudit.date_str == ds).first()
    if not audit:
        # Check yesterday's current equity to roll over balance
        yesterday_audit = db.query(models.DayAudit).order_by(models.DayAudit.id.desc()).first()
        start_equity = yesterday_audit.current_equity if yesterday_audit else 1000.0
        audit = models.DayAudit(
            date_str=ds,
            initial_equity=start_equity,
            current_equity=start_equity,
            realized_pnl_today=0.0,
            fills_count=0,
            consecutive_losses=0,
            cooldown_until=None,
            is_blocked=False,
            block_reason=None
        )
        db.add(audit)
        if commit:
            db.commit()
            db.refresh(audit)
        else:
            db.flush()
    return audit

def record_trade_fill_audit(
    db: Session,
    commit: bool = True,
    date_str: Optional[str] = None,
    clock: Optional[IClock] = None
) -> models.DayAudit:
    audit = get_or_create_today_audit(db, commit=commit, date_str=date_str, clock=clock)
    audit.fills_count += 1
    if audit.fills_count >= 3:
        audit.is_blocked = True
        audit.block_reason = "Đạt giới hạn tối đa 3 lệnh/ngày (UTC+7)"
    if commit:
        db.commit()
        db.refresh(audit)
    else:
        db.flush()
    return audit

def record_trade_close_audit(
    db: Session,
    pnl: float,
    commit: bool = True,
    date_str: Optional[str] = None,
    clock: Optional[IClock] = None,
    now_ms: Optional[int] = None
) -> models.DayAudit:
    audit = get_or_create_today_audit(db, commit=commit, date_str=date_str, clock=clock)
    audit.realized_pnl_today += pnl
    audit.current_equity += pnl

    c_now = now_ms if now_ms is not None else (clock.now_ms() if clock else int(time.time() * 1000))
    # Set 30-minute cooldown
    audit.cooldown_until = c_now + (30 * 60 * 1000)

    if pnl < 0:
        audit.consecutive_losses += 1
        if audit.consecutive_losses >= 2:
            audit.is_blocked = True
            audit.block_reason = "Dừng giao dịch ngày sau 2 lệnh lỗ liên tiếp"
    else:
        audit.consecutive_losses = 0

    # Check 1.5% daily loss cap
    max_loss_budget = audit.initial_equity * 0.015
    if audit.realized_pnl_today <= -max_loss_budget:
        audit.is_blocked = True
        audit.block_reason = f"Đạt giới hạn lỗ tối đa 1.5% ngày (${max_loss_budget:.2f})"

    if commit:
        db.commit()
        db.refresh(audit)
    else:
        db.flush()
    return audit

# ==================== ECONOMIC NEWS CRUD ====================

def bulk_upsert_news(db: Session, news_list: List[schemas.EconomicNewsItem]) -> int:
    if not news_list:
        return 0

    inserted = 0
    for item in news_list:
        existing = None
        if item.source_id:
            existing = db.query(models.EconomicNews).filter(models.EconomicNews.source_id == item.source_id).first()
        if not existing:
            # Match by scheduled_at and title
            existing = (
                db.query(models.EconomicNews)
                .filter(
                    models.EconomicNews.title == item.title,
                    models.EconomicNews.scheduled_at == item.scheduled_at
                )
                .first()
            )

        if existing:
            if item.actual is not None:
                existing.actual = item.actual
            if item.forecast is not None:
                existing.forecast = item.forecast
            if item.previous is not None:
                existing.previous = item.previous
            if item.impact is not None:
                existing.impact = item.impact
        else:
            db_news = models.EconomicNews(
                source_id=item.source_id,
                title=item.title,
                country=item.country,
                currency=item.currency,
                impact=item.impact,
                scheduled_at=item.scheduled_at,
                received_at=item.received_at,
                forecast=item.forecast,
                previous=item.previous,
                actual=item.actual,
                revised=item.revised
            )
            db.add(db_news)
            inserted += 1

    db.commit()
    return inserted

def check_news_blackout(db: Session, now_ms: Optional[int] = None) -> Tuple[bool, Optional[str], int]:
    """
    Check if current time is within high-impact news blackout window:
    - High impact USD news: 30 minutes before, 15 minutes after
    - FOMC rate decision / press conference: 60 minutes before, 30 minutes after
    Returns (is_blackout, event_title, remaining_minutes)
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    # Search window: events within +- 2 hours
    window_start = now_ms - (90 * 60 * 1000)
    window_end = now_ms + (90 * 60 * 1000)

    events = (
        db.query(models.EconomicNews)
        .filter(
            models.EconomicNews.scheduled_at >= window_start,
            models.EconomicNews.scheduled_at <= window_end,
            models.EconomicNews.impact.in_(["High", "high", "HIGH"])
        )
        .all()
    )

    for ev in events:
        is_fomc = "FOMC" in ev.title.upper() or "FED" in ev.title.upper() or "RATE DECISION" in ev.title.upper()
        before_ms = (60 if is_fomc else 30) * 60 * 1000
        after_ms = (30 if is_fomc else 15) * 60 * 1000

        blackout_start = ev.scheduled_at - before_ms
        blackout_end = ev.scheduled_at + after_ms

        if blackout_start <= now_ms <= blackout_end:
            remaining_min = max(1, int((blackout_end - now_ms) / (60 * 1000)))
            return True, f"Tin High Impact: {ev.title} (Blackout -{before_ms//60000}m/+{after_ms//60000}m)", remaining_min

    return False, None, 0

# ==================== RESEARCH REPORTS & LESSONS ====================

def get_latest_reports(db: Session, limit: int = 5) -> List[models.ResearchReport]:
    return db.query(models.ResearchReport).order_by(models.ResearchReport.created_at.desc()).limit(limit).all()

def save_research_report(db: Session, report_data: dict) -> models.ResearchReport:
    report = models.ResearchReport(**report_data)
    db.add(report)
    db.commit()
    db.refresh(report)
    return report

def get_lessons(db: Session, setup_type: Optional[str] = None) -> List[models.Lesson]:
    query = db.query(models.Lesson).filter(models.Lesson.is_approved == True)
    if setup_type and setup_type != "ALL":
        query = query.filter(models.Lesson.setup_type.in_([setup_type, "ALL"]))
    return query.order_by(models.Lesson.created_at.desc()).all()
