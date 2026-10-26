"""
backend/tests/test_v17_4_contracts.py
V17.4 Parts 141 & 142:
- Test enum, validators, dataclasses for ALL_SESSIONS_SETUP_FIRST and CG01_ALL_SESSIONS_PAPER.
- Strict rejection of invalid modes, negative values, booleans, and inconsistent state.
- Test zero NY goal across TradingPolicy, SystemConfig, quota progress, and effective daily policy resolver without 'or 1' bugs.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, SystemConfig, TradingPolicy
from policy_constants import (
    ALL_SESSIONS_SETUP_FIRST,
    ALL_SESSIONS_WITH_NY_RESERVE,
    DEFAULT_POLICY_VALUES,
    NY_ONLY,
    build_ny_quota_progress,
    validate_trading_policy_update,
)
from services.cg01_types import CG01TimingProfile
from services.scalp_m1_types import M1ScalpConfig, load_effective_scalp_config
from services.trading_policy_service import TradingPolicyService


def test_part_141_timing_profile_enum_values():
    assert CG01TimingProfile.CG01_ALL_SESSIONS_PAPER.value == "CG01_ALL_SESSIONS_PAPER"
    assert CG01TimingProfile.SILVER_BULLET_LONDON_NY.value == "SILVER_BULLET_LONDON_NY"
    assert CG01TimingProfile.CG01_NY_PAPER_EXPANDED.value == "CG01_NY_PAPER_EXPANDED"


def test_part_141_scalp_m1_config_validation():
    # Valid timing profile
    cfg = M1ScalpConfig(
        timing_profile="CG01_ALL_SESSIONS_PAPER",
        execution_profile="CG01",
    )
    assert cfg.timing_profile == "CG01_ALL_SESSIONS_PAPER"

    # Invalid timing profile must raise ValueError
    with pytest.raises(ValueError, match="INVALID_TIMING_PROFILE"):
        M1ScalpConfig(timing_profile="UNKNOWN_TIMING_PROFILE")


@pytest.mark.parametrize("mode", [
    ALL_SESSIONS_SETUP_FIRST,
    NY_ONLY,
    ALL_SESSIONS_WITH_NY_RESERVE,
])
def test_part_141_valid_entry_session_policies(mode):
    if mode == ALL_SESSIONS_SETUP_FIRST:
        update = {
            "entry_session_policy": mode,
            "ny_min_fills": 0,
            "reserve_ny_slot": False,
            "ny_fallback_enabled": False,
        }
    else:
        update = {"entry_session_policy": mode}
    validated = validate_trading_policy_update(DEFAULT_POLICY_VALUES, update)
    assert validated["entry_session_policy"] == mode


@pytest.mark.parametrize("invalid_mode", [
    "ALL_SESSIONS",
    "SETUP_FIRST",
    "INVALID_MODE",
    "",
    123,
    True,
    None,
])
def test_part_141_invalid_entry_session_policies_rejected(invalid_mode):
    with pytest.raises(ValueError):
        validate_trading_policy_update(DEFAULT_POLICY_VALUES, {"entry_session_policy": invalid_mode})


def test_part_141_setup_first_rejects_non_zero_ny_goal():
    with pytest.raises(ValueError, match="ny_min_fills bắt buộc phải là 0"):
        validate_trading_policy_update(
            DEFAULT_POLICY_VALUES,
            {
                "entry_session_policy": ALL_SESSIONS_SETUP_FIRST,
                "ny_min_fills": 1,
            },
        )


def test_part_141_setup_first_rejects_reserve_or_fallback():
    with pytest.raises(ValueError, match="reserve_ny_slot không được bật"):
        validate_trading_policy_update(
            DEFAULT_POLICY_VALUES,
            {
                "entry_session_policy": ALL_SESSIONS_SETUP_FIRST,
                "ny_min_fills": 0,
                "reserve_ny_slot": True,
            },
        )

    with pytest.raises(ValueError, match="ny_fallback_enabled không được bật"):
        validate_trading_policy_update(
            DEFAULT_POLICY_VALUES,
            {
                "entry_session_policy": ALL_SESSIONS_SETUP_FIRST,
                "ny_min_fills": 0,
                "reserve_ny_slot": False,
                "ny_fallback_enabled": True,
            },
        )


def test_part_141_risk_limits_preserved():
    # max_daily_fills bounds
    with pytest.raises(ValueError):
        validate_trading_policy_update(DEFAULT_POLICY_VALUES, {"max_daily_fills": 0})
    with pytest.raises(ValueError):
        validate_trading_policy_update(DEFAULT_POLICY_VALUES, {"max_daily_fills": 10})

    # max_open_positions bounds
    with pytest.raises(ValueError):
        validate_trading_policy_update(DEFAULT_POLICY_VALUES, {"max_open_positions": 0})
    with pytest.raises(ValueError):
        validate_trading_policy_update(DEFAULT_POLICY_VALUES, {"max_open_positions": 3})

    # min_net_rr bounds (must be >= 1.5)
    with pytest.raises(ValueError):
        validate_trading_policy_update(DEFAULT_POLICY_VALUES, {"min_net_rr": 1.2})
    validated = validate_trading_policy_update(DEFAULT_POLICY_VALUES, {"min_net_rr": 1.7})
    assert validated["min_net_rr"] == 1.7


def test_part_142_zero_ny_goal_across_sources():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # 1. TradingPolicy
        policy = TradingPolicy(
            entry_session_policy=ALL_SESSIONS_SETUP_FIRST,
            ny_min_fills=0,
            reserve_ny_slot=False,
            ny_fallback_enabled=False,
            max_daily_fills=3,
            updated_at=1700000000000,
        )
        session.add(policy)

        # 2. SystemConfig
        session.add(SystemConfig(key="timing_profile", value="CG01_ALL_SESSIONS_PAPER", updated_at=1700000000000))
        session.add(SystemConfig(key="daily_ny_goal", value="0", updated_at=1700000000000))
        session.commit()

        # Query fresh in new session
        session2 = Session()
        try:
            pol = session2.query(TradingPolicy).first()
            assert pol.entry_session_policy == ALL_SESSIONS_SETUP_FIRST
            assert pol.ny_min_fills == 0
            assert pol.reserve_ny_slot is False
            assert pol.ny_fallback_enabled is False

            # Effective daily policy
            eff = TradingPolicyService.resolve_effective_daily_policy(session2)
            assert eff.ny_goal == 0
            assert eff.reserve_ny_slot is False

            # Quota progress
            prog = build_ny_quota_progress(
                quota=None,
                latest_scan=None,
                window={"phase": "IN_STANDARD_WINDOW", "is_in_window": True},
                policy=pol,
            )
            assert prog["target_fills"] == 0
            assert prog["primary_blocker"] == "NOT_REQUIRED"
            assert "Không đặt chỉ tiêu riêng cho phiên Mỹ" in prog["next_action"]

            # Scalp config loader
            scalp_cfg = load_effective_scalp_config(session2)
            assert scalp_cfg.timing_profile == "CG01_ALL_SESSIONS_PAPER"
            assert scalp_cfg.daily_ny_goal == 0
        finally:
            session2.close()
    finally:
        session.close()
