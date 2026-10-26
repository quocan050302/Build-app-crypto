"""
backend/tests/test_v17_4_2_writer_contracts.py
Comprehensive test suite for session writer scope gates, paired contract enforcement, and legacy compatibility (W01 - W12).
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import get_db
from main import app
from models import Base, SystemConfig, TradingPolicy
from services.trading_policy_service import TradingPolicyService


@pytest.fixture
def client_and_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test_writers.db'}",
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
                min_net_rr=1.5,
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


def test_w01_put_new_scope_live_denied(client_and_factory):
    """W01: PUT /api/v1/policy/entry-session is rejected when server execution_scope is LIVE."""
    client, session_factory = client_and_factory
    with session_factory() as db:
        db.query(SystemConfig).filter_by(key="execution_scope").first().value = "LIVE"
        db.commit()

    res = client.put("/api/v1/policy/entry-session", json={
        "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
        "timing_profile": "CG01_ALL_SESSIONS_PAPER",
        "expected_version": 1,
    })
    assert res.status_code == 400
    assert "PAPER_ONLY_SESSION_PROFILE" in res.json()["detail"]


def test_w02_post_policy_live_denied(client_and_factory):
    """W02: POST /api/v1/policy/trading is rejected when server execution_scope is LIVE."""
    client, session_factory = client_and_factory
    with session_factory() as db:
        db.query(SystemConfig).filter_by(key="execution_scope").first().value = "LIVE"
        db.commit()

    res = client.post("/api/v1/policy/trading", json={
        "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
    })
    assert res.status_code == 400
    assert "PAPER_ONLY_SESSION_PROFILE" in res.json()["detail"]


def test_w03_post_scalp_live_denied(client_and_factory):
    """W03: POST /api/scalp/config is rejected when server execution_scope is LIVE and timing is CG01_ALL_SESSIONS_PAPER."""
    client, session_factory = client_and_factory
    with session_factory() as db:
        db.query(SystemConfig).filter_by(key="execution_scope").first().value = "LIVE"
        db.commit()

    res = client.post("/api/scalp/config", json={
        "timing_profile": "CG01_ALL_SESSIONS_PAPER",
    })
    assert res.status_code == 400
    assert "PAPER_ONLY_PROFILE_VIOLATION" in res.json()["detail"]


def test_w04_new_mode_omitted_goal_valid_zero(client_and_factory):
    """W04: Selecting CG01_ALL_SESSIONS_PAPER with omitted daily_ny_goal normalizes goal to 0."""
    client, session_factory = client_and_factory
    res = client.post("/api/scalp/config", json={
        "timing_profile": "CG01_ALL_SESSIONS_PAPER",
    })
    assert res.status_code == 200

    with session_factory() as db:
        pol = db.query(TradingPolicy).filter_by(id="default").first()
        assert pol.entry_session_policy == "ALL_SESSIONS_SETUP_FIRST"
        assert pol.ny_min_fills == 0
        cfg_goal = db.query(SystemConfig).filter_by(key="daily_ny_goal").first().value
        assert cfg_goal == "0"


def test_w05_explicit_goal_1_conflict(client_and_factory):
    """W05: Explicit daily_ny_goal=1 when setting CG01_ALL_SESSIONS_PAPER raises ENTRY_SESSION_CONFIG_CONFLICT."""
    client, session_factory = client_and_factory
    res = client.post("/api/scalp/config", json={
        "timing_profile": "CG01_ALL_SESSIONS_PAPER",
        "daily_ny_goal": 1,
    })
    assert res.status_code == 400
    assert "ENTRY_SESSION_CONFIG_CONFLICT" in res.json()["detail"]

    # Verify DB state did not change
    with session_factory() as db:
        pol = db.query(TradingPolicy).filter_by(id="default").first()
        assert pol.entry_session_policy == "ALL_SESSIONS_WITH_NY_RESERVE"


def test_w06_current_new_and_daily_min_update_ny_goal_remains_zero(client_and_factory):
    """W06: In ALL_SESSIONS_SETUP_FIRST, updating daily_min_goal preserves daily_ny_goal=0."""
    client, session_factory = client_and_factory
    # First set to new mode
    client.post("/api/scalp/config", json={"timing_profile": "CG01_ALL_SESSIONS_PAPER"})

    # Now update daily_min_goal
    res = client.post("/api/scalp/config", json={
        "daily_min_goal": 2,
    })
    assert res.status_code == 200

    with session_factory() as db:
        pol = db.query(TradingPolicy).filter_by(id="default").first()
        assert pol.daily_min_goal == 2
        assert pol.ny_min_fills == 0
        cfg_goal = db.query(SystemConfig).filter_by(key="daily_ny_goal").first().value
        assert cfg_goal == "0"


def test_w07_legacy_valid_goals_still_work(client_and_factory):
    """W07: In legacy ALL_SESSIONS_WITH_NY_RESERVE mode, updating valid daily_ny_goal continues to work."""
    client, session_factory = client_and_factory
    res = client.post("/api/scalp/config", json={
        "daily_ny_goal": 2,
        "daily_max_cap": 3,
    })
    assert res.status_code == 200

    with session_factory() as db:
        pol = db.query(TradingPolicy).filter_by(id="default").first()
        assert pol.ny_min_fills == 2
        cfg_goal = db.query(SystemConfig).filter_by(key="daily_ny_goal").first().value
        assert cfg_goal == "2"


def test_w08_explicit_reserve_fallback_conflict(client_and_factory):
    """W08: In update_policy, setting ALL_SESSIONS_SETUP_FIRST with reserve_ny_slot=True is rejected."""
    client, session_factory = client_and_factory
    res = client.post("/api/v1/policy/trading", json={
        "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
        "reserve_ny_slot": True,
    })
    assert res.status_code == 400
    assert "ENTRY_SESSION_CONFIG_CONFLICT" in res.json()["detail"]


def test_w09_mixed_valid_rr_and_invalid_session_rollback_all(client_and_factory):
    """W09: If a payload contains valid min_net_rr but conflicting session settings, the entire request is rejected and rolled back."""
    client, session_factory = client_and_factory
    res = client.post("/api/scalp/config", json={
        "min_net_rr": 1.9,
        "timing_profile": "CG01_ALL_SESSIONS_PAPER",
        "daily_ny_goal": 1,
    })
    assert res.status_code == 400

    with session_factory() as db:
        pol = db.query(TradingPolicy).filter_by(id="default").first()
        assert pol.min_net_rr == 1.5  # not updated to 1.9!


def test_w10_two_writers_no_partial_pair(client_and_factory):
    """W10: Sequential writes between legacy scalp config and new entry session endpoint maintain paired consistency."""
    client, session_factory = client_and_factory
    # Write 1: Update scalp mode to SHADOW via /api/scalp/config
    res1 = client.post("/api/scalp/config", json={"scalp_m1_mode": "SHADOW"})
    assert res1.status_code == 200

    # Write 2: Update entry session settings via /api/v1/policy/entry-session
    res2 = client.put("/api/v1/policy/entry-session", json={
        "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
        "timing_profile": "CG01_ALL_SESSIONS_PAPER",
        "expected_version": 1,
    })
    assert res2.status_code == 200

    with session_factory() as db:
        pol = db.query(TradingPolicy).filter_by(id="default").first()
        assert pol.entry_session_policy == "ALL_SESSIONS_SETUP_FIRST"
        assert pol.ny_min_fills == 0
        cfg_tp = db.query(SystemConfig).filter_by(key="timing_profile").first().value
        assert cfg_tp == "CG01_ALL_SESSIONS_PAPER"
        cfg_mode = db.query(SystemConfig).filter_by(key="scalp_m1_mode").first().value
        assert cfg_mode == "SHADOW"


def test_w11_new_endpoint_strict_version(client_and_factory):
    """W11: PUT /api/v1/policy/entry-session rejects missing, boolean, or negative expected_version."""
    client, _ = client_and_factory
    # Missing expected_version
    r1 = client.put("/api/v1/policy/entry-session", json={
        "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
        "timing_profile": "CG01_ALL_SESSIONS_PAPER",
    })
    assert r1.status_code == 422

    # Boolean expected_version
    r2 = client.put("/api/v1/policy/entry-session", json={
        "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
        "timing_profile": "CG01_ALL_SESSIONS_PAPER",
        "expected_version": True,
    })
    assert r2.status_code == 422

    # Zero or negative expected_version
    r3 = client.put("/api/v1/policy/entry-session", json={
        "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
        "timing_profile": "CG01_ALL_SESSIONS_PAPER",
        "expected_version": 0,
    })
    assert r3.status_code == 422


def test_w12_internal_service_calls_equal_permission_as_api(client_and_factory):
    """W12: Calling TradingPolicyService.update_policy directly in Python enforces the exact same scope & conflict rules as HTTP."""
    _, session_factory = client_and_factory
    with session_factory() as db:
        db.query(SystemConfig).filter_by(key="execution_scope").first().value = "LIVE"
        db.commit()

        with pytest.raises(ValueError) as exc:
            TradingPolicyService.update_policy(db, {
                "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
            })
        assert "PAPER_ONLY_SESSION_PROFILE" in str(exc.value)
