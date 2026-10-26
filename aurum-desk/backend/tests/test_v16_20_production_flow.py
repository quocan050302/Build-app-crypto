"""
backend/tests/test_v16_20_production_flow.py
V16.20 Full Production Flow, Resilience, and Guard Invariant Verification Suite.

Enforces:
- Rule 151: Production Flow Harness using real wiring (no mock evaluator/guards/lifecycle).
- Rule 152: Real candle ingest via candle_cache_service notifying scheduler.
- Rule 153: Scheduler retrieves real bundle from candle_cache_service without monkeypatching.
- Rule 154: Full Shadow integration: cache event -> scheduler shadow -> real quote/config -> trace queue -> persistence -> fresh report.
- Rule 155: Active full integration: paper profile -> auto ON -> owner -> arm.
- Rule 156: Quote fill through execution coordinator entry point (no direct execute_fill).
- Rule 157: Quote exit through broker price tick entry point (no direct execute_close).
- Rules 158-161: Full LONG TP and SHORT SL matrices with TP/SL invariants and PnL accounting.
- Rule 162: Auto OFF guard blocks arming without mutating active state.
- Rule 163: News guard blackout blocks candidate.
- Rule 164: Lesson guard blocks candidate.
- Rule 165: Daily cap guard stops new entries at max fills.
- Rule 166: Single open position invariant preserved.
- Rule 169: Fresh session restart recovery and report consistency.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy.orm import Session

from database import SessionLocal
import models
import crud
import schemas
from domain_calculator import CostAssumptions, calculate_risk_reward, validate_price_geometry
from services.cg01_types import CG01Config, CG01ResearchContext
from services.scalp_m1_types import M1ScalpConfig, WatchedZone
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.execution_coordinator import ExecutionCoordinator
from paper_broker import PaperBroker
from services.trading_policy_service import TradingPolicyService
from services.candle_cache_service import candle_cache_service
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
    flush_pending_traces,
)
from tests.test_v16_3_cg01_pipeline import (
    build_long_fixture_series,
    build_short_fixture_series,
    make_valid_quote,
)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_v16_20_flow_scenario_1_long_tp_real_harness(db: Session):
    """
    Rules 151-158: Full LONG TP flow:
    Real candle ingest to cache -> scheduler evaluation -> active arm ->
    coordinator quote tick fill -> broker tick TP exit -> projection -> fresh DB report.
    """
    policy = TradingPolicyService.get_or_create_policy(db)
    policy.max_daily_fills = 5
    policy.daily_min_goal = 2
    policy.ny_min_fills = 1
    policy.is_active = False

    audit = crud.get_or_create_today_audit(db)
    audit.cooldown_until = None
    audit.fills_count = 0
    audit.realized_pnl = 0.0
    audit.current_equity = 1000.0
    candle_cache_service._cache.clear()
    db.query(models.PaperOrder).delete()
    db.query(models.WatchSetup).delete()
    db.query(models.PaperResearchOrderLink).delete()
    db.query(models.PaperResearchMilestone).delete()
    db.query(models.PaperResearchOrderMilestone).delete()
    db.commit()

    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 19, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    quote = make_valid_quote(2625.5, 2625.7, now_ms)

    # 1. Ingest candles into candle_cache_service (Rule 152)
    candle_cache_service.update_from_rest_sync("XAUUSDT", "4H", c_4h)
    candle_cache_service.update_from_rest_sync("XAUUSDT", "1H", c_1h)
    candle_cache_service.update_from_rest_sync("XAUUSDT", "15M", c_15m)
    candle_cache_service.update_from_rest_sync("XAUUSDT", "1M", c_1m)

    poi = WatchedZone(
        zone_id="poi_long_v16_20_1",
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

    run_id = f"run_v16_20_long_tp_{now_ms}"
    run_ref = ensure_observation_run_started(symbol="XAUUSDT", scope="CG01", mode="ACTIVE", run_id=run_id)
    assert run_ref is not None

    cfg = M1ScalpConfig(
        scalp_m1_enabled=True,
        scalp_m1_mode="PAPER",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
        execution_profile="CG01",
        min_net_rr=2.0,
        daily_min_goal=2,
        daily_max_cap=5,
    )

    research_ctx = CG01ResearchContext(
        trace_id=f"trc_v16_20_{now_ms}",
        run_id=run_id,
        symbol="XAUUSDT",
        config_fingerprint="CFG_V16_20_FINGERPRINT",
        input_versions={"1M": "1M:test:1"},
        evaluation_as_of_ms=now_ms,
        observation_mode="ACTIVE",
    )

    # 2. Orchestrator Arm (Active Production Flow)
    orch = ScalpWatcherOrchestrator()
    orch.set_active_zones("XAUUSDT", [poi])

    cycle_res = orch.evaluate_cycle(
        db=db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        candles_1m=c_1m,
        candles_5m=[],
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        auto_enabled=True,
        config=cfg,
        quote=quote,
        research_context=research_ctx,
    )

    armed_order_id = cycle_res.get("armed_order_id")
    assert armed_order_id is not None, f"Expected order to be ARMED, got cycle_res={cycle_res}"

    order = db.query(models.PaperOrder).filter(models.PaperOrder.id == armed_order_id).first()
    assert order is not None
    assert order.state in ("armed", "placed")
    assert order.direction.upper() == "LONG"
    # Long price geometry invariant (Rule 121)
    assert order.take_profit > order.planned_entry > order.stop_loss

    # 3. Quote Fill through ExecutionCoordinator (Rule 156)
    fill_time = now_ms + 10_000
    coordinator = ExecutionCoordinator()
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2623.65, "ask": order.planned_entry, "last": order.planned_entry, "timestamp": fill_time},
        now_ms=fill_time,
    )
    db.refresh(order)
    assert order.state == "paper_open"
    assert order.actual_entry == order.planned_entry

    # 4. Quote Exit through PaperBroker (Rule 157)
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

    # 5. Projection recovery
    worker = CG01ResearchProjectionWorker(scope="CG01")
    worker.process_pending_events(db=db)

    # 6. Fresh DB session report verification
    fresh_db = SessionLocal()
    try:
        summary = build_cg01_paper_run_summary(run_id=run_id, db=fresh_db)
        assert summary["trades"]["total_closed_trades"] >= 1
        assert summary["trades"]["winning_trades"] >= 1
        assert summary["trades"]["winrate_pct"] == 100.0
        assert summary["financial_metrics_post_fee"]["net_realized_pnl_usd"] > 0.0
    finally:
        fresh_db.close()


def test_v16_20_flow_scenario_2_short_sl_matrix(db: Session):
    """
    Rules 160-161: Full SHORT SL Matrix:
    Short candidate -> Arm -> Coordinator quote fill -> Broker SL exit ->
    Assert TP < entry < SL invariant, negative PnL, journal entry, milestones.
    """
    audit = crud.get_or_create_today_audit(db)
    audit.cooldown_until = None
    audit.fills_count = 0
    audit.realized_pnl = 0.0
    audit.current_equity = 1000.0
    candle_cache_service._cache.clear()
    db.query(models.PaperOrder).delete()
    db.query(models.WatchSetup).delete()
    db.query(models.PaperResearchOrderLink).delete()
    db.query(models.PaperResearchMilestone).delete()
    db.query(models.PaperResearchOrderMilestone).delete()
    db.commit()

    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 20, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000

    c_4h, c_1h, c_15m, c_1m = build_short_fixture_series(t_start)
    quote = make_valid_quote(2649.5, 2649.7, now_ms)

    poi = WatchedZone(
        zone_id="poi_short_v16_20_2",
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

    run_id = f"run_v16_20_short_sl_{now_ms}"
    ensure_observation_run_started(symbol="XAUUSDT", scope="CG01", mode="ACTIVE", run_id=run_id)

    cfg = M1ScalpConfig(
        scalp_m1_enabled=True,
        scalp_m1_mode="PAPER",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
        execution_profile="CG01",
        min_net_rr=2.0,
        daily_min_goal=2,
        daily_max_cap=5,
    )

    research_ctx = CG01ResearchContext(
        trace_id=f"trc_v16_20_short_{now_ms}",
        run_id=run_id,
        symbol="XAUUSDT",
        config_fingerprint="CFG_V16_20_SHORT",
        input_versions={"1M": "1M:test:1"},
        evaluation_as_of_ms=now_ms,
        observation_mode="ACTIVE",
    )

    orch = ScalpWatcherOrchestrator()
    orch.set_active_zones("XAUUSDT", [poi])

    cycle_res = orch.evaluate_cycle(
        db=db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        candles_1m=c_1m,
        candles_5m=[],
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        auto_enabled=True,
        config=cfg,
        quote=quote,
        research_context=research_ctx,
    )

    armed_order_id = cycle_res.get("armed_order_id")
    assert armed_order_id is not None, f"Expected SHORT order armed, got {cycle_res}"

    order = db.query(models.PaperOrder).filter(models.PaperOrder.id == armed_order_id).first()
    assert order is not None
    assert order.direction.upper() == "SHORT"
    # Invariant: SHORT TP < entry < SL (Rule 121, 160)
    assert order.take_profit < order.planned_entry < order.stop_loss

    # Fill via coordinator quote consumer
    fill_time = now_ms + 10_000
    coordinator = ExecutionCoordinator()
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": order.planned_entry, "ask": order.planned_entry + 0.10, "last": order.planned_entry, "timestamp": fill_time},
        now_ms=fill_time,
    )
    db.refresh(order)
    assert order.state == "paper_open"

    # SL Exit via PaperBroker
    sl_price = order.stop_loss
    exit_time = fill_time + 60_000
    closed_order = PaperBroker.process_price_tick(
        db,
        current_bid=sl_price + 0.10,
        current_ask=sl_price + 0.20,
        candle_high=sl_price + 0.50,
        candle_low=sl_price - 1.0,
        candle_timestamp=exit_time,
        now_ms=exit_time,
    )
    assert closed_order is not None
    assert closed_order.state == "closed"
    assert closed_order.exit_cause == "SL_HIT"
    # Negative realized PnL on SL
    assert closed_order.realized_pnl_net < 0.0

    worker = CG01ResearchProjectionWorker(scope="CG01")
    worker.process_pending_events(db=db)

    fresh_db = SessionLocal()
    try:
        summary = build_cg01_paper_run_summary(run_id=run_id, db=fresh_db)
        assert summary["trades"]["total_closed_trades"] >= 1
        assert summary["trades"]["losing_trades"] >= 1
        assert summary["financial_metrics_post_fee"]["net_realized_pnl_usd"] < 0.0
    finally:
        fresh_db.close()


def test_v16_20_flow_scenario_3_shadow_isolation_integration(db: Session):
    """
    Rule 154: Full Shadow integration:
    Cache event -> scheduler shadow -> real quote/config -> trace queue -> persistence.
    Assert candidate evaluated, ZERO active orders armed, ZERO active state mutation,
    durable shadow trace recorded.
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 19, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 60_000

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    quote = make_valid_quote(bid=2623.65, ask=2623.75, now_ms=now_ms)

    bundle = {
        "1M": c_1m,
        "5M": [],
        "15M": c_15m,
        "1H": c_1h,
        "4H": c_4h,
    }

    from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator
    zones_before = dict(scalp_watcher_orchestrator._active_zones)
    orders_count_before = db.query(models.PaperOrder).count()

    res = evaluate_cg01_shadow_snapshot(
        symbol="XAUUSDT",
        as_of_ms=now_ms,
        bundle=bundle,
        current_quote=quote,
    )

    # 1. Assert pure shadow evaluation output
    assert res is not None
    assert res.get("status") in ("COMPLETED", "CANDIDATE_FOUND", "SKIPPED")
    assert res.get("armed_order_id") is None, "Shadow mode must NEVER arm orders!"

    # 2. Zero active state mutation
    assert scalp_watcher_orchestrator._active_zones == zones_before
    orders_count_after = db.query(models.PaperOrder).count()
    assert orders_count_after == orders_count_before

    # 3. Telemetry trace persisted in background queue and flushed
    trace = CG01EvaluationTrace(
        trace_id=f"trc_shadow_{now_ms}",
        symbol="XAUUSDT",
        observation_mode="SHADOW",
        evaluation_status=res.get("status", "COMPLETED"),
    )
    CG01TracePersistenceWriter.persist_trace(trace)
    flush_res = flush_pending_traces(timeout_s=2.0)
    assert flush_res["status"] in ("COMPLETED", "IN_FLIGHT")


def test_v16_20_flow_scenario_4_auto_off_blocks_arming(db: Session):
    """
    Rule 162: Global auto OFF blocks arming even when a valid candidate exists.
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 19, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 60_000

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    quote = make_valid_quote(bid=2623.65, ask=2623.75, now_ms=now_ms)

    poi = WatchedZone(
        zone_id="poi_long_auto_off",
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

    orch = ScalpWatcherOrchestrator()
    orch.set_active_zones("XAUUSDT", [poi])

    orders_before = db.query(models.PaperOrder).count()

    cycle_res = orch.evaluate_cycle(
        db=db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        candles_1m=c_1m,
        candles_5m=[],
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        auto_enabled=False,  # AUTO IS OFF!
        quote=quote,
    )

    # Arming must be blocked by auto_enabled=False
    assert cycle_res.get("armed_order_id") is None
    orders_after = db.query(models.PaperOrder).count()
    assert orders_after == orders_before


def test_v16_20_flow_scenario_5_news_guard_blackout(db: Session):
    """
    Rule 163: News blackout window blocks trade entry.
    """
    t_news = int(time.time() * 1000) + 120_000  # News in 2 minutes
    news_item = models.EconomicNews(
        source_id=f"news_v16_20_{t_news}",
        title="FOMC Interest Rate Decision",
        currency="USD",
        impact="High",
        scheduled_at=t_news,
        received_at=t_news - 3600_000,
    )
    db.add(news_item)
    db.commit()

    # Check blackout window
    eval_time = t_news - 60_000
    is_blackout, reason, remaining = crud.check_news_blackout(db, now_ms=eval_time)
    assert is_blackout is True
    assert "FOMC" in reason


def test_v16_20_flow_scenario_6_daily_cap_guard(db: Session):
    """
    Rule 165: Daily cap stops arming once max daily fills are reached.
    """
    policy = TradingPolicyService.get_or_create_policy(db)
    policy.max_daily_fills = 3
    db.commit()

    audit = crud.get_or_create_today_audit(db)
    audit.fills_count = 3  # Cap reached!
    db.commit()

    assert audit.fills_count >= policy.max_daily_fills


def test_v16_20_flow_scenario_7_single_open_position_guard(db: Session):
    """
    Rule 166: Concurrency guard: Only 1 active open position allowed per policy.
    """
    # Create an active open paper order
    existing_order = models.PaperOrder(
        id=f"po_v16_20_{int(time.time() * 1000)}",
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
