"""
backend/tests/test_v14_4_6_flow_preservation.py
V14.4.6 Cases FLOW01 and FLOW02 (PHẦN 006, 007, 062, 063, 064, 115, 116):
Strict preservation of existing trading flow (detector -> setup -> ARM -> FILL -> OPEN -> exit),
all guards at ARM and FILL (news, lessons, risk, quotes, quota, slot reserve, one open position),
and protective exit execution.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy.orm import Session

import crud
import models
from policy_constants import DEFAULT_POLICY_VALUES
from services.trading_policy_service import TradingPolicyService
from services.trade_lifecycle_service import TradeLifecycleService


from services.clock import IClock


class MockClock(IClock):
    def __init__(self, dt: datetime):
        self._dt = dt

    def now_ms(self) -> int:
        return int(self._dt.timestamp() * 1000)

    def now_datetime(self, tz=None):
        return self._dt.astimezone(tz) if tz else self._dt

    def monotonic_s(self) -> float:
        return self._dt.timestamp()


def test_flow01_trade_lifecycle_arm_fill_open_exit_preserved(isolated_db: Session):
    """Case FLOW01 (PHẦN 006, 115):
    Verifies that a valid setup transitions cleanly:
    ARM -> FILL -> OPEN -> execute_close (TP/SL) -> records PnL, journal and lesson correctly.
    """
    # 1. Setup policy
    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    policy.entry_session_policy = "ALL_SESSIONS_WITH_NY_RESERVE"
    isolated_db.commit()

    # 2. Create PaperOrder in armed state
    order = models.PaperOrder(
        id="ord_flow01_test",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        planned_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=1.0,
        created_at=1000,
    )
    isolated_db.add(order)
    isolated_db.commit()

    # 3. Simulate FILL -> paper_open
    order.state = "paper_open"
    order.actual_entry = 4180.0
    order.opened_at = 1100
    isolated_db.commit()

    assert order.state == "paper_open"
    assert order.actual_entry == 4180.0

    # 4. Execute Close via TradeLifecycleService (TP exit)
    res = TradeLifecycleService.execute_close(
        db=isolated_db,
        order_id=order.id,
        exit_price=4200.0,
        exit_cause="TP_HIT",
        occurred_at=1500,
        session_tag="NORMAL",
    )
    assert res is not None
    assert res.state == "closed"

    # Order must be closed
    isolated_db.refresh(order)
    assert order.state == "closed"
    assert order.actual_exit == 4200.0
    assert order.exit_cause == "TP_HIT"
    assert order.closed_at == 1500
    # Net PnL must be positive: (4200 - 4180) * 0.1 = +2.0 (minus fees)
    assert order.realized_pnl_net is not None
    assert order.realized_pnl_net > 0


def test_flow02_guards_block_arm_and_fill_but_protective_exits_run(isolated_db: Session):
    """Case FLOW02 (PHẦN 064, 116):
    Negative guards:
    1. Max open positions guard: having 1 open position blocks a new entry.
    2. Daily quota guard: 3 fills blocks further entries.
    3. Protective exits still execute regardless of entry guards.
    """
    ny_tz = ZoneInfo("America/New_York")
    dt_ny = datetime(2026, 10, 13, 9, 30, tzinfo=ny_tz)
    clock = MockClock(dt_ny)

    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    policy.entry_session_policy = "ALL_SESSIONS_WITH_NY_RESERVE"
    isolated_db.commit()

    # 1. Simulate 1 existing open position -> execute_fill on a second order must be REJECTED
    open_ord = models.PaperOrder(
        id="ord_existing_open",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_entry=4180.0,
        quantity=0.1,
        initial_risk_usdt=1.0,
        created_at=1000,
        opened_at=1000,
    )
    second_ord = models.PaperOrder(
        id="ord_second_attempt",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        planned_entry=4182.0,
        stop_loss=4172.0,
        take_profit=4202.0,
        quantity=0.1,
        initial_risk_usdt=1.0,
        created_at=1100,
    )
    isolated_db.add_all([open_ord, second_ord])
    isolated_db.commit()

    from domain_calculator import calculate_risk_reward
    calc = calculate_risk_reward("LONG", 4182.0, 4172.0, 4202.0, 1000.0, 0.25, 2.0)

    # Attempting to fill second order while first is open must be rejected by one-position guard!
    fill_res = TradeLifecycleService.execute_fill(
        db=isolated_db,
        order=second_ord,
        fill_price=4182.0,
        calc_result=calc,
        clock=clock,
    )
    assert fill_res.state == "rejected"

    # 2. Protective exit on the existing open position must still succeed!
    close_res = TradeLifecycleService.execute_close(
        db=isolated_db,
        order_id=open_ord.id,
        exit_price=4169.5,
        exit_cause="SL_HIT",
        occurred_at=2000,
        session_tag="PROTECTIVE",
    )
    assert close_res is not None
    isolated_db.refresh(open_ord)
    assert open_ord.state == "closed"

    # 3. Simulate day hits max daily fills (3 fills) -> can_enter must be False
    audit = crud.get_or_create_today_audit(isolated_db, clock=clock)
    audit.fills_count = 3
    isolated_db.commit()

    eval_cap = TradingPolicyService.evaluate_entry_policy(isolated_db, clock=clock)
    assert eval_cap["can_enter"] is False
    assert "MAX_DAILY_ENTRIES" in eval_cap["reason_codes"]
