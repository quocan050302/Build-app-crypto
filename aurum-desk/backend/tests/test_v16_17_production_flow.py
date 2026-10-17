"""
backend/tests/test_v16_17_production_flow.py
V16.17 Section R End-to-End Production Flow & Resilience Tests.

Covers:
- Scenario 1: Full Long TP end-to-end production flow (ARMED -> FILLED -> CLOSED TP_HIT).
- Scenario 2: Full Short SL end-to-end production flow (ARMED -> FILLED -> CLOSED SL_HIT).
- Scenario 3: Global Auto OFF safety (zero arming, zero orders created, no fake notifications).
- Scenario 4: Research persistence failure resilience on exit/close (Defect C verification + projection worker recovery).
- Scenario 5: Missed opportunity audit attribution for un-armed opportunities (Defect F verification).
- Scenario 6: Chart evidence binding and projection stability.
"""
import time
import pytest
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo
from database import SessionLocal
import models
import crud
from services.cg01_types import CG01ResearchContext
from services.scalp_m1_types import M1ScalpConfig, OpportunityEvaluation, WatchedZone, OpportunityStage
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.trade_lifecycle_service import TradeLifecycleService
from domain_calculator import calculate_risk_reward
from services.trading_policy_service import TradingPolicyService
from tests.test_v16_3_cg01_pipeline import build_long_fixture_series, build_short_fixture_series, make_valid_quote
from services.paper_research_run_service import (
    start_observation_run,
    build_cg01_paper_run_summary,
    associate_order_with_research_run,
    record_committed_order_milestone,
)
from services.cg01_projection_worker import cg01_projection_worker
from services.cg01_missed_audit_service import CG01MissedOpportunityAuditService
from services.cg01_evidence_adapter import CG01EvidenceAdapter
from services.opportunity_funnel_service import OpportunityFunnelService


@pytest.fixture
def db_session():
    db = SessionLocal()
    yield db
    db.close()


def test_scenario_1_long_tp_end_to_end(db_session):
    """
    Scenario 1: Long TP end-to-end production flow.
    ARMED -> FILLED -> CLOSED with TP_HIT.
    All 3 milestones committed in SQLite, positive net realized PnL, truthful summary.
    """
    policy = TradingPolicyService.get_or_create_policy(db_session)
    policy.max_daily_fills = 5
    policy.daily_min_goal = 2
    policy.ny_min_fills = 1
    policy.is_active = False
    audit = crud.get_or_create_today_audit(db_session)
    audit.cooldown_until = None
    audit.fills_count = 0
    db_session.query(models.PaperOrder).filter(models.PaperOrder.state.in_(["armed", "placed", "open", "paper_open"])).delete()
    db_session.commit()

    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = 1792050000000

    run_id = f"run_sc1_long_{t_start}"
    start_observation_run(db=db_session, run_id=run_id, symbol="XAUUSDT")
    db_session.commit()

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
        daily_max_cap=5,
    )

    research_ctx = CG01ResearchContext(
        trace_id=f"trc_sc1_{now_ms}",
        run_id=run_id,
        symbol="XAUUSDT",
        observation_mode="ACTIVE",
    )

    orchestrator = ScalpWatcherOrchestrator()
    orchestrator.set_active_zones("XAUUSDT", [poi])

    res = orchestrator.evaluate_cycle(
        db_session,
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
        research_context=research_ctx,
    )

    assert res.get("armed_order_id") is not None, f"Cycle did not arm: {res}"
    order = db_session.query(models.PaperOrder).filter(models.PaperOrder.id == res["armed_order_id"]).first()
    assert order is not None, "Order should be created and armed"
    assert order.state == "armed"
    assert order.take_profit > order.planned_entry > order.stop_loss

    # Verify link created
    link = db_session.query(models.PaperResearchOrderLink).filter(models.PaperResearchOrderLink.order_id == order.id).first()
    assert link is not None
    assert link.run_id == run_id

    # Verify ARMED milestone committed in SQLite
    armed_ms = db_session.query(models.PaperResearchMilestone).filter(
        models.PaperResearchMilestone.run_id == run_id,
        models.PaperResearchMilestone.order_id == order.id,
        models.PaperResearchMilestone.milestone == "ARMED",
    ).first()
    assert armed_ms is not None

    # Step 2: Execute Fill
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
        db_session,
        order,
        fill_price=order.planned_entry,
        calc_result=calc,
        source="AUTO",
        now_ms=fill_time,
    )
    assert filled_order is not None
    assert filled_order.state == "paper_open"

    filled_ms = db_session.query(models.PaperResearchMilestone).filter(
        models.PaperResearchMilestone.run_id == run_id,
        models.PaperResearchMilestone.order_id == order.id,
        models.PaperResearchMilestone.milestone == "FILLED",
    ).first()
    assert filled_ms is not None

    # Step 3: Execute Close via TP
    exit_time = fill_time + 120_000
    closed_order = TradeLifecycleService.execute_close(
        db_session,
        order_id=filled_order.id,
        exit_price=filled_order.take_profit,
        exit_cause="TP_HIT",
        now_ms=exit_time,
    )
    assert closed_order is not None
    assert closed_order.state == "closed"
    assert closed_order.realized_pnl_net > 0.0

    closed_ms = db_session.query(models.PaperResearchMilestone).filter(
        models.PaperResearchMilestone.run_id == run_id,
        models.PaperResearchMilestone.order_id == order.id,
        models.PaperResearchMilestone.milestone == "CLOSED",
    ).first()
    assert closed_ms is not None

    # Step 4: Verify summary
    summary = build_cg01_paper_run_summary(run_id=run_id, db=db_session)
    assert summary["trades"]["total_closed_trades"] == 1
    assert summary["trades"]["winning_trades"] == 1
    assert summary["trades"]["losing_trades"] == 0
    assert summary["trades"]["winrate_pct"] == 100.0
    assert summary["financial_metrics_post_fee"]["net_realized_pnl_usd"] > 0.0


def test_scenario_2_short_sl_end_to_end(db_session):
    """
    Scenario 2: Short SL end-to-end production flow.
    ARMED -> FILLED -> CLOSED with SL_HIT.
    All 3 milestones committed in SQLite, negative net realized PnL, truthful summary.
    """
    policy = TradingPolicyService.get_or_create_policy(db_session)
    policy.max_daily_fills = 5
    policy.daily_min_goal = 2
    policy.ny_min_fills = 1
    policy.is_active = False
    audit = crud.get_or_create_today_audit(db_session)
    audit.cooldown_until = None
    audit.fills_count = 0
    db_session.query(models.PaperOrder).filter(models.PaperOrder.state.in_(["armed", "placed", "open", "paper_open"])).delete()
    db_session.commit()

    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = 1792050000000

    run_id = f"run_sc2_short_{t_start}"
    start_observation_run(db=db_session, run_id=run_id, symbol="XAUUSDT")
    db_session.commit()

    c_4h, c_1h, c_15m, c_1m = build_short_fixture_series(t_start)

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

    quote = make_valid_quote(2649.5, 2649.7, now_ms)
    cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
        daily_max_cap=5,
    )

    research_ctx = CG01ResearchContext(
        trace_id=f"trc_sc2_{now_ms}",
        run_id=run_id,
        symbol="XAUUSDT",
        observation_mode="ACTIVE",
    )

    orchestrator = ScalpWatcherOrchestrator()
    orchestrator.set_active_zones("XAUUSDT", [poi])

    res = orchestrator.evaluate_cycle(
        db_session,
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
        research_context=research_ctx,
    )

    assert res.get("armed_order_id") is not None, f"Cycle did not arm: {res}"
    order = db_session.query(models.PaperOrder).filter(models.PaperOrder.id == res["armed_order_id"]).first()
    assert order is not None
    assert order.state == "armed"
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
        db_session,
        order,
        fill_price=order.planned_entry,
        calc_result=calc,
        source="AUTO",
        now_ms=fill_time,
    )
    assert filled_order is not None

    # Execute Close via SL
    exit_time = fill_time + 90_000
    closed_order = TradeLifecycleService.execute_close(
        db_session,
        order_id=filled_order.id,
        exit_price=filled_order.stop_loss,
        exit_cause="SL_HIT",
        now_ms=exit_time,
    )
    assert closed_order is not None
    assert closed_order.state == "closed"
    assert closed_order.realized_pnl_net < 0.0

    # Verify all 3 milestones exist
    milestones = db_session.query(models.PaperResearchMilestone).filter(
        models.PaperResearchMilestone.run_id == run_id,
        models.PaperResearchMilestone.order_id == order.id,
    ).all()
    ms_types = {m.milestone for m in milestones}
    assert {"ARMED", "FILLED", "CLOSED"}.issubset(ms_types)

    summary = build_cg01_paper_run_summary(run_id=run_id, db=db_session)
    assert summary["trades"]["total_closed_trades"] == 1
    assert summary["trades"]["losing_trades"] == 1
    assert summary["trades"]["winning_trades"] == 0
    assert summary["trades"]["winrate_pct"] == 0.0
    assert summary["financial_metrics_post_fee"]["net_realized_pnl_usd"] < 0.0


def test_scenario_3_global_auto_off_safety(db_session):
    """
    Scenario 3: Global Auto OFF safety.
    When auto_enabled=False (the default):
    - Candidate evaluated cleanly.
    - Zero orders created or armed in SQLite.
    - Zero trade notifications emitted.
    - Opportunity Funnel records transition cleanly without crashing.
    """
    policy = TradingPolicyService.get_or_create_policy(db_session)
    policy.is_active = False
    db_session.commit()

    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = 1792050000000

    run_id = f"run_sc3_auto_off_{t_start}"
    start_observation_run(db=db_session, run_id=run_id, symbol="XAUUSDT")
    db_session.commit()

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    quote = make_valid_quote(2625.5, 2625.7, now_ms)

    poi = WatchedZone(
        zone_id="poi_long_sc3_15m",
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

    orders_count_before = db_session.query(models.PaperOrder).count()

    orchestrator = ScalpWatcherOrchestrator()
    orchestrator.set_active_zones("XAUUSDT", [poi])

    # Explicitly auto_enabled=False
    res = orchestrator.evaluate_cycle(
        db_session,
        symbol="XAUUSDT",
        now_ms=now_ms,
        quote=quote,
        candles_1m=c_1m,
        candles_5m=[],
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        config=cfg,
        auto_enabled=False,
    )

    orders_count_after = db_session.query(models.PaperOrder).count()
    assert orders_count_after == orders_count_before, "No paper order must be created when auto is OFF"

    # Verify no armed order milestones were recorded
    milestone_count = db_session.query(models.PaperResearchMilestone).filter(
        models.PaperResearchMilestone.run_id == run_id
    ).count()
    assert milestone_count == 0


def test_scenario_4_research_persistence_error_resilience(db_session):
    """
    Scenario 4: Defect C verification & resilience.
    If writing research milestone raises an unhandled exception or flush error during execute_close:
    1. The core trade close transaction STILL commits successfully (not rolled back).
    2. Order transitions to CLOSED and realized PnL is computed.
    3. Asynchronous CG01ResearchProjectionWorker reads the committed trade.closed domain event
       and recovers the CLOSED milestone reliably.
    """
    now_ms = int(time.time() * 1000)
    run_id = f"run_sc4_resilience_{now_ms}"
    start_observation_run(db=db_session, run_id=run_id, symbol="XAUUSDT")
    db_session.commit()

    # Create an active filled order
    import uuid
    order = models.PaperOrder(
        id=str(uuid.uuid4()),
        instrument="XAUUSDT",
        state="paper_open",
        direction="LONG",
        planned_entry=2650.0,
        actual_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        quantity=0.1,
        initial_risk_usdt=0.5,
        created_at=now_ms - 60_000,
        opened_at=now_ms - 30_000,
        origin="AUTO",
    )
    db_session.add(order)
    db_session.flush()

    associate_order_with_research_run(
        db=db_session,
        run_id=run_id,
        order_id=order.id,
        opportunity_id=f"opp_sc4_{now_ms}",
        now_ms=now_ms,
    )
    record_committed_order_milestone(
        db=db_session,
        run_id=run_id,
        order_id=order.id,
        milestone="ARMED",
        timestamp_ms=now_ms - 60_000,
    )
    record_committed_order_milestone(
        db=db_session,
        run_id=run_id,
        order_id=order.id,
        milestone="FILLED",
        timestamp_ms=now_ms - 30_000,
    )
    db_session.commit()

    # Now simulate an intentional failure during in-band research milestone write inside execute_close
    with patch(
        "services.paper_research_run_service.record_committed_order_milestone",
        side_effect=RuntimeError("Simulated research storage fault during close!"),
    ):
        closed_order = TradeLifecycleService.execute_close(
            db_session,
            order_id=order.id,
            exit_price=2660.0,
            exit_cause="TP_HIT",
            now_ms=now_ms,
        )

    # 1. Assert order was closed despite research failure
    assert closed_order is not None, "execute_close must not return None on research failure"
    assert closed_order.state == "closed", "Order state must be successfully updated to closed"
    assert closed_order.realized_pnl_net > 0.0

    # 2. Check that domain event was committed
    ev = db_session.query(models.DomainEvent).filter(
        models.DomainEvent.event_type == "trade.closed",
        models.DomainEvent.aggregate_id == order.id,
    ).first()
    assert ev is not None, "trade.closed domain event must have been committed"

    # 3. Trigger asynchronous projection worker to recover missing CLOSED milestone
    proj_res = cg01_projection_worker.process_pending_events(db=db_session)
    assert proj_res["status"] == "OK"

    # 4. Verify CLOSED milestone is now recovered
    closed_ms = db_session.query(models.PaperResearchMilestone).filter(
        models.PaperResearchMilestone.run_id == run_id,
        models.PaperResearchMilestone.order_id == order.id,
        models.PaperResearchMilestone.milestone == "CLOSED",
    ).first()
    assert closed_ms is not None, "Projection worker must recover missing CLOSED milestone from domain event"


def test_scenario_5_missed_opportunity_audit(db_session):
    """
    Scenario 5: Defect F verification.
    Missed opportunities that never had an armed PaperOrder (blocked by guard, risk cap, auto off)
    must still be discovered and audited when tagged with run_id.
    """
    now_ms = int(time.time() * 1000)
    run_id = f"run_sc5_audit_{now_ms}"
    start_observation_run(db=db_session, run_id=run_id, symbol="XAUUSDT")
    db_session.commit()

    # Record 3 un-armed opportunities tagged with run_id
    OpportunityFunnelService.record_opportunity_transition(
        db_session,
        OpportunityEvaluation(
            opportunity_id=f"opp_blocked_{now_ms}_1",
            symbol="XAUUSDT",
            run_id=run_id,
            stage=OpportunityStage.GUARD_BLOCKED.value,
            reason_codes=("SPREAD_EXCEEDED",),
            as_of_ms=now_ms,
        ),
        now_ms=now_ms,
    )
    OpportunityFunnelService.record_opportunity_transition(
        db_session,
        OpportunityEvaluation(
            opportunity_id=f"opp_blocked_{now_ms}_2",
            symbol="XAUUSDT",
            run_id=run_id,
            stage=OpportunityStage.GUARD_BLOCKED.value,
            reason_codes=("DAILY_CAP_REACHED",),
            as_of_ms=now_ms + 10,
        ),
        now_ms=now_ms + 10,
    )
    OpportunityFunnelService.record_opportunity_transition(
        db_session,
        OpportunityEvaluation(
            opportunity_id=f"opp_detected_{now_ms}_3",
            symbol="XAUUSDT",
            run_id=run_id,
            stage=OpportunityStage.ZONE_DETECTED.value,
            as_of_ms=now_ms + 20,
        ),
        now_ms=now_ms + 20,
    )
    db_session.commit()

    audit = CG01MissedOpportunityAuditService.audit_run(run_id=run_id, db=db_session)
    assert audit["total_evaluated_count"] >= 3
    assert audit["missed_opportunities_count"] >= 2
    assert "SPREAD_EXCEEDED" in audit["top_bottleneck_reasons"]
    assert "DAILY_CAP_REACHED" in audit["top_bottleneck_reasons"]


def test_scenario_6_chart_evidence_binding(db_session):
    """
    Scenario 6: Chart evidence binding stability.
    Verifies CG01EvidenceAdapter builds chart annotations and structural evidence
    from candidate data reliably.
    """
    now_ms = int(time.time() * 1000)
    poi = WatchedZone(
        zone_id="poi_chart_sc6",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BULLISH",
        top=2630.0,
        bottom=2625.0,
        created_at=now_ms - 3600_000,
        known_at=now_ms - 1800_000,
    )

    evidence = CG01EvidenceAdapter.build_chart_evidence(
        zone=poi,
        direction="BULLISH",
        entry_price=2627.5,
        stop_loss=2624.0,
        take_profit=2635.0,
        trigger_time_ms=now_ms,
        sweep_reference=2624.5,
    )

    assert evidence is not None
    assert evidence.zone_id == "poi_chart_sc6"
    assert evidence.entry_level == 2627.5
    assert evidence.sl_level == 2624.0
    assert evidence.tp_level == 2635.0
    assert len(evidence.annotations) > 0
