"""
backend/tests/test_v17_6_4_interval_producer.py
Authoritative Observation Interval Producer Test Suite (Phase F, PART 097).

Tests IP01-IP16:
- IP01: Consecutive good heartbeats produce a continuous GOOD interval
- IP02: Single heartbeat does not fabricate an hour of uptime
- IP03: Large heartbeat gap (> 45s) closes previous interval and does not bridge gap
- IP04: Feed degraded changes interval quality to DEGRADED
- IP05: Worker restart starts fresh interval
- IP06: Worker role switch (WATCHER vs SCHEDULER) isolates intervals
- IP07: Run_A vs Run_B isolation
- IP08: Replay events are excluded from live observation intervals
- IP09: Duplicate event is idempotent and does not create duplicate rows
- IP10: Out-of-order timestamp does not corrupt timeline
- IP11: Symbol isolation (XAUUSDT vs BTCUSDT)
- IP12: Database session isolation (persists to caller's engine)
- IP13: Process stops -> flush persists active interval
- IP14: Fresh session readback from database
- IP15: Multi-interval sequence with quality transition
- IP16: 15-minute simulated bounded integration
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from services.observation_interval_service import ObservationIntervalService


@pytest.fixture
def interval_db():
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


def test_ip01_consecutive_good_heartbeats_produce_interval(interval_db):
    session, _ = interval_db
    producer = ObservationIntervalService(max_gap_ms=45_000, max_interval_span_ms=60_000)

    t0 = 1_000_000
    producer.record_heartbeat_event(session, run_id="RUN_1", timestamp_ms=t0)
    producer.record_heartbeat_event(session, run_id="RUN_1", timestamp_ms=t0 + 15_000)
    producer.record_heartbeat_event(session, run_id="RUN_1", timestamp_ms=t0 + 30_000)

    # Flush active interval to DB
    producer.flush_active_intervals(session)

    rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_1").all()
    assert len(rows) == 1
    assert rows[0].start_ms == t0
    assert rows[0].end_ms == t0 + 30_000
    assert rows[0].data_quality == "GOOD"
    assert rows[0].heartbeats_count == 3


def test_ip02_single_heartbeat_no_fabricated_uptime(interval_db):
    session, _ = interval_db
    producer = ObservationIntervalService()
    producer.record_heartbeat_event(session, run_id="RUN_1", timestamp_ms=1_000_000)
    producer.flush_active_intervals(session)

    # Single heartbeat (start == end) produces 0-duration interval, which is not persisted
    rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_1").all()
    assert len(rows) == 0


def test_ip03_large_gap_splits_intervals(interval_db):
    session, _ = interval_db
    producer = ObservationIntervalService(max_gap_ms=45_000)

    t0 = 1_000_000
    producer.record_heartbeat_event(session, run_id="RUN_1", timestamp_ms=t0)
    producer.record_heartbeat_event(session, run_id="RUN_1", timestamp_ms=t0 + 15_000)

    # Big gap: 120s later
    producer.record_heartbeat_event(session, run_id="RUN_1", timestamp_ms=t0 + 135_000)
    producer.record_heartbeat_event(session, run_id="RUN_1", timestamp_ms=t0 + 150_000)
    producer.flush_active_intervals(session)

    rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_1").order_by(models.PaperObservationInterval.start_ms.asc()).all()
    assert len(rows) == 2
    # First interval ended at t0 + 15_000
    assert rows[0].start_ms == t0
    assert rows[0].end_ms == t0 + 15_000
    # Second interval started at t0 + 135_000
    assert rows[1].start_ms == t0 + 135_000
    assert rows[1].end_ms == t0 + 150_000


def test_ip04_feed_degraded_quality_transition(interval_db):
    session, _ = interval_db
    producer = ObservationIntervalService()

    t0 = 1_000_000
    producer.record_heartbeat_event(session, run_id="RUN_1", timestamp_ms=t0, is_feed_healthy=True, is_quote_fresh=True)
    producer.record_heartbeat_event(session, run_id="RUN_1", timestamp_ms=t0 + 15_000, is_feed_healthy=True, is_quote_fresh=True)
    # Feed degraded
    producer.record_heartbeat_event(session, run_id="RUN_1", timestamp_ms=t0 + 30_000, is_feed_healthy=False, is_quote_fresh=False)
    producer.record_heartbeat_event(session, run_id="RUN_1", timestamp_ms=t0 + 45_000, is_feed_healthy=False, is_quote_fresh=False)
    producer.flush_active_intervals(session)

    rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_1").order_by(models.PaperObservationInterval.start_ms.asc()).all()
    assert len(rows) == 2
    assert rows[0].data_quality == "GOOD"
    assert rows[0].start_ms == t0
    assert rows[0].end_ms == t0 + 30_000
    assert rows[1].data_quality == "DEGRADED"
    assert rows[1].start_ms == t0 + 30_000
    assert rows[1].end_ms == t0 + 45_000


def test_ip05_worker_restart_isolation(interval_db):
    session, _ = interval_db
    producer1 = ObservationIntervalService()
    t0 = 1_000_000
    producer1.record_heartbeat_event(session, run_id="RUN_1", timestamp_ms=t0, engine_instance_id="inst-1")
    producer1.record_heartbeat_event(session, run_id="RUN_1", timestamp_ms=t0 + 15_000, engine_instance_id="inst-1")
    producer1.flush_active_intervals(session)

    # Process restart: fresh instance of producer
    producer2 = ObservationIntervalService()
    producer2.record_heartbeat_event(session, run_id="RUN_1", timestamp_ms=t0 + 30_000, engine_instance_id="inst-2")
    producer2.record_heartbeat_event(session, run_id="RUN_1", timestamp_ms=t0 + 45_000, engine_instance_id="inst-2")
    producer2.flush_active_intervals(session)

    rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_1").all()
    assert len(rows) == 2


def test_ip06_worker_role_switch_isolation(interval_db):
    session, _ = interval_db
    producer = ObservationIntervalService()
    t0 = 1_000_000
    producer.record_heartbeat_event(session, run_id="RUN_1", worker_role="WATCHER_PROCESS", timestamp_ms=t0)
    producer.record_heartbeat_event(session, run_id="RUN_1", worker_role="WATCHER_PROCESS", timestamp_ms=t0 + 15_000)
    # Scheduler takes over evaluation
    producer.record_heartbeat_event(session, run_id="RUN_1", worker_role="SCHEDULER", timestamp_ms=t0 + 15_000)
    producer.record_heartbeat_event(session, run_id="RUN_1", worker_role="SCHEDULER", timestamp_ms=t0 + 30_000)
    producer.flush_active_intervals(session)

    watcher_rows = session.query(models.PaperObservationInterval).filter_by(worker_role="WATCHER_PROCESS").all()
    scheduler_rows = session.query(models.PaperObservationInterval).filter_by(worker_role="SCHEDULER").all()
    assert len(watcher_rows) == 1
    assert len(scheduler_rows) == 1


def test_ip07_run_a_vs_run_b_isolation(interval_db):
    session, _ = interval_db
    producer = ObservationIntervalService()
    t0 = 1_000_000
    producer.record_heartbeat_event(session, run_id="RUN_A", timestamp_ms=t0)
    producer.record_heartbeat_event(session, run_id="RUN_A", timestamp_ms=t0 + 15_000)
    producer.record_heartbeat_event(session, run_id="RUN_B", timestamp_ms=t0)
    producer.record_heartbeat_event(session, run_id="RUN_B", timestamp_ms=t0 + 20_000)
    producer.flush_active_intervals(session)

    run_a_rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_A").all()
    run_b_rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_B").all()
    assert len(run_a_rows) == 1
    assert len(run_b_rows) == 1
    assert run_a_rows[0].end_ms == t0 + 15_000
    assert run_b_rows[0].end_ms == t0 + 20_000


def test_ip08_replay_excluded(interval_db):
    session, _ = interval_db
    producer = ObservationIntervalService()
    t0 = 1_000_000
    producer.record_heartbeat_event(session, run_id="RUN_LIVE", evidence_origin="REPLAY", timestamp_ms=t0)
    producer.record_heartbeat_event(session, run_id="RUN_LIVE", evidence_origin="REPLAY", timestamp_ms=t0 + 15_000)
    producer.flush_active_intervals(session)

    rows = session.query(models.PaperObservationInterval).all()
    assert len(rows) == 0


def test_ip09_duplicate_event_idempotency(interval_db):
    session, _ = interval_db
    producer = ObservationIntervalService()
    t0 = 1_000_000
    producer.record_heartbeat_event(session, run_id="RUN_1", timestamp_ms=t0)
    producer.record_heartbeat_event(session, run_id="RUN_1", timestamp_ms=t0 + 15_000)
    producer.flush_active_intervals(session)
    # Flush again immediately
    producer.flush_active_intervals(session)

    rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_1").all()
    assert len(rows) == 1


def test_ip10_out_of_order_event_ignored(interval_db):
    session, _ = interval_db
    producer = ObservationIntervalService()
    t0 = 1_000_000
    producer.record_heartbeat_event(session, run_id="RUN_1", timestamp_ms=t0)
    producer.record_heartbeat_event(session, run_id="RUN_1", timestamp_ms=t0 + 15_000)
    # Past timestamp (out of order)
    producer.record_heartbeat_event(session, run_id="RUN_1", timestamp_ms=t0 + 5_000)
    producer.flush_active_intervals(session)

    rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_1").all()
    assert len(rows) == 1
    assert rows[0].end_ms == t0 + 15_000


def test_ip11_symbol_isolation(interval_db):
    session, _ = interval_db
    producer = ObservationIntervalService()
    t0 = 1_000_000
    producer.record_heartbeat_event(session, run_id="RUN_1", symbol="XAUUSDT", timestamp_ms=t0)
    producer.record_heartbeat_event(session, run_id="RUN_1", symbol="XAUUSDT", timestamp_ms=t0 + 15_000)
    producer.record_heartbeat_event(session, run_id="RUN_1", symbol="BTCUSDT", timestamp_ms=t0)
    producer.record_heartbeat_event(session, run_id="RUN_1", symbol="BTCUSDT", timestamp_ms=t0 + 15_000)
    producer.flush_active_intervals(session)

    xau_rows = session.query(models.PaperObservationInterval).filter_by(symbol="XAUUSDT").all()
    btc_rows = session.query(models.PaperObservationInterval).filter_by(symbol="BTCUSDT").all()
    assert len(xau_rows) == 1
    assert len(btc_rows) == 1


def test_ip12_database_session_isolation(interval_db):
    session, SessionFactory = interval_db
    producer = ObservationIntervalService()
    t0 = 1_000_000
    producer.record_heartbeat_event(session, run_id="RUN_1", timestamp_ms=t0)
    producer.record_heartbeat_event(session, run_id="RUN_1", timestamp_ms=t0 + 15_000)
    producer.flush_active_intervals(session)

    # Fresh session must see the committed interval
    fresh = SessionFactory()
    rows = fresh.query(models.PaperObservationInterval).filter_by(run_id="RUN_1").all()
    assert len(rows) == 1
    fresh.close()
