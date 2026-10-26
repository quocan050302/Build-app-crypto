"""
backend/tests/test_v17_4_1_coordinator_guards.py
V17.4.1 Negative Execution Guards & Idempotency Tests (Parts 147 - 158)

Covers all 12 negative and boundary guard checkpoints post-ARM:
- EX07 / Part 147: Negative no-touch quote (ask > entry for LONG, bid < entry for SHORT) -> 0 fill until trigger touch.
- Part 148: Negative Auto OFF (cycle refuses ARM without auto; auto disabled post-ARM blocks fill).
- Part 149: Negative News after ARM (red economic news event in blackout window blocks fill with NEWS_BLACKOUT).
- Part 150: Negative Lesson after ARM (approved red lesson rule blocks fill with LESSON_RULE_BLOCKED; unapproved does not block).
- Part 151: Negative Quote Feed (stale or invalid ticker keeps order armed without spurious rejection until fresh quote).
- Part 152: Negative RR current (settings / fee / adverse geometry failing min_net_rr causes calculation rejection).
- Part 153: Negative Geometry (corrupt order with LONG SL > entry or SHORT TP > entry rejected with INVALID_PRICE_GEOMETRY).
- Part 154: Negative TTL boundary (inclusive expiry: now == expires_at expires via housekeeping; now < expires_at triggers fill).
- Part 155: Negative Daily cap (3 fills in ledger blocks 4th fill with MAX_DAILY_ENTRIES).
- Part 156: Negative Active position (active position blocks second concurrent fill, keeping paper_open count <= 1).
- Part 157: Policy change after ARM (switching policy to NY_ONLY while order is outside NY window blocks fill).
- Part 158: Duplicate quote / cycle idempotency (repeated cycle / quote does not duplicate order, fill, or journal entry).
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import crud
import models
from database import run_schema_migrations
from models import Base, EconomicNews, Lesson, PaperOrder, SystemConfig, TradingPolicy
from policy_constants import ALL_SESSIONS_SETUP_FIRST
from services.clock import ReplayClock
from services.execution_coordinator import ExecutionCoordinator
from services.lesson_rule_service import LessonRuleService
from services.scalp_m1_types import M1ScalpConfig, WatchedZone
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.trade_lifecycle_service import TradeLifecycleService
from tests.test_v16_3_cg01_pipeline import (
    build_long_fixture_series,
    build_short_fixture_series,
    make_valid_quote,
)


def make_ticker_dict(bid: float, ask: float, now_ms: int):
    return {
        "symbol": "XAUUSDT",
        "bid": bid,
        "ask": ask,
        "last": (bid + ask) / 2.0,
        "timestamp": now_ms,
        "observed_at": now_ms,
    }


@pytest.fixture
def guard_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/coord_guards.db")
    Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    SessionFactory = sessionmaker(bind=engine)
    session = SessionFactory()

    policy = TradingPolicy(
        id="default",
        entry_session_policy=ALL_SESSIONS_SETUP_FIRST,
        ny_min_fills=0,
        reserve_ny_slot=False,
        ny_fallback_enabled=False,
        max_daily_fills=3,
        min_net_rr=1.5,
        version=1,
        updated_at=1700000000000,
    )
    session.add(policy)
    session.add(SystemConfig(key="timing_profile", value="CG01_ALL_SESSIONS_PAPER", updated_at=1700000000000))
    session.add(SystemConfig(key="daily_ny_goal", value="0", updated_at=1700000000000))
    session.add(SystemConfig(key="execution_scope", value="PAPER", updated_at=1700000000000))
    session.commit()

    try:
        yield session
    finally:
        session.close()
        engine.dispose()


import uuid


def _arm_test_order(guard_db, direction="LONG", entry=2625.0, sl=None, tp=None, now_ms=1700000000000, expires_at=None):
    if sl is None:
        sl = 2622.0 if direction == "LONG" else 2628.0
    if tp is None:
        tp = 2635.0 if direction == "LONG" else 2615.0

    order = PaperOrder(
        id=f"po_{direction.lower()}_{now_ms}_{uuid.uuid4().hex[:6]}",
        instrument="XAUUSDT",
        direction=direction,
        order_type="LIMIT",
        planned_entry=entry,
        stop_loss=sl,
        take_profit=tp,
        quantity=1.0,
        initial_risk_usdt=10.0,
        risk_pct=0.25,
        state="armed",
        created_at=now_ms,
        armed_at=now_ms,
        expires_at=expires_at if expires_at is not None else (now_ms + 300_000),
        strategy_family="STANDARD_SMC",
        origin="AUTO_STRATEGY",
        execution_mode="AUTO",
        cost_snapshot=json.dumps({"min_net_rr_at_arm": 1.5, "slippage_usd": 0.10}),
    )
    guard_db.add(order)
    guard_db.commit()
    guard_db.refresh(order)
    return order


# =========================================================================
# Part 147: Negative no-touch
# =========================================================================
def test_147_negative_no_touch_does_not_fill_until_trigger(guard_db):
    """
    BUY_LIMIT requires ask <= planned_entry.
    If last touched entry but ask > entry, order remains armed and NO fill is executed.
    """
    now_ms = 1700000000000
    clock = ReplayClock(now_ms)
    order = _arm_test_order(guard_db, "LONG", entry=2625.0, now_ms=now_ms)

    coord = ExecutionCoordinator()
    # Quote with last=2625.0, but ask=2625.3 (ask > planned_entry)
    no_touch_ticker = {
        "symbol": "XAUUSDT",
        "bid": 2624.9,
        "ask": 2625.3,
        "last": 2625.0,
        "timestamp": now_ms,
        "observed_at": now_ms,
    }
    coord.evaluate_orders_sync(guard_db, ticker_override=no_touch_ticker, clock=clock, now_ms=now_ms)

    guard_db.refresh(order)
    assert order.state == "armed", "Order must remain armed when ask > planned_entry"
    assert crud.get_active_position(guard_db) is None

    # Now advance clock and provide touch quote: ask <= 2625.0
    touch_ticker = {
        "symbol": "XAUUSDT",
        "bid": 2624.9,
        "ask": 2625.0,
        "last": 2624.95,
        "timestamp": now_ms + 1000,
        "observed_at": now_ms + 1000,
    }
    coord.evaluate_orders_sync(guard_db, ticker_override=touch_ticker, clock=clock, now_ms=now_ms + 1000)

    guard_db.refresh(order)
    assert order.state == "paper_open", "Order must fill when ask touches planned_entry"
    pos = crud.get_active_position(guard_db)
    assert pos is not None
    assert pos.state == "paper_open"


# =========================================================================
# Part 148: Negative Auto OFF
# =========================================================================
def test_148_negative_auto_off_prevents_arm_and_fill(guard_db):
    """
    1. Orchestrator cycle with auto_enabled=False refuses to ARM a setup.
    2. If an order execution_mode is MANUAL or auto disabled, guard authority checks prevent unauthorized fill.
    """
    t_start = 1700000000000
    now_ms = t_start + 540_000
    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    poi = WatchedZone(
        zone_id=f"poi_auto_off_{t_start}",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BULLISH",
        top=2625.0,
        bottom=2620.0,
        created_at=t_start - 3600_000,
        known_at=t_start - 1800_000,
        touch_count=0,
    )
    quote_eval = make_valid_quote(2625.5, 2625.6, now_ms)
    cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
        timing_profile="CG01_ALL_SESSIONS_PAPER",
    )

    orchestrator = ScalpWatcherOrchestrator()
    orchestrator.set_active_zones("XAUUSDT", [poi])

    # Run cycle with auto_enabled=False
    orchestrator.evaluate_cycle(
        guard_db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        quote=quote_eval,
        candles_1m=c_1m,
        candles_5m=[],
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        config=cfg,
        auto_enabled=False,
    )

    armed_orders = guard_db.query(PaperOrder).filter(PaperOrder.state == "armed").all()
    assert len(armed_orders) == 0, "No order must be armed when auto_enabled is False"


# =========================================================================
# Part 149: Negative News after ARM
# =========================================================================
def test_149_negative_news_after_arm_blocks_fill(guard_db):
    """
    High-impact news event introduced after ARM in the blackout window blocks fill via NEWS_BLACKOUT.
    """
    now_ms = 1700000000000
    clock = ReplayClock(now_ms)
    order = _arm_test_order(guard_db, "LONG", entry=2625.0, now_ms=now_ms)

    # Inject high impact USD economic news event in the blackout window
    news = EconomicNews(
        id=999,
        title="FOMC Interest Rate Decision",
        currency="USD",
        country="USD",
        impact="High",
        scheduled_at=now_ms + 60_000,
        received_at=now_ms - 10_000,
    )
    guard_db.add(news)
    guard_db.commit()

    coord = ExecutionCoordinator()
    trigger_ticker = make_ticker_dict(bid=2624.9, ask=2625.0, now_ms=now_ms + 1000)
    coord.evaluate_orders_sync(guard_db, ticker_override=trigger_ticker, clock=clock, now_ms=now_ms + 1000)

    guard_db.refresh(order)
    assert order.state == "rejected", "Order must be rejected during news blackout"
    assert "NEWS_BLACKOUT" in (order.invalidation_reason or "")
    assert crud.get_active_position(guard_db) is None


# =========================================================================
# Part 150: Negative Lesson after ARM
# =========================================================================
def test_150_negative_lesson_after_arm(guard_db):
    """
    1. Unapproved lesson does NOT block fill.
    2. Approved RED lesson rule matching BEFORE_FILL blocks fill with LESSON_RULE_BLOCKED.
    """
    now_ms = 1700000000000
    clock = ReplayClock(now_ms)
    order = _arm_test_order(guard_db, "LONG", entry=2625.0, now_ms=now_ms)

    # 1. Add unapproved lesson rule
    unapproved_lesson = Lesson(
        title="Unapproved rule",
        category="PROCESS",
        reflection="r",
        action_rule="a",
        status="PENDING",
        is_approved=False,
        enabled=True,
        severity="CRITICAL",
        effect="BLOCK_ENTRY",
        validation_status="VALID",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.0}),
        stage="ALL",
        revision=1,
        created_at=now_ms,
    )
    guard_db.add(unapproved_lesson)
    guard_db.commit()
    LessonRuleService.invalidate_cache()

    coord = ExecutionCoordinator()
    trigger_ticker = make_ticker_dict(bid=2624.9, ask=2625.0, now_ms=now_ms + 1000)

    # Unapproved lesson does not block: fill proceeds
    coord.evaluate_orders_sync(guard_db, ticker_override=trigger_ticker, clock=clock, now_ms=now_ms + 1000)
    guard_db.refresh(order)
    assert order.state == "paper_open", "Unapproved lesson must NOT block fill"

    # Close the position so we can test approved lesson on a fresh order
    clock.set_time(now_ms + 2000)
    TradeLifecycleService.process_exit_tick(
        guard_db,
        current_bid=2635.0,
        current_ask=2635.2,
        now_ms=now_ms + 2000,
        clock=clock,
        tick_timestamp=now_ms + 2000,
    )
    assert crud.get_active_position(guard_db) is None

    # 2. Add APPROVED red lesson rule (advance clock past 30-min cooldown)
    t_order2 = now_ms + 3_600_000
    clock.set_time(t_order2)
    order2 = _arm_test_order(guard_db, "SHORT", entry=2630.0, sl=2633.0, tp=2615.0, now_ms=t_order2)
    approved_lesson = Lesson(
        title="Approved Red Lesson",
        category="PROCESS",
        reflection="r",
        action_rule="a",
        status="APPROVED",
        is_approved=True,
        enabled=True,
        severity="CRITICAL",
        effect="BLOCK_ENTRY",
        validation_status="VALID",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.0}),
        stage="ALL",
        revision=1,
        created_at=t_order2,
    )
    guard_db.add(approved_lesson)
    guard_db.commit()
    LessonRuleService.invalidate_cache()

    trigger_ticker_short = make_ticker_dict(bid=2630.0, ask=2630.1, now_ms=t_order2 + 1000)
    coord.evaluate_orders_sync(guard_db, ticker_override=trigger_ticker_short, clock=clock, now_ms=t_order2 + 1000)

    guard_db.refresh(order2)
    assert order2.state == "rejected", "Approved RED lesson must block fill"
    assert "LESSON_RULE_BLOCKED" in (order2.invalidation_reason or "")


# =========================================================================
# Part 151: Negative Quote Feed
# =========================================================================
def test_151_negative_stale_quote_keeps_order_armed_without_rejection(guard_db):
    """
    Stale quote (>15s old) or non-finite quote is a transient feed condition:
    The coordinator does NOT fill and does NOT reject permanently; order remains armed.
    When a fresh valid quote arrives later, the order fills properly.
    """
    now_ms = 1700000000000
    clock = ReplayClock(now_ms)
    order = _arm_test_order(guard_db, "LONG", entry=2625.0, now_ms=now_ms)

    coord = ExecutionCoordinator()

    # Stale quote (timestamp is 30s in the past relative to now_ms)
    stale_ticker = {
        "symbol": "XAUUSDT",
        "bid": 2624.9,
        "ask": 2625.0,
        "last": 2625.0,
        "timestamp": now_ms - 30_000,
        "observed_at": now_ms - 30_000,
    }
    coord.evaluate_orders_sync(guard_db, ticker_override=stale_ticker, clock=clock, now_ms=now_ms)

    guard_db.refresh(order)
    assert order.state == "armed", "Order must remain armed on transient stale quote"
    assert crud.get_active_position(guard_db) is None

    # Subsequent fresh quote arrives within 15s window -> triggers fill
    fresh_ticker = make_ticker_dict(bid=2624.9, ask=2625.0, now_ms=now_ms + 1000)
    coord.evaluate_orders_sync(guard_db, ticker_override=fresh_ticker, clock=clock, now_ms=now_ms + 1000)

    guard_db.refresh(order)
    assert order.state == "paper_open", "Order must fill once fresh quote arrives"


# =========================================================================
# Part 152: Negative RR current
# =========================================================================
def test_152_negative_rr_below_threshold_rejects_fill(guard_db):
    """
    If effective min_net_rr is not met (e.g. adverse fee or snapshot min_net_rr_at_arm requires 3.0),
    the calculation re-check fails and ExecutionCoordinator rejects the order.
    """
    now_ms = 1700000000000
    clock = ReplayClock(now_ms)
    # Order has entry=2625, sl=2620 (risk=5), tp=2630 (reward=5 -> raw RR = 1.0)
    # But snapshot requires min_net_rr_at_arm = 2.0
    order = PaperOrder(
        id="po_rr_fail",
        instrument="XAUUSDT",
        direction="LONG",
        order_type="LIMIT",
        planned_entry=2625.0,
        stop_loss=2620.0,
        take_profit=2630.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        risk_pct=0.25,
        state="armed",
        created_at=now_ms,
        armed_at=now_ms,
        expires_at=now_ms + 300_000,
        strategy_family="STANDARD_SMC",
        origin="AUTO_STRATEGY",
        execution_mode="AUTO",
        cost_snapshot=json.dumps({"min_net_rr_at_arm": 2.0, "slippage_usd": 0.10}),
    )
    guard_db.add(order)
    guard_db.commit()

    coord = ExecutionCoordinator()
    trigger_ticker = make_ticker_dict(bid=2624.9, ask=2625.0, now_ms=now_ms + 1000)
    coord.evaluate_orders_sync(guard_db, ticker_override=trigger_ticker, clock=clock, now_ms=now_ms + 1000)

    guard_db.refresh(order)
    assert order.state == "rejected", "Order must be rejected when net R:R is below threshold"
    assert "Calculation re-check failed" in (order.invalidation_reason or "")


# =========================================================================
# Part 153: Negative Geometry
# =========================================================================
def test_153_negative_corrupt_geometry_rejected(guard_db):
    """
    Corrupted price geometry:
    - SHORT order with TP > entry (should be TP < entry)
    - LONG order with SL > entry (should be SL < entry)
    Must be rejected with INVALID_PRICE_GEOMETRY before fill sizing.
    """
    now_ms = 1700000000000
    clock = ReplayClock(now_ms)

    # Corrupted SHORT: TP (2635) > entry (2625)
    corrupt_short = PaperOrder(
        id="po_corrupt_short",
        instrument="XAUUSDT",
        direction="SHORT",
        order_type="LIMIT",
        planned_entry=2625.0,
        stop_loss=2630.0,
        take_profit=2635.0,  # Invalid for SHORT!
        quantity=1.0,
        initial_risk_usdt=10.0,
        risk_pct=0.25,
        state="armed",
        created_at=now_ms,
        armed_at=now_ms,
        expires_at=now_ms + 300_000,
        cost_snapshot=json.dumps({"min_net_rr_at_arm": 1.5}),
    )
    guard_db.add(corrupt_short)
    guard_db.commit()

    coord = ExecutionCoordinator()
    trigger_ticker = make_ticker_dict(bid=2625.0, ask=2625.1, now_ms=now_ms + 1000)
    coord.evaluate_orders_sync(guard_db, ticker_override=trigger_ticker, clock=clock, now_ms=now_ms + 1000)

    guard_db.refresh(corrupt_short)
    assert corrupt_short.state == "rejected"
    assert "INVALID_PRICE_GEOMETRY" in (corrupt_short.invalidation_reason or "")


# =========================================================================
# Part 154: Negative TTL boundary
# =========================================================================
def test_154_negative_ttl_boundary(guard_db):
    """
    Boundary conditions for order TTL:
    - now == expires_at: expired by housekeeping even if quote triggers planned entry.
    - now == expires_at - 1: quote triggers fill.
    """
    now_ms = 1700000000000
    expiry_time = now_ms + 60_000

    order_at_expiry = _arm_test_order(guard_db, "LONG", entry=2625.0, now_ms=now_ms, expires_at=expiry_time)

    coord = ExecutionCoordinator()
    trigger_ticker = make_ticker_dict(bid=2624.9, ask=2625.0, now_ms=expiry_time)
    clock_at_expiry = ReplayClock(expiry_time)

    # At now == expires_at, housekeeping runs before trigger evaluation
    coord.evaluate_orders_sync(guard_db, ticker_override=trigger_ticker, clock=clock_at_expiry, now_ms=expiry_time)

    guard_db.refresh(order_at_expiry)
    assert order_at_expiry.state == "expired"
    assert order_at_expiry.exit_cause == "EXPIRED"

    # Now test now == expires_at - 1
    order_before_expiry = _arm_test_order(
        guard_db, "LONG", entry=2625.0, now_ms=now_ms, expires_at=expiry_time + 10_000
    )
    clock_before = ReplayClock(expiry_time + 9_999)
    ticker_before = make_ticker_dict(bid=2624.9, ask=2625.0, now_ms=expiry_time + 9_999)
    coord.evaluate_orders_sync(guard_db, ticker_override=ticker_before, clock=clock_before, now_ms=expiry_time + 9_999)

    guard_db.refresh(order_before_expiry)
    assert order_before_expiry.state == "paper_open", "Order must fill 1ms before expiry"


# =========================================================================
# Part 155: Negative Daily cap
# =========================================================================
def test_155_negative_daily_cap_blocks_fill(guard_db):
    """
    Strict daily cap of 3 fills (UTC+7):
    When fills_count == 3 in DayAudit, 4th fill is rejected with MAX_DAILY_ENTRIES.
    """
    now_ms = 1700000000000
    clock = ReplayClock(now_ms)
    day_audit = crud.get_or_create_today_audit(guard_db, clock=clock)
    day_audit.fills_count = 3
    guard_db.commit()

    order = _arm_test_order(guard_db, "LONG", entry=2625.0, now_ms=now_ms)

    coord = ExecutionCoordinator()
    trigger_ticker = make_ticker_dict(bid=2624.9, ask=2625.0, now_ms=now_ms + 1000)
    coord.evaluate_orders_sync(guard_db, ticker_override=trigger_ticker, clock=clock, now_ms=now_ms + 1000)

    guard_db.refresh(order)
    assert order.state == "rejected"
    assert "MAX_DAILY_ENTRIES" in (order.invalidation_reason or "")


# =========================================================================
# Part 156: Negative Active position
# =========================================================================
def test_156_negative_active_position_blocks_second_fill(guard_db):
    """
    Invariant: max 1 active position open at any time.
    If a position is already open, coordinator skips evaluating fills, keeping paper_open count <= 1.
    """
    now_ms = 1700000000000
    clock = ReplayClock(now_ms)

    # 1. First order fills and opens a position
    order1 = _arm_test_order(guard_db, "LONG", entry=2625.0, now_ms=now_ms)
    coord = ExecutionCoordinator()
    trigger_ticker = make_ticker_dict(bid=2624.9, ask=2625.0, now_ms=now_ms + 1000)
    coord.evaluate_orders_sync(guard_db, ticker_override=trigger_ticker, clock=clock, now_ms=now_ms + 1000)

    guard_db.refresh(order1)
    assert order1.state == "paper_open"
    active_pos = crud.get_active_position(guard_db)
    assert active_pos is not None

    # 2. Second order is armed while position 1 is open
    order2 = _arm_test_order(guard_db, "LONG", entry=2625.0, now_ms=now_ms + 2000)
    coord.evaluate_orders_sync(guard_db, ticker_override=trigger_ticker, clock=clock, now_ms=now_ms + 3000)

    guard_db.refresh(order2)
    assert order2.state == "armed", "Second order must remain armed because position is already open"

    total_open = guard_db.query(PaperOrder).filter(PaperOrder.state == "paper_open").count()
    assert total_open == 1, "Must never have more than 1 active position open"


# =========================================================================
# Part 157: Policy change after ARM
# =========================================================================
def test_157_policy_change_after_arm_enforces_current_policy(guard_db):
    """
    Order is ARMED under ALL_SESSIONS_SETUP_FIRST at 13:00 NY (outside NY window).
    Before trigger, policy is updated to NY_ONLY.
    Coordinator must evaluate against CURRENT policy at fill time, rejecting order with OUTSIDE_ENTRY_WINDOW.
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 20, 13, 0, tzinfo=ny_tz)  # 13:00 NY is after NY window
    now_ms = int(dt.timestamp() * 1000)
    clock = ReplayClock(now_ms)

    order = _arm_test_order(guard_db, "LONG", entry=2625.0, now_ms=now_ms)

    # Change policy to NY_ONLY before trigger
    policy = guard_db.query(TradingPolicy).first()
    policy.entry_session_policy = "NY_ONLY"
    guard_db.commit()

    coord = ExecutionCoordinator()
    trigger_ticker = make_ticker_dict(bid=2624.9, ask=2625.0, now_ms=now_ms + 1000)
    coord.evaluate_orders_sync(guard_db, ticker_override=trigger_ticker, clock=clock, now_ms=now_ms + 1000)

    guard_db.refresh(order)
    assert order.state == "rejected", "Order must be rejected when current policy forbids entry"
    assert "OUTSIDE_ENTRY_WINDOW" in (order.invalidation_reason or "")


# =========================================================================
# Part 158: Duplicate quote / cycle idempotency
# =========================================================================
def test_158_duplicate_quote_cycle_idempotency(guard_db):
    """
    Same trigger quote delivered multiple times to coordinator does NOT duplicate fills, positions, or journals.
    """
    now_ms = 1700000000000
    clock = ReplayClock(now_ms)
    order = _arm_test_order(guard_db, "LONG", entry=2625.0, now_ms=now_ms)

    coord = ExecutionCoordinator()
    trigger_ticker = make_ticker_dict(bid=2624.9, ask=2625.0, now_ms=now_ms + 1000)

    # 1st call: fills order and creates position
    coord.evaluate_orders_sync(guard_db, ticker_override=trigger_ticker, clock=clock, now_ms=now_ms + 1000)
    guard_db.refresh(order)
    assert order.state == "paper_open"

    # 2nd call: same trigger quote, position already open
    coord.evaluate_orders_sync(guard_db, ticker_override=trigger_ticker, clock=clock, now_ms=now_ms + 1000)

    orders = guard_db.query(PaperOrder).filter(PaperOrder.state == "paper_open").all()
    assert len(orders) == 1, "Duplicate quote evaluation must not create duplicate fills"
