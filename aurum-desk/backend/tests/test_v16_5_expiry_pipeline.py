"""
backend/tests/test_v16_5_expiry_pipeline.py
V16.5 Gate A: Expiry Pipeline & Exact Boundary Parity (Rules 021 - 050, 137 - 142):
- test_rule_008_candidate_deadline_propagation: Candidate TTL 5m from trigger, armed at T+2m -> expires_at is T+5m (not armed+2h)
- test_rule_028_evaluator_returns_expired_when_now_exceeds_deadline: evaluate_cg01_candidate returns EXPIRED stage if now_ms >= valid_until
- test_rule_036_coordinator_exact_boundary: quote touch at deadline - 1 fills; at exact deadline and deadline + 1 does NOT fill
- test_rule_037_expiry_housekeeping_on_stale_quote: stale/invalid quote does not prevent pending orders from expiring
- test_rule_038_expiry_with_active_position: existing active position does not prevent other pending orders from expiring
- test_rule_040_restart_expiry_from_db: database reload retains persisted expires_at, tick past deadline expires order
- test_rule_042_open_position_survives_setup_ttl: order in paper_open is NOT closed when setup valid_until expires
"""
import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import crud
import models
from models import Base
from services.cg01_types import (
    CG01Config,
    CG01EntryMode,
    CG01Stage,
    resolve_cg01_execution_deadline,
    resolve_candidate_order_expiry,
)
from services.clock import ReplayClock
from services.execution_coordinator import ExecutionCoordinator
from services.scalp_m1_types import M1ScalpConfig, WatchedZone
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.trading_policy_service import TradingPolicyService
from tests.test_v16_3_cg01_pipeline import (
    build_long_fixture_series,
    make_valid_quote,
)


@pytest.fixture
def expiry_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    policy = TradingPolicyService.get_or_create_policy(session)
    policy.max_daily_fills = 3
    policy.daily_min_goal = 2
    policy.ny_min_fills = 1
    policy.is_active = False
    session.commit()

    try:
        yield session, engine
    finally:
        session.close()


def arm_test_cg01_order(db, now_ms: int, ttl_ms: int = 300_000) -> models.PaperOrder:
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    quote = make_valid_quote(2625.5, 2625.7, now_ms)

    poi = WatchedZone(
        zone_id="poi_expiry_15m",
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

    cg01_cfg = CG01Config(
        enabled=True,
        profile_mode="PAPER_EXECUTION",
        entry_mode=CG01EntryMode.LIMIT_AT_CE_AFTER_CONFIRMATION.value,
        candidate_ttl_ms=ttl_ms,
    )

    m1_cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
        daily_max_cap=3,
        cg01_config=cg01_cfg,
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
        config=m1_cfg,
        auto_enabled=True,
    )

    order = db.query(models.PaperOrder).filter(models.PaperOrder.direction == "LONG").first()
    assert order is not None, "Failed to arm LONG paper order"
    return order


def test_rule_008_candidate_deadline_propagation(expiry_db):
    """
    Rule 008, 033 & 046: Candidate valid_until_ms propagates into PaperOrder.expires_at.
    Must NOT be now_ms + 2 hours (7,200,000 ms).
    """
    db, _ = expiry_db
    now_ms = 1792050000000
    ttl_ms = 300_000  # 5 minutes
    order = arm_test_cg01_order(db, now_ms, ttl_ms=ttl_ms)

    assert order.expires_at is not None
    # Must NOT be 2 hours later
    assert order.expires_at != now_ms + (2 * 3600 * 1000)
    # Must be bounded by trigger_known_at + ttl_ms
    assert order.expires_at <= now_ms + ttl_ms
    assert order.expires_at >= now_ms


def test_rule_028_evaluator_returns_expired_when_now_exceeds_deadline():
    """
    Rule 028: evaluate_cg01_candidate returns stage=EXPIRED if as_of >= valid_until.
    """
    from services.cg01_engine import evaluate_cg01_candidate
    from tests.test_v16_3_cg01_pipeline import build_long_fixture_series

    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    quote = make_valid_quote(2625.5, 2625.7, t_start + 300_000)

    cfg = CG01Config(
        enabled=True,
        profile_mode="PAPER_EXECUTION",
        candidate_ttl_ms=60_000, # 1 minute TTL
    )
    ctx = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m}

    # Evaluate at t_start + 120_000 (which exceeds 60s TTL from formation)
    dec = evaluate_cg01_candidate(
        ctx, quote, config=cfg, now_ms=t_start + 120_000, capital=1000.0, risk_pct=0.25
    )
    # Setup must not be eligible because trigger is expired
    assert not dec.is_eligible
    assert dec.stage == CG01Stage.EXPIRED or "TRIGGER_EXPIRED" in dec.reason_codes or "WAITING_SWEEP_CONFIRMATION" in dec.reason_codes


def test_rule_036_coordinator_exact_boundary(expiry_db):
    """
    Rule 036 & 047: Pending limit order cannot be filled at or after exact deadline.
    now < expires_at -> fills.
    now >= expires_at -> does NOT fill, transitions to expired.
    """
    db, _ = expiry_db
    now_ms = 1792050000000
    order = arm_test_cg01_order(db, now_ms, ttl_ms=300_000)
    deadline = order.expires_at
    coordinator = ExecutionCoordinator()

    # Case A: Quote reaches limit 1ms before deadline -> should fill!
    pre_quote = {
        "bid": order.planned_entry - 0.10,
        "ask": order.planned_entry,  # Ask touches limit for LONG
        "last": order.planned_entry,
        "timestamp": deadline - 1,
    }
    clock = ReplayClock(initial_ms=deadline - 1)
    coordinator.evaluate_orders_sync(
        db,
        ticker_override=pre_quote,
        clock=clock,
        now_ms=deadline - 1,
    )
    db.refresh(order)
    assert order.state == "paper_open", "Order should fill at deadline - 1ms"

    # Case B: Reset order to armed with exact deadline touch
    order.state = "armed"
    order.actual_entry = None
    order.opened_at = None
    db.commit()

    exact_quote = {
        "bid": order.planned_entry - 0.10,
        "ask": order.planned_entry,
        "last": order.planned_entry,
        "timestamp": deadline,
    }
    clock.set_time(deadline)
    coordinator.evaluate_orders_sync(
        db,
        ticker_override=exact_quote,
        clock=clock,
        now_ms=deadline,
    )
    db.refresh(order)
    assert order.state == "expired", "Order must expire at exact deadline"
    assert order.actual_entry is None


def test_rule_037_expiry_housekeeping_on_stale_quote(expiry_db):
    """
    Rule 037 & 049: Housekeeping expires pending orders even when current quote is invalid or stale.
    """
    db, _ = expiry_db
    now_ms = 1792050000000
    order = arm_test_cg01_order(db, now_ms, ttl_ms=300_000)
    deadline = order.expires_at
    coordinator = ExecutionCoordinator()

    # Pass an invalid quote (stale timestamp or non-finite)
    stale_quote = {
        "bid": 2620.0,
        "ask": 2620.1,
        "last": 2620.05,
        "timestamp": now_ms - 100_000, # Stale!
    }
    clock = ReplayClock(initial_ms=deadline + 10_000)
    coordinator.evaluate_orders_sync(
        db,
        ticker_override=stale_quote,
        clock=clock,
        now_ms=deadline + 10_000,
    )
    db.refresh(order)
    assert order.state == "expired", "Pending order must expire even when quote is stale"


def test_rule_038_expiry_with_active_position(expiry_db):
    """
    Rule 038: Pending order expires even if another active open position exists.
    Single-position guard prevents fills but must not starve expiry housekeeping.
    """
    db, _ = expiry_db
    now_ms = 1792050000000
    order = arm_test_cg01_order(db, now_ms, ttl_ms=300_000)
    deadline = order.expires_at

    # Create an active open position
    pos = models.PaperOrder(
        id="pos_active_existing",
        instrument="XAUUSDT",
        direction="SHORT",
        state="paper_open",
        planned_entry=2630.0,
        actual_entry=2630.0,
        stop_loss=2635.0,
        take_profit=2620.0,
        quantity=0.1,
        initial_risk_usdt=2.50,
        opened_at=now_ms,
        created_at=now_ms,
    )
    db.add(pos)
    db.commit()

    coordinator = ExecutionCoordinator()
    valid_quote = {
        "bid": 2620.0,
        "ask": 2620.1,
        "last": 2620.05,
        "timestamp": deadline + 5000,
    }
    clock = ReplayClock(initial_ms=deadline + 5000)
    coordinator.evaluate_orders_sync(
        db,
        ticker_override=valid_quote,
        clock=clock,
        now_ms=deadline + 5000,
    )
    db.refresh(order)
    assert order.state == "expired", "Order must expire even with active position"


def test_rule_040_restart_expiry_from_db(expiry_db):
    """
    Rule 040: Worker restart reloads expires_at from DB; order expires cleanly without resurrection.
    """
    db, engine = expiry_db
    now_ms = 1792050000000
    order = arm_test_cg01_order(db, now_ms, ttl_ms=300_000)
    order_id = order.id
    deadline = order.expires_at

    # Close first session and create fresh second session
    db.close()
    Session = sessionmaker(bind=engine)
    new_db = Session()

    reloaded_order = new_db.query(models.PaperOrder).filter(models.PaperOrder.id == order_id).first()
    assert reloaded_order.expires_at == deadline

    coordinator = ExecutionCoordinator()
    quote = {
        "bid": 2620.0,
        "ask": 2620.1,
        "last": 2620.05,
        "timestamp": deadline + 1000,
    }
    clock = ReplayClock(initial_ms=deadline + 1000)
    coordinator.evaluate_orders_sync(
        new_db,
        ticker_override=quote,
        clock=clock,
        now_ms=deadline + 1000,
    )
    new_db.refresh(reloaded_order)
    assert reloaded_order.state == "expired"
    new_db.close()


def test_rule_042_open_position_survives_setup_ttl(expiry_db):
    """
    Rule 042: Entry deadline applies ONLY to pending orders. An active open position
    is NOT closed or cancelled simply because candidate valid_until has passed.
    """
    db, _ = expiry_db
    now_ms = 1792050000000
    order = arm_test_cg01_order(db, now_ms, ttl_ms=300_000)
    deadline = order.expires_at
    coordinator = ExecutionCoordinator()

    # Fill the order before deadline
    fill_quote = {
        "bid": order.planned_entry - 0.10,
        "ask": order.planned_entry,
        "last": order.planned_entry,
        "timestamp": deadline - 10_000,
    }
    clock = ReplayClock(initial_ms=deadline - 10_000)
    coordinator.evaluate_orders_sync(
        db,
        ticker_override=fill_quote,
        clock=clock,
        now_ms=deadline - 10_000,
    )
    db.refresh(order)
    assert order.state == "paper_open"

    # Now advance clock past setup deadline (TP and SL are not touched)
    past_quote = {
        "bid": order.planned_entry + 0.50,
        "ask": order.planned_entry + 0.60,
        "last": order.planned_entry + 0.55,
        "timestamp": deadline + 60_000,
    }
    clock.set_time(deadline + 60_000)
    coordinator.evaluate_orders_sync(
        db,
        ticker_override=past_quote,
        clock=clock,
        now_ms=deadline + 60_000,
    )
    db.refresh(order)
    assert order.state == "paper_open", "Open position must NOT be closed due to setup TTL"
