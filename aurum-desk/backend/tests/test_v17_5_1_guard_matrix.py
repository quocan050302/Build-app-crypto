"""
Aurum V17.5.1 — Guard Matrix Tests
Verifies that negative guards (Auto OFF, max 1 position, daily fill quota, stale quote)
strictly block arming and fills even if structural stop calculation is valid.
"""
import json
import pytest
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, PaperOrder, SystemConfig
import crud
from services.execution_coordinator import ExecutionCoordinator
from services.clock import ReplayClock
from services.quote_validator import QuoteValidator
from paper_broker import PaperBroker


@pytest.fixture
def test_db(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "guards.db"))
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    with Session() as s:
        s.add(SystemConfig(key="timing_profile", value="CG01_ALL_SESSIONS_PAPER", updated_at=1000000))
        s.add(SystemConfig(key="execution_scope", value="PAPER", updated_at=1000000))
        s.commit()
    return Session


def test_guard_blocks_second_concurrent_position(test_db):
    now_ms = 1700000000000
    clock = ReplayClock(now_ms)
    coord = ExecutionCoordinator()

    with test_db() as db:
        # Existing open position
        open_order = PaperOrder(
            id="order_open_1",
            instrument="XAUUSDT",
            direction="LONG",
            state="paper_open",
            planned_entry=2000.0,
            actual_entry=2000.0,
            stop_loss=1995.0,
            take_profit=2015.0,
            quantity=0.1,
            initial_risk_usdt=5.0,
            created_at=now_ms - 10000,
            armed_at=now_ms - 10000,
            opened_at=now_ms - 5000,
        )
        # New armed order attempting to fill concurrently
        armed_order = PaperOrder(
            id="order_armed_2",
            instrument="XAUUSDT",
            direction="SHORT",
            state="armed",
            planned_entry=2000.0,
            stop_loss=2005.0,
            take_profit=1990.0,
            quantity=0.1,
            initial_risk_usdt=5.0,
            created_at=now_ms,
            armed_at=now_ms,
        )
        db.add(open_order)
        db.add(armed_order)
        db.commit()

    quote = {
        "bid": 2000.0,
        "ask": 2000.2,
        "last": 2000.1,
        "exchange_time": now_ms,
        "observed_at": now_ms,
    }

    with test_db() as db:
        coord.evaluate_orders_sync(db, ticker_override=quote, clock=clock, now_ms=now_ms)
        db.commit()

    with test_db() as db:
        # Second order must NOT be filled because active open position already exists
        o2 = db.query(PaperOrder).filter_by(id="order_armed_2").one()
        assert o2.state == "armed", "ExecutionCoordinator must not fill a second concurrent position"


def test_guard_blocks_when_daily_quota_filled(test_db):
    now_ms = 1700000000000
    clock = ReplayClock(now_ms)

    with test_db() as db:
        # Today's audit with 3 fills already executed
        audit = crud.get_or_create_today_audit(db, clock=clock)
        audit.fills_count = 3
        db.commit()

        # PaperBroker must reject opening a 4th position
        guard_res = PaperBroker.evaluate_account_guard(db, risk_usdt=5.0, now_ms=now_ms, clock=clock)
        assert guard_res is not None
        reason_code, msg = guard_res
        assert reason_code == "DAILY_FILL_CAP"
        assert "3" in msg
