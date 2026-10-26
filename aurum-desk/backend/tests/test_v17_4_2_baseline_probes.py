"""
backend/tests/test_v17_4_2_baseline_probes.py
Exact port of the 4 review red probes for Aurum V17.4.2 (Phụ lục E):
- Q: Quote revalidation (validate_ticker with prevalidated ValidatedQuote at T0+60s)
- N: Cached no-op version bypass (Session B holding cached v1 tries no-op on stale v1 after A updated to v2)
- L: Legacy policy route scope bypass (POST /api/v1/policy/trading on LIVE scope)
- C: Legacy scalp route conflicting pair (POST /api/scalp/config with timing new + daily_ny_goal 1)
"""
import os
import tempfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from database import get_db
from main import app
from models import Base, SystemConfig, TradingPolicy
from services.quote_validator import QuoteValidator, ValidatedQuote
from services.entry_session_policy_service import (
    update_entry_session_settings,
    StalePolicyVersionError,
)


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'probe_noop.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with SessionFactory() as db:
        db.add(
            TradingPolicy(
                id="default",
                entry_session_policy="ALL_SESSIONS_WITH_NY_RESERVE",
                ny_min_fills=1,
                reserve_ny_slot=True,
                ny_fallback_enabled=True,
                max_daily_fills=3,
                version=1,
                updated_at=1700000000000,
            )
        )
        db.add(SystemConfig(key="timing_profile", value="SILVER_BULLET_LONDON_NY", updated_at=1700000000000))
        db.add(SystemConfig(key="daily_ny_goal", value="1", updated_at=1700000000000))
        db.add(SystemConfig(key="execution_scope", value="PAPER", updated_at=1700000000000))
        db.commit()
    yield SessionFactory
    engine.dispose()


@pytest.fixture
def client_and_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'probe_api.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with SessionFactory() as db:
        db.add(
            TradingPolicy(
                id="default",
                entry_session_policy="ALL_SESSIONS_WITH_NY_RESERVE",
                ny_min_fills=1,
                reserve_ny_slot=True,
                ny_fallback_enabled=True,
                max_daily_fills=3,
                version=1,
                updated_at=1700000000000,
            )
        )
        db.add(SystemConfig(key="timing_profile", value="SILVER_BULLET_LONDON_NY", updated_at=1700000000000))
        db.add(SystemConfig(key="daily_ny_goal", value="1", updated_at=1700000000000))
        db.add(SystemConfig(key="execution_scope", value="PAPER", updated_at=1700000000000))
        db.commit()

    def override_get_db():
        s = SessionFactory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        yield client, SessionFactory
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def read_paired_state_fresh(session_factory):
    with session_factory() as db:
        pol = db.query(TradingPolicy).filter_by(id="default").first()
        tp_row = db.query(SystemConfig).filter_by(key="timing_profile").first()
        ny_row = db.query(SystemConfig).filter_by(key="daily_ny_goal").first()
        scope_row = db.query(SystemConfig).filter_by(key="execution_scope").first()
        return (
            pol.entry_session_policy if pol else None,
            tp_row.value if tp_row else None,
            pol.ny_min_fills if pol else None,
            ny_row.value if ny_row else None,
            pol.version if pol else None,
            scope_row.value if scope_row else None,
        )


def test_prevalidated_quote_must_be_rechecked_at_execution_time():
    """Probe Q: Re-checking a prevalidated quote with T0+60s must recompute freshness and detect staleness."""
    t0 = 1700000000000
    q = QuoteValidator.validate_ticker({
        "bid": 2623.5, "ask": 2623.75, "last": 2623.6,
        "observed_at": t0, "server_time": t0,
    }, now_ms=t0)
    assert q.is_valid
    later = QuoteValidator.validate_ticker(q, now_ms=t0 + 60000, max_age_sec=15)
    assert not later.is_valid
    assert later.is_stale
    assert later.freshness_sec >= 60
    assert q.freshness_sec == 0  # input preserves original snapshot


def test_cached_noop_must_reject_stale_expected_version(session_factory):
    """Probe N: Cached ORM entity must not allow no-op return when DB version was advanced."""
    a, b = session_factory(), session_factory()
    try:
        held_policy = b.query(TradingPolicy).filter_by(id="default").first()
        held_timing = b.query(SystemConfig).filter_by(key="timing_profile").first()
        assert held_policy.version == 1
        assert held_timing.value == "SILVER_BULLET_LONDON_NY"

        update_entry_session_settings(a, {
            "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
            "timing_profile": "CG01_ALL_SESSIONS_PAPER",
            "expected_version": 1,
        }, now_ms=1700000000100)

        # Session B did not expire its held entities. It must still raise StalePolicyVersionError!
        with pytest.raises(StalePolicyVersionError):
            update_entry_session_settings(b, {
                "entry_session_policy": "ALL_SESSIONS_WITH_NY_RESERVE",
                "timing_profile": "SILVER_BULLET_LONDON_NY",
                "expected_version": 1,
            }, now_ms=1700000000200)
    finally:
        a.close()
        b.close()


def test_legacy_policy_route_cannot_bypass_server_live_scope(client_and_factory):
    """Probe L: POST /api/v1/policy/trading cannot set ALL_SESSIONS_SETUP_FIRST when server scope is LIVE."""
    client, session_factory = client_and_factory
    with session_factory() as db:
        db.query(SystemConfig).filter_by(key="execution_scope").first().value = "LIVE"
        db.commit()
    before = read_paired_state_fresh(session_factory)
    response = client.post("/api/v1/policy/trading", json={
        "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
        "ny_min_fills": 0,
        "reserve_ny_slot": False,
        "ny_fallback_enabled": False,
    })
    assert response.status_code in (400, 409, 422)
    assert read_paired_state_fresh(session_factory) == before


def test_legacy_scalp_route_rejects_conflicting_pair(client_and_factory):
    """Probe C: POST /api/scalp/config must reject timing new + daily_ny_goal 1."""
    client, session_factory = client_and_factory
    before = read_paired_state_fresh(session_factory)
    response = client.post("/api/scalp/config", json={
        "timing_profile": "CG01_ALL_SESSIONS_PAPER",
        "daily_ny_goal": 1,
    })
    assert response.status_code in (400, 409, 422)
    assert read_paired_state_fresh(session_factory) == before
