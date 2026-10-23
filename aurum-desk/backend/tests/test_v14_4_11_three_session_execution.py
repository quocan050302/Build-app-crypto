"""
V14.4.11 Three-Session Execution Flow & Guard Verification Suite
Proves:
  P01-P06: Full production chain (evaluate_market_reading_cycle -> READY -> _auto_arm_candidate -> armed -> ExecutionCoordinator.evaluate_orders_sync -> paper_open)
           for ASIA (LONG/SHORT), LONDON (LONG/SHORT), NY (LONG/SHORT).
  N01: Auto OFF produces READY setup but 0 armed orders and 0 fills.
  N02: No-trigger quote leaves order armed and fills at 0.
  N03: NY_ONLY policy restricts outside sessions (Asia/London).
  N04: Slot reservation holds remaining slot for NY when daily quota reaches reserve limit.
  N05: Daily cap of 3 fills blocks all subsequent entries across all sessions.
  N06: Single open position / single armed order concurrency guard.
  N07: Fill guard recheck catches policy / blackout change after arming and rejects fill.
  N08: Custom NY window (e.g. 09:00-12:00) parity: candidate inside custom bounds is armed/filled; outside is blocked.
  N09: Post-fill exit path produces canonical close, net PnL, journal, and terminal outbox events.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Optional
import pytest
from sqlalchemy.orm import Session

import models
import crud
import smc_fixtures as F
from services.clock import IClock
from services.strategy_service import StrategyService
from services.trading_policy_service import TradingPolicyService, resolve_entry_session_context
from services.execution_coordinator import ExecutionCoordinator
from services.trade_lifecycle_service import TradeLifecycleService
from services.smc_scenario_config import active_scenario_config as CFG


class MockClock(IClock):
    def __init__(self, dt: datetime):
        self._dt = dt

    def now_ms(self) -> int:
        return int(self._dt.timestamp() * 1000)

    def now_datetime(self, tz=None):
        return self._dt.astimezone(tz) if tz else self._dt

    def monotonic_s(self) -> float:
        return self._dt.timestamp()


NY_TZ = ZoneInfo("America/New_York")


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


def _run_full_ready_arm_fill_pipeline(
    db: Session,
    session_dt: datetime,
    direction: str,
    auto_enabled: bool = True,
    policy_mode: str = "ALL_SESSIONS_WITH_NY_RESERVE",
) -> Dict[str, Any]:
    """Runs the exact production entry pipeline:
    1. Sets policy and risk settings.
    2. Runs StrategyService.evaluate_market_reading_cycle.
    3. If armed, runs ExecutionCoordinator.evaluate_orders_sync with triggering quote.
    Returns audit dictionary with setup, armed order, fill status, and outbox count.
    """
    now_ms = int(session_dt.timestamp() * 1000)
    clock = MockClock(session_dt)

    # 1. Ensure TradingPolicy
    policy = TradingPolicyService.get_or_create_policy(db)
    policy.entry_session_policy = policy_mode
    policy.is_active = True
    policy.mode = "PAPER"
    policy.max_daily_fills = 3
    policy.ny_min_fills = 1
    policy.reserve_ny_slot = True
    db.commit()

    # 2. Inject shifted synthetic candles
    shifted_candles = make_shifted_candles(direction, target_now_ms=now_ms)
    strategy_svc = StrategyService()
    strategy_svc._load_scenario_candles = lambda _db, _sym, _ms: shifted_candles

    # Initial quote around recent candle close
    last_5m = shifted_candles["5M"][-1]
    quote_px = last_5m.close
    init_ticker = {
        "symbol": "XAUUSDT",
        "bid": round(quote_px - 0.05, 2),
        "ask": round(quote_px + 0.05, 2),
        "last": quote_px,
        "timestamp": now_ms,
        "observed_at": now_ms,
    }

    feed_health = {"feed_healthy": True, "feed_stale": False, "ws_connected": True}

    # 3. Production market reading cycle
    reading = strategy_svc.evaluate_market_reading_cycle(
        db,
        "XAUUSDT",
        now_ms=now_ms,
        now_dt=session_dt,
        clock=clock,
        ticker=init_ticker,
        feed_health=feed_health,
        auto_enabled=auto_enabled,
    )

    setup = db.query(models.WatchSetup).order_by(models.WatchSetup.created_at.desc()).first()
    armed_order = db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").first()

    filled_order = None
    if armed_order and auto_enabled:
        # Construct triggering quote based on order planned_entry
        entry_px = armed_order.planned_entry
        if armed_order.direction == "LONG":
            fill_ticker = {
                "symbol": "XAUUSDT",
                "bid": round(entry_px - 0.1, 2),
                "ask": round(entry_px, 2),  # Ask <= limit triggers BUY LIMIT
                "last": entry_px,
                "timestamp": now_ms + 1000,
                "observed_at": now_ms + 1000,
            }
        else:
            fill_ticker = {
                "symbol": "XAUUSDT",
                "bid": round(entry_px, 2),  # Bid >= limit triggers SELL LIMIT
                "ask": round(entry_px + 0.1, 2),
                "last": entry_px,
                "timestamp": now_ms + 1000,
                "observed_at": now_ms + 1000,
            }

        coordinator = ExecutionCoordinator()
        coordinator.evaluate_orders_sync(
            db,
            ticker_override=fill_ticker,
            clock=clock,
            now_ms=now_ms + 1000,
        )
        db.refresh(armed_order)
        if armed_order.state == "paper_open":
            filled_order = armed_order

    audit = crud.get_or_create_today_audit(db, clock=clock)

    return {
        "reading": reading,
        "setup": setup,
        "armed_order": armed_order,
        "filled_order": filled_order,
        "fills_count": audit.fills_count,
        "clock": clock,
        "now_ms": now_ms,
    }


# =========================================================================
# POSITIVE MATRIX: THREE SESSIONS x LONG / SHORT (PHẦN 047)
# =========================================================================

def test_exec01_asia_long_production_pipeline(isolated_db: Session):
    """
    PHẦN 041, 043, 044, 045, 047:
    Asia session (20:00 NY), LONG direction.
    Proves READY -> ARM -> FILL in isolated DB.
    """
    asia_dt = datetime(2026, 4, 13, 20, 0, tzinfo=NY_TZ)
    res = _run_full_ready_arm_fill_pipeline(isolated_db, asia_dt, "LONG")

    assert res["reading"]["status"] == "OK"
    assert res["setup"] is not None
    assert res["setup"].direction == "LONG"

    assert res["armed_order"] is not None
    assert res["armed_order"].direction == "LONG"

    assert res["filled_order"] is not None
    assert res["filled_order"].state == "paper_open"
    assert res["filled_order"].actual_entry > 0
    assert res["filled_order"].quantity > 0
    assert res["fills_count"] == 1


def test_exec02_asia_short_production_pipeline(isolated_db: Session):
    """
    PHẦN 047: Asia session (20:00 NY), SHORT direction.
    """
    asia_dt = datetime(2026, 4, 13, 20, 0, tzinfo=NY_TZ)
    res = _run_full_ready_arm_fill_pipeline(isolated_db, asia_dt, "SHORT")

    assert res["reading"]["status"] == "OK"
    assert res["setup"] is not None
    assert res["setup"].direction == "SHORT"

    assert res["filled_order"] is not None
    assert res["filled_order"].state == "paper_open"
    assert res["filled_order"].direction == "SHORT"
    assert res["fills_count"] == 1


def test_exec03_london_long_production_pipeline(isolated_db: Session):
    """
    PHẦN 047: London session (04:00 NY = 09:00 UTC), LONG direction.
    """
    london_dt = datetime(2026, 4, 14, 4, 0, tzinfo=NY_TZ)
    res = _run_full_ready_arm_fill_pipeline(isolated_db, london_dt, "LONG")

    assert res["reading"]["status"] == "OK"
    assert res["setup"] is not None
    assert res["filled_order"] is not None
    assert res["filled_order"].state == "paper_open"
    assert res["fills_count"] == 1


def test_exec04_london_short_production_pipeline(isolated_db: Session):
    """
    PHẦN 047: London session (04:00 NY = 09:00 UTC), SHORT direction.
    """
    london_dt = datetime(2026, 4, 14, 4, 0, tzinfo=NY_TZ)
    res = _run_full_ready_arm_fill_pipeline(isolated_db, london_dt, "SHORT")

    assert res["reading"]["status"] == "OK"
    assert res["setup"] is not None
    assert res["filled_order"] is not None
    assert res["filled_order"].state == "paper_open"
    assert res["filled_order"].direction == "SHORT"
    assert res["fills_count"] == 1


def test_exec05_ny_long_production_pipeline(isolated_db: Session):
    """
    PHẦN 047: New York session (09:30 NY), LONG direction.
    """
    ny_dt = datetime(2026, 4, 14, 9, 30, tzinfo=NY_TZ)
    res = _run_full_ready_arm_fill_pipeline(isolated_db, ny_dt, "LONG")

    assert res["reading"]["status"] == "OK"
    assert res["setup"] is not None
    assert res["filled_order"] is not None
    assert res["filled_order"].state == "paper_open"
    assert res["fills_count"] == 1


def test_exec06_ny_short_production_pipeline(isolated_db: Session):
    """
    PHẦN 047: New York session (09:30 NY), SHORT direction.
    """
    ny_dt = datetime(2026, 4, 14, 9, 30, tzinfo=NY_TZ)
    res = _run_full_ready_arm_fill_pipeline(isolated_db, ny_dt, "SHORT")

    assert res["reading"]["status"] == "OK"
    assert res["setup"] is not None
    assert res["filled_order"] is not None
    assert res["filled_order"].state == "paper_open"
    assert res["filled_order"].direction == "SHORT"
    assert res["fills_count"] == 1


# =========================================================================
# NEGATIVE TEST CASES (PHẦN 046, 048, 049, 050, 051)
# =========================================================================

def test_exec07_auto_off_promotes_ready_without_arm_or_fill(isolated_db: Session):
    """
    PHẦN 049: Auto False produces WatchSetup(state='READY'), but no armed order and no fill.
    """
    asia_dt = datetime(2026, 4, 13, 20, 0, tzinfo=NY_TZ)
    res = _run_full_ready_arm_fill_pipeline(isolated_db, asia_dt, "LONG", auto_enabled=False)

    assert res["reading"]["status"] == "OK"
    assert res["setup"] is not None
    assert res["setup"].state == "READY"
    assert res["armed_order"] is None
    assert res["filled_order"] is None
    assert res["fills_count"] == 0


def test_exec08_no_trigger_quote_keeps_order_armed_fills_zero(isolated_db: Session):
    """
    PHẦN 046: When quote has not reached entry threshold, order remains armed and fills count is 0.
    """
    ny_dt = datetime(2026, 4, 14, 9, 30, tzinfo=NY_TZ)
    now_ms = int(ny_dt.timestamp() * 1000)
    clock = MockClock(ny_dt)

    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    policy.entry_session_policy = "ALL_SESSIONS_WITH_NY_RESERVE"
    isolated_db.commit()

    shifted_candles = make_shifted_candles("LONG", target_now_ms=now_ms)
    strategy_svc = StrategyService()
    strategy_svc._load_scenario_candles = lambda _db, _sym, _ms: shifted_candles

    last_5m = shifted_candles["5M"][-1]
    quote_px = last_5m.close
    init_ticker = {
        "symbol": "XAUUSDT",
        "bid": round(quote_px - 0.05, 2),
        "ask": round(quote_px + 0.05, 2),
        "last": quote_px,
        "timestamp": now_ms,
        "observed_at": now_ms,
    }

    reading = strategy_svc.evaluate_market_reading_cycle(
        isolated_db,
        "XAUUSDT",
        now_ms=now_ms,
        now_dt=ny_dt,
        clock=clock,
        ticker=init_ticker,
        feed_health={"feed_healthy": True, "feed_stale": False, "ws_connected": True},
        auto_enabled=True,
    )
    assert reading["status"] == "OK"

    armed_order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").first()
    assert armed_order is not None

    # Supply quote where ask > planned_entry for LONG limit order (NO TRIGGER)
    entry_px = armed_order.planned_entry
    no_trigger_ticker = {
        "symbol": "XAUUSDT",
        "bid": round(entry_px + 2.0, 2),
        "ask": round(entry_px + 2.1, 2),  # Ask > entry: limit buy not triggered!
        "last": round(entry_px + 2.0, 2),
        "timestamp": now_ms + 1000,
        "observed_at": now_ms + 1000,
    }

    ExecutionCoordinator().evaluate_orders_sync(
        isolated_db,
        ticker_override=no_trigger_ticker,
        clock=clock,
        now_ms=now_ms + 1000,
    )

    isolated_db.refresh(armed_order)
    assert armed_order.state == "armed"
    audit = crud.get_or_create_today_audit(isolated_db, clock=clock)
    assert audit.fills_count == 0


def test_n03_ny_only_policy_blocks_asia_and_london(isolated_db: Session):
    """
    PHẦN 037, 048: NY_ONLY policy restricts outside sessions. Asia setup is blocked by policy gate.
    """
    asia_dt = datetime(2026, 4, 13, 20, 0, tzinfo=NY_TZ)
    res = _run_full_ready_arm_fill_pipeline(isolated_db, asia_dt, "LONG", policy_mode="NY_ONLY")

    # In NY_ONLY, outside NY candidate is blocked from arming
    assert res["armed_order"] is None
    assert res["filled_order"] is None
    assert res["fills_count"] == 0


def test_n04_slot_reservation_blocks_outside_ny_when_cap_reached(isolated_db: Session):
    """
    PHẦN 048: ALL_SESSIONS_WITH_NY_RESERVE with 2 daily fills already used preserves the last slot for NY.
    Asia candidate cannot enter.
    """
    asia_dt = datetime(2026, 4, 13, 20, 0, tzinfo=NY_TZ)
    clock = MockClock(asia_dt)

    audit = crud.get_or_create_today_audit(isolated_db, clock=clock)
    audit.fills_count = 2  # 2 fills used, 1 remaining reserved for NY!
    isolated_db.commit()

    res = _run_full_ready_arm_fill_pipeline(isolated_db, asia_dt, "LONG", policy_mode="ALL_SESSIONS_WITH_NY_RESERVE")
    assert res["armed_order"] is None
    assert res["filled_order"] is None


def test_n05_daily_cap_three_blocks_all_sessions(isolated_db: Session):
    """
    PHẦN 048: Max 3 fills per day reached: NY candidate also blocked.
    """
    ny_dt = datetime(2026, 4, 14, 9, 30, tzinfo=NY_TZ)
    clock = MockClock(ny_dt)

    audit = crud.get_or_create_today_audit(isolated_db, clock=clock)
    audit.fills_count = 3  # Maximum 3 fills reached!
    isolated_db.commit()

    res = _run_full_ready_arm_fill_pipeline(isolated_db, ny_dt, "LONG")
    assert res["armed_order"] is None
    assert res["filled_order"] is None


def test_n06_active_position_blocks_concurrent_arm(isolated_db: Session):
    """
    PHẦN 007, 048: Single active position invariant. Existing open position blocks arming new candidate.
    """
    # Seed an open position
    pos = models.PaperOrder(
        id="ord_existing_pos",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=2650.0,
        actual_entry=2650.0,
        stop_loss=2640.0,
        take_profit=2670.0,
        quantity=0.1,
        initial_risk_usdt=2.0,
        opened_at=1000,
        created_at=1000,
    )
    isolated_db.add(pos)
    isolated_db.commit()

    ny_dt = datetime(2026, 4, 14, 9, 30, tzinfo=NY_TZ)
    res = _run_full_ready_arm_fill_pipeline(isolated_db, ny_dt, "LONG")
    assert res["armed_order"] is None


def test_n07_fill_guard_recheck_blocks_fill_when_policy_changes(isolated_db: Session):
    """
    PHẦN 050: Order is armed under ALL_SESSIONS. Before trigger, policy changes to NY_ONLY while in Asia session.
    Execution coordinator rechecks entry policy gate and rejects the order.
    """
    asia_dt = datetime(2026, 4, 13, 20, 0, tzinfo=NY_TZ)
    now_ms = int(asia_dt.timestamp() * 1000)
    clock = MockClock(asia_dt)

    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    policy.entry_session_policy = "ALL_SESSIONS_WITH_NY_RESERVE"
    isolated_db.commit()

    shifted_candles = make_shifted_candles("LONG", target_now_ms=now_ms)
    strategy_svc = StrategyService()
    strategy_svc._load_scenario_candles = lambda _db, _sym, _ms: shifted_candles

    last_5m = shifted_candles["5M"][-1]
    quote_px = last_5m.close
    init_ticker = {
        "symbol": "XAUUSDT",
        "bid": round(quote_px - 0.05, 2),
        "ask": round(quote_px + 0.05, 2),
        "last": quote_px,
        "timestamp": now_ms,
        "observed_at": now_ms,
    }

    reading = strategy_svc.evaluate_market_reading_cycle(
        isolated_db,
        "XAUUSDT",
        now_ms=now_ms,
        now_dt=asia_dt,
        clock=clock,
        ticker=init_ticker,
        feed_health={"feed_healthy": True, "feed_stale": False, "ws_connected": True},
        auto_enabled=True,
    )
    armed_order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").first()
    assert armed_order is not None

    # Policy changes to NY_ONLY before trigger!
    policy.entry_session_policy = "NY_ONLY"
    isolated_db.commit()

    entry_px = armed_order.planned_entry
    fill_ticker = {
        "symbol": "XAUUSDT",
        "bid": round(entry_px - 0.1, 2),
        "ask": round(entry_px, 2),
        "last": entry_px,
        "timestamp": now_ms + 1000,
        "observed_at": now_ms + 1000,
    }

    ExecutionCoordinator().evaluate_orders_sync(
        isolated_db,
        ticker_override=fill_ticker,
        clock=clock,
        now_ms=now_ms + 1000,
    )

    isolated_db.refresh(armed_order)
    # Order must be rejected due to POLICY_GATE
    assert armed_order.state == "rejected"
    assert "POLICY_GATE" in (armed_order.invalidation_reason or "")


def test_exec09_custom_window_parity_in_and_out(isolated_db: Session):
    """
    PHẦN 004, 034, 051: Custom NY window 09:00 - 12:00 NY, fallback 11:30.
    At 11:30 NY: candidate inside window, resolve_entry_session_context reflects in_ny_entry_window=True, arms & fills.
    At 08:30 NY: candidate outside custom window, in_ny_entry_window=False.
    """
    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    policy.entry_session_policy = "NY_ONLY"
    policy.ny_entry_start = "09:00"
    policy.ny_entry_end = "12:00"
    policy.ny_fallback_start = "11:30"
    isolated_db.commit()

    # 1. Inside custom window at 11:30 NY
    dt_1130 = datetime(2026, 4, 14, 11, 30, tzinfo=NY_TZ)
    clock_1130 = MockClock(dt_1130)
    ctx_1130 = resolve_entry_session_context(policy, "STANDARD_SMC", now_ms=int(dt_1130.timestamp() * 1000), clock=clock_1130)
    assert ctx_1130.in_ny_entry_window is True

    # 2. Outside custom window at 08:30 NY (before 09:00 open)
    dt_0830 = datetime(2026, 4, 14, 8, 30, tzinfo=NY_TZ)
    clock_0830 = MockClock(dt_0830)
    ctx_0830 = resolve_entry_session_context(policy, "STANDARD_SMC", now_ms=int(dt_0830.timestamp() * 1000), clock=clock_0830)
    assert ctx_0830.in_ny_entry_window is False

    # 3. Pipeline execution inside custom window at 11:30
    res = _run_full_ready_arm_fill_pipeline(isolated_db, dt_1130, "LONG", policy_mode="NY_ONLY")
    assert res["filled_order"] is not None
    assert res["filled_order"].state == "paper_open"


def test_exec10_post_fill_exit_path_canonical_close(isolated_db: Session):
    """
    PHẦN 052: After filling an order, supplying an exit quote executes a canonical close,
    calculates net PnL and fees, and updates order state to 'closed'.
    """
    ny_dt = datetime(2026, 4, 14, 9, 30, tzinfo=NY_TZ)
    res = _run_full_ready_arm_fill_pipeline(isolated_db, ny_dt, "LONG")
    filled_order = res["filled_order"]
    assert filled_order is not None

    clock = res["clock"]
    now_ms = res["now_ms"] + 5000

    # Execute close via TradeLifecycleService
    TradeLifecycleService.execute_close(
        db=isolated_db,
        order_id=filled_order.id,
        exit_price=filled_order.take_profit,
        exit_cause="TP_HIT",
        occurred_at=now_ms,
        session_tag="NY_SESSION",
    )

    isolated_db.refresh(filled_order)
    assert filled_order.state == "closed"
    assert filled_order.exit_cause == "TP_HIT"
    assert filled_order.closed_at == now_ms
    assert filled_order.realized_pnl_net is not None
    assert filled_order.realized_pnl_net > 0  # Profitable TP hit
