"""
backend/tests/test_v16_7_execution_guard_parity.py
V16.7 Group B, C, O: Execution Guard Parity & Canonical Fill Path.

Regression & characterization suite:
1. test_pending_entry_and_sl_same_bar_does_not_raise_typeerror:
   Pending LONG order touches entry and SL in same candle. Must evaluate via ExecutionCoordinator
   without TypeError and close conservatively at SL with intrabar_ambiguous flag.
2. test_pending_short_entry_and_sl_same_bar_does_not_raise_typeerror:
   Pending SHORT order touches entry and SL in same candle. Must evaluate via ExecutionCoordinator
   without TypeError and close at SL with intrabar_ambiguous flag.
3. test_expired_pending_touch_entry_and_sl_does_not_fill:
   Order expired prior to candle. Must be expired by housekeeping, never filled.
4. test_news_blackout_at_fill_blocks_entry:
   News blackout active during fill stage. Canonical BEFORE_FILL news guard blocks fill.
5. test_red_lesson_at_fill_blocks_entry:
   Active RED lesson blocks fill at BEFORE_FILL stage.
6. test_yellow_lesson_allows_entry_with_warning:
   YELLOW lesson warns but allows fill.
7. test_daily_cap_at_fill_blocks_entry:
   DayAudit fills_count == 3 blocks subsequent fills.
8. test_single_position_invariant_at_fill:
   Existing open position blocks second fill.
9. test_invalid_quote_does_not_fill_and_expiry_runs:
   Invalid/stale quote halts fill while housekeeping still expires overdue orders.
"""
from datetime import datetime
import json
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import crud
from database import run_schema_migrations
from domain_calculator import CostAssumptions
from lab.cg01_replay_execution_adapter import CG01ReplayExecutionAdapter
import models
from models import Base
from services.cg01_types import CG01Config, CG01ProfileMode
from services.clock import ReplayClock
from services.trading_policy_service import TradingPolicyService


@pytest.fixture
def lab_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    policy = TradingPolicyService.get_or_create_policy(session)
    policy.max_daily_fills = 3
    policy.daily_min_goal = 2
    policy.ny_min_fills = 1
    policy.is_active = False  # disable policy block for execution test isolation
    session.commit()

    try:
        yield session
    finally:
        session.close()


def test_pending_entry_and_sl_same_bar_does_not_raise_typeerror(lab_db):
    """
    PHẦN 012 & 014: Pending LONG order with Entry=100, SL=99, TP=103.
    Next candle touches below 99.
    Must not raise TypeError, must pass canonical coordinator BEFORE_FILL evaluation,
    fill at 100.0, close at SL 99.0, and mark intrabar_ambiguous.
    """
    now_ms = 1792050000000
    clock = ReplayClock(initial_ms=now_ms)
    adapter = CG01ReplayExecutionAdapter(
        db=lab_db,
        clock=clock,
        initial_capital=5000.0,
        risk_pct=0.10,
        slippage=0.0,
    )

    order = models.PaperOrder(
        id="ord_long_ambig",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="LIMIT",
        planned_entry=100.0,
        stop_loss=99.0,
        take_profit=103.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        risk_pct=0.10,
        created_at=now_ms - 60_000,
        armed_at=now_ms - 60_000,
        expires_at=now_ms + 300_000,
    )
    lab_db.add(order)
    lab_db.commit()

    candle_event = {
        "timestamp": now_ms,
        "open": 100.5,
        "high": 101.0,
        "low": 98.5,
        "close": 99.2,
        "bid": 99.15,
        "ask": 99.25,
        "bar_open_time": now_ms - 60_000,
        "bar_duration_ms": 60_000,
        "is_candle": True,
    }

    # Must NOT raise TypeError: execute_fill() got an unexpected keyword argument 'recovery_metadata'
    adapter.on_quote(candle_event)
    lab_db.refresh(order)

    assert order.state in ("closed", "paper_closed"), f"Order should be closed, got {order.state}"
    assert order.actual_entry == 100.0, f"Expected actual_entry == 100.0, got {order.actual_entry}"
    assert order.actual_exit <= 99.0, f"Expected actual_exit <= 99.0, got {order.actual_exit}"
    assert "SL" in (order.exit_cause or "") or "STOP_LOSS" in (order.exit_cause or "")
    assert getattr(order, "intrabar_ambiguous", False) is True, "Order should be marked intrabar_ambiguous"


def test_pending_short_entry_and_sl_same_bar_does_not_raise_typeerror(lab_db):
    """
    PHẦN 012 & 146: Pending SHORT order with Entry=100, SL=101, TP=97.
    Next candle touches above 101.
    Must not raise TypeError, must pass canonical coordinator evaluation,
    fill at 100.0, close at SL 101.0, and mark intrabar_ambiguous.
    """
    now_ms = 1792050000000
    clock = ReplayClock(initial_ms=now_ms)
    adapter = CG01ReplayExecutionAdapter(
        db=lab_db,
        clock=clock,
        initial_capital=5000.0,
        risk_pct=0.10,
        slippage=0.0,
    )

    order = models.PaperOrder(
        id="ord_short_ambig",
        instrument="XAUUSDT",
        direction="SHORT",
        state="armed",
        order_type="LIMIT",
        planned_entry=100.0,
        stop_loss=101.0,
        take_profit=97.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        risk_pct=0.10,
        created_at=now_ms - 60_000,
        armed_at=now_ms - 60_000,
        expires_at=now_ms + 300_000,
    )
    lab_db.add(order)
    lab_db.commit()

    candle_event = {
        "timestamp": now_ms,
        "open": 99.5,
        "high": 101.5,
        "low": 99.0,
        "close": 100.8,
        "bid": 100.75,
        "ask": 100.85,
        "bar_open_time": now_ms - 60_000,
        "bar_duration_ms": 60_000,
        "is_candle": True,
    }

    adapter.on_quote(candle_event)
    lab_db.refresh(order)

    assert order.state in ("closed", "paper_closed"), f"Order should be closed, got {order.state}"
    assert order.actual_entry == 100.0, f"Expected actual_entry == 100.0, got {order.actual_entry}"
    assert order.actual_exit >= 101.0, f"Expected actual_exit >= 101.0, got {order.actual_exit}"
    assert "SL" in (order.exit_cause or "") or "STOP_LOSS" in (order.exit_cause or "")
    assert getattr(order, "intrabar_ambiguous", False) is True


def test_expired_pending_touch_entry_and_sl_does_not_fill(lab_db):
    """
    PHẦN 022 & 023: Order has expires_at < bar_open_time (expired before candle event).
    Candle touches both planned entry and stop loss.
    Canonical path must expire the order; it must NEVER fill or close at a loss.
    """
    now_ms = 1792050000000
    clock = ReplayClock(initial_ms=now_ms)
    adapter = CG01ReplayExecutionAdapter(db=lab_db, clock=clock, initial_capital=5000.0)

    # Order expired prior to candle event
    order = models.PaperOrder(
        id="ord_expired_ambig",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="LIMIT",
        planned_entry=100.0,
        stop_loss=99.0,
        take_profit=103.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=now_ms - 120_000,
        armed_at=now_ms - 120_000,
        expires_at=now_ms - 70_000,
    )
    lab_db.add(order)
    lab_db.commit()

    candle_event = {
        "timestamp": now_ms,
        "open": 100.5,
        "high": 101.0,
        "low": 98.5,
        "close": 99.2,
        "bid": 99.15,
        "ask": 99.25,
        "bar_open_time": now_ms - 60_000,
        "bar_duration_ms": 60_000,
        "is_candle": True,
    }
    adapter.on_quote(candle_event)
    lab_db.refresh(order)

    assert order.state == "expired", f"Expected state == expired, got {order.state}"
    assert order.actual_entry is None, "Expired order must not have actual_entry"
    assert order.actual_exit is None, "Expired order must not have actual_exit"
    assert order.opened_at is None, "Expired order must not have opened_at"


def test_news_blackout_at_fill_blocks_entry(lab_db):
    """
    PHẦN 024: Order is armed before news blackout.
    At fill time, high-impact news blackout is active.
    BEFORE_FILL news guard must reject the order (or block fill).
    """
    now_ms = 1792050000000
    clock = ReplayClock(initial_ms=now_ms)
    adapter = CG01ReplayExecutionAdapter(db=lab_db, clock=clock, initial_capital=5000.0)

    # Insert news event active around now_ms
    news = models.EconomicNews(
        id=9999,
        source_id="news_fill_block",
        title="CPI Release",
        country="USD",
        currency="USD",
        impact="High",
        scheduled_at=now_ms,
        received_at=now_ms - 3600_000,
    )
    lab_db.add(news)

    # Turn policy active so news guard evaluates
    policy = TradingPolicyService.get_or_create_policy(lab_db)
    policy.is_active = True
    lab_db.commit()

    order = models.PaperOrder(
        id="ord_news_block",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="LIMIT",
        planned_entry=100.0,
        stop_loss=99.0,
        take_profit=103.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=now_ms - 60_000,
        armed_at=now_ms - 60_000,
        expires_at=now_ms + 300_000,
    )
    lab_db.add(order)
    lab_db.commit()

    tick_event = {
        "timestamp": now_ms,
        "bid": 99.95,
        "ask": 100.00,
        "is_candle": False,
    }
    adapter.on_quote(tick_event)
    lab_db.refresh(order)

    # Order must NOT be paper_open
    assert order.state != "paper_open", "Order should be blocked by BEFORE_FILL news guard"


def test_daily_cap_at_fill_blocks_entry(lab_db):
    """
    PHẦN 026: DayAudit already reached daily max cap (3 fills).
    Armed order touches entry limit.
    ExecutionCoordinator BEFORE_FILL policy rejects order, preventing fill 4.
    """
    now_ms = 1792050000000
    clock = ReplayClock(initial_ms=now_ms)
    adapter = CG01ReplayExecutionAdapter(db=lab_db, clock=clock, initial_capital=5000.0)

    policy = TradingPolicyService.get_or_create_policy(lab_db)
    policy.is_active = True
    policy.max_daily_fills = 3
    lab_db.commit()

    # Pre-record 3 fills in audit
    audit = crud.get_or_create_today_audit(lab_db, clock=clock)
    audit.fills_count = 3
    audit.is_blocked = True
    audit.block_reason = "Đạt giới hạn tối đa 3 lệnh/ngày (UTC+7)"
    lab_db.commit()

    order = models.PaperOrder(
        id="ord_cap_block",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="LIMIT",
        planned_entry=100.0,
        stop_loss=99.0,
        take_profit=103.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=now_ms - 60_000,
        armed_at=now_ms - 60_000,
        expires_at=now_ms + 300_000,
    )
    lab_db.add(order)
    lab_db.commit()

    tick_event = {
        "timestamp": now_ms,
        "bid": 99.95,
        "ask": 100.00,
        "is_candle": False,
    }
    adapter.on_quote(tick_event)
    lab_db.refresh(order)

    assert order.state in ("rejected", "armed"), f"Expected rejected or held armed, got {order.state}"
    assert order.state != "paper_open"


def test_single_position_invariant_at_fill(lab_db):
    """
    PHẦN 027: An active position is already open.
    Another armed pending order touches entry limit.
    ExecutionCoordinator refuses to open a second concurrent position.
    """
    now_ms = 1792050000000
    clock = ReplayClock(initial_ms=now_ms)
    adapter = CG01ReplayExecutionAdapter(db=lab_db, clock=clock, initial_capital=5000.0)

    # Active open position
    pos1 = models.PaperOrder(
        id="ord_active_1",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        order_type="LIMIT",
        planned_entry=100.0,
        actual_entry=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=now_ms - 120_000,
        opened_at=now_ms - 120_000,
    )
    # Armed second order
    pos2 = models.PaperOrder(
        id="ord_pending_2",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="LIMIT",
        planned_entry=100.0,
        stop_loss=98.0,
        take_profit=104.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=now_ms - 60_000,
        armed_at=now_ms - 60_000,
        expires_at=now_ms + 300_000,
    )
    lab_db.add_all([pos1, pos2])
    lab_db.commit()

    tick_event = {
        "timestamp": now_ms,
        "bid": 99.95,
        "ask": 100.00,
        "is_candle": False,
    }
    adapter.on_quote(tick_event)
    lab_db.refresh(pos2)

    # pos2 must not be opened
    assert pos2.state != "paper_open"
    # pos1 remains the sole open position
    open_count = lab_db.query(models.PaperOrder).filter(models.PaperOrder.state == "paper_open").count()
    assert open_count == 1


def test_red_lesson_at_fill_blocks_entry(lab_db):
    """
    PHẦN 025: Active RED lesson rule at fill stage blocks fill.
    """
    now_ms = 1792050000000
    clock = ReplayClock(initial_ms=now_ms)
    adapter = CG01ReplayExecutionAdapter(db=lab_db, clock=clock, initial_capital=5000.0)

    # Insert active RED rule matching all
    from services.lesson_rule_service import LessonRuleService
    red_rule = models.Lesson(
        created_at=now_ms - 1000,
        title="Red Block Spread Rule",
        category="PROCESS",
        reflection="Avoid high spread",
        action_rule="Do not enter",
        status="APPROVED",
        is_approved=True,
        enabled=True,
        severity="CRITICAL",
        effect="BLOCK_ENTRY",
        validation_status="VALID",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.0}),
        stage="ALL",
        revision=1,
    )
    lab_db.add(red_rule)

    policy = TradingPolicyService.get_or_create_policy(lab_db)
    policy.is_active = True
    lab_db.commit()
    LessonRuleService.invalidate_cache()

    order = models.PaperOrder(
        id="ord_lesson_red",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="LIMIT",
        planned_entry=100.0,
        stop_loss=99.0,
        take_profit=103.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=now_ms - 60_000,
        armed_at=now_ms - 60_000,
        expires_at=now_ms + 300_000,
    )
    lab_db.add(order)
    lab_db.commit()

    tick_event = {
        "timestamp": now_ms,
        "bid": 99.95,
        "ask": 100.00,
        "is_candle": False,
    }
    adapter.on_quote(tick_event)
    lab_db.refresh(order)

    # Order must NOT be paper_open
    assert order.state in ("rejected", "armed")
    assert order.state != "paper_open"


def test_yellow_lesson_allows_entry_with_warning(lab_db):
    """
    PHẦN 025: Active YELLOW lesson rule produces warning but allows fill.
    """
    now_ms = 1792050000000
    clock = ReplayClock(initial_ms=now_ms)
    adapter = CG01ReplayExecutionAdapter(db=lab_db, clock=clock, initial_capital=5000.0, slippage=0.0)

    from services.lesson_rule_service import LessonRuleService
    yellow_rule = models.Lesson(
        created_at=now_ms - 1000,
        title="Yellow Advisory Rule",
        category="PROCESS",
        reflection="Advisory note",
        action_rule="Caution advised",
        status="APPROVED",
        is_approved=True,
        enabled=True,
        severity="WARNING",
        effect="WARN_ENTRY",
        validation_status="VALID",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.0}),
        stage="ALL",
        revision=1,
    )
    lab_db.add(yellow_rule)

    policy = TradingPolicyService.get_or_create_policy(lab_db)
    policy.is_active = True
    lab_db.commit()
    LessonRuleService.invalidate_cache()

    order = models.PaperOrder(
        id="ord_lesson_yellow",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="LIMIT",
        planned_entry=100.0,
        stop_loss=99.0,
        take_profit=103.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=now_ms - 60_000,
        armed_at=now_ms - 60_000,
        expires_at=now_ms + 300_000,
    )
    lab_db.add(order)
    lab_db.commit()

    tick_event = {
        "timestamp": now_ms,
        "bid": 99.95,
        "ask": 100.00,
        "is_candle": False,
    }
    adapter.on_quote(tick_event)
    lab_db.refresh(order)

    # Yellow warning must allow fill
    assert order.state == "paper_open"
    assert order.actual_entry == 100.0


def test_invalid_quote_does_not_fill_and_expiry_runs(lab_db):
    """
    PHẦN 021 & 028: Invalid quote (bid > ask) prevents fill, but housekeeping
    still correctly expires overdue armed orders.
    """
    now_ms = 1792050000000
    clock = ReplayClock(initial_ms=now_ms)
    adapter = CG01ReplayExecutionAdapter(db=lab_db, clock=clock, initial_capital=5000.0)

    # Overdue order
    overdue_ord = models.PaperOrder(
        id="ord_overdue_1",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="LIMIT",
        planned_entry=100.0,
        stop_loss=99.0,
        take_profit=103.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=now_ms - 120_000,
        armed_at=now_ms - 120_000,
        expires_at=now_ms - 5000,  # expired
    )
    # Valid order touching entry price
    valid_ord = models.PaperOrder(
        id="ord_touch_valid",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="LIMIT",
        planned_entry=100.0,
        stop_loss=99.0,
        take_profit=103.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=now_ms - 60_000,
        armed_at=now_ms - 60_000,
        expires_at=now_ms + 300_000,
    )
    lab_db.add_all([overdue_ord, valid_ord])
    lab_db.commit()

    # Inverted quote (bid > ask -> invalid feed)
    invalid_tick = {
        "timestamp": now_ms,
        "bid": 105.00,
        "ask": 95.00,
        "is_candle": False,
    }
    adapter.on_quote(invalid_tick)
    lab_db.refresh(overdue_ord)
    lab_db.refresh(valid_ord)

    # Housekeeping expired the overdue order
    assert overdue_ord.state == "expired"
    # Invalid quote prevented valid_ord from filling
    assert valid_ord.state == "armed"
    assert valid_ord.actual_entry is None


def test_close_quote_model_ignores_intrabar_wick(lab_db):
    """
    PHẦN 063: When execution_model="CLOSE_QUOTE", candle wick touching entry
    does NOT trigger fill if close quote does not touch entry.
    """
    now_ms = 1792050000000
    clock = ReplayClock(initial_ms=now_ms)
    cfg = CG01Config(enabled=True, profile_mode=CG01ProfileMode.PAPER_EXECUTION)
    adapter = CG01ReplayExecutionAdapter(
        db=lab_db,
        clock=clock,
        config=cfg,
        initial_capital=5000.0,
        execution_model="CLOSE_QUOTE",
    )

    order = models.PaperOrder(
        id="ord_close_model",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="LIMIT",
        planned_entry=100.0,
        stop_loss=98.0,
        take_profit=105.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=now_ms - 60_000,
        armed_at=now_ms - 60_000,
        expires_at=now_ms + 300_000,
    )
    lab_db.add(order)
    lab_db.commit()

    # Candle wicks down to 99.5 (penetrating 100 limit), but close is 102.5
    wick_candle = {
        "timestamp": now_ms,
        "open": 102.0,
        "high": 103.0,
        "low": 99.5,
        "close": 102.5,
        "bid": 102.45,
        "ask": 102.55,
        "bar_open_time": now_ms - 60_000,
        "bar_duration_ms": 60_000,
        "is_candle": True,
    }
    adapter.on_quote(wick_candle)
    lab_db.refresh(order)

    # In CLOSE_QUOTE model, order remains armed because close (102.5) never touched limit (100.0)
    assert order.state == "armed", f"Expected armed under CLOSE_QUOTE model, got {order.state}"
    assert order.actual_entry is None

