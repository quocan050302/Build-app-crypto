"""
backend/tests/test_v16_5_replay_shared_execution.py
V16.5 Gate C: Shared Replay Execution Pipeline (Rules 081 - 120, 148 - 160, Appendix C, D):
- test_rule_148_replay_long_positive_shared_pipeline: full replay cycle for LONG limit order through adapter -> fill -> TP exit
- test_rule_149_replay_short_positive_shared_pipeline: full replay cycle for SHORT limit order through adapter -> fill -> TP exit
- test_rule_150_replay_expired_limit_not_filled: order expired past deadline is never filled on subsequent touch
- test_rule_151_replay_before_fill_news_block: news blackout event occurring before touch rejects pending order
- test_rule_156_replay_cg01_off: disabled or OFF profile never arms orders
- test_rule_157_service_call_trace: proves orchestrator -> coordinator -> lifecycle fill -> broker tick -> lifecycle close execution trace
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import crud
import models
from models import Base
from lab.cg01_replay_execution_adapter import CG01ReplayExecutionAdapter
from paper_broker import PaperBroker
from services.cg01_types import CG01Config, CG01ProfileMode
from services.clock import ReplayClock
from services.execution_coordinator import ExecutionCoordinator
from services.scalp_m1_types import WatchedZone
from services.trade_lifecycle_service import TradeLifecycleService
from services.trading_policy_service import TradingPolicyService
from tests.test_v16_3_cg01_pipeline import build_long_fixture_series, build_short_fixture_series, make_valid_quote


@pytest.fixture
def replay_lab_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    policy = TradingPolicyService.get_or_create_policy(session)
    policy.max_daily_fills = 3
    policy.daily_min_goal = 2
    policy.ny_min_fills = 1
    policy.is_active = False  # Deactivate session time-of-day restriction for pure execution testing
    session.commit()

    try:
        yield session, engine
    finally:
        session.close()


def test_rule_148_replay_long_positive_shared_pipeline(replay_lab_db):
    """
    Rules 081 - 100, 148 & Appendix D (R1):
    Positive LONG limit order execution through CG01ReplayExecutionAdapter:
    1. on_bar_close arms LONG limit order via real ScalpWatcherOrchestrator.
    2. on_quote with ask > limit preserves armed state (0 fills).
    3. on_quote with ask <= limit executes fill through ExecutionCoordinator and TradeLifecycleService.
    4. on_quote with bid >= TP closes position through PaperBroker.process_price_tick and TradeLifecycleService.
    5. collect_state() reports authoritative trades, net PnL, and 100% winrate.
    """
    db, _ = replay_lab_db
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000

    clock = ReplayClock(initial_ms=now_ms)
    cfg = CG01Config(enabled=True, profile_mode=CG01ProfileMode.PAPER_EXECUTION)
    adapter = CG01ReplayExecutionAdapter(db=db, clock=clock, config=cfg)

    # 1. Setup zone and candle context
    poi = WatchedZone(
        zone_id="poi_replay_long",
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
    assert order is not None, "Order should be armed by orchestrator"
    assert order.state == "armed"
    assert order.order_type == "LIMIT"
    planned_entry = order.planned_entry  # 2623.75

    # 2. Quote above limit price: ask=2624.00 > 2623.75 -> remains armed
    adapter.on_quote({
        "timestamp": now_ms + 1000,
        "bid": 2623.70,
        "ask": 2624.00,
        "high": 2624.05,
        "low": 2623.65,
    })
    db.refresh(order)
    assert order.state == "armed"
    assert order.actual_entry is None

    # 3. Quote touches limit price: ask=2623.75 <= planned_entry -> paper_open
    adapter.on_quote({
        "timestamp": now_ms + 2000,
        "bid": 2623.65,
        "ask": planned_entry,
        "high": 2623.80,
        "low": 2623.60,
    })
    db.refresh(order)
    assert order.state == "paper_open"
    assert order.actual_entry == planned_entry

    audit = crud.get_or_create_today_audit(db, clock=clock)
    assert audit.fills_count == 1

    # 4. Quote reaches take_profit (e.g. bid >= 2635.00) -> closed
    tp_price = order.take_profit
    adapter.on_quote({
        "timestamp": now_ms + 60_000,
        "bid": tp_price + 0.50,
        "ask": tp_price + 0.60,
        "high": tp_price + 1.0,
        "low": planned_entry,
    })
    db.refresh(order)
    assert order.state in ("closed", "paper_closed")
    assert order.actual_exit is not None
    assert order.realized_pnl_net > 0.0
    assert "TP" in (order.exit_cause or "")

    # 5. Collect state projections
    state = adapter.collect_state()
    assert state["total_fills"] == 1
    assert state["closed_trades_count"] == 1
    assert state["open_positions_count"] == 0
    assert state["pending_orders_count"] == 0
    assert state["metrics"]["winrate_pct"] == 100.0
    assert state["metrics"]["total_pnl_usd"] > 0.0


def test_rule_149_replay_short_positive_shared_pipeline(replay_lab_db):
    """
    Rules 081 - 100, 149 & Appendix D (R1):
    Positive SHORT limit order execution through CG01ReplayExecutionAdapter.
    """
    db, _ = replay_lab_db
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000

    clock = ReplayClock(initial_ms=now_ms)
    cfg = CG01Config(enabled=True, profile_mode=CG01ProfileMode.PAPER_EXECUTION)
    adapter = CG01ReplayExecutionAdapter(db=db, clock=clock, config=cfg)

    poi = WatchedZone(
        zone_id="poi_replay_short",
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
    adapter.orchestrator.set_active_zones("XAUUSDT", [poi])

    c_4h, c_1h, c_15m, c_1m = build_short_fixture_series(t_start)
    bar_event = {
        "symbol": "XAUUSDT",
        "as_of_ms": now_ms,
        "quote": make_valid_quote(2649.5, 2649.7, now_ms),
        "candles_1m": c_1m,
        "candles_15m": c_15m,
        "candles_1h": c_1h,
        "candles_4h": c_4h,
    }
    adapter.on_bar_close(bar_event)

    order = db.query(models.PaperOrder).filter(models.PaperOrder.direction == "SHORT").first()
    assert order is not None, "SHORT order should be armed by orchestrator"
    assert order.state == "armed"
    planned_entry = order.planned_entry  # 2651.25

    # Touch short limit (bid >= planned_entry)
    adapter.on_quote({
        "timestamp": now_ms + 2000,
        "bid": planned_entry,
        "ask": planned_entry + 0.10,
        "high": planned_entry + 0.20,
        "low": 2649.00,
    })
    db.refresh(order)
    assert order.state == "paper_open"
    assert order.actual_entry == planned_entry

    # Reach TP (ask <= take_profit)
    tp_price = order.take_profit
    adapter.on_quote({
        "timestamp": now_ms + 60_000,
        "bid": tp_price - 0.20,
        "ask": tp_price - 0.10,
        "high": planned_entry,
        "low": tp_price - 0.50,
    })
    db.refresh(order)
    assert order.state in ("closed", "paper_closed")
    assert order.actual_exit is not None
    assert order.realized_pnl_net > 0.0

    state = adapter.collect_state()
    assert state["total_fills"] == 1
    assert state["closed_trades_count"] == 1
    assert state["metrics"]["winrate_pct"] == 100.0


def test_rule_150_replay_expired_limit_not_filled(replay_lab_db):
    """
    Rule 150: Order expired past deadline is never filled even if price later reaches entry.
    """
    db, _ = replay_lab_db
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000

    clock = ReplayClock(initial_ms=now_ms)
    cfg = CG01Config(enabled=True, profile_mode=CG01ProfileMode.PAPER_EXECUTION)
    adapter = CG01ReplayExecutionAdapter(db=db, clock=clock, config=cfg)

    poi = WatchedZone(
        zone_id="poi_replay_long",
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
    assert order.state == "armed"
    expires_at = order.expires_at
    planned_entry = order.planned_entry

    # Advance time PAST expiry without touching limit (ask remains at 2625.0)
    adapter.on_quote({
        "timestamp": expires_at + 1000,
        "bid": 2624.90,
        "ask": 2625.00,
        "high": 2625.20,
        "low": 2624.80,
    })
    db.refresh(order)
    assert order.state == "expired"

    # Now price drops to touch limit, but order is already expired
    adapter.on_quote({
        "timestamp": expires_at + 5000,
        "bid": planned_entry - 0.10,
        "ask": planned_entry,
        "high": 2625.00,
        "low": planned_entry - 0.20,
    })
    db.refresh(order)
    assert order.state == "expired"
    assert order.actual_entry is None

    audit = crud.get_or_create_today_audit(db)
    assert audit.fills_count == 0

    state = adapter.collect_state()
    assert state["total_fills"] == 0
    assert state["expired_orders_count"] == 1


def test_rule_151_replay_before_fill_news_block(replay_lab_db):
    """
    Rule 151: News blackout active between ARM and fill causes coordinator to reject order.
    """
    db, _ = replay_lab_db
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000

    clock = ReplayClock(initial_ms=now_ms)
    cfg = CG01Config(enabled=True, profile_mode=CG01ProfileMode.PAPER_EXECUTION)
    adapter = CG01ReplayExecutionAdapter(db=db, clock=clock, config=cfg)

    poi = WatchedZone(
        zone_id="poi_replay_long",
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
    assert order.state == "armed"
    planned_entry = order.planned_entry

    # Insert a high-impact news event covering the touch time
    touch_time = now_ms + 2000
    news_item = models.EconomicNews(
        source_id="news_high_impact_test",
        title="FOMC Rate Decision",
        country="USD",
        currency="USD",
        impact="High",
        scheduled_at=touch_time,
        received_at=touch_time - 3600_000,
    )
    db.add(news_item)
    db.commit()

    # Quote touches limit during news blackout
    adapter.on_quote({
        "timestamp": touch_time,
        "bid": planned_entry - 0.10,
        "ask": planned_entry,
        "high": 2624.00,
        "low": planned_entry - 0.20,
    })
    db.refresh(order)

    # Order must be rejected due to news blackout
    assert order.state == "rejected"
    assert "NEWS_BLACKOUT" in (order.invalidation_reason or "")

    audit = crud.get_or_create_today_audit(db)
    assert audit.fills_count == 0


def test_rule_156_replay_cg01_off(replay_lab_db):
    """
    Rule 156: CG-01 OFF or disabled creates zero armed orders even if fixture has a valid signal.
    """
    db, _ = replay_lab_db
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000

    clock = ReplayClock(initial_ms=now_ms)
    cfg = CG01Config(enabled=False, profile_mode=CG01ProfileMode.OFF)
    adapter = CG01ReplayExecutionAdapter(db=db, clock=clock, config=cfg)

    poi = WatchedZone(
        zone_id="poi_replay_long",
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

    orders = db.query(models.PaperOrder).all()
    assert len(orders) == 0

    state = adapter.collect_state()
    assert state["total_fills"] == 0
    assert state["pending_orders_count"] == 0


def test_rule_157_service_call_trace(replay_lab_db):
    """
    Rule 157: Verify via call inspection that orchestrator -> coordinator -> fill -> broker exit -> close
    all execute through the authentic shared implementations during replay.
    """
    db, _ = replay_lab_db
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000

    clock = ReplayClock(initial_ms=now_ms)
    cfg = CG01Config(enabled=True, profile_mode=CG01ProfileMode.PAPER_EXECUTION)
    adapter = CG01ReplayExecutionAdapter(db=db, clock=clock, config=cfg)

    call_trace = []

    # Spy on coordinator.evaluate_orders_sync
    orig_eval = adapter.coordinator.evaluate_orders_sync
    def spy_eval(*args, **kwargs):
        call_trace.append("ExecutionCoordinator.evaluate_orders_sync")
        return orig_eval(*args, **kwargs)
    adapter.coordinator.evaluate_orders_sync = spy_eval

    # Spy on TradeLifecycleService.execute_fill
    orig_fill = TradeLifecycleService.execute_fill
    def spy_fill(*args, **kwargs):
        call_trace.append("TradeLifecycleService.execute_fill")
        return orig_fill(*args, **kwargs)
    TradeLifecycleService.execute_fill = staticmethod(spy_fill)

    # Spy on PaperBroker.process_price_tick
    orig_tick = PaperBroker.process_price_tick
    def spy_tick(*args, **kwargs):
        call_trace.append("PaperBroker.process_price_tick")
        return orig_tick(*args, **kwargs)
    PaperBroker.process_price_tick = staticmethod(spy_tick)

    # Spy on TradeLifecycleService.execute_close
    orig_close = TradeLifecycleService.execute_close
    def spy_close(*args, **kwargs):
        call_trace.append("TradeLifecycleService.execute_close")
        return orig_close(*args, **kwargs)
    TradeLifecycleService.execute_close = staticmethod(spy_close)

    try:
        poi = WatchedZone(
            zone_id="poi_replay_trace",
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

        # Touch entry
        adapter.on_quote({
            "timestamp": now_ms + 2000,
            "bid": 2623.65,
            "ask": order.planned_entry,
            "high": 2623.80,
            "low": 2623.60,
        })

        # Touch TP
        tp_price = order.take_profit
        adapter.on_quote({
            "timestamp": now_ms + 60_000,
            "bid": tp_price + 0.50,
            "ask": tp_price + 0.60,
            "high": tp_price + 1.0,
            "low": order.planned_entry,
        })

        # Assert all four core services were executed
        assert "ExecutionCoordinator.evaluate_orders_sync" in call_trace
        assert "TradeLifecycleService.execute_fill" in call_trace
        assert "PaperBroker.process_price_tick" in call_trace
        assert "TradeLifecycleService.execute_close" in call_trace

    finally:
        # Restore original methods
        TradeLifecycleService.execute_fill = staticmethod(orig_fill)
        PaperBroker.process_price_tick = staticmethod(orig_tick)
        TradeLifecycleService.execute_close = staticmethod(orig_close)
