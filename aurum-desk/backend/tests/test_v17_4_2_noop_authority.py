"""
backend/tests/test_v17_4_2_noop_authority.py
Comprehensive test suite for Database No-Op Authority & Concurrency (N01 - N08).
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, SystemConfig, TradingPolicy, DomainEvent
from services.entry_session_policy_service import (
    update_entry_session_settings,
    StalePolicyVersionError,
    SessionPolicyConfigError,
    apply_entry_session_update_plan,
    build_entry_session_update_plan,
)


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test_noop.db'}",
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


def test_n01_exact_cached_noop_stale_raises_409(session_factory):
    """N01: Session B holding cached v1 tries no-op on old pair after Session A committed v2. Raises 409."""
    a, b = session_factory(), session_factory()
    try:
        held_b_policy = b.query(TradingPolicy).filter_by(id="default").first()
        assert held_b_policy.version == 1

        # Session A mutates to ALL_SESSIONS_SETUP_FIRST (v2)
        update_entry_session_settings(a, {
            "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
            "timing_profile": "CG01_ALL_SESSIONS_PAPER",
            "expected_version": 1,
        }, now_ms=1700000000100)

        # Session B attempts to send its cached state with expected_version=1
        with pytest.raises(StalePolicyVersionError) as exc_info:
            update_entry_session_settings(b, {
                "entry_session_policy": "ALL_SESSIONS_WITH_NY_RESERVE",
                "timing_profile": "SILVER_BULLET_LONDON_NY",
                "expected_version": 1,
            }, now_ms=1700000000200)

        assert exc_info.value.current_version == 2
        assert exc_info.value.expected_version == 1
    finally:
        a.close()
        b.close()


def test_n02_actual_new_noop_no_version_bump_no_audit(session_factory):
    """N02: When DB is already in desired state and expected_version matches, no-op does not bump version or write audit event."""
    with session_factory() as db:
        update_entry_session_settings(db, {
            "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
            "timing_profile": "CG01_ALL_SESSIONS_PAPER",
            "expected_version": 1,
        }, now_ms=1700000000100)

    with session_factory() as db:
        events_before = db.query(DomainEvent).count()
        pol_before = db.query(TradingPolicy).filter_by(id="default").first()
        ver_before = pol_before.version
        updated_before = pol_before.updated_at

        # Call with identical desired state and expected_version=2
        resp = update_entry_session_settings(db, {
            "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
            "timing_profile": "CG01_ALL_SESSIONS_PAPER",
            "expected_version": ver_before,
        }, now_ms=1700000000500)

        assert resp.policy_version == ver_before
        assert resp.entry_session_policy == "ALL_SESSIONS_SETUP_FIRST"
        assert resp.timing_profile == "CG01_ALL_SESSIONS_PAPER"

        # Check DB state
        pol_after = db.query(TradingPolicy).filter_by(id="default").first()
        assert pol_after.version == ver_before
        assert pol_after.updated_at == updated_before
        assert db.query(DomainEvent).count() == events_before


def test_n03_legacy_true_noop_preserves_choices(session_factory):
    """N03: True no-op under legacy ALL_SESSIONS_WITH_NY_RESERVE mode preserves choices without resetting."""
    with session_factory() as db:
        events_before = db.query(DomainEvent).count()
        resp = update_entry_session_settings(db, {
            "entry_session_policy": "ALL_SESSIONS_WITH_NY_RESERVE",
            "timing_profile": "SILVER_BULLET_LONDON_NY",
            "expected_version": 1,
        }, now_ms=1700000000100)

        assert resp.policy_version == 1
        assert resp.entry_session_policy == "ALL_SESSIONS_WITH_NY_RESERVE"
        assert resp.timing_profile == "SILVER_BULLET_LONDON_NY"
        assert resp.effective_ny_goal == 1
        assert resp.reserve_ny_slot is True
        assert resp.ny_fallback_enabled is True
        assert db.query(DomainEvent).count() == events_before


def test_n04_goal_corruption_does_not_silent_noop(session_factory):
    """N04: If DB state is corrupt (policy goal 0, but SystemConfig daily_ny_goal '1'), sending plan with goal 0 is NOT a silent no-op."""
    with session_factory() as db:
        # Intentionally corrupt SystemConfig
        db.query(SystemConfig).filter_by(key="daily_ny_goal").first().value = "1"
        pol = db.query(TradingPolicy).filter_by(id="default").first()
        pol.entry_session_policy = "ALL_SESSIONS_SETUP_FIRST"
        pol.ny_min_fills = 0
        pol.reserve_ny_slot = False
        pol.ny_fallback_enabled = False
        pol.version = 5
        db.query(SystemConfig).filter_by(key="timing_profile").first().value = "CG01_ALL_SESSIONS_PAPER"
        db.commit()

    with session_factory() as db:
        plan = build_entry_session_update_plan(db, {
            "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
            "timing_profile": "CG01_ALL_SESSIONS_PAPER",
            "expected_version": 5,
        }, now_ms=1700000000100)
        # Because SystemConfig daily_ny_goal was '1', is_noop is False -> performs mutating CAS to fix the pair!
        updated, timing, modified = apply_entry_session_update_plan(db, plan)
        db.commit()

        assert modified is True
        assert updated.version == 6
        # SystemConfig is now reconciled to '0'
        cfg_goal = db.query(SystemConfig).filter_by(key="daily_ny_goal").first().value
        assert cfg_goal == "0"


def test_n05_two_session_normal_mutation_cas_regression(session_factory):
    """N05: Two sessions preloaded with version 1: first session mutates to v2, second session with expected v1 gets StalePolicyVersionError."""
    s1, s2 = session_factory(), session_factory()
    try:
        p1 = s1.query(TradingPolicy).filter_by(id="default").first()
        p2 = s2.query(TradingPolicy).filter_by(id="default").first()
        assert p1.version == p2.version == 1

        update_entry_session_settings(s1, {
            "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
            "timing_profile": "CG01_ALL_SESSIONS_PAPER",
            "expected_version": 1,
        }, now_ms=1700000000100)

        with pytest.raises(StalePolicyVersionError):
            update_entry_session_settings(s2, {
                "entry_session_policy": "NY_ONLY",
                "timing_profile": "SILVER_BULLET_LONDON_NY",
                "expected_version": 1,
            }, now_ms=1700000000200)
    finally:
        s1.close()
        s2.close()


def test_n06_concurrent_noop_and_mutation_serial_outcomes(session_factory):
    """N06: If a no-op and a mutation are ordered serially, outcomes are consistent and valid."""
    db = session_factory()
    try:
        # Step 1: True no-op on v1
        resp_noop = update_entry_session_settings(db, {
            "entry_session_policy": "ALL_SESSIONS_WITH_NY_RESERVE",
            "timing_profile": "SILVER_BULLET_LONDON_NY",
            "expected_version": 1,
        }, now_ms=1700000000100)
        assert resp_noop.policy_version == 1

        # Step 2: Mutation with expected_version 1 advances to v2
        resp_mutate = update_entry_session_settings(db, {
            "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
            "timing_profile": "CG01_ALL_SESSIONS_PAPER",
            "expected_version": 1,
        }, now_ms=1700000000200)
        assert resp_mutate.policy_version == 2

        # Step 3: Stale no-op with expected_version 1 fails with 409
        with pytest.raises(StalePolicyVersionError):
            update_entry_session_settings(db, {
                "entry_session_policy": "ALL_SESSIONS_WITH_NY_RESERVE",
                "timing_profile": "SILVER_BULLET_LONDON_NY",
                "expected_version": 1,
            }, now_ms=1700000000300)
    finally:
        db.close()


def test_n07_transaction_fault_rollback(session_factory):
    """N07: If an error occurs prior to commit, all staged paired changes are rolled back cleanly."""
    with session_factory() as db:
        plan = build_entry_session_update_plan(db, {
            "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
            "timing_profile": "CG01_ALL_SESSIONS_PAPER",
            "expected_version": 1,
        }, now_ms=1700000000100)
        updated, timing, modified = apply_entry_session_update_plan(db, plan)
        # Roll back explicitly instead of committing
        db.rollback()

    with session_factory() as db:
        pol = db.query(TradingPolicy).filter_by(id="default").first()
        assert pol.version == 1
        assert pol.entry_session_policy == "ALL_SESSIONS_WITH_NY_RESERVE"
        tp = db.query(SystemConfig).filter_by(key="timing_profile").first().value
        assert tp == "SILVER_BULLET_LONDON_NY"


def test_n08_response_status_pair_version_fresh_truth(session_factory):
    """N08: The returned response reflects canonical committed DB state with status CONFIRMED."""
    with session_factory() as db:
        resp = update_entry_session_settings(db, {
            "entry_session_policy": "ALL_SESSIONS_SETUP_FIRST",
            "timing_profile": "CG01_ALL_SESSIONS_PAPER",
            "expected_version": 1,
        }, now_ms=1700000000100)

        assert resp.status == "CONFIRMED"
        assert resp.policy_version == 2
        assert resp.entry_session_policy == "ALL_SESSIONS_SETUP_FIRST"
        assert resp.timing_profile == "CG01_ALL_SESSIONS_PAPER"
        assert resp.effective_ny_goal == 0
        assert resp.reserve_ny_slot is False
        assert resp.ny_fallback_enabled is False
