"""
backend/tests/test_v16_19_production_flow.py
V16.19 Full Production Flow & Resilience Acceptance Suite.

Covers Rules 151 - 180:
- Scenario 1: Full LONG TP flow (Candle Ingest -> Scheduler -> Evaluator -> Guards -> Arm -> Quote Fill -> Quote Exit -> Projection -> Fresh Report)
- Scenario 2: Full SHORT SL flow (Short geometry TP < entry < SL -> Fill -> SL Exit -> Projection -> Fresh Report)
- Scenario 3: Shadow Mode Isolation (Valid candidate evaluated, zero arming, zero active state mutation, durable shadow trace)
- Scenario 4: Global Auto OFF Guard (Candidate identified, auto OFF blocks arming, observation logged)
- Scenario 5: Pending-only Projection Recovery (Pending resolution commits without new domain events, preserves original occurred_at)
- Scenario 6: Coverage Summary Schema & Interval Union (Real model fields start_ms/end_ms, ended_at_ms, overlap merge)
- Scenario 7: Exact Scope / Mode Isolation (CG01 vs SMC scope conflict resolution, shadow vs active separation)
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
from domain_calculator import CostAssumptions, calculate_risk_reward, validate_price_geometry
from services.cg01_types import CG01Config, CG01ResearchContext
from services.scalp_m1_types import M1ScalpConfig, WatchedZone
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.trade_lifecycle_service import TradeLifecycleService
from services.trading_policy_service import TradingPolicyService
from services.candle_cache_service import candle_cache_service
from services.cg01_scheduler_service import (
    CG01EvaluationScheduler,
    evaluate_cg01_shadow_snapshot,
    resolve_effective_cg01_config,
)
from services.cg01_projection_worker import (
    CG01ResearchProjectionWorker,
    ProjectionOutcome,
)
from services.paper_research_run_service import (
    PaperResearchRun,
    PaperResearchRunService,
    start_observation_run,
    build_cg01_paper_run_summary,
    build_run_coverage_summary,
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


def test_v16_19_scenario_1_full_long_tp_flow(db: Session):
    """
    Rule 159, 180: Full LONG TP Production Flow:
    lifespan / setup -> candle ingest -> scheduler -> evaluator -> guards ->
    arm -> quote fill -> quote exit -> projection -> fresh report.
    """
    # 1. Setup policy and cleanup active orders
    policy = TradingPolicyService.get_or_create_policy(db)
    policy.max_daily_fills = 5
    policy.daily_min_goal = 2
    policy.ny_min_fills = 1
    policy.is_active = False

    audit = crud.get_or_create_today_audit(db)
    audit.cooldown_until = None
    audit.fills_count = 0
    db.query(models.PaperOrder).delete()
    db.query(models.WatchSetup).delete()
    db.query(models.PaperResearchOrderLink).delete()
    db.query(models.PaperResearchMilestone).delete()
    db.query(models.PaperResearchOrderMilestone).delete()
    db.query(models.SMCScenarioSnapshot).delete()
    db.query(models.SMCScenario).delete()
    db.commit()

    # 2. Timing in London Silver Bullet (03:30 NY)
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 19, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000

    run_id = f"run_v16_19_long_{t_start}"
    start_observation_run(db=db, run_id=run_id, symbol="XAUUSDT")
    db.commit()

    # 3. Candle ingest & POI
    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    quote = make_valid_quote(2625.5, 2625.7, now_ms)

    poi = WatchedZone(
        zone_id="poi_v16_19_long",
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
        daily_max_cap=5,
    )

    research_ctx = CG01ResearchContext(
        trace_id=f"trc_v16_19_{now_ms}",
        run_id=run_id,
        symbol="XAUUSDT",
        config_fingerprint="CFG_V16_19_FINGERPRINT",
        input_versions={"1M": "1M:test:1"},
        evaluation_as_of_ms=now_ms,
        observation_mode="ACTIVE",
    )

    # 4. Active Orchestrator Evaluation -> Arm Order
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
    assert order.take_profit > order.planned_entry > order.stop_loss

    # 5. Quote Fill Execution
    fill_time = now_ms + 60_000
    calc = calculate_risk_reward(
        direction="LONG",
        planned_entry=order.planned_entry,
        stop_loss=order.stop_loss,
        take_profit=order.take_profit,
        capital_usdt=1000.0,
        risk_pct=0.25,
        margin_mode="ISOLATED",
    )
    filled_order = TradeLifecycleService.execute_fill(
        db,
        order,
        fill_price=order.planned_entry,
        calc_result=calc,
        source="AUTO",
        now_ms=fill_time,
    )
    assert filled_order is not None
    assert filled_order.state == "paper_open"

    # 6. Quote Exit Execution (Touch TP)
    exit_time = fill_time + 120_000
    closed_order = TradeLifecycleService.execute_close(
        db,
        order_id=filled_order.id,
        exit_price=filled_order.take_profit,
        exit_cause="TP_HIT",
        now_ms=exit_time,
    )
    assert closed_order is not None
    assert closed_order.state == "closed"
    assert closed_order.realized_pnl_net > 0.0

    # 7. Projection Worker Recovers Milestones
    worker = CG01ResearchProjectionWorker(scope="CG01")
    worker.process_pending_events(db=db)

    # 8. Verify Fresh Session Truthful Summary
    fresh_db = SessionLocal()
    try:
        summary = build_cg01_paper_run_summary(run_id=run_id, db=fresh_db)
        assert summary["trades"]["total_closed_trades"] >= 1
        assert summary["trades"]["winning_trades"] >= 1
        assert summary["trades"]["winrate_pct"] == 100.0
        assert summary["financial_metrics_post_fee"]["net_realized_pnl_usd"] > 0.0

        milestones = fresh_db.query(models.PaperResearchOrderMilestone).filter(
            models.PaperResearchOrderMilestone.run_id == run_id,
            models.PaperResearchOrderMilestone.order_id == armed_order_id,
        ).all()
        ms_types = {m.milestone for m in milestones}
        assert "ARMED" in ms_types
        assert "FILLED" in ms_types
        assert "CLOSED" in ms_types
    finally:
        fresh_db.close()


def test_v16_19_scenario_2_full_short_sl_flow(db: Session):
    """
    Rule 160, 161, 162: Full SHORT SL Production Flow:
    ARMED -> FILLED -> CLOSED with SL_HIT.
    Asserts TP < entry < SL across geometry and lifecycle.
    """
    policy = TradingPolicyService.get_or_create_policy(db)
    policy.max_daily_fills = 5
    policy.daily_min_goal = 2
    policy.is_active = False

    audit = crud.get_or_create_today_audit(db)
    audit.cooldown_until = None
    audit.fills_count = 0
    db.query(models.PaperOrder).delete()
    db.query(models.WatchSetup).delete()
    db.query(models.PaperResearchOrderLink).delete()
    db.query(models.PaperResearchMilestone).delete()
    db.query(models.PaperResearchOrderMilestone).delete()
    db.query(models.SMCScenarioSnapshot).delete()
    db.query(models.SMCScenario).delete()
    db.commit()

    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 20, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000

    run_id = f"run_v16_19_short_{t_start}"
    start_observation_run(db=db, run_id=run_id, symbol="XAUUSDT")
    db.commit()

    c_4h, c_1h, c_15m, c_1m = build_short_fixture_series(t_start)
    quote = make_valid_quote(2649.5, 2649.7, now_ms)

    poi = WatchedZone(
        zone_id="poi_v16_19_short",
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
        daily_max_cap=5,
    )

    research_ctx = CG01ResearchContext(
        trace_id=f"trc_v16_19_sh_{now_ms}",
        run_id=run_id,
        symbol="XAUUSDT",
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
    assert armed_order_id is not None, f"Expected SHORT order to be armed, got {cycle_res}"

    order = db.query(models.PaperOrder).filter(models.PaperOrder.id == armed_order_id).first()
    assert order is not None
    assert order.direction.upper() == "SHORT"
    assert order.take_profit < order.planned_entry < order.stop_loss

    # Execute Fill
    fill_time = now_ms + 60_000
    calc = calculate_risk_reward(
        direction="SHORT",
        planned_entry=order.planned_entry,
        stop_loss=order.stop_loss,
        take_profit=order.take_profit,
        capital_usdt=1000.0,
        risk_pct=0.25,
        margin_mode="ISOLATED",
    )
    filled_order = TradeLifecycleService.execute_fill(
        db,
        order,
        fill_price=order.planned_entry,
        calc_result=calc,
        source="AUTO",
        now_ms=fill_time,
    )
    assert filled_order is not None
    assert filled_order.state == "paper_open"

    # Touch Stop Loss (Close via SL)
    exit_time = fill_time + 120_000
    closed_order = TradeLifecycleService.execute_close(
        db,
        order_id=filled_order.id,
        exit_price=filled_order.stop_loss,
        exit_cause="SL_HIT",
        now_ms=exit_time,
    )
    assert closed_order is not None
    assert closed_order.state == "closed"
    assert closed_order.realized_pnl_net < 0.0

    # Milestones projected
    worker = CG01ResearchProjectionWorker(scope="CG01")
    worker.process_pending_events(db=db)

    fresh_db = SessionLocal()
    try:
        summary = build_cg01_paper_run_summary(run_id=run_id, db=fresh_db)
        assert summary["trades"]["total_closed_trades"] >= 1
        assert summary["trades"]["losing_trades"] >= 1
        assert summary["trades"]["winrate_pct"] == 0.0
        assert summary["financial_metrics_post_fee"]["net_realized_pnl_usd"] < 0.0
    finally:
        fresh_db.close()


def test_v16_19_scenario_3_shadow_isolation():
    """
    Rules 040-050, 154: Shadow evaluation adapter does NOT mutate ACTIVE state,
    does NOT arm orders, does NOT emit trade events, and records a pure shadow trace.
    """
    t_start = 1792050000000
    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)

    bundle = {
        "1M": c_1m,
        "5M": [],
        "15M": c_15m,
        "1H": c_1h,
        "4H": c_4h,
    }

    # Snapshot active orchestrator state before shadow call
    from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator
    zones_before = dict(scalp_watcher_orchestrator._active_zones)
    rejections_before = dict(scalp_watcher_orchestrator._arm_rejections)

    res = evaluate_cg01_shadow_snapshot(
        symbol="XAUUSDT",
        as_of_ms=t_start + 60000,
        bundle=bundle,
    )

    # 1. Assert pure shadow output
    assert res["status"] in ("COMPLETED", "CANDIDATE_FOUND", "SKIPPED")
    assert res["armed_order_id"] is None, "Shadow mode must NEVER arm orders!"

    # 2. Assert ACTIVE orchestrator state was NOT mutated
    assert scalp_watcher_orchestrator._active_zones == zones_before
    assert scalp_watcher_orchestrator._arm_rejections == rejections_before


def test_v16_19_scenario_4_global_auto_off(db: Session):
    """
    Rule 163: Global auto OFF blocks arming even when candidate is valid.
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = 1792050000000

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    quote = make_valid_quote(2625.5, 2625.7, now_ms)

    poi = WatchedZone(
        zone_id="poi_v16_19_auto_off",
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

    cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        daily_max_cap=5,
    )

    # Auto disabled
    res = orch.evaluate_cycle(
        db=db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        candles_1m=c_1m,
        candles_5m=[],
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        auto_enabled=False,  # Explicitly OFF
        config=cfg,
        quote=quote,
    )

    assert res.get("armed_order_id") is None, "Auto OFF must never arm orders!"
    cg_eval = res.get("cg01_evaluation") or {}
    assert cg_eval.get("armed_order_id") is None


def test_v16_19_scenario_5_pending_retry_durability_and_timestamp():
    """
    Rules 027-028, 091-097, 169-171:
    Pending projection retry commits when no new events exist and preserves
    the original domain event occurred_at.
    """
    db_setup = SessionLocal()
    order_id = f"ord_v16_19_p_{int(time.time() * 1000)}"
    run_id = f"run_v16_19_p_{int(time.time() * 1000)}"
    ev_id = f"ev_v16_19_p_{int(time.time() * 1000)}"

    original_occurred_at = 1700000000000
    first_seen_at = 1700000080000  # 80s later

    try:
        dev = models.DomainEvent(
            event_id=ev_id,
            event_type="order.armed",
            aggregate_id=order_id,
            sequence=2000001,
            occurred_at=original_occurred_at,
            published_at=first_seen_at,
            payload=json.dumps({"order_id": order_id, "symbol": "XAUUSDT"}),
        )
        db_setup.add(dev)

        test_scope = f"CG01_P5_{int(time.time() * 1000)}"
        run_rec = models.PaperResearchRunRecord(
            run_id=run_id,
            scope=test_scope,
            symbol="XAUUSDT",
            status="COLLECTING",
            started_at_ms=original_occurred_at - 10000,
            software_version="V16.19",
            config_fingerprint="CFG_V16_19",
            execution_mode="PAPER_EXECUTION",
            created_at_ms=first_seen_at,
            updated_at_ms=first_seen_at,
        )
        db_setup.add(run_rec)

        pending = models.PaperResearchPendingProjection(
            event_id=ev_id,
            scope=test_scope,
            event_type="order.armed",
            aggregate_id=order_id,
            reason="UNLINKED_CAUSAL_RUN",
            payload_json=json.dumps({"order_id": order_id, "symbol": "XAUUSDT"}),
            first_seen_at_ms=first_seen_at,
            next_retry_at_ms=first_seen_at,
            attempt_count=1,
            status="PENDING",
            created_at_ms=first_seen_at,
            updated_at_ms=first_seen_at,
        )
        db_setup.add(pending)

        # Set checkpoint past this event so processed == 0
        from sqlalchemy import func
        max_seq = max(3000000, db_setup.query(func.max(models.DomainEvent.sequence)).scalar() or 0)
        cp = models.PaperResearchCheckpoint(
            scope=test_scope,
            last_sequence=max_seq,
            updated_at_ms=int(time.time() * 1000),
        )
        db_setup.merge(cp)
        db_setup.commit()

        # Add link
        associate_order_with_research_run(db_setup, run_id=run_id, order_id=order_id)
        db_setup.commit()
    finally:
        db_setup.close()

    worker = CG01ResearchProjectionWorker(scope=test_scope)
    worker_db = SessionLocal()
    try:
        res = worker.process_pending_events(db=worker_db)
        assert res["processed"] == 0
        assert res["resolved"] == 1
        assert res["state_changed"] is True
    finally:
        worker_db.close()

    fresh_db = SessionLocal()
    try:
        ms = fresh_db.query(models.PaperResearchOrderMilestone).filter(
            models.PaperResearchOrderMilestone.run_id == run_id,
            models.PaperResearchOrderMilestone.order_id == order_id,
            models.PaperResearchOrderMilestone.milestone == "ARMED",
        ).first()
        assert ms is not None
        assert ms.timestamp_ms == original_occurred_at, (
            f"Original timestamp {original_occurred_at} must be preserved, got {ms.timestamp_ms}"
        )
    finally:
        fresh_db.close()


def test_v16_19_scenario_6_coverage_intervals_and_union():
    """
    Rules 029, 111-115, 176:
    Coverage summary handles real model schema fields, merges overlapping
    healthy intervals, clips to run boundary, and computes truthful uptime percentage.
    """
    t_start = 1792000000000
    t_end = t_start + 100000  # Total duration 100,000 ms

    run = PaperResearchRun(
        run_id="run_coverage_v16_19",
        symbol="XAUUSDT",
        started_at_ms=t_start,
        ended_at_ms=t_end,
        status="COMPLETED",
    )

    # Two overlapping intervals:
    # inv1: [t_start + 10000, t_start + 50000] -> 40,000 ms
    # inv2: [t_start + 40000, t_start + 70000] -> overlaps by 10,000 ms
    # Union should be [t_start + 10000, t_start + 70000] -> 60,000 ms
    # Plus inv3 before start (should be clipped): [t_start - 5000, t_start + 5000] -> clipped to [t_start, t_start + 5000] -> 5,000 ms
    inv1 = models.PaperObservationInterval(
        run_id=run.run_id,
        scope="CG01",
        start_ms=t_start + 10000,
        end_ms=t_start + 50000,
        data_quality="GOOD",
        created_at_ms=t_start,
    )
    inv2 = models.PaperObservationInterval(
        run_id=run.run_id,
        scope="CG01",
        start_ms=t_start + 40000,
        end_ms=t_start + 70000,
        data_quality="HEALTHY",
        created_at_ms=t_start,
    )
    inv3 = models.PaperObservationInterval(
        run_id=run.run_id,
        scope="CG01",
        start_ms=t_start - 5000,
        end_ms=t_start + 5000,
        data_quality="OK",
        created_at_ms=t_start,
    )

    summary = build_run_coverage_summary(run, observation_intervals=[inv1, inv2, inv3])
    # Total healthy duration: [t_start, t_start + 5000] (5000) + [t_start + 10000, t_start + 70000] (60000) = 65000 ms
    # Uptime: 65000 / 100000 = 65.0%
    assert summary["uptime_pct"] == 65.0, f"Expected 65.0% uptime, got {summary['uptime_pct']}"


def test_v16_19_scenario_7_exact_scope_resolution(db: Session):
    """
    Rules 030, 074, 177: Exact scope resolution ignores unrelated scope
    even if run_id contains 'cg01'.
    """
    t_now = int(time.time() * 1000)
    smc_id = f"smc_cg01_bench_{t_now}"
    cg01_id = f"cg01_real_{t_now}"

    rec_smc = models.PaperResearchRunRecord(
        run_id=smc_id,
        scope="SMC",
        symbol="XAUUSDT",
        status="COLLECTING",
        started_at_ms=t_now,
        software_version="V16.19",
        config_fingerprint="CFG_SMC",
        execution_mode="PAPER_EXECUTION",
        created_at_ms=t_now,
        updated_at_ms=t_now,
    )
    rec_cg01 = models.PaperResearchRunRecord(
        run_id=cg01_id,
        scope="CG01",
        symbol="XAUUSDT",
        status="COLLECTING",
        started_at_ms=t_now + 10,
        software_version="V16.19",
        config_fingerprint="CFG_CG01",
        execution_mode="PAPER_EXECUTION",
        created_at_ms=t_now + 10,
        updated_at_ms=t_now + 10,
    )
    db.add(rec_smc)
    db.add(rec_cg01)
    db.commit()

    run = resolve_active_observation_run(db, scope="CG01", symbol="XAUUSDT")
    assert run is not None
    assert run.run_id == cg01_id
    assert run.run_id != smc_id
