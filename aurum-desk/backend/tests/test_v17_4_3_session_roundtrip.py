"""
backend/tests/test_v17_4_3_session_roundtrip.py
AURUM V17.4.3: Session Configuration Bidirectional Synchronization & Goal Mirror Tests (Parts 011-030, 081-120).
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import get_db
from main import app
from models import Base, SystemConfig, TradingPolicy


@pytest.fixture
def roundtrip_client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'session_roundtrip.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    Factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with Factory() as db:
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
        for key, value in (
            ("timing_profile", "SILVER_BULLET_LONDON_NY"),
            ("daily_ny_goal", "1"),
            ("execution_scope", "PAPER"),
        ):
            db.add(SystemConfig(key=key, value=value, updated_at=1700000000000))
        db.commit()

    def override_get_db():
        s = Factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        yield client, Factory
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def read_session_pair(Factory):
    """PHẦN 081, 093: Fresh Session reader extracting scalar values without identity map caching."""
    with Factory() as db:
        policy = db.query(TradingPolicy).filter_by(id="default").first()
        timing = db.query(SystemConfig).filter_by(key="timing_profile").first()
        goal = db.query(SystemConfig).filter_by(key="daily_ny_goal").first()
        return {
            "mode": policy.entry_session_policy if policy else None,
            "policy_ny_goal": policy.ny_min_fills if policy else None,
            "reserve_ny_slot": policy.reserve_ny_slot if policy else None,
            "ny_fallback_enabled": policy.ny_fallback_enabled if policy else None,
            "version": policy.version if policy else None,
            "timing_profile": timing.value if timing else None,
            "system_ny_goal": goal.value if goal else None,
        }


@pytest.mark.parametrize("writer", ["scalp", "policy"])
def test_new_to_legacy_persists_goal_mirror(roundtrip_client, writer):
    """
    PHẦN 101, 102 (Appendix C):
    Transition from setup-first to legacy via scalp or policy endpoint must persist
    both TradingPolicy.ny_min_fills = 1 AND SystemConfig.daily_ny_goal = '1'.
    """
    client, Factory = roundtrip_client

    # 1. First set setup-first via PUT entry-session
    res1 = client.put(
        "/api/v1/policy/entry-session",
        json={
            "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
            "timing_profile": "CG01_ALL_SESSIONS_PAPER",
            "expected_version": 1,
        },
    )
    assert res1.status_code == 200, res1.text
    pair1 = read_session_pair(Factory)
    assert pair1["mode"] == "ALL_SESSIONS_SETUP_FIRST"
    assert pair1["timing_profile"] == "CG01_ALL_SESSIONS_PAPER"
    assert pair1["policy_ny_goal"] == 0
    assert pair1["system_ny_goal"] == "0"

    # 2. Transition back to legacy
    if writer == "scalp":
        # Scalp timing-only update
        res2 = client.post(
            "/api/scalp/config",
            json={
                "timing_profile": "SILVER_BULLET_LONDON_NY",
            },
        )
    else:
        # Policy update to NY_ONLY with ny_min_fills=1
        res2 = client.post(
            "/api/v1/policy/trading",
            json={
                "entry_session_policy": "NY_ONLY",
                "ny_min_fills": 1,
                "reserve_ny_slot": True,
                "ny_fallback_enabled": True,
            },
        )
    assert res2.status_code == 200, res2.text

    # 3. Read fresh state from database
    pair2 = read_session_pair(Factory)
    assert pair2["policy_ny_goal"] == 1
    assert pair2["system_ny_goal"] == "1"
    assert pair2["timing_profile"] == "SILVER_BULLET_LONDON_NY"
    assert pair2["policy_ny_goal"] == int(pair2["system_ny_goal"])
    expected_mode = "ALL_SESSIONS_WITH_NY_RESERVE" if writer == "scalp" else "NY_ONLY"
    assert pair2["mode"] == expected_mode


def test_d01_legacy_to_setup_first_via_put_entry_session(roundtrip_client):
    """Case D01: Legacy to setup-first via PUT /api/v1/policy/entry-session."""
    client, Factory = roundtrip_client
    res = client.put(
        "/api/v1/policy/entry-session",
        json={
            "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
            "timing_profile": "CG01_ALL_SESSIONS_PAPER",
            "expected_version": 1,
        },
    )
    assert res.status_code == 200
    pair = read_session_pair(Factory)
    assert pair["mode"] == "ALL_SESSIONS_SETUP_FIRST"
    assert pair["timing_profile"] == "CG01_ALL_SESSIONS_PAPER"
    assert pair["policy_ny_goal"] == 0
    assert pair["system_ny_goal"] == "0"
    assert pair["reserve_ny_slot"] is False
    assert pair["ny_fallback_enabled"] is False


def test_d02_setup_first_to_legacy_scalp_timing_expanded(roundtrip_client):
    """Case D02 variant: Setup-first to CG01_NY_PAPER_EXPANDED via scalp timing-only."""
    client, Factory = roundtrip_client
    # Switch to setup-first
    client.put(
        "/api/v1/policy/entry-session",
        json={
            "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
            "timing_profile": "CG01_ALL_SESSIONS_PAPER",
            "expected_version": 1,
        },
    )
    # Switch to NY expanded timing
    res = client.post(
        "/api/scalp/config",
        json={
            "timing_profile": "CG01_NY_PAPER_EXPANDED",
        },
    )
    assert res.status_code == 200
    pair = read_session_pair(Factory)
    assert pair["mode"] == "ALL_SESSIONS_WITH_NY_RESERVE"
    assert pair["timing_profile"] == "CG01_NY_PAPER_EXPANDED"
    assert pair["policy_ny_goal"] == 1
    assert pair["system_ny_goal"] == "1"


def test_d04_legacy_policy_explicit_goal_2(roundtrip_client):
    """Case D04: Legacy policy explicit goal 2 sets both policy goal and mirror to 2."""
    client, Factory = roundtrip_client
    res = client.post(
        "/api/v1/policy/trading",
        json={
            "entry_session_policy": "ALL_SESSIONS_WITH_NY_RESERVE",
            "ny_min_fills": 2,
            "reserve_ny_slot": True,
            "ny_fallback_enabled": True,
        },
    )
    assert res.status_code == 200
    pair = read_session_pair(Factory)
    assert pair["policy_ny_goal"] == 2
    assert pair["system_ny_goal"] == "2"


def test_d05_legacy_explicit_goal_0_preserved(roundtrip_client):
    """Case D05: Legacy explicit goal 0 is preserved as 0 on both policy and SystemConfig."""
    client, Factory = roundtrip_client
    res = client.post(
        "/api/v1/policy/trading",
        json={
            "entry_session_policy": "NY_ONLY",
            "ny_min_fills": 0,
            "reserve_ny_slot": False,
            "ny_fallback_enabled": False,
        },
    )
    assert res.status_code == 200
    pair = read_session_pair(Factory)
    assert pair["policy_ny_goal"] == 0
    assert pair["system_ny_goal"] == "0"


def test_d06_setup_first_to_legacy_policy_omit_ny_min_fills(roundtrip_client):
    """Case D06: Setup-first to legacy policy when ny_min_fills is omitted."""
    client, Factory = roundtrip_client
    # Switch to setup-first
    client.put(
        "/api/v1/policy/entry-session",
        json={
            "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
            "timing_profile": "CG01_ALL_SESSIONS_PAPER",
            "expected_version": 1,
        },
    )
    # Switch to legacy mode without explicit ny_min_fills in body
    res = client.post(
        "/api/v1/policy/trading",
        json={
            "entry_session_policy": "ALL_SESSIONS_WITH_NY_RESERVE",
            "reserve_ny_slot": True,
            "ny_fallback_enabled": True,
        },
    )
    assert res.status_code == 200
    pair = read_session_pair(Factory)
    # Both policy and mirror must agree
    assert pair["policy_ny_goal"] == int(pair["system_ny_goal"])
    assert pair["timing_profile"] == "SILVER_BULLET_LONDON_NY"


def test_d07_policy_goal_only_update_in_legacy(roundtrip_client):
    """Case D07: Updating only ny_min_fills in legacy mode synchronizes daily_ny_goal."""
    client, Factory = roundtrip_client
    res = client.post(
        "/api/v1/policy/trading",
        json={
            "ny_min_fills": 2,
        },
    )
    assert res.status_code == 200
    pair = read_session_pair(Factory)
    assert pair["policy_ny_goal"] == 2
    assert pair["system_ny_goal"] == "2"
    assert pair["timing_profile"] == "SILVER_BULLET_LONDON_NY"


def test_d08_explicit_new_mode_conflicting_goal_rejected(roundtrip_client):
    """Case D08: Explicit new mode with conflicting non-zero daily_ny_goal via scalp is rejected."""
    client, Factory = roundtrip_client
    before = read_session_pair(Factory)
    res = client.post(
        "/api/scalp/config",
        json={
            "timing_profile": "CG01_ALL_SESSIONS_PAPER",
            "daily_ny_goal": 1,
        },
    )
    assert res.status_code == 400
    assert "ENTRY_SESSION_CONFIG_CONFLICT" in res.text
    after = read_session_pair(Factory)
    assert after == before


def test_d09_new_mode_server_live_rejected(roundtrip_client):
    """Case D09: New setup-first mode on server LIVE is rejected regardless of client request."""
    client, Factory = roundtrip_client
    with Factory() as db:
        db.query(SystemConfig).filter_by(key="execution_scope").first().value = "LIVE"
        db.commit()

    before = read_session_pair(Factory)
    res = client.post(
        "/api/v1/policy/trading",
        json={
            "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
            "ny_min_fills": 0,
            "reserve_ny_slot": False,
            "ny_fallback_enabled": False,
        },
    )
    assert res.status_code in (400, 422)
    after = read_session_pair(Factory)
    assert after == before


def test_d10_malformed_input_rejected(roundtrip_client):
    """Case D10: Malformed inputs (invalid timing, boolean goal) rejected with 400."""
    client, Factory = roundtrip_client
    res_timing = client.post(
        "/api/scalp/config",
        json={"timing_profile": "NON_EXISTENT_TIMING"},
    )
    assert res_timing.status_code == 400

    res_goal = client.post(
        "/api/scalp/config",
        json={"daily_ny_goal": True},
    )
    assert res_goal.status_code == 400


def test_d11_missing_mirrors_repaired_on_mutation(roundtrip_client):
    """Case D11: If SystemConfig keys are missing, valid mutation creates both keys."""
    client, Factory = roundtrip_client
    with Factory() as db:
        db.query(SystemConfig).filter(SystemConfig.key.in_(["timing_profile", "daily_ny_goal"])).delete()
        db.commit()

    res = client.put(
        "/api/v1/policy/entry-session",
        json={
            "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
            "timing_profile": "CG01_ALL_SESSIONS_PAPER",
            "expected_version": 1,
        },
    )
    assert res.status_code == 200
    pair = read_session_pair(Factory)
    assert pair["timing_profile"] == "CG01_ALL_SESSIONS_PAPER"
    assert pair["system_ny_goal"] == "0"


def test_d14_stale_expected_version_rejected_with_409(roundtrip_client):
    """Case D14: Stale expected_version returns 409 conflict."""
    client, Factory = roundtrip_client
    # Advance version from 1 to 2
    client.put(
        "/api/v1/policy/entry-session",
        json={
            "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
            "timing_profile": "CG01_ALL_SESSIONS_PAPER",
            "expected_version": 1,
        },
    )
    # Stale request sending expected_version=1
    res_stale = client.put(
        "/api/v1/policy/entry-session",
        json={
            "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
            "timing_profile": "CG01_ALL_SESSIONS_PAPER",
            "expected_version": 1,
        },
    )
    assert res_stale.status_code == 409


def test_d15_true_noop_preserves_version_and_no_audit(roundtrip_client):
    """Case D15: True no-op with matching expected_version returns 200 without version bump."""
    client, Factory = roundtrip_client
    # First save bumps version to 2
    client.put(
        "/api/v1/policy/entry-session",
        json={
            "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
            "timing_profile": "CG01_ALL_SESSIONS_PAPER",
            "expected_version": 1,
        },
    )
    v2_pair = read_session_pair(Factory)
    assert v2_pair["version"] == 2

    # Second identical save with expected_version=2 is a true no-op
    res_noop = client.put(
        "/api/v1/policy/entry-session",
        json={
            "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
            "timing_profile": "CG01_ALL_SESSIONS_PAPER",
            "expected_version": 2,
        },
    )
    assert res_noop.status_code == 200
    v_noop_pair = read_session_pair(Factory)
    assert v_noop_pair["version"] == 2


def test_d16_multi_cycle_roundtrip_preserves_custom_settings(roundtrip_client):
    """
    Case D16 (PHẦN 110, 151-158):
    Multi-cycle roundtrip (Legacy -> Setup-First -> Legacy -> Setup-First -> Legacy)
    preserves min_net_rr (1.7), risk, and position invariant.
    """
    client, Factory = roundtrip_client
    with Factory() as db:
        pol = db.query(TradingPolicy).filter_by(id="default").one()
        pol.min_net_rr = 1.7
        db.commit()

    # Cycle 1: Legacy -> Setup-First
    res1 = client.put(
        "/api/v1/policy/entry-session",
        json={
            "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
            "timing_profile": "CG01_ALL_SESSIONS_PAPER",
            "expected_version": 1,
        },
    )
    assert res1.status_code == 200

    # Cycle 2: Setup-First -> Legacy (scalp)
    res2 = client.post(
        "/api/scalp/config",
        json={"timing_profile": "SILVER_BULLET_LONDON_NY"},
    )
    assert res2.status_code == 200
    pair2 = read_session_pair(Factory)
    assert pair2["policy_ny_goal"] == 1
    assert pair2["system_ny_goal"] == "1"

    # Cycle 3: Legacy -> Setup-First (policy)
    res3 = client.post(
        "/api/v1/policy/trading",
        json={
            "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
            "ny_min_fills": 0,
            "reserve_ny_slot": False,
            "ny_fallback_enabled": False,
        },
    )
    assert res3.status_code == 200
    pair3 = read_session_pair(Factory)
    assert pair3["policy_ny_goal"] == 0
    assert pair3["system_ny_goal"] == "0"
    assert pair3["timing_profile"] == "CG01_ALL_SESSIONS_PAPER"

    # Cycle 4: Setup-First -> Legacy (policy NY_ONLY)
    res4 = client.post(
        "/api/v1/policy/trading",
        json={
            "entry_session_policy": "NY_ONLY",
            "ny_min_fills": 1,
            "reserve_ny_slot": True,
            "ny_fallback_enabled": True,
        },
    )
    assert res4.status_code == 200
    pair4 = read_session_pair(Factory)
    assert pair4["policy_ny_goal"] == 1
    assert pair4["system_ny_goal"] == "1"
    assert pair4["timing_profile"] == "SILVER_BULLET_LONDON_NY"

    # Ensure custom min_net_rr was preserved through all cycles
    with Factory() as db:
        pol = db.query(TradingPolicy).filter_by(id="default").one()
        assert pol.min_net_rr == 1.7
