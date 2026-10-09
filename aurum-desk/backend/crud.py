import time
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import List, Optional, Tuple, Dict, Any
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

def get_account_status(db: Session, clock: Optional[IClock] = None) -> Dict[str, Any]:
    """Retrieve account equity and active position state with clock injection."""
    audit = get_or_create_today_audit(db, clock=clock)
    active_pos = get_active_position(db)
    return {
        "current_equity": audit.current_equity,
        "initial_equity": audit.initial_equity,
        "realized_pnl_today": audit.realized_pnl_today,
        "fills_count": audit.fills_count,
        "consecutive_losses": audit.consecutive_losses,
        "is_blocked": audit.is_blocked,
        "block_reason": audit.block_reason,
        "cooldown_until": audit.cooldown_until,
        "has_active_position": active_pos is not None
    }


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

def get_lessons(
    db: Session,
    setup_type: Optional[str] = None,
    status: Optional[str] = None
) -> List[models.Lesson]:
    """
    Retrieve lessons for UI review.
    Supports filtering by status (ALL, PENDING_REVIEW, APPROVED, REJECTED, ARCHIVED).
    When status is None or 'ALL', returns all lessons so newly generated drafts are visible to the user.
    """
    query = db.query(models.Lesson)

    if status == "APPROVED":
        query = query.filter(models.Lesson.status == "APPROVED", models.Lesson.is_approved == True)
    elif status == "PENDING_REVIEW":
        query = query.filter(models.Lesson.status == "PENDING_REVIEW")
    elif status == "REJECTED":
        query = query.filter(models.Lesson.status == "REJECTED")
    elif status == "ARCHIVED":
        query = query.filter(models.Lesson.status == "ARCHIVED")
    # 'ALL' or None -> return all lessons for UI overview

    if setup_type and setup_type != "ALL":
        query = query.filter(models.Lesson.setup_type.in_([setup_type, "ALL"]))

    return query.order_by(models.Lesson.created_at.desc()).all()


def get_approved_lessons_for_strategy(db: Session, setup_type: Optional[str] = None) -> List[models.Lesson]:
    """Strictly retrieves ONLY active approved lessons for strategy memory injection, excluding ARCHIVED and REJECTED."""
    query = db.query(models.Lesson).filter(
        models.Lesson.status == "APPROVED",
        models.Lesson.is_approved == True,
        models.Lesson.status != "ARCHIVED",
        models.Lesson.status != "REJECTED"
    )
    if setup_type and setup_type != "ALL":
        query = query.filter(models.Lesson.setup_type.in_([setup_type, "ALL"]))
    return query.order_by(models.Lesson.created_at.desc()).all()


def get_telegram_config(db: Session) -> Optional[models.TelegramConfig]:
    return db.query(models.TelegramConfig).first()


# ==================== V8 TRADE REVIEW & PSYCHOLOGY CRUD ====================

def get_trade_review(db: Session, trade_id: str) -> Optional[models.TradeReview]:
    return db.query(models.TradeReview).filter(models.TradeReview.trade_id == trade_id).first()


def save_trade_review(
    db: Session,
    trade_id: str,
    data: schemas.TradeReviewCreateOrUpdate
) -> models.TradeReview:
    import uuid
    now_ms = int(time.time() * 1000)
    existing = get_trade_review(db, trade_id)

    emotions_json = json.dumps(data.emotions or []) if data.emotions is not None else "[]"

    if existing:
        # Check optimistic concurrency
        rev_check = data.expected_revision if data.expected_revision is not None else data.revision
        if rev_check is not None and rev_check != existing.revision:
            raise ValueError(f"CONFLICT: Review has been modified by another operation (expected rev {existing.revision}, got {rev_check})")

        existing.execution_mode = data.execution_mode or existing.execution_mode
        existing.user_notes = data.user_notes
        existing.self_reported_entry_reason = data.self_reported_entry_reason
        existing.psychology_before = data.psychology_before
        existing.psychology_during = data.psychology_during
        existing.psychology_after = data.psychology_after
        existing.emotions = emotions_json
        existing.confidence_score = data.confidence_score
        existing.discipline_score = data.discipline_score
        existing.user_loss_reason = data.user_loss_reason
        existing.mistakes = data.mistakes
        existing.what_went_well = data.what_went_well
        existing.improvement_plan = data.improvement_plan
        existing.revision += 1
        existing.updated_at = now_ms
        existing.reviewed_at = now_ms
        db.commit()
        db.refresh(existing)
        return existing
    else:
        new_review = models.TradeReview(
            id=str(uuid.uuid4()),
            trade_id=trade_id,
            execution_mode=data.execution_mode or "AUTO",
            user_notes=data.user_notes,
            self_reported_entry_reason=data.self_reported_entry_reason,
            psychology_before=data.psychology_before,
            psychology_during=data.psychology_during,
            psychology_after=data.psychology_after,
            emotions=emotions_json,
            confidence_score=data.confidence_score,
            discipline_score=data.discipline_score,
            user_loss_reason=data.user_loss_reason,
            mistakes=data.mistakes,
            what_went_well=data.what_went_well,
            improvement_plan=data.improvement_plan,
            revision=1,
            created_at=now_ms,
            updated_at=now_ms,
            reviewed_at=now_ms
        )
        db.add(new_review)
        db.commit()
        db.refresh(new_review)
        return new_review


# ==================== V8 ENHANCED JOURNAL PAGINATION & SUMMARY ====================

def get_journal_paginated(
    db: Session,
    state: Optional[str] = None,
    direction: Optional[str] = None,
    outcome: Optional[str] = None,
    strategy_family: Optional[str] = None,
    date_from_ms: Optional[int] = None,
    date_to_ms: Optional[int] = None,
    has_review: Optional[bool] = None,
    search: Optional[str] = None,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 20
) -> Dict[str, Any]:
    """
    Server-side filtering, summary calculation, and pagination for trade journal.
    Summary is computed over the entire filtered query.
    """
    from sqlalchemy import func

    query = db.query(models.PaperOrder)

    # 1. State filter
    if state and state != "ALL":
        s_upper = state.upper()
        if s_upper == "OPEN":
            query = query.filter(models.PaperOrder.state == "paper_open")
        elif s_upper == "CLOSED":
            query = query.filter(models.PaperOrder.state == "closed")
        elif s_upper == "ARMED":
            query = query.filter(models.PaperOrder.state == "armed")
        elif s_upper == "CANCELLED":
            query = query.filter(models.PaperOrder.state.in_(["cancelled", "expired", "invalidated", "rejected"]))
        else:
            query = query.filter(models.PaperOrder.state == state.lower())

    # 2. Direction filter
    if direction and direction != "ALL":
        query = query.filter(models.PaperOrder.direction == direction.upper())

    # 3. Strategy Family filter
    if strategy_family and strategy_family != "ALL":
        query = query.filter(models.PaperOrder.strategy_family == strategy_family)

    # 4. Date range filter
    if date_from_ms:
        query = query.filter(models.PaperOrder.created_at >= date_from_ms)
    if date_to_ms:
        query = query.filter(models.PaperOrder.created_at <= date_to_ms)

    # 5. Outcome filter
    if outcome and outcome != "ALL":
        o_upper = outcome.upper()
        if o_upper == "WIN":
            query = query.filter(models.PaperOrder.state == "closed", models.PaperOrder.realized_pnl_net > 0)
        elif o_upper == "LOSS":
            query = query.filter(models.PaperOrder.state == "closed", models.PaperOrder.realized_pnl_net < 0)
        elif o_upper == "BREAKEVEN":
            query = query.filter(models.PaperOrder.state == "closed", models.PaperOrder.realized_pnl_net == 0)

    # 6. Search filter (search by ID or setup_id)
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            (models.PaperOrder.id.ilike(term)) |
            (models.PaperOrder.setup_id.ilike(term))
        )

    # 7. Has review filter
    if has_review is not None:
        reviewed_trade_ids = db.query(models.TradeReview.trade_id).subquery()
        if has_review:
            query = query.filter(models.PaperOrder.id.in_(reviewed_trade_ids))
        else:
            query = query.filter(~models.PaperOrder.id.in_(reviewed_trade_ids))

    # Calculate summary over entire filtered set
    all_filtered = query.all()
    total_count = len(all_filtered)

    completed = [o for o in all_filtered if o.state == "closed"]
    open_orders = [o for o in all_filtered if o.state == "paper_open"]
    armed_orders = [o for o in all_filtered if o.state == "armed"]
    cancelled_orders = [o for o in all_filtered if o.state in ("cancelled", "expired", "invalidated", "rejected")]

    completed_count = len(completed)
    open_count = len(open_orders)
    armed_count = len(armed_orders)
    cancelled_count = len(cancelled_orders)

    wins = [o for o in completed if (o.realized_pnl_net or 0.0) > 0]
    losses = [o for o in completed if (o.realized_pnl_net or 0.0) < 0]
    breakevens = [o for o in completed if (o.realized_pnl_net or 0.0) == 0]

    win_count = len(wins)
    loss_count = len(losses)
    breakeven_count = len(breakevens)

    net_pnl = round(sum((o.realized_pnl_net or 0.0) for o in completed), 2)
    winrate_pct = round((win_count / completed_count * 100), 2) if completed_count > 0 else 0.0

    realized_r_vals = [o.realized_r for o in completed if o.realized_r is not None]
    avg_realized_r = round(sum(realized_r_vals) / len(realized_r_vals), 2) if realized_r_vals else 0.0

    summary = {
        "completed_count": completed_count,
        "open_count": open_count,
        "armed_count": armed_count,
        "cancelled_count": cancelled_count,
        "net_pnl": net_pnl,
        "win_count": win_count,
        "loss_count": loss_count,
        "breakeven_count": breakeven_count,
        "winrate_pct": winrate_pct,
        "avg_realized_r": avg_realized_r,
        "wins": win_count,
        "losses": loss_count,
        "breakevens": breakeven_count,
        "average_realized_r": avg_realized_r
    }

    # Sorting
    sort_column = getattr(models.PaperOrder, sort_by, models.PaperOrder.created_at)
    if sort_dir.lower() == "asc":
        query = query.order_by(sort_column.asc(), models.PaperOrder.id.asc())
    else:
        query = query.order_by(sort_column.desc(), models.PaperOrder.id.desc())

    # Pagination
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    total_pages = max(1, (total_count + page_size - 1) // page_size)

    items = query.offset((page - 1) * page_size).limit(page_size).all()

    return {
        "items": items,
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "summary": summary
    }
