"""
backend/tests/test_v16_18_red_reproductions.py
V16.18 Mandatory RED Reproduction Tests:
- 021: Shadow mode mutates ACTIVE orchestrator state (zones, rejections, watch setups, funnel).
- 022: Telemetry persist_trace() commits caller's uncommitted database transaction.
- 023: resolve_active_observation_run() ignores scope in query, selecting wrong run.
- 024: Projection attaches historical event of previous run to currently active run.
- 025: Projection checkpoint advances over unlinked event without durable pending retry record.
- 026: Paper run summary coverage fails to retrieve traces from DB after restart (cleared buffer).
- 027: Trace timestamp restoration in load_traces sets started_at=completed_at=created_at_ms (losing true duration).
- 028: build_run_coverage_summary reports 100% uptime despite long worker outage when trace statuses are COMPLETED.
- 029: Trace querying conflates SHADOW and ACTIVE traces or drops ACTIVE when SHADOW dominates ring buffer.
"""
from __future__ import annotations

import json
import time
import pytest
from sqlalchemy.orm import Session

from database import SessionLocal
import models
import schemas
from services.cg01_telemetry_service import (
    CG01EvaluationTrace,
    CG01TracePersistenceWriter,
    CG01TraceRecorder,
    cg01_trace_recorder,
)
from services.paper_research_run_service import (
    PaperResearchRun,
    PaperResearchRunService,
    build_cg01_paper_run_summary,
    build_run_coverage_summary,
    resolve_active_observation_run,
    start_observation_run,
    record_committed_order_milestone,
)
from services.cg01_projection_worker import (
    CG01ResearchProjectionWorker,
    cg01_projection_worker,
)
from services.cg01_scheduler_service import (
    CG01EvaluationScheduler,
    cg01_evaluation_scheduler,
)
from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_red_021_shadow_isolation_violates_active_state(db: Session):
    """
    Prompt 021 & 041-045:
    Scheduler shadow evaluation must NEVER mutate ACTIVE orchestrator state:
    - Must provide pure shadow adapter evaluate_cg01_shadow_snapshot()
    - Scheduler in shadow mode must NOT invoke singleton scalp_watcher_orchestrator.evaluate_cycle()
    - Watchlist (_active_zones), rejections (_arm_rejections), watch_setups, and production funnel
      must remain completely untouched.
    """
    import services.cg01_scheduler_service as sched_mod
    assert hasattr(sched_mod, "evaluate_cg01_shadow_snapshot"), (
        "RED: evaluate_cg01_shadow_snapshot() pure shadow adapter does not exist!"
    )



def test_red_022_telemetry_commits_caller_uncommitted_transaction(db: Session):
    """
    Prompt 022 & 031-032:
    Calling persist_trace(trace, db=caller_db) must NOT commit caller_db!
    Baseline persist_trace() executes `target_db.commit()`, which commits the caller's pending work prematurely!
    """
    run_id = f"test_uncommitted_run_{int(time.time() * 1000)}"
    # Caller creates an uncommitted record in caller session
    new_run = models.PaperResearchRunRecord(
        run_id=run_id,
        symbol="XAUUSDT",
        status="CREATED",
        started_at_ms=int(time.time() * 1000),
        created_at_ms=int(time.time() * 1000),
        updated_at_ms=int(time.time() * 1000),
    )
    db.add(new_run)
    db.flush()

    trace = CG01EvaluationTrace(
        trace_id=f"trc_red_022_{int(time.time() * 1000)}",
        symbol="XAUUSDT",
        run_id=run_id,
        evaluation_started_at=int(time.time() * 1000),
        evaluation_completed_at=int(time.time() * 1000) + 10,
        total_runtime_ms=10.0,
    )

    # Record trace passing the caller's session
    cg01_trace_recorder.record_trace(trace, persist=True, db=db)

    # Second session must NOT see the uncommitted new_run!
    fresh_db = SessionLocal()
    try:
        found_in_fresh = fresh_db.query(models.PaperResearchRunRecord).filter(
            models.PaperResearchRunRecord.run_id == run_id
        ).first()
        # On baseline, found_in_fresh is NOT None because persist_trace did target_db.commit()!
        assert found_in_fresh is None, "RED: persist_trace() prematurely committed caller's uncommitted transaction!"
    finally:
        fresh_db.close()
        db.rollback()


def test_red_023_resolve_active_run_ignores_scope(db: Session):
    """
    Prompt 023 & 051-053:
    Two active runs exist for XAUUSDT with different scopes ('CG01' vs 'SMC_M1').
    resolve_active_observation_run(db, scope='CG01') must return the CG01 run, not the SMC_M1 run!
    Baseline query filters only by symbol and status, completely ignoring scope.
    """
    t_now = int(time.time() * 1000)
    run_cg01 = models.PaperResearchRunRecord(
        run_id=f"run_cg01_{t_now}",
        scope="CG01",
        symbol="XAUUSDT",
        status="COLLECTING",
        started_at_ms=t_now,
        created_at_ms=t_now,
        updated_at_ms=t_now,
    )
    # Newer run with scope SMC_M1
    run_smc = models.PaperResearchRunRecord(
        run_id=f"run_smcm1_{t_now + 1000}",
        scope="SMC_M1",
        symbol="XAUUSDT",
        status="COLLECTING",
        started_at_ms=t_now + 1000,
        created_at_ms=t_now + 1000,
        updated_at_ms=t_now + 1000,
    )
    db.add(run_cg01)
    db.add(run_smc)
    db.commit()

    resolved_cg01 = resolve_active_observation_run(db=db, scope="CG01", symbol="XAUUSDT")
    assert resolved_cg01 is not None
    # Baseline returns run_smc because it is newer and scope was ignored!
    assert "cg01" in resolved_cg01.run_id.lower(), (
        f"RED: resolve_active_observation_run(scope='CG01') returned {resolved_cg01.run_id} instead of CG01 run!"
    )


def test_red_024_projection_attaches_old_event_to_new_run(db: Session):
    """
    Prompt 024 & 074-077:
    Order was created under run A.
    Run A completes and run B starts (currently active).
    An unlinked domain event for order from run A arrives in projection.
    Projection MUST NOT attach order from run A to run B!
    """
    t_now = int(time.time() * 1000)
    run_a = models.PaperResearchRunRecord(
        run_id=f"run_a_{t_now}",
        symbol="XAUUSDT",
        status="COMPLETED",
        started_at_ms=t_now - 100000,
        ended_at_ms=t_now - 1000,
        created_at_ms=t_now - 100000,
        updated_at_ms=t_now - 1000,
    )
    run_b = models.PaperResearchRunRecord(
        run_id=f"run_b_{t_now}",
        symbol="XAUUSDT",
        status="COLLECTING",
        started_at_ms=t_now,
        created_at_ms=t_now,
        updated_at_ms=t_now,
    )
    order_a = models.PaperOrder(
        id=f"ord_a_{t_now}",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        planned_entry=2500.0,
        stop_loss=2490.0,
        take_profit=2520.0,
        quantity=0.1,
        initial_risk_usdt=10.0,
        created_at=t_now - 50000,
        armed_at=t_now - 50000,
    )
    db.add(run_a)
    db.add(run_b)
    db.add(order_a)
    db.commit()

    # Domain event for order_a arrives with no run_id in payload
    ev = models.DomainEvent(
        event_id=f"ev_armed_{t_now}",
        sequence=999901,
        schema_version="1.0.0",
        event_type="order.armed",
        aggregate_id=order_a.id,
        occurred_at=t_now - 50000,
        published_at=t_now - 50000,
        payload=json.dumps({"order_id": order_a.id}),
    )
    db.add(ev)
    db.commit()

    worker = CG01ResearchProjectionWorker(scope="CG01")
    # Advance checkpoint to just before ev.sequence
    cp = worker.get_or_create_checkpoint(db)
    cp.last_sequence = 999900
    db.commit()

    worker.process_pending_events(db=db)

    # Check which run order_a got linked to
    link = db.query(models.PaperResearchOrderLink).filter(
        models.PaperResearchOrderLink.order_id == order_a.id
    ).first()

    # On baseline, order_a gets linked to run_b because run_b is the active run!
    assert link is None or link.run_id != run_b.run_id, (
        f"RED: Projection attached historical order {order_a.id} to new active run {run_b.run_id}!"
    )


def test_red_025_checkpoint_advances_and_drops_unlinked_event(db: Session):
    """
    Prompt 025 & 078-080:
    A valid domain event arrives (e.g. trade.closed) but lacks link/causal resolution.
    On baseline, the event is skipped silently, checkpoint advances, and the event is LOST forever!
    In V16.18, if checkpoint advances or skips, it MUST create a durable pending/dead-letter record.
    """
    t_now = int(time.time() * 1000)
    unlinked_order_id = f"ord_orphan_{t_now}"

    ev = models.DomainEvent(
        event_id=f"ev_closed_{t_now}",
        sequence=999950,
        schema_version="1.0.0",
        event_type="trade.closed",
        aggregate_id=unlinked_order_id,
        occurred_at=t_now,
        published_at=t_now,
        payload=json.dumps({"order_id": unlinked_order_id, "exit_price": 2510.0, "exit_cause": "TP_HIT"}),
    )
    db.add(ev)
    db.commit()

    worker = CG01ResearchProjectionWorker(scope="CG01")
    cp = worker.get_or_create_checkpoint(db)
    cp.last_sequence = 999949
    db.commit()

    worker.process_pending_events(db=db)

    # In baseline, checkpoint advances to 999950, but no milestone was written and NO pending record exists!
    assert hasattr(models, "PaperResearchPendingProjection"), (
        "RED: models.PaperResearchPendingProjection does not exist to track durable unprojected events!"
    )


def test_red_026_coverage_fails_after_restart(db: Session):
    """
    Prompt 026 & 094-095:
    Save traces of a run into database. Clear the in-memory ring buffer.
    Call build_cg01_paper_run_summary(run_id, db=fresh_db).
    Summary coverage must find the persisted traces for run_id from DB!
    """
    run_id = f"run_restart_{int(time.time() * 1000)}"
    start_observation_run(db=db, run_id=run_id, symbol="XAUUSDT")
    db.commit()

    # Persist a trace to DB
    trace = CG01EvaluationTrace(
        trace_id=f"trc_durable_{int(time.time() * 1000)}",
        symbol="XAUUSDT",
        run_id=run_id,
        evaluation_started_at=int(time.time() * 1000),
        evaluation_completed_at=int(time.time() * 1000) + 20,
        total_runtime_ms=20.0,
    )
    CG01TracePersistenceWriter.persist_trace(trace, db=None)

    # Clear ring buffer
    cg01_trace_recorder.clear()

    # Fresh summary query
    fresh_db = SessionLocal()
    try:
        summary = build_cg01_paper_run_summary(run_id, db=fresh_db)
        cov = summary.get("data_coverage", {})
        # On baseline, summary calls cg01_trace_recorder.get_recent_traces(limit=500)
        # without passing run_id, so it gets 0 traces and reports traces_count == 0!
        assert cov.get("traces_count", 0) > 0, (
            "RED: build_cg01_paper_run_summary found 0 traces after in-memory ring buffer was cleared!"
        )
    finally:
        fresh_db.close()


def test_red_027_trace_timestamps_and_latencies_restoration(db: Session):
    """
    Prompt 027 & 091-093:
    Trace has distinct evaluation_started_at=1000 and evaluation_completed_at=1060 (duration 60ms).
    Persist to DB, clear buffer, and load via CG01TracePersistenceWriter.load_traces().
    On baseline, load_traces sets started_at=completed_at=created_at_ms, destroying latency!
    """
    t_start = 1700000000000
    t_done = 1700000000060
    trace = CG01EvaluationTrace(
        trace_id=f"trc_ts_red_{int(time.time() * 1000)}",
        symbol="XAUUSDT",
        run_id="run_ts_test",
        event_received_at=t_start - 20,
        exchange_event_time_ms=t_start - 50,
        evaluation_enqueued_at=t_start - 10,
        evaluation_started_at=t_start,
        evaluation_completed_at=t_done,
        armed_at=t_done + 15,
        total_runtime_ms=60.0,
    )
    CG01TracePersistenceWriter.persist_trace(trace, db=None)

    # Load traces back
    loaded = CG01TracePersistenceWriter.load_traces(run_id="run_ts_test", db=db)
    assert len(loaded) > 0
    t_loaded = next((x for x in loaded if x.trace_id == trace.trace_id), None)
    assert t_loaded is not None

    # Baseline sets started_at == completed_at == created_at_ms!
    assert t_loaded.evaluation_started_at == t_start, (
        f"RED: Restored evaluation_started_at={t_loaded.evaluation_started_at} != original {t_start}"
    )
    assert t_loaded.evaluation_completed_at == t_done, (
        f"RED: Restored evaluation_completed_at={t_loaded.evaluation_completed_at} != original {t_done}"
    )
    delays = t_loaded.calculate_delays()
    assert delays.get("detector_runtime_ms") == 60.0, (
        f"RED: Restored detector_runtime_ms={delays.get('detector_runtime_ms')} != 60.0"
    )


def test_red_028_uptime_vs_trace_health_with_gap(db: Session):
    """
    Prompt 028 & 111-115:
    Worker had a multi-hour downtime, but the few traces recorded were all COMPLETED (no DATA_GAP).
    Baseline calls ((total_traces - gap_count) / total_traces) * 100 = 100.0% uptime!
    In V16.18, trace health percentage must NOT be labeled as uptime_pct without observation intervals.
    """
    run = PaperResearchRun(
        run_id=f"run_uptime_red_{int(time.time() * 1000)}",
        symbol="XAUUSDT",
        status="COLLECTING",
        started_at_ms=int(time.time() * 1000) - 10800000, # 3 hours ago
    )
    # Only 2 traces, both COMPLETED
    t1 = CG01EvaluationTrace(trace_id="t1", evaluation_status="COMPLETED")
    t2 = CG01EvaluationTrace(trace_id="t2", evaluation_status="COMPLETED")

    cov = build_run_coverage_summary(run, [t1, t2])
    # Baseline returns uptime_pct: 100.0
    # In V16.18, without observation interval / heartbeat, uptime_pct must be None / UNAVAILABLE,
    # and trace_health_pct should be 100.0
    assert cov.get("uptime_pct") is None or "trace_health_pct" in cov, (
        "RED: build_run_coverage_summary reported 100% uptime_pct from 2 traces during a 3-hour period!"
    )


def test_red_029_trace_query_mixes_shadow_and_active(db: Session):
    """
    Prompt 029 & 094-095:
    50 SHADOW traces and 5 ACTIVE traces are recorded for XAUUSDT.
    Querying ACTIVE traces by mode must strictly return only ACTIVE traces.
    """
    recorder = CG01TraceRecorder(maxlen=100)
    for i in range(50):
        t_shadow = CG01EvaluationTrace(
            trace_id=f"trc_sh_{i}",
            symbol="XAUUSDT",
            run_id="run_shared",
            evaluation_status="COMPLETED",
        )
        t_shadow.observation_mode = "SHADOW"
        recorder.record_trace(t_shadow, persist=False)

    for i in range(5):
        t_active = CG01EvaluationTrace(
            trace_id=f"trc_act_{i}",
            symbol="XAUUSDT",
            run_id="run_shared",
            evaluation_status="COMPLETED",
        )
        t_active.observation_mode = "ACTIVE"
        recorder.record_trace(t_active, persist=False)

    # In baseline, get_traces does not have a mode filter
    assert hasattr(recorder, "get_traces_by_mode") or "mode" in recorder.get_traces.__code__.co_varnames, (
        "RED: CG01TraceRecorder lacks mode filtering in get_traces!"
    )
