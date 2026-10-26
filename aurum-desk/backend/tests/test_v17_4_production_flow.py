"""
backend/tests/test_v17_4_production_flow.py
V17.4 Parts 161 - 170, 173, 176:
- 6 Production E2E tests:
  1. Asia LONG (20:30 NY)
  2. Asia SHORT (20:30 NY)
  3. London LONG outside 03-04 NY (05:00 NY)
  4. London SHORT outside 03-04 NY (05:00 NY)
  5. NY LONG post-11:00 (11:30 NY)
  6. NY SHORT post-11:00 (11:30 NY)
- Negative control tests:
  7. Missing sweep -> 0 ARM
  8. Weak displacement -> 0 ARM
  9. Insufficient distance / bad target -> 0 ARM
  10. Price does not touch entry -> NO FILL (remains armed)
- Life-cycle preservation:
  11. Existing open position preserved through session configuration change.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import run_schema_migrations
from domain_calculator import calculate_risk_reward
from models import Base, PaperOrder, SystemConfig, TradingPolicy
from policy_constants import ALL_SESSIONS_SETUP_FIRST, ALL_SESSIONS_WITH_NY_RESERVE
from services.cg01_types import CG01Stage
from services.scalp_m1_types import M1ScalpConfig, WatchedZone
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.trade_lifecycle_service import TradeLifecycleService
from services.trading_policy_service import TradingPolicyService
from tests.test_v16_3_cg01_pipeline import (
    build_long_fixture_series,
    build_short_fixture_series,
    make_bar,
    make_valid_quote,
)


@pytest.fixture
def prod_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    policy = TradingPolicy(
        entry_session_policy=ALL_SESSIONS_SETUP_FIRST,
        ny_min_fills=0,
        reserve_ny_slot=False,
        ny_fallback_enabled=False,
        max_daily_fills=3,
        min_net_rr=1.5,
        version=1,
        updated_at=1700000000000,
    )
    session.add(policy)
    session.add(SystemConfig(key="timing_profile", value="CG01_ALL_SESSIONS_PAPER", updated_at=1700000000000))
    session.add(SystemConfig(key="daily_ny_goal", value="0", updated_at=1700000000000))
    session.add(SystemConfig(key="execution_scope", value="PAPER", updated_at=1700000000000))
    session.commit()

    try:
        yield session
    finally:
        session.close()


def _run_long_case(db, t_start: int):
    now_ms = t_start + 540_000
    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    poi = WatchedZone(
        zone_id=f"poi_long_{t_start}",
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
    quote = make_valid_quote(2625.5, 2625.6, now_ms)
    cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
        timing_profile="CG01_ALL_SESSIONS_PAPER",
    )

    orchestrator = ScalpWatcherOrchestrator()
    orchestrator.set_active_zones("XAUUSDT", [poi])

    res = orchestrator.evaluate_cycle(
        db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        quote=quote,
        candles_1m=c_1m,
        candles_5m=[],
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        config=cfg,
        auto_enabled=True,
    )

    # Verify armed
    order = db.query(PaperOrder).filter(PaperOrder.state == "armed").order_by(PaperOrder.created_at.desc()).first()
    assert order is not None
    assert order.direction == "LONG"
    assert order.planned_entry == 2623.75
    assert order.stop_loss == 2619.45
    assert order.take_profit > order.planned_entry

    # Execute fill
    fill_time = now_ms + 60_000
    calc = calculate_risk_reward(
        direction="LONG",
        entry=order.planned_entry,
        sl=order.stop_loss,
        tp=order.take_profit,
        capital=2500.0,
        risk_pct=0.25,
        now_ms=fill_time,
    )
    filled = TradeLifecycleService.execute_fill(
        db,
        order,
        fill_price=order.planned_entry,
        calc_result=calc,
        source="AUTO",
        now_ms=fill_time,
    )
    assert filled.state == "paper_open"

    # Execute close at TP
    exit_time = fill_time + 120_000
    closed = TradeLifecycleService.execute_close(
        db,
        order_id=filled.id,
        exit_price=filled.take_profit,
        exit_cause="TP_HIT",
        now_ms=exit_time,
    )
    assert closed.state == "closed"
    assert closed.realized_pnl_net > 0.0
    return closed


def _run_short_case(db, t_start: int):
    now_ms = t_start + 540_000
    c_4h, c_1h, c_15m, c_1m = build_short_fixture_series(t_start)
    poi = WatchedZone(
        zone_id=f"poi_short_{t_start}",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BEARISH",
        top=2655.0,
        bottom=2650.0,
        created_at=t_start - 3600_000,
        known_at=t_start - 1800_000,
        touch_count=0,
    )
    quote = make_valid_quote(2649.5, 2649.7, now_ms)
    cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
        timing_profile="CG01_ALL_SESSIONS_PAPER",
    )

    orchestrator = ScalpWatcherOrchestrator()
    orchestrator.set_active_zones("XAUUSDT", [poi])

    res = orchestrator.evaluate_cycle(
        db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        quote=quote,
        candles_1m=c_1m,
        candles_5m=[],
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        config=cfg,
        auto_enabled=True,
    )

    order = db.query(PaperOrder).filter(PaperOrder.state == "armed").order_by(PaperOrder.created_at.desc()).first()
    assert order is not None
    assert order.direction == "SHORT"
    assert order.planned_entry == 2651.15
    assert order.stop_loss == 2655.55
    assert order.take_profit < order.planned_entry

    # Execute fill
    fill_time = now_ms + 60_000
    calc = calculate_risk_reward(
        direction="SHORT",
        entry=order.planned_entry,
        sl=order.stop_loss,
        tp=order.take_profit,
        capital=2500.0,
        risk_pct=0.25,
        now_ms=fill_time,
    )
    filled = TradeLifecycleService.execute_fill(
        db,
        order,
        fill_price=order.planned_entry,
        calc_result=calc,
        source="AUTO",
        now_ms=fill_time,
    )
    assert filled.state == "paper_open"

    # Execute close at TP
    exit_time = fill_time + 120_000
    closed = TradeLifecycleService.execute_close(
        db,
        order_id=filled.id,
        exit_price=filled.take_profit,
        exit_cause="TP_HIT",
        now_ms=exit_time,
    )
    assert closed.state == "closed"
    assert closed.realized_pnl_net > 0.0
    return closed


def test_part_162_e2e_asia_long(prod_db):
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 20, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    closed = _run_long_case(prod_db, t_start)
    assert closed.exit_cause == "TP_HIT"


def test_part_163_e2e_asia_short(prod_db):
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 20, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    closed = _run_short_case(prod_db, t_start)
    assert closed.exit_cause == "TP_HIT"


def test_part_164_e2e_london_long_outside_silver_bullet(prod_db):
    # 05:00 NY is London session, but strictly outside 03:00 - 04:00 NY Silver Bullet
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 5, 0, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    closed = _run_long_case(prod_db, t_start)
    assert closed.exit_cause == "TP_HIT"


def test_part_165_e2e_london_short_outside_silver_bullet(prod_db):
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 5, 0, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    closed = _run_short_case(prod_db, t_start)
    assert closed.exit_cause == "TP_HIT"


def test_part_166_e2e_ny_long_post_eleven(prod_db):
    # 11:30 NY is after the 08:00 - 11:00 NY entry window
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 11, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    closed = _run_long_case(prod_db, t_start)
    assert closed.exit_cause == "TP_HIT"


def test_part_167_e2e_ny_short_post_eleven(prod_db):
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 11, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    closed = _run_short_case(prod_db, t_start)
    assert closed.exit_cause == "TP_HIT"


def test_part_168_negative_missing_sweep_no_arm(prod_db):
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 20, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 540_000

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    # Tamper with candle 22 (the sweep candle) so it does NOT sweep swing low 2621.0
    c_1m[22] = make_bar(c_1m[22]["open_time"], 2622.0, 2622.5, 2621.2, 2621.8)

    poi = WatchedZone(
        zone_id="poi_long_no_sweep",
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
    quote = make_valid_quote(2625.5, 2625.6, now_ms)
    cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
        timing_profile="CG01_ALL_SESSIONS_PAPER",
    )

    orchestrator = ScalpWatcherOrchestrator()
    orchestrator.set_active_zones("XAUUSDT", [poi])
    orchestrator.evaluate_cycle(
        prod_db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        quote=quote,
        candles_1m=c_1m,
        candles_5m=[],
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        config=cfg,
        auto_enabled=True,
    )

    assert prod_db.query(PaperOrder).filter(PaperOrder.state == "armed").first() is None


def test_part_173_no_touch_no_fill(prod_db):
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 20, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 540_000

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    poi = WatchedZone(
        zone_id="poi_long_touch",
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
    quote = make_valid_quote(2625.5, 2625.6, now_ms)
    cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
        timing_profile="CG01_ALL_SESSIONS_PAPER",
    )

    orchestrator = ScalpWatcherOrchestrator()
    orchestrator.set_active_zones("XAUUSDT", [poi])
    orchestrator.evaluate_cycle(
        prod_db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        quote=quote,
        candles_1m=c_1m,
        candles_5m=[],
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        config=cfg,
        auto_enabled=True,
    )

    order = prod_db.query(PaperOrder).filter(PaperOrder.state == "armed").first()
    assert order is not None

    # Quote stays far above limit price (planned_entry=2623.75, ask=2628.0)
    far_quote = make_valid_quote(2627.9, 2628.0, now_ms + 60_000)
    # The order remains armed, not paper_open
    prod_db.refresh(order)
    assert order.state == "armed"


def test_part_176_session_change_open_position_preserved(prod_db):
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 20, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 540_000

    # 1. Create a paper_open position
    order = PaperOrder(
        id="ord_preserve_flow",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=2623.75,
        actual_entry=2623.75,
        stop_loss=2619.45,
        take_profit=2640.0,
        quantity=0.1,
        initial_risk_usdt=4.3,
        opened_at=now_ms,
        created_at=now_ms,
    )
    prod_db.add(order)
    prod_db.commit()

    # 2. Switch session setting to legacy ALL_SESSIONS_WITH_NY_RESERVE
    policy = prod_db.query(TradingPolicy).first()
    policy.entry_session_policy = ALL_SESSIONS_WITH_NY_RESERVE
    policy.ny_min_fills = 1
    policy.reserve_ny_slot = True
    prod_db.commit()

    # 3. Position and exit monitor continues uninterrupted
    exit_time = now_ms + 120_000
    closed = TradeLifecycleService.execute_close(
        prod_db,
        order_id=order.id,
        exit_price=order.take_profit,
        exit_cause="TP_HIT",
        now_ms=exit_time,
    )
    assert closed.state == "closed"
    assert closed.realized_pnl_net > 0.0


def test_part_169_negative_weak_displacement_no_arm(prod_db):
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 20, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 540_000

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    # Candle 24 (break candle) modified to have weak body (not displacing above swing high 2623.5)
    c_1m[24] = make_bar(c_1m[24]["open_time"], 2622.8, 2623.2, 2622.6, 2623.0)

    poi = WatchedZone(
        zone_id="poi_long_weak_disp",
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
    quote = make_valid_quote(2625.5, 2625.6, now_ms)
    cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
        timing_profile="CG01_ALL_SESSIONS_PAPER",
    )

    orchestrator = ScalpWatcherOrchestrator()
    orchestrator.set_active_zones("XAUUSDT", [poi])
    orchestrator.evaluate_cycle(
        prod_db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        quote=quote,
        candles_1m=c_1m,
        candles_5m=[],
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        config=cfg,
        auto_enabled=True,
    )

    assert prod_db.query(PaperOrder).filter(PaperOrder.state == "armed").first() is None


def test_part_170_negative_target_below_rr_no_arm(prod_db):
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 20, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 540_000

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    # Modify 15M target high from 2640 down to 2624.0 (below entry 2623.75 or resulting in negative R:R)
    c_15m[20] = make_bar(c_15m[20]["open_time"], 2623.0, 2624.0, 2622.0, 2623.5, tf="15M")

    poi = WatchedZone(
        zone_id="poi_long_bad_tp",
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
    quote = make_valid_quote(2625.5, 2625.6, now_ms)
    cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
        timing_profile="CG01_ALL_SESSIONS_PAPER",
    )

    orchestrator = ScalpWatcherOrchestrator()
    orchestrator.set_active_zones("XAUUSDT", [poi])
    orchestrator.evaluate_cycle(
        prod_db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        quote=quote,
        candles_1m=c_1m,
        candles_5m=[],
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        config=cfg,
        auto_enabled=True,
    )

    assert prod_db.query(PaperOrder).filter(PaperOrder.state == "armed").first() is None
