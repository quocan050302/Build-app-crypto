"""
backend/tests/test_v17_4_3_atomic_mirrors.py
Tests for stage_entry_session_config_mirrors helper:
- Input validation (strict int, negative, bool, unknown timing)
- Multiple staging calls within same transaction with autoflush=False (db.new pending object resolution)
- Atomicity and rollback on error (no partial writes)
- Verification that helper does not commit, rollback, or bump policy versions
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, SystemConfig, TradingPolicy
from services.entry_session_policy_service import (
    stage_entry_session_config_mirrors,
    _upsert_system_config_no_commit,
    SessionPolicyConfigError,
)


@pytest.fixture
def atomic_db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'atomic_mirrors.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    Factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with Factory() as db:
        db.add(
            TradingPolicy(
                id="default",
                entry_session_policy="ALL_SESSIONS_SETUP_FIRST",
                ny_min_fills=0,
                reserve_ny_slot=False,
                ny_fallback_enabled=False,
                version=2,
                updated_at=1700000000000,
            )
        )
        db.add(SystemConfig(key="timing_profile", value="CG01_ALL_SESSIONS_PAPER", updated_at=1700000000000))
        db.add(SystemConfig(key="daily_ny_goal", value="0", updated_at=1700000000000))
        db.add(SystemConfig(key="execution_scope", value="PAPER", updated_at=1700000000000))
        db.commit()
    try:
        yield Factory
    finally:
        engine.dispose()


def test_helper_rejects_invalid_timing(atomic_db_factory):
    """Case D10: Helper strictly rejects unknown timing profiles before any staging."""
    with atomic_db_factory() as db:
        with pytest.raises(SessionPolicyConfigError) as exc_info:
            stage_entry_session_config_mirrors(
                db,
                timing_profile="INVALID_UNKNOWN_TIMING",
                ny_goal=1,
                now_ms=1700000001000,
            )
        assert exc_info.value.code == "ENTRY_SESSION_CONFIG_CONFLICT"
        assert len(db.dirty) == 0
        assert len(db.new) == 0


def test_helper_rejects_negative_or_boolean_goal(atomic_db_factory):
    """Case D10: Helper strictly rejects negative integers and booleans as goals."""
    with atomic_db_factory() as db:
        # Negative int
        with pytest.raises(SessionPolicyConfigError):
            stage_entry_session_config_mirrors(
                db,
                timing_profile="SILVER_BULLET_LONDON_NY",
                ny_goal=-1,
                now_ms=1700000001000,
            )
        # Boolean (bool is subclass of int in Python, must be type(ny_goal) is int)
        with pytest.raises(SessionPolicyConfigError):
            stage_entry_session_config_mirrors(
                db,
                timing_profile="SILVER_BULLET_LONDON_NY",
                ny_goal=True,  # type: ignore
                now_ms=1700000001000,
            )


def test_helper_autoflush_false_same_key_twice_pending_row(atomic_db_factory):
    """
    Case D12 (PHẦN 076, 129):
    When session autoflush=False and stage helper is called twice with same key,
    it reuses the pending object in db.new and does NOT cause unique constraint failure.
    """
    with atomic_db_factory() as db:
        assert db.autoflush is False

        # First stage
        stage_entry_session_config_mirrors(
            db,
            timing_profile="SILVER_BULLET_LONDON_NY",
            ny_goal=1,
            now_ms=1700000001000,
        )

        # Second stage in same uncommitted transaction
        stage_entry_session_config_mirrors(
            db,
            timing_profile="CG01_NY_PAPER_EXPANDED",
            ny_goal=2,
            now_ms=1700000002000,
        )

        # Commit must succeed without duplicate key IntegrityError
        db.commit()

    # Verify final values in fresh session
    with atomic_db_factory() as db:
        tp_rows = db.query(SystemConfig).filter(SystemConfig.key == "timing_profile").all()
        ny_rows = db.query(SystemConfig).filter(SystemConfig.key == "daily_ny_goal").all()
        assert len(tp_rows) == 1
        assert len(ny_rows) == 1
        assert tp_rows[0].value == "CG01_NY_PAPER_EXPANDED"
        assert ny_rows[0].value == "2"


def test_helper_does_not_commit_or_bump_version(atomic_db_factory):
    """
    PHẦN 036, 073, 125:
    Calling stage_entry_session_config_mirrors does NOT commit the transaction
    and does NOT modify or bump TradingPolicy.version.
    """
    with atomic_db_factory() as db:
        pol_before = db.query(TradingPolicy).filter_by(id="default").one()
        v_before = pol_before.version

        stage_entry_session_config_mirrors(
            db,
            timing_profile="SILVER_BULLET_LONDON_NY",
            ny_goal=1,
            now_ms=1700000001000,
        )

        # Policy version must not be touched by helper
        pol_after = db.query(TradingPolicy).filter_by(id="default").one()
        assert pol_after.version == v_before

        # Rollback discards staged SystemConfig changes
        db.rollback()

    # Verify fresh session still has initial values
    with atomic_db_factory() as db:
        tp = db.query(SystemConfig).filter_by(key="timing_profile").one()
        ny = db.query(SystemConfig).filter_by(key="daily_ny_goal").one()
        assert tp.value == "CG01_ALL_SESSIONS_PAPER"
        assert ny.value == "0"


def test_fault_injection_rollback_preserves_initial_state(atomic_db_factory):
    """
    Case D13 (PHẦN 121, 122):
    If an exception occurs during staging or before commit, rolling back the
    transaction cleanly restores all rows to their original values.
    """
    with atomic_db_factory() as db:
        try:
            # 1. Mutate policy in transaction
            pol = db.query(TradingPolicy).filter_by(id="default").one()
            pol.entry_session_policy = "ALL_SESSIONS_WITH_NY_RESERVE"
            pol.ny_min_fills = 1

            # 2. Stage mirrors
            stage_entry_session_config_mirrors(
                db,
                timing_profile="SILVER_BULLET_LONDON_NY",
                ny_goal=1,
                now_ms=1700000001000,
            )

            # 3. Injected failure before commit
            raise RuntimeError("SIMULATED_DB_FAILURE_BEFORE_COMMIT")
        except RuntimeError:
            db.rollback()

    # Fresh session reads initial values untouched
    with atomic_db_factory() as db:
        pol = db.query(TradingPolicy).filter_by(id="default").one()
        tp = db.query(SystemConfig).filter_by(key="timing_profile").one()
        ny = db.query(SystemConfig).filter_by(key="daily_ny_goal").one()
        assert pol.entry_session_policy == "ALL_SESSIONS_SETUP_FIRST"
        assert pol.ny_min_fills == 0
        assert tp.value == "CG01_ALL_SESSIONS_PAPER"
        assert ny.value == "0"
