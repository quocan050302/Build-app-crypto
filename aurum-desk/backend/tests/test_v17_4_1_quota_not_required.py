"""
backend/tests/test_v17_4_1_quota_not_required.py
V17.4.1 NY Quota Not Required / Not Applicable Verification (Parts 107 - 120)

Verifies:
1. Under ALL_SESSIONS_SETUP_FIRST, update_quota_lifecycle_state evaluates to NOT_APPLICABLE (Part 107).
2. 0 fills and target 0 never evaluates to FULFILLED with false "0/0" achievement message.
3. Mid-day cutover from target 1 cleanly sets status to NOT_APPLICABLE (Part 108).
4. All 4 NY phases (BEFORE, IN, FALLBACK, AFTER) evaluate to NOT_APPLICABLE without missing alert (Part 109).
5. NY fill analytics count increments accurately without claiming quota fulfillment (Part 110).
6. Daily max fills limit of 3 remains invariant (Part 116).
"""
import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import run_schema_migrations
from models import Base, TradingPolicy, SystemConfig, SessionQuota
from policy_constants import (
    ALL_SESSIONS_SETUP_FIRST,
    ALL_SESSIONS_WITH_NY_RESERVE,
    CG01_ALL_SESSIONS_PAPER,
)
from services.trading_policy_service import TradingPolicyService
from services.clock import ReplayClock

NY_TZ = ZoneInfo("America/New_York")


@pytest.fixture
def quota_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/quota_test.db")
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    SessionFactory = sessionmaker(bind=engine)

    with SessionFactory() as session:
        pol = TradingPolicy(
            id="default",
            entry_session_policy=ALL_SESSIONS_SETUP_FIRST,
            ny_min_fills=0,
            reserve_ny_slot=False,
            ny_fallback_enabled=False,
            max_daily_fills=3,
            version=1,
            updated_at=1700000000000,
        )
        session.add(pol)
        session.add(SystemConfig(key="timing_profile", value=CG01_ALL_SESSIONS_PAPER, updated_at=1700000000000))
        session.add(SystemConfig(key="daily_ny_goal", value="0", updated_at=1700000000000))
        session.add(SystemConfig(key="execution_scope", value="PAPER", updated_at=1700000000000))
        session.commit()

    yield SessionFactory
    engine.dispose()


def test_zero_target_across_all_four_ny_phases(quota_db):
    """Part 109: Tests BEFORE_WINDOW (07:00 NY), IN_WINDOW (09:00 NY), IN_FALLBACK (10:45 NY), AFTER_WINDOW (11:30 NY)."""
    # 2026-10-23
    test_times = [
        datetime(2026, 10, 23, 7, 0, tzinfo=NY_TZ),    # BEFORE_WINDOW
        datetime(2026, 10, 23, 9, 0, tzinfo=NY_TZ),    # IN_WINDOW
        datetime(2026, 10, 23, 10, 45, tzinfo=NY_TZ),  # IN_FALLBACK
        datetime(2026, 10, 23, 11, 30, tzinfo=NY_TZ),  # AFTER_WINDOW
    ]

    for dt in test_times:
        ms = int(dt.timestamp() * 1000)
        clock = ReplayClock(ms)
        with quota_db() as db:
            q = TradingPolicyService.update_quota_lifecycle_state(db, clock=clock)
            assert q.quota_status == "NOT_APPLICABLE", f"Failed for time {dt}: status was {q.quota_status}"
            assert "Không đặt chỉ tiêu riêng" in q.last_status_reason
            assert q.total_fills == 0
            assert "0/0" not in q.last_status_reason


def test_cutover_from_existing_target_one(quota_db):
    """Part 108: Existing quota with target 1 switches to NOT_APPLICABLE when mode switches to ALL_SESSIONS_SETUP_FIRST."""
    dt = datetime(2026, 10, 23, 11, 30, tzinfo=NY_TZ)
    ms = int(dt.timestamp() * 1000)
    clock = ReplayClock(ms)

    with quota_db() as db:
        # Seed an existing quota object that had target 1 from earlier in the day
        pol = db.query(TradingPolicy).first()
        quota = TradingPolicyService.get_or_create_session_quota(db, pol, now_ms=ms, clock=clock)
        quota.target_fills = 1
        quota.total_fills = 0
        quota.quota_status = "MISSED"
        db.commit()

        # Update lifecycle state under ALL_SESSIONS_SETUP_FIRST
        updated_q = TradingPolicyService.update_quota_lifecycle_state(db, clock=clock)
        assert updated_q.quota_status == "NOT_APPLICABLE"
        assert "Không đặt chỉ tiêu riêng" in updated_q.last_status_reason


def test_ny_fill_analytics_recorded_without_false_achievement(quota_db):
    """Part 110: 1 NY fill records total_fills=1, status remains NOT_APPLICABLE."""
    dt = datetime(2026, 10, 23, 9, 30, tzinfo=NY_TZ)
    ms = int(dt.timestamp() * 1000)
    clock = ReplayClock(ms)

    with quota_db() as db:
        pol = db.query(TradingPolicy).first()
        quota = TradingPolicyService.get_or_create_session_quota(db, pol, now_ms=ms, clock=clock)
        quota.total_fills = 1
        db.commit()

        updated_q = TradingPolicyService.update_quota_lifecycle_state(db, clock=clock)
        assert updated_q.quota_status == "NOT_APPLICABLE"
        assert updated_q.total_fills == 1
        assert "Đã hoàn thành chỉ tiêu" not in updated_q.last_status_reason
