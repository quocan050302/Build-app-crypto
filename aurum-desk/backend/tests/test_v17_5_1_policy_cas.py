"""
Aurum V17.5.1 — Policy CAS Database Concurrency & Fence Tests
Verifies conditional SQL CAS update, StopPolicyConflictError on stale expected_version,
no-op preservation, and strict scope validation.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, SystemConfig
from services.stop_placement_policy_service import (
    update_stop_placement_policy,
    resolve_stop_placement_policy,
    StopPolicyConflictError,
    StopPolicyValidationError,
)


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "cas_test.db"))
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)


def test_concurrent_sessions_lost_update_raises_conflict(db_factory):
    with db_factory() as db:
        # Bootstrap to version 1
        update_stop_placement_policy(db, {"mode": "LEGACY"}, expected_version=1, now_ms=1000)

    with db_factory() as s1, db_factory() as s2:
        # Both sessions read version 2
        p1 = resolve_stop_placement_policy(s1)
        p2 = resolve_stop_placement_policy(s2)
        assert p1.version == 2
        assert p2.version == 2

        # Session 1 updates successfully to version 3
        p_res1, changed = update_stop_placement_policy(s1, {"atr_period": 20}, expected_version=2, now_ms=2000)
        assert changed is True
        assert p_res1.version == 3

        # Session 2 attempts update with stale expected_version=2 -> must raise StopPolicyConflictError
        with pytest.raises(StopPolicyConflictError) as exc_info:
            update_stop_placement_policy(s2, {"min_buffer_ticks": 10}, expected_version=2, now_ms=3000)
        assert exc_info.value.fresh_version == 3


def test_noop_update_preserves_version(db_factory):
    with db_factory() as db:
        res1, _ = update_stop_placement_policy(db, {"mode": "SHADOW_COMPARE", "min_buffer_ticks": 5}, expected_version=1, now_ms=1000)
        v1 = res1.version

        # Submit identical parameters -> no-op must not increment version
        res2, changed = update_stop_placement_policy(db, {"mode": "SHADOW_COMPARE", "min_buffer_ticks": 5}, expected_version=v1, now_ms=2000)
        assert changed is False, "No-op policy update must report changed=False"
        assert res2.version == v1, "No-op policy update must not bump version"


def test_scope_validation_rejects_live_under_paper_mode(db_factory):
    with db_factory() as db:
        with pytest.raises(StopPolicyValidationError):
            update_stop_placement_policy(
                db,
                {"mode": "PAPER_STRUCTURAL"},
                expected_version=1,
                server_scope="LIVE",  # Must be rejected because PAPER_STRUCTURAL is paper-only
                now_ms=1000,
            )
