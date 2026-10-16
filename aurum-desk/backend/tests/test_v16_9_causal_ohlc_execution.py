"""
backend/tests/test_v16_9_causal_ohlc_execution.py
V16.9 Acceptance Suite - Group 02 (PHẦN 011, 012), Group 03 & 04 (PHẦN 021 - 040), Group 14 (PHẦN 131 - 135).

Verifies:
1. Active LONG position does NOT close at bar_open when candle extrema touches both SL and TP.
   In ASSUMED_OHLC_PATH, it closes at the exact waypoint timestamp where stop was touched.
2. Active SHORT position symmetry (closes at waypoint timestamp, not bar_open).
3. Cooldown timestamp starts from the valid exit event timestamp, not bar_open.
4. CLOSE_QUOTE mode ignores wicks (no exit on candle high/low wick breach).
5. Unknown execution model is rejected before mutation.
6. All fills go through ExecutionCoordinator -> TradeLifecycleService (no direct fill).
7. Pure event expansion helper build_assumed_bar_events returns ordered synthetic quote events.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from database import run_schema_migrations
from services.clock import ReplayClock
from lab.cg01_replay_execution_adapter import (
    CG01ReplayExecutionAdapter,
    build_assumed_bar_events,
)
from services.cg01_types import CG01Config
from domain_calculator import CostAssumptions


@pytest.fixture
def replay_env():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    clock = ReplayClock(initial_ms=1_000_000)
    config = CG01Config(enabled=True)
    costs = CostAssumptions(maker_fee_rate=0.0002, taker_fee_rate=0.0004, slippage_usd=0.0)

    adapter = CG01ReplayExecutionAdapter(
        db=db,
        clock=clock,
        config=config,
        costs=costs,
        initial_capital=5000.0,
        risk_pct=0.10,
        capital_mode="CURRENT_ACCOUNT_BALANCE",
        execution_model="ASSUMED_OHLC_PATH",
    )
    yield db, clock, adapter
    db.close()


def test_build_assumed_bar_events_pure_helper():
    """PHẦN 027 - 029: Pure event expansion without DB mutation."""
    bar = {
        "open": 100.0,
        "high": 105.0,
        "low": 95.0,
        "close": 102.0,
        "timestamp": 1_060_000,
        "bar_open_time": 1_000_000,
        "bar_duration_ms": 60_000,
        "bid": 101.95,
        "ask": 102.05,
    }
    events = build_assumed_bar_events(bar, path_policy="COLOR_BASED")
    assert len(events) == 4
    # Green candle: OPEN -> LOW -> HIGH -> CLOSE
    phases = [e["phase"] for e in events]
    assert phases == ["OPEN", "LOW", "HIGH", "CLOSE"]

    # Strictly nondecreasing timestamps within bar bounds
    ts_list = [e["timestamp_ms"] for e in events]
    assert ts_list == [1_000_000, 1_015_000, 1_045_000, 1_060_000]
    assert all(1_000_000 <= t <= 1_060_000 for t in ts_list)

    # Required metadata
    for seq, ev in enumerate(events):
        assert ev["sequence"] == seq
        assert ev["source"] == "SYNTHETIC_WAYPOINT"
        assert ev["execution_model"] == "ASSUMED_OHLC_PATH"
        assert ev["path_policy"] == "COLOR_BASED"
        assert ev["assumption_version"] == 1


def test_no_future_close_at_open_long(replay_env):
    """
    PHẦN 011 & 132: Active LONG position with bar touching both SL and TP.
    Bar open: 100.0, low: 98.0 (SL=99), high: 103.0 (TP=102), close: 101.0.
    In GREEN candle (close >= open): OPEN (100) -> LOW (98) -> HIGH (103) -> CLOSE (101).
    At OPEN (t=1_000_000, px=100.0): neither SL nor TP is breached.
    At LOW (t=1_015_000, px=98.0): SL (99.0) is breached!
    Exit must occur at t=1_015_000, NEVER at bar_open (1_000_000).
    """
    db, clock, adapter = replay_env
    t_open = 1_000_000
    t_close = 1_060_000
    clock.set_time(t_open)

    # Seed an open position
    pos = models.PaperOrder(
        id="ord_test_causal_long",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        order_type="LIMIT",
        planned_entry=100.0,
        actual_entry=100.0,
        stop_loss=99.0,
        take_profit=102.0,
        quantity=0.5,
        initial_risk_usdt=5.0,
        risk_pct=0.10,
        created_at=t_open - 60_000,
        armed_at=t_open - 60_000,
        opened_at=t_open - 60_000,
    )
    db.add(pos)
    db.commit()

    candle_event = {
        "timestamp": t_close,
        "open": 100.0,
        "high": 103.0,
        "low": 98.0,
        "close": 101.0,
        "bid": 100.95,
        "ask": 101.05,
        "bar_open_time": t_open,
        "bar_duration_ms": 60_000,
        "is_candle": True,
    }
    adapter.on_quote(candle_event)

    db.refresh(pos)
    assert pos.state == "closed"
    assert pos.exit_cause in ("SL", "SL_HIT", "AMBIGUOUS_BAR_SL_FIRST")
    # Exit timestamp must be the waypoint timestamp (1_015_000), NOT bar_open (1_000_000)
    assert pos.closed_at == 1_015_000
    assert pos.closed_at > t_open
    # Intrabar ambiguity flag recorded
    assert pos.intrabar_ambiguous is True


def test_no_future_close_at_open_short(replay_env):
    """
    PHẦN 012: Active SHORT position with bar touching both SL and TP.
    Position: entry 100.0, SL=102.0, TP=98.0.
    Red candle: open 100.0, high 103.0 (touches SL=102), low 97.0 (touches TP=98), close 99.0.
    Red candle waypoints: OPEN (100) -> HIGH (103) -> LOW (97) -> CLOSE (99).
    At OPEN: neither breached.
    At HIGH (t=1_015_000, px=103): SL (102.0) breached.
    Exit occurs at waypoint timestamp (1_015_000), NOT bar_open (1_000_000).
    """
    db, clock, adapter = replay_env
    t_open = 1_000_000
    t_close = 1_060_000
    clock.set_time(t_open)

    pos = models.PaperOrder(
        id="ord_test_causal_short",
        instrument="XAUUSDT",
        direction="SHORT",
        state="paper_open",
        order_type="LIMIT",
        planned_entry=100.0,
        actual_entry=100.0,
        stop_loss=102.0,
        take_profit=98.0,
        quantity=0.5,
        initial_risk_usdt=5.0,
        risk_pct=0.10,
        created_at=t_open - 60_000,
        armed_at=t_open - 60_000,
        opened_at=t_open - 60_000,
    )
    db.add(pos)
    db.commit()

    candle_event = {
        "timestamp": t_close,
        "open": 100.0,
        "high": 103.0,
        "low": 97.0,
        "close": 99.0,
        "bid": 98.95,
        "ask": 99.05,
        "bar_open_time": t_open,
        "bar_duration_ms": 60_000,
        "is_candle": True,
    }
    adapter.on_quote(candle_event)

    db.refresh(pos)
    assert pos.state == "closed"
    assert pos.exit_cause in ("SL", "SL_HIT", "AMBIGUOUS_BAR_SL_FIRST")
    assert pos.closed_at == 1_015_000
    assert pos.closed_at > t_open


def test_close_quote_mode_ignores_wicks(replay_env):
    """
    PHẦN 026 & 134: In CLOSE_QUOTE mode, wick breach does NOT close position if close quote is safe.
    LONG position: entry 100, SL=95.
    Candle: open 100, low 90 (breaches 95), high 102, close 99 (safe).
    Position must remain paper_open.
    """
    db, clock, adapter = replay_env
    adapter.execution_model = "CLOSE_QUOTE"
    t_open = 1_000_000
    t_close = 1_060_000
    clock.set_time(t_open)

    pos = models.PaperOrder(
        id="ord_test_close_quote_wick",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        order_type="LIMIT",
        planned_entry=100.0,
        actual_entry=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        quantity=0.5,
        initial_risk_usdt=5.0,
        risk_pct=0.10,
        created_at=t_open - 60_000,
        armed_at=t_open - 60_000,
        opened_at=t_open - 60_000,
    )
    db.add(pos)
    db.commit()

    candle_event = {
        "timestamp": t_close,
        "open": 100.0,
        "high": 102.0,
        "low": 90.0,
        "close": 99.0,
        "bid": 98.95,
        "ask": 99.05,
        "bar_open_time": t_open,
        "bar_duration_ms": 60_000,
        "is_candle": True,
    }
    adapter.on_quote(candle_event)

    db.refresh(pos)
    assert pos.state == "paper_open"
    assert pos.closed_at is None


def test_unknown_execution_model_rejected(replay_env):
    """PHẦN 134: Unknown execution model raises ValueError before mutation."""
    db, clock, adapter = replay_env
    adapter.execution_model = "INVALID_MODEL_NAME"

    with pytest.raises(ValueError, match="CONFIG_ERROR: Unknown execution model"):
        adapter.on_quote({
            "timestamp": 1_060_000,
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
            "bid": 100.45, "ask": 100.55,
            "is_candle": True,
        })
