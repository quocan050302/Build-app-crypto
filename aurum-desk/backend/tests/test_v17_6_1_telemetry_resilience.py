"""
backend/tests/test_v17_6_1_telemetry_resilience.py
V17.6.1 OBS-03, OBS-04 & OBS-12: Heartbeat Durability, Incident Recovery & Worker Affinity.
"""

import pytest
import time
from datetime import datetime
from unittest.mock import patch
import zoneinfo
from services.engine_observability_service import (
    EngineObservabilityService,
    engine_observability_service,
)
from services.quote_validator import CanonicalQuote
import models
from database import SessionLocal

TZ_NY = zoneinfo.ZoneInfo("America/New_York")


def test_res_01_heartbeat_persists_without_caller_session():
    """OBS-03: record_engine_heartbeat opens SessionLocal when db=None and commits durably."""
    svc = EngineObservabilityService()
    inst_id = f"worker-durability-{int(time.time() * 1000)}"
    now_ms = 1792591200000

    rec = svc.record_engine_heartbeat(
        None,  # db is None
        engine_instance_id=inst_id,
        symbol="XAUUSDT",
        state="RUNNING",
        heartbeat_at_ms=now_ms,
        cadence_sec=15,
    )
    assert rec["engine_instance_id"] == inst_id

    # Verify committed in fresh independent session
    verify_db = SessionLocal()
    try:
        row = verify_db.query(models.EngineHeartbeatRecord).filter(
            models.EngineHeartbeatRecord.engine_instance_id == inst_id
        ).first()
        assert row is not None
        assert row.state == "RUNNING"
        assert row.last_process_heartbeat_at_ms == now_ms
    finally:
        verify_db.close()


def test_res_02_evaluation_cycle_persists_without_caller_session():
    """OBS-03: record_evaluation_cycle with db=None updates and commits heartbeat record."""
    svc = EngineObservabilityService()
    inst_id = f"worker-cycle-{int(time.time() * 1000)}"
    now_ms = 1792591200000
    cycle_id = f"cycle-{inst_id}"

    entry = svc.record_evaluation_cycle(
        None,  # db is None
        cycle_id=cycle_id,
        engine_instance_id=inst_id,
        started_at_ms=now_ms - 20,
        completed_at_ms=now_ms,
        status="COMPLETED",
        symbol="XAUUSDT",
        duration_ms=20.0,
    )
    assert entry["cycle_id"] == cycle_id

    # Verify committed in fresh independent session
    verify_db = SessionLocal()
    try:
        row = verify_db.query(models.EngineHeartbeatRecord).filter(
            models.EngineHeartbeatRecord.engine_instance_id == inst_id
        ).first()
        assert row is not None
        assert row.last_cycle_id == cycle_id
        assert row.last_successful_evaluation_at_ms == now_ms
        assert row.cycles_count >= 1
    finally:
        verify_db.close()


def test_res_03_incident_durable_persistence_and_bulk_recovery():
    """OBS-12: Operational incidents persist with db=None and auto-recover in bulk."""
    svc = EngineObservabilityService()
    now_ms = 1792591200000
    inc_id = f"inc-feed-test-{int(time.time() * 1000)}"

    # 1. Raise incident with db=None
    inc = svc.record_engine_incident(
        None,
        severity="WARNING",
        component="MARKET_FEED",
        reason_code="FEED_QUOTE_STALE",
        message="Quote age exceeded threshold",
        incident_id=inc_id,
        now_ms=now_ms,
    )
    assert inc["status"] == "ACTIVE"

    # Verify active in DB
    verify_db = SessionLocal()
    try:
        row = verify_db.query(models.EngineIncidentRecord).filter(
            models.EngineIncidentRecord.id == inc_id
        ).first()
        assert row is not None
        assert row.status == "ACTIVE"
    finally:
        verify_db.close()

    # 2. Bulk resolve active incidents when feed recovers
    resolved_count = svc.resolve_active_incidents(
        None,
        component="MARKET_FEED",
        reason_code="FEED_QUOTE_STALE",
        now_ms=now_ms + 5000,
    )
    assert resolved_count >= 1

    # Verify status changed to RECOVERED in fresh session
    verify_db2 = SessionLocal()
    try:
        row2 = verify_db2.query(models.EngineIncidentRecord).filter(
            models.EngineIncidentRecord.id == inc_id
        ).first()
        assert row2 is not None
        assert row2.status == "RECOVERED"
        assert row2.recovered_at_ms == now_ms + 5000
    finally:
        verify_db2.close()


def test_res_04_process_stall_vs_evaluation_stall_differentiation():
    """OBS-04: Distinguishes between dead process heartbeat and valid market closure."""
    svc = EngineObservabilityService()
    now_ms = 1792591200000  # Wednesday 14:00 UTC (Market open)

    # 1. Stale process heartbeat (> 45s) -> STALLED
    hb_stale_process = models.EngineHeartbeatRecord(
        engine_instance_id="worker-dead",
        symbol="XAUUSDT",
        state="RUNNING",
        last_process_heartbeat_at_ms=now_ms - 60_000,  # 60s ago
        cadence_sec=15,
    )
    is_stalled, reason = svc.detect_stalled_engine(
        heartbeat_record=hb_stale_process,
        now_ms=now_ms,
        feed_health={"freshness_state": "CONNECTED_FRESH"},
    )
    assert is_stalled is True
    assert "HEARTBEAT_STALE" in reason

    # 2. Alive process heartbeat, healthy feed, but cycle hangs (> 90s) -> EVALUATION_CYCLE_STALLED
    hb_stale_eval = models.EngineHeartbeatRecord(
        engine_instance_id="worker-cycle-stuck",
        symbol="XAUUSDT",
        state="RUNNING",
        last_process_heartbeat_at_ms=now_ms - 2000,  # Fresh heartbeat
        last_evaluation_completed_at_ms=now_ms - 100_000,  # 100s ago
        cadence_sec=15,
    )
    is_stalled_eval, reason_eval = svc.detect_stalled_engine(
        heartbeat_record=hb_stale_eval,
        now_ms=now_ms,
        feed_health={"freshness_state": "CONNECTED_FRESH"},
    )
    assert is_stalled_eval is True
    assert "EVALUATION_CYCLE_STALLED" in reason_eval

    # 3. Alive process heartbeat during daily maintenance -> NOT STALLED
    dt_maint = datetime(2026, 10, 21, 17, 30, tzinfo=TZ_NY)
    t_maint = int(dt_maint.timestamp() * 1000)

    hb_maint = models.EngineHeartbeatRecord(
        engine_instance_id="worker-maint",
        symbol="XAUUSDT",
        state="RUNNING",
        last_process_heartbeat_at_ms=t_maint - 2000,
        last_evaluation_completed_at_ms=t_maint - 100_000,
        cadence_sec=15,
    )
    is_stalled_m, reason_m = svc.detect_stalled_engine(
        heartbeat_record=hb_maint,
        now_ms=t_maint,
        feed_health={"freshness_state": "CONNECTED_FRESH"},
    )
    assert is_stalled_m is False
    assert "DAILY_MAINTENANCE" in reason_m


def test_res_05_multi_worker_instance_id_affinity(isolated_db):
    """OBS-04: Engine snapshot prioritizes heartbeat with current active instance ID."""
    svc = EngineObservabilityService()
    now_ms = 1792591200000

    # Old worker record
    hb_old = models.EngineHeartbeatRecord(
        engine_instance_id="worker-old-999",
        symbol="XAUUSDT",
        state="DEGRADED",
        last_process_heartbeat_at_ms=now_ms - 10_000,
        updated_at_ms=now_ms - 10_000,
    )

    # Current worker record
    hb_current = models.EngineHeartbeatRecord(
        engine_instance_id=svc.current_instance_id,
        symbol="XAUUSDT",
        state="RUNNING",
        last_process_heartbeat_at_ms=now_ms - 1000,
        updated_at_ms=now_ms - 1000,
    )

    isolated_db.add_all([hb_old, hb_current])
    isolated_db.commit()

    with patch("services.bitget_ws_service.bitget_ws_service") as mock_ws:
        mock_ws.transport_state = "CONNECTED"
        mock_ws.latest_quote = CanonicalQuote(
            symbol="XAUUSDT",
            bid=2730.0,
            ask=2730.2,
            last=2730.1,
            exchange_ts_ms=now_ms - 1000,
            received_at_ms=now_ms - 900,
            source="WS",
            connection_epoch=1,
            status="VALID",
        )
        snap = svc.build_engine_health_snapshot(isolated_db, symbol="XAUUSDT", now_ms=now_ms)
        assert snap["engine_instance_id"] == svc.current_instance_id
        assert snap["state"] == "RUNNING"
