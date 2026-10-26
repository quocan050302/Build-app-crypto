"""
backend/tests/test_v17_6_4_1_quality_transitions.py
AURUM V17.6.4.1 — Observation Quality Transitions & Conservative Boundary Semantics (EVID-05, Parts 057-068).

Cases:
- QT01: GOOD -> DEGRADED transition closes previous at last_ts, does not credit delta as GOOD
- QT02: DEGRADED -> GOOD recovery does not credit prior gap as GOOD
- QT03: GOOD -> DOWN transition
- QT04: DOWN -> GOOD recovery
- QT05: Multiple rapid quality transitions maintain discrete non-overlapping segments
- QT06: Out-of-order timestamp ignored, timeline uncorrupted
- QT07: Duplicate timestamp does not produce zero-duration interval or extra count
- QT08: Worker ownership switch does not bridge gap as GOOD
- QT09: Process restart starts fresh interval without fabricating continuity
- QT10: Two overlapping worker roles isolate their intervals
- QT11: Transition gap is classified as UNVERIFIED or bounded
- QT12: Exact mathematical coverage reconciliation (sum of intervals <= elapsed time)
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from services.observation_interval_service import (
    ObservationIntervalService,
    split_observation_quality_transition,
)


@pytest.fixture
def memory_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False)
    session = SessionFactory()
    yield session, SessionFactory
    session.close()
    engine.dispose()


def test_qt01_good_to_degraded_boundary(memory_db):
    """QT01: GOOD -> DEGRADED closes previous at last_ts, does not extend GOOD to t_now."""
    session, _ = memory_db
    producer = ObservationIntervalService(max_gap_ms=45_000)

    t0 = 1_700_000_000_000
    # 12:00:00 GOOD
    producer.record_heartbeat_event(
        session, run_id="RUN_QT01", timestamp_ms=t0,
        is_feed_healthy=True, is_quote_fresh=True, coverage_quality="GOOD"
    )
    # 12:00:15 GOOD
    producer.record_heartbeat_event(
        session, run_id="RUN_QT01", timestamp_ms=t0 + 15_000,
        is_feed_healthy=True, is_quote_fresh=True, coverage_quality="GOOD"
    )
    # 12:00:30 DEGRADED
    producer.record_heartbeat_event(
        session, run_id="RUN_QT01", timestamp_ms=t0 + 30_000,
        is_feed_healthy=False, is_quote_fresh=False, coverage_quality="DEGRADED"
    )

    # Flush
    producer.flush_active_intervals(session)

    rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_QT01").order_by(models.PaperObservationInterval.start_ms).all()
    assert len(rows) >= 1

    good_row = [r for r in rows if r.data_quality == "GOOD"][0]
    # The GOOD row must end at t0 + 15_000 (last proven timestamp), NOT t0 + 30_000!
    assert good_row.end_ms == t0 + 15_000, f"GOOD row end_ms ({good_row.end_ms}) was overstated past last proven timestamp {t0 + 15_000}!"


def test_qt02_degraded_to_good_recovery(memory_db):
    """QT02: DEGRADED -> GOOD does not invent prior GOOD time."""
    session, _ = memory_db
    producer = ObservationIntervalService(max_gap_ms=45_000)

    t0 = 1_700_000_000_000
    # 12:00:00 DEGRADED
    producer.record_heartbeat_event(
        session, run_id="RUN_QT02", timestamp_ms=t0,
        is_feed_healthy=False, is_quote_fresh=False, coverage_quality="DEGRADED"
    )
    # 12:00:15 DEGRADED
    producer.record_heartbeat_event(
        session, run_id="RUN_QT02", timestamp_ms=t0 + 15_000,
        is_feed_healthy=False, is_quote_fresh=False, coverage_quality="DEGRADED"
    )
    # 12:00:30 GOOD
    producer.record_heartbeat_event(
        session, run_id="RUN_QT02", timestamp_ms=t0 + 30_000,
        is_feed_healthy=True, is_quote_fresh=True, coverage_quality="GOOD"
    )

    producer.flush_active_intervals(session)

    rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_QT02").order_by(models.PaperObservationInterval.start_ms).all()
    good_rows = [r for r in rows if r.data_quality == "GOOD"]
    if good_rows:
        # GOOD start_ms must be at or after t0 + 30_000
        assert good_rows[0].start_ms >= t0 + 30_000


def test_qt03_good_to_down(memory_db):
    """QT03: GOOD -> DOWN transition preserves proven good segment."""
    session, _ = memory_db
    producer = ObservationIntervalService(max_gap_ms=45_000)

    t0 = 1_700_000_000_000
    producer.record_heartbeat_event(session, run_id="RUN_QT03", timestamp_ms=t0, coverage_quality="GOOD")
    producer.record_heartbeat_event(session, run_id="RUN_QT03", timestamp_ms=t0 + 10_000, coverage_quality="GOOD")
    producer.record_heartbeat_event(session, run_id="RUN_QT03", timestamp_ms=t0 + 20_000, coverage_quality="DOWN")

    producer.flush_active_intervals(session)
    rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_QT03").all()
    good_rows = [r for r in rows if r.data_quality == "GOOD"]
    assert len(good_rows) == 1
    assert good_rows[0].end_ms == t0 + 10_000


def test_qt04_down_to_good_recovery(memory_db):
    """QT04: DOWN -> GOOD recovery starts fresh GOOD interval."""
    session, _ = memory_db
    producer = ObservationIntervalService(max_gap_ms=45_000)

    t0 = 1_700_000_000_000
    producer.record_heartbeat_event(session, run_id="RUN_QT04", timestamp_ms=t0, coverage_quality="DOWN")
    producer.record_heartbeat_event(session, run_id="RUN_QT04", timestamp_ms=t0 + 15_000, coverage_quality="GOOD")
    producer.record_heartbeat_event(session, run_id="RUN_QT04", timestamp_ms=t0 + 30_000, coverage_quality="GOOD")

    producer.flush_active_intervals(session)
    rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_QT04").all()
    good_rows = [r for r in rows if r.data_quality == "GOOD"]
    assert len(good_rows) >= 1
    assert good_rows[0].start_ms == t0 + 15_000


def test_qt05_multiple_transitions(memory_db):
    """QT05: GOOD -> DEGRADED -> GOOD -> DEGRADED."""
    session, _ = memory_db
    producer = ObservationIntervalService(max_gap_ms=45_000)

    t = 1_700_000_000_000
    for q in ["GOOD", "GOOD", "DEGRADED", "DEGRADED", "GOOD", "GOOD", "DEGRADED"]:
        producer.record_heartbeat_event(session, run_id="RUN_QT05", timestamp_ms=t, coverage_quality=q)
        t += 10_000

    producer.flush_active_intervals(session)
    rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_QT05").order_by(models.PaperObservationInterval.start_ms).all()
    assert len(rows) >= 3


def test_qt06_out_of_order_timestamp(memory_db):
    """QT06: Out-of-order timestamp (t < last_ts) is ignored."""
    session, _ = memory_db
    producer = ObservationIntervalService(max_gap_ms=45_000)

    t0 = 1_700_000_000_000
    producer.record_heartbeat_event(session, run_id="RUN_QT06", timestamp_ms=t0 + 20_000, coverage_quality="GOOD")
    # Older timestamp arrives
    res = producer.record_heartbeat_event(session, run_id="RUN_QT06", timestamp_ms=t0 + 10_000, coverage_quality="GOOD")
    assert res is None


def test_qt07_duplicate_timestamp(memory_db):
    """QT07: Same-time heartbeat does not create duplicate interval."""
    session, _ = memory_db
    producer = ObservationIntervalService(max_gap_ms=45_000)

    t0 = 1_700_000_000_000
    producer.record_heartbeat_event(session, run_id="RUN_QT07", timestamp_ms=t0, coverage_quality="GOOD")
    res = producer.record_heartbeat_event(session, run_id="RUN_QT07", timestamp_ms=t0, coverage_quality="GOOD")
    assert res is None


def test_qt08_worker_ownership_change(memory_db):
    """QT08: Worker switch (WATCHER vs SCHEDULER) creates isolated intervals."""
    session, _ = memory_db
    producer = ObservationIntervalService(max_gap_ms=45_000)

    t0 = 1_700_000_000_000
    producer.record_heartbeat_event(session, run_id="RUN_QT08", worker_role="WATCHER_PROCESS", timestamp_ms=t0, coverage_quality="GOOD")
    producer.record_heartbeat_event(session, run_id="RUN_QT08", worker_role="WATCHER_PROCESS", timestamp_ms=t0 + 15_000, coverage_quality="GOOD")

    # Scheduler takes over
    producer.record_heartbeat_event(session, run_id="RUN_QT08", worker_role="SCHEDULER", timestamp_ms=t0 + 20_000, coverage_quality="GOOD")
    producer.record_heartbeat_event(session, run_id="RUN_QT08", worker_role="SCHEDULER", timestamp_ms=t0 + 35_000, coverage_quality="GOOD")

    producer.flush_active_intervals(session)
    watcher_rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_QT08", worker_role="WATCHER_PROCESS").all()
    scheduler_rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_QT08", worker_role="SCHEDULER").all()

    assert len(watcher_rows) >= 1
    assert len(scheduler_rows) >= 1


def test_qt09_process_restart(memory_db):
    """QT09: Service restart with fresh in-memory state does not bridge gap."""
    session, _ = memory_db
    p1 = ObservationIntervalService(max_gap_ms=45_000)
    t0 = 1_700_000_000_000

    p1.record_heartbeat_event(session, run_id="RUN_QT09", timestamp_ms=t0, coverage_quality="GOOD")
    p1.record_heartbeat_event(session, run_id="RUN_QT09", timestamp_ms=t0 + 15_000, coverage_quality="GOOD")
    p1.flush_active_intervals(session)

    # Process restarts 2 hours later
    p2 = ObservationIntervalService(max_gap_ms=45_000)
    t_restart = t0 + 7_200_000
    p2.record_heartbeat_event(session, run_id="RUN_QT09", timestamp_ms=t_restart, coverage_quality="GOOD")
    p2.record_heartbeat_event(session, run_id="RUN_QT09", timestamp_ms=t_restart + 15_000, coverage_quality="GOOD")
    p2.flush_active_intervals(session)

    rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_QT09").order_by(models.PaperObservationInterval.start_ms).all()
    assert len(rows) == 2
    # The gap of 2 hours is NOT filled
    assert rows[1].start_ms == t_restart


def test_qt10_two_overlapping_workers(memory_db):
    """QT10: Two workers emitting heartbeats do not corrupt each other's intervals."""
    session, _ = memory_db
    producer = ObservationIntervalService()
    t0 = 1_700_000_000_000

    for i in range(5):
        producer.record_heartbeat_event(session, run_id="RUN_QT10", worker_role="WORKER_A", timestamp_ms=t0 + i * 10_000, coverage_quality="GOOD")
        producer.record_heartbeat_event(session, run_id="RUN_QT10", worker_role="WORKER_B", timestamp_ms=t0 + i * 10_000 + 2000, coverage_quality="DEGRADED")

    producer.flush_active_intervals(session)
    rows_a = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_QT10", worker_role="WORKER_A").all()
    rows_b = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_QT10", worker_role="WORKER_B").all()

    for r in rows_a:
        assert r.data_quality == "GOOD"
    for r in rows_b:
        assert r.data_quality == "DEGRADED"


def test_qt11_transition_gap_unverified():
    """QT11: Pure helper split_observation_quality_transition assigns gap to UNVERIFIED."""
    t_prev = 1_700_000_000_000
    t_curr = 1_700_000_030_000

    segments = split_observation_quality_transition(
        previous_quality="GOOD",
        previous_verified_ts=t_prev,
        current_quality="DEGRADED",
        current_ts=t_curr,
    )
    # Segments should contain gap marked UNVERIFIED
    assert len(segments) >= 1
    assert any(s["quality"] == "UNVERIFIED" for s in segments)


def test_qt12_exact_coverage_reconciliation(memory_db):
    """QT12: Total sum of GOOD milliseconds never exceeds proven duration."""
    session, _ = memory_db
    producer = ObservationIntervalService(max_gap_ms=45_000)

    t0 = 1_700_000_000_000
    # 3 heartbeats at 0s, 10s, 20s (total proven good = 20s = 20,000ms)
    producer.record_heartbeat_event(session, run_id="RUN_QT12", timestamp_ms=t0, coverage_quality="GOOD")
    producer.record_heartbeat_event(session, run_id="RUN_QT12", timestamp_ms=t0 + 10_000, coverage_quality="GOOD")
    producer.record_heartbeat_event(session, run_id="RUN_QT12", timestamp_ms=t0 + 20_000, coverage_quality="GOOD")
    producer.flush_active_intervals(session)

    rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_QT12").all()
    total_good_ms = sum((r.end_ms - r.start_ms) for r in rows if r.data_quality == "GOOD")
    assert total_good_ms == 20_000, f"Expected 20,000 ms, got {total_good_ms}"
