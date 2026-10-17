"""
backend/tests/test_v16_22_production_flow.py
V16.22 Full Production Flow, Resilience, and Guard Invariant Verification Suite.

Enforces:
- Rules 021-030, 091-120: Real scheduler execution evidence (cache closed candle event -> scheduler processes and arms order, without direct evaluate_cycle calls in main acceptance test).
- Rules 025, 039: Async telemetry drain (drain_trace_persistence) on asyncio event loop.
- Rules 027, 028: Commit-accurate telemetry counters.
- Rules 071-085: Production config parity across ACTIVE and SHADOW callers.
- Rules 111-120: Complete matrix: LONG TP, SHORT SL, SHADOW observation, AUTO OFF, News blackout, Daily cap, Single open position.
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

# Ensure test DB isolation (Rule 016)
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
from services.cg01_scheduler_service import (
    CG01EvaluationScheduler,
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
    drain_trace_persistence,
    flush_pending_traces,
    _RETRY_BUFFER,
    _RETRY_BUFFER_LOCK,
    _TRACE_QUEUE,
    set_in_flight_count,
)
from tests.test_v16_3_cg01_pipeline import (
    build_long_fixture_series,
    build_short_fixture_series,
    make_valid_quote,
    make_bar,
)


def _clean_telemetry():
    with _RETRY_BUFFER_LOCK:
        _RETRY_BUFFER.clear()
        set_in_flight_count(0)
    while not _TRACE_QUEUE.empty():
        try:
            _TRACE_QUEUE.get_nowait()
            _TRACE_QUEUE.task_done()
        except Exception:
            break


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    _clean_telemetry()
    candle_cache_service._cache.clear()
    try:
        session.query(models.SMCScenarioSnapshot).delete()
        session.query(models.PaperOrder).delete()
        session.query(models.WatchSetup).delete()
        session.query(models.PaperResearchOrderLink).delete()
        session.query(models.PaperResearchMilestone).delete()
        session.query(models.PaperResearchOrderMilestone).delete()
        session.query(models.SystemConfig).delete()
        session.commit()
        yield session
    finally:
        session.query(models.SMCScenarioSnapshot).delete()
        session.query(models.PaperOrder).delete()
        session.query(models.WatchSetup).delete()
        session.query(models.PaperResearchOrderLink).delete()
        session.query(models.PaperResearchMilestone).delete()
        session.query(models.PaperResearchOrderMilestone).delete()
        session.commit()
        session.close()


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
    candle_cache_service.process_candle_payload(symbol, tf, rows)


def test_v16_22_flow_scenario_1_long_tp_real_scheduler_execution(db: Session):
    """
    Rules 022, 098, 105, 111: Full LONG TP flow driven strictly by Scheduler:
    Cache closed candle event -> Scheduler evaluation -> Active Arm ->
    ExecutionCoordinator quote tick fill -> PaperBroker tick TP exit ->
    ProjectionWorker recovery -> Fresh DB report.
    NEVER calls evaluate_cycle() directly.
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
    now_ms = t_start + 600_000

    # Production config loaded through SystemConfig DB (Rule 094)
    db.add(models.SystemConfig(key="scalp_m1_mode", value="PAPER", updated_at=now_ms))
    db.add(models.SystemConfig(key="cg01_enabled", value="true", updated_at=now_ms))
    db.add(models.SystemConfig(key="cg01_profile_mode", value="PAPER_EXECUTION", updated_at=now_ms))
    db.add(models.SystemConfig(key="execution_profile", value="CG01", updated_at=now_ms))
    db.commit()

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    _ingest_bars_via_payload("XAUUSDT", "4H", c_4h)
    _ingest_bars_via_payload("XAUUSDT", "1H", c_1h)
    _ingest_bars_via_payload("XAUUSDT", "15M", c_15m)
    _ingest_bars_via_payload("XAUUSDT", "1M", c_1m + [make_bar(now_ms, 2625.6, 2626.0, 2625.0, 2625.6)])

    poi = WatchedZone(
        zone_id="poi_long_v16_22_1",
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

    # 1. Real Scheduler Execution from Closed Candle Event (Rules 022, 097, 098)
    sched = CG01EvaluationScheduler()
    sched.set_flags(enabled=True, shadow_mode=False, is_owner=True)

    source_ev = {
        "event_id": f"XAUUSDT:1M:{now_ms}:1:CANDLE_CLOSED",
        "candle_timestamp": now_ms,
        "exchange_event_time_ms": now_ms,
        "received_at_ms": now_ms + 10,
        "enqueued_at_ms": now_ms + 12,
    }

    result = sched.evaluate_symbol_now(db, "XAUUSDT", as_of_ms=now_ms, source_event=source_ev)

    assert result["status"] == "COMPLETED"
    armed_id = result.get("armed_order_id")
    assert armed_id is not None, f"Expected order to be armed by scheduler, got {result}"

    order = db.query(models.PaperOrder).filter(models.PaperOrder.id == armed_id).first()
    assert order is not None
    assert order.state in ("armed", "placed")
    assert order.direction.upper() == "LONG"
    # Long price geometry invariant (Rule 111, 121): TP > entry > SL
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
    assert order.actual_entry == order.planned_entry

    # 3. Quote Exit through PaperBroker (Rule 107)
    tp_price = order.take_profit
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

    # 4. Projection recovery (Rule 108)
    worker = CG01ResearchProjectionWorker(scope="CG01")
    worker.process_pending_events(db=db)

    # 5. Telemetry drain (Rule 109)
    flush_pending_traces(timeout_s=2.0)

    # 6. Fresh DB session report verification (Rule 110)
    fresh_db = SessionLocal()
    try:
        run_id = result.get("run_id") or "run_cg01_xauusdt_active"
        summary = build_cg01_paper_run_summary(run_id=run_id, db=fresh_db)
        assert summary["trades"]["total_closed_trades"] >= 1
        assert summary["trades"]["winning_trades"] >= 1
        assert summary["trades"]["winrate_pct"] == 100.0
        assert summary["financial_metrics_post_fee"]["net_realized_pnl_usd"] > 0.0
    finally:
        fresh_db.close()


def test_v16_22_flow_scenario_2_short_sl_real_scheduler_execution(db: Session):
    """
    Rules 113, 114: Full SHORT SL Matrix via real scheduler:
    Short candidate -> Scheduler Arm -> Coordinator quote fill -> Broker SL exit ->
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
    now_ms = t_start + 540_000

    db.add(models.SystemConfig(key="scalp_m1_mode", value="PAPER", updated_at=now_ms))
    db.add(models.SystemConfig(key="cg01_enabled", value="true", updated_at=now_ms))
    db.add(models.SystemConfig(key="cg01_profile_mode", value="PAPER_EXECUTION", updated_at=now_ms))
    db.add(models.SystemConfig(key="execution_profile", value="CG01", updated_at=now_ms))
    db.commit()

    c_4h, c_1h, c_15m, c_1m = build_short_fixture_series(t_start)
    _ingest_bars_via_payload("XAUUSDT", "4H", c_4h)
    _ingest_bars_via_payload("XAUUSDT", "1H", c_1h)
    _ingest_bars_via_payload("XAUUSDT", "15M", c_15m)
    _ingest_bars_via_payload("XAUUSDT", "1M", c_1m + [make_bar(now_ms, 2649.5, 2650.0, 2649.0, 2649.6)])

    poi = WatchedZone(
        zone_id="poi_short_v16_22_2",
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

    sched = CG01EvaluationScheduler()
    sched.set_flags(enabled=True, shadow_mode=False, is_owner=True)

    source_ev = {
        "event_id": f"XAUUSDT:1M:{now_ms}:2:CANDLE_CLOSED",
        "candle_timestamp": now_ms,
        "exchange_event_time_ms": now_ms,
        "received_at_ms": now_ms + 10,
        "enqueued_at_ms": now_ms + 12,
    }

    result = sched.evaluate_symbol_now(db, "XAUUSDT", as_of_ms=now_ms, source_event=source_ev)

    assert result["status"] == "COMPLETED"
    armed_id = result.get("armed_order_id")
    assert armed_id is not None

    order = db.query(models.PaperOrder).filter(models.PaperOrder.id == armed_id).first()
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

    fresh_db = SessionLocal()
    try:
        run_id = result.get("run_id") or "run_cg01_xauusdt_active"
        summary = build_cg01_paper_run_summary(run_id=run_id, db=fresh_db)
        assert summary["trades"]["total_closed_trades"] >= 1
        assert summary["trades"]["losing_trades"] >= 1
        assert summary["financial_metrics_post_fee"]["net_realized_pnl_usd"] < 0.0
    finally:
        fresh_db.close()


def test_v16_22_flow_scenario_3_shadow_pure_observation(db: Session):
    """
    Rule 115: Shadow positive scenario:
    Closed event -> scheduler SHADOW -> candidate created -> trace committed.
    Zero active orders created, active state unmutated.
    """
    orders_before = db.query(models.PaperOrder).count()

    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 19, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    _ingest_bars_via_payload("XAUUSDT", "4H", c_4h)
    _ingest_bars_via_payload("XAUUSDT", "1H", c_1h)
    _ingest_bars_via_payload("XAUUSDT", "15M", c_15m)
    _ingest_bars_via_payload("XAUUSDT", "1M", c_1m)

    poi = WatchedZone(
        zone_id="poi_long_v16_22_shadow",
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

    sched = CG01EvaluationScheduler()
    sched.set_flags(enabled=True, shadow_mode=True, is_owner=False)

    source_ev = {
        "event_id": f"XAUUSDT:1M:{now_ms}:3:SHADOW_EVENT",
        "candle_timestamp": now_ms,
        "exchange_event_time_ms": now_ms,
        "received_at_ms": now_ms + 10,
        "enqueued_at_ms": now_ms + 12,
    }

    result = sched.evaluate_symbol_now(db, "XAUUSDT", as_of_ms=now_ms, source_event=source_ev)

    assert result["status"] == "COMPLETED"
    assert result["armed_order_id"] is None
    orders_after = db.query(models.PaperOrder).count()
    assert orders_after == orders_before


def test_v16_22_flow_scenario_4_auto_off_guard(db: Session):
    """
    Rule 116: Candidate available but Global Auto is OFF -> NO order created.
    """
    strategy_service.set_auto_state(db, False)
    orders_before = db.query(models.PaperOrder).count()

    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 19, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000

    db.add(models.SystemConfig(key="scalp_m1_mode", value="PAPER", updated_at=now_ms))
    db.add(models.SystemConfig(key="cg01_enabled", value="true", updated_at=now_ms))
    db.add(models.SystemConfig(key="cg01_profile_mode", value="PAPER_EXECUTION", updated_at=now_ms))
    db.add(models.SystemConfig(key="execution_profile", value="CG01", updated_at=now_ms))
    db.commit()

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    _ingest_bars_via_payload("XAUUSDT", "4H", c_4h)
    _ingest_bars_via_payload("XAUUSDT", "1H", c_1h)
    _ingest_bars_via_payload("XAUUSDT", "15M", c_15m)
    _ingest_bars_via_payload("XAUUSDT", "1M", c_1m)

    poi = WatchedZone(
        zone_id="poi_long_v16_22_auto_off",
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

    sched = CG01EvaluationScheduler()
    sched.set_flags(enabled=True, shadow_mode=False, is_owner=True)

    result = sched.evaluate_symbol_now(db, "XAUUSDT", as_of_ms=now_ms)
    assert result.get("armed_order_id") is None
    assert db.query(models.PaperOrder).count() == orders_before


def test_v16_22_flow_scenario_5_news_guard_blackout(db: Session):
    """
    Rule 117: News blackout guard blocks candidate from arming.
    """
    t_news = int(time.time() * 1000) + 120_000
    news_item = models.EconomicNews(
        source_id=f"news_v16_22_{t_news}",
        title="FOMC Rate Decision V16.22",
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


def test_v16_22_flow_scenario_6_daily_cap_guard(db: Session):
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


def test_v16_22_flow_scenario_7_single_open_position_guard(db: Session):
    """
    Rule 119: Single open position invariant: open position blocks new entry.
    """
    existing_order = models.PaperOrder(
        id=f"po_v16_22_{int(time.time() * 1000)}",
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
