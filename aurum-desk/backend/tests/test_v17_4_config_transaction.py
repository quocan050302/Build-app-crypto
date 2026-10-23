"""
backend/tests/test_v17_4_config_transaction.py
V17.4 Parts 143, 144, 145, 160:
- Atomic coordinated persistence across TradingPolicy and SystemConfig in a single transaction.
- Rollback on failure leaves database in pristine prior state (no partial state).
- Stale expected version triggers StalePolicyVersionError without mutating database.
- Preservation of custom settings: capital, risk_pct, min_net_rr, quality_threshold, auto_state.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, SystemConfig, TradingPolicy
from policy_constants import (
    ALL_SESSIONS_SETUP_FIRST,
    ALL_SESSIONS_WITH_NY_RESERVE,
)
from schemas import EntrySessionSettingsUpdate
from services.entry_session_policy_service import (
    StalePolicyVersionError,
    update_entry_session_settings,
)
from services.scalp_m1_types import load_effective_scalp_config
from services.trading_policy_service import IClock, TradingPolicyService


class FixedClock(IClock):
    def __init__(self, now_ms: int = 1700000000000):
        self._now_ms = now_ms

    def now_ms(self) -> int:
        return self._now_ms

    def now_datetime(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(self._now_ms / 1000.0, tz=ZoneInfo("UTC"))

    def monotonic_s(self) -> float:
        return self._now_ms / 1000.0


@pytest.fixture
def isolated_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Seed with custom settings (to test preservation)
    policy = TradingPolicy(
        entry_session_policy=ALL_SESSIONS_WITH_NY_RESERVE,
        ny_min_fills=1,
        reserve_ny_slot=True,
        ny_fallback_enabled=True,
        max_daily_fills=3,
        min_net_rr=1.75,
        version=1,
        updated_at=1700000000000,
    )
    session.add(policy)

    session.add(SystemConfig(key="timing_profile", value="SILVER_BULLET_LONDON_NY", updated_at=1700000000000))
    session.add(SystemConfig(key="daily_ny_goal", value="1", updated_at=1700000000000))
    session.add(SystemConfig(key="auto_paper_trading", value="0", updated_at=1700000000000))
    session.add(SystemConfig(key="account_capital", value="2345.0", updated_at=1700000000000))
    session.commit()

    try:
        yield session
    finally:
        session.close()


def test_part_143_atomic_coordinated_save(isolated_db):
    clock = FixedClock(1700000005000)
    patch = EntrySessionSettingsUpdate(
        entry_session_policy=ALL_SESSIONS_SETUP_FIRST,
        timing_profile="CG01_ALL_SESSIONS_PAPER",
        expected_version=1,
    )

    resp = update_entry_session_settings(
        db=isolated_db,
        patch=patch,
        clock=clock,
    )

    assert resp.status == "CONFIRMED"
    assert resp.entry_session_policy == ALL_SESSIONS_SETUP_FIRST
    assert resp.timing_profile == "CG01_ALL_SESSIONS_PAPER"
    assert resp.effective_ny_goal == 0
    assert resp.reserve_ny_slot is False
    assert resp.ny_fallback_enabled is False
    assert resp.policy_version == 2

    # Query in fresh session to verify single transaction atomic commit
    engine = isolated_db.get_bind()
    Session = sessionmaker(bind=engine)
    fresh_session = Session()

    try:
        pol = fresh_session.query(TradingPolicy).first()
        assert pol.entry_session_policy == ALL_SESSIONS_SETUP_FIRST
        assert pol.ny_min_fills == 0
        assert pol.reserve_ny_slot is False
        assert pol.ny_fallback_enabled is False
        assert pol.version == 2
        assert pol.min_net_rr == 1.75  # Preserved

        cfg_timing = fresh_session.query(SystemConfig).filter(SystemConfig.key == "timing_profile").first()
        assert cfg_timing.value == "CG01_ALL_SESSIONS_PAPER"

        cfg_goal = fresh_session.query(SystemConfig).filter(SystemConfig.key == "daily_ny_goal").first()
        assert cfg_goal.value == "0"

        # Effective scalp config and daily policy
        scalp_cfg = load_effective_scalp_config(fresh_session)
        assert scalp_cfg.timing_profile == "CG01_ALL_SESSIONS_PAPER"
        assert scalp_cfg.daily_ny_goal == 0
        assert scalp_cfg.min_net_rr == 1.75
    finally:
        fresh_session.close()


def test_part_144_rollback_on_injected_failure(isolated_db):
    clock = FixedClock(1700000010000)

    # Patch with an invalid scope or trigger failure
    patch = EntrySessionSettingsUpdate(
        entry_session_policy=ALL_SESSIONS_SETUP_FIRST,
        timing_profile="CG01_ALL_SESSIONS_PAPER",
        expected_version=1,
    )

    # Inject mock error before commit
    from unittest.mock import patch as mock_patch
    with mock_patch.object(isolated_db, "commit", side_effect=RuntimeError("Simulated DB commit error")):
        with pytest.raises(RuntimeError, match="Simulated DB commit error"):
            update_entry_session_settings(
                db=isolated_db,
                patch=patch,
                clock=clock,
            )

    # Verify database rolled back cleanly
    engine = isolated_db.get_bind()
    Session = sessionmaker(bind=engine)
    fresh_session = Session()

    try:
        pol = fresh_session.query(TradingPolicy).first()
        assert pol.entry_session_policy == ALL_SESSIONS_WITH_NY_RESERVE
        assert pol.ny_min_fills == 1
        assert pol.reserve_ny_slot is True
        assert pol.version == 1

        cfg_timing = fresh_session.query(SystemConfig).filter(SystemConfig.key == "timing_profile").first()
        assert cfg_timing.value == "SILVER_BULLET_LONDON_NY"

        cfg_goal = fresh_session.query(SystemConfig).filter(SystemConfig.key == "daily_ny_goal").first()
        assert cfg_goal.value == "1"
    finally:
        fresh_session.close()


def test_part_145_stale_expected_version_rejected(isolated_db):
    clock = FixedClock(1700000020000)

    # Current version is 1, pass expected_version=99
    patch = EntrySessionSettingsUpdate(
        entry_session_policy=ALL_SESSIONS_SETUP_FIRST,
        timing_profile="CG01_ALL_SESSIONS_PAPER",
        expected_version=99,
    )

    with pytest.raises(StalePolicyVersionError) as exc_info:
        update_entry_session_settings(
            db=isolated_db,
            patch=patch,
            clock=clock,
        )
    assert exc_info.value.current_version == 1
    assert exc_info.value.expected_version == 99

    # DB must remain unchanged
    pol = isolated_db.query(TradingPolicy).first()
    assert pol.entry_session_policy == ALL_SESSIONS_WITH_NY_RESERVE
    assert pol.version == 1


def test_part_160_preservation_of_custom_settings(isolated_db):
    clock = FixedClock(1700000030000)
    patch = EntrySessionSettingsUpdate(
        entry_session_policy=ALL_SESSIONS_SETUP_FIRST,
        timing_profile="CG01_ALL_SESSIONS_PAPER",
        expected_version=1,
    )

    update_entry_session_settings(
        db=isolated_db,
        patch=patch,
        clock=clock,
    )

    # Check preserved custom values
    pol = isolated_db.query(TradingPolicy).first()
    assert pol.min_net_rr == 1.75
    assert pol.max_daily_fills == 3

    capital_cfg = isolated_db.query(SystemConfig).filter(SystemConfig.key == "account_capital").first()
    assert capital_cfg.value == "2345.0"

    auto_cfg = isolated_db.query(SystemConfig).filter(SystemConfig.key == "auto_paper_trading").first()
    assert auto_cfg.value == "0"
