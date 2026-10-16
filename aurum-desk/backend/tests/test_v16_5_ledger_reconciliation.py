"""
backend/tests/test_v16_5_ledger_reconciliation.py
V16.5 Gate D: Authoritative Ledger & Replay Outcome Accounting (Rules 121 - 135, 161 - 166, Appendix D L1):
- test_rule_122_123_fills_identity_and_status_breakdown: total_fills = closed + open; pending/expired/rejected excluded
- test_rule_124_125_net_pnl_reconciliation_with_fees: independent hand calculation matches lifecycle realized net PnL without double slippage
- test_rule_127_128_project_replay_equity_and_drawdown: equity timeline and peak drawdown correctly calculated from trade series
- test_rule_130_161_open_end_position_reporting: range ending with open position reports fills=1, closed=0, open=1, winrate=None
- test_rule_131_165_net_winrate_requires_positive_net_pnl: trade with gross TP but net negative after fees is classified as a loss
- test_rule_132_166_date_boundary_attribution: fill before midnight and close after midnight attribute properly across DayAudit
"""
import json
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import crud
import models
from models import Base
from domain_calculator import CostAssumptions, calculate_risk_reward
from lab.cg01_replay_execution_adapter import CG01ReplayExecutionAdapter
from services.cg01_types import CG01Config, CG01ProfileMode
from services.clock import ReplayClock
from services.trade_lifecycle_service import TradeLifecycleService
from services.trading_policy_service import TradingPolicyService


@pytest.fixture
def ledger_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    policy = TradingPolicyService.get_or_create_policy(session)
    policy.max_daily_fills = 3
    policy.daily_min_goal = 2
    policy.is_active = False
    session.commit()

    try:
        yield session, engine
    finally:
        session.close()


def test_rule_122_123_fills_identity_and_status_breakdown(ledger_db):
    """
    Rules 122 & 123:
    Fills count must strictly equal closed_trades + open_positions in scope.
    Armed, expired, rejected, and candidate orders must NEVER count as executed fills.
    """
    db, _ = ledger_db
    now_ms = 1792050000000
    clock = ReplayClock(initial_ms=now_ms)
    adapter = CG01ReplayExecutionAdapter(db=db, clock=clock)

    # 1. Closed order
    o_closed = models.PaperOrder(
        id="ord_closed_1",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        order_type="LIMIT",
        planned_entry=2620.0,
        actual_entry=2620.0,
        actual_exit=2635.0,
        stop_loss=2610.0,
        take_profit=2635.0,
        quantity=0.5,
        initial_risk_usdt=5.0,
        realized_pnl_net=7.50,
        exit_cause="TP_HIT",
        created_at=now_ms - 60_000,
        opened_at=now_ms - 50_000,
        closed_at=now_ms - 10_000,
    )
    # 2. Open order
    o_open = models.PaperOrder(
        id="ord_open_1",
        instrument="XAUUSDT",
        direction="SHORT",
        state="paper_open",
        order_type="LIMIT",
        planned_entry=2640.0,
        actual_entry=2640.0,
        stop_loss=2650.0,
        take_profit=2620.0,
        quantity=0.3,
        initial_risk_usdt=3.0,
        created_at=now_ms - 30_000,
        opened_at=now_ms - 20_000,
    )
    # 3. Armed order (pending)
    o_armed = models.PaperOrder(
        id="ord_armed_1",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="LIMIT",
        planned_entry=2615.0,
        stop_loss=2605.0,
        take_profit=2635.0,
        quantity=0.4,
        initial_risk_usdt=4.0,
        created_at=now_ms - 10_000,
        expires_at=now_ms + 100_000,
    )
    # 4. Expired order
    o_expired = models.PaperOrder(
        id="ord_expired_1",
        instrument="XAUUSDT",
        direction="LONG",
        state="expired",
        order_type="LIMIT",
        planned_entry=2618.0,
        stop_loss=2608.0,
        take_profit=2638.0,
        quantity=0.4,
        initial_risk_usdt=4.0,
        exit_cause="EXPIRED",
        created_at=now_ms - 120_000,
        expires_at=now_ms - 20_000,
        closed_at=now_ms - 20_000,
    )
    # 5. Rejected order
    o_rejected = models.PaperOrder(
        id="ord_rejected_1",
        instrument="XAUUSDT",
        direction="SHORT",
        state="rejected",
        order_type="MARKET",
        planned_entry=2645.0,
        stop_loss=2655.0,
        take_profit=2625.0,
        quantity=0.4,
        initial_risk_usdt=4.0,
        invalidation_reason="NEWS_BLACKOUT",
        created_at=now_ms - 80_000,
        closed_at=now_ms - 75_000,
    )

    db.add_all([o_closed, o_open, o_armed, o_expired, o_rejected])
    db.commit()

    state = adapter.collect_state()
    # Must report exactly 2 fills (closed + open)
    assert state["total_fills"] == 2
    assert state["closed_trades_count"] == 1
    assert state["open_positions_count"] == 1
    assert state["pending_orders_count"] == 1
    assert state["expired_orders_count"] == 1
    assert state["rejected_orders_count"] == 1


def test_rule_124_125_net_pnl_reconciliation_with_fees(ledger_db):
    """
    Rules 124 & 125:
    Reconciles realized net PnL from independent hand-calculation against TradeLifecycleService ledger.
    Entry slippage is already embedded in actual_entry, so it must NOT be subtracted a second time.
    """
    db, _ = ledger_db
    now_ms = 1792050000000
    clock = ReplayClock(initial_ms=now_ms)

    cost_snap = {
        "phase": "FILLED_ESTIMATE",
        "multiplier": 1.0,
        "maker_fee_rate": 0.0002,
        "taker_fee_rate": 0.0004,
        "slippage_usd": 0.10,
        "tp_is_maker": True,  # TP exit gets maker rate
    }

    entry_price = 2623.95  # Ask 2623.85 + 0.10 slippage
    exit_price = 2645.00
    qty = 0.50

    order = models.PaperOrder(
        id="ord_pnl_recon",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        order_type="MARKET",
        planned_entry=2623.85,
        actual_entry=entry_price,
        stop_loss=2615.00,
        take_profit=exit_price,
        quantity=qty,
        initial_risk_usdt=5.0,
        cost_snapshot=json.dumps(cost_snap),
        created_at=now_ms,
        opened_at=now_ms,
    )
    db.add(order)
    db.commit()

    # Close the order via real TradeLifecycleService
    TradeLifecycleService.execute_close(
        db=db,
        order_id=order.id,
        exit_price=exit_price,
        exit_cause="TP_HIT",
        now_ms=now_ms + 60_000,
        clock=clock,
    )
    db.refresh(order)

    # Independent hand calculation
    gross_pnl = (exit_price - entry_price) * qty * 1.0
    entry_fee = entry_price * qty * 1.0 * 0.0004
    exit_fee = exit_price * qty * 1.0 * 0.0002  # maker fee for TP_HIT with tp_is_maker=True
    expected_fees = entry_fee + exit_fee
    expected_net_pnl = round(gross_pnl - expected_fees, 2)

    assert order.realized_pnl_net == expected_net_pnl
    assert order.realized_pnl_net > 0.0


def test_rule_127_128_project_replay_equity_and_drawdown(ledger_db):
    """
    Rules 127, 128 & 129:
    Projection of equity series and peak drawdown calculation.
    """
    db, _ = ledger_db
    now_ms = 1792050000000
    clock = ReplayClock(initial_ms=now_ms)
    adapter = CG01ReplayExecutionAdapter(db=db, clock=clock, initial_capital=1000.0)

    # Trade 1: Win +20 USD -> Equity reaches 1020 (Peak)
    o1 = models.PaperOrder(
        id="ord_seq_1",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=2620.0,
        actual_entry=2620.0,
        actual_exit=2640.0,
        stop_loss=2610.0,
        take_profit=2640.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        realized_pnl_net=20.00,
        exit_cause="TP_HIT",
        created_at=now_ms,
        opened_at=now_ms,
        closed_at=now_ms + 10_000,
    )
    # Trade 2: Loss -10 USD -> Equity drops to 1010 USD
    # Drawdown = (1020 - 1010) / 1020 * 100 = 0.98%
    o2 = models.PaperOrder(
        id="ord_seq_2",
        instrument="XAUUSDT",
        direction="SHORT",
        state="closed",
        planned_entry=2630.0,
        actual_entry=2630.0,
        actual_exit=2640.0,
        stop_loss=2640.0,
        take_profit=2610.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        realized_pnl_net=-10.00,
        exit_cause="SL_HIT",
        created_at=now_ms + 20_000,
        opened_at=now_ms + 20_000,
        closed_at=now_ms + 30_000,
    )
    db.add_all([o1, o2])
    db.commit()

    state = adapter.collect_state()
    metrics = state["metrics"]
    assert metrics["final_cash"] == 1010.00
    assert metrics["total_pnl_usd"] == 10.00
    assert metrics["winrate_pct"] == 50.0  # 1 win out of 2 trades
    # Max DD from 1020 down to 1010 is 10/1020 * 100 = 0.98%
    assert metrics["max_drawdown_pct"] == 0.98


def test_rule_130_161_open_end_position_reporting(ledger_db):
    """
    Rules 130, 161 & Appendix D (L1):
    When a replay range concludes while a position remains open:
    - fills_count = 1, closed = 0, open = 1
    - winrate_pct is strictly None (null, never 0.0%)
    - total_pnl_usd is None (no closed trades yet)
    """
    db, _ = ledger_db
    now_ms = 1792050000000
    clock = ReplayClock(initial_ms=now_ms)
    adapter = CG01ReplayExecutionAdapter(db=db, clock=clock, initial_capital=1000.0)

    o_open = models.PaperOrder(
        id="ord_open_end",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        order_type="LIMIT",
        planned_entry=2623.75,
        actual_entry=2623.75,
        stop_loss=2615.00,
        take_profit=2645.00,
        quantity=0.50,
        initial_risk_usdt=5.00,
        created_at=now_ms - 60_000,
        opened_at=now_ms - 30_000,
    )
    db.add(o_open)
    db.commit()

    state = adapter.collect_state()
    assert state["total_fills"] == 1
    assert state["closed_trades_count"] == 0
    assert state["open_positions_count"] == 1
    assert state["pending_orders_count"] == 0

    metrics = state["metrics"]
    assert metrics["winrate_pct"] is None
    assert metrics["total_pnl_usd"] is None
    assert metrics["max_drawdown_pct"] is None

    # Open position metadata is populated
    op = state["open_position"]
    assert op is not None
    assert op["trade_id"] == "ord_open_end"
    assert op["entry_price"] == 2623.75
    assert op["quantity"] == 0.50


def test_rule_131_165_net_winrate_requires_positive_net_pnl(ledger_db):
    """
    Rules 131 & 165:
    A trade where exit was labeled 'TP_HIT' or had gross profit,
    but net PnL is negative due to fees, must NOT be counted as a win!
    """
    db, _ = ledger_db
    now_ms = 1792050000000
    clock = ReplayClock(initial_ms=now_ms)
    adapter = CG01ReplayExecutionAdapter(db=db, clock=clock)

    # Trade has gross profit: (2620.10 - 2620.00) * 0.1 = +0.01
    # But taker fees: (2620 * 0.1 * 0.0004) * 2 = ~0.21 USD
    # Net PnL is negative!
    o_fee_loss = models.PaperOrder(
        id="ord_fee_drag",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=2620.0,
        actual_entry=2620.0,
        actual_exit=2620.10,
        stop_loss=2610.0,
        take_profit=2620.10,
        quantity=0.1,
        initial_risk_usdt=1.0,
        realized_pnl_net=-0.20,  # Negative after fees!
        exit_cause="TP_HIT",     # Labeled TP_HIT
        created_at=now_ms,
        opened_at=now_ms,
        closed_at=now_ms + 10_000,
    )
    db.add(o_fee_loss)
    db.commit()

    state = adapter.collect_state()
    assert state["closed_trades_count"] == 1
    # Because realized_pnl_net <= 0, it must NOT count as a win!
    assert state["metrics"]["winrate_pct"] == 0.0


def test_rule_132_166_date_boundary_attribution(ledger_db):
    """
    Rules 132 & 166:
    Order filled before midnight and closed after midnight in canonical timezone (Asia/Ho_Chi_Minh)
    must attribute the fill to Day 1 and the exit / realized PnL to Day 2.
    """
    db, _ = ledger_db
    vn_tz = ZoneInfo("Asia/Ho_Chi_Minh")

    # Day 1: 2026-10-15 23:50 VN
    dt_day1 = datetime(2026, 10, 15, 23, 50, tzinfo=vn_tz)
    t_fill = int(dt_day1.timestamp() * 1000)

    # Day 2: 2026-10-16 00:15 VN
    dt_day2 = datetime(2026, 10, 16, 0, 15, tzinfo=vn_tz)
    t_close = int(dt_day2.timestamp() * 1000)

    clock = ReplayClock(initial_ms=t_fill)

    order = models.PaperOrder(
        id="ord_midnight_crossover",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="MARKET",
        planned_entry=2620.0,
        stop_loss=2610.0,
        take_profit=2640.0,
        quantity=0.5,
        initial_risk_usdt=5.0,
        created_at=t_fill - 1000,
        armed_at=t_fill - 1000,
        expires_at=t_fill + 300_000,
    )
    db.add(order)
    db.commit()

    # 1. Fill at 23:50 on Day 1
    calc = calculate_risk_reward(
        direction="LONG",
        entry=2620.0,
        sl=2610.0,
        tp=2640.0,
        capital=1000.0,
        risk_pct=0.25,
        entry_has_slippage=True,
    )
    TradeLifecycleService.execute_fill(
        db=db,
        order=order,
        fill_price=2620.0,
        calc_result=calc,
        now_ms=t_fill,
        clock=clock,
    )
    db.refresh(order)
    assert order.state == "paper_open"

    audit_day1 = crud.get_or_create_today_audit(db, clock=clock)
    assert audit_day1.date_str == "2026-10-15"
    assert audit_day1.fills_count == 1
    assert audit_day1.realized_pnl_today == 0.0

    # 2. Advance clock to Day 2 and close
    clock.set_time(t_close)
    TradeLifecycleService.execute_close(
        db=db,
        order_id=order.id,
        exit_price=2640.0,
        exit_cause="TP_HIT",
        now_ms=t_close,
        clock=clock,
    )
    db.refresh(order)
    assert order.state in ("closed", "paper_closed")

    audit_day2 = crud.get_or_create_today_audit(db, clock=clock)
    assert audit_day2.date_str == "2026-10-16"
    assert audit_day2.fills_count == 0  # Fill was on day 1!
    assert audit_day2.realized_pnl_today > 0.0  # Realized PnL belongs to day 2!
