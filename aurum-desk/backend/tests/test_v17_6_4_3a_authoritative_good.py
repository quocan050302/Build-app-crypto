"""
backend/tests/test_v17_6_4_3a_authoritative_good.py
Authoritative Verification Suite for AC43A-03 & AC43A-04:
Unforgeable Evidence Authority, Removal of Typed Dataclass Bypass, and Canonical Quote Provenance.
Covers AG01 through AG18 per Master Prompt V17.6.4.3a (Phase 3, Part 060).
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
)
from services.observation_interval_service import observation_interval_service
from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator
from services.bitget_ws_service import bitget_ws_service


@pytest.fixture
def db_session(tmp_path):
    db_file = tmp_path / "authoritative_good.sqlite"
    engine = create_engine(f"sqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def test_ag01_full_property_forged_dataclass_rejected(db_session):
    """AG01: Full-property forged ObservationEvidenceQuality dataclass is strictly rejected without canonical quote."""
    obs_svc = EngineObservabilityService()
    now_ms = 1700000000000

    # Forged dataclass satisfying all internal property checks
    forged = ObservationEvidenceQuality(
        quality="GOOD",
        reason_code="FEED_VERIFIED_FRESH",
        quote_valid=True,
        owner_verified=True,
        transport_state="CONNECTED",
        feed_source="WS",
        quote_age_ms=100,
        evidence_timestamp_ms=now_ms,
    )

    obs_svc.record_engine_heartbeat(
        db_session,
        run_id="RUN_AG01",
        heartbeat_at_ms=now_ms,
        metadata={"evidence_quality": forged},
    )
    observation_interval_service.flush_active_intervals(db_session)

    # Database must contain 0 GOOD intervals
    good_intervals = db_session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.run_id == "RUN_AG01",
        models.PaperObservationInterval.data_quality == "GOOD",
    ).all()
    assert len(good_intervals) == 0, f"Forged dataclass created GOOD interval: {good_intervals}"


def test_ag02_forged_dictionary_rejected(db_session):
    """AG02: Forged dictionary metadata claiming GOOD is rejected."""
    obs_svc = EngineObservabilityService()
    now_ms = 1700000000000

    obs_svc.record_engine_heartbeat(
        db_session,
        run_id="RUN_AG02",
        heartbeat_at_ms=now_ms,
        metadata={
            "evidence_quality": {
                "quality": "GOOD",
                "reason_code": "FEED_VERIFIED_FRESH",
                "quote_valid": True,
                "owner_verified": True,
            }
        },
    )
    observation_interval_service.flush_active_intervals(db_session)

    good_intervals = db_session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.run_id == "RUN_AG02",
        models.PaperObservationInterval.data_quality == "GOOD",
    ).all()
    assert len(good_intervals) == 0


def test_ag03_forged_coverage_quality_flag_rejected(db_session):
    """AG03: Direct coverage_quality='GOOD' flag in metadata without quote is rejected."""
    obs_svc = EngineObservabilityService()
    now_ms = 1700000000000

    obs_svc.record_engine_heartbeat(
        db_session,
        run_id="RUN_AG03",
        heartbeat_at_ms=now_ms,
        metadata={
            "coverage_quality": "GOOD",
            "is_feed_healthy": True,
            "is_quote_fresh": True,
        },
    )
    observation_interval_service.flush_active_intervals(db_session)

    good_intervals = db_session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.run_id == "RUN_AG03",
        models.PaperObservationInterval.data_quality == "GOOD",
    ).all()
    assert len(good_intervals) == 0


def test_ag04_forged_worker_role_source_rejected(db_session):
    """AG04: Claiming worker_role='WATCHER_PROCESS' and transport_state='CONNECTED' does NOT authorize GOOD without Bid/Ask."""
    obs_svc = EngineObservabilityService()
    now_ms = 1700000000000

    obs_svc.record_engine_heartbeat(
        db_session,
        run_id="RUN_AG04",
        heartbeat_at_ms=now_ms,
        metadata={
            "worker_role": "WATCHER_PROCESS",
            "transport_state": "CONNECTED",
            "quote_source": "WS",
            # No bid, ask, or timestamps!
        },
    )
    observation_interval_service.flush_active_intervals(db_session)

    good_intervals = db_session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.run_id == "RUN_AG04",
        models.PaperObservationInterval.data_quality == "GOOD",
    ).all()
    assert len(good_intervals) == 0


def test_ag05_connected_but_missing_quote():
    """AG05: Transport connected but quote missing -> UNVERIFIED."""
    now_ms = 1700000000000
    res = resolve_observation_evidence_quality({
        "symbol": "XAUUSDT",
        "transport_state": "CONNECTED",
        "quote_source": "WS",
    }, now_ms=now_ms)
    assert res.quality in ("UNVERIFIED", "DEGRADED")
    assert res.quality != "GOOD"


def test_ag06_valid_bid_ask_missing_timestamp():
    """AG06: Valid Bid/Ask but missing timestamp -> UNVERIFIED."""
    now_ms = 1700000000000
    res = resolve_observation_evidence_quality({
        "symbol": "XAUUSDT",
        "transport_state": "CONNECTED",
        "quote_source": "WS",
        "bid": 2650.0,
        "ask": 2650.5,
    }, now_ms=now_ms)
    assert res.quality == "UNVERIFIED"
    assert res.reason_code == "QUOTE_TIMESTAMP_AND_AGE_MISSING"


def test_ag07_fresh_timestamp_invalid_bid_ask():
    """AG07: Fresh timestamp but invalid Bid/Ask (non-positive bid or ask < bid) -> DEGRADED."""
    now_ms = 1700000000000
    res = resolve_observation_evidence_quality({
        "symbol": "XAUUSDT",
        "transport_state": "CONNECTED",
        "quote_source": "WS",
        "last_quote_at_ms": now_ms - 100,
        "quote_age_sec": 0.1,
        "bid": -10.0,
        "ask": 2650.0,
    }, now_ms=now_ms)
    assert res.quality == "DEGRADED"

    res_crossed = resolve_observation_evidence_quality({
        "symbol": "XAUUSDT",
        "transport_state": "CONNECTED",
        "quote_source": "WS",
        "last_quote_at_ms": now_ms - 100,
        "quote_age_sec": 0.1,
        "bid": 2660.0,
        "ask": 2650.0,  # Ask < Bid (crossed)
    }, now_ms=now_ms)
    assert res_crossed.quality == "DEGRADED"
    assert "CROSSED_BOOK" in res_crossed.reason_code


def test_ag08_wrong_symbol():
    """AG08: Quote for BTCUSDT cannot authorize XAUUSDT -> UNVERIFIED."""
    now_ms = 1700000000000
    res = resolve_observation_evidence_quality({
        "symbol": "BTCUSDT",
        "transport_state": "CONNECTED",
        "quote_source": "WS",
        "last_quote_at_ms": now_ms - 100,
        "quote_age_sec": 0.1,
        "bid": 90000.0,
        "ask": 90001.0,
    }, symbol="XAUUSDT", now_ms=now_ms)
    assert res.quality == "UNVERIFIED"
    assert "SYMBOL_MISMATCH" in res.reason_code


def test_ag09_missing_trusted_source():
    """AG09: Missing quote source or UNKNOWN source fails closed -> UNVERIFIED."""
    now_ms = 1700000000000
    res = resolve_observation_evidence_quality({
        "symbol": "XAUUSDT",
        "transport_state": "CONNECTED",
        "quote_source": "UNKNOWN",
        "last_quote_at_ms": now_ms - 100,
        "quote_age_sec": 0.1,
        "bid": 2650.0,
        "ask": 2650.5,
    }, now_ms=now_ms)
    assert res.quality == "UNVERIFIED"
    assert res.reason_code == "QUOTE_SOURCE_UNKNOWN"


def test_ag10_conflicting_quote_age():
    """AG10: Conflict between last_quote_at_ms and quote_age_sec > 5000ms -> DEGRADED."""
    now_ms = 1700000000000
    res = resolve_observation_evidence_quality({
        "symbol": "XAUUSDT",
        "transport_state": "CONNECTED",
        "quote_source": "WS",
        "last_quote_at_ms": now_ms - 100,  # 100ms ago
        "quote_age_sec": 10.0,  # claims 10s ago -> 9900ms conflict
        "bid": 2650.0,
        "ask": 2650.5,
    }, now_ms=now_ms)
    assert res.quality == "DEGRADED"
    assert "QUOTE_AGE_CONFLICT" in res.reason_code


def test_ag11_disconnected_transport():
    """AG11: Transport disconnected -> DOWN."""
    now_ms = 1700000000000
    res = resolve_observation_evidence_quality({
        "symbol": "XAUUSDT",
        "transport_state": "DISCONNECTED",
        "quote_source": "WS",
        "last_quote_at_ms": now_ms - 100,
        "quote_age_sec": 0.1,
        "bid": 2650.0,
        "ask": 2650.5,
    }, now_ms=now_ms)
    assert res.quality == "DOWN"


def test_ag12_future_timestamp():
    """AG12: Future timestamp (> 5000ms clock skew) -> DEGRADED."""
    now_ms = 1700000000000
    res = resolve_observation_evidence_quality({
        "symbol": "XAUUSDT",
        "transport_state": "CONNECTED",
        "quote_source": "WS",
        "last_quote_at_ms": now_ms + 10_000,  # 10s into the future
        "bid": 2650.0,
        "ask": 2650.5,
    }, now_ms=now_ms)
    assert res.quality == "DEGRADED"
    assert res.reason_code == "QUOTE_FUTURE_CLOCK_SKEW"


def test_ag13_valid_canonical_ws_quote_accepted(db_session):
    """AG13: Genuine canonical WebSocket quote produces verified GOOD coverage and persists to DB."""
    obs_svc = EngineObservabilityService()
    now_ms = 1700000000000

    canonical_quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "quote_source": "WS",
        "transport_state": "CONNECTED",
        "last_quote_at_ms": now_ms - 100,
        "quote_age_sec": 0.1,
        "status": "VALID",
    }

    obs_svc.record_engine_heartbeat(
        db_session,
        run_id="RUN_AG13",
        heartbeat_at_ms=now_ms,
        metadata={"canonical_quote": canonical_quote},
    )
    obs_svc.record_engine_heartbeat(
        db_session,
        run_id="RUN_AG13",
        heartbeat_at_ms=now_ms + 5000,
        metadata={"canonical_quote": canonical_quote},
    )
    observation_interval_service.flush_active_intervals(db_session)

    good_intervals = db_session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.run_id == "RUN_AG13",
        models.PaperObservationInterval.data_quality == "GOOD",
    ).all()
    assert len(good_intervals) >= 1
    assert good_intervals[0].symbol == "XAUUSDT"


def test_ag14_valid_real_watcher_integration(db_session):
    """AG14: ScalpWatcherOrchestrator.evaluate_cycle with valid canonical quote persists GOOD telemetry."""
    now_ms = 1700000000000
    from services.bitget_ws_service import bitget_ws_service
    bitget_ws_service.transport_state = "CONNECTED"
    bitget_ws_service.feed_source = "WS"
    bitget_ws_service.connection_epoch = 1

    quote1 = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "last": 2650.25,
        "exchange_ts_ms": now_ms - 50,
        "received_at_ms": now_ms - 40,
        "timestamp": now_ms,
        "observed_at": now_ms,
        "is_valid": True,
        "is_stale": False,
        "quote_source": "WS",
        "transport_state": "CONNECTED",
        "connection_epoch": 1,
    }

    # Execute real watcher cycle twice to cover interval span
    scalp_watcher_orchestrator.evaluate_cycle(
        db=db_session,
        symbol="XAUUSDT",
        now_ms=now_ms,
        quote=quote1,
        execution_scope="CG01",
    )

    quote2 = dict(quote1)
    quote2["exchange_ts_ms"] = now_ms + 4950
    quote2["received_at_ms"] = now_ms + 4960
    quote2["timestamp"] = now_ms + 5000
    quote2["observed_at"] = now_ms + 5000
    scalp_watcher_orchestrator.evaluate_cycle(
        db=db_session,
        symbol="XAUUSDT",
        now_ms=now_ms + 5000,
        quote=quote2,
        execution_scope="CG01",
    )

    observation_interval_service.flush_active_intervals(db_session)

    # Confirm heartbeats and observation intervals were recorded
    hb = db_session.query(models.EngineHeartbeatRecord).filter(
        models.EngineHeartbeatRecord.symbol == "XAUUSDT"
    ).first()
    assert hb is not None

    good_intervals = db_session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.symbol == "XAUUSDT",
        models.PaperObservationInterval.data_quality == "GOOD",
    ).all()
    assert len(good_intervals) >= 1


def test_ag15_valid_real_scheduler_integration(db_session):
    """AG15: Real scheduler execution cadence records verified telemetry."""
    now_ms = 1700000000000
    from services.cg01_scheduler_service import ensure_observation_run_started
    from services.bitget_ws_service import bitget_ws_service
    bitget_ws_service.transport_state = "CONNECTED"
    bitget_ws_service.feed_source = "WS"
    bitget_ws_service.connection_epoch = 1

    run = ensure_observation_run_started(
        symbol="XAUUSDT",
        scope="CG01",
        mode="PAPER",
        run_id="RUN_AG15",
    )
    assert run is not None

    # Call watcher evaluation via scheduler context twice
    quote1 = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "last": 2650.25,
        "exchange_ts_ms": now_ms - 50,
        "received_at_ms": now_ms - 40,
        "timestamp": now_ms,
        "observed_at": now_ms,
        "is_valid": True,
        "is_stale": False,
        "quote_source": "WS",
        "transport_state": "CONNECTED",
        "connection_epoch": 1,
    }
    scalp_watcher_orchestrator.evaluate_cycle(
        db=db_session,
        symbol="XAUUSDT",
        now_ms=now_ms,
        quote=quote1,
        execution_scope="CG01",
    )
    quote2 = dict(quote1)
    quote2["exchange_ts_ms"] = now_ms + 4950
    quote2["received_at_ms"] = now_ms + 4960
    quote2["timestamp"] = now_ms + 5000
    quote2["observed_at"] = now_ms + 5000
    scalp_watcher_orchestrator.evaluate_cycle(
        db=db_session,
        symbol="XAUUSDT",
        now_ms=now_ms + 5000,
        quote=quote2,
        execution_scope="CG01",
    )
    observation_interval_service.flush_active_intervals(db_session)

    good_intervals = db_session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.symbol == "XAUUSDT",
        models.PaperObservationInterval.data_quality == "GOOD",
    ).all()
    assert len(good_intervals) >= 1


def test_ag16_replay_with_valid_quote_excluded_from_live(db_session):
    """AG16: Valid quote under REPLAY evidence origin can NEVER produce LIVE GOOD intervals."""
    obs_svc = EngineObservabilityService()
    now_ms = 1700000000000

    canonical_quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "quote_source": "WS",
        "transport_state": "CONNECTED",
        "last_quote_at_ms": now_ms - 100,
        "quote_age_sec": 0.1,
        "status": "VALID",
    }

    obs_svc.record_engine_heartbeat(
        db_session,
        run_id="RUN_AG16",
        heartbeat_at_ms=now_ms,
        metadata={
            "evidence_origin": "REPLAY",
            "evaluation_origin": "REPLAY",
            "canonical_quote": canonical_quote,
        },
    )
    observation_interval_service.flush_active_intervals(db_session)

    good_intervals = db_session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.run_id == "RUN_AG16",
        models.PaperObservationInterval.data_quality == "GOOD",
    ).all()
    assert len(good_intervals) == 0, "REPLAY produced GOOD observation interval!"


def test_ag17_test_origin_excluded_from_live(db_session):
    """AG17: Valid quote under TEST evidence origin can NEVER produce LIVE GOOD intervals."""
    obs_svc = EngineObservabilityService()
    now_ms = 1700000000000

    canonical_quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "quote_source": "WS",
        "transport_state": "CONNECTED",
        "last_quote_at_ms": now_ms - 100,
        "quote_age_sec": 0.1,
        "status": "VALID",
    }

    obs_svc.record_engine_heartbeat(
        db_session,
        run_id="RUN_AG17",
        heartbeat_at_ms=now_ms,
        metadata={
            "evidence_origin": "TEST",
            "evaluation_origin": "TEST",
            "canonical_quote": canonical_quote,
        },
    )
    observation_interval_service.flush_active_intervals(db_session)

    good_intervals = db_session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.run_id == "RUN_AG17",
        models.PaperObservationInterval.data_quality == "GOOD",
    ).all()
    assert len(good_intervals) == 0, "TEST origin produced GOOD observation interval!"


def test_ag18_no_unauthorized_good_db_rows(db_session):
    """AG18: Multi-attempt forgery simulation results in 0 unauthorized GOOD rows in database."""
    obs_svc = EngineObservabilityService()
    now_ms = 1700000000000

    attack_payloads = [
        {"coverage_quality": "GOOD"},
        {"is_feed_healthy": True, "is_quote_fresh": True, "quality": "GOOD"},
        {"evidence_quality": ObservationEvidenceQuality(quality="GOOD", reason_code="FEED_VERIFIED_FRESH", feed_source="WS")},
        {"worker_role": "WATCHER_PROCESS", "transport_state": "CONNECTED"},
        {"bid": 2650.0, "ask": 2649.0},  # crossed
        {"quote_source": "REST_FALLBACK", "bid": 2650.0, "ask": 2650.5, "transport_state": "CONNECTED"},
    ]

    for idx, payload in enumerate(attack_payloads):
        obs_svc.record_engine_heartbeat(
            db_session,
            run_id=f"RUN_AG18_{idx}",
            heartbeat_at_ms=now_ms + idx * 1000,
            metadata=payload,
        )

    observation_interval_service.flush_active_intervals(db_session)

    unauthorized_rows = db_session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.run_id.like("RUN_AG18_%"),
        models.PaperObservationInterval.data_quality == "GOOD",
    ).all()
    assert len(unauthorized_rows) == 0, f"Unauthorized GOOD intervals in DB: {unauthorized_rows}"
