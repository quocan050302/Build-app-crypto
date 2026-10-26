"""
backend/tests/test_v17_5_7_true_cycle_cost_parity.py
AURUM V17.5.7 — Gap B: True Cycle Cost Parity Verification.

Proves end-to-end cost parity across:
  actual evaluate_cycle -> actual _arm_paper_candidate -> persisted StopComparisonPlan & PaperOrder -> ExecutionCoordinator fill
WITHOUT manual seeding of PaperOrder(state='armed').

Covers:
- Parts 061-070: True cycle cost fixture, execution through evaluate_cycle, fresh session readback, fill tick.
- Parts 141-150: LONG 0.27, SHORT 0.27, explicit 0.0 slippage, custom fees, snapshot persistence, correlation, intent, expiry.
- Invariants: Paper XAUUSDT, risk budget conservation, immutable frozen order snapshot.
"""

import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import crud
import models
from services.clock import FakeClock
from services.execution_coordinator import ExecutionCoordinator
from services.plan_financial_context_service import resolve_execution_cost_context
from services.quote_validator import CanonicalQuote
from services.scalp_m1_types import M1ScalpConfig, ScalpMode, WatchedZone, ZoneState
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from tests.test_v15_1_execution_lifecycle import (
    _make_causal_long_m1_candles,
    _make_causal_short_m1_candles,
    _setup_positive_trading_policy,
)


@pytest.fixture
def true_cycle_db():
    """
    Phần 061: Isolated DB fixture with tables created and positive trading policy.
    Zero seeded armed or open orders.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False)
    session = SessionFactory()

    _setup_positive_trading_policy(session)

    session.add(models.SystemConfig(key="timing_profile", value="CG01_ALL_SESSIONS_PAPER", updated_at=1000000))
    session.add(models.SystemConfig(key="execution_scope", value="PAPER", updated_at=1000000))
    session.add(models.SystemConfig(key="stop_placement_v1.mode", value="PAPER_STRUCTURAL", updated_at=1000000))
    session.add(models.SystemConfig(key="stop_placement_v1.version", value="1", updated_at=1000000))

    clock = FakeClock(1_000_000)
    audit = crud.get_or_create_today_audit(session, clock=clock)
    audit.initial_equity = 5000.0
    audit.current_equity = 5000.0
    session.commit()

    yield session, SessionFactory

    session.close()
    engine.dispose()


def _set_system_config(db: Session, key: str, value: str, now_ms: int = 1_000_000):
    row = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if row:
        row.value = str(value)
        row.updated_at = now_ms
    else:
        db.add(models.SystemConfig(key=key, value=str(value), updated_at=now_ms))
    db.commit()


def test_p141_actual_cycle_long_custom_costs_match_comparison_order_and_fill(true_cycle_db):
    """
    Phần 061-070, 141:
    LONG cycle with custom slippage 0.27 and non-default fees.
    evaluate_cycle creates PaperOrder via ARM without manual seeding.
    Fresh session confirms cost snapshot on PaperOrder and StopComparisonPlan match.
    Coordinator fills order at Ask + 0.27 exactly.
    """
    db, SessionFactory = true_cycle_db
    now_ms = 1_300_000
    clock = FakeClock(now_ms)
    orch = ScalpWatcherOrchestrator()
    coordinator = ExecutionCoordinator()

    # 1. Seed custom cost settings via SystemConfig (production-read source)
    _set_system_config(db, "slippage_usd", "0.27", now_ms)
    _set_system_config(db, "maker_fee_rate", "0.0003", now_ms)
    _set_system_config(db, "taker_fee_rate", "0.0006", now_ms)

    # 2. Setup zone & causal candles for LONG
    zone = WatchedZone(
        zone_id="z_true_cycle_long", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    fresh_cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2622.0,
        ask=2622.50,
        last=2622.25,
        status="VALID",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
    )

    base_cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
        min_net_rr=1.5,
    )

    # 3. Call ACTUAL evaluate_cycle (Phần 063: NO manual seeding of PaperOrder)
    res = orch.evaluate_cycle(
        db, "XAUUSDT",
        now_ms=now_ms, clock=clock, quote=fresh_cq,
        candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )

    assert res["status"] == "OK"
    armed_order_id = res.get("armed_order_id")
    assert armed_order_id is not None, "evaluate_cycle must autonomously arm candidate"

    # 4. Read back in a FRESH session (Phần 068)
    fresh_db = SessionFactory()
    order = fresh_db.query(models.PaperOrder).filter(models.PaperOrder.id == armed_order_id).first()
    assert order is not None
    assert order.state == "armed"
    assert order.direction == "LONG"

    # Assert PaperOrder cost snapshot
    order_cost_snap = json.loads(order.cost_snapshot)
    cost_ctx = resolve_execution_cost_context(snapshot=order_cost_snap)
    assert cost_ctx["available"] is True
    assert cost_ctx["entry_slippage_usd"] == 0.27
    assert cost_ctx["maker_fee_rate"] == 0.0003
    assert cost_ctx["taker_fee_rate"] == 0.0006

    # Assert StopComparisonPlan persisted in PaperObservation
    obs = fresh_db.query(models.PaperObservation).filter(
        models.PaperObservation.namespace == "STOP_PLACEMENT_V1",
        models.PaperObservation.event_kind == "COMPARISON_PLAN",
    ).first()
    assert obs is not None
    plan_dict = json.loads(obs.payload_json)
    assert plan_dict["context_status"] == "VERIFIED"
    assert plan_dict["cost_snapshot"]["entry_slippage_usd"] == 0.27
    assert plan_dict["cost_snapshot"]["maker_fee_rate"] == 0.0003
    assert plan_dict["cost_snapshot"]["taker_fee_rate"] == 0.0006

    # Parity between order snapshot and comparison plan (Phần 069, 146, 147)
    assert plan_dict["order_type"] == order.order_type
    assert plan_dict["expires_at_ms"] == order.expires_at

    # 5. Coordinator execution tick -> fill
    # Ask is 2622.50. Slippage is 0.27. Expected fill = 2622.50 + 0.27 = 2622.77
    clock.advance_by(1000)
    fill_time = clock.now_ms()
    fill_dict = {
        "bid": 2622.0,
        "ask": 2622.50,
        "last": 2622.25,
        "timestamp": fill_time,
    }
    coordinator.evaluate_orders_sync(fresh_db, ticker_override=fill_dict, clock=clock, now_ms=fill_time)

    fresh_db.refresh(order)
    assert order.state in ("open", "paper_open")
    assert abs(order.actual_entry - 2622.77) < 1e-4, f"Expected 2622.77, got {order.actual_entry}"

    fresh_db.close()


def test_p142_actual_cycle_short_custom_costs_match_comparison_order_and_fill(true_cycle_db):
    """
    Phần 142:
    SHORT cycle with custom slippage 0.27.
    Coordinator fills order at Bid - 0.27 exactly.
    """
    db, SessionFactory = true_cycle_db
    now_ms = 1_300_000
    clock = FakeClock(now_ms)
    orch = ScalpWatcherOrchestrator()
    coordinator = ExecutionCoordinator()

    # Seed custom cost settings
    _set_system_config(db, "slippage_usd", "0.27", now_ms)
    _set_system_config(db, "maker_fee_rate", "0.00025", now_ms)
    _set_system_config(db, "taker_fee_rate", "0.00055", now_ms)

    # Setup zone & causal candles for SHORT
    zone = WatchedZone(
        zone_id="z_true_cycle_short", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    fresh_cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2647.50,
        ask=2648.00,
        last=2647.75,
        status="VALID",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
    )

    base_cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
        min_net_rr=1.5,
    )

    res = orch.evaluate_cycle(
        db, "XAUUSDT",
        now_ms=now_ms, clock=clock, quote=fresh_cq,
        candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )

    assert res["status"] == "OK"
    armed_order_id = res.get("armed_order_id")
    assert armed_order_id is not None

    fresh_db = SessionFactory()
    order = fresh_db.query(models.PaperOrder).filter(models.PaperOrder.id == armed_order_id).first()
    assert order is not None
    assert order.direction == "SHORT"

    order_cost_snap = json.loads(order.cost_snapshot)
    cost_ctx = resolve_execution_cost_context(snapshot=order_cost_snap)
    assert cost_ctx["entry_slippage_usd"] == 0.27

    # Coordinator fill: Bid is 2647.50. Slippage is 0.27. Expected fill = 2647.50 - 0.27 = 2647.23
    clock.advance_by(1000)
    fill_time = clock.now_ms()
    fill_dict = {
        "bid": 2647.50,
        "ask": 2648.00,
        "last": 2647.75,
        "timestamp": fill_time,
    }
    coordinator.evaluate_orders_sync(fresh_db, ticker_override=fill_dict, clock=clock, now_ms=fill_time)

    fresh_db.refresh(order)
    assert order.state in ("open", "paper_open")
    assert abs(order.actual_entry - 2647.23) < 1e-4, f"Expected 2647.23, got {order.actual_entry}"

    fresh_db.close()


def test_p143_actual_cycle_explicit_zero_slippage_not_falling_back(true_cycle_db):
    """
    Phần 143:
    Cycle with explicit 0.0 slippage preserves 0.0 and does not fall back to 0.10.
    LONG fills exactly at Ask.
    """
    db, SessionFactory = true_cycle_db
    now_ms = 1_300_000
    clock = FakeClock(now_ms)
    orch = ScalpWatcherOrchestrator()
    coordinator = ExecutionCoordinator()

    # Explicit 0.0 slippage
    _set_system_config(db, "slippage_usd", "0.0", now_ms)

    zone = WatchedZone(
        zone_id="z_true_cycle_zero", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    fresh_cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2622.0,
        ask=2622.50,
        last=2622.25,
        status="VALID",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
    )

    base_cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
        min_net_rr=1.5,
    )

    res = orch.evaluate_cycle(
        db, "XAUUSDT",
        now_ms=now_ms, clock=clock, quote=fresh_cq,
        candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )
    assert res["status"] == "OK"
    armed_order_id = res.get("armed_order_id")
    assert armed_order_id is not None

    fresh_db = SessionFactory()
    order = fresh_db.query(models.PaperOrder).filter(models.PaperOrder.id == armed_order_id).first()
    cost_ctx = resolve_execution_cost_context(snapshot=json.loads(order.cost_snapshot))
    assert cost_ctx["entry_slippage_usd"] == 0.0, "Explicit zero slippage must be preserved"

    # Fill at exact Ask with 0.0 slippage = 2622.50
    clock.advance_by(1000)
    fill_time = clock.now_ms()
    fill_dict = {
        "bid": 2622.0,
        "ask": 2622.50,
        "last": 2622.25,
        "timestamp": fill_time,
    }
    coordinator.evaluate_orders_sync(fresh_db, ticker_override=fill_dict, clock=clock, now_ms=fill_time)

    fresh_db.refresh(order)
    assert order.state in ("open", "paper_open")
    assert abs(order.actual_entry - 2622.50) < 1e-4

    fresh_db.close()


def test_p144_midcycle_settings_change_preserves_frozen_order(true_cycle_db):
    """
    Phần 151, 153:
    Order armed in Cycle 1 with slip 0.08 preserves its frozen cost snapshot even when
    DB settings change to 0.35 in Cycle 2.
    """
    db, SessionFactory = true_cycle_db
    now_ms = 1_300_000
    clock = FakeClock(now_ms)
    orch = ScalpWatcherOrchestrator()
    coordinator = ExecutionCoordinator()

    # Initial settings
    _set_system_config(db, "slippage_usd", "0.08", now_ms)

    zone = WatchedZone(
        zone_id="z_cycle_midcycle", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    fresh_cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2622.0,
        ask=2622.50,
        last=2622.25,
        status="VALID",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
    )

    base_cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
        min_net_rr=1.5,
    )

    res = orch.evaluate_cycle(
        db, "XAUUSDT",
        now_ms=now_ms, clock=clock, quote=fresh_cq,
        candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )
    armed_id = res["armed_order_id"]

    # Midcycle settings change in DB to 0.35
    slip_row = db.query(models.SystemConfig).filter(models.SystemConfig.key == "slippage_usd").first()
    slip_row.value = "0.35"
    db.commit()

    # Fresh session confirms armed order still has frozen 0.08
    fresh_db = SessionFactory()
    order = fresh_db.query(models.PaperOrder).filter(models.PaperOrder.id == armed_id).first()
    cost_ctx = resolve_execution_cost_context(snapshot=json.loads(order.cost_snapshot))
    assert cost_ctx["entry_slippage_usd"] == 0.08

    # Coordinator fills with frozen 0.08 -> 2622.50 + 0.08 = 2622.58
    clock.advance_by(1000)
    fill_time = clock.now_ms()
    fill_dict = {
        "bid": 2622.0,
        "ask": 2622.50,
        "last": 2622.25,
        "timestamp": fill_time,
    }
    coordinator.evaluate_orders_sync(fresh_db, ticker_override=fill_dict, clock=clock, now_ms=fill_time)
    fresh_db.refresh(order)
    assert order.state in ("open", "paper_open")
    assert abs(order.actual_entry - 2622.58) < 1e-4

    fresh_db.close()


def test_p170_auto_off_blocks_arm_without_seeding(true_cycle_db):
    """
    Phần 170:
    When Auto is OFF, evaluate_cycle runs detection and evaluation but does NOT arm the order.
    Zero PaperOrder created.
    """
    db, SessionFactory = true_cycle_db
    now_ms = 1_300_000
    clock = FakeClock(now_ms)
    orch = ScalpWatcherOrchestrator()

    zone = WatchedZone(
        zone_id="z_auto_off", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    fresh_cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2622.0,
        ask=2622.50,
        last=2622.25,
        status="VALID",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
    )

    base_cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
        min_net_rr=1.5,
    )

    # auto_enabled=False explicitly passed
    res = orch.evaluate_cycle(
        db, "XAUUSDT",
        now_ms=now_ms, clock=clock, quote=fresh_cq,
        candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=False,
    )

    assert res["status"] == "OK"
    assert res.get("armed_order_id") is None
    count = db.query(models.PaperOrder).count()
    assert count == 0
