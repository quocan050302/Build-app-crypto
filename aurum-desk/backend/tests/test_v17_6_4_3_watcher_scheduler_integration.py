"""
backend/tests/test_v17_6_4_3_watcher_scheduler_integration.py
Authoritative Integration Suite for DBEA-11: Real Watcher & Scheduler Telemetry Integration.

Tests per Master Prompt V17.6.4.3 (Parts 151–159):
- WS01: ScalpWatcherOrchestrator.evaluate_cycle() records evaluation cycle on isolated DB
- WS02: Watcher heartbeat with verified feed produces durable GOOD interval
- WS03: Watcher heartbeat with fake GOOD flag and missing quote fails closed
- WS04: Two-database watcher isolation (Database A vs Database B)
- WS05: Zero actual exchange order submissions (LIVE_EXCHANGE_ORDERS_SENT = 0)
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
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator


@pytest.fixture
def isolated_dbs(tmp_path):
    path_a = tmp_path / "watcher_a.sqlite"
    path_b = tmp_path / "watcher_b.sqlite"
    eng_a = create_engine(f"sqlite:///{path_a.as_posix()}", connect_args={"check_same_thread": False})
    eng_b = create_engine(f"sqlite:///{path_b.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng_a)
    Base.metadata.create_all(bind=eng_b)
    run_schema_migrations(eng_a)
    run_schema_migrations(eng_b)
    s_a = sessionmaker(bind=eng_a, autoflush=False)()
    s_b = sessionmaker(bind=eng_b, autoflush=False)()
    try:
        yield {"s_a": s_a, "s_b": s_b, "eng_a": eng_a, "eng_b": eng_b}
    finally:
        s_a.close()
        s_b.close()


def test_ws01_watcher_evaluate_cycle_records_cycle_record(isolated_dbs):
    """WS01: Actual ScalpWatcherOrchestrator.evaluate_cycle records cycle in isolated database."""
    session = isolated_dbs["s_a"]
    orchestrator = ScalpWatcherOrchestrator()
    t_now = 1700000000000

    quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "last": 2650.2,
        "is_valid": True,
        "is_stale": False,
        "exchange_ts_ms": t_now - 50,
        "received_at_ms": t_now - 50,
    }

    class DummyResearchContext:
        def __init__(self, run_id):
            self.run_id = run_id

    res = orchestrator.evaluate_cycle(
        db=session,
        symbol="XAUUSDT",
        candles_1m=[],
        candles_5m=[],
        candles_15m=[],
        quote=quote,
        now_ms=t_now,
        research_context=DummyResearchContext("RUN_WS01"),
    )

    assert res is not None
    assert "cycle_id" in res

    # Verify cycle record was written to Database A
    cycles = session.query(models.EngineEvaluationCycleRecord).filter(
        models.EngineEvaluationCycleRecord.run_id == "RUN_WS01"
    ).all()
    assert len(cycles) >= 1
    assert cycles[0].symbol == "XAUUSDT"


def test_ws02_watcher_heartbeat_with_verified_feed_produces_good_interval(isolated_dbs):
    """WS02: Watcher telemetry with canonical validated quote snapshot persists GOOD interval."""
    session = isolated_dbs["s_a"]
    t0 = 1700000000000
    canonical_quote = {
        "symbol": "XAUUSDT",
        "transport_state": "CONNECTED",
        "quote_source": "WS",
        "last_quote_at_ms": t0 - 50,
        "quote_age_sec": 0.05,
        "bid": 2650.0,
        "ask": 2650.5,
        "status": "VALID",
    }

    engine_observability_service.record_engine_heartbeat(
        session,
        run_id="RUN_WS02",
        symbol="XAUUSDT",
        heartbeat_at_ms=t0,
        metadata={"feed_health": dict(canonical_quote)},
    )
    quote2 = dict(canonical_quote)
    quote2["last_quote_at_ms"] = t0 + 10_000 - 50
    engine_observability_service.record_engine_heartbeat(
        session,
        run_id="RUN_WS02",
        symbol="XAUUSDT",
        heartbeat_at_ms=t0 + 10_000,
        metadata={"feed_health": quote2},
    )

    observation_interval_service.flush_active_intervals(session)

    good_intervals = session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.run_id == "RUN_WS02",
        models.PaperObservationInterval.data_quality == "GOOD",
    ).all()
    assert len(good_intervals) >= 1
    assert good_intervals[0].data_quality == "GOOD"


def test_ws03_fake_good_without_quote_fails_closed(isolated_dbs):
    """WS03: Watcher supplying fake GOOD metadata without valid quote snapshot fails closed."""
    session = isolated_dbs["s_a"]
    t0 = 1700000000000

    # Inject metadata with quality: GOOD but NO underlying quote
    engine_observability_service.record_engine_heartbeat(
        session,
        run_id="RUN_WS03",
        symbol="XAUUSDT",
        heartbeat_at_ms=t0,
        metadata={
            "evidence_quality": {"quality": "GOOD"},
            "is_feed_healthy": True,
            "is_quote_fresh": True,
            "transport_state": "CONNECTED",
        },
    )
    engine_observability_service.record_engine_heartbeat(
        session,
        run_id="RUN_WS03",
        symbol="XAUUSDT",
        heartbeat_at_ms=t0 + 10_000,
        metadata={
            "evidence_quality": {"quality": "GOOD"},
            "is_feed_healthy": True,
            "is_quote_fresh": True,
            "transport_state": "CONNECTED",
        },
    )

    observation_interval_service.flush_active_intervals(session)

    # Database must NOT have any GOOD intervals
    good_intervals = session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.run_id == "RUN_WS03",
        models.PaperObservationInterval.data_quality == "GOOD",
    ).all()
    assert len(good_intervals) == 0, f"Vulnerability: fake GOOD accepted: {good_intervals}"


def test_ws04_two_database_watcher_isolation(isolated_dbs):
    """WS04: Telemetry recorded on DB_A must never appear in DB_B."""
    s_a = isolated_dbs["s_a"]
    s_b = isolated_dbs["s_b"]
    t0 = 1700000000000

    canonical_quote = {
        "symbol": "XAUUSDT",
        "transport_state": "CONNECTED",
        "quote_source": "WS",
        "last_quote_at_ms": t0 - 50,
        "quote_age_sec": 0.05,
        "bid": 2650.0,
        "ask": 2650.5,
        "status": "VALID",
    }
    engine_observability_service.record_engine_heartbeat(
        s_a,
        run_id="RUN_A_WATCHER",
        heartbeat_at_ms=t0,
        metadata={"feed_health": dict(canonical_quote)},
    )
    canonical_quote["last_quote_at_ms"] = t0 + 5000 - 50
    engine_observability_service.record_engine_heartbeat(
        s_a,
        run_id="RUN_A_WATCHER",
        heartbeat_at_ms=t0 + 5000,
        metadata={"feed_health": canonical_quote},
    )

    observation_interval_service.flush_active_intervals(s_a)

    # DB_A has the record
    assert s_a.query(models.PaperObservationInterval).filter(models.PaperObservationInterval.run_id == "RUN_A_WATCHER").count() == 1
    # DB_B must NOT have the record
    assert s_b.query(models.PaperObservationInterval).filter(models.PaperObservationInterval.run_id == "RUN_A_WATCHER").count() == 0


def test_ws05_zero_live_exchange_orders():
    """WS05: Invariant check - zero real live exchange orders sent."""
    from services.execution_coordinator import LIVE_EXCHANGE_ORDERS_SENT
    assert LIVE_EXCHANGE_ORDERS_SENT == 0, f"Invariant violated: LIVE_EXCHANGE_ORDERS_SENT={LIVE_EXCHANGE_ORDERS_SENT}"
    assert hasattr(models, "PaperOrder")
    assert not hasattr(models, "LiveExchangeOrder")
    assert not hasattr(models, "BitgetLiveOrder")
