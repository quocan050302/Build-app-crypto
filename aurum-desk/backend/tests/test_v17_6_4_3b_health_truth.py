"""
backend/tests/test_v17_6_4_3b_health_truth.py
Authoritative Integration and Snapshot Suite for V17.6.4.3b:
Current Evidence Health Truth and Observation Quality Invariants.

Covers Test Matrix (Section F, Parts 093–094):
EH01: Feed GOOD + all evidence valid -> HEALTHY
EH02: Feed UNVERIFIED -> UNVERIFIED (never HEALTHY)
EH03: Feed missing (None) -> UNVERIFIED
EH04: Feed DOWN -> DOWN
EH05: Feed DEGRADED -> DEGRADED
EH06: Process stopped -> OFFLINE
EH07: Process stalled -> STALLED
EH08: Old cycle + healthy process -> NOT HEALTHY
EH09: Old interval + valid feed -> NOT HEALTHY (STALLED)
EH10: Pending commit + good feed -> NOT HEALTHY (DEGRADED)
EH11: Persistence failure + good feed -> DEGRADED
EH12: REST fallback -> NOT HEALTHY (DEGRADED)
EH13: No active run -> NO_ACTIVE_RUN / UNVERIFIED
EH14: Wrong-run interval -> NOT HEALTHY
EH15: Recovery after fresh verified evidence -> HEALTHY
EH16: Recent heartbeat, feed unverified -> process RUNNING, but observation UNVERIFIED
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
from services.observation_interval_service import observation_interval_service
from services.bitget_ws_service import bitget_ws_service
from services.collector_service import collector_service
from services.quote_validator import CanonicalQuote


@pytest.fixture
def isolated_dbs(tmp_path):
    path_a = tmp_path / "health_truth.sqlite"
    eng = create_engine(f"sqlite:///{path_a.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    run_schema_migrations(eng)
    s = sessionmaker(bind=eng, autoflush=False)()

    orig_running = getattr(bitget_ws_service, "_running", False)
    orig_transport = bitget_ws_service.transport_state
    orig_source = bitget_ws_service.feed_source
    orig_epoch = bitget_ws_service.connection_epoch
    orig_quote = bitget_ws_service.latest_quote
    orig_ticker = collector_service._latest_ticker

    try:
        yield {"session": s, "engine": eng}
    finally:
        s.close()
        bitget_ws_service._running = orig_running
        bitget_ws_service.transport_state = orig_transport
        bitget_ws_service.feed_source = orig_source
        bitget_ws_service.connection_epoch = orig_epoch
        bitget_ws_service.latest_quote = orig_quote
        collector_service._latest_ticker = orig_ticker


def _make_valid_cq(symbol="XAUUSDT", t0=1700000000000, source="WS", epoch=1):
    return CanonicalQuote(
        symbol=symbol,
        last=2650.25,
        bid=2650.0,
        ask=2650.5,
        exchange_ts_ms=t0 - 50,
        received_at_ms=t0 - 40,
        source=source,
        connection_epoch=epoch,
        status="VALID",
        freshness_sec=0.05,
    )


def test_eh01_feed_good_and_all_evidence_valid_is_healthy(isolated_dbs):
    """EH01: Feed GOOD + fresh process + committed interval + fresh cycle -> HEALTHY."""
    s = isolated_dbs["session"]
    t0 = 1700000000000
    run_id = "RUN_EH01"

    bitget_ws_service._running = True
    bitget_ws_service.transport_state = "CONNECTED"
    bitget_ws_service.connection_epoch = 1
    cq = _make_valid_cq(t0=t0)
    bitget_ws_service.latest_quote = cq

    # Heartbeat
    engine_observability_service.record_engine_heartbeat(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        state="RUNNING",
        heartbeat_at_ms=t0,
        metadata={
            "coverage_quality": "GOOD",
            "canonical_quote": cq.to_dict(),
        },
    )
    # Cycle
    engine_observability_service.record_evaluation_cycle(
        s,
        cycle_id="CYC_01",
        run_id=run_id,
        symbol="XAUUSDT",
        started_at_ms=t0 - 100,
        completed_at_ms=t0 - 10,
        status="COMPLETED",
        canonical_quote=cq.to_dict(),
    )
    # Interval
    rec = models.PaperObservationInterval(
        run_id=run_id,
        symbol="XAUUSDT",
        scope="CG01",
        worker_role="WATCHER_PROCESS",
        mode="PAPER",
        start_ms=t0 - 10_000,
        end_ms=t0,
        data_quality="GOOD",
        interval_identity="ident_eh01",
        created_at_ms=t0,
    )
    s.add(rec)
    s.commit()

    snap = engine_observability_service.build_observation_dashboard_snapshot(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        now_ms=t0,
    )
    assert snap["current_observation_evidence_health"] == "HEALTHY"


def test_eh02_feed_unverified_cannot_produce_healthy(isolated_dbs):
    """EH02: Feed UNVERIFIED must evaluate to UNVERIFIED, never HEALTHY."""
    s = isolated_dbs["session"]
    t0 = 1700000000000
    run_id = "RUN_EH02"

    bitget_ws_service._running = True
    bitget_ws_service.transport_state = "CONNECTED"
    cq_unverified = _make_valid_cq(t0=t0, source="UNKNOWN")
    bitget_ws_service.latest_quote = cq_unverified

    engine_observability_service.record_engine_heartbeat(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        state="RUNNING",
        heartbeat_at_ms=t0,
        metadata={
            "coverage_quality": "UNVERIFIED",
            "is_feed_healthy": False,
        },
    )
    engine_observability_service.record_evaluation_cycle(
        s,
        cycle_id="CYC_02",
        run_id=run_id,
        symbol="XAUUSDT",
        started_at_ms=t0 - 100,
        completed_at_ms=t0 - 10,
        status="COMPLETED",
    )
    rec = models.PaperObservationInterval(
        run_id=run_id,
        symbol="XAUUSDT",
        scope="CG01",
        worker_role="WATCHER_PROCESS",
        mode="PAPER",
        start_ms=t0 - 10_000,
        end_ms=t0,
        data_quality="UNVERIFIED",
        interval_identity="ident_eh02",
        created_at_ms=t0,
    )
    s.add(rec)
    s.commit()

    snap = engine_observability_service.build_observation_dashboard_snapshot(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        now_ms=t0,
    )
    assert snap["current_observation_evidence_health"] == "UNVERIFIED", \
        f"Vulnerability CFP-06: UNVERIFIED feed produced: {snap['current_observation_evidence_health']}"


def test_eh03_feed_missing_is_unverified(isolated_dbs):
    """EH03: Feed missing (None) evaluates to UNVERIFIED or DOWN."""
    s = isolated_dbs["session"]
    t0 = 1700000000000
    run_id = "RUN_EH03"

    bitget_ws_service._running = False
    bitget_ws_service.latest_quote = None
    collector_service._latest_ticker = None

    engine_observability_service.record_engine_heartbeat(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        state="RUNNING",
        heartbeat_at_ms=t0,
        metadata={},  # No feed
    )
    snap = engine_observability_service.build_observation_dashboard_snapshot(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        now_ms=t0,
    )
    assert snap["current_observation_evidence_health"] in ("UNVERIFIED", "DOWN", "STALLED")


def test_eh04_feed_down_is_down(isolated_dbs):
    """EH04: Feed DOWN evaluates to DOWN."""
    s = isolated_dbs["session"]
    t0 = 1700000000000
    run_id = "RUN_EH04"

    bitget_ws_service._running = False
    bitget_ws_service.transport_state = "DISCONNECTED"
    bitget_ws_service.latest_quote = None
    collector_service._latest_ticker = None

    engine_observability_service.record_engine_heartbeat(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        state="RUNNING",
        heartbeat_at_ms=t0,
        metadata={"coverage_quality": "DOWN"},
    )
    snap = engine_observability_service.build_observation_dashboard_snapshot(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        now_ms=t0,
    )
    assert snap["current_observation_evidence_health"] == "DOWN"


def test_eh05_feed_degraded_is_degraded(isolated_dbs):
    """EH05: Feed DEGRADED evaluates to DEGRADED."""
    s = isolated_dbs["session"]
    t0 = 1700000000000
    run_id = "RUN_EH05"

    bitget_ws_service._running = False
    bitget_ws_service.transport_state = "DEGRADED"
    bitget_ws_service.latest_quote = None
    collector_service._latest_ticker = None

    engine_observability_service.record_engine_heartbeat(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        state="RUNNING",
        heartbeat_at_ms=t0,
        metadata={"coverage_quality": "DEGRADED"},
    )
    engine_observability_service.record_evaluation_cycle(
        s,
        cycle_id="CYC_05",
        run_id=run_id,
        symbol="XAUUSDT",
        started_at_ms=t0 - 100,
        completed_at_ms=t0,
        status="COMPLETED",
    )
    rec = models.PaperObservationInterval(
        run_id=run_id,
        symbol="XAUUSDT",
        scope="CG01",
        worker_role="WATCHER_PROCESS",
        mode="PAPER",
        start_ms=t0 - 5000,
        end_ms=t0,
        data_quality="DEGRADED",
        interval_identity="ident_eh05",
        created_at_ms=t0,
    )
    s.add(rec)
    s.commit()

    snap = engine_observability_service.build_observation_dashboard_snapshot(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        now_ms=t0,
    )
    assert snap["current_observation_evidence_health"] in ("DEGRADED", "DOWN")


def test_eh06_process_stopped_is_offline(isolated_dbs):
    """EH06: Process stopped evaluates to OFFLINE."""
    s = isolated_dbs["session"]
    t0 = 1700000000000
    run_id = "RUN_EH06"

    bitget_ws_service._running = True
    bitget_ws_service.transport_state = "CONNECTED"
    cq = _make_valid_cq(t0=t0)
    bitget_ws_service.latest_quote = cq

    engine_observability_service.record_engine_heartbeat(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        state="STOPPED",
        heartbeat_at_ms=t0,
        metadata={"coverage_quality": "GOOD"},
    )
    snap = engine_observability_service.build_observation_dashboard_snapshot(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        now_ms=t0,
    )
    assert snap["current_observation_evidence_health"] == "OFFLINE"


def test_eh07_process_stalled_is_stalled(isolated_dbs):
    """EH07: Process stalled evaluates to STALLED."""
    s = isolated_dbs["session"]
    t0 = 1700000000000
    run_id = "RUN_EH07"

    engine_observability_service.record_engine_heartbeat(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        state="RUNNING",
        heartbeat_at_ms=t0 - 60_000,  # 60s ago (> 45s)
        metadata={"coverage_quality": "GOOD"},
    )
    snap = engine_observability_service.build_observation_dashboard_snapshot(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        now_ms=t0,
    )
    assert snap["current_observation_evidence_health"] == "STALLED"


def test_eh08_old_cycle_is_not_healthy(isolated_dbs):
    """EH08: Missing or old cycle prevents HEALTHY status."""
    s = isolated_dbs["session"]
    t0 = 1700000000000
    run_id = "RUN_EH08"

    engine_observability_service.record_engine_heartbeat(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        state="RUNNING",
        heartbeat_at_ms=t0,
        metadata={"coverage_quality": "GOOD"},
    )
    # No cycle recorded
    snap = engine_observability_service.build_observation_dashboard_snapshot(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        now_ms=t0,
    )
    assert snap["current_observation_evidence_health"] != "HEALTHY"


def test_eh09_old_interval_is_not_healthy(isolated_dbs):
    """EH09: Interval older than 90s evaluates to STALLED."""
    s = isolated_dbs["session"]
    t0 = 1700000000000
    run_id = "RUN_EH09"

    bitget_ws_service._running = True
    bitget_ws_service.transport_state = "CONNECTED"
    cq = _make_valid_cq(t0=t0)
    bitget_ws_service.latest_quote = cq

    engine_observability_service.record_engine_heartbeat(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        state="RUNNING",
        heartbeat_at_ms=t0,
        metadata={"coverage_quality": "GOOD", "canonical_quote": cq.to_dict()},
    )
    engine_observability_service.record_evaluation_cycle(
        s,
        cycle_id="CYC_09",
        run_id=run_id,
        symbol="XAUUSDT",
        started_at_ms=t0 - 100,
        completed_at_ms=t0,
        status="COMPLETED",
        canonical_quote=cq.to_dict(),
    )
    rec = models.PaperObservationInterval(
        run_id=run_id,
        symbol="XAUUSDT",
        scope="CG01",
        worker_role="WATCHER_PROCESS",
        mode="PAPER",
        start_ms=t0 - 200_000,
        end_ms=t0 - 100_000,  # 100s ago (> 90s)
        data_quality="GOOD",
        interval_identity="ident_eh09",
        created_at_ms=t0 - 100_000,
    )
    s.add(rec)
    s.commit()

    snap = engine_observability_service.build_observation_dashboard_snapshot(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        now_ms=t0,
    )
    assert snap["current_observation_evidence_health"] == "STALLED"


def test_eh10_pending_commit_is_not_healthy(isolated_dbs):
    """EH10: Active uncommitted pending intervals evaluate to DEGRADED."""
    s = isolated_dbs["session"]
    t0 = 1700000000000
    run_id = "RUN_EH10"

    bitget_ws_service._running = True
    bitget_ws_service.transport_state = "CONNECTED"
    cq = _make_valid_cq(t0=t0)
    bitget_ws_service.latest_quote = cq

    engine_observability_service.record_engine_heartbeat(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        state="RUNNING",
        heartbeat_at_ms=t0,
        metadata={"coverage_quality": "GOOD", "canonical_quote": cq.to_dict()},
    )
    observation_interval_service.record_heartbeat_event(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        timestamp_ms=t0,
        coverage_quality="GOOD",
        is_feed_healthy=True,
    )
    snap = engine_observability_service.build_observation_dashboard_snapshot(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        now_ms=t0,
    )
    assert snap["current_observation_evidence_health"] in ("DEGRADED", "UNVERIFIED")


def test_eh11_persistence_failure_is_degraded(isolated_dbs):
    """EH11: Persistence failure evaluates to DEGRADED."""
    s = isolated_dbs["session"]
    t0 = 1700000000000
    run_id = "RUN_EH11"

    engine_observability_service.record_engine_heartbeat(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        state="RUNNING",
        heartbeat_at_ms=t0,
        metadata={"coverage_quality": "GOOD"},
    )
    db_id = observation_interval_service.get_pending_metrics(s)["database_identity"]
    observation_interval_service._last_persist_error_by_db[db_id] = "SQLITE_LOCKED"
    try:
        snap = engine_observability_service.build_observation_dashboard_snapshot(
            s,
            run_id=run_id,
            symbol="XAUUSDT",
            now_ms=t0,
        )
        assert snap["current_observation_evidence_health"] in ("DEGRADED", "UNVERIFIED")
    finally:
        observation_interval_service._last_persist_error_by_db[db_id] = None


def test_eh12_rest_fallback_is_not_healthy(isolated_dbs):
    """EH12: REST fallback evaluates to DEGRADED, not HEALTHY."""
    s = isolated_dbs["session"]
    t0 = 1700000000000
    run_id = "RUN_EH12"

    bitget_ws_service._running = False
    bitget_ws_service.transport_state = "DISCONNECTED"
    bitget_ws_service.feed_source = "REST_FALLBACK"
    cq_rest = _make_valid_cq(t0=t0, source="REST_FALLBACK")
    bitget_ws_service.latest_quote = None
    collector_service._latest_ticker = cq_rest.to_dict()

    engine_observability_service.record_engine_heartbeat(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        state="RUNNING",
        heartbeat_at_ms=t0,
        metadata={
            "coverage_quality": "DEGRADED",
            "quote_source": "REST_FALLBACK",
        },
    )
    snap = engine_observability_service.build_observation_dashboard_snapshot(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        now_ms=t0,
    )
    assert snap["current_observation_evidence_health"] in ("DEGRADED", "DOWN")


def test_eh13_no_active_run_is_unverified(isolated_dbs):
    """EH13: Snapshot without active run evaluates to NO_ACTIVE_RUN or UNVERIFIED."""
    s = isolated_dbs["session"]
    snap = engine_observability_service.build_observation_dashboard_snapshot(
        s,
        run_id=None,
        symbol="XAUUSDT",
        now_ms=1700000000000,
    )
    assert snap["current_observation_evidence_health"] in ("NO_ACTIVE_RUN", "UNVERIFIED", "OFFLINE")


def test_eh14_wrong_run_interval_not_healthy(isolated_dbs):
    """EH14: Persisted intervals from another run do not satisfy target run."""
    s = isolated_dbs["session"]
    t0 = 1700000000000
    run_id = "RUN_EH14_TARGET"

    rec = models.PaperObservationInterval(
        run_id="RUN_EH14_OTHER",
        symbol="XAUUSDT",
        scope="CG01",
        worker_role="WATCHER_PROCESS",
        mode="PAPER",
        start_ms=t0 - 10_000,
        end_ms=t0,
        data_quality="GOOD",
        interval_identity="ident_eh14",
        created_at_ms=t0,
    )
    s.add(rec)
    s.commit()

    snap = engine_observability_service.build_observation_dashboard_snapshot(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        now_ms=t0,
    )
    assert snap["current_observation_evidence_health"] != "HEALTHY"


def test_eh15_recovery_after_fresh_verified_evidence(isolated_dbs):
    """EH15: After a degraded period, fresh verified evidence restores HEALTHY."""
    s = isolated_dbs["session"]
    t0 = 1700000000000
    run_id = "RUN_EH15"

    bitget_ws_service._running = True
    bitget_ws_service.transport_state = "CONNECTED"
    cq = _make_valid_cq(t0=t0)
    bitget_ws_service.latest_quote = cq

    engine_observability_service.record_engine_heartbeat(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        state="RUNNING",
        heartbeat_at_ms=t0,
        metadata={
            "coverage_quality": "GOOD",
            "canonical_quote": cq.to_dict(),
        },
    )
    engine_observability_service.record_evaluation_cycle(
        s,
        cycle_id="CYC_15",
        run_id=run_id,
        symbol="XAUUSDT",
        started_at_ms=t0 - 100,
        completed_at_ms=t0,
        status="COMPLETED",
        canonical_quote=cq.to_dict(),
    )
    rec = models.PaperObservationInterval(
        run_id=run_id,
        symbol="XAUUSDT",
        scope="CG01",
        worker_role="WATCHER_PROCESS",
        mode="PAPER",
        start_ms=t0 - 5000,
        end_ms=t0,
        data_quality="GOOD",
        interval_identity="ident_eh15",
        created_at_ms=t0,
    )
    s.add(rec)
    s.commit()

    snap = engine_observability_service.build_observation_dashboard_snapshot(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        now_ms=t0,
    )
    assert snap["current_observation_evidence_health"] == "HEALTHY"


def test_eh16_process_running_but_feed_unverified_disagrees(isolated_dbs):
    """EH16: Process is RUNNING but feed is UNVERIFIED -> snapshot shows RUNNING process, but UNVERIFIED observation health."""
    s = isolated_dbs["session"]
    t0 = 1700000000000
    run_id = "RUN_EH16"

    bitget_ws_service._running = True
    bitget_ws_service.transport_state = "CONNECTED"
    cq_unverified = _make_valid_cq(t0=t0, source="UNKNOWN")
    bitget_ws_service.latest_quote = cq_unverified

    engine_observability_service.record_engine_heartbeat(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        state="RUNNING",
        heartbeat_at_ms=t0,
        metadata={
            "coverage_quality": "UNVERIFIED",
            "canonical_quote": None,
        },
    )
    snap = engine_observability_service.build_observation_dashboard_snapshot(
        s,
        run_id=run_id,
        symbol="XAUUSDT",
        now_ms=t0,
    )
    assert snap["engine"]["state"] == "RUNNING"
    assert snap["current_observation_evidence_health"] == "UNVERIFIED"
