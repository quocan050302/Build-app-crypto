import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, TradingPolicy, SystemConfig
from services.entry_session_policy_service import (
    update_entry_session_settings,
    SessionPolicyConfigError,
    StalePolicyVersionError,
)
from services.trading_policy_service import TradingPolicyService
from services.clock import ReplayClock


@pytest.fixture
def factory(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "probe.db"))
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    with S() as db:
        db.add(
            TradingPolicy(
                id="default",
                entry_session_policy="ALL_SESSIONS_WITH_NY_RESERVE",
                ny_min_fills=1,
                reserve_ny_slot=True,
                ny_fallback_enabled=True,
                version=1,
                updated_at=1700000000000,
            )
        )
        db.add(SystemConfig(key="timing_profile", value="SILVER_BULLET_LONDON_NY", updated_at=1700000000000))
        db.commit()
    yield S
    engine.dispose()


def patch(**kwargs):
    return dict(
        entry_session_policy="ALL_SESSIONS_SETUP_FIRST",
        timing_profile="CG01_ALL_SESSIONS_PAPER",
        expected_version=1,
        **kwargs,
    )


def test_persisted_live_scope_cannot_be_overridden_by_client_paper(factory):
    with factory() as db:
        db.add(SystemConfig(key="execution_scope", value="LIVE", updated_at=1700000000000))
        db.commit()
        with pytest.raises(SessionPolicyConfigError):
            update_entry_session_settings(db, patch(execution_scope="PAPER"), now_ms=1700000000100)


def test_stale_cached_session_must_not_overwrite_newer_committed_version(factory):
    first, second = factory(), factory()
    try:
        old1 = first.query(TradingPolicy).first()
        old2 = second.query(TradingPolicy).first()
        assert old1.version == old2.version == 1
        update_entry_session_settings(first, patch(), now_ms=1700000000100)
        # A second HTTP request had already read the old policy; this must be a CAS conflict.
        with pytest.raises(StalePolicyVersionError):
            update_entry_session_settings(
                second,
                dict(entry_session_policy="NY_ONLY", timing_profile="SILVER_BULLET_LONDON_NY", expected_version=1),
                now_ms=1700000000200,
            )
    finally:
        first.close()
        second.close()


def test_zero_target_quota_is_not_fulfilled_0_of_0(factory):
    with factory() as db:
        update_entry_session_settings(db, patch(), now_ms=1700000000100)
        q = TradingPolicyService.update_quota_lifecycle_state(db, clock=ReplayClock(1700000000200))
        assert q.quota_status == "NOT_APPLICABLE", q.last_status_reason
