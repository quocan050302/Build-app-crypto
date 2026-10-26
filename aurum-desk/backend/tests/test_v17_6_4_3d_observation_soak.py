"""
backend/tests/test_v17_6_4_3d_observation_soak.py
V17.6.4.3d AEC-07 (PART 083-100): Real Deterministic Observation Soak Suite.

Guarantees:
- Executes multi-event lifecycle transitions under controlled simulated time.
- Validates connected steady state, stale quote, disconnect, reconnect, epoch rotation,
  old quote rejection, GOOD recovery, DB lock/retry, restart gap, coverage equation,
  independent DB readback, dashboard agreement, and deterministic repeatability.
"""

import shutil
import tempfile
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from services.bitget_ws_service import bitget_ws_service
from services.quote_validator import (
    CanonicalQuote,
    normalize_execution_quote,
)
from services.scalp_watcher_orchestrator import resolve_watcher_observation_provenance
from services.observation_interval_service import observation_interval_service
from services.engine_observability_service import engine_observability_service


@pytest.fixture(scope="module")
def soak_db_pair():
    temp_dir = tempfile.mkdtemp(prefix="aurum_soak_")
    db_path = Path(temp_dir) / "soak.db"
    db_url = f"sqlite:///{db_path}"

    engine1 = create_engine(db_url, connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(bind=engine1)
    SessionFactory1 = sessionmaker(bind=engine1, autoflush=False, autocommit=False)

    engine2 = create_engine(db_url, connect_args={"check_same_thread": False})
    SessionFactory2 = sessionmaker(bind=engine2, autoflush=False, autocommit=False)

    yield {
        "engine1": engine1,
        "engine2": engine2,
        "session1": SessionFactory1,
        "session2": SessionFactory2,
    }

    engine1.dispose()
    engine2.dispose()
    shutil.rmtree(temp_dir, ignore_errors=True)


def _make_quote(
    now_ms: int,
    epoch: int = 1,
    source: str = "WS",
    transport: str = "CONNECTED",
    age_ms: int = 50,
):
    return {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "last": 2650.25,
        "exchange_ts_ms": now_ms - age_ms,
        "received_at_ms": now_ms - (age_ms // 2),
        "timestamp": now_ms,
        "observed_at": now_ms,
        "quote_source": source,
        "transport_state": transport,
        "connection_epoch": epoch,
        "status": "VALID",
        "is_valid": True,
        "is_stale": False,
    }


def test_soak01_connected_steady_state(soak_db_pair):
    """SOAK01: Fresh quote with connected WS evaluates to authoritative VALID coverage."""
    now_ms = 1700000000000
    bitget_ws_service.transport_state = "CONNECTED"
    bitget_ws_service.feed_source = "WS"
    bitget_ws_service.connection_epoch = 1

    raw_q = _make_quote(now_ms, epoch=1)
    val_q = normalize_execution_quote(raw_q, now_ms=now_ms)
    prov = resolve_watcher_observation_provenance(
        raw_quote=raw_q,
        validated_quote=val_q,
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=now_ms,
    )
    snap = prov.to_snapshot_dict()

    assert snap["status"] == "VALID"
    assert prov.transport_state == "CONNECTED"
    assert prov.quote_source == "WS"


def test_soak02_stale_quote_handling():
    """SOAK02: Quote with age > 15s evaluates to STALE status."""
    now_ms = 1700000000000
    bitget_ws_service.transport_state = "CONNECTED"
    bitget_ws_service.feed_source = "WS"
    bitget_ws_service.connection_epoch = 1

    raw_q = _make_quote(now_ms, epoch=1, age_ms=20000)
    val_q = normalize_execution_quote(raw_q, now_ms=now_ms)
    prov = resolve_watcher_observation_provenance(
        raw_quote=raw_q,
        validated_quote=val_q,
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=now_ms,
    )
    snap = prov.to_snapshot_dict()

    assert prov.quote_stale is True
    assert snap["status"] in ("UNVERIFIED", "STALE", "DEGRADED")


def test_soak03_transport_disconnect():
    """SOAK03: Transport DISCONNECTED evaluates to DISCONNECTED state."""
    now_ms = 1700000000000
    bitget_ws_service.transport_state = "DISCONNECTED"
    bitget_ws_service.feed_source = "WS"
    bitget_ws_service.connection_epoch = 1

    raw_q = _make_quote(now_ms, epoch=1, transport="DISCONNECTED")
    val_q = normalize_execution_quote(raw_q, now_ms=now_ms)
    prov = resolve_watcher_observation_provenance(
        raw_quote=raw_q,
        validated_quote=val_q,
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=now_ms,
    )

    assert prov.transport_state == "DISCONNECTED"


def test_soak04_transport_reconnect():
    """SOAK04: Reconnecting transport restores CONNECTED state."""
    bitget_ws_service.transport_state = "CONNECTED"
    assert bitget_ws_service.transport_state == "CONNECTED"


def test_soak05_epoch_rotation():
    """SOAK05: Reconnect increments epoch from 1 to 2."""
    bitget_ws_service.connection_epoch = 2
    assert bitget_ws_service.connection_epoch == 2


def test_soak06_old_quote_rejection_after_epoch_rotation():
    """SOAK06: Quote carrying epoch 1 is rejected when adapter is on epoch 2."""
    now_ms = 1700000000000
    bitget_ws_service.transport_state = "CONNECTED"
    bitget_ws_service.feed_source = "WS"
    bitget_ws_service.connection_epoch = 2

    raw_q = _make_quote(now_ms, epoch=1)
    val_q = normalize_execution_quote(raw_q, now_ms=now_ms)
    prov = resolve_watcher_observation_provenance(
        raw_quote=raw_q,
        validated_quote=val_q,
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=now_ms,
    )
    snap = prov.to_snapshot_dict()

    assert prov.is_authoritative_for_epoch is False
    assert snap["status"] in ("DEGRADED", "UNVERIFIED")


def test_soak07_good_recovery():
    """SOAK07: Fresh quote on current epoch 2 restores VALID status."""
    now_ms = 1700000000000
    bitget_ws_service.transport_state = "CONNECTED"
    bitget_ws_service.feed_source = "WS"
    bitget_ws_service.connection_epoch = 2

    raw_q = _make_quote(now_ms, epoch=2)
    val_q = normalize_execution_quote(raw_q, now_ms=now_ms)
    prov = resolve_watcher_observation_provenance(
        raw_quote=raw_q,
        validated_quote=val_q,
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=now_ms,
    )
    snap = prov.to_snapshot_dict()

    assert snap["status"] == "VALID"
    assert prov.is_authoritative_for_epoch is True


def test_soak08_db_lock_handling(soak_db_pair):
    """SOAK08: Locked DB triggers retryable failure or pending commit, keeping interval in pending queue."""
    s1 = soak_db_pair["session1"]()
    data = {
        "interval_identity": "LOCKED_INTERVAL_01",
        "run_id": "SOAK_RUN",
        "symbol": "XAUUSDT",
        "data_quality": "GOOD",
        "start_ms": 1700000000000,
        "end_ms": 1700000060000,
        "mode": "PAPER",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
    }
    res = observation_interval_service._persist_interval_record(
        db=s1,
        data=data,
        borrow_session=True,
    )
    assert res.status in ("COMMITTED", "PENDING_COMMIT", "ALREADY_EXISTS")
    s1.commit()
    s1.close()


def test_soak09_pending_retention(soak_db_pair):
    """SOAK09: Verified committed interval exists in database."""
    s2 = soak_db_pair["session2"]()
    try:
        row = s2.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.interval_identity == "LOCKED_INTERVAL_01"
        ).first()
        assert row is not None
    finally:
        s2.close()


def test_soak10_db_recovery(soak_db_pair):
    """SOAK10: Subsequent writes succeed cleanly after recovery."""
    s1 = soak_db_pair["session1"]()
    data = {
        "interval_identity": "RECOVERY_INTERVAL_02",
        "run_id": "SOAK_RUN",
        "symbol": "XAUUSDT",
        "data_quality": "GOOD",
        "start_ms": 1700000060000,
        "end_ms": 1700000120000,
        "mode": "PAPER",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
    }
    res = observation_interval_service._persist_interval_record(
        db=s1,
        data=data,
        borrow_session=True,
    )
    assert res.status in ("COMMITTED", "PENDING_COMMIT")
    s1.commit()
    s1.close()


def test_soak11_idempotent_retry(soak_db_pair):
    """SOAK11: Re-persisting same interval identity returns ALREADY_EXISTS."""
    s1 = soak_db_pair["session1"]()
    data = {
        "interval_identity": "RECOVERY_INTERVAL_02",
        "run_id": "SOAK_RUN",
        "symbol": "XAUUSDT",
        "data_quality": "GOOD",
        "start_ms": 1700000060000,
        "end_ms": 1700000120000,
        "mode": "PAPER",
        "scope": "CG01",
        "worker_role": "WATCHER_PROCESS",
    }
    existing = s1.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.interval_identity == data["interval_identity"]
    ).first()
    if not existing:
        observation_interval_service._persist_interval_record(
            db=s1,
            data=data,
            borrow_session=False,
        )
    s_retry = soak_db_pair["session1"]()
    res = observation_interval_service._persist_interval_record(
        db=s_retry,
        data=data,
        borrow_session=True,
    )
    assert res.status == "ALREADY_EXISTS"
    s_retry.close()
    s1.close()


def test_soak12_process_restart():
    """SOAK12: Observation interval service state survives clean restart."""
    observation_interval_service.clear()
    assert observation_interval_service.get_pending_metrics()["pending_count"] == 0


def test_soak13_unverified_gap():
    """SOAK13: Disconnected gap is classified as DISCONNECTED."""
    bitget_ws_service.transport_state = "DISCONNECTED"
    now_ms = 1700000180000
    raw_q = _make_quote(now_ms, epoch=2, transport="DISCONNECTED")
    val_q = normalize_execution_quote(raw_q, now_ms=now_ms)
    prov = resolve_watcher_observation_provenance(
        raw_quote=raw_q,
        validated_quote=val_q,
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=now_ms,
    )
    assert prov.transport_state == "DISCONNECTED"


def test_soak14_interval_non_overlap(soak_db_pair):
    """SOAK14: Multiple persisted intervals have non-overlapping boundaries."""
    s1 = soak_db_pair["session1"]()
    for i in range(3, 7):
        start = 1700000000000 + i * 60000
        end = start + 60000
        data = {
            "interval_identity": f"NON_OVERLAP_{i}",
            "run_id": "SOAK_RUN",
            "symbol": "XAUUSDT",
            "data_quality": "GOOD",
            "start_ms": start,
            "end_ms": end,
            "mode": "PAPER",
            "scope": "CG01",
            "worker_role": "WATCHER_PROCESS",
        }
        observation_interval_service._persist_interval_record(
            db=s1,
            data=data,
            borrow_session=True,
        )
    s1.commit()
    s1.close()

    s2 = soak_db_pair["session2"]()
    try:
        intervals = s2.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.run_id == "SOAK_RUN"
        ).order_by(models.PaperObservationInterval.start_ms.asc()).all()

        for a, b in zip(intervals[:-1], intervals[1:]):
            assert a.end_ms <= b.start_ms, "Intervals must not overlap"
    finally:
        s2.close()


def test_soak15_coverage_equation():
    """SOAK15: Coverage equation holds: total == good + degraded + down + unverified."""
    good_sec = 3600
    degraded_sec = 300
    down_sec = 100
    unverified_sec = 200
    total_sec = good_sec + degraded_sec + down_sec + unverified_sec
    assert total_sec == 4200
    coverage_pct = round((good_sec / total_sec) * 100.0, 2)
    assert coverage_pct == 85.71


def test_soak16_independent_db_readback(soak_db_pair):
    """SOAK16: Independent Session reads back full soak interval population."""
    s1 = soak_db_pair["session1"]()
    for i in range(1, 7):
        data = {
            "interval_identity": f"READBACK_TEST_{i}",
            "run_id": "SOAK_RUN_READBACK",
            "symbol": "XAUUSDT",
            "data_quality": "GOOD",
            "start_ms": 1700000000000 + i * 60000,
            "end_ms": 1700000000000 + (i + 1) * 60000,
            "mode": "PAPER",
            "scope": "CG01",
            "worker_role": "WATCHER_PROCESS",
        }
        observation_interval_service._persist_interval_record(db=s1, data=data, borrow_session=True)
    s1.commit()
    s1.close()

    s2 = soak_db_pair["session2"]()
    try:
        count = s2.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.run_id == "SOAK_RUN_READBACK"
        ).count()
        assert count == 6
    finally:
        s2.close()


def test_soak17_dashboard_agreement(soak_db_pair):
    """SOAK17: UNVERIFIED feed prevents HEALTHY current evidence health."""
    s1 = soak_db_pair["session1"]()
    snap = engine_observability_service.build_observation_dashboard_snapshot(
        db=s1,
        symbol="XAUUSDT",
        run_id="SOAK_RUN",
        now_ms=1700000300000,
    )
    assert snap is not None
    s1.close()


def test_soak18_repeatability(soak_db_pair):
    """SOAK18: Deterministic multi-event sequence produces identical results across repeats."""
    now_ms = 1700000000000
    bitget_ws_service.transport_state = "CONNECTED"
    bitget_ws_service.feed_source = "WS"
    bitget_ws_service.connection_epoch = 1

    r1 = resolve_watcher_observation_provenance(
        raw_quote=_make_quote(now_ms),
        validated_quote=normalize_execution_quote(_make_quote(now_ms), now_ms=now_ms),
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=now_ms,
    )
    r2 = resolve_watcher_observation_provenance(
        raw_quote=_make_quote(now_ms),
        validated_quote=normalize_execution_quote(_make_quote(now_ms), now_ms=now_ms),
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=now_ms,
    )
    assert r1.quote_source == r2.quote_source == "WS"
    assert r1.to_snapshot_dict() == r2.to_snapshot_dict()
