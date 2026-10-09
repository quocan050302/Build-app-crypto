"""
V7 Trading Policy & New York Paper Quota Test Suite.
Validates:
1. NY entry window calculation with dynamic IANA timezones and DST (EDT/EST).
2. Out-of-window blocking (NY_ONLY policy returns OUTSIDE_ENTRY_WINDOW).
3. NY paper quota fulfillment (1/1) on genuine PAPER fill during NY window.
4. Daily fills ceiling (max 3 fills per UTC+7 day blocks 4th fill).
5. Slot reservation (ALL_SESSIONS_WITH_NY_RESERVE blocks 3rd fill outside NY).
6. Fallback activation at 10:30 NY (NY_QUOTA_PAPER) with risk cap <= 0.10%.
7. Expiry/Missed quota state after 11:00 NY without fill.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
import models
import crud
from services.trading_policy_service import TradingPolicyService, IClock
from services.ny_fallback_service import NYFallbackService


class MockClock(IClock):
    def __init__(self, dt: datetime):
        self._dt = dt

    def now(self) -> datetime:
        return self._dt

    def now_datetime(self) -> datetime:
        return self._dt

    def now_ms(self) -> int:
        return int(self._dt.timestamp() * 1000)

    def set_datetime(self, dt: datetime):
        self._dt = dt


def test_ny_window_timezone_and_dst(isolated_db):
    """Verify 08:00 - 11:00 NY dynamically maps to VN time under EDT and EST."""
    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    policy.ny_entry_start = "08:00"
    policy.ny_entry_end = "11:00"
    policy.ny_fallback_start = "10:30"
    isolated_db.commit()

    # Case 1: EDT (Summer/Fall DST, UTC-4) -> 08:00 NY is 12:00 UTC, which is 19:00 VN
    edt_dt = datetime(2026, 10, 9, 8, 30, tzinfo=ZoneInfo("America/New_York"))
    clock_edt = MockClock(edt_dt)
    w_edt = TradingPolicyService.get_ny_session_window(policy, clock=clock_edt)
    assert w_edt["is_in_window"] is True
    assert "19:00" in w_edt["vn_window_str"]
    assert "22:00" in w_edt["vn_window_str"]

    # Case 2: EST (Winter standard time, UTC-5) -> 08:00 NY is 13:00 UTC, which is 20:00 VN
    est_dt = datetime(2026, 12, 15, 8, 30, tzinfo=ZoneInfo("America/New_York"))
    clock_est = MockClock(est_dt)
    w_est = TradingPolicyService.get_ny_session_window(policy, clock=clock_est)
    assert w_est["is_in_window"] is True
    assert "20:00" in w_est["vn_window_str"]
    assert "23:00" in w_est["vn_window_str"]


def test_entry_blocked_outside_ny_window(isolated_db):
    """Under NY_ONLY policy, entry is allowed inside 08:00-11:00 NY and blocked outside."""
    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    policy.entry_session_policy = "NY_ONLY"
    policy.ny_entry_start = "08:00"
    policy.ny_entry_end = "11:00"
    isolated_db.commit()

    # Time outside NY window (03:00 NY)
    outside_dt = datetime(2026, 10, 9, 3, 0, tzinfo=ZoneInfo("America/New_York"))
    clock_outside = MockClock(outside_dt)
    res_outside = TradingPolicyService.evaluate_entry_policy(
        isolated_db, "XAUUSDT", strategy_family="STANDARD_SMC", clock=clock_outside
    )
    assert res_outside["can_enter"] is False
    assert "OUTSIDE_ENTRY_WINDOW" in res_outside["reason_codes"]

    # Time inside NY window (09:15 NY)
    inside_dt = datetime(2026, 10, 9, 9, 15, tzinfo=ZoneInfo("America/New_York"))
    clock_inside = MockClock(inside_dt)
    res_inside = TradingPolicyService.evaluate_entry_policy(
        isolated_db, "XAUUSDT", strategy_family="STANDARD_SMC", clock=clock_inside
    )
    assert res_inside["can_enter"] is True
    assert res_inside["is_in_ny_window"] is True


def make_and_record_order(db, clock, strategy_family="STANDARD_SMC"):
    now_ms = clock.now_ms()
    day_audit = crud.get_or_create_today_audit(db, clock=clock)
    order = models.PaperOrder(
        id=f"order-{now_ms}-{day_audit.fills_count}",
        strategy_family=strategy_family,
        instrument="XAUUSDT",
        direction="LONG",
        planned_entry=2650.0,
        actual_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2665.0,
        quantity=0.05,
        risk_pct=0.10,
        initial_risk_usdt=2.5,
        state="paper_open",
        created_at=now_ms,
        opened_at=now_ms
    )
    db.add(order)

    day_audit.fills_count += 1
    db.commit()
    TradingPolicyService.record_quota_fill(db, order, clock=clock, commit=True)
    return order



class DummyCandle:
    def __init__(self, open_, high, low, close, volume=100.0, is_closed=True, timestamp=None):
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.is_closed = is_closed
        self.timestamp = timestamp or 100000


def test_ny_quota_fulfillment_on_standard_fill(isolated_db):
    """When a standard SMC order fills during the NY window, NY quota is fulfilled (1/1)."""
    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    policy.entry_session_policy = "NY_ONLY"
    isolated_db.commit()

    ny_dt = datetime(2026, 10, 9, 9, 0, tzinfo=ZoneInfo("America/New_York"))
    clock = MockClock(ny_dt)

    quota = TradingPolicyService.get_or_create_session_quota(isolated_db, policy, clock=clock)
    assert quota.total_fills == 0
    assert quota.quota_status == "SEEKING_STANDARD"

    # Simulate genuine PAPER fill via lifecycle helper
    make_and_record_order(isolated_db, clock, strategy_family="STANDARD_SMC")

    isolated_db.refresh(quota)
    assert quota.total_fills == 1
    assert quota.standard_fills == 1
    assert quota.quota_status == "FULFILLED"

    eval_after = TradingPolicyService.evaluate_entry_policy(
        isolated_db, "XAUUSDT", strategy_family="STANDARD_SMC", clock=clock
    )
    assert eval_after["ny_fills"] == 1
    assert eval_after["quota_state"] == "FULFILLED"


def test_max_daily_fills_ceiling(isolated_db):
    """Strict daily fill cap: 3 fills allowed, 4th fill attempt is blocked."""
    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    policy.max_daily_fills = 3
    isolated_db.commit()

    ny_dt = datetime(2026, 10, 9, 9, 0, tzinfo=ZoneInfo("America/New_York"))
    clock = MockClock(ny_dt)

    # Record 3 fills
    for i in range(3):
        res = TradingPolicyService.evaluate_entry_policy(
            isolated_db, "XAUUSDT", strategy_family="STANDARD_SMC", clock=clock
        )
        assert res["can_enter"] is True, f"Fill {i+1} should be allowed"
        make_and_record_order(isolated_db, clock, strategy_family="STANDARD_SMC")

    # 4th fill attempt must be blocked
    res_4th = TradingPolicyService.evaluate_entry_policy(
        isolated_db, "XAUUSDT", strategy_family="STANDARD_SMC", clock=clock
    )
    assert res_4th["can_enter"] is False
    assert "MAX_DAILY_ENTRIES" in res_4th["reason_codes"]
    assert res_4th["remaining_daily_slots"] == 0


def test_slot_reservation_in_all_sessions(isolated_db):
    """ALL_SESSIONS_WITH_NY_RESERVE reserves 1 slot for NY, blocking 3rd fill before NY."""
    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    policy.entry_session_policy = "ALL_SESSIONS_WITH_NY_RESERVE"
    policy.max_daily_fills = 3
    policy.reserve_ny_slot = True
    isolated_db.commit()

    # Pre-NY session time (04:00 NY / Asia session)
    asia_dt = datetime(2026, 10, 9, 4, 0, tzinfo=ZoneInfo("America/New_York"))
    clock_asia = MockClock(asia_dt)

    # 1st fill in Asia
    res1 = TradingPolicyService.evaluate_entry_policy(
        isolated_db, "XAUUSDT", strategy_family="STANDARD_SMC", clock=clock_asia
    )
    assert res1["can_enter"] is True
    make_and_record_order(isolated_db, clock_asia, strategy_family="STANDARD_SMC")

    # 2nd fill in Asia
    res2 = TradingPolicyService.evaluate_entry_policy(
        isolated_db, "XAUUSDT", strategy_family="STANDARD_SMC", clock=clock_asia
    )
    assert res2["can_enter"] is True
    make_and_record_order(isolated_db, clock_asia, strategy_family="STANDARD_SMC")

    # 3rd fill attempted in Asia BEFORE NY has a fill: BLOCKED by slot reservation!
    res3 = TradingPolicyService.evaluate_entry_policy(
        isolated_db, "XAUUSDT", strategy_family="STANDARD_SMC", clock=clock_asia
    )
    assert res3["can_enter"] is False
    assert "NY_SLOT_RESERVED" in res3["reason_codes"]

    # Inside NY window, the 3rd reserved slot is ALLOWED!
    ny_dt = datetime(2026, 10, 9, 9, 0, tzinfo=ZoneInfo("America/New_York"))
    clock_ny = MockClock(ny_dt)
    res_ny = TradingPolicyService.evaluate_entry_policy(
        isolated_db, "XAUUSDT", strategy_family="STANDARD_SMC", clock=clock_ny
    )
    assert res_ny["can_enter"] is True


def test_fallback_activation_at_10_30_ny(isolated_db):
    """At 10:30 NY if quota is 0, fallback is activated and risk is capped at <= 0.10%."""
    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    policy.ny_fallback_enabled = True
    policy.ny_fallback_start = "10:30"
    policy.ny_fallback_risk_pct_cap = 0.10
    isolated_db.commit()

    # 10:15 NY (before fallback start): fallback inactive
    dt_1015 = datetime(2026, 10, 9, 10, 15, tzinfo=ZoneInfo("America/New_York"))
    w_1015 = TradingPolicyService.get_ny_session_window(policy, clock=MockClock(dt_1015))
    assert w_1015["is_fallback_active"] is False

    # 10:35 NY (after fallback start): fallback active
    dt_1035 = datetime(2026, 10, 9, 10, 35, tzinfo=ZoneInfo("America/New_York"))
    clock_1035 = MockClock(dt_1035)
    w_1035 = TradingPolicyService.get_ny_session_window(policy, clock=clock_1035)
    assert w_1035["is_fallback_active"] is True

    quota = TradingPolicyService.update_quota_lifecycle_state(isolated_db, policy, clock=clock_1035)
    assert quota.quota_status == "SEEKING_FALLBACK"

    # Build 20 candles with a Bullish FVG
    candles = [
        DummyCandle(2640.0 + i * 0.5, 2642.0 + i * 0.5, 2639.0 + i * 0.5, 2641.0 + i * 0.5, timestamp=1000 + i * 300000)
        for i in range(16)
    ]
    # Candle 16: high 2650
    candles.append(DummyCandle(2648.0, 2650.0, 2647.0, 2649.0, timestamp=1000 + 16 * 300000))
    # Candle 17: big bullish displacement body up to 2658, low 2651
    candles.append(DummyCandle(2650.0, 2658.0, 2651.0, 2657.0, timestamp=1000 + 17 * 300000))
    # Candle 18: low 2654, high 2660 -> FVG gap between candle 16 high (2650) and candle 18 low (2654)
    candles.append(DummyCandle(2657.0, 2660.0, 2654.0, 2659.0, timestamp=1000 + 18 * 300000))
    # Candle 19: price retesting into 2652 (inside 2650-2654)
    candles.append(DummyCandle(2659.0, 2659.0, 2652.0, 2653.0, timestamp=1000 + 19 * 300000))

    fallback_eval = NYFallbackService.evaluate_fallback_setup(
        symbol="XAUUSDT",
        candles_5m=candles,
        current_price=2653.0,
        htf_bias="BULLISH",
        h1_alignment="ALIGNED",
        policy=policy,
        account_equity=1000.0,
        remaining_risk_allowance_usdt=15.0
    )
    assert fallback_eval["strategy_family"] == "NY_QUOTA_PAPER"
    if fallback_eval["is_eligible"]:
        cand = fallback_eval["candidate"]
        assert cand["risk_pct"] <= 0.10
        assert cand["strategy_family"] == "NY_QUOTA_PAPER"


def test_deadline_missed_quota(isolated_db):
    """After 11:00 NY with 0 fills, quota is marked MISSED with transparent explanation."""
    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    policy.entry_session_policy = "NY_ONLY"
    policy.ny_entry_start = "08:00"
    policy.ny_entry_end = "11:00"
    isolated_db.commit()


    # 11:05 NY (after window closed)
    after_dt = datetime(2026, 10, 9, 11, 5, tzinfo=ZoneInfo("America/New_York"))
    clock_after = MockClock(after_dt)

    quota = TradingPolicyService.update_quota_lifecycle_state(isolated_db, policy, clock=clock_after)
    assert quota.quota_status == "MISSED"
    assert "Hết cửa sổ" in quota.last_status_reason

    # New entries outside window are blocked
    res = TradingPolicyService.evaluate_entry_policy(
        isolated_db, "XAUUSDT", strategy_family="STANDARD_SMC", clock=clock_after
    )
    assert res["can_enter"] is False
    assert "OUTSIDE_ENTRY_WINDOW" in res["reason_codes"]

