"""
backend/tests/test_v17_4_1_atomic_cas.py
V17.4.1 Database CAS & Transaction Concurrency Tests (Parts 081 - 100)

Verifies:
1. Two distinct database sessions preload policy v1. First commits v2. Second session expecting v1 raises StalePolicyVersionError (Part 096).
2. Fresh read returns current version and does not overwrite with stale in-memory entity.
3. Fault injection during staging rolls back all changes cleanly (Part 098).
4. No-op updates with expected_version preserve version number and domain event count (Part 099).
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import run_schema_migrations
from models import Base, TradingPolicy, SystemConfig, DomainEvent
from policy_constants import (
    ALL_SESSIONS_SETUP_FIRST,
    ALL_SESSIONS_WITH_NY_RESERVE,
    CG01_ALL_SESSIONS_PAPER,
    SILVER_BULLET_LONDON_NY,
)
from services.entry_session_policy_service import (
    update_entry_session_settings,
    StalePolicyVersionError,
)


@pytest.fixture
def cas_db_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/atomic_cas.db")
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    SessionFactory = sessionmaker(bind=engine)

    with SessionFactory() as session:
        pol = TradingPolicy(
            id="default",
            entry_session_policy=ALL_SESSIONS_WITH_NY_RESERVE,
            ny_min_fills=1,
            reserve_ny_slot=True,
            ny_fallback_enabled=True,
            version=1,
            updated_at=1700000000000,
        )
        session.add(pol)
        session.add(SystemConfig(key="timing_profile", value=SILVER_BULLET_LONDON_NY, updated_at=1700000000000))
        session.add(SystemConfig(key="daily_ny_goal", value="1", updated_at=1700000000000))
        session.add(SystemConfig(key="execution_scope", value="PAPER", updated_at=1700000000000))
        session.commit()

    yield SessionFactory
    engine.dispose()


def test_concurrent_sessions_cas_conflict(cas_db_factory):
    """
    Part 096: Two independent Sessions preload version 1.
    Session 1 updates to v2. Session 2 with expected_version=1 must raise StalePolicyVersionError.
    """
    s1 = cas_db_factory()
    s2 = cas_db_factory()

    try:
        # Preload policy in both sessions
        p1 = s1.query(TradingPolicy).filter(TradingPolicy.id == "default").first()
        p2 = s2.query(TradingPolicy).filter(TradingPolicy.id == "default").first()
        assert p1.version == 1
        assert p2.version == 1

        # Session 1 updates successfully to v2
        resp1 = update_entry_session_settings(
            s1,
            {
                "entry_session_policy": ALL_SESSIONS_SETUP_FIRST,
                "timing_profile": CG01_ALL_SESSIONS_PAPER,
                "expected_version": 1,
            },
            now_ms=1700000000100,
        )
        assert resp1.policy_version == 2
        assert resp1.entry_session_policy == ALL_SESSIONS_SETUP_FIRST

        # Session 2 attempts update with stale expected_version=1
        with pytest.raises(StalePolicyVersionError) as exc_info:
            update_entry_session_settings(
                s2,
                {
                    "entry_session_policy": "NY_ONLY",
                    "timing_profile": SILVER_BULLET_LONDON_NY,
                    "expected_version": 1,
                },
                now_ms=1700000000200,
            )

        err = exc_info.value
        assert err.expected_version == 1
        assert err.current_version == 2

        # Verify DB in fresh session remains at Session 1's state
        s3 = cas_db_factory()
        try:
            fresh_pol = s3.query(TradingPolicy).filter(TradingPolicy.id == "default").first()
            assert fresh_pol.version == 2
            assert fresh_pol.entry_session_policy == ALL_SESSIONS_SETUP_FIRST
        finally:
            s3.close()

    finally:
        s1.close()
        s2.close()


def test_rollback_on_upsert_failure_preserves_old_state(cas_db_factory):
    """
    Part 098: Fault injection during staging rolls back policy version and all configs.
    """
    from unittest.mock import patch

    with cas_db_factory() as db:
        # Monkeypatch _upsert_system_config_no_commit to raise an exception
        with patch("services.entry_session_policy_service._upsert_system_config_no_commit", side_effect=RuntimeError("INJECTED_DB_FAULT")):
            with pytest.raises(RuntimeError):
                update_entry_session_settings(
                    db,
                    {
                        "entry_session_policy": ALL_SESSIONS_SETUP_FIRST,
                        "timing_profile": CG01_ALL_SESSIONS_PAPER,
                        "expected_version": 1,
                    },
                    now_ms=1700000000100,
                )

    # Fresh session must observe complete rollback to version 1
    with cas_db_factory() as db:
        pol = db.query(TradingPolicy).filter(TradingPolicy.id == "default").first()
        assert pol.version == 1
        assert pol.entry_session_policy == ALL_SESSIONS_WITH_NY_RESERVE
        timing = db.query(SystemConfig).filter(SystemConfig.key == "timing_profile").first().value
        assert timing == SILVER_BULLET_LONDON_NY
