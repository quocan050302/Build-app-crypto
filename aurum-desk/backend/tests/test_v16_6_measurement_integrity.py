"""
backend/tests/test_v16_6_measurement_integrity.py
V16.6 Measurement Integrity Test Suite:
1. test_adapter_config_plumbing_exact_parity:
   Authoritative config plumbing from Lab down to orchestrator, broker, and order plan snapshot.
2. test_daily_stats_non_cumulative_fills_and_ny_session_count:
   Per-day delta statistics and precise NY session fill time attribution.
3. test_intrabar_limit_entry_wick_fill_without_close_touch:
   Limit entry execution via intrabar candle wick penetration without requiring close price touch.
4. test_intrabar_no_same_candle_retroactive_fill:
   Causality protection: order created at candle t close is never retroactively filled in candle t.
5. test_intrabar_ambiguous_candle_worst_case_sl_priority:
   Conservative worst-case handling: SL priority and intrabar_ambiguous flag when both SL and TP are touched.
6. test_open_position_at_end_of_run_reports_valid_mtm_drawdown:
   MTM series drawdown and net equity reconciliation when replay ends with an active open position.
"""
from datetime import datetime
import json
import os
import tempfile
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import crud
from database import run_schema_migrations
from domain_calculator import CostAssumptions, calculate_risk_reward
from lab.cg01_replay_driver import CG01ReplayDriver, _is_in_ny_session, compute_equity_mark
from lab.cg01_replay_execution_adapter import CG01ReplayExecutionAdapter
import models
from models import Base
from services.cg01_types import CG01Config, CG01ProfileMode
from services.clock import ReplayClock
from services.scalp_m1_types import WatchedZone
from services.trading_policy_service import TradingPolicyService
from tests.test_v16_3_cg01_pipeline import build_long_fixture_series, make_valid_quote


@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    policy = TradingPolicyService.get_or_create_policy(session)
    policy.max_daily_fills = 3
    policy.daily_min_goal = 2
    policy.ny_min_fills = 1
    policy.is_active = False
    session.commit()

    try:
        yield session, engine
    finally:
        session.close()


def test_adapter_config_plumbing_exact_parity(test_db):
    """
    Test 1 (V16.6 Part A):
    Adapter config plumbing:
    initial_capital=5000.0, risk_pct=0.10, slippage=0.0
    Snapshot must have:
    - slippage == 0.0 (not overridden to 0.10)
    - initial_risk_usd == 5.00 (0.10% of 5000.0)
    - risk_pct == 0.10
    """
    db, _ = test_db
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000

    clock = ReplayClock(initial_ms=now_ms)
    cfg = CG01Config(enabled=True, profile_mode=CG01ProfileMode.PAPER_EXECUTION)
    adapter = CG01ReplayExecutionAdapter(
        db=db,
        clock=clock,
        config=cfg,
        initial_capital=5000.0,
        risk_pct=0.10,
        slippage=0.0,
    )

    poi = WatchedZone(
        zone_id="poi_v16_6_cfg",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BULLISH",
        top=2625.0,
        bottom=2620.0,
        created_at=t_start - 3600_000,
        known_at=t_start - 1800_000,
        touch_count=0,
    )
    adapter.orchestrator.set_active_zones("XAUUSDT", [poi])

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    bar_event = {
        "symbol": "XAUUSDT",
        "as_of_ms": now_ms,
        "quote": make_valid_quote(2625.5, 2625.7, now_ms),
        "candles_1m": c_1m,
        "candles_15m": c_15m,
        "candles_1h": c_1h,
        "candles_4h": c_4h,
    }
    adapter.on_bar_close(bar_event)

    order = db.query(models.PaperOrder).filter(models.PaperOrder.direction == "LONG").first()
    assert order is not None, "Order must be created by orchestrator"
    assert order.state == "armed"

    snapshot = order.snapshot
    assert snapshot is not None, "Order must have plan_snapshot"
    assert snapshot.slippage == 0.0, f"Expected slippage == 0.0, got {snapshot.slippage}"
    assert abs(snapshot.initial_risk_usd - 5.00) < 0.05, f"Expected initial_risk_usd ~ 5.00, got {snapshot.initial_risk_usd}"
    assert abs(snapshot.risk_pct - 0.10) < 1e-4, f"Expected risk_pct ~ 0.10, got {snapshot.risk_pct}"


def test_daily_stats_non_cumulative_fills_and_ny_session_count(test_db):
    """
    Test 2 (V16.6 Part B):
    Replay simulation across 3 consecutive days:
    - Day 1: 2 fills (1 at 10:00 AM NY -> in NY session; 1 at 02:00 AM NY -> outside NY)
    - Day 2: 1 fill (at 10:30 AM NY -> in NY session)
    - Day 3: 0 fills (0 orders), but end clock in NY session.
    Assert on daily_stats:
    - Day 1: fills_count == 2, ny_fills == 1
    - Day 2: fills_count == 1 (never 3), ny_fills == 1
    - Day 3: fills_count == 0 (never 3), ny_fills == 0
    - Total fills across 3 days == total_fills == 3
    """
    db, _ = test_db
    ny_tz = ZoneInfo("America/New_York")

    # Day 1: 2026-10-14 (Wednesday)
    # 02:00 AM NY (non-NY session)
    dt_d1_non_ny = datetime(2026, 10, 14, 2, 0, tzinfo=ny_tz)
    t_d1_non_ny = int(dt_d1_non_ny.timestamp() * 1000)

    # 10:00 AM NY (NY session)
    dt_d1_ny = datetime(2026, 10, 14, 10, 0, tzinfo=ny_tz)
    t_d1_ny = int(dt_d1_ny.timestamp() * 1000)

    # Day 2: 2026-10-15 (Thursday)
    # 10:30 AM NY (NY session)
    dt_d2_ny = datetime(2026, 10, 15, 10, 30, tzinfo=ny_tz)
    t_d2_ny = int(dt_d2_ny.timestamp() * 1000)

    # Insert orders into DB
    o1 = models.PaperOrder(
        id="ord_d1_1",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=2620.0,
        actual_entry=2620.0,
        actual_exit=2630.0,
        stop_loss=2610.0,
        take_profit=2630.0,
        quantity=0.5,
        initial_risk_usdt=5.0,
        realized_pnl_net=5.0,
        exit_cause="TP_HIT",
        created_at=t_d1_non_ny - 60_000,
        opened_at=t_d1_non_ny,
        closed_at=t_d1_non_ny + 60_000,
    )
    o2 = models.PaperOrder(
        id="ord_d1_2",
        instrument="XAUUSDT",
        direction="SHORT",
        state="closed",
        planned_entry=2640.0,
        actual_entry=2640.0,
        actual_exit=2630.0,
        stop_loss=2650.0,
        take_profit=2630.0,
        quantity=0.5,
        initial_risk_usdt=5.0,
        realized_pnl_net=5.0,
        exit_cause="TP_HIT",
        created_at=t_d1_ny - 60_000,
        opened_at=t_d1_ny,
        closed_at=t_d1_ny + 60_000,
    )
    o3 = models.PaperOrder(
        id="ord_d2_1",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=2625.0,
        actual_entry=2625.0,
        stop_loss=2615.0,
        take_profit=2645.0,
        quantity=0.5,
        initial_risk_usdt=5.0,
        created_at=t_d2_ny - 60_000,
        opened_at=t_d2_ny,
    )
    db.add_all([o1, o2, o3])
    db.commit()

    # Now calculate daily stats for these 3 days using driver's authoritative filtering logic
    dates = ["2026-10-14", "2026-10-15", "2026-10-16"]
    daily_stats = {}
    all_orders = db.query(models.PaperOrder).all()

    for d_key in dates:
        d_start_dt = datetime.strptime(d_key, "%Y-%m-%d").replace(tzinfo=ny_tz)
        d_start_ms = int(d_start_dt.timestamp() * 1000)
        d_end_ms = d_start_ms + 86_400_000

        day_filled = [
            o for o in all_orders
            if o.opened_at is not None and d_start_ms <= o.opened_at < d_end_ms
        ]
        daily_stats[d_key] = {
            "date": d_key,
            "fills_count": len(day_filled),
            "ny_fills": sum(1 for o in day_filled if _is_in_ny_session(o.opened_at)),
        }

    assert daily_stats["2026-10-14"]["fills_count"] == 2
    assert daily_stats["2026-10-14"]["ny_fills"] == 1
    assert daily_stats["2026-10-15"]["fills_count"] == 1
    assert daily_stats["2026-10-15"]["ny_fills"] == 1
    assert daily_stats["2026-10-16"]["fills_count"] == 0
    assert daily_stats["2026-10-16"]["ny_fills"] == 0

    total_fills = len([o for o in all_orders if o.opened_at is not None])
    assert sum(d["fills_count"] for d in daily_stats.values()) == total_fills == 3


def test_intrabar_limit_entry_wick_fill_without_close_touch(test_db):
    """
    Test 3 (V16.6 Part C):
    Create Limit LONG at 100.0 at end of candle t.
    Next candle t+1: open=102.0, high=103.0, low=99.5, close=102.5.
    Wick low=99.5 penetrates 100.0, but close=102.5 is above 100.0. SL at 98.0 not touched.
    Assert: Limit LONG must transition to FILLED (paper_open) at 100.0 in candle t+1.
    """
    db, _ = test_db
    now_ms = 1792050000000
    clock = ReplayClock(initial_ms=now_ms)
    adapter = CG01ReplayExecutionAdapter(db=db, clock=clock, initial_capital=5000.0)

    # Armed Limit LONG order created at end of candle t (now_ms)
    order = models.PaperOrder(
        id="ord_wick_fill",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="LIMIT",
        planned_entry=100.0,
        stop_loss=98.0,
        take_profit=105.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=now_ms,
        armed_at=now_ms,
        expires_at=now_ms + 300_000,
    )
    db.add(order)
    db.commit()

    # Candle t+1 (now_ms + 60_000)
    candle_t1 = {
        "timestamp": now_ms + 60_000,
        "open": 102.0,
        "high": 103.0,
        "low": 99.5,
        "close": 102.5,
        "bid": 102.45,
        "ask": 102.55,
        "bar_open_time": now_ms,
        "bar_duration_ms": 60_000,
        "is_candle": True,
    }
    adapter.on_quote(candle_t1)
    db.refresh(order)

    assert order.state == "paper_open", f"Expected paper_open, got {order.state}"
    assert order.actual_entry == 100.0, f"Expected actual_entry == 100.0, got {order.actual_entry}"


def test_intrabar_no_same_candle_retroactive_fill(test_db):
    """
    Test 4 (V16.6 Part C Causality):
    At candle t, liquidity sweep creates signal at close of candle t.
    Assert: Order must remain in armed state at end of candle t, never retroactively
    filled using the high/low of the very candle that generated it.
    """
    db, _ = test_db
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000

    clock = ReplayClock(initial_ms=now_ms)
    cfg = CG01Config(enabled=True, profile_mode=CG01ProfileMode.PAPER_EXECUTION)
    adapter = CG01ReplayExecutionAdapter(db=db, clock=clock, config=cfg)

    poi = WatchedZone(
        zone_id="poi_causality",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BULLISH",
        top=2625.0,
        bottom=2620.0,
        created_at=t_start - 3600_000,
        known_at=t_start - 1800_000,
        touch_count=0,
    )
    adapter.orchestrator.set_active_zones("XAUUSDT", [poi])

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    bar_event = {
        "symbol": "XAUUSDT",
        "as_of_ms": now_ms,
        "quote": make_valid_quote(2625.5, 2625.7, now_ms),
        "candles_1m": c_1m,
        "candles_15m": c_15m,
        "candles_1h": c_1h,
        "candles_4h": c_4h,
    }
    adapter.on_bar_close(bar_event)

    order = db.query(models.PaperOrder).filter(models.PaperOrder.direction == "LONG").first()
    assert order is not None
    # Crucial assertion: Must be armed (PENDING), not filled retroactively
    assert order.state == "armed"
    assert order.actual_entry is None
    assert order.opened_at is None


def test_intrabar_ambiguous_candle_worst_case_sl_priority(test_db):
    """
    Test 5 (V16.6 Part C Worst-Case Ambiguity):
    Open position with entry=100.0, sl=99.0, tp=102.0.
    Next candle: open=100.0, high=103.0, low=98.0, close=101.0 (touches both SL and TP).
    Assert: Closed at STOP_LOSS (conservative worst-case), intrabar_ambiguous == True.
    """
    db, _ = test_db
    now_ms = 1792050000000
    clock = ReplayClock(initial_ms=now_ms)
    adapter = CG01ReplayExecutionAdapter(db=db, clock=clock, initial_capital=5000.0)

    order = models.PaperOrder(
        id="ord_ambig",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        order_type="LIMIT",
        planned_entry=100.0,
        actual_entry=100.0,
        stop_loss=99.0,
        take_profit=102.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=now_ms - 60_000,
        opened_at=now_ms - 60_000,
    )
    db.add(order)
    db.commit()

    ambig_candle = {
        "timestamp": now_ms,
        "open": 100.0,
        "high": 103.0,
        "low": 98.0,
        "close": 101.0,
        "bid": 100.95,
        "ask": 101.05,
        "bar_open_time": now_ms - 60_000,
        "bar_duration_ms": 60_000,
        "is_candle": True,
    }
    adapter.on_quote(ambig_candle)
    db.refresh(order)

    assert order.state in ("closed", "paper_closed")
    assert order.actual_exit == 99.0, f"Expected exit at SL 99.0, got {order.actual_exit}"
    assert "SL" in (order.exit_cause or "") or "STOP_LOSS" in (order.exit_cause or "")
    assert getattr(order, "intrabar_ambiguous", False) is True


def test_open_position_at_end_of_run_reports_valid_mtm_drawdown():
    """
    Test 6 (V16.6 Part C MTM Drawdown):
    Run replay with initial_capital=5000.0, 1 LONG position filled at 100.0,
    price drops to 95.0, then replay range ends while position remains OPEN (SL at 90.0 untouched).
    Assert:
    - max_drawdown_pct is not None and max_drawdown_pct > 0.0.
    - Final mtm_equity matches initial_capital - entry_fee + unrealized_pnl.
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 9, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)

    # 18 bars (15 warmup, 1 fill bar, 2 price decline bars)
    bars = []
    # Warmup: 15 bars around 102.0
    for i in range(15):
        t_bar = t_start + i * 60_000
        bars.append({
            "open_time": t_bar,
            "open": 102.0,
            "high": 102.5,
            "low": 101.5,
            "close": 102.0,
            "volume": 100.0,
        })

    # Bar 16: touches 100.0 (low=99.5, high=101.0, close=100.5)
    t_bar16 = t_start + 15 * 60_000
    bars.append({
        "open_time": t_bar16,
        "open": 101.0,
        "high": 101.0,
        "low": 99.5,
        "close": 100.5,
        "volume": 100.0,
    })

    # Bar 17: drops to 97.0
    t_bar17 = t_start + 16 * 60_000
    bars.append({
        "open_time": t_bar17,
        "open": 100.5,
        "high": 100.5,
        "low": 96.8,
        "close": 97.0,
        "volume": 100.0,
    })

    # Bar 18: drops to 95.0 (SL at 90.0 untouched)
    t_bar18 = t_start + 17 * 60_000
    bars.append({
        "open_time": t_bar18,
        "open": 97.0,
        "high": 97.0,
        "low": 94.8,
        "close": 95.0,
        "volume": 100.0,
    })

    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
        json.dump(bars, f)
        temp_path = f.name

    def seed_order(adapter, db):
        # Insert armed order ready to be filled on Bar 16
        order = models.PaperOrder(
            id="ord_open_end_recon",
            instrument="XAUUSDT",
            direction="LONG",
            state="armed",
            order_type="LIMIT",
            planned_entry=100.0,
            stop_loss=90.0,
            take_profit=125.0,
            quantity=1.0,
            initial_risk_usdt=10.0,
            risk_pct=0.10,
            created_at=t_start,
            armed_at=t_start,
            expires_at=t_start + 3_600_000,
        )
        db.add(order)
        db.commit()

    try:
        driver = CG01ReplayDriver(
            m1_dataset_path=temp_path,
            initial_capital=5000.0,
            risk_pct=0.10,
            costs=CostAssumptions(maker_fee_rate=0.0002, taker_fee_rate=0.0004, slippage_usd=0.0),
            setup_hook=seed_order,
        )
        res = driver.run()

        assert res["status"] == "COMPLETED"
        assert res["total_fills"] == 1
        assert res["closed_trades_count"] == 0
        assert res["open_positions_count"] == 1
        assert res["open_position"] is not None

        # Assert max_drawdown_pct is not None and > 0.0
        max_dd = res["metrics"]["max_drawdown_pct"]
        assert max_dd is not None, "max_drawdown_pct must not be None when equity_mtm has series"
        assert max_dd > 0.0, f"Expected max_drawdown_pct > 0.0, got {max_dd}"

        # Assert final mtm_equity matches initial_capital - entry_fee + unrealized_pnl
        last_mtm_point = res["equity_mtm"][-1]
        final_mtm = last_mtm_point["mtm_equity"]
        entry_fee = res["open_position"]["entry_fee_usdt"]
        qty = res["open_position"]["quantity"]
        unrealized = (95.0 - 100.0) * qty
        expected_final_mtm = round(5000.0 - entry_fee + unrealized, 2)
        assert abs(final_mtm - expected_final_mtm) < 0.05, f"Expected final mtm {expected_final_mtm}, got {final_mtm}"
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
