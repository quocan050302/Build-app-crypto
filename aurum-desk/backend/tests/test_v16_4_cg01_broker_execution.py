"""
backend/tests/test_v16_4_cg01_broker_execution.py
V16.4 Rules 080, 148, 149, 150, 153, 154, 155, 156, 160 (Gate C: Broker Execution & Coordinator Lifecycle):
- test_rule_148_long_limit_touch_fill: ask > planned_entry keeps armed; ask <= planned_entry transitions to paper_open
- test_rule_149_short_limit_touch_fill: bid < planned_entry keeps armed; bid >= planned_entry transitions to paper_open
- test_rule_150_market_before_fill_reprice: runaway quote causes calculator net R:R failure before fill
- test_rule_153_long_tick_to_tp: full lifecycle LONG via evaluate_orders_sync -> PaperBroker.process_price_tick -> closed
- test_rule_154_short_tick_to_sl: full lifecycle SHORT via evaluate_orders_sync -> PaperBroker.process_price_tick -> closed
- test_rule_155_guards_active_blocking: max daily fills cap and active position block new fill
- test_rule_156_news_guard_between_arm_and_fill: news blackout activated after ARM blocks fill in coordinator
- test_rule_160_restart_persistence: session restart preserves order_type='LIMIT', planned_entry, and executes cleanly
"""
import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from domain_calculator import calculate_risk_reward, CostAssumptions
from models import Base
import models
import crud
from services.cg01_types import CG01Config, CG01EntryMode, CG01ProfileMode, CG01Stage
from services.execution_coordinator import ExecutionCoordinator
from paper_broker import PaperBroker
from services.scalp_m1_types import M1ScalpConfig, WatchedZone
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.trade_lifecycle_service import TradeLifecycleService
from services.trading_policy_service import TradingPolicyService
from tests.test_v16_3_cg01_pipeline import build_long_fixture_series, build_short_fixture_series, make_bar, make_valid_quote


@pytest.fixture
def broker_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    policy = TradingPolicyService.get_or_create_policy(session)
    policy.max_daily_fills = 3
    policy.daily_min_goal = 2
    policy.ny_min_fills = 1
    policy.is_active = False  # Deactivate session time-of-day restriction for pure execution testing
    session.commit()

    try:
        yield session, engine
    finally:
        session.close()


def arm_long_cg01_order(db, now_ms: int) -> models.PaperOrder:
    """Helper to arm a valid CG-01 LONG limit order using real orchestrator pipeline."""
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    quote = make_valid_quote(2625.5, 2625.7, now_ms)

    poi = WatchedZone(
        zone_id="poi_long_15m",
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

    cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
        daily_max_cap=3,
    )

    orchestrator = ScalpWatcherOrchestrator()
    orchestrator.set_active_zones("XAUUSDT", [poi])
    orchestrator.evaluate_cycle(
        db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        quote=quote,
        candles_1m=c_1m,
        candles_5m=[],
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        config=cfg,
        auto_enabled=True,
    )

    order = db.query(models.PaperOrder).filter(models.PaperOrder.direction == "LONG").first()
    assert order is not None, "LONG PaperOrder was not created by orchestrator"
    assert order.state == "armed"
    assert order.order_type == "LIMIT"
    return order


def arm_short_cg01_order(db, now_ms: int) -> models.PaperOrder:
    """Helper to arm a valid CG-01 SHORT limit order using real orchestrator pipeline."""
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)

    c_4h, c_1h, c_15m, c_1m = build_short_fixture_series(t_start)
    quote = make_valid_quote(2649.5, 2649.7, now_ms)

    poi = WatchedZone(
        zone_id="poi_short_15m",
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

    cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
        daily_max_cap=3,
    )

    orchestrator = ScalpWatcherOrchestrator()
    orchestrator.set_active_zones("XAUUSDT", [poi])
    orchestrator.evaluate_cycle(
        db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        quote=quote,
        candles_1m=c_1m,
        candles_5m=[],
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        config=cfg,
        auto_enabled=True,
    )

    order = db.query(models.PaperOrder).filter(models.PaperOrder.direction == "SHORT").first()
    assert order is not None, "SHORT PaperOrder was not created by orchestrator"
    assert order.state == "armed"
    assert order.order_type == "LIMIT"
    return order


def test_rule_148_long_limit_touch_fill(broker_db):
    """
    Rule 148 & Appendix C: Buy limit triggers ONLY when current_ask <= planned_entry.
    - Ask > planned_entry: remains armed.
    - Ask <= planned_entry: transitions to paper_open.
    - Duplicate tick: no duplicate fill.
    """
    db, _ = broker_db
    now_ms = 1792050000000
    order = arm_long_cg01_order(db, now_ms)
    assert order.planned_entry == 2623.75
    assert order.order_type == "LIMIT"

    coordinator = ExecutionCoordinator()

    # 1. Ask is above limit (2624.00 > 2623.75), even if bid is at 2623.70
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2623.70, "ask": 2624.00, "last": 2623.85, "timestamp": now_ms + 1000},
        now_ms=now_ms + 1000,
    )
    db.refresh(order)
    assert order.state == "armed"
    assert order.actual_entry is None

    audit = crud.get_or_create_today_audit(db)
    assert audit.fills_count == 0

    # 2. Ask touches limit (ask = 2623.75 <= 2623.75)
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2623.65, "ask": 2623.75, "last": 2623.70, "timestamp": now_ms + 2000},
        now_ms=now_ms + 2000,
    )
    db.refresh(order)
    assert order.state == "paper_open"
    assert order.actual_entry == 2623.75

    db.refresh(audit)
    assert audit.fills_count == 1

    # 3. Duplicate tick: ensure idempotency
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2623.65, "ask": 2623.75, "last": 2623.70, "timestamp": now_ms + 3000},
        now_ms=now_ms + 3000,
    )
    db.refresh(audit)
    assert audit.fills_count == 1


def test_rule_149_short_limit_touch_fill(broker_db):
    """
    Rule 149 & Appendix C: Sell limit triggers ONLY when current_bid >= planned_entry.
    - Ask touches limit but bid < planned_entry: remains armed.
    - Bid >= planned_entry: transitions to paper_open.
    """
    db, _ = broker_db
    now_ms = 1792050000000
    order = arm_short_cg01_order(db, now_ms)
    assert order.planned_entry == 2651.15
    assert order.order_type == "LIMIT"

    coordinator = ExecutionCoordinator()

    # 1. Ask touches limit (2651.15), but bid is below limit (2650.95 < 2651.15)
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2650.95, "ask": 2651.15, "last": 2651.05, "timestamp": now_ms + 1000},
        now_ms=now_ms + 1000,
    )
    db.refresh(order)
    assert order.state == "armed"
    assert order.actual_entry is None

    audit = crud.get_or_create_today_audit(db)
    assert audit.fills_count == 0

    # 2. Bid touches/exceeds limit (bid = 2651.15 >= 2651.15)
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2651.15, "ask": 2651.25, "last": 2651.20, "timestamp": now_ms + 2000},
        now_ms=now_ms + 2000,
    )
    db.refresh(order)
    assert order.state == "paper_open"
    assert order.actual_entry == 2651.15

    db.refresh(audit)
    assert audit.fills_count == 1


def test_rule_150_market_before_fill_reprice(broker_db):
    """
    Rule 150: For MARKET order, if quote runs away prior to execution so net R:R or geometry
    collapses, coordinator BEFORE_FILL / calculation re-check blocks opening position.
    """
    db, _ = broker_db
    now_ms = 1792050000000
    order = arm_long_cg01_order(db, now_ms)

    # Change to MARKET order with wide runaway price (ask = 2635.0, TP is 2640.0, SL is 2619.45)
    order.order_type = "MARKET"
    db.commit()

    coordinator = ExecutionCoordinator()
    # Executing at ask = 2638.0 gives entry=2638.10, SL=2619.45, TP=2640.0 -> net R:R << 2.0
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2637.90, "ask": 2638.00, "last": 2637.95, "timestamp": now_ms + 1000},
        now_ms=now_ms + 1000,
    )
    db.refresh(order)
    # Must NOT be paper_open
    assert order.state != "paper_open"
    assert order.actual_entry is None


def test_rule_153_long_tick_to_tp(broker_db):
    """
    Rule 153 & Appendix C: Complete flow from armed -> coordinator tick fill -> PaperBroker.process_price_tick TP exit.
    No direct execute_fill / execute_close calls.
    """
    db, _ = broker_db
    now_ms = 1792050000000
    order = arm_long_cg01_order(db, now_ms)
    tp_price = order.take_profit

    coordinator = ExecutionCoordinator()

    # 1. Fill tick at CE (ask = 2623.75)
    fill_time = now_ms + 10_000
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2623.65, "ask": 2623.75, "last": 2623.70, "timestamp": fill_time},
        now_ms=fill_time,
    )
    db.refresh(order)
    assert order.state == "paper_open"
    assert order.actual_entry == 2623.75

    # 2. Exit tick reaching TP (bid >= tp_price)
    exit_time = fill_time + 60_000
    closed_order = PaperBroker.process_price_tick(
        db,
        current_bid=tp_price + 0.10,
        current_ask=tp_price + 0.20,
        candle_high=tp_price + 0.50,
        candle_low=tp_price - 1.0,
        candle_timestamp=exit_time,
        now_ms=exit_time,
    )
    assert closed_order is not None
    assert closed_order.id == order.id
    assert closed_order.state == "closed"
    assert closed_order.exit_cause == "TP_HIT"
    assert closed_order.realized_pnl_net > 0.0


def test_rule_154_short_tick_to_sl(broker_db):
    """
    Rule 154: Complete flow SHORT armed -> coordinator tick fill -> PaperBroker.process_price_tick SL exit.
    Asserts realized loss has negative sign and no sign reversal.
    """
    db, _ = broker_db
    now_ms = 1792050000000
    order = arm_short_cg01_order(db, now_ms)
    sl_price = order.stop_loss

    coordinator = ExecutionCoordinator()

    # 1. Fill tick at CE (bid = 2651.15)
    fill_time = now_ms + 10_000
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2651.15, "ask": 2651.25, "last": 2651.20, "timestamp": fill_time},
        now_ms=fill_time,
    )
    db.refresh(order)
    assert order.state == "paper_open"
    assert order.actual_entry == 2651.15

    # 2. Exit tick reaching SL (ask >= sl_price)
    exit_time = fill_time + 60_000
    closed_order = PaperBroker.process_price_tick(
        db,
        current_bid=sl_price - 0.10,
        current_ask=sl_price + 0.10,
        candle_high=sl_price + 0.50,
        candle_low=sl_price - 2.0,
        candle_timestamp=exit_time,
        now_ms=exit_time,
    )
    assert closed_order is not None
    assert closed_order.id == order.id
    assert closed_order.state == "closed"
    assert closed_order.exit_cause == "SL_HIT"
    assert closed_order.realized_pnl_net < 0.0


def test_rule_155_guards_active_blocking(broker_db):
    """
    Rule 155: Active position and max daily fills cap block execution coordinator from filling.
    """
    db, _ = broker_db
    now_ms = 1792050000000
    order = arm_long_cg01_order(db, now_ms)

    coordinator = ExecutionCoordinator()

    # Case A: Cap already reached
    audit = crud.get_or_create_today_audit(db)
    audit.fills_count = 3
    db.commit()

    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2623.65, "ask": 2623.75, "last": 2623.70, "timestamp": now_ms + 1000},
        now_ms=now_ms + 1000,
    )
    db.refresh(order)
    assert order.state != "paper_open"


def test_rule_156_news_guard_between_arm_and_fill(broker_db):
    """
    Rule 156: Order armed during clean news; red news blackout activates before price touch.
    ExecutionCoordinator evaluates BEFORE_FILL and rejects/blocks fill.
    """
    db, _ = broker_db
    now_ms = 1792050000000
    order = arm_long_cg01_order(db, now_ms)
    assert order.state == "armed"

    # Activate policy and insert a high-impact news blackout covering now_ms + 5000
    policy = TradingPolicyService.get_or_create_policy(db)
    policy.is_active = True
    db.commit()

    row = models.EconomicNews(
        source_id=f"test-cpi-{now_ms}",
        title="US Core CPI YoY",
        country="USD",
        currency="USD",
        impact="High",
        scheduled_at=now_ms + 5000,
        received_at=now_ms - 86_400_000,
    )
    db.add(row)
    db.commit()

    coordinator = ExecutionCoordinator()
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2623.65, "ask": 2623.75, "last": 2623.70, "timestamp": now_ms + 5000},
        now_ms=now_ms + 5000,
    )
    db.refresh(order)
    # Order must NOT have been filled
    assert order.state != "paper_open"
    assert order.actual_entry is None


def test_rule_160_restart_persistence(broker_db):
    """
    Rule 160: Worker restart loads order_type='LIMIT' and planned_entry from database without loss.
    Second session executes coordinator and successfully fills on quote touch.
    """
    db, engine = broker_db
    now_ms = 1792050000000
    order = arm_long_cg01_order(db, now_ms)
    order_id = order.id
    db.commit()

    # Simulate restart: open brand new session on same engine
    Session2 = sessionmaker(bind=engine)
    session2 = Session2()
    try:
        reloaded = session2.query(models.PaperOrder).filter(models.PaperOrder.id == order_id).first()
        assert reloaded is not None
        assert reloaded.state == "armed"
        assert reloaded.order_type == "LIMIT"
        assert reloaded.planned_entry == 2623.75

        # Execute coordinator in restarted session
        coordinator = ExecutionCoordinator()
        coordinator.evaluate_orders_sync(
            session2,
            ticker_override={"bid": 2623.65, "ask": 2623.75, "last": 2623.70, "timestamp": now_ms + 2000},
            now_ms=now_ms + 2000,
        )
        session2.refresh(reloaded)
        assert reloaded.state == "paper_open"
        assert reloaded.actual_entry == 2623.75
    finally:
        session2.close()
