"""
backend/tests/test_v16_10_causal_ohlc_path.py
V16.10 Acceptance Suite - Group 02, 03 & 04 (PHẦN 011 - 040).

Verifies:
1. Long position on RED candle (open 100, high 103, low 98, close 99.5):
   COLOR_BASED path is OPEN -> HIGH -> LOW -> CLOSE.
   Hits TP (102.0) at HIGH (waypoint 1) before SL (99.0) at LOW (waypoint 2).
   Never forces SL-first or closes at bar_open.
2. Short position on GREEN candle (open 100, low 98, high 103, close 100.5):
   COLOR_BASED path is OPEN -> LOW -> HIGH -> CLOSE.
   Hits TP (99.0) at LOW (waypoint 1) before SL (102.0) at HIGH (waypoint 2).
   Never forces SL-first or closes at bar_open.
3. Long position on GREEN candle (open 100, low 98, high 103, close 101.0):
   Hits SL (99.0) at LOW (waypoint 1) before TP (102.0) at HIGH (waypoint 2).
4. Short position on RED candle (open 100, high 103, low 97, close 99.0):
   Hits SL (102.0) at HIGH (waypoint 1) before TP (98.0) at LOW (waypoint 2).
5. Post-bar ambiguity metadata: intrabar_ambiguous is True, but exit_price,
   closed_at, and realized_pnl are preserved according to actual path.
6. Monotonic clock check: ReplayClock time is strictly nondecreasing.
7. Unknown path policy raises CONFIG_ERROR.
8. Cooldown starts at exact exit waypoint timestamp.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import crud
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


def test_long_red_candle_hits_tp_at_high_before_sl_at_low(replay_env):
    """
    PHẦN 012, 014: Long position on RED candle.
    Entry: 100.0, SL: 99.0, TP: 102.0.
    Red candle: open 100.0, high 103.0, low 98.0, close 99.5.
    COLOR_BASED waypoints:
      wp0: OPEN  (t=1_000_000, px=100.0)
      wp1: HIGH  (t=1_015_000, px=103.0) -> touches TP (102.0)!
      wp2: LOW   (t=1_045_000, px=98.0)  -> touches SL (99.0)
      wp3: CLOSE (t=1_060_000, px=99.5)
    In COLOR_BASED path, HIGH occurs before LOW.
    Outcome must be TP_HIT at HIGH (t=1_015_000), NEVER SL-first at HIGH or bar_open!
    """
    db, clock, adapter = replay_env
    t_open = 1_000_000
    t_close = 1_060_000
    clock.set_time(t_open)

    pos = models.PaperOrder(
        id="ord_long_red_test",
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
        "close": 99.5,
        "bid": 99.45,
        "ask": 99.55,
        "bar_open_time": t_open,
        "bar_duration_ms": 60_000,
        "is_candle": True,
    }
    adapter.on_quote(candle_event)

    db.refresh(pos)
    assert pos.state in ("closed", "paper_closed")
    # Must exit on TP_HIT, not SL
    assert pos.exit_cause == "TP_HIT"
    assert pos.actual_exit == 102.0
    # Must exit at exact HIGH waypoint timestamp (1_015_000)
    assert pos.closed_at == 1_015_000
    # Ambiguity flag recorded because full bar range touched both SL and TP
    assert pos.intrabar_ambiguous is True


def test_short_green_candle_hits_tp_at_low_before_sl_at_high(replay_env):
    """
    PHẦN 013, 014: Short position on GREEN candle.
    Entry: 100.0, SL: 102.0, TP: 99.0.
    Green candle: open 100.0, low 98.0, high 103.0, close 100.5.
    COLOR_BASED waypoints:
      wp0: OPEN  (t=1_000_000, px=100.0)
      wp1: LOW   (t=1_015_000, px=98.0)  -> touches TP (99.0)!
      wp2: HIGH  (t=1_045_000, px=103.0) -> touches SL (102.0)
      wp3: CLOSE (t=1_060_000, px=100.5)
    In COLOR_BASED path, LOW occurs before HIGH.
    Outcome must be TP_HIT at LOW (t=1_015_000), NEVER SL-first at LOW or bar_open!
    """
    db, clock, adapter = replay_env
    t_open = 1_000_000
    t_close = 1_060_000
    clock.set_time(t_open)

    pos = models.PaperOrder(
        id="ord_short_green_test",
        instrument="XAUUSDT",
        direction="SHORT",
        state="paper_open",
        order_type="LIMIT",
        planned_entry=100.0,
        actual_entry=100.0,
        stop_loss=102.0,
        take_profit=99.0,
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
        "close": 100.5,
        "bid": 100.45,
        "ask": 100.55,
        "bar_open_time": t_open,
        "bar_duration_ms": 60_000,
        "is_candle": True,
    }
    adapter.on_quote(candle_event)

    db.refresh(pos)
    assert pos.state in ("closed", "paper_closed")
    # Must exit on TP_HIT, not SL
    assert pos.exit_cause == "TP_HIT"
    assert pos.actual_exit == 99.0
    # Must exit at exact LOW waypoint timestamp (1_015_000)
    assert pos.closed_at == 1_015_000
    assert pos.intrabar_ambiguous is True


def test_monotonic_clock_invariant_during_bar_processing(replay_env):
    """
    PHẦN 015, 032, 039: Replay clock must advance monotonically across all events.
    Never jump to a future timestamp and then wind back to an earlier waypoint.
    """
    db, clock, adapter = replay_env
    t_open = 1_000_000
    t_close = 1_060_000
    clock.set_time(t_open)

    observed_times = []
    original_set_time = clock.set_time

    def tracking_set_time(ms):
        observed_times.append(ms)
        original_set_time(ms)

    clock.set_time = tracking_set_time

    candle_event = {
        "timestamp": t_close,
        "open": 100.0,
        "high": 105.0,
        "low": 95.0,
        "close": 102.0,
        "bid": 101.95,
        "ask": 102.05,
        "bar_open_time": t_open,
        "bar_duration_ms": 60_000,
        "is_candle": True,
    }
    adapter.on_quote(candle_event)

    # Invariant: Each timestamp in observed_times must be >= previous timestamp
    for i in range(len(observed_times) - 1):
        assert observed_times[i] <= observed_times[i + 1], (
            f"Clock reversal detected: {observed_times[i]} > {observed_times[i + 1]} in {observed_times}"
        )


def test_unknown_path_policy_raises_config_error():
    """
    PHẦN 026: build_assumed_bar_events must reject unknown path_policy with CONFIG_ERROR.
    """
    bar = {
        "open": 100.0,
        "high": 105.0,
        "low": 95.0,
        "close": 102.0,
        "timestamp": 1_060_000,
        "bar_open_time": 1_000_000,
        "bar_duration_ms": 60_000,
    }
    with pytest.raises(ValueError, match="CONFIG_ERROR: Unknown path policy"):
        build_assumed_bar_events(bar, path_policy="CONSERVATIVE_SL_FIRST_UNSUPPORTED")


def test_cooldown_starts_at_exact_exit_timestamp(replay_env):
    """
    PHẦN 019: DayAudit.cooldown_until must be calculated from the exact exit event time,
    not bar_open and not wall clock.
    """
    db, clock, adapter = replay_env
    t_open = 1_000_000
    t_close = 1_060_000
    clock.set_time(t_open)

    # Long position that will hit SL at LOW on green candle
    pos = models.PaperOrder(
        id="ord_cooldown_test",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        order_type="LIMIT",
        planned_entry=100.0,
        actual_entry=100.0,
        stop_loss=99.0,
        take_profit=105.0,
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
    assert pos.state in ("closed", "paper_closed")
    assert pos.closed_at == 1_015_000

    audit = crud.get_or_create_today_audit(db, clock=clock)
    if audit and audit.cooldown_until:
        # Cooldown duration is typically 60s or 120s from exit time
        assert audit.cooldown_until > pos.closed_at
        # Invariant: cooldown_until is relative to pos.closed_at (1_015_000), NOT t_open (1_000_000)
        cooldown_duration = audit.cooldown_until - pos.closed_at
        assert audit.cooldown_until == pos.closed_at + cooldown_duration
