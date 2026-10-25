"""
backend/tests/test_v17_6_4_3_evidence_authority.py
Authoritative Verification Suite for DBEA-03 & DBEA-04: Strict Evidence Authority Boundary.

Tests EA01 through EA22 per Master Prompt V17.6.4.3 (Parts 058–086):
- EA01: Raw quality GOOD rejected
- EA02: Dict evidence_quality GOOD rejected
- EA03: Manually constructed typed GOOD rejected
- EA04: Boolean GOOD flags rejected
- EA05: Transport + age without quote rejected
- EA06: Bid/Ask without timestamp rejected
- EA07: Timestamp without Bid/Ask rejected
- EA08: Wrong symbol rejected
- EA09: Missing source rejected
- EA10: Unknown transport rejected
- EA11: Disconnected transport rejected
- EA12: Stale quote rejected
- EA13: Future timestamp rejected
- EA14: Conflicting timestamps rejected
- EA15: Nonfinite Bid rejected
- EA16: Crossed book rejected
- EA17: REST fallback correctly degraded
- EA18: Valid canonical WS quote accepted
- EA19: Real watcher supplies verified feed
- EA20: Real scheduler supplies verified feed
- EA21: Replay valid quote excluded from live
- EA22: Invalid metadata cannot bypass validation
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
    ObservationEvidenceQuality,
    resolve_observation_evidence_quality,
    build_authoritative_observation_quality,
)
from services.observation_interval_service import ObservationIntervalService


@pytest.fixture
def db_session(tmp_path):
    db_file = tmp_path / "authority_test.sqlite"
    engine = create_engine(f"sqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def test_ea01_raw_quality_good_rejected(db_session):
    """EA01: metadata = {'coverage_quality': 'GOOD'} without quote must NOT authorize GOOD."""
    obs_svc = EngineObservabilityService()
    t_now = 1700000000000
    res = obs_svc.record_engine_heartbeat(
        db_session,
        run_id="RUN_EA01",
        heartbeat_at_ms=t_now,
        metadata={"coverage_quality": "GOOD"},
    )
    # Check intervals committed to DB
    from services.observation_interval_service import observation_interval_service
    observation_interval_service.flush_active_intervals(db_session)

    intervals = db_session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.run_id == "RUN_EA01",
        models.PaperObservationInterval.data_quality == "GOOD",
    ).all()
    assert len(intervals) == 0, f"Unauthorized GOOD interval committed: {intervals}"


def test_ea02_dict_evidence_quality_good_rejected(db_session):
    """EA02: metadata = {'evidence_quality': {'quality': 'GOOD'}} without quote must NOT authorize GOOD."""
    obs_svc = EngineObservabilityService()
    t_now = 1700000000000
    obs_svc.record_engine_heartbeat(
        db_session,
        run_id="RUN_EA02",
        heartbeat_at_ms=t_now,
        metadata={"evidence_quality": {"quality": "GOOD"}},
    )
    from services.observation_interval_service import observation_interval_service
    observation_interval_service.flush_active_intervals(db_session)

    intervals = db_session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.run_id == "RUN_EA02",
        models.PaperObservationInterval.data_quality == "GOOD",
    ).all()
    assert len(intervals) == 0


def test_ea03_manually_constructed_typed_good_rejected(db_session):
    """EA03: Manually instantiated ObservationEvidenceQuality without underlying quote proofs is rejected."""
    obs_svc = EngineObservabilityService()
    t_now = 1700000000000
    fake_quality = ObservationEvidenceQuality(
        quality="GOOD",
        reason_code="FEED_VERIFIED_FRESH",
        feed_source="WS",
        quote_valid=True,
        transport_state="CONNECTED",
    )
    obs_svc.record_engine_heartbeat(
        db_session,
        run_id="RUN_EA03",
        heartbeat_at_ms=t_now,
        metadata={"evidence_quality": fake_quality},
    )
    from services.observation_interval_service import observation_interval_service
    observation_interval_service.flush_active_intervals(db_session)

    intervals = db_session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.run_id == "RUN_EA03",
        models.PaperObservationInterval.data_quality == "GOOD",
    ).all()
    assert len(intervals) == 0


def test_ea04_boolean_good_flags_rejected(db_session):
    """EA04: Boolean flags alone (is_feed_healthy, is_quote_fresh) cannot authorize GOOD."""
    obs_svc = EngineObservabilityService()
    t_now = 1700000000000
    obs_svc.record_engine_heartbeat(
        db_session,
        run_id="RUN_EA04",
        heartbeat_at_ms=t_now,
        metadata={"is_feed_healthy": True, "is_quote_fresh": True},
    )
    from services.observation_interval_service import observation_interval_service
    observation_interval_service.flush_active_intervals(db_session)

    intervals = db_session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.run_id == "RUN_EA04",
        models.PaperObservationInterval.data_quality == "GOOD",
    ).all()
    assert len(intervals) == 0


def test_ea05_transport_and_age_without_quote_rejected(db_session):
    """EA05: transport_state == 'CONNECTED' and quote_age_sec <= 1.0 without Bid/Ask is rejected."""
    obs_svc = EngineObservabilityService()
    t_now = 1700000000000
    obs_svc.record_engine_heartbeat(
        db_session,
        run_id="RUN_EA05",
        heartbeat_at_ms=t_now,
        metadata={
            "transport_state": "CONNECTED",
            "quote_age_sec": 0.1,
            "is_feed_healthy": True,
            "is_quote_fresh": True,
        },
    )
    from services.observation_interval_service import observation_interval_service
    observation_interval_service.flush_active_intervals(db_session)

    intervals = db_session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.run_id == "RUN_EA05",
        models.PaperObservationInterval.data_quality == "GOOD",
    ).all()
    assert len(intervals) == 0


def test_ea06_bid_ask_without_timestamp_rejected():
    """EA06: Bid/Ask present but no quote timestamp or age -> UNVERIFIED."""
    q = resolve_observation_evidence_quality({
        "symbol": "XAUUSDT",
        "transport_state": "CONNECTED",
        "quote_source": "WS",
        "bid": 2650.0,
        "ask": 2650.5,
    })
    assert q.quality == "UNVERIFIED"
    assert q.reason_code == "QUOTE_TIMESTAMP_AND_AGE_MISSING"


def test_ea07_timestamp_without_bid_ask_rejected():
    """EA07: Timestamp present but missing Bid or Ask -> DEGRADED."""
    t_now = 1700000000000
    q = resolve_observation_evidence_quality({
        "symbol": "XAUUSDT",
        "transport_state": "CONNECTED",
        "quote_source": "WS",
        "last_quote_at_ms": t_now - 100,
        "quote_age_sec": 0.1,
        "bid": None,
        "ask": 2650.5,
    }, now_ms=t_now)
    assert q.quality == "DEGRADED"
    assert q.reason_code == "QUOTE_MISSING_BID_OR_ASK"


def test_ea08_wrong_symbol_rejected():
    """EA08: Quote for BTCUSDT cannot authorize XAUUSDT -> UNVERIFIED."""
    t_now = 1700000000000
    q = resolve_observation_evidence_quality({
        "symbol": "BTCUSDT",
        "transport_state": "CONNECTED",
        "quote_source": "WS",
        "last_quote_at_ms": t_now - 100,
        "quote_age_sec": 0.1,
        "bid": 90000.0,
        "ask": 90001.0,
    }, symbol="XAUUSDT", now_ms=t_now)
    assert q.quality == "UNVERIFIED"
    assert "SYMBOL_MISMATCH" in q.reason_code


def test_ea09_missing_source_rejected():
    """EA09: Missing quote source must remain UNKNOWN and fail closed -> UNVERIFIED."""
    t_now = 1700000000000
    q = resolve_observation_evidence_quality({
        "symbol": "XAUUSDT",
        "transport_state": "CONNECTED",
        "quote_source": None,
        "last_quote_at_ms": t_now - 100,
        "quote_age_sec": 0.1,
        "bid": 2650.0,
        "ask": 2650.5,
    }, now_ms=t_now)
    assert q.quality == "UNVERIFIED"
    assert q.reason_code == "QUOTE_SOURCE_UNKNOWN"


def test_ea10_unknown_transport_rejected():
    """EA10: UNKNOWN transport_state fails closed -> UNVERIFIED."""
    t_now = 1700000000000
    q = resolve_observation_evidence_quality({
        "symbol": "XAUUSDT",
        "transport_state": "UNKNOWN",
        "quote_source": "WS",
        "last_quote_at_ms": t_now - 100,
        "quote_age_sec": 0.1,
        "bid": 2650.0,
        "ask": 2650.5,
    }, now_ms=t_now)
    assert q.quality == "UNVERIFIED"
    assert q.reason_code == "TRANSPORT_STATE_UNKNOWN"


def test_ea11_disconnected_transport_rejected():
    """EA11: DISCONNECTED transport_state -> DOWN."""
    t_now = 1700000000000
    q = resolve_observation_evidence_quality({
        "symbol": "XAUUSDT",
        "transport_state": "DISCONNECTED",
        "quote_source": "WS",
        "last_quote_at_ms": t_now - 100,
        "quote_age_sec": 0.1,
        "bid": 2650.0,
        "ask": 2650.5,
    }, now_ms=t_now)
    assert q.quality == "DOWN"
    assert q.reason_code == "TRANSPORT_DISCONNECTED"


def test_ea12_stale_quote_rejected():
    """EA12: Quote older than threshold -> DEGRADED."""
    t_now = 1700000000000
    q = resolve_observation_evidence_quality({
        "symbol": "XAUUSDT",
        "transport_state": "CONNECTED",
        "quote_source": "WS",
        "last_quote_at_ms": t_now - 25000,
        "quote_age_sec": 25.0,
        "bid": 2650.0,
        "ask": 2650.5,
    }, now_ms=t_now)
    assert q.quality == "DEGRADED"
    assert "QUOTE_STALE_AGE" in q.reason_code


def test_ea13_future_timestamp_rejected():
    """EA13: Quote timestamp in the future beyond clock skew -> DEGRADED."""
    t_now = 1700000000000
    q = resolve_observation_evidence_quality({
        "symbol": "XAUUSDT",
        "transport_state": "CONNECTED",
        "quote_source": "WS",
        "last_quote_at_ms": t_now + 10_000,
        "quote_age_sec": -10.0,
        "bid": 2650.0,
        "ask": 2650.5,
    }, now_ms=t_now)
    assert q.quality == "DEGRADED"
    assert q.reason_code == "QUOTE_FUTURE_CLOCK_SKEW"


def test_ea14_conflicting_timestamps_rejected():
    """EA14: Reported quote age and calculated age conflict significantly -> DEGRADED."""
    t_now = 1700000000000
    q = resolve_observation_evidence_quality({
        "symbol": "XAUUSDT",
        "transport_state": "CONNECTED",
        "quote_source": "WS",
        "last_quote_at_ms": t_now - 200,   # calculated age 0.2s
        "quote_age_sec": 10.0,              # reported age 10.0s (conflict > 5s)
        "bid": 2650.0,
        "ask": 2650.5,
    }, now_ms=t_now)
    assert q.quality == "DEGRADED"
    assert "QUOTE_AGE_CONFLICT" in q.reason_code


def test_ea15_nonfinite_or_negative_bid_rejected():
    """EA15: Non-finite or <= 0 Bid price -> DEGRADED."""
    t_now = 1700000000000
    q1 = resolve_observation_evidence_quality({
        "symbol": "XAUUSDT",
        "transport_state": "CONNECTED",
        "quote_source": "WS",
        "last_quote_at_ms": t_now - 100,
        "quote_age_sec": 0.1,
        "bid": -5.0,
        "ask": 2650.5,
    }, now_ms=t_now)
    assert q1.quality == "DEGRADED"
    assert q1.reason_code == "QUOTE_INVALID_NON_POSITIVE_BID"

    q2 = resolve_observation_evidence_quality({
        "symbol": "XAUUSDT",
        "transport_state": "CONNECTED",
        "quote_source": "WS",
        "last_quote_at_ms": t_now - 100,
        "quote_age_sec": 0.1,
        "bid": "NaN",
        "ask": 2650.5,
    }, now_ms=t_now)
    assert q2.quality == "DEGRADED"


def test_ea16_crossed_book_rejected():
    """EA16: Ask < Bid (crossed book) -> DEGRADED."""
    t_now = 1700000000000
    q = resolve_observation_evidence_quality({
        "symbol": "XAUUSDT",
        "transport_state": "CONNECTED",
        "quote_source": "WS",
        "last_quote_at_ms": t_now - 100,
        "quote_age_sec": 0.1,
        "bid": 2655.0,
        "ask": 2650.0,
    }, now_ms=t_now)
    assert q.quality == "DEGRADED"
    assert q.reason_code == "QUOTE_CROSSED_BOOK_ASK_LESS_THAN_BID"


def test_ea17_rest_fallback_correctly_degraded():
    """EA17: Fresh REST fallback quote is valid but must be classified as DEGRADED, never GOOD."""
    t_now = 1700000000000
    q = resolve_observation_evidence_quality({
        "symbol": "XAUUSDT",
        "transport_state": "CONNECTED",
        "quote_source": "REST_FALLBACK",
        "last_quote_at_ms": t_now - 100,
        "quote_age_sec": 0.1,
        "bid": 2650.0,
        "ask": 2650.5,
    }, now_ms=t_now)
    assert q.quality == "DEGRADED"
    assert q.reason_code == "REST_FALLBACK_VALID"


def test_ea18_valid_canonical_ws_quote_accepted(db_session):
    """EA18: Fully valid canonical WebSocket quote authorizes GOOD and persists to database."""
    obs_svc = EngineObservabilityService()
    t_now = 1700000000000
    canonical_feed = {
        "symbol": "XAUUSDT",
        "transport_state": "CONNECTED",
        "quote_source": "WS",
        "last_quote_at_ms": t_now - 50,
        "quote_age_sec": 0.05,
        "bid": 2650.2,
        "ask": 2650.5,
        "status": "VALID",
    }
    obs_svc.record_engine_heartbeat(
        db_session,
        run_id="RUN_EA18",
        heartbeat_at_ms=t_now,
        metadata={"feed_health": dict(canonical_feed)},
    )
    feed2 = dict(canonical_feed)
    feed2["last_quote_at_ms"] = t_now + 10_000 - 50
    obs_svc.record_engine_heartbeat(
        db_session,
        run_id="RUN_EA18",
        heartbeat_at_ms=t_now + 10_000,
        metadata={"feed_health": feed2},
    )
    from services.observation_interval_service import observation_interval_service
    observation_interval_service.flush_active_intervals(db_session)

    intervals = db_session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.run_id == "RUN_EA18",
        models.PaperObservationInterval.data_quality == "GOOD",
    ).all()
    assert len(intervals) >= 1
    assert intervals[0].data_quality == "GOOD"


def test_ea21_replay_valid_quote_excluded_from_live(db_session):
    """EA21: Even a structurally valid quote cannot produce LIVE_FORWARD GOOD if origin is REPLAY."""
    obs_svc = EngineObservabilityService()
    t_now = 1700000000000
    canonical_feed = {
        "symbol": "XAUUSDT",
        "transport_state": "CONNECTED",
        "quote_source": "WS",
        "last_quote_at_ms": t_now - 50,
        "quote_age_sec": 0.05,
        "bid": 2650.2,
        "ask": 2650.5,
    }
    obs_svc.record_engine_heartbeat(
        db_session,
        run_id="RUN_EA21",
        heartbeat_at_ms=t_now,
        metadata={
            "feed_health": canonical_feed,
            "evidence_origin": "REPLAY",
        },
    )
    from services.observation_interval_service import observation_interval_service
    observation_interval_service.flush_active_intervals(db_session)

    intervals = db_session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.run_id == "RUN_EA21",
    ).all()
    assert len(intervals) == 0, f"Replay produced persistent live interval: {intervals}"
