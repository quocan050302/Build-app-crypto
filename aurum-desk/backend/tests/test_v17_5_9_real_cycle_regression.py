"""
backend/tests/test_v17_5_9_real_cycle_regression.py
AURUM V17.5.9 — End-to-End Real Cycle Regression Suite (Section 09 Parts 077-079).

Pipeline:
Market candles -> Detector -> Verified reference & break -> Candidate creation
-> Stop placement -> Financial calculation -> Real ARM guards -> PaperOrder persisted
-> ExecutionCoordinator fill -> Fresh session readback -> Complete financial reconciliation.

Verifies:
1. End-to-end SHORT cycle with verified financial parity and fill execution.
2. End-to-end LONG cycle with verified financial parity and fill execution.
3. Concurrent ARM attempts across separate database sessions prevent duplicates.
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
from services.plan_financial_context_service import assert_order_financial_parity
from services.quote_validator import CanonicalQuote
from services.scalp_m1_types import M1ScalpConfig, ScalpMode, WatchedZone, ZoneState
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from tests.test_v15_1_execution_lifecycle import (
    _make_causal_long_m1_candles,
    _make_causal_short_m1_candles,
    _setup_positive_trading_policy,
)


@pytest.fixture
def real_cycle_db():
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


def test_real_cycle_short_execution_and_financial_reconciliation(real_cycle_db):
    """Full-cycle SHORT: candles -> detector -> verified nodes -> ARM -> fill -> readback -> parity."""
    db, SessionFactory = real_cycle_db
    now_ms = 1_300_000
    clock = FakeClock(now_ms)
    orch = ScalpWatcherOrchestrator()
    coordinator = ExecutionCoordinator()

    _set_config(db, "slippage_usd", "0.20", now_ms)
    _set_config(db, "maker_fee_rate", "0.0002", now_ms)
    _set_config(db, "taker_fee_rate", "0.0004", now_ms)

    zone = WatchedZone(
        zone_id="z_short_pipeline", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    quote = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2647.50, ask=2648.00, last=2647.75,
        status="VALID", exchange_ts_ms=now_ms, received_at_ms=now_ms,
    )

    base_cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
        min_net_rr=1.5,
    )

    # 1. Pipeline evaluation and ARM
    res = orch.evaluate_cycle(
        db, "XAUUSDT",
        now_ms=now_ms, clock=clock, quote=quote,
        candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )
    assert res["status"] == "OK"
    armed_id = res.get("armed_order_id")
    assert armed_id is not None, "Pipeline must autonomously produce an armed order"

    # 2. Fresh session readback
    fresh_db = SessionFactory()
    order = fresh_db.query(models.PaperOrder).filter(models.PaperOrder.id == armed_id).first()
    assert order is not None
    assert order.state == "armed"
    assert order.direction == "SHORT"
    assert order.quantity > 0

    order_cost_snap = json.loads(order.cost_snapshot)
    assert order_cost_snap.get("slippage_usd") == 0.20

    # 3. Simulate market execution fill
    fill_time = now_ms + 10_000
    fill_ticker = {
        "symbol": "XAUUSDT",
        "bid": 2647.50,
        "ask": 2648.00,
        "last": 2647.75,
        "timestamp": fill_time,
    }
    coordinator.evaluate_orders_sync(fresh_db, ticker_override=fill_ticker, clock=FakeClock(fill_time), now_ms=fill_time)

    fresh_db.refresh(order)
    assert order.state in ("open", "paper_open")
    # SHORT fill: Bid - slippage = 2647.50 - 0.20 = 2647.30
    assert abs(order.actual_entry - 2647.30) < 1e-4

    # 4. Financial parity reconciliation
    parity = assert_order_financial_parity(
        order_snapshot=order_cost_snap,
        fill_result={"realized_slippage": 0.20},
    )
    assert parity["is_parity"] is True
    assert parity["status"] == "PASS"
    fresh_db.close()


def test_real_cycle_long_execution_and_financial_reconciliation(real_cycle_db):
    """Full-cycle LONG: candles -> detector -> verified nodes -> ARM -> fill -> readback -> parity."""
    db, SessionFactory = real_cycle_db
    now_ms = 1_300_000
    clock = FakeClock(now_ms)
    orch = ScalpWatcherOrchestrator()
    coordinator = ExecutionCoordinator()

    _set_config(db, "slippage_usd", "0.25", now_ms)
    _set_config(db, "maker_fee_rate", "0.0002", now_ms)
    _set_config(db, "taker_fee_rate", "0.0004", now_ms)

    # Bullish zone
    zone = WatchedZone(
        zone_id="z_long_pipeline", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    quote = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2622.00, ask=2622.50, last=2622.25,
        status="VALID", exchange_ts_ms=now_ms, received_at_ms=now_ms,
    )

    base_cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
        min_net_rr=1.5,
    )

    # 1. Pipeline evaluation and ARM
    res = orch.evaluate_cycle(
        db, "XAUUSDT",
        now_ms=now_ms, clock=clock, quote=quote,
        candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )
    assert res["status"] == "OK"
    armed_id = res.get("armed_order_id")
    assert armed_id is not None, "Pipeline must autonomously produce an armed LONG order"

    # 2. Fresh session readback
    fresh_db = SessionFactory()
    order = fresh_db.query(models.PaperOrder).filter(models.PaperOrder.id == armed_id).first()
    assert order is not None
    assert order.state == "armed"
    assert order.direction == "LONG"
    assert order.quantity > 0

    order_cost_snap = json.loads(order.cost_snapshot)
    assert order_cost_snap.get("slippage_usd") == 0.25

    # 3. Simulate market execution fill (Ask + slippage)
    fill_time = now_ms + 10_000
    fill_ticker = {
        "symbol": "XAUUSDT",
        "bid": 2622.00,
        "ask": 2622.50,
        "last": 2622.25,
        "timestamp": fill_time,
    }
    coordinator.evaluate_orders_sync(fresh_db, ticker_override=fill_ticker, clock=FakeClock(fill_time), now_ms=fill_time)

    fresh_db.refresh(order)
    assert order.state in ("open", "paper_open")
    # LONG fill: Ask + slippage = 2622.50 + 0.25 = 2622.75
    assert abs(order.actual_entry - 2622.75) < 1e-4

    # 4. Financial parity reconciliation
    parity = assert_order_financial_parity(
        order_snapshot=order_cost_snap,
        fill_result={"realized_slippage": 0.25},
    )
    assert parity["is_parity"] is True
    assert parity["status"] == "PASS"
    fresh_db.close()


def test_concurrent_sessions_prevent_duplicate_arm(real_cycle_db):
    """Part 079: Interleaved ARM evaluation using separate DB sessions prevents duplicate order persistence."""
    _, SessionFactory = real_cycle_db
    session_a = SessionFactory()
    session_b = SessionFactory()
    now_ms = 1_300_000
    clock = FakeClock(now_ms)
    orch = ScalpWatcherOrchestrator()

    zone = WatchedZone(
        zone_id="z_concurrent", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    quote = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2647.50, ask=2648.00, last=2647.75,
        status="VALID", exchange_ts_ms=now_ms, received_at_ms=now_ms,
    )

    base_cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
        min_net_rr=1.5,
    )

    # Session A executes cycle
    res_a = orch.evaluate_cycle(
        session_a, "XAUUSDT",
        now_ms=now_ms, clock=clock, quote=quote,
        candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )
    assert res_a.get("armed_order_id") is not None

    # Session B executes cycle for same setup
    res_b = orch.evaluate_cycle(
        session_b, "XAUUSDT",
        now_ms=now_ms + 500, clock=FakeClock(now_ms + 500), quote=quote,
        candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )

    # Verification: total PaperOrder count across all sessions is exactly 1
    session_check = SessionFactory()
    total_orders = session_check.query(models.PaperOrder).count()
    assert total_orders == 1, f"Expected exactly 1 order, found {total_orders}"

    session_a.close()
    session_b.close()
    session_check.close()
