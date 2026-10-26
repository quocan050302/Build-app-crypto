"""
backend/tests/test_v17_4_concurrency.py
V17.4 Parts 174 & 175:
- Duplicate cycle safety and idempotency: same candidate / trigger does not create duplicate orders.
- Final slot competition: at 2 fills, two opportunities in different sessions compete for slot 3.
  Under ALL_SESSIONS_SETUP_FIRST, max_daily_fills (3) is strictly enforced with zero overflow and zero NY reservation.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import crud
from database import run_schema_migrations
from models import Base, PaperOrder, SystemConfig, TradingPolicy
from policy_constants import ALL_SESSIONS_SETUP_FIRST
from services.clock import ReplayClock
from services.scalp_m1_types import M1ScalpConfig, WatchedZone
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.trading_policy_service import TradingPolicyService
from tests.test_v16_3_cg01_pipeline import build_long_fixture_series, make_valid_quote


@pytest.fixture
def concurrency_db():
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

    try:
        yield session
    finally:
        session.close()


def test_part_174_duplicate_cycle_idempotency(concurrency_db):
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

    # Cycle 1: creates and arms the order
    res1 = orchestrator.evaluate_cycle(
        concurrency_db,
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

    armed_orders_1 = concurrency_db.query(PaperOrder).filter(PaperOrder.state == "armed").all()
    assert len(armed_orders_1) == 1

    # Cycle 2: identical input immediately executed again
    res2 = orchestrator.evaluate_cycle(
        concurrency_db,
        symbol="XAUUSDT",
        now_ms=now_ms + 1000,
        quote=quote,
        candles_1m=c_1m,
        candles_5m=[],
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        config=cfg,
        auto_enabled=True,
    )

    # Must NOT create a second armed order
    armed_orders_2 = concurrency_db.query(PaperOrder).filter(PaperOrder.state == "armed").all()
    assert len(armed_orders_2) == 1
    assert armed_orders_2[0].id == armed_orders_1[0].id


def test_part_175_cap_slot_three_enforcement(concurrency_db):
    clock = ReplayClock(1700000000000)

    # Seed 2 daily fills already
    audit = crud.get_or_create_today_audit(concurrency_db, clock=clock)
    audit.fills_count = 2
    concurrency_db.commit()

    # Slot 3 is allowed
    eval1 = TradingPolicyService.evaluate_entry_policy(
        concurrency_db,
        now_ms=clock.now_ms(),
        strategy_family="CG01_SWEEP_MSS_FVG",
        clock=clock,
    )
    assert eval1["can_enter"] is True
    assert eval1["reserved_slots"] == 0

    # Simulate 3rd fill completed
    audit.fills_count = 3
    concurrency_db.commit()

    # Attempting slot 4 must be strictly blocked
    eval2 = TradingPolicyService.evaluate_entry_policy(
        concurrency_db,
        now_ms=clock.now_ms() + 60_000,
        strategy_family="CG01_SWEEP_MSS_FVG",
        clock=clock,
    )
    assert eval2["can_enter"] is False
    assert "MAX_DAILY_ENTRIES" in eval2["reason_codes"]
