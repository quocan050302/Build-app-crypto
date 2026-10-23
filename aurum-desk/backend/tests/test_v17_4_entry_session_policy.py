"""
backend/tests/test_v17_4_entry_session_policy.py
V17.4 Parts 080, 154, 157, 159:
- Direct testing of resolve_entry_session_context with exact model ID CG01_SWEEP_MSS_FVG.
- Policy matrix: 3 policies x strategy families x inside/outside NY x fill counts.
- Reservation handling: 2 fills outside NY -> legacy reserves slot, setup-first does not.
- 3 fills -> both block (MAX_DAILY_ENTRIES).
- Analytics session 'OFF' does not block setup-first.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import crud
from models import Base, TradingPolicy
from policy_constants import (
    ALL_SESSIONS_SETUP_FIRST,
    ALL_SESSIONS_WITH_NY_RESERVE,
    NY_ONLY,
)
from services.scalp_m1_types import ModelIdentifier
from services.trading_policy_service import (
    TradingPolicyService,
    resolve_entry_session_context,
    IClock,
)


class DeterministicClock(IClock):
    def __init__(self, now_ms: int):
        self._now_ms = now_ms

    def now_ms(self) -> int:
        return self._now_ms

    def now_datetime(self) -> datetime:
        return datetime.fromtimestamp(self._now_ms / 1000.0, tz=ZoneInfo("UTC"))

    def monotonic_s(self) -> float:
        return self._now_ms / 1000.0


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    policy = TradingPolicy(
        entry_session_policy=ALL_SESSIONS_SETUP_FIRST,
        ny_min_fills=0,
        reserve_ny_slot=False,
        ny_fallback_enabled=False,
        max_daily_fills=3,
        updated_at=1700000000000,
    )
    session.add(policy)
    session.commit()
    try:
        yield session
    finally:
        session.close()


def test_part_154_cg01_model_id_mapping(db_session):
    policy = db_session.query(TradingPolicy).first()
    ny_tz = ZoneInfo("America/New_York")

    # Time outside NY: Asia session (20:30 NY)
    dt_asia = datetime(2026, 10, 15, 20, 30, tzinfo=ny_tz)
    now_ms = int(dt_asia.timestamp() * 1000)

    # 1. CG01_SWEEP_MSS_FVG with ALL_SESSIONS_SETUP_FIRST
    ctx_setup_first = resolve_entry_session_context(
        policy=policy,
        family=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        now_ms=now_ms,
    )
    assert ctx_setup_first.standard_window_restriction is None
    assert ctx_setup_first.expiry_cap_ms is None
    assert ctx_setup_first.policy_mode == ALL_SESSIONS_SETUP_FIRST

    # 2. CG01_SWEEP_MSS_FVG with NY_ONLY
    policy.entry_session_policy = NY_ONLY
    policy.ny_min_fills = 1
    db_session.commit()

    ctx_ny_only = resolve_entry_session_context(
        policy=policy,
        family=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        now_ms=now_ms,
    )
    assert ctx_ny_only.standard_window_restriction is not None
    assert ctx_ny_only.expiry_cap_ms is not None
    assert ctx_ny_only.in_ny_entry_window is False

    # 3. Unknown strategy family must fail closed / receive unknown restriction
    ctx_unknown = resolve_entry_session_context(
        policy=policy,
        family="UNKNOWN_ARBITRARY_FAMILY",
        now_ms=now_ms,
    )
    assert "UNKNOWN_STRATEGY_FAMILY" in ctx_unknown.reason_codes


def test_part_157_reservation_at_two_fills(db_session):
    ny_tz = ZoneInfo("America/New_York")
    dt_london = datetime(2026, 10, 15, 4, 30, tzinfo=ny_tz)
    now_ms = int(dt_london.timestamp() * 1000)
    clock = DeterministicClock(now_ms)

    # Set daily fills count to 2
    audit = crud.get_or_create_today_audit(db_session, clock=clock)
    audit.fills_count = 2
    db_session.commit()

    # Setup 1: ALL_SESSIONS_WITH_NY_RESERVE with 2 fills outside NY (remaining=1)
    policy = db_session.query(TradingPolicy).first()
    policy.entry_session_policy = ALL_SESSIONS_WITH_NY_RESERVE
    policy.ny_min_fills = 1
    policy.reserve_ny_slot = True
    db_session.commit()

    eval_legacy = TradingPolicyService.evaluate_entry_policy(
        db_session,
        now_ms=now_ms,
        strategy_family=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        clock=clock,
    )
    assert eval_legacy["can_enter"] is False
    assert eval_legacy["reserved_slots"] == 1
    assert "NY_SLOT_RESERVED" in eval_legacy["reason_codes"]

    # Setup 2: ALL_SESSIONS_SETUP_FIRST with 2 fills outside NY (remaining=1)
    policy.entry_session_policy = ALL_SESSIONS_SETUP_FIRST
    policy.ny_min_fills = 0
    policy.reserve_ny_slot = False
    policy.ny_fallback_enabled = False
    db_session.commit()

    eval_setup_first = TradingPolicyService.evaluate_entry_policy(
        db_session,
        now_ms=now_ms,
        strategy_family=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        clock=clock,
    )
    assert eval_setup_first["can_enter"] is True
    assert eval_setup_first["reserved_slots"] == 0


def test_part_157_daily_cap_reached_at_three_fills(db_session):
    ny_tz = ZoneInfo("America/New_York")
    dt_ny = datetime(2026, 10, 15, 9, 30, tzinfo=ny_tz)
    now_ms = int(dt_ny.timestamp() * 1000)
    clock = DeterministicClock(now_ms)

    # Set daily fills count to 3
    audit = crud.get_or_create_today_audit(db_session, clock=clock)
    audit.fills_count = 3
    db_session.commit()

    eval_capped = TradingPolicyService.evaluate_entry_policy(
        db_session,
        now_ms=now_ms,
        strategy_family=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        clock=clock,
    )
    assert eval_capped["can_enter"] is False
    assert "MAX_DAILY_ENTRIES" in eval_capped["reason_codes"]


def test_part_159_analytics_session_off_allowed_in_setup_first(db_session):
    ny_tz = ZoneInfo("America/New_York")
    # 17:30 NY is outside Asia, London, and NY active ranges -> session_for returns 'OFF'
    dt_off = datetime(2026, 10, 15, 17, 30, tzinfo=ny_tz)
    now_ms = int(dt_off.timestamp() * 1000)
    clock = DeterministicClock(now_ms)

    ctx = resolve_entry_session_context(
        policy=db_session.query(TradingPolicy).first(),
        family=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        now_ms=now_ms,
        clock=clock,
    )
    assert ctx.standard_window_restriction is None
    assert ctx.expiry_cap_ms is None

    # Entry policy evaluation should allow entry if daily fill count < 3
    eval_res = TradingPolicyService.evaluate_entry_policy(
        db_session,
        now_ms=now_ms,
        strategy_family=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        clock=clock,
    )
    assert eval_res["can_enter"] is True
    assert eval_res["reserved_slots"] == 0
