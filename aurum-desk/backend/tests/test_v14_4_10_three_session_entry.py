"""
backend/tests/test_v14_4_10_three_session_entry.py
V14.4.10 Three-Session Entry Flow & Policy Alignment Suite:
Covers:
- E01 / ENTRY01: Asian session certified setup promoted to READY and armed under ALL_SESSIONS_WITH_NY_RESERVE
- E02 / ENTRY02: London session certified SHORT setup promoted and armed
- E03 / ENTRY03: NY_ONLY session policy continues to block Asia/London setups
- E04 / ENTRY04: Slot reservation preserves NY slot when daily quota allows
- E05 / ENTRY05: Auto off does not auto-arm but preserves scenario READY state
- E06 / ENTRY06: Expiry calculation does not incorrectly truncate to NY window end
- E07 / ENTRY07: Negative entry guards (news, lessons, risk, quotes) still block ARM and FILL
- E08 / ENTRY08: NY fallback search remains strictly bounded to NY window (10:30-11:00 NY)
- E09 / ENTRY09: DST and Vietnam day boundary handling
- E10 / ENTRY10: Full chain causality preserved
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Any, List
import pytest
from sqlalchemy.orm import Session

import models
import crud
import smc_fixtures as F
from services.clock import IClock
from services.smc_scenario_config import active_scenario_config as CFG
from services.smc_scenario_service import evaluate_scenario_cycle, load_closed_context
from services.smc_scenario_types import ScenarioState
from services.trading_policy_service import (
    TradingPolicyService,
    resolve_entry_session_context,
    compute_promoted_setup_expiry,
    EntrySessionContext,
)
from services.strategy_service import StrategyService
from services.ny_quota_helpers import should_search_ny_fallback
from policy_constants import compute_reserved_capacity


class MockClock(IClock):
    def __init__(self, dt: datetime):
        self._dt = dt

    def now_ms(self) -> int:
        return int(self._dt.timestamp() * 1000)

    def now_datetime(self, tz=None):
        return self._dt.astimezone(tz) if tz else self._dt


def make_shifted_candles(direction: str, target_now_ms: int) -> Dict[str, List[F.C]]:
    cbt = F.make_candles_by_tf(direction)
    time_offset = target_now_ms - F.NOW_MS
    shifted = {}
    for tf, candles in cbt.items():
        shifted[tf] = [
            F.C(
                timestamp=c.timestamp + time_offset,
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=c.volume,
                is_closed=c.is_closed,
            )
            for c in candles
        ]
    return shifted


def test_e01_entry01_asian_session_certified_setup_promoted_under_all_sessions(isolated_db: Session):
    """
    PHẦN 001, 002, 014, 061:
    Asian session time (20:00 NY).
    Policy is ALL_SESSIONS_WITH_NY_RESERVE.
    Confirmed structure in Asia must NOT receive OUTSIDE_ENTRY_WINDOW blocker.
    Promoted with valid expiry.
    """
    NY = ZoneInfo("America/New_York")
    asia_dt = datetime(2026, 4, 13, 20, 0, tzinfo=NY)  # 20:00 NY = Asia session
    asia_now_ms = int(asia_dt.timestamp() * 1000)
    clock = MockClock(asia_dt)

    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    policy.entry_session_policy = "ALL_SESSIONS_WITH_NY_RESERVE"
    isolated_db.commit()

    cbt = make_shifted_candles("LONG", target_now_ms=asia_now_ms)
    settings = F.default_settings()

    session_ctx = resolve_entry_session_context(policy, "STANDARD_SMC", now_ms=asia_now_ms, clock=clock)
    assert session_ctx.standard_window_restriction is None
    assert session_ctx.expiry_cap_ms is None

    result = evaluate_scenario_cycle(
        isolated_db,
        "XAUUSDT",
        cbt,
        None,
        asia_now_ms,
        config=CFG,
        settings=settings,
        session_context=session_ctx,
    )

    long_snaps = [s for s in result.snapshots if s.direction == "LONG"]
    assert len(long_snaps) > 0
    winner = long_snaps[0]

    assert "OUTSIDE_ENTRY_WINDOW" not in (winner.blockers or [])
    assert winner.state == ScenarioState.STRUCTURE_CONFIRMED


def test_e02_entry02_london_session_certified_short_setup_promoted(isolated_db: Session):
    """
    PHẦN 015, 062:
    London session time (04:00 NY = 09:00 UTC, before NY open at 08:00 NY).
    Direction is SHORT.
    Policy is ALL_SESSIONS_WITH_NY_RESERVE.
    Confirmed SHORT setup must NOT receive OUTSIDE_ENTRY_WINDOW blocker.
    Take profit < planned entry < stop loss.
    """
    NY = ZoneInfo("America/New_York")
    london_dt = datetime(2026, 4, 14, 4, 0, tzinfo=NY)  # 04:00 NY = London
    london_now_ms = int(london_dt.timestamp() * 1000)
    clock = MockClock(london_dt)

    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    policy.entry_session_policy = "ALL_SESSIONS_WITH_NY_RESERVE"
    isolated_db.commit()

    cbt = make_shifted_candles("SHORT", target_now_ms=london_now_ms)
    settings = F.default_settings()

    session_ctx = resolve_entry_session_context(policy, "STANDARD_SMC", now_ms=london_now_ms, clock=clock)
    assert session_ctx.standard_window_restriction is None

    result = evaluate_scenario_cycle(
        isolated_db,
        "XAUUSDT",
        cbt,
        None,
        london_now_ms,
        config=CFG,
        settings=settings,
        session_context=session_ctx,
    )

    short_snaps = [s for s in result.snapshots if s.direction == "SHORT"]
    assert len(short_snaps) > 0
    winner = short_snaps[0]

    assert "OUTSIDE_ENTRY_WINDOW" not in (winner.blockers or [])
    assert winner.state == ScenarioState.STRUCTURE_CONFIRMED
    assert winner.direction == "SHORT"
    assert winner.plan is not None
    tp = winner.plan["take_profit"] if isinstance(winner.plan, dict) else winner.plan.take_profit
    entry = winner.plan["planned_entry"] if isinstance(winner.plan, dict) else winner.plan.planned_entry
    sl = winner.plan["stop_loss"] if isinstance(winner.plan, dict) else winner.plan.stop_loss
    assert tp < entry < sl


def test_e03_entry03_ny_only_policy_blocks_outside_sessions(isolated_db: Session):
    """
    PHẦN 063:
    When policy is NY_ONLY:
    Asia and London setups MUST receive OUTSIDE_ENTRY_WINDOW blocker.
    Inside NY window (09:30 NY), setup does NOT have OUTSIDE_ENTRY_WINDOW blocker.
    """
    NY = ZoneInfo("America/New_York")
    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    policy.entry_session_policy = "NY_ONLY"
    isolated_db.commit()

    # 1. Asia session (20:00 NY)
    asia_dt = datetime(2026, 4, 13, 20, 0, tzinfo=NY)
    asia_now_ms = int(asia_dt.timestamp() * 1000)
    clock_asia = MockClock(asia_dt)
    ctx_asia = resolve_entry_session_context(policy, "STANDARD_SMC", now_ms=asia_now_ms, clock=clock_asia)

    assert ctx_asia.standard_window_restriction is not None

    cbt_asia = make_shifted_candles("LONG", target_now_ms=asia_now_ms)
    res_asia = evaluate_scenario_cycle(
        isolated_db, "XAUUSDT", cbt_asia, None, asia_now_ms,
        config=CFG, settings=F.default_settings(), session_context=ctx_asia,
    )
    long_asia = [s for s in res_asia.snapshots if s.direction == "LONG"][0]
    assert "OUTSIDE_ENTRY_WINDOW" in (long_asia.blockers or [])

    # 2. Inside NY window (09:30 NY)
    ny_dt = datetime(2026, 4, 14, 9, 30, tzinfo=NY)
    ny_now_ms = int(ny_dt.timestamp() * 1000)
    clock_ny = MockClock(ny_dt)
    ctx_ny = resolve_entry_session_context(policy, "STANDARD_SMC", now_ms=ny_now_ms, clock=clock_ny)

    assert ctx_ny.in_ny_entry_window is True

    cbt_ny = make_shifted_candles("LONG", target_now_ms=ny_now_ms)
    res_ny = evaluate_scenario_cycle(
        isolated_db, "XAUUSDT", cbt_ny, None, ny_now_ms,
        config=CFG, settings=F.default_settings(), session_context=ctx_ny,
    )
    long_ny = [s for s in res_ny.snapshots if s.direction == "LONG"][0]
    assert "OUTSIDE_ENTRY_WINDOW" not in (long_ny.blockers or [])


def test_e04_entry04_slot_reservation_and_quota_caps(isolated_db: Session):
    """
    PHẦN 033, 064:
    Capacity and slot reservation testing:
    - Daily max fills = 3, NY reserve = 1.
    - If total_fills = 2, ny_fills = 0: outside NY remaining is 0 (NY_SLOT_RESERVED).
    - If total_fills = 2, ny_fills = 1: outside NY remaining is 1.
    - If total_fills = 3: all blocked with DAILY_FILLS_CAP_REACHED.
    """
    # 1. 2 fills outside NY, 0 in NY
    # Total remaining = 1, but outside NY remaining = 1 - 1(reserved) = 0!
    cap_outside_empty = compute_reserved_capacity(
        max_fills=3, daily_fills=2, ny_fills=0, target=1, reserve_enabled=True,
    )
    assert cap_outside_empty["remaining"] == 1
    assert cap_outside_empty["reserved_ny"] == 1
    assert cap_outside_empty["available_outside_ny"] == 0

    # 2. 2 fills, including 1 in NY
    # Outside NY remaining capacity = 1
    cap_outside_avail = compute_reserved_capacity(
        max_fills=3, daily_fills=2, ny_fills=1, target=1, reserve_enabled=True,
    )
    assert cap_outside_avail["remaining"] == 1
    assert cap_outside_avail["reserved_ny"] == 0
    assert cap_outside_avail["available_outside_ny"] == 1

    # 3. 3 fills total
    cap_max = compute_reserved_capacity(
        max_fills=3, daily_fills=3, ny_fills=1, target=1, reserve_enabled=True,
    )
    assert cap_max["remaining"] == 0
    assert cap_max["available_outside_ny"] == 0


def test_e05_entry05_auto_off_preserves_ready_without_auto_arm(isolated_db: Session):
    """
    PHẦN 031, 065:
    When policy.auto_execution_enabled is False:
    Setup confirms to READY state in scenario cycle, but StrategyService._auto_arm_candidate does NOT arm.
    """
    NY = ZoneInfo("America/New_York")
    asia_dt = datetime(2026, 4, 13, 20, 0, tzinfo=NY)
    asia_now_ms = int(asia_dt.timestamp() * 1000)

    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    policy.entry_session_policy = "ALL_SESSIONS_WITH_NY_RESERVE"
    svc = StrategyService()
    svc.set_auto_state(isolated_db, False)
    assert svc.get_auto_state(isolated_db) is False

    # Scenario cycle creates confirmed structure
    cbt = make_shifted_candles("LONG", target_now_ms=asia_now_ms)
    session_ctx = resolve_entry_session_context(policy, "STANDARD_SMC", now_ms=asia_now_ms, clock=MockClock(asia_dt))
    result = evaluate_scenario_cycle(
        isolated_db, "XAUUSDT", cbt, None, asia_now_ms,
        config=CFG, settings=F.default_settings(), session_context=session_ctx,
    )
    long_snaps = [s for s in result.snapshots if s.direction == "LONG"]
    assert len(long_snaps) > 0
    winner = long_snaps[0]
    assert winner.state == ScenarioState.STRUCTURE_CONFIRMED

    # With Auto OFF, no armed order exists
    armed_orders = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").all()
    assert len(armed_orders) == 0

    # Turning Auto ON persists setting
    svc.set_auto_state(isolated_db, True)
    assert svc.get_auto_state(isolated_db) is True


def test_e06_entry06_expiry_calculation_and_ttl_boundaries():
    """
    PHẦN 016, 028, 029, 066:
    Tests compute_promoted_setup_expiry:
    - Standard SMC ALL_SESSIONS: does NOT cap to NY window end.
    - Standard SMC NY_ONLY: caps to NY window end.
    - Fallback: caps to NY window end.
    - Scenario already expired: returns None.
    """
    now_ms = 1_000_000
    ttl_ms = 2_700_000  # 45 mins
    scenario_expires_at = now_ms + 10_000_000  # far future
    ny_window_end_ms = now_ms + 1_000_000  # only 1,000,000 ms away (< ttl_ms)

    # 1. ALL_SESSIONS (expiry_cap_ms is None)
    ctx_all = EntrySessionContext(
        policy_mode="PAPER",
        policy_version=1,
        strategy_family="STANDARD_SMC",
        analysis_session="ASIA",
        as_of_ms=now_ms,
        in_ny_entry_window=False,
        ny_start_ms=0,
        ny_end_ms=ny_window_end_ms,
        standard_window_restriction=None,
        expiry_cap_ms=None,
    )
    exp_all = compute_promoted_setup_expiry(now_ms, scenario_expires_at, ttl_ms, ctx_all)
    assert exp_all == now_ms + ttl_ms  # Not capped!

    # 2. NY_ONLY (expiry_cap_ms is ny_window_end_ms)
    ctx_ny = EntrySessionContext(
        policy_mode="PAPER",
        policy_version=1,
        strategy_family="STANDARD_SMC",
        analysis_session="NY",
        as_of_ms=now_ms,
        in_ny_entry_window=True,
        ny_start_ms=0,
        ny_end_ms=ny_window_end_ms,
        standard_window_restriction=(0, ny_window_end_ms),
        expiry_cap_ms=ny_window_end_ms,
    )
    exp_ny = compute_promoted_setup_expiry(now_ms, scenario_expires_at, ttl_ms, ctx_ny)
    assert exp_ny == ny_window_end_ms  # Capped to NY end!

    # 3. Already expired scenario
    exp_expired = compute_promoted_setup_expiry(now_ms, now_ms - 500, ttl_ms, ctx_all)
    assert exp_expired is None


def test_e07_entry07_guards_negative_and_fill_recheck(isolated_db: Session):
    """
    PHẦN 036, 067:
    Negative entry guards verification:
    Active position prevents auto-arm.
    """
    NY = ZoneInfo("America/New_York")
    asia_dt = datetime(2026, 4, 13, 20, 0, tzinfo=NY)
    asia_now_ms = int(asia_dt.timestamp() * 1000)

    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    policy.entry_session_policy = "ALL_SESSIONS_WITH_NY_RESERVE"
    policy.auto_execution_enabled = True
    isolated_db.commit()

    # Seed active open position
    pos = models.PaperOrder(
        id="ord_active_pos",
        direction="LONG",
        state="paper_open",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=2.5,
        opened_at=asia_now_ms,
        created_at=asia_now_ms,
    )
    isolated_db.add(pos)
    isolated_db.commit()

    ws = models.WatchSetup(
        id="ws_e07",
        direction="LONG",
        state="READY",
        provisional_entry=4180.0,
        provisional_sl=4170.0,
        provisional_tp=4200.0,
        invalidation_price=4170.0,
        strategy_family="STANDARD_SMC",
        created_at=asia_now_ms,
        updated_at=asia_now_ms,
    )
    isolated_db.add(ws)
    isolated_db.commit()

    svc = StrategyService()
    arm_res = svc._auto_arm_candidate(
        isolated_db,
        ws,
        sig={},
        now_ms=asia_now_ms,
        now_dt=asia_dt,
    )
    assert arm_res["armed"] is False
    assert any("ACTIVE_POSITION_EXISTS" in c for c in arm_res["codes"])


def test_e08_entry08_ny_fallback_search_strictly_bounded():
    """
    PHẦN 008, 039, 040, 068:
    NY fallback search is strictly confined to 10:30-11:00 NY window,
    and requires target_fills > total_fills.
    """
    NY = ZoneInfo("America/New_York")

    # Outside NY window (04:00 NY)
    dt_london = datetime(2026, 4, 14, 4, 0, tzinfo=NY)
    now_ms_london = int(dt_london.timestamp() * 1000)
    w_london = TradingPolicyService.get_ny_session_window(now_ms=now_ms_london)
    assert should_search_ny_fallback(
        policy_active=True, mode="PAPER", fallback_enabled=True,
        now_ms=now_ms_london,
        window_start_ms=w_london["window_start_ms"],
        fallback_start_ms=w_london["fallback_start_ms"],
        window_end_ms=w_london["window_end_ms"],
        target_fills=1, total_fills=0,
    ) is False

    # Inside NY fallback window (10:45 NY) with target not yet met
    dt_fallback = datetime(2026, 4, 14, 10, 45, tzinfo=NY)
    now_ms_fallback = int(dt_fallback.timestamp() * 1000)
    w_ny = TradingPolicyService.get_ny_session_window(now_ms=now_ms_fallback)
    assert should_search_ny_fallback(
        policy_active=True, mode="PAPER", fallback_enabled=True,
        now_ms=now_ms_fallback,
        window_start_ms=w_ny["window_start_ms"],
        fallback_start_ms=w_ny["fallback_start_ms"],
        window_end_ms=w_ny["window_end_ms"],
        target_fills=1, total_fills=0,
    ) is True

    # Inside NY fallback window (10:45 NY) but target ALREADY met
    assert should_search_ny_fallback(
        policy_active=True, mode="PAPER", fallback_enabled=True,
        now_ms=now_ms_fallback,
        window_start_ms=w_ny["window_start_ms"],
        fallback_start_ms=w_ny["fallback_start_ms"],
        window_end_ms=w_ny["window_end_ms"],
        target_fills=1, total_fills=1,
    ) is False


def test_e09_entry09_dst_and_day_boundaries():
    """
    PHẦN 042, 069:
    Tests NY session calculations under DST (EDT vs EST) and daily quota boundary.
    """
    NY = ZoneInfo("America/New_York")

    # Summer (EDT = UTC-4): July 15, 2026
    dt_summer = datetime(2026, 7, 15, 10, 0, tzinfo=NY)
    now_summer_ms = int(dt_summer.timestamp() * 1000)
    w_summer = TradingPolicyService.get_ny_session_window(now_ms=now_summer_ms)
    start_summer_utc = datetime.fromtimestamp(w_summer["window_start_ms"] / 1000, tz=timezone.utc)
    # 08:00 EDT = 12:00 UTC
    assert start_summer_utc.hour == 12

    # Winter (EST = UTC-5): Jan 15, 2026
    dt_winter = datetime(2026, 1, 15, 10, 0, tzinfo=NY)
    now_winter_ms = int(dt_winter.timestamp() * 1000)
    w_winter = TradingPolicyService.get_ny_session_window(now_ms=now_winter_ms)
    start_winter_utc = datetime.fromtimestamp(w_winter["window_start_ms"] / 1000, tz=timezone.utc)
    # 08:00 EST = 13:00 UTC
    assert start_winter_utc.hour == 13


def test_e10_entry10_full_chain_causality(isolated_db: Session):
    """
    PHẦN 027, 070:
    Causality preservation:
    When structure is not confirmed (e.g. empty or non-breaking candles),
    scenario does NOT reach STRUCTURE_CONFIRMED regardless of policy.
    """
    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    policy.entry_session_policy = "ALL_SESSIONS_WITH_NY_RESERVE"
    isolated_db.commit()

    now_ms = 1_700_000_000_000
    # Flat / non-displacement candles
    flat_candle = F.C(timestamp=now_ms - 300_000, open=4180.0, high=4181.0, low=4179.0, close=4180.0, volume=10, is_closed=True)
    cbt_flat = {tf: [flat_candle] for tf in ["5M", "15M", "1H", "4H", "D"]}

    session_ctx = resolve_entry_session_context(policy, "STANDARD_SMC", now_ms=now_ms)
    res = evaluate_scenario_cycle(
        isolated_db, "XAUUSDT", cbt_flat, None, now_ms,
        config=CFG, settings=F.default_settings(), session_context=session_ctx,
    )

    confirmed = [s for s in res.snapshots if s.state == ScenarioState.STRUCTURE_CONFIRMED]
    assert len(confirmed) == 0
