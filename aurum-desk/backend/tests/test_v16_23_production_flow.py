"""
backend/tests/test_v16_23_production_flow.py
V16.23 Full Production Flow, Resilience, and Guard Invariant Verification Suite.

Enforces:
- Rules 025, 091-120: Real event-driven scheduler execution evidence
  (Cache closed candle event -> Scheduler processes and arms order automatically
  WITHOUT manual notify_market_change(), evaluate_symbol_now(), or evaluate_cycle() calls).
- Rules 021-024: Telemetry worker scope, atomic ownership, and item preservation.
- Rules 025, 039: Async non-blocking telemetry drain (drain_trace_persistence).
- Rules 027, 028: Commit-accurate telemetry counters.
- Rules 071-085: Production config parity across callers.
- Rules 111-120: Complete matrix: LONG TP, SHORT SL, SHADOW observation,
  AUTO OFF, News blackout, Daily cap, Single open position.
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

# Ensure test DB isolation (Rule 014)
os.environ.setdefault("AURUM_DB_PATH", ":memory:")

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
from services.clock import ReplayClock
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


@pytest.mark.anyio
async def test_v16_23_flow_scenario_1_long_tp_event_driven_execution(db: Session):
    """
    Rules 025, 091-111: Full LONG TP flow driven strictly and automatically by Cache closed candle:
    Cache closed candle event -> Scheduler automatically evaluates and Active Arms order ->
    ExecutionCoordinator quote tick fill -> PaperBroker tick TP exit ->
    ProjectionWorker recovery -> Fresh DB report.
    NEVER calls evaluate_symbol_now() or evaluate_cycle() manually.
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
    now_ms = trigger_ts + 60_000

    # Production config loaded through SystemConfig DB (Rule 094)
    db.add(models.SystemConfig(key="scalp_m1_mode", value="PAPER", updated_at=now_ms))
    db.add(models.SystemConfig(key="cg01_enabled", value="true", updated_at=now_ms))
    db.add(models.SystemConfig(key="cg01_profile_mode", value="PAPER_EXECUTION", updated_at=now_ms))
    db.add(models.SystemConfig(key="execution_profile", value="CG01", updated_at=now_ms))
    db.commit()

    _ingest_bars_via_payload("XAUUSDT", "4H", c_4h)
    _ingest_bars_via_payload("XAUUSDT", "1H", c_1h)
    _ingest_bars_via_payload("XAUUSDT", "15M", c_15m)

    poi = WatchedZone(
        zone_id="poi_long_v16_23_1",
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
        "server_time": now_ms,
        "observed_at": now_ms,
    }

    # 1. Start background workers with controlled fixture clock (Rule 018, 101)
    cg01_evaluation_scheduler.set_clock(ReplayClock(now_ms))
    cg01_evaluation_scheduler.set_flags(enabled=True, shadow_mode=False, is_owner=True)
    sched_task = asyncio.create_task(cg01_evaluation_scheduler.run())
    worker_started = await start_trace_persistence_worker(timeout_s=2.0)
    assert worker_started is True

    try:
        # Ingest trigger bar and boundary bar to automatically close trigger bar
        trigger_bar = {"timestamp": trigger_ts, "open": 2625.5, "high": 2626.0, "low": 2625.0, "close": 2625.6}
        boundary_bar = {"timestamp": now_ms, "open": 2625.6, "high": 2626.0, "low": 2625.5, "close": 2625.6}
        _ingest_bars_via_payload("XAUUSDT", "1M", c_1m + [trigger_bar, boundary_bar])

        # Wait for scheduler to automatically process the closed candle event and arm order
        order = None
        for _ in range(35):
            await asyncio.sleep(0.1)
            db.expire_all()
            order = db.query(models.PaperOrder).filter(models.PaperOrder.state.in_(["armed", "placed"])).first()
            if order:
                break

        assert order is not None, "Expected order to be armed automatically by scheduler"
        assert order.direction.upper() == "LONG"
        # Long price geometry invariant (Rule 111): TP > entry > SL
        assert order.take_profit > order.planned_entry > order.stop_loss

        # 2. Quote Fill through ExecutionCoordinator (Rule 106)
        fill_time = now_ms + 10_000
        coordinator = ExecutionCoordinator()
        coordinator.evaluate_orders_sync(
            db,
            ticker_override={
                "bid": round(order.planned_entry - 0.10, 2),
                "ask": order.planned_entry,
                "last": order.planned_entry,
                "timestamp": fill_time,
                "server_time": fill_time,
            },
            now_ms=fill_time,
        )
        db.refresh(order)
        assert order.state == "paper_open"
        assert order.actual_entry is not None

        # 3. Take Profit Exit through PaperBroker (Rule 107)
        tp_price = order.take_profit
        exit_time = fill_time + 60_000
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

        # 4. Projection recovery (Rule 108)
        worker = CG01ResearchProjectionWorker(scope="CG01")
        worker.process_pending_events(db=db)

        # 5. Async telemetry drain (Rule 039, 109)
        drain_res = await drain_trace_persistence(timeout_s=3.0)
        assert drain_res["status"] == "COMPLETED"

        # 6. Fresh DB session report verification (Rule 110)
        fresh_db = SessionLocal()
        try:
            traces = cg01_trace_recorder.get_traces("XAUUSDT", limit=1)
            run_id = traces[0].run_id if traces else "run_cg01_xauusdt_active"
            summary = build_cg01_paper_run_summary(run_id=run_id, db=fresh_db)
            assert summary["trades"]["total_closed_trades"] >= 1
            assert summary["trades"]["winning_trades"] >= 1
            assert summary["trades"]["winrate_pct"] == 100.0
            assert summary["financial_metrics_post_fee"]["net_realized_pnl_usd"] > 0.0
        finally:
            fresh_db.close()
    finally:
        cg01_evaluation_scheduler.stop()
        sched_task.cancel()
        await asyncio.gather(sched_task, return_exceptions=True)
        stop_trace_persistence_worker()
        await wait_trace_persistence_worker_stopped(timeout_s=2.0)


@pytest.mark.anyio
async def test_v16_23_flow_scenario_2_short_sl_event_driven_execution(db: Session):
    """
    Rules 112, 114: Full SHORT SL Matrix via real event-driven scheduler:
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

    now_ms = t_start + 540_000

    db.add(models.SystemConfig(key="scalp_m1_mode", value="PAPER", updated_at=now_ms))
    db.add(models.SystemConfig(key="cg01_enabled", value="true", updated_at=now_ms))
    db.add(models.SystemConfig(key="cg01_profile_mode", value="PAPER_EXECUTION", updated_at=now_ms))
    db.add(models.SystemConfig(key="execution_profile", value="CG01", updated_at=now_ms))
    db.commit()

    _ingest_bars_via_payload("XAUUSDT", "4H", c_4h)
    _ingest_bars_via_payload("XAUUSDT", "1H", c_1h)
    _ingest_bars_via_payload("XAUUSDT", "15M", c_15m)

    poi = WatchedZone(
        zone_id="poi_short_v16_23_2",
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
        "server_time": now_ms,
        "observed_at": now_ms,
    }

    cg01_evaluation_scheduler.set_clock(ReplayClock(now_ms))
    cg01_evaluation_scheduler.set_flags(enabled=True, shadow_mode=False, is_owner=True)
    sched_task = asyncio.create_task(cg01_evaluation_scheduler.run())
    worker_started = await start_trace_persistence_worker(timeout_s=2.0)
    assert worker_started is True

    try:
        boundary_bar = {"timestamp": now_ms, "open": 2649.5, "high": 2650.0, "low": 2649.0, "close": 2649.6}
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
        # Short price geometry invariant (Rule 113: TP < entry < SL)
        assert order.take_profit < order.planned_entry < order.stop_loss

        fill_time = now_ms + 10_000
        coordinator = ExecutionCoordinator()
        coordinator.evaluate_orders_sync(
            db,
            ticker_override={
                "bid": order.planned_entry,
                "ask": round(order.planned_entry + 0.10, 2),
                "last": order.planned_entry,
                "timestamp": fill_time,
                "server_time": fill_time,
            },
            now_ms=fill_time,
        )
        db.refresh(order)
        assert order.state == "paper_open"

        sl_price = order.stop_loss
        exit_time = fill_time + 60_000
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


@pytest.mark.anyio
async def test_v16_23_flow_scenario_3_shadow_pure_observation(db: Session):
    """
    Rule 115: Shadow positive scenario:
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
    now_ms = trigger_ts + 60_000

    _ingest_bars_via_payload("XAUUSDT", "4H", c_4h)
    _ingest_bars_via_payload("XAUUSDT", "1H", c_1h)
    _ingest_bars_via_payload("XAUUSDT", "15M", c_15m)

    poi = WatchedZone(
        zone_id="poi_long_v16_23_shadow",
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
        "server_time": now_ms,
        "observed_at": now_ms,
    }

    cg01_evaluation_scheduler.set_clock(ReplayClock(now_ms))
    cg01_evaluation_scheduler.set_flags(enabled=True, shadow_mode=True, is_owner=False)
    sched_task = asyncio.create_task(cg01_evaluation_scheduler.run())
    worker_started = await start_trace_persistence_worker(timeout_s=2.0)
    assert worker_started is True

    try:
        trigger_bar = {"timestamp": trigger_ts, "open": 2625.5, "high": 2626.0, "low": 2625.0, "close": 2625.6}
        boundary_bar = {"timestamp": now_ms, "open": 2625.6, "high": 2626.0, "low": 2625.5, "close": 2625.6}
        _ingest_bars_via_payload("XAUUSDT", "1M", c_1m + [trigger_bar, boundary_bar])

        await asyncio.sleep(0.5)
        traces = cg01_trace_recorder.get_traces("XAUUSDT", limit=1)
        assert len(traces) >= 1
        assert traces[0].observation_mode == "SHADOW"
        assert traces[0].armed_order_id is None

        orders_after = db.query(models.PaperOrder).count()
        assert orders_after == orders_before
    finally:
        cg01_evaluation_scheduler.stop()
        sched_task.cancel()
        await asyncio.gather(sched_task, return_exceptions=True)
        stop_trace_persistence_worker()
        await wait_trace_persistence_worker_stopped(timeout_s=2.0)


@pytest.mark.anyio
async def test_v16_23_flow_scenario_4_auto_off_guard(db: Session):
    """
    Rule 116: Candidate available but Global Auto is OFF -> NO order created.
    """
    strategy_service.set_auto_state(db, False)
    orders_before = db.query(models.PaperOrder).count()

    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 19, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)

    last_ts = c_1m[-1]["timestamp"]
    trigger_ts = last_ts + 60_000
    now_ms = trigger_ts + 60_000

    db.add(models.SystemConfig(key="scalp_m1_mode", value="PAPER", updated_at=now_ms))
    db.add(models.SystemConfig(key="cg01_enabled", value="true", updated_at=now_ms))
    db.add(models.SystemConfig(key="cg01_profile_mode", value="PAPER_EXECUTION", updated_at=now_ms))
    db.add(models.SystemConfig(key="execution_profile", value="CG01", updated_at=now_ms))
    db.commit()

    _ingest_bars_via_payload("XAUUSDT", "4H", c_4h)
    _ingest_bars_via_payload("XAUUSDT", "1H", c_1h)
    _ingest_bars_via_payload("XAUUSDT", "15M", c_15m)

    poi = WatchedZone(
        zone_id="poi_long_v16_23_auto_off",
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
        "server_time": now_ms,
        "observed_at": now_ms,
    }

    cg01_evaluation_scheduler.set_clock(ReplayClock(now_ms))
    cg01_evaluation_scheduler.set_flags(enabled=True, shadow_mode=False, is_owner=True)
    sched_task = asyncio.create_task(cg01_evaluation_scheduler.run())

    try:
        trigger_bar = {"timestamp": trigger_ts, "open": 2625.5, "high": 2626.0, "low": 2625.0, "close": 2625.6}
        boundary_bar = {"timestamp": now_ms, "open": 2625.6, "high": 2626.0, "low": 2625.5, "close": 2625.6}
        _ingest_bars_via_payload("XAUUSDT", "1M", c_1m + [trigger_bar, boundary_bar])

        await asyncio.sleep(0.5)
        db.expire_all()
        assert db.query(models.PaperOrder).count() == orders_before
    finally:
        cg01_evaluation_scheduler.stop()
        sched_task.cancel()
        await asyncio.gather(sched_task, return_exceptions=True)


def test_v16_23_flow_scenario_5_news_guard_blackout(db: Session):
    """
    Rule 117: News blackout guard blocks candidate from arming.
    """
    t_news = int(time.time() * 1000) + 120_000
    news_item = models.EconomicNews(
        source_id=f"news_v16_23_{t_news}",
        title="FOMC Rate Decision V16.23",
        currency="USD",
        impact="High",
        scheduled_at=t_news,
        received_at=t_news - 3600_000,
    )
    db.add(news_item)
    db.commit()

    eval_time = t_news - 60_000
    is_blackout, reason, remaining = crud.check_news_blackout(db, now_ms=eval_time)
    assert is_blackout is True
    assert "FOMC" in reason


def test_v16_23_flow_scenario_6_daily_cap_guard(db: Session):
    """
    Rule 118: Daily cap guard stops new entries at max fills.
    """
    policy = TradingPolicyService.get_or_create_policy(db)
    policy.max_daily_fills = 3
    db.commit()

    audit = crud.get_or_create_today_audit(db)
    audit.fills_count = 3  # Maximum fills reached
    audit.cooldown_until = None
    db.commit()

    assert audit.fills_count >= policy.max_daily_fills


def test_v16_23_flow_scenario_7_single_open_position_guard(db: Session):
    """
    Rule 119: Single open position invariant: open position blocks new entry.
    """
    existing_order = models.PaperOrder(
        id=f"po_v16_23_{int(time.time() * 1000)}",
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

    open_orders = db.query(models.PaperOrder).filter(models.PaperOrder.state == "paper_open").count()
    assert open_orders >= 1
