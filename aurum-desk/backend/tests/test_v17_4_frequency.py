"""
backend/tests/test_v17_4_frequency.py
V17.4 Parts 071 & 158:
- Test evaluate_daily_frequency_goal across NY window close and Vietnam trade day bounds.
- In ALL_SESSIONS_SETUP_FIRST, 11:30 NY (AFTER_WINDOW) does NOT trigger SHORTFALL while VN day is still active.
- At 23:59 VN (still before midnight), status is SEARCHING.
- Once the trade day concludes in daily_timezone (or next day begins), the day accounting bounds advance correctly.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import crud
from models import Base, SystemConfig, TradingPolicy
from policy_constants import ALL_SESSIONS_SETUP_FIRST, ALL_SESSIONS_WITH_NY_RESERVE
from services.trading_policy_service import (
    IClock,
    TradingPolicyService,
    resolve_trade_day_bounds,
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

    def advance_to(self, target_ms: int):
        self._now_ms = target_ms


@pytest.fixture
def freq_db():
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
        daily_min_goal=2,
        daily_timezone="Asia/Ho_Chi_Minh",
        version=1,
        updated_at=1700000000000,
    )
    session.add(policy)
    session.add(SystemConfig(key="timing_profile", value="CG01_ALL_SESSIONS_PAPER", updated_at=1700000000000))
    session.add(SystemConfig(key="daily_ny_goal", value="0", updated_at=1700000000000))
    session.commit()

    try:
        yield session
    finally:
        session.close()


def test_part_158_setup_first_not_shortfall_at_ny_close(freq_db):
    ny_tz = ZoneInfo("America/New_York")
    # 11:30 NY is AFTER_WINDOW for NY session (which ends at 11:00 NY)
    # In VN (UTC+7), this is 22:30 (10:30 PM), so VN trade day has 1.5 hours remaining
    dt_ny_close = datetime(2026, 10, 15, 11, 30, tzinfo=ny_tz)
    now_ms = int(dt_ny_close.timestamp() * 1000)
    clock = DeterministicClock(now_ms)

    policy = freq_db.query(TradingPolicy).first()
    eff_policy = TradingPolicyService.resolve_effective_daily_policy(freq_db)

    # 1. Under ALL_SESSIONS_SETUP_FIRST, with 0 fills: status must remain SEARCHING (not SHORTFALL)
    goal_eval = TradingPolicyService.evaluate_daily_frequency_goal(
        freq_db,
        now_ms=now_ms,
        clock=clock,
        effective_policy=eff_policy,
    )
    assert goal_eval.status == "SEARCHING"
    assert "phiên Mỹ đã đóng nhưng ngày giao dịch vẫn tiếp diễn" in goal_eval.reason_vi

    # 2. In contrast, under legacy ALL_SESSIONS_WITH_NY_RESERVE, NY close with 0 fills is SHORTFALL
    policy.entry_session_policy = ALL_SESSIONS_WITH_NY_RESERVE
    policy.ny_min_fills = 1
    freq_db.commit()
    eff_legacy = TradingPolicyService.resolve_effective_daily_policy(freq_db)

    goal_eval_legacy = TradingPolicyService.evaluate_daily_frequency_goal(
        freq_db,
        now_ms=now_ms,
        clock=clock,
        effective_policy=eff_legacy,
    )
    assert goal_eval_legacy.status == "SHORTFALL"
    assert "DAILY_GOAL_SHORTFALL" in goal_eval_legacy.reason_vi


def test_part_158_trade_day_bounds_transition(freq_db):
    vn_tz = ZoneInfo("Asia/Ho_Chi_Minh")

    # 23:59:00 VN on 2026-10-15
    dt_late_night = datetime(2026, 10, 15, 23, 59, 0, tzinfo=vn_tz)
    t_late = int(dt_late_night.timestamp() * 1000)
    day_key_1, start_1, end_1 = resolve_trade_day_bounds(t_late, "Asia/Ho_Chi_Minh")
    assert day_key_1 == "2026-10-15"

    # 00:01:00 VN on 2026-10-16 (midnight crossed)
    dt_next_day = datetime(2026, 10, 16, 0, 1, 0, tzinfo=vn_tz)
    t_next = int(dt_next_day.timestamp() * 1000)
    day_key_2, start_2, end_2 = resolve_trade_day_bounds(t_next, "Asia/Ho_Chi_Minh")
    assert day_key_2 == "2026-10-16"
    assert start_2 == end_1
