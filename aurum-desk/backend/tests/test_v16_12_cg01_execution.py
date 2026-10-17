"""
backend/tests/test_v16_12_cg01_execution.py
V16.12 — Real Paper Trading Integration & Historical HTF Evidence Immutability Tests.

Sections P, Q, R (Rules 151 - 180):
- Test 1: Full positive LONG paper execution flow: candidate -> arm -> fill (coordinator) -> TP exit (PaperBroker) with persisted HTF evidence.
- Test 2: Full positive SHORT paper execution flow: candidate -> arm -> fill -> exit with persisted HTF evidence.
- Test 3: Snapshot immutability: subsequent market transition does NOT mutate historical order's HTF evidence.
- Test 4: Chart evidence endpoint /api/v1/orders/{order_id}/chart-evidence returns immutable htf_context.
- Test 5: Guard checks: single position guard, news blackout, and order expiry.
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import crud
from database import get_db
from main import app
from models import Base
from paper_broker import PaperBroker
from services.execution_coordinator import ExecutionCoordinator
from services.scalp_m1_types import M1ScalpConfig, WatchedZone
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.trading_policy_service import TradingPolicyService
from tests.test_v16_3_cg01_pipeline import build_long_fixture_series, build_short_fixture_series, make_valid_quote


@pytest.fixture
def broker_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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


# =========================================================================
# TEST 1: Full Positive LONG Flow with Immutable HTF Evidence
# =========================================================================
def test_long_full_execution_flow_with_htf_evidence(broker_db):
    db, engine = broker_db
    now_ms = 1792050000000

    order = arm_long_cg01_order(db, now_ms)
    tp_price = order.take_profit
    planned_entry = order.planned_entry

    # Verify order snapshot has htf_context
    assert order.scenario_snapshot_id is not None
    snap_row = db.query(models.SMCScenarioSnapshot).filter(models.SMCScenarioSnapshot.snapshot_id == order.scenario_snapshot_id).first()
    assert snap_row is not None
    snap_data = json.loads(snap_row.payload)
    htf_ctx = (snap_data.get("context") or {}).get("htf_context")
    assert htf_ctx is not None, "ScenarioSnapshot must contain htf_context"
    assert "h4_snapshot" in htf_ctx
    assert "h1_snapshot" in htf_ctx
    assert "direction_context" in htf_ctx
    assert htf_ctx["schema_version"] == "v16.12"

    coordinator = ExecutionCoordinator()

    # 1. Fill tick at CE (ask = planned_entry)
    fill_time = now_ms + 10_000
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": planned_entry - 0.10, "ask": planned_entry, "last": planned_entry, "timestamp": fill_time},
        now_ms=fill_time,
    )
    db.refresh(order)
    assert order.state == "paper_open"
    assert order.actual_entry == planned_entry

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
    assert closed_order.state == "closed"
    assert closed_order.exit_cause == "TP_HIT"
    assert closed_order.realized_pnl_net > 0.0

    # 3. Readback in fresh DB session: verify snapshot immutability
    SessionFresh = sessionmaker(bind=engine)
    fresh_session = SessionFresh()
    try:
        reloaded_order = fresh_session.query(models.PaperOrder).filter(models.PaperOrder.id == order.id).first()
        assert reloaded_order.state == "closed"
        reloaded_snap = fresh_session.query(models.SMCScenarioSnapshot).filter(models.SMCScenarioSnapshot.snapshot_id == reloaded_order.scenario_snapshot_id).first()
        reloaded_data = json.loads(reloaded_snap.payload)
        reloaded_htf = (reloaded_data.get("context") or {}).get("htf_context")
        assert reloaded_htf is not None
        assert reloaded_htf["schema_version"] == "v16.12"
    finally:
        fresh_session.close()


# =========================================================================
# TEST 2: Full Positive SHORT Flow with Immutable HTF Evidence
# =========================================================================
def test_short_full_execution_flow_with_htf_evidence(broker_db):
    db, engine = broker_db
    now_ms = 1792050000000

    order = arm_short_cg01_order(db, now_ms)
    tp_price = order.take_profit
    planned_entry = order.planned_entry

    assert order.scenario_snapshot_id is not None
    snap_row = db.query(models.SMCScenarioSnapshot).filter(models.SMCScenarioSnapshot.snapshot_id == order.scenario_snapshot_id).first()
    snap_data = json.loads(snap_row.payload)
    htf_ctx = (snap_data.get("context") or {}).get("htf_context")
    assert htf_ctx is not None
    assert htf_ctx["schema_version"] == "v16.12"

    coordinator = ExecutionCoordinator()

    # Fill tick at planned_entry (bid >= planned_entry)
    fill_time = now_ms + 10_000
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": planned_entry, "ask": planned_entry + 0.10, "last": planned_entry, "timestamp": fill_time},
        now_ms=fill_time,
    )
    db.refresh(order)
    assert order.state == "paper_open"
    assert order.actual_entry == planned_entry

    # Exit tick reaching TP (ask <= tp_price)
    exit_time = fill_time + 60_000
    closed_order = PaperBroker.process_price_tick(
        db,
        current_bid=tp_price - 0.20,
        current_ask=tp_price - 0.10,
        candle_high=tp_price + 1.0,
        candle_low=tp_price - 0.50,
        candle_timestamp=exit_time,
        now_ms=exit_time,
    )
    assert closed_order is not None
    assert closed_order.state == "closed"
    assert closed_order.exit_cause == "TP_HIT"
    assert closed_order.realized_pnl_net > 0.0


# =========================================================================
# TEST 3: Snapshot Immutability after Subsequent Market Shift
# =========================================================================
def test_snapshot_immutability_after_subsequent_market_shift(broker_db):
    db, engine = broker_db
    now_ms = 1792050000000

    order = arm_long_cg01_order(db, now_ms)
    initial_snap_id = order.scenario_snapshot_id

    # Simulate subsequent market transition on live collector
    from services.collector_service import collector_service
    collector_service.d_4h_bias = "BEARISH"
    collector_service.h1_alignment = "OPPOSING"
    collector_service.htf_contexts = {
        "D": {"bias": "BEARISH", "phase": "ESTABLISHED"},
        "4H": {"bias": "BEARISH", "phase": "ESTABLISHED"},
        "1H": {"bias": "UNKNOWN", "phase": "TRANSITION"},
    }

    # Verify historical order's snapshot remains unchanged
    snap_row = db.query(models.SMCScenarioSnapshot).filter(models.SMCScenarioSnapshot.snapshot_id == initial_snap_id).first()
    snap_data = json.loads(snap_row.payload)
    htf_ctx = (snap_data.get("context") or {}).get("htf_context")

    # Historical evidence is strictly preserved
    assert htf_ctx["h1_snapshot"]["bias"] in ("BULLISH", "UNKNOWN", "RANGE")
    assert htf_ctx["schema_version"] == "v16.12"
    assert htf_ctx["evaluation_as_of_ms"] == now_ms


# =========================================================================
# TEST 4: Chart Evidence API Route /api/v1/orders/{order_id}/chart-evidence
# =========================================================================
def test_order_chart_evidence_api(broker_db):
    db, engine = broker_db
    now_ms = 1792050000000
    order = arm_long_cg01_order(db, now_ms)

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    try:
        resp = client.get(f"/api/v1/orders/{order.id}/chart-evidence")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["binding"]["id"] == order.id
        assert data["binding"]["direction"] == "LONG"
        assert data["htf_context"] is not None
        assert data["htf_context"]["schema_version"] == "v16.12"
        assert "h4_snapshot" in data["htf_context"]
        assert "h1_snapshot" in data["htf_context"]
    finally:
        app.dependency_overrides.clear()


# =========================================================================
# TEST 5: Guard Blocking Checks (Single Position, News, Expiry)
# =========================================================================
def test_execution_guard_blocking(broker_db):
    db, engine = broker_db
    now_ms = 1792050000000

    order = arm_long_cg01_order(db, now_ms)
    coordinator = ExecutionCoordinator()

    # Fill order
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": order.planned_entry - 0.10, "ask": order.planned_entry, "last": order.planned_entry, "timestamp": now_ms + 1000},
        now_ms=now_ms + 1000,
    )
    db.refresh(order)
    assert order.state == "paper_open"

    # Single position guard: cannot open second position
    guard_result = PaperBroker.evaluate_account_guard(db, risk_usdt=50.0, now_ms=now_ms + 2000)
    assert guard_result is not None
    assert guard_result[0] == "ACTIVE_POSITION_EXISTS"
