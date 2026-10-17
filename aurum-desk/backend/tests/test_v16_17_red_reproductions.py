"""
backend/tests/test_v16_17_red_reproductions.py
Mandatory RED reproduction tests for V16.17 defects:
- Defect A (022): Scheduler post-cycle research metadata left uncommitted before session close.
- Defect B (021): ARMED milestone queried before order link is created, resulting in 0 ARMED milestones.
- Defect C (024, 025): Research flush failures inside execute_fill / execute_close.
- Defect D (026): cg01_trace_recorder lacks get_recent_traces(), causing build_cg01_paper_run_summary to see 0 traces.
- Defect E (027): Trace latency lost after memory buffer clear / service restart.
- Defect F (028): Missed opportunity audit fails to find blocked/pending opportunities that have no orders.
- Defect G (029): Run ID resolution divergence between runtime defaults and reports.
"""
import pytest
import time
import json
from database import SessionLocal
import models
from services.cg01_telemetry_service import (
    cg01_trace_recorder,
    CG01EvaluationTrace,
)
from services.paper_research_run_service import (
    PaperResearchRunService,
    build_cg01_paper_run_summary,
    start_observation_run,
    record_committed_order_milestone,
)
from services.cg01_missed_audit_service import CG01MissedOpportunityAuditService
from services.scalp_m1_types import OpportunityEvaluation, OpportunityStage
from services.scalp_m1_model_service import ScalpCandidate
from services.opportunity_funnel_service import OpportunityFunnelService


@pytest.fixture
def test_db():
    db = SessionLocal()
    yield db
    db.close()


def test_red_026_coverage_fails_due_to_missing_get_recent_traces(test_db):
    """
    Defect D (Items 026, 121):
    Paper research summary attempts to call cg01_trace_recorder.get_recent_traces(limit=500).
    On baseline, CG01TraceRecorder only implements get_traces(), NOT get_recent_traces().
    Therefore hasattr(..., 'get_recent_traces') evaluates to False,
    silently defaulting to [] and losing all trace coverage!
    """
    run_id = f"run_cov_red_{int(time.time() * 1000)}"
    start_observation_run(db=test_db, run_id=run_id, symbol="XAUUSDT")
    test_db.commit()

    # Record a valid trace into the ring buffer
    trace = CG01EvaluationTrace(
        trace_id=f"trc_red_{int(time.time() * 1000)}",
        symbol="XAUUSDT",
        run_id=run_id,
        event_received_at=int(time.time() * 1000),
        evaluation_started_at=int(time.time() * 1000),
        evaluation_completed_at=int(time.time() * 1000) + 10,
        total_runtime_ms=10.0,
    )
    cg01_trace_recorder.record_trace(trace)

    # 1. Assert get_recent_traces method existence
    assert hasattr(cg01_trace_recorder, "get_recent_traces"), (
        "RED: CG01TraceRecorder is missing get_recent_traces()!"
    )


def test_red_027_traces_lost_after_memory_buffer_cleared(test_db):
    """
    Defect E (Items 027, 122, 123):
    Traces are only kept in the in-memory ring buffer.
    When service restarts (simulated by clearing buffer or fresh instance),
    all trace history for run_id is lost because there is no persistent trace storage.
    """
    run_id = f"run_restart_red_{int(time.time() * 1000)}"
    start_observation_run(db=test_db, run_id=run_id, symbol="XAUUSDT")
    test_db.commit()

    trace = CG01EvaluationTrace(
        trace_id=f"trc_pers_{int(time.time() * 1000)}",
        symbol="XAUUSDT",
        run_id=run_id,
        evaluation_started_at=int(time.time() * 1000),
        evaluation_completed_at=int(time.time() * 1000) + 15,
        total_runtime_ms=15.0,
    )
    cg01_trace_recorder.record_trace(trace)

    # Simulate restart by clearing in-memory buffer
    with cg01_trace_recorder._lock:
        cg01_trace_recorder._buffer.clear()

    # On baseline, traces are completely gone because there's no durable DB storage
    traces = cg01_trace_recorder.get_traces(run_id=run_id)
    assert len(traces) > 0, "RED: Trace history was completely lost after in-memory ring buffer cleared!"


def test_red_028_audit_misses_opportunity_without_order(test_db):
    """
    Defect F (Items 028, 131, 132):
    An opportunity is evaluated and blocked by news/policy/auto guard (no order created).
    The opportunity belongs to research run_id.
    Currently, audit_run queries models.OpportunityFunnelRecord.session_id == run_id
    and only associates through PaperResearchOrderLink.
    Because no order was created, audit_run completely misses the opportunity!
    """
    run_id = f"run_opp_red_{int(time.time() * 1000)}"
    start_observation_run(db=test_db, run_id=run_id, symbol="XAUUSDT")
    test_db.commit()

    opp_key = f"opp_cand_{int(time.time() * 1000)}"
    t_now = int(time.time() * 1000)

    # Opportunity is blocked by global auto guard during the run
    eval_opp = OpportunityEvaluation(
        opportunity_id=opp_key,
        symbol="XAUUSDT",
        model_id="CG01_SWEEP_MSS_FVG",
        direction="LONG",
        as_of_ms=t_now,
        stage="GUARD_BLOCKED",
        reason_codes=("GLOBAL_AUTO_DISABLED",),
        session_id="NY_SESSION",  # Real trading session, NOT run_id!
        market_event_received_at=t_now,
    )
    OpportunityFunnelService.record_opportunity_transition(test_db, eval_opp, now_ms=t_now)
    test_db.commit()

    # Now audit the run
    audit_res = CG01MissedOpportunityAuditService.audit_run(run_id=run_id, db=test_db)
    assert audit_res["total_unique_opportunities"] > 0, (
        f"RED: audit_run found 0 opportunities for {run_id}, missing blocked opportunity {opp_key}!"
    )


def test_red_021_armed_milestone_not_recorded_during_arm(test_db):
    """
    Defect B (Items 021, 044, 045):
    In scalp_watcher_orchestrator._arm_paper_candidate(), line 1686:
    It queries run_links = db.query(models.PaperResearchOrderLink).filter(order_id == order.id).all()
    to record ARMED milestone.
    BUT the link for the new order is only created in evaluate_cycle() AFTER _arm_paper_candidate returns!
    Therefore, run_links is ALWAYS EMPTY in _arm_paper_candidate, and NO ARMED milestone is ever recorded!
    """
    from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator
    from services.scalp_m1_model_service import ScalpCandidate
    from domain_calculator import calculate_risk_reward
    from services.strategy_service import strategy_service

    from services.trading_policy_service import TradingPolicyService
    import crud
    policy = TradingPolicyService.get_or_create_policy(test_db)
    policy.max_daily_fills = 20
    policy.is_active = False
    audit = crud.get_or_create_today_audit(test_db)
    audit.cooldown_until = None
    audit.fills_count = 0
    test_db.query(models.PaperOrder).filter(models.PaperOrder.state.in_(["armed", "placed", "open", "paper_open"])).delete()

    # Enable auto state for test
    strategy_service.set_auto_state(test_db, True)
    test_db.commit()

    run_id = f"run_arm_red_{int(time.time() * 1000)}"
    start_observation_run(db=test_db, run_id=run_id, symbol="XAUUSDT")
    test_db.commit()

    t_now = int(time.time() * 1000)
    calc = calculate_risk_reward(
        direction="LONG",
        planned_entry=2600.0,
        stop_loss=2590.0,
        take_profit=2630.0,
        capital_usdt=1000.0,
        risk_pct=0.25,
        leverage=20,
        margin_mode="ISOLATED",
    )

    cand = ScalpCandidate(
        is_eligible=True,
        model_id="CG01_SWEEP_MSS_FVG",
        direction="LONG",
        candidate_key=f"cand_arm_{int(time.time() * 1000)}",
        symbol="XAUUSDT",
        planned_entry=2600.0,
        stop_loss=2590.0,
        take_profit=2630.0,
        gross_rr=calc.gross_rr,
        net_rr=calc.net_rr,
        calculation=calc,
        snapshot=None,
        as_of_ms=t_now,
        valid_until_ms=t_now + 3_600_000,
        zone_id="zone_cg01",
        origin_episode_id="ep_cg01",
    )

    quote = {
        "bid": 2600.0,
        "ask": 2600.2,
        "last": 2600.1,
        "timestamp": t_now,
        "is_valid": True,
        "is_stale": False,
    }
    policy_dict = {"capital_usdt": 1000.0, "risk_pct": 0.25, "margin_mode": "ISOLATED"}

    order = scalp_watcher_orchestrator._arm_paper_candidate(
        db=test_db,
        cand=cand,
        now_ms=t_now,
        clock=None,
        quote=quote,
        policy_settings=policy_dict,
        auto_enabled=True,
    )
    assert order is not None
    test_db.commit()

    # Query PaperResearchOrderMilestone for this order in a fresh session
    fresh_db = SessionLocal()
    try:
        ms = fresh_db.query(models.PaperResearchOrderMilestone).filter(
            models.PaperResearchOrderMilestone.order_id == order.id,
            models.PaperResearchOrderMilestone.milestone == "ARMED",
        ).first()
        assert ms is not None, (
            f"RED: ARMED milestone was NOT recorded for order {order.id} because run link was not created before milestone lookup!"
        )
    finally:
        fresh_db.close()


def test_red_022_scheduler_uncommitted_post_cycle_research():
    """
    Defect A (Items 022, 031, 032):
    Scheduler evaluate_symbol_now calls evaluate_cycle(), which commits the cycle transaction.
    Then lines 350-379 stage research metadata (associate_order_with_research_run, record_committed_order_milestone)
    on the caller's db session, which only calls db.flush().
    When the scheduler worker closes the session (db.close()),
    any metadata staged after evaluate_cycle's commit is ROLLED BACK and lost!
    """
    from services.cg01_scheduler_service import CG01EvaluationScheduler
    from unittest.mock import patch

    sched = CG01EvaluationScheduler()
    sched.set_flags(enabled=True, shadow_mode=False, is_owner=True)

    fake_order_id = f"ord_sched_{int(time.time() * 1000)}"
    fake_run_id = f"run_sched_{int(time.time() * 1000)}"

    worker_db = SessionLocal()
    # Mock evaluate_cycle to simulate cycle committing and returning armed_order_id
    mock_cycle_res = {
        "status": "COMPLETED",
        "armed_order_id": fake_order_id,
        "cg01_evaluation": {"candidate_key": "cand_sched_123"},
    }

    with patch("services.scalp_watcher_orchestrator.scalp_watcher_orchestrator.evaluate_cycle", return_value=mock_cycle_res):
        with patch("services.paper_research_run_service.PaperResearchRunService.get_or_create_run") as mock_get_run:
            from services.paper_research_run_service import PaperResearchRun
            mock_run = PaperResearchRun(run_id=fake_run_id, symbol="XAUUSDT")
            mock_get_run.return_value = mock_run

            # Call evaluate_symbol_now on worker_db
            res = sched.evaluate_symbol_now("XAUUSDT", db=worker_db)
            assert res["status"] == "COMPLETED"

    # Simulate scheduler worker closing session without extra commit
    worker_db.close()

    # Query in fresh session: the link and milestone were never committed!
    fresh_db = SessionLocal()
    try:
        link = fresh_db.query(models.PaperResearchOrderLink).filter(
            models.PaperResearchOrderLink.order_id == fake_order_id
        ).first()
        assert link is not None, (
            f"RED: Link for order {fake_order_id} was lost because scheduler staged research after cycle commit without committing!"
        )
    finally:
        fresh_db.close()
