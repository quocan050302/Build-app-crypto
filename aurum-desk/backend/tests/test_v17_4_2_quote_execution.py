"""
backend/tests/test_v17_4_2_quote_execution.py
Comprehensive test suite for Quote Execution Parity & Guarded Fills (QE01 - QE08).
Verifies coordinator behavior with raw dict and ValidatedQuote object across triggers, staleness, and lifecycles.
"""
import json
import uuid
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import crud
from database import run_schema_migrations
from models import Base, PaperOrder, SystemConfig, TradingPolicy
from policy_constants import ALL_SESSIONS_SETUP_FIRST
from services.clock import ReplayClock
from services.execution_coordinator import ExecutionCoordinator
from services.execution_consumer import execution_consumer
from services.quote_validator import QuoteValidator


@pytest.fixture
def exec_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'exec_parity.db'}")
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


def _arm_order(db, direction="LONG", entry=2625.0, sl=None, tp=None, now_ms=1700000000000, expires_at=None):
    if sl is None:
        sl = 2622.0 if direction == "LONG" else 2628.0
    if tp is None:
        tp = 2635.0 if direction == "LONG" else 2615.0

    if direction == "SHORT":
        entry = 2630.0
        sl = 2633.0
        tp = 2620.0

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
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def test_qe01_raw_fresh_long_trigger_fills(exec_db):
    """QE01: Raw dict ticker with ask <= planned_entry fills BUY_LIMIT order to paper_open."""
    now_ms = 1700000000000
    clock = ReplayClock(now_ms)
    order = _arm_order(exec_db, "LONG", entry=2625.0, now_ms=now_ms)

    coord = ExecutionCoordinator()
    raw_ticker = {
        "symbol": "XAUUSDT",
        "bid": 2624.8,
        "ask": 2625.0,
        "last": 2624.9,
        "timestamp": now_ms,
        "observed_at": now_ms,
    }
    coord.evaluate_orders_sync(exec_db, ticker_override=raw_ticker, clock=clock, now_ms=now_ms)

    exec_db.refresh(order)
    assert order.state == "paper_open"
    pos = crud.get_active_position(exec_db)
    assert pos is not None
    assert pos.direction == "LONG"
    assert pos.state == "paper_open"


def test_qe02_object_fresh_short_trigger_fills(exec_db):
    """QE02: Fresh ValidatedQuote object with bid >= planned_entry fills SELL_LIMIT order to paper_open."""
    now_ms = 1700000000000
    clock = ReplayClock(now_ms)
    order = _arm_order(exec_db, "SHORT", entry=2630.0, now_ms=now_ms)

    coord = ExecutionCoordinator()
    q = QuoteValidator.validate_ticker({
        "symbol": "XAUUSDT",
        "bid": 2630.0,
        "ask": 2630.2,
        "last": 2630.1,
        "server_time": now_ms,
        "observed_at": now_ms,
    }, now_ms=now_ms)

    coord.evaluate_orders_sync(exec_db, ticker_override=q, clock=clock, now_ms=now_ms)

    exec_db.refresh(order)
    assert order.state == "paper_open"
    pos = crud.get_active_position(exec_db)
    assert pos is not None
    assert pos.direction == "SHORT"
    assert pos.state == "paper_open"


def test_qe03_object_stale_at_fill_blocks_entry(exec_db):
    """QE03: Reusing a ValidatedQuote validated at T0 when executing at T0+60s is detected as stale and does NOT fill."""
    t0 = 1700000000000
    clock = ReplayClock(t0)
    order = _arm_order(exec_db, "LONG", entry=2625.0, now_ms=t0, expires_at=t0 + 300_000)

    # Validated at t0
    stale_q = QuoteValidator.validate_ticker({
        "symbol": "XAUUSDT",
        "bid": 2624.8,
        "ask": 2625.0,
        "last": 2624.9,
        "server_time": t0,
        "observed_at": t0,
    }, now_ms=t0)
    assert stale_q.is_valid

    # Advance clock to t0 + 60s (order expires at t0 + 300s, so TTL remains)
    now_eval = t0 + 60_000
    clock.set_time(now_eval)

    coord = ExecutionCoordinator()
    coord.evaluate_orders_sync(exec_db, ticker_override=stale_q, clock=clock, now_ms=now_eval)

    exec_db.refresh(order)
    assert order.state == "armed", "Order must remain armed when quote is stale at fill time"
    assert crud.get_active_position(exec_db) is None


def test_qe04_fresh_after_stale_fills_if_ttl_remains(exec_db):
    """QE04: After being held back by a stale quote, a subsequent fresh quote triggers fill if TTL remains."""
    t0 = 1700000000000
    clock = ReplayClock(t0)
    order = _arm_order(exec_db, "LONG", entry=2625.0, now_ms=t0, expires_at=t0 + 300_000)

    # 1. Stale quote evaluation
    stale_q = QuoteValidator.validate_ticker({
        "bid": 2624.8, "ask": 2625.0, "last": 2624.9,
        "server_time": t0, "observed_at": t0,
    }, now_ms=t0)

    coord = ExecutionCoordinator()
    clock.set_time(t0 + 60_000)
    coord.evaluate_orders_sync(exec_db, ticker_override=stale_q, clock=clock, now_ms=t0 + 60_000)
    exec_db.refresh(order)
    assert order.state == "armed"

    # 2. Fresh quote arrives at t0 + 61_000
    fresh_q = QuoteValidator.validate_ticker({
        "bid": 2624.8, "ask": 2625.0, "last": 2624.9,
        "server_time": t0 + 61_000, "observed_at": t0 + 61_000,
    }, now_ms=t0 + 61_000)
    clock.set_time(t0 + 61_000)
    coord.evaluate_orders_sync(exec_db, ticker_override=fresh_q, clock=clock, now_ms=t0 + 61_000)

    exec_db.refresh(order)
    assert order.state == "paper_open"
    assert crud.get_active_position(exec_db) is not None


def test_qe05_last_touch_only_does_not_fill(exec_db):
    """QE05: Last price touched entry, but ask > planned_entry: order remains armed."""
    now_ms = 1700000000000
    clock = ReplayClock(now_ms)
    order = _arm_order(exec_db, "LONG", entry=2625.0, now_ms=now_ms)

    coord = ExecutionCoordinator()
    no_touch_q = QuoteValidator.validate_ticker({
        "bid": 2624.9, "ask": 2625.4, "last": 2625.0,
        "server_time": now_ms, "observed_at": now_ms,
    }, now_ms=now_ms)

    coord.evaluate_orders_sync(exec_db, ticker_override=no_touch_q, clock=clock, now_ms=now_ms)
    exec_db.refresh(order)
    assert order.state == "armed"
    assert crud.get_active_position(exec_db) is None


def test_qe06_now_equals_expiry_expires_order_regardless_of_quote(exec_db):
    """QE06: If now >= expires_at, order is transitioned to expired even if the feed quote is invalid."""
    now_ms = 1700000000000
    clock = ReplayClock(now_ms)
    order = _arm_order(exec_db, "LONG", entry=2625.0, now_ms=now_ms, expires_at=now_ms + 10_000)

    # Advance clock to expiry
    clock.set_time(now_ms + 10_000)

    coord = ExecutionCoordinator()
    # Invalid quote (bid=0)
    invalid_q = {
        "bid": 0.0, "ask": 0.0, "last": 0.0,
        "server_time": now_ms + 10_000, "observed_at": now_ms + 10_000,
    }
    coord.evaluate_orders_sync(exec_db, ticker_override=invalid_q, clock=clock, now_ms=now_ms + 10_000)

    exec_db.refresh(order)
    assert order.state == "expired"
    assert crud.get_active_position(exec_db) is None


def test_qe07_fresh_quote_with_degraded_feed_is_blocked(exec_db):
    """QE07: Even with a fresh valid quote, degraded feed health blocks entry."""
    now_ms = 1700000000000
    clock = ReplayClock(now_ms)
    order = _arm_order(exec_db, "LONG", entry=2625.0, now_ms=now_ms)

    execution_consumer.has_execution_gap = True
    try:
        coord = ExecutionCoordinator()
        fresh_q = QuoteValidator.validate_ticker({
            "bid": 2624.8, "ask": 2625.0, "last": 2624.9,
            "server_time": now_ms, "observed_at": now_ms,
        }, now_ms=now_ms)

        coord.evaluate_orders_sync(exec_db, ticker_override=fresh_q, clock=clock, now_ms=now_ms)

        exec_db.refresh(order)
        # Order must not fill when feed is degraded
        assert order.state == "armed"
        assert crud.get_active_position(exec_db) is None
    finally:
        execution_consumer.has_execution_gap = False


def test_qe08_duplicate_quote_does_not_duplicate_fill(exec_db):
    """QE08: Evaluating the same fill-trigger quote twice does not create duplicate positions or double-fill."""
    now_ms = 1700000000000
    clock = ReplayClock(now_ms)
    order = _arm_order(exec_db, "LONG", entry=2625.0, now_ms=now_ms)

    coord = ExecutionCoordinator()
    fresh_q = QuoteValidator.validate_ticker({
        "bid": 2624.8, "ask": 2625.0, "last": 2624.9,
        "server_time": now_ms, "observed_at": now_ms,
    }, now_ms=now_ms)

    # First evaluation fills
    coord.evaluate_orders_sync(exec_db, ticker_override=fresh_q, clock=clock, now_ms=now_ms)
    exec_db.refresh(order)
    assert order.state == "paper_open"

    # Second evaluation with same quote
    coord.evaluate_orders_sync(exec_db, ticker_override=fresh_q, clock=clock, now_ms=now_ms)
    exec_db.refresh(order)
    assert order.state == "paper_open"

    # Exactly 1 active order / open position
    open_orders = exec_db.query(crud.models.PaperOrder).filter(crud.models.PaperOrder.state == "paper_open").all()
    assert len(open_orders) == 1
    assert open_orders[0].id == order.id
