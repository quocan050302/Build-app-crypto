"""
backend/tests/test_v17_4_api.py
V17.4 Parts 146 & 147:
- GET /api/v1/policy/entry-session is strictly read-only and idempotent.
- PUT /api/v1/policy/entry-session validates input, rejects conflicts, handles 409 stale versions, and persists atomically.
- Legacy routes (PUT/POST /api/trading-policy, POST /api/scalp/config) synchronize paired fields and do not create split-brain states.
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
from models import Base, DomainEvent, SystemConfig, TradingPolicy
from policy_constants import ALL_SESSIONS_SETUP_FIRST, ALL_SESSIONS_WITH_NY_RESERVE


@pytest.fixture
def api_client_and_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_path = Path(path)

    engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionFactory()

    policy = TradingPolicy(
        entry_session_policy=ALL_SESSIONS_WITH_NY_RESERVE,
        ny_min_fills=1,
        reserve_ny_slot=True,
        ny_fallback_enabled=True,
        max_daily_fills=3,
        version=1,
        updated_at=1700000000000,
    )
    session.add(policy)
    session.add(SystemConfig(key="timing_profile", value="SILVER_BULLET_LONDON_NY", updated_at=1700000000000))
    session.add(SystemConfig(key="daily_ny_goal", value="1", updated_at=1700000000000))
    session.add(SystemConfig(key="execution_scope", value="PAPER", updated_at=1700000000000))
    session.commit()

    def override_get_db():
        db = SessionFactory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    try:
        yield client, SessionFactory
    finally:
        app.dependency_overrides.pop(get_db, None)
        session.close()
        try:
            os.remove(db_path)
        except Exception:
            pass


def test_part_147_get_entry_session_read_only(api_client_and_db):
    client, SessionFactory = api_client_and_db

    # Baseline DB state
    s1 = SessionFactory()
    pol_ver_before = s1.query(TradingPolicy).first().version
    events_count_before = s1.query(DomainEvent).count()
    configs_count_before = s1.query(SystemConfig).count()
    s1.close()

    # Call GET twice
    r1 = client.get("/api/v1/policy/entry-session")
    assert r1.status_code == 200
    data1 = r1.json()
    assert data1["status"] == "CONFIRMED"
    assert data1["entry_session_policy"] == ALL_SESSIONS_WITH_NY_RESERVE
    assert data1["timing_profile"] == "SILVER_BULLET_LONDON_NY"
    assert data1["policy_version"] == pol_ver_before

    r2 = client.get("/api/v1/policy/entry-session")
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2 == data1

    # Verify zero mutations in DB
    s2 = SessionFactory()
    assert s2.query(TradingPolicy).first().version == pol_ver_before
    assert s2.query(DomainEvent).count() == events_count_before
    assert s2.query(SystemConfig).count() == configs_count_before
    s2.close()


def test_part_146_put_entry_session_coordinated_success(api_client_and_db):
    client, SessionFactory = api_client_and_db

    payload = {
        "entry_session_policy": ALL_SESSIONS_SETUP_FIRST,
        "timing_profile": "CG01_ALL_SESSIONS_PAPER",
        "expected_version": 1,
    }
    res = client.put("/api/v1/policy/entry-session", json=payload)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "CONFIRMED"
    assert body["entry_session_policy"] == ALL_SESSIONS_SETUP_FIRST
    assert body["timing_profile"] == "CG01_ALL_SESSIONS_PAPER"
    assert body["effective_ny_goal"] == 0
    assert body["reserve_ny_slot"] is False
    assert body["ny_fallback_enabled"] is False
    assert body["policy_version"] == 2

    # Verify database persistence
    s = SessionFactory()
    pol = s.query(TradingPolicy).first()
    assert pol.entry_session_policy == ALL_SESSIONS_SETUP_FIRST
    assert pol.ny_min_fills == 0
    assert pol.version == 2
    timing_cfg = s.query(SystemConfig).filter(SystemConfig.key == "timing_profile").first()
    assert timing_cfg.value == "CG01_ALL_SESSIONS_PAPER"
    goal_cfg = s.query(SystemConfig).filter(SystemConfig.key == "daily_ny_goal").first()
    assert goal_cfg.value == "0"
    s.close()


def test_part_146_put_entry_session_conflict_and_stale_errors(api_client_and_db):
    client, SessionFactory = api_client_and_db

    # 1. Config conflict: ALL_SESSIONS_SETUP_FIRST with wrong timing
    bad_timing = {
        "entry_session_policy": ALL_SESSIONS_SETUP_FIRST,
        "timing_profile": "SILVER_BULLET_LONDON_NY",
        "expected_version": 1,
    }
    r_bad = client.put("/api/v1/policy/entry-session", json=bad_timing)
    assert r_bad.status_code == 400
    assert "ENTRY_SESSION_CONFIG_CONFLICT" in r_bad.json()["detail"]

    # 2. Stale expected version: version 99 != current 1
    stale_payload = {
        "entry_session_policy": ALL_SESSIONS_SETUP_FIRST,
        "timing_profile": "CG01_ALL_SESSIONS_PAPER",
        "expected_version": 99,
    }
    r_stale = client.put("/api/v1/policy/entry-session", json=stale_payload)
    assert r_stale.status_code == 409
    assert "STALE_POLICY_VERSION" in r_stale.json()["detail"]


def test_part_146_legacy_scalp_config_synchronization(api_client_and_db):
    client, SessionFactory = api_client_and_db

    # Updating timing_profile to CG01_ALL_SESSIONS_PAPER via legacy /api/scalp/config
    # must synchronize TradingPolicy atomically
    res = client.post("/api/scalp/config", json={"timing_profile": "CG01_ALL_SESSIONS_PAPER"})
    assert res.status_code == 200

    s = SessionFactory()
    pol = s.query(TradingPolicy).first()
    assert pol.entry_session_policy == ALL_SESSIONS_SETUP_FIRST
    assert pol.ny_min_fills == 0
    assert pol.reserve_ny_slot is False

    timing_cfg = s.query(SystemConfig).filter(SystemConfig.key == "timing_profile").first()
    assert timing_cfg.value == "CG01_ALL_SESSIONS_PAPER"
    s.close()
