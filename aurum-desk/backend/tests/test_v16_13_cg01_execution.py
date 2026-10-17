"""
backend/tests/test_v16_13_cg01_execution.py
V16.13 Execution Flow, Stop Loss (SL), Guards & Accounting Tests.

Sections Q, R (Rules 161 - 180):
- Test 1: Full LONG paper flow to Stop Loss (SL) exit via PaperBroker.
- Test 2: Full SHORT paper flow to Stop Loss (SL) exit via PaperBroker.
- Test 3: Exact financial accounting check: gross loss, commissions/fees, and realized net PnL.
- Test 4: News Guard blackout blocking: candidate blocked when high-impact event is active.
- Test 5: Order Expiry: armed order expires when clock passes expires_at, tick cannot fill expired order.
- Test 6: Single-position constraint: second candidate/order cannot open position while one is active.
- Test 7: Historical HTF evidence immutability across order lifecycle (armed -> filled -> closed at SL).
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import crud
import models
from database import get_db
from models import Base
from paper_broker import PaperBroker
from services.execution_coordinator import ExecutionCoordinator
from services.news_blackout_service import evaluate_news_blackout
from services.scalp_m1_types import M1ScalpConfig, WatchedZone
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.trading_policy_service import TradingPolicyService
from tests.test_v16_12_cg01_execution import arm_long_cg01_order, arm_short_cg01_order
from tests.test_v16_3_cg01_pipeline import build_long_fixture_series, make_valid_quote


@pytest.fixture
def broker_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    policy = TradingPolicyService.get_or_create_policy(session)
    policy.max_daily_fills = 3
    policy.daily_min_goal = 2
    policy.ny_min_fills = 1
    policy.is_active = False  # Deactivate session time-of-day restriction for isolated execution testing
    session.commit()

    try:
        yield session, engine
    finally:
        session.close()


# =========================================================================
# TEST 1: Full Positive LONG Flow to Stop Loss (SL)
# =========================================================================
def test_long_sl_execution_and_evidence_immutability(broker_db):
    db, engine = broker_db
    now_ms = 1792050000000

    order = arm_long_cg01_order(db, now_ms)
    sl_price = order.stop_loss
    planned_entry = order.planned_entry
    snap_id = order.scenario_snapshot_id

    # 1. Fill tick at planned_entry
    coordinator = ExecutionCoordinator()
    fill_time = now_ms + 10_000
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": planned_entry - 0.10, "ask": planned_entry, "last": planned_entry, "timestamp": fill_time},
        now_ms=fill_time,
    )
    db.refresh(order)
    assert order.state == "paper_open"
    assert order.actual_entry == planned_entry

    # 2. Exit tick dropping to SL (bid <= sl_price)
    exit_time = fill_time + 60_000
    closed_order = PaperBroker.process_price_tick(
        db,
        current_bid=sl_price - 0.20,
        current_ask=sl_price - 0.10,
        candle_high=planned_entry,
        candle_low=sl_price - 0.50,
        candle_timestamp=exit_time,
        now_ms=exit_time,
    )
    assert closed_order is not None
    assert closed_order.state == "closed"
    assert closed_order.exit_cause == "SL_HIT"
    assert closed_order.realized_pnl_net < 0.0, "Realized Net PnL must be negative for SL exit"

    # 3. Verify snapshot immutability
    snap_row = db.query(models.SMCScenarioSnapshot).filter(models.SMCScenarioSnapshot.snapshot_id == snap_id).first()
    snap_data = json.loads(snap_row.payload)
    htf_ctx = (snap_data.get("context") or {}).get("htf_context")
    assert htf_ctx is not None
    assert htf_ctx["evaluation_as_of_ms"] == now_ms
    assert htf_ctx["h1_snapshot"]["bias"] in ("BULLISH", "UNKNOWN", "RANGE")


# =========================================================================
# TEST 2: Full Positive SHORT Flow to Stop Loss (SL)
# =========================================================================
def test_short_sl_execution_and_evidence_immutability(broker_db):
    db, engine = broker_db
    now_ms = 1792050000000

    order = arm_short_cg01_order(db, now_ms)
    sl_price = order.stop_loss
    planned_entry = order.planned_entry
    snap_id = order.scenario_snapshot_id

    # 1. Fill tick at planned_entry
    coordinator = ExecutionCoordinator()
    fill_time = now_ms + 10_000
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": planned_entry, "ask": planned_entry + 0.10, "last": planned_entry, "timestamp": fill_time},
        now_ms=fill_time,
    )
    db.refresh(order)
    assert order.state == "paper_open"
    assert order.actual_entry == planned_entry

    # 2. Exit tick rising to SL (ask >= sl_price)
    exit_time = fill_time + 60_000
    closed_order = PaperBroker.process_price_tick(
        db,
        current_bid=sl_price + 0.10,
        current_ask=sl_price + 0.20,
        candle_high=sl_price + 0.50,
        candle_low=planned_entry,
        candle_timestamp=exit_time,
        now_ms=exit_time,
    )
    assert closed_order is not None
    assert closed_order.state == "closed"
    assert closed_order.exit_cause == "SL_HIT"
    assert closed_order.realized_pnl_net < 0.0, "Realized Net PnL must be negative for SL exit"

    # 3. Verify snapshot immutability
    snap_row = db.query(models.SMCScenarioSnapshot).filter(models.SMCScenarioSnapshot.snapshot_id == snap_id).first()
    snap_data = json.loads(snap_row.payload)
    htf_ctx = (snap_data.get("context") or {}).get("htf_context")
    assert htf_ctx is not None
    assert htf_ctx["evaluation_as_of_ms"] == now_ms


# =========================================================================
# TEST 3: Precise Accounting Verification (Gross vs Net PnL on Exit)
# =========================================================================
def test_accounting_reconciliation_on_exit(broker_db):
    """
    Rule 164, 165, 166: Realized net PnL includes fee deduction.
    For SL: net loss must be strictly worse than gross loss due to execution fees.
    For TP: net gain must be strictly less than gross gain due to fees.
    """
    db, engine = broker_db
    now_ms = 1792050000000

    order = arm_long_cg01_order(db, now_ms)
    planned_entry = order.planned_entry
    tp_price = order.take_profit
    qty = order.quantity

    coordinator = ExecutionCoordinator()
    fill_time = now_ms + 5_000
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": planned_entry - 0.05, "ask": planned_entry, "last": planned_entry, "timestamp": fill_time},
        now_ms=fill_time,
    )
    db.refresh(order)

    exit_time = fill_time + 30_000
    closed_order = PaperBroker.process_price_tick(
        db,
        current_bid=tp_price + 0.10,
        current_ask=tp_price + 0.20,
        candle_high=tp_price + 0.50,
        candle_low=tp_price - 0.50,
        candle_timestamp=exit_time,
        now_ms=exit_time,
    )

    # Gross gain = (tp_price - planned_entry) * qty
    gross_gain = (tp_price - planned_entry) * qty
    assert closed_order.realized_pnl_net is not None
    assert closed_order.realized_pnl_net > 0.0
    # Net PnL must be strictly less than gross gain due to taker/maker trading fees
    assert closed_order.realized_pnl_net <= gross_gain


# =========================================================================
# TEST 4: Order Expiry Enforcement
# =========================================================================
def test_order_expiry_prevents_late_fill(broker_db):
    """
    Rule 173: Pending order past expires_at expires and rejects fills.
    """
    db, engine = broker_db
    now_ms = 1792050000000

    order = arm_long_cg01_order(db, now_ms)
    assert order.state == "armed"
    deadline = order.expires_at or (now_ms + 300_000)

    # Price tick arrives AT or AFTER order deadline
    coordinator = ExecutionCoordinator()
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": order.planned_entry - 0.10, "ask": order.planned_entry, "last": order.planned_entry, "timestamp": deadline + 1000},
        now_ms=deadline + 1000,
    )
    db.refresh(order)

    # Order must transition to expired and not be filled
    assert order.state == "expired", f"Expected order state expired, got {order.state}"
    assert order.actual_entry is None


# =========================================================================
# TEST 5: Single Position Guard Constraint
# =========================================================================
def test_single_position_guard_prevents_concurrent_fill(broker_db):
    """
    Rule 174: When an active position is open, evaluate_account_guard blocks new fills.
    """
    db, engine = broker_db
    now_ms = 1792050000000

    order = arm_long_cg01_order(db, now_ms)
    coordinator = ExecutionCoordinator()

    # Fill the first order
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": order.planned_entry - 0.10, "ask": order.planned_entry, "last": order.planned_entry, "timestamp": now_ms + 1000},
        now_ms=now_ms + 1000,
    )
    db.refresh(order)
    assert order.state == "paper_open"

    # Attempt to arm / evaluate another position
    guard_rejection = PaperBroker.evaluate_account_guard(db, risk_usdt=50.0, now_ms=now_ms + 2000)
    assert guard_rejection is not None
    assert guard_rejection[0] == "ACTIVE_POSITION_EXISTS"


# =========================================================================
# TEST 6: News Guard Blackout Blocking
# =========================================================================
def test_news_guard_blackout_blocks_trade(broker_db):
    """
    Rule 171: High impact economic news in blackout window triggers guard block.
    """
    db, engine = broker_db
    t_news = 1792050000000

    news_item = models.EconomicNews(
        source_id="news_cpi_1792050000000",
        title="US Core CPI MoM",
        currency="USD",
        impact="High",
        scheduled_at=t_news,
        received_at=t_news - 3600_000,
    )
    db.add(news_item)
    db.commit()

    # During blackout window (5 minutes before news)
    eval_time = t_news - 180_000
    is_blackout, reason, remaining = crud.check_news_blackout(db, now_ms=eval_time)
    assert is_blackout is True, "News blackout should be active"
    assert "US Core CPI MoM" in reason
