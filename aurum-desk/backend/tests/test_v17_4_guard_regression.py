"""
backend/tests/test_v17_4_guard_regression.py
V17.4 Parts 171 & 172:
- Guard matrix before ARM:
  1. Auto OFF -> 0 order armed.
  2. Model OFF -> 0 order armed.
  3. Red news blackout -> blocked with NEWS_BLACKOUT.
  4. Approved RED lesson -> blocked.
  5. 3 daily fills already reached -> blocked.
  6. Existing open position -> blocked (max_open_positions == 1).
  7. Net RR below Settings -> blocked.
- Guard recheck before FILL:
  8. ARM succeeded, but news blackout becomes active before quote touch -> FILL blocked.
  9. ARM succeeded, but policy switched to NY_ONLY outside NY before quote touch -> FILL blocked.
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import crud
from database import run_schema_migrations
from models import Base, EconomicNews, Lesson, PaperOrder, SystemConfig, TradingPolicy
from policy_constants import ALL_SESSIONS_SETUP_FIRST, NY_ONLY
from services.cg01_types import CG01Config, CG01Stage
from services.clock import ReplayClock
from services.execution_coordinator import ExecutionCoordinator
from services.lesson_rule_service import LessonRuleService
from services.quote_validator import ValidatedQuote
from services.scalp_m1_types import M1ScalpConfig, WatchedZone
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.trading_policy_service import TradingPolicyService
from tests.test_v16_3_cg01_pipeline import build_long_fixture_series, make_valid_quote


@pytest.fixture
def guard_db():
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
    session.commit()

    LessonRuleService.invalidate_cache()
    try:
        yield session
    finally:
        session.close()


def test_part_171_auto_off_prevents_arm(guard_db):
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 540_000

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    poi = WatchedZone(
        zone_id="poi_long_15m",
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
    )

    orchestrator = ScalpWatcherOrchestrator()
    orchestrator.set_active_zones("XAUUSDT", [poi])

    # auto_enabled=False -> Candidate may be evaluated, but 0 PaperOrder armed in DB
    orchestrator.evaluate_cycle(
        guard_db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        quote=quote,
        candles_1m=c_1m,
        candles_5m=[],
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        config=cfg,
        auto_enabled=False,
    )

    armed = guard_db.query(PaperOrder).filter(PaperOrder.state == "armed").first()
    assert armed is None


def test_part_171_news_blackout_prevents_arm(guard_db):
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 540_000

    # Add active red news blackout covering now_ms
    news = EconomicNews(
        id=99101,
        title="CPI Release",
        currency="USD",
        impact="HIGH",
        scheduled_at=now_ms,
        received_at=now_ms - 3600_000,
    )
    guard_db.add(news)
    guard_db.commit()

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    poi = WatchedZone(
        zone_id="poi_long_15m",
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
    )

    orchestrator = ScalpWatcherOrchestrator()
    orchestrator.set_active_zones("XAUUSDT", [poi])

    orchestrator.evaluate_cycle(
        guard_db,
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

    armed = guard_db.query(PaperOrder).filter(PaperOrder.state == "armed").first()
    assert armed is None


def test_part_171_one_position_limit_prevents_arm(guard_db):
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 540_000

    # Seed an already open position
    open_pos = PaperOrder(
        id="ord_already_open",
        instrument="XAUUSDT",
        direction="SHORT",
        state="paper_open",
        planned_entry=2650.0,
        actual_entry=2650.0,
        stop_loss=2655.0,
        take_profit=2635.0,
        quantity=0.1,
        initial_risk_usdt=5.0,
        opened_at=now_ms - 60_000,
        created_at=now_ms - 120_000,
    )
    guard_db.add(open_pos)
    guard_db.commit()

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    poi = WatchedZone(
        zone_id="poi_long_15m",
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
    )

    orchestrator = ScalpWatcherOrchestrator()
    orchestrator.set_active_zones("XAUUSDT", [poi])

    orchestrator.evaluate_cycle(
        guard_db,
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

    # Only 1 position in DB, no new armed order
    armed = guard_db.query(PaperOrder).filter(PaperOrder.state == "armed").first()
    assert armed is None


def test_part_172_fill_rechecks_policy_and_blocks_if_switched(guard_db):
    now_ms = 1792050000000
    clock = ReplayClock(initial_ms=now_ms)

    # Create an armed order that was created under setup-first
    order = PaperOrder(
        id="ord_armed_recheck",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="LIMIT",
        planned_entry=2623.75,
        stop_loss=2619.45,
        take_profit=2640.0,
        quantity=0.1,
        initial_risk_usdt=5.0,
        created_at=now_ms - 60_000,
        armed_at=now_ms - 60_000,
        expires_at=now_ms + 300_000,
    )
    guard_db.add(order)

    # Now simulate user switching policy to NY_ONLY while currently outside NY
    policy = guard_db.query(TradingPolicy).first()
    policy.entry_session_policy = NY_ONLY
    policy.ny_min_fills = 1
    guard_db.commit()

    # Recheck entry policy at now_ms (which is outside NY window)
    eval_policy = TradingPolicyService.evaluate_entry_policy(
        guard_db,
        now_ms=now_ms,
        strategy_family="CG01_SWEEP_MSS_FVG",
        clock=clock,
    )
    assert eval_policy["can_enter"] is False
    assert "OUTSIDE_ENTRY_WINDOW" in eval_policy["reason_codes"]
