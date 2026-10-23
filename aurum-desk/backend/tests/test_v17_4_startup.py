"""
backend/tests/test_v17_4_startup.py
V17.4 Parts 148 & 149:
- Part 148: Startup with existing legacy marker does not revert ALL_SESSIONS_SETUP_FIRST.
- Part 149: Startup on fresh database without legacy marker preserves ALL_SESSIONS_SETUP_FIRST.
- Verification of zero infinite duplicate domain events / version bumps.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from models import Base, DomainEvent, SystemConfig, TradingPolicy
from policy_constants import ALL_SESSIONS_SETUP_FIRST
from services.clock import ReplayClock
from services.trading_policy_service import (
    TradingPolicyService,
    apply_runtime_contract_upgrades,
)


@pytest.fixture
def startup_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        yield session
    finally:
        session.close()


def test_part_149_startup_without_marker_preserves_setup_first(startup_db):
    """
    Part 149: Database has ALL_SESSIONS_SETUP_FIRST but NO legacy migration marker.
    Startup upgrader must NOT overwrite it to ALL_SESSIONS_WITH_NY_RESERVE.
    """
    policy = TradingPolicy(
        entry_session_policy=ALL_SESSIONS_SETUP_FIRST,
        ny_min_fills=0,
        reserve_ny_slot=False,
        ny_fallback_enabled=False,
        max_daily_fills=3,
        version=1,
        updated_at=1700000000000,
    )
    startup_db.add(policy)
    startup_db.add(SystemConfig(key="timing_profile", value="CG01_ALL_SESSIONS_PAPER", updated_at=1700000000000))
    startup_db.add(SystemConfig(key="daily_ny_goal", value="0", updated_at=1700000000000))
    startup_db.commit()

    clock = ReplayClock(1700000010000)

    # 1. Run upgrade
    res1 = apply_runtime_contract_upgrades(startup_db, deployment_scope="XAUUSDT", now_ms=clock.now_ms())
    assert res1.changed is False
    assert res1.status == "PRESERVED_ALL_SESSIONS_SETUP_FIRST"
    assert res1.after_version == 1

    # Verify policy remains ALL_SESSIONS_SETUP_FIRST
    startup_db.refresh(policy)
    assert policy.entry_session_policy == ALL_SESSIONS_SETUP_FIRST
    assert policy.version == 1
    assert policy.ny_min_fills == 0
    assert policy.reserve_ny_slot is False

    # 2. Run upgrade a second time (simulating repeated restarts)
    clock.advance_by(5000)
    res2 = apply_runtime_contract_upgrades(startup_db, deployment_scope="XAUUSDT", now_ms=clock.now_ms())
    assert res2.changed is False
    assert res2.after_version == 1
    assert startup_db.query(DomainEvent).count() == 0


def test_part_148_startup_with_existing_legacy_marker_preserves_setup_first(startup_db):
    """
    Part 148: Database has legacy marker V14_4_5, then user activates ALL_SESSIONS_SETUP_FIRST.
    Subsequent startup must NOT revert to legacy mode.
    """
    # 1. Initial state with legacy marker
    policy = TradingPolicy(
        entry_session_policy="ALL_SESSIONS_WITH_NY_RESERVE",
        ny_min_fills=1,
        reserve_ny_slot=True,
        ny_fallback_enabled=True,
        max_daily_fills=3,
        version=2,
        updated_at=1700000000000,
    )
    startup_db.add(policy)

    marker_key = "mig_marker:V14_4_5_ALL_SESSIONS_RESERVE_CONTRACT:XAUUSDT"
    startup_db.add(SystemConfig(key=marker_key, value='{"status": "APPLIED"}', updated_at=1700000000000))
    startup_db.commit()

    # 2. User activates ALL_SESSIONS_SETUP_FIRST (version becomes 3)
    policy.entry_session_policy = ALL_SESSIONS_SETUP_FIRST
    policy.ny_min_fills = 0
    policy.reserve_ny_slot = False
    policy.ny_fallback_enabled = False
    policy.version = 3
    startup_db.commit()

    clock = ReplayClock(1700000020000)

    # 3. Startup upgrader runs
    res = apply_runtime_contract_upgrades(startup_db, deployment_scope="XAUUSDT", now_ms=clock.now_ms())
    assert res.changed is False

    startup_db.refresh(policy)
    assert policy.entry_session_policy == ALL_SESSIONS_SETUP_FIRST
    assert policy.version == 3
    assert policy.ny_min_fills == 0
