"""
backend/tests/test_v17_4_1_coordinator_flow.py
V17.4.1 Causal Coordinator Execution & Lifecycle Tests (Parts 121 - 146, 159, 160)

Elevates production flow testing from force-fill lifecycle unittests to end-to-end
evaluator-triggered flows through ExecutionCoordinator and process_exit_tick:
1. Asia LONG (20:30 NY)
2. Asia SHORT (20:30 NY)
3. London LONG outside 03-04 NY (05:00 NY)
4. London SHORT outside 03-04 NY (05:00 NY)
5. NY LONG post-11:00 (11:30 NY)
6. NY SHORT post-11:00 (11:30 NY)
7. Session boundary pending: pending order with valid TTL survives session boundaries (Part 159).
8. Position preservation: open position is preserved across session settings changes (Part 160).
"""
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import run_schema_migrations
from models import Base, PaperOrder, SystemConfig, TradingPolicy
from policy_constants import ALL_SESSIONS_SETUP_FIRST
from services.clock import ReplayClock
from services.execution_coordinator import ExecutionCoordinator
from services.scalp_m1_types import M1ScalpConfig, WatchedZone
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.trade_lifecycle_service import TradeLifecycleService
from services.entry_session_policy_service import update_entry_session_settings
from tests.test_v16_3_cg01_pipeline import (
    build_long_fixture_series,
    build_short_fixture_series,
    make_valid_quote,
)


@pytest.fixture
def coordinator_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/coord_flow.db")
    Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    SessionFactory = sessionmaker(bind=engine)
    session = SessionFactory()

    policy = TradingPolicy(
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


def _run_coordinator_long_flow(db, t_start: int):
    now_ms = t_start + 540_000
    clock = ReplayClock(now_ms)
    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    poi = WatchedZone(
        zone_id=f"poi_long_{t_start}",
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

    # 1. Orchestrator cycle arms candidate
    orchestrator.evaluate_cycle(
        db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        quote=quote_eval,
        candles_1m=c_1m,
        candles_5m=[],
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        config=cfg,
        auto_enabled=True,
    )

    order = db.query(PaperOrder).filter(PaperOrder.state == "armed").order_by(PaperOrder.created_at.desc()).first()
    assert order is not None, "Order must be armed"
    assert order.direction == "LONG"
    planned_entry = order.planned_entry
    sl = order.stop_loss
    tp = order.take_profit
    assert tp > planned_entry > sl

    # 2. No-touch quote: ask is strictly above entry (e.g. entry 2623.75, ask 2625.0)
    coord = ExecutionCoordinator()
    quote_no_touch = make_valid_quote(planned_entry + 1.0, planned_entry + 1.25, now_ms + 10_000)
    coord.evaluate_orders_sync(db, ticker_override=quote_no_touch, clock=clock, now_ms=now_ms + 10_000)

    db.refresh(order)
    assert order.state == "armed", "Order must remain armed when ask does not reach limit entry"

    # 3. Trigger quote: Ask touches or dips below planned_entry
    fill_time = now_ms + 30_000
    clock.set_time(fill_time)
    quote_trigger = make_valid_quote(planned_entry - 0.20, planned_entry, fill_time)
    coord.evaluate_orders_sync(db, ticker_override=quote_trigger, clock=clock, now_ms=fill_time)

    db.refresh(order)
    assert order.state == "paper_open", f"Coordinator must fill order when ask <= planned_entry, got {order.state}"
    assert order.opened_at == fill_time
    assert order.actual_entry == planned_entry
    assert order.quantity > 0

    # 4. Exit quote: Price reaches take profit (LONG exits at bid >= TP)
    exit_time = fill_time + 60_000
    clock.set_time(exit_time)
    closed = TradeLifecycleService.process_exit_tick(
        db,
        current_bid=tp,
        current_ask=tp + 0.20,
        now_ms=exit_time,
        clock=clock,
        tick_timestamp=exit_time,
    )
    assert closed is not None, "process_exit_tick must detect TP_HIT"
    assert closed.id == order.id
    assert closed.state == "closed"
    assert closed.exit_cause == "TP_HIT"
    assert closed.realized_pnl_net > 0.0

    return closed


def _run_coordinator_short_flow(db, t_start: int):
    now_ms = t_start + 540_000
    clock = ReplayClock(now_ms)
    c_4h, c_1h, c_15m, c_1m = build_short_fixture_series(t_start)
    poi = WatchedZone(
        zone_id=f"poi_short_{t_start}",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BEARISH",
        top=2655.0,
        bottom=2650.0,
        created_at=t_start - 3600_000,
        known_at=t_start - 1800_000,
        touch_count=0,
    )
    quote_eval = make_valid_quote(2649.5, 2649.7, now_ms)
    cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
        timing_profile="CG01_ALL_SESSIONS_PAPER",
    )

    orchestrator = ScalpWatcherOrchestrator()
    orchestrator.set_active_zones("XAUUSDT", [poi])

    orchestrator.evaluate_cycle(
        db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        quote=quote_eval,
        candles_1m=c_1m,
        candles_5m=[],
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        config=cfg,
        auto_enabled=True,
    )

    order = db.query(PaperOrder).filter(PaperOrder.state == "armed").order_by(PaperOrder.created_at.desc()).first()
    assert order is not None, "Order must be armed"
    assert order.direction == "SHORT"
    planned_entry = order.planned_entry
    sl = order.stop_loss
    tp = order.take_profit
    assert tp < planned_entry < sl

    # No-touch quote: bid is strictly below entry (e.g. planned 2651.0, bid 2649.5)
    coord = ExecutionCoordinator()
    quote_no_touch = make_valid_quote(planned_entry - 1.5, planned_entry - 1.3, now_ms + 10_000)
    coord.evaluate_orders_sync(db, ticker_override=quote_no_touch, clock=clock, now_ms=now_ms + 10_000)

    db.refresh(order)
    assert order.state == "armed"

    # Trigger quote: bid touches or crosses above planned_entry (SELL LIMIT triggers on bid >= limit)
    fill_time = now_ms + 30_000
    clock.set_time(fill_time)
    quote_trigger = make_valid_quote(planned_entry, planned_entry + 0.20, fill_time)
    coord.evaluate_orders_sync(db, ticker_override=quote_trigger, clock=clock, now_ms=fill_time)

    db.refresh(order)
    assert order.state == "paper_open", f"Coordinator must fill SHORT order when bid >= planned_entry, got {order.state}"
    assert order.opened_at == fill_time
    assert order.actual_entry == planned_entry
    assert order.quantity > 0

    # Exit quote: Price reaches TP (SHORT exits at ask <= TP)
    exit_time = fill_time + 60_000
    clock.set_time(exit_time)
    closed = TradeLifecycleService.process_exit_tick(
        db,
        current_bid=tp - 0.20,
        current_ask=tp,
        now_ms=exit_time,
        clock=clock,
        tick_timestamp=exit_time,
    )
    assert closed is not None
    assert closed.id == order.id
    assert closed.state == "closed"
    assert closed.exit_cause == "TP_HIT"
    assert closed.realized_pnl_net > 0.0

    return closed


def test_part_141_positive_asia_long_coordinator_flow(coordinator_db):
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 20, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    closed = _run_coordinator_long_flow(coordinator_db, t_start)
    assert closed.exit_cause == "TP_HIT"


def test_part_142_positive_asia_short_coordinator_flow(coordinator_db):
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 20, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    closed = _run_coordinator_short_flow(coordinator_db, t_start)
    assert closed.exit_cause == "TP_HIT"


def test_part_143_positive_london_long_coordinator_flow(coordinator_db):
    # 05:00 NY is in London session, strictly outside legacy 03:00-04:00 window
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 5, 0, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    closed = _run_coordinator_long_flow(coordinator_db, t_start)
    assert closed.exit_cause == "TP_HIT"


def test_part_144_positive_london_short_coordinator_flow(coordinator_db):
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 5, 0, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    closed = _run_coordinator_short_flow(coordinator_db, t_start)
    assert closed.exit_cause == "TP_HIT"


def test_part_145_positive_ny_long_post_eleven_coordinator_flow(coordinator_db):
    # 11:30 NY is after the legacy 08:00 - 11:00 NY entry window
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 11, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    closed = _run_coordinator_long_flow(coordinator_db, t_start)
    assert closed.exit_cause == "TP_HIT"


def test_part_146_positive_ny_short_post_eleven_coordinator_flow(coordinator_db):
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 11, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    closed = _run_coordinator_short_flow(coordinator_db, t_start)
    assert closed.exit_cause == "TP_HIT"


def test_part_159_session_boundary_pending_order_preserved(coordinator_db):
    """Part 159: Pending order whose TTL extends beyond session window is not expired by boundary."""
    ny_tz = ZoneInfo("America/New_York")
    # 10:55 NY, order armed with 15-minute TTL (expires at 11:10 NY)
    dt = datetime(2026, 10, 15, 10, 55, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 540_000

    order = PaperOrder(
        id="po_part_159_boundary",
        instrument="XAUUSDT",
        direction="LONG",
        order_type="LIMIT",
        planned_entry=2623.75,
        stop_loss=2619.45,
        take_profit=2635.0,
        quantity=0.15,
        initial_risk_usdt=10.0,
        state="armed",
        created_at=now_ms,
        expires_at=now_ms + 900_000, # 15 minutes
        config_version=1,
    )
    coordinator_db.add(order)
    coordinator_db.commit()

    # Time advances across 11:00 NY window boundary to 11:05 NY (expires_at is 11:09)
    dt_after_boundary = datetime(2026, 10, 15, 11, 5, tzinfo=ny_tz)
    check_time = int(dt_after_boundary.timestamp() * 1000)
    clock = ReplayClock(check_time)

    coord = ExecutionCoordinator()
    quote = make_valid_quote(2625.0, 2625.2, check_time)
    coord.evaluate_orders_sync(coordinator_db, ticker_override=quote, clock=clock, now_ms=check_time)

    coordinator_db.refresh(order)
    assert order.state == "armed", "Order must not expire before its TTL merely because NY session window passed"


def test_part_160_position_preserved_across_config_save(coordinator_db):
    """Part 160: Open position is preserved when session settings are updated."""
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 20, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)

    # Run flow to create an open position
    now_ms = t_start + 540_000
    clock = ReplayClock(now_ms)
    coord = ExecutionCoordinator()

    pos = PaperOrder(
        id="pos_part_160_preserved",
        instrument="XAUUSDT",
        direction="LONG",
        order_type="LIMIT",
        planned_entry=2623.75,
        actual_entry=2623.75,
        quantity=0.15,
        initial_risk_usdt=10.0,
        stop_loss=2619.45,
        take_profit=2635.0,
        state="paper_open",
        opened_at=now_ms,
        created_at=now_ms,
        config_version=1,
    )
    coordinator_db.add(pos)
    coordinator_db.commit()

    # Now perform a session setting update in the background
    update_entry_session_settings(
        coordinator_db,
        {
            "entry_session_policy": "ALL_SESSIONS_WITH_NY_RESERVE",
            "timing_profile": "SILVER_BULLET_LONDON_NY",
            "expected_version": 1,
        },
        now_ms=now_ms + 10_000,
    )

    # Position must remain intact
    coordinator_db.refresh(pos)
    assert pos.state == "paper_open"
    assert pos.actual_entry == 2623.75
    assert pos.quantity == 0.15
    assert pos.take_profit == 2635.0

    # Exit evaluator can still close it cleanly
    exit_time = now_ms + 60_000
    clock.set_time(exit_time)
    closed = TradeLifecycleService.process_exit_tick(
        coordinator_db,
        current_bid=pos.take_profit,
        current_ask=pos.take_profit + 0.2,
        now_ms=exit_time,
        clock=clock,
        tick_timestamp=exit_time,
    )
    assert closed is not None
    assert closed.state == "closed"
    assert closed.exit_cause == "TP_HIT"
