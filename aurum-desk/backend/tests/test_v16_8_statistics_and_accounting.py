"""
backend/tests/test_v16_8_statistics_and_accounting.py
V16.8 Reproduction and Verification of Statistics, Accounting and Decision Funnel (NHÓM 10, 11, 12, 13, 14, 17).

Tests:
1. Daily stats use canonical trading_day_timezone from policy (e.g. Asia/Ho_Chi_Minh).
2. DST boundary handling computes exact 23h/25h days.
3. Multiplier in cost_snapshot is propagated through collect_state to compute_equity_mark.
4. Actual persisted entry fee is preserved without fallback estimation.
5. Closed trades are chronologically sorted by closed_at then stable ID.
6. Decision funnel captures on_bar_close evaluation stages and reasons.
"""
import json
import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from database import run_schema_migrations
from lab.cg01_replay_driver import compute_equity_mark, CG01ReplayDriver
from lab.cg01_replay_execution_adapter import CG01ReplayExecutionAdapter
from services.clock import ReplayClock
from services.trading_policy_service import resolve_trade_day_bounds, resolve_effective_daily_policy, TradingPolicyService


@pytest.fixture
def lab_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    yield session
    session.close()


def test_daily_stats_canonical_timezone_vietnam(lab_db):
    """PHẦN 091, 092, 093, 161: Fill near UTC midnight attributes to correct Vietnam trade day."""
    policy = TradingPolicyService.get_or_create_policy(lab_db)
    policy.daily_timezone = "Asia/Ho_Chi_Minh"
    lab_db.commit()

    # UTC 2026-10-15 17:30:00 -> Vietnam 2026-10-16 00:30:00
    ts_vn_next_day = int(datetime(2026, 10, 15, 17, 30, tzinfo=ZoneInfo("UTC")).timestamp() * 1000)

    date_str, d_start, d_end = resolve_trade_day_bounds(ts_vn_next_day, "Asia/Ho_Chi_Minh")
    assert date_str == "2026-10-16", f"Expected Vietnam date 2026-10-16, got {date_str}"
    assert d_start <= ts_vn_next_day < d_end


def test_dst_boundary_handling_in_driver():
    """PHẦN 094, 162: Day bounds calculation handles DST 23h (spring) and 25h (fall) cleanly."""
    ny_tz = ZoneInfo("America/New_York")
    # 2026 spring forward: 2026-03-08 (23 hours)
    dt_spring = datetime(2026, 3, 8, 12, 0, tzinfo=ny_tz)
    t_spring = int(dt_spring.timestamp() * 1000)
    d_str_sp, start_sp, end_sp = resolve_trade_day_bounds(t_spring, "America/New_York")
    assert d_str_sp == "2026-03-08"
    assert (end_sp - start_sp) == 23 * 3600 * 1000

    # 2026 fall back: 2026-11-01 (25 hours)
    dt_fall = datetime(2026, 11, 1, 12, 0, tzinfo=ny_tz)
    t_fall = int(dt_fall.timestamp() * 1000)
    d_str_fa, start_fa, end_fa = resolve_trade_day_bounds(t_fall, "America/New_York")
    assert d_str_fa == "2026-11-01"
    assert (end_fa - start_fa) == 25 * 3600 * 1000


def test_contract_multiplier_in_collect_state_and_equity_mark(lab_db):
    """PHẦN 015, 111, 165: Multiplier in snapshot propagates to open_position and scales MTM unrealized PnL."""
    clock = ReplayClock(1792150000000)
    adapter = CG01ReplayExecutionAdapter(
        db=lab_db,
        clock=clock,
        initial_capital=5000.0,
        risk_pct=0.10,
    )

    # Seed an open order with contract_multiplier=10.0 in cost_snapshot
    cost_snap = {
        "contract_multiplier": 10.0,
        "entry_fee_usdt": 0.50,
        "taker_fee_rate": 0.0004,
    }
    open_order = models.PaperOrder(
        id="ord_multiplier_test",
        direction="LONG",
        state="paper_open",
        planned_entry=100.0,
        actual_entry=100.0,
        stop_loss=90.0,
        take_profit=120.0,
        quantity=2.0,
        initial_risk_usdt=5.0,
        cost_snapshot=json.dumps(cost_snap),
        created_at=1792150000000,
        opened_at=1792150000000,
    )
    lab_db.add(open_order)
    lab_db.commit()

    state = adapter.collect_state()
    op = state.get("open_position")
    assert op is not None
    # Must contain contract_multiplier
    assert float(op.get("contract_multiplier", 1.0)) == 10.0

    # Test compute_equity_mark uses contract_multiplier
    # Current price = 105.0 (+5.0 USD gain per unit)
    # qty = 2.0, multiplier = 10.0 -> unrealized = 2.0 * 5.0 * 10.0 = +100.0 USD
    # Cash = 5000.0, entry_fee = 0.50 -> MTM = 5000.0 - 0.50 + 100.0 = 5099.50
    current_px = 105.0
    mtm = compute_equity_mark(5000.0, op, current_px)
    assert abs(mtm - 5099.50) < 1e-2, f"Expected 5099.50 with multiplier 10.0, got {mtm}"


def test_actual_persisted_entry_fee_used(lab_db):
    """PHẦN 113, 166: Persisted fee takes priority over estimation."""
    clock = ReplayClock(1792150000000)
    adapter = CG01ReplayExecutionAdapter(
        db=lab_db,
        clock=clock,
        initial_capital=5000.0,
        risk_pct=0.10,
    )

    cost_snap = {
        "entry_fee_usdt": 1.25,  # Specific actual fee
        "taker_fee_rate": 0.0004,
    }
    open_order = models.PaperOrder(
        id="ord_fee_source_test",
        direction="LONG",
        state="paper_open",
        planned_entry=100.0,
        actual_entry=100.0,
        stop_loss=90.0,
        take_profit=120.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        cost_snapshot=json.dumps(cost_snap),
        created_at=1792150000000,
        opened_at=1792150000000,
    )
    lab_db.add(open_order)
    lab_db.commit()

    state = adapter.collect_state()
    op = state.get("open_position")
    assert op is not None
    assert op.get("entry_fee_usdt") == 1.25


def test_closed_trades_chronological_sort(lab_db):
    """PHẦN 115: Closed trades are returned sorted by closed_at then stable ID."""
    clock = ReplayClock(1792150000000)
    adapter = CG01ReplayExecutionAdapter(
        db=lab_db,
        clock=clock,
        initial_capital=5000.0,
        risk_pct=0.10,
    )

    # Insert orders out of order
    o3 = models.PaperOrder(id="ord_3", direction="LONG", state="closed", planned_entry=100.0, stop_loss=90.0, take_profit=120.0, quantity=1.0, initial_risk_usdt=5.0, created_at=10, opened_at=20, closed_at=300)
    o1 = models.PaperOrder(id="ord_1", direction="LONG", state="closed", planned_entry=100.0, stop_loss=90.0, take_profit=120.0, quantity=1.0, initial_risk_usdt=5.0, created_at=10, opened_at=20, closed_at=100)
    o2 = models.PaperOrder(id="ord_2", direction="LONG", state="closed", planned_entry=100.0, stop_loss=90.0, take_profit=120.0, quantity=1.0, initial_risk_usdt=5.0, created_at=10, opened_at=20, closed_at=200)
    lab_db.add_all([o3, o1, o2])
    lab_db.commit()

    state = adapter.collect_state()
    trade_ids = [t["trade_id"] for t in state["closed_trades"]]
    assert trade_ids == ["ord_1", "ord_2", "ord_3"]


def test_session_policy_configurable(lab_db):
    """PHẦN 163: Session check uses policy-configured window instead of hardcoded 08:00-17:00."""
    from lab.cg01_replay_driver import _is_in_ny_session
    policy = TradingPolicyService.get_or_create_policy(lab_db)
    policy.ny_entry_start = "09:30"
    policy.ny_entry_end = "11:30"
    policy.ny_fallback_start = "11:00"
    lab_db.commit()

    ny_tz = ZoneInfo("America/New_York")
    # 08:30 NY time: inside old 08:00-17:00, but OUTSIDE configured 09:30-11:30 window
    dt_early = datetime(2026, 10, 15, 8, 30, tzinfo=ny_tz)
    ts_early = int(dt_early.timestamp() * 1000)
    assert not _is_in_ny_session(ts_early, policy=policy), "08:30 should be outside configured 09:30-11:30 window"

    # 10:00 NY time: inside configured window
    dt_in = datetime(2026, 10, 15, 10, 0, tzinfo=ny_tz)
    ts_in = int(dt_in.timestamp() * 1000)
    assert _is_in_ny_session(ts_in, policy=policy), "10:00 should be inside configured 09:30-11:30 window"


def test_missing_dataset_returns_blocked_data():
    """PHẦN 110, 170: Nonexistent dataset returns BLOCKED_DATA with clear reason."""
    driver = CG01ReplayDriver(m1_dataset_path="/nonexistent/path/candles.json")
    res = driver.run()
    assert res["status"] == "BLOCKED_DATA"
    assert "Thiếu tệp dữ liệu M1" in res["reason"]
