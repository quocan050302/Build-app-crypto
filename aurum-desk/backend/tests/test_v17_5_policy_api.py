import pytest
from database import engine, SessionLocal
from models import Base, SystemConfig
from services.stop_placement_policy_service import (
    resolve_stop_placement_policy,
    update_stop_placement_policy,
    validate_stop_placement_policy,
)
from services.stop_placement_types import StopPlacementMode, StopPlacementPolicy


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.query(SystemConfig).filter(SystemConfig.key.like("stop_placement_v1.%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(SystemConfig).filter(SystemConfig.key.like("stop_placement_v1.%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_resolve_default_shadow_compare_readonly():
    """
    When no keys exist in DB, resolve_stop_placement_policy returns SHADOW_COMPARE.
    GET is pure read-only: does not commit or insert rows into SystemConfig.
    """
    db = SessionLocal()
    try:
        policy = resolve_stop_placement_policy(db)
        assert policy.mode == StopPlacementMode.SHADOW_COMPARE.value
        assert policy.version == 1

        # Check DB count: must still be 0
        count = db.query(SystemConfig).filter(SystemConfig.key.like("stop_placement_v1.%")).count()
        assert count == 0
    finally:
        db.close()


def test_update_policy_cas_and_versioning():
    """
    Updating policy with expected_version succeeds and bumps version.
    Stale expected_version raises 409 error.
    """
    db = SessionLocal()
    try:
        current = resolve_stop_placement_policy(db)
        assert current.version == 1

        # Successful update to PAPER_STRUCTURAL
        new_policy, is_mutation = update_stop_placement_policy(
            db,
            {"mode": "PAPER_STRUCTURAL", "atr_period": 15},
            expected_version=current.version,
            server_scope="PAPER",
        )
        assert is_mutation is True
        assert new_policy.mode == "PAPER_STRUCTURAL"
        assert new_policy.version == 2
        assert new_policy.atr_period == 15

        # Stale CAS update with old version (1 instead of 2) -> raises ValueError with mismatch
        with pytest.raises(ValueError) as exc:
            update_stop_placement_policy(
                db,
                {"mode": "LEGACY"},
                expected_version=1,
                server_scope="PAPER",
            )
        assert "không khớp" in str(exc.value) or "mismatch" in str(exc.value).lower()
    finally:
        db.close()


def test_update_policy_true_noop():
    """
    Sending identical payload with correct expected_version returns is_mutation=False
    and does not increment policy version.
    """
    db = SessionLocal()
    try:
        p1, is_mutation = update_stop_placement_policy(
            db,
            {"mode": "PAPER_STRUCTURAL", "atr_period": 14},
            expected_version=1,
            server_scope="PAPER",
        )
        assert is_mutation is True
        assert p1.version == 2

        # Send same payload
        p2, is_mutation = update_stop_placement_policy(
            db,
            {"mode": "PAPER_STRUCTURAL", "atr_period": 14},
            expected_version=2,
            server_scope="PAPER",
        )
        assert is_mutation is False
        assert p2.version == 2
    finally:
        db.close()


def test_validation_rejects_negative_or_invalid():
    """
    Strict numeric validation rejects non-finite, negative, or invalid mode.
    """
    current = StopPlacementPolicy(mode="SHADOW_COMPARE", version=1)

    # Invalid mode
    with pytest.raises(ValueError) as exc:
        validate_stop_placement_policy({"mode": "MAGIC_MODE"}, current)
    assert "không hợp lệ" in str(exc.value)

    # Negative buffer ticks
    with pytest.raises(ValueError) as exc:
        validate_stop_placement_policy({"min_buffer_ticks": -5}, current)
    assert "không âm" in str(exc.value)


def test_server_scope_guard_blocks_live():
    """
    PAPER_STRUCTURAL cannot be activated if server execution scope is LIVE.
    """
    db = SessionLocal()
    try:
        with pytest.raises(ValueError) as exc:
            update_stop_placement_policy(
                db,
                {"mode": "PAPER_STRUCTURAL"},
                expected_version=1,
                server_scope="LIVE",
            )
        assert "LIVE" in str(exc.value)
    finally:
        db.close()
