"""
backend/tests/test_v17_5_8_full_cycle.py
AURUM V17.5.8 — Full Cycle ARM, Execution Safety & Parity Suite (Parts 051–060, 087–089).

Audits and proves:
1. Full end-to-end cycle from market input -> detector -> stop derivation -> ARM -> fill:
   - evaluate_cycle autonomously arms candidate into PaperOrder (state="armed")
   - ExecutionCoordinator consumes frozen cost snapshot and fills order at Bid - slippage (SHORT)
   - assert_order_financial_parity confirms true cost parity
2. Negative ARM paths:
   - Expired trigger rejected with TRIGGER_EXPIRED, zero orders created
   - Inverted geometry rejected with INVALID_PRICE_GEOMETRY
   - Stale quote rejected, zero orders created
3. Idempotency: repeated evaluate_cycle calls for same setup do NOT create duplicate orders.
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
from services.plan_financial_context_service import (
    resolve_execution_cost_context,
    assert_order_financial_parity,
)
from services.quote_validator import CanonicalQuote
from services.scalp_m1_types import M1ScalpConfig, ScalpMode, WatchedZone, ZoneState
from services.scalp_watcher_orchestrator import (
    ScalpWatcherOrchestrator,
    validate_arm_invariants,
)
from tests.test_v15_1_execution_lifecycle import (
    _make_causal_short_m1_candles,
    _setup_positive_trading_policy,
)


@pytest.fixture
def isolated_full_cycle_db():
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


def _set_config(db: Session, key: str, value: str, now_ms: int = 1_000_000):
    row = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if row:
        row.value = str(value)
        row.updated_at = now_ms
    else:
        db.add(models.SystemConfig(key=key, value=str(value), updated_at=now_ms))
    db.commit()


def test_p087_full_cycle_short_execution_with_verified_parity(isolated_full_cycle_db):
    """Part 087: Autonomous evaluate_cycle -> ARM -> ExecutionCoordinator fill for SHORT with 0.20 slippage."""
    db, SessionFactory = isolated_full_cycle_db
    now_ms = 1_300_000
    clock = FakeClock(now_ms)
    orch = ScalpWatcherOrchestrator()
    coordinator = ExecutionCoordinator()

    _set_config(db, "slippage_usd", "0.20", now_ms)
    _set_config(db, "maker_fee_rate", "0.0002", now_ms)
    _set_config(db, "taker_fee_rate", "0.0004", now_ms)

    zone = WatchedZone(
        zone_id="z_fc_short", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
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
    assert armed_order_id is not None, "evaluate_cycle must autonomously arm candidate"

    # Fresh session readback
    fresh_db = SessionFactory()
    order = fresh_db.query(models.PaperOrder).filter(models.PaperOrder.id == armed_order_id).first()
    assert order is not None
    assert order.state == "armed"
    assert order.direction == "SHORT"

    # Verify order financial parity
    order_cost_snap = json.loads(order.cost_snapshot)
    cost_ctx = resolve_execution_cost_context(snapshot=order_cost_snap)
    assert cost_ctx["available"] is True
    assert cost_ctx["entry_slippage_usd"] == 0.20

    # Coordinator fills order
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
    # SHORT fill price = Bid - slippage = 2647.50 - 0.20 = 2647.30
    assert abs(order.actual_entry - 2647.30) < 1e-4

    # Financial parity reconciliation
    parity_check = assert_order_financial_parity(
        order_snapshot=order_cost_snap,
        fill_result={"realized_slippage": 0.20},
    )
    assert parity_check["is_parity"] is True
    fresh_db.close()


def test_p088_negative_arm_trigger_expired():
    """Part 088: validate_arm_invariants rejects candidate with TRIGGER_EXPIRED when now_ms >= expires_at_ms."""
    class DummyCalc:
        is_valid = True
        can_execute = True
        quantity = 0.5
        stop_loss = 2660.0

    class DummyCandidate:
        is_valid = True
        direction = "SHORT"
        planned_entry = 2647.0
        take_profit = 2635.0
        stop_loss = 2660.0

    class DummyQuote:
        is_valid = True
        is_stale = False

    valid, reason = validate_arm_invariants(
        candidate=DummyCandidate(),
        calculation=DummyCalc(),
        quote=DummyQuote(),
        now_ms=10500,
        expires_at_ms=10000,  # expired 500ms ago
    )
    assert valid is False
    assert reason == "TRIGGER_EXPIRED"


def test_p088_negative_arm_invalid_geometry():
    """Part 088: Inverted geometry (SL < Entry for SHORT) is rejected with INVALID_PRICE_GEOMETRY."""
    class DummyCalc:
        is_valid = True
        can_execute = True
        quantity = 0.5
        stop_loss = 2640.0

    class InvertedShortCandidate:
        is_valid = True
        direction = "SHORT"
        planned_entry = 2647.0
        take_profit = 2635.0
        stop_loss = 2640.0  # SL below entry is invalid for SHORT!

    class DummyQuote:
        is_valid = True
        is_stale = False

    valid, reason = validate_arm_invariants(
        candidate=InvertedShortCandidate(),
        calculation=DummyCalc(),
        quote=DummyQuote(),
        now_ms=10000,
        expires_at_ms=20000,
    )
    assert valid is False
    assert reason == "INVALID_PRICE_GEOMETRY"


def test_p088_negative_arm_stale_quote(isolated_full_cycle_db):
    """Part 088: Stale quote rejected by evaluate_cycle, zero orders created."""
    db, SessionFactory = isolated_full_cycle_db
    now_ms = 1_300_000
    clock = FakeClock(now_ms)
    orch = ScalpWatcherOrchestrator()

    zone = WatchedZone(
        zone_id="z_stale", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    # Quote from 10 minutes ago
    stale_quote = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2647.50,
        ask=2648.00,
        last=2647.75,
        status="VALID",
        exchange_ts_ms=now_ms - 600_000,
        received_at_ms=now_ms - 600_000,
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
        now_ms=now_ms, clock=clock, quote=stale_quote,
        candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )

    # Must not arm
    assert res.get("armed_order_id") is None
    orders = db.query(models.PaperOrder).all()
    assert len(orders) == 0


def test_p089_arm_idempotency_prevents_duplicate_orders(isolated_full_cycle_db):
    """Part 089: Repeated evaluate_cycle calls for same setup do not create duplicate orders."""
    db, SessionFactory = isolated_full_cycle_db
    now_ms = 1_300_000
    clock = FakeClock(now_ms)
    orch = ScalpWatcherOrchestrator()

    zone = WatchedZone(
        zone_id="z_idem", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
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

    # First cycle
    res1 = orch.evaluate_cycle(
        db, "XAUUSDT",
        now_ms=now_ms, clock=clock, quote=fresh_cq,
        candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )
    assert res1.get("armed_order_id") is not None
    first_order_id = res1["armed_order_id"]

    # Second cycle with same candles
    res2 = orch.evaluate_cycle(
        db, "XAUUSDT",
        now_ms=now_ms + 1000, clock=FakeClock(now_ms + 1000), quote=fresh_cq,
        candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )

    # Database must still have only 1 PaperOrder
    all_orders = db.query(models.PaperOrder).all()
    assert len(all_orders) == 1
    assert all_orders[0].id == first_order_id
