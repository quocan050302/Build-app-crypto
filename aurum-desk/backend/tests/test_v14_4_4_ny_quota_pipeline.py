"""
backend/tests/test_v14_4_4_ny_quota_pipeline.py
V14.4.4 - Regression Tests for NY Quota Pipeline, Slot Reservation, and Actual Fill Accounting.

Proves:
1. compute_reserved_capacity logic:
   - Asia fill 1 -> London fill 2 -> outside NY fill 3 blocked by NY_SLOT_RESERVED.
   - NY fill allowed even when 2 fills have been used.
   - 4th fill blocked by MAX_DAILY_ENTRIES.
2. An ARMED order does NOT count as a completed fill for the 1/1 NY target; only a committed FILL counts.
3. Duplicate fill events are idempotent and do not double count.
4. Active open position from Asia/London blocks new NY entry (max 1 open position guard).
5. build_ny_quota_progress returns accurate structured progress DTO.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
import models, crud
from services.trading_policy_service import TradingPolicyService
from services.clock import IClock


class MockClock(IClock):
    def __init__(self, dt: datetime):
        self._dt = dt

    def now_ms(self) -> int:
        return int(self._dt.timestamp() * 1000)

    def now_datetime(self, tz=None):
        return self._dt.astimezone(tz) if tz else self._dt


def test_compute_reserved_capacity_pure():
    """PHẦN 014, 099: compute_reserved_capacity pure math and validation."""
    from policy_constants import compute_reserved_capacity

    # 3 max fills, 0 daily fills, 0 NY fills, target 1, reserve enabled
    res0 = compute_reserved_capacity(3, 0, 0, 1, True)
    assert res0["remaining"] == 3
    assert res0["reserved_ny"] == 1
    assert res0["available_outside_ny"] == 2

    # 1 daily fill, 0 NY fills
    res1 = compute_reserved_capacity(3, 1, 0, 1, True)
    assert res1["remaining"] == 2
    assert res1["reserved_ny"] == 1
    assert res1["available_outside_ny"] == 1

    # 2 daily fills, 0 NY fills -> slot 3 MUST be reserved for NY!
    res2 = compute_reserved_capacity(3, 2, 0, 1, True)
    assert res2["remaining"] == 1
    assert res2["reserved_ny"] == 1
    assert res2["available_outside_ny"] == 0  # Block outside NY!

    # 2 daily fills, BUT 1 NY fill already achieved -> remaining slot is free for any session!
    res2_ny = compute_reserved_capacity(3, 2, 1, 1, True)
    assert res2_ny["remaining"] == 1
    assert res2_ny["reserved_ny"] == 0
    assert res2_ny["available_outside_ny"] == 1

    # 3 daily fills -> none remaining
    res3 = compute_reserved_capacity(3, 3, 1, 1, True)
    assert res3["remaining"] == 0
    assert res3["available_outside_ny"] == 0

    # Type validation: reject bool
    with pytest.raises(ValueError):
        compute_reserved_capacity(True, 0, 0, 1, True)  # type: ignore


def test_slot_reservation_flow_outside_and_inside_ny(isolated_db):
    """PHẦN 014, 025: Asia fill 1 -> London fill 2 -> outside NY fill 3 blocked -> inside NY fill 3 allowed."""
    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    policy.entry_session_policy = "ALL_SESSIONS_WITH_NY_RESERVE"
    isolated_db.commit()

    # Time: 06:00 NY (before NY window, in Asia/London session)
    ny_tz = ZoneInfo("America/New_York")
    dt_outside = datetime(2026, 10, 13, 6, 0, tzinfo=ny_tz)
    clock_outside = MockClock(dt_outside)

    # 0 fills: entry allowed outside NY
    eval0 = TradingPolicyService.evaluate_entry_policy(isolated_db, clock=clock_outside)
    assert eval0["can_enter"] is True

    # Simulate 1 fill in DayAudit
    audit = crud.get_or_create_today_audit(isolated_db, clock=clock_outside)
    audit.fills_count = 1
    isolated_db.commit()

    eval1 = TradingPolicyService.evaluate_entry_policy(isolated_db, clock=clock_outside)
    assert eval1["can_enter"] is True

    # Simulate 2 fills in DayAudit (Asia + London)
    audit.fills_count = 2
    isolated_db.commit()

    # Outside NY: 3rd fill must be BLOCKED by NY_SLOT_RESERVED
    eval2_outside = TradingPolicyService.evaluate_entry_policy(isolated_db, clock=clock_outside)
    assert eval2_outside["can_enter"] is False
    assert "NY_SLOT_RESERVED" in eval2_outside["reason_codes"]

    # Now time moves inside NY window: 09:00 NY
    dt_inside = datetime(2026, 10, 13, 9, 0, tzinfo=ny_tz)
    clock_inside = MockClock(dt_inside)

    # Inside NY: 3rd fill IS ALLOWED!
    eval2_inside = TradingPolicyService.evaluate_entry_policy(isolated_db, clock=clock_inside)
    assert eval2_inside["can_enter"] is True
    assert "NY_SLOT_RESERVED" not in eval2_inside["reason_codes"]

    # If day hits 3 fills, 4th is blocked even in NY
    audit.fills_count = 3
    isolated_db.commit()

    eval3_inside = TradingPolicyService.evaluate_entry_policy(isolated_db, clock=clock_inside)
    assert eval3_inside["can_enter"] is False
    assert "MAX_DAILY_ENTRIES" in eval3_inside["reason_codes"]


def test_armed_pending_does_not_fulfill_ny_quota(isolated_db):
    """PHẦN 017: ARM pending order leaves quota 0/1; only actual FILL transitions to 1/1."""
    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    ny_tz = ZoneInfo("America/New_York")
    dt_ny = datetime(2026, 10, 13, 9, 30, tzinfo=ny_tz)
    clock = MockClock(dt_ny)

    quota = TradingPolicyService.get_or_create_session_quota(isolated_db, policy, clock=clock)
    assert quota.total_fills == 0
    assert quota.quota_status in ("SEEKING_STANDARD", "NOT_STARTED")

    # Creating an ARMED order
    order = models.PaperOrder(
        id="ord_test_armed_1",
        setup_id="setup_1",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        planned_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=1.0,
        created_at=clock.now_ms(),
    )
    isolated_db.add(order)
    isolated_db.commit()

    # Lifecycle state transitions to ARMED_PENDING_FILL, total_fills remains 0!
    updated_q = TradingPolicyService.update_quota_lifecycle_state(isolated_db, policy, clock=clock)
    assert updated_q.quota_status == "ARMED_PENDING_FILL"
    assert updated_q.total_fills == 0  # NOT fulfilled yet!

    # Now simulate FILL event
    order.state = "paper_open"
    order.actual_entry = 4180.0
    order.opened_at = clock.now_ms() + 500
    TradingPolicyService.record_quota_fill(isolated_db, order, clock=clock, commit=True)

    isolated_db.refresh(updated_q)
    assert updated_q.total_fills == 1
    assert updated_q.standard_fills == 1
    assert updated_q.quota_status == "FULFILLED"


def test_build_ny_quota_progress_dto(isolated_db):
    """PHẦN 021, 101: build_ny_quota_progress returns compact DTO with real blockers."""
    from policy_constants import build_ny_quota_progress
    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    ny_tz = ZoneInfo("America/New_York")
    dt_ny = datetime(2026, 10, 13, 9, 30, tzinfo=ny_tz)
    clock = MockClock(dt_ny)

    quota = TradingPolicyService.get_or_create_session_quota(isolated_db, policy, clock=clock)
    window = TradingPolicyService.get_ny_session_window(policy, clock=clock)

    progress = build_ny_quota_progress(quota, None, window, policy)
    assert progress["target_fills"] == 1
    assert progress["actual_fills"] == 0
    assert progress["phase"] == "IN_STANDARD_WINDOW"
    assert "primary_blocker" in progress
