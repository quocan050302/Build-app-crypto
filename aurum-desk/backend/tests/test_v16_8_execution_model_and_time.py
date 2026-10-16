"""
backend/tests/test_v16_8_execution_model_and_time.py
V16.8 Reproduction and Verification of Execution Model Dispatch and Nondecreasing Event Time (NHÓM 06, 07, 08, 16).

Tests:
1. Long position in CLOSE_QUOTE ignores wick breach of SL/TP when close is safe.
2. Short position in CLOSE_QUOTE ignores wick breach of SL/TP when close is safe.
3. CLOSE_QUOTE actual stop exit when close quote reaches SL.
4. CLOSE_QUOTE pending limit order ignores wick touch when close does not reach limit.
5. ASSUMED_OHLC_PATH generates strictly nondecreasing timestamps (t0 <= t1 <= t2 <= t3 == now_ms).
6. No backward mutation timestamps during intrabar execution.
7. Unknown execution model raises CONFIG_ERROR before processing mutations.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from database import run_schema_migrations
from domain_calculator import CostAssumptions
from lab.cg01_replay_execution_adapter import CG01ReplayExecutionAdapter
from services.clock import ReplayClock


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


def test_close_quote_open_position_ignores_wick_breach_long(lab_db):
    """PHẦN 012, 055, 151: In CLOSE_QUOTE mode, LONG position survives wick below SL when close is safe."""
    clock = ReplayClock(1792150000000)
    adapter = CG01ReplayExecutionAdapter(
        db=lab_db,
        clock=clock,
        initial_capital=5000.0,
        risk_pct=0.10,
        execution_model="CLOSE_QUOTE",
    )

    open_order = models.PaperOrder(
        id="ord_open_long_wick_test",
        direction="LONG",
        state="paper_open",
        planned_entry=100.0,
        actual_entry=100.0,
        stop_loss=99.0,
        take_profit=105.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=1792150000000,
        opened_at=1792150000000,
    )
    lab_db.add(open_order)
    lab_db.commit()

    # Candle with low=98.0 (wicks below SL 99.0) and high=103.0, but close=100.5
    bar_event = {
        "timestamp": 1792150060000,
        "bar_open_time": 1792150000000,
        "bar_duration_ms": 60_000,
        "open": 100.0,
        "high": 103.0,
        "low": 98.0,
        "close": 100.5,
        "bid": 100.45,
        "ask": 100.55,
        "is_candle": True,
    }
    adapter.on_quote(bar_event)

    # Position must STILL be open because in CLOSE_QUOTE, wicks do not trigger exit!
    lab_db.refresh(open_order)
    assert open_order.state == "paper_open", f"Expected paper_open in CLOSE_QUOTE mode, got {open_order.state}"
    assert open_order.closed_at is None


def test_close_quote_open_position_ignores_wick_breach_short(lab_db):
    """PHẦN 153: In CLOSE_QUOTE mode, SHORT position survives wick above SL when close is safe."""
    clock = ReplayClock(1792150000000)
    adapter = CG01ReplayExecutionAdapter(
        db=lab_db,
        clock=clock,
        initial_capital=5000.0,
        risk_pct=0.10,
        execution_model="CLOSE_QUOTE",
    )

    open_order = models.PaperOrder(
        id="ord_open_short_wick_test",
        direction="SHORT",
        state="paper_open",
        planned_entry=100.0,
        actual_entry=100.0,
        stop_loss=101.0,
        take_profit=95.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=1792150000000,
        opened_at=1792150000000,
    )
    lab_db.add(open_order)
    lab_db.commit()

    # Candle with high=102.0 (wicks above SL 101.0), low=97.0, but close=99.5
    bar_event = {
        "timestamp": 1792150060000,
        "bar_open_time": 1792150000000,
        "bar_duration_ms": 60_000,
        "open": 100.0,
        "high": 102.0,
        "low": 97.0,
        "close": 99.5,
        "bid": 99.45,
        "ask": 99.55,
        "is_candle": True,
    }
    adapter.on_quote(bar_event)

    lab_db.refresh(open_order)
    assert open_order.state == "paper_open"
    assert open_order.closed_at is None


def test_close_quote_actual_stop_exit_at_close(lab_db):
    """PHẦN 152: In CLOSE_QUOTE mode, when close quote reaches SL, position exits cleanly."""
    clock = ReplayClock(1792150000000)
    adapter = CG01ReplayExecutionAdapter(
        db=lab_db,
        clock=clock,
        initial_capital=5000.0,
        risk_pct=0.10,
        execution_model="CLOSE_QUOTE",
    )

    open_order = models.PaperOrder(
        id="ord_open_long_stop_exit",
        direction="LONG",
        state="paper_open",
        planned_entry=100.0,
        actual_entry=100.0,
        stop_loss=99.0,
        take_profit=105.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=1792150000000,
        opened_at=1792150000000,
    )
    lab_db.add(open_order)
    lab_db.commit()

    # Candle closes at 98.5 (bid 98.45 <= SL 99.0)
    bar_event = {
        "timestamp": 1792150060000,
        "bar_open_time": 1792150000000,
        "bar_duration_ms": 60_000,
        "open": 99.5,
        "high": 99.8,
        "low": 98.0,
        "close": 98.5,
        "bid": 98.45,
        "ask": 98.55,
        "is_candle": True,
    }
    adapter.on_quote(bar_event)

    lab_db.refresh(open_order)
    assert open_order.state in ("closed", "paper_closed")
    assert open_order.closed_at is not None


def test_close_quote_pending_limit_ignores_wick(lab_db):
    """PHẦN 154: In CLOSE_QUOTE mode, resting limit order at 99.0 does NOT fill if close is 100.5 even with low 98.0."""
    clock = ReplayClock(1792150000000)
    adapter = CG01ReplayExecutionAdapter(
        db=lab_db,
        clock=clock,
        initial_capital=5000.0,
        risk_pct=0.10,
        execution_model="CLOSE_QUOTE",
    )

    pending_order = models.PaperOrder(
        id="ord_pending_limit_close_quote",
        direction="LONG",
        state="armed",
        order_type="LIMIT",
        planned_entry=99.0,
        stop_loss=95.0,
        take_profit=105.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=1792150000000,
        armed_at=1792150000000,
        expires_at=1792153600000,
    )
    lab_db.add(pending_order)
    lab_db.commit()

    bar_event = {
        "timestamp": 1792150060000,
        "bar_open_time": 1792150000000,
        "bar_duration_ms": 60_000,
        "open": 101.0,
        "high": 102.0,
        "low": 98.0,  # Wick reached 98.0 <= 99.0
        "close": 100.5,  # Close quote is 100.5 > 99.0
        "bid": 100.45,
        "ask": 100.55,
        "is_candle": True,
    }
    adapter.on_quote(bar_event)

    lab_db.refresh(pending_order)
    # Must STILL be armed, NOT filled!
    assert pending_order.state == "armed"
    assert pending_order.opened_at is None


def test_assumed_ohlc_path_monotonically_increasing_timestamps(lab_db):
    """PHẦN 061, 064, 155: Waypoints follow strictly nondecreasing timestamps."""
    clock = ReplayClock(1792150000000)
    adapter = CG01ReplayExecutionAdapter(
        db=lab_db,
        clock=clock,
        initial_capital=5000.0,
        risk_pct=0.10,
        execution_model="ASSUMED_OHLC_PATH",
    )

    observed_times = []
    original_set_time = clock.set_time

    def tracking_set_time(t):
        observed_times.append(t)
        original_set_time(t)

    clock.set_time = tracking_set_time

    bar_event = {
        "timestamp": 1792150060000,
        "bar_open_time": 1792150000000,
        "bar_duration_ms": 60_000,
        "open": 100.0,
        "high": 105.0,
        "low": 95.0,
        "close": 102.0,
        "bid": 101.95,
        "ask": 102.05,
        "is_candle": True,
    }
    adapter.on_quote(bar_event)

    # Filter out redundant calls; timestamps must be monotonically nondecreasing!
    assert len(observed_times) >= 4
    for i in range(len(observed_times) - 1):
        assert observed_times[i] <= observed_times[i + 1], (
            f"Clock moved backwards: {observed_times[i]} > {observed_times[i+1]}"
        )
    # The final clock time must equal candle close
    assert clock.now_ms() == 1792150060000


def test_unknown_execution_model_rejected(lab_db):
    """PHẦN 058: Unknown execution model string raises validation error before mutation."""
    clock = ReplayClock(1792150000000)
    with pytest.raises(ValueError, match="CONFIG_ERROR"):
        CG01ReplayExecutionAdapter(
            db=lab_db,
            clock=clock,
            execution_model="UNSUPPORTED_RANDOM_MODEL",
        )
