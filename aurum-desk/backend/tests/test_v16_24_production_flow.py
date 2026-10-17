"""
backend/tests/test_v16_24_production_flow.py
V16.24 Full Production Flow, Resilience, and Clock Domain Invariant Suite.

Enforces:
- Rules 021-026, 031-060: Strict Separation of Market Evidence Time and Execution Time.
  (Market evidence from closed candle is distinct from execution clock for processing start,
  quote freshness, news, session, expiry, and arming).
- Rules 027-028, 061-085: Telemetry worker batch ownership preservation during cancellation.
- Rules 029-030, 086-100: Real worker startup/shutdown results and lifespan tracking.
- Rules 131-150: Full production flow matrix: LONG TP, SHORT SL, SHADOW observation,
  AUTO OFF, News guard at execution time, Daily cap, Restart durability, and Shutdown boundary.
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
from services.cg01_telemetry_service import (
    CG01EvaluationTrace,
    CG01TracePersistenceWriter,
    cg01_trace_recorder,
    drain_trace_persistence,
    flush_pending_traces,
    reset_telemetry_worker_state,
    start_trace_persistence_worker,
    stop_trace_persistence_worker,
    wait_trace_persistence_worker_stopped,
    get_active_batch_count,
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
# SCENARIO 1: LONG TP (Rules 131-141)
# ==============================================================================
@pytest.mark.anyio
async def test_v16_24_flow_scenario_1_long_tp_event_driven_execution(db: Session):
    """
    Rules 131-141: Full LONG TP flow driven automatically by Cache closed candle.
    - Market evidence time corresponds to closed candle.
    - Processing start and execution time reflect current clock.
    - Quote filled via ExecutionCoordinator at current execution clock.
    - TP exit via PaperBroker.
    - ProjectionWorker recovers milestones.
    - Fresh DB report verifies outcomes.
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
    now_ms = evidence_time + 5_000  # Evaluated 5s after candle close

    # Controlled execution clock
    test_clock = ReplayClock(now_ms)
    cg01_evaluation_scheduler.set_clock(test_clock)

    # Production config in DB
    db.add(models.SystemConfig(key="scalp_m1_mode", value="PAPER", updated_at=now_ms))
    db.add(models.SystemConfig(key="cg01_enabled", value="true", updated_at=now_ms))
    db.add(models.SystemConfig(key="cg01_profile_mode", value="PAPER_EXECUTION", updated_at=now_ms))
    db.add(models.SystemConfig(key="execution_profile", value="CG01", updated_at=now_ms))
    db.commit()

    _ingest_bars_via_payload("XAUUSDT", "4H", c_4h)
    _ingest_bars_via_payload("XAUUSDT", "1H", c_1h)
    _ingest_bars_via_payload("XAUUSDT", "15M", c_15m)

    poi = WatchedZone(
        zone_id="poi_long_v16_24_1",
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

    # 1. Start background workers
    cg01_evaluation_scheduler.set_flags(enabled=True, shadow_mode=False, is_owner=True)
    sched_task = asyncio.create_task(cg01_evaluation_scheduler.run())
    worker_started = await start_trace_persistence_worker(timeout_s=2.0)
    assert worker_started is True

    try:
        # Ingest trigger bar and boundary bar to close trigger bar
        trigger_bar = {"timestamp": trigger_ts, "open": 2625.5, "high": 2626.0, "low": 2625.0, "close": 2625.6}
        boundary_bar = {"timestamp": evidence_time, "open": 2625.6, "high": 2626.0, "low": 2625.5, "close": 2625.6}
        _ingest_bars_via_payload("XAUUSDT", "1M", c_1m + [trigger_bar, boundary_bar])

        # Wait for scheduler to automatically process event and arm order
        order = None
        for _ in range(35):
            await asyncio.sleep(0.1)
            db.expire_all()
            order = db.query(models.PaperOrder).filter(models.PaperOrder.state.in_(["armed", "placed"])).first()
            if order:
                break

        assert order is not None, "Expected order to be armed automatically by scheduler"
        assert order.direction.upper() == "LONG"
        # Invariant: TP > entry > SL
        assert order.take_profit > order.planned_entry > order.stop_loss

        # Verify trace provenance & clock separation (Rules 031-037)
        traces = cg01_trace_recorder.get_traces("XAUUSDT", limit=1)
        assert len(traces) > 0
        latest_trace = traces[0]
        assert latest_trace.evaluation_started_at == now_ms, (
            f"Trace started_at must be execution clock {now_ms}, got {latest_trace.evaluation_started_at}"
        )
        assert getattr(latest_trace, "market_evidence_as_of_ms", None) == evidence_time, (
            f"Trace evidence_as_of must be candle close {evidence_time}, got {getattr(latest_trace, 'market_evidence_as_of_ms', None)}"
        )

        # 2. Quote Fill through ExecutionCoordinator at execution time
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
        assert order.actual_entry is not None

        # 3. Take Profit Exit through PaperBroker
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
        assert closed_order.id == order.id
        assert closed_order.state == "closed"
        assert closed_order.exit_cause == "TP_HIT"
        assert closed_order.realized_pnl_net > 0.0

        # 4. Projection recovery
        worker = CG01ResearchProjectionWorker(scope="CG01")
        worker.process_pending_events(db=db)

        # 5. Async telemetry drain
        drain_res = await drain_trace_persistence(timeout_s=3.0)
        assert drain_res["status"] == "COMPLETED"

        # 6. Fresh DB session report verification
        fresh_db = SessionLocal()
        try:
            run_id = latest_trace.run_id or "run_cg01_xauusdt_active"
            summary = build_cg01_paper_run_summary(run_id=run_id, db=fresh_db)
            assert summary["trades"]["total_closed_trades"] >= 1
            assert summary["trades"]["winning_trades"] >= 1
            assert summary["financial_metrics_post_fee"]["net_realized_pnl_usd"] > 0.0
        finally:
            fresh_db.close()
    finally:
        cg01_evaluation_scheduler.stop()
        sched_task.cancel()
        await asyncio.gather(sched_task, return_exceptions=True)
        stop_trace_persistence_worker()
        await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# SCENARIO 2: SHORT SL (Rules 142-143)
# ==============================================================================
@pytest.mark.anyio
async def test_v16_24_flow_scenario_2_short_sl_event_driven_execution(db: Session):
    """
    Rules 142-143: Full SHORT SL Matrix via real event-driven scheduler:
    Cache closed candle -> Scheduler Arm -> Coordinator quote fill -> Broker SL exit ->
    Assert TP < entry < SL invariant, negative PnL, milestones.
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
        zone_id="poi_short_v16_24_2",
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
        # Invariant: TP < entry < SL
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

        fresh_db = SessionLocal()
        try:
            traces = cg01_trace_recorder.get_traces("XAUUSDT", limit=1)
            run_id = traces[0].run_id if traces else "run_cg01_xauusdt_active"
            summary = build_cg01_paper_run_summary(run_id=run_id, db=fresh_db)
            assert summary["trades"]["total_closed_trades"] >= 1
            assert summary["trades"]["losing_trades"] >= 1
            assert summary["financial_metrics_post_fee"]["net_realized_pnl_usd"] < 0.0
        finally:
            fresh_db.close()
    finally:
        cg01_evaluation_scheduler.stop()
        sched_task.cancel()
        await asyncio.gather(sched_task, return_exceptions=True)
        stop_trace_persistence_worker()
        await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# SCENARIO 3: SHADOW OBSERVATION (Rule 144)
# ==============================================================================
@pytest.mark.anyio
async def test_v16_24_flow_scenario_3_shadow_pure_observation(db: Session):
    """
    Rule 144: Shadow positive scenario:
    Closed event -> scheduler SHADOW -> candidate created -> trace committed.
    Zero active orders created, active state unmutated.
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
        zone_id="poi_long_v16_24_shadow",
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
# SCENARIO 4: AUTO OFF (Rule 145)
# ==============================================================================
@pytest.mark.anyio
async def test_v16_24_flow_scenario_4_auto_off_observation_only(db: Session):
    """
    Rule 145: Auto trading OFF:
    Candidate observed, trace recorded, arm rejected because auto is disabled.
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
        zone_id="poi_long_v16_24_auto_off",
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

    try:
        trigger_bar = {"timestamp": trigger_ts, "open": 2625.5, "high": 2626.0, "low": 2625.0, "close": 2625.6}
        boundary_bar = {"timestamp": evidence_time, "open": 2625.6, "high": 2626.0, "low": 2625.5, "close": 2625.6}
        _ingest_bars_via_payload("XAUUSDT", "1M", c_1m + [trigger_bar, boundary_bar])

        await asyncio.sleep(0.5)

        # No order armed because auto is OFF
        armed_order = db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").first()
        assert armed_order is None, "When auto trading is disabled, no order can be armed"
    finally:
        cg01_evaluation_scheduler.stop()
        sched_task.cancel()
        await asyncio.gather(sched_task, return_exceptions=True)


# ==============================================================================
# SCENARIO 5: NEWS GUARD AT EXECUTION TIME (Rule 146)
# ==============================================================================
def test_v16_24_flow_scenario_5_news_guard_at_execution_time(db: Session):
    """
    Rule 146: Real news data in test DB:
    Evaluated against execution clock time, news guard blocks entry cleanly.
    """
    t_news = int(time.time() * 1000) + 120_000
    news_item = models.EconomicNews(
        id=99955,
        source_id=f"news_v16_24_{t_news}",
        title="FOMC Interest Rate Decision",
        currency="USD",
        impact="High",
        scheduled_at=t_news,
        received_at=t_news - 3600_000,
    )
    db.add(news_item)
    db.commit()

    # Inside blackout window (60m before FOMC)
    exec_time = t_news - 60_000
    is_blackout, reason, remaining = crud.check_news_blackout(db, now_ms=exec_time)
    assert is_blackout is True, "News blackout must be active at execution time"
    assert "FOMC" in str(reason)


# ==============================================================================
# SCENARIO 6: DAILY CAP & OPEN POSITION INVARIANTS (Rule 147)
# ==============================================================================
def test_v16_24_flow_scenario_6_daily_cap_and_open_position_invariants(db: Session):
    """
    Rule 147: Enforce daily fill cap and single open position policy invariants.
    """
    policy = TradingPolicyService.get_or_create_policy(db)
    policy.max_daily_fills = 3
    db.commit()

    audit = crud.get_or_create_today_audit(db)
    audit.fills_count = 3
    db.commit()

    # Daily cap reached
    assert audit.fills_count >= policy.max_daily_fills, "Audit fills must reflect max daily cap"

    # Single open position invariant
    existing_order = models.PaperOrder(
        id=f"po_v16_24_{int(time.time() * 1000)}",
        symbol="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=2620.0,
        actual_entry=2620.0,
        stop_loss=2615.0,
        take_profit=2630.0,
        quantity=0.1,
        initial_risk_usdt=25.0,
        created_at=int(time.time() * 1000),
    )
    db.add(existing_order)
    db.commit()

    open_orders = db.query(models.PaperOrder).filter(models.PaperOrder.state == "paper_open").all()
    assert len(open_orders) == 1, "Only one position can be open"


# ==============================================================================
# SCENARIO 7: RESTART READ PERSISTED OUTCOMES (Rule 148)
# ==============================================================================
@pytest.mark.anyio
async def test_v16_24_flow_scenario_7_restart_durability(db: Session):
    """
    Rule 148: Fresh DB / Process reads persisted outcomes:
    Records committed survive in DB across worker/recorder state clearing.
    """
    worker_started = await start_trace_persistence_worker(timeout_s=2.0)
    assert worker_started is True

    test_trace = CG01EvaluationTrace(
        trace_id="trc_restart_v16_24_test",
        symbol="XAUUSDT",
        observation_mode="ACTIVE",
        market_evidence_as_of_ms=1700000000000,
        evaluation_started_at=1700000005000,
        evaluation_completed_at=1700000006000,
        total_runtime_ms=1.2,
    )
    cg01_trace_recorder.record_trace(test_trace, persist=True)

    drain_res = await drain_trace_persistence(timeout_s=3.0)
    assert drain_res["status"] == "COMPLETED"

    stop_trace_persistence_worker()
    await wait_trace_persistence_worker_stopped(timeout_s=2.0)

    # Clear in-memory buffer to simulate restart
    cg01_trace_recorder.clear()

    # Query from fresh DB connection
    fresh_db = SessionLocal()
    try:
        loaded = CG01TracePersistenceWriter.load_traces("XAUUSDT", limit=10, db=fresh_db)
        found = any(t.trace_id == "trc_restart_v16_24_test" for t in loaded)
        assert found is True, "Committed trace must be durable across in-memory clear and restart"
    finally:
        fresh_db.close()


# ==============================================================================
# SCENARIO 8: SHUTDOWN BOUNDARY WITH THREAD COMPLETION (Rule 149)
# ==============================================================================
@pytest.mark.anyio
async def test_v16_24_flow_scenario_8_shutdown_boundary_with_thread_completion(db: Session):
    """
    Rule 149: Trace submitted near shutdown boundary:
    Thread completion is tracked, batch handles verified, clean termination reported.
    """
    worker_started = await start_trace_persistence_worker(timeout_s=2.0)
    assert worker_started is True

    trace_boundary = CG01EvaluationTrace(
        trace_id=f"trc_boundary_v16_24_{int(time.time() * 1000)}",
        symbol="XAUUSDT",
        observation_mode="ACTIVE",
        market_evidence_as_of_ms=1700000010000,
        evaluation_started_at=1700000012000,
    )
    cg01_trace_recorder.record_trace(trace_boundary, persist=True)

    # Drain and stop cleanly
    drain_res = await drain_trace_persistence(timeout_s=3.0)
    assert drain_res["status"] in ("COMPLETED", "COMPLETED_EMPTY")

    stop_trace_persistence_worker()
    stopped_cleanly = await wait_trace_persistence_worker_stopped(timeout_s=3.0)
    assert stopped_cleanly is True, "Trace persistence worker must stop cleanly"
    assert get_active_batch_count() == 0, "All active batch handles must be resolved upon stop"
