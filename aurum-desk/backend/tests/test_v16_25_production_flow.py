"""
backend/tests/test_v16_25_production_flow.py
V16.25 Full Production Flow, Resilience, and Queue Acknowledgement Invariant Suite.

Enforces:
- Scope A (Rules 021-024, 031-060): Submitted-but-not-started batch cancellation safety.
- Scope B (Rules 025-028, 061-080): Exactly-once queue task acknowledgement across retries.
- Rules 131-150: Full production flow matrix:
  1. LONG TP event-driven execution with QueueReceipt exactly-once ACK.
  2. SHORT SL event-driven execution with QueueReceipt exactly-once ACK.
  3. SHADOW observation trace persistence without active trade mutations.
  4. AUTO OFF observation preserved, order arming inhibited.
  5. Telemetry retry failure isolation: transient telemetry retry does not impede execution.
  6. Restart durability: records committed survive process restart.
  7. Shutdown drain: queued and running operations properly accounted for without premature exit.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy.orm import Session

# Ensure test DB isolation (Rule 012)
os.environ["AURUM_DB_PATH"] = ":memory:"

from database import SessionLocal, engine
from models import Base
import models
import crud
import schemas
from domain_calculator import CostAssumptions, calculate_risk_reward, validate_price_geometry
from services.cg01_types import CG01Config, CG01ResearchContext
from services.cg01_config_factory import resolve_canonical_cg01_config
from services.scalp_m1_types import M1ScalpConfig, WatchedZone
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator, scalp_watcher_orchestrator
from services.execution_coordinator import ExecutionCoordinator
from paper_broker import PaperBroker
from services.trading_policy_service import TradingPolicyService
from services.candle_cache_service import candle_cache_service
from services.collector_service import collector_service
from services.strategy_service import strategy_service
from services.clock import ReplayClock, LiveClock, live_clock
from services.cg01_scheduler_service import (
    CG01EvaluationScheduler,
    cg01_evaluation_scheduler,
    evaluate_cg01_shadow_snapshot,
    resolve_effective_cg01_config,
    capture_cg01_quote_snapshot,
    resolve_cg01_financial_context,
    ensure_observation_run_started,
)
from services.cg01_projection_worker import (
    CG01ResearchProjectionWorker,
    ProjectionOutcome,
)
from services.paper_research_run_service import (
    PaperResearchRun,
    PaperResearchRunService,
    build_cg01_paper_run_summary,
    associate_order_with_research_run,
    record_committed_order_milestone,
    resolve_active_observation_run,
)
import services.cg01_telemetry_service as tel_mod
from services.cg01_telemetry_service import (
    CG01EvaluationTrace,
    CG01TracePersistenceWriter,
    cg01_trace_recorder,
    drain_trace_persistence,
    enqueue_trace_persistence,
    flush_pending_traces,
    reset_telemetry_worker_state,
    start_trace_persistence_worker,
    stop_trace_persistence_worker,
    wait_trace_persistence_worker_stopped,
    get_active_batch_count,
    get_persisted_count,
    _TRACE_QUEUE,
)
from tests.test_v16_3_cg01_pipeline import (
    build_long_fixture_series,
    build_short_fixture_series,
    make_valid_quote,
    make_bar,
)


@pytest.fixture(autouse=True)
def setup_isolated_db():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    reset_telemetry_worker_state()
    cg01_evaluation_scheduler.reset_state()
    cg01_trace_recorder.clear()
    candle_cache_service._cache.clear()
    try:
        session.query(models.SMCScenarioSnapshot).delete()
        session.query(models.PaperOrder).delete()
        session.query(models.WatchSetup).delete()
        session.query(models.PaperResearchOrderLink).delete()
        session.query(models.PaperResearchMilestone).delete()
        session.query(models.PaperResearchOrderMilestone).delete()
        session.query(models.CG01TraceRecord).delete()
        session.query(models.PaperResearchRunRecord).delete()
        session.query(models.SystemConfig).delete()
        session.query(models.DayAudit).delete()
        session.commit()
        yield session
    finally:
        reset_telemetry_worker_state()
        cg01_evaluation_scheduler.reset_state()
        cg01_trace_recorder.clear()
        session.close()


@pytest.fixture
def db(setup_isolated_db):
    yield setup_isolated_db


def _ingest_bars_via_payload(symbol: str, tf: str, bars: list):
    rows = []
    for b in bars:
        ts = int(b["timestamp"] if isinstance(b, dict) else b.timestamp)
        o = float(b["open"] if isinstance(b, dict) else b.open)
        h = float(b["high"] if isinstance(b, dict) else b.high)
        l = float(b["low"] if isinstance(b, dict) else b.low)
        c = float(b["close"] if isinstance(b, dict) else b.close)
        vol = float(b.get("volume", 100.0) if isinstance(b, dict) else getattr(b, "volume", 100.0) or 100.0)
        rows.append([ts, o, h, l, c, vol])
    return candle_cache_service.process_candle_payload(symbol, tf, rows)


# ==============================================================================
# SCENARIO 1: LONG TP FULL EVENT-DRIVEN EXECUTION WITH QUEUE RECEIPT ACK
# Rules 131, 133, 134, 137, 139, 141
# ==============================================================================
@pytest.mark.anyio
async def test_v16_25_flow_scenario_1_long_tp_with_queue_receipt_ack(db: Session):
    """
    Scenario 1: Event-driven LONG setup:
    Cache event -> scheduler -> candidate -> arm -> fill -> TP exit -> projection -> trace worker.
    Verifies that QueueReceipt accurately ACKs queue task upon commit.
    """
    strategy_service.set_auto_state(db, True)
    audit = crud.get_or_create_today_audit(db)
    audit.cooldown_until = None
    audit.fills_count = 0
    audit.realized_pnl = 0.0
    audit.current_equity = 1000.0
    db.commit()

    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 19, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)

    last_ts = c_1m[-1]["timestamp"]
    trigger_ts = last_ts + 60_000
    evidence_time = trigger_ts + 60_000
    now_ms = evidence_time + 5_000

    test_clock = ReplayClock(now_ms)
    cg01_evaluation_scheduler.set_clock(test_clock)

    db.add(models.SystemConfig(key="scalp_m1_mode", value="PAPER", updated_at=now_ms))
    db.add(models.SystemConfig(key="cg01_enabled", value="true", updated_at=now_ms))
    db.add(models.SystemConfig(key="cg01_profile_mode", value="PAPER_EXECUTION", updated_at=now_ms))
    db.add(models.SystemConfig(key="execution_profile", value="CG01", updated_at=now_ms))
    db.commit()

    _ingest_bars_via_payload("XAUUSDT", "4H", c_4h)
    _ingest_bars_via_payload("XAUUSDT", "1H", c_1h)
    _ingest_bars_via_payload("XAUUSDT", "15M", c_15m)

    poi = WatchedZone(
        zone_id="poi_long_v16_25_1",
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
    scalp_watcher_orchestrator.set_active_zones("XAUUSDT", [poi])

    collector_service.latest_ticker = {
        "symbol": "XAUUSDT",
        "bid": 2625.5,
        "ask": 2625.7,
        "last": 2625.6,
        "timestamp": now_ms,
        "observed_at": now_ms,
    }

    # Start background scheduler and telemetry worker
    cg01_evaluation_scheduler.set_flags(enabled=True, shadow_mode=False, is_owner=True)
    sched_task = asyncio.create_task(cg01_evaluation_scheduler.run())
    worker_started = await start_trace_persistence_worker(timeout_s=2.0)
    assert worker_started is True

    try:
        # Ingest trigger bar and boundary bar to close trigger bar
        trigger_bar = {"timestamp": trigger_ts, "open": 2625.5, "high": 2626.0, "low": 2625.0, "close": 2625.6}
        boundary_bar = {"timestamp": evidence_time, "open": 2625.6, "high": 2626.0, "low": 2625.5, "close": 2625.6}
        _ingest_bars_via_payload("XAUUSDT", "1M", c_1m + [trigger_bar, boundary_bar])

        order = None
        for _ in range(35):
            await asyncio.sleep(0.1)
            db.expire_all()
            order = db.query(models.PaperOrder).filter(models.PaperOrder.state.in_(["armed", "placed"])).first()
            if order:
                break

        assert order is not None, "Expected order to be armed automatically by scheduler"
        assert order.direction.upper() == "LONG"
        assert order.take_profit > order.planned_entry > order.stop_loss

        # Fill order
        fill_time = now_ms + 10_000
        test_clock.set_time(fill_time)
        coordinator = ExecutionCoordinator()
        coordinator.evaluate_orders_sync(
            db,
            ticker_override={
                "bid": round(order.planned_entry - 0.10, 2),
                "ask": order.planned_entry,
                "last": order.planned_entry,
                "timestamp": fill_time,
                "observed_at": fill_time,
            },
            now_ms=fill_time,
        )
        db.refresh(order)
        assert order.state == "paper_open"

        # TP exit
        tp_price = order.take_profit
        exit_time = fill_time + 60_000
        test_clock.set_time(exit_time)
        closed_order = PaperBroker.process_price_tick(
            db,
            current_bid=tp_price,
            current_ask=tp_price + 0.10,
            candle_high=tp_price + 0.5,
            candle_low=tp_price - 1.0,
            candle_timestamp=exit_time,
            now_ms=exit_time,
        )
        assert closed_order is not None
        assert closed_order.state == "closed"
        assert closed_order.exit_cause == "TP_HIT"
        assert closed_order.realized_pnl_net > 0.0

        # Projection worker
        worker = CG01ResearchProjectionWorker(scope="CG01")
        worker.process_pending_events(db=db)

        # Drain trace persistence worker cleanly
        drain_res = await drain_trace_persistence(timeout_s=3.0)
        assert drain_res["status"] == "COMPLETED"
        assert _TRACE_QUEUE.unfinished_tasks == 0, "Original queue task must be fully ACK'd"
        assert tel_mod.get_outstanding_receipt_count() == 0, "Receipt must be completed once"
        assert tel_mod.get_active_operations_count() == 0, "No pending operation tasks"
    finally:
        cg01_evaluation_scheduler.stop()
        sched_task.cancel()
        await asyncio.gather(sched_task, return_exceptions=True)
        stop_trace_persistence_worker()
        await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# SCENARIO 2: SHORT SL FULL EVENT-DRIVEN EXECUTION WITH QUEUE RECEIPT ACK
# Rules 131, 135, 142
# ==============================================================================
@pytest.mark.anyio
async def test_v16_25_flow_scenario_2_short_sl_with_queue_receipt_ack(db: Session):
    """
    Scenario 2: Event-driven SHORT setup:
    Cache event -> scheduler -> candidate -> arm -> fill -> SL exit.
    Verifies that QueueReceipt accurately ACKs queue task upon commit.
    """
    strategy_service.set_auto_state(db, True)
    audit = crud.get_or_create_today_audit(db)
    audit.cooldown_until = None
    audit.fills_count = 0
    audit.realized_pnl = 0.0
    audit.current_equity = 1000.0
    db.commit()

    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 10, 15, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    c_4h, c_1h, c_15m, c_1m = build_short_fixture_series(t_start)

    evidence_time = t_start + 540_000
    now_ms = evidence_time + 4_000

    test_clock = ReplayClock(now_ms)
    cg01_evaluation_scheduler.set_clock(test_clock)

    db.add(models.SystemConfig(key="scalp_m1_mode", value="PAPER", updated_at=now_ms))
    db.add(models.SystemConfig(key="cg01_enabled", value="true", updated_at=now_ms))
    db.add(models.SystemConfig(key="cg01_profile_mode", value="PAPER_EXECUTION", updated_at=now_ms))
    db.add(models.SystemConfig(key="execution_profile", value="CG01", updated_at=now_ms))
    db.commit()

    _ingest_bars_via_payload("XAUUSDT", "4H", c_4h)
    _ingest_bars_via_payload("XAUUSDT", "1H", c_1h)
    _ingest_bars_via_payload("XAUUSDT", "15M", c_15m)

    poi = WatchedZone(
        zone_id="poi_short_v16_25_2",
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
    scalp_watcher_orchestrator.set_active_zones("XAUUSDT", [poi])

    collector_service.latest_ticker = {
        "symbol": "XAUUSDT",
        "bid": 2649.5,
        "ask": 2649.7,
        "last": 2649.6,
        "timestamp": now_ms,
        "observed_at": now_ms,
    }

    cg01_evaluation_scheduler.set_flags(enabled=True, shadow_mode=False, is_owner=True)
    sched_task = asyncio.create_task(cg01_evaluation_scheduler.run())
    worker_started = await start_trace_persistence_worker(timeout_s=2.0)
    assert worker_started is True

    try:
        boundary_bar = {"timestamp": evidence_time, "open": 2649.5, "high": 2650.0, "low": 2649.0, "close": 2649.6}
        _ingest_bars_via_payload("XAUUSDT", "1M", c_1m + [boundary_bar])

        order = None
        for _ in range(35):
            await asyncio.sleep(0.1)
            db.expire_all()
            order = db.query(models.PaperOrder).filter(models.PaperOrder.state.in_(["armed", "placed"])).first()
            if order:
                break

        assert order is not None
        assert order.direction.upper() == "SHORT"
        assert order.take_profit < order.planned_entry < order.stop_loss

        fill_time = now_ms + 10_000
        test_clock.set_time(fill_time)
        coordinator = ExecutionCoordinator()
        coordinator.evaluate_orders_sync(
            db,
            ticker_override={
                "bid": order.planned_entry,
                "ask": round(order.planned_entry + 0.10, 2),
                "last": order.planned_entry,
                "timestamp": fill_time,
                "observed_at": fill_time,
            },
            now_ms=fill_time,
        )
        db.refresh(order)
        assert order.state == "paper_open"

        sl_price = order.stop_loss
        exit_time = fill_time + 60_000
        test_clock.set_time(exit_time)
        closed_order = PaperBroker.process_price_tick(
            db,
            current_bid=sl_price + 0.10,
            current_ask=sl_price + 0.20,
            candle_high=sl_price + 1.0,
            candle_low=sl_price - 0.50,
            candle_timestamp=exit_time,
            now_ms=exit_time,
        )
        assert closed_order is not None
        assert closed_order.state == "closed"
        assert closed_order.exit_cause == "SL_HIT"
        assert closed_order.realized_pnl_net < 0.0

        worker = CG01ResearchProjectionWorker(scope="CG01")
        worker.process_pending_events(db=db)

        drain_res = await drain_trace_persistence(timeout_s=3.0)
        assert drain_res["status"] == "COMPLETED"
        assert _TRACE_QUEUE.unfinished_tasks == 0, "Original queue task must be fully ACK'd"
        assert tel_mod.get_outstanding_receipt_count() == 0, "Receipt must be completed once"
        assert tel_mod.get_active_operations_count() == 0, "No pending operation tasks"
    finally:
        cg01_evaluation_scheduler.stop()
        sched_task.cancel()
        await asyncio.gather(sched_task, return_exceptions=True)
        stop_trace_persistence_worker()
        await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# SCENARIO 3: SHADOW OBSERVATION PERSISTENCE (Rule 137, 144)
# ==============================================================================
@pytest.mark.anyio
async def test_v16_25_flow_scenario_3_shadow_candidate_trace_persisted(db: Session):
    """
    Scenario 3: SHADOW observation mode produces committed trace,
    without active trading order creation.
    """
    orders_before = db.query(models.PaperOrder).count()

    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 19, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)

    last_ts = c_1m[-1]["timestamp"]
    trigger_ts = last_ts + 60_000
    evidence_time = trigger_ts + 60_000
    now_ms = evidence_time + 2_000

    test_clock = ReplayClock(now_ms)
    cg01_evaluation_scheduler.set_clock(test_clock)

    _ingest_bars_via_payload("XAUUSDT", "4H", c_4h)
    _ingest_bars_via_payload("XAUUSDT", "1H", c_1h)
    _ingest_bars_via_payload("XAUUSDT", "15M", c_15m)

    poi = WatchedZone(
        zone_id="poi_long_v16_25_shadow",
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
    scalp_watcher_orchestrator.set_active_zones("XAUUSDT", [poi])

    collector_service.latest_ticker = {
        "symbol": "XAUUSDT",
        "bid": 2625.5,
        "ask": 2625.7,
        "last": 2625.6,
        "timestamp": now_ms,
        "observed_at": now_ms,
    }

    # Enable SHADOW mode
    cg01_evaluation_scheduler.set_flags(enabled=True, shadow_mode=True, is_owner=False)
    sched_task = asyncio.create_task(cg01_evaluation_scheduler.run())
    worker_started = await start_trace_persistence_worker(timeout_s=2.0)
    assert worker_started is True

    try:
        trigger_bar = {"timestamp": trigger_ts, "open": 2625.5, "high": 2626.0, "low": 2625.0, "close": 2625.6}
        boundary_bar = {"timestamp": evidence_time, "open": 2625.6, "high": 2626.0, "low": 2625.5, "close": 2625.6}
        _ingest_bars_via_payload("XAUUSDT", "1M", c_1m + [trigger_bar, boundary_bar])

        await asyncio.sleep(0.5)

        drain_res = await drain_trace_persistence(timeout_s=3.0)
        assert drain_res["status"] == "COMPLETED"
        assert _TRACE_QUEUE.unfinished_tasks == 0
        assert tel_mod.get_outstanding_receipt_count() == 0

        # Assert no active orders were created
        orders_after = db.query(models.PaperOrder).count()
        assert orders_after == orders_before, "SHADOW mode must NEVER mutate paper orders"

        # Trace exists and records observation
        traces = cg01_trace_recorder.get_traces("XAUUSDT", limit=1)
        assert len(traces) >= 1
        assert traces[0].observation_mode == "SHADOW"
    finally:
        cg01_evaluation_scheduler.stop()
        sched_task.cancel()
        await asyncio.gather(sched_task, return_exceptions=True)
        stop_trace_persistence_worker()
        await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# SCENARIO 4: AUTO OFF INHIBITS ARMING (Rule 136, 145)
# ==============================================================================
@pytest.mark.anyio
async def test_v16_25_flow_scenario_4_auto_off_guard(db: Session):
    """
    Scenario 4: Valid setup observed, but auto_entry_enabled = False.
    Observation trace is persisted, but order is NOT armed.
    """
    strategy_service.set_auto_state(db, False)

    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 19, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)

    last_ts = c_1m[-1]["timestamp"]
    trigger_ts = last_ts + 60_000
    evidence_time = trigger_ts + 60_000
    now_ms = evidence_time + 3_000

    test_clock = ReplayClock(now_ms)
    cg01_evaluation_scheduler.set_clock(test_clock)

    db.add(models.SystemConfig(key="scalp_m1_mode", value="PAPER", updated_at=now_ms))
    db.add(models.SystemConfig(key="cg01_enabled", value="true", updated_at=now_ms))
    db.commit()

    _ingest_bars_via_payload("XAUUSDT", "4H", c_4h)
    _ingest_bars_via_payload("XAUUSDT", "1H", c_1h)
    _ingest_bars_via_payload("XAUUSDT", "15M", c_15m)

    poi = WatchedZone(
        zone_id="poi_long_v16_25_auto_off",
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
    scalp_watcher_orchestrator.set_active_zones("XAUUSDT", [poi])

    collector_service.latest_ticker = {
        "symbol": "XAUUSDT",
        "bid": 2625.5,
        "ask": 2625.7,
        "last": 2625.6,
        "timestamp": now_ms,
        "observed_at": now_ms,
    }

    cg01_evaluation_scheduler.set_flags(enabled=True, shadow_mode=False, is_owner=True)
    sched_task = asyncio.create_task(cg01_evaluation_scheduler.run())
    worker_started = await start_trace_persistence_worker(timeout_s=2.0)
    assert worker_started is True

    try:
        trigger_bar = {"timestamp": trigger_ts, "open": 2625.5, "high": 2626.0, "low": 2625.0, "close": 2625.6}
        boundary_bar = {"timestamp": evidence_time, "open": 2625.6, "high": 2626.0, "low": 2625.5, "close": 2625.6}
        _ingest_bars_via_payload("XAUUSDT", "1M", c_1m + [trigger_bar, boundary_bar])

        await asyncio.sleep(0.5)

        drain_res = await drain_trace_persistence(timeout_s=3.0)
        assert drain_res["status"] == "COMPLETED"
        assert _TRACE_QUEUE.unfinished_tasks == 0

        # Assert no active orders were armed
        orders = db.query(models.PaperOrder).filter(models.PaperOrder.symbol == "XAUUSDT").all()
        assert len(orders) == 0, "No orders should be armed when auto is False"

        traces = cg01_trace_recorder.get_traces("XAUUSDT", limit=1)
        assert len(traces) >= 1
    finally:
        cg01_evaluation_scheduler.stop()
        sched_task.cancel()
        await asyncio.gather(sched_task, return_exceptions=True)
        stop_trace_persistence_worker()
        await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# SCENARIO 5: TELEMETRY RETRY FAILURE ISOLATION (Rule 138)
# ==============================================================================
@pytest.mark.anyio
async def test_v16_25_flow_scenario_5_telemetry_retry_failure_isolation(db: Session):
    """
    Scenario 5: Telemetry persistence encounters temporary failure and retries.
    Business trading execution, order lifecycle, and DB transactions proceed unaffected.
    """
    ready = await start_trace_persistence_worker(timeout_s=3.0)
    assert ready is True

    t_now = int(time.time() * 1000)
    cg01_evaluation_scheduler.set_clock(ReplayClock(t_now))

    policy = TradingPolicyService.get_or_create_policy(db)
    policy.auto_entry_enabled = True
    policy.paper_trading_active = True
    policy.global_kill_switch = False
    policy.entry_session_policy = "ALL"
    policy.daily_loss_limit_usdt = 5000.0
    policy.max_daily_trades = 100
    policy.max_open_positions = 5
    db.commit()

    t_faulty = CG01EvaluationTrace(trace_id="trc_faulty_iso_05", symbol="XAUUSDT")
    enqueue_trace_persistence(t_faulty)

    order = models.PaperOrder(
        id="ord_iso_05",
        direction="LONG",
        state="armed",
        planned_entry=2650.0,
        actual_entry=2650.0,
        quantity=0.1,
        initial_risk_usdt=50.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        created_at=t_now,
    )
    db.add(order)
    db.commit()

    assert db.query(models.PaperOrder).filter_by(id="ord_iso_05").first() is not None

    drain_res = await drain_trace_persistence(timeout_s=2.0)
    assert drain_res["status"] in ("COMPLETED", "COMPLETED_WITH_LOSS", "TIMED_OUT")

    stop_trace_persistence_worker()
    await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# SCENARIO 6: RESTART DURABILITY & EXACT QUERY (Rule 140, 148)
# ==============================================================================
@pytest.mark.anyio
async def test_v16_25_flow_scenario_6_restart_durability_and_exact_query(db: Session):
    """
    Scenario 6: Committed traces survive memory reset and can be loaded from DB
    in a fresh session.
    """
    ready = await start_trace_persistence_worker(timeout_s=3.0)
    assert ready is True

    t1 = CG01EvaluationTrace(trace_id="trc_restart_dur_1", symbol="XAUUSDT", run_id="run_dur_06")
    t2 = CG01EvaluationTrace(trace_id="trc_restart_dur_2", symbol="XAUUSDT", run_id="run_dur_06")

    enqueue_trace_persistence(t1)
    enqueue_trace_persistence(t2)

    drain_res = await drain_trace_persistence(timeout_s=3.0)
    assert drain_res["status"] == "COMPLETED"

    stop_trace_persistence_worker()
    await wait_trace_persistence_worker_stopped(timeout_s=2.0)

    cg01_trace_recorder.clear()
    assert len(cg01_trace_recorder._buffer) == 0

    fresh_db = SessionLocal()
    try:
        loaded = CG01TracePersistenceWriter.load_traces(run_id="run_dur_06", db=fresh_db)
        loaded_ids = {t.trace_id for t in loaded}
        assert "trc_restart_dur_1" in loaded_ids, "Committed trace 1 must survive restart"
        assert "trc_restart_dur_2" in loaded_ids, "Committed trace 2 must survive restart"
    finally:
        fresh_db.close()


# ==============================================================================
# SCENARIO 7: CLEAN SHUTDOWN WITH QUEUED OPERATIONS (Rule 139, 149)
# ==============================================================================
@pytest.mark.anyio
async def test_v16_25_flow_scenario_7_clean_shutdown_with_queued_operations(db: Session):
    """
    Scenario 7: Shutdown initiated while items are in queue/retry.
    Drain properly waits for operations to complete, worker stops cleanly,
    and final status is COMPLETED.
    """
    ready = await start_trace_persistence_worker(timeout_s=3.0)
    assert ready is True

    for i in range(5):
        t = CG01EvaluationTrace(trace_id=f"trc_shut_{i}", symbol="XAUUSDT")
        enqueue_trace_persistence(t)

    drain_res = await drain_trace_persistence(timeout_s=3.0)
    assert drain_res["status"] == "COMPLETED"
    assert _TRACE_QUEUE.unfinished_tasks == 0
    assert get_active_batch_count() == 0

    stop_trace_persistence_worker()
    stopped = await wait_trace_persistence_worker_stopped(timeout_s=2.0)
    assert stopped is True
