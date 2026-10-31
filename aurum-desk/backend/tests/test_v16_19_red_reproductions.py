"""
backend/tests/test_v16_19_red_reproductions.py
V16.19 Mandatory RED Reproduction Tests:
- 021: Import shadow: evaluate_cg01_shadow_snapshot() fails with ImportError due to assemble_closed_candle_context.
- 022: Shadow with data: evaluate_cg01_shadow_snapshot() executes pure evaluator without mocking.
- 023: No mock detection in production: scheduler must not branch based on unittest.mock.
- 024: Trace persistence without caller DB: trace persists in worker session after caller session close.
- 025: Queue write failure: writer failure retains item for retry or accounts for drop.
- 026: Flush reports failure truthfully: flush_pending_traces() does not count failed write as success.
- 027: Retry commits without new events: process_pending_events() commits pending resolution even when processed_count == 0.
- 028: Original timestamp preserved on retry: milestone uses domain event occurred_at, not first_seen_at.
- 029: Coverage schema and intervals: build_run_coverage_summary() reads start_ms/end_ms and ended_at_ms.
- 030: Scope conflict: run with scope="SMC" and run_id containing "cg01" is never selected for scope="CG01".
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy.orm import Session

from database import SessionLocal
import models
from services.cg01_scheduler_service import (
    CG01EvaluationScheduler,
    evaluate_cg01_shadow_snapshot,
)
from services.cg01_telemetry_service import (
    CG01EvaluationTrace,
    CG01TracePersistenceWriter,
    enqueue_trace_persistence,
    flush_pending_traces,
    reset_telemetry_worker_state,
    _TRACE_QUEUE,
)
from services.cg01_projection_worker import (
    CG01ResearchProjectionWorker,
    cg01_projection_worker,
)
from services.paper_research_run_service import (
    PaperResearchRun,
    PaperResearchRunService,
    build_run_coverage_summary,
    resolve_active_observation_run,
    record_committed_order_milestone,
    associate_order_with_research_run,
)


@pytest.fixture
def db():
    session = SessionLocal()
    reset_telemetry_worker_state()
    try:
        yield session
    finally:
        reset_telemetry_worker_state()
        try:
            session.rollback()
            session.query(models.PaperResearchRunRecord).delete()
            session.commit()
        except Exception:
            session.rollback()
        session.close()


def make_candle(open_time: int, o: float, h: float, l: float, c: float, v: float = 100.0) -> dict:
    return {
        "open_time": open_time,
        "timestamp": open_time,
        "open": float(o),
        "high": float(h),
        "low": float(l),
        "close": float(c),
        "volume": float(v),
        "is_closed": True,
    }


def test_red_021_shadow_import_error():
    """
    Prompt 021 & 031: evaluate_cg01_shadow_snapshot must execute without ImportError.
    Baseline currently fails with:
    ImportError: cannot import name 'assemble_closed_candle_context' from 'services.cg01_context_adapter'
    """
    t_now = int(time.time() * 1000)
    bundle = {
        "1M": [make_candle(t_now - 60000, 2600.0, 2601.0, 2599.0, 2600.5)],
        "5M": [],
        "15M": [],
        "1H": [],
        "4H": [],
    }
    # Calling evaluate_cg01_shadow_snapshot directly:
    result = evaluate_cg01_shadow_snapshot(
        symbol="XAUUSDT",
        as_of_ms=t_now,
        bundle=bundle,
    )
    assert isinstance(result, dict)
    assert "status" in result
    assert result["armed_order_id"] is None


def test_red_022_shadow_evaluates_candidate_with_valid_fixture():
    """
    Prompt 022 & 032-033: Pure shadow evaluation with valid candle fixture
    returns structured decision without mock evaluator.
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)

    candles_1m = [
        make_candle(t_start, 2621.0, 2622.0, 2620.8, 2621.5),
        make_candle(t_start + 60_000, 2621.5, 2622.8, 2621.0, 2622.5),
        make_candle(t_start + 120_000, 2622.5, 2623.5, 2622.0, 2623.0),
        make_candle(t_start + 180_000, 2623.0, 2623.0, 2621.5, 2622.2),
        make_candle(t_start + 240_000, 2622.2, 2622.5, 2621.0, 2621.8),
        make_candle(t_start + 300_000, 2621.8, 2622.0, 2619.5, 2621.5),
        make_candle(t_start + 360_000, 2621.5, 2623.0, 2621.2, 2622.8),
        make_candle(t_start + 420_000, 2622.8, 2625.5, 2622.5, 2625.2),
        make_candle(t_start + 480_000, 2625.2, 2626.0, 2624.5, 2625.8),
    ]
    h1_candles = [
        make_candle(t_start - 3600_000 * i, 2620.0 + i, 2625.0 + i, 2618.0 + i, 2622.0 + i)
        for i in range(10, 0, -1)
    ]
    h4_candles = [
        make_candle(t_start - 14400_000 * i, 2610.0 + i, 2630.0 + i, 2605.0 + i, 2620.0 + i)
        for i in range(10, 0, -1)
    ]

    bundle = {
        "1M": candles_1m,
        "5M": [],
        "15M": [],
        "1H": h1_candles,
        "4H": h4_candles,
    }

    res = evaluate_cg01_shadow_snapshot(
        symbol="XAUUSDT",
        as_of_ms=t_start + 540_000,
        bundle=bundle,
    )
    assert res["status"] in ("COMPLETED", "CANDIDATE_FOUND", "SKIPPED")
    assert "cg01_evaluation" in res
    assert res["armed_order_id"] is None


def test_red_023_no_mock_detection_in_production():
    """
    Prompt 023 & 041: Production code in cg01_scheduler_service.py must NOT
    import unittest.mock or check isinstance(..., (Mock, MagicMock)).
    """
    import inspect
    import services.cg01_scheduler_service as sched_mod
    src = inspect.getsource(sched_mod.CG01EvaluationScheduler.evaluate_symbol_now)
    assert "unittest.mock" not in src, "Production code must not import or reference unittest.mock!"
    assert "MagicMock" not in src, "Production code must not inspect MagicMock!"


def test_red_024_trace_persisted_without_caller_db():
    """
    Prompt 024 & 051: Telemetry persistence does not use caller's DB session.
    A trace recorded via enqueue persists in fresh DB session after caller session is closed.
    """
    t_now = int(time.time() * 1000)
    trace_id = f"trc_red_024_{t_now}"
    trace = CG01EvaluationTrace(
        trace_id=trace_id,
        symbol="XAUUSDT",
        observation_mode="SHADOW",
        evaluation_started_at=t_now,
        evaluation_completed_at=t_now + 15,
        evaluation_status="COMPLETED",
    )

    # Persist via dedicated writer without caller db
    ok = CG01TracePersistenceWriter.persist_trace(trace, db=None)
    assert ok is True

    # Read back in fresh session
    fresh_db = SessionLocal()
    try:
        rec = fresh_db.query(models.CG01TraceRecord).filter(
            models.CG01TraceRecord.trace_id == trace_id
        ).first()
        assert rec is not None, "Trace must be durably committed in fresh session!"
        assert rec.observation_mode == "SHADOW"
    finally:
        fresh_db.close()


def test_red_025_queue_write_failure(monkeypatch):
    """
    Prompt 025 & 059: When database write fails, writer must not report success.
    """
    t_now = int(time.time() * 1000)
    trace = CG01EvaluationTrace(
        trace_id=f"trc_fail_{t_now}",
        symbol="XAUUSDT",
    )

    # Force database failure
    def mock_session():
        raise RuntimeError("Database connection lost")

    monkeypatch.setattr("database.SessionLocal", mock_session)
    res = CG01TracePersistenceWriter.persist_trace(trace, db=None)
    assert res is False, "persist_trace must return False on database write failure"


def test_red_026_flush_reports_failures_truthfully(monkeypatch):
    """
    Prompt 026 & 064: When persist_trace fails, flush_pending_traces() MUST NOT
    count item as successfully flushed or return status="COMPLETED".
    """
    # Drain queue first
    while not _TRACE_QUEUE.empty():
        try:
            _TRACE_QUEUE.get_nowait()
            _TRACE_QUEUE.task_done()
        except Exception:
            break

    trace = CG01EvaluationTrace(
        trace_id=f"trc_flush_{int(time.time() * 1000)}",
        symbol="XAUUSDT",
    )
    _TRACE_QUEUE.put_nowait(trace)

    # Force persist_trace to return False
    monkeypatch.setattr(CG01TracePersistenceWriter, "persist_trace", lambda t, db=None: False)

    flush_res = flush_pending_traces(timeout_s=1.0)
    # Flushed must NOT count failed items as successful flushed!
    assert flush_res.get("flushed", 0) == 0, "Failed trace persistence must not be counted as flushed!"
    assert flush_res.get("status") != "COMPLETED", "Flush with failed writes must not report COMPLETED!"


def test_red_027_pending_retry_commits_without_new_events():
    """
    Prompt 027 & 091: When there are NO new domain events, but pending projections
    are resolved during retry, process_pending_events() MUST commit the milestone
    and RESOLVED status.
    """
    db_setup = SessionLocal()
    order_id = f"ord_red_027_{int(time.time() * 1000)}"
    run_id = f"run_red_027_{int(time.time() * 1000)}"
    ev_id = f"ev_red_027_{int(time.time() * 1000)}"

    try:
        # Create run
        test_scope = f"CG01_RED27_{int(time.time() * 1000)}"
        run_rec = models.PaperResearchRunRecord(
            run_id=run_id,
            scope=test_scope,
            symbol="XAUUSDT",
            status="COLLECTING",
            started_at_ms=int(time.time() * 1000) - 10000,
            software_version="V16.19",
            config_fingerprint="CFG_TEST",
            execution_mode="PAPER_EXECUTION",
            created_at_ms=int(time.time() * 1000),
            updated_at_ms=int(time.time() * 1000),
        )
        db_setup.add(run_rec)

        # Create unlinked pending projection
        pending = models.PaperResearchPendingProjection(
            event_id=ev_id,
            scope=test_scope,
            event_type="order.armed",
            aggregate_id=order_id,
            reason="MISSING_RUN_LINK",
            payload_json=json.dumps({"order_id": order_id, "symbol": "XAUUSDT", "armed_at_ms": int(time.time() * 1000)}),
            first_seen_at_ms=int(time.time() * 1000) - 5000,
            next_retry_at_ms=int(time.time() * 1000) - 1000,
            attempt_count=1,
            status="PENDING",
            created_at_ms=int(time.time() * 1000) - 5000,
            updated_at_ms=int(time.time() * 1000) - 5000,
        )
        db_setup.add(pending)

        # Advance checkpoint so there are NO NEW events
        from sqlalchemy import func
        max_seq = db_setup.query(func.max(models.DomainEvent.sequence)).scalar() or 0
        cp = models.PaperResearchCheckpoint(
            scope=test_scope,
            last_sequence=max_seq,
            updated_at_ms=int(time.time() * 1000),
        )
        db_setup.merge(cp)
        db_setup.commit()

        # Now link the order to the run (adding causal link)
        associate_order_with_research_run(db_setup, run_id=run_id, order_id=order_id)
        db_setup.commit()
    finally:
        db_setup.close()

    # Call process_pending_events with a separate/new worker
    worker = CG01ResearchProjectionWorker(scope=test_scope, poll_interval_s=1.0)
    worker_db = SessionLocal()
    try:
        res = worker.process_pending_events(db=worker_db)
        assert res["processed"] == 0, "No new domain events should have been processed"
    finally:
        worker_db.close()

    # Read back in a FRESH session to prove commit succeeded
    fresh_db = SessionLocal()
    try:
        p_row = fresh_db.query(models.PaperResearchPendingProjection).filter(
            models.PaperResearchPendingProjection.event_id == ev_id
        ).first()
        assert p_row is not None
        assert p_row.status == "RESOLVED", (
            "Pending projection must be committed as RESOLVED even when processed_count == 0!"
        )

        ms = fresh_db.query(models.PaperResearchOrderMilestone).filter(
            models.PaperResearchOrderMilestone.run_id == run_id,
            models.PaperResearchOrderMilestone.order_id == order_id,
            models.PaperResearchOrderMilestone.milestone == "ARMED",
        ).first()
        assert ms is not None, "Milestone must be committed durably in fresh session!"
    finally:
        fresh_db.close()


def test_red_028_original_timestamp_preserved_on_retry():
    """
    Prompt 028 & 094: When retry_pending_projections succeeds, the created milestone
    must retain the original domain event occurred_at, NOT first_seen_at.
    """
    db_setup = SessionLocal()
    order_id = f"ord_red_028_{int(time.time() * 1000)}"
    run_id = f"run_red_028_{int(time.time() * 1000)}"
    ev_id = f"ev_red_028_{int(time.time() * 1000)}"

    original_occurred_at = 1700000000000  # Original event timestamp
    first_seen_at = 1700000050000         # Worker discovery timestamp (50s later)

    try:
        # Create domain event with original occurred_at
        dev = models.DomainEvent(
            event_id=ev_id,
            event_type="order.armed",
            aggregate_id=order_id,
            sequence=1000001,
            occurred_at=original_occurred_at,
            published_at=first_seen_at,
            payload=json.dumps({"order_id": order_id, "symbol": "XAUUSDT"}),
        )
        db_setup.add(dev)

        # Create run and link
        run_rec = models.PaperResearchRunRecord(
            run_id=run_id,
            scope="CG01",
            symbol="XAUUSDT",
            status="COLLECTING",
            started_at_ms=original_occurred_at - 10000,
            software_version="V16.19",
            config_fingerprint="CFG_TEST",
            execution_mode="PAPER_EXECUTION",
            created_at_ms=first_seen_at,
            updated_at_ms=first_seen_at,
        )
        db_setup.add(run_rec)

        # Pending projection with first_seen_at
        pending = models.PaperResearchPendingProjection(
            event_id=ev_id,
            scope="CG01",
            event_type="order.armed",
            aggregate_id=order_id,
            reason="MISSING_RUN_LINK",
            payload_json=json.dumps({"order_id": order_id, "symbol": "XAUUSDT"}),
            first_seen_at_ms=first_seen_at,
            next_retry_at_ms=first_seen_at,
            attempt_count=1,
            status="PENDING",
            created_at_ms=first_seen_at,
            updated_at_ms=first_seen_at,
        )
        db_setup.add(pending)

        associate_order_with_research_run(db_setup, run_id=run_id, order_id=order_id)
        db_setup.commit()
    finally:
        db_setup.close()

    worker = CG01ResearchProjectionWorker(scope="CG01")
    worker_db = SessionLocal()
    try:
        worker.retry_pending_projections(worker_db)
        worker_db.commit()
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
            f"Milestone must preserve original occurred_at ({original_occurred_at}), got {ms.timestamp_ms}"
        )
    finally:
        fresh_db.close()


def test_red_029_coverage_schema_and_intervals(db: Session):
    """
    Prompt 029 & 111-112: build_run_coverage_summary must use actual schema fields:
    run.ended_at_ms (not completed_at_ms) and PaperObservationInterval.start_ms/end_ms
    (not start_time_ms/end_time_ms).
    """
    t_now = int(time.time() * 1000)
    run_id = f"run_cov_{t_now}"
    run = PaperResearchRun(
        run_id=run_id,
        symbol="XAUUSDT",
        started_at_ms=t_now - 100000,
        ended_at_ms=t_now,
        status="COMPLETED",
    )

    # Interval using the REAL model fields
    inv1 = models.PaperObservationInterval(
        run_id=run_id,
        scope="CG01",
        symbol="XAUUSDT",
        worker_role="SCHEDULER",
        mode="SHADOW",
        start_ms=t_now - 80000,
        end_ms=t_now - 20000,  # 60000 ms duration
        data_quality="GOOD",
        created_at_ms=t_now,
    )

    summary = build_run_coverage_summary(run, traces=(), observation_intervals=[inv1])
    assert summary["uptime_pct"] is not None, "uptime_pct must not be None when intervals provided"
    # Expected: 60000 / 100000 = 60.0%
    assert summary["uptime_pct"] == 60.0, f"Expected 60.0% uptime, got {summary['uptime_pct']}"


def test_red_030_scope_conflict_exact_match(db: Session):
    """
    Prompt 030 & 074: resolve_active_observation_run for scope="CG01" must NOT select
    a run with scope="SMC" even if its run_id contains 'cg01'.
    """
    t_now = int(time.time() * 1000)
    smc_run_id = f"smc_cg01_comparison_{t_now}"

    # Insert a run with scope="SMC", but run_id contains "cg01"
    rec = models.PaperResearchRunRecord(
        run_id=smc_run_id,
        scope="SMC",  # Non-CG01 scope!
        symbol="XAUUSDT",
        status="COLLECTING",
        started_at_ms=t_now,
        software_version="V16.19",
        config_fingerprint="CFG_SMC",
        execution_mode="PAPER_EXECUTION",
        created_at_ms=t_now,
        updated_at_ms=t_now,
    )
    db.add(rec)
    db.commit()

    # Query active run specifically for scope="CG01"
    matched_run = resolve_active_observation_run(db, scope="CG01", symbol="XAUUSDT")
    if matched_run is not None:
        assert matched_run.run_id != smc_run_id, (
            f"Run with scope='SMC' was wrongly matched for scope='CG01' because run_id contained 'cg01'!"
        )
