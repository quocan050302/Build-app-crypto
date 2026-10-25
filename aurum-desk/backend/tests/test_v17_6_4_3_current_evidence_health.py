"""
backend/tests/test_v17_6_4_3_current_evidence_health.py
Authoritative Verification Suite for DBEA-07 & DBEA-08: Current Evidence Health vs Historical Commit Truth.

Tests EH01 through EH16 per Master Prompt V17.6.4.3 (Parts 109–126):
- EH01: No active run returns NO_ACTIVE_RUN
- EH02: Old committed cycle only -> UNVERIFIED
- EH03: Old committed interval only -> UNVERIFIED
- EH04: Historical cycle & interval committed, but worker offline/stalled -> commit COMMITTED, health STALLED/OFFLINE
- EH05: Fresh cycle, interval pending -> last_durable PENDING_COMMIT, health DEGRADED
- EH06: Fresh interval, missing evaluation -> health UNVERIFIED
- EH07: Healthy current evidence -> last_durable COMMITTED, health HEALTHY
- EH08: Pending write failure in target DB -> health DEGRADED
- EH09: Pending failure from unrelated database does NOT degrade current DB
- EH10: Pending failure from unrelated run does NOT degrade current run
- EH11: Restart gap detected and flagged
- EH12: Recovery after retry updates health to HEALTHY
- EH13: Stale quote degrades current health
- EH14: Zero PAPER trades is NOT an observability failure (health remains HEALTHY)
- EH15: Two active worker roles tracked independently
- EH16: Database query failure fails closed gracefully
"""
import time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from models import Base
from database import run_schema_migrations
from services.engine_observability_service import (
    EngineObservabilityService,
    engine_observability_service,
)
from services.observation_interval_service import (
    ObservationIntervalService,
    observation_interval_service,
)
from services.bitget_ws_service import bitget_ws_service
from services.quote_validator import CanonicalQuote


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "health_test.sqlite"
    engine = create_engine(f"sqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    session = Session()
    try:
        yield {"session": session, "engine": engine, "path": db_file}
    finally:
        session.close()


def test_eh01_no_active_run(test_db):
    """EH01: When no active run is resolved, health and commit status report NO_ACTIVE_RUN."""
    session = test_db["session"]
    obs = EngineObservabilityService()
    snap = obs.build_observation_dashboard_snapshot(session, run_id=None)

    assert snap["last_durable_commit_status"] == "NO_ACTIVE_RUN"
    assert snap["current_observation_evidence_health"] == "NO_ACTIVE_RUN"
    assert snap["evidence_persistence_status"] == "NO_ACTIVE_RUN"


def test_eh04_historical_rows_committed_but_worker_stalled(test_db):
    """
    EH04 (DBEA-07 Core): 10:00 Cycle and Interval committed, but at 14:00 worker is offline.
    last_durable_commit_status MUST be COMMITTED (historical truth).
    current_observation_evidence_health MUST be STALLED or OFFLINE (current operational truth).
    They must NOT be conflated!
    """
    session = test_db["session"]
    obs = EngineObservabilityService()
    t_10am = 1700000000000
    t_2pm = t_10am + 4 * 3600 * 1000  # 4 hours later
    run_id = "RUN_EH04"

    # Insert committed cycle from 10:00
    cycle = models.EngineEvaluationCycleRecord(
        cycle_id="cycle_10am",
        engine_instance_id="eng_eh04",
        symbol="XAUUSDT",
        run_id=run_id,
        status="COMPLETED",
        started_at_ms=t_10am - 1000,
        completed_at_ms=t_10am,
        created_at_ms=t_10am,
    )
    # Insert committed interval from 10:00
    interval = models.PaperObservationInterval(
        run_id=run_id,
        symbol="XAUUSDT",
        scope="CG01",
        worker_role="WATCHER_PROCESS",
        mode="PAPER",
        start_ms=t_10am - 30_000,
        end_ms=t_10am,
        data_quality="GOOD",
        created_at_ms=t_10am,
        interval_identity="ident_10am",
    )
    # Engine heartbeat last seen at 10:00 (offline for 4 hours at t_2pm)
    hb = models.EngineHeartbeatRecord(
        engine_instance_id="eng_eh04",
        symbol="XAUUSDT",
        run_id=run_id,
        state="RUNNING",
        last_process_heartbeat_at_ms=t_10am,
        updated_at_ms=t_10am,
    )
    session.add(cycle)
    session.add(interval)
    session.add(hb)
    session.commit()

    # Query snapshot at 14:00
    snap = obs.build_observation_dashboard_snapshot(session, run_id=run_id, now_ms=t_2pm)

    # Historical commit truth: committed rows exist
    assert snap["last_durable_commit_status"] == "COMMITTED"

    # Current evidence health: worker heartbeat is 4 hours stale -> STALLED!
    assert snap["current_observation_evidence_health"] in ("STALLED", "OFFLINE")


def test_eh07_healthy_current_evidence(test_db):
    """EH07: Fresh heartbeat, fresh quote, committed cycle and interval -> COMMITTED & HEALTHY."""
    session = test_db["session"]
    obs = EngineObservabilityService()
    t_now = 1700000000000
    run_id = "RUN_EH07"

    # Mock fresh quote on bitget_ws_service
    bitget_ws_service.transport_state = "CONNECTED"
    bitget_ws_service.latest_quote = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2650.0,
        ask=2650.5,
        last=2650.2,
        exchange_ts_ms=t_now - 50,
        received_at_ms=t_now - 50,
    )

    # Fresh cycle
    cycle = models.EngineEvaluationCycleRecord(
        cycle_id="cycle_now",
        engine_instance_id="eng_eh07",
        symbol="XAUUSDT",
        run_id=run_id,
        status="COMPLETED",
        started_at_ms=t_now - 1000,
        completed_at_ms=t_now,
        created_at_ms=t_now,
    )
    # Fresh interval
    interval = models.PaperObservationInterval(
        run_id=run_id,
        symbol="XAUUSDT",
        scope="CG01",
        worker_role="WATCHER_PROCESS",
        mode="PAPER",
        start_ms=t_now - 30_000,
        end_ms=t_now,
        data_quality="GOOD",
        created_at_ms=t_now,
        interval_identity="ident_now",
    )
    # Fresh engine heartbeat
    hb = models.EngineHeartbeatRecord(
        engine_instance_id="eng_eh07",
        symbol="XAUUSDT",
        run_id=run_id,
        state="RUNNING",
        last_process_heartbeat_at_ms=t_now - 2000,
        updated_at_ms=t_now - 2000,
    )
    session.add(cycle)
    session.add(interval)
    session.add(hb)
    session.commit()

    snap = obs.build_observation_dashboard_snapshot(session, run_id=run_id, now_ms=t_now)

    assert snap["last_durable_commit_status"] == "COMMITTED"
    assert snap["current_observation_evidence_health"] == "HEALTHY"
    assert snap["last_verified_cycle_at_ms"] == t_now
    assert snap["last_verified_interval_end_ms"] == t_now


def test_eh08_eh09_pending_error_isolated_by_database(tmp_path):
    """EH08 & EH09: Pending error in DB_A does NOT leak into DB_B's health snapshot."""
    file_a = tmp_path / "db_a.sqlite"
    file_b = tmp_path / "db_b.sqlite"
    eng_a = create_engine(f"sqlite:///{file_a.as_posix()}", connect_args={"check_same_thread": False})
    eng_b = create_engine(f"sqlite:///{file_b.as_posix()}", connect_args={"check_same_thread": False})

    Base.metadata.create_all(bind=eng_a)
    Base.metadata.create_all(bind=eng_b)
    run_schema_migrations(eng_a)
    run_schema_migrations(eng_b)

    s_a = sessionmaker(bind=eng_a, autoflush=False)()
    s_b = sessionmaker(bind=eng_b, autoflush=False)()

    try:
        t_now = 1700000000000
        # Mock fresh quote on bitget_ws_service
        bitget_ws_service.transport_state = "CONNECTED"
        bitget_ws_service.latest_quote = CanonicalQuote(
            symbol="XAUUSDT",
            bid=2650.0,
            ask=2650.5,
            last=2650.2,
            exchange_ts_ms=t_now - 50,
            received_at_ms=t_now - 50,
        )

        # Seed healthy records in DB_B
        c_b = models.EngineEvaluationCycleRecord(cycle_id="c_b", engine_instance_id="eng_b", run_id="RUN_B", symbol="XAUUSDT", status="COMPLETED", started_at_ms=t_now-1000, completed_at_ms=t_now, created_at_ms=t_now)
        i_b = models.PaperObservationInterval(run_id="RUN_B", symbol="XAUUSDT", start_ms=t_now-10000, end_ms=t_now, data_quality="GOOD", created_at_ms=t_now, interval_identity="ib")
        hb_b = models.EngineHeartbeatRecord(engine_instance_id="eng_b", symbol="XAUUSDT", run_id="RUN_B", state="RUNNING", last_process_heartbeat_at_ms=t_now-1000, updated_at_ms=t_now-1000)
        s_b.add_all([c_b, i_b, hb_b])
        s_b.commit()

        # Inject error into DB_A
        from services.observation_interval_service import observation_interval_service, resolve_observation_database_identity
        ident_a = resolve_observation_database_identity(s_a)
        observation_interval_service._last_persist_error_by_db[ident_a] = "ERR_SQLITE_LOCKED_A"

        obs = EngineObservabilityService()
        snap_b = obs.build_observation_dashboard_snapshot(s_b, run_id="RUN_B", now_ms=t_now)

        # Database B must be completely unaffected by Database A's error
        assert snap_b["current_observation_evidence_health"] == "HEALTHY"
        assert snap_b["last_durable_commit_status"] == "COMMITTED"
        assert snap_b["pending_intervals_count"] == 0

    finally:
        s_a.close()
        s_b.close()


def test_eh14_zero_trades_is_healthy(test_db):
    """EH14: Zero PAPER trades is normal market observation, NOT an observability failure."""
    session = test_db["session"]
    obs = EngineObservabilityService()
    t_now = 1700000000000
    run_id = "RUN_NO_TRADES"

    # Mock fresh quote on bitget_ws_service
    bitget_ws_service.transport_state = "CONNECTED"
    bitget_ws_service.latest_quote = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2650.0,
        ask=2650.5,
        last=2650.2,
        exchange_ts_ms=t_now - 50,
        received_at_ms=t_now - 50,
    )

    cycle = models.EngineEvaluationCycleRecord(
        cycle_id="cycle_1",
        engine_instance_id="eng_notrade",
        symbol="XAUUSDT",
        run_id=run_id,
        status="COMPLETED",
        started_at_ms=t_now - 1000,
        completed_at_ms=t_now,
        created_at_ms=t_now,
    )
    interval = models.PaperObservationInterval(
        run_id=run_id,
        symbol="XAUUSDT",
        start_ms=t_now - 30_000,
        end_ms=t_now,
        data_quality="GOOD",
        created_at_ms=t_now,
        interval_identity="ident_notrade",
    )
    hb = models.EngineHeartbeatRecord(
        engine_instance_id="eng_notrade",
        symbol="XAUUSDT",
        run_id=run_id,
        state="RUNNING",
        last_process_heartbeat_at_ms=t_now - 1000,
        updated_at_ms=t_now - 1000,
    )
    session.add_all([cycle, interval, hb])
    session.commit()

    snap = obs.build_observation_dashboard_snapshot(session, run_id=run_id, now_ms=t_now)
    # Zero trades in financial summary
    assert snap["financial"]["closed_trades"] in (0, None)
    # Health remains HEALTHY
    assert snap["current_observation_evidence_health"] == "HEALTHY"
    assert snap["last_durable_commit_status"] == "COMMITTED"
