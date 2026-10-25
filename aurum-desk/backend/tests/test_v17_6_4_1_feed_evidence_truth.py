"""
backend/tests/test_v17_6_4_1_feed_evidence_truth.py
AURUM V17.6.4.1 — Feed Evidence Truth & Fail-Closed Quality Test Suite (EVID-01, Parts 021-038).

Cases:
- FE01: Missing feed metadata produces UNVERIFIED / fail-closed, NO GOOD interval
- FE02: WebSocket connected, quote missing produces DEGRADED / UNVERIFIED, NO GOOD interval
- FE03: WebSocket connected, quote stale produces DEGRADED
- FE04: Disconnected transport produces DEGRADED / DOWN, NO GOOD interval
- FE05: Structurally invalid quote (bid <= 0, ask < bid, nan) produces DEGRADED / UNVERIFIED
- FE06: Valid fresh canonical quote produces GOOD interval candidate
- FE07: Future quote timestamp (> 60s clock skew) fails closed
- FE08: Wrong instrument (not XAUUSDT) fails closed
- FE09: REST fallback valid but WS disconnected produces DEGRADED, not full GOOD
- FE10: Feed recovery from stale to fresh transitions correctly
- FE11: Watcher heartbeat integration passes verified quote quality to intervals
- FE12: Scheduler heartbeat integration passes verified quote quality to intervals
"""
import math
import time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from services.engine_observability_service import (
    EngineObservabilityService,
    ObservationEvidenceQuality,
    resolve_observation_evidence_quality,
)
from services.observation_interval_service import ObservationIntervalService


@pytest.fixture
def test_db():
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


def test_fe01_missing_feed_metadata_fails_closed(test_db):
    """FE01: Heartbeat without feed metadata must NEVER generate a GOOD interval."""
    session, _ = test_db
    obs_svc = EngineObservabilityService()
    interval_svc = ObservationIntervalService()
    
    t0 = 1_700_000_000_000
    # Calling record_engine_heartbeat with NO feed metadata
    obs_svc.record_engine_heartbeat(
        session,
        symbol="XAUUSDT",
        run_id="RUN_FE01",
        state="RUNNING",
        heartbeat_at_ms=t0,
        metadata={"worker_role": "WATCHER_PROCESS"},
    )
    obs_svc.record_engine_heartbeat(
        session,
        symbol="XAUUSDT",
        run_id="RUN_FE01",
        state="RUNNING",
        heartbeat_at_ms=t0 + 15_000,
        metadata={"worker_role": "WATCHER_PROCESS"},
    )
    
    from services.observation_interval_service import observation_interval_service
    observation_interval_service.flush_active_intervals(session)

    rows = session.query(models.PaperObservationInterval).filter_by(run_id="RUN_FE01").all()
    # Any persisted interval MUST NOT be GOOD
    for r in rows:
        assert r.data_quality != "GOOD", f"Missing feed evidence produced false GOOD interval: {r.id}"


def test_fe02_ws_connected_quote_missing_not_good():
    """FE02: WS connected but quote is None/missing -> must resolve to UNVERIFIED or DEGRADED, not GOOD."""
    q_res = resolve_observation_evidence_quality(
        process_heartbeat="RUNNING",
        feed_health={
            "transport_state": "CONNECTED",
            "freshness_state": "CONNECTED_STALE",
            "last_quote_at_ms": None,
            "quote_age_sec": None,
            "bid": None,
            "ask": None,
        },
        symbol="XAUUSDT",
    )
    assert q_res.quality in ("UNVERIFIED", "DEGRADED"), f"Expected UNVERIFIED/DEGRADED, got {q_res.quality}"
    assert q_res.quality != "GOOD"
    assert "QUOTE" in q_res.reason_code


def test_fe03_ws_connected_quote_stale():
    """FE03: WS connected but quote age > threshold -> resolves to DEGRADED."""
    now_ms = 1_700_000_045_000
    q_res = resolve_observation_evidence_quality(
        process_heartbeat="RUNNING",
        feed_health={
            "transport_state": "CONNECTED",
            "freshness_state": "CONNECTED_STALE",
            "last_quote_at_ms": 1_700_000_000_000,
            "quote_age_sec": 45.0,
            "bid": 2000.0,
            "ask": 2000.5,
        },
        now_ms=now_ms,
        symbol="XAUUSDT",
    )
    assert q_res.quality == "DEGRADED"
    assert "STALE" in q_res.reason_code


def test_fe04_disconnected_transport():
    """FE04: Disconnected transport -> resolves to DEGRADED or DOWN."""
    q_res = resolve_observation_evidence_quality(
        process_heartbeat="RUNNING",
        feed_health={
            "transport_state": "DISCONNECTED",
            "freshness_state": "DISCONNECTED",
            "bid": 2000.0,
            "ask": 2000.5,
        },
        symbol="XAUUSDT",
    )
    assert q_res.quality in ("DEGRADED", "DOWN")
    assert q_res.quality != "GOOD"


def test_fe05_structurally_invalid_quote():
    """FE05: Non-positive bid, crossed book (ask < bid), or NaN -> fails closed."""
    # Crossed book
    res1 = resolve_observation_evidence_quality(
        process_heartbeat="RUNNING",
        feed_health={
            "transport_state": "CONNECTED",
            "freshness_state": "CONNECTED_FRESH",
            "quote_age_sec": 1.0,
            "bid": 2005.0,
            "ask": 2000.0,
        },
        symbol="XAUUSDT",
    )
    assert res1.quality != "GOOD"
    assert "CROSSED" in res1.reason_code or "INVALID" in res1.reason_code

    # Non-finite / NaN
    res2 = resolve_observation_evidence_quality(
        process_heartbeat="RUNNING",
        feed_health={
            "transport_state": "CONNECTED",
            "freshness_state": "CONNECTED_FRESH",
            "quote_age_sec": 1.0,
            "bid": float("nan"),
            "ask": 2000.0,
        },
        symbol="XAUUSDT",
    )
    assert res2.quality != "GOOD"


def test_fe06_valid_fresh_canonical_quote():
    """FE06: Connected WS + fresh valid quote -> resolves to GOOD candidate."""
    now_ms = 1_700_000_000_500
    q_res = resolve_observation_evidence_quality(
        process_heartbeat="RUNNING",
        feed_health={
            "transport_state": "CONNECTED",
            "freshness_state": "CONNECTED_FRESH",
            "quote_source": "WS",
            "quote_age_sec": 0.5,
            "bid": 2050.10,
            "ask": 2050.25,
            "last_quote_at_ms": 1_700_000_000_000,
        },
        now_ms=now_ms,
        symbol="XAUUSDT",
        evidence_origin="LIVE_FORWARD",
    )
    assert q_res.quality == "GOOD"
    assert q_res.quote_valid is True
    assert q_res.reason_code == "FEED_VERIFIED_FRESH"


def test_fe07_future_quote_timestamp():
    """FE07: Future timestamp > tolerance -> clock skew error, not GOOD."""
    now_ms = 1_700_000_000_000
    q_res = resolve_observation_evidence_quality(
        process_heartbeat="RUNNING",
        feed_health={
            "transport_state": "CONNECTED",
            "freshness_state": "CONNECTED_FRESH",
            "quote_source": "WS",
            "last_quote_at_ms": now_ms + 120_000,  # 2 minutes in future
            "quote_age_sec": -120.0,
            "bid": 2050.10,
            "ask": 2050.25,
        },
        symbol="XAUUSDT",
        now_ms=now_ms,
    )
    assert q_res.quality != "GOOD"
    assert "CLOCK_SKEW" in q_res.reason_code or "FUTURE" in q_res.reason_code


def test_fe08_wrong_instrument():
    """FE08: Wrong symbol quote evidence -> not GOOD for XAUUSDT."""
    q_res = resolve_observation_evidence_quality(
        process_heartbeat="RUNNING",
        feed_health={
            "symbol": "BTCUSDT",
            "transport_state": "CONNECTED",
            "freshness_state": "CONNECTED_FRESH",
            "quote_age_sec": 0.5,
            "bid": 50000.0,
            "ask": 50001.0,
        },
        symbol="XAUUSDT",
    )
    assert q_res.quality != "GOOD"
    assert "SYMBOL_MISMATCH" in q_res.reason_code


def test_fe09_rest_fallback_valid_but_ws_disconnected():
    """FE09: WS disconnected, REST fallback is valid -> DEGRADED, not full GOOD."""
    q_res = resolve_observation_evidence_quality(
        process_heartbeat="RUNNING",
        feed_health={
            "transport_state": "DISCONNECTED",
            "quote_source": "REST_FALLBACK",
            "freshness_state": "CONNECTED_FRESH",
            "quote_age_sec": 2.0,
            "bid": 2050.0,
            "ask": 2050.5,
        },
        symbol="XAUUSDT",
    )
    assert q_res.quality == "DEGRADED"
    assert "REST_FALLBACK" in q_res.reason_code or "TRANSPORT" in q_res.reason_code


def test_fe10_feed_recovery():
    """FE10: Feed degrades then recovers -> intervals reflect degraded followed by good."""
    interval_svc = ObservationIntervalService(max_gap_ms=45_000)
    t0 = 1_700_000_000_000

    # 12:00:00 GOOD
    interval_svc.record_heartbeat_event(
        None, run_id="RUN_FE10", timestamp_ms=t0,
        is_feed_healthy=True, is_quote_fresh=True, coverage_quality="GOOD"
    )
    # 12:00:15 DEGRADED
    interval_svc.record_heartbeat_event(
        None, run_id="RUN_FE10", timestamp_ms=t0 + 15_000,
        is_feed_healthy=False, is_quote_fresh=False, coverage_quality="DEGRADED"
    )
    # 12:00:30 DEGRADED
    interval_svc.record_heartbeat_event(
        None, run_id="RUN_FE10", timestamp_ms=t0 + 30_000,
        is_feed_healthy=False, is_quote_fresh=False, coverage_quality="DEGRADED"
    )
    # 12:00:45 GOOD
    interval_svc.record_heartbeat_event(
        None, run_id="RUN_FE10", timestamp_ms=t0 + 45_000,
        is_feed_healthy=True, is_quote_fresh=True, coverage_quality="GOOD"
    )

    # Active interval should now be GOOD
    key = ("RUN_FE10", "XAUUSDT", "CG01", "WATCHER_PROCESS", "LIVE_FORWARD", None)
    with interval_svc._lock:
        active = None
        for k, v in interval_svc._active_intervals.items():
            if k[0] == "RUN_FE10":
                active = v
                break
        assert active is not None
        assert active["data_quality"] == "GOOD"


def test_fe11_watcher_heartbeat_integration(test_db):
    """FE11: Watcher loop records heartbeat with feed quality evidence."""
    session, _ = test_db
    obs_svc = EngineObservabilityService()

    # Pass explicit feed health snapshot in metadata
    obs_svc.record_engine_heartbeat(
        session,
        symbol="XAUUSDT",
        run_id="RUN_FE11",
        state="RUNNING",
        heartbeat_at_ms=1_700_000_000_000,
        metadata={
            "worker_role": "WATCHER_PROCESS",
            "is_feed_healthy": True,
            "is_quote_fresh": True,
            "quote_age_sec": 0.4,
            "transport_state": "CONNECTED",
            "coverage_quality": "GOOD",
        },
    )
    from services.observation_interval_service import observation_interval_service
    with observation_interval_service._lock:
        active = None
        for k, v in observation_interval_service._active_intervals.items():
            if k[0] == "RUN_FE11":
                active = v
                break
        assert active is not None
        assert active["data_quality"] == "GOOD"


def test_fe12_scheduler_heartbeat_integration(test_db):
    """FE12: Scheduler heartbeat also requires verified feed quality."""
    session, _ = test_db
    obs_svc = EngineObservabilityService()

    # Scheduler heartbeat with UNVERIFIED feed quality
    obs_svc.record_engine_heartbeat(
        session,
        symbol="XAUUSDT",
        run_id="RUN_FE12",
        state="RUNNING",
        heartbeat_at_ms=1_700_000_000_000,
        metadata={
            "worker_role": "SCHEDULER",
            "is_feed_healthy": False,
            "is_quote_fresh": False,
            "coverage_quality": "UNVERIFIED",
        },
    )
    from services.observation_interval_service import observation_interval_service
    with observation_interval_service._lock:
        active = None
        for k, v in observation_interval_service._active_intervals.items():
            if k[0] == "RUN_FE12":
                active = v
                break
        assert active is not None
        assert active["data_quality"] != "GOOD"
